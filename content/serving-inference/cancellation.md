---
id: cancellation
module: serving-inference
name: Cancellation
kind: pattern
difficulty: core
est_minutes: 14
status: reviewed
related:
  - serving-inference/timeouts
  - serving-inference/streaming
  - serving-inference/backpressure
  - serving-inference/continuous-batching
  - fastapi-for-ai/cancellation-on-client-disconnect
  - python-for-ai/async-cancellation-and-exception-propagation
  - node-ts-for-ai/abortcontroller-and-cancellation
  - agents/agent-loop
sources:
  - Dean and Barroso, 2013 — The Tail at Scale
  - Official docs: Python asyncio task cancellation
  - Official docs: MDN AbortController
---

## TL;DR
A generation nobody is waiting for still occupies a decode slot, KV-cache memory and your token
budget until it reaches `max_tokens`. Cancellation carries "stop" from the trigger (closed tab, stop
button, superseding message, timeout, losing hedge) through every hop: browser, your API, HTTP
client, provider or engine, so the engine aborts the sequence and frees its memory. It works only if
each layer notices the disconnect and closes the next connection; streaming makes that observable,
non-streaming hides it.

## Why it exists
LLM requests are expensive, long and often abandoned. Users close tabs, hit stop, or send a new
message before the last answer finishes; agents fan out speculative branches and keep one; timeouts
give up on calls that are still running; hedged requests race two providers and only need the
winner. In every case the work continues unless something tells it to stop, because a GPU engine
has no idea whether anyone is reading.

The cost is threefold. Money: a provider bills every token decoded until the abort arrives, and a
self-hosted replica burns GPU time on output that is discarded. Capacity: an abandoned sequence
holds its KV blocks and its place in the batch, so live requests queue behind ghosts, TTFT rises and
preemptions start; under a traffic spike with many disconnects this is how a healthy cluster looks
saturated. Correctness: in a chat UI the old answer keeps streaming into the thread after the user
has moved on, and in an agent a cancelled step may already have fired a tool with side effects.

The naive alternative, "let it run, it is only a few hundred tokens", fails on arithmetic. If a
tenth of requests are abandoned halfway through 1,000-token answers, the replica spends about 5 %
of its decode capacity on nothing, and the share climbs with longer outputs and more impatient users.
Timeouts without cancellation are worse: they guarantee the client gives up while guaranteeing the
upstream does not.

## How it works
### The chain, hop by hop
1. **Trigger.** In the browser an `AbortController` aborts the `fetch`; a stop button, a new message
   or navigation calls `controller.abort()`. Server-side triggers are timeouts, superseding requests
   and hedge resolution.
2. **Transport.** The client closes the connection (or resets the HTTP/2 stream).
3. **Your server.** The framework learns of the disconnect only when it next touches the socket. A
   streaming handler finds out at its next write, or by polling a disconnect check; a non-streaming
   handler that is simply awaiting the model never touches the socket and never finds out.
4. **Task cancellation.** The handler's task is cancelled (streaming frameworks usually do this on
   disconnect) or your code cancels it. In Python, `task.cancel()` schedules a `CancelledError` at the
   task's next `await`; it is cooperative, not a kill, so `finally` blocks run and must not swallow
   the exception.
5. **Upstream close.** Cleanup closes the response you were reading from the provider or engine. That
   close is the only signal the next hop gets; forgetting it leaves the upstream running while your
   own request looks finished.
