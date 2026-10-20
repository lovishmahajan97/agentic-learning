---
id: precision-recall-f1
module: ml-fundamentals
name: Precision / recall / F1
kind: metric
difficulty: intro
est_minutes: 14
status: fact-checked
related:
  - ml-fundamentals/confusion-matrix
  - ml-fundamentals/roc-auc
  - ml-fundamentals/class-imbalance
  - ml-fundamentals/calibration
  - rag-retrieval/recall-vs-precision-trade-off
  - evals/retrieval-evaluation
  - evals/hallucination-evaluation
  - safety-security/input-output-guardrails
sources:
  - van Rijsbergen, 1979 — Information Retrieval
  - Manning, Raghavan & Schütze, 2008 — Introduction to Information Retrieval
  - Powers, 2011 — Evaluation: From Precision, Recall and F-Measure to ROC, Informedness, Markedness and Correlation
  - Official docs: scikit-learn (precision_recall_fscore_support, precision_recall_curve)
---

## TL;DR
Precision asks "of what I flagged, how much was right"; recall asks "of what was there, how much did I
find". They pull against each other through the decision threshold, so a scorer is a curve of
(precision, recall) pairs, not a point. F1 is the harmonic mean at one chosen point: a single number
that is only meaningful once you have decided the two error types cost about the same, which in
production they rarely do.

## Why it exists
Accuracy fails twice on detection problems. It is dominated by the common class, so on 2%-positive
data a model that finds nothing scores 98%. And it treats a false alarm and a miss as the same error,
when in fraud, abuse, prompt-injection or hallucination detection they have different owners and
different costs.

The metrics come from information retrieval, where the failure of accuracy is extreme: for a query
over a million documents, the true negatives (irrelevant documents correctly not returned) number in
the hundreds of thousands and say nothing about the search engine. So the field built its measures
from the three cells that do carry information: the relevant documents returned (TP), the irrelevant
ones returned (FP) and the relevant ones missed (FN). Precision and recall are those two ratios, and
every detection task has the same shape, which is why the retrieval vocabulary now covers
classifiers, extractors, guardrails and RAG pipelines.

Each metric alone is trivially gamed. Precision is maximised by flagging the single most certain item;
recall is maximised by flagging everything. The naive fix, averaging them, is still gameable: a
precision of 1.0 and recall of 0.1 average to 0.55, which rewards a model that found almost nothing.
Van Rijsbergen's effectiveness measure was designed to resist exactly that, and F1 is its most common
form.

## How it works
### Definitions from the confusion matrix
Precision P = TP / (TP + FP) and recall R = TP / (TP + FN); recall is the same quantity as sensitivity or
the true-positive rate. Both ignore TN deliberately, because in the retrieval setting it is unbounded
and in most detection settings it is uninteresting. Note what each conditions on: recall conditions on
the truth (a property of the model alone), precision conditions on the prediction (a property of the
model *and* the base rate).

### Why the harmonic mean
F1 = 2PR / (P + R) = 2TP / (2TP + FP + FN). The harmonic mean is pulled toward the smaller of its inputs:
for P = 1.0 and R = 0.1 the arithmetic mean is 0.55 but F1 ≈ 0.18. It was chosen *because* a score that
can be bought by sacrificing one side is not a score of effectiveness. The hidden assumption is the
equal weighting of FP and FN in the denominator. When that is wrong, use the general form

```
F_beta = (1 + beta^2) * P * R / (beta^2 * P + R)
```

where β says how many times more you value recall than precision: F2 for screening tasks where a miss
is the expensive error, F0.5 where a false alarm is (for example, automatically blocking a user). F1 is
β = 1.

### The threshold is the knob
A scorer outputs s(x) and you predict positive when s(x) ≥ τ. Lowering τ flags more items: recall can
only go up (it is monotone in τ), precision usually goes down but not monotonically. Plotting (R(τ),
P(τ)) for every τ gives the precision–recall curve; a random scorer sits on a horizontal line at the
prevalence, which is the floor to compare against. Average precision, AP = Σ_n (R_n − R_{n−1}) · P_n,
summarises the curve as a threshold-free number (scikit-learn's definition, which avoids the optimism
of trapezoidal interpolation). The operating point should come from a product constraint evaluated on
a validation split: "maximum precision subject to recall ≥ 0.9" is a far better specification than
"maximum F1", because it states which error you refuse to make.

### Prevalence changes precision, not recall
Fix a model with TPR 0.9 and FPR 0.01. On 1,000 items at 10% prevalence it produces 90 TP and 9 FP, so
precision ≈ 0.91. At 1% prevalence it produces 9 TP and 9.9 FP, so precision ≈ 0.48. Nothing about the
model changed; only the pool of negatives feeding the FP count did. This is why precision measured on
a balanced benchmark does not transfer to a 1%-positive production stream, while recall, in
expectation, does.

