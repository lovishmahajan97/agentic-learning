---
id: stakeholder-alignment
module: strategy
name: Stakeholder alignment
kind: practice
difficulty: core
est_minutes: 14
related:
  - adoption/stakeholder-management
  - adoption/exec-communication
  - scoping/success-metrics-kpis
  - scoping/baseline-measurement-before-deployment
  - evals/task-specific-acceptance-criteria
  - adoption/change-management
  - strategy/ai-roadmap
---

## TL;DR
Stakeholder alignment is getting the people who fund, approve, operate, use or can veto an AI system to
agree, before building, on the goal, how success is measured, what error rate is acceptable and what
happens on a failure, and who decides what. AI makes this harder than ordinary software because the
system is right only some of the time, so "does it work?" has no answer until stakeholders have agreed
on the operating point. The ongoing relationship work is covered under stakeholder management.

## Why it exists
An AI project touches more parties than a typical feature: a business owner who wants a cost or speed
gain, the operators whose workflow changes, legal and compliance who own regulatory risk, security who
owns data exposure, IT or the platform team who must run it, finance who funds the inference bill, and
sometimes works councils or unions. Each has a different definition of success, and several have an
effective veto.

The naive approach is to align on the vision ("AI-assisted claims triage") and start building, expecting
details to settle later. It fails because the disagreements are not about the vision, they are about the
numbers. The operations lead wants most claims handled automatically; compliance wants zero wrongful
denials; finance wants a cost per claim below today's. With a probabilistic system these goals trade off
directly against each other, and if the trade-off is not settled up front it gets settled at launch,
usually by a late veto. Projects then die in "pilot purgatory": the demo impressed, the eval looks good to
engineering, but there was never an agreed bar, so nobody can say yes. A related failure is the implicit
standard of perfection: without an agreed baseline, any visible AI error is judged against an imagined
error-free human process rather than the real one.

## How it works
### 1. Map who matters and what each can do
List stakeholders with their stake (what they gain or lose), their power (fund, approve, veto, adopt,
block quietly) and their main fear. Pay most attention to those who can veto late: legal, security, data
protection, and the frontline users whose non-adoption is a silent veto. Engaging them first is cheaper
*because* objections raised before design change a requirement; raised after build, they change a plan.