6. **Engine abort.** The serving layer in front of the engine (an OpenAI-compatible server, a
   provider's gateway) detects its client's disconnect and aborts the request. A continuous-batching
   engine removes the sequence from the running batch at the next step and releases its KV blocks for
   other sequences. Without the abort, the sequence runs to end-of-sequence or `max_tokens`.

Each arrow is a place the signal can be lost: a proxy configured to keep the upstream alive after the
client leaves, a worker that cannot observe the socket, a `finally` that logs and returns, an SDK
that buffers the response instead of streaming it.

### Making disconnects observable
Streaming is the practical fix because a write per token means the server notices a dead client
within one token. For non-streaming work, run generation as a separate task and race it against a
disconnect watcher, or poll the request's disconnect state while waiting. Frameworks expose this
differently (FastAPI's `request.is_disconnected()`, a disconnect message on the ASGI receive
channel), and the companion lesson in the FastAPI module covers the mechanics.

### Side effects and accounting
Cancellation interrupts at an `await`, so anything between awaits either happened or did not. For
pure generation that is harmless. For an agent mid tool-call it is not: the tool may have sent the
email, written the row or charged the card before the cancel landed. Make tools idempotent or
two-phase, record the run as "cancelled" rather than "failed", and shield the small pieces of work
that must complete (writing the usage record, releasing a lock) so a cancel cannot tear them. For
billing, remember that a cancelled stream never delivers its final usage event, while a provider
generally charges for tokens generated up to the moment its gateway sees the abort, which can be
more than you received (tokens in flight, buffered at a proxy, or decoded during the
disconnect-detection lag). Keep the text you did receive and tokenize it for an estimate, treat that
as a lower bound, and reconcile against the provider's usage reports rather than streamed chunk
counts.

### Patterns built on cancellation
- **Supersede.** The newest user message cancels the in-flight generation before starting the next,
  so two answers never interleave and the slot is freed immediately.
- **Stop button.** Same mechanism, user-triggered; keep the partial text, mark it truncated.
- **Deadline cancel.** A timeout is only useful if it cancels; the two lessons are halves of one
  behaviour.
- **Hedging.** Issue the same request to two replicas or providers after a short delay and cancel the
  loser when the first token or the full response arrives; it cuts tail latency at the price of
  duplicate tokens, so it is reserved for latency-critical, cheap-to-duplicate calls.
- **Speculative prefetch.** Start a likely-next generation early and cancel it if the user goes
  elsewhere.

## Example
The supersede pattern for a chat session: the newest request cancels the previous generation, and
the cancelled task's `finally` closes the provider stream, which is the only way the provider learns
to stop decoding. The `asyncio.wait` keeps the caller from swallowing its own cancellation.

```python
import asyncio
from openai import AsyncOpenAI

client = AsyncOpenAI()
MODEL = "my-model"

class ChatSession:
    def __init__(self):
        self.current: asyncio.Task | None = None
        self.received: list[str] = []            # a cancelled stream sends no usage event; tokenize
                                                 # this later for a lower-bound estimate of billed output

    async def _generate(self, messages, on_delta):
        stream = await client.chat.completions.create(model=MODEL, messages=messages, stream=True)
        try:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    self.received.append(chunk.choices[0].delta.content)
                    on_delta(chunk.choices[0].delta.content)
        finally:
            await stream.close()                 # runs on CancelledError too: closing the HTTP response
                                                 # is the provider's only signal to abort the sequence

    async def submit(self, messages, on_delta) -> asyncio.Task:
        prev = self.current
        if prev and not prev.done():
            prev.cancel()                        # supersede: new message or stop button
            await asyncio.wait([prev])           # wait for its cleanup without swallowing our own cancel
        self.current = asyncio.create_task(self._generate(messages, on_delta))
        return self.current

async def demo():
    s = ChatSession()
    show = lambda t: print(t, end="", flush=True)
    first = await s.submit([{"role": "user", "content": "Write a long essay about rivers."}], show)
    await asyncio.sleep(1.0)                     # user changes their mind one second in
    second = await s.submit([{"role": "user", "content": "Actually, one sentence on rivers."}], show)
    await second
    print("\nfirst cancelled:", first.cancelled(), "| chars received:", sum(map(len, s.received)))

asyncio.run(demo())
```

## Trade-offs
- Cancelling on disconnect frees capacity and money immediately but throws away partial work that
  might have been valuable; for long, expensive jobs the better design is an asynchronous job whose
  lifetime is independent of the HTTP connection.
- Hedged requests cut tail latency and double the tokens spent on every hedged call; they pay off
  only for short, latency-critical calls where the tail is bad and capacity is available.
- Immediate cancellation is cheapest, while a short grace period tolerates mobile network blips and
  tab switches; the right delay depends on how reconnecting clients resume, which for LLM streams
  is usually "they cannot", so grace periods rarely pay.
- Cooperative cancellation is safe for generation and risky around side effects; tools that are
  idempotent can be cancelled freely, non-idempotent ones need shielding or compensation.
- Polling for disconnects in non-streaming handlers adds a little overhead per request and is the
  only way those handlers ever stop early.

## Pitfalls
- No disconnect handling at all; the symptom is GPU utilisation and provider spend that stay flat
  when users leave, and TTFT that rises during periods of many abandoned requests.
- Catching `CancelledError` and continuing (on Python before 3.8 a plain `except Exception` did this
  silently, since it was not yet a `BaseException`); the symptom is tasks that never finish and
  `finally` cleanup that never runs.
- Closing your own response but not the upstream stream; the symptom is provider usage higher than
  your logs can account for.
- A proxy that keeps the upstream connection alive after the client aborts; the symptom is the
  serving layer never seeing a disconnect even though clients clearly left.
- Non-streaming endpoints assumed to cancel on disconnect; the symptom is a 2-minute generation
  completing for a client that left after 5 seconds.
- Cancelling an agent mid-tool-call with no idempotency; the symptom is duplicated emails or orders
  when the user retries after hitting stop.
- Reporting cancelled requests as errors; the symptom is a noisy error rate that masks real failures
  and panics on-call.

## When to use
Every service that fronts a model needs cancellation on disconnect, timeout and supersede; it is not
optional once outputs are longer than a sentence or traffic is bursty. Reach for hedging only for
short, latency-critical calls with capacity to spare, and for speculative prefetch only when the
abandoned cost is clearly less than the latency saved. Do not tie long batch jobs to a connection at
all. Measure the abandonment rate, time from client disconnect to engine abort, and tokens generated
after disconnect, and check that the provider's reported usage falls when abandonment rises; if
it does not, the abort is not reaching them. In interviews,
tracing the stop signal hop by hop from `AbortController` to KV-block release is the answer that
shows you have run this in production.

## Role lens
- **Engineer:** Wires `AbortController` on the client, closes upstream streams in `finally`, and
  makes handlers observe disconnects for both streaming and non-streaming paths.
- **Architect:** Decides which calls may be hedged or prefetched, which jobs are detached from
  connections, and requires idempotent tools so cancelled agent steps cannot cause duplicates.
- **FDE:** Finds the customer proxy or gateway that swallows client aborts, and proves with engine
  metrics that abandoned requests were burning their GPU capacity.

## Check yourself
**Q:** Users hit the stop button constantly, yet GPU utilisation and the provider bill do not move at
all. Trace where the cancellation is being lost, and how you would prove each hypothesis.
**A:** Start at the browser: confirm `controller.abort()` is called and the fetch actually closes
(network tab shows the request cancelled). Next the server: does the handler observe the disconnect?
A non-streaming handler awaiting the model never will; a streaming one should see a failed write or
`is_disconnected()` within a token, so log it. Then task cancellation: check that the task is
cancelled and that `finally` runs without swallowing `CancelledError`. Then the upstream: verify the
provider or engine response is closed, not merely abandoned to the garbage collector, by watching the
connection count or the provider's streamed-token usage. Then infrastructure: a proxy configured to
ignore client aborts keeps the upstream alive, visible as the serving layer never logging a
disconnect. Finally the engine: its abort count and freed-block metrics should rise with stop-button
presses; if everything upstream is right and they do not, the serving layer is not calling abort.

## Quiz
### Q: Why does a non-streaming endpoint usually keep generating after the client disconnects?
- [ ] The engine refuses to abort requests that have already started prefill
- [x] The handler is awaiting the model and never touches the socket, so nothing observes the disconnect until it tries to write the response
- [ ] HTTP does not allow a client to close a connection before the response arrives
- [ ] Non-streaming responses are cached by the proxy and must be completed
> A disconnect is only visible to code that reads from or writes to the connection. A streaming
> handler writes every token and notices quickly; a non-streaming one discovers the dead client only
> when the finished answer fails to send, long after the work was wasted.

### Q: In Python asyncio, what does calling `task.cancel()` on a running generation task actually do?
- [ ] Kills the task immediately and skips any cleanup code
- [ ] Sends a SIGTERM to the thread running the coroutine
- [x] Schedules a `CancelledError` to be raised at the task's next `await`, so `finally` blocks run and can close upstream streams
- [ ] Marks the task as done and returns its partial result
> Cancellation is cooperative: the exception is delivered at the next suspension point, cleanup in
> `finally` runs, and code must re-raise rather than swallow it. That is exactly what lets the task
> close the provider stream on its way out.

### Q: What happens to an aborted sequence in a continuous-batching engine such as vLLM?
- [ ] Its KV cache is kept until `max_tokens` would have been reached, in case the client reconnects
- [ ] It finishes decoding in the background and the output is discarded
- [x] It is removed from the running batch and its KV blocks are released for other sequences
- [ ] It is moved to a low-priority queue and completed when the GPU is idle
> The point of propagating cancellation is to reclaim capacity now. The engine drops the sequence at
> the next scheduling step and frees its memory, which is why abandoned requests only cost capacity
> when the abort never reaches the engine.
