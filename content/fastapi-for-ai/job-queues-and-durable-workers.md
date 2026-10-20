---
id: job-queues-and-durable-workers
module: fastapi-for-ai
name: Job queues & durable workers (Celery / ARQ + job-status endpoint)
kind: pattern
difficulty: core
est_minutes: 18
related:
  - backend-fundamentals/background-jobs-durable-workers
  - backend-fundamentals/message-queues
  - backend-fundamentals/redis
  - fastapi-for-ai/backgroundtasks
  - fastapi-for-ai/idempotency-keys
  - node-ts-for-ai/bullmq-and-durable-jobs
  - reliability/durable-workflow-execution
  - system-design/synchronous-vs-asynchronous-execution
sources:
  - Official docs: Celery
  - Official docs: ARQ
  - Kleppmann, 2017 — Designing Data-Intensive Applications
---

## TL;DR
A job queue moves slow or must-complete work (batch summarisation, embedding a corpus, a long agent
run) out of the request into worker processes, after recording the job's existence somewhere durable,
so the API can answer `202 Accepted` with a job id and the client learns the outcome from a job-status
endpoint. Celery is the mature, sync-first, multi-broker option; ARQ is a small asyncio-native queue on
Redis. Both deliver at-least-once, so every job body must be idempotent.

## Why it exists
Three constraints kill long HTTP requests. Intermediaries cut connections that run or idle too long,
commonly after tens of seconds by default (vendor-dependent), so a five-minute corpus summarisation
never returns. Clients retry on timeout, so the work runs twice and the second run races the first.
And a deploy, crash or scale-down kills in-flight work with nothing to say it existed. The AI shape of
the problem is worse than classic web work because a single job can mean thousands of model calls at
real cost, bounded by provider rate limits rather than by your CPUs.

`BackgroundTasks` does not help: it runs after the response in the same process and vanishes with it.
Holding the connection open with huge timeouts hides the symptom and still loses work on restart. The
backend-fundamentals lesson on durable workers covers the theory, leases, acknowledgements and
visibility timeouts; this lesson is the FastAPI-facing shape of it. The idea is to split "accept" from
"do": the API validates, persists a job record, enqueues a small message, and hands back a handle; a
separate worker fleet claims jobs, does the work with periodic progress writes, and records the result;
the client polls or subscribes for the outcome. Each half scales and fails independently, and the job
record, not the HTTP connection, is the source of truth.

## How it works
### The contract between API and client
1. `POST /jobs`: validate input fully, because this is the last moment you can return a 4xx. Insert a
   job row in your own database (`id`, `owner`, `status=queued`, `created_at`, the idempotency key if
   the client sent one). Enqueue a message carrying the job id and small parameters, never the payload
   itself; a 50 MB document goes to object storage and the message carries its key. Return `202` with
   `{"job_id": ..., "status_url": ...}` and a `Location` header.
2. `GET /jobs/{id}`: check the caller owns the job (answer 404, not 403, so ids cannot be probed).
   Return `status` (`queued`, `running`, `succeeded`, `failed`, `cancelled`), `progress`, `result` or a
   `result_url`, a sanitised `error`, and timestamps. Add `Retry-After` so well-behaved clients back
   off.
3. The worker claims a job, writes `running` with its worker id and start time, performs the work with
   progress updates, writes the result and a terminal status. Terminal states are immutable.
4. Optionally push: an SSE endpoint that tails the job row, or a webhook on completion.

Why your own row and not the broker's result backend: result backends are ephemeral (TTL-bound), carry
no ownership or audit metadata, and couple your API to the queue library. The row also absorbs a
classic race: write the row and commit *before* enqueueing; if the enqueue fails, a sweeper re-enqueues
rows stuck in `queued`. Enqueueing first can produce a worker that finds no row. The outbox pattern
makes this airtight when you need it.

### Celery
Tasks are plain functions decorated with `@app.task`; `task.delay(...)` or `apply_async(args,
countdown=..., queue=...)` enqueues to RabbitMQ, Redis or SQS. The default worker pool is prefork, one
OS process per concurrency slot, which suits CPU-bound and sync code. Reliability is opt-in: by
default a message is acknowledged when received, so a worker crash loses it; set `acks_late=True` and
`task_reject_on_worker_lost=True` for at-least-once. With the Redis broker an unacknowledged message is
redelivered after `visibility_timeout`, which defaults to one hour, so any job longer than that runs
twice unless you raise it. Retries come from `autoretry_for`, `retry_backoff`, `max_retries` and
`self.retry()`; `soft_time_limit` and `time_limit` bound runaway tasks. Celery has no native asyncio
task body: calling an async SDK means `asyncio.run(...)` inside the task or using the sync client.
Beat schedules periodic jobs, Flower monitors, and the canvas primitives (chains, groups, chords) express
fan-out such as "embed every chunk, then build the index".

