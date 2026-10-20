---
id: timeboxing
module: scoping
name: Timeboxing
kind: practice
difficulty: core
est_minutes: 11
status: reviewed
related:
  - scoping/rapid-prototyping-poc
  - scoping/problem-decomposition
  - scoping/success-metrics-kpis
  - scoping/statement-of-work
  - scoping/dependency-and-risk-management
  - evals/error-analysis
---

## TL;DR
Timeboxing fixes the time and lets scope flex: you commit to answering a specific question within a set
budget, then make an explicit decision (ship, continue, pivot, stop) when the box closes. It matters more
in AI work than in ordinary software because quality improves asymptotically, so without a box a team can
keep tuning prompts indefinitely without ever deciding whether the approach is good enough.

## Why it exists
Classic software estimation assumes the work is mostly known: you can list the features and the effort is
roughly the sum of the parts. AI work breaks that assumption. Whether an LLM can extract fields from this
customer's scanned invoices at 95% accuracy is an empirical question nobody can answer by thinking; you
find out by trying. And the trying has no natural end. Accuracy climbs fast at first, then flattens: the
first day of prompt work might move a metric from 60% to 80%, the next week from 80% to 85%, and the
following month from 85% to 87%. There is always one more prompt variant, one more retrieval tweak, one
more model to try.

The naive alternative is to estimate the work ("the extraction feature will take three weeks") and track
against it. That fails because the estimate is a guess about an unknown, and when it is wrong the
team keeps going, since stopping feels like admitting failure and the next tweak always looks promising.
The result is the familiar pattern of a proof of concept that is "almost there" for three months.

A timebox inverts the commitment. Instead of promising an outcome by a date, you promise a *decision* by a
date, backed by evidence. Scope is the variable: if the box closes and the target is not met, that is
information, not a slip. This also protects the customer relationship, because the customer is buying
a de-risked answer on a known schedule rather than an open-ended research project.

## How it works
### 1. Name the question, not the task
A good timebox answers one question with a yes/no or a number: "Can we reach 90% field-level accuracy on
their real invoice sample with an off-the-shelf model?" A bad one names an activity: "Work on extraction."
The question form matters *because* it tells you when you are done and what evidence counts.

### 2. Set the budget from the value of the answer
The length of the box should match what the answer is worth and how much uncertainty it removes, not how
long the work "should" take. A question that gates a large contract justifies two weeks; a question about
which of two chunking strategies to use justifies a day. A rough value-of-information test keeps this
honest:

```
worth boxing if   cost of box  <  P(answer changes the decision) × cost of deciding wrongly
```

Three engineers for two weeks is about 30 person-days. If the answer has a one-in-three chance of
flipping a decision that would otherwise sink six months of a larger team into the wrong design, the box
is cheap. If the team would make the same choice whatever the result, the box buys nothing and should
be skipped or shrunk. Typical boxes are hours (a spike), days (a feasibility check) or a few weeks (a
PoC), and almost never open-ended. Scrum sprints are timeboxes too, but for delivery cadence; the
discovery-style box here differs in that "stop" is an expected, legitimate outcome.

### 3. Write the exit criteria before starting
Before any work, write down the decision rule: what result means go, what means stop, and what means
pivot. For example: "≥ 90% on the 100-document set: proceed to pilot. 75–90%: one more week focused only
on the top two error categories. Below 75%: stop and recommend a human-in-the-loop design." Pre-committing
matters *because* after a week of effort everyone is anchored on their sunk cost, and criteria written
afterwards drift toward whatever was achieved.

### 4. Order the work by risk, with a scope ladder
Split scope into must, should and could. Do the riskiest must-have first, *because* if it fails nothing
else matters and you want to learn that on day two, not day nine. Build the evaluation set before tuning,
so every hour of tuning moves a number you trust.

### 5. Checkpoint at fixed points
A two-week box gets a midpoint check: are we on a trajectory to the target, or flat? Make it arithmetic
rather than mood:

```
required rate = (target − current) / days remaining
observed rate = gain over the last few working days / those days
```

If the observed rate is well below the required rate, the box will not close the gap by grinding, and
the second week should cut scope, change approach, or prepare a stop recommendation. Two cautions apply.
Learning curves bend down, so the observed rate overstates the future one. And a gain only counts if it
exceeds the eval set's noise: on 100 documents, a one-point move can come from a handful of borderline
cases flipping, so "we went from 86% to 87%" may be no movement at all.

### 6. Close the box with a decision record
At the end, record the question, the evidence, the decision and what was learned, including dead ends.
Extending a box is allowed, but only as an explicit new box with its own question and criteria, never as a
silent slide.

## Example
A forward-deployed team gets two weeks (10 working days) to test whether an LLM can extract 12 fields from
a logistics customer's bills of lading.

- **Question:** can we reach ≥ 90% field-level accuracy on the customer's real documents?
- **Exit rule (written day 0):** ≥ 90% → pilot; 80–90% → one focused extra week; < 80% → recommend a
  review-queue design where the model drafts and a clerk confirms.
- **Days 1–2:** obtain 150 real documents, have the customer's clerks label 100 of them (1,200 field
  values). This is the must-have; without it every later number is opinion.
- **Day 3:** baseline with a strong general model and a simple prompt: 71% (852 of 1,200 fields correct,
  348 wrong).
- **Days 4–5:** error analysis shows about 40% of the 348 errors (roughly 140, out of only 300 values in
  those fields) come from three fields: the consignee address on multi-line stamps, the weight unit and
  the date format. Fixing those with explicit format instructions and examples: 83% (about 996 correct).
