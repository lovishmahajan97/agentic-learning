---
id: hypercare
module: deployment
name: Hypercare
kind: practice
difficulty: core
est_minutes: 13
status: reviewed
related:
  - deployment/runbooks
  - deployment/deployment-readiness-reviews
  - adoption/handoff-to-customer-teams
  - customer-data-evaluation/production-feedback-classification
  - customer-data-evaluation/customer-specific-regression-suites
  - delivery-ownership/incident-severity-and-escalation
  - adoption/change-management
  - llmops-platform/model-monitoring
---

## TL;DR
Hypercare is a fixed, time-boxed period right after go-live in which the delivery team stays on
elevated support: closer monitoring, a daily triage of issues and feedback, faster fix cycles and named
people the customer can reach directly. It exists because real users always find problems that UAT
missed, and the first weeks decide whether they trust the system. It ends on written exit criteria and a
handoff to normal support, not when the team gets reassigned.

## Why it exists
Go-live is the moment the system meets the full distribution of real work: every user, every document
type, every odd workflow and every integration under real load. UAT sampled that distribution; production
is the distribution. For AI systems the gap is larger than for conventional software, because quality
depends on inputs the team has never seen. A retrieval system meets queries phrased in ways the golden set
did not anticipate; an extraction model meets the scanned fax from the one branch office that still uses
them; users discover behaviours nobody tested, such as pasting whole email threads into the assistant.

Adoption is also fragile in this window. Users switching from a manual process form their opinion in the
first days. If early failures are met with silence or a two-week support ticket queue, they route around
the system and return to the old way, and usage never recovers even after the bugs are fixed.

The naive alternative is to treat go-live as the finish line: hand over to the standard support process
and move the team to the next project. Standard support is tuned for a stable system with known issues; it
cannot tell a prompt regression from a user-training gap, and it has no route to change the prompt,
retrieval or model. The opposite failure is open-ended hypercare, where the delivery team quietly becomes
the customer's permanent operations team. Hypercare exists to bridge the two: concentrated expertise for a
bounded time, with a defined exit.

## How it works
### Plan it before go-live
Hypercare is agreed in the readiness review: its duration, staffing, hours of coverage, escalation paths,
what counts as an incident versus a request, and the exit criteria. A duration of a few weeks is common,
scaled to risk and user count; it is a planning choice, not a standard. Planning it in advance matters
*because* the customer's support organisation, the vendor team and the sponsor all need to know who
answers what before the first problem arrives. The plan also fixes the mechanics: a single intake channel
so issues are not scattered across email and chat, severity definitions with an agreed response time for
each, coverage hours that match when users actually work (including other time zones), and who on each
side is reachable out of hours for a severity-1.

### Heightened monitoring
Dashboards and alert thresholds are tightened for the period, and someone actively watches them rather
than waiting for pages. For AI systems this includes signals that rarely page anyone: user feedback
rates, escalation-to-human rate, guardrail blocks, retrieval misses ("no relevant documents found"),
cost per task, and a daily sample of real conversations read by a person. Reading real transcripts is the
fastest way to learn where the system fails, *because* metrics show that something changed while
transcripts show why.

### A daily triage loop
Each issue, ticket and piece of negative feedback is classified by cause: **defect** (system bug,
integration failure), **quality** (retrieval or model produced a poor answer), **knowledge gap** (the
answer is not in the indexed content), **training** (the user misunderstood what the system does), or
**change request** (new scope). Classification decides the owner and the fix: engineers for defects,
prompt or retrieval tuning for quality, the customer's content owners for knowledge gaps, enablement for
training, the sponsor for change requests. A short daily review with the customer keeps both sides
looking at the same list.

### Fast but safe fixes
Hypercare allows a faster release cadence, but every quality fix still goes through the regression suite,
and every real failure becomes a new regression case, *because* a prompt change that fixes today's
complaint can quietly break a case UAT had already accepted. Fixes ship through the same rollout criteria,
with shorter stages, not around them.

The complement of fast fixes is a freeze on everything else. Unrelated changes (a model upgrade, a new
feature, an index rebuild for another team) wait until hypercare ends, *because* the feedback signals are
already noisy and two changes shipped in the same week make it impossible to say which one moved the
thumbs-down rate. Batching fixes into one or two predictable releases a week, each with a written note of
what changed, keeps every movement in the metrics attributable.

### Exit criteria and handoff
Hypercare ends when written criteria hold for a sustained period, for example: no open severity-1 or
severity-2 issues; incident and ticket volume stable or falling over consecutive weeks, normalised by
active users (because volume also falls when people stop using the system) and read alongside usage
retention; quality metrics
at or above the UAT baseline on real traffic; the customer's support team has resolved issues using the
runbooks without escalating; and the known-issues list and backlog are handed over with owners. The
handoff is explicit: a meeting, a signed-off document and a named date when the support model changes.

## Example
A worked scenario: an HR policy assistant for a 4,000-employee company, three weeks of planned hypercare.
Exit criteria agreed at the readiness review: zero open sev-1/sev-2, weekly actionable issues below 10
for two consecutive weeks, thumbs-down rate at or below the 6% observed in UAT, and the customer's
service desk resolving at least 80% of tickets without vendor escalation.

**Week 1.** 1,900 users, 41 actionable issues. Triage: 9 defects (most from single sign-on timeouts for
one region), 14 quality, 11 knowledge gaps (the parental-leave policy for one country was never in the
indexed content), 5 training, 2 change requests. Thumbs-down rate 11%. The team fixes SSO, the customer's
HR content owner uploads the missing policy, and the 14 quality failures become regression cases; a
retrieval fix passes the suite and ships behind a flag.

