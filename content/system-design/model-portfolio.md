---
id: model-portfolio
module: system-design
name: Model portfolio
kind: pattern
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - system-design/model-selection
  - system-design/model-routing-cascading
  - system-design/multi-model-architecture
  - system-design/ai-gateway
  - system-design/model-fallback-strategies
  - llmops-platform/model-lifecycle-and-deprecation-policy
  - llmops-platform/model-registry
  - security-governance-compliance/vendor-exit-strategy
sources:
  - Chen et al., 2023 — FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance
---

## TL;DR
A model portfolio is a deliberately small, governed set of models, each with a role: a frontier tier
for hard reasoning, a workhorse for the bulk, small fast models for classification and routing,
embeddings and rerankers, plus specialised and fine-tuned variants. Each entry has an owner, eval
baselines, a cost card, approved data classes and a retirement plan. It exists because one model
everywhere overpays on easy work and underdelivers on hard work, while unmanaged per-team choices
create sprawl nobody can evaluate, secure or exit.

## Why it exists
Two failure modes bracket the problem. The first is **one model for everything**: the frontier model
classifies support intents at frontier prices and frontier latency, cannot be deployed in the region a
customer requires, and when its provider has an outage or announces a deprecation, every product is
affected at once. The second is **every team picks its own**: eighteen months later there are a dozen
models across half a dozen providers, a dozen sets of prompt lore, no shared evaluation, a separate
security review and data-processing agreement per provider, incompatible data-use terms, no record of
which model is allowed to see personal data, and when a provider retires a version nobody knows which
systems depend on it.

The reasoning that resolves this is a cost curve. Every model in the set carries a **fixed** cost
regardless of how much traffic it serves: an eval baseline per task family, a security and privacy
review, prompt expertise, monitoring, quota management, and a deprecation watch. The **marginal**
benefit of adding a model diminishes fast once the main tiers are covered, because the tasks that
remain are already served well enough by something on the list. So the optimum is small but greater
than one: enough variety to match task difficulty to price and to satisfy deployment constraints, few
enough that the task-by-model evaluation matrix is actually run. Engineering organisations already do
this with approved languages and databases; the portfolio applies the same discipline to the most
expensive dependency in the system.

## How it works
### Roles, and why each exists
- **Frontier or reasoning tier.** The hardest work: long-horizon agentic flows, complex code, low-volume
  high-value analysis. Slow and expensive, so it is used by exception, typically behind a router or an
  explicit task assignment.
- **Workhorse tier.** The mid-size model that handles most retrieval-augmented answers, drafting and
  summarisation. Chosen by the model-selection funnel as the cheapest model that clears the bar for
  the typical task families.
- **Small and fast tier.** Classification, routing, schema-bound extraction, PII detection and
  guardrail checks. Often fine-tuned, sometimes self-hosted, expected to answer in well under a second.
  This tier exists *because* most traffic is easy and the price gap between tiers is roughly an order
  of magnitude per step (illustrative, provider-dependent).
- **Embeddings and rerankers.** The retrieval stack. Versioned with extra care *because* changing an
  embedding model invalidates every stored vector; vectors from two models are not comparable.
- **Specialised models.** Vision and document understanding, speech-to-text, text-to-speech, code.
- **Fine-tuned variants.** Derived from a base for a specific high-volume task; owned by the team that
  trained them but registered centrally so their lifecycle is visible.

### The registry entry
Each model has a record: exact pinned version, provider and deployment location, data-use and
retention terms, approved data classifications (may it see customer PII, source code, regulated
data?), eval baselines per task family that uses it, a cost card (list price plus measured average
tokens per request type), quota and rate limits, owner, announced deprecation date, and its designated
fallback. The record is what turns "we use several models" into something an auditor, a finance team
and an on-call engineer can each read.

### Entry and exit rules
A model enters only if it beats an incumbent on a shared eval for some task family, or unlocks a
constraint the set cannot meet (a new modality, a cheaper tier, a residency requirement). This rule
is what keeps the set from regrowing. A model exits through a planned migration: deprecation notice →
list of consuming tasks from gateway logs → regression evals on each → cutover behind the gateway →
removal. Because provider deprecations are routine, the process has to be routine too.

