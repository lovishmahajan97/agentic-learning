---
id: shared-httpx-asyncclient
module: fastapi-for-ai
name: Shared httpx AsyncClient
kind: pattern
difficulty: core
est_minutes: 13
status: crisp
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
An AI service is mostly an HTTP client. A per-request `httpx.AsyncClient` discards the connection pool,
so every call pays DNS, TCP and TLS setup. Create one client per worker in the lifespan, set limits and
timeouts deliberately, share it, close it on shutdown.

## Why it exists
- **Connection setup is a tax on every cold call.** A request to a hosted LLM over HTTPS starts with a DNS
  lookup, a TCP handshake and a TLS handshake before a byte of the prompt is sent; a reused keep-alive
  connection skips all of it. TCP costs one round trip, TLS 1.3 one more (TLS 1.2 two), plus DNS if not
  cached.
- **It compounds in agent loops.** With an illustrative 40 ms round trip, setup is roughly 80–120 ms
  before the request is sent; ten sequential calls pay it ten times, about a second of pure setup per
  user request. Building a client also builds an SSL context and loads the CA bundle, synchronous CPU
  work on the event loop.
- **`async with httpx.AsyncClient() as c: await c.post(...)` inside the endpoint costs three ways.**
  It is correct in a script, but in a server every request opens fresh connections (the provider sees a
  stream of new TLS sessions); closed connections linger in `TIME_WAIT`, so a host can run short of
  ephemeral ports or file descriptors; and pool limits are recreated per request, so they cap nothing:
  500 concurrent requests open 500 connections.
- **A module-level client at import time has its own trap.** It shares connections, but it is created
  outside any lifecycle, is never closed cleanly, and can end up bound to a different event loop than the
  one serving requests, which shows up as confusing errors in tests.

## How it works
### What the client actually holds
- **A connection pool** (in `httpcore`). Each pooled connection is an open socket to one origin (scheme,
  host, port), already through TLS.
- **On send,** the pool:
  1. hands back an idle connection to that origin if one exists;
  2. otherwise opens a new one if under the limit;
  3. otherwise waits.
- **After the response is read** the connection returns to the pool, *because* HTTP/1.1 keep-alive lets
  one socket carry many sequential requests.
- **`max_connections` caps the whole client,** across every origin it talks to, not each origin
  separately.

### Streams hold a connection until closed
- **A connection returns only when the body is fully read or the response is closed.**
- **`await client.post(...)`** reads the body for you.
- **`async with client.stream("POST", ...) as r:`** closes it when the block exits.
- **`client.send(req, stream=True)`** hands you a response you must `await r.aclose()` yourself,
  including on error and cancellation paths.
- **Why it matters most for `StreamingResponse` relays:** a stream abandoned mid-way (the client
  disconnected, an exception in your generator) and never closed keeps its connection checked out. Leak
  `max_connections` of them and every later call waits for a free connection and fails with
  `PoolTimeout`, while the provider is healthy.

### Limits, and why you set them
| `httpx.Limits` knob | Meaning | Default (at the time of writing) |
|---|---|---|
| `max_connections` | Total open sockets | 100 |
| `max_keepalive_connections` | Idle sockets kept warm | 20 |
| `keepalive_expiry` | How long an idle one is kept, in seconds | 5.0 |

- **The defaults are modest,** sized for general use, not for a streaming LLM service where each in-flight
  generation pins a connection for tens of seconds.
- **Set `max_connections` from the concurrency you intend per worker,** *because* the pool is then a real
  ceiling: when it is full, callers wait, and that wait is bounded by the pool timeout.

### Timeouts are four numbers
`httpx.Timeout` separates `connect`, `read`, `write` and `pool`; the default is five seconds for each.
- **Too short for non-streaming completions,** which may legitimately take a minute.
- **For streaming, `read` is the maximum gap between chunks,** not total duration.
- **httpx has no overall deadline of its own.** Pair a tight `read` timeout (catches stalled streams) with
  an outer `asyncio.timeout(...)` or `asyncio.wait_for(...)` around the whole call if you need a cap on
  total generation time.
- **`pool` is how long to wait for a free connection.** A `PoolTimeout` under load is your signal that
  concurrency exceeds the pool, not that the provider is down.

### Lifecycle
1. Create the client in the lifespan.
2. Store it on `app.state` and expose it through a dependency.
3. Call `await client.aclose()` after `yield`, *because* closing drains the pool and releases sockets
   cleanly on shutdown.
- **"One client" means one per worker.** Each Uvicorn or Gunicorn worker is a separate process with its
  own lifespan, so total connections to a provider are `workers × max_connections`.

### Provider SDKs
- **Same rule:** the official OpenAI and Anthropic Python SDKs are built on httpx and keep their own pool
  per SDK client object, so construct the SDK client once in the lifespan, not per request.
- **Bring your own client:** both accept an `http_client=` argument if you want to supply a configured
  `httpx.AsyncClient` with your own limits, proxies or transport.

### HTTP/2
- **With the optional `h2` dependency and `http2=True`,** many concurrent requests multiplex over one
  connection per origin, which reduces socket count.
