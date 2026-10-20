---
id: asyncio
module: python-for-ai
name: asyncio
kind: concept
difficulty: core
est_minutes: 16
related:
  - python-for-ai/async-await
  - python-for-ai/async-cancellation-and-exception-propagation
  - python-for-ai/race-conditions-and-synchronization-primitives
  - python-for-ai/asyncio-vs-threads-vs-multiprocessing
  - python-for-ai/httpx
  - fastapi-for-ai/async-endpoints
  - node-ts-for-ai/event-loop-and-microtask-queue
sources:
  - Official docs: asyncio
  - PEP 3156 — Asynchronous IO Support Rebooted: the "asyncio" Module
  - PEP 492 — Coroutines with async and await syntax
---

## TL;DR
An AI service spends almost all of its wall-clock time waiting: on an LLM provider, a vector database,
a tool endpoint. asyncio lets one thread interleave thousands of those waits by running coroutines on
an event loop that only resumes a coroutine when the thing it awaited is ready. You get high
concurrency for I/O at near-zero per-task cost, in exchange for a strict contract: nothing on the loop
may block, or everything stalls.

## Why it exists
Take a chat backend that calls a model with a 3-second median response and streams it back. A
synchronous handler holds its thread for those 3 seconds doing nothing but waiting on a socket. Serving
1,000 concurrent chats that way needs 1,000 threads: each costs a kernel object, a reserved stack
(illustratively, hundreds of KB to a few MB of address space), and scheduler time, and under the GIL
they cannot even run Python in parallel. Most of that machinery exists only to wait.

The naive fix, a bigger thread pool, moves the ceiling but not the shape of the cost. The sharper
observation is that a waiting handler needs almost no state: a socket to watch and the point in the
code to resume from. The operating system already offers a cheap way to watch many sockets at once
(`epoll` on Linux, `kqueue` on macOS, IOCP on Windows). What Python lacked was a way to suspend a
function mid-body, keep its locals, and resume it when the socket is ready. Generators had that
ability since `yield`; PEP 492 gave it dedicated syntax (`async def` / `await`), and asyncio is the
scheduler that connects those suspended functions to the OS readiness notifications. Callback-style
frameworks solved the same problem earlier, but the code read as a graph of callbacks; coroutines keep
it reading top to bottom while behaving the same way underneath.

## How it works
### The three objects
- A **coroutine** is what calling an `async def` function returns: a suspended frame that has not
  started. It runs only when something drives it.
- A **Future** is a one-shot result holder with callbacks: "when this is set, call these". Socket
  readiness, timers and thread results all surface as futures.
- A **Task** wraps a coroutine in a future and drives it. Each time the coroutine `await`s something
  that is not ready, the task registers a callback on that future and returns control to the loop.
  When the future resolves, the callback reschedules the task, which resumes the coroutine exactly
  where it stopped.

### One iteration of the loop
The event loop is a single-threaded `while True`:
1. Compute how long until the earliest timer (`asyncio.sleep`, timeouts).
2. Ask the OS selector for sockets that are readable or writable, waiting at most that long. This is
   the only place the thread blocks, and it blocks on behalf of *all* tasks at once.
3. For each ready socket or expired timer, resolve the corresponding future, which appends the waiting
   task's step callback to the ready queue.
4. Drain the ready queue in FIFO order (`loop.call_soon` order), running each task until its next
   `await`.

Step 4 is why asyncio is called *cooperative*: a task runs until it voluntarily yields at an `await`
on a not-yet-ready future. Awaiting an already-complete future, or a plain coroutine that never touches
I/O, does not yield at all; `await asyncio.sleep(0)` is the explicit "let others run" hint.

### Design choices and their reasons
- **Single thread** *because* it removes data races between awaits: between two `await`s, no other
  task can touch your state. That is also why a blocking call (`time.sleep`, `requests.get`, a
  CPU-heavy loop) freezes every task; there is no other thread to keep the loop turning.
- **Explicit `await` points** *because* they make interleaving visible in the source. A sync-looking
  line can never secretly switch tasks, which is the property that makes most asyncio code reasonable
  without locks.
