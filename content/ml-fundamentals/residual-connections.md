---
id: residual-connections
module: ml-fundamentals
name: Residual connections
kind: concept
difficulty: core
est_minutes: 12
status: reviewed
related:
  - ml-fundamentals/normalization
  - ml-fundamentals/backpropagation
  - ml-fundamentals/gradient-clipping
  - ml-fundamentals/warmup
  - model-internals/transformer
  - model-internals/self-attention
  - training-adaptation/peft
sources:
  - He et al., 2016 — Deep Residual Learning for Image Recognition
  - He et al., 2016 — Identity Mappings in Deep Residual Networks
  - Veit et al., 2016 — Residual Networks Behave Like Ensembles of Relatively Shallow Networks
  - Elhage et al., 2021 — A Mathematical Framework for Transformer Circuits
---

## TL;DR
A residual connection adds a layer's input to its output, `y = x + F(x)`, so each layer learns a
correction to the signal rather than a replacement for it. It exists because plain deep stacks get harder
to optimize as depth grows: the identity path gives gradients a route that products of layer Jacobians
cannot shrink, and makes "do nothing" the starting point for every layer. In a transformer this shared
path is the residual stream that every attention head and MLP reads and writes.

## Why it exists
The expectation in 2014–2015 was that a deeper network should be at least as good as a shallower one: a
56-layer network can represent anything a 20-layer one can by making 36 layers compute the identity. The
experiment said otherwise. He et al. trained plain convolutional stacks of 20 and 56 layers on CIFAR-10
and the deeper one had *higher training error*, not just higher test error. That is not overfitting (the
model with more capacity fit the training data worse) and not vanishing gradients in the classic sense
(both networks used batch normalization and sound initialization, and gradients were not zero). It was an
optimization failure: gradient descent, started from a random point, could not find a solution that was
known to exist. Learning an identity through a stack of randomly initialized nonlinear layers is far
from trivial, because each layer has to undo what the next one does.

The alternatives already in use did not fix it. Careful initialization keeps activation scale constant at
step zero but says nothing about step one thousand. Normalization keeps each layer's input well-scaled
but still asks every layer to transform its input. Training longer did not close the gap. What worked was
to change what a layer is asked to learn: instead of the desired mapping `H(x)`, learn the residual
`F(x) = H(x) − x` and add the input back. If the best mapping is close to the identity, `F` is close to
zero, which is exactly where small random weights start; the network begins as roughly the identity and
each layer only has to learn a perturbation. Depth stopped being an optimization liability, networks of
more than a hundred layers trained without difficulty, and the transformer adopted the same block
structure within two years and has never dropped it.

## How it works
### Forward: additive composition
A residual block computes `y = x + F(x, W)`, where `F` is the sublayer: a pair of convolutions in a
ResNet, attention or the MLP in a transformer. Input and output must have the same shape so they can be
added, which is why a ResNet uses a projection shortcut only where width or resolution changes, and why a
transformer keeps `d_model` constant through every layer: the whole network is one vector of fixed width
that successive layers add to. Stack `L` blocks and the output is a sum:

```
x_L = x_0 + Σ_{l<L} F_l(x_l)
```

Each layer's contribution is a *term in a sum* rather than a *factor in a product*. That structural fact
is behind everything else.

### Backward: the identity term
Differentiate one block: `∂y/∂x = I + ∂F/∂x`. Through `L` blocks, the gradient of the loss with respect to
an early activation is

```
∂L/∂x_0 = ∂L/∂x_L × Π_l (I + J_l)        with  J_l = ∂F_l/∂x_l
```

Expand the product and it contains a pure `I` term, every single `J_l`, every pair, and so on: `2^L`
paths of different lengths. A plain network has only one path, the product of all `L` Jacobians, and a
product of many matrices with norm below one shrinks exponentially while one with norms above one
explodes; residuals keep that path but add all the shorter ones beside it. The gradient reaching layer 0
therefore always contains `∂L/∂x_L` itself, undiminished, plus corrections. Veit et al. measured this on
trained ResNets: most of the gradient flows through paths only a few tens of layers long even in a
110-layer network, which is why deleting a single block at test time barely hurts a ResNet while deleting
a layer from a plain network destroys it.

### Why the shortcut must be the identity
The follow-up paper on identity mappings tested alternatives: scaling the skip path by a constant, gating
it as highway networks did, placing a 1×1 convolution or dropout on it. Every variant trained worse,
because anything multiplicative on the skip path turns the clean `I` back into a factor that compounds
across depth. The same reasoning says normalization belongs on the branch input (Pre-LN), not on the
stream after the addition (Post-LN), when easy optimization is the goal; the normalization lesson covers
that placement and what it costs.

