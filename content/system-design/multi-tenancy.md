---
id: multi-tenancy
module: system-design
name: Multi-tenancy
kind: concept
difficulty: core
est_minutes: 18
status: crisp
related:
  - system-design/tenant-isolation
  - reliability/tenant-level-quotas-and-noisy-neighbor-isolation
  - infra-capacity-economics/cost-allocation-by-tenant-and-feature
  - infra-capacity-economics/shared-vs-isolated-model-serving-infrastructure
  - enterprise-integration/tenant-specific-configuration-and-feature-flags
  - agent-runtime-architecture/multi-tenant-state-isolation
  - safety-security/cross-tenant-data-leakage
  - deployment/customer-vpc-on-prem-air-gapped
sources:
  - Official docs: AWS Well-Architected SaaS Lens
  - Official docs: PostgreSQL (row security policies)
---

## TL;DR
Multi-tenancy is one running system serving many customers while keeping each one's data, configuration,
quotas and bill separate. A tenant identity set at the edge reaches every layer (index, prompt, caches,
token meter), and each resource is deliberately pooled, bridged or siloed.

## Why it exists
- **One deployment per customer does not scale.** Fine for three customers; for three hundred it means
  three hundred databases to migrate, three hundred model-version rollouts and three hundred sets of
  alerts. GPUs sit idle in most of them because no single customer's traffic fills a card.
- **Cost should track usage, not customer count.** Per-customer stacks make cost scale with customers and
  turn every upgrade into a campaign. Single-tenant deployments stay right for a few regulated or very
  large customers, but cannot be the product's default shape.
- **"Add a `customer_id` column" fails twice.** The filter is a convention, so one forgotten `WHERE`
  clause returns another customer's records; in an LLM product that record lands in a prompt and is
  paraphrased back to the wrong user.
- **A column only separates data.** Tenants also need separate configuration (model, system prompt,
  features), performance envelopes (one tenant's bulk ingestion must not starve another's chat), cost
  accounting (who used which tokens) and sometimes jurisdictions (EU data stays in the EU).
- **What the discipline buys.** All of those answered with one identity and a few explicit sharing
  decisions, rather than rediscovered one by one in incidents.

## How it works
### Tenant identity, established once and carried everywhere
1. **Credential in.** Every request carries a JWT from the identity provider, an API key or an SSO
   assertion.
2. **Resolve at the edge.** The tenant comes from that credential, never from a request body or query
   parameter, *because* anything the caller can type is something the caller can change.
3. **Carry it with the work:**
   - in a request-scoped context variable inside the process;
   - as a header or message attribute when work crosses into a queue;
   - as a field on every log line, trace span and metric.
4. **Never re-derive it.** No layer may need to work out the tenant from data, *because* re-derivation is
   where mistakes hide.

### Three sharing models, chosen per resource
The AWS SaaS Lens vocabulary names the options precisely:

| Model | What it looks like | Gains | Costs |
|---|---|---|---|
| **Pool** | Shared resource with a tenant key: one PostgreSQL schema with `tenant_id` on every table, one vector index with tenant metadata, one model-serving fleet | Cheapest, simplest to operate | Isolation rests entirely on correct filtering and quotas |
| **Bridge** | Shared infrastructure, partitioned structure: a schema or namespace per tenant in a shared database, a collection per tenant in the vector store | Stronger separation; per-tenant backup, restore and migration timing | Every migration runs N times; catalog grows with N × tables; connection pools multiply if each tenant gets its own credentials |
| **Silo** | Dedicated database, cluster or whole stack per tenant | Strongest isolation | Highest cost; operations drift back toward one deployment per customer |

- **Mix them per resource and per tier.** Pooled compute and a pooled vector index for self-serve, bridge
  databases for mid-market, silo for the enterprise tier that pays for it.
- **The real decision** is not "which model" but "which model for which resource, and what moves a
  tenant between tiers".

### The tenant directory: placement as data
- **A control-plane tenant directory** maps each tenant to its tier, region, cell, database or schema,
  index namespace and encryption-key ID.
- **The data plane looks it up (and caches it) on every request**, *because* that indirection lets you
  move a tenant without touching callers.
- **Moving a tenant from pool to silo has a fixed shape:**
  1. Provision the new placement.
  2. Copy the data.
  3. Dual-write, or freeze writes for the cut-over window.
  4. Verify row and vector counts.
  5. Flip the directory entry.
  6. Only then delete from the old placement.
