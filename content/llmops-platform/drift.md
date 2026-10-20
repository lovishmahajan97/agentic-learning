---
id: drift
module: llmops-platform
name: Drift
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - llmops-platform/model-monitoring
  - llmops-platform/golden-dataset-strategy
  - llmops-platform/model-lifecycle-and-deprecation-policy
  - llmops-platform/rollback
  - data-architecture/embedding-versioning-re-indexing-strategy
  - evals/judge-calibration-and-bias
  - observability/monitoring
  - customer-data-evaluation/production-feedback-classification
sources:
  - Gama et al., 2014 — A Survey on Concept Drift Adaptation
  - Quiñonero-Candela et al., 2009 — Dataset Shift in Machine Learning
  - Lopez-Paz & Oquab, 2017 — Revisiting Classifier Two-Sample Tests
  - Chen, Zaharia & Zou, 2023 — How Is ChatGPT's Behavior Changing over Time?
---

## TL;DR
A system validated on one distribution is deployed into a world that keeps moving. Drift is any change
between the conditions you validated under and the ones you now serve: inputs change (data drift), the
right answer for an input changes (concept drift), or, specific to LLM applications, the model, prompt,
corpus, embedder or judge changes underneath an unchanged system. Detection compares traffic windows
against a reference; the decision is always whether the shift changed outcomes, and what to re-validate.

## Why it exists
Every evaluation is a snapshot. A golden set, a canary and an offline benchmark all say "the system
behaved acceptably on *this* distribution at *this* time". Nothing in that statement survives a change
in either variable. Users start asking about a product launched after the corpus was indexed. A new
customer segment writes in a language the prompt was never tuned on. A regulation changes, so the
correct answer to "can I return this?" flips while the question text does not. The vendor retrains the
model behind the alias you call. Each of these quietly moves production away from the thing you
validated, and none of them raises an error.

The naive responses fail in predictable ways. "We'll re-run the golden set weekly" catches regressions
on old questions, not failures on new ones, because the golden set is frozen on the old distribution.
"We'll watch the quality metric" is right in spirit but late in practice: judged quality needs enough
samples to be significant and labels arrive days later, so a drift that began on Monday shows up as a
quality alert the following week. "We'll retrain or re-index on a schedule" is a cost with no
diagnosis: it fixes some drifts, cannot fix others (a prompt that is now wrong), and tells you nothing
about which one you had.

Drift as a concept exists to separate three questions that get conflated: *what* moved (inputs,
outcomes, or a component of the system), *how* it moved (abrupt, gradual, seasonal) and *whether it
matters* (did the shift change the error rate on the slice it affected). Only the third question
justifies action, but you cannot answer it without the first two.

## How it works
### The statistical vocabulary
Treat the system as a function from inputs **X** to outputs **Y**, validated on data drawn from a
joint distribution P(X, Y). Drift is any change in that joint distribution, and it is useful to name
which factor moved:

- **Covariate shift (data / input drift):** P(X) changes, P(Y|X) does not. The questions are different,
  but each question still has the same right answer. A support bot starts receiving shipping
  questions instead of billing questions. The system may still be correct; it may also now be
  operating in a region it was never validated on.
- **Prior / label shift:** P(Y) changes while P(X|Y) stays put. The mix of correct outcomes moves. A fraud classifier sees
  fraud go from 1% to 3% of traffic; thresholds tuned for the old base rate are now wrong even if the
  model's scores are unchanged.
- **Concept drift:** P(Y|X) changes. The same input now has a different correct output. Policy
  changes, price changes, a new law, a renamed product. This is the dangerous one because it is
  invisible in the inputs: the questions look identical, and only ground truth reveals the change.
- **Prediction / output drift:** the distribution of what the system *emits* changes. Observable on
  every request without labels, which makes it the cheapest early-warning signal, but ambiguous: it
  happens under any of the three above and also when the system itself changes.

For a generative system Y is free text, so read it as "the correct behaviour": label shift then means,
for example, that the share of requests whose right response is "escalate to a human" or "refuse" has
moved, which matters because prompts and thresholds were tuned for the old mix.

