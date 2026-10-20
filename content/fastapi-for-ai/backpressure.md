---
id: backpressure
module: fastapi-for-ai
name: Backpressure
kind: concept
difficulty: advanced
est_minutes: 16
related:
  - fastapi-for-ai/concurrency-limits
  - fastapi-for-ai/streamingresponse
  - fastapi-for-ai/rate-limiting
  - fastapi-for-ai/job-queues-and-durable-workers
  - reliability/backpressure-and-load-shedding
  - serving-inference/backpressure
  - infra-capacity-economics/queueing-theory
  - node-ts-for-ai/streams-and-backpressure
sources:
  - Official docs: Python asyncio (Queues)
  - Official docs: ASGI specification (HTTP messages)
  - Official docs: Uvicorn (Settings)
---

## TL;DR
Backpressure is a slow consumer's signal travelling back to a fast producer, so the producer slows
down instead of piling work into a buffer. Without it, overload turns into unbounded queues: latency
grows until every request times out and memory grows until the process dies. In a FastAPI AI service
it takes three forms: bounded internal queues, rejecting at the edge with 429/503 and `Retry-After`,
and the flow control that pauses a streaming generator when the client reads slowly.

## Why it exists
Every pipeline has a slowest stage, and in AI services it is almost always the model: a GPU that can
decode for a fixed number of sequences, a provider quota, an embedding service with a throughput
ceiling. When work arrives faster than that stage completes it, the difference has to go somewhere.
If each stage simply accepts everything and buffers it, the excess accumulates in queues: in
`asyncio` tasks waiting on a semaphore, in an unbounded `asyncio.Queue`, in the provider's own queue,
in socket buffers.

Queues do not add capacity; they convert overload into waiting. If capacity is 8 requests per second
and 12 arrive per second, the queue grows by 4 every second forever. Work at the back of the queue
waits longer than the client's timeout, so the server spends capacity on answers nobody receives, the
clients retry and add more load, and the system collapses at exactly the moment it is busiest. The
naive response, adding a bigger buffer, only delays the collapse and makes it worse, because a deeper
queue means longer waits for everything in it.

Backpressure replaces "accept and hope" with a feedback loop: each stage advertises how much it can
take, and when it is full the stage upstream either waits briefly, slows down, or is told no. The
system then runs at the bottleneck's rate with bounded latency, and the excess is handled
deliberately rather than by timeouts.

## How it works
### The core rule: every buffer is bounded
A bounded buffer is what turns "slow" into a signal. `asyncio.Queue(maxsize=n)` makes `await
q.put(x)` suspend when full, which slows a producer coroutine to the consumer's pace, and
`q.put_nowait(x)` raise `QueueFull`, which lets an endpoint reject instead of waiting. A semaphore
with a bounded acquire timeout is the same idea for in-flight calls. Size bounds from the latency
you can tolerate, *because* by Little's law the wait at the back of a queue of length *L* in front
of a stage with throughput *X* is about *L / X*.

### At the HTTP edge you cannot slow the client, only answer it
An HTTP client is a producer you do not control. The server's only backpressure tools are refusing
early and telling the client when to come back: `503 Service Unavailable` (or `429 Too Many Requests`
when it is a per-client quota) with a `Retry-After` header. Do it before reading large bodies or
calling anything expensive, *because* rejection is only cheap if it happens first. Uvicorn's
`--limit-concurrency` gives a blunt server-wide version, returning 503 above a connection or task
count; per-dependency semaphores give the precise version. Upstream load balancers and well-behaved
clients turn these signals into retries with backoff or into routing to another replica.

### Inside a stream, flow control is automatic but partial
When a `StreamingResponse` generator yields a chunk, the server's `send` writes it to the socket. If
the client reads slowly, the kernel's send buffer and the server's write buffer fill, the server
pauses writing, and the `await` on `send` stops returning, so the generator stops being iterated.
That is real backpressure from the client to your code. It stops at your code, though: if the
generator is reading tokens from a provider stream, the provider usually keeps generating on its
side, and buffers or bills tokens regardless. For an expensive upstream, a stalled or vanished client
should become an explicit decision to cancel, not an implicit pause.

### Upstream signals
Your service is also a producer for the provider. A 429 or 503 from the provider, especially with
`Retry-After`, is backpressure aimed at you. Honour it: back off, retry with jitter within the
deadline, and reduce your own concurrency rather than retrying at full rate. Adaptive limiters lower
concurrency when latency or error rates rise and raise it slowly when they recover, the same
additive-increase, multiplicative-decrease shape TCP congestion control uses.

### Backpressure versus load shedding versus queuing
Backpressure slows the producer. Load shedding drops work when slowing is impossible, preferably the
least valuable work first (free tier before paid, background re-indexing before interactive chat).
Durable job queues move work that can wait, like batch embedding, out of the request path entirely,
with depth limits of their own. A well-built service uses all three.

## Example
An ingest endpoint feeding a bounded queue drained by a fixed pool of embedding workers. When the
queue is full the endpoint rejects at once instead of queueing more work than the workers can finish
within the latency budget.