### The residual stream in a transformer
Read a decoder layer through this lens: `x ← x + Attn(Norm(x))`, then `x ← x + MLP(Norm(x))`. The vector
`x`, of width `d_model`, is the *residual stream*. Attention heads and MLPs read from it through their
input projections and write back through their output projections, and because all writes are additive
the stream behaves like a shared bus that later layers can read in any combination. Several practical
techniques depend on this picture. The logit lens applies the final norm and the unembedding to an
intermediate `x` and gets meaningful predictions, because the stream at layer 12 is already a rough draft
of the final one. LoRA adds a low-rank term to a projection and activation steering adds a vector to the
stream at one layer, and both produce a predictable shift downstream because the stream is a sum. Early
exit and layer dropping work because any prefix of the sum is a usable, if coarser, output.

### What it costs and what it changes
Because every layer adds, the norm of the stream grows with depth. Deep Pre-LN models therefore scale
down the initialization of each branch's output projection (GPT-2 divides those weights by the square
root of the number of residual layers) so the sum starts at a sensible scale, and some recipes add a
learned scalar per branch initialized near zero. The stream's activations must be stored for backward at
every layer, `[batch, seq, d_model]` per block, a visible slice of activation memory. And fixed width
means capacity is spent uniformly across depth; architectures that want to change width mid-network pay
with projection shortcuts at those points.

## Example
A 64-layer stack of `tanh(Linear)` blocks with and without the skip connection, comparing the gradient
norm at the first layer with the gradient norm at the last. The loss has a random target so the gradient
at the output is of order one in both cases; what differs is how much of it reaches layer 1.

```python
import torch, torch.nn as nn
torch.manual_seed(0)
d, depth = 256, 64

class Block(nn.Module):
    def __init__(self, residual):
        super().__init__()
        self.lin, self.residual = nn.Linear(d, d), residual
    def forward(self, x):
        f = torch.tanh(self.lin(x))                 # the branch F(x)
        return x + f if self.residual else f        # y = x + F(x)   or   y = F(x)

def first_vs_last_grad(residual):
    net = nn.Sequential(*[Block(residual) for _ in range(depth)])
    x, target = torch.randn(64, d), torch.randn(64, d)
    nn.functional.mse_loss(net(x), target).backward()
    return net[0].lin.weight.grad.norm().item(), net[-1].lin.weight.grad.norm().item()

for residual in (False, True):
    first, last = first_vs_last_grad(residual)
    print(f"residual={residual!s:5} layer 1: {first:.1e}  layer {depth}: {last:.1e}  ratio: {first/last:.1e}")
# One CPU run, seed 0 (exact digits vary by version; the orders of magnitude do not):
#   residual=False  layer 1: 1.5e-16  layer 64: 1.1e-02  ratio: 1.3e-14
#   residual=True   layer 1: 1.9e+01  layer 64: 4.3e+00  ratio: 4.4e+00
# Plain stack: the layer-1 gradient is ~14 orders of magnitude below layer 64's, because it is the
#   product of 64 Jacobians each with norm below one (tanh' <= 1 and the default init shrinks variance).
# Residual stack: the two are within a small factor of each other, because the identity term in every
#   (I + J_l) carries dL/dx_64 straight back to layer 1 regardless of depth.
```

## Trade-offs
- Depth becomes optimizable, so hundreds of layers train with ordinary SGD or Adam, but every layer must
  keep the same width, and changing representation size mid-network needs projection shortcuts that put a
  learned factor back on the skip path.
- The identity path keeps gradients alive without any tuning, yet the additive stream grows in norm with
  depth, which is why Pre-LN models need scaled branch initialization and a final norm, and why late
  layers in very deep models still contribute less.
- Short gradient paths make the network robust to removing or reordering individual blocks, which
  enables pruning, early exit and layer sharing, but the same redundancy is why very deep, narrow models
  can leave late layers underused; whether the next parameter is better spent on depth or width is an
  empirical, model-family question, not something the residual argument settles.
- The residual stream must be materialized and stored per layer for backward, costing
  `batch × seq × d_model` activations per block; gradient checkpointing trades recompute for that memory
  when long sequences make it dominant.
- Gated or scaled shortcuts add flexibility and can train without normalization, but any non-identity
  factor on the skip path compounds across depth and in practice trains worse than the plain identity
  unless the gate starts as pass-through.

## Pitfalls
- Putting dropout, a projection or a scaling factor on the skip path instead of on the branch output; the
  model trains noticeably slower and deeper configurations stop converging, which is the identity-mappings
  result rediscovered.
- Not scaling the branch output projections at initialization in a deep Pre-LN model; the stream norm
  at step zero grows steadily with layer index (log it per layer to see it), the final norm hides this from
  the logits, and the symptom is a run that is less stable than the recipe predicts, with early loss
  spikes at the target learning rate.
