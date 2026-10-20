---
id: opentelemetry
module: observability
name: OpenTelemetry
kind: tool
difficulty: core
est_minutes: 16
status: crisp
related:
  - observability/tracing
  - observability/spans
  - observability/distributed-tracing-across-services
  - observability/logging
  - observability/arize-phoenix
  - observability/langfuse
  - fastapi-for-ai/opentelemetry-instrumentation
  - node-ts-for-ai/opentelemetry-instrumentation
sources:
  - Official docs: OpenTelemetry (specification, SDKs, Collector)
  - W3C, 2021 — Trace Context (Recommendation)
  - Official docs: OpenTelemetry Semantic Conventions for Generative AI
---

## TL;DR
OpenTelemetry (OTel) is the CNCF vendor-neutral standard for producing traces, metrics and logs: one API
and SDK per language, one wire protocol (OTLP), W3C Trace Context and shared attribute names. Instrument
once; the backend becomes configuration.

## Why it exists
- **Every vendor had its own agent.** A library author who wanted to emit spans had to pick a vendor or
  write adapters, and switching backends meant re-instrumenting every service.
- **Traces broke at service boundaries,** because each system propagated context in its own header (Zipkin's
  `X-B3-*`, Jaeger's `uber-trace-id`, Google's `X-Cloud-Trace-Context`). OpenTracing (an API only) and
  OpenCensus (SDK plus metrics) tried to fix this and merged into OpenTelemetry in 2019.
- **LLM requests cross many systems.** One request crosses an API gateway, the app, a vector store, a model
  provider, tool services and perhaps a queue. The real question is "which of the fourteen calls inside this
  agent run was slow or wrong, and what was its parent"; logs cannot answer it, because they lack the causal
  tree and timing hierarchy.
- **Teams instrumented twice.** Each LLM observability product started with its own SDK, so APM and LLM
  telemetry never joined. The LLM tools have since converged on accepting OTel spans, so learning OTel once
  pays off whichever of them you use.
- **What it buys:** the backend (Jaeger, Tempo, Datadog, Langfuse, Phoenix) is a configuration choice, and an
  LLM span lands in the same trace as the HTTP, queue and database spans around it.

## How it works
### One data model, three signals
| Concept | What it holds |
|---|---|
| **Trace** | A tree of spans sharing a 128-bit `trace_id` |
| **Span** | 64-bit `span_id`, parent span id, name, kind (`SERVER`, `CLIENT`, `INTERNAL`, `PRODUCER`, `CONSUMER`), start and end timestamps, key–value **attributes**, timestamped **events** (an exception is an event), links, status (`UNSET`, `OK`, `ERROR`) |
| **Resource** | Attributes describing the emitter, riding along with every span: `service.name`, `service.version`, `deployment.environment.name` (the older `deployment.environment` is deprecated) |
| **Metrics** | Counters, up/down counters, gauges and histograms (explicit or exponential buckets) with attributes |
| **Logs** | Records carrying `trace_id` and `span_id`, so a log line joins to the span that produced it |

### API versus SDK
- **The API is a no-op until an SDK is installed.** It provides `get_tracer`, `start_span`, `set_attribute`.
- **Why the split:** a library such as an HTTP client or a model provider's SDK can instrument itself without
  pulling in exporters, protobuf or a backend dependency.
- **The application owner** installs the SDK and decides where telemetry goes.

### The export pipeline
1. **`TracerProvider`** holds the resource and a **sampler** and hands out tracers.
2. **Span processor** receives each span when it ends. `SimpleSpanProcessor` exports synchronously
   (debugging only). `BatchSpanProcessor` queues spans and exports them in batches from a background thread,
   because a network call per span would add latency to every operation.
3. **Exporter** speaks **OTLP**: protobuf over gRPC (port 4317) or over HTTP (port 4318, path `/v1/traces`),
   to a Collector or straight to a backend.

### Context propagation
- **In-process,** the current span lives in a context (`contextvars` in Python, `AsyncLocalStorage` in Node),
  so a nested `start_as_current_span` parents itself automatically.
- **Across processes,** a **propagator** injects the W3C `traceparent` header,
  `00-<32 hex trace id>-<16 hex parent span id>-<2 hex flags>`, plus optional `tracestate` and `baggage`. The
  server extracts it and continues the trace.
- **Because it is a W3C standard,** a Go gateway, a Python service and a Node tool server join one trace
  without agreeing on anything else.
