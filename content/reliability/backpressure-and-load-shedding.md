---
id: backpressure-and-load-shedding
module: reliability
name: Backpressure & load shedding
kind: pattern
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - fastapi-for-ai/backpressure
  - serving-inference/backpressure
  - node-ts-for-ai/streams-and-backpressure
  - infra-capacity-economics/queueing-theory
  - reliability/bulkheads
  - reliability/circuit-breakers
  - reliability/tenant-level-quotas-and-noisy-neighbor-isolation
  - serving-inference/rate-limiting
sources:
  - Beyer et al., 2016 — Site Reliability Engineering (Handling Overload; Addressing Cascading Failures)
  - Little, 1961 — A Proof for the Queuing Formula L = λW
  - Nichols & Jacobson, 2012 — Controlling Queue Delay
  - Bronson et al., 2021 — Metastable Failures in Distributed Systems
---

## TL;DR
When requests arrive faster than a system can serve them, queues grow without bound, latency climbs
past every timeout, clients retry, and throughput collapses. **Backpressure** pushes the "slow down"
signal upstream through bounded queues, blocking producers and explicit 429/503 responses so the
source stops sending. **Load shedding** deliberately rejects some work, cheaply and early, so the
rest completes within its latency budget. Both exist because an overloaded system that tries to serve
everyone serves no one.

## Why it exists
Little's law says the number of requests inside a system equals arrival rate times time spent:
`L = λ × W`. A service that completes 50 requests per second and receives 60 accumulates 10 per
second in its queue forever. After one minute, 600 requests are waiting, each facing a 12-second
wait before service even starts. If the client timeout is 10 seconds, every one of those requests is
already dead on arrival, yet the server will still spend full effort serving them to clients that
have hung up and retried. Now the real arrival rate is 60 plus the retries. This is congestive
collapse: throughput of *useful* work (goodput) falls toward zero while the machines run at 100%.

The naive alternative, "add a bigger queue", makes it worse, *because* queue depth converts directly
into latency and buys nothing once wait time exceeds what callers will tolerate. "Autoscale" helps
only if new capacity arrives faster than the queue grows, which is rarely true for GPU-backed
services where a replica takes minutes to pull weights and warm up. Rate limiting by fixed quota
helps with abuse but not with legitimate surges, and it cannot see the server's real condition.

The system must therefore do two things the naive design does not: tell the source to slow down,
and refuse work it cannot finish in time. LLM serving makes this concrete. A GPU server has a fixed
number of sequences it can batch; beyond that, requests queue, time-to-first-token inflates, and
users retry or send the same prompt from another tab. The provider's own 429 responses are
backpressure aimed at you, and honouring them with backoff rather than retrying harder is the
difference between a slow hour and a dead one.

## How it works
### Backpressure: propagate the limit upstream
Backpressure is the chain of signals that makes the fastest component in a pipeline run at the
speed of the slowest. Inside a process it is a bounded queue: a producer that calls `put()` on a full
queue blocks or fails instead of allocating more memory. Across a network it is TCP flow control
(the receive window), HTTP 429 or 503 with `Retry-After`, gRPC `RESOURCE_EXHAUSTED`, or a streaming
protocol in which the consumer pulls chunks at its own pace. In message systems it is consumer lag
you watch and a producer that pauses on it. The design rule is that every hop must have a bounded
buffer and a defined behaviour when it is full, *because* an unbounded buffer anywhere silently
absorbs the signal and the overload reappears there as memory exhaustion.

### Load shedding: refuse early, refuse cheap
Shedding is the decision to drop or reject some requests to keep the others within their SLO. The
mechanics that matter:
- **Reject at the front door.** The cheapest point to refuse is before any expensive work: before
  retrieval, before tokenisation, before taking a GPU slot. A rejection that costs a millisecond
  preserves capacity; one that happens after ten seconds of queueing has already spent it.
- **Use queue wait time as the overload signal**, not CPU. For a GPU service CPU is meaningless, and
  for any service the quantity users feel is how long a request waited. Bound the queue by time:
  if the oldest waiting request has been there longer than the budget allows, start shedding.
- **Size the queue from the SLO**: `max_queue ≈ service_rate × max_acceptable_wait`. At 50 req/s
  and a 2 s wait budget, a queue deeper than 100 only manufactures timeouts.
- **Shed by priority.** Classify work as interactive, background or batch, by tenant tier, or by
  whether it is a retry. Under pressure drop batch first, then background, and protect health checks
  and control-plane traffic absolutely. Priority turns shedding from random failure into a policy.
- **Prefer LIFO or CoDel under overload.** A FIFO queue serves the oldest request, which is the one
  most likely to have been abandoned. Serving newest-first, or dropping when the minimum sojourn
  time exceeds a target for a sustained interval (CoDel), keeps goodput up during the surge.
