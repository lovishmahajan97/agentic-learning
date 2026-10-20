---
id: classification
module: ml-fundamentals
name: Classification
kind: concept
difficulty: intro
est_minutes: 13
related:
  - ml-fundamentals/regression
  - ml-fundamentals/loss-functions
  - ml-fundamentals/confusion-matrix
  - ml-fundamentals/precision-recall-f1
  - ml-fundamentals/roc-auc
  - ml-fundamentals/calibration
  - ml-fundamentals/class-imbalance
  - model-internals/softmax
sources:
  - Bishop, 2006 — Pattern Recognition and Machine Learning
  - Hastie, Tibshirani & Friedman, 2009 — The Elements of Statistical Learning
  - Official docs: scikit-learn
---

## TL;DR
Classification predicts which of a fixed set of categories an input belongs to. A classifier produces a
score per class (logits), converts scores to probabilities (sigmoid or softmax), and a separate decision
rule, a threshold or argmax, turns probabilities into a label. Keeping scoring and deciding apart matters
because the threshold encodes the business cost of each kind of mistake. Every token an LLM emits is a
classification over its vocabulary, so this is the most common problem shape in AI engineering.

## Why it exists
Many decisions are discrete: spam or not, which team handles this ticket, which intent, is this answer
grounded in the source, which token comes next. Two naive approaches fail in instructive ways.

Hand-written rules are brittle, do not scale to thousands of edge cases, and produce no notion of
confidence; a rule either fires or it does not, so there is no way to send borderline cases to a human.

Treating the problem as regression on a class index 0..K−1 fails for a subtler reason. It imposes an
ordering and a distance that do not exist: class 3 is not "between" classes 2 and 4, yet squared error
would punish predicting 0 instead of 4 sixteen times harder than predicting 3. The model wastes capacity
learning an arbitrary numbering.

What is needed is a model that outputs one score per class, a loss that rewards putting probability on
the correct class and nothing else, and a decision rule that accounts for asymmetric costs. The
probability matters on its own: a 0.51 spam score and a 0.99 spam score should lead to different actions
(flag for review versus delete), and a downstream system can only make that distinction if the
classifier exposes it. Classification formalises the split: estimate P(class | input), then decide.

## How it works
### From features to scores
A linear classifier computes z = Wx + b: one score per class, from a weight vector per class. Logistic
regression is the binary case with one logit and p = σ(z) = 1/(1 + e^(−z)). For K classes the softmax
p_k = exp(z_k) / Σ_j exp(z_j) turns K logits into a distribution that sums to one. A neural network
replaces the raw features x with learned features and keeps the same final linear layer; in a
transformer the "LM head" is exactly this, a linear map from the hidden state to vocabulary-sized logits
followed by softmax. Tree ensembles partition the feature space instead of drawing linear boundaries,
and k-nearest-neighbours votes among similar training points, but all of them end with a score per class.

### The loss
Training minimises cross-entropy, −log p_y, the negative log probability assigned to the true class. It
is used because it is the maximum-likelihood objective for a categorical outcome and because its
gradient with respect to the logits is simply p − onehot(y): large when the model is confidently wrong,
near zero when it is confidently right. Squared error on probabilities loses that property, which is why
it trains badly for classes. The loss-functions lesson derives this.

### Decision rule and threshold
For a binary problem you predict positive when p ≥ τ. The default τ = 0.5 is only optimal when both
errors cost the same and the training distribution matches production. Otherwise use the expected-cost
rule: predicting positive costs (1 − p)·C_FP in expectation, predicting negative costs p·C_FN, so predict
positive when p > C_FP / (C_FP + C_FN). If a missed fraud costs ten times a false alarm, the threshold is
1/11 ≈ 0.09, not 0.5. This only works if p is calibrated, which is why calibration is a separate lesson.

### Binary, multiclass, multilabel
- Binary: one logit, sigmoid, one threshold.
- Multiclass (exactly one label): K logits, softmax, argmax, or a threshold on the top probability
  with an "abstain" branch for low-confidence cases.
