---
id: success-metrics-kpis
module: scoping
name: Success metrics / KPIs
kind: metric
difficulty: core
est_minutes: 14
status: reviewed
related:
  - scoping/baseline-measurement-before-deployment
  - scoping/roi-model
  - scoping/requirements-traceability
  - evals/task-specific-acceptance-criteria
  - customer-data-evaluation/acceptance-testing-against-customer-kpis
  - adoption/adoption-and-value-realization-measurement
  - infra-capacity-economics/unit-economics-per-successful-task
  - agent-reliability/task-completion-rate
sources:
  - Kohavi, Tang, Xu, 2020 — Trustworthy Online Controlled Experiments
---

## TL;DR
Success metrics are the numbers the customer will use to decide whether the AI project worked; KPIs
are the business outcomes among them. Agree them before building, as a tree: one outcome KPI, a few
operational drivers, system-quality metrics you can measure daily, and guardrails that must not get
worse. You need the tree because model accuracy on its own proves no value, and any single metric gets gamed.

## Why it exists
AI projects rarely fail in the middle. They fail at the review meeting. The demo impressed, the pilot
ran for six weeks, and then the sponsor asks "so, did it work?" and nobody had agreed what "work" meant.
Engineers bring the numbers they can measure: 94% answer correctness on a golden set, p95 latency of
3 seconds. The sponsor needs a different kind of number: cost per resolved case, cycle time, revenue,
regulatory exposure. Nothing in the room connects the first set to the second, so the project gets
judged on anecdote. That might be the one embarrassing answer someone screenshotted.

The naive alternatives each break in a predictable way. "We'll know it when we see it" leaves the
verdict to recency bias, and the goalposts move: once accuracy is good, the question becomes adoption,
and once adoption is good, it becomes cost. Picking one metric invites Goodhart's law (a measure that
becomes a target stops being a good measure). A support bot's deflection rate climbs fastest when the
bot makes it hard to reach a human. Reporting only model metrics leaves the project defenceless at
budget time, because nobody can translate "faithfulness 0.91" into money. And metrics chosen *after*
deployment cannot be falsified, because people pick whichever one looks good. Defining metrics up front
is what makes the project a testable claim instead of a story.

## How it works
### Build a metric tree, not a list
Arrange metrics in levels connected by a stated causal hypothesis:

1. **Outcome KPI** (lagging, monthly or quarterly). This is what the sponsor reports upward: cost per
   resolved ticket, claim cycle time, days sales outstanding, analyst hours per report. Keep it to one
   or at most two. Several outcome KPIs dilute accountability.
2. **Operational drivers** (leading, weekly). These are the behaviours that should move the outcome:
   share of cases completed without a human, handle time on AI-assisted cases, adoption rate (share of
   eligible work where the tool was actually used), edit rate on drafts.
3. **System quality** (daily, from evals and logs). This is what the engineering team controls directly:
   task completion rate, extraction field accuracy, retrieval recall, escalation precision, p95 latency,
   cost per successful task.
4. **Guardrails** (counter-metrics). These must not get worse while the others improve: customer
   satisfaction, reopen rate, compliance errors, misrouted high-severity cases.

The tree exists because outcome KPIs move slowly and are confounded by seasonality, staffing and pricing
changes, so you cannot steer week to week by them. Leading indicators predict them. Writing the causal
links down also makes failures diagnosable: if level 3 improves but level 1 does not, one specific link
in the hypothesis is wrong, and you can see which one.

### Write a spec for every metric
Each metric gets a spec: the name, the exact numerator and denominator, the population (which cases,
with what exclusions), the data source and who owns that pipeline, the measurement window, the baseline
value and how it was measured, the target, and a named owner. This is tedious *because* it is the part
that gets disputed. In three departments "resolved" can mean three things: closed by an agent, no
customer reply for 72 hours, or not reopened within 7 days. The denominator matters just as much. If
you drop "out-of-scope" tickets after the fact, the automation rate inflates silently.

