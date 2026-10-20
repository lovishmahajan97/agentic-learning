---
id: openai-anthropic-node-sdks
module: node-ts-for-ai
name: OpenAI / Anthropic Node SDKs
kind: tool
difficulty: core
est_minutes: 16
related:
  - node-ts-for-ai/api-clients-with-retries-and-circuit-breakers
  - node-ts-for-ai/abortcontroller-and-cancellation
  - node-ts-for-ai/runtime-schema-validation
  - node-ts-for-ai/vercel-ai-sdk
  - python-for-ai/openai-sdk
  - python-for-ai/anthropic-sdk
  - prompting/function-tool-calling
  - prompting/prompt-caching
sources:
  - Official docs: OpenAI Node SDK (openai)
  - Official docs: Anthropic TypeScript SDK (@anthropic-ai/sdk)
---

## TL;DR
`openai` and `@anthropic-ai/sdk` are typed, generated clients over each provider's REST API with the
same bones: a client that reads the key from the environment, resource methods that return typed
objects, automatic retries with backoff on transient failures, millisecond timeouts, `AbortSignal`
support, streaming as async iterables, and typed error classes. They differ where the APIs differ:
message and tool-call shapes, how tool results are sent back, explicit versus automatic prompt
caching. Learn the shared mechanics once, then the two wire shapes.

## Why it exists
Calling a model is one HTTPS POST, and the temptation is to write it with `fetch`. That works for a
demo and fails in production in predictable ways. The provider returns 429 or 529 under load and the
hand-rolled call either gives up or retries immediately and makes the overload worse. A streamed
response arrives as server-sent events with several event types that must be parsed, re-assembled
into content blocks and matched to a stop reason; a hand-written parser handles the text case and
silently drops the tool-call case. A tool call arrives with a JSON schema contract that TypeScript
cannot see, so arguments are cast rather than checked. Timeouts, request IDs for support tickets,
rate-limit headers, pagination of file and batch endpoints and the exact error taxonomy are all left
to each caller to rediscover.

The SDKs exist to make those behaviours uniform and typed. Both are generated from the provider's API
specification, which is *why* every request and response has a TypeScript type that matches the
wire exactly and why the type changes when the API does. Both wrap the transport with the same
production defaults: retries on connection errors and on 408, 409, 429 and 5xx responses with
exponential backoff that honours `retry-after`, a default timeout of several minutes, and per-request
overrides. The naive alternative, a thin wrapper of your own, ends up re-implementing a worse version
of exactly this. The reason to understand the SDKs rather than just call them is that their defaults
interact with yours: an outer retry loop around the SDK's inner retries multiplies attempts, a proxy
timeout shorter than the SDK's makes the SDK's retries invisible, and a stream you forget to abort on
client disconnect keeps billing.

## How it works
### Shared client mechanics
`new OpenAI()` reads `OPENAI_API_KEY`; `new Anthropic()` reads `ANTHROPIC_API_KEY`. Both accept
`apiKey`, `baseURL`, `timeout` (milliseconds, unlike the Python SDKs' seconds), `maxRetries` (default
2), `defaultHeaders` and a custom `fetch`, and both run on Node 18+, Bun, Deno and edge runtimes
because they use the global `fetch`. Every method takes an optional second argument of request
options: `{ timeout, maxRetries, signal, headers }`. The `signal` is an ordinary `AbortSignal`, so the
cancellation lesson applies unchanged: aborting rejects the promise with an abort error and closes the
socket, which stops generation and billing. Retries are counted per request, and a timeout is retried,
so the worst-case wall clock is roughly `timeout × (maxRetries + 1)`; size proxy and handler timeouts
from that number, not from `timeout` alone.

Both expose typed errors under the default export: `APIError` with a numeric `status`, and subclasses
such as `BadRequestError` (400), `AuthenticationError` (401), `RateLimitError` (429),
`InternalServerError` (5xx), `APIConnectionError` and `APIConnectionTimeoutError`. Check with
`instanceof` from most specific to least, never by matching message text. Each response carries the
provider's request ID (`_request_id` on returned objects) for support tickets, and `.withResponse()`
returns `{ data, response }` so you can read rate-limit headers from the raw `Response`.

