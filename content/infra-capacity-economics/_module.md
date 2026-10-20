---
id: infra-capacity-economics
title: Infra, capacity & economics
---

## Overview
This module is about the physical and financial reality under every AI feature: where the model
runs, how much of it you need, and whether the result pays for itself. It starts with the hardware.
GPU infrastructure is the stack from card to driver to scheduler. GPU sizing turns a model config into
bytes of weights and KV cache. The roofline explains why decode is memory-bandwidth-bound and why
"GPU-Util" lies. Card selection asks which constraint binds first: fit, bandwidth or cost per token.
Next comes capacity: queueing theory and Little's law predict the latency knee, capacity planning
converts token distributions and an SLO into replicas or quota, latency budgets split one target
across every hop, and the Universal Scalability Law shows where adding replicas stops helping. The
deployment lessons cover inference clusters, KEDA, serverless GPUs, Ray, shared versus isolated
serving, provider quotas, cloud AI platforms, hybrid on-prem and edge. The economics lessons build
cost models, cost per successful task, allocation by tenant and feature, FinOps, ordered optimization,
the quality-latency-cost frontier, break-even and TCO. Afterwards you can size a deployment, defend a
replica count or quota request, choose between API, self-hosted, hybrid and edge with numbers, and
show what a successful task costs and who caused the cost. It matters most for Architects. Engineers
need it to run serving and metering, and FDEs need it for customer sizing and pricing.

## Why it matters
- **Engineer:** You derive KV bytes per token from the model config before an OOM does it for you, read DCGM SM and DRAM activity instead of GPU-Util, scale inference replicas on queue depth and KV usage through KEDA rather than CPU, and emit one tagged cost event per model call so spend reaches the right tenant and feature.
- **Architect:** You turn token histograms and a p95 TTFT target into a replica count with headroom below the queueing knee, choose card, region, quota and shared or isolated pools on the binding constraint, and back every build-versus-buy or on-prem decision with a break-even band and a TCO that includes people, evals and exit cost.
- **FDE:** You size a customer's deployment from their real document lengths and peak hour, file quota increases weeks before launch, place the endpoint inside their Bedrock, Azure or on-prem perimeter, and show the sponsor cost per successful task against the task's value, so the pricing and ROI conversation rests on their numbers.

## Mental model
Picture one pipe with three gauges. Requests enter at the left as tokens, not calls, and every
request has a shape: input length, output length, turns and context. The pipe's width is set by
physical limits: GPU memory decides how many sequences fit at once, memory bandwidth decides how fast
each one decodes, and provider quotas cap what a hosted pool will give you. Queueing theory says what
happens as the pipe fills: wait time stays flat and then jumps at the knee, so you run below it on
purpose and buy headroom. The three gauges on the pipe are quality, latency and cost, and every lever
moves at least two of them. Batching, quantisation, smaller models, caching, serverless and shared
pools all trade one gauge against another. Underneath sits a ledger that divides all spend, including
failures, retries and idle capacity, by tasks that succeeded, and assigns each dollar to a tenant and
feature. Infrastructure decisions are sizing the pipe, and economic decisions are reading the ledger.
Neither is right unless both agree.

