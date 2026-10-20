---
id: data-labeling
module: training-adaptation
name: Data labeling
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - training-adaptation/data-curation
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/synthetic-data-generation
  - training-adaptation/rlhf
  - training-adaptation/train-val-test-split
  - evals/inter-annotator-agreement
  - evals/rubrics
  - evals/golden-dataset
sources:
  - Cohen, 1960 — A Coefficient of Agreement for Nominal Scales
  - Landis & Koch, 1977 — The Measurement of Observer Agreement for Categorical Data
  - Ouyang et al., 2022 — Training language models to follow instructions with human feedback
---

## TL;DR
A fine-tuned model learns the labeling function, including every inconsistency and shortcut the labelers
had. Data labeling turns raw inputs into targets (classes, spans, demonstrations, preferences) in a way
whose quality you can measure, chiefly by having several people label the same items and computing
agreement. At fine-tuning scale a few thousand examples carry the whole signal, so each bad label is a
measurable share of what the model becomes.

## Why it exists
Supervised fine-tuning, reward models, and the small classifiers that route requests or enforce
guardrails all need targets. The model does not learn "the task"; it learns to reproduce the mapping
present in the labeled set, noise included. If 15% of labels are wrong in a consistent direction, the
model learns that direction. If they are wrong at random, the achievable accuracy drops and more data is
needed to reach any given level. Pretraining averages any single document away across billions of
tokens; a fine-tuning set of 2,000 examples has no such averaging, so one annotator's habit of filing
every ambiguous ticket under "billing" becomes model behaviour.

The naive approach, a one-line instruction handed to whoever is free, fails for three reasons. Most real
tasks are underspecified: "is this answer helpful?" means different things to different people, so the
labels encode the annotator rather than the task. With one labeler per item you cannot measure the noise
rate, so when the model plateaus at 80% validation accuracy nobody can say whether the model or the
labels are the limit. And the items you most need labeled well, the rare and borderline ones, are exactly
where untrained labelers disagree most.

Labeling as a discipline makes the target defined (guidelines with worked edge cases), measurable
(agreement on overlapping items) and improvable (adjudicated disagreements feed back into the
guidelines).

## How it works
### What a label is in LLM work
- **Categorical:** intent, topic, toxicity, routing class. Cheapest per item; feeds classifiers and
  guardrails.
- **Span or structured:** entities, citations, JSON fields. Needs a schema and tooling that enforces it.
- **Demonstrations:** a complete target response for a prompt, written or edited by an expert. This is
  SFT data and the most expensive kind, because the annotator produces rather than judges.
- **Preferences:** a prompt plus two candidate responses; which is better, optionally why. This feeds
  reward models, RLHF and DPO. Comparing is easier and more consistent than writing, and a comparison
  stays informative even when both candidates are flawed, which is why InstructGPT-style pipelines
  collect far more comparisons than demonstrations.
- **Rubric scores:** 1–5 on named criteria. Convenient, and the hardest to keep consistent across people
  unless every score level has anchor examples.

### The loop
1. **Define the task on paper.** Label schema, decision rules, and 20–30 worked examples that include
   the boundary cases and an explicit "if unsure, do X" rule. Ambiguity you leave here gets resolved
   differently by every annotator later.
2. **Pilot.** 100–200 items, each labeled independently by two or three people.
3. **Measure agreement.** For two raters on categorical labels use Cohen's kappa,
   κ = (p_o − p_e) / (1 − p_e), where p_o is the observed fraction of items they agree on and p_e is the
   agreement expected by chance from each rater's label frequencies. The correction matters because two
   raters who both say "not spam" 95% of the time agree about 90% of the time by luck alone. Fleiss'
   kappa extends this to many raters; Krippendorff's alpha also handles missing labels and ordinal
   scales. The Landis–Koch bands are the common reading: below 0 poor, 0–0.20 slight, 0.21–0.40 fair,
   0.41–0.60 moderate, 0.61–0.80 substantial, above 0.80 almost perfect. The bands are a convention, not
   a test; set the acceptable bar by the stakes of the task.
4. **Adjudicate and rewrite.** Every disagreement is a guideline gap or an annotator error. Fix the
   guideline, re-brief, re-pilot until agreement is acceptable.
