---
id: vector-database-architecture
module: data-architecture
name: Vector database architecture
kind: concept
difficulty: advanced
est_minutes: 16
status: reviewed
related:
  - rag-retrieval/vector-databases
  - rag-retrieval/ann-indexes
  - rag-retrieval/metadata-filtering
  - rag-retrieval/acl-aware-retrieval
  - rag-retrieval/hybrid-search
  - data-architecture/vector-store-sizing
  - data-architecture/embedding-versioning-re-indexing-strategy
  - rag-retrieval/freshness-document-deletion-index-consistency
sources:
  - Malkov & Yashunin, 2018 — Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs
  - Subramanya et al., 2019 — DiskANN: Fast Accurate Billion-point Nearest Neighbor Search on a Single Node
  - Official docs: Qdrant
  - Official docs: pgvector
---

## TL;DR
A vector database is three things bolted together: an approximate-nearest-neighbour index, a document
store for ids and payload, and a query planner that combines similarity with filters. The hard problems
are not the distance math but mutability (graph indexes hate updates and deletes), filtered search
(selective filters break "find top-k then filter"), memory residency (graph traversal is random access,
so the index lives in RAM or on an SSD-aware design) and distribution. Understand those four and the
vendor comparison in the RAG module becomes a checklist.

## Why it exists
Exact nearest-neighbour search is a dot product per stored vector. For a million 1,536-dimensional
float32 vectors that is about 6 GB of reads per query, which at the tens of GB/s of memory bandwidth a
server offers is on the order of 100 ms or more per query, and it scales linearly with the corpus. Approximate indexes such as HNSW cut that to a few thousand distance
computations by navigating a graph, which is why ANN indexes exist and the rag-retrieval module explains
them. But an index alone is a library call, not a database. Production retrieval also needs to add and
delete vectors while serving queries, attach and filter on metadata (tenant, date, ACL), survive a process
restart, replicate for availability, and spread across machines when the corpus outgrows one.

Each of those requirements collides with the index. HNSW builds a graph whose quality depends on
insertion order and whose nodes cannot simply be removed without leaving the graph disconnected; deletes
become tombstones and rebuilds. A filter applied after the top-k is returned yields fewer than k, or zero,
results when the filter is selective, and a filter applied before means searching a set the graph was not
built for. Graph traversal performs random memory access, so an index that spills to disk becomes orders
of magnitude slower unless it was designed for SSDs. Sharding a similarity search means every shard must
answer and the results must be merged. A vector database is the engineering that resolves those
collisions; the choice between a dedicated engine and a vector extension inside Postgres or OpenSearch is
a choice about which of them you need solved and which you can live with.

## How it works
### Data model
A collection holds points: an id, one or more named vectors (dense, and in many engines sparse for
hybrid search), and a payload of key-value metadata. Payload fields that will be filtered on get
secondary indexes (keyword, numeric range, geo), typically inverted or bitmap indexes, because filter
evaluation must produce sets of candidate ids quickly.

### Index layer and why it is memory-bound
HNSW keeps a layered proximity graph; a query enters at the top layer, greedily walks to the nearest
node, descends, and at the base layer explores a beam of `ef` candidates. Each hop reads a node's
neighbour list and its vector, from unpredictable addresses, so throughput tracks memory latency and the
index must be RAM-resident. IVF partitions vectors into clusters by k-means and scans a few posting lists,
which is more sequential and cheaper in memory but needs training and loses recall on skewed data.
Quantization (scalar int8, product quantization, binary) shrinks the vectors the index touches during
traversal, trading a small recall loss that is recovered by rescoring the final candidates against
full-precision vectors kept on disk. DiskANN-style designs push this further: a compressed copy in RAM
guides the walk, and full vectors plus adjacency live on SSD with one read per visited node, which is how
billion-scale search fits on one machine.

### Write path: segments, tombstones and compaction
Writes do not go into the graph directly. The common design, shared in spirit by Qdrant, Milvus and
Lucene-based engines, is: append to a write-ahead log for durability; insert into a small mutable segment
that is searched by brute force; when it reaches a threshold, seal it and build an index in the
background; serve queries across all segments and merge. Deletes mark a tombstone bitmap consulted at
query time, and updates are a delete plus an insert. Periodic compaction merges segments, drops
tombstoned points and rebuilds the graph. This design exists because it keeps writes cheap and the index
immutable, at the cost of eventual visibility (a point is searchable after it lands in the mutable segment,
which may be after a refresh interval) and of query cost that creeps upward between compactions as
tombstones accumulate and segment counts grow.

