---
id: timeboxing
module: scoping
name: Timeboxing
kind: practice
difficulty: core
est_minutes: 11
status: crisp-verified
related:
  - scoping/rapid-prototyping-poc
  - scoping/problem-decomposition
  - scoping/success-metrics-kpis
  - scoping/statement-of-work
  - scoping/dependency-and-risk-management
  - evals/error-analysis
---

## TL;DR
Timeboxing fixes the time and lets scope flex: you commit to answer one question within a set budget,
then decide (ship, continue, pivot, stop) when the box closes. AI quality improves asymptotically, so
without a box teams tune prompts forever.

## Why it exists
- **AI work breaks classic estimation.** Estimation assumes known work: list the features, sum the effort.
  Whether an LLM can extract fields from this customer's scanned invoices at 95% accuracy is an empirical
  question nobody can answer by thinking; you find out by trying.
- **The trying has no natural end.** Accuracy climbs fast, then flattens: day one of prompt work might move
  a metric from 60% to 80%, the next week from 80% to 85%, the following month from 85% to 87%. There is
  always one more prompt variant, retrieval tweak or model to try.
- **Estimating the work fails.** "The extraction feature will take three weeks" is a guess about an unknown.
  When it is wrong the team keeps going, because stopping feels like admitting failure and the next tweak
  always looks promising. Result: a proof of concept that is "almost there" for three months.
- **A timebox inverts the commitment.** You promise a *decision* by a date, backed by evidence, not an
  outcome. Scope is the variable: missing the target when the box closes is information, not a slip.
- **It protects the customer relationship,** because the customer buys a de-risked answer on a known
  schedule rather than an open-ended research project.

## How it works
### 1. Name the question, not the task
- **Good:** one question with a yes/no or a number: "Can we reach 90% field-level accuracy on their real
  invoice sample with an off-the-shelf model?"
- **Bad:** an activity: "Work on extraction."
- **Why the question form matters:** it tells you when you are done and what evidence counts.

### 2. Set the budget from the value of the answer
Match the box length to what the answer is worth and how much uncertainty it removes, not to how long the
work "should" take. A rough value-of-information test keeps this honest:

```
worth boxing if   cost of box  <  P(answer changes the decision) × cost of deciding wrongly
```

- **Large stakes justify longer boxes.** A question that gates a large contract justifies two weeks; which
  of two chunking strategies to use justifies a day.
- **Worked check:** three engineers for two weeks is about 30 person-days. If the answer has a
  one-in-three chance of flipping a decision that would otherwise sink six months of a larger team into the
  wrong design, the box is cheap.
- **No decision change, no box.** If the team would make the same choice whatever the result, the box buys
  nothing; skip or shrink it.

| Box type | Typical length |
|---|---|
| Spike | hours |
| Feasibility check | days |
| PoC | a few weeks |
| Open-ended | almost never |

- **Scrum sprints are timeboxes too, but for delivery cadence.** The discovery-style box here differs in that
  "stop" is an expected, legitimate outcome.

### 3. Write the exit criteria before starting
Write the decision rule before any work: what result means go, stop or pivot. For example:

| Result on the 100-document set | Decision |
|---|---|
| ≥ 90% | proceed to pilot |
| 75–90% | one more week, focused only on the top two error categories |
| Below 75% | stop and recommend a human-in-the-loop design |

- **Why pre-commit:** after a week of effort everyone is anchored on their sunk cost, and criteria written
  afterwards drift toward whatever was achieved.

### 4. Order the work by risk, with a scope ladder
1. **Split scope** into must, should and could.
2. **Do the riskiest must-have first,** because if it fails nothing else matters and you want to learn that
   on day two, not day nine.
3. **Build the evaluation set before tuning,** so every hour of tuning moves a number you trust.

### 5. Checkpoint at fixed points
A two-week box gets a midpoint check: are we on a trajectory to the target, or flat? Make it arithmetic
rather than mood:

```
required rate = (target − current) / days remaining
observed rate = gain over the last few working days / those days
```

- **Observed well below required:** grinding will not close the gap. The second week should cut scope,
  change approach, or prepare a stop recommendation.
- **Caution 1, curves bend down:** the observed rate overstates the future one.
- **Caution 2, noise:** a gain only counts if it exceeds the eval set's noise. On 100 documents a one-point
  move can come from a handful of borderline cases flipping, so "we went from 86% to 87%" may be no
  movement at all.

### 6. Close the box with a decision record
- **Record** the question, the evidence, the decision and what was learned, including dead ends.
- **Extensions are new boxes.** Extending is allowed, but only as an explicit new box with its own question
  and criteria, never as a silent slide.

