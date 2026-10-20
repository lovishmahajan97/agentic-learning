---
id: ann-indexes
module: rag-retrieval
name: ANN indexes (HNSW, IVF)
kind: concept
difficulty: core
est_minutes: 17
status: fact-checked
related:
  - rag-retrieval/similarity-search
  - rag-retrieval/vector-databases
  - rag-retrieval/metadata-filtering
  - rag-retrieval/recall-vs-precision-trade-off
  - data-architecture/vector-database-architecture
  - data-architecture/vector-store-sizing
  - evals/retrieval-evaluation
sources:
  - Malkov & Yashunin, 2018 — Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs
  - Jégou, Douze & Schmid, 2011 — Product Quantization for Nearest Neighbor Search
  - Official docs: FAISS
  - Official docs: pgvector
---

## TL;DR
Exact nearest-neighbor search reads every vector per query, which stops fitting a latency budget
somewhere in the millions. Approximate nearest neighbor (ANN) indexes give up a few percent of recall for
sublinear search. HNSW builds a layered proximity graph and walks it greedily; IVF clusters the space
with k-means and scans only the closest clusters, often with product quantization to shrink the vectors.
The knobs (ef, nprobe) move you along a recall–latency curve you must measure, not assume.

## Why it exists
A brute-force scan is O(N·d) per query and, more to the point, reads the entire vector matrix from memory
each time. At one million 768-dimensional fp32 vectors that is 3 GB per query; at one hundred million it
is 300 GB, which no single machine streams in the tens of milliseconds a chat product can spare for
retrieval. Throwing threads at it helps linearly; the corpus grows faster.

Classical spatial indexes (k-d trees, R-trees) do not rescue you. They prune by splitting coordinates,
and in a few hundred dimensions nearly every split boundary is close to every point, so the tree visits
most leaves anyway. This is the curse of dimensionality in its practical form: exact sublinear search in
high dimensions is, for realistic data, not much better than scanning.

The way out is to stop demanding exactness. Retrieval already feeds an LLM that tolerates imperfect
context, and the embedding itself is a lossy proxy for relevance, so losing a couple of percent of true
neighbors usually costs nothing measurable downstream. Once approximation is allowed, two families of
structure become possible: navigable graphs that let a search hop toward the query in a logarithmic
number of steps, and partitioned spaces that let it examine only a handful of cells. HNSW and IVF are the
production representatives of each.

## How it works
### HNSW: a skip list over a proximity graph
HNSW keeps every vector as a node in a graph whose edges connect near neighbors. Searching such a graph
is greedy: start at some node, look at its neighbors, move to whichever is closest to the query, repeat
until no neighbor improves. The graph is built so that this walk converges, using two ideas.

1. **Hierarchy.** Each node is assigned a random maximum layer with exponentially decaying probability,
   so layer 0 holds everything and higher layers hold a sparse sample. A search starts at the top layer,
   where edges are long and a few hops cross the whole space, and descends one layer at a time, using
   the best node found so far as the entry point to the next. This is the skip-list trick, and it is
   what makes search cost grow roughly logarithmically with N instead of linearly.
2. **Beam search with ef.** At layer 0 the walk keeps a candidate heap of size `ef` rather than a single
   best node, exploring the `ef` closest unvisited nodes before stopping. Larger `ef` means more distance
   computations and higher recall; `ef` must be at least k. There are two settings: `efConstruction`,
   used while inserting (affects graph quality), and `efSearch`, used at query time (the live
   recall–latency knob).

The other build parameter is `M`, the number of links each node keeps per layer (layer 0 commonly allows
2M). When a new node is inserted, the index runs a search to find its nearest `efConstruction`
candidates, then selects `M` of them with a heuristic that prefers neighbors in different directions over
a cluster of near-duplicates, because diverse edges are what keep the graph navigable. Links are
bidirectional; if a neighbor now exceeds its limit, its weakest link is pruned.

Memory is the price. Each node stores its vector plus roughly 2M 4-byte neighbor ids at layer 0: with
M = 16 that is about 128 bytes of graph per vector on top of the 3 KB vector itself, and the whole
structure must live in RAM, since greedy traversal is random access. Deletes are usually tombstones, not
removals, because cutting a node can disconnect the graph; a heavily churned index needs periodic
rebuilding.

### IVF: cluster, then probe
IVF (inverted file) runs k-means over a training sample to produce `nlist` centroids, then assigns every
vector to its nearest centroid, producing `nlist` posting lists, the same shape as a text inverted index
but keyed by cluster. At query time it computes the distance from the query to all centroids (cheap:
nlist is thousands, not millions), picks the `nprobe` closest, and scans only those lists exhaustively.

The approximation is in the boundary: a true neighbor sitting just across a cell edge is missed unless
`nprobe` reaches its cell. Raising `nprobe` raises recall and scan cost roughly linearly. A common rule
of thumb puts `nlist` on the order of √N up to a few multiples of it, with at least tens of training
vectors per centroid so k-means has something to fit. Because the centroids are fixed at training time,
an IVF index degrades when the data distribution drifts (new product lines, new languages) and needs
re-training, whereas HNSW adapts incrementally.

