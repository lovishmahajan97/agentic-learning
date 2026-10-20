---
id: kv-cache
module: model-internals
name: KV cache
kind: concept
difficulty: core
est_minutes: 14
related:
  - model-internals/self-attention
  - model-internals/grouped-query-attention
  - model-internals/context-window
  - serving-inference/kv-cache-memory-estimation
  - serving-inference/pagedattention
  - serving-inference/prefix-caching
sources:
  - Vaswani et al., 2017 — Attention Is All You Need
  - Pope et al., 2022 — Efficiently Scaling Transformer Inference
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
---

## TL;DR
A decoder-only transformer generates one token at a time, and every new token must attend to every
earlier token. The KV cache stores the key and value vectors already computed for earlier positions so
each step only computes attention for the new token instead of recomputing the whole prefix. It turns
generation from quadratic-per-token into linear-per-token at the cost of GPU memory that grows with
sequence length, which is why memory, not compute, usually limits how many requests a server can run.

## Why it exists
Autoregressive generation is a loop: feed the prompt, get a token, append it, feed everything again.
Without caching, step *t* re-runs the full forward pass over all *t* tokens, so producing *n* tokens costs
roughly 1 + 2 + … + n = O(n²) token-forward-passes. For a 4,000-token answer that is eight million
token-passes instead of four thousand.

Look at what attention actually needs. For the newest token, the query vector **q** is new, but the keys
**K** and values **V** of every earlier position are pure functions of those earlier tokens and the model
weights. In a causal decoder they never change once computed, because position *i* cannot see position
*j > i*. Recomputing them is therefore wasted work with an identical result. The cache simply memoizes
that work per layer.

The naive alternative, "just recompute, GPUs are fast", fails for two reasons. First, the quadratic cost
makes long outputs unaffordable. Second, and less obvious, recomputation reads the whole weight matrix
for every prefix token every step, so it is memory-bandwidth bound as well as FLOP bound. The cache
converts that into reading a much smaller per-token slice.

## How it works
### What is stored
In each transformer layer, the attention block projects each token's hidden state into a key and a
value: `k = x·W_K`, `v = x·W_V`. For a token at position *i*, these depend only on the hidden state at
*i* in that layer, which in turn depends only on tokens ≤ *i*. So after a token has been processed, its
**k** and **v** for every layer can be stored and never recomputed.

### The two phases
1. **Prefill.** The whole prompt is processed in one batched forward pass. This is compute-bound
   (large matrix multiplications) and it populates the cache with K and V for every prompt position in
   every layer.
2. **Decode.** One new token per step. The model computes **q, k, v** for just that token, appends its
   **k** and **v** to the cache, and attention is `softmax(q·Kᵀ/√d)·V` over the cached K and V. Only one
   row of queries is involved, so the step is dominated by *reading* the cache and the weights, not by
   arithmetic. Decode is memory-bandwidth bound, which is why tokens/second per request barely improves
   with a faster GPU if its bandwidth is similar.

### How big it is
Per token, the cache holds one key and one value vector per layer per KV head:

```
bytes_per_token = 2 (K and V) × n_layers × n_kv_heads × head_dim × bytes_per_element
```

For a Llama-2-7B-style model (32 layers, 32 heads, head_dim 128, fp16): 2 × 32 × 32 × 128 × 2 = 524,288
bytes ≈ 0.5 MB per token. A 4k-token sequence is ~2 GB; 32 concurrent such sequences would need ~64 GB,
which is the whole card. This is the arithmetic behind the sentence "LLM serving is memory-bound".
Grouped-query attention shrinks `n_kv_heads` (8 instead of 32 in Llama-3-8B), cutting the cache 4×, and
that is exactly why GQA exists.

### Why it is per layer
Each layer has its own W_K and W_V and operates on a different hidden state, so the cache is a 4-D
tensor `[layer, position, kv_head, head_dim]` for K and the same for V. There is no sharing across layers
in a standard transformer.

### Interaction with batching
Because each request's cache grows at a different rate and finishes at a different time, naive
contiguous allocation fragments GPU memory badly. PagedAttention (vLLM) stores the cache in fixed-size
blocks addressed through a page table, the same trick operating systems use for virtual memory, which
lets a server pack many more sequences and share prompt prefixes between requests.

## Example
A minimal decode loop with and without a cache, using Hugging Face Transformers. The cached version
feeds only the new token each step and passes `past_key_values` back in.

```python
import torch, time
from transformers import AutoModelForCausalLM, AutoTokenizer

name = "Qwen/Qwen2.5-0.5B"          # any small causal LM
tok = AutoTokenizer.from_pretrained(name)
model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.float16).cuda().eval()
ids = tok("The KV cache exists because", return_tensors="pt").input_ids.cuda()

@torch.no_grad()
def generate(use_cache, steps=64):
    x, past = ids, None
    out = []
    for _ in range(steps):
        if use_cache:
            r = model(input_ids=x if past is None else x[:, -1:], past_key_values=past, use_cache=True)
            past = r.past_key_values              # grows by one position per step
        else:
            r = model(input_ids=x, use_cache=False)  # re-processes the whole prefix every step
        nxt = r.logits[:, -1].argmax(-1, keepdim=True)
        x = torch.cat([x, nxt], dim=1); out.append(nxt.item())
    return out

for flag in (False, True):
    torch.cuda.synchronize(); t = time.time(); generate(flag); torch.cuda.synchronize()
    print(f"use_cache={flag}: {time.time()-t:.2f}s")
# Expect the cached loop to be several times faster, with the gap widening as steps grows.
# Inspect size: past[0][0].shape -> [batch, n_kv_heads, seq_len, head_dim] for layer 0 keys.
```

