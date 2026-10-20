---
id: async-endpoints
module: fastapi-for-ai
name: Async endpoints
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
related:
  - python-for-ai/async-await
  - python-for-ai/asyncio
  - python-for-ai/gil
  - python-for-ai/asyncio-vs-threads-vs-multiprocessing
  - fastapi-for-ai/shared-httpx-asyncclient
  - fastapi-for-ai/concurrency-limits
  - fastapi-for-ai/cancellation-on-client-disconnect
  - serving-inference/batching-and-concurrency-limits
sources:
  - Official docs: FastAPI
  - Official docs: AnyIO
  - Official docs: Starlette
---

## TL;DR
FastAPI runs `async def` endpoints on the single event loop and plain `def` endpoints in a worker thread
pool. An `async def` handler that awaits I/O (an LLM API, a database, Redis) lets one process hold
hundreds of in-flight requests; an `async def` handler that *blocks* (a sync SDK call, a local model
forward pass) freezes every request in the process. The rule for AI services: await remote models,
thread or offload local ones, and never mix the two up.

## Why it exists
An AI endpoint spends almost all of its wall-clock time waiting. A chat completion call to a hosted
model can take anywhere from two to sixty seconds; the Python work around it — validate the request,
build messages, parse the response — takes about a millisecond. With one thread per request, serving
300 concurrent chats means 300 threads that are asleep nearly all the time, each holding a stack and
competing for the GIL during the tiny slices of work they do. Scaling that to thousands of users means
more processes, more memory and more money for CPU that sits idle.

Cooperative concurrency inverts this. A coroutine that hits `await client.post(...)` hands the loop
back; the loop runs other coroutines until the socket has data. One process can keep thousands of such
waits in flight on a single thread, because waiting is free. That is the whole reason `async def`
exists in a web framework, and for AI workloads the gain is unusually large because the wait-to-work
ratio is so extreme.

The naive version — "write `async def` everywhere, it is the modern way" — fails in the opposite
direction. The loop has exactly one thread. If a coroutine calls `requests.post`, `time.sleep`,
`model.generate(...)` on a CPU or GPU, or a sync database driver, it holds the loop for the whole
duration and nothing else in the process runs: not other requests, not health checks, not the
heartbeat that keeps the load balancer happy. The symptom is a server that is fast at one request per
second and collapses, every latency equal to the slowest call, at twenty. Knowing which code is
cooperative and which is not is the real content of this topic.

## How it works
### Two execution paths
When a request reaches a route, FastAPI resolves dependencies and calls the endpoint:
- `async def` endpoints (and dependencies) are awaited directly on the event loop. They must only
  `await` cooperative libraries.
- `def` endpoints (and dependencies) are handed to `run_in_threadpool`, which uses AnyIO's
  `to_thread.run_sync`. The loop keeps running while the thread blocks. AnyIO's default thread limiter
  allows 40 concurrent threads per process, so the 41st blocking call queues behind the others; you can
  raise it, at the cost of memory and GIL contention.
Request parsing and body validation happen on the loop in both cases; response-model validation runs
on the loop for `async def` endpoints and in the thread pool for `def` ones. This is CPU work, normally
sub-millisecond for chat-sized payloads, which is acceptable *because* it is bounded, unlike a model
forward pass; it grows with payload size, so megabyte documents or thousands of vectors are the
exception worth measuring.

### What counts as blocking
Blocking means the thread cannot be used for anything else until the call returns. In an AI service
the usual offenders are synchronous HTTP clients (`requests`, the sync variants of provider SDKs),
`time.sleep`, synchronous database drivers, file I/O on large files, tokenising a very long document,
and any local model inference (PyTorch, ONNX Runtime, llama.cpp bindings) whether on CPU or GPU, since
a GPU call blocks the calling thread until the result is back unless the library offers an async API.
The cooperative equivalents are `httpx.AsyncClient`, the `Async*` provider clients, async drivers
(asyncpg, aiomysql), `aiofiles`, and `anyio.to_thread.run_sync` for anything that has no async form.

### Fan-out and ordering
Because awaits yield, one request can run several provider calls concurrently with `asyncio.gather`
or an AnyIO task group: embed three chunks at once, call two models and pick the better answer, or run
a guardrail classifier in parallel with the main completion. Latency becomes the maximum of the calls
instead of the sum. This is only possible on the async path; a `def` endpoint would need its own
threads to achieve the same overlap.

