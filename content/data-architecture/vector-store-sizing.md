---
id: vector-store-sizing
module: data-architecture
name: Vector store sizing
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - data-architecture/vector-database-architecture
  - data-architecture/embedding-versioning-re-indexing-strategy
  - rag-retrieval/ann-indexes
  - rag-retrieval/embedding-dimension-and-normalization
  - rag-retrieval/chunking-strategies
  - infra-capacity-economics/capacity-planning
  - infra-capacity-economics/cost-modeling
  - serving-inference/kv-cache-memory-estimation
sources:
  - Jégou et al., 2011 — Product Quantization for Nearest Neighbor Search
  - Kusupati et al., 2022 — Matryoshka Representation Learning
  - Official docs: hnswlib
  - Official docs: Qdrant
---

## TL;DR
Vector store cost is set by RAM-resident bytes: `vectors × (dim × bytes_per_dim + graph_overhead +
payload)`, multiplied by replicas and a headroom factor for index builds. Every input is knowable
before you pick a vendor tier: vector count from documents and chunking, dimension and precision from the
embedding model and quantization, payload from what you choose to store beside the vector. Do the
arithmetic first; it routinely flips the decision between pgvector on an existing database and a
dedicated cluster, and it is the same discipline as KV-cache sizing in serving.

## Why it exists
Teams size vector stores the way they size nothing else: by picking a managed tier that "looks big
enough" or an instance with the most RAM they can justify. Both fail. Under-sizing shows up as
out-of-memory during a background index build, p99 latency jumping when the index spills out of memory,
or a managed plan that silently caps vectors so the last 30 % of the corpus never lands. Over-sizing
shows up as a five-figure monthly bill for a corpus that would fit in a Postgres replica.

The inputs are not mysterious. Vector count is documents times chunks per document, and chunks per
document follows from chunk size and overlap. Bytes per vector is the embedding dimension times the
storage precision. Index overhead is a function of the graph parameters. Payload is whatever text and
metadata you decided to keep in the engine. What makes the estimate wrong in practice is omission: counting
documents instead of chunks (a 10× error), forgetting that the ANN graph needs memory of its own, storing
the full chunk text beside each vector and discovering text dominates, or forgetting that a re-embedding
migration doubles the collection for its duration. This lesson is the checklist and the arithmetic, so
that the number comes from the corpus rather than from a pricing page.

## How it works
### Step 1: count vectors
`vectors = documents × chunks_per_document × vectors_per_chunk`. With token-based chunking,
`chunks_per_document ≈ tokens_per_document / (chunk_tokens − overlap_tokens)`, rounded up per document.
`vectors_per_chunk` is 1 for standard dense retrieval, more than 1 if you embed multiple views (title plus
body, or a summary) and much more for late-interaction models that store one vector per token. Add the
growth rate: a corpus adding 2 % per month is 27 % larger in a year.

### Step 2: bytes per vector
`dim × bytes_per_dim`: float32 is 4 bytes per dimension, float16 is 2, scalar int8 quantization is 1, and
binary quantization is 1 bit (one eighth of a byte). Product quantization compresses to a fixed number of
bytes per vector, commonly tens of bytes. Dimension comes from the model: common text embedding models
sit between 384 and 3,072 dimensions, and Matryoshka-style models let you truncate to a smaller prefix
at a quality cost you must measure. Quantized indexes usually keep the full-precision vectors on disk for
rescoring, so disk is not freed, only RAM.

### Step 3: index overhead
HNSW stores neighbour lists. With degree parameter `M`, the base layer holds up to `2M` links per node
and each link is a 4-byte id, so link storage is about `2 × M × 4` bytes per vector plus a small amount
for upper layers; `M = 16` gives roughly 128 bytes per vector. The hnswlib parameter guide puts the
link memory at roughly `M × 8–10` bytes per element, which matches this arithmetic; engines add their own
per-point bookkeeping (ids, versions, locks) on top, so treat it as a lower bound. IVF adds only centroids and posting
lists of ids, which is negligible next to the vectors.

