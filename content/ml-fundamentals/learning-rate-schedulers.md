---
id: learning-rate-schedulers
module: ml-fundamentals
name: Learning-rate schedulers
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
related:
  - ml-fundamentals/warmup
  - ml-fundamentals/optimizers
  - ml-fundamentals/gradient-descent
  - ml-fundamentals/batch-size
  - ml-fundamentals/gradient-accumulation
  - training-adaptation/learning-rate
  - training-adaptation/checkpointing
sources:
  - Loshchilov & Hutter, 2017 — SGDR: Stochastic Gradient Descent with Warm Restarts
  - Vaswani et al., 2017 — Attention Is All You Need
  - Hu et al., 2024 — MiniCPM: Unveiling the Potential of Small Language Models with Scalable Training Strategies
  - Official docs: PyTorch torch.optim.lr_scheduler
---

## TL;DR
A fixed learning rate is a compromise: large enough to make progress early, it is too noisy to settle
into a good minimum late. A scheduler changes the learning rate over the run, ramping up briefly, holding
or slowly decaying, then annealing down, so the optimizer takes big steps while the loss surface is
forgiving and small ones when it is not. The schedule is defined over total optimizer steps, so it must be
planned together with batch size and training length.

## Why it exists
Stochastic gradient descent does not converge to a point with a constant learning rate; it converges
to a *region*. Each mini-batch gradient is the true gradient plus noise, and the update `θ ← θ − η·g`
carries that noise into the weights. In a quadratic bowl the iterates end up bouncing around the minimum
with a spread proportional to `η × noise variance`. Halve η and the spread shrinks; the only way to
actually reach the bottom is to make η small eventually.

But a small η from the start is wasteful. Early in training the gradient is large and consistently
pointed, and the model is far from any minimum, so a small step just takes many more iterations to cover
the same distance. Worse, a very small learning rate can leave the model stuck in a poor flat region that
a bigger step would have carried it past. Adaptive optimizers such as Adam do not remove this tension:
Adam normalizes step sizes per parameter, but the global scale is still η, and the same noise-floor
argument applies.

The naive fix, "pick a medium η and train longer", pays twice: you lose the fast early progress of a high
η and still never get the low final loss of a small η. Schedulers resolve the conflict by making the
learning rate a function of time. They also solve a second, less obvious problem: at the very beginning
Adam's running estimates of gradient variance are built from a handful of samples and are unreliable, so
the first few hundred steps need a *smaller* learning rate than the main phase. That ramp is warmup,
covered in its own lesson; this lesson is about what happens after it.

## How it works
A scheduler is a function `η(t)` of the optimizer step `t`, given a peak value `η_max`, a warmup length
and a total length `T`. Frameworks implement it as a multiplier applied to the optimizer's base rate once
per optimizer step. The common shapes:

### Step decay
Multiply η by a factor (often 0.1) at fixed milestones. It is the oldest recipe, from image classification,
and it works because each plateau lets the model settle at one noise level before the next drop. Its
weakness is that the milestones are hyperparameters chosen by hand, and the abrupt drops produce visible
cliffs in the loss curve that make runs hard to compare.

### Linear decay
`η(t) = η_max × (1 − t/T)` after warmup. It decays to zero at exactly `T`. This is the default in the
Hugging Face `Trainer` and is the usual choice for fine-tuning, because fine-tuning runs are short, the
total number of steps is known, and reaching a very small η at the end is what makes the final weights
stable.

### Cosine decay
`η(t) = η_min + 0.5 × (η_max − η_min) × (1 + cos(π·t/T))`. It spends longer near `η_max` than linear
decay, then drops fastest in the middle and flattens again near `η_min`. The smooth tail is why it became
the standard for LLM pre-training; many published recipes decay to about 10% of the peak rather than
to zero so the last phase still learns. The catch is that `T` must be fixed in advance: the shape is
anchored to the end point, and training past `T` or stopping early both produce a different, usually
worse, result than planning the right `T` from the start.

### Inverse square root
The original Transformer used `η(t) ∝ min(t^−0.5, t·warmup^−1.5)`, i.e. linear warmup followed by
`1/√t` decay. It needs no total length, which is convenient, but it decays slowly and never gets small
enough for the final-loss benefits of an explicit anneal.

### Warmup–stable–decay (WSD)
Hold `η_max` constant for most of the run, then decay sharply (linearly or cosine-like) over the final
10–20% of steps. Because the stable phase has no end point baked in, you can continue training, or branch
a checkpoint from the stable phase and run a short decay to get a "finished" model at any point. Work
on MiniCPM and related scaling studies showed that this matches cosine at the same total steps while
removing the need to commit to `T` up front, which is why newer pre-training recipes often use it.

