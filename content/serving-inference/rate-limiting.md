---
id: rate-limiting
module: serving-inference
name: Rate limiting
kind: pattern
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - serving-inference/backpressure
  - serving-inference/retry-strategies
  - serving-inference/batching-and-concurrency-limits
  - fastapi-for-ai/rate-limiting
  - infra-capacity-economics/api-provider-quotas-and-regional-limits
  - reliability/tenant-level-quotas-and-noisy-neighbor-isolation
  - backend-fundamentals/pagination-rate-limits-api-quotas
  - system-design/ai-gateway
sources:
  - Official docs: Anthropic API rate limits
  - Official docs: OpenAI API rate limits
  - IETF HTTPAPI working group — RateLimit header fields for HTTP (draft)
---

## TL;DR
Rate limiting bounds how much work a caller may submit per unit of time. In LLM systems it points two
ways: inbound, so one tenant or one runaway loop cannot consume the GPU or the provider quota everyone
shares; outbound, so your fleet stays under provider limits that are counted in requests *and* tokens per
minute. The LLM twist is that a request's cost is unknown until it finishes, so limiters reserve on an
estimate and settle on the actual.

## Why it exists
The resource behind an LLM call is shared, finite and expensive. A hosted provider gives your organisation
a quota, typically requests per minute and tokens per minute per model, and when it is exceeded the 429s
hit every caller in the organisation, not just the one that caused it. A self-hosted GPU has a fixed token
budget per second. Without an inbound limiter, one customer's retry loop or one agent stuck in a tool
cycle exhausts the shared allowance, every other customer sees errors, and the bill arrives anyway.

"Let the provider's 429 be our rate limiter" is the naive design and it fails on four counts. The blast
radius is the whole organisation. It is reactive: by the time the 429 arrives, the requests are already
queued and the latency already spent. It removes your ability to prioritise, because the provider does
not know which of your tenants matter. And it interacts badly with retries, since every rejected request
comes back as another.

"Limit requests per minute like any web API" is the second naive design. One request can carry 200
tokens or 200,000; a request limit is both gamed (pack more work per call) and useless for protecting the
resource that actually runs out, which is tokens. Rate limiting for LLMs therefore has to be cost-aware,
has to estimate costs before they are known, and has to run in two directions at once.

## How it works
### Units
Providers limit requests per minute, tokens per minute (sometimes input and output tokens separately,
sometimes per day as well) and occasionally concurrency; the exact names and tiers vary by vendor and are
published in their docs. Your inbound limiter should mirror this: per-tenant requests, per-tenant tokens
and, for agents, steps or dollars per task.

### Algorithms, and why the token bucket wins here
- **Token bucket.** A bucket of capacity *B* refills at *r* units per second; a request costing *c* is
  admitted if the bucket holds at least *c*, which it then removes. It permits bursts up to *B* while
  enforcing a long-run average of *r*, and its state is two numbers (level, last refill time). LLM
  traffic is bursty and cost-weighted, which is exactly what this models; the "tokens" in the name map
  directly onto LLM tokens if you set the units that way.
- **Leaky bucket.** Requests drain at a fixed rate, smoothing output; equivalent in enforcement terms
  (the GCRA formulation) but it reshapes bursts into delay rather than admitting them.
- **Fixed window.** A counter per minute. Simple, but it allows 2× the limit across a window boundary,
  since a caller can spend the full allowance at 0:59 and again at 1:00.
- **Sliding window log.** Store every request's timestamp; exact, but memory grows with traffic.
- **Sliding window counter.** Weight the previous window's count by its overlap with the current one;
  constant memory, approximate, good enough for most inbound limits.

### Reserve, then settle
A request's cost is input tokens, which you can count or estimate before sending, plus output tokens,
which you only learn when generation ends but which are bounded by `max_tokens`. The limiter therefore
*reserves* input plus an output estimate at admission and *settles* when the response arrives, refunding
the difference. Reserving the full `max_tokens` never overshoots but under-uses quota when clients set
generous limits; reserving an expected output length uses quota well but occasionally overshoots and
takes a provider 429. Which is right depends on how expensive an overshoot is for you. Either way,
clients who set `max_tokens` far above their needs shrink everyone's headroom.

