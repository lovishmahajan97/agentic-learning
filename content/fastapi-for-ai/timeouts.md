---
id: timeouts
module: fastapi-for-ai
name: Timeouts
kind: practice
difficulty: core
est_minutes: 16
status: crisp
related:
  - fastapi-for-ai/shared-httpx-asyncclient
  - fastapi-for-ai/cancellation-on-client-disconnect
  - fastapi-for-ai/idempotency-keys
  - fastapi-for-ai/concurrency-limits
  - fastapi-for-ai/streamingresponse
  - serving-inference/timeouts
  - serving-inference/retry-strategies
  - python-for-ai/async-cancellation-and-exception-propagation
sources:
  - Official docs: HTTPX (Timeouts)
  - Official docs: Python asyncio (Timeouts)
  - Official docs: Uvicorn (Settings)
---

## TL;DR
FastAPI and uvicorn impose no per-request time limit, so a slow LLM call holds a connection and a
concurrency slot indefinitely. Set phase timeouts (connect, read, total) on every outbound call, one
overall request deadline, and make the innermost layer fire first.

## Why it exists
- **An LLM endpoint is mostly waiting.** On the model provider, the vector store, a tool's API. Most
  calls finish in a second or two, some take ten times longer, and a few never finish because a TCP
  connection silently died or a provider queue stalled.
- **Stuck requests hold resources.** With no timeout, each keeps its socket, its coroutine and any
  semaphore or pool slot. Under load the slots run out, healthy requests queue behind dead ones, and one
  slow dependency becomes a full outage: the classic cascading failure.
- **"The load balancer has a 60-second timeout" protects the client, not you.** When the proxy returns
  504, your handler keeps running and keeps paying for tokens nobody will read.
- **"Use the SDK default" is often worse.** LLM SDKs ship generous defaults (several minutes is common;
  check yours) because long generations are legitimate, far longer than any user will wait.
- **"One big timeout around everything" is too blunt.** It can't tell a provider that won't accept the
  connection (retry elsewhere right away) from one that is mid-stream (maybe keep waiting). You need
  several timeouts with different jobs that agree with each other.

## How it works
### The layers, outside in
| # | Layer | What it bounds |
|---|---|---|
| 1 | **Client / browser** | how long the caller waits before giving up |
| 2 | **Load balancer / reverse proxy** | idle or read timeout (AWS ALB defaults to 60 s idle; nginx `proxy_read_timeout` defaults to 60 s) |
| 3 | **ASGI server** | uvicorn's `--timeout-keep-alive` closes idle keep-alive connections; `--timeout-graceful-shutdown` bounds draining |
| 4 | **Application deadline** | one budget for the whole request, via `asyncio.timeout()` (Python 3.11+) or `asyncio.wait_for` |
| 5 | **Per-call timeouts** | each outbound HTTP client, database driver and queue call |

- **Proxy "idle" means no bytes.** A stream that pauses longer than the idle timeout gets cut.
- **The ASGI server does not limit request duration.** Neither uvicorn setting caps how long a request
  may run. Gunicorn's `--timeout` is a worker-liveness check, not a request deadline, and with async
  workers it doesn't fire just because one request is slow.
- **Inner timeouts must be shorter than outer ones.** If the proxy gives up at 60 s and your handler at
  90 s, your error handling never runs: the user sees a generic proxy 504 and your coroutine keeps
  working for nobody.
- **Set your deadline a few seconds under the proxy's**, so you fail with your own error body and log line.

### Phase timeouts in httpx
`httpx.Timeout` has four independent limits, because each failure mode needs its own:

| Phase | Bounds | Guidance |
|---|---|---|
| **connect** | TCP and TLS handshake | keep it short (a few seconds): a host that won't connect is down, and failing fast lets you retry or fall back |
| **read** | longest wait for *the next chunk* of bytes, not the whole response | for a stream it is the max gap between tokens: catches a stall, allows a long stream |
| **write** | sending the request body | matters for big prompts or uploads |
| **pool** | waiting for a free connection from the client's pool | when it fires, the cause is your own concurrency, not the provider |

- **Defaults are 5 s for all four.** Too short for a non-streamed completion that thinks for 20 s.
- **No httpx setting caps total duration**, because read is per chunk. That is the application
  deadline's job.

