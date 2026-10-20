---
id: uvicorn-gunicorn-workers
module: fastapi-for-ai
name: Uvicorn / Gunicorn workers
kind: tool
difficulty: core
est_minutes: 14
status: reviewed
related:
  - fastapi-for-ai/asgi-lifecycle
  - fastapi-for-ai/lifespan
  - fastapi-for-ai/async-endpoints
  - fastapi-for-ai/graceful-shutdown-and-worker-draining
  - fastapi-for-ai/concurrency-limits
  - python-for-ai/asyncio-vs-threads-vs-multiprocessing
  - backend-fundamentals/kubernetes
  - serving-inference/model-serving
sources:
  - Official docs: Uvicorn
  - Official docs: Gunicorn
  - Official docs: FastAPI
---

## TL;DR
Uvicorn is the ASGI server that runs FastAPI's event loop; Gunicorn is a pre-fork process manager that
can run several Uvicorn workers and supervise them. One Python process runs Python code on roughly one
core at a time, so workers (processes) are how you use more cores; but each worker loads its own copy of
any in-process model, so for AI services the right worker count is usually set by memory and GPUs, not CPU.

## Why it exists
FastAPI is a framework, not a server: something must open the socket, parse HTTP, speak ASGI to your app
and run the asyncio event loop. Uvicorn does that. A single Uvicorn process handles thousands of
concurrent connections well as long as handlers mostly await I/O, which is exactly the shape of an LLM
gateway: a request waits seconds for a provider while using almost no CPU.

Two limits push you beyond one process. First, on the standard CPython build the global interpreter lock
means one process executes Python bytecode on one core at a time (the experimental free-threaded builds
from Python 3.13 relax this, but most AI library stacks still assume the GIL), so CPU work (tokenizing, JSON-heavy post-processing,
local embedding inference, PDF parsing) saturates one core and stalls the event loop for everyone.
Second, a single process is a single point of failure: if it crashes, segfaults in a native library or
leaks memory until it is killed, the service is down. Running N worker processes addresses both, but
somebody has to fork them, restart the ones that die, recycle ones that leak and pass signals through.
That supervisor role is what Gunicorn was built for, and in containers it is often handed to the
orchestrator instead.

## How it works
### Uvicorn alone
`uvicorn app.main:app --host 0.0.0.0 --port 8000` runs one process with one event loop. With
`--workers N`, Uvicorn starts a small supervisor that spawns N child processes sharing the listening
socket; the kernel spreads incoming *connections* across them (not perfectly evenly). The unit is the
connection, not the request: if traffic arrives through a proxy that holds a handful of long-lived
keep-alive connections, most requests can land on one or two workers while the others idle. Recent Uvicorn
versions (0.30 onward) also restart children that die. Each child runs lifespan startup independently, so
every worker builds its own HTTP client pool, DB engine and model.

### Gunicorn with Uvicorn workers
`gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4 --bind 0.0.0.0:8000` makes Gunicorn the
master process. The master never serves requests; it forks workers, watches a heartbeat from each, and
replaces any that exit. It adds operational controls Uvicorn alone is thinner on:
- **`--timeout`** (default 30 s): a worker that fails to heartbeat for that long is killed and replaced.
  For async workers the heartbeat runs on the event loop, so a handler that blocks the loop (a synchronous
  model call) gets its worker killed, which is a symptom worth recognising.
- **`--graceful-timeout`** (default 30 s): how long workers get to finish in-flight requests on shutdown
  or reload.
- **`--max-requests` with `--max-requests-jitter`**: recycle each worker after a number of requests, a
  blunt but effective cure for slow memory leaks in native libraries; jitter prevents all workers
  restarting at once.
- **`--preload`**: import the app in the master before forking, so read-only memory pages can be shared
  copy-on-write. CPython reference counting writes to objects it touches, so sharing erodes over time, and
  CUDA must not be initialised before a fork, so preloading a GPU model is a trap.

Since Uvicorn 0.30 the built-in `uvicorn.workers` module is deprecated in favour of the separate
`uvicorn-worker` package (`-k uvicorn_worker.UvicornWorker`); the old path still works in many versions but
warns, so check which import path your pinned versions expect.

