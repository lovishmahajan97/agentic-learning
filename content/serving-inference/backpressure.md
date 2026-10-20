---
id: backpressure
module: serving-inference
name: Backpressure
kind: pattern
difficulty: core
est_minutes: 14
related:
  - serving-inference/batching-and-concurrency-limits
  - serving-inference/timeouts
  - serving-inference/cancellation
  - serving-inference/rate-limiting
  - reliability/backpressure-and-load-shedding
  - infra-capacity-economics/queueing-theory
  - fastapi-for-ai/backpressure
  - node-ts-for-ai/streams-and-backpressure
sources:
  - Little, 1961 — A Proof for the Queuing Formula L = λW
  - Beyer et al., 2016 — Site Reliability Engineering (Handling Overload; Addressing Cascading Failures)
  - Official docs: vLLM (engine arguments and metrics)
---

## TL;DR
Backpressure is how an overloaded stage tells the stage feeding it to slow down, instead of silently
buffering work until memory or latency blows up. An inference server has a hard ceiling set by KV-cache
memory and decode bandwidth, so it must bound how much it admits, measure how long work waits, and reject
the rest quickly with a 429 or 503 and a `Retry-After`. Without it a traffic spike becomes slow timeouts
for everyone instead of fast rejections for some.

## Why it exists
An inference server has a fixed capacity. The running batch is capped by KV-cache memory (`max_num_seqs`
and free cache blocks in vLLM) and the rate at which it emits tokens is capped by memory bandwidth.
Arrivals do not know this. When the arrival rate λ exceeds the service rate μ for even a few seconds, the
difference has to accumulate somewhere.

The naive design, accept every connection and park it in an unbounded queue, moves the problem rather
than solving it. Little's law, L = λW, says the number of requests in the system equals arrival rate
times time-in-system. If the queue is unbounded, W grows without limit, and every queued request
eventually outlives its client's timeout. The client retries, which raises λ further on a system that
was already saturated. Meanwhile the GPU keeps prefilling 8k-token prompts for connections nobody is
listening to, so *useful* throughput falls exactly when demand is highest. That is the cascading-failure
pattern, and LLM workloads make it worse because each unit of work is expensive and long-lived.

"Just autoscale" is the second naive answer. Adding a GPU node means pulling an image, loading tens of
gigabytes of weights and warming up, which takes minutes; the spike is either over or the service has
already collapsed. Backpressure is what keeps the system serving *some* traffic correctly while scaling
catches up. The principle: fail fast and selectively at the edge, where rejection costs microseconds,
rather than slowly and universally in the middle, where every failure has already consumed GPU time.

## How it works
### Where the pressure originates
In a continuous-batching engine the scheduler holds a running batch and a waiting queue. Each decode
step it tries to admit waiting requests; admission fails when the sequence count would exceed
`max_num_seqs` or when no free KV blocks remain. When a *running* request needs a block and none is
free, the engine preempts a sequence (swapping or recomputing its cache), which is the first sign of
overload. Engines expose these as metrics, for instance vLLM's running and waiting request gauges and
its KV-cache usage percentage. Queue wait time, not GPU utilisation, is the pressure signal: a GPU at
95% with an empty queue is healthy; at 95% with a queue growing by eight requests a second it is minutes
from collapse.

### Propagating it upstream
A request path is client → gateway → application → engine, and each hop must do two things: bound its
own buffer, and translate "I am full" into something the previous hop understands. HTTP already has the
vocabulary: 429 means *this caller* exceeded its allowance, 503 means *the server* is out of capacity;
both carry `Retry-After`. Two rules follow from the economics. The rejection decision must be made
before any GPU work starts, because rejecting after prefill wastes the most expensive part of the
request. And the decision must be cheap, ideally a counter check, because under overload you make it
thousands of times a second.

### Admission policies, simplest first
1. **Concurrency limit.** A semaphore sized from Little's law: at 4 requests/s and 8 s mean latency you
   need about 32 in flight; set the limit near the engine's real capacity and reject beyond it. Simple,
   but one number cannot describe a mix of 500-token and 50,000-token requests.
2. **Bounded queue with a wait budget.** Allow a short queue, but reject any request that has waited
   longer than a budget (say 1–2 s for interactive traffic). This is the CoDel insight from network
   queue management: control queue *delay*, not queue *length*, because length is meaningless when
   service times vary 100× between requests.