### Cyclical and warm restarts
SGDR restarts the cosine cycle periodically, jumping η back up. The intuition is that a jump kicks the
model out of a sharp basin into a wider one; the practical benefit is cheap snapshot ensembles, one per
cycle. It is rare in LLM work but common in smaller vision and tabular models. One-cycle (`OneCycleLR`)
is a single version of the same idea for short runs: ramp up to a high peak over the first part of the
run, then anneal well below the starting value, so a fixed small budget gets both phases.

### Metric-driven: reduce on plateau
`ReduceLROnPlateau` cuts η by a factor when a monitored validation metric stops improving for a set
number of evaluations. It needs no horizon, which is attractive, but it makes the schedule depend on the
evaluation cadence and on noise in the validation metric: a noisy eval triggers early drops, and two runs
with identical configs can follow different schedules, which makes them hard to compare. It survives in
classic supervised training with cheap, stable validation; LLM recipes prefer a schedule written as a
function of the step so that it is reproducible and known in advance.

### What happens past `T`
Implementations disagree, and it matters whenever a run is extended. A hand-written schedule usually
clamps: `progress = min(1, ...)`, so η stays at the floor forever. Hugging Face's linear schedule clamps
at zero. But a cosine written as `cos(π·t/T)` without a clamp keeps going: past `T` the cosine climbs
back up, so η rises again towards the peak and reaches it at `2T`. PyTorch's `CosineAnnealingLR` and the
Hugging Face cosine-with-warmup lambda both behave this way if you step them past their configured length.
An extended run therefore either learns almost nothing (clamped) or silently re-warms to a high rate with
no warmup (unclamped); neither is the plan, and the learning-rate log is the only place it shows.

### What the scheduler steps on
Schedulers advance per *optimizer* step, not per micro-batch and not per epoch. With gradient accumulation
of 8, one scheduler step happens every 8 forward/backward passes. If `T` is computed from the number of
micro-batches, the schedule finishes 8× too early and the model trains most of the run at η ≈ 0.

## Example
Linear warmup followed by cosine decay to 10% of peak, written as a plain `LambdaLR` so the arithmetic
is visible. The printout checks the shape at a few known points: step 0 is zero, the end of warmup is the
peak, and halfway through the decay phase the multiplier is exactly the midpoint between 1.0 and 0.1.

```python
import math, torch

peak_lr, warmup, total, floor = 3e-4, 100, 1000, 0.1

def multiplier(step):
    if step < warmup:
        return step / warmup                       # linear ramp 0 -> 1
    progress = min(1.0, (step - warmup) / max(1, total - warmup))  # clamp: stay at floor past T
    cosine = 0.5 * (1 + math.cos(math.pi * progress))   # 1 -> 0
    return floor + (1 - floor) * cosine            # 1 -> floor

params = [torch.nn.Parameter(torch.zeros(10))]
opt = torch.optim.AdamW(params, lr=peak_lr)
sched = torch.optim.lr_scheduler.LambdaLR(opt, multiplier)

for step in range(total):
    # loss.backward(); opt.step()  -- one optimizer step per scheduler step
    opt.step(); sched.step()
    if step in (0, 99, 549, 999):
        print(step, f"{opt.param_groups[0]['lr']:.2e}")
# 0    -> 3.00e-06   (1/100 of the way up the ramp)
# 99   -> 3.00e-04   (end of warmup: peak)
# 549  -> 1.65e-04   (midpoint of decay: 0.1 + 0.9*0.5 = 0.55 of peak)
# 999  -> 3.00e-05   (floor: 10% of peak)
```

The same shape is available as `transformers.get_cosine_schedule_with_warmup`, and the WSD variant as
`get_wsd_schedule`; the hand-written version is here so the numbers can be checked by eye.

## Trade-offs
- Cosine and linear decay give the lowest final loss for a fixed budget, but they require knowing the
  total step count in advance; the moment training length is uncertain, WSD or a constant-then-cooldown
  schedule is the better trade.
- A higher floor (decay to 10% rather than 0) keeps the model learning to the end and makes continued
  pre-training easier, at the cost of a slightly noisier final checkpoint than a full anneal.
- Warm restarts and cyclical schedules buy ensemble diversity and sometimes escape sharp minima, but each
  restart costs a temporary loss increase that a short fine-tuning run cannot afford.
- Inverse-square-root needs no horizon and is robust to running longer, but its slow tail leaves loss on
  the table compared with an explicit decay phase of the same length.

## Pitfalls
- Calling `scheduler.step()` once per epoch with a per-step schedule (or vice versa); the symptom is a
  learning rate that is either stuck at the warmup value or collapses to near zero within the first epoch.
- Computing `num_training_steps` from micro-batches when gradient accumulation is on, so the schedule
  finishes early and the second half of the run trains at a learning rate close to zero.
