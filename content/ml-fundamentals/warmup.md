---
id: warmup
module: ml-fundamentals
name: Warmup
kind: practice
difficulty: core
est_minutes: 11
status: fact-checked
related:
  - ml-fundamentals/learning-rate-schedulers
  - ml-fundamentals/optimizers
  - ml-fundamentals/gradient-clipping
  - ml-fundamentals/normalization
  - ml-fundamentals/batch-size
  - training-adaptation/learning-rate
sources:
  - Goyal et al., 2017 — Accurate, Large Minibatch SGD: Training ImageNet in 1 Hour
  - Liu et al., 2020 — On the Variance of the Adaptive Learning Rate and Beyond
  - Xiong et al., 2020 — On Layer Normalization in the Transformer Architecture
---

## TL;DR
Warmup ramps the learning rate from near zero to its peak over the first few hundred or thousand
optimizer steps. It exists because the first steps are the most dangerous: weights are random, gradients
are large and poorly conditioned, and Adam's running variance estimates are built from almost no data,
so a full-size step can throw the model into a region it never recovers from. A short ramp costs almost
nothing and removes most early-training divergence.

## Why it exists
At initialization nothing about the network is calibrated. Activations have whatever scale the init gave
them, the loss is at its maximum, and the gradient is large in magnitude and dominated by a few
directions (fix the output bias, shrink everything) rather than by anything useful. Taking a full
learning-rate step here moves the weights a long way along a direction computed from a landscape that
will look completely different one step later. The visible result is a loss that drops for a few steps,
then spikes, then either recovers slowly or produces NaNs.

Adaptive optimizers make this worse in a specific way. Adam divides each parameter's update by the square
root of a running average of squared gradients, `√v̂`. After one step, `v` is an estimate from a single
sample; after ten, from ten. The variance of that estimate is enormous early, and a parameter whose few
observed gradients happened to be small gets an update that is far too large. The RAdam analysis made this
precise: the effective per-parameter step size has divergent variance in the first iterations, and a
learning-rate warmup is a cheap way to keep updates small until `v` has enough samples to be trusted.

Architecture adds a third reason. In the original Post-LN transformer, the expected gradient norm at the
layers nearest the output is large at initialization and does not shrink with depth, while in Pre-LN it
scales down roughly as `1/√L`; in a deep Post-LN model the first updates are therefore disproportionately
large near the output, and the paper that analysed this showed Post-LN is unstable without warmup, while
Pre-LN tolerates its absence.
Large-batch training is a fourth: a bigger batch justifies a bigger learning rate, but only once the
model has left the chaotic initial region, so the ImageNet-in-one-hour recipe ramped the rate linearly
over the first five epochs rather than starting at the scaled peak.

The naive alternative is simply a lower peak learning rate. It avoids the early blow-up, but the whole run
then trains too slowly and the final loss is worse. Warmup separates the two concerns: a conservative
start and an aggressive middle.

## How it works
Warmup is a multiplier on the learning rate for the first `W` optimizer steps, after which the main
schedule takes over:

```
η(t) = η_max × t / W           for t < W      (linear warmup)
η(t) = main_schedule(t)        for t ≥ W
```

Linear is standard; some recipes start from a small non-zero fraction instead of exactly zero so the
very first step still does something. The ramp is in *optimizer steps*, which matters with gradient
accumulation: 500 warmup steps with accumulation 8 means 4,000 micro-batches.

### What it protects against, step by step
1. **Steps 1–10.** Adam's `m` and `v` are nearly empty. Bias correction fixes their *mean* but not their
   *variance*; the per-parameter step `m̂/√v̂` can be wildly off for individual weights. A tiny η keeps
   those mis-scaled updates harmless while `v` fills.
2. **Steps 10–W.** Gradients are still large and the loss surface is sharp. Weight norms, LayerNorm
   statistics and attention logit scales are settling. A rising η lets the model take progressively larger
   steps as the curvature along the trajectory decreases.
3. **After W.** The model is in a smoother basin, moment estimates are reliable, and the main schedule
   (cosine, linear, WSD) governs.

