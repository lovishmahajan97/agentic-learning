---
id: ingestion-pipelines
module: data-architecture
name: Ingestion pipelines
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
related:
  - data-architecture/etl-elt-for-rag
  - data-architecture/document-processing-pipelines
  - data-architecture/data-contracts
  - data-architecture/data-lineage
  - rag-retrieval/freshness-document-deletion-index-consistency
  - rag-retrieval/acl-aware-retrieval
  - enterprise-integration/data-ingestion-from-client-systems
  - enterprise-integration/retry-deduplication-reconciliation
sources:
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Official docs: Debezium
  - Official docs: Apache Airflow
---

## TL;DR
An ingestion pipeline is the path from source systems (databases, SaaS tools, file shares, event streams)
into a durable, replayable landing store that your AI system can transform from. The design that survives
production is incremental change capture (including deletes), an immutable raw copy keyed by source
identity and content hash, and idempotent stages with a committed watermark, because sources change
continuously and every downstream choice (parser, chunker, embedding model) will be redone more than once.

## Why it exists
The first version of every RAG system is a script: list everything in Confluence or an S3 bucket, embed
it, upsert it, run it nightly. It works at 2,000 documents and fails in four predictable ways at 200,000.

First, cost scales with the corpus rather than with change. A nightly full re-crawl re-fetches, re-parses
and re-embeds documents that have not moved, and the embedding bill and API rate limits become the
bottleneck long before the vector store does. Second, deletes are invisible. A listing-based crawl only
ever sees what exists now; a document deleted at the source stays in the index forever and keeps being
retrieved, which for HR policies or revoked contracts is a correctness and compliance bug. Third, a crash
halfway through leaves the index half new and half old with no record of which is which, and the only
recovery is another full run. Fourth, when the team changes the chunker or the embedding model, there is
no raw copy to re-transform from, so "re-index" means "re-crawl", colliding again with rate limits and
with the source team's goodwill.

The naive fix, "make the script faster", does not address any of these. What is needed is a separation
between *getting bytes out of sources* (which should be incremental, polite and durable) and *turning bytes
into retrieval units* (which should be cheap to re-run). This lesson covers the first half; the ETL/ELT
lesson in this module covers the second.

## How it works
### Classify each source by how it exposes change
Every connector design starts with one question: how does this source tell me what changed? There are
four answers, in rising order of fidelity and cost.

1. **Snapshot diff.** List everything, hash each item, compare with the last snapshot. Catches creates,
   updates and deletes. Cost is O(corpus) per run for the listing (not for the content, if the source gives
   a cheap version or ETag), so it is fine for a few hundred thousand items and wrong for a billion rows.
2. **Cursor / watermark.** Query `WHERE updated_at > :last_seen` or use a provider's change token
   (Microsoft Graph delta queries, Google Drive's changes feed). Cheap and incremental. Provider change
   tokens usually do report deletions (Graph delta marks removed items, for example), but a plain
   timestamp cursor misses hard deletes and can miss rows written with a timestamp earlier than the
   watermark by clock skew or long transactions. The fix is to read with `>=` and a small overlap window
   and dedupe by id, and to pair it with a periodic snapshot diff that reconciles deletes.
3. **Change data capture (CDC).** Tail the database's transaction log (Postgres logical decoding, MySQL
   binlog) with a tool such as Debezium and receive every insert, update and delete as an ordered event.
   Highest fidelity, including deletes, at the cost of operating a streaming system and needing DBA
   cooperation.
4. **Push: webhooks and event streams.** The source calls you. Lowest latency, but webhooks are
   at-least-once at best and often lossy, so they are a trigger to fetch, not the source of truth.

The design choice follows from freshness requirements and from what the source offers, not from taste.
A support knowledge base that changes a few hundred times a day is fine on a 15-minute cursor poll with a
nightly snapshot reconcile; an orders table that drives agent decisions wants CDC.

