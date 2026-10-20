---
id: overfitting
module: training-adaptation
name: Overfitting
kind: concept
difficulty: core
est_minutes: 13
status: fact-checked
related:
  - training-adaptation/train-val-test-split
  - training-adaptation/epochs
  - training-adaptation/catastrophic-forgetting
  - training-adaptation/checkpointing
  - training-adaptation/peft
  - ml-fundamentals/dropout-regularization
  - ml-fundamentals/train-test-leakage
  - evals/dataset-contamination
sources:
  - Hastie, Tibshirani & Friedman, 2009 — The Elements of Statistical Learning
  - Srivastava et al., 2014 — Dropout: A Simple Way to Prevent Neural Networks from Overfitting
  - Nakkiran et al., 2019 — Deep Double Descent: Where Bigger Models and More Data Hurt
  - Belkin et al., 2019 — Reconciling Modern Machine-Learning Practice and the Classical Bias-Variance Trade-off
---

## TL;DR
Overfitting is a model fitting the particulars of its training examples (label noise, phrasing) instead
of the pattern that transfers to new data. The signature is training loss that keeps falling while
held-out loss stalls and then rises. It happens because a network with far more parameters than examples
can memorise anything; the remedies are more or cleaner data, fewer steps, less trainable capacity,
regularisation and early stopping. Fine-tuning a small set for many epochs is the commonest way engineers
overfit an LLM.

## Why it exists
A training objective asks only one thing: lower the loss on these examples. A model with enough capacity
can do that by learning the rule, or by learning the examples. Both routes reach low training loss; only
the first reaches low loss on examples it has not seen. Memorising is often the easier route, because
every dataset carries signals that correlate with the label inside the set and nowhere else: an annotator
who filed ambiguous tickets under "billing", a template whose phrasing differs by class, an id or timestamp
that happens to sort with the answer. Gradient descent has no reason to prefer the rule over these
shortcuts, and once the rule is learned the shortcuts and the noise are the only loss left to remove.

The naive answer, "train until the loss stops falling", is precisely the recipe for overfitting, because
on repeated data the training loss keeps falling to zero. The other naive answer, "use a smaller model",
throws away the capacity that makes the model good at the rule in the first place. What you want is a
model with enough capacity to learn the pattern and a training procedure that stops, or is held back,
before it learns the noise. Measuring that requires data the model has never seen, which is why
overfitting and the train/validation/test split are inseparable ideas.

## How it works
### The generalisation gap
Call the loss on training data *L_train* and on fresh data from the same distribution *L_val*. Early in
training both fall together because the model is learning structure that is shared. At some point
*L_train* keeps falling while *L_val* flattens; the difference between them is the generalisation gap,
and it grows with every step that fits something specific to the training rows. The gap is driven by the
ratio of effective capacity to effective data: more trainable parameters, more steps, higher learning
rate and less diverse data all widen it. Noise in the labels sets a floor on achievable *L_val*; a model
that goes below the noise floor on training data has memorised the noise.

### How it looks in LLM fine-tuning
- Held-out loss turns up after one or two passes while training loss keeps dropping.
- The model reproduces training completions verbatim, including names, numbers and boilerplate, and can
  be prompted to regurgitate training records, which is a privacy incident as well as a modelling failure.
- Outputs converge on one style or length regardless of the prompt, because the fine-tuning set had one.
- Accuracy is high on prompts phrased like the training data and collapses on paraphrases.
- Token-level evaluation loss and the task metric can disagree: in practice it is common for held-out loss
  to rise while the task metric is still flat or improving, because loss penalises every token including
  stylistic ones. Pick checkpoints on the metric you ship, with loss as the early-warning signal.

### Levers, and why each one works
1. **Data.** More examples make memorisation harder than learning the rule; deduplication removes rows
   the model would otherwise see several times per epoch; cleaner labels lower the noise floor. Of the
   levers in this list it is the only one that raises the ceiling as well as closing the gap (a stronger
   base model can raise the ceiling too, but that is a model choice, not an overfitting remedy).
2. **Steps.** Fewer epochs, a lower learning rate, and early stopping limit how far the model travels from
   the pretrained prior, so it stops after the shared structure and before the particulars.
