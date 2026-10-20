---
id: gpu-memory-optimization
module: serving-inference
name: GPU memory optimization (VRAM math)
kind: practice
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - model-internals/kv-cache
  - serving-inference/kv-cache-memory-estimation
  - serving-inference/pagedattention
  - serving-inference/tensor-parallelism-at-inference
  - serving-inference/cpu-offloading
  - training-adaptation/quantization
  - model-internals/flashattention
  - infra-capacity-economics/gpu-sizing
sources:
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Dao et al., 2022 — FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness
  - Official docs: vLLM
  - Official docs: PyTorch (CUDA memory management)
---

## TL;DR
GPU memory at inference is a ledger with four lines: weights, KV cache, activation and workspace peaks,
and runtime overhead plus fragmentation. Write it down (`weights = params × bytes_per_param`, `KV =
2 × layers × kv_heads × head_dim × bytes × tokens_in_flight`), subtract from usable VRAM, and every
tuning knob, from quantization and model choice to context caps, concurrency caps, tensor parallelism
and chunked prefill, is just a term you are shrinking. Guessing instead of computing is how both
out-of-memory pages and 20%-utilized GPUs happen.

## Why it exists
Two failures bracket the problem. The first is the out-of-memory error at peak traffic: the deployment
passed every test because tests used short prompts and modest concurrency, and allocation is lazy, so
the first time a long prompt met a full decode batch the engine died. The second is quieter and costs
more: an 80 GB GPU running a model that leaves 50 GB unused because someone left the concurrency limit
at a cautious default, so tokens per dollar is a third of what the hardware allows.

Both come from treating memory as something you observe rather than compute. `nvidia-smi` shows a
number that mixes allocated, cached and reserved memory, and a model that "uses 18 GB" at idle can need
60 GB under load. The naive alternative, "start it and see if it fits", answers the wrong question,
because fitting at idle says nothing about capacity at peak, and because the quantities that matter,
tokens in flight and prompt length distribution, are properties of traffic, not of the model.

The arithmetic is short and exact enough to plan on. Weights are a fixed cost known from the parameter
count and precision. KV cache is a per-token cost known from the architecture, multiplied by a traffic
quantity you choose to cap. Activations are bounded by the largest step you allow. Everything else is a
few gigabytes of overhead you measure once. With those four terms you can decide, before launching,
how many concurrent conversations of what length a GPU can hold, and which lever to pull when it cannot.

## How it works
### Line 1: weights
`bytes = params × bytes_per_param`. bf16/fp16 is 2, fp8/int8 is 1, 4-bit is 0.5 plus the scales and
zero points: the simple `Q4_0` layout stores 32 weights × 4 bits plus a 16-bit scale, 4.5 bits per
weight; group-128 schemes such as GPTQ or AWQ land near 4.1–4.2. Count the embedding table and LM head
separately when they are untied: for a 128k-token vocabulary and hidden size 8,192 in bf16, each is
about 2.1 GB. For mixture-of-experts models count *all* experts, since every expert must be resident even
though only a few run per token; a model with 47B total and 13B active parameters costs 94 GB in bf16,
not 26 GB.

### Line 2: KV cache
`bytes_per_token = 2 × n_layers × n_kv_heads × head_dim × bytes_per_element`, then multiply by the
tokens in flight: the sum over active sequences of prompt plus generated tokens so far. With paged
allocation that is the *actual* total, not the sum of per-request maximums. The lever list is the formula
read term by term: fewer KV heads (GQA or MLA architectures), smaller elements (fp8 cache), fewer tokens
(context cap, concurrency cap, prefix sharing), or divide the whole thing across GPUs with tensor
parallelism.

### Line 3: activations and workspace
During prefill a chunk of T tokens flows through each layer as a [T, hidden] tensor, expands to
[T, intermediate] inside the MLP, and attention needs its own buffers. FlashAttention-style kernels
never materialise the [T, T] score matrix, so attention memory is linear in T; without them a 32k
prompt would need gigabytes of scores per head. Chunked prefill caps T per step, which caps this peak
regardless of prompt length. Sampling needs a logits buffer of [batch, vocab] in fp32: 256 sequences ×
128k vocabulary × 4 bytes is about 131 MB, small but real. Engines estimate this line by running a dummy
forward pass at the maximum batch and token budget during startup, which is why those limits change the
reported KV capacity.