### Requests and responses
OpenAI has two chat surfaces. Chat Completions (`client.chat.completions.create`) takes `messages`
with `system`, `user`, `assistant` and `tool` roles and returns `choices[0].message` with `content`
and optional `tool_calls`; the newer Responses API (`client.responses.create`) takes `input` and
returns a list of typed `output` items with `output_text` as a convenience, and can optionally keep
conversation state server-side. Anthropic has one: `client.messages.create` takes a top-level `system`
string or blocks, a `messages` array of `user` / `assistant` turns, a required `max_tokens`, and returns
`content` as an array of blocks (`text`, `tool_use`, `thinking`) with a `stop_reason`
(`end_turn`, `tool_use`, `max_tokens`, `refusal`). The block array is the structural difference to
internalise: Anthropic responses are always lists, so code narrows on `block.type`, whereas Chat
Completions responses are a single message with optional fields. Usage is `prompt_tokens` /
`completion_tokens` on one side and `input_tokens` / `output_tokens` (plus cache read and creation
counts) on the other; normalise early if you log cost.

### Streaming
Both stream as async iterables. OpenAI returns a stream when `stream: true` is set: Chat Completions
yields chunks whose `choices[0].delta.content` you concatenate, with usage in a final chunk when
`stream_options: { include_usage: true }` is set; the Responses API yields named events such as
`response.output_text.delta`. Anthropic's `client.messages.stream()` returns a helper that is both an
iterable of raw events (`content_block_start`, `content_block_delta` with `text_delta` or
`input_json_delta`, `message_delta`, `message_stop`) and an emitter with `.on("text", …)`, and whose
`.finalMessage()` resolves the complete, typed `Message` once the stream ends, so one call feeds the
user and your logs. The helper exists *because* tool-call arguments arrive as JSON fragments across many
events; accumulating them correctly is exactly the code you do not want to write per service.

### Tools and structured output: the two wire shapes
OpenAI Chat Completions defines a tool as `{ type: "function", function: { name, description,
parameters, strict } }`; the model answers with `tool_calls[]`, each carrying an `id` and
`function.arguments` as a JSON *string* you must `JSON.parse`, and the result goes back as a message
with `role: "tool"` and `tool_call_id`. Anthropic defines `{ name, description, input_schema }`; the
model answers with `tool_use` content blocks whose `input` is already an object, and results go back
inside a `user` message as `tool_result` blocks keyed by `tool_use_id`, all results for one turn in one
message. Both SDKs ship Zod helpers that derive the schema and validate the arguments: OpenAI's
`zodFunction` and `zodResponseFormat` from `openai/helpers/zod` with a `parse` method that returns
`message.parsed`, and Anthropic's `betaZodTool` with `client.beta.messages.toolRunner`, which also runs
the call-tool-append-repeat loop for you. Structured output is `response_format` with a JSON schema
and `strict: true` on OpenAI, and a typed output configuration on Anthropic; in both cases keep your
own `safeParse` on the result, as the schema-validation lesson argues.

### Caching, batching, platforms
Anthropic prompt caching is explicit: place `cache_control: { type: "ephemeral" }` on the last stable
block (system prompt, tool list) and verify with `usage.cache_read_input_tokens`; any byte change
before the breakpoint invalidates it. OpenAI caches long, repeated prefixes automatically and reports
it in usage details, so the lever there is prompt ordering rather than annotations. Both offer a
batch endpoint for offline work at a discount and a files API; Anthropic adds `messages.countTokens`,
while OpenAI token estimates come from a tokenizer library. For cloud platforms, OpenAI ships an
`AzureOpenAI` client, and Anthropic ships `@anthropic-ai/bedrock-sdk` and `@anthropic-ai/vertex-sdk`
with the same `messages` surface behind platform credentials.

## Example
One narrow `ModelClient` interface with two adapters. Each streams tokens to stdout, returns the full
text with normalised usage, relies on the SDK's retries rather than its own, and accepts an
`AbortSignal` so a disconnected client stops generation. `isRetryable` is what an outer circuit breaker
would consult, using the typed errors instead of message text.

