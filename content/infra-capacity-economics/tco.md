---
id: tco
module: infra-capacity-economics
name: TCO (total cost of ownership)
kind: concept
difficulty: core
est_minutes: 15
related:
  - infra-capacity-economics/break-even-analysis
  - infra-capacity-economics/cost-modeling
  - infra-capacity-economics/finops
  - infra-capacity-economics/unit-economics-per-successful-task
  - infra-capacity-economics/hybrid-on-prem
  - system-design/build-vs-buy
  - security-governance-compliance/vendor-exit-strategy
  - llmops-platform/model-lifecycle-and-deprecation-policy
---

## TL;DR
Total cost of ownership is every cost an option incurs over a fixed horizon (acquiring it, running it,
changing it and eventually leaving it), not just the line on the invoice. For AI systems the invoice is
usually the smaller part: people, evaluation upkeep, forced model migrations and exit costs dominate. So
you compare options delivering the same outcome over the same horizon, and report a range, not a point.

## Why it exists
AI purchasing decisions get framed by the most visible price: dollars per million tokens, dollars per GPU
hour, dollars per seat. Each of those is real, and each describes only one slice of what the option will
cost. A per-token price says nothing about the engineer who keeps the prompts working when the provider
retires the model. A GPU hourly rate leaves out the serving stack, the on-call rotation and the
three-year hardware refresh. A per-seat SaaS price leaves out integration, the security review, the
annual price escalator and what it costs to get your data back out.

The failure mode is predictable. Option A wins on the headline price, gets chosen, and then the costs
nobody wrote down arrive as "unplanned" work: a migration sprint when a model is deprecated, a re-embedding
job when the embedding model changes, a quarter of platform work to make the self-hosted cluster reliable.
Six months in, the cheaper option is the expensive one, and nobody can say whether the decision was wrong
or just unlucky, because nothing was written down to compare against.

The naive fix, "add 20% for overhead", fails because the hidden costs are not proportional to the visible
one. Engineering cost is roughly fixed regardless of volume, licence cost scales with seats, token cost
scales with usage, and migration cost arrives in lumps on someone else's schedule. TCO exists to put all
of those on one timeline, so the comparison is honest and the assumptions can be checked later.

## How it works
### 1. Fix the outcome and the horizon
Options are only comparable if they deliver the same thing: the same task, at the same quality bar on your
evals, at the same latency and availability target. If the cheaper option scores lower, either add the
cost of closing the gap (fine-tuning, a reranker, human review) or express both in cost per successful
task. The horizon is usually three years: long enough that one-off build costs amortize, and roughly the
term of a hardware depreciation schedule or a committed-use contract. For a hosted-API product, a horizon
longer than the model generation you are building on is fiction, so keep it short and model the migration.

### 2. Enumerate costs in four buckets
- **Acquire (one-off):** build effort, integration with identity, data and logging systems, procurement
  and security review, building the eval set, initial data preparation and indexing, hardware purchase.
- **Operate (recurring):** tokens or GPU hours, licences, vector database and storage, observability,
  network egress, support contracts, and the fractional people who run it, carry the pager and answer
  user tickets.
- **Change (recurring but lumpy):** forced model migrations and the re-evaluation they need, prompt
  re-tuning, re-embedding a corpus when the embedding model changes (the old vectors are unusable with the
  new model), serving-stack upgrades, price changes at renewal.
- **Exit (contingent):** data export, re-integration with a replacement, contract minimums or unused
  commitments, parallel running during cut-over. You include it because it bounds how locked in you are.

### 3. Cost people honestly
People are usually the biggest line, and the one most often left out because it does not appear on a
cloud bill. Use a loaded cost per engineer-year (salary plus benefits, overhead, tooling), and estimate
fractions of FTE per activity. Include opportunity cost when the same engineers would otherwise ship
product: an option that consumes a platform team is not free because the team is already on payroll.

