---
id: tpot
module: serving-inference
name: TPOT (time per output token)
kind: metric
difficulty: core
est_minutes: 14
status: reviewed
related:
  - serving-inference/ttft
  - serving-inference/tokens-sec
  - serving-inference/prefill-vs-decode
  - serving-inference/continuous-batching
  - model-internals/kv-cache
  - model-internals/speculative-decoding
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
  - serving-inference/tensor-parallelism-at-inference
sources:
  - Pope et al., 2022 — Efficiently Scaling Transformer Inference
  - Yu et al., 2022 — Orca: A Distributed Serving System for Transformer-Based Generative Models
  - Leviathan et al., 2023 — Fast Inference from Transformers via Speculative Decoding
---

## TL;DR
Time per output token (TPOT) is the average gap between successive tokens once generation has
started: `(t_last − t_first) / (n_out − 1)`. Each decode step must read the whole model's weights plus
the KV cache from GPU memory, so TPOT has a floor of bytes-read divided by memory bandwidth, and it
barely rises when more requests share the step. That is why batching is almost free for throughput,
why quantisation and tensor parallelism speed up single streams, and why a faster-FLOPs GPU alone
does not.

## Why it exists
After the first token appears, total latency is `TTFT + (n_out − 1) × TPOT`, and for any answer longer
than a few sentences the second term wins. A 600-token answer at 50 ms per token is 30 s; at 15 ms it
is 9 s. The user's experience of "is this thing fast" after the first word, and an agent's total
time across a dozen chained calls, are both governed by this one number.

It needs to be its own metric because decode has different physics from prefill. Prefill is one big
matrix multiplication per layer and is compute-bound. Decode processes a single new token per
sequence, so the arithmetic per step is tiny while the model's entire weight set still has to be
streamed from HBM into the compute units every step. The step time is therefore set by bytes moved,
not by FLOPs, and the levers that help it (fewer bytes per step, more bandwidth, more tokens per step)
are not the levers that help TTFT.

The naive alternative, "report tokens per second", is ambiguous between one stream and the whole
server, and hides stalls: a stream averaging 20 ms per token with a 400 ms freeze every few seconds
looks fine as a mean and feels broken. TPOT alongside its distribution (inter-token latency, ITL)
makes the difference visible and tells you which engine behaviour caused it.

## How it works
### Definition and its sibling
TPOT is a per-request mean: total decode time divided by the number of gaps. Inter-token latency
(ITL) is the distribution of the individual gaps; its p99 catches the stalls that the mean hides.
Both exclude TTFT on purpose, so a long prompt does not pollute the decode measurement. Tools are
not consistent about the names: some dashboards call the per-request mean "ITL", and some quote a
"latency per token" of `end_to_end / n_out`, which silently folds TTFT back in. Check the formula
before comparing two reports.

### The bandwidth floor
In one decode step for a batch of *B* sequences, the GPU reads every weight matrix once (the same
weights serve all *B* tokens) and, for attention, reads the KV cache of every sequence. Ignoring
compute, the step cannot be faster than:

```
step_time ≥ (weight_bytes + Σ kv_bytes_per_sequence) / memory_bandwidth
```

Illustrative numbers: an 8B-parameter model in fp16 is about 16 GB of weights. An H100 SXM moves
roughly 3.35 TB/s from HBM. At batch 1 with a short context, the floor is 16 / 3350 ≈ 4.8 ms per
token, i.e. a ceiling of about 210 tokens per second for a single stream. Real systems land below
that ceiling because of kernel launch overhead, imperfect attention kernels, sampling and the host
loop, but the ceiling is the right mental model: no amount of extra FLOPs lowers it. A GPU with
similar bandwidth and double the FLOPs gives nearly the same TPOT at low batch.

### Why batching is nearly free
The weight read is paid once per step regardless of *B*, so going from 1 to 16 concurrent sequences
multiplies the arithmetic by 16 but adds only the KV-cache reads to the bytes moved. Per-request
TPOT rises gently while aggregate throughput rises almost linearly. This continues until one of two
things happens: the arithmetic finally catches up with the bandwidth, or the KV cache of all the
sequences no longer fits in memory. The first crossover can be estimated. Counting only the weight
matmuls, a step does `2 × N × B` FLOPs while reading `N × bytes_per_param` bytes, so arithmetic
intensity is `2B / bytes_per_param` FLOPs per byte. The GPU becomes compute-bound when that reaches
its ratio of peak FLOP/s to bandwidth, roughly 1e15 / 3.35e12 ≈ 300 for an H100 in bf16. With fp16
weights that is a batch near 300, independent of model size; with int8 weights it halves, because
each byte read now feeds twice the arithmetic. KV reads add bytes without much arithmetic, which
pushes the real crossover later still. Long contexts pull that second limit in fast, because KV bytes scale with
`B × context_length`, and at long contexts the KV reads, not the weights, dominate the step.

