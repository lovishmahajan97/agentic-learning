---
id: fastapi
module: backend-fundamentals
name: FastAPI
kind: tool
difficulty: intro
est_minutes: 11
status: reviewed
related:
  - fastapi-for-ai/asgi-lifecycle
  - fastapi-for-ai/async-endpoints
  - fastapi-for-ai/pydantic-request-response-models
  - fastapi-for-ai/dependency-injection
  - fastapi-for-ai/streamingresponse
  - fastapi-for-ai/openapi-docs
  - backend-fundamentals/python
  - backend-fundamentals/rest-graphql-api-design
sources:
  - Official docs: FastAPI
  - Official docs: Starlette
  - Official docs: Pydantic
---

## TL;DR
This is an orientation lesson; the deep dive is the `fastapi-for-ai` module. FastAPI is the default
framework for Python AI services because it is async-native (ASGI, built on Starlette), uses Pydantic
models as the single source of truth for validation, serialisation and OpenAPI docs, and makes
streaming and dependency injection first-class: exactly what an endpoint that waits seconds on a
model needs. Its sharp edge: one blocking call inside an `async def` handler stalls every request on
that worker.

## Why it exists
An AI endpoint is unusual HTTP. A request may hold its connection open for ten seconds, stream
hundreds of chunks, carry a large structured body in and a large structured body out, and share
expensive clients (a model-provider connection pool, a vector store) across every request. The
classic Python web stack was built for the opposite: WSGI frameworks like Flask and traditional
Django dedicate one thread or process to a request from start to finish. With 200 in-flight model
calls of five seconds each, a WSGI server needs 200 workers to avoid queueing, and those workers sit
idle on sockets. An ASGI server runs one event loop per process that holds all 200 pending awaits at
negligible cost, so concurrency is bounded by memory and the upstream, not by worker count.

The second forcing problem is contract drift. Hand-written validation and hand-written API docs
diverge the week after launch; a client sends `temperature: "0.7"` and the handler crashes deep in a
provider call. FastAPI reads the Pydantic types on the handler signature and derives validation, a
precise 422 error, response filtering and the OpenAPI schema from one declaration, so the docs cannot
lie about the code. In AI services that same model class is also the JSON Schema you hand a model for
structured output, which closes the loop between API contract and prompt contract.

The naive alternative, "use raw Starlette or aiohttp and validate by hand", works but reinvents those
layers, and Flask-with-async-bolted-on keeps the WSGI mental model where it hurts most.

## How it works
### ASGI and the request path
Uvicorn accepts the connection and hands an ASGI `scope` to the app. The request passes through the
middleware stack (CORS, auth, tracing), the router matches the path and method, FastAPI resolves the
handler's dependencies, validates path, query and body parameters against the declared types, calls
the handler, then serialises the return value through `response_model`. Validation failures stop at
the boundary with a 422 and a field-level error list, before the handler body runs (dependencies
may already have executed, so keep side effects out of them).

