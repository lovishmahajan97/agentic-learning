---
id: server-sent-events
module: fastapi-for-ai
name: Server-Sent Events
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - fastapi-for-ai/streamingresponse
  - fastapi-for-ai/websockets
  - fastapi-for-ai/cancellation-on-client-disconnect
  - fastapi-for-ai/timeouts
  - serving-inference/streaming
  - agent-reliability/structured-event-streams
sources:
  - Official docs: WHATWG HTML Living Standard (server-sent events)
  - Official docs: MDN (Using server-sent events)
  - Official docs: sse-starlette
---

## TL;DR
Server-Sent Events (SSE) is a one-directional, text-based streaming format carried in an ordinary HTTP
response of type `text/event-stream`: events are blocks of `field: value` lines separated by a blank
line. It exists because a raw chunked body has no message boundaries and WebSockets are a heavier,
stateful protocol for what is usually a one-way token flow. Browsers reconnect automatically and resend
`Last-Event-ID`, which is why LLM vendor APIs and most chat UIs stream tokens over SSE.

## Why it exists
`StreamingResponse` gets bytes out early, but bytes are not messages. A chat client needs to tell a
token delta from a tool-call announcement from a usage report from an error, and TCP may split or
merge whatever the server yielded. Every team that starts with raw chunks ends up inventing a framing,
and every client then reimplements the parser slightly differently.

The obvious alternative is WebSockets, and for one-way traffic it is the wrong tool: an upgrade
handshake, a stateful connection pinned to one process, no automatic reconnection, no standard way to
resume, and infrastructure that must understand upgrades. Token streaming is server-to-client; the
only client input is the request that started it.

SSE sits in the gap. It was standardised as part of the HTML specification, so every browser ships a
parser (`EventSource`), the stream is a normal HTTP response that proxies, load balancers and
authentication middleware already understand, HTTP/2 multiplexes many streams over one connection, and
the format has two features a token stream needs: a typed `event:` field so one stream can carry tokens
and control messages, and an `id:` field so a client that drops mid-answer can ask for the rest instead
of paying for the generation again. Those two fields are the whole reason it beats "newline-delimited
JSON" for user-facing LLM streams.

## How it works
### The wire format
```
event: token
data: {"delta": "Hel"}
id: 1

event: token
data: {"delta": "lo"}
id: 2

: keepalive

event: done
data: {"output_tokens": 2}

```
Each line is `field: value`. A blank line dispatches the event. Rules that matter in practice: `data:`
is the payload and several `data:` lines in one event are joined with newlines; `event:` names the type
(default `message`); `id:` sets the client's last-event id; `retry:` tells the client how many
milliseconds to wait before reconnecting; a line starting with `:` is a comment that clients discard,
which makes it a free keepalive. The body is UTF-8 text, so binary payloads must be base64-encoded.
The response carries `Content-Type: text/event-stream` and should carry `Cache-Control: no-cache`;
behind nginx add `X-Accel-Buffering: no`.

