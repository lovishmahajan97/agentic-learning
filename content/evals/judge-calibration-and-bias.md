---
id: judge-calibration-and-bias
module: evals
name: Judge calibration & bias
kind: practice
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - evals/llm-as-a-judge
  - evals/rubrics
  - evals/pairwise-comparison
  - evals/inter-annotator-agreement
  - evals/golden-dataset
  - ml-fundamentals/calibration
sources:
  - Zheng et al., 2023 — Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena
  - Wang et al., 2023 — Large Language Models are not Fair Evaluators
  - Dubois et al., 2024 — Length-Controlled AlpacaEval
  - Panickssery et al., 2024 — LLM Evaluators Recognize and Favor Their Own Generations
---

## TL;DR
An LLM judge is a measuring instrument with its own systematic errors: it tends to prefer the answer
shown first, the longer answer, the answer written in its own style, and it bunches scores near the top
of the scale. Calibration means measuring those errors against human labels *before* trusting the judge;
bias mitigation is the set of protocol changes (position swapping, length control, rubrics, reference
answers, judges from a different model family) that shrink them. Skip this and a judge score measures the
judge, not your system.

## Why it exists
A judge model is attractive because it is cheap and scales to thousands of outputs. But every instrument
has two kinds of error. *Variance* is noise: run the judge twice, get slightly different numbers. Variance
is annoying but you can average it away by repeating. *Bias* is a systematic tilt in one direction, and no
amount of repetition removes it. If the judge likes long answers and your new prompt makes answers 30%
longer, the judge reports an improvement that users may never experience, and every repeat of the
experiment confirms the same wrong conclusion.

The biases are not hypothetical. Zheng et al. documented, for GPT-4-class judges on MT-Bench, three
recurring ones: **position bias** (the verdict changes when the two candidates are swapped), **verbosity
bias** (longer answers win even when the extra text adds nothing), and **self-enhancement bias** (a judge
rates outputs from itself or its own family higher). Wang et al. showed that position bias alone can
reorder a leaderboard. Later work showed judges can recognise their own generations and favour them.
Practitioners add a few more: **style bias** (markdown headings and confident tone score higher than
plain correct prose), **scale compression** (on a 1–10 scale almost everything lands at 7–9, so real
differences vanish into rounding), **knowledge-gap leniency** (a fluent but wrong proof passes when the
judge cannot do the math either), and **anchoring** on any hint in the prompt, such as a reference answer
or a label like "improved version".

The naive fix, "use the strongest model available and trust it", fails on two counts. Stronger judges
still show these biases, just less; and without a comparison to human labels you cannot tell how much.
Biases are invisible in an aggregate score, which is why they have to be probed deliberately with
controlled inputs rather than discovered by accident after a bad release.

## How it works
### Step 1: build a calibration set
Take a sample of real inputs, on the order of 100–300 items (illustrative), and have humans label them
with the exact rubric the judge will use. Seed it with the cases that expose bias: correct-but-terse
answers, wrong-but-eloquent ones, pairs that differ only in formatting, and genuine ties. Measure
inter-annotator agreement first, because it is the yardstick: a judge cannot be shown to be more
accurate than the labels it is compared against, and its agreement number only means something next to
human–human agreement computed the same way.

### Step 2: measure agreement, not just scores
Run the judge on the calibration set and compute agreement with the human majority: accuracy for
pairwise verdicts, Cohen's kappa for pass/fail, Spearman correlation for ordinal scores. Compare that
number with human–human agreement on the same items, computed like for like: judge versus each
annotator next to annotator versus annotator, or judge versus the majority next to each annotator versus
the majority of the others. Agreement with a majority label is naturally higher than agreement with one
noisy person, so mixing the two makes a judge look better than it is. A judge within a few points of
human–human agreement is "another annotator"; one far below is not usable as a gate. Then look at *where* it
disagrees. Disagreements concentrated on one failure mode (say, numeric claims) tell you the judge
cannot evaluate that criterion and a human or a programmatic check must.

### Step 3: probe each bias with controlled perturbations
- **Position.** Judge every pair in both orders. The inconsistency rate (verdict flips when swapped) is
  the direct measure; among the flipped pairs, count how often the first slot won.
- **Length.** On pairs where humans preferred the shorter answer, how often does the judge pick the
  longer one? Or regress judge preference on length difference while holding the human label fixed.
- **Self-preference.** Have the same candidate outputs judged by models from two or three families and
  compare the rankings they produce.
- **Style.** Reformat identical content (bullets vs prose, with and without headings) and check whether
  the score moves.