### Sizing
Gunicorn's classic `2 × cores + 1` guidance is for synchronous workers that block on I/O. An async worker
already overlaps I/O, so start near one worker per core and measure. Concurrency per worker is a separate
question, answered by Little's law: requests in flight = arrival rate × time in system. At 50 requests/s
and 8 s per LLM call, about 400 requests are awaiting the provider at once. One event loop can hold 400
pending awaits cheaply, so that alone needs no extra workers; what caps it is the shared HTTP client's
connection pool (httpx defaults to 100 connections), beyond which requests queue inside the client and
eventually raise pool timeouts. Then apply the constraint that dominates AI workloads:

```
workers ≤ (container memory − headroom) ÷ (per-worker RSS, including model)
workers per GPU: usually 1, because each process would load its own copy of the weights
```

### In containers
On Kubernetes the orchestrator already restarts dead processes and scales replicas, so a common choice is
one Uvicorn process per container and scaling by pod count, which keeps per-pod memory and metrics simple.
Multiple workers per pod still make sense when pods are large and per-pod overhead (sidecars, memory
reserved for the model) is significant.

## Example
A worked sizing decision for a FastAPI service that does retrieval: it calls a hosted LLM over HTTP, but
also runs a local cross-encoder reranker on CPU. Numbers are illustrative.

The node gives each pod 4 vCPU and 8 GiB. Measured resident memory per worker after startup is 1.6 GiB:
about 1.1 GiB of reranker weights and tokenizer, 0.5 GiB of Python, libraries and connection pools.

- CPU-only reasoning says 4 workers. Memory says 4 × 1.6 = 6.4 GiB, leaving 1.6 GiB for request buffers
  and spikes in tensor allocation during reranking. That fits, so 4 workers it is.
- If the team upgrades to a reranker whose workers measure 2.6 GiB each, 4 workers need 10.4 GiB: the pod
  is OOM-killed under load. Options: resize pods to 2 vCPU and about 6 GiB with 2 workers each and
  double the replicas (2 × 2.6 = 5.2 GiB per pod; same total CPU and worker count, more memory overall
  because headroom is now reserved per smaller pod), or move the reranker into a dedicated model server so the API workers shrink to
  ~0.5 GiB each and the reranker loads once.
- A configuration that sends the reranker to a GPU node should run one worker per GPU, or better, put the
  model behind an inference server and keep FastAPI as a thin async I/O layer.

The deploy command for the 4-worker case (using the `uvicorn-worker` package), with recycling to contain
a slow leak in a native tokenizer:

```
gunicorn app.main:app -k uvicorn_worker.UvicornWorker -w 4 --bind 0.0.0.0:8000 \
  --timeout 120 --graceful-timeout 60 --max-requests 5000 --max-requests-jitter 500
```

`--timeout 120` is raised because a large rerank batch run synchronously can block the loop long enough to
miss the default 30 s heartbeat. That is a workaround, not a fix: while the loop is blocked, every other
request on that worker stalls too. The real fix is moving the CPU work off the loop (`run_in_threadpool`
works for libraries such as PyTorch that release the GIL during heavy ops; pure-Python work needs a process
pool), then lowering the timeout back.

## Trade-offs
- More workers use more cores and survive single-process crashes, but every worker duplicates the model
  in memory; once weights dominate RSS, a separate model server beats adding workers.
- Gunicorn adds heartbeat kills, worker recycling and graceful reloads, but it is another layer of signals
  and config; under Kubernetes with one process per pod, plain Uvicorn is often simpler and sufficient.
- `--preload` speeds startup and can share memory, but it breaks anything that must be created after the
  fork (CUDA contexts, threads, some client pools), so it flips from helpful to harmful with GPU models.
- `--max-requests` recycling hides leaks cheaply, but it periodically cold-starts workers; if lifespan
  startup takes tens of seconds, recycling causes visible latency spikes.

## Pitfalls
- Creating DB pools or HTTP clients at import time with `--preload`; the symptom is connection errors or
  hangs in workers because sockets and threads created before fork do not survive it correctly.
- Running synchronous model inference inside `async def` handlers; the symptom is Gunicorn logging
  `WORKER TIMEOUT` and killing workers under load, with every in-flight request on them failing.
