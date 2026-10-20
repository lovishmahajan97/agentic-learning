---
id: slas-slos
module: reliability
name: SLAs / SLOs (and SLIs, error budgets)
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - reliability/high-availability
  - reliability/fmea
  - delivery-ownership/slas
  - delivery-ownership/incident-severity-and-escalation
  - observability/latency-percentiles
  - observability/monitoring
  - scoping/success-metrics-kpis
  - infra-capacity-economics/latency-budgets
sources:
  - Beyer et al., 2016 — Site Reliability Engineering
  - Beyer et al., 2018 — The Site Reliability Workbook
  - Hidalgo, 2020 — Implementing Service Level Objectives
---

## TL;DR
An SLI measures behavior users care about: the fraction of requests answered correctly within two
seconds. An SLO is the internal target for that SLI over a window (99.5 percent over 30 days). An SLA
is the external contract attaching consequences to a looser target. The gap between 100 percent and
the SLO is the error budget: reliability as a quantity you can spend on shipping. For AI systems the
hard part is choosing SLIs that capture "the answer was useful", not just "the request returned 200".

## Why it exists
Without explicit targets, reliability is argued by anecdote. Product wants features, operations wants
stability, every incident is a crisis and no incident is ever acceptable. "100 percent reliable" is not a
target: it is impossible, each additional nine costs roughly an order of magnitude more, and past a point
users cannot perceive the difference because their own network and devices fail more often than you do.
An SLO makes the trade explicit. Pick the number users actually need, measure it from their side, and
treat the remainder as a budget to be spent on velocity: deploys, model upgrades, experiments. When the
budget is gone, reliability work takes priority. That policy, not the number, is what changes behavior.

SLAs exist for a different reason. Enterprise customers need a promise with teeth before procurement
will sign, so the SLA states a target and the credit paid if it is missed. The internal SLO must be
stricter than the SLA, because the gap is the margin in which you detect and fix problems before they
become contract events; a team whose SLO equals its SLA has legal in every incident review.

AI systems add a problem the classic framework did not have to solve. The traditional availability SLI,
the share of non-5xx responses, is nearly meaningless for a model endpoint: a confident wrong answer in
800 milliseconds counts as a success. An SLO built only on that will stay green while customers churn.
Useful SLIs for an LLM product must cover latency in the shape users feel it, correctness or quality as
measured by evals, and often cost, and each of those can be broken by a vendor changing a model without
a single line of your code changing.

## How it works
### The three layers, precisely
An **SLI** is a ratio: good events divided by valid events. "Good" must be defined in one sentence that a
query can implement, for example status 2xx *and* total latency under 3 seconds *and* output passed
schema validation. "Valid" excludes what should not count, such as malformed client requests, but be
careful: excluding too much hides real failures. Measure as close to the user as you can, at the load
balancer or in the client, because the server cannot count requests it never received.

An **SLO** is a target plus a window: 99.9 percent of valid requests good over a rolling 30 days. Rolling
windows suit engineering decisions because a calendar window resets everyone's attention on the first
and exhausts by the twentieth; calendar windows remain common for SLAs and business reporting because
contracts and invoices are monthly. Many teams run the rolling window internally and report the calendar
one externally. A service usually carries several SLOs: availability, latency, and for AI
products a quality SLO.

An **SLA** is a subset of the SLOs, looser, legally reviewed, with defined remedies and exclusions
(scheduled maintenance, customer-caused load). A common shape is an internal SLO of 99.9 percent behind
an external SLA of 99.5 percent.

### Error budget arithmetic
Budget = (1 − SLO) × window. For 99.9 percent over 30 days: 30 × 24 × 60 = 43,200 minutes, times 0.001,
gives 43.2 minutes of full outage, or 1,000 bad requests per million. The whole table is just this
multiplication: 99 percent allows 7.2 hours per 30 days, 99.5 percent 3.6 hours, 99.9 percent 43.2
minutes, 99.95 percent 21.6 minutes, 99.99 percent 4.32 minutes. The last row is why 99.99 percent
requires automated failover: a human cannot be paged, understand the problem and act in four minutes.

Dependencies cap what you can promise. If your request path serially needs a provider with a 99.9
percent SLA and a vector store with 99.95 percent, the product of the two is about 99.85 percent before
your own code fails once. Your SLO cannot honestly exceed that ceiling without redundancy; an
independent fallback provider raises the provider term toward 1 − (0.001 × 0.001), though true
independence is rare and the figure is illustrative.

### Burn rate and alerting
Burn rate is how fast you are consuming the budget relative to the pace that would exhaust it exactly
at the end of the window. A burn rate of 1 spends the whole budget in 30 days; a burn rate of 10 spends
it in 3 days. Alerting on burn rate, rather than on a raw error-rate threshold, is the point: a
two-minute blip at 5 percent errors consumes a sliver of budget and should not page anyone, while a
persistent 0.3 percent error rate on a 99.9 percent SLO is a 3× burn that will breach the SLO in ten
days and should. The widely used multi-window scheme from the SRE Workbook pages when the burn rate over
the last hour exceeds 14.4 (that consumes 2 percent of a 30-day budget in an hour) or over six hours
exceeds 6 (5 percent of budget), and opens a ticket when it exceeds 1 over three days (10 percent). Each
long window is paired with a short one so the alert clears once the problem stops.