- **Scale use.** Histogram the scores. If 80% of mass sits on two adjacent values, the scale is compressed
  and deltas between variants are mostly noise.

### Step 4: mitigate what you found
- Position: run both orders and average, or count only consistent verdicts and treat flips as ties.
  This doubles judge cost, which is why you measure first; if flips are rare for your task, skip it.
- Length: report a length-controlled win rate (fit a model of preference with the length difference as
  a covariate and read off the prediction at zero difference, the idea behind length-controlled
  AlpacaEval), instruct the judge explicitly that length is not a merit, and include few-shot examples
  where the shorter answer wins.
- Scale compression: replace 1–10 with rubric-anchored levels or binary per-criterion checks, or switch
  to pairwise comparison, which needs no absolute scale at all.
- Knowledge gaps: reference-guided grading. Give the judge a gold answer or test results so it checks
  consistency instead of solving the problem itself.
- Self-preference: use a judge from a different family than the system under test, or a small panel of
  judges from several families with a majority vote.
- Reasoning: ask for a short analysis before the verdict and a structured final field. This helps on
  reasoning-heavy comparisons, with the size of the gain model-dependent, so verify it on your set.
- Drift: pin the judge model version and treat the judge prompt as versioned code. Re-run calibration
  whenever either changes; vendor model updates silently move the instrument.

### Step 5: report with uncertainty
Bootstrap a confidence interval over items for the headline score and always publish the judge's
agreement with humans next to it. A win rate without an agreement number is an unlabeled gauge.

## Example
The harness below runs each pair in both orders, measures position inconsistency, agreement with human
labels on the consistent pairs, and the judge's preference for the longer answer relative to the humans'.
The `judge` function wraps whatever LLM call you use and returns `"A"` or `"B"`.

```python
import statistics as st

def judge(question, first, second) -> str:
    """Show `first` as A and `second` as B; return 'A' or 'B'. Wrap your LLM call here."""
    raise NotImplementedError

def calibrate(pairs, human):
    """pairs: [(q, x, y)]; human: [1 or 2] = which of x, y the human majority preferred."""
    flips = first_slot_wins = agree = 0
    judge_longer, human_longer = [], []
    for (q, x, y), h in zip(pairs, human):
        v1 = judge(q, x, y)                       # x in slot A
        v2 = judge(q, y, x)                       # y in slot A
        pick1 = 1 if v1 == "A" else 2             # map both verdicts back to x=1, y=2
        pick2 = 2 if v2 == "A" else 1
        if pick1 != pick2:                        # verdict depended on order
            flips += 1
            first_slot_wins += (v1 == "A") + (v2 == "A")
            continue                              # treat as a tie; exclude from accuracy
        agree += pick1 == h
        longer = 1 if len(x) > len(y) else 2
        judge_longer.append(pick1 == longer); human_longer.append(h == longer)
    n, consistent = len(pairs), len(pairs) - flips
    print(f"position-inconsistent pairs: {flips}/{n} ({flips/n:.0%}); "
          f"first slot won {first_slot_wins}/{2*flips or 1} of those verdicts")
    print(f"agreement with humans (consistent pairs): {agree}/{consistent} ({agree/max(consistent,1):.0%})")
    print(f"chose longer answer: judge {st.mean(judge_longer):.0%} vs humans {st.mean(human_longer):.0%}")
```

Illustrative output on 200 pairs: 46 inconsistent (23%), with the first slot winning 72 of the 92
verdicts in those pairs (a flipped pair contributes either two first-slot wins or none, so this count is
always even), which is a clear first-position bias. On the 154 consistent pairs the judge agrees with the
human majority on 128 (83%), while individual annotators agreed with the majority of the others on 86%,
so it is close to annotator quality. But it picks the longer answer 68% of the time against the humans' 55%: a verbosity tilt of
roughly 13 points that will reward any prompt change that makes outputs wordier.

## Trade-offs
- Position swapping removes the largest pairwise bias but doubles judge cost; skip it only after you have
  measured a low inconsistency rate on your own calibration set, not because a paper reported one.
- Reference-guided grading sharply improves accuracy on tasks with a checkable answer, but penalises valid
  alternatives; it flips to a liability on open-ended tasks with many acceptable answers.
- A stronger judge agrees more with humans but costs more and is more likely to share a family with the
  system under test; once the candidate is the judge's sibling, a weaker judge from another family wins.
- Binary rubric checks cure scale compression but lose gradation; when the differences you care about sit
  inside a single criterion, a short anchored scale (3 levels) is the better compromise.
