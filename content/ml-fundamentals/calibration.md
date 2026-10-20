---
id: calibration
module: ml-fundamentals
name: Calibration
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - ml-fundamentals/roc-auc
  - ml-fundamentals/class-imbalance
  - ml-fundamentals/loss-functions
  - ml-fundamentals/confusion-matrix
  - model-internals/sampling-strategies
  - evals/judge-calibration-and-bias
  - system-design/model-routing-cascading
  - agents/human-in-the-loop
sources:
  - Guo et al., 2017 — On Calibration of Modern Neural Networks
  - Platt, 1999 — Probabilistic Outputs for Support Vector Machines and Comparisons to Regularized Likelihood Methods
  - Niculescu-Mizil & Caruana, 2005 — Predicting Good Probabilities with Supervised Learning
  - Kadavath et al., 2022 — Language Models (Mostly) Know What They Know
---

## TL;DR
A model is calibrated when its stated confidence matches its hit rate: of everything it calls "90%",
about 90% should be right. Ranking metrics like AUC do not need this; decisions do: cost-based
thresholds, abstaining, escalating to a human or a bigger model, combining scores. Modern neural
networks are usually overconfident, so you measure calibration on held-out data (reliability diagram,
ECE, Brier score) and fix it after training, most often with temperature scaling.

## Why it exists
A score and a probability are different contracts. A score only promises an ordering. A probability
promises that expected-value arithmetic works on it: that predicting positive when p ≥ C_FP / (C_FP +
C_FN) minimises expected cost, that "escalate when p < 0.8" escalates the cases that are wrong about
one time in five, that averaging two models' probabilities or feeding one into a Bayesian update gives
something meaningful. Every one of those uses breaks if "0.8" means "very sure" for one model and
"coin flip" for another, or means different things for the same model on Monday and on Friday.

Models do not deliver that contract by default, for reasons that are mostly understood. Cross-entropy
is a proper scoring rule, so in principle it rewards calibrated probabilities; in practice Guo et al.
showed that deep, wide networks with batch normalisation and little weight decay keep driving training
NLL down after accuracy has saturated, which pushes confidence toward 1 on everything and leaves the
test set overconfident. Margin-based learners such as SVMs and boosted trees emit scores with a
characteristic sigmoid-shaped distortion (Niculescu-Mizil & Caruana). Any class reweighting or
resampling done to fight imbalance shifts the model's implied base rate, so its probabilities are off
by a known but usually ignored factor. Large language models add their own variants: token
probabilities, verbalised confidence and sampling agreement are three different signals with
different calibration.

The naive alternative, "just tune the threshold on the raw score", works for a single fixed decision on
a fixed distribution and nowhere else: not when the threshold must transfer across customers, not when
the probability is consumed downstream, and not when the base rate drifts.

## How it works
### Definition, and why it is separate from accuracy
A binary model is perfectly calibrated when P(Y = 1 | p̂ = p) = p for every p; for multi-class the usual
version is top-label calibration, P(ŷ = y | confidence = c) = c. Calibration is orthogonal to
discrimination. A model that outputs the base rate for every input is perfectly calibrated and
useless; a model that ranks perfectly but emits every score in [0.49, 0.51] is useless as a
probability. AUC measures the second property, calibration metrics the first, and proper scoring
rules such as the Brier score and log loss blend both.

### Measuring it
A **reliability diagram** bins predictions by confidence (equal-width or equal-mass bins), then plots
the observed accuracy in each bin against the mean confidence in it. Perfect calibration is the
diagonal; points below it are overconfidence. **Expected calibration error** turns the diagram into a
number:

```
ECE = sum over bins m of  (|B_m| / n) * | acc(B_m) - conf(B_m) |
```

and **maximum calibration error** takes the worst bin instead. ECE depends on the binning and is biased
upward on small samples, so never compare ECEs computed with different bin counts. The **Brier
score**, mean (p̂ − y)², is a proper scoring rule and decomposes into a calibration term, a resolution
term and the irreducible uncertainty, so it rewards both calibration and sharpness without saying
which one moved. **Log loss** is also proper but penalises a confident mistake without bound, which is
why it is the training loss and Brier the reporting metric.

