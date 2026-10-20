---
id: normalization
module: ml-fundamentals
name: Normalization (LayerNorm, RMSNorm)
kind: concept
difficulty: core
est_minutes: 14
related:
  - ml-fundamentals/residual-connections
  - ml-fundamentals/warmup
  - ml-fundamentals/mixed-precision
  - ml-fundamentals/backpropagation
  - ml-fundamentals/batch-size
  - model-internals/transformer
  - model-internals/self-attention
sources:
  - Ba, Kiros & Hinton, 2016 — Layer Normalization
  - Zhang & Sennrich, 2019 — Root Mean Square Layer Normalization
  - Xiong et al., 2020 — On Layer Normalization in the Transformer Architecture
  - Ioffe & Szegedy, 2015 — Batch Normalization
---

## TL;DR
Normalization layers rescale each token's activation vector to a fixed scale before it enters a sublayer,
so what attention and the MLP see does not depend on what every earlier layer happened to do. LayerNorm
subtracts the mean and divides by the standard deviation over the feature dimension; RMSNorm skips the
mean and divides by the root mean square, which is cheaper and works as well. Where the norm sits
relative to the residual (Pre-LN versus Post-LN) decides how stable training is.

## Why it exists
A deep network is a composition of layers, and each layer's weights are updated on the assumption that
its input has a certain scale. Without normalization that assumption breaks on every step: a small change
in layer 3 shifts the scale and distribution of the activations arriving at layer 30, so layer 30's
gradient was computed for inputs it no longer receives. The deeper the stack, the worse the compounding,
and the outcome is a learning rate that must be tiny to be safe, activations that drift toward saturation
or explosion, and training that is exquisitely sensitive to initialization. Batch normalization (2015)
was the fix that made very deep networks routinely trainable, by standardizing each feature over the
mini-batch.

Transformers could not use it. BatchNorm's statistics depend on which examples share a batch: in sequence
models that means padding tokens, wildly different sequence lengths and small per-device micro-batches all
leak into every token's normalization, and the running averages used at inference differ from the
training-time statistics, so the model behaves differently when served than when trained. Tokens at
different positions have different statistics, and under data parallelism the batch statistics would
have to be synchronized across devices at every layer.

Layer normalization (2016) moved the statistics from "across the batch, per feature" to "across the
features, per token". Every token normalizes itself using only its own vector of `d_model` numbers, so
the result is identical at batch size 1 and 1,024, identical in training and inference, and independent
of padding. The naive alternative, careful initialization plus a low learning rate and no normalization,
trains shallow transformers but does not scale; the recent attempts to remove normalization replace it
with purpose-built substitutes (zero-initialized scalar gates on each branch, bounded element-wise
functions) to recover the same stability, which shows that the function the norm performs is not
optional even when the specific layer is.

## How it works
### LayerNorm
For one token's activation vector `x ∈ R^d`:

```
μ   = (1/d) Σ_i x_i
σ²  = (1/d) Σ_i (x_i − μ)²
y_i = γ_i × (x_i − μ) / sqrt(σ² + ε) + β_i
```

`γ` (gain) and `β` (bias) are learned per feature; `ε`, typically 1e-5 or 1e-6, prevents division by
zero for a constant vector. After normalization the vector has zero mean and unit variance *across its
features*; `γ` and `β` then let the network place each feature back on whatever scale and offset the next
layer prefers. Without them the sublayer's inputs would be pinned to the unit sphere, which costs
expressivity; with them the network can undo the normalization exactly if the gradient wants that, so
the layer never makes the model strictly less capable.

Two properties do the optimization work. The output is invariant to scaling the input, `LN(a·x) = LN(x)`
for `a > 0`, so if the weights feeding this norm grow, the forward pass does not change while the gradient
with respect to those weights shrinks like `1/a`: large weights automatically receive a smaller effective
learning rate, a self-stabilizing effect. And the gradient through the norm is projected orthogonal to the
input direction, which removes the component of an update that would only have rescaled the activation.