### Averaging across classes
For multi-class or multi-label problems each class gets one-vs-rest P, R and F1, then you aggregate.
**Micro** pools the TP, FP and FN counts first; in single-label multi-class classification every
misclassified item is one class's FP and another class's FN, so micro-P = micro-R = micro-F1 =
accuracy. **Macro** averages the per-class scores unweighted, so a rare intent counts as much as a
common one, which is the honest headline for long-tail label sets and also the noisiest. **Weighted**
averages by support and behaves like accuracy. Pick macro when the product promise is per class, micro
when it is per item.

### Undefined corners
Precision with zero predicted positives is 0/0; scikit-learn's `zero_division` parameter decides what
to return and warns by default. Recall with zero actual positives is likewise undefined, which is one
reason to stratify small evaluation sets. Per-class metrics with a support of three are noise; always
print the support column.

### Where it shows up in LLM systems
Retrieval: recall@k over the relevant chunks decides whether the generator can possibly answer;
precision@k decides how much noise it must ignore. Extraction: span-level precision and recall, exact
or partial match. Extractive QA: token-level F1 between the predicted and gold answer strings (the
SQuAD convention, reported alongside exact match). Guardrails: recall on injections versus the
false-block rate on legitimate traffic. Hallucination detection: recall on fabricated claims is the
safety number, precision is the annoyance number. LLM-as-judge: treat the judge as a classifier and
compute its precision and recall on a human-labelled set before trusting its pass rate.

## Example
A hallucination detector scores 2,000 answers of which about 5% contain a hallucination. The code
computes precision, recall and F1 at three thresholds, then finds the threshold that satisfies the
product requirement "catch at least 90% of hallucinations" with the best precision, and finally
contrasts micro and macro averaging on a three-class router where the rarest class is never predicted
(micro-F1 = 0.94 because accuracy is 94%; macro-F1 ≈ 0.57 because the missing class contributes a 0).

```python
import numpy as np
from sklearn.metrics import (precision_recall_fscore_support, precision_recall_curve,
                             average_precision_score, f1_score)

rng = np.random.default_rng(0)
y = (rng.random(2_000) < 0.05).astype(int)                      # 1 = answer contains a hallucination
score = np.where(y == 1, rng.normal(0.65, 0.15, 2_000),          # detector score: positives score higher,
                 rng.normal(0.35, 0.15, 2_000)).clip(0, 1)       # but the two populations overlap

for tau in (0.5, 0.4, 0.3):
    p, r, f, _ = precision_recall_fscore_support(y, score >= tau, average="binary", zero_division=0)
    print(f"tau={tau}: precision={p:.2f} recall={r:.2f} F1={f:.2f} flagged={(score >= tau).sum()}")

# Product spec: "catch at least 90% of hallucinations". Pick the most precise threshold that does.
prec, rec, thr = precision_recall_curve(y, score)
feasible = rec[:-1] >= 0.90                                      # the last curve point has no threshold
i = np.argmax(np.where(feasible, prec[:-1], -1))
print(f"recall>=0.90 -> tau={thr[i]:.3f}, precision={prec[i]:.2f}, AP={average_precision_score(y, score):.3f}")
# In real work choose tau on a validation split; the test split only confirms the pair you promised.

# Multi-class: macro treats a rare intent as equal to a common one; micro equals accuracy.
yt = np.array([0] * 90 + [1] * 8 + [2] * 2)
yp = np.array([0] * 88 + [1] * 2 + [1] * 6 + [0] * 2 + [0] * 2)  # the 2-example class is never predicted
print("micro", f1_score(yt, yp, average="micro"), "macro", round(f1_score(yt, yp, average="macro"), 3))
```

As τ drops from 0.5 to 0.3, recall climbs toward 1.0 while precision falls and the flagged count
roughly triples: the same model, three different products.

## Trade-offs
- F1 gives one comparable number but bakes in "a false alarm costs the same as a miss"; it flips to Fβ
  or to "recall at a fixed precision" as soon as one error is clearly more expensive, which is nearly
  always in production.
- Precision reflects what reviewers see in the flagged queue but moves with prevalence; recall is
  portable across base rates but says nothing about how much junk the queue contains. On a shifting
  base rate, report the (TPR, FPR) pair plus the expected alert volume instead.
- Macro averaging protects rare classes but makes the headline hostage to tiny-support classes whose
  estimates are noise; micro and weighted are stable but hide long-tail collapse. Choose by whether the
  product promise is per item or per class.
- Threshold-free summaries such as average precision compare rankers fairly, but you ship one
  threshold; a model with higher AP can be worse at the single operating point you need.

