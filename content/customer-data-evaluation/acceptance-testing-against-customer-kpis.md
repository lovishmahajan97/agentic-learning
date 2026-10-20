---
id: acceptance-testing-against-customer-kpis
module: customer-data-evaluation
name: Acceptance testing against customer KPIs
kind: practice
difficulty: advanced
est_minutes: 18
status: reviewed
related:
  - scoping/success-metrics-kpis
  - scoping/baseline-measurement-before-deployment
  - evals/task-specific-acceptance-criteria
  - deployment/uat
  - evals/a-b-testing
  - customer-data-evaluation/shadow-mode-validation
  - customer-data-evaluation/representative-test-case-sampling
  - customer-data-evaluation/golden-dataset-creation-with-client-smes
---

## TL;DR
Acceptance testing against customer KPIs is the pre-agreed test that decides whether an AI system is
accepted: business KPIs (handle time, error rate, throughput) are translated into measurable proxies,
thresholds and sample sizes, signed before results are seen, then measured offline on the golden set and
in a pilot against a baseline. It exists because "the model scores 91%" does not answer "did we reduce
invoice processing cost without more errors?", and because thresholds set after the fact get renegotiated.

## Why it exists
A customer buys an outcome, not an accuracy number. The sponsor's success statement is in business terms:
cut average handle time, raise first-contact resolution, process more claims per adjuster, keep error
rates no worse than today. Engineering teams naturally report what they can measure, model-level
metrics, and the two languages do not meet. A 91% extraction accuracy might be great or useless depending
on which fields fail, how errors are caught downstream and what the human process achieved before.

Three failures follow when acceptance is left informal. First, there is no baseline: nobody measured the
current process, so "better" has no reference and the human error rate is assumed to be zero. Second,
the bar moves: once results are in, stakeholders who are unhappy for other reasons re-open the
definition of success. Third, the statistics are ignored: a pilot of 200 cases "beats" a 2% error target
with 1.5%, yet the uncertainty on 200 cases is wider than the difference. The naive alternatives,
accepting on a demo or on a single overall accuracy number, either prove nothing or prove the wrong
thing. Acceptance testing exists to make the deal explicit: these metrics, measured this way, on this
data, with these thresholds and this much confidence, mean the system is accepted.

## How it works
### 1. Start from the KPI tree
Write the sponsor's KPI at the top (cost per invoice processed), decompose it into drivers (share of
invoices needing no human touch, review time per touched invoice, error rate reaching payment), and map
each driver to something measurable offline or in a pilot. Offline evals can measure accuracy and
straight-through eligibility; only a pilot can measure time and behaviour change. Doing the tree
explicitly matters *because* it shows which model metric moves which business number.

### 2. Measure the baseline
Measure the current process on the same kind of sample: human error rate, handle time, throughput.
Without it, thresholds are guesses and the system is held to a standard of perfection the humans never
met.

### 3. Define criteria of three types
- **Primary:** the KPI proxies that must improve (straight-through rate, handle time).
- **Guardrails:** metrics that must not get worse (critical-field error rate, compliance violations,
  escalation rate), *because* optimising one KPI often degrades another.
- **Segment floors:** minimum performance on high-stakes strata (high-value invoices, regulated
  products), *because* an overall average can pass while the segment that matters fails.

### 4. Fix the statistics in advance
For each criterion, state whether the threshold applies to the point estimate or to a confidence bound,
and size the sample accordingly. For proportions near 0 or 1, use an interval that behaves well there,
such as the Wilson interval; the simple normal approximation can produce bounds below zero. Sample
size must be agreed with this in mind *because* some criteria are unprovable at small n.

Three decisions hide inside "no worse than today":
- **Non-inferiority margin.** Requiring the system's upper bound to sit at or below the baseline's
  point estimate demands near-proof that it is *better*. The usual form is non-inferiority: upper bound
  ≤ baseline + δ, where δ is the largest degradation the sponsor would accept. Choosing δ is a business
  decision, not a statistical one, so it is the sponsor's to sign.
- **Baseline uncertainty.** The baseline is itself a sample. If it was measured on a few hundred cases,
  its own interval may be wider than the margin; either measure it on more cases or acknowledge that the
  comparison is approximate.
