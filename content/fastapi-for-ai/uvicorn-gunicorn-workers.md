---
id: uvicorn-gunicorn-workers
module: fastapi-for-ai
name: Uvicorn / Gunicorn workers
kind: tool
difficulty: core
est_minutes: 14
status: crisp
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
Uvicorn runs FastAPI's event loop; Gunicorn is a pre-fork manager that supervises several Uvicorn workers.
Workers (processes) use more cores, but each loads its own copy of any in-process model, so AI services
size workers by memory and GPUs, not CPU.

## Why it exists
- **FastAPI is a framework, not a server.** Something must open the socket, parse HTTP, speak ASGI to your
  app and run the asyncio event loop. Uvicorn does that.
- **One process is great for I/O.** A single Uvicorn process handles thousands of concurrent connections
  while handlers mostly await I/O, exactly the shape of an LLM gateway: a request waits seconds for a
  provider using almost no CPU.
- **Limit 1: one core of Python.** On the standard CPython build the global interpreter lock means one
  process executes Python bytecode on one core at a time. The experimental free-threaded builds from
  Python 3.13 relax this, but most AI library stacks still assume the GIL. CPU work (tokenizing,
  JSON-heavy post-processing, local embedding inference, PDF parsing) saturates one core and stalls the
  event loop for everyone.
- **Limit 2: a single point of failure.** If the process crashes, segfaults in a native library or leaks
  memory until killed, the service is down.
- **N workers fix both, but need a supervisor** to fork them, restart the ones that die, recycle leakers
  and pass signals through. Gunicorn was built for that role; in containers it is often handed to the
  orchestrator instead.

## How it works
### Uvicorn alone
- **One process:** `uvicorn app.main:app --host 0.0.0.0 --port 8000` runs one process with one event loop.
- **`--workers N`:** a small supervisor spawns N child processes sharing the listening socket; the kernel
  spreads incoming *connections* across them (not perfectly evenly).
- **The unit is the connection, not the request.** Behind a proxy holding a handful of long-lived
  keep-alive connections, most requests can land on one or two workers while the others idle.
- **Restarts:** recent Uvicorn versions (0.30 onward) also restart children that die.
- **Per-worker startup:** each child runs lifespan startup independently, so every worker builds its own
  HTTP client pool, DB engine and model.

### Gunicorn with Uvicorn workers
`gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4 --bind 0.0.0.0:8000` makes Gunicorn the
master. The master never serves requests: it forks workers, watches a heartbeat from each, and replaces
any that exit.

| Flag | Default | What it does | Watch out |
|---|---|---|---|
| **`--timeout`** | 30 s | kills and replaces a worker that misses its heartbeat that long | async heartbeat runs on the event loop, so a blocking handler (a synchronous model call) gets its worker killed |
| **`--graceful-timeout`** | 30 s | time to finish in-flight requests on shutdown or reload | must cover your longest normal request |
| **`--max-requests` + `--max-requests-jitter`** | | recycle each worker after a number of requests | blunt but effective cure for slow native-library leaks; jitter stops all workers restarting at once |
| **`--preload`** | | import the app in the master before forking, sharing read-only pages copy-on-write | refcounting writes erode sharing over time; CUDA must not be initialised before a fork, so preloading a GPU model is a trap |

- **Import path changed.** Since Uvicorn 0.30 the built-in `uvicorn.workers` module is deprecated in
  favour of the separate `uvicorn-worker` package (`-k uvicorn_worker.UvicornWorker`). The old path still
  works in many versions but warns, so check which path your pinned versions expect.

### Sizing
1. **Start from cores, async-style.** Gunicorn's classic `2 × cores + 1` is for synchronous workers that
   block on I/O. An async worker already overlaps I/O, so start near one worker per core and measure.
2. **Check concurrency per worker with Little's law:** requests in flight = arrival rate × time in system.
   At 50 requests/s and 8 s per LLM call, about 400 requests await the provider at once.
3. **Find the real cap.** One event loop holds 400 pending awaits cheaply, so that needs no extra
   workers. The cap is the shared HTTP client's connection pool (httpx defaults to 100 connections);
   beyond it requests queue inside the client and eventually raise pool timeouts.
