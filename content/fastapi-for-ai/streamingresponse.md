---
id: streamingresponse
module: fastapi-for-ai
name: StreamingResponse
kind: pattern
difficulty: core
est_minutes: 14
related:
  - fastapi-for-ai/server-sent-events
  - fastapi-for-ai/websockets
  - fastapi-for-ai/async-endpoints
  - fastapi-for-ai/cancellation-on-client-disconnect
  - fastapi-for-ai/backpressure
  - fastapi-for-ai/timeouts
  - serving-inference/streaming
  - serving-inference/ttft
sources:
  - Official docs: FastAPI (Custom Response, StreamingResponse)
  - Official docs: Starlette (Responses)
  - Official docs: ASGI specification (HTTP connection scope and messages)
---

## TL;DR
`StreamingResponse` lets an endpoint hand the server an iterator instead of a finished body, so bytes
leave the process as soon as each chunk exists. For LLM endpoints that turns the wait the user feels
from "the whole generation" into "time to first token", and it stops the server holding entire
responses in memory. The price: status and headers are frozen at the first byte, so mid-stream errors
must be signalled inside the body, and nothing between the generator and the browser may buffer.

## Why it exists
The default FastAPI path is: the handler returns a value, FastAPI serialises it, Starlette sets
`Content-Length` and writes the body in one go. That is right for a 2 KB JSON answer and wrong for
anything produced incrementally. A 600-token answer decoded at roughly 40 tokens per second (an
illustrative figure; it varies by model and load) exists in full only after about 15 seconds, and
until then the client sees nothing. Three things go wrong at once. The user experience collapses,
because people judge responsiveness by the first visible change. Infrastructure timeouts trip:
reverse proxies and load balancers commonly ship with request or idle timeouts in the tens of seconds,
so long answers die with a 502 or 504 even though the model was still working. And memory scales with
concurrency, because every in-flight request keeps its whole output alive until the end.

The naive fixes fail. Raising every timeout keeps the connection open but does nothing for first-token
latency and still ties up a connection per request. Polling a job endpoint adds a round trip per poll
and a latency granularity equal to the poll interval. WebSockets solve a different problem, two-way
traffic, and bring a stateful connection with them. And you cannot simply return a generator from a
normal endpoint: FastAPI would try to serialise it as JSON. You need an explicit opt-out from the
buffered response pipeline, and that opt-out is `StreamingResponse`.

## How it works
### What you hand over
`StreamingResponse(content, status_code=200, headers=None, media_type=None, background=None)` takes
any iterable or async iterable that yields `bytes` or `str` (strings are encoded with the response's
charset, UTF-8 by default). The class is Starlette's; FastAPI re-exports it from `fastapi.responses`.
Because you construct the response yourself, `response_model` validation and FastAPI's JSON encoding
are bypassed: what you yield is what goes on the wire.

### What happens at the ASGI layer
A normal response sends one `http.response.start` message (status, headers) followed by a single
`http.response.body` with `more_body: False`. A streaming response sends the start message, then one
`http.response.body` with `more_body: True` per chunk, then an empty body with `more_body: False` to
close. No `Content-Length` is known up front, so over HTTP/1.1 the server (uvicorn) uses
`Transfer-Encoding: chunked`; behind a proxy that terminates HTTP/2 the chunks simply become DATA
frames. This is why you must not set `Content-Length` yourself unless you really know it.

