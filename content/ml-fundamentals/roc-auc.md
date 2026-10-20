---
id: roc-auc
module: ml-fundamentals
name: ROC-AUC
kind: metric
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - ml-fundamentals/confusion-matrix
  - ml-fundamentals/precision-recall-f1
  - ml-fundamentals/class-imbalance
  - ml-fundamentals/calibration
  - evals/model-evaluation
  - evals/llm-as-a-judge
  - rag-retrieval/reranking
  - system-design/model-routing-cascading
sources:
  - Fawcett, 2006 — An Introduction to ROC Analysis
  - Hanley & McNeil, 1982 — The Meaning and Use of the Area under a Receiver Operating Characteristic (ROC) Curve
  - Davis & Goadrich, 2006 — The Relationship Between Precision-Recall and ROC Curves
  - Saito & Rehmsmeier, 2015 — The Precision-Recall Plot Is More Informative than the ROC Plot When Evaluating Binary Classifiers on Imbalanced Datasets
---

## TL;DR
ROC-AUC measures how well a scorer *ranks* positives above negatives across every threshold at once:
it equals the probability that a randomly chosen positive outscores a randomly chosen negative. It is
threshold-free and unaffected by class prevalence, which makes it the right number for comparing
scorers and the wrong number for judging a deployed classifier on rare positives, where it hides the
false-alarm volume and says nothing about whether the scores are calibrated probabilities.

## Why it exists
Every metric read off a confusion matrix depends on a threshold, and the threshold is usually decided
late, per customer, or by a cost model that does not exist yet when the model is being chosen.
Comparing two models at the default 0.5 compares two arbitrary points, and the better model at 0.5 is
often the worse model at the threshold you will actually ship. You need a way to score the *scorer*,
independent of any single cut.

The ROC curve comes from signal-detection theory: radar operators in the 1940s needed to separate how
good a receiver was at distinguishing signal from noise (its discriminative power) from how
trigger-happy the operator was (the threshold). Plotting true-positive rate against false-positive
rate for every threshold does exactly that, and the area under the plot collapses the whole curve into
one number with a clean probabilistic meaning.

The naive alternatives fail in predictable ways. Accuracy at 0.5 is threshold-bound and
prevalence-bound. "F1 at the best threshold" uses the evaluation labels to pick the threshold, which is
leakage, and still describes one point. Log loss rewards calibration as well as ranking, so a
well-ranked but overconfident model looks bad even when a monotone fix would make it excellent. AUC
isolates the one property that survives recalibration and rethresholding: the ordering.

## How it works
### Building the curve
Sort the items by score, descending. Start at (FPR, TPR) = (0, 0) with the threshold above the highest
score. Lower the threshold one item at a time: each positive you admit moves the curve up by 1/n_pos,
each negative moves it right by 1/n_neg, and a group of tied scores moves diagonally. The curve ends
at (1, 1) when everything is flagged. A scorer that ranks at random walks along the diagonal; a perfect
one goes straight up to (0, 1) and then across. A curve *below* the diagonal is not useless: the score
is anti-correlated with the label, and negating it gives an AUC of 1 − AUC.

### What the area means
AUC = P(s(x⁺) > s(x⁻)) + ½ · P(s(x⁺) = s(x⁻)) for a random positive x⁺ and a random negative x⁻. This
is the Mann–Whitney U statistic normalised by n_pos · n_neg: count, over all positive–negative pairs,
how many are ordered correctly. An equivalent rank formula, with R⁺ the sum of the ascending ranks of
the positives, is

```
AUC = (R_plus - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
```

Because AUC is a rank statistic, any strictly monotone transform of the scores leaves it unchanged.
That is why it cannot see calibration: a model whose probabilities all lie between 0.49 and 0.51 can
have an AUC of 0.95, and temperature scaling never changes AUC.

### Why prevalence does not move it
TPR is estimated within the positives and FPR within the negatives, so changing the ratio of the two
classes changes neither in expectation. This is the strength: AUC measured on a 50/50 benchmark is a
fair estimate of ranking quality on 1%-positive traffic. It is also the weakness. With 1,000 positives
and 1,000,000 negatives, an FPR of 0.01 looks small on the ROC axis but is 10,000 false alarms next to
900 true positives at TPR 0.9, a precision of about 8%. The ROC curve looks excellent; the
precision–recall curve shows the pain. Davis & Goadrich showed that one curve dominates another in
ROC space if and only if it does so in PR space, but the *areas* weight regions differently: on rare
positives the PR curve is far more sensitive to differences in the low-FPR region, which is the only
region you can afford.