- **Adaptive concurrency limits.** Instead of a fixed limit, measure latency and raise the limit while
  latency stays flat, lower it when latency rises (gradient or TCP-Vegas-style controllers). This
  tracks the service's real capacity as model sizes, prompt lengths and downstream speed change.

### Where retries fit
Retries are a load multiplier. If a client retries each rejected request up to three times, every
one of them comes back three more times, so a 10% excess becomes roughly 40% at first order; and
because the retries are themselves rejected, total attempts can approach four times the original
traffic before the budget runs out. Shedding only works if clients back off on 429/503 with jitter, honour `Retry-After`, and keep a
retry budget. Circuit breakers on the client side stop the storm entirely once rejection rates are
high; bulkheads keep one overloaded dependency from consuming every worker.

### Goodput, not throughput
The metric that tells you whether this is working is successful requests completed within the SLO per
second. A server reporting 100% utilisation and 50 req/s throughput while 90% of responses arrive
after the client gave up has a goodput near zero. Dashboards should show queue wait distribution,
rejection rate by priority, and goodput side by side.

### LLM-specific shape
Capacity is in concurrent sequences and tokens, not requests, so admission control should estimate
cost (prompt length, requested max tokens) and admit by a token budget. Streaming adds a second
backpressure path: a slow consumer of server-sent events does not slow generation, so the server
needs a bounded per-connection buffer and cancellation on disconnect, or it generates tokens nobody
will read. Long generations hold their slot for the whole duration, so a few multi-minute requests
can starve interactive ones unless they are in a separate pool.

## Example
Admission control for a chat endpoint: shed at the door by priority, bound the queue wait by the
SLO, release the slot if the client left while waiting. The limits would be derived from measured
service rate and the latency budget, then adjusted by an adaptive controller.

```python
import asyncio
from fastapi import FastAPI, HTTPException, Request, Response

app = FastAPI()
MAX_INFLIGHT, MAX_QUEUE, MAX_WAIT_S = 32, 64, 2.0   # capacity, service_rate x wait budget, SLO slice
slots = asyncio.Semaphore(MAX_INFLIGHT)
waiting = 0                                          # single event loop: no lock needed

@app.post("/v1/chat")
async def chat(req: Request):
    global waiting
    priority = req.headers.get("x-priority", "interactive")
    batch_cap = MAX_QUEUE // 4                       # batch work gets a quarter of the queue
    # 1) shed at the door: a 1 ms rejection instead of a 10 s timeout
    if waiting >= MAX_QUEUE or (priority == "batch" and waiting >= batch_cap):
        raise HTTPException(503, "overloaded", headers={"Retry-After": "5"})
    waiting += 1
    try:
        # 2) bounded queue wait: beyond the budget the answer would arrive too late to matter
        await asyncio.wait_for(slots.acquire(), timeout=MAX_WAIT_S)
    except asyncio.TimeoutError:
        raise HTTPException(503, "queue wait exceeded", headers={"Retry-After": "5"})
    finally:
        waiting -= 1
    try:
        if await req.is_disconnected():              # 3) client gave up while queued: do not spend the GPU
            return Response(status_code=204)
        return await run_inference(await req.json()) # your model client; token-cost admission goes here
    finally:
        slots.release()
```

One runtime caveat: before Python 3.12, `asyncio.wait_for` could cancel `Semaphore.acquire()` at the
same instant it succeeded and leak the slot; on older interpreters wrap the acquire so a timed-out
acquisition releases what it won. Walk the numbers: if inference averages 640 ms per request with 32
slots, service rate is about 50 req/s; a 64-deep queue therefore represents roughly 1.3 s of wait, inside the 2 s budget, and
anything beyond is rejected in microseconds with a `Retry-After` the client should honour. Export
`waiting`, rejections by priority and queue wait time, and the picture of an overload becomes a
flat goodput line instead of a cliff.

## Trade-offs
- Shedding sacrifices some requests to keep latency bounded for the rest, which is right whenever a
  late answer is worthless; it flips to queueing when callers will genuinely wait, as in batch jobs.
- Blocking backpressure keeps every request eventually served but stalls producers and can deadlock
  if a producer also holds resources the consumer needs; explicit rejection is safer across services.
- Fixed concurrency limits are predictable and easy to reason about but go stale as prompts, models
  and dependencies change; adaptive limits track reality at the cost of a controller to tune.
- LIFO serving maximises goodput under overload but starves old requests and breaks fairness; use it
  only during overload and return to FIFO when the queue drains.
- Priority shedding protects the work that matters most but needs honest classification; once every
  team marks its traffic "critical", the mechanism degrades to random drops.

