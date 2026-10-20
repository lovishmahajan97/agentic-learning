---
id: hallucination-evaluation
module: evals
name: Hallucination evaluation
kind: practice
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - evals/faithfulness-groundedness
  - model-internals/hallucination
  - rag-retrieval/citation-correctness
  - evals/llm-as-a-judge
  - evals/judge-calibration-and-bias
  - evals/golden-dataset
  - ml-fundamentals/calibration
sources:
  - Ji et al., 2023 — Survey of Hallucination in Natural Language Generation
  - Min et al., 2023 — FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation
  - Manakul et al., 2023 — SelfCheckGPT: Zero-Resource Black-Box Hallucination Detection for Generative Large Language Models
  - Lin et al., 2022 — TruthfulQA: Measuring How Models Mimic Human Falsehoods
---

## TL;DR
Hallucination evaluation measures how often a system asserts things that are not supported by its
evidence or are false in the world. Whole-response grading hides it, because a fluent answer with one
invented figure still reads as correct. So the practice breaks outputs into atomic claims, verifies each
against a reference (retrieved context, a trusted corpus, or consistency across samples), and reports
claim precision, response-level hallucination rate and abstention behaviour per slice, including the
unanswerable and false-premise cases where fabrication concentrates.

## Why it exists
The failure this measures is specific and expensive: text that is fluent, confident and structurally
right, with one wrong number, a citation that does not exist, a quote attributed to the wrong person or
an API parameter the library never had. A model produces it because, when it lacks the fact, the most
probable continuation is still a plausible-looking fact; nothing in next-token prediction prefers "I do
not know" over a well-formed guess. Plausibility is the whole problem: the reader cannot see the error
without checking every claim against a source, and that checking is exactly the slow, expensive step
the model was supposed to save.

Ordinary accuracy metrics miss it. Exact match scores a whole response 0 or 1 and is undefined for
open-ended text. A judge asked "is this a good answer" rewards fluency and completeness, and it tends
to accept a plausible claim it cannot verify, which is the same bias the generator has. String-overlap
metrics cannot tell "revenue grew 12%" from "revenue grew 21%". Users cannot be relied on either: they
rate confident answers highly and only discover the invented clause in the contract summary later.

The naive fixes also fail. "Ask the model whether it hallucinated" uses the same knowledge that produced
the error; it will defend a confident fabrication as readily as it produced it. "Spot-check a few
outputs by hand" does not scale and, worse, misses precisely the errors that look right. And the cost
asymmetry is brutal: one fabricated figure in a financial summary costs more than ten honest
abstentions, so a metric that averages everything into one score will under-weight the only failures
that matter. Hallucination evaluation exists to make the fabrication rate a first-class number: defined
precisely, measured at claim granularity, broken out by the cases most likely to trigger it.

## How it works
### Define what counts
Two definitions coexist and you must say which you mean. *Intrinsic* hallucination contradicts the
source the model was given; *extrinsic* hallucination cannot be verified from that source at all (the
survey by Ji et al. uses this split). For a RAG system the source is the retrieved context and
"unsupported by context" is the failure, even when the claim happens to be true in the world; that
overlap with faithfulness is covered in its own lesson. For a closed-book assistant with no context, the
reference has to be the world, approximated by a trusted corpus. Non-claims (opinions, hedges, questions
back to the user) are excluded, because the metric is about asserted facts.

### Choose the reference
Three regimes, in decreasing order of reliability:
1. **Context-grounded.** Reference = the documents in the prompt. A verifier (an entailment model or an
   LLM judge required to quote its evidence) labels each claim supported, contradicted or unverifiable.
2. **Corpus-grounded.** Reference = a trusted knowledge source: product docs, an internal KB, a
   versioned API spec, an encyclopedia. FActScore is the template: decompose the generation into atomic
   facts, retrieve passages for each fact, judge each as supported or not, and score the fraction
   supported. It is a *precision* measure: it says how much of what was said is true, not whether the
   answer was complete.
3. **Reference-free.** No source at all. SelfCheckGPT exploits a behavioural signature: knowledge the
   model actually has is reproduced consistently across samples, while a confabulated detail varies
   from sample to sample. Draw several responses at a non-zero temperature and score each sentence of
   the main response by how often the samples contradict it. It is cheap and needs no corpus, but it is
   a proxy: a model that is consistently wrong (because its training data was) passes.

### Decompose into claims
Response-level and claim-level numbers answer different questions and you need both. A response with
twelve claims, one of them false, has 92% claim precision and is 100% hallucinated at the response
level. The first number tracks "how much can I trust the text"; the second tracks "how often does a user
receive something wrong". Decomposition is done with a model prompted to emit atomic, self-contained
statements with pronouns resolved, because a claim like "it was founded then" cannot be verified on its
own. Each claim then goes to the verifier with its retrieved evidence.

