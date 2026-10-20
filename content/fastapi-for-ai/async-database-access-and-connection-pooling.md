---
id: async-database-access-and-connection-pooling
module: fastapi-for-ai
name: Async database access & connection pooling
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - fastapi-for-ai/sqlalchemy-sqlmodel
  - fastapi-for-ai/async-endpoints
  - fastapi-for-ai/lifespan
  - fastapi-for-ai/request-scoped-vs-application-scoped-resources
  - fastapi-for-ai/uvicorn-gunicorn-workers
  - fastapi-for-ai/concurrency-limits
  - backend-fundamentals/postgresql-transactions-and-isolation-levels
  - python-for-ai/asyncio
sources:
  - Official docs: SQLAlchemy (Asyncio Support; Connection Pooling)
  - Official docs: PostgreSQL (Connections and Authentication)
  - Official docs: PgBouncer
---

## TL;DR
An `async def` endpoint must never block the event loop, so database calls either go through an
async driver (asyncpg, psycopg 3, aiosqlite) behind SQLAlchemy's `AsyncEngine`, or stay in `def`
endpoints that run on the thread pool. Opening a Postgres connection costs a server-side process plus
TLS and authentication, and the server caps connections (100 by default), so each process keeps a
bounded pool and `processes × (pool_size + max_overflow)` must fit. The AI-specific rule: never hold a
pooled connection across a model call.