3. **Capacity.** Parameter-efficient fine-tuning (low-rank adapters, a few trainable layers) constrains the
   update to a small subspace; a smaller model does the same more bluntly.
4. **Regularisation.** Weight decay penalises large weights, dropout forces redundancy, label smoothing
   stops the model from driving probabilities to exactly one, and mixing general-purpose data into the
   fine-tuning set dilutes the shortcuts specific to your rows. Details in
   `ml-fundamentals/dropout-regularization`.

### What overfitting is not
- **Underfitting:** both losses high and close together; the model lacks capacity or steps.
- **Distribution shift:** the gap between validation and production, not between training and validation;
  more training on the same data will not help.
- **Contamination or leakage:** held-out data that duplicates training data makes overfitting invisible
  rather than curing it.
- **Catastrophic forgetting:** losing capabilities the fine-tuning set never measured; it often
  accompanies overfitting but is diagnosed on a different evaluation.

### A caveat on the classic picture
The textbook U-shaped curve (error falls, then rises with capacity) is not the whole story for modern
networks. Double descent and related results show that heavily over-parameterised models can fit the
training data exactly and still generalise well, and that test error can fall again past the
interpolation point. The practical consequence is not to rely on a rule of thumb about model size; rely
on the held-out curve for the model and data in front of you.

## Example
A worked scenario with illustrative numbers. A team fine-tunes a 7B instruct model on 2,000 support
replies for ten epochs at a modest learning rate and evaluates after each epoch:

| epoch | train loss | val loss | task metric (human-rated usefulness, %) |
|---|---|---|---|
| 1 | 1.42 | 1.38 | 71 |
| 2 | 1.10 | 1.21 | 78 |
| 3 | 0.95 | 1.18 | 80 |
| 4 | 0.82 | 1.19 | 80 |
| 5 | 0.70 | 1.25 | 78 |
| 8 | 0.41 | 1.52 | 69 |
| 10 | 0.28 | 1.71 | 63 |

Training loss falls monotonically all the way; validation loss bottoms at epoch 3 and the task metric
plateaus across epochs 3 and 4 before falling. By epoch 10 the model answers training-set questions
word for word and gives a boilerplate reply to anything else. The right checkpoint is epoch 3 or 4, and
the run should have stopped around epoch 6. The monitor below encodes that rule: stop after three
evaluations without improvement and reload the best checkpoint.

```python
class EarlyStopping:
    """Stop when the validation metric has not improved by min_delta for `patience` evaluations."""
    def __init__(self, patience=3, min_delta=0.005, mode="min"):
        self.patience, self.min_delta = patience, min_delta
        self.sign = 1 if mode == "min" else -1
        self.best, self.best_step, self.bad_evals = None, None, 0

    def update(self, value, step) -> bool:
        improved = self.best is None or self.sign * (self.best - value) > self.min_delta
        if improved:
            self.best, self.best_step, self.bad_evals = value, step, 0
        else:
            self.bad_evals += 1
        return self.bad_evals >= self.patience      # True -> stop and reload best_step's checkpoint

stop = EarlyStopping(patience=3)
for epoch, val_loss in enumerate([1.38, 1.21, 1.18, 1.19, 1.25, 1.33, 1.44, 1.52, 1.61, 1.71], start=1):
    if stop.update(val_loss, step=epoch):
        print(f"stop at epoch {epoch}; best was epoch {stop.best_step} (val {stop.best})")
        break
# -> stop at epoch 6; best was epoch 3 (val 1.18)
```

## Trade-offs
- Regularisation and fewer steps reduce variance but add bias; pushed too far they produce underfitting,
  so the balance flips when both training and validation loss sit high together.
- Early stopping is nearly free but only as good as the validation set; with a leaky or tiny validation
  set it stops at the wrong place, and the real cost moves to building that set properly.
- Parameter-efficient fine-tuning limits how much the model can memorise, but it equally limits how much
  of a genuinely new task it can learn; for tasks far from pretraining, full fine-tuning with more data wins.
- Among the anti-overfitting levers, more data is the only one that improves the achievable ceiling,
  but it costs labelling time, and synthetic data that shares one template can add rows while adding little diversity.

