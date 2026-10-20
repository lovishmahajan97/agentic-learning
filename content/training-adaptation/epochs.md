---
id: epochs
module: training-adaptation
name: Epochs
kind: concept
difficulty: intro
est_minutes: 11
status: reviewed
related:
  - training-adaptation/learning-rate
  - training-adaptation/overfitting
  - training-adaptation/checkpointing
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/pre-training
  - ml-fundamentals/batch-size
  - ml-fundamentals/gradient-accumulation
  - ml-fundamentals/learning-rate-schedulers
sources:
  - Muennighoff et al., 2023 — Scaling Data-Constrained Language Models
  - Goodfellow, Bengio & Courville, 2016 — Deep Learning
  - Official docs: Hugging Face Trainer (num_train_epochs, max_steps)
---

## TL;DR
An epoch is one full pass of the optimiser over the training set. It exists because minibatch training
consumes data piecewise, and "every example seen once" is the natural unit for reshuffling, evaluating
and stopping. More epochs means more optimizer steps over the same examples: necessary when the set is
small, harmful once the model starts memorising. For LLMs the unit flips with scale: pretraining counts
tokens and rarely completes one pass, while fine-tuning counts epochs and usually needs only a few.

## Why it exists
Gradient descent updates on minibatches of *B* examples, so after `ceil(N / B)` steps every one of the
*N* training examples has contributed to the parameters once. With a small learning rate each example
nudges the weights only slightly, and for a dataset of a few thousand rows one pass leaves the model far
from the best fit those rows can support. Repeating passes is how you extract more gradient signal from
data you cannot get more of. But every repetition shows the model the same specific examples again, and
after the shared pattern has been learned the only thing left to fit is what is particular to those rows:
label noise, phrasing, the order of fields. The epoch count is therefore a knob on how much to trust these
exact examples versus the prior the model already has.

The naive stopping rules both fail. "Train until training loss stops falling" never stops, because loss on
repeated data keeps falling all the way to memorisation. "Always one epoch" leaves small sets underfit;
"always ten" overfits most of them. The honest control is the validation curve: train while held-out loss
or the task metric improves, stop when it turns. Pretraining is the opposite regime: the corpus is so large
relative to compute that the run ends before one pass completes, and unique tokens are worth more than
repeated ones, although a data-constrained scaling study found that repeating data for up to roughly four
passes costs little compared with fresh data.

## How it works
### Accounting
With *N* examples, micro-batch size *B* and gradient accumulation over *A* micro-batches:

```
micro_batches_per_epoch = ceil(N / B)
optimizer_steps_per_epoch = micro_batches_per_epoch / A
total_optimizer_steps = epochs × optimizer_steps_per_epoch
```

Learning-rate schedulers are defined over optimizer steps, so changing the epoch count silently changes
the decay horizon: a cosine schedule over 375 steps reaches a given fraction of peak LR at a different
point than one over 1,250 steps. The Hugging Face `Trainer` derives `max_steps` from
`num_train_epochs` (default 3) unless `max_steps` is set explicitly, in which case it wins. Two runs with
different epoch counts are therefore not "the same run for longer"; they are different schedules.

### Reshuffling every epoch
The order of examples within an epoch determines which examples share a batch. A fixed order makes the
gradient noise identical in every pass and means the same examples are always freshest at the end of the
epoch when you evaluate. Reseeding the sampler per epoch (`seed + epoch`) gives different batch
compositions each pass while keeping the run reproducible and resumable: a checkpoint that records the
epoch and the number of batches consumed can regenerate the exact remaining order.

### Where evaluation and checkpoints go
The epoch boundary is the conventional moment to evaluate and save, because every example has been seen
an equal number of times. That is fine when an epoch is 125 steps. When an epoch is hours long, evaluate
every *n* steps instead: the model can overfit or diverge in the middle of an epoch and an end-of-epoch
check would miss the best checkpoint. Conversely, with 200 fine-tuning examples an epoch is a handful of
steps, and per-epoch evaluation is the only granularity that is not noise.

### Why the number matters so much in fine-tuning
A pretrained model is a strong prior; each pass over a small instruction set moves it further from that
prior and toward the specifics of those rows. Illustrative arithmetic: 1,000 examples at batch 8 give 125
steps per epoch; three epochs are 375 steps and a 3% warmup is about eleven steps (frameworks round 11.25 differently). Common practice for
supervised fine-tuning is a small number of epochs, with the count rising only for tiny sets where each
epoch is a few steps, but the actual control is total steps × learning rate judged against the
validation curve, not a magic integer.

