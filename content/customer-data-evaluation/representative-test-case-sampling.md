---
id: representative-test-case-sampling
module: customer-data-evaluation
name: Representative test-case sampling
kind: practice
difficulty: core
est_minutes: 16
status: reviewed
related:
  - evals/golden-dataset
  - evals/error-analysis
  - ml-fundamentals/train-test-leakage
  - customer-data-evaluation/golden-dataset-creation-with-client-smes
  - customer-data-evaluation/acceptance-testing-against-customer-kpis
  - customer-data-evaluation/customer-specific-regression-suites
---

## TL;DR
Representative test-case sampling chooses which of a customer's real inputs go into the eval set so that
its score predicts production behaviour. A random sample mirrors traffic but starves rare high-stakes
cases; a hand-picked one reflects what people remember, not what happens. The fix is stratified
sampling from recent production with a floor per important stratum, then re-weighting results back to
the real mix, so you get both a trustworthy overall number and enough cases per segment to act on.

## Why it exists
An eval score is an estimate of how the system will behave on future traffic, and an estimate is only as
good as the sample behind it. The customer usually hands over one of two things: the cases their team
remembers (the weird ones, the ones that caused an escalation, the demo favourites) or a random export.
Both mislead, in opposite directions.

Hand-picked sets over-represent memorable edge cases and under-represent the boring bulk, so the score
is pessimistic on volume and says nothing reliable about the mix. Worse, the cases people remember are
the ones already discussed, so they are biased toward problems the team has seen. A simple random sample
fixes the mix but fails on rare strata: if legal-threat tickets are 2% of traffic, a 300-item random
sample contains about six of them, too few to tell 60% accuracy from 90%, even though a wrong answer
there is the most expensive kind. There is also a time problem: a sample drawn from last year's traffic
misses new products, policy changes and seasonal patterns. Representative sampling exists to produce an
eval set that is both faithful to production in aggregate and dense enough in the strata where decisions
are made.

## How it works
### 1. Define the population and the sampling unit
State exactly which traffic the system will see: which channels, products, languages and date range.
Pull from the most recent representative window, ideally the same period of the business cycle the
launch will face, *because* traffic composition drifts and old samples encode old policies. Pick the
unit deliberately: sample conversations or documents, not turns or pages, when the system is judged on
the whole, and cap items per account so one heavy customer cannot dominate. Check what the logs cannot
contain, too: if today's process routes legal threats straight to a phone team, they never reach the
ticket log, and no sampling scheme recovers them; that gap must be filled from another source.

### 2. Choose strata from the decision, not the data
Strata are the segments where you need a separate answer: intent or document type, customer tier,
language, risk class, input length band, source system. Pick them by asking "if quality differed here,
would we act differently?" Each stratum you add costs sample budget, so keep only those with a
consequence. When nobody knows the segments yet, cluster a large unlabelled slice by embedding,
have SMEs name the clusters, and promote the ones with distinct risk to strata.

### 3. Size the budget and allocate with a floor
Precision depends on the count in a stratum, not its share of traffic. For a pass rate near p measured
on n items, the standard error is about √(p(1−p)/n): at p = 0.9 and n = 100 that is 0.03, so a 95%
interval is roughly ±6 points, and six items cannot distinguish anything. Inverting it gives the count
a stratum needs for a target half-width E: n ≈ 1.96² · p(1−p) / E². When failures should be rare, the
"rule of three" is the quick check: zero failures in n items still leaves a one-sided 95% upper bound of
about 3/n on the failure rate, so proving "under 1% failures" needs roughly 300 clean items in that
stratum.

