---
id: synchronous-vs-asynchronous-execution
module: system-design
name: Synchronous vs asynchronous execution
kind: decision
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - system-design/event-driven-architecture
  - fastapi-for-ai/job-queues-and-durable-workers
  - backend-fundamentals/background-jobs-durable-workers
  - serving-inference/streaming
  - fastapi-for-ai/server-sent-events
  - fastapi-for-ai/timeouts
  - reliability/backpressure-and-load-shedding
  - infra-capacity-economics/queueing-theory
  - python-for-ai/asyncio
sources:
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Nygard, 2018 — Release It! (2nd ed.)
  - Official docs: FastAPI (background tasks)
---

## TL;DR
Synchronous execution means the caller holds a connection open until the result arrives; asynchronous
execution means the caller gets an acknowledgement (a job id) and the result arrives later by polling,
push or callback. LLM calls take seconds and agent runs take minutes, which is longer than load
balancers, mobile networks and humans will wait, so most AI workloads need the asynchronous shape, with
streaming as the middle ground when a person is watching.

## Why it exists
A classic web request completes in tens of milliseconds, so holding the connection is free. An LLM
completion takes seconds; a tool-using agent run takes minutes; a batch of a thousand document
summaries takes an hour. Holding a connection for that long collides with limits you do not control.
Every hop in the path has an idle timeout: load balancers and reverse proxies commonly default to around
a minute (the exact value varies by product), and mobile networks drop connections when the device
changes cells. When a hop times out, the client sees an error and retries, but the server is still
working on the original request. Now the same expensive run executes twice, you pay twice for tokens,
and if the run has side effects (it posted a comment, it charged a card) they happen twice.

Capacity is the second problem. By Little's law, concurrent in-flight requests ≈ arrival rate × time in
system. At 10 requests per second averaging 30 seconds each, every instance holds 300 open connections,
each with a goroutine, thread or coroutine, its buffers and its partial state. Scale-out is driven by
connection count rather than by useful work.

The third problem is loss. If the server process restarts mid-request, a synchronous run vanishes; the
client learns only that the socket closed. There is no record that the work existed, so there is
nothing to resume.

The naive fix, "raise every timeout to ten minutes", only hides the issue: long-lived connections pile
up, one deploy drops them all, and the client still cannot distinguish "slow" from "dead". The real fix
is to separate *accepting* the work from *doing* it, which is what asynchronous execution means.

