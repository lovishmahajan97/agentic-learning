---
id: httpexception-handling
module: fastapi-for-ai
name: HTTPException handling
kind: pattern
difficulty: core
est_minutes: 15
status: typed
lesson_type: lab
agent_relevance: core
related:
  - fastapi-for-ai/middleware-ordering
  - fastapi-for-ai/pydantic-request-response-models
  - fastapi-for-ai/streamingresponse
  - fastapi-for-ai/structured-logging
  - fastapi-for-ai/timeouts
  - python-for-ai/exceptions-and-custom-exception-hierarchies
  - backend-fundamentals/rest-graphql-api-design
  - system-design/graceful-degradation
sources:
  - Official docs: FastAPI (Handling Errors)
  - Official docs: Starlette (Exceptions)
  - Nottingham, Wilde, Dalal, 2023 — RFC 9457: Problem Details for HTTP APIs
---

## TL;DR
`raise HTTPException(status_code, detail)` aborts a request from anywhere and becomes an error response.
For agent APIs and tool servers: map LLM and tool failures to meaningful codes, one error shape, no
leaked internals, checks before streams start, tool errors as observations.

## For agent builders
- **Why it matters for agents.** Agents are API clients that read errors. A tool server that answers
  with a clear status, a `retryable` flag and a short fix lets the model or the executor recover; a bare
  500 or leaked stack trace gives it nothing, or worse, puts internals into the transcript.
- **Where it shows up in your stack.** Exception handlers on your agent API (`POST /runs`), on tool
  servers your agent calls over HTTP, the provider-error wrapper around your LLM client, and the in-band
  error events of a streamed run.
- **What you control.** The mapping table (provider 429 to 503, timeout to 504), the envelope and its
  `retryable` field, which errors end the run and which become tool results, and what reaches the model.
- **What you can skip.** Starlette's internal middleware details beyond one rule: raising from your own
  middleware gives a 500, so middleware returns responses.

## Lab
### Task
An agent's booking tool server answers every failure with a bare `Internal Server Error`, so the model
cannot tell a bad date from an outage. You give it one Problem Details contract (domain errors, routing
errors, validation errors and crashes) and write the executor function that turns those responses
into safe observations for the model.
- **Time box:** 45-60 minutes.
- **Prerequisites:** Pydantic request and response models, middleware ordering, structured logging.
- **Runs offline:** `TestClient(app, raise_server_exceptions=False)` returns the 500 a real client sees.
- **Needs:** `fastapi`, `httpx`, `pytest`; checked with FastAPI 0.115.6 and 0.161.1 on Python 3.12.

### Starter code
```python
# errors_lab.py: one error contract for an agent tool server, and an executor that reads it
import uuid
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

class AppError(Exception):
    status, code, retryable, retry_after = 500, "internal", False, None
    def __init__(self, detail: str): self.detail = detail
class UpstreamBusy(AppError):  status, code, retryable, retry_after = 503, "model_busy", True, 10
class InvalidDate(AppError):   status, code = 422, "invalid_date"

def problem(req: Request, status: int, code: str, detail, retryable=False, headers=None) -> JSONResponse:
    rid = req.headers.get("x-request-id") or str(uuid.uuid4())
    return JSONResponse({"type": f"https://docs.acme.ai/errors/{code}", "title": code, "status": status,
                         "detail": detail, "code": code, "retryable": retryable, "request_id": rid},
                        status, headers=headers, media_type="application/problem+json")

app = FastAPI()

# YOUR CODE: register handlers so every failure below leaves as problem(...):
#   AppError -> its status, code, retryable and Retry-After; Starlette's HTTPException (routing
#   404/405 too) -> code "http_error"; RequestValidationError -> 422 "invalid_request" with each
#   error's "input" dropped; any other Exception -> 500 "internal" with a generic detail

class Booking(BaseModel):
    date: str
    note: str = ""

@app.post("/tools/book")
async def book(b: Booking):
    if len(b.date) != 10:
        raise InvalidDate("date must be YYYY-MM-DD")
    if b.date.startswith("2099"):
        raise UpstreamBusy("model capacity exhausted")
    if b.date.startswith("2000"):
        raise RuntimeError("connection to db.internal:5432 refused")   # must never reach a client
    return {"booked": b.date}

def to_observation(r) -> dict:
    """Turn a tool server response into what the model sees in its transcript."""
    # YOUR CODE: 2xx -> the JSON body; 4xx -> {"error": detail, "code": code} so the model can fix
    # its arguments; 5xx -> {"error": "tool unavailable", "retryable": True}, never the raw body
    return {"result": r.text}

if __name__ == "__main__":
    c = TestClient(app, raise_server_exceptions=False)
    for body in ({"date": "2026-10-22"}, {"date": "22/10"}, {"date": "2099-01-01"}, {"date": "2000-01-01"}):
        r = c.post("/tools/book", json=body)
        print(r.status_code, r.headers.get("content-type"), to_observation(r))
    print(c.get("/nope").status_code, c.get("/nope").headers.get("content-type"))
```