### Line 4: runtime overhead and fragmentation
The CUDA context, cuBLAS workspaces, NCCL buffers for tensor parallelism and CUDA-graph captures take a
few hundred megabytes to a couple of gigabytes as an illustrative range, per process, which is why two
processes on one GPU waste more than one. PyTorch's caching allocator holds freed blocks in reserve, so
`memory_reserved()` exceeds `memory_allocated()`, and variable-size allocations fragment that reserve
over time; the `expandable_segments` allocator option in recent PyTorch versions reduces it. Serving
engines sidestep most fragmentation by pre-allocating the KV pool in fixed blocks and by profiling the
activation peak once, which is the design logic of PagedAttention applied to the whole budget.

### Putting the ledger together
```
usable          = VRAM × memory_fraction          (engine budget, e.g. 0.9)
pool_for_kv     = usable − weights − activation_peak − overhead
tokens_capacity = pool_for_kv / bytes_per_token
concurrency     = tokens_capacity / expected_tokens_per_sequence
```
Run it per GPU under tensor parallelism, with weights and KV divided by the TP degree but overhead not.
Then check the result against the latency target, because a capacity of 60 concurrent sequences is
useless if the step time at 60 breaks the per-token SLO.

### Reading the instruments
`torch.cuda.memory_allocated()`, `memory_reserved()` and `max_memory_allocated()` separate live
tensors, allocator reserve and the true peak; `torch.cuda.memory_summary()` shows fragmentation.
Engines log the number of KV blocks they could allocate at startup, which is the single most useful
number to compare against the ledger. `nvidia-smi` is for spotting unexpected processes and for the
total, not for attribution.

## Example
A Llama-3-70B-class model (80 layers, 8 KV heads, head_dim 128, hidden 8,192, about 140 GB in bf16) on
two 80 GB GPUs with tensor parallelism of 2 and a 0.9 memory fraction, so 72 GB usable per GPU.

In bf16, weights are about 70.6 GB per GPU, which with any reserve already exceeds the 72 GB budget:
not a deployment. Quantize the weights to int8: about 35.3 GB per GPU. KV per token is 2 × 80 × 8 × 128
× 2 = 320 KB in fp16, or 160 KB per GPU after sharding. Reserve an illustrative 2 GB for activation peak
and overhead: 72 − 35.3 − 2 ≈ 34.7 GB for the pool, which is 34.7 × 2³⁰ ÷ 160 KB ≈ 227,000 tokens. At an
expected 8k tokens per conversation that is about 27 concurrent conversations. Switching the KV cache to
fp8 halves bytes per token and doubles that to about 55; capping context at 4k doubles it again to about
111. Each step of the plan is one term of the ledger, and the calculator below reproduces it so the
numbers can be re-run for any model.

```python
def vram_plan(vram_gb, params_b, w_bytes, layers, kv_heads, head_dim, kv_bytes,
              tp=1, fraction=0.9, reserve_gb=2.0, tokens_per_seq=8192, vocab=128_256, hidden=8192):
    usable = vram_gb * fraction
    weights = params_b * w_bytes / tp                       # GB per GPU (1e9 params ~ 1 GB at 1 byte)
    kv_per_token = 2 * layers * kv_heads * head_dim * kv_bytes / tp
    pool_gb = usable - weights - reserve_gb
    tokens = int(pool_gb * 2**30 / kv_per_token) if pool_gb > 0 else 0
    return dict(weights_gb=round(weights, 1), kv_kb_per_token=kv_per_token / 1024,
                pool_gb=round(pool_gb, 1), tokens=tokens, concurrent=tokens // tokens_per_seq)

base = dict(vram_gb=80, params_b=70.6, layers=80, kv_heads=8, head_dim=128, tp=2)
print("bf16 weights, fp16 KV:", vram_plan(w_bytes=2, kv_bytes=2, **base))
print("int8 weights, fp16 KV:", vram_plan(w_bytes=1, kv_bytes=2, **base))
print("int8 weights, fp8 KV: ", vram_plan(w_bytes=1, kv_bytes=1, **base))
print("int8, fp8 KV, 4k ctx: ", vram_plan(w_bytes=1, kv_bytes=1, tokens_per_seq=4096, **base))
```

## Trade-offs
- Quantizing weights frees the largest line and speeds decode, but every quantized artifact needs its own
  quality evaluation; the saving is not worth a silent regression on the task.
- An fp8 KV cache doubles token capacity at low reported quality cost, yet long-context retrieval is
  where cache precision bites, so the trade flips as context lengths grow.
- A higher memory fraction gives more blocks, but shrinks the headroom for activation peaks and other
  processes; past about 0.9–0.95 the OOM risk rises faster than capacity.
- Lowering the context cap raises concurrency proportionally but rejects or truncates legitimate long
  requests; the right cap is the measured p99 of real traffic, not the model's maximum.