### Query path and the filtering problem
A query is: embed (outside the database), evaluate the filter to a candidate set, search, merge across
segments and shards, rescore if quantized, fetch payloads, return top-k. The filter strategy is the
subtle part.

- **Post-filtering:** run ANN for top-k′ (k′ > k), then drop points failing the filter. Simple, and
  wrong when the filter is selective: if 1 % of points match, top-100 contains on average one survivor.
- **Pre-filtering:** compute the allowed-id bitmap from the payload index, then search only among them.
  Correct, but a graph traversal restricted to 1 % of nodes becomes disconnected, so engines fall back to
  brute force over the allowed set when it is small, which is fine precisely because it is small.
- **Filter-aware traversal:** check the filter at each hop and keep exploring through disallowed nodes
  without returning them, with extra graph links built per indexed payload field so the subgraph stays
  connected (Qdrant's approach), or a flat-search cutoff below which the engine abandons the graph
  (Weaviate's `flatSearchCutoff`; newer Weaviate releases also offer an ACORN-style filtered traversal). pgvector historically returned fewer than k rows under a selective `WHERE`
  because the index scan ran first; newer releases added iterative index scans that keep walking until
  enough rows pass.

A plain `WHERE tenant_id = ?` across millions of points is therefore an architecture question, not a
syntax question, and it is the first thing to test when evaluating an engine.

### Distribution and multi-tenancy
Sharding by hashed point id spreads load evenly but makes every query scatter to all shards and merge,
so latency is the slowest shard's. Sharding by a routing key such as tenant sends each query to one
shard, which is both faster and a natural isolation boundary, at the cost of hot shards for large tenants.
Replication provides availability and read scaling; most engines offer per-request consistency levels
because replicas apply writes asynchronously. Multi-tenancy has three patterns: a collection per tenant
(strong isolation, but each HNSW graph carries fixed overhead and thousands of tiny collections waste
memory), a payload filter per tenant (cheap, relies on the filtered-search path and on nobody forgetting
the filter), and tenant-partitioned indexes where the engine builds a separate graph per tenant value
inside one collection, which is the usual recommendation for many small tenants.

### Dedicated engine versus extension
pgvector gives ANN inside the database that already holds the chunks, with transactions, joins and row
security, and it is the right answer until the index no longer fits comfortably in the database's memory
or filtered recall and write throughput become the bottleneck. OpenSearch and Elasticsearch give HNSW
beside BM25 and existing log infrastructure. Dedicated engines win on filtered-search machinery,
quantization options, segment management and horizontal scaling. The architecture above is the lens for
deciding which of those you will actually hit.

## Example
The filtering problem, made visible. This simulation uses exact similarity, so the only variable is
*when* the filter is applied. One collection holds three tenants owning 90 %, 9.5 % and 0.5 % of the
points. Post-filtering takes the top-k′ by similarity and then drops other tenants' points.
Pre-filtering builds the allowed-id set first and scans it exactly.

```python
import numpy as np

rng = np.random.default_rng(0)
N, D, K = 200_000, 64, 10
X = rng.standard_normal((N, D)).astype(np.float32)
X /= np.linalg.norm(X, axis=1, keepdims=True)              # cosine = dot product
share = {"big": 0.90, "mid": 0.095, "small": 0.005}         # tenant sizes: 90 %, 9.5 %, 0.5 %
tenant = rng.choice(list(share), size=N, p=list(share.values()))

def post_filter(q, t, k_prime):                             # ANN-style: top-k' first, then filter
    top = np.argpartition(-(X @ q), k_prime)[:k_prime]
    return [i for i in top if tenant[i] == t][:K]

def pre_filter(q, t):                                       # allowed ids first, exact scan over them
    allowed = np.flatnonzero(tenant == t)
    scores = X[allowed] @ q
    return allowed[np.argsort(-scores)[:K]], len(allowed)

q = rng.standard_normal(D).astype(np.float32); q /= np.linalg.norm(q)
for t in share:
    got = [len(post_filter(q, t, kp)) for kp in (10, 100, 1000)]
    ids, scanned = pre_filter(q, t)
    print(f"{t:>5}: post-filter results for k'=10/100/1000 -> {got}; "
          f"pre-filter -> {len(ids)} results, scanned {scanned:,} vectors")
# Output (seed 0):
#   big: post-filter results for k'=10/100/1000 -> [9, 10, 10]; pre-filter -> 10 results, scanned 179,893 vectors
#   mid: post-filter results for k'=10/100/1000 -> [1, 10, 10]; pre-filter -> 10 results, scanned 19,110 vectors
# small: post-filter results for k'=10/100/1000 -> [0, 0, 4]; pre-filter -> 10 results, scanned 997 vectors
```

Read the output as the planner's decision table. Post-filtering returns about `k′ × selectivity`
survivors: fine for the big tenant, and empty for the small one even at k′ = 1,000, a hundred times the
requested k. Pre-filtering is always correct, but its cost is the size of the allowed set. Now scale to a
production collection of 10 million 1,024-dimensional float32 vectors (4 KB each). The small tenant's
50,000 vectors are about 200 MB to scan exactly, a few milliseconds to a few tens of milliseconds on one
node depending on memory bandwidth and threads. The big tenant's 9 million are about 36 GB, which is far
too much per query, so for it the engine must walk the graph and check the filter during traversal,
where the filter hardly prunes anything. A good engine estimates the allowed-set size from the payload
index and picks per query: exact scan below a threshold (Qdrant's `full_scan_threshold`, Weaviate's
`flatSearchCutoff`), filter-aware graph search above it. When you evaluate an engine, test the tenant
sizes in between, where neither strategy is cheap.

In a real engine the setup is a payload index on the filtered field, for example
`client.create_payload_index("chunks", field_name="tenant", field_schema="keyword")` in Qdrant, plus a
filter passed with the query. A delete marks a tombstone and returns at once; its memory comes back when
the segment is optimized. Neither behaviour shows on a toy collection, because servers brute-force
segments below their indexing threshold. Benchmark at realistic sizes.

## Trade-offs
- HNSW gives high recall at low latency but costs RAM for links plus vectors and a slow build; IVF or
  disk-based indexes flip the choice when the corpus is billions of vectors or memory is the budget line.
- Quantization cuts index memory by 4× to 32× and often speeds traversal, at a recall cost that rescoring
  mostly recovers; it stops being worth it when the collection is small enough to fit unquantized anyway.
- Pre-filtering is correct for selective filters but expensive when the filter matches most of the
  collection; engines pick per query by estimated selectivity, and you should test both extremes.
- Tenant-routed sharding gives isolation and single-shard queries, but a tenant larger than one shard
  forces a hybrid of routing plus scatter-gather that most engines handle less gracefully.
- A vector extension in an existing database saves a system and gives transactions with the chunk
  tables, but it flips toward a dedicated engine when filtered recall, write throughput or memory
  residency in the shared database become the bottleneck.

## Pitfalls
- Post-filtering with a selective filter; the symptom is queries that return fewer than k results, or
  none, for small tenants while large tenants work fine.
- Measuring recall only on a freshly built index; the symptom is retrieval quality and latency drifting
  worse over months as tombstones and segment fragmentation accumulate without compaction.
- Sizing memory for vectors alone and forgetting graph links, payload and merge headroom; the symptom is
  OOM kills during background index builds rather than during steady-state queries.
- Storing large payloads such as full chunk text inside the vector engine; the symptom is RAM dominated
  by text while the index itself is small, and slow snapshots and rebalances.
- Relying on application code to add the tenant filter; the symptom is a cross-tenant result in a log or
  a security review, which the ACL-aware retrieval lesson treats as a structural problem.
- Expecting writes to be immediately searchable; the symptom is an integration test that upserts and
  queries in the same millisecond and intermittently fails.

## When to use
Think in these terms whenever you choose or tune a vector store, size a cluster, or debug retrieval that
is correct in notebooks and wrong in production. The questions to answer are concrete: how selective are
the filters you will run, how many writes and deletes per hour, does the index fit in RAM with headroom,
how many tenants and how skewed are they, and what recall at what p99 latency. Benchmark filtered recall
and latency at your real selectivity, not the vendor's. Recall has a precise meaning here: take a few
hundred real queries with their real filters, compute the exact top-k by brute force, and report the
fraction of those ids the engine returns, at the p99 latency you get with the same settings. Repeat
after a month of writes and deletes, because a freshly built index is the best case. Start with pgvector when the chunk tables already
live in Postgres and the corpus is in the low millions; move to a dedicated engine when the architecture
above says one of its mechanisms is what you need. In interviews, explaining why a selective filter
breaks naive ANN and how segments and tombstones make deletes eventual is the level expected of an
architect.

## Role lens
- **Engineer:** Creates payload indexes for every filtered field, sets `ef` and quantization per
  collection, tests filtered recall at real selectivity, and schedules or monitors compaction.
- **Architect:** Chooses between an extension and a dedicated engine, defines the sharding and
  multi-tenancy pattern, and sets recall, latency and freshness targets the engine must be benchmarked
  against.
- **FDE:** Maps the customer's tenant, ACL and retention requirements onto the engine's filtering and
  isolation model, and demonstrates that a deleted document stops being retrievable within the agreed
  window.

## Check yourself
**Q:** A SaaS product serves 3,000 tenants from one collection using a payload filter on `tenant_id`.
Small tenants complain that search returns only one or two results, large tenants report slow queries,
and a security audit asks how cross-tenant leakage is prevented. Explain the mechanisms behind each
symptom and propose an architecture.
**A:** Small tenants get few results because the filter is applied after similarity search: the engine
takes the top-k′ over all 3,000 tenants and then discards the others, so a tenant owning 0.1 % of points
gets about `k′ × 0.001` survivors, which is effectively none. Large tenants are slow for related reasons.
The usual workaround for small tenants, a larger k′ or `ef` on every query, makes each query walk much
more of the shared graph. And a large tenant's points are spread over every shard, so each of its
queries pays scatter-gather and waits for the slowest shard. Leakage prevention currently depends on
application code remembering the filter. The fix has three parts. First, give the engine what it needs
to plan by selectivity: a payload index on `tenant_id`, and ideally per-tenant index structure inside the
collection (in Qdrant, a tenant-marked keyword index with graphs built per payload value instead of one
global graph). Small tenants are then searched exactly or as small graphs, and none of them is a sliver
of a huge one. Second, put the handful of very large tenants on dedicated shards or collections, which
gives them isolation and predictable latency. Third, enforce the filter in a retrieval service that
derives `tenant_id` from authentication, never from the request body. Then verify with recall@k against
exact search, measured separately at the smallest, median and largest tenant sizes.

## Quiz
### Q: Why does an HNSW index need to be resident in RAM to perform well?
- [ ] Because the distance computation uses SIMD instructions that require RAM
- [x] Because graph traversal reads neighbour lists and vectors from unpredictable addresses, so each hop is a random access that disk cannot serve quickly
- [ ] Because the graph is rebuilt on every query
- [ ] Because payload filters are stored in the same memory region
> A query walks tens to thousands of nodes, each at an address determined by the previous hop. Random
> reads from SSD are orders of magnitude slower than from memory, which is why disk-based designs like
> DiskANN keep a compressed copy in RAM to guide the walk and read full vectors once per visited node.

### Q: A deletion in a vector database returns instantly but memory is not freed. What explains this?
- [ ] The engine batches deletes until the next replica sync
- [ ] Deletes are only applied to the payload store, not the vectors
- [x] The point is marked with a tombstone consulted at query time, and the graph and memory are rebuilt during a later compaction
- [ ] Vector indexes cannot remove nodes, so deletes are ignored
> Removing a node from a proximity graph would leave neighbours disconnected and degrade recall, so
> engines mark deletions and filter them out of results. Compaction later rebuilds segments without the
> tombstoned points, which is when memory and query cost recover.

### Q: With a filter that matches 0.5 % of a collection, which filtering strategy is likely to return correct top-k results?
- [ ] Retrieve the top-k by similarity, then discard points failing the filter
- [x] Compute the matching ids first and search only within them, falling back to brute force over that small set
- [ ] Keep post-filtering but double the candidate count k′ before discarding non-matching points
- [ ] Disable the HNSW index and rely on the payload index alone
> With 0.5 % selectivity, a top-100 similarity result contains about half a matching point on average,
> and doubling k′ to 200 still yields about one, so post-filtering returns almost nothing. Pre-filtering
> yields a small candidate set that is cheap to scan exactly, which is why engines switch strategy based on
> estimated selectivity.
