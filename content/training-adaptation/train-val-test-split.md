---
id: train-val-test-split
module: training-adaptation
name: Train / validation / test split
kind: practice
difficulty: intro
est_minutes: 12
status: fact-checked
related:
  - ml-fundamentals/train-test-leakage
  - training-adaptation/data-curation
  - training-adaptation/data-labeling
  - training-adaptation/overfitting
  - training-adaptation/supervised-fine-tuning
  - evals/golden-dataset
  - evals/dataset-contamination
  - llmops-platform/drift
sources:
  - Hastie, Tibshirani & Friedman, 2009 — The Elements of Statistical Learning
  - Kohavi, 1995 — A Study of Cross-Validation and Bootstrap for Accuracy Estimation and Model Selection
  - Official docs: scikit-learn model_selection
---

## TL;DR
Weights are fit on the training set. Every decision made while looking at a score (learning rate, epochs,
which checkpoint to keep) is fit on the validation set. The test set is read once, at the end, to estimate
what the chosen model does on data nobody tuned against. Three sets exist because a score you optimised
against is no longer an unbiased estimate, and the cut must follow the unit the model must generalise
across (conversation, user, document, time), not rows.

## Why it exists
Training minimises loss on the examples it sees, so training loss says nothing about new data; a model
that memorised every row would score perfectly. Held-out data is the only instrument that measures
generalisation. But one held-out set stops being enough the moment you start making decisions with it.
Suppose you try twenty learning-rate and epoch combinations and keep the one with the best validation
score. Each score is true quality plus noise, and picking the maximum of twenty noisy numbers picks the
luckiest noise along with the best model, so the winning score overstates what production will see.
Statisticians call this selection bias; practitioners call it overfitting the validation set. The fix is
the same either way: a second held-out set that no decision ever touched, read once.

The naive recipe, "shuffle the rows and cut 80/20", fails in a second, quieter way: rows are rarely
independent. Four turns of one support conversation, ten chunks of one PDF, fifty events from one user,
last month's and this month's tickets. If the model trains on some of them and is tested on the rest, it
is rewarded for recognising the conversation, document, user or period, and the number you get has no
relationship to the number on a genuinely new one. A split is only meaningful when it is made at the level
on which you expect the model to generalise.

## How it works
### Three roles, not three ratios
- **Train** receives gradient steps. Nothing else is ever fit to it.
- **Validation** (dev) drives every choice made by looking at a number: hyperparameters, early stopping,
  checkpoint selection, decision thresholds, prompt and template variants. You may read it as often as you
  like, and that is exactly why its final score is biased upward.
- **Test** gives the one-shot estimate. If you read it, change something and read it again, it has become
  a second validation set; disciplined teams then carve a fresh one out of newly arrived data.

### How big each set should be
Ratios such as 80/10/10 are inherited from the small-data era. What matters is the absolute size of the
held-out sets, because the standard error of a measured accuracy *p* on *n* examples is about
`sqrt(p·(1−p)/n)`. At p = 0.9 that is ±3 points for n = 100, ±0.95 points for n = 1,000 and ±0.3 points
for n = 10,000. So a 1,000-example test set cannot separate two models one point apart, however large the
training set is. With ten million rows a 1% test set is 100,000 rows, far more than needed; with 2,000
fine-tuning examples a 10% slice is 200 rows with a standard error of about ±2 points, so it cannot
reliably separate two models two points apart. Choose held-out
sizes from the effect you need to detect, then give everything else to training.

### Ways to cut
1. **Random.** Only valid when rows are independent and identically distributed, which is rarer than it
   looks.
2. **Stratified.** Preserve class or category proportions in every split. Essential when a class is rare:
   a random 10% slice of 50 positives can easily contain two of them, and the metric on that class becomes
   a coin flip.
3. **Grouped.** Assign whole groups (conversation, user, document, source repository) to a single split.
   Hashing the group id into a bucket makes the assignment deterministic and stable as new data arrives.
