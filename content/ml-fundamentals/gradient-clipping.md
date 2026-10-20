---
id: gradient-clipping
module: ml-fundamentals
name: Gradient clipping
kind: practice
difficulty: core
est_minutes: 11
status: fact-checked
related:
  - ml-fundamentals/backpropagation
  - ml-fundamentals/optimizers
  - ml-fundamentals/warmup
  - ml-fundamentals/mixed-precision
  - ml-fundamentals/gradient-accumulation
  - ml-fundamentals/learning-rate-schedulers
  - training-adaptation/distributed-training
sources:
  - Pascanu et al., 2013 — On the difficulty of training recurrent neural networks
  - Zhang et al., 2020 — Why Gradient Clipping Accelerates Training: A Theoretical Justification for Adaptivity
  - Official docs: PyTorch torch.nn.utils.clip_grad_norm_
---

## TL;DR
Gradient clipping caps the size of the update computed from one batch: if the global L2 norm of all
gradients exceeds a threshold, every gradient is scaled down by the same factor so the norm equals the
threshold. The direction is preserved; only the step length is bounded. It exists because a single rare
batch can produce a gradient tens of times larger than usual, and one such step can undo thousands of
good ones or corrupt the optimizer's running statistics for the rest of the run.

## Why it exists
Backpropagation multiplies Jacobians layer by layer. Whenever those products line up, the gradient at the
bottom of the network can be orders of magnitude larger than typical. In recurrent networks this happened
systematically along the time axis, and the 2013 analysis of exploding gradients proposed clipping as the
fix. In transformers it happens sporadically: an unusual batch, a sharp region of the loss surface, an
attention pattern that saturates, or a numerical edge case in mixed precision produces a gradient norm of
50 where the running average is 1.

The damage from one such step is out of proportion to its frequency. With plain SGD the weights jump by
`η × 50` instead of `η × 1`, which can move the model out of the basin it spent a day finding. With Adam
the raw step is normalized, which softens the immediate jump, but the huge gradient enters the first and
second moment estimates. The second moment `v` uses a decay like 0.95 or 0.999, so a single squared value
2,500× larger than normal inflates `v` for hundreds of steps and shrinks every subsequent update for that
parameter. The run does not crash; it quietly learns slower for a while, and the loss curve shows a
plateau nobody can explain.

The naive alternative is to lower the learning rate until the worst batch is survivable. That penalizes
every normal step to protect against a rare one. Clipping is the targeted version: leave normal steps
untouched and bound only the outliers. Theory later caught up with practice: clipped gradient descent can
be shown to converge faster than fixed-step descent on functions whose smoothness varies with gradient
magnitude, which is the regime deep networks live in.

## How it works
### Global norm clipping
Collect every parameter gradient, compute one scalar:

```
total_norm = sqrt( Σ_params Σ_elements g² )
scale      = min(1, max_norm / (total_norm + ε))
g         ← g × scale        for every parameter
```

Two design choices matter. First, the norm is *global*, across all parameters, not per tensor. Per-tensor
clipping would rescale layers by different factors and change the direction of the overall step; global
clipping multiplies everything by the same scalar, so the step points exactly where backprop said and is
merely shorter. Second, the operation is a no-op when `total_norm ≤ max_norm`. On a healthy run clipping
should fire on a minority of steps; if it fires on almost every step, the threshold is acting as a
constant learning-rate reduction and the real problem is elsewhere, usually the peak learning rate or a
missing warmup.

### Value clipping
`clip_grad_value_` instead clamps each element to `[−c, c]`. It is cheaper to reason about but changes
the direction of the gradient, since large components are flattened while small ones are untouched. It
survives mostly in older RNN code; transformer recipes use norm clipping, almost universally with
`max_norm = 1.0`.

### Where it sits in the step
Clipping must happen after the full gradient for the optimizer step exists and before the optimizer uses
it:

1. Run forward and backward for every micro-batch in the accumulation window; gradients sum in `.grad`.
2. If using fp16 loss scaling, **unscale** the gradients first. The scaled gradients are thousands of
   times larger than real, so clipping them at 1.0 would clip everything to nearly zero.
