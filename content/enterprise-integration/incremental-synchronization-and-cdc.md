---
id: incremental-synchronization-and-cdc
module: enterprise-integration
name: Incremental synchronization & CDC
kind: pattern
difficulty: advanced
est_minutes: 18
status: typed-verified
lesson_type: decision
agent_relevance: useful
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
An agent's RAG index is a copy of moving sources. Incremental sync copies only what changed; CDC reads
every insert, update and delete from the database log. Your mechanism decides how stale, leaky and
expensive the agent's answers are.

## For agent builders
- **Why it matters for agents.** An agent that cites a closed ticket, a deleted contract or a document whose
  permissions were revoked is failing at sync, not at reasoning. Freshness and deletes in the index set the
  ceiling on how trustworthy retrieval-backed answers can be.
- **Where it shows up in your stack.** The ingestion job that feeds your vector store, the cursor or
  replication slot it reads from, the hash gate in front of the embedding model, and the "as of" metadata
  your retrieval tool can return to the agent.
- **What you control.** Mechanism per source, the overlap window, delete and permission propagation,
  re-embedding only on text change, and whether the agent falls back to a live fetch when a record is newer
  than the index.
- **What you can skip.** Running Kafka and Debezium yourself for a pilot. Start with a watermark plus soft
  deletes, learn the failure modes below, and move to log-based CDC when the numbers demand it.

## Decision case
### Scenario
A support agent answers from an index of the customer's on-prem PostgreSQL ticket database, run by a
vendor application you cannot modify. You must choose the sync mechanism for production. Figures are
illustrative.
- **Data:** 2 million tickets; about 10,000 changed rows a day, of which about 1,500 change text.
- **Deletes:** the application hard-deletes tickets (no `deleted_at`); legal requires deleted tickets out
  of agent answers within 1 hour.
- **Freshness:** the agent may state a ticket's status only if the data is under 5 minutes old.
- **DBA rules:** no triggers or schema changes on the vendor's tables; one logical replication slot is
  allowed only with a retained-WAL cap and an alert. WAL rate is 2 GB an hour; 50 GB of disk is free.
- **Team:** 2 engineers; the platform team already runs a managed Kafka cluster.

### Options
| Option | How it works | Re-embeds per day | Hard deletes gone within | Status freshness | DBA rules |
|---|---|---|---|---|---|
| Nightly full reload | reload and diff hashes | up to 2,000,000 | up to 24 h | up to 24 h | passes |
| Watermark every 5 min, hash gate | overlap window, keyset paging | about 1,500 | never without a key comparison | up to about 5 min | passes |
| Trigger-based changelog | triggers write changes and deletes to a table | about 1,500 | minutes | minutes | fails: triggers not allowed |
| Log-based CDC, hash gate | Debezium reads the slot into Kafka; LSN-guarded consumer | about 1,500 | seconds | seconds | passes with cap and alert |

Watermark plus hourly key-only reconciliation would meet the delete rule, but costs 2 million x 24 = 48
million key reads a day on the source or a replica.

### Decision
**Log-based CDC with a hash-gated, LSN-guarded consumer, weekly key and hash reconciliation, and a
retrieval tool that reports index lag.** It is the only option that sees hard deletes within the hour
without triggers or tens of millions of key reads, and it keeps re-embeds at about 1,500 a day.
- **Slot safety:** at 2 GB an hour, a stalled slot fills 50 GB in about 25 hours. Set
  `max_slot_wal_keep_size` to 20 GB (invalidation after about 10 hours) and page at 10 GB retained, about 5
  hours of stall. That alert protects the disk, not the delete rule, which breaks after 1 hour; page
  separately on end-to-end consumer lag over about 15 minutes.
- **Consumer rules:** upsert by key, skip events with an older LSN, keep tombstones, never write the
  unchanged-TOAST placeholder, and update metadata in place when the text hash is unchanged.
- **Agent side:** each hit carries `source_updated_at`, and when lag exceeds 5 minutes for a status
  question the agent calls a live lookup tool instead.

**What would change it.** If the DBA refuses a slot, use the 5-minute watermark plus hourly key-only
reconciliation on a replica and accept its read cost. If the vendor adds soft deletes, the watermark alone
meets every constraint with no pipeline to run.

### Trade-offs accepted
- **A pipeline to operate.** Connector upgrades, Kafka topics and schema changes are ongoing work; monitor
  consumer deserialization errors after every vendor upgrade.
