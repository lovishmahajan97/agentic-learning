---
id: change-management
module: adoption
name: Change management
kind: practice
difficulty: core
est_minutes: 16
status: reviewed
related:
  - adoption/user-training
  - adoption/stakeholder-management
  - adoption/usage-analytics
  - adoption/adoption-and-value-realization-measurement
  - scoping/workflow-analysis
  - agents/human-in-the-loop
  - strategy/stakeholder-alignment
sources:
  - Kotter, 1996 — Leading Change
  - Hiatt, 2006 — ADKAR: A Model for Change in Business, Government and Our Community
  - Rogers, 1962 — Diffusion of Innovations
---

## TL;DR
Change management is the deliberate work of moving people from the old way of doing a task to the new
one, so that a deployed AI system is actually used and produces value. It exists because the common
failure of enterprise AI is not a broken model but an unchanged workflow: people ignore, work around or
over-trust a tool whose output is probabilistic and whose arrival can feel like a threat to their role.

## Why it exists
Shipping is not adoption. A typical pattern after an internal AI launch is a burst of curious usage in
the first week, then a slow decay back to the old process, while the dashboard still shows the system
"live". Nothing in the code is wrong; the people around it never changed what they do.

AI systems make this harder than ordinary software for three reasons. First, the output is probabilistic,
so users have to learn *when* to trust it. Users who under-trust redo the work by hand and now spend more
time than before, because they read the draft *and* do the task. Users who over-trust let errors through
(automation bias), and the first public mistake poisons the tool for everyone. Second, AI arrives with an
implied question about jobs. If nobody answers it, people answer it themselves, and quiet resistance is a
rational response. Third, the value usually requires redesigning the work around the tool: if agents are
still measured on the old metric, still follow the old checklist and still route through the old
approval step, the AI step is pure overhead.

The naive alternatives fail in predictable ways. "Announce it, record a training video, it's intuitive"
creates awareness but not desire, and leaves incentives untouched. "Mandate it" produces compliance
theatre: people open the tool so the login count looks good and then ignore the output. Change management
replaces both with a plan that names the behavior change, finds what blocks it for each group, and
removes those blocks one by one.

## How it works
### Use frameworks as diagnostic checklists
Two well-known models cover different levels. **ADKAR** (Prosci) describes the individual: *Awareness* of
why the change is happening, *Desire* to take part, *Knowledge* of how, *Ability* to do it in real work,
and *Reinforcement* that makes it stick. Its value is diagnostic: find the first stage that is blocked
for a group and work there, *because* training (Knowledge) cannot fix a Desire problem, and enthusiasm
(Desire) cannot fix a missing integration (Ability). **Kotter's eight steps** describe the organization:
create urgency, build a guiding coalition, form a vision, communicate it, remove obstacles, generate
short-term wins, consolidate gains, and anchor the new way in culture. The older
unfreeze-change-refreeze framing, traditionally attributed to Kurt Lewin (historians note the tidy
three-step version was largely popularized by later writers), makes the same point compactly: the old
habit has to be loosened before the new one can form, and then deliberately locked in. None of these is
an algorithm; use them to ask "what is missing for this group?", not as a sequence of ceremonies.

### 1. Define the change as behavior
"Adopt the AI assistant" cannot be managed or measured. "Claims adjusters start every file over 30 pages
from the AI summary and verify its cited sections, instead of reading the file front to back" can. Write
one such sentence per affected role, *because* every later step (training, metrics, incentives) hangs off
it.

### 2. Assess impact per group
For each group, list what changes: tasks, skills, metrics, tools, approvals, and any effect on headcount
or roles. Be explicit about job impact, *because* silence is filled with rumor. Groups differ: the end
users, their team leads, QA reviewers, IT support and compliance each experience a different change.

Treat resistance as data about which stage is blocked, not as an attitude problem. "It's slower" is an
Ability or incentive problem; "it got the prior-injury note wrong" is a trust problem; "why would I train
my replacement?" is a Desire problem rooted in job security; a senior expert's "I don't need it" is often
about losing the status of being the person who knows. Each needs a different fix, which is why the
diagnosis has to be per group.

