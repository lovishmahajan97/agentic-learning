---
id: finops
module: infra-capacity-economics
name: FinOps
kind: practice
difficulty: core
est_minutes: 14
status: typed-verified
lesson_type: decision
agent_relevance: useful
related:
  - observability/token-cost-tracking
  - infra-capacity-economics/cost-allocation-by-tenant-and-feature
  - infra-capacity-economics/unit-economics-per-successful-task
  - infra-capacity-economics/cost-optimization
  - infra-capacity-economics/tco
  - system-design/ai-gateway
  - infra-capacity-economics/cost-modeling
sources:
  - Official docs: FinOps Foundation (FinOps Framework)
  - Storment & Fuller, 2019 — Cloud FinOps
---

## TL;DR
FinOps makes agent spend visible, attributed and owned by the teams that cause it: meter every model call
at the gateway, tag it with run, tenant and feature, track cost per successful run, enforce step and
token budgets, and review the loop weekly.

## For agent builders
- **Why it matters for agents.** One agent run is many model calls with a growing context, and a stuck
  loop or a retrieval change can multiply the bill overnight with no code reviewer thinking about money.
  Without per-run metering you cannot tell growth from a runaway loop.
- **Where it shows up in your stack.** An AI gateway or SDK wrapper around every model call, the provider
  response usage fields (input, cached and output tokens), trace tools that show cost per run, a
  warehouse table of usage events, and the max-steps setting of your agent framework or loop.
- **What you control.** Tags on every call (run id, tenant, feature, prompt version), per-run step and
  spend limits enforced in code, separate keys for evals and development, and the cost estimate written
  before a new agent ships.
- **What you can skip.** Procurement work (committed-use contracts, reservations) and FOCUS billing
  normalization unless you own the platform budget; know they exist and who owns them.

## Decision case
### Scenario
A B2B company has six product teams running four agents on two LLM providers. All figures are
illustrative; the stuck-run costs reuse the per-run meter example below.
- **Spend:** the invoice is $62,000 a month, but metered events explain only $48,360, so $13,640 (22%) is
  unallocated.
- **Incident:** over one weekend about 385 support-agent runs looped on a failing tool to the 100-step
  ceiling, at about $23.36 each, roughly $9,000.
- **The CFO's constraints:** no single incident above $2,000; unallocated spend at or below 5% (about
  $3,100 at this bill) within one quarter; no customer-facing outage caused by a cost control.
- **Team:** one platform engineer for six weeks.
- **Latency:** any new hop must add at most 20 ms of p95 per call, the agents' own budget rule.

### Options
| Option | How it works | Bounds a runaway loop? | Customer outage risk | Attribution | Effort |
|---|---|---|---|---|---|
| A. Hard monthly caps per key | spend limits in each provider console | bounds the month, not the run: a loop burns until the cap | high: a tripped cap takes the agent down | unchanged, 22% unallocated | days |
| B. Gateway, tags, run limits, forecasts | sole egress with mandatory tags; per-run step and spend limits in each agent loop; two-window forecast alerts; showback; hard caps only on non-production keys | yes: 385 stuck runs × $0.56 ≈ $216 | low: a stopped run returns "could not finish" | per tenant, feature, run; target 5% or less | about six weeks |
| C. B plus chargeback now | as B, debiting team budgets from month one | yes | low | as B, but disputed while 22% is unallocated | six weeks plus finance work |
| D. Per-team keys, monthly review | one provider key per team, invoice read monthly | no, found a month later | none | per team only, no tenant, feature or run | days |

### Decision
Choose **B**, and start chargeback only after unallocated spend stays at or below 5% for two consecutive
months.
- **It is the only option that bounds a single run:** the gateway sees calls, so the step and spend limit
  must live in the loop; at `max_usd=0.50` the weekend incident costs about $216, under $2,000.
- **It meets the outage constraint:** production keys get forecast alerts and rate limits, not hard caps.
- **It meets the attribution target:** mandatory tags plus monthly reconciliation make the 22% gap a
  tracked KPI and expose the bypass paths, typically eval jobs and scripts with personal keys.
