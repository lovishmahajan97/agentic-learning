---
id: tenant-isolation
module: system-design
name: Tenant isolation
kind: pattern
difficulty: advanced
est_minutes: 17
related:
  - system-design/multi-tenancy
  - safety-security/cross-tenant-data-leakage
  - rag-retrieval/acl-aware-retrieval
  - rag-retrieval/metadata-filtering
  - agent-runtime-architecture/multi-tenant-state-isolation
  - agent-runtime-architecture/secrets-and-credential-isolation
  - reliability/tenant-level-quotas-and-noisy-neighbor-isolation
  - security-governance-compliance/data-residency
sources:
  - Official docs: PostgreSQL (row security policies)
  - Official docs: AWS Well-Architected SaaS Lens
  - OWASP, 2025 — Top 10 for LLM Applications
---

## TL;DR
Tenant isolation is the set of controls that make the "each tenant is alone" promise of multi-tenancy
true even when application code is wrong. It layers independent mechanisms, scoped credentials,
database-enforced row policies, partitioned indexes and caches, per-tenant quotas and tenant-tagged
telemetry, so that a single bug cannot leak data or performance across tenants. In AI systems the
prompt is a new exfiltration path: anything retrieved, remembered or cached under the wrong tenant key
is read out loud by the model.

## Why it exists
Multi-tenancy establishes a tenant identity and tells every layer to respect it. Isolation is what you
build because some layer, some day, will not. A developer adds an admin report that forgets the filter;
an ORM relationship lazy-loads across the tenant column; a background job runs with no tenant in context
and queries everything; a cache key omits the tenant; a retrieval call passes the filter as a soft
re-ranking hint instead of a hard constraint. Each is an ordinary mistake, and in a classic SaaS it
produces a wrong row on a screen. In an LLM product the wrong row becomes part of a prompt, and the model
helpfully summarises another company's contract to the user.

The naive answer, "we filter everything in the application layer and we have tests", fails because it
has a single point of failure: the discipline of every engineer on every code path forever. Reviews
catch most omissions; the leak comes from the one they miss, and it is discovered by a customer. The
second naive answer, "give every tenant their own database", is isolation by cost: correct but
unaffordable at scale, and it still leaves compute, caches, model servers, logs and agent credentials
shared.

Isolation therefore means **defence in depth**: at least two independent mechanisms between any tenant
and any other tenant's data, performance and secrets, such that the application being wrong is not
sufficient to cause a leak. It also means being able to *prove* that to a security reviewer, which is why
the mechanisms must be enforceable and testable rather than conventions.

## How it works
### The dimensions you are isolating
Isolation is not one property. A review should walk through each dimension and name the control:

1. **Data at rest and in query.** Who can read which rows, documents, embeddings and files.
2. **Performance.** One tenant's load must not become another's latency; noisy-neighbour control.
3. **Secrets and credentials.** A tenant's connector tokens, API keys and encryption keys are usable only
   in that tenant's work.
4. **Execution.** Agent tool calls and generated code run in a sandbox scoped to the tenant.
5. **Configuration and lifecycle.** Prompt versions, model choices and feature flags are per tenant, and
   a tenant can be migrated, suspended or deleted on its own.
6. **Telemetry.** Logs, traces and evaluation samples carry the tenant tag and are access-controlled by
   it, because support staff reading traces is itself a cross-tenant read.
7. **Residency.** Data and inference stay in the tenant's required region or environment.

### Layer 1: the database enforces the filter
With a pooled schema, the strongest cheap control is **row-level security**: the database attaches a
predicate to every query on a table, and the predicate reads the tenant from a per-transaction setting
that the application sets right after it verifies the caller. This exists *because* it moves the filter
from "every query" to "one policy": a forgotten `WHERE` still returns only the current tenant's rows.
Two details make or break it. The application role must not be a superuser or hold `BYPASSRLS`, and the
policy must apply to the table owner (`FORCE ROW LEVEL SECURITY`), otherwise migrations and admin
scripts silently bypass it. And the tenant setting must be transaction-local (`SET LOCAL`), because
with transaction-mode connection pooling a session-level setting leaks to whichever request gets the
connection next. Bridge and silo models get the same property structurally: a schema or database per
tenant, with per-tenant database credentials so a connection can only ever reach one tenant.