### Deadlines and cancellation
1. `async with asyncio.timeout(t):` cancels the current task when `t` elapses.
2. That raises `CancelledError` at whatever `await` the task is parked on, and converts it into
   `TimeoutError` as it leaves the `async with`.
3. Cancellation propagates into httpx, which closes the connection. For a plain HTTP completion call,
   closing the connection is usually the only stop signal you can send.

- **Whether generation stops is provider-specific.** Whether the provider halts on disconnect, and
  whether tokens generated before the close are billed, depends on the provider. Self-hosted servers such
  as vLLM abort on disconnect; some hosted APIs offer an explicit cancel only for background or batch jobs.
- **Never swallow `CancelledError` inside the block**, or the timeout silently stops working.
- **A deadline is a budget.** Compute the time left before each step (`deadline - loop.time()`) and give
  each call the minimum of its own limit and what remains, so a retry can't overrun the request.

### Timeouts and retries
- **A timeout means *you* stopped waiting.** It does not tell you whether the server did the work: a
  non-streamed completion that timed out on read may have finished and been billed.
- **Retry only idempotent calls**, or calls that carry an idempotency key, and only within the
  remaining deadline, with backoff.
- **Watch for retries you did not write.** Provider SDKs often retry internally (the OpenAI and
  Anthropic Python SDKs default to two retries, with a timeout measured in minutes; check the version
  you run).
- **Worst case for one call** is roughly `(retries + 1) × per-attempt timeout + total backoff`. Either
  pass the SDK a timeout derived from the remaining budget and `max_retries=0` and retry yourself, or
  make sure that product still fits.

### Streaming needs three limits, not one
1. **Time to first token.** The provider accepted the request but is queueing or thinking. Bound it by
   timing the wait for the first chunk (an `asyncio.timeout` around the first `anext`), because the right
   number (seconds) is very different from the inter-token gap (sub-second normally).
2. **Gap between chunks.** httpx's `read` timeout, which catches a stalled stream.
3. **Total duration.** An application deadline, because neither of the above caps it.

- **After the 200, a timeout cannot become a 504.** Once your own `StreamingResponse` has sent its 200,
  the timeout must be emitted as an in-band error event and the stream closed.
- **So do the cheap-to-fail work first.** Everything that can time out cheaply (retrieval, opening the
  upstream stream) should happen before the response starts.

