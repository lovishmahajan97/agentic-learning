---
id: adoption-and-value-realization-measurement
module: adoption
name: Adoption & value-realization measurement
kind: metric
difficulty: advanced
est_minutes: 16
status: crisp
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
Adoption measures whether the intended people use the system for the intended work; value realization
measures whether that use changed a business outcome versus a baseline or comparison group. Keep them
apart: adoption leads weekly, realized value renews the contract.

## Why it exists
- **Every deployment is sold on a projection.** An ROI model says something like "25% faster claims
  handling across 200 adjusters". Ninety days later someone has to say how much of that showed up.
- **Adoption gets reported as value.** "80% of adjusters used it this month" says nothing about whether
  claims got faster. The tool could be used and ignored, or used and slower.
- **Before/after claims credit for everything that changed.** A new intake form, a seasonal dip in claim
  complexity or a staffing change all move the number. Early adopters also tend to be the fast ones, so
  adopter-versus-non-adopter comparisons measure who chose the tool, not what it did.
- **Self-reported savings are optimistic.** Hours "saved" that are not redeployed to backlog, volume or
  reduced overtime never reach a P&L.
- **The forcing problem is attribution under business conditions.** You rarely get a clean lab experiment
  but still owe the sponsor a defensible number, a range around it, and an explanation of the gap. That
  explanation decides the next move: more training, product fixes, or re-scoping.

## How it works
### 1. Write the value equation before go-live
Make the ROI model's chain explicit so each link can be measured separately:

```
realized value ≈ eligible volume × adoption share × effect per assisted unit × conversion − run cost
```

| Factor | Meaning |
|---|---|
| *Adoption share* | Fraction of eligible work actually done with AI assistance, not the fraction of users who logged in |
| *Effect per assisted unit* | Change in the outcome metric (minutes, error rate, revenue) on that work |
| *Conversion* | Whether the gain becomes money or capacity: freed hours absorbed into backlog, avoided hires, reduced overtime |

- **Decompose *because* a total shortfall is useless on its own.** You need to know which factor fell
  short to know what to fix.

### 2. Define adoption as a ladder, not a number
- **Breadth:** share of eligible users active weekly.
- **Depth:** share of their eligible work items AI-assisted.
- **Durability:** cohort retention past the novelty period.
- **Targets by date,** for example 30/60/90 days.
- **Adoption is the lever the delivery team controls most directly,** through training, workflow
  integration and change management.

### 3. Choose the strongest attribution design the situation allows
From strongest to weakest:

| Design | How | Caveat |
|---|---|---|
| **Randomised holdout** | A random subset of users or cases keeps the old process | Clean but politically hard when the tool is popular |
| **Staggered rollout with difference-in-differences** | Groups go live at different dates; compare each group's change with the change in groups not yet live | Removes shared trends such as seasonality or a process change, provided groups would have moved in parallel without the tool |
| **Matched comparison** | Compare against similar teams that did not get the tool | Selection bias remains |
| **Before/after against a baseline** | Compare with the pre-launch period | Valid only if nothing else changed; always report as the weakest evidence |
| **Self-report surveys** | Ask users | Useful for satisfaction and for *why*, not for the size of the effect |

- **Check that pre-period trends really were parallel** before trusting a DiD number.
- **With many groups going live at different dates,** compare each group only against groups not yet
  live. A single pooled regression that also uses already-live groups as controls can be biased when the
  effect grows over time.
- **Use intention-to-treat** (all work in the live group, used or not) for the deployment's real effect.
  Dividing it by the adoption share gives a rough per-assisted-unit effect, under the assumption that
  non-assisted work did not change.

### 4. Put a range on the estimate honestly
- **The tempting error bar is wrong.** Computed from thousands of claims, it comes out tiny, because it
  ignores that treatment was assigned to *groups* (regions, teams), not individual claims, and groups
  drift apart for reasons unrelated to the tool. With two regions you have, in effect, one comparison.
- **Placebo tests:** run the same DiD calculation on pre-launch weeks, pretending a go-live happened when
  it did not. The spread of those fake "effects" shows how much groups wander apart on their own; a real
  effect has to stand clearly outside it.
- **Unit-level variation:** when the rollout is staggered over many teams, compute the estimate per team
  (or bootstrap by resampling teams, not claims) and report the spread across teams.
- **Watch the effect over time.** Week-6 numbers can include novelty and extra attention from the launch
  team, so re-check the per-assisted-unit effect at the 90-day point before it goes into a renewal.

### 5. Pair every value metric with guardrails
- **Faster but worse is not value.** Speed gains that raise error, rework, complaint or
  compliance-exception rates do not count.
- **Agree thresholds in advance,** so "faster but worse" is caught by the definition and not argued about
  afterwards.

### 6. Run a value-realization cadence
1. **Report projected versus realized value by factor** at each checkpoint.
2. **Give ranges, not point estimates,** and state the attribution method, the assumptions and the
   guardrail status.
