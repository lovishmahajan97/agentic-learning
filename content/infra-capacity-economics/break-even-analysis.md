---
id: break-even-analysis
module: infra-capacity-economics
name: Break-even analysis (hosted API vs self-hosting)
kind: decision
difficulty: core
est_minutes: 16
status: reviewed
related:
  - system-design/self-host-vs-api
  - system-design/open-weight-vs-proprietary
  - infra-capacity-economics/tco
  - infra-capacity-economics/gpu-sizing
  - infra-capacity-economics/capacity-planning
  - infra-capacity-economics/cost-modeling
  - serving-inference/throughput-vs-latency
  - infra-capacity-economics/api-provider-quotas-and-regional-limits
---

## TL;DR
A hosted API is pure variable cost: you pay per token and nothing when idle. Self-hosting is mostly fixed:
GPUs and the people who run them are paid for whether or not tokens flow. Break-even is the monthly volume
where the two curves cross. It depends far more on achievable utilization and team cost than on GPU list
price, which is why most teams cross it much later than per-token arithmetic suggests, and why an API price
cut can push the crossover to infinity.

## Why it exists
The question arrives with the first five-figure API invoice: "would it not be cheaper to run this
ourselves?" The back-of-envelope answer takes a GPU's hourly price, divides by its benchmark tokens per
second times 3,600, gets a cost per million tokens far below the API's list price, and concludes that
self-hosting is several times cheaper. That calculation is wrong in three independent ways.

First, it assumes the GPU is saturated 24 hours a day. Real interactive traffic is diurnal and bursty; you
provision for the peak with headroom to protect latency, so the average utilization of what you bought is a
fraction, and the effective cost per token is the list cost divided by that fraction. Second, the GPU is the
visible line, not the biggest one: someone has to build and run the serving stack, carry the pager, upgrade
the model and prove on your evals that an open-weight model matches the hosted one. Third, it compares
different things: a hosted frontier model against an open-weight model, at different quality.

The opposite reflex, "never self-host, it is a distraction", is equally lazy. At sustained high volume the
fixed cost amortizes until the marginal token is nearly free, and non-cost drivers such as data residency,
rate limits during bursts, latency control or a model the vendor does not offer can decide the question on
their own. The break-even analysis exists to replace both reflexes with a curve and a sensitivity band.

## How it works
### Two cost curves
Write monthly cost as a function of monthly volume *V* in tokens.

```
Cost_api(V)  = p_in · V_in + p_out · V_out          ≈ p · V   for a stable input/output mix
Cost_self(V) = F + N(V) · G
```

`p` is the blended price per token at *your* mix (output tokens usually cost several times input tokens,
so blend per feature, not per company). `F` is the fixed monthly cost that does not scale with volume:
people, platform tooling, eval upkeep. `G` is the monthly cost of one GPU replica. `N(V)` is the number of
replicas needed to serve *V*, and it is a step function because you cannot buy a third of a GPU.

### Replica count comes from the peak, not the average
```
avg_rate  = V / seconds_per_month
peak_rate = avg_rate · peak_to_average · (1 + headroom)
N(V)      = ceil(peak_rate / T_slo)
```

`T_slo` is the sustained tokens per second one replica delivers *at your latency target*, measured on your
sequence lengths, not the throughput at maximum batch. The distinction matters because continuous batching
trades per-request latency for aggregate throughput: the batch size that maximizes tokens per second gives a
p95 nobody would ship. Measure it for your request *shape* (typical input and output lengths), not as a
generic tokens-per-second figure, *because* a prompt token is processed in parallel during prefill and
costs far less GPU time than an output token generated one at a time in decode. An API's price ratio
between input and output tokens is not your GPU's cost ratio, so blend both sides on the same mix. The
peak-to-average ratio comes from your traffic logs; headroom covers failover and
the fact that a replica at 100% load is already past its latency target.

The achieved utilization is `u = avg_rate / (N · T_slo)`, roughly `1 / (peak_to_average · (1 + headroom))`
once N is large. Interactive traffic with a peak-to-average of 2–3 and 20% headroom lands around a third.
Batch or offline workloads can be scheduled flat and push *u* toward one, which is why self-hosting pays off
far earlier for them. Utilization is the single most important number in the whole analysis.

