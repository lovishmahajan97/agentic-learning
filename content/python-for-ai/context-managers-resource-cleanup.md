---
id: context-managers-resource-cleanup
module: python-for-ai
name: Context managers / resource cleanup
kind: pattern
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - python-for-ai/generators
  - python-for-ai/async-cancellation-and-exception-propagation
  - python-for-ai/decorators
  - python-for-ai/httpx
  - python-for-ai/pytorch
  - fastapi-for-ai/lifespan
  - fastapi-for-ai/async-database-access-and-connection-pooling
  - fastapi-for-ai/graceful-shutdown-and-worker-draining
sources:
  - PEP 343 — The "with" Statement
  - PEP 492 — Coroutines with async and await syntax (async with)
  - Official docs: contextlib
---

## TL;DR
A context manager pairs setup with guaranteed teardown: `with` runs `__enter__`, your block, then
`__exit__` whether the block returns, raises or is cancelled. It exists because the resources an AI
service holds (HTTP connection pools, streams, DB sessions, locks, temp files, GPU memory modes) leak
quietly when cleanup depends on every code path remembering it. `async with` and `contextlib` extend the
pattern to async code, generators and dynamic sets of resources.

## Why it exists
Resources are acquired on the happy path and leaked on the unhappy one. A streaming LLM call opens a
connection; the client disconnects, a parser raises on malformed JSON, or a timeout cancels the task,
and the line that closes the stream never runs. Each leak is small, but an AI backend does this
thousands of times an hour: the connection pool fills, new requests wait for a free connection, and
latency climbs until the service looks down. GPU code has the same shape: forget to restore a mode or
release a buffer and memory creeps up.

The naive fixes fall short. Calling `close()` at the end of the function misses every early return and
exception. `try/finally` is correct, but it must be repeated at every call site, nested for each
additional resource, and written correctly every time. Relying on `__del__` or garbage collection gives
no timing guarantee (reference cycles delay it, other interpreters do not refcount, and async cleanup
cannot run from a finaliser at all).

PEP 343 packaged `try/finally` into the resource itself. The object that knows how to acquire also knows
how to release, and the `with` statement guarantees the release runs. Callers write one line and cannot
get the cleanup wrong.

## How it works
### The protocol
`with expr as x:` evaluates `expr` to a manager, calls `manager.__enter__()` and binds its return value to
`x`, runs the block, then always calls `manager.__exit__(exc_type, exc, tb)`. On a normal exit all three
are `None`. On an exception they describe it, and if `__exit__` returns a truthy value the exception is
**suppressed**, *because* some managers (`contextlib.suppress`) exist precisely to do that; everyone else
must return `None` or `False`. Multiple managers in one statement exit in reverse order of entry, so
dependencies unwind correctly.

### async with
`async with` calls `await __aenter__()` and `await __aexit__(...)`, *because* closing a network
resource is itself I/O: flushing a stream, returning a connection, sending a close frame. `httpx.AsyncClient`,
async DB sessions and `asyncio.Lock` are all async context managers. Cancellation arrives as
`CancelledError` and passes through `__aexit__` like any exception, which is why `async with` is the
reliable way to release resources in cancellable code. Keep exit code short and bounded, because it can
itself be interrupted during shutdown.

### contextlib: managers without classes
`@contextmanager` turns a generator into a manager: code before `yield` is setup, the yielded value is
bound by `as`, code after is teardown. The exception from the block is thrown in at the `yield`, so
teardown must sit in `finally` or it is skipped on error. `@asynccontextmanager` is the async version, and
it is what FastAPI's `lifespan` expects. `ExitStack`/`AsyncExitStack` manage a number of resources known
only at runtime and unwind whatever was entered if a later setup step fails. Smaller helpers: `closing()`
for objects with only `close()`, `suppress()`, `nullcontext()` for optional managers, and `aclosing()` for
async generators.

### Context managers that are not about I/O
`torch.no_grad()` and `torch.inference_mode()` switch autograd off for a block and restore it after.
`decimal.localcontext()` and temporary environment patches in tests follow the same pattern: change state,
guarantee restoration. The principle generalises to anything with a "before" and a mandatory "after".

## Example
```python
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

class Client:                              # stands in for httpx.AsyncClient, a DB pool
    def __init__(self, name: str): self.name = name
    async def aclose(self): print(f"{self.name}: closed")

@asynccontextmanager
async def open_client(name: str):
    client = Client(name)
    print(f"{name}: opened")
    try:
        yield client
    finally:
        await client.aclose()              # runs on success, error or cancellation

@asynccontextmanager
async def lifespan(app=None):              # FastAPI calls lifespan(app) with this shape
    async with AsyncExitStack() as stack:
        llm = await stack.enter_async_context(open_client("llm-http"))
        db = await stack.enter_async_context(open_client("vector-db"))
        yield {"llm": llm, "db": db}

async def main() -> None:
    try:
        async with lifespan() as deps:
            print("serving with", sorted(deps))
            raise RuntimeError("crash mid-request")
    except RuntimeError as exc:
        print("propagated:", exc)

asyncio.run(main())
```
Output: `llm-http: opened`, `vector-db: opened`, `serving with ['db', 'llm']`, then `vector-db: closed`
and `llm-http: closed` in reverse order, then `propagated: crash mid-request`. The error is not
suppressed, both clients are closed, and the dependency opened last is closed first. The clients are
created once per process, not once per request, which is the point of putting them in a lifespan.

