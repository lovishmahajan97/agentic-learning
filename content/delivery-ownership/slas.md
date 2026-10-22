---
id: slas
module: delivery-ownership
name: SLAs
kind: practice
difficulty: core
est_minutes: 15
status: agentic
agent_relevance: useful
related:
  - reliability/slas-slos
  - delivery-ownership/incident-severity-and-escalation
  - delivery-ownership/operational-ownership-and-support-boundaries
  - delivery-ownership/system-reliability
  - scoping/statement-of-work
  - infra-capacity-economics/api-provider-quotas-and-regional-limits
  - system-design/model-fallback-strategies
  - observability/latency-percentiles
sources:
  - Beyer et al., 2016 — Site Reliability Engineering
  - Beyer et al., 2018 — The Site Reliability Workbook
---

## TL;DR
An SLA for an agent is a contract: covered service, exact measurement, exclusions and remedies. A
multi-step agent chains model, tools and retrieval, so commit only to what that chain and on-call can
back.

## For agent builders
- **Why it matters for agents.** One agent run makes many model and tool calls in series, so its
  availability is the product of every dependency on the path, and one slow step blows a latency
  promise. The SLA you can sign is set by your loop design, not by your model provider's headline.
- **Where it shows up in your stack.** Model provider quotas and SLAs, tool and vector-store
  dependencies, per-run step and token caps, fallback model routing, and the attainment query over your
  agent's request logs.
- **What you control.** The unit you promise on (a run, not a model call), step and token caps that bound
  run latency, which tools are on the critical path, fallbacks, and the notice you give before a model or
  prompt change.
- **What you can skip.** Contract law and credit negotiation tactics. Know the allowance table, the
  dependency-ceiling product and why quality stays out of the uptime percentage.

## Why it exists
- **The buyer needs a written promise.** An enterprise is about to route a business process through your
  agent. Procurement, legal and their operations team need to know what happens when it is down at
  2 a.m., how fast someone answers, and what they are owed if it keeps happening.
- **Unwritten expectations get set anyway.** Without an SLA they come from the best week of the pilot,
  what a salesperson said on a call, or the customer's experience with a hyperscaler. Every outage then
  becomes an argument about whether it "counted".
- **The naive numbers fail predictably.**
  - Copying a cloud provider's headline number ignores that your service sits on several dependencies,
    each failing independently, so your honest ceiling is lower than any one of theirs.
  - Promising what the pilot achieved ignores that it ran at a tenth of production traffic with the
    builders watching it.
  - Promising "24/7 support" with no rotation behind it turns the first night incident into a breach.
  - Promising answer quality as if it were uptime creates a metric neither side measures the same way.
- **A signed SLA changes what an incident is.** Downtime is no longer only an engineering problem: it
  accrues credits, triggers reporting obligations and, past a threshold, termination rights. The delivery
  owner has to understand that commercial clock as well as the technical one.
- **Scope of this lesson.** SLI, SLO and error-budget mechanics live in reliability/slas-slos; this
  lesson is about writing and operating the promise.

## How it works
### Anatomy of a service SLA
1. **Service definition.** Which endpoints, features and environments are covered. Production only;
   beta features and sandboxes are explicitly out, *because* you need somewhere to ship experiments.
2. **Metric definition.** The SLI formula in one sentence a query can implement:
   - share of valid requests returning a non-error response within the latency bound, or
   - minutes in which a synthetic probe failed.
   - **Measurement point:** your load balancer, not the customer's laptop, *because* both sides must
     compute the same number from data both can inspect.
   - **Partial outages:** need an explicit rule, usually a "bad minute" whose error rate exceeds a
     threshold. Without it, a month of 3 percent errors is either invisible (probe minutes rarely fail)
     or a total breach, depending on who is reading.
   - **Valid requests:** requests over the rate limit, malformed requests and client cancellations are
     normally excluded from the denominator.
3. **Window and target.** Almost always a calendar month, *because* credits are paid against monthly
   invoices. The allowance is `allowed_downtime = (1 − target) × minutes_in_window`.
4. **Exclusions.** Scheduled maintenance with notice, customer-caused load beyond contracted limits,
   force majeure, and features marked preview. Whether an upstream model provider's outage is excluded
   is the most contested line in AI contracts; customers usually refuse, reasoning that your vendor
   choice is your risk.