### `async def` versus `def` handlers
An `async def` handler runs directly on the event loop, so every `await` yields to other requests. A
plain `def` handler is run in a bounded worker threadpool so that it cannot block the loop. The choice
is deliberate: use `async def` when everything you call is async (httpx, async DB drivers, provider
SDKs' async clients); use `def` when you must call a blocking library. The failure mode is the
mix: a sync SDK call inside `async def` blocks the loop and every other request on that worker waits.
The same rule applies to dependencies: a plain `def` dependency is also run in the threadpool.

The capacity arithmetic differs between the two. A `def` handler's concurrency per worker is capped by
the threadpool (AnyIO's default limiter is 40 tokens), so if each call holds a thread for 5 seconds,
one worker serves at most 40 / 5 = 8 requests per second before requests queue for a thread. An
`async def` handler that awaits the same 5-second call is capped by memory and the upstream's limits
instead, which is why the async path is the default for model-calling endpoints.

### Workers and processes
One uvicorn process is one event loop on one core. Production runs several worker processes per
machine (uvicorn `--workers`, gunicorn with uvicorn workers, or one process per container behind the
orchestrator) *because* JSON parsing, validation and serialisation still use CPU, and a single loop
cannot use a second core. Workers share nothing, so in-memory caches, rate-limit counters and
"which streams are open" state belong in Redis or the database, not in a module-level dict.

### Pydantic as the contract
Declaring `body: ChatRequest` validates and parses the JSON body; `response_model=Answer` filters the
output to the declared fields (so a leaked internal attribute never reaches a client) and documents
it. `Field` constraints (`max_length`, `ge`, `le`) become both runtime checks and schema metadata.

### Dependency injection and lifespan
`Depends` lets a handler ask for a current user, a DB session or a shared HTTP client without
constructing it; dependencies compose and can be overridden in tests. `lifespan` runs startup and
shutdown code, which is where you build application-scoped resources (one `httpx.AsyncClient`, one
model client) instead of recreating them per request.

### Streaming
`StreamingResponse` takes an async generator and writes each yielded chunk as it arrives. With
`text/event-stream` framing you get server-sent events, the standard way to stream tokens to browsers
and SDKs. Because each `yield` happens between awaits, a slow stream does not block other requests.

### Background tasks are not a job queue
`BackgroundTasks` runs a function after the response is sent, in the same process. That suits a
few hundred milliseconds of follow-up (write an audit row, emit a metric). It is the wrong home for a
two-minute summarisation *because* the work dies with the process on a deploy or crash, nothing
retries it, and it competes with live requests for the same loop or threadpool. Long or must-complete
work goes to a durable queue and a separate worker, with a `202` and a job resource on the API side.

### Compared with the alternatives
Flask is simpler and sync-first, fine for small internal tools but a poor fit for long-lived streaming
calls. Django brings an ORM, admin and auth; choose it when those matter more than async purity, and
note its async support is partial. Starlette is FastAPI without validation, DI and docs. Litestar is
a comparable ASGI framework with a different DI style; the reasoning in this lesson applies to it too.

## Example
A streaming chat endpoint: one shared upstream client created in `lifespan`, a validated request
body, and an SSE stream produced by an async generator. Every `await` inside `events()` releases the
loop to other requests, which is the whole point of the async design.

```python
import json
from contextlib import asynccontextmanager
import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(base_url="http://model-server:8000", timeout=60)
    yield                                          # the app serves requests here
    await app.state.http.aclose()                  # shutdown: close the pool cleanly

app = FastAPI(lifespan=lifespan)

class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    temperature: float = Field(0.2, ge=0, le=2)   # validated and documented from one declaration

def http(request: Request) -> httpx.AsyncClient:   # dependency: one shared pool, not one per request
    return request.app.state.http

@app.post("/v1/chat")
async def chat(body: ChatRequest, client: httpx.AsyncClient = Depends(http)):
    async def events():
        async with client.stream("POST", "/generate", json=body.model_dump()) as r:
            async for line in r.aiter_lines():     # each await yields the loop to other requests
                if line:
                    yield f"data: {json.dumps({'token': line})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")
```

## Trade-offs
- You gain high concurrency for I/O-bound handlers, but every library on the hot path must be async
  or explicitly offloaded; one blocking call turns the advantage into a liability.
- Pydantic-driven validation removes boilerplate and keeps docs honest, but it adds per-request
  parsing cost; for very large bodies or extreme throughput that cost is measurable and worth profiling.
- FastAPI is deliberately thin (no ORM, no admin, no auth framework), which keeps it flexible but
  means you assemble those pieces yourself; Django wins when a full-stack CRUD app is the actual job.
- Single-process asyncio scales I/O, not CPU; you still run several uvicorn workers per machine and
  design them to be stateless.

## Pitfalls
- Using a synchronous provider SDK or `requests` inside `async def`; the symptom is latency on every
  endpoint of that worker growing with the sum of the blocking upstream calls queued ahead of it.
- Creating an `httpx.AsyncClient` per request; the symptom is a new TLS handshake per call and socket
  exhaustion under load, fixed by one client in `lifespan`.
- Returning ORM objects or internal dicts without `response_model`; the symptom is an internal field
  (a cost figure, a secret flag) appearing in client responses.
- Doing CPU-heavy work (tokenizing megabytes, re-ranking in pure Python) inside the handler; the
  symptom is event-loop stalls visible as every endpoint slowing together.
- Running long jobs through `BackgroundTasks`; the symptom is work that silently vanishes on every
  deploy or pod restart, with no error logged because the request already returned 200.
- Keeping state in module-level variables; the symptom is behaviour that changes with which worker
  served the request (a rate limit that allows N times the configured rate with N workers).

## When to use
Reach for FastAPI when building the HTTP layer of a Python AI service: model gateways, RAG endpoints,
agent runners, anything that awaits a model and benefits from streaming and typed contracts. Prefer
Django when the application is mostly conventional CRUD with an ORM and admin, and Flask only for
tiny sync tools. Measure event-loop lag (schedule a no-op every 100 ms and record how late it fires),
the health check's latency under load (it should stay flat; if it tracks the model endpoint's
latency, something is blocking the loop), threadpool saturation for `def` handlers, and upstream
connection reuse. In day-to-day work the skill is keeping the request path fully async,
designing Pydantic models that double as structured-output schemas, and wiring shared clients
through dependencies; interviews probe whether you know why `def` and `async def` handlers behave
differently. The `fastapi-for-ai` module covers each mechanism in depth.

## Role lens
- **Engineer:** Builds endpoints with Pydantic contracts, shared async clients via `Depends`, and SSE
  streaming, and keeps anything blocking out of the event loop.
- **Architect:** Decides that the AI service layer is ASGI and stateless, sets worker and concurrency
  limits per process, and treats the OpenAPI schema as the published contract for other teams.
- **FDE:** Stands up a FastAPI service inside a customer's network, exposes `/docs` so their
  integrators can self-serve, and explains why blocking code in their middleware (a sync auth lookup)
  stalls every async handler behind it.

## Check yourself
**Q:** A FastAPI service has one `async def` endpoint that calls a provider through a synchronous SDK.
Under light load it is fine; under load every endpoint, including the health check, becomes slow.
Explain the mechanism and two fixes with their trade-offs.
**A:** The sync SDK call blocks the event loop thread for the full duration of the model request, so
no other coroutine on that worker, including the health check, can run until it returns; under load
requests queue behind each other. Fix one: switch to the SDK's async client (or httpx) so the call
yields to the loop; this is the right long-term fix but requires the whole path to be async. Fix two:
change the handler to plain `def` so FastAPI runs it in the threadpool; this unblocks the loop
immediately but caps concurrency at the threadpool size (AnyIO's default limiter is 40 threads,
tunable) and adds thread overhead; at 5 seconds per call, 40 threads is only 8 requests per second per
worker. A middle path is to keep the handler async and wrap just the sync call in
`await asyncio.to_thread(...)`, with the same thread cap. Either way, add a metric for event-loop lag
so a regression is visible, and treat a slow health check as the first sign of a blocked loop.

## Quiz
### Q: Why does FastAPI run a plain `def` handler in a threadpool but an `async def` handler on the loop?
- [ ] Threadpools are faster for JSON serialisation
- [x] A sync function would block the single event loop, so it is isolated in a thread while coroutines yield at each await
- [ ] Pydantic validation only works in threads
- [ ] The threadpool enables HTTP/2 multiplexing
> The event loop is one thread; a blocking function on it stops every other request. Running sync
> handlers in threads protects the loop, while async handlers cooperate by yielding at awaits.

### Q: What do you get from declaring the request body as a Pydantic model?
- [ ] Automatic caching of identical request bodies
- [ ] Guaranteed backward compatibility when fields are renamed
- [x] Runtime validation with a 422 error, parsed typed access, and the matching OpenAPI schema from one declaration
- [ ] Automatic retries of the upstream model call on validation failure
> The handler's type annotations drive validation, parsing and documentation together, so the docs and
> the behaviour cannot diverge. Caching, compatibility and retries are separate concerns.

### Q: When is Django a better choice than FastAPI for a service that also calls an LLM?
- [ ] When the service must stream tokens to a browser
- [x] When the application is mostly CRUD with an ORM, admin and auth, and the LLM call is a small part of it
- [ ] When the service needs OpenAPI documentation
- [ ] When the provider SDK is async-only
> FastAPI's strengths are async I/O and typed contracts; Django's are batteries for conventional web
> apps. If the bulk of the work is CRUD and admin, Django's ecosystem outweighs async purity.
