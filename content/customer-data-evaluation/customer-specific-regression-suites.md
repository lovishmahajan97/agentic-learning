---
id: customer-specific-regression-suites
module: customer-data-evaluation
name: Customer-specific regression suites
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - evals/regression-tests
  - evals/golden-dataset
  - customer-data-evaluation/golden-dataset-creation-with-client-smes
  - customer-data-evaluation/acceptance-testing-against-customer-kpis
  - llmops-platform/evaluation-gates-for-production-releases
  - llmops-platform/ci-cd-for-prompts-and-models
  - enterprise-integration/tenant-specific-configuration-and-feature-flags
  - customer-data-evaluation/shadow-mode-validation
---

## TL;DR
A customer-specific regression suite is a frozen set of that customer's own cases, run with that
customer's configuration, every time a shared prompt, model or pipeline changes. It exists because a
change can raise the global eval score while breaking one customer's documents, jargon or contract
rules, and averages hide it. Each suite is gated on its own result, compared case by case against the
current production version, not against a global threshold.

## Why it exists
Once one product serves many customers, every shared component (base prompt, model version, retriever,
parser) is a change to all of them at once. The team's global eval set is usually a blend of cases from
many sources, and a blend answers "did we get better on average?" Customers never experience the
average. They experience their own traffic: their invoice layout, their label taxonomy, their rule that
amounts are always written with two decimals.

The naive approach is one big golden set with some cases from each customer. It fails through dilution.
If customer A contributes 120 of 3,000 cases (4%) and a change drops A from 92% to 80% pass, the
global score moves by about half a point, which is inside the run-to-run noise of most LLM evals. The
release ships, and the first signal is an angry email from A's operations lead. The second naive
approach, "re-run the customer's acceptance test before each release", fails differently: acceptance
tests were written once, for go-live, and do not contain the bugs found since. The bugs a customer has
already reported are the ones they will notice first if they come back, so they are the most valuable
tests you own.

A per-customer suite fixes both: results are reported and gated per customer, so a regression for one
customer cannot be averaged away by gains for others, and the suite grows with every fixed incident.

## How it works
### What goes into a suite
A suite is assembled from four sources, each for a different reason:
1. **Acceptance cases.** The cases that defined "done" at go-live (see acceptance testing against KPIs).
   They protect the contractual baseline.
2. **Fixed incidents.** Every production failure that was diagnosed and fixed becomes a case, *after*
   the fix, with the assertion that would have caught it. This is the "every bug becomes a test" rule,
   and it is why the suite gets more valuable over time.
3. **Contract rules.** Format, terminology, refusal and escalation behaviours the customer requires
   ("never state a settlement amount", "dates as DD/MM/YYYY"). These are often deterministic checks.
4. **Sampled traffic.** A small stratified sample of recent real inputs, so the suite does not drift
   away from what the customer actually sends.

### What a case records
Each case stores the input, the **customer configuration it must run under** (tenant flags, prompt
version, retrieval index snapshot or fixed context, tool stubs), the assertion type (exact match,
schema check, field-level comparison, rubric or LLM judge), a **severity tier** (blocking or tracked),
provenance (which ticket or SME session it came from), and an owner. Configuration is part of the case
because a regression that only appears with A's feature flags is invisible if you test with defaults.
Retrieval is frozen (a snapshot or the captured context) because otherwise a failure could be caused by
new documents rather than by your change, and you could not tell which.

### How it is run and judged
On every change to a shared component, the harness runs each customer's suite under that customer's
config, for both the **candidate** and the **current production version**. Comparison is paired, case by
case, because the same cases are run on both sides: what matters is which cases flipped from pass to
fail and which flipped the other way. Paired comparison is far more sensitive than comparing two pass
rates, because case difficulty cancels out. The gate has two parts:
- **Blocking cases must pass.** A contract-rule or previously-fixed-incident case that fails blocks the
  release for that customer, regardless of averages.
