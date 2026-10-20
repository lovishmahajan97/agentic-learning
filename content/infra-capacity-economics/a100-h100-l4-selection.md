---
id: a100-h100-l4-selection
module: infra-capacity-economics
name: A100 / H100 / L4 selection
kind: decision
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - infra-capacity-economics/gpu-sizing
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
  - infra-capacity-economics/cost-modeling
  - infra-capacity-economics/tco
  - serving-inference/tensor-parallelism-at-inference
  - serving-inference/quantization-quality-evaluation
  - training-adaptation/quantization
sources:
  - Official docs: NVIDIA A100, H100 and L4 datasheets
  - Pope et al., 2022 — Efficiently Scaling Transformer Inference
---

## TL;DR
Choose a card by the constraint that binds first: does the model fit (memory capacity), is per-stream
decode fast enough (memory bandwidth), and what does a token cost at the utilisation you will
actually run (price ÷ throughput ceiling). The L4 is a cheap, 24 GB, low-bandwidth card for small
models and light traffic; the A100 is the mature 40/80 GB workhorse without FP8; the H100 is the
80 GB, ~3.35 TB/s, FP8-capable card that wins per token at high load and for training.

## Why it exists
Three cards from the same vendor can differ by an order of magnitude in price and in decode speed,
and the cheapest option per hour is frequently the most expensive per token, or simply cannot run the
model at all. Teams that pick by hourly price end up with L4s that cannot hold a 70B model or that
deliver 15 tokens per second to a chat user; teams that pick by prestige put an 8B internal tool on
an H100 that idles 95% of the day. Neither error shows up until the bill or the latency complaints
arrive.

The naive heuristic, "buy the newest card", fails because inference is usually bound by memory
capacity and bandwidth, not by the compute that generation-over-generation marketing emphasises, and
because the cost of a token depends on how full the card is, which depends on your traffic, not on
the card. The other naive heuristic, "buy the cheapest card that fits", fails because fitting is not
serving: a card that holds the weights with no room for KV cache serves one user, and a card with
300 GB/s of bandwidth cannot reach interactive decode speeds on anything beyond a few billion
parameters. Selection is therefore a short, repeatable procedure over four numbers — memory,
bandwidth, price and expected load — followed by a feature checklist. This lesson gives the
procedure and the datasheet facts it runs on.

## How it works
### The cards, by the numbers that matter
Rounded figures from NVIDIA's datasheets; variants differ (A100 40 GB has ~1.55 TB/s, H100 PCIe
~2 TB/s and lower TFLOPs, H100 NVL 94 GB, H200 141 GB at ~4.8 TB/s), so confirm the exact SKU.

| | L4 | A100 80 GB SXM | H100 80 GB SXM |
|---|---|---|---|
| Architecture | Ada Lovelace | Ampere | Hopper |
| Memory | 24 GB GDDR6 | 80 GB HBM2e | 80 GB HBM3 |
| Bandwidth | ~300 GB/s | ~2.0 TB/s | ~3.35 TB/s |
| Dense bf16 tensor | ~120 TFLOP/s | ~312 TFLOP/s | ~990 TFLOP/s |
| FP8 | yes | no | yes (Transformer Engine) |
| MIG partitions | no | up to 7 | up to 7 |
| Interconnect | PCIe only, no NVLink | NVLink 600 GB/s (SXM) | NVLink 900 GB/s (SXM) |
| Power / form | 72 W, single-slot PCIe | 400 W SXM (PCIe variant 300 W) | up to 700 W SXM (PCIe 350 W) |

The bandwidth column is the decode column: the H100 streams weights ~11× faster than the L4 and
~1.7× faster than the A100, and single-stream tokens/s follow those ratios almost directly. The
memory column is the fit column. The FP8 row matters *because* fp8 weights halve the bytes per decode
step and double the KV room with far less quality loss than int4. The A100 has no fp8 tensor cores:
it falls back to int8 or int4 schemes, or to engine-specific fp8 *weight-only* kernels that dequantise
to bf16 on the fly, which save memory and bandwidth but not compute. MIG matters for packing many small models onto one
card with isolation; the L4 cannot do it, but at its price you simply buy more L4s.

### The procedure
1. **Fit.** Weights at your precision plus KV headroom, from the sizing lesson. Roughly: an L4 holds
   an 8B model in bf16 with a few concurrent 4k contexts, or a ~13B model in int4; an 80 GB card holds
   8B with hundreds of contexts, 30B-class models in bf16, or 70B in int4; 70B in fp8 or bf16 needs
   two or four 80 GB cards with NVLink. Anything that does not fit ends the discussion.
