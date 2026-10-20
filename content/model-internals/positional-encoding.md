---
id: positional-encoding
module: model-internals
name: Positional encoding (RoPE)
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - model-internals/self-attention
  - model-internals/context-window
  - model-internals/kv-cache
  - model-internals/transformer
  - rag-retrieval/long-context-vs-rag
  - rag-retrieval/lost-in-the-middle
  - prompting/context-engineering
sources:
  - Vaswani et al., 2017 — Attention Is All You Need
  - Su et al., 2021 — RoFormer: Enhanced Transformer with Rotary Position Embedding
  - Press et al., 2022 — Train Short, Test Long: Attention with Linear Biases Enables Input Length Extrapolation
  - Chen et al., 2023 — Extending Context Window of Large Language Models via Positional Interpolation
---

## TL;DR
Self-attention treats its input as a set, so a transformer needs an explicit signal for token order.
Early models added a sinusoid or learned vector per absolute position to the embeddings. RoPE instead
rotates each query and key by an angle proportional to its position, so their dot product depends only
on their distance apart. That relative property, zero parameters and clean interaction with the KV cache
made RoPE the default in open LLMs, and context extension is mostly about stretching those angles.

## Why it exists
Without position information, self-attention gives identical per-token outputs for "dog bites man" and
"man bites dog"; permuting the input permutes the output and nothing else. Order has to be injected, and
a good scheme has to satisfy several demands at once: every position must be distinguishable; the model
must be able to learn *relative* offsets, because most linguistic structure is local (adjective before
noun, matching brackets, the previous token); it should degrade gracefully past the lengths seen in
training; and it must be cheap and compatible with caching keys and values.

The original transformer added fixed sinusoidal vectors to the embeddings, chosen because a shift by
*k* positions is a linear function of the sinusoids, so relative offsets are in principle learnable.
But the signal is added once at the input, gets mixed into values and content, and the model has to
disentangle it. GPT-2 and BERT replaced sinusoids with a learned table, one vector per position, which
is simple and strong at trained lengths but hard-caps the context at the table size and leaves rarely
seen positions undertrained. Relative-position schemes (Shaw et al., the T5 bias) add a learned bias per
offset directly to the attention scores, a better inductive bias, but they bucket offsets, add a term to
the attention kernel and still extrapolate poorly.

RoPE's move is to encode absolute position as a rotation of the query and key vectors, so that the score
`qₘ·kₙ` depends only on `n − m` and the contents. It needs no parameters, adds nothing to the score
computation beyond a cheap elementwise rotation, works with any attention kernel, and lets cached keys
stay valid as the sequence grows. ALiBi, the main alternative, instead adds a fixed distance penalty to
the scores; it extrapolates better out of the box but imposes a recency prior that can hurt long-range
retrieval. The open-model ecosystem converged on RoPE.

## How it works
### The rotation
Split the head dimension *d* into d/2 pairs. Pair *i* is assigned a frequency `θ_i = base^(−2i/d)`, with
base = 10,000 in the original paper and Llama 2 and 500,000 in Llama 3. A vector at position *m* has each
pair rotated by the angle `m·θ_i`:

```
[x, y] -> [x·cos(mθ_i) − y·sin(mθ_i),  x·sin(mθ_i) + y·cos(mθ_i)]
```

This is applied to queries and keys, **not** to values. Why the dot product becomes relative: a 2-D
rotation matrix satisfies `R_mᵀ·R_n = R_(n−m)`, so `(R_m q)·(R_n k) = q·R_(n−m) k`. The absolute positions
cancel; only the offset and the contents remain. Values are left alone because they carry content that
is summed into the output; rotating them would scramble what is retrieved by where it sits, whereas
positions should only influence *who attends to whom*.

### Frequencies and what they encode
Pair 0 rotates one radian per token and wraps every ~6 tokens, so it resolves very local order. The
slowest pair, with d = 128 and base 10,000, turns by about 27° across 4,096 tokens and completes a full
rotation only every ~54,000 tokens, so within a typical context it barely moves and acts as a
near-absolute, long-range channel. Raising the base to 500,000 leaves pair 0 at one radian per token and
slows every other pair, increasingly so toward the slow end; the slowest pair then needs about 2.5
million tokens for a full turn, so more pairs stay inside the arc they saw in training at long range. The spread of frequencies lets different dimensions
specialize in different ranges, the same geometric schedule the sinusoidal encoding used additively.
The RoPE paper also shows that the inner product between a fixed query and key decays with distance on
average, a built-in mild preference for nearby tokens.

