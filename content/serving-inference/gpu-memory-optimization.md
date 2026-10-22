---
id: gpu-memory-optimization
module: serving-inference
name: GPU memory optimization (VRAM math)
kind: practice
difficulty: core
est_minutes: 16
status: crisp-verified
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
Inference memory is a four-line ledger: weights, KV cache, activation peaks, and overhead plus
fragmentation. Subtract them from usable VRAM and every tuning knob is a term you shrink. Guessing causes
both out-of-memory pages and 20%-utilized GPUs.

## Why it exists
- **Failure 1: OOM at peak traffic.** Tests used short prompts and modest concurrency, and allocation is
  lazy, so the first time a long prompt met a full decode batch the engine died.
- **Failure 2: quietly wasted hardware.** An 80 GB GPU leaves 50 GB unused because the concurrency limit
  stayed at a cautious default, so tokens per dollar is a third of what the hardware allows.
- **Both come from observing instead of computing.** `nvidia-smi` mixes allocated, cached and reserved
  memory, and a model that "uses 18 GB" at idle can need 60 GB under load.
- **"Start it and see if it fits" answers the wrong question.** Fitting at idle says nothing about
  capacity at peak, because what matters (tokens in flight, prompt-length distribution) is a property of
  traffic, not the model.
- **The arithmetic is short and exact enough to plan on.** Weights are fixed by parameter count and
  precision; KV cache is a per-token cost times a traffic quantity you cap; activations are bounded by the
  largest step you allow; overhead is a few gigabytes you measure once.

## How it works
### Line 1: weights
`bytes = params × bytes_per_param`, or as a ledger line, `weights = params × bytes_per_param`.

| Precision | Bytes per param |
|---|---|
| bf16/fp16 | 2 |
| fp8/int8 | 1 |
| 4-bit | 0.5 plus scales and zero points |

- **4-bit is more than 4 bits.** The simple `Q4_0` layout stores 32 weights × 4 bits plus a 16-bit scale,
  4.5 bits per weight; group-128 schemes such as GPTQ or AWQ land near 4.1–4.2.
- **Embeddings and LM head.** Published parameter counts include them; if you estimate from per-layer
  shapes, add them. For a 128k-token vocabulary and hidden size 8,192 in bf16 each is about 2.1 GB, and
  an untied model has both. Weight-only quantizers often leave them at higher precision, so a "4-bit" file
  is larger than params × 0.5 suggests.
- **Everything else resident:** a draft model for speculative decoding (plus its own KV cache), a vision
  encoder, preallocated LoRA adapter slots.
- **MoE: count all experts,** since every expert must be resident though only a few run per token. A model
  with 47B total and 13B active parameters costs 94 GB in bf16, not 26 GB.

### Line 2: KV cache
```
bytes_per_token = 2 × n_layers × n_kv_heads × head_dim × bytes_per_element
KV = 2 × layers × kv_heads × head_dim × bytes × tokens_in_flight
```
- **Tokens in flight** = the sum over active sequences of prompt plus generated tokens so far.
- **With paged allocation** that is the *actual* total, not the sum of per-request maximums.
- **The levers are the formula read term by term:** fewer KV heads (GQA or MLA architectures), smaller
  elements (fp8 cache), fewer tokens (context cap, concurrency cap, prefix sharing), or divide it across
  GPUs with tensor parallelism.

### Line 3: activations and workspace
- **Prefill shape.** A chunk of T tokens flows through each layer as [T, hidden], expands to
  [T, intermediate] inside the MLP, and attention needs its own buffers.
- **FlashAttention keeps attention linear in T,** because it never materialises the [T, T] score matrix;
  without it a 32k prompt would need gigabytes of scores per head.
- **Chunked prefill caps T per step,** which caps this peak regardless of prompt length.
- **Scale.** With T = 8,192 and a 70B-class intermediate size of 28,672, gate and up projections each
  produce 8,192 × 28,672 × 2 bytes ≈ 0.47 GB, about 0.94 GB together before a tensor-parallel split. It is
  one layer's transient, reused layer after layer, not summed.