3. Compute the global norm and clip. Log the pre-clip norm.
4. `optimizer.step()`, then `zero_grad()`.

Clipping each micro-batch separately is wrong for the same reason per-tensor clipping is: the accumulated
gradient is a sum, and clipping the pieces changes both its magnitude and its direction.

### Distributed training
Under data parallelism the gradient is all-reduced before the step, so clipping the local gradient after
the reduction is equivalent to clipping the global one. Under sharded training (FSDP, ZeRO) each rank
holds only part of the gradient, so the norm must be computed as a sum of squares across ranks followed by
a collective. Frameworks handle this in one of two ways: either they expose their own clipping entry point
(FSDP1's `model.clip_grad_norm_`, DeepSpeed's `gradient_clipping` config) or, as FSDP2 does by storing
gradients as DTensors, they make the standard utility reduce across ranks. Calling the plain utility on
the local shards of a framework that does neither silently computes a per-rank norm.

### The norm as a health signal
The pre-clip gradient norm is one of the most informative training curves. It should be large in the
first steps, fall during warmup, then drift slowly. Spikes that coincide with loss spikes point at data or
numerics; a steady upward trend late in training points at the learning rate being too high for the
current loss surface; a norm that sits above the threshold for hundreds of consecutive steps means the
run is being throttled by the clip rather than protected by it.

## Example
A training step with accumulation, fp16 loss scaling and clipping in the correct order. The function
returns the pre-clip norm so it can be logged, and the comment shows the arithmetic for one concrete
step.

```python
import torch
from torch.nn.utils import clip_grad_norm_

def train_step(model, opt, scaler, micro_batches, accum, max_norm=1.0):
    opt.zero_grad(set_to_none=True)
    for x, y in micro_batches:                       # len(micro_batches) == accum
        with torch.autocast("cuda", dtype=torch.float16):
            loss = torch.nn.functional.cross_entropy(model(x), y) / accum
        scaler.scale(loss).backward()                # grads are scaled by S (e.g. 65536)
    scaler.unscale_(opt)                             # divide grads by S BEFORE measuring the norm
    pre_clip = clip_grad_norm_(model.parameters(), max_norm)   # returns the norm before clipping
    scaler.step(opt)                                 # skips the step if any grad is inf/nan
    scaler.update()
    return pre_clip.item()

# Worked step: suppose the accumulated gradient has global norm 4.0 and max_norm is 1.0.
#   scale = min(1, 1.0 / 4.0) = 0.25 -> every element is multiplied by 0.25, direction unchanged.
# If instead the norm had been 0.7, scale = min(1, 1/0.7) = 1 -> nothing happens.
# Forgetting unscale_(): the scaled norm is 4.0 * 65536 = 262144, so the clip scales it to 1.0;
#   when the scaler then unscales internally, the real gradient norm is 1/65536 ~ 1.5e-5 instead of 4.0.
```

With bf16 there is no scaler; drop the `scaler` calls and call `clip_grad_norm_` directly after the last
`backward()`. The ordering rule (accumulate, unscale, clip, step) is the same.

## Trade-offs
- Clipping bounds the damage of rare outlier batches at no cost on normal steps, but if the threshold is
  far below the typical norm it becomes a hidden learning-rate cut that slows the whole run.
- Global-norm clipping preserves update direction, while value clipping is simpler and cheaper but distorts
  the direction whenever a few coordinates dominate.
- A tighter threshold buys stability for aggressive learning rates and large models, but it masks
  problems (bad data, numeric overflow) that would otherwise show up as a clear divergence.
- Under sharded training the global norm requires an extra collective per step; the cost is small compared
  with the gradient all-reduce, but skipping it in favor of local norms produces subtly inconsistent clipping.

## Pitfalls
- Clipping before `unscale_()` under fp16: every step is clipped to near zero and the loss barely moves
  while nothing reports an error.
- Clipping after `optimizer.step()` or before `backward()`: the call succeeds, does nothing useful, and
  the first real gradient spike still corrupts the run.
- Clipping each micro-batch in a gradient-accumulation loop, which changes the direction of the summed
  gradient and makes the effective threshold depend on the accumulation count.