- **But measure it:** a single TCP connection can then become the bottleneck, and providers differ in
  support, so it is not a default.

## Diagram
One client lives for the whole worker: the lifespan builds it, every request borrows a pooled
connection, and a connection goes back to the pool only when its body is read or the response is closed.

```mermaid
flowchart TD
    LS["Lifespan builds one AsyncClient"] --> P[("Connection pool per worker")]
    RQ["Request handler via Depends"] --> Q{"Idle connection to this origin?"}
    P --> Q
    Q -->|"yes"| SEND["Send on warm socket, no DNS, TCP or TLS"]
    Q -->|"no, under max_connections"| NEW["Open new socket: DNS, TCP, TLS"]
    Q -->|"no, pool full"| WAIT["Wait up to the pool timeout"]
    WAIT -->|"timeout"| E["PoolTimeout, map to 503 with Retry-After"]
    NEW --> SEND
    SEND --> RD["Body read or response aclose"]
    RD -->|"return connection"| P
    P -.->|"after yield"| CL["Shutdown: await client.aclose"]
```

## Examples
### One client per worker, injected as a dependency
Created in the lifespan with explicit limits and timeouts, and reused by every request.

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

- **Fleet budget:** with four workers this process group holds at most 4 × 64 = 256 connections to the
  provider.
- **In a load-test trace,** the per-request version shows a TLS handshake on every call; the shared
  version shows them only while the pool warms up.

### Relaying a provider stream without leaking connections
`send(..., stream=True)` keeps the connection checked out until `aclose()`, so the relay closes it in a
`finally` and on the early error path.

```python
from fastapi.responses import StreamingResponse

@app.post("/stream")
async def stream(body: dict, http: httpx.AsyncClient = Depends(get_http)):
    req = http.build_request("POST", "/v1/complete", json={**body, "stream": True})
    r = await http.send(req, stream=True)
    if r.status_code >= 400:
        await r.aclose()                  # error path: release before raising
        raise HTTPException(502, "upstream error")

    async def relay():
        try:
            async for chunk in r.aiter_raw():
                yield chunk
        finally:
            await r.aclose()              # completion, upstream error, or generator closed early

    return StreamingResponse(relay(), media_type=r.headers.get("content-type", "text/event-stream"))
```

- **One gap remains:** a generator's `finally` runs only once the generator has started. If the client
  disconnects before the first chunk is pulled, `relay()` never starts and `r` stays open, so add a
  close-once guard that cleanup outside the generator can also call (see cancellation on client
  disconnect), and alert on connections that stay checked out.

### Worked numbers: setup tax in an agent loop
Illustrative 40 ms round trip, TLS 1.3, ten sequential calls per user request; the range covers DNS
cached (0 ms) or not (one round trip).

| Step | Per call | Ten calls |
|---|---|---|
| DNS (only if uncached) | ~0–40 ms | ~0–400 ms |
| TCP handshake | 40 ms | 400 ms |
| TLS 1.3 handshake | 40 ms | 400 ms |
| Fresh client total | ~80–120 ms | ~0.8–1.2 s |
| Shared, warm pool | ~0 ms | ~0 ms |

## Production patterns
- **One client per upstream per worker.** When a service calls an LLM, a vector store and internal tools,
  give each its own `AsyncClient` in the lifespan with its own `Limits` and `Timeout`, so a slow upstream
  cannot starve the others. Monitor `PoolTimeout` counts labelled by upstream.
- **Size the pool from the provider budget.** Set `max_connections` to roughly provider concurrency
  limit ÷ (workers × replicas), so `workers × max_connections` stays under it. Monitor 429 rate and
  connection count to each upstream.
- **Fail fast on pool exhaustion.** Keep `pool` short (2 seconds in the example) and map `PoolTimeout` to
  503 with `Retry-After`, as above, instead of letting requests queue. Monitor the 503 rate against
  in-flight requests.
- **Keep-alive shorter than the path's idle timeout.** Set `keepalive_expiry` below the shortest idle
  timeout of any NAT, proxy or load balancer between you and the upstream, so pooled sockets are retired
  before they are silently dropped. Monitor connection-reset errors on first reuse.
- **Configure SDKs through `http_client=`.** Pass a lifespan-built `httpx.AsyncClient` to the OpenAI or
  Anthropic SDK client so proxies, CA bundles and limits live in one place. Monitor connect and TLS
  spans in traces, which should only appear during warm-up.

## Trade-offs
- **Shared pool vs contention.** It removes per-call handshake cost and caps sockets, but becomes a
  contention point; if the pool is smaller than real concurrency, requests queue on `PoolTimeout`
  instead of failing fast.
- **More connections vs provider limits.** Larger `max_connections` raises throughput until you hit the
  provider's own concurrency or rate limit, after which extra connections only add 429s; size from the
  provider's limit.
- **One client per upstream vs one for everything.** Separate clients isolate pools and timeouts per
  dependency (LLM, vector store, internal tools); one client is simpler but lets a slow upstream starve
  the others.
