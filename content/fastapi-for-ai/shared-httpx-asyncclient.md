---
id: shared-httpx-asyncclient
module: fastapi-for-ai
name: Shared httpx AsyncClient
kind: pattern
difficulty: core
est_minutes: 13
related:
  - python-for-ai/httpx
  - fastapi-for-ai/lifespan
  - fastapi-for-ai/request-scoped-vs-application-scoped-resources
  - fastapi-for-ai/timeouts
  - fastapi-for-ai/concurrency-limits
  - fastapi-for-ai/testclient-plus-pytest-asyncio
  - python-for-ai/openai-sdk
sources:
  - Official docs: HTTPX (Clients, Resource limits, Timeouts, Async support)
  - Official docs: FastAPI (Lifespan events)
---

## TL;DR
An AI service is mostly an HTTP client: it calls model providers, vector stores and internal tools.
Creating an `httpx.AsyncClient` per request throws away the connection pool, so every call pays DNS,
TCP and TLS setup and leaves sockets churning. Create one client per worker in the lifespan, share
it through `app.state` or a dependency, configure its limits and timeouts deliberately, and close it
on shutdown.

## Why it exists
A request to a hosted LLM over HTTPS starts with a DNS lookup, a TCP handshake and a TLS handshake
before a single byte of the prompt is sent. On a reused keep-alive connection all of that is skipped.
How much it saves depends on network distance and TLS version, but it is a fixed tax of one or more
round trips on every call, and agent loops make many calls per user request.

The naive pattern is `async with httpx.AsyncClient() as c: await c.post(...)` inside the endpoint. It
looks tidy and is correct in a script. In a server it has three costs. Every request opens fresh
connections, so the setup tax is paid every time and the provider sees a stream of new TLS sessions.
Closed connections linger in `TIME_WAIT`, so under load a host can run short of ephemeral ports or
file descriptors. And the pool limits that would have capped concurrent connections are recreated per
request, so they cap nothing: 500 concurrent requests open 500 connections.

The other naive pattern, a module-level client created at import time, shares connections but has its
own trap: the client is created outside any lifecycle, is never closed cleanly, and can end up bound to
a different event loop than the one serving requests, which shows up as confusing errors in tests.

## How it works
### What the client actually holds
An `AsyncClient` wraps a connection pool (in `httpcore`). Each pooled connection is an open socket to
one origin (scheme, host, port), already through TLS. When you send a request the pool hands back an
idle connection to that origin if one exists, otherwise opens a new one if under the limit, otherwise
waits. After the response is read the connection returns to the pool, *because* HTTP/1.1 keep-alive
lets one socket carry many sequential requests.

### Limits, and why you set them
`httpx.Limits` has three knobs: `max_connections` (total open sockets), `max_keepalive_connections`
(idle sockets kept warm) and `keepalive_expiry` (how long an idle one is kept). The defaults are modest
(100 total, 20 keep-alive at the time of writing), sized for general use, not for a streaming LLM
service where each in-flight generation pins a connection for tens of seconds. Set `max_connections`
from the concurrency you intend per worker, *because* the pool is then a real ceiling: when it is full,
callers wait, and that wait is bounded by the pool timeout.

### Timeouts are four numbers
`httpx.Timeout` separates `connect`, `read`, `write` and `pool`. The default is five seconds for each,
which is too short for a non-streaming completion that may legitimately take a minute. For a streaming
call, `read` is the maximum gap between chunks, not total duration, so a generous total with a tight
read timeout catches stalled streams. `pool` is how long to wait for a free connection; a
`PoolTimeout` under load is your signal that concurrency exceeds the pool, not that the provider is
down.

### Lifecycle
Create the client in the lifespan, store it on `app.state`, expose it through a dependency, and call
`await client.aclose()` after `yield`, *because* closing drains the pool and releases sockets
cleanly on shutdown. Each Uvicorn or Gunicorn worker is a separate process with its own lifespan, so
"one client" means one per worker; total connections to a provider are `workers × max_connections`.

### Provider SDKs
The official OpenAI and Anthropic Python SDKs are built on httpx and keep their own pool per SDK
client object. The same rule applies: construct the SDK client once in the lifespan, not per request.
Both accept an `http_client=` argument if you want to supply a configured `httpx.AsyncClient` with
your own limits, proxies or transport.

### HTTP/2
With the optional `h2` dependency installed and `http2=True`, many concurrent requests multiplex over
one connection per origin. That reduces socket count, but a single TCP connection can then become the
bottleneck, and providers differ in support, so treat it as something to measure, not a default.

## Example
One client per worker, created in the lifespan with explicit limits and timeouts, injected as a
dependency and reused by every request.

```python
import os
from contextlib import asynccontextmanager
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(
        base_url="https://llm.internal.example",
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=32,
                            keepalive_expiry=30.0),
        timeout=httpx.Timeout(connect=3.0, read=60.0, write=10.0, pool=2.0),
        headers={"Authorization": f"Bearer {os.environ['LLM_TOKEN']}"},
    )
    yield
    await app.state.http.aclose()   # drain the pool on shutdown

app = FastAPI(lifespan=lifespan)

def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http

@app.post("/complete")
async def complete(body: dict, http: httpx.AsyncClient = Depends(get_http)):
    try:
        r = await http.post("/v1/complete", json=body)
        r.raise_for_status()
    except httpx.PoolTimeout:
        # pool exhausted: shed load with a retryable status instead of queueing forever
        raise HTTPException(503, "upstream busy", headers={"Retry-After": "2"})
    return r.json()
```