### Distributed enforcement
Several application replicas share one provider quota, so the limiter's state must be shared, or the
quota statically partitioned. Shared state lives in Redis with the check-and-decrement done atomically
in a Lua script, because two replicas that read, decide and then write will both admit the last unit.
Static partitioning (each of three replicas gets a third) needs no coordination but wastes headroom
whenever load is uneven. A central gateway process is the third option and the usual home for
per-tenant policy.

### The response contract and the feedback loop
Inbound rejections return 429 with `Retry-After` and remaining-limit headers (a standard set of header
names is being defined by the IETF; providers currently use their own). Outbound, read the provider's
remaining-quota and reset headers and slow down before the first 429, decreasing the local rate when
headroom is thin. Keep 429 for "you exceeded your allowance" and 503 for "we are out of capacity";
clients fix them differently.

### Fairness and layering
Size per-tenant buckets from the plan, a global bucket from the provider quota minus a safety margin
(running at around 80% leaves room for retries and bursts), and let high-priority traffic borrow from
the global bucket before low-priority traffic does. Multiple keys, workspaces or regions multiply quota
when a single bucket is not enough.

## Example
A cost-aware token bucket with a reservation step. Capacity is ten seconds of refill so short bursts are
absorbed, the refill rate is 80% of an illustrative provider limit, and unused reservations are refunded
once the real output length is known. In production the bucket lives in Redis with `try_reserve` as an
atomic script.

```python
import time, threading

class TokenBucket:
    """Cost-aware limiter: `capacity` units, refilled continuously at `rate` units/second."""
    def __init__(self, capacity: float, rate: float):
        self.capacity, self.rate = capacity, rate
        self.level, self.updated = capacity, time.monotonic()
        self.lock = threading.Lock()
    def _refill(self):
        now = time.monotonic()
        self.level = min(self.capacity, self.level + (now - self.updated) * self.rate)
        self.updated = now
    def try_reserve(self, cost: float) -> float:
        """Reserve `cost` now. Returns 0 if admitted, else seconds until it would fit."""
        with self.lock:
            self._refill()
            if self.level >= cost:
                self.level -= cost
                return 0.0
            return (cost - self.level) / self.rate
    def settle(self, reserved: float, actual: float):
        with self.lock:                       # refund what the model did not actually use
            self.level = min(self.capacity, self.level + max(0.0, reserved - actual))

# Illustrative provider limit of 400k tokens/minute; run at 80% to leave headroom for retries.
TPM = 400_000 * 0.8
tpm_bucket = TokenBucket(capacity=TPM / 6, rate=TPM / 60)     # 10 s of burst, ~5,333 tokens/s

def call_llm(prompt_tokens: int, max_tokens: int):
    reserved = prompt_tokens + max_tokens                  # safe estimate; expected output is the alternative
    wait = tpm_bucket.try_reserve(reserved)
    if wait > 0:
        raise RateLimited(retry_after=round(wait, 1))      # inbound: 429 + Retry-After; outbound: sleep
    resp = provider.generate(max_tokens=max_tokens)
    tpm_bucket.settle(reserved, prompt_tokens + resp.usage.output_tokens)
    return resp
```

## Trade-offs
- Token buckets admit bursts, which users like, but a burst that fits your bucket may still exceed the
  provider's own windowing; keep burst capacity below what the upstream tolerates.
- Reserving `max_tokens` never overshoots but strands quota when clients over-specify; reserving an
  expected output uses quota fully but accepts occasional provider 429s, so pick by how costly an
  upstream rejection is.
- A shared Redis limiter is exact across replicas at the price of a round trip and a dependency; static
  partitioning is free and sufficient when replicas are few and load is even.
- Strict per-tenant limits guarantee fairness but block legitimate spikes; letting tenants borrow unused
  global headroom improves utilisation at the cost of weaker isolation.

## Pitfalls
- Limiting requests only; the symptom is provider 429s while your requests-per-minute dashboard is green,
  because long prompts exhausted the token limit.
- Fixed windows; the symptom is bursts of upstream 429s at minute boundaries as callers double-spend
  across the edge.
- Non-atomic check-then-decrement across replicas; the symptom is overshoot that scales with replica count
  and vanishes in single-instance tests.