Drift also has a shape (the Gama et al. taxonomy): **sudden** (a release, a provider update),
**gradual** (new segment ramping up), **incremental** (slow vocabulary change) and **recurring**
(seasonality: tax season, Black Friday). The shape decides the detector: a sudden shift needs a short
window and a change-point test; a gradual one needs a long window and a trend test; a recurring one
needs a baseline from the same season, not last week.

### What drifts in an LLM application that did not drift in classic ML
In classic ML the model was yours and frozen; only the world moved. In LLM systems the components move
too, and each is a separate drift source with its own detector and fix:

- **Upstream model drift:** a provider updates the model behind an alias, or changes a default. Same
  prompt, different behaviour across *all* tenants at one timestamp. Detected by proxy metrics
  shifting globally at once and by the response's reported model id. Fixed by pinning snapshots and
  treating upgrades as releases.
- **Prompt and configuration drift:** someone edits the system prompt, a tool description, or a
  temperature without going through the release path. Detected by hashing the resolved bundle on every
  request and alerting when the hash changes without a deploy.
- **Knowledge / corpus drift:** the RAG index falls behind the world (stale prices, retired policies).
  Inputs and model are unchanged; answers are confidently out of date. Detected by document age at
  retrieval time and by groundedness checks against the *current* source of truth, not the index.
- **Embedding drift:** the embedder is upgraded but the index was built with the old one; query and
  document vectors no longer live in the same space. Detected by a collapse in retrieval scores on a
  fixed probe set. Fixed by versioning embeddings with the index and re-indexing together.
- **Judge drift:** the LLM-as-a-judge is itself an alias that gets updated; every quality series steps
  on the day the judge changed. Detected by re-scoring a fixed, human-labelled calibration set on a
  schedule.
- **Feedback-loop drift:** users adapt to the system (they learn which phrasings work) and the
  system's outputs enter its own corpus or training data. Slow and self-reinforcing; detected only by
  comparing against held-out, never-served data.

### Detecting it
Detection is always a comparison between a **reference window** (the traffic you validated on, or the
same period last cycle) and a **current window**, per feature and per slice:

1. Choose the signal. For categorical features (intent, language, tenant, tool called, finish reason)
   use the bucket distribution. For numeric ones (input length, retrieval score, latency) bucket them
   or use the empirical CDF. For free text, embed it and compare the embedding clouds.
2. Pick a statistic. **PSI** (population stability index) for bucketed distributions:
   `PSI = Σ_b (cur_b − ref_b) · ln(cur_b / ref_b)`, a symmetric, interpretable number where 0 means
   identical. The credit-risk rule of thumb treats under 0.1 as stable, 0.1–0.25 as worth a look, and
   above 0.25 as a real shift; those cut-offs are convention, not theory, and should be re-derived from
   your own history. **KS test** for continuous features. **Jensen–Shannon divergence** when you want
   a bounded, symmetric measure. For embeddings, the **classifier two-sample test**: train a classifier
   to tell reference from current; an AUC near 0.5 means indistinguishable, and the classifier's most
   confident "current" examples show *what* moved, which a single divergence number never does.
3. Control for noise. Require minimum window sizes, compare like with like (same weekday, same
   season), and slice by tenant and intent, because a global PSI of 0.03 can hide one tenant at 0.6.
4. Separate "the world moved" from "the system moved". Re-run a **frozen probe set** (a fixed slice of
   the golden set with known-good outputs) on a schedule and whenever an alert fires. It is a control:
   its inputs never change, so if its scores move, a component changed (model, prompt, embedder, judge);
   if it is stable while production metrics move, the world changed. This one check halves most drift
   investigations.
5. Separate "moved" from "broke". A detected shift raises priority for the slow signals: route more of
   that slice to judging and human review, and check whether error rate on it changed. Input drift
   with stable outcomes is information, not an incident. Concept drift can only be confirmed by
   labels, so the detector's job is to decide where to spend them.

### Responding
Each drift type has a different fix, which is why diagnosis comes first: new inputs → extend the
golden set and prompt coverage; concept drift → update the corpus, policy text or labels, then
re-validate; upstream model change → pin, evaluate, canary; embedding mismatch → re-index; judge shift
→ re-calibrate and pin. Retraining is the answer to one of these, not to all of them. Whatever the
fix, the reference window moves forward afterwards, because the newly validated distribution is the
new baseline.

