---
id: agent-loop
module: agents
name: Agent loop
kind: concept
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - agents/tool-calling
  - agents/workflows-vs-agents
  - agents/state-machines
  - agents/context-compaction-summarization
  - agent-reliability/max-step-budgets
  - agent-reliability/stopping-conditions-termination
  - agent-reliability/loop-detection
  - prompting/react
sources:
  - Yao et al., 2022 — ReAct: Synergizing Reasoning and Acting in Language Models
  - Anthropic, 2024 — Building effective agents
  - Official docs: Anthropic Claude API tool use
---

## TL;DR
An agent is a loop, not a model. The model proposes an action as a structured tool call, your code
(the harness) executes it, the result is appended to the transcript, and the model is called again with
everything so far, until it stops asking for actions or a budget runs out. The model only ever chooses
the next step; the loop is what lets it observe the consequence of each step and correct course, which
is exactly what one-shot generation cannot do.

## Why it exists
A language model call is a pure function from a prompt to text. It cannot read a database, run a test
or check whether an email actually went out, and it cannot see what happened after it answered. For a
task like "find out why the nightly job failed and fix it", the information needed to decide step three
does not exist until steps one and two have run. No amount of prompting fixes that, because the missing
facts are outside the prompt.

The naive fix is to ask the model for a complete plan up front and execute the plan with ordinary code.
That works only when the world is predictable: the first command that returns something unexpected (a
file is missing, a query returns 40,000 rows instead of 4) invalidates every later step, and the
pre-made plan has no way to react. The other naive fix, a hand-written pipeline of fixed steps, is
reliable but can only solve the cases its author foresaw.

The agent loop is the minimal structure that gives the model feedback: act, observe, decide again. Each
iteration is still a single stateless model call; the *state* is the transcript, which grows by one
action and one observation per step. That is the whole trick, and everything else in agent engineering
(budgets, approvals, memory, compaction) is a consequence of it.

## How it works
### The three parties
- **The model** reads the transcript and emits either final text or one or more tool-call blocks (a
  tool name plus JSON arguments). It never executes anything.
- **The harness** is your code: it sends requests, runs the tools the model asked for, decides what the
  model is allowed to do, appends results, and decides when to stop.
- **The environment** is whatever the tools touch: APIs, files, databases, a browser, a shell.

The split matters because authority lives in the harness. The model expresses intent; the harness can
refuse, ask a human, log, rate-limit or substitute. A design that lets the model execute directly has
nowhere to put policy.

### One iteration
1. Build the request: system prompt, tool definitions, and the full message history.
2. Call the model. The response carries a `stop_reason`. If it is `tool_use`, the content contains one
   or more `tool_use` blocks, each with a unique `id`, a tool `name` and already-parsed `input`.
3. Append the assistant message to the history **unchanged**, including the `tool_use` blocks, because
   the next request must show the model what it asked for and the API matches results to requests by id.
4. Execute each requested tool. Catch exceptions and turn them into results rather than crashing: an
   error the model can read is recoverable, an exception that kills the loop is not.
5. Append one user message containing a `tool_result` block per call, each carrying its `tool_use_id`.
   If several tools were requested in one turn, all their results go in that same message. (This is
   the Claude API's shape; OpenAI's Chat Completions instead sends one `role: "tool"` message per
   call. The invariant on every API is that each call is answered before the next model turn.)
6. Go to step 1.

The loop ends when `stop_reason` is anything other than `tool_use` (normally `end_turn`; with
provider-run server tools, `pause_turn` means "send the conversation back to let me continue", not
"done"), or when the harness decides to stop: a step budget, a wall-clock or token budget, a detected repetition, or an
unrecoverable error. `max_tokens` as a stop reason means the model was cut off mid-answer, not that it
finished; treat it as a failure to retry, never as a result to execute.

