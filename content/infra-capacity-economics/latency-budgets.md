---
id: latency-budgets
module: infra-capacity-economics
name: Latency budgets
kind: practice
difficulty: core
est_minutes: 15
related:
  - reliability/slas-slos
  - observability/latency-percentiles
  - serving-inference/ttft
  - serving-inference/timeouts
  - serving-inference/streaming
  - infra-capacity-economics/queueing-theory
  - infra-capacity-economics/quality-latency-cost-optimization
  - observability/distributed-tracing-across-services
sources:
  - Dean & Barroso, 2013 — The Tail at Scale
  - Beyer et al., 2016 — Site Reliability Engineering
---

## TL;DR
A latency budget takes one user-facing target, such as "first token within 2 s at p95", and divides
it among every hop on the request path: network, guardrails, retrieval, reranking, model queue and
prefill, tools, plus a reserve. It exists because AI pipelines are long chains of slow, variable steps,
and without an explicit allocation each team spends the whole budget independently. The budget then
drives timeouts, deadline propagation and which steps to skip under pressure.

## Why it exists
A modern AI request is a pipeline, not a call. A RAG answer may pass through an edge proxy, an auth
check, an input moderation classifier, a query rewrite with a small model, an embedding call, a vector
search, a reranker, prompt assembly, a queue in front of the model, prefill, decode, an output
guardrail and a logging hop. Each was built by someone who measured it alone and found it "fast
enough". Added together, and at the tail rather than the median, they blow through any product target.

The naive approach is to set a single end-to-end timeout and hope. That fails in three ways. First, it
gives no one a number to design against, so a 400 ms reranker gets added without anyone noticing it
consumed a fifth of the budget. Second, a single outer timeout fires too late: the request has already
burned GPU time, and every downstream step kept working after the user gave up. Third, percentiles do
not add. If ten parallel calls each have a 1% chance of being slow, the request is slow about 10% of the
time (1 − 0.99¹⁰ ≈ 9.6%); with 100 fan-out calls it is about 63%. Tail behaviour in a chain or fan-out
is worse than any single component's tail, so budgets must be set on percentiles and verified end to
end, not assembled from component medians.

## How it works
### 1. Start from the experience, not the system
Pick the metric the user actually feels. For streamed chat that is time-to-first-token (TTFT) plus a
readable token rate; for a voice agent it is time to first audio; for an API that returns JSON it is
total completion time; for a background agent it may be minutes per task. Fix the percentile (p95 or
p99) and the load at which it must hold. A target with no percentile or no load is not a budget.

### 2. Decompose the critical path
Draw the path and mark which steps are sequential and which run in parallel. Only the critical path
consumes budget; parallel branches cost the slowest branch, not the sum. This is where the biggest wins
usually hide: running moderation in parallel with retrieval instead of before it removes its cost from
the critical path entirely, at the price of occasionally doing retrieval work for a request you then block.

### 3. Allocate, with a reserve
Give each step a number derived from measured p95 (or p99) under realistic load, and hold back a
reserve of 10–20% (an illustrative rule of thumb) for network jitter, retries and growth. For the model
step, split the budget into queue wait, prefill (grows with prompt length) and decode (time per output
token × tokens). Prompt length is therefore a latency decision as well as a cost one: adding 20 retrieved
chunks lengthens prefill and TTFT.

### 4. Enforce with deadlines, not just timeouts
Turn the budget into a **deadline**: an absolute time by which the response must be produced, created
at the edge and propagated with the request (gRPC deadlines do this natively; over HTTP, pass a header
with the remaining time). Each step sets its timeout to the smaller of its own allocation and what is
left minus what later steps need. This matters *because* a step that started late should get less time,
not the same time, and a downstream service should stop working once the caller has given up.

### 5. Degrade instead of failing
Classify steps as required or optional. When the remaining budget is short, skip the optional ones
(query rewrite, reranking, a second retrieval hop) rather than squeezing them into a timeout they will
miss. Retry only when the remaining budget exceeds the attempt's p95; otherwise a retry is guaranteed to
time out and only adds load. Hedged requests (send a second copy after the p95 delay and take the first
response) cut tails for idempotent reads at a modest extra cost.

### 6. Verify end to end
Use distributed tracing so each span reports its duration against its allocation, and alert on the
end-to-end percentile, not on component averages. Budgets drift as features land; review them like any
other interface contract.

## Example
An interactive RAG assistant with a target of p95 TTFT ≤ 2.0 s at peak load. Allocation (illustrative,
from measured p95s): edge and TLS 100 ms, gateway and auth 30 ms, input moderation 120 ms, query rewrite
300 ms (optional), embedding 50 ms, vector search 80 ms, rerank 200 ms (optional), prompt assembly 20 ms,
model queue plus prefill 800 ms, reserve 300 ms. Sum: 2.0 s. Moving moderation in parallel with
retrieval frees 120 ms. With 400 output tokens at 25 ms per token, the full answer takes another 10 s,
which is acceptable streamed but not if an output guardrail must see the whole answer before display.

