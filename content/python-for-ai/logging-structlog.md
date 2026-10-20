---
id: logging-structlog
module: python-for-ai
name: logging / structlog
kind: tool
difficulty: core
est_minutes: 15
status: reviewed
related:
  - observability/logging
  - fastapi-for-ai/structured-logging
  - observability/tracing
  - observability/opentelemetry
  - agent-reliability/conversation-execution-identifiers
  - safety-security/pii-redaction
  - observability/token-cost-tracking
  - python-for-ai/async-await
sources:
  - Official docs: Python logging (Logging HOWTO and Logging Cookbook)
  - Official docs: structlog
---

## TL;DR
Python's standard `logging` module routes records from named loggers through levels, handlers and
formatters; structlog sits on top and makes every log line a structured event (a dict of key-value pairs)
that is rendered as JSON in production. For AI services this matters because the questions you ask in an
incident ("which model, how many tokens, which request, which retry?") are queries over fields, not greps
over prose.

## Why it exists
`print()` and f-string log lines work until there are many concurrent requests. An LLM service interleaves
hundreds of in-flight calls in one process; a line like `"call failed after 3.2s"` is useless when you cannot
tell which user, conversation, model or prompt version it belongs to. Prose logs also cannot be aggregated:
"p95 latency by model" or "token spend by tenant" needs fields, and parsing those back out of free text with
regexes is fragile and slow.

The stdlib `logging` module solves routing (who emits, at what level, to where) but not structure. You can
pass `extra={...}`, yet every formatter must know which keys to print, and there is no natural way to attach
a request ID once and have every downstream line carry it, especially across `await` points. structlog fills
that gap: context is bound once, every event is a dict, and the final rendering (JSON for machines, coloured
console for humans) is a configuration choice rather than something baked into every call site.

## How it works
### stdlib logging: the routing layer
- **Loggers** are named and hierarchical (`myapp.rag.retriever` is a child of `myapp`). Use
  `logging.getLogger(__name__)` so names mirror modules.
- **Levels** (`DEBUG < INFO < WARNING < ERROR < CRITICAL`) are checked on the logger and again on each handler.
- **Handlers** send records somewhere (stderr, file, syslog); **formatters** turn a record into text.
- **Propagation**: a record goes to its logger's handlers and then up to ancestors, ending at the root. This
  is why a library should never call `basicConfig` or attach handlers: configuration belongs to the
  application, and a library adds only a `NullHandler` so it stays silent unless the app opts in.
- Use `log.info("retrieved %d chunks", n)`, not an f-string, *because* formatting is deferred and skipped
  entirely when the level is disabled; `log.exception(...)` inside `except` attaches the traceback.

### structlog: events and processors
A structlog call such as `log.info("llm_call", model="gpt-4o-mini", tokens_in=812)` creates an **event
dict**. That dict passes through a **processor chain**, an ordered list of functions
`(logger, method_name, event_dict) -> event_dict`, then a final **renderer**. Typical processors:
`merge_contextvars` (pull in request-scoped context), `add_log_level`, `TimeStamper(fmt="iso")`,
`format_exc_info` or `dict_tracebacks` (structured exceptions), a custom redaction step, then
`JSONRenderer()` in production or `ConsoleRenderer()` locally. The pipeline design means policy (timestamps,
redaction, sampling) lives in one place instead of at every call site. A processor is just a function, so
redaction and sampling look like this:

```python
SENSITIVE = {"api_key", "authorization", "prompt", "completion"}
def redact(logger, method_name, event_dict):
    for key in SENSITIVE & event_dict.keys():
        event_dict[key] = "[redacted]"
    return event_dict

def sample_debug(logger, method_name, event_dict):
    if method_name == "debug" and random.random() > 0.01:
        raise structlog.DropEvent          # silently discards this event
    return event_dict
```

Order matters *because* each processor sees only what earlier ones produced: redaction must run after
`merge_contextvars` (or a key bound in context escapes it) and before the renderer (after which there is
only a string). Every processor runs on every emitted event, so keep them cheap.

