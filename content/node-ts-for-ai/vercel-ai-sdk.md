---
id: vercel-ai-sdk
module: node-ts-for-ai
name: Vercel AI SDK
kind: tool
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - node-ts-for-ai/openai-anthropic-node-sdks
  - node-ts-for-ai/streams-and-backpressure
  - node-ts-for-ai/runtime-schema-validation
  - node-ts-for-ai/abortcontroller-and-cancellation
  - serving-inference/streaming
  - prompting/function-tool-calling
  - prompting/structured-outputs-json-mode
  - agents/frameworks
sources:
  - Official docs: Vercel AI SDK
  - Official docs: Zod
---

## TL;DR
The Vercel AI SDK (`ai` on npm) is a provider-agnostic TypeScript layer with two halves: a core API
(`generateText`, `streamText`, `generateObject`, `tool`, `embed`) that maps one call shape onto OpenAI,
Anthropic, Google and others through small provider packages, and a UI half (`useChat`) that streams
text, tool calls and structured data to the browser over a defined protocol. Swapping a model becomes
one line and browser streaming stops being a hand-rolled parser; the price is that provider-specific
features sit behind an escape hatch.

## Why it exists
Two kinds of plumbing dominate the first week of any TypeScript AI product. The first is provider
shape: OpenAI returns `choices[0].message.content` and tool calls with JSON-string arguments, Anthropic
returns an array of content blocks with parsed `input`, Google differs again, and each streams a
different event vocabulary. A team that wants to compare models, keep a fallback provider, or let a
customer bring their own key ends up writing an adapter per provider and a normalised stream type of
its own. The second is the browser: a chat UI needs tokens as they arrive, tool-call progress, partial
structured objects, stop buttons and message history, and the naive `fetch` plus `TextDecoder` loop
grows into a protocol with no name that only one engineer understands.

Calling the native SDKs directly is the right choice when you use one provider and need its full
surface, but it does not solve either problem; it is the thing the adapter would wrap. Picking a
heavyweight agent framework solves them at the cost of owning its abstractions for everything else.
The AI SDK is deliberately narrower: it standardises the model call and the UI stream and stops
there. Its design bet is that the unified call can cover the large common core (messages, tools,
structured output, streaming, usage) and that the provider-specific long tail can be reached through
typed provider options rather than by abandoning the layer. Whether that bet holds for your use case is
exactly the decision this lesson is about.

