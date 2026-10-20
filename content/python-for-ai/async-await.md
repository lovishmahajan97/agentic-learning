---
id: async-await
module: python-for-ai
name: async / await
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - python-for-ai/asyncio
  - python-for-ai/async-cancellation-and-exception-propagation
  - python-for-ai/race-conditions-and-synchronization-primitives
  - python-for-ai/asyncio-vs-threads-vs-multiprocessing
  - python-for-ai/gil
  - python-for-ai/httpx
  - fastapi-for-ai/async-endpoints
  - serving-inference/streaming
sources:
  - Selivanov, 2015 — PEP 492: Coroutines with async and await syntax
  - Official docs: asyncio
---

## TL;DR
`async def` defines a coroutine, a function that can pause at `await` while it waits for I/O and
let other coroutines run on the same thread. An event loop schedules them. It exists because AI
workloads are dominated by waiting on slow network calls (model providers, vector stores, tools),
often many at once, and a single thread that interleaves thousands of waits is cheaper and simpler
than a thread per call. It does nothing for CPU-bound work, and one blocking call stalls everything.

## Why it exists
An LLM request takes on the order of seconds (illustrative; it varies with model and output
length). A retrieval-augmented agent might issue an embedding call, several vector-store queries,
a few tool calls and two or three model calls per user turn, and a batch evaluation might issue
thousands of independent calls. Nearly all of that time is spent waiting on a socket, with the CPU
idle. Sequential code serializes the waits: twenty independent one-second calls take twenty
seconds.

The classic answer is threads: one per in-flight call. It works, and `concurrent.futures` makes it
easy, but it scales badly for this shape of work. Each OS thread carries its own stack and kernel
scheduling state, so thousands of in-flight requests mean thousands of threads; shared state needs
locks; and the small CPU portions (JSON parsing, prompt assembly) contend on the GIL. Streaming
responses, where tokens arrive over seconds, would pin a thread per stream for the whole duration.

`async`/`await` takes a different route: a single thread, a queue of coroutines, and explicit pause
points. When a coroutine awaits a network response it yields control; the loop runs another
coroutine; when the socket becomes readable the first one resumes. Ten thousand waiting coroutines
are ten thousand small Python objects, not ten thousand threads. The design is cooperative *because*
that is what makes it cheap: there is no preemption, no kernel involvement, and no lock needed to
protect state between two `await`s. The price is discipline: anything that blocks without awaiting
freezes every other coroutine.

## How it works
### Coroutines, awaitables and the loop
Calling an `async def` function does not run it; it returns a coroutine object, a suspended
computation. `await coro` runs it until it either finishes (the `await` expression evaluates to the
return value) or hits an `await` on something not yet ready, at which point control returns to the
event loop. Under the hood a coroutine is built on the generator machinery: `await x` is roughly
`yield from x.__await__()`, and the coroutine's frame is kept alive between resumptions. Things you
can await are coroutines, `Task`s, `Future`s and any object implementing `__await__`.

The event loop (`asyncio.run(main())` creates one and runs it to completion) keeps a queue of ready
callbacks and asks the operating system's I/O multiplexer (epoll, kqueue, IOCP) which sockets are
ready. A network read registers interest in a socket and returns a `Future`; when the OS reports
readiness, the loop resolves the Future and reschedules the coroutine awaiting it. The loop runs
one callback at a time, which is why there is no data race between two `await` points and also why
a long computation inside a coroutine blocks every other one.

### A trace: two calls on one thread
Say tasks A and B each send a request and await a reply that takes 1 s. At t=0 the loop runs A
until it awaits the socket read; A's frame is parked with a pending Future and control returns to
the loop. The loop runs B the same way. Now nothing is ready, so the loop sleeps inside
`epoll`/`kqueue` with no CPU used. At t≈1 s both sockets become readable; the loop resolves both
Futures and resumes A, then B, each from exactly the line after its `await`. Total ≈1 s, not 2 s,
and at no moment did two pieces of Python run at once. That last fact is both the safety property
(no torn updates between awaits) and the hazard: if A had computed for 1 s instead of awaiting, B
would not even have *sent* its request until A finished.

### Function colouring
`await` is legal only inside `async def`, so a function that needs to await something must itself
become async, and so must its callers, up to `asyncio.run` or a framework that owns the loop. This
"colouring" is the cost of making suspension points explicit: you can see every place your code
can be interleaved, *because* each one is spelled `await`. The practical consequences are that an
async SDK cannot be called from plain sync code without starting a loop, a sync helper cannot
await, and libraries often ship both a sync and an async client (`OpenAI` and `AsyncOpenAI`,
`httpx.Client` and `httpx.AsyncClient`) rather than one that works in both worlds.

