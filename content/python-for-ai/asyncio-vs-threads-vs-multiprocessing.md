---
id: asyncio-vs-threads-vs-multiprocessing
module: python-for-ai
name: asyncio vs threads vs multiprocessing
kind: decision
difficulty: core
est_minutes: 17
status: agentic
agent_relevance: core
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
Run each agent tool where its waiting fits: asyncio for model and API calls, threads for blocking SDKs
and GIL-releasing C code, processes for CPU-bound Python. Keep an async core, bridged by
`asyncio.to_thread` and a process pool.

## For agent builders
- **Why it matters for agents.** An agent loop is mostly network waits (model, search, MCP servers), but
  its tools are a mix: a sync database driver, a vendor SDK with no async client, a PDF parser or code
  linter that burns CPU. Each tool must run where it cannot freeze the loop that serves every session.
- **Where it shows up in your stack.** FastAPI and Uvicorn workers serving the agent, `asyncio.to_thread`
  around sync tools (frameworks such as LangChain also run sync tools in a thread pool when invoked
  asynchronously), and process pools or separate workers for RAG ingestion and document parsing.
- **What you control.** Which executor each tool uses, the size of the default thread pool, process-pool
  `initializer=`, `chunksize=` and start method, and how many server workers fit your memory budget.
- **What you can skip.** Writing your own process orchestration or shared-memory schemes. Learn the
  decision procedure and the bridges; reach for a job queue when CPU work outgrows one machine.

## Why it exists
- **Agent backends do two kinds of work.** Most wall-clock time is waiting (model provider, embedding API,
  vector database, S3 download, web search tool). Some is pure CPU (parsing PDFs, chunking, tokenising,
  JSON-schema validation of large tool outputs, local re-ranking). Each of Python's three models fits only
  some of it.
- **"Everything async" breaks on the first sync library** (a vendor SDK, a PDF parser, a database driver
  without async support inside a tool): it blocks the event loop and every concurrent agent session stalls
  behind it.
- **"Everything threads" does not speed up pure-Python CPU work**, because in standard CPython the GIL
  lets only one thread run bytecode at a time, and thousands of threads for thousands of in-flight
  sessions are heavy.
- **"Everything multiprocessing" costs memory and pickling.** Each process has its own memory, model copies
  and connection pools, and every argument and result is pickled across a pipe, which can cost more than
  the work itself.
- **Getting it wrong is quietly expensive.** Usually not a crash, but an agent service whose throughput
  does not move however many workers you add.

## How it works
### The three models at a glance
| | asyncio | Threads | Processes |
|---|---|---|---|
| Scheduling | Cooperative, only at `await` | Preemptive, by the kernel | Separate interpreters, each with its own GIL |
| Parallel Python CPU | None | No (one thread runs bytecode at a time) | Yes, across cores |
| Practical count | Tens of thousands of waits on one thread | Hundreds to low thousands | About the core count |
| Shared state | Shared; races only across awaits | Shared; needs `threading.Lock` | No shared heap; data is pickled |
| Main cost | Every layer must be async | GIL for pure Python | Pickling, startup, memory per worker |
| Agent fit | The loop, model calls, async tools | Sync SDK tools, NumPy-heavy tools | Parsing, chunking, CPU-heavy tools |

### asyncio: one thread, many waits
- **Mechanism.** An event loop multiplexes sockets and resumes coroutines when their I/O is ready.
- **Cheap.** A suspended coroutine is a small heap object, so tens of thousands of concurrent LLM streams
  are feasible on one thread.
- **Easier races.** Switching happens only at `await`.
- **The cost is "function colouring":** every layer must be async, and any blocking call freezes the
  loop. It gives no CPU parallelism at all.

### Threads: preemptive, shared memory, GIL-limited
- **Mechanism.** OS threads are kernel-scheduled and share the process's memory.
- **When the GIL is released.** A thread holds the GIL while running bytecode and releases it on blocking
  I/O and inside C extensions that choose to (NumPy on large arrays, many PyTorch ops, hashlib,
  compression).