### Levers that move TPOT
- **Fewer weight bytes.** Weight-only int8 or int4 quantisation cuts the bytes read per step by 2–4×,
  which is a direct TPOT win at low batch; at high batch, where compute starts to bind, the gain
  shrinks and dequantisation cost shows.
- **Fewer KV bytes.** Grouped-query attention, KV-cache quantisation and shorter contexts reduce the
  per-sequence term, which matters most for long-context, high-concurrency serving.
- **More bandwidth per model.** Tensor parallelism splits the weights across *k* GPUs so each reads
  `1/k` of them per step; the all-reduce after each layer adds a fixed communication cost, so it
  helps most on NVLink-connected GPUs and large models.
- **More tokens per step.** Speculative decoding drafts several tokens cheaply and verifies them in one
  forward pass of the big model, so a step that costs one weight read can yield two or three accepted
  tokens. Effective TPOT falls at low batch; at high batch the wasted verification compute can lower
  throughput instead. It also changes the shape of ITL: accepted tokens arrive in bursts of several
  per step, so the gap distribution becomes lumpy (near-zero gaps inside a burst, a full step between
  bursts) even when the mean improves.
- **A smaller model.** Halving parameters roughly halves bytes per step; whether quality survives is
  an eval question, not a serving one.

### Where the stalls come from
In continuous batching, every running stream advances one token per engine step. When a new request
is admitted, its prefill runs inside that step; a 10k-token prefill can take hundreds of
milliseconds, during which every other stream emits nothing. The result is a flat 25 ms ITL punctuated
by spikes, which is a scheduling artefact, not a model property. Chunked prefill caps the prefill
work per step to bound the stall. Preemption is the other source: when KV memory runs out the engine
evicts a sequence and later recomputes or swaps its cache back, and that sequence's ITL shows a
multi-second gap.

### How fast is fast enough
Adults read prose at roughly 200–300 words per minute, about 4–5 words per second, which at the
usual 1.3 tokens per English word is around 5–7 tokens per second, so a TPOT near 150–200 ms
already keeps pace with a reader for plain chat. Products still aim much lower because users skim, scroll
and want code to appear in blocks, and because agentic workloads consume tokens by machine: a
planner that makes eight sequential calls of 300 tokens each turns a 30 ms TPOT into over a minute
of waiting before anyone reads a word.

## Example
A worked scenario using the bandwidth floor, so you can predict TPOT before buying hardware. Take a
Llama-3-8B-style model: 8B parameters in fp16 (16 GB of weights), 32 layers, 8 KV heads, head
dimension 128. KV cache per token is `2 × 32 × 8 × 128 × 2 bytes = 131,072 bytes`, i.e. 128 KB. Put
it on an H100 SXM with about 3.35 TB/s of HBM bandwidth and ignore compute, which is safe here
because even at batch 100 the decode FLOPs per step (`2 × 8e9 × 100 = 1.6e12`) take only about
3 ms at half of the roughly 1e15 FLOP/s peak, well under the bandwidth time at that batch.

Batch 1, 4k-token context: bytes per step = 16 GB + 4,000 × 128 KB ≈ 16.5 GB → 16.5 / 3350 ≈ 4.9 ms →
about 200 tokens per second for that one user.

Batch 32, each at 4k context: KV reads = 32 × 4,000 × 131,072 B ≈ 16.8 GB, so bytes per step ≈ 32.8 GB →
about 9.8 ms per step. Each user now sees roughly 100 tokens per second, but the server emits
32 × 100 ≈ 3,300 tokens per second. Sixteen times the throughput for twice the TPOT.

Batch 32 at 32k context: KV reads = 32 × 32,000 × 131,072 B ≈ 134 GB, which does not even fit next to the
weights in 80 GB, and would take about 45 ms per step if it did. Long context, not model size, is
what pushes TPOT up at scale. Measured numbers will be worse than every figure here; the point is
the ratios and which term dominates.

## Trade-offs
- Raising concurrency multiplies throughput for a small TPOT cost while weights dominate the step,
  but once KV reads dominate (long contexts) or compute binds, each added sequence costs every user
  real latency.
- Weight quantisation lowers TPOT at low batch and can cut quality on some tasks; at high batch the
  speed gain fades while the quality cost stays, so evaluate both before shipping.
- Tensor parallelism cuts per-GPU bytes but adds a per-layer all-reduce; on PCIe-connected GPUs the
  communication can eat the gain, so it pays mainly with NVLink and large models.
- Speculative decoding reduces effective TPOT when the acceptance rate is high and the batch is
  small; with low acceptance or a saturated GPU it burns compute and can reduce throughput.
- Chunked prefill smooths ITL for running streams at the price of slower TTFT for long prompts; for
  a prefill-heavy, low-concurrency workload you may prefer the stalls.

