---
id: softmax
module: model-internals
name: Softmax
kind: concept
difficulty: intro
est_minutes: 12
status: fact-checked
related:
  - model-internals/logits
  - model-internals/attention-mechanism
  - model-internals/sampling-strategies
  - model-internals/flashattention
  - ml-fundamentals/loss-functions
  - ml-fundamentals/calibration
  - ml-fundamentals/classification
sources:
  - Bridle, 1990 — Probabilistic Interpretation of Feedforward Classification Network Outputs
  - Vaswani et al., 2017 — Attention Is All You Need
  - Milakov & Gimelshein, 2018 — Online normalizer calculation for softmax
  - Xiao et al., 2023 — Efficient Streaming Language Models with Attention Sinks
---

## TL;DR
Softmax turns a vector of arbitrary real scores into a probability distribution:
`p_i = exp(z_i / T) / Σ_j exp(z_j / T)`. It is differentiable, order-preserving, invariant to shifting
all scores, and its sharpness is controlled by the temperature *T*. An LLM uses it twice: inside every
attention head to weight positions, and at the output to turn logits into next-token probabilities.
Its properties, including "never exactly zero" and "must sum to one", explain sampling behaviour,
attention sinks and several numerical bugs.

## Why it exists
A network that scores *K* options needs a way to choose among them that it can learn through. Taking
the argmax is the obvious choice, but it is piecewise constant: a tiny change in scores either does
nothing or flips the answer, so the gradient is zero almost everywhere and nothing propagates back to
the scores that were not chosen. Dividing each score by the sum of scores fails too: scores can be
negative, the result is not a distribution, and the map does nothing to sharpen or soften preferences.

Exponentiating first solves both problems. `exp` is positive, so the normalized values are a valid
distribution for any real input; it is smooth, so every score receives a gradient; and it is monotone,
so ranking is preserved. The result is the "soft" version of argmax: as the gap between the best score
and the rest grows, the output approaches a one-hot, and as scores converge it approaches uniform. Paired
with cross-entropy loss, the gradient with respect to the logits is simply `p − y`, prediction minus
target, which is as clean a training signal as exists. Softmax also has a principled reading: it is the
maximum-entropy distribution consistent with the scores as expected energies, the same form as the
Boltzmann distribution in physics, which is where the word "temperature" comes from.

## How it works
### The function and its properties
For scores `z` and temperature `T > 0`:

```
p_i = exp(z_i / T) / Σ_j exp(z_j / T)
log p_i = z_i / T − logsumexp(z / T)
```

- **Shift invariance.** `softmax(z + c) = softmax(z)` for any constant *c*, because `exp(c)` factors out
  of numerator and denominator. Implementations subtract `max(z)` before exponentiating *because* the
  result is identical and the largest exponent becomes `exp(0) = 1`, so nothing overflows.
- **Temperature.** Dividing by `T < 1` widens gaps between scores and sharpens the distribution; `T > 1`
  flattens it. As `T → 0` softmax becomes argmax; as `T → ∞` it becomes uniform.
- **Strictly positive.** Every output is greater than zero for finite inputs. Only `−∞` produces an
  exact zero, which is how masks and token bans work.
- **Sums to one.** Mass removed from one entry must go somewhere. This is a constraint, not a feature,
  and it has consequences in attention (below).
- **Gradient.** `∂p_i/∂z_j = p_i(δ_ij − p_j)`. With cross-entropy against a one-hot target the combined
  gradient is `p − y`.
- **Two classes.** With `K = 2`, softmax reduces to the sigmoid of the score difference; the logistic
  function is the special case.

### At the output layer
The vocabulary softmax converts the last position's logits into the next-token distribution over tens
of thousands of entries. Because every entry stays positive, the distribution has a long tail of
individually tiny but collectively significant probabilities; sampling from the full distribution will
eventually draw one of them, which is why top-k and top-p truncation exist. Temperature here is a
user-facing knob; temperature inside attention is not.

### Inside attention
Each head computes `softmax(q·Kᵀ / √d_k)` over positions, producing weights that sum to one across the
positions the query may see. The `√d_k` scale is a fixed temperature: dot products of *d_k*-dimensional
vectors grow in magnitude with *d_k*, and without the scale the softmax would saturate early in training,
giving near-one-hot weights and vanishing gradients. Causal masking adds `−∞` to future positions before
the softmax so they get exactly zero weight. Because the weights must sum to one even when no position is
relevant, models learn to dump excess attention onto an uninformative position, usually the first token;
these **attention sinks** are a direct consequence of the normalization constraint, and streaming methods
that evict old tokens must keep the sink tokens or quality collapses.

### Computing it efficiently
A naive softmax over a long row needs the whole row in memory to find the max and the sum. The online
formulation keeps a running maximum and rescales the running sum when the maximum changes, so the result
can be accumulated in tiles that never leave fast on-chip memory. That trick is the heart of
FlashAttention: exact attention without materializing the full `n × n` score matrix.

## Example
The code shows a stable softmax with temperature on a three-way score vector and prints how entropy
moves with *T*; the numbers are worked out below so you can check them by hand. The last two lines
show the overflow the max-subtraction trick prevents.

```python
import numpy as np

def softmax(z, T=1.0):
    z = np.asarray(z, dtype=np.float64) / T
    z = z - z.max()                      # shift invariance: same result, no overflow
    e = np.exp(z)
    return e / e.sum()

z = [2.0, 1.0, 0.1]
for T in (1.0, 0.5, 2.0):
    p = softmax(z, T)
    H = -(p * np.log(p)).sum()
    print(f"T={T}: p={p.round(3)} entropy={H:.2f} nats")

big = np.array([1000.0, 0.0], dtype=np.float32)
print(np.exp(big) / np.exp(big).sum())   # overflow: inf/inf -> nan in the first slot
print(softmax(big))                      # [1. 0.]
```

