---
id: pagination-rate-limits-api-quotas
module: backend-fundamentals
name: Pagination, rate limits & API quotas
kind: concept
difficulty: core
est_minutes: 16
status: crisp
related:
  - backend-fundamentals/rest-graphql-api-design
  - backend-fundamentals/api-and-sdk-integration
  - backend-fundamentals/redis
  - backend-fundamentals/sql
  - fastapi-for-ai/rate-limiting
  - serving-inference/retry-strategies
  - infra-capacity-economics/api-provider-quotas-and-regional-limits
  - reliability/tenant-level-quotas-and-noisy-neighbor-isolation
sources:
  - IETF RFC 6585 — Additional HTTP Status Codes
  - IETF RFC 9110 — HTTP Semantics
  - Winand, 2012 — SQL Performance Explained
  - Official docs: Stripe API
---

## TL;DR
Every API needs three bounds at three time scales: pagination bounds one response, rate limits bound
requests or tokens per minute, quotas bound total spend per period. Providers design them, consumers
handle them; for LLM traffic, tokens per minute usually binds.

## Why it exists
- **The unbounded list is a bomb.** `GET /documents` for a tenant with two million rows: the database
  materialises every row, the server serialises hundreds of megabytes of JSON, a 30-second timeout fires
  mid-parse, and the client retries and doubles the load. Response size must be bounded by the server,
  not by the client's good manners.
- **The obvious fix, `?offset=1000&limit=50`, degrades.** Page 20,000 makes the engine produce and discard
  a million rows, and a row inserted while the client is on page 3 shifts every later page, so items are
  skipped or shown twice.
- **Capacity is shared.** A connection pool, a CPU, a provider's tokens-per-minute allowance: one burst (a
  retry storm, a batch script, a scraper) takes capacity from everyone and cascades into timeouts.
  "Just scale up" fails: you cannot scale a provider's limit, bursts beat autoscaling, and unlimited
  capacity is an unlimited bill.
- **A limiter is also a signal.** `429` plus `Retry-After` tells a well-behaved client exactly when to
  come back, which turns overload into orderly queueing instead of a pile-up.
- **Rate limits say nothing about a month.** They protect the system *this minute*, not against a tenant
  running at the limit for a month. When every call costs money, as every LLM call does, you need an
  entitlement (how much, not how fast), or a free-tier batch job outspends its lifetime revenue unnoticed.

## How it works
### Pagination: offset, keyset, cursor
| Style | Query shape | Cost per page | Under concurrent writes | Use for |
|---|---|---|---|---|
| Offset | `ORDER BY created_at DESC LIMIT 50 OFFSET 1000` | produces 1,050 rows and throws 1,000 away; grows linearly with depth | window drifts | small, static sets needing "page 7 of 12" |
| Keyset (seek) | `WHERE (created_at, id) < (:last_ts, :last_id) ORDER BY created_at DESC, id DESC LIMIT 50` | roughly O(log n + page size), any depth | stable | large or moving lists |
| Cursor | keyset values in an opaque token | same as keyset | stable | public APIs and agent tools |

- **Keyset needs an index on `(created_at, id)`** so the engine seeks directly to the position. Inserts do
  not shift the window *because* position is defined by a value rather than a count.
- **The `id` tie-breaker is mandatory** because `created_at` is not unique; without it, rows sharing a
  timestamp at a page boundary get skipped or repeated.

### Cursor rules
1. **Wrap the keyset values in an opaque token** (base64 JSON, optionally HMAC-signed) and return
   `{"data": [...], "next_cursor": "...", "has_more": true}`.
2. **Keep it opaque** *because* it is part of your contract: if clients can read it they will build it,
   and you can never change the sort or the key again.
3. **Bind the cursor to its query.** Include the sort and a hash of the filters, or sign it, so reuse
   with different filters gets a clear 400 rather than a silently wrong page.
4. **Fetch `limit + 1` rows** to compute `has_more` without a count.
5. **Enforce a default and a maximum page size** on the server; clients will ask for 10,000.
6. **Omit or estimate totals**, because `COUNT(*)` over a large table is a full scan on every page.

- **As a consumer:** loop until `has_more` is false, and treat an invalid cursor as "start over".
- **Agent tools need the same discipline:** one page per call with `has_more` surfaced to the model, or one
  tool result eats the context window.

### Rate limiting algorithms
- **Token bucket:** capacity *b* refilled at *r* tokens per second; each request takes one token (or *n*
  for token-metered APIs); empty means reject or wait. It allows a burst of *b* and a sustained rate of
  *r*, what most APIs want *because* real clients are bursty and shedding a five-request burst from an
  idle client helps nobody.
