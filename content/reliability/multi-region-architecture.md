---
id: multi-region-architecture
module: reliability
name: Multi-region architecture
kind: pattern
difficulty: advanced
est_minutes: 17
status: reviewed
related:
  - reliability/high-availability
  - reliability/disaster-recovery
  - reliability/durable-workflow-execution
  - reliability/backpressure-and-load-shedding
  - system-design/data-consistency
  - system-design/distributed-systems
  - security-governance-compliance/data-residency
  - infra-capacity-economics/api-provider-quotas-and-regional-limits
sources:
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Abadi, 2012 — Consistency Tradeoffs in Modern Distributed Database System Design (PACELC)
  - Helland, 2007 — Life beyond Distributed Transactions
  - Official docs: AWS Well-Architected Framework, Reliability Pillar
---

## TL;DR
A multi-region architecture runs a system in two or more geographically separate cloud regions to
survive a whole-region outage, cut latency for distant users, and satisfy data-residency rules. The
hard part is not duplicating compute but deciding where each piece of state lives and how it is
replicated: synchronous replication taxes every write with a cross-region round trip, asynchronous
replication loses recent writes on failover. Start multi-AZ in one region; go multi-region only for
a reason you can name.

## Why it exists
A single region, even spread across availability zones, shares a control plane, a set of regional
services and a blast radius. Regional incidents are rare but they do happen, they take down every zone
at once, and the first thing a customer's procurement team asks after one is "what is your multi-region
story". Redundancy inside the region cannot answer that, because it is correlated failure by design.

Physics is the second reason. Light in fibre travels roughly 200,000 km/s, about 5 ms per 1,000 km one
way, so a user 6,000 km from your only region pays at least ~60 ms of round trip before any work
happens, and in practice more after routing. For a chat product whose time-to-first-token budget is a
few hundred milliseconds, that is a visible slice.

Law is the third. Data-residency obligations require that certain customers' data is stored and
processed inside a jurisdiction, and for an AI product "processed" includes the prompt sent to the
model, so the inference endpoint must also be in-region.

AI systems add a fourth, practical reason: model providers publish quotas and model availability per
region. A second region can double your effective rate limit and give you a place to run when one
region's capacity for a given model is exhausted. The naive approach, "deploy the same Terraform to
two regions and put DNS in front", fails the moment two regions both accept writes to the same
record, or a failover promotes a replica that is seconds behind. Compute is easy to duplicate; truth
is not.

## How it works
### Three topologies, in rising cost
1. **Active-passive.** One region serves; the other holds replicated data and enough infrastructure to
   take over (from "pilot light" to "warm standby"). This is disaster recovery with a shorter clock,
   covered in that lesson; the standby's real risk is that it is never exercised.
2. **Active-active, partitioned.** Every region serves traffic, but each tenant, user or shard has a
   single *home* region that owns its writes; the other regions hold async replicas and can take over
   ownership on failover. This is the workable default for stateful products, *because* it keeps
   every write single-leader and avoids write conflicts entirely while still using all regions.
3. **Active-active, shared.** Any region accepts any write to any record. This needs either a
   globally consistent database that pays cross-region latency on commits or multi-leader replication
   with conflict resolution. Choose it only for data that is genuinely written from everywhere.

### State is the whole problem
A write replicated **synchronously** is durable in both regions before the client gets an
acknowledgement, so failover loses nothing (RPO ≈ 0), *but* every write now waits a cross-region
round trip, commonly tens of milliseconds within a continent and ~100 ms or more across an ocean
(illustrative orders of magnitude). A write replicated **asynchronously** returns immediately, and the
replica trails by a lag that is usually sub-second but spikes under load; failover during a spike
loses that window. PACELC names the trade honestly: under a partition you choose availability or
consistency, and even without one you choose latency or consistency.

For partitioned active-active, the mechanics are: route each request to the tenant's home region;
replicate asynchronously to the others; on failover, promote the replica, re-home the tenant, and
accept the lag as lost writes to reconcile later. For shared active-active, conflicts must be resolved
with per-key ownership, CRDTs for data that merges naturally (counters, sets), or last-writer-wins
where losing an update is tolerable. Helland's advice still holds: design entities so that each
transaction touches one entity in one place.

