---
id: scoping
title: Scoping
---

## Overview
This module covers the work between "the customer wants AI" and "the team is building the right thing,
against agreed numbers, with a path to production". It starts with understanding. Customer discovery
turns a requested solution into the underlying job, its current cost, the cost of an error and the
person who decides. AI use-case discovery ranks candidate ideas on value, fit for a probabilistic model,
data feasibility and error tolerance. Workflow analysis finds the step that actually limits the outcome.
Problem decomposition splits the goal into nodes with inputs, outputs, techniques and tests. Domain
modeling pins down what the customer's words mean, and data access and permissions discovery starts the
slowest approvals early. The second group makes value measurable: a metric tree with guardrails, a
baseline captured before anything changes, and an ROI model built as ranges with a break-even point.
The third group turns this into a commitment: a dependency and risk log, a statement of work with
statistical acceptance and dated customer obligations, and requirements traced to eval cases. The last
group moves from evidence to production through timeboxed prototypes and PoCs, demos that drive
decisions, a staged pilot plan with entry and exit criteria, and risk-ordered hardening. After the
module you can take a vague request, pick a use case worth doing, write a scope and SOW a buyer will
sign, prove or kill feasibility inside a timebox, and plan a pilot whose go/no-go is a reading of agreed
numbers. It matters most for FDEs, who run this with customers, and for Architects, who own the
decomposition, data access and production plan.

## Why it matters
- **Engineer:** Decomposition gives you nodes with contracts and tests instead of one untestable prompt, and the frozen eval set, traced requirements and timebox tell you what "done" means before you start tuning.
- **Architect:** You decide which nodes need a model and which are plain code, how the domain model projects into schemas, prompts and retrieval filters, how source permissions carry into the system, and what hardening production needs.
- **FDE:** You turn "build us a chatbot" into a signed scope: the real job, a ranked use case, a baseline, an ROI range, dated customer dependencies and acceptance criteria that survive a probabilistic model and a steering meeting.

## Mental model
Picture scoping as a funnel of decisions, each one narrowing what you build and each backed by
evidence. At the wide top are a customer request and a pile of ideas; discovery, use-case ranking and
workflow analysis narrow these to one job and the step that limits its outcome. Decomposition and the
domain model give that job a shape: nodes with contracts, words with fixed meanings, data with known
owners and permissions. Then comes the measuring stick, which you fix before building: a metric tree,
a baseline and an ROI range, which together say what success is worth and which number the pilot must
move. The SOW, risk log and traceability matrix turn that measuring stick into a commitment both sides
can check. Below that, every stage of execution (timeboxed PoC, demo, pilot, production) ends in an
explicit go, pivot or stop decision taken against the same numbers. If a stage cannot name its
question, its evidence and its decision rule, it is not scoped yet.

## Suggested order
1. customer-discovery — every later step depends on knowing the real job, who feels it, what it costs and who decides.
2. ai-use-case-discovery — narrows many ideas to a ranked shortlist on value, model fit, data feasibility and error cost.
3. workflow-analysis — maps the current process so the AI lands on the step that actually limits the outcome.
4. problem-decomposition — splits the chosen job into nodes with inputs, outputs, techniques and tests, separating model work from plain code.
5. domain-modeling — fixes the entities, states and vocabulary that the decomposed nodes, schemas and eval labels will share.
6. data-access-and-permissions-discovery — identifies sources, owners and permission rules for those entities early, because approvals have the longest lead time.
7. success-metrics-kpis — defines the outcome, driver, quality and guardrail metrics before architecture choices lock in.
8. baseline-measurement-before-deployment — measures those metrics under the current process, because the before number cannot be recovered later.
9. roi-model — turns metrics and baseline into a money range with sensitivity and break-even, which can kill or prioritise the use case.
10. dependency-and-risk-management — lists dated customer dependencies and sized risks, which the SOW must contain.
11. statement-of-work — commits scope, statistical acceptance, dated obligations and change control, built from everything above.
12. timeboxing — sets the question, budget and decision rule that keep uncertain work like a PoC from expanding without limit.
13. rapid-prototyping-poc — tests the riskiest assumption on real data inside a timebox and ends in a decision.
14. demos — presents PoC and pilot evidence to a named audience so it produces a decision rather than applause.
15. requirements-traceability — links SOW requirements to components, prompts and eval cases once the pilot build begins.
16. pilot-to-production-plan — stages the rollout with cohorts, entry and exit criteria, owners and rollback, measured against the baseline.
17. productionization — closes the gap between a working pilot and a system that survives real traffic, outages, model changes and audits.

## Capstone
### Brief
Scope an AI engagement end to end for a realistic customer: a mid-sized insurer's claims team that
has asked for "a chatbot to answer adjusters' policy questions". Work from a short written case (a
one-page sponsor brief, three interview transcripts you write or role-play, and a synthetic dataset of
a few hundred claims with timestamps and policy documents). Over a week, run discovery and reframe the
request into the underlying job, rank at least five candidate use cases on the four axes, and map the
current claims workflow to find the step that limits cycle time. Decompose the chosen use case into
nodes, write a glossary and one state machine for a claim, and build a data source register with owners,
access methods and permission models. Define a metric tree with guardrails, compute a baseline from the
synthetic logs, and build an ROI model with ranges, sensitivity and break-even. Write a phased SOW with
statistical acceptance, then run a two-day timeboxed PoC on the riskiest node and record a short demo
that ends in a stated decision. Finish with a pilot-to-production plan and a prioritised hardening list.

### Deliverables
- A discovery brief (one page) stating the job, current cost, cost of an error, success evidence and decision-maker, plus a ranked use-case table scored on value, model fit, data feasibility and error tolerance.
- A workflow swimlane with wait times and variant frequencies, a decomposition diagram where every node has an input, output, technique, owner and test, a domain glossary with a claim state machine, and a data source register with owner, access method, classification and permission model per source.
- A metric tree (outcome KPI, drivers, quality metrics, guardrails), a baseline report with means, variance, per-segment results and a human-quality audit, and an ROI spreadsheet with ranges, a sensitivity ranking and a break-even value.
- A RAID log with dated dependencies and risks sized in days, and a phased SOW with scope, out-of-scope items, statistical acceptance against a frozen eval set, dated customer obligations and change control.
- A PoC report from a two-day timebox with the question, pass/fail bar, results on real-shaped data and the decision, a recorded demo of ten minutes or less, a traceability matrix from requirements to eval cases, and a pilot-to-production plan with stages, cohorts, entry and exit criteria, rollback and a risk-ordered hardening list.

### Rubric
- The discovery brief names a job different from "chatbot" and supports it with quotes from the interviews, and the use-case ranking shows at least one high-value idea rejected for error cost or data feasibility, with the reason stated.
- The chosen AI step is the one the workflow map shows limits cycle time, and the decomposition multiplies per-node error rates to name the node that dominates end-to-end error.
- Every SOW acceptance criterion is a threshold on a named metric, on a frozen eval set with a stated size and slices, and every customer dependency has an owner and a need-by date that also appears in the RAID log.
- The ROI model's break-even value and its top-ranked sensitivity assumption are the numbers the pilot plan says it will measure, and the baseline uses the same metric definitions as the pilot.
- The PoC report ends with an explicit ship, continue, pivot or stop decision tied to its pass/fail bar, and the demo shows at least one failure case alongside eval numbers.
- Each pilot exit criterion maps to a metric in the tree, and every requirement in the traceability matrix links to at least one eval case and to the component or prompt that implements it.
