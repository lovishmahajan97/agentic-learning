---
id: build-vs-buy
module: system-design
name: Build vs buy
kind: decision
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - strategy/vendor-evaluation
  - security-governance-compliance/vendor-exit-strategy
  - scoping/roi-model
  - infra-capacity-economics/tco
  - system-design/self-host-vs-api
  - system-design/open-weight-vs-proprietary
  - strategy/platform-strategy
  - delivery-ownership/productizing-repeated-customer-solutions
---

## TL;DR
Build versus buy in AI is not one decision but one per layer of the stack: model, serving, gateway,
orchestration, retrieval, evaluation and observability, guardrails, and the application itself. Buy
what is commodity and fast-moving, build what differentiates and touches your data most intimately,
and keep a thin seam you own at every boundary so the decision can be reversed. It exists because the
AI stack changes faster than build cycles, and both "build everything" and "buy everything" fail in
predictable ways.

## Why it exists
In mature engineering domains the question is settled: nobody writes their own database. The AI stack
is young, so the boundaries are not settled. Is an AI gateway a product or three hundred lines? Is
evaluation tooling a vendor or a script and a spreadsheet? Is a retrieval pipeline a managed service or
your core intellectual property? Because the answers are unclear, teams default to one of two
mistakes.

The first is **build everything, because AI is strategic**. Six months later there is a home-grown
vector store, gateway, tracing system and prompt manager, the actual product is behind schedule, and
the platform is now a maintenance burden competing with open-source projects that improved faster than
the team could. The error is conflating strategic importance with differentiation: the model is the
most strategic component in the system and almost nobody should train one.

The second is **buy an end-to-end product, because speed matters**. The start is fast. Then the
limits appear: the vendor's workflow is not your workflow, your most sensitive data source cannot be
sent to them, per-seat pricing scales with adoption rather than with value, the quality loop is a
black box you cannot run your own evaluation against, and at renewal you discover that prompts,
analytics and the test set all live inside their platform. The error is treating the application layer
as commodity when it is precisely where your domain knowledge and data live.

Neither default is wrong everywhere; each is wrong at specific layers. That is why the decision has to
be decomposed before it can be made.

## How it works
### Decompose the stack into layers
1. **Foundation model.** Rent through an API or run open weights; training from scratch is for a
   handful of organisations. The real choice here is self-host versus API and open-weight versus
   proprietary, each with its own lesson.
2. **Serving.** Buy (the API) or build on open-source engines if self-hosting; rarely write your own.
3. **Gateway and routing.** Buy, adopt open source, or a thin build; commodity, but you must own the
   configuration because it encodes your portfolio and budgets.
4. **Orchestration and agent logic.** Usually built, in plain code or on an open-source framework,
   because this is where your workflow lives and where most of the testing happens.
5. **Retrieval and knowledge.** Buy the vector store or search engine; build ingestion, chunking,
   access-control mapping and ranking tuned to your corpus, because that is where quality is decided.
6. **Evaluation and observability.** Buy or adopt the tracing and eval platform; always build the
   golden sets and task metrics. Nobody can sell you your own test set.
7. **Guardrails.** Buy generic detectors (PII, toxicity, injection); build the domain policies and the
   evaluations that prove they hold.
8. **Application and experience.** Build if it is your product; buy if it is internal productivity
   with no differentiation.

### Four questions per layer
- **Does it differentiate?** Would a customer choose you or pay more because of your version of this
  layer? Differentiation usually lives in data, domain workflows, evaluation sets and integrations,
  and rarely in plumbing. If the answer is no, buy.
- **How fast is the layer moving?** A fast-moving commodity layer should be bought or adopted,
  *because* a build will be obsolete before it is finished. A fast-moving core layer should be built
  thin and kept switchable.
- **What do data and compliance require?** A source that cannot leave your environment, retention
  rules, or audit requirements narrow the buy options to vendors that deploy where you are, and
  sometimes force a build.
- **What is the total cost including exit?** Build cost is engineering time to a first version plus
  ongoing maintenance, which is a material fraction of the build cost every year, plus the opportunity
  cost of what those engineers did not ship. Buy cost is licence plus integration (routinely
  underestimated) plus the lock-in premium at renewal plus the exit cost: data migration, prompt
  re-tuning, user retraining. Compare over three years, not year one.