- **A and D are fast but fail the constraints:** A trades a runaway bill for an outage; D sees the
  incident a month late.
- **C is premature:** chargeback on numbers with a 22% hole produces arguments, not decisions.

**What would change it:**
- **The gateway adds more than 20 ms of p95 in a load test:** use an in-process SDK wrapper with the same
  tags and limits.
- **A team needs a provider feature the gateway cannot pass through:** allow a direct key with its own
  tags, a hard cap and an owner.
- **A bill of a few hundred dollars:** tagged events and a daily report are enough; skip the gateway.

### Trade-offs accepted
- **One more hop and a single point of failure:** monitor gateway p95 and error rate, and keep a tested
  fail-open path for production traffic.
- **Legitimate long runs may be stopped:** set `max_usd` above a high percentile of successful-run cost,
  and track the share of runs stopped by budget against run success.
- **Call-site discipline:** quarantine untagged calls and report the unallocated share weekly.
- **Delayed chargeback accountability:** showback carries the first quarter.

### Review rubric
- Quantifies the incident and shows the chosen control bounds it per run, with the arithmetic.
- Separates production from non-production controls and explains why hard caps sit only on the latter.
- Sets a measurable attribution target and the reconciliation that tracks it.
- Sequences showback before chargeback with an explicit entry condition.
- Derives run limits from the cost distribution of successful runs, not a guess.
- States the latency or capability threshold that would change the enforcement point.

## Why it exists
- **Cloud broke purchase-order budgeting.** Traditional budgets assume finance approves spend in advance;
  in the cloud a line in a config file decides spend, and the invoice arrives a month later.
- **LLM APIs break it harder.** Spend is per token, so a prompt edit, a retrieval change that doubles
  context, a retrying agent loop or a traffic spike can move the bill by a multiple with no procurement
  event and no code reviewer thinking about money.
- **The invoice says nothing useful.** It is a single line, "API usage", with no product, customer, feature,
  agent or team. Finance cannot allocate it, engineering gets no feedback, and the two sides meet only when
  the number is already alarming.
- **The naive responses each fail.** Approval gates for every model or key slow delivery and get bypassed
  with personal keys, worsening attribution. A one-time "cost project" saves a slice, then the bill climbs
  back because nothing structural changed. Hard spend caps protect the budget by taking the feature down
  for customers at month end.
- **What works is what worked for latency and reliability:** timely, attributed data in front of the
  people making decisions, clear ownership, and a loop that runs every week rather than once a year. Cost
  becomes an engineering signal like latency rather than a quarterly surprise.

## How it works
### The loop: inform, optimize, operate
The FinOps Foundation frames the practice as a cycle:

| Phase | Meaning |
|---|---|
| *Inform* | see and allocate spend accurately and quickly |
| *Optimize* | act on it, through both usage and rate levers |
| *Operate* | make the first two continuous: budgets, forecasts, reviews, owners and policy |

It is a loop *because* costs drift: models are updated, prices change, agents and tools ship weekly and
traffic mix moves. A snapshot goes stale in a month.

### Inform: meter at the point of call
1. **Choose one chokepoint.** Provider invoices lack your context, so attribution happens in your own
   code, at the single point every call passes through: an AI gateway or an SDK wrapper that the agent
   loop, the judge and every script must use.
2. **Record one event per call:** model; input, output and cached token counts; latency; tenant; feature;
   environment; prompt version; and a request id that joins to your traces. For agents add the run id and
   step number, so cost rolls up per run.
3. **Price at record time from a versioned price table,** and store the price used, *because* prices change
   and a cost history that silently re-prices itself is useless for trend analysis.
4. **Take token counts from the provider's response usage fields,** not your own tokenizer estimate,
   *because* tokenizers differ between models and estimates drift.
   - **Read each provider's fields carefully:** OpenAI's `prompt_tokens` already includes the cached
     tokens reported in `prompt_tokens_details.cached_tokens`, while Anthropic reports
     `cache_read_input_tokens` and `cache_creation_input_tokens` separately from `input_tokens`.
     Map both into the same uncached, cached and cache-write columns, or cached tokens get billed twice.
