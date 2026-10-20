---
id: opentelemetry-instrumentation
module: node-ts-for-ai
name: OpenTelemetry instrumentation
kind: practice
difficulty: core
est_minutes: 17
status: reviewed
related:
  - observability/opentelemetry
  - observability/tracing
  - observability/spans
  - observability/distributed-tracing-across-services
  - observability/token-cost-tracking
  - fastapi-for-ai/opentelemetry-instrumentation
  - node-ts-for-ai/bullmq-and-durable-jobs
  - observability/langfuse
sources:
  - Official docs: OpenTelemetry JavaScript
  - OpenTelemetry Semantic Conventions — Generative AI
  - Official docs: Anthropic TypeScript SDK (OpenTelemetry tracing)
---

## TL;DR
OpenTelemetry (OTel) is the vendor-neutral standard for traces, metrics and logs. In a Node AI service
you install the SDK once at process start, let auto-instrumentation wrap HTTP, `fetch`, Postgres, Redis
and your web framework, and add your own spans around agent steps carrying tenant, run id and prompt
version. Context follows `await` through `AsyncLocalStorage` and crosses services in the W3C
`traceparent` header, so one request's path from API to queue to LLM to database becomes a single
queryable tree.

## Why it exists
An agent request in Node fans out: an HTTP handler calls a planner model, which calls three tools, two
of which hit Postgres and one of which enqueues a BullMQ job that calls an embedding API on another
replica. When that request takes 40 s or costs 60,000 tokens, logs from six processes tell you *that*
it happened but not *why*, because nothing ties the lines together and nothing records durations as a
tree. Hand-rolled solutions, a `requestId` threaded through every function and `console.time` pairs,
break at the first `await` you forgot, do not cross the queue, and produce data no tool can draw as a
waterfall.

Node makes the problem sharper than threaded runtimes do: there is no thread to hang context on, so
"which request is this log line from" must travel along the async continuation chain. OTel's Node SDK
solves that with `AsyncLocalStorage`, propagates context across processes with standard headers, and
emits the result in a format every backend (Jaeger, Tempo, Honeycomb, Datadog, Langfuse and others via
OTLP) accepts. The vendor-neutral part matters *because* the backend you pick in year one is rarely the
one you have in year three, and because enterprise customers want your spans in *their* collector. The
observability module covers the concepts; this lesson is the Node mechanics.

## How it works
### API, SDK and the split between them
Your code calls the API (`@opentelemetry/api`): `trace.getTracer(name).startActiveSpan(...)`,
`metrics.getMeter(...)`. The SDK (`@opentelemetry/sdk-node`, or `NodeTracerProvider` directly) is
configured once and decides what happens: sampling, batching, exporting. Libraries depend only on the
API *because* then a library can be instrumented without forcing an SDK or an exporter on its users;
with no SDK registered the API calls are no-ops.

### Startup order and auto-instrumentation
Auto-instrumentation works by patching modules (`http`, `undici`/`fetch`, `pg`, `ioredis`, `express`,
`fastify`, `pino` and others) when they are loaded. It must therefore be installed *before* those
modules are imported, which is why the SDK lives in its own `instrumentation.ts` loaded with
`node --require` (CommonJS) or `node --import` (ESM), or via `NODE_OPTIONS`. ESM needs one more piece:
`import` statements bypass the CommonJS `require` hook, so the file must also register OTel's ESM
loader hook (`module.register("@opentelemetry/instrumentation/hook.mjs", ...)`), which the zero-code
entry point `@opentelemetry/auto-instrumentations-node/register` does for you. Importing it from
`app.ts` after your framework is a classic silent failure: no error, no spans.

### Spans, kinds and the tree
A span is a named, timed operation with a trace id (shared by the whole request), its own span id, a
parent span id, attributes, events (timestamped points such as "first token") and a status. The tree
is reconstructed by the backend from parent ids alone, which is why a missing parent link produces an
orphan root rather than an error. Each span has a kind: `SERVER` for handling an inbound request,
`CLIENT` for an outbound call, `PRODUCER` and `CONSUMER` for the two ends of a queue, `INTERNAL` for
in-process work such as an agent step. Kinds matter *because* backends derive service maps and
latency breakdowns from them: a `CLIENT` span with no matching `SERVER` child is time spent waiting on
something you do not instrument, such as a model provider.

