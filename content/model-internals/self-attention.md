---
id: self-attention
module: model-internals
name: Self-attention
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
related:
  - model-internals/attention-mechanism
  - model-internals/multi-head-attention
  - model-internals/kv-cache
  - model-internals/positional-encoding
  - model-internals/flashattention
  - model-internals/context-window
  - serving-inference/prefill-vs-decode
sources:
  - Vaswani et al., 2017 — Attention Is All You Need
  - Xiao et al., 2023 — Efficient Streaming Language Models with Attention Sinks
  - Olsson et al., 2022 — In-context Learning and Induction Heads
---

## TL;DR
Self-attention is attention in which queries, keys and values all come from the same sequence: every
token asks every other token it is allowed to see for information and builds its new representation
from the answers. It is the only operation in a transformer that moves information between positions,
it runs as one matrix multiplication over all positions at once, and it costs O(n²) in sequence length,
which is where context limits, the KV cache and FlashAttention all come from.

## Why it exists
The meaning of a token depends on its neighbours: "bank" next to "river" and "bank" next to "account"
need different representations. A recurrent network builds that context by reading left to right and
accumulating a state, which is sequential and leaks information over distance. A convolution sees a
fixed window and needs many stacked layers before distant tokens can interact.

Self-attention lets every position compute a context-dependent representation from all positions in a
single step. The transformer paper compared layer types on three axes: compute per layer, number of
sequential operations, and the longest path a signal must travel between two positions. Self-attention
costs O(n²·d) per layer against O(n·d²) for recurrence, needs O(1) sequential steps against O(n), and
has a path length of O(1) against O(n) for recurrence and O(log n) for dilated convolution. When
sequence length *n* is smaller than the model width *d*, which held for the sentence-length inputs of
2017, self-attention is also cheaper per layer than recurrence.

The deeper reason it won is that the routing is content-based. Which token influences which is decided by
the data through learned similarity, not by a fixed topology, so the same layer can implement "look at
the previous token", "find the matching bracket" or "find the earlier occurrence of this name". That
flexibility is what let one architecture serve prose, code, protein sequences and audio tokens without
redesign. The cost is that nothing is free: the n×n interaction is paid for in compute and memory, and
the layer has no notion of order unless you give it one.

## How it works
### Projections
From the layer input **X** of shape `[n, d]`, three learned projections produce queries, keys and values:
`Q = X·W_Q`, `K = X·W_K`, `V = X·W_V`. They are separate matrices because a token's roles as asker,
advertiser and content are different. If you used **X** directly for all three, the score matrix
`X·Xᵀ` would be symmetric (i would want j exactly as much as j wants i, which "adjective → its noun"
relations are not), and for vectors of similar norm each token would match itself most strongly, so the
layer would mostly return each token unchanged. Separate W_Q and W_K make the score an asymmetric,
learned relation.

### Scores, mask, mix
`S = Q·Kᵀ / √d_k` is an `[n, n]` matrix whose entry (i, j) says how much position *i* wants to read
from position *j*. For a decoder, a **causal mask** sets `S[i, j] = −∞` for every *j > i*. Then each row
is passed through softmax and the result multiplies the values: `A = softmax(S)·V`.

Two design choices carry reasons. The mask is applied *before* softmax with −∞ rather than by zeroing
weights afterwards because `exp(−∞) = 0` exactly and the remaining weights renormalize over the allowed
positions; zeroing after softmax would leave rows that no longer sum to one. The mask is causal at all
because training predicts every next token in one pass with teacher forcing; if position *i* could see
position *i+1*, the prediction task would be trivial during training and impossible at inference, when
*i+1* does not exist yet. Encoders such as BERT drop the causal mask and let every token see every
other. Padding uses the same mechanism: −∞ on padded key positions.

