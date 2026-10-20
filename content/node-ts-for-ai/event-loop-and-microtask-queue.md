---
id: event-loop-and-microtask-queue
module: node-ts-for-ai
name: Event loop & microtask queue
kind: concept
difficulty: core
est_minutes: 14
related:
  - node-ts-for-ai/promises-and-async-error-propagation
  - node-ts-for-ai/worker-threads-for-cpu-bound-tasks
  - node-ts-for-ai/streams-and-backpressure
  - python-for-ai/asyncio
  - python-for-ai/async-await
  - serving-inference/streaming
sources:
  - Official docs: Node.js — The Node.js Event Loop, Timers, and process.nextTick()
  - Official docs: Node.js — perf_hooks (monitorEventLoopDelay)
---

## TL;DR
Node runs your JavaScript on one thread and multiplexes thousands of in-flight I/O operations through
the event loop: libuv waits on sockets and files, and the loop runs callbacks in phases. Promise
callbacks and `process.nextTick` go on separate microtask queues that are drained completely after
every callback, before the loop advances. That is why an AI gateway can hold 5,000 open model streams
cheaply, and also why one 200 ms synchronous computation stalls every one of them.

## Why it exists
A service that calls LLM APIs spends almost all of its wall-clock time waiting: a request to a model
might take 2–30 seconds, during which the CPU does nothing useful for that request. The thread-per-
request model (one OS thread blocked per outstanding call) burns a stack and a kernel thread for every
idle wait, and context-switching among thousands of them is where throughput goes to die. Node took the
opposite bet: one JavaScript thread, non-blocking I/O everywhere, and a loop that runs the callback for
whichever operation just completed. A pending HTTP response costs a few kilobytes of heap, not a thread.

The price of that bet is that the single thread is shared by *everything*. There is no scheduler to
preempt a long computation; a callback runs until it returns. If a request handler spends 300 ms
synchronously tokenizing a 2 MB document or `JSON.parse`-ing a huge tool result, no other callback
runs for 300 ms: streamed tokens stop flowing to every other client, health checks time out, timers
fire late. The naive mental model, "async code runs in parallel", fails here; only the *waiting* is
concurrent, the JavaScript is strictly sequential. Understanding the loop's phases and the microtask
queues is what lets you reason about ordering (why a promise callback runs before a `setTimeout(0)`),
about starvation (a recursive `nextTick` that never lets I/O run), and about when to move work off the
thread entirely.

## How it works
### One thread, a loop, and libuv
Node embeds V8 for JavaScript and libuv for the event loop and asynchronous I/O. Network sockets are
watched with the OS's readiness mechanism (epoll, kqueue, IOCP), so waiting on 10,000 sockets is one
system call, not 10,000 threads. Operations that the OS cannot do asynchronously (most filesystem calls,
DNS lookups via `getaddrinfo`, some crypto) are run on libuv's small thread pool (four threads by
default, tunable with `UV_THREADPOOL_SIZE`) and their results are posted back to the loop as events.
Your JavaScript never runs on those threads.

### The phases
Each iteration ("tick") of the loop visits phases in a fixed order, running the callbacks queued for
each:

1. **timers** — callbacks of `setTimeout`/`setInterval` whose threshold has elapsed. A timer is a
   *minimum* delay, not a schedule; it runs when the loop gets here.
2. **pending callbacks** — deferred I/O callbacks from the previous iteration (some system errors).
3. **poll** — the heart of the loop: block waiting for I/O (with a timeout derived from the nearest
   timer), then run the callbacks for completed I/O: socket data, HTTP responses, file reads.
4. **check** — `setImmediate` callbacks. It exists so that code can say "run right after the I/O I just
   handled" without competing with timers.
5. **close callbacks** — `'close'` events for destroyed sockets and handles.

Between every single callback, Node drains the two microtask queues (next section). The loop exits when
there are no pending timers, handles or requests, which is why a script "just ends" when its work is done.

### Microtasks: `process.nextTick` and promise jobs
Node keeps two queues that are not phases at all: the `nextTick` queue and the promise microtask queue
(the ECMAScript "job queue", where `.then` callbacks, `await` continuations and `queueMicrotask` land).
After each callback returns, Node drains the `nextTick` queue completely, then the promise queue
completely, repeating until both are empty, *then* moves on. Microtasks queued while draining are run in
the same drain. Two consequences:

- Ordering is deterministic: synchronous code first, then every `nextTick`, then every promise
  continuation, then the next macrotask (timer, I/O or immediate). An `await` on an already-resolved
  value still yields to the microtask queue, which is enough to let other promise continuations run but
  *not* enough to let I/O run.
- Starvation is possible: a function that re-queues itself with `nextTick` or with a resolved promise
  keeps the drain going forever, and the poll phase never gets a turn. The symptom is a process that is
  busy but handles no requests. `setImmediate` is the safe way to yield to I/O.

`setTimeout(fn, 0)` versus `setImmediate(fn)` from the main module is non-deterministic (it depends on
whether 1 ms has elapsed by the time the timers phase is checked); inside an I/O callback, `setImmediate`
always runs first because check comes before the next timers phase.

### What "blocking the loop" looks like in an AI service
Everything in a request handler that is not an `await` on I/O runs to completion on the loop thread:
`JSON.parse` of a multi-megabyte response, a regex over a long transcript, computing cosine similarity
across 100k embeddings in plain JS, synchronous `zlib`/`crypto` calls, or a tight loop building a
prompt from a large document. While that runs, every open SSE stream in the process freezes, because
the `write()` calls that would flush their next tokens are callbacks waiting for the loop. Event-loop
lag is therefore the first metric to watch: `perf_hooks.monitorEventLoopDelay()` samples how late the
loop is, and p99 lag above tens of milliseconds means some handler is doing CPU work inline. The
remedies, in order: make it async in chunks (`await setImmediate`-style yielding), move it to a worker
thread, or move it out of the process.

## Example
The ordering rules and a lag monitor in one script. Run with `node --import tsx script.ts` or compile
with `tsc` first.

```ts
import { monitorEventLoopDelay } from "node:perf_hooks";
import { setImmediate as yieldToLoop } from "node:timers/promises";

console.log("1 sync");
setTimeout(() => console.log("5 timeout (timers phase)"), 0);
setImmediate(() => console.log("6 immediate (check phase)"));
Promise.resolve().then(() => console.log("4 promise microtask"));
process.nextTick(() => console.log("3 nextTick (drained before promises)"));
console.log("2 sync");
// Prints 1,2,3,4 then 5,6 (5/6 order may swap from the main module; never from an I/O callback).

// Event-loop lag: sample the histogram and alert when p99 is high.
const h = monitorEventLoopDelay({ resolution: 10 });
h.enable();
setInterval(() => {
  console.log(`loop lag p99 ${(h.percentile(99) / 1e6).toFixed(1)} ms`);
  h.reset();
}, 5_000).unref();

// A CPU-heavy scoring loop made cooperative: yield to I/O every 500 vectors so
// streaming responses to other clients keep flowing while this request computes.
async function topK(query: Float32Array, vectors: Float32Array[], k: number) {
  const scores: Array<[number, number]> = [];
  for (let i = 0; i < vectors.length; i++) {
    let dot = 0;
    for (let d = 0; d < query.length; d++) dot += query[d]! * vectors[i]![d]!;
    scores.push([dot, i]);
    if (i % 500 === 499) await yieldToLoop();   // setImmediate: lets the poll phase run
  }
  return scores.sort((a, b) => b[0] - a[0]).slice(0, k);
}
void topK;
```

## Trade-offs
- One thread with non-blocking I/O gives very cheap concurrency for I/O-bound work like proxying
  model calls, but gives zero parallelism for CPU work; the moment handlers do real computation the
  model flips and you need worker threads or separate processes.
- Deterministic microtask ordering makes promise-based code predictable and easy to test, but the same
  rule lets a promise loop starve I/O, which thread-based runtimes would have preempted.
- Chunking CPU work with `setImmediate` keeps the process responsive without threads, at the cost of
  longer total time for that request and code that is harder to read; it wins for occasional medium
  jobs and loses for sustained heavy compute.
- The libuv thread pool hides blocking syscalls for you, but with four threads by default a burst of
  file reads or DNS lookups queues behind each other; raising `UV_THREADPOOL_SIZE` helps until memory
  and contention bite.

## Pitfalls
- Doing CPU-heavy work inline in a request handler (tokenizing, big `JSON.parse`, similarity search);
  the symptom is every concurrent stream stalling at once and p99 latency spiking while CPU is at 100%
  on a single core.
- Recursively scheduling with `process.nextTick` or resolved promises to "spread work out"; the symptom
  is a process that is alive and busy yet accepts no new connections, because the poll phase never runs.