- **Leaky bucket:** requests queue and drain at a fixed rate; smooth output at the cost of queueing delay.
  Use it in front of a downstream that cannot absorb bursts at all.
- **Fixed window:** `INCR limit:{key}:{minute}` with a TTL. One command, but a client can send a full
  window's worth at 11:59:59 and again at 12:00:00, so the real peak is twice the nominal limit.
- **Sliding window log:** every request timestamp in a sorted set, counting those in the trailing window;
  exact, but memory grows with traffic.
- **Sliding window counter:** weight the previous window's count by its overlap with the trailing window;
  approximate, cheap, usually enough.
- **Concurrency limit:** cap requests *in flight*. By Little's law, in-flight = arrival rate × duration, so
  10 requests per second of 30-second streaming completions is about 300 open streams, each holding a
  connection and a model-server slot. LLM gateways usually enforce both a rate and a max-in-flight per
  tenant.

### Token bucket trace
Take *b* = 10 and *r* = 2 per second; an idle client fires 15 requests at t = 0.

| Time | Tokens before | Requests | Outcome |
|---|---|---|---|
| t = 0 | 10 | 15 | first 10 pass and drain the bucket; next 5 are rejected (or wait) |
| t = 1 s | 2 | 2 | 2 more pass |
| any interval *T* | | | at most `b + r·T` admitted: burst *b*, long-run rate *r* |

### Distributed enforcement and placement
- **Counters must be atomic.** `INCR` followed by `EXPIRE` as two commands leaves a key without a TTL if
  the process dies in between, so use a Lua script or `SET NX EX` plus `INCR`.
- **Gateway** (nginx, Envoy, a cloud API gateway): coarse per-IP or per-key limits, no business context.
- **Application level in Redis:** per tenant, per model and token-weighted.
- **Provider dimensions.** Model providers limit requests per minute (RPM) and tokens per minute (TPM)
  separately, often per model and per region. For LLM workloads TPM binds first *because* each request
  carries thousands of tokens.
- **Admission accounting.** Some providers debit the token budget at admission using your `max_tokens`
  plus a prompt estimate, not tokens actually generated, so a generous `max_tokens` can produce 429s
  while real usage is low. Check your provider's accounting rules.

### Signalling limits
- **Respond `429 Too Many Requests`** (RFC 6585) with `Retry-After` in seconds or as an HTTP date.
- **Publish remaining budget** so clients throttle before they hit 429: `X-RateLimit-Limit`,
  `X-RateLimit-Remaining`, `X-RateLimit-Reset` by convention.
- **The standard is still a draft.** An IETF httpapi draft, still evolving, standardises `RateLimit` and
  `RateLimit-Policy` fields; earlier drafts used `RateLimit-Limit`/`-Remaining`/`-Reset`.
- **As a consumer:** honour `Retry-After`, back off exponentially with jitter, cap concurrency with a
  semaphore, and keep a client-side token bucket sized to your known limit, so you queue locally instead
  of burning retries.

### Quotas: entitlement and cost
- **Definition.** A cap on total consumption over a long window (day, month) or a budget (dollars, tokens,
  jobs) per account, tenant or feature. Rate limit answers "how fast"; quota answers "how much".
- **Enforcement.** A counter per `(tenant, period)` in Redis or the database.
- **Reserve, then reconcile.** For token quotas, reserve an estimate at admission and reconcile from the
  response's `usage` field, *because* real cost is known only after the call and a stream can fail
  halfway.
- **Soft vs hard.** Soft limits alert, degrade to a cheaper model, lower `max_tokens` or require approval;
  hard limits reject.
- **Distinguishable errors.** No universal status code exists for quota exhaustion. The error body must be
  machine-distinguishable from a rate limit, because the client reaction differs: wait seconds versus
  stop until the next period or upgrade.
- **Both directions.** You consume a provider's quota and issue quotas to tenants; allocations against one
  provider key must fit its limit, or you overbook with priorities and shed low-priority work on a 429.
  That mapping is capacity planning, covered in the infra-capacity-economics and reliability modules.

## Diagram
A request passes three bounds in order of time scale before any work runs, and the response itself is
bounded by a page size.

```mermaid
flowchart TD
    R["Tenant request"] --> G{"Gateway: per-key rate OK?"}
    G -->|"no"| E1["429 plus Retry-After"]
    G -->|"yes"| Q{"Tenant quota left this period?"}
    Q -->|"no"| E2["Quota error, distinct body"]
    Q -->|"yes"| C{"In-flight below cap?"}
    C -->|"no"| E1
    C -->|"yes"| B["Client-side TPM bucket"]
    B --> P["Provider call, reserve then reconcile tokens"]
    P --> L["Page of results, limit plus has_more"]
```