### Shapes and FLOPs for one layer
Counting a matmul of `[m, k]·[k, p]` as 2·m·k·p FLOPs: the four projections (Q, K, V and the output
projection) cost 8·n·d²; `Q·Kᵀ` costs 2·n²·d and `A·V` another 2·n²·d, so the attention core costs
4·n²·d. The ratio of core to projections is therefore n/(2d). For d = 4096: at n = 4k the ratio is 0.5,
so the linear layers dominate; at 32k it is 4 and at 128k it is 16, so the quadratic term dominates.
Memory follows the same shape: the score matrix is n×n per head, and with 32 heads at n = 8,192 in fp16
that is 32 × 8192² × 2 bytes ≈ 4.3 GB per layer per sequence if it is ever materialized, which is the
problem FlashAttention removes.

### Permutation equivariance
With no positional signal, permuting the rows of **X** permutes the rows of the output identically: the
layer treats its input as a set. That is why positional encodings exist. A causal mask alone leaks some
order information, since each token can tell roughly how many tokens precede it, and decoders trained
with no explicit positions do learn something usable, but nearly every production model adds an explicit
scheme (a few recent families drop it in some layers).

### What happens at inference
Under the causal mask, the keys and values of earlier positions never change once computed, so they are
cached. During decoding only one new query row exists: the work per step is `[1, d]·[d, n]` per head,
O(n·d) rather than O(n²·d), and it is bound by reading the cache from memory rather than by arithmetic.
Prefill (the whole prompt at once) is the compute-bound n² case; decode is the bandwidth-bound linear
case. That split is the backbone of serving economics.

### Patterns that emerge
Trained models show recurring head types: previous-token heads, heads that attend to delimiters, and
induction heads that find an earlier occurrence of the current token and copy what followed it, which is
a mechanism behind in-context learning. Many heads also dump most of their weight on the first token as
an "attention sink": softmax must put its mass somewhere, and a constant, content-free position is a
convenient no-op. This matters in practice because evicting the first tokens from a sliding-window cache
breaks those heads, which is why streaming schemes keep them.

## Example
Causal self-attention written out with explicit shapes, then checked against PyTorch's fused kernel.
The explicit version is what you should be able to write on a whiteboard; the fused one is what runs in
production.

```python
import torch, torch.nn.functional as F
torch.manual_seed(0)
B, n, d, h = 2, 6, 64, 4            # batch, tokens, model width, heads
dk = d // h
x = torch.randn(B, n, d)
Wq, Wk, Wv, Wo = (torch.randn(d, d) / d ** 0.5 for _ in range(4))

def heads(t):                       # [B, n, d] -> [B, h, n, dk]
    return t.view(B, n, h, dk).transpose(1, 2)

q, k, v = heads(x @ Wq), heads(x @ Wk), heads(x @ Wv)
S = q @ k.transpose(-1, -2) / dk ** 0.5                     # [B, h, n, n] raw scores
future = torch.triu(torch.ones(n, n, dtype=torch.bool), diagonal=1)
S = S.masked_fill(future, float("-inf"))                    # j > i is invisible to i
A = S.softmax(dim=-1)                                       # each row sums to 1 over j <= i
print(A[0, 0].round(decimals=2))                            # lower-triangular weight matrix
out = (A @ v).transpose(1, 2).reshape(B, n, d) @ Wo         # merge heads, output projection

ref = F.scaled_dot_product_attention(q, k, v, is_causal=True)
ref = ref.transpose(1, 2).reshape(B, n, d) @ Wo
print(torch.allclose(out, ref, atol=1e-4))                  # True: same math, fused kernel
print(S.shape, "scores per head are n x n ->", n * n, "entries; at n=8192 that is", 8192 ** 2)
```

## Trade-offs
- O(1) path length and full parallelism across positions, paid for with O(n²) compute and memory in
  sequence length; the balance flips against dense attention when n is many times d, which is when
  sliding windows, sparse patterns or retrieval take over part of the job.
- Content-based routing adapts to any structure in the data, but brings no built-in locality bias, so
  small-data regimes favour architectures with stronger priors and large-data regimes favour attention.
- Bidirectional self-attention gives richer per-token representations; causal self-attention gives up
  the right context in exchange for generation and a reusable KV cache.
- Exact softmax attention can retrieve a single specific earlier token (copying, induction); linear
  attention and state-space models cut cost to O(n) but compress history into a fixed state and give up
  some of that precise recall.

