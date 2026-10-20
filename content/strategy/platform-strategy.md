---
id: platform-strategy
module: strategy
name: Platform strategy
kind: decision
difficulty: advanced
est_minutes: 16
related:
  - llmops-platform/centralized-vs-federated-ai-platform
  - llmops-platform/ai-control-plane-vs-data-plane
  - system-design/build-vs-buy
  - system-design/ai-gateway
  - infra-capacity-economics/cost-allocation-by-tenant-and-feature
  - delivery-ownership/productizing-repeated-customer-solutions
  - strategy/ai-roadmap
---

## TL;DR
Platform strategy decides which AI capabilities an organisation provides once, as a shared internal
product, and which it leaves to each team, and when to make that move. The rule of thumb: platformise
what several teams need, what varies little between them, and what carries organisation-wide risk or
cost (model access, identity, budgets, audit, telemetry, evaluation tooling); leave the differentiating
parts (prompts, corpora, agents, UX) with teams. Build the platform after demand appears, not before.

## Why it exists
Once a company has more than a couple of AI projects, the same needs recur: an API key to a model
provider, a way to log prompts and costs, a document-ingestion pipeline, an eval set and harness, PII
redaction, a review UI. Left alone, each team builds its own. That has visible costs (duplicated
engineering, several contracts with the same provider at worse prices) and invisible ones: no single
inventory of where models are used, no consistent data-retention policy, no way to rotate a key or swap a
deprecated model everywhere, and no organisation-wide view of spend.

The naive fix is the opposite extreme: a central team designs a comprehensive "AI platform" up front and
mandates it. That fails in a different way. The platform is designed from guesses about requirements, so
it fits nobody exactly; it is built before anyone uses it, so there is no feedback; and because it is the
only sanctioned path, every missing feature becomes a ticket in a queue while product teams wait. Teams
then route around it, and you have both a platform and shadow stacks.

Platform strategy is the discipline between these: deciding the boundary, the timing, the operating
model and the funding so the shared layer is pulled by demand rather than pushed by architecture. It
connects to two neighbouring lessons: build-vs-buy decides how each layer is sourced, and
centralised-vs-federated decides the organisational ownership model. This lesson is about the sequencing
and the economics that tie them together.

## How it works
### 1. Inventory the recurring capabilities
List what each current and roadmapped use case needs, layer by layer: model access, routing and
fallback, secrets, prompt and model registry, retrieval and ingestion, evaluation, observability and cost
attribution, guardrails, human review. The inventory is the demand signal; without it, the platform is
designed from imagination.

### 2. Score each capability on three questions
- **Breadth of demand:** how many teams need it now or in the next two quarters? A common heuristic is to
  wait until the third consumer, *because* by then you have seen enough variation to generalise correctly.
- **Variance between teams:** do they need roughly the same thing (a gateway, audit logging) or genuinely
  different things (a retrieval strategy tuned to legal contracts vs. support tickets)? Low-variance
  capabilities centralise well; high-variance ones become a lowest-common-denominator abstraction that
  every team fights.
- **Externalities:** does getting it wrong in one team create risk or cost for everyone (leaked keys,
  data sent to an unapproved region, uncapped spend, no audit trail)? Those belong in the shared layer
  even with low demand, *because* the cost of inconsistency is organisational.

### 3. Draw a thin mandatory core and an optional paved road
The core is what every AI workload must go through: identity, approved model access through a gateway,
budgets and rate limits, logging to a common trace store, data-classification rules. It is mandatory
*because* its value comes from being universal (one inventory, one place to swap a model). The paved road
is everything else the platform offers as defaults (an eval harness, an ingestion template, a review UI):
optional, but easier than building your own. Teams that leave the paved road accept owning what they
build.

### 4. Treat the platform as a product
It has users (internal teams), a roadmap driven by their needs, documentation, SLOs and adoption metrics:
number of teams onboarded, time from idea to first production call, share of spend flowing through the
gateway. A platform team measured on features shipped rather than on consumer outcomes drifts toward the
over-built monolith described above.