- **Power.** To show an upper bound at or below threshold t when the true rate is p, with z_α for the
  bound and z_β for the chance of passing, roughly
  n ≈ [z_α·√(t(1−t)) + z_β·√(p(1−p))]² / (t − p)². The (t − p)² in the denominator is the lesson: halving
  the gap between expected performance and threshold quadruples the sample.

Finally, if acceptance requires *every* criterion to pass, the chances multiply: three independent
criteria each passed with 90% probability by a system that truly meets them are all passed only about
73% of the time (0.9³). That is a reason to keep the signed list short and to power each criterion
generously.

### 5. Sign before measuring
Put metrics, data, thresholds, sample sizes and the decision rule in the statement of work or an
acceptance annex, signed by the sponsor. Freeze the acceptance dataset version and the holdout.

### 6. Run in stages
Offline on the frozen golden holdout, then shadow mode or a limited pilot on live traffic with humans
still in control, then a pilot that measures time and throughput against the baseline or a concurrent
control group. Each stage has its own pass criteria, and failure sends you back to the review loop.

### 7. Report against the contract
Report every criterion with its estimate, interval and pass or fail, plus segment results. Do not
substitute a different metric that happens to look better.

## Example
Invoice extraction for an accounts-payable team. Baseline, measured on 500 invoices: 2.0% critical-field
error rate (amount, supplier bank details, due date), and every invoice is touched by a human. The KPI is
cost per invoice; the agreed proxies are below, all illustrative.

```python
import math

def wilson(k, n, z=1.96):
    """95% Wilson interval for a proportion k/n (better than the normal approximation near 0 or 1)."""
    p, d = k / n, 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return centre - half, centre + half

# Criteria signed in the SOW: (name, observed count, n, rule, threshold)
CRITERIA = [
    ("critical-field error rate", 6, 400, "upper<=", 0.02),   # guardrail: no worse than baseline
    ("straight-through rate", 292, 400, "lower>=", 0.65),     # primary: no human touch needed
    ("critical-field error, high-value", 3, 80, "upper<=", 0.03),  # segment floor
]
for name, k, n, rule, t in CRITERIA:
    lo, hi = wilson(k, n)
    ok = hi <= t if rule == "upper<=" else lo >= t
    print(f"{name:34s} {k/n:6.1%} CI [{lo:.1%}, {hi:.1%}] {rule}{t:.0%} {'PASS' if ok else 'FAIL'}")
# critical-field error rate            1.5% CI [0.7%, 3.2%] upper<=2% FAIL
# straight-through rate               73.0% CI [68.4%, 77.1%] lower>=65% PASS
# critical-field error, high-value     3.8% CI [1.3%, 10.5%] upper<=3% FAIL
```

The point estimate of 1.5% beats the 2% baseline, but 400 invoices cannot show it with confidence; with
a true rate of 1.5%, about 3,000 invoices are needed just for the upper bound at the expected error
count to reach 2%, and at that size the test still passes only about half the time; roughly 8,000
invoices are needed for around a 90% chance of passing. The criterion was also stricter than its
label: the baseline was 10 errors in 500, whose own Wilson interval is roughly 1.1% to 3.6%, so "upper
bound ≤ 2.0%" asked for proof of improvement over a noisy number. A non-inferiority version, upper bound
≤ 2.5% (a 0.5-point margin the AP manager signs as tolerable), needs only about 2,000 to 2,500 invoices
for a roughly 90% chance of passing at a true 1.5%, a volume pilot traffic can often supply. The
segment floor is worse: even zero errors in 80 invoices gives an upper bound near 4.6%, so that
criterion could never pass at that size. All of this should have surfaced when the annex was written.
The fix is a renegotiation *before* testing: either larger samples (pilot traffic can supply them), or a
point-estimate rule backed by a downstream control such as human review of all high-value invoices.

## Trade-offs
- Confidence-bound thresholds protect the customer from lucky small samples, but demand large test sets;
  when labelled data is scarce, use point estimates plus a production guardrail that triggers rollback.
- Concurrent control groups give a clean causal estimate of time saved, but cost the customer capacity
  running two processes; a before-and-after comparison is cheaper when volume and mix are stable.