### Land raw bytes immutably
Each fetched item is written to a landing store (object storage or a lakehouse table) as the raw bytes
plus a metadata record: `source`, `source_id`, `source_version` (ETag, revision number or content hash),
`fetched_at`, `acl` (who may read it at the source), and the raw `uri`. The record is immutable; a new
version is a new row, not an overwrite. This is the single most important choice in the pattern, because
everything downstream (parsing, chunking, embedding) is a deterministic function of the raw bytes and can
be replayed from them without touching the source again.

### Make every stage idempotent
The natural key of an item is `(source, source_id)`. Content hashing on top of it lets the pipeline skip
items whose bytes have not changed even when the source claims an update (many SaaS APIs bump
`updated_at` on permission changes or views). Skipping re-parsing and re-embedding is right in that case,
but the ACL must still be compared and propagated, because a permission change with identical bytes is
exactly the update retrieval filtering depends on. Downstream writes are upserts keyed by that identity, and
deletes are written as tombstone rows carrying the same key, so a replay of any window produces the same
end state. The watermark is committed *after* the batch's writes are durable; committing it first is the
classic bug that turns a crash into a silent gap.

The delivery guarantee this buys is worth stating precisely. No stage is exactly-once: the overlap window,
retries and redelivered webhooks all mean the same change can arrive twice. The pipeline is
*at-least-once with idempotent effects*. Re-applying a change keyed by `(source, source_id, version)`
leaves the store unchanged, so a duplicate does no harm and a gap is the only failure you must prevent.
That is why the commit order matters more than deduplication: duplicates are absorbed downstream, gaps
are not.

### Initial load, backfills and the handoff to incremental
Every connector has two modes: the first full load and steady-state increments. The dangerous moment is
the switch from one to the other. If you snapshot the source and then start polling "from now", changes
made during the snapshot, which can take hours for a large source, fall between the two. The fix is to
record the change position *before* the snapshot starts: the cursor value, the provider's delta token,
or the log position for CDC. Then the incremental reader starts from that recorded position, and the
idempotent upserts absorb the overlap. Debezium works this way: it takes a consistent initial snapshot,
then streams from the log offset it recorded at snapshot time. Run backfills, such as re-ingesting a
source after a connector bug, in a separate lane with their own rate-limit budget. Otherwise a
multi-day backfill uses up the API quota and the incremental sync goes stale while it runs.

### Treat source schemas as a contract that will break
SaaS APIs add fields, rename them and change pagination without notice, and a database team will
eventually drop a column the connector reads. Validate each fetched record against an expected schema
at the landing boundary. Store unknown fields in the raw record instead of dropping them, and send
records that fail validation to quarantine rather than letting them flow on with nulls. A connector
that quietly writes `title = null` for a whole source is worse than one that stops, because the index
fills with untitled chunks and nothing alarms. The data-contracts lesson covers making this explicit
with the source owner.

### Decouple fetch rate from processing rate
Sources impose rate limits; parsers and embedders have their own throughput. A queue between the connector
and the transformation stages absorbs the mismatch, gives retries with backoff a place to live, and lets a
poison document (a 2 GB PDF that crashes the parser) be parked in a dead-letter queue instead of stalling
the run. Batch orchestration (Airflow, Dagster, Prefect) suits snapshot and cursor sources; a streaming
consumer suits CDC and webhooks. Micro-batches every few minutes are the common middle ground.

### Carry ACLs and lineage as data
Permissions from the source travel with the document as a first-class field, because retrieval must filter
on them later and a pipeline that drops them cannot be fixed at query time. Lineage, which raw version a
chunk came from and which pipeline version produced it, is what makes a selective re-run possible.

### Observe the pipeline as a pipeline
Per stage, count items in, items out and items failed; track lag as `now − last_committed_watermark`;
alarm on a change ratio that is zero (connector silently broken) or near 100 % (source migrated and every
id changed). These three numbers answer "is the index fresh?" without reading the index.