5. **Remedies.** Tiered service credits as a percentage of the monthly fee, often stated as the sole
   remedy, plus a termination right after repeated misses. Credits are usually claimed by the customer
   within a stated period, not paid automatically.

### The allowance table is a staffing statement
For a 30-day month (43,200 minutes):

| Target | Allowed downtime per month | What it implies |
|---|---|---|
| 99 percent | 432 minutes | humans can notice and fix |
| 99.5 percent | 216 minutes | a staffed rotation with fast paging |
| 99.9 percent | 43.2 minutes | automated detection and rollback |
| 99.95 percent | 21.6 minutes | automation plus tested failover |
| 99.99 percent | about 4.3 minutes | redundancy across every dependency |

- **Why 99.9 needs automation:** 43 minutes a month is less than one slow
  page-acknowledge-diagnose-rollback cycle, so it cannot rely on a human noticing.

### Support SLAs
- **Promise response time.** The time until a qualified human acknowledges and starts work, per severity
  level (see incident-severity-and-escalation).
- **Treat resolution time as a target** or a workaround commitment, *because* you control when someone
  picks up but not how long an unknown root cause takes to find.
- **Coverage hours matter as much as minutes.** A 30-minute Sev1 response around the clock implies a
  staffed on-call rotation, not one engineer with a phone.

### Deriving what you can commit
1. **List the serial dependencies** on the request path with their own SLAs.
2. **Multiply them, then account for your own failures:** `ceiling = A_own × ∏ SLA_dependency`.
3. **Order the numbers:** `SLA < internal SLO < ceiling`. Set the internal SLO below the ceiling and the
   SLA below the SLO, *because* the gap is the room in which you detect and fix problems before they
   become contract events.

- **The ceiling is contractually backed, not a forecast.** It assumes failures are independent and each
  dependency runs exactly at its SLA floor. Real availability may be better or, for some providers,
  worse, so use measured history where you have it.
- **Remember the asymmetry.** When your provider misses its SLA you receive credits on your provider
  bill, while you owe credits on a customer contract that is usually far larger.

### Operating the promise
Signing is the start. The owner runs the promise all month:
1. **Month-to-date budget.** Minutes consumed against the allowance, visible to the on-call team,
   *because* a 20-minute incident on day 3 and on day 28 of a month that already burned 200 minutes are
   different decisions.
2. **Error-budget policy.** When most of the budget is gone, slow risky changes (prompt edits, model
   swaps, migrations) until the window resets.
3. **SLA-impact field on every incident.** Covered minutes, excluded or not, and why, filled in during
   the postmortem rather than reconstructed at month end, *because* exclusion arguments are won with
   contemporaneous evidence.
4. **Month-end attainment report.** Built from the same query the contract describes; handle credit
   claims against it.

Two commercial facts shape this:
- **Credits are small.** They are commonly capped and stated as the sole remedy, so their value to the
  customer is small next to the business they lost.
- **Repeated misses are what customers escalate over,** which is why termination-on-repeated-breach
  clauses are the ones that matter.

### What AI changes
- **Quality** rarely belongs in the SLA as a percentage. Put it in acceptance criteria, recurring eval
  reports, and a commitment to remediate regressions within a set time.
- **Model changes** need a notice clause: how far ahead you announce a model or prompt change that can
  alter outputs, and whether the customer can pin a version.
- **Latency** depends on output length, so define it as time to first token or per-request latency for
  requests under a token cap; an unqualified p95 promise is broken by one long-answer workload.
- **Throughput**: the SLA applies within contracted rate limits, *because* a 10x burst would otherwise
  turn a provider quota into your breach.

### What agents add
- **Promise on the run, not the call.** Define a valid request as one agent run (user task in, answer or
  explicit handoff out), *because* that is what the customer experiences. A run that retries a tool five
  times and then succeeds is good; one that hits the step cap without an answer is bad.
- **Calls per run multiply the ceiling.** If every run makes several model calls and tool calls in
  series, each with its own failure rate, the run's availability is the product, not the model's number.
  Retries and fallbacks raise it back; measure the run-level number, not the per-call one.
- **Latency needs a bounded loop.** Run latency is roughly steps × (model time + tool time), so a p95
  promise needs a step cap, a token cap per call and a wall-clock budget per run, with a graceful
  "partial answer plus handoff" when the budget runs out.
- **Agent actions need their own terms.** If the agent writes to customer systems, say what happens on a
  failed or duplicated action (idempotent tools, reconciliation), *because* an uptime number says nothing
  about a wrong write.