3. **Token-aware admission.** The real cost is tokens. Admit against a budget of prompt tokens plus
   `max_tokens`, because one 100k-token document request displaces fifty chat turns and a request
   count hides that.
4. **Priority shedding.** When over capacity, drop batch and background work before interactive
   traffic, because the cost of a dropped request differs by an order of magnitude between them.
5. **Adaptive limits.** Measure the latency gradient and shrink the limit when it rises, the TCP-Vegas
   idea used by adaptive concurrency libraries, because any static limit drifts as the prompt mix and
   the model change.

### Streaming and slow consumers
Streaming adds pressure in the reverse direction. The server produces tokens faster than a slow client
reads them, so the application buffers; the engine keeps generating and holding KV memory for a consumer
that may have gone away. Bound the per-connection buffer, detect disconnects, and cancel the generation
(see cancellation), otherwise capacity leaks to dead streams.

### Queues as deliberate backpressure
For non-interactive work a durable queue with a bounded consumer pool turns rejection into latency:
producers enqueue freely, consumers pull at the GPU's pace. This only works if the queue has a maximum
depth or age; an unbounded queue is the original problem wearing a different hat.

## Example
An admission gate in front of an engine: a semaphore sized from Little's law, a wait budget after which
the request is shed with a `503` and `Retry-After`, a disconnect check so no GPU work is done for a
caller who left while queued, and the queue-wait histogram that is the real pressure signal.

```python
import asyncio, time
from fastapi import FastAPI, HTTPException, Request, Response

app = FastAPI()
MAX_CONCURRENT = 32      # Little's law: ~4 req/s × ~8 s latency, trimmed to the engine's real capacity
WAIT_BUDGET_S = 1.5      # longest an interactive request may queue before we shed it
sem = asyncio.Semaphore(MAX_CONCURRENT)

@app.post("/v1/generate")
async def generate(req: Request):
    body = await req.json()
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=WAIT_BUDGET_S)
    except asyncio.TimeoutError:
        # Shed at the edge: costs us microseconds, and tells the caller exactly what to do.
        raise HTTPException(503, "overloaded", headers={"Retry-After": "2"})
    queue_wait_ms = (time.monotonic() - t0) * 1000   # export as a histogram: this IS the pressure signal
    try:
        if await req.is_disconnected():               # caller gave up while queued: do no GPU work
            return Response(status_code=499)
        return await call_engine(body)               # vLLM / provider call, with its own timeout
    finally:
        sem.release()
```

The two numbers to tune are the concurrency limit (start from Little's law, then lower it until the
engine's waiting queue stays near zero under load) and the wait budget (from the product's latency
budget: if users abandon at 5 s and generation takes 3 s, queueing longer than 1.5–2 s is pointless).
In production the semaphore is usually token-weighted and per-priority, and the same shape appears
inside the engine as `--max-num-seqs`.

## Trade-offs
- Shedding early converts some requests into fast 503s instead of all requests into slow timeouts; it
  flips when callers cannot retry (synchronous webhooks, batch jobs with no scheduler), where a longer
  queue with a strict age limit is kinder.
- A static concurrency limit is predictable and trivial to reason about, but it is tuned for one prompt
  mix; when 50k-token documents start arriving it is either too high (OOM, preemption) or too low (idle
  GPU), and token-aware or adaptive limits cost that simplicity back.
- Queue-delay control keeps latency bounded regardless of request size, but the wait budget is a product
  decision that must be revisited whenever generation length or the latency SLO changes.
- A durable queue hides overload from producers entirely, which is right for pipelines and wrong for
  chat: once a reply takes longer than a user will wait, finishing it is wasted GPU time, so interactive
  paths need explicit rejection.

## Pitfalls
- Unbounded in-memory queues, which many frameworks give you by default; the symptom is p99 latency
  climbing for minutes with a flat error rate, then an out-of-memory crash with no 5xx warning before it.
- Watching queue length instead of queue time; the symptom is a "healthy" queue depth of ten while p99
  explodes, because those ten are long-document requests.
- Rejecting after expensive work (after retrieval, prompt assembly or, worst, after prefill); the symptom
  is a GPU at 100% while completed requests per second falls.
- Omitting `Retry-After`, or clients ignoring it; the symptom is a sawtooth of load spikes at a fixed
  interval after each shedding episode as every rejected client retries together.
