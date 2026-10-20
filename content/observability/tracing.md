---
id: tracing
module: observability
name: Tracing
kind: concept
difficulty: intro
est_minutes: 13
status: fact-checked
related:
  - observability/spans
  - observability/distributed-tracing-across-services
  - observability/opentelemetry
  - observability/langfuse
  - agent-reliability/audit-logs-execution-traces
  - evals/error-analysis
sources:
  - Sigelman et al., 2010 — Dapper, a Large-Scale Distributed Systems Tracing Infrastructure
  - Official docs: OpenTelemetry
---

## TL;DR
A trace is the complete, timed record of one request as it moves through an LLM application: the
user turn at the root, and under it every retrieval, model call, tool call and retry as nested spans.
It exists because LLM systems fail *semantically* (a wrong answer, a bad tool choice) rather than by
crashing, so you need to see exactly what each step received and produced, in order and with timing.
Logs tell you something happened; a trace tells you what caused what on this request.

## Why it exists
Think about debugging a complaint like "the assistant told the customer their order shipped, but it
hadn't". Nothing threw. HTTP 200 everywhere. Somewhere in a chain of six steps (rewrite the query,
retrieve eight chunks, rerank, call a tool for order status, generate, run an output guardrail) one
step produced something plausible but wrong, and every later step faithfully built on it. To find it
you need, for that one request, the inputs and outputs of each step and the order they ran in.

The naive alternative is log lines. They fail in three ways. First, they are flat: under concurrency,
lines from hundreds of requests interleave, and reconstructing "which retrieval fed which generation"
from timestamps is guesswork. Second, they are lossy by default: people log "calling model" but not
the rendered prompt the model actually saw after templating, truncation and history compaction.
Third, they have no notion of duration or hierarchy, so "the request took 9 s" cannot be decomposed
into "7.8 s of it was one tool call that retried three times".

A trace fixes all three by construction: every step is a span with a start, an end, a parent and a
bag of attributes, and all spans of one request share a trace id. That gives you causality, timing
attribution and the payloads, scoped to the request. Metrics and logs are still needed; the trace is
what links them to a single story.

## How it works
### The data model
A trace is a tree. The root span is the unit of user-visible work (one chat turn, one agent task).
Children are the steps. Each span carries a `trace_id` shared by the whole tree, its own `span_id`,
a `parent_span_id` (empty for the root), a name, start and end timestamps, a status, and attributes
(key-value pairs). The spans lesson goes into each field; here the point is the tree itself. It is a
tree *because* latency and failure only make sense relative to a parent: a 4 s model call is fine if
the user-visible turn budget is 10 s and a disaster if that turn was supposed to be a 500 ms
autocomplete.

### What LLM-specific spans record
Generic tracing records "HTTP GET /orders 200 in 120 ms". That is not enough here, because the
interesting failures are in the content. An LLM trace typically has span kinds such as:
- **generation**: model id, parameters (temperature, max tokens), the rendered prompt or a reference
  to it, the completion, finish reason, input and output token counts, time to first token.
- **retrieval**: query text, top-k, the ids and scores of the returned chunks, which index.
- **tool**: tool name, the arguments the model produced, the result (or its size and a hash), error.
- **agent step**: the iteration number, the decision taken, the stopping reason.

Capturing payloads is the design choice that separates LLM tracing from classic APM, and it is also
where cost, size and privacy problems come from. Most teams capture inputs and outputs by reference
(store the blob, trace the pointer) or sample full payload capture, keeping 100% of failures.

### How spans get created
Three mechanisms, usually combined:
1. **Manual instrumentation**: a context manager or decorator around each step. Explicit, precise,
   and you choose the boundaries.
2. **SDK wrapping / auto-instrumentation**: a library patches the provider client (`openai`,
   `anthropic`, `httpx`) so every model call becomes a span with token counts filled in. Zero effort,
   but you get only what the wrapper thought to record.
3. **Framework callbacks**: LangChain, LangGraph, LlamaIndex and similar emit events per node that a
   tracer subscribes to. Convenient, but span boundaries follow the framework's structure, not your
   mental model of the pipeline.

