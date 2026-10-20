---
id: distributed-tracing-across-services
module: observability
name: Distributed tracing across services
kind: concept
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - observability/tracing
  - observability/spans
  - observability/opentelemetry
  - system-design/ai-gateway
  - agents/model-context-protocol
  - agent-reliability/conversation-execution-identifiers
  - llmops-platform/observability-stack
sources:
  - Sigelman et al., 2010 — Dapper, a Large-Scale Distributed Systems Tracing Infrastructure
  - Official docs: W3C Trace Context
  - Official docs: OpenTelemetry
---

## TL;DR
One user turn in a production LLM system typically crosses several processes: an API gateway, an
orchestrator, a retrieval service, a model gateway, tool servers, perhaps a queue. In-process span
context dies at each boundary, so distributed tracing *propagates* the trace id and parent span id
with every call (the W3C `traceparent` header for HTTP, message metadata for queues) and each service
continues the same tree. Without it you get disconnected fragments and cannot attribute latency or
failure to the hop that actually mattered.

## Why it exists
A tracer keeps the "current span" in a context variable inside one process. The moment the
orchestrator makes an HTTP call to the retrieval service, that context is gone: the retrieval service
starts its own trace with a fresh id, and the model gateway behind it starts another. You now have
three trees describing one request and nothing linking them except approximate timestamps and, if
you are lucky, a user id.

Correlating by timestamp and user fails under exactly the conditions that make you need tracing:
concurrency (the same user has two tabs open), retries (the orchestrator called retrieval twice;
which one fed the answer?), and asynchrony (an agent step was enqueued and executed 40 s later by a
worker that handles messages from hundreds of requests). Grepping logs across five services for a
request id is the usual fallback; it recovers the events but not the structure or the durations, and
it only works if every service logs the same id, which is itself a propagation problem.

LLM systems make the boundaries more numerous, not fewer. Putting model calls behind a gateway for
key management and fallbacks, retrieval behind its own service, and tools behind MCP servers means a
single answer can involve four or five hops before the third-party provider is even reached. If the
trace stops at the first hop, the waterfall shows "orchestrator: 8.2 s" and nothing underneath, which
is precisely the question you were trying to answer.

## How it works
### Context propagation
Propagation has two halves. On the calling side, the tracer *injects* the current span context into
the outgoing request as metadata. On the receiving side, the server *extracts* that context and
starts its server span as a child of the remote parent. Spans are still created locally and exported
independently by each service; the backend reassembles the tree by trace id. Only the ids travel with
the request, *because* the alternative, having services report to a central tracer synchronously,
would put the tracing system on the latency path of every call.

### The wire format
The W3C Trace Context standard defines the HTTP header most systems now use:

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
             ^^ ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ ^^^^^^^^^^^^^^^^ ^^
          version        trace_id (32 hex)      parent span_id    flags (01 = sampled)
