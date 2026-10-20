---
id: optimizers
module: ml-fundamentals
name: Optimizers (Adam, AdamW)
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - ml-fundamentals/gradient-descent
  - ml-fundamentals/backpropagation
  - ml-fundamentals/learning-rate-schedulers
  - ml-fundamentals/warmup
  - ml-fundamentals/gradient-clipping
  - ml-fundamentals/mixed-precision
  - training-adaptation/learning-rate
  - training-adaptation/peft
sources:
  - Kingma & Ba, 2015 — Adam: A Method for Stochastic Optimization
  - Loshchilov & Hutter, 2019 — Decoupled Weight Decay Regularization
  - Rajbhandari et al., 2020 — ZeRO: Memory Optimizations Toward Training Trillion Parameter Models
  - Official docs: PyTorch torch.optim
---

## TL;DR
An optimizer turns gradients into parameter updates. Plain SGD applies one learning rate to every
parameter, which fails when gradient scales differ by orders of magnitude across a network. Adam keeps a
running mean (momentum) and a running mean square of each parameter's gradient and divides one by the
root of the other, so every parameter moves by roughly the learning rate whatever its gradient scale.
AdamW applies weight decay directly to the weights instead of through the gradient, and is the default
for transformers.

## Why it exists
Inside a transformer the gradients of different parameters live on different scales. The embedding row
of a rare token receives a gradient only when that token appears, and a small one; layer-norm gains,
attention projections and output heads each see their own typical magnitude. With a single learning
rate, either it is right for the large-gradient parameters and the small-gradient ones barely move, or it
is right for the small ones and the large ones diverge. On top of that, plain gradient descent crawls
along low-curvature directions for the condition-number reasons covered in the gradient-descent lesson.

The answer is per-parameter adaptation. AdaGrad divided each gradient by the square root of the sum of
its past squares, so rarely updated parameters got larger steps, but the sum grows forever and the
effective learning rate decays to zero. RMSProp replaced the sum with an exponential moving average.
Adam combined that moving average of squared gradients with a moving average of the gradients themselves
(momentum) and added a correction for the fact that both averages start at zero. It became the default
because it made the learning rate far less sensitive to the problem.

Then weight decay went wrong. The standard regulariser, an L2 penalty on the weights added to the loss,
enters Adam through the gradient and is therefore divided by the same per-parameter scale, which
quietly turns a uniform shrinkage into one that depends on gradient history. Loshchilov and Hutter
showed that decoupling the decay from the gradient restores the intended behaviour and improves
generalisation; that variant is AdamW, and it is what every modern transformer recipe uses.

## How it works
### The Adam update
For each parameter, with gradient g_t at step t:

```
m_t = β1·m_{t-1} + (1 − β1)·g_t          # first moment: EMA of gradients        (β1 = 0.9)
v_t = β2·v_{t-1} + (1 − β2)·g_t²         # second moment: EMA of squared gradients (β2 = 0.999 default)
m̂ = m_t / (1 − β1^t);  v̂ = v_t / (1 − β2^t)     # bias correction
θ_{t+1} = θ_t − η · m̂ / (√v̂ + ε)
```

Each piece has a reason. The first moment is momentum: it averages out mini-batch noise and
accumulates in consistent directions. The division by √v̂ normalises the step by the parameter's typical
gradient magnitude, so the update is about η · E[g]/√E[g²], whose magnitude is at most about 1·η. For a
gradient with a consistent sign the ratio is near 1 and the parameter moves by roughly η per step
whatever the gradient's scale, which is why Adam behaves like sign descent early on, why its learning
rates sit around 1e-4 to 1e-3 rather than SGD's 0.1, and why the choice is so much more forgiving.

Bias correction exists because m and v start at zero. After one step m₁ = 0.1·g₁ and v₁ = 0.001·g₁², so
the uncorrected ratio m/√v would be 0.1/0.0316 ≈ 3.2 times too large; dividing each by (1 − βᵗ) removes
the shrinkage. Even corrected, v is estimated from a handful of samples early in training and is noisy,
which is the reason learning-rate warmup is standard with Adam. The ε term prevents division by zero and
acts as a floor: parameters whose gradient RMS is far below ε behave as if trained by SGD with rate η/ε.
The default is 1e-8; some low-precision recipes raise it for stability.