### 2. Elicit success criteria and red lines separately
For each stakeholder ask what outcome they need (a metric) and what they cannot accept (a constraint).
Constraints are hard limits ("no customer data leaves the EU", "no automated denial without human
review"); outcomes are things to optimise. Keeping them separate prevents a constraint being traded away
in a negotiation over an outcome.

### 3. Measure the baseline
Measure the current process on the same metrics: human error rate, cycle time, cost per item. This
replaces "AI must never be wrong" with "AI plus review must beat today's process by an agreed margin",
which is a standard a system can actually meet.

### 4. Agree the operating point with evidence
Run an offline eval on representative data and show the trade-off curve: as the confidence threshold for
automation rises, fewer items are automated and fewer are wrong. Stakeholders then choose a point on a
real curve instead of arguing in the abstract. This is the AI-specific heart of alignment.

### 5. Write it down in one page
Goal, primary metric with baseline and target, hard constraints, the operating point and why, failure
handling (fallback to humans, escalation path, rollback trigger), decision rights (who approves launch,
who can pause it; a RACI or DACI model works), and review cadence. A written artefact matters *because*
memories of verbal agreements diverge within weeks.

### 6. Re-align at each gate
Present results against the one-pager at each roadmap gate. If results move the trade-off (accuracy is
lower than hoped, costs are higher), re-open the decision explicitly rather than drifting.

## Example
A worked scenario: claims triage at an insurer, three stakeholders with conflicting goals. Figures are
illustrative.

- **Operations VP:** wants at least 50% of claims routed automatically.
- **Compliance:** red line, wrong-queue errors on auto-routed claims must be no worse than today's manual
  error rate, measured at 4%.
- **Finance:** cost per claim below today's $3.10.

The team runs the classifier on 1,000 labelled historical claims and tabulates by confidence threshold:

| Threshold | Share auto-routed | Error rate on auto-routed | Blended cost per claim |
|---|---|---|---|
| 0.70 | 78% | 6.5% | $1.20 |
| 0.85 | 61% | 3.2% | $1.75 |
| 0.95 | 38% | 1.1% | $2.30 |

At 0.70, ops gets its volume but the compliance red line is breached. At 0.95, compliance is comfortable
but ops is well short of 50%. At 0.85 all three are satisfied: 61% automated, 3.2% error (below the 4%
baseline), and cost well under $3.10. The one-pager records 0.85 as the operating point, a rollback
trigger if the production error rate on a weekly audited sample exceeds 4%, compliance as the owner of
that trigger, and a review after a month of production data. The argument that would have happened at
launch happened in a one-hour meeting with a table on the screen.

## Trade-offs
- Up-front alignment prevents late vetoes, but delays the start; for a reversible internal experiment, a
  lighter agreement on the stop condition is enough.
- Involving many stakeholders surfaces constraints early, but slows decisions; give advisory voice
  broadly and decision rights narrowly.
- Writing explicit numeric bars makes go/no-go decisions clear, but commits you before the data is fully
  understood; set them as provisional and re-open at each gate with evidence.
- Showing the full error trade-off builds trust with risk owners, but exposes failure rates some sponsors
  would rather not see; hiding them only moves the discovery to launch, where it costs more.

## Pitfalls
- Aligning on vision instead of numbers; the symptom is a successful pilot that nobody can approve
  because nobody agreed what "good enough" meant.
- Engaging legal, security or data protection only before launch; the symptom is a late veto or a
  redesign around a constraint that was knowable on day one.
- No baseline for the human process, so any visible AI error is compared with perfection and the
  project stalls on anecdotes.
- Treating frontline users as recipients rather than stakeholders; adoption stays low while every
  dashboard looks green, a silent veto.
- Decision rights left implicit; when production metrics wobble, two executives give contradictory
  instructions and the team freezes.

## When to use
Do this before significant build work on any AI system that changes a workflow, touches regulated data,
or will need sign-off from people outside the building team, which is most of them. Revisit at each
roadmap gate and whenever evidence moves the trade-off. Skip the heavy version for internal experiments
that are cheap to stop. Measure alignment by whether a launch decision can be made from the one-pager and
the latest eval without a new negotiation. In interviews, describe turning conflicting goals into a
choice on a measured trade-off curve.

## Role lens
- **Engineer:** Produces the threshold trade-off table and the baseline measurement that let stakeholders
  choose an operating point with evidence.
- **Architect:** Translates stakeholders' red lines into hard design constraints (residency, human review
  gates, audit trails) and owns the decision-rights section.
- **FDE:** Runs the alignment meetings inside the customer, identifies the quiet vetoes early, and keeps
  the one-pager current as the deployment evolves.

## Check yourself
**Q:** The business sponsor says the AI must be "100% accurate" before launch. How do you move the
conversation forward?
**A:** I would not argue with the goal; I would make the comparison concrete. First measure the current
process: how often human handlers make the same kind of error, and what that costs. Then show the eval
results as a trade-off: at a given confidence threshold, what share of items the system handles, its error
rate on those, and what happens to the rest (human review). The question becomes "which operating point
beats today's process, with which safeguards", plus a rollback trigger if production errors exceed the
baseline. Most sponsors accept a measured improvement with a safety net once they see the human error
rate.

## Quiz
### Q: Why separate stakeholders' hard constraints from their outcome goals?
- [ ] Constraints are only relevant to legal teams
- [x] So a red line cannot be traded away during negotiation over metrics that are meant to be optimised
- [ ] Outcomes are always less important than constraints
- [ ] Constraints are easier to measure than outcomes
> Outcomes such as automation rate are legitimately traded against each other; constraints such as data
> residency are not. Mixing them invites treating a red line as one more weight in the balance.

### Q: What makes alignment on AI systems harder than on deterministic features?
- [ ] AI systems need more stakeholders by regulation
- [ ] Stakeholders cannot understand model architectures
- [x] Goals like automation rate and error rate trade off directly, so an operating point must be chosen
- [ ] AI systems cannot be measured before deployment
> A probabilistic system is right only part of the time, so "does it work" depends on the chosen
> threshold. Stakeholders with different goals must agree on a point on that trade-off curve.

### Q: What is the main purpose of measuring the human baseline before deployment?
- [ ] To show that the human team should be reduced
- [ ] To train the model on human mistakes
- [x] To replace an implicit perfection standard with a realistic bar the system can be judged against
- [ ] To satisfy a requirement of most model providers
> Without a baseline, every AI error is compared with an imagined error-free process. Measuring today's
> error rate and cost gives an agreed, achievable bar for the go/no-go decision.