4. **Temporal.** Train on the past, validate on the recent past, test on the newest period. Mandatory when
   the data changes over time and the deployed model will serve the future, which covers most production
   systems; it also exposes drift early.
5. **Cross-validation.** *k* folds rotate the validation role, giving *k* estimates at *k* times the
   training cost. Worth it when data is small and training is cheap (classical models, small heads),
   rarely for LLM fine-tunes.

### LLM-specific rules
- Deduplicate before splitting, including near-duplicates such as one template with different fill-ins;
  otherwise validation is partly a copy of training and overfitting becomes invisible.
- Check held-out sets against any public benchmark you report, and against data your base-model vendor may
  have pretrained on; a contaminated test set measures memory, not skill.
- Split before fitting anything on data: tokenizer training, normalisation statistics, target encodings,
  embedding-based filters. Fitting on the union leaks the held-out distribution into the pipeline.
- Validation loss on the dev set and the task metric on the test set are different instruments. Token-level
  loss is cheap and continuous; the task metric is what the customer sees. Keep both.

## Example
A deterministic, grouped 80/10/10 split for a multi-turn support dataset. Hashing the conversation id
means every turn of a conversation lands in the same split, the assignment never changes when rows are
added, and the result is identical on every machine.

```python
import hashlib
from collections import Counter

def bucket(key: str, buckets: int = 100) -> int:
    # Stable across processes and machines; Python's built-in hash() is salted per run.
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % buckets

def assign(example: dict) -> str:
    # Split on the unit of generalisation: all turns of one conversation travel together.
    b = bucket(example["conversation_id"])
    return "test" if b < 10 else "val" if b < 20 else "train"   # 10 / 10 / 80

rows = [{"conversation_id": f"conv-{i // 4}", "turn": i % 4,
         "label": "billing" if i % 7 == 0 else "other"} for i in range(4000)]
splits = {s: [r for r in rows if assign(r) == s] for s in ("train", "val", "test")}

for name, rs in splits.items():
    convs = {r["conversation_id"] for r in rs}
    print(name, len(rs), "rows", len(convs), "conversations", Counter(r["label"] for r in rs))

# Leakage check: no conversation may appear in two splits.
ids = {s: {r["conversation_id"] for r in rs} for s, rs in splits.items()}
assert not (ids["train"] & ids["val"]) and not (ids["train"] & ids["test"]) and not (ids["val"] & ids["test"])

# A row that arrives next month keeps the split its conversation already has.
assert assign({"conversation_id": "conv-17"}) == assign({"conversation_id": "conv-17"})
```

The `Counter` line is the stratification check: if the `billing` share differs noticeably between splits,
either enlarge the held-out sets or stratify within hash buckets. When there are no groups, scikit-learn's
`train_test_split(..., stratify=y)` does the stratified cut directly.

## Trade-offs
- Larger validation and test sets give tighter estimates but take rows from training; the balance flips
  toward training when the dataset is a small fine-tuning set and a ±2-point error bar is acceptable.
- Grouped and temporal splits report lower numbers than a random split, and the lower number is the honest
  one; the random number was high precisely because it leaked.
- Cross-validation multiplies training cost by *k*; it pays only when the variance of a single split is
  large relative to the differences you need to detect.
- A hash-based split is reproducible and stable as data grows, but cannot be re-stratified after the fact;
  rare classes have to be checked or stratified within buckets.
- Reading the test set once gives an unbiased estimate but teaches you nothing for the next iteration;
  rotating in fresh test sets costs labelling effort but keeps honesty over a long project.

## Pitfalls
- Splitting by row when rows share a source; the symptom is validation accuracy far above production and a
  sharp drop the first time a new customer, document or user arrives.
- Fitting preprocessing (scalers, tokenizers, vocabularies, filters) on all data before splitting; the
  symptom is a small, persistent optimistic bias that vanishes when the pipeline is rebuilt on train only.
- Reusing the test set across iterations; the symptom is a test score that keeps climbing while production
  does not move, because the test set has quietly become a validation set.
- Random splits with a rare class; the symptom is the minority-class metric jumping between 0% and 100%
  across seeds.
