---
id: gradient-accumulation
module: ml-fundamentals
name: Gradient accumulation
kind: practice
difficulty: core
est_minutes: 11
status: reviewed
related:
  - ml-fundamentals/batch-size
  - ml-fundamentals/gradient-clipping
  - ml-fundamentals/learning-rate-schedulers
  - ml-fundamentals/warmup
  - ml-fundamentals/mixed-precision
  - ml-fundamentals/optimizers
  - training-adaptation/gradient-checkpointing
  - training-adaptation/distributed-training
sources:
  - Official docs: PyTorch DistributedDataParallel (no_sync)
  - Official docs: Hugging Face Accelerate, gradient accumulation
  - Rajbhandari et al., 2020 — ZeRO: Memory Optimizations Toward Training Trillion Parameter Models
---

## TL;DR
Gradient accumulation runs several small micro-batches and lets their gradients add up before one
optimizer step. Because the gradient of a mean loss is linear in the examples, the sum equals the
gradient of the larger batch, so the optimizer sees the batch the recipe asked for while GPU memory only
ever holds one micro-batch of activations. It costs no memory and changes nothing in the optimization,
but it buys no speed: every micro-batch still runs.

## Why it exists
Memory on a training GPU splits into two very different parts. Model state (weights, gradients, optimizer
moments) is fixed by the parameter count: with Adam in mixed precision it is about 16 bytes per parameter,
so a 7B model needs roughly 112 GB before a single example is processed. Activations, the intermediate
tensors kept for backward, scale with micro-batch × sequence length × hidden size × layers, and they are
the only part that shrinks when you process fewer examples at once. On a given card the largest
micro-batch that fits is therefore a hardware fact, illustratively 1–8 sequences for a multi-billion
parameter model at a few thousand tokens of context.

The training recipe does not care about that fact. It wants an effective batch chosen from the
gradient-noise scale, and a learning rate tuned for that batch: hundreds of sequences for fine-tuning,
millions of tokens for pre-training. Simply training at the micro-batch that fits is a different
experiment: the gradient is noisier, the learning rate tuned for the large batch is now too high, the
schedule gets many more steps than planned, and per-step overhead (optimizer update, communication,
kernel launches) is paid every few examples instead of every few hundred.

The naive alternatives are to buy memory (a bigger GPU, or more GPUs under data parallelism) or to shrink
the model (frozen or quantized parameters). Both are legitimate; both cost money or capability.
Accumulation is the free option. It exploits the fact that the gradient of a sum of per-example losses is
the sum of per-example gradients, so the large-batch gradient can be assembled piece by piece without
the large batch ever existing in memory.

## How it works
### The arithmetic
For a batch of `B` examples with mean loss `L = (1/B) Σ ℓ_i`, the gradient is `(1/B) Σ ∇ℓ_i`. Split the
batch into `k` micro-batches of `b = B/k` examples. Micro-batch `j` has mean loss `L_j` and gradient
`∇L_j`, and

```
∇L = (1/k) × Σ_j ∇L_j
```

so dividing each micro-batch loss by `k` and summing the resulting gradients reproduces the big-batch
gradient exactly, as long as the weights do not change in between, which is precisely the situation
inside one optimizer step. PyTorch makes the summing free: `backward()` *adds* into `.grad` rather than
overwriting it, which is the only reason `zero_grad()` exists. Accumulation is simply not calling
`zero_grad()` until the window is complete.

### The step, in order
1. `zero_grad()` once, at the start of the window.
2. For each of the `k` micro-batches: forward, divide the loss by `k`, `backward()`. Gradients add up in
   `.grad`; the activations of this micro-batch are freed as soon as its backward finishes, so peak
   memory is that of one micro-batch.
3. After the k-th backward: if using fp16 loss scaling, unscale; clip the global gradient norm once;
   `optimizer.step()`; `scheduler.step()`.

