---
id: throughput-vs-latency
module: serving-inference
name: Throughput vs latency
kind: decision
difficulty: core
est_minutes: 15
related:
  - serving-inference/continuous-batching
  - serving-inference/batching-and-concurrency-limits
  - serving-inference/tokens-sec
  - serving-inference/tpot
  - serving-inference/ttft
  - infra-capacity-economics/queueing-theory
  - infra-capacity-economics/quality-latency-cost-optimization
  - reliability/slas-slos
sources:
  - Yu et al., 2022 — Orca: A Distributed Serving System for Transformer-Based Generative Models
  - Agrawal et al., 2024 — Taming Throughput-Latency Tradeoff in LLM Inference with Sarathi-Serve
  - Little, 1961 — A Proof for the Queuing Formula L = λW
---

## TL;DR
A decode step costs about the same whether it advances one sequence or thirty-two, because its time
is dominated by reading the weights once. Packing more requests into each step therefore multiplies
throughput almost for free, until compute or KV memory saturates, but every added request lengthens
each step and the queue in front of it, so per-request latency rises. No setting maximises both. You
pick an operating point on that curve per workload and enforce it with concurrency limits, admission
control and SLOs.

## Why it exists
A GPU serving an LLM at concurrency 1 is mostly idle: it streams 16 GB of weights through its memory
system to produce a single token, then does it again. Letting it produce 32 tokens per pass instead
costs almost nothing extra, which is why continuous batching exists and why self-hosting is
affordable at all. But the same arithmetic means the server is now making 32 users share one
pipeline. Each step is a little slower, new arrivals wait for a slot, and under bursty traffic the
wait, not the step, becomes the latency.

Teams that never choose get the worst of both. Run at high utilisation to keep cost per token low and
the p99 latency explodes, because queueing delay grows without bound as utilisation approaches 100 %.
Run at low concurrency to keep latency tight and you pay for GPUs that are 80 % idle. The naive
"autoscale on GPU utilisation" does not resolve it either: a GPU can report high utilisation while
being bandwidth-bound with spare compute, and the metric lags the queue that users feel.

Providers make the trade-off explicit in their pricing. Interactive endpoints are tuned for latency
and cost more; batch endpoints run at the throughput end of the curve with multi-hour completion
windows at roughly half the interactive price at the time of writing. If you run your own engine,
you are the one who has to make that choice, per workload, with numbers.

## How it works
### The shape of the curve
Plot concurrency on the x-axis. Throughput (aggregate output tokens/s) rises almost linearly, bends,
and plateaus. Per-request latency (TPOT, and TTFT once queueing starts) stays nearly flat, then turns
upward at a knee, then climbs steeply. The mechanism for the decode phase is a single line:

```
step_time ≈ (weight_bytes + B × kv_bytes_per_seq) / bandwidth        (bandwidth-bound regime)
throughput ≈ B / step_time          per-request TPOT ≈ step_time
```

While `weight_bytes` dominates, *B* can grow with little effect on step time, so throughput scales
with *B*. As `B × kv_bytes` catches up (long contexts do this quickly) each new sequence costs every
other one real latency. Eventually arithmetic overtakes bandwidth and the step becomes compute-bound,
or KV memory fills and the engine can admit no one; from there additional load only lengthens the
queue.

### Queueing sits on top
Even below the knee, arrivals are random. Little's law says the average number of requests in the
system equals arrival rate times average time in system (`L = λW`), and for a simple single-server
queue the time in system grows like `service_time / (1 − ρ)`, where ρ is utilisation. At ρ = 0.5 a
request spends twice its service time in the system; at ρ = 0.9, ten times. LLM services have
long, highly variable service times (output length is unknown in advance), which makes the tail
worse than the simple model predicts. That is why interactive services target moderate utilisation
and why TTFT p99 is the first thing to break under load.

### Choosing the operating point
1. Write the SLO as percentiles: for example TTFT p95 ≤ 1 s and TPOT p95 ≤ 50 ms for chat.
2. Run a load curve at realistic prompt and output lengths and find the largest concurrency at
   which both hold. That is the replica's *goodput* capacity.
3. Set the engine's per-replica concurrency limit (`max_num_seqs` in vLLM) and token budget there,
   with headroom for bursts.
