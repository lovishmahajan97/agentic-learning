---
id: confusion-matrix
module: ml-fundamentals
name: Confusion matrix
kind: concept
difficulty: intro
est_minutes: 12
status: reviewed
related:
  - ml-fundamentals/classification
  - ml-fundamentals/precision-recall-f1
  - ml-fundamentals/roc-auc
  - ml-fundamentals/class-imbalance
  - ml-fundamentals/calibration
  - evals/error-analysis
  - evals/llm-as-a-judge
  - safety-security/content-moderation
sources:
  - Fawcett, 2006 — An Introduction to ROC Analysis
  - Chicco & Jurman, 2020 — The advantages of the Matthews correlation coefficient (MCC) over F1 score and accuracy in binary classification evaluation
  - Official docs: scikit-learn (confusion_matrix, classification_report)
---

## TL;DR
A confusion matrix is the raw count of what the classifier said against what was true, one cell per
(actual, predicted) pair. Every single-number classification metric is a ratio of its cells, so any one
number throws away what the table shows: which class is mistaken for which, and in which direction.
Read the table first and choose the metric second, because the right metric depends on which cell's
errors are expensive, and that is a product decision.

## Why it exists
A classifier emits a label and the world has a true label. In the binary case four things can happen:
the model says yes and is right (true positive), says yes and is wrong (false positive), says no and is
wrong (false negative), says no and is right (true negative). Accuracy collapses the four into one
number and in doing so discards the only thing you need in order to act: the *direction* of the errors.

The cost of the two error types is almost never symmetric. A missed fraud and a wrongly declined card
are both "errors", but one costs the bank money and the other costs a customer. A moderation model
that lets harmful content through and one that blocks legitimate posts can both show "95% accuracy"
if the harmful class is 5% of traffic; so does the model that never flags anything. Without the table,
none of these are distinguishable.

The naive alternative is to report accuracy, or "the metric", and move on. But precision, recall,
specificity, F1 and MCC are all *derived* from the table; the table is the primary data. For
multi-class problems the aggregate hides even more: an intent router with 85% accuracy may be nearly
perfect on ten intents and useless on two that it confuses with each other, and the fix for that
(merge them, relabel, add a disambiguation rule) is only visible in the off-diagonal cells.

## How it works
### Layout and the convention trap
scikit-learn's `confusion_matrix(y_true, y_pred)` puts **actual classes in rows and predicted classes in
columns**, ordered by sorted label value unless you pass `labels=`. For binary labels `[0, 1]`:

```
                 predicted 0   predicted 1
actual 0 (neg)       TN            FP
actual 1 (pos)       FN            TP
```

so `tn, fp, fn, tp = cm.ravel()`. Other tools, textbooks and Wikipedia put predictions in rows. The
numbers are the same; the positions of FP and FN swap. Because a swapped reading silently exchanges
precision and recall, always label both axes and pass `labels=` explicitly. FP is the Type I error
(false alarm); FN is the Type II error (miss).

### Every metric is a ratio of cells
Group the derived quantities by what they *condition on*, because that determines how they behave.

**Row-normalised (condition on the truth).** These ask "given what the item really was, what did the
model do?" They are properties of the model alone; changing how many positives exist does not move
them in expectation.
- Recall = sensitivity = true-positive rate (TPR) = TP / (TP + FN)
- Specificity = true-negative rate (TNR) = TN / (TN + FP)
- False-positive rate (FPR) = FP / (FP + TN) = 1 − specificity
- False-negative rate (FNR) = FN / (FN + TP) = 1 − recall

**Column-normalised (condition on the prediction).** These ask "given the model said X, how often was
it right?", which is what a consumer of the prediction experiences. They mix model quality with the
base rate: the same model has lower precision when positives are rarer, because the FP column fills up
from a larger pool of negatives.
- Precision = positive predictive value (PPV) = TP / (TP + FP)
- Negative predictive value (NPV) = TN / (TN + FN)

**Whole-table.**
- Accuracy = (TP + TN) / N, dominated by whichever class is large.
- Prevalence = (TP + FN) / N, the base rate; report it next to every metric.
- Balanced accuracy = (TPR + TNR) / 2, accuracy with both classes weighted equally.
- Matthews correlation coefficient, MCC = (TP·TN − FP·FN) / √((TP+FP)(TP+FN)(TN+FP)(TN+FN)). It is the
  correlation between predicted and actual labels, ranges from −1 to 1, is 0 for a random classifier
  (and, by convention, for a constant one, where the denominator vanishes; scikit-learn returns 0),
  and uses all four cells, so it cannot be gamed by predicting one class.

### Multi-class
With *k* classes the matrix is *k × k*; the diagonal holds correct predictions and cell (i, j) counts
items of class *i* predicted as *j*. Each class gets its own one-vs-rest counts: TP_i is the diagonal
cell, FP_i the rest of column *i*, FN_i the rest of row *i*, TN_i everything else. Normalising by row
(`normalize="true"`) shows per-class recall; by column (`normalize="pred"`) per-class precision. The
off-diagonal *mass* is the diagnostic: one dense cell means two classes are not separable with the
current features or prompt, and the fix is local to that pair. In LLM systems this is the matrix of
an intent router, a guardrail classifier, or an LLM judge scored against human labels, where a judge
that is too lenient shows up as a heavy FP cell and one that is too strict as a heavy FN cell.