5. **Send events to the warehouse** where every other business metric lives.
6. **Reconcile monthly against the invoice.** The gap is a data-quality KPI; a growing one means a call
   path is bypassing the gateway or a price in the table is stale.
7. **Normalize billing data** once GPU, API and SaaS spend must be read together; that is what the FinOps
   Foundation's FOCUS specification is for.

### Keys, tags, showback and chargeback
- **The root cause of unattributable spend is one shared API key per company.** The gateway is the
  structural fix.
- **Per-team or per-feature keys plus mandatory tags** make unallocated spend visible as a bucket to drive
  toward zero.
- **Start with *showback*:** show each team its own spend.
- **Move to *chargeback*** (actually debiting internal budgets) only once allocation is trusted; chargeback
  on shaky numbers produces arguments about the numbers instead of decisions about the spend.

### Shared costs: allocate by a driver, not evenly
Not every dollar has a natural tag: a self-hosted GPU pool serves many agents; an embedding index, an
eval suite and the platform team serve everyone; a one-year reservation is paid upfront.

| Shared cost | Allocation driver |
|---|---|
| Self-hosted GPU pool | GPU-seconds or tokens served per feature; decode tokens weigh more than prefill tokens, because decode generates them one at a time |
| Reservations | amortized monthly over the term, not booked in the month they were paid |
| Platform team and tooling | a stated proxy such as share of requests |

- **Splitting evenly is the tempting shortcut and it is wrong,** *because* it subsidizes the heavy users
  with the light ones, hiding exactly the unprofitable tenant the exercise was meant to find.
- **Write the rule down.** Disputes about allocation are disputes about the rule, and a written rule ends
  them.

### Unit economics: telling growth from waste
- **Total spend is the wrong headline.** Ask whether cost per unit of value moved: cost per successful task
  (for agents, per successful run), per active user, per document processed, gross margin per tenant.
- **Growth vs regression:** a bill that doubled while cost per task stayed flat is growth; a bill that
  doubled on flat volume is a regression.
- **Only the unit view distinguishes them,** and only the unit view lets product price the feature.

### Optimize: usage levers and rate levers are different jobs
| | Usage optimization | Rate optimization |
|---|---|---|
| Goal | consume fewer units | pay less per unit |
| Levers | prompt trimming, prompt and response caching, routing cheap models first, output caps, step and token budgets on agents, retiring idle GPU replicas | committed-use and provisioned throughput discounts, reserved GPUs, negotiated enterprise pricing, moving offline work to discounted batch endpoints (same tokens, lower price) |
| Owner | engineering | procurement |

Keep them separate, because they belong to different people, move on different timescales, and mixing
them hides which one is actually working.

### Operate: budgets, anomalies, forecasts, owners
- **Budget per feature and team, alerting on *forecast*** (month-to-date burn rate projected to month end),
  not just on the threshold, because by the time a threshold fires the money is spent.
- **Forecast from the *recent* daily rate too** (say, the trailing three days), because a step change on
  day eight is diluted by seven normal days in the month-to-date average and shows in full in the recent
  rate.
- **Detect anomalies daily, per dimension.** A feature whose tokens per task tripled overnight is a bug, not
  a trend.
- **Name an owner** who reviews each cost centre weekly.
- **Put guardrails in the gateway:** per-key spend limits, model allowlists, default output caps.
- **Put guardrails in the agent loop:** a max-steps and max-spend limit per run, because a gateway sees
  calls, not runs.
- **Estimate cost in design review:** expected tokens per task times expected volume, so cost is discussed
  before the code exists.
- **Mature gradually.** The FinOps Foundation describes it as crawl, walk, run: a daily report first, then
  budgets and showback, then forecasts, anomaly detection and chargeback.

### What is specific to AI and agent spend
- **Tokens are driven by prompt design,** so cost is a code-review concern.
- **Agents multiply spend:** a ten-step loop is ten calls, and context grows with every step.
- **Evaluation is real spend:** a large eval suite run against a judge model on every pull request can
  rival production traffic.