### Step 4: payload and secondary indexes
Everything stored beside the vector costs memory in most engines: the id, filterable fields and their
indexes, and the chunk text if you keep it there. Text is the surprise: at roughly 4 characters per token
(an English-prose approximation), a 400-token chunk is about 1.6 KB, larger than an int8-quantized
1,024-dimensional vector. If memory is the constraint, keep text and rich metadata in Postgres or object
storage keyed by `chunk_id` and store only ids and filter fields with the vector.

### Step 5: replicas and headroom
Multiply by the replication factor for availability. Then add headroom: segment merges and index rebuilds
temporarily hold two copies of a segment, and operating at the edge of RAM turns every compaction into an
incident. A factor of about 1.5 is a common rule of thumb. Note what it is applied to: Qdrant's capacity
guidance multiplies raw vector bytes by 1.5 to cover index structures, metadata and temporary segments
during optimization together, whereas this lesson itemizes links and payload first and then applies 1.5×
as build headroom, which is the more conservative reading. Treat either as a floor, not a target.

### Step 6: throughput and shards
Memory gives one lower bound on node count; queries per second give another. Per-node throughput depends
on dimension, `ef`, filter selectivity and hardware, so it must be measured with your own vectors and
filters, not taken from a benchmark. `nodes = max(ceil(total_RAM / RAM_per_node), ceil(peak_QPS /
QPS_per_node))`, sized at peak rather than average, within the latency budget the product allows retrieval.

### Step 7: decide what must be RAM-resident, then size the disk
Not every byte has to be in RAM, and the line between "must be" and "may be" is set by access pattern.
The graph links and whatever vectors the traversal compares against are read at random on every hop,
so they must be resident; if they are memory-mapped from disk and the working set falls out of the page
cache, latency rises by orders of magnitude. Full-precision vectors used only for rescoring are read a
few dozen to a few hundred times per query (k times the oversampling factor), so on an SSD they can live
on disk. Payload is fetched only for the final k. Engines expose this as per-collection settings (for
example on-disk vectors or payload in Qdrant, with the quantized copy kept in RAM). Then size the disk
separately: full-precision vectors, the quantized copy, the write-ahead log, and snapshots or backups,
which are complete copies. Disk is cheap but not free, and the snapshot is what you restore from.

### Step 8: migrations
A change of embedding model means the new vectors coexist with the old until cutover. Budget for the
collection to double during that window, or plan to migrate tenant by tenant so the overlap is bounded;
the re-indexing lesson in this module covers the strategy.

## Example
A worked scenario, then a calculator you can keep.

A knowledge base has 200,000 documents averaging 4,000 tokens. Chunks are 400 tokens with 50 overlap, so
each document yields about 4,000 / 350 ≈ 11.4 chunks; with rounding per document call it 12, giving
2.4 million vectors. The embedding model is 1,024-dimensional.

- Vectors in float32: 2.4 M × 4,096 B ≈ 9.8 GB.
- HNSW links at `M = 16`: 2.4 M × 128 B ≈ 0.3 GB.
- Payload with chunk text (≈ 1.6 KB) plus ids and filter fields (≈ 0.4 KB): 2.4 M × 2 KB ≈ 4.8 GB.
- Steady state per replica ≈ 14.9 GB; with 1.5× headroom ≈ 22 GB; two replicas ≈ 45 GB of RAM in total.

Now change two decisions. Scalar int8 quantization makes the vectors 2.4 M × 1,024 B ≈ 2.5 GB, so the
in-memory total becomes roughly 2.5 + 0.3 + 4.8 ≈ 7.6 GB, and text is now the biggest line. Move text out
to the chunk table and keep 200 B of ids and filters: 2.5 + 0.3 + 0.5 ≈ 3.3 GB, about 5 GB with headroom
per replica. The same corpus went from needing large-memory nodes to fitting on one modest node, with
full-precision vectors (9.8 GB) kept on disk for rescoring. Whether a given engine can do this exact
configuration varies: pgvector, for instance, offers half-precision (`halfvec`, 2 bytes per dimension) and
binary (`bit`) types rather than int8, so its nearest equivalent is about 4.9 GB of vectors, still small
enough for an existing database host. All figures are illustrative order-of-magnitude arithmetic; measure
recall after quantizing.