## Diagram
Read it top to bottom: the agent's dependencies set the ceiling, the ceiling bounds the SLO, the SLO
bounds the SLA, and every incident (a provider outage, a broken tool, a bad prompt deploy) flows into the
monthly attainment and credit decision.

```mermaid
flowchart TD
    D["Model, tool, vector store SLAs"] --> C["Ceiling: own x product of deps"]
    C --> S["Internal SLO below ceiling"]
    S --> A["Signed SLA below SLO"]
    I["Incident"] --> E{"Excluded by contract?"}
    E -->|"yes, with evidence"| R["Logged, no budget used"]
    E -->|"no"| B["Month-to-date budget"]
    B -->|"budget low"| F["Freeze risky changes"]
    B --> M["Month-end attainment report"]
    A --> M
    M -->|"below target"| CR["Credit if customer claims"]
```

## Examples
### SLA calculator: allowance, attainment, credits and ceiling
Pure Python; the numbers match the worked month below.

```python
MINUTES = 30 * 24 * 60  # 43,200 in a 30-day month
TIERS = [(0.95, 0.50), (0.99, 0.25), (0.995, 0.10)]  # (below this, credit share of fee)

def allowance(target, minutes=MINUTES):
    return (1 - target) * minutes

def attainment(bad_minutes, minutes=MINUTES):
    return 1 - bad_minutes / minutes

def credit(avail, fee):
    for floor, pct in TIERS:          # most severe tier first
        if avail < floor:
            return pct * fee
    return 0.0

def ceiling(own, *deps):
    out = own
    for d in deps:
        out *= d
    return out

for t in (0.99, 0.995, 0.999, 0.9995, 0.9999):
    print(f"{t:.2%} -> {allowance(t):.1f} min/month")   # 432.0, 216.0, 43.2, 21.6, 4.3

incidents = {"provider outage": 140, "bad deploy": 95}
for exclude_provider in (False, True):
    bad = sum(m for name, m in incidents.items()
              if not (exclude_provider and name.startswith("provider")))
    a = attainment(bad)
    print(f"exclude_provider={exclude_provider}: {bad} min, {a:.3%}, credit ${credit(a, 40_000):,.0f}")
# exclude_provider=False: 235 min, 99.456%, credit $4,000
# exclude_provider=True: 95 min, 99.780%, credit $0

print(f"ceiling = {ceiling(0.999, 0.999, 0.9995):.4%}")   # 99.7502%
```

### Worked month: two incidents and a breach
A customer pays $40,000 per month (illustrative). The SLA promises 99.5 percent monthly availability,
measured as minutes in which a synthetic probe against the production API fails twice in a row.

| Credit tier | Credit |
|---|---|
| below 99.5 percent | 10 percent of the fee |
| below 99.0 percent | 25 percent |
| below 95 percent | 50 percent |

The month has 30 days, so 43,200 minutes and an allowance of 216 minutes.

| Day | Event | Budget left |
|---|---|---|
| 9 | Model provider regional outage, 140 minutes; fallback never configured for that region | 76 minutes for 21 days |
| 9 | The error-budget policy should have frozen risky changes now; it did not | 76 |
| 22 | Bad deploy breaks the API for 95 minutes | none: 235 minutes total |

- **Attainment:** 1 − 235/43,200 ≈ 99.456 percent, a breach in the first tier: a $4,000 credit if the
  customer files a claim.
- **With a provider exclusion:** downtime would be 95 minutes, 99.78 percent, no breach, which is
  exactly why that clause is fought over.
- **The ceiling behind the number:** provider SLA 99.9 percent, vector database 99.95 percent, your own
  service historically about 99.9 percent. The serial product is 0.999 × 0.9995 × 0.999 ≈ 0.9975, or
  about 99.75 percent if each runs at those levels and fails independently.
- **Margin:** a 99.5 percent SLA leaves about a quarter of a percent. A 99.9 percent SLA, which sales had
  floated, would be breached in an ordinary month unless a tested cross-region or cross-provider
  fallback existed.
- **The asymmetry:** your provider bill is $6,000, so even a 10 percent provider credit returns $600
  against the $4,000 you owe.

### Worked scenario: run-level availability of a support agent
A support agent's typical run makes 4 model calls, 2 tool calls to the customer's ticketing API and 1
vector-store query, all in series. Per-call success rates are illustrative, taken from a month of logs.