### Implementation
Two equivalent layouts exist: interleaved pairs `(2i, 2i+1)` as in the paper and in Meta's reference
Llama code (which multiplies complex numbers), or "rotate-half", which pairs dimension *i* with
*i + d/2* and computes `q·cos + rotate_half(q)·sin`, as in the Hugging Face Llama implementation. They
differ by a permutation of dimensions, and the permutation is baked into trained `W_Q` and `W_K`, so
converting checkpoints between layouts requires permuting those weights; the official conversion script
does exactly that, and forgetting it is a classic porting bug. The cos/sin tables `[max_len, d/2]` are
precomputed, usually in fp32, and applied after the Q/K projections and before the score matmul. The
order matters: rotating the hidden state *before* `W_Q` and `W_K` would put the projection matrices
between the two rotations and destroy the `R_mᵀ·R_n = R_(n−m)` cancellation. Some families rotate only a
fraction of each head's dimensions (`partial_rotary_factor` or `rotary_pct` in configs) and leave the
rest position-free, so check the config rather than assuming the whole head is rotated.

### Interaction with the KV cache
Each key is rotated by its own absolute position when it is inserted into the cache, and each query by
its position at query time; the relative property emerges in the dot product, so nothing in the cache
needs re-rotation as the sequence grows. The consequences follow: evicting tokens from the front of a
sliding-window cache leaves the survivors at their original absolute angles (streaming schemes re-assign
positions inside the cache for this reason), and editing anything before position *i* invalidates every
cached key from *i* onward.

### Context extension
Past the training length, the low-frequency pairs reach angles the model never saw, attention patterns
break and perplexity rises sharply soon after the limit. Position interpolation
(Chen et al.) rescales positions by `L_train / L_new` so all angles stay inside the trained range, then
fine-tunes briefly; it works but squeezes the high-frequency pairs, costing resolution between adjacent
tokens. NTK-aware scaling instead raises the base, which stretches low frequencies a lot and high ones
little. YaRN combines per-frequency treatment, leaving high frequencies untouched and interpolating low
ones, with a temperature on the logits, and needs fewer fine-tuning tokens. Llama 3.1's 128k context uses
a frequency-dependent scaling of this family on top of an 8k-trained base. The common lesson is that
"max context" is a property of the trained angles, not of a config field.

### ALiBi, briefly
ALiBi adds `−slope_h·(i − j)` to the score of query *i* and key *j ≤ i*, with per-head slopes forming a
geometric series. No parameters, decent extrapolation, used by BLOOM and MPT; its recency bias is the
trade.

## Example
A minimal RoPE in NumPy that demonstrates the relative property numerically and shows why extension
beyond the trained length needs care.

```python
import numpy as np

def rope(x, pos, base=10000.0):
    """Rotate x (even length d) to position pos: pair (2i, 2i+1) turns by pos * theta_i."""
    d = x.shape[-1]
    theta = base ** (-np.arange(0, d, 2) / d)       # d/2 frequencies from 1 down to ~1/base
    ang = pos * theta
    cos, sin = np.cos(ang), np.sin(ang)
    x1, x2 = x[0::2], x[1::2]
    out = np.empty_like(x)
    out[0::2] = x1 * cos - x2 * sin
    out[1::2] = x1 * sin + x2 * cos
    return out

rng = np.random.default_rng(0)
d = 128
q, k = rng.standard_normal(d), rng.standard_normal(d)

# Same offset (3 apart), different absolute positions -> identical attention score.
print(round(rope(q, 5) @ rope(k, 2), 6), round(rope(q, 1005) @ rope(k, 1002), 6))
# Different offsets -> different scores although q and k never changed.
print(round(rope(q, 5) @ rope(k, 4), 6), round(rope(q, 5) @ rope(k, 0), 6))

# Why extrapolation fails: the slowest pair barely moves inside a 4k window ...
theta_min = 10000 ** (-(d - 2) / d)
print(f"slowest pair: {np.degrees(4096 * theta_min):.1f} degrees over 4096 tokens, "
      f"full turn every {2 * np.pi / theta_min:,.0f} tokens")
# ... so positions past the trained length put those pairs at angles the model has never seen.
# Position interpolation: rope(x, pos * L_train / L_new) keeps every pair inside its trained range.
```

## Trade-offs
- RoPE gives relative attention with zero parameters and a cache that never needs re-rotation, but it
  extrapolates poorly past the trained length; if you need length generalization with no fine-tuning,
  ALiBi's fixed penalty behaves better out of the box.
- Learned absolute tables are simple and strong at trained lengths, with a hard cap on context; they
  remain reasonable for fixed-length encoders and wrong for anything that must grow.
