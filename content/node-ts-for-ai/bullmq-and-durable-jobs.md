---
id: bullmq-and-durable-jobs
module: node-ts-for-ai
name: BullMQ & durable jobs
kind: tool
difficulty: core
est_minutes: 16
status: reviewed
related:
  - node-ts-for-ai/redis
  - node-ts-for-ai/graceful-shutdown
  - backend-fundamentals/background-jobs-durable-workers
  - fastapi-for-ai/job-queues-and-durable-workers
  - agent-reliability/durable-execution-and-checkpointing
  - agent-reliability/idempotent-tool-execution
  - reliability/durable-workflow-execution
  - system-design/outbox-inbox-patterns
sources:
  - Official docs: BullMQ
  - Official docs: Redis
---

## TL;DR
BullMQ is a Redis-backed job queue for Node: producers add jobs, workers process them with concurrency,
retries with backoff, rate limits, delays, priorities and parent/child flows, and job state survives
process restarts because it lives in Redis, not in a promise. An HTTP request is the wrong unit of work
for a minutes-long agent run or a 10,000-document embedding batch. Delivery is at-least-once, so
handlers must be idempotent, and the queue is only as durable as the Redis behind it.

## Why it exists
An agent run takes two minutes, calls the model eight times and calls three tools. Doing that inside an
HTTP handler means the client holds a connection for two minutes, a load balancer idle timeout (60 s is a
common default) can kill it first, a deploy that restarts the process loses the run with no record, and a burst of 200
runs starts 200 concurrent model calls that hit the provider's rate limit together. The same shape
appears for batch embedding, nightly evals, document ingestion, webhook fan-out and scheduled
re-indexing.

The in-process alternative, `setImmediate(() => runAgent(...))` with a `Map` of in-flight runs, fails
on every axis: it dies with the process, cannot be shared across replicas, has no retry policy and no
visibility. Postgres-as-queue (`SELECT ... FOR UPDATE SKIP LOCKED`) is a legitimate design and keeps
everything in one transactional store, but you then write the polling, locking, retry, backoff, delay
and dead-letter machinery yourself.

BullMQ packages that machinery on top of Redis. Redis fits *because* the queue's hot path (claim a job,
renew its lock, move it between states) is a handful of atomic operations on lists, sorted sets and
hashes, which Redis executes in microseconds and which BullMQ implements as Lua scripts so a claim can
never be half-done. The trade is that the queue inherits Redis's durability settings: snapshot-only
persistence or an LRU eviction policy means lost jobs.

## How it works
### Objects
- **Queue**: the producer handle. `queue.add(name, data, opts)` writes a job hash and pushes its id onto
  the waiting list (or a delayed or prioritised sorted set).
- **Worker**: the consumer. It takes a processor `(job) => Promise<result>` and a `concurrency`; each
  slot atomically moves a job from waiting to active and runs the processor. Many Worker processes on
  many replicas share one queue, which is how you scale.
- **Job**: `id`, `name`, `data` (JSON), `opts`, `attemptsMade`, `progress`, `returnvalue` and
  timestamps. A custom `jobId` deduplicates: adding a job whose id already exists in the queue is a
  no-op while the earlier job is still stored, which is the simplest exactly-once-enqueue tool you have.
  Custom ids must not be plain integers or contain `:` (BullMQ uses it as its key separator and throws
  on `add`), so use `embed-<docId>`, not `embed:<docId>`. The dedup window is exactly the job's
  lifetime in Redis: once `removeOnComplete` deletes the job, the same id can be added again, and while
  a completed job is still retained, a deliberate re-run with that id is silently ignored. BullMQ 5
  also offers a `deduplication` option with its own id and TTL when you want that window explicit.
- **QueueEvents**: a Redis-streams-based listener for `completed`, `failed` and `progress`, so an API
  replica can stream progress to a client without being the worker.
- **FlowProducer**: adds a parent job with children; the parent runs only after all children complete,
  which models "embed 500 chunks, then write the document summary".