The incentive question can usually be made arithmetic. A user adopts the new path when, on the metric
they are judged by, it beats the old one *including* the cost of verifying the output:
`t_ai = t_review_output + t_verify + t_fix_errors` versus `t_manual`. If verification is slow or the
task is short, `t_ai > t_manual` and non-adoption is the rational choice, not a training gap.

### 3. Build sponsorship and a change network
An executive sponsor who visibly uses the tool and explains the why creates legitimacy. Champions inside
each team, usually the early adopters in Rogers' diffusion model, create proof, *because* people copy
respected peers more readily than memos. Team leads matter most: if a lead says "don't trust that thing",
no amount of central communication wins.

### 4. Change the system around the tool
Update the SOPs, QA checklists and KPIs so the new way is the measured way. Decide when the old path is
retired and what the fallback is when the AI is down or wrong. *Because* people follow incentives, an
unchanged scorecard quietly pulls them back to the old process.

### 5. Roll out in phases, and plan for the dip
Start with a pilot cohort, fix what they hit, then expand. Expect performance to *fall* before it rises
(often called the J-curve): people are learning a new habit, running old and new paths side by side, and
hitting first-version bugs. Tell the sponsor in advance, temporarily protect the pilot cohort's
productivity targets, and judge the change on the trend after the learning period rather than on week
two, *because* a dip that nobody predicted reads as failure and gets the project cancelled at its
lowest point.

Close the feedback loop. Publish a "you said, we did" list, *because*
visible responsiveness is the cheapest way to build desire. Show the measured error rate honestly and
teach how to verify (citations, confidence cues), which calibrates trust instead of inflating it.

### 6. Reinforce and measure
Track leading indicators (weekly active users in the target group, share of eligible tasks started with
the AI, override and edit rates) and lagging outcomes (cycle time, quality, cost per case). Usage without
outcome improvement usually means people are using it but the workflow around it has not changed (or the
tool is not adding value); outcome improvement without usage means something other than the tool is
driving the outcome, so crediting the AI would be wrong.

## Example
A hypothetical insurer rolls out an AI claims-file summarizer to 120 adjusters. Usage starts at about 70%
of adjusters in week one and decays to about 25% by week six. The model's offline eval looks fine, so the
team interviews ten adjusters and checks the logs instead of retraining.

The ADKAR diagnosis differs by group. Adjusters know how to use the tool (Knowledge is fine), but Desire is
low: they are scored on claims closed per day, and on short files reading the summary plus checking it
takes longer than reading the file. Two team leads tell their teams not to rely on it after a story spread
about a summary that missed a prior-injury note. Ability is also weak: the summary opens in a separate tab,
so verification means switching windows.

The incentive problem is worth doing as arithmetic (illustrative figures from the interviews): reading a
file by hand takes about 0.6 minutes per page; the AI path costs about 10 minutes of fixed overhead
(read the summary, open and check each cited section) plus about 0.25 minutes per page for the spot
reading that remains. Break-even is where 0.6p = 10 + 0.25p, so p ≈ 29 pages. On a 12-page file the AI
path costs 13 minutes against 7 by hand; on an 80-page file it costs 30 against 48. Adjusters who
abandoned the tool were, on average, handling shorter files: they were right.

Interventions follow the diagnosis. The tool is enabled only for files over 30 pages, where it saves time.
The missed-note bug is reproduced, fixed and announced, and the team publishes its golden-set result
(say, a material omission in 1 of 25 long files) alongside the instruction to check cited sections for
medical history. The two skeptical leads join the pilot review and become champions. The QA checklist is
updated to accept "summary verified" as a valid review step. Citations are moved inline, which cuts the fixed verification overhead and lowers the break-even
further. Usage on eligible files recovers and stays, and cycle time on long files becomes the metric
reported to the sponsor, rather than the share of all adjusters using the tool, which would have
pressured people to use it on short files where it costs time.

## Trade-offs
- A phased pilot surfaces workflow problems cheaply, but it delays org-wide value; skip it only when the change is small and reversible.
- Retiring the old path forces adoption and ends dual running, but removes the fallback; keep it while the AI's error rate is still being established.
- Heavy executive sponsorship speeds legitimacy, but a sponsor seen as imposing cuts can turn the change into a headcount fight; it flips back to an asset once the sponsor has stated plainly what happens to roles.
- Mandating usage gets the numbers up quickly, but produces compliance theatre unless incentives and workflow change first.
- Investing in a champion network costs people's time, which pays off at scale and is overkill for a ten-person team.

