---
id: hypercare
module: deployment
name: Hypercare
kind: practice
difficulty: core
est_minutes: 13
status: typed-verified
lesson_type: decision
agent_relevance: useful
mastery: evaluate
code_tested: yes
prerequisites:
  - deployment/runbooks
  - customer-data-evaluation/customer-specific-regression-suites
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
Hypercare is a time-boxed period after your agent goes live where the delivery team gives elevated
support: closer monitoring, daily trace triage, faster fixes, named contacts. Real users find
what UAT missed; it ends on written exit criteria and a handoff, not reassignment.

## For agent builders
- **Why it matters for agents.** Production is the first time your agent meets the full spread of user
  phrasing, documents and tool responses. Its failures (wrong tool, a loop, a confident answer from
  missing content) are found fastest by reading real runs in the first weeks.
- **Where it shows up in your stack.** A daily sample of agent traces, dashboards for task completion,
  steps per task, tool errors, approval rejections and handoffs to humans, one intake queue with a cause
  field, and a regression suite that every fix must pass.
- **What you control.** Which signals you watch, how traces are sampled, how fixes are batched and gated,
  the change freeze, and the exit criteria that end your team's involvement.
- **What you can skip.** Building new features. Hypercare is for fixing what real traffic exposes, and
  anything else waits until it ends.

## Decision case
### Scenario
A manufacturer launches a procurement agent to 1,200 buyers in India, the UK and the US. It answers
supplier and policy questions and can draft and submit purchase orders through a
`create_purchase_order` write tool. The team must choose the hypercare coverage model at the readiness
review. All figures are illustrative planning numbers.
- **Hours:** across the three regions, buyers work over a span of about 18 hours a day, 5 days a week.
- **Vendor team:** 4 engineers, all UK-based, budgeted for 4 weeks of hypercare: 4 x 40 = 160 hours
  a week, 640 in total.
- **Response rule:** the contract requires a response to a severity-1 within 30 minutes at any hour
  buyers are working.
- **Customer desk:** the customer's service desk is new to the agent and will own support after exit.
- **Risk:** a wrong purchase order commits real money, and procurement staff abandon tools quickly when
  early answers are wrong.

### Options
| Option | How it works | Vendor watch hours a week | Hours left for fixes a week | Severity-1 within 30 min at all hours | Desk learns before exit |
|---|---|---|---|---|---|
| Vendor watches all hours | Vendor engineers in rotating shifts watch dashboards and traces for all 18 hours | 18 x 5 = 90 | 160 - 90 = 70 | yes | weakly: the vendor handles everything |
| UK-hours watch, desk first line, pager | Vendor watches 8 UK hours; the desk takes first line in other hours with runbooks; a vendor on-call carries a severity-1 pager; purchase orders are approval-only outside vendor hours | 8 x 5 = 40 | 160 - 40 = 120 | yes, by pager | yes: the desk leads off-hours tickets from day 1 |
| Standard support only | Tickets enter the normal queue with a 5-day response; no daily triage | 0 | not reserved | no | yes, but with no route to fix prompts, tools or retrieval |

### Decision
**UK-hours watch with the desk as first line in other hours, a vendor severity-1 pager, and purchase
orders approval-only outside vendor hours.**
- **It meets the response rule** through the pager rather than by watching dashboards at night.
- **It leaves the most time for fixes.** About 120 hours a week, against 70 when watching all hours,
  and fixes are what hypercare is for.
- **It fences the costly action.** When no vendor engineer is watching, every agent-drafted purchase
  order needs a buyer's approval, so a regression cannot commit money unseen.
- **It trains the future owner.** The desk works off-hours tickets with the runbooks from day 1, which
  feeds the exit criterion that the desk resolves tickets without vendor escalation.
- **Why not standard support:** it fails the response rule and cannot tell a prompt regression from a
  training gap while adoption is still fragile.

**What would change it.**
- **Severity-1s clustering outside UK hours:** add a watch shift in that region, for example from a
  partner team.
- **Approval-only slowing procurement badly** (for example, India-hours orders waiting until the next
  day): extend watched hours instead of keeping the fence.
- **A read-only agent:** without the write tool, the desk plus pager could cover more hours alone.

### Trade-offs accepted
- **Off-hours issues wait for triage.** Non-severity-1 issues raised in India or US hours are triaged
  the next UK morning. Watch: time from report to triage by region, and thumbs-down rate by region.
- **Approval friction off-hours.** Buyers outside UK hours confirm every order. Watch: approval rejection
  rate (an early sign of wrong proposals) and order lead time by region.