- Multilabel (any subset of K tags): K logits with independent sigmoids and per-tag thresholds. Softmax
  is wrong here because it forces the tags to compete for a total of one.
- Ordinal (small, medium, large) and hierarchical (department, then team) labels need their own
  treatment; forcing them into flat multiclass throws away structure.

### Evaluation
Accuracy misleads whenever classes are imbalanced: with 1% positives, predicting "negative" always
scores 99%. The confusion matrix shows where errors go; precision, recall and F1 summarise it at one
threshold; ROC-AUC and PR-AUC summarise ranking quality across thresholds; calibration checks whether a
0.8 means 80%. The metric lessons in this module cover each.

### Classification in the LLM era
Three ways to build a classifier today: prompt an LLM to output the label (no training data, flexible,
but slow, costly per call and with no calibrated probability unless you read token log-probabilities);
fine-tune a small encoder on labelled data (fast, cheap per call, reasonably calibrated, needs labels);
or embed inputs with a pre-trained encoder and fit logistic regression on top (a strong middle ground).
Routers, guardrails, intent detectors and LLM-as-judge pass/fail checks are all classifiers and deserve
the same rigour: a held-out set, a confusion matrix, and a threshold chosen from costs.

## Example
A fraud-style problem with 3% positives. The same trained model gives very different operating points
depending on the threshold; the threshold, not the model, is where the business decision lives.

```python
import numpy as np
from sklearn.datasets import make_classification
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import precision_score, recall_score

# Synthetic, illustrative: 20k rows, 20 features, ~3% positives
X, y = make_classification(n_samples=20_000, n_features=20, n_informative=8,
                           weights=[0.97, 0.03], random_state=0)
X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, stratify=y, random_state=0)

clf = LogisticRegression(max_iter=1000).fit(X_tr, y_tr)
p = clf.predict_proba(X_te)[:, 1]              # scores in [0, 1]; no decision has been made yet

# A missed positive costs ~10x a false alarm  ->  Bayes threshold = C_FP / (C_FP + C_FN) = 1/11
for tau in (0.5, 1 / 11):
    pred = (p >= tau).astype(int)
    print(f"tau={tau:.2f}  precision={precision_score(y_te, pred):.2f}  "
          f"recall={recall_score(y_te, pred):.2f}  flagged={pred.mean():.1%}")
# Expect: tau=0.50 -> high precision, low recall, few flagged.
#         tau=0.09 -> recall rises sharply, precision falls, many more rows flagged.
# Same model, same probabilities; only the decision rule changed.
```

## Trade-offs
- Exposing probabilities with a tunable threshold is more flexible than emitting hard labels, but it
  obliges you to keep the probabilities calibrated; if calibration cannot be maintained, a fixed decision
  boundary validated on a confusion matrix is the honest alternative.
- Softmax multiclass yields a proper distribution for mutually exclusive classes, but is wrong for
  overlapping labels; switch to per-label sigmoids the moment one input can legitimately carry two tags.
- An LLM prompted as a classifier needs no labelled data and changes in minutes, but costs more per call,
  adds latency and gives no calibrated score; once you have a few thousand labels and real throughput, a
  trained small classifier usually wins on all three.
- A finer taxonomy gives more useful labels but fewer examples per class, so tail classes degrade; a
  coarse classifier followed by a fine one within each branch flips the balance when tail classes matter.

## Pitfalls
- Reporting accuracy on imbalanced data; the symptom is 99% accuracy from a model that never predicts
  the minority class, visible instantly in the confusion matrix.
- Leaving the threshold at 0.5; the symptom is a respectable AUC with collapsed recall on the rare
  class, because the probabilities for that class rarely exceed 0.5.
- Trusting an LLM's verbal confidence ("I am 90% sure") as a probability; the symptom is a calibration
  curve where stated confidence has little relation to observed accuracy.
- Using softmax for a multilabel task; the symptom is a model that can never assign two tags to one
  document and splits probability between them instead.