- **So threads give real concurrency** for I/O and GIL-releasing native code, but pure-Python CPU work runs
  one thread at a time.
- **Bounded count.** Each thread reserves its own stack and the OS schedules it: hundreds to low
  thousands, not tens of thousands.
- **Shared state needs `threading.Lock`**, because switching can happen between almost any two bytecodes.

### Processes: true parallelism, no shared heap
- **Mechanism.** `multiprocessing` and `ProcessPoolExecutor` start separate interpreters, each with its own
  GIL, so CPU-bound Python scales across cores.
- **Costs.** Arguments and results are pickled (lambdas and open clients cannot be sent), each worker pays
  startup and import cost, and memory is multiplied by the number of workers.

**Start methods:**
| Method | Behaviour | Caveat |
|---|---|---|
| `fork` | Copies the parent quickly | Unsafe if the parent has threads or CUDA state |
| `spawn` | Default on macOS and Windows; starts clean | Re-imports your modules |
| `forkserver` | Linux default since 3.14 | Same import requirements as spawn |

- **Guard entry points.** Under spawn and forkserver, worker entry points must be importable and guarded
  by `if __name__ == "__main__":`.
- **Heavy per-worker state** (a tokenizer, a parser model) belongs in the pool's `initializer=` function,
  which runs once per worker, *because* passing it with every task would pickle it every time.
- **The payoff test is a ratio:** per-task compute time against the time to pickle arguments and result
  (`len(pickle.dumps(arg))` and a quick timing tell you).
- **Batch small items** into chunks (`chunksize=` on `map`) until compute dominates.

### A decision procedure (per tool or pipeline stage)
1. **Measure first:** wall time, process CPU time and per-core utilisation for a representative run.
2. **CPU well below one core while latency is high:** you are waiting. Use asyncio if the libraries are
   async, threads if they are blocking. Either way, bound concurrency to what the dependency allows.
3. **One core pinned and adding threads changes nothing:** pure-Python CPU work under the GIL. Use
   processes, or move the hot loop into native code (NumPy, Polars, a compiled library).
4. **Several cores busy with threads:** the hot path is in GIL-releasing native code, and threads are the
   cheapest parallelism you have, sharing memory and model weights.
5. **The work mixes both:** keep one async core and push each kind of work to the executor that matches it.

### Bridges between them
- **`await asyncio.to_thread(fn, *args)`** (3.9+) runs a blocking call in the default thread pool without
  blocking the loop.
- **`loop.run_in_executor(process_pool, fn, *args)`** sends CPU work to processes and returns an awaitable.
- **The usual shape:** async at the edges for I/O fan-out, executors for everything else.
- **Free-threaded CPython (PEP 703)** removes the GIL in an optional build, which may change the
  thread-versus-process answer for CPU work. Check its status and your dependencies' support for your
  Python version before relying on it.

## Diagram
The decision procedure, applied to each tool or pipeline stage: measure first, then let what the work
waits on pick the model. Mixed agents keep one async core and bridge out.

```mermaid
flowchart TD
    M["Measure wall time, CPU, cores"] --> Q1{"CPU low while latency high?"}
    Q1 -->|"yes, waiting on I/O"| Q2{"Library async?"}
    Q2 -->|"yes"| A["asyncio plus a Semaphore"]
    Q2 -->|"no, blocking SDK"| T["Threads via asyncio.to_thread"]
    Q1 -->|"no, CPU busy"| Q3{"Native code releases GIL?"}
    Q3 -->|"yes, several cores busy"| T2["Threads, shared weights"]
    Q3 -->|"no, one core pinned"| P["Process pool, batched"]
    A --> MIX["Async core plus executor bridges"]
    T --> MIX
    P --> MIX
```

## Examples
### Routing agent tools to the right executor
One dispatcher decides per tool: async tools are awaited, blocking tools go to a bounded thread pool, and
CPU-heavy tools go to a process pool. Tool bodies are stand-ins.