4. **Apply the constraint that dominates AI workloads:**

```
workers ≤ (container memory − headroom) ÷ (per-worker RSS, including model)
workers per GPU: usually 1, because each process would load its own copy of the weights
```

### In containers
- **Common choice on Kubernetes:** one Uvicorn process per container, scaling by pod count, because the
  orchestrator already restarts dead processes and scales replicas. Per-pod memory and metrics stay simple.
- **Multiple workers per pod** still make sense when pods are large and per-pod overhead (sidecars, memory
  reserved for the model) is significant.

## Examples
### Worked sizing: API with a local CPU reranker
A FastAPI retrieval service calls a hosted LLM over HTTP and runs a local cross-encoder reranker on CPU.
Numbers are illustrative. Each pod gets 4 vCPU and 8 GiB.

| Measured per worker after startup | GiB |
|---|---|
| Reranker weights and tokenizer | about 1.1 |
| Python, libraries, connection pools | 0.5 |
| **Total RSS** | **1.6** |

- **Current reranker:** CPU says 4 workers; memory says 4 × 1.6 = 6.4 GiB, leaving 1.6 GiB for request
  buffers and tensor-allocation spikes during reranking. It fits: 4 workers.
- **Bigger reranker, 2.6 GiB per worker:** 4 workers need 10.4 GiB, so the pod is OOM-killed under load.
- **Option A:** resize pods to 2 vCPU and about 6 GiB with 2 workers each and double the replicas
  (2 × 2.6 = 5.2 GiB per pod). Same total CPU and worker count, more memory overall because headroom is
  now reserved per smaller pod.
- **Option B:** move the reranker into a dedicated model server, so API workers shrink to ~0.5 GiB each
  and the reranker loads once.
- **GPU variant:** run one worker per GPU or, better, put the model behind an inference server and keep
  FastAPI as a thin async I/O layer.

Deploy command for the 4-worker case (using the `uvicorn-worker` package), with recycling to contain a
slow leak in a native tokenizer:

```
gunicorn app.main:app -k uvicorn_worker.UvicornWorker -w 4 --bind 0.0.0.0:8000 \
  --timeout 120 --graceful-timeout 60 --max-requests 5000 --max-requests-jitter 500
```

- **Why `--timeout 120`:** a large rerank batch run synchronously can block the loop long enough to miss
  the default 30 s heartbeat.
- **A workaround, not a fix:** while the loop is blocked, every other request on that worker stalls too.
- **The real fix:** move CPU work off the loop (`run_in_threadpool` works for libraries such as PyTorch
  that release the GIL during heavy ops; pure-Python work needs a process pool), then lower the timeout
  back.

### Sizing calculator (runnable)
Memory caps workers; Little's law tells you whether each worker's HTTP pool will saturate.

```python
def workers_by_memory(pod_mib, rss_mib, headroom_mib):
    return (pod_mib - headroom_mib) // rss_mib          # integers avoid float floor surprises

def in_flight(requests_per_s, seconds_per_request):
    return requests_per_s * seconds_per_request         # Little's law: L = lambda x W

def plan(cores, pod_mib, rss_mib, headroom_mib, rps, latency_s, pool_size=100):
    by_cpu = cores                                      # async workers: start near 1 per core
    by_mem = workers_by_memory(pod_mib, rss_mib, headroom_mib)
    w = max(1, min(by_cpu, by_mem))
    per_worker = in_flight(rps, latency_s) / w
    return {"by_cpu": by_cpu, "by_mem": by_mem, "workers": w,
            "in_flight_per_worker": per_worker,
            "pool_saturated": per_worker > pool_size}

print(plan(4, 8192, 1638, 1638, rps=50, latency_s=8))   # 1.6 GiB workers
# {'by_cpu': 4, 'by_mem': 4, 'workers': 4, 'in_flight_per_worker': 100.0, 'pool_saturated': False}
print(plan(4, 8192, 2662, 1638, rps=50, latency_s=8))   # 2.6 GiB workers
# {'by_cpu': 4, 'by_mem': 2, 'workers': 2, 'in_flight_per_worker': 200.0, 'pool_saturated': True}
```

