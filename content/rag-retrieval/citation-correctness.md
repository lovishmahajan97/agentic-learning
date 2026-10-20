---
id: citation-correctness
module: rag-retrieval
name: Citation correctness
kind: metric
difficulty: advanced
est_minutes: 14
status: reviewed
related:
  - rag-retrieval/citations-grounding
  - evals/faithfulness-groundedness
  - evals/llm-as-a-judge
  - evals/judge-calibration-and-bias
  - evals/hallucination-evaluation
  - evals/retrieval-evaluation
  - evals/golden-dataset
sources:
  - Gao et al., 2023 — Enabling Large Language Models to Generate Text with Citations
  - Rashkin et al., 2023 — Measuring Attribution in Natural Language Generation Models
  - Liu et al., 2023 — Evaluating Verifiability in Generative Search Engines
---

## TL;DR
Citation correctness asks whether each citation actually supports the claim it is attached to, not merely
whether citations are present. It splits into citation recall (is every claim fully supported by what it
cites) and citation precision (is every citation necessary and relevant). It matters because a cited but
unsupported sentence is worse than an uncited one: it borrows the source's authority. Measure it with an
entailment model or calibrated judge against labelled samples.

## Why it exists
Once a RAG system emits citations, teams need to know whether they can be trusted. The tempting metric is
*citation rate*: the share of sentences with a citation. It is easy to compute and nearly useless, because
a model can attach a citation to every sentence while the cited text says something different. Studies of
generative search engines found exactly this gap: fluent, heavily cited answers in which a substantial
share of citations did not support their sentences.

Answer correctness does not cover it either. An answer can be correct while citing the wrong source
(the model knew the fact from pre-training and attached a nearby chunk), and an answer can be faithfully
cited while being wrong because the source itself is outdated. These are three different properties:
*answer correctness* (is it true), *faithfulness* (is it derived from the provided context), and *citation
correctness* (does this specific pointer back this specific claim). Users clicking a citation are testing
the third one, and every failed click erodes trust in all the others.

So citation correctness needs its own metric, defined per claim and per citation, with a judge whose
agreement with humans you have measured.

## How it works
### Unit of evaluation
Split the answer into claims, usually sentences, or atomic facts if sentences are compound. Each claim
*c* has a set of cited passages *P(c)*. The core question is attribution: would a reasonable reader, given
only *P(c)*, agree that *c* is true? This is the "attributable to identified sources" framing used in
attribution research.

### Citation recall (support)
A claim has full support if the concatenation of its cited passages entails it. Citation recall is the
fraction of claims with full support:

```
citation_recall = |{c : entails(concat(P(c)), c)}| / |claims requiring citation|
```

Uncited factual claims count as unsupported. The denominator matters: decide whether hedges,
transitions and restatements of the question require citations, and keep that rule fixed across runs.

### Citation precision (relevance)
A citation is irrelevant if it does not support the claim alone *and* removing it leaves the claim's
support unchanged. Precision is the fraction of citations that are not irrelevant. It penalises the
"cite everything" strategy, where a model attaches five sources to each sentence to game recall. This is
the definition popularised by the ALCE benchmark; variants exist, so state which you use.

### Who decides "entails"
- **Exact-quote check:** if the model returns a verbatim quote, verify it is a substring of the cited
  chunk. Cheap and deterministic, but proves only that the quote exists, not that it supports the claim.
- **NLI model:** a natural language inference classifier scores premise (cited text) against hypothesis
  (claim). Fast and consistent, but weaker on long premises, numbers and multi-step reasoning.
- **LLM judge:** prompted to answer "does this passage fully support this claim: yes / partial / no",
  with the passage and claim only, never the full answer, so it cannot be swayed by fluency. More capable,
  but it has biases and must be calibrated against human labels.
- **Humans:** the reference. Label a few hundred claim-citation pairs, then measure the automated judge's
  agreement with them before trusting its numbers. Report agreement per label, not only overall: the
  costly judge error is calling a partial or unsupported pair "full", so the precision of the judge's
  "full" verdict matters more than its accuracy on easy cases, and judges tend to err in the lenient
  direction.

### Partial support
Real claims are often partly supported: the passage gives the 30-day window but not the "annual plans
only" qualifier. Use a three-way label (full / partial / none) and report partial separately, because
partial support is where subtle errors such as a wrong number or a dropped condition hide.

### Reading the result
Low recall with high retrieval recall means a generation problem: the evidence was in context but the
model drifted or cited the wrong chunk. Low recall with low retrieval recall means the model is answering
without evidence, so fix retrieval or enforce abstention. Low precision means citation spraying; tighten
the instruction or the schema to one or two citations per claim.

One extra judge call per unsupported claim sharpens the diagnosis: check the claim against *all*
retrieved passages, not just the cited ones. Supported by an uncited passage means misattribution: the
answer is grounded but points at the wrong chunk, which a post-hoc re-citation pass can repair.
Supported by nothing in context means the claim came from parametric knowledge or invention, which only
abstention or better retrieval fixes. The two failures look identical in citation recall and need
opposite remedies.

## Example
A worked scoring of one answer to "What are the rules for refunding annual plans?" with sources S1 to S4.

| Claim | Cited | Judge on cited text | Notes |
|---|---|---|---|
| C1 Annual plans can be refunded within 30 days. | S2 | full | S2 states it |
| C2 Refunds go to the original payment method. | S2, S4 | full | S4 alone supports; S2 is silent |
| C3 Refunds after 30 days are prorated. | S3 | none | S3 says no refunds after 30 days |
| C4 Enterprise contracts are excluded. | (none) | none | uncited factual claim |
| C5 Contact billing to start a refund. | S1 | full | S1 supports |

