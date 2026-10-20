---
id: multi-tenant-state-isolation
module: agent-runtime-architecture
name: Multi-tenant state isolation
kind: pattern
difficulty: core
est_minutes: 16
status: reviewed
related:
  - system-design/multi-tenancy
  - system-design/tenant-isolation
  - safety-security/cross-tenant-data-leakage
  - reliability/tenant-level-quotas-and-noisy-neighbor-isolation
  - agents/agent-memory
  - agents/sandboxing
  - agent-reliability/state-and-checkpoint-stores
  - data-architecture/data-retention-and-deletion-architecture
sources:
  - Official docs: PostgreSQL — Row Security Policies
  - Official docs: Qdrant — Multitenancy
---

## TL;DR
An agent runtime scatters tenant data across far more places than a CRUD app does: step logs, long-term
memory, retrieval indexes, uploaded files, code sandboxes, semantic and tool caches, traces and queues.
Cross-tenant leakage happens in whichever of those a filter was forgotten. Isolation therefore has to be
structural: tenant identity derived from authentication and carried in the run context, never from model
output, and enforced at the storage layer of every surface rather than remembered in application code.

## Why it exists
In a conventional SaaS backend, multi-tenancy is mostly a database problem: one `tenant_id` column, one
place to forget the `WHERE`. An agent runtime multiplies the surfaces. A run's conversation and tool results
sit in a step store. Long-term memory sits in a vector index where "similar" is computed across everything
unless you say otherwise. Uploaded documents sit in object storage. Generated code runs in a sandbox with a
filesystem. A semantic cache maps "questions like this" to "answers like that". Traces carry full prompts
into an observability tool with its own access model. Each of these is a different technology with a
different notion of scoping, and the leak will be in the one nobody listed.

The failure modes are also stranger than a wrong row. Vector similarity does not care about tenants: a
question from tenant B about "our refund policy" is nearest to tenant A's refund policy if A's documents are
in the same index without a filter. A semantic cache keyed only on the normalised question returns tenant
A's cached answer to tenant B. A warm sandbox reused for speed still has A's files in `/tmp`. Memory
written by an agent "for this user" is recalled for a different user if the key omits the user.

The naive alternative, "every query filters by tenant, we'll be careful", fails because care does not scale
across N stores and M engineers, and because the most dangerous tenant identifier in an agent system is
the one the model produces. If a tool accepts `tenant_id` as an argument, a prompt-injected or hallucinated
call can supply someone else's. The design has to make the wrong thing impossible, not merely discouraged.

## How it works
### Enumerate the surfaces
Isolation starts with an inventory. For an agent runtime the list is usually: (1) run and step store,
(2) long-term memory, (3) retrieval indexes, (4) file and object storage, (5) execution sandboxes,
(6) caches: semantic cache, tool-result cache, embedding cache, (7) logs and traces, (8) queues and event
streams, (9) evaluation datasets built from production traffic, and (10) in-process state: a framework agent
object with built-in memory, or a module-level conversation buffer, created once at import and reused by
every request the process serves. Every surface gets an owner and an
isolation mechanism, and the list is reviewed whenever a new store is added. This step is unglamorous and
is where most real incidents would have been prevented.

### Tenant identity comes from the edge, never from the model
The tenant (and user) is established once, at authentication, and placed in the run context the runtime
attaches to every call. Tools never accept tenant or user identifiers as model-supplied arguments; adapters
read them from the context. This is the single most important rule in the pattern, because it removes the
model, and therefore prompt injection, from the trust path entirely.

### Enforce at the storage layer
Application-level filters are a convention; storage-level enforcement is a guarantee. The mechanisms differ
per surface:

- **Relational stores**: PostgreSQL row-level security with a policy on `tenant_id` compared to a
  per-transaction setting (`set_config('app.tenant_id', ..., true)` so the value is transaction-local and
  cannot leak through a pooled connection). Use `FORCE ROW LEVEL SECURITY` so the table owner is not
  exempt, and connect as a role without `BYPASSRLS`.
- **Vector indexes**: a mandatory tenant filter or a tenant partition. Most engines support payload
  filtering during search (Qdrant filters, pgvector with RLS) or per-tenant namespaces (Pinecone
  namespaces); the filter must be applied *inside* the search, not after top-k, or small tenants get
  empty results while large ones dominate the candidates. In-search filtering needs a payload index on the
  tenant field, and engines handle very selective filters differently (some switch to exact search), so
  measure recall for your smallest tenants, not your average one.
- **Object storage**: per-tenant prefixes with identity policies that condition access on the prefix, and
  credentials scoped per request, not a bucket-wide key held by the runtime.
- **Sandboxes**: ephemeral per run (or at least per tenant), destroyed afterwards, with no shared volumes;
  warm pools are acceptable only if a pooled sandbox is never handed to a second tenant.
- **Caches**: tenant (and, for personal data, user) is part of the key, never inferred from the value.
  A semantic cache is a retrieval index and gets the same filter rules. Self-hosted inference with prefix
  caching shares KV blocks between requests whose prompts start identically; that returns no foreign
  content, but cache hits are faster than misses, which is a potential timing side channel, so deployments
  with strict isolation requirements partition or salt the prefix cache per tenant.
