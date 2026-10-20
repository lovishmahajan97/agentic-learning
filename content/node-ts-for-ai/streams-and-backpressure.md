---
id: streams-and-backpressure
module: node-ts-for-ai
name: Streams & backpressure
kind: concept
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - node-ts-for-ai/abortcontroller-and-cancellation
  - node-ts-for-ai/event-loop-and-microtask-queue
  - serving-inference/streaming
  - serving-inference/backpressure
  - fastapi-for-ai/streamingresponse
  - reliability/backpressure-and-load-shedding
  - agent-reliability/structured-event-streams
sources:
  - Official docs: Node.js — Stream (Backpressuring in Streams, stream.pipeline, Web Streams API)
  - Official docs: WHATWG Streams Standard
---

## TL;DR
A stream processes data in chunks as it arrives instead of buffering everything, which is how a
token-by-token model response reaches a browser with sub-second first-token latency. Backpressure is
the flow-control rule that makes this safe: when the consumer is slower than the producer, the
producer is told to pause, so memory stays bounded. Node has two stream APIs (classic `node:stream`
and WHATWG Web Streams, used by `fetch`), and `pipeline`/`pipeTo` are what wire them together with
error propagation and backpressure intact.

## Why it exists
An LLM answer of 800 tokens takes on the order of ten seconds to generate at typical decode rates of
tens of tokens per second. Waiting for the whole thing and then
sending it makes the user stare at a blank screen for the full duration; streaming each token as it is
produced turns the same latency into a visibly "typing" response with time-to-first-token measured in
hundreds of milliseconds. The same shape appears everywhere in AI systems: a 300 MB PDF corpus to chunk
and embed, a JSONL export of a million rows to score, audio frames to transcribe. Loading any of these
into memory first means memory proportional to input size, and a single large input takes the process
down.

Streaming alone is not enough, though, because the two ends rarely run at the same speed. A model API
pushes tokens faster than a phone on a poor connection can acknowledge them; a file reader can produce
64 KiB chunks far faster than an embedding call can consume them. Without a rule for the mismatch, the
fast side's output piles up in buffers in your process. For one request that is a few megabytes; for
a thousand concurrent streams to slow clients it is the whole heap, and the process dies of memory
pressure while every request looks healthy. The naive fix, "make the consumer faster", does not exist
for a network you do not control. Backpressure is the actual fix: the consumer's capacity propagates
backwards to the producer, which stops reading from its own source until there is room. Node's stream
abstractions exist to make that propagation automatic, and to make errors and early termination
propagate along the same path.

## How it works
### The classic `node:stream` types
- **Readable** — a source you pull from (`fs.createReadStream`, an HTTP request body, a socket).
- **Writable** — a sink you push into (an HTTP response, a file, a socket).
- **Duplex** — both, independently (a TCP socket).
- **Transform** — a Duplex where output is computed from input (gzip, a line splitter, an SSE parser).

Each has an internal buffer bounded by `highWaterMark` (bytes for byte streams, item count in
`objectMode`). It is a *threshold*, not a hard cap: the stream will accept a write beyond it, but
reports that it would rather you stopped.

### The backpressure protocol
`writable.write(chunk)` returns `false` when the internal buffer has reached `highWaterMark`. The
contract is that the producer stops writing and waits for the `'drain'` event, then resumes. On the
readable side, when nobody is consuming (the destination said stop), the readable stops calling its
underlying `_read`, so the OS socket buffer fills, and TCP's own flow control eventually tells the
remote server to slow down. That chain, from a slow browser through your process back to the model
provider's socket, is backpressure end to end. It only works if every link honours the signal, which is
why hand-written `on('data', chunk => res.write(chunk))` loops are the classic bug: they ignore the
`false` return and buffer without bound.

### `pipe` versus `pipeline`
`readable.pipe(writable)` implements the pause/drain dance for you, but it does not forward errors or
clean up: if the destination errors or closes, the source is merely unpiped and left paused, never
destroyed, so its file descriptor or upstream HTTP connection stays open; if the source errors, the
destination is not closed. Both leak. `stream.pipeline(src, ...transforms, dest, cb)` (or its promise form in `node:stream/promises`)
wires backpressure *and* error propagation *and* cleanup: an error or early close anywhere destroys
every stream in the chain and the callback receives the error. There is almost no reason to use `pipe`
in a server.

### Async iteration
Readables are async iterables: `for await (const chunk of readable)` pulls one chunk at a time and
naturally applies backpressure because the loop body must finish before the next chunk is requested.
`Readable.from(asyncGenerator)` goes the other way. For token streams this is often the clearest code:
an async generator that yields parsed events, consumed by a loop that writes to the response and
awaits `drain` when needed.

