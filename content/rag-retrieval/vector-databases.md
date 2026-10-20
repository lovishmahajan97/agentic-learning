---
id: vector-databases
module: rag-retrieval
name: Vector databases (pgvector, Qdrant, Pinecone, Weaviate, Milvus, Chroma)
kind: tool
difficulty: core
est_minutes: 16
status: reviewed
related:
  - rag-retrieval/ann-indexes
  - rag-retrieval/similarity-search
  - rag-retrieval/metadata-filtering
  - rag-retrieval/hybrid-search
  - rag-retrieval/embedding-pipelines
  - data-architecture/vector-database-architecture
  - data-architecture/vector-store-sizing
  - system-design/build-vs-buy
sources:
  - Malkov & Yashunin, 2016 — Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs
  - Official docs: pgvector
  - Official docs: Qdrant
  - Official docs: Milvus
---

## TL;DR
A vector database stores embeddings with their metadata and answers "the k nearest vectors to this one,
subject to these filters" fast enough for a request path. It exists because brute-force scanning
millions of vectors is too slow and a bare ANN library (FAISS, hnswlib) has no persistence, updates,
filtering or multi-tenancy. pgvector adds this inside Postgres; Qdrant, Weaviate and Milvus are dedicated
open-source engines; Pinecone is fully managed; Chroma is the embedded prototyping option. The choice is
mostly about operations and scale, not retrieval quality.

## Why it exists
Exact nearest-neighbour search costs O(N·d) per query: ten million 1536-dimensional vectors is roughly
15 billion multiply-adds per query and about 61 GB of float32 vectors streamed from memory, fine for a
nightly batch job and hopeless on a CPU node at 100 queries per second with a 50 ms budget. Approximate nearest-neighbour indexes such as HNSW and IVF cut the work to a small
fraction of the corpus at a modest recall cost. But an index is not a database. FAISS gives you an
in-memory index; you still have to persist it, update it as documents change (graph indexes handle
deletes awkwardly), store the chunk text and metadata somewhere, filter by tenant or date without
destroying recall, replicate for availability, and shard when the vectors stop fitting one machine. Teams
that start with "FAISS and a pickle file" rebuild each of these after an incident.

The relational alternative, storing vectors as float arrays and ordering by a computed distance, is
exact and simple but scans every row, so somewhere between hundreds of thousands and a few million
vectors (illustrative; it depends on dimension, hardware and latency budget) it stops fitting an
interactive request. A vector
database is the layer that packages ANN indexing with ordinary database properties: durability, CRUD,
filtering, access control and horizontal scale.

## How it works
### The common core
Every one of these systems has a collection (or table) with a fixed dimension and a distance metric;
records made of an id, a vector and a payload of metadata; an ANN index, almost always HNSW, with IVF,
product-quantization and disk-oriented variants for larger corpora; filtered search; CRUD with
soft-deletes that are compacted later; durability through a write-ahead log and snapshots; optional
quantization of stored vectors; and an HTTP or gRPC API. Recall and latency are tuned by index
parameters (`M` and `ef_construction` at build time, `ef_search` or `nprobe` at query time), and higher
recall always costs more latency.

### Filtering is where they differ
Filtering looks trivial and is the hardest part. Pre-filtering restricts candidates to the rows that match
and then searches them: exact, but slow when the filter is loose. Post-filtering runs the ANN search for
the top-k and then drops non-matching rows: fast, but a selective filter can leave fewer than k results or
none at all. Dedicated engines integrate the filter into the graph traversal (Qdrant calls this
filterable HNSW) and fall back to a brute-force scan when a filter is very selective. pgvector applies
the `WHERE` clause to rows coming out of the index scan, which is why a filtered query can return fewer
rows than `LIMIT` asks for; version 0.8.0 added opt-in iterative index scans (`hnsw.iterative_scan`)
that keep walking the graph until enough matching rows are found. Knowing which behaviour your store has is the key to debugging "the
filtered query returns nothing".

