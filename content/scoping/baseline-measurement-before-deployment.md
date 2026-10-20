---
id: baseline-measurement-before-deployment
module: scoping
name: Baseline measurement before deployment
kind: practice
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - scoping/success-metrics-kpis
  - scoping/workflow-analysis
  - scoping/roi-model
  - evals/a-b-testing
  - customer-data-evaluation/shadow-mode-validation
  - customer-data-evaluation/representative-test-case-sampling
  - adoption/adoption-and-value-realization-measurement
sources:
  - Kohavi, Tang, Xu, 2020 — Trustworthy Online Controlled Experiments
---

## TL;DR
A baseline is the measured value of each success metric under the current process, captured before
the AI system changes anything. Without it, an "after" number means nothing, and you cannot get it
back later: the old process disappears and people's behaviour shifts. Measure variance as well as the
mean, stratify by case type, audit human quality the same way you will audit the AI, and freeze the
definitions.

## Why it exists
Every claim of AI impact is a difference: handle time fell, errors dropped, throughput rose. A
difference needs two numbers, and the first one has to be measured before deployment, because
deployment destroys it. Once the assistant is live, agents work differently, the old tool gets
switched off, the team is reorganised around the new flow, and the historical data was never logged
at the right granularity. Trying to reconstruct the baseline at review time produces exactly the
contested, cherry-picked number the review was meant to avoid.

The naive substitutes fail in known ways. Existing management reports use their own definitions: they
often exclude escalated cases, mix business days with calendar days, or report a target rather than a
measurement. Asking subject-matter experts to estimate is unreliable, because people tend to misjudge
how long their own work takes and how often they make mistakes. That is why direct measurement
beats self-report. The most expensive failure is skipping the *quality* baseline. Customers often
demand near-perfect accuracy from the AI while the human process, once anyone checks, makes errors on
a meaningful fraction of cases. If you have not measured human error the same way, you will be held
to an imaginary standard. The project can then fail a bar the incumbent process never met.

## How it works
### 1. Start from the metric spec
Baseline exactly the metrics in the agreed spec, with the same numerator, denominator, population and
exclusions you will use after launch. If the definitions differ between before and after, the
comparison is invalid, however careful the measurement was.

### 2. Define the unit of work and the window
Pick the unit you will count: a ticket, a claim, a document, a report. Then choose a window long
enough to cover the work's natural cycles, such as month-end peaks, weekly patterns and seasonal
products. A two-week baseline taken in a quiet month and compared against a peak-season pilot will
show a "regression" that is really just volume.

### 3. Choose measurement methods per metric
- **System timestamps** from the ticketing, case-management or workflow tool, for cycle time and
  throughput. They are cheap and complete, but check what each timestamp actually records.
- **Time-and-motion or work sampling** for effort that the system does not log, such as time spent
  searching other tools. This is observational and small-sample, but it is the only way to see hidden work.
- **Retrospective quality audit:** SMEs blind re-review a random sample of past human outputs against
  the same rubric the AI will face. This is the step teams skip, and it is the one that sets a fair bar.
- **Shadow labelling:** humans keep working normally while the system's proposed output is logged
  but not used. This gives a paired, same-case comparison.

### 4. Capture distribution, not just averages
Record the median, p90 and standard deviation, and keep the raw per-case data. Variance determines how
many post-launch cases you need to detect a change. Handle times are usually right-skewed, so medians
and percentiles are more honest than means, and a log transform helps when testing.

### 5. Stratify by case type
Report each metric per segment (category, channel, region, complexity). This matters *because* of mix
shift. If the AI pilot happens to receive easier cases, the overall number improves even when no
segment does. This is a composition (mix) effect; its extreme form, where the aggregate trend runs
opposite to every segment's trend, is Simpson's paradox. Comparing within segments and re-weighting to
the baseline mix removes that illusion.

