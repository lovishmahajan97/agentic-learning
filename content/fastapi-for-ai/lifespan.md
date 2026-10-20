---
id: lifespan
module: fastapi-for-ai
name: Lifespan (load model / client once)
kind: pattern
difficulty: core
est_minutes: 13
status: reviewed
related:
  - fastapi-for-ai/asgi-lifecycle
  - fastapi-for-ai/request-scoped-vs-application-scoped-resources
  - fastapi-for-ai/shared-httpx-asyncclient
  - fastapi-for-ai/dependency-injection
  - fastapi-for-ai/uvicorn-gunicorn-workers
  - fastapi-for-ai/health-readiness-endpoints
  - fastapi-for-ai/graceful-shutdown-and-worker-draining
  - serving-inference/model-serving
sources:
  - Official docs: FastAPI
  - Official docs: Starlette
  - Official docs: ASGI specification
  - Official docs: Uvicorn
---

## TL;DR
`lifespan` is an async context manager passed to `FastAPI(lifespan=...)`: code before `yield` runs
once per worker before any request is served, code after `yield` runs once at shutdown. It is where
you load model weights, build the provider client and its connection pool, and open the DB engine,
because those are expensive, shareable, and need a guaranteed close, none of which import-time
globals or per-request construction give you.

## Why it exists
An AI service owns a few objects that are slow to build and meant to be shared: a tokenizer and model
that take seconds and gigabytes to load, an `httpx.AsyncClient` or provider SDK client whose value is
its pool of warm TLS connections, a database engine, a vector index loaded from disk. There are three
naive places to put them, and each fails in a specific way.

Building them **per request** pays the full cost every call: a model reload per request is seconds of
latency and a copy of the weights per concurrent call, and a fresh client per call means a new TCP and
TLS handshake every time with sockets piling up under load. Building them **at import time** as module
globals fixes the repetition but loses control: every `import app` (tests, Alembic migrations, a CLI
script, the OpenAPI generator) now loads a model; there is no running event loop for `await`-based
setup, and loop-bound objects (an asyncpg pool, an `aiohttp` session) created on one loop fail with
"attached to a different loop" errors when a test or server runs them on another; and there is no hook
to close the client or flush buffers when the process stops. Building them **lazily
on first request** hides the cost inside a user's request, races when two first requests arrive
together, and lets a worker report healthy before it can actually serve.

What is needed is a hook with three properties: it runs once per process, it runs *before* the server
reports the app as started, and it is paired with a teardown that is guaranteed to run at shutdown. The
ASGI spec provides exactly that as the lifespan protocol, and FastAPI's `lifespan` parameter is the
idiomatic way to write it.

## How it works
### The protocol underneath
When a worker process starts, the ASGI server (Uvicorn, Hypercorn) opens a special connection with
`scope["type"] == "lifespan"` and sends `lifespan.startup`. Starlette, which FastAPI builds on, enters
your context manager, runs everything up to `yield`, and replies `lifespan.startup.complete`. Only then
does the server treat the app as ready. If the code before `yield` raises, Starlette replies
`lifespan.startup.failed` and the server exits instead of serving a half-initialised app. This is the
fail-fast property you want: a missing API key or a corrupt weights file stops the deploy instead of
producing a fleet of workers that 500 on every request. At shutdown (for example on SIGTERM), the
server stops accepting new connections, waits for in-flight requests to finish (Uvicorn waits
indefinitely unless `--timeout-graceful-shutdown` bounds it), then sends `lifespan.shutdown`, and
the code after `yield` runs. Teardown therefore normally happens after
requests have drained, which is why closing the shared client there is safe.

### Writing it
```
@asynccontextmanager
async def lifespan(app):     # setup ... yield ... teardown
```
The context-manager shape is chosen *because* it pairs setup and teardown in one function: the client
you open on line 3 is visibly closed on line 9, and both can share local variables. It replaces the
older `@app.on_event("startup")` / `("shutdown")` decorators, which FastAPI documents as deprecated;
if you pass `lifespan=`, those event handlers are not called, so do not mix the two.

### Where the objects go
Two ways to hand resources to requests:
1. **`app.state`**: assign `app.state.model = ...` in lifespan, read `request.app.state.model` in a
   dependency. Simple and explicit.
2. **Lifespan state**: `yield {"model": model}`. Starlette makes a shallow copy of that dict available
   as `request.state` on every request. Shallow means per-request reassignment does not leak, but
   mutating a shared object does, exactly as with `app.state`.

