---
id: technical-due-diligence
module: strategy
name: Technical due diligence
kind: practice
difficulty: advanced
est_minutes: 17
related:
  - strategy/vendor-evaluation
  - data-architecture/model-and-dataset-provenance
  - evals/dataset-contamination
  - infra-capacity-economics/unit-economics-per-successful-task
  - safety-security/supply-chain-security-for-models-and-datasets
  - security-governance-compliance/ai-risk-management
  - system-design/open-weight-vs-proprietary
---

## TL;DR
Technical due diligence on an AI company, product or team (for an acquisition, investment, major
partnership or strategic vendor) tests whether the technical claims behind a deal are true and what they
will cost to keep true. For AI it centres on five questions: does the AI work on data the target did not
choose, who owns the data and models it depends on, what happens to margins as usage grows, how exposed
is it to upstream model providers, and can the system and team be operated by someone else.

## Why it exists
Deals are priced on claims: "proprietary model", "95% accuracy", "data moat", "70% gross margin",
"enterprise-ready". In conventional software, diligence checks code quality, architecture, security and
team. Those still matter, but AI adds failure modes that a code review cannot see. Accuracy can be an
artefact of a test set the target curated or trained on. A "proprietary model" may be prompts around a
third-party API whose terms or prices can change. Training data may have been collected without the
rights the business model assumes. Inference costs scale with usage, so margins that look healthy at
current volume can compress as customers use the product more, or as the product adds longer context or
agentic loops.

The naive approach is to review slides, a demo and the architecture diagram, and to ask the founders.
That checks presentation, not reality: a demo runs on chosen inputs, a diagram shows intent rather than
what is deployed, and the people answering have every reason to be optimistic. The cost of missing a
problem is asymmetric: discovering after closing that accuracy does not hold on the acquirer's customers,
or that a key dataset cannot be used commercially, can erase the deal's value. Diligence exists to turn
claims into evidence before the price is fixed.

