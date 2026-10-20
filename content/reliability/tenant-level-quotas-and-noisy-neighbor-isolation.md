---
id: tenant-level-quotas-and-noisy-neighbor-isolation
module: reliability
name: Tenant-level quotas & noisy-neighbor isolation
kind: pattern
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - reliability/bulkheads
  - reliability/backpressure-and-load-shedding
  - system-design/tenant-isolation
  - serving-inference/rate-limiting
  - infra-capacity-economics/api-provider-quotas-and-regional-limits
  - infra-capacity-economics/cost-allocation-by-tenant-and-feature
  - fastapi-for-ai/concurrency-limits
  - safety-security/cross-tenant-data-leakage
sources:
  - Beyer et al., 2016 — Site Reliability Engineering (chapter on handling overload)
---

## TL;DR
In a multi-tenant AI service every tenant draws on the same scarce pools: provider tokens-per-minute,
GPU concurrency, workers, connections. A noisy neighbor is one tenant whose burst (a backfill, a looping
agent, a retry storm) fills a pool so everyone else's latency and errors rise. Tenant-level quotas cap
what each tenant may consume; isolation mechanisms (per-tenant lanes, fair queuing, separate pools)
ensure a tenant hitting its cap degrades only itself. The goal is to turn a shared failure into a local one.

## Why it exists
Take a gateway that fronts one provider key rated, illustratively, at one million tokens per minute and
shared by forty customers. Tenant A starts a nightly job that re-embeds 50,000 documents and submits
them as fast as its client can. A global rate limiter keeps the gateway under the provider's limit, so
nothing errors upstream. But the queue in front of that limiter is first-come-first-served, and it is now
20,000 deep with A's requests. Tenant B's support chat, which needs 400 tokens and a two-second answer,
waits behind all of them. B's p95 triples, B opens a ticket, and your dashboards show the provider
perfectly healthy.

The naive answers each fail for a specific reason. "Add a global rate limit" protects the upstream, not
the neighbors: it bounds the total but says nothing about how the total is divided. "Scale up" does not
help when the scarce resource is a vendor quota or a fixed GPU pool you cannot buy more of tonight, and
it rewards the tenant who misbehaves with everyone's money. "Ask tenants to behave" ignores that agents
loop, clients retry, and a tenant's bug becomes your incident.

Three properties of AI workloads make this sharper than in classic SaaS. Work per request spans three
or more orders of magnitude (a 20-token classification versus a 100k-token summary), so counting
requests measures almost nothing. Agents fan out, so one user action can become fifty calls. And the
binding resource often belongs to a vendor, so you cannot engineer your way out by adding capacity; you
can only allocate what you already have.

## How it works
### Choose the units you meter
Four quantities matter, and they fail in different ways. Tokens per minute (input plus output) track
provider limits and cost. Requests per minute catch chatty clients. Concurrent in-flight requests
protect whatever is actually saturating: provider concurrency, KV-cache memory, worker slots. Spend per
day bounds the bill. Meter tokens and concurrency at minimum, because a tenant can stay under any
request count while consuming the whole token budget with large prompts, and because latency collapse
is a function of concurrency, not of request rate.

### Enforce at admission with a token bucket per tenant
A token bucket has a capacity (the burst it tolerates) and a refill rate (the sustained allowance). Each
tenant gets its own. The twist for LLM calls is that output size is unknown until the call finishes, so
the gateway reserves the worst case (prompt tokens plus `max_tokens`), makes the call, then reconciles
by refunding the unused part. It works this way because the alternative, charging after the fact, lets a
tenant go arbitrarily far over quota inside a single minute. When the bucket cannot cover the
reservation, reject immediately with 429 and a `Retry-After` header rather than queueing: a queue that
grows past the budget just moves the noisy-neighbor problem into the queue, which is exactly the
opening example.

### Isolate the shared path with lanes and fair scheduling
Quotas alone are not isolation. Forty tenants each under quota can still sum to more than the provider
allows, and a FIFO dispatcher will still serve them in arrival order. So the dispatcher that drains to
the provider keeps one queue per tenant and services them round-robin, or weighted by tier, which is a
simple form of weighted fair queuing. A tenant with 20,000 queued requests then gets one turn per cycle,
the same as a tenant with one. Pair this with a per-tenant concurrency semaphore so a single tenant
cannot hold every in-flight slot. Fair scheduling is work-conserving: when only one tenant is active it
receives everything, which hard caps alone can never offer.

