---
id: middleware-ordering
module: fastapi-for-ai
name: Middleware ordering
kind: concept
difficulty: core
est_minutes: 13
related:
  - fastapi-for-ai/asgi-lifecycle
  - fastapi-for-ai/cors
  - fastapi-for-ai/api-keys-oauth2-jwt
  - fastapi-for-ai/structured-logging
  - fastapi-for-ai/httpexception-handling
  - fastapi-for-ai/opentelemetry-instrumentation
  - fastapi-for-ai/streamingresponse
sources:
  - Official docs: Starlette
  - Official docs: FastAPI
---

## TL;DR
Middleware in FastAPI is a stack of ASGI apps wrapped around each other: a request walks down the stack
and the response walks back up. `app.add_middleware()` inserts at the *outside*, so the last one added
runs first on the way in and last on the way out. Order decides whether a 401 carries CORS headers,
whether a trace ID exists when auth logs, and whether timing sees exceptions. Starlette also pins
`ServerErrorMiddleware` outermost and `ExceptionMiddleware` innermost, which explains several surprises.

## Why it exists
Cross-cutting concerns — request IDs, auth, CORS, compression, timing, tracing, body-size limits — do
not belong in every endpoint, so frameworks put them in a chain that every request passes through. The
chain has to be ordered because the concerns depend on each other. A request-ID layer must run before
anything that logs, or early log lines have no ID. CORS must wrap auth, or the browser receives a 401
with no `Access-Control-Allow-Origin` and reports an opaque "CORS error" instead of "unauthorized". A
body-size limit must run before anything reads the body. A tracing span should open before auth so
rejected requests are still traced.

The naive approach, "add them in whatever order and let each one be independent", fails because
middleware is not a set of independent filters; it is nested function calls. Each layer receives the
request from the layer outside it and the response from the layer inside it. Headers added on the way
out by an inner layer are visible to outer layers; values set on the way in by an outer layer are
visible to inner layers; nothing flows the other way. Exceptions propagate outward through the
layers, and a layer that returns early (a 401, a 413) short-circuits everything inside it. Ordering is
therefore semantics, not style.

FastAPI adds a wrinkle: its `add_middleware` API inserts each new middleware on the outside, the
opposite of what reading the file top to bottom suggests, and two framework-owned layers sit at fixed
positions. Most "my middleware does not see X" bugs come from not knowing these two facts.

## How it works
### The stack is built once
Starlette keeps `user_middleware` as a list. On the first request it builds the chain as
`ServerErrorMiddleware(user_mw_0(user_mw_1(... ExceptionMiddleware(Router))))`: the first element of the
list is the outermost user layer. Because the stack is built lazily and then frozen, adding middleware
after the app has started raises an error in current Starlette; configure everything before the first
request.

### Two ways to add, two orderings
- `app.add_middleware(M)` inserts `M` at index 0 of `user_middleware`. Each call pushes the new layer to
  the *outside*. Reading a file with three `add_middleware` calls, the bottom one is the outermost; the
  request hits it first.
- `FastAPI(middleware=[Middleware(A), Middleware(B)])` keeps list order: A is outermost, B inside it.
- `@app.middleware("http")` is sugar for `add_middleware(BaseHTTPMiddleware, dispatch=fn)`, so
  decorator-defined middleware follow the `add_middleware` rule: last defined, outermost.

### The two fixed layers
`ServerErrorMiddleware` is always the outermost layer. It catches any exception that escaped everything
else, returns a 500 (or the handler you registered for `500` / `Exception`), then re-raises so the
server logs it. *Because* it is outside your middleware, a 500 it generates never passes back through
`CORSMiddleware`: a browser sees a CORS failure, not a 500. `ExceptionMiddleware` is always the
innermost layer, directly around the router. It converts `HTTPException` and anything with a registered
handler into a normal response. *Because* it is inside your middleware, your layers see a 404 or a 422
as an ordinary response with a status code, not as an exception. Only unhandled exceptions travel
through your middleware as exceptions, which is why a timing or logging middleware must use
`try/finally` rather than code placed after `await call_next(request)`.