### Context propagation
Within a process the SDK installs an `AsyncLocalStorage`-based context manager so the active span
follows `await`, promises, timers and callbacks. `AsyncLocalStorage` works by capturing the current
store whenever Node creates an async resource (a promise, a timer, a socket callback) and restoring it
when that resource's continuation runs, so "the active span" is whatever was active when the
continuation was *scheduled*. That rule explains where context gets lost: a library that collects
callbacks in its own queue and invokes them later from a different continuation (a hand-rolled
connection pool, a batching client) runs them in the wrong context, and an `EventEmitter` listener runs
in the context of whoever called `emit()`, not whoever registered it. The fix is to bind explicitly with
`context.bind(context.active(), fn)` (or `AsyncResource.bind`) at the point the callback is handed
over. Across HTTP it injects and extracts `traceparent` and
`tracestate` (W3C Trace Context); the `http` and `fetch` instrumentations do this for every outbound
and inbound request. Across a queue nothing is automatic unless the library supports it (BullMQ 5 has an opt-in `telemetry`
option, provided by the `bullmq-otel` package, that does this): otherwise serialise the
context into job data on the producer (`propagation.inject(context.active(), carrier)`) and restore it
in the worker (`propagation.extract(...)`) so the job's spans link to the originating request.

### Spans for model calls
The Anthropic TypeScript SDK emits its own span per request, `anthropic.messages.create` (kind
`CLIENT`), through whatever tracer provider the application registered, carrying
`gen_ai.provider.name`, `gen_ai.usage.input_tokens` (cache reads and writes included),
`gen_ai.usage.output_tokens`, the cache read and write counts, and `anthropic.request.id`; its tool
runner adds a parent span plus one span per tool it runs. The feature is in beta, so pin the SDK
version. Two rules follow. Wrap the call in a span of your own (`agent.plan`, `tool.search_docs`) with
app-level attributes (tenant, run id, prompt version) and the SDK span nests under it; the SDK then
sends its own span's `traceparent` to the API, so server-side spans (if API tracing is configured) join
the same trace. And if a third-party instrumentation already patches the SDK,
turn one of the two off (`openTelemetry: false` or `ANTHROPIC_OPEN_TELEMETRY=false`) or every request
appears twice. For providers whose SDK emits nothing, set the GenAI semantic-convention attributes
yourself (`gen_ai.provider.name`, the request model, `gen_ai.usage.*`); the conventions are still
marked experimental, so expect renames.

Prompt and completion text stay out of spans by default: the SDK's content mode is `metadata_only`, and
`content` mode exists *because* reading conversations from traces is useful but makes your tracing
backend a store of customer data under its own retention rules, with a per-span size limit (0.75 MB by
default) beyond which content is truncated. Treat that as an explicit opt-in, or send content to an
LLM-specific tool (Langfuse, Phoenix) built for it.

### Streaming
A streaming call's span ends when the stream is fully read or closed, so close a stream you abandon or
its span never ends. Spans you start while reading chunks are siblings of the SDK span, not children;
start one span of your own around both the call and the read loop, and add an event at the first token
so time-to-first-token is queryable.

### Metrics and logs
Use a histogram for model latency and a counter for tokens by model and tenant; these aggregate cheaply
where traces are sampled. Pino or Winston with the OTel log instrumentation gets `trace_id` and
`span_id` on every line, which is what lets you jump from a log to the trace.

### Export, sampling, cost
Spans go through a `BatchSpanProcessor` to an OTLP exporter (HTTP or gRPC), usually to an OpenTelemetry
Collector sidecar or gateway that fans out to backends, redacts, and does tail sampling. The batch
processor exists *because* exporting each span synchronously would put a network call on every
operation: ended spans go into an in-memory queue (2,048 spans by default) and are exported in batches
of up to 512 every 5 seconds or when a batch fills. When the exporter cannot keep up, the queue fills
and new spans are dropped, not blocked, which protects request latency at the cost of silently missing
data; tune `OTEL_BSP_MAX_QUEUE_SIZE` and watch the dropped count. Use the `SimpleSpanProcessor`
(export on end) only in tests. Head sampling
in the SDK (`parentbased_traceidratio` at, say, 10%) cuts cost; the `parentbased` part means a service
follows the sampled flag in the incoming `traceparent` and only applies its own ratio at the root,
*because* independent coin flips per service would produce traces with random holes. Head sampling
decides before anything has gone wrong, so it throws away 90% of the rare slow or failed traces along with the boring ones; tail
sampling in the collector keeps errors and slow traces *because* it decides after seeing the whole
trace. Configuration is environment-driven: `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`,
`OTEL_TRACES_SAMPLER`, `OTEL_RESOURCE_ATTRIBUTES`. On shutdown call `sdk.shutdown()` so the batch
processor flushes; otherwise the last spans of every deploy vanish.

