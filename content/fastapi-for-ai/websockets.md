---
id: websockets
module: fastapi-for-ai
name: WebSockets
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - fastapi-for-ai/server-sent-events
  - fastapi-for-ai/streamingresponse
  - fastapi-for-ai/api-keys-oauth2-jwt
  - fastapi-for-ai/backpressure
  - fastapi-for-ai/cancellation-on-client-disconnect
  - fastapi-for-ai/uvicorn-gunicorn-workers
  - serving-inference/cancellation
sources:
  - Fette & Melnikov, 2011 — RFC 6455, The WebSocket Protocol
  - Official docs: FastAPI (WebSockets)
  - Official docs: ASGI specification (WebSocket connection scope)
  - Official docs: Uvicorn (settings)
---

## TL;DR
A WebSocket upgrades one HTTP connection into a persistent, full-duplex channel that carries framed
text or binary messages in both directions. Reach for it when the client must talk *while* the server
is streaming: interrupting a generation, two-way voice audio, agent sessions with mid-run approvals.
The price is state: the connection is pinned to one process, authentication, reconnection and resume
are hand-rolled, and every load balancer in the path must understand long-lived upgrades. For one-way
token streaming, SSE is simpler.

## Why it exists
HTTP is request then response. Once the client has sent its body it cannot say anything else on that
exchange, and the server cannot push anything the client did not ask for. Two AI interactions break
that model. The first is interruption: a user watching tokens arrive over SSE presses stop, and that
stop must reach the task producing the tokens. Over HTTP it becomes a second request, which the load
balancer may route to a different worker process, so now you need shared state (a Redis pub/sub
channel, a cancel flag in a store) just to deliver one bit to one coroutine. The second is continuous
two-way media: a voice agent receives audio frames every few tens of milliseconds while sending
synthesized audio back. A request per frame is absurd in overhead and ordering; long-polling hacks add
latency and complexity without removing either problem.

The WebSocket protocol (RFC 6455) was designed for exactly this: a single TCP connection, established
through an HTTP handshake so it passes existing infrastructure, after which both sides send messages
with a two-to-fourteen-byte frame header and no further HTTP ceremony. Both AI needs map onto it
directly: the stop message arrives on the same socket the tokens are leaving on, so it lands in the
same process with no shared state, and audio travels as binary frames without base64. What you accept
in exchange is that the connection, not the request, is now the unit of state.

## How it works
### The handshake
The client sends a GET with `Upgrade: websocket`, `Connection: Upgrade` and a random
`Sec-WebSocket-Key`. The server answers `101 Switching Protocols` with `Sec-WebSocket-Accept`, a hash
of the key and a fixed GUID that proves it understood the upgrade. From then on the bytes are frames,
not HTTP. Two consequences follow from the handshake being a plain GET. Browsers cannot attach an
`Authorization` header to it, because the JavaScript `WebSocket` API has no header option, so auth
arrives via a cookie, a short-lived token in the query string (which lands in access logs), or a first
message after connect that the server must receive before doing any work. And CORS does not protect
it: if browsers are clients and cookies carry auth, check the `Origin` header yourself, otherwise any
site can open a socket to your API with the user's cookies.

### Frames, messages and closes
Messages are text (must be valid UTF-8) or binary. Control frames carry ping/pong, which servers use to
detect dead peers, and close, which carries a code: 1000 normal, 1001 going away (server shutting
down), 1008 policy violation (a common choice for auth failures), 1011 internal error. Uvicorn exposes
the knobs: `--ws-max-size` caps a message (default 16 MiB), and `--ws-ping-interval` /
`--ws-ping-timeout` (20 seconds each by default) decide how quickly a silent, dead client is dropped.
Uvicorn needs the `websockets` or `wsproto` package for WebSocket support, which `uvicorn[standard]`
installs.

### ASGI and FastAPI
A WebSocket arrives as a scope of type `websocket`. The app receives `websocket.connect`, answers with
`websocket.accept` (or `websocket.close` to refuse, which the server turns into an HTTP 403 before
upgrade), then exchanges `websocket.receive` and `websocket.send` until `websocket.disconnect`.
FastAPI wraps this: `@app.websocket("/ws")`, a `WebSocket` parameter, `await websocket.accept()`,
`receive_text/json/bytes` and `send_text/json/bytes`, and a `WebSocketDisconnect` exception when the
peer closes. Dependencies, path and query parameters, cookies and headers all work in WebSocket routes.
`HTTPException` does not fit a connection that is no longer HTTP; raise `WebSocketException(code=...)`
from a dependency instead, and FastAPI closes with that code. Note where in the lifecycle you are: a
close *before* `accept()` never reaches the browser as a WebSocket close code, because no WebSocket
exists yet; the server rejects the upgrade with HTTP 403 and the browser's `close` event reports the
generic 1006 (abnormal closure). Reject before accept when you want unauthenticated clients to cost
nothing; accept first and then close with 1008 or a 4000-range application code when the client needs
to know *why*, for example to refresh its token instead of retrying blindly.