5. **Produce with QA.** Single-label most items, but keep 5–10% overlap to track agreement, seed gold
   items with known answers to catch drift and inattention, and route anything flagged to adjudication.
6. **Version and freeze.** Store guideline version, annotator id and timestamp on every record. A
   guideline change mid-project silently splits the dataset into two tasks.

### Who labels, and how much a model should help
Domain experts are the only option for specialised demonstrations; crowd or vendor workforces scale
categorical tasks once guidelines are solid. Model-assisted labeling, where an LLM proposes a label or
drafts a response and a human verifies, cuts cost sharply but introduces anchoring: people accept
plausible suggestions, so errors the model makes confidently pass through, and a model later trained on
that data is partly trained on its own output. Keep the overlap and gold subsets blind to the suggestion
so you can measure the acceptance bias. Active learning chooses which items to label next, those where
the current model is uncertain or annotators disagreed, because labels on items the model already
handles add little.

### Why agreement rather than accuracy
There is no external truth to score the labels against; the labels are the truth you are constructing.
Agreement is the observable proxy for whether the task is defined well enough to be learnable, and it
sets a ceiling: a model cannot be meaningfully evaluated above the level at which humans agree with
each other.

## Example
A pilot round on support tickets: two annotators, kappa computed by hand, disagreements listed for
adjudication, and the adjudicated result written as SFT records with provenance. On this data it prints
kappa ≈ 0.48, "moderate" on the Landis–Koch scale, which means the guidelines are not finished.

```python
from collections import Counter
import json

pilot = [  # (item_id, text, label_from_annotator_a, label_from_annotator_b)
    ("t1", "Card charged twice this month", "billing", "billing"),
    ("t2", "App crashes when I open invoices", "bug", "billing"),
    ("t3", "How do I export my data?", "howto", "howto"),
    ("t4", "Refund still not received after 10 days", "billing", "billing"),
    ("t5", "Login page blank on Safari", "bug", "bug"),
    ("t6", "Can I pay yearly instead of monthly?", "billing", "howto"),
]

def cohen_kappa(a, b):
    n = len(a)
    p_o = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    p_e = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    return (p_o - p_e) / (1 - p_e)

a = [r[2] for r in pilot]; b = [r[3] for r in pilot]
print(f"kappa = {cohen_kappa(a, b):.2f}")          # 0.48 on this pilot
for item_id, text, la, lb in pilot:
    if la != lb:
        print(f"ADJUDICATE {item_id}: {text!r}  A={la}  B={lb}")

# After adjudication the agreed label is the target. Keep provenance on every record.
gold = {"t2": "bug", "t6": "billing"}
with open("tickets_sft.jsonl", "w") as f:
    for item_id, text, la, lb in pilot:
        label = gold.get(item_id, la)
        record = {
            "messages": [{"role": "user", "content": f"Classify this support ticket: {text}"},
                         {"role": "assistant", "content": label}],
            "meta": {"guideline": "v3", "annotators": ["a", "b"], "adjudicated": item_id in gold},
        }
        f.write(json.dumps(record) + "\n")
```

## Trade-offs
- Domain experts give the highest label quality and are mandatory for specialised demonstrations, but
  cost and throughput push most categorical work to trained crowds once agreement is stable.
- Overlapping annotators multiplies cost, but it is the only way to measure label noise; reduce overlap
  to 5–10% only after the pilot shows agreement has stabilised.
- Model-assisted labeling cuts cost and time sharply, but it anchors humans to the model's errors; the
  saving is real only if a blind subset shows acceptance is not inflating agreement.
- Detailed guidelines raise consistency but freeze a definition; when production inputs drift, agreement
  on new items falls and the guideline must be reopened rather than defended.
- Preference labels are cheaper per item than demonstrations and tolerate imperfect candidates, but each
  carries less information, so you need many more of them and a reward model or DPO to use them.

## Pitfalls
- One annotator per item with no overlap; the symptom is a validation plateau that nobody can attribute
  to the model or to the labels.
- Labeling the evaluation set with the same model-assisted pipeline as the training set; the symptom is
  inflated scores that collapse on a human-only audit.
- Changing guidelines mid-project without relabeling earlier items; the symptom is agreement with gold
  items drifting over time and a model that learns a blend of two definitions.