## Example
Two detectors on one week of support-assistant traffic: PSI on the intent mix, and a classifier
two-sample test on input embeddings. The intent shift is real but moderate; the embedding test on a
synthetic shift shows what a clearly separable week looks like (AUC ≈ 0.87–0.88) versus an unshifted
one (≈ 0.5 on the same code, give or take cross-validation noise).

```python
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score

def psi(expected, actual, eps=1e-6):
    """Population Stability Index between two bucket distributions (fractions summing to 1)."""
    e, a = np.asarray(expected) + eps, np.asarray(actual) + eps
    return float(np.sum((a - e) * np.log(a / e)))

# Intent mix: reference week vs this week (fractions of requests)
ref = [0.30, 0.25, 0.20, 0.15, 0.10]      # billing, shipping, returns, account, other
cur = [0.22, 0.20, 0.33, 0.15, 0.10]
print(f"intent PSI = {psi(ref, cur):.3f}")  # 0.101: 'returns' rose from 20% to 33%; look at that slice

def embedding_drift_auc(ref_emb, cur_emb):
    """Classifier two-sample test: can a model tell this week's inputs from the reference week's?"""
    X = np.vstack([ref_emb, cur_emb])
    y = np.r_[np.zeros(len(ref_emb)), np.ones(len(cur_emb))]
    clf = LogisticRegression(max_iter=1000)
    return cross_val_score(clf, X, y, cv=5, scoring="roc_auc").mean()
# AUC 0.5 = indistinguishable. Use a few thousand embeddings per window, then inspect the inputs the
# classifier is most confident are 'current' to see what actually changed.

rng = np.random.default_rng(0)
ref_emb = rng.normal(0, 1, (2000, 64))
cur_emb = rng.normal(0, 1, (2000, 64)); cur_emb[:, :8] += 0.6   # shift along a few directions
print(f"embedding drift AUC = {embedding_drift_auc(ref_emb, cur_emb):.2f}")   # ~0.87-0.88
# Ceiling for a linear separator: Phi(0.6*sqrt(8)/sqrt(2)) = Phi(1.2) ~ 0.885
```

Reading the PSI by hand: the returns bucket contributes (0.33 − 0.20) · ln(1.65) ≈ 0.065, billing
(0.22 − 0.30) · ln(0.733) ≈ 0.025, shipping ≈ 0.011, the two unchanged buckets 0. The total of ≈ 0.10
says the mix moved noticeably; whether it matters depends on whether the returns slice, now a third of
traffic, was well covered by the prompt and golden set.

## Trade-offs
- Label-free detectors (PSI, embedding tests, output proxies) are cheap and immediate but cannot see
  concept drift; the moment ground truth lags by weeks, you need both, and the detectors decide where
  the scarce labels go.
- Short windows catch sudden shifts fast but alert on noise; long windows are stable but late. Running
  both, with different thresholds, is usually cheaper than tuning one window to do both jobs.
- Fine slicing exposes per-tenant drift that aggregates hide, yet each slice shrinks the sample and
  multiplies alert series; beyond a few dozen slices, rank slices by drift score rather than alerting
  on each.
- Pinning every component (model snapshot, embedder, judge) makes drift attributable and removes a
  whole class of surprises, at the price of a scheduled upgrade burden and the risk of running on a
  model the provider is about to retire.
- Moving the reference window forward after each validated change keeps detectors relevant, but if
  you move it without validating you quietly normalise the drift you were supposed to catch.

## Pitfalls
- Treating a drift alert as a defect; the symptom is a team retraining or re-prompting in response to
  an input shift whose outcomes were fine, and a worse system afterwards.
- Comparing this week against last week during seasonal traffic; the symptom is a recurring alert
  every Monday or every month-end that everyone has learned to ignore, which then masks a real shift.
- Monitoring inputs and outputs but not the components; the symptom is a global step change in every
  metric with no deploy in the log, traced days later to a provider alias update or a judge upgrade.
- Upgrading the embedding model without re-indexing; the symptom is retrieval scores collapsing on
  the fixed probe set and answers that cite irrelevant documents, while the model itself is unchanged.
