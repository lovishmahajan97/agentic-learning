---
id: api-provider-quotas-and-regional-limits
module: infra-capacity-economics
name: API provider quotas & regional limits
kind: concept
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - infra-capacity-economics/cloud-ai-architecture
  - infra-capacity-economics/capacity-planning
  - serving-inference/rate-limiting
  - serving-inference/retry-strategies
  - serving-inference/fallbacks
  - reliability/multi-region-architecture
  - reliability/circuit-breakers
  - system-design/ai-gateway
sources:
  - Official docs: OpenAI rate limits
  - Official docs: Anthropic rate limits
  - Official docs: Azure OpenAI quotas and limits
  - Official docs: Amazon Bedrock quotas
---

## TL;DR
Hosted model APIs are shared pools, so every provider caps what one account may take: requests and
tokens per minute (often input and output separately), per model, per region, per account, rising
with spend tier. Exceed them and you get 429s; when the pool itself is full you get overload errors
regardless of quota. Capacity planning on an API is therefore quota planning: measure demand in the
provider's units, keep headroom, spread across regions where residency allows, and reserve capacity
for the base load.

## Why it exists
A provider's GPU fleet is finite and shared by everyone. Without limits, one customer's traffic
spike would degrade every other customer, and the provider could not size its fleet. Rate limits
are the provider's admission control; quotas are the allocation of that control per customer. They
are set per model because models have different cost and capacity, per region because the fleet is
regional, and per account or subscription because that is the billing and abuse boundary. Tiers
that rise with spend exist because payment history is the provider's best signal that a customer is
real and predictable.

From the application side, the naive model is "the API is infinite, just call it; retry on error".
It fails in three ways. First, a 429 under sustained demand is not transient: retries add load to a
limiter that is already rejecting you, so the retry storm raises latency for every request without
increasing throughput. Second, the limit is measured in tokens, not requests, and several major
providers (OpenAI, Azure OpenAI and Bedrock among them) document that they count `max_tokens`
against the token budget at admission, so a request with `max_tokens=4096` that produces 50 tokens
still holds 4,096 tokens of budget when it is admitted (some providers credit the unused part back
when the response completes, others do not); a team that never set `max_tokens` deliberately can
find its effective capacity is a fraction of what the quota implies. The exact rule differs per
provider and has changed over time, so read the current docs for the one you use.
Third, quotas are regional, and the model the product depends on may not be available, or may have
a lower quota, in the only region the data-residency rule allows. A launch has been delayed by a
quota ticket more often than by a model's quality. Treating quota as a planned resource like GPU
memory, with measurement, headroom and a growth process, is what this topic is about.

## How it works
### The units
- **RPM** (requests per minute): the simplest cap, binding for workloads with many small requests.
- **TPM** (tokens per minute): the cap that usually binds for LLM traffic. Some providers count
  input and output together; others keep separate input-TPM and output-TPM budgets, which matters
  because long-prompt RAG traffic and long-output generation traffic exhaust different budgets.
- **TPD / RPD** (per day): present at lower tiers on some providers, a budget rather than a rate.
- **Concurrency**: some platforms cap simultaneous in-flight requests rather than, or in addition
  to, rates.
- **Per-model, per-region, per-account.** Azure OpenAI allocates TPM per model per region per
  subscription and you split it across deployments; Bedrock's on-demand quotas are per model per
  region per account and are raised through the service-quotas process; vendor APIs set limits per
  organisation per model by usage tier; Vertex has moved toward dynamically shared quota for some
  models, where the fixed per-project cap gives way to a pool shared across customers.

### How counting works
Providers implement limits as token buckets or sliding windows: the budget refills continuously, a
request is admitted if the bucket holds enough, otherwise it is rejected with 429 and usually a
`Retry-After` (or `retry-after-ms`) header saying when to try again. On providers that reserve output,
the token cost is estimated at admission from the prompt length plus `max_tokens` (or a default
when absent), *because* the provider must reserve capacity before it knows the real output length;
some then adjust the accounting when the response completes, and others count only actual output
against an output-token limit. Response headers report the remaining budget,
for example OpenAI's `x-ratelimit-remaining-tokens` family and Anthropic's
`anthropic-ratelimit-tokens-remaining` family, which a gateway can read to slow down before the
first 429 rather than after it.

### Quota versus capacity
A quota is a promise about *your* admission, not about the pool's availability. When the regional
pool is saturated, requests fail with overload or capacity errors (Anthropic's 529, Bedrock's
throttling exceptions, Azure 429s on a deployment that is under its TPM) even though you are
under quota. Provisioned or reserved capacity exists for this gap: it buys dedicated throughput that
does not compete with the pool, which is why it is priced by the hour rather than by the token.