Push one step further to see the next bottleneck. Binary quantization stores 1,024 bits, which is 128
bytes per vector, so the vectors take 2.4 M × 128 B ≈ 0.3 GB. That is the same as the HNSW links. From
here, shrinking vectors further barely helps, because the graph and the per-point ids and filter fields
now dominate. The remaining levers are a smaller `M` (fewer links, lower recall), or IVF-style or
disk-based indexes whose overhead is not per-link. Binary codes also lose more recall than int8, so they
are normally used with heavier oversampling and rescoring from full-precision vectors on disk. Each
lever you pull exposes the next term of the formula as the largest one.

```python
import math

def vector_store_bytes(docs, tokens_per_doc, chunk_tokens, overlap, dim,
                       bytes_per_dim=4.0, M=16, payload_bytes=2048, replicas=2, headroom=1.5):
    chunks_per_doc = math.ceil(tokens_per_doc / (chunk_tokens - overlap))
    n = docs * chunks_per_doc
    vec = n * dim * bytes_per_dim
    links = n * 2 * M * 4                      # base-layer HNSW neighbour ids
    payload = n * payload_bytes
    per_replica = (vec + links + payload) * headroom
    return {"vectors": n, "vector_gb": vec / 1e9, "links_gb": links / 1e9,
            "payload_gb": payload / 1e9, "ram_per_replica_gb": per_replica / 1e9,
            "cluster_ram_gb": per_replica * replicas / 1e9}

base  = vector_store_bytes(200_000, 4_000, 400, 50, 1024)
lean  = vector_store_bytes(200_000, 4_000, 400, 50, 1024, bytes_per_dim=1.0, payload_bytes=200)
for name, r in (("float32 + text in engine", base), ("int8 + ids only", lean)):
    print(f"{name}: {r['vectors']:,} vectors, {r['ram_per_replica_gb']:.1f} GB per replica, "
          f"{r['cluster_ram_gb']:.1f} GB cluster")
```

Run it with your own corpus numbers before any vendor conversation; the two scenarios print about 22.6 GB
and 4.9 GB per replica, a factor of roughly 4.6 in RAM for the same retrieval corpus.

## Trade-offs
- Quantization cuts RAM by 4× (int8) to 32× (binary) and speeds traversal, but costs recall that must be
  measured per model; for a collection that fits unquantized with headroom, it is complexity for nothing.
- Keeping chunk text in the engine makes retrieval a single call and simplifies the stack, but text often
  outweighs quantized vectors; move it out when memory, snapshot time or rebalance time are the constraint.
- Larger chunks mean fewer vectors and a smaller index, but retrieval precision usually falls as chunks
  grow; size for the chunking the retrieval evals chose, not the other way round.
- Reducing dimension (a smaller model or a Matryoshka prefix) shrinks memory linearly, at a quality cost
  that is small for some models and tasks and large for others; it is a measured decision, not a default.
- More replicas buy availability and read throughput at linear cost; a single replica with a fast
  rebuild from the chunk table is acceptable for internal tools and wrong for a customer-facing SLA.

## Pitfalls
- Counting documents instead of chunks; the symptom is a cluster sized for 200,000 vectors receiving 2.4
  million and failing to load the index.
- Forgetting graph links and merge headroom; the symptom is out-of-memory kills during background
  compaction or index rebuild rather than under query load.
- Ignoring payload size; the symptom is RAM dominated by text with an index that is a fraction of it, and
  slow snapshots and rebalances.
- Sizing throughput on average QPS; the symptom is p99 latency collapsing during the daily peak while
  dashboards show healthy averages.
- Assuming a spill to disk is a graceful degradation; the symptom is latency rising by orders of
  magnitude once the graph no longer fits, because traversal is random access.
- Not budgeting for the migration window; the symptom is a model upgrade blocked because the cluster
  cannot hold both embedding versions at once.

