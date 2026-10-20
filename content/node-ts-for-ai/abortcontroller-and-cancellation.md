---
id: abortcontroller-and-cancellation
module: node-ts-for-ai
name: AbortController & cancellation
kind: concept
difficulty: core
est_minutes: 14
related:
  - node-ts-for-ai/promises-and-async-error-propagation
  - node-ts-for-ai/streams-and-backpressure
  - serving-inference/cancellation
  - serving-inference/timeouts
  - fastapi-for-ai/cancellation-on-client-disconnect
  - python-for-ai/async-cancellation-and-exception-propagation
sources:
  - Official docs: Node.js — AbortController, AbortSignal, events.once, timers/promises
  - Official docs: WHATWG DOM Standard — AbortController and AbortSignal
---

## TL;DR
Promises cannot be cancelled, so JavaScript cancels cooperatively: an `AbortController` owns an
`AbortSignal`, the signal is passed down to every operation that should stop together, and calling
`abort()` makes `fetch`, timers, streams and your own code reject with an `AbortError`. In AI services
this is what stops a model from generating billed tokens after the user closed the tab, and what
turns a 30-second timeout into a real stop rather than a promise you merely stopped waiting for.

## Why it exists
A model call is the most expensive thing a typical Node service does: seconds of latency, tokens
billed as they are produced, and a connection held open for the duration. Users abandon requests
constantly (closing a tab, hitting stop, re-sending a prompt), agent loops decide half-way that a
tool result is no longer needed, and timeouts must give up on a slow provider and fall back to another.
In every case the question is the same: how do you make work *stop*, not just stop waiting for it?

Promises offer no answer. A promise is a handle to a result, not to the work; `Promise.race` against a
timer settles your await but the underlying HTTP request keeps streaming, the provider keeps
generating, and the tokens keep costing money. Node's older approach was ad hoc: every API had its
own `req.destroy()`, `stream.destroy()`, `clearTimeout()`, and composing them across a call tree meant
threading callbacks by hand. `AbortController` and `AbortSignal`, borrowed from the browser, give one
object that every cancellable API understands. The design is cooperative *because* JavaScript cannot
preempt a running function: the runtime cannot safely unwind a function that holds a database
transaction or a half-written file. Instead, a signal is a broadcast that each layer checks or
subscribes to, so cancellation flows down the same path as the request, and cleanup happens where the
resources were acquired.

## How it works
### The two objects
`const ac = new AbortController()` creates a controller with one `ac.signal`. The signal has
`aborted` (boolean), `reason` (whatever `abort(reason)` was given, defaulting to a `DOMException`
named `AbortError`), `throwIfAborted()` (throws the reason if already aborted), and an `'abort'` event.
The split is deliberate: the controller stays with the owner who may cancel, while the signal, which
can only be observed, is handed to everyone else. A callee cannot abort its caller's work by accident.

### How built-in APIs consume it
- `fetch(url, { signal })` rejects with the reason and destroys the socket; with a streaming body,
  reading from `response.body` also rejects. The TCP close tells the provider to stop generating.
- `setTimeout` from `node:timers/promises` takes `{ signal }` and rejects early when aborted.
- `events.once(emitter, 'name', { signal })` stops waiting.
- Streams: `stream.addAbortSignal(signal, stream)` and `pipeline(..., { signal })` destroy the chain.
- Child processes, `fs` promises, `readline` and most newer APIs accept a signal.
- Provider SDKs (OpenAI, Anthropic, the Vercel AI SDK) pass a `signal` option through to the request.

### Composing signals
`AbortSignal.timeout(ms)` returns a signal that aborts with a `TimeoutError` after the delay.
`AbortSignal.any([a, b])` (available in current Node releases) returns a signal that aborts when any
input aborts, carrying that input's reason. Together they express the common case in one line: "stop
when the client disconnects *or* after 30 s *or* when the parent agent step is cancelled". Before
`any` existed the same was done by hand with listeners that forward one abort to another controller.

### Making your own code cancellable
Three idioms cover nearly everything:

1. **Check at loop boundaries**: `signal.throwIfAborted()` at the top of each iteration of an agent
   loop or chunk-processing loop, so a long computation stops at the next safe point.
2. **Subscribe for cleanup**: `signal.addEventListener('abort', () => conn.release(), { once: true })`
   when you hold a resource with no signal-aware API. Remove the listener when the work finishes
   normally, otherwise a long-lived signal accumulates listeners for every call made with it and leaks.
