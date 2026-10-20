---
id: disaster-recovery
module: reliability
name: Disaster recovery (RPO / RTO)
kind: concept
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - reliability/business-continuity
  - reliability/high-availability
  - reliability/multi-region-architecture
  - deployment/runbooks
  - data-architecture/vector-database-architecture
  - data-architecture/embedding-versioning-re-indexing-strategy
  - llmops-platform/rollback
  - security-governance-compliance/vendor-exit-strategy
sources:
  - Official docs: AWS Well-Architected Framework, Reliability Pillar
  - NIST SP 800-34 Rev. 1 — Contingency Planning Guide for Federal Information Systems
  - Kleppmann, 2017 — Designing Data-Intensive Applications
---

## TL;DR
Disaster recovery (DR) is the plan and machinery for restoring service after a failure that redundancy
cannot mask: a region loss, a deleted database, corrupted data, a compromised account. Two numbers
define it. **RPO** (recovery point objective) is how much data, measured in time, you may lose. **RTO**
(recovery time objective) is how long service may be down. Every DR design is a trade of cost against
those two numbers, and a DR plan that has not been rehearsed is a hypothesis, not a plan.

## Why it exists
High availability handles component failures with redundancy that is usually in the same place and
usually replicating in real time. That is exactly what makes it useless for a whole class of disasters.
Synchronous replication faithfully copies a `DROP TABLE`, a bad migration, or a ransomware encryption
to every replica within milliseconds. A regional outage takes every zone down together. A leaked cloud
credential can delete the replicas along with the primary. For these, "we have replicas" is not an
answer; you need a copy that is separated in time (point-in-time history), in place (another region
or account), and in trust (immutable, separately permissioned).

The naive alternative is nightly backups and good intentions. It fails on three counts. Nobody has
tested the restore, so it takes a day and surfaces a missing dependency. The backup is up to 24 hours
old, so a whole day of conversations, approvals or orders vanishes. And the backup covers the
database but not the vector index, the prompt registry, the fine-tuned adapter weights or the
secrets, so the "restored" system does not behave like the one that was lost.

AI systems raise the stakes because they carry state that is expensive to recreate. A vector index
over millions of chunks can be rebuilt from source documents, but rebuilding means re-embedding
hundreds of millions of tokens, which has a cost in money and hours that must be counted in RTO.
Fine-tuned weights that were never checkpointed to durable storage are simply gone.

## How it works
### The two numbers, precisely
Picture a timeline. The disaster strikes at T. The most recent recoverable state is at some point
before T; the gap is the **recovery point**, and the RPO is the maximum gap you have agreed to
tolerate. Service comes back at some point after T; that gap is the **recovery time**, and the RTO is
the maximum you have agreed to tolerate. The objectives are promises set with the business; the
measured results of a real recovery or drill are the actuals (some teams write them RPA and RTA), and
the gap between objective and actual is the finding.

Both numbers follow from the mechanism:
- RPO is bounded by **how often state is copied away**. Nightly snapshot: up to 24 h. Continuous
  log shipping (write-ahead log archived every minute): about a minute. Synchronous cross-region
  replication: approximately zero, at the cost described in multi-region.
- RTO is a **sum**: time to detect, time to decide, time to provision or promote, time to restore
  data and replay logs, time to re-point traffic, time to verify. Teams who only measure the restore
  step are usually off by an hour of detection and decision.

### The four strategies
Named roughly as the AWS guidance does, in rising cost and falling RPO/RTO:
1. **Backup and restore.** Periodic backups to another region or account; rebuild infrastructure from
   code on demand. RPO hours, RTO hours to a day. Cheapest; adequate for internal tools.
2. **Pilot light.** Data is continuously replicated to the DR region; the minimum core (database
   replica, container images, secrets) is always on, everything else is created at failover. RPO
   minutes, RTO tens of minutes to hours.
3. **Warm standby.** A scaled-down but fully functional copy runs continuously and is scaled up on
   failover. RPO seconds to minutes, RTO minutes.
