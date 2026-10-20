---
id: tool-calling
module: agents
name: Tool calling
kind: concept
difficulty: core
est_minutes: 13
status: reviewed
related:
  - agents/agent-loop
  - agents/tool-schemas-and-argument-validation
  - agents/tool-selection-accuracy
  - agents/tool-result-interpretation
  - agents/parallel-tool-calls
  - agents/model-context-protocol
  - prompting/function-tool-calling
  - prompting/structured-outputs-json-mode
sources:
  - Schick et al., 2023 — Toolformer: Language Models Can Teach Themselves to Use Tools
  - Official docs: Anthropic Claude API tool use
---

## TL;DR
Tool calling lets a model request an action instead of describing one. Given declared tools (a name, a
description and a JSON Schema for the arguments), the model emits a structured call, your code executes
it and returns the result, and the model continues with that result in context. It exists because models
cannot reach live data or cause effects, and because parsing actions out of free text is unreliable; a
trained structured format turns "text that looks like a command" into a typed request.

## Why it exists
A model's knowledge stops at its training cutoff and its reach stops at its output text. Ask it for
today's exchange rate or to create a ticket and it can only guess or describe. The first workaround was
to prompt it to write commands in text ("respond with ACTION: search(query)") and parse them with regular
expressions. That failed in three ways: the model drifted from the format in long contexts, the parser
could not tell a quoted example from a real command, and arguments were free text, so a date arrived as
"next Tuesday" when the API needed ISO 8601.

