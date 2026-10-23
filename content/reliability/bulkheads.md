---
id: bulkheads
module: reliability
name: Bulkheads
kind: pattern
difficulty: core
est_minutes: 13
status: typed
lesson_type: decision
agent_relevance: core
related:
  - reliability/circuit-breakers
  - reliability/tenant-level-quotas-and-noisy-neighbor-isolation
  - reliability/fault-tolerance
  - reliability/backpressure-and-load-shedding
  - fastapi-for-ai/concurrency-limits
  - fastapi-for-ai/async-database-access-and-connection-pooling
  - infra-capacity-economics/shared-vs-isolated-model-serving-infrastructure
  - infra-capacity-economics/queueing-theory
sources:
  - Nygard, 2007 — Release It!
  - Official docs: Resilience4j
---

## TL;DR
A bulkhead gives each tool, model endpoint, workload class or tenant its own capped share of workers,
connections or pods, so one hung tool cannot stall every agent run. It bounds damage before anything
detects failure; the price is capacity that cannot be borrowed.

## For agent builders
- **Why it matters for agents.** An agent service calls many dependencies per turn: the model, a vector
  store, and several tools, often in parallel. One hung tool can hold every worker; then runs that never
  touch it fail too, and so do your health checks.
- **Where it shows up in your stack.** An `asyncio.Semaphore` per tool in the tool executor, one HTTP client
  per upstream, separate queues for background agents and interactive chat, Kubernetes namespaces with a
  `ResourceQuota`, and Resilience4j bulkheads on the JVM.