### The metrics
- **Claim precision**: supported claims / all claims, averaged per response and then per slice.
- **Response hallucination rate**: fraction of responses with at least one contradicted claim (strict
  variant: at least one unsupported claim).
- **Severity weighting**: numbers, names, dates, citations and legal or medical statements weighted
  above descriptive filler, because that is where the cost lives.
- **Fabrication on unanswerable items**: given a question whose answer is absent from the context (or a
  false-premise question such as "why did the 2019 merger fail" when there was none), the correct
  behaviour is to say so. The rate of confident answers here is the purest hallucination signal, which
  is why SQuAD 2.0-style unanswerable questions and TruthfulQA's false-belief questions exist.
- **False abstention**: refusing or hedging on answerable items. It must be reported next to the
  fabrication rate, because the cheapest way to drive hallucination to zero is to answer nothing.
- **Citation precision**: for outputs with citations, whether each cited passage actually supports the
  sentence citing it. A citation to a real document that says something else is a hallucination wearing
  a reference.

### Build the dataset for it
A golden set sampled only from answerable, well-covered questions underestimates the rate badly. Add
deliberate slices: questions answerable only from context, questions answerable from general knowledge,
questions whose answer is missing from the context, false-premise questions, questions about entities
that do not exist, and questions about facts past the model's training cutoff. Fabrication concentrates
in the last four, so they are where the metric moves when you change the prompt or add retrieval.

