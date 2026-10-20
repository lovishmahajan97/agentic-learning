---
id: event-driven-architecture
module: system-design
name: Event-driven architecture
kind: pattern
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - system-design/synchronous-vs-asynchronous-execution
  - system-design/outbox-inbox-patterns
  - system-design/exactly-once-processing-limitations
  - system-design/saga-pattern-and-distributed-transactions
  - system-design/domain-driven-design-and-bounded-contexts
  - backend-fundamentals/message-queues
  - agent-reliability/structured-event-streams
  - observability/distributed-tracing-across-services
sources:
  - Hohpe & Woolf, 2003 — Enterprise Integration Patterns
  - Fowler, 2017 — What do you mean by "Event-Driven"?
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Official docs: Apache Kafka
---

## TL;DR
In an event-driven architecture a service records that something happened ("DocumentUploaded",
"RunCompleted") as an immutable event on a broker, and any number of consumers react on their own
schedule. The producer no longer knows or waits for its consumers, which is what lets you add a billing
meter, an eval sampler and an audit log to an agent pipeline without touching the agent. The price is
eventual consistency, at-least-once delivery and a flow of control that no single stack trace shows.

## Why it exists
Start with the naive design. The ingestion endpoint receives a PDF and then, in the same request,
calls the parser, the chunker, the embedder, the index writer, the notification service and the
analytics API. Three things go wrong as the system grows.

First, **temporal coupling**: the upload succeeds only if all six downstream services are up at that
instant. If each is available 99.9% of the time, the chain is available roughly 0.999⁶ ≈ 99.4%, and
its latency is the sum of six latencies. The slowest or flakiest consumer sets the user's experience.

Second, **knowledge coupling**: every new consumer (a compliance team wants a copy of every upload) means
a code change and a redeploy of the producer, plus a new failure mode inside the producer's request path.
The producer accumulates reasons to change that have nothing to do with uploading.

Third, **lost history**: once the request returns, the fact that an upload happened exists only as a row
the producer chose to keep. You cannot replay last Tuesday's uploads through a fixed chunker, because
the calls were ephemeral.

Events invert the dependency. The producer's only job is to durably record the fact. Consumers depend on
the event contract, not on the producer; the producer depends on nothing. Availability becomes "can I
write to the broker", and history becomes the log itself. That inversion, not the broker technology, is
the idea.

## How it works
### Events versus commands
An **event** is a past-tense, immutable statement of fact owned by the producer: `OrderPlaced`,
`EmbeddingGenerated`. It has no addressee and no expected reply. A **command** (`ChargeCard`) is
imperative, has exactly one handler and implies an outcome the sender cares about. The distinction
matters because an event lets the producer stay ignorant of consumers, whereas a command re-introduces
the coupling you were escaping. If you find yourself publishing `SendWelcomeEmail` as an event, you have
written a remote procedure call with extra steps.

### The envelope
Every event carries metadata separate from its payload: a globally unique `event_id` (so consumers can
deduplicate), `type` and `schema_version` (so consumers can evolve independently), `occurred_at`, the
`aggregate_id` it belongs to (the partition key, see below), and a `trace_id` for observability. The
payload is either a thin notification ("document 42 changed, fetch it") or a self-contained snapshot
("document 42 now has these fields"). Fowler calls these *event notification* and *event-carried state
transfer*; the second removes a callback to the producer at the cost of larger messages and consumers
holding copies of data that can go stale.

### The broker and its guarantees
A log-style broker (Kafka, Pulsar, Redpanda) appends events to **partitions** and lets each consumer
group keep its own **offset**. Ordering is guaranteed only within a partition, so you choose the
partition key to be the thing whose order matters, usually the aggregate id: all events for document 42
land in one partition and are seen in order, while documents 42 and 43 may interleave arbitrarily.
Because the log is retained, a new consumer can start from offset 0 and rebuild its state, which is the
replay property the naive design lacked. Queue-style brokers (RabbitMQ, SQS) instead delete a message
once a consumer acknowledges it and hand each message to one competing consumer; they are simpler for
work distribution but lose replay.

Both styles deliver **at least once**: a consumer that crashes after processing but before acknowledging
will see the message again. Consumers must therefore be idempotent, by recording processed `event_id`s or
by making the effect naturally repeatable. Exactly-once is a property you build at the consumer, not one
the broker gives you end to end: Kafka's transactional producers cover only Kafka-to-Kafka read-process-write
loops, and any effect outside the log (a database write, an HTTP call) still needs idempotence.