## Trade-offs
- Compute drops from O(n²) to O(n) per generation, but memory grows linearly with sequence length and
  with concurrent requests, so the binding constraint moves from FLOPs to VRAM and bandwidth.
- Longer context windows multiply cache size directly; a 128k-token window is only usable because of
  GQA, quantized caches and paging, each of which costs some quality or engineering complexity.
- Caching the prompt prefix across requests (prefix caching) saves prefill compute for shared system
  prompts, but only when prefixes are byte-identical and the cache can be kept resident between calls.
- Quantizing the cache to 8-bit (or 4-bit) halves memory and raises batch size, at a measurable but
  usually small accuracy cost that must be evaluated per model and task.

## Pitfalls
- Estimating serving capacity from model weights alone and forgetting the cache; the symptom is
  out-of-memory errors or aggressive request preemption once traffic has long contexts.
- Assuming a faster GPU will speed decode proportionally; decode is bandwidth-bound, so tokens/second
  tracks memory bandwidth more than peak FLOPs.
- Editing or truncating conversation history in the middle and reusing the old cache; cached K and V
  encode absolute positions and earlier content, so any change before position *i* invalidates
  everything from *i* onward.
- Benchmarking "time per token" on a single request and extrapolating to a batch; with batching, the
  cache of all concurrent requests competes for the same memory bandwidth.

## When to use
You do not choose whether to use a KV cache; every production inference engine does. What you choose is
how to live within its memory budget: the maximum context length you allow, the batch size and
concurrency limits, whether to enable prefix caching for shared system prompts, whether to quantize the
cache, and which model (GQA vs full multi-head attention) fits your hardware. Reach for the formula above
whenever someone asks "how many concurrent users can this GPU serve" or "why did latency collapse when
we raised the context limit". In interviews, being able to derive bytes-per-token and connect it to GQA,
PagedAttention and the prefill/decode split is the expected depth for an AI engineer.

## Role lens
- **Engineer:** Sets `max_model_len`, batch limits and prefix-caching flags in vLLM or SGLang, and debugs
  OOMs by computing cache size rather than guessing.
- **Architect:** Owns the capacity model: context window policy, GPU selection and concurrency targets all
  follow from cache arithmetic and the prefill/decode cost split.
- **FDE:** Explains to a customer why their 100-page-document use case needs a different GPU tier or a
  retrieval step, using the per-token memory number as the argument.

## Check yourself
**Q:** A team serves a 32-layer, 32-head, head-dim-128 model in fp16 on an 80 GB GPU; weights take 14 GB.
They want 64 concurrent chats averaging 6k tokens. Does it fit, and what are their options if not?
**A:** Cache per token is 2 × 32 × 32 × 128 × 2 bytes ≈ 0.5 MB. Per chat at 6k tokens that is ~3 GB; 64
chats need ~192 GB, far beyond the ~66 GB left after weights. Options, in rough order of cost: cap
concurrency or context (about 20 chats fit); switch to a GQA model with 8 KV heads, cutting the cache 4×
so ~80 chats fit; quantize the cache to 8-bit for another 2×; use paged attention so memory is not lost to
fragmentation; add prefix caching if chats share a long system prompt; or shard across more GPUs. A strong
answer also notes decode is bandwidth-bound, so higher concurrency raises per-request latency.

## Quiz
### Q: Why does the KV cache store keys and values but not queries?
- [ ] Queries are too large to store efficiently
- [x] Only the newest token's query is needed at each step, while past keys and values are reused by every later token
- [ ] Queries are recomputed because they change after each layer's normalization
- [ ] Storing queries would break causal masking
> Attention for the new token needs its own query against all previous keys and values. Earlier queries
> were only needed when those tokens were generated, so caching them would be wasted memory.

### Q: Roughly how does KV-cache memory scale with sequence length and batch size?
- [ ] Constant in sequence length, linear in batch size
- [ ] Quadratic in sequence length, constant in batch size
- [x] Linear in sequence length and linear in batch size
- [ ] Logarithmic in both, because of paging
> Each token in each sequence adds one K and one V vector per layer, so memory is proportional to
> tokens × concurrent sequences. Paging reduces waste from fragmentation but not the total needed.

### Q: Which change reduces KV-cache size without changing sequence length or precision?
- [ ] Increasing the number of transformer layers
- [ ] Switching from RoPE to learned positional embeddings
- [x] Using grouped-query attention with fewer KV heads
- [ ] Enabling speculative decoding
> Cache size is proportional to the number of KV heads; GQA shares each K/V head across several query
> heads, so Llama-3-8B's 8 KV heads need a quarter of the cache of a 32-head layout. Speculative decoding
> changes compute per step, not cache per token.
