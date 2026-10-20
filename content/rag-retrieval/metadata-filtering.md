---
id: metadata-filtering
module: rag-retrieval
name: Metadata filtering
kind: concept
difficulty: core
est_minutes: 14
related:
  - rag-retrieval/vector-databases
  - rag-retrieval/ann-indexes
  - rag-retrieval/acl-aware-retrieval
  - rag-retrieval/hybrid-search
  - rag-retrieval/recall-vs-precision-trade-off
  - rag-retrieval/freshness-document-deletion-index-consistency
  - system-design/tenant-isolation
  - data-architecture/vector-database-architecture
sources:
  - Malkov & Yashunin, 2018 — Efficient and Robust Approximate Nearest Neighbor Search Using Hierarchical Navigable Small World Graphs
  - Official docs: Qdrant (filtering and payload indexes)
  - Official docs: pgvector
---

## TL;DR
Vector similarity ranks by meaning only, but most real queries also carry hard constraints: this
tenant, this document type, this date range, this language. Metadata filtering evaluates those
predicates *inside* the nearest-neighbour search so the top-k is computed over the eligible set
rather than over everything. Where the predicate is applied (after, before, or during the ANN walk)
decides whether you get k results at all, how fast the query runs, and whether a very selective
filter quietly destroys recall.

## Why it exists
An embedding of "Q3 revenue summary" is close to every Q3 revenue summary ever written. Nothing in
the vector says which tenant it belongs to, whether the document is a draft or the signed version, or
whether it was superseded last month. Those facts live in metadata, and users expect them to be
honoured exactly: a filter is a promise ("only 2025 contracts"), not a preference.

The naive approach is to run the vector search unfiltered and drop non-matching results in
application code (post-filtering). It fails arithmetically. Suppose one tenant owns 1% of a
million-chunk index. The unfiltered top-10 is drawn from all tenants, so on average it contains
about 0.1 chunks from the one you want. You compensate by asking for the top-1,000 and filtering
down, which costs ten to a hundred times the work and *still* gives no guarantee: a rare tenant with
unusual vocabulary may have zero chunks in the top-1,000. The symptom is a search that returns
three results instead of ten, or none at all, for exactly the users with the least data.

The other naive approach, one index per filter value, explodes combinatorially (tenant × document
type × year) and cannot express ranges. So the search engine itself has to understand predicates,
and the interesting engineering is in how it combines a boolean predicate with a graph-based
approximate index that was never designed to skip nodes.

## How it works
### Three places to apply the predicate
1. **Post-filtering.** Run ANN for the top-k, then discard non-matching hits. Simple and index-agnostic,
   but the result count is unpredictable and the cost of inflating k is paid on every query. It is
   acceptable only when the filter is loose (matches most of the corpus).
2. **Pre-filtering.** Evaluate the predicate first, usually through a secondary index on the metadata
   field (an inverted index or bitmap), producing the set of eligible IDs. If that set is small,
   search it exhaustively with exact distance computations. This is exact, and cheap *because* the
   set is small: 5,000 eligible vectors of 1,024 dimensions is about five million multiply-adds,
   a few milliseconds on one core. It becomes expensive when the filter matches, say, 60% of a
   50-million-vector corpus, because brute force then touches 30 million vectors.
3. **In-graph (integrated) filtering.** Walk the HNSW graph as usual, but only count nodes that pass
   the predicate as candidates for the result list, while still being allowed to traverse through
   non-matching nodes to keep moving. The predicate is checked per visited node against a bitset or
   payload index. This keeps the cost near the unfiltered cost for moderately selective filters.
   Its weakness is the opposite of pre-filtering's: when very few nodes match, almost every
   neighbour fails the check, the beam fills with far-away matches or none at all, and recall
   collapses, because the subgraph of matching nodes is not connected enough to walk.

### The planner: choosing by cardinality
Production engines combine 2 and 3 with a cost-based rule, exactly as a SQL planner chooses between
an index scan and a sequential scan. The payload index gives an estimate of how many points satisfy
the filter. Below a threshold (tens of thousands of points, engine-dependent) the engine brute-forces
the filtered set and the answer is exact; above it, it walks the graph with the predicate applied.
Qdrant additionally builds extra graph edges among points that share common payload values so that
filtered traversal stays connected; Lucene-based engines (Elasticsearch, OpenSearch) compute the
filter bitset first and switch to exact search when the bitset is small. The design goal in all of
them is the same: never let a selective filter turn into either an empty result or a full scan.

