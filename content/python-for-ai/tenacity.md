---
id: tenacity
module: python-for-ai
name: tenacity
kind: tool
difficulty: core
est_minutes: 12
status: fact-checked
related:
  - serving-inference/retry-strategies
  - reliability/circuit-breakers
  - serving-inference/idempotency
  - serving-inference/timeouts
  - python-for-ai/decorators
  - python-for-ai/exceptions-and-custom-exception-hierarchies
  - python-for-ai/httpx
  - agent-reliability/idempotent-tool-execution
sources:
  - Official docs: tenacity
  - Brooker, 2015 — Exponential Backoff And Jitter (AWS Architecture Blog)
---

## TL;DR
tenacity is a Python library that turns a retry policy into a declaration: when to stop, how long to wait,
which outcomes to retry, and what to log. LLM and embedding APIs fail transiently all the time (429s, 5xx,
timeouts), and a hand-written retry loop usually gets backoff, jitter and error classification wrong.
tenacity makes those choices explicit and the same for sync and async code, but it cannot make an unsafe
operation safe to repeat.

## Why it exists
A production AI service makes thousands of remote calls: chat completions, embeddings, vector-store queries,
tool APIs. Some fraction fail for reasons that go away on their own: rate limits, overloaded upstreams,
dropped connections. Without retries, each of those becomes a user-facing error or a dead agent run.

The naive retry, `for i in range(3): try: ... except Exception: time.sleep(1)`, fails in four ways. It
retries **everything**, including a 400 for a malformed request or a 401 for a bad key, wasting time and
quota on errors that will never succeed. It waits a **fixed** interval, so when a provider rate-limits a
fleet of workers, they all come back at the same instant and get limited again (the thundering herd). It
has **no total deadline**, so three attempts with long timeouts can hold a request open past the caller's
own timeout. And it is **invisible**: no log says a call needed four attempts, so a degrading dependency
looks healthy until it falls over. Writing this correctly every time, in sync and async variants, is the
gap tenacity fills.

## How it works
A retry policy in tenacity is built from four composable pieces passed to `@retry` (or to the `Retrying` /
`AsyncRetrying` objects):

### stop: when to give up
`stop_after_attempt(5)`, `stop_after_delay(30)` (seconds since the first attempt), combined with `|`
("whichever comes first"). A time-based stop matters *because* the caller has its own deadline; attempt
counts alone do not bound latency. Note that stop conditions are evaluated only after an attempt fails, so
`stop_after_delay` is a soft budget: an attempt that starts at 29 s still runs to completion (or to its own
timeout), and the true worst case is roughly the delay plus one wait plus one attempt timeout. Recent releases
add `stop_before_delay`, which also refuses an attempt that would begin past the budget; either way, keep the
per-attempt timeout small relative to the budget. **The default is never stop**, so a bare `@retry` loops
forever.

### wait: how long between attempts
`wait_fixed`, `wait_random`, `wait_exponential(multiplier=, min=, max=)`, and jittered variants such as
`wait_random_exponential` (a random wait up to an exponentially growing cap) and `wait_exponential_jitter`.
Waits can be added together, e.g. `wait_fixed(1) + wait_random(0, 2)`. Exponential growth backs off from an
overloaded server; jitter spreads clients out so their retries do not arrive in synchronized waves. The
default is no wait at all.

### retry: which outcomes count as failures
`retry_if_exception_type((RateLimitError, APITimeoutError))`, `retry_if_exception(predicate)`, or
`retry_if_result(lambda r: r is None)` for "successful" responses that are actually bad, combined with `|`
and `&`. This is where you encode the classification: retry 429, 5xx and timeouts; never retry 400, 401,
403 or validation errors. The default retries on any exception, which is exactly the classification bug above.

### hooks and the final failure
`before_sleep=before_sleep_log(logger, logging.WARNING)` logs each retry with the attempt number and wait.
When the stop condition fires, tenacity raises `RetryError` wrapping the last attempt; with `reraise=True`
it re-raises the original exception instead, which is usually what callers and error handlers expect.
Every callback receives a `RetryCallState` (attempt number, elapsed time, `outcome`), so a custom wait can,
for example, honor a server's `Retry-After` header. Decorated functions expose `.retry.statistics` and
`.retry_with(...)` to override the policy per call site.

### Async and code blocks
Decorating an `async def` works directly: tenacity detects the coroutine and sleeps with the event loop
rather than blocking it. For retrying a block instead of a function, iterate:
`for attempt in Retrying(...): with attempt: ...`.

### Alternatives
`backoff` is a similar decorator library; `stamina` is a thin, opinionated wrapper over tenacity with safer
defaults; `urllib3.Retry` and httpx transports retry at the HTTP layer only. Vendor SDKs (OpenAI,
Anthropic) already retry some errors internally through a `max_retries` setting, which you must account for
before adding your own layer.

## Example
A policy for a chat-completion call: retry only transient errors, exponential backoff with jitter, a
30-second retry budget, a log line per retry, and the original exception re-raised at the end. The SDK's own
retries are switched off so attempts do not multiply. Because the budget is checked between attempts, the
worst case here is about 30 s + one wait (up to 20 s) + one 20 s attempt timeout, roughly 70 s; if a caller
needs a strict ceiling, shrink the per-attempt timeout and wait cap or enforce an outer deadline.