- **Pager load on one engineer at a time.** Watch: out-of-hours pages per week; more than a few means
  the desk needs more runbooks or the agent needs a fix.

### Review rubric
- **Hours recomputed:** watch hours and fix hours per option follow from coverage span and team size.
- **Contract rules mapped:** each response obligation is met by a named mechanism (watch, pager, desk).
- **Write actions fenced when unwatched:** the plan says what the agent may do with no expert watching.
- **Ownership transfer built in:** the customer's desk handles real tickets before the exit date.
- **Exit criteria measurable:** written before go-live, normalised by active users.
- **Flip conditions stated:** the incident pattern or friction level that would change the model.

## Why it exists
- **Production is the full distribution; UAT only sampled it.** Every user, document type, odd workflow
  and integration under real load arrives at once.
- **The gap is larger for AI**, because quality depends on inputs the team has never seen: queries phrased
  in ways the golden set did not anticipate, the scanned fax from the one branch office that still uses
  them, users pasting whole email threads into the assistant.
- **Adoption is fragile in the first days.** Users switching from a manual process form their opinion
  fast. Early failures met with silence or a two-week ticket queue send them back to the old way, and
  usage never recovers even after the bugs are fixed.
- **"Go-live is the finish line" fails.** Standard support is tuned for a stable system with known issues:
  it cannot tell a prompt regression from a user-training gap and has no route to change the prompt,
  retrieval, tools or model.
- **Open-ended hypercare fails the other way:** the delivery team quietly becomes the customer's permanent
  operations team. Hypercare bridges the two: concentrated expertise for a bounded time, with a defined
  exit.

## How it works
### Plan it before go-live
- **Agree it in the readiness review:** duration, staffing, hours of coverage, escalation paths, what
  counts as an incident versus a request, and exit criteria.
- **Duration is a planning choice, not a standard.** A few weeks is common, scaled to risk and user count.
- **Plan in advance** because the customer's support organisation, the vendor team and the sponsor must
  know who answers what before the first problem arrives.
- **Fix the mechanics:** a single intake channel so issues are not scattered across email and chat;
  severity definitions with an agreed response time for each; coverage hours that match when users work
  (including other time zones); who on each side is reachable out of hours for a severity-1.

### Heightened monitoring
- **Tighten dashboards and alert thresholds**, and have someone actively watch them rather than wait for
  pages.
- **Watch AI signals that rarely page anyone:** user feedback rates, escalation-to-human rate, guardrail
  blocks, retrieval misses ("no relevant documents found"), cost per task.
- **Add agent signals:** task completion rate, steps per task, step-budget hits, tool-call error rate, and
  how often humans reject an action the agent asked approval for. A rising rejection rate is an early sign
  the agent proposes wrong actions.
- **Read a daily sample of real conversations and traces.** It is the fastest way to learn where the agent
  fails, because metrics show that something changed while traces show why: which tool, which argument,
  which retrieved document.

### A daily triage loop
Every issue, ticket and piece of negative feedback is classified by cause, which decides the owner and the
fix. A short daily review with the customer keeps both sides on the same list.

| Cause | Meaning | Owner and fix |
|---|---|---|
| **Defect** | System bug, integration or tool failure | Engineers |
| **Quality** | Retrieval, tool choice or model produced a poor result | Prompt, tool-description or retrieval tuning |
| **Knowledge gap** | The answer is not in the indexed content | Customer's content owners |
| **Training** | User misunderstood what the agent does | Enablement |
| **Change request** | New scope | Sponsor |

### Fast but safe fixes
- **Faster cadence, same gates.** Every quality fix still passes the regression suite, and every real
  failure becomes a new regression case, because a prompt change fixing today's complaint can quietly
  break a case UAT already accepted.
- **Same rollout criteria, shorter stages**, not around them.
- **Freeze everything else.** A model upgrade, a new feature or tool, or an index rebuild for another team
  waits until hypercare ends, because feedback signals are already noisy and two changes in one week make
  it impossible to say which moved the thumbs-down rate.
- **Batch fixes into one or two predictable releases a week**, each with a written note of what changed,
  so every movement in the metrics stays attributable.

### Exit criteria and handoff
Hypercare ends when written criteria hold for a sustained period, for example:
1. No open severity-1 or severity-2 issues.
2. Incident and ticket volume stable or falling over consecutive weeks, **normalised by active users**
   (because volume also falls when people stop using the system) and read alongside usage retention.
