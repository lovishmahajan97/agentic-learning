---
id: fine-tuning-pipelines-on-client-data
module: customer-data-evaluation
name: Fine-tuning pipelines on client data
kind: practice
difficulty: advanced
est_minutes: 17
status: reviewed
related:
  - training-adaptation/fine-tune-vs-rag-vs-prompt-decision
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/peft
  - data-architecture/training-data-pipelines
  - data-architecture/model-and-dataset-provenance
  - data-architecture/data-retention-and-deletion-architecture
  - security-governance-compliance/pii-protection-dlp
  - customer-data-evaluation/model-integration
---

## TL;DR
A fine-tuning pipeline on client data is a repeatable, audited path from a customer's records to a
deployed model: permission check, extraction, redaction, cleaning, leakage-safe splits, training
(usually a per-customer adapter), evaluation against the prompted baseline, and registration with full
lineage. It must be a pipeline, not a notebook, because you will have to rerun it: after a base-model
deprecation, a data-deletion request or a new batch of labels.

## Why it exists
Fine-tuning on a customer's data is sometimes the right call: a classification scheme with hundreds of
house-specific labels, a rigid output format, a house writing style, or distilling a large prompted
model into a smaller, cheaper one for their volume. The technical part, running supervised fine-tuning
or LoRA, is the easy part. What makes it hard is that the data belongs to someone else.

Consider what the one-off notebook approach leaves behind. Nobody can say exactly which records trained
the model, so when the customer's legal team asks you to delete a former employee's data, you cannot
tell whether the weights are affected. The train/test split was random by row, so near-duplicate tickets
sit on both sides and the eval looks better than reality. Personal data was not redacted, and the model
can now reproduce a customer's phone number when prompted with the right prefix. The base model gets
deprecated by its provider a year later and nobody can reproduce the training run. And if two customers'
data were combined "because more data helps", one customer's information can surface in the other's
outputs.

Each of these is a reason the work must be an automated pipeline with explicit gates: the same inputs and
config reproduce an equivalent model (bit-identical weights are rare on GPUs because of non-deterministic
kernels, but eval results should match within noise), every artifact has lineage, and data governance is enforced in code rather than
remembered.

## How it works
### 0. Decide it is worth it
Fine-tune only after a strong prompted baseline (with retrieval where knowledge is the issue) has been
measured on the customer's golden set and has plateaued. Fine-tuning is good at *behaviour*: format,
style, label space, and cost/latency via distillation. It is bad at *facts that change*, because facts
baked into weights go stale and cannot be cited; those stay in retrieval.

### 1. Permission gate
Check the contract and data processing agreement: is training permitted, on which data, who owns the
resulting weights or adapter, and where may training run (region, cloud, on-prem)? Record the basis in
the run manifest. If it is not written down, the pipeline should refuse to start.

### 2. Extract and minimise
Pull only the fields the task needs, from a snapshot with a recorded identifier and time, so the run is
reproducible and deletion requests can be checked against it.

### 3. Redact and filter
Detect and mask PII and secrets that the task does not need (names, account numbers, credentials),
because a model trained on them can memorise and regurgitate them. Then deduplicate (exact and near-
duplicate), drop low-quality or mislabelled examples, and balance classes. On customer data, cleaning
often removes a large share of the raw records, which is normal.

### 4. Split without leakage
Split by **group** (customer account, case, document, or time period), not by row. Tickets from one
case share wording; if they land on both sides, the test set measures memorisation. A time-based split
also matches the deployment reality: train on the past, predict the future. Grouping does not catch
everything (the same template email can open many unrelated cases), so after splitting run a
near-duplicate check *across* the boundary (MinHash or embedding similarity above a high threshold) and
move or drop test items that have a near-twin in train. Report how many you removed; a large number
means the dataset is more repetitive than it looked.

### 5. Format and train
Render examples in the base model's exact chat template, with the same system prompt the production
system will use. Prefer a **per-customer adapter** (LoRA or another PEFT method) over full fine-tuning:
adapters are small, cheap to retrain, isolate each customer's data in its own artifact, and can be
deleted when the contract ends. Never pool customers' data unless every customer has agreed.

