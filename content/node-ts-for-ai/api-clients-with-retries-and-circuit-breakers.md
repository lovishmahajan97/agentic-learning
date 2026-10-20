---
id: api-clients-with-retries-and-circuit-breakers
module: node-ts-for-ai
name: API clients with retries & circuit breakers
kind: pattern
difficulty: core
est_minutes: 15
related:
  - serving-inference/retry-strategies
  - reliability/circuit-breakers
  - serving-inference/timeouts
  - serving-inference/idempotency
  - node-ts-for-ai/abortcontroller-and-cancellation
  - node-ts-for-ai/openai-anthropic-node-sdks
  - serving-inference/rate-limiting
  - enterprise-integration/retry-deduplication-reconciliation
sources:
  - Nygard, 2007 — Release It!
  - Brooker, 2015 — Exponential Backoff And Jitter (AWS Architecture Blog)
  - Official docs: Anthropic TypeScript SDK
  - Official docs: OpenAI Node SDK
---

## TL;DR
LLM and tool APIs fail transiently (429, 5xx, resets) far more than internal services, so every
outbound call needs a per-attempt timeout, a retry policy that retries only retryable errors with
exponential backoff and jitter, and a total deadline. A circuit breaker sits above that: when a
dependency is down it stops sending traffic for a cooling period, so your service fails fast instead of
piling up waiting requests and hammering a recovering provider. Vendor SDKs already retry; you add
timeouts, idempotency, breakers and bulkheads.

## Why it exists
A naive client does `await fetch(url)` and throws on error. In production that produces three distinct
failures. First, latency with no bound: a provider that accepts the TCP connection and then stalls
leaves your handler waiting until the socket gives up, which for Node's built-in `fetch` is measured in
minutes by default; a few hundred of those and every worker slot, pooled DB connection and queue lock is
held by a request going nowhere. Second, transient errors surface as user-facing failures: a 429 from a shared
quota or a 529 `overloaded_error` from Anthropic is usually gone in a second, so not retrying throws
away a request that would have succeeded. Third, the opposite naive fix, "retry everything, immediately,
five times", is worse: during a provider incident a thousand clients retrying in lockstep multiply the
load five-fold at exactly the wrong moment, synchronised by the same schedule, and non-idempotent calls
(a tool that sends an email, a POST that creates a resource) execute more than once.

Retries with backoff and jitter fix the second problem and defuse the third; timeouts fix the first;
idempotency keys make retries safe; and the circuit breaker handles the case backoff cannot. When the
dependency is down for minutes, the correct behaviour is not "wait 30 s then fail" per request, it is
"fail in a millisecond and try again later", which protects your own latency budget, frees resources
and gives the provider room to recover.

## How it works
### Timeouts first
Every attempt gets an `AbortSignal` with a per-attempt timeout (`AbortSignal.timeout(ms)` in current
Node, or an `AbortController` you cancel), *because* a retry policy without a timeout retries nothing
while the first attempt hangs. Time-to-first-token is a different number from total completion time:
for streaming calls put the timeout on the connection and first chunk, then an idle timeout between
chunks, not one giant budget that must cover a 90-second answer. Carry a request-level deadline through
the call chain so attempt timeouts plus backoff never exceed what the caller can wait for.

### Classify before retrying
Retry only what can succeed on a second try: network errors (`ECONNRESET`, `ETIMEDOUT`,
`ECONNREFUSED`), 408, 429, 500, 502, 503, 504 and Anthropic's 529. Do not retry 400, 401, 403, 404 or
422: the request is wrong and will be wrong again. Treat a timeout as retryable only if the operation is
idempotent, *because* the first attempt may have succeeded server-side after you stopped listening.

### Exponential backoff with full jitter
Attempt *n* sleeps `random(0, min(cap, base × 2^n))`. The exponential term backs off fast enough to help
a recovering provider; the cap keeps the wait sane; the jitter spreads clients out *because* identical
schedules re-synchronise the thundering herd on every attempt. Honour `Retry-After` (seconds or an HTTP
date; the Anthropic and OpenAI SDKs also read a millisecond variant) when present: the server knows its
own recovery window better than your formula. Bound by both attempt count and total elapsed time.

### Idempotency
A retried POST that creates a run, sends a message or charges a budget must be safe. Pass an idempotency
key the server deduplicates on (many APIs accept an `Idempotency-Key` header), or make the operation
naturally idempotent (PUT by id, upsert). If neither is possible, do not retry automatically; surface the
ambiguity to the caller.