### Tier the isolation
Pooled tenants share buckets and lanes. Tenants who pay for guarantees get a dedicated pool: their own
provider key or model deployment, their own worker group, their own vector-index replica. This is the
bulkhead pattern applied per tenant, and it is the only level at which a vendor-side limit (a per-key
TPM cap) is genuinely isolated, because the vendor's limiter does not know your tenants exist.

### Degrade before you deny
When a tenant reaches its cap, the cheapest useful behavior is often not a 429 but a downgrade: route
to a smaller model, shed its batch-class traffic while keeping interactive traffic, or lower its
`max_tokens`. That requires classifying traffic (interactive versus batch, user-initiated versus
agent-initiated) at the edge so the scheduler can shed the right thing first.

### Make it observable and billable
Tag every request, span and cost record with `tenant_id`. Per-tenant queue wait, in-flight count, tokens
consumed and 429 counts are the signals that tell you whether isolation is working, and they settle the
"you throttled us" conversation with data. Quota disputes are a commercial problem, and the only defense
is a measurement both sides trust.

## Example
A single-process sketch of the admission layer: a per-tenant token bucket for tokens per minute with
reserve-then-reconcile, plus a per-tenant semaphore for concurrency. In production the bucket state
lives in Redis behind an atomic Lua script so every gateway replica sees the same level; the logic is
identical.

```python
import asyncio, time
from collections import defaultdict

class QuotaExceeded(Exception):
    def __init__(self, tenant, retry_after):
        super().__init__(f"tenant {tenant} over token quota"); self.retry_after = retry_after

class TenantLimiter:
    """Per-tenant token bucket (tokens/min) + concurrency lane. Single-process version."""
    def __init__(self, tokens_per_min, max_in_flight):
        self.rate, self.cap = tokens_per_min / 60.0, float(tokens_per_min)
        self.level = defaultdict(lambda: self.cap)           # starts full: one burst is allowed
        self.stamp = defaultdict(time.monotonic)
        self.lanes = defaultdict(lambda: asyncio.Semaphore(max_in_flight))

    def _refill(self, t):
        now = time.monotonic()
        self.level[t] = min(self.cap, self.level[t] + (now - self.stamp[t]) * self.rate)
        self.stamp[t] = now

    def reserve(self, t, est_tokens):
        self._refill(t)
        if self.level[t] < est_tokens:                       # reject now; queueing would hide overload
            raise QuotaExceeded(t, retry_after=(est_tokens - self.level[t]) / self.rate)
        self.level[t] -= est_tokens

    def reconcile(self, t, est_tokens, used_tokens):
        self.level[t] += est_tokens - used_tokens            # refund the unused reservation

async def call_llm(limiter, tenant, prompt_tokens, max_output_tokens, call):
    est = prompt_tokens + max_output_tokens                  # worst case: output size unknown until done
    limiter.reserve(tenant, est)
    async with limiter.lanes[tenant]:                        # the tenant's own concurrency lane
        try:
            resp = await call()
        except Exception:
            limiter.reconcile(tenant, est, prompt_tokens)    # failed calls still charge the prompt
            raise
    limiter.reconcile(tenant, est, resp["usage"]["total_tokens"])
    return resp
```

Notice the ordering: the reservation happens before the lane is taken and before any provider call, so
a tenant over quota never occupies a slot. A failed call still charges the prompt tokens, so a retry
storm counts against the tenant that caused it rather than against the shared pool. The 429 carries a
`retry_after` computed from the refill rate, which gives well-behaved clients an honest wait instead of
a guess.

## Trade-offs
- Hard per-tenant caps guarantee isolation but strand capacity: if the caps sum to the provider limit,
  each tenant gets 1/N even when the others are idle. This flips toward soft caps with fair-share
  scheduling when tenants are many and bursts are uncorrelated, and back toward hard caps when you have
  sold specific capacity to specific customers.
- Dedicated pools and provider keys per tenant give the strongest isolation, including against vendor
  limits, but carry per-tenant minimum cost and operational surface. Worth it when a tenant's revenue
  or contractual commitments exceed the cost of the idle capacity.
- Metering tokens is accurate but requires reserve-then-reconcile and a tokenizer at admission; metering
  requests is nearly free but gameable with large prompts. Request counting is adequate only when a
  schema bounds prompt size.
- Rejecting at the cap pushes work back to the client's retry logic and keeps latency honest; queueing
  instead smooths bursts but hides overload and raises everyone's latency. Queue only batch traffic,
  and only with a bounded depth.

## Pitfalls
- Relying on a single global rate limit. The symptom: the provider never returns 429, yet small tenants'
  p99 spikes precisely while one tenant runs a batch job.
