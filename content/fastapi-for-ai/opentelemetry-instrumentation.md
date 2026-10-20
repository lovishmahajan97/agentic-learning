---
id: opentelemetry-instrumentation
module: fastapi-for-ai
name: OpenTelemetry instrumentation
kind: tool
difficulty: core
est_minutes: 15
related:
  - fastapi-for-ai/structured-logging
  - fastapi-for-ai/shared-httpx-asyncclient
  - fastapi-for-ai/uvicorn-gunicorn-workers
  - fastapi-for-ai/streamingresponse
  - observability/opentelemetry
  - observability/distributed-tracing-across-services
  - observability/spans
  - node-ts-for-ai/opentelemetry-instrumentation
sources:
  - Official docs: OpenTelemetry Python
  - Official docs: OpenTelemetry semantic conventions
  - Official docs: W3C Trace Context
---

## TL;DR
OpenTelemetry (OTel) is a vendor-neutral API, SDK and wire protocol for traces, metrics and logs. In
FastAPI you initialise a tracer provider with an OTLP exporter, auto-instrument the app and its clients
(FastAPI, httpx, SQLAlchemy, Redis), and add manual spans around LLM calls and retrieval steps. The
payoff is a single trace per request showing where its seconds went, propagated across services, sent to
whichever backend you choose without re-instrumenting.

## Why it exists
A chat request that takes 9 seconds might spend them in the provider, in a vector search, in a re-ranker,
in a database lock, or in three retries of a tool call made by an agent loop. Logs tell you that events
happened; they do not show the causal tree of who called whom and how long each part took, especially
once the request crosses services (gateway, retrieval service, model server). Without that tree, latency
debugging is guesswork.

Vendor-specific APM agents solve this but lock the instrumentation to one backend; switching vendors means
re-instrumenting every service, and libraries cannot ship instrumentation for every vendor. Hand-rolled
timing logs do not propagate across process boundaries and cannot be assembled into a tree. OTel separates
the concerns: libraries and your code call a stable API, the SDK decides sampling and batching, and an
exporter speaks OTLP to a collector or backend, so the backend is a deployment choice rather than a code
change. It has become the common substrate that LLM observability tools also ingest.

## How it works
### Data model
A **trace** is a tree of **spans** sharing a trace id. Each span has a name, start and end time, a parent,
a status and attributes (key-value pairs). Spans are created through a **tracer** obtained from the global
**tracer provider**, which owns the **resource** (attributes describing the service, such as
`service.name` and `deployment.environment`), the **sampler** and the **span processors**. The
`BatchSpanProcessor` queues finished spans and exports them on a background thread, because exporting
synchronously would add network latency to every request.

### Propagation
When the instrumented httpx client calls another service, it injects a W3C `traceparent` header carrying
the trace id and the current span id. The receiving service's FastAPI instrumentation extracts it and
makes its server span a child, which is how one trace spans many services. Within a process, the current
span lives in a `contextvars`-based context, so async code keeps correct parentage across `await`s.

### Auto versus manual instrumentation
- **Auto:** `FastAPIInstrumentor.instrument_app(app)` wraps the ASGI app in middleware that creates a
  server span per request, named after the route template, with HTTP method and status attributes.
  Library instrumentors (`HTTPXClientInstrumentor`, `SQLAlchemyInstrumentor`, Redis) add client spans.
  The `opentelemetry-instrument` command can apply installed instrumentors without code changes.
- **Manual:** auto-instrumentation sees an HTTP POST to the provider but not that it was a chat completion
  on a specific model with 812 input tokens. Wrap model calls, retrieval, reranking and tool execution in
  your own spans and set attributes. OTel's GenAI semantic conventions define names such as
  `gen_ai.request.model` and `gen_ai.usage.input_tokens`; they are still marked as in development, so pin
  the version you follow.

### Sampling
Recording every span of every request is expensive at volume. A `ParentBased(TraceIdRatioBased(0.1))`
sampler keeps about 10% of new traces and follows the caller's decision for propagated ones, so a trace is
never half-sampled across services. Tail-based sampling (keep all errors and slow traces) happens in the
collector, because only after a trace ends do you know whether it was interesting.

### Process model
The batch processor's export thread does not survive `fork`. With Gunicorn `--preload` or any pre-fork
server, initialise the tracer provider in each worker (a `post_fork` hook or lifespan), not in the master.

## Example
Initialisation in lifespan (per worker), auto-instrumentation for FastAPI and httpx, and a manual span
around an LLM call with GenAI-style attributes.

```python
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

@asynccontextmanager
async def lifespan(app: FastAPI):
    provider = TracerProvider(resource=Resource.create({"service.name": "chat-api"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # OTEL_EXPORTER_OTLP_ENDPOINT
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()
    app.state.http = httpx.AsyncClient(base_url="https://llm.example.internal", timeout=60)
    yield
    await app.state.http.aclose()
    provider.shutdown()                       # flushes queued spans; skipped if the pod is SIGKILLed

app = FastAPI(lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)       # wraps the ASGI app; set OTEL_PYTHON_FASTAPI_EXCLUDED_URLS for probes
tracer = trace.get_tracer("chat-api")

@app.post("/v1/chat")
async def chat(body: dict):
    with tracer.start_as_current_span("chat model-a") as span:
        span.set_attribute("gen_ai.request.model", "model-a")
        r = await app.state.http.post("/v1/chat/completions", json=body)   # child client span
        usage = r.json().get("usage", {})
        span.set_attribute("gen_ai.usage.input_tokens", usage.get("prompt_tokens", 0))
        span.set_attribute("gen_ai.usage.output_tokens", usage.get("completion_tokens", 0))
        return r.json()
```

