---
id: request-scoped-vs-application-scoped-resources
module: fastapi-for-ai
name: Request-scoped vs application-scoped resources
kind: decision
difficulty: core
est_minutes: 13
status: reviewed
related:
  - fastapi-for-ai/lifespan
  - fastapi-for-ai/dependency-injection
  - fastapi-for-ai/shared-httpx-asyncclient
  - fastapi-for-ai/async-database-access-and-connection-pooling
  - fastapi-for-ai/uvicorn-gunicorn-workers
  - fastapi-for-ai/redis-caching
  - python-for-ai/context-managers-resource-cleanup
  - python-for-ai/race-conditions-and-synchronization-primitives
sources:
  - Official docs: FastAPI
  - Official docs: HTTPX
---

## TL;DR
Every object in a FastAPI process has a scope. Application-scoped things (model weights, tokenizer,
provider client with its connection pool, DB engine, vector index, settings) are built once per worker
in lifespan and shared by all requests; request-scoped things (DB session, transaction, auth principal,
request ID, budget, trace span) are built by a dependency and die with the request. Wrong in one
direction wastes seconds and sockets per request; wrong in the other leaks one user's state into
another's.

## Why it exists
Two kinds of mistake are common in AI services, and they are mirror images. The first is building
expensive, shareable things per request: `httpx.AsyncClient()` inside the handler,
`from_pretrained(...)` inside a dependency, a new engine per call. A fresh client means a new TCP and
TLS handshake for every provider call (tens to hundreds of milliseconds, as an illustrative order of
magnitude), no connection reuse, and file descriptors that pile up under load; a fresh model load
means seconds of latency and a copy of the weights per concurrent request until memory runs out. The
second mistake is sharing things that carry per-request state: a module-level `current_user`, a single
DB session reused across requests, a global list used as a scratch buffer. Under concurrency, two
requests interleave on the event loop and one reads the other's user, commits the other's half-finished
transaction, or returns the other's documents. These bugs are intermittent, appear only under load,
and look like data leaks because they are.

The naive rule "make everything global for speed" produces the second class of bug; "make everything
fresh for safety" produces the first. The decision has to be made per object, by asking two
questions: is it expensive or stateful across requests (a pool, a cache, loaded weights), and is it
safe to use from many concurrent requests at once? Expensive and concurrency-safe → application scope.
Carries per-request identity or a transaction, or unsafe for concurrent use → request scope. FastAPI
gives one mechanism for each: lifespan plus `app.state` for the former, `Depends` with `yield` for the
latter, and a worker process as the boundary of "application".

## How it works
### Application scope: once per worker
The lifespan context manager runs before the worker accepts traffic. Objects created there — the
provider client, the engine, the loaded model, the tokenizer, parsed settings — are attached to
`app.state` (or yielded as lifespan state and read from `request.state`). They live until shutdown,
when the lifespan's exit code closes them. "Application" means the worker process: with four Uvicorn
workers there are four clients, four engines and four copies of the model, each with its own memory.
Nothing in this scope is shared across processes; cross-process state (rate counters, caches, session
stores) belongs in Redis or the database.

Objects in this scope are used from many requests at once, so they must be safe for concurrent use.
`httpx.AsyncClient`, SQLAlchemy's engine and async pool, and provider SDK clients are designed for it.
A loaded PyTorch model in eval mode can serve concurrent forward passes, but the GPU serialises the
work, so a concurrency limit belongs next to it. A raw SQLAlchemy `Session` is not safe to share and
does not belong here.

"Safe for concurrent use" means two different things depending on how it is called. Coroutines on one
loop interleave only at `await`, so a plain object used from `async def` code without awaits in the
middle of a mutation is effectively single-threaded. The same object used from `def` endpoints or
`run_in_threadpool` is touched by real threads at the same time. Some objects that are fine in the
first case are not in the second: Hugging Face fast tokenizers, for example, have been reported to
raise "Already borrowed" when one instance is called from several threads at once. Check which kind
of concurrency each shared object will actually see.

### Request scope: once per request, torn down with it
A dependency with `yield` builds the object, hands it to the handler and cleans up afterwards. This is
the home for a DB session and its transaction (commit on success, roll back on exception), the
authenticated principal, the tenant's resolved configuration, a per-request token budget, the trace
span, the request ID. FastAPI's per-request dependency cache guarantees that one request sees one
instance even when several dependencies ask for it, which is how a handler and its auth dependency
share a single transaction.