- **`create_task` schedules rather than runs** *because* the creator should continue; the new task
  starts on a later loop iteration. `asyncio.gather(*coros)` wraps each coroutine in a task and
  returns their results in order; `asyncio.TaskGroup` (3.11+) does the same with structured
  cancellation.
- **Blocking work is pushed off the thread** with `asyncio.to_thread(fn, *args)` (a
  `ThreadPoolExecutor` under the hood) or `loop.run_in_executor(process_pool, fn)`, and the result
  comes back as a future the loop can await. The loop stays responsive; the work is still serialized by
  the GIL if it is pure Python.

### Entry and exit
`asyncio.run(main())` creates a fresh loop, runs `main` as a task, then cancels any tasks still
pending, finalizes async generators, and closes the loop. It cannot be called from inside a running
loop (Jupyter already runs one; use `await main()` there). Debug mode (`PYTHONASYNCIODEBUG=1` or
`asyncio.run(..., debug=True)`) logs coroutines that were never awaited and callbacks that hog the
loop longer than `loop.slow_callback_duration` (100 ms by default), which is the first tool for finding
accidental blocking.

## Example
Twenty chat completions fanned out concurrently, sharing one HTTP connection pool, with a blocking
tokenizer step moved to a thread so it cannot stall the loop. Point `API` at any OpenAI-compatible
server (a local vLLM or Ollama instance works).

```python
import asyncio, time
import httpx

API = "http://localhost:8000/v1/chat/completions"
HEADERS = {"Authorization": "Bearer local"}

async def ask(client: httpx.AsyncClient, question: str) -> str:
    body = {"model": "small-chat", "messages": [{"role": "user", "content": question}]}
    r = await client.post(API, headers=HEADERS, json=body, timeout=60)   # the await is where we yield
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def count_tokens(text: str) -> int:
    time.sleep(0.05)                      # pretend this is a slow, blocking tokenizer
    return len(text.split())

async def main(questions: list[str]) -> None:
    async with httpx.AsyncClient() as client:          # one connection pool for every call
        t0 = time.perf_counter()
        answers = await asyncio.gather(*(ask(client, q) for q in questions))
        # blocking work goes to a thread so the loop keeps serving other tasks meanwhile
        counts = await asyncio.gather(*(asyncio.to_thread(count_tokens, a) for a in answers))
        print(f"{len(questions)} calls in {time.perf_counter() - t0:.2f}s, {sum(counts)} tokens")

asyncio.run(main([f"Summarise item {i} in one line." for i in range(20)]))
```

Sequentially the twenty calls would take roughly twenty times one call's latency; with `gather` they
take about the slowest single call plus a little scheduling overhead, because all twenty sockets are
waited on in one selector call.

## Trade-offs
- Thousands of concurrent waits cost kilobytes each instead of a thread apiece, but the moment the work
  is CPU-bound Python the single loop thread becomes the bottleneck and threads or processes win.
- Explicit await points make interleaving auditable, but "function colour" is contagious: an async call
  chain cannot call a blocking library without `to_thread`, so library choice (httpx vs requests, an
  async database driver vs a sync one) is dictated by the loop.
- Cooperative scheduling has no preemption overhead and needs no locks for most state, but one
  misbehaving task (a tight loop, a sync HTTP call) degrades every request on the loop, where a thread
  pool would only lose one worker.
- Tracebacks and profilers see a flat sequence of task steps rather than a call stack, which is harder
  to read than threaded code until you learn asyncio debug mode and named tasks.

## Pitfalls
- Calling a blocking function inside a coroutine (`time.sleep`, the sync OpenAI client,
  `pandas.read_csv` on a large file). Symptom: p99 latency climbs under load while CPU sits idle, and
  debug mode logs "Executing <Task ...> took 2.3 seconds".
- Forgetting to `await` a coroutine. Nothing runs; the symptom is `RuntimeWarning: coroutine 'x' was
  never awaited` and a feature that silently does nothing.
- Fire-and-forget `asyncio.create_task(...)` without keeping a reference. The loop holds tasks weakly,
  so the task can be garbage-collected mid-flight; the symptom is background work that sometimes
  vanishes. Store tasks in a set and discard on completion, or use a `TaskGroup`.
