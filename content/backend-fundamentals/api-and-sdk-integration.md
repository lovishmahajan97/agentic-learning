---
id: api-and-sdk-integration
module: backend-fundamentals
name: API & SDK integration
kind: practice
difficulty: core
est_minutes: 14
status: reviewed
related:
  - python-for-ai/openai-sdk
  - python-for-ai/anthropic-sdk
  - python-for-ai/httpx
  - python-for-ai/tenacity
  - serving-inference/retry-strategies
  - serving-inference/timeouts
  - reliability/circuit-breakers
  - node-ts-for-ai/api-clients-with-retries-and-circuit-breakers
sources:
  - Official docs: httpx
  - Official docs: OpenAI Python SDK
  - Official docs: Anthropic Python SDK
  - Nygard, 2007 — Release It!
---

## TL;DR
Most of an AI system is glue to APIs you do not control. An SDK is a vendor-maintained HTTP client
that bakes in auth, typed models, retries, pagination and stream parsing; use it, but know what it
does with timeouts, retries and versions, because its defaults suit the average caller, not your
latency budget. The durable skill is a thin integration layer with explicit timeouts, a retry policy,
idempotency, an error taxonomy and observability, whether or not an SDK sits underneath.

## Why it exists
A model provider, a vector database, a CRM, a payment processor: each is a remote system that will
return 429 when you are busiest, 503 during its deploys, a half-finished stream when a load balancer
recycles, and a slightly different JSON shape after a release you did not read about. Your code must
plan for every one of those, and the place that plan lives is the integration layer.

The naive version is `requests.post(url, json=payload)` sprinkled through the codebase. It has no
timeout (the `requests` library applies none by default), so a hung provider pins a worker forever.
It has no retry, so a transient 503 becomes a user-visible failure. It raises raw exceptions, so
callers cannot tell "retry later" from "your request is invalid". Its API key is read in twelve
places, so rotation is a treasure hunt. The symptom of this design is an incident where every worker
is blocked waiting on one vendor and the dashboard shows zero errors.

The opposite naive version is "the SDK handles it". It partly does: official SDKs retry on 429 and
5xx with backoff, parse streams and type the responses. But SDK defaults are generic. Automatic
retries silently multiply the latency of a slow outage, and the cost too whenever an attempt the
provider already processed is abandoned and resent; default timeouts in provider SDKs (on the order of
ten minutes in the major Python LLM SDKs at the time of writing) are long enough to let one stuck call
hold a request for minutes; and an SDK upgrade can change the API version it speaks. You still need a layer that owns those decisions explicitly.

## How it works
### Raw HTTP versus SDK
An SDK gives you: authentication headers, a base URL, typed request and response models, retry with
exponential backoff on retryable statuses (honouring `Retry-After`), SSE parsing into events,
auto-pagination iterators, and matching sync and async clients. It hides: the connection pool, the
timeout values, which calls it considers safe to retry, and the mapping from HTTP status to exception
class. Read the client constructor for `timeout` and `max_retries` before shipping; those two numbers
define your worst-case latency.

### The adapter: one module owns the vendor
Wrap each external system in one module that exposes your own types and raises your own exceptions.
This exists *because* vendor types and errors leak otherwise, and swapping or adding a provider becomes
a rewrite. Inside the adapter, classify failures into a taxonomy:

- **Retryable:** 429, 5xx (most often 502/503/504), connection errors, timeouts, truncated streams.
- **Terminal:** 400/422 (your request is wrong), 401/403 (credentials), 404, content-policy refusals.
- **Ambiguous:** a timeout on a non-idempotent call; the request may have completed.

### Timeouts
Set connect, read and total timeouts separately. In httpx and most HTTP clients the read timeout is
the longest allowed gap between received bytes, not a deadline for the whole response: on a stream it
bounds time-to-first-token and every inter-chunk gap, and on a non-streaming model call (where the
server sends nothing until generation finishes) it effectively bounds generation time. A streaming call
may legitimately run for minutes while each chunk arrives within a second, so the read timeout is a
per-chunk budget. A total deadline needs a separate wrapper (`asyncio.timeout` around the call); tie it
to what the user-facing request can afford, not to what the provider allows.

### Retries, idempotency and budgets
Retry retryable errors with exponential backoff and full jitter (sleep a uniform random time in
`[0, min(cap, base × 2^n)]`), and honour `Retry-After` when present, *because* synchronized retries from many clients create a thundering
herd. Retry only operations that are idempotent or carry an idempotency key; an LLM completion is
usually safe to re-run (it costs money, not correctness), while a tool call that sends an email is
not. Bound retries per request and per time window so an outage does not triple your traffic.

