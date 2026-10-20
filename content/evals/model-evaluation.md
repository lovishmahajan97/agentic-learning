---
id: model-evaluation
module: evals
name: Model evaluation
kind: concept
difficulty: intro
est_minutes: 14
status: fact-checked
related:
  - evals/golden-dataset
  - evals/eval-harness
  - evals/benchmarking
  - evals/llm-as-a-judge
  - evals/task-specific-acceptance-criteria
  - evals/offline-vs-online-evaluation
  - evals/error-analysis
  - llmops-platform/evaluation-frameworks
sources:
  - Liang et al., 2022 — Holistic Evaluation of Language Models (HELM)
  - Chang et al., 2023 — A Survey on Evaluation of Large Language Models
---

## TL;DR
Model evaluation is the discipline of producing a number you can trust about whether a model, or a
system built on one, does the job. It splits into two questions that are constantly confused:
*capability* evaluation ("what can this model do in general", answered by benchmarks) and
*application* evaluation ("does my prompt + retrieval + model do my task on my data", answered by a
golden dataset and task-specific graders). Without it, every prompt or model change is a guess
judged by whoever looked at the demo last.

## Why it exists
Classical ML evaluation was tidy: hold out a test set, compute one metric, compare. Large language
models break three of its assumptions at once. Outputs are open-ended text, so there is rarely a
single correct string to match. Behaviour is stochastic and prompt-sensitive, so the same input can
score differently across runs and across trivial rewordings. And the model is a black box you did not
train, so you cannot reason about what it learned from its data; you can only observe it.

Three naive substitutes fill the gap in most teams, and each fails in a predictable way. The
*vibe check*, a handful of prompts in a playground, has a sample size in the single digits, a
selection bias toward the inputs the author thought of, and no memory: nobody can say whether last
week's version was better. The *leaderboard*, picking the model with the highest public benchmark
score, measures general capability on tasks that are not yours, on data the model may have seen in
training, and says nothing about your retrieval, your prompt or your tools. The *wait-for-production*
approach, reading thumbs-up rates after shipping, is slow, confounded by traffic mix, and lets the
regression reach users first.

Evaluation exists to make a change decidable before it ships: a repeatable measurement on a fixed set
of inputs, with graders whose verdicts you have checked against humans, and enough samples that the
difference between two versions is larger than the noise.

## How it works
### Two questions, two kinds of evaluation
*Capability (model) evaluation* compares models to each other on standardized tasks such as MMLU,
HumanEval, SWE-bench or GPQA. Its job is model selection and progress tracking. Its weaknesses are
contamination, saturation and the fact that it ignores everything you add around the model.

*Application (system) evaluation* measures your whole pipeline on your inputs: system prompt,
retrieved context, tool calls, output parsing and the model together. Its job is to decide whether a
specific change (new prompt, new chunking, cheaper model) is safe. It needs a dataset you own and
graders tied to your task. Nearly all day-to-day eval work is this kind.

### The anatomy of an eval
Every evaluation, from a benchmark to a one-off regression check, has the same data flow:

```
dataset item  →  system under test  →  raw output  →  grader  →  score  →  aggregate over items (and slices)
```

Each box is a design decision with a reason behind it:

- **Dataset.** A fixed, versioned set of inputs with expected outputs or labels (the golden dataset).
  Fixed, *because* a number is only comparable to another number computed on the same items.
- **System under test.** Pinned model id, prompt hash, temperature, retrieval index version. Pinned,
  *because* otherwise a changed score cannot be attributed to the change you made.
- **Grader.** Chosen by output type, not by convenience. Classification → accuracy, precision, recall,
  F1. Extraction → field-level precision/recall against labels. Code → unit tests pass or fail. Open
  text → a rubric applied by a calibrated LLM judge or a human; string-overlap metrics such as BLEU and
  ROUGE are weak proxies for open-ended quality and should not be the headline. RAG → retrieval
  recall/precision plus faithfulness of the answer to the retrieved context.