## Pitfalls
- Expecting a faster-FLOPs GPU to speed up single-stream decode proportionally; the symptom is a
  costly upgrade that moves TPOT only as much as the bandwidth ratio.
- Reporting TPOT only as a mean; the symptom is users describing "freezes" while the dashboard shows
  a healthy 25 ms, because prefill stalls and preemptions live in p99 ITL.
- Including TTFT in the TPOT calculation; the symptom is TPOT that mysteriously grows with prompt
  length even though decode speed has not changed.
- Benchmarking TPOT at batch 1 and sizing concurrency from it; the symptom is per-user speed that
  halves the moment real traffic arrives.
- Ignoring context length in capacity math; the symptom is TPOT and preemptions climbing as
  conversations get longer during the day.
- Counting streamed chunks as tokens when computing TPOT; servers can coalesce tokens, so the
  symptom is a TPOT that looks better than the provider's own usage numbers imply.

## When to use
Track TPOT (and p99 ITL) for every surface where output length is non-trivial: chat, code
generation, long-form drafting, and above all agent loops where total time is a sum of many decode
phases. Set the target from the consumer: a human reader is satisfied by a few hundred milliseconds
per token, a code editor wants tens of milliseconds, and an orchestrator chaining calls wants the
lowest you can afford. Use the bandwidth floor to sanity-check vendor claims and hardware choices
before testing, then measure under realistic concurrency and context length. In interviews, deriving
the floor from weights, KV size and bandwidth, and then explaining why batching barely changes it, is
the expected depth.

## Role lens
- **Engineer:** Measures TPOT and p99 ITL from the stream, turns on chunked prefill when stalls
  appear, and tests weight quantisation against an eval set before enabling it.
- **Architect:** Picks GPU class by memory bandwidth and capacity rather than FLOPs for decode-heavy
  workloads, and sets the concurrency ceiling where per-user TPOT still meets the SLO.
- **FDE:** Explains to a customer with an on-prem A100 why their chat streams at half the speed of
  the demo on H100s, and which of quantisation, TP or a smaller model is viable for them.

## Check yourself
**Q:** Product wants chat tokens to stream twice as fast. The team proposes moving from A100 to H100
GPUs. Is that enough, what else would you consider, and what are the catches of each option?
**A:** Decode is bandwidth-bound, so the upgrade buys roughly the ratio of memory bandwidth, about
1.6× from A100-80GB to H100 SXM, not 2×, and less if the batch is large enough to be partly
compute-bound. To close the gap: weight-only int8 or int4 quantisation halves or quarters the bytes
per step, with a quality check required; tensor parallelism across two NVLink GPUs halves per-GPU
reads but adds all-reduce latency and doubles hardware per replica; speculative decoding can give
2–3× at low batch if the draft model's acceptance rate is high, but wastes compute at high load;
lowering per-replica concurrency raises per-user speed at a throughput cost. Before any of it, check
p99 ITL: if the complaint is really periodic stalls from prefill or preemption, chunked prefill and
more KV memory headroom fix it with no hardware change.

## Quiz
### Q: Why does per-request TPOT rise only slightly when the batch grows from 1 to 16 sequences?
- [ ] The GPU has 16 streaming multiprocessors, one per sequence
- [x] The model weights are read from memory once per step and shared by all sequences, so only the KV-cache reads grow
- [ ] Continuous batching pauses other sequences while each one decodes
- [ ] Larger batches switch the engine from fp16 to int8 automatically
> A decode step is dominated by streaming the weights from HBM; that cost is paid once regardless of
> batch size. Each extra sequence adds only its KV reads and a little arithmetic, so throughput scales
> nearly linearly while per-token latency creeps up.

### Q: Which of these does NOT reduce TPOT for a single stream at batch 1?
- [ ] Weight-only int4 quantisation
- [ ] Speculative decoding with a well-matched draft model
- [x] Prefix caching of the system prompt
- [ ] Tensor parallelism across two NVLink-connected GPUs
> Prefix caching avoids prefill work and therefore helps TTFT, but every decode step still reads the
> full weights. Quantisation and tensor parallelism cut bytes per step, and speculative decoding
> yields several tokens per weight read.

### Q: A stream shows a steady 25 ms between tokens with occasional 300 ms gaps. What is the most likely cause?
- [ ] Network jitter between the client and the server
- [ ] The sampling temperature is too high for stable decoding
- [x] Prefill of newly admitted requests, or preemption, running inside the shared engine step
- [ ] The tokenizer is slow on rare words
> In continuous batching, a long prompt entering the batch makes that step expensive and every other
> stream waits for it; preemption when KV memory is exhausted causes the same signature. Chunked
> prefill and more memory headroom are the fixes, not network or sampling changes.