### Producing reliably
The producer writes its own database row and publishes the event. Doing those as two separate operations
is the **dual-write problem**: a crash between them loses the event or emits one for a rolled-back
transaction. The transactional outbox pattern fixes it by writing the event into the same database
transaction and relaying it afterwards.

### Choreography versus orchestration
With **choreography** each service reacts to events and emits new ones; the workflow is emergent. With
**orchestration** a dedicated component listens for events and issues commands to drive the steps. For
two or three steps choreography is lighter; beyond that nobody can answer "where is order 42 right now"
without an orchestrator or a very good trace. Multi-step agent workflows with compensation needs usually
end up orchestrated.

### Flavours worth naming
*Event sourcing* goes further and makes the event log the system of record, rebuilding current state by
folding the events. *CQRS* separates the write model from read models that are built from events. Both
are separate commitments with their own costs; "event-driven" does not require either.

## Example
A document-ingestion flow expressed as events. `publish` and `consume` stand in for any broker client;
the parts that matter are the envelope, the partition key and the idempotent consumer.

```python
import uuid, json, datetime as dt

def make_event(type_, aggregate_id, payload, trace_id, version=1):
    return {
        "event_id": str(uuid.uuid4()),      # dedupe key for consumers
        "type": type_, "schema_version": version,
        "aggregate_id": aggregate_id,       # partition key: per-document ordering
        "occurred_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "trace_id": trace_id, "payload": payload,
    }

# producer: the upload service knows nothing about who listens
def on_upload(doc_id, storage_uri, tenant_id, trace_id, publish):
    ev = make_event("DocumentUploaded", doc_id,
                    {"uri": storage_uri, "tenant_id": tenant_id}, trace_id)
    publish(topic="documents", key=doc_id, value=json.dumps(ev))

# consumer: embedder reacts, and tolerates redelivery
seen = set()   # in production: an inbox table in the same DB transaction as the effect

def on_document_uploaded(raw, embed_and_index, publish):
    ev = json.loads(raw)
    if ev["event_id"] in seen:            # at-least-once delivery => duplicates happen
        return
    chunk_count = embed_and_index(ev["payload"]["uri"], ev["payload"]["tenant_id"])
    seen.add(ev["event_id"])
    publish(topic="documents", key=ev["aggregate_id"], value=json.dumps(
        make_event("DocumentIndexed", ev["aggregate_id"],
                   {"chunks": chunk_count}, ev["trace_id"])))
```

Adding a billing meter later means one more consumer of `DocumentIndexed`; the upload service and the
embedder do not change. If the embedder is down for an hour, the events wait on the topic and are
processed when it returns, in per-document order.

## Trade-offs
- Producers gain independence from consumers, but you lose the synchronous answer: the uploader cannot
  tell the user "indexed, ask away" unless something waits for `DocumentIndexed`; this flips back toward
  a synchronous call when the user needs the result in the same interaction.
- Replayable logs let you rebuild indexes and backfill new consumers, but every event schema becomes a
  long-lived public contract; once events are retained for months, a breaking payload change needs
  versioning and dual-reading consumers.
- Choreography keeps services small and decoupled, yet end-to-end visibility degrades with each hop;
  past roughly four or five steps, or whenever compensation is needed, orchestration pays for itself.
- Event-carried state transfer removes callbacks and lets consumers survive producer outages, at the
  cost of duplicated data that drifts; thin notifications stay fresh but create a thundering herd of
  reads on the producer when a hot event fans out.
- At-least-once delivery with idempotent consumers is robust and cheap, but it pushes deduplication
  state into every consumer; exactly-once brokers trade throughput and operational complexity for it.

## Pitfalls
- Commands disguised as events (`SendEmailRequested`) with exactly one intended handler; the symptom is
  producers that break when "their" consumer changes, which means the coupling never left.
- Dual writes without an outbox: rows exist with no corresponding event, usually noticed as "the index
  is missing documents that are in the database" after a deploy or a crash.
- Assuming global ordering; a consumer that processes `DocumentDeleted` before `DocumentUploaded` because
  the two were keyed differently will resurrect deleted content in the index.
- Non-idempotent consumers; duplicate embeddings, double-counted billing and repeated notifications
  appear after any consumer restart or broker rebalance.