### Regional limits
Quotas are per region, which cuts both ways. If residency rules allow, a workload can spread across
several regions or several Azure deployments and multiply its effective quota. If residency names
one region, the quota in that region is the ceiling and the plan must be built around it, including
lead time for increase requests. Model availability is also regional: the newest model may reach
some regions months after others. Cross-region inference profiles, global and data-zone deployment
types and global endpoints are the providers' tools for borrowing capacity from elsewhere; using
them is a residency decision as much as a capacity one.

### The client-side design
1. **Measure demand in the provider's units.** Peak tokens per minute in and out, peak requests
   per minute, per model, per region, from your own telemetry, with `max_tokens` included in the
   input-side estimate when the provider reserves it.
2. **Limit yourself below the quota.** A gateway-level token bucket at roughly 80–90% of each
   quota smooths bursts, so the provider never sees a storm and the first rejection happens in your
   queue, where you control priority.
3. **Queue with priority and bound the wait.** Interactive traffic ahead of batch; shed or defer
   when the queue exceeds the latency budget rather than letting everything time out.
4. **Retry correctly.** Honour `Retry-After`, add jitter, cap attempts, and open a circuit breaker
   when the failure rate shows the pool is overloaded, since retries cannot create capacity.
5. **Spread and fall back.** Weighted routing across regions, deployments, accounts or providers
   where allowed, with the fallback order chosen by residency and quality constraints.
6. **Buy reserved capacity for the base load** and use pay-as-you-go for burst, with batch APIs for
   offline work because they have separate limits and typically a lower price.
7. **Operate the quota.** Alert on remaining-budget headers and 429 rates; file increases before
   growth, not during the incident; keep a table of quota per model per region in the runbook.

## Example
A gateway-side limiter that budgets tokens the way the provider does (reserving `max_tokens`),
routes across a pool of deployments, honours `Retry-After`, and refuses to retry into an overloaded
pool. Deployment names and quotas are placeholders.

```python
import time, random, threading

class TokenBucket:
    def __init__(self, per_minute: int, share: float = 0.85):
        self.cap = per_minute * share; self.tokens = self.cap; self.t = time.time(); self.lock = threading.Lock()
    def take(self, n: int) -> bool:
        with self.lock:
            now = time.time(); self.tokens = min(self.cap, self.tokens + (now - self.t) * self.cap / 60); self.t = now
            if self.tokens >= n: self.tokens -= n; return True
            return False
    def give(self, n: int):                      # refund a reservation that was never sent
        with self.lock: self.tokens = min(self.cap, self.tokens + n)

class Deployment:
    def __init__(self, name, tpm, rpm):
        self.name, self.tpm, self.rpm, self.open_until = name, TokenBucket(tpm), TokenBucket(rpm), 0.0

pool = [Deployment("eu-west-a", tpm=300_000, rpm=1_800), Deployment("eu-west-b", tpm=150_000, rpm=900)]

def pick(need: int):
    for d in random.sample(pool, len(pool)):   # random order spreads load; budget only the one used
        if d.open_until > time.time() or not d.rpm.take(1): continue
        if d.tpm.take(need): return d
        d.rpm.give(1)                          # token budget short: return the request slot
    return None

def call(send, prompt_tokens, max_tokens, attempts=4):
    need = prompt_tokens + max_tokens          # count max_tokens, as the provider does at admission
    for attempt in range(attempts):
        d = pick(need)
        if d is None:
            time.sleep(min(2 ** attempt, 8) * random.uniform(0.5, 1.5)); continue   # wait in our queue
        status, headers, body = send(d.name)
        if status == 200: return body
        if status == 429:                      # our estimate drifted or the pool is busy: back off
            time.sleep(float(headers.get("retry-after", 2 ** attempt)) * random.uniform(1.0, 1.5))
        elif status in (503, 529):             # overloaded: trip this deployment for a while
            d.open_until = time.time() + 30
        else:
            raise RuntimeError(f"{d.name}: {status}")
    raise RuntimeError("quota exhausted: shed or defer this request")
```

