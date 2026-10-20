---
id: system-design
title: System design
---

## Overview
This module covers designing the whole system around a model call, from the first requirement to the
deployment boundary. It starts with the architect's working method. AI solution architecture says what
belongs to the model and what belongs to deterministic code. NFRs turn adjectives like "fast" and
"accurate" into measurable scenarios. Trade-off analysis, ADRs and C4 diagrams then let you choose
between options, record the choice and show the result. Reference architectures and build versus buy
give you starting blueprints and a per-layer sourcing decision. The model layer comes next: selecting a
model on your own evals, SLMs against LLMs, open-weight against proprietary, self-host against API, and
how to govern a small portfolio. From there you route and cascade requests, put an AI gateway in front of
every call, and plan fallbacks, degradation and caching. The application shapes follow: RAG, memory,
agentic and multi-agent systems, and orchestration. They rest on distributed-systems foundations:
partial failure, sync against async execution, consistency, events, outbox and inbox, sagas, and the
limits of exactly-once. The module closes with DDD, modular monolith against microservices, and
multi-tenancy with real tenant isolation. Afterwards you can take a vague AI product brief and produce
a defensible architecture. That means measurable NFRs, a costed model plan, a failure-aware runtime
design, tenant boundaries, and ADRs and diagrams that let other people review and challenge it. It
matters most for Architects. Senior Engineers who own a service end to end and FDEs who design
deployments for customers need it too.

## Why it matters
- **Engineer:** You implement the seams this module defines: the gateway client, the outbox relay, idempotent saga steps, tenant-scoped cache keys and retrieval filters, the async job API with streaming, and the router's quality check. If any of them is wrong, a duplicate charge or a cross-tenant leak follows.
- **Architect:** You own the decisions that are expensive to reverse: model tiers and sourcing, gateway and orchestration runtime, monolith or service split, pool, bridge or silo tenancy, and consistency guarantees per store. You have to defend each one with NFR scenarios, a trade-off matrix, an ADR and a C4 view that reviewers can check.
- **FDE:** Customers arrive with residency rules, an existing identity provider, a budget and a hard deadline. You have to fit a reference architecture to those constraints, explain self-host versus API and build versus buy in their terms, and leave behind diagrams and ADRs their own team can maintain after you leave.

## Mental model
Picture the system as concentric layers around a rented, probabilistic, stateless model, each layer
adding a guarantee the model cannot give. The innermost layer is the model plan: which models, chosen
on your data, sourced by deliberate build, buy, host and licence decisions, and governed as a
portfolio. Around it sits the control plane: a gateway, routing and cascades, fallbacks, a degradation
ladder and caches, so quality, cost and availability are managed in one place. Next come the
application shapes, which feed the model context and let it act: RAG, memory, workflows and agents.
Under all of them is a distributed system, so every boundary needs timeouts, idempotency, an explicit
consistency choice, and outbox, inbox or saga handling where state crosses services. The outer layer is
structure and ownership: bounded contexts, deployment units and tenant isolation. Wrapping everything is
the decision discipline. Measurable NFRs drive trade-off analysis, the result is recorded in ADRs, and C4
diagrams show it. A design nobody can review or reverse is not finished.