- Calling the plain PyTorch utility on parameters sharded by FSDP1 instead of its `clip_grad_norm_` method;
  the norm is computed over the local shard only, so clipping behaves differently on every rank.
- Using clipping to paper over divergence caused by a too-high learning rate or missing warmup; the clip
  fires on nearly every step, which is the signal that the root cause is still there.
- Not logging the pre-clip norm, which throws away the single best early-warning signal for bad data,
  numeric issues and a learning rate that has outlived its schedule.

## When to use
Turn it on for every transformer training run; the default of `max_norm = 1.0` is a sensible starting
point, and training frameworks such as the Hugging Face `Trainer` already apply it. The decision is less
whether to clip than how to read it: log the pre-clip norm, note the fraction of steps on which clipping
fired, and treat sustained clipping as a configuration problem rather than a success. Revisit the
threshold when changing optimizer, precision or batch size, since all three move the typical norm.
In interviews, the expected depth is the ordering (accumulate, unscale, clip, step), the reason global
norm beats per-element clipping, and why clipping still matters with Adam even though Adam normalizes
step sizes.

## Role lens
- **Engineer:** Places the clip call correctly relative to loss scaling and accumulation, logs the pre-clip
  norm, and alerts on it as part of training-run monitoring.
- **Architect:** Fixes clipping and norm-logging as part of the standard training recipe so that every
  team's runs produce comparable stability diagnostics across models and clusters.
- **FDE:** When a customer's fine-tuning diverged or stalled, pulls their gradient-norm trace to show
  whether the run was unprotected or quietly throttled, turning a vague complaint into a concrete fix.

## Check yourself
**Q:** A team trains with Adam, warmup, bf16 and clipping at 1.0. Their logs show clipping fired on 97% of
steps for the entire run, yet the loss decreased steadily and nothing diverged. Is this fine?
**A:** It is not a failure, but it is a sign that the configuration is not what they think it is. If
the norm is above 1.0 almost every step, clipping is acting as a constant rescaling of the gradient
rather than a safety net, so the effective learning rate is lower than the configured one and varies
with the gradient norm from step to step. The run converged, but probably slower than it could have, and
the schedule they tuned is not the schedule that actually ran. The usual causes are a learning rate set
too high for the model size, a loss that is summed instead of averaged over tokens so its scale is large,
or a threshold copied from a different setup. The fix is to look at the pre-clip norm distribution and
either raise the threshold to sit above the typical norm or correct the loss scaling and learning rate so
clipping fires only on outliers.

## Quiz
### Q: Why is global-norm clipping preferred over clamping each gradient element to a fixed range?
- [ ] It is cheaper because it needs only one pass over the gradients
- [x] It multiplies every gradient by the same scalar, so the update keeps its direction and only its length is bounded
- [ ] It works with Adam, whereas value clipping only works with SGD
- [ ] It automatically adapts the threshold to the learning rate
> Clamping elements flattens the large coordinates and leaves small ones alone, which changes where the
> step points. A single global scale factor shortens the step without rotating it.

### Q: In an fp16 training loop with a GradScaler, where must the clipping call go?
- [ ] Immediately after the loss is computed, before backward
- [ ] After `scaler.step()` so the optimizer has already applied the update
- [x] After `scaler.unscale_(optimizer)` and before `scaler.step()`
- [ ] Anywhere, because the scaler accounts for clipping automatically
> Gradients after backward are multiplied by the loss scale, so their norm is thousands of times too large.
> Unscaling first makes the threshold meaningful; clipping after the step is too late to affect it.

### Q: Adam already normalizes each parameter's update by its running gradient magnitude. Why does clipping still help?
- [ ] It does not; clipping is only useful with SGD
- [ ] Clipping replaces the need for a second-moment estimate
- [x] A single huge gradient still enters Adam's moment estimates and distorts updates for many subsequent steps
- [ ] Adam's normalization only applies to the first layer of the network
> Adam's second moment has a long memory, so one outlier inflates it and shrinks later updates for that
> parameter; clipping keeps the outlier out of the statistics in the first place.
