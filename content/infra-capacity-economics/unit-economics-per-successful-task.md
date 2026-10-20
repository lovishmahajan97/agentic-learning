---
id: unit-economics-per-successful-task
module: infra-capacity-economics
name: Unit economics per successful task
kind: metric
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - infra-capacity-economics/cost-modeling
  - infra-capacity-economics/cost-optimization
  - infra-capacity-economics/quality-latency-cost-optimization
  - agent-reliability/task-completion-rate
  - evals/task-specific-acceptance-criteria
  - agents/human-in-the-loop
  - scoping/success-metrics-kpis
---

## TL;DR
Cost per successful task divides everything a unit of work really costs (failed attempts, retries,
verification, human fallback and the damage from errors that slip through) by the number of tasks that
met an agreed acceptance bar. It replaces cost per call because a cheap model that fails often is
expensive once its failures are paid for, and it is the number you compare with the value of the task
to know whether the product makes money.

## Why it exists
Model providers bill per token, so dashboards naturally show cost per call or per request. That number
answers the wrong question. The business does not buy calls; it buys resolved tickets, extracted
invoices, merged pull requests, qualified leads. Between a call and a resolved task sit retries, tool
errors, verification passes, escalations to a human and, worst, wrong outputs that nobody caught and
that cost a refund, a compliance incident or a customer.

Optimizing cost per call therefore rewards the wrong design. Swapping to a smaller model halves the per-
call cost and can triple the cost per resolved task once escalations are counted. Agents make the gap
wider: a run that loops for 40 steps and then fails has consumed real money and produced nothing. A
second failure is definitional: "success" that is not measured against explicit acceptance criteria
becomes whatever makes the metric look good, such as "the model returned something". Unit economics per
successful task exists to put cost and outcome in one ratio, measured the same way every time, so that
model choices, retry policies and human-in-the-loop design can be compared on the only basis the
business cares about: what does one good outcome cost, and is it less than it is worth?

## How it works
### Define success first
Write down what counts as a successful task in terms the customer would accept: ticket resolved and
not reopened within a set window, extraction matching the source on all required fields, code change
passing tests and merged. Decide how success is measured: automated checks, sampled human review, or
a downstream signal. A metric without a fixed definition drifts toward whatever is easy to count.

### Count all the cost
For a period or a cohort of tasks, sum:
- model tokens for every attempt, including failed and abandoned ones, and reasoning tokens;
- non-model calls (retrieval, tools, guardrails, verifier or judge models);
- human time for review, escalation and correction, at a loaded hourly rate;
- the expected cost of undetected errors (rate of silent failure × cost per incident);
- amortized fixed costs if the comparison is about the whole product rather than a design change.

### Divide by successes
```
cost_per_successful_task = total_cost / tasks_meeting_acceptance
```
For reasoning about designs, a simple expected-value model helps. If each attempt costs c and succeeds
with probability p, and every failure is reliably detected and retried, the expected AI cost per success
is c/p. That ratio already shows why a model at 4× the price with 1.5× the success rate is not 4× worse.
Real systems add two terms that usually dominate: the share of failures handed to humans, and the share
of failures *not detected* at all.

### Compare with value
Value per task is what the business gains: the human handling cost avoided, revenue per conversion, or
time saved. Margin per task = value − fully loaded cost. Break-even success rate is the p at which that
margin reaches zero; it tells you how good the system must be before automation pays.

### Caveats that change the answer
Attempts are not independent: if a model fails on a hard input, retrying the same model often fails
again, so the c/p retry math is optimistic. Detection is not free or perfect: a verifier adds cost and
misses some failures. And the mix of tasks shifts over time, so recompute per task category, not one
global number.

## Example
A ticket-triage agent where a human handles anything the agent cannot (all figures illustrative): human
cost $4.00 per ticket, a verifier costing $0.005 catches 80% of agent failures, and a failure that slips
through costs $20 in rework and goodwill.

```python
def per_task(c, p, k=1, verify=0.005, detect=0.8, human=4.00, error=20.00):
    """Expected fully loaded cost of one task: up to k attempts; a caught failure is retried
    (then sent to a human after k); a missed failure costs `error`. Independence is optimistic."""
    cost, reach, solved, ai = 0.0, 1.0, 0.0, 0.0
    for _ in range(k):
        ai += reach * (c + verify)
        cost += reach * (1 - p) * (1 - detect) * error   # silent failure reaches the customer
        solved += reach * p
        reach *= (1 - p) * detect                         # caught failure: try again
    cost += ai + reach * human
    return cost, solved, ai / solved

for name, c, p in (("small", 0.02, 0.60), ("large", 0.08, 0.90)):
    for k in (1, 3):
        cost, solved, ai_per_win = per_task(c, p, k=k)
        print(f"{name} k={k}: ${cost:.3f}/task automated {solved:.1%} AI spend/success ${ai_per_win:.3f}")
# small k=1: $2.905/task automated 60.0% AI spend/success $0.042
# small k=3: $2.442/task automated 85.3% AI spend/success $0.042
# large k=1: $0.805/task automated 90.0% AI spend/success $0.094
# large k=3: $0.529/task automated 97.8% AI spend/success $0.094
```

