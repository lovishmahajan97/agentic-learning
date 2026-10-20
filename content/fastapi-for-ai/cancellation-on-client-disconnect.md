---
id: cancellation-on-client-disconnect
module: fastapi-for-ai
name: Cancellation on client disconnect
kind: pattern
difficulty: advanced
est_minutes: 15
related:
  - fastapi-for-ai/asgi-lifecycle
  - fastapi-for-ai/streamingresponse
  - fastapi-for-ai/async-endpoints
  - fastapi-for-ai/concurrency-limits
  - python-for-ai/async-cancellation-and-exception-propagation
  - serving-inference/cancellation
  - fastapi-for-ai/idempotency-keys
  - fastapi-for-ai/job-queues-and-durable-workers
sources:
  - Official docs: ASGI specification (HTTP disconnect event)
  - Official docs: Starlette (Requests, StreamingResponse)
  - Official docs: Python asyncio (Task cancellation, shield)
---

## TL;DR
Users close tabs, press stop and retry, but a server that does not notice keeps generating tokens,
holding a concurrency slot and GPU memory, and possibly running agent tool calls for nobody. ASGI
reports a vanished client as an `http.disconnect` message, not an exception, so your code must detect
it and cancel the in-flight work, let cancellation propagate to the upstream call, run cleanup in
`finally`, and protect the few side effects that must finish.

## Why it exists
LLM requests are long and expensive compared with ordinary web requests. A chat answer can stream for
thirty seconds; an agent run can take minutes and make dozens of model and tool calls. During that
time users abandon requests constantly: they navigate away, hit "stop generating", lose mobile
signal, or click retry, which disconnects the first request and starts a second identical one.

If the server ignores the disconnect, each abandoned request keeps consuming everything it was
consuming: output tokens billed by the provider, a slot under your concurrency limit, a sequence in a
self-hosted engine's batch with its KV cache, a database connection. A retry storm therefore doubles
load at exactly the wrong moment, since every retry leaves a ghost behind. In agent workflows the cost
is worse than money: the ghost may go on to send an email or write a record the user thought they had
stopped.

The naive assumption is that the framework handles it, because the connection is gone. It does not,
in general. The ASGI server learns about the closed socket and puts an `http.disconnect` event on the
receive channel, but your handler is awaiting a provider call, not reading that channel, so nothing
interrupts it. Streaming responses get partial help in recent Starlette versions; ordinary endpoints
typically run to completion. Cancellation is something you wire up deliberately.

## How it works
### How a disconnect reaches the app
When the client closes the connection, the server marks it closed and the next `receive()` returns
`{"type": "http.disconnect"}`. Starlette exposes this as `await request.is_disconnected()`, which
checks for that message without blocking. Nothing is raised into the handler, *because* the handler
is the one deciding what to await.

### Streaming responses
For a `StreamingResponse`, recent Starlette versions run the body iterator alongside a task that
listens for `http.disconnect` and cancel the iterator when it arrives, so an `async for` over the
provider stream receives `CancelledError`. The exact behaviour has changed across releases, so also
check `is_disconnected()` between chunks, and open the upstream stream with `async with` so leaving
the block closes the upstream connection. Closing the upstream stream is what tells the provider or
inference engine to stop; self-hosted engines such as vLLM abort the sequence and free its cache,
while hosted providers' billing for partial output is vendor-specific.

### Ordinary endpoints
For a non-streaming endpoint, run the expensive work as a task and race it against a disconnect
watcher. If the watcher wins, cancel the task. Cancellation raises `CancelledError` at the task's
current `await`; inside httpx that aborts the request and closes the connection, *because* httpx
treats cancellation as abandoning the exchange. The result is cooperative: cancellation lands only at
an `await`, so CPU-bound code or a blocking call in a thread cannot be interrupted this way.

### Rules for code that may be cancelled
1. **Do not swallow `CancelledError`.** Catching `Exception` is safe because `CancelledError` derives
   from `BaseException`, but a bare `except:` or `except BaseException:` that does not re-raise turns
   cancellation into a silent continue.
2. **Put cleanup in `finally`**: release semaphore slots, close streams, record partial usage.
3. **Shield what must finish.** Writing the usage or audit record for tokens already spent should not
   be abandoned; run it under `asyncio.shield` or as a short, bounded step in `finally`, *because* a
   cancelled request still cost money and must be accounted for.
4. **Choose cancel points in agent loops.** Check for disconnect between steps, not in the middle of a
   side-effecting tool call. Tool calls that change external state should carry idempotency keys so a
   retry after cancellation cannot duplicate them.

### When not to cancel
If the user expects the work to finish without them watching, such as a long report or a batch
ingestion, cancellation on disconnect is wrong. Move it to a durable job, return a job ID, and let the
client reconnect for the result.

### The path in between
A proxy or load balancer between client and app may not propagate the disconnect promptly, or may
keep the upstream connection open while it buffers. Test the full path: start a long request through
the real ingress, close the client, and confirm the app logs a cancellation.

## Example
A helper that runs a non-streaming model call as a task and cancels it if the client disconnects,
returning 499 (the "client closed request" code popularised by nginx) for logs and metrics.