### The client side
`EventSource` is built into browsers: it opens a GET, parses events, fires listeners by `event:` name,
reconnects automatically when the connection drops and sends the `Last-Event-ID` header on reconnect.
Reconnection is driven by the browser: after a drop it waits the `retry:` interval (a few seconds by
default, browser-dependent) and reissues the GET; a server that wants a stream to end for good must
respond to that reconnect with HTTP 204, or the client must call `close()`, otherwise a finished run is
re-requested forever. Its limits decide how chat UIs are built: it can only GET, cannot set an `Authorization` header or a
body, and sends credentials only via cookies. A chat prompt is a POST with a JSON body and a bearer
token, so production frontends use `fetch()` and parse the stream themselves (a `ReadableStream`, a
`TextDecoder` and a line splitter, or a small library), which means they also own reconnection logic.
Server-side consumers use `httpx`'s `client.stream("POST", ...)` context manager with `aiter_lines()`
(`stream=True` is the `requests` spelling, not httpx's); vendor SDKs parse the vendor's own SSE for you.

### The server side in FastAPI
The minimum is a `StreamingResponse` whose generator yields correctly formatted strings with
`media_type="text/event-stream"`. The `sse-starlette` package adds an `EventSourceResponse` that
formats dict events, sends periodic ping comments, and handles client disconnects, which removes the
most common mistakes. Either way the endpoint is an ordinary route: dependencies, auth and validation
run before the first byte, which is exactly when errors can still be reported with a status code.

### Keepalives and intermediaries
Load balancers and proxies drop connections that carry no bytes for a configurable idle period, often
on the order of a minute by default (vendor-dependent). An agent that spends ninety seconds inside a
tool call before producing the next token looks idle. Sending a comment line every 10 to 20 seconds
keeps the timers reset at no cost to the client. The same intermediaries must also not buffer; see
the StreamingResponse lesson.

### HTTP/1.1 connection limits
Browsers limit concurrent HTTP/1.1 connections per origin to a small number, around six, and every open
`EventSource` holds one. Several tabs with live streams can starve the rest of the app. HTTP/2 lifts
the limit by multiplexing, so terminate HTTP/2 at the edge; uvicorn speaking HTTP/1.1 behind that
proxy is fine.

### Resumability
`Last-Event-ID` is only useful if the server can replay. The pattern: give each run an id, assign
monotonic event ids, append every event to a short-lived buffer (a Redis list with a TTL keyed by run
id) as it is produced, and on a request carrying `Last-Event-ID` replay the buffered events after that
id before continuing live. For agent runs that cost real money per attempt this converts a flaky
mobile connection from "re-run the whole thing" into "catch up".

### Event vocabularies
Vocabularies differ by vendor and by API generation, so check the current docs. OpenAI's Chat
Completions API streams untyped JSON chunks in `data:` lines and ends with a literal `data: [DONE]`,
while its newer Responses API uses named events such as `response.output_text.delta`; Anthropic's
Messages API streams typed events such as `message_start`, `content_block_delta` and `message_stop`.
Your own API should define its own small vocabulary (`token`, `tool_call`, `usage`, `done`, `error`)
and translate, so that swapping the model provider never changes the frontend.

## Example
A FastAPI SSE endpoint built directly on `StreamingResponse`. The model stream runs in its own task
feeding a queue, so the consumer can wait with a timeout and emit a keepalive comment during silent
phases without cancelling the producer. Each event carries a monotonic id; this endpoint does not
replay, but those ids are exactly what a resume endpoint (see Resumability) would key its buffer on.
With tokens 0.3 s apart nothing is silent for 15 s, so here the ping only fires if the model stalls,
for instance during a long tool call.

```python
import asyncio, json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI()

class Ask(BaseModel):
    prompt: str

def sse(event: str, data: dict, id: int) -> str:
    return f"event: {event}\nid: {id}\ndata: {json.dumps(data)}\n\n"   # blank line dispatches

async def model_stream(prompt: str):                  # stand-in for your SDK's async stream
    for w in "Server-sent events frame the tokens.".split():
        await asyncio.sleep(0.3); yield w + " "

@app.post("/chat")
async def chat(body: Ask):
    q: asyncio.Queue = asyncio.Queue()

    async def produce():
        try:
            async for piece in model_stream(body.prompt):
                await q.put(("token", {"delta": piece}))
            await q.put(("done", {}))
        except Exception as exc:
            await q.put(("error", {"message": str(exc)}))

    async def events():
        task, n = asyncio.create_task(produce()), 0
        try:
            while True:
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"; continue       # keepalive comment; clients ignore it
                n += 1
                yield sse(event, data, n)
                if event in ("done", "error"):
                    break
        finally:
            task.cancel()                              # client gone or stream over: stop generating
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

## Trade-offs
- Plain HTTP means SSE passes through proxies, auth middleware and HTTP/2 multiplexing untouched, but
  it is one-directional: an "interrupt generation" button needs a second request plus shared state,
  and once the client must talk continuously (voice, collaborative editing) WebSockets win.
- Text-only framing keeps parsers trivial, but binary payloads such as audio must be base64-encoded,
  adding about a third to their size; at that point a binary WebSocket frame is cheaper.
- Automatic reconnection with `Last-Event-ID` gives resumability for almost no client code, but only if
  the server buffers events per run; without the buffer a reconnect silently restarts and double-bills
  the generation.
- `EventSource` is zero code but GET-only and header-less; parsing with `fetch()` gives you POST bodies
  and bearer tokens at the cost of owning reconnection and the parser yourself.

## Pitfalls
- Forgetting the blank line that ends each event (a single newline only separates fields); the
  symptom is a client that receives nothing, because a conforming parser discards an undispatched
  event at end of stream, or a hand-rolled parser that glues everything into one malformed event.
- Putting raw text with embedded newlines into one `data:` line; the symptom is events split at the
  wrong place, which JSON-encoding the payload (newlines become `\n`) avoids entirely.
- No keepalive during long tool calls; the symptom is streams that die at a fixed wall-clock interval,
  often exactly the load balancer's idle timeout, only when the agent is "thinking".
- A buffering proxy or compression middleware on the route; the symptom is all events arriving in a
  burst at the end, with the server logs showing they were produced on time.
- Ending a run by simply closing the response while the client uses `EventSource`; the symptom is a
  fresh GET, and possibly a fresh billed generation, every few seconds after each answer finishes,
  because the browser treats a closed stream as a dropped one; send a `done` event the client acts on
  by calling `close()`, and answer reconnects for finished runs with 204.
- Leaking the vendor's event schema straight to the browser; the symptom is a model-provider switch
  that breaks the frontend parser.
- Replaying a run from `Last-Event-ID` without re-checking that the caller owns that run; the symptom
  is one user resuming another user's stream by guessing an id.

## When to use
Use SSE for anything the server pushes and the client only consumes: token streams, agent progress
and tool-call events, job status updates, live evaluation dashboards. Prefer it over WebSockets by
default because it keeps the stateless HTTP model, and move to WebSockets only when the client must
send during the stream or needs binary frames. Measure client-observed time to first event, event
counts per run, reconnect rate and the share of streams lost to idle timeouts. In interviews, expect to
write the frame format from memory, explain why chat UIs cannot use `EventSource` for POST, and
describe how `id:` plus a per-run buffer makes a stream resumable.

## Role lens
- **Engineer:** Implements the event vocabulary, the formatter and the keepalive, and makes sure every
  failure after the first byte is emitted as an `error` event rather than a dropped connection.
- **Architect:** Chooses SSE versus WebSockets per route, mandates HTTP/2 at the edge, and defines the
  resumability contract (run ids, event ids, buffer TTL) that clients can rely on.
- **FDE:** Traces a customer's dying streams to an idle timeout on their corporate proxy and fixes it
  with keepalive comments instead of asking their network team for an exception.

## Check yourself
**Q:** A mobile client loses connectivity thirty seconds into a two-minute agent run. Design the server
so the user does not pay for a second run, and explain what the client must do.
**A:** Give each run an id when it starts and assign monotonically increasing `id:` values to every
event. As events are produced, append them to a per-run buffer with a TTL, for example a Redis list,
in the same step that sends them. On reconnect the client sends `Last-Event-ID` (automatically with
`EventSource`, manually with a `fetch`-based client) to a resume endpoint that first checks the caller
owns the run, replays buffered events after that id, then attaches to the live stream. Generation
itself must keep running independently of the HTTP connection, so it belongs in a worker task or job
rather than inside the request's generator, and the response generator becomes a reader of the buffer.

## Quiz
### Q: What does a line beginning with a colon mean in an SSE stream?
- [ ] It begins a multi-line data payload
- [x] It is a comment the client ignores, which makes it a free keepalive to reset idle timers
- [ ] It sets the reconnection delay in milliseconds
- [ ] It terminates the current event
> Comment lines are discarded by every conforming parser, so a periodic `: ping` keeps proxies and
> load balancers from closing a connection that is silent while the model or a tool is working.

### Q: Why do chat frontends usually parse SSE from a fetch() call instead of using EventSource?
- [ ] EventSource cannot parse the `event:` field
- [ ] fetch() reconnects automatically while EventSource does not
- [x] EventSource only issues GET requests without custom headers or a body, but a chat turn is a POST with JSON and a bearer token
- [ ] Browsers limit EventSource to one connection per page
> The built-in API has no way to send a request body or an Authorization header. Fetch-based parsing
> gives POST and headers back, at the cost of implementing reconnection and the line parser yourself.

### Q: What must the server do for Last-Event-ID based resumption to work?
- [ ] Set `retry:` on every event
- [ ] Use WebSockets, because SSE cannot resume
- [x] Assign event ids and buffer each run's events so it can replay those after the id the client last saw
- [ ] Send `Content-Length` so the client knows how much is missing
> Reconnection only carries the last id the client saw; replaying from that point requires the server
> to have kept the events. Without a per-run buffer the reconnect simply starts a new generation.