## How it works
### Two meanings of "asynchronous"
Inside one process, *async I/O* (`asyncio`, Node's event loop) lets a thread wait on many sockets at once
instead of blocking per request. That improves how many connections a server can hold; it does not
change the interaction model, and it is covered in the language modules. This lesson is about the
*interaction* model between a caller and a service: does the caller wait for the result or not?

### The synchronous shape
Client sends a request, the server does the work, the response carries the result. The caller's state is
the connection itself. This is the right shape when work completes well inside every timeout in the path
and when the caller needs the answer to continue. Its failure semantics are simple: an error means the
result was not delivered, but it does *not* mean the work did not happen, which is why even synchronous
calls that have side effects need an idempotency key.

### Streaming: synchronous with incremental delivery
Server-sent events or chunked responses keep the connection synchronous but push partial results as they
are produced (tokens, tool-call progress). Time-to-first-byte drops from the full generation time to the
prefill time, which is what makes a 40-second answer tolerable to a person. The connection is still held,
so timeouts and retries still apply; a reconnecting client needs a way to resume ("give me events after
sequence 812") or it re-runs the generation.

### The asynchronous shape
1. **Submit.** The client sends the request with a client-generated idempotency key. The server validates
   it, persists a job record (`status=queued`, inputs, key, tenant, created_at) and returns `202 Accepted`
   with a job id and a status URL. The write to the job store is the only thing that must succeed inside
   the request, so the request is fast and the work survives a process restart *because* it exists in
   durable storage before anyone starts on it.
2. **Execute.** A worker claims the job from a queue (Redis, SQS, Postgres with `SKIP LOCKED`), marks it
   `running` with a lease or heartbeat, performs the work, and writes the result and `succeeded` or
   `failed` status. A lease matters because a worker that dies mid-job must have its job reclaimed by
   another worker after the lease expires; without it the job stays `running` forever.
3. **Deliver.** The client learns the outcome by polling `GET /jobs/{id}` (simple, works through any
   proxy, costs requests), by a webhook the server calls (efficient, but the server must retry and the
   receiver must verify and deduplicate), or by a push channel such as SSE or WebSocket subscribed to the
   job (best UX for interactive clients).

The status model is a small state machine: `queued → running → succeeded | failed | cancelled`, often
with a `progress` field and an `attempt` counter. Results need a retention policy; a job store that keeps
every result forever becomes the biggest table in the system.

### The hybrid: respond if fast, otherwise hand back a job
Many APIs accept a `wait` budget: the server runs the job and, if it finishes within, say, 20 seconds,
returns the result inline; otherwise it returns the job id. The client code handles both outcomes. This
keeps short requests simple without making long ones fragile.

### Backpressure
An asynchronous front door accepts work faster than workers drain it, so the queue is where overload
becomes visible. Bound it: reject submissions with `429` or `503` when queue depth or estimated wait
exceeds a threshold, and expose that wait estimate to clients. An unbounded queue turns an outage into a
multi-hour backlog that keeps failing after the cause is fixed.

## Example
A minimal FastAPI service with the asynchronous shape: submit returns `202` and a job id, a worker drains
a queue, and a status endpoint reports progress. The in-memory queue and dict are for illustration; in
production they are a durable queue and a database table so jobs survive restarts.

```python
import asyncio, uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Response

jobs: dict[str, dict] = {}              # production: a table with status, result, attempt, lease
queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)   # bounded => backpressure

async def run_analysis(text: str) -> str:
    await asyncio.sleep(5)              # stands in for a multi-second LLM/agent run
    return f"{len(text.split())} words"

async def worker():
    while True:
        job_id = await queue.get()
        jobs[job_id]["status"] = "running"
        try:
            jobs[job_id]["result"] = await run_analysis(jobs[job_id]["input"])
            jobs[job_id]["status"] = "succeeded"
        except Exception as e:          # production: retry with backoff, then dead-letter
            jobs[job_id].update(status="failed", error=str(e))
        queue.task_done()

@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(worker()); yield; task.cancel()

app = FastAPI(lifespan=lifespan)

@app.post("/analyses", status_code=202)
async def submit(body: dict, response: Response):
    job_id = body.get("idempotency_key") or str(uuid.uuid4())
    if job_id not in jobs:              # a retried submit returns the same job
        jobs[job_id] = {"status": "queued", "input": body["text"]}
        try:
            queue.put_nowait(job_id)
        except asyncio.QueueFull:
            del jobs[job_id]; raise HTTPException(503, "busy, retry later")
    response.headers["Location"] = f"/analyses/{job_id}"
    return {"job_id": job_id, "status": jobs[job_id]["status"]}

@app.get("/analyses/{job_id}")
async def status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)
    return jobs[job_id]
```

A client retrying the `POST` with the same `idempotency_key` gets the same job back instead of a second
five-second run; a full queue is refused immediately rather than silently growing.

## Trade-offs
- Synchronous calls are simple to build, test and reason about, but their reliability is bounded by the
  shortest timeout in the path; once p99 work time approaches that timeout, the shape must change.
- Asynchronous jobs survive restarts and absorb bursts, but they add a job store, a worker fleet, a status
  API and client-side polling or callback logic; for sub-second work that machinery is pure overhead.
- Streaming gives the best perceived latency for a human reader, yet it holds connections, complicates
  retries and resumption, and offers nothing to a machine caller that only wants the final answer.
- Polling is proxy-friendly and stateless but wastes requests and adds up to one interval of latency;
  webhooks are efficient but make you responsible for retries, signatures and the receiver's uptime.
- The hybrid "wait up to N seconds" keeps simple cases simple, but clients must handle both response
  shapes, and a poorly chosen N recreates the timeout problem it was meant to avoid.

## Pitfalls
- Raising timeouts layer by layer instead of changing the shape; the symptom is duplicate runs and double
  token spend after clients retry requests the server was still processing.
- Accepting jobs without an idempotency key; every client retry of the submit creates a new job, visible
  as bursts of identical jobs seconds apart.
- Workers with no lease or heartbeat; a worker crash leaves jobs in `running` indefinitely and users see
  spinners that never resolve.
- Unbounded queues; an upstream outage builds a backlog that takes hours to drain, during which fresh
  requests wait behind stale ones nobody still wants.
- Polling with a fixed short interval from thousands of clients; status endpoints become the top traffic
  source and the database behind them the bottleneck, which is the cue to add push delivery or backoff.
- Results kept forever with no TTL and no size limit; the jobs table becomes the largest and slowest
  table and its backups the longest.

## When to use
Decide from the p99 of the work, not the median, against the smallest timeout in the path and against
how long a human will wait. Work that finishes within a few seconds at p99 stays synchronous. Work that
takes several seconds to roughly a minute with a person watching should stream. Anything longer, anything
that must survive a restart, anything with retries and compensation, and anything with no human waiting
(batch summarisation, re-indexing, nightly evaluations) is an asynchronous job. Measure time-to-first-byte,
end-to-end completion time, queue depth and the ratio of retried submits to unique jobs. In day-to-day
work this shows up as the decision of whether an endpoint returns `200` with a body or `202` with a
`Location` header, and whether your agent framework exposes a `run` call or a `submit` plus `events`
pair. In interviews, name the three shapes, justify each with a timeout or a human-attention argument,
and mention idempotency keys and leases without being prompted.

## Role lens
- **Engineer:** Builds the `202` + status endpoint pair, adds idempotency keys to submits, implements
  worker leases and retries, and streams tokens over SSE for interactive paths.
- **Architect:** Sets the latency budget per interaction, chooses queue technology and delivery channel,
  and defines the job lifecycle, retention and backpressure policy as platform standards.
- **FDE:** Fits the product's job model to the customer's integration style, often replacing a
  synchronous call that times out through their gateway with polling or a signed webhook.

## Check yourself
**Q:** A customer's workflow calls your agent synchronously through their API gateway, which times out at
30 seconds. Agent runs take 5 to 120 seconds. They report intermittent "failures" and duplicate tickets
being created in their system. Diagnose the problem and propose a design.
**A:** The gateway closes the connection at 30 seconds, so every run longer than that looks like a failure
even though the agent keeps working and eventually creates the ticket; the customer's retry then starts a
second run that creates a second ticket. Two changes are needed. First, change the shape: `POST /runs`
persists the run and returns `202` with a run id inside the gateway's timeout, a worker executes it, and
the customer polls `GET /runs/{id}` or receives a signed webhook on completion. Second, make the ticket
creation idempotent by deriving its key from the run id and the step, so even a genuine retry cannot
create two tickets. Workers need leases so a crash reclaims the run, the queue should be bounded with a
clear `503` on overload, and run results need a retention window. If the customer wants progress, offer
an SSE stream keyed by run id rather than lengthening the gateway timeout.

## Quiz
### Q: Why does raising the load balancer timeout from 60 s to 600 s not fix an endpoint whose agent runs take up to five minutes?
- [ ] Load balancers cannot be configured above 60 seconds
- [x] The work still vanishes on a restart, retries still duplicate it, and connection count grows with run time
- [ ] Longer timeouts increase token cost per request
- [ ] HTTP/1.1 does not permit responses slower than 60 seconds
> A longer timeout hides the symptom but keeps the synchronous shape: in-flight runs have no durable record,
> a client or proxy retry re-executes them, and each instance holds connections proportional to run time.
> Separating submission from execution addresses all three.

### Q: A client retries a job submission after a network error and the server creates a second identical job. What was missing?
- [ ] A longer polling interval
- [ ] A streaming response instead of a job id
- [x] A client-supplied idempotency key that the server uses to return the existing job
- [ ] A webhook so the client would not have retried
> The submit request itself can be duplicated by the network, so the server must recognise a repeat. An
> idempotency key stored with the job lets the retry return the original job id instead of enqueuing new work.
> Delivery channel and polling cadence do not affect submission duplicates.

### Q: When is streaming the right choice over a background job?
- [x] A person is watching and the work finishes within the connection's timeout budget
- [ ] The work must survive a server restart
- [ ] The caller is a batch process that only needs the final answer
- [ ] The result must be delivered to several consumers
> Streaming improves perceived latency for a human reader while keeping a synchronous connection, so it fits
> interactive work that still completes within timeouts. Durability, machine callers and fan-out all point to
> a persisted job with polling, push or events.