```

A second header, `tracestate`, carries vendor-specific key-value pairs. Older systems used Zipkin's
B3 headers; OpenTelemetry can inject and extract both, but every service on a path must agree,
*because* a service that understands only B3 will ignore `traceparent` and start a new trace,
silently cutting the chain.

### Sampling decisions travel too
The `01` flag says "this trace is being sampled". It is set at the root and honoured downstream so a
trace is either complete or absent. If each service sampled independently at 10%, a five-hop trace
would be complete one time in a hundred thousand. Tail-based sampling (decide after the whole trace
has arrived) is done in a collector that buffers spans by trace id, which requires all services to
export to the same collector tier.

### Queues and asynchronous hops
For a message queue there is no response to join on, so the context is injected into message headers
or a metadata field of the payload. The consumer extracts it and starts a `CONSUMER` span. When a
consumer processes a batch from many producers, strict parent-child does not fit; the batch span
carries *links* to each message's producer span instead. For long gaps (an agent step scheduled for an
hour later) teams usually still continue the trace but also record a durable execution id, *because*
trace backends retain data for days while the workflow may live for weeks.

### Where LLM systems break the chain
- **Model and AI gateways.** A proxy in front of providers must extract the incoming context, inject
  it on the outbound call, and emit its own span recording provider, model, retry and fallback
  decisions. A gateway that does not is a black hole in every trace.
- **Tool servers over MCP.** Tool calls leave the orchestrator's process. For HTTP-based transports,
  standard header propagation applies if both sides are instrumented. MCP requests also carry a
  `_meta` object intended for metadata, which implementations can use to pass trace context; whether
  a given SDK does this automatically varies by version, so verify rather than assume.
- **Third-party providers.** The provider will not continue your trace, so the client span is the
  leaf. Record the provider's request identifier from the response headers (providers return one
  under names such as `request-id` or `x-request-id`) as an attribute, *because* it is the only handle
  their support team can use when you report a slow or failed call.
- **Thread pools.** Not a network hop, but the same failure: Python does not copy context into
  executor threads by default (asyncio tasks do copy it at creation). Wrap submitted callables with
  `contextvars.copy_context().run` or use an instrumented executor.

### Baggage: carrying more than ids
W3C Baggage is a companion header for application-defined key-values (tenant, feature flag,
experiment arm) that travel with the trace so a downstream service can tag its spans and logs with
them. Use it sparingly: baggage is forwarded on *every* outbound call unless filtered, including calls
to third-party APIs, so a tenant name or user email in baggage is a data leak by default.

### Clock skew
Each service timestamps with its own clock. A child span that starts before its parent, or a negative
network gap, is the symptom. Keep hosts under NTP or equivalent; most trace viewers do not correct for it.

## Example
The same two primitives, `inject` and `extract`, cover HTTP and queues; only the carrier changes.
Auto-instrumentation for FastAPI, httpx or requests performs exactly these calls for you; they are
written out here so the mechanism is visible, including the queue hop where the carrier is a field in
the message rather than an HTTP header.

```python
from opentelemetry import trace
from opentelemetry.propagate import inject, extract
import httpx

tracer = trace.get_tracer("orchestrator")

# calling side (orchestrator)
def call_retrieval(query: str) -> dict:
    with tracer.start_as_current_span("retrieval.client",
                                      kind=trace.SpanKind.CLIENT) as span:
        headers = {}
        inject(headers)                        # adds traceparent (+ tracestate, baggage)
        span.set_attribute("retrieval.query_chars", len(query))
        r = httpx.post("http://retrieval:8000/search",
                       json={"q": query, "k": 5}, headers=headers, timeout=5)
        r.raise_for_status()
        return r.json()

# receiving side (retrieval service request handler)
def handle_search(request_headers: dict, body: dict) -> dict:
    ctx = extract(request_headers)             # remote parent becomes the context
    with tracer.start_as_current_span("retrieval.server", context=ctx,
                                      kind=trace.SpanKind.SERVER) as span:
        hits = [{"id": 11, "score": 0.81}, {"id": 42, "score": 0.77}]   # stand-in
        span.set_attribute("retrieval.hit_ids", [h["id"] for h in hits])
        return {"hits": hits}

# queue hop: inject into the message instead of HTTP headers
def enqueue_agent_step(queue, step: dict):
    carrier = {}
    inject(carrier)
    queue.put({"step": step, "trace": carrier})   # consumer: extract(msg["trace"])
