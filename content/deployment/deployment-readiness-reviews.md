---
id: deployment-readiness-reviews
module: deployment
name: Deployment readiness reviews
kind: practice
difficulty: core
est_minutes: 14
related:
  - deployment/security-and-architecture-reviews
  - deployment/uat
  - deployment/rollout-criteria-and-rollback-triggers
  - deployment/runbooks
  - deployment/hypercare
  - llmops-platform/evaluation-gates-for-production-releases
  - evals/task-specific-acceptance-criteria
  - scoping/pilot-to-production-plan
sources:
  - Beyer, Jones, Petoff, Murphy, 2016 — Site Reliability Engineering
---

## TL;DR
A deployment readiness review is a scheduled go/no-go decision, made before production traffic
arrives, in which every launch requirement (quality evidence, security sign-off, capacity, monitoring,
rollback, support ownership) is checked against written evidence by a named owner. It exists because
launch risk is spread across teams who each assume someone else covered the gap; the review turns
"we think we're ready" into a list of items that are either proven, waived with an expiry, or blocking.

## Why it exists
An AI system going live in a customer environment touches many owners at once: the engineers who built
the prompt and retrieval pipeline, the customer's security team, their network and identity teams, the
business owner who defined success, and whoever will be paged at 3 a.m. Each group checks its own slice.
Nobody checks the seams: the eval suite passed, but on last month's index; the security review approved
the architecture, but not the new tool the agent gained afterwards; dashboards exist, but no alert routes
to a human; a rollback plan exists, but nobody has run it.

The naive alternative is "the tech lead decides we're ready", usually under a date that was promised to
a sponsor. That fails for two reasons. First, one person cannot hold the full checklist in their head, and
the items they forget are precisely the ones outside their expertise. Second, schedule pressure biases
the decision: without criteria written down in advance, every gap gets rationalised as "we'll fix it in
hypercare". The review exists to make the decision *structural*: criteria agreed before the date, evidence
instead of assertions, and an explicit record of which risks were accepted and by whom. Google's SRE
practice formalised a version of this as the Production Readiness Review, run before an SRE team agrees
to carry the pager for a service; the same logic applies when a customer's operations team is about to
inherit your system.

## How it works
### 1. Agree the criteria early
The checklist is drafted at the start of the build, not the week before launch, because criteria written
after the system exists tend to describe what was built rather than what is needed. For an AI system the
categories are usually:

- **Quality:** eval results on the golden set and customer-specific regression suite meet the acceptance
  criteria agreed in scoping; UAT is signed off; known failure modes are documented.
- **Safety and security:** security and architecture review approved for the *current* design; guardrails
  tested against the red-team set; data handling (retention, PII, residency) matches the contract.
- **Operability:** health checks, dashboards and alerts exist and route to a named on-call; every alert
  links to a runbook; logs and traces are retained long enough to debug an incident.
- **Capacity and cost:** load test at expected peak plus headroom; provider quotas and rate limits
  confirmed in writing; per-request and daily cost ceilings enforced, not just estimated.
- **Release mechanics:** rollout stages, promotion criteria and rollback triggers defined; rollback
  rehearsed in a production-like environment; feature flag or kill switch verified.
- **People:** support model and escalation paths agreed; customer team trained; hypercare staffed.

### 2. Evidence, not assertions
Each item has an owner and a link to evidence: an eval report with its dataset version, a load-test
result, a screenshot of an alert firing in staging, the ticket where security signed off. The rule is
*because* "done" is the cheapest word to say and the most expensive to be wrong about. An item without
evidence is treated as not done.

### 3. Classify every item
Items are **blocking** (no launch without them, e.g. rollback works, PII is not logged) or
**non-blocking** (launch can proceed if a gap is accepted). A non-blocking gap becomes a **waiver**: a
named risk owner, a compensating control, and an expiry date. Waivers expire *because* "temporary"
exceptions without dates become permanent.

### 4. Decide and record
The meeting is short when the prep is good: walk the open items, not the green ones. Outcomes are *go*,
*conditional go* (launch with listed waivers), or *no-go* with the specific items that must change. The
decision and its waivers are written down, because when an incident happens in week two, the first
question will be "did we know about this?".

### 5. Re-run on material change
A review approves a specific configuration: model version, prompt, tools, index, data sources. Swapping
the model or giving an agent a write-capable tool is a new launch for review purposes, scaled down to the
items the change touches.

## Example
A readiness gate kept as data next to the code, so the go/no-go is computed rather than argued. The
scenario: an insurer's claims-triage assistant, two weeks before go-live.

```python
from datetime import date

items = [  # (id, blocking, evidence_link_or_None, passed, waiver)
    ("eval.golden_set>=0.85",   True,  "evals/run-0412.json", True,  None),
    ("security.review_current", True,  "SEC-2291",            True,  None),
    ("rollback.rehearsed",      True,  None,                  False, None),
    ("alerts.route_to_oncall",  True,  "staging-alert.png",   True,  None),
    ("load.peak_x2",            False, "load/0409.html",      False,
        {"owner": "cust-ops-lead", "control": "rate limit 20 rps", "expires": date(2026, 11, 30)}),
    ("training.agents_trained", False, None,                  False, None),
]

def decide(items, today):
    blockers, waived, gaps = [], [], []
    for iid, blocking, evidence, passed, waiver in items:
        ok = passed and evidence is not None          # no evidence means not done
        if ok:
            continue
        if blocking:
            blockers.append(iid)
        elif waiver and waiver["expires"] > today:
            waived.append((iid, waiver["owner"], waiver["expires"]))
        else:
            gaps.append(iid)                            # non-blocking but unowned: still a no-go
    if blockers or gaps:
        return "NO-GO", blockers + gaps
    return ("CONDITIONAL GO" if waived else "GO"), waived

print(decide(items, date(2026, 10, 20)))
# ('NO-GO', ['rollback.rehearsed', 'training.agents_trained'])
```

