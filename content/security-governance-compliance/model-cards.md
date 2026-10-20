---
id: model-cards
module: security-governance-compliance
name: Model cards
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - security-governance-compliance/model-governance
  - security-governance-compliance/ai-inventory-and-use-case-risk-classification
  - security-governance-compliance/compliance-architecture
  - data-architecture/model-and-dataset-provenance
  - llmops-platform/model-registry
  - evals/model-evaluation
  - delivery-ownership/technical-documentation
sources:
  - Mitchell et al., 2019 — Model Cards for Model Reporting
  - Gebru et al., 2018 — Datasheets for Datasets
  - Official docs: Hugging Face Hub model cards
---

## TL;DR
A model card is a short, structured document that travels with a model and states what it is for, what
it is not for, how it was evaluated (broken down by the conditions that matter), and where it fails. It
exists because a model's behaviour cannot be inferred from its weights or API, so the people deploying,
approving and auditing it need the builder's knowledge written down, versioned, and tied to the exact
artifact it describes.

## Why it exists
A trained model is an opaque function. Someone downstream, another team, a customer, a regulator, has
to decide whether it is safe to use for *their* purpose, and the only people who know the relevant
facts (what data it saw, which populations it was tested on, where accuracy drops) are the builders.
Without a written record, that knowledge lives in a notebook and a Slack thread and leaves with the
engineer who trained it.