- **The customer's disk is at stake.** Alert on retained WAL per slot and on inactive slots, with a
  runbook to drop orphaned slots.
- **Cap invalidation forces a re-snapshot.** Use incremental snapshots so the stream keeps flowing; track
  snapshot progress and time to catch up.
- **Default replica identity.** Deletes carry only the key, so the index must be addressable by ticket ID;
  monitor deletes that matched no indexed document.

### Review rubric
- **Delete semantics found first:** states whether the source soft- or hard-deletes before choosing.
- **Numbers recomputed:** re-embeds per day, slot fill time and reconciliation cost from stated inputs.
- **Source protected:** WAL cap, retained-WAL alert and orphaned-slot runbook agreed with the DBA.
- **Consumer correctness:** idempotent upserts, LSN guard, tombstones, placeholder handling and hash gate.
- **Agent sees freshness:** lag and modified time in tool results, with a live fallback for volatile fields.
- **Fallback named:** what happens if the slot is refused or the source changes its delete behaviour.

## Why it exists
- **The index is a derived copy of a moving source.** Tickets are edited, records reassigned, documents
  deleted for legal reasons, permissions revoked. Reloading on a schedule is fine for small sources.
- **Full reloads stop scaling.** Illustratively, a 2-million-record source where 0.5% of records change per
  day has about 10,000 changes daily. A nightly reload re-reads and potentially re-embeds 2 million records
  to apply them, about 200 times the necessary work, hammers source APIs, and still leaves up to 24 hours of
  staleness.
- **Reloads hide "what changed?"**, so downstream consumers cannot react to specific changes, such as
  triggering an agent when a ticket's status flips.
- **`updated_at > last_run` is where subtle bugs live:**
  - hard-deleted rows never match the query;
  - rows from transactions that commit after your run but carry an earlier timestamp are skipped forever;
  - rows sharing the boundary timestamp are dropped or duplicated;
  - bulk updates that bypass the application may not touch `updated_at` at all.
- **Incremental sync and CDC get changes, deletes included, completely and in a defined order.** Delta
  tokens move the bookkeeping to the source; log-based CDC sees every committed change, at the price of
  operating a replication pipeline.

## How it works
### The spectrum of mechanisms
| # | Mechanism | Deletes? | Cost and caveat |
|---|---|---|---|
| 1 | **Full snapshot plus diff**: reload and compare hashes | Yes | Correct, but cost scales with total size; fine for small or rarely changing sources |
| 2 | **Watermark queries**: `WHERE updated_at > :hw` | Only with a soft-delete column or tombstone table | Needs a reliable, database-maintained (not app-maintained) modified column with an index |
| 3 | **Source-provided change APIs** | Often reported | Tokens can expire, forcing a resync |
| 4 | **Trigger-based CDC**: triggers write each change to a changelog table | Yes | Adds write cost to every transaction on the source |
| 5 | **Log-based CDC**: read the write-ahead log | Yes | Every committed change, in commit order per table key, no polling load; you operate the pipeline |

Watermark mitigations, each with its reason:
- **Re-read an overlap window**, *because* transactions can commit after rows with later timestamps became visible.
- **Page by the tuple `(updated_at, id)`**, *because* many rows can share one timestamp.
- **Take the upper bound from the database clock**, not the worker's.

Source change APIs:
- **Examples:** Microsoft Graph delta tokens, Salesforce Change Data Capture events and
  `getUpdated`/`getDeleted` calls, Jira and Confluence `updated` queries with JQL/CQL.
- **Persist an opaque cursor per container**, *because* a token is only meaningful to the API that issued it.

Log-based CDC sources:
- **Logs:** PostgreSQL logical decoding via a replication slot, MySQL binlog, SQL Server CDC, Oracle redo via
  a supported tool.
- **Debezium** is the common open-source connector, publishing change events to Kafka with `before`, `after`
  and `op` (`c` create, `u` update, `d` delete, `r` snapshot read).

### What log-based CDC events actually contain
The log records what the database needs for replication, which is not always what a consumer expects:
- **The `before` image depends on `REPLICA IDENTITY`.** With the PostgreSQL default, an update or delete
  carries only the primary key as `before`. A consumer that needs old values (to remove a document from the
  right shard, say) must set `REPLICA IDENTITY FULL` and pay for larger log records.
- **Untouched TOASTed values may be placeholders.** Large out-of-line column values that an update did not
  touch may arrive as a placeholder, so a consumer that blindly writes `after` can overwrite a document body
  with it, and the agent will then retrieve and quote the placeholder.
