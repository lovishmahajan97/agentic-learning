---
id: failure-analysis
module: delivery-ownership
name: Failure analysis
kind: practice
difficulty: core
est_minutes: 15
related:
  - delivery-ownership/production-debugging
  - delivery-ownership/root-cause-analysis-and-postmortems
  - evals/error-analysis
  - reliability/fmea
  - customer-data-evaluation/production-feedback-classification
  - rag-retrieval/retrieval-failure-diagnosis
  - agent-reliability/agent-trajectory-evaluation
---

## TL;DR
Failure analysis looks across many failures to find the few failure modes that cause most of the harm,
instead of fixing whichever one was reported last. You sample failures without bias, label each with
what went wrong and at which pipeline stage, count, weight by severity, and spend engineering effort on
the top of that ranked list. In LLM systems this matters because failures are diverse and quiet.

## Why it exists
Production debugging explains one failure. But an AI product in production fails in hundreds of small
ways a day: an answer that misses a detail, a tool call with a wrong argument, a refusal that should
not have happened, a timeout. If the team reacts to each report as it arrives, effort goes to whatever
is loudest: the angry enterprise customer, the screenshot on social media, the bug the CEO hit. The
loudest failure is rarely the most common, and fixing it one at a time leaves the dominant failure modes
untouched.