By hand at `T = 1`: `exp(2.0) = 7.389`, `exp(1.0) = 2.718`, `exp(0.1) = 1.105`, sum `11.213`, so
`p ≈ [0.659, 0.242, 0.099]`. At `T = 0.5` the scores become `[4, 2, 0.2]`, exponentials
`[54.598, 7.389, 1.221]`, sum `63.209`, `p ≈ [0.864, 0.117, 0.019]`: the leader gained and the tail
shrank by a factor of five. At `T = 2` the scores are `[1, 0.5, 0.05]`, exponentials
`[2.718, 1.649, 1.051]`, sum `5.418`, `p ≈ [0.502, 0.304, 0.194]`: close to uniform. Same ranking in
all three cases; only the confidence changed.

## Trade-offs
- Smooth, differentiable normalization gives clean gradients everywhere, but it can never express a hard
  zero, so sampling must be truncated externally and attention must park unused mass somewhere.
- Lower temperature raises consistency and accuracy on tasks with one right answer, while higher
  temperature raises diversity; the right value flips with whether the task rewards variety.
- The exact softmax needs the whole row, which is memory-heavy for long attention rows; the online
  version is exact and tile-friendly at the cost of extra rescaling work per tile.
- Scaling by `√d_k` stabilizes attention at initialization, but it is a fixed temperature; heads that
  need very sharp or very flat attention must learn to grow or shrink their query and key norms.

## Pitfalls
- Exponentiating in half precision or without subtracting the max; the symptom is NaN or inf
  probabilities on long or confident rows, often only on some batches.
- Masking after the softmax (multiplying probabilities by zero) instead of adding `−∞` before it; the
  symptom is rows that no longer sum to one. The sibling bug, masking with a large finite negative such
  as −1e4 that underflows to nothing in fp16, shows up as attention leaking to masked positions.
- Treating "99% probability" as 99% correctness; softmax outputs are scores shaped by training, and
  calibration must be measured rather than assumed.
- Evicting the first tokens of a long context to save KV memory; attention weights that used to land on
  the sink get redistributed and output quality degrades sharply.
- Reusing an output-layer temperature inside attention or vice versa; they are separate knobs with
  separate meanings.

## When to use
You do not choose softmax; you inherit it at two places in every transformer and in nearly every
classifier. What you choose is the temperature at the output, the truncation rules layered on top, the
precision the normalization runs in, and whether a problem is better framed as multi-class (softmax) or
multi-label (independent sigmoids, because labels are not mutually exclusive). Measure calibration if
you intend to read probabilities as confidence. The function shows up daily when debugging NaNs, when
tuning sampling, and when reasoning about why attention behaves as it does. In interviews, expect to
derive the gradient with cross-entropy, explain the max-subtraction trick, and say why attention scores
are scaled by `√d_k`.

## Role lens
- **Engineer:** Sets temperature per endpoint, computes softmax and losses in fp32, and masks with `−∞`
  before normalization rather than zeroing afterwards.
- **Architect:** Decides whether a classification problem is mutually exclusive (softmax) or multi-label
  (sigmoids), and whether exposed probabilities are calibrated enough for downstream policy.
- **FDE:** Explains to a customer why temperature 0.2 made their extraction consistent and why the
  "confidence" column still needs a calibration check on their data.

## Check yourself
**Q:** Why are attention scores divided by the square root of the key dimension before the softmax, and
what would you expect to see in training if that scale were removed?
**A:** A dot product of two *d_k*-dimensional vectors with roughly unit-variance components has variance
proportional to *d_k*, so with `d_k = 128` raw scores are about eleven times larger in spread than with
`d_k = 1`. Softmax of large-magnitude inputs is nearly one-hot, and in that regime its gradient
`p_i(δ_ij − p_j)` is close to zero for every entry, so the query and key projections receive almost no
learning signal. Dividing by `√d_k` restores unit variance, keeping the softmax in its responsive range
at initialization. Without it you would expect attention maps that collapse to single positions early,
slow or stalled loss decrease, and sensitivity to initialization, which is exactly the instability the
scale was introduced to prevent.

## Quiz
### Q: Why do implementations subtract the maximum score before exponentiating?
- [ ] To make the output sum to one
- [x] Because the result is identical by shift invariance and the largest exponent becomes exp(0), preventing overflow
- [ ] To apply the temperature
- [ ] To zero out masked positions
> Softmax is unchanged by adding a constant to all inputs, so subtracting the max costs nothing in
> accuracy and bounds every exponent at or below 1, which keeps the sum finite in fp32 or fp16.

### Q: What happens to the next-token distribution as temperature approaches zero?
- [ ] It becomes uniform over the vocabulary
- [ ] It becomes undefined because of division by zero
- [x] It concentrates all mass on the highest-logit token, equivalent to greedy decoding
- [ ] It removes tokens below a probability threshold
> Dividing by a tiny T widens the gaps between scores without bound, so the top entry dominates the sum
> and the rest vanish. Engines special-case T = 0 as argmax rather than dividing.

### Q: Why do attention sinks arise in trained transformers?
- [ ] Because the first token has a special embedding
- [ ] Because rotary position encoding gives position zero a distinctive rotation
- [x] Because attention weights must sum to one even when no position is relevant, so the model learns to dump excess mass onto a convenient token
- [ ] Because of weight decay
> The normalization constraint forces every query to allocate its full weight somewhere. Early tokens
> are visible to all later positions, so they become the default place to park attention mass.
