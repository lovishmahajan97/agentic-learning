---
id: tenant-isolation
module: system-design
name: Tenant isolation
kind: pattern
difficulty: advanced
est_minutes: 18
status: crisp
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
Tenant isolation keeps tenants apart even when application code is wrong, by layering independent
controls (scoped credentials, row policies, partitioned indexes and caches, quotas, tagged telemetry).
In AI systems the prompt is an exfiltration path: whatever is retrieved under the wrong key is read out.

## Why it exists
- **Some layer, some day, will not respect the tenant.** Multi-tenancy tells every layer to; isolation is
  what you build for when one does not. Typical misses: an admin report without the filter, an ORM
  relationship lazy-loading across the tenant column, a background job with no tenant in context, a cache
  key without the tenant, a retrieval filter passed as a soft re-ranking hint.
- **In an LLM product a wrong row is read aloud.** In classic SaaS each mistake shows a wrong row on a
  screen. Here the row becomes part of a prompt, and the model helpfully summarises another company's
  contract to the user.
- **"We filter in the app and have tests" is a single point of failure:** the discipline of every
  engineer on every code path forever. Reviews catch most omissions; the leak comes from the one they
  miss, and a customer discovers it.
- **"A database per tenant" is isolation by cost.** Correct but unaffordable at scale, and compute,
  caches, model servers, logs and agent credentials stay shared anyway.
- **So: defence in depth you can prove.** At least two independent mechanisms between any tenant and any
  other tenant's data, performance and secrets, so the app being wrong is not enough to leak. They must be
  enforceable and testable, *because* you must show a security reviewer, not assert.

## How it works
### The dimensions you are isolating
Isolation is not one property. A review walks each dimension and names its control:

| # | Dimension | What must hold |
|---|---|---|
| 1 | **Data at rest and in query** | Who can read which rows, documents, embeddings and files |
| 2 | **Performance** | One tenant's load must not become another's latency (noisy-neighbour control) |
| 3 | **Secrets and credentials** | Connector tokens, API keys and encryption keys usable only in that tenant's work |
| 4 | **Execution** | Agent tool calls and generated code run in a sandbox scoped to the tenant |
| 5 | **Configuration and lifecycle** | Prompt versions, models and flags per tenant; a tenant can be migrated, suspended or deleted alone |
| 6 | **Telemetry** | Logs, traces and eval samples carry the tenant tag and are access-controlled by it, because support staff reading traces is itself a cross-tenant read |
| 7 | **Residency** | Data and inference stay in the tenant's required region or environment |

### Layer 1: the database enforces the filter
**Row-level security (RLS)** is the strongest cheap control for a pooled schema:
1. The database attaches a predicate to every query on the table.
2. The predicate reads the tenant from a per-transaction setting.
3. The application sets it right after verifying the caller.

*Why:* the filter moves from "every query" to "one policy", so a forgotten `WHERE` still returns only the
current tenant's rows.

**Two details make or break it:**
- **No bypass.** The application role must not be a superuser or hold `BYPASSRLS`, and the policy must
  apply to the table owner (`FORCE ROW LEVEL SECURITY`); otherwise migrations and admin scripts silently
  bypass it.
- **Transaction-local setting.** Use `SET LOCAL`, *because* with transaction-mode connection pooling a
  session-level setting leaks to whichever request gets the connection next.

**Bridge and silo get the same property structurally:** a schema or database per tenant, with per-tenant
database credentials so a connection can only ever reach one tenant.

**What RLS does and does not defend against:**
- **Catches the forgotten filter**, because the tenant is set once after authentication and every later
  query in the transaction inherits it.
- **Does not stop a compromised application.** SQL injection, or any path that can issue `SET LOCAL`, can
  name another tenant, because the setting is just a string the database trusts. Per-tenant credentials
  (bridge or silo) address that threat, which is why RLS is one layer, not the whole answer.
- **Index for it.** The policy adds `tenant_id = …` to every query, so composite indexes should lead with
  `tenant_id`, or large tables degrade into scans that the policy then filters.

### Layer 2: retrieval and caches are partitioned, not post-filtered
Vector search is where AI products most often leak. The tenant key must be a **hard pre-filter** or a
**separate namespace**, never a re-ranking step after top-k, *because*:
- a post-filter on a cross-tenant top-k can return fewer results than requested, tempting someone to widen
  the search;
- the unfiltered candidates have already crossed the boundary in memory and logs.

