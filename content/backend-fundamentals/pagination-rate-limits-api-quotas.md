---
id: pagination-rate-limits-api-quotas
module: backend-fundamentals
name: Pagination, rate limits & API quotas
kind: concept
difficulty: core
est_minutes: 16
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
Three bounds every API needs, at three time scales. Pagination bounds one response, because an
unbounded list is a memory and latency bomb on both ends. Rate limits bound requests (or tokens) per
second or minute, because capacity is shared and bursts must be shed before they cascade. Quotas bound
total consumption per day or month, because calls cost money and tenants need entitlements. You design
all three as a provider and handle all three as a consumer; for LLM traffic the binding limit is
usually tokens per minute.

## Why it exists
Start with the unbounded list. `GET /documents` for a tenant with two million rows: the database
materialises every row, the server serialises hundreds of megabytes of JSON, the client tries to parse
it, and somewhere in the middle a 30-second timeout fires, so the client retries and doubles the load.
Pagination exists because response size has to be bounded by the server, not by the client's good
manners. The obvious fix, `?offset=1000&limit=50`, works until the set is large or moving: page 20,000
makes the engine produce and discard a million rows, and a row inserted while the client is on page 3
shifts every later page, so items are skipped or shown twice.

Rate limits exist because capacity is shared. A connection pool, a CPU, a model provider's
tokens-per-minute allowance: one client's burst (a retry storm, a batch script, a scraper) takes
capacity from everyone else and cascades into timeouts upstream. "Just scale up" fails on three counts:
you cannot scale a provider's limit, bursts arrive faster than autoscaling reacts, and unlimited
capacity is an unlimited bill. A limiter is also a signal. `429` plus `Retry-After` tells a well-behaved
client exactly when to come back, which turns overload into orderly queueing instead of a pile-up.

Quotas exist because a rate limit protects the system *this minute* and says nothing about a tenant
that runs at the limit continuously for a month. When every call costs money, which is true of every
LLM call, you need an entitlement: not how fast, but how much. Without one, a free-tier user can run a
batch job that costs more than their lifetime revenue, and nobody notices until the invoice.

## How it works
### Pagination: offset, keyset, cursor
**Offset** (`ORDER BY created_at DESC LIMIT 50 OFFSET 1000`) forces the engine to produce the first
1,050 rows in order and throw 1,000 away. Cost grows linearly with depth and the window drifts under
concurrent writes. It is acceptable for small, static sets where the UI needs "page 7 of 12".

**Keyset** (also called seek) pagination remembers the sort key of the last row and asks for rows after
it: `WHERE (created_at, id) < (:last_ts, :last_id) ORDER BY created_at DESC, id DESC LIMIT 50`. With an
index on `(created_at, id)` the engine seeks directly to that position, so every page costs roughly
O(log n + page size) regardless of depth, and inserts do not shift the window *because* position is
defined by a value rather than a count. The `id` tie-breaker is there because `created_at` is not
unique; without it, rows sharing a timestamp at a page boundary get skipped or repeated.

**Cursor** pagination wraps the keyset values in an opaque token (base64 JSON, optionally HMAC-signed)
and returns `{"data": [...], "next_cursor": "...", "has_more": true}`. The token is opaque *because* it
is part of your contract: if clients can read it they will build it, and you can never change the sort
or the key again. Fetch `limit + 1` rows to compute `has_more` without a count. Enforce a default and a
maximum page size on the server; clients will ask for 10,000. Omit or estimate totals, because
`COUNT(*)` over a large table is a full scan on every page. As a consumer, loop until `has_more` is
false and treat an invalid cursor as "start over". An agent tool that returns a list needs the same
discipline: one page per call, with `has_more` surfaced to the model, or one tool result eats the
context window.

### Rate limiting: algorithms, placement, signals
- **Token bucket:** a bucket of capacity *b* refilled at *r* tokens per second; each request takes one
  token (or *n* for token-metered APIs); an empty bucket means reject or wait. It allows a burst of *b*
  and a sustained rate of *r*, which is what most APIs want *because* real clients are bursty and
  shedding a five-request burst from an otherwise idle client helps nobody.
- **Leaky bucket:** requests queue and drain at a fixed rate; the output is smooth at the cost of
  queueing delay. Use it in front of a downstream that cannot absorb bursts at all.
- **Fixed window:** `INCR limit:{key}:{minute}` with a TTL. One command, but a client can send a full
  window's worth at 11:59:59 and again at 12:00:00, so the real peak is twice the nominal limit.
- **Sliding window log:** keep every request timestamp in a sorted set and count those inside the
  trailing window; exact, but memory grows with traffic. **Sliding window counter:** weight the
  previous window's count by its overlap with the trailing window; approximate, cheap, usually enough.

Distributed counters must be atomic: `INCR` followed by `EXPIRE` as two commands leaves a key without
a TTL if the process dies in between, so use a Lua script or `SET NX EX` plus `INCR`. Placement is a
trade: a gateway (nginx, Envoy, a cloud API gateway) handles coarse per-IP or per-key limits with no
business context; application-level limiting in Redis can be per tenant, per model and token-weighted.
Dimensions matter. Model providers limit requests per minute (RPM) and tokens per minute (TPM)
separately, often per model and per region, and for LLM workloads TPM binds first *because* each
request carries thousands of tokens. Some providers debit the token budget at admission using your
`max_tokens` plus an estimate of the prompt rather than the tokens actually generated, so a generous
`max_tokens` can produce 429s while real usage is low; check your provider's accounting rules.