```python
import asyncio, time

class Deadline:
    def __init__(self, seconds): self.end = time.monotonic() + seconds
    def remaining(self): return self.end - time.monotonic()

async def stage(fn, budget, dl, keep=0.0, optional=False, fallback=None):
    avail = dl.remaining() - keep           # leave room for the stages after this one
    if optional and avail < budget:
        return fallback                     # skip an optional step rather than squeeze it
    try:
        return await asyncio.wait_for(fn(), max(0.0, min(budget, avail)))
    except asyncio.TimeoutError:
        if optional:
            return fallback
        raise

PREFILL_P95 = 0.8                           # measured queue + prefill at peak

async def first_token(query, rewrite, retrieve, rerank, generate):
    dl = Deadline(2.0 - 0.13)               # SLO minus edge + gateway already spent
    q = await stage(lambda: rewrite(query), 0.30, dl, keep=0.15 + PREFILL_P95,
                    optional=True, fallback=query)
    docs = await stage(lambda: retrieve(q), 0.15, dl, keep=PREFILL_P95)
    docs = await stage(lambda: rerank(q, docs), 0.20, dl, keep=PREFILL_P95,
                       optional=True, fallback=docs[:5])
    return await stage(lambda: generate(q, docs), dl.remaining(), dl)
```

If the gateway queue ate 400 ms, the rewrite is skipped automatically and retrieval still runs with
generation's share protected.

## Trade-offs
- Tight per-step allocations make regressions visible early, but over-precise budgets create churn;
  they flip to a burden when the pipeline changes weekly and owners spend time renegotiating numbers.
- Skipping optional steps protects latency at the cost of answer quality; this is right for chat and
  wrong for workflows where a wrong answer costs more than a slow one.
- Hedged requests cut tail latency but add load, roughly the fraction of requests that exceed the
  hedge delay; they backfire on saturated systems or non-idempotent calls.
- Streaming hides decode time behind reading time, but forces output guardrails to work on partial
  text or to hold tokens back, which reintroduces latency.

## Pitfalls
- Adding component p95s or medians and calling it the end-to-end p95; the symptom is a dashboard where
  every service is green while users report slowness.
- Retrying inside a budget that cannot afford another attempt; visible as retry counts rising together
  with timeouts, and more GPU work per successful request.
- Budgeting the model step from a single-request benchmark; under load, queue wait and prefill grow,
  seen as TTFT p95 degrading at peak hours while prompt sizes are unchanged.
- Letting retrieval chunk counts creep upward without re-budgeting prefill; symptom is TTFT rising after
  a "quality" change that never touched serving.

## When to use
Write a latency budget for any user-facing AI path with more than two hops, and before adding a new
step such as a reranker, guardrail or tool call. Measure p95 and p99 per span under peak load, the
end-to-end percentile, and the share of requests that took a degraded path. Skip formal budgets for
offline batch jobs, where throughput and cost per task matter more than per-request latency. In
interviews, decomposing a target into steps, pointing out that percentiles do not add, and proposing
deadline propagation with optional-step degradation is the expected answer to "make this feel fast".

## Role lens
- **Engineer:** Implements deadline propagation, per-stage timeouts and degradation paths, and adds
  tracing spans so each step's latency is compared against its allocation.
- **Architect:** Owns the end-to-end target and its split, decides what runs in parallel, which steps
  are optional, and when a slow component must be redesigned rather than tuned.
- **FDE:** Accounts for the customer's network, proxies and on-prem retrieval systems, which often
  consume budget the vendor's demo never had to pay.

## Check yourself
**Q:** Your voice agent must start speaking within 1 s at p95. Measured p95s: speech-to-text 300 ms,
retrieval 250 ms, LLM TTFT 600 ms, text-to-speech first audio 200 ms. What do you change?
**A:** The sequential sum of p95s is 1.35 s, and the true p95 of the sum is usually somewhat lower than that
sum for independent steps, but still clearly over budget. Options: stream speech-to-text and start retrieval on partial
transcripts or run it in parallel with endpointing; cut prompt length or use a smaller or
prefix-cached model to reduce TTFT; stream LLM tokens into TTS sentence by sentence so first audio
depends on the first clause, not the full answer; and speak a short acknowledgement while retrieval
completes. Then verify the end-to-end p95 with tracing under load, keeping a reserve for network
jitter.

## Quiz
### Q: A request fans out to 50 parallel services, each with a 1% chance of exceeding its p99. Roughly how often does the request wait on at least one slow call?
- [ ] About 1%, since each service meets its p99
- [ ] About 50%, one percent per service added linearly
- [x] About 40%, because 1 − 0.99⁵⁰ ≈ 0.39
- [ ] Never, because parallel calls hide each other's latency
> The request waits for its slowest branch. The chance that all 50 are fast is 0.99⁵⁰ ≈ 0.61, so
> about 39% of requests hit at least one tail. Linear addition overestimates; ignoring fan-out
> underestimates.

### Q: Why propagate an absolute deadline instead of giving every stage a fixed timeout?
- [ ] Fixed timeouts are not supported by most HTTP clients
- [x] A stage that starts late gets only the time actually left, and downstream services stop working once the caller has given up
- [ ] Deadlines make each stage run faster by raising its priority
- [ ] Deadlines remove the need to measure per-stage latency
> Fixed timeouts let each stage spend its full allocation even after earlier stages overran, so the
> total exceeds the target and abandoned work keeps consuming capacity.

### Q: The remaining budget is 300 ms and the model call's p95 is 700 ms. What should the pipeline do?
- [ ] Retry the previous slow stage once to recover time
- [ ] Call the model anyway with a 300 ms timeout
- [x] Take the degraded path: skip or shortcut steps, fall back, or fail fast with a clear response
- [ ] Extend the deadline because the model step is required
> A 300 ms timeout on a 700 ms p95 call almost always fails while still consuming GPU time. Degrading
> or failing fast keeps the SLO honest and avoids wasted work.