## Example
`instrumentation.ts` configures the SDK and is loaded before the app with
`node --import ./instrumentation.js app.js` (for a CommonJS build, use `--require` and drop the two
`register` lines, which only an ES module can run). The second block shows an
application span around a model call; the Anthropic SDK's own `anthropic.messages.create` span, with
token usage, nests under it automatically because the SDK uses the registered provider.

```ts
// instrumentation.ts — loaded first, never imported by application code except for shutdown
import { register } from "node:module";
register("@opentelemetry/instrumentation/hook.mjs", import.meta.url);  // ESM only: lets patches reach `import`ed modules
import { NodeSDK } from "@opentelemetry/sdk-node";
import { getNodeAutoInstrumentations } from "@opentelemetry/auto-instrumentations-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";

export const sdk = new NodeSDK({
  traceExporter: new OTLPTraceExporter(),            // endpoint from OTEL_EXPORTER_OTLP_ENDPOINT
  instrumentations: [getNodeAutoInstrumentations({ "@opentelemetry/instrumentation-fs": { enabled: false } })],
});
sdk.start();                                          // registers the provider and the AsyncLocalStorage context manager

// llm.ts — your span carries app semantics; the SDK span carries gen_ai.* usage
import { trace, SpanStatusCode } from "@opentelemetry/api";
import Anthropic from "@anthropic-ai/sdk";
const tracer = trace.getTracer("agent-api");
const client = new Anthropic();                       // SDK tracing on by default, metadata only: no prompt text exported

export async function plan(tenant: string, runId: string, prompt: string) {
  return tracer.startActiveSpan("agent.plan", async (span) => {
    span.setAttributes({ "app.tenant": tenant, "app.run_id": runId, "app.prompt_version": "planner-v7" });
    try {
      const res = await client.messages.create(
        { model: process.env.MODEL ?? "claude-opus-5-5", max_tokens: 16000, messages: [{ role: "user", content: prompt }] },
        { openTelemetry: { conversationId: runId } },   // written to gen_ai.conversation.id on the SDK span
      );
      span.setAttribute("app.stop_reason", res.stop_reason ?? "unknown");
      return res.content.flatMap((b) => (b.type === "text" ? [b.text] : [])).join("");
    } catch (e: any) {
      span.recordException(e); span.setStatus({ code: SpanStatusCode.ERROR, message: e.message });
      throw e;
    } finally {
      span.end();                                     // always end, or the span never exports
    }
  });
}
```

## Trade-offs
- Auto-instrumentation gives HTTP, DB and Redis spans with zero code changes, but it patches modules and must load first; it also instruments noisy things (`fs`, DNS) that you will want to disable to control volume.
- Head sampling at the SDK is cheap and predictable, but drops rare slow or failed traces; tail sampling in a collector keeps them at the cost of running and sizing the collector.
- Rich span attributes (tenant, model, tokens, prompt version) make traces queryable for cost and quality questions, but attributes are billed per byte by most vendors, and prompt content in spans is a data-protection liability that needs an explicit decision.
- Linking queue jobs to their originating request through injected context gives end-to-end traces, but requires every producer and consumer to carry the carrier; one unlinked hop breaks the tree into orphans.
- LLM-specific observability tools (Langfuse, Phoenix, LangSmith) understand prompts, evals and sessions in ways generic tracing does not, while OTel is the common wire; sending OTel spans to such a tool via OTLP avoids instrumenting twice.