### Layer 2: retrieval and caches are partitioned, not post-filtered
Vector search is where AI products most often leak. The tenant key must be a **hard pre-filter** or a
**separate namespace**, never a re-ranking step applied after top-k, because a post-filter on a
cross-tenant top-k can return fewer results than requested, which tempts someone to widen the search,
and because the unfiltered candidates have already crossed the boundary in memory and logs. With
pgvector the row policy from Layer 1 applies before ranking; with a dedicated vector database use a
collection or namespace per tenant for larger tenants and mandatory metadata filtering for pooled ones,
and make the filter non-optional in the retrieval client's signature. Semantic caches, prompt caches and
agent memory stores must include the tenant in the key for the same reason: a cache hit is a read.

### Layer 3: credentials are scoped per tenant and per run
Agent tools that reach into a customer's systems (CRM, ticketing, email) use that tenant's stored
credentials, fetched from a secrets manager by tenant ID at run time, injected into the tool adapter for
that run only, and never placed in the prompt. Per-tenant encryption keys for stored documents mean
that even a storage-level leak yields ciphertext, and deleting a tenant becomes deleting a key.
Model-provider keys can also be split per tenant or per tier so that a leaked key has a bounded blast
radius and spend can be attributed.

### Layer 4: performance isolation
Quotas and fair scheduling at the gateway and in the job system cap what one tenant can consume:
requests per second, concurrent generations, tokens per period, ingestion jobs in flight. Dedicated
worker pools or model-serving capacity for the top tier isolate the heaviest tenants physically. The
indicator that this layer is missing is simple: a latency incident whose root cause is one tenant's
traffic.

### Layer 5: verification
Isolation that is not tested decays. Two tests earn their keep. A **cross-tenant canary**: each tenant's
corpus contains a unique marker document, and an automated probe runs representative questions as every
other tenant and fails on any marker appearing in retrieved passages or generated answers. And
**negative access tests** in CI: every data-access function is called with tenant A's context against
tenant B's identifiers and must return nothing or raise. Combined with log review for queries executed
without a tenant setting, this turns isolation into something you can show an auditor.

## Example
PostgreSQL row-level security on a pooled `documents` table that also holds embeddings. The policy uses
a transaction-local setting; if the application forgets to set it, `current_setting(..., true)` returns
NULL, the predicate is NULL, and the query returns no rows, so the failure mode is "empty", not "leak".
The ranking happens after the policy, so a forgotten filter in the vector query still cannot cross
tenants.

```sql
CREATE TABLE documents (
    id         bigserial PRIMARY KEY,
    tenant_id  uuid NOT NULL,
    body       text NOT NULL,
    embedding  vector(1536)          -- pgvector
);

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;   -- owner is not exempt (migrations, admin scripts)

CREATE POLICY tenant_rows ON documents
    USING      (tenant_id = current_setting('app.tenant_id', true)::uuid)   -- reads
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);  -- inserts/updates

-- The application connects as a role with no BYPASSRLS and no ownership of the table.
CREATE ROLE app_rw LOGIN NOBYPASSRLS;
GRANT SELECT, INSERT, UPDATE, DELETE ON documents TO app_rw;

-- Per request, inside one transaction (safe with transaction-mode pooling):
BEGIN;
SET LOCAL app.tenant_id = '2b6f1c1e-6c0a-4f7e-9d7e-1b8c3b2d9a10';
SELECT id, body
FROM   documents
ORDER  BY embedding <=> $1      -- cosine distance to the query embedding
LIMIT  8;                        -- policy is applied before ranking, so top-8 is within-tenant
COMMIT;
```

## Trade-offs
- Database-enforced policies catch application mistakes, but they add a per-query predicate and make
  "run this query across all tenants" an explicit privileged path; that is a feature until an analytics
  team needs cross-tenant reporting, which should then go through a separate replica and role.
- Namespaces or collections per tenant give clean separation and per-tenant re-indexing, but many small
  collections waste memory and connections; pooled indexes with mandatory filters win for the long tail.
- Per-tenant encryption keys make deletion and breach containment simple, but key management becomes
  part of every read path; the cost is justified when contracts require crypto-shredding or residency.
- Dedicated capacity for a tenant removes noisy-neighbour risk, but it reintroduces idle cost; it is
  worth it only when a tenant's load is both heavy and latency-sensitive, or when they pay for it.
- Canary and negative tests slow the suite slightly and need per-tenant fixtures, but they are the only
  evidence of isolation that survives a refactor.

## Pitfalls
- Enabling RLS but connecting as the table owner or a superuser; the symptom is that tests pass, and a
  manual check with the production role shows every tenant's rows.
