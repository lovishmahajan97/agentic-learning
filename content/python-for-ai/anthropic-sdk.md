---
id: anthropic-sdk
module: python-for-ai
name: Anthropic SDK
kind: tool
difficulty: core
est_minutes: 16
status: reviewed
related:
  - python-for-ai/openai-sdk
  - python-for-ai/litellm
  - python-for-ai/pydantic
  - prompting/prompt-caching
  - prompting/function-tool-calling
  - agents/tool-calling
  - agents/parallel-tool-calls
  - node-ts-for-ai/openai-anthropic-node-sdks
sources:
  - Official docs: Anthropic API (Messages API)
  - Official docs: anthropic-sdk-python
---

## TL;DR
The `anthropic` Python package is the official typed client for Claude's Messages API. Its model is
different enough from OpenAI's to matter: responses are lists of typed content blocks (text, thinking,
tool use), the system prompt is a top-level parameter, `max_tokens` is required, tool results go back
as blocks in a user message, and prompt caching is controlled explicitly with `cache_control`. Learn
those shapes and most Claude integration bugs become obvious.

## Why it exists
Calling Claude means solving the same transport problems as any LLM API (authentication, retries on
429 and 5xx, timeouts, server-sent-event streaming), plus problems specific to its design. A single
response can mix several kinds of content: reasoning blocks, text, and one or more tool calls. Agent
loops must echo assistant content back faithfully, including blocks your code does not display,
because the API expects the conversation history to contain them. Prompt caching only pays off if the
cached prefix is byte-identical across requests and the breakpoints sit in the right places.

Hand-rolled HTTP clients tend to treat the response as one string, which silently drops tool calls,
loses reasoning blocks needed for the next turn, or concatenates partial stream deltas incorrectly.
Generic multi-provider wrappers solve portability but often lag behind provider-specific features such
as cache breakpoints, citations, batches or extended reasoning controls, or flatten them away.

The SDK exists to make the API's real shape the path of least resistance: typed request parameters,
typed content-block classes you branch on with `.type`, streaming helpers that accumulate the final
message for you, an exception hierarchy that separates retryable from fatal failures, and helpers for
token counting, batches and the tool-use loop. It also provides client classes for Claude on Amazon
Bedrock, Google Vertex AI and Microsoft Foundry, so the same calling code works across those platforms
(feature availability still differs per platform).

## How it works
### Client
`anthropic.Anthropic()` resolves credentials from the environment (typically `ANTHROPIC_API_KEY`);
`AsyncAnthropic` is the asyncio twin. Like any pooled HTTP client, create it once per process. Timeouts
(default ten minutes) and `max_retries` (default 2, covering connection errors, 408, 409, 429 and 5xx
with exponential backoff) are set on the client or per call via `client.with_options(...)`. Note that
timeouts are retried too, so worst-case wall-clock time is the timeout times the number of attempts.

### The Messages call
`client.messages.create(model=..., max_tokens=..., system=..., messages=[...])`. Design choices:
- **`system` is top-level**, not a message, because it is operator configuration rather than a turn,
  and keeping it separate makes it a stable, cacheable prefix.
- **`max_tokens` is required** so every call has an explicit output ceiling; hitting it ends the
  response with `stop_reason == "max_tokens"`, which your code must treat as truncation.
- **`messages` is a sequence of user and assistant turns** (consecutive turns with the same role are
  merged), and each message's `content` may be a string or a list of blocks (text, image, document,
  tool_result).

### Content blocks and stop reasons
`response.content` is a list. Branch on `block.type`: `"text"`, `"thinking"` (reasoning; opt-in on older models, on by default on
recent ones, and its visible text may be summarised or empty depending on the display setting),
`"tool_use"` (with `id`, `name`, `input` already parsed as a dict). `stop_reason` tells you what to do
next: `end_turn` (done), `tool_use` (run tools and continue), `max_tokens` (truncated), `stop_sequence`,
`pause_turn` (a long server-side tool turn to resume) and `refusal` (declined; check it before reading
content).

Thinking blocks carry a cryptographic `signature`. Echo them back unchanged on the next turn with the
same model, because the server verifies them and uses them to continue the reasoning across tool
calls. The practical rule this produces: treat conversation history as append-only. Rewriting or
trimming earlier assistant turns can invalidate the thinking attached to them, and on recent models
the exact enforcement varies by model and account, so check the current docs before building a harness
that edits history.

### Tool loop
You declare tools as `name`, `description`, `input_schema` (JSON Schema). When `stop_reason` is
`tool_use`, append the assistant's full `response.content` as an assistant message, run each requested
tool, and send one user message containing a `tool_result` block per call, each with the matching
`tool_use_id` (set `is_error: True` for failures rather than dropping them). All results for one turn go
in a single message; splitting them across messages breaks the parallel-call pattern. The SDK's beta
tool runner can drive this loop for decorated Python functions.