```python
import logging
from openai import OpenAI, RateLimitError, APITimeoutError, APIConnectionError, InternalServerError
from tenacity import (retry, stop_after_attempt, stop_after_delay, wait_random_exponential,
                      retry_if_exception_type, before_sleep_log)

log = logging.getLogger("llm")
client = OpenAI(max_retries=0, timeout=20.0)   # one retry layer only: ours

TRANSIENT = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)

@retry(
    retry=retry_if_exception_type(TRANSIENT),            # 400/401/422 fail immediately
    wait=wait_random_exponential(multiplier=1, max=20),  # jittered, capped backoff
    stop=stop_after_attempt(5) | stop_after_delay(30),   # whichever comes first
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,                                        # caller sees RateLimitError, not RetryError
)
def summarize(text: str) -> str:
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": f"Summarize:\n{text}"}],
    )
    return r.choices[0].message.content

# Per-call override, e.g. a batch job that can wait longer:
# summarize.retry_with(stop=stop_after_attempt(10))(doc)
```

## Trade-offs
- Retries convert transient failures into latency instead of errors, which is right for background jobs but
  can blow a user-facing p99; tighten `stop_after_delay` when a person is waiting.
- Jittered exponential backoff protects a struggling upstream, but makes worst-case latency less predictable
  than a fixed wait; predictability wins only when there is a single client, not a fleet.
- Retrying in your code gives one place for policy and logging, but duplicates what SDKs and HTTP transports
  already do; it pays off when you disable the inner layers or retry at a higher level (a whole tool call).
- Per-call retries handle blips, but under a sustained outage they multiply load; beyond a few attempts a
  circuit breaker that fails fast is the better tool.

## Pitfalls
- Using bare `@retry` with no arguments; the symptom is a request that hangs forever, spinning with no
  wait, against an endpoint returning a permanent error.
- Retrying non-idempotent operations such as "send email" or "create ticket" without an idempotency key;
  the symptom is duplicate side effects after a timeout that actually succeeded upstream.
- Stacking retries in the SDK, your function and the agent loop; with 3 layers of 3 attempts the symptom is
  up to 27 upstream calls per user action and rate limits that get worse under load.
- Forgetting `reraise=True`; the symptom is error handlers that catch `RateLimitError` never firing because
  the exception arriving is a `tenacity.RetryError`.
- Retrying on `Exception` and thereby on your own bugs; the symptom is a `KeyError` in response parsing that
  costs five paid API calls before surfacing.

## When to use
Reach for tenacity whenever code calls something remote that can fail transiently and the operation is safe
to repeat: LLM and embedding calls, vector-store queries, idempotent tool APIs, downloads. Prefer the SDK's
built-in retries when they already cover your error classes and you only need to tune a count; use tenacity
when you need custom classification (retry on a bad *result*, such as invalid JSON), a time budget, logging,
or retries around a multi-step block. Do not use it to paper over permanent errors or sustained outages.
Measure attempts per call and retry rate per dependency; a rising retry rate is an early warning that a
provider is degrading.

## Role lens
- **Engineer:** Writes the stop/wait/retry policy per dependency, disables duplicate SDK retries, and adds
  `before_sleep` logging so retries show up in traces.
- **Architect:** Owns the retry budget across layers, which operations are idempotent, and where circuit
  breakers take over from retries.
- **FDE:** Tunes backoff to the customer's actual provider quota and gateway timeouts so retries fit inside
  their proxy's deadline instead of being cut off mid-attempt.

## Check yourself
**Q:** An agent's tool calls a customer's ticketing API through a function decorated with
`@retry(stop=stop_after_attempt(3))`. Customers report duplicate tickets. What is happening and how do you fix it?
**A:** Creating a ticket is not idempotent. When a request times out after the server has already created
the ticket, tenacity retries on the exception and creates a second one; the policy also retries on any
exception with no wait. Fixes, in order: send an idempotency key (or a client-generated ticket ID) so the
server deduplicates; restrict `retry=` to errors that guarantee the request was not processed, such as
connection refused or explicit 429/503; add jittered backoff and a time budget; and check whether the HTTP
client or SDK also retries, so attempts are not multiplied across layers.

## Quiz
### Q: What does `@retry` with no arguments do when the wrapped function keeps raising?
- [ ] Retries three times with exponential backoff, then re-raises
- [x] Retries indefinitely with no wait between attempts
- [ ] Retries once, then raises RetryError
- [ ] Retries only on network-related exceptions
> tenacity's defaults are stop-never, no wait, and retry on any exception. That is why every real policy
> sets stop, wait and retry explicitly; the "three attempts with backoff" behaviour must be configured.

### Q: Why add jitter to exponential backoff when many workers call the same LLM API?
- [ ] Jitter reduces the total number of attempts each worker makes
- [ ] Jitter is required for tenacity to work with asyncio
- [x] It desynchronizes clients so retries do not arrive together and trigger the rate limit again
- [ ] It makes the latency of each retry predictable for SLOs
> Without jitter, workers limited at the same moment back off by identical amounts and return in a wave.
> Randomizing the wait spreads the load; it makes individual latency less predictable, not more.

### Q: Your error handler catches `openai.RateLimitError`, but after retries are exhausted it never fires. Likely cause?
- [ ] The SDK swallows rate-limit errors after retries
- [ ] tenacity converts all exceptions to TimeoutError
- [x] Without reraise=True, tenacity raises RetryError wrapping the last attempt instead of the original exception
- [ ] before_sleep_log suppresses the final exception
> When the stop condition fires, tenacity raises RetryError by default. Setting reraise=True re-raises the
> last underlying exception, so existing except clauses for the SDK's error types keep working.