### Enforcement through indirection
Applications reference **logical names** such as `workhorse`, `classifier-small` or `embed-v3`, and
the gateway maps those to the pinned vendor model. Only registered models are routable. This
indirection is the mechanism that makes fallback, rotation and vendor exit possible *because* the
dependency lives in one configuration table rather than in a dozen code bases. It does not make
prompts magically portable; a rotation still runs regression evals on every consuming task.

### Diversity and concentration
Critical tiers should have a second source on a different provider, for outage resilience and for
negotiating leverage. Every additional provider, though, is another security review and contract, so
diversity is bought per tier, not blanket. Two concentration metrics tell you whether the portfolio is
working: share of spend per provider, and share of requests landing on the frontier tier. If the
frontier handles a large fraction of volume, routing or task assignment is wrong.

## Example
A worked scenario with **illustrative price ratios** (frontier 10 units per million input tokens and 30
per million output; workhorse 1 and 3; small 0.1 and 0.3; real prices vary by provider and date).
Daily traffic is 1,000,000 requests: 70% intent classification (300 input, 10 output tokens), 25% RAG
answers (3,000 in, 250 out), 5% agentic tasks averaging 6 model calls each (20,000 in, 2,000 out per call).

**Everything on the frontier model.** Classification: 700,000 × (300×10 + 10×30)/10⁶ = 2,310 units.
RAG: 250,000 × (3,000×10 + 250×30)/10⁶ = 9,375. Agentic: 300,000 calls × (20,000×10 + 2,000×30)/10⁶ =
78,000. Total ≈ 89,700 units per day.

**Tiered portfolio.** Classification on the small tier: 700,000 × (300×0.1 + 10×0.3)/10⁶ ≈ 23 units.
RAG on the workhorse: 250,000 × (3,000×1 + 250×3)/10⁶ ≈ 938. Agentic still on the frontier: 78,000.
Total ≈ 78,960, only a 12% saving, because the agentic 5% of requests is 87% of spend.

**Portfolio plus routing inside the agentic task.** Tracing shows that 4 of the 6 calls per task are
tool-result summarisation and formatting that the workhorse handles at the same eval score. Frontier:
100,000 calls × 0.26 = 26,000. Workhorse: 200,000 × (20,000×1 + 2,000×3)/10⁶ = 5,200. Agentic total
31,200; grand total ≈ 32,160 units, a 64% reduction against the single-model baseline. The lesson is
that the portfolio's first job is to make spend legible: the biggest lever was not the obvious 70% of
traffic but the step-level routing inside the smallest slice, and prefix caching of the shared 20k
context would cut it further.

## Trade-offs
- Tiering cuts spend, but every tier adds evaluation, prompt and monitoring overhead; below a certain
  traffic volume a two-model portfolio, one strong and one small, is the right size.
- A central approved list gives governance and purchasing leverage but slows experimentation; the
  usual fix is a sandbox tier for unregistered models on non-sensitive data, with registration
  required before anything reaches production.
- Multi-provider diversity buys resilience and negotiating power, but each provider is another review,
  contract and set of behavioural quirks; for low-stakes tasks a single provider with graceful
  degradation is enough.
- Logical model names decouple applications from vendors, but prompts and tool-calling behaviour are
  not fully portable, so a rotation still costs a regression run per consuming task.
- Fine-tuned variants give the best cost-quality on narrow tasks but multiply lifecycle work, since
  each must be retrained or re-evaluated when its base model is retired.

## Pitfalls
- The portfolio exists on paper while applications call vendor model IDs directly. Symptom: a single
  deprecation notice turns into code changes and releases across a dozen repositories.
- The frontier tier carries bulk traffic because "it works". Symptom: frontier share of spend far
  exceeds the share of tasks that need it, and cost grows linearly with adoption.
