---
id: multi-head-attention
module: model-internals
name: Multi-head attention
kind: concept
difficulty: core
est_minutes: 12
status: fact-checked
related:
  - model-internals/self-attention
  - model-internals/attention-mechanism
  - model-internals/grouped-query-attention
  - model-internals/flashattention
  - model-internals/kv-cache
  - model-internals/transformer
  - serving-inference/tensor-parallelism-at-inference
sources:
  - Vaswani et al., 2017 — Attention Is All You Need
  - Michel et al., 2019 — Are Sixteen Heads Really Better than One?
  - Bhojanapalli et al., 2020 — Low-Rank Bottleneck in Multi-head Attention Models
---

## TL;DR
Multi-head attention runs several attention operations in parallel, each in its own lower-dimensional
projection of the input, and concatenates the results. One softmax can express only one weighting over
the sequence per position; with *h* heads a position can look in *h* places for *h* different reasons at
once. Because the model width is split across heads (d_k = d_model / h), the parameter count and FLOPs
match a single wide head, while the attention-probability memory and the natural unit for sharding both
scale with *h*.

## Why it exists
A single attention head produces, for each query position, one probability distribution over the
sequence and one blended output. Consider the verb in "The keys that the engineer lost were on the
table": to represent it well the layer wants to look at "keys" for number agreement, at "engineer" for
who did the losing, and at "table" for where things ended up. Those are different relations with
different relevant positions, and one softmax asked to serve all three at once averages them into a
blur.

There is a second limitation. A dot product in a single d_model-dimensional space tests one notion of
similarity. "Is this the subject of my clause" and "is this the token right before me" are different
comparisons that want different subspaces.

The naive fixes are poor. Stacking more layers so each layer picks one relation serializes work that
could be parallel and deepens the network. Widening d_k does not help at all, because one softmax still
yields one weighting. The transformer paper's answer was to project the input into *h* = 8 subspaces of
64 dimensions each, attend independently in each, concatenate the eight 64-dimensional outputs back to
512, and mix them with an output projection. Since each head's score computation costs n²·d_k and
h·d_k = d_model, the total matches a single full-width head, and the paper found that several heads with
the same parameter budget beat one head measurably. The design has survived unchanged in its essentials.

## How it works
### Shapes
From input **X** `[n, d]`, head *i* uses its own slices of the projection matrices:
`Q_i = X·W_Q^i`, `K_i = X·W_K^i`, `V_i = X·W_V^i`, each `[n, d_k]`, then
`head_i = softmax(Q_i·K_iᵀ / √d_k)·V_i`. The outputs are concatenated into `[n, h·d_k]` and multiplied by
`W_O` of shape `[h·d_k, d]`. In code the per-head matrices are not separate: one fused `W_QKV` of shape
`[d, 3d]` is applied, the result is reshaped from `[B, n, 3d]` to `[B, 3, h, n, d_k]`, and a batched matmul
over the head axis does all heads in one kernel. The heads are a *view* of one big matmul, not eight
small modules.

Typical sizes: d_k = 64 in the 2017 paper; 128 in Llama-2-7B (4096 / 32), Llama-3-8B and most current
open models, partly because fused attention kernels are tuned for head dimensions of 64, 128 and 256.

### Why the output projection
Concatenation only lays the heads' outputs side by side. Without `W_O`, head *i* could write only into
dimensions `i·d_k … (i+1)·d_k` of the residual stream, tying each head to a fixed slot forever. `W_O`
lets information from all heads combine and land anywhere in the stream, and it is where a head's
contribution can be scaled up or down.

### Parameter and FLOP accounting
Per layer, `W_Q`, `W_K`, `W_V` and `W_O` are each `d × d`, giving 4d² parameters regardless of *h*. Score
FLOPs are h × 2·n²·d_k = 2·n²·d, again independent of *h*. Three things do change with *h*:
- the materialized attention-probability tensor, `[B, h, n, n]`, grows linearly with *h*; at h = 32,
  n = 8,192, fp16 it is about 4.3 GB per layer per sequence, which is why fused kernels that never
  materialize it matter;
