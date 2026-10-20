---
id: evaluation-gates-for-production-releases
module: llmops-platform
name: Evaluation gates for production releases
kind: practice
difficulty: core
est_minutes: 16
status: reviewed
related:
  - llmops-platform/ci-cd-for-prompts-and-models
  - llmops-platform/canary-shadow-deployments
  - llmops-platform/rollback
  - llmops-platform/reproducible-evaluations
  - llmops-platform/golden-dataset-strategy
  - evals/regression-tests
  - evals/task-specific-acceptance-criteria
  - evals/offline-vs-online-evaluation
sources:
  - Efron & Tibshirani, 1993 — An Introduction to the Bootstrap
  - Kohavi, Tang & Xu, 2020 — Trustworthy Online Controlled Experiments
  - Humble & Farley, 2010 — Continuous Delivery
  - Breck et al., 2017 — The ML Test Score: A Rubric for ML Production Readiness and Technical Debt Reduction
---

## TL;DR
An evaluation gate is an automated policy that decides, from eval results rather than a green unit
suite, whether a candidate bundle (code, prompt, model, parameters, index) may move to the next stage. It
combines absolute floors (safety, schema validity, regression items), a paired non-inferiority test
against the champion with a confidence interval, per-slice floors and cost/latency budgets, and returns
pass, block or needs-review with every override recorded. It exists because sampled outputs make
exact-match tests flake and one aggregate hides the slice that broke.

## Why it exists
Without a gate, the release test for an LLM change is either "someone looked at a few outputs" or "the
aggregate went up". Both fail in ways that reach users. The aggregate rises one point while the refunds
slice collapses, because the slice is 3% of the set. An exact-string regression test flakes on a
rephrased but correct answer, so it is marked flaky and ignored. A "small prompt tweak" ships without any
eval because prompts are config, and a provider model upgrade moves every behaviour at once with no
test in the path at all.

The naive gate designs fail too, and knowing why shapes the real one. "Block if the score is below the
last score" blocks on noise: at a few hundred items a one-point drop is well inside run-to-run variance,
so engineers learn to re-run until green, which destroys the gate's meaning in a month. "A human reviews
every release" does not scale and degrades to rubber-stamping within a quarter. "Only test online with an
A/B" is slow, exposes users to the regression before it is detected, and cannot enforce a safety floor,
since the point of a floor is that no user ever sees the failure. A gate that works makes the decision
rule explicit, statistical and auditable: it says which metrics must not fall, by how much, with what
confidence, on which items, and who may override it.

## How it works
### Four kinds of criteria
1. **Absolute floors.** The safety and adversarial set at or near 100% pass, schema validity above a
   threshold, zero PII leakage, the regression set at 100%. These are not relative to the champion
   *because* "no worse than before" is not acceptable for a safety property: a flawed champion must not
   license a flawed candidate. With sampled outputs a floor is checked over repeats: run each safety item
   *k* times and require every repeat to pass, *because* an answer that is unsafe one time in ten is unsafe
   for the user who draws it, and a single run would let it through nine times out of ten.
2. **Non-inferiority against the champion.** The candidate's primary metric must not be worse than the
   champion's by more than a margin δ, with confidence, on the same dataset version and scorer version,
   paired by item. δ is set per metric from product risk (one or two points on a pass rate is a common
   illustrative choice), and it can never be tighter than the measured noise floor.
3. **Per-slice floors.** The same test per slice with a wider margin (smaller n), or an absolute minimum
   per slice, *because* the aggregate is a traffic-weighted average that hides rare, high-value cases.
4. **Operational budgets.** p95 latency, cost per request and output tokens measured in the same run,
   *because* a prompt that scores two points higher at three times the tokens is a cost regression that
   the quality metric cannot see.

### The statistics of the relative gate
For each item *i*, compute the paired difference *dᵢ* = candidate score − champion score. Pairing matters
*because* item difficulty is the largest source of variance and it cancels in *dᵢ*; a paired test on 400
items is far tighter than comparing two independently measured averages. Estimate the confidence interval
of the mean difference with a bootstrap: resample the *dᵢ* with replacement a few thousand times,
compute the mean each time, and take the 2.5th and 97.5th percentiles. The bootstrap is used *because*
scores are often bounded, bimodal or judge-graded on a few discrete levels, where a normal approximation
is shaky and the bootstrap needs no distributional assumption.

Reading only the lower end of a two-sided 95% interval makes this a one-sided test at 97.5% confidence,
the conventional non-inferiority setup; the question asked is "could it be worse by more than δ", not
"is it different".