### Context propagation
The current span must be findable by whatever code runs next, without threading it through every
function signature. In Python this is a `contextvars.ContextVar`; in Node it is `AsyncLocalStorage`.
A child span reads the current span to set its parent, then makes itself current until it ends.
This design exists *because* the alternative, passing a span object everywhere, does not survive
library code you do not own. It is also the mechanism that breaks when you hop thread pools or cross
a network boundary; the distributed-tracing lesson covers that.

### Sampling, export and storage
Recording every span of every request is expensive at volume, so SDKs support sampling: decide at
the root (head-based) and carry the decision to children so a trace is all-or-nothing, or keep
everything briefly and decide after the fact (tail-based) so you can keep every error and slow
request. Spans are batched and exported asynchronously to a backend (an OpenTelemetry collector,
Langfuse, LangSmith, Phoenix, a generic APM) that indexes by trace id, time and attributes and
renders the waterfall view.

### Traces as a data asset
Because a trace holds the exact inputs and outputs of each step, traces double as raw material for
evaluation: a bad trace becomes a regression test case, and a sampled set of traces becomes the
dataset an online LLM-judge scores. This is why LLM observability tools expose "add to dataset"
straight from the trace view, and why error analysis usually starts by reading traces.

## Example
The entire mechanism in about thirty lines: a `span` context manager that discovers its parent
through a context variable, records timing and attributes, and appends itself to an in-memory
exporter. Nothing is passed between `answer`, the retrieval step and the generation step; the parent
is found through the context variable, which is exactly what production SDKs do.

```python
import time, uuid, contextvars
from contextlib import contextmanager

_current = contextvars.ContextVar("current_span", default=None)
SPANS = []  # production code hands finished spans to an exporter instead

@contextmanager
def span(name, **attrs):
    parent = _current.get()
    s = {"trace_id": parent["trace_id"] if parent else uuid.uuid4().hex,
         "span_id": uuid.uuid4().hex[:16],
         "parent_id": parent["span_id"] if parent else None,
         "name": name, "attrs": attrs, "start": time.perf_counter()}
    token = _current.set(s)
    try:
        yield s
        s["status"] = "ok"
    except Exception as e:
        s["status"] = f"error: {type(e).__name__}"; raise
    finally:
        s["end"] = time.perf_counter(); _current.reset(token); SPANS.append(s)

def answer(question):
    with span("chat.turn", question=question):
        with span("retrieval", top_k=4) as r:
            chunks = ["order #881 shipped 2 May", "returns take 5 days"]  # stand-in hits
            r["attrs"]["chunk_ids"] = [11, 42]
        with span("generation", model="example-model") as g:
            prompt = f"Context: {chunks}\nQ: {question}"
            g["attrs"].update(prompt_chars=len(prompt), input_tokens=312, output_tokens=48)
            return "Your order shipped on 2 May."       # stand-in completion and usage

answer("Where is my order?")
for s in sorted(SPANS, key=lambda s: s["start"]):
    indent = "" if s["parent_id"] is None else "  "
    print(f'{indent}{s["name"]:<12}{(s["end"]-s["start"])*1000:7.2f} ms  {s["attrs"]}')
```

## Trade-offs
- Full payload capture makes traces debuggable on their own, but it multiplies storage by prompt size
  and creates a PII store; it flips to reference-only capture once volume or a compliance review makes
  the trace backend a liability.
- Auto-instrumentation gets coverage in an afternoon, but span boundaries follow the library's
  structure; it flips to manual instrumentation when the question you keep failing to answer lives
  between the auto-generated spans (for example, "what did we do with the tool result?").
- Head-based sampling is cheap and keeps traces complete, but it discards slow and failed requests at
  the same rate as healthy ones; tail-based sampling keeps the interesting minority at the price of
  buffering every span until the trace ends.
- Tracing every step adds a little latency and a background export thread; negligible around a 3 s
  model call, noticeable if you wrap a function called ten thousand times per request.

## Pitfalls
- Tracing the model call but not prompt construction; the symptom is a trace showing a clean call
  with a prompt nobody can reproduce because templating, truncation or compaction happened untraced.
