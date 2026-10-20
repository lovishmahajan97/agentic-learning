---
id: httpexception-handling
module: fastapi-for-ai
name: HTTPException handling
kind: pattern
difficulty: core
est_minutes: 15
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
`raise HTTPException(status_code, detail)` aborts a request from anywhere in the call stack (handler
or dependency) and becomes an HTTP error response. Handling errors well means deciding how failures
reach the client. Map domain and upstream-LLM failures to meaningful status codes, return one
consistent error shape, never leak internals, and remember that once a stream has started the status
code can no longer change.

## Why it exists
An AI endpoint fails in more ways than a CRUD endpoint. The input can be invalid, the caller
unauthorised or over quota, the document missing, the model provider rate-limited, down or slow, the
output can fail schema validation, the safety filter can refuse it, or the context can overflow. Each
needs a different client reaction: fix the request, re-authenticate, back off for N seconds, retry,
or report to a human. Clients can act only if the response tells them which case they hit.

The naive patterns break this. Letting exceptions propagate turns everything into a bare 500, so
clients retry validation errors forever and give up on transient ones. Returning
`{"ok": false, "error": "..."}` with status 200 breaks every HTTP-aware tool: retries, monitoring,
load-balancer health metrics, the OpenAI-style SDKs your customers use. Passing the provider's
exception text through (`detail=str(e)`) leaks API keys in URLs, internal hostnames, prompt
fragments and stack traces. And `try/except` in every handler duplicates mapping logic and drifts.

FastAPI's model is to raise typed exceptions where the problem is detected, and convert them to
responses in one place. Domain code stays free of HTTP, and the error contract is defined once.

## How it works
### The flow
Starlette builds a middleware stack: `ServerErrorMiddleware` (outermost), then your middleware, then
`ExceptionMiddleware`, then the router. When a handler or dependency raises:
1. `ExceptionMiddleware` looks for a handler registered for that exception class, walking the class's
   MRO, so a handler for a base class catches subclasses.
2. FastAPI registers defaults: `HTTPException` becomes `{"detail": ...}` with its status and headers,
   and `RequestValidationError` becomes 422 with a list of field errors.
3. Anything unhandled goes up to `ServerErrorMiddleware`, which returns a plain 500 (or a traceback
   page in debug mode), calls a handler registered for `Exception` or `500` if there is one, and
   re-raises so the server logs it.

Because `ExceptionMiddleware` sits *inside* your middleware, an `HTTPException` raised from a
middleware is not converted by those handlers and becomes a 500. Middleware should return responses
directly.

### fastapi.HTTPException versus Starlette's
FastAPI's `HTTPException` subclasses Starlette's, and its `detail` may be any JSON-serialisable value,
not just a string. Starlette raises its own for routing errors (404, 405). To customise all of them,
register your handler for `starlette.exceptions.HTTPException`.

### Domain exceptions plus one mapping
Define an application hierarchy (`AppError` with `code`, `status`, `retryable`, `retry_after`) and
raise those from services. One handler converts them, so services never import FastAPI, and the
mapping becomes a single reviewable table.

### Mapping upstream LLM failures
- Provider 429 or quota exhausted becomes **503** (or 429 when it is the *caller's* own quota), with
  `Retry-After` taken from the provider's hint. Don't relay the provider's 429 as if the client
  exceeded *your* limit.
- Provider timeout becomes **504**. Provider 5xx or connection error becomes **502** or **503**.
- Context too long, or invalid parameters you could have checked, becomes **422** or **400** with a
  fix ("input is 140k tokens; the limit is 128k").
- Model output that fails your schema after retries becomes **502**: the upstream gave you something
  unusable, and the client did nothing wrong.
- A safety refusal is often a successful 200 with a structured refusal field, because the system
  worked as designed. If your API treats it as an error, use a specific, documented code.

### One error shape
Pick an envelope and use it everywhere, including 404s and 422s. RFC 9457 Problem Details
(`application/problem+json` with `type`, `title`, `status`, `detail`, `instance`) is a standard choice.
Include a `request_id` matching your logs so support can find the trace without the client seeing it.

### Streaming
With `StreamingResponse` or SSE, status and headers go out with the first byte. An exception mid-stream
can't become a 503. The connection just breaks, and the client sees a truncated answer. So do every
check that can fail (auth, quota, validation, opening the upstream stream) *before* returning the
response, and emit failures after that as an in-band error event the client knows how to render.

## Example
A domain hierarchy, a single mapping to Problem Details, and safe 500s.

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

def problem(req: Request, status: int, code: str, detail, headers=None):
    rid = req.headers.get("x-request-id", str(uuid.uuid4()))
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
    return problem(req, 422, "invalid_request", jsonable_encoder(e.errors()))

@app.exception_handler(Exception)                   # runs in ServerErrorMiddleware
async def unhandled(req: Request, e: Exception):
    log.exception("unhandled error")                 # full detail goes to logs only
    return problem(req, 500, "internal", "unexpected error; quote request_id to support")
```

A provider 429 inside `generate()` is caught in the client wrapper and re-raised as
`UpstreamBusy("model capacity exhausted")`. The caller receives a 503 Problem Details body with
`Retry-After: 10` and a `request_id`. The SDK backs off for ten seconds instead of hammering, and
support can find the trace from the ID. Nothing from the provider's raw error reaches the client.

## Trade-offs
- A central mapping keeps the contract consistent and the domain HTTP-free, at the cost of
  indirection: you must look up the table to know what a service exception becomes. That is worth it
  past a handful of endpoints.
- Detailed error messages speed up client debugging, but they risk leaking internals or enabling
  enumeration (for example, "user not found" versus "wrong tenant"). Be specific for validation, vague
  for auth and internal failures.
- Treating safety refusals as 200 with a refusal field keeps "the system worked" separate from
  "the system failed", but naive clients may show refusals as answers. Use an error code if your
  consumers are automated and must branch.
- Standard Problem Details helps third-party clients and tooling. A custom envelope may match an
  existing SDK or provider-compatible API, and compatibility wins when you mimic another provider's API.

## Pitfalls
- `detail=str(exc)` from provider or database errors. The symptom is customers seeing internal
  hostnames, SQL fragments or partial prompts in error messages and filing security tickets.
- Raising `HTTPException` from middleware. The symptom is a 500 with "Exception in ASGI application"
  in logs where you expected a clean 401 or 429.
- Registering a handler only for FastAPI's `HTTPException`. The symptom is routing 404s and 405s still
  returning the default `{"detail": "Not Found"}` shape while every other error uses your envelope.
- Raising after a `StreamingResponse` has started. The symptom is clients receiving a 200 with a
  truncated answer and no error, which surfaces as "the bot just stops mid-sentence".
- Mapping every upstream failure to 500. The symptom is client SDKs that never retry transient
  provider outages, or retry permanent errors forever, because the codes carry no meaning.

## When to use
Set up central exception handling at the start of every FastAPI service, not after the first
incident: one domain hierarchy, one mapping, one envelope, handlers for Starlette's `HTTPException`,
`RequestValidationError` and `Exception`. Raise `HTTPException` directly in thin, HTTP-specific code
(auth dependencies, routers). Raise domain errors from services. For AI endpoints, write the upstream
mapping table deliberately and document which codes are retryable. Measure error rates by code and by
route, and alert on 5xx separately from 4xx, since a 4xx spike means a client problem and a 5xx spike
means yours. In interviews, explain the difference between 502, 503 and 504, and the
streamed-response limitation.

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