### pgvector: filters are WHERE clauses, with a catch
In Postgres, `WHERE tenant_id = $1 ORDER BY embedding <=> $2 LIMIT 10` looks like pre-filtering but
is not. When the planner picks the HNSW index, the index scan yields roughly `hnsw.ef_search`
candidates in distance order (default 40), and the WHERE clause is applied to *those*, so a selective
filter returns fewer than 10 rows. Mitigations: raise `hnsw.ef_search`; use a B-tree index on the
filter column so the planner can filter first and sort exactly when the subset is small; build a
partial HNSW index per large tenant; or, in recent pgvector versions, enable iterative index scans,
which keep pulling candidates from the index until the LIMIT is satisfied.

### Designing the metadata itself
- Index every field you filter on (payload index, GIN, B-tree). An unindexed predicate forces the
  engine to read each candidate's payload during the walk and blinds the cardinality estimate.
- Denormalise document-level fields onto every chunk at ingest, because the search runs at chunk
  granularity and a join at query time is not available inside the ANN loop.
- Prefer low-cardinality enums, numeric ranges and keyword IDs. Free-text fields are for search,
  not filters.
- Store dates as integers or native dates, never as strings with mixed formats; range predicates on
  strings compare lexicographically.

### Where the filter values come from
Hard filters can be set by the application (tenant, permissions) or inferred from the query
("policies updated since January for the EU team" → `doc_type=policy, updated_at>=2025-01-01,
region=EU` plus the semantic part). Inferred filters must be validated against the schema and the
set of known enum values, with a fallback to no filter on parse failure, otherwise a hallucinated
value produces an empty result set. Distinguish constraints from preferences: "prefer recent" should
be a score boost in a hybrid ranker, not a predicate that excludes everything older.

## Example
A Qdrant collection where the tenant, document type and update day are indexed payload fields and
every query carries a filter. With a selective filter (one small tenant, one type, last 30 days) the
engine's planner brute-forces the few hundred matching points and the result is exact; with a loose
filter it walks the graph with the predicate checked per node.

```python
from qdrant_client import QdrantClient, models

client = QdrantClient(url="http://localhost:6333")
COL = "chunks"

# Index every field that appears in predicates. Without an index the engine reads
# each visited point's payload during the HNSW walk and cannot estimate cardinality.
client.create_payload_index(COL, "tenant_id", field_schema="keyword")
client.create_payload_index(COL, "doc_type", field_schema="keyword")
client.create_payload_index(COL, "updated_day", field_schema="integer")  # days since epoch

def search(qvec, tenant, doc_types, since_day, k=10):
    flt = models.Filter(must=[
        models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant)),
        models.FieldCondition(key="doc_type", match=models.MatchAny(any=doc_types)),
        models.FieldCondition(key="updated_day", range=models.Range(gte=since_day)),
    ])
    res = client.query_points(COL, query=qvec, query_filter=flt, limit=k,
                              search_params=models.SearchParams(hnsw_ef=128))
    return [(p.id, p.score, p.payload["doc_id"]) for p in res.points]

# Diagnostic: compare against post-filtering to see the shortfall for a small tenant.
def post_filtered(qvec, tenant, k=10, overfetch=100):
    res = client.query_points(COL, query=qvec, limit=overfetch)
    hits = [p for p in res.points if p.payload["tenant_id"] == tenant]
    return hits[:k]          # often fewer than k for tenants with little data
```

Illustrative arithmetic for the diagnostic: if the tenant owns 1% of the collection and its chunks
are not unusually close to the query, over-fetching 100 yields about one matching hit, so the
post-filtered path returns one result where the filtered path returns ten.

## Trade-offs
- Pre-filtering with brute force is exact, and its cost scales with the size of the filtered set; it
  stops being the right choice once the filter matches more than a few percent of a large corpus.
- Post-filtering needs no engine support and no payload indexes, but the result count is
  unpredictable; it is defensible only for loose filters where over-fetching a little is cheap.
- Indexing every metadata field costs write throughput and memory; the cost is justified only for
  fields that actually appear in predicates, so unused payload indexes should be dropped.
- Denormalising document metadata onto chunks makes filters fast, but a change to one document's
  metadata (reclassified, re-dated) must be written to every chunk; if metadata churns faster than
  content, keep a document table and filter by an ID list instead.
- A hard filter guarantees the constraint but returns nothing when nothing matches; a soft boost
  degrades gracefully but can violate the user's intent, so the choice depends on whether the
  constraint is a rule or a preference.