The optimizer, the scheduler, clipping and logging all operate on the window, because the window *is*
the batch. Warmup steps, `num_training_steps` and the "step" in every log line therefore mean optimizer
steps: a run with accumulation 8 that processes 80,000 micro-batches has taken 10,000 steps.

### Under data parallelism
DistributedDataParallel all-reduces gradients across ranks inside `backward()`, bucket by bucket, as
they become ready. With accumulation that would reduce `k` times per step, `k − 1` of them wasted,
because only the sum matters. The `model.no_sync()` context manager disables the reduction for the first
`k − 1` micro-batches so that one all-reduce carries the whole window; Accelerate's `accumulate()`
wrapper does the same bookkeeping. Under sharded training (FSDP, ZeRO) each rank normally keeps only its
shard of the gradient; postponing the reduce-scatter across the window means holding the full unsharded
gradient on every rank until the step, which is a memory cost, so those frameworks expose it as an
option and the trade is decided per job.

### When the sum is not the big-batch gradient
Two things break the identity. The first is any operation whose output depends on which examples share a
batch: BatchNorm computes statistics per micro-batch, so `k` micro-batches of 4 do not behave like one
batch of `4k`. Transformers use LayerNorm or RMSNorm, which are per-token, so this is a CNN concern.
The second is loss normalization. For language models the loss is usually a mean over *tokens*, and
micro-batches rarely contain the same number of non-padding tokens. `loss / k` with per-micro-batch means
gives a micro-batch holding 50 real tokens the same weight as one holding 4,000, which is not the
per-token mean of the window. The exact form is to sum token losses within each micro-batch and divide by
the total token count across the window, computed once before the loop. This mismatch was found in
widely used fine-tuning code in 2024 and showed up as a training loss that depended on the accumulation
count. Data parallelism adds one more level: DDP *averages* gradients across ranks, so dividing by each
rank's local token count yields a mean of per-rank means. The exact version all-reduces the window's token
count once, divides each rank's summed loss by that global count, and multiplies by the world size to
undo DDP's averaging.

### What it costs
Nothing in memory beyond the `.grad` buffers, which exist anyway. In time, `k` forward/backward passes
per step, exactly as many as a true batch of `kb` would need on an infinitely large GPU, minus the
amortization of per-step overhead. What it does not do is make training faster: throughput per token is
set by how busy the GPU is during each micro-batch, and accumulation does not touch that. Raising the
micro-batch until memory is nearly full (or the step is compute-bound) is what raises throughput;
accumulation then covers the remaining gap to the target effective batch. Gradient checkpointing is the
other compute-for-memory trade, and it is complementary: it shrinks per-sequence activation memory at
the price of recomputation, so it can make a long sequence fit at all, which accumulation cannot.

## Example
One optimizer step with token-weighted loss normalization, a single all-reduce per window under DDP, and
clipping and the scheduler applied once to the summed gradient. The model is assumed to be a Hugging Face
causal LM; the worked numbers underneath show the step accounting that schedulers must be given.