### Why the whole transcript is resent every time
The model is stateless, so every iteration sends the entire history. Over *n* steps the tokens
processed grow roughly as 1 + 2 + … + n, quadratic in the number of steps. Prompt caching makes this
affordable *because* the history is append-only: the cached prefix stays valid and only the new tail
is processed. This is why harnesses never edit earlier messages in place; an edit invalidates the cache
from that point onward. Reasoning output adds a harder constraint on some APIs: Claude's thinking blocks
carry a signature and must be passed back unmodified within a tool-use turn, and OpenAI's reasoning
items are returned encrypted for the same reason, so tampering with them gets the request rejected. When the history must shrink, it is compacted explicitly (summarized, or old tool results
cleared) as a separate, deliberate operation.

### Where reasoning fits
Before native tool calling, ReAct-style prompting made the model write "Thought / Action / Observation"
as text and the harness parsed it with regular expressions. Native tool calling replaced the parsing
with structured blocks, and models trained for it reason between calls, visibly or in hidden thinking
blocks. The loop shape is identical; what changed is reliability.

## Example
A complete manual loop with the three things most toy versions omit: a step budget, errors returned as
results instead of raised, and all results for a turn returned in one message. The second order id does
not exist, so the model receives an error result for it and has to handle that in its answer.

```python
import json, anthropic

client = anthropic.Anthropic()
TOOLS = [{
    "name": "get_order",
    "description": "Look up one order by id. Call this before answering any question "
                   "about an order's status, items or delivery date.",
    "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}},
                     "required": ["order_id"], "additionalProperties": False},
}]
DB = {"A100": {"status": "shipped", "eta": "2026-10-23"}}

def get_order(order_id: str) -> dict:
    if order_id not in DB:
        raise KeyError(f"no order with id {order_id}")
    return DB[order_id]

def run(user_text: str, max_steps: int = 8) -> str:
    messages = [{"role": "user", "content": user_text}]
    for _ in range(max_steps):
        resp = client.messages.create(model="claude-opus-5-5", max_tokens=8192,
                                      tools=TOOLS, messages=messages)
        if resp.stop_reason == "max_tokens":        # cut off: never return or execute it
            raise RuntimeError("truncated turn; retry with a higher max_tokens")
        messages.append({"role": "assistant", "content": resp.content})  # keep tool_use blocks
        if resp.stop_reason != "tool_use":          # end_turn, stop_sequence, refusal
            return "".join(b.text for b in resp.content if b.type == "text")
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            try:
                content, is_error = json.dumps(get_order(**block.input)), False
            except Exception as e:                  # the model sees the failure; the loop survives
                content, is_error = f"error: {e}", True
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": content, "is_error": is_error})
        messages.append({"role": "user", "content": results})  # all results, one message
    raise RuntimeError(f"no final answer after {max_steps} steps")

print(run("Where is order A100, and has order A200 shipped yet?"))
```

## Trade-offs
- Adaptivity versus predictability: the loop handles cases nobody scripted, but its path, cost and
  latency vary per run; when the task is a fixed sequence of known steps, a workflow with one or two
  model calls is cheaper, deterministic and easier to test.
- Each iteration is a full model call, so latency and cost scale with step count, and resending the
  transcript makes cost superlinear without prompt caching; this only pays off when tool results
  genuinely change what the next step should be.
- Append-only history keeps caches and reasoning blocks valid, but the context fills with stale tool
  output; past a few dozen steps you either pay for compaction machinery or accept degraded attention
  over a long history.
- Putting policy in the harness (budgets, approvals, allow-lists) adds code and friction, but the
  alternative, trusting the model to stop itself, has no upper bound on damage or spend.

## Pitfalls
- Dropping or re-serializing the assistant's `tool_use` blocks before sending results; the symptom is
  an API validation error about an unmatched `tool_use_id`, or a model that re-issues the same call.
- No step or token budget; the symptom is a run that bounces between two tools for an hour and is
  usually discovered through the bill.
- Letting a tool exception propagate out of the loop instead of returning `is_error: true`; the symptom
  is a crash on exactly the inputs where you wanted the model to recover.