- Sampling items uniformly from production so rare classes get a handful of examples; the symptom is
  high overall accuracy with near-zero recall on the class you built the model for.
- Rubric scores without anchor examples per level; the symptom is one annotator's "4" being another's
  "2", hidden because most items cluster in the middle.
- Sending raw customer text to a vendor without PII redaction and a data-processing agreement; the
  symptom is a compliance incident rather than a model problem.

## When to use
Label when the behaviour you need is not already in the base model or cannot be prompted reliably, and
when you will measure the result: a routing classifier, a reward model for your domain, SFT
demonstrations in a house style, or a golden evaluation set. Before labeling thousands of items, check
whether a few hundred high-quality examples plus a strong prompt gets you there; in LLM fine-tuning,
quality and coverage beat volume, and a smaller set of clean, diverse demonstrations routinely
outperforms a much larger noisy one. Measure kappa on the pilot, the gold-item pass rate during
production, and the model's accuracy on a human-only held-out set. In interviews, the expected depth is
to explain why chance-corrected agreement matters, how adjudication improves guidelines, and what
model-assisted labeling does to the data it produces.

## Role lens
- **Engineer:** Builds the labeling export and import, computes agreement in the pipeline, stores
  guideline version and annotator ids on each record, and keeps the gold subset blind to model
  suggestions.
- **Architect:** Decides what gets labeled and by whom, where labeled data lives and how it is versioned,
  and which tasks justify expert annotators versus crowd or model-assisted labeling.
- **FDE:** Runs the pilot with the customer's own domain experts, turns their disagreements into
  guidelines, and sets the expectation that labeling quality, not model choice, is usually the
  bottleneck.

## Check yourself
**Q:** A vendor offers to label 20,000 historical support tickets into eight routing classes in a week,
one annotator per ticket. How would you structure the engagement so that the result is trustworthy?
**A:** Start with a written guideline and a pilot of about 200 tickets labeled by three annotators
independently, compute Fleiss' kappa or Krippendorff's alpha, and adjudicate every disagreement into
guideline revisions before releasing the full batch. Insist on 5–10% ongoing overlap and a seeded gold
set so drift and inattention are measurable during the week. Stratify the sample so the rare classes get
enough items, redact PII before export, and label a separate human-only evaluation set that never sees
any model suggestion. Finally, record guideline version and annotator id on every record so later
analysis can isolate a bad batch instead of discarding the whole dataset.

## Quiz
### Q: Why report Cohen's kappa instead of the raw percentage of items two annotators agree on?
- [ ] Kappa is easier to compute for more than two annotators
- [x] Raw agreement is inflated when labels are skewed, because raters agree by chance on the common class; kappa subtracts that chance agreement
- [ ] Kappa measures accuracy against the ground truth, which raw agreement cannot
- [ ] Raw agreement cannot be computed for categorical labels
> With a 95% "not spam" base rate, two raters who ignore the text would still agree about 90% of the
> time. Kappa removes the expected-by-chance share, so it reflects agreement on the decisions that
> actually required judgement. Neither metric measures accuracy, since there is no external truth.

### Q: What is the main risk of having an LLM pre-fill labels for human review?
- [ ] It makes the labels incompatible with the fine-tuning format
- [ ] Human annotators become slower because they must read two things
- [x] Annotators tend to accept plausible suggestions, so the model's confident errors pass through and the next model is partly trained on its own output
- [ ] The model's labels are always worse than human labels, so pre-filling never helps
> Pre-filling genuinely cuts cost, which is why it is common. The problem is anchoring: agreement looks
> high because humans agree with the suggestion, not with each other. Keeping a blind subset lets you
> measure how much acceptance bias the suggestions introduce.

### Q: Why do RLHF-style pipelines collect many more preference comparisons than written demonstrations?
- [ ] Preference labels are the only kind a transformer can learn from
- [ ] Demonstrations cannot be stored in the chat format used for training
- [x] Comparing two responses is faster and more consistent across annotators than writing a good response, and it still gives signal when both candidates are imperfect
- [ ] Preference comparisons carry more information per item than a demonstration
> Each comparison carries less information than a full demonstration, which is why many are needed.
> They win on cost and consistency: judging is easier than producing, and a reward model can aggregate
> thousands of noisy comparisons into a usable signal.