## How it works
### The provider abstraction
Every provider package (`@ai-sdk/openai`, `@ai-sdk/anthropic`, `@ai-sdk/google`, plus community ones)
implements the SDK's language-model specification: a small interface with a `doGenerate` and a
`doStream` method that take the unified prompt (system, messages with typed parts, tool definitions,
settings) and return either a result or a stream of unified parts: text deltas, reasoning deltas,
tool-call deltas, tool results, a finish part with `finishReason` and `usage`. The core functions are
written once against that interface, which is *why* `anthropic("...")` and `openai("...")` are
interchangeable at the call site: the mapping from the unified shape to each provider's wire format and
back lives inside the provider package, not in your code. Provider-only features (Anthropic's
`cache_control` breakpoints or thinking settings, OpenAI's reasoning effort, a Google safety setting)
are passed through `providerOptions` keyed by provider, so you can reach them without leaving the
unified call; they simply do nothing on a provider that lacks them.

### Core functions
- `generateText` and `streamText` take `model`, `system`, `messages` (or `prompt`), optional `tools`,
  and settings such as `temperature`, `maxOutputTokens` and `abortSignal`. The streaming variant returns
  an object whose `textStream` and `fullStream` are async iterables and whose `text`, `usage` and
  `steps` are promises that resolve when the stream finishes, so one call serves both the response
  body and your logging.
- `tool({ description, inputSchema, execute })` defines a tool from a Zod (or JSON) schema; the schema
  becomes the provider's tool definition and the model's arguments are validated against it before
  `execute` runs, which is the same contract-as-value idea as the schema-validation lesson. When
  `execute` is present the SDK runs the tool and records its result, and if you set `stopWhen` (for
  example `stepCountIs(n)`) it appends the result and calls the model again, looping until the condition
  holds or the model stops calling tools. The default for `generateText` / `streamText` is a single
  step (`stepCountIs(1)`), so without `stopWhen` the tool runs but the model never sees its result and
  never writes the final answer. Omit `execute` to receive the tool call and run it yourself, for
  approvals or client-side tools.
- `generateObject` and `streamObject` take a Zod schema and return a typed, validated object (or a
  stream of partial objects). Under the hood the provider package picks its best mechanism, a JSON
  schema response format, a forced tool call, or an instruction, and the SDK validates the result
  against the schema, so a non-conforming answer surfaces as an error (`NoObjectGeneratedError`) rather
  than a mis-typed value.
- `embed` and `embedMany` do the same unification for embedding models.

### The UI half and its protocol
`useChat` on the client keeps the message list, sends it to your route on submit, and appends parts as
they stream: text, tool invocations with their state, and custom data. The server route returns the
result's response helper, which encodes the unified stream parts into the SDK's UI message stream
(in v5, JSON parts sent as server-sent events and marked by a protocol-version header; v4 used a
line-prefixed "data stream" format), consumed by the hook; a plain text alternative exists when you
only need tokens. This is the piece that lets a tool call started on the server render
as a progress card in the browser without you writing the protocol. The client and server packages
must agree on the protocol version, which is why the two sides are upgraded together.

### Middleware and telemetry
`wrapLanguageModel` wraps any model with middleware that can transform the prompt, intercept the
result or short-circuit the call: logging, caching, guardrails and default settings live here instead
of at every call site. OpenTelemetry support, switched on per call with the `experimental_telemetry`
option, emits spans for each call and step with token usage, which plugs into the same tracing
pipeline as the rest of the service.

### Runtime fit
The core runs anywhere `fetch` exists: Node, Bun, Deno, edge runtimes, and inside Express, Fastify,
Hono or Next.js route handlers. Nothing requires Vercel's platform; the UI hooks require a framework
only on the client side. Note that names have shifted across major versions: in v5 tool `parameters`
became `inputSchema`, `maxTokens` became `maxOutputTokens`, `maxSteps` became `stopWhen`, and
`convertToCoreMessages` became `convertToModelMessages`. v6 then deprecated `generateObject` /
`streamObject` in favour of `generateText` / `streamText` with an `output` setting and added an agent
abstraction, and its migration guide lists further signature changes. Check the version you install
against its docs rather than copying old snippets.

## Example
A route handler that streams a tool-using answer to a `useChat` client, and a separate function that
classifies text into a typed object. The model line is the only provider-specific code; written
against AI SDK 5 naming.

```ts
import { streamText, generateObject, tool, stepCountIs, convertToModelMessages } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { z } from "zod";
import { db } from "./db";

const model = anthropic(process.env.MODEL_ID ?? "claude-opus-5-5");   // swap for openai("...") to compare

export async function chatHandler(req: Request): Promise<Response> {
  const { messages } = await req.json();                             // UI messages from useChat
  const result = streamText({
    model,
    system: "You are a support assistant. Look orders up with the tool; never guess order data.",
    messages: convertToModelMessages(messages),
    tools: {
      lookupOrder: tool({
        description: "Fetch an order by its id",
        inputSchema: z.object({ orderId: z.string().regex(/^ord_\w+$/) }),
        execute: async ({ orderId }) => (await db.orders.find(orderId)) ?? { error: "not found" },
      }),
    },
    stopWhen: stepCountIs(4),          // at most 4 model/tool rounds per request
    abortSignal: req.signal,           // client disconnect stops generation and further billing
    onFinish: ({ totalUsage, steps }) =>   // `usage` here is the last step only; totalUsage sums all
      console.log({ totalUsage, steps: steps.length, toolCalls: steps.flatMap((s) => s.toolCalls).length }),
  });
  return result.toUIMessageStreamResponse();   // tokens, tool state and data to the browser
}

const Intent = z.object({
  intent: z.enum(["refund", "shipping", "other"]),
  confidence: z.number().min(0).max(1),
});

export async function classify(text: string) {
  const { object } = await generateObject({ model, schema: Intent, prompt: `Classify:\n${text}` });
  return object;                       // typed { intent, confidence }, validated against Intent
}
```

## Trade-offs
- One call shape across providers makes comparisons and fallbacks cheap, but the newest
  provider-specific feature usually arrives behind `providerOptions` or not at all for a while;
  if your product is built on such a feature, the native SDK is the better primary interface.
- The built-in tool loop and `generateObject` remove boilerplate, yet they decide the mechanism
  (forced tool versus JSON mode) for you; when you need deterministic control over every request body,
  for caching or auditing, drop to the native SDK for that path.
- The UI protocol gives streaming tool and data parts for free, at the cost of coupling client and
  server package versions and of a wire format you do not own; a plain text stream is simpler when
  the client is not yours.
- Middleware centralises logging, caching and guardrails, but a stack of middlewares is a second place
  behaviour hides; keep it short and visible.
- Major versions have renamed core options, which is cheap to follow in a small codebase and painful in
  a large one; pin the version and schedule upgrades rather than floating.

## Pitfalls
- Assuming `generateObject` removes the need to handle failure; the symptom is an unhandled schema
  validation error in production when the model returns a value outside the enum.
- Setting a generous `stopWhen` limit, or using an agent abstraction with a high default, without a
  budget; the symptom is a request that makes a dozen tool calls, times out at the proxy and bills for
  every round. The opposite mistake, omitting `stopWhen`, shows up as a tool that runs but a reply that
  ends with no answer text.
- Forgetting to pass `abortSignal` from the request; the symptom is server logs showing completions
  finishing minutes after users closed the tab.
- Upgrading `ai` on the server without the client package; the symptom is a UI that renders nothing
  or shows raw protocol lines because the stream format changed.
- Reaching for `providerOptions` without checking the provider; the symptom is a cache or thinking
  setting silently ignored after switching models, with cost and latency changing unexpectedly.
- Treating the model string as configuration-free; the symptom is a provider swap that passes tests
  with the fake and fails on tool schemas the new provider rejects.

## When to use
Use it when you want to compare or switch providers, when a browser UI needs streamed text, tool state
or partial objects, or when a small team wants typed tools and structured output without owning an
agent framework. Prefer the native provider SDK when you commit to one provider and lean on its
specific features (explicit prompt caching, batch APIs, server-side tools), when you need byte-level
control over requests, or when you are writing a library others will consume. Measure the same things
as with a native client: tokens and cost per request, p95 time to first token, tool-call counts per
conversation, and schema validation failure rate. In interviews the useful answer explains what the
abstraction normalises, what it cannot, and when you would step outside it.

## Role lens
- **Engineer:** Writes route handlers with `streamText`, defines tools with Zod, sets step limits and
  abort signals, and uses `providerOptions` for caching or reasoning settings per provider.
- **Architect:** Decides whether the unified layer or a native SDK is the primary interface, how
  provider switching is exercised in CI, and where middleware for logging and guardrails sits.
- **FDE:** Uses the provider swap to run a customer's prompts across models with one codebase and
  shows them the cost, latency and quality differences before they commit to a vendor.

## Check yourself
**Q:** A team built their product on the AI SDK and now wants Anthropic prompt caching on a 40k-token
system prompt and a batch job that classifies a million records overnight. What do you advise?
**A:** Split the two. Prompt caching is reachable without leaving the SDK: pass the cache breakpoint
through `providerOptions` for the Anthropic provider on the system message or tool definitions, then
verify by reading cache-read token counts in `usage` or provider metadata; if the breakpoint is not
exposed in their version, that single path can use the native SDK while the rest stays unified. The
batch job is a different story: a million offline classifications belong on the provider's batch API,
which trades latency for a lower price and is not something the unified call models. Write that job
directly against the native SDK, reuse the same Zod schema for validation, and keep the interactive
paths on the AI SDK. The rule is: unified layer for the common interactive surface, native SDK for
provider-specific economics.

## Quiz
### Q: What makes `anthropic("...")` and `openai("...")` interchangeable in a `streamText` call?
- [ ] The SDK translates prompts into a provider-neutral language the models all understand
- [x] Each provider package implements the same language-model specification, mapping the unified prompt and stream parts to its own wire format
- [ ] Both providers expose an identical REST API, so no mapping is needed
- [ ] The SDK proxies every request through Vercel's servers, which normalise the response
> The core functions are written against one interface; the provider packages do the translation in
> both directions. The provider APIs differ substantially, and nothing is routed through Vercel; the
> package runs wherever `fetch` runs.

### Q: When should a team step outside the AI SDK and call a provider's native SDK directly?
- [ ] Whenever they stream responses to a browser
- [ ] Whenever they use tools, since the SDK cannot run a tool loop
- [x] When they depend on provider-specific features or endpoints the unified layer does not model, such as a batch API or byte-level request control
- [ ] Never; the SDK exposes every provider feature through its core functions
> Streaming and tool loops are the SDK's core strengths. Its limits are the features it chose not to
> unify; `providerOptions` covers many, but batch processing, exact request bodies and the newest
> provider capabilities are reasons to use the native client for that path.

### Q: What does `generateObject` with a Zod schema do that `JSON.parse` on the model's text does not?
- [ ] It guarantees the model cannot produce an invalid answer, so no error handling is needed
- [ ] It caches the result so repeated prompts are free
- [x] It sends the schema to the provider using its structured-output mechanism and validates the result, so the returned value is typed and conforming or an error is raised
- [ ] It streams the object to the client automatically
> `JSON.parse` gives you `unknown` from whatever the model wrote. `generateObject` constrains the
> generation where the provider supports it and validates against the schema, so a non-conforming
> value becomes an explicit error you must handle rather than a silently wrong object. It does not guarantee success, cache results or stream to the client.
