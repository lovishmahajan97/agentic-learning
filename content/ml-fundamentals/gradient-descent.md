---
id: gradient-descent
module: ml-fundamentals
name: Gradient descent
kind: concept
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - ml-fundamentals/loss-functions
  - ml-fundamentals/backpropagation
  - ml-fundamentals/optimizers
  - ml-fundamentals/learning-rate-schedulers
  - ml-fundamentals/batch-size
  - ml-fundamentals/warmup
  - training-adaptation/learning-rate
  - training-adaptation/overfitting
sources:
  - Robbins & Monro, 1951 — A Stochastic Approximation Method
  - Polyak, 1964 — Some Methods of Speeding Up the Convergence of Iteration Methods
  - Bottou, Curtis & Nocedal, 2018 — Optimization Methods for Large-Scale Machine Learning
  - Goodfellow, Bengio & Courville, 2016 — Deep Learning
---

## TL;DR
Gradient descent minimises a loss by repeatedly moving the parameters a small step against the
gradient: θ ← θ − η∇L(θ). It is the only practical way to fit models with millions to billions of
parameters, because the gradient costs about as much as a forward pass regardless of parameter count.
Stochastic (mini-batch) gradient descent uses a noisy estimate from a few examples, trading precision per
step for many more steps per second, and that noise turns out to help rather than hurt.

## Why it exists
Training means finding θ that minimises L(θ), the average loss over the data. Closed-form solutions
exist only for a few special cases such as linear least squares. Grid or random search over parameters
is exponential in their number: even ten candidate values per parameter is 10^P combinations, absurd
when P is a billion. Derivative-free methods such as evolution strategies need on the order of P loss
evaluations to learn as much as one gradient tells you.

The gradient is the escape. A first-order Taylor expansion says L(θ + δ) ≈ L(θ) + ∇L(θ)ᵀδ, and among all
steps δ of a fixed small length, the one that decreases L the most points exactly along −∇L. Backpropagation
delivers all P components of that direction in one backward pass costing roughly twice a forward pass. So
each iteration buys a P-dimensional improvement direction for a constant factor over evaluating the loss
once. Nothing else offers that ratio.

Two problems remain. The approximation is local, so the step must be small enough for it to hold; that
is the learning rate, and getting it wrong is the most common training failure. And computing the exact
gradient over billions of examples per step is itself too expensive. Robbins and Monro showed in 1951
that noisy, unbiased gradient estimates suffice if the steps are chosen sensibly, which licenses
mini-batches and turns gradient descent into stochastic gradient descent, the algorithm underneath every
modern optimizer.

## How it works
### The update and why the learning rate has a ceiling
θ_{t+1} = θ_t − η g_t, with g_t the gradient. Take a one-dimensional quadratic bowl L = ½λθ², so
g = λθ and θ_{t+1} = (1 − ηλ)θ_t. The iteration converges only if |1 − ηλ| < 1, that is η < 2/λ, and
converges in one step at η = 1/λ. In many dimensions every eigen-direction of the Hessian has its own
curvature λ_i, and the same η applies to all of them: it must satisfy η < 2/λ_max or the steepest
direction diverges, while progress along the flattest direction shrinks by only (1 − ηλ_min) per step.
With η near 1/λ_max that factor is about 1 − λ_min/λ_max, so the number of iterations scales with the
condition number κ = λ_max/λ_min. This one ratio explains why unscaled features train slowly, why
normalisation layers help, and why momentum and adaptive optimizers exist: they all attack κ.

### Mini-batches: stochastic gradient descent
The full gradient is an average over N examples. A mini-batch of B examples gives an unbiased estimate
whose variance scales as 1/B, at a cost proportional to B. For a fixed compute budget, smaller batches
mean more updates; beyond a problem-dependent critical batch size, adding examples to a batch reduces
noise that was no longer limiting and the extra compute is wasted. Shuffle each epoch so consecutive
batches are not correlated. When you raise the batch size, the learning rate usually needs to rise with
it to keep progress per epoch, a heuristic that holds up to a point and then breaks. The widely held,
still-debated view is that the noise is not merely tolerated but useful: it helps escape saddle points
and biases training toward flatter minima that generalise better. The batch-size lesson goes deeper.

