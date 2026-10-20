---
id: trade-off-analysis
module: system-design
name: Trade-off analysis
kind: practice
difficulty: core
est_minutes: 15
related:
  - system-design/nfrs
  - system-design/adrs
  - system-design/build-vs-buy
  - system-design/self-host-vs-api
  - system-design/slm-vs-llm-trade-offs
  - infra-capacity-economics/quality-latency-cost-optimization
  - infra-capacity-economics/break-even-analysis
  - serving-inference/throughput-vs-latency
sources:
  - Richards, Ford, 2020 — Fundamentals of Software Architecture
  - Ford, Richards, Sadalage, Dehghani, 2021 — Software Architecture: The Hard Parts
  - Kazman, Klein, Clements, 2000 — ATAM: Method for Architecture Evaluation
---

## TL;DR
Trade-off analysis is the disciplined way to choose between architectural options when none dominates:
frame the decision and its hard constraints, enumerate real options, score them against the NFRs that
matter using evidence rather than opinion, find the sensitivity points where the ranking flips, weigh
reversibility, then decide and record. It exists because every architectural choice gives something up
and "best practice" hides what. In AI systems the usual battleground is quality against latency against
cost, with vendor risk as the fourth corner.

## Why it exists
Richards and Ford's first law of software architecture is that everything is a trade-off, and their
corollary is that if you think you have found a choice without one, you have not yet found the
trade-off. The practice exists because teams keep making architectural decisions in ways that hide the
cost until it is paid.

The common failure modes are recognisable. The **pros-and-cons list** has no weights, no evidence and no
conditions, so it can justify anything. The **weighted scoring matrix** looks rigorous but is usually
built backwards: the favourite is chosen first and the weights are adjusted until it wins, with
single-digit precision pretending to be measurement. **Best-practice** and **vendor-benchmark**
decisions import someone else's context, which is the one thing a trade-off is sensitive to. And
**analysis paralysis** is the opposite failure, where the absence of a method makes every decision a
debate that ends with the loudest voice.

AI systems make this worse in a specific way. The options change monthly, published benchmarks do not
transfer to your data, and the honest answer to most questions, hosted or self-hosted, large model or
small, long context or retrieval, fine-tune or prompt, is "it depends on volume, quality threshold and
how often you will need to switch". A good analysis does not pretend otherwise; it tells you exactly what
it depends on, at what value the answer changes, and how expensive it is to be wrong. That is a
different output from "we picked X", and it is the one that survives contact with next quarter.

## How it works
### 1. Frame the decision
State the decision in one sentence, its scope, and who decides by when. Then separate **hard
constraints** from **dimensions**. Hard constraints (EU residency, a licence that forbids commercial
use, a budget ceiling, a launch date) are filters: an option that violates one is removed before
scoring. Treating a constraint as a weighted dimension is how a disqualified option "wins on points".
The constraints come from the NFRs, which is why trade-off analysis without written NFRs degrades into
opinion.

### 2. Enumerate real options
At least three, including "do nothing or defer" and usually a hybrid. A decision with one option is a
ratification, and a decision between two is often a false dichotomy; the hybrid (hosted model for
generation, self-hosted for embeddings; small model with escalation to a large one) is frequently the
answer and is only found if it is on the list.

### 3. Choose the dimensions from the NFR priorities
Four to seven dimensions, drawn from the architecturally significant requirements rather than a generic
template: quality on your golden set, p95 latency, cost per successful task, operational burden,
vendor or lock-in risk, time to deliver, reversibility. Fewer dimensions with real evidence beat a
twelve-column matrix of guesses. The priority order among them must be the one the business already
agreed in the NFRs, so that the tie-breaker is not invented during the analysis.

