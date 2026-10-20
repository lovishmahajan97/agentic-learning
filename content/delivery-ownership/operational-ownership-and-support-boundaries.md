---
id: operational-ownership-and-support-boundaries
module: delivery-ownership
name: Operational ownership & support boundaries
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - delivery-ownership/incident-severity-and-escalation
  - delivery-ownership/slas
  - delivery-ownership/technical-documentation
  - delivery-ownership/productizing-repeated-customer-solutions
  - deployment/hypercare
  - deployment/runbooks
  - deployment/customer-vpc-on-prem-air-gapped
  - reliability/slas-slos
---

## TL;DR
Operational ownership means a named team is on the hook for a component in production: it gets paged,
has the access and authority to change it, and answers for its reliability. Support boundaries write
down where that ownership stops: which layers you support, which belong to the customer or a vendor, and
how issues cross the line. AI deployments span many parties, so without explicit boundaries incidents
bounce between teams while nobody fixes them.

## Why it exists
A deployed AI system is a stack of things owned by different people: the model provider's API, your
platform and serving layer, the application's prompts, retrieval and tools, the customer's identity
provider, network and data sources, and often a customer-built front end. When an answer is wrong or a
request fails, the cause can be in any layer, and each owner's first instinct is to show it is not
theirs. The customer, meanwhile, just sees "your AI is broken."

The naive arrangement is implicit ownership: whoever built it, or whoever is most helpful, fixes it. In
forward-deployed work this usually means the engineer who did the integration keeps getting called months
after go-live, for problems in systems they no longer control. It does not scale, it makes that person a
single point of failure, and it hides the true cost of supporting the deployment. The opposite failure is
tight "not our problem" boundaries with no path across them, where a ticket about stale answers is closed
because the stale documents live in the customer's SharePoint.

Explicit ownership and boundaries exist so that every component has someone who will act, every party
knows what it must do, and issues that span boundaries have a defined route instead of a debate.

## How it works
### Owners have pager, access and authority
A team owns a component only if it carries three things together: it is paged when it fails, it has the
access needed to diagnose and change it, and it has the authority (and budget) to prioritise fixes.
Ownership without access produces helpless on-call; access without ownership produces drive-by changes no
one is accountable for. A service catalog records each component's owner, on-call rotation, runbook,
dependencies and support hours.

### Draw the boundary by layer
For each layer of the stack, state who is responsible for operating it, who for changing it, and who for
its inputs. A shared-responsibility table is the usual artefact, sometimes expressed as RACI (Responsible,
Accountable, Consulted, Informed), *because* "who must act" and "who must be told" are different
questions and conflating them causes either paralysis or surprise. The rule that makes RACI useful is
exactly one Accountable per row: several Responsible parties can share the work, but if two parties are
Accountable, each can wait for the other, and a row with none is unowned by construction.

### Supported configurations and coverage
A boundary is also a statement of *what* you support and *when*. A supported-configuration list names the
versions, models, connectors and deployment shapes you will diagnose; a customer who swaps in their own
model or edits the system prompt has moved outside it, and the agreement should say whether support then
becomes best-effort. Coverage hours matter as much as scope: a 24×7 SEV1 commitment needs a staffed
rotation (or follow-the-sun across time zones), while business-hours support must say what happens to a
SEV1 at 02:00 on Saturday. Mismatched hours between vendor and customer are a classic gap, because
each side's on-call assumes the other's is awake.

### Define the crossing points
Most real incidents span a boundary, so the important part is the interface: what evidence the reporting
party must provide (request IDs, timestamps, tenant), which channel to use, response targets by severity,
and who leads joint incidents. "Out of scope" should mean "we route it to the right owner with
evidence", not "we close the ticket."

### Tiered support
Support is usually layered: tier 1 takes the report, gathers evidence and resolves known issues from
runbooks; tier 2 diagnoses using logs and tooling; tier 3 is the engineering team that can change code,
prompts or models. Each tier needs documentation to resolve what it can, *because* every issue that
reaches engineering unnecessarily is expensive and slows real fixes. The useful measures are the share
of tickets resolved at each tier and the share escalated with complete evidence: a tier 3 that keeps
asking "which request ID?" is doing tier 1's job.

### Plan the handover
Deployments move through phases: build, launch, a period of elevated attention (hypercare) and steady
state. Ownership should change at defined points with exit criteria, such as a stable error and quality
baseline, runbooks validated by the receiving team, and alerts routed to their on-call. Without this, the
build team never actually lets go.

### AI-specific boundaries
Make explicit the things that are new in AI systems: who owns answer quality (the prompt and eval owner,
not the platform), who owns source data freshness and access permissions (often the customer), who decides
when a provider model snapshot changes, and who responds to harmful-output reports. These are the
questions most likely to be unowned by default.

## Example
A vendor deploys a RAG assistant into a bank's private cloud. The boundary table agreed at handover:

| Layer | Operates | Changes | Notes |
|---|---|---|---|
| Model endpoint (cloud provider) | Provider | Vendor chooses snapshot | Vendor escalates to provider |
| Application, prompts, retrieval, evals | Vendor | Vendor | Quality incidents are the vendor's |
| Kubernetes cluster, network, SSO | Bank platform team | Bank | Vendor has read-only logs |
| Source document repositories | Bank content owners | Bank | Freshness and permissions are the bank's |
| Chat front end in the intranet | Bank web team | Bank | Vendor supplies API only |

