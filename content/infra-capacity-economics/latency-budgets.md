---
id: latency-budgets
module: infra-capacity-economics
name: Latency budgets
kind: practice
difficulty: core
est_minutes: 15
status: typed
lesson_type: decision
agent_relevance: core
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
A latency budget splits one user-facing target, such as "first token within 2 s at p95" or "task done in
15 s", across every model call, tool call and hop plus a reserve. For agents it also caps loop steps.

## For agent builders
- **Why it matters for agents.** An agent's latency is a loop: plan, call a tool, read the result, repeat.
  The step count is decided at run time, so without a budget one extra tool call or retry silently doubles
  task time. The budget tells the loop when to stop exploring and answer.
- **Where it shows up in your stack.** Per-step timeouts on the model client and each tool, a step or
  recursion limit in the agent framework (LangGraph has a recursion limit per run; most frameworks have a
  max-iterations setting), deadline headers between services, and spans in LangSmith, Langfuse or
  OpenTelemetry traces.
- **What you control.** The task deadline, the maximum number of steps, which tools and steps are optional,
  whether tool calls run in parallel, prompt length (prefill), and when to stream a partial answer.
- **What you can skip.** Formal SLO maths. Know that percentiles do not add, that queue wait grows with
  load, and how to propagate a deadline; that covers most agent latency work.

## Decision case
### Scenario
An insurer's claims-status agent runs in a web chat. Product wants the first answer text on screen within
12 s at p95 at peak load. Compliance requires an output guardrail before any text is shown. All p95s
below are illustrative measurements at peak load.
- **Loop iteration:** main model picks a tool 1.8 s, the claims API answers in 0.9 s, so 2.7 s per
  iteration.
- **Final answer:** queue plus prefill to first token 2.0 s, then 25 ms per output token for about 300
  tokens. The guardrail check takes 0.4 s per check.
- **Reserve:** 1.5 s for jitter and one cheap retry.
- **Task traces:** 62% of tasks need at most 2 tool iterations, 90% at most 3, 97% at most 4.
- **Constraints:** no new GPUs this quarter, a team of three, and a 6-week deadline.

### Options
| Option | How it works | Time left for the loop | Iterations that fit | Tasks covered |
|---|---|---|---|---|
| A. Current: full-answer guardrail, `max_steps=4` | Decode all 300 tokens (7.5 s), check, then display | 12 − 1.5 − (2.0 + 7.5 + 0.4) = 0.6 s | 0, yet 4 run: 4 × 2.7 + 9.9 = 20.7 s | 97%, but every one breaks the target |
| B. Stream with a sentence guardrail, time-aware loop | Check each sentence before showing it; first sentence about 20 tokens: 2.0 + 0.5 + 0.4 = 2.9 s | 12 − 1.5 − 2.9 = 7.6 s | 7.6 ÷ 2.7 = 2.8, so 2 | 62% |
| C. B plus a small model for tool-selection steps | Router picks tools in 0.8 s, so 0.8 + 0.9 = 1.7 s per iteration; main model writes the answer | 7.6 s | 7.6 ÷ 1.7 = 4.5, so 4 | 97% |

### Decision
Choose **C**, gated on an eval.
- **Latency:** the full-answer guardrail alone spends 9.9 s of the 12 s, so A cannot meet the target at any
  step count. Moving the guardrail to sentence level is the change that makes a loop affordable at all.
- **Task success:** B fits only 2 iterations, so 38% of tasks would end on the partial-answer path.
  C fits 4 and covers the 97% the traces show.
- **Constraints:** C needs no GPUs (the small model serves on the existing pool) and is two changes the
  team can ship in 6 weeks: a guardrail mode and a model route for tool steps.
- **Gate:** C ships only if the router's tool-selection accuracy on the agent's eval set is within an
  agreed margin of the main model's, because faster wrong tool calls waste iterations.

**What would change it:** a failing router eval leaves B plus a request to product for a 15 s target
(15 − 1.5 − 2.9 = 10.6 s, 3 iterations, 90% of tasks). A compliance ruling that sentence checks are not
enough moves the agent to a background task with status updates, where minutes are acceptable.