### Product quantization: trading bytes for bits
IVF is usually paired with product quantization (IVF-PQ). PQ splits each d-dimensional vector into `m`
sub-vectors and replaces each with the id of its nearest centroid from a 256-entry codebook, so a
768-dim fp32 vector (3,072 bytes) becomes `m` bytes: 96 bytes at m = 96, a 32× compression. Distances
are computed asymmetrically: the query stays exact, and per-subspace lookup tables turn each candidate's
distance into `m` table reads. This is what makes billion-scale search fit in memory or on GPU, at a
recall cost you recover partly by re-ranking the top few hundred PQ candidates with their exact vectors.

### Choosing between them
- HNSW: best recall at a given latency, incremental inserts, no training step; costs the most RAM and the
  longest build.
- IVF-Flat: faster to build and simpler, needs training and periodic re-training; at equal recall it
  usually computes more distances per query than HNSW, because whole cells are scanned.
- IVF-PQ: smallest footprint by far, GPU-friendly; lowest raw recall, so pair it with exact re-ranking.

### Filters interact badly with both
A metadata filter that keeps 1% of vectors breaks the assumptions: HNSW's greedy walk cannot reach the
few allowed nodes if the path runs through disallowed ones, and an IVF probe may find zero allowed
vectors in the chosen cells. Engines handle this with filtered traversal (check the filter during the
walk and widen ef), pre-filtering to an id set and falling back to brute force when it is small, or
partitioning the index by the filter key. Know which your engine does, because "search returns fewer
than k results" is the classic symptom.

## Example
FAISS lets you build an exact index as the recall reference, then an HNSW and an IVF index over the same
vectors, and sweep each one's search knob. Random Gaussian data is the worst case for ANN (no cluster
structure), so the recall numbers printed will be lower than on real embeddings at the same settings; the
shape of the curve is what to look at.

```python
import numpy as np, faiss, time
rng = np.random.default_rng(0)
N, d, k = 200_000, 128, 10
xb = rng.standard_normal((N, d)).astype("float32")
xq = rng.standard_normal((500, d)).astype("float32")

flat = faiss.IndexFlatL2(d); flat.add(xb)
_, truth = flat.search(xq, k)                        # exact top-10 = the recall reference

hnsw = faiss.IndexHNSWFlat(d, 32)                    # M = 32 links per node per layer
hnsw.hnsw.efConstruction = 200
hnsw.add(xb)                                         # no training; inserts one by one

nlist = 1024                                         # ~2.3 * sqrt(N) cells
quantizer = faiss.IndexFlatL2(d)                     # holds the centroids; keep a reference
ivf = faiss.IndexIVFFlat(quantizer, d, nlist)
ivf.train(xb[:50_000]); ivf.add(xb)                  # k-means needs a training sample first

def report(index):
    t = time.perf_counter(); _, I = index.search(xq, k)
    ms = (time.perf_counter() - t) * 1e3 / len(xq)
    recall = np.mean([len(set(I[i]) & set(truth[i])) / k for i in range(len(xq))])
    return f"recall@10={recall:.3f}  {ms:.2f} ms/query"

for ef in (16, 64, 256):
    hnsw.hnsw.efSearch = ef;  print("HNSW efSearch", ef, report(hnsw))
for nprobe in (1, 8, 64):
    ivf.nprobe = nprobe;      print("IVF  nprobe  ", nprobe, report(ivf))
print("flat", report(flat))
```

Expect recall to climb toward 1.0 as `efSearch` or `nprobe` grows while per-query time grows with it,
and `flat` to sit at exactly 1.0. Two caveats on the timings: they are batch throughput divided by 500
(FAISS parallelizes across queries), not single-query latency, and batched flat search runs as one BLAS
matrix multiply, so at a mere 200k vectors it can be surprisingly competitive. That is the point of the
"start with exact search" advice; the gap opens as N grows, because flat cost is linear in N.

## Trade-offs
- HNSW buys the best recall per millisecond with incremental updates, but every vector plus its links
  must sit in RAM; the moment the corpus outgrows memory you move to IVF-PQ or a disk-oriented graph
  index.
- IVF indexes build faster than HNSW and quantize well, at the cost of a training step whose centroids go stale
  as data drifts; the trade flips against IVF when documents are inserted continuously and re-training
  windows are hard to schedule.
- Product quantization shrinks storage by an order of magnitude or more and makes GPU search feasible, but raw
  PQ recall is noticeably lower; it only pays when you re-rank the PQ shortlist with exact vectors.
- Raising `efSearch` or `nprobe` improves recall with roughly linear latency cost; once recall@k is above
  what downstream answer quality can detect, further increases are pure waste and the budget should go
  to the reranker instead.

## Pitfalls
- Reporting recall only from the vendor's benchmark; the symptom is a product that works in demos and
  misses obvious documents in production, because your embeddings and filters are not theirs. Measure
  against a brute-force scan on your data.