### Request scope is not the same as unit of work
In a CRUD service a request lasts milliseconds, so "one session per request" and "one transaction per
unit of work" coincide. In an AI service the request includes a 5–30 s model call, and a session that
has run a query holds a pooled connection until its transaction ends. Hold it across the provider call
and pool size, not the provider, sets your concurrency. With SQLAlchemy's defaults (`pool_size=5`,
`max_overflow=10`) a worker has 15 connections; at 15 s per call that is 1 request/s per worker, and
request 16 waits up to `pool_timeout` (30 s by default) before failing. Keep the session request-scoped
but the transactions short: read, commit (returning the connection), call the model, then write in a
second short transaction.

### Scopes in between
Some objects are neither global nor per request: a client per tenant (different API key or region),
a per-model tokenizer, a compiled prompt template. Treat them as application scope keyed by tenant or
model: a bounded dict built in lifespan or lazily behind an `asyncio.Lock`, so two first requests do not
build the same client twice. Conversation memory spans many requests and many workers, so it is not
in-process at all; it belongs in Redis or the database.

### The hand-off between scopes
Request-scoped objects are typically *derived from* application-scoped ones: the session comes from the
engine's pool, the per-request "view" of the client is the shared client plus per-request headers, the
retrieval handle is the shared index plus the tenant's namespace. The dependency borrows from
`app.state` and returns something cheap. Resources flow one way, application → request, never back; a
request must not stash its session or its user on `app.state`.

### Why the process is the boundary
Python's event loop interleaves requests within one process, so application scope means "shared by
concurrent requests" and must be concurrency-safe. Across processes nothing is shared, so application
scope also means "duplicated per worker" and must be affordable N times. Both facts come from the same
architecture: Uvicorn workers are independent processes with independent loops.

### Context variables
For data that must follow a request through code that cannot take parameters (logging formatters, deep
library calls), `contextvars` give request-scoped storage without globals: set the request ID in a
middleware or dependency, read it anywhere in that task. Each request's task sees its own value, which
is exactly the isolation a module global cannot give.

## Example
Lifespan creates the application-scoped objects; dependencies derive request-scoped ones; the handler
mixes them without ever creating a client or sharing a session, and commits before the model call so
its pooled connection is not held for the 10–30 s the provider takes. `expire_on_commit=False` is what
lets `doc.text` still be read after that commit. `Document` and `Summary` are ORM classes defined
elsewhere.

```python
from contextlib import asynccontextmanager
from typing import Annotated
import httpx
from fastapi import Depends, FastAPI, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(base_url="https://api.example.com", timeout=30)  # pool shared
    app.state.engine = create_async_engine("postgresql+asyncpg://user:pw@db/app", pool_size=10)
    app.state.sessions = async_sessionmaker(app.state.engine, expire_on_commit=False)
    yield
    await app.state.http.aclose()            # application scope ends with the worker
    await app.state.engine.dispose()

app = FastAPI(lifespan=lifespan)

async def db(request: Request):              # request scope: one session per request
    async with request.app.state.sessions() as session:
        yield session                         # closing it rolls back anything uncommitted

def principal(x_api_key: Annotated[str, Header()]) -> str:
    return x_api_key.split("-")[0]           # request scope: derived from this request's headers

@app.post("/summarise")
async def summarise(doc_id: int, request: Request,
                    session: Annotated[AsyncSession, Depends(db)],
                    who: Annotated[str, Depends(principal)]):
    http = request.app.state.http             # borrow the shared client; never httpx.AsyncClient() here
    doc = await session.get(Document, doc_id)  # checks out a pooled connection
    await session.commit()                     # short read transaction: connection back to the pool
    r = await http.post("/v1/complete", json={"text": doc.text}, headers={"x-tenant": who})
    session.add(Summary(doc_id=doc_id, text=r.json()["text"], by=who))
    await session.commit()                     # second short transaction for the write
    return {"ok": True}
```

## Trade-offs
- Application scope amortises expensive setup across all requests, but everything in it is duplicated
  per worker; past a couple of workers the memory of a loaded model forces a separate model server.
- Request scope guarantees isolation and clean teardown at the cost of per-request construction; it
  flips to a problem only when the object is expensive, which is the signal to move construction to
  lifespan and keep only a cheap handle per request.
- Sharing a concurrency-safe client gives connection reuse and pool limits in one place, but one bad
  setting (a tiny pool, a global timeout) now affects every endpoint at once.
- Splitting a request into short transactions around the model call frees pool connections, but the
  read and the write are no longer atomic; when the write must match what was read (a document edited
  meanwhile), add a version check on the write rather than holding the transaction open.