Tool calling (OpenAI's term "function calling" names the same idea) moves the format into the model's
training and into the API contract. The model is trained to emit a dedicated content block containing a
tool name and arguments that conform to the declared schema, and the API parses that block before you
see it. Your code receives a dictionary, not a string to scrape. Crucially, the model still does
nothing: it issues a request, and whether that request is honoured, modified, rejected or routed to a
human is decided by the code that receives it. That separation is what makes tool calling safe enough
to connect to real systems, and it is why "the model can call tools" really means "the model can ask".

## How it works
### Declaration
Each tool is declared with a `name`, a `description`, and an `input_schema` (JSON Schema, normally an
object with typed properties, a `required` list and `enum` where the values are fixed). The declarations
are rendered into the prompt on every request (on the Claude API they sit at the very front: the cache
order is tools, then system, then messages), so they consume input tokens every time and form part of
the cacheable prefix; changing one tool invalidates the cache for everything after it. The description is the single most important field:
it is all the model has to decide *whether* and *when* to call the tool, so it should state trigger
conditions ("call this when the user asks about an order's status") and not only what the tool does.

### The model's side
Given the prompt, the model chooses among plain text, one or more tool calls, or text followed by calls.
A call arrives as a `tool_use` content block with a unique `id`, the `name`, and `input` already parsed
into JSON, and the response's `stop_reason` is `tool_use`, which is the harness's signal that work is
expected. A `tool_choice` parameter steers the decision: `auto` (the default), `none` (never call), and
`any` or a named tool to force a call (OpenAI spells these `auto`, `none`, `required` and a named
function). Forcing is not always available: on the Claude API, requests with extended thinking enabled
accept only `auto` and `none`, so there the instruction has to go in the prompt. Treat forcing as
model- and mode-dependent and always check that a `tool_use` block actually came back. With `strict: true` on the declaration (and
`additionalProperties: false` in the schema) the API guarantees the arguments validate against the
schema, which removes a class of parsing failures but says nothing about whether the values make sense.

### Under the hood
Nothing about tool calling is outside next-token prediction. The provider serializes your declarations
into the prompt in a format the model was fine-tuned on, and the model emits the call as ordinary tokens
wrapped in special delimiter tokens; the API recognises the delimiters, stops generation, parses the
arguments into JSON and hands you a `tool_use` block. Two consequences follow. First, without `strict`,
argument validity is a *trained tendency*, not a guarantee: the model usually produces schema-shaped JSON
*because* it was trained on millions of such calls, but it can still omit a required field or invent an
enum value, so your handler must validate. Second, `strict` mode works by constrained decoding: the
schema is compiled into a grammar and, at each step, tokens that would break it are masked out before
sampling. That is why it can *guarantee* shape, why it supports only a subset of JSON Schema (the grammar
has to be compilable), and why it cannot help with meaning; a grammar can force `"date"` to be a string
matching a pattern, not to be the date the user meant.

### Your side
For each `tool_use` block you execute the matching function with `input` as its arguments and send back
a user message containing a `tool_result` block with the same `tool_use_id` and a `content` (a string,
or a list of text and image blocks). If the tool failed, set `is_error: true` and put a readable message
in `content`; models are trained to treat such results as recoverable and will retry, adjust arguments
or explain. Several calls in one turn get several `tool_result` blocks in a single message, and on the
Claude API the `tool_result` blocks must come first in that message's content, before any text you add.
(OpenAI's Chat Completions API differs: one `role: "tool"` message per call, keyed by `tool_call_id`.) The model is
then called again with the extended history, which is one iteration of the agent loop.

### Client-side versus server-side tools
Tools you execute are client-side: the API returns the request and waits for your result. Providers also
offer server-side tools (web search, code execution, hosted MCP connections) where the provider executes
the call inside the same request and the result appears directly in the response. Server-side tools
remove the round trip and the hosting burden; client-side tools keep execution, secrets and policy in
your process. Both kinds can be declared in one request.

### Token and latency accounting
Every declared tool costs tokens on every call, so a catalogue of fifty tools can be several thousand
tokens of fixed overhead, and selection accuracy degrades as the catalogue grows. Each round trip is a
full model call plus your tool's latency. Those two facts drive most of the engineering around tool
calling: small focused tool sets, deferred loading or tool search for large catalogues, parallel calls
for independent reads, and compact results.

## Example
One tool, one call, one result, one answer. The point is the exact shape of each message: the assistant
turn carries the `tool_use` block, the following user turn carries the `tool_result` with the matching
id, and only then does the model produce the final text. The rate itself is stubbed.

```python
import json, anthropic

client = anthropic.Anthropic()
tools = [{
    "name": "get_fx_rate",
    "description": "Return the current exchange rate between two ISO 4217 currencies. "
                   "Call it whenever the user asks for a conversion or a current rate; "
                   "never estimate a rate from memory.",
    "input_schema": {
        "type": "object",
        "properties": {
            "base": {"type": "string", "description": "3-letter code, e.g. EUR"},
            "quote": {"type": "string", "description": "3-letter code, e.g. USD"},
        },
        "required": ["base", "quote"], "additionalProperties": False,
    },
    "strict": True,
}]

def get_fx_rate(base: str, quote: str) -> dict:                       # stub for the lesson
    return {"base": base, "quote": quote, "rate": 1.0842, "as_of": "2026-10-20T09:00Z"}

messages = [{"role": "user", "content": "How much is 250 EUR in USD right now?"}]
first = client.messages.create(model="claude-opus-5-5", max_tokens=8192,
                               tools=tools, messages=messages)
assert first.stop_reason == "tool_use"
call = next(b for b in first.content if b.type == "tool_use")
print(call.name, call.input)                       # get_fx_rate {'base': 'EUR', 'quote': 'USD'}

messages += [
    {"role": "assistant", "content": first.content},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call.id,
                                  "content": json.dumps(get_fx_rate(**call.input))}]},
]
final = client.messages.create(model="claude-opus-5-5", max_tokens=8192,
                               tools=tools, messages=messages)
print(next(b.text for b in final.content if b.type == "text"))   # "250 EUR is about 271.05 USD ..."
```

## Trade-offs
- Structured calls are reliable to parse but cost declaration tokens on every request; when only one or
  two tools are ever relevant, a large catalogue is pure overhead and should be trimmed or loaded on demand.
- Letting the model decide (`auto`) handles mixed conversations, but for a step that must always produce
  a call, a forced choice or an explicit prompt instruction plus a check for a `tool_use` block is more
  predictable; which of these is available depends on the model.
- Server-side tools cut latency and hosting work but move execution, data and credentials to the
  provider; when the tool touches internal systems or needs approval gates, client-side execution is the
  only option.
- `strict` schemas guarantee argument shape at the cost of a restricted, provider-specific schema
  vocabulary (support for recursion and numeric or length constraints varies); when you need rich validation, keep the schema simple and validate the meaning
  of the values in your handler.

## Pitfalls
- Descriptions that say what the tool does but not when to use it; the symptom is a model that answers
  from memory when it should have called, or picks the wrong one of two similar tools.
- Returning raw API payloads of thousands of tokens as the result; the symptom is context exhaustion
  after a handful of steps and answers that miss the one field buried in the dump.
- Raising on tool failure instead of returning `is_error: true`; the symptom is a crashed loop where a
  retry with corrected arguments would have succeeded.
- Matching on the serialized `input` string (in tests or caches) rather than the parsed object; the
  symptom is spurious mismatches when the model escapes Unicode or slashes differently between runs.
- On the Claude API, sending results for several calls as separate user messages, or putting text
  before the `tool_result` blocks; the symptom is a validation error about unanswered `tool_use` ids,
  or a model that quietly stops batching independent calls.

## When to use
Use tool calling whenever the answer depends on data the model does not have or on an effect it cannot
cause: lookups, calculations that must be exact, writes to systems, anything time-sensitive. Do not use
it merely to get structured output from a model that is not acting; a JSON schema on the response
(structured outputs) is the right tool there where the provider supports it, and forcing a "function"
just to extract JSON is a leftover habit from before that existed. Measure three things: how often the model calls when it should and refrains when it
should not, how often arguments validate, and tokens per completed task. Day to day the work is writing
descriptions, keeping schemas small, shaping results and handling `is_error`. In interviews, be able to
draw the request and response shapes from memory and explain why the model never executes anything
itself.

## Role lens
- **Engineer:** Declares tools with trigger-condition descriptions, dispatches `tool_use` blocks to
  handlers, and returns compact `tool_result` blocks with `is_error` set on failure.
- **Architect:** Decides which capabilities become tools, which run client-side versus provider-side,
  and where approval and audit sit between the model's request and the execution.
- **FDE:** Maps a customer's existing APIs onto a small tool set the model can select reliably, and
  proves it with a selection eval before connecting any write operations.

## Check yourself
**Q:** Your support agent has 40 tools and users complain that it "forgets to look things up". What are
the likely causes, and how would you diagnose and fix them?
**A:** Three causes dominate. First, descriptions that describe function but not trigger conditions, so
the model does not know when a lookup is expected; rewrite them as "call this when …" and add negative
guidance for near-duplicates. Second, too many overlapping tools diluting selection; consolidate (one
`search_records` with a `type` enum beats five searchers) or defer rarely used tools so only relevant
ones sit in context. Third, a system prompt that rewards answering directly. Diagnose with a selection
eval: a few hundred real queries labelled with the expected tool or "none", scored for should-call
recall, tool-name accuracy and argument validity, changing one lever at a time. Confirm in production by
logging the ratio of calls to answerable queries.

## Quiz
### Q: What does the model actually do when it "calls" a tool?
- [ ] Executes the function in a provider-hosted sandbox and returns the output
- [x] Emits a structured request (name and JSON arguments) that the calling code may execute, modify or refuse
- [ ] Sends an HTTP request to the URL given in the tool description
- [ ] Looks up the answer in a cache of previous tool results
> The model only produces a `tool_use` block; execution, policy and the result are entirely the
> harness's responsibility. Server-side tools are executed by the provider, but even then the model
> itself only issued the request.

### Q: A tool handler throws because the user gave an invalid order id. What should be sent back to the model?
- [ ] Nothing; stop the loop and show the exception to the user
- [ ] A tool_result with empty content so the model moves on
- [x] A tool_result with is_error set and a readable message explaining what was invalid
- [ ] A new user message saying "that failed" without any tool_result
> Models are trained to treat an error result as something to recover from: re-ask, correct the
> argument or explain. An empty result looks like a successful lookup that found nothing, which leads
> to a confidently wrong answer.

### Q: Which field most influences whether the model decides to call a tool at all?
- [ ] The position of the tool in the tools array
- [ ] The number of required parameters
- [x] The tool description, especially explicit conditions for when it should be used
- [ ] The length of the tool name
> Selection is driven by the description; the schema shapes the arguments once a tool is chosen. Array
> position and name length matter far less than a description that states the trigger condition.
