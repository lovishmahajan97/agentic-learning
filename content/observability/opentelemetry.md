---
id: opentelemetry
module: observability
name: OpenTelemetry
kind: tool
difficulty: core
est_minutes: 16
status: fact-checked
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
OpenTelemetry (OTel) is the CNCF vendor-neutral standard for *producing* telemetry: one API and SDK per
language for traces, metrics and logs, one wire protocol (OTLP), one context-propagation format (W3C
Trace Context) and one vocabulary of attribute names. It exists so instrumentation is written once and
the backend (Jaeger, Tempo, Datadog, Langfuse, Phoenix) becomes a configuration choice. For LLM
applications it is the plumbing under most tracing tools, and the way an LLM span lands in the same trace
as the HTTP, queue and database spans around it.

## Why it exists
Before OTel, every APM vendor shipped its own agent and SDK. A library author who wanted to emit spans had
to pick a vendor or write adapters; switching backends meant re-instrumenting every service; and traces
broke at service boundaries because each system propagated context in its own header (Zipkin's `X-B3-*`,
Jaeger's `uber-trace-id`, Google's `X-Cloud-Trace-Context`). Two open projects tried to fix this,
OpenTracing (an API only) and OpenCensus (SDK plus metrics), and in 2019 they merged into OpenTelemetry.

LLM applications make the problem sharper. One user request crosses an API gateway, the application, a
vector store, a model provider, several tool services and perhaps a queue, and the question you actually
need answered is "which of the fourteen calls inside this agent run was slow or wrong, and what was its
parent". Logs cannot answer that: they lack the causal tree and the timing hierarchy. Each LLM
observability product also started with its own SDK, so teams instrumented twice, once for the APM and
once for the LLM tool, and the two never joined. The LLM tools have since converged on accepting OTel
spans, which is why learning OTel once pays off whichever of them you use.

## How it works
### One data model, three signals
A **trace** is a tree of **spans** that share a 128-bit `trace_id`. Each span has a 64-bit `span_id`, a
parent span id, a name, a kind (`SERVER`, `CLIENT`, `INTERNAL`, `PRODUCER`, `CONSUMER`), start and end
timestamps, key–value **attributes**, timestamped **events** (an exception is an event), links to other
spans and a status (`UNSET`, `OK`, `ERROR`). **Resource** attributes describe the emitter
(`service.name`, `service.version`, `deployment.environment.name`; the older `deployment.environment`
is deprecated) and ride along with every span. **Metrics**
are counters, up/down counters, gauges and histograms (explicit or exponential buckets) with attributes.
**Logs** are records that carry `trace_id` and `span_id` so a log line can be joined to the span that
produced it.

### API versus SDK
The API package (`get_tracer`, `start_span`, `set_attribute`) is a no-op until an SDK is installed. The
split exists *because* a library such as an HTTP client or a model provider's SDK should be able to
instrument itself without pulling in exporters, protobuf or a backend dependency. The application owner
installs the SDK and decides where telemetry goes.

### The export pipeline
A `TracerProvider` holds the resource and a **sampler** and hands out tracers. When a span ends it goes to a
**span processor**: `SimpleSpanProcessor` exports synchronously (debugging only), `BatchSpanProcessor`
queues spans and exports them in batches from a background thread, because a network call per span would
add latency to every operation. The **exporter** speaks **OTLP**, protobuf over gRPC (port 4317) or over
HTTP (port 4318, path `/v1/traces`), to a Collector or straight to a backend.

### Context propagation
In-process, the current span lives in a context (`contextvars` in Python, `AsyncLocalStorage` in Node), so a
nested `start_as_current_span` parents itself automatically. Across processes, a **propagator** injects the
W3C `traceparent` header, `00-<32 hex trace id>-<16 hex parent span id>-<2 hex flags>`, plus optional
`tracestate` and `baggage`, into outgoing requests, and the server side extracts it and continues the
trace. Because the format is a W3C standard, a Go gateway, a Python service and a Node tool server join one
trace without agreeing on anything else.

### Sampling
**Head sampling** decides at the root: `ParentBased(TraceIdRatioBased(p))` keeps a fraction of traces and
records the decision in the trace flags so every child follows it. It is cheap and consistent but blind,
since it decides before anyone knows the trace will error or take 30 seconds. **Tail sampling** keeps whole
traces that match a policy (error status, latency over a threshold, a tenant attribute) and requires a
Collector that buffers complete traces, which in turn requires every span of a trace to reach the same
Collector instance (a load-balancing exporter keyed on trace id).

### The Collector
A standalone process with **receivers** (OTLP, Prometheus scrape, …), **processors** (batch,
memory limiter, attribute redaction, filtering, tail sampling, transforms) and **exporters** (OTLP to one
or several backends, Prometheus, files). It exists so that changing backends, scrubbing PII, sampling at
the tail and fanning out to two systems are configuration changes, not application redeploys. Typical
layout: an agent per host or pod plus a central gateway tier.

