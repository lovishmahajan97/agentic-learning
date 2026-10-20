---
id: inference-optimization
module: serving-inference
name: Inference optimization
kind: concept
difficulty: core
est_minutes: 16
related:
  - serving-inference/prefill-vs-decode
  - serving-inference/continuous-batching
  - serving-inference/prefix-caching
  - serving-inference/quantization-quality-evaluation
  - serving-inference/tensor-parallelism-at-inference
  - model-internals/speculative-decoding
  - model-internals/flashattention
  - training-adaptation/quantization
sources:
  - Pope et al., 2022 — Efficiently Scaling Transformer Inference
  - Dao et al., 2022 — FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness
  - Leviathan et al., 2023 — Fast Inference from Transformers via Speculative Decoding
---

## TL;DR
Inference optimization is the set of levers that make a fixed model cheaper or faster to run. Almost all
of them work by attacking one of two bottlenecks: decode is bound by memory bandwidth (every token
re-reads the weights and the KV cache), prefill is bound by compute. Knowing which bound you are hitting
tells you which lever can possibly help, which is why the roofline model, not a list of tricks, is the
thing to learn.

## Why it exists
A GPU has a fixed ratio of arithmetic throughput to memory bandwidth. An H100 can do on the order of a
thousand teraFLOPs of dense fp16 math but move only a few terabytes per second from HBM, so it needs
roughly a few hundred floating-point operations per byte loaded to keep its arithmetic units busy. Each
transformer matmul applied to a batch of *B* tokens performs about 2·B FLOPs per weight element while
reading that element once, so its arithmetic intensity is about *B* FLOPs per byte. A single-request
decode step has *B* = 1. The GPU spends essentially all of its time streaming weights and cache through
memory and almost none computing. That is a utilization in the low single digits of percent, and it is
the default state of a naive LLM server.

The naive response, "buy a bigger GPU", fails because a GPU with more FLOPs and similar bandwidth makes
decode no faster. The second naive response, "run more requests at once", is actually correct, but it
runs into memory: each concurrent request pins a KV cache, and the pool fills long before the batch is
large enough to be compute-bound. Every optimization technique is a way around one of those two walls:
move fewer bytes per token, do less work per request, or fit more requests into the same bytes. The
discipline is matching the lever to the wall you are actually hitting, which is why measurement precedes
tuning.

## How it works
### Start from the roofline
For a decode step at batch size *B* with weights of *W* bytes and a total KV cache of *C* bytes across the
batch:

```
time_per_step ≈ max( (W + C) / bandwidth ,  2·B·params / FLOPs )
```

The left term is the bandwidth floor and dominates until *B* reaches the GPU's ridge point (a few hundred
tokens per step on current hardware). Prefill processes the whole prompt as one batch of *B* = prompt
length tokens, so it sits on the right-hand, compute-bound side. Each lever below shrinks one term.

### Levers that move fewer bytes (help decode)
- **Weight quantization** (int8, fp8, 4-bit formats such as AWQ or GPTQ): halves or quarters *W*, so the
  batch-1 bandwidth floor drops proportionally. It also frees VRAM for more cache. Quality must be
  evaluated per model and task, because the loss is not uniform.
- **KV-cache quantization** (fp8 or int8 cache): shrinks *C*, which matters once the batch is large enough
  that cache reads rival weight reads. Long-context, high-concurrency workloads benefit most.
- **Architecture choices**: grouped-query attention and multi-head latent attention cut cache bytes per
  token by sharing or compressing K/V. You pick these by picking the model, not by tuning.

### Levers that do less work per request
- **Prefix caching**: skip prefill for a prompt prefix already in cache. Cuts TTFT and compute for shared
  system prompts, few-shot blocks and multi-turn chat.
- **Prompt and output discipline**: shorter prompts cut prefill compute linearly; a sensible `max_tokens`
  and stop sequences stop decode from generating tokens nobody uses.
- **Speculative decoding**: a small draft model (or a lightweight head) proposes several tokens; the large
  model verifies them in one forward pass. Verification of *k* tokens costs barely more than one decode
  step because that step was bandwidth-bound anyway, so accepted drafts come almost free. It helps when
  the batch is small and the draft is accurate; at large batch the GPU is already busy and it can hurt.
- **Early exit on structure**: constrained decoding with a grammar removes retries for invalid JSON, which is
  an optimization at the request level rather than the kernel level.

### Levers that fit more work into the same bytes
- **Continuous batching**: keep the running batch full by admitting and retiring requests every iteration.
  Each weight read then serves many tokens, pushing *B* toward the ridge point.