## Examples
### Keyset endpoint with an opaque cursor, plus a client-side TPM bucket
The bucket is in-process; with several workers the same logic moves into Redis. `db` is an asyncpg pool
created at startup.

```python
import asyncio, base64, json, time
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query

app = FastAPI()

def encode_cursor(created_at: datetime, id_: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([created_at.isoformat(), id_]).encode()).decode()

def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        ts, id_ = json.loads(base64.urlsafe_b64decode(cursor))
        return datetime.fromisoformat(ts), int(id_)
    except (ValueError, TypeError):
        raise HTTPException(400, "invalid cursor")             # opaque: clients never build or parse it

@app.get("/documents")
async def list_documents(limit: int = Query(20, ge=1, le=100), cursor: str | None = None):
    sql, args = "SELECT id, created_at, title FROM documents", []
    if cursor:
        last_ts, last_id = decode_cursor(cursor)
        sql += " WHERE (created_at, id) < ($1, $2)"             # keyset: seek to position, never skip rows
        args = [last_ts, last_id]
    sql += f" ORDER BY created_at DESC, id DESC LIMIT {limit + 1}"  # +1 row answers has_more without COUNT(*)
    rows = await db.fetch(sql, *args)                           # needs an index on (created_at, id)
    page, has_more = rows[:limit], len(rows) > limit
    next_cursor = encode_cursor(page[-1]["created_at"], page[-1]["id"]) if has_more else None
    return {"data": [dict(r) for r in page], "next_cursor": next_cursor, "has_more": has_more}

class TokenBucket:
    """Client-side limiter: queue locally instead of collecting 429s from the provider."""
    def __init__(self, rate_per_sec: float, capacity: float):
        self.rate, self.capacity = rate_per_sec, capacity
        self.tokens, self.last = capacity, time.monotonic()
    async def acquire(self, n: float) -> None:
        if n > self.capacity:
            raise ValueError("request larger than bucket capacity would wait forever")
        while True:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate); self.last = now
            if self.tokens >= n:
                self.tokens -= n; return                        # no await between check and debit: safe in asyncio
            await asyncio.sleep((n - self.tokens) / self.rate)  # sleep exactly the refill time we need

tpm_bucket = TokenBucket(rate_per_sec=200_000 / 60, capacity=200_000)   # illustrative 200k TPM limit
# before each model call: await tpm_bucket.acquire(prompt_token_estimate + max_tokens)
```

### Atomic fixed-window counter in Redis
The script runs atomically on the Redis server, so the key can never be left without its TTL. It uses
the redis-py client; `r.eval(script, numkeys, *keys_and_args)` is the standard call.

```python
import time
import redis

r = redis.Redis()
FIXED_WINDOW = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n
"""

def allow(tenant: str, limit: int = 100, window_s: int = 60) -> bool:
    key = f"rl:{tenant}:{int(time.time() // window_s)}"     # one key per tenant per window
    count = r.eval(FIXED_WINDOW, 1, key, window_s)          # INCR and EXPIRE as one atomic step
    return count <= limit                                   # caller returns 429 + Retry-After if False
```

Remember the boundary burst: this admits up to 2 × `limit` in a two-second span across a window edge,
so put a token bucket in front of anything fragile.

### Worked scenario: a 1,000-document batch against a TPM limit
Provider limit: 500 RPM and 200,000 TPM. Each request carries a ~20,000-token prompt.

| Quantity | Value |
|---|---|
| Requests per minute allowed by TPM | 200,000 / 20,000 = 10 |
| Requests per minute allowed by RPM | 500, irrelevant |
| Total tokens in the batch | 1,000 × 20,000 = 20 million |
| Best-case drain time | 20,000,000 / 200,000 = 100 minutes |
| Without a client bucket | 429s within seconds, retries make it worse |

## Production patterns
- **Client-side bucket per provider key and model.** Size it a little below the published TPM and RPM
  (for example 90%), charge prompt estimate plus `max_tokens`, and queue instead of calling. Monitor
  time waiting in the bucket and the 429 rate; both should stay low and flat.
- **Atomic Redis limiter per tenant and model.** Implement the bucket or window as a Lua script keyed by
  tenant, model and window, and decide up front whether a Redis outage fails open or closed. Monitor
  limiter latency, Redis errors and rejections per tenant.
