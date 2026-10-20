---
id: metadata-filtering
module: rag-retrieval
name: Metadata filtering
kind: pattern
difficulty: core
est_minutes: 14
related:
  - rag-retrieval/ann-indexes
  - rag-retrieval/vector-databases
  - rag-retrieval/acl-aware-retrieval
  - rag-retrieval/hybrid-search
  - rag-retrieval/query-rewriting
  - rag-retrieval/embedding-pipelines
  - rag-retrieval/freshness-document-deletion-index-consistency
  - data-architecture/vector-database-architecture
sources:
  - Official docs: Qdrant filtering and payload indexes
  - Official docs: pgvector
  - Official docs: Weaviate filtered vector search
---

## TL;DR
An embedding encodes what a chunk is about, not which tenant owns it, when it was written or which
product version it describes. Metadata filtering stores those structured attributes next to each vector
and applies them as hard predicates *inside* the search, because applying them afterwards to an
approximate top-k returns too few results, or none, as soon as the predicate is selective. The
engineering question is where the predicate runs: before the ANN walk, during it, or after it.

## Why it exists
Similarity is a soft score; many real constraints are hard. "Only the 2025 version of the travel policy",
"only this customer's tickets", "only contracts, not emails", "only French". A query embedding cannot
exclude the 2024 policy, because the 2024 and 2025 versions are near-duplicates and therefore each
other's nearest neighbours. The top-k fills with the wrong year and the model confidently answers from
stale rules.

The first naive fix is to write the attribute into the text ("Year: 2025. Tenant: acme.") and hope the
embedding carries it. An embedding compresses hundreds of tokens into one vector; a short attribute
barely moves it, so the "filter" is probabilistic at best, and it cannot express ranges, negation or
list membership at all.

The second naive fix is post-filtering: fetch the top-k by similarity, then drop rows that fail the
predicate. If a fraction *s* of the corpus passes the filter and the unfiltered top-k is roughly a random
sample with respect to that attribute, the expected number of survivors is about *k·s*. For k = 10 and a
filter that keeps 1% of rows, that is 0.1 results: usually an empty list. Over-fetching k/s = 1,000
candidates recovers ten on average, but ANN recall degrades at large k, latency grows, and the survivors
are still not guaranteed to be the true top-10 among matching rows, because those may sit outside the
unfiltered top-1,000 entirely. The predicate therefore has to be part of the search, which is why vector
databases ship payload indexes and filtered graph traversal rather than leaving it to the caller.

## How it works
### Representing metadata
Each vector carries a payload: `tenant_id`, `doc_id`, `source`, `doc_type`, `language`, `created_at`
(as an integer timestamp, not a string), `version`, `section_path`, and for access control a list of
allowed principals. Types matter because they determine which predicates are possible and fast: keyword
fields support exact and set membership (`in [...]`), numeric and datetime fields support ranges, boolean
and geo fields have their own operators, and array fields support "any of". A filterable field needs a
secondary index on the payload (a bitmap or inverted index keyed by value), exactly as a relational
column needs an index; without it the engine scans every candidate's payload and filtered latency grows
linearly with collection size.

### Three places the predicate can run
1. **Pre-filtering.** Evaluate the predicate first, using the payload index, to get the set of allowed
   IDs. If that set is small (hundreds to a few tens of thousands of vectors) the cheapest and most
   accurate thing is a brute-force exact distance computation over just those IDs; no ANN structure is
   needed at all. If it is large, the allowed set is passed into the ANN search as a bitmap mask. The
   result is exact with respect to the predicate; the cost scales with the size of the allowed set.
2. **Post-filtering.** ANN top-k′, then drop failures. Trivial to implement and acceptable only when the
   predicate passes most rows, for example excluding the 2% of documents flagged as deleted. For a
   selective predicate it is broken by the arithmetic above.
3. **Filtered traversal (in-search filtering).** The HNSW greedy walk evaluates the predicate on every
   candidate and only admits passing nodes to the result set, while still traversing through failing
   nodes so the graph stays connected. This is the default in most engines, and it has a failure mode:
   when the predicate is very selective, the walk visits many failing nodes before it finds passing ones,
   and the passing nodes may be poorly connected to each other, so recall drops and latency spikes.
   Engines therefore switch strategy on estimated cardinality: below a threshold they brute-force the
   pre-filtered set, above it they run filtered traversal. Qdrant exposes such a threshold in its HNSW
   configuration, Weaviate has a flat-search cutoff, and pgvector historically behaved like a
   post-filter (the planner pulls `ef_search` candidates from the index, then applies `WHERE`, so a
   selective predicate returns fewer than `LIMIT` rows); recent pgvector versions add iterative index
   scans that keep walking until enough rows pass, and partial indexes (`CREATE INDEX ... WHERE
   tenant_id = ...`) remain the pre-filter option.