```python
import asyncio, time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

async def web_search(q):            # async client: await directly
    await asyncio.sleep(0.4); return f"results for {q}"

def crm_lookup(user_id):            # sync vendor SDK: blocks, releases GIL on I/O
    time.sleep(0.3); return {"id": user_id, "tier": "gold"}

def parse_attachment(text):         # pure-Python CPU: holds the GIL
    return sum(ord(c) % 7 for c in text * 2000)

TOOLS = {"web_search": ("async", web_search),
         "crm_lookup": ("thread", crm_lookup),
         "parse_attachment": ("process", parse_attachment)}

async def run_tool(name, arg, procs):
    kind, fn = TOOLS[name]
    loop = asyncio.get_running_loop()
    if kind == "async":
        return await fn(arg)
    if kind == "thread":
        return await asyncio.to_thread(fn, arg)           # default executor, bounded below
    return await loop.run_in_executor(procs, fn, arg)     # fn and arg must be picklable

async def main():
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=16))
    with ProcessPoolExecutor(max_workers=4) as procs:
        t = time.perf_counter()
        calls = [("web_search", "refund policy"), ("crm_lookup", 7),
                 ("parse_attachment", "invoice line " * 500)]
        out = await asyncio.gather(*(run_tool(n, a, procs) for n, a in calls))
        print(f"{len(out)} tools in {time.perf_counter() - t:.2f}s; loop never blocked")

if __name__ == "__main__":
    asyncio.run(main())
```

### Worked scenario: one agent step with three tools
Illustrative numbers for a step where the model requested a web search (0.4 s network), a CRM lookup
through a sync SDK (0.3 s network) and parsing an attachment (0.8 s pure-Python CPU), on a worker serving
50 other sessions.

| Design | Step time | Effect on other sessions |
|---|---|---|
| All three called directly in the coroutine | 0.4 + 0.3 + 0.8 = 1.5 s, sequential | Loop frozen for 1.1 s (CRM plus parse) |
| Search awaited, CRM and parse via `to_thread` | about 0.8 s | Loop free, but the parse still holds the GIL, so other sessions' Python work is slowed while it runs |
| Search awaited, CRM via `to_thread`, parse in a process pool | about 0.8 s | Loop free and the parse runs on another core |

### Worked scenario: ingesting 2,000 PDFs for an agent's RAG index
Illustrative numbers. Each PDF: download from S3 (about 0.3 s, network), extract text with a pure-Python
parser (about 0.5 s CPU), embed chunks via an API (about 0.4 s, network). 8-core machine.

| Design | Time | Why |
|---|---|---|
| Sequential | 2,000 × 1.2 s = 2,400 s, about 40 minutes | Nothing overlaps |
| All asyncio, parser called directly | at least ~17 minutes | Parsing alone is 2,000 × 0.5 s = 1,000 s on one core; during each parse no downloads or embeddings progress |
| Threads for everything | the same ~1,000 s floor, plus contention | I/O overlaps, but parses serialise on the GIL |
| Async I/O + process pool of 8 for parsing | near the parse floor, a couple of minutes | Parse time ≈ 1,000 s ÷ 8 ≈ 125 s if it scales cleanly, with I/O bounded by a semaphore and overlapped |

The last row holds only if pickling the PDF bytes in and the extracted text back is cheap relative to
0.5 s of work.

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

### Measure it yourself: threads, processes and chunksize
Pure Python, standard library only.

