---
id: a-b-testing
module: evals
name: A/B testing
kind: practice
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - evals/offline-vs-online-evaluation
  - evals/pairwise-comparison
  - llmops-platform/canary-shadow-deployments
  - llmops-platform/rollback
  - llmops-platform/experiment-tracking
  - observability/monitoring
  - scoping/success-metrics-kpis
  - enterprise-integration/tenant-specific-configuration-and-feature-flags
sources:
  - Kohavi, Tang and Xu, 2020 — Trustworthy Online Controlled Experiments
---

## TL;DR
An A/B test randomly assigns users (or sessions) to the current system or a candidate and compares a
pre-declared outcome metric, so a difference can be attributed to the change rather than to time,
traffic mix or luck. It is the only online method that gives a causal answer, and it exists because
offline scores and before-and-after dashboards cannot. The discipline is in the design: the
randomisation unit, one primary metric with guardrails, a sample size fixed in advance, and no peeking.

## Why it exists
The question a product team actually has is causal: did this prompt, this model, this retriever change
make users better off? A before-and-after comparison cannot answer it, because everything else moved at
the same time: the day of the week, a marketing campaign, a news event that changed what people ask, a
provider update to the model behind the API. Offline evaluation cannot answer it either, because it
measures proxies on a frozen dataset, not outcomes on real people. Asking users which version they
prefer selects for the users who answer surveys. Letting an LLM judge pick a winner on production traces
measures the judge's taste.

For LLM products the need is sharper than for classic software. Changes are cheap to make (edit a
prompt) and their effects are subtle (a few points of resolution rate), so the temptation to ship on a
hunch is high and the chance of noticing a small regression without an experiment is low. Outcomes are
behavioural and delayed: whether the user came back, whether the ticket stayed closed. And costs move
with quality: a candidate that resolves 1% more tickets at 40% more tokens per conversation may be a
loss, which only a test with cost as a guardrail reveals.

Randomisation is what removes the confounders: with users assigned by a coin flip, the only systematic
difference between the arms is the change, so the difference in outcomes is the effect of the change
plus noise, and the noise can be quantified. That is also why A/B sits at the top of the evaluation
ladder rather than replacing it: it is slow, needs traffic, and exposes real users to the candidate, so
it is reserved for changes that have already passed the cheaper rungs.

## How it works
### One hypothesis, one primary metric, guardrails
Declare before starting: the change, the primary outcome metric (resolution without escalation, task
completed, conversion, return within seven days), the minimum effect worth detecting, and the guardrail
metrics that must not degrade (p95 latency, cost per conversation, error rate, refusal rate, safety
flags, escalations). A single primary metric prevents fishing through twenty metrics for one that
moved; guardrails prevent "winning" by breaking something the primary metric does not see.

### The randomisation unit
Assign at the user level for anything conversational: `bucket = hash(user_id + experiment_salt) mod
100`, so a user always sees the same variant. A user who sees both arms confounds the comparison and
notices the inconsistency. Session-level assignment is for anonymous traffic; request-level only for
stateless one-shot tasks. The unit of *analysis* must match the unit of *randomisation*: randomising by
user and then counting requests as independent samples underestimates variance, because one user's
requests are correlated, and produces false positives. The salt is per experiment so that assignments are
independent across experiments, instead of the same users always landing in treatment together.

### Sample size before, not after
Power analysis says how much traffic the test needs to detect the minimum effect. For a proportion
metric at baseline *p₁* and target *p₂*, two-sided α = 0.05 and 80% power, the standard formula gives
roughly *n* ≈ 16 · *p*(1 − *p*) / δ² per arm, where δ = *p₂* − *p₁*. To move resolution rate from 60%
to 63%: 16 × 0.24 / 0.0009 ≈ 4,270 users per arm (the exact two-proportion formula in the example gives
about 4,130). To detect a 1-point lift instead, it is nine times that. This is why low-traffic
enterprise deployments often cannot A/B on outcomes at all and fall back to pairwise judging on shadow
traffic or to longer-running tests with larger expected effects.

### Running it
Start with a small ramp (1%, then 10%) guarded by the canary metrics, then move to the planned split,
usually 50/50 because it maximises power for a given total. Run for at least one full weekly cycle so
weekday and weekend users are both represented. Check the **sample ratio**: if a 50/50 assignment logs
52/48 on 10,000 users (z ≈ 4, p < 0.001), the bucketing or logging is biased (a crash on one arm drops its users, say) and the result is
invalid regardless of the p-value.

