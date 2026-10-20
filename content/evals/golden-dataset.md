---
id: golden-dataset
module: evals
name: Golden dataset (eval dataset)
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - evals/model-evaluation
  - evals/task-specific-acceptance-criteria
  - evals/regression-tests
  - evals/inter-annotator-agreement
  - evals/dataset-contamination
  - llmops-platform/golden-dataset-strategy
  - customer-data-evaluation/golden-dataset-creation-with-client-smes
  - training-adaptation/train-val-test-split
---

## TL;DR
A golden dataset is a curated, versioned set of inputs paired with outputs or labels that the people
who own the task have agreed are correct. It is the fixed reference every eval number is computed
against, so without it two prompts, two models or two weeks cannot be compared. "Golden" means
*agreed and authoritative*, not perfect or large: a few hundred well-chosen, well-labeled items beat
ten thousand scraped ones, provided they are stratified across the cases that matter and kept out of
prompts and training data.

## Why it exists
Every eval score is "the fraction of *this set* that passed". If the set changes between runs, the
score is meaningless as a comparison; if the set is not representative, the score is meaningless as a
prediction. Teams that skip building one end up with three substitutes, each of which fails.

"Use production logs directly" gives realistic inputs but no labels, no stable composition (this
week's traffic has a billing spike, last week's did not), and privacy constraints that keep the data
out of CI. "Have the model generate test cases" produces inputs that share the generator's blind
spots and phrasing, and if the same model also writes the expected answers the whole eval is circular:
it measures agreement with itself. "The engineer writes twenty examples" yields a set biased toward
the cases the engineer already handled, and the same engineer then tunes the prompt until those twenty
pass, which is overfitting with extra steps.

A golden dataset solves these by making the reference explicit and shared. Subject-matter experts sign
off on the expected outputs, so disagreement about "is this answer right" happens once, during
labeling, instead of in every review. The set is versioned, so a score is reproducible. And because
every production failure becomes a new item, the set grows toward the failures that actually occur
rather than the ones imagined up front.

## How it works
### What a record contains
Each item needs enough to reproduce the request and to grade the response:

- **Input**, complete: the user message, the system context that matters (retrieved documents or their
  ids, account state, conversation history), and any tool state the system reads.
- **Expected output or label.** For closed tasks a label or exact value; for extraction a set of
  fields; for open-ended tasks a reference answer *plus* a grading spec (rubric, must-contain facts,
  acceptable alternatives), *because* a single reference string would fail correct paraphrases.
- **Grader spec**: which grader applies (exact, JSON-schema, tests, rubric judge) and its parameters.
- **Metadata**: slice tags (intent, language, difficulty, customer segment), provenance (sampled from
  logs on date X, authored by SME Y, synthetic from generator Z), labeler ids, and a version.

Store it as JSONL under version control or in a dataset store with immutable versions. Changing the
dataset changes the metric, so every run must record which dataset version it used.

### Where items come from
Four sources, in rough order of value per item:

1. **Sampled real traffic**, stratified. Production distributions are long-tailed: uniform random
   sampling gives you hundreds of the common easy intent and zero of the rare one that causes
   escalations. Set a quota per slice so each slice has enough items to measure on its own.
2. **Failures from error analysis.** Every bug found in production or review becomes an item. This is
   the highest-signal source *because* it encodes a failure mode you know exists.
3. **SME-authored edge cases**: boundary conditions, ambiguous inputs that should trigger a clarifying
   question, policy exceptions. Experts know where the task is hard.
4. **Synthetic generation** to fill coverage gaps (rare languages, adversarial phrasing), labeled by
   humans rather than by the generator, and tagged as synthetic so you can check whether synthetic and
   real items behave differently.

### Labeling
Write labeling guidelines before labeling; they become the task definition. Double-label a subset and
measure agreement (see inter-annotator agreement). Where experts legitimately disagree, the item is
either ambiguous (fix the guideline or drop it) or multi-answer (store all acceptable answers). Low
agreement among qualified labelers is usually a property of the task definition rather than of the
people (check labeler expertise and training too), and a grader cannot be more reliable than the
labels it is checked against.

### Size and statistics
For a pass rate *p* on *n* items the standard error is √(p(1−p)/n). At p = 0.8: n = 100 gives ±4
points standard error (roughly ±8 at 95%); n = 400 gives ±2; n = 1,000 gives ±1.3. Per-slice
numbers need per-slice *n*, which is why stratification matters more than total size.