### Routing and failover
Latency-based or geo DNS sends users to the nearest healthy region, but DNS answers are cached and
clients routinely ignore short TTLs, so DNS failover takes minutes. Anycast global load balancers move
traffic in seconds and are preferred where available. The dangerous moment is the decision itself:
automatic failover of a *database* on a flaky health signal produces split brain, two primaries both
accepting writes. Mature teams automate stateless failover and keep data-tier failover behind a
human-confirmed runbook step or a strict fencing mechanism.

The failover path must also be **statically stable**: it must work using only what already runs in
the surviving region. A runbook that needs the failed region's CI/CD system, its secrets manager, a
DNS or IAM change routed through a control plane hosted there, or a container registry that only
exists there, fails exactly when it is needed. The same logic favours pre-provisioned capacity over
"scale up on failover", *because* during a regional event every other customer of that cloud is
calling the same scaling APIs in the same surviving regions at the same moment.

### Capacity is part of the design
If two regions each run at 70% utilisation and one dies, the survivor needs 140%. Either run each
region at or below 50% (paying for idle capacity) or plan explicit load shedding on failover:
which tenants and which workloads keep running. The same arithmetic applies to provider quotas, which
do not move with your traffic.

### What is different for AI systems
- Model availability and rollout timing vary by region; the same model name may not be live in both
  regions on the same day, so pin versions and test in both.
- Embeddings must come from the same model and version in every region, or a replicated vector index
  becomes incoherent. Replicate the index artefact or the source documents plus a rebuild pipeline,
  and know how long rebuild takes.
- Prefix caches and KV caches are per-server; a failed-over region starts cold and pays higher
  time-to-first-token until warm.
- Residency pins both storage and inference: a residency-bound tenant whose home region fails must
  *degrade in place*, not be served from a foreign region.
- Long-running agent runs in flight die with the region unless their progress is checkpointed in a
  replicated store, which is why durable execution and multi-region go together.

## Example
A worked failover for a two-region, tenant-partitioned agent platform (all numbers illustrative).

Setup: regions `us-east` and `eu-west`, each with a full stateless tier, a Postgres primary for its
home tenants and an async replica of the other region's tenants with typical lag under 2 s. Agent
checkpoints live in the same database. Each region runs at about 40% of capacity. The provider quota
is 1,000 requests per minute per region, and EU tenants are residency-bound.

Incident: `us-east` becomes unreachable. The global load balancer marks it unhealthy after ~60 s of
failed checks and routes all users to `eu-west`. The on-call engineer confirms the region, not the
checks, is the problem and runs the promotion step; `eu-west`'s replica of US tenants becomes primary
at T+6 min. Writes that `us-east` accepted in its last ~2 s before the loss never replicated: the RPO
for US tenants is about 2 s. Those writes are stranded in `us-east` until it returns; clients that
hold their idempotency keys can safely resubmit now, and anything else is reconciled from
`us-east`'s log at failback. Agent runs that were mid-step in `us-east` resume in `eu-west` from their last
checkpoint, re-executing only the interrupted step.

Capacity: `eu-west` now carries both regions' traffic, 40% + 40% = 80% of one region, so compute
holds. The provider quota does not: EU's 1,000 rpm must now cover roughly double the demand, so the
platform sheds batch summarisation jobs first and keeps interactive chat. EU tenants are unaffected
and remained in-region throughout. Had the failure gone the other way, residency-bound EU tenants
would have been placed in a read-only degraded mode rather than served from `us-east`.

Failback: when `us-east` returns, replication is reversed, the reconciliation log is replayed, and
US tenants are re-homed during a low-traffic window after the replica catches up.

## Trade-offs
- Region loss survival and lower latency for distant users, paid for with duplicated infrastructure and
  idle headroom; it stops paying off when all users and regulators sit in one geography.
- Synchronous replication gives zero data loss but taxes every write with a cross-region round trip;
  flip to asynchronous when write latency is user-visible and a few seconds of loss is survivable.
- Partitioned active-active avoids write conflicts but makes cross-tenant features (global search,
  shared workspaces) hard; shared active-active enables them at the price of conflict resolution.
- Automatic failover shortens outages but risks split brain on false positives; manual data-tier
  promotion is slower and safer, and becomes the better choice as the data's value rises.
- A second region doubles provider quota and model-availability options, yet model and prompt
  behaviour must now be validated in two places, doubling the eval and rollout work.

## Pitfalls
- A standby region that was never failed over to: expired credentials, missing secrets, a model not
  enabled in that region. The symptom is discovering it during the real incident.