### Threads and the GIL
`def` endpoints parallelise waiting, not Python computation: two threads running pure-Python
tokenisation share one interpreter lock. They do help for C and CUDA code that releases the GIL, which
PyTorch and NumPy do for most heavy kernels, so a `def` endpoint running `model.generate()` lets the
loop keep serving health checks while the GPU works. It does not make the GPU any less of a single
shared resource; concurrency limits still apply.

### Capacity arithmetic
Little's law, `concurrency = throughput × latency`, turns the two paths into numbers. With hosted LLM
calls that take 10 s:
- `def` endpoint: at most 40 requests in flight (the thread limiter), so the ceiling is 40 ÷ 10 s =
  4 requests/s per process, however idle the CPU is. Request 41 waits for a thread.
- `async def` endpoint: in-flight requests are bounded by the HTTP client's connection pool instead
  (`httpx` defaults to 100 connections), giving 100 ÷ 10 s = 10 requests/s per client, and you can
  raise that limit cheaply because a parked coroutine costs kilobytes, not a thread.
- Blocking call on the loop: each request holds the *whole process* for its blocking time *b*, so
  throughput can never exceed 1 ÷ *b*; 40 ms of synchronous work caps the worker at 25 requests/s.
The async path does not remove limits; it moves them from threads to sockets and provider quotas,
which are the limits you actually want to be bound by.

### Cancellation and disconnects
Uvicorn does not cancel a handler when the client leaves; it delivers a disconnect event. On the async
path your code can act on it: race the provider call against `request.is_disconnected()` in a task
group and cancel the call, so you stop paying for tokens nobody will read. Cancelling a coroutine
raises `CancelledError` at its current `await`, which closes the provider socket. A thread blocked in a
sync call cannot be cancelled from outside in Python; it runs to completion. That asymmetry is another
reason to keep remote model calls on the async path.

### Choosing per endpoint
- Calls a hosted LLM, embedding API, vector database, Redis, or Postgres over an async driver →
  `async def`.
- Runs a local model or a sync-only library you cannot change → `def`, or `async def` with
  `await run_in_threadpool(fn, ...)` around just the blocking part.
- Heavy local inference in production → neither; put the model behind a dedicated inference server and
  go back to the first case.

## Example
The commented-out version blocks the loop twice: a CPU reranker and a sync HTTP call. The live version
runs the reranker in the thread pool, borrows the shared async client created in lifespan, and fans
out the completion and a safety classifier so the request's latency is the slower of the two, not
their sum.

```python
import anyio, httpx
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

app = FastAPI()

class Ask(BaseModel):
    question: str
    candidates: list[str]

def rerank_sync(question: str, docs: list[str]) -> list[str]:
    # stand-in for a CPU/GPU cross-encoder; blocks for the duration of the forward pass
    return sorted(docs, key=lambda d: -len(set(d.split()) & set(question.split())))

# BAD: both calls hold the event loop; every other request in the process waits.
# @app.post("/ask")
# async def ask_blocking(body: Ask):
#     ranked = rerank_sync(body.question, body.candidates)
#     r = httpx.post("https://api.example.com/v1/complete", json={"q": body.question, "ctx": ranked[:3]})
#     return r.json()

@app.post("/ask")
async def ask(body: Ask, request: Request):
    client: httpx.AsyncClient = request.app.state.http       # created once in lifespan, with base_url
    ranked = await run_in_threadpool(rerank_sync, body.question, body.candidates)
    results = {}
    async def call(name, path, payload):
        r = await client.post(path, json=payload, timeout=30)
        results[name] = r.json()
    async with anyio.create_task_group() as tg:               # fan out: completion + guardrail
        tg.start_soon(call, "answer", "/v1/complete", {"q": body.question, "ctx": ranked[:3]})
        tg.start_soon(call, "safety", "/v1/classify", {"text": body.question})
    if results["safety"].get("flagged"):
        return {"answer": None, "reason": "blocked"}
    return {"answer": results["answer"], "sources": ranked[:3]}
```

## Trade-offs
- `async def` gives huge concurrency per process for I/O-bound work, but one accidental blocking call
  degrades every request; the win flips to a loss exactly when a sync library sneaks in.
- `def` endpoints are safe by default and tolerate sync libraries, but each in-flight request occupies a
  thread from a bounded pool (40 by default), so throughput caps far lower under long calls.
- Fan-out with `gather` or task groups cuts latency to the slowest call, but multiplies provider
  concurrency and cost, and needs per-request concurrency limits once traffic grows.
- Running blocking inference in the thread pool keeps the loop alive, yet threads cannot be cancelled
  on disconnect and still contend for the GIL on the pure-Python parts.