- **Long vs short `keepalive_expiry`.** Long expiry keeps connections warm for bursty traffic, but idle
  sockets can be silently dropped by NATs or load balancers, so the first reuse fails and must be
  retried; keeping the expiry below the shortest idle timeout on the path flips the trade towards
  shorter values.

## Pitfalls
- **Creating `AsyncClient()` inside the endpoint.** Symptom: traces show a TLS handshake on every call and
  the host accumulates sockets in `TIME_WAIT` under load. Fix: lifespan client.
- **Never closing the client.** Symptom: shutdown logs warnings about unclosed connections and graceful
  restarts leave sockets half-open. Fix: `aclose()` after `yield`.
- **Default five-second timeout on a non-streaming completion.** Symptom: long prompts fail with
  `ReadTimeout` even though the provider would have answered. Fix: set `read` per call type.
- **Client created at import time and reused across test functions.** Symptom: event-loop errors such as
  a future attached to a different loop. Fix: create it in the lifespan.
- **A new OpenAI or Anthropic SDK client per request.** It hides the same per-request pool problem inside
  a library call that looks cheap. Fix: construct once in the lifespan.
- **Streaming with `send(..., stream=True)` and not closing on every path.** Symptom: `PoolTimeout` after
  a burst of cancelled or failed streams, with idle CPU and a healthy upstream, clearing only on restart.
  Fix: `async with client.stream(...)`, or `aclose()` in a `finally` and on every early error path.

## When to use
- **Always,** in any FastAPI service that makes outbound HTTP calls: one client (or one SDK client) per
  upstream per worker, created in the lifespan.
- **Decide the numbers, not whether:** pool size from intended concurrency and the provider's limits,
  timeouts per call type, and whether to separate clients per upstream.
- **Measure** connection count to each upstream, `PoolTimeout` rate, and the share of latency spent in
  connect and TLS in traces.
- **Interviews:** the standard probe for whether a candidate understands application-scoped resources
  and connection reuse.

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

## Interview questions
### Why should a FastAPI service share one httpx AsyncClient instead of creating one per request?
- **The client owns the connection pool.** A per-request client starts empty and is thrown away, so every
  call pays DNS, a TCP handshake and a TLS handshake again, about 80–120 ms at an illustrative 40 ms round trip.
- **Limits only work on a shared pool.** Recreated per request, `max_connections` caps nothing, so 500
  concurrent requests open 500 connections and leave sockets in `TIME_WAIT`.
- **Lifecycle matters too:** create it in the lifespan, close it with `aclose()` after `yield`.

### A streaming relay starts returning PoolTimeout after a burst of cancelled requests, while the provider is healthy. How do you debug it?
- **Suspect leaked streams.** A response from `client.send(req, stream=True)` keeps its connection checked
  out until `aclose()`, so streams abandoned on disconnect or error never return to the pool.
- **Confirm it:** connection count stays at `max_connections` with idle CPU, and the errors clear only on
  restart.
- **Fix:** use `async with client.stream(...)`, or close in a `finally` inside the generator and on every
  early error path before raising.

### After quiet periods, the first call to the provider sometimes fails with a connection reset. Why?
- **A pooled idle socket was dropped silently.** A NAT, proxy or load balancer on the path closed it after
  its own idle timeout, but the pool still held it, so the first reuse hit a dead connection.
- **Fix the timing:** set `keepalive_expiry` below the shortest idle timeout on the path, so the pool retires
  sockets before the network does.
- **Then make reuse failures safe:** retry idempotent calls once on a connect or reset error, and watch
  connection-reset counts on first reuse to confirm the fix.

### When would you not use the default httpx timeouts, and how would you set them for streaming?
- **The default is five seconds for each of `connect`, `read`, `write` and `pool`,** which is too short for a
  non-streaming completion that may legitimately take a minute.
- **For streaming, `read` is the maximum gap between chunks,** not total duration, so set it to the longest
  pause you tolerate between tokens.
- **httpx has no overall deadline,** so wrap the call in `asyncio.timeout(...)` or `asyncio.wait_for(...)` if
  total generation time needs a cap. Keep `pool` short so exhaustion fails fast.

### Would you enable HTTP/2 to cut socket count to your LLM provider?
- **It can help:** with the optional `h2` dependency and `http2=True`, many concurrent requests multiplex
  over one connection per origin, reducing sockets.
- **But it is not a default,** because a single TCP connection can become the bottleneck and providers
  differ in support.
- **So measure it** under realistic streaming load, comparing throughput, tail latency and error rates
  against an HTTP/1.1 pool before switching.

## Quiz
### Q: What is the main cost of creating a new `httpx.AsyncClient` inside each request handler?
- [ ] httpx refuses to send more than one request per client
- [x] Each request discards the connection pool and pays DNS, TCP and TLS setup again
- [ ] The client blocks the event loop while it is constructed
- [ ] Response bodies cannot be streamed from a short-lived client
> The pool is what makes keep-alive reuse possible. A per-request client starts empty and is thrown
> away, so every call pays connection setup and pool limits cap nothing across requests. Construction
> does do some synchronous work (building an SSL context), but that is a secondary cost, not the main one.

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