- **The LM-head trap.** On every prompt position it would produce 8,192 × 128k × 4 bytes ≈ 4.2 GB of fp32
  logits, which is why engines compute logits only at positions they sample from.
- **Sampling buffer.** [batch, vocab] in fp32: 256 sequences × 128k vocabulary × 4 bytes is about 131 MB,
  small but real.
- **Engines estimate this line** with a dummy forward pass at the maximum batch and token budget during
  startup, which is why those limits change the reported KV capacity.

### Line 4: runtime overhead and fragmentation
- **Per process:** the CUDA context, cuBLAS workspaces, NCCL buffers for tensor parallelism and CUDA-graph
  captures take a few hundred megabytes to a couple of gigabytes (illustrative range). That is why two
  processes on one GPU waste more than one.
- **Allocator reserve.** PyTorch's caching allocator holds freed blocks, so `memory_reserved()` exceeds
  `memory_allocated()`, and variable-size allocations fragment that reserve over time.
- **Mitigation.** The `expandable_segments` allocator option in recent PyTorch versions reduces
  fragmentation.
- **Engines sidestep most of it** by pre-allocating the KV pool in fixed blocks and profiling the
  activation peak once: PagedAttention's design logic applied to the whole budget.

### Putting the ledger together
```
usable          = VRAM × memory_fraction          (engine budget, e.g. 0.9)
pool_for_kv     = usable − weights − activation_peak − overhead
tokens_capacity = pool_for_kv / bytes_per_token
concurrency     = tokens_capacity / expected_tokens_per_sequence
```
- **Under tensor parallelism** run it per GPU, with weights and KV divided by the TP degree but overhead
  not.
- **Then check latency.** A capacity of 60 concurrent sequences is useless if the step time at 60 breaks
  the per-token SLO.

### Reading the instruments
| Instrument | Tells you |
|---|---|
| `torch.cuda.memory_allocated()` | live tensors |
| `memory_reserved()` | allocator reserve |
| `max_memory_allocated()` | the true peak |
| `torch.cuda.memory_summary()` | fragmentation |
| engine startup log | KV blocks allocated: the most useful number to compare with the ledger |
| `nvidia-smi` | unexpected processes and the total, not attribution |

## Diagram
The ledger as a pipeline: each subtraction is a lever, and the final concurrency must still pass the
latency check.

```mermaid
flowchart TD
    V["VRAM x memory fraction"] --> W["Minus weights"]
    W --> A["Minus activation peak"]
    A --> O["Minus overhead"]
    O --> P[("KV pool")]
    P -->|"divide by bytes per token"| T["Tokens of capacity"]
    T -->|"divide by tokens per sequence"| C["Concurrent sequences"]
    C --> L{"Step time meets SLO?"}
    L -->|"no"| R["Lower concurrency or add GPUs"]
    L -->|"yes"| D["Set engine limits"]
    Q["Quantize, fp8 KV, caps, TP"] -.->|"shrink weights"| W
    Q -.->|"fewer bytes or tokens"| T
```

## Examples
### Worked plan: Llama-3-70B-class on two 80 GB GPUs
80 layers, 8 KV heads, head_dim 128, hidden 8,192, about 140 GB in bf16. TP = 2 and a 0.9 fraction give
72 GB usable per GPU.

| Step | Per-GPU ledger | Result |
|---|---|---|
| bf16 weights | about 70.6 GB against 72 GB | essentially nothing left for cache (a few GB at best, depending on GB vs GiB): not useful |
| int8 weights | about 35.3 GB | KV per token 2 × 80 × 8 × 128 × 2 = 320 KB in fp16, 160 KB per GPU after sharding |
| Reserve 2 GB (illustrative) | 72 − 35.3 − 2 ≈ 34.7 GB pool | 34.7 × 2³⁰ ÷ 160 KB ≈ 227,000 tokens |
| 8k tokens per conversation | | about 27 concurrent conversations |
| fp8 KV cache | bytes per token halved | about 55 |
| Context capped at 4k | tokens per conversation halved | about 111 |

