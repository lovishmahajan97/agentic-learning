---
id: backpropagation
module: ml-fundamentals
name: Backpropagation
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - ml-fundamentals/gradient-descent
  - ml-fundamentals/loss-functions
  - ml-fundamentals/optimizers
  - ml-fundamentals/gradient-clipping
  - ml-fundamentals/residual-connections
  - ml-fundamentals/normalization
  - training-adaptation/gradient-checkpointing
  - python-for-ai/pytorch
sources:
  - Rumelhart, Hinton & Williams, 1986 — Learning Representations by Back-Propagating Errors
  - Baydin et al., 2018 — Automatic Differentiation in Machine Learning: a Survey
  - Griewank & Walther, 2008 — Evaluating Derivatives
  - Official docs: PyTorch autograd
---

## TL;DR
Backpropagation computes the gradient of a scalar loss with respect to every parameter in one backward
sweep, by applying the chain rule from the loss back through the recorded computation graph. It costs a
small constant multiple of the forward pass no matter how many parameters there are, which is the only
reason training billion-parameter models is possible. The price is memory: the backward pass needs the
forward activations, so training memory scales with batch × sequence length × depth, not just with
parameter count.

## Why it exists
Gradient descent needs ∇L, one partial derivative per parameter. The obvious way to get it is finite
differences: nudge a parameter, re-run the forward pass, measure the change. That is P + 1 forward
passes per step; for a model with seven billion parameters it is seven billion forward passes to make one
update, and the result is numerically poor besides. Symbolic differentiation of the whole network
produces expressions whose size explodes with depth. Forward-mode automatic differentiation, which
carries derivatives alongside values through the forward pass, is exact and cheap per direction, but it
computes the derivative along one input direction at a time, so it also needs P passes.

The decisive observation is the shape of the problem: one output (the scalar loss) and millions of
inputs (the parameters). Reverse-mode differentiation propagates "how sensitive is the loss to this
value" backwards from the output, and because there is a single output, one backward sweep yields the
sensitivity of the loss to every intermediate value and every parameter. Its cost is a small constant
times the forward pass, independent of P. Backpropagation is reverse-mode automatic differentiation
specialised to layered networks; it was derived several times before the 1986 paper made it standard.
It is not a learning algorithm by itself. It computes gradients; gradient descent and its descendants
decide what to do with them.

## How it works
### The computation graph and adjoints
A forward pass is a sequence of primitive operations, each recording its inputs, its output and which
operation it was. The loss L sits at the end. For every intermediate value v define its adjoint
v̄ = ∂L/∂v, with L̄ = 1. Walk the graph in reverse topological order. For an operation y = f(x₁, …, xₙ),
add to each input's adjoint the product of the upstream adjoint and the local derivative:
x̄ᵢ += ȳ · ∂f/∂xᵢ. The products are vector–Jacobian products, never full Jacobians, which is what keeps
the cost linear. The addition matters: a value used in several places collects a contribution from each
use, which is the multivariable chain rule. When the sweep reaches the parameters, their adjoints are the
gradient.

A three-line case makes the fan-out rule concrete. Let a = 3, b = a², c = 2a, L = b + c. Forward:
b = 9, c = 6, L = 15. Backward: L̄ = 1, so b̄ = 1 and c̄ = 1; a receives b̄·2a = 6 from the square and
c̄·2 = 2 from the doubling, so ā = 8, which matches dL/da = 2a + 2 = 8. Forgetting the "+=" and keeping
only the last contribution would give 2 or 6, a silently wrong gradient.

### What the framework actually does
In PyTorch the graph is recorded during the forward pass itself: every tensor produced by a
differentiable op carries a `grad_fn` pointing at the op and the values it saved. `loss.backward()`
walks those pointers in reverse, writes each parameter's adjoint into `.grad`, and then frees the graph
and its saved tensors (unless `retain_graph=True`). `.grad` is *added to*, not overwritten, which is the
same += rule applied across calls; that is why `zero_grad()` is needed between steps and why gradient
accumulation over several micro-batches works by simply calling `backward()` several times before
`step()`.

### Why reverse rather than forward
Forward mode costs one pass per input direction; reverse mode costs one pass per output. With one
scalar output and P inputs, reverse mode wins by a factor of P. The cheap-gradient result from automatic
differentiation theory bounds the backward sweep by a small constant multiple of the forward cost. For
matrix-multiply-dominated networks it is about twice the forward FLOPs, because each linear layer
needs two matmuls in the backward pass against one in the forward pass. That is the origin of the rule
of thumb that a training step costs about three forward passes.

