---
id: centralized-vs-federated-ai-platform
module: llmops-platform
name: Centralized vs federated AI platform
kind: decision
difficulty: core
est_minutes: 14
related:
  - strategy/platform-strategy
  - llmops-platform/ai-control-plane-vs-data-plane
  - system-design/ai-gateway
  - security-governance-compliance/model-governance
  - security-governance-compliance/ai-inventory-and-use-case-risk-classification
  - infra-capacity-economics/cost-allocation-by-tenant-and-feature
  - infra-capacity-economics/shared-vs-isolated-model-serving-infrastructure
  - adoption/change-management
sources:
  - Skelton & Pais, 2019 — Team Topologies
  - Dehghani, 2022 — Data Mesh
  - Conway, 1968 — How Do Committees Invent?
---

## TL;DR
Centralized: one platform team owns model access, registries, evaluation infrastructure,
observability and governance, and product teams consume them. Federated: each team runs its own AI
stack under shared standards. Pure centralization becomes a ticket queue; pure federation becomes
seven gateways, no inventory and duplicated spend. For most organisations the answer is a thin
mandatory core (identity, model access, budgets, audit, telemetry) with federated ownership of
prompts, evals, corpora and agents, with the line moved as team count and regulatory load grow.

## Why it exists
LLM adoption spreads faster than any previous infrastructure. A product team can be in production
with a provider API key and a credit card in an afternoon, so by the time anyone asks "what AI do we
run", the honest answer is a survey. That state has concrete costs: spend spread over many accounts
that never reach volume pricing; three home-grown gateways and four tracing setups; guardrails
configured differently per team; and no way to answer a regulator's or customer's question about
which models process which data. The federated default is not a decision anyone made; it is what
happens without one.

The reflex response is a central AI team, sometimes called a centre of excellence, that owns all
model access and reviews every use case. That fails differently. The central team cannot know the
claims domain, the support domain and the legal domain well enough to own their prompts and quality
bars, so quality disputes replace quality. Every request to use a model or change a prompt waits in
a queue, and the teams with the most urgent needs route around it, recreating the shadow usage the
centralization was meant to end. The central team, measured by what it ships, starts building
applications for the loudest business unit and stops being a platform.

The decision exists because the two failure modes are real and opposite. Conway's law guarantees the
platform's shape will follow the organisation's, so the question is which responsibilities must be
owned once, for the whole company, and which must stay with the people who understand the domain.
Team Topologies' answer is a platform team that treats the platform as a product consumed by
stream-aligned teams; Data Mesh's answer for analytics, domain ownership with federated
computational governance, translates directly to AI: domains own their prompts and evals, the
platform owns the rails and the rules are enforced by code rather than by review.

## How it works
### The two poles
Under **centralization** the platform team runs the gateway with all provider keys, the approved
model list, the registries, the evaluation harness and judge services, the trace pipeline, baseline
guardrails and governance artefacts such as risk classification and model cards. It often also
builds the applications. Product teams request access and capacity. Under **federation** each
product team or business unit picks providers, holds keys, builds or buys its own tracing and
evaluation, sets its own quality bar, and the central function is at most a policy document and a
quarterly review.

### What must be owned once
Some responsibilities only work if there is exactly one of them, *because* their value comes from
completeness: identity and model access through a single gateway, so every call is authenticated,
attributed and rate-limited; budgets and chargeback, so finance sees one account and the volume
discount applies; the inventory and audit log, so "which models touched this tenant's data" is a
query; the telemetry pipeline with its redaction policy, so compliance holds for every team; the
deprecation calendar, so a provider sunset is handled once; and the approved-model list with risk
tiers. These form the mandatory core. It is kept thin *because* every mandatory item is friction,
and friction breeds workarounds.

### What must stay with the domain
Quality is domain knowledge. The prompts, the golden sets, the rubrics, the retrieval corpora, the
agent designs and the acceptance thresholds for a claims extractor can only be owned by the claims
team, *because* only they can judge an output. Federating these is not a concession; it is the only
way the quality bar is meaningful. The platform supports them with a shared eval harness, judge
services, templates and training, offered rather than imposed.