## Pitfalls
- Reporting F1 at the default 0.5 threshold; the symptom is "the model only has 0.3 recall" when
  lowering τ to 0.2 would give 0.85 recall at an acceptable precision.
- Choosing the threshold on the test set; the symptom is that the promised precision/recall pair is not
  reproduced on the next batch of data, because the selection leaked test labels into the decision.
- Comparing precision across datasets with different prevalence; the symptom is a model that
  "degraded" when it moved from a 50/50 benchmark to 2% production traffic without changing at all.
- Quoting macro-F1 with classes of support one to five; the symptom is the metric swinging by 0.1
  between runs with no model change.
- Blaming the generator for low RAG answer quality when recall@k is the problem; the symptom is weeks
  of prompt tuning while the right chunk is not in the top-k at all.
- Scoring free-text LLM answers with token F1 against a single gold phrasing; the symptom is correct
  paraphrases marked wrong. Normalise aggressively or use a judge for anything non-extractive.

## When to use
Reach for precision and recall whenever the positive class is what matters and the negatives are
plentiful or uninteresting: detection, extraction, retrieval, moderation, routing to a rare action.
Report them as a pair at a threshold chosen on validation, with the prevalence and the support, and
add the precision–recall curve or AP when you are comparing scorers. Use F1 as a tie-breaker for
dashboards and leaderboards, Fβ when costs are asymmetric and roughly known, and a cost-weighted
expected loss when they are known precisely. Do not lean on any of them where the negative class
matters as much as the positive; then look at specificity or balanced accuracy too. In interviews, be
ready to derive F1 from the matrix, explain why the harmonic mean, and show how prevalence moves
precision but not recall.

## Role lens
- **Engineer:** Writes the threshold sweep into the eval harness, stores the chosen τ with the model
  version, and reports recall@k and precision@k for retrieval separately from answer quality.
- **Architect:** Translates the business constraint into which metric is fixed and which is maximised
  (recall floor versus false-alarm budget) and makes that the acceptance criterion for every release.
- **FDE:** Asks the customer which error they would rather explain to their users, sets β or the recall
  floor from that answer, and shows the precision–recall curve so they see what each choice costs.

## Check yourself
**Q:** Your prompt-injection detector has precision 0.95 and recall 0.60 on the evaluation set. The
security team wants recall of at least 0.95. What will happen to precision, and how do you decide
whether the result is acceptable?
**A:** Raising recall means lowering the threshold, which flags more borderline inputs, so precision
falls; how far depends on how much the score distributions of injections and benign messages overlap,
which the precision–recall curve on the validation split shows directly. Acceptability is a volume
question, not a ratio question: at a production prevalence of 0.1%, every 100,000 messages contain
about 100 injections, recall 0.95 catches 95, and if precision drops to 0.5 there are also 95 false
blocks, roughly one legitimate message in a thousand. Whether that is acceptable depends on what a
false block costs (friction, a fallback to human review). If it is too high, the usual move is a
two-stage design: this high-recall detector feeding a slower, more precise check on the small flagged
set.

## Quiz
### Q: A classifier has precision 1.0 and recall 0.1. What is its F1, and why does it differ from the arithmetic mean of 0.55?
- [ ] 0.55, because F1 is the average of precision and recall
- [x] About 0.18, because the harmonic mean is pulled toward the smaller value
- [ ] About 0.32, because F1 is the geometric mean
- [ ] 0.10, because F1 equals the minimum of the two
> F1 = 2 × 1.0 × 0.1 / 1.1 ≈ 0.18. The harmonic mean punishes imbalance so a model cannot score well by
> sacrificing one side; the geometric mean would be about 0.32 and the minimum 0.10, and neither of
> those is F1.

### Q: A model has TPR 0.9 and FPR 0.01 on a balanced benchmark. In production positives are 1% of traffic. Which metric transfers unchanged in expectation?
- [ ] Precision
- [ ] F1
- [x] Recall
- [ ] All three
> Recall is the TPR and conditions only on positives, so prevalence cancels. Precision mixes true
> positives with false positives whose count scales with the number of negatives: per 1,000 items the
> model goes from about 450 TP against 5 FP to 9 TP against 9.9 FP, so precision falls from 0.99 to
> about 0.48, and F1 falls with it.

### Q: In single-label multi-class classification, micro-averaged F1 is always equal to which quantity?
- [ ] Macro-averaged F1
- [x] Accuracy
- [ ] The F1 of the largest class
- [ ] The mean of the per-class recalls
> Pooling counts makes total FP equal total FN, because each misclassified item is a false positive for
> the predicted class and a false negative for the true class; micro-precision, micro-recall and
> micro-F1 therefore all equal correct/total. Macro-F1 differs whenever classes perform unequally.