### Context binding
`log = log.bind(conversation_id=cid)` returns a new logger carrying that field; bound loggers are immutable,
so binding in one function cannot leak into another. For request-wide context, `structlog.contextvars`
(`bind_contextvars`, `clear_contextvars`) stores fields in a `contextvars.ContextVar`. Context variables are
copied into each asyncio task, so a request ID bound in FastAPI middleware appears on every line emitted
while handling that request, even across `await`s, without passing it explicitly. Clear it at the start of
each request so a reused worker does not carry stale values. The copy is one-way and happens at task
creation: a value bound inside a child task is invisible to its parent, and work handed to a thread with
`loop.run_in_executor` does not inherit the context at all (`asyncio.to_thread` does copy it), so lines
logged from that thread lose their `request_id`.

### Handlers can block the event loop
A stdlib handler writes synchronously inside the logging call. Writing to stdout or stderr in a container
is normally cheap, but a file on a slow disk or a network handler (syslog, HTTP) stalls every coroutine in
the process while it writes. The stdlib answer is `QueueHandler` on the hot path plus a `QueueListener`
thread that owns the slow handlers, *because* enqueueing is fast and the I/O moves off the event loop. In
containers the usual pattern sidesteps this: write JSON to stdout and let the platform's log agent ship it.

### Level discipline
Levels are only useful if they mean the same thing everywhere: DEBUG for detail you would enable while
investigating, INFO for one summary event per meaningful unit of work (a request, an LLM call, a tool
call), WARNING for something that recovered (a retry, a fallback model), ERROR for a failed unit of work
someone may need to look at. Alerting is then built on ERROR rate and on fields, not on message text.

### Integrating the two
Third-party libraries (httpx, openai, uvicorn) log through stdlib. structlog can either use stdlib loggers as
its backend or render stdlib records via `structlog.stdlib.ProcessorFormatter`, so library and application
lines come out in one JSON format. `make_filtering_bound_logger(logging.INFO)` gives a fast level filter
when structlog is used standalone, and `cache_logger_on_first_use=True` avoids rebuilding loggers on every call.

### Logs vs traces vs metrics
Logs are discrete events; traces (OpenTelemetry spans) carry timing and parent-child structure across
services; metrics are cheap aggregates. Put the trace ID in every log event so the three can be joined, and
do not use logs as your only latency metric.

## Example
Production-style configuration: JSON output, request context via contextvars, and one structured event per
LLM call carrying the fields you will later aggregate on. The prompt itself is not logged, only its hash.

```python
import hashlib, logging, time, uuid
import structlog

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,     # request-scoped fields
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.dict_tracebacks,        # exceptions as structured data
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    cache_logger_on_first_use=True,
)
log = structlog.get_logger()

def handle_request(user_id: str, prompt: str) -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=str(uuid.uuid4()), user_id=user_id)
    t0 = time.perf_counter()
    # ... call the model here ...
    log.info("llm_call", model="gpt-4o-mini", prompt_version="summarize-v3",
             prompt_sha=hashlib.sha256(prompt.encode()).hexdigest()[:12],
             tokens_in=812, tokens_out=164, latency_ms=round((time.perf_counter() - t0) * 1000))

handle_request("u_42", "Summarize this contract ...")
# {"model": "gpt-4o-mini", "prompt_version": "summarize-v3", "prompt_sha": "…", "tokens_in": 812,
#  "tokens_out": 164, "latency_ms": 0, "event": "llm_call", "request_id": "…", "user_id": "u_42",
#  "level": "info", "timestamp": "2026-…Z"}
# (keys follow processor order: call kwargs, then contextvars, level, timestamp; pass
#  JSONRenderer(sort_keys=True) if your pipeline wants a stable alphabetical order)
```

## Trade-offs
- JSON logs are queryable and aggregate cleanly, but are hard to read in a terminal; render with
  `ConsoleRenderer` in development and switch on an environment flag rather than choosing one forever.
- Logging full prompts and completions makes debugging easy, but stores user data and secrets in a system
  with broad access and long retention; it flips to acceptable only with redaction, sampling and a short
  retention policy, or by sending payloads to a dedicated LLM tracing store instead.
- Rich context on every line costs bytes and ingestion fees at high volume; sample DEBUG and per-chunk
  streaming events, keep one summary event per call at INFO.