### Job lifecycle
A job is always in exactly one state, and each state is a Redis structure: `waiting` (a list),
`prioritized` and `delayed` (sorted sets ordered by priority or due time), `active` (a list plus a
per-job lock key), `waiting-children` (a parent blocked on its flow), and the terminal `completed` and
`failed` sets. Every transition (claim, complete, fail and reschedule, promote a delayed job) is one Lua
script *because* it touches several keys at once, and a crash between "remove from waiting" and "add to
active" would otherwise lose or duplicate the job. Reading the queue's health is therefore reading the
sizes of these sets: a growing `waiting` means too few workers or a limiter that is too tight; a growing
`failed` means a bug or a dependency that is down; a non-empty `active` with nobody working means stalls.

### Retries, backoff, delays
`opts.attempts` with `opts.backoff: { type: 'exponential', delay: 1000 }` re-schedules a failed job
after 1 s, 2 s, 4 s and so on until `attempts` is exhausted, then leaves it in the failed set for
inspection or manual retry. Delayed jobs sit in a sorted set keyed by timestamp and are promoted when
due *because* polling a list for "is it time yet" would waste Redis round trips. `priority` orders
waiting jobs; `removeOnComplete` and `removeOnFail` with a count or an age keep Redis from filling with
history. Recurring work (nightly evals, hourly re-index) uses a job scheduler
(`queue.upsertJobScheduler(id, { pattern: cron } or { every: ms }, template)` in recent BullMQ 5
releases, which supersedes the older repeatable-job API): the scheduler keeps one upcoming delayed job
per schedule, so running several producer replicas does not multiply the runs.

### Locks and stalled jobs
When a worker claims a job it sets a lock with `lockDuration` and renews it on an interval while the
processor runs. If the process dies, or the event loop is blocked long enough that renewal stops, the
lock expires, a stall checker notices an active job with no lock and moves it back to waiting, up to
`maxStalledCount` times before marking it failed. This is the mechanism that gives durability across
crashes, and it is also why a CPU-bound processor that blocks the event loop can get a job processed
twice: the renewal timer never fires. BullMQ's sandboxed processors (pass a file path instead of a
function; the job runs in a child process or a worker thread) exist *because* of that failure mode.

### Rate limiting for LLM providers
`Worker` takes `limiter: { max, duration }` to cap jobs started per window across all workers of a
queue. For provider 429s, a processor can call `await queue.rateLimit(ms)` (in BullMQ 5 this replaces
the deprecated `worker.rateLimit`) and throw `Worker.RateLimitError()` so the job returns to the queue
without consuming an attempt while the queue pauses for that window. Per-group limits (one quota per tenant) are a BullMQ Pro feature; the
open-source approach is a queue per tenant tier, or a Redis token bucket checked inside the processor.

### Delivery semantics, and what BullMQ is not
Every path above is at-least-once: a job can run twice after a stall, after a crash between finishing
the work and moving the job to completed, or after a manual retry. Processors must therefore be
idempotent, usually by keying side effects on `job.id` (a Postgres unique constraint, an idempotency key
on a downstream API). BullMQ is also not a workflow engine: it does not checkpoint the middle of a
processor. A ten-step agent run should either be split into one job per step with state in Postgres, or
run under a durable-execution engine (Temporal, Inngest, Restate and similar) when you need replayable
history, long timers and human-in-the-loop waits.

## Example
An embedding pipeline: the API enqueues one job per document with a deterministic id (a double-submit is
harmless), a worker with bounded concurrency and a provider rate limit processes it, and the processor
is idempotent because the vector write keys on document id and chunk index. Each BullMQ object gets its
own ioredis connection from a factory here; BullMQ also duplicates a Worker's connection internally for
its blocking commands, and refuses a Worker connection whose `maxRetriesPerRequest` is not `null`.

