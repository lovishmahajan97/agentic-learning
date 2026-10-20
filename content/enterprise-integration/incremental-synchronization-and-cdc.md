---
id: incremental-synchronization-and-cdc
module: enterprise-integration
name: Incremental synchronization & CDC
kind: pattern
difficulty: advanced
est_minutes: 18
related:
  - enterprise-integration/webhooks
  - enterprise-integration/enterprise-connectors
  - enterprise-integration/retry-deduplication-reconciliation
  - enterprise-integration/legacy-system-integration
  - rag-retrieval/freshness-document-deletion-index-consistency
  - system-design/outbox-inbox-patterns
  - data-architecture/ingestion-pipelines
  - data-architecture/embedding-versioning-re-indexing-strategy
sources:
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Official docs: Debezium
  - Official docs: PostgreSQL logical decoding and replication slots
  - Official docs: Microsoft Graph delta query
---

## TL;DR
Incremental synchronization copies only what changed since the last run instead of reloading everything;
change data capture (CDC) is its strongest form, reading inserts, updates and deletes from the database's
own transaction log. `updated_at` watermarks are simple but miss hard deletes and late commits; API delta
tokens move the bookkeeping to the source; log-based CDC sees every committed change, at the price of
operating a replication pipeline. This choice sets how fresh and how expensive an AI index is.

## Why it exists
A RAG index or feature store over customer data is a derived copy. The source keeps changing: tickets are
edited, records reassigned, documents deleted for legal reasons, permissions revoked. The obvious way to
keep the copy current is to reload it on a schedule, and for small sources that is fine.

It stops working as volume grows. As an illustrative example, a 2-million-record source where 0.5% of
records change per day has about 10,000 changes daily; a nightly full reload re-reads and potentially
re-embeds 2 million records to apply them, about 200 times the necessary work, while hammering source APIs
and still leaving up to 24 hours of staleness. A full reload also makes "what changed?" invisible, so
downstream consumers cannot react to specific changes. The first improvement, querying rows with
`updated_at > last_run`, is where subtle bugs live: hard-deleted rows never match the query, rows written in
transactions that commit after your run but carry an earlier timestamp are skipped forever, rows sharing the
boundary timestamp are dropped or duplicated, and bulk updates that bypass the application may not touch
`updated_at` at all. Incremental sync and CDC are the techniques for getting changes, deletes included,
completely and in a defined order.

## How it works
### The spectrum of mechanisms
1. **Full snapshot plus diff.** Reload and compare hashes to find changes. Correct, including deletes, but
   cost scales with total size. Fine for small or rarely changing sources.
2. **Watermark queries.** `WHERE updated_at > :hw`. Requires a reliable modified column (database-maintained,
   not app-maintained), an index on it, and a way to see deletes (soft-delete column or a separate tombstone
   table). Mitigations: re-read an overlap window, *because* transactions can commit after rows with later
   timestamps became visible; page by the tuple `(updated_at, id)`, *because* many rows can share one
   timestamp; take the upper bound from the database clock, not the worker's.
3. **Source-provided change APIs.** Microsoft Graph delta tokens, Salesforce Change Data Capture events
   and `getUpdated`/`getDeleted` calls, Jira and Confluence `updated` queries with JQL/CQL. The source tracks
   state and often reports deletions; you persist an opaque cursor per container, *because* a token is only
   meaningful to the API that issued it. Tokens can expire, forcing a resync.
4. **Trigger-based CDC.** Database triggers write each change to a changelog table. Captures deletes, but
   adds write cost to every transaction on the source.
5. **Log-based CDC.** Read the write-ahead log (PostgreSQL logical decoding via a replication slot, MySQL
   binlog, SQL Server CDC, Oracle redo via a supported tool). Debezium is the common open-source connector,
   publishing change events to Kafka with `before`, `after` and `op` (`c` create, `u` update, `d` delete,
   `r` snapshot read). Every committed change appears, in commit order per table key, with no load from
   polling queries.

### Snapshot, then stream
Every incremental system starts with an initial snapshot and then follows changes. The hand-off must not
lose changes made during the snapshot: log-based tools record the log position before snapshotting and
stream from it, so overlap is resolved by idempotent upserts.

### Applying changes downstream
Upsert by primary key and apply deletes as deletes (remove chunks and embeddings). Make application
idempotent, *because* every mechanism above can redeliver after a crash. For an embedding index, compare a
content hash before re-embedding, *because* many updates touch only metadata, such as a status or assignee,
not the text; update metadata in place and re-embed only when the text changed. Permission changes must flow
through the same path, or the index serves revoked content.

### Operating it
Persist cursors transactionally with the applied changes (or after them, with idempotent replays). Monitor
lag: for watermarks, now minus high-water mark; for CDC, consumer offset behind the log head. A stalled
PostgreSQL replication slot makes the server retain WAL, which can fill the source's disk, so slot lag is a
production alert, not a dashboard curiosity.

## Example
A watermark sync from a PostgreSQL table with soft deletes, an overlap window, keyset paging on
`(updated_at, id)`, and hash-gated re-embedding. `conn` is a psycopg connection; `index` is your vector store.