- Using session-level `SET` behind a transaction-pooling proxy; the symptom is intermittent, unreproducible
  cases where a request sees a previous request's tenant.
- Applying the tenant filter after top-k in vector search; the symptom is queries returning fewer than
  k results for small tenants, followed by someone raising k to compensate.
- Putting customer connector tokens into the system prompt so the agent can "use them"; the symptom is a
  token appearing in a trace, an eval sample or a model response.
- Trusting the vector database's metadata filter without making it mandatory in the client; the symptom
  is one new call site that omits the filter and passes review because the code "looks like the others".
- Logging full prompts to a shared log store without tenant tags and access control; the symptom is
  support engineers reading tenant data they are not cleared for, which is itself a reportable breach.

## When to use
Every multi-tenant AI system needs at least two independent isolation layers for data and one for
performance from the first production tenant; the question is how far up the ladder each tier goes.
Pooled tenants: RLS or equivalent plus mandatory retrieval filters plus quotas. Mid tier: add per-tenant
namespaces and encryption keys. Enterprise or regulated: bridge or silo data, dedicated capacity, and
per-tenant credentials end to end. Measure it: count queries executed without a tenant setting (should be
zero), run the cross-tenant canary continuously, and track per-tenant latency so noisy neighbours are
seen as a graph rather than a ticket. In security reviews and interviews, the expected depth is naming
each layer, explaining what failure it catches that the layer above misses, and showing the test that
proves it.

## Role lens
- **Engineer:** Sets the tenant in the transaction, makes the retrieval client refuse calls without a
  tenant filter, keys every cache by tenant, and writes the negative access tests.
- **Architect:** Defines the isolation ladder per tier, the roles and policies in the database, the
  key-management model, and signs off that two independent layers exist for every data path.
- **FDE:** Walks a customer's security team through the controls with evidence (policies, role grants,
  canary results), and translates their requirements into the tier that actually meets them.

## Check yourself
**Q:** A security reviewer says, "Your API filters by tenant in every query, and you have unit tests. Why
is that not enough for our data?" Give the answer a strong architect would give, including what you
would add and how you would prove it works.
**A:** Application filtering has one point of failure: a developer forgetting it on one path, and in an
LLM product that missed row ends up in a prompt. I would add a second, independent layer that holds
even when the code is wrong: row-level security in PostgreSQL with a transaction-local tenant setting,
with the application role denied `BYPASSRLS` and the policy forced on the owner, so a missing filter
returns nothing rather than everything. Retrieval gets a mandatory tenant filter in the client signature
or a namespace per tenant, and every cache key includes the tenant. For evidence, I would show the
policy definitions and role grants, a CI suite that calls every data function as tenant A against
tenant B's IDs, and a continuously running cross-tenant canary that plants marker documents and alerts
if any marker is ever retrieved or generated for another tenant.

## Quiz
### Q: Why is `SET LOCAL app.tenant_id` preferred over a session-level `SET` when using row-level security?
- [ ] It is faster because it avoids writing to the catalog
- [x] It is scoped to the transaction, so a pooled connection cannot carry one tenant's setting into the next request
- [ ] It encrypts the setting in memory
- [ ] Session-level settings cannot be read by `current_setting`
> Transaction-mode poolers hand the same connection to different requests; a session setting would
> persist across them and make a later request run as the earlier tenant. Transaction scope removes
> that failure mode.

### Q: What is the main reason to make the tenant filter a hard pre-filter in vector search rather than filtering the top-k results afterwards?
- [ ] Pre-filtering produces better-calibrated similarity scores
- [ ] Post-filtering is not supported by any vector database
- [x] Post-filtering lets cross-tenant candidates cross the boundary and can return fewer than k results, inviting a wider search
- [ ] Pre-filtering is required for cosine distance to work
> Cross-tenant neighbours already fetched into memory and logs are a leak surface, and small tenants
> see depleted result sets after filtering, which pushes engineers to widen the query. A hard filter or a
> namespace prevents both.

### Q: Which control best limits the blast radius if one tenant's connector token is leaked through an agent trace?
- [ ] Rotating the model-provider API key
- [ ] Reducing the model's temperature
- [x] Per-tenant credentials fetched at run time and injected only into that tenant's tool adapter, never into prompts
- [ ] Enabling row-level security on the conversations table
> A leaked token is bounded to one tenant only if credentials are scoped per tenant and kept out of the
> prompt and traces. Row-level security protects rows, not secrets, and the provider key is unrelated.
