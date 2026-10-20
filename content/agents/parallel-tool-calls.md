---
id: parallel-tool-calls
module: agents
name: Parallel tool calls
kind: pattern
difficulty: core
est_minutes: 12
status: fact-checked
related:
  - agents/tool-calling
  - agents/agent-loop
  - agents/typed-tool-contracts
  - python-for-ai/asyncio
  - python-for-ai/async-cancellation-and-exception-propagation
  - agent-reliability/idempotent-tool-execution
  - backend-fundamentals/pagination-rate-limits-api-quotas
  - serving-inference/throughput-vs-latency
sources:
  - Official docs: Anthropic Claude API tool use
  - Official docs: Python asyncio
---

## TL;DR
A model can emit several tool calls in a single turn. The harness runs the independent ones
concurrently and answers every call before the next model turn (on the Claude API, all results in one
user message), which removes a full model
round trip per call and cuts latency for independent reads. The model decides what to batch, but only
the harness knows which tools are safe to run at the same time, so it must schedule, cap concurrency,
isolate failures and keep the result message intact.

## Why it exists
The latency of an agent step is a model call plus a tool call, and the model call is usually the
slower part, often seconds. An agent that needs three documents, or the status of four orders, or the
weather in two cities, pays that model latency three or four times if it fetches sequentially, and
grows the transcript by an assistant turn and a user turn each time. Nothing in those fetches depends
on the others; the model is only in the loop between them because the loop is sequential.

The naive fixes both fall short. Telling the model to call one tool at a time is simple and keeps
ordering trivial, but it is the slow path above. Adding a special batch tool that accepts a list of
ids works for one tool at a time but cannot express "look up this order *and* that customer *and* fetch
this page", and it moves the batching logic into every tool. Parallel tool calls generalize the idea:
by emitting several `tool_use` blocks in one response the model says "these do not depend on each
other", and the harness, which knows what each tool does, decides how to run them.

## How it works
### The model's side
Models trained for tool use emit multiple `tool_use` blocks in one turn when the calls are independent;
on the major APIs this is on by default, and it can be switched off: `disable_parallel_tool_use: true`
inside `tool_choice` on the Claude API, `parallel_tool_calls: false` on OpenAI's. What the model cannot express in a single turn is a dependency: "use the
customer id from call A as the argument of call B". For that it emits A, waits for the result, then
emits B, which is correct and sequential. If the chain is long, programmatic tool calling (the model
writes a script that calls tools from inside a sandbox) is the alternative that keeps intermediate
results out of the transcript (Anthropic offers this as "programmatic tool calling"; code-as-action
agent frameworks implement the same idea).

### The harness's side
1. Collect every `tool_use` block from the response, not just the first.
2. Classify each by the tool's contract metadata: read-only calls are parallel-safe; side-effecting
   calls run one at a time, in the order the model emitted them, or go through an approval gate.
   Idempotency does not make a write parallel-safe: "set status to shipped" and "set status to
   cancelled" are each idempotent, yet run concurrently they race on which lands last.
3. Run the safe set concurrently (`asyncio.gather`, a thread pool, a task group) under a concurrency
   cap, because a model that emits ten calls to the same API is a burst your rate limits did not plan for.
4. Isolate failures. Each call gets its own result, and one exception must not cancel the others,
   because the model needs every result to reason, and a missing `tool_result` for any `tool_use` is a
   protocol error.
5. Apply a per-call timeout and convert a timeout into an `is_error` result, so one slow upstream does
   not hold the whole turn.
6. Build a single user message with one `tool_result` per `tool_use`, bound by id. Order is cosmetic;
   completeness is mandatory.