## Suggested order
1. gpu-infrastructure — the layer map (card, host, driver, runtime, scheduler, procurement) that every later hardware lesson plugs into.
2. gpu-sizing — the weights-plus-KV-cache arithmetic that decides card count, precision and promised concurrency.
3. gpu-utilization-and-memory-bandwidth — the roofline explains why decode is bandwidth-bound, which sizing alone does not show.
4. a100-h100-l4-selection — applies fit, bandwidth and cost per token from the previous two lessons to a concrete card choice.
5. queueing-theory — Little's law and the utilization knee, the reason no capacity plan targets "average load fits".
6. capacity-planning — combines queueing with measured per-replica capacity to turn token demand and an SLO into replicas or quota.
7. latency-budgets — splits the user-facing target across every hop, so capacity work knows how much time the model actually gets.
8. scalability — the USL shows where adding replicas stops paying off and which shared resource is to blame.
9. inference-clusters — the replica fleet, router and LLM-specific scaling signals that put the capacity plan into practice.
10. keda-autoscaling — the Kubernetes mechanism that scales those replicas on queue depth and to zero, and its cold-start limits.
11. serverless-gpus — the per-second alternative for bursty load, decided by utilization break-even and cold-start tolerance.
12. ray-kuberay — the distributed Python runtime for heterogeneous batch, embedding and multi-node workloads a plain Deployment handles poorly.
13. shared-vs-isolated-model-serving-infrastructure — when one batched pool serves every tenant and when noisy neighbours or contracts force isolation.
14. api-provider-quotas-and-regional-limits — capacity planning on hosted APIs is quota planning, with 429s, headroom and reserved throughput.
15. cloud-ai-architecture — places a managed model endpoint inside a cloud's identity, network and per-region quota perimeter.
16. hybrid-on-prem — routes restricted data to owned GPUs and the rest to hosted APIs, and sizes the fixed pool so it stays busy.
17. edge-ai-deployment — the far end of the placement spectrum, sizing small models to device memory and bandwidth with a cloud fallback.
18. cost-modeling — turns request shape (fresh, cached, output and reasoning tokens, plus agent turns) into dollars from logged distributions.
19. unit-economics-per-successful-task — divides the full cost, failures included, by tasks that met the bar, the number every later decision uses.
20. cost-allocation-by-tenant-and-feature — assigns direct and shared spend to whoever caused it, so unit economics exist per tenant.
21. finops — the ongoing inform–optimize–operate practice that keeps metering, allocation and budgets running after the first analysis.
22. cost-optimization — the ordered list of levers (avoid, shrink, route, buy cheaper, tune), each gated by an eval.
23. quality-latency-cost-optimization — chooses from the Pareto frontier across all three gauges instead of optimizing cost alone.
24. break-even-analysis — the API-versus-self-host crossover, driven by utilization and team cost rather than GPU list price.
25. tco — the widest frame, adding people, eval upkeep, forced migrations and exit cost over a fixed horizon to every option.

## Capstone
### Brief
You are the architect for a B2B SaaS company whose contract-review assistant extracts clauses and
flags risks in uploaded agreements. It runs on a hosted frontier API in one region and costs $48k a
month. Three things have just happened: the largest tenant, 40% of volume, now requires EU data
residency; the provider's tokens-per-minute quota throttled the Monday morning peak twice last
month; and finance wants to know whether self-hosting an open-weight 70B model would be cheaper.
Over a week, using a synthetic request log with realistic input and output token histograms, tenant
IDs, timestamps and task outcomes (generate it or use the one provided), produce a decision that holds
up under review. Size the model on candidate cards, build the capacity plan and latency budget, model
cost per successful task by tenant, compare hosted, self-hosted and hybrid options on break-even and
TCO, and recommend a serving shape with its scaling and quota plan.

### Deliverables
- A sizing sheet for the 70B candidate showing weights and KV bytes per token derived from the model config, concurrency per card at the p95 context length on L4, A100 and H100 at bf16 and fp8, and the roofline time per output token compared with a measured or published figure.
- A capacity plan that converts the log's peak hour into requests/s, input and output tokens/s and in-flight sequences via Little's law, sets per-replica capacity at the TTFT and TPOT SLO, gives a replica count with headroom justified by queueing, and converts the hosted path into TPM and RPM per region with a quota-increase request.
- A latency budget for the review path (upload, parsing, retrieval, guardrail, model queue and prefill, decode) with per-hop p95 allocations, a reserve, and the steps that degrade under deadline pressure.
- A cost model and allocation report giving cost per successful task by tenant and feature, with retries, failed extractions and human review included, shared GPU cost allocated by GPU-seconds, and the unattributed share stated.
- A break-even and TCO comparison of hosted API, self-hosted and hybrid (EU tenant on owned or regional capacity, the rest hosted) over 24 months, with a sensitivity table on utilization, team cost and an API price cut.
- A serving design with an autoscaling configuration (KEDA or equivalent) keyed on queue depth and KV usage, a shared-versus-isolated decision for the large tenant, and a one-page recommendation that names the premium paid for the chosen option.

### Rubric
- Concurrency per card is derived from bytes per token and free memory after weights, and the engine's KV-block count, or a published figure for the same engine, is within about 20% of the estimate, or the gap is explained.
- The replica count targets a utilization below the queueing knee with the wait-time consequence shown, and plans for peak demand rather than the daily average.
- Every cost figure is per successful task rather than per call, and at least one option that looks cheaper per call is shown to cost more once failures and retries are counted.
- Break-even is reported as a band with the assumption that flips the recommendation named explicitly, and the TCO includes people, eval upkeep and migration or exit cost.
- The autoscaling rule never uses CPU or GPU-Util as its signal, and its threshold comes from a measured per-replica capacity curve.
- The residency requirement is enforced at the gateway by data classification, and the large tenant's isolation decision cites a measured noisy-neighbour effect or a contract term, not preference.