With four workers this process group holds at most 4 × 64 = 256 connections to the provider. In a
load test, the per-request version would show a TLS handshake on every call in a trace; the shared
version shows them only while the pool warms up.

## Trade-offs
- A shared pool removes per-call handshake cost and caps sockets, but becomes a contention point; if
  the pool is smaller than real concurrency, requests queue on `PoolTimeout` instead of failing fast.
- Larger `max_connections` raises throughput to a provider until you hit the provider's own concurrency
  or rate limit, at which point extra connections only add 429s; size from the provider's limit.
- One client per upstream (LLM, vector store, internal tools) isolates pools and timeouts per
  dependency; one client for everything is simpler but lets a slow upstream starve the others.
- Long `keepalive_expiry` keeps connections warm for bursty traffic, but idle sockets can be silently
  dropped by NATs or load balancers, so the first reuse fails and must be retried.

## Pitfalls
- Creating `AsyncClient()` inside the endpoint; traces show a TLS handshake on every call and the host
  accumulates sockets in `TIME_WAIT` under load.
- Never closing the client; shutdown logs warnings about unclosed connections and graceful restarts
  leave sockets half-open.
- Leaving the default five-second timeout on a non-streaming completion; long prompts fail with
  `ReadTimeout` even though the provider would have answered.
- Creating the client at import time and reusing it across test functions; tests fail with event-loop
  errors such as a future attached to a different loop.
- Constructing a new OpenAI or Anthropic SDK client per request, which hides the same per-request pool
  problem inside a library call that looks cheap.

## When to use
Always, in any FastAPI service that makes outbound HTTP calls: one client (or one SDK client) per
upstream per worker, created in the lifespan. The decisions are the numbers, not whether: pool size
from intended concurrency and the provider's limits, timeouts per call type, and whether to separate
clients per upstream. Measure connection count to each upstream, `PoolTimeout` rate, and the share of
latency spent in connect and TLS in traces. In interviews this is the standard probe for whether a
candidate understands application-scoped resources and connection reuse.

## Role lens
- **Engineer:** Moves client construction into the lifespan, injects it as a dependency, sets limits
  and per-call timeouts, and handles `PoolTimeout` explicitly.
- **Architect:** Sets the connection budget per upstream across workers and replicas so the fleet
  stays within provider limits and egress constraints.
- **FDE:** Configures proxies, custom CA bundles and egress allow-lists in the customer's network on the
  shared client, once, instead of patching every call site.

## Check yourself
**Q:** A service with 8 workers, each holding a shared client with `max_connections=100`, starts
seeing 429s from its LLM provider whose limit is 200 concurrent requests, plus `PoolTimeout` errors
on one busy worker. Explain both symptoms and what to change.
**A:** The fleet can open 8 × 100 = 800 concurrent connections, four times the provider's 200, so the
provider rejects the excess with 429. Meanwhile load is uneven, so one worker fills its own pool and
callers wait past the pool timeout. The pool limit is per worker and does not see the global budget.
Set per-worker concurrency to about 200 / 8 = 25 via a semaphore or pool size, retry 429s with
backoff, return 503 fast instead of queueing when the pool is full, and use a shared limiter (for
example in Redis) if replicas scale dynamically.

## Quiz
### Q: What is the main cost of creating a new `httpx.AsyncClient` inside each request handler?
- [ ] httpx refuses to send more than one request per client
- [x] Each request discards the connection pool and pays DNS, TCP and TLS setup again
- [ ] The client blocks the event loop while it is constructed
- [ ] Response bodies cannot be streamed from a short-lived client
> The pool is what makes keep-alive reuse possible. A per-request client starts empty and is thrown
> away, so every call pays connection setup and pool limits cap nothing across requests.

### Q: Your streaming completions fail with `ReadTimeout` after five seconds even though tokens keep arriving slowly. What is the most likely cause?
- [ ] The pool timeout is too short
- [ ] HTTP/2 is disabled
- [x] The default read timeout is five seconds and the gap between chunks sometimes exceeds it
- [ ] The keep-alive expiry closed the connection mid-stream
> In httpx the read timeout applies to each wait for data. A slow model pause between chunks above five
> seconds trips it. Pool timeout concerns waiting for a free connection, not reading from one.

### Q: In a Gunicorn deployment with 6 Uvicorn workers, each creating one shared client in its lifespan with `max_connections=50`, what is the maximum number of connections to one upstream?
- [ ] 50, because the client is shared
- [ ] 56
- [x] 300, because each worker process has its own client and pool
- [ ] Unlimited, because limits apply per request
> Lifespan runs once per worker process, so there are six independent pools. Shared means shared within
> a process. Fleet-wide budgets need per-worker sizing or a distributed limiter.