### A linear layer, worked out
For y = xW with x of shape [B, d_in] and W of shape [d_in, d_out], given the upstream adjoint ȳ of shape
[B, d_out]: W̄ = xᵀȳ (shape [d_in, d_out]) and x̄ = ȳWᵀ (shape [B, d_in]). Note what W̄ needs: x, the
layer's input activation from the forward pass. ReLU's backward is x̄ = ȳ ⊙ 1[x > 0], which needs the
sign of its input. Softmax followed by cross-entropy has the fused backward z̄ = q − onehot(y). Attention
and normalisation layers are more involved but follow the same recipe: a local derivative applied to the
upstream adjoint, using saved forward values.

### Where training memory goes
Each saved forward value is memory held from the forward pass until its backward step. Per layer that is
several tensors of shape [batch, sequence, hidden], so activation memory scales with B × T × d × depth
and for long sequences dwarfs the parameters. Gradient checkpointing stores only each block's input and
recomputes the interior during the backward pass, trading roughly a third more compute for a large
memory reduction. Fused attention kernels recompute attention probabilities in the backward pass rather
than storing the T × T matrix. Freezing weights, as in LoRA, removes their gradient and optimizer-state
memory, but the backward sweep still has to pass through the frozen layers to reach trainable ones
earlier in the network, so activation memory does not disappear. What is skipped is the weight-gradient
matmul for each frozen layer, roughly half the backward FLOPs, so compute per step falls by at most about a
third, nowhere near in proportion to the parameter count.

### Vanishing and exploding gradients
The adjoint at the first layer is the upstream adjoint multiplied by the Jacobian of every layer above
it. A product of many matrices shrinks or grows exponentially. A sigmoid's derivative is at most 0.25, so
twenty sigmoid layers scale the gradient by at most 0.25²⁰ ≈ 10⁻¹²; early layers simply stop learning.
Every standard remedy is a response to this arithmetic: ReLU and GELU have derivative near 1 on their
active side; residual connections make each block's Jacobian I plus something small, so the product
stays near the identity and gradients have a highway to early layers; normalisation keeps activation
scales from drifting; variance-preserving initialisation starts the product near 1; clipping bounds the
explosions that remain.

### Where the graph stops
Argmax, sampling, string operations and external tool calls have no useful derivative, so you cannot
backpropagate through "generate a response and score it". That gap is why policy-gradient methods exist
for RLHF, and why straight-through estimators and Gumbel-softmax are used when a discrete step must sit
inside a differentiable model. `detach()` cuts the graph deliberately, for example to stop gradients
flowing into a target network or a teacher.

## Example
A two-layer network with ReLU and softmax cross-entropy, differentiated by hand and checked against a
finite difference. Each backward line is one local derivative multiplied by the upstream adjoint; the
numeric check should agree to around 1e-10.

```python
import numpy as np
rng = np.random.default_rng(0)
X = rng.normal(size=(8, 4)); y = rng.integers(0, 3, size=8)            # 8 examples, 4 features, 3 classes
W1 = rng.normal(scale=0.5, size=(4, 5)); W2 = rng.normal(scale=0.5, size=(5, 3))

def forward(W1, W2):
    h_pre = X @ W1; h = np.maximum(h_pre, 0)                          # linear, ReLU
    logits = h @ W2
    p = np.exp(logits - logits.max(1, keepdims=True)); p /= p.sum(1, keepdims=True)
    loss = -np.log(p[np.arange(8), y]).mean()                         # cross-entropy
    return loss, (h_pre, h, p)

loss, (h_pre, h, p) = forward(W1, W2)

# backward: reverse order, each step = local derivative x upstream adjoint
d_logits = p.copy(); d_logits[np.arange(8), y] -= 1; d_logits /= 8     # dL/dlogits = (p - onehot) / N
dW2 = h.T @ d_logits                                                   # logits = h @ W2  -> needs h
d_h = d_logits @ W2.T
d_hpre = d_h * (h_pre > 0)                                             # ReLU gate     -> needs h_pre
dW1 = X.T @ d_hpre                                                     # h_pre = X @ W1 -> needs X

# finite-difference check on one weight
i, j, eps = 1, 2, 1e-5
W1p, W1m = W1.copy(), W1.copy(); W1p[i, j] += eps; W1m[i, j] -= eps
numeric = (forward(W1p, W2)[0] - forward(W1m, W2)[0]) / (2 * eps)
print(dW1[i, j], numeric)        # agree to ~1e-10; the saved h, h_pre, X are the "activation memory"
```

## Trade-offs
- Reverse mode gives every gradient for about twice the forward compute, but must keep forward
  activations alive; when memory binds, checkpointing trades recompute for memory, and forward-mode
  (Jacobian–vector products) wins only when you need a few directional derivatives rather than the full gradient.
- Dynamic graphs built eagerly are flexible and easy to debug with ordinary Python, while compiled
  graphs fuse operations and run faster; the compiled route pays off once the model is stable and the
  step time matters more than iteration speed.
- Freezing most weights cuts gradient and optimizer memory sharply, but the backward pass still
  traverses the frozen layers, so step time (at most about a third less) and activation memory fall far
  less than parameter counts suggest.
