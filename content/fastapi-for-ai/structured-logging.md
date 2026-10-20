---
id: structured-logging
module: fastapi-for-ai
name: Structured logging
kind: practice
difficulty: core
est_minutes: 13
related:
  - fastapi-for-ai/middleware-ordering
  - fastapi-for-ai/opentelemetry-instrumentation
  - fastapi-for-ai/streamingresponse
  - python-for-ai/logging-structlog
  - observability/logging
  - observability/token-cost-tracking
  - agent-reliability/audit-logs-execution-traces
sources:
  - Official docs: structlog
  - Official docs: Python logging
  - Official docs: Uvicorn
---

## TL;DR
Structured logging emits each log event as a machine-parseable record (usually one JSON object per line)
with named fields such as `request_id`, `tenant`, `model`, `prompt_tokens` and `latency_ms`, instead of a
sentence. In a FastAPI service, a middleware binds request context into `contextvars` so every log line
from that request carries it automatically, which turns logs into something you can filter, join to
traces and aggregate into cost and latency per tenant.

## Why it exists
A line like `INFO: chat done for acme in 2.3s using gpt-x (812 tokens)` is readable once and useless at
scale. To answer "which tenant's p95 latency doubled after the prompt change?" or "how many tokens did
the summariser burn yesterday?", someone must write a regex per message format, and the regex breaks the
day a developer rewords the sentence. Interleaving makes it worse: an async worker handles hundreds of
requests concurrently, so lines from different requests alternate, and without a request identifier on
every line you cannot reconstruct what happened in any single one.

AI services raise the stakes. Each request has fields that matter commercially and operationally: model,
prompt version, input and output tokens, time to first token, finish reason, retries, cache hits, tool
calls. These are numbers you will want to sum and group, which is what log platforms do well on fields
and badly on prose. The naive alternative, passing a `request_id` argument into every function so it can
be printed, pollutes every signature and still misses library logs. Structured logging plus context
propagation gets consistent fields onto every line without threading them through the code.

## How it works
### Events, not sentences
A log call becomes an event name plus key-value fields: `log.info("llm_call_finished", model=m,
prompt_tokens=812, completion_tokens=240, latency_ms=2310)`. The message is a stable identifier you can
count; the variable data lives in fields with stable names and types. A renderer serialises the event to
JSON at the end of the pipeline, so developers can still get coloured key-value output locally from the
same calls.

### Request context with contextvars
Python's `contextvars` give each asyncio task its own view of a variable, and a task copies the current
context when it is created. A middleware at the top of the stack generates or accepts a `request_id`
(from an incoming `X-Request-ID` header, or the trace id if tracing is on), binds it together with tenant
and route, and clears it when the request ends. Every log call during that request, in any function or
library that goes through the same logging pipeline, inherits those fields without receiving them as
arguments. structlog does this with `bind_contextvars` and the `merge_contextvars` processor.

### The pipeline
A typical structlog chain: merge contextvars, add log level, add an ISO timestamp, render exceptions as
structured fields, then render JSON. Configure the standard library `logging` to use the same formatter
so third-party loggers (httpx, SQLAlchemy, Uvicorn errors) come out as JSON with the same fields rather
than as stray plain-text lines.

### What to log for LLM calls
One event per model call with: model and provider, prompt template id and version, input and output
tokens, latency and time to first token, finish reason, retry count, cache hit, and the error class on
failure. Log the *shape* of content by default, not the content: lengths, hashes, counts of messages and
tool calls. Prompts and completions routinely contain personal data and secrets pasted by users; if you
need them for debugging, put them behind an explicit, sampled, access-controlled path with retention
limits, and redact before writing.

### Access logs
Uvicorn's access logger prints its own plain-text line per request. Most teams disable it
(`--no-access-log`) and emit one structured `request_finished` event from the middleware instead, with
status, route template (not the raw path, to avoid high-cardinality ids) and duration.

## Example
structlog configured for JSON, a middleware that binds request context, and one structured event per LLM
call.

```python
import time, uuid, logging, structlog
from fastapi import FastAPI, Request

structlog.configure(processors=[
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.format_exc_info,
    structlog.processors.JSONRenderer(),
])
log = structlog.get_logger()
app = FastAPI()

@app.middleware("http")
async def bind_request_context(request: Request, call_next):
    structlog.contextvars.clear_contextvars()
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex
    structlog.contextvars.bind_contextvars(request_id=rid,
                                           tenant=request.headers.get("x-tenant", "unknown"))
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    log.info("request_finished", status=response.status_code,
             route=getattr(route, "path", "unmatched"),
             duration_ms=round((time.perf_counter() - start) * 1000))
    response.headers["x-request-id"] = rid
    return response

@app.post("/v1/chat")
async def chat(body: dict):
    t0 = time.perf_counter()
    usage = {"prompt_tokens": 812, "completion_tokens": 240}       # from the provider response
    log.info("llm_call_finished", model="model-a", prompt_version="support-v14",
             finish_reason="stop", latency_ms=round((time.perf_counter() - t0) * 1000),
             prompt_chars=sum(len(m.get("content", "")) for m in body.get("messages", [])), **usage)
    return {"content": "..."}
```