- Letting the golden set stay frozen while traffic moves; the symptom is a green eval suite alongside
  rising complaints from a segment that has no representation in it.
- Running a PSI with empty buckets and no smoothing; the symptom is infinite or NaN scores that crash
  the job or get silently clipped, hiding exactly the new categories that signal drift.

## When to use
Drift detection belongs in any system that will run for longer than its validation data stays
representative, which is every production system. Start with the cheap signals on day one: intent and
language mix, input length, output proxies, and a hash of the resolved bundle so configuration drift
is caught instantly. Add embedding-based input drift once you have a few thousand requests per window,
and judge re-calibration once you rely on an LLM judge. Measure drift per slice and feed each
confirmed shift back into the golden set, because the shift that moved production is exactly the
distribution your next evaluation must contain. In interviews, the expected answer distinguishes
covariate shift from concept drift, explains why the LLM-specific sources (model, prompt, corpus,
embedder, judge) need pinning and hashing, and insists that a detected shift is a question about
outcomes, not an answer.

## Role lens
- **Engineer:** Implements the window comparisons, bundle hashing and slice-level PSI jobs, and wires a
  drift score into the sampler so shifted slices get judged and reviewed first.
- **Architect:** Decides which components are pinned, how reference windows are chosen and advanced,
  and the policy that turns a confirmed shift into a re-validation and a golden-set update.
- **FDE:** Spots customer-side drift first: a new product line, a reorganised knowledge base or a
  changed policy, and gets it into the corpus and the regression suite before the complaints arrive.

## Check yourself
**Q:** A claims-extraction system's accuracy on its monthly human-reviewed sample fell from 94% to 87%.
Input-length and field-mix PSIs are below 0.05, and no deploy happened. Walk through how you would find
the cause and what you would do for each possibility.
**A:** First confirm the drop is real: on a review sample of a few hundred cases, seven points is
well outside sampling noise, but on fifty it may not be. Stable input statistics then rule out the
obvious covariate shift, so look at the components and at concept drift. The fastest split is to
re-run the frozen probe set: if it also dropped, the system changed even though nobody deployed; if it
held, the world changed. Check the model id reported in responses and the bundle hash per request: if the
provider alias moved, every tenant's proxies will have stepped at one timestamp, and the fix is to pin
the previous snapshot, evaluate the new one and canary it. Check the judge or review process: if the
reviewers' guidelines changed, the "drop" is a measurement change. Then test for concept drift: pull
the failed cases and see whether the correct extraction changed, for example a new claim form layout
or a renamed field; inputs can look statistically identical while the mapping changed. That fix is
prompt and golden-set updates plus re-validation. Finally run the classifier two-sample test on
document embeddings, because a layout change can be invisible to length and field-mix buckets while
being obvious in embedding space.

## Quiz
### Q: Which kind of drift cannot be detected by any statistic computed on inputs and outputs alone?
- [ ] Covariate shift, because input distributions are high-dimensional
- [ ] Prediction drift, because outputs are free text
- [x] Concept drift, because the inputs and outputs can look identical while the correct output has changed
- [ ] Upstream model drift, because the provider does not announce it
> Concept drift is a change in P(Y|X): the same question now has a different right answer. Only
> labels or ground truth can reveal it. Input, output and model-id signals all detect the other types.

### Q: An intent-mix PSI of 0.10 is reported for one tenant. What is the correct next step?
- [ ] Roll back to the previous bundle immediately
- [ ] Retrain or re-prompt, since the distribution has moved
- [x] Route that tenant's slice to more judging and review to learn whether outcomes changed
- [ ] Raise the threshold, since 0.10 is within normal variation for all systems
> PSI says the inputs moved, not that the system is wrong. A shift of this size justifies spending
> labels on that slice; action follows only if the error rate on the shifted traffic has changed.

### Q: Why does the classifier two-sample test on embeddings add something a divergence score does not?
- [ ] It is the only test that works on categorical data
- [x] The classifier's most confident "current" examples show what moved, not just that something moved
- [ ] It needs no reference window
- [ ] It detects concept drift without labels
> A single divergence number says how far apart two windows are. A trained discriminator can be
> inspected: the inputs it is surest belong to the new window are the new kind of traffic, which is
> what you need to extend the golden set and prompt.