3. **Pass it down**: every async function that does I/O takes `signal` as a parameter and forwards it.
   The call tree becomes a cancellation tree, with one controller at the root per request.

### Distinguishing abort from failure
An abort surfaces as a rejection whose `name` is `'AbortError'` (or `'TimeoutError'` for the timeout
signal), and `signal.reason` tells you *why*. This matters for the retry layer: a provider 503 should
be retried, a client disconnect must not be, and a timeout may be retried against a different provider.
Code that treats every rejection as a failure will retry cancelled calls and bill tokens for a user
who already left. Check `signal.aborted` (or `error.name`) before classifying.

### Where the request-level controller comes from
In an HTTP server, the root signal comes from the connection: `res.on('close')` (or the framework's
request abort event; in `fetch`-style handlers it is `request.signal`) fires when the client goes away.
Create one controller per request, abort it on close, combine with a timeout, and pass the resulting
signal into the model call, the tool calls and any streams. Cancelling the parent cancels everything
underneath because they all share one signal.

## Example
A request-scoped signal combining client disconnect, a hard timeout, and an agent-step cancellation,
passed through a streaming model call, with the abort reason used to decide whether the error is
retryable. The listener cleanup in `withResource` shows the pattern for resources that have no
signal-aware API.

```ts
import { setTimeout as sleep } from "node:timers/promises";
import type { IncomingMessage, ServerResponse } from "node:http";

function requestSignal(req: IncomingMessage, res: ServerResponse, timeoutMs: number) {
  const client = new AbortController();
  res.once("close", () => client.abort(new Error("client disconnected")));
  return AbortSignal.any([client.signal, AbortSignal.timeout(timeoutMs)]);
}

async function streamModel(prompt: string, signal: AbortSignal, onToken: (t: string) => void) {
  const res = await fetch("https://provider.example/v1/stream", {
    method: "POST", body: JSON.stringify({ prompt, stream: true }), signal,
  });
  for await (const chunk of res.body!) {          // rejects with AbortError when the signal fires
    signal.throwIfAborted();                       // also stop between chunks if parsing is slow
    onToken(Buffer.from(chunk).toString("utf8"));
  }
}

async function withResource<T>(signal: AbortSignal, fn: () => Promise<T>): Promise<T> {
  const conn = { release() { console.log("released"); } };
  const onAbort = () => conn.release();
  signal.addEventListener("abort", onAbort, { once: true });
  try { return await fn(); }
  finally { signal.removeEventListener("abort", onAbort); conn.release(); }
}

export async function handler(req: IncomingMessage, res: ServerResponse) {
  const signal = requestSignal(req, res, 30_000);
  try {
    await withResource(signal, () => streamModel("Explain backpressure", signal, (t) => res.write(t)));
    res.end();
  } catch (e: unknown) {
    const name = (e as { name?: string }).name;
    if (name === "AbortError") return;                       // user left: no retry, no error log
    if (name === "TimeoutError") { res.statusCode = 504; return res.end("provider timeout"); }
    throw e;                                                 // genuine failure: let the error layer classify it
  }
}
void sleep;                                                  // sleep(ms, undefined, { signal }) for cancellable waits
```

## Trade-offs
- Cooperative cancellation is safe (cleanup runs where resources live) but only as good as the weakest
  link: one function that ignores the signal keeps the work alive, so coverage is a discipline, not a
  feature.
- Aborting a model stream stops billing from that moment, but tokens already generated are paid for,
  and some providers bill a minimum; cancelling very early buys the most and very late buys almost
  nothing.
- Aborting mid-tool-call can leave external state half-done (an email sent, a row inserted); for
  side-effecting tools prefer to cancel *before* the call or make the tool idempotent rather than
  cutting it off.
- A single request-level signal is simple, but an agent sometimes wants to cancel one branch and keep
  the rest; child controllers whose signals combine the parent via `AbortSignal.any` give that
  granularity at the cost of more plumbing.
- Timeouts as signals stop the work, unlike `Promise.race`, but a too-short timeout on a slow-but-fine
  provider turns success into waste; set them from measured p99 latency, not round numbers.

## Pitfalls
- Using `Promise.race` with a timer as a "timeout"; the symptom is the request returning 504 while the
  provider keeps streaming and the token bill keeps growing.
- Not wiring `res.on('close')` to a controller; the symptom is upstream connections and token spend
  continuing for minutes after users closed their tabs, visible as open sockets outliving requests.