- **Queues are the awkward boundary.** The producer injects `traceparent` into message headers; the consumer
  continues that trace as a child, or, when one consumer span processes a batch from many traces, records
  **span links** to each, because a span can have only one parent.

### Sampling
| | Head sampling | Tail sampling |
|---|---|---|
| Decides | At the root, before the trace runs | After the whole trace is seen |
| Typical config | `ParentBased(TraceIdRatioBased(p))`, decision recorded in trace flags so children follow it | Policies: error status, latency over a threshold, a tenant attribute |
| Strength | Cheap and consistent | Keeps the traces that matter |
| Weakness | Blind: decides before anyone knows the trace will error or take 30 seconds | Needs a Collector buffering complete traces, with every span of a trace at the same instance (a load-balancing exporter keyed on trace id) |

- **The ratio sampler is deterministic.** It derives the decision from the trace id rather than a random
  draw, so two services with the same ratio agree about the same trace even without seeing each other's flags.

### The Collector
- **Pipeline stages.** **Receivers** (OTLP, Prometheus scrape, …), **processors** (batch, memory limiter,
  attribute redaction, filtering, tail sampling, transforms) and **exporters** (OTLP to one or several
  backends, Prometheus, files).
- **Why it exists:** changing backends, scrubbing PII, tail sampling and fanning out to two systems become
  configuration changes, not application redeploys.
- **Typical layout:** an agent per host or pod (cheap batching, redaction, host metadata) plus a central
  gateway tier.
- **Tail sampling lives in the gateway tier,** because an agent only sees spans emitted on its own host, and a
  trace that crosses five services is never complete there.

### Semantic conventions and generative AI
- **Standard names** make dashboards work across services: `http.request.method`, `db.system`,
  `server.address`.
- **GenAI span attributes:** `gen_ai.provider.name` (formerly `gen_ai.system`), `gen_ai.request.model`,
  `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, and
  `gen_ai.operation.name` with values such as `chat`. Recommended span name: `{operation} {model}`.
- **GenAI client metrics:** a `gen_ai.client.token.usage` histogram and a `gen_ai.client.operation.duration`
  histogram, so token and latency dashboards need no span queries.
- **Content is opt-in** (off by default) because prompts and completions are sensitive and large.
- **Still experimental; pin versions.** Names have changed more than once: token counts were renamed from
  `prompt`/`completion` to `input`/`output`, then `gen_ai.system` became `gen_ai.provider.name`.
- **Instrumentation packages** emit such spans for OpenAI, Anthropic, Bedrock, LangChain and others.
  OpenLLMetry and the provider-specific `opentelemetry-instrumentation-*` packages follow the GenAI
  conventions; Arize's OpenInference instrumentors emit plain OTel spans with their own LLM vocabulary
  (`openinference.span.kind`, `llm.token_count.*`).

### Auto-instrumentation
- **Whole process:** `opentelemetry-instrument python app.py` (Python) patches supported libraries at start-up.
- **Selective:** per-library instrumentors (`FastAPIInstrumentor`, `HTTPXClientInstrumentor`, …).
- **What you get free:** HTTP, database and client spans. Spans with domain meaning, "retrieve", "rerank",
  "agent step 3", tool name, remain manual.

## Diagram
Spans flow from the SDK through a batch processor and OTLP to an agent Collector, then a gateway that tail
samples and fans out. The `traceparent` header is what joins the downstream service.

```mermaid
flowchart TD
    A["App code: API calls"] --> S["SDK: TracerProvider and sampler"]
    S --> B["BatchSpanProcessor"]
    B -->|"OTLP gRPC 4317 or HTTP 4318"| CA["Collector agent per pod"]
    CA -->|"keyed on trace id"| CG["Collector gateway: tail sampling"]
    CG --> T[("Tracing backend")]
    CG --> L[("LLM tool: Langfuse or Phoenix")]
    A -->|"traceparent header"| D["Downstream service"]
    D -->|"same trace_id"| CA
```

## Examples
### Manual instrumentation of a RAG answer
A root span, a retrieval child and a model-call child carrying GenAI attributes, exported in batches to a
Collector with 25% head sampling. `retrieve`, `build` and `llm` stand for your own code and an
OpenAI-compatible client.

```python
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode

provider = TracerProvider(
    resource=Resource.create({"service.name": "support-bot", "deployment.environment.name": "prod"}),
    sampler=ParentBased(TraceIdRatioBased(0.25)),   # keep 1 trace in 4; children follow the root decision
)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint="otel-collector:4317", insecure=True)))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("support-bot")