```python
import hashlib
from datetime import timedelta

OVERLAP = timedelta(minutes=10)  # illustrative; must exceed your longest write transaction

def sync_once(conn, state: dict, index, page: int = 500) -> None:
    hi = conn.execute("SELECT now()").fetchone()[0]           # database clock, not the worker's
    cursor = (state["high_water"] - OVERLAP, "")              # re-read the overlap window
    while True:
        rows = conn.execute(
            """SELECT id, updated_at, deleted_at, title, body, acl FROM documents
               WHERE (updated_at, id) > (%s, %s) AND updated_at <= %s
               ORDER BY updated_at, id LIMIT %s""",
            (*cursor, hi, page)).fetchall()
        for doc_id, updated_at, deleted_at, title, body, acl in rows:
            if deleted_at is not None:
                index.delete(doc_id)                          # soft delete makes deletes visible
                continue
            digest = hashlib.sha256(body.encode()).hexdigest()
            if index.content_hash(doc_id) == digest:
                index.update_metadata(doc_id, title=title, acl=acl)  # no re-embedding needed
            else:
                index.upsert(doc_id, title=title, body=body, acl=acl, content_hash=digest)
        if len(rows) < page:
            break
        cursor = (rows[-1][1], rows[-1][0])                   # keyset: last (updated_at, id)
    state["high_water"] = hi                                  # persist only after a full pass
```

Run it every few minutes and the illustrative 10,000 daily changes cost 10,000 row reads plus re-embedding
only the subset whose text changed. Hard deletes that bypass `deleted_at` are still invisible here, which is
the argument for log-based CDC or a periodic full key comparison.

## Trade-offs
- Watermark queries need no infrastructure, but depend on a trustworthy `updated_at` and soft deletes; move
  to log-based CDC when hard deletes, bulk updates or sub-minute freshness matter.
- Log-based CDC captures every committed change with minimal query load, but adds Kafka or a similar log,
  connector operations, schema-change handling and slot monitoring; it rarely pays off for one small table.
- Source delta APIs are the only option for SaaS systems and handle deletes for you, but tokens expire and
  semantics vary per vendor, so you still need a full-resync path.
- A wider overlap window catches later-committing transactions, but re-reads more rows each run; it is cheap
  with idempotent, hash-gated upserts and expensive without them.

## Pitfalls
- Using `updated_at > last_run` with the worker's clock and no overlap; the symptom is a small, steady
  number of records that never appear in the index.
- No delete path; the symptom is the assistant citing documents that were deleted from the source weeks ago.
- Re-embedding on every update; the symptom is embedding cost that tracks metadata churn, like ticket status
  changes, rather than content changes.
- An abandoned PostgreSQL replication slot after decommissioning a consumer; the symptom is the source
  database's disk usage climbing until the customer's DBA pages you.
- Saving the cursor before changes are applied; the symptom is changes lost permanently after a worker crash
  mid-batch.

## When to use
Use full reloads for small sources and early pilots. Move to watermark or source delta sync once reload cost
or staleness hurts, insisting on a delete mechanism. Choose log-based CDC when you own or can access the
database, need every change including hard deletes, or need low lag, and when someone will operate the
pipeline. Keep a periodic full reconciliation regardless. Measure sync lag, changes applied per run, re-embed
ratio, deletes applied, and drift found by reconciliation. In interviews, explaining why naive watermarks miss
changes and how CDC avoids it is the expected depth.

## Role lens
- **Engineer:** Implements cursors, overlap windows, keyset paging, idempotent upserts with hash-gated
  re-embedding, and delete propagation into the vector index.
- **Architect:** Picks the mechanism per source (reload, watermark, delta API, log-based CDC), defines
  freshness targets and owns the snapshot-to-stream and full-resync strategies.
- **FDE:** Negotiates with the customer's DBA for replica access or a replication slot, confirms how deletes
  are recorded, and agrees on monitoring so CDC never threatens their database.

## Check yourself
**Q:** A sync uses `WHERE updated_at > :last_run` every five minutes. A few records modified during busy periods
never appear in the index, and deleted records never disappear. Explain both and fix them.
**A:** Missing records: `updated_at` is set when the statement runs, but the row becomes visible only when its
transaction commits. A long transaction can commit after the sync advanced past that timestamp, so the row is
skipped forever; worker clock skew and shared boundary timestamps make it worse. Fix with the database clock
for the upper bound, an overlap window longer than the longest transaction, keyset paging on `(updated_at,
id)`, and idempotent upserts. Deleted records: hard deletes leave no row to match. Fix with soft deletes or a
tombstone table, or move to log-based CDC, which emits delete events, plus periodic key reconciliation.

## Quiz
### Q: Why can a watermark query on updated_at miss rows even with a correct clock?
- [ ] Indexes on timestamps are approximate
- [x] A row can carry a timestamp earlier than the watermark but commit and become visible after the sync ran
- [ ] PostgreSQL does not store microseconds
- [ ] Rows with NULL ids are skipped
> The timestamp reflects when the write happened, not when it committed. A late commit lands behind an
> already-advanced watermark, which is why overlap windows or log-based CDC are needed.

### Q: What does log-based CDC capture that a soft-delete-free watermark query cannot?
- [ ] Rows inserted in the last minute
- [x] Hard deletes, as explicit delete events from the transaction log
- [ ] Changes to columns with indexes
- [ ] Rows larger than one page
> Deleted rows leave nothing to match a query, but the log records the delete, so CDC emits an event (op d in
> Debezium) that the consumer applies to the index.

### Q: An index re-embeds a document whenever its row changes. Most changes are status updates. What fixes the cost?
- [ ] Switch to a larger embedding model
- [ ] Sync less often so changes are batched
- [x] Compare a content hash and update metadata in place unless the text changed
- [ ] Delete and re-insert every changed document
> Embeddings depend on text, so metadata-only changes should update filters like status or ACL without calling
> the embedding model; batching reduces frequency but not the wasted re-embeds.