- **Logs and traces**: tenant as a first-class attribute, access to prompt content restricted by tenant
  in the observability tool, or prompt bodies redacted before export.

### Choose the isolation model per tier of tenant
Shared infrastructure with enforced scoping, a schema or namespace per tenant, a database or index per
tenant, and a fully separate deployment (a "cell") form a ladder of rising cost and rising assurance. Most
platforms run the shared model by default and offer the dedicated options to regulated tenants or those with
data-residency requirements. Deciding this per surface is normal: shared Postgres with RLS, but a dedicated
vector collection per tenant because the index's filtering is weaker than the database's.

### Deletion and offboarding
Tenant data must be deletable from every surface in the inventory, including vector entries, cached
answers, traces and evaluation sets derived from production. A deletion that clears the database and leaves
the memory index intact is a compliance incident waiting for a subject-access request.

### Prove it continuously
Isolation is tested, not assumed. Seed a secret marker into tenant A through the normal product path, run
tenant B's agent against queries designed to retrieve it, and assert the marker never appears in output,
memory, cache or traces. Run this in CI against every store, and again whenever a new tool or store is added.

## Example
Three surfaces, one rule: tenant comes from the authenticated context and is enforced where the data lives.
Row-level security on the step store with a transaction-local setting, a mandatory tenant filter inside the
vector search, a tenant-prefixed cache key, and the canary test that proves the whole path.

```python
import hashlib
from contextlib import contextmanager

DDL = """
alter table agent_steps enable row level security;
alter table agent_steps force row level security;   -- the table owner is not exempt
create policy tenant_rows on agent_steps            -- FOR ALL; USING also acts as WITH CHECK on writes
  using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
-- missing_ok=true plus nullif: an unset or reset ('') setting compares as NULL, so an unscoped request
-- sees zero rows and cannot insert any, instead of erroring or seeing everything: it fails closed.
"""

@contextmanager
def tenant_tx(pool, ctx):
    with pool.connection() as conn, conn.transaction():
        # is_local=True: the setting dies with the transaction, so a pooled connection cannot carry it over
        conn.execute("select set_config('app.tenant_id', %s, true)", (ctx.tenant_id,))
        yield conn                                                 # every query here is tenant-scoped by Postgres

def recall(vdb, ctx, query_vec, k=8):
    # Qdrant-style filter built from ctx, never from tool arguments; applied inside the ANN search
    return vdb.search(collection_name="memory", query_vector=query_vec, limit=k,
                      query_filter={"must": [{"key": "tenant_id", "match": {"value": ctx.tenant_id}},
                                             {"key": "user_id",   "match": {"value": ctx.user_id}}]})

def semantic_cache_key(ctx, model, normalised_prompt):
    h = hashlib.sha256(normalised_prompt.encode()).hexdigest()
    return f"semcache:{ctx.tenant_id}:{model}:{h}"                # tenant in the key, not the value

def test_no_cross_tenant_leak(run_agent, ctx_a, ctx_b, stores):
    marker = "zx-" + hashlib.sha256(b"canary-2026-10").hexdigest()[:10]
    run_agent(ctx_a, f"Remember that our internal project codename is {marker}.")
    answer = run_agent(ctx_b, "What is our internal project codename? Check memory and documents.")
    assert marker not in answer
    for store in stores:                                           # memory, cache, traces, step log
        assert not store.contains(ctx_b.tenant_id, marker)
```

Why the vector filter placement matters, with numbers: a shared memory collection holds 1,000,000 vectors,
of which tenant B owns 2,000 (0.2%). A post-filter design asks for the top 100 neighbours and then drops
foreign rows. If B's vectors were spread like everyone else's, about 0.2 of those 100 would be B's, so the
typical result is empty and B's agent "has no memory". The usual fix is to raise the candidate count, which
makes every query slower and still fails for the smallest tenants. With the filter inside the search, the
engine returns B's 8 nearest vectors directly, and no foreign payload is ever returned to application code
that might log or cache it. The canary test then checks the property end to end, through the same product
path a real user would take, across every store in the inventory.

## Trade-offs
- Shared stores with enforced scoping are cheap and operationally simple, but a single policy bug has
  platform-wide blast radius; dedicated databases or cells per tenant invert that, and become mandatory
  when regulation or residency says so.
- Per-run ephemeral sandboxes give the cleanest isolation at the cost of seconds of cold start per run;
  per-tenant warm pools recover the latency but require strict pool affinity and lifecycle discipline.
- Tenant-scoped caches lose the hit-rate benefit of shared knowledge (the same public question asked by
  many tenants is computed many times); a separate, explicitly curated global cache for non-tenant content
  gets some of it back safely.
- Namespaces or collections per tenant in a vector store give hard boundaries but hit engine limits and
  waste resources with thousands of tiny tenants; filtered shared collections scale better and lean on
  filter correctness.