3. Quality metrics at or above the UAT baseline on real traffic.
4. The customer's support team resolves issues using the runbooks without escalating.
5. The known-issues list and backlog are handed over with owners.

The handoff is explicit: a meeting, a signed-off document and a named date when the support model changes.

## Diagram
The daily loop runs until the exit criteria hold; an unmet criterion extends hypercare with a specific
focus rather than letting it drift.

```mermaid
flowchart TD
    G["Agent go-live"] --> M["Monitor metrics and read traces"]
    M --> T{"Daily triage by cause"}
    T -->|"defect or quality"| F["Fix, pass regression suite"]
    T -->|"knowledge gap"| K["Customer content owner"]
    T -->|"training"| EN["Enablement"]
    T -->|"change request"| SP["Sponsor backlog"]
    F --> R["Batched release, shortened rollout"]
    R --> X{"Exit criteria held?"}
    K --> X
    EN --> X
    X -->|"no"| EXT["Extend with one focus"]
    EXT --> M
    X -->|"yes"| H["Signed handoff to support"]
```

## Examples
### Worked scenario: an HR policy agent
An HR policy agent for a 4,000-employee company, with retrieval over policy documents and a tool to open
HR cases. Three weeks of planned hypercare.

Exit criteria agreed at the readiness review: zero open sev-1/sev-2, weekly actionable issues below 10
for two consecutive weeks, thumbs-down rate at or below the 6% observed in UAT, and the customer's
service desk resolving at least 80% of tickets without vendor escalation.

| Week | Users | Actionable issues | Thumbs-down | Desk unaided |
|---|---|---|---|---|
| 1 | 1,900 | 41 | 11% | n/a |
| 2 | 2,600 | 18 | 7% | 65% |
| 3 | 2,900 | 9 | 5.8% | 78% |

- **Week 1 triage:** 9 defects (most from single sign-on timeouts for one region), 14 quality, 11
  knowledge gaps (the parental-leave policy for one country was never in the indexed content), 5
  training, 2 change requests.
- **Week 1 fixes:** the team fixes SSO, the customer's HR content owner uploads the missing policy, the 14
  quality failures become regression cases, and a retrieval fix passes the suite and ships behind a flag.
- **Week 2 near miss:** a prompt change improved answer tone but dropped citations on long answers. The
  regression suite caught it in staging, so it never shipped and never became a production incident.
- **Week 3 is real improvement:** per active user, issues fell from about 2.2 per 100 users in week 1 to
  about 0.3 while the user count kept rising, so the drop is not people giving up.
- **But exit is not met:** volume is below 10 for only one week and desk resolution is below 80%. Rather
  than letting hypercare drift, the team extends it by one week with a single focus: a runbook session
  with the service desk on the three ticket types they still escalate. Week 4 meets all criteria and
  support is handed over.

### Exit criteria as code
The same criteria, evaluated from weekly numbers so the exit decision is not argued from impressions.

```python
weeks = [  # open_sev12 values are illustrative
    {"week": 1, "users": 1900, "issues": 41, "open_sev12": 0, "thumbs_down": 0.11,  "desk_unaided": None},
    {"week": 2, "users": 2600, "issues": 18, "open_sev12": 0, "thumbs_down": 0.07,  "desk_unaided": 0.65},
    {"week": 3, "users": 2900, "issues": 9,  "open_sev12": 0, "thumbs_down": 0.058, "desk_unaided": 0.78},
]

def exit_check(weeks, max_issues=10, td_baseline=0.06, desk_min=0.80):
    unmet = []
    last = weeks[-1]
    if last["open_sev12"] > 0:
        unmet.append("open sev-1/sev-2")
    if len(weeks) < 2 or any(w["issues"] >= max_issues for w in weeks[-2:]):
        unmet.append("issues below 10 for two consecutive weeks")
    if last["thumbs_down"] > td_baseline:
        unmet.append("thumbs-down above UAT baseline")
    if (last["desk_unaided"] or 0) < desk_min:
        unmet.append("desk resolution below 80%")
    return unmet

for w in weeks:
    print(w["week"], f"{100 * w['issues'] / w['users']:.1f} issues per 100 users")
print(exit_check(weeks))
# 1 2.2 issues per 100 users
# 2 0.7 issues per 100 users
# 3 0.3 issues per 100 users
# ['issues below 10 for two consecutive weeks', 'desk resolution below 80%']
```