### One matrix is one threshold
A probabilistic model becomes a classifier only after you pick a threshold. Lowering it moves counts
from FN into TP and from TN into FP at the same time. The matrix therefore describes one *operating
point*; ROC and precision–recall curves are the matrix evaluated at every threshold. Comparing two
models by their matrices at the default 0.5 compares two arbitrary points.

### From cells to a decision: the cost matrix
The table becomes a decision tool once each cell has a price. Write a cost matrix with the same layout
(usually zero on the diagonal, C_FP and C_FN off it) and the expected cost of a model is the sum of
cell count times cell cost. This is the only principled way to rank two matrices with different error
mixes, and it is what every single-number metric approximates with a hidden, fixed cost ratio:
accuracy assumes C_FP = C_FN, F1 assumes the two errors are equally bad and TN are free. For multi-class
problems the cost matrix is k × k, because confusing "refund" with "cancel" may be cheap while confusing
"cancel" with "legal complaint" is not. For multi-label problems (each item can carry several labels)
there is no single k × k table; you get one 2 × 2 matrix per label (scikit-learn's
`multilabel_confusion_matrix`) and aggregate as with per-class metrics.

## Example
A fraud screen on 10,000 transactions where 1% (100) are fraud. The model flags 270 of them: 70 real
frauds and 200 legitimate ones, and misses 30 frauds. Reading the table: recall = 70/100 = 0.70;
precision = 70/270 ≈ 0.26; specificity = 9,700/9,900 ≈ 0.98; FPR ≈ 0.02; accuracy = 9,770/10,000 =
0.977. That accuracy is *worse* than the 0.99 you would get by never flagging anything, which is the
whole argument against accuracy on imbalanced data. Balanced accuracy is 0.84 and MCC ≈ 0.42, both of
which say "useful but far from done", which is what the table says too.

Now price the cells, with illustrative costs: a missed fraud loses $500 and a false decline costs $10
in support time and goodwill. The model costs 30 × 500 + 200 × 10 = $17,000; "never flag" costs
100 × 500 = $50,000. The model with the *lower* accuracy is the far cheaper one. Flip the costs (a
false decline costs $500 because these are high-value customers, a missed fraud $10 because it is
insured) and the model costs 30 × 10 + 200 × 500 = $100,300 against $1,000 for never flagging. Same
table, opposite verdict: the cost of the cells, not the table, decides.

```python
import numpy as np
from sklearn.metrics import confusion_matrix, classification_report, matthews_corrcoef

# Counts chosen to match the walkthrough above
tn, fp, fn, tp = 9_700, 200, 30, 70
y_true = np.array([0] * (tn + fp) + [1] * (fn + tp))
y_pred = np.array([0] * tn + [1] * fp + [0] * fn + [1] * tp)

cm = confusion_matrix(y_true, y_pred, labels=[0, 1])   # rows = actual, columns = predicted
print(cm)                                              # [[9700  200]
                                                       #  [  30   70]]
TN, FP, FN, TP = cm.ravel()
recall    = TP / (TP + FN)          # 0.70  share of fraud we caught
precision = TP / (TP + FP)          # 0.26  share of alerts that were fraud
fpr       = FP / (FP + TN)          # 0.02  looks tiny; it is 200 blocked customers
accuracy  = (TP + TN) / cm.sum()    # 0.977, and "never flag" would score 0.990
print(f"recall={recall:.2f} precision={precision:.2f} fpr={fpr:.3f} acc={accuracy:.3f}")
print("MCC", round(matthews_corrcoef(y_true, y_pred), 3))          # 0.417
print(classification_report(y_true, y_pred, target_names=["legit", "fraud"], digits=3))

# Multi-class routers: normalise by row to read per-class recall at a glance
# confusion_matrix(y_true, y_pred, labels=intents, normalize="true")
```

`classification_report` prints precision, recall, F1 and support per class plus macro and weighted
averages. Note how the "legit" row looks excellent and the weighted average inherits that, while the
"fraud" row carries the actual story.

## Trade-offs
- The table holds all the information about one operating point but gives you no ranking of two models
  by itself; to compare them you must first agree on a cost per cell, which is a product decision.
- Row-normalised rates (recall, FPR) are free of prevalence and portable across datasets, but they hide
  the absolute volume of false alarms; column-normalised rates (precision) show what users feel but
  move whenever the base rate moves.
- One matrix equals one threshold: cheap to compute and easy to explain, but comparing models at a
  default threshold compares arbitrary points; use curves when the threshold is tunable and matrices
  when the product has fixed it.
- A cost matrix turns the table into a clear decision, but its prices are estimates; when they are
  uncertain by more than the gap between candidate models, show the verdict across a range of cost
  ratios instead of one number.