- the rank of each head's attention pattern is bounded by d_k, so more heads means each is individually
  weaker;
- the KV cache per token per layer is 2·(number of KV heads)·d_k, which equals 2d for full multi-head
  attention and is only reduced when KV heads are shared, the subject of grouped-query attention.

### What heads learn
Interpretability work finds recognizable specialists: previous-token heads, heads that track syntactic
roles, heads that attend to delimiters or the first token as a sink, and induction heads that find an
earlier copy of the current token and attend to what followed it. It also finds redundancy: Michel et
al. showed that a large fraction of heads in trained translation and BERT models can be removed at test
time with little loss. That redundancy is part of why sharing keys and values across heads (GQA) costs
so little quality.

### Choosing h and d_k
More heads means more distinct relations per layer; smaller d_k means each head's score matrix has lower
rank and sharper patterns become harder to express. Hardware adds constraints: d_k should be a value the
attention kernels are tuned for, and *h* should be divisible by the tensor-parallel degree, because heads
are the natural unit to shard across GPUs with no communication inside the attention block.

## Example
A complete multi-head attention module. The loop at the bottom shows that changing *h* from 1 to 64 leaves
the parameter count at exactly 4·512² = 1,048,576 and changes only the shape of the attention tensor.

```python
import torch, torch.nn as nn

class MHA(nn.Module):
    def __init__(self, d, h):
        super().__init__()
        assert d % h == 0
        self.h, self.dk = h, d // h
        self.qkv = nn.Linear(d, 3 * d, bias=False)      # W_Q | W_K | W_V fused into one matmul
        self.o = nn.Linear(d, d, bias=False)            # W_O: lets heads mix and land anywhere

    def forward(self, x, causal=True):                  # x: [B, n, d]
        B, n, d = x.shape
        q, k, v = self.qkv(x).chunk(3, dim=-1)          # each [B, n, d]
        split = lambda t: t.view(B, n, self.h, self.dk).transpose(1, 2)   # -> [B, h, n, dk]
        q, k, v = split(q), split(k), split(v)
        scores = q @ k.transpose(-2, -1) / self.dk ** 0.5                  # [B, h, n, n]
        if causal:
            future = torch.triu(torch.ones(n, n, dtype=torch.bool), 1)
            scores = scores.masked_fill(future, float("-inf"))
        attn = scores.softmax(-1)                                          # one distribution per head
        out = (attn @ v).transpose(1, 2).reshape(B, n, d)                  # concat heads
        return self.o(out), attn

x = torch.randn(2, 10, 512)
for h in (1, 8, 64):
    m = MHA(512, h)
    y, attn = m(x)
    print(f"h={h:2d} head_dim={512 // h:3d} params={sum(p.numel() for p in m.parameters()):,} "
          f"attn={tuple(attn.shape)} out={tuple(y.shape)}")
# h= 1 head_dim=512 params=1,048,576 attn=(2, 1, 10, 10)  out=(2, 10, 512)
# h= 8 head_dim= 64 params=1,048,576 attn=(2, 8, 10, 10)  out=(2, 10, 512)
# h=64 head_dim=  8 params=1,048,576 attn=(2, 64, 10, 10) out=(2, 10, 512)
```

## Trade-offs
- More heads give more parallel relations per layer, but each head's attention matrix has rank at most
  d_k, so past a point extra heads buy diversity at the price of per-head expressiveness; most current
  models settle at d_k = 128.
- Same FLOPs and parameters as a single wide head, yet the attention-probability tensor is *h* times
  larger; this is harmless with fused kernels and a memory wall without them.
- Heads shard across GPUs with no communication inside attention, which makes tensor parallelism
  simple, but *h* (and the number of KV heads) must divide by the parallel degree, which constrains
  hardware layouts.
- Redundant heads make the model robust to pruning and to KV sharing, but mean some attention compute
  at inference is doing little useful work.

