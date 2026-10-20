---
id: finops
module: infra-capacity-economics
name: FinOps
kind: practice
difficulty: core
est_minutes: 14
status: reviewed
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
FinOps is the operating practice that makes variable cloud and AI spend visible, attributable and owned by
the teams that cause it, so cost becomes an engineering signal like latency rather than a quarterly
surprise. For LLM systems it means metering every call at the point where it is made, allocating spend to
tenant and feature, tracking cost per successful task, and running a continuous inform–optimize–operate
loop instead of a one-off cost-cutting project.

## Why it exists
Traditional budgeting assumes spending is decided by purchase orders that finance approves in advance.
Cloud broke that assumption: a line in a config file decides spend, and the invoice arrives a month later.
LLM APIs break it harder. Spend is per token, so a prompt edit, a retrieval change that doubles context, an
agent loop that retries, or a traffic spike can move the bill by a multiple with no procurement event and no
code reviewer thinking about money. And the provider's invoice is a single line, "API usage", that says
nothing about which product, customer, feature or team caused it. Finance cannot allocate it; engineering
gets no feedback; the two sides meet only when the number is already alarming.

The naive responses each fail. Approval gates for every new model or key slow delivery and get bypassed
with personal keys, which makes attribution worse. A one-time "cost project" saves a slice and then the bill
climbs back, because nothing structural changed and nobody is watching. Hard spend caps protect the budget
by taking the feature down for customers at month end. What actually works is the thing that worked for
latency and reliability: timely, attributed data in front of the people making the decisions, clear
ownership, and a loop that runs every week rather than once a year.

## How it works
### The loop: inform, optimize, operate
The FinOps Foundation frames the practice as a cycle. *Inform* means seeing and allocating spend
accurately and quickly. *Optimize* means acting on it, through both usage and rate levers. *Operate* means
making the first two continuous: budgets, forecasts, reviews, owners and policy. It is a loop *because*
costs drift: models are updated, prices change, features ship weekly and traffic mix moves. A snapshot
goes stale in a month.

### Inform: meter at the point of call
Provider invoices lack your context, so attribution has to happen in your own code, at the single point
every call passes through: an AI gateway or an SDK wrapper. Each call records the model, input, output and
cached token counts, latency, tenant, feature, environment, prompt version and a request id that joins to
your traces. Cost is computed at record time from a versioned price table and stored alongside the price
used, *because* prices change and a cost history that silently re-prices itself is useless for trend
analysis. Token counts come from the provider's response usage fields, not from your own tokenizer
estimate, *because* tokenizers differ between models and estimates drift. Events flow to the warehouse
where every other business metric lives, and once a month the metered total is reconciled against the
invoice; the gap is a data-quality KPI, and a growing one means a call path is bypassing the gateway or a
price in the table is stale. Normalizing cloud and AI billing into one schema is what the FinOps
Foundation's FOCUS specification is for, which matters once GPU, API and SaaS spend must be read together.

The gateway is the structural fix for the root cause of unattributable spend: one shared API key per
company. Per-team or per-feature keys plus mandatory tags make unallocated spend visible as a bucket to be
driven toward zero. Start with *showback*, showing each team its own spend, and move to *chargeback*,
actually debiting internal budgets, only once allocation is trusted; chargeback on shaky numbers produces
arguments about the numbers instead of decisions about the spend.

### Shared costs: allocate them by a driver, not evenly
Not every dollar has a natural tag. A self-hosted GPU pool serves many features; an embedding index, an
eval suite and the platform team serve everyone; a one-year reservation is paid upfront. Allocate each by
the driver that causes it: GPU pool cost by GPU-seconds or tokens served per feature (decode tokens weigh
more than prefill tokens for the reason above), reservations amortized monthly over the term rather than
booked in the month they were paid, platform cost by a stated proxy such as share of requests. Splitting
evenly is the tempting shortcut and it is wrong *because* it subsidizes the heavy users with the light
ones, which hides exactly the unprofitable tenant the exercise was meant to find. Whatever the rule, write
it down; disputes about allocation are disputes about the rule, and a written rule ends them.

### Unit economics: telling growth from waste
Total spend is the wrong headline. The question is whether cost per unit of value moved: cost per
successful task, per active user, per document processed, gross margin per tenant. A bill that doubled while
cost per task stayed flat is growth; a bill that doubled on flat volume is a regression. Only the unit view
distinguishes them, and only the unit view lets product price the feature.

### Optimize: usage levers and rate levers are different jobs
*Usage optimization* means consuming fewer units: prompt trimming, prompt and response caching, routing
cheap models first, output caps, step and token budgets on agents,
retiring idle GPU replicas. *Rate optimization* means paying less per unit: committed-use and provisioned
throughput discounts, reserved GPUs, negotiated enterprise pricing, and moving offline work to
discounted batch endpoints, which bill the same tokens at a lower price. They are kept
separate because they belong to different people (engineering versus procurement), move on different
timescales, and mixing them hides which one is actually working.

