---
id: eval-review-loops
module: customer-data-evaluation
name: Eval review loops
kind: pattern
difficulty: core
est_minutes: 17
status: reviewed
related:
  - evals/error-analysis
  - evals/eval-driven-development
  - evals/judge-calibration-and-bias
  - evals/llm-as-a-judge
  - customer-data-evaluation/sme-review-and-annotation-guidelines
  - customer-data-evaluation/labeling-sessions
  - customer-data-evaluation/customer-specific-regression-suites
  - customer-data-evaluation/acceptance-testing-against-customer-kpis
---

## TL;DR
An eval review loop is the recurring cycle in which a team runs the customer eval, reads the failures
with SMEs, classifies each as a system error, a label error or a guideline gap, fixes the right thing,
and re-runs with regression checks. It exists because an eval score alone says how much is wrong but
not why, and because labels, guidelines and judges are themselves wrong in ways only expert review
reveals. Without a held-out set, the loop overfits to the cases it keeps looking at.

## Why it exists
After the golden set and a first prompt exist, the tempting workflow is "change the prompt, re-run, watch
the number". That treats the eval as an oracle. It is not. In a customer deployment, a failing case can
mean three different things: the system is wrong; the reference label is wrong; or the guideline never
defined the case, so neither is clearly right. Early in a project the second and third are common,
because the dataset was labelled before anyone saw how the system fails. If you only tune the system,
you will "fix" correct behaviour to match wrong labels, and the customer's experts will reject the
result in production.

There is a second reason. Most customer evals at scale use an automated grader, often an LLM judge,
because SMEs cannot review every output of every run. The judge drifts from the experts in ways that
are invisible from the score: lenient on confident wrong answers, harsh on terse correct ones. Only
periodically comparing judge verdicts against SME verdicts on the same items keeps the automated number
honest. And third, a score without review builds no shared understanding with the customer. The loop is
where the customer's experts see failures, agree on what matters, and come to trust the number used at
acceptance.

## How it works
### One iteration
1. **Run** the current system version on the iteration set (never the holdout) and grade with the
   automated grader. Record system version, prompt version, dataset version and grader version together,
   *because* a score is only comparable when all four are pinned.
2. **Select for review.** All failures in high-stakes strata, a random sample of other failures, and a
   random sample of *passes*. Passes are included *because* grader leniency only shows up there.
3. **Read and code failures.** Engineers and SMEs read each selected item and assign a cause from a
   small, evolving taxonomy (retrieval missed the clause, misread table, wrong policy precedence, format
   violation, and so on). Open coding first, then cluster; a fixed taxonomy invented upfront misses the
   failure modes you did not anticipate.
4. **Attribute each item.** System error, label error, guideline gap, or grader error. This is the step
   that distinguishes a review loop from error analysis alone.
5. **Route the fix.** System errors go to the prompt, retrieval or code backlog, ordered by frequency
   times cost. Label errors go to relabelling with SME sign-off. Guideline gaps go to the policy owner,
   then the guideline, labels and judge prompt are updated together. Grader errors go to judge
   recalibration.
6. **Re-run with regression checks.** Compare per-stratum results against the previous version, and
   inspect every item that flipped from pass to fail, *because* averages hide regressions offset by
   gains elsewhere.
7. **Report.** A short note to the customer: score with interval, top failure clusters, decisions taken,
   what changed in labels or guideline (so score movement is explainable).

### How much to read, and in what order
Read failures until new cause categories stop appearing; in practice the first few dozen failures
usually reveal the main clusters, and reading hundreds more mostly adds counts, not categories. Then
rank clusters by frequency × cost of the error in this customer's process, *because* a rare error that
sends money to the wrong bank account outranks a common formatting slip.

### Reading the score through the review
The review sample also corrects the headline. If the judge passes a share p_judge of items, SMEs find
that a fraction f_pass of judge-passes are actually wrong, and a fraction r_fail of judge-fails are
actually right (label or grader errors), then the SME-equivalent pass rate is approximately

```
p_true ≈ p_judge × (1 − f_pass) + (1 − p_judge) × r_fail
```

Both fractions come from small samples, so the corrected number carries a wide band; its value is in
showing which direction the grader errs and by how much.

### Keeping the grader honest
Each cycle, have SMEs grade a fixed-size sample that the judge also graded, and track judge-to-SME
agreement over time. A drop means the judge prompt or the guideline it encodes has fallen behind. The
sample must be random rather than drawn from failures, or it measures the judge only where it is
already suspected.

### Avoiding overfitting
The loop repeatedly exposes the iteration set. Keep a holdout that nobody reads, and evaluate on it only
at milestones. A large gap between iteration and holdout scores means the team has tuned to specific
cases rather than the behaviour.

### Cadence
Weekly is a common rhythm during a pilot: frequent enough to keep momentum, slow enough for SMEs. Rerun
the automated eval on every change; reserve the human review for the cadence.

## Example
A three-week loop on a support-answer assistant for a software customer; numbers illustrative. The
iteration set has 300 items, the holdout 100. An LLM judge grades correctness against references.

**Week 1.** Judge pass rate 78%. The team reviews all 66 failures plus 30 random passes with two SMEs.
Of the 66 failures: 31 are system errors (19 retrieval returned an outdated release note, 12 wrong
product-tier logic), 14 are label errors (references written before a pricing change), 9 are guideline
gaps (is a workaround an acceptable answer when a fix exists?) and 12 are judge errors (correct answers
marked wrong because they were shorter than the reference). Among the 30 passes, 4 are actually wrong,
judged lenient because they sounded confident.