```python
import torch
from contextlib import nullcontext
from torch.nn.utils import clip_grad_norm_

def train_step(model, opt, sched, micro_batches, max_norm=1.0):
    """micro_batches: k dicts with input_ids and labels (-100 = ignore). Performs ONE optimizer step."""
    k = len(micro_batches)
    n_tokens = sum((mb["labels"][:, 1:] != -100).sum().item() for mb in micro_batches)  # whole window
    ddp = isinstance(model, torch.nn.parallel.DistributedDataParallel)
    opt.zero_grad(set_to_none=True)                     # once per window, not per micro-batch
    for j, mb in enumerate(micro_batches):
        sync_ctx = model.no_sync() if ddp and j < k - 1 else nullcontext()
        with sync_ctx:                                  # all-reduce only on the last micro-batch
            logits = model(mb["input_ids"]).logits[:, :-1]   # position t predicts token t+1
            labels = mb["labels"][:, 1:]                     # so shift labels left by one
            loss_sum = torch.nn.functional.cross_entropy(
                logits.reshape(-1, logits.size(-1)), labels.reshape(-1),
                ignore_index=-100, reduction="sum")
            (loss_sum / n_tokens).backward()            # grads add up in .grad across the k calls
    grad_norm = clip_grad_norm_(model.parameters(), max_norm)   # once, on the summed gradient
    opt.step(); sched.step()                            # one optimizer step, one scheduler step
    return grad_norm.item()

# Worked numbers: micro-batch 4 sequences x 2,048 tokens on one GPU, target 128 sequences per step.
#   k = 128 / 4 = 32 micro-batches per optimizer step.
#   100,000 micro-batches in the dataset -> 3,125 optimizer steps, so num_training_steps=3125 and
#   warmup=100 (3.2%) are what the scheduler must be given, not 100,000 and 3,200.
#   Peak memory: identical to k = 1.  Time per optimizer step: ~32x the time of one micro-batch.
#   With per-micro-batch means (loss.mean() / k) a micro-batch holding 300 real tokens would weigh
#   the same as one holding 8,000, so the gradient would differ from a true batch of 128.
```

## Trade-offs
- Reaches any effective batch on fixed memory at zero memory cost, but delivers zero speed-up: the
  forward/backward work runs serially, so time per optimizer step grows linearly with `k`.
- Exact for per-token mean losses and per-token normalization layers, but inexact for BatchNorm-style
  layers and for a naive `loss / k` under variable-length batches; the error grows with the spread in
  tokens per micro-batch.
- Skipping the all-reduce until the last micro-batch cuts DDP communication `k`-fold, but under FSDP or
  ZeRO it means holding unsharded gradients for the whole window, which can cost the memory accumulation
  was meant to save.
- A large `k` preserves the recipe's effective batch, yet optimizer steps per hour fall with it; on a small
  fine-tuning set, fewer and larger steps can be worse than more and smaller ones, which is a batch-size
  question that accumulation cannot answer.
- Compared with gradient checkpointing, accumulation costs no extra compute but cannot make a single long
  sequence fit; checkpointing costs roughly one extra forward pass per layer but shrinks per-sequence
  activation memory. Most memory-constrained runs end up using both.

## Pitfalls
- Forgetting to divide the loss by `k` (or by the window's token count): the summed gradient is `k`×
  too large, which acts like a `k`× learning rate for SGD and distorts clipping thresholds and logged
  gradient norms for Adam.
- Stepping the scheduler, or counting warmup, per micro-batch; decay finishes `k`× early and the symptom
  is a run that spends most of its length at the floor learning rate.
- Clipping inside the micro-batch loop: each clip rescales a partial sum, so the final gradient's
  direction and magnitude depend on `k`. Clip once after the last backward.
- Calling `zero_grad()` at the top of every micro-batch, copied from a plain loop: only the last
  micro-batch's gradient survives, so the model trains on a batch of `b` with a learning rate tuned for
  `kb`, and the loss curve is far noisier than the config suggests.
- Expecting faster training: tokens per second are unchanged or slightly lower, and the person who set
  `gradient_accumulation_steps=16` "for speed" has made each optimizer step 16× slower.
- Per-micro-batch loss means on variable-length data: the training loss shifts when `k` changes and short
  sequences are over-weighted; fix by summing token losses and dividing by the window's token count (and
  under DDP, by the global count across ranks if exactness matters).
- Handling the last partial window of an epoch inconsistently: a step from 3 micro-batches divided by
  `k = 8` is a 3/8-size step; either drop the remainder or normalize by the actual count.

## When to use
Use it whenever the micro-batch that fits is smaller than the effective batch the recipe calls for, which
describes most single-GPU and small-cluster fine-tuning. The order of operations: first raise the
micro-batch until memory is nearly full or the GPU is compute-bound, because that is what raises
throughput; then set accumulation to close the gap to the target effective batch; then size warmup and
total steps in optimizer steps. Do not use it to push the effective batch past the gradient-noise scale:
that costs data for no gain whether the batch comes from GPUs or from accumulation. Measure tokens per
optimizer step, step time and peak memory, and log the effective batch explicitly. In interviews, the
expected depth is the linearity argument for why the sum is the big-batch gradient, the ordering with
clipping, loss scaling and the scheduler, the `no_sync` communication point, and the plain statement that
it saves memory, not time.

