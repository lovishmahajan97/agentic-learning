---
id: httpx
module: python-for-ai
name: httpx
kind: tool
difficulty: core
est_minutes: 14
related:
  - python-for-ai/asyncio
  - python-for-ai/async-await
  - python-for-ai/tenacity
  - python-for-ai/openai-sdk
  - python-for-ai/test-doubles-mocks-fixtures
  - fastapi-for-ai/shared-httpx-asyncclient
  - fastapi-for-ai/timeouts
  - serving-inference/streaming
sources:
  - Official docs: HTTPX
---

## TL;DR
httpx is a Python HTTP client with a requests-style API that works both synchronously and with
`async`/`await`, supports streaming responses, optional HTTP/2, connection pooling and pluggable
transports for testing. It matters in AI work because LLM calls are slow, concurrent, streamed network
requests, and the official OpenAI and Anthropic Python SDKs are built on it, so its timeout and pooling
behaviour is your SDK's behaviour.

## Why it exists
`requests` defined the ergonomic Python HTTP API, but it is synchronous. An AI service that fans out to
an LLM, a vector store and two tools per user request spends nearly all its time waiting on the network;
with a blocking client inside an async web framework, each wait blocks the event loop and the server
handles one request at a time. Threads work around that but scale poorly to thousands of concurrent
streams. `aiohttp` is async-only, with a different API, so code and tests cannot be shared with sync
scripts.

LLM traffic adds two further needs. Responses are streamed as server-sent events over many seconds, so
the client must expose bytes or lines incrementally rather than buffer the body. And requests are
expensive and long, so timeouts, connection reuse and pool limits stop being defaults you ignore and
become things you must set deliberately. httpx provides one API, `httpx.Client` and
`httpx.AsyncClient`, with identical methods, streaming, explicit fine-grained timeouts and a transport
layer you can swap for a mock in tests.

## How it works
### Clients own connection pools
`httpx.get(...)` is a convenience that opens and discards a connection. A `Client`/`AsyncClient` keeps a
pool of keep-alive connections, so repeated calls to the same host skip TCP and TLS handshakes, often the
largest part of a short request's latency. That is *why* you create one client per process (or per
upstream) and reuse it, closing it on shutdown with a context manager or `aclose()`. Pool size is set by
`httpx.Limits(max_connections=..., max_keepalive_connections=...)`; when the pool is full, further
requests wait for a free connection, and that wait has its own timeout.