- Treating `stop_reason == "max_tokens"` like a finished answer; the symptom is truncated JSON or a
  half-written tool call that the harness then tries to execute.
- Editing earlier messages to "fix" the model's path mid-run; the symptom is a cache hit rate that
  collapses and, if the edit touches signed or encrypted reasoning blocks, rejected requests.

## When to use
Reach for a loop when the next action genuinely depends on the result of the previous one and the
space of possible paths is too large to enumerate: debugging, research across many sources, multi-step
operations against live systems. Do not reach for it when the steps are known in advance; a workflow
with explicit code between model calls is cheaper, deterministic and testable. Measure steps per task,
tokens per task, task completion rate and how often the budget trips; a rising step count with a flat
completion rate is the first sign that the tools or instructions are wrong, not the loop. Day to day you
spend most of your time on the harness, not the prompt: budgets, error-to-result conversion, logging
every step, and the stop conditions. In interviews, expect "walk me through one iteration" and "where
does the state live"; the strong answer names the transcript as the state, the harness as the
authority, and append-only history as the reason caching works.

## Role lens
- **Engineer:** Writes the loop and its stop conditions, converts exceptions into `tool_result`
  errors, and traces every iteration with the step number, tool name and token usage.
- **Architect:** Decides which decisions belong to the model and which are hard-coded in the harness,
  and sets the budgets, approval points and compaction policy that bound cost and blast radius.
- **FDE:** Reads a customer's failed run as a transcript of steps, spots where the loop went wrong (bad
  result, missing stop condition, wrong tool) and fixes the harness rather than the prompt.

## Check yourself
**Q:** A colleague proposes saving tokens by sending the model only the latest tool result on each
iteration instead of the full history. What breaks, and what is the correct way to cut the cost?
**A:** The model is stateless, so without the history it cannot know what it already tried, which call
the result belongs to, or what the original task was; it will repeat calls or answer the wrong question,
and the API will reject a `tool_result` whose `tool_use_id` has no matching `tool_use` block in the
conversation. The correct levers are prompt caching, which makes the append-only prefix cheap to resend;
keeping tool results small and structured so the transcript grows slowly; clearing or summarizing old
tool results in an explicit compaction step when the context gets large; and a step budget so a stuck
run does not keep paying. Cost is controlled by shaping what enters the transcript, not by hiding the
transcript from the model.

## Quiz
### Q: Why must the assistant message containing the tool_use blocks be appended to the history before the tool results?
- [ ] Because the API bills tool calls only when they appear in the history
- [x] Because each tool_result must reference a tool_use id the model can see, and the model needs a record of what it asked for
- [ ] Because tool results are only valid inside an assistant message
- [ ] Because the model deletes tool_use blocks from its own output otherwise
> A `tool_result` is matched to its request by `tool_use_id`; if the request block is missing the API
> rejects the message, and even if it did not, the model would have no record of which action the
> result answers.

### Q: After step 12 of a run the response has stop_reason "max_tokens" and a partial tool_use block. What should the harness do?
- [ ] Execute the partial tool call with default values for the missing arguments
- [ ] Treat the text produced so far as the final answer
- [x] Treat it as a truncation, retry the turn with a higher output limit, and never execute the partial call
- [ ] Append an empty tool_result and continue the loop
> `max_tokens` means the model was cut off, so the call's arguments are untrustworthy. Executing or
> faking a result turns a truncation into a wrong side effect; the recoverable move is to retry the turn.

### Q: Which fact most directly explains why agent harnesses keep message history append-only?
- [ ] Models are trained to reject conversations that have been edited
- [x] Prompt caching and signed reasoning blocks depend on an unchanged prefix, so edits make later turns more expensive or invalid
- [ ] Appending is the only operation the SDK message list supports
- [ ] Edited history exceeds the context window faster than appended history
> Caching works by matching the prefix byte for byte, so an edit at position *i* invalidates everything
> after it. Compaction is done as an explicit operation precisely because it is the one place where the
> prefix is allowed to change.