As given, the bad date, the busy upstream and the crash all print
`500 text/plain; charset=utf-8 {'result': 'Internal Server Error'}`, and the 404 is plain JSON.

### Expected output
```
200 application/json {'booked': '2026-10-22'}
422 application/problem+json {'error': 'date must be YYYY-MM-DD', 'code': 'invalid_date'}
503 application/problem+json {'error': 'tool unavailable', 'retryable': True}
500 application/problem+json {'error': 'tool unavailable', 'retryable': True}
404 application/problem+json
```

### Tests
```python
# test_errors_lab.py: run with `pytest -q`
from fastapi.testclient import TestClient
from errors_lab import app, to_observation

c = TestClient(app, raise_server_exceptions=False)
PROBLEM = "application/problem+json"

def book(date, **kw):
    return c.post("/tools/book", json={"date": date, **kw}, headers={"x-request-id": "req-7"})

def test_domain_error_maps_to_503_with_retry_after():
    r = book("2099-01-01")
    assert r.status_code == 503 and r.headers["content-type"].startswith(PROBLEM)
    assert r.headers["retry-after"] == "10"
    assert r.json()["code"] == "model_busy" and r.json()["retryable"] is True

def test_routing_errors_share_the_envelope():
    for r in (c.get("/nope"), c.get("/tools/book")):            # 404 and 405 from the router
        assert r.headers["content-type"].startswith(PROBLEM) and r.json()["code"] == "http_error"

def test_validation_error_does_not_echo_input():
    r = c.post("/tools/book", json={"note": "sk_live_SECRET"})   # date missing
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"
    assert "sk_live_SECRET" not in r.text and '"input"' not in r.text

def test_crash_is_generic_and_traceable():
    r = book("2000-01-01")
    assert r.status_code == 500 and r.json()["code"] == "internal"
    assert "db.internal" not in r.text and r.json()["request_id"] == "req-7"

def test_executor_turns_errors_into_observations():
    assert to_observation(book("2026-10-22")) == {"booked": "2026-10-22"}
    assert to_observation(book("22/10")) == {"error": "date must be YYYY-MM-DD", "code": "invalid_date"}
    assert to_observation(book("2000-01-01")) == {"error": "tool unavailable", "retryable": True}
```

All five fail on the starter: no handlers are registered, so every error is a plain 500 or FastAPI's
default shape, and the executor passes raw text to the model.

### Failure scenarios
- **Inject a handler for `fastapi.HTTPException` only.** The routing test fails: `/nope` still returns
  `application/json` with `{"detail":"Not Found"}`, because the router raises Starlette's base class.
  Debug by printing `type(e).__mro__` in a catch-all handler.
- **Inject `detail=str(e)` in the `Exception` handler.** The crash test fails because
  `db.internal:5432` reaches the client, and through `to_observation` it would reach the transcript.
  Grep responses in CI for hostnames and SQL.
- **Inject an auth middleware that raises `HTTPException(401)`.** The request returns 500 with code
  `internal`, not 401: the raise happens outside `ExceptionMiddleware`. Return a `JSONResponse` from
  middleware instead.

### Submission
- **Your `errors_lab.py`** with the four handlers and `to_observation` done.
- **A `pytest -q` run** showing 5 passed.
- **The script's printed output**, matching the expected lines.
- **A short note** listing which of these failures should end an agent run and which the model should
  see as a tool result, with one sentence of reasoning each.
- **Evaluated on:** green tests, one envelope for every status, no `input` or internals echoed, and
  `request_id` matching the caller's header.

## Why it exists
- **AI endpoints fail in many distinct ways.** Invalid input, an unauthorised or over-quota caller, a
  missing document, a model provider that is rate-limited, down or slow, output that fails schema
  validation, a safety refusal, an overflowing context.
