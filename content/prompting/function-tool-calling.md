---
id: function-tool-calling
module: prompting
name: Function / tool calling
kind: concept
difficulty: core
est_minutes: 15
related:
  - prompting/structured-outputs-json-mode
  - prompting/react
  - prompting/prompt-caching
  - prompting/prompt-injection
  - agents/agent-loop
  - agents/tool-calling
  - agents/tool-schemas-and-argument-validation
  - agents/model-context-protocol
sources:
  - Official docs: Anthropic tool use
  - Schick et al., 2023 — Toolformer: Language Models Can Teach Themselves to Use Tools
  - Yao et al., 2023 — ReAct: Synergizing Reasoning and Acting in Language Models
---

## TL;DR
A language model can only emit tokens: it cannot look anything up, change any state or do reliable
arithmetic. Function calling (tool calling) is a trained protocol in which you describe functions with a
name, description and JSON schema, the model emits a structured request to call one with arguments, your
code executes it and returns the result, and the model continues. The model never runs anything. The
"call" is a message; execution, permissions and error handling are yours.

## Why it exists
A model answering from weights alone has a knowledge cutoff, no access to your database, no way to send
an email, and a tendency to get long multiplications wrong. The first workaround was to ask the model to
write an action in its text, such as `ACTION: search("refund policy")`, parse it with a regular
expression, run it, and paste the result back. ReAct-style prompting made this systematic. It also
showed why text parsing is not enough: the model drifts in formatting, invents function names, writes
arguments as prose, and nothing in the API tells you that a call was intended rather than quoted.

Function calling fixes this at the model level. Providers fine-tune the model to recognise a block of
registered tool definitions rendered into the prompt and to produce calls as a dedicated structured
block with well-formed JSON arguments, accompanied by a distinct stop reason. The schema does three jobs
at once: it tells the model what exists, it constrains the arguments, and it gives your code a contract
to validate against. The result is a reliable, inspectable boundary between "the model decided" and
"the system did", which is exactly the boundary that agents, permissions and audit logs are built on.

