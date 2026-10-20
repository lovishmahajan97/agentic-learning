---
id: stakeholder-management
module: adoption
name: Stakeholder management
kind: practice
difficulty: core
est_minutes: 13
status: fact-checked
related:
  - strategy/stakeholder-alignment
  - adoption/exec-communication
  - adoption/change-management
  - adoption/workshops
  - scoping/dependency-and-risk-management
  - deployment/security-and-architecture-reviews
  - scoping/success-metrics-kpis
---

## TL;DR
Stakeholder management is the ongoing work of knowing who can help, block or be affected by an AI
deployment, what each of them needs to see, and engaging them on purpose rather than by accident. It
exists because AI projects often stall for reasons that have nothing to do with the model: when a security reviewer, data owner or
team lead who was never engaged says no late, or when a sponsor's expectations drift away from what the
system can deliver.

## Why it exists
An enterprise AI deployment touches more groups than most software projects. The sponsor funds it, a
process owner's team uses it, IT runs the integration, security and legal review the data flow, a data
owner controls access to the source systems, procurement handles the contract, and in some regions a works
council or union must be consulted when work changes. Each can slow or stop the project, and each defines
success differently: the sponsor wants a business metric, the users want less tedium and no blame,
security wants no new exposure, the data owner wants no surprises.

AI raises the stakes in two ways. First, sending company data to a model triggers reviews (data
classification, residency, vendor risk) that ordinary tools may not, and those reviewers have real veto
power. Second, quality is probabilistic, so expectations need active management: a sponsor who heard
"it works" in the demo will treat a 5% error rate as a failure unless someone set that expectation early.

The naive approach, talking mostly to the sponsor and the friendly champion, fails because it optimizes
for the people already on your side. Blockers are discovered when they block, typically at the security
review or the go-live decision, when changes are most expensive. Treating everyone the same (a weekly
update email to all) fails too: it is noise to most readers and too shallow for the few who decide.

## How it works
### 1. Identify everyone, including the quiet vetoes
List people and groups, not departments. For each, ask: can they approve, block, fund, or are they
affected? Probe specifically for the roles AI projects forget: data owners, security, privacy, legal,
compliance, IT operations, team leads of end users, and employee representatives. Ask each stakeholder
"who else should I talk to?", *because* informal vetoes are often invisible on the org chart.

### 2. Map influence, interest and stance
The power-interest grid sorts people into four groups: high power and high interest (manage closely),
high power and low interest (keep satisfied), low power and high interest (keep informed), low power and
low interest (monitor). Add a third attribute, current stance (supporter, neutral, skeptic, blocker), and
what each person needs to believe to support the project and what they fear. *Because* engagement effort is
finite, the grid tells you where to spend it.

### 3. Define decision rights
For each key decision (scope, data access, go-live, rollback), agree who is responsible, accountable,
consulted and informed (RACI). This prevents the classic stall where three people each think another one
approves the release, *because* an AI go-live usually needs sign-off from several functions.

### 4. Plan engagement per person
Manage-closely stakeholders get one-to-ones and early drafts. Security and legal get the architecture and
data-flow documentation before they ask, *because* a reviewer who receives a complete package early is
far less likely to block late. Users and team leads get workshops and pilots. Executives get short
decision-focused updates.

### 5. Maintain the map
Keep a register with owner, stance, last contact and next action, review it weekly, and update it when
people change roles. When a sponsor leaves, the successor inherits a project they did not choose and may cut it, so
build relationships with more than one senior supporter.

## Example
A minimal stakeholder register, the kind an FDE keeps alongside the project plan, with a check that flags
where attention is overdue. The rule encodes the grid: high-power people who are not supporters, or whom
nobody has spoken to recently, need action this week.

```python
from datetime import date

stakeholders = [
    # name, role, power 1-5, interest 1-5, stance, last contact
    ("Priya", "VP Ops (sponsor)",         5, 5, "supporter", date(2026, 10, 14)),
    ("Marc",  "CISO delegate",            5, 2, "skeptic",   date(2026, 9, 2)),
    ("Lena",  "Data owner, claims DB",    4, 2, "neutral",   date(2026, 9, 20)),
    ("Tom",   "Team lead, adjusters",     3, 5, "blocker",   date(2026, 10, 1)),
    ("Ana",   "Adjuster (champion)",      2, 5, "supporter", date(2026, 10, 16)),
]

def quadrant(power, interest):
    if power >= 4:
        return "manage closely" if interest >= 4 else "keep satisfied"
    return "keep informed" if interest >= 4 else "monitor"

def actions(today, stale_days=21):
    for name, role, p, i, stance, last in stakeholders:
        q, age = quadrant(p, i), (today - last).days
        flags = []
        if p >= 4 and stance != "supporter":
            flags.append(f"high power, {stance}")
        if stance == "blocker":
            flags.append("blocker: find out what they fear")
        if p >= 4 and age > stale_days:
            flags.append(f"no contact for {age} days")
        if flags:
            print(f"{name:5} {role:24} [{q}] -> " + "; ".join(flags))

actions(date(2026, 10, 20))
```