- **Each failure needs a different client reaction:** fix the request, re-authenticate, back off for N
  seconds, retry, or report to a human. Clients can act only if the response tells them which case they hit.
- **The naive patterns break this.**
  - Letting exceptions propagate turns everything into a bare 500, so clients retry validation errors
    forever and give up on transient ones.
  - Returning `{"ok": false, "error": "..."}` with status 200 breaks every HTTP-aware tool: retries,
    monitoring, load-balancer health metrics, the OpenAI-style SDKs your customers use.
  - Passing the provider's exception text through (`detail=str(e)`) leaks API keys in URLs, internal
    hostnames, prompt fragments and stack traces.
  - `try/except` in every handler duplicates mapping logic and drifts.
- **FastAPI's model: raise typed exceptions where the problem is detected, convert them in one place.**
  Domain code stays free of HTTP, and the error contract is defined once.

## How it works
### The flow through the middleware stack
Starlette builds the stack outside in: `ServerErrorMiddleware` (outermost), then your middleware, then
`ExceptionMiddleware`, then the router. When a handler or dependency raises:
1. **`ExceptionMiddleware` looks for a handler** registered for that exception class, walking the class's
   MRO, so a handler for a base class catches subclasses.
2. **FastAPI registers defaults:** `HTTPException` becomes `{"detail": ...}` with its status and headers,
   and `RequestValidationError` becomes 422 with a list of field errors.
3. **Anything unhandled goes up to `ServerErrorMiddleware`**, which returns a plain 500 (or a traceback
   page in debug mode), calls a handler registered for `Exception` or `500` if there is one, and
   re-raises so the server logs it.

- **Consequence for middleware:** because `ExceptionMiddleware` sits *inside* your middleware, an
  `HTTPException` raised from a middleware is not converted by those handlers and becomes a 500.
  Middleware should return responses directly.

### Why raise rather than return
- **Raising unwinds the whole stack in one step.** A dependency three levels deep cannot `return` a
  response to the client; it can only return a value to its caller. That is why auth and quota checks
  live in dependencies that raise.
- **Raising lets cleanup see the failure.** A `yield` dependency (a DB session, a lock) receives the
  exception at its `yield` and can roll back.
- **Returning hides it.** A handler that catches the error and returns `JSONResponse(status_code=409)`
  leaves that dependency believing the request succeeded, so its commit-on-exit logic runs.

### fastapi.HTTPException versus Starlette's
- **FastAPI's `HTTPException` subclasses Starlette's**, and its `detail` may be any JSON-serialisable
  value, not just a string.
- **Starlette raises its own for routing errors** (404, 405).
- **So to customise all of them**, register your handler for `starlette.exceptions.HTTPException`.

### Domain exceptions plus one mapping
- **Define an application hierarchy:** `AppError` with `code`, `status`, `retryable`, `retry_after`, and
  raise those from services.
- **One handler converts them**, so services never import FastAPI, and the mapping becomes a single
  reviewable table.

