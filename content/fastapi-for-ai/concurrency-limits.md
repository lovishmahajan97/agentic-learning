---
id: concurrency-limits
module: fastapi-for-ai
name: Concurrency limits (semaphores)
kind: pattern
difficulty: core
est_minutes: 15
related:
  - fastapi-for-ai/backpressure
  - fastapi-for-ai/rate-limiting
  - fastapi-for-ai/shared-httpx-asyncclient
  - fastapi-for-ai/uvicorn-gunicorn-workers
  - serving-inference/batching-and-concurrency-limits
  - python-for-ai/race-conditions-and-synchronization-primitives
  - reliability/bulkheads
  - infra-capacity-economics/queueing-theory
sources:
  - Official docs: Python asyncio (Synchronization Primitives)
  - Official docs: AnyIO (Synchronization, Working with threads)
  - Official docs: Uvicorn (Settings)
---

## TL;DR
An async FastAPI worker will happily start thousands of model calls at once; the provider, the GPU or
your memory will not handle them. A semaphore caps how many run concurrently: each call takes a slot
before starting and returns it when done, and everyone else waits or is turned away. Size the cap
from the downstream's real limit divided across workers, bound the wait, and fail fast with 503
when the wait is too long.

## Why it exists
With `async def` endpoints, a worker does not run out of threads; each request is a cheap coroutine.
That is the point of async, and also the danger. A burst of 2,000 chat requests becomes 2,000
concurrent provider calls in a few milliseconds. The provider answers with 429s past its concurrency
quota, a self-hosted vLLM server queues them internally until latency is useless, each in-flight
request holds a connection, a prompt and a growing response in memory, and retries from impatient
clients double the load. Nothing in the async machinery pushes back.

The naive answers do not fix it. A rate limit (requests per second) controls arrivals, not how many
are in flight at once; with LLM calls taking anywhere from one to sixty seconds, the same rate can
mean ten or six hundred concurrent calls. A bigger connection pool only moves the queue into
`PoolTimeout`. More workers multiply the problem. What is missing is a direct cap on in-flight work
for each scarce resource, enforced before the work starts. The relationship to aim at is Little's law:
in-flight = throughput × latency. If a dependency can sustain 64 concurrent calls and calls take 4
seconds on average, its ceiling is about 16 requests per second, whatever you do upstream.

## How it works
### The primitive
`asyncio.Semaphore(n)` holds a counter starting at *n*. `acquire()` decrements it, or suspends the
coroutine if it is zero; `release()` increments it and wakes one waiter. `async with sem:` pairs them
and releases even on exceptions and cancellation, *because* a leaked slot permanently shrinks capacity
until restart. `BoundedSemaphore` additionally raises if released more times than acquired, which
turns a double-release bug into an error instead of silent extra capacity. AnyIO's `CapacityLimiter`
is the equivalent when the codebase uses AnyIO.

### Where it lives
Create one semaphore per scarce dependency at startup in the lifespan and store it on `app.state`.
It must be shared across requests, *because* a semaphore created per request limits nothing. It is
also bound to the process: each Uvicorn worker has its own, so the effective limit is
`workers × n`. To keep a fleet under a provider quota of 64 with 4 workers, use 16 per worker; with
autoscaling replicas, a fixed per-process number drifts and you need a shared limiter (for example
leases with expiry in Redis) or a gateway that enforces the global cap.

### Bound the wait
A plain `async with sem:` waits forever. Under sustained overload the wait queue grows without limit,
which is just an unbounded queue with extra steps. Wrap the acquire in a timeout, and if it expires
return 503 with `Retry-After`, *because* a fast, honest rejection lets the client or load balancer
retry elsewhere while a slow one burns the client's whole timeout. You can also check
`sem.locked()` to reject immediately when there is no free slot.

### Separate limits per dependency
One semaphore for the LLM, another for embeddings, another for the reranker or a slow internal tool.
This is the bulkhead idea: a stall in one dependency fills only its own slots instead of starving
every endpoint. Endpoints that call several dependencies take each slot only around the call that
needs it.

### Streaming endpoints
For a `StreamingResponse`, acquire the slot *before* returning the response, so you can still answer
503 with proper headers, and release it in the generator's `finally`, *because* the expensive work
happens while the stream is being consumed, after the endpoint function has returned. One edge
case: an async generator that is never started never runs its `finally`, so if the response is
abandoned before the first chunk the slot can leak; guard with a release-once flag that is also
triggered from cleanup outside the generator, and alert on slots that never return.

### The hidden limit you already have
Sync `def` endpoints and `run_in_threadpool` calls run on AnyIO's worker thread pool, which has a
default capacity limiter (40 threads in current AnyIO). That is a concurrency limit too, often hit by
accident when blocking SDK calls sit in a sync endpoint. Server-wide, Uvicorn's `--limit-concurrency`
returns 503 once a worker holds too many connections or tasks; it is a coarse safety net, not a
per-dependency limit.

## Example
A per-worker cap on calls to an LLM provider whose quota is 64 concurrent requests across 4 workers,
with a bounded wait and a slot held for the full duration of a stream.