- **Paged KV cache**: eliminate fragmentation so the memory pool holds more sequences, which is what makes
  large batches reachable.
- **Chunked prefill**: split long prompts into chunks interleaved with decode steps so prefill's compute
  and decode's bandwidth demand overlap rather than queue.
- **Tensor parallelism**: shard weights across GPUs so each reads a fraction of *W* per step; adds
  all-reduce communication per layer, so it pays off when a model does not fit or when per-GPU bandwidth is
  the limit.

### Levers at the kernel level
- **Fused attention kernels** (FlashAttention and its successors) avoid materializing the full attention
  matrix, cutting memory traffic in prefill and enabling long contexts.
- **Fused elementwise ops and CUDA graphs** remove per-kernel launch overhead, which at batch 1 can be a
  surprisingly large fraction of a step.
- **Compiled or engine-specific runtimes** (TensorRT-LLM style) specialize kernels to the model shape.

### Levers outside the engine
Route easy requests to a smaller model, cache whole responses for repeated queries, distill a task-specific
small model, or precompute embeddings. These often beat any engine knob because they cut the number of
tokens that touch an expensive GPU in the first place.

### The order of operations
Measure TTFT, TPOT, tokens/sec per GPU, batch size per step and KV-cache utilization. If batch size is
small and the pool is far from full, you are latency-bound per request: quantize weights, add speculative
decoding, trim prompts. If the pool is full, you are memory-bound: quantize cache, enable prefix caching,
choose a GQA model, add GPUs. If prefill dominates and TTFT is the complaint, enable chunked prefill,
prefix caching and shorter prompts. Changing a knob without knowing which regime you are in is how teams
"optimize" into a slower system.

## Example
A tiny roofline calculator that reproduces the reasoning above. The hardware numbers are rounded, publicly
quoted figures and are only meant to show the shape of the result, not to predict a benchmark.

```python
def decode_step_ms(params_b, batch, ctx_tokens, kv_bytes_per_token,
                   weight_bytes=2, bw_tb_s=3.0, tflops=900):
    """Lower bound on one decode iteration: bandwidth floor vs compute floor."""
    W = params_b * 1e9 * weight_bytes                # bytes of weights read per step
    C = batch * ctx_tokens * kv_bytes_per_token      # bytes of KV cache read per step
    t_bw = (W + C) / (bw_tb_s * 1e12)
    t_fl = 2 * batch * params_b * 1e9 / (tflops * 1e12)
    bound = "bandwidth" if t_bw >= t_fl else "compute"
    return 1000 * max(t_bw, t_fl), bound

KV8B = 2 * 32 * 8 * 128 * 2        # 8B GQA model, fp16 cache: ~128 KB per token
for batch in (1, 8, 64, 256):
    ms, b = decode_step_ms(8, batch, ctx_tokens=2048, kv_bytes_per_token=KV8B)
    print(f"fp16 batch={batch:4d}: {ms:6.2f} ms/step, {batch/ms*1000:8.0f} tok/s  ({b}-bound)")

ms4, _ = decode_step_ms(8, 1, 2048, KV8B, weight_bytes=0.5)   # 4-bit weights, fp16 cache
print(f"4-bit batch=1: {ms4:.2f} ms/step  -> weight bytes were the floor, so ~3-4x faster")
# Illustrative output shape: ~5.4 ms at batch 1 (~180 tok/s); by batch 256 the step is
# ~5x longer while aggregate tok/s is ~50x higher, and the growth is all KV-cache traffic.
```

Read it as: at batch 1 the step time is almost entirely weight traffic, so quantizing weights is the lever;
at batch 256 with 2k contexts the cache reads already exceed the weight reads, so cache size (GQA, cache
quantization) is the lever, and the compute term only takes over at larger batches with short contexts. The same script with `ctx_tokens=32000` shows cache reads overtaking weight reads at modest
batch sizes, which is when KV quantization and GQA start to pay.

## Trade-offs
- Weight quantization cuts bytes per step and VRAM but costs some accuracy; it flips against you when the
  task is sensitive (math, code, long reasoning) and no evaluation has been run on it.
- Speculative decoding lowers per-token latency at small batch by spending extra compute; at high
  utilization the GPU has no spare compute and it can reduce throughput.
- Larger batches raise throughput per GPU but lengthen every user's TPOT; the right point depends on
  whether you sell tokens per dollar or seconds per answer.
- Tensor parallelism spreads bandwidth across GPUs but adds inter-GPU communication every layer; it stops
  helping once the model fits comfortably and the interconnect is slow.