- Calling `asyncio.run` from code that is already inside a loop (a FastAPI handler, a notebook).
  Symptom: `RuntimeError: asyncio.run() cannot be called from a running event loop`.
- Treating `gather` as a limiter. Fanning out 5,000 embedding calls at once floods the provider and
  your own sockets; the symptom is a wave of 429s and connection errors. Bound it with a `Semaphore`.

## When to use
Reach for asyncio when the program's time is dominated by waiting on many independent network
operations: LLM and embedding calls, tool and MCP servers, vector stores, streaming responses to many
clients. That describes most of an AI service, which is why FastAPI, httpx, the OpenAI and Anthropic
SDKs and the MCP SDK all expose async APIs. Do not reach for it to speed up CPU-bound code; it will
not, and it makes that code harder to run. Measure event-loop lag (time between scheduling a callback
and it running) and the number of pending tasks; both tell you whether the loop is healthy. In
interviews, expect to explain what happens at an `await`, why a blocking call hurts every request, and
when you would move work to a thread or process instead.

## Role lens
- **Engineer:** Writes async handlers and clients, fans out model and tool calls with `gather` or
  `TaskGroup`, and keeps blocking work off the loop with `to_thread`.
- **Architect:** Decides which services are async-native and where the sync/async boundaries sit, so
  one blocking dependency cannot stall a whole worker process.
- **FDE:** Diagnoses a customer's "slow under load but CPU is idle" by finding the blocking call on
  their loop, often a sync SDK inside an async route.

## Check yourself
**Q:** A FastAPI service declares its chat route `async def`, and inside it calls the provider with a
synchronous SDK client. At ten concurrent users p99 latency is about thirty seconds while CPU sits at
five percent. Explain the mechanism and give two fixes with their trade-offs.
**A:** The route runs on the event loop thread, and the sync client blocks that thread for the whole
provider round-trip, so every other request on the worker waits in line: with ten users and a
three-second call, the last one waits around thirty seconds, which matches the symptom, and CPU is idle
because the thread is parked on a socket. Fix one: switch to the SDK's async client so the call becomes
an `await` and the loop keeps serving others; this is the right default but requires async-compatible
code throughout the call chain. Fix two: keep the sync client but call it via
`await asyncio.to_thread(...)`, or declare the route with plain `def` so the framework runs it in its
thread pool; this is a smaller change, but the pool is bounded, so concurrency caps at its size and each
call still costs a thread.

## Quiz
### Q: A coroutine on the event loop calls `time.sleep(2)`. What happens to the other tasks?
- [ ] They continue, because the loop runs each task in its own thread
- [x] They all stall for two seconds, because the one loop thread is blocked and cannot resume anyone
- [ ] Only tasks waiting on the same socket stall
- [ ] The loop detects the block and migrates the call to a thread automatically
> asyncio is single-threaded and cooperative. A blocking call never returns control to the loop, so no
> ready callback or timer can run until it finishes. Only `asyncio.sleep` or `to_thread` keep the loop
> free while waiting.

### Q: Why should you keep a reference to a task created with `asyncio.create_task`?
- [ ] Tasks cannot start until they are awaited by someone
- [ ] The result is discarded unless a reference exists
- [x] The loop keeps only weak references, so an unreferenced task can be garbage-collected before it finishes
- [ ] `create_task` returns a coroutine, not a task, until it is referenced
> The task starts on a later loop iteration regardless, but the event loop does not own it. Without a
> strong reference elsewhere, garbage collection may destroy it mid-execution, which is why the docs
> recommend storing tasks in a collection or using a TaskGroup.

### Q: Which workload gains the most from moving to asyncio?
- [ ] Re-ranking 10,000 candidates with a pure-Python scoring loop
- [x] Calling an embeddings API for 2,000 chunks with a concurrency cap of 32
- [ ] Parsing a 5 GB JSONL file into pandas
- [ ] Training a small model with PyTorch on a GPU
> The embeddings fan-out is I/O-bound: each call waits on the network, so one thread can keep 32
> requests in flight. The other options are CPU- or GPU-bound and would not benefit; they would block
> the loop instead.