### Mapping upstream LLM failures
| Upstream situation | Your status | Why |
|---|---|---|
| Provider 429 or quota exhausted | **503** (or 429 when it is the *caller's* own quota), with `Retry-After` from the provider's hint | don't relay the provider's 429 as if the client exceeded *your* limit |
| Provider timeout | **504** | the upstream did not answer in time |
| Provider 5xx or connection error | **502** or **503** | the upstream failed or is unavailable |
| Context too long, or invalid parameters you could have checked | **422** or **400**, with a fix ("input is 140k tokens; the limit is 128k") | the client can correct it |
| Model output fails your schema after retries | **502** | the upstream gave you something unusable, and the client did nothing wrong |
| Safety refusal | often a successful 200 with a structured refusal field | the system worked as designed; if your API treats it as an error, use a specific, documented code |

### One error shape
- **Pick an envelope and use it everywhere**, including 404s and 422s.
- **RFC 9457 Problem Details is a standard choice:** `application/problem+json` with `type`, `title`,
  `status`, `detail`, `instance`.
- **Include a `request_id`** matching your logs, so support can find the trace without the client
  seeing it.

### Streaming
- **Status and headers go out with the first byte** of a `StreamingResponse` or SSE stream.
- **So an exception mid-stream can't become a 503.** The connection just breaks, and the client sees a
  truncated answer.
- **Do every check that can fail first** (auth, quota, validation, opening the upstream stream) *before*
  returning the response.
- **After that, emit failures as an in-band error event** the client knows how to render.

### Inside the agent loop versus at its edge
An agent run has two error boundaries, and each needs a different treatment:

| Failure | Inside the loop (executor) | At the run's edge (HTTP) |
|---|---|---|
| Tool returns 404, 422 or a domain error | append an error tool result; the model can fix arguments or try another tool | nothing: the run continues |
| Tool returns 503 with `Retry-After` | executor retries once within the run budget, then an error result | nothing |
| LLM provider 429 or timeout on a model step | retry within budget, else stop the run | 503 or 504 before streaming; an in-band error event after |
| Bug in your own code | not shown to the model | logged 500 with `request_id` |

- **Errors the model sees are prompt content.** Keep tool error messages short, actionable and free of
  hostnames, SQL or stack traces, because they land in the transcript, the trace store and eval datasets.
- **Tool servers should speak the same envelope.** Problem Details with a `code` and `retryable` lets one
  executor handle every tool without per-tool parsing.

## Diagram
Follow an exception raised in an agent API or tool server outward: `ExceptionMiddleware` converts what
it has handlers for, and anything that escapes it, or is raised in your own middleware, ends up as a 500
at `ServerErrorMiddleware`, which an agent executor can only treat as "tool unavailable".

```mermaid
flowchart TD
    H["Handler or dependency raises"] --> EM{"ExceptionMiddleware: handler for class or a base in MRO?"}
    EM -->|"AppError"| P1["One mapping: Problem Details with code, status, Retry-After"]
    EM -->|"Starlette HTTPException incl. routing 404, 405"| P2["Same envelope via the Starlette base class"]
    EM -->|"RequestValidationError"| P3["422 envelope, input field dropped"]
    EM -->|"no handler"| UM["Passes up through your middleware"]
    MW["Your middleware raises HTTPException"] --> SE
    UM --> SE["ServerErrorMiddleware"]
    SE --> P4["500 handler: log with request_id, generic body, re-raise to server log"]
    P1 --> C["Client gets status, envelope and request_id"]
    P2 --> C
    P3 --> C
    P4 --> C
```

## Examples
### A domain hierarchy, a single mapping to Problem Details, and safe 500s

```python
import logging, uuid
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("api")
app = FastAPI()

class AppError(Exception):
    status, code, retry_after = 500, "internal", None
    def __init__(self, detail: str): self.detail = detail
class UpstreamBusy(AppError):    status, code, retry_after = 503, "model_busy", 10
class UpstreamTimeout(AppError): status, code = 504, "model_timeout"
class ContextTooLong(AppError):  status, code = 422, "context_too_long"

def problem(req: Request, status: int, code: str, detail, headers=None, rid=None):
    rid = rid or req.headers.get("x-request-id") or str(uuid.uuid4())  # usually set by middleware
    body = {"type": f"https://docs.acme.ai/errors/{code}", "title": code, "status": status,
            "detail": detail, "request_id": rid}
    return JSONResponse(body, status, headers=headers, media_type="application/problem+json")

@app.exception_handler(AppError)
async def app_error(req: Request, e: AppError):
    h = {"Retry-After": str(e.retry_after)} if e.retry_after else None
    return problem(req, e.status, e.code, e.detail, h)

@app.exception_handler(StarletteHTTPException)      # also catches routing 404/405
async def http_error(req: Request, e: StarletteHTTPException):
    return problem(req, e.status_code, "http_error", e.detail, getattr(e, "headers", None))

@app.exception_handler(RequestValidationError)
async def invalid(req: Request, e: RequestValidationError):
    errs = [{k: v for k, v in err.items() if k != "input"} for err in e.errors()]  # no echoed prompt
    return problem(req, 422, "invalid_request", jsonable_encoder(errs))

@app.exception_handler(Exception)                   # runs in ServerErrorMiddleware
async def unhandled(req: Request, e: Exception):
    rid = req.headers.get("x-request-id") or str(uuid.uuid4())
    log.exception("unhandled error request_id=%s", rid)   # full detail goes to logs only
    return problem(req, 500, "internal", "unexpected error; quote request_id to support", rid=rid)
```

What happens on a provider 429:
1. **A provider 429 inside `generate()`** is caught in the client wrapper and re-raised as
   `UpstreamBusy("model capacity exhausted")`.
2. **The caller receives a 503** Problem Details body with `Retry-After: 10` and a `request_id`.
3. **The SDK backs off for ten seconds** instead of hammering, and support can find the trace from the ID.
4. **Nothing from the provider's raw error reaches the client.**

- **The 500 handler logs the same `request_id` it returns.** An ID minted fresh in the response but
  absent from the log line would send support looking for a trace that doesn't exist.
- **In a real service a middleware assigns the ID once per request** and every log line carries it.

### The client wrapper that maps provider errors (production snippet)
Uses the hierarchy above plus a 502 class; this is where `generate()` turns httpx errors into domain
errors, so no handler ever sees provider text.

```python
import httpx

class UpstreamFailed(AppError):  status, code = 502, "model_upstream_error"

async def generate(client: httpx.AsyncClient, payload: dict) -> dict:
    try:
        r = await client.post("/v1/chat/completions", json=payload)
        r.raise_for_status()
        return r.json()
    except httpx.TimeoutException:
        raise UpstreamTimeout("model did not answer in time") from None
    except httpx.ConnectError:
        raise UpstreamFailed("model provider unreachable") from None
    except httpx.HTTPStatusError as e:
        s = e.response.status_code
        log.warning("provider status=%s body=%.200s", s, e.response.text)  # logs only
        if s == 429:
            err = UpstreamBusy("model capacity exhausted")
            hint = e.response.headers.get("retry-after", "")
            if hint.isdigit():
                err.retry_after = int(hint)          # provider's hint beats the default 10 s
            raise err from None
        if s >= 500:
            raise UpstreamFailed("model provider error") from None
        raise                                        # provider 4xx = our bug: a logged 500
```

| Provider event | Raised as | Client sees |
|---|---|---|
| 429 with `retry-after: 7` | `UpstreamBusy` | 503, `Retry-After: 7` |
| 503 from the provider | `UpstreamFailed` | 502 |
| read or connect timeout | `UpstreamTimeout` | 504 |
| 400 (we sent a bad request) | unmapped `HTTPStatusError` | generic 500 with `request_id`, full detail in logs |

### Agent executor turning tool-server errors into observations
The tool servers return the Problem Details envelope above, ideally with `code` and `retryable` added as
extension members (RFC 9457 allows extra fields; the `problem()` helper above carries the code only in
`title`, so the executor falls back to it). The executor decides what the model sees. `http` is your
shared `httpx.AsyncClient`.

```python
import asyncio, httpx

async def call_tool(http: httpx.AsyncClient, url: str, args: dict, budget_left: float) -> dict:
    for attempt in range(2):
        try:
            r = await http.post(url, json=args, timeout=min(10.0, budget_left))
        except httpx.TimeoutException:
            return {"error": "tool timed out", "retryable": True}
        if r.status_code < 400:
            return r.json()
        p = r.json() if r.headers.get("content-type", "").endswith("json") else {}
        retry_after = r.headers.get("retry-after", "")
        if r.status_code == 503 and attempt == 0 and retry_after.isdigit() \
                and int(retry_after) < budget_left / 2:
            await asyncio.sleep(int(retry_after))              # one retry inside the run budget
            budget_left -= int(retry_after); continue
        if 400 <= r.status_code < 500:                          # model can fix its arguments
            return {"error": p.get("detail", "invalid request"),
                    "code": p.get("code") or p.get("title", "invalid")}
        return {"error": "tool unavailable", "retryable": True}  # 5xx: no internals to the model
    return {"error": "tool busy", "retryable": True}
```

| Tool server answers | Model sees | Run outcome |
|---|---|---|
| 422 `{"code": "invalid_date", "detail": "date must be YYYY-MM-DD"}` | that exact fix | model re-calls with `2026-10-22` |
| 503 with `Retry-After: 2`, 20 s budget left | the result after one retry | continues |
| 500 with a stack trace in the body | `tool unavailable` | model answers without that tool |

### What the defaults and the middleware trap look like (runnable with TestClient)
Requires `fastapi` and `httpx`. Shows routing errors sharing the envelope, middleware that raises versus
returns, and the default 422 echoing input.

```python
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

@app.exception_handler(StarletteHTTPException)       # covers handler-raised and routing errors
async def http_error(req: Request, e: StarletteHTTPException):
    return JSONResponse({"title": "http_error", "status": e.status_code, "detail": e.detail},
                        e.status_code, media_type="application/problem+json")

@app.middleware("http")
async def check_key(req: Request, call_next):
    key = req.headers.get("x-api-key")
    if key == "banned":
        raise HTTPException(401, "banned key")              # wrong: outside ExceptionMiddleware
    if key == "revoked":
        return JSONResponse({"detail": "revoked key"}, 401)  # right: middleware returns a response
    return await call_next(req)

@app.get("/v1/docs/{doc_id}")
async def get_doc(doc_id: int):
    raise HTTPException(404, f"document {doc_id} not found")

c = TestClient(app, raise_server_exceptions=False)
print(c.get("/v1/docs/7").json())        # {'title': 'http_error', 'status': 404, 'detail': 'document 7 not found'}
print(c.get("/nope").json())             # {'title': 'http_error', 'status': 404, 'detail': 'Not Found'}
print(c.delete("/v1/docs/7").status_code)                                  # 405, same envelope
print(c.get("/v1/docs/7", headers={"x-api-key": "revoked"}).status_code)   # 401
print(c.get("/v1/docs/7", headers={"x-api-key": "banned"}).status_code)    # 500
print(c.get("/v1/docs/abc").json()["detail"][0]["input"])                  # abc: default 422 echoes input
```

## Production patterns
- **Request ID assigned once, everywhere** — on every service. A small middleware reads `X-Request-ID`
  or mints one, stores it on `request.state`, adds it to the response header and the log context, and
  the error handlers read it from there. Monitor the share of error responses whose `request_id` is
  found in your logs; it should be 100%.
- **Provider errors mapped at the client boundary** — for every LLM or tool client. Wrap the SDK or httpx
  call as in the snippet above, log the raw provider status and body, and raise only domain errors.
  Monitor upstream errors by provider status next to your own 502/503/504 counts.
- **Error metrics by status, code and route** — always. Emit a counter labelled with status, the `code`
  from the envelope and the route template, and alert on 5xx separately from 4xx. Monitor 4xx spikes per
  client (their problem) and 5xx spikes per route (yours).
- **Pre-flight checks, then in-band stream errors** — on SSE and streaming routes. Validate, check quota
  and open the upstream stream before returning `StreamingResponse`; after that, catch errors in the
  generator and yield an `event: error` frame with a code and `retryable`. Monitor a stream-outcome
  metric (completed, errored, client-disconnected) separately from HTTP status.
- **Error contract tests** — in CI. With `TestClient(app, raise_server_exceptions=False)`, assert that an
  unknown route, a wrong method, an invalid body and a forced exception all return
  `application/problem+json` with a `request_id`. Monitor nothing at runtime; the test fails the build
  when someone adds a handler that bypasses the envelope.
- **Model-readable tool errors, documented in OpenAPI** — for every tool server agents call. Return
  Problem Details with a stable `code`, a `retryable` flag and a one-line fix in `detail`, and declare it
  with FastAPI's `responses=` parameter (for example `responses={503: {"model": Problem}}` with a
  Pydantic `Problem` model) so SDKs and tool wrappers know the shape. Monitor tool errors by `code` and
  how often the run recovers after one.

