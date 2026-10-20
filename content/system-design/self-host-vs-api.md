---
id: self-host-vs-api
module: system-design
name: Self-host vs API
kind: decision
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - system-design/build-vs-buy
  - system-design/open-weight-vs-proprietary
  - system-design/model-selection
  - infra-capacity-economics/break-even-analysis
  - infra-capacity-economics/tco
  - serving-inference/engines
  - security-governance-compliance/data-residency
  - security-governance-compliance/vendor-exit-strategy
sources:
  - Official docs: vLLM
---

## TL;DR
A hosted model API buys capability, elasticity and zero operations at a per-token price; self-hosting
open-weight models buys control, data locality and a lower *marginal* cost per token, but only once the
GPUs are busy enough to amortise their fixed cost and the people who run them. It is a utilisation and
constraints decision, not a price-list comparison: start on the API unless a hard constraint (residency,
air gap, customisation, sustained volume) forces hosting, and re-run the numbers as traffic grows.

## Why it exists
Every AI system has to answer where inference runs, and the two options have opposite cost structures.
An API is pure variable cost: you pay per token, it scales to zero, and someone else does capacity
planning. A self-hosted cluster is mostly fixed cost: a GPU bills by the hour whether it is serving 2,000
tokens per second or idling at 3 a.m., and it comes with an engineer on call.

The naive comparisons both fail. "The API charges X per million tokens and a GPU costs Y per hour, so
the GPU is cheaper" ignores utilisation: a GPU that averages 20% of its peak throughput delivers tokens
at five times its nominal price. "Our data is sensitive, so we must self-host" ignores that enterprise
API terms with zero retention and in-region endpoints often satisfy legal, while some regulated
environments genuinely cannot send data to any third party, which makes the API option unavailable at
any price. "Open models are good enough now" is a per-task claim that only your eval set can settle.

Hidden costs sit on the self-host side: upgrades of engines and drivers, security patching, capacity for
peaks, cold starts measured in minutes while a model loads, and re-running evals after every change.
Hidden costs sit on the API side too: deprecation schedules you do not control, rate limits during
incidents, and coupling to provider-specific features. The decision exists because neither default is
right, and because the right answer changes over a product's life.

## How it works
### The cost model
API cost is the sum over calls of tokens × price, with separate prices for input, output and (often)
cached input tokens. Self-host cost is fixed spend divided by useful work:

```
effective_$ per 1M tokens = gpu_hourly_cost × 1e6 / (tokens_per_second × 3600 × utilisation)
```

Three terms matter. `tokens_per_second` is the aggregate throughput of one GPU at *your* batch size and
prompt/output mix, not a vendor headline; decode is memory-bandwidth bound, so it rises with batching
until the KV cache fills memory. `utilisation` is average load divided by provisioned capacity; because
interactive traffic has daily peaks several times its average, you provision for the peak and run the
rest of the day partly idle, unless batch work fills the valleys. The third term is people: a fraction of
an engineer plus on-call, which is a fixed cost that does not shrink with traffic.

