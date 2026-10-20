---
id: observability-stack
module: llmops-platform
name: Observability stack
kind: pattern
difficulty: core
est_minutes: 14
related:
  - observability/tracing
  - observability/opentelemetry
  - observability/distributed-tracing-across-services
  - observability/token-cost-tracking
  - observability/langfuse
  - observability/langsmith
  - observability/arize-phoenix
  - llmops-platform/model-monitoring
sources:
  - Official docs: OpenTelemetry Semantic Conventions for Generative AI
  - Official docs: OpenTelemetry Collector
  - Sigelman et al., 2010 — Dapper, a Large-Scale Distributed Systems Tracing Infrastructure
  - Beyer et al., 2016 — Site Reliability Engineering
---

## TL;DR
An observability stack is the pipeline that turns every AI request into something you can query
later: instrumentation that emits traces carrying LLM-specific attributes, a collector that redacts
and samples, separate stores for traces, metrics and payloads, an analysis layer where scores and
evaluations attach to traces, and alerting on top. It is a stack because each layer has a different
owner, retention rule and cost. The deep topics live in the Observability module; this lesson is how
they fit together as a platform.

## Why it exists
A single team with one chatbot can get by with a vendor SDK that logs prompts and completions to a
hosted dashboard. The moment there are several teams, several models and a compliance function, that
arrangement breaks in four places at once. Each team instruments differently, so there is no way to
answer "what did we spend on model X across the company" or "which tenants were affected by the
provider's outage". Prompts containing customer data flow into whichever SaaS the team picked, with
retention nobody checked. A request that crosses the gateway, the application, a retriever and three
tool calls appears as four unrelated records in four systems. And the evaluation loop, which needs
to pull failed traces into a dataset and re-score them, has no join key between the trace store and
the eval tool.

The naive fix, "pick one vendor and make everyone use it", solves consistency and creates lock-in:
every span is emitted in that vendor's format, every dashboard is theirs, and migrating means
re-instrumenting every service. The other naive fix, "reuse the existing APM stack", gives you
latency and errors but not tokens, cost, prompt versions, judge scores or the ability to open a span
and read the completion, because classic APM has no notion of a generation.

An observability stack exists to separate the concerns: a neutral wire format at the edge so
instrumentation is written once; a collection layer where policy (redaction, sampling, routing) is
applied centrally; stores chosen per signal type; and LLM-specific analysis tools that consume the
same data rather than owning it. The goal is that any alert, invoice line or quality score can be
traced back to the exact requests behind it, across teams, without the data leaving your control.

## How it works
### Layer 1: instrumentation
Applications emit traces: a tree of spans per request, with one span per model call, retrieval,
tool call and guardrail check. OpenTelemetry is the standard SDK and wire format *because* it is
vendor-neutral and already carries W3C trace context across HTTP and queue boundaries, which is what
makes the gateway's span and the application's span part of one trace. The GenAI semantic
conventions name the LLM-specific attributes: operation, provider, requested and actual model, input
and output token counts, finish reasons, and optionally the prompt and completion content as events.
Those conventions are still evolving and attribute names have changed between versions, so the
platform pins a version and keeps a mapping layer. On top of the standard attributes, every span
carries the platform's own keys: tenant, user (pseudonymous), bundle and prompt version, config
version, and request id. These are mandatory *because* every later question is a group-by on them.

### Layer 2: collection
Services send to an OpenTelemetry Collector rather than straight to a backend. The collector is where
platform policy runs: an attributes or redaction processor strips or hashes payload fields according
to the tenant's data classification; a tail-sampling processor keeps 100% of traces with errors, low
scores or high latency and a small fraction of the rest; a batch processor reduces write amplification;
exporters fan the same stream out to several backends. Centralising this exists *because* each
application team getting redaction right independently is the thing that fails. Tail sampling, which
decides after the trace is complete, is chosen over head sampling *because* the traces you want are
rare and only identifiable at the end.

### Layer 3: storage, by signal
Three signals, three stores, chosen by cardinality and retention. **Traces** (high volume, structured,
weeks of retention) go to a trace store. **Metrics** (low cardinality, long retention) go to a time
series database; a spanmetrics connector in the collector derives request rate, error rate and
latency histograms per model, bundle and tenant from the spans, so nobody hand-instruments counters.
**Payloads**, the prompts and completions, are the sensitive, bulky part and live in a store with
their own access control and retention, referenced from spans by id. A full RAG trace with retrieved
chunks can be tens of kilobytes; at a million requests a day that is tens of gigabytes a day
(illustrative), which is why payload retention is a budget decision, not a default.