### RMSNorm
```
rms = sqrt( (1/d) Σ_i x_i² + ε )
y_i = γ_i × x_i / rms
```

RMSNorm drops the mean subtraction and the bias. The 2019 paper's argument is that LayerNorm's benefit
comes from re-scaling invariance, not from re-centering, and its experiments showed matching quality at
lower cost: one reduction over the vector instead of two, no bias parameters, and a simpler kernel that
fuses well. Per token the saving is small, but it sits on the critical path of every layer, and at
inference the norm is a bandwidth-bound op where every pass over the vector counts. Llama, Mistral and
most open-weight decoders since 2023 use RMSNorm; most implementations compute it in fp32 and cast back,
and some (Gemma, for example) parameterize the gain as `1 + γ` so that a zero-initialized `γ` means
identity scaling.

### Where the norm sits: Post-LN versus Pre-LN
The original transformer normalized *after* the residual addition (Post-LN):

```
x ← LN(x + Attention(x));   x ← LN(x + MLP(x))
```

Modern decoders normalize the *input* of each sublayer and leave the residual stream untouched (Pre-LN),
with one final norm before the output head:

```
x ← x + Attention(LN(x));   x ← x + MLP(LN(x));   logits = W_out · LN_final(x)
```

The difference is the gradient path. In Post-LN the residual stream itself passes through a norm at
every layer, so the gradient reaching early layers is scaled by a product of per-layer factors; the 2020
analysis showed that at initialization the expected gradient norm near the output grows with depth,
which is why Post-LN needs a long warmup and a small learning rate to survive the first steps. In Pre-LN
the skip path is a pure identity from the embeddings to the final norm, so gradients at initialization
are roughly uniform across layers, warmup can be short, and deep models train without heroics. The price
is that the residual stream's norm grows with depth, since every layer adds to it and nothing rescales
it, so later layers see inputs dominated by the accumulated sum and contribute relatively less; the final
norm fixes the scale before the head but not that imbalance. Variants address it: normalizing both the
input and the output of each sublayer, normalizing queries and keys before the attention dot product
(QK-norm) so attention logits cannot grow without bound, and depth-dependent scaling of the residual
branches.

### Numerics
Under mixed precision the variance is a sum of thousands of squares; in bf16 that sum loses its small
terms, and in fp16 it can overflow. Autocast therefore runs `layer_norm` in fp32 and returns fp32, and a
hand-written RMSNorm should upcast before squaring. `ε` must also survive the dtype: 1e-8 is below fp16's
smallest subnormal and becomes exactly zero if the norm runs in fp16, so a padding token's all-zero
vector divides by zero.

### Training details
Weight decay is conventionally not applied to `γ` and `β` (nor to biases); decaying the gain toward zero
fights the normalization's purpose. Norm parameters are a tiny fraction of the model, which is why they
are often left trainable even in parameter-efficient fine-tuning.

## Example
A from-scratch LayerNorm and RMSNorm checked against the built-in, with one token's numbers worked out in
the comments, followed by the Pre-LN block that every modern decoder layer reduces to. The offset test is
the point: RMSNorm leaves the mean in place, which is the entire difference between the two.