def answer(question: str) -> str:
    with tracer.start_as_current_span("support.answer") as root:          # no parent in context: trace root
        root.set_attribute("app.question_chars", len(question))
        with tracer.start_as_current_span("retrieve") as s:
            docs = retrieve(question)
            s.set_attribute("retrieval.document_count", len(docs))
        with tracer.start_as_current_span("chat gpt-4o-mini", kind=SpanKind.CLIENT) as s:
            s.set_attributes({"gen_ai.provider.name": "openai", "gen_ai.operation.name": "chat",
                              "gen_ai.request.model": "gpt-4o-mini"})
            try:
                resp = llm.chat.completions.create(model="gpt-4o-mini", messages=build(question, docs))
            except Exception as e:
                s.record_exception(e); s.set_status(Status(StatusCode.ERROR)); raise
            s.set_attributes({"gen_ai.usage.input_tokens": resp.usage.prompt_tokens,
                              "gen_ai.usage.output_tokens": resp.usage.completion_tokens})
            return resp.choices[0].message.content

# Short-lived process (CLI, Lambda, batch job)? Flush before exit or the batch processor drops the tail:
# provider.force_flush(); provider.shutdown()
```

What crosses the wire is small:
- **The header.** If `retrieve` called a vector service over instrumented HTTP, the request would carry
  `traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01`: version `00`, the 32-hex trace id
  shared by all three spans, the 16-hex id of the calling span, and flags `01` meaning "sampled".
- **Unsampled requests.** Three quarters carry `-00` instead; downstream services see that and record nothing,
  which is how one root decision keeps a trace all-or-nothing.
- **Export side.** The batch processor holds finished spans in a bounded queue and ships them every few
  seconds or when a batch fills; if the queue overflows under load it drops spans rather than block your
  request.
- **With an instrumented HTTP client** making the model call, the `CLIENT` span and `traceparent` injection
  appear without the manual span; keep the GenAI attributes on a wrapping span or let an LLM instrumentation
  library set them.

### Collector gateway: keep every error, 10% of the rest
A production gateway config using the contrib `tail_sampling` processor. A trace is kept if any policy
matches, so errors and slow traces are always retained.

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318
processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 1024
  tail_sampling:
    decision_wait: 10s            # buffer spans this long before deciding
    policies:
      - name: errors
        type: status_code
        status_code: {status_codes: [ERROR]}
      - name: slow
        type: latency
        latency: {threshold_ms: 5000}
      - name: baseline
        type: probabilistic
        probabilistic: {sampling_percentage: 10}
  batch: {}
exporters:
  otlp:
    endpoint: tempo:4317
    tls: {insecure: true}
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, tail_sampling, batch]
      exporters: [otlp]
```

- **Agents in front** must use a load-balancing exporter routed by trace id, so every span of a trace reaches
  the same gateway instance.
- **Applications send everything** (`ParentBased` with ratio 1.0, or the default sampler) when tail sampling
  decides, otherwise errors were already dropped at the head.

### Worked sizing: span volume under each policy
Illustrative numbers for an agent service at 200 requests/s, 20 spans per request, 2% error traces.

| Policy | Spans/s stored | Error traces kept |
|---|---|---|
| No sampling | 4,000 | 100% |
| Head 10% | 400 | ~10% |
| Tail: errors + 10% baseline | ~80 (errors) + ~392 (10% of the rest) = ~472 | 100% |

Tail sampling costs about the same storage as head 10% but keeps every failure; the price is gateway
memory to hold `decision_wait` seconds of full traffic (here 40,000 spans for 10 s).

## Production patterns
- **Configure by environment, not code.** Set `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`,
  `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_TRACES_SAMPLER=parentbased_traceidratio` and
  `OTEL_TRACES_SAMPLER_ARG=0.25` per deployment, so sampling and routing change without a release. Monitor
  trace completeness per service.
- **Agent plus gateway Collectors.** Agents batch, add host metadata and redact; a gateway tier behind the
  `loadbalancing` exporter (routing by trace id) does tail sampling and fan-out. Monitor the Collector's own
  metrics for refused and dropped spans and exporter queue size.
- **Flush in serverless handlers.** Call `provider.force_flush()` at the end of each Lambda or batch job
  invocation, or use the Lambda instrumentation layer. Monitor traces missing their final span.