### Concurrency: tasks, gather, groups
`await a(); await b()` is sequential: `b` starts after `a` finishes. To overlap them, wrap each in a
`Task` with `asyncio.create_task(coro)`, which schedules it immediately, and await the tasks later.
`asyncio.gather(*coros)` does this for a list and returns results in order;
`return_exceptions=True` makes failures appear as values instead of raising the first.
`asyncio.TaskGroup` (3.11) is the structured form: tasks started inside `async with` are all
awaited at the block's end, and if one fails the others are cancelled and the failures are raised
together as an `ExceptionGroup`. Prefer it for fan-out where a partial result is useless.

### Limits and timeouts
Unbounded fan-out against a provider turns into a rate-limit storm. `asyncio.Semaphore(n)` with
`async with sem:` around the call caps in-flight requests at `n`, which is the knob that matches
your provider's concurrency allowance. It bounds concurrency, not rate: five slots with half-second
calls still allow about ten requests per second, so a requests- or tokens-per-minute quota needs a
token-bucket limiter alongside it. `async with asyncio.timeout(seconds):` (3.11; earlier,
`asyncio.wait_for`) cancels the awaited work when the budget expires and raises `TimeoutError`,
which is how you stop a hung stream from holding a slot forever.

### Async iteration and context managers
`async for chunk in stream:` consumes an async generator or iterator, awaiting each item; this is
the natural shape for token streaming, where each chunk arrives from the socket at its own time.
`async with client:` runs `__aenter__`/`__aexit__` that themselves await, which is how an
`httpx.AsyncClient` or an SDK client opens and closes its connection pool.

### What blocks the loop
`time.sleep`, `requests.get`, a synchronous database driver, a synchronous SDK client, heavy
`json.loads` on a huge payload, a CPU-bound embedding computation: none of these await, so the loop
cannot switch away. The symptom is global: every request's latency rises together, and a "fast"
endpoint becomes slow because a different endpoint blocked. Fix by using async libraries, by moving
blocking I/O to a thread with `await asyncio.to_thread(fn, ...)`, and by moving CPU work to a
process pool. The GIL, threads versus processes and cancellation semantics each have their own
lesson in this module; this one is about the syntax and the cooperative model they build on.

## Example
Twenty simulated model calls with a concurrency cap of five, a per-call timeout, references kept to
every task, and an async generator standing in for a token stream. `asyncio.sleep` plays the role
of network latency.

```python
import asyncio, random, time

MAX_INFLIGHT = 5
in_flight = peak = 0

async def llm_call(prompt: str, sem: asyncio.Semaphore) -> str:
    global in_flight, peak
    async with sem:                                    # at most MAX_INFLIGHT past this line
        in_flight += 1; peak = max(peak, in_flight)    # safe: no await between these two lines
        try:
            async with asyncio.timeout(2.0):           # per-call budget; raises TimeoutError
                await asyncio.sleep(random.uniform(0.2, 0.6))   # stands in for the network wait
            return f"answer to {prompt!r}"
        finally:
            in_flight -= 1

async def fan_out(prompts: list[str]) -> list[str | BaseException]:
    sem = asyncio.Semaphore(MAX_INFLIGHT)
    tasks = [asyncio.create_task(llm_call(p, sem)) for p in prompts]   # keep references
    return await asyncio.gather(*tasks, return_exceptions=True)

async def stream_tokens(text: str):
    for tok in text.split():
        await asyncio.sleep(0.02)                      # a real client awaits each chunk here
        yield tok

async def main() -> None:
    t0 = time.perf_counter()
    results = await fan_out([f"q{i}" for i in range(20)])
    print(f"{len(results)} results in {time.perf_counter() - t0:.2f}s, peak in-flight={peak}")
    # roughly: 20 results in ~1.7-1.9s, peak in-flight=5  (sequential ~8s)
    async for tok in stream_tokens("tokens arrive as the model produces them"):
        print(tok, end=" ", flush=True)
    print()

asyncio.run(main())
```

Replace `await asyncio.sleep(...)` with `time.sleep(...)` and the run takes the full sequential
time with `peak in-flight=1`: the blocking call never yields, so no other coroutine can start.

## Trade-offs
- One thread handles thousands of concurrent waits cheaply, but a single blocking call freezes all
  of them; the model flips to threads or processes when you cannot avoid blocking libraries.
- No preemption means no data races between `await`s and no locks for simple state, yet any state
  touched across an `await` can be interleaved and needs the same care as threaded code.
- Fan-out with `gather` collapses wall-clock time, but without a semaphore it collapses into
  provider rate limits and retries that cost more than the sequential version.
- Async SDKs and drivers are required end to end; a stack with one synchronous dependency in the
  request path gains little and complicates debugging, so sometimes a thread pool is the honest
  choice.
- Structured concurrency (`TaskGroup`) gives clean cancellation and error aggregation, but it
  forces all-or-nothing semantics; when partial results are valuable, `gather` with
  `return_exceptions=True` fits better.

