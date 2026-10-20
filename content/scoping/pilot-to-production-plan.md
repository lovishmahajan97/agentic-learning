---
id: pilot-to-production-plan
module: scoping
name: Pilot → production plan
kind: practice
difficulty: advanced
est_minutes: 14
related:
  - scoping/rapid-prototyping-poc
  - scoping/productionization
  - scoping/baseline-measurement-before-deployment
  - scoping/success-metrics-kpis
  - deployment/rollout-criteria-and-rollback-triggers
  - deployment/deployment-readiness-reviews
  - deployment/hypercare
  - adoption/change-management
---

## TL;DR
A pilot puts the system in front of real users doing real work, at limited scope, so you can measure
impact against a baseline before betting the organization on it. The pilot → production plan defines the
stages, the entry and exit criteria for each, the owners, and the work needed to close the gap between
"works for twenty people" and "runs for everyone". Without it, successful pilots stall because nobody
planned the path, budget or ownership beyond them.

## Why it exists
A PoC answers "can it work?" on a sample. It cannot tell you whether people will use it in their daily
workflow, whether it changes the business metric, or what breaks under real traffic, real permissions and
real edge cases. Those answers only come from real use, which is what a pilot is for.

But pilots have a well-known failure mode: they succeed and then nothing happens. The pilot ran on a
champion team with an engineer on call in a chat channel, on a sandbox environment exempted from security
review, paid for from an innovation budget. Moving to production needs SSO, a security sign-off, an
operations owner, a support process, a cost line in someone's budget, and training for users who did not
volunteer. If none of that was planned, the pilot's success creates a demand nobody can meet, and the
project stalls in what people call pilot purgatory.

The naive alternatives fail in opposite ways. Skipping the pilot and launching to everyone means the
first real measurement happens at full blast radius, with no baseline comparison and no easy way back.
Running an open-ended pilot "to learn" means there is never a moment when the evidence is weighed and a
decision is made. A pilot → production plan exists to make the pilot a measured experiment with a
pre-agreed path to scale, so success triggers production work that is already scoped and funded.

## How it works
### 1. Define the stages and what each proves
A common progression is PoC → pilot → limited rollout → general availability. Each stage widens exposure
and answers a different question: feasibility; real-workflow impact; operability at scale; full adoption.
Name the question for each stage *because* a stage without a question has no natural end.

### 2. Measure the baseline before the pilot starts
Capture the current metric (handle time, error rate, cycle time) for the pilot cohort and a comparison
group *before* switching anything on. Without a baseline, any post-pilot number is uninterpretable,
*because* you cannot separate the system's effect from seasonality or the attention of being piloted.

### 3. Choose the cohort deliberately
Pilot cohorts tend to be enthusiastic volunteers, which inflates results. Include at least some typical
users, and if possible keep a comparable group without the tool. Size the cohort so the metric can move
detectably within the pilot's duration.

### 4. Write success, guardrail and exit criteria
- **Success metrics:** the business outcome the system is meant to move.
- **Guardrail metrics:** what must not get worse (customer satisfaction, error escapes, compliance
  incidents, cost per transaction).
- **Exit criteria:** the go/no-go rule, e.g. "median handle time down ≥ 15% with no guardrail regression
  over four weeks".
- **Stop triggers:** conditions that halt the pilot early, such as a data leak or a severe error.

Guardrails matter *because* AI systems often improve the headline metric by shifting cost elsewhere, for
instance faster replies with more escalations.

### 5. Build the production-gap list during the pilot, not after
List everything production needs that the pilot skipped: identity integration, security and privacy
review, data residency, monitoring and alerting, on-call, runbooks, support process, cost budget and
quotas, evaluation regression gates, user training. Each item gets an owner and an estimate. Running these
in parallel with the pilot is what prevents purgatory, *because* most of them are slow organizational
processes, not code.

### 6. Name owners and the handoff
Decide who owns the system after launch: which team runs it, who funds inference cost, who triages
quality issues. If the builders are external (an FDE team, a vendor), the handoff plan is part of the
production plan.

### 7. Hold the go/no-go and roll out in steps
At the end of the pilot, compare against the exit criteria in a scheduled meeting with the decision
makers. If go, expand in increments with rollback triggers, followed by a hypercare period of heightened
monitoring and fast response.

## Example
A support-ticket drafting assistant for an insurer's customer service team. All figures are illustrative.

- **Baseline (weeks −2 to 0):** two pilot teams (30 agents) and two comparison teams; median handle time
  11.0 minutes, quality-audit pass rate 92%, CSAT 4.2/5.
- **Pilot (weeks 1–6):** pilot teams use AI drafts; agents edit and send. Weekly audit of 200 randomly
  sampled AI-assisted tickets. At a pass rate near 92%, a 200-ticket sample estimates it to within about
  ±4 points at 95% confidence, enough to catch a large regression but not a one-point drift.
- **Exit criteria (agreed week 0):** handle time down ≥ 15% relative to the comparison teams; audit pass
  rate not below 90%; CSAT not below 4.1; inference cost under the per-ticket budget agreed with finance.