Either way, handlers should reach the objects through a dependency rather than directly, *because* a
dependency is the seam tests use to substitute a fake model or client via `dependency_overrides`.

### Robust teardown
If startup creates three resources and the third raises, code after `yield` never runs, so the first
two leak. Wrap acquisition in `contextlib.AsyncExitStack` (or `try/finally`): each resource registers
its own close as it is created, and the stack unwinds whatever was built, in reverse order, on failure
or at shutdown.

### Per worker, not per deployment
Lifespan runs once *per worker process*. With four Uvicorn workers you get four model copies, four
connection pools and four engines; size the DB pool and GPU memory with that multiplication in mind.
When the per-worker model copy no longer fits, the model belongs in a dedicated inference server and
lifespan just builds a client to it.

The tempting shortcut, loading the model once in a Gunicorn master with `--preload` and letting the
forked workers share pages copy-on-write, is outside lifespan entirely and has sharp edges: CPython's
reference counting writes to object headers, so "shared" pages get copied gradually, and CUDA cannot be
re-initialised in a forked child, so a GPU model initialised in the master is unusable in the workers.

### Blocking work during startup
`from_pretrained`-style loaders are synchronous. During startup no requests are being served, so a
blocking load mostly just delays readiness; still, `await asyncio.to_thread(load)` keeps the loop
responsive (for example to signals) and costs nothing. Run a small warm-up inference before `yield` so
lazy allocations and kernel selection happen at startup rather than on the first user's request.

### Things that do not run lifespan
Starlette runs the lifespan of the root app only, not of sub-applications attached with `app.mount`.
In tests, `TestClient(app)` runs lifespan only when used as `with TestClient(app) as client:`, and
`httpx.AsyncClient(transport=ASGITransport(app))` does not run it at all; a helper such as the
`asgi-lifespan` package's `LifespanManager` is needed. Forgetting this is the usual reason a test sees
`AttributeError: 'State' object has no attribute 'model'`.

## Example
An embedding service that loads a sentence-transformer model once, builds one shared HTTP client for
a reranking provider, warms the model, and closes everything at shutdown even if startup fails
halfway. `RERANK_URL` and `RERANK_KEY` come from the environment, so a missing key fails startup.

```python
import asyncio, os
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated
import httpx
from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        key = os.environ["RERANK_KEY"]              # KeyError here -> startup.failed, worker exits
        app.state.http = await stack.enter_async_context(
            httpx.AsyncClient(base_url=os.environ["RERANK_URL"],
                              headers={"Authorization": f"Bearer {key}"}, timeout=10.0))
        model = await asyncio.to_thread(SentenceTransformer, "all-MiniLM-L6-v2")
        await asyncio.to_thread(model.encode, ["warm-up"])   # pay first-call costs now
        app.state.model = model
        yield                                        # serve traffic
    # leaving the stack closed the client (also on a failed startup)

app = FastAPI(lifespan=lifespan)

def get_model(request: Request) -> SentenceTransformer:
    return request.app.state.model                   # override this in tests

class EmbedIn(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=256)   # empty list would break .shape[1]

@app.post("/embed")
async def embed(body: EmbedIn, model: Annotated[SentenceTransformer, Depends(get_model)]):
    vectors = await asyncio.to_thread(model.encode, body.texts)   # CPU/GPU work off the loop
    return {"dim": int(vectors.shape[1]), "count": len(body.texts)}
```

Run with `uvicorn app:app --workers 2` and the model is loaded twice, once per worker, before either
accepts a request; with `with TestClient(app) as c:` the same lifespan runs in the test.

## Trade-offs
- Loading in lifespan makes every request fast and predictable, but startup now takes as long as the
  model load; for large weights this flips orchestration from "readiness is instant" to needing a
  startup probe with a generous budget.
- Fail-fast startup turns misconfiguration into a crashed deploy instead of silent 500s, but a flaky
  optional dependency at boot (a cache that is briefly down) now blocks the whole service; make truly
  optional resources degrade at request time instead.
- One shared client or model per worker maximises reuse, but multiplies by the worker count; once N
  copies of the weights exceed host memory, a separate model server beats more workers.
- `app.state` is explicit and easy to read, while lifespan state via `yield {...}` is rebuilt on each
  lifespan run and can be described with a `TypedDict`; the choice matters little until several apps
  share a process or tests start the same app repeatedly and need fresh state each time.

## Pitfalls
- Using `TestClient(app)` without `with`, or an `httpx` ASGI transport, in tests; symptom: handlers
  fail with `'State' object has no attribute ...` only in the test suite.