### Daily trace sample, biased toward failures
A uniform sample of agent runs is mostly successes. This picks the morning's reading list by
over-sampling runs that already look wrong, while keeping some normal runs to catch silent failures.

```python
import random

def daily_sample(runs, n=40, seed=None):
    rng = random.Random(seed)
    flagged = [r for r in runs if r["thumbs_down"] or r["hit_step_limit"]
               or r["tool_errors"] > 0 or r["approval_rejected"]]
    normal = [r for r in runs if r not in flagged]
    k_flag = min(len(flagged), n // 2)                 # up to half the sample from flagged runs
    picked = rng.sample(flagged, k_flag) + rng.sample(normal, min(len(normal), n - k_flag))
    return [r["trace_id"] for r in picked]
```

| Agent signal, week 1 | Value | What the trace reading found |
|---|---|---|
| Step-budget hits | 3% of runs | The agent re-searched with near-identical queries when no policy matched (often a knowledge gap) |
| Approval rejections on `open_hr_case` | 18% of proposals | Cases opened for questions the policy already answered; fixed in the tool description |
| Tool-call errors | 1% | SSO timeouts in one region (the defect above) |

These week-1 agent signal values are illustrative; the point is that each number led to a trace, and each
trace to a cause and an owner.

## Production patterns
- **Single intake channel with a cause field.** Route every report (tickets, chat, feedback buttons) into
  one tracker queue whose required fields include cause and severity. Monitor issues per 100 active users
  by cause each day.
- **Daily trace sampling.** Pull a fixed sample of real agent runs each morning, over-weighting flagged
  runs, under the data-access rules agreed in the readiness review, and have one person read them before
  triage. Monitor new failure patterns found per day; when it reaches zero for several days, sampling can
  drop in frequency.
- **Agent signal board.** Track task completion, steps per task, step-budget hits, tool errors, approval
  rejections and human handoffs daily, each linked to example traces. Monitor day-over-day change after
  each fix release.
- **Change freeze with a fix train.** Only hypercare fixes ship, batched into one or two scheduled releases
  a week, each passing the regression suite and a shortened rollout with a written change note. Monitor
  metric shifts against release notes so every movement is attributable.
- **Shadow-then-lead support transfer.** The customer's service desk first shadows the vendor on tickets,
  then leads with vendor backup, using the runbooks. Monitor the share of tickets resolved without vendor
  escalation against the exit threshold.

## Trade-offs
- **Expert coverage vs customer ownership.** Longer hypercare catches more issues with experts on hand,
  but ties up the delivery team and delays the customer's own ownership; extend only against unmet exit
  criteria, never by default.
- **Fast fixes vs learning.** Embedding vendor engineers with the customer gives the fastest fixes, but can
  stop the customer team from learning; the balance flips once routine issues are being solved by vendor
  staff on request.
- **Cadence vs regression risk.** A faster release cadence fixes real pain quickly but raises regression
  risk; it is safe only when every change passes the regression suite and a shortened rollout.
- **Insight vs privacy cost.** Reading many real traces finds failure patterns early, but costs time and
  needs privacy approval; agree data-access rules during the readiness review, not during an incident.

## Pitfalls
- **No exit criteria.** Symptom: the delivery team still answers the customer's tickets months after
  go-live, and nobody can say when it ends. Fix: written criteria agreed before go-live.
- **Prompt tweaks that skip the regression suite.** Symptom: a fix for this week's issue reopens a case
  accepted in UAT. Diagnose: list releases with no suite run attached and diff reopened cases against
  them. Fix: no fix ships without the suite.
- **Lumping all feedback into "bugs".** Symptom: engineers spend days on missing content or user training
  while real defects wait. Diagnose: chart engineering hours by triage cause. Fix: classify by cause and
  route to the right owner.
- **Measuring only incidents and uptime.** Symptom: a quiet incident queue alongside falling usage,
  because unhappy users silently returned to the old process. Diagnose: plot issues per 100 active users
  next to weekly retention. Fix: normalise by active users and track retention.
- **Reading only final answers.** Symptom: "the answer looks fine" while the agent took 14 steps, called a
  write tool it did not need, or had proposals rejected. Diagnose: check steps per task and approval
  rejections in the daily trace sample. Fix: read traces and watch agent signals.
- **Doing the support work instead of transferring it.** Symptom: a customer service desk that escalates
  every ticket on the first day after handoff. Fix: the desk leads tickets before exit.

## When to use
- **Use when:** real users meet a new agent, the customer's own team will eventually operate it, or
  adoption matters commercially.
