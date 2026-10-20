---
id: spans
module: observability
name: Spans
kind: concept
difficulty: core
est_minutes: 12
status: reviewed
related:
  - observability/tracing
  - observability/distributed-tracing-across-services
  - observability/opentelemetry
  - observability/latency-percentiles
  - node-ts-for-ai/opentelemetry-instrumentation
  - serving-inference/ttft
sources:
  - Official docs: OpenTelemetry
---

## TL;DR
A span is the unit a trace is built from: one named operation with a start time, an end time, a
parent, a status and a set of attributes, plus optional timestamped events. Traces are only as useful
as their spans, so the real skill is choosing span boundaries that match the questions you will ask,
and recording the attributes (model, tokens, chunk ids, tool arguments) that let a span be filtered,
aggregated and understood on its own.

## Why it exists
Once you accept that a request should be recorded as a tree, you need a unit for the nodes. That
unit has to do three jobs at once. It must carry *timing*, so durations can be summed and compared.
It must carry *structure*, a parent reference, so trees can be assembled from pieces emitted by
different code paths, threads or services that never see each other. And it must carry *meaning*:
enough attributes that a span can be found and interpreted without reading the code that emitted it.

The naive units each fail one of those jobs. A single span per request has timing but no
attribution: you learn the turn took 6 s and nothing about why. A span per function call, as a
profiler would produce, has structure but no meaning: hundreds of nameless frames, none of which say
"this was the reranker" or "this generation hit max tokens". Plain log lines have meaning but neither
duration nor a reliable parent.

LLM applications sharpen the requirement because the operations that matter are not functions in
the profiler sense. "One generation", "one retrieval", "one tool call", "one agent iteration" are
semantic units whose interesting properties (which model, how many tokens, which documents, what
arguments, why it stopped) are data, not code locations. Spans are the container that lets you attach
that data to a timed, nested operation, and the conventions around them are what let spans from
different teams and libraries land in one backend and still be comparable.

## How it works
### Anatomy of a span
Every tracing system converges on roughly the same fields; OpenTelemetry's names are used here
because they are the lingua franca.
- `trace_id` (128-bit) shared by the whole request, and `span_id` (64-bit) unique to this span.
- `parent_span_id`, empty for the root. This single field turns a bag of spans into a tree.
- `name`: a low-cardinality label for the *operation*, such as `generation`, `retrieval.hybrid`,
  `tool.get_order`. Low cardinality *because* names are what you group and compare by; a name that
  embeds a user id makes every span unique and aggregation impossible.
- `start_time`, `end_time`: duration is derived, never stored. Clock skew between services therefore
  shows up as children that appear to start before their parent.
- `kind`: `INTERNAL`, `CLIENT`, `SERVER`, `PRODUCER`, `CONSUMER`. It tells the backend which side of a
  network call the span represents, so the gap between a client span and its matching server span can
  be read as network and queueing time.
- `status`: unset, `OK`, or `ERROR` with a description. Recording an exception on a span does not by
  itself mark it failed; you set the status, *because* a caught, retried failure inside an otherwise
  successful operation should not paint the operation red. OpenTelemetry's convention is that
  libraries leave a successful span `UNSET` and only application code asserts `OK`, so backends treat
  "not error" as success.
- `attributes`: key-value pairs with primitive or array values. This is where LLM semantics live.
- `events`: timestamped points inside the span, each with its own attributes, for things that happen
  *during* an operation but are not operations themselves: first token received, retry attempted,
  cache hit decided.
- `links`: references to spans in other traces, for fan-in cases such as a batch job that processes
  messages originating from many requests.

### Lifecycle and context
A span is started, becomes "current" for the code running beneath it, is enriched, and is ended
exactly once. "Current" lives in a context (a `ContextVar` in Python, `AsyncLocalStorage` in Node) so
a child started anywhere downstream can discover its parent. The end must happen on every exit path,
which is why idiomatic APIs are context managers or decorators: a span that is never ended never
exports, and the trace then looks truncated at exactly the step that failed, the one you most wanted.

Ending a span freezes it. Attributes set afterwards are dropped (the SDK at most logs a warning),
which bites streaming code hardest: if a function opens a span, starts a provider stream and returns
the iterator, the span ends when the function returns, milliseconds in, while the tokens, the
first-token time and the usage all arrive later during iteration. The span must stay open until the
stream is exhausted, so wrap the iteration itself (a generator that holds the span) rather than the
call that creates the stream.

