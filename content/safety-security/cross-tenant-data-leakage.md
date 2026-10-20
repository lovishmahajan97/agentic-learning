---
id: cross-tenant-data-leakage
module: safety-security
name: Cross-tenant data leakage
kind: concept
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - system-design/tenant-isolation
  - system-design/multi-tenancy
  - rag-retrieval/acl-aware-retrieval
  - rag-retrieval/metadata-filtering
  - agent-runtime-architecture/multi-tenant-state-isolation
  - safety-security/data-leakage
  - system-design/caching-strategy-and-cache-invalidation
  - reliability/tenant-level-quotas-and-noisy-neighbor-isolation
sources:
  - Official docs: OWASP Top 10 for LLM Applications (LLM02 Sensitive Information Disclosure, LLM08 Vector and Embedding Weaknesses)
  - OpenAI, 2023 — March 20 ChatGPT outage: Here's what happened
---

## TL;DR
Cross-tenant leakage is tenant A's data appearing in tenant B's answer. It happens because an LLM
pipeline copies data into stores the database's row-level security never sees: vector indexes, semantic
and prefix caches, conversation memory, traces, sometimes the weights themselves. The fix is one
server-derived tenant identity threaded as a hard, pre-applied filter through every store and proven by
negative tests, not a prompt telling the model to use only this customer's documents.

## Why it exists
Classic SaaS has essentially one place where tenancy must hold: the data-access layer, with a
`WHERE tenant_id = ?` on every query or row-level security in the database. Teams have well-worn patterns
for it. An LLM feature copies data *out* of that layer into half a dozen new places, each usually built by
a different person with a different mental model: an embedding pipeline writes chunks into a vector
store, a cache keeps completions keyed by question, an agent keeps memory, the tracing SDK ships every
prompt and completion to an observability backend, and a fine-tuning job may bake examples into weights.

The retrieval path is the dangerous one because failure is silent. A wrong row in SQL throws a 403 or
returns nothing; a wrong chunk in a prompt gets summarised fluently and confidently, and the only signal
is a customer screenshot. The naive defence, instructing the model to use only the current customer's
documents, fails because the model cannot verify ownership, an injected document can override the
instruction, and by the time the chunk is in the context the leak has already happened: it is now in the
trace store. The second naive defence, retrieving top-k and then dropping other tenants' hits, fails
quietly too: when the index is shared, the top 10 nearest neighbours may all belong to a large tenant,
recall for the small tenant collapses to zero, and someone eventually "fixes" it by raising k or removing
the filter in one code path.

## How it works
### The leak surfaces, one by one
1. **Vector index.** A shared collection with a missing filter, or a filter applied after the ANN search.
   The correct form is a filter evaluated *inside* the search so that k means "k within this tenant".
   Engines differ: some implement filtered HNSW natively, others over-fetch and post-filter, which
   degrades under skewed tenant sizes. Know which yours does.
2. **Semantic cache.** Completions cached by embedding similarity of the question. Tenant B asks "what
   is our refund policy?", the cache finds tenant A's near-identical question and returns A's policy
   verbatim. The tenant must be part of the key, not a check after the hit.
3. **Prefix / prompt cache.** Exact-token caches do not return another tenant's content, since you need
   the identical prefix to hit, but a hit is measurably faster than a miss, so latency can reveal that
   someone else already sent that exact prefix. That is a side channel, not a content leak; whether a
   hosted provider scopes its cache per organisation is vendor-dependent and worth asking.
4. **Agent memory and state.** Memory keyed by user or session rather than tenant, recycled session
   identifiers, or "remember our API key is…" stored in a shared memory namespace.
5. **Shared fine-tuned model.** Training one model on every tenant's data turns the weights into a
   cross-tenant store with no access control; memorised examples can be regurgitated. Per-tenant
   adapters (LoRA) or RAG keep the data in something you can filter and delete.
6. **Observability.** Traces containing full prompts and completions, visible to all engineers, or to all
   customers in a shared dashboard.
7. **Tool credentials.** A connector that uses one global CRM key for every tenant; the agent is one
   injected argument away from reading another customer's records.
8. **Infrastructure bugs around the model.** The March 2023 ChatGPT incident exposed other users' chat
   titles and some billing details because of a bug in a Redis client library under concurrency. The LLM
   was not involved; the cache next to it was.

