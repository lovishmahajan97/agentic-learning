---
id: async-await
module: python-for-ai
name: async / await
kind: concept
difficulty: core
est_minutes: 16
related:
  - python-for-ai/asyncio
  - python-for-ai/async-cancellation-and-exception-propagation
  - python-for-ai/asyncio-vs-threads-vs-multiprocessing
  - python-for-ai/gil
  - python-for-ai/generators
  - python-for-ai/httpx
  - fastapi-for-ai/async-endpoints
  - node-ts-for-ai/event-loop-and-microtask-queue
sources:
  - Selivanov, 2015 — PEP 492: Coroutines with async and await syntax
  - Selivanov, 2016 — PEP 525: Asynchronous Generators
  - Official docs: asyncio
---

## TL;DR
`async def` declares a coroutine: a function that can pause itself at `await` points and let a
single-threaded event loop run other coroutines while it waits for I/O. It exists because LLM and
embedding calls spend almost all their time waiting on the network, and one thread that juggles
hundreds of waiting calls is cheaper and easier to reason about than hundreds of threads. The rule
that makes it work is also its hazard: a coroutine only yields at `await`, so any blocking call
freezes everything.

## Why it exists
An LLM request takes on the order of hundreds of milliseconds to several seconds, during which your
process does nothing but wait for bytes. A retrieval step that embeds ten chunks sequentially pays
ten round-trips back to back; a chat server with fifty active users would need fifty things waiting
at once. Sequential code cannot do that. Threads can, and for a handful of concurrent calls they are
fine, but each OS thread carries its own stack, every shared structure needs a lock, preemption can
interleave two threads anywhere, and the GIL means they never run Python code in parallel anyway.
Thread pools are a workable patch for a few dozen waits, not a model for thousands.

The idea behind `async`/`await` is cooperative multitasking: instead of the OS interrupting threads
at arbitrary points, a coroutine announces exactly where it is willing to pause (`await`), and a
scheduler in the same thread runs another coroutine until the awaited I/O completes. Because switches
happen only at those marked points, the code between two `await`s runs atomically with respect to
other coroutines, which eliminates most of the locking that threads require. Memory per waiting
coroutine is a small Python object rather than a thread stack, so tens of thousands of in-flight
calls are routine. Streaming token output maps naturally onto the same mechanism as `async for`.

The price is that the model is only as good as its cooperation: a `time.sleep`, a synchronous
`requests.get`, or a CPU-heavy tokenisation loop never reaches an `await`, so the loop cannot run
anyone else, and every user's latency spikes together. Understanding that mechanism is the whole
lesson.

## How it works
### Coroutine objects
Calling an `async def` function does not run it; it returns a *coroutine object*, a suspended
computation. It runs only when something drives it: `await`ing it from another coroutine, wrapping
it in a Task, or handing it to `asyncio.run()` at the top level. A coroutine that is created and
never driven triggers `RuntimeWarning: coroutine ... was never awaited`, which is the single most
common async bug and a sign that an `await` is missing.

### What `await` does
`await x` requires `x` to be *awaitable*: a coroutine, a Task, a Future, or any object whose
`__await__` returns an iterator. Mechanically, coroutines are built on the generator machinery
(PEP 492): `await` delegates to the awaitable's iterator, and when that iterator yields, the yield
propagates up through every enclosing `await` to the event loop. The chain always bottoms out in a
Future owned by the loop, created by a transport when it registers interest in a socket with the
operating system's readiness API (`epoll`, `kqueue`, IOCP). The loop then runs other ready
coroutines. When the socket becomes readable, the loop sets the Future's result and resumes the
waiting coroutine by calling `send()` on it, which continues execution right after the `await` with
the value in hand. Exceptions travel the same path: a failure inside an awaited call is raised at
the `await` expression exactly as if it were a normal function call.

### Sequential versus concurrent awaiting
`await` runs the awaited thing to completion before continuing, so `for p in prompts: await
complete(p)` is strictly sequential and gains nothing. Concurrency requires scheduling several
coroutines as *Tasks* so the loop can interleave them: `asyncio.gather(*coros)`, a `TaskGroup`, or
`asyncio.create_task`. The `asyncio` lesson covers those APIs; what matters here is the distinction
between "await inline" (one at a time) and "schedule, then await the results" (overlapped).