### Request path and response path
Each layer is either `async def __call__(self, scope, receive, send)` (pure ASGI) or
`async def dispatch(self, request, call_next)` (`BaseHTTPMiddleware`). On the way in a layer may read or
mutate the scope (set `scope["state"]["request_id"]`), reject early, or wrap `receive` to count body
bytes. On the way out it may wrap `send` to inject headers on `http.response.start`, time the response,
or observe the status. A layer that returns before calling the next one short-circuits the rest.

### Why pure ASGI middleware is preferred for streaming
`BaseHTTPMiddleware` runs the downstream app in a separate task and exposes the response as a streaming
body. That is convenient but has costs: context variables set downstream are not visible to the
middleware afterwards, the extra task adds overhead per request, and it has a history of interfering
with streaming and background tasks. For token streaming and trace propagation, write pure ASGI
middleware that wraps `send`; it adds no task and passes `more_body` chunks through untouched.

### A sane default order, outermost first
1. Request ID / trace context, so every later log line and span has it.
2. CORS, so every response, including auth rejections and preflight answers, gets its headers.
3. Body-size limits and timeouts, to reject before spending anything.
4. Authentication, which needs the trace and produces a principal for inner layers.
5. Timing and metrics, inside auth if you want handler time, outside it if you want total time.
6. Compression, kept away from token streams, since buffering for gzip delays each chunk.
With `add_middleware`, add these in *reverse*; with the `middleware=` list, in this order.

## Example
The constructor-list form makes order explicit. A pure ASGI request-ID layer sits outermost, CORS wraps
everything else, and a timing layer uses `try/finally` so exceptions are timed too. The 401 raised in
the endpoint is turned into a response by `ExceptionMiddleware`, then picks up CORS headers and the
request ID on its way out.

```python
import time, uuid
from fastapi import FastAPI, HTTPException, Request
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

class RequestID:                                   # pure ASGI: no extra task, streaming-safe
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = uuid.uuid4().hex[:12]
        scope.setdefault("state", {})["request_id"] = rid
        async def send_with_id(msg):
            if msg["type"] == "http.response.start":
                msg = {**msg, "headers": [*msg.get("headers", []), (b"x-request-id", rid.encode())]}
            await send(msg)
        await self.app(scope, receive, send_with_id)

class Timing(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        t0 = time.perf_counter()
        try:
            return await call_next(request)        # 4xx from HTTPException arrive here as responses
        finally:                                   # unhandled exceptions pass through as exceptions
            ms = (time.perf_counter() - t0) * 1000
            print(request.state.request_id, request.url.path, f"{ms:.1f}ms")

app = FastAPI(middleware=[                         # list order: first is outermost
    Middleware(RequestID),
    Middleware(CORSMiddleware, allow_origins=["https://app.example.com"], allow_headers=["*"]),
    Middleware(Timing),
])

@app.post("/chat")
async def chat(request: Request, body: dict):
    if "x-api-key" not in request.headers:
        raise HTTPException(401, "missing key")    # still gets CORS headers: CORS is outside
    return {"request_id": request.state.request_id, "reply": "..."}
```

## Trade-offs
- Outer placement sees every request including ones later rejected, which is right for CORS and trace
  IDs but wrong for metrics meant to measure handler time; the choice flips with what the number is for.
- `BaseHTTPMiddleware` is quick to write and gives you a Request/Response API, but the extra task per
  request, contextvar isolation and streaming interactions push you to pure ASGI once you stream tokens
  or propagate trace context.
- Early rejection (size limits, auth) outermost saves work on bad requests, but anything outside the
  request-ID layer logs without an ID; so the ID layer stays outermost even though it does "work" for
  traffic that is about to be rejected.
- Middleware is global per app; concerns needed on only some routes are cheaper as router-level
  dependencies, and the trade flips back when the concern must see raw ASGI events (body bytes,
  response chunks) that dependencies cannot.

## Pitfalls
- Adding CORS before (hence inside) an auth middleware that returns 401 directly; symptom: the browser
  console shows a CORS error on failed logins and preflight OPTIONS requests fail, while curl works.
- Measuring or logging after `await call_next(request)` without `try/finally`; symptom: requests that end
  in unhandled exceptions vanish from timing logs and metrics, so error latency is invisible.