| Path | Per-call success | Calls per run | Run success |
|---|---|---|---|
| Model provider | 99.9 percent | 4 | 0.999⁴ ≈ 99.60 percent |
| Ticketing tool | 99.5 percent | 2 | 0.995² ≈ 99.00 percent |
| Vector store | 99.95 percent | 1 | 99.95 percent |
| Whole run, no retries | | 7 | ≈ 98.56 percent |
| Whole run, one retry per failed call | | | ≈ 99.99 percent (if failures are independent) |

- **The lesson:** the per-call numbers look like "three nines", but without retries one run in about 70
  fails, far below a 99.5 percent run-level SLA.
- **Retries are not free:** each retry adds latency, so the step cap and wall-clock budget must leave room
  for them, and correlated outages (a provider region down) defeat retries entirely; only a fallback model
  or region helps there.

```python
def run_success(per_call, calls, retries=0):
    fail = (1 - per_call) ** (retries + 1)      # assumes independent failures
    return (1 - fail) ** calls

parts = [(0.999, 4), (0.995, 2), (0.9995, 1)]   # model, ticketing tool, vector store
for r in (0, 1):
    total = 1.0
    for p, n in parts:
        total *= run_success(p, n, r)
    print(f"retries={r}: run success {total:.2%}")  # 98.56%, 99.99%
```

## Production patterns
- **Contract-exact SLI query** — when signing any SLA, write the attainment query first and attach its
  definition (measurement point, bad-minute rule, valid-request filter) to the contract. Run the same
  query for the internal dashboard and the monthly report; monitor any gap between it and the
  customer's own monitoring.
- **Burn-rate alerts on the internal SLO** — when the SLA allowance is under an hour, alert on how fast
  the stricter SLO budget is burning (a fast window for paging, a slow one for tickets) rather than on
  the SLA threshold. Monitor pages per month and time from first bad minute to acknowledgement.
- **Error-budget change freeze** — when month-to-date budget consumption passes an agreed share, block
  prompt edits, model swaps and migrations through a deploy-pipeline gate until the window resets.
  Monitor how often the freeze triggers and how many changes it deferred.
- **SLA-impact fields in the incident template** — on every incident, require covered minutes, the
  exclusion claimed and the evidence link before the postmortem closes. Monitor the share of incidents
  closed with the field empty.
- **Tested fallback before raising the number** — when sales asks for a higher tier, first run a
  scheduled failover drill to a secondary provider or region and record the switch time. Monitor
  fallback success rate and the drill's measured switch time against the allowance.
- **Run-level SLI from agent traces** — emit one record per agent run (run id, outcome, steps, wall-clock
  time, which dependency failed) and compute attainment over runs, not model calls. Monitor run success
  rate, step-cap hits and the dependency most often named in failed runs.
- **Bounded agent loops** — set a step cap, a per-call token cap and a wall-clock budget per run, and
  return a partial answer with a human handoff when the budget runs out. Monitor run latency p95 against
  the contracted bound and the share of runs ending in handoff.

## Trade-offs
- **Tighter SLA vs cost.** It wins enterprise deals and premium pricing, but each step multiplies the
  redundancy and on-call cost; it flips to worthwhile only when the price uplift exceeds that recurring
  cost.
- **Excluding upstream provider outages vs trust.** It protects your margin, but customers read it as
  you not owning your architecture; it flips toward acceptable when the customer chose or mandated that
  provider.
- **Request-based vs probe-minute measurement.** Requests are fairer to partial outages but need logs
  both sides trust; it flips to probe-based when the customer cannot see your logs or traffic is too
  sparse for per-minute error rates to mean anything.
- **Resolution-time commitments vs control.** They reassure buyers, but you cannot control unknown root
  causes; they only become safe for narrow, well-understood failure classes with a documented
  workaround.
- **Automatic vs claim-based credits.** Automatic credits build trust and avoid claim disputes, but cost
  money on breaches nobody noticed; claim-based credits are cheaper but make every breach a negotiation.
- **Retries and fallbacks vs run latency.** Retrying failed tool and model calls lifts run-level
  availability sharply, but every retry spends latency budget; it flips against retries when the latency
  bound is tight or failures are correlated, where a fallback model or region is the better spend.

## Pitfalls
- **Signing the number sales promised without computing the dependency ceiling.** Symptom: breaching in
  a month where no single component missed its own SLA.