**Week 2.** 2,600 users, 18 issues, thumbs-down 7%, service desk resolves 65% unaided. One near miss: a
prompt change improved answer tone but dropped citations on long answers; the regression suite caught it
in staging, so it never shipped and never became a production incident.

**Week 3.** 2,900 users, 9 issues, thumbs-down 5.8%, service desk at 78%. Per active user, issues fell
from about 2.2 per 100 users in week 1 to about 0.3, while the user count kept rising, so the drop is
real improvement rather than people giving up. Volume is below 10 but only for
one week and desk resolution is below 80%, so exit criteria are not met. Rather than letting hypercare
drift on, the team extends it by one week with a single focus: a runbook session with the service desk
on the three ticket types they still escalate. Week 4 meets all criteria and support is handed over.

## Trade-offs
- Longer hypercare catches more issues with experts on hand, but ties up the delivery team and delays
  the customer's own ownership; extend only against unmet exit criteria, never by default.
- Embedding vendor engineers with the customer gives the fastest fixes, but can stop the customer team
  from learning; the balance flips once routine issues are being solved by vendor staff on request.
- A faster release cadence during hypercare fixes real pain quickly, but raises regression risk; it is
  safe only when every change passes the regression suite and a shortened rollout.
- Reading many real transcripts finds failure patterns early, but costs time and needs privacy
  approval; agree data-access rules for hypercare during the readiness review, not during an incident.

## Pitfalls
- Starting hypercare with no exit criteria; the symptom is the delivery team still answering the
  customer's tickets months after go-live, with nobody able to say when it ends.
- Fixing complaints with prompt tweaks that skip the regression suite; the symptom is a fix for this
  week's issue reopening a case accepted in UAT.
- Lumping all feedback into "bugs"; the symptom is engineers spending days on problems that were missing
  content or user training, while real defects wait.
- Measuring only incidents and uptime; the symptom is a quiet incident queue alongside falling usage,
  because unhappy users silently returned to the old process.
- Doing the support work instead of transferring it; the symptom is a customer service desk that
  escalates every ticket on the first day after handoff.

## When to use
Plan hypercare for any go-live where real users meet a new AI system, where the customer's own team will
eventually operate it, or where adoption matters commercially. Size it by risk and scale: a small
internal pilot may need a few days of close attention; an enterprise rollout to thousands of users needs
weeks with dedicated staff. Track issue volume by cause, user feedback, usage and retention, quality on
real traffic versus the UAT baseline, and the share of tickets the customer resolves alone. In interviews,
strong answers describe hypercare as bounded, criteria-driven and aimed at transferring ownership, with
AI-specific feedback classification.

## Role lens
- **Engineer:** Turns each real failure into a regression case, ships fixes through shortened rollouts,
  and builds the daily views of feedback, escalations and sampled transcripts used in triage.
- **Architect:** Ensures the system supports hypercare safely: feature flags for quick fixes,
  regression gates that cannot be skipped, and data-access rules for reviewing real conversations.
- **FDE:** Runs the daily triage with the customer, separates defects from content, training and scope
  issues, and drives the handoff so their team can operate the system without escalating.

## Check yourself
**Q:** Three weeks into hypercare, incidents are near zero but weekly active users have fallen 30% from
week one. The sponsor says hypercare can close. Do you agree?
**A:** Not yet. A quiet incident queue with falling usage often means users stopped reporting because they
stopped using the system. I would look at who dropped off and what they last did, read a sample of their
final sessions, check feedback and abandonment by task type, and talk to a few users directly. Likely
causes are a quality gap on a common task, missing content, or a workflow that is slower than the old
process. If the cause is fixable, extend hypercare against a specific adoption criterion; if it is a
training or change-management issue, hand that to the customer's enablement owner explicitly. Closing on
incident counts alone would hide the real failure.

## Quiz
### Q: What should normally end a hypercare period?
- [ ] The planned number of weeks has passed
- [ ] The delivery team is needed on another project
- [x] Written exit criteria have held for a sustained period and ownership is formally handed over
- [ ] The first week with zero incidents
> Hypercare is bounded by criteria, not the calendar or staffing. A single quiet week can be noise or
> reflect falling usage, and calendar-based exits hand over a system the customer cannot yet run.

### Q: Users report that the assistant cannot answer questions about one country's leave policy. The ingestion pipeline works, but the customer's HR team never supplied that policy for indexing. How should this be classified in triage?
- [ ] As a model quality defect needing prompt changes
- [ ] As a user training issue
- [x] As a knowledge gap owned by the customer's content owners
- [ ] As a change request to the sponsor
> The system cannot answer from content it does not have. Tuning prompts would risk hallucinated answers;
> the right owner is whoever controls the source content, and the fix is to ingest the missing policy.

### Q: Why must quality fixes during hypercare still pass the regression suite?
- [ ] Because the customer's change-management policy always requires it
- [x] Because a prompt or retrieval change that fixes one complaint can silently break cases already accepted in UAT
- [ ] Because regression suites are the only way to measure latency
- [ ] Because hypercare fixes are deployed to all users at once
> AI behaviour changes are global: a prompt edit affects every query. The regression suite, extended with
> each real failure, is what keeps a fast fix cadence from trading one problem for another.