- **Redact at the Collector.** Use an `attributes` processor with `action: delete` (or a redaction
  processor) on keys that may hold PII before anything leaves the VPC. Monitor for allow-list violations in a
  sampled audit.
- **Pin GenAI instrumentation versions.** Treat the experimental `gen_ai.*` names as a schema: pin package
  versions and test dashboards on upgrade. Monitor token panels for sudden gaps.
- **Opt in to content per environment.** Enable prompt and completion capture in staging and for sampled
  production traffic only, using the instrumentation library's content-capture setting. Monitor span size and
  storage growth.

## Trade-offs
- **Vendor neutrality vs uneven maturity.** One instrumentation for every backend, but maturity varies across
  languages and signals (traces are stable everywhere, logs arrived last). It flips only if you are committed
  to one vendor's proprietary agent for features OTel does not model, such as continuous profiling.
- **Auto vs manual spans.** Auto-instrumentation yields HTTP, database and client spans in minutes, but adds
  spans you did not ask for and attributes you must size; manual spans cost code and carry the domain
  semantics you will actually query.
- **Collector vs direct export.** A Collector is one more component to run, but it unlocks tail sampling,
  redaction and fan-out. Skip it for a single service sending to one SaaS backend; add it the moment you want
  100% of errors with 10% of successes.
- **Head vs tail sampling.** Head sampling is cheap and consistent but discards the rare failing trace; tail
  sampling keeps what matters at the cost of buffering whole traces in memory and routing spans by trace id.
- **Content on spans vs obligations.** Prompt and completion text makes traces debuggable, yet inflates
  storage and creates PII and retention obligations, which is exactly why the GenAI conventions make content
  opt-in.

## Pitfalls
- **No flush in short-lived processes.** Spans never arrive from Lambda, CLI or batch jobs because
  `BatchSpanProcessor` exports on a timer and the process exited first. Symptom: traces missing their last
  spans, or missing entirely, with no error.
- **Lost context across threads, tasks or queues.** Work crosses a thread pool, an `asyncio` task created
  without copying context, or a queue. Symptom: every tool call appears as its own root trace with no parent.
- **Protocol and port mismatch.** The gRPC exporter pointed at 4318, or the HTTP exporter missing
  `/v1/traces`. Symptom: an empty backend and, at best, connection errors buried in application logs.
- **High-cardinality attributes on metrics.** `user_id` or prompt text on metrics rather than spans. Symptom:
  a series explosion in Prometheus or a surprise bill, followed by slow dashboards.
- **Instrumenting too late.** The client library was imported or instantiated before the instrumentor patched
  it. Symptom: HTTP spans present but model-call spans absent.
- **Ending a streaming span early.** The span ends when the call returns the stream object. Symptom: model
  spans of a few milliseconds and no token counts while users wait ten seconds. Fix: end the span when the
  stream is exhausted and record first-token time as an event or attribute.
- **Unpinned experimental names.** Dashboards built on GenAI attribute names without pinned versions. Symptom:
  token and cost panels go blank after a dependency upgrade renames an attribute.

## When to use
- **Use OTel** as the instrumentation layer for any service that calls a model, a retriever or a tool, and
  pick the backend separately: a general tracing store for the infrastructure view and an LLM-aware tool
  (Langfuse, Phoenix, LangSmith via OTLP) for prompts, tokens and evaluation.
- **Avoid when:** a notebook experiment (unnecessary) or a single script (overkill).
- **Measure:** trace completeness (fraction of requests with a root span and all expected children), exporter
  error rate, and the share of error traces you actually retain under your sampling policy.
- **Day to day:** the `traceparent` you forward through a gateway, the `service.name` you set on a new
  deployment and the flush you add to a serverless handler.
- **Interviews:** explain the API/SDK split, head versus tail sampling and how a trace survives a service
  boundary.

## Role lens
- **Engineer:** Configures providers, processors and exporters, adds domain spans around retrieval and
  agent steps, and fixes broken propagation across async tasks and queues.
- **Architect:** Owns the telemetry topology: Collector placement, sampling policy, which attributes are
  allowed to leave the VPC, and the one trace id that ties gateway, app and model calls together.
- **FDE:** Plugs the deployed solution into the customer's existing OTel Collector and backend instead of
  shipping another agent, and negotiates which prompt content may be captured.

