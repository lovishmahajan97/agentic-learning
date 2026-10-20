---
id: regression
module: ml-fundamentals
name: Regression
kind: concept
difficulty: intro
est_minutes: 12
related:
  - ml-fundamentals/classification
  - ml-fundamentals/loss-functions
  - ml-fundamentals/gradient-descent
  - ml-fundamentals/train-test-leakage
  - training-adaptation/overfitting
  - python-for-ai/scikit-learn-basics
  - infra-capacity-economics/quality-latency-cost-optimization
sources:
  - Hastie, Tibshirani & Friedman, 2009 — The Elements of Statistical Learning
  - Huber, 1964 — Robust Estimation of a Location Parameter
  - Official docs: scikit-learn
---

## TL;DR
Regression predicts a continuous quantity: latency in milliseconds, cost in dollars, a quality score.
The model outputs a number and the loss measures its distance from the truth; choosing squared versus
absolute error is choosing whether you estimate the conditional mean or the median, and how much a few
extreme points steer the fit. In AI systems regression appears as reward models, latency and cost
predictors, judge scores and value heads, and it fails in the same ways least squares always has:
extrapolation, leverage points and leakage.

## Why it exists
Some questions are "how much", not "which": how long will this request take, what will this month's
token bill be, how good is this response on a scale. Two naive approaches fail.

Bucketing the quantity into ranges and classifying throws away the ordering and the resolution: a
prediction of "200–500 ms" cannot be summed into a capacity plan, and a value just over a bucket edge is
treated as a different category from one just under it. Lookup tables of averages per group cannot
interpolate to combinations they have not seen and explode combinatorially as features are added.

What is needed is a function from features to a number together with a differentiable notion of error.
Least squares was invented for astronomy to combine noisy observations into one estimate; machine
learning kept the frame and replaced the linear function with anything differentiable. The crucial
insight that survived is that the loss is a statement about the noise. Squared error is the
maximum-likelihood choice if the noise is Gaussian, and its minimiser is the conditional mean. If the
noise is heavy-tailed or you care about the typical case rather than the average, the loss should change,
not just the model.

## How it works
### The model
ŷ = f_θ(x). The simplest form is linear, ŷ = wᵀx + b, and it is more powerful than it looks because x
can contain transformed features: log(tokens), tokens², an embedding, an indicator for the model tier.
A neural regressor is the same final linear layer applied to learned features. A reward model is an LLM
whose output head produces a single scalar instead of vocabulary logits.

### The loss decides what you estimate
- **Mean squared error**, mean (ŷ − y)². The minimiser is E[y | x], the conditional mean. The gradient
  2(ŷ − y) grows with the error, so a point 10× further away pulls 10× harder; outliers dominate.
- **Mean absolute error**, mean |ŷ − y|. The minimiser is the conditional median. The gradient is ±1
  regardless of distance, so it is robust to outliers, but it is not smooth at zero and converges more
  slowly near the optimum.
- **Huber loss**: quadratic for |ŷ − y| ≤ δ, linear beyond. Smooth near the solution, robust far from it;
  δ is where you decide "this residual is an outlier".
- **Quantile (pinball) loss** estimates a chosen quantile instead of the centre. For a p95 latency
  target you fit the 0.95 quantile directly rather than predicting the mean and guessing the tail.
- **Log-transformed target**: when the quantity is positive and multiplicative (costs, latencies,
  durations), fitting MSE on log y estimates the geometric mean and penalises relative rather than
  absolute error. Remember to evaluate on the original scale.

### Fitting
For a linear model with MSE there is a closed form, the normal equation w = (XᵀX)⁻¹Xᵀy, costing roughly
O(d³) in the number of features plus O(nd²) to build XᵀX. For non-linear models, huge n or huge d, you
use gradient descent instead. Gradient descent on unscaled features is slow or unstable because the
condition number of XᵀX governs convergence, which is why standardising features is the first step, not an
afterthought (see the gradient-descent lesson).