Finished spans go to a *span processor*. A simple processor exports synchronously as each span ends,
which is fine for a demo and puts the exporter's network call on your request path in production. A
batch processor queues ended spans and exports them in the background, *because* tracing must never
add the backend's latency to the user's; the cost is that its queue is bounded, so under overload or a
dead backend it drops spans rather than block, and an abrupt process exit loses whatever was still
queued unless the provider is shut down (flushed) on exit.

### Attributes for LLM spans
What to record follows from the questions you will ask. For a generation span: provider, model id,
request parameters (temperature, max tokens, tool choice), input and output token counts, finish
reason, time to first token as an event, and the prompt and completion either inline or as a
reference to a blob. For retrieval: the query, the index name, top-k, the returned ids and scores. For
a tool call: tool name, the arguments as the model produced them, result size and status, and whether
an approval gate fired. OpenTelemetry publishes generative-AI semantic conventions (attribute names
prefixed `gen_ai.`) that standardise these; at the time of writing they were still marked
experimental, so check the exact names against the current specification rather than memorising them.

### Events versus child spans
If the thing has a duration you care about on its own, make it a child span; if it is an instant,
make it an event. "Waiting for the first token" is a duration, but it is part of the generation, so
most teams record a `first_token` event on the generation span and derive TTFT as event time minus
span start. A retry is normally a new child span *because* you want its own status and duration.

### Size and cardinality
Attributes are indexed and sometimes turned into metrics; events and payloads are stored but rarely
indexed. Putting a 40 KB prompt into an attribute is tolerated by LLM-focused backends and punished by
general-purpose ones. Putting a free-text value into a *name*, or into any label used for
aggregation, is punished everywhere. The rule: identifiers and enums as attributes, bulky text as
payload or by reference, nothing per-user in names.

## Example
A streaming generation instrumented with the OpenTelemetry SDK. The console exporter prints two
spans sharing one `trace_id`; the `generation` span has the root's id as its parent, carries model
and usage attributes, and holds a `first_token` event from which TTFT can be derived. The span is
held open across the whole iteration of the stream, which is why its duration and usage are real. The attribute
names follow the `gen_ai.` conventions; verify them against the current spec before relying on them.
`start_as_current_span` already records uncaught exceptions and sets the error status; the explicit
handler is there to show the mechanism.

```python
import time
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode

provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))  # demo; Batch in prod
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("support-bot")

def fake_stream():                        # stand-in for a streaming model call
    time.sleep(0.15); yield "Your"        # first token after ~150 ms
    for w in (" order", " shipped."):
        time.sleep(0.02); yield w

def generate(prompt):
    with tracer.start_as_current_span("generation") as span:
        span.set_attribute("gen_ai.request.model", "example-model")
        span.set_attribute("gen_ai.request.temperature", 0.2)
        span.set_attribute("app.prompt_ref", "s3://prompts/7f3a")   # by reference, not inline
        out, first = [], True
        try:
            for tok in fake_stream():
                if first:
                    span.add_event("first_token"); first = False
                out.append(tok)
            span.set_attribute("gen_ai.usage.output_tokens", len(out))  # real code: provider usage
            span.set_attribute("gen_ai.response.finish_reasons", ["stop"])
            span.set_status(Status(StatusCode.OK))
            return "".join(out)
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

with tracer.start_as_current_span("chat.turn") as root:
    root.set_attribute("app.tenant", "acme")
    print(generate("Where is my order?"))
```

## Trade-offs
- Fine-grained spans give precise attribution, but each span costs export bandwidth and storage and
  adds a little overhead; it flips to coarser spans when the operation is sub-millisecond or runs
  thousands of times per request (tokenisation, per-chunk scoring).
- Inline prompts and completions make a span self-contained for debugging, but they bloat the trace
  store and spread PII; it flips to by-reference storage once volume or a compliance review makes the
  trace backend the largest copy of user data you hold.
- Rich attributes make spans filterable and aggregatable, but every attribute you index costs backend
  resources; it flips when an attribute's cardinality approaches the number of requests, at which
  point the value belongs in the payload, not the index.
- Events are cheaper than child spans and keep the tree readable, but they have no duration or status
  of their own; it flips to a child span the moment you need to chart or alert on that sub-step's
  latency or failure rate.

## Pitfalls
- Ending spans only on the happy path; the symptom is traces that stop exactly at the failing step,
  so the span you needed most is missing rather than marked as an error.