- **Size by risk and scale:** a small internal pilot may need a few days of close attention; an enterprise
  rollout to thousands of users, or an agent with write tools, needs weeks with dedicated staff.
- **Track:** issue volume by cause, user feedback, usage and retention, quality on real traffic versus the
  UAT baseline, agent signals, and the share of tickets the customer resolves alone.

## Exercise
**Goal:** write the hypercare plan for an agent you have built and run one simulated week against it.
No solution is given.
- **Inputs:** at least 300 logged or generated runs with feedback, step counts, tool errors and approval
  outcomes, plus a user count per day.
- **Constraints:** exit criteria are written before you look at the data; every criterion is measurable
  from logs; volume is normalised by active users.
- **Deliver:** the plan (duration, intake, severities, freeze rules, exit criteria), a labelled daily
  sample of 40 traces, and a short exit memo.
- **Acceptance:** every sampled trace has a cause and an owner; a deliberately injected loop shows up in
  the sample and the agent signal board; the exit memo names each unmet criterion and the single focus
  of any extension.

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

## Interview questions
### What is hypercare, and why not hand straight over to standard support at go-live?
- **Definition:** a bounded period of elevated support after go-live, with closer monitoring, daily triage,
  faster fixes and named contacts, ending on written exit criteria.
- **Why not standard support:** it is tuned for stable systems, cannot tell a prompt regression from a
  training gap, and cannot change the prompt, retrieval, tools or model.

### What would you watch during hypercare for an agent that can take actions?
- **Agent signals beyond feedback:** task completion, steps per task, step-budget hits, tool-call errors,
  approval rejection rate and handoffs to humans, each linked to example traces.
- **A daily trace sample biased to flagged runs:** rejected proposals and step-limit runs show wrong
  actions and loops before users complain about them.

### A customer complaint arrives daily about answers on a topic. How do you triage it?
- **Classify by cause first:** is the content indexed (knowledge gap), retrieved but answered badly
  (quality), broken by an integration (defect), misunderstood by users (training), or new scope (change
  request)?
- **Route to the owner:** a knowledge gap goes to the customer's content owners, because tuning the prompt
  for content that is not there risks hallucinated answers.

### The exit date has arrived but criteria are not met. What do you do?
- **Extend with a single focus, not open-endedly:** name the unmet criterion and the work that closes it,
  for example a runbook session on the ticket types the desk still escalates.
- **Do not exit on the calendar:** handing over before the customer can run the system just moves the
  escalations into normal support.

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

## Mastery test
**Problem:** week 2 of hypercare for a procurement agent that drafts purchase orders for approval. Issues
per 100 users fell from 3.1 to 1.2, thumbs-down is flat, but approval rejections rose from 8% to 21%.

Meanwhile the customer asks to slip in a model upgrade "while you are all here". What do you conclude
and do?

**A strong answer:**
- reads the falling issue rate as errors now caught silently by approvers instead of reported, so
  quality may be worse, not better;
- checks whether the rise lines up with a week-1 fix release, a shift in request mix, or stricter
  approvers (rejection reasons per approver);
- samples rejected proposals, classifies the cause (tool description, retrieval of supplier data, a
  recent fix) and adds them as regression cases;
- refuses the model upgrade until exit, because two changes would hide which one moved rejections;
- adds a rejection-rate threshold to the exit criteria.

## Use cases & projects
### Use cases
- **First weeks of a customer-facing support or HR agent.** Daily trace review and cause triage turn real
  failures into fixes and regression cases while adoption is still forming.
- **Agents with approval-gated write tools.** Approval rejection rate during hypercare shows whether the
  agent proposes the right actions before approvals are relaxed.
- **Handing an agent to a customer's service desk.** Shadow-then-lead transfer and an unaided-resolution
  threshold decide when the vendor steps back.
- **Model or prompt fixes under a freeze.** A weekly fix train keeps every change attributable while
  signals are noisy.

### Mini project
**Run a simulated hypercare week for an agent (2-3 hours).**
- **Goal:** turn simulated agent runs into a daily triage list, fixes and an exit decision.
- **Steps:** generate or log 300 runs (one day) from a toy RAG agent with a write tool, including thumbs-down,
  step-limit hits and rejected approvals; build the biased daily sample of 40; read and label each by
  cause; add every quality failure to a regression file; compute issues per 100 users and the exit check
  for three simulated weeks.
- **Done when:** you have a cause breakdown with an owner per cause, at least five new regression cases,
  and an exit check that names exactly which criteria are unmet and the single focus for an extension.