- **Aggregation.** Report per slice (intent, language, difficulty, customer), *because* a 95% average
  can hide a 40% slice that matters most.

### Levels of evaluation
*Component (unit) evals* test one stage: did retrieval return the right chunk, did the classifier
pick the right route, did the extractor fill the right fields. They localize failures. *End-to-end
evals* test the user-visible result and are what stakeholders care about. You need both, because an
end-to-end failure with no component evals is undiagnosable, and green component evals with a red
end-to-end eval tells you the integration is the problem.

*Offline* evals run on the fixed dataset before a change ships. *Online* evals (A/B tests, sampled
judge scoring of live traffic, user feedback) run after. Offline is for decisions; online is for
catching what the dataset did not cover and for feeding new items back into it.

### Reading the number
A pass rate is an estimate from a sample. For a proportion *p* measured on *n* items the standard
error is √(p(1−p)/n): at p = 0.8 and n = 100 that is 0.04, so the 95% interval is roughly ±8 points.
Two prompts scoring 81% and 84% on 100 items are indistinguishable. Three practices follow. Compare
versions on the *same* items and count which items flipped (a paired comparison has far less noise
than two independent averages). Run each item more than once when sampling temperature is above zero.
And grow the dataset where the decision is tight rather than everywhere.

### The loop
Evaluation is not a phase; it is a loop. Look at real failures (error analysis), turn each failure
class into a criterion and a few dataset items, add the grader to the harness, gate releases on the
result, monitor production, and feed new failures back in. The remaining lessons in this module are
the pieces of that loop.

## Example
A worked scenario (numbers illustrative). A team runs a RAG support assistant and wants to swap the
embedding model. Their golden dataset has 300 questions stratified across six intents, each with a
reference answer and the ids of the document chunks that answer it.

They evaluate at two levels. Component: retrieval hit rate@5 (fraction of items where at least one
correct chunk is in the top five; often loosely called recall@5, which strictly is the fraction of all
relevant chunks retrieved) moves from 0.84 to 0.90 on the 300 items. End-to-end: an LLM judge, previously
checked against human labels, grades each answer against the reference as pass or fail; the pass rate
moves from 0.78 to 0.83.

Is +5 points real? The standard error at p ≈ 0.8, n = 300 is √(0.8 × 0.2 / 300) ≈ 0.023, so each
number carries roughly ±4.5 points, and an unpaired test on the difference (standard error ≈ 0.032,
z ≈ 1.5) cannot call it. The paired view is sharper because it ignores the items that did not change:
27 items flipped fail → pass and 12 flipped pass → fail; net +15 of 300 is the +5 points, and an exact
sign (McNemar) test on 27 versus 12 gives p ≈ 0.02. The gain is probably real. Looking at the
12 regressions, 9 are in the "billing" intent, where the new embeddings rank a policy FAQ above the
customer's own invoice data. The decision is not "ship" or "don't ship" but "fix billing retrieval,
re-run, then ship behind a canary while sampling live answers through the same judge". That is what
evaluation buys: a specific, attributable, slice-level reason rather than an overall feeling.

## Trade-offs
- Deterministic graders are cheap, fast and exact, but only apply when the output has a checkable
  form; the moment the task is open-ended you pay for judges or humans and inherit their noise.
- A larger golden dataset tightens confidence intervals, but every item costs labeling time and every
  run costs tokens; the right size is the smallest one at which your typical decisions are clear.
- Public benchmarks give comparability across models for free, but they stop being informative once
  contamination or saturation sets in, and they never measured your task in the first place.
- Offline evals are reproducible and fast, but they only cover what you thought to include; online
  evaluation catches the rest at the price of exposing real users and slower, confounded signals.

## Pitfalls
- Confusing capability with application evaluation: choosing a model from a leaderboard and skipping
  the task eval; the symptom is a "state of the art" model that underperforms the cheaper one on your
  own extraction job.