### The error budget policy
The numbers do nothing by themselves. The policy says what happens when the budget is spent: freeze
risky launches, require review for prompt and model changes, prioritize reliability work until the
budget recovers; and what happens when it is plentiful: ship faster, run chaos experiments, which spend
budget on purpose. Agreeing this in advance with product leadership is most of the work.

### SLIs for AI systems
- **Latency**, in the shape users feel: time to first token for streaming responses; total time per
  response; for agents, time to task completion, which can be minutes and needs its own target.
- **Availability** after resilience: the fraction of requests that complete without a provider or tool
  error once retries and fallbacks have had their chance.
- **Quality**: the fraction of a sampled slice of responses that pass automated evals (groundedness,
  schema, policy), with a smaller human-labeled sample as calibration. Sampling is used because
  evaluating every response costs as much as serving it. A quality SLO is the one most likely to move
  without a deploy, which is why model version pinning and evaluation gates exist.
- **Cost** per successful task, paired with quality so that neither can be gamed by sacrificing the other.
- **Side-effect correctness** for agents: actions reconciled against intent, such as tickets created
  equals tickets requested.

Note what vendor SLAs cover: usually availability only, with service credits. They do not promise
latency or quality, so your latency and quality SLOs must assume you absorb those risks yourself.

## Example
A worked month for an assistant with an SLO of 99.9 percent "good" responses over a rolling 30 days,
where good means 2xx, total latency under 4 seconds, and schema-valid output. Traffic is 20 million
requests per month, so the budget is 20,000 bad responses, equivalent to 43.2 minutes of total outage.
Average traffic is 20,000,000 ÷ 43,200 minutes, about 463 requests per minute.

On day 6 the provider degrades for 25 minutes and 60 percent of requests in that window fail or time
out. Bad responses: 25 × 463 × 0.6, about 6,945, which is 35 percent of the month's budget gone in 25
minutes. The burn rate during the incident is 0.6 ÷ 0.001 = 600, so the one-hour 14.4× page fires within
the first few minutes, which is exactly right. Afterwards 13,055 bad responses remain for 24 days.

On day 9 a prompt edit raises schema-validation failures from 0.02 percent to 0.15 percent, persistently.
That is a bad fraction of 0.0015 against an allowed 0.001: a burn rate of 1.5. Nothing is on fire, no
page fires, but at 463 × 0.0015 ≈ 0.69 bad responses per minute the remaining budget lasts 13,055 ÷ 0.69
≈ 18,800 minutes, about 13 days, so the SLO will be breached before the window closes. The three-day 1×
ticket alert catches it once the new 1.5× burn dominates its window, roughly two days in (around day
11), long before the budget is gone; the paging thresholds never fire, which is the point of having a
slow-burn tier. The error budget policy now applies: the prompt change is rolled back
or guarded with a repair step, and model upgrades are frozen until the rolling budget recovers. The
external SLA of 99.5 percent, allowing 100,000 bad responses, was never close to breach; the internal SLO
did its job of forcing action two weeks before a customer could have noticed.

## Trade-offs
- A tighter SLO pleases users but costs roughly an order of magnitude more per extra nine, in
  redundancy and in lost velocity from a smaller budget; it stops paying off once users' own client
  reliability is worse than yours.
- Measuring SLIs at the edge or client is truthful but includes noise you do not control; server-side
  measurement is clean but blind to the failures users feel. Use the edge for availability and the server
  for latency breakdowns.
- Request-based SLOs weight by traffic, so a ten-minute outage at peak costs more than one at night;
  time-based SLOs ("minutes with error rate under x") treat them equally. Request-based reflects user
  impact better; time-based is simpler to put in a contract.
- Quality SLIs via sampled automated evals are affordable but noisy and judge-dependent; full human
  review is accurate but too slow and expensive for a 30-day window. The sample size sets the confidence,
  so size it to the decision you need to make.
- Signing an SLA unlocks enterprise deals but makes every dependency's SLA your ceiling and converts
  internal misses into contract events. Worth it when revenue justifies buying the redundancy.

## Pitfalls
- Defining the SLI as "not a 5xx". The symptom: a green SLO dashboard during a quality incident while
  support tickets climb.
- Measuring inside the service. The symptom: the service reports 99.99 percent while the load balancer
  shows 2 percent 502s during every deploy.
- Setting a high-nines SLO on a low-traffic service. The symptom: at 2,000 requests a month a 99.9
  percent SLO allows two bad requests, so one flaky client exhausts the budget and burn-rate alerts
  flap; use a longer window, a looser target, or synthetic probes to supply enough events.