- **What you control.** The compartment boundaries (per tool, per model, interactive versus batch agents),
  each cap (sized with Little's law from p99 latency), how long a caller waits for a permit, and what the
  agent does when a compartment is full.
- **What you can skip.** Queueing theory beyond Little's law. Concurrency = rate × time in system is enough
  to size every cap in this lesson.

## Decision case
### Scenario
One fleet of 300 agent workers runs interactive support agents by day and a document-review batch by
night. Each run holds a worker for its whole duration. All figures are illustrative.
- **Interactive load:** 3 runs a second at 15 seconds each, so 3 × 15 = 45 workers by Little's law.
- **Nightly batch:** 10,000 runs at 60 seconds each, 600,000 worker-seconds, due within 8 hours.
- **Tools:**
  - a customer CRM at 6 calls a second, 0.5 s at the p99, with a 30-second timeout its slowest
    month-end queries need;
  - a code sandbox with 12 VMs at 2 calls a second, 4 s at the p99.
- **The incident to design for:** the CRM hangs until its timeout. On a shared pool that is 6 × 30 = 180
  stuck workers.
- **Constraints:**
  - interactive p95 queue wait under 2 seconds;
  - the batch finishes overnight;
  - health checks stay green;
  - no new node pool this quarter;
  - two engineers and two weeks.

### Options
| Option | How it works | CRM hang | Interactive wait while batch runs | Batch time | Infra and effort |
|---|---|---|---|---|---|
| **A. Double the shared pool** | 600 workers, one queue, one HTTP client | 180 of 600 held: survives | fails: 10,000 queued batch runs take every free worker | 600,000 ÷ 600 = 1,000 s | twice the pods; trivial |
| **B. Per-tool bulkheads, one queue** | semaphore per tool (CRM 25, sandbox 12), one HTTP client per upstream, a separate health path | capped at 25; other CRM calls get `tool_busy` in 50 ms | fails: batch still fills the shared queue | 600,000 ÷ 300 = 2,000 s | no new infra; about a week |
| **C. B plus separate queues** | B's bulkheads inside two consumer groups: interactive 150 workers, batch 150 | capped at 25 in each group | 45 needed of 150; 45 + 25 stuck = 70 even with the CRM hung | 600,000 ÷ 150 = 4,000 s, about 67 min | no new infra; about two weeks |
| **D. Separate deployments** | own node pool per workload class | isolated | isolated | as C or better | needs a new node pool: fails |

### Decision
Choose **C**, because B's tool bulkheads fix the hang but only a separate queue fixes the batch.
- **CRM hang:** a cap of 25 holds at most 25 workers. At 6 calls a second it still admits everything
  until the CRM's average latency passes 25 ÷ 6 ≈ 4.2 s, over eight times its 0.5 s p99.
- **Interactive wait:** the interactive group never queues behind batch runs. Even with the CRM pinned it
  uses 70 of its 150 workers.
- **Batch:** 67 minutes fits the night many times over, so capping it at half the fleet costs nothing
  that matters.
- **Sandbox:** its cap of 12 equals the VM count, so the bulkhead also protects the sandbox itself.
- **Infra:** no new node pool. D's extra isolation buys nothing these constraints ask for.

**What would change it:**
- **A batch deadline under about an hour:** 600,000 ÷ 3,600 ≈ 167 workers, more than 150. Use a soft
  partition (a guaranteed minimum plus shared overflow) or resize the split.
- **Interactive traffic past about 8 runs a second:** 125 ÷ 15 ≈ 8.3, the rate at which 150 workers minus
  25 CRM-stuck ones run out.
- **A tenant paying for isolation, or a vendor key limit to isolate:** move to D.

### Trade-offs accepted
- **The batch group's 150 workers idle by day.** Scale batch consumers down on a schedule, and watch
  total utilization against the bill.
- **`tool_busy` answers carry less context.** Watch the `tool_busy` rate per tool, and score runs that
  answered without the CRM on the eval set.
- **Caps can be mis-sized.** Rejections at normal traffic mean the cap was sized from the mean. Watch
  rejections per bulkhead when the dependency is healthy.
- **The hang still costs 25 workers for 30 seconds each.** Pair the bulkhead with a breaker so permits
  free up once failures are observed.

### Review rubric
- **Every cap is derived with Little's law** from tool-call rate and p99 latency, with the tolerated
  average latency (cap ÷ rate) stated.
- **The oversubscription check is shown:** with one compartment pinned, the healthy paths' demand still
  fits.
- **Workload classes are separated** when batch and interactive agents share a fleet, not only
  dependencies.
- **Health and admission paths are named** as their own compartment.
- **A full compartment has a defined agent behavior,** such as a `tool_busy` result or a degraded answer,
  never an unbounded wait.
- **At least two triggers that would change the split** are stated as numbers.

## Why it exists
- **One slow dependency becomes a full outage.** A service with one pool of 200 workers calls an LLM, a
  vector database and a customer's CRM API. The CRM hangs until the 30-second timeout. At only 5 CRM calls
  per second, Little's law (concurrency = arrival rate × time in system) gives 5 × 30 = 150 stuck workers,
  three quarters of the pool, reached within half a minute.
- **Healthy paths die with it.** Add LLM traffic and the pool is full; requests that need only the
  healthy LLM and vector store are rejected, health checks share the pool and fail, and pods restart.
- **Workload mixing does the same with no failure at all.** A batch summarization agent that is merely slow
  and plentiful fills the pool, interactive users wait behind it, and nothing is broken.
- **The usual fixes do not cap the aggregate.** A bigger pool only raises the threshold: occupancy under a
  hang is rate × timeout, set by the caller's traffic, and retries push that rate up until any fixed pool
  fills. A shorter timeout only shrinks the number, it does not cap it.
- **Breakers react too late here.** A circuit breaker needs a minimum number of observed failures first,
  and a hung dependency that never errors can take minutes to register.
- **The missing property is a hard ceiling per compartment.** A ship's hull is divided into watertight
  compartments so one breach floods one section, not the vessel; a software bulkhead does the same for
  worker threads, connections, semaphores, queues and pods.
- **Static, not reactive.** Where a circuit breaker reacts after it detects failure, a bulkhead bounds the
  damage whether or not anything detects it.

## How it works
### Size with Little's law
```
cap ≈ peak rate × p99 latency + burst headroom     (and a small fraction of the total pool)
tolerated average latency = cap ÷ rate
```
- **Use a high percentile, not the mean,** because LLM and tool latency has a long tail and the cap must
  absorb it.
- **In the failure case latency equals the timeout,** so occupancy can never exceed the cap no matter how
  slow the dependency gets. The formula tells you both how large a cap is safe and the worst damage the
  dependency can do.
- **Read it backwards.** A cap *C* on a dependency called at rate λ tolerates an average latency of up to
  *C* ÷ λ before callers are rejected. A CRM cap of 20 at 5 calls per second admits everything until CRM
  latency averages 4 seconds, about thirteen times its normal 300 ms; beyond that the bulkhead sheds.
- **That number is the real design choice:** how much slowdown you will absorb before you would rather
  degrade.
- **Agents multiply the rate.** The rate a tool sees is runs per second × calls of that tool per run, and
  parallel tool calls arrive together, so size from tool-call rate, not request rate.

### Oversubscription is fine; check the right condition
- **Caps do not have to sum to the pool,** because dependencies rarely all hang at once.
- **What must hold:** when any one compartment is pinned at its cap, the remaining capacity still covers the
  healthy paths' demand. Include any pair that shares a plausible common cause, such as two APIs behind the
  same customer VPN.
- **Check that condition, not the sum.**

### Five forms, from cheapest to strongest
| Form | Mechanism | Use when | Cost |
|---|---|---|---|
| 1. Semaphore | counting semaphore per dependency; acquire before the call, release after; fail immediately or wait a very short bounded time | non-blocking clients; the default in async agent services | nearly free |
| 2. Thread pool | separate executor per dependency | blocking clients and CPU-bound work (a synchronous SDK, a tokenizer, PDF parsing) that would stall the event loop or each other | threads and context switches |
| 3. Connection pool | separate HTTP client with its own connection limits per upstream | always, *in addition to* a semaphore | a client object per upstream |
| 4. Queue | separate queues and consumer groups per workload class (interactive versus batch agents, per tenant tier) | batch and interactive share a fleet | more queues and consumers |
| 5. Deployment | separate pods, node pools, namespaces with resource quotas, or clusters; separate provider keys or model deployments | a tenant pays for isolation or a vendor limit must be isolated | highest |

- **Why the connection pool needs its own compartment:** it is itself a shared resource. A hung upstream
  holds its connections open, and if every dependency shares one client, the healthy ones cannot get a
  socket even though their semaphores show headroom.
- **Why queues isolate:** the consumer fleet is the compartment, so a flood of batch jobs can fill the batch
  queue to any depth without delaying an interactive message.
- **Why deployments are strongest:** they are the only form that also isolates vendor-side limits.

### What to compartment by
- **By dependency:** the most common, and the baseline; in an agent, each tool endpoint and each model.
- **By workload class:** protects interactive agents' latency from batch and background agent volume.
- **By tenant tier:** how you sell isolation.
- **By criticality, the one teams forget:** health and readiness endpoints, authentication and the
  admission layer must never share a compartment with LLM calls, or an upstream outage turns into
  restarts and lock-outs.

### Reject or wait
- **When a compartment is full, fail fast or wait a bounded, short time for a permit.**
- **Unbounded waiting is forbidden** because it recreates the shared-pool problem one level up: the waiters
  are now the stuck resource.
- **A rejection should enable graceful degradation** (answer without the CRM context, serve from cache,
  enqueue for later) or an honest 503 with `Retry-After`.
- **In an agent, a rejection is a tool result.** Return "tool busy" to the model so it can continue without
  that tool, rather than failing the run.

### How it composes with breakers and load shedding
| Mechanism | Kind | Role |
|---|---|---|
| Bulkhead | static limit | bounds damage regardless of health; also caps the load you put on the dependency |
| Circuit breaker | dynamic reaction | stops sending once failure is observed |
| Load shedding | global admission control | drops the lowest-value work when the whole service is saturated |

- **The bulkhead protects the dependency too:** a cap of 20 is the most concurrent load your service can
  ever put on the CRM, which matters when the CRM's own capacity is the thing that is failing.
- **They stack:** the bulkhead keeps a hung dependency from exhausting the pool while the breaker
  accumulates its minimum calls; once the breaker opens, even the bulkhead's permits are freed; if the
  whole service is saturated, shedding drops the lowest-value work first.

## Diagram
Each dependency gets its own permits and its own HTTP client, and health checks bypass them all; a full
compartment degrades its own agent steps instead of draining the shared pool.

```mermaid
flowchart TD
    R["Agent step"] --> W["Worker pool: 200"]
    H["Health checks"] --> HP["Dedicated health path"]
    W --> V["Vector DB bulkhead: 10"]
    W --> C{"CRM tool bulkhead: 20 free?"}
    W --> L["LLM bulkhead: 100"]
    C -->|"yes"| CC["CRM client, 20 connections"]
    C -->|"no, after 50 ms"| D["Degrade: answer without CRM"]
    L --> LC["LLM client, 100 connections"]
    CC -.->|"hung: at most 20 stuck"| X["CRM API"]
```

## Examples
### Per-dependency semaphore bulkheads with a degrade path
Semaphore bulkheads with a short bounded wait, separate HTTP clients so the connection pool is partitioned
too, and a degrade path when a compartment is full. The caps come from Little's law with illustrative
traffic numbers.

```python
import asyncio, httpx

class BulkheadFull(Exception): pass

class Bulkhead:
    """Caps concurrent calls to one dependency; a caller waits at most max_wait for a slot."""
    def __init__(self, name, max_concurrent, max_wait=0.25):
        self.name, self.max_wait = name, max_wait
        self.sem = asyncio.Semaphore(max_concurrent)

    async def run(self, fn, *args, **kw):
        try:
            await asyncio.wait_for(self.sem.acquire(), self.max_wait)
        except asyncio.TimeoutError:
            raise BulkheadFull(f"{self.name} at capacity")     # fail fast; never queue without bound
        try:
            return await fn(*args, **kw)
        finally:
            self.sem.release()                                  # released on success, error or cancellation

# Little's law: concurrency = peak rate x tail latency, plus headroom. Illustrative numbers:
# LLM 20 rps x 3 s = 60 -> cap 100; vector DB 40 rps x 0.05 s = 2 -> cap 10; CRM 5 rps x 0.3 s = 1.5 -> cap 20
# (generous headroom for the CRM, but still a tenth of the 200-worker pool, so a hang can take at most 20).
LLM = Bulkhead("llm", 100)
VEC = Bulkhead("vector_db", 10)
CRM = Bulkhead("crm_api", 20, max_wait=0.05)

# Separate HTTP clients: the connection pool is a shared resource as well.
llm_http = httpx.AsyncClient(limits=httpx.Limits(max_connections=100), timeout=30.0)
crm_http = httpx.AsyncClient(limits=httpx.Limits(max_connections=20), timeout=5.0)

async def handle(req):
    docs = await VEC.run(search, req.query)
    try:
        account = await CRM.run(lookup_account, req.user_id)
    except BulkheadFull:
        account = None                                          # degrade: answer without CRM context
    return await LLM.run(generate, req, docs, account)
```

- **Outcome with the CRM hung:** at most 20 permits plus 20 connections are ever tied up; the other 180
  workers keep serving requests that need only retrieval and the model.
- **Degradation is fast:** CRM-dependent requests degrade in 50 milliseconds instead of hanging for 30
  seconds.
- **The `finally` matters:** a permit leaked on cancellation is a compartment that slowly fills with ghosts.

### Agent tool executor: parallel tool calls behind per-tool bulkheads
The model often emits several tool calls in one step. Each runs behind its own tool's bulkhead, and a full
compartment becomes a tool result instead of an exception. It reuses `Bulkhead` and `BulkheadFull` above.

```python
TOOL_BULKHEADS = {
    "search":    Bulkhead("search", 30),
    "crm":       Bulkhead("crm_api", 20, max_wait=0.05),
    "run_code":  Bulkhead("sandbox", 8, max_wait=0.1),     # sandboxes are scarce: small cap
}

async def run_one(call, tools):
    bh = TOOL_BULKHEADS[call["name"]]
    try:
        out = await bh.run(tools[call["name"]], **call["args"])
        return {"id": call["id"], "ok": True, "content": out}
    except BulkheadFull:
        return {"id": call["id"], "ok": False, "error": "tool_busy",
                "message": f"{call['name']} is at capacity; continue without it or try later"}

async def run_tool_calls(calls, tools):
    # Parallel tool calls from one model step; each competes only inside its own compartment.
    return await asyncio.gather(*(run_one(c, tools) for c in calls))
```

- **A hung sandbox holds at most 8 workers,** however many runs ask for code execution at once; every
  other run gets `tool_busy` within 100 ms.
- **The bulkhead does not end a hang.** Each tool still needs its own timeout (as the HTTP clients above
  have), or the 8 runs holding permits wait forever and the compartment never frees.
- **The model sees `tool_busy`** in the tool results and can finish the answer with the other tools.

### Sizing calculator with the oversubscription check
Runnable: computes normal demand, the latency each cap tolerates, and whether the healthy paths still fit
when one compartment is pinned at its cap.

```python
POOL = 200
deps = {                      # name: (peak_rps, p99_latency_s, cap)
    "llm":       (20, 3.0, 100),
    "vector_db": (40, 0.05, 10),
    "crm_api":   (5, 0.3, 20),
}

def demand(rps, latency):
    return rps * latency      # Little's law: concurrency = rate x time in system

for name, (rps, p99, cap) in deps.items():
    print(f"{name}: normal {demand(rps, p99):.1f}, cap {cap}, "
          f"sheds once avg latency > {cap / rps:.2f}s")

for hung, (_, _, cap) in deps.items():   # pin one compartment at its cap
    healthy = sum(demand(r, l) for n, (r, l, _) in deps.items() if n != hung)
    left = POOL - cap
    print(f"{hung} hung: {left} workers left, healthy paths need {healthy:.1f} -> "
          f"{'ok' if left >= healthy else 'CASCADE'}")
# crm_api: normal 1.5, cap 20, sheds once avg latency > 4.00s
# crm_api hung: 180 workers left, healthy paths need 62.0 -> ok
```

### Worked scenario: the CRM hang, before and after
| Quantity | Shared pool | With bulkheads |
|---|---|---|
| CRM occupancy while hung | 5 × 30 = 150 workers | capped at 20 |
| LLM demand | 20 × 3 = 60 workers | 60, served from the 180 left |
| Total demand vs pool | 210 vs 200: saturated | 80 vs 200: fine |
| CRM-dependent request | hangs 30 s, then fails | `BulkheadFull` in 50 ms, answered without CRM |
| Health checks | miss, pods restart | own path, stay green |

### Worked scenario: a background agent fleet next to chat
Illustrative: interactive support agents and a nightly batch of 5,000 document-review agents share one
fleet of 200 agent workers. Each run holds a worker for its whole duration.

| Quantity | One shared queue | Separate queues and consumer groups |
|---|---|---|
| Batch runs in flight | up to 200 (all workers) | capped at 120 by the batch consumer group |
| Interactive runs (2 per second × 20 s per run) | 40 needed, wait behind batch | 40 served from their own 80 workers |
| Interactive p95 queue wait | minutes while the batch drains | near zero |
| Batch completion | fastest possible | slower, but finishes overnight |

## Production patterns
- **Resilience4j bulkheads on the JVM.** A semaphore bulkhead per dependency with `maxConcurrentCalls` and a
  short `maxWaitDuration`; a thread-pool bulkhead (`maxThreadPoolSize`, `coreThreadPoolSize`,
  `queueCapacity`) for blocking SDKs. Monitor available permits and rejected calls per bulkhead.
- **Blocking SDKs off the event loop.** In asyncio, run a synchronous client on its own executor via
  `loop.run_in_executor(executor, fn)` with a small dedicated `ThreadPoolExecutor(max_workers=...)`, so a
  hang stalls only that executor. Monitor event-loop lag; spikes mean blocking code leaked in.
- **One HTTP client per upstream.** Create a separate client with its own connection limits per upstream
  (as with `httpx.Limits` above) and its own timeout. Monitor connection-pool wait time per client.
- **Per-tool bulkheads in the tool executor.** Give each tool its own semaphore, with small caps for scarce
  resources such as code sandboxes or browser sessions, and turn rejections into tool results. Monitor
  `tool_busy` rate per tool and permits in use.
- **Separate queues for batch and interactive agents.** Give batch work its own queue and consumer group
  with its own concurrency, so batch depth never delays interactive messages. Monitor per-queue depth and
  age of the oldest message.
- **Namespace quotas for deployment bulkheads.** In Kubernetes, give each workload class or tenant tier its
  own namespace with a `ResourceQuota`, or its own node pool, so one class cannot schedule over another.
  Monitor quota usage and pending pods per namespace.

## Trade-offs
- **Isolation vs utilization.** Capacity in one compartment cannot be borrowed by another, so the CRM's 20
  permits sit idle when the CRM is quiet. When load is bursty and uncorrelated, soft partitions (a
  guaranteed minimum per compartment plus a shared overflow pool) recover most of the waste.
- **Cheap vs strong forms.** Semaphores are cheap but only bound non-blocking work; thread pools isolate
  blocking and CPU-bound calls at the cost of threads; separate deployments isolate everything, including
  vendor limits, at the cost of money. Escalate only when the cheaper form demonstrably leaks.
- **Short vs long wait.** A tiny `max_wait` fails fast but rejects during ordinary micro-bursts; a longer
  one absorbs bursts but ties callers up. Interactive paths want the short wait; batch paths can afford to
  queue, in their own queue.
- **Fine vs coarse compartments.** Many fine compartments give precise isolation and metrics but multiply
  configuration and the chance of mis-sizing one; coarse compartments are simple but share blast radius.
  Partition by failure domain, not by code module.
- **Degraded answers vs failed runs.** Returning `tool_busy` keeps runs alive but lets the agent answer with
  less information; failing the run is honest but costs the whole turn. Decide per tool which one is
  optional.

## Pitfalls
- **Sizing the cap from mean latency.** Symptom: steady trickle of `BulkheadFull` rejections on a healthy
  dependency at peak, because the tail of slow but successful calls holds permits the mean did not account
  for. Fix: size from p99.
- **Setting the cap as a large fraction of the pool.** Symptom: a bulkhead of 150 out of 200 "works" until
  the dependency hangs, and the service still cascades because 150 stuck workers is an outage.
- **Sharing one HTTP client across dependencies behind separate semaphores.** Symptom: the CRM semaphore
  shows five in flight while CRM calls hang, because the LLM's slow responses hold every connection in the
  common pool.
- **Blocking calls inside async code.** Symptom: every compartment reports headroom, yet everything
  including health checks stalls, because a synchronous SDK call froze the event loop that all the
  semaphores live on.
- **Letting health and readiness endpoints share the worker pool.** Symptom: during a dependency outage the
  orchestrator restarts healthy pods, converting a partial outage into a total one.
- **No degrade path for `BulkheadFull`.** Symptom: user-visible error rate rises in lockstep with the share
  of requests that happen to touch the saturated dependency, even though the answer could have been given
  without it.
- **Releasing the permit only on the success path.** Symptom: capacity drifts downward over days; the
  compartment reads full with no real work in flight, and a restart "fixes" it.
- **Sizing tool caps from request rate.** Symptom: a tool's bulkhead rejects at normal traffic because each
  agent run calls it several times in parallel. Fix: size from tool calls per second.

## When to use
- **Use when** a service talks to more than one dependency with different latency or reliability, or
  serves more than one workload class; nearly every tool-calling agent service qualifies.
- **Start with the cheap baseline:** a semaphore per dependency sized from Little's law, a separate HTTP
  client per upstream, and a dedicated path for health checks. Promote to queue-level separation when
  batch and interactive traffic share a fleet.
- **Escalate to deployments** when a tenant pays for it or a vendor key limit must be isolated.
- **Measure** per-compartment in-flight count, rejection rate and wait time; alert on sustained saturation
  of any one compartment, which is an early warning long before the breaker trips.
- **Interviews:** the question is "one slow dependency took down the whole service, how do you prevent
  it". The complete answer is timeouts to bound each call, bulkheads to bound the aggregate, and breakers
  to stop sending, with the sizing arithmetic to show why each is needed.

## Role lens
- **Engineer:** Configures per-dependency semaphores and per-upstream clients, moves blocking SDKs onto
  their own executor, and writes the degrade path for every `BulkheadFull`.
- **Architect:** Decides the compartment boundaries, by dependency, workload class, tenant tier and
  criticality, and whether each lives in-process, at the queue, or as a separate deployment.
- **FDE:** Uses bulkheads so a customer's flaky legacy system cannot take down the agent, and separates
  the customer's bulk backfills from their users' interactive sessions inside one deployment.

## Check yourself
**Q:** An agent service runs one shared pool of 200 workers with 30-second timeouts. It calls an LLM
(about 20 requests per second, 3 seconds each) and a CRM API (5 requests per second, normally 300 ms).
The CRM API hangs. Walk through what happens with numbers, then show how bulkheads change the outcome.
**A:** With the CRM hung, each CRM call occupies a worker until the 30-second timeout. Little's law gives
5 × 30 = 150 workers stuck in steady state, reached within about 30 seconds. LLM traffic needs 20 × 3 = 60
workers, so total demand is 210 against a pool of 200: the pool saturates, LLM-only requests queue or
fail, health checks miss, and pods restart. With a CRM bulkhead capped at 20 permits and a 50 ms maximum
wait, CRM occupancy can never exceed 20; 180 workers remain, more than the 60 the LLM path needs.
CRM-dependent requests receive `BulkheadFull` within 50 ms and degrade by answering without CRM context.
A separate HTTP client for the CRM keeps its hung sockets out of the LLM's connection pool. Shrinking the
CRM timeout to 5 seconds additionally lowers worst-case demand to 25 even without the cap, and a circuit
breaker on top releases the 20 permits once failures are confirmed.

## Interview questions
### What is a bulkhead, and why isn't a bigger shared pool an equivalent fix?
- **Definition:** a hard concurrency ceiling per compartment (dependency, workload class, tenant tier), so
  one exhausted compartment cannot drain capacity the others need.
- **A bigger pool only moves the threshold,** because occupancy during a hang is rate × timeout, set by
  caller traffic; retries raise the rate until any fixed pool fills.
- **The bulkhead turns that unbounded number into one you chose.**

### Your agents make 20 model calls per second at a 3-second p99. How would you size the LLM bulkhead?
- **Little's law:** 20 × 3 = 60 concurrent calls in normal operation, so a cap around 100 leaves burst
  headroom.
- **Check what it tolerates:** 100 ÷ 20 = 5 seconds of average latency before shedding starts.
- **Check the pool:** with that compartment pinned at 100, the remaining workers must still cover every
  healthy path's demand.

### Your agent's code-execution sandbox started hanging and now chat agents that never run code are timing out. How do you fix the design?
- **Give the sandbox tool its own bulkhead,** a small semaphore sized from tool calls per second × p99, so a
  hang can hold only that many workers.
- **Return `tool_busy` to the model** when the compartment is full, so runs continue without code execution
  instead of waiting.
- **Separate its clients and executors** too, and add a breaker so permits free up once the hang is
  confirmed; monitor permits in use per tool.

### Every compartment shows free permits, yet the whole service, including health checks, is stalled. What do you suspect?
- **A blocking call on the event loop,** such as a synchronous SDK inside async code; all semaphores live on
  that loop, so nothing progresses.
- **Or a shared HTTP client:** a hung upstream holds every connection in the common pool while semaphores
  show headroom.
- **Fix:** move blocking work to its own executor, give each upstream its own client, and watch event-loop
  lag and connection-pool wait.

### When would you not use hard per-dependency bulkheads?
- **When load is bursty and uncorrelated and idle capacity is costly:** hard caps strand permits, so use soft
  partitions with a guaranteed minimum plus a shared overflow pool.
- **When there is only one dependency and one workload class,** a single pool with timeouts and load
  shedding is simpler and loses nothing.

### How do bulkheads, circuit breakers and load shedding work together during a dependency hang?
- **Bulkhead first:** caps how many workers the hung dependency can hold while the breaker is still
  accumulating its minimum calls.
- **Breaker next:** once failures are confirmed it stops sending, freeing the bulkhead's permits.
- **Shedding last:** if the whole service is saturated, global admission control drops the lowest-value
  work first.

## Quiz
### Q: What is the essential difference between a bulkhead and a circuit breaker?
- [ ] A bulkhead retries failed calls; a breaker does not
- [x] A bulkhead statically bounds resource use per compartment regardless of health; a breaker dynamically stops calls after observing failures
- [ ] A bulkhead applies to databases and a breaker to HTTP APIs
- [ ] They are two names for the same mechanism in different libraries
> The bulkhead works before and without detection: a hung dependency can never hold more than its cap.
> The breaker needs evidence of failure first, then removes load entirely. They are complementary, not
> interchangeable.

### Q: A dependency is called 10 times per second with a 20-second timeout. In the worst case, how many concurrent calls can it tie up without a bulkhead?
- [ ] 10
- [ ] 20
- [x] 200
- [ ] 2
> Little's law: concurrency equals arrival rate times time in system, and when the dependency hangs the
> time in system is the timeout, so 10 × 20 = 200. The bulkhead cap replaces that number with a ceiling
> you choose.

### Q: Why give each upstream its own HTTP client even when per-dependency semaphores are already in place?
- [ ] Separate clients make requests faster by avoiding TLS handshakes
- [x] The connection pool is itself a shared resource, and a hung upstream's open connections can exhaust it for healthy upstreams
- [ ] Semaphores do not work with HTTP clients
- [ ] It is required by the HTTP/2 specification
> A semaphore bounds how many calls your code starts, but a shared client has its own connection limit;
> sockets held open by a hung dependency are unavailable to the others, so the healthy paths stall while
> their semaphores show plenty of permits.

## Use cases & projects
### Use cases
- **Tool-calling agents with many integrations.** A semaphore per tool keeps one hung CRM, browser or
  sandbox from stalling every agent run.
- **Background agents next to interactive chat.** Separate queues and consumer groups keep nightly batch
  agents from delaying users' sessions.
- **Scarce agent resources.** Small caps on code sandboxes, headless browsers or GPU-backed tools stop a
  burst of runs from exhausting them.
- **Premium tenants on an agent platform.** Deployment-level bulkheads (own namespace, own provider key)
  isolate a paying customer from everyone else, including vendor limits.

### Mini project
**Contain a hung tool in a parallel-tool agent (1-2 hours).**
- **Goal:** show that per-tool bulkheads keep chat-only agent runs fast while one tool hangs.
- **Steps:** build an async agent executor with three fake tools and a shared pool of 50 workers; make one
  tool hang for 30 seconds; send a mix of runs that do and do not use it; measure p95 latency of the runs
  that never call the hung tool, first with one shared pool, then with per-tool `Bulkhead` caps sized by
  Little's law and a `tool_busy` result.
- **Done when:** a table shows the unaffected runs' p95 collapsing in the shared-pool version and staying
  flat with bulkheads, and the hung tool never holds more than its cap.
