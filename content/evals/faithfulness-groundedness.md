---
id: faithfulness-groundedness
module: evals
name: Faithfulness / groundedness
kind: metric
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - evals/hallucination-evaluation
  - evals/retrieval-evaluation
  - evals/llm-as-a-judge
  - evals/tooling
  - rag-retrieval/citations-grounding
  - rag-retrieval/citation-correctness
  - model-internals/hallucination
sources:
  - Maynez et al., 2020 — On Faithfulness and Factuality in Abstractive Summarization
  - Es et al., 2023 — RAGAS: Automated Evaluation of Retrieval Augmented Generation
  - Min et al., 2023 — FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation
---

## TL;DR
Faithfulness (groundedness) measures whether every claim in a generated answer is supported by the
context the model was given, not whether the claim is true. It exists because a RAG system can produce
fluent, even correct, answers its retrieved documents do not support, and those break when the documents
change. It is computed by splitting the answer into atomic claims and checking each against the context
with an entailment model or an LLM judge; it needs no reference answer, so it runs on production traffic.

## Why it exists
The promise of retrieval-augmented generation is "the answer comes from your documents". Nothing in the
architecture enforces that promise. The model can answer from parametric memory and ignore the context;
it can blend memory with context; it can misread the context (a wrong number, the wrong entity, a
dropped negation); or it can turn a specific statement into a general one. All of these produce
confident prose that looks grounded.

The crucial distinction is that **correctness and faithfulness are different axes**, and the 2×2 is
diagnostic. *Faithful but wrong* means the context itself is wrong or stale, which is an ingestion or
freshness problem. *Unfaithful but correct* means the model answered from memory; it is right today,
unverifiable, uncitable, and will drift when the facts change. *Unfaithful and wrong* is the classic
hallucination. An end-to-end correctness score collapses these into one number and points you at the
wrong fix.

Naive alternatives fail in specific ways. Comparing to a reference answer needs references for every
item and conflates retrieval and generation errors. Asking a judge "is this answer hallucinated?" in one
shot is unreliable because a holistic verdict over a paragraph misses the one wrong figure among six
correct sentences. N-gram overlap with the context rewards copying and penalises paraphrase. The
claim-level method exists because granularity is what makes the check both accurate and actionable, and
because a reference-free metric is the only one you can run on live traffic.

## How it works
### Step 1: decompose the answer into atomic claims
An LLM splits the answer into self-contained factual statements, one fact each, with pronouns and
references resolved so every claim can be judged in isolation. This exists because a paragraph-level
verdict hides a single wrong number, while atomic claims localise the failure and give a ratio.
Granularity is a design choice: too coarse and errors hide inside compound claims; too fine and trivial
claims ("the policy exists") inflate the score. FActScore applied the same atomic-fact idea to long-form
factuality against a knowledge source; faithfulness applies it against the retrieved context.

### Step 2: verify each claim against the context only
For each claim decide **supported** (entailed by the context), **contradicted** (the context says
otherwise) or **not found** (the context is silent). Two families of verifier:

- An **NLI model** with premise = context and hypothesis = claim. Cheap, fast, deterministic, but
  limited by its input length and weaker when support requires combining sentences or light reasoning.
- An **LLM judge** instructed to use only the context and to quote the supporting or contradicting span
  before giving a verdict. It handles long multi-passage contexts and reasoning, at higher cost and with
  the usual judge biases, notably leniency toward paraphrases that quietly change scope ("all" became
  "most", a figure got rounded).

A hybrid is common: NLI first, LLM only on uncertain claims. The three-way split matters because
*contradicted* is worse than *not found*: Maynez et al. separated **intrinsic** hallucination
(contradicts the source) from **extrinsic** hallucination (absent from the source), and the two call
for different responses.

### Step 3: aggregate
faithfulness = supported claims / total claims, which is the RAGAS definition. Alongside the mean,
report the share of answers with at least one contradiction (a gating signal) and the unsupported count.
When the system emits citations, add **citation groundedness**: does the cited span entail the sentence
it is attached to (citation precision), and does every sentence that needs support carry a citation
(citation recall)?

