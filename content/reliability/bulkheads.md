---
id: bulkheads
module: reliability
name: Bulkheads
kind: pattern
difficulty: core
est_minutes: 13
status: reviewed
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
A ship's hull is divided into watertight compartments so one breach floods one section, not the vessel.
A software bulkhead partitions a shared resource (worker threads, connections, semaphores, queues, pods)
per dependency, workload class or tenant, so exhaustion in one compartment cannot drain what the others
need. Where a circuit breaker reacts after it detects failure, a bulkhead bounds the damage in advance,
whether or not anything detects it. The price is capacity that cannot be borrowed across compartments.

## Why it exists
Picture a service with one pool of 200 workers that calls an LLM provider, a vector database and a
customer's CRM API. The CRM API hangs: connections open, no bytes return, each call lasts until the
30-second timeout. Every request that touches the CRM now holds a worker for 30 seconds. At only 5 CRM
calls per second, Little's law (concurrency = arrival rate × time in system) says 5 × 30 = 150 workers are
stuck in steady state, three quarters of the pool, reached within half a minute. Add normal LLM traffic
and the pool is full. Requests that need only the LLM and the vector store, both perfectly healthy, are
now rejected or queued. Health checks share the pool too, so the orchestrator restarts the pods. One slow
dependency became a complete outage.

The usual first responses do not solve this. A bigger pool only raises the threshold: occupancy under a
hang is still rate × timeout, a number set by the caller's traffic and not by you, and client retries
push that rate up until any fixed pool fills. Timeouts bound each call, but the arithmetic above
already includes the timeout; aggregate occupancy is rate × timeout, and a shorter timeout only shrinks
the number, it does not cap it. A circuit
breaker helps once it trips, but it needs a minimum number of observed failures first, and a hung
dependency that never returns an error can take minutes to register. None of these stops the healthy
paths from being starved in the meantime.

The same shape appears with workload mixing rather than failure: a batch summarization job that is
merely slow and plentiful fills the pool and interactive users wait behind it. Nothing is broken, and
the service is still unusable. The missing property is a hard ceiling per compartment, which is what a
bulkhead supplies.

## How it works
### Size with Little's law
The concurrency a dependency can consume is its call rate times its latency, and in the failure case
latency equals the timeout. The bulkhead cap for a dependency is therefore its peak rate × a high
percentile of latency (p99, not the mean, because LLM latency has a long tail and the cap must absorb
it), with headroom for bursts, and crucially it must be a small fraction of the total pool. The formula
tells you two things: how large a cap is safe, and how much damage the dependency can do at worst,
because occupancy can never exceed the cap no matter how slow it gets.

Read it backwards as well. A cap *C* on a dependency called at rate λ tolerates an average latency of
up to *C* ÷ λ before callers start being rejected. A CRM cap of 20 at 5 calls per second admits
everything until CRM latency averages 4 seconds, about thirteen times its normal 300 ms; beyond that
the bulkhead starts shedding. That number is the real design choice: how much slowdown you will absorb
before you would rather degrade.

The caps do not have to sum to the pool. Oversubscription is normal, because dependencies rarely all
hang at once; what must hold is that, when any one compartment (or any pair that shares a plausible
common cause, such as two APIs behind the same customer VPN) is pinned at its cap, the remaining
capacity still covers the healthy paths' demand. Check that condition, not the sum.

### Five forms, from cheapest to strongest
1. **Semaphore bulkhead.** A counting semaphore per dependency in the process. Acquire before the call,
   release after; if no permit is free, fail immediately or wait for a very short bounded time. Nearly
   free, and sufficient when the client is non-blocking. This is the default choice in async services.
2. **Thread-pool bulkhead.** A separate executor per dependency. It isolates blocking clients and
   CPU-bound work (a synchronous SDK, a tokenizer, PDF parsing) so they cannot stall the event loop or
   each other. It costs threads and context switches, which is why it is reserved for blocking work.
3. **Connection-pool bulkhead.** A separate HTTP client, with its own connection limits, per upstream.
   This is needed *in addition to* a semaphore because the connection pool is itself a shared resource:
   a hung upstream holds its connections open, and if every dependency shares one client, the healthy
   ones cannot get a socket even though their semaphores show headroom.
4. **Queue bulkhead.** Separate queues and consumer groups per workload class (interactive versus batch,
   per tenant tier). The consumer fleet is the compartment, so a flood of batch jobs can fill the batch
   queue to any depth without delaying an interactive message.
5. **Deployment bulkhead.** Separate pods, node pools, namespaces with resource quotas, or clusters per
   workload class or tenant tier, and separate provider keys or model deployments. Strongest isolation,
   highest cost, and the only form that also isolates vendor-side limits.