4. Shed or queue beyond it visibly: a fast 429 with `Retry-After`, or a queue position, instead of a
   silent 10-second TTFT.
5. Autoscale on queue depth or TTFT, not on GPU utilisation.
6. Separate pools: interactive traffic at the knee, batch and background jobs at the plateau where
   each token is cheapest and nobody is waiting.

### Techniques move the curve; they do not remove it
Continuous batching lets sequences join and leave every step instead of waiting for the slowest.
PagedAttention stops KV fragmentation so more sequences fit before memory binds. Chunked prefill
bounds the prefill work per step so new arrivals do not stall running streams. Disaggregated
prefill and decode put the two phases on different GPUs so one workload's TTFT stops competing with
another's TPOT. Quantisation shrinks bytes per step and KV footprint. Speculative decoding lowers
latency at low concurrency but spends compute that the plateau needs, so it can reduce peak
throughput. Each of these shifts the knee rightward or the floor downward; none lets you run at
100 % utilisation with flat latency.

### If you do not run the engine
With a hosted API you do not set the batch size, but the trade-off is still yours: you choose between
the interactive and batch tiers, how many requests you keep in flight per key, which timeouts you
accept, and whether to pay for provisioned or dedicated capacity when tail latency matters. The
provider's rate limits are, in effect, the operating point they chose for you.

## Example
A worked scenario on one replica, using the illustrative bandwidth model from the TPOT lesson: an
8B fp16 model (16 GB of weights, 128 KB of KV cache per token) on an 80 GB GPU with about 3.35 TB/s
of memory bandwidth, every request at 4k tokens of context, so each sequence holds about 0.5 GB of
KV cache.

Memory capacity caps concurrency first: after weights and a few GB of working space, roughly 60 GB
remain, so at most about 120 sequences can be resident. Bytes per step at *B* sequences are
`16 GB + B × 0.5 GB`, so TPOT ≈ `(16 + 0.5B) / 3350` seconds and throughput ≈ `B / TPOT`.

Now apply an SLO. If chat needs TPOT ≤ 50 ms, the bandwidth model allows `16 + 0.5B ≤ 167 GB`,
i.e. *B* up to about 300. The memory cap of ~120 binds first, so latency is not the limit: at
*B* = 120 the step is `(16 + 60) / 3350 ≈ 23 ms`, comfortably inside the SLO, and throughput is
about 120 / 0.023 ≈ 5,300 tokens/s. Run the replica full.

Tighten the SLO to TPOT ≤ 15 ms, as a code-completion product might. Now `16 + 0.5B ≤ 50 GB`, so
*B* ≤ 68 and latency binds before memory does; throughput falls to about 68 / 0.015 ≈ 4,500
tokens/s. Tighten again to 10 ms and *B* ≤ 35, throughput ≈ 3,500 tokens/s. The same hardware
delivers about a third fewer tokens per hour, hence roughly 50 % higher cost per token, purely
because of the latency target. That is the trade-off turned into a budget line, before any
queueing delay is added on top; real measurements will sit below every figure here.

## Trade-offs
- Higher concurrency lowers cost per token and raises per-request latency; the balance flips from
  "fill the GPU" to "cap the batch" the moment a per-request SLO exists for that traffic.
- Running at low utilisation buys flat tail latency and wastes hardware; it is the right call for
  interactive tiers and the wrong one for offline jobs that can wait hours.
- Speculative decoding improves latency when the GPU has spare compute (low concurrency) and can cut
  peak throughput when it does not, so it belongs on the interactive pool, not the batch pool.
- Sharing one pool between interactive and batch traffic maximises utilisation but lets batch
  arrivals push interactive TTFT past the knee; separate pools cost headroom and buy predictability.
- Shedding load with fast 429s protects the latency of admitted requests at the cost of failed ones;
  queueing admits everyone and lets latency drift, which is better only when callers can wait.

## Pitfalls
- Autoscaling on GPU utilisation; the symptom is replicas that scale out after users have already
  waited through a queue, because utilisation lags and saturates early in bandwidth-bound decode.
- Benchmarking throughput with a closed loop and no SLO; the symptom is a "capacity" figure that
  production never reaches without blowing p99.