### Why all results go back together
Every API expects each tool call in an assistant turn to be answered before the next assistant turn.
The wire shape differs: the Claude API wants all `tool_result` blocks in the single user message that
follows, and rejects a request that leaves a `tool_use` unanswered there; OpenAI's Chat Completions
uses one `role: "tool"` message per call, all placed directly after the assistant message. There is a
subtler reason as well: the transcript is also the model's example of how this conversation works. A
history in which calls appear to have been answered one at a time teaches the model, in context, to
make them one at a time, and the speedup quietly disappears.

### Side effects and ordering
Two writes emitted together have no guaranteed ordering once you run them concurrently. If tool A
creates a record and tool B updates it, the model should not have batched them, but it sometimes will;
the harness's `parallel_safe` flag is what prevents a race. Idempotency keys on write tools make a
retried or duplicated call harmless, which is the other half of running anything concurrently, but they
protect against duplicates, not against two different writes landing in the wrong order.

### The latency arithmetic
Illustrative numbers: a model call of 3 s and tool calls of 1 s each. Four independent reads done
sequentially cost four model calls each followed by one tool call, then the final model call:
4 × (3 + 1) + 3 = 19 s. Batched, they cost one model call, the slowest of the four tool calls, and the
final model call: 3 + 1 + 3 = 7 s. The transcript also gains one assistant
turn instead of four, which compounds across the rest of the run.

## Example
A harness function for one turn: safe calls run concurrently with a semaphore and a timeout, unsafe
calls run in order, every call gets exactly one result, and all results go back in one message.
`HANDLERS` maps tool names to async functions; `PARALLEL_SAFE` comes from the tool contracts.

```python
import asyncio, json

PARALLEL_SAFE = {"get_order", "search_docs", "get_weather"}   # read-only tools, from the contracts
sem = asyncio.Semaphore(5)                                      # protect upstream rate limits
TIMEOUT_S = 10

async def run_one(block) -> dict:
    """Execute one tool_use block and always return a tool_result for it."""
    try:
        async with sem:
            out = await asyncio.wait_for(HANDLERS[block.name](**block.input), timeout=TIMEOUT_S)
        return {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(out)}
    except asyncio.TimeoutError:
        return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                "content": f"timed out after {TIMEOUT_S}s; retry once or narrow the request"}
    except Exception as e:                                      # isolate: one failure, one error result
        return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                "content": f"{type(e).__name__}: {e}"}

async def execute_turn(response) -> dict:
    calls = [b for b in response.content if b.type == "tool_use"]
    safe = [b for b in calls if b.name in PARALLEL_SAFE]
    unsafe = [b for b in calls if b.name not in PARALLEL_SAFE]
    results = {}
    for r in await asyncio.gather(*(run_one(b) for b in safe)):   # concurrent; run_one never raises
        results[r["tool_use_id"]] = r
    for b in unsafe:                                              # writes: sequential, model's order
        r = await run_one(b)
        results[r["tool_use_id"]] = r
    return {"role": "user", "content": [results[b.id] for b in calls]}   # one message, every call answered

# inside the loop:
#   messages.append({"role": "assistant", "content": response.content})
#   messages.append(await execute_turn(response))
```

## Trade-offs
- Lower latency and fewer model turns for independent calls, but a burst of concurrent load on
  downstream systems; without a concurrency cap the speedup turns into rate-limit errors.
- Model-decided batching is convenient, but the model may batch calls that are dependent or
  side-effecting; the harness needs contract metadata to serialize them, which is extra design work.
- Returning all results at once makes one large message; with big results the context jumps in a
  single step, so per-result size caps matter more than in the sequential case.
- Disabling parallel calls simplifies the harness and guarantees ordering but gives up the speedup; a
  fair choice for low-volume, write-heavy agents where correctness dominates latency.

## Pitfalls
- On the Claude API, splitting results into separate user messages; the symptom is an API error about
  unanswered tool calls, or a model that stops batching after a few turns.
- Using `gather` without isolating failures; the symptom is one 404 cancelling every sibling call and
  a turn with no results at all.
- Running writes concurrently; the symptom is duplicated or out-of-order side effects that only appear
  under load.