### Trust the verifier only after measuring it
The verifier is itself a model. Measure its agreement with human claim labels on a few hundred claims
(Cohen's kappa) before using its numbers, and use a different model family from the generator, because
models trained on overlapping data share false beliefs and will agree with each other's fabrications.

## Example
A claim-level pipeline against a trusted corpus. `call_model` is any chat model (not the one that wrote
the responses) and `search` retrieves from your own knowledge source, not the open web. The output
separates claim precision, response-level rate, fabrication on unanswerable items and false abstention.

```python
import json
from collections import Counter

def call_model(prompt: str) -> str: ...                 # a different model family from the generator
def search(claim: str, k: int = 3) -> list[str]: ...    # trusted corpus, versioned
def is_abstention(text: str) -> bool: ...               # small classifier or judge: "says it cannot answer"

DECOMPOSE = ("Split the text into atomic, self-contained factual claims. Resolve pronouns. "
             "Return a JSON list of strings.\n\nTEXT:\n{text}")
VERIFY = ("Evidence:\n{evidence}\n\nClaim: {claim}\nAnswer with exactly one word: "
          "SUPPORTED, CONTRADICTED or UNVERIFIABLE.")

def score_response(text: str) -> dict:
    claims = json.loads(call_model(DECOMPOSE.format(text=text)))
    labels = Counter()
    for c in claims:
        evidence = "\n---\n".join(search(c))
        labels[call_model(VERIFY.format(evidence=evidence, claim=c)).strip(" .\n").upper()] += 1
    n = len(claims)                     # an abstention has no claims: precision is undefined, not 0
    return {"n_claims": n, "abstained": is_abstention(text),
            "claim_precision": labels["SUPPORTED"] / n if n else None,
            "hallucinated": labels["CONTRADICTED"] > 0,
            "unsupported_rate": (labels["CONTRADICTED"] + labels["UNVERIFIABLE"]) / n if n else None}

def mean(rows: list[dict], key: str) -> float:     # skips undefined values; bools count as 0/1
    vals = [float(r[key]) for r in rows if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else float("nan")

def evaluate(items: list[dict]) -> dict:   # item: {"question", "answerable": bool, "response"}
    rows = [{**it, **score_response(it["response"])} for it in items]
    ans = [r for r in rows if r["answerable"]]
    unans = [r for r in rows if not r["answerable"]]
    return {"claim_precision": mean(ans, "claim_precision"),
            "response_hallucination_rate": mean(ans, "hallucinated"),
            "false_abstention": mean(ans, "abstained"),
            "fabrication_on_unanswerable": 1 - mean(unans, "abstained")}
```

## Trade-offs
- Claim decomposition gives precise, actionable numbers but costs several model calls per response;
  on a 500-item set run nightly that is affordable, on every PR it is not, so tier it like any eval.
- Corpus-grounded verification is only as good as the corpus: an out-of-date internal KB turns correct
  new answers into "contradicted" and hides the model's real errors behind retrieval noise.
- Reference-free consistency checks need no labels or corpus and catch random confabulation, but they
  are blind to systematic errors, so they suit triage and monitoring rather than release decisions.
- A strict response-level rate (any unsupported claim fails) is a good gate for high-stakes domains, but
  on chatty assistants it pushes teams toward terse, evasive answers unless false abstention is gated too.
- Severity weighting makes the score match business cost but adds a judgment layer that must itself be
  agreed with the domain owner, or two teams will report different numbers for the same outputs.

## Pitfalls
- Measuring only on answerable questions; the symptom is a near-zero hallucination rate in the eval and
  a stream of invented policies in production, because fabrication was never provoked.
- Using the generating model as the verifier; the symptom is high "supported" rates on claims a human
  later finds false, since the verifier shares the generator's beliefs.
- Reporting claim precision without response-level rate; the symptom is a 95% score while one in three
  answers contains something wrong, which is what users experience.
- Driving fabrication down without watching false abstention; the symptom is a model that answers
  "I cannot determine that from the provided information" to questions whose answer is on page one.
- Counting a citation as correct because the document exists; the symptom is answers with impeccable
  footnotes that, when opened, say something else.
- Treating unverifiable and contradicted as the same label; the symptom is a hallucination rate that
  jumps whenever the corpus is thin, not whenever the model is wrong.

## When to use
Reach for a dedicated hallucination eval whenever the system asserts facts a user might act on:
summaries of contracts or medical records, customer support answers about policy, code that names APIs,
analytics narratives with numbers. Decide the reference regime from what you have: context-grounded for
RAG, corpus-grounded for closed-book assistants over a domain you own, consistency-based when you have
neither and need a monitoring signal. Measure claim precision, response-level rate, fabrication on
unanswerable items and false abstention together, per slice, and gate releases on the pair that matters
for the product. Skip the heavy pipeline for creative or opinion tasks where "unsupported" is the point.
In interviews, the expected depth is to explain why claim-level verification beats a holistic judge,
why unanswerable items must be in the set, and why the verifier must be a different model.

## Role lens
- **Engineer:** Builds the decompose-and-verify pipeline, adds unanswerable and false-premise slices to
  the golden set, and wires the four numbers into the eval report with per-slice breakdowns.
- **Architect:** Decides the reference regime and the gate: which domains require a strict response-level
  limit, what false-abstention ceiling balances it, and which corpus is authoritative for verification.
- **FDE:** Runs claim-level audits on the customer's real documents with their experts, because the
  customer's definition of "wrong" (a rounding difference, a stale policy) is what the verifier must learn.

## Check yourself
**Q:** A team reports "hallucination rate 2%" from an LLM judge that grades each answer as correct or
not on a golden set of answerable support questions. The customer keeps finding invented refund terms.
What is wrong with the measurement and how would you redesign it?
**A:** Three things. The judge grades whole responses holistically, so an answer that is mostly right
with one invented clause is scored correct; the fix is claim decomposition with per-claim verification
against the policy corpus, reporting both claim precision and the fraction of responses containing any
contradicted claim. The set contains only answerable questions, so fabrication is never provoked; add
questions whose answer is absent from the policy, false-premise questions and edge cases past the
corpus, and report fabrication rate on them separately. And the judge is probably the same model family
as the generator and was never validated; measure its agreement with human claim labels and use a
different model. Finally gate on false abstention too, or the fix will be a model that refuses.

## Quiz
### Q: Why does hallucination evaluation decompose responses into atomic claims instead of grading each response once?
- [ ] Atomic claims are cheaper to grade than whole responses
- [x] A single invented detail inside an otherwise correct response is invisible to holistic grading but visible per claim
- [ ] Judges cannot read responses longer than a few sentences
- [ ] Claim decomposition removes the need for a reference corpus
> Holistic judges reward fluency and completeness and accept plausible claims they cannot verify, so a
> response with one false figure passes. Verifying each atomic claim against evidence exposes it; it costs
> more calls, not fewer, and still needs a reference.

### Q: A closed-book assistant scores 97% claim precision on a golden set of answerable questions. What slice is missing before you trust the number?
- [ ] Longer questions, because precision drops with length
- [ ] Questions with multiple correct answers
- [x] Unanswerable and false-premise questions, where fabrication actually concentrates
- [ ] Questions phrased in a second language
> Fabrication is provoked when the model lacks the fact, so a set of well-covered questions never
> triggers it. Add questions with no answer in the corpus and false-premise questions, and report the
> fabrication rate on them alongside false abstention on answerable ones.

### Q: What kind of hallucination does a SelfCheckGPT-style consistency check across samples fail to detect?
- [ ] Hallucinated numbers, because digits are sampled deterministically
- [x] Systematically wrong facts the model reproduces consistently because its training data was wrong
- [ ] Hallucinations in the first sentence of a response
- [ ] Any hallucination when temperature is above zero
> Consistency checks rely on confabulated details varying between samples. A false belief the model holds
> firmly is reproduced identically every time and looks exactly like real knowledge, so only a reference
> corpus or human check can catch it.