- **Cached tokens are priced differently,** so cache hit rate is a cost KPI; a stable agent prefix is a
  FinOps decision.
- **For self-hosted models the waste metric is GPU idle time,** which belongs on the same dashboard as token
  spend.

## Diagram
Every call passes one gateway that tags and prices it; the warehouse feeds reconciliation, unit-cost
views and alerts, and owners close the loop with usage or rate changes to the agent.

```mermaid
flowchart TD
    A["App or agent call"] --> G["AI gateway: key, tags, limits"]
    G --> P["Provider or GPU pool"]
    G --> E["Usage event with price version"]
    E --> W[("Warehouse")]
    I["Provider invoice"] -->|"monthly"| R["Reconcile: gap is a KPI"]
    W --> R
    W --> U["Showback and unit cost"]
    W --> F["Forecasts and anomaly alerts"]
    U --> O["Owner weekly review"]
    F --> O
    O -->|"usage or rate lever"| A
```

## Examples
### Gateway-side recorder with a burn-rate review
A minimal gateway-side recorder and a daily review that forecasts on burn rate. Prices are placeholders;
in a real system they are loaded from the provider's current price list and versioned.

```python
import time, uuid
from collections import defaultdict

PRICES = {  # USD per 1M tokens, illustrative; "in" = uncached input tokens
    "large-model": {"in": 3.00, "out": 15.00, "cached_in": 0.30},
    "small-model": {"in": 0.15, "out": 0.60,  "cached_in": 0.015},
}
EVENTS = []

def record_call(model, usage, *, tenant, feature, env, prompt_version):
    p = PRICES[model]
    cost = (usage["in"] * p["in"] + usage["out"] * p["out"] + usage["cached_in"] * p["cached_in"]) / 1e6
    event = {"ts": time.time(), "id": str(uuid.uuid4()), "model": model, **usage, "cost_usd": cost,
             "tenant": tenant, "feature": feature, "env": env, "prompt_version": prompt_version,
             "price_version": "2026-10"}   # store the price used so history stays reproducible
    EVENTS.append(event)
    return event

def daily_review(events, budgets_usd, day_of_month, days_in_month=30):
    spend = defaultdict(float)
    for e in events:
        spend[e["feature"]] += e["cost_usd"]
    for feature, budget in budgets_usd.items():
        mtd = spend[feature]
        forecast = mtd / day_of_month * days_in_month        # burn-rate forecast, not a threshold
        flag = "OVER-FORECAST" if forecast > budget else "ok"
        print(f"{feature:<10} mtd=${mtd:8.2f}  forecast=${forecast:8.2f}  budget=${budget:6.0f}  {flag}")

# Illustrative traffic after a retrieval change doubled the context sent by 'search'
for _ in range(250_000):
    record_call("small-model", {"in": 2400, "out": 150, "cached_in": 800},
                tenant="acme", feature="search", env="prod", prompt_version="v12")
for _ in range(50_000):
    record_call("large-model", {"in": 1200, "out": 300, "cached_in": 0},
                tenant="acme", feature="summarize", env="prod", prompt_version="v3")
daily_review(EVENTS, {"search": 300, "summarize": 1500}, day_of_month=10)
# search     mtd=$  115.50  forecast=$  346.50  budget=$   300  OVER-FORECAST
# summarize  mtd=$  405.00  forecast=$ 1215.00  budget=$  1500  ok
```

- **The first line is the point.** On day ten search has spent only $115 of its $300 budget, so a
  threshold alert would stay silent for two more weeks; the burn-rate forecast flags it today.
- **Tags make the diagnosis one query.** Every event carries `prompt_version` and token counts, so tokens
  per call by prompt version shows that `v12` doubled input tokens. The fix is a prompt rollback, not a
  budget increase.
- **The simulation flatters the month-to-date forecast.** It runs all ten days at the new rate. If `v12`
  shipped on day eight, the average would mix in seven cheaper days and understate the overrun; a
  trailing-days forecast shows it in full (a later example).