### Effective cost per token and the sign test
```
c_self = G / (T_slo · seconds_per_month · u)
```

Compare `c_self` to `p` before anything else. If `c_self > p` at the utilization you can realistically
achieve, there is no break-even at any volume: fixed costs only widen the gap. If `c_self < p`, the
continuous approximation of the crossover is `V* ≈ F / (p − c_self)`, and the step function makes the real
crossover slightly later. The sign test is what an API price cut attacks: halve `p` and a workload that was
comfortably past break-even can be on the wrong side of it permanently.

### Payback on the switch
Moving is itself a one-off cost `M`: building the serving path, evaluating and possibly fine-tuning the
open-weight model, load testing, migrating traffic. Even past break-even, the switch pays back only after
`M / (Cost_api − Cost_self)` months. Put that next to the reservation term and the expected life of the
model choice: a 14-month payback on a decision you expect to revisit within a year is a loss.

### What goes into G and F
`G` is the accelerator plus its share of the node (CPU, memory, local disk, network), ingress and load
balancing, and the difference between on-demand and one- or three-year reserved pricing, which is large.
On-prem adds amortized hardware, power, cooling, space and replacement of failed parts. `F` is the fraction
of platform engineers and SREs, the on-call rotation, serving-stack upgrades, security and compliance work,
observability tooling, and the recurring eval and fine-tuning work needed to keep quality parity each time
a new open-weight model or a new hosted model appears. `F` is where most "it would be cheaper" spreadsheets
go wrong, because it is the line nobody wants to write down.

### Quality parity is a cost, not an assumption
If the candidate open-weight model scores below the hosted one on your evals, you either pay to close the
gap (fine-tuning, which adds a one-off cost and a recurring maintenance line to `F`) or you accept lower
quality and price that through cost per successful task. Either way, compare the two options at equal
quality or convert the quality gap into money; comparing at unequal quality is comparing nothing.

### Sensitivity, not a point
The honest output is a band. Vary utilization between the batch-flat and interactive-bursty cases, vary
`p` across list and committed-use pricing and a plausible future price cut, and vary `G` between
on-demand and reserved. Time matters too: a reservation locks `G` for its term, while hosted prices have
tended to fall and hosted models to improve, so a decision that is marginal today is a bet on the next
twelve months of price movement.

### Non-cost factors that can decide it alone
Regulated data that cannot leave a region or a building; rate limits and quotas that cap bursts during
your peak; latency control through co-location; a specific open-weight or custom fine-tuned model the
vendor does not serve; deprecation and lock-in risk. Any one of these can settle the question regardless
of cost, but run the arithmetic anyway so the premium being paid for the constraint is known and owned.

### Separate the model choice from the operating model
"Hosted API versus self-hosting" silently bundles two decisions: which model (proprietary or open-weight)
and who operates it. They can be unbundled. Several providers serve open-weight models per token, and some
vendors sell dedicated or provisioned capacity for a fixed fee. A per-token open-weight endpoint gives you
the open model's price and portability with no `F` for serving; provisioned capacity gives you fixed cost
and guaranteed throughput without running the stack. Price these as separate rows, *because* the
self-hosting case often rests on the model being cheaper, not on operating it yourself, and a managed
endpoint captures that saving without the fixed costs.

### The usual answer is hybrid
Self-host the predictable base load, where utilization is high and fixed cost amortizes well, and send
bursts and rare high-complexity requests to the API, which prices variable demand well. Or split by task:
high-volume simple tasks on a self-hosted small model, frontier-quality tasks on the API. The hybrid wins
because it puts each cost structure on the traffic shape it is built for, at the price of running two
serving paths and two quality baselines.

## Example
A runnable version of the model above, with every number illustrative. The output shows the curves crossing
and, more importantly, how the crossover moves when one assumption changes.