### The defence pattern
- **One tenant identity, derived once.** Resolve `tenant_id` from the verified token at the API edge and
  place it in an immutable request context. Never read it from the request body, the model's output, or a
  retrieved document, because all three are attacker-influenceable.
- **No unscoped call sites.** Wrap every store so the only available functions take the context:
  `retrieve(ctx, q)`, `cache.get(ctx, key)`, `memory.load(ctx)`. The unscoped variant should not exist,
  so forgetting the filter is a compile-time or review-time error rather than a production leak.
- **Pre-filter, not post-filter,** because post-filtering silently loses recall and invites the k
  workaround.
- **Choose the partitioning deliberately.** Collection-per-tenant gives hard isolation and simple
  deletion but costs per-tenant resources and operations; it fits a few large or regulated tenants. A
  shared collection with a mandatory filter scales to thousands of small tenants but is one bug away
  from a leak. Namespaces are the usual middle ground.
- **Tenant in every cache key.** `hash(tenant_id, model, prompt)`; a semantic cache additionally filters
  candidates by tenant before similarity.
- **Classify derived stores like their source.** Embeddings, traces and memory inherit the source data's
  classification, access control and retention.
- **Prove it with canaries.** Plant a unique token per tenant in a document, then in CI query every
  other tenant for it with a large k across retrieval, cache and memory, and assert it never appears.
  In production, sample outputs and scan for any tenant's canary in the wrong tenant's response.

## Example
A scoped store where every method requires a tenant context, the filter runs inside the vector search, the
cache key includes the tenant, and a parametrised test checks that each tenant's canary is invisible to all
others. The vector client call is written in a Qdrant-style filter syntax; adapt the dictionary to your
engine.

```python
import hashlib, pytest
from dataclasses import dataclass

@dataclass(frozen=True)
class TenantCtx:
    tenant_id: str            # set once from the verified JWT at the API edge
    user_id: str

class ScopedStore:
    """Every method takes ctx; there is no unscoped variant to call by mistake."""
    def __init__(self, vec, cache):
        self.vec, self.cache = vec, cache

    def retrieve(self, ctx: TenantCtx, qvec, k=8):
        # filter evaluated inside the ANN search, so k is k *within* the tenant
        flt = {"must": [{"key": "tenant_id", "match": {"value": ctx.tenant_id}}]}
        return self.vec.search(vector=qvec, limit=k, query_filter=flt)

    def cache_key(self, ctx: TenantCtx, model: str, prompt: str) -> str:
        return hashlib.sha256(f"{ctx.tenant_id}|{model}|{prompt}".encode()).hexdigest()

    def answer(self, ctx, model, prompt, qvec, llm):
        key = self.cache_key(ctx, model, prompt)
        if (hit := self.cache.get(key)) is not None:
            return hit
        docs = self.retrieve(ctx, qvec)
        out = llm(prompt, docs)
        self.cache.set(key, out, ttl=3600)
        return out

# CI: each tenant's canary document must be invisible to every other tenant
CANARIES = {"acme": "CANARY-acme-7f3a", "globex": "CANARY-globex-91c2", "initech": "CANARY-initech-c0de"}

@pytest.mark.parametrize("asker", CANARIES)
def test_no_cross_tenant_leak(store, embed, asker):
    ctx = TenantCtx(tenant_id=asker, user_id="qa-bot")
    for owner, token in CANARIES.items():
        if owner == asker:
            continue
        hits = store.retrieve(ctx, embed(f"what is {token}?"), k=50)   # large k on purpose
        assert not any(token in h.payload["text"] for h in hits), f"{asker} can see {owner}"
```

The test uses k=50 deliberately: a post-filter implementation that only drops foreign hits from the top 8
would still pass at k=8 by luck and fail here, which is the point.

## Trade-offs
- Collection-per-tenant buys hard isolation and trivial deletion, but each collection carries index and
  memory overhead, so beyond a few hundred tenants the shared-collection-with-filter model becomes the
  only affordable option.
- Pre-filtering inside the ANN search keeps recall correct for small tenants, but on engines that
  implement it by over-fetching it costs latency in proportion to how skewed tenant sizes are.
- Putting the tenant in the semantic cache key removes cross-tenant hits, but it also removes the
  cross-tenant hit rate that made the cache attractive; if most savings came from identical questions
  across customers, the cache may no longer pay for itself.
- Per-tenant fine-tuned adapters isolate training data cleanly, yet they multiply serving complexity;
  if tenants only differ in their documents, RAG with a filter gives isolation without any adapter.