## Example
An incremental connector loop for a cursor-based source. It reads with an overlap window, dedupes by id,
skips re-embedding unchanged content by hash while still propagating ACL changes, writes tombstones for
deletes reported by the source, and commits the watermark only after the batch is durable.

```python
import hashlib, datetime as dt

OVERLAP = dt.timedelta(minutes=5)          # tolerate clock skew / late commits at the source
EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)

def sync_once(source, landing, state):
    wm = state.get("watermark")                              # tz-aware datetime, or None on first run
    since = wm - OVERLAP if wm else EPOCH
    seen, newest = set(), wm or EPOCH
    for item in source.list_changes(since=since):          # yields {id, updated_at, deleted, acl, fetch}
        if item["id"] in seen:
            continue                                         # same id twice in one listing (paged API)
        seen.add(item["id"])
        newest = max(newest, item["updated_at"])
        key = (source.name, item["id"])
        if item["deleted"]:
            landing.write_tombstone(key, at=item["updated_at"])   # idempotent if re-delivered
            continue
        raw = item["fetch"]()                                # bytes; respects source rate limits
        digest = hashlib.sha256(raw).hexdigest()
        if landing.latest_hash(key) == digest:               # overlap re-delivery or metadata-only bump
            if landing.latest_acl(key) != item.get("acl"):
                landing.update_acl(key, item.get("acl"))     # permissions changed: no re-embed needed
                landing.emit_event("document.acl_changed", key=key)
            continue
        landing.write_version(key, raw, hash=digest, acl=item.get("acl"),
                              fetched_at=dt.datetime.now(dt.timezone.utc))
        landing.emit_event("document.changed", key=key, hash=digest)
    landing.flush()                                          # make writes durable first ...
    state["watermark"] = newest                              # ... then advance the cursor
    return len(seen)
```

A nightly snapshot-diff job runs alongside this loop to catch deletes the source never reported, which is
the standard pairing for sources that only expose `updated_at`.

## Trade-offs
- Snapshot diff is the simplest way to catch deletes and needs no source cooperation, but its listing cost
  grows with the corpus, so it flips to cursor-plus-reconcile once a full listing takes longer than the
  freshness window.
- CDC gives exact, ordered, delete-inclusive change streams, but it requires log access, DBA buy-in and a
  streaming platform; for a source changing a few hundred times a day, polling is cheaper to run.
- Storing every raw version makes replays and audits trivial at the cost of storage; when documents are
  large binaries that change often, keep only the last N versions and rely on lineage for the rest.
- Streaming ingestion cuts freshness lag to seconds but spreads work evenly, which is worse for embedding
  APIs that are cheaper and faster in large batches; micro-batching usually wins unless the use case is
  truly real-time.

## Pitfalls
- Advancing the watermark before the batch's writes are durable; the symptom is documents that are
  missing from the index after a crash with no error in any log.
- Using a timestamp cursor without an overlap window; the symptom is sporadic missed updates traced to
  rows committed late or from a clock-skewed replica.
- Never handling deletes; the symptom is a retrieved answer citing a document that no longer exists at
  the source, often discovered by a customer rather than a dashboard.
- Dropping source ACLs during ingestion "to simplify"; the symptom is a user retrieving a document they
  could not open in the source system, which is a security incident, not a bug.
- Re-fetching on every `updated_at` bump without a content hash; the symptom is a large embedding bill
  for a corpus that barely changes, because the SaaS source bumps timestamps on permission edits. The
  opposite error, skipping hash-identical items entirely, silently drops those permission edits.
- Starting the incremental cursor at the time the initial snapshot *finished* rather than when it
  started; the symptom is documents edited during the first load that stay at their old version until
  someone edits them again.
- No dead-letter path; the symptom is a whole nightly run that fails repeatedly on the same oversized or
  malformed file while thousands of healthy documents go stale.