```python
import math
SEC_MONTH = 30 * 24 * 3600

def api_cost(tokens_m, price_per_m):
    return tokens_m * price_per_m

def self_host_cost(tokens_m, gpu_month, tok_per_s_at_slo, peak_to_avg, headroom, fixed):
    avg_rate = tokens_m * 1e6 / SEC_MONTH                 # tokens/s averaged over the month
    peak_rate = avg_rate * peak_to_avg * (1 + headroom)   # what you must provision for
    replicas = max(1, math.ceil(peak_rate / tok_per_s_at_slo))  # cannot buy 0.3 of a GPU
    return fixed + replicas * gpu_month, replicas

# Illustrative: blended API price $2.00/M tokens; $3,000 per GPU replica-month (reserved);
# 2,000 tok/s per replica at the latency target; $16,000/month of people and tooling.
for label, price, p2a in (("interactive", 2.00, 2.5), ("flat batch", 2.00, 1.2), ("API cut", 1.00, 2.5)):
    print(label)
    for v in (5_000, 20_000, 50_000, 100_000):            # million tokens per month
        api = api_cost(v, price)
        sh, n = self_host_cost(v, 3_000, 2_000, p2a, 0.2, 16_000)
        print(f"  {v:>7}M  api=${api:>8,.0f}  self=${sh:>8,.0f} ({n:>2} GPUs)  {'self' if sh < api else 'api'} wins")
# interactive:  5,000M api=$10,000 self=$25,000 (3)  20,000M api=$40,000 self=$52,000 (12)
#               50,000M api=$100,000 self=$103,000 (29)  100,000M api=$200,000 self=$190,000 (58)
# flat batch:   20,000M api=$40,000 self=$34,000 (6) -> self already wins; 50,000M self=$58,000
# API cut:      100,000M api=$100,000 self=$190,000 (58) -> api wins at every volume shown
```

Read the three blocks together, starting with the sign test. One replica serves
2,000 × 2,592,000 ≈ 5.2 billion tokens a month at full load, so `c_self` = $3,000 / 5,184M ≈ $0.58 per
million at u = 1. Interactive traffic gives u ≈ 1 / (2.5 × 1.2) = 1/3, so `c_self` ≈ $1.74, below the $2
price but only just: `V* ≈ 16,000 / (2.00 − 1.74)` ≈ 62 billion tokens a month. That matches the table:
self-hosting does not win until somewhere past 50 billion tokens a month, an API bill around $100,000. Make the load flat (u ≈ 0.69, `c_self` ≈ $0.83) and the same GPUs serve
roughly twice the tokens, so the crossover drops to about 14 billion. Halve the API price and the sign test
fails: $1.74 is above $1.00, and no volume rescues it. The GPU price
never changed across the three runs; utilization and the competitor's price did all the work.

## Trade-offs
- Self-hosting converts variable spend into fixed commitments; it wins when load is high and flat and loses
  when load is small or spiky, and the flip is governed by achievable utilization, not by GPU price.
- Reserved pricing cuts `G` substantially but locks a term; if traffic falls or a better hosted model
  appears you keep paying for idle capacity, so it is only worth it with confidence in a year or more of demand.
- The hosted API delivers frontier quality with zero platform work, but prices, quotas, regions and
  deprecations are the vendor's decisions; the trade flips when a quota or residency rule blocks you outright.
- Hybrid serving captures the best cost structure for each traffic shape but doubles the surface area: two
  serving paths, two eval baselines and a routing layer that must keep them consistent.
- Matching hosted quality with an open-weight model often needs fine-tuning; that cost is front-loaded and
  recurs with each base-model generation, which favours the API when model turnover is fast.

## Pitfalls
- Using benchmark peak throughput for capacity; the symptom is a plan for 20 GPUs that becomes 60 once
  p95 latency targets are enforced in load tests.
- Omitting people and platform cost; the symptom is a GPU bill below the API bill and a total cost above it,
  discovered when the headcount request lands.
- Comparing against API list price when committed-use discounts exist; the symptom is a break-even computed
  against a price nobody at your volume actually pays.
- Treating the crossover as permanent; the symptom is a self-hosting decision that looked right at one price
  per million tokens and looks wrong after the next provider price cut.
- Ignoring the input/output mix; the symptom is decode-heavy traffic achieving far less than the measured
  throughput, because decode is bandwidth-bound while the benchmark was prefill-heavy.
- Framing it as "frontier API versus our own GPUs" and skipping managed open-weight endpoints; the symptom
  is a self-hosting project whose whole saving came from the cheaper model, which a per-token endpoint
  would have delivered without the platform team.
- Arguing cost when the real driver is residency or quotas; the symptom is weeks of spreadsheet debate
  while the compliance blocker that actually decides the question goes unaddressed.

