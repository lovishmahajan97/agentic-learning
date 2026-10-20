---
id: backgroundtasks
module: fastapi-for-ai
name: BackgroundTasks
kind: pattern
difficulty: core
est_minutes: 12
related:
  - fastapi-for-ai/job-queues-and-durable-workers
  - fastapi-for-ai/dependency-injection
  - fastapi-for-ai/request-scoped-vs-application-scoped-resources
  - fastapi-for-ai/graceful-shutdown-and-worker-draining
  - fastapi-for-ai/async-endpoints
  - backend-fundamentals/background-jobs-durable-workers
sources:
  - Official docs: FastAPI (Background Tasks)
  - Official docs: Starlette (Background Tasks)
  - Official docs: FastAPI (Dependencies with yield)
---

## TL;DR
`BackgroundTasks` runs functions *after* the response has been sent, in the same process and event
loop, with no persistence, no retry and no status. It exists so a handler can answer quickly while
doing cheap follow-up work (write a trace, bump a usage counter, fire a webhook) without standing up a
queue. Use it for work you can afford to lose when the process dies; anything that must happen, or
that takes more than a few seconds, belongs in a durable job queue.

## Why it exists
Every LLM request drags follow-up work behind it: persist the prompt, response and token usage for
observability and billing, update a rate-limit counter, save an evaluation sample, emit an analytics
event, notify a webhook. Doing them inline adds their latency to the response (a database write plus a
webhook round trip can be a few hundred milliseconds; illustrative) and couples the user's success to
theirs: a slow analytics endpoint should never turn a good answer into a 500.

The naive escapes each fail. `asyncio.create_task(...)` and forgetting the handle looks right, but the
event loop keeps only weak references to tasks, so a task with no strong reference can be garbage
collected mid-flight, and its exception is swallowed; the asyncio documentation warns about exactly
this. Spawning a thread per request has no limit and loses exceptions the same way. A full job queue
(Celery, ARQ) is the correct tool for durable work, but it is a broker, a worker fleet and a
serialisation boundary, which is a lot of machinery for a fifty-millisecond insert.

`BackgroundTasks` is the deliberately small middle: Starlette keeps the task list on the response
object and awaits it right after the body has been flushed, so ordering relative to the response is
guaranteed, sync functions are pushed to the thread pool, async functions are awaited, and nothing new
has to be deployed. Its limits are the same facts stated the other way round: the work exists only in
this process's memory, so a crash, a deploy or an out-of-memory kill erases it silently.

## How it works
### Declaring tasks
Add a parameter typed `BackgroundTasks` to a path operation or to any dependency; FastAPI injects one
instance per request and merges every `add_task(func, *args, **kwargs)` call into a single ordered
list. `Response(..., background=BackgroundTask(fn))` is the lower-level Starlette form for custom
responses, including `StreamingResponse`.

### When they run
Starlette's `Response.__call__` sends `http.response.start`, then the body, then, if a background
object is attached, `await self.background()`. So tasks start only after the final body bytes have
been handed to the server; the client already has its response. `BackgroundTasks.__call__` runs them
sequentially in insertion order: an `async def` is awaited on the event loop, a plain `def` is run via
`run_in_threadpool`. Nothing runs in parallel, so one slow task delays the rest.

### What the server sees
The ASGI call for the request has not returned while tasks run. That means uvicorn's graceful shutdown
waits for them (up to its shutdown timeout), anything counting in-flight requests counts them, and the
handler's resources stay allocated. It also means the work dies with the process: an orchestrator that
sends SIGKILL after its grace period takes every pending task with it, and nothing recorded that they
existed.

### Exceptions
An exception in a task propagates out of the application after the response is gone. The server logs
it (uvicorn prints an "Exception in ASGI application" traceback), the client never learns of it, and
there is no retry. Wrap each task body in `try/except` with real logging and a metric, and write tasks
so that running twice is harmless, because the day you move them to a queue they will.

### Interaction with yield dependencies
A dependency that yields a database session has exit code that must run at some point. Whether that
teardown runs before or after background tasks has changed across FastAPI versions (0.106 changed the
default so sessions are not held while the response travels the network; later versions added a scope
option to choose). The only version-proof rule is: never hand a request-scoped resource to a background
task. Create the session, client or file handle inside the task.

