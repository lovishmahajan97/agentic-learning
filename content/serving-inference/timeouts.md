---
id: timeouts
module: serving-inference
name: Timeouts
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - serving-inference/streaming
  - serving-inference/cancellation
  - serving-inference/retry-strategies
  - serving-inference/fallbacks
  - fastapi-for-ai/timeouts
  - reliability/circuit-breakers
  - infra-capacity-economics/latency-budgets
  - system-design/synchronous-vs-asynchronous-execution
sources:
  - Dean and Barroso, 2013 — The Tail at Scale
  - Official docs: httpx timeouts
  - Official docs: OpenAI Python SDK
---

## TL;DR
LLM calls last from under a second to several minutes, and the duration depends on an output length
nobody knows in advance plus queueing nobody controls. A single flat timeout therefore either kills
legitimate work or lets hung connections hold resources for minutes. Use layered timers (connect,
first token, idle gap between chunks, total deadline derived from `max_tokens × TPOT`), propagate
the remaining budget downstream, pair every expiry with cancellation, and audit the default timeouts
of every proxy between you and the model.

## Why it exists
Ordinary HTTP timeouts assume a response takes tens of milliseconds and that anything slower is
broken. An LLM response at 20 ms per token takes 20 s for 1,000 tokens when everything is healthy;
a reasoning model can pause for a long stretch before emitting anything; and a request queued behind
a burst can wait seconds before prefill even starts. Variance of 100× between fast and slow legitimate
calls is normal. Under those conditions a "30 seconds and give up" rule silently fails a large share
of long outputs, while "ten minutes to be safe" lets a dead upstream pin a worker, a connection-pool
slot and a user for ten minutes.

The damage compounds. Hung requests hold slots, so healthy requests queue behind them; the client
times out and retries, adding load to an upstream that is already slow; the retries time out too.
This is the classic overload spiral, and LLM backends are unusually prone to it because each request
is expensive and long. Meanwhile infrastructure you forgot about has its own opinions: reverse
proxies and load balancers ship with idle timeouts of about a minute, so a non-streaming generation
that takes 70 s is cut off with a gateway error and the engine keeps generating for nobody.

The naive alternative, "stream and never time out", is not enough either. Streaming turns "no bytes
for N seconds" into a usable liveness signal, but you still need a bound on total time so a stuck
stream cannot run until `max_tokens`, and you still need every downstream call in an agent step to
know how much of the user's patience remains.

## How it works
### The timeout stack
A single call crosses several layers, each with its own timer: the SDK (the OpenAI Python client
defaults to a 10-minute timeout and two retries), the HTTP library under it (httpx splits a timeout
into connect, read, write and pool phases, each defaulting to five seconds), your own server's
handler, the proxy or load balancer in front of it (nginx's `proxy_read_timeout` and an AWS ALB's
idle timeout both default to 60 s), the provider's gateway, and finally the engine's scheduler, which
may hold a request in its admission queue. The effective timeout is the *shortest* of these, and the
one that fires is rarely the one you configured.

### Which timers, and what each one means
- **Connect timeout.** TCP and TLS handshake. Short (a few seconds); failure here means the host is
  unreachable, and retrying elsewhere is cheap and safe.
- **Time to first byte / first token.** Covers queueing plus prefill. Set it from your TTFT p99 with
  margin (illustratively 10–30 s for long-prompt workloads); when it fires the request has consumed
  little, so a fallback or retry on another replica is reasonable.
- **Idle (read) timeout between chunks.** The liveness signal for a stream. A healthy stream emits a
  token every few tens of milliseconds, so an idle timer of 15–30 s only fires when the upstream is
  stuck, provided the server sends heartbeats during legitimate pauses such as hidden reasoning.
- **Total deadline.** The budget for the whole call, and for non-streaming calls the only timer that
  distinguishes "slow" from "dead". It must cover the longest *legitimate* response.

### Budget arithmetic
For a non-streaming call the deadline has to be `TTFT_p99 + max_tokens × TPOT_p99 + margin`. With
`max_tokens = 4096` and a TPOT of 30 ms that is already over two minutes before queueing, which is
why non-streaming calls with large `max_tokens` collide with 60-second proxies and why some SDKs
refuse very long non-streaming requests and tell you to stream. Derive the number from `max_tokens`
per call rather than hard-coding one constant, and cap `max_tokens` to what the product needs; a
smaller cap is the cheapest timeout fix there is.