## Check yourself
**Q:** A Python agent service sits behind a Go API gateway and calls a Node tool server; a Lambda summariser
runs afterwards. Traces show three separate roots per request and nothing at all from the Lambda. Diagnose
and fix.
**A:** Three roots means context is not propagated at two boundaries. The gateway must inject (or forward an
incoming) `traceparent`; the Python service must extract it on the inbound request, which FastAPI or WSGI
instrumentation does, and must inject it on outbound calls, which requires instrumenting the HTTP client it
uses; the Node server needs the OTel SDK with the W3C propagator registered and must extract on its inbound
handler. Check nobody is still on B3 and that samplers are `ParentBased` so children honour the root decision.
The Lambda problem is almost certainly the batch processor: the runtime freezes or exits before the timer
fires, so call `force_flush` at the end of the handler or use the Lambda instrumentation that does so. Finally
confirm each service sets a distinct `service.name` so the merged trace is readable.

## Interview questions
### What does OpenTelemetry standardise, and why does it matter for an LLM application?
- **Four things:** the API and SDK per language, the OTLP wire protocol, W3C Trace Context propagation, and
  semantic-convention attribute names, including `gen_ai.*` for model calls.
- **Why it matters:** LLM observability tools accept OTel spans, so a model call lands in the same trace as
  the gateway, retrieval and database spans, and the backend is a configuration change.

### After moving tool execution to asyncio tasks, every tool call shows up as its own root trace. How do you debug it?
- **Symptom means lost context.** The parent lives in `contextvars`; work started in a thread pool or a task
  created without the current context has no parent, so the SDK starts a new root.
- **Fix:** propagate context explicitly (copy the context into the task or executor, or use instrumentation
  that does), then verify by checking that tool spans share the root `trace_id`.
- **Also check queues,** where `traceparent` must be injected into message headers.

### When would you choose head sampling over tail sampling?
- **Head sampling** when traffic is uniform, failures are not rare, or you have no Collector tier: it is
  cheap, stateless and consistent through `ParentBased`.
- **Tail sampling** when you must keep every error or slow trace with a small baseline; it costs gateway
  memory for `decision_wait` of full traffic and trace-id routing.
- **Avoid tail sampling** if you cannot guarantee all spans of a trace reach one instance; decisions on
  partial traces are wrong.

### Model-call spans show 5 ms durations and no token counts, but users wait 10 s. What is wrong?
- **The span ends at stream creation.** A streaming call returns immediately; the span closes before any
  tokens arrive, so duration and usage are missing.
- **Fix:** end the span when the stream is exhausted, set `gen_ai.usage.*` from the final chunk's usage, and
  record first-token time as an event or attribute so TTFT is visible.

### Should prompt and completion text go on spans in production?
- **Default no.** The GenAI conventions make content opt-in because it is large and sensitive, creating PII
  and retention obligations.
- **Pragmatic middle:** capture content in staging and for a sampled or consented slice of production, redact
  at the Collector, and keep token counts and model names on every span.

## Quiz
### Q: Why is OpenTelemetry split into separate API and SDK packages?
- [ ] The API handles traces and the SDK handles metrics and logs
- [x] So libraries can instrument themselves with a no-op dependency while the application chooses the SDK, exporters and backend
- [ ] The SDK is vendor-specific and the API is the only open part
- [ ] To allow sampling decisions to be made in the API layer before spans are created
> A library author cannot know which backend its users run. Depending only on the API lets them emit spans
> that cost nothing until an application installs and configures an SDK; the SDK is as vendor-neutral as the API.

### Q: You head-sample 10% of traces at the root but must retain every trace that contains an error. What is required?
- [ ] Set the sampler to AlwaysOn only on spans whose status is ERROR
- [ ] Raise the ratio to 50% so most errors are statistically included
- [x] Tail sampling in a Collector, with all spans of a trace routed to the same Collector instance
- [ ] Replace the BatchSpanProcessor with a SimpleSpanProcessor
> Head sampling decides before the error happens, so no root-level setting can do it. A tail sampler sees
> the complete trace and keeps it on an error policy, which only works if the whole trace lands in one place.

### Q: What does the W3C `traceparent` header carry?
- [ ] The full set of span attributes of the calling span
- [ ] The caller's service name and resource attributes
- [x] A version, the trace id, the parent span id and trace flags such as the sampled bit
- [ ] The sampling ratio as a floating-point value
> `traceparent` is deliberately tiny: `00-<trace id>-<parent span id>-<flags>`. Attributes and resources
> stay on the spans themselves; the sampled flag, not a ratio, tells downstream services whether to record.
