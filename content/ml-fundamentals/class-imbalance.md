---
id: class-imbalance
module: ml-fundamentals
name: Class imbalance
kind: concept
difficulty: core
est_minutes: 15
related:
  - ml-fundamentals/confusion-matrix
  - ml-fundamentals/precision-recall-f1
  - ml-fundamentals/roc-auc
  - ml-fundamentals/calibration
  - ml-fundamentals/loss-functions
  - ml-fundamentals/train-test-leakage
  - training-adaptation/synthetic-data-generation
  - customer-data-evaluation/production-feedback-classification
sources:
  - He & Garcia, 2009 — Learning from Imbalanced Data
  - Chawla et al., 2002 — SMOTE: Synthetic Minority Over-sampling Technique
  - Lin et al., 2017 — Focal Loss for Dense Object Detection
  - Elkan, 2001 — The Foundations of Cost-Sensitive Learning
---

## TL;DR
When one class is rare, the default recipe of accuracy as the metric, an unweighted loss and a 0.5
threshold quietly optimises for "say the common class". Imbalance is less a data problem than a cost
problem that the defaults get wrong. Fix the evaluation first (confusion matrix, precision–recall,
stratified splits), then the decision threshold, and only then reach for class weights, resampling or
focal loss, remembering that each of those distorts the probabilities you may need later.

## Why it exists
Rare positives are the normal case in valuable problems: fraud, abuse, prompt injection, hallucinated
answers, churn, defects, thumbs-down feedback. Three things go wrong at once when the positive class is
1% of the data.

The **metric** lies: accuracy for the constant "never positive" classifier is 99%, so the number cannot
distinguish a useless model from a good one. The **optimisation** is lopsided: the loss is a sum over
examples, 99% of which are negatives, so the gradient mostly says "get the negatives right" and the
boundary drifts to wherever majority errors are cheapest; with few minority examples the model also
cannot learn the minority region well, whatever the weighting. The **decision rule** is wrong: argmax,
or a 0.5 threshold, is the Bayes-optimal rule only when both errors cost the same, and in every
problem listed above they do not. Elkan's result makes this concrete: with calibrated probabilities,
the cost-minimising rule is to predict positive when p ≥ C_FP / (C_FP + C_FN), which for a miss that
costs fifty times a false alarm is a threshold near 0.02, nowhere near 0.5.

The naive responses fail in their own ways. "Collect more data" keeps the same ratio, and the minority
is usually the expensive class to label. "Oversample until balanced and carry on" works only if you
keep the duplicates out of the validation folds and repair the probabilities afterwards; done
carelessly it produces leakage and a model whose "0.5" means 0.05. The enemy is not the imbalance; it
is the mismatch between the cost structure and the defaults.