- Combining a selective metadata filter with an HNSW search and getting fewer than k results, or results
  that ignore the filter; the fix is engine-specific (filtered traversal, pre-filtering, partitioned
  indexes), so check how yours behaves before shipping.
- Inserting and deleting heavily into an HNSW index without rebuilding; tombstones accumulate, recall
  drifts down and memory never shrinks, visible as slowly rising latency for the same `efSearch`.
- Training IVF on a non-representative sample (say, only English documents before a multilingual
  launch); the symptom is good recall on old content and poor recall on new content at identical
  settings.
- Setting `efSearch` below k; some libraries silently clamp, others return fewer results than requested,
  and either way the recall numbers look wrong for no visible reason.

## When to use
Stay with exact search while the corpus fits comfortably in memory and a scan meets your latency budget,
which on a modern CPU is typically up to a low number of millions of vectors; it is simpler, has zero
recall loss and is the reference you will need anyway. Move to HNSW when latency forces it and the data
fits in RAM; it is what pgvector, Lucene, Qdrant, Weaviate and most managed services offer first. Move to
IVF-PQ when the vector set outgrows memory or must run on GPU at very high throughput. Whatever you
choose, instrument recall@k against the exact index on a labeled sample and expose the search knob as a
configuration value, because the right operating point changes with traffic and content. Interviewers
ask you to explain the greedy walk, why ef and nprobe exist, and what happens under a restrictive filter.

## Role lens
- **Engineer:** Picks index type and parameters, writes the recall-versus-latency sweep, and tunes
  `efSearch` or `nprobe` per collection rather than accepting library defaults.
- **Architect:** Decides memory-versus-recall policy at fleet level: which collections must be in-RAM
  HNSW, which can be quantized, and how re-indexing fits release and drift schedules.
- **FDE:** Explains to a customer why their filtered search returns three results instead of ten, and
  chooses the filtering strategy their engine supports before promising recall numbers.

## Check yourself
**Q:** A team indexes 50 million 1024-dimensional fp32 embeddings and wants p99 retrieval under 50 ms on
a single 128 GB server, with new documents arriving all day. Walk through which index you would choose
and why, including what you would measure.
**A:** The raw vectors are 50M × 4 KB ≈ 200 GB, so an in-RAM HNSW over full-precision vectors does not
fit; even before graph overhead it exceeds the server. Options: reduce vector storage with scalar or
product quantization so an HNSW over compressed vectors fits (several engines support HNSW on int8 or PQ
codes), use IVF-PQ, which fits easily at tens of bytes per vector, or shard across machines. Continuous
inserts favor HNSW's incremental build over IVF's trained centroids, so a quantized HNSW with periodic
rebuilds, or IVF-PQ with scheduled re-training, are the realistic choices. In every case I would re-rank
the top few hundred candidates with exact vectors fetched from disk, and measure recall@10 against a
brute-force scan on a labeled query set while sweeping ef or nprobe to find the cheapest point that keeps
downstream answer quality flat.

## Quiz
### Q: In HNSW, what does raising `efSearch` do, and why does it help recall?
- [ ] It adds more links per node, so the graph becomes denser at query time
- [x] It widens the candidate beam kept during the greedy walk, so the search explores more near-miss nodes before stopping
- [ ] It increases the number of layers traversed, so the search starts closer to the query
- [ ] It forces the index to fall back to exact search for the final k results
> `efSearch` is the size of the dynamic candidate list at layer 0; a larger beam visits more nodes and is
> less likely to stop in a local minimum. Links per node (M) and the layer count are fixed at build time
> and cannot change per query.

### Q: Why is IVF typically combined with product quantization, while HNSW usually keeps full vectors?
- [ ] IVF requires integer codes to run k-means, while HNSW does not
- [ ] HNSW cannot compute distances on compressed vectors
- [x] IVF targets the memory and throughput regime where vectors must shrink to fit, and its list scans use PQ lookup tables efficiently, whereas HNSW's advantage is recall at a scale where RAM allows exact vectors
- [ ] Product quantization breaks the graph's navigability, so it is mathematically incompatible with HNSW
> The pairing is about operating points, not impossibility: HNSW over quantized vectors exists in several
> engines. IVF-PQ is the structure of choice when billions of vectors must fit in memory or on GPU, and
> its sequential list scans suit PQ's table-based distance computation.

### Q: A search with a metadata filter that matches 0.5% of documents returns only two of the requested ten results from an HNSW index. What is the most likely cause?
- [ ] The filter is applied before embedding, so most queries have no vector to compare
- [ ] The index was built with a cosine metric but the filter requires L2
- [x] The greedy traversal cannot reach enough allowed nodes because paths run through filtered-out neighbors, and the engine stops after exhausting its candidate list
- [ ] The filter key was not included in the k-means training sample
> Restrictive filters starve graph traversal: allowed nodes may only be reachable through disallowed
> ones, and a fixed-size candidate list runs dry. Engines address this with filtered traversal,
> pre-filtering to an id set with a brute-force fallback, or partitioned indexes; k-means is an IVF
> concept, not HNSW.
