---
id: message-queues
module: backend-fundamentals
name: Message queues (Kafka, SQS, RabbitMQ)
kind: concept
difficulty: core
est_minutes: 18
related:
  - backend-fundamentals/background-jobs-durable-workers
  - backend-fundamentals/redis
  - system-design/event-driven-architecture
  - system-design/outbox-inbox-patterns
  - system-design/exactly-once-processing-limitations
  - fastapi-for-ai/job-queues-and-durable-workers
  - reliability/backpressure-and-load-shedding
  - infra-capacity-economics/queueing-theory
sources:
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Official docs: Apache Kafka
  - Official docs: Amazon SQS
  - Official docs: RabbitMQ
---

## TL;DR
A message queue puts a durable buffer between the code that produces work and the code that does it, so
the two no longer have to be up, fast, or scaled the same way at the same moment. Every real broker
delivers at least once, so consumers must be idempotent. Kafka is a replayable partitioned log, SQS is a
managed queue with no replay, and RabbitMQ is a flexible-routing broker; the choice follows from whether
you need ordering, replay, routing or just a simple buffer.

## Why it exists
A request that triggers five downstream actions (store the upload, extract text, embed chunks, update
the index, notify the user) cannot do them inline: the HTTP call would take minutes, any one failure
fails the whole request, and a burst of uploads would hit every downstream service at the burst rate.
Calling the services synchronously couples their availability (if the embedder is down, uploads fail),
their capacity (the slowest service sets the pace), and their deployment (a change in one means retries
across all). The naive fix, "retry in a background thread", loses the work when the process restarts and
still has no buffer when the downstream is slower than the upstream.

A queue solves the three couplings at once. Producers write a message and return; the broker stores it
durably; consumers pull at their own rate; if a consumer is down the messages wait. The same mechanism
gives fan-out (several consumer groups read the same event), load levelling (a burst becomes a backlog
that drains), and a natural place to put retries and dead-letter handling. In AI systems the pattern
appears constantly: document ingestion pipelines, batch embedding, webhook buffering from providers,
decoupling an agent's tool calls from the services that execute them, and emitting events (run started,
tool failed, tokens consumed) that observability and billing consume independently.

The price is that the system becomes asynchronous and eventually consistent, and that every consumer
must tolerate seeing the same message twice.

## How it works
### The core loop
A producer publishes a message to a named destination (queue, topic, exchange). The broker persists it
and makes it available. A consumer receives it, does the work, and **acknowledges** it; only then does the
broker consider it done. If the consumer crashes before acknowledging, the broker redelivers to another
consumer. This ack-after-work protocol is what makes delivery **at-least-once**: a consumer that finishes
the work and dies before the ack causes a duplicate. Acknowledging *before* the work gives
**at-most-once**: no duplicates, but a crash loses the message. "Exactly-once" across arbitrary side
effects is not achievable; what systems offer is at-least-once delivery plus **idempotent** consumers
(deduplicate by message id or business key), which is effectively-once from the outside.

### Ordering, partitions and parallelism
A single queue consumed by one worker is ordered; the moment you add workers, messages complete out of
order. Kafka's answer is **partitions**: a topic is split into N ordered logs, a message's key decides its
partition (same key, same partition, strict order), and a **consumer group** assigns each partition to
exactly one consumer in the group. Parallelism is therefore capped by partition count, and a skewed key
(one huge tenant) makes one partition hot. SQS FIFO queues offer the same idea with message group ids;
SQS standard queues and RabbitMQ classic queues give best-effort ordering only.

### Backpressure and retries
A queue only levels load if someone watches the **lag** (messages waiting, or the age of the oldest
message); an unbounded backlog is a silent outage. Consumers limit in-flight messages (RabbitMQ
`prefetch`, SQS `MaxNumberOfMessages`, Kafka `max.poll.records`). Failed messages are retried with
backoff, and after N attempts go to a **dead-letter queue** (DLQ) so one poison message cannot block the
rest. A message whose payload is large (a PDF, a transcript) should not travel through the broker:
store it in object storage and send the reference (the claim-check pattern).

### Kafka: a distributed, replayable log
Kafka keeps messages on disk for a configured retention (time or size), regardless of whether they were
consumed. Consumers track their own **offset** per partition and commit it; replaying means resetting the
offset. Durability comes from replicating each partition to several brokers and, for producers,
`acks=all` plus the idempotent producer setting, which deduplicates retries within a session. Log
compaction keeps the latest message per key, turning a topic into a changelog. Kafka is built for high
throughput, many consumers of the same stream, and event sourcing; it costs an operations burden (or a
managed service) and a rigid partition-based parallelism model. It is a poor fit for a small job queue
where each job needs individual retry and priority.