### 6. Size the comparison
For a proportion measured to within ±e, you need roughly n ≈ z²·p(1−p)/e² cases. To detect a mean
shift δ between two groups with standard deviation σ, you need roughly
n ≈ 2(z_α/2 + z_β)²σ²/δ² per arm. If the pilot cannot reach that volume in its window, you learn
that before launch, not at the review.

### 7. Freeze it
Store the queries, the data snapshot, the definitions, the date range, and the names of the people who
signed off. A baseline that nobody can re-run is just an anecdote with a decimal point.

### Choosing the after-comparison design
The baseline also determines how "after" will be read. A simple before/after comparison is weakest
because it absorbs seasonality and any concurrent change. A concurrent holdout (some teams or queues
without the tool) controls for time. A staggered rollout gives you both. Shadow mode gives paired
comparisons on identical cases, but it measures quality, not the behavioural effect on humans.

## Example
A support team will pilot an AI drafting assistant. This script computes the per-category baseline
from an 8-week ticket export, including a confidence interval on the human error rate from an SME audit
sample, and the pilot volume needed to detect a 2-minute drop in handle time.

```python
import csv, math, statistics as st
from collections import defaultdict

def wilson(k, n, z=1.96):
    """95% Wilson score interval for a proportion k/n (well-behaved for small n)."""
    p, d = k / n, 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return centre - half, centre + half

def n_per_arm(sd, delta, z_a=1.96, z_b=0.84):
    """Cases per arm to detect a mean shift of `delta` (two-sided 5%, 80% power)."""
    return math.ceil(2 * (z_a + z_b) ** 2 * sd ** 2 / delta ** 2)

by_cat = defaultdict(list)
with open("tickets_w01_w08.csv") as f:          # 8-week export, one row per closed ticket
    for r in csv.DictReader(f):
        by_cat[r["category"]].append(
            (float(r["handle_min"]), r["audited"] == "1", r["audit_error"] == "1"))

for cat, rows in sorted(by_cat.items()):
    mins = [m for m, _, _ in rows]
    errs = [e for _, audited, e in rows if audited]   # SME blind re-review sample
    lo, hi = wilson(sum(errs), len(errs)) if errs else (float("nan"),) * 2
    print(f"{cat:10} n={len(mins):5} median={st.median(mins):5.1f} "
          f"p90={st.quantiles(mins, n=10)[8]:5.1f} sd={st.stdev(mins):5.1f} "
          f"audited={len(errs):4} error_rate_95ci=[{lo:.1%}, {hi:.1%}]")

print(n_per_arm(sd=9.0, delta=2.0))   # billing: sd 9 min, detect a 2-minute drop -> 318
```

Read the output critically. Suppose billing shows 11 errors in 120 audited tickets. The point estimate
is 9.2%, but the Wilson interval is roughly 5% to 16%. "The AI must beat 9.2%" is therefore false
precision. The honest bar is "not worse than the human interval", and you may need to enlarge the
audit before signing it. The sample-size line says the pilot needs about 318 billing tickets in each
arm. If billing only gets 80 tickets a week, a two-week pilot cannot answer the question.

## Trade-offs
- Longer baseline windows capture seasonality and reduce noise but delay the project. Shorten them only
  when the work has no strong cycles and volume is high enough to reach the needed sample size.
- Retrospective quality audits cost SME hours, which the customer often resists. They become non-negotiable
  when the success criterion compares AI quality to human quality, which is most of the time.
- Concurrent holdout groups give the cleanest causal read but withhold the tool from some users. Use a
  pure before/after comparison only when the expected effect is far larger than seasonal variation.
- Shadow mode gives paired same-case comparisons without risk, but it does not measure how humans
  change when they actually rely on the output, so it cannot replace a live comparison.
- Heavy stratification makes comparisons fair but splits the sample thin. Merge segments whose
  baselines are statistically indistinguishable.