- **Cells extend this at scale.** Several identical, independently deployed pooled stacks each hold a
  subset of tenants, so a bad deploy or runaway tenant hurts one cell, not everyone. The price is a
  routing layer and N fleets to deploy.

### Tenant is a boundary, not an authorisation model
- **Outer boundary only.** Inside a tenant, users still have roles and documents still have ACLs.
- **Necessary, not sufficient.** Scoping retrieval by tenant does not stop an intern and the CFO of the
  same tenant retrieving the same board minutes.
- **Pick the isolation level early** (reseller, organisation, workspace), *because* it is the key every
  store is partitioned by, and changing it later means re-keying everything.

### Lifecycle: onboarding and offboarding
- **Onboarding is one idempotent workflow** that creates the directory entry, configuration, keys and any
  dedicated resources, so a half-failed signup can simply be retried.
- **Offboarding is where audits find gaps.** Deletion must reach the primary database, vector index,
  object storage, caches, queues, logs and traces, evaluation samples, fine-tuned adapters and, on their
  retention schedule, backups.
- **Silo vs pool.** In a silo this is "drop the stack"; in a pool it is a delete keyed by tenant across
  every store, which only works if every store carries the key.
- **Crypto-shredding.** Per-tenant encryption keys turn the backup problem into deleting one key.

### Tenant-aware configuration
- **The tenant record carries:** allowed model (or model tier), system prompt and prompt version, enabled
  features and tools, rate and token quotas, retention period, data-residency region, allowed connectors.
- **Load once per request and pass explicitly**, rather than sprinkling `if tenant == "acme"` through the
  code, *because* that is what keeps a hundred tenants from becoming a hundred code paths.

### Fairness and noisy neighbours
Pooled resources need fair-share mechanisms *because* the model-serving queue and the database do not
know tenants exist:
- **Per-tenant rate limits and concurrency caps** at the gateway.
- **Per-tenant queues or weighted scheduling** for background work.
- **Token budgets per billing period**, so one tenant's batch job does not become everyone's latency
  incident.

### Metering and cost allocation
- **Record per LLM call:** input tokens, output tokens, model and tenant.
- **Why:** token spend is the dominant variable cost of an AI product and is invisible without per-tenant
  attribution.
- **What it feeds:** billing, quota enforcement, and the margin analysis that decides which tier a
  tenant belongs in.

### What is specific to AI systems
Tenant identity must reach places a classic SaaS never had:

| Place | Rule |
|---|---|
| Retrieval filter | The vector query carries the tenant key, and the index enforces it rather than relying on a post-filter |
| Prompt assembly | Tenant-specific instructions; no cross-tenant few-shot examples |
| Semantic and prompt caches | Keys include the tenant, or a cache hit becomes a leak |
| Agent memory and tool credentials | A tenant's connector token is used only for that tenant's runs |
| Fine-tuned adapters | A LoRA trained on one tenant's data is that tenant's asset |
| Evaluation sets | Per-tenant golden sets, because quality differs by corpus |

## Diagram
Follow the tenant ID from the verified credential: it is resolved once, then every store, cache, meter and
queue message receives it explicitly.

```mermaid
flowchart TD
    R["Request with JWT or API key"] --> E["Edge: verify credential"]
    E --> T["tenant_id from claims"]
    T --> D[("Tenant directory: tier, region, cell")]
    T --> C["Request context variable"]
    C --> CF["Tenant config and quota check"]
    CF --> V[("Vector search filtered by tenant")]
    V --> L["LLM call, cache key has tenant"]
    L --> M["Meter tokens per tenant"]
    C -->|"explicit field"| Q["Queue message for background work"]
```

## Examples
### FastAPI slice: tenant from the token, carried to every layer
- **What it shows:** the tenant is resolved from a verified token and stored in a context variable, so
  deeper layers and `asyncio` tasks inherit it. Retrieval, configuration and token metering are scoped by
  it; nothing downstream takes `tenant_id` from user input.
- **The detail that matters:** the dependency is `async def`. FastAPI runs a plain `def` dependency in a
  worker thread with a *copy* of the context, so a `ContextVar.set` made there is silently discarded
  before the endpoint runs.

