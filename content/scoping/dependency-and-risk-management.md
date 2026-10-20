---
id: dependency-and-risk-management
module: scoping
name: Dependency & risk management
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - scoping/statement-of-work
  - scoping/data-access-and-permissions-discovery
  - scoping/timeboxing
  - scoping/rapid-prototyping-poc
  - scoping/pilot-to-production-plan
  - reliability/fmea
  - security-governance-compliance/ai-risk-management
  - infra-capacity-economics/api-provider-quotas-and-regional-limits
sources:
  - Official docs: NIST AI Risk Management Framework (AI RMF 1.0)
---

## TL;DR
AI projects are rarely late because of code. They are late because of things the team does not
control: data exports, access, security reviews, SME time, provider quotas. Dependency management maps
those inputs with owners, lead times and need-by dates. Risk management lists what could go wrong,
sizes each risk in days or money, and attacks the biggest ones first with cheap spikes, so the worst
surprises arrive in week one rather than week nine.

## Why it exists
A typical enterprise AI pilot plan says "weeks 1–2: data ingestion". In practice, week 1 is spent
learning that the document system's export needs a ticket to a team with a three-week queue. Week 3
reveals that a third of the documents are scanned images. Week 5 brings the security review's verdict
that customer data cannot go to an external API in that region. None of these is an engineering
failure, yet each one moves the delivery date, and the team takes the blame because nobody wrote the
dependency down with an owner and a date.

The naive approaches fail predictably. A risk list written once for the kickoff deck and never
reopened gives no protection: it records risks and drives no action. Rating everything "medium" makes
it impossible to prioritise. Treating risks as things to watch rather than things to retire leaves the
biggest unknowns, such as data quality, security approval and feasibility on real inputs, unresolved
until late, when there is no slack left to absorb them. AI work also carries risks ordinary software
projects do not have. The data may not support the task. The eval set may not represent production.
Model behaviour can change when a provider updates or retires a version. Quotas and regional
availability can block a deployment design that worked in a demo.

## How it works
### Keep a RAID log
Keep one living log of **R**isks (things that might happen), **A**ssumptions (things you are treating
as true but have not verified), **I**ssues (risks that have already happened) and **D**ependencies
(inputs owned by someone else). The categories matter *because* each one triggers a different action.
An assumption gets verified. A risk gets a response. An issue gets resolved now. A dependency gets
chased by its owner.

### Map dependencies
For each dependency, record what is needed (in a specific format), who owns it (a named person, not
"IT"), the realistic lead time, the need-by date derived from the plan, and the status. Then find the
**critical path**: the chain of dependent tasks with zero slack, such as data export → eval set →
PoC → pilot sign-off. Anything on that chain slips the end date day for day. Anything off it has slack
you can spend. Start the longest-lead items first. Security reviews, legal agreements and procurement
often take longer than the engineering, so they should start in week zero, before anything is built.

### Identify risks by category
Walk a checklist so you do not rely on memory:
- **Data:** availability, quality, volume, representativeness, rights to use.
- **Access and security:** credentials, network paths, approval of external model APIs, data residency.
- **Technical and model:** feasibility on real inputs, latency or cost at volume, provider quotas,
  deprecation of a model version.
- **People and adoption:** SME availability, sponsor turnover, user resistance.
- **Commercial and vendor:** pricing changes, contract terms, lock-in.
- **Regulatory and harm:** whether outputs could harm users or breach rules. This overlaps with
  AI-system risk management (frameworks such as the NIST AI RMF), which is covered in its own lesson.

### Assess in units people can act on
Score each risk by likelihood and impact. Where possible, express impact in schedule days or money,
not "high". The expected delay, probability × impact, can be compared directly with the slack on the
affected path. A 50% chance of a 15-day slip on the critical path is 7.5 expected days, which outranks
a 90% chance of a 2-day slip on a path with 10 days of slack.

Expected delay ranks risks, but it is not the whole story. A 10% chance of a 60-day slip has the same
6 expected days as a near-certain 6-day slip, yet only the first can end the engagement. For risks
whose impact would be fatal, such as the pilot being unable to run at all or a budget window closing,
plan by impact rather than by expected value: buy them down early or prepare a contingency whatever
their expected days say.

### Choose a response and a trigger
For each significant risk, choose one of four responses. **Avoid** it by changing the plan. **Reduce**
it with a spike, a fallback or an earlier start. **Transfer** it to the party best placed to own it,
for example by making a data date a customer obligation in the SOW. Or **accept** it with a
contingency plan. Give each risk an early-warning trigger, such as "export not received by day 10".
That way, switching to the contingency is a pre-agreed step rather than a debate held in a crisis.

### Retire the biggest unknowns first
Order early work by the uncertainty it removes, not by the order the architecture suggests. A two-day
spike on 50 real documents removes more risk than two weeks of polished pipeline code over synthetic
ones. Review the log weekly at the steering meeting, with escalation thresholds agreed in the SOW.

## Example
A 10-week pilot of a contract-review assistant for a legal department. All figures are illustrative.
At kickoff the RAID log holds these top items:

| ID | Item | Owner | Prob | Impact | Exp. days | Response and trigger |
|----|------|-------|------|--------|-----------|----------------------|
| D1 | DMS export of 3,000 contracts | Customer IT | — | critical path | — | Ticket raised day 0; need-by end of week 2 |
| R1 | Security review rejects external LLM API | Customer CISO | 0.3 | 20 days | 6 | Start review day 0; adapter layer allows in-region hosted model |
| R2 | Many contracts are scans with poor OCR | Delivery lead | 0.5 | 10 days | 5 | Spike on 50 real files in week 1 |
| R3 | SME lawyers miss labelling sessions | Customer GC | 0.6 | 5 days | 3 | Book sessions now; trigger if <75% attended in week 3 |
| R4 | Provider quota too low for batch backfill | Engineer | 0.2 | 3 days | 0.6 | Request increase week 1; accept |

