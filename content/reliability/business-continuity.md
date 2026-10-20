---
id: business-continuity
module: reliability
name: Business continuity
kind: practice
difficulty: core
est_minutes: 12
related:
  - reliability/disaster-recovery
  - reliability/fmea
  - reliability/slas-slos
  - delivery-ownership/incident-severity-and-escalation
  - deployment/runbooks
  - security-governance-compliance/vendor-exit-strategy
  - security-governance-compliance/ai-incident-response
  - system-design/model-fallback-strategies
sources:
  - ISO 22301 — Security and resilience, Business continuity management systems
  - NIST SP 800-34 Rev. 1 — Contingency Planning Guide for Federal Information Systems
  - Beyer et al., 2016 — Site Reliability Engineering
---

## TL;DR
Business continuity (BC) is the practice of keeping the organisation's critical functions running
through a disruption, whatever its cause: a cloud outage, a vendor failure, a model deprecation, a
security incident, or the one person who understands the prompts leaving. Disaster recovery is its
technical subset. BC starts from a business impact analysis that ranks functions and sets tolerable
downtime, then builds plans, workarounds, roles and rehearsals around that ranking, because during a
real disruption nobody has time to decide what matters.

## Why it exists
Disaster recovery answers "how do we bring the system back". It does not answer the questions that
decide whether the business survives the hour in between: which functions must keep working, who is
allowed to declare the failover, how customers are told, what staff do by hand while systems are down,
and what happens when the thing that failed is not your system but a supplier you cannot restore. A
company that restores its database in forty minutes but spends six hours finding the person with the
authority to approve the restore has a DR plan and no continuity.

The naive alternative is to treat continuity as an IT problem and buy more redundancy. It fails for
disruptions that redundancy cannot touch. A model provider deprecates the model your prompts were
tuned on with ninety days' notice. A vendor changes its terms so your use case is no longer permitted.
A regulator halts processing of a data category. A key engineer who holds the undocumented
knowledge of why the system works leaves. None of these show up on an uptime dashboard, and all of
them stop a business function just as thoroughly as a region outage.

AI products concentrate these risks. Many run a core function on a single external model, a single
embedding model that the whole index depends on, and a small number of people who understand the
evaluation set. Enterprise buyers know this, which is why "describe your business continuity plan"
and "what happens if your AI vendor is unavailable" are standard lines in security questionnaires,
and why a credible answer is often a precondition for the deal.

## How it works
### Business impact analysis first
The BIA is a ranked inventory of business functions, not systems. For each function record: what it
does, who depends on it, the **maximum tolerable downtime** (how long before harm becomes
unacceptable; some frameworks call it maximum acceptable outage), the resources and systems it needs,
upstream suppliers, and the cost of an hour of disruption in money, compliance exposure and customer
trust. RPO and RTO for each supporting system are *derived* from this, which is why a BIA done
honestly often shrinks the list of things that need expensive recovery: most functions can wait a
day, and a few cannot wait ten minutes.

### Threat and dependency mapping
For each critical function, list how it can be disrupted and through which dependency: infrastructure
(region, network), suppliers (model provider, vector database SaaS, identity provider), data
(corruption, loss of access), people (key-person loss, mass unavailability), and external (legal,
regulatory, reputational). The AI-specific entries are supplier concentration on one model vendor,
model and API deprecation timelines, sudden pricing or quota changes, and the fact that prompts and
eval sets are intellectual property that may live in one person's head or one unbacked-up notebook.
FMEA is the structured tool for this step.

### Strategies per function, in a ladder
Each critical function gets a sequence of fallbacks, cheapest and most automatic first:
1. **Automatic technical fallback**: secondary provider, smaller self-hosted model, cached results.
   Owned by engineering and covered by fault-tolerance mechanisms.
2. **Degraded service**: retrieval-only answers, templated responses, longer queues, with customers
   informed. Owned by product; the acceptable degradation is decided in advance, not in the incident.