## Pitfalls
- Unbounded queues anywhere in the path (an async job queue, an executor pool, a channel), visible as
  latency rising for minutes before a memory crash instead of a quick, clean rejection.
- Shedding after the expensive work: rejecting at the model call after retrieval and prompt assembly
  already ran, so the server stays saturated while returning errors.
- Clients that treat 429 and 503 as transient and retry immediately without backoff, so every shed
  request returns within milliseconds and arrival rate doubles; the symptom is rejections climbing
  faster than traffic.
- Using CPU or request count as the overload signal for GPU-bound work; the GPU is saturated at 20% CPU
  and queue wait time was the signal all along.
- Health checks sharing the shed queue, so under load the load balancer marks healthy nodes dead and
  removes capacity exactly when it is needed, turning overload into an outage.

## When to use
Every service with a finite capacity needs both: bounded buffers with a defined full behaviour at
every hop, and an admission decision at the front door driven by queue wait time and priority. Start
by measuring service rate and the latency budget, set queue bounds from them, and add adaptive limits
once prompt lengths or model mix vary. Choose blocking backpressure inside a process or pipeline,
explicit 429/503 across service boundaries, and always pair shedding with client backoff and retry
budgets. Watch goodput, queue wait percentiles and rejection rate by priority; if rejections never
happen, the limits are either generous or missing. In day-to-day work this is the semaphore in front
of the model client and the `Retry-After` handling in the SDK. In interviews, the depth signal is
deriving queue size from Little's law and explaining why early rejection raises goodput.

## Role lens
- **Engineer:** Puts a bounded semaphore and a queue-wait timeout in front of every model and tool
  client, honours provider `Retry-After`, and exports goodput and rejection metrics by priority.
- **Architect:** Defines the priority classes and tenant tiers that shedding enforces, sets latency
  budgets that queue bounds derive from, and places bulkheads so one workload cannot starve another.
- **FDE:** Explains to a customer why their burst traffic sees 429s rather than slow answers, tunes
  priorities to their business rules, and sizes capacity against their measured peak arrival rate.

## Check yourself
**Q:** A GPU-backed summarisation service handles about 40 requests per second with p50 latency under
a second. A marketing email sends 90 requests per second for ten minutes. Clients time out at 8
seconds and retry twice. Describe what happens without protection, then design the protection and
justify each number.
**A:** The queue grows by 50 requests per second, so after 8 seconds the oldest waiting request has
already exceeded the client timeout; from then on the server serves abandoned requests while clients
add two retries each, pushing effective arrival toward 270 per second. Goodput falls near zero though
the GPUs are fully busy, and the collapse can outlast the ten-minute surge because retries keep it
fed, the metastable pattern. Protection: bound the queue by wait time, around 40 × (8 s minus
service time), so no admitted request can miss its deadline; shed above that at the front door with
503 and `Retry-After`; classify the campaign's traffic as batch so it gets a small share and
interactive users stay protected; require the client SDK to back off with jitter and cap retries at a
budget; add an adaptive concurrency limit so the bound follows real latency; and alert on goodput and
rejection rate rather than utilisation. The campaign then completes slower, not never, and nobody
else notices.

## Quiz
### Q: Why does adding a larger queue not fix an overloaded service?
- [ ] Larger queues consume GPU memory that the model needs
- [x] Queue depth converts directly into wait time; once wait exceeds what callers tolerate, the server serves requests that have already been abandoned
- [ ] Queues only work for CPU-bound services, not GPU-bound ones
- [ ] Larger queues reduce the service rate by increasing lock contention
> By Little's law, waiting requests equal arrival rate times wait time. A deeper queue lets wait time
> grow past client timeouts, so capacity is spent on work nobody will receive.

### Q: Where should load shedding decisions be made, and why?
- [ ] After inference, so the server can return a partial answer instead of an error
- [ ] At the client, because only it knows its own priority
- [x] As early and cheaply as possible, before retrieval and prompt assembly, so a rejection preserves capacity instead of spending it
- [ ] In the database layer, where contention is easiest to measure
> A rejection that costs microseconds protects the service. Rejecting after seconds of queueing or
> after expensive preprocessing has already consumed the resources shedding was meant to save.

### Q: Which signal is the best trigger for shedding on a GPU-backed LLM endpoint?
- [ ] CPU utilisation above 80%
- [ ] Total requests per second exceeding last week's average
- [x] Queue wait time of admitted requests approaching the latency budget
- [ ] Number of open TCP connections
> Queue wait is what users experience and tracks real capacity regardless of model size or prompt
> length. CPU is irrelevant for GPU-bound work, and request counts ignore how expensive each request is.
