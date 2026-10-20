---
id: retry-strategies
module: serving-inference
name: Retry strategies
kind: pattern
difficulty: core
est_minutes: 13
status: reviewed
related:
  - serving-inference/timeouts
  - serving-inference/idempotency
  - serving-inference/rate-limiting
  - serving-inference/fallbacks
  - reliability/circuit-breakers
  - node-ts-for-ai/api-clients-with-retries-and-circuit-breakers
  - enterprise-integration/retry-deduplication-reconciliation
sources:
  - Brooker, 2015 — Exponential Backoff and Jitter (AWS Architecture Blog)
  - Beyer et al., 2016 — Site Reliability Engineering (Handling Overload)
  - Official docs: Anthropic SDK (retries and timeouts)
  - Dean & Barroso, 2013 — The Tail at Scale
---

## TL;DR
A retry re-issues a request that failed for a reason likely to be transient. Done well, it classifies the
error, waits with exponential backoff plus jitter, honours `Retry-After`, caps attempts and total time,
keeps a global retry budget and relies on idempotency; it then absorbs the steady trickle of 429s, 5xx
and connection resets that LLM APIs produce. Done naively, retrying everything immediately at every
layer, it turns a brief provider hiccup into a self-inflicted outage.

## Why it exists
Provider APIs fail a small fraction of the time by design: 429 when shared capacity is tight, 529 or 503
when overloaded, 5xx during deployments, connection resets through proxies, and timeouts on long
generations. At a 1% independent failure rate and 100,000 calls a day that is 1,000 user-visible errors;
a single well-placed retry cuts it to about 10, because the probability of two independent failures is
0.01%. That arithmetic is why every SDK retries by default.

The arithmetic breaks when failures are correlated, and during an incident they always are. If the
provider is saturated, each retry is new load on a system that is already dropping requests. Three
immediate retries quadruple the offered load; stack an SDK that retries twice (3 attempts), a service that
retries three times (4) and a browser that retries three times (4), and one user click becomes up to
3 × 4 × 4 = 48 attempts. That is the retry storm, and it can turn a 30-second blip into an outage that
lasts many minutes.

LLM calls raise the stakes further. Each attempt costs seconds and real money, a timed-out call may have
already done its work (or, in an agent, executed a tool with side effects), and a streaming response
cannot be transparently retried once tokens have reached the user. So the design question is not whether
to retry but exactly which failures, how many times, with what spacing, under what aggregate limit, and
with what guarantee that a duplicate is harmless.