## Pitfalls
- SDK initialised after the framework or `pg` is imported: the symptom is a trace with only your manual spans, no HTTP or DB children, and no error anywhere.
- ESM app started with `--require`: the symptom is auto-instrumentation silently doing nothing because ESM modules bypass the CommonJS hook; use `--import` with the register entry.
- Spans never ended on an error path (missing `finally`), or a streaming response abandoned without closing it: the symptom is traces that look incomplete or never export, and memory growing with open spans.
- No `sdk.shutdown()` on SIGTERM: the symptom is the last few seconds of spans missing before every deploy, which is exactly when errors happen.
- Both the vendor SDK's built-in tracing and a third-party instrumentation of that SDK active: the symptom is every model call appearing twice in the trace with identical timing.
- Context lost across a callback queue or an `EventEmitter`: the symptom is DB or tool spans that attach to the wrong request's trace, or start new root traces, only under concurrency, because the callback ran in whichever context was active when it was invoked.
- Trace context not propagated into BullMQ jobs: the symptom is worker spans that form their own root traces, unlinked from the API request that caused them.

## When to use
Instrument every service that calls a model, from the first deployment; retrofitting traces during an
incident is the worst time. Use auto-instrumentation for transport and storage, the vendor SDK's spans
for model calls, manual spans for anything semantically meaningful (agent step, tool call, retrieval),
metrics for cost and latency aggregates, and logs with trace ids for detail. Reach for an LLM-specific
tool when you need prompt versioning, evals and human review, and feed it through OTLP. Measure spans
per request, export errors, dropped spans from the batch processor, and whether every error trace is
kept. Interviewers ask how context crosses `await` in Node, why auto-instrumentation must load first,
and what you would put on a model-call span.

## Role lens
- **Engineer:** Owns `instrumentation.ts`, wraps agent steps and tool calls in spans with tenant and run attributes, propagates context into jobs, and ensures spans end and flush on every path.
- **Architect:** Chooses the collector topology, sampling policy, attribute and content-mode rules, and whether an LLM observability product sits beside or behind the OTel pipeline.
- **FDE:** Points the exporter at the customer's collector, negotiates which attributes may leave their environment, and uses traces to show where latency and tokens go in their workflow.

## Check yourself
**Q:** Traces from your Node agent API show the HTTP server span and your manual spans, but no
Postgres or Redis spans, and worker-side spans appear as separate traces. What is wrong and how do you
fix both?
**A:** Missing `pg` and `ioredis` spans almost always mean the SDK was initialised after those modules
were loaded, so the patches never applied; the fix is a separate instrumentation file loaded via
`--require` or, for ESM, `--import` with the auto-instrumentation register hook, before any
application import. The worker traces are orphans because queue hops do not propagate context on their
own: on the producer, call `propagation.inject(context.active(), carrier)` and store the carrier in the
job data; in the worker, `propagation.extract` it and start the processing span inside that context so
it becomes a child (or a linked span) of the API request. Verify with one request and confirm a single
trace spans API, database, Redis and worker.

## Quiz
### Q: Why must the OpenTelemetry SDK be loaded before importing your web framework and database client?
- [ ] Because the SDK reserves port 4318 before the server starts
- [x] Auto-instrumentation patches modules when they are first loaded, so modules imported earlier are never wrapped
- [ ] Because spans need a root span created at process start
- [ ] Because the exporter needs the framework's config to resolve the endpoint
> Instrumentations hook the module loader; anything required before the hook is installed is the original, unpatched module, which is why the SDK lives in a file passed via `--require` or `--import`.

### Q: How does the active span follow an `await` in Node when there is no thread to attach it to?
- [ ] Spans are stored in a global variable that the SDK resets per request
- [ ] The event loop tags each callback with the span id
- [x] The SDK uses `AsyncLocalStorage` so context flows along the async continuation chain
- [ ] Each promise carries the span in its prototype
> OTel's Node context manager stores the active context in `AsyncLocalStorage`, which Node propagates through promises, timers and callbacks, so code after an `await` still sees the right parent span.

### Q: Which is the best default for recording model calls in spans?
- [ ] Store full prompt and completion text as attributes on every span
- [x] Record model, token usage, finish reason and tenant as attributes, and keep content out unless explicitly opted in
- [ ] Record only the HTTP status, since the auto-instrumented span already exists
- [ ] Skip spans for model calls and rely on provider dashboards
> Token and model attributes make cost and latency queryable at low volume; prompt text is large, sensitive and billed per byte, so it belongs behind an opt-in content mode or in a purpose-built LLM observability store.