On this data the check flags Marc (skeptic with a veto, 48 days without contact: send the data-flow
package and book a review), Lena (neutral data owner, 30 days: confirm access terms) and Tom (a team lead
blocking adoption: a one-to-one about what he fears, usually blame for AI errors or his team's metrics).
The sponsor and the champion need nothing this week.

## Trade-offs
- Mapping everyone formally catches hidden vetoes, but costs time; a small internal tool with one team needs only a short list.
- Engaging blockers early surfaces objections while they are cheap, but can slow momentum; skip only when their approval is genuinely not required.
- A single strong sponsor makes decisions fast, but creates a single point of failure if that person moves; cultivate a second senior supporter.
- Tailoring messages per stakeholder improves relevance, but risks inconsistent stories; keep one source of truth that every message draws from.
- Sharing the error rate openly sets realistic expectations, but can alarm cautious stakeholders unless paired with the mitigation and the baseline.

## Pitfalls
- Talking mainly to supporters; the symptom is a surprise "no" from security or legal at the go-live review.
- Leaving decision rights implicit, revealed when go-live waits for weeks because nobody knows who signs off.
- Ignoring team leads of end users, which shows up as adoption varying sharply between teams with the same tool.
- Letting the sponsor's expectations drift from the eval results, so a normal error rate is read as project failure.
- Never updating the map after reorganizations, discovered when a new executive cancels a project they never heard of.

## When to use
Keep a stakeholder map on any AI deployment that crosses team boundaries, needs data access from another
owner, or requires security or compliance review, which covers most enterprise work. Build it in the first
week of scoping and review it weekly through rollout and hypercare. Measure it indirectly: no late vetoes,
decisions made on schedule, and sponsors whose expectations match the evals. For a small internal tool used
by one team, a short list of approvers is enough. In interviews, show that you look for blockers and veto
holders first, not just sponsors.

## Role lens
- **Engineer:** Produces the artifacts reviewers need, such as data-flow diagrams, eval results and access logs, and answers technical objections precisely.
- **Architect:** Owns the decision rights for architecture, data access and security sign-off, and makes sure reviewers see designs before they harden.
- **FDE:** Maintains the map inside the customer, finds informal vetoes by asking who else matters, and converts skeptics through one-to-ones.

## Check yourself
**Q:** Two weeks before go-live, the customer's security team, never formally engaged, asks to review the system and signals concern. How do you respond, and what would you have done differently?
**A:** Now: treat it as legitimate, not an obstacle. I would meet them immediately with a complete package (architecture and data-flow diagram, data classification, where data is stored and for how long, vendor terms, access controls, logging) and ask for their specific concerns in writing so we can resolve them one by one. I would tell the sponsor honestly that go-live may move. Earlier: security has a veto on any system that sends company data to a model, so they belong in the high-power quadrant from week one, with the data-flow design reviewed before integration was built.

## Quiz
### Q: On a power-interest grid, a security reviewer who rarely engages but can block go-live belongs where?
- [ ] Monitor, because their interest is low
- [ ] Keep informed, because they only need status updates
- [x] Keep satisfied, because high power means their objection can stop the project
- [ ] Manage closely, because every reviewer must attend weekly meetings
> High power with low interest means "keep satisfied": give them what they need to approve, early and
> complete. Monitoring them risks a late veto; weekly meetings waste their time.

### Q: Why define a RACI for the go-live decision on an AI deployment?
- [ ] To make the sponsor solely responsible for any errors
- [x] Because go-live usually needs several functions, and without explicit decision rights the release stalls
- [ ] Because RACI documents are required by most AI regulations
- [ ] To replace the need for security and compliance reviews
> AI go-lives typically need sign-off from operations, security, compliance and IT. A RACI makes the
> accountable approver explicit, so nobody waits for an approval nobody believes is theirs.

### Q: A team lead is openly discouraging his team from using the tool. What is the best first move?
- [ ] Escalate to the sponsor to mandate usage on his team
- [ ] Exclude his team from the rollout
- [x] Meet him one-to-one to learn what he fears, such as blame for AI errors or damage to team metrics
- [ ] Send his team additional training materials
> Blockers usually have a concrete concern. Understanding it allows a real fix; mandating through the
> sponsor tends to produce compliance theatre, and more training does not address his objection.
