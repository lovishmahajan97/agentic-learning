---
id: kv-cache-memory-estimation
module: serving-inference
name: KV-cache memory estimation
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - model-internals/kv-cache
  - model-internals/grouped-query-attention
  - serving-inference/gpu-memory-optimization
  - serving-inference/kv-cache-paging-and-eviction
  - serving-inference/batching-and-concurrency-limits
  - infra-capacity-economics/gpu-sizing
  - infra-capacity-economics/capacity-planning
sources:
  - Pope et al., 2022 — Efficiently Scaling Transformer Inference
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Official docs: vLLM
---

## TL;DR
KV-cache memory is the number that turns "we have an 80 GB GPU" into "we can serve N concurrent requests
of length L". It is a short multiplication you can do from a model's config file: bytes per token equals
2 × layers × KV heads × head dimension × bytes per element, times tokens, times concurrent sequences.
Learning to do it quickly, and knowing what the formula leaves out, is how you avoid both out-of-memory
surprises and paying for GPUs that sit half empty.

## Why it exists
Weights are the visible cost of a model; the cache is the invisible one. A team that budgets 16 GB for an
8B fp16 model and assumes the remaining 64 GB is "headroom" has not budgeted at all, because every
running request pins a cache that grows with its context, and the engine refuses or preempts requests
once that pool is exhausted. The failure is silent until load arrives: nothing crashes at low traffic, and
then under real concurrency the server either OOMs, stalls admissions, or evicts half-finished generations
and recomputes them, which shows up as latency collapse.

The naive alternative, "try it and see", fails because the right answer depends on things you do not
observe until production: the distribution of prompt lengths, output lengths and concurrency, and how they
move together. Load tests with synthetic 500-token prompts say nothing about the day a customer pastes a
40-page contract. The other naive alternative, "pick the biggest GPU", fails on cost and often still does
not fit: a 70B model's cache at 128k context on a modest batch exceeds any single card. The estimate exists
so you can reason forward from a config file and a traffic model to a GPU count before buying anything,
and backward from an OOM to the parameter that caused it.

## How it works
### Bytes per token
Each transformer layer stores one key vector and one value vector per KV head per token. So:

```
bytes_per_token = 2 × n_layers × n_kv_heads × head_dim × bytes_per_element
```

The factor 2 is K and V. `n_kv_heads` is the number of key/value heads, not attention heads: with
multi-head attention they are equal, with grouped-query attention (GQA) KV heads are fewer (8 is common),
and with multi-query attention it is 1. `head_dim` is usually `hidden_size / n_attention_heads`.
`bytes_per_element` is 2 for fp16/bf16, 1 for fp8 or int8 cache. All of these come from the model's
config file (`num_hidden_layers`, `num_key_value_heads`, `hidden_size`, `num_attention_heads`).

Multi-head latent attention (DeepSeek-V2/V3 and derivatives) replaces per-head K and V with one compressed
latent vector per layer per token plus a small decoupled RoPE key, so the formula becomes
1 × layers × (latent_dim + rope_dim) × bytes (for DeepSeek-V3: 61 × (512 + 64) × 2 ≈ 70 KB in bf16, versus
several MB for an equivalent MHA layout); read the architecture before applying the standard formula.

### From per-token to per-GPU
```
cache_per_request = bytes_per_token × (prompt_tokens + max_output_tokens)
pool_bytes        = gpu_memory × utilization_fraction − weight_bytes − activation_workspace
max_concurrency   ≈ pool_bytes / cache_per_request
```

Three refinements matter in practice:

1. **Weights.** Parameters × bytes per parameter: 8B × 2 = 16 GB in fp16, 8 GB at int8, ~4.5 GB at 4-bit
   (plus a little for scales). Quantizing weights frees cache space; that is often its biggest payoff.
2. **Activation and workspace.** Engines profile a worst-case forward pass at startup (the largest
   prefill batch they will allow) and reserve that memory before sizing the pool. CUDA graphs, sampling
   buffers and the framework's own allocator overhead also take a few GB. vLLM exposes the knob as
   `gpu_memory_utilization` (default 0.9) and prints the number of cache blocks it managed to allocate;
   that printed number, times block size, is the real pool in tokens.
3. **Sequence length is a distribution.** Use the p95 or p99 of (prompt + output), not the mean, to decide
   what fits without preemption, and use the mean to estimate steady-state concurrency. Paged allocation
   means a request only consumes what it has actually generated so far, so admission can be optimistic
   about the mean while the max context length bounds the worst case.

