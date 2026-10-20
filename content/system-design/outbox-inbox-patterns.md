---
id: outbox-inbox-patterns
module: system-design
name: Outbox / inbox patterns
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
related:
  - system-design/event-driven-architecture
  - system-design/exactly-once-processing-limitations
  - system-design/saga-pattern-and-distributed-transactions
  - backend-fundamentals/postgresql-transactions-and-isolation-levels
  - backend-fundamentals/message-queues
  - enterprise-integration/incremental-synchronization-and-cdc
  - enterprise-integration/retry-deduplication-reconciliation
  - fastapi-for-ai/idempotency-keys
sources:
  - Richardson, 2018 — Microservices Patterns
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Official docs: Debezium (outbox event router)
---

## TL;DR
A service cannot atomically commit a database row and publish a message. The **outbox** pattern writes
the message into a table inside the same transaction as the business change and lets a relay publish it,
so a message exists if and only if the change committed. The **inbox** pattern records each processed
message id inside the consumer's transaction, so redeliveries become no-ops. Together they turn an
at-least-once broker into effectively-once processing between two databases.

## Why it exists
The producer side of every event-driven system has to do two things when an order is placed: commit the
order row and tell the rest of the world. There are three naive orderings and each is wrong.

**Commit, then publish.** The process crashes, is OOM-killed or loses its broker connection after the
commit and before the publish. The row exists; the event never happens. Downstream, the search index,
the billing meter and the audit log never hear about the order. This is a silent failure: nothing errors,
data is simply missing.

**Publish, then commit.** The event is out; the commit fails on a constraint violation or a deadlock and
rolls back. Consumers now act on an order that does not exist.

**Publish inside the transaction, before commit.** The consumer receives the event, queries the producer
for details, and finds nothing, because the transaction has not committed yet. Or the transaction later
rolls back and the event is again a phantom.

The frequency is not academic. Suppose the crash window between the two operations is a millisecond and
the service processes 1,000 transactions per second; at an illustrative one restart per day you lose on
average about one event per day, and at scale that becomes "the index is missing a few hundred documents
and nobody knows which". Retrying the publish in application code does not help when the process itself
is gone. Wrapping both in a distributed transaction would require the broker to participate in two-phase
commit, which most brokers do not support and which blocks under coordinator failure.

The way out is to use the one atomic primitive you already have, the local database transaction, and to
make publishing a separate, retryable, idempotent step that reads from committed state.

## How it works
### The outbox table
Alongside the business tables lives an `outbox` table: `id` (monotonic), `aggregate_type`,
`aggregate_id`, `event_type`, `payload`, `created_at`, `published_at`. When the application commits a
business change it inserts the corresponding event row *in the same transaction*. Atomicity is now
guaranteed by the database: either both the order and its event row are durable or neither is. The event
is modelled as data, not as a side effect, *because* data is the only thing the transaction can protect.

### The relay
A separate process moves rows from the outbox to the broker. Two implementations exist.

**Polling publisher.** A loop runs `SELECT ... FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT n
FOR UPDATE SKIP LOCKED`, publishes each row, then marks it published (or deletes it). `SKIP LOCKED` lets
several relay instances run without double-claiming rows. It is simple and database-agnostic; its cost is
polling latency and load on the table. Selecting by `published_at IS NULL` rather than "rows with `id` above
the last one I published" is deliberate: ids come from a sequence assigned at insert time, but
transactions commit in a different order, so row 101 can be visible while row 100's transaction is still
open. A relay that advances a high-water mark past 101 never returns for 100, and that event is lost
without any error.

**Log tailing (CDC).** A connector such as Debezium reads the database's write-ahead log, sees committed
inserts into the outbox table in commit order, and forwards them to the broker. There is no polling, the
latency is milliseconds, and ordering follows commit order. The cost is operating a CDC pipeline and
granting it replication access.

Either way the relay can crash after publishing and before marking the row, so it will publish that row
again on restart. The outbox therefore gives **at-least-once** publication, never exactly-once. That is
acceptable precisely because the inbox exists on the other side.

### Ordering
Consumers typically need per-aggregate order. Publish with `aggregate_id` as the partition key, and have
the relay process rows in `id` order. With several polling relays, order across relays is not guaranteed
unless rows are partitioned among relays by aggregate as well; CDC avoids the problem by reading a single
commit-ordered log.

### Housekeeping
The outbox grows by one row per event forever unless pruned. Delete or archive rows after publication
with a lag that covers your debugging needs; a table with hundreds of millions of published rows makes the
`WHERE published_at IS NULL` scan slow unless a partial index exists.

### The inbox table
On the consumer, every message handler runs inside a database transaction that does two things: performs
the business effect and inserts the message's id into an `inbox` table with a unique constraint
(`INSERT ... ON CONFLICT DO NOTHING`). If the insert affects zero rows the message was already handled,
and the handler commits nothing. Doing the inbox insert *first*, before the effect, also handles two
copies arriving concurrently on different consumer instances: in Postgres the second insert waits on the
unique index until the first transaction commits, then finds the conflict and does nothing, so the
unique constraint, not application logic, serialises the duplicates. Because the dedupe record and the effect commit together, a crash
between them cannot leave the effect applied but unrecorded, or recorded but unapplied. The dedupe key
must be the producer's `event_id`, not the broker's delivery id, *because* a redelivery carries the same
event with a new delivery id.