The limiter admits only when both the request budget and the token budget of the chosen deployment
have room, which is the condition the provider will check, and it debits only the deployment the
request is actually sent to (debiting every candidate would silently burn the others' budgets).
Running it at 85% of quota keeps a margin for estimation drift. A production version would also
refund the unused part of `max_tokens` when the response reports its real usage, if the provider
does the same.

## Trade-offs
- Self-limiting below quota prevents 429 storms and lets you prioritise, but it leaves a slice of
  paid-for quota unused; the margin should shrink as your token estimates get better.
- Spreading across regions or deployments multiplies quota and availability, but it weakens the
  residency guarantee and splits prefix-cache and provisioned-capacity benefits across endpoints.
- Reserved capacity removes 429 and overload risk for the base load, but it is billed by the hour
  whether used or not; it pays when steady load fills most of it or an SLO forbids shed traffic.
- Setting `max_tokens` tightly frees quota and lowers reservation waste, but a value below what
  real answers need truncates output; tune it per endpoint from observed output-length percentiles.
- Batch APIs give separate limits and a lower price, but hours of turnaround; they flip from useful
  to useless when a workload is interactive or has a deadline inside the batch window.

## Pitfalls
- Retrying 429s immediately and without a cap; the symptom is latency rising across every endpoint
  while throughput stays flat, because retries are consuming the budget that fresh requests need.
- Leaving `max_tokens` at a large default; the symptom is hitting TPM at a fraction of the token
  volume the quota suggests, visible as 429s with low measured output tokens.
- Planning capacity in one region without checking model availability and quota there; the
  symptom is a launch waiting on a quota-increase ticket.
- Treating a quota as a capacity guarantee; the symptom is overload errors during a provider-wide
  peak while your dashboards show you well under quota.
- Ignoring remaining-budget headers; the symptom is learning about exhaustion from the first 429
  instead of throttling smoothly before it.
- Enabling cross-region or global routing to fix a quota problem on a residency-bound workload; the
  symptom is an audit finding rather than a runtime error.

## When to use
This is relevant to every workload on a hosted model API. Plan quotas when you choose the model and
region, not at launch: convert peak demand into the provider's units, compare with the current
allocation per model per region, and file increases with weeks of lead time. Put a self-limiter,
priority queue and correct retry logic in the gateway from day one; add spreading across regions or
deployments when residency allows and demand exceeds one region's quota; buy reserved capacity when
there is a steady base load or an SLO at peak. Measure 429 and overload rates, remaining budget,
queue wait time, and actual versus reserved output tokens. In interviews, expect questions on why
retries do not fix sustained 429s and why `max_tokens` affects throughput before it affects cost.

## Role lens
- **Engineer:** Implements token-aware limiting, priority queueing and `Retry-After` handling in the
  gateway, tunes `max_tokens` per endpoint and surfaces remaining-budget headers as metrics.
- **Architect:** Decides the region, deployment and provider topology per workload, when to buy
  reserved capacity, and how residency constraints bound the spreading options.
- **FDE:** Audits a customer's quotas per model per region before go-live, drives the increase
  requests early, and explains overload errors that occur while they are under quota.

## Check yourself
**Q:** A team on a hosted API has a quota of 400k TPM for their model in the one region their
residency rule allows. Their load test shows 429s starting at about 120k measured tokens per
minute. Explain what is probably happening and how you would raise effective capacity without
breaking the residency rule.
**A:** The provider reserves `max_tokens` at admission; if requests set `max_tokens` at 4,000 while
real answers average a few hundred tokens, each request reserves far more than it uses, so the
limiter sees the quota exhausted at a third of the measured usage. First, set `max_tokens` from
observed output-length percentiles per endpoint, which can roughly triple effective capacity on
its own. Second, add a gateway token bucket at 85% of quota with a priority queue so interactive
traffic is admitted first and batch work is deferred rather than retried. Third, move offline work
to the batch API, which has separate limits. Fourth, file a quota increase with usage evidence,
and if a peak SLO exists, buy provisioned capacity in the same region for the base load. Spreading
to other regions is off the table because of residency, so the plan is efficiency, prioritisation
and reserved capacity in-region.

## Quiz
### Q: Why does retrying immediately after a 429 fail to improve throughput under sustained demand?
- [ ] 429 responses are cached by the provider for one minute
- [x] The limiter is already rejecting at the quota, so retries consume budget that new requests need and only add latency
- [ ] Retries are billed at double the token price
- [ ] The provider blocks the account after repeated 429s
> A 429 under sustained load means the budget is exhausted; another request does not add capacity,
> it competes for the same refill. Back off with jitter, honour Retry-After, and prioritise in your
> own queue.

### Q: Why does an overly large `max_tokens` reduce effective throughput on a tokens-per-minute quota?
- [ ] Larger outputs always take longer to generate
- [ ] The provider charges for `max_tokens` whether used or not
- [x] Providers that reserve output count `max_tokens` against the budget at admission, so unused reservation still counts for that minute
- [ ] Requests with large `max_tokens` are routed to a slower pool
> Billing is on actual tokens, but a limiter that reserves before it knows the output length uses
> `max_tokens` (OpenAI, Azure OpenAI and Bedrock document this; check your provider). Setting it from real output-length percentiles recovers the wasted
> budget.

### Q: A residency-bound workload exceeds the quota available in its region. Which option is valid?
- [ ] Enable the provider's global routing so excess traffic is served elsewhere
- [ ] Spread traffic across accounts in several regions
- [x] Improve efficiency, prioritise traffic, use batch APIs for offline work, and request reserved capacity or a quota increase in that region
- [ ] Disable rate limiting in the client so requests are not delayed
> Global or multi-region routing violates residency silently. The in-region toolkit is tighter
> token reservation, prioritisation, separate batch limits, and reserved capacity or increases with
> lead time.
