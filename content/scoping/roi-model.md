---
id: roi-model
module: scoping
name: ROI model
kind: practice
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - scoping/success-metrics-kpis
  - scoping/baseline-measurement-before-deployment
  - infra-capacity-economics/cost-modeling
  - infra-capacity-economics/break-even-analysis
  - infra-capacity-economics/tco
  - infra-capacity-economics/unit-economics-per-successful-task
  - adoption/exec-communication
---

## TL;DR
An ROI model turns the success metrics into money: measured baseline × expected improvement ×
adoption × capture rate, minus one-time and recurring costs, over a stated horizon. Build it as ranges
with a sensitivity analysis and a break-even point, not as a single heroic number. The assumptions
that decide AI ROI, such as how much freed time turns into real savings and how fast people adopt the
tool, are rarely the ones vendors talk about.

## Why it exists
AI projects compete for budget against every other project, and the person who approves the budget
reasons in payback periods and net present value, not in eval scores. If you cannot say what the
system is worth, someone else will decide for you, usually with a vendor's slide or a gut feeling.
The model also protects the customer: a use case that cannot clear break-even under plausible
assumptions should be found in week two, not in month nine.

The naive version is a one-line spreadsheet: "agents spend 12 minutes per ticket, AI saves 50%,
40,000 tickets a month, so we save 240,000 minutes." It fails in four ways. It treats freed time as
money, but salaries do not fall when each person saves twenty minutes a day unless hiring is avoided,
overtime drops, or the time goes to work that has value. It assumes full adoption from day one, when
real adoption ramps and plateaus below 100%. It ignores the recurring costs AI adds: inference per
task (including failed and retried tasks), human review of outputs, eval-set upkeep, monitoring, and
re-validation when a model version is deprecated. And it presents a point estimate. A point estimate
hides which assumption the whole case rests on, so no one knows what to measure in the pilot to
confirm or kill it.

## How it works
### Benefit lines
Build each benefit as a product of measurable factors, so every factor can be traced to a baseline or
a pilot measurement:

- **Labour efficiency** = eligible volume × adoption × time saved per used case × loaded cost per
  minute × capture rate. The *capture rate* is the share of freed time that turns into cash or
  redeployed capacity, and finance should own that number. It is high when the team is growing (hires
  avoided), when there is overtime or contractor spend, or when there is a backlog with a cost. It is
  low when the freed time just becomes slack.
- **Quality / error avoidance** = volume × change in error rate × cost per error (rework, credits,
  write-offs, penalties). This only counts if the error rate was baselined.
- **Revenue** = affected volume × change in conversion or speed × value per unit. This is the hardest
  line to attribute, so it needs a holdout group to defend.
- **Risk reduction** = change in probability × impact of a loss event. Present it separately, because
  finance discounts it heavily.

### Cost lines
- **One-time:** discovery, integration, data preparation, building the eval set (SME hours count),
  security review, training and change management.
- **Recurring:** inference cost per task × total tasks attempted, platform or vendor fees, human
  review minutes per output × loaded cost, engineering upkeep (prompt and eval maintenance,
  monitoring, incident response), and periodic re-validation when the provider retires a model.

Divide recurring cost by *successful* tasks, not attempted ones. Retries and failed attempts consume
tokens but produce nothing, and the review time on a bad draft is a cost, not a saving.

### Horizon, ramp and summary measures
Model month by month over a horizon the sponsor uses, often 24 or 36 months, with an adoption ramp
taken from comparable rollouts or the pilot. Report three numbers, because they answer different
questions:
- **ROI** = (total benefit − total cost) / total cost.
- **Payback month**, the month cumulative net value turns positive, which is the cash-flow question.
- **NPV**, net value discounted at the customer's rate, for comparing against other investments.