3. **Assign each shortfall to an owner:** adoption gaps to enablement, effect gaps to engineering,
   conversion gaps to the customer's operations leadership.

## Diagram
Each factor of the value equation is measured separately, so a shortfall routes to the team that can
fix it; guardrails decide whether a gain counts at all.

```mermaid
flowchart TD
    P["Projected value from ROI model"] --> R["Realized value, with a range"]
    R --> G{"Guardrails held?"}
    G -->|"no"| X["Not value: fix quality first"]
    G -->|"yes"| F{"Which factor fell short?"}
    F -->|"adoption share"| EN["Enablement and workflow fit"]
    F -->|"effect per assisted unit"| ENG["Engineering: quality or task fit"]
    F -->|"conversion"| OPS["Customer operations leadership"]
    A["Attribution: holdout, staggered DiD"] --> R
    PL["Placebo weeks"] -.->|"realistic range"| R
```

## Examples
### Worked scenario: claims-intake assistant, staggered by region
A claims-intake assistant for 200 eligible adjusters, each handling about 45 claims a week (9,000 claims a
week in total). All numbers are illustrative.

| Item | Value |
|---|---|
| Baseline, four weeks before launch | 40 minutes of handling time per claim |
| Projection | a 25% cut (10 minutes) on assisted claims at 90% adoption: 10 × 0.9 × 9,000 = 81,000 minutes ≈ **1,350 hours a week** |
| Rollout | region A live in week 0, region B in week 6, a natural comparison |
| Region A, all claims, weeks 0–6 | 40 → 32 minutes (−8) |
| Region B, not yet live | 41 → 38 minutes (−3); a simplified intake form shipped company-wide in week 2 |
| Difference-in-differences | −8 − (−3) = **−5 minutes per claim** attributable to the assistant (pre-launch trends were flat and parallel) |
| Placebo check | pretend go-lives in pre-launch weeks gave "effects" between −1 and +1 minute, so report about −4 to −6 minutes, not −5.00 ± 0.1 from the claim-level standard error |
| Naive before/after, adopters only | −9 minutes: it included the form change and the fact that fast adjusters adopted first |

Decomposition at week 6 in region A:
- **Adoption share:** 65% of adjusters weekly active, using it on 70% of their claims, so about
  0.65 × 0.70 ≈ 0.46 of claims (assuming similar volumes per adjuster).
- **Effect per assisted claim:** 5 ÷ 0.46 ≈ **11 minutes** (about 9–13 across the range), in line with the
  projected 10.
- **Realized value at full rollout,** if region B reaches region A's adoption: 5 × 9,000 = 45,000 minutes
  ≈ **750 hours a week** (600–900 across the placebo range), about 56% of projection (roughly 45–65%).
- **The gap is adoption** (0.46 against a planned 0.9), not model quality. The next investment is
  enablement: the 35% of adjusters who do not use it, and the 30% of claims active users still handle
  unassisted.
- **Guardrail:** claim reopen rate moved from 4.1% to 4.3%, inside the agreed ±0.5-point band.
- **Conversion, checked separately:** the customer's backlog fell, so freed hours were absorbed rather than
  lost to slack.

### Value-equation calculator in Python
The same scenario as code, so the decomposition and range are reproducible at every checkpoint.

```python
claims_per_week = 9_000                 # 200 adjusters x 45 claims
projected_h = 10 * 0.9 * claims_per_week / 60          # minutes -> hours

def did(treat_pre, treat_post, ctrl_pre, ctrl_post):
    return (treat_post - treat_pre) - (ctrl_post - ctrl_pre)

effect = did(40, 32, 41, 38)            # minutes per claim, intention-to-treat
placebo_spread = 1.0                    # fake "effects" ranged -1 to +1 minute
low, high = abs(effect) - placebo_spread, abs(effect) + placebo_spread

adoption_share = 0.65 * 0.70            # breadth x depth
for name, e in (("low", low), ("mid", abs(effect)), ("high", high)):
    per_assisted = e / adoption_share
    realized_h = e * claims_per_week / 60
    print(f"{name}: {per_assisted:.0f} min/assisted claim, "
          f"{realized_h:.0f} h/week, {realized_h / projected_h:.0%} of projection")
print(f"projected {projected_h:.0f} h/week, DiD {effect} min/claim")
# low: 9 min/assisted claim, 600 h/week, 44% of projection
# mid: 11 min/assisted claim, 750 h/week, 56% of projection
# high: 13 min/assisted claim, 900 h/week, 67% of projection
# projected 1350 h/week, DiD -5 min/claim
```

## Production patterns
- **Signed measurement plan before go-live** — during scoping, write the baseline window, the value
  equation, the attribution design and the guardrail thresholds into one page the sponsor and finance
  agree. Monitor whether the baseline metric definition changes in the customer's systems mid-project.
- **Rollout calendar as experiment design** — when a holdout is politically impossible, schedule regions or
  teams to go live weeks apart so not-yet-live groups are controls. Monitor a pre-period trend chart per
  group to confirm parallel trends before reading any DiD result.