```ts
import Anthropic from "@anthropic-ai/sdk";
import OpenAI from "openai";

export interface Completion { text: string; inputTokens: number; outputTokens: number; requestId?: string }
export interface ModelClient { complete(system: string, user: string, signal?: AbortSignal): Promise<Completion> }

export function anthropicClient(model = "claude-opus-5-5"): ModelClient {
  const client = new Anthropic({ maxRetries: 3, timeout: 60_000 });      // ms; retries 408/409/429/5xx
  return {
    async complete(system, user, signal) {
      const stream = client.messages.stream(
        { model, max_tokens: 2048, system, messages: [{ role: "user", content: user }] },
        { signal },
      );
      stream.on("text", (delta) => process.stdout.write(delta));
      const msg = await stream.finalMessage();                             // typed Message, after the stream
      const text = msg.content.flatMap((b) => (b.type === "text" ? [b.text] : [])).join("");
      return { text, inputTokens: msg.usage.input_tokens, outputTokens: msg.usage.output_tokens,
               requestId: msg._request_id ?? undefined };
    },
  };
}

export function openaiClient(model = process.env.OPENAI_MODEL ?? "set-a-current-model-id"): ModelClient {
  const client = new OpenAI({ maxRetries: 3, timeout: 60_000 });
  return {
    async complete(system, user, signal) {
      const stream = await client.chat.completions.create(
        { model, stream: true, stream_options: { include_usage: true },
          messages: [{ role: "system", content: system }, { role: "user", content: user }] },
        { signal },
      );
      let text = "", usage: OpenAI.CompletionUsage | undefined;
      for await (const chunk of stream) {
        const delta = chunk.choices[0]?.delta?.content ?? "";
        process.stdout.write(delta); text += delta;
        if (chunk.usage) usage = chunk.usage;                               // arrives in the final chunk
      }
      return { text, inputTokens: usage?.prompt_tokens ?? 0, outputTokens: usage?.completion_tokens ?? 0 };
    },
  };
}

export function isRetryable(err: unknown): boolean {
  const e = err instanceof Anthropic.APIError || err instanceof OpenAI.APIError ? err : undefined;
  return e !== undefined && (e.status === 429 || (e.status ?? 0) >= 500);
}
```

## Trade-offs
- Built-in retries make transient failures invisible, which is what you want until you wrap the call in
  your own retry or a queue with redelivery; then attempts multiply and you should set `maxRetries: 0`
  in one of the layers.
- Generated types track the API exactly, so upgrades surface breaking changes at compile time; the
  flip side is that a minor SDK bump can rename a type and force a same-day fix across the codebase.
- The streaming helpers accumulate tool arguments and expose the final message for free, but they
  buffer the whole response in memory; for very long outputs you may prefer the raw event iterable.
- Explicit cache breakpoints (Anthropic) give control and a verifiable hit counter at the cost of
  discipline about prompt ordering; automatic caching (OpenAI) needs no annotations but gives less
  control over what is cached.
- Using a provider's Zod helper couples your tool definitions to that SDK; a shared schema module
  converted per provider keeps the option of a second provider open.

## Pitfalls
- Matching on `error.message` strings instead of `instanceof` and `status`; the symptom is retry logic
  that stops working after an SDK or API wording change.
- Passing the timeout in seconds as in Python; the symptom is every request timing out after a few
  milliseconds and retrying three times before failing.
- Forgetting the `signal` on streaming calls; the symptom is completions still running, and billing,
  long after the HTTP client disconnected.
- Treating `function.arguments` as an object on OpenAI, or `input` as a string on Anthropic; the
  symptom is a `TypeError` or `[object Object]` inside a tool.
- Sending Anthropic tool results across several `user` messages or in the wrong role; the symptom is
  a 400 about tool_use IDs without matching tool_result blocks.
- Reading `response.content[0].text` on Anthropic without narrowing; the symptom is a compile error at
  best, and at worst a runtime `undefined` when the first block is `tool_use` or `thinking`.
- Omitting `max_tokens` on Anthropic or setting it too low; the symptom is a 400 on the former and
  truncated JSON with `stop_reason: "max_tokens"` on the latter.
- Verifying caching by vibes; the symptom is a system prompt with a timestamp in it, a cache hit rate
  of zero and a bill that never drops.

