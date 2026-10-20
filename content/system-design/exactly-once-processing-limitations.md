---
id: exactly-once-processing-limitations
module: system-design
name: Exactly-once processing limitations
kind: concept
difficulty: advanced
est_minutes: 15
related:
  - system-design/outbox-inbox-patterns
  - system-design/event-driven-architecture
  - system-design/distributed-systems
  - agent-reliability/idempotent-tool-execution
  - agent-reliability/durable-execution-and-checkpointing
  - serving-inference/idempotency
  - fastapi-for-ai/idempotency-keys
  - enterprise-integration/retry-deduplication-reconciliation
sources:
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Gray, 1978 — Notes on Data Base Operating Systems
  - Official docs: Apache Kafka (idempotent producer and transactions)
  - Official docs: Apache Flink (checkpointing and exactly-once sinks)
---

## TL;DR
Exactly-once *delivery* over an unreliable network is impossible: a sender without an acknowledgement
cannot tell whether the message or the ack was lost, so it must risk a duplicate or a loss. What you can
build is exactly-once *effect*: at-least-once delivery plus deduplication or idempotent processing, with
the dedupe state committed together with the effect. Every "exactly-once" feature, Kafka transactions
included, stops at that boundary; external API calls and LLM invocations beyond it are not covered.

## Why it exists
Every retry policy, every queue and every agent framework eventually meets the same question from a
product owner: "can you guarantee this runs exactly once?" The honest answer needs the reasoning, because
the marketing answer ("our broker supports exactly-once") hides where the guarantee ends.

Picture a consumer that reads a message, applies its effect and acknowledges. Three failure points exist:
before the effect (safe to redeliver), after the effect but before the ack (redelivery duplicates the
effect), and after the ack (fine). The middle case is unavoidable because "apply effect" and "send ack"
are two operations on two systems. If the consumer acks *before* applying the effect, a crash in between
loses the message instead. Those are the only two choices a protocol can make: **at-most-once** (ack
first, may lose) or **at-least-once** (ack last, may duplicate). Jim Gray's framing of the two generals
problem makes it exact: two parties exchanging messages over a channel that can drop them can never reach
certainty that both know the outcome, no matter how many confirmations they send, because the last
confirmation could always be the one that was lost.

The naive escape is "make the network reliable" with TCP, retries and timeouts. TCP gives exactly-once
*within a connection*; the moment a connection breaks and the client opens a new one and retries, the
server sees the request twice. Timeouts make it worse: a request that timed out may have been processed,
and the client simply does not know. This is the state an LLM-calling service is in several times an
hour.

So the problem is reframed. Nobody needs the *message* exactly once; they need the *effect* exactly once.
That is achievable, with conditions, and the conditions are the content of this lesson.

## How it works
### Effectively-once: the recipe
Take at-least-once delivery and make processing idempotent. Two mechanisms do this:

