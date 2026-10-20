---
id: grouped-query-attention
module: model-internals
name: Grouped-query attention (GQA)
kind: concept
difficulty: core
est_minutes: 12
status: reviewed
related:
  - model-internals/multi-head-attention
  - model-internals/kv-cache
  - model-internals/llm-architecture
  - serving-inference/kv-cache-memory-estimation
  - serving-inference/tensor-parallelism-at-inference
  - serving-inference/tpot
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
sources:
  - Shazeer, 2019 — Fast Transformer Decoding: One Write-Head is All You Need
  - Ainslie et al., 2023 — GQA: Training Generalized Multi-Query Transformer Models from Multi-Head Checkpoints
  - Touvron et al., 2023 — Llama 2: Open Foundation and Fine-Tuned Chat Models
---

## TL;DR
Grouped-query attention lets several query heads share one key/value head, a middle point between full
multi-head attention (one KV head per query head) and multi-query attention (one for all). It exists
because decoding is bound by reading the KV cache from GPU memory, and that cache scales with the number
of KV heads: going from 32 to 8 shrinks it fourfold at a quality cost small enough that Llama 2 70B,
Llama 3 and Mistral all adopted it.

## Why it exists
During decoding each step processes one new token per sequence. The arithmetic is tiny; the time goes
into reading the model weights and every cached key and value for every layer from high-bandwidth
memory. Time per token is therefore roughly bytes read divided by bandwidth. For a 7B-class model with
full multi-head attention the cache costs about 0.5 MB per token, so 64 concurrent 4k-token sequences
hold ~128 GB of cache against ~14 GB of weights. The cache, not the model, dominates both the memory
budget (how many sequences fit) and the traffic per step (how fast each one advances).

Shazeer's 2019 multi-query attention attacked exactly this: keep all the query heads, but give the layer
a single key head and a single value head shared by all of them. The cache shrinks by a factor of *h*
(32× for a 32-head model). The problem is that it is a real loss of capacity; quality drops measurably,
training can be less stable, and an existing multi-head checkpoint cannot be converted without harm.

Ainslie et al. observed that the extremes were not the only options. With *g* KV heads, each shared by
h/g query heads, the cache shrinks by h/g, and at g = 8 for a 64-head model the quality is close to full
multi-head while the speed is close to multi-query. The naive alternatives all cost more quality per byte
saved: fewer layers cuts capacity everywhere, smaller head dimensions weaken every attention pattern,
and cache quantization is orthogonal and can be stacked on top. GQA targets the one factor that was
mostly redundancy, since the key and value projections of neighbouring heads in a trained model are
similar enough to share.

## How it works
### The grouping
Take *h* query heads and *g* KV heads with *h* divisible by *g*. The key and value projection matrices
shrink from `[d, h·d_k]` to `[d, g·d_k]`; the query projection and `W_O` are unchanged. In the forward
pass, **K** and **V** come out as `[B, g, n, d_k]`, and query head *i* attends to KV head `i // (h/g)`.
Implementations either expand **K** and **V** to `[B, h, n, d_k]` by repeating each KV head h/g times
(the `repeat_kv` helper in Hugging Face) or, in fused kernels, index the shared head directly without
copying. The attention math per head is unchanged. For Llama-3-8B (h = 32, g = 8, d_k = 128, d = 4096)
the K and V projections hold 8.4M parameters per layer instead of 33.6M, about 0.8B fewer parameters
across 32 layers than an all-MHA layout of the same width would need.

### Cache arithmetic
Per token, the cache holds `2 × n_layers × g × d_k × bytes_per_element`. For 32 layers, d_k = 128 and
fp16:

```
MHA  g = 32 : 2 × 32 × 32 × 128 × 2 = 524,288 B = 512 KB per token
GQA  g = 8  : 2 × 32 ×  8 × 128 × 2 = 131,072 B = 128 KB per token
MQA  g = 1  : 2 × 32 ×  1 × 128 × 2 =  16,384 B =  16 KB per token
```

At 64 sequences of 8k tokens that is 256 GB, 64 GB and 8 GB respectively. The GQA workload fits on one
80 GB card with room for weights; the MHA one does not fit on three.

