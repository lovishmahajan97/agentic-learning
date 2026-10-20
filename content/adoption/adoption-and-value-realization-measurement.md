---
id: adoption-and-value-realization-measurement
module: adoption
name: Adoption & value-realization measurement
kind: metric
difficulty: advanced
est_minutes: 16
status: fact-checked
related:
  - adoption/usage-analytics
  - scoping/baseline-measurement-before-deployment
  - scoping/roi-model
  - scoping/success-metrics-kpis
  - evals/a-b-testing
  - adoption/exec-communication
  - customer-data-evaluation/acceptance-testing-against-customer-kpis
  - adoption/change-management
sources:
  - Kohavi, Tang, Xu, 2020 — Trustworthy Online Controlled Experiments
  - Angrist, Pischke, 2009 — Mostly Harmless Econometrics
---

## TL;DR
Adoption measures whether the intended people use the system for the intended work. Value realization
measures whether that use changed a business outcome, compared with what would have happened without it.
You need both, kept apart. Adoption is a leading indicator you can act on weekly. Realized value is the
lagging number that renews the contract, and it can only be credited to the AI against a baseline or a
comparison group.

## Why it exists
Every AI deployment is sold on a projected value: an ROI model that says something like "25% faster
claims handling across 200 adjusters". Ninety days later someone has to say how much of that showed up.
Without a deliberate measurement design, three things go wrong.

First, adoption gets reported as value. "80% of adjusters used it this month" says nothing about whether
claims got faster. The tool could be used and ignored, or used and slower. Second, before/after
comparisons claim credit for everything that changed at the same time. A new intake form, a seasonal dip
in claim complexity, or a staffing change all move the number. Users who adopt early also tend to be the
fast ones, so comparing adopters with non-adopters measures who chose the tool, not what the tool did.
Third, self-reported time savings are routinely optimistic. Hours "saved" that are not redeployed to
backlog, volume or reduced overtime never reach a P&L.

The forcing problem is attribution under business conditions. You rarely get a clean lab experiment.
You still owe the sponsor a defensible number, a range around it, and an explanation of the gap between
projected and realized value. The explanation decides the next move: more training, product fixes, or
re-scoping.

## How it works
### 1. Write the value equation before go-live
Make the ROI model's chain explicit so each link can be measured separately:

```
realized value ≈ eligible volume × adoption share × effect per assisted unit × conversion − run cost
```

*Adoption share* is the fraction of eligible work actually done with AI assistance, not the fraction of
users who logged in. *Effect per assisted unit* is the change in the outcome metric (minutes, error rate,
revenue) on that work. *Conversion* is whether the gain becomes money or capacity: freed hours absorbed
into backlog, avoided hires, reduced overtime. Decomposing it this way matters *because* a shortfall in
the total is useless on its own. You need to know which factor fell short to know what to fix.

### 2. Define adoption as a ladder, not a number
Measure breadth (share of eligible users active weekly), depth (share of their eligible work items
AI-assisted) and durability (cohort retention past the novelty period). Set targets for each by date,
for example 30/60/90 days. Adoption is the lever the delivery team controls most directly: through
training, workflow integration and change management.

### 3. Choose the strongest attribution design the situation allows
From strongest to weakest:
- **Randomised holdout**: a random subset of users or cases keeps the old process. This is clean but
  politically hard when the tool is popular.
- **Staggered rollout with difference-in-differences**: groups go live at different dates, and you compare
  each group's change with the change in groups not yet live. This removes shared trends such as
  seasonality or a process change, provided the groups would have moved in parallel without the tool.
  Check that the pre-period trends really were parallel. With many groups going live at different dates,
  compare each group only against groups not yet live; a single pooled regression that also uses
  already-live groups as controls can be biased when the effect grows over time.
- **Matched comparison**: compare against similar teams that did not get the tool. Selection bias remains.
- **Before/after against a baseline**: valid only if nothing else changed. Always report it as the weakest
  evidence.
- **Self-report surveys**: useful for satisfaction and for *why*, but not for the size of the effect.
Use an intention-to-treat comparison (all work in the live group, used or not) when you want the
deployment's real effect. Dividing it by the adoption share gives a rough per-assisted-unit effect,
under the assumption that non-assisted work did not change.

### 4. Pair every value metric with guardrails
Speed gains that raise error, rework, complaint or compliance-exception rates are not value. Agree the
guardrail thresholds with the customer in advance, so "faster but worse" is caught by the definition and
not argued about afterwards.

### 5. Run a value-realization cadence
At each checkpoint, report projected versus realized value by factor. Give ranges, not point estimates,
and state the attribution method, the assumptions and the guardrail status. Assign each shortfall to an
owner: adoption gaps to enablement, effect gaps to engineering, conversion gaps to the customer's
operations leadership.

## Example
A claims-intake assistant for 200 eligible adjusters, each handling about 45 claims a week (9,000 claims a
week in total). The baseline measured over four weeks before launch was 40 minutes of handling time per
claim. The ROI model projected a 25% cut (10 minutes) on assisted claims at 90% adoption:
10 × 0.9 × 9,000 = 81,000 minutes ≈ **1,350 hours a week**. All numbers are illustrative.

Region A goes live in week 0 and region B in week 6, which gives a natural comparison. Over weeks 0–6:
- Region A, all claims: 40 → 32 minutes (−8).
- Region B, not yet live: 41 → 38 minutes (−3). A simplified intake form shipped company-wide in week 2.
- Difference-in-differences: −8 − (−3) = **−5 minutes per claim** attributable to the assistant (the
  pre-launch trends of the two regions were flat and parallel).