### SQS: a managed queue without replay
SQS is a fully managed queue: no brokers to run, near-unlimited throughput on standard queues, and
pay-per-request. A consumer receives messages with a **visibility timeout**; the message is hidden from
others until the consumer deletes it or the timeout expires, at which point it reappears. Long polling
waits up to 20 seconds for messages instead of returning empty. Retention is bounded (days, not forever),
messages are deleted when acknowledged, so there is no replay; a redrive policy moves messages to a DLQ
after `maxReceiveCount` attempts. Standard queues are at-least-once with best-effort ordering; FIFO
queues add ordering per message group and a deduplication id honoured for a five-minute window. Message
size is small (a quarter of a megabyte historically; check current limits), so payloads go to S3.

### RabbitMQ: a routing broker
RabbitMQ implements AMQP: producers publish to an **exchange**, bindings route messages to **queues** by
routing key (direct), pattern (topic), to all bound queues (fanout), or by headers. This makes complex
routing and per-consumer queues easy. Consumers ack or nack; `prefetch` bounds in-flight messages per
consumer; dead-letter exchanges handle rejects and TTL expiry. Quorum queues replicate via Raft for
durability; the newer streams feature adds a Kafka-like replayable log. It is a good fit for task
distribution with rich routing and moderate throughput, and it needs operating like any stateful system.

### Choosing
Need replay, many independent consumers of the same events, or very high throughput: Kafka (or a managed
equivalent). Need a simple durable buffer with minimal operations on AWS: SQS. Need routing rules,
per-message priorities or AMQP interop: RabbitMQ. Need a small job queue and already run PostgreSQL: a
table with `SKIP LOCKED` is often enough, and Redis-backed queues sit between that and a real broker.

## Example
An SQS consumer for an embedding job. It long-polls, processes each message idempotently (the job id is
the dedup key stored with the result), extends the visibility timeout while a slow model call runs so the
message is not redelivered mid-flight, and deletes only after success. Failures are left to expire and
retry; the queue's redrive policy sends repeat offenders to a DLQ.

```python
import json, threading, boto3

sqs = boto3.client("sqs")
QUEUE = "https://sqs.us-east-1.amazonaws.com/123456789012/embed-jobs"

def extend_visibility(receipt, stop, every=30, extra=60):
    while not stop.wait(every):                        # heartbeat while work is in flight
        sqs.change_message_visibility(QueueUrl=QUEUE, ReceiptHandle=receipt,
                                      VisibilityTimeout=extra)

def process(job):
    if results.exists(job["job_id"]):                  # idempotent: seen this job already
        return
    vectors = embed(load_chunks(job["document_id"]))   # the slow call
    results.store(job["job_id"], vectors)              # write result keyed by job id

while True:
    resp = sqs.receive_message(QueueUrl=QUEUE, MaxNumberOfMessages=5,
                               WaitTimeSeconds=20, VisibilityTimeout=60)
    for msg in resp.get("Messages", []):
        stop = threading.Event()
        hb = threading.Thread(target=extend_visibility,
                              args=(msg["ReceiptHandle"], stop), daemon=True)
        hb.start()
        try:
            process(json.loads(msg["Body"]))
            sqs.delete_message(QueueUrl=QUEUE, ReceiptHandle=msg["ReceiptHandle"])
        except Exception as e:
            log.warning("job failed, will be redelivered: %s", e)   # no delete: retry later
        finally:
            stop.set()
```

`results`, `embed`, `load_chunks` and `log` are the application's own; the shape of the loop is what
matters. With Kafka the same loop would poll a partition, process, then commit the offset; with RabbitMQ
it would `basic_consume` with `prefetch_count=5` and `basic_ack` after success.

## Trade-offs
- Decoupling producers from consumers gains availability and burst tolerance, but the caller no longer
  knows when (or whether) the work finished, so every flow needs a status path or an event back.
- At-least-once delivery never loses acknowledged work, but it forces idempotent consumers; at-most-once
  is simpler and acceptable only for data you can afford to drop, such as metrics samples.
- Kafka's retained log allows replay and many consumer groups, but operations cost, partition planning
  and the lack of per-message retry make it heavy for a plain job queue.
- SQS removes all broker operations and scales without planning, but no replay, bounded retention and a
  small message size push payloads to S3 and audit history to a database.
- RabbitMQ's exchanges give fine-grained routing and priorities, but a single very hot queue is harder
  to scale than Kafka partitions, and the cluster must be run and upgraded.
- Partition-keyed ordering preserves per-entity sequence, but parallelism is capped by partition count
  and a skewed key concentrates load; the trade flips toward unordered queues when order does not matter.