### The paved road
Between mandatory and federated sits the shared-but-optional layer: a reference application
template with tracing, evals and guardrails pre-wired; a managed judge; dataset tooling; golden-path
documentation. The paved road works when it is easier to use than to bypass, which is a product
requirement on the platform team: self-service onboarding, a published SLA for requests, and a
roadmap driven by adoption metrics rather than feature counts. Governance in a federated model is
**computational**: conformance checks in CI (spans carry mandatory attributes, model pins are on the
approved list, bundles are registered) replace review meetings, *because* review scales with
headcount and checks scale with code.

### Mapping to control plane and data plane
The split often lands on the control-plane boundary: centralize the control plane (policy, keys,
inventory, rollout state, deprecations) and allow federated data-plane deployments, so a business
unit with residency or isolation requirements runs its own gateway replicas in its own region or
network while consuming central configuration. The reverse, federated policy with a shared gateway,
is rarely stable.

### Moving the line as the organisation grows
With one to three teams there is no platform, only the most mature team's stack, and the right move
is to make that stack reusable. From a handful of teams, a shared gateway and telemetry pipeline
become worth a part-time owner. Past roughly ten teams, or at the first regulated use case, a
dedicated platform team with the mandatory core pays for itself. The signals that the line is in the
wrong place are measurable: the share of model spend flowing through the gateway, the median time
from "we want to use a model" to the first production call, the age of the platform's request queue,
and the number of unregistered use cases found per audit.

## Example
An insurer has 14 product teams building on LLMs (all figures illustrative). Three teams bought their
own provider keys, two built gateways, one has an evaluation harness, and the rest copy prompts
between repositories. Monthly provider spend is around $180k across seven accounts, so no account
reaches the provider's volume tier and finance cannot attribute spend to products. Compliance asks
which models process policyholder data and receives an estimate.

Option A, full centralization: a six-person platform team takes over every key, builds gateway,
registries, harness and tracing, and reviews each team's prompts. At roughly 25 requests a month and
two engineer-days each, review alone consumes about 50 of the team's 120 engineer-days, leaving
little for the platform itself; the queue grows, and the two teams with deadlines keep their keys.

Option B, leave it federated: each of 14 teams builds its own gateway, tracing and harness at about
six engineer-weeks each, roughly 84 engineer-weeks of duplicated work, with 14 guardrail
configurations and still no inventory.

Option C, hub and spoke: the platform team spends a quarter on the mandatory core only: gateway with
keys, budgets and audit; trace pipeline with redaction; approved-model list; deprecation calendar;
and a paved-road template. Keys outside the gateway are revoked on a dated schedule. Spend
consolidates into one account, so even a 10% volume discount is worth about $18k a month; the
inventory becomes a query; teams keep their prompts, evals and corpora and inherit tracing from the
template. The platform's own metrics: share of spend through the gateway (target near 100%), median
onboarding under two days via self-service, and zero unregistered use cases at the next audit.

## Trade-offs
- Centralizing model access gives pooled pricing, a complete inventory and one enforcement point, at
  the cost of a single point of failure and a team that can become a bottleneck; the balance tips
  back toward federation only when the gateway's outages or queue cost more than the duplication did.
- Federated ownership of prompts and evals keeps quality with the domain but duplicates harnesses and
  standards; offering the harness as a paved road recovers most of the consistency without taking
  ownership away.
- A long mandatory list guarantees compliance but breeds workarounds; every item added to the core
  should be justified by something that only works when it is complete.
- Central chargeback is precise when all traffic flows through one gateway and meaningless when
  40% bypasses it; attribution quality is a direct function of how mandatory the gateway really is.
- Federated data-plane deployments satisfy residency and isolation, but each one is an operational
  surface the platform must support; the cost is justified by regulation, not by preference.

## Pitfalls
- The platform team building applications for one business unit; the symptom is a roadmap full of
  that unit's features while other teams wait, and the platform itself stagnating.
