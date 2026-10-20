---
id: rate-limiting
module: fastapi-for-ai
name: Rate limiting (slowapi)
kind: tool
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - fastapi-for-ai/api-keys-oauth2-jwt
  - fastapi-for-ai/concurrency-limits
  - fastapi-for-ai/backpressure
  - fastapi-for-ai/redis-caching
  - fastapi-for-ai/middleware-ordering
  - serving-inference/rate-limiting
  - reliability/tenant-level-quotas-and-noisy-neighbor-isolation
  - backend-fundamentals/pagination-rate-limits-api-quotas
sources:
  - Official docs: SlowApi
  - Official docs: limits (Python library)
  - Fielding, Nottingham, Reschke, 2022 — RFC 9110: HTTP Semantics (Retry-After)
---

## TL;DR
An LLM endpoint turns every request into real spend and scarce GPU or provider quota, so one runaway
script can drain your budget or starve every other tenant. Rate limiting caps how fast each caller may
consume. slowapi is a FastAPI/Starlette port of Flask-Limiter: decorator-based limits like
`"10/minute"` keyed per client, counted in memory or Redis, returning 429. It counts *requests*. AI
services usually also need a *token* budget, which you build next to it.

## Why it exists
Your upstream capacity is fixed: the provider grants your organisation a requests-per-minute and
tokens-per-minute quota, or your own GPUs can decode only so many tokens per second. Demand is not
fixed. A customer's retry loop with no backoff, a leaked API key, a batch job someone started at full
speed, or a scraper hitting a free tier can each consume the whole budget. Without per-caller limits,
the provider's 429s land on *everyone*, and the bill lands on you.

Two naive answers fall short. "Limit concurrency globally" protects the server but not fairness: one
tenant fills every slot. "Let the provider rate-limit us" pushes the limit to the wrong place, because
the provider sees one organisation, not your hundred customers, and its throttling fails random users
rather than the one causing the load. You need a limit applied *per caller identity*, enforced *before*
the expensive call, and shared across every worker that serves traffic, with an answer the client can
act on (429 plus `Retry-After`) instead of a timeout.

## How it works
### What slowapi does
slowapi wraps the `limits` library, which does the counting, and adds FastAPI glue. You create a
`Limiter(key_func=..., storage_uri=..., strategy=...)`, put it on `app.state.limiter`, register a
handler for `RateLimitExceeded`, and decorate routes with `@limiter.limit("20/minute")`. A decorated
endpoint must accept a `request: Request` parameter, because slowapi reads the key from it. Default
limits for every route can be set on the `Limiter` and applied through `SlowAPIMiddleware`. For each
request, slowapi computes `key_func(request)`, builds a counter key from the key, the route and the
limit, increments it in storage, and raises `RateLimitExceeded` when the count is over the limit. The
handler turns that into a 429.

### The key function decides fairness
The default `get_remote_address` uses the socket peer's IP. Behind a load balancer that is the
balancer's IP, so every user shares one bucket, unless uvicorn trusts the balancer. Uvicorn's
`--proxy-headers` is on by default, but it only rewrites the client address from `X-Forwarded-For`
when the connection comes from an address in `--forwarded-allow-ips`, which defaults to 127.0.0.1.
Set it to the balancer's range (never `*` on a port the internet can reach directly, or anyone can
forge the header). For an authenticated
AI API, key by API key or tenant instead. IPs are shared by whole offices (NAT) and rotated by
attackers.

### Storage decides correctness
The default in-memory storage is per process. Run four uvicorn workers and each keeps its own counters,
so the effective limit is four times what you wrote, and it resets on deploy. Use `storage_uri=
"redis://..."` so every worker and replica increments the same counter. `limits` relies on Redis's
atomic operations, so concurrent increments don't race.

### Algorithms (strategy)
- **Fixed window** (the default): one counter per calendar window. It is cheap (one key, one increment),
  but a client can send the full limit at 12:00:59 and again at 12:01:00, which is up to 2x the limit
  in a two-second burst.
- **Moving window**: stores a timestamp per hit and counts those within the trailing window. It is
  exact, but memory and work grow with the limit.
- Recent versions of `limits` also offer a **sliding-window counter**, which approximates the
  trailing window from two fixed-window counters weighted by overlap. It is nearly exact and cheap.