### Deadline propagation
An agent step that must answer within 60 s and makes four LLM calls and two tool calls cannot give
each of them 60 s. Carry a deadline (an absolute timestamp) through the call chain, and give every
downstream call `remaining = deadline − now`. When `remaining` falls below the minimum useful time
for a step, fail fast with a partial result rather than start work that cannot finish. gRPC carries
deadlines natively; over HTTP you pass it as a header or in your own context object.

### What happens when a timer fires
Nothing stops upstream unless you make it. A client-side timeout that merely raises an exception
leaves the engine decoding, the provider billing, and the connection half-open until the server's
own timers notice. Every timeout must therefore close the connection or abort the request so the
signal reaches the engine; that is the cancellation lesson's job, and timeouts are its most common
trigger.

### Retrying after a timeout
A timeout is the most expensive failure to retry: the first attempt may still be running, it signals
overload more often than a transient glitch, and the retry needs the same long budget again. Retry
only when the remaining deadline allows it, with jitter, and only for idempotent requests. Prefer
falling back (a smaller or different model, a cached answer, a degraded response) over retrying the
same call, and open a circuit breaker when timeouts cluster, so a sick upstream is not hammered.

### Server-side timers if you run the engine
Bound the admission queue: a request that has waited longer than its first-token budget should be
rejected with a fast 429 or 503 rather than served late. Cap per-request generation time and
`max_tokens` at the API boundary, and expose queue wait as a metric so you can see the budget being
spent before clients time out.

## Example
Layered timeouts around a streaming call with `httpx`: a short connect timeout, an idle timer that
resets on every chunk, and a total deadline derived from `max_tokens` and measured TPOT. The caller
passes an absolute deadline so an agent can share one budget across several steps.

```python
import asyncio, json, time
import httpx

TTFT_P99, TPOT_P99 = 8.0, 0.05            # measured for this model and traffic, in seconds

def call_budget(max_tokens: int) -> float:
    return TTFT_P99 + max_tokens * TPOT_P99 * 1.5    # 1.5x margin on decode

async def stream_completion(client: httpx.AsyncClient, payload: dict, deadline: float):
    remaining = deadline - time.monotonic()
    budget = min(call_budget(payload["max_tokens"]), remaining)
    if budget < 2.0:
        raise TimeoutError("not enough budget left to start this call")
    timeout = httpx.Timeout(connect=3.0, read=20.0, write=10.0, pool=3.0)  # read = idle gap
    text = []
    try:
        async with asyncio.timeout(budget):                       # total deadline (Python 3.11+)
            async with client.stream("POST", "/v1/chat/completions",
                                     json={**payload, "stream": True}, timeout=timeout) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():                # read timeout resets per line
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    delta = json.loads(line[6:])["choices"][0]["delta"].get("content")
                    if delta: text.append(delta)
    except (TimeoutError, httpx.ReadTimeout) as e:
        # leaving the `async with client.stream` block closed the connection, which is the
        # signal the upstream needs to abort decoding; decide here whether partial text is usable
        raise TimeoutError(f"timed out after {len(text)} chunks: {e}") from e
    return "".join(text)

async def main():
    deadline = time.monotonic() + 60.0                            # the user's whole budget
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        out = await stream_completion(client, {"model": "my-model", "max_tokens": 512,
              "messages": [{"role": "user", "content": "Explain timeouts."}]}, deadline)
        print(out[:200])
asyncio.run(main())
```

## Trade-offs
- A short first-token timeout fails fast and enables cheap fallback, but under a traffic burst it
  turns a recoverable queue into a wave of errors; the balance flips when callers can wait and
  nothing else can serve them.
- Idle timeouts give a precise liveness signal for streams at the cost of needing heartbeats during
  legitimate pauses; without heartbeats they are worse than a plain total deadline.
- Deriving deadlines from `max_tokens` is accurate but means a generous cap buys a long timeout;
  tighter caps make timeouts tighter at the cost of truncating rare long answers.
- Retrying after a timeout can rescue a transient failure and can also double load on an overloaded
  upstream; the decision flips on whether remaining budget exists and whether the timeout rate is
  rising fleet-wide.
- Long server-side timeouts maximise the chance a slow request completes and let hung requests
  hold capacity; shorter ones protect the pool and shed work that would have finished.

## Pitfalls
- Inheriting defaults: a 60-second proxy or load balancer idle timeout in front of a non-streaming
  endpoint; the symptom is gateway errors at exactly 60 s on long generations while the engine keeps
  running.