- `contextvars` remove the need to thread request context through signatures, but values set inside a
  thread-pool call or a separate task do not flow back, so the mechanism is only as predictable as the
  task structure around it.

## Pitfalls
- Creating `httpx.AsyncClient()` or a provider SDK client per request; symptom: elevated provider
  latency from handshakes, "too many open files" under load, and no benefit from keep-alive.
- Loading a model in a dependency or on first request; symptom: a multi-second first call per worker,
  memory growing with concurrency, and OOM kills once several workers run.
- Storing the current user or request-specific data on `app.state` or a module global; symptom:
  intermittent wrong-user responses that reproduce only under concurrent load.
- Holding a DB transaction open across the model call; symptom: the database is idle and the provider
  is under quota, yet requests queue and fail with SQLAlchemy `QueuePool limit ... reached` /
  `TimeoutError` once concurrency passes the pool size.
- Sharing one DB session across requests; symptom: "session already in a transaction" errors and
  commits that contain other requests' changes.
- Expecting an in-process cache or counter on `app.state` to be global; symptom: rate limits enforced
  per worker, so a limit of 100 becomes 400 with four workers.

## When to use
Decide scope for each object by writing it down: cost to create, whether it is safe for concurrent
use, whether it carries per-request identity. Clients, engines, models, indexes and settings go to
lifespan; sessions, principals, budgets, spans and IDs go to dependencies; anything that must be shared
across workers or survive a restart goes to an external store. Measure connection reuse (new
connections per second on the provider client), time-to-first-request per worker, and memory per
worker; each points at a scoping mistake when it is wrong. This decision comes up in interviews as
"where would you create the OpenAI client and why not in the endpoint?", and in code review every time
a `Client()` call appears inside a function that runs per request.

## Role lens
- **Engineer:** Builds clients, engines and models in lifespan, derives sessions and principals in
  yield dependencies, and never instantiates a client inside a handler.
- **Architect:** Sets the worker-versus-model-server boundary from per-worker memory, and decides which
  state must leave the process for Redis or the database.
- **FDE:** Finds the per-request client or the shared session in a customer's codebase when they report
  handshake latency or cross-tenant data appearing in responses.

## Check yourself
**Q:** A multi-tenant RAG service shows two symptoms after scaling to four workers: provider latency
rose noticeably per call, and a customer once saw another tenant's document titles. The code creates
an `AsyncClient` in each handler and keeps the current tenant in a module-level variable set by a
middleware. Explain each symptom and the fix.
**A:** The latency is the handshake cost of a new client per request: no pooled connections are
reused, so every provider call pays TCP and TLS setup; move the client to lifespan on `app.state` and
borrow it from a dependency. The leak is a request-scoped value held in application scope: the
middleware sets a module global that every concurrent request in the worker reads, so when request B's
middleware runs while request A awaits the provider, A's retrieval uses B's tenant. Replace the global
with a dependency that resolves the tenant from the request (or a `contextvar` set per request) and
pass it into the retrieval call. Both are the same mistake in opposite directions: an expensive
shareable thing was made per request, and a per-request thing was made shared.

## Quiz
### Q: Which object belongs in request scope rather than application scope?
- [ ] The tokenizer used for counting prompt tokens
- [ ] The `httpx.AsyncClient` used to call the model provider
- [x] The database session and its transaction for the current call
- [ ] The parsed settings object with provider keys and limits
> A session carries a transaction and is not safe for concurrent use, so each request needs its own,
> derived from the application-scoped engine and torn down after the handler finishes.

### Q: With `uvicorn --workers 4`, what does "application-scoped" imply for a loaded model?
- [ ] One copy shared by all workers through shared memory
- [x] Four independent copies, one per worker process, each loaded in that worker's lifespan
- [ ] One copy in the master and lazy copies on first request
- [ ] The model is reloaded whenever a worker restarts a request
> Workers are separate processes with separate event loops and app instances; application scope means
> once per process, which is why large models usually move to a dedicated server.

### Q: Why is keeping the current user in a module-level variable a bug in an async FastAPI service?
- [ ] Module globals are read-only in async code
- [ ] FastAPI resets module globals between requests
- [x] Concurrent requests interleave on the loop and overwrite each other's value
- [ ] Module globals are not visible from dependencies
> Request-scoped identity stored in application scope is shared by every in-flight request in the
> process; a dependency or a context variable keeps it isolated per request.