- Using mean latency as the SLI. The symptom: p50 is fine while a tenth of users wait 20 seconds; use
  the fraction of requests under a threshold instead.
- Calendar-month windows. The symptom: relaxed attention on the first, a burned budget by the twentieth,
  every month.
- Setting the SLO equal to the SLA. The symptom: legal joins engineering incident reviews and every
  internal miss becomes a credit negotiation.
- Having no error budget policy. The symptom: the budget is exhausted three months running and nothing
  about how the team ships has changed.
- Alerting on raw error-rate thresholds. The symptom: pages at three in the morning for two-minute blips
  that consumed a fraction of a percent of budget, while a slow burn that will breach the SLO goes
  unnoticed for a week.
- Assuming a vendor's SLA covers what your SLO needs. The symptom: the provider "met its SLA" during a
  ten-fold latency degradation that blew your latency SLO, because their SLA only promises availability.
- Setting a quality SLO without pinning model versions. The symptom: the eval pass rate drops with no
  deploy in the changelog, and the cause is a silent vendor model update.

## When to use
Every user-facing service needs at least one availability SLO and one latency SLO; an AI service adds a
quality SLO as soon as it has an eval it trusts, and a cost-per-task target soon after. Set the first
SLO from measured history, slightly below current performance, rather than from aspiration, then tighten
with evidence. Sign SLAs only for SLIs you can measure from the customer's vantage point and that your
dependencies' SLAs can support. Day to day this looks like checking budget remaining in sprint planning,
burn-rate alerts on the pager, and a monthly SLO review. In interviews, define the three terms crisply,
compute an error budget from a target and a window without hesitation, explain why burn-rate alerting
beats threshold alerting, and propose SLIs for an LLM product that go beyond uptime.

## Role lens
- **Engineer:** Instruments good and valid counters at the edge, builds the eval sampling pipeline for
  the quality SLI, implements multi-window burn-rate alerts, and checks the budget before risky changes.
- **Architect:** Chooses SLOs per service tier, derives them from dependency SLAs, decides where
  redundancy is required to meet them, and owns the error budget policy agreed with product.
- **FDE:** Translates a customer's SLA request into SLIs measurable inside their environment, explains why
  99.99 percent needs redundancy they have not bought, and builds the shared dashboard that governs
  hypercare.

## Check yourself
**Q:** A customer asks for a 99.95 percent availability SLA on your agent product. Your LLM provider
offers a 99.9 percent SLA, your vector database 99.95 percent, and you have no fallback provider. How
do you respond, and what would have to change?
**A:** Start with the ceiling. The request path needs both dependencies, so availability is bounded by
roughly 0.999 × 0.9995 ≈ 0.9985, about 99.85 percent, before any failure of your own code. Signing
99.95 percent, whose budget is 21.6 minutes per 30 days, would be a promise one provider incident breaks.
The honest response offers 99.5 percent contractually with an internal SLO of 99.9 percent now, and a
path to 99.95 percent priced to include what it costs: an independent fallback provider, which lifts the
provider term toward 1 − 0.001² if the two are truly independent, a vector-store replica, and a
definition of "available" that counts degraded-mode answers from cache or fallback. Add exclusions for
scheduled maintenance and customer-caused overload, and measure from their side of the network. Finally
clarify scope: an availability SLA says nothing about latency or answer quality, so propose separate
latency and quality SLOs, visible on a shared dashboard but not under penalty until both sides trust
the measurement.

## Quiz
### Q: What is the error budget for a 99.9 percent SLO over a 30-day window, expressed as full-outage time?
- [ ] 4.32 minutes
- [x] 43.2 minutes
- [ ] 7.2 hours
- [ ] 21.6 minutes
> 30 days is 43,200 minutes; 0.1 percent of that is 43.2 minutes. The 4.32-minute figure belongs to
> 99.99 percent, 21.6 minutes to 99.95 percent and 7.2 hours to 99 percent; the arithmetic is the same
> multiplication each time.

### Q: Why should the internal SLO be stricter than the external SLA?
- [ ] Because SLAs are measured over longer windows than SLOs
- [ ] Because customers expect the published number to be rounded up
- [x] The gap is the margin in which problems are detected and fixed before they become contract events with penalties
- [ ] Because vendors require it in their own terms of service
> If the SLO equals the SLA, every internal miss is already a breach and legal enters every incident
> review. A stricter SLO forces action while there is still budget left before the contractual line.

### Q: Why alert on error-budget burn rate rather than on a raw error-rate threshold?
- [ ] Because error rates cannot be computed in real time
- [x] Burn-rate alerts page only when the budget is genuinely threatened, catching both fast outages and slow persistent degradation
- [ ] Because burn rate is required for SLA reporting
- [ ] Because raw error rates are always noisier than burn rates
> A two-minute blip consumes a sliver of budget and should not wake anyone; a persistent 0.3 percent
> error rate on a 99.9 percent SLO burns at 3× and will breach within the window. Multi-window burn-rate
> alerts distinguish the two; a single threshold cannot.