### Concurrency inside the handler
A handler is one coroutine. If it awaits `receive_json()` it is not producing tokens, and if it is
iterating the model stream it is not listening, so a sequential loop can never implement interruption.
The pattern is two concurrent activities: a reader loop that handles incoming messages, and a
generation task started with `asyncio.create_task` that the reader can signal (an `asyncio.Event`) or
cancel. Funnel all outgoing sends through one place, for example the generation task or a single
sender task fed by a queue, so two tasks never interleave frames.

### Scaling and state
The socket lives in one worker process. Messages from the client reach that process for free, which is
the point, but anything originating elsewhere (a job finishing on a worker machine, a message from
another user) must be fanned out to the process holding the socket, usually through Redis pub/sub.
Load balancers must allow upgrades and keep long idle connections alive; many default to about a
minute of idle time, so application-level pings matter. Rolling deploys must close sockets deliberately
(code 1001) and clients must reconnect and resume, which means you design a session id and sequence
numbers yourself: there is no `Last-Event-ID` equivalent.

### Backpressure
With uvicorn's protocol implementations, `await websocket.send_text()` waits when the peer reads
slowly and the transport's write buffer passes its high-water mark, which throttles the generation
loop, exactly what you want. Sending with fire-and-forget tasks removes that coupling and lets memory
grow without bound. On the receive side the `websockets`-based implementation keeps a bounded queue of
incoming messages (uvicorn's `--ws-max-queue`, default 32) and stops reading from the socket when it
is full, so TCP pushes back on the sender; check your implementation before relying on it.

## Example
A chat socket where the server streams tokens from a generation task while the reader loop stays free
to receive a `stop` message, which interrupts the current answer. Auth is a query token because the
browser cannot send headers on the upgrade.

```python
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, WebSocketException, status

app = FastAPI()

async def model_stream(prompt: str):                 # stand-in for your SDK's async stream
    for w in "A WebSocket lets the user interrupt this sentence midway.".split():
        await asyncio.sleep(0.4); yield w + " "

async def generate(ws: WebSocket, prompt: str, stop: asyncio.Event):
    async for piece in model_stream(prompt):
        if stop.is_set():
            await ws.send_json({"type": "stopped"}); return
        await ws.send_json({"type": "token", "delta": piece})
    await ws.send_json({"type": "done"})

@app.websocket("/ws/chat")
async def chat(ws: WebSocket, token: str):           # ?token=... (short-lived); verify before accept
    if token != "demo":
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    await ws.accept()
    stop, task = asyncio.Event(), None
    try:
        while True:                                   # reader loop: never blocked by generation
            msg = await ws.receive_json()
            if msg["type"] == "prompt":
                if task and not task.done():
                    stop.set(); await task             # finish the old answer cleanly first
                stop = asyncio.Event()
                task = asyncio.create_task(generate(ws, msg["text"], stop))
            elif msg["type"] == "stop":
                stop.set()
    except WebSocketDisconnect:
        pass
    finally:
        if task:
            task.cancel()                             # client gone: stop paying for tokens
```

## Trade-offs
- Full-duplex messaging with tiny per-message overhead, but the connection is stateful and pinned to a
  process, so cross-process delivery needs pub/sub; when traffic is one-way the statelessness of SSE
  is worth more than the duplex channel.
- Binary frames carry audio and images without base64's roughly one-third size overhead, but you
  must define your own message typing and framing on top; for JSON-only, server-to-client protocols
  that freedom buys nothing over SSE's built-in `event:` and `id:` fields.
- One long-lived connection avoids a handshake and auth check per message, but thousands of idle
  sockets each hold memory and a slot in load balancers and worker processes, and shutdowns become a
  coordination problem instead of waiting for in-flight requests to finish.
- No built-in reconnection or resume means you design session ids and sequence numbers, which is more
  work than SSE's `Last-Event-ID` but also lets you resume with arbitrary semantics.

## Pitfalls
- A sequential handler that awaits `receive` and then generates; the symptom is a stop button that only
  takes effect after the answer has finished.
- Putting a long-lived credential in the query string; the symptom is secrets in access logs and proxy
  logs, which is why upgrade tokens must be short-lived or auth must happen in the first message.
- An ingress not configured for upgrades or with a short idle timeout; the symptom is 400 or 502 on
  connect, or every socket dying after exactly the same number of silent seconds.
- Raising `HTTPException` in a WebSocket route or dependency; the symptom is an unhelpful 500 or an
  abrupt close instead of a meaningful close code.
- Expecting the browser to see your auth close code when you reject before `accept()`; the symptom is a
  client that only ever sees 1006 and retries in a tight loop instead of refreshing its credentials.
- Sending from several tasks or with fire-and-forget `create_task`; the symptom is messages arriving
  in an order no single task produced (a `done` before the last token),
  `RuntimeError: Cannot call "send" once a close message has been sent`, or memory growth with slow
  clients.
- Not cancelling the generation task on `WebSocketDisconnect`; the symptom is model spend continuing
  after users close the tab, visible as a gap between tokens billed and tokens delivered.
- Skipping the `Origin` check with cookie auth; the symptom is cross-site WebSocket hijacking found by
  the first security review.

## When to use
Choose WebSockets when the client must send during the server's stream: interrupt or steer a running
generation, voice and other continuous media, interactive agent sessions where a human approves tool
calls mid-run, and multi-party rooms. Choose SSE when the server talks and the client listens, which
is most chat products. Measure connection counts per process, message latency in both directions, time
to detect dead peers, and reconnect success rate during deploys. Interviewers probe whether you know
why auth is awkward on the handshake, how to make a handler both read and write concurrently, and
what breaks when you scale from one process to many.

## Role lens
- **Engineer:** Writes handlers with a reader loop and a cancellable generation task, chooses close
  codes deliberately, and keeps all sends on one task so frames never interleave.
- **Architect:** Decides which products justify stateful connections, specifies the pub/sub fan-out,
  sticky routing and reconnect contract, and sets ingress timeouts and ping intervals.
- **FDE:** Gets a customer's voice agent through their corporate proxy and load balancer, which usually
  means proving the upgrade is allowed and tuning idle timeouts and pings.

## Check yourself
**Q:** Product wants a stop button for a chat that currently streams over SSE from four uvicorn
workers. Compare adding a cancel endpoint with moving the chat to WebSockets, and say which you pick.
**A:** With SSE the stop becomes a `POST /runs/{id}/cancel` that may land on any of the four workers,
so the generating worker must learn about it through shared state: a cancel flag in Redis checked
between tokens, or a pub/sub channel the generator subscribes to. That is a small amount of code and
keeps the stateless HTTP model, auth and resumability. With WebSockets the stop arrives on the same
socket as the tokens, so cancellation is local, but you take on handshake auth, origin checks,
reconnection and resume, ingress configuration and shutdown handling. For a text chat whose only
client input is stop, I keep SSE and add the Redis-backed cancel, and I move to WebSockets only if the
product also needs voice or continuous client input.

## Quiz
### Q: Why can a browser not send an Authorization header on the WebSocket handshake?
- [ ] The handshake is encrypted before headers are added
- [x] The JavaScript WebSocket API offers no way to set request headers, so auth must use cookies, a query token or a first message
- [ ] WebSocket servers strip all headers after the upgrade
- [ ] Authorization headers are only valid on POST requests
> The upgrade is a GET the browser issues on your behalf with no header option. This forces the
> cookie, short-lived query token or post-connect auth-message patterns, each with its own trade-off.

### Q: In a FastAPI WebSocket handler, why does supporting interruption require a second task?
- [ ] FastAPI forbids awaiting the model inside a WebSocket route
- [ ] A single coroutine cannot send more than one frame
- [x] One coroutine cannot both await the next incoming message and iterate the model stream at the same time
- [ ] The event loop processes WebSocket frames on a separate thread
> The handler is one coroutine; whatever it is awaiting, it is not doing the other thing. A reader loop
> plus a cancellable generation task is the standard shape, with sends kept on one task.

### Q: What does SSE provide out of the box that WebSockets do not?
- [ ] Binary frames for audio
- [ ] Bidirectional messages on one connection
- [x] Automatic client reconnection that resends the last event id seen
- [ ] A ping/pong mechanism for detecting dead peers
> `EventSource` reconnects by itself and sends `Last-Event-ID`; with WebSockets you design session ids,
> sequence numbers and the reconnect loop. Ping/pong and binary frames are WebSocket features, not SSE.