### Sensitivity and break-even
Vary one input at a time across its plausible range and rank the inputs by how far they swing NPV.
This is a tornado analysis. Then solve for the break-even value of the most uncertain input. The model
exists to answer this question. If break-even needs 2 minutes saved and the pilot measures 4, the
case is robust. If it needs 3.8, the pilot has to be designed to measure that one number precisely.

## Example
A drafting assistant for a 40,000-ticket-per-month support team. All inputs are illustrative. A real
model takes volume and handle time from the baseline, loaded cost and capture rate from finance, and AI
cost per ticket from a token measurement on real traffic.

```python
BASE = dict(tickets=40_000, min_saved=4.0, cost_per_min=0.80, capture=0.6,
            adoption_ramp=[0.2, 0.35, 0.5, 0.6, 0.7], adoption_steady=0.7,
            ai_cost_per_ticket=0.04, review_min=0.0, platform=6_000, upkeep=10_000,
            build=180_000)                  # review_min=0: min_saved is measured net of review

def monthly(p, month):
    ramp = p["adoption_ramp"][month] if month < len(p["adoption_ramp"]) else 1.0
    a = min(ramp, p["adoption_steady"])          # the plateau caps the ramp
    benefit = p["tickets"] * a * (p["min_saved"] - p["review_min"]) * p["cost_per_min"] * p["capture"]
    run = p["tickets"] * p["ai_cost_per_ticket"] + p["platform"] + p["upkeep"]  # drafts for every ticket
    return benefit, run

def evaluate(p, months=24, rate=0.10):
    cum, payback, npv = -p["build"], None, -p["build"]
    for m in range(months):
        b, r = monthly(p, m)
        cum += b - r
        npv += (b - r) / (1 + rate / 12) ** (m + 1)
        if payback is None and cum >= 0:
            payback = m + 1
    benefit = sum(monthly(p, m)[0] for m in range(months))
    cost = p["build"] + sum(monthly(p, m)[1] for m in range(months))
    return dict(roi=(benefit - cost) / cost, payback=payback, npv=round(npv))

print("base", evaluate(BASE))                      # ROI ~ +100%, payback month 8, NPV ~ 517k
for k, v in [("capture", 0.3), ("adoption_steady", 0.4), ("min_saved", 2.5), ("upkeep", 20_000)]:
    print(k, v, evaluate({**BASE, k: v}))
# capture 0.3 -> ROI ~ 0, no payback in 24 months; adoption 0.4 -> +19%, month 16
# min_saved 2.5 -> +25%, month 15;               upkeep 20k -> +43%, month 11

lo, hi = 0.0, 10.0                                 # break-even on minutes saved
for _ in range(40):
    mid = (lo + hi) / 2
    lo, hi = (mid, hi) if evaluate({**BASE, "min_saved": mid})["roi"] < 0 else (lo, mid)
print(f"break-even minutes saved: {hi:.2f}")       # ~2.0
```

The base case looks strong. The sensitivity run shows that halving the capture rate wipes out the
return entirely, while doubling engineering upkeep roughly halves it but leaves it clearly positive. So the most valuable scoping
conversation is with finance about what happens to freed time, not with engineering about tokens.
The pilot must also measure minutes saved precisely, because the case survives anything above about
2 minutes.

## Trade-offs
- Ranges and sensitivity analysis are more honest than a point estimate, but sponsors often want one
  number. Lead with the conservative case, and show the range alongside it.
- Including risk-reduction and revenue benefits raises the headline, but those lines are hard to
  attribute. Keep them separate unless a holdout group can measure them.
- A longer horizon makes payback look easier, but it assumes model costs, prices and the workflow stay
  stable. Use 24 months or less when the provider or the process is likely to change.
- A detailed bottom-up model is defensible but slow to build. For an early go/no-go, a 10-line
  version with honest ranges is enough, provided the break-even input is identified.
- Counting capture rate at 100% simplifies the story but overstates value. It is only justified when
  the team is actively hiring or paying for overtime the tool will remove.

## Pitfalls
- Treating time saved as cash saved. The symptom is a CFO asking which budget line goes down, and
  nobody being able to answer.
