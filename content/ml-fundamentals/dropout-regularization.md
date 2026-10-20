---
id: dropout-regularization
module: ml-fundamentals
name: Dropout / regularization
kind: concept
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - ml-fundamentals/optimizers
  - ml-fundamentals/normalization
  - ml-fundamentals/residual-connections
  - ml-fundamentals/loss-functions
  - training-adaptation/overfitting
  - training-adaptation/peft
  - training-adaptation/train-val-test-split
sources:
  - Srivastava et al., 2014 — Dropout: A Simple Way to Prevent Neural Networks from Overfitting
  - Loshchilov & Hutter, 2019 — Decoupled Weight Decay Regularization
  - Gal & Ghahramani, 2016 — Dropout as a Bayesian Approximation
---

## TL;DR
Regularization deliberately makes a model fit its training data *worse* so it generalizes *better*:
a penalty on weight size, noise injected during training, or stopping early. Dropout is the noise
version: during training each activation is zeroed with probability *p* and the survivors are scaled
by 1/(1−p), so no unit can rely on another being present; at inference nothing is dropped. Whether
you want it depends on the data regime: small fine-tuning sets, yes; single-epoch pretraining on
trillions of tokens, usually no.

## Why it exists
A network with more parameters than training examples can reach zero training loss by memorizing:
it learns detectors that fire on accidents of the training set rather than on structure that recurs
in new data. The visible symptom is the gap between training and validation loss widening as
training continues. The naive fix is to shrink the model until it cannot memorize, but that throws
away capacity you need for the hard parts of the task, and the "right" size is unknowable in
advance. Regularization lets you keep the capacity and instead change *what kind* of solution the
optimizer prefers.

Two ideas cover most of it. First, penalize complexity directly: add λ·‖w‖² (L2) or λ·‖w‖₁ (L1) to
the loss, so large weights must earn their keep by reducing the data loss by more than they cost.
Second, inject noise so the training problem is harder than the test problem: corrupt the inputs
(data augmentation), the labels (label smoothing) or the hidden units (dropout). The noise forces the
network to find features that are robust to perturbation, and robust features are exactly the ones
that transfer to unseen data.

Dropout exists to attack *co-adaptation*: a unit can learn to fix up the specific mistakes of its
neighbours, and that fragile arrangement only works on the training distribution. Randomly deleting
units breaks those alliances, because a unit cannot depend on a partner that is absent 10–50% of the
time. What matters is that the noise is multiplicative and per unit, not that it is Bernoulli: the
original paper also tried multiplicative Gaussian noise of matching variance and found it works about
as well, whereas additive jitter leaves a unit's dependence on its neighbours intact.

## How it works
### The dropout mechanism
During a training forward pass, each activation *x* gets an independent Bernoulli mask
*m* ~ Bernoulli(1−p), and the layer outputs

```
y = m ⊙ x / (1 − p)
```

The division is "inverted dropout", the form every modern framework uses. It is there *because*
E[y] = (1−p)·x/(1−p) = x: the expected activation is unchanged, so at inference you can run the full
network with no mask and no rescaling and the next layer sees inputs on the same scale it trained on.
The original paper instead kept activations unscaled during training and multiplied the weights by
(1−p) at test time; the two are equivalent, but inverted dropout keeps inference code untouched.

Backward: the gradient flows only through the kept units (the mask is a constant for that step), so
each step updates a random sub-network. A network with *n* droppable units contains 2ⁿ such
sub-networks sharing weights. The inference-time full network is a cheap approximation to averaging
all of their predictions, which is the ensemble interpretation of why dropout generalizes.

