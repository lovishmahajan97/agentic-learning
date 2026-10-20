---
id: incident-severity-and-escalation
module: delivery-ownership
name: Incident severity & escalation
kind: practice
difficulty: core
est_minutes: 15
related:
  - delivery-ownership/production-debugging
  - delivery-ownership/root-cause-analysis-and-postmortems
  - delivery-ownership/operational-ownership-and-support-boundaries
  - delivery-ownership/slas
  - security-governance-compliance/ai-incident-response
  - deployment/runbooks
  - deployment/rollout-criteria-and-rollback-triggers
sources:
  - Beyer, Jones, Petoff, Murphy (eds.), 2016 — Site Reliability Engineering
  - Beyer, Murphy, Rensin, Kawahara, Thorne (eds.), 2018 — The Site Reliability Workbook
---

## TL;DR
A severity scale classifies an incident by its impact on users and the business, not by its cause or how
hard it is to fix, so that response speed, who gets woken up and how often stakeholders hear from you
are decided in advance rather than argued about mid-incident. Escalation rules say when to pull in more
expertise (functional) or more authority (hierarchical). AI systems need extra criteria for quality and
safety incidents that throw no errors.

## Why it exists
Without agreed severity levels, every incident starts with a negotiation. One engineer thinks a 5% error
rate is a shrug; another pages the VP. Small issues wake people at 3 a.m. and burn out on-call, while
serious ones sit in a ticket queue because the person who noticed did not feel entitled to raise the
alarm. Customers get updates erratically, and the contractual clock in an SLA runs while people debate.

The naive alternative, "use judgement", fails under exactly the conditions incidents create: stress,
partial information, and someone senior asking whether it is really that bad. Classifying by cause
("it's just a config bug") fails too, because cause is unknown at the start and the same root cause can
be trivial or catastrophic depending on how many users it reaches.

AI products add a twist. A conventional outage is loud: errors spike, health checks fail. An AI
incident is often silent: the service returns HTTP 200 with fluent, wrong, or unsafe content. If severity
criteria only mention availability and latency, a model that starts giving incorrect medical dosage
information, or leaking one tenant's documents into another's answers, may not register as an incident
at all.

## How it works
### Severity is defined by impact
A typical scale has four or five levels. The exact criteria are organisation-specific; a common shape:
- **SEV1 / critical:** broad user-facing outage or degradation of a core function, data exposure or
  security breach, safety harm, or legal or regulatory exposure. All hands, leadership informed.
- **SEV2 / major:** significant degradation for a meaningful share of users or a key customer, with no
  workaround. Urgent, paged response.
- **SEV3 / minor:** limited impact or a workaround exists. Handled in working hours by the owning team.
- **SEV4 / low:** cosmetic or internal-only. Goes to the backlog.
Criteria are written as observable facts (fraction of requests or tenants affected, whether data crossed
a tenant boundary, whether a contractual SLA is at risk) *because* responders must classify within
minutes, with incomplete information.

### AI-specific criteria
Add impact criteria that are about content, not uptime: unsafe or harmful outputs reaching users; cross-
tenant or PII leakage, including through prompt injection; an agent taking unauthorised or irreversible
actions; a measured quality metric (groundedness, task success) falling past a defined threshold;
a provider model change or outage affecting all traffic. A single confirmed data-exposure case can
justify the top severity even if it touched one user, because the harm is not proportional to volume.

### Declare early, adjust freely
Teams are told to declare at the higher plausible severity and downgrade as facts arrive, *because*
under-declaring delays response while over-declaring costs only some attention. Severity is not a verdict
on anyone; it is a routing decision and should change as the picture changes.

### Severity drives the response
Each level maps to: who is paged, a target time to acknowledge and to mitigate, whether an incident
commander is assigned, the update cadence for stakeholders and customers, and whether a postmortem is
mandatory. The commander coordinates and decides; others investigate, communicate, or scribe. Splitting
these roles stops the person debugging from also fielding questions from executives.

### Two kinds of escalation
- **Functional escalation** brings in expertise or ownership: the retrieval team, the platform on-call,
  the model provider's support with request IDs and timestamps. Trigger: the current responders cannot
  make progress or the fault is in someone else's component.
- **Hierarchical escalation** brings in authority: a decision to take a feature offline, notify customers
  or regulators, spend money, or override a customer's change freeze. Trigger: the needed decision is above
  the responder's remit.
Both should also be **time-based**: if a SEV2 is not mitigated within the policy's window, it escalates
automatically, so no one has to judge whether asking for help is admitting failure.

## Example
An illustrative policy (the targets are examples, not a standard) and a timeline for an enterprise RAG
assistant.

Policy excerpt: SEV2 = a key customer or more than 5% of requests degraded with no workaround; page
on-call, acknowledge within 15 minutes, updates every 30 minutes, escalate to engineering manager if not
mitigated in 60 minutes. Any confirmed cross-tenant data exposure = SEV1.

- **09:10** Alert: 4% of responses fail JSON schema validation after a provider model alias moved to a
  newer snapshot. Failures fall back to a generic answer. On-call declares SEV3: workaround exists, small share.