### Turn targets into decision rules
A target needs a reference point, a horizon and a consequence. For example: "median cycle time falls
from the measured baseline of X to at most Y within 90 days of pilot start. If it does, we expand to
region B. If it improves by less than Z, we stop." Without a pre-agreed decision rule the target is
decoration, and the review turns into a negotiation.

### Pair every target with its cheapest way to cheat
For each metric you optimise, name the cheap way to game it and add a guardrail that would catch it.
Deflection pairs with reopen rate and escalations within 24 hours. Handle time pairs with a QA score.
Auto-approval rate pairs with the error rate on auto-approved items, audited by sampling. This does not
assume bad faith. Optimisation pressure, including the team's own prompt iteration against the metric, finds the
cheap path by default.

### Check the target is attributable and detectable
A target is only useful if the pilot can tell whether it was hit, and that takes two more decisions
made now, not at the review. **Attribution:** what would the KPI have done without the system?
Outcome KPIs drift with seasonality, staffing and pricing, so agree the comparison up front: a
concurrent holdout group, a staggered rollout, or at minimum a before/after read against a comparison
queue (the baseline lesson covers these designs). **Detectability:** a rule of thumb from online
experimentation is n ≈ 16σ²/δ² cases per group for about 80% power at a two-sided 5% significance
level, where δ is the smallest change that matters and, for a rate, σ² = p(1−p). Guardrails are where
this bites, because they are rare-event rates. Detecting a misrouting rise from 1.5% to 2.5% needs about
16 × 0.015 × 0.985 / 0.01² ≈ 2,400 cases per group. If the pilot will not see that volume, lengthen the
window, accept a wider tolerance, or back the guardrail with targeted audits instead of the raw rate.

## Example
Here is a worked scenario with illustrative numbers. An insurer wants an assistant that reads
first-notice-of-loss (FNOL) emails, extracts policy number, loss date, loss type and description, and
pre-fills the claim record for an adjuster to confirm.

- **Outcome KPI:** median hours from FNOL receipt to claim set up. The baseline over 8 weeks was
  26 hours. The target is at most 8 hours, 90 days after the pilot starts.
- **Driver:** the share of FNOLs where every mandatory field was pre-filled and the adjuster accepted
  it without edits. The target is 70%.
- **System quality:** field-level accuracy on a 400-email golden set. The target is at least 99% for
  policy number and at least 90% for loss-type category, measured on every prompt or model change.
- **Guardrails:** the misrouting rate (claim sent to the wrong line of business) stays at or below the
  1.5% baseline, and no claim is set up against the wrong policy number, with weekly audits.

In week 3 the driver reads 78%, ahead of target, and cycle time has fallen to 11 hours. The guardrail
shows misrouting at 3.1%. Is that noise? With about 700 FNOLs that week (illustrative), 3.1% is roughly
22 misroutes against about 10 expected at the 1.5% baseline, and a count of 10 varies by only about ±3
week to week, so this is a real shift, not a bad draw. A drill-down finds that adjusters are accepting pre-filled loss types without
reading them on the busiest queue, and that the extractor confuses "escape of water" with "flood" in
one region's phrasing. Without the guardrail, the dashboard would have said "success" while claims
went to the wrong handlers. The team disables auto-accept for the water categories, adds 40
region-specific emails to the golden set, and re-runs the eval. The tree told them which link broke: the
driver rose for the wrong reason.

## Trade-offs
- More metrics give better diagnosis but blur accountability. Keep one outcome KPI and three to five
  drivers, and add more only if a review cannot locate a failure.
- Outcome KPIs are what sponsors care about, but they lag by weeks and carry confounders. Lean on
  leading drivers for steering unless the outcome moves fast, as in real-time conversion.
- Tight metric specs prevent disputes but take days of negotiation with data owners. For a throwaway
  proof of concept, a looser spec is acceptable if it is labelled as such.
- Ambitious targets motivate, but they make a sound project look like a failure. Set targets from a
  measured baseline and the realistic human error rate, not from vendor marketing.
- Hard guardrails (a breach stops rollout) protect users but can halt a sound launch over noise. Define
  the breach threshold with a confidence interval, not a single week's reading.

