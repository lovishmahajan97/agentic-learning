---
id: slas
module: delivery-ownership
name: SLAs
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
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
An SLA is a contract, not a dashboard: it defines the covered service, exactly how it is measured, what
is excluded, and what the customer receives when it is missed. Whoever owns delivery owns two kinds,
service SLAs (availability, latency) and support SLAs (response time by severity), and the job is to
commit only to what your dependency chain and on-call rotation can actually back. SLI, SLO and
error-budget mechanics live in reliability/slas-slos; this lesson is about writing and operating the promise.

## Why it exists
An enterprise buyer is about to route a business process through your AI system. Procurement, legal
and their own operations team need to know what happens when it is down at 2 a.m., how fast someone
answers, and what they are owed if it keeps happening. Without a written SLA, expectations are set
implicitly: by the best week of the pilot, by what a salesperson said on a call, or by the customer's
experience with a hyperscaler. Every outage then becomes an argument about whether it "counted".

The naive approaches fail in predictable ways. Copying a cloud provider's headline number ignores that
your service sits on top of several dependencies, each of which can fail independently, so your honest
ceiling is lower than any one of theirs. Promising what the pilot achieved ignores that a pilot ran at a
tenth of production traffic with the builders watching it. Promising "24/7 support" without a rotation
behind it converts the first night incident into a breach. And promising answer quality as if it were
uptime creates a metric neither side can measure the same way.

An SLA also changes what an incident is. Once it is signed, downtime is no longer only an engineering
problem; it accrues credits, triggers reporting obligations and, past a threshold, termination rights.
The person who owns delivery has to understand that commercial clock as well as the technical one.

## How it works
### Anatomy of a service SLA
1. **Service definition.** Which endpoints, features and environments are covered. Production only;
   beta features and sandboxes are explicitly out, *because* you need somewhere to ship experiments.