- Human calibration labels are expensive, but they are reusable across judge versions and are the only
  way to detect drift; a few hundred pairwise labels usually suffice for an accuracy estimate.

## Pitfalls
- Publishing a judge win rate without the judge's agreement with humans; the symptom is a prompt change
  that "improves" the score while user complaints rise.
- Generating and judging with the same model family; the symptom is the candidate beating stronger
  external models suspiciously often, and the ranking changing when the judge family changes.
- A compressed score distribution; the symptom is every variant scoring 8.2–8.6 with differences inside
  the bootstrap interval, so the eval cannot distinguish anything.
- Letting the vendor upgrade the judge model silently; the symptom is a step change in a historical trend
  chart on the day nothing in your system changed.
- Leaking metadata into the judge prompt (model names, "v2", "improved"); the symptom is verdicts that flip
  when the labels are swapped while the content stays the same.
- Judging in one fixed order; the symptom is a leaderboard that reorders when candidate A and B are
  exchanged.

## When to use
Calibrate whenever a judge gates a decision: prompt releases, model swaps, fine-tune selection, vendor
comparisons. Do it once per combination of judge model, judge prompt and task, and again whenever any of
the three changes. Track three numbers: position inconsistency rate, agreement with humans next to
human–human agreement, and the judge's length preference relative to humans. For tasks with an
executable check (tests, exact match, schema validation) do not use a judge at all. In day-to-day work
this is the calibration job in CI that fails when agreement drops; in interviews the question "how do
you know your judge is right?" expects exactly this: a labeled set, swap-and-average, length control, and
kappa against the human ceiling.

## Role lens
- **Engineer:** Implements both-order judging, structured verdict output, a pinned judge version, and a
  calibration job that reports agreement and fails the build when it degrades.
- **Architect:** Decides which judge families may evaluate which systems, the minimum agreement a judge
  needs before it can gate a release, and the human relabeling budget per quarter.
- **FDE:** When a customer's SMEs disagree with the judge, runs the calibration on their data and uses
  the agreement number to negotiate what "good" means rather than arguing case by case.

## Check yourself
**Q:** Your team replaced a 1–10 judge with pairwise comparison against the production prompt, and the new
prompt wins 64% of comparisons. What do you check before shipping, and why?
**A:** First, position consistency: run every pair in both orders; if a large share of verdicts flip, part
of the 64% is slot order, so recompute on consistent pairs or average. Second, length: if the new prompt
produces longer outputs, compute a length-controlled win rate or restrict to length-matched pairs, because
verbosity bias alone can manufacture a double-digit swing in win rate. Third, agreement with humans on a labeled
subset of about a hundred pairs, compared with human–human agreement, to confirm the judge is tracking
quality on this task. Fourth, self-preference: if the change involved a model swap, make sure the judge is
from a different family. Finally, bootstrap a confidence interval; if it still excludes 50% after these
controls, ship behind an online A/B to confirm.

## Quiz
### Q: A judge's verdict changes for 30% of pairs when the two answers are swapped. What is being measured, and what is the fix?
- [ ] Verbosity bias; fix it by truncating both answers to the same length
- [x] Position bias; run both orders and average, or treat inconsistent pairs as ties
- [ ] Self-preference bias; switch to a judge from a different model family
- [ ] Scale compression; switch from scores to pairwise comparison
> A verdict that depends on which slot an answer occupies is position bias by definition. Both-order
> judging cancels it; truncation and judge-family changes target different biases.

### Q: A report says the judge agrees with the human majority label 92% of the time, while two human annotators agree with each other only 78%. What should you conclude first?
- [ ] The judge is genuinely better than the human annotators
- [ ] Position bias inflated the judge's accuracy
- [x] The two numbers are not like for like: agreement with a majority label is naturally higher than agreement between two individuals; recompute both the same way, with intervals, before concluding anything
- [ ] Kappa was computed with the wrong marginals
> A majority label is less noisy than any single annotator, so judge-versus-majority can exceed
> annotator-versus-annotator even for a merely human-level judge. Labels written by these humans also
> cannot demonstrate that the judge is better than them; position bias and kappa marginals do not explain the gap.

### Q: Which mitigation specifically targets self-enhancement (self-preference) bias?
- [ ] Judging each pair in both orders
- [ ] Giving the judge a reference answer
- [x] Using a judge from a different model family than the system being evaluated
- [ ] Lowering the judge's sampling temperature
> Self-preference is a judge favouring outputs that resemble its own; changing the judge family removes
> that resemblance. Order swapping fixes position bias, references fix knowledge gaps, and temperature
> only affects variance.
