---
id: feature-stores
module: data-architecture
name: Feature stores (hybrid ML only)
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - ml-fundamentals/train-test-leakage
  - data-architecture/training-data-pipelines
  - data-architecture/lakehouse
  - data-architecture/data-contracts
  - data-architecture/data-lineage
  - llmops-platform/drift
  - llmops-platform/model-registry
  - system-design/data-consistency
sources:
  - Official docs: Feast
---

## TL;DR
Caveat first: feature stores matter only in hybrid systems that train and serve classical ML models
(ranking, fraud, churn, routing classifiers) alongside LLM components; pure LLM-application roles can skim
this and move on. A feature store is the layer that computes model input features once, serves the same
values to training (historically, point-in-time correct) and to inference (now, at low latency), and
versions their definitions, because training/serving skew and time leakage are the two bugs that quietly
ruin tabular models.

## Why it exists
A fraud model needs "number of transactions by this card in the last hour" and "customer's average order
value over 90 days". Those features are computed in two places: a batch SQL job that builds the training
set from the warehouse, and a service that computes them at request time from a cache or production
database. The two implementations drift. The batch job counts refunds; the service does not. The batch job
uses UTC day boundaries; the service uses local time. The model learns from one definition and is scored
with another, and accuracy in production is lower than in evaluation for reasons nobody can see. This is
**training/serving skew**, and it is the first problem a feature store removes by making the definition
singular.

The second problem is subtler. When you build a training set by joining labels to features, the join
must use the feature values *as they were at the moment each label's event happened*. Join to today's
values and the model learns from the future: the "90-day average" for a transaction last March includes
orders from April. Offline metrics look excellent, production is worse, and the gap is **time leakage**.
Doing this join correctly by hand across dozens of features with different update cadences is where most
hand-rolled pipelines go wrong.

The naive alternative is a shared feature library plus a cache. It fixes definition drift but not
leakage, does not store history, and gives no answer to "which feature version did model v12 train on".
The feature store adds a historical store, point-in-time joins, versioned definitions and an online
serving path, which is the minimum set that solves both problems.