A variant stores the full message in the inbox first, acknowledges to the broker, and processes from the
inbox asynchronously. This decouples receipt from processing and gives the consumer its own durable
queue, at the cost of another relay-like loop.

### What the pair does and does not guarantee
Outbox plus inbox plus an at-least-once broker gives effectively-once effects from the producer's
database to the consumer's database. It does not make effects outside the consumer's database idempotent:
if the handler calls an external API, sends an email or invokes a model, a duplicate that slips in
between the effect and the commit still repeats that call. Those effects need their own idempotency key at
the external boundary.

## Example
Tables and the three code paths: the producer writes order and outbox row in one transaction, a polling
relay publishes with `SKIP LOCKED`, and a consumer deduplicates through the inbox. Uses psycopg 3; the
`broker.publish` call stands in for a Kafka or SQS client.

```sql
CREATE TABLE outbox (
  id BIGSERIAL PRIMARY KEY, aggregate_id TEXT NOT NULL, event_type TEXT NOT NULL,
  payload JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now(), published_at TIMESTAMPTZ);
CREATE INDEX outbox_unpublished ON outbox (id) WHERE published_at IS NULL;
CREATE TABLE inbox (event_id UUID PRIMARY KEY, handled_at TIMESTAMPTZ DEFAULT now());
```

```python
import json, uuid, psycopg

def place_order(conn, order):                       # producer: one transaction, two inserts
    with conn.transaction():
        conn.execute("INSERT INTO orders (id, total) VALUES (%s, %s)", (order["id"], order["total"]))
        conn.execute("INSERT INTO outbox (aggregate_id, event_type, payload) VALUES (%s, %s, %s)",
                     (order["id"], "OrderPlaced",
                      json.dumps({"event_id": str(uuid.uuid4()), **order})))

def relay_once(conn, broker, batch=100):            # N copies won't double-claim, but can reorder
    with conn.transaction():
        rows = conn.execute("""SELECT id, aggregate_id, event_type, payload FROM outbox
                               WHERE published_at IS NULL ORDER BY id LIMIT %s
                               FOR UPDATE SKIP LOCKED""", (batch,)).fetchall()
        for id_, agg, etype, payload in rows:
            broker.publish(topic="orders", key=agg, value=json.dumps({"type": etype, **payload}))
            conn.execute("UPDATE outbox SET published_at = now() WHERE id = %s", (id_,))
    return len(rows)   # crash after publish, before commit => republish: at-least-once

def handle_order_placed(conn, msg):                 # consumer: effect + inbox in one transaction
    with conn.transaction():
        cur = conn.execute("INSERT INTO inbox (event_id) VALUES (%s) ON CONFLICT DO NOTHING",
                           (msg["event_id"],))
        if cur.rowcount == 0:
            return "duplicate"                      # already handled: commit nothing new
        conn.execute("INSERT INTO revenue_ledger (order_id, amount) VALUES (%s, %s)",
                     (msg["id"], msg["total"]))
    return "handled"
```

Kill the relay between `broker.publish` and the `UPDATE` and the row is published twice; the consumer's
second `INSERT INTO inbox` conflicts, `rowcount` is zero, and the ledger is written exactly once. Two
production details the sketch glosses: the relay publishes while holding row locks, so a slow broker
stretches the transaction, which is why real relays use small batches and a short publish timeout; and
with several relays running, two events for the same order can be claimed by different relays and
published in the wrong order, so either run one relay per partition of aggregates or use CDC.

## Trade-offs
- You gain a hard guarantee that events match committed state, but every event now costs a database
  write and a relay hop, adding latency of tens of milliseconds with polling (less with CDC) compared with
  publishing directly.
- A polling relay is trivial to run anywhere, whereas CDC gives lower latency and commit ordering but
  requires replication privileges and a connector to operate; polling flips to CDC once event volume or
  latency sensitivity makes table scans costly.
- The inbox makes any handler idempotent regardless of its logic, yet it adds a write and a growing
  table per consumer; when the effect is naturally idempotent (an upsert keyed by aggregate id) the
  inbox is redundant and can be skipped.
- Keeping outbox and inbox rows longer eases debugging and widens the dedupe window, but table size and
  index bloat grow linearly; the retention must at least exceed the broker's maximum redelivery delay.
- The pattern couples publishing to the service's database schema and transaction, which is the point,
  but it means services without a relational store (or using a database per request) need a different
  mechanism, such as a broker with transactional producers.

## Pitfalls
- Publishing from a database trigger or an ORM "after commit" hook that calls the broker directly; this is
  still a dual write and shows up as occasional missing events after deploys and failovers.
- Running multiple polling relays without `SKIP LOCKED` or partitioning; the same event is published
  several times in a burst and per-aggregate order breaks, seen as out-of-order status transitions
  downstream.