## When to use
Run the analysis when monthly API spend becomes material, a useful threshold being when it approaches the
fully loaded cost of one engineer, and again whenever a non-cost driver appears: a residency requirement, a
quota ceiling during peaks, a vendor deprecation notice. Collect the inputs before opening a spreadsheet:
tokens per month by feature with the input/output split, the peak-to-average ratio from logs, the latency
target, throughput per replica at that target measured on a candidate open-weight model with your sequence
lengths, the quality delta on your evals, reserved GPU quotes, and a realistic people plan. The output is a
break-even band under sensitivity and a recommendation that names the premium being paid for whichever
side is chosen. In interviews, the structural answer is expected: fixed versus variable cost, the
utilization-driven effective cost per token, the sign test, and the hybrid shape.

## Role lens
- **Engineer:** Measures throughput at the latency target on real sequence lengths, extracts the
  peak-to-average ratio from traffic logs, and keeps the calculator honest with production numbers.
- **Architect:** Owns the decision and its band: sets the utilization assumption, the reservation term, the
  quality-parity requirement and the hybrid split, and records the non-cost drivers that override the arithmetic.
- **FDE:** Rebuilds the break-even with the customer's volumes, their cloud pricing and their compliance
  constraints, and explains why their "GPUs are cheaper" slide omitted the people and utilization lines.

## Check yourself
**Q:** Your API bill is $40,000 a month at a blended $2 per million tokens, so 20 billion tokens a month.
An engineer says one GPU does 2,000 tokens per second, so four GPUs at $3,000 a month, $12,000 in total,
would replace it. What is wrong with that, and what is the right calculation?
**A:** Twenty billion tokens a month is roughly 7,700 tokens per second on average, and four GPUs at
2,000 tokens per second cover that only if they are saturated every second of the month with no headroom.
With a peak-to-average ratio of 2.5 and 20% headroom you must provision about 23,000 tokens per second,
which is twelve replicas, $36,000, before adding the platform engineer, on-call and eval upkeep that make
it about $52,000, above the $40,000 API bill. The 2,000 figure must also be re-measured at the latency
target on your sequence lengths rather than at maximum batch, and the open-weight model must match the
hosted one on your evals or the gap must be priced. Then run the sensitivity: a flat batch workload drops
the replica count to around six and self-hosting wins, while a committed-use API discount to $1.50 per
million makes the API $30,000 and widens its lead. The honest recommendation is to measure peak-to-average
and throughput at SLO first, and to consider a hybrid that self-hosts the flat base load and sends bursts
to the API.

## Quiz
### Q: For a chosen model and serving stack, which parameter most changes the self-hosted cost per token?
- [ ] The GPU's hourly list price
- [x] The utilization actually achieved after provisioning for peak with headroom
- [ ] The maximum-batch throughput in the vendor's published benchmark
- [ ] The network egress price of the cloud region
> Effective cost per token is the replica cost divided by the tokens it actually serves. Provisioning for
> a bursty peak can leave two-thirds or more of the capacity idle, which typically moves the cost per token
> more than price differences between comparable GPU offers; a vendor benchmark changes no real cost, and
> egress is small for text tokens.

### Q: At realistic utilization the self-hosted effective cost per token comes out above the API's price per token. What does that imply?
- [ ] Break-even will be reached at a high enough volume
- [x] There is no break-even at any volume, because the fixed costs only widen the gap
- [ ] Break-even is reached at a low volume
- [ ] More GPUs should be added to lower the cost per token
> If the variable part alone is more expensive than the API, adding fixed costs for people and platform
> makes self-hosting worse at every volume. Only a change in utilization, GPU price or API price can
> restore a crossover.

### Q: Why is a hybrid, self-hosting the base load and sending bursts to the API, so often the cheapest shape?
- [ ] Hosted APIs are always cheaper per token than any self-hosted setup
- [x] Fixed capacity amortizes best on flat, predictable load, while bursts are exactly what per-token pricing handles well
- [ ] It removes the need to evaluate the open-weight model
- [ ] Self-hosted GPUs cannot technically serve bursty traffic
> Each cost structure is matched to the traffic shape it suits: the reserved GPUs run near full
> utilization on the steady base, and the API absorbs the spiky remainder without idle capacity being
> paid for. The price is two serving paths to keep consistent.