One request yields lines like
`{"request_id": "9f2c...", "tenant": "acme", "model": "model-a", "prompt_tokens": 812, "event": "llm_call_finished", ...}`.
A query grouping `llm_call_finished` by `tenant` and summing tokens is now a one-liner in any log platform.
For a `StreamingResponse`, `request_finished` fires when headers are sent, so log stream completion and
token counts from inside the generator instead.

## Trade-offs
- JSON lines are trivially queryable but harder to read raw in a terminal; use a console renderer locally
  and JSON in deployed environments, driven by one setting.
- Rich per-request fields make debugging fast, but high-cardinality fields (raw paths, user ids as label
  keys) inflate cost in some log backends; keep them as fields, not index keys, when the bill flips.
- Logging prompts and completions makes quality debugging possible, but it creates a copy of sensitive
  data with weaker controls; default to metadata and opt in to content per tenant with redaction.
- Logs are cheap per event but expensive in aggregate at token granularity; one event per LLM call is
  usually right, and per-token logging belongs only in short-lived debugging.

## Pitfalls
- Formatting values into the message with f-strings; the symptom is thousands of unique "messages" and
  dashboards that cannot group or count events.
- Leaving library loggers on the default plain-text handler; the symptom is mixed JSON and text lines that
  the log shipper fails to parse, often dropping exactly the error lines you need.
- Forgetting to clear contextvars per request or binding in a long-lived task; the symptom is log lines
  carrying the previous request's `request_id` or tenant, which misattributes incidents and costs.
- Logging full prompts by default; the symptom is personal data or API keys showing up in a log search
  months later, outside any retention or access policy.
- Logging the route's raw path instead of its template; the symptom is per-path aggregations with one row
  per document id and a log index that grows without bound.

## When to use
Use structured logging in any service that runs in more than one process or handles concurrent requests,
which is every FastAPI deployment. Start with a request-context middleware, one `request_finished` event
and one `llm_call_finished` event with token and latency fields; those answer most production questions.
Add `trace_id` to logs once tracing exists, so a log line links to its trace. Use logs for discrete events
and audit facts, metrics for things you graph continuously, and traces for latency breakdowns across
services. Interviewers look for request correlation via contextvars and a clear stance on logging prompt content.

## Role lens
- **Engineer:** Configures structlog and stdlib logging into one JSON pipeline, writes the context
  middleware, and emits consistent fields for every model call.
- **Architect:** Sets the logging schema and field names across services, the policy on prompt content and
  retention, and what goes to logs versus metrics versus traces.
- **FDE:** Wires the service's JSON logs into the customer's SIEM or log platform, mapping fields and
  honouring their data-residency and redaction rules.

## Check yourself
**Q:** Finance asks for daily token spend per tenant and per feature, and security says prompts must never
appear in logs. Using only logging, how would you deliver this, and what would you check before trusting
the numbers?
**A:** Emit exactly one `llm_call_finished` event per provider call with fields `tenant`, `feature` (route
template or an explicit tag), `model`, `prompt_tokens`, `completion_tokens` and `retry_count`, with
tenant bound by middleware through contextvars so every call inherits it. Content is represented only by
lengths or hashes, and a redaction processor drops any field named like `prompt` or `messages` as a safety
net. Spend is then a group-by on tenant and feature multiplied by per-model prices. Before trusting it, I
would reconcile totals against the provider's billing for a day, confirm retries and streamed calls are
each logged once with final usage, and check that sampling is off for this event, since sampled cost
logs undercount.

## Quiz
### Q: Why do request fields bound with `contextvars` appear on log lines from deep inside library calls?
- [ ] structlog patches every library to accept a `request_id` argument
- [x] Each asyncio task carries its own context, which the logging pipeline merges into every event
- [ ] FastAPI injects the request object into all function calls automatically
- [ ] Uvicorn prefixes each output line with the connection id
> Context variables are task-local and copied when tasks are created, so a value bound in middleware is
> visible to any code running for that request, and `merge_contextvars` adds it to each event.

### Q: What is the best default for logging LLM prompt content in production?
- [ ] Log full prompts and completions at INFO for every request
- [ ] Never log anything about the prompt
- [x] Log metadata such as template version, token counts and lengths, with content only via sampled, redacted, access-controlled paths
- [ ] Log prompts only when the response status is 200
> Metadata answers cost and latency questions without copying sensitive data. Logging nothing loses
> debuggability; logging everything creates an uncontrolled store of personal data and secrets.

### Q: Why do teams often disable Uvicorn's access log and log requests from middleware instead?
- [ ] Uvicorn's access log blocks the event loop on every request
- [ ] The access log cannot be written to stdout
- [x] The middleware can emit one structured event with request context, route template and duration
- [ ] Access logs are required to be disabled for HTTP/2
> Uvicorn's line is plain text without your bound fields. A middleware event carries `request_id`, tenant
> and route template in the same JSON schema as everything else, so it can be joined and aggregated.