## Why it exists
Two separate problems hide behind "talk to the database from FastAPI". The first is blocking. A
synchronous driver call such as psycopg2's `cursor.execute()` inside an `async def` endpoint freezes
the event loop for the query's duration. At 20 ms per query and fifty concurrent requests (illustrative)
nothing else runs, including health checks, so the symptom is a readiness probe failing exactly when
traffic is highest. FastAPI offers two correct escapes: a plain `def` endpoint, which it runs on a
worker thread, or an async driver that yields to the loop while the server works. The thread-pool
route caps concurrency at the pool size (anyio's default limiter is 40) and makes mixing a database
read with an awaited model call awkward, which is why AI services usually take the async-driver route.

The second problem is connections. Every Postgres connection is a backend process on the server with
its own memory (a few megabytes each, order of magnitude), and establishing one means a TCP handshake,
usually TLS, and authentication, which together cost milliseconds to tens of milliseconds. The server
also refuses connections beyond `max_connections`, whose default is 100. A service that opens a
connection per request pays the handshake every time and, under a burst, storms the server into
"too many clients already". A pool solves both: keep a bounded set of connections open per process,
lend one out per unit of work, take it back when done. But a pool per process multiplied by many
processes and worker fleets is itself a capacity problem, and that arithmetic, not query speed, is what
usually bites first.

## How it works
### The driver layer
SQLAlchemy URLs name the driver: `postgresql+asyncpg://` (binary protocol, fastest, its own API) or
`postgresql+psycopg://` (psycopg 3 with async support, DB-API shaped); `sqlite+aiosqlite://` for tests.
SQLAlchemy's asyncio extension keeps its synchronous core and bridges to the async driver with
greenlets: ORM code runs inside a greenlet and switches out whenever the driver would block. The
consequence is that any implicit IO where you cannot `await`, such as accessing a lazy relationship or
an attribute expired by `commit()`, raises `MissingGreenlet`. So async sessions use eager loading
(`selectinload`, `joinedload`) and `expire_on_commit=False`.

### Pool mechanics
`create_async_engine(url, pool_size=10, max_overflow=5, pool_timeout=30, pool_recycle=1800,
pool_pre_ping=True)` configures the per-process pool. `pool_size` connections persist; up to
`max_overflow` extra are opened on demand and closed when returned (SQLAlchemy's defaults are 5 and
10). A checkout that waits longer than `pool_timeout` raises the famous `QueuePool limit of size N
overflow M reached, connection timed out`. `pool_pre_ping` sends a trivial query on checkout so a
connection killed by a failover is replaced instead of handed to you, at the cost of one round trip.
`pool_recycle` closes connections older than the given seconds, because cloud databases and network
appliances silently drop idle ones. A session checks a connection out on its first statement and
returns it on `commit()`, `rollback()` or `close()`.

### The per-request session
The idiomatic dependency is a `yield` that opens an `AsyncSession` from an `async_sessionmaker` and
closes it afterwards. The trap is what happens between: a session held open across
`await call_model(...)` holds its connection for the whole model call. With 15 connections per process
and an average 8-second model call, the process can serve at most about two requests per second before
checkouts time out, while the database sits idle. The fix is structural: read what you need, release
the connection (`commit()` or `close()`), call the model, then open a short second session to write.

### Sizing from hold time
The pool size a process needs follows from Little's law: connections busy on average = request rate ×
time each request holds a connection. A route doing 200 requests per second that holds a connection for
10 ms of queries keeps about 2 connections busy; give it headroom for bursts and 5 to 10 is plenty. The
same route holding its session across a 4-second model call needs 200 × 4 = 800 connections, which no
pool or server will give you. That is why hold time, not query speed or pool size, is the number to
measure and attack first: shrinking it shrinks the pool you need linearly, while raising the pool only
moves the queue from the pool to Postgres.

### Process and loop boundaries
The engine and its pool belong to one process and one event loop. Create them in the lifespan handler
rather than at import time: with gunicorn `--preload` a pool that has opened connections before the
fork hands the same sockets to every child and the protocol streams get corrupted, and asyncpg connections are bound to the loop that made
them, which is also why tests that spin up their own loop break with import-time engines. Call
`engine.dispose()` on shutdown.

### Capacity arithmetic
Four API processes with `pool_size=10, max_overflow=5` can hold 60 connections. Three ARQ workers with
a pool of 10 add 30. A migration job and a few operator shells add a handful. Postgres keeps a few
connections reserved for superusers (`superuser_reserved_connections`, 3 by default), so the budget
is 97, and 60 + 30 + 5 = 95 fits, barely. Add one replica of either tier and it does not. That is when
PgBouncer enters: application pools connect to PgBouncer, which multiplexes them onto a small server
pool. In transaction pooling mode a server connection is assigned per transaction, so anything that is
session-scoped breaks: `SET` session variables, advisory locks held across transactions,
`LISTEN/NOTIFY`, and prepared statements. asyncpg prepares statements implicitly and SQLAlchemy's
asyncpg dialect keeps its own prepared-statement cache on top, so with PgBouncer in transaction mode
disable both: `statement_cache_size=0` in `connect_args` and `prepared_statement_cache_size=0` on the
URL (SQLAlchemy's docs also suggest unique statement names via `prepared_statement_name_func`). The
alternative is a PgBouncer version with protocol-level prepared-statement support (1.21 and later,
enabled with `max_prepared_statements`).

### Concurrency inside one request
A session is not safe for concurrent use. `asyncio.gather(session.execute(a), session.execute(b))`
fails with an error about concurrent operations on one session. Run statements sequentially, or give
each task its own session, which also means its own connection.

## Example
Engine and session factory created in the lifespan, a standard per-request dependency for ordinary
routes, and a chat endpoint that returns its connection before the slow model call and opens a short
second session for the write.

```python
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = create_async_engine(
        "postgresql+asyncpg://app:pw@db/app",
        pool_size=10, max_overflow=5, pool_timeout=10, pool_recycle=1800, pool_pre_ping=True,
    )                                         # per process: 4 uvicorn workers -> up to 60 connections
    app.state.sessions = async_sessionmaker(app.state.engine, expire_on_commit=False)
    yield
    await app.state.engine.dispose()

app = FastAPI(lifespan=lifespan)

async def get_session(request: Request):      # for ordinary CRUD routes: one session per request
    async with request.app.state.sessions() as session:
        yield session

@app.post("/chat/{conv_id}")
async def chat(conv_id: int, body: Ask, request: Request):
    sessions = request.app.state.sessions
    async with sessions() as s:               # session 1: read, then RELEASE the connection
        history = (await s.scalars(select(Message).where(Message.conv_id == conv_id)
                                   .order_by(Message.id))).all()

    answer = await call_model(body.prompt, history)   # seconds; holds no database connection

    async with sessions() as s:               # session 2: short write
        s.add(Message(conv_id=conv_id, role="assistant", content=answer))
        await s.commit()
    return {"answer": answer}

# Ask, Message and call_model are the Pydantic model, ORM model and model client from the other lessons.
```

## Trade-offs
- An async driver lets one process keep hundreds of requests in flight, but every database touch must
  be awaited and the ORM's implicit lazy loading is gone; for a simple CRUD service with a team new to
  asyncio, `def` endpoints with a sync driver are less error-prone.
- Larger pools cut checkout waits, but they multiply across processes against a fixed
  `max_connections`; once the total exceeds the server, the answer is PgBouncer, not bigger pools.
- `pool_pre_ping` costs a round trip per checkout but eliminates "server closed the connection
  unexpectedly" after failovers; if every microsecond matters and `pool_recycle` is short, turn it off.
- Transaction-mode PgBouncer multiplexes thousands of client connections, but forbids session state
  and complicates prepared statements; session mode keeps the semantics but surrenders most of the
  multiplexing savings, since each client holds a server connection for its whole session.

## Pitfalls
- Holding a session across a model or HTTP call; the symptom is `QueuePool limit ... reached` under
  modest traffic while database CPU is near idle.
- A sync driver inside `async def`; the symptom is every route, including `/health`, stalling together
  whenever database-heavy traffic arrives.
- Lazy loading or `expire_on_commit=True` in async sessions; the symptom is `MissingGreenlet` raised
  from an innocent attribute access.
- Creating the engine at import time under gunicorn preload or in tests; the symptom is asyncpg
  errors such as "another operation is in progress" or "attached to a different loop".
- Sharing one session across `asyncio.gather`; the symptom is an error about concurrent operations on
  the session, or silently interleaved transactions.
- asyncpg behind transaction-mode PgBouncer with the statement cache on; the symptom is
  `prepared statement "__asyncpg_stmt_N__" does not exist` or "already exists" errors under load.

## When to use
Use the async driver path for any FastAPI service that mixes database access with awaited model or
HTTP calls, which is nearly every AI backend. Stay on sync drivers in `def` endpoints for small
internal tools or when the team has no asyncio experience. Do the capacity arithmetic before the first
load test: processes, pools, workers, reserved connections, and whether PgBouncer is needed. Measure
checkout wait time, pool saturation, connection count on the server, and the time each request holds a
connection. In interviews, expect to compute the pool budget, explain why holding a connection across
a model call is the classic failure, and describe what transaction pooling breaks.

## Role lens
- **Engineer:** Creates the engine in the lifespan, writes the session dependency, structures handlers
  so no connection is held across a model call, and tunes pool sizes from measured checkout waits.
- **Architect:** Owns the connection budget across API processes, workers and jobs, decides when
  PgBouncer is introduced and in which mode, and sets the no-session-across-LLM-call rule as policy.
- **FDE:** Diagnoses a customer's "database is slow" as pool exhaustion caused by long model calls,
  and proves it with pool metrics against an idle database.

## Check yourself
**Q:** A service runs 6 uvicorn workers, each with `pool_size=10, max_overflow=10`, against a Postgres
with default `max_connections`. Under load users see connection errors. Explain and fix it.
**A:** The arithmetic is 6 × (10 + 10) = 120 possible connections against a server allowing 100, of
which a few are reserved for superusers, so under load the overflow connections are refused with "too
many clients already" and surface as 500s. Short-term, cut the per-process pool so the total fits, for
example `pool_size=8, max_overflow=4` for 72, leaving headroom for workers and migrations. Then check
why the pool fills: if handlers hold sessions across model calls, restructuring them to release before
the slow call will drop real usage far below the cap. If the fleet must grow beyond the budget, put
PgBouncer in front in transaction mode, disable asyncpg's and SQLAlchemy's prepared-statement caches
(or use PgBouncer's protocol-level prepared-statement support), and audit for
session-scoped features such as `SET` and advisory locks that transaction pooling breaks.

## Quiz
### Q: Why does holding a session open across an awaited model call hurt, even though the code is fully async?
- [ ] The event loop is blocked while the model responds
- [x] The session keeps a pooled connection checked out for the whole call, so the pool empties while the database does nothing
- [ ] asyncpg closes idle connections after one second
- [ ] SQLAlchemy serialises all sessions in a process
> Async keeps the loop free, but the connection is a scarce resource held for seconds per request.
> Fifteen connections and eight-second calls cap a process at roughly two requests per second.

### Q: What does a `MissingGreenlet` error in an async SQLAlchemy app indicate?
- [ ] The greenlet package is not installed
- [ ] The engine was created on a different event loop
- [x] Implicit database IO was triggered outside an awaitable context, typically a lazy load or an attribute expired by commit
- [ ] The pool has been exhausted
> The async bridge can only switch to the driver inside an awaited operation. Lazy relationship access
> and post-commit refresh try to query synchronously, so use eager loading and `expire_on_commit=False`.

### Q: Which feature breaks when you put asyncpg behind PgBouncer in transaction pooling mode without changes?
- [ ] SELECT statements with JOINs
- [x] Implicit prepared statements, because the server connection can change between transactions
- [ ] Connection pooling in SQLAlchemy, which must be disabled
- [ ] TLS between the application and the database
> Transaction mode hands out a different server connection per transaction, so a statement prepared on
> one may not exist on the next. Disable the driver and dialect statement caches, or use PgBouncer's
> protocol-level prepared statement support.