### The abstention loophole
"I could not find that in the documents" contains no claims, so its faithfulness is trivially 1. Optimise
faithfulness alone and the system learns to say nothing. It must be paired with answer relevance and
completeness; this is the "RAG triad" of context relevance, groundedness and answer relevance. The same
pairing with retrieval recall separates "answered from memory because retrieval failed" from "answered
from memory despite good context".

### Calibrating the verifier
The verifier is a judge, so calibrate it: a human-labeled set of (claim, context, label) triples, with
agreement measured per label class. Add a sanity test with shuffled context: faithfulness should fall
toward zero, and if it does not, the judge is using its own knowledge rather than the context.

## Example
A minimal extract-then-verify pipeline with an abstract `llm` callable, followed by a worked case where
the first sentence of the answer is correct and the answer still fails.

```python
import json

EXTRACT = """Split the answer into atomic factual claims, one fact each, with pronouns resolved.
Answer: {answer}
Return a JSON list of strings."""

VERIFY = """Use ONLY the context below. Decide whether the claim is SUPPORTED (entailed by the context),
CONTRADICTED (the context states otherwise) or NOT_FOUND (the context is silent).
Quote the relevant span from the context, then write the verdict alone on the last line.
Context:
{context}

Claim: {claim}"""

def faithfulness(answer: str, context: str, llm) -> dict:
    claims = json.loads(llm(EXTRACT.format(answer=answer)))
    verdicts = [llm(VERIFY.format(context=context, claim=c)).strip().splitlines()[-1].strip()
                for c in claims]
    n = len(claims)
    return {
        "faithfulness": sum(v == "SUPPORTED" for v in verdicts) / n if n else 1.0,  # no claims: trivially faithful
        "contradicted": sum(v == "CONTRADICTED" for v in verdicts),
        "claims": list(zip(claims, verdicts)),
    }

# Context: "Standard plans may be refunded within 30 days of purchase. Enterprise plans are non-refundable."
# Answer:  "Standard plans can be refunded within 30 days. This also applies to Enterprise plans.
#           Refunds go back to the original payment method."
# Claims -> "Standard plans can be refunded within 30 days"   SUPPORTED
#           "Enterprise plans can be refunded within 30 days" CONTRADICTED
#           "Refunds go to the original payment method"       NOT_FOUND
# faithfulness = 1/3, contradicted = 1  ->  fails a contradiction gate despite a correct first sentence.
```

Averaged over an eval set, the headline is the mean faithfulness, but the decision-relevant numbers are
the fraction of answers with any contradicted claim and the per-claim log that shows *which* fact was
invented.

## Trade-offs
- Claim-level verification is precise and diagnostic but costs one judge call per claim, often five to
  fifteen per answer (illustrative); at production volume, sample traffic or fall back to a single-pass
  NLI check.
- NLI verifiers are cheap and deterministic but short-context and weak on multi-sentence support; LLM
  judges handle long contexts and reasoning but add cost and bias; the crossover is set by context
  length and volume.
- Strict faithfulness penalises helpful additions such as unit conversions or a plain-language gloss;
  if the product wants the model to add general knowledge, define the allowed class of "world knowledge"
  claims explicitly rather than loosening the metric.
- Being reference-free lets the metric run on live traffic, but it cannot tell whether the context itself
  is wrong; pair it with ingestion freshness checks and retrieval evaluation.

## Pitfalls
- Scoring at answer level instead of claim level; the symptom is 95% "grounded" answers while a slice of
  them carry wrong numbers or dates.
- Optimising faithfulness in isolation; the symptom is a rising abstention rate and falling answer
  relevance as the assistant learns that silence is safe.
- Collapsing NOT_FOUND and CONTRADICTED into one "unsupported" bucket; the symptom is an answer that
  directly contradicts policy shipping with a respectable 0.8.
- A judge that uses its own knowledge; the symptom is faithfulness staying high when the context is
  shuffled or emptied, which the sanity test catches.