### Momentum
Heavy-ball momentum keeps a running velocity: v ← μv + g, θ ← θ − ηv. The velocity is an exponential
moving average of gradients. In a steep direction where the gradient flips sign every step (zig-zagging
across a valley), successive terms cancel and the effective step shrinks; in a consistent direction they
accumulate, up to a factor of 1/(1 − μ), ten times with μ = 0.9. The net effect is a smaller effective
κ. Nesterov momentum evaluates the gradient at the look-ahead point θ − ημv; the practical difference is
usually small.

### Non-convexity
Deep-network losses are non-convex, so the guarantee is only convergence to a stationary point. In
practice, in high dimensions most stationary points are saddles rather than bad local minima, noise and
momentum carry the iterate past them, and many different minima turn out to be equally good. What
decides outcomes is not the fear of local minima but the learning-rate schedule, warmup, batch size and
initialisation.

### Reading the loss curve
Loss shooting up or becoming NaN: η above the stability limit, or no gradient clipping. Loss flat from
the start: η far too small, dead units, or a bug stopping gradient flow. Loss oscillating with period
two: η near 2/λ_max in some direction. Long plateau then a sudden drop: a saddle. Watching the gradient
norm alongside the loss separates these cases quickly.

## Example
A two-dimensional quadratic whose curvature is 50× larger along w2 than along w1 (condition number 50).
The stability limit is 2/50 = 0.04. Plain gradient descent must crawl along w1 to stay stable along w2;
momentum fixes it.

```python
import numpy as np

# Loss = 0.5 * (1*w1^2 + 50*w2^2): a bowl 50x steeper along w2 than along w1.
H = np.diag([1.0, 50.0])
grad = lambda w: H @ w                     # gradient of the quadratic; the minimum is at (0, 0)

def descend(eta, steps=200, mu=0.0):
    w, v = np.array([1.0, 1.0]), np.zeros(2)
    for _ in range(steps):
        v = mu * v + grad(w)               # mu = 0: plain GD;  mu > 0: heavy-ball momentum
        w = w - eta * v
    return w

for eta in (0.01, 0.03, 0.041):
    print(f"eta={eta}: w={descend(eta)}")
print(f"eta=0.03 + momentum 0.9: w={descend(0.03, mu=0.9)}")

# eta=0.01 : w1 is still 0.134 after 200 steps (0.99^200); w2 converged long ago. Too slow on the flat axis.
# eta=0.03 : w1 = 0.0023; w2 oscillated (factor -0.5 per step) but converged. Stable because 0.03 < 2/50.
# eta=0.041: w2 blows up to ~1.7e4 since |1 - 0.041*50| = 1.05 > 1, even though w1 is fine.
# momentum : both coordinates ~2e-5. The average cancels the w2 zig-zag and compounds the steady w1 pull.
```

## Trade-offs
- A large learning rate makes fast progress and crosses flat regions, but diverges once it exceeds
  2/λ_max in any direction; since the landscape sharpens as training proceeds, schedules decay η over time.
- Small batches give more updates per epoch and helpful noise, but poor GPU utilisation and noisy
  estimates; past the critical batch size, larger batches stop reducing the steps needed and only add cost.
- Momentum accelerates in consistent directions and damps oscillation, but overshoots when μ and η are
  both large and costs one extra copy of the parameters for the velocity.
- Full-batch gradient descent is exact and deterministic, which is pleasant for small convex problems,
  but delivers one update per pass over the data, hopeless at dataset scale.

## Pitfalls
- Unscaled inputs or targets inflate the condition number; the symptom is a loss that plateaus unless
  the learning rate is tiny, fixed by standardising features or adding normalisation layers.
- Learning rate too high; the symptom is loss rising after a few steps or NaN. Learning rate too low;
  the symptom is loss decreasing slowly and almost linearly for the whole run.
- Not shuffling between epochs; the symptom is a loss curve with a periodic pattern once per epoch and
  a model biased by data order.
- Forgetting to zero gradients between steps in PyTorch, so they accumulate; the symptom is an
  effective learning rate that grows every step and a run that diverges for no visible reason.