### The decision rule is selectivity
Let *s* be the fraction of the corpus that passes. When *s* is tiny, pre-filter and brute-force: exact,
cheap, and the ANN index is irrelevant. When *s* is large (most rows pass), filtered traversal costs
about the same as an unfiltered search and even post-filtering with a modest over-fetch works. The
dangerous middle (roughly 0.1% to 10%, model it for your engine) is where filtered traversal can lose
recall, so this is the band to measure: run your labeled queries with their real filters and compute
recall@k per selectivity bucket, not just unfiltered.

### Multi-tenancy is the most common filter
Options: one collection per tenant (strong isolation, simple queries, but thousands of small graphs cost
memory and operations), one shared collection with a `tenant_id` predicate on every query (one index,
cheap, but enforcement must live in code the caller cannot bypass), or tenant-aware layouts that
co-locate a tenant's vectors so the filtered search touches one region (Qdrant's tenant payload index
option, Weaviate's native per-tenant shards). The ACL-aware retrieval lesson covers the security side;
the point here is that the tenant predicate must be the cheapest predicate you have, because it is on
every request.

### Where filter values come from
From the UI (date pickers, product selectors), from the authenticated request (tenant, principals; never
from the request body or the model), or from an LLM that parses natural-language constraints ("last
quarter's incident reports") into structured predicates: the self-query pattern from query rewriting.
LLM-derived filters must be validated against an allowlist of fields and values, must never touch
security fields, and need a fallback (retry without the filter, or ask) because a misparsed predicate
silently returns an empty result set.

### Filters and the rest of the pipeline
The same predicate must be applied in every retrieval arm (BM25 and dense in hybrid search) and before
reranking, otherwise one arm surfaces documents the other excluded. Metadata updates are payload writes,
not re-embeds: changing a document's status or ACL should never trigger re-computing its vectors.

## Example
A self-contained simulation of the two strategies on 200k random unit vectors split across 100 tenants,
so any single tenant's filter keeps about 1% of rows. `pre_filter` evaluates the predicate first and does
an exact search inside it; `post_filter` takes the unfiltered top-k′ and drops other tenants, as a
post-filtering engine would.

```python
import numpy as np
rng = np.random.default_rng(0)
N, d, k = 200_000, 64, 10
X = rng.standard_normal((N, d)).astype(np.float32); X /= np.linalg.norm(X, axis=1, keepdims=True)
tenant = rng.integers(0, 100, N)                      # 100 tenants: each filter keeps ~1% of rows
q = X[rng.integers(N)] + 0.1 * rng.standard_normal(d); q /= np.linalg.norm(q)
sims = X @ q                                          # cosine similarity of every row to the query

def pre_filter():                                     # predicate first, exact search inside it
    allowed = np.flatnonzero(tenant == 7)             # ~2,000 ids, straight from a payload index
    return allowed[np.argsort(-sims[allowed])[:k]]

def post_filter(k_fetch):                             # unfiltered top-k_fetch, then drop other tenants
    top = np.argpartition(-sims, k_fetch)[:k_fetch]
    keep = top[tenant[top] == 7]
    return keep[np.argsort(-sims[keep])][:k]

truth = set(pre_filter())
for kf in (10, 100, 1_000, 10_000):
    got = post_filter(kf)
    print(f"over-fetch {kf:>6}: {len(got):>2} results, {len(set(got) & truth):>2}/{k} correct")
# Typical run: over-fetch 10 -> 0 results; 100 -> ~1; 1,000 -> ~10 but some wrong;
# 10,000 -> all 10 correct, at 5x the candidate volume of the exact pre-filtered search.
```

The pre-filtered search touched ~2,000 rows and was exact. Post-filtering needed to over-fetch 5% of
the whole corpus to match it, and at k′ = 10 (what a naive `top_k=10` call does) it returned nothing.

## Trade-offs
- Pre-filtering is exact with respect to the predicate, but its cost scales with the allowed set; once
  the filter passes most of the corpus, brute force becomes a full scan and you need the graph instead.
- Post-filtering needs no engine support and is adequate when nearly every row passes; it flips to
  useless as the pass fraction drops, and it never guarantees the true filtered top-k.
- Payload indexes make predicates fast but cost memory and write throughput per indexed field; indexing
  every field you might ever filter on is the vector-store version of indexing every column.
- One collection per tenant gives isolation and trivially cheap predicates, but thousands of small HNSW
  graphs cost more memory and operations than one shared graph; the flip depends on tenant count and
  size skew.
- LLM-derived filters make natural-language constraints work, but every misparse silently empties the
  result set, so they cost a validation layer and a fallback path.

## Pitfalls
- Post-filtering an ANN top-k; the symptom is small tenants or rare document types returning fewer than
  k results, or none, while the same query without the filter returns plenty.
- Filtering on a field with no payload index; the symptom is filtered queries whose latency grows with
  collection size while unfiltered queries stay flat.
- Storing dates as strings in mixed formats so range predicates compare lexically; the symptom is
  "newest document" queries returning old content or nothing for certain date windows.
- Re-embedding and re-inserting documents to change their metadata; a status or ACL change is a payload
  update and should cost milliseconds, not an embedding job.
- Accepting `tenant_id` or ACL values from the client request or from the model; the symptom is
  cross-tenant leakage the moment someone edits a request body.
- Applying the predicate in only one arm of hybrid search or after reranking; the symptom is documents
  appearing in results that the filter should have excluded.
- Evaluating recall only without filters; the symptom is a filtered-traversal recall collapse that shows
  up in production for selective filters and never in the eval.

## When to use
Reach for a metadata filter whenever a constraint is a hard predicate rather than a preference: tenant,
access control, date range, document type, language, product version, publication status. Do not use
filters for soft preferences such as "prefer recent"; those belong in score boosting or reranking, where
they trade off against relevance instead of excluding it. Measure three things: filtered recall@k on
labeled queries bucketed by selectivity, p95 latency per bucket, and the rate of queries that return
fewer than k results. In day-to-day work the filter builder is where security, UX and retrieval quality
meet, which is why interviewers ask "what happens in your vector database when a filter matches 0.5% of
the rows": the expected answer covers pre-, post- and in-traversal filtering, the k·s arithmetic, and the
cardinality threshold at which the engine should switch to brute force.

## Role lens
- **Engineer:** Designs the payload schema with typed fields, creates payload indexes for every filtered
  field, writes a validated filter builder, and tests the fewer-than-k cases explicitly.
- **Architect:** Decides the tenant layout (shared collection with predicate versus collection per
  tenant), which fields are filterable, and the rule that security predicates derive from identity, never
  from request bodies.
- **FDE:** Maps the customer's document attributes (SharePoint sites, Confluence spaces, product lines,
  regions) onto filterable metadata during ingestion and checks that the filters match how users actually
  scope their questions.