Where training runs is a separate decision with its own consequences. A provider's hosted fine-tuning
API is the least engineering, but the customer's data goes to that provider (which their data agreement
must allow), the tuned model typically lives only on that provider and cannot be exported, and when the
provider retires the base model you retrain on whatever replaces it. Self-hosted training on an open
model keeps data in the customer's boundary and gives you a portable adapter, at the cost of owning GPUs,
the training stack and serving. If the training targets are outputs of a large prompted model
(distillation), check that model's terms as well: some providers restrict using outputs to train other
models.

### 6. Evaluate against the baseline
Compare the tuned model with the prompted baseline on the customer's held-out golden set and regression
suite, and run general capability and safety checks to catch catastrophic forgetting or loosened refusal
behaviour. Add **memorisation probes**: prompt with prefixes of training records and check whether
redacted-class strings (or planted canary strings) come back.

Also train on nested subsets (say 25%, 50% and 100% of the training groups) and plot the golden-set score
against size. If the curve is still climbing at 100%, more labelled data is the cheapest next gain; if it
flattened at 50%, more data will not help and the ceiling is in the labels, the task definition or the
base model. This one plot answers the customer's most common question, "should we label more?", with
evidence instead of opinion.

### 7. Register with lineage
Register the artifact with the dataset snapshot hash, the record-id manifest, code version, base model
id and version, hyperparameters, eval results and the permission record. That manifest is what lets you
answer "was record X used?" and rerun the pipeline when needed.

### Deletion is retraining
Removing a record's influence from trained weights is not reliably possible today (machine unlearning is
an active research area, not a guarantee). The practical answer to a deletion request is to drop the
record and retrain, which is only cheap if the pipeline is automated and adapters are small. Deletion
also has to reach everything derived from the snapshot: cached tokenised datasets, the golden set if the
record was in it, eval logs that quote it, and old adapter versions still in the registry.

## Example
The leakage-safe split and the manifest step, the two parts most often done wrong. Records are grouped by
case, split by a stable hash of the group key, and the manifest records exactly which ids went where.

```python
import hashlib, json

def bucket(key: str, salt: str = "split-v1") -> float:
    h = hashlib.sha256(f"{salt}:{key}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF            # stable in [0, 1]

def split_and_manifest(records, tenant, snapshot_id, permission_ref, test_frac=0.15):
    assert permission_ref, "no recorded training permission: refusing to run"
    assert all(r["tenant"] == tenant for r in records), "cross-tenant data in batch"
    train, test = [], []
    for r in records:                               # all tickets of one case land together
        (test if bucket(r["case_id"]) < test_frac else train).append(r)
    manifest = {
        "tenant": tenant, "snapshot_id": snapshot_id, "permission_ref": permission_ref,
        "train_ids": sorted(r["id"] for r in train),
        "test_ids": sorted(r["id"] for r in test),
        "train_cases": len({r["case_id"] for r in train}),
        "test_cases": len({r["case_id"] for r in test}),
    }
    blob = json.dumps(manifest, sort_keys=True).encode()
    manifest["manifest_sha256"] = hashlib.sha256(blob).hexdigest()
    return train, test, manifest
```

Illustrative scenario (numbers chosen for the walkthrough, not a benchmark): 40,000 raw support tickets
become 26,000 after deduplication and quality filters. They belong to 9,000 cases, so the 15% bucket puts
roughly 1,350 cases (around 3,900 tickets, since cases vary in size) in test. The cross-boundary
near-duplicate check then finds 210 test tickets with a near-twin in train, mostly auto-generated
"password reset" requests, and drops them. A LoRA adapter on a mid-size open model is compared with the
prompted large-model baseline on the customer's golden set:

- Baseline: 86% label accuracy. Adapter at 25%, 50%, 100% of training cases: 81%, 85%, 87%. The curve is
  still rising, so more labels would likely help.
- The adapter is within noise of the baseline at a fraction of the per-request cost, general capability
  and refusal checks are unchanged, and no canary string comes back in 500 prefix probes.

That combination, not the accuracy number alone, is what moves it to shadow mode. Had the row-split
version been run instead, the same adapter would have scored higher offline, because near-identical
tickets from the same case would sit on both sides, and the gap would have surfaced only in production.

## Trade-offs
- Per-customer adapters isolate data and make deletion and retraining cheap, but cap how much the model
  can learn compared with full fine-tuning; full tuning is worth it only with large data and ownership terms
  that allow it.