## Pitfalls
- Choosing model metrics as the success criteria. The symptom is a project that hits 95% accuracy and
  still gets cancelled because nobody could say what it saved.
- Leaving the denominator undefined. The symptom is two teams reporting different automation rates from
  the same data and the review turning into an argument about definitions.
- Setting targets before measuring the baseline. The symptom is a demand for 99% accuracy on a task
  where the human team, once audited, turns out to be 88% accurate.
- No guardrail for the optimised metric. The symptom is the headline number improving while complaints,
  reopens or downstream rework quietly rise.
- Measuring adoption as logins rather than use on eligible work. The symptom is high "active users"
  alongside no change in handle time.

## When to use
Define success metrics in the first two weeks of any customer-facing AI engagement, before
architecture choices, because the metrics decide what you build: a cycle-time KPI favours automation,
while a quality KPI favours assistive drafting with review. Skip the full tree only for internal
spikes whose sole question is technical feasibility, and say so in writing. In day-to-day work the tree
becomes the pilot dashboard and the agenda of every steering meeting. In interviews, expect "how would
you measure success for X?" A strong answer names the outcome, the drivers, the guardrail that catches
gaming, and how the baseline gets measured.

## Role lens
- **Engineer:** Instruments the system so every driver and quality metric can be computed from logs,
  with task IDs that join model events to business records.
- **Architect:** Owns the causal hypothesis linking system quality to business outcome, and makes sure
  the platform can attribute outcomes to a specific model and prompt version.
- **FDE:** Negotiates the metric specs with the customer's operations and finance owners, and gets the
  decision rule signed before the pilot starts.

## Check yourself
**Q:** A customer asks you to "maximise the share of chats resolved by the bot." Why is that a dangerous
sole KPI, and what would you propose instead?
**A:** Bot-resolved share rises if the bot makes escalation hard, ends chats early, or answers
confidently when it should hand off, so it rewards exactly the failures that hurt customers. I would
make the outcome KPI cost per *successfully* resolved contact, or contacts per customer per month.
Bot resolution share would be one driver, defined as no human contact and no repeat contact on the same
issue within 7 days. Guardrails would be CSAT on bot-resolved chats, the 7-day repeat-contact rate, and
the share of high-severity intents escalated within one turn. I would measure all of these over a
baseline window first, and agree in writing what result triggers expansion or a stop.

## Quiz
### Q: Why does a metric tree separate operational drivers from the outcome KPI?
- [ ] Drivers are easier for executives to understand than outcomes
- [x] Outcomes lag and are confounded, so leading drivers are needed to steer and to locate which causal link failed
- [ ] Drivers replace the outcome KPI once the pilot starts
- [ ] Outcome KPIs cannot be measured for AI systems
> Outcome KPIs such as cost per case move slowly and depend on seasonality and staffing. Drivers move
> weekly and predict the outcome. Keeping both, linked by a stated hypothesis, shows whether a missed
> outcome came from low adoption, poor quality, or a wrong assumption.

### Q: A deflection bot's containment rate rose from 40% to 65% in a month. What should you check first?
- [ ] Whether the model was upgraded to a larger version
- [ ] Whether latency improved enough to explain it
- [x] Guardrails such as repeat contacts and escalations within a few days, which reveal contained-but-unresolved chats
- [ ] Whether the golden-set accuracy also rose by 25 points
> Containment is easy to game, even unintentionally, by making handoff harder. The repeat-contact and
> reopen guardrails show whether contained chats were actually resolved. Accuracy and latency changes
> do not answer that question.

### Q: What makes a success target usable at a pilot review?
- [ ] It is ambitious enough to impress the sponsor
- [ ] It is expressed as a model metric so engineering can own it
- [ ] It is chosen after seeing the first month of pilot data
- [x] It has a measured baseline, a horizon and a pre-agreed decision such as expand or stop
> A target becomes a decision tool only when it has a reference point, a deadline and an agreed
> consequence. Targets chosen after seeing data cannot be falsified, and model metrics alone do not
> show business value.