### 4. Put it on a timeline and discount it
Lay costs out by period. Capital purchases are depreciated over their useful life for accounting, but for
a decision compare cash flows. Discount future cash at a rate your finance team provides:
`NPV = Σ_t cost_t / (1 + r)^t`. Discounting matters *because* options differ in timing: a build is
front-loaded, a growing subscription is back-loaded, and undiscounted totals overstate the back-loaded one.

### 5. Model uncertainty as scenarios
Volume, adoption, provider price moves and model deprecation dates are all uncertain. Rather than one
number, run a base case plus the two or three assumptions that move the answer most (adoption, price,
team size), and attach expected costs to risk events as probability times impact. The output is a range
and a statement of which assumption flips the decision.

### How it relates to its neighbours
Break-even analysis is one slice of TCO along the volume axis. Cost modeling supplies the per-request
operate costs. Unit economics divides TCO by successful outcomes. FinOps is what checks the TCO model
against actual spend once the option is live.

## Example
Build a contract-review assistant on a hosted LLM API, or buy a vendor SaaS product priced per seat?
Every figure below is illustrative, in thousands of dollars.

```python
FTE, RATE = 200, 0.10               # loaded engineer-year; discount rate from finance

def build_on_api(users):
    upfront = 1.0 * FTE + 30        # 2 engineers x 6 months, plus building the eval set
    run = [u * 0.12                 # tokens at $120 per active user-year
           + 0.75 * FTE             # maintenance, on-call, prompt upkeep
           + 24                     # vector DB, observability, gateway
           + 40                     # one forced model migration and re-eval per year
           for u in users]
    return [upfront] + run

def buy_saas(users, seat=0.36, escalator=0.07):
    upfront = 60                    # integration, SSO, security review
    run = [u * seat * (1 + escalator) ** y + 0.25 * FTE for y, u in enumerate(users)]
    return [upfront] + run          # licence with annual escalator, plus admin time

def tco(flows, rate=0.0):
    return sum(c / (1 + rate) ** t for t, c in enumerate(flows))

for label, users in (("growth", [500, 1000, 1500]), ("flat", [500, 500, 500])):
    b, s = build_on_api(users), buy_saas(users)
    print(f"{label}: build {tco(b):.0f} (NPV {tco(b, RATE):.0f})  buy {tco(s):.0f} (NPV {tco(s, RATE):.0f})")
# growth: build 1232 (NPV 1051)  buy 1393 (NPV 1131)
# flat:   build 1052 (NPV 911)   buy 789 (NPV 662)
```

Three readings. First, in the build option tokens are a minority of cost even in year three ($180k of
$394k); the 0.75 FTE of upkeep is the biggest recurring line, so negotiating a cheaper token price would
barely move the answer. Second, the decision flips on adoption: per-seat pricing scales with users while
the build is mostly fixed, so build wins if usage triples and buy wins clearly if it stays flat. That makes
adoption the assumption to test before committing. Third, discounting halves build's advantage in the
growth case ($161k undiscounted, $80k NPV) because the subscription's large payments come late. A strong
write-up would also add exit cost to the buy side and the option value of reusing the built pipeline for a
second feature, then say which one the recommendation depends on.

## Trade-offs
- A full TCO model exposes hidden costs but takes days to build; for a reversible, small decision a
  one-page estimate is enough, and the full model pays off only when commitments exceed a quarter or so.
- Longer horizons amortize build cost and favour building, but in AI they also stretch past the life of
  the current model generation, so assumptions beyond about two years become guesses.
- Discounting gives a fairer comparison between front-loaded and back-loaded options, yet the discount rate
  is a finance choice, and a high rate can make deferring cost look better than it is.
- Including risk-weighted exit and migration costs makes lock-in visible, but probabilities are judgement
  calls, so present them separately from firm costs instead of folding them in silently.
- Committed-use and reserved pricing lowers operate cost per unit, but converts it into a fixed obligation
  that only pays if utilization materializes; it flips to a loss when adoption undershoots.

## Pitfalls
- Comparing the invoice line only, such as tokens versus GPU hours; the symptom is a "cheaper" option
  whose first quarter is consumed by unplanned platform and migration work.