## Pitfalls
- Reading `tenant_id` from a request header or body the client controls; the symptom is a penetration
  tester changing one header and receiving another customer's documents.
- Applying the filter in the application after retrieval; the symptom is small tenants complaining that
  search "returns nothing" while large tenants are fine, followed by someone raising k.
- Caching completions keyed on question text or embedding only; the symptom is a customer seeing a
  policy, price or name that is not theirs, often weeks after the cache was introduced.
- Shipping full prompts and completions to a shared tracing backend; the symptom is a support engineer
  being able to read every customer's conversations, which auditors treat as a breach even without
  misuse.
- Testing isolation with one happy-path query per tenant; the symptom is passing tests and a leak that
  only appears under near-duplicate questions or large k.

## When to use
Treat every multi-tenant LLM feature as needing an explicit isolation design before launch, and revisit it
whenever a new store enters the pipeline: a cache, a memory layer, a new vector engine, an observability
vendor. Measure three things: the fraction of store call sites that take a tenant context (target 100%,
enforced by a lint or wrapper), canary-test pass rate across tenant pairs in CI, and sampled production
outputs scanned for foreign canaries. In interviews this appears as "how would you isolate tenants in a
RAG system?", and the expected answer goes beyond "add a filter" to where the tenant ID comes from, pre-
versus post-filtering, caches, memory, logs and how you would prove it.

## Role lens
- **Engineer:** Writes the scoped wrappers, puts the tenant in every cache key, and adds the canary test
  matrix to CI so a missing filter fails the build rather than a customer.
- **Architect:** Decides collection-per-tenant versus shared-with-filter, how tenant identity flows from
  the edge to every store, and what classification derived stores like embeddings and traces inherit.
- **FDE:** Demonstrates isolation to a customer's security team with the canary test and explains why a
  shared index with a mandatory filter is, or is not, acceptable for their regulatory regime.

## Check yourself
**Q:** Your RAG product is moving from five enterprise tenants, each with its own vector collection, to
two thousand self-serve tenants, and an engineer proposes one shared collection with a `tenant_id`
filter. What breaks, what would you require before approving, and what would you measure afterwards?

**A:** Nothing breaks logically, but the failure mode changes: with separate collections a missing filter
returns nothing, with a shared collection it returns someone else's data. Before approving I would require
that the filter is applied inside the ANN query and benchmarked for recall on the smallest tenants, that
the tenant ID comes only from the verified token via an immutable context, that the vector client is
wrapped so no unscoped search method is reachable, that the semantic cache and any memory store include
the tenant in their keys, and that a canary-per-tenant test runs in CI across tenant pairs with a large
k. I would also confirm deletion: removing a tenant must be a filtered delete that is verified, since there
is no collection to drop. Afterwards I would track canary hits in sampled production outputs, recall for
small tenants versus the old setup, and latency, since filtered search on a skewed index can get slower
than per-tenant collections were.

## Quiz
### Q: Why is applying the tenant filter after retrieving the top-k neighbours insufficient?
- [ ] Because vector databases cannot store a tenant field alongside the embedding
- [x] Because the top-k may be filled by other tenants' chunks, collapsing recall, and the filter is one forgotten call site away from a leak
- [ ] Because post-filtering changes the embedding space and breaks cosine similarity
- [ ] Because the model needs to see all tenants' chunks to rank them correctly
> Post-filtering returns fewer than k results for small tenants and tempts engineers to raise k or drop
> the filter; a filter evaluated inside the search keeps k meaningful and makes the scoping structural.

### Q: Which component can leak tenant data even though the relational database enforces row-level security correctly?
- [ ] The HTTPS load balancer in front of the API
- [ ] The tokenizer used by the model
- [x] A semantic cache keyed only on the embedding of the question
- [ ] The JSON schema used for structured outputs
> Row-level security protects rows at query time; a semantic cache stores completions derived from those
> rows and serves them to whoever asks a similar question next, regardless of tenant, unless the tenant is
> in the key.

### Q: Where should the tenant identifier used for filtering come from?
- [ ] The `tenant` field in the JSON request body sent by the client
- [ ] The model's structured output, which includes the tenant it believes it is serving
- [x] The authenticated token resolved at the API edge and carried in an immutable request context
- [ ] The metadata of the first retrieved document
> Request bodies, model outputs and retrieved documents are all attacker-influenceable; only the verified
> credential is a trustworthy source, and carrying it in a context object prevents it being overwritten.
