---
id: learning-rate
module: training-adaptation
name: Learning rate
kind: concept
difficulty: core
est_minutes: 14
related:
  - ml-fundamentals/learning-rate-schedulers
  - ml-fundamentals/warmup
  - ml-fundamentals/optimizers
  - ml-fundamentals/gradient-descent
  - ml-fundamentals/batch-size
  - ml-fundamentals/gradient-clipping
  - training-adaptation/peft
  - training-adaptation/catastrophic-forgetting
sources:
  - Smith, 2017 — Cyclical Learning Rates for Training Neural Networks
  - Goyal et al., 2017 — Accurate, Large Minibatch SGD: Training ImageNet in 1 Hour
  - Loshchilov & Hutter, 2019 — Decoupled Weight Decay Regularization
  - Touvron et al., 2023 — Llama 2: Open Foundation and Fine-Tuned Chat Models
---

## TL;DR
The learning rate is the optimiser's step size: how far parameters move per update along the gradient
(SGD) or the per-coordinate-normalised gradient (Adam). It is the most sensitive hyperparameter because a
gradient gives direction, not a safe distance: too large and the loss oscillates or diverges, too small
and the run crawls and ends worse. In LLM work, peak, warmup and decay are chosen together; fine-tuning
uses rates far below pretraining to stay near the pretrained solution.

## Why it exists
Gradient descent is `θ ← θ − η·∇L(θ)`. The gradient is a first-order, local statement: it says which
direction is downhill at this point, not how far downhill continues. How far is governed by curvature.
For a one-dimensional quadratic `L = ½λθ²` the update multiplies θ by `(1 − ηλ)`, so the iteration
converges only when `η < 2/λ`; above that, each step overshoots the minimum by more than it approached
it and the loss grows without bound. A real loss surface has many directions with different curvatures,
and a single η must be small enough for the sharpest of them while still making progress along the flat
ones. That compromise is why the learning rate must be tuned and why adaptive optimisers, which rescale
each coordinate, exist at all; even with them, the global η still sets how far every weight can move.

Both naive policies fail. "Set it small to be safe" wastes the budget: distance travelled is roughly steps
× step size, so a rate ten times too small spends the whole run in the early part of the trajectory and
ends with the loss of a much shorter run; it also tends to generalise worse, an empirical regularity whose
explanation is still debated. "Set it large to go fast" produces loss spikes, NaNs and a model whose
pretrained features were destroyed in the first hundred steps. The learning rate is therefore not a
constant but a trajectory, with a warmup, a peak and a decay, each answering a different failure.

## How it works
### What η means under SGD versus Adam
Under SGD, `Δθ = −η·g`: the step scales with the gradient's magnitude, which differs across layers by
orders of magnitude, so a good SGD rate is specific to the architecture and even the initialisation.
Under Adam and AdamW, `Δθ ≈ −η·m̂ / (√v̂ + ε)`, where m̂ and v̂ are running estimates of the gradient's
mean and squared magnitude. The division normalises scale away: for a consistently signed gradient the
step is about η per parameter regardless of how large the raw gradient was, and smaller when the gradient
flips sign. That is why transformer learning rates cluster in a narrow band whatever the layer: the Llama 2
report, for instance, trained its 7B and 13B models with a peak of 3e-4 and the 34B and 70B with 1.5e-4,
with cosine decay to 10% of peak. AdamW's weight decay is also multiplied by η (`θ ← θ·(1 − η·λ)`), so
raising the learning rate strengthens regularisation at the same time.

### Warmup: why the first steps are special
Runs start with η near zero and ramp linearly to the peak, over thousands of steps in pretraining or a few
percent of the steps in fine-tuning. Two reasons. Adam's second-moment estimate is built from very few
samples at the start, so normalisation is unreliable and a full-size step can land anywhere. And the
earliest gradients are the largest and least informative: a freshly attached head or a zero-initialised
adapter produces gradients that would tear through pretrained features if applied at full rate. Warmup
lets the statistics and the representation settle before committing to large moves. Depth on the
mechanism lives in `ml-fundamentals/warmup`.