- Re-splitting with a new seed on every run; the symptom is incomparable experiments and "improvements"
  that are nothing but split noise.
- Temporal splits with features computed over the whole time window (aggregates, embeddings fit on all
  periods); the symptom is a time-split test score that is still too good to be true.
- Drawing the validation set from the same synthetic template as training; the symptom is near-perfect
  validation alongside poor human evaluation on real prompts.

## When to use
Every supervised project needs the three roles; the decision is how to cut. Reach for a grouped split the
moment any two rows share an origin, and a temporal split whenever data arrives over time and the model
will serve the future. Size held-out sets from the standard-error formula against the smallest difference
you need to detect. Use cross-validation for small classical models, not for LLM fine-tunes where one run
is expensive. Day to day this is the first thing to audit when a model looks too good: who made the split,
on what key, and was anything fit before it. In interviews, "how would you split this data" is really
"what is the unit of generalisation and how would you stop leakage", and a strong answer names the key,
the sizes and the one-shot discipline for the test set.

## Role lens
- **Engineer:** Writes the deterministic split function, the leakage assertion and the stratification
  check into the data pipeline so every run uses the same held-out sets.
- **Architect:** Owns the policy that evaluation sets are versioned, group- or time-keyed, sized to the
  decision they support and refreshed when the test set has been consumed by iteration.
- **FDE:** Builds the held-out set with the customer's SMEs from their most recent data and defends a lower,
  honest number against the inflated one produced by a row-level random split.

## Check yourself
**Q:** You fine-tune a ticket classifier on 40,000 tickets from 3,000 customers with a random 80/10/10
split and get 94% validation accuracy, but the first new customer onboarded sees 78%. What happened, how
would you re-split, and how large should the held-out sets be?
**A:** The random split put tickets from the same customer on both sides, so the model learned
customer-specific signals (product names, signatures, phrasing) and the 94% measured recognition, not
classification. Re-split by hashing the customer id so each customer is entirely in one set, and
consider a temporal cut as well if ticket mix drifts. The honest number will fall toward 78%, which is
the number that matters. Sizing: 4,000 held-out tickets would give a standard error of about 0.5 points
at 90% accuracy if tickets were independent, but tickets from one customer are correlated, so the
effective sample lies somewhere between the ~300 held-out customers and the 4,000 tickets; expect wider
error bars (a customer-level bootstrap measures them) and stratify by category so rare ticket types
appear in every split. Finally, keep the test set untouched until the final
comparison.

## Quiz
### Q: Why keep a separate test set when a validation set already measures held-out performance?
- [ ] The validation set is too small to be statistically meaningful
- [x] Choosing the best of many configurations on validation biases its score upward, so an untouched set is needed for an unbiased estimate
- [ ] Validation data is used for gradient updates after the first epoch
- [ ] Test sets are required to compute the loss function correctly
> Every selection made on validation picks the configuration whose noise was luckiest as well as the best
> model, so the winning validation score overstates generalisation. Validation data is never used for
> gradient updates, and size is a separate concern.

### Q: You are predicting next month's churn from per-user event logs. Which split is appropriate?
- [ ] A random 80/10/10 split over events
- [ ] A stratified split over events by churn label
- [x] A temporal split: build features and train on earlier months, evaluate on a later month
- [ ] Ten-fold cross-validation over events
> The model will be applied to a future month, so evaluation must come after all training data; any
> event-level random split puts a user's later events (and their churn outcome) into training, and
> stratification does not fix that. If the model must also serve users never seen, hold out whole users too.

### Q: Roughly how precisely does a 1,000-example test set measure accuracy near 90%?
- [ ] About ±0.1 percentage points
- [x] About ±1 percentage point
- [ ] About ±5 percentage points
- [ ] About ±10 percentage points
> The standard error is sqrt(0.9 × 0.1 / 1000) ≈ 0.0095, so differences smaller than about one point are
> inside the noise; a tenfold larger set is needed for ±0.3, which is why absolute size matters more than
> the split ratio.