- No per-call timeout; the symptom is a whole turn stalled on one slow upstream while the others
  finished long ago.
- Dropping the result for a call the policy refused to run; the symptom is a protocol error, when the
  correct move is an `is_error` result saying the call was declined.

## When to use
Enable parallel calls whenever an agent does many independent reads: research across sources, fan-out
lookups, multi-entity status checks. Keep them disabled, or serialize in the harness, for agents whose
tools are mostly writes or whose downstream systems cannot absorb bursts. Measure the average number of
calls per assistant turn (a value stuck at one means the model is not batching, usually because the
transcript taught it not to), the wall-clock per step, and the error rate under concurrency. In
day-to-day work the bugs are in the harness: isolation, timeouts, caps and the one-message rule. In
interviews, "what changes in your loop to support parallel calls" is a good test of whether someone
has built one; the answer is scheduling by contract metadata, failure isolation, and completeness of
the result message.

## Role lens
- **Engineer:** Implements concurrent execution with a semaphore, per-call timeouts and failure
  isolation, and returns exactly one `tool_result` per `tool_use` in a single message.
- **Architect:** Decides which tools are declared parallel-safe, sets concurrency caps per downstream
  system, and requires idempotency keys on any write that could be duplicated.
- **FDE:** Measures the customer's per-task latency, identifies the sequential fan-out, and enables
  batching only after checking the customer's API quotas can take the burst.

## Check yourself
**Q:** A research agent fetches eight web pages one at a time and takes about forty seconds per task.
Someone proposes "just turn on parallel tool calls". What has to change in the harness, and what could
go wrong?
**A:** The model will batch the fetches once the transcript shows that batching is answered correctly,
but the harness must change first: collect every `tool_use` block, run the fetches concurrently under a
concurrency cap so the target sites and your proxy are not hammered, give each a timeout, catch every
exception and convert it to an `is_error` result so one dead page does not cancel the rest, and return
all eight results in one user message. Then watch three risks: eight large pages arriving at once can
blow the context, so cap each result's size; fetched pages are untrusted and now arrive in bulk, so
the provenance labels and action gates must already be in place; and if any tool in the set has side
effects it must be excluded from the concurrent group. Confirm with calls per turn and wall-clock per task.

## Quiz
### Q: On the Claude API, why must all the tool results for one assistant turn be returned in a single user message?
- [ ] Because the API bills per user message
- [x] Because every tool call in the turn must be answered before the next assistant turn, and a transcript that answers them one at a time teaches the model to stop batching
- [ ] Because tool results are only matched by position, not by id
- [ ] Because concurrent execution requires a single thread to write the message
> The protocol expects completeness, and the transcript doubles as the model's in-context example of
> how calls are handled. Results are matched by `tool_use_id`, so order within the message does not
> matter. (OpenAI's API uses one tool message per call instead, but the completeness rule is the same.)

### Q: Which calls should the harness refuse to run concurrently by default?
- [ ] Calls to tools with long descriptions
- [ ] Calls whose arguments contain the same id
- [x] Side-effecting calls such as creates, updates and sends, unless their contract marks them parallel-safe
- [ ] Any call that returns more than one row
> Concurrent writes have no guaranteed order and can race or duplicate, and idempotency only removes
> the duplication risk, not the ordering race. Reads are safe. Argument overlap and result size are
> not what determine safety.

### Q: One of five concurrent calls times out. What should the harness send back?
- [ ] Nothing for that call; the model will notice it is missing
- [ ] An empty tool_result so the message stays complete
- [x] An is_error tool_result for the timed-out call alongside the four normal results
- [ ] Retry the whole turn, including the four calls that succeeded
> Every `tool_use` needs a `tool_result`, and an error result tells the model what happened so it can
> retry just that call. An empty result looks like success, and re-running the successful calls wastes
> time and may duplicate side effects.