| Store | How isolation holds | Caveat |
|---|---|---|
| **pgvector** | The Layer 1 row policy runs inside the database, so no other tenant's row reaches the app | With an approximate index (HNSW, IVFFlat) the engine filters the index's candidate list: isolation holds but small tenants can get fewer than k rows. Iterative index scans (pgvector 0.8 and later) mitigate the recall loss; a partition or partial index per large tenant is the structural fix |
| **Dedicated vector database** | Collection or namespace per tenant for larger tenants; mandatory metadata filtering for pooled ones | Make the filter non-optional in the retrieval client's signature |
| **Semantic caches, prompt caches, agent memory** | The tenant is part of every key | A cache hit is a read |

### Layer 3: credentials are scoped per tenant and per run
- **Connector credentials.** Agent tools reaching a customer's CRM, ticketing or email use that tenant's
  credentials: fetched from a secrets manager by tenant ID at run time, injected into the tool adapter for
  that run only, never placed in the prompt.
- **Per-tenant encryption keys** for stored documents: a storage-level leak yields ciphertext, and
  deleting a tenant becomes deleting a key.
- **Model-provider keys split per tenant or tier**, so a leaked key has a bounded blast radius and spend
  can be attributed.

### Layer 4: performance isolation
- **Quotas and fair scheduling** at the gateway and in the job system cap requests per second, concurrent
  generations, tokens per period and ingestion jobs in flight.
- **Dedicated worker pools or model-serving capacity** for the top tier isolate the heaviest tenants
  physically.
- **The missing-layer indicator:** a latency incident whose root cause is one tenant's traffic.

### Layer 5: verification
Isolation that is not tested decays. Two tests earn their keep:
1. **Cross-tenant canary.** Each tenant's corpus holds a unique marker document. An automated probe runs
   representative questions as every other tenant and fails on any marker in retrieved passages or
   generated answers.
2. **Negative access tests in CI.** Every data-access function is called with tenant A's context against
   tenant B's identifiers and must return nothing or raise.

Add log review for queries executed without a tenant setting, and isolation becomes something you can
show an auditor.

## Diagram
A bug in application code still meets independent layers before any data crosses tenants; the canary and
negative tests continuously check that those layers hold.

```mermaid
flowchart TD
    A["Verified request for tenant A"] --> B["App code, filter may be missing"]
    B --> C["Postgres RLS with SET LOCAL"]
    B --> D["Retrieval client: mandatory tenant filter"]
    B --> E["Cache and memory keys include tenant"]
    B --> F["Secrets fetched by tenant for this run"]
    C --> G[("Only tenant A rows")]
    D --> G
    E --> G
    H["Canary probe and negative CI tests"] -.->|"verify"| C
    H -.->|"verify"| D
    H -.->|"verify"| E
```

## Examples
### PostgreSQL row-level security on a pooled table with embeddings
- **Transaction-local setting.** If the app forgets to set it, `current_setting(..., true)` returns NULL
  on a fresh connection but an empty string on a connection that used the setting before (the
  placeholder persists for the session). The policy wraps it in `NULLIF`, so either way the predicate is
  NULL and the query returns no rows.
- **Fails closed.** The failure mode is "empty", not "leak".
- **Vector queries inherit it.** The policy predicate filters the scan, so a forgotten filter in the
  vector query still cannot cross tenants. With HNSW or IVFFlat the filter may leave fewer than k rows:
  a recall problem, not an isolation problem.

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
    USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)   -- reads
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);  -- inserts/updates

-- The application connects as a role with no BYPASSRLS and no ownership of the table.
CREATE ROLE app_rw LOGIN NOBYPASSRLS;
GRANT SELECT, INSERT, UPDATE, DELETE ON documents TO app_rw;

-- Per request, inside one transaction (safe with transaction-mode pooling):
BEGIN;
SET LOCAL app.tenant_id = '2b6f1c1e-6c0a-4f7e-9d7e-1b8c3b2d9a10';
SELECT id, body
FROM   documents
ORDER  BY embedding <=> $1      -- cosine distance to the query embedding
LIMIT  8;                        -- policy filters the scan, so the 8 rows are within-tenant
COMMIT;
-- Same SELECT with no SET LOCAL: the predicate is NULL, so 0 rows come back (fails closed).
-- INSERT naming another tenant_id: ERROR: new row violates row-level security policy for table "documents"
```

### Runnable: a cross-tenant canary probe
Plant one unique marker per tenant, ask as every other tenant, and fail on any marker that appears. The
toy store shows the probe catching a search that drops the tenant pre-filter.

```python
import uuid