## Trade-offs
- **Central mapping vs indirection.** A central mapping keeps the contract consistent and the domain
  HTTP-free, at the cost of indirection: you must look up the table to know what a service exception
  becomes. That is worth it past a handful of endpoints.
- **Detailed messages vs leaks.** Detailed error messages speed up client debugging, but they risk
  leaking internals or enabling enumeration (for example, "user not found" versus "wrong tenant"). Be
  specific for validation, vague for auth and internal failures.
- **Refusal as 200 vs as an error.** Treating safety refusals as 200 with a refusal field keeps "the
  system worked" separate from "the system failed", but naive clients may show refusals as answers. Use
  an error code if your consumers are automated and must branch.
- **Show the model the error vs hide it.** Passing a tool's 4xx detail to the model lets it fix its own
  arguments, but every word becomes prompt content and can carry injected text from a third-party
  service. Pass details from your own tool servers; summarise or genericise errors from external ones.
- **Standard vs compatible envelope.** Standard Problem Details helps third-party clients and tooling. A
  custom envelope may match an existing SDK or provider-compatible API, and compatibility wins when you
  mimic another provider's API.

## Pitfalls
- **`detail=str(exc)` from provider or database errors.** Symptom: customers see internal hostnames,
  SQL fragments or partial prompts in error messages and file security tickets.