Rehearsing the rollback and either training the agents or writing a waiver for it turns this into a
conditional go, with the load-test gap explicitly owned by the customer's ops lead until 30 November.

## Trade-offs
- A formal review catches cross-team gaps, but adds calendar time; for a low-risk internal tool a
  lightweight checklist in the pull request is enough, and a full meeting is ceremony.
- Strict blocking criteria prevent bad launches, but if everything is blocking the team learns to game
  evidence; keep the blocking list short and reserve it for irreversible or customer-visible harm.
- Waivers let launches proceed under known risk, but each one is debt; the trade flips when waivers
  outnumber fixes across releases, which means the criteria or the schedule are wrong.
- A central review board brings consistency across customers, but becomes a bottleneck at scale; mature
  teams move to self-service checklists with automated evidence and reserve humans for exceptions.

## Pitfalls
- Running the review the day before launch, so every finding becomes a waiver; the symptom is a
  "conditional go" with more conditions than the previous three launches combined.
- Accepting "yes, done" without a link; the symptom is an incident where the alert, runbook or rollback
  that was marked green turns out never to have existed.
- Reviewing the architecture that was approved months ago rather than what is deploying now; the
  symptom is a tool, data source or model in production that no security reviewer ever saw.
- Waivers with no expiry or owner; the symptom is the same accepted risk appearing in the next
  quarter's review with nobody able to say who agreed to it.
- Treating AI quality as a single number; the symptom is a launch that passed the aggregate eval but
  fails a critical slice (one claim type, one language) that the business cares most about.

## When to use
Hold one before any first production launch, before handing operations to a customer team, and before
material changes: a new model or provider, a new tool with side effects, a new data source, a new
region. Scale it to risk: a prompt tweak behind a flag needs an automated eval gate, not a meeting; an
agent that can issue refunds needs the full review. Measure the review itself by what escapes it: track
incidents in the first weeks after launch and ask whether a checklist item would have caught each one,
then add it. In interviews, a strong answer describes criteria, evidence, waivers with expiry, and the
re-review trigger, not just "we had a launch checklist".

## Role lens
- **Engineer:** Produces the evidence: versioned eval reports, load-test results, a rehearsed rollback,
  and alerts proven to fire, linked from each checklist item rather than described.
- **Architect:** Owns the criteria and the blocking list, decides which changes trigger re-review, and
  signs off on which risks the organisation is willing to accept with waivers.
- **FDE:** Runs the review with the customer's security, ops and business owners in the room, so their
  sign-offs and accepted risks are recorded before the go-live date is defended.

## Check yourself
**Q:** Two days before go-live, the load test shows the system handles only 60% of forecast peak because
the customer's provider quota is lower than requested. The sponsor wants to launch. What do you do?
**A:** First classify the gap: is overload customer-visible harm or graceful degradation? If the system
has a rate limiter and a clear "busy, try again" path, capacity can be non-blocking, so I would propose
a conditional go with a waiver: the customer's ops lead owns the risk, the compensating control is a
rate limit plus a staged rollout capped below 60% of peak, and the waiver expires when the quota
increase lands. If overload would cause silent failures or timeouts mid-workflow, it is blocking and the
answer is no-go or a reduced launch scope. Either way the decision is written down with its owner.

## Quiz
### Q: What most distinguishes a readiness review from a team simply agreeing they are ready?
- [ ] It is attended by more senior people
- [x] Each criterion is agreed in advance and judged against linked evidence with a named owner
- [ ] It replaces the need for evals and load tests
- [ ] It always happens after the first week of production traffic
> The value is structural: criteria fixed before schedule pressure, evidence instead of assertions, and
> recorded ownership. Seniority alone does not prevent unchecked seams, and the review consumes evals rather than replacing them.

### Q: A non-blocking item failed. What makes it acceptable to launch anyway?
- [ ] The tech lead believes it will be fixed during hypercare
- [ ] The item is moved to the backlog with a low priority
- [x] A waiver with a named risk owner, a compensating control and an expiry date
- [ ] The sponsor approves the launch date
> A waiver makes the accepted risk explicit and temporary. Backlog items and verbal assurances have no
> owner or deadline, so the gap silently becomes permanent and nobody is accountable when it bites.

### Q: Which change most clearly requires re-running at least part of the readiness review?
- [ ] Rewording a help-text string in the UI
- [ ] Rotating an API key on its normal schedule
- [ ] Adding a new dashboard panel
- [x] Giving the agent a new tool that can write to the customer's CRM
> A review approves a specific configuration. A write-capable tool changes the security surface, the
> failure modes and the rollback story, so the affected items (security, guardrails, runbooks) must be re-checked.
