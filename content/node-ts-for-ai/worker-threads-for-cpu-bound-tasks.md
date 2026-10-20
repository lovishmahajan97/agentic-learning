---
id: worker-threads-for-cpu-bound-tasks
module: node-ts-for-ai
name: Worker threads for CPU-bound tasks
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
related:
  - node-ts-for-ai/event-loop-and-microtask-queue
  - node-ts-for-ai/bullmq-and-durable-jobs
  - python-for-ai/asyncio-vs-threads-vs-multiprocessing
  - python-for-ai/gil
  - serving-inference/batching-and-concurrency-limits
sources:
  - Official docs: Node.js — worker_threads
  - Official docs: MDN — Structured clone algorithm, Transferable objects, SharedArrayBuffer and Atomics
---

## TL;DR
The event loop runs all your JavaScript on one thread, so a CPU-heavy step freezes every concurrent
request. `worker_threads` run JavaScript in separate V8 isolates on other OS threads, communicating by
message passing (structured clone, or zero-copy transfer of `ArrayBuffer`s and shared memory via
`SharedArrayBuffer`). Use a pool of them for sustained computation such as tokenizing, parsing large
documents or vector math; use them sparingly, because each worker is a separate heap with its own
startup cost, and messages are copied unless transferred.

## Why it exists
Most of an AI service is waiting on the network, which the event loop handles beautifully. But some
steps are genuinely compute: counting tokens with a BPE tokenizer over a 200-page document before
deciding how to chunk it, parsing a 50 MB PDF, computing cosine similarity between a query and a few
hundred thousand embeddings held in memory, hashing files for deduplication, decompressing a large
export. Each of these can take hundreds of milliseconds to seconds of pure JavaScript, and during that
time the single thread serves nobody: streamed tokens for every other user stop, and health checks fail.

The naive fixes do not work. Marking the function `async` does nothing; `async` only yields at `await`
and there is nothing to await in a tight loop. Chunking with `setImmediate` keeps the loop responsive
but makes the heavy job slower and still spends the one core on it; two heavy requests at once halve
each other's throughput. Spawning a child process per job works but costs a whole Node runtime (tens of
megabytes and tens of milliseconds, illustrative) and serializes everything through pipes. Before
`worker_threads` existed, the standard advice was "Node is not for CPU work; call out to another
service". Worker threads changed the trade: a worker is a second V8 isolate inside the same process,
starts far cheaper than a process, can share memory with the main thread, and lets the main loop keep
streaming while real parallel work happens on the other cores.

## How it works
### Isolates, not shared heaps
`new Worker(file, { workerData })` starts an OS thread running its own V8 isolate and its own event
loop. The worker has a separate heap and global scope: no variables, no module instances, no open
sockets are shared with the parent. This is *because* V8's heap is not thread-safe; isolating heaps is
what makes it possible to run JavaScript in parallel at all, and it is also why you cannot "just call
a function on another thread". Each worker therefore costs memory (its own heap, its own copies of
loaded modules) and startup time (loading and compiling the worker script), which is why workers are
pooled and reused rather than created per request.

### Message passing and structured clone
Parent and worker talk through `MessagePort`s: `worker.postMessage(value)` on one side,
`parentPort.on('message', ...)` on the other. Values are copied with the structured clone algorithm,
which handles plain objects, arrays, `Map`, `Set`, `Date`, typed arrays and `Error`s. Functions and
symbols throw a `DataCloneError`, and class instances arrive as plain objects with their prototype and
methods gone, so a `Tokenizer` object cannot be sent, only the data needed to rebuild one. Cloning is O(size): sending a 100 MB string to a
worker copies 100 MB, and the copy itself happens on the sending thread, blocking it. The design
consequence is that the message should be a job description (a file path, an id, a small payload), and
large data should be transferred or shared rather than cloned.

### Transfer and shared memory
`postMessage(value, [arrayBuffer])` lists `ArrayBuffer`s to *transfer*: ownership moves to the
receiver in O(1) and the sender's view becomes detached (length 0). This is the right way to hand a
`Float32Array` of embeddings or a `Buffer` of file bytes to a worker and get a result buffer back. For
data that both sides must read concurrently, `SharedArrayBuffer` gives genuinely shared memory, with
`Atomics` for synchronization (`Atomics.add`, `Atomics.wait`/`notify`). Shared memory is fast and
dangerous in the usual ways: no structural locking, so keep it to numeric arrays and a clear protocol.