- Wiring load shedding into the load balancer's health check; the symptom is a total outage during a
  partial overload, because every instance reports unhealthy at once and is removed. Keep liveness
  separate from load.

## When to use
Every boundary that fronts a GPU or a metered provider quota needs explicit backpressure: the gateway in
front of your API, the application in front of the engine, and the engine's own scheduler limits. Reach
for the simple semaphore-plus-wait-budget first, add token weighting when prompt lengths vary widely, and
add priority tiers when batch and interactive traffic share a deployment. Measure queue wait time,
rejection rate, in-flight tokens and KV-cache usage, and alert on wait time rather than on errors because
it moves first. In day-to-day work it shows up as the question "what happens when traffic doubles for
ten minutes"; in interviews, the expected answer walks from Little's law to bounded queues to
503-with-Retry-After, and distinguishes that from rate limiting, which protects against a single caller
rather than against aggregate load.

## Role lens
- **Engineer:** Implements the admission gate in the API layer, sizes `max_num_seqs` and queue limits on
  the engine, exports queue-wait histograms, and tests the path by load-testing past capacity.
- **Architect:** Decides where in the chain rejection happens, which traffic classes are shed first, and
  how the latency SLO translates into a queue wait budget and autoscaling triggers.
- **FDE:** Diagnoses a customer's "the model got slow at 2 pm" incident from queue depth and preemption
  metrics, and negotiates priority tiers between their batch pipeline and their chat product on shared GPUs.

## Check yourself
**Q:** Your vLLM-backed API serves 4 requests per second at 8 seconds mean latency. A campaign triples
traffic for ten minutes. Walk through what happens with no backpressure, then design the controls you
would add and the numbers you would set.

**A:** By Little's law steady state needs about 32 concurrent requests; at 12 requests per second it
needs 96, so with the engine capped near 32 the queue grows by roughly 8 requests every second. After a
minute some 480 requests are waiting and queue time is near a minute; clients time out at 30 s and
retry, raising the arrival rate further, while the GPU prefills prompts for abandoned connections.
Useful throughput falls below the original 4 per second: that is the collapse. With backpressure: keep
the engine limit near its true capacity, put a semaphore with a 1–2 s wait budget in front of it, return
503 with Retry-After for the rest, cancel generation on client disconnect, and shed batch traffic before
interactive. Roughly two thirds of requests get a fast rejection, but the third that is served sees
normal latency, and queue-wait time becomes the autoscaling trigger. A strong answer also mentions
token-aware admission if prompt lengths vary, and notes that per-tenant rate limiting is a separate
control for a different problem.

## Quiz
### Q: Why should an admission controller for LLM requests bound queue *time* rather than queue *length*?
- [ ] Queue length cannot be measured accurately in an asynchronous server
- [x] Service times vary by orders of magnitude with prompt and output length, so the same queue length can mean one second or two minutes of waiting
- [ ] HTTP clients can only communicate time-based limits, not length-based ones
- [ ] Queue time is cheaper to compute than queue length
> A queue of ten short chat turns drains in a second; ten 50k-token document requests take minutes.
> Length hides this, while measured wait time directly tracks what the caller experiences, which is why
> delay-based control is the right signal.

### Q: A server at aggregate capacity must reject a request from a caller who is well within their own quota. Which response is correct?
- [ ] 429 Too Many Requests with Retry-After
- [x] 503 Service Unavailable with Retry-After
- [ ] 408 Request Timeout
- [ ] 200 with an empty completion and a warning header
> 429 states that the caller exceeded their allowance, which is false here and would send them to the
> wrong fix. 503 says the server is the bottleneck, and Retry-After tells clients when to come back
> without a synchronized retry storm.

### Q: Which is the primary reason an unbounded queue in front of a GPU server is harmful rather than merely wasteful?
- [ ] It makes the engine's continuous batching less efficient
- [ ] Queued requests consume KV-cache memory before they are scheduled
- [x] The server keeps doing expensive work for requests whose clients have already timed out, while their retries add new load
- [ ] HTTP keep-alive connections are limited to a fixed number per process
> Once wait time exceeds client timeouts, every completed request is wasted and every timed-out client
> retries, so useful throughput falls as load rises. Waiting requests do not yet hold KV memory; the
> damage is the wasted GPU time and the feedback loop.