- Vague or overlapping class definitions; the symptom is low inter-annotator agreement and a confusion
  matrix whose errors concentrate between the same two classes, a ceiling no model can break.
- Training on a rebalanced dataset and deploying on the natural distribution; the symptom is systematic
  over-prediction of the minority class and miscalibrated probabilities.

## When to use
Reach for classification whenever the output is a choice from a fixed set and you can state what each
kind of error costs. Do not use it when the label set is open-ended or changes weekly (use extraction,
retrieval or generation), or when the "class" is really a quantity in disguise (regression). Measure
with a confusion matrix at the deployed threshold, one threshold-free metric for ranking quality, a
calibration check, and per-class recall so tail classes are not hidden by the average. Day to day it
appears as routing, guardrails, intent detection, triage and judge pass/fail decisions. In interviews,
the recurring questions are how you choose the threshold, why cross-entropy rather than squared error,
and when accuracy is the wrong metric.

## Role lens
- **Engineer:** Builds the router or guardrail classifier, returns probabilities rather than bare labels,
  picks the threshold from a held-out confusion matrix and monitors class-distribution drift in production.
- **Architect:** Decides which decisions in the system are classifiers (router, safety filter, judge),
  sets the cost matrix and acceptable error rates for each, and chooses prompted-LLM versus trained
  classifier from volume, latency and calibration needs.
- **FDE:** Turns a customer's fuzzy categories into a label schema their experts can apply consistently,
  measures agreement before training anything, and sets thresholds against their stated cost of a miss.

## Check yourself
**Q:** An LLM-based guardrail flags about 1% of requests as unsafe and the safety team reports it is
"99% accurate". What questions do you ask, and what would you change about the system?
**A:** Accuracy says almost nothing when positives are rare; a filter that flags nothing would also score
near 99%. Ask for the confusion matrix on a labelled sample drawn at the natural distribution: how many
truly unsafe requests exist, how many were caught (recall), and how many flags were wrong (precision),
broken down by category. Then ask how the decision is made. If the model emits a text label, there is
no score and therefore nothing to tune; switch to the log-probability of the label token or to a trained
classifier that outputs a probability, check its calibration, and choose the threshold from the cost of a
missed unsafe request versus a wrongly blocked user. Finally add monitoring for drift in the flag rate
and periodic re-labelling, because the input distribution will move.

## Quiz
### Q: Why not treat a K-class problem as regression on the label index 0..K−1?
- [ ] Regression models cannot represent more than two outcomes
- [x] It imposes an ordering and distance between classes that does not exist, so errors are penalised arbitrarily
- [ ] Regression cannot be trained with gradient descent
- [ ] Regression outputs are constrained to the range 0 to 1
> Squared error on an index would treat predicting class 0 for a class-4 example as far worse than
> predicting class 3, although both are simply wrong. One score per class with cross-entropy removes the
> fake geometry and yields probabilities you can threshold.

### Q: A missed positive costs ten times a false alarm and the model's probabilities are calibrated. Which threshold minimises expected cost?
- [ ] 0.5, because that is where the two classes are equally likely
- [ ] 0.9, because the positive class must be certain before acting
- [x] About 0.09, from C_FP / (C_FP + C_FN) = 1/11
- [ ] The base rate of positives multiplied by 0.1
> Predict positive when p·C_FN exceeds (1 − p)·C_FP, which rearranges to p > C_FP/(C_FP + C_FN) = 1/11.
> A threshold of 0.5 silently assumes equal costs, and 0.9 would miss most of the expensive positives.

### Q: Documents may carry several tags at once. Which output layer is appropriate?
- [ ] Softmax over tags followed by argmax
- [x] An independent sigmoid per tag, each with its own threshold
- [ ] One regression output predicting the number of tags
- [ ] Softmax with a fixed top-3 selection
> Softmax forces the tag probabilities to sum to one, so two correct tags compete for the same mass.
> Independent sigmoids treat each tag as its own binary decision and allow per-tag thresholds that
> reflect each tag's cost and prevalence.