```python
import torch

def layer_norm(x, gamma, beta, eps=1e-5):
    mu = x.mean(-1, keepdim=True)
    var = x.var(-1, unbiased=False, keepdim=True)        # population variance over the features
    return gamma * (x - mu) / torch.sqrt(var + eps) + beta

def rms_norm(x, gamma, eps=1e-6):
    x32 = x.float()                                       # upcast: the sum of squares is a long reduction
    rms = torch.sqrt(x32.pow(2).mean(-1, keepdim=True) + eps)
    return (gamma * (x32 / rms)).to(x.dtype)

x = torch.tensor([[1., 2., 3., 4.]])                     # one token, d_model = 4
g, b = torch.ones(4), torch.zeros(4)
print(layer_norm(x, g, b))      # [-1.3416, -0.4472, 0.4472, 1.3416]   mean 2.5, std sqrt(1.25)
print(rms_norm(x, g))           # [ 0.3651,  0.7303, 1.0954, 1.4606]   rms = sqrt(7.5) = 2.7386
print(layer_norm(3 * x, g, b))  # same as layer_norm(x): scale invariance
print(rms_norm(x + 10, g))      # [0.8765, 0.9562, 1.0359, 1.1155]: RMSNorm does NOT remove an offset
print(torch.allclose(torch.nn.LayerNorm(4)(x), layer_norm(x, g, b)))   # True (gamma=1, beta=0 at init)

class PreLNBlock(torch.nn.Module):     # norm on the sublayer INPUT; the residual stream is untouched
    def __init__(self, d, attn, mlp):
        super().__init__()
        self.n1, self.n2 = torch.nn.RMSNorm(d), torch.nn.RMSNorm(d)   # torch.nn.RMSNorm: PyTorch 2.4+
        self.attn, self.mlp = attn, mlp
    def forward(self, x):
        x = x + self.attn(self.n1(x))
        return x + self.mlp(self.n2(x))
```

## Trade-offs
- RMSNorm saves one reduction and the bias parameters per layer and matches LayerNorm quality in the
  published and open-model evidence; it keeps the mean, which matters only if a sublayer's input develops
  a large common offset that nothing else removes.
- Pre-LN trains deep models with short warmup and higher learning rates, but lets the residual stream's
  norm grow with depth so late layers contribute proportionally less; Post-LN keeps the stream normalized
  and has been reported to reach slightly better final quality at equal depth when it can be trained at
  all.
- Per-token normalization is batch-independent, which makes training and inference identical and
  removes any need for synchronized statistics, but it cannot use cross-example information the way
  BatchNorm does, a loss only in vision-style settings with large homogeneous batches.
- A smaller `ε` normalizes near-zero vectors more faithfully while a larger one is safer in 16-bit
  arithmetic; the value is baked into the checkpoint, so an inference engine must use the trained one.
- QK-norm and double (sandwich) norms buy stability against attention-logit growth at large scale or
  high learning rate, at the cost of extra norm ops in the attention path and a departure from the plain
  layout that pretrained weights and fused kernels assume.

## Pitfalls
- Normalizing over the wrong axis: `LayerNorm(normalized_shape)` normalizes the trailing dimensions, so a
  `[B, d, T]` tensor is normalized across time instead of features; the model trains but worse, and the
  first clue is a norm whose parameter count equals the sequence length.
- Computing the statistics in bf16 or fp16 in a custom implementation; the symptom is a slow quality gap
  against the reference model (bf16) or NaNs after a few hundred steps (fp16 overflow in the sum of
  squares). Upcast to fp32 inside the op.
- Applying weight decay to norm gains and biases because the optimizer was handed `model.parameters()` as
  one group; the gains drift toward zero and per-layer effective learning rates wander. Put norm and bias
  parameters in a no-decay group.
- Porting weights between implementations that differ in detail: whether the gain is `1 + w` or `w`,
  whether the multiply by `γ` happens before or after casting back to bf16, and the `ε` value. The symptom
  is logits that match to two digits and generations that diverge after a few hundred tokens.
- Forgetting the final norm before the output head in a Pre-LN model; the raw residual stream has a norm
  that grows with depth, so logits are huge, the softmax saturates, and the loss sits high from step one.
- Copying a Post-LN model's warmup and learning rate to a Pre-LN model, or the reverse: the first wastes
  thousands of steps at a needlessly low rate, the second diverges from an overly aggressive start.

## When to use
You do not choose whether a transformer has normalization; you choose which, where, and how it is
computed. For new decoder models, Pre-LN with RMSNorm, fp32 statistics, a final norm before the head and
no weight decay on norm parameters is the default the open-weight ecosystem has converged on; add QK-norm
when training at large scale or with aggressive learning rates and attention logits are seen growing. For
fine-tuning or serving existing models, match the checkpoint exactly: norm type, `ε`, gain
parameterization and cast order are part of the model. Measure the residual-stream norm per layer and the
attention-logit magnitudes during training; both growing without bound predicts a loss spike. In
interviews, be ready to write both formulas, explain why per-token statistics beat per-batch ones for
sequences, and derive why Pre-LN needs less warmup from the gradient path through the skip connection.

