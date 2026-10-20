---
id: scikit-learn-basics
module: python-for-ai
name: scikit-learn basics
kind: concept
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - ml-fundamentals/train-test-leakage
  - ml-fundamentals/classification
  - ml-fundamentals/precision-recall-f1
  - ml-fundamentals/class-imbalance
  - ml-fundamentals/calibration
  - training-adaptation/train-val-test-split
  - python-for-ai/numpy
  - python-for-ai/pandas
sources:
  - Pedregosa et al., 2011 — Scikit-learn: Machine Learning in Python
  - Buitinck et al., 2013 — API design for machine learning software: experiences from the scikit-learn project
  - Official docs: scikit-learn
---

## TL;DR
scikit-learn is classical machine learning behind one small, consistent interface: every model and
preprocessing step is an estimator with `fit`, then `predict` or `transform`. Pipelines chain them so
preprocessing is learned only from training data, and model-selection tools split, cross-validate and
score honestly. For AI engineers it is the fastest way to build baselines, embedding-based classifiers
and evaluation metrics, often good enough that no LLM call is needed.

## Why it exists
Classical ML needs the same chores in every project: scale features, encode categories, vectorize text,
fit a model, tune hyperparameters, estimate how it will do on unseen data. Before a common library, each
algorithm came with its own calling convention, so swapping logistic regression for a random forest
meant rewriting glue code, and every team rebuilt evaluation loops by hand.

The hand-rolled glue is also where the worst bug in applied ML lives: leakage. Fit a scaler or a TF-IDF
vocabulary on the full dataset, then split, and statistics from the test rows have shaped the features
the model trains on. The reported score is optimistic and the production score is not. Nobody notices
until after launch.

scikit-learn's answer is an API contract more than an algorithm collection. Every component exposes the
same methods, learned state is kept separate from configuration, and composite objects (pipelines,
cross-validators, grid searches) can treat any estimator uniformly. Once preprocessing lives inside a
pipeline, the library refits it per fold automatically, so the honest procedure becomes the easy one.
In AI work this matters because the questions recur: is the LLM router better than a TF-IDF classifier?
Can a logistic regression on embeddings replace a per-request LLM call for intent detection?

## How it works
### The estimator contract
- **Constructor = hyperparameters only.** `LogisticRegression(C=1.0, max_iter=1000)` stores settings
  and touches no data, *because* that lets tools clone an estimator and refit it on new folds.
- **`fit(X, y)` learns state** and returns `self`. Learned attributes end with an underscore (`coef_`,
  `classes_`, `vocabulary_`), so you can tell configuration from fitted state at a glance.
- **Predictors** add `predict`, and usually `predict_proba` or `decision_function`; **transformers** add
  `transform` (and `fit_transform`). `X` is a 2-D array-like of shape `(n_samples, n_features)` (NumPy,
  sparse matrices, or pandas frames); `y` is 1-D.

### Pipelines and column transformers
`Pipeline` (or `make_pipeline`) chains transformers ending in a model, and is itself an estimator.
Calling `fit` fits each step on the training data and transforms it for the next step; `predict` only
transforms. `ColumnTransformer` applies different preprocessing to different columns (one-hot encode
`plan_tier`, scale `tokens_used`, TF-IDF the `text`). Because the whole chain is one object, cross
validation and deployment both use exactly the same preprocessing, and nothing is fit on held-out data.

### Honest evaluation
`train_test_split(..., stratify=y)` keeps class proportions in both splits, which matters with
imbalanced intents. `cross_val_score` or `cross_validate` with `StratifiedKFold` gives a mean and
spread across folds rather than a single lucky split. `GridSearchCV` or `RandomizedSearchCV` tunes
hyperparameters by cross validation on the training set; the test set is touched once, at the end.
Grouped splitters (`GroupKFold`) keep all rows of one customer or conversation on the same side, which
prevents near-duplicate leakage. The concepts themselves (leakage, splits, imbalance) are covered in
depth in the ML fundamentals module; here the point is that the library encodes them.

### Metrics and baselines
`sklearn.metrics` provides `classification_report`, `confusion_matrix`, `f1_score`, `roc_auc_score`,
`precision_recall_curve` and regression metrics, and many LLM eval harnesses call them underneath
rather than reimplementing them. `DummyClassifier` gives the majority-class baseline every result should beat.

### What it is not
It runs on CPU, mostly in memory, with no GPU deep learning. Persisting a model with `joblib` or pickle
ties it to the library version that saved it, and loading untrusted pickles executes code.

## Example
An intent classifier for support tickets, the kind of baseline you build before deciding an LLM router
is needed.

```python
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import make_pipeline

df = pd.read_json("tickets.jsonl", lines=True)          # columns: text, intent
X_tr, X_te, y_tr, y_te = train_test_split(
    df["text"], df["intent"], test_size=0.2, stratify=df["intent"], random_state=42)

pipe = make_pipeline(                                    # vectorizer is refit inside every fold
    TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
    LogisticRegression(max_iter=1000, class_weight="balanced"),
)
cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
f1 = cross_val_score(pipe, X_tr, y_tr, cv=cv, scoring="f1_macro")
base = cross_val_score(DummyClassifier(strategy="most_frequent"), X_tr, y_tr,
                       cv=cv, scoring="f1_macro")
print(f"model macro-F1 {f1.mean():.3f} ± {f1.std():.3f} | baseline {base.mean():.3f}")

pipe.fit(X_tr, y_tr)                                     # final fit on all training data
print(classification_report(y_te, pipe.predict(X_te)))   # test set used once
probs = pipe.predict_proba(["I was charged twice this month"])[0]
print(dict(zip(pipe.classes_, probs.round(2))))
```