- Letting shapes drift (a sublayer that changes the hidden size) and "fixing" it with a learned projection
  on the residual path at every layer; the network is now effectively plain, and the gradient-path
  argument no longer holds.
- Placing normalization after the addition in a new architecture because the original paper did, then
  needing thousands of warmup steps; check whether the path through the stream is still an identity.
- Implementing a residual add across a dimension mismatch with zero-padding or truncation and silently
  discarding part of the signal; the symptom is a deep model that matches a shallower baseline exactly
  because the extra layers contribute nothing.
- Treating the residual stream as "the layer's output" when hooking activations for interpretability or
  steering: the stream at layer k is the sum of all previous writes, not what layer k computed; read the
  branch output if the layer's own contribution is what you want.

## When to use
Any network more than a handful of layers deep should use residual connections, and every transformer
does; the decision space is how they are arranged. Keep the skip path an identity, normalize the branch
input, scale branch initialization with depth, and fix `d_model` for the whole stack. Reach for the
residual-stream picture when debugging deep-model training (plot each layer's branch-output norm against
the stream norm: a branch at 1% of the stream is a layer doing little), when deciding where to attach
adapters or steering vectors, and when evaluating early-exit or layer-pruning proposals, since all three
rest on the additive structure. In interviews, the degradation-problem story, the `I + J` Jacobian
argument and the connection to Pre-LN placement are the expected depth; explaining why a gate on the
shortcut is harmful separates recall from understanding.

## Role lens
- **Engineer:** Writes blocks as `x + f(norm(x))`, scales output projections at init, hooks the residual
  stream rather than branch outputs when a technique needs it, and reads per-layer contribution norms when
  a deep model trains poorly.
- **Architect:** Treats the residual stream's width and depth as the capacity budget of a model family and
  decides where depth-related techniques (early exit, layer pruning, adapter placement) are allowed to
  intervene.
- **FDE:** Uses the additive-stream picture to explain why dropping or sharing layers of an open model for
  latency keeps most of its quality, and to place LoRA adapters where their effect is predictable.

## Check yourself
**Q:** A colleague proposes making each transformer block's skip connection learnable, `y = α·x + F(x)`
with `α` trained per layer, arguing it gives the model more flexibility. What do you expect in a 48-layer
model, and why?
**A:** The gradient reaching layer k is `∂L/∂x_L` multiplied by `Π (α_l I + J_l)` over the layers above
it. With `α` fixed at 1 that product always contains an undiminished identity term; with learned `α` the
identity path acquires the factor `Π α_l`, which across 48 layers is tiny if the values drift below one
and huge if they drift above, and nothing in the loss pulls them to exactly one, because the loss has no
direct stake in protecting the gradient path. The identity-mappings experiments tried exactly this on
ResNets (constant scaling, gating, projections on the shortcut) and every variant trained worse than the
plain identity, with the gap widening with depth. The flexibility argument is also weak: `F` already ends
in an output projection, so any per-layer scaling the model wants is available on the branch without
touching the skip. If the real goal is stability at depth, the better tools are scaled branch
initialization, Pre-LN placement, or a learned scalar on the *branch* initialized at zero, all of which
leave the skip path clean.

## Quiz
### Q: Why does a residual connection keep gradients from vanishing through depth?
- [ ] It normalizes the gradient at every layer
- [x] Each block's Jacobian is `I + ∂F/∂x`, so the product across layers always contains an identity term that passes the gradient back unchanged
- [ ] It doubles the learning rate for early layers
- [ ] It replaces the nonlinearity with a linear function in the backward pass
> Expanding the product of `(I + J_l)` terms yields paths of every length, including the direct one; a
> plain network has only the full-length product, which shrinks or explodes exponentially with depth.

### Q: Why is `d_model` the same in every layer of a transformer?
- [ ] Attention requires square weight matrices
- [ ] Positional encodings only work at a fixed width
- [x] The residual addition `x + F(x)` requires input and output of the same shape, so the stream keeps one width from the embeddings to the output head
- [ ] Tensor cores are only efficient at powers of two
> Each sublayer writes into the stream by addition, which needs matching shapes. Networks that change
> width, such as ResNets at stage boundaries, must insert projection shortcuts there, which transformers
> avoid entirely.

### Q: What did the "identity mappings" experiments show about modifying the skip path?
- [ ] A learned gate on the shortcut improves accuracy by letting layers be skipped
- [ ] Adding dropout to the shortcut regularizes deep networks better
- [x] Any scaling, gating or projection on the shortcut trained worse than the plain identity, because the factor compounds across depth
- [ ] The shortcut can be replaced by a 1×1 convolution at no cost
> Multiplicative changes to the skip path turn the clean identity term in the gradient back into a
> product, undoing the benefit; all tested variants degraded training, and increasingly so with depth.