Citation recall = claims fully supported / claims requiring citation = 3 / 5 = 0.60 (C3 is contradicted,
C4 is uncited). Citation precision: there are 5 citations in total (S2, S2, S4, S3, S1). The S2 on C2 does
not support C2 alone and removing it leaves support unchanged, so it is irrelevant; the S3 on C3 supports
nothing. Precision = 3 / 5 = 0.60. Citation rate, by contrast, is 4 / 5 = 0.80, which would have looked
healthy.

Diagnosis: C3 is a contradiction, the worst case, since a user who clicks S3 sees the opposite claim. C4
may well be true (parametric knowledge) but is unverifiable. Across a 300-question eval set you would
aggregate these per claim, report the contradiction rate separately, and slice by question type to find
where the generator drifts.

## Trade-offs
- NLI scorers are cheap enough to run on every production answer, but miss numeric and multi-hop
  subtleties; when answers are dense with figures, a calibrated LLM judge on a sample is the better buy.
- Sentence-level claims are simple to split and label, but compound sentences hide partial support; when
  partial errors matter (regulated domains), decompose into atomic facts despite the extra judge calls.
- Strict precision penalties discourage citation spraying, but can punish legitimate multi-source
  synthesis; for summary-style answers, weight recall more and accept redundant citations.
- Judging with the passage alone isolates citation quality, but ignores whether the claim is actually true;
  pair it with answer-correctness evals when sources themselves may be stale or wrong.

## Pitfalls
- Reporting citation rate as citation quality; the symptom is a dashboard near 100% while user feedback
  reports "the link doesn't say that".
- Showing the judge the whole answer and all sources rather than one claim and its cited passages; the judge
  gets persuaded by fluency and scores support far higher than humans do.
- Never measuring judge agreement with human labels; judge scores drift with prompt or model changes and a
  regression in citation quality goes unnoticed.
- Ignoring version skew between the chunk the model saw and the document fetched at eval time; correct
  citations score as unsupported after documents are edited, or wrong ones score as fine.
- Collapsing "partial" into "full"; dropped qualifiers and wrong numbers, the errors that cause real harm,
  disappear from the metric.

## When to use
Track citation correctness whenever your product shows citations to users or relies on them for audit.
Run it offline on a golden set for every prompt, model or retrieval change, and online on a sampled slice
of production traffic. Report recall, precision and contradiction rate separately, alongside retrieval
recall so you can tell generation failures from retrieval failures. Put an interval on every number
before acting on a change: claims within one answer are correlated, so bootstrap over questions rather
than claims, and expect a two-point movement on a few hundred questions to be within noise. If you show no citations, measure
faithfulness instead. In interviews, the expected depth is to distinguish citation correctness from
faithfulness and answer correctness, and to explain how you would validate the judge itself.

## Role lens
- **Engineer:** Builds the claim splitter, the per-claim judge call with only cited passages, and the eval
  job that reports recall, precision and contradictions per release.
- **Architect:** Sets the quality gate, for example no release if contradiction rate rises, and decides
  where judges run, what is sampled, and how chunk versions are pinned for evaluation.
- **FDE:** Labels a sample of the customer's own questions with their domain experts, giving a trusted
  baseline and the judge-agreement figure that the customer's risk team will ask for.

## Check yourself
**Q:** After switching to a stronger generator, answer accuracy on your eval set rises but citation recall
falls. How can both be true, and what do you do?
**A:** The stronger model knows more from pre-training, so it answers correctly even when retrieved
context is thin, then attaches a nearby citation that does not actually contain the fact. Accuracy rises
because the claims are true; citation recall falls because they are not supported by what is cited. For a
product sold as "answers from your documents" that is a regression: claims are unverifiable and may be
stale for this customer. Fix by enforcing abstention or flagging uncited knowledge, checking retrieval
recall for the failing questions, and gating releases on citation recall and contradiction rate, not only
accuracy.

## Quiz
### Q: An answer cites a source on every sentence, but half the cited passages do not state the claim. Which metric exposes this?
- [ ] Citation rate
- [x] Citation recall, measured by entailment between cited passages and each claim
- [ ] Retrieval recall at k
- [ ] Answer exact-match accuracy
> Citation rate only counts presence, so it reads 100%. Citation recall checks whether the cited text
> entails the claim, which is precisely the failure here; retrieval recall measures a different stage.

### Q: What does citation precision penalise?
- [ ] Claims that have no citation at all
- [ ] Answers that are factually wrong
- [x] Citations that neither support the claim alone nor contribute to its support, such as citation spraying
- [ ] Sources that were retrieved but not cited
> Precision asks whether each citation earns its place. Attaching many sources to a sentence can raise
> recall cheaply; precision counts the irrelevant ones so that strategy does not look good.

### Q: Why should an LLM judge for citation support see only the claim and its cited passages?
- [ ] It reduces the cost of the judge call to nearly zero
- [ ] Judges cannot read more than one passage at once
- [x] Context from the full answer and other sources lets the judge infer support that the cited text alone does not provide
- [ ] It makes the judge agree with the generator more often
> Citation correctness is about the specific pointer. Given the whole answer, a judge fills gaps from
> other sentences or sources and over-credits support, inflating scores relative to human labels.