2. **Per-stream latency.** Decode step time is at least `bytes per step ÷ bandwidth`. For 8B bf16:
   ~53 ms on L4 (≤19 tokens/s), ~8 ms on A100 (≤125), ~5 ms on H100 (≤200). If the SLO needs
   30 tokens/s per user, the L4 is out regardless of price.
3. **Cost per token at your load.** Estimate the throughput ceiling at the batch your memory allows,
   multiply by expected utilisation, divide price by it. The expensive card wins when traffic keeps
   it busy; the cheap card wins when traffic is sparse. The example below runs this.
4. **Features.** FP8 (H100, L4) for cheap precision reduction; MIG (A100, H100) for isolated sharing;
   NVLink (SXM) for tensor parallelism; L4's low power and single-slot size for edge boxes and ordinary
   servers; L4's video encode/decode engines for vision pipelines.
5. **Availability and procurement.** Quotas, regions, reservation terms and spot pricing differ by
   card; A100 supply is typically easier than H100, and L4s are widely available in small counts.
   Cloud families to look for: AWS p4d/p4de (A100 40/80 GB), p5 (H100), g6 (L4); GCP a2 (A100),
   a3 (H100), g2 (L4); Azure ND A100 v4 and ND H100 v5. Check the current catalogue before planning.

### Training versus serving
Training and full fine-tuning are compute- and interconnect-bound: H100's bf16 and fp8 throughput and
NVLink make it the default, A100 remains cost-effective for smaller runs, and the L4 is only sensible
for LoRA on small models because 24 GB and PCIe-only links cap everything else. For serving, step 2
and 3 dominate, which is why the "A100 is obsolete" claim is wrong for inference: its bandwidth is
60% of an H100's at a typically lower price, and for models under 30B it is often the best value.

### Neighbours you will see
A10G (24 GB, ~600 GB/s) and L40S (48 GB, ~860 GB/s) sit between the L4 and the A100 in price and
bandwidth; H200 is an H100
with more and faster memory for long contexts; newer generations exist and follow the same procedure
with their own datasheet numbers.

## Example
An 8B bf16 chat model, two traffic levels, illustrative on-demand prices of roughly $0.80 per hour
for an L4 and $4.00 per hour for an H100 (ballpark only; prices vary by provider and over time).

*Ceilings.* On the L4, KV room allows about eight 4k-token contexts, so batch 8: a step reads 16 GB
of weights plus ~4.3 GB of cache, ≈ 68 ms at 300 GB/s, so ≈ 120 tokens/s aggregate. On the H100,
KV room allows about a hundred contexts: a step reads 16 GB plus ~54 GB of cache, ≈ 21 ms at
3.35 TB/s, so ≈ 4,800 tokens/s aggregate. Both are upper bounds.

*Cost per million tokens at full load.* L4: 120 × 3,600 ≈ 430k tokens per hour, so $0.80 ÷ 0.43M ≈
$1.90 per million. H100: 4,800 × 3,600 ≈ 17M tokens per hour, $4.00 ÷ 17M ≈ $0.23 per million. At full
load the H100 is roughly eight times cheaper per token despite costing five times more per hour.

*Scenario A, internal tool:* 0.3 requests/s averaging 300 output tokens is ~90 tokens/s, which keeps
the L4 at about 75% of its ceiling and the H100 at under 2%. Hourly cost is what you pay: $0.80 versus
$4.00; the L4 wins five to one, and per-user decode (≤19 tokens/s) is acceptable for an internal
tool.

*Scenario B, public product:* 10 requests/s averaging 300 tokens is 3,000 tokens/s. That needs 25 L4s
($20/h) or one H100 at ~60% of ceiling ($4/h), and the H100 users also get several times faster
per-stream decode (the batch-1 bandwidth ratio is ~11×; at these batch sizes the gap is smaller).

The crossover is wherever the H100's higher hourly price is covered by the tokens it actually serves:
here, somewhere around 500 tokens/s of sustained demand. Below it buy L4s; above it buy H100s; and
if the demand is bursty, read the serverless lesson before buying either.

## Trade-offs
- H100 delivers the lowest cost per token and fastest decode, but only when load keeps it busy; at
  low or bursty utilisation its hourly price makes the L4 or an API provider cheaper.
- A100 is often the best value for sub-30B serving and small training runs, but its lack of FP8
  forces int8/int4 for memory relief, with the quality evaluation that implies.
- L4 is cheap, low-power and easy to get, yet its 300 GB/s bandwidth caps per-stream decode and its
  24 GB caps model size, so it flips from "obvious choice" to "cannot do it" at roughly 8B–13B.
- SXM nodes with NVLink make tensor parallelism efficient for 70B-class models, at the cost of buying
  eight-card blocks; PCIe cards are flexible but make multi-card sharding slow.