```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request

EMBED_WORKERS, QUEUE_MAX = 8, 200   # 200 waiting chunks is the most we accept

async def embed_worker(q: asyncio.Queue):
    while True:
        chunk = await q.get()
        try:
            await embed_and_store(chunk)          # slow stage: the embedding service
        finally:
            q.task_done()

@asynccontextmanager
async def lifespan(app: FastAPI):
    q = asyncio.Queue(maxsize=QUEUE_MAX)
    workers = [asyncio.create_task(embed_worker(q)) for _ in range(EMBED_WORKERS)]
    app.state.embed_q = q
    yield
    for w in workers: w.cancel()

app = FastAPI(lifespan=lifespan)

@app.post("/ingest")
async def ingest(body: dict, request: Request):
    q: asyncio.Queue = request.app.state.embed_q
    if q.qsize() + len(body["chunks"]) > QUEUE_MAX:            # all-or-nothing admission
        raise HTTPException(503, "ingest saturated", headers={"Retry-After": "10"})
    for c in body["chunks"]:
        q.put_nowait(c)
    return {"accepted": len(body["chunks"]), "queued": q.qsize()}
```

Numbers (illustrative): if each embed call takes 0.5 s, 8 workers drain 16 chunks per second, so a
full queue of 200 waits about 200 / 16 ≈ 12.5 s. If the product needs ingested text searchable within
15 s, 200 is the right bound; an unbounded queue under a 30-chunks-per-second burst would grow by 14
chunks every second and push waits past minutes. This in-process queue is lost on restart; work that
must survive belongs in a durable job queue with the same depth limit.

## Trade-offs
- Bounded queues keep latency predictable but reject work during bursts the system might have absorbed
  later; deeper bounds suit batch work with loose deadlines, shallow ones interactive chat.
- Rejecting early with 503 protects everyone already admitted, but pushes retry handling onto clients;
  it only works when clients honour `Retry-After` and back off with jitter.
- Waiting briefly on a full stage smooths spikes, while waiting long just converts overload into
  timeouts; the wait should be a small fraction of the client's deadline.
- Shedding by priority keeps paid or interactive traffic healthy under overload, at the cost of
  classifying requests up front and occasionally failing work that was cheap to serve.

## Pitfalls
- Using unbounded `asyncio.Queue()` or unbounded task creation with `create_task` per request; memory
  climbs during a burst and p99 latency rises steadily while CPU looks idle.
- Retrying provider 429s immediately at full concurrency; the error rate increases after the first
  throttle instead of recovering, because retries add load to a saturated dependency.
- Rejecting only after reading the full body or calling the model; overload responses are as
  expensive as successes, so shedding does not relieve anything.
- Assuming a paused stream pauses the provider; a slow or stalled client keeps upstream tokens billing
  while the generator waits on `send`.
- Measuring only average latency; queue growth shows up first in p99 and in queue depth, so averages
  hide the collapse until it is total.

## When to use
Design for backpressure in any service whose slowest stage is a model, a quota or a GPU, which is
nearly every AI service. Bound every queue and in-flight pool, decide which requests to reject first,
return 503 or 429 with `Retry-After`, and honour the same signals from your providers. Move work that
can wait into a durable queue. Measure queue depth, time in queue, rejection rate, p99 latency and
upstream 429s. In interviews, expect to explain why bigger buffers make overload worse and to show
where in a request path you would shed load.

## Role lens
- **Engineer:** Bounds queues and semaphores, returns fast 503s with `Retry-After`, honours upstream
  429s with backoff, and exposes queue depth as a metric.
- **Architect:** Defines shedding priorities, latency budgets per stage and which work moves to durable
  queues, so the system degrades predictably under overload.
- **FDE:** Explains to a customer why their burst ingestion got 503s, tunes depth to their deadlines,
  and helps their client honour `Retry-After` instead of retrying in a tight loop.

## Check yourself
**Q:** During a marketing launch, chat latency climbs from 3 s to 45 s over ten minutes, CPU stays
low, memory rises, then the service starts timing out entirely. The code awaits a semaphore with no
timeout before each LLM call. Explain what happened and how you would redesign it.
**A:** Arrivals exceeded the throughput the semaphore allowed, and with no bound on the wait every
excess request became a suspended coroutine holding its request state, so the wait queue grew
linearly with the overload. By Little's law the wait was queue length divided by throughput, which
crossed client timeouts, so the service spent capacity on answers nobody received and retries added
load. The redesign bounds the wait (a short acquire timeout or an explicit queue cap), returns 503
with `Retry-After` before doing expensive work, sheds lower-priority traffic first, alerts on queue
depth, and raises real capacity if the overload is normal demand rather than a spike.

## Quiz
### Q: A stage handles 10 requests per second and 15 arrive per second. What does adding a larger unbounded buffer in front of it achieve?
- [ ] It raises throughput to 15 requests per second
- [ ] It stabilises latency at a higher but constant level
- [x] It delays failure while waits grow without limit, until requests time out
- [ ] It converts the excess into 503 responses automatically
> The queue grows by 5 requests per second regardless of its size, so waiting time rises steadily.
> A buffer absorbs short bursts, but under sustained overload it only postpones and worsens timeouts.

### Q: What is the server's most direct backpressure mechanism toward an external HTTP client?
- [ ] Slowing down the TCP accept loop for that client's IP
- [x] Rejecting early with 503 or 429 and a `Retry-After` header
- [ ] Holding the request open until capacity frees up
- [ ] Returning 200 with an empty body
> The server cannot make a remote client produce more slowly; it can only refuse cheaply and say when to
> retry. Holding requests open is unbounded queuing, which is what backpressure prevents.

### Q: A streaming endpoint relays tokens from a provider to a client on a slow mobile network. What happens when the client stops reading?
- [ ] The provider automatically pauses generation
- [ ] The server buffers the entire remaining response in memory
- [x] The server's `send` stops completing, so the generator pauses, but the provider may keep generating
- [ ] FastAPI raises a `BackpressureError` in the generator
> Socket and server buffers fill and the awaited `send` stalls the generator, which is real flow
> control. It does not reach the provider, which may keep generating and billing unless you cancel.