### Decay: why the last steps are special
Minibatch gradients are noisy, so at a fixed rate the parameters jitter inside a region whose size grows
with η; the loss plateaus at the noise floor rather than at the minimum. Shrinking η late in the run
(cosine or linear decay to a fraction of peak) lets the parameters settle into the basin. Some recent
recipes hold the rate constant and decay only in a final phase so a run can be extended without replaying
the schedule. Schedule shapes are covered in `ml-fundamentals/learning-rate-schedulers`.

### Scaling with batch size
A larger batch averages more samples, reduces gradient noise and tolerates larger steps. For SGD the
linear scaling rule (multiply η by *k* when the batch grows by *k*, with warmup) is well established; for
Adam the right exponent is contested and square-root scaling is often recommended. Either way, a rate
copied from a run with a different batch size is a different hyperparameter.

### Fine-tuning rates
Full fine-tuning of an LLM typically uses peaks in the low 1e-5 range; LoRA and other adapters typically
use rates around ten times higher. Treat these as common starting points, not laws. The full-model rate is
low because every weight moves and large moves erase pretrained knowledge (see catastrophic forgetting);
adapter rates are higher because the trainable set is small, starts at zero and must leave zero quickly.

### Finding the rate
The range test increases η exponentially over a short run and plots loss: the usable region ends just
before the loss explodes, and the best peak sits somewhat below that edge. A coarse grid at ×3 spacing
(1e-5, 3e-5, 1e-4) resolves most fine-tuning questions. Diagnostics: spikes or NaN mean too high;
a slow, monotone descent that ends above the baseline means too low; gradient clipping firing on most
steps means the rate is leaning on the clip rather than being right.

## Example
The stability bound is easiest to see on the quadratic, and Adam's normalisation is easiest to see by
feeding it two gradients that differ by 1000× and watching the parameter move by the same amount.

```python
import torch

def gd(lr, curvature=4.0, theta=1.0, steps=10):
    # Loss L = 0.5 * curvature * theta**2, gradient = curvature * theta
    for _ in range(steps):
        theta = theta - lr * curvature * theta          # = theta * (1 - lr * curvature)
    return theta

for lr in (0.05, 0.1, 0.45, 0.5, 0.6):
    print(f"lr={lr:<5} factor={1 - lr * 4:+.2f}  theta after 10 steps={gd(lr):+.4f}")
# lr=0.05  factor=+0.80  0.1074   slow, monotone
# lr=0.1   factor=+0.60  0.0060   fast, monotone
# lr=0.45  factor=-0.80  0.1074   oscillates, still converges
# lr=0.5   factor=-1.00  1.0000   bounces forever: exactly the 2/curvature limit
# lr=0.6   factor=-1.40  28.925   diverges

# Adam normalises the gradient scale away, so the first step is ~lr whatever the gradient size.
w = torch.nn.Parameter(torch.tensor([1.0]))
opt = torch.optim.Adam([w], lr=1e-3)
for scale in (1.0, 1000.0):
    w.data.fill_(1.0); opt.state.clear()
    w.grad = torch.tensor([scale])
    opt.step()
    print(f"gradient={scale:>6}: moved by {1.0 - w.item():.6f}")   # both ~0.001
```

In a Hugging Face fine-tune the same ideas appear as `learning_rate=2e-5`, `warmup_ratio=0.03` and
`lr_scheduler_type="cosine"`; the peak, the ramp and the decay are three settings of one trajectory.

## Trade-offs
- A higher peak rate reaches a lower loss within a fixed budget and often generalises better, but
  instability grows with it; the balance flips as soon as loss spikes appear, which happens more readily at
  larger model scale.
- Longer warmup protects against early divergence and feature damage but delays learning; with a short
  fine-tune of a few hundred steps, a long warmup can consume a large share of the run.
- Decaying to near zero gives the best final weights but a finished run cannot be extended; a constant
  phase with a late decay keeps that option at the cost of a slightly less settled endpoint.
- A low fine-tuning rate preserves pretrained knowledge and reduces forgetting but may underfit the new
  task; the balance shifts toward a higher rate when the task is far from the pretraining distribution.

## Pitfalls
- Copying a rate across optimisers, batch sizes or parameterisations (SGD versus AdamW, full fine-tune
  versus LoRA); the symptom is a run that diverges immediately or one that barely moves in the budget.
- Changing the number of steps (epochs, dataset size) while the schedule is defined in steps; the symptom
  is two "identical" configs with different effective rates at the same step and incomparable results.