```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

WORKERS, PROVIDER_LIMIT = 4, 64

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.llm_slots = asyncio.BoundedSemaphore(PROVIDER_LIMIT // WORKERS)  # 16 per worker
    yield

app = FastAPI(lifespan=lifespan)

async def take_slot(sem: asyncio.Semaphore, wait_s: float = 2.0) -> None:
    try:
        async with asyncio.timeout(wait_s):    # Python 3.11+
            await sem.acquire()
    except TimeoutError:
        raise HTTPException(503, "LLM capacity full", headers={"Retry-After": "2"})

@app.post("/chat")
async def chat(body: dict, request: Request):
    sem = request.app.state.llm_slots
    await take_slot(sem)                       # reject before headers are sent

    async def gen():
        try:
            async for token in stream_llm(body["messages"]):   # provider call
                yield token
        finally:
            sem.release()                      # held until the stream really ends
    return StreamingResponse(gen(), media_type="text/plain")
```

With an average generation of 8 seconds, 16 slots per worker sustain about 16 / 8 = 2 requests per
second per worker, 8 across the fleet. Above that, requests wait up to 2 seconds and then get 503
instead of piling up.

## Trade-offs
- A tight cap protects the downstream and keeps latency predictable, but rejects requests the
  downstream might have absorbed; raise it only when measured downstream latency stays flat.
- Waiting briefly for a slot smooths short bursts; waiting long only converts overload into latency,
  so the right wait is short compared with the client's timeout.
- Per-process semaphores are simple and fast but assume a fixed worker count; under autoscaling, a
  distributed limiter is accurate at the price of a network round trip per acquire.
- One shared cap per dependency is fair to no one in particular; per-tenant caps prevent a noisy
  customer from taking every slot, at the cost of idle capacity when tenants are quiet.

## Pitfalls
- Creating the semaphore inside the endpoint or a per-request dependency; it limits nothing, and the
  symptom is provider 429s at the same load as before the change.
- Forgetting that the cap is per worker; scaling from 4 to 8 workers doubles provider concurrency and
  429s return after the scale-up.
- Acquiring without a timeout; under overload p99 latency climbs to the client timeout and memory
  grows with the number of suspended waiters.
- Releasing in the endpoint for a streaming response; the slot is freed when headers go out, so real
  concurrency against the provider far exceeds the cap.
- Manual `acquire()` without `try/finally`; a cancelled or failed request leaks a slot, and capacity
  shrinks slowly until the worker seems to hang at zero.

## When to use
Put a semaphore in front of every scarce or quota-bound dependency an async service calls: hosted
LLMs with concurrency quotas, self-hosted inference servers whose latency degrades past a batch size,
embedding and reranking services, slow internal tools. Pair it with rate limiting for per-client
fairness and with backpressure for what to do when slots run out. Measure slot utilisation, wait
time, rejection rate and downstream latency against concurrency. In interviews, expect to compute a
per-worker limit from a quota and to explain why rate limits alone do not bound concurrency.

## Role lens
- **Engineer:** Creates per-dependency semaphores in the lifespan, bounds acquisition with a timeout,
  and holds slots correctly across streaming responses and cancellation.
- **Architect:** Owns the concurrency budget per dependency across workers and replicas, and decides
  where a global limiter or gateway replaces per-process caps.
- **FDE:** Sizes caps to the customer's provider quota or GPU deployment, and explains the resulting
  throughput ceiling in requests per second using Little's law.

## Check yourself
**Q:** Your team caps LLM calls at 20 per worker with 6 workers. The provider allows 100 concurrent
requests. Average generation is 5 seconds. What throughput can you sustain, what happens at 30
requests per second, and what would you change?
**A:** The fleet allows 6 × 20 = 120 in flight, above the provider's 100, so near saturation you will
see 429s; set per-worker caps to about 16. With 96 slots and 5-second calls, Little's law gives about
96 / 5 ≈ 19 requests per second. At 30 requests per second arrivals exceed that by 11 per second, so
waiters accumulate; with a bounded wait the excess gets fast 503s with `Retry-After` rather than
timing out. To serve 30 per second you need more provider concurrency, shorter generations (lower
max tokens), caching, or routing some traffic to another model.

## Quiz
### Q: Why does a requests-per-second rate limit not bound the number of concurrent LLM calls?
- [ ] Rate limits only apply to authenticated clients
- [x] In-flight work equals arrival rate times latency, and LLM latency varies widely
- [ ] Rate limits are enforced after the call completes
- [ ] Async endpoints bypass rate limiting middleware
> By Little's law, concurrency is throughput multiplied by time in system. The same 10 requests per
> second can mean 10 or 600 calls in flight depending on whether generations take 1 or 60 seconds.

### Q: A streaming endpoint acquires a semaphore slot in the endpoint body with `async with sem:` around `return StreamingResponse(gen())`. What happens?
- [ ] The slot is held until the client disconnects
- [x] The slot is released as soon as the response object is returned, before tokens are generated
- [ ] The stream cannot start until another request releases a slot
- [ ] FastAPI raises an error because semaphores cannot be used with streams
> The `async with` exits when the function returns the response object, but generation happens later
> while the server iterates the generator. Release in the generator's `finally` to cover the stream.

### Q: A provider allows 120 concurrent requests and you run 8 Uvicorn workers with one semaphore each. What per-worker limit keeps the fleet within quota?
- [ ] 120
- [ ] 960
- [x] 15
- [ ] 8
> Each worker process has its own semaphore, so the fleet total is workers times per-worker limit.
> 120 / 8 = 15 per worker; setting 120 each would allow 960 concurrent calls.