Suppose there are 4,000 tickets across 6 intents and `billing` is 40% of them. The dummy baseline then
scores 40% accuracy but a macro-F1 near 0.1, since it gets one class partly right and five wrong, which
is why macro-F1 is the headline metric. If the pipeline reaches a macro-F1 close to what an LLM prompt
achieves on the same test set, the classifier is the cheaper, faster router. Trying precomputed
sentence embeddings instead is a small change: drop `TfidfVectorizer` from the pipeline and pass the
`(n_samples, dim)` embedding matrix as `X`; the splits, scoring and baseline stay identical.

## Trade-offs
- A uniform API makes swapping models trivial, but the abstractions assume in-memory, tabular `X`;
  data beyond RAM or GPU-scale deep learning needs PyTorch or a distributed framework.
- Linear models on TF-IDF or embeddings train in seconds and are easy to inspect, but they cap out on
  tasks needing reasoning or long context, where an LLM or fine-tuned model wins despite cost.
- Cross validation gives a far more honest estimate than one split, at the cost of k times the
  training time; for slow models or huge data, a single well-built validation split is acceptable.
- `class_weight="balanced"` raises recall on rare intents, but it shifts predicted probabilities, so
  scores used as confidence thresholds need recalibration.

## Pitfalls
- Fitting a scaler or vectorizer on all data before splitting; the symptom is cross-validation scores
  clearly higher than performance on genuinely new data.
- Splitting rows randomly when the same customer or conversation appears many times; the symptom is
  near-perfect validation scores that collapse in production, fixed with `GroupKFold`.
- Reporting accuracy on imbalanced classes; the symptom is a model that "scores 90%" while never
  predicting the rare intents that matter.
- Tuning hyperparameters against the test set; the symptom is a test score that keeps creeping up
  across experiments while live metrics stay flat.
- Loading a pickled model with a different scikit-learn version; the symptom is warnings about
  inconsistent versions or subtly different predictions, fixed by pinning versions with the artifact.

## When to use
Use scikit-learn whenever a problem is classification, regression or clustering on features you already
have: TF-IDF text, embeddings, tabular usage data. In AI products that means intent routing, spam or
abuse filters, quality classifiers over LLM outputs, and baselines that any LLM solution must beat on
cost and accuracy. Use its metrics and splitters inside LLM eval harnesses even when the model is an LLM.
Do not use it for training neural networks or for data that does not fit in memory. In interviews,
expect to explain pipelines and leakage, and to justify a baseline before proposing an LLM.

## Role lens
- **Engineer:** Builds pipeline-based baselines and embedding classifiers, evaluates with stratified or
  grouped cross validation, and uses `sklearn.metrics` inside eval code.
- **Architect:** Decides when a cheap classical model should front or replace an LLM call, and sets
  the evaluation protocol every candidate must pass.
- **FDE:** Trains a quick classifier on a customer's labelled tickets to show what is achievable and to
  quantify where an LLM adds value.

## Check yourself
**Q:** A teammate reports 0.97 macro-F1 for a ticket classifier. They TF-IDF-vectorized all 10,000
tickets, then ran `train_test_split`, and many customers filed several near-identical tickets. Do you
trust the number? What do you change?
**A:** No. Two leaks inflate it. The vectorizer learned vocabulary and IDF weights from test tickets,
so features already encode the test set; putting the vectorizer in a `Pipeline` refits it inside each
fold. Worse, random row splits place a customer's near-duplicate tickets on both sides, so the model is
partly memorizing. Use `GroupKFold` or a group-aware split keyed on customer ID, and ideally a
time-based holdout of the most recent tickets. Report macro-F1 against a `DummyClassifier` baseline. A
real score will be lower but will predict production.

## Quiz
### Q: Why does scikit-learn require that constructors only store hyperparameters and never touch data?
- [ ] It makes estimators smaller when pickled
- [x] Tools such as cross validation and grid search can clone an unfitted estimator and refit it on each fold
- [ ] It forces all data to be passed as NumPy arrays
- [ ] It allows models to train on the GPU
> Cloning works by reading constructor parameters and creating a fresh, unfitted copy. If constructors
> learned from data, every fold would inherit state from data it should never have seen.

### Q: What is the main reason to put a `TfidfVectorizer` inside a `Pipeline` before cross validation?
- [ ] Pipelines run the vectorizer faster in parallel
- [ ] Vectorizers cannot be used outside a pipeline
- [x] The vectorizer is then fit only on each fold's training portion, preventing leakage of test vocabulary and IDF statistics
- [ ] Pipelines automatically choose the best n-gram range
> Fitting the vectorizer on all rows lets held-out documents shape the feature space. Inside a pipeline,
> each fold refits it on its own training data, so the validation score reflects truly unseen text.

### Q: Your intent dataset is 85% one class. Which evaluation choice best reveals whether the model helps?
- [ ] Plain accuracy on a random split
- [ ] Training accuracy, since it has the most data
- [ ] Accuracy averaged over unstratified folds
- [x] Macro-F1 with stratified cross validation, compared against a majority-class dummy baseline
> Accuracy rewards predicting the majority class, which already scores 85%. Macro-F1 weights every class
> equally, stratification keeps rare classes in each fold, and the dummy baseline shows the floor to beat.