- Aggressive redaction lowers memorisation risk but can remove signal the task needs (names in entity
  extraction); keep a field only when the task cannot work without it, and probe for leakage.
- A fine-tuned small model is cheaper and faster than a prompted large one at high volume, but every base-
  model change means retraining; at low volume the prompted model's flexibility usually wins.
- Group or time splits give honest numbers that look worse than random splits; accept the lower number,
  because it is the one production will show.

## Pitfalls
- Changing the split salt or re-extracting a live table between runs; the symptom is a "rerun" whose
  test ids differ from the original manifest, so before-and-after scores are not comparable.
- Splitting by row; the symptom is an excellent offline score followed by a much worse production result
  on new cases.
- Training without a recorded permission basis; the symptom is a legal review that orders the model
  deleted, with no way to prove what it contained.
- Formatting training data in a different template or system prompt from production; the symptom is a
  tuned model that does well in the training harness and inconsistently in the product.
- Skipping memorisation probes; the symptom is a red-team or user finding real customer identifiers in
  generated text.
- Mixing customers' data into one model "for more signal"; the symptom is one customer's terminology or
  records appearing in another's outputs, which is a cross-tenant leak.
- Treating the run as one-off; the symptom is a deprecation notice for the base model and nobody able to
  reproduce the dataset or hyperparameters.

## When to use
Build the pipeline when a prompted baseline has plateaued on a behaviour fine-tuning is good at, the
customer permits training, and volume or latency makes a smaller tuned model pay off. Do not fine-tune to
add knowledge that changes, to fix retrieval failures, or before a golden set exists to prove the gain.
Measure the tuned model against the prompted baseline on held-out groups, plus cost per request,
forgetting and memorisation checks. In interviews, emphasise permission, group splits, adapters for
isolation and the fact that deletion means retraining.

## Role lens
- **Engineer:** Implements extraction, redaction, deduplication, group splits, template-exact formatting,
  adapter training and memorisation probes as one rerunnable pipeline.
- **Architect:** Decides adapter-per-tenant versus shared models, where training runs relative to data
  residency, and the lineage records the registry must hold.
- **FDE:** Secures the written training permission, works with customer SMEs on labels and the golden set,
  and explains to the customer what deletion requests mean for trained models.

## Check yourself
**Q:** Six months after you deliver a fine-tuned adapter, the customer must delete all data about one of
their clients. What do you do, and what would have made it easy?
**A:** First check the run manifest: if none of that client's record ids appear in the training set, the
adapter is unaffected and you document that. If they do, I would not rely on unlearning, because removing a
record's influence from weights is not dependable; I would drop the records from the snapshot, rerun the
pipeline, re-evaluate against the same golden set and replace the adapter, then delete the old one. That is
easy only if the pipeline is automated, inputs are versioned snapshots with id manifests, and the model is
a small per-customer adapter rather than a shared multi-customer model.

## Quiz
### Q: Why should a fine-tuning dataset of support tickets be split by case rather than by individual ticket?
- [ ] Case-level splits produce more training examples
- [x] Tickets in the same case share content, so a row split leaks near-duplicates into the test set and inflates scores
- [ ] Training frameworks require all examples from a case to be in one batch
- [ ] Row-level splits make the dataset impossible to deduplicate
> Related records on both sides of the split let the test measure memorisation. Grouping by case, account
> or time gives a score that reflects new, unseen inputs.

### Q: What is the most dependable way to honour a request to delete records that were used for training?
- [ ] Run a machine unlearning algorithm on the existing weights
- [ ] Add the records to a blocklist in the output guardrail
- [x] Remove the records from the dataset snapshot and retrain from the pipeline
- [ ] Lower the sampling temperature so memorised text is less likely
> Unlearning methods do not yet give reliable guarantees, and output filters hide rather than remove
> influence. Retraining without the records is dependable when the pipeline is automated and reproducible.

### Q: A customer's product catalogue changes weekly. What should fine-tuning on their data be used for?
- [ ] Teaching the model current prices and stock levels
- [ ] Replacing retrieval so the model answers without documents
- [ ] Nothing, since fine-tuning never helps with catalogue tasks
- [x] Teaching output format, tone or label scheme, while retrieval supplies the changing catalogue facts
> Facts in weights go stale and cannot be cited. Fine-tuning shapes stable behaviour; changing knowledge
> belongs in retrieval, where it can be updated and grounded.