### Patterns that come out of the questions
- **Buy then build.** Adopt a vendor to learn the real requirements, then replace the differentiating
  part once usage shows what it is. Justified when requirement uncertainty is high, *because* you
  cannot know what differentiates before users use it.
- **Build on open source.** The middle path for gateways, serving, eval frameworks: you own deployment
  and configuration but not development. It trades licence fees for operational expertise.
- **Buy the platform, build the product.** The common enterprise shape: a managed model platform with
  governance, and in-house applications on top.
- **Build the eval whatever else you buy.** The evaluation set is the one asset that is always yours,
  *because* it is the only instrument that holds a vendor accountable and makes switching measurable.
- **Own the seams.** Wrap every bought component in an interface you control. The test of a seam is
  "how long would it take to swap this", and that number should be known, not guessed.

### Procurement realities
Security review, data-processing agreement, residency, subprocessor lists and certifications take
calendar time regardless of the technical merits. Pricing models matter as much as price: per-seat
pricing punishes successful adoption, usage pricing without caps punishes success differently. A
credible build alternative is the strongest negotiating position. For FDEs the roles reverse: the
customer is making this decision about *your* product, and your job is to make "buy" credible by
showing the integration path, the exit path and a transparent quality loop.

## Example
An internal knowledge assistant for 2,000 employees over wikis, tickets and documents, one of which is
restricted and cannot be sent to third parties. Three options over three years, all figures
**illustrative** and in abstract units.

**A. Buy a vertical SaaS product.** 20 units per seat per month × 2,000 seats × 36 months = 1,440,000
units, plus about two engineer-months of connector and SSO integration. No differentiation, the
restricted source is excluded, and the evaluation loop is the vendor's.

**B. Build on managed components.** Rented model API, managed vector database, open-source gateway and
tracing. Build: two engineers for four months, 8 engineer-months at 15,000 units each = 120,000.
Maintenance at roughly 25% of build per year ≈ 90,000 over three years. Infrastructure ≈ 2,000 per
month = 72,000. Usage: 2,000 users × 10 queries per day × 20 workdays × 36 months = 14.4M queries at
about 3,500 tokens each ≈ 50 billion tokens, at 1 unit per million ≈ 50,400. Total ≈ 332,000 units.
The team owns the eval set, the access-control filter that lets the restricted source in, and the
model seam.

**C. Fully self-host open weights.** Option B plus a 24/7 GPU footprint at 8,000 per month = 288,000
and additional operations effort of perhaps 180,000 over three years, roughly 800,000 units. Justified
only if residency rules forbid the API, which they do not here.

Decision: B. The useful insight is the flip point. Option A costs 720 units per seat over three years
and B is dominated by about 282,000 of fixed cost plus roughly 25 units per seat of usage, so the two
cross near 400 seats. Below that, buy; above it, build on managed components. The restricted data
source would have forced B regardless, which is the compliance question overriding the cost question.

## Trade-offs
- Buying gives time-to-value and someone else's roadmap, but you pay lock-in and lose control of the
  quality loop; it flips the moment the capability is the product rather than a utility.
- Building gives control and differentiation, but you pay the build, permanent maintenance and the
  opportunity cost, while open-source alternatives keep improving; it flips when the layer commoditises.
- Open source avoids licence fees and vendor lock-in, but you own upgrades, security patches and the
  expertise to run it; with a small team that cost often exceeds a licence.
- Buy-then-build de-risks requirements, but you pay twice plus a migration; it is worth it when
  uncertainty about what users need is high and the vendor's exit path is clean.
- Seams and adapters bound switching cost, but they add abstraction and leak when a vendor-specific
  feature is the reason you bought; invest in proportion to switching probability × switching cost.

## Pitfalls
- Reading "AI is strategic" as "build every layer". Symptom: a platform team six months in with an
  in-house vector database and no shipped product.
- Underestimating integration for a bought product. Symptom: the licence was a third of the project
  cost; SSO, connectors and access-control mapping were the rest.