### Running the numbers for common shapes
Illustrative, computed from public config values:
- 32 layers, 32 KV heads, head_dim 128, fp16 (a Llama-2-7B-class MHA model): 2 × 32 × 32 × 128 × 2 =
  524,288 B ≈ 512 KB per token. 8k context ≈ 4 GB per request.
- 32 layers, 8 KV heads, head_dim 128, fp16 (a Llama-3-8B-class GQA model): 2 × 32 × 8 × 128 × 2 =
  131,072 B = 128 KB per token. 8k context ≈ 1 GB per request. Same size class, 4× less cache: this is why
  GQA exists.
- 80 layers, 8 KV heads, head_dim 128, fp16 (a 70B-class GQA model): 2 × 80 × 8 × 128 × 2 = 327,680 B =
  320 KB per token. 8k context ≈ 2.5 GB per request; 128k context ≈ 40 GB for one request.

### Reading it backwards
If the engine reports N cache blocks of 16 tokens, the pool holds 16N tokens. Divide by your p50 sequence
length for expected concurrency, by your `max_model_len` for guaranteed concurrency. If the gap between
those two is large, the scheduler will run many requests at once and preempt when the tail arrives; that
is a policy choice, not an error, but you should make it consciously.

## Example
A helper you can run against any Hugging Face config to answer "how many fit" before touching a GPU.

```python
import json

def kv_bytes_per_token(cfg, cache_bytes=2):
    n_heads = cfg["num_attention_heads"]
    n_kv = cfg.get("num_key_value_heads", n_heads)        # GQA/MQA; defaults to MHA
    head_dim = cfg.get("head_dim") or cfg["hidden_size"] // n_heads
    return 2 * cfg["num_hidden_layers"] * n_kv * head_dim * cache_bytes

def capacity(cfg, params_b, gpu_gb=80, util=0.90, weight_bytes=2, workspace_gb=4,
             p50_seq=3000, max_seq=8192, cache_bytes=2):
    per_tok = kv_bytes_per_token(cfg, cache_bytes)
    weights = params_b * 1e9 * weight_bytes
    pool = gpu_gb * 1e9 * util - weights - workspace_gb * 1e9
    return {
        "kv_kb_per_token": per_tok / 1024,
        "pool_gb": pool / 1e9,
        "pool_tokens": int(pool // per_tok),
        "concurrency_at_p50": int(pool // (per_tok * p50_seq)),
        "concurrency_at_max": int(pool // (per_tok * max_seq)),
    }

llama3_8b_like = {"num_hidden_layers": 32, "num_attention_heads": 32,
                  "num_key_value_heads": 8, "hidden_size": 4096}
print(json.dumps(capacity(llama3_8b_like, params_b=8), indent=1))
# -> ~128 KB/token, pool ≈ 52 GB ≈ 400k tokens, ~132 requests at a 3k p50, ~48 at an 8k max.
print(capacity(llama3_8b_like, 8, cache_bytes=1)["concurrency_at_p50"])   # fp8 cache: ~2x
print(capacity(llama3_8b_like, 8, weight_bytes=0.5)["pool_gb"])           # 4-bit weights: +12 GB pool
```

The last two lines are the two cheapest levers when the answer is "not enough": quantize the cache to
double token capacity, or quantize the weights to enlarge the pool. Compare the printed `pool_tokens`
with the block count the engine logs at startup; if they disagree by more than ~10%, the workspace
estimate is wrong for that engine and model and you should trust the log.

## Trade-offs
- Sizing to the maximum context guarantees no preemption but leaves most of the pool idle on typical
  traffic; sizing to the median packs more users but accepts preemption on the tail.
- Quantizing the cache to 8-bit doubles token capacity at a small, task-dependent quality cost; the trade
  flips against it for workloads where exact long-range recall is the product.
- Choosing a GQA model shrinks the cache several-fold and raises concurrency, but constrains model choice;
  a stronger MHA model may still win when quality per request matters more than requests per GPU.
- Raising `gpu_memory_utilization` toward 1.0 enlarges the pool but removes headroom for activation
  spikes and other processes; it flips to dangerous when the GPU is shared or prompts are bursty.

## Pitfalls
- Using the attention-head count instead of the KV-head count; symptom is a capacity estimate four or
  eight times too pessimistic for GQA models, and a purchase of GPUs you did not need.
- Forgetting that output tokens also need cache; symptom is OOM or preemption on long generations even
  though every prompt fit comfortably in the estimate.