The classic failure is silent scope creep. A classifier evaluated on adult English support tickets gets
reused for a medical triage queue, or a summarizer tested on news is pointed at legal contracts. Nothing
in the API tells the second team the model was never measured there. A single headline metric ("94%
accuracy") makes this worse, because an aggregate can hide a subgroup where the model is much worse.

The naive alternative, "the README describes the model", fails in three ways: it is unstructured, so
critical sections (out-of-scope uses, disaggregated results) are simply absent; it is not tied to a
version, so it describes last quarter's model; and it is written for users of the code, not for the
reviewer who must approve a deployment. Mitchell et al. proposed model cards to fix exactly this: a
fixed set of headings that forces the builder to answer the questions a deployer will ask. Regulation has
since made the same demand: the EU AI Act requires technical documentation for high-risk systems and
documentation that general-purpose model providers must pass to downstream providers.

## How it works
### The sections and why each exists
The original proposal has nine sections; most production templates keep their spirit:
1. **Model details**: owner, version, date, architecture, licence, contact. Exists *because* a card
   that cannot be tied to one artifact and one accountable owner is unauditable.
2. **Intended use and out-of-scope uses**: the primary use cases and users, and explicitly what the
   model must not be used for. The out-of-scope list is the most valuable line in the card *because* it
   is the only defence against reuse in a context nobody tested.
3. **Factors**: the conditions along which performance is expected to vary: demographic groups,
   languages, input channels, document types, device or sensor.
4. **Metrics**: which metrics, why those, and the decision thresholds used, *because* "accuracy" means
   nothing without knowing whether false positives or false negatives cost more.
5. **Evaluation data** and 6. **Training data**: provenance, size, collection period, preprocessing,
   known gaps. For third-party foundation models you often only know what the provider discloses; say so.
7. **Quantitative analyses**: results *disaggregated* by the factors, with uncertainty where possible.
   This is the core idea: an aggregate number averages away the subgroup that gets hurt.
8. **Ethical considerations**: sensitive data, risks to people, mitigations applied.
9. **Caveats and recommendations**: known failure modes and what a deployer must add (human review,
   guardrails, monitoring).

### Model card vs system card vs datasheet
A **datasheet** (Gebru et al.) documents a dataset: motivation, composition, collection, consent,
recommended uses. A **model card** documents one model. A **system card** documents a deployed system:
the model plus prompts, retrieval, tools, guardrails and human oversight. For most LLM applications the
system card is what matters, *because* the same foundation model behaves very differently behind
different prompts, tools and filters, and the risk lives in the composition. A good practice is a chain:
the provider's model card, your fine-tune's card that references it, and a system card per deployment.

### Making it a living artifact
A card written once at launch rots. The mechanism that keeps it honest is automation:
- Store the card next to the model in the registry, keyed by the immutable version (or weights hash),
  so promoting a version without a card is a failed check, *because* a card that can drift from its
  artifact is worse than none: it lends false authority to the wrong model.
- Generate the quantitative section from the evaluation pipeline, not by hand, so the numbers in the
  card are the numbers that gated the release.
- Require human-written sections (intended use, out-of-scope, caveats) to be reviewed when the eval
  set, training data or intended use changes, and record who signed them, *because* intent is the one
  thing a pipeline cannot measure.
- Make the gate fail on *missing* evidence, not only on bad evidence: a declared factor with no slice, or
  a slice too small to be meaningful, blocks release just as a low score does. Otherwise the cheapest way
  to pass is to not measure.
- Keep machine-readable metadata (Hugging Face model cards put YAML front matter above Markdown) so the
  inventory can query licence, task and owner across all models.

## Example
A team fine-tunes a small model to route insurance claims emails into 12 queues. The release pipeline
writes the quantitative section from eval results, and a gate refuses promotion when any factor slice
falls below the threshold the card declares.

```python
import json

card = {
    "model": "claims-router", "version": "2026.10.1", "owner": "claims-ml@corp",
    "intended_use": "Route inbound claim emails to one of 12 queues for human handling",
    "out_of_scope": ["Deciding claim approval or payout", "Non-English emails", "Voice transcripts"],
    "metric": "macro-F1", "release_threshold": 0.85, "min_slice_n": 200,
    "factors": ["language_variant", "channel", "attachment_present"],
}

# Produced by the eval pipeline on the frozen eval set: (score, n). Illustrative numbers.
results = {
    ("channel", "web_form"): (0.91, 1800), ("channel", "email"): (0.89, 950),
    ("channel", "broker_portal"): (0.78, 240), ("channel", "fax_scan"): (0.88, 40),
    ("attachment_present", True): (0.86, 1100), ("attachment_present", False): (0.90, 1930),
}

rows = [{"factor": f, "value": str(v), "macro_f1": s, "n": n} for (f, v), (s, n) in results.items()]
card["quantitative_analyses"] = rows
failing = [r for r in rows if r["n"] >= card["min_slice_n"] and r["macro_f1"] < card["release_threshold"]]
too_small = [r for r in rows if r["n"] < card["min_slice_n"]]
untested = [f for f in card["factors"] if f not in {f for f, _ in results}]
card["caveats"] = [f"Below threshold on {r['factor']}={r['value']} ({r['macro_f1']:.2f}, n={r['n']})" for r in failing]
card["caveats"] += [f"Too few examples to judge {r['factor']}={r['value']} (n={r['n']})" for r in too_small]
card["caveats"] += [f"No evaluation results for declared factor {f}" for f in untested]

print(json.dumps(card["caveats"], indent=1))
if failing or too_small or untested:
    raise SystemExit("Release blocked: a slice is below threshold, under-sampled, or unmeasured")
```

Why the aggregate hides the problem: if production traffic is roughly 60% web form, 31% email, 8% broker
portal and 1% fax, the traffic-weighted score is about 0.6 × 0.91 + 0.31 × 0.89 + 0.08 × 0.78 +
0.01 × 0.88 ≈ 0.89 (F1 does not decompose exactly by slice, but the order of magnitude holds). The
headline clears 0.85 comfortably, while broker-portal claims, which are a small share of traffic but all
come from one class of customer, get a router measurably worse than everyone else's. The card surfaces it, and the team either fixes it or ships with "broker portal
out of scope" written down.

The gate also refuses two kinds of false comfort. `fax_scan` scores 0.88, but on 40 examples that number
is noise: for an accuracy-like metric near 0.8, the standard error at n = 100 is already
√(0.8 × 0.2 / 100) ≈ 0.04, so a 95% interval is about ±0.08, and at n = 40 it is wider still. And
`language_variant` is declared as a factor with no slice at all; a declared factor that was never measured
is a silent gap, not a pass. Both become caveats a reviewer must read, instead of numbers that look fine.

## Trade-offs
- A strict template gives comparability across hundreds of models, but invites box-ticking; it flips
  to a liability when reviewers accept boilerplate instead of reading the out-of-scope list.
- Disaggregated metrics expose hidden failures but need labelled slices; when slice sizes are tiny the
  numbers are noisy, so report counts and intervals or the card becomes misleadingly precise.
- Publishing detailed cards builds customer and regulator trust, but can reveal competitive or
  security-relevant details; split into a public card and an internal annex when that matters.
- Automating cards from the pipeline keeps numbers current, but generated text cannot state intent;
  the human-written sections still need an owner and a review trigger.

## Pitfalls
- Writing the card after launch from memory; the symptom is numbers in the card that nobody can
  reproduce from any stored eval run.
- Reporting only the aggregate metric; the symptom is a complaint from one customer segment that the
  card's headline number says should not exist.
- Documenting the base model but not the system; the symptom is an audit question about the guardrails
  or retrieval sources that no document answers.
- Leaving out-of-scope uses empty or generic ("do not use for illegal purposes"); the symptom is the
  model quietly appearing in a second product with a different risk profile.
- Not versioning the card with the model; the symptom is a card describing a model three versions old.

## When to use
Write one for every model or AI system that another team, a customer or an approver relies on, and make
it a release gate rather than a wiki page. Depth should scale with risk: a low-risk internal tagger needs
a half-page card, while a system in an employment, credit or health decision needs full disaggregated
evaluation, data provenance and a system card. For third-party models, collect the provider's card and
write your own system card on top. Measure card health by coverage (share of inventory entries with a
current card) and staleness (versions shipped without a card update). In interviews, the strong signal is
knowing that out-of-scope uses and disaggregated results are the point, not the formatting.

## Role lens
- **Engineer:** Wires the eval pipeline to write the card's quantitative section and adds a registry check
  that blocks promotion of any version without a current card.
- **Architect:** Defines the documentation chain (provider card, fine-tune card, system card), the required
  sections per risk tier, and where cards live relative to the model registry and inventory.
- **FDE:** Hands the customer's risk or procurement team a system card for their deployment and adds
  their data and slices to the evaluation so the card reflects their reality.

## Check yourself
**Q:** Your company deploys a vendor LLM behind a RAG pipeline for HR policy questions. The vendor
publishes a model card. Is that sufficient documentation for your internal AI review board? What would
you add?
**A:** No. The vendor card describes the base model's training and general evaluations, but the risk
your board cares about lives in the system: the retrieval corpus and its freshness, the system prompt,
guardrails, who the users are, and what decisions the answers inform. I would write a system card that
references the vendor card and adds intended use (answering policy questions, not making employment
decisions), out-of-scope uses, groundedness and refusal results on our own HR question set broken down by
policy area and language, known failure modes such as stale documents, and the human escalation path. It
must be versioned with the prompt and index, because changing either changes behaviour.

## Quiz
### Q: Which model card section most directly prevents a model being reused in a context it was never tested in?
- [ ] Model details, because it identifies the owner
- [x] Intended use with explicit out-of-scope uses
- [ ] Training data, because it shows what the model has seen
- [ ] Metrics, because it states the decision thresholds
> The out-of-scope list tells a downstream team in plain words where the model must not be used. Training
> data hints at coverage but requires inference; the explicit exclusion is what stops silent scope creep.

### Q: Why does the model card approach insist on disaggregated quantitative results?
- [ ] Regulators require one number per demographic group by law
- [ ] Aggregate metrics are statistically invalid for classifiers
- [x] An aggregate can look acceptable while one subgroup or condition performs much worse
- [ ] It makes the card longer and therefore more credible
> Averages weight slices by their frequency, so a small but important subgroup with poor performance barely
> moves the headline. Breaking results down by factors is the only way to see that failure before users do.

### Q: For an LLM application built from a vendor model, prompts, retrieval and guardrails, which document best captures deployment risk?
- [ ] The vendor's model card alone
- [ ] A datasheet for the retrieval corpus alone
- [x] A system card covering the composed system and referencing the vendor's model card
- [ ] The application's API reference documentation
> The same base model behaves very differently depending on prompts, tools and filters, so risk lives in the
> composition. A system card documents that composition, while the vendor card and datasheets are inputs to it.