- **Raising `HTTPException` from middleware.** Symptom: a 500 with "Exception in ASGI application" in
  logs where you expected a clean 401 or 429.
- **Registering a handler only for FastAPI's `HTTPException`.** Symptom: routing 404s and 405s still
  return the default `{"detail": "Not Found"}` shape while every other error uses your envelope.
- **Raising after a `StreamingResponse` has started.** Symptom: clients receive a 200 with a truncated
  answer and no error, which surfaces as "the bot just stops mid-sentence".
- **Returning Pydantic's raw validation errors on a prompt endpoint.** Pydantic v2 errors carry the
  offending `input`, so the symptom is 422 bodies that echo a 100k-token prompt (or a pasted secret)
  back to the client and into any logging proxy. Keep `loc`, `msg` and `type`, drop or truncate `input`.
- **Letting a tool error abort the whole agent run.** Symptom: one tool's 404 turns into a 500 for the
  user's request, though the model could have recovered. Fix: convert tool errors to tool results in the
  executor; only budget, provider or internal failures end the run.
- **Mapping every upstream failure to 500.** Symptom: client SDKs that never retry transient provider
  outages, or retry permanent errors forever, because the codes carry no meaning.

## When to use
- **Set up central exception handling at the start of every FastAPI service**, not after the first
  incident: one domain hierarchy, one mapping, one envelope, handlers for Starlette's `HTTPException`,
  `RequestValidationError` and `Exception`.