Allocation is then a deliberate compromise. Proportional allocation mirrors traffic and is best for the
headline number only when strata behave alike; Neyman allocation (n_h ∝ N_h·σ_h, where σ_h is the
stratum's spread) minimises the variance of the population estimate. Neither guarantees a usable count
in a 2% stratum, so give every important stratum a floor first and distribute the rest of the budget in
proportion to traffic.

### 4. Deduplicate before sampling
Production logs are full of near-duplicates: templated emails, retries, the same invoice resubmitted.
Cluster by normalised text or embedding similarity and sample clusters, not rows, *because* fifty copies
of one template make the set look large while testing one case.

### 5. Re-weight to report the population number
Because strata were sampled at different rates, the raw average over the set is biased toward the
oversampled strata. With W_h the stratum's share of traffic and p_h its measured pass rate, the
production estimate is p̂ = Σ W_h·p_h, and its standard error is √(Σ W_h²·p_h(1−p_h)/n_h). The second
formula shows the price of floors: oversampled small strata contribute almost nothing to the variance
(W_h² is tiny), while the bulk stratum, which lost items to the floors, contributes most of it. Report
per-stratum numbers with their own intervals alongside the weighted one.

### 6. Add targeted cases as a labelled minority
Known failure modes, adversarial inputs and SME-nominated edge cases belong in the suite, but tagged as a
separate "targeted" stratum that is excluded from the weighted population metric. Mixing them in
silently makes the headline number unrepresentative.

### 7. Keep it fresh and leak-free
Resample a slice each cycle from new traffic, keep a holdout nobody tunes against, and make sure no
sampled case also appears as a prompt example or in a fine-tuning set.

## Example
Ticket triage for a telecom customer. Of 10,000 recent tickets, 9,000 are billing, 800 cancellation and
200 legal threats (illustrative mix). The budget is 300 SME-labelled items, with a floor of 60 per
stratum so the rare one is measurable.

```python
import random, math
from collections import defaultdict

def stratified_sample(logs, key, budget, floor, seed=7):
    """Floor per stratum first, the rest of the budget proportionally. Returns sample, weights."""
    rng, strata = random.Random(seed), defaultdict(list)
    for r in logs:
        strata[key(r)].append(r)
    spare = budget - floor * len(strata)
    sample, weight = [], {}
    for s, items in strata.items():
        n = min(len(items), floor + round(spare * len(items) / len(logs)))
        sample += [(s, r) for r in rng.sample(items, n)]
        weight[s] = len(items) / n          # each sampled item stands for this many real ones
    return sample, weight

def weighted_estimate(graded, weight, N):
    """graded: (stratum, correct) pairs. Population estimate and its standard error."""
    by = defaultdict(list)
    for s, ok in graded:
        by[s].append(ok)
    est = se2 = 0.0
    for s, oks in by.items():
        W, p = weight[s] * len(oks) / N, sum(oks) / len(oks)   # W = stratum share of traffic
        est += W * p
        se2 += W * W * p * (1 - p) / len(oks)
    return est, math.sqrt(se2)

logs = ([{"intent": "billing"}] * 9000 + [{"intent": "cancel"}] * 800
        + [{"intent": "legal"}] * 200)
sample, w = stratified_sample(logs, key=lambda r: r["intent"], budget=300, floor=60)
n = {s: sum(t == s for t, _ in sample) for s in w}
print(n)            # {'billing': 168, 'cancel': 70, 'legal': 62}

true_acc = {"billing": 0.95, "cancel": 0.85, "legal": 0.60}   # pretend grading results
graded = [(s, i < round(true_acc[s] * n[s])) for s in n for i in range(n[s])]
raw = sum(ok for _, ok in graded) / len(graded)
est, se = weighted_estimate(graded, w, len(logs))
print(f"unweighted {raw:.3f}  weighted {est:.3f} ± {1.96 * se:.3f}")
# unweighted 0.857  weighted 0.938 ± 0.030
```

The unweighted 85.7% understates production quality because legal threats are 21% of the set but 2% of
traffic; the weighted 93.8% is the population estimate. The floors cost little on the headline: ±3.0
points against about ±2.7 for a simple random sample of 300 at the same accuracy, because the legal and
cancel strata carry tiny weights. What they buy is the per-stratum view, which is the actionable one: at
62 items, legal accuracy near 60% carries roughly ±12 points of uncertainty, enough to know it is far
below billing and must be fixed before launch. A proportional 300-item sample would have held about six
legal tickets.

## Trade-offs
- Floors make rare strata measurable, but they spend budget away from the bulk, widening the interval on
  the population metric; when the overall number is the acceptance criterion, lower the floors.
- More strata give finer diagnosis, but each one needs its own floor; beyond a handful, merge strata
  whose quality you would act on identically.
- Recent-window sampling tracks current traffic, but can miss seasonal or quarter-end patterns; when the
  business is seasonal, sample across the cycle or add the missing period explicitly.
- Cluster-level deduplication tests more distinct behaviours per label, but underweights genuinely
  high-volume templates; weight clusters by size when reporting the population metric.

## Pitfalls
- Reporting the raw average of an oversampled set as "accuracy"; the symptom is a production metric that
  is consistently higher or lower than the eval said, by a stable amount.
- Letting the customer hand-pick the eval set; the symptom is a set dominated by escalation stories and
  a score that does not move when the bulk of traffic improves.
- Sampling before deduplicating; the symptom is a large share of the set sharing one template, and fixes
  for that template swinging the score far more than their production impact.
- Mixing targeted adversarial cases into the population metric; the symptom is a headline number that
  drops every time someone adds a new red-team case.
- Treating the log as the population when part of the traffic bypasses it; the symptom is post-launch
  complaints about a case type that never appeared in the eval set at all.
- Sampling from a period before a policy or product change; the symptom is SMEs relabelling many items
  as "no longer how we do it".

## When to use
Use stratified, re-weighted sampling whenever an eval set will inform a ship decision or be quoted to a
customer, and whenever traffic contains rare segments with high cost of error. Simple random sampling is
fine for a quick health check on homogeneous traffic. Decide strata with the business owner at the same
time as acceptance criteria, because they define where thresholds apply. Track per-stratum counts,
interval widths and the gap between eval and observed production metrics. In interviews, being able to
explain why a random sample fails for a 2% stratum, and how to re-weight, is the expected depth.

## Role lens
- **Engineer:** Implements deduplication, stratified draws with floors and population re-weighting, and
  reports per-stratum results with confidence intervals rather than one average.
- **Architect:** Defines the sampling frame and refresh cadence as part of the eval platform, so every
  customer's suite is drawn and weighted the same auditable way.
- **FDE:** Works with the customer to name the strata that matter commercially, and explains why their
  hand-picked favourites become a targeted stratum, not the whole set.

## Check yourself
**Q:** A customer's eval set shows 92% accuracy, but production QA reports 81%. The set was built from
cases their team nominated. What is likely happening and how do you fix it?
**A:** Nominated sets reflect memory, not traffic. The nominated cases are probably heavy in categories the
team already worked on and light in the high-volume segments where production errors actually occur,
or drawn from an older period. I would compare the strata mix of the set against recent traffic, then
draw a fresh stratified sample from the latest production window, deduplicated, with floors for
high-stakes segments, and re-weight results to the production mix. I would keep the nominated cases as a
targeted stratum outside the headline number. If the new weighted metric is close to 81%, the gap was
sampling; if not, I would look at grading differences between eval and production QA.

## Quiz
### Q: Legal-threat tickets are 2% of traffic. Why is a 300-item simple random sample a poor eval for them?
- [ ] Random sampling systematically excludes rare categories
- [ ] Legal tickets need a different metric than accuracy
- [x] It contains only about six such tickets, too few to estimate their accuracy with useful precision
- [ ] Rare categories should be evaluated only in production
> Precision depends on the number of items in the stratum. Six items give an interval tens of points
> wide, so a floor (oversampling) is needed, followed by re-weighting for the overall metric.

### Q: After oversampling rare strata, how should you report overall production accuracy?
- [ ] Use the plain average over all sampled items
- [ ] Report only the worst stratum
- [ ] Drop the oversampled items and average the rest
- [x] Weight each stratum's results by its share of production traffic
> Oversampling distorts the raw average toward rare strata. Weighting by stratum size over sample count
> recovers the population estimate while keeping the per-stratum precision you paid for.

### Q: Why deduplicate production logs before sampling test cases?
- [ ] Duplicates break most labelling tools
- [x] Near-duplicate templates inflate the set while testing one behaviour, so fixes to them distort the score
- [ ] Duplicates contain more personal data than unique records
- [ ] Deduplication guarantees the set matches production traffic
> Fifty copies of a templated email are one test case wearing fifty labels. Sampling clusters gives more
> distinct behaviours per expert label; re-weighting by cluster size restores the population view.