- Fused kernels that recompute intermediate results in the backward pass trade FLOPs for memory
  bandwidth and capacity; they win when the saved tensors would dominate memory, as attention does at long sequence lengths.

## Pitfalls
- Forgetting `optimizer.zero_grad()`, so gradients accumulate across steps; the symptom is a gradient
  norm that grows step by step and a run that diverges without any change in the data.
- Modifying a tensor in place after it was saved for backward; the symptom is the autograd error about a
  variable needed for gradient computation having been modified by an in-place operation.
- Breaking the graph by converting to NumPy, calling `.item()` or using a leaf created with
  `requires_grad=False` in the middle of the model; the symptom is parameters that never change.
- Vanishing gradients in a deep stack without residuals or normalisation; the symptom is per-layer
  gradient norms that fall by orders of magnitude from the last layer to the first.
- Keeping references to the loss tensor (for logging) instead of `loss.item()`, which keeps the whole
  graph alive; the symptom is memory growing every step until an out-of-memory error.
- Gradient-checking in fp16 or bf16; the symptom is "mismatches" that are nothing but rounding noise. Do
  the check in float64.

## When to use
You do not choose backpropagation; the framework performs it. What you choose follows from its
arithmetic: which parameters to freeze, where to checkpoint, what sequence length and batch fit in
memory, when a custom autograd function is needed for a non-differentiable step, and how to debug a
model that is not learning. Measure per-layer gradient norms with hooks and peak activation memory with
the framework's memory profiler. In interviews, be able to run the chain rule through a two-layer network
on a whiteboard, explain why reverse mode beats forward mode, account for where training memory goes, and
say why residual connections keep gradients alive. Those four questions cover most of what is asked.

## Role lens
- **Engineer:** Debugs "the model is not learning" with gradient-norm hooks, fixes graph breaks and
  in-place errors, and turns on gradient checkpointing to fit a longer sequence length.
- **Architect:** Sizes training hardware from the memory accounting of parameters, gradients, optimizer
  state and activations, and decides between full fine-tuning and PEFT partly from that arithmetic.
- **FDE:** When a customer's fine-tuning job runs out of memory on their GPUs, explains activation memory
  and adjusts checkpointing, sequence length and batch size rather than demanding bigger hardware.

## Check yourself
**Q:** A twelve-layer MLP with sigmoid activations trains its last two layers well, but the weights of
the early layers barely move. Explain mechanically what is happening and what you would change.
**A:** Backpropagation multiplies the upstream adjoint by each layer's Jacobian on the way down. A
sigmoid's derivative is at most 0.25 and typically smaller, and with modest weights each layer's
Jacobian has norm well below one, so after ten layers the gradient has shrunk by many orders of
magnitude; the early layers receive almost no signal while the top layers learn normally. It is not a
data problem and not simply a learning-rate problem, since raising the rate enough for the early layers
would blow up the late ones. Replace sigmoids with ReLU or GELU, add residual connections so each block's
Jacobian is near the identity, add normalisation, and use variance-preserving initialisation. Confirm the
diagnosis before and after by logging per-layer gradient norms.

## Quiz
### Q: Why is reverse-mode automatic differentiation the right choice for training neural networks?
- [ ] It is more numerically precise than forward mode
- [x] One backward pass yields the derivative of the single scalar loss with respect to all parameters, whereas forward mode needs one pass per parameter
- [ ] It does not need to store any forward activations
- [ ] It avoids the chain rule entirely
> The cost of reverse mode scales with the number of outputs, and training has exactly one, the loss.
> Forward mode scales with the number of inputs, which is every parameter. Reverse mode does require
> storing activations, which is its main cost.

### Q: For long-sequence fine-tuning of a mid-sized transformer, where does most of the training memory go?
- [ ] The model weights themselves
- [x] Saved activations, which scale with batch × sequence length × depth
- [ ] The metadata of the computation graph
- [ ] The gradients of the embedding table
> Every layer saves several tensors of shape [batch, sequence, hidden] for its backward step, so
> activation memory grows with sequence length while weights do not. This is why gradient checkpointing
> and fused attention kernels exist.

### Q: You freeze all weights except small LoRA adapters. What happens to the cost of the backward pass?
- [ ] The backward pass becomes essentially free
- [x] Gradient and optimizer memory for the frozen weights disappears, but the backward sweep still propagates through them to reach the adapters, so compute per step drops by at most about a third
- [ ] Activations no longer need to be stored at all
- [ ] The forward pass becomes twice as cheap
> Adapters in early layers need the adjoint of the loss with respect to their outputs, which can only be
> obtained by backpropagating through every frozen layer above them. What is skipped is the weight-gradient
> matmul (about half the backward FLOPs) and the optimizer state for frozen parameters.