The resulting trace is `POST /v1/chat` (server) → `chat model-a` (manual) → `POST` (httpx client), with
the provider's latency and token counts on the spans. For streaming responses, start the model span inside
the generator: a span ended when the handler returns would close before the first token is streamed.

## Trade-offs
- Vendor-neutral instrumentation lets you change backends by changing an exporter endpoint, but OTel's
  configuration surface (providers, processors, samplers, collectors) is larger than a single vendor agent.
- Auto-instrumentation gives broad coverage in minutes, but it records generic HTTP spans; the
  AI-specific meaning (model, tokens, prompt version) only exists if you add manual spans.
- Head sampling at a low ratio keeps cost bounded, but it drops most rare errors; flip to collector tail
  sampling when you need every failed or slow trace.
- Recording prompts and completions as span attributes or events helps quality debugging, but traces are
  often retained and shared more widely than logs, so default to metadata only.

## Pitfalls
- Initialising the provider before a pre-fork server forks workers; the symptom is a service that works
  but exports no spans from workers, or exports only from the first process.
- Ending the LLM span before a `StreamingResponse` finishes; the symptom is model spans that last a few
  milliseconds while the server span lasts the full 30 seconds of streaming.
- Calling `instrument_app` and also adding the instrumentation middleware manually, or instrumenting twice
  on reload; the symptom is duplicated nested server spans for every request.
- Leaving health probes instrumented; the symptom is traces dominated by `/livez` every few seconds per pod
  and a tracing bill driven by probes.
- Putting user ids or raw prompts in span names instead of attributes; the symptom is an explosion of
  unique span names that breaks grouping and latency aggregation in the backend.

## When to use
Instrument with OTel as soon as a request touches more than one slow dependency or more than one service,
which describes almost every RAG or agent backend. Start with auto-instrumentation for FastAPI and your HTTP
and DB clients, add manual spans for model calls, retrieval and tool execution, and export to a collector so
sampling and routing stay out of application code. Correlate by adding the trace id to structured logs.
Measure span coverage of p95 latency, so that unexplained time between spans shrinks. In interviews, explain
propagation via `traceparent` and why manual spans are needed for LLM semantics.

## Role lens
- **Engineer:** Initialises the SDK per worker, instruments FastAPI and clients, writes manual spans with
  GenAI attributes, and keeps spans open for the whole stream.
- **Architect:** Chooses the collector topology, sampling policy, attribute conventions and backend, and
  decides what content may ever appear in telemetry.
- **FDE:** Points the exporter at the customer's existing collector or APM, often in a restricted network,
  so the service's traces appear in tools their operators already use.

## Check yourself
**Q:** Traces for your agent API show a 12-second server span with only 3 seconds covered by child spans
(two provider calls and one vector search). How do you find the missing 9 seconds?
**A:** Uncovered time is work that happens outside any instrumented span, so I would list what the handler
does between those calls: prompt assembly, tokenisation, JSON parsing, tool execution, retries with backoff
sleeps, or a client that is not instrumented (a sync SDK running in a thread, a gRPC client, a Redis call).
I would add manual spans around each agent step and tool call and instrument missing clients. If gaps remain
with no code there, it points to event-loop blocking: the loop could not resume awaiting tasks, so check loop
lag and look for synchronous calls in async handlers. I would also check the stream case: if the response
streams, part of that time is token streaming that a manual span closed early.

## Quiz
### Q: How does a downstream service's span become a child of the caller's span?
- [ ] Both services write to the same database table of span ids
- [x] The instrumented client injects a `traceparent` header that the server instrumentation extracts
- [ ] The collector matches spans by timestamp after export
- [ ] FastAPI shares its context object across processes through shared memory
> W3C Trace Context carries the trace id and parent span id in a header. The receiving instrumentation
> starts its server span with that parent, so spans from many services assemble into one trace.

### Q: Why add manual spans around LLM calls when httpx is already auto-instrumented?
- [ ] Auto-instrumented spans are not exported by default
- [ ] httpx instrumentation only records failed requests
- [x] Generic HTTP spans lack model, token and prompt attributes, and miss steps that are not HTTP calls
- [ ] Manual spans make the provider call faster
> The client span shows a POST and its duration. Model name, token usage, retries and non-HTTP steps
> like reranking or tool execution only appear if you create spans and set attributes yourself.

### Q: Spans export from a local run but not from production behind Gunicorn with `--preload`. What is the likely cause?
- [ ] The OTLP exporter is incompatible with Uvicorn workers
- [x] The tracer provider and its background export thread were created in the master before fork
- [ ] Gunicorn strips the `traceparent` header
- [ ] Sampling is always disabled under Gunicorn
> Threads do not survive `fork`, so workers inherit a batch processor without its export thread and queued
> spans are never sent. Initialise the provider per worker in `post_fork` or lifespan.