### The six, briefly
- **pgvector** is a Postgres extension: a `vector(d)` column type, the operators `<->` (L2), `<=>`
  (cosine distance) and `<#>` (negative inner product), and `ivfflat` and `hnsw` index types; without an
  index it does an exact scan. Its strength is everything Postgres already gives you: transactions, joins
  against your real tables, row-level security, one backup and a team that already runs it. Its limits
  are single-node scaling, index build time and memory, and the filtered-recall behaviour above. Two
  sharp edges: HNSW and IVFFlat indexes on the `vector` type accept at most 2,000 dimensions, so a
  3,072-dimensional model must be stored as `halfvec` (indexable up to 4,000) or truncated; and there is
  no built-in BM25, so hybrid search means fusing results with Postgres full-text search (whose
  `ts_rank` is a different scoring function) or adding another extension.
- **Qdrant** is an open-source engine written in Rust: HNSW with payload-aware filtering built into the
  traversal, scalar, binary and product quantization, named and sparse vectors per point for hybrid
  search, REST and gRPC APIs, sharding and replication in distributed mode, and a managed cloud.
- **Weaviate** is an open-source engine written in Go: HNSW, optional modules that call an embedding model
  for you at write and query time, built-in BM25 with hybrid score fusion, GraphQL and REST APIs, and
  multi-tenancy with per-tenant shards.
- **Milvus** is an open-source distributed system that separates compute from storage: segments in object
  storage, metadata in etcd, and a log service as the write-ahead log (historically Pulsar or Kafka;
  the exact component varies by release), with many index types (IVF variants,
  HNSW, DiskANN, GPU indexes). It targets billions of vectors and is correspondingly heavier to operate;
  Milvus Lite embeds in a Python process for development and Zilliz Cloud is the managed version.
- **Pinecone** is a fully managed, proprietary service with serverless and pod-based deployments,
  namespaces for tenant isolation, metadata filtering and sparse-dense hybrid search. You get zero
  operations; you pay per usage and accept that data and index live with the vendor.
- **Chroma** is open source and Python/JS-first: it runs embedded in the application process with
  on-disk persistence or as a small server, exposes collections with `add`, `query` and `where`
  filters, and uses an HNSW index underneath. The open-source single-node mode is ideal for prototypes
  and tests, not for high-concurrency or multi-node deployments; the vendor's hosted service is a
  separate, distributed offering, so check its current capabilities rather than assuming either way.

Adjacent options: FAISS and hnswlib (libraries, not databases), Elasticsearch and OpenSearch kNN (if you
already run them for lexical search), Redis, LanceDB and Vespa.

### When a write becomes searchable
The systems also differ on read-your-writes. In pgvector a row is visible to queries the moment its
transaction commits, like any other row. Dedicated engines acknowledge a write before, or separately
from, making it searchable: Qdrant's upsert takes a `wait` flag that decides whether the call returns
only after the change is applied; Milvus exposes consistency levels (Strong, Bounded, Session,
Eventually) per collection or query; Pinecone documents its indexes as eventually consistent, with a
short delay before new records appear. This matters for pipelines that upsert and then immediately
verify counts or run a test query, and for user flows such as "upload a document, then ask about it",
where the first question can miss the document the user just added.

### What "scale" means here
Memory is the first wall: vectors plus the HNSW graph must be in RAM for low latency. As an illustrative
order of magnitude, ten million 1536-dimensional float32 vectors are about 61 GB before the graph.
Beyond one node, engines shard by id hash and replicate shards for reads; disk-based indexes trade
latency for capacity; quantization shrinks memory by 4× (int8) to 32× (binary), or more with product quantization, usually
with full-precision rescoring to recover recall. The second wall is
write throughput: HNSW inserts are expensive, so bulk loads should finish before the index is built.