## When to use
Do this arithmetic whenever a vector store is being chosen, a tier is being upgraded, a chunking or
embedding change is proposed, or a tenant is being onboarded with a known corpus. Measure the real
chunks-per-document distribution from a sample rather than assuming the mean, measure recall before and
after any quantization, and load-test QPS with production-shaped filters. The number tells you whether
pgvector in the existing database suffices, whether quantization is worth its recall cost, and what the
managed-service bill will be at the chosen precision. In interviews, "how big a vector store does this
corpus need" is answered by walking the eight steps with explicit assumptions and then naming the two
levers, quantization and payload placement, that change the answer most.

## Role lens
- **Engineer:** Computes vectors, bytes and overhead from the pipeline's real chunk statistics, chooses
  quantization and payload placement, and verifies recall and peak QPS on a staging cluster.
- **Architect:** Owns the capacity model and the cost line for retrieval, decides between an extension
  and a dedicated engine on the basis of the estimate, and budgets memory for re-embedding migrations.
- **FDE:** Sizes a customer deployment from their document count and page statistics during scoping, and
  explains why the quoted tier changes when they add a second content source or a larger embedding model.

## Check yourself
**Q:** A customer wants to index 1.5 million support tickets averaging 300 tokens with a 1,536-dimensional
float32 embedding, one chunk per ticket, on a managed service, and they want the full ticket text
returned with each hit. Estimate the memory, then show how you would cut it by more than half without
changing the embedding model or the retrieval results they see.
**A:** One chunk per ticket gives 1.5 million vectors. Float32 at 1,536 dimensions is 6,144 bytes per
vector, so about 9.2 GB of vectors; HNSW links at `M = 16` add about 128 bytes each, roughly 0.2 GB; ticket
text at 300 tokens is about 1.2 KB plus metadata, roughly 2 GB. Steady state is about 11.4 GB, around
17 GB with 1.5× headroom per replica, and double that for two replicas. To cut it: store the vectors with
scalar int8 quantization, bringing vectors to about 2.3 GB while keeping float32 copies on disk for
rescoring so returned results are effectively unchanged, and move ticket text out of the engine into the
ticket database keyed by id, fetched after retrieval. The in-memory total drops to roughly 2.3 + 0.2 +
small metadata, under 3 GB before headroom, which is a quarter of the original. Then measure recall on a
labelled query set to confirm quantization did not change the top-k, and load-test at peak QPS.

## Quiz
### Q: Which omission most often causes a vector store to be under-provisioned by an order of magnitude?
- [ ] Forgetting that cosine similarity needs normalized vectors
- [x] Counting documents rather than chunks when estimating the number of vectors
- [ ] Using float16 instead of float32 in the estimate
- [ ] Ignoring the number of HNSW layers
> A document with a few thousand tokens becomes ten or more chunks, each with its own vector. Estimates
> that start from document count miss this multiplier entirely; precision and graph layers change the
> total by much smaller factors.

### Q: After enabling int8 scalar quantization, why does disk usage not fall (it even grows slightly) while vector RAM drops roughly 4×?
- [ ] The engine compresses the payload instead of the vectors
- [ ] Quantization only applies to the HNSW links
- [x] The quantized copy replaces float32 in memory, but full-precision vectors are kept on disk for rescoring the final candidates
- [ ] Disk usage is always dominated by the write-ahead log
> Quantization trades precision during graph traversal for memory, and engines recover accuracy by
> re-ranking the shortlist against the original vectors. Those originals must still exist, so disk holds
> both copies while RAM holds only the small one.

### Q: Why should the capacity estimate include a headroom factor beyond steady-state bytes?
- [ ] Because object storage charges per request
- [ ] Because embedding dimensions grow over time
- [x] Because segment merges and index rebuilds temporarily hold additional copies, and a cluster at the edge of RAM fails during those operations
- [ ] Because replicas must each hold the full write-ahead log
> Background maintenance builds a new segment or graph before releasing the old one. Without free memory
> for that overlap, compaction and re-indexing trigger out-of-memory failures even though steady-state
> queries fit.