### Layer 4: LLM-specific analysis and the evaluation loop
Langfuse, LangSmith and Arize Phoenix sit here: a trace viewer that renders prompts and completions,
sessions and users, prompt-version comparison, and, crucially, **scores** attached to traces: user
feedback, human annotation and LLM-as-a-judge results. Scores on traces are what close the loop:
filter traces by low score, add them to a dataset, run the candidate bundle against that dataset,
compare. These tools increasingly accept OpenTelemetry ingestion, so they consume the same stream
the collector exports rather than requiring their own instrumentation; whether to self-host or use
their cloud is a data-residency decision.

### Layer 5: metrics, SLOs and alerting
Dashboards and alerts consume the derived metrics: latency percentiles, error classes, tokens and
cost per tenant and feature, and the behavioural proxies and judge aggregates from the monitoring
lesson. Every alert links to a trace query *because* an alert without examples is a two-hour hunt.

### What makes it a platform rather than a tool
Trace context propagated through the gateway so cost attribution and debugging work end to end; one
redaction policy; one set of mandatory attributes; one place where a judge score and a user's
thumbs-down land on the same trace; and the ability to swap any backend by changing an exporter.

## Example
Instrumenting one request with the OpenTelemetry SDK: a root span for the request, child spans for
retrieval and the model call, GenAI attributes on the model span, and the platform's mandatory keys.
`retrieve` and `call_model` are the application's existing functions; the exporter sends to a
collector whose address comes from the standard `OTEL_EXPORTER_OTLP_ENDPOINT` variable.

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))   # -> collector, not a vendor
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("support-assistant")

MODEL = "model-id-from-bundle"          # pinned in the bundle, never hard-coded here

def answer(question, tenant, bundle="support-assistant@41", config_version=118):
    with tracer.start_as_current_span("answer") as root:              # one trace per request
        root.set_attributes({"app.tenant": tenant, "app.bundle": bundle,
                             "app.config_version": config_version})   # mandatory platform keys
        with tracer.start_as_current_span("retrieve") as s:
            docs = retrieve(question)
            s.set_attribute("retrieval.doc_count", len(docs))
        with tracer.start_as_current_span(f"chat {MODEL}") as s:      # GenAI span naming convention
            s.set_attributes({"gen_ai.operation.name": "chat",
                              "gen_ai.provider.name": "anthropic",     # 'gen_ai.system' in older versions
                              "gen_ai.request.model": MODEL})
            resp = call_model(MODEL, question, docs)
            s.set_attributes({"gen_ai.response.model": resp.model,
                              "gen_ai.usage.input_tokens": resp.usage.input_tokens,
                              "gen_ai.usage.output_tokens": resp.usage.output_tokens})
            s.add_event("gen_ai.content.completion", {"preview": resp.text[:200]})  # redacted downstream
        return resp.text