### Operate: budgets, anomalies, forecasts, owners
Each feature and team gets a budget with an alert on *forecast* (month-to-date burn rate projected to
month end), not just on the threshold, because by the time a threshold fires the money is spent. Project
from the *recent* daily rate (say, the trailing three days) as well as the month-to-date average, because
a step change on day eight is diluted by seven normal days in the average and shows up in full in the
recent rate. Maturity is gradual, and the FinOps Foundation describes it as crawl, walk, run: a daily
report first, then budgets and showback, then forecasts, anomaly detection and chargeback. Daily
anomaly detection runs per dimension: a feature whose tokens per task tripled overnight is a bug, not a
trend. A named owner reviews each cost centre weekly. Guardrails live in the gateway: per-key spend
limits, model allowlists, default output caps. New features carry a cost estimate through design review,
expected tokens per task times expected volume, so cost is discussed before the code exists.

### What is specific to AI spend
Tokens are driven by prompt design, so cost is a code-review concern. Agents multiply spend: a ten-step
loop is ten calls, and context grows with every step. Evaluation is real spend: a large eval suite run
against a judge model on every pull request can rival production traffic. Cached tokens are priced
differently, so cache hit rate is a cost KPI. For self-hosted models the waste metric is GPU idle time,
which belongs on the same dashboard as token spend.

## Example
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

The point of the output is the first line. On day ten the search feature has spent only $115 of its
$300 budget, so a threshold alert would stay silent for two more weeks; the burn-rate forecast flags it
today. Because every event carries `prompt_version` and token counts, the next query, tokens per call by
prompt version, shows that `v12` doubled input tokens, and the fix is a prompt rollback rather than a
budget increase. The simulation runs the whole month-to-date at the new rate; in real traffic, if `v12`
shipped on day eight, the month-to-date average would mix in seven cheaper days, so the burn-rate forecast
would understate the overrun and a trailing-days forecast would show it in full. Note too where the money
is: each search call costs about $0.00046, and 372 of its 462 micro-dollars are input tokens, so context
size, not output length, is the lever for that feature.

## Trade-offs
- A gateway chokepoint gives complete attribution and enforcement, but it is one more hop of latency and a
  single point of failure; the trade flips when teams need provider features the gateway cannot pass through.
- Chargeback creates real accountability, but needs allocation that teams trust; until allocation is nearly
  complete, showback produces better decisions with fewer arguments.
- Hard per-key spend caps stop runaway bills but can take a customer-facing feature down mid-month; soft
  forecast alerts plus rate limiting suit production paths, hard caps suit development and evaluation keys.
- Commitments such as reserved GPUs or provisioned throughput lower unit price but lock in volume; they pay
  off only when the baseline is predictable for the whole term.
- Fine-grained tags (tenant, feature, prompt version) answer "which customer is unprofitable" but demand
  discipline on every call site; coarse tags are cheap and answer almost nothing.

## Pitfalls
- One shared API key for the whole company; the symptom is finance asking who spent the money and nobody
  being able to answer beyond "the AI team".
- Alerting only on a monthly threshold; the symptom is an alert that fires on the 28th, after the budget is
  gone and before anyone can react.
- Reporting total spend instead of unit cost; the symptom is a growth month triggering a panic cost cut
  that damages the product.
- Hard-coding prices and never updating them; the symptom is an internal dashboard that disagrees with the
  invoice by a wide margin and loses everyone's trust.
- Ignoring non-production spend; the symptom is production explaining half the bill while evals, load tests
  and developer experiments own the rest with no owner.
- Splitting a shared GPU pool or platform cost evenly across teams; the symptom is a margin report where
  every tenant looks equally profitable and the heavy tenant's growth never shows up as a cost.
- Agents without step and token budgets; the symptom is a single stuck loop becoming the largest line item
  of the day.

## When to use
Start the day there is a bill worth caring about, which for most teams is the first month the API invoice
exceeds a few hundred dollars or the first GPU reservation. The minimum viable version is one structured
event per call with tags, a versioned price table and a daily report by feature; budgets, forecasts,
anomaly detection and showback follow as spend grows, and chargeback comes last. Measure the practice
itself: percentage of spend allocated, cost per successful task by feature, cache hit rate, GPU utilization,
forecast error and time from anomaly to detection. Day to day it shows up as a cost column in the trace
viewer, a cost estimate in design reviews and a weekly ten-minute review. In interviews, be able to explain
the loop, why attribution must happen at the gateway, the usage-versus-rate distinction and why unit cost
rather than total spend is the headline.

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