Signal clearly: respond `429 Too Many Requests` (RFC 6585) with `Retry-After` in seconds or as an HTTP
date, and publish remaining budget in headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`,
`X-RateLimit-Reset` by convention; an IETF draft standardises `RateLimit-*` equivalents) so clients can
throttle before they hit 429. As a consumer: honour `Retry-After`, back off exponentially with jitter,
cap concurrency with a semaphore, and keep a client-side token bucket sized to the limit you know you
have so you queue locally instead of burning retries.

### Quotas: entitlement and cost
A quota caps total consumption over a long window (day, month) or a budget (dollars, tokens, jobs) per
account, tenant or feature. Rate limit answers "how fast"; quota answers "how much". Enforce it with a
counter per `(tenant, period)` in Redis or the database. For token quotas, reserve an estimate at
admission and reconcile from the response's `usage` field *because* the real cost is only known after
the call and a streaming call can fail halfway. Distinguish soft limits (alert, degrade to a cheaper
model, lower `max_tokens`, require approval) from hard limits (reject). There is no universal status
code for quota exhaustion; what matters is that the error body is machine-distinguishable from a rate
limit, since the correct client reaction differs: wait seconds versus stop until the next period or
upgrade. Finally, you consume a provider's quota and issue quotas to tenants; the sum of allocations
against one provider key must fit the provider's limit, or you overbook with priorities and shed
low-priority work when the provider says 429. That mapping is capacity planning, covered in the
infra-capacity-economics and reliability modules.

## Example
A keyset-paginated endpoint with an opaque cursor, and a client-side token bucket sized to a provider's
tokens-per-minute limit. The bucket is in-process; with several workers the same logic moves into Redis.
`db` is an asyncpg pool created at startup.

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
        while True:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate); self.last = now
            if self.tokens >= n:
                self.tokens -= n; return                        # no await between check and debit: safe in asyncio
            await asyncio.sleep((n - self.tokens) / self.rate)  # sleep exactly the refill time we need

tpm_bucket = TokenBucket(rate_per_sec=200_000 / 60, capacity=200_000)   # illustrative 200k TPM limit
# before each model call: await tpm_bucket.acquire(prompt_token_estimate + max_tokens)
```

## Trade-offs
- Keyset and cursor pagination cost the same at page 1 and page 10,000 and stay stable under writes,
  but they cannot jump to page *N* or sort by an arbitrary unindexed column; offset wins when the set
  is small, static and the UI genuinely needs page numbers.
- A token bucket admits bursts, which keeps latency low for bursty but light clients, yet a burst of
  size *b* still lands on the downstream at once; when that downstream has no buffer of its own, a
  leaky bucket's smoothing is worth its queueing delay.
- Enforcing at the gateway is uniform and cheap, but the gateway cannot see token counts or tenant
  tier; application-level limiting can, at the price of a Redis round trip per request and one more
  component that must stay highly available.
- Hard quotas protect the bill and fairness between tenants, but stopping a paying customer dead at
  100% creates support load; soft limits with graceful degradation win when the customer's SLA matters
  more than the marginal cost.
- An exact sliding-window log is fair and precise, but its memory grows with traffic; the approximate
  sliding counter is cheaper and good enough unless the limit itself is contractual.

## Pitfalls
- Offset pagination on a growing table; the symptom is page latency that climbs with page number and
  users reporting duplicated or missing items while the list is being written to.
- Sorting on a non-unique column without a tie-breaker; the symptom is rows that vanish or repeat
  exactly at page boundaries when many rows share a timestamp.
- A fixed-window counter guarding a fragile downstream; the symptom is overload incidents clustered in
  the seconds around minute boundaries, at twice the nominal limit.
- Retrying 429 immediately or without jitter; the symptom is a retry storm in which the 429 rate climbs
  instead of clearing, sometimes ending with the provider disabling the key.
- Counting requests when the provider counts tokens; the symptom is 429s at a small fraction of the
  request limit because each request carries a 40k-token prompt.
- Charging quota only after the response arrives; the symptom is tenants exceeding their cap by one
  burst's worth of concurrent requests, and failed streaming calls that are never accounted for.
- Exposing a readable cursor; the symptom is integrations breaking after a release that changed the
  sort order, because clients had been hand-building cursors.

## When to use
Default to cursor pagination for any list that can grow or is ordered by time; keep offset for small
admin tables. Rate-limit every endpoint that fronts a scarce resource, per tenant or key, with a token
bucket in Redis at the application layer and coarse limits at the gateway, and put a client-side bucket
in front of every provider whose limits you know. Add quotas as soon as a call has a cost, which for LLM
products is day one. Measure the 429 rate per tenant, time spent waiting in client limiters, quota
utilisation, and page latency by depth. Interviewers ask "design a rate limiter" constantly; the
expected answer compares token bucket with fixed and sliding windows, handles atomicity in Redis,
chooses response headers, and separates rate from quota.

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
instead of whole documents, prefix caching where the provider supports it), and ask for a quota
increase or spread load across regions or providers before launch. If the provider offers an
asynchronous batch endpoint with separate limits, route offline jobs there. Then watch the 429 rate and
queue wait time.

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