## Role lens
- **Engineer:** Implements or configures the norm exactly as the checkpoint expects (type, `ε`, fp32
  statistics, gain convention) and excludes norm parameters from weight decay in the optimizer groups.
- **Architect:** Fixes the block layout (Pre-LN, RMSNorm, QK-norm or not) for an organization's model
  family, since it determines the warmup and learning-rate regime every downstream run inherits.
- **FDE:** When a customer's self-hosted port of an open model gives subtly different outputs from the
  reference, checks the norm implementation first, because `ε` and cast-order mismatches are the usual
  cause.

## Check yourself
**Q:** A team builds a 48-layer decoder with the original Post-LN layout and finds it only trains with a
10,000-step warmup and a peak learning rate a quarter of what a comparable Pre-LN model uses. Explain the
mechanism, what you would change, and what you give up.
**A:** In Post-LN the residual stream passes through a LayerNorm at every layer, so the gradient reaching
layer k is the loss gradient multiplied by a chain of per-layer Jacobians of norms and sublayers. At
initialization that chain is badly balanced: layers nearest the output receive gradients whose expected
norm grows with depth, so a full-size update there is far too large while early layers see small
gradients. The long warmup and the low peak rate are compensating for that imbalance, not fixing it.
Moving the norm to the sublayer input (Pre-LN) makes the skip path an identity from the embeddings to the
final norm, so gradients at initialization are similar in size at every depth; warmup can drop to a few
hundred steps and the peak rate can rise. What you give up is a normalized residual stream: its norm now
grows with depth, so late layers contribute proportionally less, and you must add a final norm before the
head and probably QK-norm or sandwich norms at larger scale. Some reports find Post-LN reaches a slightly
better final loss when it can be trained, but at 48 layers trainability dominates.

## Quiz
### Q: What does RMSNorm remove relative to LayerNorm, and why is that acceptable?
- [ ] The learned gain, because the next linear layer can absorb any scaling
- [x] The mean subtraction and the bias, because the stabilizing effect comes from rescaling the vector, not from re-centering it
- [ ] The epsilon term, because the root mean square is never zero
- [ ] The per-token statistics, replacing them with running averages
> Both norms divide by a scale computed over the token's own features; RMSNorm computes that scale
> without first subtracting the mean. Experiments show re-centering contributes little, so dropping it
> saves a reduction at no quality cost.

### Q: Why do transformers use LayerNorm or RMSNorm rather than BatchNorm?
- [ ] BatchNorm has no learnable parameters, so it limits model capacity
- [ ] LayerNorm is cheaper because it needs no division
- [x] Per-token statistics are independent of batch composition, padding and sequence length, and are identical at training and inference
- [ ] BatchNorm cannot be used together with residual connections
> BatchNorm's statistics depend on which examples share the batch and are replaced by running averages
> at inference, which breaks down with variable-length sequences and small micro-batches. Per-token
> normalization uses only the token's own vector.

### Q: Why does a Pre-LN transformer tolerate a much shorter warmup than a Post-LN one?
- [ ] Pre-LN has fewer normalization layers, so fewer parameters need to settle
- [ ] Pre-LN normalizes the gradients directly during backpropagation
- [x] In Pre-LN the residual path is an identity from the embeddings to the final norm, so gradients at initialization are similar in size across layers instead of growing toward the output
- [ ] Pre-LN uses RMSNorm, which has no bias to warm up
> Post-LN puts a norm on the residual stream at every layer, which at initialization makes gradients near
> the output grow with depth; warmup compensates for that. Pre-LN leaves the skip path untouched, so the
> imbalance never arises. RMSNorm versus LayerNorm is independent of placement.