### The vendor SDKs
The Anthropic TypeScript SDK retries connection errors, 408, 409, 429 and 5xx with backoff, honours
`Retry-After`, and exposes `maxRetries` (default 2) and `timeout` (default 10 minutes, in milliseconds).
A timed-out attempt is itself retried, so wall-clock time can reach `timeout × (maxRetries + 1)`, which
is the number to put in your deadline arithmetic. The OpenAI Node SDK follows the same design with the
same defaults. Errors come back as typed classes (`Anthropic.RateLimitError`,
`Anthropic.APIConnectionError`, `Anthropic.APIError` with `.status` and `.type` such as
`overloaded_error`), so classification is an `instanceof` chain, not string matching. The right move is
to configure those options rather than wrap the SDK in a second retry loop, *because* nested retries
multiply: 3 × 3 = 9 attempts against a provider that is already overloaded.

### Circuit breaker
A breaker wraps a dependency and keeps a small amount of state:
1. **Closed** (normal): calls pass through; failures are counted in a rolling window.
2. **Open**: once failures exceed a threshold (say, over half of at least 20 calls in 10 s), the breaker
   rejects calls immediately for a `resetTimeout` without touching the dependency.
3. **Half-open**: after the timeout, one trial call is let through; success closes the breaker, failure
   re-opens it.

It trips on rate over a minimum volume, not on a single failure, *because* one bad response is noise; it
rejects fast *because* a queued request that will fail anyway costs latency and a connection slot; and
it probes instead of flipping back blindly *because* you want evidence of recovery before releasing the
full load. A breaker needs a fallback: a smaller model, a cached answer, a degraded response, or a clear
error with a `Retry-After` for your own callers. In Node, `opossum` is the common breaker library;
`cockatiel` composes retry, timeout, breaker and bulkhead policies; `p-retry` covers retry alone.

### Bulkheads
Limit concurrent in-flight calls per dependency (`p-limit` or a semaphore) so one slow provider cannot
consume every worker slot. For LLM providers this doubles as a way to stay under concurrency-based
quotas, and backed by Redis it works across replicas.

## Example
A minimal `fetchWithRetry` with per-attempt timeout, error classification, full-jitter backoff and
`Retry-After`, plus a small count-based circuit breaker around it. Production code uses the SDK's
built-in retries and `opossum` or `cockatiel`, but the mechanics are these. Note that after the last
retryable status the response is returned, so callers still check `res.ok`.

```ts
const RETRYABLE = new Set([408, 429, 500, 502, 503, 504, 529]);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const jitter = (n: number, base: number, cap: number) => Math.random() * Math.min(cap, base * 2 ** n);

export async function fetchWithRetry(url: string, init: RequestInit,
                                     o = { attempts: 4, baseMs: 500, capMs: 8_000, attemptTimeoutMs: 20_000 }) {
  for (let n = 0; ; n++) {
    try {
      const res = await fetch(url, { ...init, signal: AbortSignal.timeout(o.attemptTimeoutMs) });
      if (!RETRYABLE.has(res.status) || n + 1 >= o.attempts) return res;     // success, non-retryable, or out of attempts
      const retryAfter = Number(res.headers.get("retry-after"));             // seconds, when the server sends one
      await sleep(retryAfter > 0 ? retryAfter * 1000 : jitter(n, o.baseMs, o.capMs));
    } catch (err: any) {
      const code = err?.cause?.code;                                          // undici wraps socket errors in err.cause
      const transient = err?.name === "TimeoutError" || ["ECONNRESET", "ETIMEDOUT", "ECONNREFUSED"].includes(code);
      if (!transient || n + 1 >= o.attempts) throw err;
      await sleep(jitter(n, o.baseMs, o.capMs));
    }
  }
}

export class CircuitBreaker {
  private failures = 0; private openedAt = 0; private state: "closed" | "open" | "half" = "closed";
  constructor(private threshold = 5, private resetMs = 30_000) {}
  async call<T>(fn: () => Promise<T>, fallback: () => T): Promise<T> {
    if (this.state === "open") {
      if (Date.now() - this.openedAt < this.resetMs) return fallback();         // fail fast, no network
      this.state = "half";                                                      // let one probe through
    }
    try {
      const out = await fn();
      this.failures = 0; this.state = "closed"; return out;
    } catch (e) {
      if (++this.failures >= this.threshold || this.state === "half") { this.state = "open"; this.openedAt = Date.now(); }
      throw e;
    }
  }
}
// const breaker = new CircuitBreaker();
// await breaker.call(() => fetchWithRetry(url, init), () => cachedOrDegradedAnswer());
```