- structlog adds a dependency and a configuration step over plain stdlib; for a small script or a library,
  stdlib logging with `extra=` is enough and keeps you dependency-free.

## Pitfalls
- Calling `logging.basicConfig` or adding handlers inside a library; the symptom is duplicated lines or
  log formats changing depending on import order in the host application.
- Binding request context in a module-level global instead of contextvars; the symptom is log lines from
  one user's request carrying another user's `request_id` under concurrency.
- Logging an exception with `log.error(str(e))`; the symptom is incidents where you know *that* something
  failed but have no traceback, because only `log.exception` or `exc_info=True` captures it.
- Logging raw prompts that contain API keys, PII or retrieved documents; the symptom is a security review
  finding customer data in a log index that half the company can query.
- Offloading blocking work with `loop.run_in_executor` and logging inside it; the symptom is a subset of
  lines, exactly those from the thread pool, missing `request_id` and impossible to join to their request.
- Emitting a log line per streamed token; the symptom is log volume and cost scaling with output length and
  the useful summary line drowning in noise.

## When to use
Use stdlib logging everywhere as the routing backbone, and add structlog in any service that handles
concurrent requests or whose logs feed a search or analytics backend; that is nearly every deployed AI
service. Define a small event vocabulary early (`llm_call`, `retrieval`, `tool_call`, `guardrail_block`)
with consistent field names, because dashboards and alerts are built on them. Measure log volume per
request and check that every line carries a request or trace ID. In interviews, explaining propagation,
why libraries do not configure logging, and how contextvars keep async context straight is the expected depth.

## Role lens
- **Engineer:** Configures the processor chain, binds request and conversation IDs in middleware, and emits
  one structured event per model or tool call with tokens and latency.
- **Architect:** Sets the logging contract: field names, what may never be logged, retention, and how logs
  join traces and cost metrics across services.
- **FDE:** Adapts the output format and redaction rules to the customer's log pipeline and compliance rules,
  often their SIEM or a mandated JSON schema.

## Check yourself
**Q:** Under load, your FastAPI service's logs sometimes show a request's events tagged with another user's
ID. You store the current user in a module-level dict that middleware updates. Why does this happen and
what is the fix?
**A:** A module-level dict is shared by every concurrent request in the process. With async handlers,
request A sets the user, awaits the model, request B overwrites the value, and A's later log lines read B's
user. The fix is request-scoped storage: `structlog.contextvars.bind_contextvars` (backed by a
`ContextVar`), which asyncio copies per task, so each request sees its own values across awaits. Also clear
contextvars at request start so a reused worker does not inherit stale fields, and add a test that runs
concurrent requests and asserts each line's ID matches.

## Quiz
### Q: Why should a library add only a NullHandler rather than configure logging itself?
- [ ] NullHandler makes the library's logging faster than a StreamHandler
- [x] Handler and format configuration belong to the application; libraries that configure logging cause duplicated or reformatted output
- [ ] Libraries are not allowed to use getLogger(__name__)
- [ ] Records from libraries do not propagate to the root logger
> Library records propagate to the application's handlers, so the app decides where and how they appear.
> A library that attaches handlers or calls basicConfig overrides that choice and often duplicates lines.

### Q: What does a structlog processor chain receive and return?
- [ ] A formatted string, returning a formatted string
- [ ] A stdlib LogRecord, returning a LogRecord
- [x] The event dict, returning a (possibly modified) event dict, with a renderer at the end
- [ ] A list of handlers, returning the handler to use
> Each processor takes the logger, method name and event dict and returns an event dict; the final
> renderer turns it into JSON or console text. That is what makes redaction and timestamps one-place policy.

### Q: Why does `log.info("retrieved %d chunks", n)` beat `log.info(f"retrieved {n} chunks")` in stdlib logging?
- [ ] f-strings are not supported by logging handlers
- [ ] The %-style call writes JSON automatically
- [x] Formatting is deferred and skipped when the level is disabled, and the template stays constant for grouping
- [ ] The f-string version is not thread-safe
> The f-string is built before logging checks the level, wasting work for disabled levels, and it produces
> a unique message per value, which defeats grouping by message template in log tools.