## Pitfalls
- Judging a run by training loss; the symptom is a proud "loss went to 0.1" followed by a model that
  cannot handle a rephrased question.
- A validation set that near-duplicates training rows; the symptom is validation loss tracking training
  loss downward forever, with overfitting surfacing only in production.
- Stopping on token loss when the task metric is what matters, or the reverse; the symptom is a
  checkpoint chosen one or two epochs away from the best one on the metric customers see.
- Mistaking distribution shift for overfitting and adding regularisation; the symptom is validation
  numbers that stay fine while production keeps degrading.
- Tuning dozens of hyperparameters on the same validation set; the symptom is a validation score that
  improves with every experiment and a test score that does not, because you have overfit the validation set.
- Fine-tuning on records that contain personal data for many epochs; the symptom is the model
  regurgitating names, addresses or account numbers verbatim when prompted with a prefix.

## When to use
Overfitting is not something you choose; it is what every sufficiently long run does, so the decision is
how to detect and bound it. Always train with a held-out set that respects the unit of generalisation,
evaluate at least once per epoch, save a checkpoint at each evaluation and keep the best one. Start with
few epochs and a low learning rate and lengthen only if validation is still improving. Reach for more or
cleaner data before reaching for regularisation knobs, and for a smaller trainable footprint when data is
scarce. Day to day, the gap between training and validation curves is the first chart to inspect in any
run review. In interviews, be ready to explain the gap, the levers, and why the classic U-curve is only
a guide for modern networks.

## Role lens
- **Engineer:** Wires held-out evaluation, early stopping and best-checkpoint loading into the training
  job and reads the train/validation gap before believing any loss number.
- **Architect:** Sets the policy that every fine-tuned model ships with a validation curve, a held-out
  task metric and a data-deduplication step, so overfit models are caught before deployment.
- **FDE:** Explains to a customer why a model trained longer on their 500 examples got worse, and why the
  cure is more diverse examples and an honest evaluation set rather than more epochs.

## Check yourself
**Q:** A fine-tuned classifier shows 99% training accuracy, 97% validation accuracy, and 81% accuracy
in production two weeks after launch. Is this overfitting, and what would you check before changing the
training recipe?
**A:** The small train-validation gap argues against classic overfitting; the big validation-production
gap points elsewhere. Check first whether the validation set shares sources with training (same users,
documents or templates), since leakage would hide overfitting and make 97% meaningless; a grouped
re-split answers that. Then compare the production input distribution with the training data: new
customers, new product names or a changed upstream pipeline mean distribution shift, which more
regularisation will not fix and which calls for fresh data and a temporal validation set. Only if a
leak-free validation set also drops would I treat it as overfitting and reduce epochs, add data or
constrain the trainable parameters.

## Quiz
### Q: Which pattern in the training curves most clearly indicates overfitting?
- [ ] Training and validation loss both high and close together
- [x] Training loss falling steadily while validation loss has turned upward
- [ ] Validation loss falling faster than training loss
- [ ] Both losses falling together throughout the run
> A widening gap with validation loss rising means the model is fitting particulars of the training rows
> that do not transfer. Both losses high and close is underfitting, and both falling together is healthy.

### Q: A 2,000-example fine-tune overfits after two epochs. Which single change most directly raises the achievable quality rather than just closing the gap?
- [ ] Training for more epochs with a lower learning rate
- [ ] Raising the LoRA rank so the adapter has more trainable capacity
- [x] Adding more deduplicated, diverse training examples
- [ ] Increasing weight decay
> More diverse data makes the rule cheaper to learn than the rows and lowers the noise floor, improving
> the ceiling. Weight decay and a lower rate only trade variance for bias, and a higher LoRA rank adds
> trainable capacity without adding information, which makes memorising the 2,000 rows easier, not harder.

### Q: The validation set contains near-duplicates of training examples. What happens to overfitting detection?
- [ ] Overfitting is detected earlier because duplicates are easier to fit
- [ ] Nothing changes as long as the test set is clean
- [x] Validation loss tracks training loss downward, so overfitting becomes invisible until production
- [ ] The model cannot overfit because duplicates act as regularisation
> Memorised rows score well on their near-copies, so the gap that reveals overfitting never appears. A
> clean test set would show the truth, but only once, at the end, after every decision was already made.