## Pitfalls
- Treating training as the whole change plan; the symptom is high course completion with usage decaying after launch week.
- Measuring logins instead of the target behavior; dashboards look healthy while cycle time and quality do not move.
- Leaving old KPIs in place, which shows up as users reverting to the manual path whenever they are busy.
- Ignoring team leads and middle managers; usage varies sharply team by team with no technical explanation.
- Overselling accuracy at launch, so the first visible error triggers a trust collapse and a spike in overrides.
- Setting an adoption target of "everyone uses it for everything"; the symptom is usage on tasks where the tool is slower, flat or worse outcomes, and resentment.
- Reviewing results during the learning dip, which shows up as a sponsor wanting to cancel in week three when the trend was about to turn.
- Staying silent on job impact, revealed by rumors, union or works-council questions, and slow passive resistance.

## When to use
Plan change management whenever an AI system changes how a group of people does its daily work, which is
almost every enterprise deployment beyond a developer tool. The size of the effort scales with the number
of people affected, how much their metrics and roles change, and how costly mistakes are. Start it during
scoping, not after launch: the behavior statement and impact assessment belong next to the success
metrics. Measure the target behavior and the business outcome, not logins: share of *eligible* tasks done
the new way, override and edit rates, time per task on the new path versus the old, and usage segmented
by team (team-by-team variance with no technical cause points at leads or local incentives). If you
cannot write the behavior sentence or show the new path beats the old on the user's own metric, fix that
before planning communications. In interviews, the signal is
whether you treat low adoption as a diagnosis problem (which ADKAR stage is blocked, for whom) rather than
as a call for more training or a better model.

## Role lens
- **Engineer:** Instruments the product so the target behavior is measurable, and builds the trust affordances (citations, confidence cues, easy override) users need.
- **Architect:** Owns the design of the human workflow around the system: where review sits, what the fallback is, and which metrics define success.
- **FDE:** Sits with users, diagnoses which stage of change is blocked for which team, and brings concrete workflow and incentive fixes to the sponsor.

## Check yourself
**Q:** Six weeks after launch, usage of your AI drafting tool has halved, but offline evals are unchanged. The sponsor wants to fund a model upgrade. What do you do first?
**A:** I would not assume the model is the cause, because evals are flat and decay after a launch spike is the classic adoption pattern. I would segment usage by team and task type, interview a handful of users and team leads, and map what I find onto ADKAR to find the first blocked stage. Typical causes are incentives (old KPIs), workflow friction (verification is slow), a trust event (one bad output went viral) or a lead discouraging use. Each has a cheaper fix than a model upgrade. I would come back to the sponsor with the diagnosis, the targeted fixes and the metric that will show whether they worked.

## Quiz
### Q: In ADKAR terms, what is the main risk of responding to low adoption with more training sessions?
- [ ] Training sessions are too expensive to run for most teams
- [x] Training addresses Knowledge, which may not be the blocked stage if users lack Desire or Ability
- [ ] Training reduces Awareness by overwhelming users with detail
- [ ] Training should only happen after the Reinforcement stage
> ADKAR is diagnostic: work on the first stage that is blocked. If users understand the tool but are
> scored on a metric it hurts, or cannot verify outputs easily, more training changes nothing.

### Q: Which indicator best shows that the intended behavior change has happened?
- [ ] Number of logins per week across the company
- [ ] Course completion rate for the launch training
- [x] Share of eligible tasks that are started with the AI output in the target group
- [ ] Number of positive comments in the launch announcement channel
> Logins and completions measure exposure, not behavior. The share of eligible tasks actually done the new
> way maps directly to the behavior statement, and can be paired with outcome metrics.

### Q: Why do AI tools need explicit trust calibration as part of change management, more than ordinary software?
- [ ] Because AI tools are always less accurate than the manual process they replace
- [ ] Because regulators require users to sign off on every AI output
- [x] Because probabilistic outputs make users either redo the work or let errors through unless they learn when to rely on it
- [ ] Because AI tools cannot show any evidence for their outputs
> Deterministic software is right or broken; AI output is right most of the time. Under-trust doubles the
> work and over-trust lets errors pass, so users need honest error rates and a way to verify.
