---
id: citations-grounding
module: rag-retrieval
name: Citations / grounding
kind: pattern
difficulty: core
est_minutes: 14
status: reviewed
related:
  - rag-retrieval/citation-correctness
  - rag-retrieval/lost-in-the-middle
  - rag-retrieval/reranking
  - evals/faithfulness-groundedness
  - model-internals/hallucination
  - prompting/structured-outputs-json-mode
  - prompting/context-engineering
sources:
  - Gao et al., 2023 — Enabling Large Language Models to Generate Text with Citations
  - Rashkin et al., 2023 — Measuring Attribution in Natural Language Generation Models
  - Official docs: Anthropic Messages API, citations
---

## TL;DR
Grounding means the answer is derived from, and only from, the retrieved sources; citations are the
machine-checkable pointers from each claim back to the source span that supports it. You need both
because retrieval alone does not stop a model from mixing in memorised or invented facts, and without
pointers neither a user nor an evaluator can tell which sentences are backed. Give sources stable IDs,
require a citation per claim, allow abstention, and verify citations in code.

## Why it exists
A RAG pipeline puts relevant text in the prompt, but the model is not obliged to use it. It has
parametric knowledge from pre-training, it fluently fills gaps, and it will happily answer a question the
sources do not cover. The result is the most dangerous kind of error: a confident answer that looks like
it came from the company's documents but did not. In regulated or contractual settings (policy, legal,
medical, finance, support for a paid product) an unbacked sentence is a liability, not a quality nit.

The naive fix is a system prompt that says "only use the context". That helps, but it is an instruction
the model follows probabilistically and it leaves no evidence trail. When a user disputes an answer, you
cannot show where it came from; when you evaluate a thousand answers, you cannot automatically check
them. Another naive fix is to list the retrieved documents under the answer as "sources". That is
decoration: it says what was retrieved, not what supports which sentence, and users quickly learn the
list is not trustworthy.

Citations turn grounding from a hope into a contract that can be inspected. Each claim carries an ID that
points to a specific chunk or span, so the UI can show the evidence, a validator can reject citations to
sources that were never provided, and an evaluator can check whether the cited text actually entails the
claim. That last check is the subject of citation correctness.

## How it works
### 1. Make sources addressable
Every retrieved chunk gets a short, stable identifier in the prompt (`[S1]`, `[S2]`, or a document ID
plus chunk offset) together with its title, date and any metadata that helps the model disambiguate. Short
IDs matter because the model must reproduce them exactly; long UUIDs get mangled. Keep a server-side map
from the short ID to the real document, version and character range, because the citation shown to the
user must resolve to the exact text the model saw, not to the current version of the file.

### 2. Choose the citation granularity
Citations can point at a document, a chunk, a sentence or a character span. Coarser pointers are
easier for the model to produce and nearly always "correct", which is exactly why they are weak: a
40-page PDF supports almost any claim about its topic, so the user still has to hunt and a verifier
cannot check anything. Finer pointers make verification and the UI highlight precise, but the model must
locate the exact supporting text, and chunk boundaries can split it. The usual operating point is
chunk-level IDs plus a short verbatim quote, which gives span precision without asking the model to
emit character offsets it cannot count reliably; the server resolves the quote to offsets afterwards.

### 3. Instruct the citation contract
The instruction is specific: cite after every sentence that states a fact, cite only IDs that appear
above, prefer quoting the supporting phrase, and if the sources do not contain the answer, say so instead
of answering. The abstention clause is the grounding mechanism; without a legitimate "I don't know" path,
the model will reach for parametric knowledge and attach the nearest-looking citation to it.

### 4. Constrain the output shape
Free-text `[S2]` markers are easy to read but hard to verify. A sturdier pattern is structured output: a
list of claims, each with `source_ids` and a short verbatim `quote`. Because the quote must appear in the
cited chunk, a string check in code catches fabricated or misattributed citations cheaply. Some provider
APIs offer native citation modes in which you pass documents and the response returns the cited text
with its location (character ranges for plain text, page numbers for PDFs, the exact shape varying by
vendor); these remove the parsing burden but tie you to that provider's format.