### Reading the curve across epochs
In LLM fine-tuning a characteristic shape appears once the model starts memorising: training loss is
roughly flat within an epoch and then drops abruptly at the start of the next one, giving a staircase.
The drop happens *because* the model is now seeing examples it has already seen, and it predicts them
better than it predicts the unseen remainder of the first pass. A staircase on training loss with flat or
rising validation loss is direct evidence that further epochs are buying memorisation, not skill. The
control that turns this into a decision is early stopping with patience: evaluate at a fixed cadence,
keep the best checkpoint, and stop after *p* evaluations without improvement, so the epoch count you
configure is an upper bound rather than a commitment.

### What "an example" means with packing
Fine-tuning frameworks often pack several short examples into one fixed-length sequence to avoid
padding waste. An epoch is still one pass over the examples, but steps per epoch now follow from total
tokens divided by tokens per batch, not from `N / B`. A dataset of many short examples and one of a few
long ones can have the same row count and very different step counts, so compute steps from tokens when
packing is on.

### Pretraining counts tokens
A corpus of trillions of tokens with a compute budget that covers a fraction of it has no meaningful
epoch; the schedule, the warmup and the evaluation cadence are all defined in tokens or steps. Partial
reuse of data is a choice made under data constraints, not a default.

### Which unit to compare on
Compare runs on the same dataset by epochs; compare runs across dataset sizes by optimizer steps or tokens
seen. "Two epochs on 10,000 examples" and "two epochs on 1,000" differ by 10× in updates.

## Example
A minimal training loop that makes the accounting explicit: epochs, micro-batches, accumulation, a
per-epoch reseeded sampler, and the linear schedule defined over optimizer steps. Run it and the printed
step counts show why the schedule length depends on the epoch count.

```python
import math, torch
from torch.utils.data import DataLoader, TensorDataset

N, B, EPOCHS, ACCUM = 1000, 8, 3, 4
x = torch.randn(N, 16); y = (x.sum(1) > 0).float()
xv = torch.randn(200, 16); yv = (xv.sum(1) > 0).float()            # held-out
model = torch.nn.Sequential(torch.nn.Linear(16, 32), torch.nn.ReLU(), torch.nn.Linear(32, 1))
opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
loss_fn = torch.nn.functional.binary_cross_entropy_with_logits

micro_per_epoch = math.ceil(N / B)                                 # 125
total_steps = math.ceil(EPOCHS * micro_per_epoch / ACCUM)          # 94 optimizer steps, incl. final flush
sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: max(0.0, 1 - s / total_steps))

micro, step = 0, 0
for epoch in range(EPOCHS):
    g = torch.Generator().manual_seed(1234 + epoch)                # new order every epoch, reproducible
    loader = DataLoader(TensorDataset(x, y), batch_size=B, shuffle=True, generator=g)
    model.train()
    for xb, yb in loader:
        (loss_fn(model(xb).squeeze(1), yb) / ACCUM).backward()
        micro += 1
        if micro % ACCUM == 0:                                     # one update per ACCUM micro-batches
            opt.step(); sched.step(); opt.zero_grad(); step += 1
    model.eval()
    with torch.no_grad():
        val = loss_fn(model(xv).squeeze(1), yv).item()
    print(f"epoch {epoch+1}: micro-batches={micro} optimizer steps={step} "
          f"lr={sched.get_last_lr()[0]:.2e} val_loss={val:.3f}")

# 375 micro-batches is not divisible by 4: three gradients are still pending. Flush them.
if micro % ACCUM:
    opt.step(); opt.zero_grad(); step += 1
```

The leftover micro-batches at the end are the kind of accounting detail epochs hide: without the final
flush, three batches of data contribute nothing; if the schedule is sized to 93 steps instead of 94, the
flush runs at a learning rate of zero and still contributes nothing; and with accumulation that does not
divide an epoch, gradients from the last batches of one epoch land in the first update of the next.

## Trade-offs
- More epochs lower training loss and improve the fit on small sets, but each extra pass fits more of the
  rows' particulars; the balance flips at the point where the validation curve turns upward.
- Repeating data is free while new data costs money; unique tokens are worth more per token, so repetition
  is a fallback for data-constrained regimes, not a substitute for collection.
- Per-epoch evaluation is cheap and conventional but coarse for long epochs; step-based evaluation finds
  the best checkpoint at the cost of extra evaluation compute.
- Dropping the last partial batch keeps tensor shapes uniform but leaves a few examples unseen each pass;
  irrelevant at scale, measurable with a few hundred examples.