## Pitfalls
- Masking with zeros after softmax, or masking the wrong triangle, lets tokens see the future; the
  symptom is a training loss that falls suspiciously close to zero followed by nonsense at inference.
- Right-padding a batch for a decoder-only model during generation puts pad tokens after the prompt;
  the symptom is that shorter prompts in a batch produce junk while the longest one is fine. Use left
  padding with an attention mask.
- A fully masked row (every key −∞, as with an all-padding query row) makes softmax return NaN; the
  symptom is NaN losses that appear only on certain batches.
- Costing attention with only the quadratic term or only the linear term; the symptom is a capacity plan
  that is right at one context length and wrong by multiples at another.

## When to use
Self-attention is not optional in a transformer, so the real decisions are around it: how long a context
you can afford, whether to use an encoder (bidirectional) or decoder (causal) model for a task, how to
pad and mask batches correctly, and which kernel serves it. Measure the share of attention time in
your profiles at your actual sequence lengths before optimizing anything; at short context the linear
layers dominate and attention kernels will not help. In interviews, be able to derive the O(n²·d)
cost and the n/(2d) crossover, explain why the mask uses −∞ before softmax, state why positions must be
added explicitly, and show how the KV cache and the prefill/decode split follow from causality.

## Role lens
- **Engineer:** Writes and debugs the masking and padding logic, chooses left padding for generation,
  and profiles whether attention or the MLP dominates at the team's real sequence lengths.
- **Architect:** Sets context-length policy from the n/(2d) crossover and the memory curve, and decides
  where retrieval must shrink inputs instead of paying quadratic attention.
- **FDE:** Diagnoses a customer's "it works for short inputs and breaks for long ones" by checking
  context limits, padding direction and attention memory on their hardware.

## Check yourself
**Q:** A model with hidden size 4,096 processes an 8k-token prompt. In the attention layer, do the
Q/K/V/output projections or the score computation dominate FLOPs, and how does that change at 128k?
**A:** Projections cost 8·n·d² and the attention core (QKᵀ plus AV) costs 4·n²·d, so their ratio is
n/(2d). At n = 8,192 and d = 4,096 the ratio is 1: the two halves are equal, and together with the MLP
(which is larger than the projections) the linear layers still dominate the layer as a whole. At
n = 131,072 the ratio is 16, so the quadratic core is sixteen times the projections and dwarfs the MLP
too; the layer is now attention-dominated. The practical consequences follow: at 8k, kernel choices for
matmuls and quantizing weights matter most; at 128k, attention kernels, memory for the n×n scores (never
materializing them) and possibly sparse or windowed attention are what determine cost and feasibility.

## Quiz
### Q: Why is the causal mask applied as −∞ before the softmax instead of zeroing weights after it?
- [ ] Because −∞ is faster to compute on GPUs than multiplying by zero
- [x] Because exp(−∞) is exactly 0 and the remaining weights renormalize to sum to one over allowed positions
- [ ] Because softmax cannot accept zeros as inputs
- [ ] Because zeroing after softmax would leak information from earlier positions
> Zeroing after softmax leaves rows whose weights no longer sum to one, scaling outputs by how much was
> masked. Masking the logits lets softmax redistribute probability over the visible positions only.

### Q: Which property of self-attention makes an explicit positional encoding necessary?
- [ ] Its quadratic cost in sequence length
- [ ] The use of separate key and value projections
- [x] Permutation equivariance: without positions, reordering the input only reorders the output
- [ ] The fact that softmax weights sum to one
> Self-attention treats its input as a set; "dog bites man" and "man bites dog" would yield the same
> per-token outputs. Positional encodings break that symmetry so order can influence attention.

### Q: Per the original transformer analysis, when is self-attention cheaper per layer than a recurrent layer?
- [x] When sequence length n is smaller than the representation width d
- [ ] When sequence length n is larger than the representation width d
- [ ] Always, regardless of n and d
- [ ] Never; it is chosen only for parallelism
> Self-attention costs O(n²·d) per layer and recurrence O(n·d²), so attention is cheaper when n < d.
> At long contexts recurrence is cheaper in FLOPs, and attention is kept for its path length and
> parallelism.