- Routing to a smaller model or caching responses can beat every engine knob, but only if you have an
  evaluation that proves the cheaper path meets the quality bar.

## Pitfalls
- Tuning for throughput on a batch benchmark and shipping to an interactive product; symptom is good
  tokens/sec dashboards alongside user complaints about slow typing.
- Enabling speculative decoding under heavy load; symptom is throughput dropping while per-request latency
  barely improves, because verification steals compute from the batch.
- Quantizing to 4-bit without a task-specific eval; symptom is a regression that only appears on the hard
  tail of the evaluation set, weeks after deployment.
- Chasing kernel-level gains while prompts carry thousands of tokens of boilerplate; symptom is prefill
  dominating TTFT no matter what engine flags change.
- Reading vendor peak FLOPS as a throughput promise; symptom is utilization in the single digits and a
  purchase order for faster GPUs that do not help decode.

## When to use
Reach for optimization when a measured number misses a target: TTFT above the budget, TPOT making text
feel slow, or cost per million tokens above plan. Classify the regime first (bandwidth-bound decode,
compute-bound prefill, or memory-capacity-bound concurrency) and pick the lever that attacks it. Do not
optimize a workload you have not profiled, and do not stack levers blindly: quantization plus speculative
decoding plus aggressive batching interact. In day-to-day work this is a loop of change one flag, re-run
the load test, compare TTFT/TPOT/throughput and a quality eval. In interviews, the expected answer to "how
would you make this faster" starts with "which bound are we hitting", not with a technique.

## Role lens
- **Engineer:** Runs the load test, reads batch size and cache utilization from engine metrics, and
  changes one flag at a time with a quality eval in the loop.
- **Architect:** Sets the latency and cost targets that define which regime matters, chooses GQA models
  and GPU tiers accordingly, and decides where routing to smaller models is acceptable.
- **FDE:** Diagnoses a customer's slow deployment by classifying the bottleneck from their metrics, then
  proposes the cheapest lever rather than the most impressive one.

## Check yourself
**Q:** A team serves a 70B model across two GPUs with tensor parallelism. Batch size per step averages 3,
the KV-cache pool is 30% used, and users complain about slow token streaming. Which optimizations would
you try first, which would you avoid, and why?
**A:** Batch 3 with a nearly empty cache pool means they are not memory-capacity-bound; they are
bandwidth-bound per step, reading 140 GB of fp16 weights for three tokens. The first lever is weight
quantization (fp8 or 4-bit with an eval), which cuts bytes per step directly; the second is speculative
decoding, which is ideal at low batch because the verification step is nearly free. Checking that CUDA
graphs and a fused attention kernel are enabled is cheap. Avoid adding GPUs for capacity or quantizing the
KV cache: the pool is not the constraint and cache traffic is small at batch 3. Also avoid raising batch
limits; there is no demand to fill them. If traffic later grows and the pool fills, the answer changes.

## Quiz
### Q: Why does weight quantization speed up single-request decode even though the GPU was not compute-limited?
- [ ] Smaller numbers are faster to multiply on tensor cores
- [x] Decode at batch 1 is bound by bytes read per step, and quantization halves or quarters those bytes
- [ ] Quantized models have fewer layers to run
- [ ] It reduces the number of tokens that must be generated
> At batch 1 the step time is the time to stream the weights through memory. Fewer bytes per weight means
> less traffic and a lower floor. Multiply speed is not the limit, and quantization changes neither depth
> nor output length.

### Q: When does speculative decoding stop paying off?
- [ ] When the draft model is smaller than the target model
- [ ] When prompts are longer than 1,000 tokens
- [x] When the batch is large enough that the GPU is already compute-busy, so verification has no slack to use
- [ ] When the KV cache is quantized
> Speculation exploits idle compute during bandwidth-bound decode. Once batching has made the step
> compute-bound, verifying drafted tokens competes with real work, and throughput falls. The draft being
> smaller is the normal setup, not a failure condition.

### Q: Which measurement tells you whether to prioritize KV-cache reductions over weight reductions?
- [ ] Average prompt length in characters
- [x] Whether the cache pool is near full and cache bytes read per step rival weight bytes
- [ ] The GPU's peak FLOPS rating
- [ ] Tokenizer vocabulary size
> Cache levers (quantized cache, GQA models, prefix caching) matter when concurrency and context make the
> cache the dominant memory traffic or the thing that caps concurrency. If the pool is mostly empty and
> batches are small, weight bytes dominate and weight quantization is the lever.