- Multi-class matrices grow as k², readable at ten classes and unreadable at two hundred; past that
  you summarise (per-class recall sorted, top confused pairs) and lose the full picture.

## Pitfalls
- Reading a rows-as-actual matrix with the predictions-as-rows convention swaps FP and FN; the symptom
  is precision and recall exchanging values, and "the model misses everything" turning into "the model
  over-flags".
- Reporting accuracy on an imbalanced table; the symptom is a model with 97.7% accuracy that loses to
  the constant classifier at 99%.
- Omitting `labels=` when a class is absent from `y_true` or `y_pred`; the matrix silently shrinks or
  reorders and `.ravel()` unpacks the wrong cells, so every derived metric is computed from the wrong
  numbers.
- Treating the matrix at threshold 0.5 as "the" performance of a probabilistic model; the symptom is a
  model declared to have terrible recall when nobody tried a lower threshold.
- Collapsing a multi-class matrix into macro-F1 before looking at which pairs are confused; the symptom
  is weeks spent adding data to a class whose errors come from label ambiguity with its neighbour.
- Scoring an LLM judge only by agreement rate; the symptom is a judge that "agrees 96%" because it
  passes nearly everything, and the 4% it misses are exactly the failures you built it to catch.

## When to use
Every time you evaluate a classifier, before any single-number metric, and every time someone quotes
one: ask for the table and the prevalence. It matters most where error costs are asymmetric (fraud,
safety, medical triage), where classes are imbalanced, and where the "classifier" is an LLM: an intent
router, a guardrail, a feedback classifier or a judge scored against human labels. In day-to-day work
it is the first artefact of error analysis and the thing you put in front of a stakeholder to turn
"the model is wrong" into "it confuses A with B". In interviews, expect to derive precision, recall,
specificity and FPR from a table, to explain why accuracy misleads on rare classes, and to know which
axis is which in the library you claim to use.

## Role lens
- **Engineer:** Puts `confusion_matrix(y, ŷ, labels=...)` and the row-normalised multi-class plot into
  every eval report, and reads the off-diagonal cells of the router or guardrail before editing the prompt.
- **Architect:** Decides which cell is the expensive one for the product (missed fraud versus blocked
  customer) and encodes it as the metric, threshold and alerting policy the team optimises against.
- **FDE:** Walks the customer's subject-matter experts through the pilot's matrix, converts "the model is
  wrong" into "it confuses refund requests with cancellations", and gets a decision on which error they
  would rather live with.

## Check yourself
**Q:** A content-moderation classifier reports 99.2% accuracy. The product manager is pleased; the
trust-and-safety lead is not. Using the confusion matrix, explain what each of them may be looking at
and what you would report instead.
**A:** If harmful content is 0.8% of traffic, the constant "allow everything" classifier also scores
99.2%, so the number carries no information. On 100,000 items that is 800 harmful posts; a model with
TP = 400, FN = 400 and FP = 400 has exactly 99.2% accuracy while catching half the harm (recall 0.5) and
wrongly removing 400 legitimate posts (precision 0.5). The PM sees the aggregate; trust-and-safety sees
the FN cell, and the appeals team will see the FP cell. Report the four counts with the prevalence,
then recall at a fixed false-positive budget (or precision and recall at a threshold chosen on
validation), and compare candidate models with MCC or the precision–recall curve rather than accuracy.

## Quiz
### Q: In scikit-learn's `confusion_matrix(y_true, y_pred)` with labels [0, 1], which cell holds the false negatives?
- [ ] C[0, 1]
- [x] C[1, 0]
- [ ] C[1, 1]
- [ ] C[0, 0]
> Rows are actual classes and columns are predicted, so row 1 (actually positive) and column 0
> (predicted negative) is a miss. C[0, 1] is the false-positive cell; tools that put predictions in rows
> transpose this, which is why you unpack with `tn, fp, fn, tp = cm.ravel()` and label both axes.

### Q: The model is unchanged but the positive class becomes rarer in production. Which metrics move?
- [ ] Recall and specificity
- [x] Precision and negative predictive value
- [ ] False-positive rate and false-negative rate
- [ ] None of them; they are all properties of the model
> Precision and NPV condition on the prediction, so their denominators mix true positives with false
> positives whose count scales with the number of negatives. Recall, specificity, FPR and FNR each
> condition on the true class and are unchanged in expectation when only the class ratio changes.

### Q: A 12-intent router has macro-F1 0.81 and most of its errors sit in one off-diagonal cell: actual "cancel_subscription" predicted as "refund_request". What is the most useful next step?
- [ ] Add more training examples for every intent equally
- [ ] Raise the decision threshold for "refund_request"
- [x] Inspect those examples for label ambiguity between the two intents, then merge them or add a disambiguation rule
- [ ] Switch the headline metric from macro-F1 to micro-F1
> A single dense off-diagonal cell is a separability problem between two specific classes, usually
> overlapping definitions or inconsistent labelling, so the fix is local to that pair. Spreading data
> across all classes or changing the aggregate metric does not touch the cause.