- **Concurrency leases for streaming.** Cap in-flight streams per tenant with a counter or semaphore whose
  entries carry a TTL, so a crashed worker cannot leak slots. Monitor in-flight per tenant against the
  Little's-law estimate (rate × duration).
- **Reserve-and-reconcile token quotas.** Debit an estimate at admission, correct it from `usage` when the
  response ends, and release it on failure. Alert tenants and on-call at a soft threshold (for example
  80%) before the hard stop.
- **Bounded pages everywhere, including agent tools.** Enforce a default and maximum `limit`, return
  `next_cursor` and `has_more`, and never return totals by `COUNT(*)` on hot paths. Monitor p95 page
  latency by depth and the largest page sizes requested.

## Trade-offs
- **Keyset/cursor vs page numbers.** They cost the same at page 1 and page 10,000 and stay stable under
  writes, but cannot jump to page *N* or sort by an arbitrary unindexed column; offset wins when the set
  is small, static and the UI genuinely needs page numbers.
- **Token bucket bursts vs smoothing.** Admitting bursts keeps latency low for bursty, light clients, but a
  burst of size *b* still lands on the downstream at once; when it has no buffer, a leaky bucket's
  smoothing is worth its queueing delay.
- **Gateway vs application enforcement.** The gateway is uniform and cheap but cannot see token counts or
  tenant tier; application-level limiting can, at the price of a Redis round trip per request and one
  more component that must stay highly available.
- **Hard vs soft quotas.** Hard quotas protect the bill and fairness, but stopping a paying customer dead
  at 100% creates support load; soft limits with graceful degradation win when the SLA matters more than
  the marginal cost.
- **Exact vs approximate windows.** A sliding-window log is fair and precise but its memory grows with
  traffic; the sliding counter is cheaper and good enough unless the limit itself is contractual.

## Pitfalls
- **Offset pagination on a growing table.** Symptom: page latency climbs with page number, and users
  report duplicated or missing items while the list is being written to.
- **A non-unique sort column without a tie-breaker.** Symptom: rows vanish or repeat exactly at page
  boundaries when many rows share a timestamp.
- **A fixed-window counter guarding a fragile downstream.** Symptom: overload incidents clustered around
  minute boundaries, at twice the nominal limit.
- **Retrying 429 immediately or without jitter.** Symptom: a retry storm in which the 429 rate climbs
  instead of clearing, sometimes ending with the provider disabling the key.
- **Counting requests when the provider counts tokens.** Symptom: 429s at a small fraction of the request
  limit, because each request carries a 40k-token prompt.
- **Charging quota only after the response.** Symptom: tenants exceed their cap by one burst of concurrent
  requests, and failed streaming calls are never accounted for.
- **Rate-limiting long streams without a concurrency cap.** Symptom: the model server or connection pool
  saturates while every tenant is under its requests-per-minute limit.
- **A readable cursor.** Symptom: integrations break after a release that changed the sort order, because
  clients had been hand-building cursors.

## When to use
- **Pagination:** default to cursor pagination for any list that can grow or is ordered by time; keep
  offset for small admin tables.
- **Rate limits:** every endpoint fronting a scarce resource, per tenant or key, with a token bucket in
  Redis at the application layer and coarse limits at the gateway; put a client-side bucket in front of
  every provider whose limits you know.
- **Quotas:** as soon as a call has a cost, which for LLM products is day one.
- **Measure** the 429 rate per tenant, time waiting in client limiters, quota utilisation, and page
  latency by depth.
- **Interviews:** "design a rate limiter" is constant; compare token bucket with fixed and sliding
  windows, handle atomicity in Redis, choose response headers, and separate rate from quota.

## Role lens
- **Engineer:** Implements keyset endpoints and opaque cursors, the Redis limiter script, the client-side
  TPM bucket, and the 429 and `Retry-After` handling inside every SDK wrapper.
- **Architect:** Chooses the limit dimensions (RPM, TPM, concurrency), the tenant quota model and how it
  overbooks provider capacity, and where enforcement lives (gateway versus service).
- **FDE:** Translates a customer's expected load into provider limits, requests quota increases ahead of
  launch, and designs the "you have hit your quota" experience and dashboards their admins will see.