Reading the score through the review: f_pass ≈ 4/30 ≈ 0.13, and r_fail = 26/66 ≈ 0.39 (the label and
judge errors, where the system was right). So p_true ≈ 0.78 × 0.87 + 0.22 × 0.39 ≈ 0.76, before the 9
guideline gaps are ruled on. The headline was roughly right by accident: about 30 false passes and 26
false fails nearly cancel. A grader whose errors cancel on this mix will not cancel on the next one, so
the judge is fixed anyway.

Decisions: filter retrieval by release version; relabel 14 items; product owner rules "a workaround is
acceptable only if the answer also states the fix version"; judge prompt updated to grade content not
length and to check stated version numbers.

**Week 2.** After relabelling and the judge update, the *same* week-1 system scores 81%; the
improvement is measurement, not the model, and is reported as such. It is smaller than the 26 recovered
label and length errors alone would suggest (that would be about 87%) because the stricter version check
also turns some previously lenient passes into failures; the week-1 pass sample implied there were
dozens of them. With the retrieval fix, 87%. Two
items flip pass-to-fail; both involve multi-version questions now filtered too aggressively, so the
filter is relaxed for those queries.

**Week 3.** 89% on the iteration set and 86% on the holdout, a small gap and well inside the roughly
±7-point 95% interval a 100-item holdout carries. Judge-to-SME agreement on a fresh random 40-item
sample is 36 of 40, up from 31 of 40 on the equivalent random sample in week 1. Forty items is a small
sample, so the team treats this as consistent with the fixes rather than proof, and keeps sampling
weekly. The customer now trusts the number enough to use it in the acceptance test.

## Trade-offs
- Deep SME review of every failure gives accurate attribution, but consumes expert time; once judge and
  SME agree well, review a sample of failures and all high-stakes ones.
- Fixing labels and guidelines makes the score more truthful, but moves the baseline, so trends are
  harder to read; always re-score the previous system version on the new labels before comparing.
- A weekly cadence keeps SMEs engaged, but can feel slow to engineers; run automated evals continuously
  and keep human review weekly.
- An evolving failure taxonomy captures new failure modes, but makes cluster counts incomparable over
  time; freeze it at a milestone once new categories stop appearing.

## Pitfalls
- Treating every failure as a system error; the symptom is the team rewriting prompts to match a
  reference that SMEs later admit was wrong.
- Reviewing only failures; the symptom is a judge that is lenient on confident wrong answers and a
  production complaint rate higher than the eval suggests.
- Comparing scores across label or judge changes without re-scoring the old system; the symptom is a
  celebrated improvement that came from relabelling, not engineering.
- Repeatedly tuning on the same visible items; the symptom is a widening gap between iteration and
  holdout scores.
- Silently editing labels; the symptom is the customer asking why last week's 82% is now 79% with no
  system change and nobody being able to explain it.

## When to use
Run an eval review loop from the first time you have a system, a golden set and an automated grader,
and keep it running through the pilot and into production with real traffic samples. It is the main
mechanism for converting SME expertise into system improvements and a trusted acceptance metric. Skip
the heavy human part only when grader-to-SME agreement is high and stable and the task is not
high-stakes. Measure the attribution mix over time; label and guideline errors should shrink as the
dataset matures. In interviews, the distinguishing answer separates system, label, guideline and grader
errors.

## Role lens
- **Engineer:** Pins system, prompt, dataset and grader versions per run, builds the flip report and
  review queue, and turns failure clusters into prioritised fixes.
- **Architect:** Designs the eval platform so versions, holdouts and judge-to-SME agreement are tracked
  per customer, making scores comparable and auditable across releases.
- **FDE:** Runs the weekly review with the customer's SMEs, routes guideline gaps to the policy owner,
  and explains every score movement in the customer report.

## Check yourself
**Q:** Your judge-scored pass rate jumped from 74% to 85% in one week. The engineers changed the prompt;
the SMEs also relabelled 30 items and the judge prompt was edited. How much of the gain is the prompt?
**A:** I cannot tell from the two numbers because three things changed at once. I would re-run the
previous system version with the new labels and the new judge; the difference between that and 74% is
the measurement change. The difference between that re-score and 85% is the prompt effect. I would also
check flips in both directions, verify the judge edit with an SME agreement sample, and confirm the
holdout moved in the same direction. Only the prompt-attributable part should be reported as system
improvement to the customer.

## Quiz
### Q: Why should an eval review include a random sample of passing items?
- [ ] Passing items are where the system has the most errors
- [x] Grader leniency only shows up in items it marked as passes
- [ ] SMEs need easy items to stay motivated
- [ ] Including passes raises the measured pass rate
> If the judge accepts confident wrong answers, those errors never appear in the failure list. Reviewing
> passes estimates false passes and reveals where the grader needs recalibration.

### Q: A failure is traced to a reference answer written before a policy change. What is the right action?
- [ ] Change the prompt so the system reproduces the old reference
- [ ] Delete the item from the dataset
- [x] Relabel it with SME sign-off, version the dataset, and re-score the previous system
- [ ] Leave it, since the score only needs to be directionally right
> This is a label error, not a system error. Fixing it with sign-off and re-scoring the old version
> keeps comparisons honest and stops the team tuning the system toward outdated behaviour.

### Q: What does a growing gap between the iteration-set score and the holdout score indicate?
- [ ] The holdout set is mislabelled
- [ ] The automated judge is becoming more accurate
- [ ] Production traffic has drifted
- [x] The team is overfitting fixes to the specific cases it repeatedly reviews
> The iteration set is read every cycle, so fixes can become case-specific. The untouched holdout
> estimates generalisation; a widening gap means improvements are not transferring.
