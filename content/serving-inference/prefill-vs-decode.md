---
id: prefill-vs-decode
module: serving-inference
name: Prefill vs decode
kind: concept
difficulty: core
est_minutes: 14
related:
  - model-internals/kv-cache
  - serving-inference/ttft
  - serving-inference/tpot
  - serving-inference/continuous-batching
  - serving-inference/prefix-caching
  - serving-inference/inference-optimization
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
sources:
  - Pope et al., 2022 — Efficiently Scaling Transformer Inference
  - Agrawal et al., 2023 — SARATHI: Efficient LLM Inference by Piggybacking Decodes with Chunked Prefills
  - Zhong et al., 2024 — DistServe: Disaggregating Prefill and Decoding for Goodput-optimized LLM Serving
---

## TL;DR
Every LLM request runs in two phases with opposite hardware profiles. Prefill processes the whole prompt in
one pass: thousands of tokens per weight read, compute-bound, and it sets time-to-first-token. Decode then
produces one token per step: one token per weight read, memory-bandwidth-bound, and it sets time per
output token. Because the phases stress different resources, mixing them on one GPU causes interference,
and most scheduling techniques exist to manage that collision.

## Why it exists
The split is not a design choice; it falls out of autoregression. The prompt is known in full before
generation starts, so all of its tokens can be pushed through the model together as one big matrix
multiplication per layer. Generated tokens are not known in advance, so each depends on the previous one
and must be produced sequentially. Same weights, same layers, radically different shapes of work.

Why does that matter enough to deserve its own lesson? Because a GPU's efficiency depends almost entirely
on the shape of the work. A matmul over 2,000 prompt tokens reads each weight once and uses it 2,000
times; the arithmetic units are saturated. A decode step over one token reads the same weights and uses
each once; the GPU spends the step waiting on memory. The result is that prefill of a 2,000-token prompt
can take about as long as a few dozen decode steps, not 2,000 of them, and that a faster GPU helps prefill
a lot and decode barely at all.

The naive mental model, "cost is proportional to tokens", gets both pricing and scheduling wrong. It
predicts that a long prompt with a short answer and a short prompt with a long answer cost the same; in
practice the second one occupies the GPU far longer. It also predicts that batching helps uniformly, when
in fact batching is what rescues decode and does almost nothing for prefill. Every serving metric that
matters, TTFT, TPOT, throughput, is really a statement about one phase or the other, and you cannot tune
what you have not separated.

## How it works
### Prefill
Input: the prompt's *P* token embeddings as a `[P, d_model]` matrix. Each layer runs its projections and
MLP as dense matmuls over all *P* rows at once, computes causal attention among the *P* positions, and
writes K and V for every position into the KV cache. The last position's logits produce the first output
token. Cost scales with *P* for the matmuls and with *P²* for attention (mitigated by fused attention
kernels that avoid materializing the full matrix). Arithmetic intensity is roughly *P* FLOPs per weight
byte, so for any prompt longer than a few hundred tokens the phase is compute-bound: TTFT grows roughly
linearly with prompt length and shrinks with more FLOPs.

### Decode
Input: one new token, shape `[1, d_model]` per request. Each layer runs the same matmuls over a single row,
computes attention of that one query against every cached K and V, and appends one K and one V. Per step,
the GPU must read all weights plus the entire KV cache of the request, and the useful work is a handful of
FLOPs per byte. Step time is therefore approximately `(weight_bytes + cache_bytes) / bandwidth`, and
TPOT is almost independent of prompt length except through the cache-read term. Batching *B* requests
reads the weights once for *B* tokens, which is why continuous batching is the throughput lever for decode
and irrelevant for prefill.

### Why they interfere
Both phases run on the same GPU inside the same iteration. If the scheduler admits a 10k-token prompt
into a batch of 50 decoding requests, that iteration takes as long as the prefill, and all 50 users see a
pause in their token stream: a TPOT spike caused by someone else's TTFT. Conversely, prioritizing decode
makes new arrivals wait and TTFT climbs. The two phases trade against each other inside every scheduling
decision.

### Techniques that manage the collision
- **Chunked prefill.** Split long prompts into fixed-size chunks (a token budget per iteration) and run
  each chunk alongside the decode batch. The decode tokens ride along at near-zero marginal cost because
  the iteration was going to be compute-bound anyway, and no single iteration becomes a stall. The cost
  is slightly longer TTFT for the long prompt.