## Pitfalls
- Using management-report numbers as the baseline. The symptom is a "before" figure nobody can
  reproduce from raw data, and a dispute at review about what it included.
- Baselining only speed and not quality. The symptom is the customer holding the AI to a near-perfect
  accuracy bar the human process was never measured against.
- Ignoring case mix. The symptom is an overall improvement that disappears when results are broken down
  by category, because the pilot queue received easier work.
- Changing the metric definition after launch, for example by excluding a newly "out of scope" case type.
  The symptom is an improvement that appears exactly when the definition changed.
- Announcing the measurement to the team being measured, then using that period as the baseline. The
  symptom is unusually good baseline numbers that regress once observation ends.

## When to use
Measure a baseline for every metric in the success spec before any pilot user sees the system,
ideally during discovery while you are already mapping the workflow. If historical logs are good, the
baseline can be largely retrospective and take days. If effort or quality is not logged, plan two to
four weeks of sampling and audits, and put that in the statement of work as a customer dependency.
Skip it only for pure feasibility spikes with no value claim. In interviews, a baseline question usually
hides inside "how would you prove the pilot worked?" The expected answer covers before/after
definitions, human error rates, mix shift and sample size.

## Role lens
- **Engineer:** Writes the extraction queries and audit-sampling scripts, and makes the post-launch
  telemetry compute metrics with exactly the same definitions as the baseline.
- **Architect:** Chooses the comparison design (holdout, staggered rollout, shadow mode) and makes sure
  the platform can route and tag cases so that design is possible.
- **FDE:** Gets access to historical exports, schedules SME audit time, and gets the customer to sign
  off on the frozen baseline before the pilot starts.

## Check yourself
**Q:** The customer wants the AI to reach 99% accuracy before rollout. You have no baseline for the human
team. What do you do, and why?
**A:** I would run a blind retrospective audit. SMEs re-review a random, stratified sample of recent
human outputs against the same rubric we will use for the AI. The sample should be large enough that
the error-rate interval is narrow enough to set a bar. This changes the conversation from an
aspirational number to a fair comparison. If humans turn out to be at, say, 90% to 94%, a target of "at
least as good as humans, with no regression on high-severity case types" is defensible. The 99% figure
can stay as a stretch goal for low-risk categories. I would also record per-category human rates,
because the AI may beat humans on routine work and lose on rare, complex cases. Those rare cases are
where review should stay mandatory.

## Quiz
### Q: Why must the baseline be measured before deployment rather than reconstructed afterwards?
- [ ] Historical data is always deleted when new software is installed
- [x] Deployment changes the process and people's behaviour, and old logs rarely match the new metric definitions
- [ ] Statistical tests require the baseline to be collected first chronologically
- [ ] Regulators require baselines to be dated before a go-live
> Once the system is live, the old process is gone and behaviour has shifted, so it cannot be
> re-observed. Historical reports usually use different definitions, which makes a reconstructed
> baseline contestable.

### Q: A pilot shows a 20% drop in average handle time overall, but no individual ticket category improved. What is the most likely explanation?
- [ ] The confidence interval is too wide to tell
- [ ] The model improved only on the largest category
- [x] The pilot received a different mix of case types, with more easy cases, than the baseline
- [ ] Handle time should have been measured with medians instead of means
> If no segment improved but the total did, the weights changed: this is mix shift, the composition
> effect behind Simpson's paradox. Stratifying the baseline and re-weighting pilot results to the
> baseline mix exposes it.
> Medians would not fix a composition change.

### Q: Why record the standard deviation of a baseline metric, not just its mean?
- [ ] Executives prefer dashboards that show variability
- [ ] The standard deviation is needed to compute the ROI
- [ ] It replaces the need to stratify by case type
- [x] It determines how many post-launch cases are needed to detect a given improvement
> Required sample size scales with the variance divided by the square of the effect you want to detect.
> Without the spread, you cannot tell whether the pilot window has enough volume to distinguish a real
> change from noise.