### Semantic conventions and generative AI
Attribute names are standardised so dashboards work across services: `http.request.method`,
`db.system`, `server.address`. The **GenAI semantic conventions** add `gen_ai.provider.name`
(formerly `gen_ai.system`), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`,
`gen_ai.usage.output_tokens` and `gen_ai.operation.name` with values such as `chat`, and recommend the span
name `{operation} {model}`. Prompt and completion content is opt-in (off by default) because it is
sensitive and large. At the time of writing these conventions are still marked experimental and names have
already changed more than once (the token-count attributes were renamed from `prompt`/`completion` to
`input`/`output`, then `gen_ai.system` became `gen_ai.provider.name`), so pin your instrumentation library
versions. Instrumentation packages emit such spans automatically for OpenAI, Anthropic, Bedrock, LangChain
and others: OpenLLMetry and the provider-specific `opentelemetry-instrumentation-*` packages follow the
GenAI conventions, while Arize's OpenInference instrumentors emit plain OTel spans with their own,
LLM-specific attribute vocabulary (`openinference.span.kind`, `llm.token_count.*`).

### Auto-instrumentation
`opentelemetry-instrument python app.py` (Python) patches supported libraries at start-up; per-library
instrumentors (`FastAPIInstrumentor`, `HTTPXClientInstrumentor`, …) do the same selectively. That gives
HTTP, database and client spans for free. The spans that carry your domain meaning, "retrieve", "rerank",
"agent step 3", tool name, remain manual.

## Example
Manual instrumentation of a retrieval-augmented answer: a root span, a retrieval child and a model-call
child carrying GenAI attributes, exported in batches to a Collector with 25% head sampling. `retrieve`,
`build` and `llm` stand for your own code and an OpenAI-compatible client.

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

If an instrumented HTTP client makes the model call, the `CLIENT` span and `traceparent` injection appear
without the manual span; you would keep the GenAI attributes on a wrapping span or let an LLM
instrumentation library set them.

## Trade-offs
- Vendor neutrality and a single instrumentation for every backend, against uneven maturity across
  languages and signals (traces are stable everywhere, logs arrived last); it flips only if you are
  committed to one vendor's proprietary agent for features OTel does not model, such as continuous profiling.
- Auto-instrumentation yields HTTP, database and client spans in minutes, but adds spans you did not ask for
  and attributes you must size; manual spans cost code and carry the domain semantics you will actually query.
- A Collector is one more component to run, but it unlocks tail sampling, redaction and fan-out; skip it for
  a single service sending to one SaaS backend, add it the moment you want 100% of errors with 10% of successes.
- Head sampling is cheap and consistent but discards the rare failing trace; tail sampling keeps what matters
  at the cost of buffering whole traces in memory and routing spans by trace id.
- Putting prompt and completion text on spans makes traces debuggable, yet inflates storage and creates PII
  and retention obligations, which is exactly why the GenAI conventions make content opt-in.

## Pitfalls
- Spans never arrive from Lambda, CLI or batch jobs because `BatchSpanProcessor` exports on a timer and the
  process exited first; the symptom is traces missing their last spans, or missing entirely, with no error.
- Orphaned traces when work crosses a thread pool, an `asyncio` task created without copying context, or a
  queue; the symptom is every tool call appearing as its own root trace with no parent.
- Protocol and port mismatch, the gRPC exporter pointed at 4318 or the HTTP exporter missing `/v1/traces`;
  the symptom is an empty backend and, at best, connection errors buried in application logs.
- High-cardinality attributes (`user_id`, prompt text) on metrics rather than spans; the symptom is a series
  explosion in Prometheus or a surprise bill, followed by slow dashboards.
- Instrumenting after the client library was imported or instantiated, when the instrumentor patches at
  instrument time; the symptom is HTTP spans present but model-call spans absent.
- Building dashboards on experimental GenAI attribute names without pinning versions; the symptom is token
  and cost panels going blank after a dependency upgrade renames an attribute.

## When to use
Use OTel as the instrumentation layer for any service that calls a model, a retriever or a tool, and pick the
backend separately: a general tracing store for the infrastructure view and an LLM-aware tool (Langfuse,
Phoenix, LangSmith via OTLP) for prompts, tokens and evaluation. It is unnecessary for a notebook experiment
and overkill for a single script. Measure trace completeness (fraction of requests with a root span and all
expected children), exporter error rate, and the share of error traces you actually retain under your
sampling policy. In day-to-day work OTel shows up as the `traceparent` you forward through a gateway, the
`service.name` you set on a new deployment and the flush you add to a serverless handler. In interviews,
expect to explain the API/SDK split, head versus tail sampling and how a trace survives a service boundary.

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
