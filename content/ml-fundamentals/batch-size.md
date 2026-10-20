---
id: batch-size
module: ml-fundamentals
name: Batch size
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - ml-fundamentals/gradient-descent
  - ml-fundamentals/gradient-accumulation
  - ml-fundamentals/learning-rate-schedulers
  - ml-fundamentals/warmup
  - ml-fundamentals/mixed-precision
  - training-adaptation/gradient-checkpointing
  - training-adaptation/distributed-training
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
sources:
  - McCandlish et al., 2018 — An Empirical Model of Large-Batch Training
  - Goyal et al., 2017 — Accurate, Large Minibatch SGD: Training ImageNet in 1 Hour
  - Shallue et al., 2019 — Measuring the Effects of Data Parallelism on Neural Network Training
  - Keskar et al., 2017 — On Large-Batch Training for Deep Learning: Generalization Gap and Sharp Minima
---

## TL;DR
Batch size is how many examples (for LLMs, how many tokens) contribute to one gradient estimate. A
larger batch gives a less noisy gradient and better hardware utilization, so each step is worth more and
costs less per example, but only up to a point: past the *critical batch size* set by gradient noise,
extra examples per step stop reducing the number of steps needed and simply burn data. Batch size,
learning rate and step count are therefore one coupled decision, not three.

## Why it exists
Full-batch gradient descent computes the exact gradient over the whole dataset once per step. For a
dataset of billions of tokens that is absurd: one step would take hours and the model would learn nothing
until it finished. Stochastic gradient descent instead estimates the gradient from a sample, and the
batch size is the sample size. That makes it a statistics question. The mini-batch gradient is an
unbiased estimate of the full gradient with variance proportional to `1/B`. Small `B` gives cheap,
noisy steps; large `B` gives expensive, accurate ones.

Hardware pushes in one direction. A GPU multiplies matrices efficiently only when they are large, and
the number of tokens flowing through each layer (batch × sequence length) is what makes the activations
large. A batch of only a few hundred tokens leaves most of the chip idle and makes the step
memory-bandwidth bound (weights are read once per step regardless of batch, so more tokens per step
amortize that read). Doubling the batch until the GPU is compute-bound is close to
free in wall-clock per example. Data parallelism pushes the same way: each additional GPU usually adds
its share of examples to the batch rather than splitting a fixed batch finer.

Optimization pushes back. Once the gradient estimate is already accurate, more samples do not change the
direction of the step, so a batch twice as large produces essentially the same update and the model
learns the same amount per step from twice the data. The run needs the same number of steps but consumes
twice the tokens: wasted compute, and for a fixed dataset, fewer epochs' worth of unique signal. The naive
policy "use the biggest batch that fits" is therefore wrong in both directions: too small wastes hardware,
too large wastes data. The useful question is where the crossover is, and the answer depends on the
noise in the gradient, which changes over training.

## How it works
### Gradient noise and the critical batch size
Write the per-example gradient as the true gradient `G` plus noise with covariance `Σ`. The *gradient
noise scale* is roughly `B_noise = tr(Σ) / |G|²`: the batch size at which the noise in the estimate is
comparable to the signal. The empirical model of large-batch training gives a clean relation between the
steps `S` and examples `E` a run needs at batch `B`, relative to the minimums `S_min` (infinite batch) and
`E_min` (infinitesimal batch):

```
S = S_min × (1 + B_noise / B)        steps
E = E_min × (1 + B / B_noise)        examples processed  (E = S × B)
```

With `B ≪ B_noise`, doubling `B` roughly halves `S` for the same `E`: perfect scaling, free speed-up from
more hardware. With `B ≫ B_noise`, doubling `B` leaves `S` unchanged and doubles `E`: pure waste. At
`B = B_noise` both are 2× their minimum, which is why the noise scale is a sensible "critical" batch size.
Two consequences follow. The noise scale *grows* during training, because `|G|` shrinks as the loss falls
while the per-example noise does not, so the useful batch size is larger late in training than early;
this is why some pre-training recipes ramp the batch size up over the first part of the run. And the
noise scale tracks how low a loss the run reaches and how hard the task is: the scaling-law work of
Kaplan et al. found the critical batch to depend mainly on the loss rather than directly on parameter
count (later studies dispute the details), which is why LLM pre-training, reaching low losses on a hard
task, uses batches of millions of tokens while a small classifier saturates at a few hundred examples.

### Measuring the noise scale
You do not have to guess it. Because the squared norm of a mini-batch gradient is biased upward by the
noise, `E|G_B|² = |G|² + tr(Σ)/B`, measuring the gradient norm at two batch sizes gives two equations in
the two unknowns:

```
|G|²   ≈ (B_big · |G_big|² − B_small · |G_small|²) / (B_big − B_small)
tr(Σ)  ≈ (|G_small|² − |G_big|²) / (1/B_small − 1/B_big)
B_simple = tr(Σ) / |G|²
```