- **09:25** Scoping shows all failures are from one enterprise tenant whose prompts use a tool schema the
  new snapshot handles differently; for them, 60% of requests fail. That tenant is a named account with an
  SLA. Upgraded to SEV2; incident commander assigned; account team informed.
- **09:40** Functional escalation: ticket to the provider with ten request IDs. Mitigation: pin that
  tenant to the previous model snapshot.
- **09:55** Failure rate back to baseline. Customer update sent. Incident stays open at SEV3 until the
  schema is made compatible with the new snapshot.
- **10:30** During review, an engineer notices one fallback answer contained a document title from a
  different tenant's cache. Re-declared SEV1 immediately: security on-call paged, leadership and legal
  informed, cache disabled.

The severity changed three times (SEV3, SEV2, SEV3, SEV1), each time because new facts about impact arrived, not because the cause
changed.

## Trade-offs
- Low thresholds catch real incidents early but page people for noise; they flip toward higher thresholds
  when on-call fatigue causes alerts to be ignored, which is worse than a slower page.
- Detailed severity criteria reduce argument but take time to apply under pressure; keep the top levels
  simple and observable, and put nuance in the lower levels.
- Automatic time-based escalation removes social friction but can pull managers into incidents that are
  nearly resolved; allow the commander to acknowledge and defer with a stated reason.
- Treating quality degradations as incidents protects users but requires a trustworthy online quality
  metric; without one, quality incidents generate false alarms and are quietly ignored.

## Pitfalls
- Defining severity only by uptime and error rate; the symptom is a harmful or wrong-answer episode that
  ran for days because every dashboard stayed green.
- Classifying by suspected cause rather than impact; the symptom is "it's only a config change" incidents
  that turn out to affect every tenant.
- Reluctance to declare or escalate for fear of blame; the symptom is incidents first heard about from
  customers or account managers rather than from monitoring or on-call.
- One person acting as commander, debugger and communicator; the symptom is customer updates that stop
  for an hour exactly when the incident is worst.
- Escalating to a model provider without request IDs, timestamps and a minimal reproduction; the symptom
  is a support ticket that sits unanswered while you wait.

## When to use
Every team running a production service needs a written severity scale and escalation policy before the
first incident, agreed with support, account management and, for enterprise deals, aligned with the SLA's
priority definitions. Revisit it after each postmortem that revealed a classification dispute. For AI
systems, add content-level criteria and the online signals that detect them. Measure time to declare,
time to acknowledge and time to mitigate by severity, and how often severity changed during incidents.
In interviews, expect "how would you classify a hallucination that reached one customer?" and answer by
impact and harm, not by error rate.

## Role lens
- **Engineer:** Writes alerts that map to severity criteria, carries the pager, and knows how to pin a
  model snapshot or disable a feature as the first mitigation.
- **Architect:** Defines the severity scale with AI-specific impact criteria and ensures the system has
  kill switches and per-tenant controls that make fast mitigation possible.
- **FDE:** Aligns the customer's own severity scale and contacts with the vendor's, so an incident in the
  customer's environment escalates to the right people on both sides within minutes.

## Check yourself
**Q:** One user reports that the assistant quoted a paragraph from a document they should not have access
to. Error rates and latency are normal. What severity do you assign and what happens next?
**A:** Top severity, provisionally. Impact, not volume, drives classification: a single confirmed access-
control breach is a data exposure with legal and contractual consequences, and it may affect many users
who simply did not notice. I would page security and the owning team, assign a commander, preserve the
trace, and mitigate fast, for example by disabling the retrieval path or the cache involved. Hierarchical
escalation covers customer and possibly regulatory notification. If investigation shows the user did
have access, we downgrade; declaring high and downgrading is cheaper than the reverse.

## Quiz
### Q: What should primarily determine an incident's severity level?
- [ ] How difficult the root cause is expected to be to fix
- [ ] Which team's component is suspected to be at fault
- [x] The observed impact on users, data, safety and contractual obligations
- [ ] How long the system has been in production
> Cause and ownership are usually unknown when an incident starts, and the same cause can be trivial or
> severe; impact is observable early and determines how urgent and broad the response must be.

### Q: What distinguishes hierarchical escalation from functional escalation?
- [ ] Hierarchical escalation is automatic, functional escalation is manual
- [x] Hierarchical brings in authority to make decisions; functional brings in expertise or the owning team
- [ ] Hierarchical escalation is only used for security incidents
- [ ] Functional escalation always goes to an external vendor
> Functional escalation adds people who can diagnose or fix the component; hierarchical escalation adds
> people who can approve decisions such as taking a feature offline or notifying customers.

### Q: Why do incident policies tell responders to declare at the higher plausible severity?
- [ ] Higher severity incidents are exempt from postmortems
- [ ] It guarantees the SLA clock stops running
- [ ] Customers expect every incident to be reported as critical
- [x] Under-declaring delays the response while over-declaring only costs attention, and severity can be lowered later
> The costs are asymmetric: a late response to a real SEV1 compounds harm, whereas downgrading after facts
> arrive is cheap, so the policy biases toward early, high declaration.