- **Raise `HTTPException` directly** in thin, HTTP-specific code (auth dependencies, routers). Raise
  domain errors from services.
- **For AI endpoints**, write the upstream mapping table deliberately and document which codes are retryable.
- **For agents**, decide per failure whether it ends the run (HTTP status or in-band event) or becomes a
  tool result the model sees, and give tool servers the same envelope.
- **Measure** error rates by code and by route, and alert on 5xx separately from 4xx, since a 4xx spike
  means a client problem and a 5xx spike means yours.
- **Interviews:** explain the difference between 502, 503 and 504, and the streamed-response limitation.

## Role lens
- **Engineer:** Builds the exception hierarchy and handlers, wraps provider SDK errors at the client
  boundary, and pre-validates before opening a stream so failures get proper status codes.
- **Architect:** Defines the platform-wide error contract (envelope, codes, retryability, request IDs)
  so every service and SDK behaves consistently and gateways can make sense of failures.
- **FDE:** Reads a customer's failing integration logs, explains which errors are theirs (4xx) versus
  the platform's (5xx), and uses request IDs to pull the matching traces.

## Check yourself
**Q:** Your chat endpoint streams SSE. During a provider outage users see answers stop mid-sentence,
and dashboards show 100% 200s. What is happening and how do you redesign the error handling?
**A:** The response status and headers were sent with the first event, so when the provider fails
partway through, the code can't switch to 503. The exception just closes the stream, and access
logs record 200. Redesign in two phases. Before returning the `StreamingResponse`, do everything that
can fail cheaply: auth, quota, validation, token counting, and opening the upstream stream (or reading
its first chunk), so outages at that point get a proper 503 or 504. After streaming starts, catch
upstream errors in the generator and emit a typed error event (`event: error` with a code and
`retryable`) that the client renders, then end cleanly. Record a stream-outcome metric separate from
HTTP status, so dashboards reflect mid-stream failures.

## Interview questions
### What is the difference between 502, 503 and 504, with an LLM example of each?
- **502 Bad Gateway:** the upstream answered with something unusable, such as a provider 5xx or model
  output that still fails your schema after retries.
- **503 Service Unavailable:** temporary lack of capacity, such as the provider's 429 or exhausted quota,
  sent with `Retry-After` so clients back off.
- **504 Gateway Timeout:** the upstream did not answer within your deadline.
- **Why it matters:** SDKs decide whether and when to retry from these codes, so mapping all of them to
  500 removes that signal.

### Why should an auth or quota dependency raise instead of returning a JSONResponse?
- **A dependency can't return a response to the client**, only a value to its caller; raising unwinds
  the whole stack in one step.
- **Cleanup sees the failure:** a `yield` dependency receives the exception at its `yield` and can roll
  back, while a caught-and-returned error makes it believe the request succeeded and commit.

### A security ticket says your error messages contain internal hostnames and prompt fragments. How do you find and fix the cause?
- **Search for pass-through details:** `detail=str(e)` around provider or database calls, and default
  422 bodies that echo `input`.