## Suggested order
1. ai-solution-architecture — frames the job: what the model does, what deterministic code must guarantee, and the components around every call.
2. nfrs — turns quality, latency, cost and safety goals into measurable scenarios, so later choices have something to be judged against.
3. trade-off-analysis — gives a repeatable method for choosing between options using those NFRs, sensitivity points and reversibility.
4. adrs — records each decision with its evidence and revisit trigger, so the rest of the module produces durable records.
5. c4-diagrams — supplies the notation for showing where models, data and trust boundaries sit, used in every later design.
6. reference-architectures — introduces the recurring AI system shapes you start from and deviate from on purpose.
7. build-vs-buy — decides per layer what to own and what to rent, and where to keep a seam so the choice can be reversed.
8. model-selection — the eval-driven funnel for picking the cheapest model that clears a measured quality bar.
9. slm-vs-llm-trade-offs — applies selection to model size and shows when a narrow small model beats a frontier one.
10. open-weight-vs-proprietary — separates the control and licensing axis from the deployment decision.
11. self-host-vs-api — works through the utilisation and constraint arithmetic that decides whether GPUs pay for themselves.
12. model-portfolio — turns individual choices into a small, governed set of models with owners, baselines and retirement plans.
13. multi-model-architecture — breaks one system into stages with a different model per stage, joined by typed contracts.
14. model-routing-cascading — sends each request to the right portfolio tier before or after a cheap first attempt.
15. ai-gateway — centralises keys, budgets, routing, caching and logging, which makes routing and fallbacks operable.
16. model-fallback-strategies — decides in advance what the gateway calls when a provider throttles or fails.
17. graceful-degradation — goes beyond fallbacks to a labelled ladder of reduced functions for when the AI path cannot be saved.
18. caching-strategy-and-cache-invalidation — cuts cost and latency at the prefix, response, embedding and retrieval layers without serving stale or wrong answers.
19. rag-architecture — the most common application shape: an offline indexing pipeline and an online retrieval path joined by an index.
20. memory-architecture — extends retrieval to state the system writes itself, scoped per user and tenant, with forgetting policies.
21. distributed-systems — sets out partial failure and the core toolkit every multi-service AI product relies on.
22. synchronous-vs-asynchronous-execution — picks request, stream or job-and-callback shapes for calls that take seconds to minutes.
23. data-consistency — chooses which invariants must be strong and which copies, such as indexes, caches and memory, may lag.
24. event-driven-architecture — decouples producers from consumers such as billing, evals and audit, at the cost of eventual consistency.
25. exactly-once-processing-limitations — explains why delivery cannot be exactly once and how to get exactly-once effect instead.
26. outbox-inbox-patterns — the concrete mechanism for publishing events atomically and processing redeliveries idempotently.
27. saga-pattern-and-distributed-transactions — coordinates multi-step operations across services with compensations around an irreversible pivot.
28. workflow-orchestration — runs long, stateful processes durably with Airflow, Prefect or Temporal instead of hand-rolled sagas.
29. agentic-architecture — wraps a model-driven loop in typed tools, budgets, approval gates and externalised state.
30. agent-orchestration — decides how much control flow is code and how much is the model, and where checkpoints and interrupts go.
31. multi-agent-systems — splits work across several agents only when isolation, parallelism or specialisation pays for the coordination cost.
32. domain-driven-design-and-bounded-contexts — draws boundaries by business meaning, which then define tool, data and service ownership.
33. modular-monolith-vs-microservices — turns those boundaries into deployment units only where scaling, team or blast radius demands it.
34. multi-tenancy — carries tenant identity through every layer and picks pool, bridge or silo per resource.
35. tenant-isolation — layers independent controls so one bug cannot leak data or capacity across tenants, including through the prompt.

## Capstone
### Brief
Design, and partly build, the architecture for "Policy Desk", a multi-tenant AI assistant that a
B2B insurance-software vendor will sell to its broker customers. Brokers upload policy documents and
ask questions with cited answers. An agent drafts endorsement change requests, which a human must
approve before the agent submits them to the carrier's API and records the fee in billing. Tenants
range from ten-seat agencies to one enterprise that requires EU data residency and refuses to have its
data in a shared vector index. Over one week, write measurable NFRs for answer quality, latency, cost per
question, availability and isolation. Run model selection on a small labelled set and define a
three-tier portfolio with routing and a fallback chain behind a gateway. Design the RAG and memory
layers with tenant-scoped indexes and caches. Specify the approval-and-submit flow as a durable workflow
saga with an outbox for the billing event. Decide the bounded contexts, the deployment units and the
pool, bridge or silo tier for each tenant class. Record each major choice as an ADR. Build a thin
vertical slice that proves the riskiest parts: tenant-scoped retrieval, gateway routing with fallback,
and an idempotent submit step.

### Deliverables
- An architecture document with C4 context and container diagrams, plus a component diagram for the agent runtime, each showing the trust and tenant boundaries and where every model call sits.
- An NFR catalogue of at least ten measurable scenarios, each with stimulus, condition and numeric response measure, mapped to an SLO, a test or an eval gate.
- A model-selection report: candidates filtered by hard constraints, eval scores and cost per 1,000 requests on the labelled set, the chosen portfolio tiers, the routing or cascade rule, and the ordered fallback chain with the error classes that trigger it.
- At least six ADRs, covering at minimum self-host versus API, gateway choice, tenancy tier per customer class, sync versus async for agent runs, monolith versus service split, and cache keying. Each has options, a trade-off matrix and a revisit trigger.
- A sequence diagram and failure table for the approve-and-submit saga, listing each step's compensation, the irreversible pivot, idempotency keys, and the outbox and inbox records.
- A runnable slice with tests: a gateway config with two providers and fallback, tenant-filtered retrieval and tenant-keyed response cache, and a workflow step that submits to a mocked carrier API exactly once under retries.

### Rubric
- Every NFR has a number and a measurement method, and at least three ADRs cite a specific NFR or eval result as the deciding evidence instead of preference.
- The model plan shows measured cost and quality per tier on the labelled set, and the router's chosen mix is within a stated quality tolerance of always using the top tier at a lower stated cost.
- A test that queries as tenant A, with tenant B's documents indexed and cached on the same key text, returns no B content. The isolation test suite fails if the tenant filter or cache key prefix is removed.
- Killing the provider in the gateway config makes requests succeed on the secondary with a labelled fallback flag in logs. Killing both makes the UI show the designed degraded state, not an error page.
- Replaying the carrier submit step three times produces one carrier call and one billing event, shown by idempotency-key and outbox logs. A forced failure after the pivot runs the documented forward-recovery path, not a compensation.
- The C4 diagrams pass a reviewer check: every box has a name, type, technology and purpose, every arrow has intent, and the EU enterprise tenant's data path stays inside its silo and region.