The arithmetic of a retry policy is worth doing once. With *a* attempts, per-attempt timeout *t* and
total backoff sleep *S*, worst-case latency is `a·t + S`, and during a full outage the load you
send the vendor is multiplied by up to *a*. Layering makes it worse: if your adapter makes 3 attempts
around an SDK that makes 3 per call (its default of 2 retries), one user request can become 9
upstream calls. Retry in exactly one layer and switch the others off (`max_retries=0` on the SDK client).

### Streams fail differently
A streaming call can return `200`, send half the answer and then stop: a load balancer recycled the
connection, or the provider hit an internal error mid-generation. The status code already said
success, so the only signal is the missing terminal event (a final `finish_reason`, a stop event or a
`[DONE]` sentinel, depending on the provider). The adapter must treat a stream that ends without its
terminal event as a retryable failure, not as a short answer. Retrying a stream is also not free of
user impact: tokens already shown cannot be unshown, so the choice is to restart and replace the
partial text, or to surface the truncation and let the user retry.

### Circuit breakers, fallbacks and connection reuse
After N consecutive failures stop calling the vendor for a cool-down and fail fast or route to a
fallback model; this protects your own latency and the vendor's recovery. Keep one client per process
so TLS handshakes and connections are reused; creating a client per request is a common source of
socket exhaustion.

### Configuration, observability and tests
Keys come from a secret store or environment, never source; log the vendor's request id, status,
latency, attempt count and token usage on every call with a correlation id. Pin SDK versions and any
API-version header. Test with recorded responses for happy paths and injected 429/timeout/truncation
for the failure paths, and run a small contract test against the live sandbox on a schedule.

## Example
A minimal adapter around a model endpoint using `httpx`: explicit per-phase timeouts, a shared client,
an error taxonomy, bounded retries with jitter that honour `Retry-After`, an idempotency key so a
retried `POST` cannot double-execute, and one total deadline that the retries must fit inside.

Walk the worst case to see why the deadline matters. Without it, three attempts that each hit the
60-second read timeout plus backoff sleeps can hold the caller for over three minutes. With a
20-second budget, the first attempt times out at the deadline and the caller gets a `TimeoutError` at
20 seconds that it can turn into a fallback or a clear error. A `Retry-After: 30` hint on a 429 is
also handled correctly: it cannot fit in the remaining budget, so the adapter fails immediately
instead of sleeping past the point where the user has given up.

```python
import asyncio, os, random
import httpx

class RetryableError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg); self.retry_after = retry_after

class TerminalError(Exception): ...

TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)   # explicit, per phase
client = httpx.AsyncClient(base_url="https://api.example-llm.com", timeout=TIMEOUT,
                           headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"]})

def classify(r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    if r.status_code == 429 or r.status_code >= 500:
        ra = r.headers.get("retry-after")
        raise RetryableError(f"HTTP {r.status_code}", float(ra) if ra and ra.isdigit() else None)
    raise TerminalError(f"HTTP {r.status_code}: {r.text[:200]}")   # 400/401/403/422: retrying won't help

async def complete(payload: dict, idem_key: str, attempts: int = 3, budget_s: float = 20.0) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_s                  # total budget across ALL attempts and sleeps
    async with asyncio.timeout_at(deadline):           # Python 3.11+; raises TimeoutError at the deadline
        for n in range(attempts):
            try:
                r = await client.post("/v1/complete", json=payload,
                                      headers={"Idempotency-Key": idem_key})   # safe to retry
                classify(r)
                return r.json()
            except (RetryableError, httpx.TimeoutException, httpx.TransportError) as e:
                hinted = getattr(e, "retry_after", None)
                delay = hinted if hinted else random.uniform(0, min(8.0, 2 ** n))  # full jitter
                if n == attempts - 1 or loop.time() + delay >= deadline:
                    raise                              # retrying cannot finish inside the budget
                await asyncio.sleep(delay)
    raise RuntimeError("unreachable")
```

## Trade-offs
- An SDK saves weeks of client code and tracks API changes for you, but it chooses retry and timeout
  defaults you must override, and it couples you to its release cadence; raw HTTP flips to the
  better choice only for tiny surfaces or unsupported languages.
- Automatic retries hide transient failures from users, but each retry costs money and latency and
  can amplify an outage; the balance flips when the operation is non-idempotent or the budget is tight.