The question a dataset must answer is not "how precise is one score" but "can it detect the
improvement I care about". For two *independent* runs at 80% power and α = 0.05 the textbook
approximation is n ≈ (1.96 + 0.84)² · 2p(1−p) / δ² per version: detecting δ = 5 points at p = 0.8
needs about 1,000 items. Paired comparisons on the same items need far fewer, *because* only items
that flip carry information: with a fraction *d* of items discordant between versions,
n ≈ (1.96 + 0.84)² · d / δ². If about 13% of items flip (a typical prompt change; measure yours from
two runs), the same 5-point effect needs roughly 400 items. That arithmetic is why harnesses pair
runs and why "how many items do we need" depends on how much versions disagree, not only on the pass
rate. A workable starting point (illustrative, not a rule) is tens to a hundred items per slice you
need to decide on, grown by every failure found afterwards.

### Splits and hygiene
Keep a *dev* split for iterating on prompts and a *held-out* split you look at only when deciding to
ship, *because* a prompt tuned against the full set has been fitted to it. Never copy golden items
into few-shot examples, fine-tuning data or synthetic-generation prompts; that is contamination of
your own eval, and its symptom is a perfect offline score with unchanged production quality.

### Maintenance
Items go stale when the product or policy changes; labels turn out to be wrong when a "failure" is
inspected and found correct. A set can also saturate: once every candidate passes 97% of it, it no
longer separates good versions from great ones, and the remaining budget belongs on harder items
drawn from recent failures. Treat the dataset like code: changes go through review, versions are
tagged, and a changelog says why an item was added, relabeled or retired. When a version changes,
re-run the baseline on the new version before comparing, *because* a score on v3 and a score on v4
are different metrics.

## Example
A stratified sample from logs with per-intent quotas, a deterministic dev/held-out split by id hash,
and the standard error you should expect from the resulting set. Replace `fetch_logs` with your store.

```python
import hashlib, json, math, random
from collections import defaultdict
from dataclasses import dataclass, asdict, field

@dataclass
class Item:
    id: str; input: dict; expected: dict; grader: str
    slice: dict = field(default_factory=dict)      # {"intent": "billing", "lang": "en"}
    provenance: dict = field(default_factory=dict) # {"source": "logs", "sampled": "2026-10-01"}

def stratified(logs, quota: dict[str, int], seed=0) -> list[Item]:
    by_intent = defaultdict(list)
    for row in logs: by_intent[row["intent"]].append(row)
    rng, out = random.Random(seed), []
    for intent, n in quota.items():                 # a quota per slice, not a global random sample
        for row in rng.sample(by_intent[intent], min(n, len(by_intent[intent]))):
            out.append(Item(id=row["id"], input={"message": row["message"], "doc_ids": row["doc_ids"]},
                            expected={}, grader="rubric_v1",      # expected filled by SMEs, not by a model
                            slice={"intent": intent}, provenance={"source": "logs", "sampled": "2026-10-01"}))
    return out

def split(item: Item) -> str:                       # stable: the same id always lands in the same split
    h = int(hashlib.sha256(item.id.encode()).hexdigest(), 16) % 100
    return "dev" if h < 70 else "heldout"

def stderr(p: float, n: int) -> float:
    return math.sqrt(p * (1 - p) / n)

items = stratified(fetch_logs(days=30), {"billing": 60, "refund": 60, "shipping": 40, "cancel": 40})
with open("golden_v3.jsonl", "w") as f:
    for it in items: f.write(json.dumps({**asdict(it), "split": split(it)}) + "\n")
print(len(items), "items;", f"SE at p=0.8 ≈ {stderr(0.8, len(items)):.3f}")   # 200 items -> 0.028
print(f"per-slice SE for billing (n=60) ≈ {stderr(0.8, 60):.3f}")               # -> 0.052
```

The printed numbers say what the set can and cannot decide: overall differences under about 6 points
are inside the noise, and the billing slice alone cannot resolve anything under about 10 points.

## Trade-offs
- Stratified quotas give every slice measurable *n*, but they distort the overall average away from
  the production mix; report per slice, or weight by traffic when you need a headline number.
- Real-traffic items are realistic but need privacy review and redaction before they can sit in a
  repository; synthetic items avoid that at the cost of sharing the generator's phrasing and blind spots.
- A held-out split protects against overfitting prompts, but it removes a share of the items from
  iteration (30% in the example above); with small sets, rotate the split periodically rather than
  fixing it forever.
