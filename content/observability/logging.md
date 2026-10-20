---
id: logging
module: observability
name: Logging
kind: practice
difficulty: intro
est_minutes: 12
status: fact-checked
related:
  - observability/tracing
  - observability/monitoring
  - python-for-ai/logging-structlog
  - fastapi-for-ai/structured-logging
  - agent-reliability/audit-logs-execution-traces
  - safety-security/pii-redaction
  - data-architecture/data-retention-and-deletion-architecture
sources:
  - Official docs: structlog
  - Official docs: OpenTelemetry
---

## TL;DR
Logging records discrete events as structured, searchable records: timestamp, level, event name, the
trace and request ids that tie each line to the rest of the system, and event-specific fields. In LLM
apps, logs hold what traces and metrics should not: rendered prompts, raw completions before parsing,
tool arguments and results, guardrail decisions and provider errors, redacted and retained by policy.
The design work is deciding what to log, at which level, in what shape, and how to bind it to the
request.

## Why it exists
A model can return a perfectly formed response that is wrong, truncated or in the wrong format, and
nothing in your process will raise. Two weeks later a customer forwards the screenshot. To work out
what happened you need the exact prompt the model saw (after templating, history compaction and
truncation), the model and parameters, the raw text that came back before your JSON parser touched
it, which tool was called with which arguments, and what the guardrail decided. Traces give you the
structure of that request, but they are usually sampled, retained briefly and deliberately light on
payloads; metrics give you counts. Logs are the durable, searchable record of the events and their
content.

The naive alternative is `print` or free-text `logger.info(f"calling model for user {uid}")`. It
fails predictably. Free text is not queryable: "find every request where the output failed to parse
and the model was X" becomes a regex over terabytes. Lines carry no request context unless every call
site remembers to add it, so correlating them with a trace or across services is luck. Payloads end up
logged in full at every level, so volume and PII exposure explode together, and the first compliance
review turns logging off entirely, which is the worst outcome.

Structured logging with bound context solves this: every line is a JSON object with a fixed set of
correlation fields injected automatically, event-specific fields added explicitly, and a processing
pipeline that redacts, samples and routes before anything is written.

## How it works
### Shape: one event, one object
Each record is a flat object: `timestamp`, `level`, `event` (a short, stable name such as `llm.call`
or `tool.result`), correlation ids (`trace_id`, `span_id`, `request_id`, and the conversation or
execution id), tenant and user identifiers as opaque ids, then the event's own fields. Fixed names
matter *because* queries, dashboards and alerts are written against field names; a field that is
`latency_ms` in one service and `duration` in another is two queries forever. JSON Lines is the usual
encoding: one object per line, append-only, trivially shippable.

### Binding context once
The correlation fields are bound to the request context at the edge (middleware extracts or creates
the request id and reads the current trace id) and merged into every subsequent record automatically
by a context-variable processor. Call sites log only what is specific to their event. This is the most
important mechanical choice, *because* it makes correlation independent of discipline: a line logged
deep inside a retry helper still carries the ids.

### Levels mean something
- `DEBUG`: full payloads (rendered prompt, raw completion, tool results). Off in production by
  default; switchable per tenant or per request for live debugging.
- `INFO`: one record per meaningful event with sizes, ids and outcomes but not the bulky content:
  `llm.call` with model, token counts, latency, finish reason and prompt hash; `retrieval.done` with
  hit ids; `tool.call` with tool name and an argument hash.
- `WARNING`: degraded but handled: a retry on rate limiting or overload, a fallback to another model,
  output repaired after a parse failure, a guardrail triggered.
- `ERROR`: the request failed for the user, with the provider's error type and request id.

The reason to put the *raw* model output in the parse-failure warning, rather than the parsed object
in the INFO line, is that you can always re-parse raw text but you can never recover raw text from a
failed parse.

### Payloads: by reference, hashed, or sampled
Prompts and completions are large (tens of kilobytes per call once context is stuffed) and sensitive.
Three techniques, usually combined: store the payload in an object store keyed by request id and log
the key; log a content hash so identical prompts can be grouped and the template version identified;
sample full-payload logging at a small rate plus 100% of errors and flagged responses. Illustrative
arithmetic: one million calls a day at 20 KB each is 20 GB of payload per day before indexing, which
is why "just log everything" ends at the first invoice.