### Analysis
A two-proportion z-test or a t-test on per-user means, reported as an effect size with a confidence
interval, not just a p-value. Do not peek: looking at the result every day and stopping when it is
significant inflates the false-positive rate well above the nominal 5%; if early stopping is needed, use
a sequential method designed for it (group-sequential boundaries or always-valid p-values). Segment
breakdowns are hypothesis generation, not results, because twenty segments at α = 0.05 will produce a
"significant" one by chance. Watch for novelty: compare week two with week one, since a new behaviour
can draw attention that fades.

### LLM-specific wrinkles
- **Non-stationary models.** If the test is "model X vs model Y" and X is silently updated mid-test,
  the comparison is confounded; pin snapshots or versions for the duration.
- **Shared caches.** Prefix or semantic caches shared across arms leak latency and cost between them;
  isolate per arm or exclude cache hits from guardrail metrics.
- **Judge-based primary metrics.** When outcome events are too sparse, a calibrated judge over sampled
  traces can serve as the primary metric, but it must be validated against outcomes first or the test
  measures the judge.
- **Interleaving for retrieval.** For rankers and retrievers, interleaving the two systems' results in
  one list and counting which source gets clicks is far more sensitive than an A/B on the same traffic.
- **Bandits.** When exploiting the better arm during the test matters more than a clean estimate, a
  multi-armed bandit shifts traffic adaptively, at the cost of a harder-to-interpret result.

## Example
Sample size for a 60% to 63% lift, the two-proportion test on the resulting data, and a sample-ratio
check. Running it prints roughly 4,129 users per arm; the test on 2,460/4,100 versus 2,583/4,100 gives
z ≈ 2.8, p ≈ 0.005 and a 95% interval of about +0.9 to +5.1 points; the SRM check on 4,100 versus 4,350
logged users returns p ≈ 0.007, which means the assignment or logging is broken and the test is void.

```python
import math

def phi(x: float) -> float:                        # standard normal CDF
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

Z_ALPHA, Z_POWER = 1.959964, 0.841621              # two-sided alpha = 0.05, power = 0.80

def n_per_arm(p1: float, p2: float) -> int:
    pbar = (p1 + p2) / 2
    num = Z_ALPHA * math.sqrt(2 * pbar * (1 - pbar)) + Z_POWER * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    return math.ceil((num / abs(p2 - p1)) ** 2)

def two_proportion(x1: int, n1: int, x2: int, n2: int) -> dict:
    p1, p2, p = x1 / n1, x2 / n2, (x1 + x2) / (n1 + n2)
    z = (p2 - p1) / math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    lo, hi = p2 - p1 - Z_ALPHA * se, p2 - p1 + Z_ALPHA * se
    return {"lift_pp": round(100 * (p2 - p1), 2), "z": round(z, 2),
            "p_value": round(2 * (1 - phi(abs(z))), 4), "ci95_pp": (round(100 * lo, 2), round(100 * hi, 2))}

def sample_ratio_mismatch(n1: int, n2: int, expected: float = 0.5) -> float:
    n = n1 + n2
    z = (n1 - expected * n) / math.sqrt(n * expected * (1 - expected))
    return 2 * (1 - phi(abs(z)))                   # small p-value: assignment or logging is biased

print(n_per_arm(0.60, 0.63))                           # users per arm needed for a 3-point lift
print(two_proportion(x1=2460, n1=4100, x2=2583, n2=4100))
print(sample_ratio_mismatch(4100, 4350))               # planned 50/50; investigate before reading results
```

## Trade-offs
- Randomisation gives a causal answer no other method does, but it costs calendar time and traffic, and
  it exposes half your users to a possibly worse variant for the duration.
- A single pre-declared primary metric protects against fishing, but it can miss an improvement that
  shows up elsewhere; secondary metrics inform the next hypothesis rather than this decision.
- User-level assignment keeps the experience consistent and the statistics honest, but it reduces the
  effective sample size compared with counting requests, which flips only for stateless tasks.
- Sequential methods allow early stopping safely, but they need a larger maximum sample than a
  fixed-horizon test for the same power, so they pay off only when real effects often stop the test early.
- Judge-scored metrics make low-traffic tests feasible, but they move the question from "did users
  benefit" to "did the judge approve", which is acceptable only after the judge is validated.