## Check yourself
**Q:** Your model provider allows 500 requests and 200,000 tokens per minute on the model you use. Your
agent sends prompts of roughly 20,000 tokens. A customer submits 1,000 documents at once. What happens,
and how do you redesign?
**A:** TPM binds: 200,000 / 20,000 is 10 requests per minute, so the 500 RPM figure is irrelevant and
the burst produces 429s within seconds. The batch is 20 million tokens, so 100 minutes at the very
best. Redesign: queue the batch and drain it through a client-side token bucket sized to TPM (counting
the prompt estimate plus `max_tokens`), bound concurrency, honour `Retry-After` with jittered backoff,
give each tenant a quota so one customer cannot consume the whole budget, shrink the prompt (retrieval
instead of whole documents; prompt caching cuts cost and latency, and on some providers cached tokens
also stop counting against the input-token limit), and ask for a quota
increase or spread load across regions or providers before launch. If the provider offers an
asynchronous batch endpoint with separate limits, route offline jobs there. Then watch the 429 rate and
queue wait time.

## Interview questions
### Why does offset pagination show duplicates and gaps while the list is being written to?
- **Position is a count:** `OFFSET 1000` means "skip 1,000 rows", so a row inserted above the client's
  position shifts every later page by one and an item is shown twice.
- **Deletes do the reverse:** a removed row shifts items up, and one is skipped.
- **Keyset fixes it** because position is a value (`(created_at, id)` of the last row seen), and the `id`
  tie-breaker keeps boundaries exact when timestamps collide.

### Design the rate limiter for a multi-tenant LLM gateway.
- **Dimensions:** per tenant and per model, enforcing requests per minute, tokens per minute
  (prompt estimate plus `max_tokens`) and max in-flight streams.
- **Mechanism:** a token bucket as an atomic Lua script in Redis, coarse per-IP limits at the gateway,
  and a client-side bucket in front of the provider so tenant allocations fit its limit.
- **Signals and failure:** `429` with `Retry-After` and remaining-budget headers, plus a decision on fail
  open or closed when Redis is down.

### Your service gets 429s from the provider, but the usage dashboard shows you at 30% of TPM. What do you check?
- **Admission accounting:** some providers debit `max_tokens` plus a prompt estimate at admission, so a
  generous `max_tokens` consumes budget that is never used.
- **Dimension and scope:** RPM, a per-model or per-region limit, or another service sharing the same key.
- **Fix:** right-size `max_tokens`, add a client-side bucket using the provider's accounting, and split
  keys or projects per workload.

### When do you need a concurrency limit in addition to a rate limit?
- **When requests are long:** by Little's law in-flight = rate × duration, so 10 requests per second of
  30-second streams is about 300 open streams at any moment.
- **What saturates:** connections and model-server slots, not the per-minute counter.
- **So** cap in-flight per tenant with TTL leases, and size the cap from the expected rate and duration.

### Rate limit or quota: which error should the client see, and why does it matter?
- **Rate limit:** `429` with `Retry-After`; the right reaction is to wait seconds and retry.
- **Quota:** a machine-distinguishable error body (no universal status code exists), because the right
  reaction is to stop until the next period, degrade or upgrade.
- **Mixing them up** makes clients retry into an exhausted quota, or give up on a momentary burst.

## Quiz
### Q: Why does keyset pagination stay fast at page 10,000 while offset pagination slows down?
- [ ] Keyset pages are cached in Redis after the first request
- [x] The index seek starts at the last-seen key, so each page costs about the page size rather than the offset
- [ ] Keyset queries return fewer columns, so less data is transferred
- [ ] The database compresses rows that have already been paginated
> OFFSET forces the engine to produce and discard every earlier row in sort order, so cost grows with
> depth. A keyset predicate on an indexed column lets the engine jump straight to the position, so the
> cost is proportional to the page, not the depth.

### Q: A fixed-window limiter allows 100 requests per minute. What is the most a client can push through in a two-second span that straddles the minute boundary?
- [ ] 100, because the limit is enforced per minute
- [ ] About 3, because 100 per minute is under two per second
- [x] 200: the last 100 of one window and the first 100 of the next
- [ ] Unbounded, because the counter resets to zero
> The counter is per calendar window, not per trailing minute, so a client can exhaust one window at
> its end and the next at its start. Sliding-window and token-bucket limiters remove that boundary burst.

### Q: A tenant's plan allows 60 requests per minute and 100,000 requests per month. They send a steady 30 requests per minute for three days. Which bound stops them?
- [ ] The rate limit, because 30 per minute sustained for hours exceeds 60 per minute
- [x] The monthly quota, which 30 per minute exhausts in a little over two days
- [ ] Neither, because they never exceed 60 per minute
- [ ] Both at once, on the third day
> 30 per minute is 43,200 per day, so 100,000 is used up after about 2.3 days without ever exceeding
> the per-minute rate. Rate limits bound speed; quotas bound total consumption over the period.