### Capacity
Sync tasks share the default thread pool (anyio's limiter, 40 threads) with sync endpoints and sync
dependencies, so a burst of "cheap" sync tasks can starve ordinary sync routes. Async tasks share the
event loop, so a CPU-heavy task, computing embeddings locally, hashing a large upload, blocks every
request for its duration. Treat a task as part of the request's cost, just paid after the response.

## Example
An LLM endpoint that answers immediately and records a trace afterwards. The task opens its own
session, guards its own exceptions, and would be harmless if run twice.

```python
import logging, time
from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel

app, log = FastAPI(), logging.getLogger("trace")

class Ask(BaseModel):
    prompt: str

async def call_model(prompt: str) -> tuple[str, int]:
    return f"echo: {prompt}", 7                      # stand-in: (answer, output_tokens)

async def record_trace(user_id: str, prompt: str, answer: str, tokens: int, ms: float):
    try:
        async with session_factory() as session:     # its OWN session; never the request's
            session.add(Trace(user_id=user_id, prompt=prompt, answer=answer,
                              output_tokens=tokens, latency_ms=ms))
            await session.commit()
    except Exception:
        log.exception("trace write failed")          # client already has its answer: log, don't raise

@app.post("/answer")
async def answer(body: Ask, bg: BackgroundTasks):
    t0 = time.perf_counter()
    text, tokens = await call_model(body.prompt)
    ms = (time.perf_counter() - t0) * 1000
    bg.add_task(record_trace, "user-123", body.prompt, text, tokens, ms)   # runs after the body is sent
    return {"answer": text}

# session_factory and Trace come from your SQLAlchemy setup (see the async database lesson).
```

## Trade-offs
- Zero infrastructure and in-process execution, but zero durability: the moment losing the work has a
  cost (billing ledgers, audit records) the trade flips to a queue with persisted jobs.
- Latency is hidden from the user, but the worker is still busy; under load, background work competes
  with new requests for the loop and thread pool, and once tasks take seconds that competition is
  visible in p99 latency.
- Sequential execution per request is simple to reason about, but tasks cannot overlap, so a slow
  webhook delays the trace write queued behind it.
- Exceptions are isolated from the client, which protects the user experience, but the same isolation
  hides failures unless you add logging and metrics yourself.

## Pitfalls
- Running the LLM job itself in the background and returning a job id; the symptom is jobs that vanish
  after every deploy and a status endpoint with nothing to read, because nothing persisted the job.
- Passing the request's yield-dependency session into a task; the symptom is "session is closed" or
  "connection already returned to pool" errors, often appearing only after a FastAPI upgrade.
- A CPU-heavy sync task; the symptom is latency on unrelated sync endpoints rising with traffic as the
  40-thread pool fills up.
- A blocking call (sync HTTP client, sync SDK) inside an async task; the symptom is the whole worker
  freezing for the duration of each task, visible as periodic stalls in every route.
- Assuming the client would have noticed a failure; the symptom is silent data loss in traces or
  counters discovered weeks later during an audit.
- Reaching for bare `asyncio.create_task` instead; the symptom is tasks that sometimes never run,
  because nothing held a reference to them.

## When to use
Use `BackgroundTasks` for short, idempotent, loss-tolerant follow-ups: trace and metric writes, cache
warming, non-critical notifications, cleanup of temporary files. Do not use it for the LLM call, for
anything a customer will later ask about, for retries, or for work longer than a few seconds. The
decision test is "if this process dies now, is losing this acceptable?" Measure task duration, failure
rate from your own logging, and thread-pool saturation. In interviews the key distinction to articulate
is after-response versus durable: when it runs, where it runs, and what happens on a crash.

## Role lens
- **Engineer:** Queues trace writes and counters after the response, gives each task its own
  resources and exception handling, and knows the thread-pool and event-loop cost of each.
- **Architect:** Draws the line between loss-tolerant follow-ups and durable jobs, and mandates a
  queue for anything with financial or audit consequences.
- **FDE:** Spots a customer's "we run the summarisation in a BackgroundTask" design and explains why
  their jobs disappear on deploys before it becomes a support escalation.

## Check yourself
**Q:** A team writes the per-request billing ledger entry in a `BackgroundTasks` function to keep
latency low. What is wrong with that, and what would you do instead?
**A:** A ledger entry is money, and `BackgroundTasks` offers no durability: a crash, SIGKILL after the
shutdown grace period or an unhandled exception loses the entry silently, and the client has already
received a success. Either write the entry synchronously in the same transaction as the request's own
state, which costs a few milliseconds and is the simplest correct option, or record the intent
durably before responding and process it asynchronously: insert an outbox row in that transaction and
let a worker or queue consumer post it, with idempotency keyed by request id so retries never
double-charge. Keep `BackgroundTasks` for the non-financial trace and metrics writes.

## Quiz
### Q: When exactly does a function added with BackgroundTasks start running?
- [ ] Immediately, in parallel with the handler, on a separate thread
- [x] After the response body has been sent to the client, inside the same process and request lifecycle
- [ ] When the event loop is idle, on a best-effort basis
- [ ] On the next request to the same worker
> Starlette awaits the background object right after flushing the body, so the client already has its
> response but the ASGI call is still running until the tasks finish.

### Q: What happens when a background task raises an exception?
- [ ] The client receives a 500 response
- [ ] FastAPI retries the task up to three times
- [x] The server logs the traceback, the client is unaffected, and the work is simply lost
- [ ] The exception is delivered to the next request on that connection
> The response is already sent, so there is no status code to change and no retry machinery. Your own
> try/except with logging is the only visibility you get.

### Q: Which of these is an appropriate use of BackgroundTasks?
- [ ] Running a three-minute batch summarisation and returning a job id
- [ ] Capturing a card payment after responding with "order confirmed"
- [x] Posting a non-critical analytics event about the completed request
- [ ] Writing the invoice record that finance reconciles against
> The test is whether losing the work on a crash is acceptable. An analytics event is; a payment,
> an invoice or a multi-minute job need a durable queue with persisted job state and retries.