## Pitfalls
- Non-idempotent consumers; the symptom is duplicate emails, double charges or two embeddings per chunk
  after any consumer restart or timeout.
- A visibility timeout or ack deadline shorter than the work (a 90-second model call under a 30-second
  timeout); the symptom is the same job running on two workers and redelivery counts climbing under load.
- No dead-letter queue; the symptom is one malformed message redelivered forever, consuming a worker and
  hiding behind it a growing backlog.
- Committing the Kafka offset (or deleting the SQS message) before processing; the symptom is work that
  silently disappears whenever a worker crashes.
- Nobody alerting on queue lag or oldest-message age; the symptom is a backlog discovered hours later by
  a user asking why their document never got indexed.
- Choosing Kafka for a few messages per second of job traffic; the symptom is a team spending its time on
  partition rebalances and consumer-group tuning instead of the product.
- Assuming order on SQS standard queues or across Kafka partitions; the symptom is "updated" events
  processed before "created" events and intermittent not-found errors.
- Sending multi-megabyte payloads through the broker; the symptom is size errors, slow brokers and a
  broker that has become an accidental blob store.

## When to use
Introduce a queue when work must outlive the request, when a downstream is slower or less available than
the caller, when several consumers need the same events, or when bursts must be smoothed. Do not
introduce one for a synchronous read the user is waiting on, or when a database table with `SKIP LOCKED`
would carry the load without a new system. Measure lag, oldest-message age, redelivery rate and DLQ
depth from day one. Day to day this shows up as "should this endpoint enqueue or call directly", choosing
a partition key, and tuning timeouts to the real duration of an LLM call. In interviews, expect to
explain at-least-once, design a partition key for a tenant-skewed workload, and justify Kafka versus SQS
for a given scenario.

## Role lens
- **Engineer:** Writes idempotent consumers, sets visibility or ack timeouts to match model latency,
  wires the DLQ and alerts on lag before the first production backlog.
- **Architect:** Chooses the broker per workload (log versus queue), defines topics, keys and retention,
  and decides where the status of asynchronous work is recorded for callers.
- **FDE:** Integrates with the customer's existing Kafka or SQS, negotiates message schemas and
  ordering guarantees, and sizes consumers against their throughput and quota limits.

## Check yourself
**Q:** A document pipeline on SQS standard occasionally embeds the same chunk twice and sometimes
processes a document's "deleted" event before its "created" event. What is happening and how would you
change the design?
**A:** Both are expected properties of a standard queue. Duplicate embeddings come from at-least-once
delivery: a worker finished the model call but crashed or exceeded its visibility timeout before deleting
the message, so it was redelivered. The fix is an idempotent consumer: key each result by chunk id or
job id and skip or overwrite when it already exists, and set the visibility timeout above the real
processing time with heartbeat extension for long calls. The ordering problem comes from standard queues
offering only best-effort order across many workers. Either move to a FIFO queue with the document id as
the message group id, so events for one document are serialized while different documents proceed in
parallel, or make the consumer order-independent by storing a version per document and ignoring events
older than the current state. Add a DLQ so poison messages do not block either path.

## Quiz
### Q: Why is "exactly-once delivery" not something a broker can promise for a consumer that calls an external API?
- [ ] Brokers lose messages under load, so at-least-once is the best achievable
- [x] The consumer can complete the external call and then fail before acknowledging, so the broker cannot know whether the side effect happened and must redeliver
- [ ] External APIs do not support message acknowledgements
- [ ] Exactly-once requires FIFO queues, which most brokers lack
> The acknowledgement and the side effect are separate operations that cannot be made atomic across
> systems. Redelivery is the safe default, so idempotency on the consumer side is what makes the outcome
> effectively once.

### Q: A Kafka topic has 4 partitions and a consumer group has 8 consumers. What happens?
- [ ] Each partition is read by two consumers for redundancy
- [ ] Kafka automatically increases the partition count to 8
- [x] Four consumers sit idle, because each partition is assigned to at most one consumer in a group
- [ ] Messages are round-robined across all 8 consumers regardless of partition
> Parallelism within a consumer group is bounded by partition count. Extra consumers are standby only,
> which is why partition count must be planned for peak consumer parallelism.

### Q: Which situation makes SQS a better choice than Kafka?
- [ ] Multiple independent teams need to replay the same event history
- [x] A team needs a durable buffer for background jobs with minimal operations and no need to re-read past messages
- [ ] Strict ordering of every message across the whole topic is required
- [ ] Messages must be retained indefinitely as the system of record
> SQS offers a managed, pay-per-use queue with retries and dead-lettering but deletes messages on
> acknowledgement and bounds retention. Replay, long retention and many consumer groups are Kafka's strengths.