### Pooling
A pool keeps N workers (typically the CPU count, minus one for the main thread) alive, queues jobs, and
dispatches each to an idle worker, resolving a promise with the result. Writing one is about forty
lines; `piscina` (created by Node.js core contributors) is the common library and adds idle timeouts,
cancellation and `AbortSignal` support. The pool is also your concurrency limit: with a queue in front,
a burst of 1,000 tokenization requests becomes N parallel jobs and a bounded wait, instead of 1,000
threads. Check the CPU count the runtime reports against the container's CPU quota; on older Node
versions and some orchestrators `availableParallelism()` reflects the host, and a pool sized to 64 host
cores inside a 2-CPU container just thrashes.

### Parallelism across jobs versus within one job
A pool parallelises *across* jobs: eight queries on eight workers can finish in about the time of
one, if there are eight free cores and the work is compute-bound. A brute-force scan of a 1.2 GB matrix
is often limited by memory bandwidth instead, so eight concurrent scans share that bandwidth and the
speed-up is smaller; measure it rather than assuming linear scaling.
It does nothing for the latency of a single query, which still scans every vector on one thread. To
cut one job's latency, split the data: give each of N workers a range of rows in the shared matrix,
have each return its local top-k, and merge the N small lists on the main thread. That divides the
scan by up to N (less when memory bandwidth is the limit), at the cost of N messages and a merge per query, and it occupies the whole pool
for one request, so it fits low-concurrency, latency-sensitive paths rather than high-throughput ones.

### Cancelling work inside a worker
A worker running a tight synchronous loop is in the same position as the main thread was: its own
event loop is blocked, so it cannot receive a `'message'` saying "stop". Passing an `AbortSignal`
does not help either, since the abort is delivered as a callback the busy worker never runs. Two real
options exist. `worker.terminate()` stops the thread at the next safe point regardless of what it is
doing, which is reliable but throws away the worker (the pool must spawn a replacement, paying
startup again). Or the job checks a cancellation flag in a `SharedArrayBuffer` with `Atomics.load`
every few thousand iterations, which the main thread sets with `Atomics.store`; that keeps the worker
alive but requires the job to cooperate. Libraries such as `piscina` map an `AbortSignal` onto these
mechanisms for you.

### Memory limits per worker
Each worker has its own V8 heap limits. `new Worker(file, { resourceLimits: { maxOldGenerationSizeMb } })`
caps a worker's heap so one runaway document parse kills that worker (an `'error'` the pool can
handle) instead of pushing the whole process into the container's out-of-memory killer. Budget
container memory as main heap + N × worker heap + shared buffers.

### Workers versus `cluster` and child processes
`cluster` (or several replicas behind a load balancer) runs N copies of the whole server so I/O-bound
traffic uses N cores; each copy still blocks on its own CPU work. Worker threads keep one server and
move the CPU work aside. Child processes give stronger isolation (a crash or native-module segfault
does not take down the server) at higher memory and IPC cost. Many production services use replicas
for I/O scale and a worker pool inside each replica for CPU steps.