`tool_choice` controls whether the model may or must call tools (`auto`, `none`, and on some models
forced `any` or a named tool). Forcing is not supported on every current model, so the portable way to
guarantee a well-formed call is `auto` plus a clear instruction, with `strict: true` on the tool
definition so `input` always validates against `input_schema`.

### Structured output
When you want JSON rather than a tool call, `output_config={"format": ...}` with a JSON Schema constrains
the response to that schema, and `client.messages.parse(...)` accepts a Pydantic model and returns the
validated object. This replaces the older trick of defining a fake tool just to get structured
arguments back. You still check `stop_reason`: a refusal or a `max_tokens` cut-off means there is no
complete object to parse.

### Prompt caching
Mark a block with `cache_control={"type": "ephemeral"}` (or use top-level automatic caching). The API
caches the prefix up to that breakpoint, in render order tools, then system, then messages. Later
requests with a byte-identical prefix read it at a large discount and lower latency. `usage` reports
`cache_creation_input_tokens` and `cache_read_input_tokens`, which is how you verify it works. Any change
earlier in the prefix (a timestamp in the system prompt, reordered tools) invalidates everything after.

### Streaming, counting, batches
`with client.messages.stream(...) as s:` yields events and `s.text_stream`, and `s.get_final_message()`
returns the assembled message, so you never hand-merge deltas. Streaming is recommended for long
outputs and large `max_tokens`, where a non-streaming request can hit HTTP timeouts; the Python SDK
refuses a non-streaming request whose `max_tokens` implies an expected duration beyond its timeout and
tells you to stream instead.
`client.messages.count_tokens(...)` returns the exact input token count for a request, the right tool
for Claude because local OpenAI tokenizers do not match. `client.messages.batches` submits many
requests for asynchronous processing at a discount, with results keyed by your `custom_id`.

## Example
A minimal tool-use loop with caching of the stable system prompt. Model IDs change; take them from
configuration.

```python
import os, anthropic

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
client = anthropic.Anthropic(max_retries=3)
SYSTEM = [{"type": "text", "text": "You are an order-support agent. Use tools for facts.",
           "cache_control": {"type": "ephemeral"}}]  # caches only above the model's minimum prefix length
TOOLS = [{"name": "get_order", "description": "Look up an order by id.",
          "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}},
                           "required": ["order_id"]}}]

def get_order(order_id: str) -> str:
    return f'{{"order_id": "{order_id}", "status": "shipped", "eta": "2 days"}}'

messages = [{"role": "user", "content": "Where is order A-1042?"}]
while True:
    resp = client.messages.create(model=MODEL, max_tokens=8192, system=SYSTEM,   # thinking counts too
                                  tools=TOOLS, messages=messages)
    print(resp._request_id, resp.usage.input_tokens, resp.usage.cache_read_input_tokens)
    if resp.stop_reason != "tool_use":
        break
    messages.append({"role": "assistant", "content": resp.content})   # echo every block back
    results = []
    for block in resp.content:
        if block.type == "tool_use":
            out = get_order(**block.input) if block.name == "get_order" else "unknown tool"
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
    messages.append({"role": "user", "content": results})               # all results, one message

if resp.stop_reason == "refusal" or resp.stop_reason == "max_tokens":
    print("did not finish normally:", resp.stop_reason)
print("".join(b.text for b in resp.content if b.type == "text"))
```

Trace of a typical run: call 1 returns `stop_reason="tool_use"` with content such as
`[thinking, tool_use(id="toolu_…", name="get_order", input={"order_id": "A-1042"})]`. History becomes
three messages: the user question, the assistant turn with every block intact, and a user turn holding
one `tool_result` whose `tool_use_id` matches. Call 2 resends all three, the model reads the result and
returns `stop_reason="end_turn"` with a text block. Each call re-reads the whole history, so input tokens
grow every iteration, which is exactly the cost that caching the stable prefix attacks.

This toy system prompt is far shorter than the minimum cacheable prefix (model-dependent, from a few
hundred to a few thousand tokens), so here `cache_read_input_tokens` stays at zero; with a realistic
multi-thousand-token system prompt and tool list it rises from the second call. A production loop also
caps iterations, validates `block.input` before running a tool, and catches
`anthropic.RateLimitError`, `anthropic.APIStatusError` and `anthropic.APIConnectionError` separately.

## Trade-offs
- The native SDK exposes Claude-specific features fully (cache breakpoints, content blocks, batches,
  reasoning controls), but code written against it is not portable; a router helps once several
  providers are genuinely in production.