### Where it sits in a transformer
The original Transformer paper names two dropout sites, sharing one rate (0.1 in the base model): on
the sum of token and positional embeddings, and on each sub-layer's output just before it is added to
the residual stream ("residual dropout"). Its reference implementation and most descendants (BERT,
GPT-2) add a third, on the attention probabilities after the softmax ("attention dropout", which
randomly removes some tokens from each head's view). Because the residual path itself is never
dropped, information always has a clean route forward; dropout only perturbs the *corrections* each
block adds.

### Weight decay, and why AdamW is not "Adam plus L2"
L2 regularization adds (λ/2)‖w‖² to the loss, whose gradient is λw. With plain SGD the update becomes
w ← w − η(∇L + λw) = (1 − ηλ)w − η∇L: every step shrinks the weights by a constant factor, which is
why it is also called weight decay. With Adam the two are *not* the same: Adam divides each
parameter's gradient by a running estimate of its gradient magnitude, so if you put λw into the
gradient, parameters with large historical gradients get their penalty divided away and are barely
regularized. AdamW fixes this by applying the shrinkage (1 − ηλ)w *outside* the adaptive step, which
is what "decoupled" means. Standard practice is to exclude biases and LayerNorm gains from decay,
because shrinking a scale parameter toward zero changes what the layer computes rather than how
complex it is.

### Why the data regime decides
Regularization trades fit for generalization, so it only pays when the model can overfit. A
fine-tuning run that sees 5,000 examples for three epochs can memorize them; dropout and weight decay
are doing real work there. A pretraining run that streams trillions of tokens and sees each roughly
once cannot memorize in the same way, so dropout mostly costs capacity and slows convergence. That is
why several large-scale pretraining recipes report training with no dropout at all, while
parameter-efficient fine-tuning libraries still expose a `lora_dropout` knob. Other regularizers
follow the same logic: early stopping, label smoothing, stochastic depth and data augmentation all
buy the most when data is scarce relative to parameters.

## Example
The first block shows inverted dropout preserving the expectation and the train/eval switch. The
second shows the two dropout sites in a transformer block and the standard AdamW parameter split.

```python
import torch, torch.nn as nn

torch.manual_seed(0)
p = 0.3
x = torch.ones(1_000_000)
mask = (torch.rand_like(x) > p).float()            # keep each unit with probability 1-p
y_train = x * mask / (1 - p)                       # inverted dropout rescales the survivors
print(mask.mean().item(), y_train.mean().item())   # ~0.70 kept, mean ~1.00: expectation preserved

drop = nn.Dropout(p)
drop.train(); print(drop(x)[:6])                   # zeros and 1/(1-p) = 1.4286
drop.eval();  print(drop(x)[:6])                   # identity: inference is deterministic

class Block(nn.Module):
    def __init__(self, d, p=0.1):
        super().__init__()
        self.ln = nn.LayerNorm(d)
        self.attn = nn.MultiheadAttention(d, 8, dropout=p, batch_first=True)  # attention dropout
        self.drop = nn.Dropout(p)                                              # residual dropout
    def forward(self, h):
        z = self.ln(h)
        a, _ = self.attn(z, z, z, need_weights=False)
        return h + self.drop(a)                    # drop the correction, never the residual stream

model = Block(64)
decay, no_decay = [], []
for name, param in model.named_parameters():       # no decay on biases or LayerNorm gains
    (no_decay if param.ndim < 2 else decay).append(param)
opt = torch.optim.AdamW([{"params": decay, "weight_decay": 0.1},
                         {"params": no_decay, "weight_decay": 0.0}], lr=3e-4)
```

## Trade-offs
- Dropout lowers the train/validation gap but slows convergence, because each step trains a thinner
  network with noisier gradients; with abundant data the gap was small anyway and you pay only the slowdown.
- Weight decay improves generalization and keeps weights in a range where mixed precision is stable,
  but too much under-fits; the right λ falls as the dataset grows relative to the model.
- Keeping dropout active at inference (Monte Carlo dropout) gives a cheap uncertainty estimate from
  the spread of several passes, at the cost of several forward passes and non-deterministic outputs.
- Label smoothing softens over-confident targets and often helps accuracy and reduces
  overconfidence, but it caps the confidence the model can express and changes the probability
  scale, so re-measure calibration before anything downstream consumes the probabilities.
- Early stopping is the cheapest regularizer, but it ties your model to the validation split; if that
  split leaks or drifts, you stop at the wrong point.

## Pitfalls
- Forgetting `model.eval()` before validation or serving, so dropout stays on; the symptom is noisy,
  non-reproducible predictions and a validation loss worse than it should be.
- Reading training loss computed under dropout as a measure of fit; it is systematically pessimistic,
  which is why validation loss sometimes looks *lower* than training loss early on.
- Passing `weight_decay` to `torch.optim.Adam` and expecting AdamW behaviour; Adam implements it as
  L2 in the gradient, so heavily-updated weights are barely regularized and results differ.
- Applying weight decay to LayerNorm gains and biases; the symptom is a small, persistent accuracy
  loss that disappears when those parameters are moved to the no-decay group.
- Adding dropout to a model that is under-fitting; training and validation loss are both high and
  close together, and more noise only widens the capacity shortfall.
- Placing dropout immediately before a BatchNorm layer; the variance of the activations differs
  between train and eval mode, so BatchNorm's running statistics are wrong at inference.

## When to use
Start from the diagnosis, not the technique. If training loss keeps falling while validation loss
rises, you are overfitting and regularization is the lever: for fine-tuning, that means weight decay
through AdamW, dropout (including `lora_dropout` for adapters), fewer epochs and early stopping on a
clean held-out split. If both losses are high and close, you are under-fitting; remove regularization
and add capacity or data instead. For pretraining-scale data, follow the recipe of the model family
you are reproducing, which usually means dropout 0 and modest weight decay. Measure the train/val
gap per epoch and the validation metric at the eval-mode checkpoint, never the train-mode loss. In
interviews, the expected depth is: derive the 1/(1−p) scaling, explain why AdamW differs from Adam
with L2, and argue from the data regime rather than reciting default rates.

## Role lens
- **Engineer:** Sets dropout and weight-decay groups in the training config, guards every evaluation
  path with `model.eval()`, and reads overfitting from the train/val curves before touching hyperparameters.
- **Architect:** Decides the fine-tuning data budget and epoch policy that make regularization
  necessary, and insists validation splits are clean enough that early stopping is trustworthy.
- **FDE:** Explains why a customer's 300-example fine-tune memorized their examples, and reframes the
  fix around more varied data and a held-out set rather than exotic regularizers.

## Check yourself
**Q:** The pretraining team trained your base model with no dropout. The fine-tuning team wants
dropout 0.1 and weight decay 0.1 for a LoRA run on 4,000 support tickets over three epochs. Are both
teams right, and what would you check during the run?
**A:** Both can be right because the regimes differ. Pretraining streams each token roughly once, so
the model cannot memorize individual examples; dropout would only slow convergence and remove
capacity, which is why several large-scale recipes omit it. The fine-tune sees each ticket three
times, and a 7B model can memorize 4,000 examples easily, so LoRA dropout, decoupled weight decay and
early stopping are appropriate. During the run, compare eval-mode validation loss with training loss
per epoch: a widening gap means stop earlier or regularize harder; two high, close losses mean the
regularization is too strong or the adapter rank is too small. Confirm decay excludes biases and
norm gains, and that validation tickets do not duplicate training ones, or early stopping will
trigger on a leak.

## Quiz
### Q: Why does inverted dropout divide the kept activations by 1 − p during training?
- [ ] To compensate for the smaller number of gradient updates each unit receives
- [x] So the expected activation equals the un-dropped value, letting inference run the full network unchanged
- [ ] To keep the dropout noise on the same scale as the weight-decay penalty
- [ ] Because the dropped units are re-inserted with scaled weights at inference
> With probability 1 − p a unit survives, so without rescaling the next layer would see inputs
> shrunk by that factor in training but not at inference. Scaling by 1/(1 − p) makes the expectation
> match, which is what makes the deterministic full network a valid inference-time approximation.

### Q: Why does AdamW apply weight decay separately from the gradient instead of adding λw to it?
- [ ] Because the L2 penalty is not differentiable at zero and breaks Adam's second-moment estimate
- [ ] Because decoupling lets the learning rate and the decay rate share a single scheduler
- [x] Because Adam divides gradients by a per-parameter magnitude estimate, so an in-gradient penalty is scaled away for frequently updated weights
- [ ] Because decoupled decay regularizes biases and normalization gains more evenly
> Adam normalizes each parameter's update by its running gradient magnitude. If λw is part of that
> gradient, weights with large gradients get a proportionally tiny penalty. Applying (1 − ηλ)w
> outside the adaptive step shrinks every weight uniformly, which is the behaviour L2 has under SGD.

### Q: Which observation most strongly suggests that dropout is hurting a fine-tuning run rather than helping?
- [ ] Validation loss is slightly lower than training loss during the first epoch
- [ ] Training loss falls steadily while validation loss starts rising after epoch two
- [x] Training and validation loss are both high and stay close together throughout training
- [ ] Validation accuracy differs by a fraction of a point between two random seeds
> Two high, close losses are under-fitting: the model lacks effective capacity, and dropout removes
> more. A lower validation loss is a normal artifact of measuring training loss with dropout on, and a
> rising validation loss is overfitting, the case where dropout helps.
