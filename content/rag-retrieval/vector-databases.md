---
id: vector-databases
module: rag-retrieval
name: Vector databases (pgvector, Qdrant, Pinecone, Weaviate, Milvus, Chroma)
kind: tool
difficulty: core
est_minutes: 16
status: agentic
agent_relevance: core
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
A vector database is where your agent's knowledge and long-term memory live: embeddings plus metadata,
returning the k nearest under filters at request speed. pgvector, Qdrant, Weaviate, Milvus, Pinecone and
Chroma differ on operations, filtering and scale, not retrieval quality.

## For agent builders
- **Why it matters for agents.** Every search-tool call and memory recall is a filtered nearest-neighbour
  query on the agent's critical path, often several per task. Per-user memory is a very selective filter,
  and "save, then recall on the next step" needs read-your-writes; both are where stores differ most.
- **Where it shows up in your stack.** LangChain and LlamaIndex vector store integrations (PGVector,
  Qdrant, Pinecone, Weaviate, Milvus, Chroma), the store behind a LangGraph agent's long-term memory, and
  managed "file search" features that hide a vector store behind a tool.
- **What you control.** Which store, the collection schema and metadata (user, tenant, ACL, model),
  filter indexes, `ef_search`, consistency on writes, and the token budget of what the tool returns.
- **What you can skip.** HNSW internals and vendor feature matrices. Know filtered-search behaviour,
  read-your-writes and the memory sizing sum.

## Why it exists
- **Exact search is O(N·d) per query.** Ten million 1536-dimensional vectors is roughly 15 billion
  multiply-adds and about 61 GB of float32 streamed from memory per query: fine for a nightly batch,
  hopeless on a CPU node at 100 queries per second with a 50 ms budget.
- **ANN indexes fix speed, not operations.** HNSW and IVF cut the work to a small fraction of the corpus
  at a modest recall cost, but FAISS or hnswlib give an in-memory index with no persistence, updates
  (graph indexes handle deletes awkwardly), text and metadata storage, filtering, replication or
  sharding. Teams starting with "FAISS and a pickle file" rebuild each after an incident.
- **Plain relational arrays scan every row.** Storing float arrays and ordering by computed distance is
  exact and simple, but somewhere between hundreds of thousands and a few million vectors (illustrative;
  it depends on dimension, hardware and latency budget) it stops fitting an interactive request.
- **So a vector database packages ANN indexing with database properties:** durability, CRUD, filtering,
  access control and horizontal scale, which an agent's memory needs as much as its knowledge base.

## How it works
### The common core
- **Collection or table** with a fixed dimension and a distance metric.
- **Records:** an id, a vector and a payload of metadata.
- **ANN index:** almost always HNSW, with IVF, product-quantization and disk-oriented variants for larger
  corpora.
- **Filtered search, CRUD** with soft-deletes compacted later, **durability** through a write-ahead log
  and snapshots, optional **quantization**, and an HTTP or gRPC API.
- **Tuning:** `M` and `ef_construction` at build time, `ef_search` or `nprobe` at query time. Higher
  recall always costs more latency, paid on every agent search step.

### Filtering is where they differ
| Strategy | How | Failure |
|---|---|---|
| Pre-filtering | restrict to matching rows, then search them | exact but slow when the filter is loose |
| Post-filtering | ANN top-k first, then drop non-matching rows | a selective filter leaves fewer than k, or none |
| Filter in traversal | Qdrant's filterable HNSW; brute-force fallback when very selective | engine-specific behaviour to learn |

- **pgvector post-filters:** it applies the `WHERE` clause to rows coming out of the index scan, so a
  filtered query can return fewer rows than `LIMIT` asks for.
- **pgvector 0.8.0 added opt-in iterative index scans** (`hnsw.iterative_scan`) that keep walking the
  graph until enough matching rows are found, up to the cap set by `hnsw.max_scan_tuples`, so a very
  selective filter can still come back short.