On AI spend per success the small model looks more than twice as good. Fully loaded, it costs about
3.6× more per task (k=1), because its silent failures and escalations dominate. Retries help only with
*detected* failures, and every extra attempt is another chance for a failure to slip past the verifier:
for the small model, going from k=1 to k=3 cuts human escalation from $1.60 to about $0.13 per task but
*raises* the silent-error term from $1.60 to about $2.28, which now dominates its bill. That is why
improving detection is often worth more than adding retries, or even than improving the model.

## Trade-offs
- A stricter success definition makes the metric trustworthy, but lowers the headline number and costs
  review effort; a looser one flips into self-deception once customers start reopening tasks.
- Adding a verifier costs money on every task, but pays back whenever the cost of a silent failure is
  much larger than the verification cost; for low-stakes tasks it can be pure overhead.
- More retries raise automation rate, but each retry costs tokens and latency and helps only when
  failures are detected and not strongly correlated across attempts.
- Including amortized fixed costs gives the true product margin, but blurs comparisons between design
  options; use marginal cost to choose designs and fully loaded cost to price.

## Pitfalls
- Reporting cost per call as unit cost; the symptom is a "cheaper" model rollout followed by more
  escalations and a higher total support bill.
- Counting only successful runs' tokens; visible as unit cost that ignores agent runs that looped and
  failed, so finance totals never reconcile with the metric.
- Defining success as "completed without error"; the metric looks excellent while customer reopen rates
  or downstream correction volume quietly climb.
- Treating the retry math as independent; real success after retries stays well below the predicted
  rate because the same hard inputs keep failing.

## When to use
Use this metric whenever you compare models, prompts, retry policies or human-in-the-loop designs, and
whenever you price an AI feature or argue for its ROI. Compute it per task category and per tenant
rather than globally, track it weekly next to success rate and escalation rate, and validate the
success definition with sampled human review. It is less useful during early prototyping, before there
is a stable task definition. In interviews, asking "per successful what?" when someone quotes a cost
number is the mark of someone who has run an AI product.

## Role lens
- **Engineer:** Instruments each task end to end with an ID so every attempt, tool call, verifier
  result and escalation is attributed to one outcome.
- **Architect:** Chooses model tier, retry budget, verifier and human-fallback design by comparing
  fully loaded cost per successful task against task value.
- **FDE:** Agrees the success definition and the value per task with the customer, so the ROI case uses
  their numbers and survives their finance review.

## Check yourself
**Q:** Model X costs $0.01 per attempt with 70% success; model Y costs $0.05 with 92%. Failures go to a
human at $3. Which is cheaper per task, and what assumption would you challenge?
**A:** With one attempt and perfect detection, X costs 0.01 + 0.30 × 3 = $0.91 per task and Y costs
0.05 + 0.08 × 3 = $0.29, so Y is about three times cheaper despite the 5× price. The human fallback
dominates. I would challenge perfect detection: if some failures are not caught, the cost of each silent
error must be added, and that term usually favors the more accurate model further. I would also check
whether retrying X helps or whether its failures correlate on hard inputs, and compute the numbers per
ticket category.

## Quiz
### Q: Why is cost per API call a misleading unit cost for an AI feature?
- [ ] API calls are billed monthly rather than per call
- [x] It ignores failed attempts, retries, human fallback and undetected errors, which can dominate the cost of a good outcome
- [ ] Token prices change too frequently to measure per call
- [ ] It double-counts cached input tokens
> The business pays for outcomes. A cheaper call that fails more often moves cost into retries,
> escalations and error handling, so cost per successful task can rise while cost per call falls.

### Q: Each attempt costs c, succeeds with probability p, and failures are always detected and retried. What is the expected AI cost per success?
- [ ] c × p
- [x] c / p
- [ ] c / (1 − p)
- [ ] c + p
> The number of attempts until success is geometric with mean 1/p, so expected cost per success is
> c/p. c/(1 − p) inverts the wrong probability: it is the expected cost until the first *failure*, a
> common mix-up.

### Q: A verifier catches 80% of failures. Adding more retries mainly reduces which cost term?
- [ ] The cost of silent failures that reach the customer
- [x] The share of tasks escalated to a human after caught failures
- [ ] The per-attempt token cost
- [ ] The verifier's own cost per attempt
> Retries only act on failures the verifier detects, so they shift work from human escalation to
> automation. Missed failures never trigger a retry, and each extra attempt is another chance of a
> silent miss, so the silent-error term does not fall and can even grow; it needs better detection.