### Decode speed
Bytes read per decode step ≈ weights + the cache of every sequence in the batch. At batch 64 and 4k
average length with an 8B fp16 model: ~16 GB of weights plus ~128 GB of MHA cache, against 16 + 32 GB
with GQA. Roughly three times fewer bytes per step means up to roughly three times the decode throughput
before compute becomes the limit; treat the factor as illustrative, since real engines overlap and batch
differently. A second, subtler effect: in decode attention every K/V vector loaded from HBM is used by
h/g query heads instead of one, so the attention kernel's arithmetic intensity rises by the group size.
MHA decode attention does about one multiply-add per cached element loaded, which no GPU can feed
from memory; GQA moves it partway toward the compute-bound regime. Prefill is unaffected: it is compute-bound and the attention FLOPs are identical because the
queries are not shared.

### Uptraining existing checkpoints
The GQA paper's second contribution was conversion: take a multi-head checkpoint, **mean-pool** the key
and value projection matrices of the heads in each group into one, then continue pre-training for about
5% of the original compute. Mean pooling is used because it preserves the group's average behaviour
better than keeping one head or re-initializing. That is why GQA could be retrofitted onto already-trained
models; newer families (Llama 3, Mistral) simply train with it from the start.

### Interaction with tensor parallelism
Heads are the unit sharded across GPUs. With g = 8 KV heads on 8 GPUs each GPU holds one KV head and the
four query heads that use it: ideal. With a tensor-parallel degree larger than *g*, KV heads must be
replicated so each GPU has the one its query heads need, and the per-GPU cache saving shrinks
accordingly. Serving engines handle the replication, but the capacity arithmetic changes.

### Beyond GQA
Multi-head latent attention (DeepSeek-V2) projects keys and values into a low-rank latent vector per
token and caches only that, reconstructing K and V on use; it reports a smaller cache than GQA at
comparable or better quality, a vendor claim worth verifying per model. Cache quantization to 8-bit or
4-bit stacks multiplicatively with any head layout, and sliding-window attention bounds cache *length*
rather than width.

## Example
The first half shows how query heads map onto shared KV heads with a `repeat_kv` expansion; the second
computes the cache table for the three layouts from a config.

```python
import torch
import torch.nn.functional as F

def repeat_kv(x, n_rep):                      # [B, g, n, dk] -> [B, g * n_rep, n, dk]
    B, g, n, dk = x.shape
    if n_rep == 1:
        return x
    return x[:, :, None].expand(B, g, n_rep, n, dk).reshape(B, g * n_rep, n, dk)

B, n, dk, h, g = 1, 5, 128, 32, 8
q = torch.randn(B, h, n, dk)
k, v = torch.randn(B, g, n, dk), torch.randn(B, g, n, dk)      # only 8 KV heads are ever cached
k_full, v_full = repeat_kv(k, h // g), repeat_kv(v, h // g)   # query head i uses KV head i // 4
out = F.scaled_dot_product_attention(q, k_full, v_full, is_causal=True)
print(out.shape, torch.equal(k_full[:, 0], k_full[:, 3]), torch.equal(k_full[:, 3], k_full[:, 4]))
# torch.Size([1, 32, 5, 128]) True False   -> heads 0..3 share KV head 0; head 4 starts group 1

def kv_bytes_per_token(layers, kv_heads, head_dim, dtype_bytes=2):
    return 2 * layers * kv_heads * head_dim * dtype_bytes

for name, kv_heads in (("MHA 32 kv heads", 32), ("GQA  8 kv heads", 8), ("MQA  1 kv head ", 1)):
    per_tok = kv_bytes_per_token(32, kv_heads, 128)
    print(f"{name}: {per_tok // 1024:4d} KB/token, {per_tok * 8192 * 64 / 2**30:6.0f} GiB for 64 x 8k")
# MHA 32 kv heads:  512 KB/token,    256 GiB for 64 x 8k
# GQA  8 kv heads:  128 KB/token,     64 GiB for 64 x 8k
# MQA  1 kv head :   16 KB/token,      8 GiB for 64 x 8k
```

## Trade-offs
- Cache size and decode bytes drop by h/g, paid for with a small quality loss; the trade moves toward
  MQA when memory is the hard limit (edge devices, extreme batch) and back toward full MHA for small
  models at short context where the cache was never the constraint.
- Fewer K/V parameters save a little memory and prefill compute, but the attention FLOPs are
  unchanged because queries are not shared, so GQA is nearly invisible in time-to-first-token and only
  shows in per-token decode latency and throughput.
- Uptraining lets a team retrofit GQA onto a model they already have, but it costs around 5% of
  pre-training compute plus the data pipeline; for almost everyone the right move is to pick a model
  that was trained with GQA.