```

## Trade-offs
- A standard header (W3C) interoperates with every vendor and language, but migrating a fleet that
  already uses B3 means a period of dual propagation; vendor formats win only when a legacy system
  cannot be changed.
- Head-based sampling keeps propagation trivial (one bit in the header) and needs no buffering, but
  it drops slow and failed traces at the same rate as healthy ones; tail-based sampling keeps the
  interesting traces at the cost of a collector tier that buffers every span.
- Auto-instrumentation of HTTP clients and servers gives propagation for free, but it is invisible,
  so when a hop silently loses context nobody knows where to look; manual inject and extract at
  service boundaries is more work and far easier to audit.
- Baggage lets every service tag spans and logs with tenant and experiment without changing APIs, but
  it leaks to every downstream call by default; it flips to explicit API fields once third-party calls
  are in the path.

## Pitfalls
- A gateway, load balancer or service mesh that strips unknown headers; the symptom is traces that
  always end at the same hop, while the downstream service shows a matching single-span trace with a
  fresh id.
- Mixed propagators (one service on B3, another on W3C); the symptom is the same break but only on
  some paths, which is maddening until you compare the header names each side expects.
- Submitting work to executor threads without copying context; the symptom is orphan traces that
  start milliseconds after a gap in the real trace.
- Putting user identifiers or emails into baggage; the symptom is a security review finding them in
  outbound headers to a third-party model provider.
- Each service sampling independently; the symptom is traces that are complete for short paths and
  almost never complete for deep ones.
- Treating a child-before-parent waterfall as evidence of a bug; the symptom is minutes lost before
  someone checks the hosts' clocks.

## When to use
Propagate trace context across every internal process boundary from the start; the cost is a few
lines or an auto-instrumentation package, and retrofitting it after you have a dozen services is slow
and political. It becomes essential the moment a model gateway, retrieval service, tool server or
queue enters the path, which describes almost every production LLM system. Do not expect propagation
into third-party providers; capture their request ids instead. Measure the fraction of traces whose
root is an edge service and whose leaf reaches the provider call, broken down by path; that number is
your propagation health. In interviews, be ready to sketch the `traceparent` flow across three
services and a queue, explain why the sampling flag must travel, and name the two places LLM systems
usually lose the chain: the gateway and the tool server.

## Role lens
- **Engineer:** Adds inject and extract at every client and server boundary, instruments the queue
  carrier, copies context into executors, and records provider request ids on the leaf span.
- **Architect:** Mandates one propagation format and one collector tier across services, decides head
  versus tail sampling, and writes the baggage policy that keeps tenant data out of outbound headers.
- **FDE:** Verifies that trace context survives the customer's own gateways, meshes and tool servers
  during integration, and uses the complete cross-service waterfall to settle "whose latency is it"
  disputes.

## Check yourself
**Q:** Your traces consistently show the orchestrator's `tool.call` span taking 3–6 seconds, but the
MCP tool server's own traces show every request finishing in under 200 ms, each as a separate
single-span trace. Diagnose the situation and describe the fix.
**A:** Two trees for one request means context is not crossing the boundary: the tool server is
extracting nothing and starting fresh traces. First check what the orchestrator injects (HTTP headers
for an HTTP transport, or the request metadata for MCP) against what the server extracts; a
propagator mismatch (B3 versus W3C) or a proxy stripping headers produces exactly this. Once the
server span is a child of the client span, the gap between them is network and queueing time, which
is where the missing 3–6 s will appear, most likely in the transport (connection setup, a serial pipe,
a connection-pool limit) rather than in tool execution. Add a span around the transport layer, record
the tool request id for correlation, and confirm the fix by watching the fraction of orchestrator tool
spans that have a server child climb towards 100%.

## Quiz
### Q: What does the sampled flag in `traceparent` prevent when every downstream service honours it?
- [ ] Clock skew between hosts
- [x] Traces that are partially recorded because each service made its own independent sampling decision
- [ ] Duplicate span ids across services
- [ ] Header stripping by load balancers
> If each hop sampled independently, deep traces would almost never be complete. Carrying the decision
> from the root makes a trace all-or-nothing, which is what makes cross-service waterfalls trustworthy.

### Q: A worker consumes a batch of twenty queued agent steps that came from twenty different user requests. How should its span relate to them?
- [ ] Make the batch span a child of the first message's producer span
- [ ] Start a fresh trace with no relation; batching breaks tracing
- [x] Create one consumer span with links to each producer span, since a span can have only one parent
- [ ] Create twenty parent spans for the single consumer span
> Parent-child is one-to-one and implies containment. Fan-in is many-to-one, and links exist for
> exactly this case so each original request can still find the batch that processed it.

### Q: Why should a call to a third-party model provider be treated as the leaf of a trace, and what should be recorded there?
- [ ] Because provider calls are too fast to need spans
- [ ] Because trace ids cannot be sent over HTTPS
- [x] Because the provider will not continue your trace; record its returned request id so the call can be correlated with the provider's own records
- [ ] Because providers reject requests that carry a traceparent header
> You cannot see inside the provider, but their support can if you give them the identifier they
> returned. Capturing it on the leaf span turns "the API was slow" into something they can investigate.