The naive before/after for adopters alone said −9 minutes. It included the form change and the fact that
fast adjusters adopted first.

Adoption in region A at week 6: 65% of adjusters weekly active, using it on 70% of their claims, so the
adoption share is about 0.65 × 0.70 ≈ 0.46 of claims (assuming similar volumes per adjuster). The implied
effect per assisted claim is 5 ÷ 0.46 ≈ **11 minutes**, slightly *better* than the projected 10.

Realized value at full rollout, if region B reaches the same adoption as region A: 5 × 9,000 = 45,000
minutes ≈ **750 hours a week**, about 56% of projection. The decomposition shows the gap is entirely
adoption (0.46 against a planned 0.9), not model quality, so the next investment is enablement: the 35%
of adjusters who do not use it, and the 30% of claims that active users still handle unassisted. Guardrail: claim reopen rate moved from 4.1% to 4.3%,
inside the agreed ±0.5-point band. Conversion is checked separately: the customer's backlog fell, so the
freed hours were absorbed rather than lost to slack.

## Trade-offs
- Randomised holdouts give the most credible effect estimate, but they withhold a useful tool from some users; when politics forbid it, a staggered rollout is the usual compromise.
- Intention-to-treat estimates are honest about real deployment impact but understate per-use effect; divide by adoption share only when non-assisted work plausibly did not change.
- Reporting ranges and assumptions protects credibility with finance, but executives may prefer one number; give the conservative end as the headline when the decision is a renewal.
- Measuring business outcomes takes weeks to accumulate signal, so you also track leading indicators (adoption, acceptance) that move faster but prove nothing about value on their own.

## Pitfalls
- Reporting usage as value: the dashboard shows high weekly actives, but the finance team finds no change in cost per case and stops trusting the AI team's numbers.
- Before/after without a comparison group: a large effect appears that coincides with a seasonal dip or a process change, and it shrinks or reverses the following quarter.
- Comparing adopters with non-adopters: the effect looks huge because self-selected adopters were already the strongest performers, which shows up as a gap that predates go-live.
- Counting hours saved that are never redeployed: projected savings appear in slides, but headcount, overtime and backlog are unchanged, so the budget owner sees nothing.
- Skipping guardrails: handling time improves while rework or complaints rise, discovered later through escalations instead of the measurement plan.

## When to use
Set up value-realization measurement during scoping, when you agree the baseline and the ROI model, and
start reporting at the first 30-day checkpoint. It is mandatory for anything sold on a business case,
for renewals and expansions, and for internal platform teams defending budget. For small internal
productivity tools, a lighter version (adoption ladder plus a baseline comparison) may be proportionate.
Measure adoption weekly. Measure value monthly or per checkpoint, once enough volume has accumulated.
In interviews, expect a "how would you prove this worked?" question. The strong answer names the
comparison group, decomposes the gap, and names the guardrails.

## Role lens
- **Engineer:** Builds the pipelines that join usage events to the customer's outcome data, and computes
  intention-to-treat and per-assisted-unit effects reproducibly.
- **Architect:** Designs the rollout so attribution is possible (staggered regions, holdout cohorts) and
  owns the value equation that links system metrics to the business case.
- **FDE:** Runs the 30/60/90 value reviews with the sponsor, explains shortfalls by factor, and turns the
  biggest gap into an action plan with named owners.

## Check yourself
**Q:** Ninety days in, realized value is 40% of the ROI projection. The sponsor asks whether the model is
underperforming. How do you answer?
**A:** I decompose the shortfall instead of guessing. Using the value equation, I check adoption share
(breadth × depth) against the plan, the effect per assisted unit from the comparison design, and whether
freed capacity was converted. If adoption is at 45% against a 90% plan while the per-assisted-unit effect
matches projection, the model is fine and the gap is enablement or workflow fit. If adoption is on plan
but the effect is small, it is a quality or task-fit problem for engineering. I state the attribution
method, give a range and confirm the guardrails held. Then I propose the specific next lever with an owner and date.

## Quiz
### Q: A staggered rollout shows the live region's handling time fell 8 minutes and the not-yet-live region's fell 3 minutes over the same weeks. What is the difference-in-differences estimate, and what does it assume?
- [ ] 8 minutes, assuming the not-yet-live region is irrelevant
- [x] 5 minutes, assuming both regions would have followed parallel trends without the tool
- [ ] 11 minutes, assuming the two changes add up
- [ ] 5 minutes, assuming adoption in the live region was 100%
> DiD subtracts the shared trend (−3) from the treated change (−8), giving −5. Its key assumption is
> parallel trends. Adoption below 100% does not invalidate it; it makes the result an intention-to-treat estimate.

### Q: Why is comparing outcomes of adopters versus non-adopters within the same team a weak attribution design?
- [ ] Because the two groups use different model versions
- [ ] Because adopters generate more analytics events
- [x] Because users choose to adopt, so differences may reflect who adopted rather than what the tool did
- [ ] Because non-adopters cannot have baseline measurements
> Self-selection is the problem. Early adopters are often already faster or more engaged, so their
> advantage predates the tool. Randomisation or staggered rollout breaks that link.

### Q: Realized value is half the projection, adoption share is half the planned level, and the per-assisted-unit effect matches the projection. What should the next investment target?
- [ ] Fine-tuning the model to improve output quality
- [x] Enablement and workflow integration to raise adoption among non-users
- [ ] Revising the baseline measurement upward
- [ ] Adding guardrail metrics to the dashboard
> The decomposition puts the entire gap in adoption, because each assisted unit delivers as projected.
> Improving the model would raise an effect that is already on target and leave the real shortfall untouched.