- **Transactions arrive as per-row events.** A consumer can observe a multi-row transaction half-applied
  unless it groups by transaction ID.
- **Schema changes flow through the same pipeline.** A renamed or retyped column changes the event shape,
  which is why CDC deployments usually pair with a schema registry and a consumer that tolerates added fields.
- **Delivery is at-least-once.** Every consumer is an idempotent upsert keyed by primary key, ideally guarded
  by the log position (LSN) so a replayed older event cannot overwrite a newer one.

### Snapshot, then stream
1. **Every incremental system starts with an initial snapshot**, then follows changes.
2. **The hand-off must not lose changes made during the snapshot.** Log-based tools record the log position
   before snapshotting and stream from it, so overlap is resolved by idempotent upserts.
3. **Avoid hours-long snapshot transactions.** A one-shot snapshot of a large table can hold a long-running
   read transaction on the source for hours.
4. **Use incremental snapshots.** Debezium reads the table in key-ordered chunks interleaved with the
   stream, using watermarks in the log to resolve conflicts between a chunk and concurrent changes (the
   approach described in Netflix's DBLog work), so a table can be re-snapshotted without pausing the stream.

### Applying changes downstream
- **Upsert by primary key; apply deletes as deletes** (remove chunks and embeddings).
- **Make application idempotent**, *because* every mechanism above can redeliver after a crash.
- **Hash-gate re-embedding**, *because* many updates touch only metadata, such as a status or assignee, not
  the text. Update metadata in place; re-embed only when the text changed.
- **Route permission changes through the same path**, or the index serves revoked content.
- **Store `source_updated_at` and `indexed_at` on every chunk**, *because* the agent's retrieval tool can
  then tell the model how old a fact is.

### Freshness the agent can see
- **Index lag is a fact the agent should know.** Return each hit's source modified time and the current
  sync lag with retrieval results.
- **Fall back to a live read for volatile fields.** If the question is about current status and the lag
  exceeds the field's tolerance, the agent calls a live lookup tool instead of trusting the index.
- **Read-your-writes for agent actions.** When an agent updates a record through a tool, the index will not
  show it until the next sync or CDC event, so the agent should trust its own tool result, not a re-retrieval.

### Operating it
- **Persist cursors transactionally with the applied changes** (or after them, with idempotent replays).
- **Monitor lag:** for watermarks, now minus high-water mark; for CDC, consumer offset behind the log head.
- **Treat slot lag as a production alert.** A stalled PostgreSQL replication slot makes the server retain
  WAL, which can fill the source's disk.
- **Cap retained WAL where you can.** PostgreSQL 13+ offers `max_slot_wal_keep_size`, at the price of
  invalidating a slot that falls too far behind, which then forces a re-snapshot.

## Diagram
Snapshot first, then stream from the recorded log position; the consumer's LSN guard and hash gate keep
replays and metadata churn cheap before anything reaches the agent's index.

```mermaid
flowchart TD
    DB[("Source database")] --> WAL["Write-ahead log"]
    WAL --> SL["Replication slot"]
    SL --> DZ["CDC connector, e.g. Debezium"]
    DB -.->|"initial or incremental snapshot"| DZ
    DZ --> K[("Change topic, e.g. Kafka")]
    K --> C["Consumer"]
    C --> G{"Newer LSN than stored?"}
    G -->|"no"| SK["Skip replay"]
    G -->|"yes"| H{"Text hash changed?"}
    H -->|"yes"| E["Re-embed and upsert"]
    H -->|"no"| MD["Update metadata and ACL"]
    SL -.->|"stalled slot retains WAL"| DB
```

## Examples
### Watermark sync with overlap, keyset paging and hash-gated re-embedding
A PostgreSQL table with soft deletes. `conn` is a psycopg connection; `index` is your vector store.

```python
import hashlib
from datetime import timedelta

OVERLAP = timedelta(minutes=10)  # illustrative; must exceed your longest write transaction

def sync_once(conn, state: dict, index, page: int = 500) -> None:
    hi = conn.execute("SELECT clock_timestamp()").fetchone()[0]  # DB clock; now() is frozen per txn
    cursor = (state["high_water"] - OVERLAP, "")              # overlap; "" sorts first for text ids
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

- **`clock_timestamp()` rather than `now()`** matters if the connection is reused inside one open
  transaction: `now()` returns the transaction's start time, so the upper bound would never advance.
- **Cost at five-minute runs:** the illustrative 10,000 daily changes cost a few tens of thousands of row
  reads (each changed row is re-read by every run whose 10-minute overlap window covers it, about three runs
  here), plus re-embedding only the subset whose text changed. The hash gate keeps the overlap cheap.
- **Starting id:** the `""` assumes text ids; with integer or UUID keys start from 0 or the nil UUID instead.
- **Still blind to hard deletes** that bypass `deleted_at`, which is the argument for log-based CDC or a
  periodic full key comparison.

### Applying Debezium-style change events safely
Pure Python, runnable. It guards by LSN, keeps a tombstone so a late update cannot resurrect a deleted row,
and never writes the unchanged-TOAST placeholder into the index.

```python
PLACEHOLDER = "__debezium_unavailable_value"   # Debezium's default placeholder for unchanged TOAST

def apply_change(index: dict, ev: dict) -> str:
    """Apply one Debezium-style change event idempotently, guarded by log position (LSN)."""
    op, lsn = ev["op"], ev["source"]["lsn"]
    key = (ev["after"] or ev["before"])["id"]          # default replica identity: before = key only
    current = index.get(key)
    if current and current["lsn"] >= lsn:
        return "skip: replayed or older event"
    if op == "d":
        index[key] = {"row": None, "lsn": lsn}          # tombstone keeps the LSN guard working
        return "delete chunks and embeddings"
    row = dict(ev["after"])
    for col, val in row.items():
        if val == PLACEHOLDER:                          # column untouched by this update
            if not (current and current["row"] and col in current["row"]):
                return "re-fetch row from source"       # never saw the value: do not write the marker
            row[col] = current["row"][col]
    index[key] = {"row": row, "lsn": lsn}
    return "upsert"

idx = {}
print(apply_change(idx, {"op": "r", "before": None, "source": {"lsn": 100},
                         "after": {"id": 7, "status": "open", "body": "long text"}}))   # upsert
print(apply_change(idx, {"op": "u", "before": {"id": 7}, "source": {"lsn": 140},
                         "after": {"id": 7, "status": "closed", "body": PLACEHOLDER}}))  # upsert
print(idx[7]["row"]["body"])                                                          # long text
print(apply_change(idx, {"op": "u", "before": {"id": 7}, "source": {"lsn": 120},
                         "after": {"id": 7, "status": "open", "body": "old"}}))         # skip
```

### A freshness-aware retrieval tool for the agent
Runnable standard library: each hit carries its age, and the tool tells the agent to use a live lookup
when a volatile field is staler than its tolerance.

```python
import time

SYNC_STATE = {"tickets": {"high_water": time.time() - 900}}      # last full pass 15 minutes ago
TOLERANCE_S = {"status": 300, "body": 86_400}                     # status must be < 5 min old
INDEX = [{"id": "T-12", "status": "open", "body": "VPN drops hourly", "source_updated_at": time.time() - 3600}]

def search_tickets(query: str, field_needed: str = "body") -> dict:
    lag = time.time() - SYNC_STATE["tickets"]["high_water"]
    hits = [h for h in INDEX if query.lower() in h["body"].lower()]
    advice = None
    if lag > TOLERANCE_S.get(field_needed, 3600):
        advice = f"index is {lag/60:.0f} min behind; call get_ticket_live for current {field_needed}"
    return {"hits": hits, "index_lag_s": round(lag), "advice": advice}

print(search_tickets("vpn")["advice"])                   # None: body tolerates a day of lag
print(search_tickets("vpn", field_needed="status")["advice"])   # index is 15 min behind; call get_ticket_live ...
```

- **The model decides with facts, not guesses:** lag and age are in the tool result, so the agent can
  caveat an answer or make the live call.

### Worked incident: a stalled replication slot
A consumer was decommissioned but its slot was left behind. Figures are illustrative.

| Quantity | Value |
|---|---|
| WAL generated by the source | 2 GB per hour |
| Free disk on the source volume | 50 GB |
| Time until the disk fills with no cap | about 25 hours |
| With `max_slot_wal_keep_size` set to 20 GB | slot invalidated after about 10 hours; the database is safe |
| Cost of the cap | the consumer must re-snapshot before streaming again |

The fix was a slot-lag alert at a fraction of free disk, a runbook to drop orphaned slots, and the cap as a
last line of defence.

### Worked scenario: embedding cost for a support agent's ticket index
Illustrative: 2 million tickets, 10,000 changed rows a day, of which about 1,500 have text changes (most
changes are status or assignee updates).

| Strategy | Re-embeds per day | Staleness |
|---|---|---|
| Nightly full reload | 2,000,000 | up to 24 hours |
| Watermark sync, no hash gate | about 30,000 (10,000 changes × about 3 overlapping runs) | up to about 5 minutes; up to 15 for late commits caught by the overlap |
| Watermark sync with hash gate | about 1,500 | same as above |
| Log-based CDC with hash gate | about 1,500 | seconds |

## Production patterns
- **Alert on retained WAL per slot.** Query `pg_replication_slots` with
  `pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)` on a schedule and page well before it threatens free
  disk; agree the threshold with the customer's DBA. Monitor inactive slots as well as lagging ones.
- **Heartbeats for quiet databases.** On low-traffic sources, configure the connector's heartbeat (Debezium
  has `heartbeat.interval.ms`) so the slot's confirmed position keeps advancing. Monitor slot position age.
- **Freshness metadata in every retrieval result.** Store source modified time and index time per chunk, and
  return them plus current sync lag from the agent's search tool. Monitor sync lag per source against the
  freshness target agreed for the agent's use case.
- **Schema registry plus tolerant consumers.** Register change-event schemas, ignore unknown added fields,
  and fail loudly on removed or retyped ones you depend on. Monitor consumer deserialization errors after
  every source migration.
- **Hash-gated re-embedding with a ratio metric.** Store a content hash per document and re-embed only when
  it changes. Monitor the re-embed ratio; near 1 on a metadata-heavy source means the gate is missing.
- **Periodic key and hash reconciliation.** Weekly, compare primary keys and content hashes between source
  and index, and repair drift. Monitor drift counts; non-zero trends point at a lost-delete or cursor bug.

## Trade-offs
- **Watermarks: no infrastructure vs trust.** They depend on a trustworthy `updated_at` and soft deletes.
  Move to log-based CDC when hard deletes, bulk updates or sub-minute freshness matter.
- **Log-based CDC: completeness vs operations.** It captures every committed change with minimal query load,
  but adds Kafka or a similar log, connector operations, schema-change handling and slot monitoring. It
  rarely pays off for one small table.
- **Source delta APIs: the only SaaS option vs vendor variance.** They handle deletes for you, but tokens
  expire and semantics vary per vendor, so you still need a full-resync path.
- **Wider overlap vs re-reads.** A wider window catches later-committing transactions but re-reads more rows
  each run; cheap with idempotent, hash-gated upserts and expensive without them.
- **Fresher index vs live tool calls.** Lower sync lag lets the agent answer from retrieval alone; a staler
  index is cheaper to run but pushes volatile questions to live lookups that cost source API calls per step.

## Pitfalls
- **`updated_at > last_run` with the worker's clock and no overlap.** Symptom: a small, steady number of
  records never appear in the index.
- **No delete path.** Symptom: the assistant cites documents deleted from the source weeks ago.
- **Re-embedding on every update.** Symptom: embedding cost tracks metadata churn, like ticket status
  changes, rather than content changes.
- **An abandoned PostgreSQL replication slot** after decommissioning a consumer. Symptom: the source
  database's disk usage climbs until the customer's DBA pages you.
- **Applying Debezium `after` images blindly** on a PostgreSQL source with default replica identity.
  Symptom: document bodies become a placeholder string after an unrelated metadata update, or deletes cannot
  be routed because `before` holds only the key.
- **Saving the cursor before changes are applied.** Symptom: changes lost permanently after a worker crash
  mid-batch.
- **Hiding index age from the agent.** Symptom: the agent states a ticket is "open" minutes after it closed,
  with no caveat. Fix: return lag and modified time with hits, and a live lookup tool.

## When to use
- **Full reloads:** small sources and early pilots.
- **Watermark or source delta sync:** once reload cost or staleness hurts, insisting on a delete mechanism.
- **Log-based CDC:** when you own or can access the database, need every change including hard deletes, or
  need low lag, and when someone will operate the pipeline.
- **Always:** keep a periodic full reconciliation.
- **Measure:** sync lag, changes applied per run, re-embed ratio (re-embeds over changes; a ratio near 1 on a
  metadata-heavy source means the hash gate is missing), deletes applied, replication-slot retained WAL for
  log-based CDC, and drift found by reconciliation (keys or hashes that differ between source and index).
- **Interviews:** explain why naive watermarks miss changes and how CDC avoids it; that is the expected depth.

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
**A:** Missing records: `updated_at` is set when the write happens (with `DEFAULT now()`, at transaction start,
even earlier), but the row becomes visible only when its transaction commits. A long transaction can commit after the sync advanced past that timestamp, so the row is
skipped forever; worker clock skew and shared boundary timestamps make it worse. Fix with the database clock
for the upper bound, an overlap window longer than the longest transaction, keyset paging on `(updated_at,
id)`, and idempotent upserts. Deleted records: hard deletes leave no row to match. Fix with soft deletes or a
tombstone table, or move to log-based CDC, which emits delete events, plus periodic key reconciliation.

## Interview questions
### Compare watermark sync, source delta APIs and log-based CDC.
- **Watermarks** need no infrastructure but trust `updated_at`, miss hard deletes and late commits without
  overlap and soft deletes.
- **Delta APIs** are the only option for SaaS sources and often report deletes, but tokens expire and
  semantics vary per vendor.
- **Log-based CDC** sees every committed change including deletes, at the cost of operating slots,
  connectors, a log and schema changes.

### After a metadata-only update, some indexed documents show a strange placeholder as their body. Why?
- **Unchanged TOASTed columns** can arrive in Debezium `after` images as a placeholder rather than the value.
- **The consumer wrote `after` blindly**, replacing the real body.
- **Fix:** keep the stored value when a column carries the placeholder, re-fetch when you never had it, or set
  `REPLICA IDENTITY FULL` if the larger log records are acceptable.

### When is log-based CDC not worth it?
- **One small table**, or a source that changes rarely: a full reload or watermark is simpler.
- **No one to operate it:** slots, connector upgrades and schema changes are ongoing work.
- **No database access:** for SaaS systems the vendor's delta API is the only path anyway.

### How would you size and protect a replication slot on a customer's production database?
- **Estimate WAL rate** and free disk to find how long a stalled consumer can survive.
- **Alert on retained WAL** per slot well before that, and on inactive slots.
- **Cap it** with `max_slot_wal_keep_size` on PostgreSQL 13+, accepting that an invalidated slot forces a
  re-snapshot.

### Your support agent told a customer a ticket was still open, but it closed ten minutes earlier. How do you fix the system?
- **Measure first:** sync lag for the ticket source at that moment, and whether the close event was
  applied, delayed or lost.
- **Make age visible:** return source modified time and index lag with each hit, so the agent can caveat.
- **Route volatile fields live:** for status questions beyond the lag tolerance, the agent calls a live
  lookup tool; for the pipeline, move toward CDC or webhooks if the target is seconds.

### Embedding spend for the agent's index doubled with no change in document volume. Where do you look?
- **The re-embed ratio:** if it rose toward 1, the hash gate broke or a migration started touching text
  columns.
- **Overlap and replays:** a wider overlap window or a reset cursor re-processes rows, harmless only when
  hash-gated.
- **Metadata churn:** a new bulk job updating status or ACLs is cheap with in-place metadata updates and
  expensive without them.

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

## Use cases & projects
### Use cases
- **Support and IT agents over ticket databases.** CDC or watermark sync keeps status, assignee and ACLs
  current in the index, with hash-gated re-embedding.
- **Contract and policy assistants.** Delete propagation removes documents withdrawn for legal reasons
  before the agent can cite them.
- **Agents over SaaS sources.** Graph delta tokens or Salesforce Change Data Capture feed the index without
  database access.
- **Event-driven agents.** A CDC stream of status changes triggers follow-up agents ("ticket reopened,
  summarise history for the new assignee").

### Mini project
**Fresh, delete-aware ticket index for a RAG agent (2-3 hours).**
- **Goal:** keep a small agent's ticket index current with watermark sync, deletes and hash-gated
  re-embedding, and expose freshness to the agent.
- **Steps:** create a local PostgreSQL `tickets` table with `updated_at` and `deleted_at`; write the
  watermark sync with overlap and keyset paging; store a content hash and timestamps per chunk; build a
  `search_tickets` tool that returns index lag; run a script that edits statuses, edits bodies and deletes rows.
- **Done when:** deleted tickets vanish from search within one run, status-only edits cause zero re-embeds,
  a row from a transaction held open for a few minutes (shorter than the overlap) still appears after it
  commits, and the agent caveats or calls a live lookup when
  lag exceeds the status tolerance.