### How long
There is no closed form. Practical ranges: for fine-tuning, a few percent of total steps or a fixed 50–200
steps; for pre-training, on the order of a thousand to a few thousand steps (Llama 2, for example,
reports 2,000). Larger peak learning rates, larger batches and Post-LN architectures want longer warmup;
small fine-tuning runs with a low peak often work with very little. The tell for "too short" is a loss
spike or a sudden rise in gradient norm within the first few hundred steps; the tell for "too long" is
a flat early loss curve that only starts dropping when the ramp ends.

### Interaction with other stabilizers
Warmup, gradient clipping and Pre-LN normalization all attack the same early instability from different
sides, which is why modern recipes use all three. Clipping bounds the damage of any single bad step;
Pre-LN keeps gradient magnitudes roughly uniform across depth; warmup shrinks the step itself. Removing
one usually means asking more of the others.

### Resuming and re-warming
When resuming a run from a checkpoint with intact optimizer state, warmup should *not* repeat, because
`v` is already populated; the scheduler state must be restored so the learning rate continues where it
left off. When starting a new phase (continued pre-training on new data, or fine-tuning from a base model)
the optimizer state is fresh, so a new warmup is needed even though the weights are trained.

## Example
A fine-tuning configuration where the warmup length is derived from the real number of optimizer steps,
then checked by printing the learning rate at the ends of the ramp. The scheduler comes from
`transformers`; the arithmetic that most people get wrong is the step count.

```python
import torch
from transformers import get_cosine_schedule_with_warmup

n_examples, epochs = 20_000, 3
micro_batch, accum, n_gpus = 8, 4, 2
seq_per_opt_step = micro_batch * accum * n_gpus          # 64 sequences per optimizer step
opt_steps = epochs * (n_examples // seq_per_opt_step)    # 3 * 312 = 936 optimizer steps
warmup = int(0.05 * opt_steps)                           # 5% -> 46 steps

params = [torch.nn.Parameter(torch.zeros(4))]
opt = torch.optim.AdamW(params, lr=2e-5)
sched = get_cosine_schedule_with_warmup(opt, num_warmup_steps=warmup,
                                        num_training_steps=opt_steps)

seen = {}
for step in range(opt_steps):
    # for _ in range(accum): loss.backward()   # accumulate micro-batches
    opt.step(); opt.zero_grad(); sched.step()  # ONE scheduler step per optimizer step
    seen[step] = opt.param_groups[0]["lr"]

print(f"{seen[0]:.2e} {seen[warmup // 2]:.2e} {seen[warmup - 1]:.2e} {seen[opt_steps - 1]:.2e}")
# ~4.3e-07  ~1.0e-05  2.0e-05  ~0  -> ramp reaches the peak at step 45, then cosine decays
```

If `opt_steps` had been computed from micro-batches (`epochs * n_examples // micro_batch` = 7,500), the
same 5% rule would give 375 warmup steps, longer than the entire real run of 936 optimizer steps would
need, and the cosine decay would never reach its floor.

## Trade-offs
- A longer warmup is safer against early divergence but spends more of a short fine-tuning run below the
  productive learning rate; past roughly 10% of total steps the safety gain is usually negligible.
- Warmup lets you use a higher peak learning rate (and therefore reach a given loss in fewer steps),
  but the higher peak then needs a proper decay phase or the run ends noisy.
- Pre-LN architectures and gradient clipping reduce the *need* for warmup, so very short ramps work, but
  removing warmup entirely still risks polluting Adam's moment estimates with the first erratic gradients.
- Skipping warmup on resume is correct when optimizer state is restored, and wrong when it is not;
  the convenience of "just reload the weights" trades for a second unstable start.

## Pitfalls
- Setting `warmup_steps` as if steps were micro-batches or epochs; the symptom is either a ramp that
  never finishes before the run ends or one that is over after a single logging interval.
- Resuming a crashed run without the scheduler state, so warmup restarts from zero at step 10,000;
  the loss curve shows a visible dip in progress for the next few hundred steps.
- Treating a loss spike at step 50 as a data problem when it is a warmup problem; check the gradient norm
  trace first, which jumps before the loss does.