- Explicit cache breakpoints give precise control over what is cached and what it costs, but require
  disciplined prompt layout; automatic caching is simpler when you do not need per-block placement.
- Batches cut cost substantially for offline work, but results arrive asynchronously and in any order,
  so they suit evals and backfills, not interactive requests.
- Built-in retries absorb transient failures cheaply, but on long timeouts they multiply worst-case
  latency; interactive paths should set short timeouts and few retries.

## Pitfalls
- Reading `response.content[0].text` blindly: when the first block is thinking or a tool call, you get an
  attribute error or an empty answer, and tool calls are silently ignored.
- Appending only the text of an assistant turn instead of `response.content`: the next request fails
  validation because `tool_result` blocks reference `tool_use` ids that are no longer in history.
- Putting a timestamp or request ID at the top of the system prompt: `cache_read_input_tokens` stays at
  zero on every call and costs never drop.
- Setting a small `max_tokens` and not checking `stop_reason`: answers or JSON are cut off mid-way, and the
  symptom is parse errors that look like model mistakes.
- Using tiktoken to budget Claude prompts: counts differ, and requests near the context limit fail; use
  `count_tokens` instead.

## When to use
Use the Anthropic SDK whenever Claude is a primary model in your product and you want its features at
full fidelity: tool-using agents, long cached system prompts or documents, citations, batch evaluation,
or reasoning controls. Wrap it behind your own narrow interface so tests can fake it. Use the Bedrock or
Vertex client classes when the customer's contract or data residency requires those platforms, keeping
the same calling code. Choose LiteLLM or a gateway only when multi-provider routing is a real
requirement, and check that the features you depend on survive the translation. Measure cache read
ratio, stop-reason distribution, tool-loop iterations per task and error rates by class. In interviews,
expect to walk through the tool loop and explain why a cache hit rate is zero.

## Role lens
- **Engineer:** Implements the tool loop with correct block handling, streams long outputs, lays out
  prompts so caching hits, and logs request IDs and usage for every call.
- **Architect:** Decides provider abstraction boundaries, which platform (first-party, Bedrock, Vertex)
  each workload uses, and where batches replace synchronous calls to cut cost.
- **FDE:** Integrates Claude through the customer's cloud account on Bedrock or Vertex, and debugs
  auth, region and quota differences while keeping application code unchanged.

## Check yourself
**Q:** A team's Claude agent has a 6,000-token system prompt and tool list, and they enabled prompt
caching, yet `cache_read_input_tokens` is always zero. Walk through how you would diagnose it.
**A:** Caching is a prefix match over tools, then system, then messages, so anything that varies early in
that order breaks every hit. I would diff two consecutive request bodies byte for byte: typical culprits
are a timestamp or user name interpolated into the system prompt, tools built from an unordered set so
their order changes, or JSON serialised without sorted keys. I would check the breakpoint is placed after
the stable content and that the prefix exceeds the model's minimum cacheable length. I would confirm
requests arrive within the cache lifetime and use the same model, since caches are model-scoped. Then I
would move volatile data after the last breakpoint and watch the read counter rise.

## Quiz
### Q: After a response with `stop_reason == "tool_use"`, what must the next request contain?
- [ ] Only the tool result text appended to the system prompt
- [ ] A new user message summarising the tool call
- [x] The assistant's full content blocks, then a user message with a `tool_result` block per `tool_use` id
- [ ] A request with `tool_choice` reset so the model cannot call tools again
> The API validates that each `tool_result` matches a `tool_use` in the preceding assistant turn. Echoing
> the full content and returning every result in one user message keeps history consistent.

### Q: Why might a correctly marked cache breakpoint still produce no cache reads?
- [ ] Because caching only works for streaming requests
- [x] Because something earlier in the prefix changes between requests, such as a timestamp in the system prompt
- [ ] Because cache reads are reported only in the billing dashboard
- [ ] Because caching requires the Batches API
> Caching matches the exact prefix up to the breakpoint. Any byte that varies before it, in tools or
> system, creates a new prefix, so every request writes a cache entry and none read one.

### Q: Why is `max_tokens` a required parameter, and what should code do with it?
- [ ] It sets the input context limit; code should truncate prompts to fit it
- [ ] It controls billing tier; higher values cost more per token
- [x] It caps output length explicitly; code must check for `stop_reason == "max_tokens"` as truncation
- [ ] It limits the number of tool calls per turn
> Requiring an output ceiling makes cost and latency bounds explicit. When the model hits it, the response
> is incomplete, so treating that stop reason as success produces cut-off answers and broken JSON.