The decision has three outcomes, not two. If the interval's lower bound is above −δ the candidate is
non-inferior: **pass** (and if the lower bound is above zero it is an improvement you may claim). If the
upper bound is below −δ the candidate is confidently worse: **block**. If the interval spans −δ the
evidence is inconclusive: **needs review**, which routes to a larger run or a human, *because* treating
inconclusive as pass lets regressions through and treating it as fail teaches people to re-run until
lucky. With many slices, false alarms multiply, so slice verdicts default to review rather than block,
or margins are widened.

A worked instance (illustrative numbers): 400 items, champion pass rate 0.82, candidate 0.84, mean paired
difference +2.0 points, bootstrap 95% interval [−0.5, +4.5]. The interval's width depends on how many
items changed verdict between the two bundles: for pass/fail scores the per-item differences are −1, 0
or +1, so their variance is roughly the discordant fraction, and a half-width of 2.5 points at n = 400
corresponds to about one item in fifteen flipping. With δ = 2, the lower bound −0.5 is above −2, so the gate
passes, but the interval includes zero, so the release note may not claim an improvement. Had the interval
been [−3.5, +0.5], the lower bound would fall below −δ while the upper bound sits above it: inconclusive,
needs review, probably a larger paired run.

### Where the gates sit
Depth follows blast radius, the ladder from the CI/CD lesson. PR: smoke set, hard floors on a small safety
subset block, everything else warns. Merge and nightly: full set, all four criteria, block. Release
candidate: full set plus sealed holdout, repeats to establish the noise floor, and a human sign-off for
high-risk applications. Canary: online metrics against the champion's live traffic with an automatic
rollback trigger, *because* offline evals cannot see distribution shift in real traffic. Full rollout
only after the canary window closes clean.

### Overrides, configuration and gate fatigue
An override is allowed, with a named approver, a reason, an expiry and a record in the registry next to
the promotion, *because* unrecorded overrides are how gates die quietly. Gate policy lives as versioned
configuration in the repository, per application, so threshold changes are themselves reviewed. Track
the override rate and the "gate was right" rate (blocked candidates later confirmed bad); a high override
rate means the margin is tighter than the noise floor, the golden items are stale, or a scorer is flaky,
and the fix is to recalibrate the gate rather than to keep overriding it.

## Example
A gate that applies absolute floors first, then a paired bootstrap non-inferiority test per slice, then
the operational budgets, and returns one of three outcomes. Inputs are per-item scores for candidate and
champion aligned by item id on the same dataset and scorer versions.

```python
import random, statistics

def paired_bootstrap_ci(cand: list[float], champ: list[float], iters: int = 2000, seed: int = 0):
    """cand and champ are aligned by item id, same dataset version, same scorer version."""
    d = [c - h for c, h in zip(cand, champ)]
    rng, n = random.Random(seed), len(d)
    means = sorted(statistics.mean(rng.choice(d) for _ in range(n)) for _ in range(iters))
    return statistics.mean(d), means[int(0.025 * iters)], means[int(0.975 * iters)]

def gate(cand: dict, champ: dict, policy: dict):
    # 1. absolute floors: never relative to the champion
    failed = [k for k, floor in policy["floors"].items() if cand["floors"][k] < floor]
    if failed:
        return "block", {"failed_floors": failed}
    # 2. paired non-inferiority, aggregate and per slice
    verdicts = {}
    for name, margin in policy["margins"].items():            # {"all": 0.02, "refunds": 0.04, ...}
        mean, lo, hi = paired_bootstrap_ci(cand["scores"][name], champ["scores"][name])
        if lo > -margin:
            verdicts[name] = ("pass", mean, lo, hi)             # improvement claimable only if lo > 0
        elif hi < -margin:
            verdicts[name] = ("block", mean, lo, hi)
        else:
            verdicts[name] = ("review", mean, lo, hi)           # inconclusive is neither pass nor fail
    # 3. operational budgets measured in the same run
    for k in ("p95_latency_ms", "cost_per_request"):
        if cand["ops"][k] > policy["budgets"][k]:
            verdicts[k] = ("block", cand["ops"][k])
    states = {v[0] for v in verdicts.values()}
    outcome = "block" if "block" in states else ("review" if "review" in states else "pass")
    return outcome, verdicts

policy = {"floors": {"safety": 1.0, "schema_valid": 0.995, "regression": 1.0},
          "margins": {"all": 0.02, "refunds": 0.04},
          "budgets": {"p95_latency_ms": 2500, "cost_per_request": 0.012}}
```

## Trade-offs
- A statistical non-inferiority gate stops flaking on noise, but needs enough items to be decisive; with a
  small set it can only catch large breaks, so either grow the set or accept a wider margin.
- Absolute safety floors are unambiguous, but one mislabeled item blocks every release until fixed, which
  is acceptable only if the item-review path is fast.
- Three outcomes keep humans for the ambiguous cases, yet if most verdicts land in review the queue
  becomes the bottleneck; that is the signal to raise n or loosen the margin, not to skip review.