- A wider non-inferiority margin makes the test passable at realistic sample sizes, but admits a
  system that is somewhat worse than today; when errors are costly or regulated, keep δ small and pay
  for the larger sample instead.
- Many criteria cover more risks, but each extra criterion raises the chance of failing on noise;
  keep a handful of primary metrics and guardrails, and report the rest as diagnostics.
- Strict segment floors protect high-stakes cases, but can block launch on tiny segments; when a segment
  is too small to test, route it to humans and exclude it from automated scope.

## Pitfalls
- Setting thresholds without a measured baseline; the symptom is a system rejected for a 2% error rate
  when the human process, once measured, turns out to run higher.
- Agreeing thresholds after seeing results; the symptom is a sponsor or a skeptical stakeholder
  re-opening the definition of success at the go/no-go meeting.
- Only primary metrics, no guardrails; the symptom is a faster process that pushes more errors into
  payment or compliance, discovered at month-end.
- Criteria that are unprovable at the agreed sample size; the symptom is a "fail" on a system whose
  observed performance beats the threshold.
- Accepting on offline evals alone; the symptom is good golden-set numbers and no change in handle time,
  because users do not trust the output and re-check everything.

## When to use
Define acceptance tests at scoping, as soon as the KPIs and baseline are known, and certainly before the
pilot starts; they drive dataset size, sampling strata and pilot design. Use offline criteria for
quality and pilot criteria for time, throughput and behaviour. Skip formal statistics only for
low-stakes internal tools where the cost of a wrong decision is a cheap rollback. Track each criterion
with its interval over the pilot, not just at the end. In interviews, show the chain from business KPI
to proxy to threshold to sample size, and mention baselines and guardrails unprompted.

## Role lens
- **Engineer:** Implements the acceptance harness that computes every signed criterion with intervals and
  segment breakdowns from frozen dataset versions, reproducibly.
- **Architect:** Owns the KPI tree and decides which criteria are tested offline, in shadow mode, or in a
  live pilot, and what production guardrails back them after launch.
- **FDE:** Negotiates the acceptance annex with the sponsor, insists on a baseline, and checks that
  thresholds are provable at the agreed sample size before anything is signed.

## Check yourself
**Q:** A sponsor wants the contract to say "the AI must be 99% accurate". How do you turn that into an
acceptance test you can actually pass or fail fairly?
**A:** I would ask what business outcome 99% stands for, then decompose it: which errors matter (a wrong
bank account is not a misspelled supplier name), what the current human error rate is, and which KPI
the sponsor is buying, such as time or cost per item. I would propose a primary metric tied to that KPI,
guardrails on critical-field errors framed as non-inferiority against the measured baseline, with a
margin the sponsor signs, and floors on high-stakes segments. For each I would state whether the threshold applies to a point estimate or a confidence bound
and size the sample so it is provable. Then everything goes into a signed annex with a frozen dataset.

## Quiz
### Q: Why measure the human baseline before setting acceptance thresholds?
- [ ] Baselines are required for statistical significance tests
- [ ] The AI system must always beat humans by a fixed margin
- [x] Without it, thresholds are guesses and the system may be held to a standard the current process never met
- [ ] Baselines replace the need for a golden dataset
> Human processes have error rates and costs too. Measuring them grounds thresholds in reality and
> turns "is it perfect?" into "is it better than what we do today?"

### Q: An acceptance criterion says the error rate's 95% upper bound must be at most 3%, tested on 80 items. What is the problem?
- [ ] 80 items is too many for a segment test
- [ ] Upper bounds should never be used for error rates
- [ ] The Wilson interval cannot be computed at small n
- [x] Even zero errors on 80 items leaves an upper bound above 3%, so the criterion cannot pass
> With n = 80, the Wilson upper bound for zero errors is about 4.6%. The test is unpassable by design,
> which should be caught when the criteria and sample sizes are agreed.

### Q: What is the role of guardrail metrics in a KPI acceptance test?
- [ ] They replace primary metrics when those are hard to measure
- [x] They ensure improving the target KPI does not degrade something else that matters, such as errors
- [ ] They are only reported after launch
- [ ] They measure the model's latency and nothing else
> Pushing straight-through rate or speed can increase downstream errors or compliance breaches.
> Guardrails make "no worse than baseline" on those dimensions an explicit condition of acceptance.