- Resuming from a checkpoint without restoring scheduler state, which restarts warmup or resets the
  decay; the loss curve shows a jump at the resume point that was not there before.
- Extending a cosine run past its planned end without re-planning it: with a clamped schedule the extra
  steps run at the floor and barely move the model ("more data did not help"); with an unclamped cosine
  the rate climbs back towards the peak and the loss rises. Either way the logged learning rate shows it.
- Reading the sharp loss drop during the decay phase as a sign the model suddenly "got it"; the drop is the
  noise floor shrinking as η falls, and it appears in every well-scheduled run.

## When to use
Every real training run has a schedule; the decision is which one, and it is usually a smaller decision
than the peak value: among reasonable decaying shapes, a well-tuned peak matters more than the choice
between linear and cosine, so tune the peak first (a short sweep over a factor of 2–3) and the shape second. For fine-tuning, use linear or cosine
decay with a short warmup and let the run end at a small learning rate, because the total steps are known
and stability of the final weights matters more than squeezing out the last fraction of loss. For
pre-training or long continued pre-training where the budget may change, prefer WSD or constant with a
planned cooldown so checkpoints remain useful at any point. Before launching, print the learning rate at
steps 0, end of warmup, midpoint and end, and check they match the plan; log the learning rate alongside
loss so a bad curve can be attributed. In interviews, the expected depth is being able to explain *why*
decay lowers final loss (noise floor), why the schedule must be tied to optimizer steps, and when cosine's
fixed horizon is a liability.

## Role lens
- **Engineer:** Sets `lr_scheduler_type`, `warmup_ratio` and `num_training_steps` in the training config, and
  verifies the schedule by logging the learning rate rather than trusting defaults.
- **Architect:** Decides the training-budget policy: whether runs have fixed horizons (cosine) or must be
  extendable and checkpoint-branchable (WSD), which shapes the data pipeline and evaluation cadence.
- **FDE:** Diagnoses a customer's "fine-tuning plateaued" complaint by inspecting the schedule first, since a
  mis-set step count or a missing decay explains most such reports before touching the data.

## Check yourself
**Q:** A team fine-tunes with cosine decay over 3,000 steps, then decides the model is undertrained and
runs 3,000 more steps by resuming the checkpoint with the same config. The extra steps barely change the
loss. What happened, and what should they have done?
**A:** The resumed run restored the scheduler at step 3,000 with the same 3,000-step horizon. Their
schedule clamps at the floor (the observation of "barely changes" says so), so all 3,000 extra steps ran
at the minimum learning rate and the weights hardly moved; with an unclamped cosine the same mistake would
instead have re-warmed the rate towards the peak with no warmup, which shows up as a loss rise. Either way
the experiment says nothing about whether more training helps. Options: restart from the original checkpoint with `T = 6,000`
so the schedule is planned for the full length; or, if resuming is required, create a new schedule that
re-warms briefly to a moderate peak and decays over the extra steps. Going forward, a WSD-style schedule
would let them extend the stable phase and apply the decay only once the final length is known. The
general lesson is that the schedule is a function of planned total steps, so changing the plan means
changing the schedule.

## Quiz
### Q: Why does decaying the learning rate late in training usually lower the final loss?
- [ ] It reduces the model's capacity, which acts as regularization
- [x] Mini-batch gradient noise makes the weights bounce in a region whose size scales with the learning rate, so a smaller rate lets them settle closer to the minimum
- [ ] It forces the optimizer to revisit earlier examples more often
- [ ] It compensates for Adam's second-moment estimate becoming too large
> SGD with a constant rate converges to a noise-limited region, not a point; shrinking η shrinks that region.
> Capacity is unchanged, and Adam's second moment is a separate per-parameter scaling.

### Q: A run uses gradient accumulation of 4 and the schedule's total steps were set to the number of micro-batches. What goes wrong?
- [ ] The learning rate peaks at 4× the intended value
- [ ] Warmup takes 4× longer than intended
- [x] The schedule completes after a quarter of the run, so most training happens at the floor learning rate
- [ ] Nothing, because schedulers count micro-batches internally
> Schedulers advance once per optimizer step, and with accumulation of 4 there are a quarter as many
> optimizer steps as micro-batches, so a schedule sized in micro-batches reaches its end four times too early.

### Q: Which schedule lets you branch a usable "finished" model from a checkpoint partway through a long pre-training run?
- [ ] Cosine decay to zero
- [ ] Step decay with fixed milestones
- [x] Warmup–stable–decay, by running a short decay from a stable-phase checkpoint
- [ ] Inverse square root, because it has no horizon
> WSD keeps the learning rate constant for most of the run, so any stable-phase checkpoint can be annealed
> with a short decay to get the final-loss benefit. Cosine's shape is anchored to a fixed end point, and
> inverse-square-root never anneals sharply.