- **Scored cases must not regress beyond noise.** Count pass-to-fail and fail-to-pass flips and test
  whether the imbalance is larger than chance (McNemar's test on the discordant pairs). Measure noise by
  running production against itself a few times; cases that flip between identical runs are flaky and
  should be fixed or repeated, not gated on.

### Cost and cadence
Cost is customers × cases × repetitions, so suites are tiered: a small smoke subset per customer on every
pull request, the full suite nightly and before release. A failing customer can be held on the old
version through per-tenant routing while the rest roll forward, which is only possible if config and
version are per tenant.

### Data handling
Suites contain customer data. They are stored and run under that customer's data agreement and region,
never shared with other tenants' suites or used for training without permission, and deleted or
returned when the contract ends.

## Example
A shared extraction prompt change lifts the global score from 91.2% to 92.0%. Customer A's 120-case
suite tells a different story. The paired comparison below counts flips and computes an exact McNemar
p-value from the discordant pairs.

```python
from math import comb

def mcnemar_exact(b, c):
    """b = pass->fail flips, c = fail->pass flips. Two-sided exact binomial test."""
    n, k = b + c, min(b, c)
    tail = sum(comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)

def gate(baseline, candidate, blocking_ids):
    """baseline/candidate: {case_id: bool passed}. Returns (ok, report)."""
    broken_blockers = [cid for cid in blocking_ids if not candidate[cid]]
    b = sum(baseline[k] and not candidate[k] for k in baseline)
    c = sum(not baseline[k] and candidate[k] for k in baseline)
    p = mcnemar_exact(b, c)
    ok = not broken_blockers and not (b > c and p < 0.05)
    return ok, {"pass_to_fail": b, "fail_to_pass": c, "p": round(p, 4),
                "broken_blockers": broken_blockers}

# Customer A: 14 cases broke, 3 improved, one broken case is a past incident (blocking).
print(mcnemar_exact(14, 3))   # 2 * (1 + 17 + 136 + 680) / 2**17 = 0.0127
```

With 14 pass-to-fail and 3 fail-to-pass, p ≈ 0.013: the drop is real, not noise, even though A's pass
rate only moved from, say, 90% to 81% and the global number went up. One of the broken cases was a
blocking past-incident case, so the gate fails for A on two counts. The fix is to hold A on the old
prompt version through per-tenant routing, inspect the 14 flips (here, multi-currency invoices), and
re-run before promoting.

## Trade-offs
- Per-customer gating catches regressions averages hide, but cost and run time grow with the number of
  customers; tiered smoke-versus-full runs keep pull requests fast until the customer count is large.
- Freezing retrieval context makes failures attributable to your change, but stops the suite from
  catching regressions caused by index or ingestion changes; run a separate live-index suite for those.
- Gating on every customer slows shared releases; per-tenant version pinning lets most customers move
  forward, at the price of maintaining several live versions for a while.
- LLM-judge assertions cover open-ended outputs that exact checks cannot, but add their own noise; prefer
  deterministic checks whenever the customer's requirement can be stated as a rule.

## Pitfalls
- Running every customer's cases with default configuration; the symptom is a green suite followed by a
  regression that only occurs under one tenant's feature flags or prompt overrides.
- Comparing pass rates instead of paired flips; the symptom is releases blocked by noise one week and
  real regressions waved through the next, because two independent rates are too noisy at 100-case scale.
- Never removing or re-reviewing cases; the symptom is a suite full of expectations the customer no
  longer holds, and engineers learning to ignore failures as "probably stale".
- Adding the incident case before the fix is verified, or with the wrong expected output; the symptom is
  a blocking case that fails on every candidate and gets silently downgraded to tracked.
- Treating customer suites as shared fixtures; the symptom is one customer's documents appearing in
  another customer's test logs or in a training set, which is a data-handling breach.

## When to use
Build a per-customer suite as soon as a second customer runs on shared components, and start it on day
one with the acceptance cases. Grow it with every fixed incident. It matters most when customers differ
in document formats, terminology or contract rules, and when shared prompts or models change often. With
one customer, your global regression set already is the customer suite. Measure flips per customer,
blocking-case failures and flaky-case rate. In interviews, the strong signal is explaining why averages
hide per-customer regressions and why the comparison must be paired against production.

## Role lens
- **Engineer:** Builds the harness that runs each suite under its tenant config, freezes retrieval
  context, computes paired flips and wires the per-customer gate into CI.
- **Architect:** Makes configuration and model version per tenant so a failing customer can be held back,
  and sets the data boundary for where customer suites live.
- **FDE:** Converts every fixed customer incident into a blocking case with the customer's SME, and uses
  the suite results to tell the customer exactly what a release changes for them.

## Check yourself
**Q:** A shared prompt change improves the global eval by 1.5 points. One customer's 80-case suite shows
6 cases newly failing and 2 newly passing. Should you ship, and to whom?
**A:** Look at the paired flips, not the averages. Six versus two on 8 discordant pairs gives an exact
McNemar p of about 0.29, so on its own that imbalance is not clearly beyond noise; I would check the
production-versus-production flake rate for that suite. But the type of case matters more: if any of the
six is a blocking contract rule or a past incident, that customer is held on the old version through
per-tenant routing while everyone else gets the change. I would then read the six failures to see if
they share a cause, fix or add a customer-scoped override, and re-run before promoting that tenant.

## Quiz
### Q: Why are customer regression suites compared case by case against production rather than against a pass-rate threshold?
- [ ] Thresholds are illegal to set in customer contracts
- [x] Paired comparison cancels out case difficulty, so flips reveal real regressions that noisy rates hide
- [ ] Production outputs are always correct, so they serve as the ground truth labels
- [ ] Pass-rate thresholds cannot be computed for LLM judges
> Running the same cases on both versions turns the question into which cases flipped, which is much more
> sensitive than comparing two noisy rates. Production is not ground truth; it is the paired baseline.

### Q: Which source of cases most directly prevents a customer from seeing the same bug twice?
- [ ] A random sample of the global eval set
- [ ] Synthetic cases generated from the product documentation
- [x] Each fixed production incident added as a case with the assertion that would have caught it
- [ ] The acceptance tests written at go-live
> Acceptance tests predate the bugs found in production. Turning each fixed incident into a blocking
> case guards exactly the failures the customer has already noticed and will check first.

### Q: Why should each case store the customer's configuration and a frozen retrieval context?
- [ ] To reduce the token cost of running the suite
- [ ] Because LLM judges require a configuration object to score outputs
- [ ] So the suite can be reused for other customers with similar documents
- [x] So failures reproduce under that tenant's real setup and are attributable to the change being tested
> Bugs often appear only under one tenant's flags or prompt overrides, and live retrieval adds changes
> unrelated to the release. Pinning both makes a flip mean that your change caused it.
