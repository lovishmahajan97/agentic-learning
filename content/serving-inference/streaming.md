---
id: streaming
module: serving-inference
name: Streaming (SSE)
kind: pattern
difficulty: core
est_minutes: 15
related:
  - serving-inference/ttft
  - serving-inference/tpot
  - serving-inference/cancellation
  - serving-inference/timeouts
  - serving-inference/backpressure
  - fastapi-for-ai/streamingresponse
  - fastapi-for-ai/websockets
  - agent-reliability/structured-event-streams
sources:
  - Official docs: WHATWG HTML Living Standard, server-sent events
  - Official docs: OpenAI API streaming
  - Official docs: Anthropic Messages API streaming
---

## TL;DR
A 500-token answer at 20 ms per token takes 10 s to finish, but the first token exists after a few
hundred milliseconds. Streaming sends each token as it is decoded, so the latency a user feels becomes
time-to-first-token rather than total time. Server-Sent Events (SSE) is the usual transport because it
is plain one-directional HTTP that proxies and provider APIs already speak. The price: errors, usage
and tool calls arrive piecemeal, and nothing between GPU and browser may buffer.

## Why it exists
Autoregressive decoding produces output one token at a time, and the total time is roughly
`TTFT + (n_tokens − 1) × TPOT`. For a chat answer the second term dominates: a few hundred tokens at
tens of milliseconds each is several seconds of pure waiting in which the server already has most of
the answer and the user sees nothing. People judge responsiveness by the first visible change, not by
completion, so a non-streaming UI feels broken at exactly the output lengths that make LLMs useful.

The naive fixes all fail somewhere. Polling a job endpoint adds a round trip per poll, gives latency
granularity equal to the poll interval and multiplies request volume. Returning a raw chunked
`text/plain` body gets tokens out early but has no framing: the client cannot tell a token from a
mid-stream error, a tool-call argument fragment or the final usage record. WebSockets give full
duplex and framing, but they need a protocol upgrade, long-lived connection state, their own
reconnect logic and are the thing most likely to be blocked or mishandled by a corporate proxy, all
for a channel where only one side talks.

SSE sits in the gap: ordinary HTTP response, `Content-Type: text/event-stream`, a trivial line-based
framing with named events, and native browser support. That is why OpenAI- and Anthropic-style APIs
stream over it, and why every serving stack between the engine and the screen has to carry it
without buffering.

## How it works
### The wire format
An SSE body is UTF-8 text made of events separated by a blank line. Each line is `field: value`.
The fields that matter are `data:` (the payload; several `data:` lines in one event are joined with
newlines), `event:` (an optional name the client can dispatch on), `id:` (lets a reconnecting client
send `Last-Event-ID`) and `retry:` (reconnect delay hint). A line starting with `:` is a comment; the
client ignores it, which makes `: keep-alive` a free heartbeat. The response stays open; under
HTTP/1.1 it is sent with `Transfer-Encoding: chunked`, under HTTP/2 as an open stream. A token stream
is just:

```
event: delta
data: {"text": "The"}

event: delta
data: {"text": " KV"}

event: done
data: {"usage": {"output_tokens": 2}}

```

### What flows through it
Providers map generation onto events differently but the shape is the same. OpenAI-style chat
completions send a sequence of `data:` JSON chunks, each carrying a `delta` (typically a first chunk
with only the role and empty content, then content fragments), and end with a literal
`data: [DONE]` sentinel; token usage only arrives in a final chunk if you ask for it
(`stream_options: {"include_usage": true}`). Anthropic's Messages API uses named events:
`message_start` (with input-token usage), `content_block_start`, repeated `content_block_delta`
(`text_delta` or, for tool use, `input_json_delta` carrying partial JSON), `content_block_stop`,
`message_delta` (stop reason, output-token usage) and `message_stop`, plus periodic `ping` events.
Two consequences follow. Tool-call arguments arrive as fragments of JSON that are only parseable at
the end of the block. And the authoritative token count is at the tail of the stream, so a client
that disconnects early never sees it.