class ToyStore:
    def __init__(self):
        self.rows = []                                   # (tenant_id, text)
    def add(self, tenant_id, text):
        self.rows.append((tenant_id, text))
    def search(self, tenant_id, query, k=8, enforce=True):
        words = query.lower().split()
        return [text for tid, text in self.rows
                if (not enforce or tid == tenant_id)     # the hard pre-filter
                and any(w in text.lower() for w in words)][:k]

def plant_canaries(store, tenants):
    markers = {t: f"CANARY-{uuid.uuid4().hex[:12]}" for t in tenants}
    for t, m in markers.items():
        store.add(t, f"internal codename is {m}")
    return markers

def find_leaks(search, tenants, markers, questions):
    leaks = []
    for asker in tenants:
        for q in questions:
            seen = " ".join(search(asker, q))            # in production, also scan the answer
            leaks += [(asker, owner) for owner, m in markers.items()
                      if owner != asker and m in seen]
    return leaks

tenants = ["acme", "globex", "initech"]
store = ToyStore()
markers = plant_canaries(store, tenants)
qs = ["what is our internal codename"]
print("enforced:", find_leaks(store.search, tenants, markers, qs))
unfiltered = lambda t, q: store.search(t, q, enforce=False)
print("unfiltered:", len(find_leaks(unfiltered, tenants, markers, qs)), "leaks")
# enforced: []
# unfiltered: 6 leaks   (3 askers x 2 other tenants)
```

### Worked incident: session-level SET behind a transaction pooler
| Step | What happened |
|---|---|
| Change | A helper switched from `SET LOCAL` to a session-level `SET` "to save a round trip" |
| Pooling | The pooler runs in transaction mode, so request 2 got request 1's connection |
| Symptom | Intermittent, unreproducible reports of a user seeing another tenant's document titles |
| Why tests missed it | Tests used one connection per test, so the setting never outlived a request |
| Fix | Back to `SET LOCAL` inside the request transaction, plus a check that fails a query run with no tenant setting |

## Production patterns
- **Mandatory tenant argument in the retrieval client.** Use for every vector store. Make the tenant a
  required keyword-only parameter of `search()` and have the client add the filter or pick the namespace
  itself, so no call site can omit it. Monitor the number of code paths that call the store's SDK
  directly instead of the client.
- **Fail-closed connection checkout.** Use with RLS and a transaction pooler. Wrap transaction start so
  it always issues `SET LOCAL app.tenant_id` from the request context and raises if the context has no
  tenant. Monitor counts of raised "no tenant" errors per job type; they find background jobs that lost
  context.
- **Continuous cross-tenant canary.** Use for every pooled corpus. Plant a marker document per tenant (or
  per tier for the long tail), run the probe on a schedule and after every retrieval or prompt change,
  and page on any hit. Monitor probe coverage and time since last successful run.
- **Separate path for cross-tenant analytics.** Use when reporting needs all tenants. Give analytics a
  read replica and a dedicated role, and create reporting views with `security_invoker` (PostgreSQL 15+)
  so they check the caller's policies. Monitor which roles hold `BYPASSRLS`; the list should be tiny.
- **Per-tenant secret paths.** Use for agent connectors. Store each tenant's tokens under a path keyed by
  tenant ID in the secrets manager, grant the runtime read access per run, and redact tokens from traces.
  Monitor secret reads whose tenant differs from the run's tenant.

## Trade-offs
- **Database-enforced policies: catch app mistakes vs explicit privileged paths.** They add a per-query
  predicate and make "run this across all tenants" a privileged path. That is a feature until analytics
  needs cross-tenant reporting, which should then use a separate replica and role.
- **Namespaces per tenant: clean separation vs overhead.** Per-tenant collections allow per-tenant
  re-indexing, but many small collections waste memory and connections; pooled indexes with mandatory
  filters win for the long tail.
- **Per-tenant encryption keys: simple deletion vs key management on every read.** Deletion and breach
  containment become simple, but key management joins every read path. Justified when contracts require
  crypto-shredding or residency.
- **Dedicated capacity: no noisy neighbour vs idle cost.** Worth it only when a tenant's load is both
  heavy and latency-sensitive, or when they pay for it.
- **Canary and negative tests: a slower suite vs durable evidence.** They need per-tenant fixtures, but
  they are the only evidence of isolation that survives a refactor.

## Pitfalls
- **Enabling RLS but connecting as the table owner or a superuser.** Symptom: tests pass, and a manual
  check with the production role shows every tenant's rows. Fix: a dedicated non-owner app role with
  `NOBYPASSRLS`, plus `FORCE ROW LEVEL SECURITY`.
- **Using session-level `SET` behind a transaction-pooling proxy.** Symptom: intermittent, unreproducible
  cases where a request sees a previous request's tenant. Fix: `SET LOCAL` inside the request transaction.
- **Applying the tenant filter after top-k in vector search.** Symptom: queries return fewer than k
  results for small tenants, followed by someone raising k to compensate. Fix: a hard pre-filter or a
  namespace per tenant.
- **Putting customer connector tokens into the system prompt** so the agent can "use them". Symptom: a
  token appears in a trace, an eval sample or a model response. Fix: inject them into the tool adapter
  for that run only.
- **Trusting the vector database's metadata filter without making it mandatory in the client.** Symptom:
  one new call site omits the filter and passes review because the code "looks like the others". Fix: a
  required tenant parameter in the retrieval client, and no direct SDK calls.
- **Reading through views or `SECURITY DEFINER` functions owned by a superuser or a `BYPASSRLS` role.**
  Symptom: a reporting view returns every tenant's rows although the base table has a policy, because a
  view checks policies as its owner unless created with `security_invoker` (PostgreSQL 15+). Fix: create
  views `WITH (security_invoker = true)` and own views and definer functions by a role without
  `BYPASSRLS`.
- **Assuming RLS hides everything.** Unique and foreign-key checks bypass row security, so "this email is
  already registered" on a globally unique column tells tenant A a value exists in tenant B. Symptom: an
  enumeration finding in a pen test. Fix: scope unique constraints to `(tenant_id, …)`.
- **Logging full prompts to a shared log store without tenant tags and access control.** Symptom: support
  engineers read tenant data they are not cleared for, which is itself a reportable breach. Fix: tag
  every log and trace with the tenant and gate access to them by it.

## When to use
- **Baseline:** every multi-tenant AI system needs at least two independent isolation layers for data and
  one for performance from the first production tenant; the question is how far up the ladder each tier
  goes.
- **Pooled tenants:** RLS or equivalent, plus mandatory retrieval filters, plus quotas.
- **Mid tier:** add per-tenant namespaces and encryption keys.
- **Enterprise or regulated:** bridge or silo data, dedicated capacity, and per-tenant credentials end to
  end.
- **Measure:** queries executed without a tenant setting (should be zero), the cross-tenant canary running
  continuously, and per-tenant latency so noisy neighbours show up as a graph rather than a ticket.
- **Interviews and security reviews:** name each layer, explain what failure it catches that the layer
  above misses, and show the test that proves it.

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

## Interview questions
### What does row-level security protect against, and what does it not?
- **Protects:** the forgotten filter. The tenant is set once per transaction after authentication, so any
  query that omits `WHERE tenant_id = …` still sees only that tenant's rows.
- **Does not protect:** a compromised application. SQL injection or any code that can run `SET LOCAL` can
  name another tenant; per-tenant credentials in a bridge or silo model address that.
- **Also bypassed by:** superusers, `BYPASSRLS` roles, unforced table owners, and unique or foreign-key
  checks.

### Small tenants say search returns only two or three results. What is going on?
- **Likely cause:** the tenant filter is applied to a shared approximate index (HNSW or IVFFlat), which
  returns a candidate list that the filter then thins.
- **Isolation is fine; recall is not.** Do not raise k across tenants as a workaround.
- **Fixes:** iterative index scans in pgvector 0.8 and later, a partition or partial index per large
  tenant, or a namespace per tenant in a dedicated vector database.

### When is a database per tenant worth it over RLS on a pooled schema?
- **Worth it** when contracts or regulators require physical separation, when a compromised app must not
  reach other tenants, or when a tenant needs its own backup and migration timing.
- **Not worth it** for the long tail: migrations run per tenant and connections and catalogs multiply,
  while RLS plus mandatory filters and canaries covers the forgotten-filter risk cheaply.

### How do you keep an agent's connector tokens from leaking across tenants?
- **Fetch per run:** read the tenant's token from a secrets manager by tenant ID when the run starts.
- **Inject into the adapter only:** the tool adapter holds it; the prompt, traces and eval samples never
  do.
- **Bound the blast radius:** scope tokens to one tenant, and split model-provider keys per tenant or tier
  so a leak is contained and spend is attributable.

### After enabling RLS, queries on a large pooled table got much slower. Why, and what do you change?
- **Cause:** the policy adds `tenant_id = …` to every query, so indexes that do not lead with `tenant_id`
  force scans that the policy then filters row by row.
- **Fix:** composite indexes that lead with `tenant_id` (for example `(tenant_id, created_at)`), and a
  partition or partial vector index for the largest tenants.
- **Verify:** compare `EXPLAIN ANALYZE` plans as the app role with the tenant set, because a superuser
  bypasses the policy and shows a misleading plan.

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