- A thin adapter per vendor adds a layer of types and mapping, but it makes provider swaps, fallbacks
  and tests cheap; for a single-vendor prototype it can feel like ceremony.
- Aggressive timeouts protect your latency budget, but they turn slow successes into failures and
  create ambiguous outcomes on non-idempotent calls.

## Pitfalls
- No timeout or an inherited multi-minute default; the symptom is worker pools draining during a
  vendor incident while error rates stay at zero.
- Retrying everything, including 400 and 401; the symptom is the same invalid request hammering the
  vendor four times and a rate-limit ban on top of the original bug.
- Retrying a non-idempotent `POST` after a timeout without an idempotency key; the symptom is
  duplicate records, double charges or two emails sent.
- Retrying in two layers (your wrapper and the SDK, or a gateway and the app); the symptom is
  vendor-side request counts several times your own request count during an incident.
- Treating a stream that ended without its terminal event as complete; the symptom is answers that
  stop mid-sentence and parsers failing on truncated JSON with no error in the logs.
- A new client per request; the symptom is a TLS handshake on every call and "too many open files"
  under load.
- Parsing vendor error bodies ad hoc in callers; the symptom is a schema change in the error payload
  breaking twelve call sites at once.

## When to use
Every external call deserves the adapter treatment, scaled to its blast radius: a model provider on
the request path gets timeouts, retries, idempotency keys, a breaker and metrics; a nightly report
API can live with a timeout and a retry. Use the official SDK when one exists and you have read its
timeout and retry settings; drop to `httpx` when the SDK lacks a feature, blocks the event loop, or
you need exact control over streaming. Measure attempt counts, p99 latency including retries, and
vendor request ids in logs. In interviews, "how do you call a third-party API in production" is a
standard question; the expected answer walks timeouts, retry policy, idempotency, error taxonomy and
observability in that order.

## Role lens
- **Engineer:** Builds and tests the adapter: explicit timeouts, retry with jitter, idempotency keys,
  one shared client, and error mapping that callers can branch on.
- **Architect:** Sets the integration standard (which errors retry, the retry budget, breaker
  thresholds, fallback routing) and decides where provider abstraction lives.
- **FDE:** Integrates the AI service with a customer's internal APIs whose SDKs may be absent or
  unreliable, and negotiates sandbox access, rate limits and credentials for them.

## Check yourself
**Q:** Your service calls a model provider through its official SDK with defaults. During a provider
incident, user requests take four minutes to fail and your token spend for the hour jumps well above
normal. Explain what happened and how you would configure the integration differently.
**A:** The SDK retried each failing call (twice by default in the major LLM SDKs) with backoff, and its
long default timeout let each attempt wait minutes, so one user request became several long attempts.
Each retry resent the full prompt, and attempts the provider had partly or fully processed before the
connection died or the client gave up are generally still billed, while users who saw failures retried
on top; that is the cost spike (exactly which failed requests are billed is provider-specific). I would set an explicit total timeout tied to the user-facing
budget, cap retries to one or two with jitter and honour `Retry-After`, add a circuit breaker that
fails fast or routes to a fallback model after consecutive failures, and emit attempt-count and
latency metrics so the behaviour is visible. Idempotency keys would make the remaining retries safe
if the call had side effects.

## Quiz
### Q: Which failure should an integration layer not retry automatically?
- [ ] A 503 from the provider's load balancer
- [ ] A connect timeout
- [x] A 422 saying a required field is missing
- [ ] A 429 with a `Retry-After` header
> Retries help only when the next attempt can succeed. A validation error is deterministic, so
> retrying repeats the same bad request, wastes quota and delays the real fix.

### Q: Why add an `Idempotency-Key` header when retrying a `POST` after a timeout?
- [ ] It makes the request faster on the second attempt
- [x] The first attempt may have completed, and the key lets the server return that result instead of executing again
- [ ] It tells the server to skip authentication on retries
- [ ] It disables the server's rate limiter for the retry
> A timeout is ambiguous: the server may have done the work. An idempotency key lets it recognise the
> repeat and return the stored outcome, preventing duplicate side effects.

### Q: What is the main reason to keep one HTTP client per process rather than per request?
- [ ] Per-request clients cannot send custom headers
- [ ] A single client guarantees ordered delivery of requests
- [x] Connection and TLS session reuse, plus one place to configure timeouts and limits
- [ ] Multiple clients would share the same API key incorrectly
> Each new client opens fresh connections and TLS handshakes, which cost latency and sockets. A shared
> client pools connections and centralises the configuration the whole service depends on.