Under data parallelism both are free: each rank's local gradient before the all-reduce is the small
batch, and the reduced gradient is the big one. The individual estimates are noisy, so average the
numerator and denominator separately over many steps (an exponential moving average) before dividing.
`B_simple` assumes a well-conditioned loss surface, so treat it as an order-of-magnitude guide to where
the critical batch is and how it moves during training, not a value to set to the nearest token.

### Learning rate must move with batch size
A bigger batch justifies a bigger step, because the step is computed from a more trustworthy gradient.
For SGD the *linear scaling rule* (multiply the learning rate by `k` when multiplying the batch by `k`)
works well in the perfect-scaling regime, provided the run has warmup; this is the recipe that trained
ImageNet in an hour at a batch of 8,192. For Adam-family optimizers the picture is less clean: square-root
scaling is a common starting point with some theoretical support, but in practice the peak learning rate
is re-tuned when the batch changes by more than a small factor. Either way, changing the batch without
touching the learning rate compares two different optimizers, not two batch sizes.

### Generalization
Early work observed that very large batches reached sharper minima and generalized worse. Later, larger
studies showed most of that gap closes when the learning rate, warmup and number of steps are re-tuned
for the larger batch; what remains is the hard ceiling from the noise scale, not a mysterious property of
big batches. The practical reading: a large batch is not harmful by itself, but copying a small-batch
recipe to a large batch is.

### Memory and the effective batch
Activation memory scales linearly with the batch (and roughly linearly with sequence length under fused
attention kernels, quadratically without them), while weight, gradient and optimizer memory do not. On a given GPU the largest *micro-batch* that fits is a hardware fact; the
*effective batch* that the optimizer sees is

```
effective_batch = micro_batch × gradient_accumulation_steps × data_parallel_world_size
```

and for LLMs it is usually quoted in tokens: `effective_batch × sequence_length`. Gradient accumulation
and more GPUs change the effective batch without changing per-GPU memory; gradient checkpointing and
mixed precision change how large the micro-batch can be. These are different knobs for different
constraints, and conflating them ("I added GPUs so I can raise the learning rate" is right; "I added
accumulation so training is faster" is wrong) is a common source of confusion.

### Inference is a different problem
Batching requests at serving time shares the name but not the trade-off: it is about amortizing weight
reads across concurrent users, with latency rather than gradient noise as the cost. That lives in the
serving module under continuous batching.

## Example
A planning function that turns a hardware constraint and a token budget into the three numbers that
actually get written into a config, with the learning-rate adjustment made explicit. The numbers are
illustrative; the structure is what matters.

```python
def plan(tokens_budget, seq_len, target_batch_tokens,
         micro_batch_per_gpu, n_gpus, base_lr, base_batch_tokens, rule="sqrt"):
    seqs_per_step = target_batch_tokens // seq_len
    per_micro_step = micro_batch_per_gpu * n_gpus
    accum = max(1, seqs_per_step // per_micro_step)
    eff_tokens = accum * per_micro_step * seq_len          # what the optimizer actually sees
    steps = tokens_budget // eff_tokens
    ratio = eff_tokens / base_batch_tokens
    lr = base_lr * (ratio ** 0.5 if rule == "sqrt" else ratio)
    return dict(accum=accum, eff_tokens=eff_tokens, steps=steps, lr=lr)

print(plan(tokens_budget=10_000_000_000, seq_len=4096, target_batch_tokens=1_000_000,
           micro_batch_per_gpu=4, n_gpus=8, base_lr=3e-4, base_batch_tokens=524_288))
# -> accum 7, eff_tokens 917504, steps 10899, lr ~3.97e-4
#   244 seqs/step wanted, 32 per micro-step -> 7 accumulation steps -> 224 seqs = 0.92M tokens
#   10B tokens / 0.92M = ~10.9k optimizer steps; sqrt-scaling from a 0.5M-token recipe at 3e-4
#   gives ~4.0e-4 (linear scaling would give 5.25e-4; either is a starting point to re-tune).
```

The same function with `n_gpus=64` returns `accum=1` and `eff_tokens=1,048,576`: the target batch is now
reached without accumulation, the step count drops to ~9.5k, and the learning rate moves only slightly
(to ~4.2e-4) because the effective batch moved only slightly. That is the point: adding GPUs changed
throughput, not the optimization problem, because the plan held the effective batch roughly fixed.

## Trade-offs
- Larger batches raise GPU utilization and cut wall-clock per token, but past the noise scale they stop
  reducing the step count, so each extra example is paid for in data and compute without a loss benefit.
- Small batches give more optimizer steps per token and a regularizing noise, which helps on small
  datasets, but they under-use accelerators and make the step bandwidth-bound rather than compute-bound.
- Scaling the learning rate with the batch keeps the optimizer comparable across hardware sizes, yet the
  exact rule (linear, square-root, re-tune) depends on the optimizer and breaks down near the noise scale.
- A batch-size ramp matches the growing noise scale during training and saves data early, at the cost of
  a schedule whose step count and learning rate must be tied to tokens rather than steps.
- Reaching a large effective batch through accumulation costs nothing in memory but delivers no speed-up;
  reaching it through more GPUs costs hardware but actually shortens the run.

## Pitfalls
- Doubling the batch (or the GPU count) and leaving the learning rate alone; the run trains noticeably
  slower per token and the team blames the larger batch rather than the untouched learning rate.
- Quoting batch size in sequences when sequence length varies between experiments; two runs with "batch
  64" at 2k and 8k context see 4× different numbers of tokens per gradient.
- Sizing a schedule in steps, then changing the batch, so warmup and decay now cover a different fraction
  of the data than intended.
- Raising the batch to push GPU utilization and watching loss-per-token flatten; the symptom of exceeding
  the noise scale is more tokens for the same loss, visible when runs are plotted against tokens rather
  than steps.
- Assuming batch size does not matter for fine-tuning because the dataset is small; with a few thousand
  examples, a batch of 128 may give only a few dozen optimizer steps per epoch, too few for the optimizer
  to do anything.

## When to use
Treat batch size as the first number fixed in a training plan, because the learning rate, warmup and step
count all follow from it. For fine-tuning, start small enough to get at least a few hundred optimizer
steps over the run, then raise the micro-batch until the GPU is busy and use accumulation only to reach a
target effective batch. For pre-training or large-scale continued training, pick the effective batch from
the model's expected noise scale (prior runs of similar size are the best guide, and the two-batch
estimator measures it on the live run), consider ramping it,
and scale the learning rate when the batch changes. Always compare runs on loss versus tokens, not loss
versus steps. In interviews, the depth expected is the noise-scale argument, the coupling with learning
rate, and the distinction between micro-batch, effective batch and tokens per step.