### Fixing it after training
Every post-hoc method learns a monotone map g from raw score to probability on a **calibration split
that was not used for training**, because fitting g on training data re-learns the overconfidence. You
then report on a third split; fitting and reporting on the same split flatters every method, isotonic
regression most.
- **Platt scaling** fits g(z) = σ(a·z + b) to the logit z by minimising NLL: two parameters, good when
  the distortion is sigmoid-shaped, originally built for SVM margins.
- **Temperature scaling** is the multi-class version with one parameter: softmax(z / T). T > 1 flattens
  the distribution (fixes overconfidence), T < 1 sharpens it. Because every logit is divided by the same
  positive constant, the argmax is unchanged, so accuracy and ROC-AUC are untouched by construction.
  Guo et al. found this single scalar as effective as far more flexible methods on the networks they
  tested. It cannot fix miscalibration that differs by class or is non-monotone in the score.
- **Isotonic regression** fits a non-parametric monotone step function. It can repair any monotone
  distortion but needs thousands of calibration points, overfits on fewer, and its flat steps create
  ties that can shave a little AUC. scikit-learn exposes both as `CalibratedClassifierCV` with
  `method="sigmoid"` or `method="isotonic"`.
- **Training-time influences** change the starting point rather than fixing it: label smoothing, mixup,
  stronger weight decay, early stopping on NLL rather than on accuracy, and ensembles, whose averaged
  probabilities are less extreme than any member's.

### Prior shift and resampling
If the model learned on data with positive rate π_s (because you rebalanced it) and will serve at rate
π_t, its probabilities are off by the ratio of the odds. The correction, assuming the class-conditional
distributions did not change, is

```
w   = (pi_t * (1 - pi_s)) / (pi_s * (1 - pi_t))
p_t = p_s * w / (p_s * w + (1 - p_s))
```

A model trained on a 50/50 rebalanced set that says 0.6 for an item means about 0.07 at a true
prevalence of 5%. The alternative is simpler: calibrate on a validation set drawn at the deployment
prevalence and skip the algebra.

### Calibration does not survive drift
Calibration is measured against one data distribution. Under distribution shift, models typically
become more overconfident on inputs unlike their training data, so a calibrated model in the eval
harness is not a calibrated model six months later. Treat ECE on fresh labelled samples as a
production monitor, not a one-time certificate.

### Abstention needs ranking *and* calibration
An "answer or escalate" rule asks two separate questions of the confidence. Does sorting by confidence
put the wrong answers at the bottom? That is discrimination, measured by the AUROC of confidence
against correctness, or by the risk–coverage curve: error rate among the kept answers as you keep the
top 100%, 90%, 80%… Then, where do I cut? That is where calibration matters, because with calibrated
confidence a threshold c means the marginal kept answer is wrong with probability 1 − c, the expected
error among kept answers is the mean of 1 − confidence over them, and the escalation rate can be
predicted before launch. Temperature scaling fixes only the second: it cannot reorder answers, so a confidence
signal whose risk–coverage curve is flat stays useless however well it is calibrated. Measure both
before building a cascade; if the curve is flat, the fix is a better signal (sampling agreement, a
trained verifier), not a better calibrator.

### Three confidences in an LLM
1. **Token probabilities.** For multiple-choice or classification formats, the probability mass on the
   answer token. Kadavath et al. report that large pretrained models are reasonably calibrated in this
   format; post-training with RLHF has been reported to degrade it, so re-check per model. Few-shot
   classifiers also carry majority-label and recency biases from the demonstrations, which can be
   estimated with a content-free input and divided out (contextual calibration).
2. **Verbalised confidence.** "I am 90% sure" is generated text, not a probability; it tends to cluster
   in a narrow high band regardless of correctness. Treat it as a feature to calibrate, not as p.
3. **Agreement under sampling.** Sample the answer n times at non-zero temperature and use the
   agreement rate. This behaves like an ensemble and is often the best cheap confidence signal for a
   black-box model, at n times the cost.

For LLM-as-judge, the same applies to the judge's score against human labels; for routers and
cascades, a calibrated "probability the small model is right" is what makes a cost-versus-quality
threshold mean anything.

