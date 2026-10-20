---
id: asyncio-vs-threads-vs-multiprocessing
module: python-for-ai
name: asyncio vs threads vs multiprocessing
kind: decision
difficulty: core
est_minutes: 17
status: reviewed
related:
  - python-for-ai/asyncio
  - python-for-ai/gil
  - python-for-ai/async-await
  - python-for-ai/race-conditions-and-synchronization-primitives
  - python-for-ai/memory-and-performance-profiling
  - fastapi-for-ai/async-endpoints
  - fastapi-for-ai/uvicorn-gunicorn-workers
  - data-architecture/document-processing-pipelines
sources:
  - Official docs: asyncio, threading, multiprocessing, concurrent.futures
  - PEP 703 — Making the Global Interpreter Lock Optional in CPython
---

## TL;DR
Pick by what the work waits on. asyncio suits many concurrent network waits, as long as the libraries
are async. Threads suit blocking I/O libraries and C code that releases the GIL. Processes suit
CPU-bound pure Python, which the GIL otherwise serialises. Real AI pipelines mix all three: an async
core, `asyncio.to_thread` for blocking SDKs, and a process pool for parsing or tokenising at scale.

## Why it exists
AI backends have two very different kinds of work. Most wall-clock time goes to waiting: on a model
provider, an embedding API, a vector database, an S3 download. Some work is pure CPU: parsing PDFs,
chunking text, tokenising, JSON-schema validation of large outputs, local re-ranking. Python gives you
three concurrency models, and each fits only some of this.

The naive approach is to pick one for everything. "Everything async" falls over on the first
synchronous library (a vendor SDK, a PDF parser, a database driver without async support): it blocks
the event loop, and every concurrent request stalls behind it. "Everything threads" works for I/O but
does not speed up pure-Python CPU work, because in standard CPython the GIL lets only one thread run
Python bytecode at a time, and thousands of threads for thousands of in-flight requests are heavy.
"Everything multiprocessing" gives true CPU parallelism, but each process has its own memory, model
copies and connection pools, and every argument and result is pickled across a pipe, which can cost
more than the work itself.

The decision exists because the right answer depends on the bottleneck, and getting it wrong is
expensive. Usually the result is not a crash but a service whose throughput does not move however
many workers you add.

## How it works
### asyncio: one thread, many waits
An event loop multiplexes sockets and resumes coroutines when their I/O is ready. A suspended
coroutine costs a small heap object, so tens of thousands of concurrent LLM streams are feasible on
one thread. Switching happens only at `await`, which makes races easier to reason about. The cost is
"function colouring": every layer must be async, and any blocking call freezes the loop. It gives no
CPU parallelism at all.

### Threads: preemptive, shared memory, GIL-limited
OS threads are scheduled by the kernel and share the process's memory. In standard CPython a thread
holds the GIL while running bytecode and releases it on blocking I/O and inside C extensions that
choose to release it (NumPy on large arrays, many PyTorch ops, hashlib, compression). So threads give
real concurrency for I/O and for GIL-releasing native code, but pure-Python CPU work runs one thread
at a time. Each thread reserves its own stack and the OS schedules it, so the practical count is
hundreds to low thousands, not tens of thousands. Switching can happen between almost any two
bytecodes, so shared state needs `threading.Lock`.

### Processes: true parallelism, no shared heap
`multiprocessing` and `ProcessPoolExecutor` start separate interpreters, each with its own GIL, so
CPU-bound Python scales across cores. The price: arguments and results are pickled (lambdas and open
clients cannot be sent), each worker pays startup and import cost, and memory is multiplied by the
number of workers. The start method matters. `fork` copies the parent quickly but is unsafe if the
parent has threads or CUDA state; `spawn`, the default on macOS and Windows, starts clean but
re-imports your modules (since 3.14 the Linux default is `forkserver`, which has the same import
requirements), so worker entry points must be importable and guarded by
`if __name__ == "__main__":`. Heavy per-worker state (a tokenizer, a parser model) belongs in the
pool's `initializer=` function, which runs once per worker, *because* passing it with every task would
pickle it every time. The test for whether a process pool pays off is a ratio: per-task compute time
against the time to pickle the arguments and result (`len(pickle.dumps(arg))` and a quick timing tell
you). Batch small items into chunks (`chunksize=` on `map`) until compute dominates.