- **Prefill priority versus decode priority.** Engines expose this as scheduler policy; prefill-first
  minimizes TTFT, decode-first smooths TPOT. The right setting depends on the product.
- **Disaggregated serving.** Run prefill on one pool of GPUs and decode on another, transferring the KV
  cache between them over the interconnect. Each pool is then tuned for its phase (prefill wants FLOPs,
  decode wants bandwidth and memory capacity), and one phase's load can no longer spike the other's
  latency. The price is the transfer and a more complex system; it pays at scale.
- **Prefix caching.** Reuse an already-computed KV prefix so part of the prefill is skipped entirely; the
  biggest TTFT lever when prompts share a long system prompt.
- **Speculative decoding.** Turns several decode steps into one verification pass, exploiting the slack
  compute that bandwidth-bound decode leaves idle.

### What the metrics mean
- TTFT ≈ queue wait + prefill time (+ a cache transfer in disaggregated setups).
- TPOT ≈ decode step time, which rises with batch size and with the total cache the batch reads.
- Throughput in tokens/sec is dominated by decode batching; prefill throughput is a separate number and
  is what a long-document, short-answer workload actually consumes.

## Example
A worked scenario on one 80 GB GPU serving an 8B GQA model in fp16 (weights ~16 GB, cache ~128 KB per
token), with rounded, illustrative hardware figures of ~3 TB/s bandwidth and ~900 TFLOPs.

Request A: 4,000-token prompt, 50-token answer. Request B: 200-token prompt, 1,000-token answer.

Prefill A: 2 × 8e9 × 4,000 = 64 TFLOPs of matmul work; at ~900 TFLOPs that is roughly 70 ms, plus
attention and overhead, so TTFT on the order of 100 ms when the GPU is otherwise idle. Prefill B is 20× less
work: a few milliseconds, so TTFT is dominated by queueing.

Decode A: each step reads ~16 GB of weights plus ~0.5 GB of cache (4,050 tokens × 128 KB); at ~3 TB/s
that is ~5.5 ms per step, so the 50 tokens take ~280 ms. Decode B: ~16 GB + ~0.15 GB per step, ~5.4 ms,
times 1,000 tokens ≈ 5.4 s.

Total GPU time: A ≈ 0.1 + 0.28 ≈ 0.4 s; B ≈ 0.005 + 5.4 ≈ 5.4 s. B has one-twentieth the prompt tokens
but occupies the GPU 13× longer, and nearly all of that time the GPU is streaming the same 16 GB of
weights over and over. Batch 32 B-like requests together and the per-step cost rises only to ~7 ms
(weights read once, cache reads grow), so aggregate throughput goes up ~25× while each user's TPOT
rises from 5.4 to 7 ms. Now admit one A-like prompt into that batch without chunked prefill: that
iteration takes ~100 ms instead of 7 ms, and all 32 users see a visible hitch. With chunked prefill at a
512-token budget, A's prefill is spread over 8 iterations of ~15 ms each: A's TTFT becomes ~120 ms
instead of ~100 ms, and nobody else stalls.

## Trade-offs
- Chunked prefill smooths TPOT for everyone and raises utilization, but lengthens TTFT for long prompts;
  flips when the workload is mostly long prompts with short answers and TTFT is the metric sold.
- Disaggregating prefill and decode isolates the phases and lets each pool be sized for its bottleneck,
  but adds KV transfer and operational complexity; it pays only at a scale where interference is measurable.
- Prefill-first scheduling minimizes TTFT at the cost of decode stalls; decode-first does the opposite;
  the right choice depends on whether users wait for the first token or read the stream.
- Faster-FLOPs hardware shortens prefill substantially but barely moves decode; buying it makes sense for
  document-heavy, short-output workloads and not for chat with long answers.

## Pitfalls
- Benchmarking "latency" as a single number; symptom is a product with fine average latency whose users
  complain either about the pause before text appears or about stuttering mid-stream.
- Assuming cost is proportional to total tokens; symptom is a pricing or capacity model that is wildly
  wrong for long-output workloads, because output tokens cost far more GPU time than input tokens.
