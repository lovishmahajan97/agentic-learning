---
id: evals
title: Evals
---

## Overview
This module is about producing numbers you can trust about whether an LLM system does its job, and
using those numbers to decide what ships. It starts by separating capability evaluation (what a model
can do in general, answered by benchmarks) from application evaluation (whether your prompt, retrieval
and model do your task on your data), then builds the core machinery of the second: task-specific
acceptance criteria that define "done", a versioned golden dataset labeled by people with authority
over the task, and an eval harness that runs, grades, aggregates per slice with confidence intervals
and compares runs. Grading open-ended output leads to LLM-as-a-judge, which is only trustworthy once it
is calibrated against human labels, scored with clear rubrics, compared pairwise where absolute scales
fail, and anchored by inter-annotator agreement on the labels themselves. Specialised metrics follow:
pass@k for executable tasks, retrieval metrics, faithfulness and hallucination evaluation for RAG.
Regression, metamorphic and property-based tests guard known behaviour and robustness without labels;
adversarial evaluation and red teaming cover the worst case. Offline and online evaluation, A/B tests,
error analysis and eval-driven development close the loop from production failures back into the
suite. Afterwards you can design, build and defend an evaluation stack for a real feature and gate
releases on it. It matters most for AI Engineers who change prompts and models every week, and for
FDEs who need a customer to sign off on what "working" means.

## Why it matters
- **Engineer:** You turn every prompt, model, retriever or tool change into a paired harness run against a golden set with per-slice intervals, read the items that flipped, and refuse to merge a change whose eval diff you cannot explain.
- **Architect:** You decide where evaluation lives in the delivery pipeline: which graders are deterministic versus judged, which suites gate CI versus releases, how offline scores are validated against online outcomes, and which tooling owns datasets, traces and experiments.
- **FDE:** You write acceptance criteria with the customer before committing to a date, build a golden set from their real documents and labelers, and show a calibrated report that makes sign-off unambiguous instead of resting on a demo that looked fine.

## Mental model
Hold one picture: an eval is a measuring instrument, and every part of it can be miscalibrated. The
specification (acceptance criteria and rubrics) says what to measure; the reference (a golden dataset
whose labels humans agree on) says what correct looks like; the harness runs the system under pinned
configuration and stores raw outputs; graders (exact checks, executable tests, retrieval metrics,
calibrated judges) turn outputs into scores; and statistics (paired comparisons, intervals, pass@k)
say whether a difference is real. Each layer has its own error: vague criteria, noisy or leaked labels,
biased judges, too few items. Around the instrument runs a loop: error analysis on real traces finds
failure modes, they become criteria and items, adversarial and metamorphic suites probe what averages
hide, offline gates decide what ships, and online measurement checks that offline measured the right
thing. Trust a number only as far as its weakest layer.

## Suggested order
1. model-evaluation — frames the capability-versus-application split and why every change needs a measured number, the lens for the whole module.
2. task-specific-acceptance-criteria — defines what "done" means for one task before any dataset or grader is built.
3. golden-dataset — builds the agreed, versioned reference every eval number is computed against.
4. eval-harness — turns dataset, system and graders into a repeatable, comparable run with slices and intervals.
5. error-analysis — teaches reading real traces and counting failure modes, which is where criteria and items should come from.
6. benchmarking — covers public benchmarks as a model-shortlisting filter once the difference from your own eval is clear.
7. dataset-contamination — explains why benchmark and golden-set scores inflate through leakage, right after benchmarks are introduced.
8. pass-k — gives the correct estimator for sampled executable tasks, needed to read coding benchmarks and your own code evals.
9. llm-as-a-judge — introduces model graders for open-ended outputs that no exact check can score.
10. rubrics — shows how to turn "is it good?" into observable, mostly binary criteria a judge or human applies consistently.
11. inter-annotator-agreement — measures whether the human labels a judge is scored against are themselves reliable.
12. judge-calibration-and-bias — measures and mitigates position, length and self-preference bias against those human labels.
13. pairwise-comparison — offers relative judging, win rates and Bradley–Terry ranking once judge biases are understood.
14. retrieval-evaluation — isolates the retriever with recall@k, MRR and NDCG, the first stage of any RAG eval.
15. faithfulness-groundedness — scores whether generated claims are supported by the retrieved context, the generation-side RAG metric.
16. hallucination-evaluation — generalises claim-level verification to fabrication, unanswerable items and abstention.
17. regression-tests — pins known behaviour and detects per-item flips across changes despite sampling noise.
18. metamorphic-testing — checks label-free relations between outputs to expose brittleness the golden set cannot see.
19. property-based-testing — applies generated inputs and shrinking to the deterministic scaffolding around the model.
20. red-teaming — discovers the attacks a goal-driven adversary would use against prompts, data and tools.
21. adversarial-evaluation — turns those discovered attacks into a versioned suite with attack success and over-refusal rates.
22. offline-vs-online-evaluation — lays out the ladder from offline gates through shadow mode and canaries to live measurement.
23. a-b-testing — gives the causal online answer, with randomisation unit, power, guardrails and no peeking.
24. eval-driven-development — ties everything into a workflow where the eval suite is the executable specification of a feature.
25. tooling — maps Promptfoo, RAGAS, DeepEval, Braintrust, LangSmith and Langfuse onto the pipeline once you know what it must do.