D1, then R1 and R2, sit on the critical path, because the eval set needs the export and the pilot
needs security approval. Plan slack is 5 days. The expected delays from R1 and R2 alone (11 days)
exceed it, so the plan as drawn is not credible. The team acts accordingly. It submits the security
questionnaire on day 0, not when the pilot is ready. It runs the OCR spike in week 1. The spike finds
that 30% of files are scans and that OCR is acceptable on 80% of them, so R2 drops to a 3-day impact.
It also writes D1's need-by date into the SOW, so that a late export moves the schedule. In week 4 the
security review asks for an in-region deployment. Because R1 had a contingency, the switch takes 4 days
instead of the 20 it would have cost unplanned.

## Trade-offs
- A detailed RAID log catches more surprises but costs steering time every week. Keep the top ten
  items active and archive the rest, unless the engagement is regulated or multi-vendor.
- Front-loading spikes removes uncertainty early but delays visible demo progress. That trade is worth
  making whenever a feasibility or data risk sits on the critical path.
- Transferring risk to the customer through dated obligations protects the schedule but can strain
  the relationship. Frame it as shared planning, and only for items they genuinely control.
- Building contingency architecture, such as a provider adapter or in-region fallback, costs effort
  upfront. It pays off when security or provider approval is uncertain, and is waste when approval is
  already confirmed.
- Quantified scoring (probability × days) gives real prioritisation but implies false precision. Treat
  the numbers as ranking tools, not forecasts, when the estimates are rough.

## Pitfalls
- Writing the risk list once at kickoff. The symptom is a deck slide that still lists "data quality"
  as medium in week 8, after it has already caused a slip.
- Naming teams instead of people as owners. The symptom is a dependency marked "with IT" for three
  weeks with nobody able to say who is working on it.
- Starting security and legal reviews only when the system is ready. The symptom is a finished pilot
  that waits a month for approval to touch production data.
- Accepting risks without triggers or contingencies. The symptom is a crisis meeting debating options
  that could have been decided calmly in week one.
- Ignoring model and provider risks. The symptom is a deployment design that fails on quota, regional
  availability or a deprecated model version in the final week.

## When to use
Start the dependency map and RAID log during scoping, before the SOW is signed, because dated customer
dependencies and known risks belong in the SOW itself. Then review the log weekly for the life of the
engagement. Even a two-week internal PoC benefits from a ten-line version naming the data, access and
people it depends on. Measure how the log is working: count dependencies past their need-by date, compare the expected
delay with slack on the critical path, and track total exposure (the sum of expected days on the
critical path) week by week. Exposure should fall as spikes retire unknowns. A flat or rising curve
means the work is not attacking the risks. In interviews and on site, expect "what could
stop this project?" The strong answer names specific, owned dependencies and the spike that would
retire the biggest unknown first.

## Role lens
- **Engineer:** Runs the early spikes that retire technical and data risks, and builds the adapters
  and fallbacks that turn contingencies into small switches.
- **Architect:** Identifies architectural risks such as data residency, provider lock-in, quotas and
  deprecation, and designs seams so the contingency is cheap to execute.
- **FDE:** Owns the RAID log with the customer, chases named owners for access and data, and escalates
  through the steering group when a trigger fires.

## Check yourself
**Q:** You are two weeks into a 12-week pilot. The data export is late, SMEs have missed one labelling
session, and the security review has not started. How do you prioritise, and what do you change?
**A:** First I would map which items sit on the critical path. The security review usually has the
longest lead time and gates any production-data pilot, so not having started it is the biggest risk.
I would get it submitted this week, with an architecture fallback ready in case external APIs are
rejected. The late export is a dependency on the critical path, so I would name a specific owner,
agree a new date, and invoke the SOW clause that moves milestones day for day. The missed SME session
is a risk to watch: rebook it and set an attendance trigger. Then I would re-estimate expected delay
against remaining slack and tell the sponsor now whether the end date still holds.

## Quiz
### Q: Why should a security review or data-processing agreement usually start in week zero?
- [ ] Reviewers prefer to see an early design rather than a finished system
- [ ] It is a legal requirement before any code is written
- [x] It often has the longest lead time and gates the pilot, so starting late puts its full duration on the critical path
- [ ] Starting early lets the team skip the review if nobody objects
> Approval processes are frequently slower than the engineering and block access to real data.
> Starting them first runs them in parallel with building, instead of serially after it.

### Q: A risk has a 50% chance of a 15-day slip on the critical path; another has a 90% chance of a 2-day slip on a path with 10 days of slack. Which matters more?
- [ ] The second, because it is more likely to happen
- [x] The first, because its expected delay of 7.5 days lands directly on the end date
- [ ] They are equal, because both are rated likely
- [ ] Neither, because risks under 20 days are acceptable
> Expected delay is probability × impact, and a slip moves the end date only by the amount it exceeds
> the slack on its path. The second risk's 2-day slip, even if it happens, is fully absorbed by 10 days
> of slack, while the first lands directly on the end date with 7.5 expected days.

### Q: What distinguishes an assumption from a risk in a RAID log?
- [ ] Assumptions are low-impact risks
- [ ] Risks are owned by the customer, while assumptions are owned by the vendor
- [ ] Assumptions are issues that have already happened
- [x] An assumption is something treated as true but unverified, so its action is to verify it
> Each RAID category has a different action. Assumptions get verified, because a false one usually
> becomes a risk or issue. Risks get a response and a trigger, issues get resolved now, and dependencies
> get chased with their owner.