### `async with` and `async for`
Resources whose setup or teardown performs I/O (an HTTP client, a database session) implement
`__aenter__`/`__aexit__` as coroutines and are used with `async with`, so opening and closing can
yield to the loop. Sources that produce values over time (a streaming LLM response) implement
`__anext__` and are consumed with `async for`. An `async def` containing `yield` is an *asynchronous
generator* (PEP 525), the simplest way to write such a stream; each `await` inside it can pause the
consumer without blocking the loop.

### One loop per thread, cooperative only
The event loop lives on one thread and runs one coroutine at a time; there is no parallelism of
Python code. CPU-bound work must leave the loop via `await asyncio.to_thread(fn)` (a thread, useful
when the work releases the GIL or is short) or a process pool. Synchronous code cannot `await`, so
async "colours" the call chain: an async endpoint needs an async HTTP client, an async DB driver and
async-aware libraries down to the socket, or else it is blocking in disguise.

### Error and cancellation semantics in brief
Exceptions propagate through `await` normally. A Task can be cancelled, which raises
`CancelledError` *inside* the coroutine at its current `await`; that is a `BaseException` so generic
`except Exception` handlers do not swallow it. The dedicated cancellation lesson goes deeper.

## Example
The same ten fake provider calls awaited sequentially and concurrently, a streamed response consumed
with `async for`, and a demonstration that calling a coroutine runs nothing. `asyncio.sleep` stands in
for the network wait of an `httpx.AsyncClient` request; the mechanics are identical.

```python
import asyncio, time

async def complete(prompt: str, latency: float = 0.3) -> str:
    await asyncio.sleep(latency)        # stands in for `await client.post(...)`; loop runs others
    return f"answer to {prompt!r}"

async def stream(prompt: str):          # async generator: an LLM token stream looks like this
    for tok in ["The", " KV", " cache", "..."]:
        await asyncio.sleep(0.05)
        yield tok

async def main() -> None:
    prompts = [f"q{i}" for i in range(10)]

    t = time.perf_counter()
    seq = [await complete(p) for p in prompts]              # await inline: one after another
    print(f"sequential: {time.perf_counter() - t:.2f}s")    # ~3.0s

    t = time.perf_counter()
    conc = await asyncio.gather(*(complete(p) for p in prompts))   # ten tasks, one thread
    print(f"concurrent: {time.perf_counter() - t:.2f}s")    # ~0.3s
    assert seq == list(conc)

    t = time.perf_counter()
    async for tok in stream("q"):
        print(tok, end="", flush=True)                      # first token at ~50ms, not ~200ms
    print(f"  streamed in {time.perf_counter() - t:.2f}s")

    coro = complete("never")                                # creating a coroutine runs nothing
    print(type(coro).__name__)                              # coroutine
    coro.close()                                            # silence "was never awaited"

    # time.sleep(1) here would stall every other coroutine on this loop for a full second

asyncio.run(main())
```

## Trade-offs
- One thread can hold thousands of waiting calls cheaply, but it offers no CPU parallelism; when the
  bottleneck is tokenisation, parsing or local inference, a process pool or threads around a
  GIL-releasing library are needed instead.
- Switches only at `await` remove most locking, yet they make a single blocking call catastrophic
  for everyone; threads degrade more gracefully when one call misbehaves.
- Async composes naturally with streaming and fan-out, at the cost of colouring the whole call chain;
  a codebase with many synchronous libraries may be cheaper to run on a thread pool.
- Unbounded fan-out is one `gather` away, which is great for throughput until the provider's rate
  limit answers with 429s; a semaphore or bounded task group is the flip side of the same power.

## Pitfalls
- Forgetting `await`: the "result" is a coroutine object, nothing runs, and the log shows
  `RuntimeWarning: coroutine 'complete' was never awaited`.
- Calling `time.sleep`, `requests.get` or a synchronous SDK client inside a coroutine; the symptom is
  every request's latency rising in lockstep and one core at 100% while the rest idle.
- Awaiting inside a `for` loop and believing it is concurrent; the symptom is total time equal to the
  sum of the calls rather than the maximum.