- Letting gradient clipping hide an excessive rate; the symptom is the clip triggering on almost every
  step while the loss creeps rather than drops.
- Skipping or truncating warmup with Adam; the symptom is divergence or a permanent loss plateau in the
  first few hundred steps that no later schedule repairs.
- Raising the learning rate without noticing that AdamW's decay scales with it; the symptom is weights
  shrinking faster than intended and underfitting that looks like a data problem.
- Reporting "learning rate 2e-5" with no warmup, schedule or batch size; the symptom is that nobody,
  including you, can reproduce the run.

## When to use
Every training run sets a learning rate; the decision is how to choose it and its trajectory. For a new
fine-tuning task, start from a common rate for the parameterisation you are using, run a short range test
or a ×3 grid, and read the loss curve for spikes (too high) or a flat, early plateau (too low). Pair the
peak with a warmup of a few percent of the steps and a decay that fits the run length, and record all
three with the batch size. Watch the gradient norm alongside the loss; a healthy run has a stable norm,
not one rescued by clipping. In interviews, derive the 2/λ bound, explain why Adam makes rates transferable
across layers, and say why fine-tuning rates are lower than pretraining rates; that chain of reasoning is
what distinguishes understanding from a remembered number.

## Role lens
- **Engineer:** Picks the peak rate, warmup ratio and schedule in the training config, runs the range test
  or grid, and reads loss and gradient-norm curves to diagnose a rate that is too high or too low.
- **Architect:** Sets the policy that learning rate, warmup, schedule and batch size are recorded together
  for every run so results are reproducible and comparable across teams and model sizes.
- **FDE:** When a customer's fine-tune on their data diverges or regresses on general ability, the learning
  rate and its warmup are the first settings to inspect and the easiest to explain.

## Check yourself
**Q:** Your full fine-tune at a peak rate of 2e-5 learns the task but regresses on general instruction
following; your LoRA run at the same 2e-5 learns almost nothing. Explain both outcomes and what you would
change.
**A:** The full fine-tune moves every weight by about the learning rate per step under AdamW, so even a
modest rate accumulated over hundreds of steps shifts the whole network away from the pretrained solution;
the regression is forgetting, and the fixes are a lower rate, fewer steps, early stopping on a general
evaluation, or mixing general data into the set. The LoRA adapters start at zero and are a tiny fraction
of the parameters, so at 2e-5 they barely leave their initialisation within the run; adapter training
commonly uses rates around ten times higher. The change is to tune the rate per parameterisation, run a
quick grid at ×3 spacing for each, and watch both the task metric and the general evaluation rather than
training loss alone.

## Quiz
### Q: For the loss L = ½λθ² with λ = 4, which learning rate makes plain gradient descent diverge?
- [ ] 0.1
- [ ] 0.25
- [ ] 0.45
- [x] 0.6
> The update multiplies θ by (1 − ηλ), which must stay inside (−1, 1), so η must be below 2/λ = 0.5. At 0.45
> the iterate oscillates but shrinks; at 0.6 the factor is −1.4 and the loss grows every step.

### Q: Why do Adam learning rates transfer across layers and architectures better than SGD rates?
- [ ] Adam ignores the gradient and moves every weight by exactly η
- [x] Adam divides by a running estimate of gradient magnitude, so the step is roughly η per parameter regardless of raw gradient scale
- [ ] Adam automatically performs a learning-rate range test during warmup
- [ ] Adam applies weight decay, which cancels differences in gradient scale
> Normalisation by the second-moment estimate removes the layer-to-layer scale differences that make SGD
> rates architecture-specific. Adam does not ignore gradients; sign changes and noise still shrink its steps.

### Q: A bf16 fine-tune shows loss spikes and then NaN within the first 50 steps. Which learning-rate issue is most likely?
- [ ] The rate is too low, so the loss cannot leave its plateau
- [x] The peak rate is too high or the warmup too short, so early unreliable Adam steps are taken at full size
- [ ] The decay schedule ended too early
- [ ] Weight decay was set to zero
> Early steps combine the largest gradients with the least reliable second-moment estimates; a full-size
> step there overshoots sharply curved directions. Decay is irrelevant that early, and a too-low rate
> produces slowness, not spikes.
