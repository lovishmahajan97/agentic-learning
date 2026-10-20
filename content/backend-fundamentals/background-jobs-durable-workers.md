---
id: background-jobs-durable-workers
module: backend-fundamentals
name: Background jobs / durable workers
kind: pattern
difficulty: core
est_minutes: 17
status: reviewed
related:
  - backend-fundamentals/message-queues
  - backend-fundamentals/redis
  - backend-fundamentals/postgresql-transactions-and-isolation-levels
  - fastapi-for-ai/job-queues-and-durable-workers
  - node-ts-for-ai/bullmq-and-durable-jobs
  - system-design/outbox-inbox-patterns
  - reliability/durable-workflow-execution
  - agent-reliability/durable-execution-and-checkpointing
sources:
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Official docs: PostgreSQL (SELECT ... FOR UPDATE SKIP LOCKED)
  - Official docs: Celery
  - Official docs: Temporal
---

## TL;DR
A background job is work a request handler hands off so it can respond now. A *durable* worker is one
where the job's existence, state and progress live in a store that outlives both the process that
enqueued it and the process running it, so a deploy or crash never loses the work. Durability is bought
with a lease-and-acknowledge protocol, which means a job can run twice, so every job body must be
idempotent.

## Why it exists
The forcing problem is any request whose real work takes longer than a client will wait: embedding a
300-page PDF, running a twenty-step agent, sending ten thousand notifications, re-indexing a tenant.
Doing it inline fails three ways at once. Load balancers and browsers cut idle connections (commonly
around a minute by default; check your own), so the client sees an error while the server keeps working.
The web process's capacity is consumed by slow work, so fast requests queue behind it. And a failure at
minute nine leaves partial side effects with no record of how far it got.

The first naive fix is "fire and forget": start a thread, an `asyncio` task or a framework background
task and return. This fails because the job now exists only in one process's memory. The next deploy,
out-of-memory kill or autoscaler scale-in silently destroys every in-flight and waiting job, and nobody
is told. There is no retry, no visibility, no backpressure (a burst of uploads becomes a burst of
threads) and no way to scale the workers separately from the web tier. Teams discover this the day a
customer asks why their document never got indexed.

A durable worker fixes the root cause by making the job a *record* rather than a *call*. Once the record
is committed, three invariants hold: the job is not lost; at most one worker runs it at a time; and if
that worker dies, the job becomes runnable again. That is the same at-least-once contract a message
queue gives, seen from the worker's side and extended with state the worker owns: attempts, next run
time, progress, result. In AI systems nearly every expensive operation is such a job, and "durable" is
what lets you ship five deploys a day without aborting the agent runs in progress.

## How it works
### The job is a row
A job record carries an id, a type (which handler runs it), a payload (arguments, small; large inputs go
to object storage by reference), a state (`queued`, `running`, `succeeded`, `failed`, `dead`), an attempt
counter, a `run_at` time, a lease (`locked_by`, `lease_until`), and an error or result. Each field exists
for a reason: `run_at` gives delayed and scheduled jobs for free; `attempts` makes retry policy a function
of the row instead of worker memory; the lease is what makes crash recovery possible; and the row itself
is the audit trail and the status endpoint for the caller.

### Enqueue is a write, not a call
Because the job is a row, enqueueing is an insert. If the job table lives in the same database as the
business data, insert it in the *same transaction* as the change that caused it: the job exists if and
only if the business change committed. If the queue is a separate system (Redis, SQS, a broker) you
have a dual-write problem: "commit, then enqueue" loses the job if the process dies between the two;
"enqueue, then commit" runs a job for data that may have been rolled back. The transactional outbox
pattern is the general cure: write the job to a table in the business transaction and let a relay
forward it to the external queue.

### Claim with a lease, not a lock
A worker claims a job by atomically flipping `queued` to `running` and setting `lease_until = now + T`
and `locked_by = me`. In PostgreSQL the idiom is a single `UPDATE ... WHERE id = (SELECT ... FOR UPDATE
SKIP LOCKED LIMIT 1) RETURNING *`. `SKIP LOCKED` exists because without it N workers block on the same
hot row and serialize; with it each worker skips rows another worker is examining and takes the next.
The claim is a *lease* rather than a transaction-long row lock because a lock held by a dead process is
held until the connection drops, and holding a transaction open for a ten-minute job pins a database
connection. A lease simply expires: the claim query also matches `running` rows whose `lease_until` is
in the past, which is exactly how an orphaned job from a crashed worker gets picked up. SQS calls the
same thing a visibility timeout; Redis-based libraries call it a lock with a duration the worker renews.