- **Where the money is:** each search call costs about $0.00046, and 372 of its 462 micro-dollars are input
  tokens, so context size, not output length, is the lever for that feature.

### Agent scenario: per-run metering with step and spend limits
A gateway sees calls; only the agent loop knows a run. This meter tags each step with the run id and stops
a stuck loop. Prices are illustrative.

```python
PRICE = {"in": 3.00, "out": 15.00, "cached_in": 0.30}   # USD per 1M tokens, illustrative

class BudgetExceeded(Exception):
    pass

class RunMeter:
    """One per agent run: tags every step with run_id and enforces step and spend limits."""
    def __init__(self, run_id, tenant, feature, max_steps=15, max_usd=0.50):
        self.run_id, self.tenant, self.feature = run_id, tenant, feature
        self.max_steps, self.max_usd = max_steps, max_usd
        self.events, self.spent = [], 0.0

    def record_step(self, usage):
        cost = sum(usage[k] * PRICE[k] for k in PRICE) / 1e6
        self.spent += cost
        self.events.append({"run_id": self.run_id, "tenant": self.tenant, "feature": self.feature,
                            "step": len(self.events) + 1, **usage, "cost_usd": cost})
        if len(self.events) >= self.max_steps or self.spent >= self.max_usd:
            raise BudgetExceeded(f"{self.run_id}: {len(self.events)} steps, ${self.spent:.2f}")

# A stuck loop: the agent retries a failing tool; the transcript (~1.5k tokens a step) is re-sent uncached
meter = RunMeter("run-42", tenant="acme", feature="support-agent")
try:
    for step in range(100):
        meter.record_step({"in": 1_500 * (step + 1), "cached_in": 6_000, "out": 300})
except BudgetExceeded as e:
    print("stopped:", e)
# stopped: run-42: 14 steps, $0.56
```

| Stuck run | Steps | Cost per run | 1,000 such runs a day |
|---|---|---|---|
| No limits (stops only at an illustrative 100-step ceiling) | 100 | ≈ $23.36 | ≈ $23,355 |
| `max_usd=0.50` in the meter | 14 | ≈ $0.56 | ≈ $560 |

- **The spend limit fired before the step limit,** because context growth makes later steps expensive:
  step 1 costs about $0.011, step 14 about $0.069.