- **`relaxed_order` versus `strict_order`:** relaxed is faster but may return rows slightly out of
  distance order, so re-sort in an outer query (pgvector's docs use a materialized CTE) when order matters.
- **Knowing your store's behaviour** is the key to debugging "the filtered query returns nothing", which
  an agent experiences as "no memories found" for a user who has plenty.

### Agent memory is the extreme filter
- **One user's memories are a tiny slice** of a shared collection, so post-filtering an ANN scan almost
  never finds them.
- **Small slices are cheap to search exactly.** A few thousand vectors per user can be scored directly
  after selecting them by an indexed user field; in Postgres the planner may pick the B-tree on the user
  column and compute distances for just those rows.
- **Or isolate by design:** a namespace, partition or per-tenant shard per user group, or a store whose
  filter-aware traversal or brute-force fallback handles very selective filters.

### The six, briefly
- **pgvector:** a Postgres extension with a `vector(d)` column, operators `<->` (L2), `<=>` (cosine
  distance) and `<#>` (negative inner product), and `ivfflat` and `hnsw` indexes; without an index it
  does an exact scan. Strength: transactions, joins against real tables, row-level security, one backup,
  a team that already runs it. Limits: single-node scaling, index build time and memory, filtered recall.
- **pgvector sharp edges:** HNSW and IVFFlat indexes on `vector` accept at most 2,000 dimensions, so a
  3,072-dimensional model needs `halfvec` (indexable up to 4,000) or truncation. There is no built-in
  BM25, so hybrid search fuses with Postgres full-text search (whose `ts_rank` is a different scoring
  function) or adds another extension.
- **Qdrant:** open source, written in Rust. HNSW with payload-aware filtering in the traversal; scalar,
  binary and product quantization; named and sparse vectors per point for hybrid search; REST and gRPC;
  sharding and replication in distributed mode; a managed cloud.
- **Weaviate:** open source, written in Go. HNSW, optional modules that call an embedding model at write
  and query time, built-in BM25 with hybrid score fusion, GraphQL and REST APIs, and multi-tenancy with
  per-tenant shards.
- **Milvus:** open-source distributed system separating compute from storage: segments in object storage,
  metadata in etcd, a log service as write-ahead log (historically Pulsar or Kafka; varies by release).
  Many index types (IVF variants, HNSW, DiskANN, GPU).
- **Milvus at scale:** it targets billions of vectors and is heavier to operate; Milvus Lite embeds in
  Python for development, Zilliz Cloud is the managed version.
- **Pinecone:** fully managed, proprietary; serverless and pod-based deployments, namespaces for tenant
  isolation, metadata filtering, sparse-dense hybrid search. Zero operations; you pay per usage and data
  and index live with the vendor.
- **Chroma:** open source, Python/JS-first; embedded in the app process with on-disk persistence or as a
  small server; collections with `add`, `query` and `where` filters over an HNSW index. Single-node mode
  suits prototypes and tests, not high concurrency or multi-node.
- **Chroma hosted:** the hosted service is a separate, distributed offering; check its current
  capabilities rather than assuming.
- **Adjacent options:** FAISS and hnswlib (libraries, not databases), Elasticsearch and OpenSearch kNN
  (if you already run them for lexical search), Redis, LanceDB and Vespa.

### When a write becomes searchable
| System | Read-your-writes behaviour |
|---|---|
| pgvector | visible the moment its transaction commits, like any row |
| Qdrant | upsert takes a `wait` flag: return only after the change is applied, or earlier |
| Milvus | consistency levels per collection or query: Strong, Bounded, Session, Eventually |
| Pinecone | documented as eventually consistent; a short delay before new records appear |

This matters for pipelines that upsert then immediately verify counts or run a test query, for "upload
a document, then ask about it" flows, where the first question can miss the new document, and for agents
that save a memory and try to recall it one step later.

### What "scale" means here
1. **Memory is the first wall.** Vectors plus the HNSW graph must be in RAM for low latency; as an
   illustrative order of magnitude, ten million 1536-dimensional float32 vectors are about 61 GB before
   the graph.
2. **Beyond one node,** engines shard by id hash and replicate shards for reads; disk-based indexes trade
   latency for capacity.
3. **Quantization** shrinks memory 4× (int8) to 32× (binary), or more with product quantization, usually
   with full-precision rescoring to recover recall.
4. **Write throughput is the second wall.** HNSW inserts are expensive, so bulk loads should finish
   before the index is built. Agent memory writes trickle in continuously instead, so check sustained
   insert latency too.

## Diagram
A filtered query's path decides whether you get k results: post-filtering can starve a selective
tenant or user, while iterative scans, filter-aware traversal or a partition keep the result list full.

```mermaid
flowchart TD
    Q["Agent search: vector, filter, LIMIT k"] --> S{"How does the store filter?"}
    S -->|"post-filter"| PF["ANN gives ef candidates, WHERE drops"]
    S -->|"filter in traversal"| FT["HNSW walk skips non-matching"]
    S -->|"partition or namespace"| PT["Search only that tenant or user"]
    PF --> C{"At least k rows survive?"}
    C -->|"yes"| R["Top-k to agent"]
    C -->|"no, iterative scan on"| W["Keep walking the graph"]
    W --> C
    C -->|"no, iterative scan off"| SHORT["Fewer than k, or empty"]
    FT --> R
    PT --> R
```

## Examples
### pgvector end to end
A chunk table keyed by the pipeline's content hash, an HNSW index on cosine distance, a tenant filter and
the query-time recall knob. The index parameters shown are pgvector's defaults; raising
`hnsw.ef_search` increases recall at the cost of latency.

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

- **The `model` predicate** makes an in-place model migration safe inside one table, as long as the new
  model has the same dimension (a different one needs a new column or table: the blue/green route).
  During migration it is also a filter that can shrink HNSW results, as described above.
- **The `ORDER BY` operator must match the index's operator class**, or Postgres falls back to a
  sequential scan.

### Agent scenario: per-user memory tools on Qdrant
Save waits for the write to apply, so the next agent step can recall it; recall filters on an indexed
`user_id` payload field. `embed` is the shared embedding function.

```python
import uuid
from qdrant_client import QdrantClient, models

client = QdrantClient(url="http://localhost:6333")
client.create_payload_index("memories", field_name="user_id",
                            field_schema=models.PayloadSchemaType.KEYWORD)

def save_memory(user_id: str, text: str) -> str:
    pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{user_id}:{text}"))   # idempotent point id
    client.upsert("memories", wait=True, points=[models.PointStruct(
        id=pid, vector=embed(text), payload={"user_id": user_id, "text": text})])
    return "Saved."

def recall_memory(user_id: str, query: str, k: int = 5) -> list[str]:
    hits = client.query_points("memories", query=embed(query), limit=k,
        query_filter=models.Filter(must=[models.FieldCondition(
            key="user_id", match=models.MatchValue(value=user_id))])).points
    return [h.payload["text"] for h in hits]
```

- **`wait=True`** trades a little save latency for read-your-writes within the same agent run.
- **The payload index** lets Qdrant's filter-aware search handle a filter that matches a tiny slice.

### Why a small tenant gets empty results
A pure-Python simulation of post-filtering: the "small" tenant owns 1% of 10,000 rows, and the index
hands `ef` candidates to the `WHERE` clause.

```python
import random

random.seed(0)
N, k = 10_000, 10
rows = [{"id": i, "tenant": "small" if i % 100 == 0 else "big", "score": random.random()}
        for i in range(N)]                                   # "small" owns 1% of the rows
ranked = sorted(rows, key=lambda r: -r["score"])             # stand-in for the ANN candidate order

def post_filter(ef):                                         # index returns ef candidates, then WHERE
    return [r for r in ranked[:ef] if r["tenant"] == "small"][:k]

def iterative(ef=40):                                        # keep widening until k rows survive
    while len(post_filter(ef)) < k and ef < N:
        ef *= 2
    return ef, len(post_filter(ef))

pre = [r for r in ranked if r["tenant"] == "small"][:k]      # filter first, then rank: exact
print("post-filter, ef=40 :", len(post_filter(40)), "rows")   # 0 rows
print("post-filter, ef=100:", len(post_filter(100)), "rows")  # 2 rows
print("iterative scan      :", iterative(), "(candidates visited, rows)")  # (1280, 10)
print("pre-filter          :", len(pre), "rows")              # 10 rows
```

At 1% selectivity you expect about one survivor per 100 candidates, so `LIMIT 10` needs on the order of
1,000 candidates. That is why selective tenants need iterative scans, filter-aware traversal or their own
partition.

### Worked scenario: one user's memories in a shared collection
50,000 users with 2,000 memories each share one 100-million-row collection (illustrative).

| Quantity | Value |
|---|---|
| One user's share of rows | 2,000 ÷ 100,000,000 = 0.002% |
| Expected survivors from 100 post-filtered candidates | about 0.002, so effectively none |
| Candidates needed for 5 survivors | about 250,000 |
| Exact scan of that user's 2,000 vectors at 1536 dims | about 3 million multiply-adds, trivial |

So per-user memory wants pre-filtering by an indexed user field or isolation, not a bigger `ef_search`.

### Worked choice: three teams
| Situation | Pick | Because |
|---|---|---|
| Postgres app, 2M chunks, per-tenant filters, 50 QPS | pgvector | about 12 GB raw fits one node; row-level security for tenants |
| 80M chunks, heavy filtered traffic, platform team | Qdrant or Milvus self-hosted | filter-aware HNSW, quantization, sharding |
| No ops staff, residency allows a vendor | Pinecone or another managed service | zero operations, usage-based pricing |

## Production patterns
- **Tune `ef_search` against measured recall** — for a sample of real agent queries with their real
  filters, compute exact top-10 (for example with a brute-force scan offline) and compare with the index
  results at several `hnsw.ef_search` values. Pick the lowest that meets the recall target; monitor
  recall@10 and p95 latency together.
- **Bulk load, then index** — in pgvector, load rows, raise `maintenance_work_mem` for the session, then
  `CREATE INDEX` (parallel workers speed it up); in Qdrant, defer HNSW indexing during the upload via the
  collection's optimizer settings and restore them afterwards. Monitor build time and memory per
  million vectors.
- **Index the filter fields** — create payload indexes on user, tenant and date fields (Qdrant
  `create_payload_index`, a B-tree in Postgres) and give tiny tenants their own namespace or partition.
  Monitor result count below k per tenant, and empty memory recalls for users known to have memories.
- **Read-your-writes where users expect it** — pass `wait=True` on Qdrant upserts in "upload then ask"
  flows and agent memory saves, use Strong consistency in Milvus for verification queries, and poll
  before confirming in eventually consistent services. Monitor time from upsert to first successful query.
- **Treat the index as derived** — keep documents and chunk text in a source-of-truth store, snapshot the
  vector store on a schedule, and rehearse a full rebuild. Monitor soft-deleted fraction and compact or
  re-index before it erodes recall.
- **Budget search latency per agent step** — an agent that searches five times per task pays store p95
  five times. Trace each tool call (LangSmith or your own spans) and alert on store latency per step,
  not just per request.

## Trade-offs
- **pgvector consolidation vs scale.** One system, transactions and joins against business data, but
  slow index builds, vertical scaling and careful filtered recall; it flips toward a dedicated engine at
  tens of millions of vectors or when filtered queries dominate the load.
- **Dedicated open-source engine vs operating it.** Qdrant, Weaviate and Milvus generally give stronger
  filtered search, quantization and horizontal scale, but are another stateful system to run, upgrade
  and back up; without a platform team that burden flips the decision toward managed.
- **Managed vs self-hosted.** Pinecone, Qdrant Cloud, Zilliz Cloud and Weaviate Cloud remove operations,
  at usage-based pricing (storage plus reads and writes, model varies by vendor), a data-residency
  question and an exit cost; strict residency or a large, rarely-queried corpus flips it back.
- **Embedded vs server.** Chroma, Milvus Lite and LanceDB need no infrastructure and suit notebooks, tests
  and demos, but offer no concurrency, replication or multi-node growth; the first multi-user deployment
  flips it.
- **Waiting writes vs agent latency.** Read-your-writes makes a memory recallable on the next step but
  adds latency to the save tool; skip it only when the memory is not needed within the same run.
- **Text in the payload vs a pointer.** One hop for retrieval, but inflated memory and backups; a pointer
  to a document store is leaner at the cost of a second lookup.

## Pitfalls
- **Filtered query returns fewer than k, or none.** Cause: post-filtering inside the index scan. Fix
  with a higher `ef_search`, iterative scans, a store with filterable HNSW, or partitioning by the
  filter key.
- **Agent memory recall is empty for active users.** Cause: a per-user filter post-applied to a global
  ANN scan. Fix: index the user field, pre-filter, or partition memories by user group.
- **HNSW on a `vector(3072)` column.** The build fails on pgvector's 2,000-dimension limit. Use
  `halfvec(3072)` with `halfvec_cosine_ops`, or a truncated Matryoshka dimension, decided before the
  schema ships.
- **Building HNSW before the bulk load.** The load takes hours because every insert updates the graph.
  Load first, index after.
- **Measuring recall only at default query parameters.** Great benchmark numbers, then missing results
  once filters and real queries arrive.
- **Vector store as the source of truth.** After a chunking bug or model upgrade nobody can rebuild the
  index. Keep documents elsewhere and treat the index as derived.
- **Letting soft-deleted vectors accumulate.** Memory grows and recall degrades until the collection is
  compacted or re-indexed.
- **A different distance operator than the index.** Latency jumps from milliseconds to seconds because
  the planner chose a sequential scan.
- **One giant multi-tenant collection with a selective tenant filter.** Small tenants get empty or slow
  results. Use namespaces, per-tenant shards or partitions.

## When to use
- **Start with pgvector** when the app already runs on Postgres and the corpus is in the low millions:
  no new system, transactional updates alongside business data, row-level security for ACL-aware
  retrieval, and agent memory in the same database as users.
- **Move to Qdrant, Weaviate or Milvus** when filtered recall, write throughput or corpus size outgrow
  one Postgres node, or you need built-in quantization and hybrid sparse-dense search.
- **Choose Pinecone or another managed service** when no one can operate a database and data residency
  allows it.
- **Use Chroma** for notebooks, tests and demos, and plan to leave it.
- **Measure:** recall@k at your real filters, p95 latency at target QPS, index build time and memory per
  million vectors.
- **Interviews:** explain filtered-search behaviour and size memory from vector count and dimension,
  rather than reciting vendor features.

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

## Interview questions
### What does a vector database give you that FAISS or hnswlib alone does not?
- **Database properties around the ANN index:** persistence through a write-ahead log and snapshots,
  CRUD with soft-deletes, payload storage and filtering, access control, replication and sharding.
- **Why it matters:** a library index lives in memory with none of these, so teams that start with "FAISS
  and a pickle file" end up rebuilding each one after an incident.

### A small tenant's filtered queries return empty results while big tenants are fine. Walk me through it.
- **Diagnose:** the store post-filters, so at 1% selectivity about one row survives per 100 candidates and
  `LIMIT 10` needs on the order of 1,000 candidates.
- **Fix in pgvector:** raise `hnsw.ef_search` or enable `hnsw.iterative_scan` (0.8.0+).
- **Structural fix:** a store with filter-aware HNSW, a payload index on the tenant field, or a per-tenant
  namespace, shard or partition.

### Design the storage for long-term memory of an agent with 50,000 users. What do you watch for?
- **Schema:** one record per memory with vector, text, user id, timestamp and model, under a
  content-addressed id so repeated saves are idempotent.
- **Filtering:** each user is a tiny slice, so index the user field and pre-filter, or partition; a
  post-filtered global ANN scan returns nothing.
- **Consistency and latency:** wait for writes when the agent recalls within the same run, and budget
  recall latency per step because it repeats inside the loop.

### When would you not pick a dedicated vector database?
- **Already on Postgres, low millions of vectors:** pgvector adds no new system, keeps chunks
  transactional with business tables and lets row-level security enforce tenant isolation.
- **Prototype or tests:** Chroma, Milvus Lite or LanceDB embed in the process with no infrastructure.
- **No ops staff:** a managed service beats self-hosting an engine nobody can run, if data residency allows.

### Size the memory for 80 million 1536-dimensional chunks and say how you would fit it.
- **Raw float32:** 80M × 1536 × 4 bytes ≈ 491.5 GB before the HNSW graph and payloads.
- **Quantize:** int8 is about 122.9 GB, binary about 15.4 GB, with full-precision rescoring to recover
  recall.
- **Scale out:** shard by id hash across nodes with replicas for reads, or use a disk-based index and
  accept higher latency.

### Your agent saves a user preference, then one step later its recall tool cannot find it. Why, and what do you change?
- **Cause:** the store is not read-your-writes for that path, for example eventually consistent in
  Pinecone or a Qdrant upsert sent without waiting.
- **Fix:** pass `wait=True` on Qdrant upserts, use Strong consistency in Milvus, or poll until the record
  is queryable before the save tool returns.
- **Monitor** the time from upsert to first successful query, and the same flow for user document uploads.

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