```ts
import IORedis from "ioredis";
import { Queue, Worker, QueueEvents } from "bullmq";
const conn = () => new IORedis(process.env.REDIS_URL!, { maxRetriesPerRequest: null });  // required for Workers

export const embedQueue = new Queue("embed", { connection: conn(),
  defaultJobOptions: { attempts: 5, backoff: { type: "exponential", delay: 2_000 },
                       removeOnComplete: { count: 1_000 }, removeOnFail: { age: 7 * 24 * 3600 } } });

export async function enqueueEmbedding(docId: string, tenant: string) {
  await embedQueue.add("embed-doc", { docId, tenant }, { jobId: `embed-${docId}` });   // dedup by id (no ':' allowed)
}

// worker.ts — its own process; several replicas share the queue
const worker = new Worker("embed", async (job) => {
  const { docId } = job.data as { docId: string; tenant: string };
  const chunks = await loadChunks(docId);
  for (let i = 0; i < chunks.length; i++) {
    if (await embeddingExists(docId, i)) continue;                   // resume, do not redo, after a retry
    try {
      const vec = await embed(chunks[i].text);                       // provider call
      await upsertEmbedding(docId, i, vec);                           // idempotent: (docId, i) is unique
    } catch (e: any) {
      if (e.status === 429) { await embedQueue.rateLimit(30_000); throw Worker.RateLimitError(); }
      throw e;                                                       // anything else: retry with backoff
    }
    await job.updateProgress(Math.round(((i + 1) / chunks.length) * 100));
  }
  return { chunks: chunks.length };
}, { connection: conn(), concurrency: 4, limiter: { max: 50, duration: 60_000 } });

worker.on("failed", (job, err) => console.error("embed failed", job?.id, job?.attemptsMade, err.message));

// api.ts — stream progress to a client without being the worker
const events = new QueueEvents("embed", { connection: conn() });
events.on("progress", ({ jobId, data }) => notifyClient(jobId, data));
```

## Trade-offs
- Redis makes claim, renew and move operations microsecond-fast and lets any number of replicas share a queue, but durability equals Redis's: AOF and `noeviction` are mandatory for work you cannot re-enqueue, while a cache-style instance is fine for best-effort jobs.
- Retries with exponential backoff absorb transient provider failures without code, but each retry re-runs the whole processor; a 500-chunk job failing at chunk 499 restarts at chunk 0 unless the processor checkpoints per chunk.
- A worker-level `limiter` keeps a whole fleet under a provider's quota with one setting, but it is per queue, not per tenant or per model; fairness across tenants needs separate queues, BullMQ Pro groups, or an in-processor token bucket.
- At-least-once delivery is simple and robust and forces idempotent processors; exactly-once is not on offer from the queue, and chasing it with locks inside the processor usually costs more than a unique constraint.
- BullMQ is far lighter than a workflow engine and needs only Redis, but it has no durable mid-job state; once runs need long waits, signals or replay, Temporal-style engines earn their operational cost.

## Pitfalls
- A processor that blocks the event loop (large synchronous JSON parse, vector math): the symptom is `stalled` events and jobs processed twice because the lock renewal timer could not fire. Move CPU work to a sandboxed processor or a worker thread.
- Non-idempotent side effects (send email, charge budget, append message) keyed on nothing: the symptom is duplicates after a deploy or a stall, with every attempt logging success.
- An ioredis connection with `maxRetriesPerRequest` left at its default (20): the symptom is a Worker throwing at construction when handed that instance (BullMQ only warns and overrides it when given plain options), or blocking waits erroring out during a Redis reconnect.
- Relying on `jobId` for dedup while also using `removeOnComplete`: the symptom is the "duplicate" document being embedded again a few minutes after the first job finished, because the completed job (and its id) had already been deleted.
- `removeOnComplete` left off in a high-volume queue: the symptom is Redis memory growing until eviction or out-of-memory, weeks after launch.
- Treating `queue.add` as part of a database transaction: the symptom is jobs for rows that rolled back, or committed rows with no job; use an outbox table and a relay worker.
- Exiting with `process.exit()` instead of `await worker.close()`: the symptom is active jobs re-run after every deploy once the stall checker catches them.