### Timeouts are per phase
`httpx.Timeout` separates **connect** (establishing the socket), **read** (waiting for the next chunk
of data), **write** (sending a chunk) and **pool** (waiting for a free connection). The default is 5
seconds for each. Crucially, a read timeout bounds the gap *between* chunks, not the total duration. A
streamed completion that sends a token every 100 ms never trips a 5 s read timeout even if it runs for
ten minutes, while a reasoning model that thinks for 30 s before its first byte trips it immediately. For
a total deadline you wrap the call in `asyncio.timeout(...)` (or anyio's equivalent).

### Streaming
`client.stream("POST", url, json=...)` is a context manager that returns once headers arrive; you then
iterate `aiter_bytes()`, `aiter_text()` or `aiter_lines()`. Leaving the context closes the response and
returns the connection to the pool, *which is why* the context-manager form matters: an abandoned
stream holds its connection until garbage collection.

### Behaviour that differs from requests
- Redirects are **not** followed by default (`follow_redirects=True` to enable).
- Timeouts are on by default; `requests` waits forever unless told otherwise.
- `raise_for_status()` raises `httpx.HTTPStatusError`; network-level failures are `httpx.RequestError`
  subclasses such as `ConnectError`, `ReadTimeout` and `PoolTimeout`.

### Transports
Every client sends through a transport. `httpx.HTTPTransport(retries=n)` retries only connection
failures, not HTTP 429 or 5xx, so application retries with backoff remain your job (Tenacity, or the
SDK's built-in retries). `httpx.MockTransport(handler)` routes requests to a Python function, giving
fast deterministic tests with no network. `httpx.ASGITransport(app=app)` sends requests straight into an
ASGI app such as FastAPI, in-process. HTTP/2 needs the optional extra (`httpx[http2]`) and `http2=True`,
and multiplexes many requests over one connection.

## Example
Streaming an OpenAI-compatible completion with deliberate limits, a total deadline and a mock for tests.

```python
import asyncio, json, httpx

TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=2.0)  # slow first token is normal
LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20)

async def stream_chat(client: httpx.AsyncClient, prompt: str) -> str:
    body = {"model": "my-model", "stream": True,
            "messages": [{"role": "user", "content": prompt}]}
    parts = []
    async with asyncio.timeout(120):                      # total deadline (Python 3.11+)
        async with client.stream("POST", "/v1/chat/completions", json=body) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    delta = json.loads(line[6:])["choices"][0]["delta"]
                    parts.append(delta.get("content") or "")
    return "".join(parts)

def fake(request: httpx.Request) -> httpx.Response:       # test double, no network
    chunk = {"choices": [{"delta": {"content": "hi"}}]}
    return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")

async def main():
    async with httpx.AsyncClient(base_url="https://llm.internal", timeout=TIMEOUT, limits=LIMITS,
                                 transport=httpx.MockTransport(fake)) as client:
        print(await asyncio.gather(*(stream_chat(client, f"q{i}") for i in range(3))))

asyncio.run(main())
```

Drop the `transport=` argument and the same code talks to a real server; the 50-connection cap means a
burst of 200 calls queues inside the pool instead of opening 200 sockets.

## Trade-offs
- One API for sync and async lets scripts, services and tests share code, but async only helps if every
  other call on the path is async too; one blocking call still stalls the loop.
- A shared long-lived client gives pooling and lower latency, at the cost of lifecycle management:
  creating it at startup, closing it at shutdown, and not sharing it across event loops or forked processes.
- Per-phase timeouts let you allow slow first tokens while failing fast on dead hosts, but they never
  express a total budget; long streams need an outer deadline as well.
- HTTP/2 multiplexes many requests over one connection, which helps with many small calls to one host,
  but adds a dependency and can concentrate load on one backend behind a connection-level load balancer.

## Pitfalls
- Creating a new `AsyncClient` per request; every call repeats the TLS handshake, and the symptom is
  higher latency plus "too many open files" or ephemeral-port exhaustion under load.
- Keeping the 5 s default read timeout for LLM calls; long prompts or reasoning models delay the first
  byte, and the symptom is `ReadTimeout` only on big inputs while small ones succeed.
- Assuming the read timeout caps total duration; a slowly trickling stream runs far past your budget,
  which shows up as requests hanging for minutes without any timeout firing.
- Expecting `HTTPTransport(retries=3)` to retry 429 or 503; it only retries connection failures, and the
  symptom is rate-limit errors surfacing straight to users.
- Exiting a `stream()` block early without the context manager; connections leak, and the symptom is
  `PoolTimeout` after a period of normal traffic.

## When to use
Use httpx whenever Python code makes HTTP calls in an AI system: direct calls to model servers, tool
APIs, webhooks, and as the `http_client` you pass to provider SDKs to control proxies, timeouts and
limits. Choose `AsyncClient` inside async frameworks and agents; the sync client is fine for scripts and
batch jobs. Prefer `requests` only in legacy synchronous code where nothing else changes. Measure p50/p99
latency per upstream, timeout counts per phase and pool wait time. In interviews, expect questions on why
a read timeout is not a deadline and why clients must be shared.

## Role lens
- **Engineer:** Creates one shared `AsyncClient` per upstream with explicit timeouts and limits, streams
  with context managers, and tests with `MockTransport` instead of patching.
- **Architect:** Sets the timeout, retry and concurrency budgets for each dependency so that one slow
  model provider cannot exhaust connections for the whole service.
- **FDE:** Configures proxies, custom CA bundles and mutual TLS through the client so SDK traffic works
  inside a customer's locked-down corporate network.

## Check yourself
**Q:** Your service streams completions through httpx with `timeout=30`. Short prompts work; 100k-token
prompts fail with `ReadTimeout`, and occasionally a request hangs for ten minutes. Explain both.
**A:** A single number sets every phase, so read is 30 s between chunks. With a 100k-token prompt the
server's prefill takes longer than 30 s before the first byte, so the read timeout fires. The ten-minute
hang is the opposite case: once tokens flow, each chunk resets the read timer, so a stream that keeps
trickling never trips it. Fix both by raising the read timeout to cover worst-case time to first token,
and wrapping the whole call in `asyncio.timeout` for a total deadline, plus metrics on time to first
byte and total duration per request.

## Quiz
### Q: What does httpx's read timeout actually bound?
- [ ] The total time from sending the request to receiving the full response
- [x] The maximum wait for the next chunk of data from the server
- [ ] Only the time to establish the TCP and TLS connection
- [ ] The time a request waits for a free pooled connection
> Read timeouts reset whenever data arrives, so a slow but steady stream never trips them. Total
> deadlines need an outer timeout; connect and pool waits are separate phases.

### Q: Why should an async service create one shared AsyncClient rather than one per request?
- [ ] AsyncClient objects cannot be garbage collected
- [x] The shared client's pool reuses keep-alive connections, avoiding repeated handshakes and socket exhaustion
- [ ] Per-request clients cannot stream responses
- [ ] httpx raises an error if more than one client exists per process
> Connection pooling lives in the client. A new client per call pays TCP and TLS setup every time and can
> run out of file descriptors under load, while streaming works either way.

### Q: A team sets HTTPTransport(retries=3) and still sees 429 errors reach users. Why?
- [ ] The retries parameter only applies to synchronous clients
- [ ] 429 responses are retried, but without backoff they always fail again
- [x] Transport retries cover connection failures only, not HTTP status codes like 429
- [ ] Retries are disabled when HTTP/2 is enabled
> The transport retries failed connects, not error responses. Retrying on 429 or 5xx with backoff
> and respect for Retry-After belongs in application code or the provider SDK.