## How it works
### The definition
A tool is a name, a description and an `input_schema` in JSON Schema. The definitions are rendered into
the prompt in a fixed position (ahead of the system prompt on Anthropic's API) in the format the model
was trained on, so they cost input tokens on every request; that is why a stable, deterministically
ordered tool list belongs in the cached prefix. The description is the model's only guide to *when* to
call, so it should state the trigger condition ("call this before answering any question about an
order's status") and not merely what the function does.

### The round trip
1. You send the tools and the messages.
2. The model decides whether to answer in text or to call. A call is a `tool_use` content block with an
   `id`, the tool `name` and a JSON `input`; the response stops with `stop_reason: "tool_use"`. One
   response may contain several `tool_use` blocks, which is how parallel calls are expressed.
3. Your code validates the input against the schema and your business rules, checks permissions,
   executes, and appends a user message containing one `tool_result` block per call, each carrying the
   matching `tool_use_id`. Failures go back as a result with `is_error: true`, not as an exception that
   ends the conversation, so the model can adapt. All results for one assistant turn go in one user
   message, because splitting them teaches the model that parallel calls do not work.
4. The model reads the results and either calls again or finishes with `end_turn`.

The ids exist *because* parallel and out-of-order results need correlation; without them the model
could not tell which result belongs to which request. The assistant message with the `tool_use` blocks
must be kept verbatim in history, since the result refers to it.

### Who controls the decision
`tool_choice` is `auto` by default (the model decides), `none` forbids calls, and some providers offer a
forced mode (`any`, or a named tool); Anthropic's newest models reject forced modes, so steer with the
prompt instead and use structured outputs when the forced call only existed to obtain JSON.
`disable_parallel_tool_use` limits a turn to one call, which matters for tools with side effects.
`strict: true` on a tool applies constrained decoding to the arguments, the same mechanism as
structured outputs, so the input validates exactly against the schema.

### The loop is yours
Each iteration is a full request carrying the whole history, so cost grows with the number of steps; a
cached prefix keeps that affordable. The loop needs a step budget, because a tool that keeps failing can
otherwise be retried forever. The agents module covers the loop, stopping conditions and selection
accuracy in depth; this lesson is the protocol those lessons build on.

### Tool results are untrusted input
Whatever a tool returns, including a web page or a document, enters the context as data the model will
read. If that data contains instructions, the model may follow them. That is prompt injection, and the
tool boundary is where it enters; the lesson on it covers the defences.

### Beyond your own functions
Providers also run server-side tools (web search, code execution) whose calls never reach your code, and
the Model Context Protocol standardises how tools are discovered and invoked across clients. Both are
the same contract seen from a different side.

## Example
A support agent with a read tool and a consequential write tool. The refund limit is enforced in the
execution code, not in the prompt; errors go back to the model as results; all results for a turn are
returned in one message; and a step budget bounds the loop. `db` and `payments` stand for your own
services.

```python
import json, anthropic

client = anthropic.Anthropic()
TOOLS = [
    {"name": "get_order", "strict": True,
     "description": "Look up an order by id. Call this before answering any question about an order's status, items or refund eligibility.",
     "input_schema": {"type": "object", "properties": {"order_id": {"type": "string", "description": "e.g. ORD-10422"}},
                      "required": ["order_id"], "additionalProperties": False}},
    {"name": "issue_refund", "strict": True,
     "description": "Refund an order. Only call after get_order shows the order is eligible.",
     "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}, "amount_cents": {"type": "integer"}},
                      "required": ["order_id", "amount_cents"], "additionalProperties": False}},
]

def run_tool(name, args):                                   # your code: validation, policy, side effects
    if name == "get_order":
        return json.dumps(db.get_order(args["order_id"]))
    if name == "issue_refund":
        if args["amount_cents"] > 50_00:                    # policy lives here, where a prompt cannot override it
            return {"error": "amount above auto-approval limit; escalate to a human"}
        return json.dumps(payments.refund(args["order_id"], args["amount_cents"]))

def chat(user_text, max_steps=6):
    messages = [{"role": "user", "content": user_text}]
    for _ in range(max_steps):
        r = client.messages.create(model="claude-opus-5-5", max_tokens=1024,
                                   system="You are a support agent for Acme.", tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": r.content})   # keep tool_use blocks verbatim
        if r.stop_reason != "tool_use":
            return next(b.text for b in r.content if b.type == "text")
        results = []
        for b in r.content:
            if b.type == "tool_use":
                out = run_tool(b.name, b.input)                            # b.input is parsed JSON already
                err = isinstance(out, dict) and "error" in out
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out) if err else out, "is_error": err})
        messages.append({"role": "user", "content": results})             # all results in ONE user message
    raise RuntimeError("step budget exhausted")
```

## Trade-offs
- Native tool calling gives reliable structure, parallel calls and a distinct stop reason, but the wire
  format is provider-specific; it flips toward an abstraction layer or MCP when you must stay portable.
- More tools mean more capability but lower selection accuracy and a larger prefix on every request; as
  the set grows, group tools, defer loading, or expose a search tool over the catalogue.
- Detailed, trigger-stating descriptions raise the should-call rate but cost tokens every request; a
  cached prefix makes that cost close to free, so err on the side of detail.
- Forcing a call guarantees a structured result but removes the model's judgement about whether a call
  is needed at all; prefer structured outputs when all you want is JSON.
- Returning errors as results lets the model self-correct, but a persistent error becomes a retry loop;
  cap retries per tool and per task.
- Parallel calls cut latency but complicate side effects (two refunds issued at once); disable
  parallelism or enforce idempotency keys for non-idempotent tools.

## Pitfalls
- Treating a `tool_use` block as execution; the symptom is the model reporting "I have sent the email"
  when nothing ran, or, worse, code executing unvalidated arguments straight from the block.
- Dropping the assistant's `tool_use` blocks from history or returning results in separate messages;
  the symptom is API errors about unmatched ids, or the model quietly ceasing to make parallel calls.
- Descriptions that say what but not when; the symptom is the model answering from memory instead of
  calling, or calling for every message including greetings.
- Loose parameter types with no enums or format hints; the symptom is dates in three formats, amounts in
  the wrong unit, and ids with stray whitespace.
- Encoding policy only in the prompt ("never refund more than $50"); the symptom is a successful
  injection or jailbreak producing a refund the code happily executed.
- No step budget; the symptom is a runaway loop on a persistently failing tool and a surprising bill.
- Passing raw stack traces or secrets back as tool results; the symptom is credentials in traces or the
  model echoing internal paths to the user.

## When to use
Reach for tool calling when the model needs facts it does not have, must act on the world, needs exact
computation, or must choose among several structured next steps. Do not use it when a fixed pipeline
suffices: if every request needs the same lookup, do the lookup in code and put the result in the
prompt, which is cheaper and deterministic. Measure tool selection accuracy, argument validity rate,
tool-call success rate, steps per task and cost per task, and inspect traces for calls that should not
have happened. In interviews, be able to narrate the round trip, explain why ids exist, say who executes
the call, and state where policy must live.

## Role lens
- **Engineer:** Writes schemas and trigger-stating descriptions, validates inputs, implements the loop
  with a step budget, returns errors as results, and logs every call with its arguments and outcome.
- **Architect:** Defines which capabilities are exposed as tools and with what privileges, where approval
  gates and idempotency sit, and whether the organisation standardises on MCP for tool exposure.
- **FDE:** Wraps the customer's internal APIs as tools, maps authorisation to the customer's identity
  model, and tunes descriptions from the mis-selections visible in the customer's traces.

## Check yourself
**Q:** You are building a support agent that must look up orders and sometimes issue refunds. Design the
tool interface and the loop, and say where the refund rules live and why.
**A:** Two tools: `get_order` (read) and `issue_refund` (write), each with a strict schema and a
description stating when to call it, for instance "only after get_order shows eligibility". The loop
sends tools and history, executes any `tool_use` blocks after schema validation, returns all results for
the turn in one user message with matching ids and `is_error` on failures, and stops on `end_turn` or a
step budget. The refund rules (limit, eligibility window, one refund per order) live in the execution
code behind the tool, with an idempotency key and a human-approval gate above a threshold, because
anything stated only in the prompt can be overridden by a jailbreak or by instructions hidden in an order
note. Parallel calls are disabled for the refund tool, every call is logged, and tool results are treated
as untrusted text.

## Quiz
### Q: When the model "calls a tool", who executes the function?
- [ ] The provider's servers, inside a sandbox attached to the model
- [x] Your application code, after receiving the structured request from the model
- [ ] The model itself, during generation of the response
- [ ] The tokenizer, which maps the call to a registered handler
> A tool call is a content block the model emits; nothing runs until your code reads it, validates the
> arguments and executes. Server-side tools are the exception and are declared separately.

### Q: Why does every tool result carry the id of the tool_use block it answers?
- [ ] So the provider can bill the call to the correct tool
- [ ] Because the model cannot read results without an id
- [x] To correlate results with calls when a turn contains several parallel or reordered calls
- [ ] To let the API deduplicate repeated calls automatically
> One assistant turn can request several calls, and results may arrive in any order. The id is the
> only link between a request and its answer; a mismatch breaks the conversation.

### Q: Where should the rule "never refund more than $50 automatically" be enforced?
- [ ] In the system prompt, so the model knows the limit
- [ ] In the tool description, so the model applies it when choosing the tool
- [x] In the code that executes the refund tool, with the prompt stating it only as guidance
- [ ] In the user message, restated on every turn
> Prompt text can be overridden by injection or jailbreaks. A rule that must hold is enforced where
> execution happens; the prompt may repeat it so the model explains the refusal gracefully.