```python
from contextvars import ContextVar
import jwt                                    # PyJWT
from fastapi import Depends, FastAPI, Header

tenant_ctx: ContextVar[str] = ContextVar("tenant_id")
app = FastAPI()

async def current_tenant(authorization: str = Header(...)) -> str:
    token = authorization.removeprefix("Bearer ")
    claims = jwt.decode(token, PUBLIC_KEY, algorithms=["RS256"], audience="assistant-api")
    tenant_id = claims["tenant_id"]           # minted by the IdP, never read from the body
    tenant_ctx.set(tenant_id)                 # async dep: same context as the endpoint and its tasks
    return tenant_id                          # (a sync `def` dep would set it in a thread's copy)

@app.post("/v1/chat")
async def chat(req: ChatRequest, tenant_id: str = Depends(current_tenant)):
    cfg = await tenant_config.load(tenant_id)              # model, prompt version, quotas, region
    await quotas.check(tenant_id, cfg)                     # fail fast before spending tokens
    passages = await vector_store.search(
        req.message, top_k=8, filter={"tenant_id": tenant_id}   # enforced in the index, not after
    )
    result = await llm.complete(
        model=cfg.model, system=cfg.system_prompt, context=passages, user=req.message,
        cache_key=f"{tenant_id}:{cfg.prompt_version}",          # cache never crosses tenants
    )
    await meter.record(tenant_id, model=cfg.model,
                       input_tokens=result.usage.input_tokens,
                       output_tokens=result.usage.output_tokens)
    return {"answer": result.text}

# Work handed to a queue must carry the tenant explicitly: context does not cross processes.
async def enqueue_ingestion(doc_id: str):
    await queue.send({"tenant_id": tenant_ctx.get(), "doc_id": doc_id})
```

### Runnable: a per-tenant fair queue for ingestion
A FIFO queue puts a bulk upload in front of everyone. Round-robin across tenants means a small tenant
waits at most one turn per active tenant, not behind 1,000 queued chunks.

```python
from collections import deque, defaultdict

class FairQueue:
    """Round-robin across tenants so one tenant's backlog cannot starve the rest."""
    def __init__(self):
        self.queues = defaultdict(deque)   # tenant_id -> pending jobs
        self.turns = deque()               # tenants with pending work, in turn order

    def put(self, tenant_id: str, job: str) -> None:
        if not self.queues[tenant_id]:
            self.turns.append(tenant_id)   # tenant joins the rotation
        self.queues[tenant_id].append(job)

    def get(self) -> tuple[str, str]:
        tenant_id = self.turns.popleft()
        job = self.queues[tenant_id].popleft()
        if self.queues[tenant_id]:
            self.turns.append(tenant_id)   # back of the line, not the front
        return tenant_id, job

q = FairQueue()
for i in range(1000):                       # enterprise bulk upload arrives first
    q.put("enterprise", f"chunk-{i}")
for t in ("small-a", "small-b", "small-c"):
    q.put(t, "chunk-0")
print([q.get()[0] for _ in range(6)])
# ['enterprise', 'small-a', 'small-b', 'small-c', 'enterprise', 'enterprise']
```

Weighted fair scheduling generalises this by giving paying tiers more turns per round.

### Worked scenario: promoting a tenant from pool to silo
An enterprise customer's contract now requires a dedicated database. The directory makes it a runbook,
not a code change.

| Step | Action | Check before moving on |
|---|---|---|
| 1 | Provision a dedicated database and index namespace | Directory still points at the pool |
| 2 | Copy rows and vectors keyed by the tenant | Copy completes without errors |
| 3 | Dual-write, or freeze writes for the cut-over window | Row and vector counts match; no writes lost |
| 4 | Flip the directory entry | Cached directory lookups expire; traffic lands on the silo |
| 5 | Delete the tenant's rows from the pool | Pool queries for that tenant return nothing |

## Production patterns
- **Row-level security as a backstop.** Use when pooled tables share a schema. Enable PostgreSQL row
  security on each table with a policy comparing `tenant_id` to a per-transaction setting the app sets
  with `SET LOCAL` from the verified tenant. The table owner bypasses policies unless row security is
  forced, and superusers or `BYPASSRLS` roles always do, so the app must not connect as either. Monitor
  queries that run without the setting.
- **Tenant on every signal.** Use from day one. Add the tenant ID as a structured log field and a trace
  span attribute on every request and job, and as a metric label while tenant count stays modest
  (thousands of label values strain most metrics backends). Monitor per-tenant p95 latency, errors and
  token spend.
