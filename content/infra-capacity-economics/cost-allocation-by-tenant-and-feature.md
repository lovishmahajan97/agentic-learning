---
id: cost-allocation-by-tenant-and-feature
module: infra-capacity-economics
name: Cost allocation by tenant & feature
kind: practice
difficulty: core
est_minutes: 15
related:
  - infra-capacity-economics/finops
  - infra-capacity-economics/unit-economics-per-successful-task
  - infra-capacity-economics/cost-modeling
  - observability/token-cost-tracking
  - system-design/ai-gateway
  - reliability/tenant-level-quotas-and-noisy-neighbor-isolation
  - infra-capacity-economics/shared-vs-isolated-model-serving-infrastructure
  - observability/opentelemetry
sources:
  - Official docs: FinOps Foundation (FinOps Framework)
---

## TL;DR
Cost allocation assigns every dollar of AI spend to the tenant and feature that caused it: direct costs
(tokens per call) by tagging each request at the gateway, shared costs (GPU fleets, vector stores,
idle capacity) by an explicit driver such as GPU-seconds or queries. It exists because a provider
invoice says only how much you spent, not who or what spent it, and without that you cannot price
tiers, find unprofitable customers or know which feature to optimize.

## Why it exists
A hosted model invoice arrives as one number per model per month. A self-hosted GPU fleet costs the
same per hour whether one tenant or fifty used it. Meanwhile the product has pricing tiers, enterprise
contracts and a roadmap of features, each with a very different cost profile. A tenant who uploads
500-page contracts and runs long agent sessions can cost a hundred times more than one asking short
questions on the same plan. Without allocation, that tenant looks as profitable as everyone else until
gross margin turns negative.

The naive approaches fail predictably. Splitting spend evenly across tenants or by seat count
subsidizes heavy users with light ones and hides the problem. Allocating only the direct API cost
ignores shared infrastructure, which for self-hosted or RAG-heavy systems can be most of the spend.
Reconstructing attribution later from logs fails because the tenant and feature were never recorded at
call time, and retrofitting the tags across many services is slow. Allocation exists to answer, with
numbers that reconcile to the invoice: what does each tenant cost us, what does each feature cost, and
where is the margin?

## How it works
### 1. Tag at the point of spend
Route all model, embedding and reranker calls through one gateway or client library that requires
`tenant_id`, `feature` (or workflow) and a task or trace ID on every call, and rejects or flags calls
missing them. Record per call: model, tokens by type (fresh input, cached input, output, reasoning),
latency, and for self-hosted models the GPU-seconds or token-seconds consumed. Propagate the tags
through agents, tools and background jobs as trace attributes, *because* the call that spends money is
often several hops away from the request that caused it.

### 2. Price direct costs per call
Compute cost at write time from a versioned price table, so historical rows keep the price that applied
then. Apply the cache, batch and long-context rules your provider actually bills. Where possible, use
separate provider API keys or projects per environment or major feature so the invoice can be split at
the source as a cross-check.

### 3. Allocate shared costs with explicit drivers
Shared pools need a driver that approximates causation:
- **GPU fleet**: GPU-seconds or token-seconds per request; concurrent batching makes exact
  attribution impossible, so tokens weighted by phase (prefill vs decode) is a common approximation.
- **Vector store**: stored vectors for storage-bound costs, query count for compute-bound costs.
- **Embedding re-indexing**: to the tenant whose data was re-embedded.
- **Idle and headroom capacity**: either spread proportionally to usage, or kept as a visible
  platform-overhead line. Spreading makes tenant totals add up; keeping it separate shows the cost of
  over-provisioning instead of hiding it.

Write the driver choice down; it is a policy decision, and changing it changes everyone's numbers.

### 4. Reconcile and track the unallocated share
Each month, allocations must sum to the invoices. Track the unattributed share (untagged calls, shared
jobs) as a KPI and drive it down, because a large unattributed bucket makes every per-tenant number
suspect.

### 5. Use it: showback, chargeback, pricing
**Showback** reports cost per tenant and feature to teams and account owners; **chargeback** actually
bills internal teams or customers. Most products start with showback, then use the data to set tier
limits, overage pricing, per-tenant quotas, and to flag tenants whose cost exceeds their revenue.

## Example
A month of usage: hosted API calls priced at the gateway, plus a shared GPU fleet and vector store
billed as lump sums (PostgreSQL; numeric columns).

```sql
WITH usage AS (
  SELECT COALESCE(tenant_id, 'UNATTRIBUTED') AS tenant_id,
         COALESCE(feature,   'UNATTRIBUTED') AS feature,
         SUM(api_cost_usd)::numeric      AS direct_usd,  -- priced per call at the gateway
         SUM(gpu_seconds)::numeric       AS gpu_s,       -- self-hosted calls
         SUM(retrieval_queries)::numeric AS rq
  FROM usage_events
  WHERE ts >= DATE '2026-09-01' AND ts < DATE '2026-10-01'
  GROUP BY 1, 2
),
pools AS (
  SELECT SUM(amount_usd) FILTER (WHERE pool = 'gpu_fleet')    AS gpu_usd,
         SUM(amount_usd) FILTER (WHERE pool = 'vector_store') AS vec_usd
  FROM shared_invoices WHERE month = DATE '2026-09-01'
),
alloc AS (
  SELECT u.tenant_id, u.feature, u.direct_usd,
         COALESCE(p.gpu_usd * u.gpu_s / NULLIF(SUM(u.gpu_s) OVER (), 0), 0) AS gpu_usd,
         COALESCE(p.vec_usd * u.rq / NULLIF(SUM(u.rq) OVER (), 0), 0)       AS vector_usd
  FROM usage u CROSS JOIN pools p
)
SELECT tenant_id, feature,
       ROUND(direct_usd, 2) AS direct, ROUND(gpu_usd, 2) AS gpu, ROUND(vector_usd, 2) AS vector,
       ROUND(direct_usd + gpu_usd + vector_usd, 2) AS total
FROM alloc
ORDER BY total DESC;
```