### What workers are not for
I/O-bound work gains nothing: a worker waiting on an HTTP call is a wasted thread. Work that native
code already runs off the loop (the async `crypto` and `zlib` APIs use libuv's pool) does not need a
worker.
And work that needs more than one machine's cores, or must survive a process crash, belongs in a job
queue (see the BullMQ lesson), not in threads. Workers also complicate observability: logs, traces and
metrics from inside a worker need to be forwarded or tagged, because the worker has its own globals.

## Example
A minimal pool that dispatches top-k cosine-similarity jobs to workers, transferring the query vector
instead of cloning it. The embedding matrix lives in a `SharedArrayBuffer`: passing an ordinary
`Float32Array` as `workerData` would structured-clone the whole ~1.2 GB matrix into every worker. The worker file and the pool live in separate modules; this listing shows both
with the worker guarded by `isMainThread`.

```ts
import { Worker, isMainThread, parentPort, workerData } from "node:worker_threads";
import { availableParallelism } from "node:os";

type Job = { query: Float32Array; k: number };
type Hit = { index: number; score: number };

if (!isMainThread) {
  const vectors = workerData.vectors as Float32Array;      // flattened [n, dim], L2-normalised, shared
  const dim = workerData.dim as number;
  parentPort!.on("message", ({ query, k }: Job) => {
    const hits: Hit[] = [];
    for (let i = 0, n = vectors.length / dim; i < n; i++) {
      let dot = 0;                                          // dot of unit vectors = cosine
      for (let d = 0; d < dim; d++) dot += query[d]! * vectors[i * dim + d]!;
      hits.push({ index: i, score: dot });
    }
    hits.sort((a, b) => b.score - a.score);
    parentPort!.postMessage(hits.slice(0, k));             // small result: cloning is fine
  });
} else {
  class Pool {                                              // minimal: no crash replacement
    private idle: Worker[] = []; private queue: Array<() => void> = [];
    constructor(file: URL, data: unknown, size = Math.max(1, availableParallelism() - 1)) {
      for (let i = 0; i < size; i++) this.idle.push(new Worker(file, { workerData: data }));
    }
    run(job: Job): Promise<Hit[]> {
      return new Promise((resolve, reject) => {
        const start = () => {
          const w = this.idle.pop()!;
          const onError = (e: Error) => { w.off("message", onMsg); reject(e); };
          const onMsg = (hits: Hit[]) => {
            w.off("error", onError); this.idle.push(w); this.queue.shift()?.(); resolve(hits);
          };
          w.once("message", onMsg).once("error", onError);
          w.postMessage(job, [job.query.buffer as ArrayBuffer]); // transfer: O(1), query detached here
        };
        this.idle.length ? start() : this.queue.push(start);
      });
    }
  }
  const dim = 1536, n = 200_000;                            // 200k x 1536 x 4 B = ~1.2 GB
  const vectors = new Float32Array(new SharedArrayBuffer(n * dim * 4)); // shared, not cloned per worker
  const pool = new Pool(new URL(import.meta.url), { vectors, dim });
  const hits = await pool.run({ query: new Float32Array(dim).fill(0.01), k: 5 });
  console.log(hits[0]);                                    // main loop stayed free to serve streams
}
```

## Trade-offs
- Workers give true parallelism for JavaScript on multi-core machines, but every worker is a separate
  heap, so memory scales with pool size; on a small container two workers may be all you can afford.
- Structured clone is convenient and safe, while transfer is O(1) but detaches the sender's buffer;
  choose transfer for large payloads and accept the discipline of not touching them afterwards.
- `SharedArrayBuffer` avoids copying entirely and suits read-mostly data like an embedding matrix, but
  it reintroduces data races that JavaScript otherwise never has, so keep it to numeric arrays with
  `Atomics` guarding writes.
- A pool bounds concurrency and amortizes startup, but a job that blocks a worker for seconds blocks
  that slot; give long jobs their own pool or move them to a queue.
- `terminate()` cancels anything instantly but costs a worker restart, while a shared-memory cancel flag
  is cheap but needs the job to poll it; terminate wins for rare cancellations of untrusted or
  third-party code, the flag wins when cancellation is routine (users abandoning requests).
- Compared with a separate service, workers are simpler to deploy and share the process's memory, yet
  they die with the process and cannot scale beyond one machine; durable or horizontally scaled work
  belongs in a job queue.

## Pitfalls
- Creating a new `Worker` per request; the symptom is latency dominated by worker startup and memory
  spikes under load, often worse than running the job inline.
- Sending large buffers without the transfer list; the symptom is the main thread stalling on the copy,
  which is the very problem the worker was meant to solve.
- Reading from a `TypedArray` after transferring its buffer; the symptom is zero-length arrays or
  `TypeError`s about detached buffers far from the `postMessage` call.
- Using workers for I/O-bound steps like calling the model API; the symptom is no throughput gain plus
  extra memory and harder debugging.
- Forgetting that workers have their own globals; the symptom is missing logs and traces, or a
  database client initialized twice because the module was loaded again in the worker.
- Not handling `'error'` and `'exit'` events; the symptom is a pool that silently shrinks after a
  worker crashes and eventually hangs with all jobs queued.
- Expecting a timeout or abort to stop a worker mid-computation; the symptom is the request returning
  an error while the worker's CPU stays at 100% and its pool slot stays busy until the job finishes.

## When to use
Reach for worker threads when a code path performs sustained synchronous computation (more than a few
tens of milliseconds, repeatedly) that must not stall the event loop and that fits in one machine's
memory: tokenization at scale, document parsing, embedding math in JS, compression and hashing of large
payloads. Prefer `setImmediate` chunking for rare, medium-sized jobs, native or WASM libraries that
release the thread when they exist, and a job queue for anything that must be durable or spread across
machines. Measure event-loop lag before and after, per-job time inside the worker, pool queue depth, and
process RSS. Interviewers typically ask why `async` does not help, how data crosses to a worker, and
when a worker is the wrong tool; be ready to compare with Python's multiprocessing and the GIL.

## Role lens
- **Engineer:** Moves tokenization and parsing into a pooled worker, transfers buffers instead of
  cloning them, and forwards worker logs and errors so failures are visible.
- **Architect:** Sets the rule for what runs on the loop, in workers, or in a separate job service, and
  sizes pools against container CPU and memory limits.
- **FDE:** Spots a customer's chunking pipeline parsing PDFs on the request thread, demonstrates the
  loop-lag fix with a pool, and explains why their 2-core container caps the gain.

## Check yourself
**Q:** A document-ingestion endpoint parses uploaded PDFs and tokenizes them in the request handler.
During bulk uploads the chat endpoint in the same process becomes unresponsive. A colleague proposes
wrapping the parsing in an `async` function and calling it without `await`. Evaluate the proposal and
give a better design.
**A:** The proposal does nothing: `async` only yields at `await` points, and parsing is a synchronous
loop, so it still runs to completion on the event-loop thread and the chat streams still freeze. The
real fix is to move parsing and tokenization into a pool of worker threads sized to the container's
CPUs, send each job the file bytes as a transferred `ArrayBuffer` (not a clone), and return the chunked
text as the result. The pool also bounds concurrency so a bulk upload becomes a queue rather than a
thundering herd. If uploads can exceed the memory of one process or must survive restarts, push jobs
into a durable queue with separate worker processes instead, and keep the API process for I/O only.
Measure event-loop lag to confirm the chat path no longer stalls.

## Quiz
### Q: Why does moving a CPU-heavy function into an `async` function not stop it from blocking the event loop?
- [ ] `async` functions run on the libuv thread pool but return to the main thread for results
- [x] `async` only yields control at `await`, and a synchronous loop has no await, so it still runs to completion on the main thread
- [ ] `async` functions are compiled to generators that are slower, making the block worse
- [ ] The event loop pauses microtasks while an async function runs
> `async` changes the return type to a promise and allows `await` inside; it does not move code to
> another thread. Only an actual `await` on a pending promise suspends the function. True parallelism
> for JavaScript requires a worker thread.

### Q: What happens to a `Float32Array` on the main thread after its buffer is listed in the transfer list of `postMessage`?
- [ ] It is copied, and both sides hold independent data
- [ ] It becomes read-only on the main thread
- [x] Its buffer is detached, so the main-thread view has length 0 and the worker owns the memory
- [ ] It is shared, so writes on either side are visible to both
> Transfer moves ownership without copying, which is why it is O(1), and the sender's view is detached
> to prevent two threads touching the same memory. Shared visibility requires a `SharedArrayBuffer`;
> structured clone without a transfer list copies the data.

### Q: Which workload benefits least from a worker-thread pool?
- [ ] Tokenizing thousands of documents for chunking
- [x] Making concurrent HTTP requests to a model provider
- [ ] Computing cosine similarity across an in-memory embedding matrix
- [ ] Parsing large PDF uploads into text
> HTTP requests are I/O-bound and already non-blocking on the main thread; a worker waiting on a socket
> adds memory and complexity with no throughput gain. Workers pay off for sustained synchronous
> computation that would otherwise stall the loop.
