---
id: adoption
title: Adoption
---

## Overview
This module is about the gap between "the system is live" and "the system is used and is paying for
itself". A deployed AI system can pass every eval and still fail, because people ignore it, work around
it or trust it too much. Stakeholder management maps who can help, block or be affected, and finds the
security reviewers, data owners and team leads who can veto late. Change management names the exact
behavior that must change, finds what blocks it for each group, and redesigns incentives and workflow
around the tool. Workshops pull the knowledge out of experts' heads into concrete artifacts: a mapped
workflow, an agreed rubric, labelled examples. User training teaches a working mental model of where the
system is reliable, how it fails and how to check its output. Usage analytics instruments what people do
with each output (accept, edit, regenerate, abandon) against an eligible population. Adoption and
value-realization measurement keeps "they use it" separate from "it changed a business outcome", using a
baseline or comparison group. Exec communication turns those numbers into a conclusion-first update with
a clear ask. Continuous improvement and feature-request triage route production evidence and customer
asks into measured fixes or reasoned roadmap decisions. Handoff moves ownership to the customer's team
through staged, demonstrated exit criteria. Afterwards you can plan and run a rollout, instrument it,
prove or disprove its value with a defensible number, and leave behind a team that can run it. It
matters most for FDEs, and for Architects who own a platform that has to show value to keep its budget.

## Why it matters
- **Engineer:** You build the disposition events (accepted unchanged, edit size, regenerate, abandon) without copying raw prompts into the warehouse, and you run the improvement loop: classify failures, fix at the cheapest layer, add the cases to a frozen regression suite, gate the release, and check that the targeted failure bucket actually shrank in production.
- **Architect:** You design measurement in from the start: an eligible-population denominator, a baseline or comparison group agreed during scoping, guardrail metrics beside the value metric, and system boundaries (repos, CI, monitoring, identities) chosen so the customer team can own the system after handoff without a rebuild.
- **FDE:** You are the person in the room. You map stakeholders and veto holders in week one, run the workshops that settle quality criteria, train users to verify outputs, triage asks without promising forks, report to the sponsor with the conclusion and the ask first, and hand over only when the customer team has shown it can ship a gated change and handle an incident.

## Mental model
Picture adoption as a funnel with a loop beside it. The funnel runs from eligible people, to people who
try the tool, to people who use it for the intended work, to work that comes out measurably better than
the baseline. Each layer leaks for its own reason, and each lesson is a tool for one leak. People who
never start are a stakeholder or change problem. People who try and drop off are usually a training,
trust or workflow problem. People who use it without effect are a quality or scoping problem. Usage
analytics shows where the funnel leaks, and value measurement shows what reaches the bottom. The loop
beside it takes what leaks out (failures, complaints, feature asks), sorts it by type, and feeds it back
as fixes, training or roadmap decisions. Exec communication reports the funnel's shape to whoever funds
it. Handoff is the moment the loop's owner changes. Usage is the leading indicator you act on weekly;
realized value is the lagging number that renews the contract. Never report one as the other.

## Suggested order
1. stakeholder-management — find the sponsors, users and veto holders first, because every later step depends on knowing who must agree.
2. change-management — define the behavior change and its blockers per group, which sets the target for training, analytics and measurement.
3. workshops — the main tool for getting knowledge and agreement out of those stakeholders as concrete artifacts such as rubrics and workflows.
4. user-training — teaches users the mental model and verification habits the change plan calls for, with drills rather than feature tours.
5. usage-analytics — instruments what users do with each output, so you can see whether the change and training are working.
6. adoption-and-value-realization-measurement — builds on usage data, adding a baseline or comparison group to separate engagement from business value.
7. exec-communication — turns adoption and value numbers into conclusion-first updates and decisions for the people who fund the work.
8. continuous-improvement — the operating loop that turns classified production failures into gated, measured fixes.
9. feature-request-triage — routes customer asks, separating real features from quality failures, training gaps and model limits that belong in the improvement loop.
10. handoff-to-customer-teams — the last step, which moves ownership of the whole loop to the customer once they have shown they can run it.

## Capstone
### Brief
You are the forward-deployed engineer on an AI drafting assistant for a mid-size insurer's claims team.
It drafts replies to policyholder emails from the claim file and policy documents. It has been live for
four weeks across 180 eligible adjusters in three regional teams. The sponsor, the head of claims
operations, was sold "30% faster first response". The dashboard shows 1,400 requests a week, and the
sponsor wants to know whether to expand to another 400 adjusters next quarter. Over a week, using a
synthetic or anonymised event log and claim dataset (provided or generated), find out what is really
happening and set the deployment up to succeed after you leave. Map the stakeholders and veto holders.
Write the behavior statement and change plan. Design the usage event schema and adoption dashboard.
Measure realized value against a baseline or comparison group. Run a quality-criteria workshop, design
training with verification drills, triage a backlog of 30 raw asks, run one improvement cycle, write the
expansion recommendation for the sponsor, and draft a staged handoff plan for the insurer's own team.

### Deliverables
- A stakeholder map covering at least eight roles (sponsor, team leads, adjusters, IT, security, data owner, compliance, works council or HR), each with what they need, their influence or veto power, and a planned engagement and cadence.
- A change plan with a one-sentence behavior statement, the blockers for each group, and incentive or workflow changes, plus a training plan with at least five verification drills built from real failure types and a measure for each.
- A usage event schema with no raw prompt or claim content, and a dashboard showing weekly active over eligible, AI-assisted share of replies, week-4 cohort retention, and accepted-unchanged, edit-size and regeneration rates sliced by team and prompt version.
- A value-realization report that compares first-response time against a baseline or comparison group, gives a range rather than a point, and breaks the gap between the projected 30% and the realized figure into adoption, quality and attribution parts.
- A workshop agenda and output for agreeing a reply-quality rubric, a triaged backlog of the 30 asks with a classification, cluster, score and route for each, and one completed improvement cycle with new regression cases and before/after bucket rates.
- A one-page executive update recommending expand, hold or re-scope, with the decision needed and the date, and a staged handoff plan with exit criteria the insurer's team must demonstrate.

### Rubric
- The value figure is credited to the assistant only against a baseline or comparison group, comes with a stated range, and explicitly rules out at least one confounder such as early adopters being the fastest adjusters.
- The dashboard never reports a raw request count without a denominator, and at least one team-level difference is traced to a cause (team lead, incentive or training) rather than left as noise.
- The executive update gives its recommendation and the decision needed in the first two sentences, translates the error rate into cases and cost, and a reader can restate the ask after one read.
- At least a third of the 30 asks are reclassified as quality failures, training gaps or model limits and routed to the matching place, and every declined ask has a stated reason sent back to the requester.
- The improvement cycle adds regression cases before the fix, shows no regression on the frozen suite, and reports the targeted failure bucket's rate before and after.
- Handoff exit criteria are tasks the customer team performs (a gated prompt change, a simulated incident, a credential rotation away from personal accounts), not dates or documents delivered.
