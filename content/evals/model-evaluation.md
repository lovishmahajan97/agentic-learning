---
id: model-evaluation
module: evals
name: Model evaluation
kind: concept
difficulty: intro
est_minutes: 15
status: crisp-verified
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
Model evaluation produces a number you can trust about whether a model, or a system built on one, does
the job. Capability evals (benchmarks) and application evals (your golden dataset and graders) answer
different questions; without them every change is a guess.

## Why it exists
- **LLMs break classical ML evaluation.** The old recipe was tidy: hold out a test set, compute one
  metric, compare. LLMs break three of its assumptions at once.
  - *Open-ended outputs:* there is rarely a single correct string to match.
  - *Stochastic and prompt-sensitive:* the same input can score differently across runs and trivial
    rewordings.
  - *Black box you did not train:* you cannot reason about what it learned from its data; you can only
    observe it.
- **The vibe check fails.** A handful of playground prompts has a sample size in the single digits, a
  selection bias toward inputs the author thought of, and no memory: nobody can say whether last week's
  version was better.
- **The leaderboard fails.** The highest public benchmark score measures general capability on tasks
  that are not yours, on data the model may have seen in training, and says nothing about your
  retrieval, prompt or tools.
- **Wait-for-production fails.** Reading thumbs-up rates after shipping is slow, confounded by traffic
  mix, and lets the regression reach users first.
- **What evaluation buys.** A change becomes decidable before it ships: a repeatable measurement on a
  fixed set of inputs, graders checked against humans, and enough samples that the difference between
  two versions is larger than the noise.

## How it works
### Two questions, two kinds of evaluation
| | Capability (model) evaluation | Application (system) evaluation |
|---|---|---|
| Question | "What can this model do in general?" | "Does my prompt + retrieval + model do my task on my data?" |
| Measures | Models against each other on standardized tasks (MMLU, HumanEval, SWE-bench, GPQA) | Your whole pipeline: system prompt, retrieved context, tool calls, output parsing and model together |
| Job | Model selection and progress tracking | Deciding whether a specific change (new prompt, new chunking, cheaper model) is safe |
| Needs | Public benchmark | A dataset you own and graders tied to your task |
| Weakness | Contamination, saturation, ignores everything you add around the model | Only covers what you put in the dataset |

Nearly all day-to-day eval work is application evaluation.

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
- **Grader.** Chosen by output type, not by convenience (table below).
- **Aggregation.** Report per slice (intent, language, difficulty, customer), *because* a 95% average
  can hide a 40% slice that matters most.

| Output type | Grader |
|---|---|
| Classification | Accuracy, precision, recall, F1 |
| Extraction | Field-level precision/recall against labels |
| Code | Unit tests pass or fail |
| Open text | A rubric applied by a calibrated LLM judge or a human |
| RAG | Retrieval recall/precision plus faithfulness of the answer to the retrieved context |

- **String overlap is a weak proxy.** BLEU and ROUGE are weak proxies for open-ended quality and should
  not be the headline.

### Levels of evaluation
- **Component (unit) evals** test one stage: did retrieval return the right chunk, did the classifier
  pick the right route, did the extractor fill the right fields. They localize failures.
- **End-to-end evals** test the user-visible result; this is what stakeholders care about.
- **You need both.** An end-to-end failure with no component evals is undiagnosable. Green component
  evals with a red end-to-end eval tells you the integration is the problem.
- **Offline evals** run on the fixed dataset before a change ships; they are for decisions.
- **Online evals** (A/B tests, sampled judge scoring of live traffic, user feedback) run after; they
  catch what the dataset did not cover and feed new items back into it.

### Reading the number
A pass rate is an estimate from a sample. For a proportion *p* on *n* items:

```
standard error = √(p(1−p)/n)        # p = 0.8, n = 100  →  0.04  →  95% interval ≈ ±8 points
```

- **Small gaps are noise.** Two prompts scoring 81% and 84% on 100 items are indistinguishable.
- **Compare on the same items and count flips.** A paired comparison has far less noise than two
  independent averages, *because* item difficulty is shared by both versions and cancels. Only items
  that changed verdict carry information about the difference.
- **Repeat items when temperature is above zero.** There are two sources of variance: which items you
  picked and which sample each item drew. The second does not shrink by adding items.
- **Grow the dataset where the decision is tight**, not everywhere.

### Quality is a vector, not a scalar
- **Axes:** task correctness, safety and policy compliance, format validity (does the downstream parser
  accept it), latency and cost per request.
- **They do not average.** A 2-point quality gain that doubles p95 latency, or a cheaper model equal on
  average but failing a hard safety check, is a different decision from "the score went up".
- **Practice:** report each axis separately, decide in advance which are hard constraints and which are
  traded, and compute them in the same run so they describe the same outputs.

### The loop
Evaluation is not a phase; it is a loop:
1. Look at real failures (error analysis).
2. Turn each failure class into a criterion and a few dataset items.
3. Add the grader to the harness.
4. Gate releases on the result.
5. Monitor production and feed new failures back in.