### Heartbeats and the long-job dilemma
If the lease is shorter than the job, a healthy worker loses its job to a colleague mid-run, and both
run it. If the lease is very long, recovery from a real crash waits that long. The usual answer is a
short lease plus a heartbeat that extends it while work is in progress, paired with a hard maximum
duration so a worker that is alive but stuck cannot hold a job forever.

### Finish, retry, dead-letter
On success the worker sets `succeeded` and stores the result. On failure it classifies the error.
Retryable errors (timeouts, 429s, 5xxs from a model provider) set `queued` again with `run_at = now +
backoff(attempts)`, exponential with jitter because a dependency recovering from an outage would
otherwise be hit by every retry at the same instant. Permanent errors (validation, 4xx, a missing
document) go straight to `dead`: burning five retries on them only delays the moment a human sees the
problem. After `max_attempts` the job is `dead` and stays inspectable, which is the dead-letter queue in
table form. Nothing is ever deleted on failure.

### Idempotency is the price of durability
The worker can finish the side effect, then die before marking the row done. The lease expires, the job
runs again. There is no way to make "call the external API" and "update the row" atomic, so the handler
must tolerate a second run: upsert keyed by job id, pass an idempotency key to payment and email
providers, check the store before acting. For multi-step jobs, checkpoint progress (a step index, or a
per-chunk `done` marker) so a retry *resumes* instead of restarting; a 40-minute job that fails at minute
39 should cost one more minute, not forty. Taken to its conclusion, recording every step and replaying
on failure is what durable execution engines such as Temporal do, and what agent runtimes need for long
tool-calling loops; the lessons on durable workflow execution and checkpointing go deeper.

### Where the store lives
A PostgreSQL table with `SKIP LOCKED` needs no new infrastructure, gives transactional enqueue, and is
trivially inspectable; it polls, so idle workers cost queries, and finished rows must be archived or the
table bloats. Redis-backed libraries (Celery with Redis, RQ, BullMQ, Sidekiq) give blocking pops and low
latency, but inherit Redis's weaker durability and the dual-write problem. Broker-backed workers (Celery
with RabbitMQ, SQS consumers) are the message-queue lesson from the consumer's chair. Choose by what
losing a job costs and by throughput; a few hundred jobs per second (illustrative) is well within a
Postgres table on modest hardware.

### Sizing, fairness and shutdown
Worker count follows from Little's law: jobs in progress = arrival rate × average duration. If uploads
arrive at 0.5 per second and an embedding job averages 4 minutes, 0.5 × 240 = 120 jobs are running at
steady state, so fewer than 120 worker slots means the backlog grows without bound, however fast each
worker is. Size for the peak arrival rate you must absorb within your latency target, and remember that
the model provider's rate limit, not your worker count, may be the real ceiling.

A single shared queue in run order is unfair: one tenant that enqueues 10,000 jobs puts every other
tenant's job behind them. The usual fixes are a per-tenant concurrency cap enforced in the claim (skip
tenants already at their limit), a claim that rotates across tenants rather than taking the globally
oldest row, or separate queues per priority class so interactive work is never behind bulk backfills.

Deploys are the most frequent "crash". A worker that receives SIGTERM should stop claiming, finish or
checkpoint the current job within the platform's grace period, and exit; anything it cannot finish is
recovered by lease expiry, which is why the lease, not the shutdown handler, is the correctness mechanism.

### What to measure
Queue depth, age of the oldest queued job, run duration per type, attempts histogram, and dead count.
The first two detect a stalled worker fleet; the last two detect a failing dependency.

## Example
A PostgreSQL-backed worker in Python using psycopg 3. The claim reclaims expired leases, the handler is
required to be idempotent, retryable failures are rescheduled with jittered backoff, and permanent
failures go to `dead` without burning attempts. Every finishing update is guarded by `locked_by`, so a
worker whose lease expired and was taken over cannot overwrite the new holder's outcome. Heartbeating is
omitted for brevity; for jobs longer than the five-minute lease, extend `lease_until` from a timer thread.