### The constraints that decide before cost does
- **Data.** Residency, retention, customer contracts, or an air gap. Check what the API vendor's
  enterprise terms and in-cloud platforms (a model served inside your cloud account's boundary) actually
  provide before assuming hosting is required.
- **Control.** Pinning a model version for a year, deterministic behaviour across time, custom
  fine-tunes or LoRA adapters, logit access, engine-level constrained decoding. APIs offer some of these;
  hosting offers all of them.
- **Latency.** Hosting inside your VPC removes a WAN hop and lets you tune time-to-first-token; but a
  small, poorly utilised cluster has worse tail latency than a provider's large fleet.
- **Capability.** Frontier proprietary models are usually ahead on open-ended tasks; the gap on a narrow
  task (classification, extraction) is often zero once a small model is fine-tuned. Only your eval knows.
- **Elasticity.** APIs absorb spikes up to your quota; hosting absorbs spikes up to your headroom, and
  adding a replica means minutes of model loading.

### The middle options
Managed open-weight hosting (a vendor serves open models per token), provisioned-throughput plans from
API vendors (fixed cost, no ops), cloud model platforms inside your account boundary, serverless GPUs for
spiky loads, and the hybrid most teams end up with: the API for low-volume frontier tasks and self-hosted
small models for one or two high-volume narrow tasks that dominate spend.

### How the decision evolves
Prototype on the API because it is the fastest way to learn whether the product works. Instrument
tokens per month per task, prompt/output ratio, peak-to-average ratio and quality on a golden set. When a
single narrow task dominates spend and an open model clears the eval bar, move that task and keep the
long tail on the API. Revisit quarterly: API prices and open-model quality both move, and the break-even
point moves with them.

## Example
A break-even calculator with illustrative inputs. Every figure is an assumption to replace with your
own measurements; the point is the shape of the result, not the numbers.

```python
# All numbers illustrative. Replace with your traces and a throughput benchmark on your prompt mix.
monthly_in, monthly_out = 40e9, 8e9           # tokens/month: 40B prompt, 8B completion
api_in_price, api_out_price = 0.15, 0.60      # $ per 1M tokens, small-model tier (illustrative)
api_cost = monthly_in / 1e6 * api_in_price + monthly_out / 1e6 * api_out_price

gpu_hourly = 2.50                             # $ per GPU-hour on demand (illustrative)
agg_out_tps = 1500                            # decode tokens/s per GPU at your batch size (measure it)
peak_to_avg = 3.0                             # interactive traffic: provision for the daily peak
eng_cost = 12000                              # $/month: part of an engineer plus on-call

avg_tps = monthly_out / (30 * 24 * 3600)      # ~3,086 output tokens/s averaged over the month
gpus = -(-avg_tps * peak_to_avg // agg_out_tps)          # ceil: GPUs needed at peak
gpu_cost = gpus * gpu_hourly * 730
utilisation = avg_tps / (gpus * agg_out_tps)
print(f"API ${api_cost:,.0f}/mo vs self-host ${gpu_cost + eng_cost:,.0f}/mo "
      f"({int(gpus)} GPUs at {utilisation:.0%} utilisation)")
```

With these inputs the API wins: about $10,800 per month against $24,775 for 7 GPUs running at 29%
utilisation. Change two parameters, five times the traffic, reserved GPUs at $1.50 per hour and batch
jobs that flatten the peak ratio to 1.5, and the result flips: $54,000 on the API against about $29,520
for 16 GPUs at 64% utilisation. The model ignores prefill compute, which matters when prompts are five
times longer than outputs, so benchmark aggregate throughput on your real traffic before trusting it.

## Trade-offs
- Lower marginal cost per token when self-hosted, but only above a utilisation threshold; low or spiky
  traffic flips the advantage back to the API, and every provider price cut moves the break-even point.
- Full control over model version and behaviour, in exchange for owning every upgrade; providers
  deprecate on their schedule, but you must re-evaluate each engine and model change yourself.
- Data stays inside your boundary when hosted, yet zero-retention terms and in-cloud model platforms
  often meet the same requirement without operations; it flips only when no third-party processor is
  permitted at all.
- Frontier APIs usually lead on open-ended quality; the gap narrows to nothing on narrow tasks where a
  fine-tuned small model beats a general model on your eval, which is when hosting that task pays.
- APIs absorb traffic spikes up to the quota you negotiated; self-hosting needs idle headroom or a
  queue, and it flips when provider rate limits themselves become the bottleneck during peaks.

## Pitfalls
- Comparing list prices without utilisation; the symptom is projected savings that never appear in the
  bill while dashboards show GPUs idling at 20%.
- Treating "sensitive data" as automatically meaning self-hosting; the symptom is months of platform
  work for a requirement a contract addendum met, or the reverse, learning at procurement review that
  data could never leave and the API prototype must be rebuilt.
- Migrating on cost alone without a golden-set eval; the symptom is spend falling while quality
  complaints and escalations rise within weeks.
- Omitting people and on-call from the model; the symptom is a single engineer who is the cluster's real
  single point of failure and cannot take holiday.
- Hard-coding provider-specific features (tool-call formats, cache controls, response schemas) deep in
  application code; the symptom is "we can't switch" during a price negotiation.
- Sizing for average load instead of peak; the symptom is queueing and time-to-first-token collapsing
  at the daily peak, exactly when the most users are present.

## When to use
Default to the API for new products and for any task whose volume is small or whose quality bar needs a
frontier model. Reach for self-hosting when a hard constraint applies (air gap, strict residency with no
acceptable in-cloud option, logit or adapter access, version pinning the vendor will not give you) or
when one narrow task has high sustained volume, an open model passes your eval, and your utilisation
model shows a clear margin after people cost. Measure tokens per task per month, prompt/output ratio,
peak-to-average ratio, p95 latency and golden-set quality before deciding. In interviews, the expected
answer is the utilisation formula plus the constraint checklist, ending in a hybrid portfolio with a
revisit cadence, not a one-word preference.

## Role lens
- **Engineer:** Builds behind a provider-agnostic interface from day one, instruments tokens and latency
  per task, and feeds real trace numbers into the break-even model rather than vendor estimates.
- **Architect:** Owns the decision record: constraints first, cost second, a hybrid split of tasks across
  API and hosted models, and a scheduled review as prices and model quality move.
- **FDE:** Maps a customer's residency, procurement and network rules to the deployment options, and
  often lands on an in-cloud managed model as the compromise between the API and a GPU cluster.

## Check yourself
**Q:** A customer insists on self-hosting a 70B-parameter model for a support assistant handling about
2,000 chats a day, because "APIs are too expensive and our data is sensitive". How do you evaluate the
request?
**A:** Quantify first. 2,000 chats at roughly 4,000 tokens each is about 8 million tokens a day, around
240 million a month, which is a modest API bill at any current price tier. A 70B model in 16-bit
precision has about 140 GB of weights, so it needs a multi-GPU node running around the clock; 240 million
tokens a month averages under 100 tokens per second against a node that can serve many times that, so
utilisation would be in the single-digit percent range and the per-token cost many times the API's. Then
test the data claim: what exactly may not leave, and would zero-retention enterprise terms or an
in-cloud model platform in their region satisfy legal? If a genuine air-gap requirement survives that
conversation, propose a smaller model on one GPU after an eval shows it meets the quality bar, budget the
on-call, and record the decision and its assumptions in an ADR so it can be revisited when volume grows.

## Quiz
### Q: Which parameter most often decides whether self-hosting beats the API on cost?
- [ ] The GPU's list price per hour
- [x] Average utilisation: served load divided by the capacity you had to provision for peak
- [ ] The number of parameters in the model
- [ ] The length of the system prompt
> Self-host cost is fixed spend divided by useful tokens, so idle capacity multiplies the effective
> price per token. GPU price and model size matter, but a cheap GPU at 15% utilisation still loses to the
> API, while an expensive one at 70% can win.

### Q: A prototype runs on a frontier API and one high-volume classification task is 70% of spend. What is the best next step?
- [ ] Move all traffic to a self-hosted frontier-class model to eliminate API spend
- [ ] Negotiate a discount and keep the architecture unchanged
- [x] Evaluate an open small model on that task's golden set and, if it passes, self-host only that task
- [ ] Switch the classification task to a larger API model to raise accuracy
> The hybrid split captures most of the saving with the least risk: one narrow task, measurable quality,
> predictable volume. Moving everything multiplies operational burden and puts the open-ended tasks on a
> weaker model.

### Q: Which requirement cannot be met by a hosted API regardless of contract terms?
- [ ] Zero retention of prompts and outputs by the provider
- [ ] Processing inside a specific cloud region
- [x] Inference inside an air-gapped network with no outbound connectivity
- [ ] Sub-second time to first token for short prompts
> Retention and regional processing are routinely covered by enterprise agreements and in-cloud
> platforms, and APIs can deliver fast first tokens. An air gap removes the network path the API needs,
> so hosting is the only option.