- Leaving out people because they are "already on payroll"; it shows up as roadmap features slipping
  while the team keeps the self-hosted or custom-built stack alive.
- Comparing options at unequal quality; the symptom is a cheap option that needs a human review queue, or
  a fine-tuning project, that nobody costed.
- Ignoring change costs such as model deprecations and embedding-model upgrades; the symptom is an urgent
  re-embedding job or migration sprint arriving on the provider's schedule rather than yours.
- Presenting one number with false precision; when one assumption, usually adoption, turns out wrong,
  stakeholders lose trust in the whole analysis instead of updating the input that moved.
- Never revisiting the model after launch; without FinOps actuals to compare against, the next TCO repeats
  the same blind spots.

## When to use
Build a TCO model whenever a decision commits money or people for more than a few months: build versus
buy, hosted API versus self-hosting, choosing a vector database or serving platform, signing a committed-use
contract, or moving workloads on-prem. Skip the full model for cheap, reversible experiments; there, run the
pilot and measure. Measure loaded people cost, cost per successful task, migration frequency and adoption,
because those dominate. Revisit the model at renewal or when a provider changes price or retires a model.
In interviews, the signal is naming the non-invoice lines unprompted and saying which assumption flips the
recommendation.

## Role lens
- **Engineer:** Supplies the honest numbers: tokens per task, FTE fractions for upkeep, and how long the
  last model migration and re-evaluation actually took.
- **Architect:** Owns the comparison frame: equal outcome, horizon, cost buckets, scenarios, and designing
  for low exit cost so the TCO's lock-in line stays small.
- **FDE:** Builds the customer's TCO for adopting the product, including their integration, security review
  and change-management effort, which often decides the deal more than licence price.

## Check yourself
**Q:** A team proposes self-hosting an open-weight model because GPU cost per token is a third of the API
price. What would you ask before agreeing, in terms of total cost of ownership?
**A:** I would ask whether the comparison is at equal quality on our evals, and if not what closing the gap
costs. Then the operate costs beyond GPUs: fractional platform and on-call engineers at loaded cost,
serving-stack upgrades, observability and achieved utilization rather than benchmark throughput. Then
change costs: how often we will swap models and what each re-evaluation costs. Then exit and commitment
risk: reserved capacity we may not fill. I would want this over a two-to-three-year horizon with
scenarios for volume and API price cuts, and the assumption that flips the answer named explicitly.

## Quiz
### Q: Why should two options be compared at equal quality on your evals before comparing TCO?
- [ ] Because vendors are required to disclose quality differences in their pricing
- [x] Because a quality gap creates real costs, such as fine-tuning or human review, that otherwise go uncounted
- [ ] Because lower-quality models always use more tokens per request
- [ ] Because TCO is only defined for options with identical architectures
> The cheaper option often needs extra work to reach the same outcome. Comparing at unequal quality hides
> that work; equalize quality or compare cost per successful task instead.

### Q: In a build-on-API option, token spend is a small share of three-year TCO. What does that imply?
- [ ] Negotiating a lower token price is the most effective way to cut TCO
- [ ] The option is under-utilized and should be moved to self-hosting
- [x] People and change costs dominate, so reducing upkeep effort moves the total more than token price does
- [ ] Discounting should be removed because it distorts small cost lines
> Effort on a line should follow its share of the total. When upkeep FTE is the biggest line, automation
> and stable interfaces save more than a token discount; self-hosting would usually add people cost.

### Q: Discounting future costs at 10% narrows the gap between a front-loaded build and a growing subscription. Why?
- [ ] Discounting inflates upfront costs to reflect risk
- [x] The subscription's largest payments come in later years, which discounting weights less than early ones
- [ ] Discounting applies only to licence fees, not engineering costs
- [ ] Discounting removes the annual price escalator from the subscription
> Each future cost is divided by (1 + r)^t, so late cash counts for less. The subscription grows with
> adoption and escalators, so its back-loaded payments shrink most; the upfront build cost is untouched.