## Trade-offs
- `with` guarantees cleanup at a lexically visible point, but ties resource lifetime to a block; when a
  resource must outlive the function (a pool shared by all requests), move it to an app-level lifespan.
- `@contextmanager` is far shorter than a class, but forgets cleanup on error if you omit `try/finally`;
  a class with `__exit__` is clearer when the manager carries state or is reused often.
- `ExitStack` handles dynamic resource sets, at the cost of less obvious control flow; for two or three
  fixed resources a single multi-item `with` statement is easier to read.
- Suppressing exceptions in `__exit__` can turn a crash into graceful degradation, but hides real bugs
  unless the manager suppresses only specific, expected exception types.

## Pitfalls
- Creating an `httpx.AsyncClient` inside each request with `async with`; correct cleanup but no
  connection reuse, which shows up as high latency from repeated TLS handshakes.
- Returning a lazy stream or generator from inside a `with` block; the resource closes on return, and
  the consumer later fails with "stream closed" or reads nothing.
- Writing `@contextmanager` teardown after `yield` without `finally`; it runs in tests on the happy path
  and silently does not run when the block raises.
- An `__exit__` that returns `True` by accident (e.g. returning the result of a cleanup call); exceptions
  vanish and callers see `None` results with no traceback.
- Relying on `__del__` to close connections; the symptom is "unclosed client session" warnings and
  pool exhaustion that varies with garbage-collection timing.

## When to use
Use a context manager for anything with an acquire-and-release pair: clients and connection pools, streams,
DB sessions and transactions, locks and semaphores, temp files and directories, tracing spans, and
temporary mode changes such as `torch.inference_mode()`. Prefer the library's own manager over manual
`close()` calls. Give long-lived resources a lifespan-scoped manager and short-lived ones a block-scoped
manager. Measure by watching open connections, pool wait time and file descriptors under load and after
error injection; if they climb after errors, a path is skipping cleanup. In interviews, expect to explain
`__exit__`'s arguments and return value and write a `@contextmanager` with a correct `finally`.

## Role lens
- **Engineer:** Wraps every client, stream, session and lock in `with`/`async with`, and writes
  `@asynccontextmanager` helpers with cleanup in `finally`.
- **Architect:** Defines resource lifetimes, process-scoped pools in a lifespan versus per-request sessions,
  so connection counts and shutdown behaviour are predictable.
- **FDE:** Traces a customer's "connection pool exhausted" incident to a stream opened without a context
  manager on an error path, and fixes it under their load.

## Check yourself
**Q:** A helper does `with httpx.stream("POST", url, json=body) as r: return r.iter_lines()`, and callers loop over
the result. It fails in production with errors about a closed stream. Why, and how should it be written?
**A:** `return` leaves the `with` block, so `__exit__` runs immediately and closes the response before
anyone iterates; `iter_lines()` is lazy, so the caller iterates a closed stream. The context manager must
stay open for as long as the data is consumed. Either make the helper a generator that `yield`s lines
inside the `with` block, so the stream closes when iteration ends, with consumers using `closing()` or
`aclosing()` for early exit, or have the helper itself be a context manager that yields the response so
the caller controls its lifetime with `with`.

## Quiz
### Q: What happens if an `__exit__` method returns `True` while the block raised an exception?
- [ ] The exception is re-raised after cleanup completes
- [x] The exception is suppressed and execution continues after the `with` statement
- [ ] Python raises a TypeError because `__exit__` must return None
- [ ] The block is retried once before the exception propagates
> A truthy return from `__exit__` tells the interpreter the exception was handled. That is how
> `contextlib.suppress` works, and why accidental truthy returns hide bugs.

### Q: Why does an async HTTP client use `async with` rather than plain `with`?
- [ ] Because plain `with` cannot be used inside an `async def` function
- [ ] Because `async with` runs the block in a separate thread
- [x] Because opening and closing it involve I/O that must be awaited without blocking the event loop
- [ ] Because `async with` suppresses CancelledError during cleanup
> Releasing connections and closing streams involve network I/O, so `__aenter__`/`__aexit__` are
> coroutines. Ordinary `with` remains usable inside async code for purely synchronous resources.

### Q: A `@contextmanager` function has setup, `yield conn`, then `conn.close()` with no try/finally. When does the connection leak?
- [ ] Never, because the decorator always runs code after the yield
- [ ] Only when the generator is garbage-collected before the block starts
- [ ] Only when the block returns early with `return`
- [x] When the block raises, because the exception is thrown in at the yield and skips the close
> The exception from the block is raised inside the generator at the `yield`, so code after it runs
> only if it is in `finally`. An early `return` exits normally and does run it.