### 5. Generation-time vs post-hoc attribution
Two families exist. *Generation-time* citation asks the model to cite while writing, which is cheap and
usually accurate when retrieval is good. *Post-hoc* attribution generates the answer first, then for each
sentence searches the sources for a supporting span (with an embedding search plus an entailment check)
and attaches or flags it. Post-hoc is slower but works with any model and can find unsupported sentences
the generator never cited. Mature systems often do generation-time citation and then a post-hoc
verification pass.

### 6. Verify and act
After generation, code checks: every cited ID exists in this request's source set; every quote is a
substring (after whitespace normalisation) of its cited chunk; every factual sentence has at least one
citation. Failures trigger a repair retry, removal of the unsupported sentence, or a visible "unverified"
marker, depending on the product's risk tolerance. The strongest check, whether the cited text actually
entails the claim, needs an NLI model or an LLM judge and is usually run on a sample or in offline evals.

### Where grounding fails upstream
Citations cannot fix retrieval. If the right chunk was never retrieved, a well-behaved model abstains and
a badly-behaved one cites something adjacent. Chunks that cut a fact in half, or evidence buried in the
middle of a long context, both degrade citation quality, so grounding is a property of the whole pipeline.

## Example
Generation with a quote-backed citation contract, then a cheap verification pass.

```python
import json, re

def build_prompt(question, chunks):
    src = "\n\n".join(f"[S{i+1}] ({c['title']}, {c['date']})\n{c['text']}"
                      for i, c in enumerate(chunks))
    return (f"Sources:\n{src}\n\n"
            "Answer using ONLY the sources. Return JSON: "
            '{"claims":[{"text":...,"source_ids":["S1"],"quote":...}], "answerable":true}. '
            "quote must be copied verbatim from a cited source. "
            'If the sources do not answer the question, return {"claims":[],"answerable":false}.\n\n'
            f"Question: {question}")

def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()

def verify(raw, chunks):
    out = json.loads(raw)
    ids = {f"S{i+1}": c for i, c in enumerate(chunks)}
    problems = []
    if out.get("answerable") and not out["claims"]:
        problems.append((None, "marked answerable but made no claims"))
    for n, cl in enumerate(out["claims"]):
        if not cl.get("source_ids"):
            problems.append((n, "uncited claim"))
            continue
        bad = [s for s in cl["source_ids"] if s not in ids]
        if bad:
            problems.append((n, f"unknown source {bad}"))
            continue
        quote = norm(cl.get("quote") or "")          # an empty quote would match any chunk
        if not quote or not any(quote in norm(ids[s]["text"]) for s in cl["source_ids"]):
            problems.append((n, "quote missing or not found in cited sources"))
    return out, problems   # caller repairs, drops, or flags the claims listed in problems
```

Suppose three chunks are retrieved for "What is the refund window for annual plans?" and the model returns
two claims: one cites S2 with the quote "Annual plans may be refunded within 30 days", which appears in S2
and passes; the other says partial refunds are prorated and cites S3 with a quote that appears nowhere.
The verifier flags the second claim (index 1 in `problems`), and the product either regenerates or drops it and shows only the supported
sentence. No model call was needed to catch the fabricated quote.

## Trade-offs
- Mandatory per-claim citations raise trust and auditability, but they make prose choppier, add output
  tokens and push the model toward claims it can quote; for casual or creative use the friction is not
  worth it, and it flips back the moment an answer can be acted on or disputed.
- Strict abstention lowers hallucination rate but increases "I can't find that" responses; if retrieval
  recall is weak, users experience it as a broken product, so fix recall first.
- Verbatim-quote requirements make verification a cheap string check, but paraphrase-heavy answers (summaries
  across many sources) fit them poorly; there, an entailment check per claim is the better verifier.
- Provider-native citation features save parsing and return precise spans, but lock the output format to
  one vendor; a portable JSON contract is preferable when you route across models.