## How it works
### Classify first
Retry: 408, 429, 500, 502, 503, 504, 529 (Anthropic's overloaded status), connection errors and, only
when the operation is idempotent, timeouts. Do not retry: 400 (malformed request, context-length
overflow), 401 and 403 (credentials), 404 (wrong model id), 413, 422 and content-policy refusals; they
fail identically next time. A 429 needs a second look: a per-minute rate limit clears in seconds, a
monthly quota exhaustion does not, and `Retry-After` tells you which.

### Exponential backoff with jitter
The wait before attempt *n* grows geometrically, `min(cap, base × 2ⁿ)`, so a persistent fault quickly
spreads retries thin. Pure exponential backoff has a flaw: every client that failed at the same instant
retries at the same instant, so the load arrives in synchronized waves at 1 s, 2 s, 4 s. Jitter breaks
the synchrony. With *full jitter* the wait is uniform between 0 and the exponential ceiling; with
*decorrelated jitter* it is uniform between the base and three times the previous wait, capped.
Brooker's simulations showed both complete the same work with far fewer total calls than un-jittered
backoff, which is why full jitter is a common library default.

### Honour the server's schedule
If the response carries `Retry-After`, use it instead of your own schedule: the server knows when
capacity returns, and waiting less is wasted load that may count against you. The header may be a
number of seconds or an HTTP date, so parse both, and still cap it: a `Retry-After` longer than the
request's remaining deadline means "give up or fall back now", not "sleep". Providers also return
remaining-quota and reset headers; an outbound rate limiter can read them to slow down *before* the
first 429.

### Caps and budgets
Per request: a maximum attempt count (two or three) and a total deadline that includes the backoff
sleeps, so a 30-second provider timeout cannot become a two-minute wait. Per client: a retry budget,
retries allowed only while they stay below roughly 10% of recent requests (the figure the SRE book uses).
The per-request cap bounds the worst case for one call; the budget bounds aggregate amplification during
an incident to about 1.1× instead of 3×. A common implementation is itself a token bucket: every
successful request deposits 0.1 tokens, every retry spends one, and retries stop when the bucket is
empty. In normal operation the bucket stays full and nothing changes; in an outage, where nothing
succeeds, it drains after a few retries and the client degrades to one attempt per request, which is
exactly the behaviour you want from a fleet hammering a saturated provider.

Worked numbers make the point. A fleet sends 200 requests per second; the provider goes fully down.
With three attempts per request and no budget, it receives 600 requests per second while it is trying
to recover. With a 10% budget it receives about 220. As the provider recovers, every request it rejects
from the first fleet comes back twice more, so partial recovery is met with up to triple load and can
stay pinned in overload; the second fleet's offered load is near its real demand from the start, and
successes refill the budget only as the provider actually heals.

### Retry at one layer
Know what the SDK does. The official Anthropic and OpenAI SDKs retry twice by default with exponential
backoff and honour `Retry-After` (check the current defaults for your version). Either disable that and
own the policy, or let it be the only retrying layer; two layers multiply.

### LLM-specific rules
- Prefer a tight time-to-first-token timeout plus a retry over a long total timeout plus a retry, because
  stalled requests rarely recover and the user is waiting.
- Never retry a stream after the first token has been forwarded; restart it as a new message id so the
  client can discard the partial one.
- Give every retried operation an idempotency key, so a retried tool call does not send a second email.
- Prompt caching makes retried prefill cheaper, since the retry reads the prefix the first attempt wrote,
  provided the first attempt got far enough to write it (on Anthropic an entry becomes readable once the
  first response begins).
- A hedged request (start a second attempt once the first has exceeded the p95 latency and keep whichever
  finishes first) cuts tail latency for a few percent of extra load, since only the slowest 5% of calls
  spawn a hedge; firing it immediately would double the load. It needs cancellation of the loser to be
  worth it (Dean and Barroso's "tail at scale" pattern).
- A wrong answer is not a transient failure. Validation-driven regeneration is a separate loop with its
  own bound and usually a changed prompt or temperature.

## Example
The helper below decides by status code, prefers the server's `Retry-After`, otherwise sleeps with full
jitter under a cap, refuses to exceed the request's deadline, and consults a process-wide retry budget so
that an incident cannot multiply load. In an async service replace `time.sleep` with `await asyncio.sleep`.

```python
import random, time

RETRYABLE = {408, 429, 500, 502, 503, 504, 529}
BASE_S, CAP_S, MAX_ATTEMPTS, DEADLINE_S = 0.5, 8.0, 3, 30.0

class RetryBudget:
    """Process-wide: retries may not exceed `ratio` of recent requests (use a sliding window in prod)."""
    def __init__(self, ratio=0.1): self.ratio, self.requests, self.retries = ratio, 0, 0
    def allow(self): return self.retries < self.ratio * max(self.requests, 10)

budget = RetryBudget()

def call_with_retry(send):
    deadline = time.monotonic() + DEADLINE_S
    budget.requests += 1
    for attempt in range(MAX_ATTEMPTS):
        try:
            return send()                                   # raises ApiError(status, retry_after)
        except ApiError as e:
            last_try = attempt == MAX_ATTEMPTS - 1
            if e.status not in RETRYABLE or last_try or not budget.allow():
                raise
            if e.retry_after is not None:                   # the server knows when capacity returns
                delay = min(float(e.retry_after), CAP_S * 4)
            else:
                delay = random.uniform(0, min(CAP_S, BASE_S * 2 ** attempt))   # full jitter
            if time.monotonic() + delay > deadline:
                raise                                       # a retry that cannot finish in time is waste
            budget.retries += 1
            time.sleep(delay)
```

## Trade-offs
- Retries raise success rates when failures are independent but amplify load when they are correlated;
  the retry budget is what makes the trade safe, and without it the first incident proves the point.
- A higher backoff cap is gentler on the provider but worse for the user; for interactive features cap at
  one or two short retries and then fall back, for batch pipelines cap in minutes and let the queue absorb it.
- Hedged requests cut tail latency but add load (a few percent when fired at the p95, up to double when
  fired immediately) and need cancellation; they pay off only when time-to-first-token variance is high
  and the call is cheap relative to the user's time.
- Retrying after timeouts recovers from stalls but risks duplicated effects; without idempotency keys the
  safe choice is to surface the timeout rather than retry.

## Pitfalls
- Retrying 400s and context-length errors; the symptom is the identical error three times in a row in the
  logs and triple the latency on every such request.
- No jitter; the symptom is load spikes at 1, 2 and 4 seconds after an incident starts, visible as a
  sawtooth on the provider's error graph.
- Stacked retries across SDK, service and client; the symptom is one user action producing 20 or more
  provider calls in a single trace.
- Ignoring `Retry-After`; the symptom is long streaks of 429s whose remaining-quota headers never recover
  because your retries keep spending the refill.
- `time.sleep` inside an async handler; the symptom is every in-flight request stalling whenever one
  request backs off.
- Retrying inside an open stream; the symptom is duplicated paragraphs appearing in the user interface.

## When to use
Every outbound LLM call needs an explicit retry policy, even if that policy is "the SDK's two attempts and
nothing else". Choose numbers from the latency budget: an interactive path gets one or two retries with a
small cap and then a fallback; a background job gets more attempts, a larger cap and a queue to wait in.
Pair retries with idempotency keys whenever the call has side effects, with a circuit breaker when the
dependency can be down for minutes, and with a rate limiter so retries never push you over quota. Measure
retry rate, success-after-retry rate and the amplification factor (provider calls per user request). In
interviews, the expected answer names classification, exponential backoff with full jitter,
`Retry-After`, per-request caps plus a global budget, and explains why layering retries is dangerous.

## Role lens
- **Engineer:** Configures or replaces SDK retries, implements jittered backoff and a budget, and makes
  sure async code never blocks the event loop while waiting.
- **Architect:** Sets the policy per traffic class, decides which layer owns retries, and defines the
  amplification factor the system may impose on providers during incidents.
- **FDE:** Traces a customer's "the API is flaky" complaint to stacked retries in their integration and
  tunes their client against the provider's Retry-After and quota headers.

## Check yourself
**Q:** You are shipping a chat feature that calls a hosted LLM through an SDK that already retries twice.
Describe the retry policy you would deploy end to end and justify each number.

**A:** I would make the SDK the only retrying layer for transport-level failures, keep its default of two
attempts, and set its timeout from the product's latency budget rather than the long default, since a
chat user leaves after a few seconds. Retries apply only to 408, 429, 5xx, 529 and connection errors; a
400 or a context-length error goes straight to the caller. Backoff is exponential with full jitter and
honours Retry-After. Above the SDK I add a circuit breaker and a fallback rather than more retries, so an
outage costs one short wait and then a different target, and a process-wide retry budget of about 10% so
that an incident cannot triple our load on the provider. Every turn carries an idempotency key, which
makes retrying a timed-out tool call safe, and streaming responses are never retried after the first
token; a failure there becomes a new message. I would monitor the amplification factor to prove the
policy holds.

## Quiz
### Q: Why add random jitter to exponential backoff?
- [ ] It shortens the average wait so users see lower latency
- [x] It desynchronizes clients that failed at the same moment, so their retries do not arrive in simultaneous waves
- [ ] It is required for the server's Retry-After header to be respected
- [ ] It prevents the exponential delay from exceeding the cap
> Without jitter every client that failed together retries together at 1, 2 and 4 seconds, recreating the
> overload. Spreading each wait uniformly over the window flattens those waves; Brooker's simulations
> showed it also reduces total calls.

### Q: Which of these should be retried against the same endpoint without changing the request?
- [x] A 503 Service Unavailable
- [ ] A 400 reporting that the prompt exceeds the context window
- [ ] A 401 Unauthorized
- [ ] A 422 reporting an invalid tool schema
> A 503 is a capacity signal that will likely clear. The other three describe defects in the request or
> credentials that will fail identically on every attempt, so retrying only adds latency and cost.

### Q: What does a client-wide retry budget provide that a per-request maximum attempt count does not?
- [ ] A guarantee that no single request waits longer than the deadline
- [ ] Protection against retrying non-idempotent operations
- [x] A bound on aggregate load amplification when every request is failing at once
- [ ] Automatic detection of which errors are transient
> Three attempts per request still means 3× load when all requests fail together. A budget limits retries
> to a fraction of recent traffic, so during an incident the extra load stays near 10% instead of 200%.