## Pitfalls
- Fewer than k results, or an empty list, as soon as a filter is added; the symptom of post-filtering
  or of a candidate-limited index scan (pgvector with a low `ef_search`).
- Result quality that is fine for large tenants and poor for small ones; the symptom of in-graph
  filtering losing connectivity under a selective predicate, fixed by the engine's brute-force
  fallback threshold or by partitioning small tenants.
- A p99 latency cliff after adding a new predicate; almost always an unindexed payload field.
- Enabling LLM-inferred filters without schema validation; the symptom is a jump in the
  "no results" rate because the model invented an enum value or a date format.
- Dates stored as strings in mixed formats, so range filters silently include or exclude the wrong
  rows; the symptom is a "since last month" query returning two-year-old documents.
- Using the metadata filter as the authorization mechanism but computing the filter value from
  request parameters the client controls; see the ACL-aware retrieval lesson.

## When to use
Use a filter whenever the query carries a constraint that must hold exactly: tenant and permission
boundaries always, then document type, status, language, date range and product version as the
application demands. Do not use a filter for preferences ("recent", "from my team") that should
influence ranking rather than eligibility; those belong in a hybrid scorer. Measure three things per
filter: the shortfall rate (queries returning fewer than k), filtered recall against an exhaustive
search on a golden set bucketed by selectivity, and p99 latency by selectivity. In day-to-day work
this shows up as the first question when someone says "search is empty for customer X"; in
interviews, expect to explain pre-, post- and in-graph filtering and why a highly selective filter is
the hard case for a graph index.

## Role lens
- **Engineer:** Declares payload indexes, builds the filter from server-side context, validates
  inferred filters against the schema, and benchmarks filtered recall per selectivity bucket.
- **Architect:** Decides which fields are hard constraints, whether tenants share one index or get
  partitions, and which engine's filtering model matches the corpus's selectivity profile.
- **FDE:** Maps the customer's document taxonomy (types, regions, statuses) onto filterable metadata at
  ingest and proves on their data that small business units get the same recall as large ones.

## Check yourself
**Q:** After adding a `tenant_id` filter, a vector search returns 3 results instead of 10 for a small
tenant, and the quality for that tenant is visibly worse than for large tenants. What is happening,
and what are your options?
**A:** Two mechanisms produce this. Either the engine post-filters (or, as in pgvector's default HNSW
scan, filters a fixed candidate list), so a tenant owning a small fraction of the index rarely has
enough chunks among the candidates; or the engine filters in-graph and the tenant's few points are
poorly connected, so the walk cannot reach them. Options: use a payload index so the engine can
estimate cardinality and brute-force the small tenant's points exactly; raise the candidate count
(`ef_search`) or enable iterative scanning; partition small tenants into their own collection or
partial index; and add a regression test that measures filtered recall per tenant-size bucket
against an exhaustive scan so the problem cannot return silently.

## Quiz
### Q: Why does post-filtering return fewer than k results for a selective filter?
- [ ] Because the vector index cannot store metadata alongside vectors
- [x] Because the top-k is fixed before the predicate is applied, so matches outside that top-k are never considered
- [ ] Because filtered vectors are normalised differently from unfiltered ones
- [ ] Because post-filtering requires an exact search that times out
> The filter runs on a result list that was already truncated to k by similarity alone; anything
> matching the filter but ranked below k is gone. Over-fetching reduces, but cannot eliminate, the shortfall.

### Q: When is exhaustively scanning the filtered subset the right strategy?
- [ ] Whenever the collection has fewer than one million vectors
- [ ] Never; HNSW is always faster than brute force
- [x] When the predicate is highly selective, so the eligible set is small enough to score exactly in milliseconds
- [ ] Only when the metadata field is unindexed
> Brute force costs one distance computation per eligible vector; for a few thousand vectors that is
> cheaper and exact, whereas a graph walk restricted to a sparse set of matching nodes loses recall.

### Q: Why can a very selective filter reduce recall for in-graph (filtered HNSW) traversal?
- [ ] The engine falls back to keyword search for selective filters
- [ ] Selective filters force the vectors to be re-embedded at query time
- [x] Few visited nodes pass the predicate, so the matching subgraph is poorly connected and the search beam cannot reach the true neighbours
- [ ] HNSW stores filtered vectors in a separate lower layer with fewer edges
> Graph search relies on neighbours leading to better neighbours; when nearly all neighbours fail the
> predicate, the walk stalls, which is why engines add filter-aware edges or switch to brute force.