- Tensor parallelism divides weights and cache across GPUs, but adds per-GPU overhead and interconnect
  cost, so two GPUs give less than twice the capacity of one.

## Pitfalls
- Sizing from weights alone. The symptom is a server that starts cleanly, reports healthy, and OOMs or
  preempts heavily the first time concurrency and context length rise together.
- Reading `nvidia-smi` as "memory the model needs"; it includes the allocator's reserve and the
  pre-allocated KV pool, so it reports near-full even at idle for a serving engine.
- Running a second process (an embedding model, a reranker) on the same GPU without re-profiling; the
  engine's pool was sized to the free memory at its own startup, and the newcomer OOMs or evicts it.
- Testing with short prompts and small batches so the activation peak is never exercised; the first
  32k-token prompt during a full decode batch triggers the OOM that profiling would have predicted.
- Treating a mixture-of-experts model as its active-parameter size when budgeting weights, then finding
  the checkpoint does not load.

## When to use
Do the ledger before every deployment, every model swap, every change to context or concurrency limits,
and every time a new co-tenant lands on a GPU. Use it to pick between levers in a fixed order: a model
with fewer KV heads or a quantized weight format first, then context and concurrency caps matched to the
traffic p99, then fp8 cache, then tensor parallelism, with CPU offloading as a last resort for
non-interactive work. Measure the engine's reported block count and peak allocated memory under a
production-shaped load test and compare them with the ledger; a gap means a term you forgot. In
interviews, deriving bytes-per-token and walking the four lines for a named model on a named GPU is the
standard test of serving depth.

## Role lens
- **Engineer:** Computes the ledger in a notebook, sets `gpu_memory_utilization`, `max_model_len` and
  sequence limits from it, and verifies against the engine's logged block count and peak memory.
- **Architect:** Chooses GPU tier, precision policy and tensor-parallel degree per model from the ledger,
  and sets the context and concurrency policy that capacity planning depends on.
- **FDE:** Sizes a customer's self-hosted deployment on the hardware they have, showing the four lines
  and which lever changes their concurrent-user number before any purchase decision.

## Check yourself
**Q:** A customer wants to serve a Llama-3-8B-class model (32 layers, 8 KV heads, head_dim 128, ~16 GB
in bf16) on a single 24 GB GPU with 16k-token documents and asks how many users it supports. Walk the
ledger.
**A:** Usable memory at a 0.9 fraction is about 21.6 GB. Weights take 16 GB, and an illustrative 1.5 GB
goes to activation peak and runtime overhead, leaving roughly 4 GB for KV cache. KV per token is 2 × 32 ×
8 × 128 × 2 = 128 KB in fp16, so the pool holds around 32k tokens: two 16k documents in flight, not a
multi-user service. The levers in order: quantize weights to int8 (8 GB freed, about 96k tokens, six
documents) or 4-bit (another 4 GB, roughly 130k tokens), use an fp8 KV cache to double any of those,
enable chunked prefill so a 16k prefill does not blow the activation peak, and confirm the step time at
the resulting concurrency still meets their latency target. If they need dozens of concurrent documents,
the answer is a larger GPU, not more tuning.

## Quiz
### Q: Which term of the memory ledger grows with traffic rather than with the model?
- [ ] Weights, because each request loads its own copy of the parameters
- [x] The KV cache, because it scales with tokens in flight across all active sequences
- [ ] Runtime overhead, because the CUDA context grows with each connection
- [ ] The embedding table, because new tokens are added as prompts arrive
> Weights and overhead are fixed once the model is loaded. The cache is proportional to the total number
> of prompt and generated tokens currently being served, which is a property of traffic you must cap.

### Q: Why does a serving engine run a dummy forward pass at startup before allocating its KV pool?
- [ ] To warm up the CUDA kernels so the first request is fast
- [ ] To verify the model weights loaded correctly
- [x] To measure the peak activation memory at the configured batch and token limits, so the pool can take the rest
- [ ] To compute the perplexity of the model on a calibration set
> Activation and workspace memory depends on the maximum step the scheduler may build. Measuring it
> once lets the engine give every remaining byte to KV blocks without risking OOM on the largest step.

### Q: A 47B-total, 13B-active mixture-of-experts model is planned for a GPU budget sized from 13B parameters. What happens?
- [ ] It fits, because only active experts are loaded per token
- [ ] It fits if the KV cache is quantized to fp8
- [x] It fails to load, because all experts must be resident and weights cost 47B × bytes per parameter
- [ ] It loads but runs at one-quarter speed due to expert swapping
> Routing picks experts per token, but every expert can be chosen, so the full set must sit in GPU
> memory. Active parameters predict compute per token, not the weight footprint.