```python
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

def score(doc: str) -> int:                  # pure-Python CPU work: holds the GIL
    return sum(ord(c) % 7 for c in doc)

def timed(executor, docs, chunksize=1) -> float:
    t = time.perf_counter()
    with executor as ex:                     # map's chunksize is ignored by thread pools
        list(ex.map(score, docs, chunksize=chunksize))
    return time.perf_counter() - t

if __name__ == "__main__":                   # required under spawn and forkserver
    big = ["chunk this text " * 15_000] * 64  # few large tasks: compute dominates
    tiny = ["short doc"] * 50_000             # many tiny tasks: overhead dominates
    print(f"big,  1 thread      {timed(ThreadPoolExecutor(1), big):.2f}s")
    print(f"big,  8 threads     {timed(ThreadPoolExecutor(8), big):.2f}s")
    print(f"big,  8 processes   {timed(ProcessPoolExecutor(8), big):.2f}s")
    print(f"tiny, 8 proc, cs=1  {timed(ProcessPoolExecutor(8), tiny):.2f}s")
    print(f"tiny, 8 proc, cs=1k {timed(ProcessPoolExecutor(8), tiny, 1000):.2f}s")
```

What to expect (one laptop run, illustrative, not a benchmark):
- **8 threads ≈ 1 thread** (about 0.56 s each): the GIL serialises pure-Python work.
- **8 processes about 3× faster** (0.16-0.20 s), less than 8× because pool startup and pickling are
  included in the timing.
- **Tiny tasks: about 4.4 s with `chunksize=1`, under 0.1 s with `chunksize=1000`.** Per-task overhead in
  the parent dominated until batching made compute the larger share.

## Production patterns
- **Classify every tool by executor.** Keep a registry that marks each agent tool async, thread or process,
  and route through one dispatcher as above, so a new sync tool cannot land on the loop by accident.
  Monitor loop lag and per-tool latency.
- **Bound the default thread pool.** At startup, call
  `loop.set_default_executor(ThreadPoolExecutor(max_workers=N))` so `to_thread` calls to a slow sync SDK
  cannot grow without limit. Monitor executor queue length and the latency of offloaded calls.
- **Initialise workers once, recycle them.** Create `ProcessPoolExecutor(max_workers=..., initializer=load_parser)`
  and, on 3.11+, set `max_tasks_per_child=` to restart workers that leak memory. Monitor per-worker RSS.
- **Choose the start method explicitly.** Pass `mp_context=multiprocessing.get_context("spawn")` (or
  `"forkserver"`) when the parent has threads or CUDA, instead of relying on the platform default. Watch
  for intermittent worker hangs, the fork-with-threads fingerprint.
- **Batch CPU tasks and split them out.** Send hundreds of small documents per task (`chunksize=` or explicit
  batches), and run RAG parsing and chunking as queue-fed workers scaled separately from the I/O-bound
  agent API tier. Monitor tasks/s, queue depth and age of the oldest job.
- **Size server workers against memory.** Each Uvicorn/Gunicorn worker process duplicates models and pools,
  so set the worker count from the memory budget, not only from cores. Monitor RSS per worker.

## Trade-offs
- **Concurrency per byte vs async everywhere.** asyncio gives the most concurrency per byte of memory but
  demands async libraries throughout; one synchronous tool dependency in the hot path makes threads the
  pragmatic choice for that tool.
- **Easy parallel I/O vs no CPU gain.** Threads are the easiest way to parallelise blocking SDK calls, but
  shared state needs locks and pure-Python CPU work gains nothing. That flips when the CPU work is in
  GIL-releasing native code.
- **Real CPU parallelism vs overhead.** Processes pay pickling, startup and memory per worker; they stop
  paying off when each task is so small that serialisation dominates the compute.
- **Free scaling vs memory.** Separate server workers (Uvicorn/Gunicorn processes) scale an agent tier
  across cores without code changes, but multiply model memory and connection pools per worker, which
  matters with local models and embedding models loaded in-process.

## Pitfalls
- **A synchronous SDK or `requests` inside an `async def` tool.** Symptom: p99 latency rises for all
  sessions on a worker while CPU stays low. Fix: async client or `to_thread`.
- **Threads to speed up pure-Python parsing or tokenisation.** Symptom: CPU sits at about one core and wall
  time barely changes. Fix: processes or native code.
- **Large objects per task to a process pool** (documents, NumPy arrays, model weights). Symptom: profiling
  shows most time in pickling, and worker memory spikes. Fix: `initializer=` or shared storage.