- Deduplicating on the broker's message or receipt id instead of the producer's `event_id`; redeliveries
  carry new ids and sail past the inbox, so duplicates reappear exactly when the broker retries.
- A relay that tracks "last published id" instead of querying unpublished rows; events from
  transactions that committed out of sequence order are skipped forever, showing up as rare, permanent
  gaps that no retry ever fills.
- Letting the outbox grow unbounded with no partial index; relay latency creeps from milliseconds to
  seconds as the unpublished scan degrades.
- Treating the inbox as protection for external calls; the handler emails the customer, then crashes
  before commit, the redelivery emails again, and support hears about duplicate messages.
- Writing a huge payload per event; outbox rows bloat the primary database and the WAL, slowing every
  unrelated transaction, which argues for thin events with an id to fetch.

## When to use
Use the outbox whenever a service both persists state in a transactional database and must publish
messages about that state, which describes nearly every service in an event-driven system: order
services, document-ingestion services recording upload rows, agent runtimes persisting step results
while emitting telemetry. Use the inbox in any consumer whose effect is not naturally idempotent (ledgers,
counters, "send once" notifications, calls that append rather than upsert). Skip both when there is no
database in the loop, when the broker itself is the system of record, or when a lost message is
genuinely harmless. Measure outbox lag (oldest unpublished row age), relay throughput, duplicate rate at
the inbox and table sizes. In interviews, the expected move is to raise the dual-write problem yourself,
sketch the outbox with `SKIP LOCKED` or CDC, and state plainly that the result is at-least-once plus
consumer-side deduplication rather than exactly-once delivery.

## Role lens
- **Engineer:** Adds the outbox insert to the same transaction as the business write, runs the relay as a
  separate process, and guards every handler with an inbox insert keyed by event id.
- **Architect:** Standardises the outbox and inbox schemas, event envelope and relay technology across
  services, and decides where CDC replaces polling.
- **FDE:** Recognises dual writes in a customer's integration code (webhook handlers that update their CRM
  then call the product) and introduces an inbox so redelivered webhooks do not double-process.

## Check yourself
**Q:** Your ingestion service writes a document row and then publishes `DocumentUploaded` to Kafka. After
a Kubernetes node failure, 37 documents exist in Postgres with no chunks in the vector index. Explain the
cause and redesign the write path so it cannot recur, including what happens on relay crashes.
**A:** The service committed rows and was killed before the publish calls completed, a textbook dual
write; nothing retried because the process was gone. Redesign: insert the `DocumentUploaded` event into
an outbox table inside the same transaction as the document row, so the pair is atomic. A relay, either a
polling loop using `FOR UPDATE SKIP LOCKED` or Debezium tailing the WAL, publishes unpublished rows to
Kafka keyed by document id and marks them published. If the relay crashes after publishing and before
marking, it republishes, so the indexer must be idempotent. If the chunks live in Postgres (pgvector),
the indexer inserts the event id into an inbox table in the same transaction as writing the chunks and
skips the work on conflict. If the vector store is a separate system, no shared transaction exists, so
idempotence has to come from the effect: deterministic chunk ids such as `doc_id:chunk_no` and upserts,
so a republished event overwrites instead of duplicating. Backfilling the 37 missing
documents is now a one-off insert of outbox rows for them. Add an alert on the age of the oldest
unpublished outbox row and on inbox duplicate rate.

## Quiz
### Q: What exactly does the transactional outbox guarantee?
- [ ] Each event is delivered to consumers exactly once
- [x] An event row is durable if and only if the business transaction that created it committed
- [ ] Events are delivered to consumers in global order
- [ ] Consumers never see duplicates
> The outbox moves the event into the same local transaction as the state change, which is the only
> atomicity the database can give. Publication by the relay is still at-least-once, so duplicates and
> consumer-side deduplication remain part of the design.

### Q: Why must the inbox deduplicate on the producer's event id rather than the broker's delivery id?
- [ ] Delivery ids are too long to index efficiently
- [ ] Producers never generate ids, so the broker id is the only option
- [x] A redelivered message carries the same event id but may carry a new delivery id
- [ ] The broker id changes when the consumer group rebalances, but the event id also changes
> Deduplication must identify "the same event", and only the producer-assigned id is stable across
> redeliveries, relay republishes and consumer rebalances. Keying on a per-attempt delivery id lets exactly
> the duplicates you wanted to catch through.

### Q: The consumer handler sends an email and then commits the inbox row. A redelivery arrives after a crash between the two. What happens?
- [ ] The inbox prevents the second email because the first send was recorded
- [x] The email is sent twice, because the inbox only protects effects inside the consumer's transaction
- [ ] The broker detects the duplicate and drops it
- [ ] The first email is automatically recalled
> Outbox and inbox give effectively-once effects between databases; an external side effect that happens
> before the commit is outside the transaction and repeats on redelivery. External calls need their own
> idempotency key at the provider or must be moved after the commit with their own at-least-once loop.