- Mandating the gateway without self-service onboarding; the symptom is shadow keys reappearing
  within a quarter, because the sanctioned path was slower than a credit card.
- Federation without an inventory; the symptom is a compliance questionnaire answered by survey,
  and a provider sunset discovered separately by each team.
- Measuring the platform by features shipped rather than adoption; the symptom is a rich platform
  with a third of spend bypassing it.
- Centralizing prompts and golden sets; the symptom is a central team defending a quality bar for a
  domain it does not understand, and the domain team disowning the result.
- Governance by review meeting; the symptom is a monthly committee that cannot keep up and approvals
  that become rubber stamps.

## When to use
Decide explicitly as soon as the second team starts an LLM project; the cheapest moment to make the
gateway and telemetry mandatory is before anyone has to migrate. Centralize what must be complete to
be useful, federate what needs domain judgement, and put everything else on the paved road. Revisit
the line when the number of teams, the regulatory load or the spend concentration changes. Measure
it by spend through the gateway, time to first production call, queue age and audit findings. In
daily work it appears as the argument over who owns a prompt, who pays for a judge, and whether a
team may hold its own key. In interviews, the strong answer names the mandatory core, explains why
quality ownership cannot be centralized, and describes computational governance instead of
committees.

## Role lens
- **Engineer:** Builds on the paved-road template, registers bundles and pins approved models, and
  adds the CI conformance checks that make federated governance enforceable.
- **Architect:** Draws the line between the mandatory core, the paved road and domain ownership, and
  defines the adoption metrics that tell the organisation when to move it.
- **FDE:** Fits a customer deployment into their platform model, running data-plane components in
  the customer's boundary while respecting their central policy, keys and inventory.

## Check yourself
**Q:** After a compliance scare, a CTO proposes moving all AI work into one central team. What would
you centralize, what would you refuse to centralize, and how would you stop the central team from
becoming a bottleneck?
**A:** Centralize what the scare actually needs: model access through one gateway with keys, budgets
and audit, the telemetry pipeline with redaction, the inventory and risk classification of use cases,
the approved-model list and the deprecation calendar. Those answer the compliance question completely
only if they are complete, which is the argument for making them mandatory. Refuse to centralize
prompts, golden sets, rubrics, corpora and agent designs, because quality judgement lives with the
domain and a central team owning them would produce either a queue or an unowned quality bar. To
avoid the bottleneck, run the platform as a product: self-service onboarding with a published lead
time, a paved-road template that is faster than building, conformance checks in CI instead of review
meetings, and adoption metrics such as spend through the gateway and time to first production call
as the team's success criteria. Offer enabling engineers to embed with teams temporarily rather than
taking their work over.

## Quiz
### Q: Which responsibility is strongest as a candidate for the mandatory central core?
- [ ] The golden datasets for each product's evaluation
- [x] Model access, keys and budgets through a single gateway
- [ ] The agent design for the support assistant
- [ ] The retrieval corpus for the legal team
> Access and budgets only deliver inventory, attribution and pooled pricing when they are complete,
> which requires a single enforcement point. Golden sets, agents and corpora need domain judgement.

### Q: Which symptom most clearly indicates over-centralization rather than under-centralization?
- [ ] Compliance cannot list which models process customer data
- [ ] Three teams run three different gateways
- [x] Teams with deadlines keep their own provider keys because the sanctioned path takes weeks
- [ ] Provider spend is spread across many accounts
> Shadow keys reappearing despite a mandate means the central path is slower than bypassing it. The
> other three symptoms are what federation without a core looks like.

### Q: What distinguishes a paved road from a mandate in a federated platform?
- [ ] A paved road is enforced by CI, a mandate by policy documents
- [ ] A paved road applies only to new teams
- [x] A paved road is adopted because it is easier than the alternative, while a mandate is required regardless
- [ ] A paved road covers infrastructure, a mandate covers prompts
> The paved road competes with building it yourself and wins on convenience; mandates are reserved
> for the few things that must be complete to work. Both can be checked in CI.