## Role lens
- **Engineer:** Writes the micro-batch, accumulation and world-size arithmetic into the config, logs tokens
  per step, and re-tunes the learning rate whenever the effective batch changes.
- **Architect:** Decides how training throughput is scaled across the fleet (more GPUs at fixed effective
  batch versus larger batches) with the noise-scale ceiling in mind, because it bounds useful parallelism.
- **FDE:** Translates a customer's hardware constraint into an effective-batch plan their fine-tuning job can
  actually run, and explains why their small dataset needs a smaller batch than the vendor default.

## Check yourself
**Q:** A team moves a pre-training job from 8 GPUs to 64 GPUs by keeping the per-GPU micro-batch and
dropping gradient accumulation from 8 to 1, so the effective batch is unchanged. Throughput rises 8× but
a colleague proposes "since we have 8× the GPUs we should also use an 8× larger batch and finish faster".
How do you evaluate that?
**A:** The first move is sound: the effective batch, learning rate and schedule are unchanged, so the
optimization problem is identical and the run simply finishes sooner. The proposal is a different
experiment. Multiplying the batch by 8 only shortens the run if the current batch is well below the
gradient noise scale; if it is near or above it, the step count barely falls and the run consumes up to
8× more tokens for the same loss. The honest evaluation is to estimate the noise scale, which with 64 data-parallel ranks is nearly free
(compare each rank's local gradient norm with the all-reduced norm, as in the two-batch estimator), or
take it from prior runs of this model size, scale the learning rate (square-root for Adam as a
starting point, then re-tune), lengthen warmup, and compare both configurations on loss versus tokens.
If the data budget is fixed, exceeding the noise scale also means fewer effective passes over unique
data, which is a quality cost, not just a compute one.

## Quiz
### Q: What determines the point beyond which increasing batch size stops reducing the number of training steps needed?
- [ ] GPU memory capacity
- [ ] The number of layers in the model
- [x] The gradient noise scale, the batch size at which the gradient estimate's noise becomes comparable to its signal
- [ ] The learning-rate schedule's total step count
> Once the mini-batch gradient is already an accurate estimate, more samples do not change the step, so
> steps stop falling while tokens consumed keep rising. Memory limits the micro-batch, not the useful batch.

### Q: A fine-tuning run doubles its effective batch size and keeps everything else fixed. What is the most likely immediate effect?
- [ ] Training loss per token improves because gradients are more accurate
- [x] Progress per token slows, because the learning rate is now too small for the less noisy gradient
- [ ] Memory per GPU doubles even if the micro-batch is unchanged
- [ ] The warmup period becomes twice as long in tokens, improving stability
> A larger batch justifies a larger step; holding the learning rate fixed means each step is as cautious
> as before but there are half as many of them. Per-GPU memory depends on the micro-batch, not the effective batch.

### Q: Which change increases the effective batch size without changing per-GPU memory or the time per optimizer step?
- [ ] Enabling gradient checkpointing
- [ ] Switching from fp32 to bf16
- [x] Adding data-parallel GPUs while holding the per-GPU micro-batch constant
- [ ] Doubling the sequence length
> Each data-parallel rank contributes its micro-batch to the effective batch, so more ranks mean a larger
> batch per step at the same per-GPU footprint. Checkpointing and bf16 change what fits, not the batch itself.