```python
import random, time, uuid, psycopg

WORKER = str(uuid.uuid4())
CLAIM = """
UPDATE jobs SET state='running', locked_by=%s, attempts=attempts+1,
                lease_until=now() + interval '5 minutes'
WHERE id = (SELECT id FROM jobs
            WHERE (state='queued' AND run_at <= now())
               OR (state='running' AND lease_until < now()    -- orphaned by a dead worker
                   AND attempts < 5)                          -- crash-loopers left for a sweeper
            ORDER BY run_at LIMIT 1 FOR UPDATE SKIP LOCKED)
RETURNING id, type, payload, attempts"""
DONE = " WHERE id=%s AND locked_by=%s"     # fence: only the current lease holder may finish

def backoff(attempt):                      # 2, 4, 8 ... s, capped, with jitter
    return min(2 ** attempt, 300) * (0.5 + random.random())

def embed_document(job_id, payload):       # idempotent: keyed writes, safe to re-run
    for chunk in load_chunks(payload["document_id"]):
        if not vectors.exists(chunk.id):   # resume: skip chunks a previous attempt finished
            vectors.upsert(chunk.id, embed(chunk.text))

HANDLERS = {"embed_document": embed_document}

def run_one(conn):
    with conn.transaction():
        row = conn.execute(CLAIM, (WORKER,)).fetchone()
    if row is None:
        return False
    job_id, kind, payload, attempts = row
    try:
        HANDLERS[kind](job_id, payload)
        conn.execute("UPDATE jobs SET state='succeeded', finished_at=now()" + DONE, (job_id, WORKER))
    except RetryableError as e:
        if attempts >= 5:
            conn.execute("UPDATE jobs SET state='dead', error=%s" + DONE, (str(e), job_id, WORKER))
        else:
            conn.execute("UPDATE jobs SET state='queued', error=%s, "
                         "run_at=now() + %s * interval '1 second'" + DONE,
                         (str(e), backoff(attempts), job_id, WORKER))
    except Exception as e:                 # permanent: straight to dead, keep it inspectable
        conn.execute("UPDATE jobs SET state='dead', error=%s" + DONE, (str(e), job_id, WORKER))
    conn.commit()
    return True

with psycopg.connect(DSN) as conn:
    while True:
        if not run_one(conn):
            time.sleep(1)                  # idle poll; LISTEN/NOTIFY can wake workers instead
```

`load_chunks`, `embed`, `vectors`, `RetryableError` and `DSN` belong to the application. A partial index
on `(run_at) WHERE state IN ('queued','running')` keeps the claim fast as the table grows. The
`attempts < 5` guard on orphan reclaim matters because a job that kills its worker (an out-of-memory
crash on one huge document) never reaches the `except` branches; without the guard it is reclaimed
forever, and a small periodic sweeper should move such rows to `dead`.

## Trade-offs
- A job table in the main database gains transactional enqueue and zero new infrastructure, but pays
  polling and row churn; it flips toward Redis or a broker when throughput reaches (as an order of
  magnitude) thousands of jobs per second, or when hundreds of idle workers poll.
- Redis-backed queues gain blocking pops and millisecond pickup, but lose jobs if persistence is off or
  lagging; acceptable for cache warming, unacceptable for billing or customer-visible work.
- A long lease simplifies the worker, but delays recovery after a crash; a short lease with heartbeats
  recovers fast but needs a maximum-duration guard so a stuck worker cannot renew forever.
- Retrying every failure is easy to write, but hides permanent errors behind five slow attempts;
  classifying errors costs code and flips to worthwhile the moment a model provider returns 4xx.
- Restarting a failed multi-step job is simple, but repeats side effects and wasted minutes;
  checkpointing adds state to manage and pays off once jobs exceed a few minutes or several steps.
- A durable execution engine gains resume-from-step and full history, but costs a runtime to operate and
  a programming model to learn; a table and a loop are enough until workflows become long and branching.

## Pitfalls
- Using in-process background tasks for work that matters; the symptom is jobs that vanish after every
  deploy, discovered weeks later by a customer rather than by an alert.
- A non-idempotent handler; the symptom is duplicate emails, double charges or two embeddings per chunk
  after a worker is OOM-killed or a lease expires.
- Enqueueing outside the business transaction; the symptom is jobs that fail on "row not found" because
  the insert was rolled back, or committed changes with no job because the process died first.
- A lease shorter than the job and no heartbeat; the symptom is the same job id running on two workers
  and `attempts` climbing with no recorded failure.
- No maximum attempts or dead state; the symptom is one poison job retried forever, a worker permanently
  busy and a backlog growing behind it.
- One shared run-order queue for all tenants; the symptom is a bulk import by one customer turning every
  other customer's two-second job into a two-hour wait, with workers fully busy and no errors.