### Trade-offs accepted
- **Sentence-level guardrail.** A violation spread across sentences is caught later than with a full check.
  Log the guardrail verdicts and sample full transcripts for review.
- **Two models in one loop.** Routing quality can drift on model updates. Track tool-call error rate and
  steps per task per model version.
- **Partial answers for 3% of tasks.** Monitor the partial-answer share; a rise means iterations got
  slower or tasks got harder.
- **Load-bound numbers.** All p95s hold only up to the utilization they were measured at; re-measure when
  peak occupancy rises.

### Review rubric
- States the target with a percentile, a load and the user-visible event it measures.
- Lays out the critical path with the fixed parts (reserve, final answer, guardrail) subtracted first.
- Derives the step limit by dividing the remaining time by the per-iteration p95, and ties it to the
  traced distribution of steps per task.
- Identifies the single largest line item and attacks it before tuning smaller ones.
- Gates any quality-risking speedup on a named eval, and names the fallback if it fails.

## Why it exists
- **An AI request is a pipeline, not a call.** A RAG answer may pass an edge proxy, auth, input
  moderation, a query rewrite with a small model, embedding, vector search, a reranker, prompt assembly,
  a model queue, prefill, decode, an output guardrail and a logging hop. An agent repeats part of that
  chain once per step.
- **Each step was "fast enough" alone.** Added together, and at the tail rather than the median, they blow
  through any product target.
- **One end-to-end timeout gives nobody a number.** A 400 ms reranker gets added without anyone noticing
  it consumed a fifth of the budget, and nobody knows how many agent steps the task can afford.
- **One outer timeout fires too late.** The request has already burned GPU time, and every downstream step
  kept working after the user gave up.
- **Percentiles do not add.** Ten parallel calls each 1% likely to be slow make the request slow about 10%
  of the time (1 − 0.99¹⁰ ≈ 9.6%); 100 fan-out calls make it about 63%. So budgets are set on
  percentiles and verified end to end, never assembled from component medians.

## How it works
### 1. Start from the experience, not the system
| Product | Metric the user feels |
|---|---|
| Streamed chat | Time-to-first-token (TTFT) plus a readable token rate |
| Voice agent | Time to first audio |
| JSON API | Total completion time |
| Background agent | Possibly minutes per task |

- **Fix the percentile (p95 or p99) and the load** at which it must hold. A target with no percentile or
  no load is not a budget.
- **Streamed output needs two numbers.** A TTFT budget for the wait before anything appears, and a
  time-per-output-token (inter-token latency) budget for the stream, because a fast first token followed
  by a stuttering stream still feels broken.
- **They use different levers:** prompt length and queueing for TTFT; batch size and model size for
  per-token time.
- **Interactive agents need a progress signal too.** Streaming "searching orders..." status events keeps a
  15 s task feeling alive; the budget still applies to the final answer.

### 2. Decompose the critical path
- **Mark sequential versus parallel steps.** Only the critical path consumes budget; parallel branches
  cost the slowest branch, not the sum.
- **The biggest wins hide here.** Running moderation in parallel with retrieval instead of before it
  removes its cost from the critical path entirely, at the price of occasionally doing retrieval work for
  a request you then block.
- **In an agent loop, each iteration is one critical-path unit:** model call (queue, prefill, decode of the
  tool call) plus tool execution. Parallel tool calls in one turn cost the slowest tool, not the sum.

### 3. Allocate, with a reserve
1. **Give each step a number** from measured p95 (or p99) under realistic load.
2. **Hold back a reserve of 10–20%** (an illustrative rule of thumb) for network jitter, retries and
   growth.
3. **Split the model step** into queue wait, prefill (grows with prompt length) and decode (time per
   output token × tokens).
4. **For agents, divide what is left by the per-iteration p95** to get the step limit, after setting aside
   the final answer step.
- **Prompt length is a latency decision as well as a cost one:** adding 20 retrieved chunks lengthens
  prefill and TTFT, and an agent transcript lengthens prefill on every later step unless prefix caching
  absorbs it.
- **Queue wait is the most load-sensitive line**, because it grows as ρ/(1−ρ) with utilization (see
  queueing theory). An allocation measured at 50% load is valid only up to the utilization you tested,
  so state that utilization alongside the budget.