### A decision procedure
1. Measure first: wall time, process CPU time and per-core utilisation for a representative run.
2. CPU well below one core while latency is high: you are waiting. Use asyncio if the libraries are
   async; threads if they are blocking. Either way, bound concurrency to what the dependency allows.
3. One core pinned and adding threads changes nothing: pure-Python CPU work under the GIL. Use
   processes, or move the hot loop into native code (NumPy, Polars, a compiled library).
4. Several cores busy with threads: the hot path is in GIL-releasing native code, and threads are the
   cheapest parallelism you have, sharing memory and model weights.
5. The work mixes both: keep one async core and push each kind of work to the executor that matches it.

### Bridges between them
`await asyncio.to_thread(fn, *args)` (3.9+) runs a blocking call in the default thread pool without
blocking the loop. `loop.run_in_executor(process_pool, fn, *args)` sends CPU work to processes and
returns an awaitable. This is the usual shape: async at the edges for I/O fan-out, executors for
everything else. The free-threaded CPython build introduced by PEP 703 removes the GIL in an optional
build, which may change the thread-versus-process answer for CPU work; check its status and your
dependencies' support for your Python version before relying on it.

## Example
A worked scenario, with illustrative numbers. An ingestion job must process 2,000 PDFs: download each
from S3 (about 0.3 s, network), extract text with a pure-Python parser (about 0.5 s CPU), then embed
chunks via an API (about 0.4 s, network). On an 8-core machine:

- **Sequential:** 2,000 × 1.2 s = 2,400 s, about 40 minutes.
- **All asyncio, parser called directly:** downloads and embeddings overlap, but every parse blocks the
  loop. Parsing alone is 2,000 × 0.5 s = 1,000 s on one core, so the job takes at least ~17 minutes, and
  during each parse no downloads or embeddings make progress.
- **Threads for everything:** I/O overlaps well, but parses still serialise on the GIL: the same
  ~1,000 s floor, plus contention.
- **Async I/O plus a process pool of 8 for parsing:** parse time becomes roughly 1,000 s ÷ 8 ≈ 125 s if
  it scales cleanly. With downloads and embeddings bounded by a semaphore and overlapped, the job is
  near the parse floor, a couple of minutes, provided pickling the PDF bytes in and the extracted text
  back is cheap relative to 0.5 s of work.

```python
import asyncio

async def handle(pdf_key, pool, sem):                   # pool: ProcessPoolExecutor(8)
    loop = asyncio.get_running_loop()
    async with sem:
        raw = await s3_get(pdf_key)                     # async I/O
    text = await loop.run_in_executor(pool, parse, raw) # CPU in a process
    async with sem:
        return await embed(chunk(text))                 # async I/O
```

## Trade-offs
- asyncio gives the most concurrency per byte of memory, but demands async libraries throughout;
  one synchronous dependency in the hot path makes threads the pragmatic choice.
- Threads are the easiest way to parallelise blocking SDK calls, but shared state needs locks and
  pure-Python CPU work gains nothing; that flips when the CPU work is in GIL-releasing native code.
- Processes deliver real CPU parallelism, at the cost of pickling, startup and memory per worker;
  they stop paying off when each task is so small that serialisation dominates the compute.
- Separate server workers (Uvicorn/Gunicorn processes) scale a web tier across cores without code
  changes, but multiply model memory and connection pools per worker, which matters with local models.

## Pitfalls
- Calling a synchronous SDK or `requests` inside `async def`; the symptom is p99 latency rising for
  all requests on a worker while CPU stays low.
- Adding threads to speed up pure-Python parsing or tokenisation; CPU sits at about one core and
  wall time barely changes.
- Passing large objects (documents, NumPy arrays, model weights) to a process pool per task; profiling
  shows most time in pickling and worker memory spikes.