- **Fix at the boundary:** wrap the provider client so it logs raw errors and raises domain errors with
  fixed messages, and strip `input` in the `RequestValidationError` handler.
- **Backstop:** an `Exception` handler that returns a generic body with a `request_id` and logs the
  rest, plus a CI test that forces an error and asserts no internals appear.

### Would you return a safety refusal as a 200 or as an error status?
- **200 with a structured refusal field** keeps "the system worked as designed" separate from "the system
  failed", so 5xx dashboards stay meaningful.
- **The risk** is naive clients rendering the refusal as if it were the answer.
- **Use a specific, documented error code** when consumers are automated pipelines that must branch.

### A tool your agent calls fails. When should that end the run, and when should the model see it?
- **The model sees it** when it can act: a 4xx with a clear fix (bad argument, not found), or a
  retryable 503 after the executor's one retry, so it can change arguments, pick another tool or answer.
- **The run ends** on failures the model cannot fix: exhausted run budget, LLM provider down, or a bug in
  your own code, mapped to 503, 504 or a logged 500 (or an in-band error event once streaming).
- **Never show internals**: tool 5xx bodies are replaced with a generic message before entering the
  transcript.

### Your agent API's 5xx rate doubles but the 4xx rate is flat. What do you look at first?
- **Split 5xx by envelope `code` and route**: a jump in `model_busy` or `model_timeout` points at the
  provider, while a jump in `internal` points at your own code.
- **Pull traces by `request_id`** from a sample of failing responses to see the original exception.
- **Compare with upstream error counts by provider status**, since mapped errors should track them; 5xx
  growth without upstream errors means a regression in your service.

## Quiz
### Q: Where should a FastAPI service map an upstream provider's 429 to a client response, and to what?
- [ ] Pass the provider's 429 and message through unchanged
- [x] Convert it at the client wrapper into a domain error mapped to 503 with Retry-After, without exposing provider details
- [ ] Return 200 with an error field so clients do not retry
- [ ] Return 400 because the request could not be served
> The caller did not exceed your limit; your upstream is saturated, which is a temporary unavailability.
> A 503 with Retry-After lets clients back off correctly without leaking upstream internals.

### Q: Why does raising `HTTPException(401)` inside custom middleware often produce a 500?
- [ ] HTTPException is only valid in GET handlers
- [ ] Middleware runs after the response is sent
- [x] The exception-handling middleware sits inside user middleware, so handlers never see exceptions raised outside it
- [ ] FastAPI forbids 401 responses from middleware
> Starlette places ExceptionMiddleware between user middleware and the router. Exceptions raised in
> user middleware bypass it and reach ServerErrorMiddleware, which returns 500, so middleware should return responses.

### Q: Which handler registration ensures routing 404s use your error envelope?
- [ ] @app.exception_handler(fastapi.HTTPException)
- [x] @app.exception_handler(starlette.exceptions.HTTPException)
- [ ] @app.exception_handler(RequestValidationError)
- [ ] @app.exception_handler(404) is impossible; 404s cannot be customised
> Routing raises Starlette's HTTPException, and FastAPI's is a subclass of it. Registering for the
> Starlette base class covers both, while registering only the subclass misses router-generated errors.

## Use cases & projects
### Use cases
- **Agent APIs with a stable error contract.** `POST /runs` returns one envelope with a `retryable` flag,
  so UIs and SDKs know when to retry and when to show the user a fix.
- **Tool servers built for models.** Clear statuses and short, safe messages let an agent recover from a
  bad argument instead of looping on a bare 500.
- **Provider failure mapping.** A wrapper turns provider 429s, timeouts and outages into 503 or 504
  responses with no leaked hostnames or prompt fragments.
- **Executors that keep runs alive.** Recoverable tool failures become observations for the model, while
  auth and budget failures end the run cleanly.

### Mini project
**One error contract from tool server to agent (1-2 hours).**
- **Goal:** every failure in a small agent stack reaches the client or the model in one safe, predictable
  shape.
- **Steps:** build a FastAPI tool server with a domain exception hierarchy and a single handler that emits
  Problem Details with `retryable`; register the handler for Starlette's `HTTPException`; wrap a fake LLM
  client that raises provider errors; have the agent executor turn tool errors into observations.
- **Done when:** tests cover a routing 404, a validation error, a provider 429 and an unexpected crash,
  all in the same envelope, none leaking internals, and a trace shows the model retrying after a
  retryable tool error.