## Pitfalls
- Randomising by user and analysing by request; the symptom is many "significant" results that fail to
  reproduce, because correlated requests were counted as independent.
- Peeking daily and stopping at the first significant day; the symptom is a win rate across experiments
  far above what the effects justify, and shipped changes that do nothing.
- Ignoring a sample ratio mismatch; the symptom is a clean-looking lift caused by one arm silently
  dropping the users who hit an error.
- Running a model-vs-model test without pinning versions; the symptom is a result that flips halfway
  through when the provider updates one of the models.
- Declaring the primary metric after seeing the data; the symptom is every experiment "winning" on a
  different metric.
- Stopping after three days; the symptom is a weekday-only population and a novelty effect reported as
  a durable lift.

## When to use
Reach for an A/B test when a change is strategic or expensive enough to need a causal answer on user
outcomes, when the traffic exists to detect the effect you care about within a few weeks, and when the
candidate has already passed offline evals and shadow mode. Do not run one for every prompt tweak, for
changes whose effect you could not act on, or when the power analysis says it would take six months. In
those cases use pairwise judging on shadow traffic and accept a weaker answer. Measure the primary
metric with a confidence interval, the guardrails, and the sample ratio, and record every experiment
with its hypothesis and decision. In interviews, the expected depth is the randomisation-unit argument,
a back-of-envelope sample size, and the peeking and SRM failure modes.

## Role lens
- **Engineer:** Implements deterministic bucketing with a per-experiment salt, logs assignment and
  outcome events on the same key, pins model versions per arm, and runs the SRM check before analysis.
- **Architect:** Owns the experimentation platform decisions: units of assignment, metric definitions,
  guardrails, and which changes require a test before a full rollout.
- **FDE:** Negotiates with the customer which outcome counts as success, sizes the test against their
  actual volume, and proposes shadow-mode pairwise judging when their traffic cannot power an A/B.

## Check yourself
**Q:** A team wants to A/B test a new model for a support assistant with about 1,500 conversations a
week, hoping to lift resolution rate from 70% to 72%. What do you tell them?
**A:** Run the numbers first: with *p* ≈ 0.71 and δ = 0.02, the per-arm requirement is roughly
16 × 0.206 / 0.0004 ≈ 8,240 users, so about 16,500 in total, which at 1,500 a week is around eleven
weeks even if every conversation is a distinct user. A model-vs-model test over that period is also
exposed to provider updates, so both versions must be pinned. The honest options are to accept a larger
minimum detectable effect, to run shadow mode with a validated pairwise judge and treat the result as
evidence rather than proof, to use a judge-scored metric on sampled traces as the primary metric after
validating it against resolution, or to run the test and pre-commit to the long horizon with no peeking.
Whatever they choose, assignment must be per user with a per-experiment salt, and the sample ratio and
guardrails (latency, cost, escalations) are checked before anyone reads the primary metric.

## Quiz
### Q: A team randomises by user but computes significance over individual requests. What goes wrong?
- [ ] The test loses power because users are counted once
- [x] Variance is underestimated because a user's requests are correlated, producing false positives
- [ ] The sample ratio check becomes impossible
- [ ] Nothing, as long as the primary metric is a proportion
> Requests from one user are not independent samples; treating them as such makes the standard error
> too small and the result look significant when it is not. The analysis unit must match the
> randomisation unit.

### Q: Why does checking the result every day and stopping when p < 0.05 invalidate the test?
- [ ] Daily traffic is too small to compute a p-value
- [ ] It violates the guardrail metrics
- [x] Repeated looks give many chances to cross the threshold by noise, inflating the false-positive rate
- [ ] It changes the randomisation unit mid-test
> A fixed-horizon p-value is valid for one look. Looking repeatedly and stopping on the first
> significant day turns a 5% error rate into a much larger one; sequential methods exist precisely to
> make early stopping valid.

### Q: A 50/50 experiment logged 4,100 users in control and 4,350 in treatment. What is the right action?
- [ ] Analyse as usual; the imbalance is within normal variation
- [ ] Reweight the control arm to match
- [x] Treat the test as invalid until the cause of the sample ratio mismatch is found
- [ ] Extend the test until the counts equalise
> A mismatch this large under a 50/50 split is very unlikely by chance and usually means one arm drops
> users (errors, logging failures), which biases who remains. Any result computed on biased arms is
> untrustworthy, so fix the pipeline first.