Each step is one term of the ledger. The calculator reproduces it for any model. It blurs GB and GiB, a 7%
difference smaller than the uncertainty in the reserve; when the margin is that thin, use the engine's
logged block count instead.

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

### Check the ledger against a live process
Measures weights, predicted KV and the prefill peak for a small model with PyTorch's own counters. The
gap between peak and live memory is Line 3; the gap between reserved and allocated is Line 4's allocator
reserve.

```python
import torch
from transformers import AutoModelForCausalLM

GB, T = 2**30, 4096
model = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-0.5B", torch_dtype=torch.bfloat16).cuda().eval()
cfg = model.config
params = sum(p.numel() for p in model.parameters())
print(f"Line 1 weights: predicted {params * 2 / GB:.2f} GiB, "
      f"allocated {torch.cuda.memory_allocated() / GB:.2f} GiB")

head_dim = cfg.hidden_size // cfg.num_attention_heads
kv_pred = 2 * cfg.num_hidden_layers * cfg.num_key_value_heads * head_dim * 2 * T
print(f"Line 2 KV for {T} tokens (bf16): predicted {kv_pred / GB:.3f} GiB")

torch.cuda.reset_peak_memory_stats()
ids = torch.randint(0, cfg.vocab_size, (1, T), device="cuda")
with torch.no_grad():
    out = model(ids, use_cache=True)                   # one prefill; logits for EVERY position
print(f"live after prefill {torch.cuda.memory_allocated() / GB:.2f} GiB, "
      f"peak {torch.cuda.max_memory_allocated() / GB:.2f} GiB, "
      f"reserved {torch.cuda.memory_reserved() / GB:.2f} GiB")
print(f"full logits tensor alone: {out.logits.numel() * out.logits.element_size() / GB:.2f} GiB")
```

The last line shows the LM-head trap at small scale: a large vocabulary times every prompt position, which
serving engines avoid by computing logits only where they sample.

## Production patterns
- **Derive engine limits from the ledger.** Before launch, compute the pool and set vLLM's
  `--gpu-memory-utilization`, `--max-model-len` and `--max-num-seqs` from it. Compare the KV-cache size
  the engine logs at startup with your prediction; a large gap is a forgotten term.
- **Load-test the worst step, not the average.** Include your longest allowed prompt arriving during a full
  decode batch, so the activation peak profiling predicted is actually exercised. Monitor peak memory,
  OOMs and preemptions during the test.
- **One engine per GPU, or explicit fractions.** If an embedder or reranker must share a GPU, start it
  first and give the main engine a lower memory fraction, then re-profile. Alert on unexpected processes
  in `nvidia-smi`.