```python
import asyncio
from fastapi import FastAPI, Request, Response

app = FastAPI()

async def run_unless_disconnected(request: Request, coro, poll_s: float = 0.25):
    task = asyncio.create_task(coro)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll_s)
            if done:
                return task.result()               # re-raises the task's own exception
            if await request.is_disconnected():
                task.cancel()                      # CancelledError lands at the provider await
                await asyncio.wait({task})         # let its finally blocks run, no re-raise
                return None
    finally:
        task.cancel()                              # no-op if done; covers our own cancellation

async def answer_question(q: str) -> str:
    try:
        return await llm_complete(q)               # httpx call, closed on cancellation
    finally:
        await asyncio.shield(record_usage(q))      # billing record must survive cancellation

@app.post("/answer")
async def answer(body: dict, request: Request):
    result = await run_unless_disconnected(request, answer_question(body["q"]))
    if result is None:
        return Response(status_code=499)           # nobody is listening; count it
    return {"answer": result}
```

Scenario (illustrative): a support bot sees 20% of chat requests abandoned after about 3 seconds of a
typical 15-second generation. Without cancellation those requests consume the full 15 seconds of
model time and their slots; with it they free capacity after about 3 seconds, which under a fixed
concurrency cap translates directly into shorter queues for the users who stayed.

## Trade-offs
- Cancelling on disconnect saves tokens, slots and GPU memory, but loses work a user might have come
  back for; move such work to a durable job with a result the client can fetch later.
- Polling `is_disconnected()` frequently notices faster but adds overhead per request; a quarter to
  half a second is usually ample next to multi-second model calls.
- Aggressive cancellation in agent loops prevents ghost actions, but cancelling mid-tool-call can leave
  external systems half-updated; cancel between steps and make side effects idempotent.
- Shielding cleanup keeps billing and audit records complete, but a shielded step that hangs blocks
  shutdown; bound every shielded operation with its own timeout.

## Pitfalls
- Assuming the handler is cancelled automatically when the client leaves; provider dashboards show
  token usage well above what users actually received.
- A broad `except BaseException:` or bare `except:` around the model call that logs and continues; the
  cancellation is swallowed and the request keeps running after the disconnect.
- Cancelling the task but not closing the upstream stream; the app stops reading while the provider or
  engine keeps generating, visible as engine batch occupancy that does not drop.
- Releasing concurrency slots only on the success path; every cancelled request leaks a slot, and
  capacity shrinks until the worker appears hung.
- Testing only against the app directly; behind a buffering proxy the disconnect never arrives, so
  cancellation works in development and silently does nothing in production.

## When to use
Wire up disconnect-driven cancellation for every interactive endpoint whose work is long or expensive:
chat and completion endpoints, streaming generations, agent runs, and retrieval pipelines with
several model calls. Skip it for work that must complete regardless of the client, which belongs in a
job queue. Measure the rate of 499s or cancelled requests, tokens spent after disconnect, slot
occupancy, and time from disconnect to cancellation through the real ingress. In interviews, explain
that disconnect is a message, how cancellation propagates through `await`, and what must be shielded.

## Role lens
- **Engineer:** Races model calls against a disconnect watcher, propagates cancellation into the httpx
  or SDK call, and keeps cleanup and usage recording in `finally`.
- **Architect:** Decides which operations are cancellable, which must become durable jobs, and where
  agent loops are allowed to stop without leaving external systems inconsistent.
- **FDE:** Verifies that the customer's proxies and load balancers forward disconnects, and explains
  the token and capacity saved by cancellation using their abandonment rate.

## Check yourself
**Q:** An agent endpoint runs a loop of LLM calls and tool calls, one of which creates a ticket in the
customer's helpdesk. Users who press stop sometimes still get tickets created. How do you make stop
mean stop without corrupting state?
**A:** First detect the disconnect: run the loop as a task and cancel it when `is_disconnected()`
returns true, or have the loop check before each step. Cancellation lands at the next `await`, so
place explicit cancel points between steps rather than relying on wherever it happens to land. The
ticket-creating call should either complete or not start: check for disconnect before it, give it an
idempotency key so a retry cannot duplicate it, and shield only the bookkeeping that records what was
done. Make sure no broad `except` swallows `CancelledError`, and record the run's final state so the
user can see what happened before they stopped it.

## Quiz
### Q: How does an ASGI application learn that an HTTP client has disconnected?
- [ ] The server raises `ConnectionResetError` inside the handler immediately
- [x] The next `receive()` returns an `http.disconnect` message, which the app must read
- [ ] The response status is changed to 499 automatically
- [ ] FastAPI cancels every pending dependency
> Disconnect is delivered as a message on the receive channel. A handler that is awaiting a provider
> call never reads it unless it checks, which is what `request.is_disconnected()` does.

### Q: Why might a `try/except BaseException` around a model call stop cancellation from working?
- [x] `CancelledError` derives from `BaseException`, so the handler catches it and can continue running
- [ ] `BaseException` handlers disable the event loop's task scheduler
- [ ] Cancellation only works with synchronous code
- [ ] httpx converts cancellation into a `TimeoutError`
> Since Python 3.8, `CancelledError` is a `BaseException`, so `except Exception` lets it pass but a broad
> `BaseException` handler swallows it unless it re-raises, and the cancelled request keeps going.

### Q: Which work should not be cancelled when the client disconnects?
- [ ] A streaming chat completion the user stopped reading
- [ ] A retrieval-plus-generation request the user abandoned
- [x] Recording the usage and audit entry for tokens already spent
- [ ] An agent step that has not yet started its next model call
> The tokens were spent and billed whether or not anyone read them, so accounting must finish; shield it
> with a timeout. The other work produces output for a client that is gone, so cancelling it is the point.