4. **Multi-site active-active.** Both sites serve traffic; failover is a routing change. RPO near
   zero with synchronous replication, RTO near zero. This is the multi-region pattern.

### Mechanisms that make the numbers real
- **Point-in-time recovery** from a base snapshot plus an archived log lets you restore to just before
  a bad write, *because* a replica cannot un-apply a mistake but a log can be replayed up to a chosen
  second.
- **Immutable, isolated backups**: object-lock or vault policies, a separate account with separate
  credentials, *because* a disaster that includes a compromised identity must not reach the backup.
- **Infrastructure as code** so the DR environment can be created, not hand-built, during the worst
  hour of the year.
- **Restore drills** on a schedule, timed, with the resulting RTA and RPA recorded. The drill also
  catches the quiet killers: a backup job that has been silently failing, a missing KMS key grant.
- **Runbooks** that name who decides, how failover is triggered, and how failback happens, including
  the reconciliation of writes that landed after the recovery point.

### The AI-specific inventory
List every store, decide for each whether it is *restored* or *rebuilt*, and estimate the time:
conversation and session databases (restore); vector indexes (restore from index snapshot in minutes,
or rebuild by re-embedding in hours); source documents in object storage (usually versioned, near-zero
RPO); prompt and config registries (in git, trivially recoverable, but only if the deploy pipeline
reads from git and not from a mutable store); fine-tuned weights and adapters (copied to a second
region as artefacts, since retraining is not a recovery plan); agent checkpoints and workflow
histories (restore, and accept that runs after the recovery point replay from an earlier step); eval
datasets and golden sets (restore; losing them silently breaks release gates). The embedding model
version must be recorded with the index, or a rebuild produces an index the query path cannot use.

## Example
A worked RTO/RPO budget for a RAG assistant (all figures illustrative orders of magnitude).

The business sets RPO 15 minutes and RTO 4 hours. State: a 500 GB Postgres database (sessions,
tenants, audit log), a vector index of 2 million chunks averaging 500 tokens each, 300 GB of source
documents in versioned object storage, prompts and configs in git, one fine-tuned adapter.

Current plan: nightly Postgres snapshot copied cross-region, no index snapshot, adapter stored only on
the training machine. Worst-case RPO is 24 hours, failing the 15-minute target. Estimated RTO: detect
and decide 30 min, provision from infrastructure code 20 min, restore 500 GB snapshot roughly 45 min,
rebuild the index by re-embedding 2M × 500 = 1 billion tokens, which at provider rate limits in the
low millions of tokens per minute is on the order of eight hours, then verify 15 min. Total about
10 hours, failing RTO, and the adapter is unrecoverable.

Revised plan: enable continuous WAL archiving to the DR region with a one-minute archive interval
(RPO about 1 minute, meets 15), snapshot the vector index to the DR region every six hours (an index
restore of roughly 20 minutes replaces the eight-hour rebuild; index RPO of 6 h is acceptable because
documents since then can be incrementally re-embedded in the background), copy the adapter and its
training manifest to artefact storage in both regions. New RTO estimate: 30 + 20 + 45 + 20 + 15 ≈ 130
minutes, inside 4 hours, with a quarterly timed drill to replace the estimates with actuals.

## Trade-offs
- Shorter RPO means more frequent or continuous replication, which costs bandwidth, storage and
  sometimes write latency; it flips to "nightly is fine" when the data is cheaply re-derivable.
- Shorter RTO means more infrastructure kept running idle (warm standby, active-active); it flips to
  backup-and-restore when an hour of downtime costs less than the standby's monthly bill.
- Rebuilding derived state such as vector indexes saves storage but spends hours and provider tokens
  during the worst moment; snapshotting the index costs storage every day to save that one day.
- Immutable, separately-owned backups protect against compromised credentials and bad automation but
  make routine cleanup and retention changes slower and more bureaucratic.
- Automated failover shortens RTO but can trigger on false alarms and cause data loss or split brain;
  a human decision step adds minutes and removes that class of error.

## Pitfalls
- Backups that are never restored: the job has been failing for months or the restore needs a key
  nobody has. The symptom is discovering it during the disaster; the cure is timed drills.