### 4. Enforce with deadlines, not just timeouts
- **A deadline is an absolute time** by which the response must be produced, created at the edge and
  propagated with the request. gRPC deadlines do this natively; over HTTP, pass a header with the
  remaining time.
- **Each step's timeout** is the smaller of its own allocation and what is left minus what later steps
  need.
- **Why:** a step that started late should get less time, not the same time, and a downstream service
  (a tool, a sub-agent) should stop working once the caller has given up.

### 5. Degrade instead of failing
- **Classify steps as required or optional.** When the remaining budget is short, skip optional ones
  (query rewrite, reranking, a second retrieval hop, one more verification step) rather than squeezing
  them into a timeout they will miss.
- **Answer with what you have.** When the loop runs out of budget, force a final model call that answers
  from the evidence gathered so far, flagged as partial, instead of returning a timeout.
- **Retry only when the remaining budget exceeds the attempt's p95.** Otherwise the retry has a large
  chance of timing out and adds load exactly when the system is struggling.
- **Hedged requests** (send a second copy after the p95 delay, take the first response) cut tails for
  idempotent reads at a modest extra cost.

### 6. Verify end to end
- **Distributed tracing:** each span reports its duration against its allocation; an agent trace shows
  one span per step, so you can see which step blew the budget.
- **Alert on the end-to-end percentile**, not on component averages.
- **Review budgets like any interface contract**, because they drift as features land.

## Diagram
The edge sets one task deadline; each agent iteration checks the time left before running, and when an
iteration no longer fits, the loop jumps to the final answer.

```mermaid
flowchart TD
    E["Edge: task deadline = now + 15 s"] --> L{"Time left for step plus answer?"}
    L -->|"yes"| M["Model call: pick tool"]
    M --> T["Tool call with its own timeout"]
    T --> D{"Model says done?"}
    D -->|"no"| L
    D -->|"yes"| A["Final answer step"]
    L -->|"no"| P["Answer from evidence so far"]
    P --> A
    A --> U["Stream answer to user"]
    T -.->|"span vs allocation"| O["Tracing and p95 alert"]
```

## Examples
### Worked allocation: RAG assistant, p95 TTFT ≤ 2.0 s at peak load
Illustrative allocation from measured p95s:

| Step | Allocation | Notes |
|---|---|---|
| Edge and TLS | 100 ms | |
| Gateway and auth | 30 ms | |
| Input moderation | 120 ms | can move parallel to retrieval |
| Query rewrite | 300 ms | optional |
| Embedding | 50 ms | |
| Vector search | 80 ms | |
| Rerank | 200 ms | optional |
| Prompt assembly | 20 ms | |
| Model queue plus prefill | 800 ms | grows with load and prompt length |
| Reserve | 300 ms | jitter, retries, growth |
| **Sum** | **2.0 s** | |

- **Parallelize moderation** with retrieval and 120 ms is freed.
- **Decode is separate.** With 400 output tokens at 25 ms per token the full answer takes another 10 s:
  acceptable streamed, not if an output guardrail must see the whole answer before display.

### Worked scenario: how many steps a support agent can afford
Target: task answered within 15 s at p95. Measured p95s at peak load (illustrative):

| Item | p95 | Budget left |
|---|---|---|
| Task deadline | | 15.0 s |
| Reserve (about 13%) | 2.0 s | 13.0 s |
| Final answer step (prefill plus streamed TTFT, rest streams after) | 3.0 s | 10.0 s |
| One loop iteration: model picks tool 1.8 s + tool 0.7 s | 2.5 s | |
| **Step limit** | 10.0 ÷ 2.5 | **4 iterations** |

- **Parallel tools stretch it.** If the model issues two lookups in one turn and runs them together, one
  iteration still costs about 2.5 s but gathers twice the evidence.
- **Sum of p95s is pessimistic** for independent steps (see the simulation below), so this limit is safe;
  verify the real task p95 with traces.