- Expert labeling is expensive and slow, but cheap labels (crowd, or the model itself) move the error
  from the system into the reference, where it is harder to notice and impossible to fix by prompting.

## Pitfalls
- Label errors in the reference; the symptom is a pass-rate ceiling below 100% where the "failures"
  turn out, on inspection, to be correct outputs disagreeing with a wrong label.
- A set that is too easy because it was drawn from happy-path traffic; the symptom is 98% offline
  while support tickets keep arriving about cases the set never contained.
- Golden items reused as few-shot examples or fine-tuning data; the symptom is a jump in offline score
  with no movement in production metrics.
- One reference string for an open-ended task; the symptom is correct, differently worded outputs
  failing exact or overlap matching, and a team "fixing" the prompt to mimic the reference wording.
- Never retiring stale items after a product change; the symptom is a stable score that no longer
  tracks what the system is supposed to do, and arguments about whether old failures still count.
- No per-slice quotas; the symptom is a rare but critical intent represented by three items, so its
  pass rate swings between 33% and 100% run to run.
- Comparing a new run on dataset v4 with an old baseline scored on v3; the symptom is a "regression"
  or "improvement" that vanishes when the baseline is re-run on the same version.

## When to use
Build one as soon as a prompt or pipeline is going to be changed more than once, which is always.
Start small and targeted: a few dozen items per slice that matters, each labeled by someone with
authority over the task, with a grader spec per item. Measure its statistical reach with the standard
error before trusting a result from it, and grow it where decisions are tight and where production
failures appear. Do not use it for training or few-shot prompting, and do not let the people tuning
prompts see the held-out split. In interviews, expect to be asked how you would build one for a given
product, how big it must be to detect a given improvement, and how you keep it from leaking into the
system under test.

## Role lens
- **Engineer:** Writes the sampling and split code, keeps the JSONL under version control, and turns
  every production bug into a new item with a grader spec.
- **Architect:** Defines the slices that must be represented, the ownership and review process for
  labels, and the rule that dataset versions are recorded with every eval run.
- **FDE:** Runs labeling sessions with the customer's experts, turns their disagreements into written
  guidelines, and uses the per-slice numbers to agree what "done" means.

## Check yourself
**Q:** You inherit a 2,000-item eval set generated by prompting the production model to write questions
and answers about the company's documents. Offline pass rate is 96%; users complain daily. What is
wrong with the set and how would you rebuild it?
**A:** The set is circular: the model wrote both inputs and references, so it rewards the model's own
phrasing and misses what real users ask, and the "labels" were never checked by anyone with authority.
I would sample real questions from logs with per-intent quotas so rare but costly intents have enough
items, pull every recent complaint in as an item, have subject-matter experts write or verify the
expected answers under written guidelines, double-label a subset to measure agreement, attach a grader
spec per item instead of exact-match on a reference, split into dev and held-out by id hash, and
version the result. A few hundred such items will say more than two thousand synthetic ones.

## Quiz
### Q: Why sample golden items with per-slice quotas rather than uniformly at random from production logs?
- [ ] Uniform sampling violates privacy rules
- [x] Rare but important slices would otherwise have too few items to measure on their own
- [ ] Quotas make the overall pass rate match production
- [ ] Random sampling cannot be reproduced
> Traffic is long-tailed, so a random sample is dominated by the common intent and leaves rare ones
> with a handful of items whose pass rate is pure noise. Quotas trade a distorted average for
> measurable slices; the average can be re-weighted if needed.

### Q: A prompt engineer copies five golden items into the system prompt as few-shot examples. What happens to the eval?
- [ ] Nothing, five items are statistically negligible
- [ ] The eval becomes stricter because the model now knows the format
- [x] The eval is contaminated: those items no longer measure generalization and the score inflates
- [ ] The held-out split absorbs the effect automatically
> Items the model has seen verbatim in its prompt measure recall, not capability, and the effect
> spreads because the prompt is now tuned to the set. The symptom is a higher offline score with no
> change in production quality.

### Q: Two experts disagree on the expected answer for 20% of items in a new golden set. What does this most likely indicate?
- [ ] One expert should be replaced
- [ ] The model will also fail those items, so they are good hard cases
- [x] The task definition or labeling guideline is ambiguous and must be fixed before grading means anything
- [ ] The set needs more items to average out the disagreement
> Disagreement between qualified experts usually points at the task definition, not the people. A
> grader cannot be more reliable than its labels, so the guideline must be sharpened, ambiguous items dropped or made multi-answer, and
> agreement re-measured before the set is used.