Illustrative result: a $30,000 GPU fleet where tenant A's contract-review feature used 45% of
GPU-seconds gets $13,500 of it; added to $6,200 of direct API spend, A's total is about $19,700 against a
$12,000 monthly contract. The even-split view would have shown A as profitable. The `UNATTRIBUTED` row
should be small; if it is 15% of spend, fix tagging before trusting any tenant number.

## Trade-offs
- Fine-grained drivers (GPU-seconds by phase, per-query vector cost) are fairer, but cost engineering
  effort; coarse token-proportional splits suffice while shared costs are a small share of spend.
- Spreading idle capacity across tenants makes totals reconcile, but hides over-provisioning; keep it
  separate when the platform team needs to see and own utilization.
- Chargeback changes behavior because teams and customers see their bill, but disputes over drivers
  follow; showback is the better start until the numbers are trusted.
- Per-tenant API keys or dedicated pools make attribution exact, but lose pooling efficiency and add
  quota management; worth it for large enterprise tenants, not for a long tail of small ones.

## Pitfalls
- Tagging only the user-facing call; the symptom is a large unattributed bucket made of agent tool
  calls, background re-embeddings and retries that lost their tenant context.
- Repricing history with today's price table; visible as last quarter's per-tenant costs changing
  after a provider price update.
- Allocating shared GPU cost by request count; long-document tenants look cheap while short-query
  tenants are overcharged, and cost per tenant does not track their token volume.
- Never reconciling with invoices; per-tenant totals drift away from actual spend and finance stops
  trusting the dashboard.

## When to use
Set up tagging before the first multi-tenant launch; it is cheap at the start and painful to retrofit.
Formal allocation of shared pools becomes worthwhile once shared infrastructure is a material part of
spend or pricing decisions depend on per-tenant cost. Measure unattributed share, cost per tenant
against revenue per tenant, cost per feature per active user, and cost per successful task by tenant.
In interviews, explaining how to attribute shared GPU cost under batching, and why idle capacity is a
policy choice, shows depth beyond "add a tenant label".

## Role lens
- **Engineer:** Enforces required tenant and feature tags in the gateway or client, propagates them
  through traces and jobs, and computes per-call cost from a versioned price table.
- **Architect:** Chooses allocation drivers, decides between shared and dedicated pools per tier, and
  makes per-tenant cost an input to pricing, quotas and isolation design.
- **FDE:** Gives a customer a defensible view of what their usage costs, and spots integration patterns
  such as oversized uploads that will push them past their contract.

## Check yourself
**Q:** Your platform shares one self-hosted model fleet across 40 tenants with continuous batching. How
do you allocate the fleet cost, and what do you do with idle capacity?
**A:** Exact per-request GPU time is not observable under batching, so I would use a driver that
approximates causation: tokens per request weighted by phase, since prefill and decode cost differently,
or measured token-seconds from the engine, recorded per call with tenant and feature tags. Fleet cost is
split proportionally to that driver. Idle and headroom capacity is a policy choice: spread it
proportionally so tenant totals reconcile to the invoice, or report it as platform overhead so
utilization stays visible. I would document the choice, reconcile monthly, and track the unattributed
share.

## Quiz
### Q: Why should tenant and feature tags be attached at the gateway on every model call rather than reconstructed later?
- [ ] Providers require tenant IDs in every API request
- [x] The call that spends money is often a tool or background step several hops away, and its tenant context is lost if not propagated at call time
- [ ] Tags reduce token usage by shortening prompts
- [ ] Logs cannot store more than one identifier per request
> Agents, retries and background jobs issue calls far from the originating request. If context is not
> propagated and recorded when the call is made, the spend ends up unattributed.

### Q: Allocating a shared GPU fleet by number of requests per tenant tends to do what?
- [ ] Allocate cost accurately, since every request uses the same GPU time
- [x] Undercharge tenants with long documents and overcharge tenants with many short requests
- [ ] Overcharge tenants who use prompt caching
- [ ] Make allocations fail to sum to the invoice
> GPU time scales with tokens processed, not request count. A request driver ignores size, so heavy
> long-context tenants are subsidized by light ones. Totals still reconcile; only fairness suffers.

### Q: What is the main argument for reporting idle GPU capacity as a separate platform line rather than spreading it?
- [ ] It makes per-tenant totals sum exactly to the invoice
- [ ] Tenants are not allowed to be billed for idle capacity
- [x] It keeps the cost of over-provisioning visible to the team that controls utilization
- [ ] It removes the need for an allocation driver
> Spreading idle cost hides it inside every tenant's number. A separate line makes utilization a
> visible, owned cost; the trade-off is that tenant totals then do not add up to the invoice by themselves.