- Buying a product with an opaque quality loop. Symptom: nobody can explain a wrong answer, run the
  internal eval set against it, or tell whether last month's update made it worse.
- Mismatched pricing model. Symptom: per-seat pricing that doubled when adoption succeeded, or usage
  pricing with no cap that produced a surprise invoice.
- No exit plan. Symptom: at renewal the price rises and the prompts, analytics and evaluation data all
  turn out to live inside the vendor's platform.
- Building the evaluation set only inside the vendor's tooling. Symptom: switching vendors means
  starting the test set from zero.

## When to use
Run the per-layer decision at the start of any AI initiative, again at each renewal, and whenever a
layer visibly commoditises (a capability that needed a vendor last year ships in an open-source
project this year). Buy models, serving, gateways, tracing, vector stores and generic guardrails by
default. Build evaluation sets, domain workflows, context assembly, integrations with your systems of
record, and the experience if it is your product. Measure time-to-swap per bought component, spend by
layer, the integration-to-licence cost ratio, and whether a new engineer can point to which parts are
yours. In interviews, "would you build or buy X" is answered with the layer decomposition and the four
questions, never with one word.

## Role lens
- **Engineer:** Implements the adapter seams, evaluates a vendor component and a home-built option
  against the same golden set, and measures how long a swap actually takes.
- **Architect:** Owns the layer map and each layer's decision with a three-year cost that includes
  exit, and negotiates with a credible build alternative in hand.
- **FDE:** Sits on the vendor side of the customer's decision, proving integration path, exit path and
  evaluation transparency, and recognising honestly when the customer should build instead.

## Check yourself
**Q:** A fintech wants a customer-facing assistant that answers questions about a user's own
transactions and explains fees. The CTO asks, "build or buy?" Walk through your answer.
**A:** Decompose it. Model: rent, through an API or a private endpoint if the regulator requires it;
never train. Gateway, tracing and eval tooling: buy or adopt open source, they are commodity. Retrieval
over fee documents: a managed vector store, but build the ingestion and, above all, the per-user
transaction tool, because that integration with the ledger and its access controls is both the most
sensitive and the most differentiating piece. Guardrails: buy PII and toxicity detection, build the
"no financial advice" policy and the evaluation that proves it holds. Evaluation set: always build,
from real support conversations with compliance-reviewed answers. The experience: build, it is the
product. A vertical banking-chatbot SaaS fails three tests at once: transaction data would flow to a
vendor, fee explanations are the product, and compliance cannot review a black box. So buy the
platform layers, build the data, workflow and evaluation layers, keep seams at the model and the
vector store, and revisit at renewal.

## Quiz
### Q: Which layer should an organisation build regardless of what else it buys?
- [ ] The foundation model, to avoid vendor dependence
- [ ] The vector database, because retrieval quality depends on it
- [x] The evaluation set and task metrics, because they are the only instrument that holds any vendor or model accountable
- [ ] The AI gateway, because it handles credentials
> Models, vector stores and gateways are commodity layers with mature options. The golden set encodes
> your task and your quality bar; without it you cannot compare vendors, detect regressions or switch
> with confidence.

### Q: A vendor's end-to-end product costs less than building in year one. What most often reverses that over three years?
- [ ] Open-source alternatives become free, eliminating the build cost
- [x] Per-seat or usage pricing scales with adoption, integration costs more than the licence, and exit costs appear at renewal
- [ ] Models become cheaper, so the vendor's margins shrink
- [ ] Engineers become more productive, so builds get faster
> Year-one comparisons ignore how licence cost grows with success, how much integration work the
> purchase still requires, and the lock-in premium a vendor can charge once your prompts, data and
> analytics live inside their platform.

### Q: What is the purpose of an adapter seam around a purchased component?
- [ ] To reduce the component's latency by caching its responses
- [ ] To satisfy the vendor's API contract requirements
- [x] To bound the cost of switching so the build-versus-buy decision stays reversible
- [ ] To hide the component's cost from the finance team
> A seam you own turns a vendor swap into a known amount of work rather than a rewrite. Its value is
> proportional to how likely and how expensive a switch would otherwise be.