### Weight decay: coupled L2 versus decoupled
With an L2 penalty the loss becomes L + (λ/2)‖θ‖², so the gradient becomes g + λθ. Under SGD this is
θ ← θ − ηg − ηλθ, a uniform shrink of every weight by the factor (1 − ηλ) per step, which is the intended
regulariser. Under Adam the term λθ enters m and v and is divided by √v̂. A weight with a large gradient
history has a large √v̂, so its decay is scaled down almost to nothing; a weight with tiny gradients has
λθ dominating both numerator and denominator, so the ratio is about sign(θ) and it decays at the full
rate regardless of λ. The regularisation strength has become a function of gradient statistics rather
than of λ.

A rough quantitative picture: near convergence a weight's loss gradient is mostly noise with RMS σ,
so m̂ ≈ λθ while √v̂ ≈ σ (when λθ ≪ σ), and the coupled update shrinks the weight by about ηλθ/σ per
step. The effective decay coefficient is λ/σ, not λ. With λ = 0.01, a weight whose gradient RMS is 1 is
decayed at 0.01 and one whose gradient RMS is 0.1 at 0.1, ten times harder, for no reason anyone chose.
Under AdamW both decay at exactly ηλ per step.

AdamW removes λθ from the gradient entirely and applies decay as its own step: θ ← θ − η(m̂/(√v̂ + ε) +
λθ), implemented in PyTorch as θ ← θ·(1 − ηλ) followed by the ordinary Adam step. Every weight now
shrinks by the same fraction per step, independent of its gradient history, and λ can be tuned without
re-tuning η. Two practical notes: `torch.optim.Adam(weight_decay=...)` is the coupled L2 form while
`torch.optim.AdamW` is decoupled, a frequent source of confusion; and by convention decay is not applied
to biases or normalisation gains, because shrinking a layer-norm scale toward zero has no regularising
meaning.

### Memory and compute
Adam keeps m and v, two fp32 values per parameter, so 8 bytes per parameter of optimizer state on top of
the weights. In mixed-precision training the usual accounting is 16 bytes per parameter: 2 for fp16
weights, 2 for fp16 gradients, 4 for an fp32 master copy, 4 for m and 4 for v. A 7-billion-parameter
model therefore needs about 112 GB before a single activation is stored, more than one 80 GB GPU. That
number drives the alternatives: 8-bit Adam quantises m and v; Adafactor stores a factored second moment
and drops the first; ZeRO and FSDP shard the optimizer state across GPUs; parameter-efficient fine-tuning
keeps state only for the adapter weights.

### Hyperparameters in practice
β1 = 0.9 almost everywhere. β2 = 0.999 is the default, but many LLM pre-training recipes use 0.95,
because a long memory in v reacts slowly when the gradient distribution shifts and that lag produces loss
spikes in long runs. Weight decay of 0.01 (the PyTorch default) to 0.1 (common in LLM recipes) on
matrices only. Peak learning rates for full fine-tuning are typically in the 1e-5 to 5e-5 range and for
LoRA adapters in the 1e-4 to 3e-4 range (illustrative, recipe-dependent), always with warmup, a decay
schedule and gradient clipping around 1.0. SGD with momentum still matches or beats Adam on some
convolutional benchmarks with a tuned schedule; newer optimizers such as Lion, Sophia and Muon trade
memory or steps against Adam and should be compared under equal compute before adoption.

## Example
A hand-written AdamW step compared against `torch.optim.AdamW` on the same parameters. The two must
agree exactly, which confirms the update rule above is the whole algorithm; the final print shows the
per-parameter step sizes settling near one learning rate each, whatever the gradient magnitudes.