- Putting teardown after `yield` without `AsyncExitStack` or `try/finally`; symptom: when one startup
  step fails, the earlier clients or pools are never closed and warnings about unclosed connections
  appear in logs.
- Adding `@app.on_event("startup")` handlers to an app that already passes `lifespan=`; symptom: that
  startup code silently never runs.
- Defining lifespan on a sub-application mounted with `app.mount`; symptom: the mounted app's model or
  client is never created and its routes fail while the root app's routes work.
- Pointing a liveness probe at the app during a long model load; symptom: the orchestrator kills the
  container mid-load in a restart loop, because the worker has not finished startup and is not
  answering yet.
- Storing per-request data on objects created in lifespan; symptom: intermittent cross-user leaks under
  concurrent load, because those objects are shared by every in-flight request.

## When to use
Use lifespan for anything that is expensive to create, safe to share across concurrent requests, and
needs a clean close: model weights and tokenizers, provider and HTTP clients, DB engines, Redis pools,
vector indexes, parsed settings. Do not use it for per-request state (sessions, users, budgets); those
belong in yield dependencies. Measure time from process start to startup complete, memory per worker
after load, and the latency of the first request versus steady state; a slow first request means some
initialisation still escapes lifespan. In code review, any `Client(...)`, `create_engine(...)` or
`from_pretrained(...)` inside a handler or at module top level is a signal to move it here. In
interviews, expect "where do you load the model and why not at import or per request?".

## Role lens
- **Engineer:** Writes the lifespan with an `AsyncExitStack`, loads and warms the model before `yield`,
  exposes resources through dependencies, and runs tests inside `with TestClient(app)`.
- **Architect:** Decides what may live in a worker's lifespan versus a dedicated model server, from
  per-worker memory multiplied by worker count and from acceptable startup time.
- **FDE:** Diagnoses a customer deployment stuck in a restart loop or with slow first requests by
  checking probe timing against model load time and finding initialisation hidden in handlers.

## Check yourself
**Q:** A team loads a 3 GB model at module import in `main.py` and creates an `httpx.AsyncClient`
inside each endpoint. Their pytest suite takes minutes to start, Alembic migrations need a GPU box, and
the provider calls are slower than expected. They also see Kubernetes restarting new pods during
rollout. What is going on and what should change?
**A:** Import-time loading runs on every `import main`, so tests, migrations and any tool that imports
the app pay the model load; it also gives no shutdown hook. The per-endpoint client discards its pool
after each call, so every provider request pays a fresh TCP and TLS handshake. Move both into a
lifespan: create the client once (closed via `AsyncExitStack` at shutdown) and load and warm the model
before `yield`, exposing each through a dependency so tests can override them. The restarts are likely
probes firing before startup completes; add a startup probe sized to the load time and keep liveness
cheap. Also budget memory for one model copy per worker.

## Quiz
### Q: When does the code after `yield` in a FastAPI lifespan run?
- [ ] After every request completes, to release per-request resources
- [ ] Immediately after startup, before the first request is served
- [x] Once per worker at shutdown, after the server sends the ASGI `lifespan.shutdown` event
- [ ] Only if startup raised an exception
> The lifespan context manager spans the worker's whole life: setup before `yield`, serving during it,
> teardown after it at shutdown. Per-request cleanup belongs in yield dependencies, not in lifespan.

### Q: A test uses `client = TestClient(app)` and gets `'State' object has no attribute 'model'`. Why?
- [ ] `app.state` is cleared between requests by Starlette
- [x] Lifespan only runs when TestClient is used as a context manager, so the model was never loaded
- [ ] TestClient runs a separate worker process with its own state
- [ ] Lifespan state is unavailable in synchronous tests
> `TestClient` triggers the lifespan startup and shutdown events only inside a `with` block. Without it
> the app serves requests without running startup, so nothing was attached to `app.state`.

### Q: Startup creates an HTTP client, then a DB engine, then fails loading the model. What prevents the client and engine from leaking?
- [ ] Python's garbage collector closes them when the worker exits
- [ ] Putting the close calls after `yield` in the lifespan function
- [x] Registering each resource on an `AsyncExitStack` (or `try/finally`) as it is created
- [ ] Using `@app.on_event("shutdown")` alongside the lifespan
> Code after `yield` never runs when startup fails before reaching it. An exit stack unwinds only what
> was actually acquired, in reverse order, on both failure and normal shutdown; on_event handlers are
> ignored when lifespan is set.