- **`fork` after starting threads or initialising CUDA.** Symptom: workers that deadlock intermittently on a
  lock some parent thread held at fork time, or PyTorch's "Cannot re-initialize CUDA in forked subprocess"
  error. Fix: spawn or forkserver.
- **Loading a local model at import time in a module that `spawn` re-imports.** Each worker loads its own
  copy and the machine runs out of memory. Fix: load in the initializer or behind the main guard.

## When to use
- **Measure first.** CPU low while latency is high means I/O-bound; one core pinned means CPU-bound in Python.
- **Agent API calling LLMs, tools and vector stores:** asyncio with async clients such as `httpx` or the
  official async SDKs; wrap unavoidable blocking tools in `asyncio.to_thread`.
- **Heavy parsing, chunking or local inference preprocessing:** a process pool, or a separate worker service.
- **GPU inference:** the work runs in native code, and a dedicated serving engine usually beats rolling your
  own concurrency.
- **Interviews:** justify the choice from the bottleneck, not from preference.

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

## Interview questions
### What is the core difference between asyncio, threads and processes in Python?
- **asyncio** is cooperative scheduling on one thread, switching only at `await`, so tens of thousands of
  network waits are cheap but there is no CPU parallelism.
- **Threads** are preemptive and share memory, giving real concurrency for blocking I/O and GIL-releasing
  C code, but pure-Python bytecode runs one thread at a time in standard CPython.
- **Processes** each have their own interpreter and GIL, so CPU-bound Python scales across cores at the
  cost of pickling, startup and memory per worker.

### Your agent has a web-search tool, a sync CRM SDK tool and a PDF-parsing tool. How do you run them?
- **Search is awaited directly** on the loop with an async client, inside a semaphore and a timeout.
- **The CRM tool goes through `asyncio.to_thread`** with a bounded default executor, because it blocks on
  I/O and releases the GIL while waiting.
- **The parser goes to a process pool** (or a separate worker service), because pure-Python CPU work would
  otherwise hold the GIL and slow every session; check that its inputs and outputs pickle cheaply.

### An async agent API calls a vendor SDK that has no async client. How do you integrate it?
- **Offload each call** with `await asyncio.to_thread(sdk_call, ...)`, so the event loop keeps serving
  other sessions while the call blocks in a worker thread.
- **Bound the pool:** set `loop.set_default_executor(ThreadPoolExecutor(max_workers=N))` at startup and
  cap in-flight calls, because each offloaded call holds a thread.
- **Monitor** executor queue length and offloaded-call latency, and switch to an async client when one
  exists.

### Would free-threaded CPython (PEP 703) change how you design CPU-bound work?
- **Potentially, for CPU work.** Without the GIL, threads could run pure-Python code in parallel while
  sharing memory and model weights, avoiding pickling and per-worker copies.
- **Not yet by default:** it is an optional build, so check its status for your Python version and
  whether every C extension you depend on supports it.
- **Shared state then needs real locking**, because code that was accidentally safe under the GIL can
  race; measure before replacing a working process pool.

### Why would you choose spawn or forkserver over fork for a worker pool?
- **`fork` copies the parent's state**, including locks held by other threads and CUDA state, so
  children can deadlock intermittently or hit "Cannot re-initialize CUDA in forked subprocess".
- **`spawn` and `forkserver` start clean** but re-import your modules, so entry points need the
  `if __name__ == "__main__":` guard and models must load in the initializer, not at import.
- **Pass the context explicitly** with `mp_context=` rather than relying on platform defaults.

### How do you size Uvicorn or Gunicorn workers for an agent service that loads a local model?
- **Memory first:** each worker process duplicates the model and connection pools, so the count comes
  from the memory budget divided by per-worker RSS, not only from cores.
- **Keep the I/O tier async** so each worker handles many concurrent agent sessions, and move CPU-heavy
  parsing to a separately scaled queue-fed worker service.
- **Monitor** RSS per worker, queue depth and oldest-job age.

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