```python
import torch
torch.manual_seed(0)
lr, b1, b2, eps, wd = 1e-3, 0.9, 0.999, 1e-8, 0.01

w = torch.randn(5, requires_grad=True)                   # hand-rolled AdamW updates this copy
w_ref = w.detach().clone().requires_grad_(True)           # torch.optim.AdamW updates this one
opt = torch.optim.AdamW([w_ref], lr=lr, betas=(b1, b2), eps=eps, weight_decay=wd)
m, v = torch.zeros(5), torch.zeros(5)                     # optimizer state: 2 extra floats per parameter
x = torch.randn(5)
loss_fn = lambda p: ((p * x).sum() - 1.0) ** 2

for t in range(1, 4):
    (g,) = torch.autograd.grad(loss_fn(w), w)
    with torch.no_grad():
        w.mul_(1 - lr * wd)                               # decoupled decay: shrink weights, gradient not involved
        m.mul_(b1).add_(g, alpha=1 - b1)                  # EMA of gradients (momentum)
        v.mul_(b2).addcmul_(g, g, value=1 - b2)           # EMA of squared gradients (per-parameter scale)
        m_hat, v_hat = m / (1 - b1 ** t), v / (1 - b2 ** t)   # undo the zero-initialisation bias
        step = lr * m_hat / (v_hat.sqrt() + eps)
        w.sub_(step)                                      # |step| ~ lr per parameter, whatever |g| is
    opt.zero_grad(); loss_fn(w_ref).backward(); opt.step()

print(torch.allclose(w, w_ref, atol=1e-6))                # True: this is exactly what AdamW does
print(step.abs() / lr)                                    # step sizes in units of lr: all close to 1
print(g.abs())                                            # ... even though the raw gradients differ a lot
```

## Trade-offs
- Adam and AdamW are robust to gradient-scale differences and to the learning-rate choice and make fast
  early progress, but cost two extra states per parameter and, on some vision tasks, generalise slightly
  worse than tuned SGD; when memory binds, 8-bit states or Adafactor recover most of the benefit.
- Decoupled decay makes regularisation strength independent of gradient statistics, but the effective
  decay per step is η·λ, so schedules that change η change the decay too, and λ must be chosen with that in mind.
- A high β2 (0.999) gives a smooth, stable scale estimate but adapts slowly to shifts in gradient
  magnitude, which shows up as loss spikes in long runs; 0.95 adapts faster at the cost of a noisier
  denominator, and the balance tips toward 0.95 as runs get longer.
- A larger ε stabilises parameters with tiny gradients, especially in low precision, but damps the very
  adaptivity Adam exists for; it is a knob to move deliberately, not a default to leave untouched in bf16.

## Pitfalls
- Using `torch.optim.Adam` with `weight_decay` and believing it is AdamW; the symptom is regularisation
  that seems to do nothing, because the decay on large-gradient weights has been divided away.
- Applying weight decay to biases and normalisation gains; the symptom is a small but consistent loss
  penalty, fixed with parameter groups that exclude them.
- Carrying an SGD learning rate such as 0.1 into Adam; the symptom is immediate divergence, since Adam's
  step is about η per parameter per step.
- Skipping warmup; the symptom is a loss spike in the first few hundred steps, sometimes unrecoverable,
  caused by updates computed from a second-moment estimate built on a handful of samples.
- Resuming from a checkpoint that saved weights but not optimizer state; the symptom is a loss spike at
  the resume point as m and v restart from zero.
- Forgetting optimizer state in capacity planning; the symptom is an out-of-memory error at the first
  `optimizer.step()` even though the bf16 weights fitted comfortably.
- Keeping m and v in plain bf16 to save memory; bf16 resolves only about 0.4% relative changes, so the
  per-step increment (1 − β2)·g² = 0.001·g² to v is rounded away and the scale estimate stops tracking.
  The symptom is training that stalls or drifts compared with an fp32-state run, which is why states stay
  in fp32 or use block-wise 8-bit quantisation designed for this.