### Web Streams
`fetch` in Node returns `response.body` as a WHATWG `ReadableStream`, the same API as browsers and the
one many AI SDKs expose. It has the same ideas with different names: a queuing strategy with a
`highWaterMark`, `pipeThrough(transformStream)`, `pipeTo(writable)` with error and abort propagation,
and `reader.read()` for manual pulling. `Readable.fromWeb()` and `Readable.toWeb()` convert between the
two worlds, which you need when a provider SDK hands you a Web stream and your HTTP framework wants a
Node stream (or the reverse). Both kinds are async-iterable, so `for await` works on either.

### Framing on the wire: SSE and chunked bodies
A token stream has no natural chunk boundaries at the TCP level; a chunk may contain half a JSON
event. Providers therefore frame events with Server-Sent Events (`data: {...}\n\n`), and a Transform
on your side must buffer partial lines until a blank line arrives, and must decode bytes with a
`StringDecoder` (or `TextDecoderStream`) because a chunk boundary can fall inside a multi-byte UTF-8
character. A `Transform` enforces backpressure at chunk granularity for you: it does not call
`_transform` with the next chunk until its readable side has been drained below `highWaterMark`. One
chunk that expands into fifty events overshoots the threshold by that one chunk's output, which is
bounded. Backpressure truly breaks in a hand-written `Readable` or event-driven producer that keeps
calling `push()` after it has returned `false`; that buffer has no bound.

## Example
A Transform that reframes a provider's SSE stream into newline-delimited JSON for the browser, wired
with `pipeline` so a client disconnect or upstream error tears down the whole chain. On client
disconnect the `AbortSignal` cancels the upstream `fetch` and closes that connection; most providers
stop generating when the connection drops, but whether partial output is still billed is
vendor-specific.

```ts
import { Transform, Readable, type TransformCallback } from "node:stream";
import { pipeline } from "node:stream/promises";
import { StringDecoder } from "node:string_decoder";
import type { IncomingMessage, ServerResponse } from "node:http";

class SseToNdjson extends Transform {
  private buf = "";
  private dec = new StringDecoder("utf8");   // never splits a multi-byte character across chunks
  override _transform(chunk: Buffer, _enc: BufferEncoding, cb: TransformCallback) {
    this.buf += this.dec.write(chunk).replace(/\r\n/g, "\n");   // SSE also allows CRLF
    let idx: number;
    try {
      while ((idx = this.buf.indexOf("\n\n")) !== -1) {           // one complete SSE event
        const event = this.buf.slice(0, idx); this.buf = this.buf.slice(idx + 2);
        for (const line of event.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trim();
          if (data === "[DONE]") continue;                         // stream ends when upstream ends
          this.push(JSON.stringify({ t: JSON.parse(data).delta ?? "" }) + "\n");
        }
      }
    } catch (e) { return cb(e as Error); }  // report parse errors to pipeline instead of throwing
    cb();                                    // Transform waits for the reader before the next chunk
  }
}

export async function proxy(req: IncomingMessage, res: ServerResponse) {
  const ac = new AbortController();
  res.on("close", () => ac.abort());          // client went away -> stop upstream (no-op if finished)
  const upstream = await fetch("https://provider.example/v1/stream", {
    method: "POST", body: JSON.stringify({ prompt: "hi", stream: true }), signal: ac.signal,
  });
  if (!upstream.ok || !upstream.body) { res.statusCode = 502; return res.end(); }
  res.writeHead(200, { "content-type": "application/x-ndjson", "x-accel-buffering": "no" });
  try {
    await pipeline(Readable.fromWeb(upstream.body as any), new SseToNdjson(), res, { signal: ac.signal });
  } catch (e) {
    if (!ac.signal.aborted) console.error("stream failed", e);   // disconnect is normal
  }
}
```

## Trade-offs
- Streaming gives low time-to-first-token and bounded memory, but you lose the ability to validate or
  moderate the complete output before the user sees it; add a trailing check or stream through a
  guard stage when the use case demands it.
- Backpressure protects memory, but it also means a slow client slows the upstream read, holding a
  provider connection open longer; for very slow clients a bounded buffer plus disconnect may be better
  than perfect flow control.
- `objectMode` streams of parsed events are convenient and let the `highWaterMark` count events, but
  each object is a heap allocation; for bulk byte processing stay in `Buffer` mode.
- Web Streams are portable across runtimes (Node, browsers, edge) and match the AI SDK surfaces, while
  Node streams have richer tooling and wider framework support; converting between them is cheap but
  easy to get wrong around error propagation.
- A larger `highWaterMark` smooths bursty producers and reduces pause/resume churn, at the cost of more
  memory per stream, which multiplies by concurrency.

## Pitfalls
- Ignoring the `false` return of `write()` in a manual `'data'` handler; the symptom is heap growth
  proportional to the number of slow clients and eventual out-of-memory under load.
- Using `pipe` instead of `pipeline`, so an error on the response does not destroy the upstream body;
  the symptom is leaked file descriptors and upstream connections held open after users disconnect.