- Counting requests instead of tokens. The symptom: one tenant with 100k-token prompts stays inside its
  request quota while the cost report attributes most of the monthly spend to a few percent of calls.
- Keeping limiter state per replica. The symptom: effective quota equals cap times replica count and
  silently grows on scale-out; provider 429s appear right after autoscaling events.
- Attributing retries to the pool rather than the tenant. The symptom: a tenant's retry storm multiplies
  upstream load and every other tenant pays for it.
- Identifying tenants by API key alone when a customer has many end users. The symptom: the customer
  complains that "our app is slow for some users", because one of their users is starving the rest
  inside a single quota.
- Not reserving output tokens. The symptom: your bucket says the tenant is under quota while the provider
  rejects for exceeding tokens-per-minute, because long completions were never counted until they
  finished.

## When to use
Reach for this the moment more than one tenant shares a provider key, GPU pool or worker fleet, or when
a single tenant mixes workload classes (batch backfills next to interactive chat). Start small: a Redis
token bucket keyed by tenant for tokens per minute plus a per-tenant concurrency semaphore covers most
early-stage services. Add per-tenant queues with fair draining when batch jobs appear, and dedicated
pools when a contract promises capacity. Measure per-tenant queue wait, share of tokens by tenant, and
429s split by tenant and by upstream; isolation is working when one tenant's spike moves only that
tenant's lines. In interviews the prompt is usually "a customer's batch job slowed everyone down; what
do you change?", and the expected answer distinguishes bounding the total from dividing it fairly.

## Role lens
- **Engineer:** Implements the shared token bucket and per-tenant semaphore in the gateway, reserves
  worst-case tokens before each call, and tags every span and cost record with the tenant.
- **Architect:** Owns the tiering model: which units are quota'd, how quotas map to pricing plans and to
  vendor limits, and which tenants justify dedicated keys, deployments or worker pools.
- **FDE:** Inside a customer's deployment the tenants are departments or business units; the FDE
  negotiates their quotas, explains the 429s, and builds the per-team usage dashboard that ends disputes.

## Check yourself
**Q:** A shared gateway fronts one provider key with an illustrative limit of one million tokens per
minute for forty tenants. Tenant A starts an embedding backfill and tenant B's chat latency triples, but
the global rate limiter never fires. Explain why, and design the fix.
**A:** The global limiter bounds total upstream load, not its division; the FIFO queue ahead of it is
dominated by A, so B waits behind thousands of A's requests while the provider stays healthy. The fix
has three layers. First, per-tenant admission: a token bucket per tenant (say an illustrative 50k tokens
per minute default) with worst-case reservation and a concurrency semaphore, so A is rejected with 429
and `Retry-After` once over its share. Second, isolation on the shared path: one queue per tenant drained
round-robin, so even under quota A gets one turn per cycle. Third, classify A's traffic as batch so it
is shed first and only consumes leftover capacity, and move premium tenants to a dedicated key. Verify
with per-tenant queue-wait metrics: after the change, A's wait rises and B's is flat.

## Quiz
### Q: Why does a single global rate limit fail to prevent noisy-neighbor problems?
- [ ] Global limits only count requests, never tokens
- [x] It bounds total upstream load but says nothing about how that load is divided among tenants
- [ ] Rate limiters cannot run in a distributed gateway
- [ ] Providers ignore client-side limits and apply their own
> A global limit protects the dependency, so the provider looks healthy, but the queue in front of it is
> still served in arrival order; one tenant's burst fills it and everyone else waits behind. Isolation
> needs per-tenant admission and fair scheduling, not a tighter total.

### Q: Why should an LLM gateway meter tokens rather than only requests per minute?
- [ ] Token counts are easier to compute than request counts
- [ ] Providers do not enforce request-rate limits
- [x] Cost and provider limits scale with tokens, so one large-prompt tenant can exhaust the budget while under its request quota
- [ ] Request counting cannot be stored in Redis
> Work per request varies by orders of magnitude; a tenant sending 100k-token prompts at a modest request
> rate consumes the tokens-per-minute budget that other tenants need. Requests are a useful secondary
> signal, not the primary unit.

### Q: Which mechanism keeps isolation work-conserving, so idle capacity is not stranded?
- [ ] Hard per-tenant caps that sum to exactly the provider limit
- [ ] Per-replica limiters with no shared state
- [x] Fair-share scheduling across per-tenant queues with soft caps
- [ ] A dedicated provider key for every tenant
> Round-robin or weighted draining of per-tenant queues gives each active tenant an equal turn while a
> lone active tenant receives the whole pool. Hard 1/N caps leave capacity unused when others are idle,
> and per-replica limiters break isolation on scale-out.