- Pricing inference on successful tasks only. The symptom is actual monthly spend running well above
  the model, because retries, failed runs and unused drafts were never counted.
- Assuming instant, complete adoption. The symptom is a payback month that slips every quarter while
  usage sits on a plateau far below the modelled figure.
- Forgetting the human review cost. The symptom is "time saved" shrinking in the pilot because
  reviewers spend minutes checking each output.
- Building the model before the baseline exists. The symptom is ROI inputs that the customer's own
  data contradicts when the pilot report is reviewed.

## When to use
Build a first-cut ROI model during scoping, as soon as the success metrics are agreed. Use it to kill
or prioritise use cases before anyone writes integration code. Refine it with baseline numbers, then
with pilot measurements, and re-run it before any expansion decision. Skip it only for exploratory
work explicitly funded as learning. In practice the most useful outputs are the break-even value and
the ranked sensitivity list, because they tell you which number the pilot must measure. In interviews,
expect "this use case saves 50% of time, what is it worth?" The strong answer asks about capture rate,
adoption, review cost and cost per successful task before multiplying.

## Role lens
- **Engineer:** Measures real cost per successful task from traces, including retries and failures, and
  supplies the inference and upkeep numbers the model needs.
- **Architect:** Owns the cost structure behind the model: model tier, caching, review design and
  hosting choices, which set recurring cost and therefore break-even.
- **FDE:** Builds the model with the customer's finance and operations owners, so capture rate and
  loaded cost are their numbers, which makes the result credible internally.

## Check yourself
**Q:** A pilot shows a drafting tool saves 5 minutes per ticket, but the sponsor's finance team rejects
the ROI case. What are the likely reasons, and how do you fix the model?
**A:** Most likely the model counted all freed time as cash. If the team is not shrinking or avoiding
hires, those minutes do not reduce any budget line, so the capture rate is far below 100%. Other
likely gaps: the 5 minutes was measured on the cases where the tool was used, not across all eligible
work at realistic adoption; review time on bad drafts was not subtracted; and inference was priced per
successful draft instead of per attempt. I would rebuild with finance's own capture assumption, which
might be avoided hiring next year, overtime or backlog cost. I would use the measured adoption curve
and full per-attempt cost, then show the break-even capture rate so they can judge whether it is plausible.

## Quiz
### Q: In an AI labour-efficiency benefit, what does the capture rate represent?
- [ ] The share of tickets the model handles without a human
- [x] The share of freed employee time that turns into real savings or valuable redeployed capacity
- [ ] The fraction of model outputs that pass the eval suite
- [ ] The share of users who adopt the tool after training
> Saving minutes reduces cost only if hiring, overtime or contractor spend falls, or if the time goes to
> valuable work. Capture rate encodes that, and it often swings ROI more than accuracy does. Adoption
> and automation rate are separate factors.

### Q: Why should recurring AI cost be divided by successful tasks rather than attempted tasks?
- [ ] Providers only bill for successful completions
- [ ] Attempted tasks are hard to count in logs
- [x] Failed attempts and retries consume tokens and review time but deliver no value, so they belong in the cost of each success
- [ ] It makes the cost per task look smaller to the sponsor
> Cost per successful task is the true unit cost. Failures, retries and drafts that get discarded are
> paid for but produce nothing. Dividing by attempts understates the cost of the value you actually
> deliver.

### Q: A sensitivity analysis shows NPV swings most with minutes saved per case. What should the scoping team do?
- [ ] Raise the assumed minutes saved to give the case more margin
- [ ] Drop the sensitivity analysis and present the base case
- [ ] Shorten the horizon so the assumption matters less
- [x] Compute break-even minutes saved and design the pilot to measure that input precisely
> The highest-sensitivity input decides the investment, so the pilot should measure it against the
> break-even value. Raising the assumption hides the risk. Shortening the horizon changes the question
> without reducing the uncertainty.