## Trade-offs
- Retries raise success rate during blips but multiply load during outages; with jitter and a cap on total time the amplification is bounded, without them a fleet-wide retry storm can keep a provider down.
- Aggressive per-attempt timeouts keep latency predictable but cut off slow-yet-successful long completions; for large outputs use a time-to-first-token timeout plus an idle timeout rather than one short total budget.
- A circuit breaker protects your latency and the provider, but a mis-tuned threshold trips on normal error noise and takes a healthy dependency offline; thresholds should be rate-based over a minimum call volume.
- Falling back to a cheaper model or a cached answer keeps the product up at a quality cost users may notice; whether that beats a clean error depends on the task (a chat assistant can degrade, a compliance extraction probably cannot).
- The SDK's built-in retry is simpler and correct for its own error classes, but it knows nothing about your deadline or breaker; the integration point is its `maxRetries` and `timeout` options, not a wrapper loop around it.

## Pitfalls
- Nested retry loops (SDK retries inside your retries inside the queue's retries): the symptom is a single user action producing dozens of provider calls during an incident, visible as a cost spike in the same hour as the outage.
- Retrying non-idempotent POSTs on timeout: the symptom is duplicate runs, messages or charges that each carry a different request id but trace back to one user click.
- No jitter: the symptom is provider error rates oscillating in neat waves at your backoff intervals, as every client retries at the same instant.
- Breaker without a minimum volume: the symptom is the breaker opening at 03:00 because two of the night's three requests failed.
- Ignoring `Retry-After`: the symptom is 429s that keep coming despite backoff, because the server told you its window and you retried earlier than it.
- Treating a stream that failed after the first tokens as retryable and replaying it into the same response: the symptom is clients receiving an answer that restarts in the middle.

## When to use
Every outbound call to an LLM, embedding, vector, search or SaaS API gets a timeout and a classified
retry policy; this is the baseline, not an optimisation. Add a circuit breaker per dependency when the
service has a meaningful fallback or when the dependency's failure would otherwise exhaust worker
capacity; add a bulkhead when one slow dependency can starve the others. Do not retry inside a request
path when a queue above it already retries; decide which layer owns retries and make the others fail
fast. Measure attempts per request, retry success rate, breaker state transitions, p99 latency with and
without retries, and duplicate side effects. In interviews, derive the full-jitter formula, explain why
breakers trip on rate, and say which layer should own retries.

## Role lens
- **Engineer:** Configures SDK `maxRetries` and `timeout`, wraps tool calls with `cockatiel` or `opossum`, passes `AbortSignal`s and idempotency keys, and writes tests that simulate 429s, 529s and timeouts.
- **Architect:** Assigns retry ownership per layer (client, service, queue), defines breaker thresholds and fallbacks per provider, and sets the latency budget each dependency is allowed to consume.
- **FDE:** Tunes timeouts and limits to a customer's negotiated quotas and their egress proxy's behaviour, and explains why their incident dashboard shows retry waves.

## Check yourself
**Q:** During a provider incident your service's p99 latency went from 3 s to 90 s and your bill
tripled for the hour, even though the provider was mostly returning errors. Explain the mechanics and
the fix.
**A:** Each request waited the full default timeout on a provider that accepted connections but failed
slowly, then the SDK retried twice, then the handler's own loop retried three times, and the queue above
re-enqueued failures: a single user action became over a dozen slow attempts, which explains both the
latency and the cost, since some attempts were billed before erroring. The fix is layered: a
per-attempt timeout sized for the call, a single owner of retries (the SDK with `maxRetries` set and
nothing above it), full-jitter backoff honouring `Retry-After`, a total deadline per request, and a
circuit breaker per provider that opens on a high failure rate and fails fast with a fallback or a clear
error. Idempotency keys make any remaining retries safe to repeat.

## Quiz
### Q: Why add random jitter to exponential backoff rather than retrying at fixed exponential intervals?
- [ ] Jitter makes individual retries succeed more often
- [x] Without jitter, clients that failed together retry together, so each wave of retries hits the provider at the same instant
- [ ] Jitter is required by the HTTP specification for 429 responses
- [ ] It reduces the total number of attempts per request
> Backoff alone keeps the schedule synchronised across clients; randomising each sleep spreads the retries over the window so the provider sees a smooth load instead of spikes that re-trigger the overload.

### Q: A circuit breaker is in the open state. What happens to a new call?
- [ ] It is queued until the dependency recovers
- [ ] It is retried with backoff until the reset timeout elapses
- [x] It is rejected immediately (or served a fallback) without contacting the dependency
- [ ] It is sent, and the breaker closes if it succeeds
> Open means "known bad": the breaker fails fast to protect latency and the dependency, and only after the reset timeout does it move to half-open and allow a single probe call.

### Q: Which response should a well-behaved client NOT retry automatically?
- [ ] 503 Service Unavailable with a `Retry-After` header
- [ ] A connection reset before any response bytes arrived
- [x] 400 Bad Request caused by an over-long prompt
- [ ] 429 Too Many Requests from a shared rate limit
> A 400 means the request itself is invalid and will fail identically on every attempt; retrying only adds load and latency, whereas 503, 429 and connection resets are transient by definition.
