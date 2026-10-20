---
id: data-retention-and-deletion-architecture
module: data-architecture
name: Data retention & deletion architecture
kind: pattern
difficulty: core
est_minutes: 17
status: reviewed
related:
  - data-architecture/data-lineage
  - rag-retrieval/freshness-document-deletion-index-consistency
  - agents/memory-retrieval-update-deletion-policies
  - security-governance-compliance/data-classification
  - security-governance-compliance/compliance-architecture
  - security-governance-compliance/data-residency
  - safety-security/pii-redaction
  - serving-inference/caching
sources:
  - Regulation (EU) 2016/679 (GDPR), Article 17 — Right to erasure
  - Kleppmann, 2017 — Designing Data-Intensive Applications
---

## TL;DR
An LLM application copies user data into far more places than a conventional service: prompt logs,
traces, vector indexes, caches, eval sets, fine-tuning data, vendor-side logs and model weights. Retention
and deletion architecture is the design that knows every one of those places, assigns each a retention
class and a time-to-live, and can execute a deletion request across all of them with proof. It has to be
designed in, because a deletion you cannot propagate is a promise you cannot keep.

## Why it exists
Privacy law (GDPR's right to erasure, similar rights under CCPA and other regimes) and most enterprise
contracts require that personal or customer data is kept no longer than needed and deleted on request
within a bounded time (under GDPR, a response is due without undue delay and within one month, extendable
by two further months for complex requests). For a classic web application the data lives in a database with backups, and
deletion is a `DELETE` plus a backup expiry policy. An AI application breaks that model.

Trace the life of one support conversation. The user's message, containing their name and order number,
is written to the application database; copied into a prompt; sent to a model vendor whose API logs it;
recorded by the observability stack as a trace with full prompt and completion; embedded and stored as a
memory vector for future personalization; cached as a semantic-cache entry keyed on its embedding;
sampled into a weekly eval set; exported into a fine-tuning dataset; and, after the fine-tune, partially
encoded in model weights. Setting aside the transient prompt, that is eight persisted copies in eight
systems, typically owned by several different teams, with at least one (the vendor's logs) outside the
company, and two if the observability stack is a hosted service.

The naive alternative, "delete from the primary database and let the rest expire", fails because several
of those copies have no expiry (the vector index, the fine-tuning dataset, the weights) and because the
ones that do often expire too late to satisfy a request deadline. The second naive alternative, "never
store anything", fails because observability, evaluation and improvement all depend on retaining
interactions. The architecture has to make retention a per-copy decision and make deletion a traversal
that reaches every copy.

## How it works
### Inventory and classify every copy
Start with a map of where data lands. For each store, record what classes of data it holds (user content,
derived content such as embeddings and summaries, operational metadata, aggregate metrics), whether it
is keyed by a subject identifier, and who owns it. The lineage system supplies most of this map for
derived data; the rest (vendor logs, caches, backups) is documented by hand. Classification matters
*because* retention is set per class: an aggregate latency metric can live for years, a raw prompt with a
customer's address should not.

### Retention classes and TTLs
Assign each store a retention policy: a time-to-live after which data is deleted automatically, or
"retain while the relationship exists". Derived data inherits the shortest TTL of its inputs *because*
an embedding of a deleted message is still a representation of it. Enforce TTLs mechanically: database
partitions dropped by date, object storage lifecycle rules, vector collections partitioned by ingestion
month, trace stores with built-in retention. Policy that depends on someone remembering to run a cleanup
is not a policy.

### Retention minimums and legal holds
Retention is not only a maximum. Some data must be kept for a minimum period (financial records, security
audit logs, records a regulator requires), and litigation can place a *legal hold* on data that would
otherwise expire or be erased. GDPR's erasure right itself has exceptions, including data needed to comply
with a legal obligation or to establish or defend legal claims. So each retention class needs both a
floor and a ceiling, and the deletion job must consult a hold register before deleting: a held record is
skipped and reported as held, not deleted and not silently ignored. Resolving conflicts between the
floor, the ceiling, a hold and an erasure request is a policy decision for counsel; the architecture's
job is to make the rule explicit and executable rather than to decide it.

Vendors are a store too. Model API providers typically retain request logs for a period for abuse
monitoring unless a zero-retention arrangement is in place; the exact terms vary by provider and
contract, so the architecture must record what the agreement is and whether it covers the data classes
you send.

### Deletion as a propagated job
A deletion request (one subject, one tenant, or one document) becomes a job that:
1. Resolves the subject to identifiers in every store, using lineage to find derived artifacts: the
   document's chunks, their vectors, cache entries that embedded them, eval examples sampled from them,
   fine-tuning rows that include them.
2. Issues deletes to each store, idempotently, with retries, and records a per-store receipt.
3. Verifies by re-querying each store for the identifiers.
4. Writes a tombstone so re-ingestion from a still-live source does not resurrect the data, and so a
   later audit can show when and where deletion happened.

The job is idempotent and resumable *because* it fans out across many systems and some will be down.
Partial completion must be visible, not silently treated as done.

### Store-specific mechanics
- **Vector indexes:** deletion must be keyed by metadata (`subject_id`, `document_id`), so those fields
  must exist on every vector. Many ANN implementations implement deletion as a soft-delete marker until
  compaction, segment merge or rebuild, which means the vector may remain on disk after it stops being returned; treat
  the index as a derived store that can be rebuilt from the source of truth, and schedule rebuilds.
- **Caches:** semantic and exact-match response caches hold prompts and completions; key them so that
  entries carrying a subject's data can be found, or give them a TTL short enough that deletion
  deadlines are met by expiry alone.
- **Traces and logs:** either redact PII before the trace is written, so the trace store holds nothing
  deletable, or keep traces keyed by subject with a retention short enough to expire within the deadline.
  Redaction at write time is the more robust choice.
- **Backups and immutable stores:** you cannot delete one row from a backup. Two patterns are common.
  The simpler is a short backup rotation plus a deletion log: backups age out within a defined window,
  and any restore replays the recorded deletions before the data goes live again. The stronger is
  crypto-shredding: encrypt each subject's (or tenant's) data under its own key, and delete the key. The
  ciphertext remains but is unrecoverable, which is widely treated as meeting the intent of erasure for
  backups and append-only logs; regulators have not uniformly codified that view, so confirm the
  approach with your privacy counsel.
- **Summaries and memories:** an LLM-written summary or agent memory derived from a conversation is a new
  document that may blend several people's data. It cannot be filtered row by row; it must record which
  conversations and subjects it was derived from, and erasing one subject means deleting the summary or
  regenerating it from the remaining inputs.
- **Training datasets:** rows must carry subject or tenant identifiers so they can be removed, and the
  dataset's snapshot digest changes, which flows into provenance.
- **Model weights:** data that has been trained into a model cannot be deleted from it; machine
  unlearning techniques exist but are not reliable enough to certify. The architectural answers are to
  keep deletable data out of training (redact, or train only on data with consent terms that survive
  deletion requests), to time-box models so a retrain on the cleaned dataset happens within the deadline,
  or to accept retraining as the deletion mechanism and budget for it.

### Tenant-level deletion
Enterprise offboarding deletes everything for a tenant. Partitioning every store by tenant (separate
collections, prefixes or keys) turns this from a filtered scan into dropping a partition, which is both
faster and more verifiable. Per-tenant encryption keys make the backup problem disappear for the same
reason as crypto-shredding.

### Proof
The output of a deletion job is a report: request id, a keyed reference to the subject, each store
touched, receipts, verification results, and the tombstone id. Regulators and customers ask for this, and the job is the only thing that
can produce it accurately.

## Example
A deletion orchestrator that fans out over registered stores, retries, verifies, and produces a receipt.
Stores are small stand-ins; the shape of the job is what matters.

```python
import hashlib, hmac, time
from dataclasses import dataclass, field

TOMB_KEY = b"from-a-secret-store"                       # tombstones must not hold the raw id
def tomb_id(subject): return hmac.new(TOMB_KEY, subject.encode(), hashlib.sha256).hexdigest()

@dataclass
class Store:
    name: str
    rows: dict = field(default_factory=dict)           # id -> {"subject": ..., ...}
    flaky: int = 0                                     # simulate transient failures
    def find(self, subject):
        return [i for i, r in self.rows.items() if r["subject"] == subject]
    def delete(self, ids):
        if self.flaky: self.flaky -= 1; raise ConnectionError(self.name)
        for i in ids: self.rows.pop(i, None)

class KeyVault(Store):                                  # crypto-shredding for backups
    def find(self, subject): return [subject] if subject in self.rows else []

def erase(subject: str, stores: list[Store], tombstones: set, max_tries=3) -> dict:
    receipt = {"subject_ref": tomb_id(subject)[:16], "stores": {}, "complete": True}
    for s in stores:
        ids = s.find(subject)
        for attempt in range(1, max_tries + 1):
            try:
                s.delete(ids); break
            except ConnectionError:
                time.sleep(0)                           # backoff in real code
        remaining = s.find(subject)
        ok = not remaining
        receipt["stores"][s.name] = {"found": len(ids), "verified": ok, "attempts": attempt}
        receipt["complete"] &= ok
    tombstones.add(tomb_id(subject))                    # blocks re-ingestion from live sources
    receipt["tombstone"] = tomb_id(subject) in tombstones
    return receipt

app_db   = Store("app_db",   {"m1": {"subject": "u42", "text": "order 991"}, "m2": {"subject": "u7"}})
vectors  = Store("vectors",  {"c1": {"subject": "u42", "doc": "m1"}}, flaky=1)
cache    = Store("sem_cache",{"k9": {"subject": "u42"}})
backups  = KeyVault("backup_keys", {"u42": {"subject": "u42", "key": "..."}})
tomb = set()
print(erase("u42", [app_db, vectors, cache, backups], tomb))
# ingestion computes tomb_id(subject) and skips any record whose id is in `tomb`
```

The `complete` flag is what an audit reads; a `False` means the job must be rerun or escalated, never
quietly closed. In production the store list comes from the inventory, and the derived-artifact ids come
from a forward lineage traversal rather than a per-store scan.

## Trade-offs
- Short TTLs on traces and caches make deletion deadlines trivial to meet but shrink the window for
  debugging and evaluation; the balance shifts toward longer retention once PII is redacted at write time.
- Crypto-shredding handles backups and immutable logs cleanly, but per-subject keys add a key-management
  system and a lookup on every read; per-tenant keys are far cheaper and cover offboarding, but cannot
  shred one person inside a tenant, so per-subject erasure then falls back to backup expiry plus replay
  of the deletion log on restore.
- Keeping personal data out of fine-tuning avoids the unlearning problem entirely, but some use cases
  need it; then retraining on a schedule is the cost of being able to honour deletions.
- Partitioning every store by tenant makes offboarding a partition drop, but multiplies collections and
  indexes; for thousands of small tenants, logical partitioning with tenant keys plus filtered deletes
  is more practical.
- Verification by re-query adds load and time to each deletion job, but a deletion without verification
  is an assertion, not evidence; skip verification only for stores with transactional guarantees.

## Pitfalls
- Vectors stored without subject or document identifiers; the symptom is a deletion request that
  requires a full index rebuild because nothing can be targeted.
- Treating "the source document was deleted" as "the chunks are gone"; the symptom is retrieval still
  citing a page that no longer exists.
- Relying on ANN soft deletes; the symptom is an audit finding the bytes still on disk months after a
  confirmed deletion.
- Full prompts in traces with multi-year retention; the symptom is an observability stack that becomes
  the largest PII store in the company and the hardest to delete from.
- Re-ingestion resurrecting deleted data because the source system still has it; the symptom is a user
  deleted last month reappearing in the index after the next crawl.
- Forgetting the vendor; the symptom is a data-processing audit asking for the provider's retention terms
  and nobody knowing whether zero-retention was ever enabled.
- Deletion jobs that report success on partial completion; the symptom is a later discovery in a store
  that was down during the run.
- Tombstones and deletion receipts that store the raw email or user id; the symptom is a deny-list that is
  itself a growing register of exactly the people who asked to be forgotten. Store a keyed hash instead.
- Deleting data that is under legal hold because the erasure job never consults the hold register; the
  symptom surfaces in litigation, which is the worst place to discover it.

## When to use
This architecture is required wherever personal data, customer-confidential data or regulated data
enters prompts, indexes or training sets, which is most enterprise deployments and any consumer product.
Design it before the first index exists, because adding subject identifiers to vectors and tenant
partitioning to collections after the fact is a full rebuild. Measure: time from deletion request to
verified completion across all stores, number of stores with no automated TTL, and percentage of derived
artifacts reachable through lineage. Run deletion drills quarterly. In interviews, "a user invokes their
right to erasure; walk me through what happens in your RAG system" should produce the store inventory,
the propagated job with receipts, the tombstone, and an honest statement about model weights.

## Role lens
- **Engineer:** Adds subject and tenant identifiers to every vector, cache entry and training row, builds
  the idempotent deletion job with receipts, and wires tombstone checks into ingestion.
- **Architect:** Owns the store inventory and retention classes, chooses crypto-shredding and partitioning
  strategies, and decides whether personal data may enter training at all.
- **FDE:** Maps a customer's data-protection requirements onto the stack, confirms the vendor's retention
  terms match their contract, and runs a deletion drill with their privacy officer before go-live.

## Check yourself
**Q:** Your assistant fine-tunes a model monthly on sampled conversations, and a user invokes their right to
erasure. The conversations are already in last month's checkpoint. What do you do, and how should the
system have been designed?
**A:** Immediately run the propagated deletion for all deletable copies: application records, traces,
vector memories, caches, the eval and fine-tuning datasets (producing a new dataset snapshot digest), and
record a tombstone and receipt. For the checkpoint, be honest: the data cannot be surgically removed from
weights with certifiable reliability. The options are to retire the checkpoint and retrain on the cleaned
snapshot within the deadline, which the monthly cadence makes feasible if the retrain can be triggered on
demand, or to show that the training data was redacted so no personal data was ever in the weights. The
system should have been designed to make one of those true by construction: redact PII before sampling
into training data, carry subject identifiers on every training row, keep models time-boxed with an
on-demand retrain path, and document in the vendor agreement what the provider retains. Deletion proof is
the job's receipt plus the provenance manifest of the replacement model showing the cleaned snapshot.

## Quiz
### Q: Why is "delete from the primary database and let everything else expire" insufficient for an LLM application?
- [ ] Primary databases cannot delete rows that are referenced by foreign keys
- [x] Several derived copies, such as vectors, fine-tuning data and weights, have no expiry at all
- [ ] Expiry policies are not permitted under privacy regulation
- [ ] The primary database is usually not the system of record
> Vector indexes, training datasets and model weights persist indefinitely unless a deletion mechanism
> is designed for them. Expiry works only for stores that have a TTL, and even those may expire after the
> request deadline.

### Q: How does crypto-shredding make deletion possible in backups and append-only logs?
- [ ] It compresses the backup so the deleted rows are overwritten
- [ ] It rewrites the backup without the subject's rows
- [x] Data is encrypted per subject or tenant, and deleting the key makes the remaining ciphertext unrecoverable
- [ ] It moves the subject's rows to a separate archive with shorter retention
> Immutable stores cannot have individual rows removed. Encrypting under a per-subject key and destroying
> the key leaves only unreadable ciphertext without modifying the store, which is why it is the common
> pattern for backups. Compression or a separate archive still leaves the data readable.

### Q: In the propagated deletion job, what is the tombstone written in the final step for?
- [ ] To mark the subject's rows as pending so a nightly job can remove them later
- [x] To prevent re-ingestion from resurrecting deleted data and to record that deletion occurred
- [ ] To hold the subject's data for the appeal period before final deletion
- [ ] To signal the model vendor to purge their logs
> Source systems or crawls may still contain the data; the tombstone tells ingestion to skip it and gives
> auditors a record of what was deleted and when. It is a deny-list, not a holding area.