## Example
Suppose a five-class classifier's predictions on 1,000 held-out items fall into five confidence bins
of 200 each, with mean confidences 0.55, 0.65, 0.75, 0.85, 0.96 and observed accuracies 0.52, 0.58,
0.64, 0.70, 0.80. Every bin sits below the diagonal; ECE = 0.2 × (0.03 + 0.07 + 0.11 + 0.15 + 0.16) =
0.104, and mean confidence is 0.75 against an overall accuracy of 0.65: a classic overconfident
network. The code fits a temperature on synthetic logits built to be exactly twice as sharp as they
should be, so the fitted T should land near 2, ECE should drop by roughly an order of magnitude, and
accuracy should not move.

```python
import torch, torch.nn.functional as F

def ece(probs, labels, n_bins=15):
    """Expected calibration error of the top label, equal-width bins."""
    conf, pred = probs.max(dim=1)
    hit = (pred == labels).float()
    edges = torch.linspace(0, 1, n_bins + 1)
    total = torch.zeros(())
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            total += m.float().mean() * (hit[m].mean() - conf[m].mean()).abs()
    return total.item()

def fit_temperature(logits, labels):
    """One scalar T fitted by NLL on a held-out calibration split. Argmax is unchanged, so accuracy is too."""
    log_t = torch.zeros(1, requires_grad=True)            # parametrise log T so T stays positive
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=200)
    def closure():
        opt.zero_grad()
        loss = F.cross_entropy(logits / log_t.exp(), labels)
        loss.backward()
        return loss
    opt.step(closure)
    return log_t.exp().item()

torch.manual_seed(0)
def make(n, k=5, overconf=2.0):
    """Stand-in for a trained net: the calibrated logits are 1.5*z; the 'net' emits twice that."""
    y = torch.randint(0, k, (n,))
    z = torch.randn(n, k) + 1.5 * F.one_hot(y, k)
    return overconf * 1.5 * z, y
logits_cal, y_cal = make(5_000)          # calibration split: never used for training
logits_test, y_test = make(5_000)        # reporting split: never used to fit T

T = fit_temperature(logits_cal, y_cal)
print(f"T={T:.2f}  ECE {ece(F.softmax(logits_test, 1), y_test):.3f} -> "
      f"{ece(F.softmax(logits_test / T, 1), y_test):.3f}  (T > 1 means the model was overconfident)")
# On this data T comes out close to 2 (about 1.94) and ECE falls from roughly 0.16 to below 0.02; accuracy is identical
```

## Trade-offs
- Temperature scaling has one parameter, preserves accuracy and ranking and needs little data, but it
  can only correct a global over- or under-confidence; switch to isotonic or per-class scaling when the
  reliability curve is S-shaped or class-dependent and you have thousands of calibration points.
- Post-hoc calibration is cheap and model-agnostic but is fitted to one distribution; under drift it
  degrades, so it buys you a production monitor to maintain rather than a one-time fix.
- Proper scoring rules reward calibration and sharpness in one number but cannot tell you which one
  moved; ECE isolates calibration but depends on binning and is biased on small samples. Report both
  when someone downstream will act on the probability.
- Spending a labelled split on calibration costs training data; worth it whenever a threshold, cascade,
  human escalation or user-facing confidence consumes the probability, pointless when only the ranking
  is consumed.
- Sampling-based confidence for LLMs is the most reliable black-box signal but costs n calls per
  answer; flip to token log-probabilities when the API exposes them and the output format is
  constrained enough for them to mean something.

## Pitfalls
- Fitting the calibrator on training data; the symptom is an ECE that looks fine offline while
  production overconfidence persists untouched.
- Fitting and reporting on the same split; the symptom is an optimistic ECE, worst with isotonic
  regression, that does not reproduce on new data.
- Class-weighting or resampling for imbalance and shipping the raw probabilities; the symptom is a mean
  predicted probability far above the observed base rate and a flooded queue at any sensible threshold.
- Assuming a better AUC means better probabilities, or vice versa; the symptom is a calibration fix
  judged useless because AUC did not move, or a new model trusted because AUC rose.