- Storage-level enforcement such as RLS adds query overhead and makes some admin and analytics access
  awkward; the alternative, trusting every code path, has no overhead right up to the breach.

## Pitfalls
- A tool whose schema includes `tenant_id` or `user_id` as model-fillable arguments; the symptom is an
  injection test that reads another tenant's records by simply asking.
- Using `SET` instead of a transaction-local `set_config` with a connection pool; the symptom is
  intermittent wrong-tenant rows that disappear when you reproduce on a single connection.
- Filtering vector results by tenant after retrieving top-k; the symptom is small tenants getting empty
  or thin recall while the index "works fine" in tests with one tenant.
- Forgetting non-obvious surfaces: a semantic cache without tenant in the key, traces visible to all
  support staff, or an eval set exported from production; the symptom is a leak that bypasses every
  database control you audited.
- Instantiating an agent or memory object once at module import and serving every request with it; the
  symptom is a user seeing a fragment of someone else's conversation under concurrent load, never in tests.
- Reusing a warm sandbox across tenants; the symptom is generated code that lists files it should never
  have seen.
- Offboarding that deletes rows but not vectors, objects or cached answers; the symptom is a departed
  tenant's content surfacing in another tenant's recall months later.

## When to use
Any runtime serving more than one organisation needs the full pattern; even single-tenant deployments need
the user-level version of it for memory and files. Start by writing the surface inventory and assigning an
enforcement mechanism per surface; that document is the design. Measure the canary test pass rate on every
build, the fraction of stores with storage-level (not application-level) scoping, and deletion completeness
across surfaces during offboarding drills. In day-to-day work this appears as a context object threaded
through every adapter, RLS migrations, mandatory filter helpers that will not compile without a tenant, and
a recurring review when a new store is introduced. Interviewers will ask where tenant identity comes from,
how you isolate a vector index, and what the leak test looks like; the strong answer lists the surfaces
nobody remembers.

## Role lens
- **Engineer:** Threads the authenticated context through every store adapter, writes the RLS migrations
  and filter helpers, and keeps the cross-tenant canary test green in CI.
- **Architect:** Owns the surface inventory, the isolation model per tenant tier and per store, and the
  rule that identity never enters via model-supplied arguments.
- **FDE:** Walks the customer's security team through the inventory, chooses dedicated versus shared stores
  for their compliance regime, and runs the leak test in their environment before go-live.

## Check yourself
**Q:** A platform stores agent memory in a shared vector collection with a `tenant_id` payload. A tenant
reports that an answer mentioned a competitor's internal project name. Describe how you would find the
leak and what structural change prevents the class of bug.
**A:** I would trace the offending run and look at where the project name entered: the recall step's
filter, the semantic cache, or a tool result. The likely causes are a search call that omitted the tenant
filter or applied it after top-k, a cache keyed only on the question, or a tool that took `tenant_id` from
model-supplied arguments. The structural fix is to make tenant scoping impossible to omit: a single recall
helper that requires the authenticated context and builds the filter inside the ANN query, cache keys that
include the tenant, tool schemas with no identity arguments, and ideally storage-level enforcement (RLS on
pgvector, or a collection per tenant for memory). Then I would add the canary test to CI so the regression
is caught before a customer reports it, and run the offboarding deletion drill across every surface.

## Quiz
### Q: Why must the tenant identifier used for scoping come from the authenticated run context rather than from a tool argument?
- [ ] Tool arguments are limited in length and cannot hold UUIDs reliably
- [x] Anything the model supplies can be influenced by prompt injection or hallucination, so it cannot be trusted for access control
- [ ] Tenant identifiers in arguments increase token cost per call
- [ ] Vector databases cannot filter on values passed from the model
> Access control inputs must come from a trusted source. If the model can fill in `tenant_id`, an injected
> document can ask for another tenant's data and the filter faithfully applies the attacker's choice.

### Q: Why should a vector search apply the tenant filter inside the ANN query rather than filtering the top-k results afterwards?
- [ ] Post-filtering violates the vector database licence
- [ ] Filtering inside the query makes embeddings tenant-specific
- [x] Post-filtering can leave small tenants with few or no results because large tenants fill the candidate set
- [ ] Inside-query filters are the only way to log the tenant for audit
> Top-k is computed over the whole collection; if a tenant's vectors are a small fraction, few or none of
> them appear among the k nearest, and the post-filter discards the rest. In-query filtering keeps recall
> correct for every tenant and never returns foreign payloads to application code.

### Q: Which setting choice makes PostgreSQL row-level security safe to use behind a connection pool?
- [ ] `SET app.tenant_id` once when the connection is opened
- [x] `set_config('app.tenant_id', value, true)` so the setting is local to the current transaction
- [ ] Storing the tenant id in a session variable on the application server
- [ ] Granting the application role `BYPASSRLS` and filtering in SQL instead
> A session-level setting persists after the transaction and can be inherited by the next request that
> borrows the same pooled connection. A transaction-local setting is discarded at commit or rollback, so
> every request must set it explicitly. `BYPASSRLS` disables the control entirely.