- **Reduce fragmentation in custom PyTorch servers.** Set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`
  and watch the reserved-minus-allocated gap and `memory_summary()` over long runs.
- **Pull levers in a fixed order.** Fewer KV heads or quantized weights first, then context and
  concurrency caps at the traffic p99, then an fp8 cache (`--kv-cache-dtype fp8`) after an eval, then
  tensor parallelism. Re-run the ledger and the load test after each change.

## Trade-offs
- **Quantized weights vs quality risk.** Quantizing frees the largest line and speeds decode, but every
  quantized artifact needs its own quality evaluation; the saving is not worth a silent regression.
- **fp8 KV cache vs long-context accuracy.** It doubles token capacity at low reported quality cost, yet
  long-context retrieval is where cache precision bites, so the trade flips as context lengths grow.
- **Higher memory fraction vs headroom.** More blocks, but less headroom for activation peaks and other
  processes; past about 0.9–0.95 the OOM risk rises faster than capacity.
- **Lower context cap vs legitimate long requests.** Concurrency rises proportionally, but long requests
  are rejected or truncated; the right cap is the measured p99 of real traffic, not the model's maximum.
- **Tensor parallelism vs per-GPU overhead.** It divides weights and cache across GPUs but adds per-GPU
  overhead and interconnect cost, so two GPUs give less than twice the capacity of one.

## Pitfalls
- **Sizing from weights alone.** Symptom: a server that starts cleanly, reports healthy, and OOMs or
  preempts heavily the first time concurrency and context length rise together. Fix: write all four
  lines and size KV from capped tokens in flight.
- **Reading `nvidia-smi` as "memory the model needs".** It includes the allocator's reserve and the
  pre-allocated KV pool, so a serving engine reports near-full even at idle. Fix: use the PyTorch
  counters and the engine's logged KV size.
- **Adding a second process without re-profiling** (an embedding model, a reranker). The engine's pool was
  sized to the free memory at its own startup, so the newcomer OOMs or evicts it. Fix: lower the main
  engine's memory fraction and restart it.
- **Testing only short prompts and small batches.** The activation peak is never exercised; the first
  32k-token prompt during a full decode batch triggers the OOM profiling would have predicted. Fix:
  load-test the worst step.
- **Budgeting an MoE model by its active parameters.** The checkpoint then does not load. Fix: budget
  weights from total parameters.

## When to use
- **Do the ledger** before every deployment, model swap, change to context or concurrency limits, and
  whenever a new co-tenant lands on a GPU.
- **Pick levers in a fixed order:** a model with fewer KV heads or a quantized weight format first, then
  context and concurrency caps matched to traffic p99, then fp8 cache, then tensor parallelism, with CPU
  offloading as a last resort for non-interactive work.
- **Measure** the engine's reported block count and peak allocated memory under a production-shaped load
  test and compare with the ledger; a gap means a term you forgot.
- **Interviews:** derive bytes-per-token and walk the four lines for a named model on a named GPU; it is
  the standard test of serving depth.

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

## Interview questions
### What are the four lines of the inference memory ledger, and which one grows with traffic?
- **Weights, KV cache, activation and workspace peaks, runtime overhead plus fragmentation.**
- **Only the KV cache grows with traffic,** because it is bytes per token times tokens in flight; weights
  and overhead are fixed, and activations are bounded by the largest step you allow.

### A deployment passed load tests but OOMs in production. How do you find the missing term?
- **Compare the ledger with reality:** the engine's logged KV size, `max_memory_allocated()` and
  `nvidia-smi` for co-tenant processes.
- **Check the worst step:** did production send longer prompts than the test, during a full decode batch,
  with chunked prefill off or a high token budget?
- **Check additions:** a draft model, LoRA slots or a second process that the startup profile never saw.

### When would you raise the memory fraction, and when would you not?
- **Raise it** when the GPU is dedicated, the activation peak is profiled at your real limits, and KV usage
  is the bottleneck.
- **Do not** past about 0.9–0.95, or when other processes, CUDA graphs or unprofiled peaks need headroom;
  the OOM risk then rises faster than capacity.

### How much memory does a 47B-total, 13B-active MoE model need for weights in bf16?
- **About 94 GB,** because every expert must be resident even though only a few run per token.
- **Active parameters predict compute per token,** not the weight footprint; budgeting from 13B (26 GB)
  means the checkpoint does not load.

### Estimate concurrency for an int8 70B-class model at TP = 2 on 80 GB GPUs with 8k conversations.
- **Per GPU:** 72 GB usable − 35.3 GB weights − ~2 GB reserve ≈ 34.7 GB pool.
- **KV:** 320 KB per token in fp16, 160 KB per GPU, so about 227,000 tokens, about 27 conversations.
- **Levers:** fp8 KV doubles it to about 55; a 4k context cap doubles it again to about 111.

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