## Example
pgvector end to end: a chunk table whose id is the content hash from the embedding pipeline, an HNSW
index on cosine distance, a tenant filter, and the query-time recall knob. The index parameters shown are
pgvector's defaults; raising `hnsw.ef_search` increases recall at the cost of latency.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE chunks (
  id         text PRIMARY KEY,          -- content hash from the embedding pipeline
  doc_id     text NOT NULL,
  tenant_id  text NOT NULL,
  model      text NOT NULL,             -- which embedding model produced the vector
  body       text NOT NULL,
  embedding  vector(1536) NOT NULL      -- dimension is fixed per column
);

-- Bulk load first, then build the index: HNSW construction is the slow, memory-hungry step.
CREATE INDEX chunks_embedding_hnsw ON chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX chunks_tenant_idx ON chunks (tenant_id);

-- Query time: wider graph search for better recall (default 40).
SET hnsw.ef_search = 100;
-- pgvector 0.8.0+: keep scanning the graph until LIMIT rows survive the WHERE clause.
SET hnsw.iterative_scan = relaxed_order;

SELECT id, doc_id, 1 - (embedding <=> $1::vector) AS cosine_similarity
FROM chunks
WHERE tenant_id = $2 AND model = 'text-embedding-3-small'
ORDER BY embedding <=> $1::vector       -- must use the same operator the index was built for
LIMIT 10;
```

The `model` predicate is what makes an in-place model migration safe inside a single table, as long as
the new model has the same dimension (a different one needs a new column or table, which is the
blue/green route); during the migration it is also a filter that can shrink HNSW results, as described
above. The `ORDER BY` operator must match the index's operator class or Postgres falls back to a
sequential scan.

## Trade-offs
- pgvector gives one system, transactions and joins against business data, but index builds are slow,
  scaling is vertical, and filtered recall needs care; the trade flips toward a dedicated engine when the
  corpus reaches tens of millions of vectors or filtered queries dominate the load.
- A dedicated open-source engine (Qdrant, Weaviate, Milvus) generally delivers stronger filtered search,
  quantization and horizontal scale, but is another stateful system to run, upgrade and back up; without
  a platform team that burden flips the decision toward managed.
- A managed service (Pinecone, Qdrant Cloud, Zilliz Cloud, Weaviate Cloud) removes operations entirely,
  at usage-based pricing (storage plus reads and writes, model varies by vendor), a data-residency
  question and an exit cost; strict residency rules or a
  large, rarely-queried corpus flip it back toward self-hosting.
- An embedded store (Chroma, Milvus Lite, LanceDB) needs no infrastructure and is perfect for notebooks,
  tests and demos, but offers no concurrency, replication or multi-node growth; the first multi-user
  deployment flips it.
- Keeping chunk text in the payload makes retrieval one hop but inflates memory and backups; a pointer
  to a document store is leaner at the cost of a second lookup.

## Pitfalls
- A filtered query returns fewer than k results or none while the unfiltered one works; cause:
  post-filtering inside the index scan. Fix with a higher `ef_search`, iterative scans, a store with
  filterable HNSW, or partitioning by the filter key.
- Declaring a `vector(3072)` column and then creating an HNSW index on it; symptom: the index build
  fails on pgvector's 2,000-dimension limit. Use `halfvec(3072)` with `halfvec_cosine_ops`, or a
  truncated Matryoshka dimension, decided before the schema ships.
- Building the HNSW index before the bulk load; symptom: the load takes hours because every insert
  updates the graph. Load first, index after.
- Measuring recall only at the default query-time parameters; symptom: great benchmark numbers and
  missing results in production once filters and real queries arrive.
- Treating the vector store as the source of truth for documents; symptom: after a bug in chunking or a
  model upgrade nobody can rebuild the index. Keep documents elsewhere and treat the index as derived.
- Letting soft-deleted vectors accumulate; symptom: memory grows and recall degrades until the collection
  is compacted or re-indexed.
- Querying with a different distance operator than the index was built for; symptom: latency jumps from
  milliseconds to seconds because the planner chose a sequential scan.
- One giant multi-tenant collection with a highly selective tenant filter; symptom: small tenants get
  empty or slow results. Use namespaces, per-tenant shards or partitions.

## When to use
If the application already runs on Postgres and the corpus is in the low millions of vectors, start with
pgvector: no new system, transactional updates alongside business data, row-level security for ACL-aware
retrieval. Move to Qdrant, Weaviate or Milvus when filtered recall, write throughput or corpus size
outgrow one Postgres node, or when you need quantization and hybrid sparse-dense search built in. Choose
Pinecone or another managed service when there is no one to operate a database and data residency
allows it. Use Chroma for notebooks, tests and demos, and plan to leave it. Measure recall@k at your
real filters, p95 latency at target QPS, index build time and memory per million vectors. In interviews,
the expected depth is explaining filtered-search behaviour and sizing memory from vector count and
dimension, not reciting vendor features.

## Role lens
- **Engineer:** Chooses index parameters, writes the filtered queries, tunes `ef_search` against measured
  recall, and schedules compaction so deletes do not silently erode results.
- **Architect:** Owns the build-versus-buy and single-system-versus-dedicated decision, the multi-tenancy
  model, and the sizing from vector count, dimension and QPS to nodes and cost.
- **FDE:** Fits the store to the customer's constraints: existing Postgres estate, data-residency rules,
  on-prem versus cloud, and the operations team that will inherit it after the engagement.

## Check yourself
**Q:** A startup runs its product on Postgres, has two million chunks, needs per-tenant filtering and
expects about 50 queries per second. Do they need a dedicated vector database, and what would make you
change that answer later?
**A:** Not yet. Two million 1536-dimensional vectors are about 12 GB raw, which fits a mid-sized Postgres
instance with an HNSW index, and 50 QPS is within reach with `ef_search` tuned against measured recall.
pgvector keeps chunks transactionally consistent with tenant tables and lets row-level security enforce
tenant isolation, which is a real security advantage. The risks to watch are filtered recall for small
tenants, index build time during re-indexing, and memory as the corpus grows. I would move to a dedicated
engine or a managed service when the corpus passes tens of millions of vectors, when filtered queries
start returning short result lists, when write bursts from re-indexing hurt the production database, or
when hybrid sparse-dense search and quantization become requirements.

## Quiz
### Q: Why can a filtered nearest-neighbour query return fewer results than requested even though many rows match the filter?
- [ ] The vector index stores only a sample of the rows
- [x] The engine runs the approximate search first and applies the filter afterwards, so matching rows outside the retrieved candidates are never seen
- [ ] Filters disable the index and force an exact scan that times out
- [ ] Metadata filters are only applied to the first shard
> Post-filtering, which is what a plain pgvector HNSW scan does, means the ANN index returns its
> nearest candidates and the filter removes those that do not match. If the filter is selective, few candidates survive. Filterable HNSW,
> iterative scans or partitioning by the filter key are the fixes.

### Q: Which statement best captures the difference between pgvector and a dedicated engine such as Qdrant or Milvus?
- [ ] pgvector uses exact search while dedicated engines use approximate search
- [ ] Dedicated engines produce better embeddings
- [x] pgvector gives transactions and joins inside the database you already run, while dedicated engines offer filter-aware indexing, quantization and horizontal scaling at the cost of operating another system
- [ ] pgvector cannot store metadata alongside vectors
> Retrieval quality comes from the embedding model and the index parameters, which all of them support.
> The real difference is operational: consolidation and transactional consistency on one side, scale and
> specialised features on the other.

### Q: A team builds the HNSW index, then bulk-inserts ten million vectors, and the load takes many hours. What should they have done?
- [ ] Used a lower-dimensional embedding model
- [x] Loaded the vectors first and built the index afterwards, because each insert into an existing graph is expensive
- [ ] Used cosine distance instead of inner product
- [ ] Split the data across more collections
> HNSW inserts search the graph to place every new node and update neighbour links, which is far slower
> than building the graph in one pass over loaded data. Bulk load, then build, is the documented
> sequence for pgvector, and engines such as Qdrant let you defer indexing during bulk upload for the
> same reason.
