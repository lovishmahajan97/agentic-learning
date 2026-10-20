---
id: reliability
title: Reliability
---

## Overview
This module is about keeping an AI product working, or failing in a controlled way, when the things
it depends on do not. It begins with targets: SLIs, SLOs, SLAs and error budgets turn "reliable" into
a number you can measure from the user's side, including quality and cost for model endpoints. High
availability then shows how availabilities multiply along a request path and why the model provider
usually caps yours. Fault tolerance supplies the baseline toolkit for every remote call: timeouts,
bounded jittered retries and idempotency. Circuit breakers, bulkheads, backpressure and load shedding
and tenant-level quotas are the containment layer, each stopping one slow dependency, workload or
customer from consuming capacity everyone else needs. Durable workflow execution makes long agent runs
and approval flows survive crashes and deploys without redoing paid model calls. Multi-region
architecture, disaster recovery and business continuity widen the frame to region loss, deleted data,
vendor exits and the people and decisions around a restore. FMEA and chaos engineering close the loop:
one finds the failure modes nobody is watching, the other proves the mechanisms actually fire. After
the module you can set SLOs for an LLM service, size pools and queues with Little's law, design
fallbacks and failover with stated RPO and RTO, and show evidence that they work. It matters most for
Architects, who own these trade-offs, and for Engineers who carry the pager.

## Why it matters
- **Engineer:** You write the client wrapper every service imports, so you must get timeouts, retry budgets, idempotency keys, breaker thresholds, per-dependency semaphores and `Retry-After` handling right, and export the metrics that show when traffic is quietly running on a fallback path.
- **Architect:** You choose the availability target, the number of zones, regions and model providers, the RPO and RTO for each store, and the isolation boundaries between tenants and workload classes, and each choice sets cost, complexity and what the SLA can honestly promise.
- **FDE:** Enterprise buyers ask about multi-region, continuity plans and what happens when your AI vendor is down; you need credible answers, a sensible SLA, and quotas that stop one customer's backfill from slowing everyone else on a shared deployment.

## Mental model
Picture reliability as a budget defended in rings. At the centre is the error budget from your SLO:
the amount of failure users will tolerate, measured as they experience it. The first ring is per-call
hygiene (timeout, bounded retry, idempotency), which turns transient faults into successes without
duplicating side effects. The second ring is containment: breakers, bulkheads, backpressure, load
shedding and tenant quotas, each keeping a local fault local by capping how much shared capacity it
can take, sized with Little's law (in-flight = rate × time). The third ring is survival of larger
losses: redundancy and failover for components and regions, durable execution for long processes,
backups separated in time, place and trust for data, and continuity plans for suppliers and people.
Around all of it sits verification: FMEA decides which faults to worry about, and chaos experiments
show whether each ring holds. A mechanism nobody has seen fire does not count as protection.

## Suggested order
1. slas-slos — sets measurable targets and error budgets first, so every later mechanism has a goal to defend.
2. high-availability — shows how availabilities compose along a dependency chain and why the provider caps your nines.
3. fault-tolerance — the baseline per-call toolkit of timeouts, retry budgets, idempotency and fallbacks that the later patterns build on.
4. circuit-breakers — stops sending to a failing dependency, the first containment pattern layered on top of timeouts and retries.
5. bulkheads — caps aggregate resource use per dependency or workload, covering the window before a breaker trips.
6. backpressure-and-load-shedding — applies Little's law to overload, bounding queues and rejecting early to protect goodput.
7. tenant-level-quotas-and-noisy-neighbor-isolation — extends bounded capacity to fair division among tenants who share a provider key or GPU pool.
8. durable-workflow-execution — makes multi-step agent and approval processes survive crashes and deploys without repeating side effects.
9. multi-region-architecture — moves from component failure to region loss, latency and residency, where state placement decides what is lost.
10. disaster-recovery — defines RPO and RTO and the restore of state that redundancy cannot protect, such as indexes, weights and deleted data.
11. business-continuity — wraps DR in function-first planning for vendors, people, roles and communication during a disruption.
12. fmea — systematically lists and ranks failure modes, especially silent AI ones, to decide what to guard and test.
13. chaos-engineering-and-resilience-testing — verifies the earlier mechanisms by injecting the top FMEA faults under a bounded blast radius.

## Capstone
### Brief
Harden a multi-tenant document question-answering and drafting service for a B2B SaaS company that is
about to sign its first enterprise customers. The service has a chat endpoint that retrieves from a
vector store and calls a hosted LLM, a batch endpoint that re-embeds customer documents, and an agent
flow that drafts a reply, waits for human approval and then sends it through a CRM API. Today it runs in
one region on one provider key with one shared worker pool, and a single tenant's backfill has already
slowed everyone. Over a week, define SLIs and SLOs for availability, latency, answer quality and cost
per task; run an FMEA of the request path; then implement the fixes the top rows call for: a shared
client wrapper with timeouts, retry budgets and idempotency keys, a breaker and secondary-provider
fallback, per-dependency bulkheads, queue-wait-based load shedding, per-tenant token buckets with fair
queuing, and the approval flow as a durable workflow. Write the DR and continuity plan, including how
the vector index is restored or rebuilt and what happens if the provider is down for a day, and prove
the design with fault-injection experiments.

### Deliverables
- An SLO document with SLI definitions (including a quality SLI backed by an eval), 30-day targets, error-budget arithmetic, a burn-rate alert rule and a draft external SLA that is looser than the SLOs.
- An FMEA spreadsheet covering at least 15 failure modes across retrieval, model calls, tools and tenancy, scored S × O × D, with the top five mapped to specific mitigations in the code.
- Working code for the resilience layer: a client wrapper, a circuit breaker with a fallback provider, per-dependency semaphores sized with Little's law, an admission controller that sheds by queue wait and priority, per-tenant quotas, and the approval flow as a durable workflow.
- A DR and business-continuity runbook listing every stateful asset (database, vector index, prompt registry, secrets) with its RPO, RTO and restore-or-rebuild method, named roles, and the manual fallback mode.
- A resilience test report from at least four experiments (provider 429s and latency, CRM hang, noisy-tenant backfill, worker crash mid-workflow), each with steady-state hypothesis, blast radius, abort condition, measured result and follow-up fix.

### Rubric
- During a simulated noisy-tenant backfill, other tenants' p95 chat latency stays within the latency SLO, and per-tenant dashboards show only the offending tenant's queue wait and 429s rising.
- With the CRM API hanging, chat requests that do not need the CRM keep succeeding; the CRM bulkhead saturates, its breaker opens within the stated threshold, and no worker pool beyond that compartment is exhausted.
- Killing a worker mid-approval resumes the workflow at the same step with no second model call and no duplicate CRM send, shown by activity history and idempotency-key logs.
- Pool sizes, queue bounds and retry budgets are each justified in writing with Little's law or error-budget arithmetic, not chosen arbitrarily.
- A timed restore drill records actual RPO and RTO against the targets, including the measured time and cost to re-embed the vector index if it must be rebuilt.
- Every chaos experiment states its hypothesis and abort condition before it runs, at least one refuted hypothesis produced a code or config change, and the change was verified by re-running the experiment.