### The timeouts people forget
- **Postgres:** a `statement_timeout` (or the driver's command timeout), so a bad query plan can't hold
  a pool connection for minutes.
- **Redis:** a socket timeout on the client.
- **Pools and semaphores:** a timeout on acquiring a pool connection or a semaphore slot.
- **Queues:** a deadline on queue `get` calls.
- **Service-to-service:** pass the remaining budget along so the downstream service doesn't start work
  its caller has already abandoned. gRPC does this natively with deadlines; over HTTP a header carrying
  the remaining milliseconds works, relative so that clock skew between hosts doesn't matter.

## Diagram
Follow one request inward: each layer's limit is shorter than the one outside it, and every call takes
the smaller of its own limit and the budget left.

```mermaid
flowchart TD
    C["Client waits"] --> P["Proxy read or idle timeout, e.g. 60 s"]
    P --> A["App deadline via asyncio.timeout, a few s under the proxy"]
    A --> R["Retrieval call: min of 1.5 s and budget left"]
    R --> G["LLM call: phase timeouts, read capped by budget left"]
    G -->|"answers in time"| OK["200 response"]
    G -->|"connect fails fast"| E3["Your 503 with Retry-After"]
    G -->|"read or deadline fires"| E4["Your 504, socket closed, upstream cancelled"]
    P -.->|"if app has no deadline"| X["Anonymous proxy 504, handler keeps paying"]
```

## Examples
### Chat endpoint with an 8-second budget behind a 10-second proxy
The budget is split between retrieval and generation.

```python
import asyncio, httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request

REQUEST_BUDGET = 8.0          # proxy read timeout is 10 s; we must fail first

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=6.0, write=5.0, pool=1.0)) as c:
        app.state.http = c
        yield

app = FastAPI(lifespan=lifespan)

def remaining(deadline: float) -> float:
    return max(0.0, deadline - asyncio.get_running_loop().time())

@app.post("/chat")
async def chat(req: Request, body: dict):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + REQUEST_BUDGET
    http: httpx.AsyncClient = req.app.state.http
    try:
        async with asyncio.timeout(REQUEST_BUDGET):
            docs = await http.post("http://search/q", json=body,
                                   timeout=min(1.5, remaining(deadline)))
            # generation gets whatever retrieval left over
            r = await http.post("http://llm/v1/chat", json={"docs": docs.json(), **body},
                                timeout=httpx.Timeout(remaining(deadline), connect=2.0))
            r.raise_for_status()
            return r.json()
    except (TimeoutError, httpx.TimeoutException):
        raise HTTPException(504, "upstream model did not answer in time")
    except httpx.ConnectError:
        raise HTTPException(503, "model provider unreachable", headers={"Retry-After": "5"})
```

| Moment | What happens |
|---|---|
| 0.4 s | retrieval returns; the generation call is built with about 7.6 s on every phase |
| 8 s | whichever fires first (httpx's 7.6 s read timeout or the outer `asyncio.timeout`) ends the call and closes the socket |
| ~8 s | both paths map to *your* 504 with a clear message |
| 10 s | the proxy would have returned an anonymous 504; you answered two seconds earlier |

The outer deadline is the backstop: it also covers time spent in code between calls, which no per-call
timeout sees.

### Read is a gap limit, the deadline is a total limit (runnable, no network)
A fake stream sends a token every 0.3 s. A per-chunk limit (like httpx `read`) never trips; only the
total deadline stops it. Requires Python 3.11+.

```python
import asyncio

class StreamStalled(Exception):
    pass

async def fake_stream(gap: float, n: int):
    for i in range(n):
        await asyncio.sleep(gap)                  # provider sends one token every `gap` s
        yield f"tok{i}"

async def with_gap_limit(stream, gap_limit: float):
    it = stream.__aiter__()
    while True:
        try:
            yield await asyncio.wait_for(anext(it), gap_limit)   # per-chunk cap, like httpx read
        except StopAsyncIteration:
            return
        except TimeoutError:
            raise StreamStalled(f"no chunk for {gap_limit}s") from None

async def consume(gap_limit: float, total: float) -> str:
    got = 0
    try:
        async with asyncio.timeout(total):        # total cap: the application deadline
            async for _ in with_gap_limit(fake_stream(gap=0.3, n=100), gap_limit):
                got += 1
        return f"finished: {got} chunks"
    except StreamStalled as e:
        return f"stalled after {got} chunks ({e})"
    except TimeoutError:
        return f"deadline hit after {got} chunks"

print(asyncio.run(consume(gap_limit=1.0, total=2.0)))  # deadline hit after 6 chunks
print(asyncio.run(consume(gap_limit=0.1, total=2.0)))  # stalled after 0 chunks (no chunk for 0.1s)
```

### Worked scenario: the hidden retry multiplier
Illustrative numbers for one call during a provider incident, using the worst-case formula above.

| Setup | Per-attempt timeout | Attempts | Backoff total | Worst case |
|---|---|---|---|---|
| Your loop (2 retries) around an SDK that also retries 2 times | 20 s | 3 × 3 = 9 | ~3 s | ~183 s |
| SDK with `max_retries=0`, your loop with 2 retries | 20 s | 3 | ~1.5 s | ~61.5 s |
| Same, but each attempt gets `min(20, remaining)` from an 8 s budget | ≤ 8 s total | as many as fit | inside budget | 8 s |

Only the last row respects a request deadline; the first also sends three times the upstream requests
of the second.

## Production patterns
- **Deadline under the proxy** — for every HTTP route. Set the app budget a few seconds below the proxy
  read or idle timeout (for example 27 s under a 30 s proxy) with `asyncio.timeout`. Monitor
  proxy-generated 504s versus app 504s; any proxy 504 means the ordering is wrong.
- **SDK calls driven by the remaining budget** — whenever you call a provider SDK. Pass the remaining
  seconds as the SDK's `timeout` and `max_retries=0`, then retry yourself only while budget remains.
  Monitor upstream request count divided by your request count; it should stay near 1 outside incidents.
- **Phase-labelled timeout metrics** — on every dependency. Count `httpx.ConnectTimeout`,
  `httpx.ReadTimeout`, `httpx.WriteTimeout` and `httpx.PoolTimeout` separately per dependency, because a
  pool timeout points at your concurrency while a connect timeout points at the provider. Alert on
  rising rates.
- **Database timeouts at the pool** — for any SQL dependency. Set Postgres `statement_timeout` per
  connection (asyncpg accepts it through `server_settings`, and `command_timeout` bounds the client side).
  Monitor pool wait time and statements cancelled by timeout.
- **Streaming-aware proxy settings** — for SSE or chunked routes. Raise the proxy read or idle timeout
  only on streaming routes, disable buffering there (nginx `proxy_buffering off`), and send periodic SSE
  comment lines (`: ping`) so idle timers see bytes. Monitor streams ending without a final event.
- **Budget propagation between services** — for internal fan-out. Send remaining milliseconds in a
  request header of your choosing and have the callee start its own deadline from it. Monitor
  downstream work started after the caller's deadline.

## Trade-offs
- **Short vs long timeouts.** Short ones free capacity quickly and surface outages early, but cut off
  legitimately slow requests. The trade flips toward longer limits for long-form generation or reasoning
  models, where a slow answer is normal and should be streamed.
- **Single deadline vs phase timeouts.** One overall deadline is simple and always honoured, but can't
  tell "can't connect" from "slow to answer". Add phase timeouts once you retry or fall back between
  providers.
- **Retry vs duplicate cost.** Retrying after a timeout raises success rates on transient stalls, but can
  double cost and side effects when the first attempt actually finished, so it only pays off with
  idempotent calls.
- **Streaming vs operational complexity.** Streaming keeps a long total time while catching stalls with
  a short read timeout, at the cost of proxy configuration (idle timeouts, buffering) and error handling
  after the 200 is sent.

## Pitfalls
- **Relying on the load balancer's timeout as yours.** Symptom: proxy-generated 504s with no matching
  error in your logs, while provider bills show completions that finished after the client had gone.
- **Treating httpx's `read` as a total limit.** Symptom: a streamed response trickles a token every few
  seconds and runs for minutes, well past what you thought your limit was.
- **Swallowing `CancelledError`.** Catching broad `Exception` (or `BaseException`) inside a timed block.
  Symptom: `asyncio.timeout` "doesn't work"; requests overrun the deadline and finish late.
- **A fresh `httpx.AsyncClient` per request with default timeouts.** Symptom: 5-second `ReadTimeout`
  errors on longer completions, plus connection churn in TLS-handshake latency.
- **Retrying without checking the remaining budget.** Symptom: a p99 latency that is a multiple of the
  configured timeout.
- **Your retry loop around an SDK that still retries internally.** Symptom: upstream request counts
  several times your own during a provider incident, and latencies clustered at multiples of the SDK
  timeout.
- **Timing out the HTTP call but not the database.** Symptom: pool-exhaustion errors on unrelated
  endpoints while slow queries hold connections far past any request deadline.

## When to use
- **Always.** Every outbound call in a FastAPI service needs explicit timeouts, and every request needs a
  deadline shorter than whatever sits in front of it.
- **Write the layer chain down** (client, CDN, proxy, app deadline, per-call) with the number for each,
  and check that the numbers shrink as you go inward.
- **Pick numbers from measured latency:** a per-call timeout a bit above that dependency's observed p99,
  and a request budget from the user experience you promised.
- **Measure:** timeout counts per dependency as a metric, because a rising rate is often the first sign
  of a provider incident.
- **Interviews:** explain why read is not total, why inner timeouts must be shorter, and why a timeout
  makes retries dangerous without idempotency.

## Role lens
- **Engineer:** Configures `httpx.Timeout` phases on the shared client, wraps request work in
  `asyncio.timeout`, threads remaining budget into each call, and maps timeouts to 504.
- **Architect:** Owns the end-to-end timeout budget across CDN, gateway, services and providers, and
  decides which paths stream versus fail fast.
- **FDE:** Finds the customer's corporate proxy or API gateway cutting streams at its own idle timeout
  and negotiates the setting or adds keep-alive events.

## Check yourself
**Q:** Your chat endpoint sits behind a proxy with a 60 s read timeout. Users report occasional generic
504 pages, your logs show nothing, but token spend keeps rising. What is going on and how do you fix it?
**A:** The proxy gives up first. Your handler has no deadline, so it keeps waiting on the provider after
the proxy has returned its own 504. The client is gone, but the generation completes and gets billed,
and because your code never failed, nothing is logged. The fix is a request deadline a few seconds
under 60 s using `asyncio.timeout`, which cancels the upstream call and closes its socket. Map it to
your own 504 with a log line and a metric. Add per-call phase timeouts so connect failures fail in
seconds. Detect client disconnects too. For long answers, stream so the proxy sees bytes, and keep
retries within the remaining budget.

## Interview questions
### Why does a FastAPI service need its own timeouts when the load balancer already has one?
- **The proxy timeout protects the client, not the server.** When it fires, the proxy returns 504 but
  your coroutine keeps running, holding its socket, pool slot and semaphore and paying for tokens.
- **Uvicorn imposes no per-request limit**, so only an application deadline (`asyncio.timeout`) and
  per-call timeouts actually cancel the upstream work and free resources.

### Explain the four httpx timeout phases and which one catches a stalled stream.
- **connect** bounds the TCP and TLS handshake, **write** bounds sending the body, **pool** bounds
  waiting for a free connection, and **read** bounds the wait for the next chunk.
- **A stalled stream trips `read`**, because no chunk arrives within the gap limit.
- **A slow-but-steady stream trips none of them**, because read is per chunk; only an application
  deadline caps total duration.

### p99 latency on your chat endpoint is three times your configured timeout during a provider incident. How do you debug it?
- **Suspect stacked retries.** Compare upstream request counts with your own request count; a ratio well
  above 1 means your loop and the SDK's internal retries are multiplying.
- **Check the budget logic.** Worst case is roughly `(retries + 1) × per-attempt timeout + total
  backoff`; if retries don't use `min(limit, remaining)`, they overrun the deadline.
- **Fix:** set the SDK's `max_retries=0`, pass it the remaining budget as `timeout`, and retry yourself
  only while budget remains.

### When would you choose a long timeout instead of failing fast?
- **For long-form generation or reasoning models**, where a slow answer is normal and the user expects to
  wait; stream it so the proxy sees bytes and a short read timeout still catches stalls.
- **Not for connect**, because a host that won't accept a connection is down; keep connect at a few
  seconds and fail over.
- **The cost** is capacity held per request, so pair long limits with a concurrency cap.

### How do you handle a timeout that fires after a streaming response has already sent its 200?
- **You cannot change the status any more**, because status and headers went out with the first byte.
- **Emit an in-band error event** with a code and close the stream, and record a stream-outcome metric
  separate from HTTP status.
- **Move cheap-to-fail work first** (retrieval, opening the upstream stream, time to first token) so most
  timeouts still map to a real 504.

## Quiz
### Q: An httpx client has `read=10`. A streamed completion sends one token every 3 s for 4 minutes. What happens?
- [ ] It fails after 10 s because the total read time exceeds the limit
- [x] It completes, because the read timeout bounds the gap between chunks, not the total
- [ ] It fails with a pool timeout once the stream passes 10 s
- [ ] It completes only if the write timeout is also raised
> httpx's read timeout applies to each individual read. A chunk every 3 s never exceeds 10 s, so the
> stream runs for minutes. Capping total duration needs an application deadline such as asyncio.timeout.

### Q: The proxy's timeout is 30 s. Which application deadline is best?
- [ ] 60 s, so the application never cuts off a request the proxy would allow
- [ ] 30 s, exactly matching the proxy
- [x] About 27 s, so the application fails first with its own error and cancels upstream work
- [ ] None; the proxy timeout already bounds the request
> Inner limits must be shorter than outer ones. A few seconds of margin lets your handler cancel the
> upstream call, log, and return a meaningful error before the proxy returns its own anonymous 504.

### Q: Why is blindly retrying a non-streamed LLM call after a read timeout risky?
- [ ] Retries are always rejected by LLM providers
- [ ] The read timeout leaves the connection pool permanently exhausted
- [x] The first attempt may have completed and been billed, so the retry can duplicate cost and side effects
- [ ] Retrying resets the asyncio event loop
> A timeout only means the client stopped waiting. The server may have finished the work, so retries
> need idempotency (keys or naturally idempotent operations) and must fit inside the remaining deadline.