- Replication mistaken for backup: a bad migration or deletion is faithfully copied to every replica
  within seconds, and no point-in-time copy exists to roll back to.
- Recovering the database but not the rest of the inventory, so the restored assistant answers from an
  empty index or an older prompt and the incident continues as a quality incident.
- Backups in the same account and region as production, reachable by the same credentials; one leaked
  key or one wrong Terraform destroy removes both.
- Measuring RTO as the restore step only, forgetting detection, decision and traffic cut-over; the
  drill shows two hours where the plan said thirty minutes.

## When to use
Every production system needs a DR plan; the choice is which strategy tier, and that follows from the
business impact analysis: what an hour of downtime and an hour of lost data cost for each capability.
Pick the cheapest tier that meets the agreed RPO and RTO, write down the inventory of state and whether
each item is restored or rebuilt, automate the restore, and drill it at least quarterly with the
actuals recorded. Reach for cross-region or cross-account immutable backups the moment the data has
compliance value or the threat model includes compromised credentials. Day to day, DR shows up in
design reviews as "where does this new store get backed up and who restores it". In interviews, the
expected depth is deriving RPO from the replication mechanism and RTO as a sum, and explaining why
replicas are not backups.

## Role lens
- **Engineer:** Adds every new store to the backup inventory, records embedding and model versions with
  index snapshots, and writes the restore script that the drill actually runs.
- **Architect:** Chooses the DR tier per capability from the agreed RPO/RTO, owns the state inventory
  and the restore-versus-rebuild decision for derived data like indexes.
- **FDE:** Answers the customer's RPO/RTO questionnaire with measured drill results, and aligns the
  deployment's backups with the customer's own retention and residency requirements.

## Check yourself
**Q:** A team says "our Postgres has a synchronous replica in another zone, so our RPO is zero and we
do not need backups." Where does this reasoning fail, and what would you add?
**A:** The replica protects against the primary's hardware or zone failing, which is high availability,
not disaster recovery. It replicates every write, including the destructive ones: a mistaken
`DELETE`, a bad migration, an application bug corrupting rows, or an attacker with the database
credential. After any of those, both copies are equally wrong within milliseconds, and the RPO for
that class of disaster is effectively infinite because there is no earlier state to return to. A
regional outage or an account compromise can also take both. I would add base snapshots plus
continuous WAL archiving to a separate region and account with immutable retention, giving
point-in-time recovery to any second before the bad event, and a timed quarterly restore drill so the
RTO is a measured number. I would also extend the inventory beyond Postgres to the vector index,
prompt registry and model artefacts.

## Quiz
### Q: A system takes a full backup every night at 02:00. Data is destroyed at 13:00. What is the recovery point loss, and which objective does it test?
- [ ] 13 hours, testing the RTO
- [x] 11 hours, testing the RPO
- [ ] 24 hours, testing the RTO
- [ ] Zero, because the backup can be replayed forward to 13:00
> The last recoverable state is the 02:00 backup, so 11 hours of writes are lost; that gap is the
> recovery point. A full backup alone cannot be rolled forward; that requires archived logs.

### Q: Why is a synchronous database replica not a substitute for backups?
- [ ] Replicas are always slower to restore than backups
- [ ] Replicas cannot be placed in another availability zone
- [x] Replication copies destructive writes and corruption immediately, so there is no earlier state to recover to
- [ ] Replicas increase RPO because they lag the primary
> A replica is a live copy of the present, including mistakes. Point-in-time recovery needs history:
> snapshots plus logs that let you stop just before the bad write.

### Q: For a RAG system, why should the vector index be snapshotted even though it can be rebuilt from the source documents?
- [ ] Source documents cannot be backed up reliably
- [ ] Rebuilt indexes return different search results from the original
- [x] Rebuilding means re-embedding every chunk, which can take hours and provider spend, directly inflating RTO
- [ ] Vector databases do not support restoring from a snapshot
> Rebuild is a valid recovery path, but its duration is part of the recovery time. A snapshot restore
> turns hours of re-embedding into minutes, usually for modest storage cost.