- **The 100-step ceiling is illustrative;** framework defaults differ (LangGraph's `recursion_limit`
  counts graph super-steps, the OpenAI Agents SDK's `max_turns` counts model turns), and none of them
  caps spend, so set your own step and spend limits explicitly.
- **Log the stop as an event,** so "runs stopped by budget" becomes a daily metric, not a silent failure.

### Worked scenario: month-to-date vs trailing-days forecast
Illustrative: search costs $10 a day until `v12` ships on day eight, then $25 a day. The budget is $500.

```python
def forecasts(daily, days_in_month=30, trailing=3):
    day = len(daily)
    mtd = sum(daily)
    avg_forecast = mtd / day * days_in_month                     # month-to-date average
    recent = sum(daily[-trailing:]) / trailing                    # trailing-days rate
    recent_forecast = mtd + recent * (days_in_month - day)
    return mtd, avg_forecast, recent_forecast

daily = [10.0] * 7 + [25.0] * 3          # day 10, step change on day 8
mtd, avg_f, recent_f = forecasts(daily)
print(f"mtd=${mtd:.0f}  avg forecast=${avg_f:.0f}  trailing-3 forecast=${recent_f:.0f}")
# mtd=$145  avg forecast=$435  trailing-3 forecast=$645
```

| Forecast on day 10 | Value | Against $500 budget |
|---|---|---|
| Month-to-date average × 30 | $435 | ok, alert stays silent |
| Spent so far + trailing-3 rate × 20 remaining days | $645 | over, alert fires today |

Run both and alert on either: the average resists noise, the trailing window catches step changes.

## Production patterns
- **Gateway as the only egress, tags mandatory.** When: from the first shared key. How: route every
  provider call, including judge and eval calls, through one gateway or SDK wrapper, issue per-team or
  per-feature keys, and reject or quarantine calls missing tenant and feature tags. Monitor the
  unallocated share of spend weekly.
- **Cost per run as a trace field.** When: from the first agent in production. How: carry a run id through
  every step, sum `cost_usd` per run, and join it to the run's eval or success label. Monitor cost per
  successful run and its p95 per agent; a fat tail is usually loops.
- **Step and spend limits in the loop.** When: every agent, before launch. How: a max-steps and max-spend
  per run enforced in code (as in the meter above), returning a graceful "could not finish" instead of
  looping. Monitor the share of runs stopped by budget.
- **Monthly invoice reconciliation.** When: every billing cycle. How: sum metered `cost_usd` per provider
  and compare with the invoice line. Monitor the gap as a KPI; a rising gap means a bypass path or a stale
  price version.
- **Two-window forecast alerts.** When: any feature with a budget. How: compute both the month-to-date
  burn-rate forecast and a trailing-three-days forecast per feature, as in the example above, and alert if
  either exceeds budget. Monitor forecast error at month end.
- **Tokens-per-task anomaly detection.** When: daily, per feature and prompt version. How: compare each
  day's tokens per task with a trailing baseline for the same feature and flag large ratios. Monitor time
  from anomaly to detection.
- **Hard caps for non-production, soft limits for production.** When: dev, eval and load-test keys. How:
  per-key spend limits in the gateway for non-production keys; forecast alerts plus rate limiting for
  customer-facing keys. Monitor non-production spend as its own line with an owner.
- **Cost estimate as a design-review field.** When: every new feature or agent. How: expected steps per run
  times tokens per step times expected volume times the current price table, reviewed again after launch
  against metered numbers.

## Trade-offs
- **Gateway chokepoint: complete attribution and enforcement vs one more hop.** It adds latency and a
  single point of failure; the trade flips when teams need provider features the gateway cannot pass
  through.
- **Chargeback: real accountability vs needing allocation that teams trust.** Until allocation is nearly
  complete, showback produces better decisions with fewer arguments.
- **Hard per-key caps: stop runaway bills vs outages.** They can take a customer-facing feature down
  mid-month. Soft forecast alerts plus rate limiting suit production paths; hard caps suit development and
  evaluation keys.
- **Tight per-run limits: no runaway loops vs unfinished hard tasks.** A low step or spend limit stops
  legitimate long runs too; set it from the cost distribution of successful runs, not a guess.
- **Commitments (reserved GPUs, provisioned throughput): lower unit price vs locked volume.** They pay off
  only when the baseline is predictable for the whole term.
- **Fine-grained tags vs call-site discipline.** Tenant, feature and prompt-version tags answer "which
  customer is unprofitable" but demand discipline on every call site; coarse tags are cheap and answer
  almost nothing.

## Pitfalls
- **One shared API key for the whole company.** Symptom: finance asks who spent the money and nobody can
  answer beyond "the AI team". Fix: per-team keys and mandatory tags at the gateway.
- **Alerting only on a monthly threshold.** Symptom: an alert fires on the 28th, after the budget is gone
  and before anyone can react. Fix: forecast alerts.
- **Reporting total spend instead of unit cost.** Symptom: a growth month triggers a panic cost cut that
  damages the product. Fix: lead every review with cost per successful task.
- **Hard-coding prices and never updating them.** Symptom: an internal dashboard disagrees with the invoice
  by a wide margin and loses everyone's trust. Fix: a versioned price table and monthly reconciliation.
- **Ignoring non-production spend.** Symptom: production explains half the bill while evals, load tests
  and developer experiments own the rest with no owner. Fix: separate keys, a budget and an owner per
  environment.
- **Splitting a shared GPU pool or platform cost evenly.** Symptom: a margin report where every tenant
  looks equally profitable and the heavy tenant's growth never shows up as a cost. Fix: allocate by a
  written driver such as GPU-seconds.
- **Agents without step and token budgets.** Symptom: a single stuck loop becomes the largest line item of
  the day. Fix: a max-steps and max-tokens limit per run, enforced in code.
- **Metering calls but not runs.** Symptom: cost per call looks flat while cost per run doubled because the
  agent takes more steps. Fix: a run id on every event and a per-run rollup.

## When to use
- **Start** the day there is a bill worth caring about: for most teams the first month the API invoice
  exceeds a few hundred dollars, or the first GPU reservation. For agents, add per-run limits before the
  first production run regardless of the bill.
- **Minimum viable version:** one structured event per call with tags, a versioned price table and a daily
  report by feature. Budgets, forecasts, anomaly detection and showback follow as spend grows; chargeback
  comes last.
- **Measure the practice itself:** percentage of spend allocated, cost per successful task by feature,
  cache hit rate, GPU utilization, forecast error and time from anomaly to detection.
- **Day to day:** a cost column in the trace viewer, a cost estimate in design reviews and a weekly
  ten-minute review.
- **Interviews:** explain the loop, why attribution must happen at the gateway, the usage-versus-rate
  distinction, and why unit cost rather than total spend is the headline.

## Role lens
- **Engineer:** Instruments every call with tags and token counts, keeps the price table versioned, adds
  step and token budgets to agents, and treats a tokens-per-task regression like a latency regression.
- **Architect:** Owns the gateway as the enforcement point, the allocation model, the budget structure and
  the policy that no feature ships without a cost estimate and an owner.
- **FDE:** Sets up per-tenant metering and showback inside the customer's environment, and turns the first
  surprising invoice into a cost-per-task conversation about margin rather than a cancellation.

## Check yourself
**Q:** The monthly LLM bill jumped from $20,000 to $45,000 and leadership wants a 30% cut by Friday. What
do you do first, and why might cutting be the wrong move?
**A:** Attribute before acting. Break the $25,000 delta down by feature, tenant, model and prompt version,
and compute tokens per task and cost per successful task before and after the jump. Three outcomes are
possible. If volume grew and unit cost is flat, the increase is growth, and the right response is to show
margin per task and leave the feature alone. If tokens per task jumped in one feature, it is a regression,
typically a retrieval change, a prompt edit or an agent loop retrying, and fixing it recovers most of the
delta without touching anything else. If the model or price mix changed, the levers are routing traffic
back to cheaper models (usage) and committed-use pricing or a tier change (rate). Then install the guardrails that were missing: per-feature budgets
with burn-rate forecasts, daily anomaly alerts on tokens per task, per-key limits and a cost estimate in
design review, so the next jump is caught in a day rather than on the invoice.

## Interview questions
### Thirty teams call three LLM providers with a handful of shared keys. How would you make spend attributable?
- **One chokepoint:** route every call through an AI gateway or SDK wrapper, with per-team or per-feature
  keys and mandatory tenant, feature, environment and prompt-version tags.
- **Price at record time:** compute cost from a versioned price table using the provider's usage fields,
  and store the price version with each event.
- **Land it with business data:** events go to the warehouse; reconcile monthly against each invoice and
  normalize sources into one schema (FOCUS) once GPU and SaaS spend join in.

### One agent's daily cost tripled overnight while its run count stayed flat. How do you find the cause?
- **Roll up by run first:** compare steps per run, tokens per step and cached-token share against the
  previous day, split by prompt version and tool.
- **Look at the tail:** a few runs hitting the step limit points to a loop (a failing tool, a bad stop
  condition); a uniform rise points to a prompt or retrieval change that grew context.
- **Fix and guard:** roll back or fix the change, and add or lower the per-run spend limit so the next
  regression is bounded.

### How would you set step and spend limits for a production agent?
- **From data, not a guess:** take the distribution of steps and cost for successful runs on your eval set
  and production traces, and set limits above a high percentile so legitimate runs finish.
- **Enforce in code per run,** return a graceful partial answer or escalation, and log the stop as an
  event.
- **Watch both sides:** the share of runs stopped by budget and the success rate; if stops rise while
  success falls, the limit or the agent needs work.

### When would you not use hard spend caps?
- **On customer-facing production paths,** because a cap that trips mid-month takes the feature down for
  customers, which usually costs more than the overspend.
- **Use forecast alerts plus rate limiting there instead,** with an owner who can act the same day.
- **Keep hard caps for development, eval and load-test keys,** where an outage costs little and runaway
  experiments are common.

### Metered spend for the month is $38,000 but the invoice says $45,000. What does the gap tell you?
- **About 16% of spend is unexplained,** which makes every per-feature number suspect until it is closed.
- **Likely causes:** a call path that bypasses the gateway (a script with a personal key, an eval job), a
  stale price in the table, or token counts estimated locally instead of read from usage fields.
- **Track the gap as a KPI** and drive it toward zero before introducing chargeback.

### How would you split a $30,000-a-month self-hosted GPU pool across three features?
- **By the driver that causes the cost:** GPU-seconds or tokens served per feature, weighting decode tokens
  above prefill tokens.
- **Not evenly:** a feature using most of the GPU-seconds should carry most of the cost; an even split
  subsidizes it and hides an unprofitable tenant or feature.
- **Amortize any reservation monthly** over its term, and write the rule down so disputes end.

## Quiz
### Q: Why must token and cost attribution happen in your own gateway rather than from the provider's invoice?
- [ ] Because provider invoices arrive too late to be useful
- [x] Because the invoice carries none of your context: tenant, feature, environment or prompt version
- [ ] Because providers round costs to the nearest dollar
- [ ] Because gateways compute costs faster than providers do
> The provider sees API keys and tokens, not your product structure. Only the code that makes the call
> knows which customer and feature it serves, so the tags have to be attached there and the cost computed
> alongside them.

### Q: Which of these is a usage-optimization lever rather than a rate-optimization lever?
- [ ] Signing a committed-use discount with the provider
- [ ] Negotiating enterprise pricing for the large model
- [x] Trimming the prompt and capping output length so each task consumes fewer tokens
- [ ] Reserving GPU instances for a one-year term
> Rate levers change the price paid per unit and belong to procurement. Trimming and capping reduce the
> number of billable units consumed, which is an engineering change to usage; keeping the two apart shows
> which lever is working.

### Q: Total spend doubled this month while cost per successful task stayed flat. What is the FinOps reading?
- [ ] Waste doubled and spend should be cut immediately
- [x] Usage doubled; check margin per task and whether the growth is wanted, rather than cutting
- [ ] Provider prices doubled without notice
- [ ] Cost attribution is broken and the numbers cannot be trusted
> Flat unit cost with doubled total means twice as many successful tasks were delivered at the same
> efficiency. That is growth, and the decision belongs to product and pricing, not to a cost cut that would
> reduce the value delivered.

## Use cases & projects
### Use cases
- **Catching a runaway agent loop.** Per-run cost rollups and a tokens-per-task anomaly alert flag the day a
  failing tool makes an agent retry, and per-run limits bound the damage.
- **Pricing an agent product per tenant.** Tags on every step let the team compute cost per successful run
  per customer and set plan limits or prices that keep margin positive.
- **Keeping eval spend in check.** Separate keys and budgets for judge-model eval runs on pull requests stop
  CI from quietly rivalling production traffic.
- **Explaining a bill jump to leadership.** Attribution by feature, prompt version and run separates growth
  from regression before anyone cuts a feature.

### Mini project
**Per-run cost meter and daily report for an agent (2-3 hours).**
- **Goal:** know what every run of a small tool-calling agent costs, and stop runaway runs automatically.
- **Steps:** wrap the agent's model client so each call records a usage event (run id, step, tenant,
  feature, prompt version, tokens from the usage fields, cost from a versioned price table) to SQLite or a
  CSV; add a max-steps and max-spend limit per run; run 30 tasks including a few that force a failing tool;
  write a daily report of cost per run, cost per successful run and runs stopped by budget.
- **Done when:** the report shows per-run cost with the stuck runs capped, a burn-rate forecast for the
  month against a stated budget, and the metered total matches the provider's own usage view within a few
  percent.