### ARQ
Tasks are `async def` functions taking a `ctx` dict first. A `WorkerSettings` class lists `functions`,
`redis_settings`, `max_jobs` (concurrent jobs per worker process), `job_timeout`, `max_tries`,
`keep_result` and `on_startup`/`on_shutdown` hooks where you create shared clients (an `httpx` client,
a database pool) into `ctx`. Enqueue with `await pool.enqueue_job("name", *args, _job_id=...)`; a job
id that already exists is not enqueued again, which gives you deduplication for free. Retries are
requested by raising `arq.Retry(defer=seconds)`. One worker process holds `max_jobs` concurrent
coroutines, so a single process can keep many LLM calls in flight, which is exactly the shape of
IO-bound AI work. It is Redis-only and small, with no canvas, routing or RabbitMQ.

### Choosing
Pick Celery when the organisation already runs it, when you need RabbitMQ, multiple queues with
priorities, Beat schedules or canvas workflows, or when jobs are CPU-bound. Pick ARQ when the codebase
is asyncio, Redis is already present, jobs are IO-bound model calls, and a small operational surface
matters. Either way the body must be idempotent because delivery is at-least-once: check the job's
status before acting, write results with upserts keyed by job id, and pass idempotency keys to
external calls. For multi-step agent runs that need checkpoints and resumption, a workflow engine
is the next step up; see the durable workflow lesson.

### Backpressure and capacity
Queue depth is your backpressure signal: export it, cap in-flight jobs per tenant, and reject new jobs
with `429` or `503` when depth times average duration exceeds what you promised. Use separate queues
for cheap interactive jobs and expensive batch jobs so one never starves the other. Size worker
concurrency by provider rate limits and memory, not CPU.

## Example
The FastAPI side of the contract with an ARQ worker. The job row is committed before enqueueing, the
status endpoint hides other users' jobs, and the worker checks status first so a redelivered job is a
no-op. `db` stands for your async data-access layer.

```python
import uuid
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

app = FastAPI()                       # lifespan creates app.state.arq = await create_pool(RedisSettings())

class Req(BaseModel):
    user_id: str
    corpus_id: str

@app.post("/jobs/summaries", status_code=202)
async def create(body: Req, request: Request, response: Response):
    job_id = str(uuid.uuid4())
    await db.create_job(job_id, owner=body.user_id, status="queued")        # commit BEFORE enqueue
    await request.app.state.arq.enqueue_job("summarize_corpus", job_id, body.corpus_id, _job_id=job_id)
    response.headers["Location"] = f"/jobs/{job_id}"
    return {"job_id": job_id, "status_url": f"/jobs/{job_id}"}

@app.get("/jobs/{job_id}")
async def status(job_id: str, user_id: str, response: Response):
    job = await db.get_job(job_id)
    if job is None or job.owner != user_id:
        raise HTTPException(404)                                            # 404, not 403: don't leak ids
    if job.status in ("queued", "running"):
        response.headers["Retry-After"] = "5"
    return {"status": job.status, "progress": job.progress, "result_url": job.result_url, "error": job.error}

# worker.py  — run with:  arq worker.WorkerSettings
from arq.connections import RedisSettings

async def summarize_corpus(ctx, job_id: str, corpus_id: str):
    if await db.job_status(job_id) == "succeeded":                          # at-least-once: redelivery is a no-op
        return
    await db.set_status(job_id, "running")
    try:
        docs = await db.docs(corpus_id)
        for i, doc in enumerate(docs, 1):
            await db.upsert_summary(doc.id, await ctx["llm"].summarize(doc))   # idempotent write per doc
            await db.set_progress(job_id, i, len(docs))
        await db.set_status(job_id, "succeeded", result_url=f"/corpora/{corpus_id}/summaries")
    except Exception as exc:
        await db.set_status(job_id, "failed", error=type(exc).__name__); raise   # raise so ARQ counts the try

class WorkerSettings:
    functions = [summarize_corpus]
    redis_settings = RedisSettings()
    max_jobs, job_timeout, max_tries = 10, 1800, 3   # concurrency bound by LLM rate limits, not CPU
```

## Trade-offs
- Durability and horizontal scaling of workers, but a second deployable, a broker to operate, and a
  serialisation boundary; for follow-ups measured in milliseconds that can be lost, `BackgroundTasks`
  is cheaper.