- Leaving chunked prefill off on a mixed workload; symptom is periodic TPOT spikes across all users that
  coincide with the arrival of long prompts, not with request rate.
- Expecting a GPU upgrade to speed up chat decode; symptom is TTFT improving while tokens/sec per user is
  unchanged, because bandwidth did not change much.
- Reporting prefill throughput and decode throughput as one "tokens/sec"; symptom is capacity estimates
  that are off by an order of magnitude when the input/output mix shifts.

## When to use
This split is the first thing to apply when a latency or cost number looks wrong. Ask which phase the
metric belongs to: TTFT problems lead to prefill (prompt length, queueing, prefix caching, chunking);
TPOT problems lead to decode (batch size, cache size, bandwidth, quantization). Use it when sizing
hardware: a summarization service of long documents wants FLOPs and prefill throughput, a chat service
with long answers wants bandwidth and memory. Use it when pricing or budgeting: count input and output
tokens separately because they cost different resources. In interviews, explaining why decode is
bandwidth-bound and prefill is compute-bound, with the arithmetic-intensity reasoning, is the baseline
expectation for any serving question.

## Role lens
- **Engineer:** Enables chunked prefill and sets the per-iteration token budget, reads TTFT and TPOT as
  separate histograms, and attributes spikes to the phase that caused them.
- **Architect:** Chooses GPU types and whether to disaggregate the phases based on the workload's
  input/output mix, and sets latency targets per phase rather than one blended number.
- **FDE:** Explains to a customer why their long-document pipeline and their chat assistant need different
  tuning, and reproduces their stutter by injecting a long prompt into a loaded replica.

## Check yourself
**Q:** A document-summarization service has 15k-token prompts and 300-token outputs. A chat assistant has
300-token prompts and 1,500-token outputs. Both run on identical GPUs. Which hardware upgrade would help
each, which scheduling settings differ, and how should the two be billed relative to each other?
**A:** Summarization is prefill-dominated: 15k tokens of compute-bound matmul per request, so TTFT is the
pain and more FLOPs per GPU would help directly; prefix caching helps only if documents share prefixes,
which they usually do not. Chat is decode-dominated: 1,500 bandwidth-bound steps per request, so more
bandwidth and more memory for larger batches help, and FLOPs do not. Scheduling: summarization benefits
from chunked prefill so that its long prompts do not stall other users, and from a higher per-iteration
token budget; chat wants a large `max_num_seqs`, decode-friendly scheduling and possibly speculative
decoding. Billing: the chat request occupies the GPU for seconds of decode versus the summarizer's tens
of milliseconds of prefill, so output tokens must be priced several times higher than input tokens, which
is exactly what public API price lists do.

## Quiz
### Q: Why is the decode phase memory-bandwidth-bound while prefill is compute-bound?
- [ ] Decode uses a different, slower set of kernels than prefill
- [x] Decode reads all weights and the cache to produce one token per request, while prefill reuses each weight across every prompt token
- [ ] Prefill does not need to write to the KV cache
- [ ] Decode runs on the CPU in most engines
> Arithmetic intensity is FLOPs per byte moved. Prefill performs thousands of operations per weight byte;
> decode performs one or a few, so its time is set by how fast memory can be streamed. Same kernels, same
> device, different shape of work.

### Q: What is the primary purpose of chunked prefill?
- [ ] To reduce the total compute needed for long prompts
- [ ] To allow prompts longer than the context window
- [x] To interleave pieces of a long prompt with decode steps so one prefill does not stall every running request
- [ ] To compress the KV cache written during prefill
> Chunking does not reduce work; it spreads it across iterations so decode batches keep flowing. The
> prefill's own TTFT gets slightly longer, in exchange for smooth TPOT for everyone else.

### Q: A team upgrades to a GPU with twice the FLOPs and similar memory bandwidth. Which metric improves most?
- [ ] Time per output token for a single chat request
- [x] Time to first token on long prompts
- [ ] Maximum number of concurrent requests
- [ ] KV-cache memory per token
> Prefill is compute-bound, so doubling FLOPs roughly halves its time and TTFT drops. Decode is
> bandwidth-bound and gains little; concurrency is set by memory capacity; cache size per token is a model
> property.