- Never refunding reservations; the symptom is sustained throughput far below quota while the limiter
  reports itself full.
- Limiting after expensive work (retrieval, prompt assembly); the symptom is CPU and vector-store load
  that does not fall when requests are rejected.
- Counting tokens with the wrong tokenizer; the symptom is systematic under- or over-reservation that
  appears the day a new model is added.
- Treating 429 and 503 as interchangeable; the symptom is clients that back off per-caller when the problem
  is global, or that hammer a saturated server believing they are under quota.

## When to use
Put an inbound limiter in front of every LLM endpoint that serves more than one tenant or more than one
application, and an outbound limiter in front of every provider key that more than one process uses.
Start with per-tenant request and token buckets plus one global token bucket per provider model; add
priority borrowing and per-agent step budgets when agents appear. Measure rejection rate per tenant,
quota utilisation against the provider's limit, reservation-to-actual ratio and upstream 429 rate; the
last should be near zero if the outbound limiter works. In interviews, expect to be asked why requests
per minute is insufficient, how to limit when cost is unknown up front, and how to make a limiter correct
across replicas.

## Role lens
- **Engineer:** Implements the bucket in Redis with an atomic script, wires reserve and settle around
  every provider call, and returns 429 with Retry-After from the API layer.
- **Architect:** Allocates provider quota across tenants, priorities and regions, decides the safety
  margin, and defines what each plan tier is entitled to in tokens, not just requests.
- **FDE:** Explains to a customer why their nightly batch job starved the chat assistant, and configures
  per-workload buckets or separate keys so the two no longer share one allowance.

## Check yourself
**Q:** You have a single provider key limited to one million tokens per minute, three application
replicas and forty tenants of very different sizes. Design the rate limiting and explain each choice.

**A:** Two layers. Outbound: one global token bucket per provider model, held in Redis so all three
replicas share it, with capacity set to perhaps ten seconds of refill and the rate at about 80% of the
limit to leave headroom for retries. Each call reserves prompt tokens plus an output estimate and refunds
the difference on completion, because output length is unknown until the response ends. Inbound: a
per-tenant token bucket sized from the tenant's plan, enforced before retrieval or prompt assembly so
rejected requests cost nothing. Interactive traffic may borrow from global headroom before batch traffic
does. Rejections return 429 with Retry-After and limit headers; a global shortfall returns 503 instead so
clients back off correctly. I would read the provider's remaining-quota headers to slow down before the
first upstream 429, and monitor upstream 429 rate, per-tenant rejection rate and reservation accuracy. If
one tenant genuinely needs most of the quota, I would give them a separate key rather than let them
dominate the shared bucket.

## Quiz
### Q: Why is a requests-per-minute limit insufficient for LLM traffic?
- [ ] Providers do not publish request limits, only token limits
- [x] Requests vary in cost by orders of magnitude, so the real constraint, tokens per minute, can be exhausted by a few large requests
- [ ] Request counts cannot be shared across replicas
- [ ] Token limits are easier to compute than request counts
> A single 150k-token request costs as much as hundreds of short ones. Limiting requests alone leaves the
> token quota unprotected, which is why provider 429s appear while request dashboards look healthy.

### Q: What is the main weakness of a fixed-window counter?
- [ ] It requires storing a timestamp for every request
- [x] A caller can spend a full allowance at the end of one window and again at the start of the next, producing a 2× burst
- [ ] It cannot be implemented atomically in Redis
- [ ] It does not allow any bursting at all
> Fixed windows reset abruptly, so two full allowances can land within seconds across the boundary.
> Sliding windows and token buckets spread enforcement continuously and avoid the edge.

### Q: Why does an LLM token limiter reserve at admission and settle at completion?
- [ ] Because provider APIs require clients to pre-declare token usage
- [ ] To make the limiter work without a shared store
- [x] Because output token count is unknown until generation finishes, so the limiter must charge an estimate first and correct it afterwards
- [ ] To allow retries to reuse the original reservation
> Input tokens can be counted up front but output tokens cannot; `max_tokens` only bounds them. Reserving
> an estimate prevents overshoot while settling refunds unused quota so throughput is not stranded.