```

The collector pipeline for this stream, in prose: an OTLP receiver; a redaction processor that drops
`preview` for tenants classified as confidential; tail sampling keeping every trace with an error
status or latency above the budget plus a 5% sample (illustrative) of the rest; a spanmetrics
connector producing per-model, per-bundle rate, error and latency series; and exporters to the trace
store, the metrics database and the LLM analysis tool.

## Trade-offs
- OpenTelemetry keeps instrumentation vendor-neutral and composable, but vendor SDKs capture richer
  LLM detail with less code; the balance tips toward the SDK only for a single team with one tool
  and no residency constraints.
- Tail sampling keeps every interesting trace at the cost of buffering whole traces in the collector,
  which needs memory and sticky routing; head sampling is cheap but throws away the errors you wanted.
- Storing full prompts and completions makes any incident reproducible but creates a sensitive store
  with retention and access obligations; hashing or short previews trade debuggability for safety,
  and the right answer differs per tenant classification.
- Deriving metrics from spans removes hand-written counters and guarantees consistency, but the
  cardinality of the labels you allow (tenant × bundle × model) sets the metrics bill.
- Self-hosting the analysis tool keeps data in your boundary but adds an operational surface; the
  vendor cloud is simpler until the first customer asks where their prompts are stored.

## Pitfalls
- Breaking trace context at the gateway or a queue; the symptom is cost attributed to "unknown" and
  model spans with no parent, so a slow request cannot be followed end to end.
- Logging prompts as text lines in the application logger; the symptom is customer data in the log
  SaaS, discovered during a retention audit, with no per-tenant redaction possible.
- Using user id or request id as a metric label; the symptom is the metrics database running out of
  memory or the vendor bill multiplying as series count explodes.
- Head sampling at a flat percentage; the symptom is an error-rate alert with no example traces,
  because the failures were in the discarded 90%.
- Mandatory attributes left optional; the symptom is a regression visible in aggregate but not
  attributable to a bundle or prompt version, so rollback becomes guesswork.
- Judge scores stored in the eval tool without the trace id; the symptom is a quality dashboard that
  cannot be joined to the requests it describes.

## When to use
Build the stack, rather than adopting one tool, as soon as a second team, a second model provider or
a compliance requirement appears; before that, a single LLM-tracing tool with OpenTelemetry under it
is enough and migrates cleanly later. Start with tracing through the gateway and the mandatory
attributes, then the collector with redaction, then derived metrics and scores. Measure the stack by
the share of model spend that is attributed to a tenant and feature, the time from an alert to an
example trace, and whether a judge score can be joined to its request. In interviews, be ready to
draw the five layers and justify two choices: why the collector is where redaction and sampling
live, and why payloads are stored separately from spans.

## Role lens
- **Engineer:** Instruments model, retrieval and tool calls with the standard attributes, propagates
  trace context across services, and writes the collector configuration for sampling and redaction.
- **Architect:** Chooses the wire format, the per-signal stores, the retention and redaction policy
  per data classification, and the mandatory attribute set every team must emit.
- **FDE:** Connects the customer's existing APM and SIEM to the stack, agrees which payloads may be
  stored and for how long, and gives the customer a trace view for their own debugging.

## Check yourself
**Q:** Finance says the provider invoice is 30% higher than the sum of what each product team reports.
Compliance separately asks whether any prompt containing a customer's account number was stored.
Which parts of a well-built observability stack answer each question, and what is missing if they
cannot?
**A:** The spend gap is a cost-attribution question answered by spans: every model span carries
tokens, model and tenant and bundle attributes, and the spanmetrics-derived cost series groups by
them. If the sum does not match the invoice, either some spans lack tenant or bundle, which means the
mandatory attributes are not enforced, or some calls bypass the instrumented path, which means keys
exist outside the gateway. The compliance question is answered by the collector's redaction policy
and the payload store: if account numbers are redacted before export for the relevant classification,
the answer is a configuration document plus a query over the payload store for that tenant's
retention window. If prompts were logged as plain log lines in a third-party log service, there is no
reliable answer, which is the failure the stack exists to prevent.

## Quiz
### Q: Why does a platform route telemetry through an OpenTelemetry Collector instead of exporting directly from each service to the backend?
- [ ] The collector compresses traces so storage is cheaper
- [x] Redaction, sampling and fan-out are enforced in one place rather than re-implemented by every team
- [ ] Backends cannot accept connections from many services at once
- [ ] It is the only way to derive metrics from spans
> Each application getting redaction and sampling right independently is the thing that fails. A
> central collector applies policy once and can export the same stream to several backends.

### Q: Why is tail-based sampling preferred over head-based sampling for LLM traces?
- [ ] It uses less memory in the collector
- [ ] It samples a fixed fraction, which keeps costs predictable
- [x] The traces worth keeping (errors, low scores, slow requests) are only identifiable once the trace is complete
- [ ] Head sampling is unsupported by the GenAI semantic conventions
> A decision at the start of a request cannot know it will fail or score badly. Tail sampling waits
> for the whole trace, keeps all of the interesting ones and a small fraction of the rest, at the
> price of buffering.

### Q: Why are prompts and completions stored separately from spans and metrics?
- [ ] Span attributes have a hard size limit of a few bytes
- [x] They are bulky and sensitive, so they need their own retention and access control, referenced from spans by id
- [ ] Metrics databases cannot store strings
- [ ] Analysis tools require a dedicated payload API
> Payloads dominate volume and carry customer data; keeping them in a dedicated store lets the
> platform apply per-tenant retention and access rules without touching the trace or metrics stores.