### Redaction in the pipeline, not at call sites
A processor in the logging pipeline scrubs known secret patterns (API keys, bearer tokens), masks PII
fields by name, and truncates oversized values. It is centralised *because* a call site that forgets
is otherwise a leak, and because the rules change (a newly regulated field) more often than the code.

### The canonical log line
A pattern worth borrowing from high-volume web services: at the end of each request emit one wide
record that summarises everything, such as route, tenant, model, total tokens, cost, latency breakdown,
retries, tools used, guardrail result and outcome. Most "what kinds of requests do X" questions become
a query over this single table, with the per-event lines kept for drilling in.

### Where logs sit relative to traces and metrics
Traces give structure and timing for a sampled subset, logs give events and content for all requests,
metrics give aggregates cheaply. The join key is the trace id (and span id) on every log line.
OpenTelemetry's logs signal exists precisely to carry that correlation and to ship logs through the
same collector as spans.

### Retention and deletion
Logs with payloads are user data. Define retention tiers (hot and searchable for a short window, cold
archive for compliance, then deletion) and make deletion requests executable, which is only possible
when records are keyed by tenant and user ids.

## Example
A structlog pipeline with context binding and a redaction processor. The two records emitted both
carry `request_id`, `trace_id` and `tenant` although neither call site mentions them, the prompt
preview has its key-shaped token masked before it reaches disk, and the parse failure logs the raw
completion rather than the half-parsed result. Token counts are stand-ins for the provider's usage.

```python
import hashlib, json, re, time, uuid
import structlog
from structlog.contextvars import bind_contextvars, merge_contextvars

SECRET = re.compile(r"\b(sk|key)-[A-Za-z0-9]{8,}")

def redact(logger, method, event):            # pipeline step: runs on every record
    for k, v in list(event.items()):
        if isinstance(v, str):
            event[k] = SECRET.sub("[REDACTED]", v)[:2000]   # mask secrets, cap size
    return event

structlog.configure(processors=[
    merge_contextvars,                         # injects the bound request fields
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    redact,
    structlog.processors.JSONRenderer(),
])
log = structlog.get_logger()

def handle_turn(tenant, trace_id, prompt):
    bind_contextvars(request_id=uuid.uuid4().hex, trace_id=trace_id, tenant=tenant)
    t0 = time.perf_counter()
    raw = '{"status": "shipped", "eta": "2 May"'     # stand-in: truncated model output
    log.info("llm.call", model="example-model", input_tokens=812, output_tokens=21,
             latency_ms=round((time.perf_counter() - t0) * 1000, 1),
             prompt_sha=hashlib.sha256(prompt.encode()).hexdigest()[:12],
             prompt_preview=prompt[:80], finish_reason="length")
    try:
        return json.loads(raw)
    except ValueError as e:
        log.warning("llm.parse_failed", error=str(e), raw_output=raw)   # raw, not parsed
        return None

handle_turn("acme", "4bf92f3577b34da6a3ce929d0e0e4736",
            "Where is my order? my token is key-ABCDEFGH12345")
```

## Trade-offs
- Full payload logging makes every incident reproducible, but it multiplies volume by prompt size and
  makes the log store your largest PII repository; it flips to hashed or by-reference payloads as soon
  as traffic or a compliance review arrives.
- JSON records are queryable by machines and ugly for humans; a console renderer in development and
  JSON in production costs one configuration switch and keeps both.
- Binding context in middleware removes the burden from call sites, but it depends on context
  propagating through threads and tasks; it flips to explicit arguments inside executors and
  background workers.
- Sampling payload logs cuts cost dramatically, but the request you need is the one not sampled;
  keeping 100% of errors, warnings and flagged responses, plus a per-request debug switch, recovers
  most of the value.
- Synchronous logging is simple and loses nothing, but a slow sink stalls the request path;
  asynchronous handlers decouple at the cost of possible loss on crash.

## Pitfalls
- Logging the parsed or repaired output instead of the raw completion; the symptom is a parse-failure
  rate you cannot explain because the evidence was destroyed before it was written.