- Trusting the formula over the engine's startup log; symptom is a pool 10–20% smaller than planned
  because activation workspace and allocator overhead were not budgeted.
- Estimating with mean sequence length and promising that concurrency; symptom is latency collapse the
  first time a batch of long-context requests arrives together.
- Applying the standard formula to a model with latent or sliding-window attention; symptom is an estimate
  off by a large factor in either direction, because the cache layout is different.

## When to use
Do this arithmetic at four moments: when choosing a model and GPU before any code exists; when setting
`max_model_len`, `max_num_seqs` and utilization flags for a deployment; when an OOM or preemption storm
appears in production; and when someone asks for a longer context window or a bigger model on the same
hardware. Keep the per-token number for every model you serve in your head or in a table, because it
answers most capacity questions in seconds. Measure actual cache utilization and preemption counts in
production to validate the estimate against real traffic. In interviews, deriving bytes per token from a
config, then turning it into a concurrency number with the weights subtracted, is the canonical serving
question, and the follow-up is always "what would you change if it does not fit".

## Role lens
- **Engineer:** Computes the pool before deployment, sets context and concurrency flags from it, and
  checks the engine's logged block count against the estimate on every model change.
- **Architect:** Owns the capacity model that links context-window policy, model choice (GQA or not), GPU
  tier and replica count, and reviews any request to raise the context limit against it.
- **FDE:** Turns a customer's "we need 200 concurrent users with 50-page documents" into a per-token
  number, a GPU count and a cost, and shows the trade when it does not fit their budget.

## Check yourself
**Q:** A customer wants a 70B-class GQA model (80 layers, 8 KV heads, head_dim 128) in fp16 to serve 40
concurrent sessions that average 12k tokens of context, on 80 GB GPUs. How many GPUs, and what would you
propose if they only have two?
**A:** Cache per token is 2 × 80 × 8 × 128 × 2 = 320 KB. Per session at 12k tokens: ~3.84 GB. Forty
sessions: ~154 GB of cache. Weights in fp16 are ~140 GB, which already needs at least two 80 GB GPUs with
tensor parallelism, and at 90% utilization those two offer ~144 GB, so after weights and workspace there
is almost nothing left for cache: the model loads but serves one or two sessions. Four GPUs give ~288 GB
usable, ~140 GB after weights and workspace, about 36 sessions at the average and no tail headroom; six
GPUs (~280 GB of cache) would be comfortable. With only two GPUs: quantize the weights to 8-bit (~70 GB,
leaving ~70 GB) and the cache to 8-bit (halving per-session cost to ~1.9 GB), which fits ~35 sessions
at the average, then cap context per session at 16k, enable paging and prefix caching, and run a quality
eval on the quantized configuration before promising anything. A strong answer notes that decode at 40 sessions
will have higher TPOT because every step reads all 40 caches.

## Quiz
### Q: Which model-config fields do you need to compute KV-cache bytes per token?
- [ ] Vocabulary size, hidden size and number of layers
- [x] Number of layers, number of KV heads, head dimension and the cache element size
- [ ] Number of parameters and the context length
- [ ] Number of attention heads, MLP width and dropout rate
> The cache stores one K and one V vector per layer per KV head per token, so those dimensions plus the
> bytes per element are exactly what is needed. Vocabulary, parameter count and MLP width do not appear
> in the cache at all.

### Q: Two 8B models differ only in that one has 32 KV heads and the other 8. What is the effect on serving capacity?
- [ ] No difference; cache size depends on parameter count
- [ ] The 8-KV-head model needs 4× more cache because each head is larger
- [x] The 8-KV-head model needs about one quarter of the cache per token, so roughly 4× more sequences fit
- [ ] The 32-KV-head model is faster to decode because attention is more parallel
> Cache bytes scale linearly with the number of KV heads. Grouped-query attention shares each K/V head
> across several query heads precisely to cut this number, which is why GQA models serve far more
> concurrent requests per GPU.

### Q: Your estimate says 60 requests fit, but the engine's startup log allocates blocks for only 45. What is the most likely cause?
- [ ] The engine is using a larger block size than you assumed
- [x] Activation workspace, CUDA graphs and allocator overhead reduce the pool below "VRAM minus weights"
- [ ] The engine stores keys and values in separate pools
- [ ] Your formula double-counted the factor of 2 for K and V
> Engines profile a worst-case forward pass and reserve memory for activations, graphs and sampling before
> sizing the cache pool. Block size does not change total pool bytes, and the K/V factor is correct. Trust
> the log and tighten the workspace term in the estimate.