- Decomposition that is too fine; the symptom is scores inflating as answers get wordier because trivial
  claims are all supported.
- Reporting faithfulness as if it were accuracy; the symptom is a stakeholder reading 0.9 as "90% of
  answers are correct" when the context itself was never checked.

## When to use
Use it for any system that generates from supplied context: RAG question answering, summarisation,
document extraction, agent reports built from tool outputs. It is the core metric for the generation
step, always reported with retrieval recall and answer relevance, with a hard gate on contradictions
and a sampled run on production traffic. It is not meaningful for tasks that are supposed to draw on
parametric knowledge, such as open-domain creative writing. In day-to-day work it is the per-claim log
attached to a trace; in interviews, expect to lay out the correctness-versus-faithfulness 2×2, the
claim-decomposition pipeline and the abstention loophole.

## Role lens
- **Engineer:** Implements extract-then-verify with structured outputs, runs it on a sample of production
  traces, alerts on the contradiction rate, and maintains a labeled set to check the verifier itself.
- **Architect:** Sets the faithfulness threshold and the contradiction gate for release, decides whether
  unfaithful-but-correct answers are acceptable in this domain, and budgets the judge sampling rate.
- **FDE:** Shows the customer the 2×2 on their own data, so a failure is attributed to their documents
  (faithful but wrong) or to the system, which decides whether the fix is ingestion or the generator.

## Check yourself
**Q:** A RAG assistant for an insurer scores 0.91 faithfulness on your eval set, yet the compliance team
found three answers quoting coverage limits that appear nowhere in the policy documents. Explain how both
can be true and what you would change.
**A:** 0.91 is an average of per-answer claim ratios; a 9% unsupported share across thousands of claims easily contains
three invented coverage limits, and averages hide gated failures. Other contributors: decomposition too
coarse, so a figure sits inside a long claim judged "mostly supported"; judge leniency on numbers; the
judge drawing on its own knowledge; or NOT_FOUND and CONTRADICTED collapsed together. Changes: gate any
answer with a contradicted claim or an unsupported monetary or numeric claim; add a deterministic
number check that extracts figures from the answer and string-matches them against the context;
calibrate the judge on a hard numeric set and run the shuffled-context sanity test; report the share of
answers with at least one unsupported claim rather than the mean; and route gated answers to abstain
or to human review.

## Quiz
### Q: An answer matches the gold label exactly, but none of its claims appear in the retrieved context. What is its faithfulness, and how should it be treated?
- [ ] High, because the answer is correct
- [x] Low: it came from parametric memory, is unverifiable and uncitable, and is usually treated as a failure for a RAG system
- [ ] Undefined, because faithfulness requires a reference answer
- [ ] Exactly 0.5, splitting correctness and support
> Faithfulness measures support by the context, not truth. A correct answer the documents do not support
> will drift when the facts change and cannot be cited, which is the failure RAG was meant to prevent.

### Q: Why decompose the answer into atomic claims instead of asking for one grounded / not grounded verdict?
- [ ] It is cheaper, because short claims use fewer tokens
- [x] A single verdict averages over the whole answer and misses one wrong figure among many correct sentences; per-claim verdicts localise the error and yield a ratio
- [ ] LLM judges cannot produce a reliable yes/no answer
- [ ] It removes the need to pass the context to the judge
> Holistic judgments are dominated by the bulk of correct text. Atomic claims make each fact a separate
> check, which is more accurate and tells you exactly what was invented.

### Q: A team raises faithfulness from 0.80 to 0.97 through prompt changes alone. What should they check before celebrating?
- [ ] Retrieval latency, which may have regressed
- [x] Answer relevance and the abstention rate, because refusing or hedging more raises faithfulness trivially
- [ ] Total token count of the answers
- [ ] The judge's sampling temperature
> An answer with no claims is perfectly faithful, so the cheapest way to improve the metric is to say
> less. Relevance and completeness are the counterweights that keep the gain honest.