- Starting a child span in a thread-pool worker, or in a background task created before the parent
  span became current, without carrying context; the symptom is a single-span trace with no parent
  appearing at the same moment the real trace shows an unexplained gap.
- Encoding dynamic values in span names (`tool.get_order_88213`); the symptom is a backend listing
  thousands of distinct operations with one data point each, so no latency chart is possible.
- Recording an exception without setting the status, or the reverse; the symptom is error-rate
  dashboards that disagree with the exceptions visible in the trace detail.
- Wrapping the function that *creates* a provider stream instead of the loop that consumes it; the
  symptom is generation spans lasting a few milliseconds with no usage or first-token event while the
  root span takes seconds.
- Shipping the simple (synchronous) span processor to production; the symptom is request latency that
  rises whenever the trace backend is slow, and requests that hang when it is down.
- Capturing the prompt *template* instead of the rendered prompt; the symptom is a span that cannot
  reproduce the model's behaviour because the variables, truncation and history are missing.

## When to use
Create a span whenever a unit of work has a duration, a success or failure outcome, or data you will
want to filter by later; in an LLM app that is every model call, retrieval, reranking pass, tool call
and agent iteration, plus the user-facing turn as the root. Do not create spans for pure functions
that take microseconds, or for loops with thousands of iterations; use one span with counts in its
attributes. Measure spans per trace and bytes per span to keep the trace store honest, and check that
`status=ERROR` on spans agrees with your error metrics. Interviewers probe this by asking what
attributes you would put on a generation span and why, and whether a retry should be an event or a
child span; the answer should be reasoned from the questions you will ask of the data.

## Role lens
- **Engineer:** Writes the `start_as_current_span` blocks, decides what goes in attributes versus events
  versus by-reference payloads, and makes sure every exit path ends the span with the right status.
- **Architect:** Defines the span naming and attribute conventions for the organisation (which keys,
  which cardinality rules, which payload policy) so spans from different teams aggregate and join in
  one backend.
- **FDE:** Reads customer spans to pinpoint whether a slow or wrong answer came from retrieval, the
  model or a tool, and adds the missing attribute when a customer question cannot be answered from the
  existing ones.

## Check yourself
**Q:** You are instrumenting a streaming generation that may retry once on a provider overload error.
Should time to first token, the retry, and the final token usage be attributes, events or child
spans, and what status should the parent carry if the retry succeeds?
**A:** Time to first token is a point in time inside the generation, so record a `first_token` event
on the generation span and derive TTFT from the span start; storing it also as a numeric attribute is
fine for querying. Each attempt has its own duration, outcome and provider request id, so attempts
should be child spans (`generation.attempt`): the first marked `ERROR` with the overload recorded,
the second `OK`. The parent generation span aggregates: total duration, the final usage tokens as
attributes taken from the successful attempt, an `attempts=2` attribute, and status `OK` *because*
the operation the caller asked for succeeded. Marking the parent as an error would make the error
rate count a recovered failure as user-visible, while hiding the retry entirely would leave the
latency unexplainable.

## Quiz
### Q: What single field turns a set of independently emitted spans into a trace tree?
- [ ] The span name
- [x] The parent span id, together with the shared trace id
- [ ] The start timestamp
- [ ] The span kind (CLIENT or SERVER)
> The trace id groups spans into one request and the parent id gives each its position in the tree;
> names and timestamps describe spans but cannot establish containment, especially across services.

### Q: Why should span names be low-cardinality, with identifiers moved into attributes?
- [ ] Attribute storage is cheaper per byte than name storage
- [ ] Names are limited to 32 characters in most backends
- [x] Names are the grouping key for latency and error aggregation; unique names make every span its own series
- [ ] Attributes are encrypted while names are not
> Backends chart and compare operations by name. If each span's name embeds an order id or user id,
> you get thousands of one-sample operations and no usable p95 or error rate per operation.

### Q: A retrieval span shows status OK, yet its attributes record zero returned chunks and the final answer was wrong. What does this illustrate?
- [ ] The span should have been an event instead
- [x] Status reflects whether the operation executed, not whether its result was useful; semantic quality must be checked from attributes or evals
- [ ] The tracer dropped the error because of sampling
- [ ] Retrieval spans cannot carry a status
> Span status is about execution failure. An empty retrieval that completed normally is OK at the
> mechanics level; detecting that it was semantically bad requires reading recorded attributes or
> scoring the trace, which is why traces feed evals.