- Replicating the database but not the vector index, prompt registry or feature flags, so the
  failed-over region answers from stale or empty retrieval and behaves differently.
- Assuming DNS TTLs are honoured; traffic keeps arriving at the dead region for minutes after
  "failover" and users see errors you believe are impossible.
- Treating replication lag as zero: dashboards show success, but customers report lost messages or
  duplicated agent actions from the seconds before failover.
- Sizing each region for its own load; after failover the survivor overloads and a regional outage
  becomes a global one, visible as a second wave of errors minutes after the first.
- A failover procedure with a hidden dependency on the failed region (its CI/CD runner, secrets store
  or registry); drills that only fail over while the source region is healthy never reveal it, so the
  symptom is a runbook step that hangs during the real event.

## When to use
Go multi-region when you can name the driver: a contractual requirement to survive region loss, a
latency budget that one region cannot meet for a material user base, a residency rule, or provider
quota you cannot obtain in one region. Otherwise multi-AZ plus a tested disaster-recovery plan is
cheaper and usually adequate. If you do go, start with partitioned active-active and asynchronous
replication, and reserve synchronous or globally consistent stores for the few records that truly
need them. Measure replication lag (as a distribution, since its tail is your real RPO), failover time
and survivor utilisation, and rehearse failover on a schedule, ideally by cutting the source region
off rather than politely draining it. In interviews, the signal of depth is talking about where writes are owned and what is
lost on failover, not about load balancers.

## Role lens
- **Engineer:** Makes every service region-aware (home-region routing, region-tagged idempotency keys,
  checkpointed long runs) and runs the failover rehearsal that proves the second region works.
- **Architect:** Decides the topology, which data is single-leader versus replicated, the consistency
  model per store, and the capacity and quota headroom each region must hold for failover.
- **FDE:** Maps the customer's residency and latency constraints to tenant home regions, and sets
  expectations about what degrades, rather than moves, when a residency-bound region fails.

## Check yourself
**Q:** A customer wants an AI assistant in `us-east` and `eu-west` with "no data loss on failover" and
"no added latency". Both are stated as hard requirements. How do you respond, and what design do you
propose?
**A:** The two requirements conflict for any write: zero loss on region failure requires the write to
be durable in both regions before acknowledgement, which costs a cross-region round trip, tens of
milliseconds at best, on every write. So I would make the trade explicit and split the data. For the
small set of records where loss is unacceptable, such as billing events or approval decisions, use
synchronous replication or a globally consistent store and accept the latency on those operations
only. For high-volume, user-visible writes like conversation turns and agent checkpoints, replicate
asynchronously with sub-second lag, publish the measured RPO, and make the client idempotent so the
few seconds of lost writes can be replayed. Partition tenants by home region to avoid write conflicts,
pin residency-bound tenants to degrade in place, and rehearse the failover so the numbers are measured
rather than promised.

## Quiz
### Q: In a partitioned active-active design, why does each tenant have a single home region for writes?
- [ ] To reduce storage costs by keeping only one copy of each tenant's data
- [x] So every record has one leader at a time, eliminating cross-region write conflicts while all regions still serve traffic
- [ ] Because cloud providers only permit one writable database per account
- [ ] To guarantee that failover never loses any writes
> Single-leader-per-tenant keeps writes conflict-free without global coordination. Data is still
> replicated to other regions; failover can still lose the replication lag window.

### Q: What is the main risk of fully automatic failover for a cross-region database primary?
- [ ] It makes the replication lag longer because the replica must be verified first
- [ ] It violates data-residency rules in every jurisdiction
- [x] A false-positive health signal can promote the replica while the old primary is still accepting writes, producing split brain
- [ ] It prevents the application tier from also failing over
> Health checks can fail for reasons other than region death. Two primaries both accepting writes
> diverge, and reconciling them is far worse than a few minutes of human-confirmed failover.

### Q: Two regions each run at 70% utilisation. One fails. What is the most likely outcome without a prior plan?
- [ ] The survivor serves all traffic normally because compute autoscaling is instant
- [x] The survivor is driven to 140% of its capacity and overloads, turning a regional outage into a global one
- [ ] Traffic automatically waits in DNS until the failed region returns
- [ ] Provider quotas shift to the surviving region to absorb the load
> Failover moves demand, not capacity. Either run each region at or below 50% or decide in advance
> which workloads to shed, because autoscaling and provider quotas do not react in time.