## Role lens
- **Engineer:** Writes the accumulation loop, or configures the trainer, with per-window loss
  normalization, clipping and scheduler stepping, and verifies optimizer-step counts and tokens per step
  in the logs before launching.
- **Architect:** Decides when accumulation on existing hardware is acceptable versus when the schedule
  demands more GPUs, since accumulation holds the effective batch constant but stretches wall-clock
  linearly.
- **FDE:** Makes a customer's fine-tuning recipe run on the single 24 GB or 80 GB card they actually have
  by trading micro-batch for accumulation, and explains why the job is now correct but not faster.

## Check yourself
**Q:** A colleague reports that with `gradient_accumulation_steps=8` their training loss at step 500
differs from a run with a true batch 8× larger on a bigger GPU, and asks whether accumulation is
"approximate". What should be identical, what can legitimately differ, and which bugs would explain a
real discrepancy?
**A:** For a transformer with a per-token mean loss, the accumulated gradient equals the large-batch
gradient in exact arithmetic, so the two runs should follow the same trajectory. Legitimate differences
are small: floating-point summation order under bf16 or fp16, different dropout random streams, and
different data order if the loaders are not seeded identically. Because the gradient is the same, Adam's
`m` and `v` and therefore every update are the same too; there is no optimizer-side reason for drift. Anything larger points at a bug. The
usual suspects, in order: the loss normalized as a per-micro-batch mean divided by 8 when sequences
vary in length, so short micro-batches are over-weighted; the scheduler or warmup counting micro-batches,
so the learning rates at "step 500" are not the same; clipping applied inside the loop; `zero_grad()`
inside the loop, which discards seven of the eight gradients; or a BatchNorm layer somewhere. The clean
test is a tiny model in fp32 with dropout off and the same data order: the gradients after one window
and after one large batch should agree to around 1e-6, and whichever step of the loop breaks that
agreement is the bug.

## Quiz
### Q: Why is each micro-batch loss divided by the accumulation count k before `backward()`?
- [ ] To keep the activations small enough to fit in memory
- [x] So the k summed gradients equal the gradient of the mean loss over the whole window instead of k times it
- [ ] Because PyTorch overwrites `.grad` on every backward call
- [ ] To make the scheduler advance once per window
> `backward()` adds into `.grad`, so k unscaled micro-batch gradients sum to k times the big-batch
> gradient, which behaves like a k× learning rate. Dividing by k restores the mean; memory and the
> scheduler are unaffected by this division.

### Q: A run raises gradient accumulation from 1 to 16 while keeping the micro-batch fixed. Which statement is correct?
- [ ] Peak GPU memory rises roughly 16×
- [ ] Tokens per second rise because the optimizer runs less often
- [x] Peak memory is unchanged and each optimizer step takes about 16× longer
- [ ] The learning rate must be divided by 16 to compensate
> Only one micro-batch of activations is alive at a time, so memory is flat, and the 16 forward/backward
> passes run one after another. The larger effective batch would, if anything, justify a larger learning
> rate, not a smaller one.

### Q: In which situation does gradient accumulation NOT reproduce the gradient of one large batch?
- [ ] When the model uses LayerNorm and the loss is a per-token mean over the whole window
- [ ] When training under DDP with `no_sync()` on all but the last micro-batch
- [x] When the network contains BatchNorm layers whose statistics are computed per micro-batch
- [ ] When mixed precision is enabled with bf16
> BatchNorm couples the examples inside a micro-batch, so statistics over 4 examples differ from those
> over 64. LayerNorm is per-token, `no_sync` only delays communication, and bf16 adds rounding noise but
> no systematic difference.