3. **Manual workaround**: humans perform the function with a documented procedure and the minimum
   tooling, *because* the business function matters more than the automation that normally does it.
4. **Suspension with communication**: the function pauses, customers and regulators are told what and
   when, and the backlog plan is known.

### Plans, roles and communication
The business continuity plan documents activation criteria (who may declare an event and on what
evidence), roles with named deputies, a communication tree for staff, customers and regulators,
decision authority during the event, and the recovery and return-to-normal steps. It is written for
someone reading it at 3 a.m. with the usual experts unavailable, which is a different document from
an architecture diagram.

### Testing and maintenance
Plans decay. Tabletop exercises walk the team through a scenario on paper and surface missing
decisions; functional drills actually execute the fallback (switch provider in production for an
hour, process a day's work manually for a sample). Each exercise ends with recorded findings and
owners. Plans are reviewed when the architecture, suppliers or staffing change, and at least annually.
ISO 22301 formalises this lifecycle; regulated customers may ask for alignment with it.

### Vendor exit as a continuity control
For every critical supplier, know the exit: portable prompts and evals that have been run on a second
model, an export path for the vector index and its embedding-model version, contract clauses on
notice periods, and a tested procedure to switch. The exit plan is the BC answer to supplier failure,
and it turns a model deprecation from a crisis into scheduled work.

## Example
A worked continuity plan for an AI support-triage function (figures illustrative).

The BIA for a SaaS company lists "classify and route inbound support tickets" as a critical function:
about 300 tickets per hour at peak, a contractual first-response time of four hours for enterprise
customers, and a maximum tolerable downtime of two hours before SLA penalties start. Dependencies: one
model provider for classification, a vector database SaaS for similar-ticket retrieval, the ticketing
system, and two engineers who maintain the prompts and the 1,200-item eval set. Derived targets: RTO
one hour for the triage service, RPO five minutes for the routing audit log.

Threats recorded: provider outage or quota cut, provider model deprecation, vector SaaS outage, loss
of both prompt maintainers, a prompt-injection incident that forces a halt.

Ladder for a provider outage: at T+0 automatic fallback to a second provider using prompts already
validated against the eval set with a recorded accuracy gap; if that also fails, at T+10 minutes the
service degrades to keyword rules that route to a general queue with a banner telling agents triage
is degraded; at T+30 minutes, if the backlog exceeds 150 tickets, the support lead activates the manual
procedure in which two agents triage from a shared view, which the BIA shows can sustain roughly half
of peak volume, so the backlog grows at about 150 tickets per hour and enterprise tickets are pulled
first to protect the four-hour SLA. Communication: the status page updates at T+15 minutes, enterprise
customers with contractual notification clauses are emailed at T+60.

For deprecation risk: the second provider is exercised in production for one hour every month, and
the eval set lives in the repository with two named owners plus a documented onboarding runbook. The
plan is tabletop-tested twice a year, and last year's drill found the fallback provider's key had
expired, which is now monitored.

## Trade-offs
- A thorough BIA focuses spend on the functions that matter, but it takes real time from business
  owners; it flips to a lightweight version for small teams whose entire product is one function.
- Manual workarounds keep the function alive without any system, yet they sustain only a fraction of
  volume; they are worth documenting when the function is critical and the volume is manageable.
- Keeping a second model provider validated gives a real exit and fallback, at the cost of running
  evals twice and accepting behaviour differences; it stops paying when the function is non-critical.
- Frequent drills keep plans honest but consume engineering and operations time; the balance tilts to
  more drills as contractual penalties and regulatory exposure grow.
- Detailed plans cover more scenarios but go stale faster; shorter plans with clear roles and a
  decision tree survive turnover better than hundred-page binders.

## Pitfalls
- Writing the plan for systems instead of functions, so the team restores the database while nobody
  handles the customers; the symptom is a "successful recovery" followed by churn and penalties.
- Fallbacks that exist on paper only: the secondary provider was never run against the eval set, and
  its first production use reveals a large accuracy drop or broken tool schemas.
- Undefined activation authority, visible as an hour lost to "who can approve this" while the
  backlog grows; name the roles and deputies in advance.
- Single points of knowledge: one engineer understands the prompts and the eval set, and the plan
  silently assumes they are reachable. Key-person loss is a continuity event.
- Ignoring supplier-side disruptions such as deprecations, quota cuts and terms changes because the
  dashboard is green; the symptom is a ninety-day deadline nobody planned for.

## When to use
Do a business impact analysis the moment an AI feature supports a function someone depends on
contractually or operationally, and revisit it when you add a supplier, drop a human from the loop, or
sign an enterprise customer with notification or recovery clauses. Keep the plan proportionate: a
one-page ladder with named roles beats a binder nobody reads. Measure drill findings closed, time to
activate the fallback, and the sustainable throughput of the manual mode, since those are the numbers
auditors and customers will ask for. In day-to-day work this is the "what do we do if the provider is
down for a day" conversation and the vendor-exit checklist. In interviews, the distinguishing point is
that BC is function-first and includes people, suppliers and communication, while DR is the technical
restore underneath it.

## Role lens
- **Engineer:** Builds and exercises the automatic fallback tier, keeps the second provider's prompts
  and evals current, and makes the degraded mode observable and switchable without a deploy.
- **Architect:** Owns the dependency map and supplier concentration risk, sets the degradation ladder per
  function, and translates the BIA's tolerable downtime into system RPO and RTO.
- **FDE:** Writes the customer-facing continuity answer for questionnaires, aligns the plan with the
  customer's own BC programme, and runs the joint tabletop that proves both sides know their roles.

## Check yourself
**Q:** Your assistant's only model provider announces that the model you use will be retired in ninety
days, and its replacement scores noticeably lower on your eval set. Is this a business continuity
event, and how should a mature plan have prepared for it?
**A:** Yes. Nothing is down, but a critical function faces a dated loss of its primary dependency with
a quality regression attached, which is exactly a supplier disruption in BIA terms. A mature plan
would already hold an exit path: prompts and tool schemas kept portable, a second provider or a
self-hosted model evaluated on the same set with a recorded gap, and the eval set itself versioned
with named owners so the comparison can be rerun this week rather than rebuilt. The response is then
scheduled work: run the eval against the replacement and the alternates, pick the best, tune prompts
with the gap measured, canary the switch, and update the ladder so the retired model is no longer a
fallback. Communication to customers who contractually care about model changes happens on the
plan's timeline. Without that preparation, the same notice becomes a ninety-day scramble and a
silent quality drop.

## Quiz
### Q: What is the relationship between business continuity and disaster recovery?
- [ ] They are synonyms used by different industries
- [x] DR is the technical subset of BC that restores systems; BC also covers functions, people, suppliers and communication
- [ ] BC is for small outages and DR is for large ones
- [ ] DR is planned by the business and BC by engineering
> Continuity asks how the function keeps running, including by hand or through a different supplier.
> Recovery asks how the system comes back. A fast restore with nobody handling customers is DR without BC.

### Q: Where should the RTO for a system come from?
- [ ] The cloud provider's published recovery figures for the service
- [ ] The engineering team's estimate of how fast a restore can run
- [x] The business impact analysis's maximum tolerable downtime for the functions that system supports
- [ ] The average duration of past incidents
> Objectives are requirements set by business impact, then met by engineering. Deriving them from what
> the system can already do inverts the logic and hides the gap the plan exists to close.

### Q: Which of these is a business continuity risk that uptime monitoring will not detect?
- [ ] A model provider returning 503 errors for ten minutes
- [ ] A database primary failing over to its replica
- [x] The one engineer who maintains the prompts and the evaluation set resigning
- [ ] A load balancer health check marking a node unhealthy
> Key-person loss stops a function's ability to change or recover safely without any system going
> down. BC treats people and knowledge as dependencies to be documented and duplicated.