### Reading the number
0.5 is random, 1.0 is perfect separation, and nothing about 0.8 means "80% correct". Scale is
task-dependent, so compare models on the same data, not AUCs across problems. Differences of 0.01 can
be noise with few positives, because each positive contributes one full row of the pair matrix; report
a bootstrap confidence interval. When only a slice of operating points is feasible, use partial AUC
over FPR ≤ your budget, or simply read TPR at that FPR.

### Multi-class
scikit-learn's `roc_auc_score(multi_class="ovr")` computes a one-vs-rest AUC per class and averages
them (macro or support-weighted); `"ovo"` averages pairwise AUCs between every pair of classes, which is
less sensitive to imbalance. Both need probability vectors, not hard labels.

### Where it shows up in LLM systems
Guardrail, moderation and injection detectors: AUC chooses the model or version, then the PR curve and
a threshold at the false-positive budget make it a classifier. Rerankers: AUC over (relevant,
irrelevant) pairs is a sanity check beside NDCG. Routers and cascades: the confidence that decides
whether to escalate to a bigger model is a scorer, and the AUC of "confidence versus whether the small
model was right" tells you whether routing can work at all, before you tune a threshold. LLM-as-judge
with a numeric score: AUROC against human pass/fail labels tells you whether the judge is a usable
detector, separately from where you cut it.

## Example
Four positives and six negatives give 24 positive–negative pairs; 20 are ordered correctly (the
positive at 0.3 loses to three negatives and the one at 0.6 loses to the negative at 0.7), so AUC =
20/24 ≈ 0.833. The rank formula agrees: the positives have ascending ranks 4, 7, 9 and 10, so
(30 − 10)/24. The code checks both against scikit-learn, shows that cubing or taking logs of the
scores changes nothing, prints the confusion-matrix sweep behind the curve, and then builds two
datasets with identical score separation (two unit-variance Gaussians two standard deviations apart,
where the theoretical AUC is Φ(√2) ≈ 0.92) at prevalences of 50% and 1%: ROC-AUC barely moves while
average precision collapses.

```python
import numpy as np
from sklearn.metrics import roc_auc_score, average_precision_score, roc_curve

y = np.array([1, 1, 1, 1, 0, 0, 0, 0, 0, 0])                         # 4 positives, 6 negatives
s = np.array([0.9, 0.8, 0.6, 0.3, 0.7, 0.5, 0.4, 0.2, 0.1, 0.05])    # detector scores

def auc_by_pairs(y, s):
    pos, neg = s[y == 1], s[y == 0]
    wins = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return wins / (len(pos) * len(neg))

print(auc_by_pairs(y, s), roc_auc_score(y, s))                       # both 0.8333
print(roc_auc_score(y, s ** 3), roc_auc_score(y, np.log(s)))         # unchanged: AUC only sees the ranking

fpr, tpr, thr = roc_curve(y, s, drop_intermediate=False)
for f, t, th in zip(fpr, tpr, thr):
    print(f"threshold>={th:.2f}: FPR={f:.3f} TPR={t:.2f}")           # one confusion matrix per row

rng = np.random.default_rng(0)
def sample(n_pos, n_neg):
    yy = np.r_[np.ones(n_pos), np.zeros(n_neg)]
    ss = np.r_[rng.normal(2, 1, n_pos), rng.normal(0, 1, n_neg)]     # same separation in both datasets
    return yy, ss
for n_pos, n_neg in ((5_000, 5_000), (500, 50_000)):
    yy, ss = sample(n_pos, n_neg)
    print(f"prevalence={n_pos/(n_pos+n_neg):.1%}: ROC-AUC={roc_auc_score(yy, ss):.3f}  "
          f"PR-AUC={average_precision_score(yy, ss):.3f}")
# Expect ROC-AUC near 0.92 in both runs and average precision falling from about 0.9 to well under 0.3
```

## Trade-offs
- Threshold-free and prevalence-free, so ideal for model selection and cross-dataset comparison; but
  the product ships at one threshold at one prevalence, so AUC can rank two models in the opposite
  order from the metric you will be judged on.
- It averages over all operating points equally, including FPR regions you could never afford; flip to
  partial AUC or the precision–recall curve when only the low-FPR corner matters, which on rare
  positives is always.
- Rank-based, so robust to score scale and to monotone drift in the scorer, but blind to calibration;
  pair it with ECE or the Brier score whenever a downstream decision consumes the probability itself.
- Stable with thousands of each class, noisy with few positives, since each positive is a whole row of
  the pair comparison; the fix is a bootstrap interval, which costs compute and a little humility.

