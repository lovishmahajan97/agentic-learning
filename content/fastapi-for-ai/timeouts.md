---
id: timeouts
module: fastapi-for-ai
name: Timeouts
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
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
FastAPI and uvicorn impose no per-request time limit, so a handler waiting on a slow LLM call will wait
as long as the slowest dependency lets it, holding a connection, memory and a concurrency slot the whole
time. Timeouts turn "hangs forever" into "fails fast with a known error". Set them per phase (connect,
read, total) on every outbound call, give the request one overall deadline, and order the layers so the
innermost one fires first.

## Why it exists
An LLM endpoint is mostly waiting: on the model provider, the vector store, a tool's API. Most calls
finish in a second or two, but some take ten times longer, and a few never finish because a TCP
connection silently died or a provider queue stalled. With no timeout, each stuck request keeps its
socket, its coroutine and any semaphore or pool slot it holds. Under load those slots run out, and
healthy requests queue behind dead ones. A single slow dependency turns into a full outage. That is the
classic cascading failure.

The naive fixes don't hold up. "The load balancer has a 60-second timeout" protects the client, not
your server. When the proxy gives up and returns 504, your handler keeps running and keeps paying for
tokens nobody will read. "Use the SDK default" is often worse. LLM SDKs ship generous defaults (several
minutes is common; check yours) because long generations are legitimate, and that is far longer than
any user will wait. "One big timeout around everything" can't tell a provider that won't accept the
connection (retry elsewhere right away) from one that is mid-stream (maybe keep waiting). You need
several timeouts with different jobs, and they have to agree with each other.

## How it works
### The layers, outside in
1. **Client / browser**: how long the caller waits before giving up.
2. **Load balancer / reverse proxy**: an idle or read timeout (AWS ALB defaults to 60 s idle; nginx
   `proxy_read_timeout` defaults to 60 s). For streaming, "idle" means no bytes, so a stream that
   pauses longer than this gets cut.
3. **ASGI server**: uvicorn's `--timeout-keep-alive` closes idle keep-alive connections, and
   `--timeout-graceful-shutdown` bounds draining. Neither limits how long a request may run. Gunicorn's
   `--timeout` is a worker-liveness check, not a request deadline, and with async workers it doesn't
   fire just because one request is slow.
4. **Application deadline**: one budget for the whole request, enforced with `asyncio.timeout()` (Python
   3.11+) or `asyncio.wait_for`.
5. **Per-call timeouts**: on each outbound HTTP client, database driver and queue call.

The rule is that **inner timeouts must be shorter than outer ones**. If the proxy gives up at 60 s and
your handler at 90 s, your error handling never runs: the user sees a generic proxy 504 and your
coroutine keeps working for nobody. Set your deadline a few seconds under the proxy's so that you fail
with your own error body and log line.

### Phase timeouts in httpx
`httpx.Timeout` has four independent limits, because each failure mode needs its own:
- **connect**: TCP and TLS handshake. Keep it short (a few seconds). A host that won't connect is down,
  and failing fast lets you retry or fall back.
- **read**: the longest wait for *the next chunk* of response bytes, not for the whole response. For
  a streamed completion it is the maximum gap between tokens, so it catches a stalled stream but
  allows a long one.
- **write**: sending the request body (relevant for big prompts or uploads).
- **pool**: waiting for a free connection from the client's pool. When this fires, the cause is your
  own concurrency, not the provider.

httpx defaults all four to 5 s. That is too short for a non-streamed completion that thinks for 20 s.
Because read is per chunk, no httpx setting caps the *total* duration. That is the job of the
application deadline.

### Deadlines and cancellation
`async with asyncio.timeout(t):` cancels the enclosed task when `t` elapses and raises `TimeoutError`
at the `async with`. Cancellation propagates into httpx, which closes the connection. For a plain
HTTP completion call, closing the connection is usually the only stop signal you can send. Whether the
provider actually halts generation on disconnect, and whether tokens generated before the close are
billed, depends on the provider (self-hosted servers such as vLLM abort on disconnect; some hosted APIs
offer an explicit cancel only for background or batch jobs). Two consequences follow. First, code inside
the block must not swallow `CancelledError`, or the timeout silently stops working. Second, a deadline
is a *budget*: compute the time left before each step (`deadline - loop.time()`) and give each call
the minimum of its own limit and what remains, so a retry can't overrun the request.

### Timeouts and retries
A timeout tells you *you* stopped waiting. It does not tell you whether the server did the work. A
non-streamed completion that timed out on read may have finished and been billed. Retrying is safe
only for calls that are idempotent or carry an idempotency key, and retries must fit within the
remaining deadline, with backoff.

## Example
A chat endpoint with an 8-second request budget on a 10-second proxy, split between retrieval and
generation.

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

Walkthrough: retrieval takes 0.4 s, so the generation call is built with about 7.6 s on every phase.
If the model has not answered by the 8 s mark, whichever fires first (httpx's 7.6 s read timeout or the
outer `asyncio.timeout`) ends the call, the socket is closed, and both paths map to *your* 504 with a
clear message at about 8 s, two seconds before the proxy would have returned an anonymous one. The
outer deadline is the backstop: it also covers time spent in code between calls, which no per-call
timeout sees.

## Trade-offs
- Short timeouts free capacity quickly and surface outages early, but they cut off legitimately slow
  requests. That trade flips toward longer limits for long-form generation or reasoning models, where
  a slow answer is normal and should be streamed.
- A single overall deadline is simple and always honoured, but it can't tell "can't connect" from
  "slow to answer". Add phase timeouts once you retry or fall back between providers.
- Retrying after a timeout raises success rates on transient stalls, but it can double cost and side
  effects when the first attempt actually finished, so it only pays off with idempotent calls.
- Streaming lets you keep a long total time while catching stalls with a short read timeout, at the
  cost of proxy configuration (idle timeouts, buffering) and error handling after the 200 is sent.

## Pitfalls
- Relying on the load balancer's timeout as your timeout. The symptom is proxy-generated 504s with no
  matching error in your logs, while provider bills show completions that finished after the client
  had gone.
- Treating httpx's `read` as a total limit. The symptom is a streamed response that trickles a token
  every few seconds and runs for minutes, well past what you thought your limit was.
- Catching broad `Exception` (or `BaseException`) inside a timed block and swallowing
  `CancelledError`. The symptom is `asyncio.timeout` that "doesn't work": requests overrun the
  deadline and finish late.
- Creating a fresh `httpx.AsyncClient` per request with default timeouts. The symptom is 5-second
  `ReadTimeout` errors on longer completions, plus connection churn in TLS-handshake latency.
- Retrying in a loop without checking the remaining budget. The symptom is a p99 latency that is a
  multiple of the configured timeout.

## When to use
Always. Every outbound call in a FastAPI service needs explicit timeouts, and every request needs a
deadline shorter than whatever sits in front of it. Write the layer chain down (client, CDN, proxy,
app deadline, per-call) with the number for each, and check that the numbers shrink as you go inward.
Pick numbers from measured latency: a per-call timeout a bit above that dependency's observed p99, a
request budget from the user experience you promised. Track timeout counts per dependency as a metric,
because a rising rate is often the first sign of a provider incident. In interviews, the expected depth
is: explain why read is not total, why inner timeouts must be shorter, and why a timeout makes retries
dangerous without idempotency.

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