- A larger base (500,000 versus 10,000) improves long-context behaviour by slowing the middle and low
  frequencies, at the cost of fewer pairs that distinguish offsets in the hundreds-to-thousands range;
  whether that costs short-context quality depends on model and data, and vendors tune it empirically.
- Position interpolation is simple and robust after a short fine-tune but blurs local resolution; YaRN
  preserves high frequencies and needs fewer tokens but has more knobs to get wrong.

## Pitfalls
- Mixing the interleaved and rotate-half layouts when loading ported weights; the symptom is a model with
  correct shapes and no errors whose perplexity is in the hundreds.
- Feeding wrong position ids: restarting at 0 on each decode step, or counting pad tokens in a
  left-padded batch; the symptom is output that is fine for the longest prompt in a batch but degraded
  for shorter ones, or quality that drops right after the first generated token. Positions must come
  from the attention mask and the cache length.
- Running past `max_position_embeddings` with no scaling; the symptom is output that degenerates into
  repetition or nonsense right after the trained length, with no exception raised.
- Computing the cos/sin tables in bf16 or fp16 for large positions; the symptom is slowly degrading
  quality at high positions caused by rounding of large angles. Keep the frequency computation in fp32.
- Setting `rope_scaling` in a serving config without the fine-tuning the method assumes; the symptom is
  degraded short-context quality and only partial recovery at long context.

## When to use
You choose a positional scheme indirectly by choosing a model, but you meet it directly in three places:
reading `rope_theta` and `rope_scaling` in a config to know what context the model really supports,
running a context-extension project (interpolation or YaRN plus a fine-tune, evaluated at the target
length with needle-in-haystack and real tasks, not just perplexity), and debugging ported checkpoints
or custom kernels where the rotation layout matters. Measure quality at the lengths you will actually
serve, because the loss past the trained length is a cliff, not a slope. In interviews, be ready to
derive the relative property from rotation composition, explain why only queries and keys are rotated,
and describe why extrapolation fails and how interpolation fixes it.

## Role lens
- **Engineer:** Sets `rope_scaling` correctly when serving extended context, verifies the rotation
  layout when converting weights, and tests at target lengths before shipping.
- **Architect:** Decides whether to buy long context through RoPE scaling plus fine-tuning or through
  retrieval, knowing that the trained length, not the config, bounds reliable behaviour.
- **FDE:** Answers a customer's "can we just raise max tokens to 64k?" by explaining trained angles
  versus config fields and proposing an evaluated extension or a retrieval design.

## Check yourself
**Q:** Explain how RoPE encodes absolute position yet produces relative attention, and why a model trained
at 4k tokens degrades sharply at 8k if nothing is changed.
**A:** Each query and key is rotated, pair of dimensions by pair, through an angle equal to its absolute
position times a per-pair frequency. Because rotations compose, the dot product between a query rotated
by *m* and a key rotated by *n* equals the dot product between the original query and the key rotated by
*n − m*; the absolute positions cancel and only the offset and contents remain. Values are not rotated so
retrieved content is unaffected. During training at 4k, the low-frequency pairs only ever sweep a small
arc, so the model's weights are tuned to scores produced within that arc. At positions 4k–8k those pairs
enter angles never seen, the scores they contribute are effectively random, attention patterns break and
perplexity climbs. Position interpolation rescales positions so 8k maps into
the trained arc, then a short fine-tune restores quality.

## Quiz
### Q: In RoPE, which tensors are rotated?
- [ ] Queries, keys and values
- [x] Queries and keys only
- [ ] The token embeddings before the first layer
- [ ] The attention probabilities after the softmax
> Rotating queries and keys makes their dot product depend on relative position. Values are left alone
> because they carry content that is summed into the output, and positions should affect only who
> attends to whom.

### Q: After RoPE, the attention score between a query at position m and a key at position n depends on
- [ ] m and n separately, as with learned absolute embeddings
- [x] The offset n − m and the content of the two vectors
- [ ] Only the content, with no positional influence
- [ ] The absolute position of the key alone
> Rotation matrices compose, so R_mᵀ·R_n = R_(n−m) and the absolute positions cancel inside the dot
> product. Learned absolute tables, by contrast, give the model separate vectors for m and n.

### Q: Position interpolation extends a RoPE model's context by
- [ ] Adding new learned position vectors for the extra positions
- [ ] Increasing the head dimension so more frequencies are available
- [x] Scaling positions down so all rotation angles stay within the range seen in training, then fine-tuning briefly
- [ ] Removing the positional signal for tokens beyond the trained length
> Beyond the trained length the slow frequencies reach unseen angles. Compressing positions keeps every
> pair inside its trained arc, at the cost of finer-grained local resolution, which a short fine-tune
> largely recovers.