### Sync versus async iterators
If `content` is a plain generator, Starlette drives it with `iterate_in_threadpool`: each `next()` is
run on a worker thread so it cannot block the event loop. That is safe but costs a thread hop per
chunk and occupies a slot in the shared thread pool (anyio's default limiter is 40 threads) for the
life of the stream. An `async def` generator is awaited directly on the loop, which is what you want
when the source is an async SDK stream or an `httpx` streaming response. Mixing the two, for example
calling a blocking SDK inside an async generator, blocks every other request for the duration of
each call.

### Flow control
Each `await send(chunk)` returns only when the server has accepted the chunk. Uvicorn pauses writers
when the transport's buffer passes its high-water mark, so a client that reads slowly eventually
stalls the generator. That is natural backpressure, but it engages only after the OS and server
buffers fill, so a stalled client can still hold tens of kilobytes of generated text and, more
importantly, an upstream model stream that keeps billing tokens.

### Disconnects
When the client goes away the server delivers an `http.disconnect` event. Recent Starlette versions
run the generator inside a task group next to a listener for that event and cancel the generator
when it fires, so an `async for` over the model stream receives `CancelledError` and can propagate
the cancellation upstream. The exact behaviour has changed across Starlette releases, so the robust
pattern is to also check `await request.is_disconnected()` inside the loop and to put cleanup in
`try/finally`. The point is economic as much as technical: tokens generated after the user closed the
tab are pure cost.

### Headers are frozen at the first chunk
Status and headers go out before the first body chunk. An exception raised by the generator after
that cannot become a 500; the client sees a truncated body and, with chunked encoding, a missing
terminating chunk that most clients report as a protocol error. So do everything that can fail,
authentication, quota checks, input validation, ideally even the first call to the model, *before*
returning the `StreamingResponse`, and signal failures during the stream in-band with a typed chunk.

### Framing
TCP does not preserve your `yield` boundaries: a client may receive two chunks glued together or one
chunk split, even in the middle of a multi-byte character. Raw text is therefore only acceptable when
the client treats the body as a single growing string. For anything structured pick a framing and set
`media_type` to match: newline-delimited JSON (`application/x-ndjson`), Server-Sent Events
(`text/event-stream`, covered in its own lesson), or length-prefixed binary.

### Middleware and proxies
Any component that waits for the whole body destroys the benefit. nginx buffers proxied responses by
default (`proxy_buffering on`); send `X-Accel-Buffering: no` or turn buffering off for the route.
Some CDNs and API gateways buffer too, and compression middleware can delay small chunks while it
fills a compression window. Always test with `curl -N` against the real ingress, not just uvicorn on
localhost.

## Example
An NDJSON token stream. Validation happens before the response object is created, the loop stops
generating when the client has gone, and a failure after the first byte becomes a typed error line
instead of a truncated body.

```python
import asyncio, json
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI()

class Ask(BaseModel):
    prompt: str

async def model_stream(prompt: str):
    # Stand-in for your SDK's async streaming iterator; both major vendor SDKs expose one.
    for word in "Streaming keeps the user reading while the model is still decoding.".split():
        await asyncio.sleep(0.05)
        yield word + " "

@app.post("/answer")
async def answer(body: Ask, request: Request):
    if len(body.prompt) > 8000:
        raise HTTPException(413, "prompt too long")      # fail BEFORE any byte of the stream is sent

    async def ndjson():
        try:
            async for piece in model_stream(body.prompt):
                if await request.is_disconnected():
                    break                                 # stop paying for tokens nobody will read
                yield json.dumps({"delta": piece}) + "\n"
            yield json.dumps({"done": True}) + "\n"
        except Exception as exc:                          # headers are gone: signal the error in-band
            yield json.dumps({"error": type(exc).__name__}) + "\n"

    return StreamingResponse(ndjson(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# Check it really streams through your ingress, not just locally:
#   curl -N -X POST http://localhost:8000/answer -H 'content-type: application/json' -d '{"prompt":"hi"}'
```

## Trade-offs
- First-token latency drops to the model's own TTFT, but total server time per request does not shrink
  and the connection stays open for the whole generation, so capacity must be sized by concurrent
  streams rather than by requests per second.
- You stop buffering whole bodies in memory, but you lose `Content-Length`, so clients cannot show a
  percentage and HTTP caches will not store the response; for small, cacheable payloads a plain
  `JSONResponse` is the better choice.
- In-band error chunks let the client keep partial output, but every consumer must now parse a protocol
  instead of checking a status code; when the consumers are third-party integrators expecting REST
  semantics, offer a non-streaming variant as well.
- A sync generator works without rewriting a blocking source, but every chunk costs a thread hop; fine
  for reading a file in 64 KB blocks, wasteful for thousands of token-sized chunks per second.

## Pitfalls
- Raising `HTTPException` inside the generator; the symptom is a client-side "incomplete chunked
  encoding" or truncated body while the server log shows an exception after a 200 was already sent.
- A buffering proxy between you and the user; the symptom is a stream that works with uvicorn locally
  but arrives as one burst at the very end in production, often followed by gateway timeouts on long
  answers.
- Using a request-scoped resource from a `yield` dependency (a DB session) inside the generator; its
  teardown can run before the generator body executes, and the timing has changed across FastAPI
  versions, so the symptom is "session is closed" errors that appear only after an upgrade.
- Yielding raw text and assuming one `yield` equals one client read; the symptom is tokens glued
  together or a split multi-byte character that decodes as garbage on the client.
- Awaiting nothing while calling a blocking SDK inside an `async def` generator; the symptom is that
  every other request, including health checks, stalls while one stream is being produced.

## When to use
Reach for `StreamingResponse` whenever output is produced incrementally and the consumer benefits from
seeing it early: LLM token streams, long agent runs that emit progress, large exports generated row by
row, proxying an upstream streaming API. Do not use it for small responses, for anything a CDN should
cache, or as a substitute for a job queue when the work must survive a disconnect. Measure time to
first byte at the client, bytes in flight per connection, and the fraction of streams cancelled by
disconnects. In interviews the expected depth is the ASGI message sequence, why headers are frozen,
how disconnects and backpressure reach the generator, and what breaks at the proxy layer.

## Role lens
- **Engineer:** Writes async generators that wrap the model SDK's stream, frames chunks as NDJSON or
  SSE, validates before the first byte and checks for disconnects inside the loop.
- **Architect:** Decides which routes stream, sets ingress and load-balancer buffering and timeout
  policy for them, and sizes capacity by concurrent open streams instead of request rate.
- **FDE:** Diagnoses the customer's "it works in staging but not behind our gateway" by walking the
  path from uvicorn through nginx, API gateway and CDN looking for the buffer.

## Check yourself
**Q:** A streaming chat endpoint works locally, but in production tokens arrive in a single burst
when the answer finishes, and answers longer than about a minute fail with a 504. Diagnose it.
**A:** Both symptoms point at an intermediary that buffers the response. The burst means something is
collecting chunks until the body completes: nginx `proxy_buffering`, an API gateway or CDN, or a
compression layer. Because no bytes reach the load balancer while it buffers, the balancer's idle
timeout fires on long answers and returns 504. Confirm with `curl -N` against each hop in turn, then
disable buffering for the route (`X-Accel-Buffering: no` or `proxy_buffering off`), exclude the route
from compression, and raise the idle timeout only as a last resort. Also check that the endpoint is
not accidentally setting `Content-Length`, and add periodic keepalive chunks for silent phases.

## Quiz
### Q: Why can a StreamingResponse not change its status code to 500 when the generator fails halfway?
- [ ] Starlette only allows status changes for JSONResponse
- [x] Status and headers are sent in the `http.response.start` message before the first chunk, so they cannot be revised
- [ ] Chunked transfer encoding does not support status codes
- [ ] The event loop has already moved on to the next request
> The ASGI protocol emits the start message once, before any body bytes. After that, only body chunks
> can follow, which is why mid-stream failures must be signalled in-band as a typed chunk.

### Q: What is the main reason to prefer an async generator over a sync generator as the content?
- [ ] Sync generators cannot yield bytes
- [ ] Async generators automatically set Content-Length
- [x] Each item from a sync generator is pulled on a thread-pool thread, costing a hop per chunk and a pool slot for the stream's lifetime
- [ ] Sync generators bypass disconnect detection entirely
> Starlette wraps sync iterators with `iterate_in_threadpool` to keep the loop unblocked. That is
> correct but expensive for thousands of small chunks and it consumes a limited thread pool.

### Q: A client disconnects in the middle of a token stream. What should the endpoint be designed to do?
- [ ] Nothing; the server automatically returns the unused tokens to the model provider
- [ ] Retry the request so the client receives the full answer on reconnect
- [x] Detect the disconnect, stop iterating, and cancel the upstream model call so no further tokens are generated or billed
- [ ] Switch the response to a BackgroundTask so generation completes anyway
> Tokens generated after the client left are pure cost. Starlette cancels the generator on disconnect
> in recent versions, and checking `request.is_disconnected()` with cleanup in `finally` makes that
> behaviour explicit and version-independent.
