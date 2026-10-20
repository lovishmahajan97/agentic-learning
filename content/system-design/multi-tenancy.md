---
id: multi-tenancy
module: system-design
name: Multi-tenancy
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
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
Multi-tenancy is one running system serving many customers (tenants) while keeping each one's data,
configuration, quotas and bill separate. It exists because one deployment per customer does not scale in
cost or operations. The mechanism is a tenant identity established at the edge and carried through every
layer, plus a deliberate choice per resource of how much is shared (pool), partitioned (bridge) or
dedicated (silo). In AI systems that identity must also reach the retrieval index, the prompt, the caches
and the token meter.

## Why it exists
The alternative is a deployment per customer. For three customers it is fine. For three hundred it means
three hundred databases to migrate, three hundred model-version rollouts, three hundred sets of alerts,
and GPUs sitting idle in most of them because no single customer's traffic fills a card. Cost scales with
customer count rather than with usage, and every upgrade becomes a campaign. Single-tenant deployments
remain the right answer for a few regulated or very large customers, but they cannot be the default
shape of a product.

The naive multi-tenant answer, "add a `customer_id` column and filter in the query", fails in two ways.
First, the filter is a convention, and one forgotten `WHERE` clause returns another customer's records;
in an LLM product that record then lands in a prompt and is paraphrased back to the wrong user. Second,
a column solves data separation only. Tenants also need separate configuration (which model, which
system prompt, which features), separate performance envelopes (one tenant's bulk ingestion must not
starve another's chat), separate cost accounting (who used which tokens) and sometimes separate
jurisdictions (EU data stays in the EU). Multi-tenancy is the discipline of answering all of those with
one identity and a small number of explicit sharing decisions, rather than rediscovering each one in an
incident.

## How it works
### Tenant identity, established once and carried everywhere
Every request enters with a credential (a JWT from the identity provider, an API key, an SSO assertion).
The system resolves the tenant from that credential *at the edge*, never from a request body or a query
parameter, because anything the caller can type is something the caller can change. From then on the
tenant ID travels with the work: in a request-scoped context variable inside the process, as a header or
message attribute when work crosses into a queue, and as a field on every log line, trace span and
metric. The rule is that no layer may need to re-derive it from data, because re-derivation is where
mistakes hide.

### Three sharing models, chosen per resource
The AWS SaaS Lens vocabulary is useful because it names the options precisely:

- **Pool.** Tenants share the resource, separated by a tenant key: one PostgreSQL schema with a
  `tenant_id` column on every table, one vector index with tenant metadata, one model-serving fleet.
  Cheapest and simplest to operate; isolation depends entirely on correct filtering and quotas.
- **Bridge.** Shared infrastructure, partitioned structure: a schema or namespace per tenant in a shared
  database, a collection per tenant in the vector store. Stronger separation and per-tenant migration is
  possible, at the cost of N schemas to manage and connection-pool pressure.
- **Silo.** A dedicated resource per tenant: separate database, cluster or whole stack. Strongest
  isolation, highest cost, and the operational model drifts back toward one-deployment-per-customer.

Most real systems mix them *per resource and per tier*: pooled compute and a pooled vector index for
self-serve customers, bridge databases for mid-market, silo for the enterprise tier that pays for it.
The architecture decision is not "which model" but "which model for which resource, and what moves a
tenant between tiers".

### Tenant-aware configuration
Tenants differ in more than data. A tenant record carries the model (or model tier) it is allowed to use,
its system prompt and prompt version, enabled features and tools, rate and token quotas, retention
period, data-residency region and allowed connectors. Loading this once per request and passing it
explicitly, rather than sprinkling `if tenant == "acme"` through the code, is what keeps a hundred
tenants from becoming a hundred code paths.

### Fairness and noisy neighbours
Pooled resources need fair-share mechanisms *because* the model-serving queue and the database do not
know tenants exist. Per-tenant rate limits and concurrency caps at the gateway, per-tenant queues or
weighted scheduling for background work, and token budgets per billing period keep one tenant's batch
job from turning into everyone's latency incident.

### Metering and cost allocation
Every LLM call records input tokens, output tokens, model and tenant. This exists because token spend is
the dominant variable cost of an AI product and it is invisible without per-tenant attribution; it feeds
billing, quota enforcement and the margin analysis that decides which tier a tenant belongs in.

### What is specific to AI systems
Tenant identity has to reach places a classic SaaS never had: the retrieval filter (the vector query
must carry the tenant key, and the index must enforce it rather than relying on a post-filter), the
prompt assembly (tenant-specific instructions and no cross-tenant few-shot examples), the semantic and
prompt caches (keys include the tenant, or a cache hit becomes a leak), agent memory and tool
credentials (a tenant's connector token is used only for that tenant's runs), fine-tuned adapters (a
LoRA trained on one tenant's data is that tenant's asset), and the evaluation sets (per-tenant golden
sets, because quality differs by corpus).

## Example
A FastAPI slice that resolves the tenant from a verified token, stores it in a context variable so
deeper layers and `asyncio` tasks inherit it, scopes retrieval and configuration by it, and meters
token usage against it. Nothing downstream takes `tenant_id` from user input. One detail matters: the
dependency is `async def`. FastAPI runs a plain `def` dependency in a worker thread with a *copy* of
the context, so a `ContextVar.set` made there is silently discarded before the endpoint runs.

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

## Trade-offs
- Pooling maximises utilisation and minimises operations, but isolation rests on software correctness;
  that flips to bridge or silo when a tenant's contract or regulator demands physical separation.
- Siloing gives the strongest isolation and simplest per-tenant customisation, but cost grows with
  tenant count and upgrades become fleets; it is sustainable only for a small, high-value tier.
- Per-tenant configuration makes customisation cheap, but every knob multiplies the test matrix; it pays
  off when tenants differ in policy (model, prompt, tools) rather than in code.
- Carrying tenant identity through every layer costs discipline and a little plumbing, but it is far
  cheaper than the forensic work of finding which code path lost it after a leak.
- A shared model-serving fleet amortises GPUs across tenants, but one tenant's long-context traffic can
  dominate the KV cache; dedicated capacity becomes worth it when a tenant's load is both large and
  latency-sensitive.

## Pitfalls
- Accepting `tenant_id` from a request body or query string; the symptom is a penetration test, or a
  curious customer, reading another tenant's conversations by editing one field.
- Forgetting the tenant key on the vector search or on a semantic cache; the symptom is an answer that
  cites a document the user's organisation never uploaded.
- Letting tenant context evaporate at a process or thread boundary (queues, cron jobs, thread pools,
  a sync FastAPI dependency that sets a `ContextVar` in a worker thread); the symptom is background jobs
  or handlers that run as "no tenant" and either fail or write into the wrong partition.
- Hard-coding exceptions for a big customer in the code; the symptom is a release that breaks only for
  them, discovered in production because the test suite has no such tenant.
- Measuring cost per model but not per tenant; the symptom is a tenant on a flat plan quietly consuming
  most of the token budget with no alarm until the invoice.

## When to use
Design for multi-tenancy from the first commit of any product that will serve more than a handful of
customers, because retrofitting tenant identity into an existing schema, index and prompt pipeline is
one of the most expensive migrations there is. Choose the sharing model per resource, starting pooled
for compute and bridge or pooled for data, and write down what promotes a tenant to a dedicated tier.
Keep single-tenant deployment as an explicit product option for regulated buyers rather than an
accident. Measure per-tenant latency percentiles, error rates, token spend and quota hits; if you cannot
break a dashboard down by tenant, you cannot run the product. In interviews, expect to be asked how the
tenant ID reaches the vector store and the cache, and what happens when one tenant uploads a million
documents at once.

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