### Deadline-aware agent loop
```python
import asyncio, time

class Deadline:
    def __init__(self, seconds): self.end = time.monotonic() + seconds
    def remaining(self): return self.end - time.monotonic()

STEP_P95, ANSWER_P95 = 2.5, 3.0       # measured per-iteration and final-step p95 at peak

async def run_agent(task, remaining_s, call_model, run_tool, max_steps=8):
    dl = Deadline(remaining_s)         # from the propagated deadline, not a fresh 15 s
    transcript, partial = [task], False
    for _ in range(max_steps):
        if dl.remaining() < STEP_P95 + ANSWER_P95:
            partial = True             # no room for another step: answer with what we have
            break
        try:
            step = await asyncio.wait_for(call_model(transcript), dl.remaining() - ANSWER_P95)
        except asyncio.TimeoutError:
            partial = True             # slow model step: keep the answer budget, stop exploring
            break
        if step.get("final"):
            return step["text"], False
        try:                           # each tool gets at most what the loop can spare
            out = await asyncio.wait_for(run_tool(step["tool"], step["args"]),
                                         min(step.get("timeout", 5.0), dl.remaining() - ANSWER_P95))
        except asyncio.TimeoutError:
            out = {"error": "tool timed out"}      # tell the model, let it choose another path
        transcript += [step, out]
    final = await asyncio.wait_for(call_model(transcript + ["Answer now from the evidence above."]),
                                   max(0.5, dl.remaining()))
    return final["text"], partial
```

- **`remaining_s` comes from the deadline set at the edge**, so upstream delay is already subtracted.
- **The stop rule** is "time left covers one more step plus the answer", not a fixed step count, so a slow
  gateway or slow tool automatically leaves fewer iterations.
- **A timed-out tool becomes an observation** the model can route around, instead of an exception that
  kills the task; a timed-out model step ends the loop but still leaves the answer step its budget.

### Deadline-aware stages in a RAG step
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

async def first_token(query, remaining_s, rewrite, retrieve, rerank, generate):
    dl = Deadline(remaining_s)              # from the propagated deadline header, not a fresh 2.0 s
    q = await stage(lambda: rewrite(query), 0.30, dl, keep=0.15 + PREFILL_P95,
                    optional=True, fallback=query)
    docs = await stage(lambda: retrieve(q), 0.15, dl, keep=PREFILL_P95)
    docs = await stage(lambda: rerank(q, docs), 0.20, dl, keep=PREFILL_P95,
                       optional=True, fallback=docs[:5])
    return await stage(lambda: generate(q, docs), dl.remaining(), dl)
```

- **Normal request:** about 1.87 s is left (2.0 s minus 130 ms of edge and gateway) and the rewrite runs.
- **Slow gateway:** if the queue ate an extra 700 ms, only 1.17 s is left. The rewrite needs its 0.30 s
  plus the 0.95 s kept for retrieval and generation, so it is skipped automatically, and generation's
  0.8 s share stays protected.
- **The bug this prevents:** creating `Deadline(2.0)` inside this function would silently ignore upstream
  delay, which is exactly what deadline propagation exists to stop.

### Percentiles do not add: a quick simulation
Pure Python; three sequential steps with skewed (lognormal) latencies.

```python
import random

random.seed(7)
def p(xs, q):
    xs = sorted(xs)
    return xs[int(q * (len(xs) - 1))]

N = 100_000
stt = [random.lognormvariate(-1.4, 0.4) for _ in range(N)]
ret = [random.lognormvariate(-1.7, 0.5) for _ in range(N)]
llm = [random.lognormvariate(-0.8, 0.4) for _ in range(N)]
total = [a + b + c for a, b, c in zip(stt, ret, llm)]

print(f"sum of step p95s: {sum(p(x, 0.95) for x in (stt, ret, llm)):.2f} s")   # ~1.76 s
print(f"p95 of the sum:   {p(total, 0.95):.2f} s")                               # ~1.43 s
for n in (1, 10, 50, 100):          # fan-out: chance at least one call hits its 1% tail
    print(n, f"{1 - 0.99 ** n:.0%}")                                             # 1%, 10%, 39%, 63%