1. **Deduplication.** The consumer records each processed message id (an inbox table, a Redis set with
   TTL, Kafka's per-partition sequence number) and skips repeats. It works for any effect, but only if the
   record and the effect commit atomically, which is why the inbox row lives in the same database
   transaction as the business write.
2. **Natural idempotency.** The effect is defined so that applying it twice equals applying it once:
   `SET status='paid'`, an upsert keyed by order id, `PUT` rather than `POST`. No dedupe state is needed,
   but the effect must be formulated as an absolute state rather than a delta (`balance += 10` is not
   idempotent; `balance = 110 WHERE version = 7` is).

### What Kafka's "exactly-once semantics" actually covers
The **idempotent producer** attaches a producer id and a per-partition sequence number to every batch;
the broker rejects a batch whose sequence it has already appended, so a producer retry after a lost ack
does not create a duplicate record. **Transactions** extend this to read-process-write loops entirely
inside Kafka: a consumer reads from topic A, produces to topic B, and commits its input offsets as part
of the same transaction, so either both the output records and the offset advance or neither does.
Consumers configured with `read_committed` never see records from aborted transactions. This is genuine
exactly-once for stream processing whose input and output are both Kafka topics. The limitation is the
boundary: if the processing step calls a payment API or writes to Postgres, those effects are outside the
transaction and the standard at-least-once reasoning returns. Flink's checkpoint-based exactly-once has
the same shape: operator state is restored consistently from the last checkpoint, and sinks are
exactly-once only when they are idempotent or support a two-phase-commit protocol.

### Where it breaks: the limitations
- **External side effects.** Any effect in a system that does not share the transaction (HTTP APIs, email,
  file storage, a model call) is at-least-once unless that system accepts an idempotency key and
  deduplicates for you. Payment providers do; most internal services and most LLM endpoints do not.
- **Finite dedupe windows.** Ids are kept for a window (a table retention, a TTL, Kafka's producer-session
  state). A replay or redelivery that arrives after the window is treated as new. The window must exceed
  the longest possible redelivery delay, including the dead-letter queue's replay time.
- **Key stability.** Deduplication needs the same key on every attempt. A client that generates a fresh
  UUID per retry defeats it. The key must be created once, before the first attempt, and persisted by the
  caller; for agent steps it is derived from `(run_id, step_id)`, never from the attempt number or the
  current time.
- **Non-deterministic processing.** Replaying a step that calls a model yields a different output and
  possibly different downstream tool arguments. Idempotency then means *memoisation*: store the output
  under the key on first success and return the stored output on every later attempt. Durable-execution
  engines do exactly this.
- **Unknown outcomes.** When a call times out, dedupe alone does not tell you whether the effect happened.
  The only resolution is to query the receiver by key (reconciliation) before retrying or compensating.
- **Ordering across keys.** Dedupe is per key; two different messages that must be applied in order are a
  separate problem, solved by partitioning, not by idempotency.
- **Cost.** Transactional producers add coordinator round trips and reduce throughput; inbox tables add a
  write and an index per message. Exactly-once is paid for per message, which argues for applying it only
  where duplicates actually hurt.
- **Human-visible effects.** A duplicate notification cannot be deduplicated after the fact; the dedupe has
  to sit before the send, and the send itself must be the last thing before the commit, not the first.

### A boundary test
For each effect a handler produces, ask: does the dedupe record commit atomically with this effect? If
yes, the effect is exactly-once within the dedupe window. If no, it is at-least-once and needs either an
idempotency key honoured by the receiver, a memoised result, or acceptance of duplicates.

## Example
An agent tool step that must happen at most once per `(run_id, step_id)`, even though the worker can be
killed at any point and the step itself (a model call that produces a message) is non-deterministic. The
result is memoised under the key inside the same transaction that marks the step done; an external send
is made idempotent by passing the same key to the provider.

```python
import json, psycopg

# CREATE TABLE step_results (step_key TEXT PRIMARY KEY, result JSONB NOT NULL,
#                            committed_at TIMESTAMPTZ DEFAULT now());

def run_step_once(conn, run_id, step_id, llm, mailer, prompt):
    key = f"{run_id}:{step_id}"                       # stable across every retry
    row = conn.execute("SELECT result FROM step_results WHERE step_key = %s", (key,)).fetchone()
    if row:
        return row[0]                                 # replay: reuse the memoised output

    draft = llm.complete(prompt)                      # non-deterministic; must not be redone later
    # the external send is at-least-once from our side; the provider dedupes on this key
    mailer.send(to="customer@example.com", body=draft, idempotency_key=key)

    with conn.transaction():
        cur = conn.execute("""INSERT INTO step_results (step_key, result) VALUES (%s, %s)
                              ON CONFLICT (step_key) DO NOTHING""", (key, json.dumps({"draft": draft})))
        if cur.rowcount == 0:                         # a concurrent worker won the race
            return conn.execute("SELECT result FROM step_results WHERE step_key = %s",
                                (key,)).fetchone()[0]
    return {"draft": draft}

# Failure analysis:
# - killed before llm.complete: nothing happened; retry is clean.
# - killed after mailer.send, before INSERT: retry calls the LLM again (new draft!) and sends again;
#   the provider's idempotency key suppresses the second email, but the memoised draft may differ
#   from what was sent. To close that gap, persist the draft BEFORE sending, then send, then mark sent.
# - killed after INSERT: retry returns the stored result; no model call, no send.
```

The comment block is the point: even with a dedupe table and a provider-side idempotency key, the order
of persist, send and commit decides which duplicates remain possible. The fully safe version is three
phases (persist draft, send with key, mark sent), each idempotent on its own.

## Trade-offs
- At-least-once with idempotent consumers is cheap, broker-agnostic and robust, but it pushes dedupe
  state and key discipline into every consumer; broker-level transactions remove that burden only for
  broker-to-broker pipelines.
- Kafka transactions give true exactly-once inside Kafka at the cost of throughput and latency from
  coordinator round trips; for pipelines whose sink is a database or an API they add cost without
  extending the guarantee.
- Long dedupe windows catch late redeliveries but grow storage and slow the lookup; short windows are
  cheap but let a replayed dead-letter message through as a fresh event.
- Memoising non-deterministic outputs makes agent replays reproducible and avoids repeated model spend,
  yet it freezes a possibly poor output; a deliberate "regenerate" must use a new key, which is a
  product decision, not a retry.
- Accepting duplicates is sometimes the right trade: for an idempotent upsert into a search index, the
  cost of a duplicate is a wasted write, which is cheaper than any dedupe machinery.

## Pitfalls
- Believing the broker's "exactly-once" flag covers the HTTP call inside the handler; the symptom is
  duplicate charges or double-posted messages that appear only during broker rebalances or deploys.
- Generating the idempotency key per attempt (`uuid4()` inside the retry loop); duplicates persist despite
  "having idempotency keys", visible as several keys for one logical operation in the provider's logs.
- A dedupe TTL shorter than the dead-letter replay delay; messages replayed from the DLQ a day later are
  processed again because their ids have expired.
- Recording the processed id in a different store (Redis) from the effect (Postgres); a crash between the
  two writes either loses the effect or lets the duplicate through, and the inconsistency is rare enough
  to be blamed on "flakiness" for months.
- Retrying a timed-out model or tool call without memoisation; each retry produces a different answer and
  the downstream saga compensations no longer match what was actually done.
- Treating delta updates as idempotent; `credits -= 1` applied twice is wrong, discovered when customer
  balances drift from the ledger.

## When to use
Decide per effect, not per system. For each side effect a handler or agent step produces, classify it:
naturally idempotent (upserts, absolute state), deduplicable in the same transaction (inbox or result
table), idempotent at the receiver (payment and messaging providers that honour keys), or
duplicate-tolerant. Only effects in none of those classes need the heavyweight options: broker
transactions for Kafka-to-Kafka flows, or a reconciliation job that detects and repairs duplicates after
the fact. Measure duplicate rate at each dedupe point, the age distribution of redeliveries against your
window, and the number of unknown-outcome timeouts needing reconciliation. In day-to-day work this is the
`idempotency_key` parameter on every tool adapter and the `step_results` table in the agent runtime. In
interviews, the strong answer explains why delivery cannot be exactly-once, defines effectively-once via
atomic dedupe plus effect, and names the boundary where the guarantee ends.

## Role lens
- **Engineer:** Derives stable idempotency keys from run and step ids, memoises model outputs per step,
  and keeps dedupe records in the same transaction as the effect.
- **Architect:** Classifies every external effect by its idempotency class, sets dedupe-window policy,
  and decides where broker transactions are worth their throughput cost.
- **FDE:** Audits a customer's webhook and integration handlers for the "exactly-once" assumption, and
  negotiates idempotency-key support with their downstream systems before go-live.

## Check yourself
**Q:** A product manager asks you to guarantee that an agent's "post summary to the customer's Slack
channel" step happens exactly once per run, given that workers can be preempted at any moment. What can
you promise, and how would you build it?
**A:** Promise exactly-once *effect* within a stated window, not exactly-once delivery. Build it in
phases keyed by `(run_id, step_id)`: first generate the summary and persist it in a `step_results` row,
so a preempted worker on retry reuses the same text instead of calling the model again; then call Slack
with that key as the idempotency token if their API honours one, and if it does not, record a "sent"
marker only after the call returns and accept that a preemption between the post and the marker can
produce a duplicate. Reduce that window by making the marker write the very next operation, and add a
reconciliation that searches the channel for the summary's correlation id before any retry. State the
residual risk explicitly: with a provider that lacks idempotency keys, the duplicate probability equals
the crash probability inside a window of a few milliseconds, which is tiny but not zero.

## Quiz
### Q: Why can no protocol over a lossy network guarantee exactly-once delivery?
- [ ] Because TCP limits retransmissions to a fixed count
- [x] Because a sender without an acknowledgement cannot distinguish a lost message from a lost ack, so it must choose between risking loss and risking duplication
- [ ] Because clocks on the two sides are never synchronised
- [ ] Because brokers do not persist messages to disk by default
> This is the two generals argument: certainty about the other side's state would require a final
> confirmation that itself could be lost. Retries resolve toward at-least-once; acking first resolves toward
> at-most-once. Clock sync and persistence are separate concerns.

### Q: A Kafka Streams job with transactions enabled reads orders, calls a payment API, and writes receipts to another topic. What is exactly-once?
- [ ] The payment API call, because the transaction wraps the whole handler
- [x] The offset commit and the receipt records, but not the payment API call
- [ ] Nothing, because transactions only work with a single topic
- [ ] Everything, provided consumers use read_committed
> Kafka transactions make input offsets and output records atomic inside Kafka. The payment call is an
> external effect outside the transaction, so it is at-least-once and needs its own idempotency key at the
> provider. read_committed affects what consumers see, not external calls.

### Q: Which change makes a retried agent step produce the same downstream behaviour as the first attempt?
- [ ] Lower the model temperature to zero
- [ ] Add a longer timeout before retrying
- [x] Persist the step's output under a stable (run_id, step_id) key and return it on replay
- [ ] Generate a new idempotency key for each retry so the provider accepts it
> Memoising the first successful output under a stable key is what makes a non-deterministic step
> idempotent; the replay reads the stored result instead of recomputing. Temperature zero is not guaranteed
> deterministic across runs, and per-retry keys defeat deduplication entirely.