2. **Metric definition.** The SLI formula in one sentence a query can implement: share of valid
   requests returning a non-error response within the latency bound, or minutes in which a synthetic
   probe failed. State the measurement point (your load balancer, not the customer's laptop) *because*
   the two sides must compute the same number from data both can inspect. Partial outages need an
   explicit rule, usually a "bad minute" defined as a minute whose error rate exceeds a threshold;
   without it, a month of 3 percent errors is either invisible (probe minutes rarely fail) or a total
   breach, depending on who is reading. Say also what counts as "valid": requests over the rate limit,
   malformed requests and client cancellations are normally excluded from the denominator.
3. **Window and target.** Almost always a calendar month, *because* credits are paid against monthly
   invoices. The allowance is `allowed_downtime = (1 − target) × minutes_in_window`. For a 30-day month
   (43,200 minutes): 99 percent allows 432 minutes, 99.5 allows 216, 99.9 allows 43.2, 99.95 allows
   21.6 and 99.99 allows about 4.3. Read that table as a staffing statement: 43 minutes a month is
   less than one slow page-acknowledge-diagnose-rollback cycle, so 99.9 percent implies automated
   detection and rollback, not a human noticing.
4. **Exclusions.** Scheduled maintenance with notice, customer-caused load beyond contracted limits,
   force majeure, and features marked preview. Whether an upstream model provider's outage is excluded
   is the most contested line in AI contracts; customers usually refuse, reasoning that your vendor
   choice is your risk.
5. **Remedies.** Tiered service credits as a percentage of the monthly fee, often stated as the sole
   remedy, plus a termination right after repeated misses. Credits are usually claimed by the customer
   within a stated period, not paid automatically.

### Support SLAs
Support SLAs promise **response time**, the time until a qualified human acknowledges and starts work,
per severity level (see incident-severity-and-escalation). Commit to response, and treat **resolution
time** as a target or a workaround commitment, *because* you control when someone picks up but not
how long an unknown root cause takes to find. Coverage hours matter as much as minutes: a 30-minute
Sev1 response around the clock implies a staffed on-call rotation, not one engineer with a phone.

### Deriving what you can commit
List the serial dependencies on the request path with their own SLAs, multiply them, then account for
your own failures: `ceiling = A_own × ∏ SLA_dependency`, and the ordering you want is
`SLA < internal SLO < ceiling`. That product is your contractually backed ceiling, not a forecast: it assumes failures
are independent and that each dependency runs exactly at its SLA floor (real availability may be better or,
for some providers, worse, so use measured history where you have it); set the internal SLO below it and the SLA below the SLO,
*because* the gap is the room in which you detect and fix problems before they become contract events.
Remember the asymmetry: when your provider misses its SLA you receive credits on your provider bill,
while you owe credits on a customer contract that is usually far larger.

### Operating the promise
Signing is the start. The owner runs a month-to-date budget: minutes consumed so far against the
allowance, visible to the on-call team, *because* a 20-minute incident on day 3 and on day 28 of a
month that already burned 200 minutes are different decisions. When most of the budget is gone, the
error-budget policy should slow risky changes (prompt edits, model swaps, migrations) until the window
resets. Each incident record gets an SLA-impact field (covered minutes, excluded or not, and why),
filled in during the postmortem rather than reconstructed at month end, *because* exclusion arguments
are won with contemporaneous evidence. At month end, publish an attainment report built from the same
query the contract describes, and handle credit claims against it. Two commercial facts shape this:
credits are commonly capped and stated as the sole remedy, so their value to the customer is small
next to the business they lost; what customers actually escalate over is repeated misses, which is
why termination-on-repeated-breach clauses are the ones that matter.

### What AI changes
- **Quality** rarely belongs in the SLA as a percentage. Put it in acceptance criteria, recurring eval
  reports, and a commitment to remediate regressions within a set time.
- **Model changes** need a notice clause: how far ahead you announce a model or prompt change that can
  alter outputs, and whether the customer can pin a version.
- **Latency** depends on output length, so define it as time to first token or per-request latency for
  requests under a token cap; an unqualified p95 promise is broken by one long-answer workload.
- **Throughput**: the SLA applies within contracted rate limits, *because* a 10x burst would otherwise
  turn a provider quota into your breach.

## Example
A worked month. A customer pays $40,000 per month (illustrative). The SLA promises 99.5 percent monthly
availability, measured as minutes in which a synthetic probe against the production API fails twice in a
row. Credits: below 99.5 percent, 10 percent of the fee; below 99.0 percent, 25 percent; below 95 percent,
50 percent. The month has 30 days, so 43,200 minutes and an allowance of 216 minutes.

Two incidents occur. On day 9 the model provider has a regional outage for 140 minutes and your fallback
was never configured for that region. The month-to-date budget now shows 76 minutes left for 21 days,
which is the moment the error-budget policy should have frozen risky changes. It did not, and on day 22
a bad deploy breaks the API for 95 minutes. Total downtime is 235
minutes; availability is 1 − 235/43,200 ≈ 99.456 percent. That is a breach in the first tier: a $4,000
credit if the customer files a claim. If the contract had excluded upstream provider outages, downtime
would be 95 minutes, 99.78 percent, no breach, which is exactly why that clause is fought over.

Now the ceiling check behind the number. The provider's SLA is 99.9 percent, the vector database's
99.95 percent, and your own service has historically run at about 99.9 percent. The serial product is
0.999 × 0.9995 × 0.999 ≈ 0.9975, or about 99.75 percent if each runs at those levels and fails independently. A 99.5 percent SLA leaves a margin of about
a quarter of a percent; a 99.9 percent SLA, which sales had floated, would be breached in an ordinary
month unless a tested cross-region or cross-provider fallback existed. Finally the asymmetry: your
provider bill is $6,000, so even a 10 percent provider credit returns $600 against the $4,000 you owe.

## Trade-offs
- A tighter SLA wins enterprise deals and premium pricing, but each step multiplies the redundancy and
  on-call cost; it flips to worthwhile only when the price uplift exceeds that recurring cost.
- Excluding upstream provider outages protects your margin, but customers read it as you not owning your
  architecture; it flips toward acceptable when the customer chose or mandated that provider.
- Request-based measurement is fairer to partial outages than probe minutes, but needs logs both sides
  trust; it flips to probe-based measurement when the customer cannot see your logs or traffic is too
  sparse for per-minute error rates to mean anything.
- Committing to resolution times reassures buyers, but you cannot control unknown root causes; it only
  becomes safe for narrow, well-understood failure classes with a documented workaround.
- Automatic credits build trust and avoid claim disputes, but cost money on breaches nobody noticed;
  claim-based credits are cheaper but make every breach a negotiation.

## Pitfalls
- Signing the number sales promised without computing the dependency ceiling; the symptom is breaching in
  a month where no single component missed its own SLA.
- Defining the metric vaguely ("the service is available"); the symptom is a month-end dispute where your
  dashboard says 99.7 percent and the customer's monitoring says 98.9 percent.
- Promising 24/7 response with no funded rotation; the symptom is a Sev1 acknowledged hours late because
  the only person on call was asleep or on a plane.
- Treating the SLA target as the alerting threshold; the symptom is learning about a breach from the
  customer's credit claim instead of from an internal SLO alert days earlier.
- Leaving the "bad minute" and valid-request rules undefined; the symptom is a month with a sustained
  partial error rate that your probe calls 100 percent available while the customer's users saw failures.
- Changing the model or prompt without the notice the contract requires; the symptom is a customer escalating a
  "silent regression" that is also a contractual violation.

## When to use
Write an explicit SLA for any production deployment a customer pays for and depends on, and for
internal platforms other teams build on. Skip formal credits for pilots and proofs of concept, but still
write down response expectations so the pilot does not set an unspoken bar. Before signing, compute the
dependency ceiling, confirm the on-call staffing behind every support commitment, and check that the
metric can be computed from data both sides can see. While operating, track SLA attainment monthly, alert
on the stricter internal SLO, and review the contract whenever architecture or providers change. In
interviews, expect "how would you set the SLA for this system?" and answer with the chain arithmetic.

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