- At-least-once delivery means no job is lost, but every job can run twice, so idempotency is a
  requirement rather than a nicety; the flip is at-most-once (Celery's default early ack), which is
  only acceptable for work you would rather drop than repeat.
- Polling a status endpoint is simple and stateless, but at scale it becomes your highest-QPS route;
  once clients number in the thousands, push via SSE or webhooks pays for itself.
- Celery's breadth (brokers, routing, Beat, canvas) buys flexibility at the price of sync task bodies
  and more configuration; ARQ's asyncio model fits LLM IO perfectly but stops at Redis and a single
  queue abstraction.

## Pitfalls
- Enqueueing before the job row is committed; the symptom is workers logging "job not found" and
  retrying until the row appears, or never.
- Stuffing documents into the message; the symptom is Redis memory spikes and slow enqueues; pass
  object-storage keys instead.
- Keeping Celery's default early acknowledgement; the symptom is jobs stuck in `running` forever after
  a worker is OOM-killed.
- A Redis `visibility_timeout` shorter than the longest job; the symptom is the same job id appearing
  twice in worker logs and doubled model spend.
- Serving status from the broker's result backend; the symptom is `404` for jobs older than the
  result TTL, typically an hour.
- No ownership check on the status route; the symptom is a security finding that job ids, often
  sequential, expose other tenants' results.
- Clients polling in tight loops; the symptom is the status endpoint dominating request volume; send
  `Retry-After` and document a backoff.
- Letting a retried job overwrite a terminal state; the symptom is a job flipping from `succeeded`
  back to `running` and confusing every dashboard.

## When to use
Use a queue when work outlives a reasonable HTTP timeout, must survive restarts, needs retries, or
should be throttled against provider rate limits independently of API traffic: corpus ingestion and
embedding, batch evaluation runs, scheduled re-summarisation, long agent tasks. Do not use it for
interactive single-turn chat, where streaming is the right answer, or for sub-second follow-ups.
Measure queue depth, time-in-queue, job duration percentiles, retry and duplicate rates, and status
polls per job. In interviews, be ready to draw the 202-plus-status contract, explain why the job row
beats the result backend, and say what at-least-once forces on job bodies.

## Role lens
- **Engineer:** Writes idempotent task bodies, the enqueue-after-commit path, and the status route
  with ownership checks and `Retry-After`, and wires shared clients into worker startup hooks.
- **Architect:** Chooses Celery versus ARQ versus a workflow engine, defines queue separation, per-tenant
  limits and backpressure policy, and decides where job state lives.
- **FDE:** Migrates a customer's "run it in a background task" prototype to a queue when deploys start
  losing jobs, and tunes worker concurrency against their provider rate limits.

## Check yourself
**Q:** A worker crashes halfway through summarising 1,000 documents. Walk through what happens with
Celery at default settings, with `acks_late=True`, and what your job code must do in each case.
**A:** At default settings Celery acknowledges the message on receipt, so after the crash the broker
considers it done: the job row stays `running` forever, 600 summaries are missing, and nobody retries.
A sweeper that marks stale `running` jobs as failed is the minimum mitigation. With `acks_late=True`
(and `task_reject_on_worker_lost`) the message is redelivered after the visibility timeout, so the
job runs again from the start on another worker. For that to be correct, the task must be idempotent:
check the job status first, skip documents that already have a summary (upsert keyed by document id),
and update progress monotonically, so the second run does the remaining 600 and marks success. In both
cases the row, not the broker, is what the status endpoint and the on-call engineer read.

## Quiz
### Q: Why does the API return 202 with a job id instead of holding the connection until the work finishes?
- [ ] Because FastAPI cannot keep a connection open for more than thirty seconds
- [x] Because intermediaries cut long connections, clients retry and duplicate work, and in-flight work dies with the process, whereas a persisted job survives all three
- [ ] Because 202 responses are cached by browsers
- [ ] Because workers cannot write HTTP responses
> The job record plus a status endpoint decouples completion from the connection. The connection can
> drop, the client can retry safely, and the worker can be restarted without losing the job.

### Q: Why must a job function check the job's status before doing its work?
- [ ] To update the job's `started_at` timestamp
- [ ] Because ARQ requires it for the result to be stored
- [x] Because delivery is at-least-once, so the same job can be redelivered after a crash or timeout and must be a no-op if already done
- [ ] To obtain a lock on the Redis key
> Both Celery with late acks and ARQ redeliver jobs whose worker died. Reading the status first and
> using idempotent writes turns a duplicate delivery into a harmless resume rather than double work.

### Q: Which situation most favours ARQ over Celery?
- [ ] The team needs RabbitMQ with priority queues and Beat schedules
- [x] An asyncio codebase on Redis whose jobs are mostly concurrent IO-bound model calls
- [ ] Jobs are CPU-bound image preprocessing that must use every core
- [ ] The organisation already runs Celery for other services
> ARQ's async task bodies let one process keep many model calls in flight. Celery's prefork pool and
> broker breadth are the better fit for CPU-bound work, RabbitMQ features, and existing Celery estates.