- Treating the HTTP read timeout as a total timeout; the symptom is a "timeout" that never fires on a
  slow-but-alive stream, since it resets on every chunk.
- Timing out without cancelling; the symptom is provider usage and GPU load that stay high after the
  clients have given up, and tokens billed for answers nobody saw.
- One timeout constant for every call regardless of `max_tokens`; the symptom is short calls waiting
  minutes to fail and long calls being cut off while healthy.
- Agent steps that each get the full user budget; the symptom is a 60-second SLO breached by a
  request whose individual calls all "succeeded within timeout".
- Retrying timeouts immediately and without a budget check; the symptom is an overload that gets
  worse as clients time out, the signature of a retry storm.

## When to use
Every call to a model, a retriever or a tool needs the layered timers above; the question is only the
values. Derive them from measured percentiles and from `max_tokens`, revisit them when you change
models or prompt lengths, and give agents a single propagated deadline rather than per-call constants.
Stream whenever `max_tokens` is large, so liveness comes from the idle timer instead of a multi-minute
total. Measure timeout rate by layer and by cause (first-token versus idle versus total), and alert
when it rises, because it is usually the first visible sign of upstream overload. In interviews,
expect "how would you set timeouts for an LLM call" and answer with the stack, the arithmetic and the
cancel-and-fallback behaviour, not a number.

## Role lens
- **Engineer:** Configures connect, first-token, idle and total timers per call from `max_tokens` and
  measured TPOT, and makes every expiry close the upstream connection.
- **Architect:** Sets the end-to-end latency budget per surface, mandates deadline propagation across
  services, and decides where fallback replaces retry.
- **FDE:** Finds the 60-second idle timeout in the customer's load balancer or API gateway that is
  truncating long answers, and gets streaming enabled through it.

## Check yourself
**Q:** An agent makes up to six sequential LLM calls and three tool calls per user request, and the
user-facing SLO is 60 seconds. How do you set the timeouts, and what happens when the budget runs out?
**A:** Give the request one absolute deadline at entry and pass it to every step; each call gets
`min(its own derived budget, remaining)`. Derive each LLM call's budget from its `max_tokens` and
measured TPOT and stream it so the idle timer (with heartbeats) is the liveness check; give tools
budgets from their own latency percentiles. Before starting a step, check that enough budget remains
to be useful; if not, stop and return the best partial result or a clear "could not finish" rather
than start a call that will be cut off. Never retry a timed-out step unless the remaining budget
covers it, and prefer a cheaper fallback model. Every expiry must cancel the in-flight call so the
upstream stops. Measure which step consumes the budget in practice and shrink `max_tokens` or move
work earlier accordingly.

## Quiz
### Q: What is the most useful liveness signal for a streaming LLM response?
- [ ] The connect timeout, since a connected socket means the server is alive
- [ ] A single total timeout set generously to cover the longest answer
- [x] An idle timeout on the gap between chunks, combined with server heartbeats during legitimate pauses
- [ ] The time to first token, after which the stream can be trusted to finish
> A healthy stream delivers data continuously, so a stalled gap is the earliest and most specific
> sign of a dead upstream. A total timeout still bounds the worst case, but it reacts far later; TTFT
> says nothing about what happens after the first token.

### Q: Long non-streaming generations fail at exactly 60 seconds with a gateway error, and the GPU stays busy afterwards. What is the likely cause?
- [ ] The model has a built-in 60-second generation limit
- [x] An intermediary's default idle timeout cut the connection, and the engine was never told to abort
- [ ] The client's connect timeout is too short
- [ ] `max_tokens` is too low for the prompt
> Reverse proxies and load balancers commonly default to about a minute of idle time, which a
> non-streaming response exceeds with nothing sent. The engine keeps decoding because no cancellation
> reached it; streaming with heartbeats or a longer idle setting plus propagated cancellation fixes
> both halves.

### Q: A 90-second generation times out while the upstream is clearly overloaded. What is the safest default behaviour?
- [ ] Retry immediately with the same parameters to recover from the transient failure
- [ ] Extend the timeout and wait, since the answer is probably almost done
- [x] Cancel the in-flight request upstream, then fall back or fail clearly, retrying only if the remaining budget allows
- [ ] Switch to a non-streaming call so the response arrives in one piece
> Retrying a long, timed-out call under overload doubles the load that caused the timeout and may
> duplicate work still running upstream. Cancelling frees capacity, and a cheaper fallback or an
> honest failure respects the user's budget.