- Reporting a single aggregate; the symptom is a stable overall score while one customer segment or
  one intent quietly degrades after a change.
- Treating a 3-point move on 100 items as a result; the symptom is prompt changes that "improved
  things" and then regress on the next run with no change at all.
- Using string-overlap metrics as the headline for open-ended text; the symptom is correct answers
  scored as failures because they are phrased differently from the reference.
- Trusting an LLM judge that was never checked against human labels; the symptom is a high eval
  score alongside user complaints that the judge is blind to.

## When to use
Evaluation is the default, not the exception: any change to a prompt, model, retrieval setting or
tool definition that reaches users should be measured against a fixed dataset first. Reach for
capability benchmarks only to shortlist models, then decide on your own eval. Measure at the level
where the decision is made: a retrieval change needs retrieval metrics, a prompt change needs
end-to-end pass rates per slice. In day-to-day work this looks like a harness run in CI, a dashboard
of per-slice pass rates with intervals, and a rule that every production bug becomes a dataset item.
In interviews, the expected depth is explaining why a benchmark score does not transfer to a task,
how you would pick a grader for a given output type, and why a small difference on a small set is
not a decision.

## Role lens
- **Engineer:** Builds the dataset and harness, picks graders by output type, and refuses to merge a
  prompt change without a paired comparison on the same items.
- **Architect:** Decides which metrics are release gates, what per-slice floors exist, and how offline
  evaluation connects to online monitoring across the platform.
- **FDE:** Translates the customer's definition of "works" into labeled examples and graders, and uses
  slice-level results to explain a regression to a skeptical stakeholder.

## Check yourself
**Q:** A product manager shows you that model B scores six points higher than model A on a well-known
benchmark and asks you to switch the production summarizer to B. How do you respond, and what would
you do before deciding?
**A:** The benchmark measures general capability on its own tasks, probably under a different prompt
format, and B may have seen it in training, so the six points say little about summarization of our
documents through our prompt. I would run both models through our harness on the golden dataset,
grading summaries with the rubric judge we calibrated against human labels, and compare per slice
with a paired analysis: which items flipped and in which direction. I would also record latency and
cost per summary, since a quality gain that doubles cost is a different decision. If the paired
comparison shows a gain larger than the noise with no slice regressing, we switch behind a canary;
if not, the benchmark number is irrelevant.

## Quiz
### Q: A team wants to evaluate a free-text customer-support reply. Which grading approach is most defensible as the headline metric?
- [ ] ROUGE-L against a single reference reply
- [ ] Cosine similarity between the reply and the reference embedding
- [x] A rubric applied by an LLM judge whose verdicts were checked against human labels
- [ ] Exact match on the first sentence of the reply
> Open-ended replies have many correct phrasings, so overlap and similarity metrics reward wording
> rather than correctness. A rubric judge can assess the content, but only once its agreement with
> humans has been measured; an unchecked judge is just a different vibe check.

### Q: Prompt v2 scores 84% and prompt v1 scores 81% on the same 100-item golden set. What is the right conclusion?
- [ ] v2 is better and should ship
- [x] The difference is within sampling noise; compare which items flipped and consider a larger set
- [ ] v1 is better because shorter prompts generalize more
- [ ] The dataset is too easy and should be replaced
> At n = 100 and p ≈ 0.8 the standard error is about 4 points, so a 3-point gap is not evidence. A
> paired look at flipped items, and more items where the decision is tight, is how to resolve it.

### Q: Why keep component-level evals (for example retrieval recall) alongside end-to-end evals?
- [ ] Component evals are cheaper, so end-to-end evals can be skipped once they pass
- [ ] End-to-end evals cannot be automated
- [x] Component evals localize the cause when the end-to-end score drops
- [ ] Stakeholders prefer component metrics in reports
> An end-to-end failure alone does not say whether retrieval, the prompt or the model is at fault.
> Component evals attribute the drop; they do not replace the end-to-end check, which is what users
> experience.