- Sizing from mean latency; the symptom is on-call pages for p99 while the dashboard average looks
  fine, since queueing lives entirely in the tail.
- Letting one batch job share the interactive pool; the symptom is TTFT spikes at the exact minute a
  cron job starts.
- Raising `max_num_seqs` to fix throughput without checking KV memory; the symptom is preemptions and
  recompute stalls that lower throughput and latency at once.
- Treating speculative decoding as free throughput; the symptom is lower aggregate tokens/s at peak
  than without it.

## When to use
This is the decision you make every time you configure a replica, choose a provider tier, or set a
rate limit. Reach for the latency end whenever a person or a latency-sensitive agent is waiting:
chat, completions, voice, interactive tools. Reach for the throughput end for evaluation runs, bulk
extraction, embeddings backfills, nightly summaries and anything with a deadline measured in hours.
Measure the curve (throughput, TTFT p95, TPOT p95 against concurrency) for each model and hardware
pairing, pick the point, and enforce it with limits and alerts on queue depth. In interviews the
strong answer draws the curve, explains why it bends (weights shared per step, then KV and compute
binding), adds the queueing tail, and names the concurrency limit as the control.

## Role lens
- **Engineer:** Produces the load curve, sets per-replica concurrency and token budgets, and wires
  429 shedding and queue-depth metrics into the serving layer.
- **Architect:** Owns the operating point per traffic class, decides on separate pools or tiers,
  and translates the SLO into replica counts and cost per token.
- **FDE:** Negotiates a customer's latency target against their GPU budget with the curve in hand,
  often by moving their batch workloads off the interactive pool.

## Check yourself
**Q:** One replica reports 85 % GPU utilisation and p99 TTFT of 6 s. Finance will not approve more
GPUs. What do you do, and in what order?
**A:** First establish which constraint binds: check KV-memory occupancy, preemption counts and
engine queue depth; 85 % utilisation in a bandwidth-bound decode loop says little about headroom.
Then protect latency with a per-replica concurrency cap set from a load curve, shedding the excess
with fast 429s so admitted requests stay inside the SLO instead of everyone waiting 6 s. Next
recover capacity without hardware: enable prefix caching for the shared system prompt, turn on
chunked prefill so long prompts stop stalling admission, cap `max_model_len` to what the product
needs so more sequences fit, and evaluate weight or KV quantisation. Move any batch or background
traffic to off-peak hours or a separate cheap pool. Finally measure goodput at the SLO and present
finance with cost per token at the required latency, which is the real request.

## Quiz
### Q: Why does raising the batch size increase throughput almost linearly at first?
- [ ] Each sequence gets its own copy of the weights in GPU memory
- [x] The weights are read from memory once per decode step and shared by all sequences in the batch
- [ ] The GPU clock increases under heavier load
- [ ] Continuous batching compresses the KV cache as the batch grows
> A decode step is bound by streaming the weights from memory, a cost paid once per step regardless of
> batch size. More sequences per step mean more tokens for nearly the same time until KV reads or
> compute start to dominate.

### Q: Which statement about queueing delay in a loaded LLM service is correct?
- [ ] Average wait grows linearly with utilisation, so 90 % load means 90 % more delay
- [x] Average time in the system grows roughly like 1 / (1 − utilisation), so tail latency explodes as load nears saturation
- [ ] Queueing only matters once GPU memory is completely full
- [ ] Mean latency captures queueing well because waits are evenly distributed
> The simple single-server result `W ≈ service_time / (1 − ρ)` already gives ten times the service
> time at ρ = 0.9, and LLM service times are long and variable, which makes the tail worse. The
> damage shows up in p95 and p99 long before memory is exhausted.

### Q: Which technique improves per-request latency at low load but can reduce peak throughput at high load?
- [ ] PagedAttention
- [ ] Prefix caching
- [x] Speculative decoding
- [ ] Chunked prefill
> Speculative decoding spends extra compute drafting and verifying several tokens per step; that
> compute is spare at low concurrency but is exactly what a saturated GPU needs, so peak throughput
> can fall. PagedAttention, prefix caching and chunked prefill shift the curve without that penalty.