- Writing a custom `Readable` (for example, one fed by an SDK's event callbacks) that keeps calling
  `push()` after it returns `false`; the symptom is backpressure that "works in tests" but buffers
  unboundedly when real clients are slow.
- Decoding each chunk with `chunk.toString()` in a streaming parser; the symptom is occasional `\uFFFD`
  replacement characters in non-English or emoji output where a character straddled two chunks.
- Buffering a whole stream (`await res.text()`) to parse it, then streaming it to the client; the
  symptom is time-to-first-token equal to total generation time.
- Reverse proxies or compression middleware buffering the response; the symptom is tokens arriving in
  one burst at the end even though the server wrote them incrementally (hence nginx's `X-Accel-Buffering: no`
  header, or the equivalent setting in your proxy, and disabling compression for streamed routes).
- Not listening for client disconnect and aborting upstream; the symptom is token spend on responses
  nobody receives.

## When to use
Stream whenever the output is produced incrementally and a human is waiting (chat completions, voice,
progress of long agent runs), and whenever input size is unbounded (files, exports, logs). Use
`pipeline` or `pipeTo` rather than manual event wiring, keep one `AbortController` per request tied
to client disconnect, and build Transforms that respect chunk boundaries. Do not stream when the
consumer needs the complete result to act (a JSON tool call must be parsed whole) or when the
response is small and fast. Measure time-to-first-token, per-stream heap, and the count of upstream
streams still open after client disconnect. In interviews, explain what `write()` returning `false`
means, why `pipeline` beats `pipe`, and how a slow browser ends up throttling a model provider.

## Role lens
- **Engineer:** Builds the SSE-to-client proxy with `pipeline`, respects `drain`, converts between Web
  and Node streams at SDK boundaries, and aborts upstream on disconnect.
- **Architect:** Decides where streaming is exposed in the API, sets buffer and concurrency budgets per
  process, and ensures proxies and gateways in the path do not buffer streamed routes.
- **FDE:** Fixes "the answer appears all at once" at a customer by finding the buffering load balancer
  or compression layer, and explains why their memory alerts correlate with slow mobile clients.

## Check yourself
**Q:** A chat gateway's memory climbs steadily during peak hours and the process is eventually killed.
Model latency is normal and request counts are flat. Walk through how streams could cause this and how
you would prove and fix it.
**A:** The likely cause is missing backpressure between the provider stream and slow clients: the
handler reads tokens as fast as the provider sends them and writes them to each response, ignoring
`write()` returning `false`, so bytes for slow connections accumulate in the response's internal buffer.
Memory then scales with the number of slow clients, not with request count. Prove it with a heap
snapshot showing large `Writable` buffers, or by correlating memory with the count of long-lived
responses. Fix by replacing manual wiring with `pipeline` (or `pipeTo`), which pauses the upstream read
when the response buffer is full, and by aborting the upstream request on client `'close'` so abandoned
streams stop consuming memory and tokens. Add a per-stream buffered-bytes metric so a regression is
visible.

## Quiz
### Q: What does `writable.write(chunk)` returning `false` mean?
- [ ] The chunk was rejected and must be written again
- [ ] The stream has ended and further writes will throw
- [x] The chunk was buffered but the buffer is at or above `highWaterMark`, so the producer should pause until `'drain'`
- [ ] The underlying socket is closed and the data was lost
> The write still succeeds; `false` is a request to stop producing. Ignoring it does not lose data, it
> grows the buffer without bound. Waiting for `'drain'` before writing again is the backpressure
> contract that `pipeline` implements for you.

### Q: Why is `stream.pipeline` preferred over `readable.pipe(writable)` in a server?
- [ ] `pipeline` is faster because it skips the internal buffer
- [x] `pipeline` propagates errors and early closes to every stream in the chain and destroys them, while `pipe` leaves the source open
- [ ] `pipe` does not implement backpressure
- [ ] `pipeline` works with Web Streams and `pipe` does not
> `pipe` handles pause and drain correctly but does not forward errors or destroy the source when the
> destination fails, which leaves the source paused but open and leaks upstream connections. `pipeline` ties
> the lifetimes together and reports a single error.

### Q: A provider SDK returns a WHATWG `ReadableStream`, and the HTTP framework expects a Node `Readable`. What is the right bridge?
- [ ] Read the whole stream with `await response.text()` and write the string
- [x] `Readable.fromWeb(stream)` and then `pipeline` into the response
- [ ] Wrap each `reader.read()` result in `process.nextTick`
- [ ] Use `JSON.parse` on the stream object directly
> `Readable.fromWeb` adapts the Web stream to a Node `Readable` while keeping backpressure and
> cancellation semantics, so `pipeline` can manage the chain. Buffering with `text()` destroys the
> streaming benefit, and the other options do not produce a stream at all.