### What to compartment by
By dependency is the most common and should be the baseline. By workload class protects interactive
latency from batch volume. By tenant tier is how you sell isolation. By criticality is the one teams
forget: health and readiness endpoints, authentication and the admission layer must never share a
compartment with LLM calls, or an upstream outage turns into restarts and lock-outs.

### Reject or wait
When a compartment is full, the caller either fails fast or waits a bounded, short time for a permit.
Unbounded waiting is forbidden because it recreates the shared-pool problem one level up: the waiters
are now the stuck resource. A rejection should carry enough information for a graceful degradation
(answer without the CRM context, serve from cache, enqueue for later) or an honest 503 with
`Retry-After`.

### How it composes with breakers and load shedding
A bulkhead is a static limit that bounds damage regardless of health. It also protects the dependency:
a cap of 20 is the most concurrent load your service can ever put on the CRM, which matters when the
CRM's own capacity is the thing that is failing. A breaker is a dynamic reaction
that stops sending once failure is observed. Load shedding is global admission control under overload.
They stack: the bulkhead keeps a hung dependency from exhausting the pool while the breaker accumulates
its minimum calls; once the breaker opens, even the bulkhead's permits are freed; and if the whole
service is saturated, shedding drops the lowest-value work first.

## Example
Per-dependency semaphore bulkheads with a short bounded wait, separate HTTP clients so the connection
pool is partitioned too, and a degrade path when a compartment is full. The caps come from Little's law
with illustrative traffic numbers.

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

With the CRM hung, at most 20 permits plus 20 connections are ever tied up; the other 180 workers keep
serving requests that need only retrieval and the model, and CRM-dependent requests degrade in 50
milliseconds instead of hanging for 30 seconds. The `finally` matters: a permit leaked on cancellation
is a compartment that slowly fills with ghosts.

## Trade-offs
- Isolation versus utilization: capacity in one compartment cannot be borrowed by another, so the CRM's
  20 permits sit idle when the CRM is quiet. When load is bursty and uncorrelated, soft partitions (a
  guaranteed minimum per compartment plus a shared overflow pool) recover most of the waste.
- Semaphores are cheap but only bound non-blocking work; thread pools isolate blocking and CPU-bound
  calls at the cost of threads; separate deployments isolate everything, including vendor limits, at the
  cost of money. Escalate only when the cheaper form demonstrably leaks.
- A tiny `max_wait` fails fast but rejects during ordinary micro-bursts; a longer one absorbs bursts but
  ties callers up. Interactive paths want the short wait; batch paths can afford to queue, in their own
  queue.
- Many fine compartments give precise isolation and precise metrics but multiply configuration and the
  chance of mis-sizing one; coarse compartments are simple but share blast radius. Partition by failure
  domain, not by code module.

## Pitfalls
- Sizing the cap from mean latency. The symptom: steady trickle of `BulkheadFull` rejections on a healthy
  dependency at peak, because the tail of slow but successful calls holds permits the mean did not
  account for.
- Setting the cap as a large fraction of the pool. The symptom: a bulkhead of 150 out of 200 "works"
  until the dependency hangs, and the service still cascades because 150 stuck workers is an outage.
- Sharing one HTTP client across dependencies behind separate semaphores. The symptom: the CRM
  semaphore shows five in flight while CRM calls hang, because the LLM's slow responses hold every
  connection in the common pool.
- Blocking calls inside async code. The symptom: every compartment reports headroom, yet everything
  including health checks stalls, because a synchronous SDK call froze the event loop that all the
  semaphores live on.
- Letting health and readiness endpoints share the worker pool. The symptom: during a dependency outage
  the orchestrator restarts healthy pods, converting a partial outage into a total one.
- No degrade path for `BulkheadFull`. The symptom: user-visible error rate rises in lockstep with the
  share of requests that happen to touch the saturated dependency, even though the answer could have
  been given without it.
- Releasing the permit only on the success path. The symptom: capacity drifts downward over days; the
  compartment reads full with no real work in flight, and a restart "fixes" it.

## When to use
Introduce bulkheads as soon as a service talks to more than one dependency with different latency or
reliability, or serves more than one workload class. The baseline is cheap: a semaphore per dependency
sized from Little's law, a separate HTTP client per upstream, and a dedicated path for health checks.
Promote to queue-level separation when batch and interactive traffic share a fleet, and to deployment
separation when a tenant pays for it or a vendor key limit must be isolated. Measure per-compartment
in-flight count, rejection rate and wait time; alert on sustained saturation of any one compartment,
which is an early warning long before the breaker trips. In interviews the question is "one slow
dependency took down the whole service, how do you prevent it", and the complete answer is timeouts to
bound each call, bulkheads to bound the aggregate, and breakers to stop sending, with the sizing
arithmetic to show you understand why each is needed.

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