- The KV-head count sets the finest tensor-parallel split without replication, so g = 8 fits 8-GPU
  nodes neatly and awkwardly fits anything else.

## Pitfalls
- Using `num_attention_heads` instead of `num_key_value_heads` in a capacity estimate; the symptom is a
  cache budget four times too large, and a cluster sized or rejected on the wrong number.
- Expecting GQA to cut time-to-first-token; the symptom is an unchanged TTFT after switching models
  while only throughput and per-token latency improved.
- Materializing `repeat_kv` copies in a custom attention path at prefill; the symptom is transient
  activation memory as large as MHA even though the cache itself is small.
- Converting an MHA checkpoint to a GQA layout by slicing or averaging heads without the uptraining
  step; the symptom is a sharp perplexity jump on the first evaluation that only continued
  pre-training (the paper's ~5% uptraining) recovers.
- Tensor-parallel degree larger than the KV-head count; the symptom is per-GPU cache memory that does
  not shrink as expected when you add GPUs.

## When to use
When choosing a model to serve at long context or high concurrency, read `num_key_value_heads` before
reading the benchmark table, and compute cache-per-token for your sequence lengths; two models with
equal quality on your evaluation can differ by four in how many users one GPU serves. Prefer GQA models
for chat and agent workloads where decode dominates; the difference matters less for short-prompt
classification where prefill dominates. If you train or fine-tune, keep the layout the checkpoint was
trained with. Measure tokens per second per GPU at your target batch, not single-stream latency, because
that is where the saving appears. In interviews, derive the cache formula, explain why decode speed
follows bytes rather than FLOPs, and place MHA, GQA and MQA on that line.

## Role lens
- **Engineer:** Plugs `num_key_value_heads` into the cache estimator, sets batch and context limits in
  the serving engine from it, and checks that the kernel path indexes shared heads instead of copying.
- **Architect:** Decides model families and GPU counts with the KV-head count as a first-class input,
  since it fixes both cache capacity and how evenly the model shards.
- **FDE:** Shows a customer why the GQA model serves four times their concurrent users on the same
  hardware as the MHA model they benchmarked, with the per-token bytes as the evidence.

## Check yourself
**Q:** Two 7B-class models score identically on your evaluation. One uses full multi-head attention with
32 KV heads, the other GQA with 8. You serve 4k-token chats on one 80 GB GPU. Quantify the difference and
say what else you would check.
**A:** Both have 32 layers and head_dim 128, so the cache per token is 2 × 32 × 32 × 128 × 2 bytes
= 512 KB for MHA and 128 KB for GQA. A 4k chat costs 2 GB versus 0.5 GB. After ~14 GB of fp16 weights,
roughly 66 GB remain, so about 30 MHA chats fit against about 130 GQA chats, before fragmentation and
activations. Per decode step, bytes read drop from weights plus 2 GB per active chat to weights plus
0.5 GB, so at equal batch the GQA model generates tokens faster, and at equal memory it serves four times
the concurrency. I would also check that the two evaluations covered long-context behaviour, that the
serving engine actually uses a GQA-aware kernel, and that my tensor-parallel plan divides 8 KV heads
evenly.

## Quiz
### Q: What does grouped-query attention primarily reduce?
- [ ] The number of query heads and therefore attention FLOPs
- [x] The number of key/value heads, and with them the KV cache size and bytes read per decode step
- [ ] The number of transformer layers needed for a given quality
- [ ] The size of the vocabulary embedding
> Queries stay at full head count; only keys and values are shared across groups. The win is memory and
> bandwidth during decoding, not arithmetic, which is why prefill speed is essentially unchanged.

### Q: Llama-3-8B has 32 query heads and 8 KV heads. How many query heads share each KV head?
- [ ] 1
- [ ] 2
- [x] 4
- [ ] 8
> Group size is 32 / 8 = 4: query heads 0–3 use KV head 0, 4–7 use KV head 1, and so on. The cache is
> therefore a quarter of what full multi-head attention would need.

### Q: Why does GQA give almost no improvement to time-to-first-token?
- [ ] Because the prompt is processed without attention
- [ ] Because the cache is only created after the first token
- [x] Because prefill is compute-bound and the attention FLOPs are unchanged when only K and V are shared
- [ ] Because the output projection dominates prefill time
> Prefill multiplies large matrices and is limited by arithmetic, not by reading the cache. Sharing K/V
> heads changes how many bytes decode must read per step, which is where GQA's gain appears.