## How it works
### Step 0: measure with the right instruments
Report the prevalence. Split with stratification so every fold keeps the ratio; otherwise a small test
fold can contain zero positives and recall is undefined. Read the confusion matrix with raw counts.
Prefer the precision–recall curve and average precision to ROC-AUC when positives are rare, because
ROC's false-positive axis is normalised by the huge negative count and hides the alert volume.
Specify the operating point as a constraint ("recall ≥ 0.8 at the best precision", or "at most N
false alarms per day") and pick the threshold on a validation split, never on the test split.

### Lever 1: move the threshold
For a model that already ranks reasonably, most of the "imbalance problem" is the 0.5 default. Choose
τ on validation to meet the cost-optimal rule above or the product constraint. No retraining, no
distortion of probabilities. Try this first *because* reweighting and resampling are, for a
well-specified model, roughly equivalent to shifting the threshold, and the threshold is the cheaper
knob.

### Lever 2: reweight the loss
Multiply each example's loss by a class weight; the common "balanced" heuristic is w_c = N / (k · n_c),
so with 2% positives the minority weight is about 25 and the majority about 0.51 (scikit-learn's
`class_weight="balanced"`). This is oversampling in expectation without duplicating rows. It moves the
boundary toward recall, but it changes the whole fitted solution, not only the intercept, so verify
the ranking with average precision rather than assuming it improved; and the probabilities now reflect
an artificial 50/50 prior and must be recalibrated or corrected before anything consumes them. For
deep networks the same idea is weighted cross-entropy, or **focal loss**:

```
FL(p_t) = -alpha_t * (1 - p_t)^gamma * log(p_t)
```

where p_t is the probability assigned to the true class. The (1 − p_t)^γ factor shrinks the loss of
examples the model already gets right, so the thousands of easy negatives stop dominating the
gradient; γ = 2 is the value the original detection paper settled on. It was invented *because* in
dense object detection the problem was not only the class ratio but that almost all negatives were
trivially easy.

### Lever 3: resample
- **Random undersampling** of the majority: fast, loses information; right when negatives are
  abundant and redundant. Training several models on different undersamples and averaging them
  recovers much of the lost signal.
- **Random oversampling** of the minority: duplicates rows, so trees and high-capacity models overfit
  to them. It must happen *after* the split, or inside each cross-validation fold; otherwise copies of
  the same row land in train and validation and scores inflate.
- **SMOTE**: for each minority point, pick one of its k nearest minority neighbours and create a
  synthetic point on the segment between them. This widens the minority region instead of just making
  it denser. It assumes a continuous feature space, so it produces nonsense on one-hot or embedding
  features, and it can plant points inside majority territory when the classes overlap; borderline and
  adaptive variants exist for that. For text, the modern analogue is LLM-generated minority examples,
  with the same two risks (distribution mismatch, label noise) and the same rule: validate only on real
  minority data.

### Lever 4: change the problem
When positives are below roughly one in a thousand, or unlabelled, reframe: anomaly or one-class
detection, active learning to find positives worth labelling, or a two-stage cascade where a cheap
high-recall filter hands a small enriched set to an expensive precise model, which is how most
LLM-based moderation and injection pipelines are actually built. Rare classes in a multi-class problem
can be merged into a parent class or handled hierarchically.

### Translating between enriched evaluation and production
Evaluation sets are often enriched with positives to make recall estimates stable. Recall and FPR
transfer; precision does not. Convert with the production prevalence π:

```
precision_at_prod = TPR * pi / (TPR * pi + FPR * (1 - pi))
```

A detector with TPR 0.95 and FPR 0.001 has precision of about 0.49 when injections are 0.1% of traffic,
and about 0.09 if its FPR is 0.01.

### Imbalance in LLM systems
Few-shot classification inherits the label distribution of the demonstrations: if nine of ten examples
in the prompt are "benign", the model leans benign regardless of input (majority-label bias), so
balance the demonstrations or calibrate the outputs. A judge evaluated on a set where 3% of outputs
are failures can "agree with humans 97%" by passing everything; score it on recall of the failure
class. Production feedback is imbalanced by construction (thumbs-down is rare), so a feedback
classifier's headline should be recall on bad outputs at a fixed review budget.

## Example
A 2%-positive problem, split three ways with stratification. Two logistic regressions, one unweighted
and one with balanced class weights, are compared on ranking (average precision), on behaviour at the
default threshold, and after choosing the threshold on validation for "recall ≥ 0.80"; the last column
shows what the weights did to the probabilities.

```python
import numpy as np
from sklearn.datasets import make_classification
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import average_precision_score, precision_recall_curve, precision_score, recall_score

X, y = make_classification(n_samples=60_000, n_features=20, n_informative=6, n_redundant=2,
                           class_sep=1.5, flip_y=0.0, weights=[0.98, 0.02], random_state=0)
X_tr, X_rest, y_tr, y_rest = train_test_split(X, y, test_size=0.4, stratify=y, random_state=0)
X_val, X_te, y_val, y_te = train_test_split(X_rest, y_rest, test_size=0.5, stratify=y_rest, random_state=0)
print(f"prevalence train={y_tr.mean():.3f} val={y_val.mean():.3f} test={y_te.mean():.3f}")  # ~0.02 everywhere

for cw in (None, "balanced"):
    clf = LogisticRegression(max_iter=2000, class_weight=cw).fit(X_tr, y_tr)
    p_val, p_te = clf.predict_proba(X_val)[:, 1], clf.predict_proba(X_te)[:, 1]
    # Lever 1: choose the threshold on validation for the product spec "recall >= 0.80"
    prec, rec, thr = precision_recall_curve(y_val, p_val)
    ok = rec[:-1] >= 0.80
    tau = thr[np.argmax(np.where(ok, prec[:-1], -1))]
    print(f"class_weight={cw}: AP={average_precision_score(y_te, p_te):.3f} | "
          f"tau=0.5 recall={recall_score(y_te, p_te >= 0.5):.2f} "
          f"precision={precision_score(y_te, p_te >= 0.5, zero_division=0):.2f} | "
          f"tau={tau:.3f} recall={recall_score(y_te, p_te >= tau):.2f} "
          f"precision={precision_score(y_te, p_te >= tau):.2f} | "
          f"mean p={p_te.mean():.3f} vs base rate {y_te.mean():.3f}")
# Expect: at tau=0.5 the unweighted model has lower recall and the balanced one far lower precision;
# once tau is tuned both meet the recall floor; the balanced model's mean probability sits far above
# the 2% base rate; and its average precision is not better (in this run it is slightly worse).
```

The last line is the one people miss: the weights did not teach the model anything new about the
minority, they moved its operating point and inflated its probabilities, and the same operating point
was available for free by moving τ.

## Trade-offs
- Threshold moving costs nothing and keeps probabilities honest, but it cannot help a model that never
  learned the minority structure; if the precision–recall curve hugs the prevalence line, you need data
  or a different model, not a different τ.
- Class weights and oversampling push the boundary toward recall and are cheap to try, but they change
  the fitted solution and inflate probabilities; the flip is any consumer that needs p, where you must
  recalibrate or prior-correct afterwards.
- Undersampling trains faster and often loses little when negatives are redundant, but it throws away
  data; with millions of negatives undersample, with thousands weight instead.
- SMOTE widens the minority region in continuous feature spaces and can lift recall, but it produces
  implausible points on categorical, one-hot or embedding features and across overlapping classes;
  prefer real or human-validated synthetic examples there.
- Focal loss rescues deep detectors drowning in easy negatives, but adds two hyperparameters and
  changes the probability scale, so calibration must be re-checked after switching.
- Enriched evaluation sets stabilise recall estimates but make precision unrepresentative; convert
  through TPR, FPR and the production prior before promising a number.

## Pitfalls
- Resampling or SMOTE before the split; the symptom is near-perfect cross-validation followed by a
  collapse in production, because neighbours of the same minority point sat in both folds.
- Non-stratified splits with rare positives; the symptom is folds with zero positives, undefined recall
  and wildly varying scores between random seeds.
- Reporting accuracy or ROC-AUC alone; the symptom is 0.99 accuracy with no positives caught, or 0.95
  AUC with a review queue that is 90% false alarms.
- Shipping raw probabilities from a reweighted model; the symptom is that everything looks "likely
  positive" and every downstream threshold breaks.
- Measuring precision on the enriched evaluation set; the symptom is 0.8 offline and 0.1 in production
  with no change in the model.
- Few-shot prompts whose demonstrations are all one class; the symptom is predictions skewing to that
  class whatever the input says.
- Adding LLM-generated minority examples and validating on them; the symptom is excellent numbers on
  synthetic positives and no improvement on real ones.

## When to use
Treat any problem with prevalence under roughly 10% as imbalanced and start with the measurement
discipline: prevalence, stratified splits, confusion matrix, precision–recall, a threshold chosen on
validation against a stated constraint. Add class weights or resampling only when the ranking itself
is poor and you have verified with average precision that they help; use focal loss for deep detectors
swamped by easy negatives; use undersampling when negatives are enormous and redundant; use anomaly
detection or a cascade when positives are below one in a thousand. Whatever you do to the training
distribution, recalibrate before anyone consumes the probabilities. In interviews, the expected depth
is: why accuracy fails, the cost-optimal threshold formula, how resampling causes leakage, and how
enriched-set precision converts to production precision.

## Role lens
- **Engineer:** Builds the stratified split, the threshold sweep and the prior correction into the
  training pipeline, and makes `class_weight` or resampling a tracked experiment rather than a default.
- **Architect:** Owns the cost model (what a miss and a false alarm each cost) that fixes the operating
  point, and decides whether a cascade or a human-review stage is the cheaper way to buy precision.
- **FDE:** Enriches the customer's evaluation set with real positives from their incident history,
  converts offline precision to their actual traffic mix, and sets the daily alert budget with the team
  that will work the queue.

## Check yourself
**Q:** You must build a prompt-injection detector. Injections are 0.1% of production messages; you
have 2,000 labelled injections and about a million benign messages. How do you build the training set
and the evaluation set, and how do you choose the threshold?
**A:** Train on all 2,000 positives against an undersampled but diverse negative set (tens of
thousands, deliberately keeping hard negatives such as benign messages that mention "ignore the
instructions"), or on everything with class weights; either way the probabilities will be shifted, so
plan to recalibrate on a sample drawn at production prevalence. Hold out a stratified evaluation set
with enough positives (several hundred) to estimate recall tightly and a large benign sample to
estimate FPR, then convert to production precision with the 0.1% prior rather than quoting precision
from the enriched set. Set the threshold from the cost model: a false block costs friction, a missed
injection risks exfiltration, so fix the FPR budget security will accept and read recall there on
validation. If the score feeds a second-stage LLM check, calibrate it. Monitor daily flag volume and
review a sample so drift in either class is noticed.

## Quiz
### Q: Missing a fraud costs about fifty times as much as a false alarm. With calibrated probabilities, which decision threshold minimises expected cost?
- [ ] 0.5, because that is where the model is most uncertain
- [x] About 0.02
- [ ] About 0.98
- [ ] It depends only on the prevalence of fraud
> The cost-optimal rule predicts positive when p ≥ C_FP / (C_FP + C_FN) = 1 / 51 ≈ 0.02. Prevalence
> is already inside a calibrated probability, so it does not enter the threshold separately; 0.5 is
> only optimal when both errors cost the same.

### Q: You apply SMOTE to the whole dataset and then run 5-fold cross-validation. Scores are excellent; production is poor. What is the most likely cause?
- [ ] SMOTE created too few synthetic points to matter
- [x] Synthetic points interpolated from a minority example land in other folds, leaking near-duplicates between train and validation
- [ ] Class weights should have been used as well
- [ ] ROC-AUC is unsuitable for imbalanced data
> Each synthetic point lies on a segment between two real minority points, so when those are split
> across folds the validation fold contains near-copies of training data and the score is inflated.
> Resampling belongs inside each fold, applied to the training portion only.

### Q: After training with class_weight="balanced", the mean predicted probability on the test set is 0.35 while the prevalence is 0.02. What is going on and what should you do?
- [ ] The model is broken; retrain without weights
- [x] This is expected: the weights shifted the learned prior, so recalibrate or apply the prior correction before anyone consumes the probabilities
- [ ] The test set is mislabelled
- [ ] Set the threshold to 0.35 and ship
> Balanced weights make the model behave as if positives were half the data, so its probabilities are
> systematically inflated by the odds ratio between the artificial and the real prior. The ranking may
> be fine; the probabilities are not, until you correct them or recalibrate on unweighted data.