- Rotating the embedding model like any other model. Symptom: retrieval quality collapses overnight
  because the index was not rebuilt with the new model's vectors.
- No per-task eval baselines in the registry. Symptom: a rotation approved on one team's evaluation
  silently degrades another team's task.
- Too many registered models. Symptom: the task-by-model evaluation matrix is never fully run, so the
  quality of most combinations is unknown.
- Skipping data-classification approval per model. Symptom: an audit finds personal data flowing to a
  model whose terms never permitted it.

## When to use
Start a portfolio as soon as more than one task family or more than one team uses models; even a
single product benefits from a small-plus-strong pair with explicit assignment. A lone prototype does
not need one. Measure spend share per tier and provider, the fraction of requests on the frontier
tier, the number of registered models (it should stay roughly flat), time to complete a migration
after a deprecation notice, and eval coverage of the task-by-model matrix. In daily work this is the
question "which model should this new feature use", answered by the registry rather than by habit. In
interviews, "how would you manage models across an organisation" is looking for tiers, a registry,
entry and exit rules, logical names behind a gateway, and the concentration metrics.

## Role lens
- **Engineer:** Calls models by logical name through the gateway, adds eval baselines for any task that
  joins the portfolio, and runs the regression suite when a rotation is proposed.
- **Architect:** Owns the tier structure, the entry and exit rules, second-sourcing decisions per tier,
  and the concentration metrics that show whether routing matches intent.
- **FDE:** Translates the portfolio into the customer's environment, substituting models that meet
  their residency and data-use constraints while keeping the same logical roles and baselines.

## Check yourself
**Q:** After eighteen months your organisation runs eleven models across six providers. Leadership
wants this reduced without breaking products. How do you rationalise the portfolio, and how do you
stop it regrowing?
**A:** Start with an inventory from gateway or billing logs: which applications call which model, at
what volume and spend. Cluster the calls into task families and assemble or collect an eval set per
family. For each family, run the selection funnel across the models already in use, which reveals the
minimal set that clears every family's bar, typically a frontier model, a workhorse, a small model,
an embedding model and one or two specialised ones. Plan migrations family by family with regression
evals, moving applications onto logical names behind the gateway as you go so the next change is
configuration only. Keep a second provider for the tiers that matter. To prevent regrowth, enforce the
entry rule, a new model must beat an incumbent on a shared eval or unlock a constraint, give every
entry an owner and a retirement date, and publish spend share and frontier-tier share monthly.

## Quiz
### Q: What is the main reason to keep a model portfolio small rather than registering every useful model?
- [ ] Providers charge more when an organisation uses many models
- [x] Each model carries fixed costs for evaluation, security review, monitoring and deprecation tracking, while the benefit of another model diminishes once the tiers are covered
- [ ] Gateways can only route to a limited number of back ends
- [ ] Small portfolios always produce higher quality outputs
> The cost of a model is not only its tokens; it is the task-by-model evaluations, reviews and
> lifecycle work it demands. Once a strong, a mid and a small tier exist, an extra model rarely
> clears a bar that nothing on the list already clears.

### Q: Why should applications reference logical names such as `workhorse` instead of vendor model identifiers?
- [ ] Logical names are billed at a lower rate
- [ ] Providers require logical names in their SDKs
- [x] So fallback, rotation and vendor exit become a configuration change in the gateway rather than code changes across every application
- [ ] Logical names avoid provider rate limits
> The indirection puts the dependency in one table. A deprecation or outage is then handled centrally,
> with regression evals per consuming task, instead of being discovered repository by repository.

### Q: Which portfolio rotation requires more than a prompt regression evaluation before cutover?
- [ ] Replacing the frontier model with a newer version from the same provider
- [x] Replacing the embedding model used by the retrieval index
- [ ] Replacing the small classification model with a fine-tuned variant
- [ ] Replacing the speech-to-text model
> Vectors produced by different embedding models live in different spaces, so queries embedded with
> the new model cannot be compared against documents embedded with the old one. The whole corpus must
> be re-indexed and retrieval quality re-measured before any switch.