- Assuming `setTimeout(fn, 100)` fires at 100 ms; the symptom is timers drifting by seconds under load,
  because they only fire when the timers phase is reached.
- Blocking the loop with synchronous APIs (`fs.readFileSync`, `child_process.execSync`, sync `zlib`) in
  a hot path because "it is just a small file"; the symptom appears only under concurrency, when the
  small stall multiplies across every in-flight request.
- Not watching event-loop lag at all; the symptom is latency incidents with no CPU, memory or upstream
  signal to explain them, because the delay is inside the process.

## When to use
You do not opt into the event loop; you design around it. Reach for plain async I/O for everything that
waits on a network or a model, and expect it to scale to thousands of concurrent requests per process.
The decision point is CPU: if a code path does more than a few milliseconds of synchronous work per
request, chunk it with `setImmediate` yielding when it is occasional, or move it to a worker thread or a
separate service when it is sustained. Measure with `monitorEventLoopDelay` in production and put a
p99 lag alert next to your latency SLO; add `--cpu-prof` or `clinic`-style profiling when lag is high.
In interviews, expect to be asked to predict the output order of a snippet mixing `setTimeout`,
`setImmediate`, `nextTick` and promises, and to explain why a single slow handler hurts unrelated users.

## Role lens
- **Engineer:** Keeps request handlers free of synchronous heavy work, yields or offloads CPU-bound steps,
  and ships an event-loop-lag gauge with every service so stalls are visible.
- **Architect:** Decides which workloads belong on the loop (model proxying, streaming, orchestration)
  and which must live in workers or separate services (parsing, embedding math, batch jobs).
- **FDE:** Diagnoses "the assistant freezes for everyone at once" at a customer by checking loop lag
  before blaming the model provider, and explains why a bigger VM will not fix a single-thread stall.

## Check yourself
**Q:** A streaming chat gateway shows all active streams pausing for about half a second several times
a minute. CPU is at 100% on one core, the model provider's latency is normal, and memory is flat. What is
happening and how would you confirm and fix it?
**A:** The pattern points to event-loop blocking: some handler runs roughly 500 ms of synchronous
JavaScript, and because every stream's next `write()` is a callback queued on the same loop, all streams
freeze together. Confirm by sampling `monitorEventLoopDelay` (p99 lag will match the pause) and taking a
CPU profile to find the hot function; likely candidates are parsing a large tool result, tokenizing
documents for context packing, or a similarity search over embeddings in plain JS. Fix by moving that
work to a worker thread (or a separate service) if it is sustained, or by chunking it with `setImmediate`
yields if it is occasional, and keep a loop-lag alert so regressions are caught before users notice.

## Quiz
### Q: In what order do these run: a `setTimeout(fn, 0)`, a resolved promise's `.then`, and a `process.nextTick` callback queued from the same synchronous block?
- [ ] setTimeout, then nextTick, then the promise callback
- [ ] promise callback, then nextTick, then setTimeout
- [x] nextTick, then the promise callback, then setTimeout
- [ ] All three run in the order they were queued
> After the synchronous block finishes, Node drains the nextTick queue, then the promise microtask queue,
> and only then advances the loop to the timers phase where the setTimeout fires. Queue order matters
> only within a single queue.

### Q: Why does a loop that re-queues itself with `process.nextTick` prevent the server from accepting connections?
- [ ] nextTick callbacks run on the libuv thread pool, which fills up
- [x] The nextTick queue is drained completely before the loop reaches the poll phase, so I/O callbacks never run
- [ ] nextTick disables the timers phase until the queue is empty
- [ ] Each nextTick call opens a file descriptor that is never closed
> Microtask queues are drained to empty after every callback, and work queued during the drain is included.
> A self-re-queuing nextTick is never empty, so the loop never gets to poll for socket events. Using
> setImmediate instead schedules in the check phase and lets I/O interleave.

### Q: Which change most directly fixes a handler that stalls all concurrent streams for 300 ms while computing embeddings similarity in JavaScript?
- [ ] Increase `UV_THREADPOOL_SIZE` so the computation runs on more threads
- [ ] Wrap the computation in `async` and `await` its result
- [x] Move the computation to a worker thread or yield with `setImmediate` between chunks
- [ ] Replace `setTimeout` calls in the handler with `setImmediate`
> Marking a function `async` does not move its synchronous body off the loop, and the libuv pool only
> serves native operations, not your JavaScript. Only a worker thread gives true parallelism, and chunked
> yielding at least lets other callbacks run between slices.