- **Gateway quotas per tenant.** Use for any pooled API. Keep a token-bucket or sliding-window counter
  keyed by tenant (for example in Redis) for requests and for LLM tokens, returning 429 with a retry
  hint. Monitor 429 rate per tenant and the share of tokens used by the top tenant.
- **Two-tenant isolation tests in CI.** Use for every endpoint and background job. Seed two tenants with
  look-alike data, run each flow as tenant A, and assert nothing from tenant B appears in responses,
  caches or retrieved passages. Monitor coverage of endpoints by this suite.
- **Per-tenant data keys for deletion.** Use when offboarding must reach backups. Encrypt each tenant's
  data with its own key held in a KMS, record the key ID in the tenant directory, and delete the key at
  offboarding. Monitor time from offboarding request to verified deletion across all stores.

## Trade-offs
- **Pooling: utilisation vs software-only isolation.** It maximises utilisation and minimises operations,
  but isolation rests on software correctness. It flips to bridge or silo when a tenant's contract or
  regulator demands physical separation.
- **Siloing: strongest isolation vs linear cost.** It gives the strongest isolation and simplest
  per-tenant customisation, but cost grows with tenant count and upgrades become fleets. Sustainable only
  for a small, high-value tier.
- **Per-tenant configuration: cheap customisation vs test matrix.** Every knob multiplies the test matrix.
  It pays off when tenants differ in policy (model, prompt, tools) rather than in code.
- **Carrying tenant identity everywhere: plumbing vs forensics.** It costs discipline and a little
  plumbing, but is far cheaper than the forensic work of finding which code path lost it after a leak.
- **Shared model-serving fleet: amortised GPUs vs KV-cache contention.** One tenant's long-context
  traffic can dominate the KV cache. Dedicated capacity becomes worth it when a tenant's load is both
  large and latency-sensitive.

## Pitfalls
- **Accepting `tenant_id` from a request body or query string.** Symptom: a penetration test, or a
  curious customer, reads another tenant's conversations by editing one field. Fix: derive it from the
  verified credential at the edge.
- **Forgetting the tenant key on the vector search or a semantic cache.** Symptom: an answer cites a
  document the user's organisation never uploaded. Fix: enforce the filter in the index and put the
  tenant in every cache key.
- **Letting tenant context evaporate at a process or thread boundary.** Queues, cron jobs, thread pools,
  or a sync FastAPI dependency that sets a `ContextVar` in a worker thread. Symptom: background jobs or
  handlers run as "no tenant" and either fail or write into the wrong partition. Fix: put the tenant
  explicitly on every message and job, use `async def` for context-setting dependencies, and fail closed
  when no tenant is set.
- **Hard-coding exceptions for a big customer.** Symptom: a release breaks only for them, discovered in
  production because the test suite has no such tenant. Fix: express the difference as tenant
  configuration.
- **Offboarding by deleting database rows only.** Symptom: a departed customer's document resurfaces
  from the vector index or a cached answer, or a deletion audit finds it in traces and eval samples.
  Fix: one offboarding workflow that deletes by tenant key in every store, plus crypto-shredding for
  backups.
- **Measuring cost per model but not per tenant.** Symptom: a tenant on a flat plan quietly consumes most
  of the token budget with no alarm until the invoice. Fix: meter tokens per tenant at the call site and
  alert on spend against plan.

## When to use
- **Design for it from the first commit** of any product that will serve more than a handful of
  customers, *because* retrofitting tenant identity into an existing schema, index and prompt pipeline is
  one of the most expensive migrations there is.
- **Choose the sharing model per resource:** start pooled for compute and bridge or pooled for data, and
  write down what promotes a tenant to a dedicated tier.
- **Keep single-tenant deployment as an explicit product option** for regulated buyers, not an accident.
- **Measure:** per-tenant latency percentiles, error rates, token spend and quota hits. If you cannot
  break a dashboard down by tenant, you cannot run the product.
- **Interviews:** expect to be asked how the tenant ID reaches the vector store and the cache, and what
  happens when one tenant uploads a million documents at once.

## Role lens
- **Engineer:** Threads the tenant through request context, queue messages, retrieval filters, cache
  keys and meter calls, and writes tests that run as two tenants and assert they never see each other.
- **Architect:** Decides the pool/bridge/silo model per resource and per tier, the quota and fairness
  policy, and the promotion path from shared to dedicated capacity.
- **FDE:** Maps a customer's requirements (residency, SSO, dedicated GPUs, retention) onto the tenant
  configuration and tier model, and knows when the honest answer is a single-tenant install.