- Sizing workers by CPU count and ignoring per-worker model memory; the symptom is pods OOM-killed during
  traffic peaks, often reported as mysterious restarts with exit code 137.
- Using in-process state (a dict cache, a rate-limit counter) and then adding workers; the symptom is
  rate limits that are N times too generous and cache hit rates that drop as workers increase.
- Adding workers behind a proxy that reuses a few keep-alive connections; the symptom is one worker
  pinned near 100% CPU with high event-loop lag while its siblings idle, so p99 barely improves.
- Running many workers per pod and many pods without reckoning total connections; the symptom is the
  database or provider rejecting connections because workers × pool size × replicas exceeds its limit.

## When to use
For an I/O-bound LLM gateway on Kubernetes, start with one Uvicorn process per container and scale
replicas. Use Gunicorn with Uvicorn workers on VMs or large pods where you want in-pod supervision, leak
recycling and heartbeat kills. Size workers by measured RSS and GPU count before CPU. Move CPU-heavy or
GPU work out of the API process (thread pool, process pool or a model server) instead of adding workers to
hide event-loop blocking. Measure per-worker RSS, event-loop lag, worker restarts and connection counts. In
interviews, explaining why worker count is a memory question for AI services is the distinguishing answer.

## Role lens
- **Engineer:** Picks the run command, sets timeouts and recycling, keeps blocking calls off the event loop,
  and ensures resources are created per worker in lifespan, not at import.
- **Architect:** Decides whether models live inside API workers or in a separate inference tier, and
  whether scaling happens by workers per host or replicas per cluster.
- **FDE:** Fits the service into the customer's fixed VM or node sizes, choosing worker counts that their
  memory limits and database connection caps actually allow.

## Check yourself
**Q:** A FastAPI service that calls a hosted LLM runs as 1 Uvicorn process per pod. Under load p99 latency
climbs although CPU is at 35% of the pod's 4 cores. A colleague proposes `--workers 4`. Is that the right
fix?
**A:** Possibly, but first find out why one process is slow at 35% pod CPU. One process can use at most
about one core of Python execution, so 35% of four cores could mean that one core is saturated, which more
workers would help. But if the event loop is blocked by a synchronous call (a sync HTTP client, a tokenizer,
JSON over large payloads), more workers only spread the stall. I would check event-loop lag and per-core
usage. If the loop is CPU-bound on legitimate work, adding workers (or replicas) is right, checking that
memory and DB connection limits allow it. If something blocks, fix it with an async client or a thread pool.

## Quiz
### Q: Why does adding Uvicorn workers help a CPU-bound FastAPI service but barely help a slow I/O-bound one?
- [ ] Workers share one event loop, so they only speed up CPU work
- [x] Each worker is a separate process with its own interpreter lock, while one event loop already overlaps I/O waits
- [ ] Uvicorn disables async I/O when more than one worker is running
- [ ] Workers increase the provider's rate limit for the service
> The interpreter lock limits one process to roughly one core of Python execution, so processes add CPU
> capacity. Async I/O waits already overlap within one loop, so the bottleneck is elsewhere, usually upstream.

### Q: A service loads a 3 GB model in lifespan and runs 4 Gunicorn workers. Roughly how much memory goes to model weights?
- [ ] About 3 GB, because workers share the parent's memory
- [x] About 12 GB, because each worker runs lifespan and loads its own copy
- [ ] About 0.75 GB per worker, because the model is split across workers
- [ ] Nothing until the first request, because models load lazily
> Lifespan runs in each worker process, so each one loads the weights. Copy-on-write sharing from
> `--preload` does not apply to models loaded after fork, and erodes even for preloaded objects.

### Q: Gunicorn logs `WORKER TIMEOUT` and restarts Uvicorn workers during traffic spikes. What is the most likely cause?
- [ ] The client disconnected before the response was complete
- [ ] `--max-requests` is set too low
- [x] A handler is blocking the event loop, so the worker cannot send its heartbeat to the master
- [ ] The provider's API returned 429 responses
> For async workers the heartbeat runs on the event loop. A synchronous model call or CPU-heavy step inside
> an async handler starves it, and after `--timeout` the master kills the worker and its in-flight requests.