- **Midpoint check (day 5):** at 83% with five days left, the required rate is (90 − 83) / 5 = 1.4 points
  per day; the last two days delivered about 6 points per day. Even allowing for the curve flattening,
  continuing is justified. Drop the "could" item (a review UI) to protect the time.
- **Days 6–9:** OCR preprocessing for stamped regions moves accuracy to 88%; further prompt variants add
  under a point.
- **Day 10:** 88% (about 1,056 correct, 144 wrong) lands in the 80–90% band. Error analysis shows roughly
  60% of the remaining errors sit in the ~20 documents with handwritten weights and annotations. Decision
  recorded: one more week, scoped only to handwritten documents, with its own pass/fail threshold.

The numbers here are illustrative, but the shape is typical: rapid early gains, then a plateau that only a
pre-written rule stops the team from chasing indefinitely.

## Trade-offs
- Fixed time gives predictable schedules and forces decisions, but you may stop just short of a working
  solution; this flips when the remaining gap is a known, well-scoped engineering problem.
- Short boxes reduce sunk cost and expose dead ends early, but too-short boxes cannot produce a
  trustworthy eval set, so the decision rests on anecdotes instead of measurements.
- Pre-written exit criteria resist motivated reasoning, but they can be wrong if written before you
  understood the data; allow one explicit revision at the first checkpoint, with the reason recorded.
- Cutting scope keeps the date, but repeatedly cutting the same must-have item means the question itself
  was too large and should have been decomposed into smaller boxes.

## Pitfalls
- Silently extending the box ("just two more days") until the PoC has run for months; the symptom is a
  status update that says "almost there" three times in a row.
- Timeboxing an activity instead of a question, so the box ends with work done but no decision; the
  symptom is a closing meeting where nobody can say whether to proceed.
- Tuning prompts before building the evaluation set, so progress is judged by eyeballing outputs; the
  symptom is accuracy claims that collapse when someone runs a held-out sample.
- Setting the box length by how long the work feels like it should take rather than by the value of the
  answer, which shows up as two-week investigations into decisions worth a day.
- Treating a "stop" outcome as a team failure, so nobody ever recommends stopping; the symptom is an
  organisation whose boxes all end in "promising, continue" and never in a stop.

## When to use
Reach for a timebox whenever the outcome is uncertain and the work could expand without limit: feasibility
checks, PoCs, model or vendor bake-offs, debugging an unexplained quality drop, and any research spike
inside a delivery project. Do not timebox well-understood engineering work, where ordinary estimation is
fine and the scope is fixed by requirements. Measure progress by the metric the question names, plotted
over the box, not by hours spent. In interviews and customer conversations, a timebox is how you say
"we will know by Friday" credibly: you name the question, the evidence and the decision rule up front.

## Role lens
- **Engineer:** Builds the eval set first, plots the metric daily, and flags a flat curve at the midpoint
  instead of quietly trying more prompt variants.
- **Architect:** Decides which unknowns get boxed and in what order, so the riskiest architectural
  assumption is tested before the team invests in surrounding infrastructure.
- **FDE:** Sells a fixed-length discovery or PoC engagement with written exit criteria, which keeps the
  customer relationship anchored on a decision date rather than an open-ended promise.

## Check yourself
**Q:** Your two-week PoC ends at 84% accuracy against a 90% target, the team is confident another week will
get there, and the customer is enthusiastic. What do you do?
**A:** Go back to the exit rule written at the start. If it defined a middle band, follow it; if not, treat
an extension as a new box, not a continuation. Ask what the error analysis says: if most remaining errors
cluster in one or two identifiable categories with a concrete fix, a one-week box scoped only to those is
justified, with its own pass/fail threshold. If errors are diffuse and the curve has flattened, another
week of tuning is unlikely to close a six-point gap, so recommend a design that tolerates 84% (human review,
narrower scope) instead. Enthusiasm is not evidence; the trajectory and error distribution are.

## Quiz
### Q: What is the defining feature of a timebox compared with an ordinary estimate?
- [ ] It uses smaller units of time, such as hours instead of days
- [x] Time is fixed and scope flexes, with an explicit decision when the time runs out
- [ ] Scope is fixed and the deadline moves when new information appears
- [ ] It removes the need for success criteria because the date is the criterion
> An estimate predicts how long fixed scope will take; a timebox fixes the time and ends in a decision.
> The date alone is not a criterion; you still need the question and exit rule to know what the result means.

### Q: Why should exit criteria be written before the timeboxed work begins?
- [ ] So the team can ask the customer to sign them as part of the contract
- [ ] Because criteria cannot be measured until the eval set exists
- [x] Because after investing effort, people anchor on sunk cost and criteria drift toward whatever was achieved
- [ ] Because the model provider requires a target accuracy for evaluation access
> Pre-commitment protects the decision from motivated reasoning. Contract signature can help but is not the
> reason; the core issue is that judgment after effort is biased toward continuing.

### Q: At the midpoint of a two-week box, accuracy has been flat for three days. What is the best response?
- [ ] Keep going, since the plan allocated two weeks and the second week may bring gains
- [ ] Immediately extend the box by a week to give the approach a fair chance
- [x] Treat it as an early signal: cut scope, change approach, or prepare a stop recommendation
- [ ] Switch to measuring hours spent so the effort is visible to stakeholders
> Checkpoints exist to detect a flat trajectory early. Continuing unchanged spends the second week hoping;
> extending without a new question is a silent slide, and hours spent measure effort, not learning.