The naive alternatives fail in specific ways. Reading the aggregate metric ("satisfaction dropped from
82% to 76%") tells you something is wrong but not what to change. Swapping in a bigger model or rewriting
the whole prompt is a guess that costs money and often moves the error elsewhere. Writing an automated
classifier for failure types before anyone has looked at the data bakes in categories that the team
imagined rather than the ones that occur.

Failure analysis exists to turn a pile of individual failures into a ranked, quantified list of causes,
so that the next fix is the one with the largest expected reduction in harm.

## How it works
### 1. Define what counts as a failure
Write down the signals: explicit (thumbs-down, escalation to a human, complaint), implicit (user
rephrases the same question, abandons the session, copies nothing), system (exception, timeout, schema
validation failure, tool error) and evaluation (an LLM judge or rule flags the output). Each source is
biased: thumbs-down over-represents engaged, annoyed users; system errors miss confident wrong answers.
Using several sources reduces the blind spots of any one.

### 2. Sample without bias
Pull a random sample of failing cases from a fixed window, stratified if needed by tenant or use case,
rather than the cases someone forwarded. A sample of a few hundred is usually enough to rank the top
failure modes, because you are estimating proportions, not finding every bug. Also pull some
*successes*: they calibrate whether a "failure pattern" is really specific to failures.

### 3. Open coding, then a taxonomy
Read each case end to end (the trace, not just the final answer) and write a short free-text note on the
*first* thing that went wrong. Anchoring on the first error matters because later errors are often
consequences of it: a wrong retrieval causes a wrong citation causes a wrong answer, and you want to count
one failure, not three. After a pass, cluster the notes into categories ("retrieved outdated policy",
"tool called with wrong date format", "ignored instruction to ask for order ID"). Each category is tagged
with the pipeline stage where it originated. This is the same discipline the evals module calls error
analysis; here it is applied to live production traffic.

### 4. Count and weight
Compute each category's share, with uncertainty: a proportion *p* from *n* samples has standard error
√(p(1−p)/n). Then weight by severity, because a rare failure that leaks another tenant's data outranks a
common one that adds an extra sentence. A simple score is frequency × severity × reach (fraction of users
or revenue exposed).

### 5. Attribute and choose fixes
For the top categories, ask what the cheapest effective intervention is at the stage that originated it:
index freshness for stale retrieval, a schema or validator for malformed tool arguments, a prompt rule or
few-shot example for ignored instructions, a guardrail for unsafe content. Fixing at the originating stage
beats patching downstream symptoms.

### 6. Make it repeatable
Turn the top categories into automated detectors or eval cases so their rate becomes a tracked metric,
and repeat the manual pass periodically, because new features and traffic shifts create new categories.

### Reactive versus proactive
This lesson is about analysing failures that happened. Its proactive counterpart, FMEA, enumerates
failure modes before launch and scores severity, occurrence and detectability; the two feed each other,
since observed failures update the FMEA's occurrence estimates.

## Example
A support assistant handles about 10,000 conversations a week. The satisfaction metric has dropped and
the team's instinct is to switch to a larger model. Instead, they randomly sample 200 conversations that
had a failure signal and code the first error in each:

| Category (originating stage)            | Count | Share |
|-----------------------------------------|------:|------:|
| Retrieved outdated policy (retrieval)   | 76    | 38%   |
| Wrong order lookup argument (tool)      | 44    | 22%   |
| Answered when it should escalate (policy)| 30   | 15%   |
| Reasoning error with correct context (model) | 24 | 12% |
| Timeout or provider error (infra)       | 16    | 8%    |
| Other                                   | 10    | 5%    |

The top share has standard error √(0.38 × 0.62 / 200) ≈ 0.034, so a 95% interval of roughly 31%–45%:
wide, but clearly the largest bucket. Only 12% of failures are genuine model reasoning errors, so a
bigger model would address at most about an eighth of the problem while increasing cost on every call.
Re-indexing on document change plus filtering superseded policies attacks 38%; validating the order-ID
format before the tool call attacks another 22%. The escalation category is lower in frequency but higher
in severity (a customer given a wrong commitment), so it goes second in priority despite its share.

## Trade-offs
- Manual reading of traces finds categories nobody anticipated, but costs expert hours; it flips toward
  automated classifiers once the taxonomy is stable and volume makes reading every week impractical.
- Larger samples narrow the confidence intervals, but ranking the top few modes rarely needs more than a
  few hundred cases; spend extra effort on rare, high-severity categories instead.
- A fine-grained taxonomy gives precise fixes but sparse counts per category; merge categories when most
  have only a handful of cases.
- Coding only the first error keeps counts honest, but hides compounding issues; record secondary errors
  separately when you suspect two independent defects.

## Pitfalls
- Analysing only the failures customers forwarded; the symptom is a priority list that tracks your
  largest accounts rather than your most common defects.
- Reading only final answers instead of full traces; the symptom is that almost everything is labelled
  "model hallucinated" and no stage-specific fix emerges.
- Counting cascaded errors as separate failures; the symptom is categories whose totals exceed the number
  of cases and fixes that all point at the same upstream defect.
- Building the automated failure classifier before doing a manual pass; the symptom is a large "other"
  bucket and categories that never change week to week.
- Ranking by frequency alone; the symptom is the team polishing tone while a rare data-exposure or
  wrong-commitment failure keeps recurring.

## When to use
Run a failure-analysis pass when an aggregate quality metric moves, before choosing between expensive
interventions (model swap, fine-tuning, re-architecture), at the end of a pilot, and on a regular cadence
for any system in production. Do not use it as a substitute for incident response: a single severe
failure needs debugging and mitigation now, not a sampling exercise. Measure category shares over time;
a fix is confirmed when its category's share falls in the next sample. In interviews, answering "quality
dropped, what do you do?" with "sample failures, categorise by stage, rank by impact" signals maturity.

## Role lens
- **Engineer:** Pulls the sample, codes traces, and converts the top failure categories into automated
  detectors and regression eval cases that run on every change.
- **Architect:** Uses the stage attribution to decide where to invest, for example retrieval freshness
  infrastructure versus model upgrades, and ensures traces exist to make analysis possible.
- **FDE:** Runs the analysis with the customer's subject-matter experts, whose judgement of what counts as
  a failure and how severe it is often differs from the vendor's assumptions.

## Check yourself
**Q:** Leadership wants to fine-tune a model because "accuracy is 78%". How do you decide whether
fine-tuning is the right investment?
**A:** I would sample a few hundred failing cases at random, read the full traces and code the first error
with its originating stage. Fine-tuning only addresses failures where the model had the right context and
still answered wrongly, or where style or format is the issue. If most failures originate in retrieval,
tool arguments or missing escalation rules, fine-tuning cannot fix them and the money is better spent at
those stages. I would show the category shares with confidence intervals, weight them by severity, and
estimate the ceiling each intervention could reach before committing.

## Quiz
### Q: Why should failure coding anchor on the first thing that went wrong in a trace?
- [ ] The first error is always the most severe one
- [x] Later errors are often consequences of the first, so counting them separately inflates downstream categories
- [ ] Models only make one error per conversation
- [ ] It makes the analysis faster because the rest of the trace can be skipped
> A wrong retrieval often causes a wrong citation and then a wrong answer; attributing all three to the
> originating defect keeps counts honest and points the fix at the stage that actually caused the failure.

### Q: Which failure sample is the least biased basis for prioritisation?
- [ ] All conversations escalated by the five largest customers
- [ ] Every thumbs-down from the past day
- [x] A random sample from all failure signals over a fixed window, plus a few successes for comparison
- [ ] The cases the support team flagged as most embarrassing
> Forwarded or explicit-feedback samples over-represent loud accounts and engaged users; random sampling
> across several signal types estimates true proportions, and successes show whether a pattern is failure-specific.

### Q: In a sample of 200 failures, 12% are model reasoning errors with correct context. What does that imply about switching to a stronger model?
- [ ] It will fix most failures because stronger models also retrieve better
- [ ] It is pointless because 12% is within the margin of error
- [x] It can address at most roughly that share, so other stages likely offer larger gains
- [ ] It should be done first because model errors are the hardest to fix otherwise
> A stronger model improves generation, not stale indexes or malformed tool arguments; its ceiling is about
> the share of failures that originate in the model, so larger categories elsewhere usually deserve priority.