```

- **Sequential, independent steps:** the sum of p95s overstates the end-to-end p95, so it is a safe but
  pessimistic budget.
- **Fan-out:** waiting on the slowest branch makes tails far more common than any one branch's tail; an
  agent that fans out to many sub-agents or tools pays this.
- **Either way, measure the end-to-end percentile** rather than deriving it from components.

## Production patterns
- **Propagate deadlines on every hop.** When a request crosses services, set the deadline once at the
  edge and pass it on: gRPC carries it natively; over HTTP use a header holding remaining milliseconds,
  and have each service, tool server or sub-agent subtract its elapsed time. Monitor requests arriving
  with negative or near-zero remaining budget.
- **Time-aware step limits.** Keep the framework's hard step or recursion limit as a safety net, and add a
  budget check before each iteration that forces the final answer when time is short. Monitor steps per
  task, the share of tasks ending on the partial-answer path, and task p95.
- **Cancel abandoned work.** When the client disconnects or the deadline passes, cancel downstream calls
  and stop decoding; in Starlette or FastAPI check `await request.is_disconnected()` while streaming.
  Monitor tokens generated for requests whose client already left.
- **Run independent stages and tools concurrently.** Start moderation and retrieval together with
  `asyncio.gather`, and execute parallel tool calls from one model turn together. Block on the moderation
  verdict before generation. Monitor how often retrieval work is wasted on blocked requests.
- **Budget spans, and re-budget on every change.** Tag each span (model step, tool call) with its
  allocation as an attribute, alert on end-to-end p95 and p99 rather than component averages, and treat the
  budget table as a reviewed artifact: a new reranker, guardrail, tool or more retrieved chunks means new
  numbers. Monitor the degraded-path share, TTFT and task p95 after each "quality" change.
- **Hedge only idempotent reads, with a cap.** Send a second copy after the p95 delay for retrieval, cache
  or read-only tool calls, never for tools with side effects, and limit hedges to a small fraction of
  traffic. Monitor extra load from hedging and turn it off under saturation.

## Trade-offs
- **Tight per-step allocations vs churn.** They make regressions visible early, but over-precise budgets
  become a burden when the pipeline changes weekly and owners spend time renegotiating numbers.
- **Skipping optional steps vs quality.** It protects latency at the cost of answer quality: right for
  chat, wrong for workflows where a wrong answer costs more than a slow one.
- **Fewer agent steps vs task success.** A tight step limit keeps p95 low but cuts off tasks that needed one
  more lookup; measure success rate against the step limit before tightening it.
- **Hedged requests vs load.** They cut tail latency but add load, roughly the fraction of requests that
  exceed the hedge delay. They backfire on saturated systems or non-idempotent calls.
- **Streaming vs guardrails.** Streaming hides decode time behind reading time, but forces output
  guardrails to work on partial text or hold tokens back, which reintroduces latency.

## Pitfalls
- **Adding component p95s or medians and calling it the end-to-end p95.** Symptom: every service is green
  while users report slowness. Fix: measure the end-to-end percentile with tracing.
- **A fixed step limit with no time check.** Symptom: most tasks are fast but a slow tool makes some run
  four times the target, still within the step limit. Fix: check remaining time before every iteration.
- **Retrying inside a budget that cannot afford another attempt.** Symptom: retry counts rising together
  with timeouts, and more GPU work per successful request. Fix: retry only when time left exceeds the
  attempt's p95.
- **Budgeting the model step from a single-request benchmark.** Under load, queue wait and prefill grow,
  seen as TTFT p95 degrading at peak hours while prompt sizes are unchanged. Fix: measure at peak load.
- **Enforcing the deadline only at the edge.** The user sees a timeout while the model keeps decoding and
  tools keep running, visible as GPU or token spend on requests whose clients already left. Fix: propagate
  and cancel.
- **Letting retrieval chunk counts creep up without re-budgeting prefill.** Symptom: TTFT rising after a
  "quality" change that never touched serving. Fix: re-budget prefill with each change.

## When to use
- **Use when** a user-facing AI path has more than two hops, when an agent loop has an open-ended step
  count, and before adding a new step such as a reranker, guardrail or tool.
- **Measure** p95 and p99 per span under peak load, the end-to-end percentile, steps per task, and the
  share of requests that took a degraded or partial-answer path.
- **Avoid formal budgets** for offline batch jobs and background agents, where throughput and cost per task
  matter more than per-request latency; keep only a generous hard timeout.
- **Interviews:** decompose the target into steps, point out that percentiles do not add, and propose
  deadline propagation with optional-step degradation; that is the expected answer to "make this feel
  fast".

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

## Interview questions
### What is a latency budget, and why can't you build one by adding component medians?
- **Definition:** one user-facing target at a stated percentile and load, divided among every step on the
  critical path plus a reserve.
- **Medians hide the tail:** users feel p95 and p99, and tails compound across a chain or fan-out.
- **So** allocate from measured p95s under load and verify the end-to-end percentile with tracing.

### Design a budget for a RAG chat feature that must show the first token within 2 s at p95.
- **Choose the metric:** TTFT at p95 at peak load, plus a separate per-token budget for the stream.
- **Shape the path:** run moderation parallel to retrieval, mark rewrite and rerank optional, and keep
  about 10–20% as reserve.
- **Protect the model step:** allocate queue plus prefill (for example 800 ms) and cap retrieved chunks so
  prefill stays inside it.
- **Enforce:** an edge deadline propagated to every hop, with optional stages skipped when time is short.

### A tool-calling agent must finish within 15 s at p95. How do you keep it inside that?
- **Set aside the fixed parts:** a reserve and the final answer step, then divide the rest by the measured
  per-iteration p95 (model call plus tool) to get the affordable step count.
- **Check time, not just steps:** before each iteration, continue only if time left covers one more step
  plus the answer; otherwise answer from the evidence so far and mark it partial.
- **Shorten iterations:** parallel tool calls, per-tool timeouts, prefix caching for the growing
  transcript, and a smaller model for routing steps.

### TTFT p95 degrades every day at peak, but prompt sizes have not changed. Where do you look?
- **Queue wait first:** it grows as ρ/(1−ρ), so a budget measured at 50% load breaks at peak utilization.
- **Split the span:** compare queue wait with prefill time; if queue wait dominates, add capacity or
  shed load rather than optimizing the prompt.
- **Check retries and hedges:** they raise load exactly at peak.

### When would you not use hedged requests?
- **Non-idempotent calls:** a duplicated tool call or write can cause side effects twice, such as an agent
  issuing two refunds.
- **Saturated systems:** hedging adds roughly the fraction of requests that exceed the hedge delay, which
  deepens the queue that caused the tail.
- **Expensive model calls:** a second GPU-heavy request doubles cost for that tail; prefer hedging cheap
  reads such as retrieval or caches.

### The user closes the tab mid-answer while the agent is still working. What should happen?
- **Cancel everything downstream:** stop decoding and abort pending tool, retrieval and sub-agent calls,
  because the deadline is gone and the work is wasted.
- **Implement:** detect disconnect in the streaming handler, cancel the task, and propagate cancellation
  to the model client and tool servers; for durable background agents, decide explicitly whether the run
  continues.
- **Verify:** track tokens or GPU time spent on requests whose client already left; it should fall
  toward zero.

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
> A 300 ms timeout on a 700 ms p95 call fails a large share of the time while still consuming GPU time. Degrading
> or failing fast keeps the SLO honest and avoids wasted work.

## Use cases & projects
### Use cases
- **Support and booking agents with a task deadline.** The budget sets how many tool-calling steps fit
  before the agent must answer with what it has.
- **RAG chat with a first-token target.** Splitting the TTFT target across retrieval, reranking, prompt
  assembly and prefill shows which stage to cut when the p95 slips.
- **Multi-service agent platforms.** Propagated deadlines stop downstream tools and model calls from
  working after the caller has given up.
- **Choosing degraded paths.** A budget decides when to skip the reranker, use a smaller model or stream a
  partial answer instead of timing out.

### Mini project
**Build a deadline-aware agent loop (1-3 hours).**
- **Goal:** keep a tool-calling agent inside a fixed task deadline by checking the remaining budget before
  every step.
- **Steps:** build a small agent with three fake tools that sleep for random durations; pass an absolute
  deadline into the loop; before each model or tool call, compare the remaining time with that step's p95
  and take a degraded path (skip an optional tool or answer now) when it does not fit; log per-step spans.
- **Done when:** over 100 runs the task-level p95 stays under the deadline, you have a per-step latency
  table, and you can show the runs where the loop chose to stop early and why.