### Regularisation
Ridge adds λ||w||² and shrinks coefficients, which stabilises the fit when features are collinear and
still has a closed form, (XᵀX + λI)⁻¹Xᵀy. Lasso adds λ||w||₁ and drives some coefficients exactly to
zero, which selects features. Weight decay in deep networks is ridge by another name.

### Evaluation
RMSE is in the units of the target and punishes large errors; MAE is easier to explain and robust; R²
is the fraction of variance explained relative to predicting the mean, can be negative on test data, and
is not comparable across datasets with different variance. MAPE breaks on targets near zero. Always
compare against a trivial baseline (predict the training mean, or the last observed value) and look at
the residuals: a fan shape means the noise grows with the prediction, a curve means the model is missing
a non-linearity.

### Where it meets AI engineering
Reward models output a scalar trained from preference pairs; LLM judges emit 1–5 scores that are ordinal
rather than truly continuous; latency and cost predictors drive routing between model tiers; value
functions in reinforcement learning are regressors; token-usage forecasts feed capacity planning.

## Example
A latency model from serving logs, fitted two ways. The closed form recovers the generating
coefficients directly; gradient descent recovers them too, but only after the features are scaled,
because prompt lengths in the thousands make the raw problem badly conditioned.

```python
import numpy as np
rng = np.random.default_rng(0)

# Synthetic serving logs (illustrative): latency ≈ 40 + 0.05*prompt_tokens + 25*output_tokens + noise
n = 5000
prompt = rng.integers(50, 4000, n); out = rng.integers(1, 400, n)
latency = 40 + 0.05 * prompt + 25 * out + rng.normal(0, 30, n)

X = np.column_stack([np.ones(n), prompt, out]).astype(float)   # bias column + 2 features
w_closed = np.linalg.solve(X.T @ X, X.T @ latency)              # normal equation
print("closed form:", np.round(w_closed, 3))                    # ≈ [40, 0.05, 25]

# Gradient descent on MSE: standardise features first, or the 'prompt' axis dominates the curvature
mu, sd = X[:, 1:].mean(0), X[:, 1:].std(0)
Xs = np.column_stack([np.ones(n), (X[:, 1:] - mu) / sd])
w = np.zeros(3)
for _ in range(2000):
    grad = 2 * Xs.T @ (Xs @ w - latency) / n
    w -= 0.1 * grad
print("GD intercept (= mean latency in scaled space):", round(w[0], 1))
print("GD slopes back in original units:", np.round(w[1:] / sd, 3))   # ≈ [0.05, 25]
```

## Trade-offs
- MSE is smooth and estimates the mean, which is what you want when predictions will be summed into a
  total such as monthly cost, but a handful of extreme points can drag the whole fit; switch to MAE or
  Huber when the tail is noise rather than signal.
- A linear model is interpretable, data-efficient and extrapolates predictably, while trees and neural
  regressors capture interactions and curvature at the cost of overfitting and erratic extrapolation;
  the balance tips toward flexible models when n is large and the relationship is clearly non-linear.
- Predicting a single number is simple, but SLO and capacity decisions need a tail quantile or an
  interval, which costs an extra model or a quantile loss.
- Fitting on log(y) gives relative error and positive predictions, but the back-transformed mean is
  biased low; correct for it or report medians when you use it.

## Pitfalls
- Extrapolating beyond the training range; the symptom is confident predictions for 32k-token prompts
  from a model that only ever saw up to 4k, and a sharp error jump at the boundary.
- Leakage through features that are only known after the target, such as using output token count to
  predict latency; the symptom is a near-perfect offline R² and a model that cannot be run at request
  time because the feature does not exist yet.
- Optimising MSE and then reporting MAPE or median error; the symptom is metrics that disagree and a
  model tuned for a quantity nobody reads.
- Averaging ordinal judge scores as if 1–5 were a ratio scale; the symptom is "quality went from 3.2 to
  3.4" claims with no idea whether that gap means anything.
