---
id: vendor-evaluation
module: strategy
name: Vendor evaluation
kind: practice
difficulty: core
est_minutes: 15
related:
  - system-design/model-selection
  - system-design/build-vs-buy
  - evals/golden-dataset
  - infra-capacity-economics/unit-economics-per-successful-task
  - infra-capacity-economics/api-provider-quotas-and-regional-limits
  - security-governance-compliance/vendor-exit-strategy
  - llmops-platform/model-lifecycle-and-deprecation-policy
  - deployment/security-questionnaires
---

## TL;DR
Vendor evaluation is choosing a model provider, vector database, eval tool or AI platform by testing
candidates on your own task and constraints rather than on demos and public leaderboards. The method:
hard gates first (data terms, residency, security, compliance), then a bake-off on your own labelled data
measuring quality, latency under your load and cost per successful task, then contract and exit terms.
It matters because AI vendors differ most on exactly the things a demo never shows.

## Why it exists
AI vendors are unusually hard to compare. Public benchmarks measure tasks that may look nothing like
yours, and some benchmark data has leaked into training sets, so a leaderboard gap may not transfer.
Demos are run on the vendor's chosen examples. Pricing is per token, per query, per seat or per
"credit", which makes headline prices incomparable until you convert them to cost per unit of your work.
And the decisive properties are often non-functional: whether your data is retained or used for training,
which regions process it, what rate limits you get, how much notice you get before a model is deprecated,
and how hard it is to leave.

The naive process, a feature checklist plus a demo plus the lowest list price, fails predictably. The
checklist rewards breadth over fit. The demo hides the failure modes on your messy inputs. The list price
ignores that a cheaper model needing two retries, longer prompts or more human review can cost more per
correct outcome. Teams that choose this way discover the gaps after integration, when switching is
expensive, and then live with a vendor that fails a security review or throttles them at peak.

Evaluation also recurs: model vendors ship new versions frequently, so the same harness should be
re-runnable when a new candidate appears, rather than a one-off procurement exercise.

## How it works
### 1. Write requirements as gates and weighted criteria
Separate **must-haves** (pass/fail) from **differentiators** (scored). Gates typically cover data use
(no training on your data, retention period, zero-retention options), data residency, security
attestations and the answers your security questionnaire needs, required deployment mode (API, VPC,
self-hosted), and compliance obligations. A vendor that fails a gate is out regardless of score,
*because* a weighted average lets a high quality score compensate for a disqualifying flaw. Fix the
weights for the differentiators **before** seeing results, so the scoring is not reverse-engineered to
justify a favourite.

### 2. Build a task-specific evaluation set
Use a few hundred real, representative, labelled examples from your workflow, including the hard and
ugly cases (scanned PDFs, long threads, other languages). Keep it private and never send it to vendors
for "tuning" before the test, *because* then you are measuring their preparation, not their product.

### 3. Run a controlled bake-off
Same harness, same prompts adapted minimally per vendor, same scoring. For each candidate measure:
- **Quality** on your metric (accuracy, faithfulness, pass rate on acceptance criteria).
- **Latency** at p50 and p95 at your expected concurrency, not single requests, *because* rate limits and
  queueing appear only under load.
- **Cost per successful task:** total cost (tokens, retries, fixed fees, human review of failures)
  divided by tasks completed correctly. This is the number that makes different pricing models comparable.
- **Operational fit:** quotas in your region, SLA terms, observability hooks, SDK quality, support.

### 4. Assess the vendor, not just the product
Financial and roadmap stability, model deprecation policy and notice period, how they communicate
changes, and whether a model version can be pinned. For a small vendor, ask what happens to your data
and service if they are acquired or shut down.

### 5. Price the exit before you enter
Estimate the cost of leaving: proprietary formats, embeddings that must be regenerated, prompts tuned to
one model, contractual minimums. Prefer vendors that fit behind your own interface (gateway, standard
APIs), *because* a low exit cost is what keeps the next negotiation honest.

### 6. Decide, document, and schedule re-evaluation
Record the scores, the gates and the reasoning in a decision record, and re-run the harness when a
material new option or price change appears.

## Example
Scoring three model providers for invoice field extraction. Quality and latency come from a bake-off on
400 labelled invoices; prices and figures are illustrative.

```python
vendors = {
    #       gates_ok, accuracy, p95_s, cost_per_call, review_cost_per_failure
    "A": (True,  0.96, 2.8, 0.010, 0.50),
    "B": (True,  0.91, 1.2, 0.002, 0.50),
    "C": (False, 0.97, 1.9, 0.006, 0.50),   # fails residency gate: data leaves the region
}
weights = {"cost": 0.5, "latency": 0.2, "accuracy": 0.3}   # fixed before the bake-off
P95_BUDGET = 3.0

def cost_per_success(acc, call, review):
    return (call + (1 - acc) * review) / acc   # failures need a human fix and still cost a call

rows = {}
for v, (ok, acc, p95, call, review) in vendors.items():
    if not ok or p95 > P95_BUDGET:
        print(v, "excluded by gate"); continue
    rows[v] = (acc, p95, cost_per_success(acc, call, review))

best_cost = min(r[2] for r in rows.values()); best_p95 = min(r[1] for r in rows.values())
for v, (acc, p95, cps) in rows.items():
    score = (weights["cost"] * best_cost / cps + weights["latency"] * best_p95 / p95
             + weights["accuracy"] * acc)
    print(f"{v}: acc={acc:.2f} p95={p95}s cost/success=${cps:.4f} score={score:.3f}")
# C excluded by gate
# A: acc=0.96 p95=2.8s cost/success=$0.0313 score=0.874
# B: acc=0.91 p95=1.2s cost/success=$0.0516 score=0.776
```