- **Tuning spoils the estimate.** Any set you tune against stops being an unbiased estimate of quality
  (Goodhart's law applied to prompts). So the dataset keeps a held-out split read only when deciding to
  ship, and online signals remain the final check.
- The remaining lessons in this module are the pieces of that loop.

## Diagram
Follow one item through the eval, then the loop back: per-slice scores gate the release, and production
failures return to the dataset.

```mermaid
flowchart TD
    D[("Golden dataset, versioned")] --> S["System under test: pinned model, prompt, index"]
    S --> O["Raw output"]
    O --> G["Grader chosen by output type"]
    G --> A["Aggregate per slice with intervals"]
    A --> Q{"Paired diff beats noise and no slice below floor?"}
    Q -->|"yes"| R["Ship behind a canary"]
    Q -->|"no"| F["Fix and re-run"]
    F --> S
    R --> M["Online evals and monitoring"]
    M -->|"new failures become items"| D
```

## Examples
### Worked scenario: swapping the embedding model in a RAG assistant
Numbers illustrative. The golden dataset has 300 questions stratified across six intents, each with a
reference answer and the ids of the document chunks that answer it.

| Level | Metric | Old | New |
|---|---|---|---|
| Component | Retrieval hit rate@5 | 0.84 | 0.90 |
| End-to-end | LLM-judge pass rate (judge previously checked against human labels) | 0.78 | 0.83 |

- **Hit rate@5** is the fraction of items with at least one correct chunk in the top five. It is often
  loosely called recall@5, which strictly is the fraction of all relevant chunks retrieved.
- **Is +5 points real? Unpaired, no.** The standard error at p ≈ 0.8, n = 300 is
  √(0.8 × 0.2 / 300) ≈ 0.023, so each number carries roughly ±4.5 points. An unpaired test on the
  difference (standard error ≈ 0.032, z ≈ 1.5) cannot call it.
- **Paired, probably yes.** It ignores unchanged items: 27 items flipped fail → pass and 12 flipped
  pass → fail. Net +15 of 300 is the +5 points; an exact sign (McNemar) test on 27 versus 12 gives
  p ≈ 0.02.
- **Slice view.** Of the 12 regressions, 9 are in the "billing" intent, where the new embeddings rank a
  policy FAQ above the customer's own invoice data.
- **Decision.** Not "ship" or "don't ship" but: fix billing retrieval, re-run, then ship behind a canary
  while sampling live answers through the same judge. That is a specific, attributable, slice-level
  reason rather than an overall feeling.

### Paired comparison in pure Python
Runnable with the standard library; reproduces the numbers above from per-item verdicts.

```python
from math import comb, sqrt

def paired_report(old, new):
    """old, new: dicts item_id -> bool (pass), graded on the SAME items."""
    ids = old.keys() & new.keys()
    gained = sum(1 for i in ids if not old[i] and new[i])   # fail -> pass
    lost = sum(1 for i in ids if old[i] and not new[i])     # pass -> fail
    n, k = gained + lost, min(gained, lost)
    p_exact = min(1.0, 2 * sum(comb(n, j) for j in range(k + 1)) / 2**n)  # two-sided sign test
    p_new = sum(new[i] for i in ids) / len(ids)
    se = sqrt(p_new * (1 - p_new) / len(ids))
    return dict(n=len(ids), gained=gained, lost=lost, p_value=round(p_exact, 3),
                new_rate=round(p_new, 3), ci95=round(1.96 * se, 3))

# Rebuild the scenario: 300 items, 234 pass before (0.78), 27 gained, 12 lost -> 249 pass (0.83)
old = {i: i < 234 for i in range(300)}
new = dict(old)
for i in range(234, 261): new[i] = True      # 27 fail -> pass
for i in range(0, 12):    new[i] = False     # 12 pass -> fail
print(paired_report(old, new))
# {'n': 300, 'gained': 27, 'lost': 12, 'p_value': 0.024, 'new_rate': 0.83, 'ci95': 0.043}
```

## Production patterns
- **Eval gate in CI.** Run the harness on every pull request that touches a prompt, model id, retrieval
  setting or tool definition, and block merge when a hard-constraint axis fails. Monitor run cost and
  flaky items that flip with no change.
- **Paired diff report on every change.** Post the gained/lost item lists and the sign-test p-value as
  the PR comment instead of two averages, so reviewers see which items moved. Monitor the share of
  changes merged with p above your threshold.
- **Per-slice floors.** Declare a minimum pass rate per intent, language or customer next to the
  overall target, and fail the run when any slice drops below it. Monitor the weakest slice, not the
  mean.
- **Held-out split read only at ship time.** Keep a split that prompt iteration never touches and log
  every read of it. Monitor the gap between dev and held-out scores; a growing gap means overfitting.
- **Every production bug becomes a dataset item.** Triage tickets and sampled low-scoring live answers
  into labeled items with a source tag. Monitor how many new items arrive per week and which failure
  classes they cover.

## Trade-offs
- **Deterministic graders vs judges and humans.** Deterministic graders are cheap, fast and exact, but
  only apply when the output has a checkable form. Once the task is open-ended you pay for judges or
  humans and inherit their noise.
- **Dataset size vs cost.** A larger golden dataset tightens confidence intervals, but every item costs
  labeling time and every run costs tokens. The right size is the smallest one at which your typical
  decisions are clear.
- **Public benchmarks vs relevance.** They give comparability across models for free, but stop being
  informative once contamination or saturation sets in, and never measured your task in the first place.
- **Offline vs online.** Offline evals are reproducible and fast but only cover what you thought to
  include. Online evaluation catches the rest at the price of exposing real users and slower, confounded
  signals.

## Pitfalls
- **Confusing capability with application evaluation.** Choosing from a leaderboard and skipping the
  task eval. Symptom: a "state of the art" model underperforms the cheaper one on your own extraction job.
- **Reporting a single aggregate.** Symptom: a stable overall score while one customer segment or one
  intent quietly degrades after a change. Fix: per-slice reporting and floors.
- **Treating a 3-point move on 100 items as a result.** Symptom: prompt changes that "improved things"
  then regress on the next run with no change at all. Fix: paired comparison and more items.
- **String-overlap metrics as the headline for open text.** Symptom: correct answers scored as failures
  because they are phrased differently from the reference.
- **Trusting an uncalibrated LLM judge.** Symptom: a high eval score alongside user complaints the judge
  is blind to. Fix: measure its agreement with human labels first.
- **Iterating against the release set.** Symptom: a dev-set score that keeps climbing while the held-out
  split and production metrics stay flat.

## When to use
- **Default, not exception:** any change to a prompt, model, retrieval setting or tool definition that
  reaches users is measured against a fixed dataset first.
- **Benchmarks only to shortlist:** use capability benchmarks to shortlist models, then decide on your
  own eval.
- **Measure at the decision's level:** a retrieval change needs retrieval metrics; a prompt change needs
  end-to-end pass rates per slice.
- **Day to day:** a harness run in CI, a dashboard of per-slice pass rates with intervals, and a rule that
  every production bug becomes a dataset item.
- **Interviews:** explain why a benchmark score does not transfer to a task, how you would pick a grader
  for a given output type, and why a small difference on a small set is not a decision.

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

## Interview questions
### What is the difference between capability evaluation and application evaluation, and why does it matter?
- **Capability evaluation** compares models on standardized tasks such as MMLU or SWE-bench; it answers
  "what can this model do in general" and is useful for shortlisting.
- **Application evaluation** runs your whole pipeline (prompt, retrieval, tools, parsing) on a dataset
  you own, because that is the only thing that predicts whether your change works on your task.
- **Why it matters:** a leaderboard gain can be contamination, a different prompt format or an
  irrelevant task, so model choice is shortlisted by benchmark and decided by your own eval.

### Your end-to-end pass rate dropped 6 points after a release. How do you find the cause?
- **Confirm it is real:** check the run used the same dataset version and pinned config, and do a
  paired comparison against the baseline to list which items flipped.
- **Slice the losses:** group the pass-to-fail items by intent, language and customer; a cluster in
  one slice usually points at one change.
- **Use component evals to localize:** if retrieval hit rate fell, the index or embedding changed; if
  retrieval is green, look at the prompt, model or output parsing.

### Prompt v2 scores 3 points higher on 300 items. How would you decide whether to ship it?
- **Do not compare two averages.** At p ≈ 0.8 and n = 300 each score carries roughly ±4.5 points, so
  an unpaired 3-point gap cannot be called.
- **Count flips on the same items** and run an exact sign (McNemar) test on gained versus lost; only
  discordant items carry information.
- **Check the other axes and slices:** latency, cost, format validity and per-slice floors in the same
  run, then ship behind a canary if all hold.

### When would you not rely on an LLM judge as your main grader?
- **When the output is checkable:** classification, extraction and code have exact graders (F1,
  field-level precision and recall, unit tests) that are cheaper, faster and exact.
- **When the judge is uncalibrated:** without measured agreement against human labels, a judge score is
  another vibe check and can be blind to the failures users complain about.
- **Trade-off:** judges unlock open-ended tasks at the price of cost and noise, so use them only where a
  deterministic grader cannot express the criterion.

### How do you stop the team overfitting prompts to the eval set?
- **Keep a held-out split** that prompt iteration never reads, used only at ship time, because any set
  you tune against stops being an unbiased estimate (Goodhart's law).
- **Watch the dev versus held-out gap;** a dev score that keeps climbing while held-out and production
  stay flat is the symptom.
- **Keep feeding production failures in,** so the dataset tracks failures that actually occur rather
  than the ones the team has already fixed.

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