- Starting spans in a thread pool or background worker without copying context; the symptom is
  orphan traces with a single span, and a root that appears to have done nothing for seconds.
- Treating the trace store as the audit log; traces are sampled and retained for days, so the symptom
  is a compliance question you cannot answer three months later.
- Recording raw prompts or free-text user identifiers as indexed attributes; the symptom is a backend
  bill or cardinality warnings, followed by a privacy review that halts the project.
- Reading traces only when something breaks; teams that never sample healthy traces miss slow drift
  in prompt size, tool usage and retry rates until the cost or latency graph moves.

## When to use
Trace from the first day you have more than one step between the user and the model, which in
practice means every RAG or agent system. Do it before you have an incident, because the decision of
what to capture is hard to retrofit. Reach for a trace whenever the question is about one request:
why was this answer wrong, where did these 9 seconds go, which tool call did the agent repeat. Do not
use traces to answer "how often" questions; that is monitoring over aggregated span data. Measure
trace completeness (fraction of requests with a root span and all expected children) and the export
drop rate. In interviews, the expected depth is being able to sketch the span tree for a RAG or agent
request and say what each span should record and why.

## Role lens
- **Engineer:** Wraps retrieval, generation and tool steps in spans, decides which payloads to capture
  by value versus by reference, and uses the waterfall to find where latency and bad answers originate.
- **Architect:** Chooses the tracing backend and data model, sets the payload retention and redaction
  policy, and makes trace context a required part of every internal service contract.
- **FDE:** Uses a trace to show a customer exactly why an answer was wrong (the retrieved chunk, the
  tool output, the prompt), turning a trust argument into evidence, and exports the bad trace as a
  regression case.

## Check yourself
**Q:** An agent-based support bot occasionally gives a confident wrong answer with no errors in the
logs. You already have request-level logs and a latency dashboard. What would tracing add, and how
would you design the spans?
**A:** Logs and dashboards show that a request completed and how long it took; neither shows the
intermediate content that produced the wrong answer. A trace gives one tree per turn: a root span for
the turn, children for query rewriting, retrieval (with chunk ids and scores), each tool call (with
the arguments the model produced and the raw result), each generation (with the rendered prompt,
model, parameters, token counts and finish reason) and the guardrail decision. With that, a bad
answer is diagnosed by walking the tree: was the retrieved context wrong, did the model misread a
correct tool result, or was the final prompt missing context because compaction dropped it? I would
capture large payloads by reference, sample 100% while the bot is new, move to tail-based sampling
later so every flagged or slow turn is kept, and feed bad traces into the eval set.

## Quiz
### Q: Why is a trace organised as a tree of spans rather than an ordered list of events?
- [ ] Trees compress better in storage than flat event lists
- [x] Latency and failure must be attributed to a parent step; the tree records which step caused or contained which
- [ ] Models produce hierarchical output, so the trace must mirror it
- [ ] Ordered lists cannot carry timestamps
> Attribution is the point: a 4 s generation only means something relative to the turn that contained
> it, and a failed tool call only matters through the step that depended on it. A flat list loses
> that containment.

### Q: Which failure mode do logs handle worst compared with traces in an LLM application?
- [ ] Recording that a provider returned HTTP 429
- [ ] Capturing a stack trace for an unhandled exception
- [x] Reconstructing which retrieved chunks fed which generation under concurrent traffic
- [ ] Measuring total request count per minute
> Logs record events well; what they lack is request-scoped structure. Under concurrency, interleaved
> lines from many requests make the causal chain between steps guesswork, which is exactly what the
> shared trace id and parent links provide.

### Q: A team enables auto-instrumentation of their model client and declares tracing done. What is the most likely gap?
- [ ] Token counts will be missing from the spans
- [ ] The traces will not have a trace id
- [x] Prompt construction, retrieval and tool handling run outside the wrapped client, so the trace shows the call but not what produced its input
- [ ] Auto-instrumentation cannot run in async code
> Client wrappers see the request as sent. The templating, truncation, retrieval and tool-result
> handling that decide what goes into that request happen before it and stay invisible unless
> instrumented manually.