- Expecting CORS headers on 500 responses; `ServerErrorMiddleware` is outside everything, so its
  response skips `CORSMiddleware`. Symptom: the front end cannot read the error body of crashes; the fix
  is an exception handler that runs inside the stack.
- Reading the body in a `BaseHTTPMiddleware` for logging and then expecting the endpoint to read it too;
  symptom: endpoints hang or receive an empty body only when request logging is enabled.
- Assuming the file order of `add_middleware` calls is execution order; symptom: a request-ID middleware
  "added first" produces IDs that the auth middleware's log lines never contain.

## When to use
Use middleware for concerns that must touch every request at the ASGI level: request IDs, CORS,
compression, body limits, tracing, global timing. Use dependencies instead when the concern is
per-route, needs typed parameters, or should appear in OpenAPI (auth schemes). Decide order by writing
the request path as a list from the edge inwards and asking, for each layer, "what must already exist
when this runs, and who must see its output on the way back?" Verify it empirically: a test that
triggers a 401 and asserts the CORS header, a test that raises in an endpoint and asserts the timing log
line. In interviews, the questions are "which middleware runs first with `add_middleware`?" and "why
does my 500 have no CORS headers?"; the mechanism above answers both.

## Role lens
- **Engineer:** Assembles the stack with the constructor list so order is explicit, writes pure ASGI
  middleware for streaming routes, and tests the 401-with-CORS case.
- **Architect:** Owns which concerns are global middleware versus route dependencies versus the gateway,
  and where trace context is first created across services.
- **FDE:** Untangles a customer's "CORS error" that is really an auth or crash problem, by tracing where
  in the stack the response was produced.

## Check yourself
**Q:** A team calls `add_middleware` three times in this order: `CORSMiddleware`, an API-key middleware
that returns 401 directly, then a `BaseHTTPMiddleware` that logs method, path, status and latency after
`call_next`. Users report CORS errors on bad keys and the latency log never shows 500s. Explain both
and fix the order.
**A:** `add_middleware` inserts on the outside, so the logging layer is outermost, then API-key, then
CORS innermost. A bad key is rejected by the API-key layer before the request reaches CORS, so the 401
has no `Access-Control-Allow-Origin` and the browser reports a CORS failure; preflight OPTIONS requests
fail the same way because CORS never gets to answer them. The logger misses 500s because an unhandled
exception propagates through `call_next` as an exception, skipping the code after it. Fix: add
middleware in reverse of the desired outer-to-inner order (or use the `middleware=` list): logging
outermost, CORS next, API-key inside CORS; and wrap `call_next` in `try/finally` so exceptions are
timed. Note that 500s produced by `ServerErrorMiddleware` still lack CORS headers unless you register
an exception handler that runs inside the stack.

## Quiz
### Q: With three `app.add_middleware()` calls A, B, C in that order, which runs first on an incoming request?
- [ ] A, because it was registered first
- [ ] B, because Starlette balances the stack
- [x] C, because each call inserts at the outside of the stack
- [ ] They run concurrently in separate tasks
> `add_middleware` inserts at index 0 of the user middleware list, and index 0 is the outermost layer,
> so the last added middleware sees the request first and the response last.

### Q: Why does a 404 from `HTTPException` reach your middleware as a response, while a `KeyError` reaches it as an exception?
- [ ] Middleware only sees exceptions that subclass `Exception`
- [x] `ExceptionMiddleware` sits inside your middleware and converts handled exceptions to responses; unhandled ones propagate outward
- [ ] `HTTPException` is raised inside a background task
- [ ] The router catches all exceptions and returns them as JSON
> Starlette places `ExceptionMiddleware` innermost, so `HTTPException` and registered handlers produce
> responses before your layers see them; anything unhandled travels through your layers to the
> outermost `ServerErrorMiddleware`.

### Q: Which placement guarantees that 401 responses from your auth layer carry CORS headers?
- [ ] Put auth outside CORS so it rejects before CORS does any work
- [x] Put CORS outside auth so the 401 passes back through it on the way out
- [ ] Order does not matter because CORS headers are added by the browser
- [ ] Register both with `@app.middleware("http")` so they share a task
> Headers are added on the response path by layers outside the one that produced the response; CORS
> must therefore wrap auth, which also lets CORS answer preflight requests before auth rejects them.