- Using `fork` after starting threads or initialising CUDA in the parent; the symptom is workers that
  deadlock intermittently on a lock some parent thread held at fork time, or PyTorch's "Cannot
  re-initialize CUDA in forked subprocess" error.
- Loading a local model at import time in a module that `spawn` re-imports; each worker loads its
  own copy and the machine runs out of memory.

## When to use
Start by measuring where time goes: if CPU is low while latency is high, you are I/O-bound; if one
core is pinned, you are CPU-bound in Python. For an API server calling LLMs and vector stores, use
asyncio and async clients such as `httpx` or the official async SDKs. Wrap unavoidable blocking calls
in `asyncio.to_thread`. For heavy parsing, chunking or local inference preprocessing, use a process
pool, or move the work to a separate worker service. For GPU inference, the work runs in native code
and a dedicated serving engine usually beats rolling your own concurrency. In interviews, justify the
choice from the bottleneck, not from preference.

## Role lens
- **Engineer:** Keeps the event loop free of blocking calls, uses `to_thread` and process pools as
  bridges, and profiles before adding workers.
- **Architect:** Splits I/O-bound request handling from CPU-bound batch work into separately scaled
  services, and decides worker counts against memory budgets.
- **FDE:** Finds the synchronous call blocking a customer's async service, or the thread pool that
  cannot speed up their parser, and fixes it in their codebase.

## Check yourself
**Q:** A team's chunking step (pure-Python regex and string splitting, about 2 ms per document) is too slow.
They wrap it in a 16-thread pool: no change, and `top` shows the process at about 100% CPU. They switch to a
`ProcessPoolExecutor(16)` submitting one document per task, and it gets slower. Explain both results and
what you would do.
**A:** The threads changed nothing because the work is pure-Python bytecode: under the GIL only one thread
runs at a time, and "100% CPU" means one core, the fingerprint of GIL-bound work. The process pool removed
the GIL limit but introduced a per-task cost paid serially in the parent: it pickles every document,
pushes it through a pipe, tracks a future per task, and unpickles every list of chunks that comes back.
Sixteen workers each finishing a task every 2 ms demand thousands of tasks per second from that one
parent thread, so the parent becomes the bottleneck, and when documents are large the per-task
serialisation alone can exceed the 2 ms of work it is shipping. The fix keeps
processes but changes the grain: send batches of a few hundred documents per task (`map` with
`chunksize`, or explicit batches) so compute dominates, initialise any heavy objects once per worker,
and size the pool to physical cores. Then measure throughput against batch size, because the optimum is
empirical. If the hot loop can be expressed as vectorised Polars string operations, that may beat both.

## Quiz
### Q: Which workload is the best fit for a `ProcessPoolExecutor` rather than threads or asyncio?
- [ ] Calling an LLM API 5,000 times with async-capable clients
- [ ] Running large NumPy matrix multiplications that release the GIL
- [x] Parsing thousands of documents with a pure-Python parser
- [ ] Streaming tokens from a model provider to many websocket clients
> Pure-Python CPU work is serialised by the GIL in threads and blocks the loop in asyncio, so only
> separate processes run it in parallel. NumPy releasing the GIL makes threads viable for the second option.

### Q: What does `await asyncio.to_thread(fn)` achieve inside a coroutine?
- [ ] It makes `fn` run in parallel with other Python bytecode regardless of the GIL
- [x] It runs the blocking `fn` in a worker thread so the event loop keeps serving other coroutines
- [ ] It converts `fn` into a coroutine that yields at each line
- [ ] It runs `fn` in a separate process to avoid the GIL
> The event loop stays responsive because the blocking call waits in another thread. It does not
> bypass the GIL, so it helps for blocking I/O but not for pure-Python CPU work.

### Q: Why can a process pool make a job slower than running it sequentially?
- [ ] Processes share one GIL, so they contend more than threads
- [ ] The operating system limits processes to one core each by default
- [ ] Process pools cannot run more than one task per worker
- [x] Each task's arguments and results are pickled and copied, which can cost more than tiny tasks
> Processes have separate memory, so data crosses a pipe as pickled bytes. When per-task compute is
> small or the payload is large, serialisation and startup overhead outweigh the parallel speed-up.