- Adding `'abort'` listeners to a long-lived signal without removing them; the symptom is
  `MaxListenersExceededWarning` and memory growth proportional to calls made under that signal.
- Retrying on every rejection, including `AbortError`; the symptom is duplicate model calls after
  cancellation and bills that do not match request counts.
- Calling `abort()` and then continuing to use the response stream; the symptom is confusing
  `ERR_STREAM_PREMATURE_CLOSE` errors reported as failures rather than expected cancellations.
- Forgetting that `AbortSignal.timeout` starts counting at creation; the symptom is a timeout that
  fires early because the signal was created long before the request it guards.

## When to use
Create one controller per inbound request and pass its signal into every model call, tool call,
stream and timer that belongs to that request; combine it with a timeout and, in agent loops, with a
per-step controller so a planner can abandon a branch. Check `throwIfAborted` at the top of loops that
do real work. Treat `AbortError` as a normal outcome in logs and metrics, separate from failures, and
never retry it. Skip the machinery only for fire-and-forget work that must complete regardless, such
as audit logging. Measure the count of aborts by reason (client, timeout, parent), the token spend on
aborted requests, and sockets open after request end. Interview questions here usually probe why
promises cannot be cancelled and how a signal propagates through a call tree.

## Role lens
- **Engineer:** Threads a `signal` parameter through every I/O function, wires client disconnect and
  timeouts into it, and classifies `AbortError` separately from provider failures in the retry layer.
- **Architect:** Sets the timeout budget per hop and the policy for cancelling side-effecting tools,
  and makes "accepts an AbortSignal" a requirement for internal client libraries.
- **FDE:** Finds the customer's gateway that keeps streaming after users hit stop, quantifies the wasted
  token spend, and adds disconnect-to-abort wiring as the first-day fix.

## Check yourself
**Q:** A team wraps every model call in `Promise.race([call(), timeoutAfter(20_000)])` and reports that
timeouts "work" because requests return in 20 seconds. Their token bill is higher than their request
volume predicts. Explain what is happening and how to fix it properly.
**A:** `Promise.race` settles the await when the timer fires, but it does nothing to the underlying HTTP
request: the socket stays open, the provider keeps generating, and every token is billed until the
response finishes on its own. The same applies to retries: the retry starts while the original call is
still running, so two calls are billed. The fix is to create an `AbortSignal.timeout(20_000)`, combine
it with the client-disconnect signal using `AbortSignal.any`, and pass the result to `fetch` or the SDK's
`signal` option, so the socket is destroyed when the deadline passes and generation stops. The error
surfaces as a `TimeoutError`, which the retry layer can handle deliberately. Measuring sockets open
after the deadline and token usage per request before and after will show the difference.

## Quiz
### Q: Why does JavaScript use cooperative cancellation through `AbortSignal` instead of letting a promise be cancelled directly?
- [ ] Promises are immutable, so adding a cancel method would break the specification
- [x] The runtime cannot safely preempt running code that holds resources, so each layer must observe the signal and clean up where the resource was acquired
- [ ] Cancellation is handled by the operating system, not the language
- [ ] Direct cancellation would require every promise to run on a separate thread
> A promise is a handle to a result, not to the work. Forcibly unwinding a function mid-transaction or
> mid-write would corrupt state, so the signal is a broadcast that cooperating code checks or subscribes
> to. The cost is that an ignoring layer keeps work alive.

### Q: What is the difference between `Promise.race` with a timer and `AbortSignal.timeout` passed to `fetch`?
- [ ] There is none; both stop the request after the delay
- [ ] `Promise.race` is faster because it avoids creating a signal
- [x] `Promise.race` only stops waiting while the request continues; the signal destroys the request so upstream generation stops
- [ ] `AbortSignal.timeout` resolves with partial data instead of rejecting
> The race settles your promise but leaves the socket open and the provider generating billed tokens.
> A signal passed to `fetch` aborts the request, which closes the connection and makes the promise reject
> with a `TimeoutError` that can be handled explicitly.

### Q: An `AbortError` reaches a retry wrapper that retries any rejection. What is the correct behaviour?
- [ ] Retry once, since the abort may have been spurious
- [ ] Retry against a different provider, because the first was cancelled
- [x] Do not retry; the abort means the caller no longer wants the result
- [ ] Convert it to a 500 error so the client can decide
> An abort is a decision by the owner of the request (client disconnect, parent cancellation) that the
> work is no longer wanted. Retrying it spends tokens for nobody. Only a timeout signal, identified by
> its `TimeoutError` reason, may justify a fallback attempt.