Week 9 ticket: "answers cite last year's expense policy." Tier 1 at the bank collects three request IDs.
Vendor tier 3 traces them: retrieval returned the 2025 document because the 2026 one was uploaded to a
folder not included in the connector scope. The cause is on the bank's side (document placement), the
detection gap is on the vendor's (no freshness eval). Resolution: the bank's content owner moves the
file and adopts a publishing checklist; the vendor adds a freshness eval and an alert when a source's
newest document is older than a set threshold. The ticket is resolved in a day because both sides knew
who did what, and neither closed it as "not ours".

## Trade-offs
- "You build it, you run it" aligns incentives and shortens feedback, but loads product teams with
  on-call; it flips toward a dedicated operations or platform team when many small services share infrastructure.
- Narrow support boundaries keep costs predictable, but frustrate customers when problems straddle them;
  widen them, or sell an extended tier, for strategic accounts where retention matters more than margin.
- Keeping FDEs as the first line after launch gives customers expert help, but stops FDEs from moving to
  new deployments; it only works for a time-boxed hypercare window with a dated handover.
- Granting the vendor broad access inside a customer environment speeds diagnosis, but raises security
  and compliance burden; read-only, audited access to logs and traces is usually the workable middle.

## Pitfalls
- Ownership assigned to a team without access or authority; the symptom is on-call that can only open
  tickets for someone else and mean time to mitigate measured in days.
- Orphaned components after a reorganisation or project end; the symptom is alerts routed to a channel
  nobody reads and a service catalog entry naming someone who has left.
- No owner for answer quality; the symptom is a slow decline in user satisfaction with every team
  pointing at another layer and no one running evals.
- Handover without exit criteria; the symptom is the original builders still being paged months after
  go-live, often unofficially through direct messages.
- Boundaries written only in the contract; the symptom is support staff on both sides who have never seen
  the table and argue scope during an incident.
- Two parties marked Accountable for the same layer, often "shared" data quality; the symptom is a known
  problem that stays open for weeks while each side waits for the other to schedule the fix.

## When to use
Define ownership and boundaries before go-live of any production system, and revisit them at each phase
change, reorganisation or new integration. For multi-party AI deployments, agree the boundary table with
the customer as part of deployment readiness, mirror it in the SLA, runbooks and support tooling, and
test it with a tabletop exercise. Measure tickets bounced between parties, time to route an issue to its
owner, and the share of tickets reaching engineering that tier 1 or 2 could have resolved. In interviews,
expect "after launch, who supports this?" and answer with owners, crossing points and a handover plan.

## Role lens
- **Engineer:** Builds the runbooks, dashboards and evidence-gathering tools that let other tiers resolve
  issues without escalating to the engineers who wrote the code.
- **Architect:** Draws component boundaries so each has one owner, and designs the access model and
  interfaces that let ownership cross organisations safely.
- **FDE:** Negotiates the boundary table with the customer, runs hypercare, and executes the handover so
  the deployment does not depend on them personally after it ends.

## Check yourself
**Q:** Six months after launch, the FDE who built a customer's deployment still receives most of its
support requests by direct message. What is wrong, and what would you change?
**A:** Ownership was never transferred: the deployment has an implicit owner with no on-call rotation,
no shared runbooks and no defined route for the customer's issues. That makes one person a single point
of failure and hides the support cost. I would write the boundary table with the customer, register the
components in the service catalog with an owning team and rotation, route requests to a support queue
with tiering, make sure runbooks and dashboards let tier 2 diagnose common issues, and set handover exit
criteria the receiving team signs off. The FDE then becomes a tier 3 escalation, not the front door.

## Quiz
### Q: Which combination makes a team a genuine operational owner of a component?
- [ ] It wrote the original code and is listed in the README
- [x] It is paged for failures, has access to diagnose and change it, and has authority to prioritise fixes
- [ ] It has admin credentials to the production environment
- [ ] It attends the weekly status meeting with the customer
> Paging without access, or access without accountability, both fail; ownership requires being alerted,
> being able to act, and being empowered to choose the work.

### Q: A user reports stale answers; the stale document lives in the customer's repository, outside your support scope. What is the correct response?
- [ ] Close the ticket as out of scope and inform the user
- [ ] Silently fix the repository yourself using any access you have
- [x] Route it to the customer's content owner with evidence, and check whether your side should have detected it
- [ ] Retrain the embedding model on the latest documents
> A boundary defines who acts, not whether the issue matters; out-of-scope issues are routed with evidence,
> and detection gaps on your side remain your action items.

### Q: What is the main purpose of exit criteria for a hypercare period?
- [ ] To decide when the customer can stop paying for support
- [ ] To guarantee no incidents happen after handover
- [ ] To let the build team skip writing runbooks
- [x] To make the transfer of ownership a deliberate, verified step rather than something that never happens
> Without criteria such as a stable baseline, validated runbooks and alerts routed to the new owner,
> builders stay informally on the hook indefinitely and the receiving team is never ready.