## Capstone
### Brief
Build and defend the evaluation stack for a RAG assistant that answers employee questions about a
company's HR and expense policies, then use it to decide a real change: swapping the generation model
for a cheaper one and changing the chunking strategy. Start by reading at least 50 traces from a
baseline version and writing an error-analysis taxonomy with counts. From it, write acceptance criteria
that separate hard gates (no invented policy, no answer to out-of-scope or unanswerable questions) from
soft thresholds with slice floors, plus latency and cost limits. Build a golden set of 150 to 300
stratified items, including unanswerable and false-premise questions, with a held-out split and a
contamination check against any few-shot examples. Have two people label an overlap sample to measure
agreement. Write a harness that pins configuration, stores raw outputs before grading, and reports
retrieval recall@k and MRR, claim-level faithfulness, hallucination and abstention rates, and a
rubric-based LLM judge whose position bias and agreement with humans you have measured. Add a regression
tier seeded from known bugs, a metamorphic suite (paraphrase, irrelevant-paragraph insertion), property
tests on the chunker, and a small prompt-injection suite through poisoned documents. Wire the fast tier
into CI as a gate and write an online plan for the winning candidate.

### Deliverables
- An error-analysis sheet of at least 50 read traces with a failure taxonomy, counts per category, and the criteria and golden items each top category produced.
- An acceptance-criteria document listing hard gates, soft thresholds with slice floors, and latency and cost limits, each with its grader and scope.
- The versioned golden dataset with slice labels, a held-out split, a grader spec per item, an inter-annotator agreement report on the overlap sample, and a contamination-check script.
- The eval harness, runnable from one command, producing a per-slice report with confidence intervals, plus a CI configuration that runs the fast tier and fails the build on a hard-gate violation.
- A judge calibration report showing agreement and kappa against human labels next to human–human agreement, position-swap inconsistency rate, and length preference.
- Regression, metamorphic, property-based and adversarial suites, each with its own pass or violation rate on the baseline.
- A decision memo on the model swap and chunking change, citing paired per-item results, regressed items read, cost per answer, and an online rollout plan (shadow, canary, A/B with a power estimate and guardrails).

### Rubric
- Every reported metric carries a confidence interval or paired comparison, and the decision memo does not call a difference significant when its interval includes zero.
- The judge is not used as a release gate until its true-positive and true-negative rates against human labels are reported, and its agreement is within reach of the human–human ceiling measured on the same items.
- Running the harness twice on an unchanged system produces per-item flip rates below a stated flakiness tolerance, and the regression tier distinguishes a planted regression from that noise.
- The unanswerable and false-premise slices are present and reported separately, and the fabrication rate on them is a hard gate rather than averaged into overall accuracy.
- A planted prompt injection in a retrieved document is caught by the adversarial suite, and the report lists over-refusal on benign questions next to attack success rate.
- At least one golden item and one criterion trace back to a named category in the error-analysis taxonomy, and the held-out split was never used while tuning prompts.