## Pitfalls
- Reshaping `[B, n, h·d_k]` straight to `[B, h, n, d_k]` without the transpose, so heads mix positions
  instead of channels; the symptom is a model that trains without error but learns slowly and produces
  incoherent output.
- Picking a head dimension the attention kernels are not tuned for (anything outside 64/128/256, or
  not a multiple of 8); the symptom is a silent fall-back to a slow path and throughput several times
  lower than the same model at d_k = 128.
- Leaving out `W_O`, or applying it per head; the symptom is subtle, usually that adding heads stops
  helping because they cannot combine.
- Explaining a prediction with one head's attention map; the symptom is contradictory stories from
  different heads and layers for the same output.

## When to use
Multi-head attention is a given inside any transformer; what you decide is the split. When reading a
model config, `num_attention_heads`, `num_key_value_heads` and `head_dim` tell you the KV cache per
token, the shard granularity for tensor parallelism and whether fast kernels apply. When designing or
fine-tuning a small model, keep d_k at a kernel-friendly size and choose *h* from that rather than the
other way round. When debugging a from-scratch implementation, test with h = 1 first, then confirm the
reshape by checking that the per-head output for a given head matches a single-head computation on that
slice. In interviews, expect to explain why several heads beat one wide head at equal cost, what `W_O`
is for, and which quantities do and do not change with *h*.

## Role lens
- **Engineer:** Gets the view/transpose right, keeps head_dim kernel-friendly, and reads head counts
  off a config to size caches and pick a tensor-parallel degree.
- **Architect:** Treats head layout as a hardware constraint: the number of query and KV heads bounds
  how a model can be sharded across the GPUs the team actually owns.
- **FDE:** Explains why a customer's chosen model cannot be split evenly across their 6-GPU node and
  what the options are, using head counts as the argument.

## Check yourself
**Q:** You change a 4,096-wide attention layer from 8 heads to 32 heads. State what happens to the
parameter count, the FLOPs, the attention memory and the per-head expressiveness, and why.
**A:** Parameters stay at 4d² ≈ 67M because W_Q, W_K, W_V and W_O remain d×d; the heads are slices of
the same matrices. FLOPs stay the same because h × 2·n²·d_k = 2·n²·d regardless of the split. The
materialized attention tensor `[B, h, n, n]` grows four times, from 8 to 32 n×n maps, which matters if
you use a kernel that writes it to memory and is irrelevant with FlashAttention. Each head's d_k drops
from 512 to 128, so each attention pattern is lower rank and individually less expressive, but there are
four times as many of them. The KV cache per token is 2d either way for full multi-head attention; it
only shrinks if you reduce KV heads through GQA, which the question did not do.

## Quiz
### Q: Why does multi-head attention cost roughly the same as a single head of width d_model?
- [ ] Because heads share their key and value projections
- [x] Because each head works in d_model / h dimensions, so h heads together do the work of one full-width head
- [ ] Because only one head is active for each token
- [ ] Because the softmax is computed once and shared across heads
> Splitting the width across heads keeps the projection matrices d×d and the score FLOPs at 2·n²·d.
> Sharing K/V projections across heads is grouped-query attention, a separate idea.

### Q: What is the role of the output projection W_O?
- [ ] It normalizes the attention weights so each row sums to one
- [ ] It applies the causal mask to the concatenated heads
- [x] It mixes information across heads and maps the concatenation back into the residual stream
- [ ] It reduces the KV cache by compressing the heads
> Concatenation alone pins each head to a fixed slice of the output; W_O lets heads combine and place
> their contribution anywhere. Masking and normalization happen inside each head before concatenation.

### Q: Which quantity grows when you increase the number of heads at fixed d_model?
- [ ] The number of parameters in the attention layer
- [ ] The FLOPs of the Q/K/V projections
- [x] The size of the materialized attention-probability tensor [B, h, n, n]
- [ ] The KV cache per token for full multi-head attention
> Parameters and projection FLOPs depend only on d_model, and the full-MHA KV cache per token is 2d per
> layer. The n×n probability map is produced once per head, so its memory scales with h.