- **Defining the metric vaguely ("the service is available").** Symptom: a month-end dispute where your
  dashboard says 99.7 percent and the customer's monitoring says 98.9 percent.
- **Promising 24/7 response with no funded rotation.** Symptom: a Sev1 acknowledged hours late because
  the only person on call was asleep or on a plane.
- **Treating the SLA target as the alerting threshold.** Symptom: learning about a breach from the
  customer's credit claim instead of from an internal SLO alert days earlier.
- **Leaving the "bad minute" and valid-request rules undefined.** Symptom: a month with a sustained
  partial error rate that your probe calls 100 percent available while the customer's users saw failures.
- **Changing the model or prompt without the notice the contract requires.** Symptom: a customer
  escalating a "silent regression" that is also a contractual violation.
- **Measuring the agent per model call.** Symptom: the dashboard shows 99.9 percent while customers see
  failed runs every day, because one run makes many calls. Fix: compute the SLI per run from traces.
- **Unbounded agent loops under a latency promise.** Symptom: p95 run latency breached by a few runs that
  looped 30 steps. Fix: step, token and wall-clock caps with a graceful handoff.

## When to use
- **Use when:** any production agent deployment a customer pays for and depends on, and internal agent
  platforms other teams build on.
- **Pilots and proofs of concept:** skip formal credits, but still write down response expectations so
  the pilot does not set an unspoken bar.
- **Before signing:** compute the dependency ceiling per agent run (calls per run times per-call
  rates), confirm the on-call staffing behind every support
  commitment, and check that the metric can be computed from data both sides can see.
- **While operating:** track SLA attainment monthly, alert on the stricter internal SLO, and review the
  contract whenever architecture or providers change.
- **Interviews:** expect "how would you set the SLA for this system?" and answer with the chain
  arithmetic.

## Role lens
- **Engineer:** Implements the SLI exactly as the contract defines it, builds the monthly attainment
  report, and wires internal SLO burn alerts that fire well before the contractual threshold.
- **Architect:** Owns the gap between the dependency ceiling and the committed number, deciding which
  redundancy, fallback provider or region the promised SLA actually requires.
- **FDE:** Negotiates exclusions, measurement points and model-change notice with the customer's
  procurement and operations teams, translating their fears into terms you can honestly meet.

## Check yourself
**Q:** A customer asks for 99.95 percent monthly availability and a two-hour resolution time for Sev1. Your
path depends on one LLM provider (99.9 percent SLA) and a managed database (99.99 percent). What do you offer?
**A:** First the ceiling: 0.999 × 0.9999 ≈ 0.9989 before any of my own failures, so 99.95 percent cannot
be backed on this architecture: the provider's own contract permits more downtime than my whole budget,
and signing it means betting on the provider beating its SLA every month. I would offer 99.5 percent, or 99.9
percent only if they fund a tested secondary provider and region, which raises the provider term close
to one. On support, I commit to a Sev1 response time with 24/7 coverage that our rotation can staff,
plus a workaround or status update cadence, but not a two-hour resolution, because root-cause time is
unbounded. I would also add a model-change notice clause and define latency per request under a token cap.

## Interview questions
### What is the difference between an SLA, an SLO and an SLI, and why keep all three?
- **SLI is the measurement,** for example the share of valid requests answered without error within the
  latency bound, computed at a stated point such as your load balancer.
- **SLO is the internal target** on that SLI, and the SLA is the external contract with remedies attached.
- **Why three:** the SLO sits stricter than the SLA so alerts fire and the team acts before a miss
  becomes credits, and the SLI definition is what both sides must compute identically.

### A customer's monitoring shows 98.9 percent for the month and yours shows 99.7 percent. How do you resolve it?
- **Go back to the contract's metric definition:** measurement point, bad-minute rule and which requests
  count as valid. The gap usually lives in one of those three.
- **Recompute with the contract's own query** and compare minute by minute; a sustained partial error
  rate or client-side timeouts often explain the difference.
- **Fix it for next month:** tighten the definition, share the data source both sides can inspect, and
  attach SLA-impact evidence to each incident.

### When would you refuse to exclude upstream model provider outages, and when would you push for it?
- **Accept owning it** when you chose the provider, because customers reasonably say your vendor choice
  is your risk; then you need a tested fallback and a lower committed number.