## When to use
Use BullMQ when work outlives a request, must survive restarts, needs retries with backoff, or must be
throttled against an external quota: agent runs, ingestion, embedding, evaluation batches, webhook
delivery, scheduled refreshes. Keep synchronous sub-second work in the request. If the team already has
Postgres and low volume, `SKIP LOCKED` polling can be enough; if runs need durable multi-step state,
timers of hours or days, or human approval steps, reach for a durable-execution engine and keep BullMQ
for simple, idempotent units. Measure waiting-queue depth, job age at start, failure rate by attempt,
stalled count and worker concurrency utilisation. Interviewers ask what happens when a worker dies
mid-job and how you keep the job from doing its side effect twice.

## Role lens
- **Engineer:** Designs job payloads and ids, writes idempotent processors with per-chunk checkpoints, configures attempts, backoff, concurrency and limiter, and closes workers gracefully on SIGTERM.
- **Architect:** Decides queue topology (per workload, per tenant tier), the Redis durability policy behind it, where the outbox sits, and when a workflow engine replaces the queue for long-running agent state.
- **FDE:** Deploys workers inside the customer's environment, checks their Redis eviction and persistence settings, and tunes limiter values to the customer's negotiated provider quotas.

## Check yourself
**Q:** A worker embedding 2,000-chunk documents dies during a deploy. On restart, some documents are
embedded twice and one job is marked failed after being "stalled" repeatedly. Explain what happened and
what you change.
**A:** When the process died, its jobs' locks expired, the stall checker moved them back to waiting and
other workers re-ran them from the first chunk; because the upsert was not keyed on (document, chunk
index), the second pass wrote duplicate vectors. The repeatedly stalled job is a processor that blocks
the event loop, probably synchronous parsing or vector math, so lock renewal stops even though the
process is alive; after `maxStalledCount` stalls BullMQ marks it failed. Changes: make the upsert
idempotent with a unique key, check for existing chunks at the start of each iteration so a retry
resumes instead of restarting, move CPU-heavy work to a sandboxed processor, and close workers with
`await worker.close()` on SIGTERM so in-flight jobs finish before the pod is killed.

## Quiz
### Q: What makes a job survive a worker process crash in BullMQ?
- [ ] The processor's promise is serialised to disk by Node
- [x] Job state lives in Redis and the lock expires, so the stall checker moves the active job back to waiting
- [ ] Workers replicate in-flight jobs to each other over pub/sub
- [ ] BullMQ writes a write-ahead log in the worker's filesystem
> Durability comes from Redis holding the job and from the lock-and-stall mechanism: a dead worker stops renewing its lock, and once it expires the job is returned to the queue for another worker, which is why handlers must be idempotent.

### Q: Why does BullMQ require `maxRetriesPerRequest: null` on a worker's ioredis connection?
- [ ] To disable job retries, which BullMQ manages itself
- [ ] Because Redis Cluster does not support per-request retries
- [x] So that commands, including the worker's long blocking waits, queue until Redis reconnects instead of failing after a fixed number of reconnection attempts
- [ ] To allow the connection to be shared with pub/sub subscribers
> ioredis's `maxRetriesPerRequest` fails pending commands after N reconnection attempts (20 by default); a worker's loop is built around long blocking commands that must survive a reconnect rather than reject, so BullMQ insists on `null` (wait indefinitely) for worker connections.

### Q: Which design keeps a 500-chunk embedding job from restarting at chunk 0 after a transient failure at chunk 499?
- [ ] Increase `attempts` so the job eventually gets through
- [ ] Lower `concurrency` so fewer jobs fail at once
- [x] Make each chunk's write idempotent and check for existing chunks so a retry skips work already stored
- [ ] Use `priority: 1` so the job is retried first
> BullMQ retries re-run the whole processor; only application-level checkpoints (idempotent upserts plus an existence check at the start of each chunk) turn a retry into a resume. More attempts just repeat the same 499 calls.