## Check yourself
**Q:** A support search over 5 million ticket chunks lets agents scope to one customer. One customer owns
300,000 chunks, another owns 400. The index is HNSW with post-filtering. What happens for each customer,
and how would you redesign the filter?
**A:** Post-filtering survives roughly k·s of the fetched candidates. The large customer is 6% of the
corpus, so an over-fetch of ~170 candidates yields about 10 results, which is workable. The small customer
is 0.008% of the corpus; even a top-1,000 over-fetch yields 0.08 expected survivors, so agents see empty
results. Redesign: put a payload index on `customer_id` and let the engine pick the strategy by
cardinality: brute-force exact search over the 400 chunks for the small customer (cheaper than any graph
walk), filtered traversal or a tenant-aware layout for the large one. Verify with filtered recall@k
measured per customer-size bucket, add an alert on fewer-than-k results, and never accept `customer_id`
from the client without checking the caller is authorized for it.

## Quiz
### Q: Why does post-filtering an ANN top-k fail for selective predicates?
- [ ] The ANN index cannot compute distances for rows that carry metadata
- [x] Only about k·s of the k candidates pass, and the true filtered top-k may not be in the unfiltered candidates at all
- [ ] Post-filtering forces the engine to rebuild the HNSW graph for each query
- [ ] The predicate is evaluated before the embedding is computed, so it is ignored
> With selectivity s, the expected survivors are k·s (0.1 for k = 10 and s = 1%). Over-fetching helps
> only partly, because matching rows ranked outside the unfiltered candidate set are never seen.

### Q: A filter matches 300 vectors out of 10 million. Which execution strategy is best?
- [ ] Raise ef_search until the filtered HNSW walk finds enough matches
- [ ] Post-filter with an over-fetch of 10,000 candidates
- [x] Pre-filter via the payload index and run an exact brute-force search over the 300 matching IDs
- [ ] Build a separate HNSW index for every distinct value of the filtered field
> Three hundred exact distance computations are cheaper and more accurate than any graph traversal.
> Engines do this automatically below a cardinality threshold; over-fetching and ef_search tuning fight
> the arithmetic instead of avoiding it, and per-value indexes do not scale to arbitrary predicates.

### Q: A date-range filter returns nothing even though matching documents exist. What is the most likely cause?
- [ ] HNSW indexes cannot support range predicates
- [x] Dates were stored as strings in inconsistent formats, so the range comparison is lexical and misses
- [ ] The embedding model does not encode dates
- [ ] The query vector was not normalized before the search
> Range predicates need a numeric or datetime field; mixed string formats compare lexically and silently
> exclude rows. The embedding is irrelevant to a metadata predicate, and HNSW engines support ranges via
> payload indexes.