- Reusing a pre-training warmup length (thousands of steps) for a fine-tuning run of 500 steps, so the
  model never reaches its intended learning rate.
- Starting LoRA fine-tuning at a high peak rate with no warmup; the adapter's `B` matrix is zero-initialized
  so the update starts as a no-op, and the first few steps can set a bad direction that later steps only
  partially undo.

## When to use
Use warmup in essentially every run that uses Adam-family optimizers, which is every run in modern
AI engineering. For fine-tuning, default to a few percent of total optimizer steps and verify the
step count. For pre-training or large-batch training, size it in the thousands of steps and increase it
when raising the peak learning rate or batch size. Skip it only in tiny experiments where divergence is
cheap to notice and restart. Measure it by logging the learning rate and gradient norm from step zero:
a healthy run shows the norm falling as the rate rises. In interviews, the question is rarely "what is
warmup" but "why does Adam need it", and the answer is the variance of early second-moment estimates
plus large, poorly conditioned initial gradients.

## Role lens
- **Engineer:** Computes warmup from the real optimizer step count, logs learning rate and grad norm from
  step zero, and reads early loss spikes as a warmup or clipping problem before blaming data.
- **Architect:** Sets the stability recipe for the organization's training stack (warmup length, clipping,
  Pre-LN) so that teams scaling batch or learning rate do not rediscover divergence independently.
- **FDE:** When a customer's fine-tuning job diverges on their own data, checks warmup and step accounting
  in their config first, since a mis-sized ramp is the most common cause and the cheapest fix.

## Check yourself
**Q:** A colleague argues that because the model uses Pre-LN and gradient clipping at 1.0, warmup is
redundant and they will fine-tune at a constant 1e-4 from step one. What do you expect to happen, and why?
**A:** Pre-LN removes the depth-dependent gradient blow-up and clipping bounds the size of any one step,
but neither fixes Adam's early second-moment estimates. In the first steps `v` is estimated from one or
two gradients per parameter, so individual parameters whose first observed gradients were small receive
updates far larger than intended even after clipping, because clipping acts on the raw gradient, not on
Adam's normalized step. Expect a loss that drops briefly and then spikes within the first hundred steps,
or a run that converges but to a worse final loss than the same recipe with a 50-step ramp. The cost of
warmup is a few percent of a short run, and the fix for any observed spike is cheaper up front than after
a wasted run.

## Quiz
### Q: Why does Adam specifically benefit from learning-rate warmup?
- [ ] Adam has no bias correction, so early updates are biased toward zero
- [x] Its per-parameter second-moment estimate is built from very few samples early on, so normalized step sizes have high variance until enough gradients have been seen
- [ ] Adam's momentum term is zero at the start, so updates are too small without warmup
- [ ] Adam requires the learning rate to be below the Lipschitz constant, which warmup estimates
> Bias correction fixes the mean of the moment estimates, not their variance; the early `v` is simply
> noisy. Warmup keeps the learning rate small while that estimate stabilizes.

### Q: A run with gradient accumulation of 8 is configured with 400 warmup steps and 10,000 micro-batches in total. How much of the run is warmup?
- [ ] 4%, because warmup is counted in micro-batches
- [x] 32%, because there are only 1,250 optimizer steps and warmup counts those
- [ ] 0.4%, because warmup counts tokens
- [ ] It depends on the number of GPUs
> Schedulers advance per optimizer step. 10,000 micro-batches at accumulation 8 is 1,250 optimizer steps,
> so 400 warmup steps is nearly a third of the run, far longer than intended.

### Q: You resume a crashed pre-training run from a checkpoint that includes optimizer state. Should warmup repeat?
- [ ] Yes, because the GPU memory state was lost
- [ ] Yes, warmup is always required after any restart
- [x] No; the second-moment estimates are already populated, so the scheduler should continue from its saved step
- [ ] Only if the learning rate at the crash point was above the warmup peak
> The purpose of warmup is to protect against unreliable early optimizer statistics and a random init.
> With optimizer state restored, neither applies, and repeating the ramp just slows the next few hundred steps.