## When to use
Use the native SDK whenever you commit to a provider and want its full surface: streaming helpers,
tool runners, batch and files endpoints, explicit caching, platform clients for Azure, Bedrock or
Vertex. Wrap it behind a narrow interface of your own so tests can inject a fake and a second provider
can be added later, and keep the SDK's retries while deciding explicitly where your own retries, queue
redelivery and circuit breaker sit. Reach for a unification layer such as the Vercel AI SDK when
multi-provider or browser streaming is the primary requirement. Measure tokens and cost per request
from `usage`, p95 time to first token, retry counts, and cache hit rate. Interviews probe the tool-call
round trip in both shapes, what the SDK retries and what it does not, and how a cancelled request
actually stops.

## Role lens
- **Engineer:** Configures clients with explicit timeouts and retries, writes adapters that normalise
  usage and errors, narrows content blocks correctly, and propagates `AbortSignal` into every call.
- **Architect:** Decides the provider boundary (native SDK behind an interface versus a unification
  layer), where retries and breakers live across layers, and how cost telemetry is normalised.
- **FDE:** Reads a customer's integration for the classic faults (string-matched errors, missing
  signals, doubled retries, unverified caching) and converts it to typed, cancellable, observable calls.

## Check yourself
**Q:** A service wraps `client.messages.create` in its own retry loop of three attempts with backoff,
and the SDK keeps its default retries. Under a provider outage, how many requests can one user action
generate, how long can it take, and what would you change?
**A:** The SDK retries twice by default, so each outer attempt can send three requests; three outer
attempts give up to nine requests, each waiting up to the SDK timeout plus backoff, so one click can
hold a connection for many minutes and amplify load on an already failing provider. The fix is to pick
one layer. Either set `maxRetries: 0` and own retries, jitter and a circuit breaker in your client
wrapper, or keep the SDK's retries and remove the outer loop, treating a thrown `RateLimitError` or
`InternalServerError` as a signal to shed load or fail fast. Whichever you choose, set the handler and
proxy timeouts from `timeout × (retries + 1)`, pass an `AbortSignal` so a disconnected user stops the
chain, and record the request ID from the response for the inevitable support ticket.

## Quiz
### Q: What is the key structural difference between a tool call in OpenAI Chat Completions and in Anthropic Messages?
- [ ] OpenAI requires a JSON schema while Anthropic infers parameters from the description
- [x] OpenAI returns `tool_calls` with `arguments` as a JSON string and takes results as `role: "tool"` messages; Anthropic returns `tool_use` content blocks with a parsed `input` and takes results as `tool_result` blocks in a user message
- [ ] Anthropic executes tools server-side, so no result needs to be returned
- [ ] OpenAI allows only one tool call per response while Anthropic allows many
> Both require a JSON schema and both can return several calls in one turn. The difference is the
> envelope: a string to parse and a `tool` role on one side, a parsed object inside a content block and a
> `tool_result` block on the other. Neither executes your custom tool for you.

### Q: The TypeScript SDKs take `timeout: 30`. What happens, and why?
- [ ] Requests time out after 30 seconds, matching the Python SDK
- [x] Requests time out after 30 milliseconds and are retried `maxRetries` times, because the TypeScript SDKs take milliseconds
- [ ] The value is ignored because timeouts are set per request only
- [ ] The SDK throws at construction because the minimum timeout is one second
> Unit mismatch is a common port-from-Python bug. The TypeScript clients take milliseconds, and because
> timeouts count as retryable, the 30 ms failure repeats up to the retry limit before the typed
> `APIConnectionTimeoutError` surfaces.

### Q: Why should error handling use `instanceof OpenAI.RateLimitError` rather than inspecting `error.message`?
- [ ] Because `error.message` is always empty in the SDKs
- [ ] Because `instanceof` is faster than string comparison
- [x] Because the typed classes map to HTTP status and error type stably, while message text can change with any SDK or API release
- [ ] Because `error.message` contains the API key and must not be read
> The SDKs expose a class per status (and a `status` field) precisely so callers can branch on a stable
> contract. Message wording is informational and may change; code that depends on it breaks silently
> when it does.