## Pitfalls
- Reading AUC as accuracy or as "the probability a flag is right"; the symptom is stakeholders
  expecting 90% of alerts to be true because the AUC was 0.9.
- Celebrating 0.98 AUC on 0.1%-prevalence data; the symptom is a review queue that is 95% false alarms
  once the threshold is set low enough to catch most positives.
- Comparing probabilities before and after temperature scaling by AUC; the symptom is "calibration did
  nothing", because AUC is invariant to monotone maps by construction.
- Passing hard 0/1 predictions instead of scores; the symptom is a two-point curve whose AUC equals
  (TPR + TNR)/2, so every model looks similar and worse than it is.
- Macro one-vs-rest AUC on a heavily imbalanced multi-class problem; the symptom is a class with three
  test examples swinging the headline number between runs.
- Treating a 0.003 AUC gain as progress without an interval; the symptom is the gain reversing on the
  next data refresh.

## When to use
Use ROC-AUC to choose between scorers, to track whether a retrained model still ranks as well, and to
compare quality across datasets with different base rates; it is the natural first metric for any
detector, reranker, router confidence or judge score. Do not use it alone to sign off a deployed
classifier: add the precision–recall curve when positives are rare, TPR at your FPR budget when the
budget is fixed, and a calibration check when the score feeds a threshold, a cascade or a user-facing
confidence. Measure it with a confidence interval when positives number in the hundreds or fewer. In
interviews, expect to state the pairwise-probability interpretation, explain why prevalence does not
move it and why that is both good and bad, and say when you would switch to PR-AUC.

## Role lens
- **Engineer:** Logs ROC-AUC and average precision for every detector version in the eval harness and
  bootstraps an interval before declaring a retrain better than the incumbent.
- **Architect:** Sets which curve is the acceptance criterion for each component, and fixes the FPR
  budget that turns a ranking into a deployed threshold.
- **FDE:** Explains to a customer why the vendor's 0.97 AUC still produced a flood of false alerts on
  their 0.2%-positive traffic, and re-expresses success as alerts per day at a target recall.

## Check yourself
**Q:** Two prompt-injection detectors score ROC-AUC 0.97 and 0.94 on the same evaluation set. The
security team can tolerate at most 0.5% false blocks on legitimate traffic. Which do you ship, and what
do you need to look at before deciding?
**A:** Not necessarily the 0.97 model. AUC averages ranking quality over every false-positive rate,
but the constraint pins the operating point to FPR ≤ 0.005, so the question is which curve is higher
at that point: read TPR at FPR = 0.005 (or the partial AUC over [0, 0.005]) on each ROC curve, with
bootstrap intervals, because the 0.94 model may be steeper near the origin. Then translate to
production volume: at 0.1% prevalence, 0.5% FPR is five times as many false blocks as there are
injections, so also check precision at that point and whether a second, more precise stage can sit
behind the detector. If the confidence is used to route to that second stage, check its calibration
too, which AUC cannot tell you.

## Quiz
### Q: What does ROC-AUC = 0.85 literally mean?
- [ ] 85% of the model's predictions are correct
- [x] A randomly chosen positive outscores a randomly chosen negative 85% of the time
- [ ] 85% of positives are found at the default threshold
- [ ] The model's probabilities are 85% calibrated
> AUC is the normalised Mann–Whitney statistic: the fraction of positive–negative pairs ordered
> correctly, with ties counting half. It says nothing about accuracy at any threshold or about whether
> the scores are valid probabilities.

### Q: After temperature scaling, a detector's expected calibration error falls from 0.12 to 0.02 but its ROC-AUC is identical. Why?
- [ ] Temperature scaling failed to change the model
- [x] AUC depends only on the ranking, and dividing all logits by a constant is a monotone transform
- [ ] ECE and AUC were computed on different splits
- [ ] AUC only uses the hard predictions, which did not change
> Temperature scaling rescales scores without reordering them; because AUC is a rank statistic it is
> invariant to any strictly monotone map. ECE measures the match between stated confidence and hit
> rate, which the rescaling does change.

### Q: Positives are 0.2% of traffic. Which view best reveals whether the alert queue will be usable?
- [ ] The ROC curve
- [ ] Accuracy at the default threshold
- [x] The precision–recall curve or average precision
- [ ] Log loss
> The precision axis shows the fraction of alerts that are true at each recall level, which is what the
> queue experiences. The ROC's FPR axis is normalised by the huge negative count, so an FPR of 1% looks
> fine while producing five times as many false alerts as there are positives.