- Ignoring heteroscedastic noise; the symptom is a residual plot that fans out and prediction intervals
  that are far too narrow for large requests.
- Collinear features; the symptom is coefficients that flip sign between retrains while predictions
  barely change, which destroys any story told about feature importance.

## When to use
Use regression when the output is a quantity and the size of the error matters. Do not use it when the
quantity is a probability of an event in disguise (classify, then take an expected value) or when you
need the whole distribution rather than a point (quantile regression or a probabilistic model). Measure
RMSE and MAE against a naive baseline, inspect residuals, and report per-segment error, because an
average hides the segments where routing or capacity decisions actually go wrong. Day to day it appears
in latency and cost predictors for routing, in reward and judge heads, and in usage forecasting. In
interviews, expect "why does MSE estimate the mean", "when would you use MAE or Huber", and the caveats
around R².

## Role lens
- **Engineer:** Builds the scalar head or sklearn regressor, chooses the loss to match the noise, checks
  residual plots, and ships a mean-prediction baseline first so improvements are measurable.
- **Architect:** Decides where a predicted quantity drives a system decision, such as routing to a cheaper
  model or autoscaling on predicted load, and what error tolerance that decision can survive.
- **FDE:** Reconciles a customer's "predict it exactly" expectation with error bars, defines the target from
  their actual tables (resolution time, cost per case) and hunts leakage in their historical data.

## Check yourself
**Q:** You must predict the dollar cost of each request before it runs, to route it between a cheap
and an expensive model. Costs are heavy-tailed: most requests cost cents, a few cost dollars. Which loss
and target transform do you use, and how do you evaluate?
**A:** Costs are positive and multiplicative, so fit on log(cost) with MSE, which penalises relative error
and stops a few dollar-sized requests dominating; Huber on the raw scale is the alternative if the tail
is mostly noise. Features must be available before the request runs (prompt length, user tier, tool
flags), never output length, or the model leaks. Evaluate on the original dollar scale: MAE and RMSE
against a baseline that predicts last week's mean, calibration of predicted total spend against actual
spend (the back-transformed log mean is biased low and needs a correction), and above all the routing
decision quality: what fraction of genuinely expensive requests were sent to the expensive model and how
much cheap traffic was misrouted. Check error separately for the tail, because that is where routing
pays off.

## Quiz
### Q: Which statement about MSE versus MAE is correct?
- [ ] MAE estimates the conditional mean and MSE the conditional median
- [x] MSE's minimiser is the conditional mean and its gradient grows with the error, so outliers pull the fit harder than under MAE
- [ ] MAE is differentiable everywhere and MSE is not
- [ ] For linear models the two losses always produce the same coefficients
> The gradient of a squared residual is proportional to the residual, so distant points dominate and the
> solution moves toward the mean; absolute error has a constant-magnitude gradient and settles on the
> median, which is why it resists outliers.

### Q: A latency regressor reaches R² = 0.98 offline but is useless in production. What is the most likely cause?
- [ ] The learning rate was set too high during training
- [ ] The target should have been standardised before fitting
- [x] A feature such as output token count is only known after the request completes, so the offline fit leaked the answer
- [ ] R² above 0.95 is not achievable on real data, so the evaluation code is wrong
> Output length almost determines latency, so including it yields a near-perfect fit; at request time the
> feature does not exist, so the model cannot be used. Leakage is the first suspect whenever an offline
> score looks too good.

### Q: You need a p95 latency estimate for SLO planning. What is the most defensible approach?
- [ ] Fit MSE and multiply the prediction by 1.645
- [x] Fit a quantile (pinball) loss at the 0.95 level
- [ ] Fit MAE and add the largest residual seen in training
- [ ] Train a classifier for "slow versus fast" and report its threshold
> Mean plus 1.645 standard deviations only works for Gaussian, constant-variance residuals; latency
> residuals are skewed and grow with request size. The pinball loss estimates the 95th percentile
> directly without that assumption.