With the bigger model, memory halves the workers and each worker's 100-connection pool is now
oversubscribed, so either raise the pool limit or add replicas.

## Production patterns
- **One process per pod on Kubernetes.** For an I/O-bound gateway run plain
  `uvicorn app.main:app --host 0.0.0.0 --port 8000` and scale replicas with the autoscaler. Monitor:
  per-pod CPU (one process tops out near one core), event-loop lag and p99 latency.
- **A checked-in `gunicorn.conf.py` on VMs and large pods.** Set `workers`, `worker_class`, `timeout`,
  `graceful_timeout`, `max_requests` and `max_requests_jitter` in one reviewed file instead of ad hoc
  flags. Monitor: worker restarts and `WORKER TIMEOUT` lines in Gunicorn's log.
- **Recycle leaky workers with jitter.** `--max-requests 5000 --max-requests-jitter 500` contains slow
  native leaks without synchronised restarts. Monitor: per-worker RSS over time (sawtooth is expected,
  a rising floor is not) and latency spikes at recycle time.
- **Connection budget across the fleet.** Keep workers × pool size × replicas under the database's and
  provider's connection limits, and create pools in lifespan, never at import. Monitor: open connections
  on the database and pool-timeout errors in the client.
- **Event-loop lag as a first-class metric.** Run a small background task that sleeps a fixed interval and
  records how late it wakes; export the overshoot. Monitor: lag climbing with load is a blocking call, not
  a worker shortage.

## Trade-offs
- **More workers vs duplicated models.** Workers use more cores and survive single-process crashes, but
  each duplicates the model in memory; once weights dominate RSS, a separate model server beats adding
  workers.
- **Gunicorn's controls vs another layer.** Heartbeat kills, worker recycling and graceful reloads come
  with another layer of signals and config; under Kubernetes with one process per pod, plain Uvicorn is
  often simpler and sufficient.
- **`--preload` speed vs fork safety.** It speeds startup and can share memory, but it breaks anything
  that must be created after the fork (CUDA contexts, threads, some client pools), so it flips from helpful
  to harmful with GPU models.
- **`--max-requests` vs cold starts.** Recycling hides leaks cheaply but periodically cold-starts workers;
  if lifespan startup takes tens of seconds, recycling causes visible latency spikes.

## Pitfalls
- **DB pools or HTTP clients at import time with `--preload`.** Symptom: connection errors or hangs in
  workers, because sockets and threads created before fork do not survive it correctly.
- **Synchronous model inference inside `async def` handlers.** Symptom: Gunicorn logs `WORKER TIMEOUT`
  and kills workers under load, and every in-flight request on them fails.
- **Sizing workers by CPU count and ignoring per-worker model memory.** Symptom: pods OOM-killed during
  traffic peaks, often reported as mysterious restarts with exit code 137.
- **In-process state, then more workers.** A dict cache or a rate-limit counter per process. Symptom: rate
  limits N times too generous and cache hit rates that drop as workers increase.
- **Workers behind a proxy reusing a few keep-alive connections.** Symptom: one worker pinned near 100% CPU
  with high event-loop lag while siblings idle, so p99 barely improves.
- **Ignoring total connections.** Many workers per pod and many pods. Symptom: the database or provider
  rejects connections because workers × pool size × replicas exceeds its limit.

## When to use
- **I/O-bound LLM gateway on Kubernetes:** start with one Uvicorn process per container and scale replicas.
- **VMs or large pods:** use Gunicorn with Uvicorn workers for in-pod supervision, leak recycling and
  heartbeat kills.
- **Size by:** measured RSS and GPU count before CPU.
- **Move CPU-heavy or GPU work out** of the API process (thread pool, process pool or a model server)
  instead of adding workers to hide event-loop blocking.
- **Measure:** per-worker RSS, event-loop lag, worker restarts and connection counts.
- **Interviews:** explaining why worker count is a memory question for AI services is the distinguishing
  answer.

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