- Event soup: dozens of topics with no owner, no schema registry and no documented consumers; the
  symptom is that nobody dares delete or change anything, and the system's behaviour is discovered by
  reading consumer code.
- Treating the broker as infinitely fast: a slow consumer accumulates lag silently until a dashboard
  shows events processed hours after they occurred; consumer lag must be a first-class alert.

## When to use
Reach for events when more than one party cares about a fact, when consumers may be down while
producers are up, when you need to replay history, or when the producer's latency budget cannot absorb
the consumers' work. Typical AI examples: ingestion pipelines (upload, parse, chunk, embed, index),
agent run telemetry fanned out to billing, evaluation sampling and audit, and model-output moderation
that must not block the response. Do not use events when the caller needs the result now, when there is
exactly one consumer and the workflow is short, or when the team has no way to trace a flow across
services yet. Measure consumer lag, redelivery rate and dead-letter volume; those three numbers tell you
whether the architecture is healthy. In interviews, the expected depth is to state the dual-write
problem unprompted, explain per-partition ordering, and say "at-least-once plus idempotent consumers"
before anyone asks about exactly-once.

## Role lens
- **Engineer:** Designs event envelopes, chooses partition keys, writes idempotent consumers with an
  inbox table, and wires dead-letter queues and lag alerts for every consumer group.
- **Architect:** Decides which facts become published contracts, who owns each event's schema, where
  choreography ends and orchestration begins, and which flows must remain synchronous.
- **FDE:** Integrates with a customer's existing bus (often Kafka or SQS), maps their events onto the
  product's ingestion, and explains why the index trails the source system by seconds.

## Check yourself
**Q:** A customer wants uploaded contracts to be parsed, chunked, embedded and indexed, with an audit
record per stage and a Slack notification when indexing completes. The current implementation does all
of this inside the upload request and times out on large files. Redesign it with events and explain
what guarantees each stage has.
**A:** The upload handler stores the file, writes the document row and, in the same transaction, an
outbox row for `DocumentUploaded` keyed by document id; a relay publishes it, so the user gets a fast
"received" response and the event cannot be lost. Parser, chunker, embedder and indexer are consumer
groups on the same topic chain, each emitting its own completion event; keying every event by document
id keeps per-document order. Audit and Slack are additional consumers of those events, so they add no
latency and no failure modes to the pipeline. Each consumer records processed event ids in an inbox
table inside the same transaction as its effect, because the broker delivers at least once. The
notification consumer in particular must be idempotent, or every restart re-pings Slack. Failures go to
a dead-letter topic with alerts; lag per consumer group is the health metric. If the customer also needs
"ask questions immediately after upload", the UI waits for `DocumentIndexed` via a push channel rather
than making indexing synchronous.

## Quiz
### Q: What most clearly distinguishes an event from a command in a message-driven system?
- [ ] Events are JSON while commands use a binary encoding
- [x] An event states an immutable fact with no addressee; a command instructs one handler and implies an expected outcome
- [ ] Events are always delivered exactly once and commands at least once
- [ ] Commands can be replayed from the log but events cannot
> The difference is semantic, not technical. A fact ("OrderPlaced") lets the producer ignore consumers; an
> instruction ("ChargeCard") binds sender and handler and reintroduces coupling. Encoding and delivery
> guarantees are independent of which one you publish.

### Q: You need every event about the same document processed in order. What is the correct mechanism on a partitioned log?
- [ ] Enable "global ordering" on the topic
- [ ] Use a single consumer for the whole topic
- [x] Partition by document id so all events for one document share a partition
- [ ] Add a sequence number and have consumers sort incoming events by it
> Partitioned logs guarantee order only within a partition, so the partition key must be the entity whose
> order matters. A single consumer sacrifices throughput and still breaks if the producer spread the events
> across partitions; client-side sorting cannot know when the sequence is complete.

### Q: After a deploy, some rows exist in the orders table with no corresponding event on the bus. What is the likely cause?
- [ ] The broker's retention period expired
- [ ] Consumers were not idempotent and dropped duplicates
- [x] The service wrote the row and published the event as two separate operations and crashed in between
- [ ] The partition key was wrong, so events landed on an unread partition
> This is the dual-write problem: a database commit and a broker publish cannot be made atomic. A crash
> between them loses the event. The transactional outbox writes the event in the same database transaction
> and relays it afterwards, so an event exists if and only if the row does.