- Gateways often use **token bucket** or GCRA: capacity *b* refills at rate *r*. It allows bursts up
  to *b* and a sustained rate of *r*, which suits chat traffic.

### Requests are not tokens
"60 requests/minute" treats a 50-token classification and a 100k-token document summary the same,
but the second costs 2,000 times as much. Providers limit both RPM and TPM, so you should too. The
standard technique is to *reserve* an estimate before calling (prompt tokens counted with the
tokenizer plus `max_tokens`), reject if the reservation exceeds the remaining budget, then *reconcile*
with the actual usage the provider returns. slowapi's decorator runs before the handler and doesn't
know the cost, so token budgets are usually a small custom dependency on the same Redis.

### Alternatives
Limits at the edge (nginx `limit_req`, Envoy, an API gateway, a cloud WAF) run before Python and are
cheaper per rejected request, but they rarely know tenant or token cost. `fastapi-limiter` offers a
dependency-style alternative, so limits sit next to your other `Depends`. Many teams combine a coarse edge limit (IP, DDoS) with
slowapi or custom limits per tenant in the app.

## Example
Per-API-key request limits with slowapi, plus a token-per-minute budget as a dependency.

```python
import hashlib, time
from fastapi import Depends, FastAPI, HTTPException, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
import redis.asyncio as redis

def caller(request: Request) -> str:            # key by credential, not IP
    # production: key on the *validated* principal; a raw header lets random keys mint new buckets
    key = request.headers.get("x-api-key")
    return hashlib.sha256(key.encode()).hexdigest()[:16] if key else get_remote_address(request)

limiter = Limiter(key_func=caller, storage_uri="redis://redis:6379",
                  default_limits=["600/hour"])
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)            # applies default_limits
r = redis.Redis(host="redis")
TPM = 40_000                                      # tokens per minute per caller

async def token_budget(request: Request) -> int:
    body = await request.json()                   # cached; the endpoint still parses it
    est = len(str(body.get("messages", ""))) // 4 + body.get("max_tokens", 512)  # rough estimate
    key = f"tpm:{caller(request)}:{int(time.time() // 60)}"
    used = await r.incrby(key, est)
    await r.expire(key, 120)
    if used > TPM:
        await r.decrby(key, est)                  # release the rejected reservation
        raise HTTPException(429, "token budget exceeded",
                            headers={"Retry-After": str(60 - int(time.time()) % 60)})
    return est

@app.post("/v1/chat")
@limiter.limit("30/minute")
async def chat(request: Request, body: dict, est: int = Depends(token_budget)):
    # after the call: await r.incrby(key, actual_tokens - est) to reconcile
    return {"reply": "..."}
```

Scenario: a caller sends 30 short requests within one minute window (about 600 tokens each, 18k
total), so request 31 gets slowapi's 429. Another caller sends 3 requests, each estimated at about 15k
tokens (document plus `max_tokens`): the third pushes the reservation to about 45k, which exceeds 40k,
so it gets 429 with `Retry-After` and its 15k is released. The request limit alone would have let it
through. One ordering detail: FastAPI resolves `Depends` before it calls the endpoint, and slowapi's
decorator does its check inside the endpoint wrapper, so request 31 has already reserved its tokens
when slowapi rejects it. Stash the reservation on `request.state` and release it in the
`RateLimitExceeded` handler, or run both checks as dependencies in a fixed order.

## Trade-offs
- In-memory storage needs no infrastructure and adds no latency, but it is only correct with a
  single process. Once you run several workers or replicas, Redis is required, which adds a network
  round trip and a dependency whose outage you must decide to fail open or closed on.
- Fixed windows are the cheapest, but they allow boundary bursts of up to 2x. Moving or sliding windows
  smooth this at more storage cost. That matters when your upstream quota is itself enforced tightly.
- Keying by IP works for anonymous endpoints, but it punishes shared NATs and is easy to evade. Keying
  by API key or tenant is fair and harder to evade, but it requires authentication to run first.
- App-level limits know tenant and token cost but spend Python cycles on each rejected request. Edge
  limits are cheaper under attack but coarser. Use both when abuse volume is high.

## Pitfalls
- Running multiple workers with in-memory storage. The symptom is that load tests show the limit is
  exceeded by roughly the worker count, and limits reset at every deploy.
