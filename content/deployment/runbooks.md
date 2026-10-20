---
id: runbooks
module: deployment
name: Runbooks
kind: practice
difficulty: core
est_minutes: 13
status: reviewed
related:
  - deployment/health-checks
  - deployment/hypercare
  - delivery-ownership/incident-severity-and-escalation
  - security-governance-compliance/ai-incident-response
  - observability/monitoring
  - adoption/handoff-to-customer-teams
  - delivery-ownership/production-debugging
sources:
  - Beyer, Jones, Petoff, Murphy, 2016 — Site Reliability Engineering
---

## TL;DR
A runbook is a written, tested procedure for one operational situation: what triggered it, how to
confirm the impact, the exact steps to mitigate, how to verify recovery and when to escalate. It exists
because the person handling an incident is rarely the person who built the system, and at 3 a.m. under
pressure nobody reasons well from first principles. Good runbooks are linked from the alert that fires,
favour mitigation over diagnosis, and are rehearsed so they are known to work.

## Why it exists
When something breaks, the knowledge needed to fix it usually lives in a few engineers' heads: which
dashboard shows the real problem, which flag switches to the fallback model, which log query finds the
failing tenant. That works until those engineers are asleep, on holiday, or have moved to another
project. For forward-deployed work it fails structurally: after handoff, the customer's operations team
is on call for a system they did not build, often in an environment the vendor cannot access.

The naive alternatives both fail. "The on-call will figure it out" turns every incident into an
investigation, stretching time to mitigation from minutes to hours while users are affected. A long
architecture document does not help either: it explains how the system works, not what to do when the
provider is returning 429s, and nobody reads twenty pages during an outage.