- **Push for the exclusion** when the customer chose or mandated that provider, because you cannot route
  around their decision.
- **Either way,** price the risk: the provider credit you receive is far smaller than the credit you owe.

### Sales wants to sign 99.9 percent. The path is provider 99.9, vector database 99.95, and your service has historically run at about 99.9. What do you say?
- **Compute the ceiling:** 0.999 × 0.9995 × 0.999 ≈ 99.75 percent, so 99.9 would be breached in an
  ordinary month.
- **Translate it into minutes:** 99.9 allows 43.2 minutes a month, less than one human
  page-diagnose-rollback cycle.
- **Offer options:** sign 99.5 percent now, or 99.9 only after a tested cross-region or cross-provider
  fallback raises the provider term.

### How do you handle answer quality and latency for an LLM agent in an SLA?
- **Quality** goes into acceptance criteria, recurring eval reports and a regression-remediation
  commitment, not an uptime-style percentage neither side measures the same way.
- **Latency** is defined as time to first token or per-request latency under a token cap, because output
  length drives total latency.
- **Model changes** get a notice clause and, where possible, version pinning, so a model swap is not a
  silent contract violation.

### Your agent makes about seven model and tool calls per run. How do you define and defend its availability SLI?
- **Define the unit as a run:** a valid user task that ends in an answer or an explicit handoff within the
  wall-clock bound counts as good; errors, step-cap exhaustion and timeouts count as bad.
- **Compute the ceiling per run:** multiply per-call success rates by calls per run, then show how retries
  and a fallback model lift it, assuming independent failures and naming the correlated ones.
- **Defend it with traces:** one record per run with the failing dependency, so exclusions and credits are
  argued from evidence.

## Quiz
### Q: Why should the internal SLO be stricter than the external SLA?
- [ ] Because regulators require internal targets to exceed contractual ones
- [x] Because the gap gives time to detect and fix degradation before it becomes a contract breach
- [ ] Because customers measure availability more leniently than providers do
- [ ] Because service credits are calculated from the internal SLO
> If the alerting target equals the contract, you learn about problems only once you owe credits. A
> stricter SLO burns first and gives the team room to act; regulation and credit formulas do not require it.

### Q: In a support SLA, which commitment is usually safe to make for every severity level?
- [ ] A guaranteed resolution time for each severity
- [ ] A guaranteed root-cause report within one hour
- [x] A response time for a qualified human to acknowledge and begin work
- [ ] A guarantee that no Sev1 incident will recur
> Response time is within your control if the rotation is staffed. Resolution depends on an unknown root
> cause, so promising it per severity converts hard incidents into automatic breaches.

### Q: Your provider SLA is 99.9 percent and you sign 99.9 percent with your customer. What is the main flaw?
- [ ] Provider SLAs are measured quarterly, so the windows cannot match
- [x] Your service also depends on other components and your own code, so its ceiling is below 99.9 percent
- [ ] Customer SLAs must always be at least one nine looser than provider SLAs
- [ ] Credits from the provider will fully cover the credits you owe
> Serial dependencies multiply, so any additional failure source pushes you below the provider's number.
> There is no fixed one-nine rule, and provider credits are usually far smaller than what you owe.

## Use cases & projects
### Use cases
- **Enterprise support or operations agents.** The customer routes tickets or back-office tasks through
  the agent and procurement asks for availability, latency and Sev1 response terms before go-live.
- **Agent platforms serving internal teams.** Product teams building on your shared agent runtime need a
  written run-level availability and latency target to plan their own commitments.
- **Choosing a model provider or fallback.** The dependency-ceiling arithmetic decides whether a second
  provider or region is required for the tier sales wants to sell.
- **Model and prompt upgrades.** The model-change notice clause sets how far ahead you announce a swap
  and whether the customer can pin a version.

### Mini project
**Run-level SLA report for a toy agent (2-3 hours).**
- **Goal:** compute a contract-style monthly attainment for an agent from its own run traces.
- **Steps:** build a small tool-calling agent with two fake tools that fail at configurable rates; run it
  a few hundred times with and without one retry and with a step cap; log one record per run (outcome,
  steps, duration, failing dependency); compute run success, p95 run latency, budget used against a 99.5
  percent allowance and the credit tier from the calculator above.
- **Done when:** you have a one-page report comparing per-call and per-run availability, showing what
  retries buy and cost in latency, and naming the SLA number you would sign for this agent.