- **Stop trigger:** any instance of a draft exposing another customer's data.
- **Production-gap list, run in parallel:** SSO and role mapping (IT, 3 weeks), privacy review of
  prompt logging (legal, 4 weeks), monitoring dashboards and alerts (platform team), runbook and on-call
  rota (support engineering), training for 400 agents (enablement), cost centre for inference (finance).
- **Week 6 result:** handle time 9.1 minutes vs. 10.8 in the comparison teams (−16%); audit pass rate
  91%; CSAT 4.2. Go decision. Rollout to 25%, then 100% over six weeks, with rollback if audit pass rate
  drops below 90% for two consecutive weeks. Hypercare for the first four weeks after full rollout.

Because the gap list ran in parallel, production started two weeks after the decision instead of months.

## Trade-offs
- A comparison group gives a credible causal estimate, but it delays benefits for those users; it flips
  when the expected effect is so large or obvious that a before/after comparison is convincing enough.
- Volunteer cohorts make pilots easier to run and generate goodwill, but they overstate adoption and
  impact; include typical users when the decision depends on organization-wide adoption.
- Longer pilots smooth out weekly noise and seasonality, but delay value and erode stakeholder patience;
  set duration from how long the metric needs to stabilize, not by habit.
- Running production-readiness work in parallel shortens time to launch, but spends effort on a system
  that might get a no-go; this is worth it when the PoC evidence was strong.

## Pitfalls
- No baseline measured before the pilot, so the result is a before/after story nobody can verify; the
  symptom is a debate at go/no-go about what the number "would have been".
- A champion-only cohort whose results do not transfer; the symptom is strong pilot metrics followed by
  low adoption and complaints when typical users get access.
- Pilot running in a sandbox exempt from security and identity requirements, with no plan to meet them;
  the symptom is a go decision followed by months waiting on reviews.
- No guardrail metrics, so the headline metric improves while cost moves elsewhere; the symptom is faster
  handle times alongside rising escalations or complaints.
- No named post-launch owner or budget, so the builders keep running it informally; the symptom is the
  pilot engineer still answering production questions six months later.

## When to use
Write the plan when a PoC has passed and before the pilot starts, because baseline measurement and
criteria must be fixed before anyone sees results. Use the full staged approach when the system touches
many users, regulated data or customer-facing outcomes. For small internal tools with easy rollback,
compress it to a short pilot and a checklist. Measure the plan's quality by whether the go/no-go meeting
is a short reading of agreed numbers and whether production follows within weeks. In interviews, a
strong answer names baseline, cohort, guardrails, gap list, owner and rollback.

## Role lens
- **Engineer:** Instruments the pilot so success and guardrail metrics are captured automatically, and
  turns production-gap items like monitoring and evaluation gates into tracked work.
- **Architect:** Owns the stage gates and the production-gap list, deciding which pilot shortcuts are
  acceptable and which must be resolved before wider rollout.
- **FDE:** Negotiates exit criteria and the comparison group with the customer, chases their security and
  identity reviews in parallel, and plans the handoff to the customer's own team.

## Check yourself
**Q:** A pilot of an AI coding assistant with 25 volunteer developers reports "developers say they are
40% more productive". Leadership wants to roll it out to 2,000 developers next month. What do you ask
before agreeing?
**A:** First, what was measured: self-reported productivity from volunteers is weak evidence, inflated by
enthusiasm and novelty. Ask for a baseline and comparison group on observable metrics such as cycle time,
review rework and defect rates, plus guardrails like security findings in generated code. Then the
production gap: licensing or inference cost at 2,000 seats, identity and policy controls, data-handling
review for code sent to the provider, support and training for non-volunteers. Propose a staged rollout
to a few hundred typical developers with agreed exit criteria and rollback, rather than a jump from 25 to 2,000.

## Quiz
### Q: Why must the baseline be measured before the pilot begins?
- [ ] Because the system cannot log metrics once it is live
- [x] Because without it you cannot separate the system's effect from seasonality or the attention of being piloted
- [ ] Because finance requires baselines for any budget approval
- [ ] Because the model's accuracy changes after deployment
> The pilot's value is a comparison. Measured afterwards, the baseline is contaminated or reconstructed from
> memory, and the go/no-go turns into argument rather than evidence.

### Q: What is the main purpose of guardrail metrics in a pilot plan?
- [ ] To replace the success metric when it is hard to measure
- [ ] To monitor GPU utilization during the pilot
- [x] To catch cases where the headline metric improves by pushing cost or harm elsewhere
- [ ] To decide which users join the pilot cohort
> AI systems can speed one step while increasing escalations, errors or cost downstream. Guardrails make
> those regressions part of the decision, so a "win" is not a cost shifted elsewhere.

### Q: Which practice most directly prevents "pilot purgatory"?
- [ ] Extending the pilot until every user has tried the system
- [ ] Choosing only enthusiastic volunteers for the cohort
- [x] Building and staffing the production-gap list in parallel with the pilot, with named owners
- [ ] Postponing security review until after the go decision
> Purgatory happens when success arrives with no path, owner or budget for production. Working the gap list
> during the pilot means a go decision can be acted on quickly; postponing reviews causes the stall.