- Logging at call sites without bound context; the symptom is lines that cannot be joined to a trace
  or to each other, so an incident becomes a timestamp-matching exercise.
- Prompts containing customer data or pasted API keys written at INFO; the symptom is a security
  finding, followed by logging being disabled wholesale.
- Counting log lines as your metrics; the symptom is dashboards that take minutes to render and a
  logging bill that dwarfs the metrics system.
- Logging only failures; the symptom is a model that quietly degrades (wrong but well-formed answers)
  with nothing in the logs to compare against.
- Unbounded field values (a 200 KB tool result in a log field); the symptom is records dropped at the
  shipper or indexer, with gaps exactly where the big payloads were.

## When to use
Log every event that changes the course of a request or that you would need to reproduce it: each
model call with its parameters and usage, each tool invocation, each retrieval, each retry or
fallback, each guardrail decision, and the final outcome, all at INFO with sizes and ids; raw payloads
at DEBUG or in a sampled payload stream. Do not log inside tight loops or per token. Measure log
volume per request, the fraction of records carrying a trace id, and the time it takes to answer
"show me everything about request X". In interviews, be ready to explain what you would log for a RAG
request, which fields go on every line versus per event, and how you would keep prompts out of the
log store without losing debuggability.

## Role lens
- **Engineer:** Configures the structured logger with context binding and a redaction processor,
  chooses the event names and fields, and makes sure raw model output is captured on parse failures.
- **Architect:** Sets the log schema and field naming across services, the payload policy (hash,
  reference or sample), retention tiers, and which store receives which level.
- **FDE:** Fits the application's logs into the customer's existing pipeline, proves that no prompt
  content leaves the agreed boundary, and uses the canonical log line to answer the customer's usage
  questions.

## Check yourself
**Q:** A support assistant occasionally returns malformed JSON that your code repairs silently.
Product now wants to know how often it happens, for which prompts, and whether a recent prompt change
made it worse. What should the logging have looked like to answer this?
**A:** Three things. First, the repair path must log a WARNING event such as `llm.parse_failed` with
the raw completion, the error, the model and the prompt template version, because the repaired object
cannot be used to reconstruct what went wrong. Second, every `llm.call` INFO record should carry the
prompt template version or a hash of the rendered prompt, so the failure rate can be grouped by
version and the before-and-after comparison is a query rather than archaeology. Third, both records
must carry the bound request and trace ids so a failing case can be pulled up as a full trace and
turned into a regression test. With that in place, "how often" is a count of warnings over calls,
"which prompts" is a group-by on the version field, and "did the change make it worse" is the same
count split at the deploy timestamp. If payload logging is sampled, warnings and errors must be
exempt from sampling or the numerator is unknown.

## Quiz
### Q: Why bind request and trace ids in middleware rather than passing them to each log call?
- [ ] Middleware logging is faster than call-site logging
- [x] It makes correlation independent of developer discipline, so lines from helpers and libraries still carry the ids
- [ ] Log libraries cannot accept more than three keyword arguments
- [ ] Trace ids are only available in middleware
> The join between logs and traces fails the first time one call site forgets the id. Injecting the
> fields from a context variable guarantees every record in the request carries them.

### Q: Your JSON parser fails on about 1% of model outputs. Which log design lets you diagnose it later without paying for full-payload logging on every call?
- [ ] Log the parsed object at INFO on success and nothing on failure
- [ ] Log the exception message only, to keep volume down
- [x] Log a WARNING with the raw completion, prompt version and model on every parse failure
- [ ] Log the full prompt and completion at INFO for every call
> Raw output is the only evidence that explains a parse failure, and failures are rare enough to log
> in full. Logging everything at INFO would also work, but at a volume and privacy cost the warning
> path avoids.

### Q: What is the main reason to run redaction as a processor in the logging pipeline rather than at each call site?
- [ ] Processors run in a separate thread, so they are free
- [x] A single enforced step cannot be forgotten by a call site, and the rules can change without touching application code
- [ ] Call sites do not have access to regular expressions
- [ ] Redaction only works on JSON-rendered output
> One forgotten call site is a leak. Centralising redaction turns a per-developer habit into a
> property of the system, and lets compliance rules evolve in one place.