## When to use
Default to AdamW for transformers and for most new architectures: learning rate from a short sweep,
weight decay in the 0.01 to 0.1 range on matrices only, β2 of 0.95 for long pre-training runs if spikes
appear, paired with warmup, a cosine or linear decay and gradient clipping. Move to 8-bit Adam, Adafactor
or parameter-efficient fine-tuning when optimizer memory is the binding constraint, and keep SGD with
momentum for convolutional recipes that are known to work with it. Measure the loss and gradient-norm
curves and the ratio of update size to weight size per layer, a useful sanity check that updates are
neither negligible nor wild. In interviews, expect to write the Adam update, explain bias correction,
contrast coupled L2 with decoupled decay, and count the bytes of optimizer state per parameter.

## Role lens
- **Engineer:** Configures AdamW with parameter groups that exclude norms and biases from decay, sets
  β2 and ε per recipe, saves optimizer state in checkpoints and switches to 8-bit states when memory is tight.
- **Architect:** Counts optimizer-state bytes in training capacity, using the 16-bytes-per-parameter rule
  to decide GPU count, sharding strategy and whether full fine-tuning is even on the table versus PEFT.
- **FDE:** When a customer's fine-tune diverges or runs out of memory on their hardware, diagnoses learning
  rate, warmup, β2 and optimizer memory, and justifies the PEFT recommendation with the byte arithmetic.

## Check yourself
**Q:** Explain why an L2 penalty added to the loss does not behave like weight decay under Adam, and what
AdamW changes. Include what happens to a parameter with consistently large gradients.
**A:** Under Adam the L2 term adds λθ to the gradient, and that combined gradient feeds both moment
estimates; the update is η·m̂/(√v̂ + ε). For a parameter with a large gradient history, √v̂ is large and
the λθ portion of the numerator is divided down to almost nothing, so it is barely regularised at all.
For a parameter with tiny gradients, λθ dominates numerator and denominator alike, the ratio collapses
to about sign(θ), and it decays at the full rate η regardless of λ. The effective regularisation is
therefore set by gradient statistics rather than by λ. AdamW takes λθ out of the gradient and applies
θ ← θ(1 − ηλ) as a separate step before the Adam update, so every weight shrinks by the same fraction,
λ and η become separately tunable, and generalisation improves at the same learning rate. One caveat:
the shrink per step is ηλ, so a learning-rate schedule scales the decay along with it.

## Quiz
### Q: What does dividing by √v̂ achieve in Adam's update?
- [ ] It computes the diagonal of the Hessian exactly
- [x] It normalises each parameter's step by its typical gradient magnitude, so updates are about η in size regardless of gradient scale
- [ ] It removes the need to choose a learning rate at all
- [ ] It makes training deterministic across runs
> √v̂ estimates the root-mean-square gradient for that parameter; dividing by it makes the step roughly
> η·E[g]/√E[g²], bounded by about η. The learning rate is still needed, it just means "parameter units per
> step" instead of being entangled with gradient scale.

### Q: Why does Adam apply bias correction to m and v?
- [ ] Because mini-batch gradients are biased estimates of the full gradient
- [x] Both averages start at zero, so early estimates are shrunk by a factor of (1 − βᵗ); without correcting both, the first steps would be badly mis-scaled
- [ ] To prevent overfitting during the first epoch
- [ ] Because β1 is larger than β2
> After one step m is only 0.1·g and v only 0.001·g², and the two shrink by different amounts, so the
> uncorrected ratio is off by a factor of about three. Dividing by (1 − β1ᵗ) and (1 − β2ᵗ) restores
> unbiased estimates; the correction fades to nothing as t grows.

### Q: Full fine-tuning of a 7-billion-parameter model with AdamW in mixed precision: roughly how much memory do weights, gradients and optimizer state need before activations?
- [ ] About 14 GB
- [ ] About 28 GB
- [x] About 112 GB
- [ ] About 1 TB
> The usual accounting is 16 bytes per parameter: 2 (fp16 weights) + 2 (fp16 gradients) + 4 (fp32 master
> weights) + 4 (m) + 4 (v). 7 × 10⁹ × 16 bytes ≈ 112 GB, which is why full fine-tuning at this size needs
> several GPUs or sharded and quantised optimizer state, and why PEFT is so common.