- Trusting verbalised LLM confidence as a probability; the symptom is a reliability diagram that is
  flat at 85–95% confidence across correct and incorrect answers alike.
- Ten equal-width bins on 200 examples; the symptom is ECE moving by 0.05 when you change the bin
  count.
- Reporting only the average ECE; the symptom is a minority class that is badly overconfident hiding
  behind a calibrated majority.

## When to use
Calibrate whenever something consumes the probability rather than the ranking: a cost-based
threshold, an abstain-or-escalate rule, a cascade to a larger model, a human-review queue sized by
confidence, a user-facing "likely" or "unlikely", or any averaging of scores across models. Skip it
when only the order matters, as in top-k retrieval or a leaderboard. Measure with a reliability
diagram plus ECE and Brier on a split the calibrator never saw, per class when classes are
imbalanced, and again on fresh production labels on a schedule. Default fix: temperature scaling;
escalate to isotonic with enough data. In interviews, expect to define calibration, distinguish it
from discrimination, explain why temperature scaling preserves accuracy, and say how you would get a
usable confidence out of a black-box LLM.

## Role lens
- **Engineer:** Fits the temperature on the validation split inside the training pipeline, stores T with
  the model artefact, and adds an ECE check on fresh labelled samples to the monitoring job.
- **Architect:** Decides which decisions in the system consume probabilities (routing thresholds,
  abstention, escalation budgets) and requires calibration evidence before any of them goes live.
- **FDE:** Turns the customer's "only escalate when the model is unsure" into a calibrated threshold with
  a measured escalation rate, and explains why a verbalised "95% confident" from the model is not that.

## Check yourself
**Q:** You built a cascade: a small model answers, and if its confidence is below 0.8 the request
escalates to a large model. After launch 5% of requests escalate, yet 30% of the non-escalated answers
are wrong. Diagnose the problem and say how you would fix it.
**A:** If a confidence of at least 0.8 were calibrated, at most about 20% of the kept answers could be
wrong; 30% means the small model is overconfident, so the threshold is not doing what its number
claims. Label a sample of production traffic, draw the reliability diagram, fit a temperature (or
Platt scaling) on it, and re-pick the threshold against the real costs: the price of an escalation
versus the price of a wrong answer. The escalation rate will rise, which is the honest number. If the
confidence is a verbalised one from a black-box model, replace it with sampling agreement or a trained
verifier. Then monitor ECE on fresh labels, because drift will undo the fix.

## Quiz
### Q: Why does temperature scaling leave accuracy and ROC-AUC unchanged?
- [ ] It only alters predictions whose confidence is below 0.5
- [x] Dividing every logit by the same positive T preserves each prediction's argmax and the ordering of scores
- [ ] It retrains the final layer with a calibration loss
- [ ] It is applied only during training, not at inference
> A single positive scalar is a strictly monotone transform of each logit vector, so the top class
> and the relative order of scores across examples are preserved; only the confidence values change.
> Accuracy depends on the argmax and AUC on the ordering, so neither can move.

### Q: A classifier trained on a 50/50 rebalanced set outputs p = 0.6 for an item. The true prevalence is 5%. Roughly what is the corrected probability?
- [ ] 0.60, because the model's probability already accounts for the prior
- [x] About 0.07
- [ ] About 0.03
- [ ] About 0.95
> The odds 0.6/0.4 = 1.5 must be multiplied by the ratio of deployment odds to training odds,
> (0.05/0.95)/(0.5/0.5) ≈ 0.053, giving odds ≈ 0.079 and a probability of about 0.073. The model's
> 0.6 silently assumed positives were as common as negatives.

### Q: Which statement about calibration metrics is correct?
- [ ] The Brier score measures calibration and nothing else
- [x] A predictor that always outputs the base rate has near-zero ECE yet no discriminative power, so calibration and discrimination are separate properties
- [ ] ECE is a proper scoring rule
- [ ] A low ECE implies a high ROC-AUC
> ECE only checks that confidence matches hit rate, which the constant base-rate predictor satisfies
> trivially. Brier blends calibration with resolution, ECE is a binned diagnostic rather than a proper
> scoring rule, and nothing about low ECE constrains the ranking that AUC measures.