B's call price is five times lower, but its extra 5 points of errors each need a 50-cent human fix, so
its cost per successful extraction is higher than A's. C has the best accuracy and is out anyway.

## Trade-offs
- A rigorous bake-off on your data gives decisions that transfer to production, but costs weeks of
  labelling and engineering; for low-risk, easily reversible choices a lighter pilot is enough.
- Multi-vendor setups behind a gateway reduce lock-in and outage risk, but multiply security reviews,
  contracts and prompt maintenance; they pay off when volume or criticality is high.
- Choosing the top-quality vendor maximises accuracy, but may cost more per call; it flips when error
  costs are low and a cheaper model's failures are cheap to catch.
- Long-term commitments buy discounts, but raise exit cost in a market where prices often fall and
  better models appear; keep terms short when the category is moving fast.

## Pitfalls
- Trusting public leaderboards for a domain task; the symptom is a vendor that ranked first but loses
  on your own invoices or tickets.
- Comparing list prices instead of cost per successful task; the cheap option turns out expensive once
  retries and human correction are counted.
- Testing latency with single requests; production hits rate limits or queueing at peak and p95 latency
  breaks the user-facing budget.
- Setting weights after seeing results; the decision record reads like a justification, and the next
  audit or new stakeholder challenges it.
- Ignoring deprecation policy; a pinned model is retired with short notice and the team must re-validate
  prompts and evals in a hurry.

## When to use
Run a structured evaluation whenever the choice is expensive to reverse or touches sensitive data:
primary model provider, vector store, platform vendors, anything inside the customer's data boundary.
Use a light version (gates plus a small bake-off) for tools that are cheap to swap. Measure quality on
your own set, p95 latency at load, cost per successful task, and exit cost. Re-run when new models ship
or prices change. In interviews, the signal is separating gates from scores, insisting on your own data,
and converting pricing to cost per correct outcome.

## Role lens
- **Engineer:** Builds the reusable bake-off harness, runs candidates on the same labelled set, and
  reports quality, p95 latency and cost per success in one table.
- **Architect:** Defines the gates and weights, owns the decision record, and makes sure the chosen
  vendor sits behind an interface that keeps exit cost low.
- **FDE:** Evaluates vendors against the customer's own constraints (their region, procurement rules,
  security questionnaire) and data, not the vendor's demo set.

## Check yourself
**Q:** Vendor X's model is cheaper per token than Vendor Y's and scores slightly lower on your eval.
Procurement wants X. What do you check before agreeing?
**A:** First, that X passes every gate: data use and retention terms, residency, security answers,
deployment mode. Then convert both to cost per successful task: X may need longer prompts, more retries or
more human correction, and if failures cost real review time, a small accuracy gap can outweigh a
large token discount. I would also check p95 latency and rate limits at our concurrency, the deprecation
policy and version pinning, and the exit cost. If X still wins on cost per success within the latency
budget, X is the right call; if not, the table makes the case to procurement in their own terms.

## Quiz
### Q: Why are must-have requirements handled as gates instead of heavily weighted criteria?
- [ ] Gates are faster to evaluate than weighted scores
- [x] A weighted average lets strong scores elsewhere compensate for a disqualifying flaw
- [ ] Vendors prefer pass/fail requirements in procurement
- [ ] Weighted scoring cannot handle more than five criteria
> A vendor that violates residency or trains on your data is unacceptable whatever its accuracy. Any
> finite weight lets enough quality offset the violation, so the requirement must be a hard filter.

### Q: Which metric best compares vendors that use different pricing models?
- [ ] Price per million input tokens
- [ ] Monthly platform fee
- [x] Total cost divided by the number of tasks completed correctly
- [ ] Rank on a public benchmark divided by price
> Cost per successful task folds in token prices, retries, fixed fees and the cost of handling failures,
> so a per-token discount and a per-seat plan become directly comparable on the outcome you pay for.

### Q: Why should latency in a bake-off be measured at expected concurrency?
- [ ] Single-request latency is always higher than under load
- [ ] Vendors charge less for concurrent requests
- [x] Rate limits and queueing only appear under load, and they drive p95 latency
- [ ] Concurrency improves accuracy through batching
> One request at a time hides throttling and queueing delays. The user-facing latency budget is usually a
> tail percentile, so it must be measured under production-like concurrency.