- Post-hoc attribution catches sentences the generator never cited, at the cost of an extra retrieval and
  entailment pass per sentence, which matters when latency budgets are tight.

## Pitfalls
- Listing retrieved documents under the answer and calling them citations; the symptom is users clicking a
  source and finding nothing relevant, then ignoring sources entirely.
- Citing document IDs that resolve to the latest version rather than the chunk version the model saw; the
  symptom is citations that "used to" support the claim after a document edit.
- Giving no abstention path, so the model attaches the nearest citation to parametric knowledge; it shows
  up as citations that are topically related but do not state the claimed fact.
- Only checking that citation IDs exist, not that the cited text supports the claim; evals show high
  "citation rate" while human reviewers still find many unsupported sentences.
- Letting long, numerous chunks bury the evidence mid-context; citation accuracy drops as top-k grows even
  though retrieval recall goes up.
- Citing whole documents rather than chunks or spans; the symptom is a near-perfect automated citation
  score alongside users who say the cited PDF is 40 pages and they could not find the sentence.

## When to use
Use explicit citations whenever an answer could be acted on, disputed or audited: internal knowledge
assistants, customer support, policy and legal Q&A, anything sold as "answers from your documents". Skip or
soften them for brainstorming, drafting and conversational tasks where sources are not the point. Measure
three things: the fraction of factual sentences carrying a citation, the fraction of citations that pass
verification, and abstention rate on questions you know are unanswerable. In interviews, expect to explain
why "just tell it to use the context" is insufficient and how you would verify citations automatically.

## Role lens
- **Engineer:** Assigns stable source IDs, enforces a claims-with-quotes schema, and writes the verifier
  that rejects unknown IDs and missing quotes before the answer reaches the UI.
- **Architect:** Decides the grounding policy per surface (strict abstain vs best effort), where
  verification runs, and how chunk versions are stored so citations resolve to what the model saw.
- **FDE:** Shows a sceptical customer clickable evidence for each sentence during a pilot, which is often
  what turns a demo into an approved rollout in compliance-heavy teams.

## Check yourself
**Q:** Your assistant cites a source on 98% of sentences, yet reviewers say one answer in six contains a
claim the cited source does not support. What is going on and what do you change?
**A:** Citation *rate* measures formatting, not support. The model is attaching IDs to sentences, often
the nearest topically related chunk, including sentences derived from parametric knowledge or from
merging two chunks. Fixes: require a verbatim quote per claim and verify it in code; add an explicit
abstention path so the model is not forced to answer; reduce top-k or rerank so evidence is not buried;
and measure citation correctness with an entailment check or judge on a labelled sample, tracking support
rather than presence. Also check retrieval recall, since missing evidence produces exactly this pattern.

## Quiz
### Q: Why is an explicit "say you don't know" instruction central to grounding?
- [ ] It reduces token usage on hard questions
- [x] Without a legitimate abstention path the model fills gaps from parametric knowledge and attaches a nearby citation
- [ ] It prevents prompt injection from retrieved documents
- [ ] It forces the retriever to return more chunks
> Grounding fails mostly on questions the sources do not answer. If the only allowed output is an answer,
> the model invents one and decorates it with a plausible citation; abstention gives it a correct exit.

### Q: What does requiring a verbatim quote with each citation buy you?
- [ ] It guarantees the answer is factually correct
- [ ] It removes the need for retrieval evaluation
- [x] A cheap string check can catch fabricated or misattributed citations without another model call
- [ ] It lets the model cite sources it was not given
> If the quote is not in the cited chunk, the citation is wrong by construction. This catches a class of
> errors for free, though a real quote can still fail to entail the claim, which needs an entailment check.

### Q: When is post-hoc attribution preferable to generation-time citation?
- [ ] When latency must be as low as possible
- [x] When you need to find unsupported sentences the generator never cited, or use a model that cites poorly
- [ ] When the sources are very short
- [ ] When the product has no UI for showing sources
> Post-hoc attribution searches the sources for support for every sentence after generation, so it can
> flag uncited or unsupported claims regardless of the generator, at the cost of extra latency.