## Pitfalls
- Calling a coroutine without `await` (`client.chat(...)` instead of `await client.chat(...)`);
  nothing runs, and the only hint is a `RuntimeWarning: coroutine ... was never awaited`, emitted
  when the unused coroutine object is garbage-collected (often right away, sometimes much later).
- Using a synchronous SDK client or `requests` inside an async endpoint; the symptom is every
  endpoint's p99 latency rising together under modest load.
- Creating tasks with `create_task` and discarding the result; the loop holds only a weak reference,
  so the task can be garbage-collected mid-execution and silently disappear.
- Firing `gather` over thousands of provider calls with no semaphore; the symptom is a burst of 429
  responses followed by a retry storm that can end up slower than running sequentially.
- Reading shared state, awaiting, then writing based on what you read (`if budget > cost: await
  call(); budget -= cost`); every task passes the check before any of them subtracts, and the
  symptom is a token budget or quota overspent by roughly the concurrency factor. Hold an
  `asyncio.Lock` across the check-and-act, or reserve before awaiting.
- Calling `asyncio.run()` from inside a running loop (notebooks, some frameworks); it raises
  `RuntimeError`, and the fix is to `await` directly or use the framework's loop.
- Catching `BaseException` or bare `except:` around awaits; it swallows `CancelledError` and
  produces tasks that cannot be cancelled and shutdowns that hang.

## When to use
Use `async`/`await` when the work is I/O-bound and concurrent: fan-out model calls, parallel tool
execution, streaming tokens to a client, serving many simultaneous chats from one process. Do not
use it for CPU-bound work (tokenization of large corpora, local embedding computation) or when your
critical dependencies are synchronous; use threads or processes there, or wrap the blocking parts
in `to_thread`. Measure event-loop lag (how late scheduled callbacks run) and in-flight counts; a
rising lag with low CPU points at a blocking call. Day to day, this is the shape of every FastAPI
endpoint, agent loop and evaluation harness you will write. In interviews, be ready to explain what
`await` actually does, why one `time.sleep` breaks everything, and how you bound concurrency
against a rate limit.

## Role lens
- **Engineer:** Writes agent loops and eval harnesses with `create_task`, `gather`, semaphores and
  timeouts, and keeps every call in the request path on an async client.
- **Architect:** Decides which services run on the event loop versus thread or process pools, and
  sets concurrency caps and rate limiters per provider so fan-out stays inside negotiated quotas.
- **FDE:** Finds the one synchronous call (a legacy auth client, a sync database driver) stalling a
  customer's async service and moves it to `to_thread` or an async driver.

## Check yourself
**Q:** A FastAPI service with async endpoints handles 50 requests per second comfortably, but after
a developer adds a feature that calls a legacy sync HTTP client, latency on *every* endpoint rises
under load, with the CPU mostly idle. Explain the mechanism and how you would fix and verify it.
**A:** The sync client blocks the event-loop thread for the whole request round trip. During that
time no other coroutine can run, so every in-flight request, including unrelated endpoints, waits.
CPU stays idle because the thread is blocked on a socket, not computing. The fix is to make the
call non-blocking: switch to an async client such as `httpx.AsyncClient`, or wrap the legacy call
in `await asyncio.to_thread(...)` so it runs in a worker thread while the loop continues. Verify by
measuring event-loop lag or by load-testing one endpoint while the slow one is exercised; after the
fix the unrelated endpoint's latency should no longer move.

## Quiz
### Q: What happens when a coroutine hits `await` on a network response that is not yet ready?
- [ ] The thread blocks until the response arrives
- [x] The coroutine suspends and the event loop runs other ready coroutines until the socket is readable
- [ ] A new thread is spawned to wait for the response
- [ ] The response is polled in a busy loop until it arrives
> `await` yields control to the loop, which uses the OS I/O multiplexer to learn when the socket is
> ready and then resumes the coroutine. No thread blocks and no busy-waiting occurs.

### Q: Why does a single `time.sleep(1)` inside one coroutine delay every other request in the process?
- [ ] `time.sleep` acquires the GIL exclusively
- [ ] asyncio runs each coroutine on its own thread and `time.sleep` joins them
- [x] The loop is cooperative and single-threaded; a call that never awaits never yields, so nothing else can run
- [ ] `time.sleep` cancels pending tasks
> Coroutines only switch at `await`. A blocking call holds the one thread the loop uses, so all
> scheduled work waits. `await asyncio.sleep(1)` yields instead and the problem disappears.

### Q: What is the purpose of wrapping each provider call in `async with asyncio.Semaphore(5)`?
- [ ] To make the calls run in parallel on five CPU cores
- [ ] To retry each call up to five times
- [x] To cap the number of in-flight calls at five so fan-out cannot flood the provider all at once
- [ ] To guarantee results are returned in order
> A semaphore bounds concurrency: the sixth call waits until one of the five completes. `gather`
> alone would start all calls at once and trigger 429 responses. Note it limits calls in flight, not
> requests per minute; a requests- or tokens-per-minute quota needs a rate limiter on top.