- Storing large payloads in the job row; the symptom is a bloated table, slow claims and a database that
  has become a blob store.
- Never archiving finished rows and no partial index; the symptom is a claim query that gets slower every
  week even though the backlog is small.
- Assuming the library acknowledges after the work; some default to acknowledging before execution
  (Celery's `acks_late` is off by default), and the symptom is lost jobs on worker crash despite a
  "durable" broker.

## When to use
Reach for a durable worker whenever work must survive the request: anything longer than a few seconds,
anything with an external side effect that must happen exactly once from the user's view, anything that
needs retry, scheduling or a status the caller can poll. Stay inline for fast reads the user is waiting
on, and use in-process tasks only for best-effort work you can afford to lose, such as a metrics sample.
Start with a table in the database you already run and move to a broker or an execution engine when
throughput, fan-out or workflow length demands it. Measure oldest-job age and dead count from day one.
Day to day this shows up as "should this endpoint enqueue or call", choosing lease length against real
LLM latency, and deciding what is retryable. In interviews, expect to design the job table, explain
`SKIP LOCKED` and leases, and justify idempotency.

## Role lens
- **Engineer:** Writes the claim query, idempotent handlers and error classification, sizes leases to
  model latency, and adds the partial index and archival job before the table grows.
- **Architect:** Decides where job state lives (database, Redis, broker, execution engine), the retry and
  dead-letter policy, and how callers learn the status of asynchronous work.
- **FDE:** Fits the pipeline to the customer's deploy cadence and infrastructure, proves that a mid-run
  deploy loses nothing, and explains duplicate-safe design when their downstream systems are not idempotent.

## Check yourself
**Q:** Your API accepts document uploads that take two to twenty minutes to chunk and embed, and the
team deploys five times a day. Design the job pipeline so a deploy in the middle of an embedding never
loses or duplicates work, and explain each mechanism's role.
**A:** Make the job a row inserted in the same transaction as the upload record, so a job exists exactly
when a document does. Workers claim with an atomic update using `FOR UPDATE SKIP LOCKED` that sets a
lease; on deploy, workers receive SIGTERM, stop claiming and either finish or let the lease expire, after
which a new worker reclaims the row, so nothing is lost. Guard every completion update with the lease
holder's id so a pre-empted worker cannot overwrite the new holder's result. Duplication is handled inside the handler: each
chunk's vector is upserted by chunk id and skipped if present, so a re-run after an expired lease resumes
rather than repeats, and the twenty-minute job costs only the unfinished chunks. Heartbeat the lease
while embedding so a slow but healthy worker is not pre-empted, with a maximum duration as a guard.
Classify provider errors: 429 and 5xx reschedule with jittered backoff, 4xx go to `dead`. Alert on the age
of the oldest queued job and the dead count.

## Quiz
### Q: Why does a durable worker claim a job with an expiring lease instead of holding a row lock for the duration of the job?
- [ ] Row locks are not supported for UPDATE statements in most databases
- [x] A lease expires if the worker dies, letting another worker reclaim the job, whereas a lock would require keeping a transaction and connection open for the whole job
- [ ] Leases allow several workers to run the same job in parallel for speed
- [ ] Locks cannot be combined with SKIP LOCKED
> Crash recovery depends on the claim timing out. A transaction-long lock either pins a database
> connection for minutes or, if the worker dies, disappears without any record of what was in progress.

### Q: A worker finishes sending an email through a provider, then crashes before marking the job succeeded. What happens next in a correctly built durable system?
- [ ] The job is marked failed because the worker's connection dropped
- [ ] The broker detects the completed side effect and marks the job done
- [x] The lease expires, another worker re-runs the job, and the handler's idempotency key stops the provider from sending a second email
- [ ] The job is lost, which is why email should be sent inline
> Side effect and acknowledgement cannot be made atomic, so re-running is the only safe default. The
> handler must therefore be idempotent, for example by passing a key the provider deduplicates on.

### Q: Which enqueue strategy guarantees that a job exists if and only if the business change was committed?
- [ ] Push to Redis first, then commit the database transaction
- [ ] Commit the transaction, then push to the queue inside a try/except
- [x] Insert the job row in the same database transaction as the business change, or write it to an outbox table relayed to the queue
- [ ] Enqueue from a background thread after the response is sent
> Only a write in the same transaction is atomic with the business change. Any order of two separate
> writes can be interrupted between them, producing either an orphan job or a change with no job.