### 4. Fill each cell with evidence, and label its grade
Every cell is one of: **measured** (your workload, your eval set), **prototyped** (a spike on
representative data), **estimated** (back-of-envelope from known prices and throughput), or
**asserted** (someone's belief). Write the grade next to the number. The two cells with the highest
weight and the lowest grade are where to spend a prototyping day, because they decide the outcome and
are the most likely to be wrong. Carry ranges, not points: "cost per 1k conversations $30–55 depending on
cache hit rate" is more useful than "$42".

### 5. Find the sensitivity points and the flip conditions
This is the step that distinguishes analysis from scoring. For each important input, ask at what value
the ranking changes: monthly volume, GPU utilisation, provider price, the quality threshold, team size.
ATAM calls these sensitivity points, and a parameter that moves two quality attributes in opposite
directions is a trade-off point. If a flip condition sits close to your expected operating point, the
decision is fragile, and the right output is not a winner but a design that makes switching cheap (an
abstraction such as a gateway) plus a trigger to re-run the analysis.

### 6. Weigh reversibility and the cost of being wrong
A two-way door (swap the model behind a gateway, change a chunking strategy) can be decided quickly on
thin evidence because the cost of error is small. A one-way door (train on customer data, commit to an
embedding model that fixes a billion-vector index, sign a multi-year commitment) deserves prototypes,
ranges and a pre-mortem. Under uncertainty the reversible option carries an option value that a static
matrix does not show, so it should get explicit credit.

### 7. Decide, record, schedule the revisit
Write the outcome as an ADR: the constraints, the options, the matrix with evidence grades, the flip
conditions and the revisit triggers. The analysis is reusable only if the inputs were recorded; "we
chose hosted" is unreproducible, "we chose hosted because break-even was 18B tokens a month at 50%
utilisation and we expect 2B" can be re-run in five minutes next year.

### Techniques that fit inside the steps
Break-even arithmetic for cost cells; scenario walkthroughs (run the top NFR scenarios through each
option on a whiteboard) for availability and operability; a utility tree to agree which dimensions
matter; a pre-mortem ("it is a year later and this choice failed, why?") to surface risks the matrix
missed; and the halving test ("would we still choose this if volume halved or doubled?") as a cheap
sensitivity check.

## Example
The cost dimension of a hosted-versus-self-hosted decision, done as a break-even with an explicit flip
condition. Every figure is illustrative and must be replaced with your own quotes and a load-tested
throughput number; the script is the shape of the reasoning, not a benchmark. Note what it shows: at
these inputs self-hosting only wins above roughly 18 billion tokens a month, and a drop in utilisation
from 50% to 40% makes it never win, because idle GPUs still bill. That utilisation sensitivity is the
finding, and it belongs in the ADR alongside the quality and risk dimensions this cell says nothing about.

```python
# Illustrative figures only. Replace with your provider quote and your measured throughput.
HOSTED_PER_M  = 2.00         # $ per 1M tokens, blended input/output
GPU_HOUR      = 2.50         # $ per GPU-hour, on-demand
TOK_PER_GPU_H = 3_000_000    # tokens one GPU serves per hour on YOUR workload (load-tested)
OPS_PER_MONTH = 6_000        # $ people + tooling to run serving, paid regardless of volume

def monthly(tokens: float, util: float) -> tuple[float, float]:
    hosted = tokens / 1e6 * HOSTED_PER_M
    self_hosted = tokens / (TOK_PER_GPU_H * util) * GPU_HOUR + OPS_PER_MONTH
    return hosted, self_hosted

def break_even_tokens(util: float) -> float:
    # hosted == self_hosted  =>  tokens * (price_per_token - gpu_cost_per_token) == fixed ops cost
    gap = HOSTED_PER_M / 1e6 - GPU_HOUR / (TOK_PER_GPU_H * util)
    return OPS_PER_MONTH / gap if gap > 0 else float("inf")

for util in (0.5, 0.4):
    be = break_even_tokens(util)
    verdict = f"{be / 1e9:.1f}B tokens/month" if be != float("inf") else "never (GPU cost/token exceeds hosted)"
    print(f"utilisation {util:.0%}: self-hosting pays off above {verdict}")
    for t in (5e8, 5e9, 5e10):
        h, s = monthly(t, util)
        print(f"   {t / 1e9:>5.1f}B tokens   hosted ${h:>9,.0f}   self-hosted ${s:>9,.0f}")

# utilisation 50%: self-hosting pays off above 18.0B tokens/month
#      0.5B tokens   hosted $    1,000   self-hosted $    6,833
#      5.0B tokens   hosted $   10,000   self-hosted $   14,333
#     50.0B tokens   hosted $  100,000   self-hosted $   89,333
# utilisation 40%: self-hosting pays off above never (GPU cost/token exceeds hosted)
```

## Trade-offs
- A full matrix with evidence grades and sensitivity analysis produces decisions that survive review,
  but it costs days; for two-way doors a one-paragraph analysis with a named flip condition is the
  right amount of rigour.
- Prototyping the uncertain cells replaces guesses with measurements, but a spike on unrepresentative
  data is worse than an honest estimate; the prototype must use your golden set and your traffic shape.
- Explicit weights make priorities arguable in the open, but they invite tuning toward a favourite; the
  defence is to fix the priority order in the NFRs before any option is scored.
- Optimising for reversibility keeps options open, but every abstraction that enables switching costs
  something and can hide capabilities of the thing behind it; it pays off when the flip condition is
  near, not when one option clearly dominates.
- Sensitivity analysis tells you what the decision depends on, but it needs real inputs (volume
  forecasts, utilisation, prices); with none available, say so and set a short revisit date instead of
  inventing them.

## Pitfalls
- Weights adjusted after the first scoring pass; the symptom is a matrix whose history shows the
  favourite losing, then the weights changing, then the favourite winning.
- Dimensions copied from a generic template rather than from the NFRs; the symptom is a column such as
  "community size" outweighing the quality threshold the product will be judged on.
- Point estimates with false precision; the symptom is a decision resting on a 7.4 versus 7.1 total
  that no one can reproduce.
- Using the provider's or a paper's benchmark as the quality cell; the symptom is a model that wins on
  the leaderboard and loses on your golden set after the contract is signed.
- Forgetting the fixed operational cost of running things yourself; the symptom is a self-hosting plan
  whose per-token cost looked lower and whose monthly bill is higher.
- Treating a hard constraint as a weighted dimension; the symptom is a vendor that "scored best" and
  cannot satisfy the customer's residency requirement.
- Never recording the inputs; the symptom is the same debate, from scratch, every time a new model or a
  new price list appears.

## When to use
Run the full method for one-way doors and for any choice whose flip condition is plausibly near your
operating point: hosted versus self-hosted, embedding model and vector store, tenancy model, fine-tune
versus retrieval, build versus buy on a core capability. Run the short form, constraints, three options,
one flip condition, a reversibility note, for everything else, and resist the urge to run anything for
decisions that are cheap to undo. What to measure: your own quality numbers on your own golden set, your
own throughput under your own load, and your own volume forecast with a range. In interviews, the
strongest answers to "would you use X or Y" name the dimensions, state the assumption that decides it,
give the value at which the answer flips, and say how they would keep the choice reversible until the
data arrives.

## Role lens
- **Engineer:** Produces the measured cells, latency under load, eval scores, token counts, cache hit
  rates, and builds the spike that turns the riskiest estimate into a measurement.
- **Architect:** Owns the framing, the constraint-versus-dimension split, the priority order inherited
  from the NFRs, the sensitivity analysis and the written ADR with revisit triggers.
- **FDE:** Runs the analysis with the customer's real volumes, prices and constraints, and shows them the
  flip condition so the recommendation is understood as conditional rather than as vendor preference.

## Check yourself
**Q:** A customer asks whether they should self-host an open-weight model or keep using a hosted API,
and wants a yes or no. How do you structure the answer so it is both decisive and honest?
**A:** I would first apply the hard constraints: residency, licence, security review, and whether they
have anyone to operate GPUs. If self-hosting survives those, I would score the live options, hosted,
self-hosted and a hybrid, on the dimensions their NFRs prioritise: quality on their golden set, p95
latency, cost per successful task, operational burden and reversibility, labelling each cell as measured,
prototyped or estimated. The decisive part is the flip condition: with their quotes and a load-tested
throughput, self-hosting breaks even above some monthly token volume at a given utilisation, and I would
compare that to their forecast range. If the forecast is well below it, the answer is hosted, behind a
gateway so the switch stays cheap, with a revisit trigger at a stated volume or when an open-weight model
reaches eval parity. If the forecast straddles the flip point, the recommendation is the hybrid and a
measured pilot rather than a bet.

## Quiz
### Q: Why should hard constraints be applied as filters before scoring, rather than as heavily weighted dimensions?
- [ ] Because scoring matrices cannot represent more than five dimensions
- [x] Because an option that violates a constraint can still win on total points, producing a choice that cannot actually be deployed
- [ ] Because constraints always come from legal rather than engineering
- [ ] Because filtering is faster to compute
> A residency or licence violation is disqualifying regardless of how well the option scores elsewhere;
> giving it a weight lets strong scores on other columns outvote a requirement that has no
> compensation.

### Q: What is the most valuable output of a sensitivity analysis in an architecture decision?
- [ ] A more precise total score for the winning option
- [ ] A list of all the stakeholders consulted
- [x] The input values at which the ranking of options changes, compared with the expected operating point
- [ ] A ranking of dimensions by how hard they are to measure
> Knowing where the answer flips tells you whether the decision is robust or fragile and what to monitor
> as a revisit trigger. A more precise score does not reveal how much the result depends on uncertain
> inputs.

### Q: In the break-even example, why can lowering GPU utilisation from 50% to 40% make self-hosting never pay off?
- [ ] Because the hosted provider lowers its price at lower volumes
- [ ] Because lower utilisation increases the fixed operational cost
- [x] Because idle GPU time still bills, so the effective cost per served token rises above the hosted per-token price
- [ ] Because utilisation below 50% triggers autoscaling penalties
> Self-hosted cost per token is GPU cost per hour divided by tokens actually served per hour; serving
> fewer tokens on the same billed hardware raises it. Once it exceeds the hosted price per token, no
> volume recovers the fixed costs.