## Pitfalls
- Using a sync provider SDK (`OpenAI()` rather than `AsyncOpenAI()`, or `requests`) inside `async def`;
  symptom: p50 latency fine in isolation, every latency equal to the slowest call under modest load,
  health checks timing out.
- Calling `time.sleep` for retry back-off inside a coroutine; symptom: a single rate-limited request
  pauses the whole process for the back-off period.
- Declaring dependencies `async def` and doing blocking work in them (sync DB session creation, sync
  JWKS fetch); symptom: the same stall, but harder to find because it is outside the endpoint body.
- Running a GPU model directly in `async def` to "keep it async"; symptom: throughput equals one request
  at a time with no overlap of network waits, and the loop-lag metric spikes to the model's latency.
- Raising the thread limiter to hundreds to fix slow `def` endpoints; symptom: memory growth and GIL
  thrash with no throughput gain for Python-bound code.

## When to use
Default to `async def` for any endpoint whose time goes to remote calls, which is most AI gateway, RAG
and agent endpoints, and make sure every library on that path is actually async. Use `def` or
`run_in_threadpool` for unavoidable blocking work, and bound how much of it can run at once. Measure
event-loop lag (schedule a no-op every second and record how late it runs) and the thread-pool queue
depth; lag above a few milliseconds under load means something is blocking. Day to day this shows up
as a code-review comment on imports: `import requests` in an async service is a bug until proven
otherwise. In interviews, expect "what happens if you call a sync function in an async endpoint?" and
"when would you use `def` instead?"; the answers are the stall and the thread pool.

## Role lens
- **Engineer:** Picks the async client of every SDK, wraps leftover sync calls in `run_in_threadpool`,
  and adds an event-loop-lag gauge to catch regressions before users do.
- **Architect:** Decides that local inference lives behind a model server rather than inside the API
  process, so API workers stay purely I/O-bound and cheap to scale.
- **FDE:** Finds the one synchronous call a customer's team added to an async endpoint when they report
  "it falls over at thirty users".

## Check yourself
**Q:** A RAG endpoint is `async def`: it embeds the query with a local sentence-transformer (about
40 ms on CPU), queries a vector database over an async client, then calls a hosted LLM asynchronously.
Under load, throughput plateaus at roughly 25 requests per second even though the LLM provider is
nowhere near its limits. Explain and fix.
**A:** The embedding call is synchronous CPU work executed on the event loop. At 40 ms per call the
loop can do at most about 25 such calls per second, and while each one runs no other coroutine
progresses, so the asynchronous vector-database and LLM calls never overlap as they should. Moving the
embedding to `await run_in_threadpool(embed, query)` frees the loop, so the vector-database and LLM
waits overlap again; NumPy and PyTorch release the GIL during the heavy kernels. It does not make the
CPU faster: if one 40 ms embedding already uses every core through PyTorch's intra-op threads,
embedding throughput stays near the same ceiling, now without stalling everything else. Beyond that,
batch embeddings across concurrent requests or move the embedder to a separate service, so the API
process is purely I/O-bound and its throughput is set by provider latency and connection limits rather
than by CPU time spent on the loop.

## Quiz
### Q: What happens when an `async def` endpoint calls a synchronous HTTP client that takes 5 seconds?
- [ ] FastAPI detects the blocking call and moves it to a thread
- [x] The event loop is blocked for 5 seconds, so every other request in the process waits
- [ ] Only that request is delayed; others proceed on other loop threads
- [ ] The call is cancelled after the default timeout
> The loop runs on one thread and cannot switch coroutines while a non-awaiting call holds it;
> concurrency on the async path depends entirely on code yielding with `await`.

### Q: Why can a plain `def` endpoint running `model.generate()` keep the server responsive while an `async def` one cannot?
- [ ] `def` endpoints are compiled to C and release the GIL
- [ ] `def` endpoints run in a separate process
- [x] `def` endpoints run in a thread pool, so the loop thread stays free to serve other requests
- [ ] `async def` endpoints are limited to one request at a time
> FastAPI dispatches sync endpoints through `run_in_threadpool`; the loop continues scheduling other
> coroutines while the worker thread blocks, bounded by the thread limiter's capacity.

### Q: When does fanning out two provider calls with `asyncio.gather` reduce latency?
- [ ] Always, because gather parallelises CPU work across cores
- [x] When both calls spend their time awaiting I/O, so their waits overlap on one loop
- [ ] Only when the endpoint is declared with `def`
- [ ] Never; gather only orders the results
> Overlap comes from both coroutines yielding while sockets are idle; CPU-bound work does not overlap on
> a single loop thread, and the gain is capped by the slower of the two calls.