## Check yourself
**Q:** Your pooled RAG product has 400 tenants. One enterprise tenant uploads 2 million documents
overnight and complains the next morning that chat is slow for them, while three small tenants also open
latency tickets. Diagnose the mechanism and propose a fix at each layer.
**A:** The bulk ingestion flooded shared resources with no tenant-aware fairness. The embedding queue was
FIFO, so the enterprise tenant's two million chunks sat in front of everyone else's small jobs; the
embedding model server and the database write path were saturated; and the vector index was being
rebuilt or compacted under load, slowing searches for all tenants. Fixes: per-tenant queues with weighted
fair scheduling and a per-tenant ingestion concurrency cap; separate worker pools or at least priority
for interactive traffic over batch; an ingestion quota per hour in the tenant configuration with a
back-pressure response rather than silent queuing; and, for a tenant of this size, a bridge or silo tier
for the index so its compaction cannot affect others. The dashboard should show queue depth and p95
latency per tenant so this is seen before the tickets.

## Interview questions
### What are pool, bridge and silo, and why choose per resource rather than per system?
- **Definition:** pool shares a resource with a tenant key, bridge partitions structure (schema or
  collection per tenant) on shared infrastructure, silo dedicates the resource to one tenant.
- **Why per resource:** compute, data and indexes have different isolation and cost needs, so a typical
  system pools compute, bridges or pools data, and silos only the tier that pays for it.

### How does the tenant ID reach the vector store, the cache and a background job?
- **Edge:** resolved from the verified token, then set in a request-scoped context variable.
- **Stores and caches:** passed as an index-enforced filter on every vector query and included in every
  semantic or prompt cache key.
- **Jobs:** written as an explicit field on the queue message, because context variables do not cross
  process boundaries.

### A customer reports an answer citing a document they never uploaded. How do you debug it?
- **Treat it as a leak first:** find the request's trace, the retrieved passage IDs and their owning
  tenant.
- **Check the usual paths:** a vector search without the tenant filter, a cache key without the tenant,
  a background job that ran with no tenant context, or a few-shot example drawn from another tenant.
- **Then close the class of bug:** add a two-tenant test for that path and a database or index-level
  backstop.

### When would you not use a pooled database for tenant data?
- **When a contract or regulator demands physical separation**, or a tenant needs its own backup,
  restore and migration timing.
- **When one tenant is large enough to dominate** shared write paths or index compaction.
- **The cost of leaving the pool:** migrations run N times, the catalog and connection pools grow, and a
  silo tier drifts back toward per-customer operations.

### How would you allocate LLM cost to tenants on a pooled serving fleet?
- **Meter every call:** input tokens, output tokens, model and tenant, recorded at the call site.
- **Price by model:** multiply token counts by each model's per-token price to get spend per tenant per
  period.
- **Act on it:** feed billing and quotas, and compare spend with plan revenue to decide tier moves before
  a flat-plan tenant erodes margin.

## Quiz
### Q: Where should a multi-tenant API derive the tenant identity for a request?
- [ ] From a `tenant_id` field in the JSON body, validated against a list of known tenants
- [x] From the verified credential (token claims, API key record) at the edge, then propagated
- [ ] From the first row returned by the user's query
- [ ] From the subdomain, with the body value taking precedence if both exist
> The tenant must come from something the caller cannot forge. A body field is attacker-controlled even
> when validated against known tenants, because the attacker simply names another valid tenant.

### Q: A team uses a shared semantic cache keyed only by the normalised question text. What is the risk?
- [ ] Cache hit rate will be too low to justify the cache
- [ ] Embeddings will drift between tenants
- [x] One tenant can receive an answer generated from another tenant's documents
- [ ] The cache will store duplicate entries per tenant
> A cached answer embeds the context it was generated from; without the tenant in the key, a similar
> question from a different tenant returns content grounded in someone else's data.

### Q: Which statement about pool, bridge and silo is correct?
- [ ] Silo is always the right choice for data because it is most secure
- [ ] Pool must be used for every resource or the system is not multi-tenant
- [x] The model is chosen per resource and per tier, and tenants may move between tiers
- [ ] Bridge means one database per tenant on dedicated hardware
> The models describe degrees of sharing for a single resource. Real systems pool compute, bridge or
> pool data, and silo for the tier that pays for it; bridge is partitioned structure on shared
> infrastructure, not dedicated hardware.