## Pitfalls
- Choosing by hourly price alone; symptom is a fleet of L4s that cannot meet a per-user tokens/s SLO
  or cannot load the model the product team actually wants.
- Choosing by TFLOPs for an inference workload; symptom is paying H100 prices for decode throughput
  that an A100 would have delivered at 60%.
- Forgetting the A100 has no FP8; symptom is a quantisation plan validated on H100 that must be redone
  with int8/int4 kernels and re-evaluated for quality.
- Sizing to the 80 GB figure without KV headroom; symptom is a 70B bf16 model that loads on two
  cards and serves one request at a time.
- Ignoring quota and region availability; symptom is an architecture built around H100s that cannot
  be launched where the data must stay.

## When to use
Run the five-step procedure whenever a new model, SLO or traffic forecast arrives, and re-run it when
prices or quotas change. Reach for the L4 for embeddings, rerankers, vision models, small LLMs and
anything with sparse traffic; for the A100 when a 7B–30B model needs real concurrency at the best
price, or when H100 quota is unavailable; for the H100 for 70B-class serving, fp8, training, or any
service whose sustained load would keep it busy. Measure tokens/s per card against the bandwidth
ceiling and the actual utilisation against the plan. In interviews, expect "which card for X and
why"; the strong answer names the binding constraint (fit, bandwidth or cost per token) before naming
the card.

## Role lens
- **Engineer:** Benchmarks the candidate card at the real batch size and context, confirms fp8 or
  int4 kernels exist for the engine, and reports tokens/s per dollar rather than utilisation.
- **Architect:** Owns the card-tier matrix (which workloads go on L4, A100, H100), the procurement
  mix, and the policy for when a workload must move tiers.
- **FDE:** Maps a customer's available hardware and quotas to what each model can realistically do
  there, and defends the recommendation with fit and bandwidth arithmetic.

## Check yourself
**Q:** A customer runs a 13B model in bf16 for 200 daily-active users and asks whether to buy L4s,
A100s or H100s. Reason it through.
**A:** Fit first: 13B in bf16 is ~26 GB of weights, so the L4 is out unless the model is quantised to
int4 (~7 GB), which needs a quality evaluation. Both 80 GB cards fit it with tens of gigabytes of KV
headroom. Latency next: decode on the A100 is bounded near 26 GB ÷ 2 TB/s ≈ 13 ms per step, around
75 tokens/s per stream, comfortably interactive; the H100 would be ~1.7× faster. Load: 200 daily
users is likely a few requests per second at peak, well inside one A100's ceiling, so an H100's extra
throughput would sit idle. Recommendation: one A100 80 GB (two for redundancy), or an int4 L4 pair if
the quality evaluation passes and budget dominates; revisit if traffic grows past the A100's ceiling
or fp8 becomes necessary.

## Quiz
### Q: Which card property most directly determines single-stream decode tokens per second for a given model and precision?
- [ ] Peak bf16 TFLOP/s
- [x] Memory bandwidth
- [ ] Number of streaming multiprocessors
- [ ] PCIe generation
> Each decode step must stream all weights (and the sequence's KV cache) from memory, so the step
> time floor is bytes ÷ bandwidth. TFLOPs matter for prefill and training, and PCIe only affects
> host transfers and multi-card traffic.

### Q: A team validated fp8 serving of a 70B model on H100s and now must deploy on A100s. What changes?
- [ ] Nothing; fp8 checkpoints run on any card with 80 GB
- [ ] The A100 runs fp8 tensor-core matmuls at about half the H100's rate
- [x] The A100 has no fp8 tensor cores, so they need a different quantisation path and must re-evaluate quality and speed
- [ ] They must use the A100 40 GB variant, which supports fp8
> FP8 tensor cores arrived with Hopper and Ada (H100, L4); Ampere lacks them. Memory relief must come
> from int8/int4 schemes, or from fp8 weight-only kernels that dequantise to bf16 where the engine
> offers them; either way the kernels and numerics differ, which invalidates the earlier evaluation.

### Q: An internal tool serves an 8B model at 0.3 requests per second. Why is an L4 usually the better choice than an H100 here?
- [ ] The L4 has higher memory bandwidth for small models
- [ ] The H100 cannot run models smaller than 30B efficiently
- [x] At that load the H100 would sit almost idle, so its higher hourly price buys throughput nobody uses, while the L4's decode speed is adequate
- [ ] The L4 supports MIG and the H100 does not
> Cost per token depends on utilisation. The H100 is far cheaper per token at full load, but at a few
> percent utilisation the hourly price dominates and the L4's lower bandwidth still meets an
> internal tool's latency needs. The L4 does not support MIG.