## How it works
### 1. Start from the thesis, not a checklist
Write down the claims the deal depends on ("their extraction is materially better than what we could
build in a year", "margins hold at 10× volume", "we can deploy it in our regulated customers' VPCs").
Each claim becomes a hypothesis with the evidence that would confirm or refute it. This focuses limited
time *because* a generic checklist spends equal effort on things that do not affect value.

### 2. Verify the AI actually works, independently
Request their eval methodology, datasets and recent results, then go further: run the system on a
held-out sample **you** provide, from your own or your customers' data, scored by your criteria. Ask how
the eval set was built and whether any of it was used in training or prompt tuning (contamination). A
gap between their number and yours is the most informative finding in AI diligence.

### 3. Map dependencies and their terms
List every model, API, dataset, and open-source component in the production path, with license and
contract terms: is a third-party model's output allowed in this use, can the provider change pricing or
deprecate the model, do open-weight model licenses permit the intended commercial scale, is the data
processed in regions customers accept. Ask what share of the product would survive the main provider
doubling prices or withdrawing a model.

### 4. Establish data rights and provenance
For training, fine-tuning and retrieval data: where it came from, under what consent or license, whether
customer data is used across customers, and whether deletion requests can be honoured, including in
trained weights. Missing provenance is a legal and reputational liability that transfers with the deal.

### 5. Rebuild the unit economics
From billing data and traces, compute cost per request and per customer, then project at higher usage:
token growth per task, retries, agent steps, human review. Check whether the target's pricing model passes
those costs on. Margins that depend on a provider's current discount are a risk, not a fact.

### 6. Check operability and the team
Is there monitoring, an incident history, an eval-gated release process, reproducible training? Can the
system be rebuilt from the repository? How concentrated is knowledge (key-person risk)? Interview
engineers separately from leadership, *because* the most useful information is often the mismatch.

### 7. Classify findings by consequence
Each finding is a deal-breaker, a price or terms adjustment (escrow, warranty, earn-out tied to a
metric), or a post-close remediation item with an estimated cost. This converts technical findings into
decisions the deal team can act on.

## Example
A worked scenario. A company considers acquiring a startup that sells AI contract review. Claims: "94%
clause-extraction accuracy" and "68% gross margin". All figures are illustrative.

**Accuracy.** The startup's eval set has 1,000 contracts it selected; the diligence team learns 300 of
them were also used to iterate on prompts. The team supplies 200 contracts from its own customers. Result:
86% on the acquirer's sample, with most failures on scanned contracts and non-English clauses, which make
up a larger share of the acquirer's customer base. The claim is not false, but it does not transfer.

**Economics.** Revenue per customer averages $4,000 per month. Current inference cost is $900 per month per
customer, plus $400 of hosting and support, giving (4,000 − 1,300) / 4,000 ≈ 68%. But the roadmap adds a
multi-step agent review that the startup's own traces show uses roughly 3× the tokens per contract.
At the same price and token rates, inference becomes $2,700 and margin falls to (4,000 − 3,100) / 4,000 =
22.5%. Margin also depends on a discounted rate with one provider that expires next year.

**Dependencies.** Everything runs on one external model API; no abstraction layer; prompts tuned
specifically to that model.

**Classification.** Not a deal-breaker. Price adjustment for the margin risk; an earn-out tied to accuracy
on the acquirer's own held-out set; post-close remediation budget for OCR improvements and a gateway
abstraction. Without independent testing, the deal would have been priced on 94% and 68%.

## Trade-offs
- Independent re-testing on your own data gives the most reliable evidence, but requires data-sharing
  agreements and time; when the timeline is short, a smaller sample still beats taking the claim on trust.
- Deep code and infrastructure review finds operability risk, but is expensive; scale it to how much of
  the deal value depends on running the system yourself rather than on its customers or team.
- Treating heavy reliance on a third-party model as a red flag protects against provider risk, but can
  undervalue a target whose real asset is workflow, data and distribution; judge the cost of switching,
  not the dependency itself.
- Strict findings protect the buyer, but can kill reasonable deals; classifying into deal-breaker,
  adjustment and remediation keeps the response proportionate.

## Pitfalls
- Accepting the target's accuracy number on its own eval set; the symptom appears after closing, when
  quality drops on the acquirer's customers and nobody can explain why.
- Ignoring train/test overlap or prompt tuning on the eval set; scores look remarkably stable and high
  but fall sharply on any genuinely new sample.
- Computing gross margin at today's usage and today's discounted prices; margin erodes as features add
  tokens and the discount lapses.
- Skipping data-provenance questions; a later customer audit or deletion request reveals the target
  cannot show the rights to its training data.
- Interviewing only founders; key-person risk and undocumented systems surface only when that person
  leaves after the earn-out period.

## When to use
Run AI-specific technical diligence for any acquisition, investment or strategic partnership where AI is
part of the value, and in lighter form for a critical vendor whose failure would hurt you (see vendor
evaluation, which covers the comparative selection process). Scale depth to deal size and to how much
value depends on the AI claims. The outputs to aim for are an independent accuracy number on your data, a
dependency and license map, a data-rights summary, a usage-sensitive margin model, and a classified
findings list. In interviews, the strong signal is turning each headline claim into a test you would run.

## Role lens
- **Engineer:** Runs the independent eval on held-out data, reads traces to rebuild cost per request, and
  checks whether the system can actually be rebuilt and deployed from the repository.
- **Architect:** Owns the dependency map, provider-exposure analysis and integration risk, and estimates
  the remediation needed to make the target fit the acquirer's platform and controls.
- **FDE:** Brings real customer data and environments to the test, and judges whether the product will
  survive deployment in regulated or air-gapped customer settings.

## Check yourself
**Q:** A target claims a "proprietary model". How would you test that claim and why does the answer matter
to the deal?
**A:** I would ask for the production architecture and then verify it in traces and billing: which
model calls are made, to which providers, with what weights or adapters. "Proprietary" might mean a model
trained from scratch, a fine-tune of an open-weight base, or prompts and retrieval around a third-party
API. Each has different value and risk: a fine-tune needs data rights and license checks on the base
model; a wrapper depends on provider pricing and terms, so its moat is workflow, data and distribution. I
would then measure how much better it is than a strong off-the-shelf baseline on our own held-out data.
The answer changes the valuation, not necessarily the decision.

## Quiz
### Q: What is the single most informative test in AI technical due diligence?
- [ ] Reviewing the target's architecture diagram
- [x] Running the system on a held-out sample the acquirer provides, scored by the acquirer's criteria
- [ ] Counting the number of models the target has trained
- [ ] Reading the target's published benchmark results
> Independent testing on data the target did not choose removes curation and contamination effects. A
> gap between their number and yours directly measures how far the headline claim transfers.

### Q: Why can a healthy gross margin at current usage be misleading for an AI product?
- [ ] Gross margin excludes all infrastructure costs by definition
- [ ] AI products always have negative margins at scale
- [x] Inference cost grows with tokens per task and usage, and may rest on temporary provider discounts
- [ ] Revenue is always recognised later for AI products
> Features like longer context or multi-step agents multiply tokens per task, and discounts can lapse.
> Unless pricing passes those costs through, margin falls as usage grows.

### Q: A target's eval set was partly used to tune its prompts. What symptom would you expect on fresh data?
- [ ] Higher accuracy than reported, because prompts generalise
- [x] Lower accuracy than reported, since the reported score partly reflects fitting to those examples
- [ ] Identical accuracy, because prompt tuning does not affect evaluation
- [ ] Random accuracy, because prompts are model-specific
> Iterating on prompts against an eval set fits the system to those examples, just like training on test
> data. The reported score overstates performance on genuinely new inputs.