### 5. Choose a funding model
Central funding makes adoption easy but hides cost. Showback (reporting each team's usage) creates
awareness without friction. Chargeback (billing teams) aligns incentives but can push teams to route
around the platform to save money. Many organisations start with central funding plus showback and move
to chargeback for variable costs such as tokens once usage is significant.

### 6. Keep seams so decisions are reversible
Expose the platform through stable internal interfaces (an OpenAI-compatible endpoint, a trace schema,
an eval-result format) rather than leaking a vendor's SDK into every service, *because* the model and
tooling market changes quickly and you will swap components under the interface.

## Example
A worked scenario. An insurer has six product teams using LLMs. The platform lead tabulates the
capabilities (counts are from the inventory; costs are illustrative):

| Capability | Teams needing it | Variance | Externality | Decision |
|---|---|---|---|---|
| Model access + keys + budgets | 6 | low | high (spend, data region) | mandatory core |
| Prompt/response logging, cost attribution | 6 | low | high (audit) | mandatory core |
| PII redaction before the model | 4 | low | high (privacy) | mandatory core |
| Eval harness + result store | 5 | medium | medium | paved road |
| Document ingestion pipeline | 3 | medium | low | paved road, template |
| Retrieval strategy / chunking | 3 | high | low | team-owned |
| Agent framework | 2 | high | low | team-owned, revisit |

Before the platform, each team had its own provider account; finance saw six invoices and no per-feature
breakdown, and security found two teams sending claim documents to a model region the data policy did
not allow. The first platform release is deliberately small: a gateway in front of approved models, with
per-team budgets, common logging and redaction. Migration is cheap for teams because the gateway speaks
the same API shape they already call. Within a quarter, all spend is attributable per feature, one team's
model deprecation is handled by changing a routing rule, and the eval harness is offered next because
five teams asked for it. Agent frameworks stay team-owned: only two teams use them and their needs
differ, so a shared abstraction now would freeze the wrong design.

## Trade-offs
- A mandatory core gives one inventory, one audit trail and one place to swap models, but adds a hop and
  a dependency; it flips toward federation when latency budgets are tight or teams are in separate
  regulatory domains.
- Building the platform after the third consumer generalises from real needs, but tolerates some early
  duplication; it flips when an externality (data residency, spend) makes even two divergent stacks
  unacceptable.
- Abstracting over model providers enables switching and routing, but hides provider-specific features
  (caching modes, tool formats); allow pass-through for teams that need those features.
- Chargeback aligns usage with cost, but taxes adoption; prefer showback until the platform is the
  default path and token spend is material.

## Pitfalls
- Building a comprehensive platform before any consumer exists; the symptom is a polished system with
  low adoption and teams quietly keeping their own API keys.
- Centralising high-variance capabilities like retrieval strategy; teams file endless feature requests or
  fork the shared component, and its configuration surface balloons.
- A platform team measured on output, not adoption; it ships features nobody asked for while the top
  consumer request ages in the backlog.
- Leaking one vendor's SDK through the platform interface; the first provider migration turns into a
  change in every consuming service.
- No exit path for teams off the paved road, so exceptions accumulate as unreviewed shadow stacks.

## When to use
Make platform decisions explicitly once two or three teams are building on models, or earlier if a
compliance or spend externality already exists. Start with the thin core: gateway, identity, budgets,
logging. Add paved-road components when demand is proven and variance is low. Measure adoption, time to
first production call, share of traffic through the core, and consumer satisfaction. Do not build a
platform as the first AI project; build it out of the first projects' shared parts. In interviews, show
you can name what to centralise and why, and when you would deliberately leave duplication in place.

## Role lens
- **Engineer:** Consumes the platform through its stable interface and contributes back components that a
  second team clearly needs, rather than forking or bypassing it.
- **Architect:** Owns the boundary between mandatory core, paved road and team-owned layers, and the
  interfaces that keep vendor choices reversible.
- **FDE:** Notices which customer solutions repeat across accounts and argues, with the evidence, for
  turning them into platform or product capabilities.

## Check yourself
**Q:** Two teams want a shared agent framework built by the platform team; four other teams use plain
model calls. Should the platform build it now?
**A:** Probably not yet. Two consumers is thin demand, and agent frameworks are high-variance: tool
contracts, state handling and orchestration differ a lot between use cases and the ecosystem is still
moving. A shared abstraction now would likely encode one team's design and become a constraint for the
other. What the platform should provide now are the low-variance parts both teams need anyway: model
access through the gateway, tracing, budgets, sandboxed tool credentials. Revisit when a third team needs
agents and the common shape is visible, and keep a light standard (trace format, approval-gate pattern)
so convergence later is cheap.

## Quiz
### Q: Which capability is the strongest candidate for the mandatory platform core?
- [ ] A chunking strategy tuned for one team's contracts
- [ ] An agent framework used by two teams
- [x] Approved model access with per-team budgets and audit logging
- [ ] A shared prompt library for marketing copy
> Model access, budgets and audit are needed by every team, vary little, and create organisation-wide
> risk when done inconsistently. The chunking strategy and agent framework are high-variance and fit poorly.

### Q: Why is "wait for the third consumer" a common heuristic before platformising a capability?
- [ ] Platform teams are usually staffed only after three projects exist
- [x] By then the real variation in needs is visible, so the shared design generalises from evidence
- [ ] Licensing for shared tools typically requires three internal customers
- [ ] Two teams can always share code without a platform
> One or two consumers give too little signal to separate essential needs from incidental ones. The
> heuristic is overridden when an externality such as data residency makes divergence unacceptable early.

### Q: What is the usual effect of switching to chargeback before the platform is the default path?
- [ ] Adoption rises because teams can see their costs
- [ ] Platform costs fall because usage becomes more efficient
- [x] Some teams route around the platform to avoid the bill, recreating shadow stacks
- [ ] It has no effect, because the costs are the same either way
> Billing teams for a platform they could bypass makes bypassing look cheaper locally. Showback first
> builds cost awareness without that incentive, and chargeback works once the core is unavoidable.