- **Placebo band on every estimate** — rerun the DiD on pre-launch weeks with fake go-live dates and report
  the realized estimate with that spread. Monitor the band width; if it is as large as the effect, say so.
- **Factor table at 30/60/90 reviews** — show projected versus realized for adoption share, effect per
  assisted unit and conversion, each with a named owner and a date. Monitor adoption weekly and the effect
  monthly once volume accumulates.
- **Guardrail band written into the definition** — for example reopen rate within ±0.5 points of baseline;
  a breach voids the value claim for that period. Monitor guardrail metrics on the same cadence as value.
- **90-day re-check before renewal** — recompute the per-assisted-unit effect after the launch team's
  attention fades, because week-6 numbers can include novelty.

## Trade-offs
- **Randomised holdout vs goodwill.** It gives the most credible effect estimate but withholds a useful
  tool from some users; when politics forbid it, a staggered rollout is the usual compromise.
- **Intention-to-treat vs per-use effect.** ITT is honest about real deployment impact but understates
  per-use effect; divide by adoption share only when non-assisted work plausibly did not change.
- **Ranges vs one number.** Reporting ranges and assumptions protects credibility with finance, but
  executives may prefer one number; give the conservative end as the headline when the decision is a
  renewal.
- **Business outcomes vs speed of signal.** Outcomes take weeks to accumulate, so you also track leading
  indicators (adoption, acceptance) that move faster but prove nothing about value on their own.

## Pitfalls
- **Reporting usage as value.** The dashboard shows high weekly actives, but finance finds no change in
  cost per case and stops trusting the AI team's numbers.
- **Before/after without a comparison group.** A large effect appears that coincides with a seasonal dip or
  a process change, and it shrinks or reverses the following quarter.
- **Comparing adopters with non-adopters.** The effect looks huge because self-selected adopters were
  already the strongest performers, which shows up as a gap that predates go-live.
- **Counting hours saved that are never redeployed.** Projected savings appear in slides, but headcount,
  overtime and backlog are unchanged, so the budget owner sees nothing.
- **Claim-level error bars on a region-level rollout.** The reported range is implausibly tight, and the
  next region's result lands far outside it.
- **Skipping guardrails.** Handling time improves while rework or complaints rise, discovered later
  through escalations instead of the measurement plan.

## When to use
- **Set it up during scoping,** when you agree the baseline and the ROI model, and start reporting at the
  first 30-day checkpoint.
- **Mandatory for:** anything sold on a business case, renewals and expansions, and internal platform
  teams defending budget.
- **Lighter version:** for small internal productivity tools, an adoption ladder plus a baseline comparison
  may be proportionate.
- **Measure:** adoption weekly; value monthly or per checkpoint, once enough volume has accumulated.
- **Interviews:** expect "how would you prove this worked?". The strong answer names the comparison group,
  decomposes the gap, and names the guardrails.

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

## Interview questions
### Why keep adoption and value realization as separate measurements?
- **Adoption is a leading indicator** of whether the intended people use the system for the intended work;
  the delivery team can act on it weekly through training and workflow integration.
- **Value is a lagging, attributed outcome** that needs a baseline or comparison group and weeks of volume.
- **Merging them hides the diagnosis:** high usage can coexist with no outcome change, and a shortfall
  cannot be routed to an owner unless the factors are measured separately.

### The sponsor refuses a holdout for a rollout across four regions. How do you design the measurement?
- **Stagger the go-live dates** so regions not yet live act as controls, and compute difference-in-
  differences against only not-yet-live groups.
- **Check parallel pre-period trends** per region before go-live, and run placebo DiDs on pre-launch weeks
  to size the natural drift.
- **Report intention-to-treat,** then divide by adoption share for a rough per-assisted-unit effect, with
  guardrails agreed up front.

### When is a plain before/after comparison acceptable, and what does it cost you?
- **Acceptable** for a small internal tool where a lighter, proportionate version is enough, and only when
  you can show nothing else changed in the period.
- **The cost:** any concurrent change (a new form, seasonality, staffing) is credited to the AI, so it is
  always reported as the weakest evidence.
- **Flip point:** once the number feeds a renewal or a budget decision, invest in a comparison group.

### A DiD over 9,000 claims a week gives minus 5 minutes with a standard error of 0.1. What do you report?
- **Not ± 0.1,** because treatment was assigned by region, so with two regions you effectively have one
  comparison; claim-level errors ignore group drift.
- **Run placebo tests** on pre-launch weeks; if fake effects span −1 to +1 minute, report about −4 to −6.
- **Translate the range into hours** (600–900 a week here) and lead with the conservative end for a renewal.

### How do you show that saved time actually reached the P&L?
- **Measure conversion as its own factor:** backlog, volume handled per head, overtime or avoided hires,
  owned by the customer's operations leadership.
- **Self-reported hours are not enough,** because unredeployed time never reaches a P&L.
- **In the claims example,** a falling backlog showed the freed hours were absorbed rather than lost to slack.

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