## Pitfalls
- Copying a fixed epoch count from a tutorial without looking at the validation curve; the symptom is
  held-out loss rising through the second half of the run while training loss keeps falling.
- Changing epochs and not noticing the learning-rate schedule changed with it; the symptom is runs that
  are "longer" but incomparable, with different LR at the same step.
- Gradient accumulation that does not divide the epoch; the symptom is an optimizer step count that does
  not match the expected arithmetic and gradients leaking across epoch boundaries.
- Comparing runs by epoch when dataset sizes differ; the symptom is a "two-epoch" run on ten times the
  data being credited to the data rather than to ten times the updates.
- Resuming from a checkpoint without the sampler position; the symptom is some examples seen twice and
  others never in that epoch, and a resumed run that no longer matches an uninterrupted one.
- Reading the staircase drop in training loss at each epoch boundary as the model "learning more"; the
  symptom that shows it is memorisation is validation loss flat or rising across the same boundaries.
- Evaluating only at epoch boundaries on a large dataset; the symptom is the best checkpoint sitting
  between two evaluations and never being saved.

## When to use
Think in epochs when the dataset is fixed and small enough that the model will see it several times:
supervised fine-tuning, reward-model training, classifier heads. Start low, evaluate at least once per
epoch, and let the validation curve, not a convention, decide when to stop; save a checkpoint at each
evaluation so the best one can be recovered. Think in steps or tokens when data streams or is far larger
than the budget: pretraining and continued pretraining. When reading someone else's training config,
convert epochs into optimizer steps before judging it, and check whether the schedule length was tied to
that number. In interviews, being able to derive steps from examples, batch size and accumulation, and to
explain why pretraining uses tokens while fine-tuning uses epochs, is the expected baseline.

## Role lens
- **Engineer:** Sets `num_train_epochs` or `max_steps`, wires per-epoch reseeding and evaluation, and
  checks that accumulation divides the epoch so step counts match the plan.
- **Architect:** Defines the training budget in steps or tokens and the evaluation cadence, so epoch
  counts across teams and datasets translate into comparable compute and comparable schedules.
- **FDE:** Explains to a customer with 800 labelled examples why the fine-tune is run for a few epochs with
  validation checks rather than "until the loss is zero", and what the curve they are shown means.

## Check yourself
**Q:** A colleague reports that the fine-tune run with five epochs reached a lower loss than the run with
two, so five epochs is better. What would you ask to see before agreeing?
**A:** First, which loss: training loss on repeated data always falls with more epochs, so only the
held-out loss or the task metric can support the claim. Second, the validation curve across the whole run,
to see whether held-out performance peaked earlier and the five-epoch endpoint is already past the turn.
Third, the schedule: with step-based decay the five-epoch run spent its learning rate differently, so the
comparison mixes epochs with schedule shape; comparing the best checkpoint of each run is fairer than
comparing endpoints. Finally, a check on the task metric and on general capability, because a lower token
loss after many passes often comes with memorised phrasing and brittleness on paraphrased inputs.

## Quiz
### Q: 4,000 examples, micro-batch 16, gradient accumulation 2, three epochs. How many optimizer steps?
- [ ] 750
- [x] 375
- [ ] 250
- [ ] 1,500
> Each epoch has 4000 / 16 = 250 micro-batches, and accumulation over two makes 125 optimizer steps per
> epoch; three epochs give 375. The tempting 750 forgets accumulation, which halves the update count.

### Q: Why is LLM pretraining usually described in tokens rather than epochs?
- [ ] Tokenisation makes examples variable-length so epochs cannot be counted
- [x] The corpus is larger than the compute budget can cover, so the run ends before one full pass and unique data is preferred over repeats
- [ ] Optimizers require a token budget to compute the learning rate
- [ ] Epochs only apply to classification tasks
> A pass over trillions of tokens rarely completes, so the schedule and evaluation cadence are set in
> tokens or steps. Variable-length examples are easily counted; the issue is scale, not tokenisation.

### Q: What goes wrong if the training set is presented in the same order every epoch?
- [ ] The learning rate schedule resets at every epoch boundary
- [ ] Gradient accumulation stops working across epochs
- [x] Batch compositions and gradient noise repeat exactly, and the same examples are always freshest at evaluation time
- [ ] The validation loss can no longer be computed
> Reseeding the sampler each epoch gives different batch groupings and decorrelated noise between passes
> while staying reproducible; a fixed order biases what the model has just seen when it is evaluated.