- Per-slice gates catch hidden regressions, and every added slice adds a chance of a false alarm; past a
  handful of slices, make slice verdicts review-only or correct for multiple comparisons.
- Gating on cost and latency prevents expensive "improvements", but ties quality releases to
  infrastructure variance; measure both bundles in the same run on the same infrastructure.

## Pitfalls
- Comparing the candidate against a stored champion score instead of re-running the champion on the same
  dataset and scorer version; the symptom is every candidate failing after a judge or dataset update.
- Treating an inconclusive interval as a pass, or as a fail; the symptom is either regressions reaching
  canary or engineers re-running the gate until the dice land right.
- A margin tighter than the noise floor; the symptom is an unchanged bundle failing the gate against
  itself.
- Gating on the aggregate only; the symptom is a slice dropping fifteen points while the headline metric
  rises one.
- Checking a 100% safety floor with one sample per item at production temperature; the symptom is a
  floor that passed in CI and a jailbreak that reproduces in production on the second or third attempt.
- Overrides with no record; the symptom is a post-incident review that cannot find who approved the
  release the gate had blocked.
- Gating only on the smoke set; the symptom is the nightly full run discovering what the PR gate missed,
  after the merge.

## When to use
Install a gate wherever a change to code, prompt, model, parameters or index can reach users, and start
small: hard floors plus one paired non-inferiority test on the primary metric, with slices added as error
analysis reveals them and block-versus-warn configured per stage. Measure the gate: pass, block and
review rates, override rate, time to verdict, the "gate caught it" rate and escaped regressions found by
the canary. Day to day it is the thing that turns "the eval looks fine" into a verdict with a confidence
interval attached; in interviews, expect to explain why the test is paired, what the margin means, why
there are three outcomes, and which criteria must be absolute.

## Role lens
- **Engineer:** Implements the paired bootstrap and the policy file, makes the pipeline re-run the
  champion alongside the candidate, and wires the verdict into the registry promotion step.
- **Architect:** Owns the gate policy per application: which metrics are absolute, the margins and their
  relation to the noise floor, which stages block, and who may override with what record.
- **FDE:** Translates a customer's acceptance criteria into floors and margins on their golden set, so
  go-live is a reproducible verdict rather than a negotiation over a demo.

## Check yourself
**Q:** A candidate prompt scores 85.0% against the champion's 83.5% on 300 items, and the product manager
wants to ship it as an improvement. What does your gate say, and what do you tell the PM?
**A:** First confirm both runs share the dataset and scorer versions and that the champion was re-run,
not read from history. Then compute the paired bootstrap interval on the 300 per-item differences; at
this size a 1.5-point mean difference will very likely have an interval that includes zero, so the gate
cannot certify an improvement. Whether it can even report non-inferior for a two-point margin depends on
how many items flipped: the lower bound is about 1.5 minus the half-width, so it clears −2 only if the
half-width is under 3.5 points, which at n = 300 means roughly fewer than one item in ten changing
verdict; with more churn than that the verdict is needs-review, not pass. Check the absolute
floors, the per-slice verdicts and the cost and latency budgets, since a longer prompt often buys its
points with tokens. Tell the PM the change is safe to ship if the gate passes and everything else does too, that "improvement"
needs either a larger paired run or a canary that shows it in live metrics, and that the release note
should say "no regression" until then.

## Quiz
### Q: Why does the gate compare candidate and champion on identical items with a paired test rather than against last week's champion score?
- [ ] Because last week's run is too expensive to look up
- [x] Because item difficulty cancels in paired differences and the pins (dataset, scorer, model) are guaranteed equal
- [ ] Because the champion's score changes every day due to caching
- [ ] Because a paired test does not require a confidence interval
> Pairing removes the dominant source of variance and forces both bundles through the same dataset and
> scorer versions. A historical score may have been produced by a different judge, dataset or model
> snapshot, so a difference against it has no clear cause.

### Q: The bootstrap interval for candidate minus champion is [−3.5, +0.5] points and the margin is 2 points. What is the verdict?
- [ ] Pass, because the interval includes positive values
- [ ] Block, because the mean difference is negative
- [x] Needs review, because the interval spans the margin and the evidence is inconclusive
- [ ] Pass, because the upper bound is above zero
> The lower bound is below −2 and the upper bound is above it, so the data neither establishes
> non-inferiority nor confirms a regression. The honest outcome routes to a larger paired run or a human,
> not to a coin flip.

### Q: Which criterion should be an absolute floor rather than a relative test against the champion?
- [ ] Average answer length
- [ ] The judge's helpfulness score on the full set
- [x] The pass rate on the prompt-injection and safety set
- [ ] p50 latency
> "No worse than the champion" is the wrong standard for a safety property, since a flawed champion would
> license a flawed candidate. Helpfulness is legitimately relative, and latency belongs to the operational
> budget rather than to a safety floor.