## When to use
Reach for this pattern the moment a corpus has more than one source, changes while the system is live, or
must respect deletions and permissions, which is to say for anything beyond a demo. Measure freshness lag,
change ratio per run, per-stage failure counts and the embedding spend per run; those four numbers expose
nearly every ingestion defect. Do not build CDC for a wiki that changes twice a day, and do not accept a
"full re-crawl nightly" design for a corpus whose embedding cost per full pass exceeds the monthly vector
store bill. In day-to-day work this is the code that pages you at 3 a.m. when a source's API changes; in
interviews it is the "design ingestion for SharePoint plus Jira plus a Postgres orders table, with
deletes and permissions" question, and the expected answer walks through change detection per source,
immutable raw landing, idempotent upserts and tombstones, and the watermark commit order.

## Role lens
- **Engineer:** Writes connectors with cursors, overlap windows, content hashing and tombstones, and
  instruments lag and change ratio so a silent connector failure is visible within one polling interval.
- **Architect:** Decides per source between snapshot diff, cursor polling, CDC and push, defines the
  landing-store schema including ACL and lineage fields, and sets the freshness SLA that justifies the cost.
- **FDE:** Negotiates API access, rate limits and service accounts with the customer's source-system owners,
  and proves deletions and permission changes propagate before go-live, because that is the question
  their security team will ask.

## Check yourself
**Q:** A customer's RAG index is built by a nightly job that lists every page in their wiki and embeds
pages whose `updated_at` changed. Users report that deleted pages still appear in answers and that some
edits never show up. Diagnose both symptoms and describe the minimal redesign.
**A:** Deleted pages persist because a listing of current pages can only report what exists; nothing
ever emits a delete for a page that is gone, so the index keeps its chunks. Missed edits come from the
timestamp cursor: a page saved just before the previous run's watermark but committed after the listing
was taken, or saved on a replica with skewed time, falls below the cursor and is never re-read. The
minimal fix keeps the nightly job but makes it a snapshot diff: hash the current page list and content
versions, compare with the stored snapshot, and emit tombstones for ids that disappeared and updates for
changed hashes. Add an overlap window to the cursor read and dedupe by id, commit the watermark only
after writes are durable, and store the raw page bytes so chunker changes can be replayed without
re-crawling the wiki.

## Quiz
### Q: Why should the ingestion watermark be committed only after the batch's writes are durable?
- [ ] Because the watermark must be the largest timestamp in the batch, which is unknown until the end
- [x] Because a crash between committing the watermark and persisting the writes would silently skip those items on the next run
- [ ] Because committing earlier would cause duplicate writes in the landing store
- [ ] Because the source system rejects queries with a watermark newer than its own clock
> If the cursor advances first and the process dies before the writes land, the next run starts after the
> lost items and nothing ever re-reads them. Duplicates are the safe failure; gaps are the silent one.

### Q: A source exposes only an `updated_at` field and a list-all endpoint. Which design catches deletes?
- [ ] Polling `updated_at` with an overlap window and deduplicating by id
- [ ] Subscribing to the source's webhooks and treating each event as authoritative
- [x] Running a periodic snapshot diff of the listed ids against the previously stored snapshot
- [ ] Hashing document content and skipping items whose hash has not changed
> A timestamp cursor never sees a row that no longer exists, and webhooks are lossy. Only comparing the
> set of ids present now with the set stored last time reveals which ids vanished, so tombstones can be
> emitted for them.

### Q: What is the main reason to store immutable raw bytes in a landing store rather than only the derived chunks?
- [ ] Raw bytes compress better than chunk text in object storage
- [ ] Vector databases cannot store the original document
- [x] Parsing, chunking and embedding are re-run far more often than sources change, and replaying from raw avoids re-crawling
- [ ] Regulators require a copy of every source document
> The transformation steps are where experimentation and model upgrades happen. With a raw copy, a new
> chunker is a local re-run; without it, every change becomes a full re-crawl against rate-limited sources.