## Diagram
Read it top to bottom: the question and exit rule come before any work, and every path out of the box
ends in a recorded decision, never in a silent extension.

```mermaid
flowchart TD
    Q["Name one question"] --> B["Budget from value of answer"]
    B --> E["Write exit rule on day 0"]
    E --> S["Build eval set first"]
    S --> R["Riskiest must-have first"]
    R --> M{"Midpoint: observed vs required rate"}
    M -->|"on track"| C["Drop could items, continue"]
    M -->|"flat"| P["Cut scope or change approach"]
    C --> X["Box closes: apply exit rule"]
    P --> X
    X --> D[("Decision record")]
    D -->|"go, pivot, stop"| O["Next step"]
    D -.->|"extend"| N["New box, new question"]
```

## Examples
### Midpoint checkpoint calculator
Turns the checkpoint into arithmetic, with a rough noise floor (one standard error of a proportion over
the number of documents, because fields within a document fail together).

```python
import math

def checkpoint(target, scores, day, days_total, n_docs, window=2):
    """scores: accuracy in points (0-100) for the last few working days, oldest first."""
    current = scores[-1]
    required = (target - current) / (days_total - day)
    observed = (scores[-1] - scores[-1 - window]) / window
    p = current / 100
    noise = 100 * math.sqrt(p * (1 - p) / n_docs)    # one standard error, in points
    if observed >= 2 * required:
        verdict = "continue; drop 'could' items to protect the time"
    elif observed >= required:
        verdict = "continue, scoped to the top error categories"
    else:
        verdict = "change approach or prepare a stop recommendation"
    return round(required, 2), round(observed, 2), round(noise, 1), verdict

print(checkpoint(90, [71, 77, 83], day=5, days_total=10, n_docs=100))
# (1.4, 6.0, 3.8, "continue; drop 'could' items to protect the time")
print(checkpoint(90, [86, 86.5, 87], day=7, days_total=10, n_docs=100))
# (1.0, 0.5, 3.4, 'change approach or prepare a stop recommendation')
```

The second call shows the trap: a 1-point gain sits well inside a ~3.4-point noise floor, so it is not
evidence of progress.

### Worked scenario: a two-week extraction box
A forward-deployed team gets two weeks (10 working days) to test whether an LLM can extract 12 fields from
a logistics customer's bills of lading.

- **Question:** can we reach ≥ 90% field-level accuracy on the customer's real documents?
- **Exit rule (written day 0):** ≥ 90% → pilot; 80–90% → one focused extra week; < 80% → recommend a
  review-queue design where the model drafts and a clerk confirms.

| Day | Work | Result |
|---|---|---|
| 1–2 | Obtain 150 real documents; customer's clerks label 100 of them (1,200 field values). The must-have: without it every later number is opinion. | eval set |
| 3 | Baseline: strong general model, simple prompt | 71% (852 of 1,200 correct, 348 wrong) |
| 4–5 | Error analysis: about 40% of the 348 errors (roughly 140, out of only 300 values in those fields) come from three fields: consignee address on multi-line stamps, weight unit, date format. Explicit format instructions and examples. | 83% (about 996 correct) |
| 5 (midpoint) | Required rate (90 − 83) / 5 = 1.4 points per day; the last two days delivered about 6 points per day. Even allowing for flattening, continue. Drop the "could" item (a review UI). | continue |
| 6–9 | OCR preprocessing for stamped regions; further prompt variants add under a point | 88% |
| 10 | 88% (about 1,056 correct, 144 wrong) lands in the 80–90% band. Roughly 60% of remaining errors sit in the ~20 documents with handwritten weights and annotations. | one more week, scoped only to handwritten documents, with its own pass/fail threshold |

The numbers are illustrative, but the shape is typical: rapid early gains, then a plateau that only a
pre-written rule stops the team from chasing indefinitely.

## Production patterns
- **Decision record per box** — when every box closes. How: a one-page file in the repo or project wiki
  with question, exit rule, evidence table, decision and dead ends, committed on day 0 with the rule filled
  in. Monitor: share of boxes that end with a written decision, and how many end in "stop".
- **Frozen, versioned eval set** — before any tuning. How: label the set, store it read-only with a content
  hash in the file name, and have the eval script refuse to run if the hash changes. Monitor: score on the
  frozen set plus a small held-out sample nobody tunes against.
- **Daily metric plot** — throughout the box. How: run the eval script once a day (a scheduled CI job or a
  notebook) and append date, commit and score to a shared sheet. Monitor: slope over the last few days
  versus the required rate.