- Keying by `get_remote_address` behind a proxy without proxy headers. The symptom is all traffic
  sharing one bucket, so legitimate users get 429s whenever anyone is busy.
- Forgetting the `request: Request` parameter on a decorated endpoint. The symptom is an exception at
  startup or on the first call complaining that no request argument was found.
- Limiting only requests on an LLM endpoint. The symptom is a tenant within its RPM limit blowing
  through the provider's TPM quota and causing upstream 429s for everyone.
- Returning 429 without `Retry-After`, or with CORS middleware inside the limiter. The symptom is
  clients retrying instantly in a tight loop, or browsers reporting a CORS error instead of the 429.

## When to use
Put per-caller limits on every endpoint that triggers paid or scarce work: chat, embeddings, document
ingestion, agent runs. Use slowapi when you want quick, declarative request-rate limits inside a
FastAPI app with Redis-backed counters and tier-based strings. Add a token or cost budget alongside it
for LLM routes. Reach for gateway limits for anonymous abuse and volumetric attacks, and for
concurrency limits when the scarce resource is in-flight work rather than rate. Measure 429 counts per
tenant, upstream provider 429s, and spend per key. A healthy setup shows your limiter rejecting before
the provider does. In interviews, expect to compare fixed window with token bucket and to explain why
RPM alone is insufficient for LLMs.

## Role lens
- **Engineer:** Wires slowapi with a Redis store and a credential-based key function, adds the token
  budget dependency, and returns 429 with `Retry-After` that the SDK honours.
- **Architect:** Sets the quota model (tiers, RPM and TPM per tenant, burst allowances), decides edge
  versus app enforcement, and keeps the sum of tenant limits within upstream quota.
- **FDE:** Tunes limits for a customer's batch backfill, for example a temporary higher tier or an
  off-peak window, so the migration doesn't trip limits or starve other tenants.

## Check yourself
**Q:** You set `@limiter.limit("100/minute")` and run 8 uvicorn workers behind an ALB. A customer
reports they can make about 800 requests per minute, while a different customer behind a corporate NAT
says they're throttled at low volume. Explain both and the fix.
**A:** Two independent bugs. First, storage is in memory, so each of the 8 worker processes counts
separately and a client spread across them gets up to 8x the limit. Pointing `storage_uri` at Redis
makes all workers share one atomic counter. Second, the key function is IP-based and either sees the
ALB's address (if proxy headers aren't trusted) or the NAT's single public IP, so many people share
one bucket. Key by API key or tenant, and if IPs are needed, put the ALB's range in uvicorn's
`--forwarded-allow-ips` (proxy headers are honoured by default, but only from trusted addresses). For LLM routes, add a tokens-per-minute budget.

## Quiz
### Q: Why can a fixed-window limit of 100/minute admit about 200 requests in two seconds?
- [ ] Redis increments are not atomic
- [x] The client can use the full allowance at the end of one window and again at the start of the next
- [ ] slowapi double-counts requests that fail
- [ ] Fixed windows reset every 30 seconds
> A fixed window counts per calendar minute, so 100 requests at 12:00:59 and 100 at 12:01:00 both
> pass. Moving or sliding windows count over the trailing interval and close this boundary burst.

### Q: A service runs four uvicorn workers with slowapi's default storage. What is the effective per-client limit?
- [ ] Exactly the configured limit
- [ ] A quarter of the configured limit
- [x] Up to four times the configured limit, since each process keeps its own counters
- [ ] Unlimited, because in-memory storage is disabled with multiple workers
> In-memory counters live per process. Requests spread across workers each see a fresh counter, so the
> limit multiplies. Shared Redis storage is needed for correct limits across workers and replicas.

### Q: Why is a request-per-minute limit alone weak protection for an LLM endpoint?
- [ ] Providers do not rate-limit by tokens
- [ ] Request counting is too slow to compute
- [x] Request cost varies by orders of magnitude with prompt and output length, so RPM does not bound spend or upstream TPM
- [ ] slowapi cannot return 429 for POST requests
> A long-document request can cost thousands of times a short one. Budgeting estimated and actual
> tokens per caller bounds both spend and provider quota, which a pure request count cannot do.