## How it works
### Entities, features and feature views
A feature is a named, typed value attached to an **entity** (a customer id, a card id, a merchant id)
with an **event timestamp** saying when the value became true. Features are grouped into **feature
views** (Feast's term; other systems say feature groups or tables): a set of features for one entity
computed by one transformation from one source, with a TTL that says how long a value stays valid. The
definition lives in code under version control, *because* it must be the single source of truth for
both the batch and the serving path.

### The offline store
Historical feature values, keyed by entity and event timestamp, in the warehouse or lakehouse. It is
append-only by design: a new value for a customer is a new row with a later timestamp, not an overwrite,
*because* training needs every past value to reconstruct what was known at any given moment.

### The point-in-time join
Given a label table with `(entity_id, label_timestamp, label)`, the training set is built by, for each
row, selecting from each feature view the latest value with `event_timestamp <= label_timestamp` and
`label_timestamp - event_timestamp <= ttl`. If no value qualifies, the feature is null, which is the
honest answer: at that time the model would not have known it. This "as-of" join is the core algorithm
and is the reason the offline store keeps history. Feast exposes it as `get_historical_features`.

### The boundary must match what serving sees
The `<=` in the join is a decision, not a detail. Suppose the feature is "transactions by this card in
the last hour" and the label is attached to a transaction at 14:00:00. If the feature pipeline has
already counted that transaction in a value stamped 14:00:00, an inclusive join hands the model a count
that includes the very event being scored. At serving time the scorer runs *before* the transaction is
recorded, so it never sees itself in the count. The training and serving views then differ by exactly
one, systematically, which is leakage that looks like a rounding error. Choose strict or inclusive
comparison, and any allowance for ingestion delay, to reproduce what the online path can know at
decision time.

### Backfills
A new feature is useless for training until it has history. A *backfill* runs the feature's
transformation over historical source data to populate the offline store as if the feature had always
existed, which is possible only if the sources are themselves historical (an event log or a snapshot
table). If the feature reads a mutable production table that keeps only current state, history cannot be
reconstructed; the only honest options are to start logging the feature now and wait until enough
labelled history accumulates, or to accept a training set where the feature is null before its launch
date. This is why feature sources should be append-only events wherever possible.

### The online store
A low-latency key-value store (Redis, DynamoDB, Bigtable and similar) holding only the latest value per
entity per feature view. Serving reads it with a single lookup per entity. It is populated by
**materialization**: a scheduled job copies the latest values from the offline store into the online
store, so both paths see the same computed numbers. For features that must be fresher than the
materialization cadence (counts in the last minute), a streaming path writes to both stores from the
event stream.

### Why one definition serves both paths
The transformation is written once and executed in the batch engine for history and in the materializer
or stream processor for serving. Skew is prevented structurally rather than by code review, *because*
there is no second implementation to drift. Where a transformation depends on the request payload, two
options exist: run the same transformation code in both paths at request time and at training time
(Feast's on-demand feature views work this way), or log the values actually served so they can be reused
as training features later, closing the loop. Logging is the safer of the two when the request-time
inputs cannot be reconstructed from history.

### Versioning and lineage
Feature view definitions are versioned; a model's training run records which versions and which offline
snapshot it used, which plugs directly into provenance. Changing a feature's semantics means a new
version, and both versions can be served during a model migration. Feature statistics (null rates,
distributions) computed at materialization are what drift monitoring compares against.

### Where it sits in a hybrid AI system
Typical hybrid shapes: a classical ranking or routing model decides which documents or which LLM an
agent should use; a fraud or risk score gates whether an LLM-generated action executes; a churn model
decides which users get an LLM-drafted outreach. The features for those models come from a feature store;
the LLM gets the score or the decision, not the features. Embeddings also appear as features in these
models (a user or item embedding fed to a ranker); the embedding model's version is then part of the
feature view's definition, so changing it is a new feature version and a retrain, for the same reason it
forces a re-index in retrieval. If your system has none of these models, the
analogous concerns (consistent metadata between ingestion and query time, versioned transformations)
are handled by the ingestion contract and embedding versioning, not by a feature store.

### Products
Feast is the common open-source option (definitions in Python, pluggable offline and online stores).
Managed or commercial options include Tecton, the Databricks feature store, Amazon SageMaker Feature
Store, Vertex AI Feature Store and Hopsworks; this market consolidates and renames products often, so
check current offerings. The differences are mostly in streaming support, transformation execution and
how tightly they integrate with a given platform; the concepts above are shared.

## Example
A point-in-time join in pure Python, contrasted with the leaking "join to the latest value" that
hand-rolled pipelines usually do. Feature rows are `(entity, event_time, value)`; labels are
`(entity, label_time)`.

```python
from bisect import bisect_right
from collections import defaultdict

# avg_order_value_90d for customer c1, appended as it changes (offline store rows)
features = [("c1", "2024-03-01", 40.0), ("c1", "2024-04-15", 55.0), ("c1", "2024-05-20", 90.0)]
labels   = [("c1", "2024-03-10"), ("c1", "2024-04-20"), ("c1", "2024-06-01")]
TTL_DAYS = 120

by_entity = defaultdict(list)
for ent, ts, val in sorted(features, key=lambda r: r[1]):
    by_entity[ent].append((ts, val))

def days_between(a, b):                     # ISO dates, good enough for the demo
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days

def point_in_time(ent, label_ts):
    rows = by_entity[ent]
    i = bisect_right([ts for ts, _ in rows], label_ts) - 1      # latest event_time <= label_ts
    if i < 0 or days_between(rows[i][0], label_ts) > TTL_DAYS:
        return None                                              # unknown at that time
    return rows[i][1]

def leaky_latest(ent, _label_ts):
    return by_entity[ent][-1][1]                                 # what a naive join does

for ent, lts in labels:
    print(lts, "correct:", point_in_time(ent, lts), "| leaky:", leaky_latest(ent, lts))
# 2024-03-10 correct: 40.0 | leaky: 90.0
# 2024-04-20 correct: 55.0 | leaky: 90.0
# 2024-06-01 correct: 90.0 | leaky: 90.0
```

The March label trained with the leaky join would see a value that only became true in May. A model fit
on such rows learns relationships that cannot exist at inference time, which is exactly why offline
metrics overstate production performance.

## Trade-offs
- A feature store removes skew and leakage by construction, but it is another platform to run and a new
  workflow for data scientists; below a handful of models, a shared transformation library plus a
  careful as-of join in SQL is a reasonable interim.
- Materializing to an online store gives millisecond lookups but bounds freshness by the materialization
  cadence; features that must reflect the last few seconds need the streaming path, which costs more
  to operate.
- Append-only history makes point-in-time joins possible but grows storage without bound; partition by
  time and expire beyond the longest TTL any model needs.
- Managed stores integrate tightly with their platform and reduce operational work, but lock the feature
  layer to that platform; Feast keeps portability at the cost of assembling the pieces yourself.
- Logging served features for later training closes the loop on online-only transformations, but adds a
  write per prediction to a path that was read-only; make it asynchronous and sample when traffic is high.

## Pitfalls
- Joining labels to current feature values; the symptom is offline evaluation far better than production,
  with no bug anywhere in the model code.
- Two implementations of the same feature for batch and serving; the symptom is a production accuracy
  drop that disappears when the serving values are logged and compared against the batch ones.
- Setting no TTL on a feature view; the symptom is a model happily consuming a value computed a year ago
  for a dormant entity as if it were current.
- Mixing event time and ingestion time; the symptom is subtle leakage when late-arriving events get
  stamped with when they were loaded rather than when they happened.
- Adopting a feature store for an LLM-only application; the symptom is an expensive platform holding
  document metadata that the ingestion pipeline already handled.
- Not recording feature view versions in the model's provenance; the symptom is being unable to tell
  why model v12 and v13 disagree after a feature definition changed.
- An inclusive as-of join on a feature that counts the label event itself; the symptom is a feature that
  is suspiciously predictive offline and whose logged serving values sit consistently one below the
  training values.

## When to use
Reach for a feature store when the system trains and serves more than a couple of classical models that
share features, when any feature is computed from time-varying history, or when training/serving skew
has already been observed. Do not reach for it when the only models are LLMs consuming retrieved text;
there the equivalent discipline lives in ingestion contracts and embedding versioning. Measure: the gap
between offline and online metrics (skew or leakage shows up here first), feature freshness at serve
time, and the fraction of features with a single definition. In interviews for hybrid roles, expect to
explain the point-in-time join and why the online and offline stores must be fed from one definition;
for LLM-application roles, expect at most to say why you do not need one.

## Role lens
- **Engineer:** Writes feature views as code, implements the as-of join or uses the store's historical
  retrieval, and sets materialization schedules and TTLs per feature.
- **Architect:** Decides whether the system is hybrid enough to justify a feature store, picks managed
  versus open source, and owns the freshness and consistency guarantees between offline and online paths.
- **FDE:** Recognizes when a customer's "AI system" includes classical models with hand-rolled feature
  pipelines, and diagnoses skew and leakage before attributing problems to the LLM.

## Check yourself
**Q:** A customer's fraud model shows 0.95 AUC offline and behaves like 0.80 in production. They blame the
model and want to add an LLM to "explain" transactions. What do you check first, and why?
**A:** Check for time leakage and training/serving skew before touching the model. First, inspect how the
training set was built: if labels were joined to current feature values rather than values as of each
transaction's timestamp, the offline score is inflated by information from the future; rebuild with a
point-in-time join and re-evaluate. Second, log the feature values the serving path actually sends and
compare them with the batch-computed values for the same entities and timestamps; any systematic
difference (refund handling, timezone boundaries, null defaults) is skew, and the model is being scored on
inputs it never saw in training. Both are fixed by a single feature definition feeding an offline store
with history and an online store by materialization, which is what a feature store provides. An LLM
explanation layer would add cost and would explain decisions made on wrong inputs; it belongs, if at all,
after the inputs are right.

## Quiz
### Q: What does a point-in-time join guarantee when building a training set?
- [ ] Each label is matched to the most recently computed feature value
- [x] Each label sees only feature values whose event time is at or before the label's timestamp
- [ ] Features are computed in the same timezone as the labels
- [ ] Every label has a non-null value for every feature
> The join reconstructs what was knowable when each label event occurred. Matching to the latest value
> leaks the future into training, and nulls are the correct outcome when no value existed yet or the TTL
> had expired.

### Q: Why does a feature store keep separate offline and online stores rather than one database?
- [ ] Offline stores are cheaper, so all data should live there
- [ ] Online stores can run SQL joins faster
- [x] Training needs full history for as-of joins while serving needs millisecond lookups of only the latest values
- [ ] Regulations require training data to be isolated from production
> The two access patterns conflict: history and heavy scans versus single-key latency. Materialization
> copies the latest values from the historical store to the key-value store so both paths see the same
> computed numbers.

### Q: A team building a pure RAG assistant asks whether they need a feature store for document metadata. What is the right guidance?
- [ ] Yes, because metadata filters are features of the retrieval model
- [ ] Yes, because the online store will speed up metadata lookups
- [x] No; without classical models to train and serve, the ingestion contract and embedding versioning cover their needs
- [ ] Only if they use more than one embedding model
> Feature stores solve training/serving skew and time leakage for models trained on tabular features.
> A RAG assistant has no such model; consistency between ingestion and query metadata is an ingestion
> contract concern, and transformation versioning is handled by the embedding recipe.