- **Calendar-fixed midpoint review** — for any box of a week or more. How: book the midpoint and closing
  meetings with the decision-maker on day 0, and present the required-versus-observed arithmetic. Monitor:
  whether scope was cut at the midpoint when the curve was flat.
- **Scope ladder in the tracker** — when a box has more than a few tasks. How: label each ticket must,
  should or could, and work riskiest-must first. Monitor: how often a must-have gets cut, since repeated
  cuts mean the question was too large.

## Trade-offs
- **Fixed time vs a working solution.** It gives predictable schedules and forces decisions, but you may
  stop just short of a working solution; this flips when the remaining gap is a known, well-scoped
  engineering problem.
- **Short boxes vs trustworthy evidence.** They reduce sunk cost and expose dead ends early, but too-short
  boxes cannot produce a trustworthy eval set, so the decision rests on anecdotes instead of measurements.
- **Pre-written exit criteria vs understanding the data.** They resist motivated reasoning, but can be wrong
  if written before you understood the data; allow one explicit revision at the first checkpoint, with the
  reason recorded.
- **Cutting scope vs answering the question.** It keeps the date, but repeatedly cutting the same must-have
  item means the question was too large and should have been decomposed into smaller boxes.

## Pitfalls
- **Silently extending the box.** "Just two more days" until the PoC has run for months; the symptom is a
  status update that says "almost there" three times in a row. Fix: extensions only as a new box.
- **Timeboxing an activity instead of a question.** The box ends with work done but no decision; the
  symptom is a closing meeting where nobody can say whether to proceed. Fix: rewrite the box as one
  question with a yes/no or numeric answer before day 1.
- **Tuning prompts before building the evaluation set.** Progress is judged by eyeballing outputs; the
  symptom is accuracy claims that collapse when someone runs a held-out sample. Fix: label the eval set
  first and make it the must-have of days 1-2.
- **Sizing the box by feel.** Setting length by how long the work feels like it should take rather than by
  the value of the answer shows up as two-week investigations into decisions worth a day. Fix: run the
  value-of-information test and shrink or skip boxes whose answer would not change the decision.
- **Treating "stop" as a team failure.** Nobody ever recommends stopping; the symptom is an organisation
  whose boxes all end in "promising, continue" and never in a stop. Fix: name "stop" as a legitimate
  outcome in the exit rule and credit the team for a well-evidenced stop.

## When to use
- **Use when:** the outcome is uncertain and the work could expand without limit: feasibility checks, PoCs,
  model or vendor bake-offs, debugging an unexplained quality drop, and any research spike inside a
  delivery project.
- **Avoid when:** the engineering work is well understood; ordinary estimation is fine and the scope is
  fixed by requirements.
- **Measure:** progress by the metric the question names, plotted over the box, not by hours spent.
- **Interviews and customer conversations:** a timebox is how you say "we will know by Friday" credibly:
  name the question, the evidence and the decision rule up front.

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

## Interview questions
### What is a timebox, and why does AI work need it more than ordinary software?
- **Definition:** fixed time, flexible scope, and an explicit decision (ship, continue, pivot, stop) when
  the time runs out.
- **Why AI needs it:** quality improves asymptotically, so there is always one more prompt or model to try.
  Without a box, a PoC stays "almost there" for months because the next tweak always looks promising.

### How would you decide whether a question deserves a one-day or a two-week box?
- **Value of information:** box only if its cost is below the chance the answer changes the decision times
  the cost of deciding wrongly.
- **Concrete:** two weeks for three engineers is about 30 person-days, cheap against six months of a larger
  team going the wrong way, but wasted if the team would choose the same either way.

### At the midpoint your metric moved from 86% to 87% over two days on a 100-document set. What do you conclude?
- **Probably nothing.** One standard error at that accuracy on 100 documents is roughly 3-4 points, so a
  1-point move can be a handful of borderline cases flipping.
- **Then compare rates:** if the required rate to hit target exceeds the observed rate, cut scope, change
  approach, or prepare a stop recommendation rather than grinding.

### When would you not timebox, and when would you break your own exit rule?
- **Do not box** well-understood engineering work with requirements-fixed scope; estimation works there.
- **Revise the rule once,** at the first checkpoint, if it was written before you understood the data, and
  record why. Revising it at the end to match what was achieved is the failure the rule exists to prevent.

### A stakeholder asks for "a few more days" at the end of a box. How do you respond?
- **Open a new box, do not slide.** Name a narrower question (for example only handwritten documents), its
  own pass/fail threshold and a fixed length.
- **Ground it in error analysis:** extend only if remaining errors cluster in a category with a concrete
  fix; diffuse errors on a flat curve argue for a design that tolerates the current accuracy.

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
