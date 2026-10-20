---
id: llmops-platform
title: LLMOps & AI platform
---

## Overview
This module covers what it takes to change an LLM application safely and repeatedly once it is in
production and more than one team depends on it. The unit of release is not a container image but a
bundle: code, prompt version, model version or provider snapshot, decoding parameters, retrieval index
and guardrail policy, any one of which can change behaviour without touching the others. The module
builds the system of record for that bundle first: experiment tracking for runs and lineage, a model
registry and a prompt registry that both split immutable versions from mutable aliases. It then builds
the evidence: a shared evaluation framework, a tiered golden-dataset portfolio, and reproducible runs
with manifests and a measured noise floor. CI/CD, release gates, shadow and canary rollouts and rollback
turn that evidence into controlled exposure. Monitoring, drift detection, an observability stack and a
lifecycle and deprecation policy keep the bundle honest after release, while the world, the provider
and the corpus move underneath it. The last two lessons step back to platform shape: a control plane
that publishes config snapshots to a fast, independent data plane, and the line between what a central
platform team owns and what product teams own. Afterwards you can design a release pipeline where
promotion and rollback are audited pointer moves backed by statistical evidence, explain why a prompt
edit is a production change, and lay out the platform for an organisation with many AI teams. It
matters most for AI Engineers and Architects who run shared AI infrastructure, and for FDEs who must
show a customer how changes reach their tenant.

## Why it matters
- **Engineer:** You will wire prompts and models into registries, write the eval-based CI ladder with content-addressed caching, implement a paired non-inferiority gate, emit traces that carry the resolved bundle version, and make rollback a single alias move you have rehearsed.
- **Architect:** You decide what the release unit is, where the control plane ends and the data plane begins, which signals gate a promotion, how provider sunsets and embedder upgrades are migrated, and which platform pieces are mandatory and central versus federated to product teams.
- **FDE:** Customers will ask how a prompt or model change reaches their tenant, what evidence backs it, how fast you can undo it and how you will notice quality slipping; you need to show the gate report, the canary plan, the rollback drill and per-tenant monitoring on their traffic.

## Mental model
Picture one immutable, content-hashed bundle moving along a conveyor of environments, with a pointer
(an alias such as `champion` or `canary`) marking where it is live. Everything left of production
produces evidence about the bundle: tracked runs, a versioned golden portfolio, reproducible eval runs
with manifests, and a gate that compares the candidate to the champion with a confidence interval and
per-slice floors. Crossing into production is never a rebuild, only a pointer move, first to a shadow
copy of traffic, then to a sticky canary slice, then to everyone. Everything right of production
watches the bundle against the world: monitoring slices quality proxies by bundle version, drift
detection asks whether inputs, answers or a pinned component moved, and the lifecycle policy schedules
the day a version must leave. Any red signal moves the pointer back to the last validated bundle. The
control plane owns the pointers and the evidence; the data plane only reads a snapshot and serves it.

## Suggested order
1. experiment-tracking — the run data model and lineage that every registry entry and eval score points back to.
2. model-registry — introduces the immutable-version versus mutable-alias split on the artifact that is hardest to version.
3. prompt-registry — applies the same split to prompt text and its model settings, completing the bundle's components.
4. evaluation-frameworks — the shared datasets, targets, scorers, runner and results store that make eval scores comparable.
5. golden-dataset-strategy — turns the dataset interface into a tiered, owned, refreshed portfolio sized to the regressions it must detect.
6. reproducible-evaluations — pins every drifting part of an eval in a manifest and measures the noise floor gates will rely on.
7. ci-cd-for-prompts-and-models — assembles registries and evals into a pipeline that promotes one tested bundle through a risk-sized ladder.
8. evaluation-gates-for-production-releases — formalises the promotion decision as floors, a paired non-inferiority test and three outcomes.
9. canary-shadow-deployments — extends evidence to live traffic with bounded exposure once offline gates pass.
10. rollback — makes the reverse pointer move fast and safe, including the components that destroy their own undo path.
11. observability-stack — the trace, metric and payload pipeline that post-release signals and eval loops depend on.
12. model-monitoring — layers operational, proxy and late quality signals over that stack, sliced by bundle version and tenant.
13. drift — separates what moved, how it moved and whether it mattered, and feeds confirmed shifts back into the golden set.
14. model-lifecycle-and-deprecation-policy — plans the exit of versions and provider snapshots as dated, evaluated migrations.
15. ai-control-plane-vs-data-plane — reframes registries, aliases and policies as a control plane publishing snapshots to a serving path.
16. centralized-vs-federated-ai-platform — decides which of all these pieces one platform team owns and which product teams own.

## Capstone
### Brief
Build a release platform for a small retrieval-augmented support assistant that answers questions about
a fictional company's returns and billing policy. The assistant runs behind a minimal gateway that
resolves a `champion` and a `canary` alias from a local control-plane store into a bundle: a prompt
version from a prompt registry, a model version (a provider snapshot or a small local model) from a
model registry, decoding parameters and an index version. The gateway caches the resolved snapshot and
keeps serving it if the control plane is stopped. Every eval run is tracked with a manifest that pins
dataset, bundle, judge and rubric versions, and runs over a tiered golden portfolio with smoke, full,
slice and regression tiers. A CI pipeline runs the smoke tier on every prompt change and the full tier on
release candidates, then applies a gate with safety and schema floors, per-slice floors and a paired
non-inferiority test against the champion. Passing candidates go to a shadow run on replayed traffic, then
a sticky 10% canary with guard metrics and automatic rollback. Traces carry the resolved bundle hash, and
a monitoring view shows proxies and sampled judge scores by version, with a drift check on intent and
language mix. Finally, deprecate the old model version through the lifecycle states with an inventory of
which bundles still pin it.

### Deliverables
- A runnable repository with the gateway, control-plane store, prompt and model registries, tracked eval runner and golden portfolio, started with one command.
- A CI configuration that runs the eval ladder with cached results and writes a gate report showing per-slice deltas, the confidence interval and the pass, block or needs-review verdict.
- A deployment script that performs shadow, canary ramp and rollback as audited alias moves, plus a recorded rollback drill with its measured time.
- A monitoring dashboard or notebook that slices latency, cost, schema failures, refusals and judge scores by bundle version and tenant, with a drift report comparing two traffic windows.
- A two-page platform write-up covering the control/data-plane split, the deprecation plan for the retired model, and which components would be central versus team-owned.

### Rubric
- A deliberately degraded prompt that raises the aggregate score but breaks the refunds slice is blocked by the gate, and the report names the slice and the confidence interval that caused the block.
- Re-running any recorded eval from its manifest reproduces the verdict, and a comparison across mismatched dataset or judge versions is refused rather than reported.
- Every production trace resolves to one bundle hash, and moving the alias back restores the previous bundle on all replicas within the measured rollback time, without a redeploy.
- Stopping the control-plane store leaves the gateway serving the last snapshot with zero failed requests, and alias changes during the canary are visible in an audit log.
- Monitoring shows the canary arm separately from the champion, a seeded language shift in replayed traffic appears in the drift report, and the failing examples become new regression items.
- CI rejects a new bundle that pins the deprecated model version, and the inventory lists every bundle and consumer still depending on it before the freeze date.