- Creating tasks with `create_task` and dropping the reference; the loop keeps only weak references,
  so the task can be garbage collected mid-flight and its work silently vanishes.
- Calling `asyncio.run()` inside Jupyter or another running loop; the symptom is `RuntimeError:
  asyncio.run() cannot be called from a running event loop`, and the fix is a top-level `await`.
- Catching `Exception` around an `await` and continuing; cancellation is a `BaseException` and
  passes through, but a handler that catches `BaseException` will block cancellation and hang shutdown.

## When to use
Choose async when the workload is dominated by waiting on the network: LLM and embedding calls,
vector store queries, webhooks, streaming to clients, agent tool fan-out. Stay synchronous for
scripts and batch jobs with little concurrency, and move CPU-heavy steps out of the loop. Measure
with the loop's debug mode, which logs callbacks slower than its threshold, and by comparing wall
time against the slowest single call: a fan-out whose total equals the sum is sequential by mistake.
Day to day it shows up as FastAPI endpoints, `httpx.AsyncClient`, async SDK clients and `async for`
over token streams; in interviews, expect "what happens if I call `time.sleep` in a handler" and
"why is awaiting in a loop not concurrent".

## Role lens
- **Engineer:** Writes async endpoints and clients, fans out retrieval and tool calls with bounded
  concurrency, and streams tokens with async generators without blocking the loop.
- **Architect:** Decides which services run on an event loop versus worker pools, sets concurrency
  limits per provider, and requires async-compatible drivers across the stack.
- **FDE:** Diagnoses a customer's "everything got slow at once" incident as a blocking call on the
  loop, usually a synchronous SDK or logging handler dropped into an async service.

## Check yourself
**Q:** An async FastAPI service calls an embedding API and then an LLM. p50 latency is fine, but under
load p99 explodes and one CPU core sits at 100% while others idle. What is happening and how would you
confirm and fix it?
**A:** Something on the event loop is blocking: a synchronous HTTP client, CPU-heavy work such as
tokenising or chunking large documents, or large JSON serialisation. Because the loop is one thread,
every blocking stretch delays every other in-flight request, which inflates the tail far more than
the median; the single busy core is the loop thread. Confirm by enabling asyncio debug mode, which
logs slow callbacks, or by sampling the loop thread with a profiler to see non-`await` frames
dominating. Fix by replacing sync clients with async ones, moving CPU work to `asyncio.to_thread` or
a process pool, and bounding fan-out with a semaphore so the provider is not flooded. Then re-measure
p99 against the slowest single upstream call.

## Quiz
### Q: What happens when you call an `async def` function without `await`?
- [ ] It runs to completion synchronously and returns its value
- [x] It returns a coroutine object and executes nothing until something drives it
- [ ] It starts running in a background thread immediately
- [ ] It raises `TypeError` because coroutines cannot be called directly
> Calling creates a suspended coroutine. Only `await`, a Task, or `asyncio.run()` runs it, which is why
> a forgotten `await` produces a "never awaited" warning rather than an error at the call site.

### Q: Why does a single `time.sleep(1)` inside one request handler slow down every concurrent request?
- [ ] Because `time.sleep` acquires the GIL for the full second
- [x] Because the event loop is one thread and only switches at `await`; a blocking call never yields, so no other coroutine can run
- [ ] Because `asyncio` serialises all handlers through a global lock
- [ ] Because the sleeping handler holds the shared HTTP connection pool
> Cooperative scheduling means a coroutine keeps the thread until it awaits. `time.sleep` is not an
> awaitable, so the loop is stuck; `await asyncio.sleep(1)` yields and lets others proceed.

### Q: Inside a coroutine, how does `[await f(p) for p in ps]` differ from `await asyncio.gather(*(f(p) for p in ps))`?
- [ ] They are equivalent; the comprehension is just shorter
- [ ] The comprehension runs the calls in parallel threads, `gather` in one thread
- [x] The comprehension awaits each call before starting the next (sequential); `gather` schedules all as tasks so their waits overlap
- [ ] `gather` returns results in completion order, the comprehension in input order
> `await` inline means "finish this before continuing", so the comprehension sums the latencies.
> `gather` wraps each coroutine in a task and the loop interleaves them, so total time approaches the
> slowest call; results are still returned in input order.