### The path from GPU to screen
The engine (vLLM, SGLang, TGI, a provider's fleet) emits one token per decode step per sequence.
The serving layer serialises it as an event and *flushes* it; a framework that buffers generator
output defeats the whole point. Then it crosses every intermediary: your own reverse proxy, an API
gateway, a load balancer, maybe a CDN, then the client's HTTP library. Each one is allowed by HTTP
to buffer a response body, and several do by default (nginx `proxy_buffering`, response compression
layers that wait for a full block). The fixes are per hop: `X-Accel-Buffering: no` and
`Cache-Control: no-cache` headers, disabling compression for `text/event-stream`, and heartbeats so
idle-connection timers on load balancers (commonly around a minute) do not close a stream during a
long prefill or a reasoning pause.

### Semantics that change once you stream
The status code is sent before generation starts, so a `200` means "the stream opened", not "the
answer succeeded". Mid-stream failures (model error, content filter stop, upstream timeout) must
travel in-band as an `error` event or a `finish_reason`, and clients must treat the output they have
as partial. Streams are not resumable: the browser `EventSource` API will auto-reconnect and send
`Last-Event-ID`, but an LLM server cannot replay generation from an id, so a dropped stream is a
cancel-and-restart, not a resume. And `EventSource` only issues `GET` requests without custom
headers, so real clients consume the provider's `POST` stream with `fetch` and a `ReadableStream`
reader, which is also where per-origin connection limits on HTTP/1.1 (six in most browsers) bite if
every tab holds a stream open.

## Example
A minimal SSE endpoint that forwards an engine's tokens with correct framing, a connect comment that
forces intermediaries to commit to the response, heartbeats on idle, in-band errors and a stop on
client disconnect. The token source is a stand-in for an engine or provider stream.

```python
import asyncio, json
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()

def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"      # blank line terminates the event

async def token_source(prompt: str):                             # stand-in for an engine stream
    for i, tok in enumerate(("Stream", "ing", " works", ".")):
        await asyncio.sleep(0.3); yield i, tok

@app.post("/v1/answer")
async def answer(req: Request):
    body = await req.json()
    q: asyncio.Queue = asyncio.Queue()

    async def pump():                                            # engine -> queue, errors in-band
        n = 0
        try:
            async for i, tok in token_source(body["prompt"]):
                await q.put(("delta", {"i": i, "text": tok})); n += 1
            await q.put(("done", {"output_tokens": n}))
        except Exception as e:
            await q.put(("error", {"message": str(e)}))          # status is already 200

    async def gen():
        task = asyncio.create_task(pump())
        yield ": connected\n\n"                                  # comment: flushes proxies, no client event
        try:
            while True:
                try:
                    ev, data = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"; continue           # heartbeat beats idle timeouts
                if await req.is_disconnected(): break            # client left: stop forwarding
                yield sse(ev, data)
                if ev in ("done", "error"): break
        finally:
            task.cancel()                                        # propagate the stop upstream

    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

## Trade-offs
- Perceived latency drops to TTFT, but total completion time does not improve and may rise slightly
  from per-token serialisation and small TCP writes; the gain disappears when the consumer is a
  machine that needs the whole JSON anyway, although early cancellation and idle-timeout liveness
  still argue for streaming there.
- SSE is simple and proxy-friendly because it is one-directional; the moment the client must talk
  mid-stream (interrupt, barge-in voice, collaborative edits) you need WebSockets or a second
  request channel.
- Coalescing several tokens into one event every few tens of milliseconds cuts frame and render
  overhead on busy servers, at the cost of visibly choppier output at low concurrency.
- Structured output cannot be validated until the final event, so a streaming JSON response either
  needs an incremental parser or loses the latency benefit for machine consumers.
- Streaming exposes TTFT and inter-token gaps to measurement, which is valuable, but pushes usage and
  cost data to the end of the stream where disconnects can lose it.

## Pitfalls
- A proxy or middleware buffers the body; the symptom is a stream that works on localhost and arrives
  all at once in production after the full generation time.
- Using the browser `EventSource` against an authenticated `POST` endpoint; the symptom is 401 or 405
  errors because `EventSource` can only `GET` without custom headers.
- No heartbeats during long prefill or thinking pauses; the symptom is streams dying at a fixed
  interval (often exactly 60 s) with no error from either end.
- Treating the `200` as success and ignoring in-band `error` events or stop reasons; the symptom is
  truncated answers logged as successes and never retried.
- Rendering each delta directly into Markdown; the symptom is flickering, half-open code fences and
  broken links until the closing token arrives.
- Counting SSE chunks as tokens; servers may coalesce or split, so the symptom is usage numbers that
  disagree with the provider's bill.
- Forgetting that a disconnected client does not stop generation unless you propagate the stop; the
  symptom is GPU time and provider spend that stay flat when traffic drops.

## When to use
Stream by default for anything a human reads that is longer than a sentence, and for long-running
machine calls where early cancellation and liveness matter (agent steps, tool-argument generation).
Do not bother for short classification, extraction or embedding calls, or for batch pipelines that
validate the complete output anyway. Measure TTFT, the distribution of inter-event gaps, stream
completion rate and disconnect rate, and alert on "all events arrived within the last 100 ms of the
response", which is the buffering signature. In interviews, be able to write the SSE framing from
memory, explain why not WebSockets, name the buffering layers, and describe how error semantics
change once the status code has already been sent.

## Role lens
- **Engineer:** Implements the generator and client parser, sets the anti-buffering headers and
  heartbeats, and handles partial JSON for streamed tool calls.
- **Architect:** Decides where streaming terminates (gateway vs service), which hops are allowed to
  buffer, and whether interactive and batch traffic share the same path.
- **FDE:** Debugs "it streams in dev but not behind the customer's proxy", usually a buffering or
  idle-timeout setting on infrastructure the customer owns.

## Check yourself
**Q:** A customer reports that your streaming endpoint "works on localhost but does not stream in
production, the whole answer just appears after ten seconds". How do you diagnose it, in what order?
**A:** First confirm the server really flushes per token by hitting the service directly with
`curl -N` from inside the environment; if tokens trickle there, the server is fine. Then walk the
hops outward: the reverse proxy (nginx buffers upstream responses by default; set
`X-Accel-Buffering: no` or disable `proxy_buffering`), any compression middleware or gateway that
waits for a full block before emitting, a CDN or WAF that caches or inspects bodies, and the load
balancer's idle timeout. Finally check the client: awaiting the whole response body instead of
reading the stream reproduces the exact symptom. Verify by sending a `: connected` comment first; if
even that arrives late, the problem is an intermediary, not the model.

## Quiz
### Q: Why is SSE preferred over WebSockets for streaming LLM tokens to a browser?
- [ ] SSE delivers each message with lower latency than a WebSocket frame
- [x] Token output is one-directional, and SSE gives framing over ordinary HTTP that proxies, gateways and provider APIs already handle
- [ ] WebSockets cannot carry JSON payloads reliably
- [ ] SSE supports binary token ids while WebSockets are text only
> SSE is not faster per message; it wins because it is plain HTTP for a one-way channel, so it needs
> no protocol upgrade or connection state and survives intermediaries. WebSockets are the right tool
> only when the client must send data mid-stream.

### Q: A streamed response returned HTTP 200, emitted 300 tokens, then the model errored. What must the client do?
- [ ] Nothing special; the 200 status already indicates the request succeeded
- [ ] Automatically retry and append the new output to the 300 tokens it has
- [x] Handle the in-band error event or finish reason and treat the 300 tokens as a partial, failed result
- [ ] Wait for the `[DONE]` sentinel, which is always sent after errors
> Once streaming starts the status code is fixed, so failures can only be signalled inside the stream.
> Appending a retry's output would duplicate content; the partial answer must be marked incomplete and
> the retry started from scratch.

### Q: Every token of a stream arrives at once, exactly when generation finishes. What is the most likely cause?
- [ ] Time-to-first-token is unusually high because of a long prompt
- [x] An intermediary or middleware is buffering the response body until it completes
- [ ] The model does not support streaming so the server falls back to a single response
- [ ] The client negotiated HTTP/2, which does not allow chunked transfer
> Buffering is the classic signature: generation ran at normal speed but a proxy, compression layer or
> the client's own body reader held the bytes. High TTFT would delay the first token, not bunch all of
> them, and HTTP/2 streams SSE fine without chunked encoding.