- Comparing runs with different batch sizes at the same learning rate; the symptom is the larger-batch
  run "learning slower", which is fewer updates per epoch, not a worse optimizer.
- Judging convergence from the training loss alone; the symptom is training loss still falling while
  validation loss rises, which is overfitting, not an optimisation problem.

## When to use
Gradient descent is the default for every differentiable model; the decisions are about its variant and
its hyperparameters. Plain SGD with momentum remains competitive for convolutional networks; AdamW is the
default for transformers (see the optimizers lesson). Choose the learning rate with a short sweep, pair
it with warmup and a decay schedule, and pick the batch size from hardware and the critical-batch-size
curve. It is the wrong tool for tiny convex problems with closed forms, and it cannot be applied directly
to non-differentiable objectives such as "did the agent complete the task", which is why reinforcement
learning and surrogate losses exist. Measure loss and gradient norm over steps. Interviewers ask why not a
closed form, why the learning rate has an upper bound, why mini-batches, and what momentum does.

## Role lens
- **Engineer:** Runs the learning-rate sweep, reads loss and gradient-norm curves to tell divergence from
  stagnation, and checks feature scaling and the learning rate first when a fine-tune does nothing.
- **Architect:** Budgets training as steps multiplied by cost per step; the batch-size and learning-rate
  relationship decides how many GPUs actually shorten a run and when adding more stops helping.
- **FDE:** Explains to a customer why a fine-tune on their data needs a learning-rate sweep rather than
  defaults, and why "the loss went to NaN after an hour" is an optimisation setting, not a data problem.

## Check yourself
**Q:** Training loss decreases steadily for 300 steps, then climbs sharply and becomes NaN. A colleague
proposes adding more data. What is your diagnosis, and in what order would you act?
**A:** This is optimisation instability, not a data-volume problem. As training progresses the loss
surface sharpens, so a learning rate that was below 2/λ_max in every direction can cross it; a single
large-gradient batch then pushes the weights into a region from which the iteration diverges. Check the
gradient-norm log for a spike just before the NaN, confirm whether warmup and clipping are configured,
and check for mixed-precision overflow and for a batch containing extreme values. Then, in order: lower
the peak learning rate or lengthen warmup, enable gradient-norm clipping, verify loss scaling for fp16,
inspect the offending batch, and resume from the last good checkpoint. More data changes nothing about
the per-step dynamics that caused the blow-up.

## Quiz
### Q: For a quadratic loss with curvature λ, plain gradient descent converges only when the learning rate satisfies which condition?
- [ ] η < λ
- [x] η < 2/λ
- [ ] η < 1
- [ ] η > 1/λ
> The update multiplies the error by (1 − ηλ) each step, which shrinks only when its magnitude is below
> one, i.e. 0 < η < 2/λ. The fastest convergence is at η = 1/λ; beyond 2/λ the iterate oscillates with
> growing amplitude.

### Q: Why does momentum help on an ill-conditioned problem?
- [ ] It raises the learning rate automatically in every direction
- [x] Gradients that alternate sign across a steep valley cancel in the running average while consistent gradients accumulate, so the effective step is large where it is safe and small where it is not
- [ ] It approximates second derivatives and takes Newton steps
- [ ] It removes all noise from mini-batch gradient estimates
> The velocity is an exponential average of past gradients. Across the steep direction successive
> gradients point opposite ways and cancel; along the flat direction they agree and sum to up to
> 1/(1 − μ) times a single gradient, which is what reduces the effective condition number.

### Q: You double the batch size and leave the learning rate unchanged. What happens to one epoch of training?
- [ ] Twice as many parameter updates, each with less noise
- [x] Half as many updates with lower-variance gradients, so progress per epoch typically drops unless the learning rate is raised
- [ ] An identical trajectory, because the gradient estimate is unbiased either way
- [ ] Guaranteed divergence, because the gradient magnitude doubles
> The gradient estimate stays unbiased but there are half as many steps per pass over the data. Lower
> variance lets you use a larger learning rate, which is why batch size and learning rate are tuned
> together rather than independently.