AI systems add failure modes that conventional operations teams have not seen: an LLM provider degrading
or rate-limiting, a cost spike from a prompt loop or an abusive user, a sudden rise in guardrail blocks,
a stale or partially rebuilt retrieval index, a jailbreak or prompt-injection report, a model answering
confidently and wrongly after an upstream change. Each needs a specific response, and some (pausing an
agent's write tools, notifying security) carry business consequences the on-call needs explicit
permission to take. A runbook is where that permission and procedure are written down in advance.

## How it works
### One situation per runbook
A runbook covers one alert or symptom, not a whole service, *because* the reader arrives from a specific
page and needs the steps for that situation, not a tour. Every alert links to its runbook; an alert with
no runbook is a sign the alert is either unnecessary or the knowledge is missing.

### A fixed structure
1. **Trigger:** the alert name and threshold, or the user-reported symptom.
2. **Impact:** who is affected and how badly, with a severity guide, *because* severity decides who is
   woken up and how fast customers are told.
3. **Confirm:** one or two checks that tell a real problem from a false alarm, with exact dashboard
   links or queries.
4. **Mitigate:** the steps that stop user harm, in order, with exact commands or flag names. Mitigation
   comes before diagnosis *because* restoring service is urgent and root cause can wait for the
   postmortem.
5. **Verify:** what metric must return to normal, and for how long, before declaring recovery.
6. **Escalate:** who to call if mitigation fails or the situation does not match, with names or rotas,
   not "the platform team".
7. **Afterwards:** what to record for the postmortem (timeline, traces, request IDs).

### Precision over prose
Steps are imperative and copy-pasteable: the flag name, the query, the expected output. Decision points
are explicit ("if 429s are from one tenant, go to step 6; otherwise continue"). Vague steps such as
"check the logs" are where incidents stall.

### Pre-authorised actions
Runbooks state which disruptive actions the on-call may take without asking: switching to a fallback
model, disabling an agent's write tools, blocking a tenant, shedding load. Pre-authorisation exists
*because* waiting for a manager's approval at night extends the incident.

### Automation is the next step, not a replacement
A runbook step that is run identically every time is a candidate for a script, then for automatic
remediation. Keep the runbook even after automation, describing what the automation does and what to do
when it fails, *because* automation fails in exactly the unusual situations where humans need context.

### Store them where the outage is not
A runbook is useless if it is unreachable during the incident it covers. Keep runbooks somewhere that
does not share fate with the system: not only on a wiki behind the same single sign-on that may be
failing, not only in a cloud account the customer's on-call cannot access, and with an offline or mirrored
copy for the scenarios (identity outage, network partition into an air-gapped site) where the primary
location goes down too. The alert should carry the link, *because* searching for the right page costs
minutes the incident does not have.

Terminology varies: some teams say *playbook* for the broader incident-handling process (roles,
communication, severity) and *runbook* for the per-situation procedure described here. The distinction
matters less than having both, and not expecting one to do the other's job.

### Keep them alive
Each runbook has an owner and a last-tested date. Teams rehearse them in game days (deliberately
injected failures in staging) and update them after every incident where the runbook was wrong or
missing. A runbook that has never been executed should be assumed not to work.

## Example
An abridged runbook for an LLM-backed assistant, written for a customer operations team after handoff.
Flag names, dashboards and thresholds are illustrative stand-ins for the team's own.

```markdown
### RB-07 LLM provider degraded (429s / 5xx / timeouts)
Trigger: alert `llm_upstream_error_rate > 5% for 5 min` (any tenant)
Impact: answers fail or slow down for all users. SEV2; SEV1 if > 25% for 15 min.

Confirm
1. Dashboard "LLM upstream" -> panel "errors by status code".
   429 = rate limit, 5xx/timeout = provider outage. Check the provider status page.
2. Panel "requests by tenant": is one tenant > 50% of traffic? If yes, go to step 6.

Mitigate (pre-authorised, no approval needed)
3. 5xx/timeouts: set flag `llm.route=fallback` (secondary provider/region). Expect
   slightly lower answer quality; banner `degraded_mode=on` informs users.
4. 429s, no single hot tenant: set `llm.max_concurrency=40` (default 80) to stop retry storms.
5. Pause batch jobs: set `jobs.summarise_nightly.enabled=false`.
6. One tenant flooding: set `tenant.<id>.rate_limit_rps=2`; notify their admin (template T-3).

Verify
7. Upstream error rate < 1% and p95 < 8 s for 15 min. Then revert step 3 first, watch 15 min,
   then restore concurrency, then re-enable batch jobs last.

Escalate
8. Not recovered 30 min after step 3, or fallback also failing: page vendor on-call
   (rota "assistant-l3"), open a ticket with the provider quoting request IDs from the trace.
Afterwards: export the incident timeline and five failing trace IDs to the postmortem doc.
```

Notice what it encodes: a split between rate-limit and outage causes, a hot-tenant branch, actions the
operator may take without approval, an explicit recovery condition, and an order for reverting changes.
The two branches exist *because* the same alert has opposite fixes: for a provider outage you move traffic
away, while for rate limiting you reduce your own pressure, and failing over a rate-limited workload can
simply exhaust the fallback's quota too. The revert order is deliberate as well: one change at a time,
with a watch period, so that if errors return you know which reverted change they depended on, and the
most load-generating change (batch jobs) comes back last, when the system has proven it is stable.

## Trade-offs
- Detailed, command-level runbooks make incidents fast for unfamiliar operators, but go stale quickly as
  the system changes; the cost flips when nobody owns updates, and a wrong runbook is worse than none.
- Pre-authorising disruptive actions shortens incidents, but risks over-reaction on false alarms; keep
  the confirm step strict so disruptive mitigation follows evidence.
- Automating remediation removes human delay, but hides the system's behaviour from operators; keep
  humans in the loop for actions with customer-visible or irreversible effects.
- Many narrow runbooks match alerts precisely, but become hard to navigate; a short index by symptom
  helps when the triggering alert is not obvious.

## Pitfalls
- Writing runbooks that explain the architecture instead of giving steps; the symptom is an on-call who
  read the page and still had to page the original engineer.
- Never testing them; the symptom is a step that references a flag, dashboard or command that was renamed
  months ago, discovered mid-incident.
- Alerts with no linked runbook; the symptom is repeated pages that each get the same ad-hoc response
  and no consistent mitigation time.
- Leaving out AI-specific situations such as cost spikes, guardrail-block surges or prompt-injection
  reports; the symptom is a generic "service degraded" response to a problem that needed a kill switch.
- Keeping runbooks only behind the system's own login or on infrastructure that fails with it; the
  symptom is an identity or network outage in which the on-call cannot open the page that tells them
  what to do.
- Writing them only for the vendor team; the symptom is a customer operations team that escalates every
  page during hypercare because the runbooks assume access and context they do not have.

## When to use
Write a runbook for every alert that pages a human and for every known failure mode a different team
will handle, before go-live and as a readiness-review item. Start with the situations most likely to
happen and most harmful when they do: provider outage, rate limits, cost runaway, index staleness, unsafe
output reports, authentication failures to customer systems. Measure them by time to mitigate, by how
often responders escalate despite the runbook, and by how often an incident finds a runbook wrong. In
interviews, describing alert-linked runbooks with pre-authorised mitigations and rehearsal shows
operational maturity beyond "we have monitoring".

## Role lens
- **Engineer:** Writes runbooks for the failure modes they built in, with exact flags and queries, and
  adds a kill switch or fallback path wherever a runbook needs one that does not exist.
- **Architect:** Ensures the system exposes the levers runbooks need (fallback routing, per-tenant limits,
  tool disablement) and defines which actions are pre-authorised for on-call.
- **FDE:** Rewrites runbooks for the customer's operators, in their tools and access model, and runs a
  game day with them before handoff so the procedures are proven in their environment.

## Check yourself
**Q:** During hypercare the customer's on-call escalated every one of six incidents to your team, even
though runbooks existed. What would you investigate and change?
**A:** I would review each incident with the on-call to see where they stopped. Common causes: the
runbook assumed access they lack (a vendor dashboard or cloud account); steps were vague ("check the
model") instead of exact; the confirm step did not distinguish their situation so they did not trust the
mitigation; or disruptive actions were not explicitly pre-authorised, so they waited for permission. Fixes
follow from the cause: rewrite with their tools and links, add decision branches, state pre-authorisation
in writing with their management's agreement, then run a game day so they execute each runbook once
under realistic conditions.

## Quiz
### Q: Why do runbooks put mitigation steps before diagnosis?
- [ ] Diagnosis is the vendor's responsibility, not the on-call's
- [x] Stopping user harm is urgent, while root cause can be found afterwards from preserved evidence
- [ ] Diagnosis cannot be written down in advance
- [ ] Mitigation steps are easier to automate
> The incident's cost accrues while users are affected, so restoring service comes first. Recording
> timelines and trace IDs preserves what the postmortem needs, so diagnosis is deferred, not lost.

### Q: Which runbook step is most likely to stall an incident?
- [ ] "Set flag llm.route=fallback"
- [ ] "If one tenant exceeds 50% of traffic, go to step 6"
- [x] "Check the logs for anything unusual"
- [ ] "Verify upstream error rate stays below 1% for 15 minutes"
> Vague instructions force the responder to reason from scratch, which is what runbooks exist to avoid.
> The other steps are exact actions, explicit branches or measurable recovery conditions.

### Q: A team automated a runbook's mitigation as an auto-remediation job. What should happen to the runbook?
- [ ] Delete it, since the automation now handles the situation
- [ ] Freeze it as historical documentation
- [x] Keep it, describing what the automation does and what to do if the automation fails or does not fire
- [ ] Convert it into a postmortem template
> Automation fails in unusual conditions, which is when operators most need context. The runbook becomes
> the fallback for the automation and explains its behaviour so responders do not fight it.
