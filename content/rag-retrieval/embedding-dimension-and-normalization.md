---
id: embedding-dimension-and-normalization
module: rag-retrieval
name: Embedding dimension & normalization
kind: concept
difficulty: core
est_minutes: 13
status: reviewed
related:
  - rag-retrieval/embedding-models
  - rag-retrieval/similarity-search
  - rag-retrieval/ann-indexes
  - rag-retrieval/vector-databases
  - data-architecture/vector-store-sizing
  - ml-fundamentals/normalization
  - training-adaptation/quantization
  - model-internals/embeddings
sources:
  - Kusupati et al., 2022 — Matryoshka Representation Learning
  - Ethayarajh, 2019 — How Contextual are Contextualized Word Representations?
  - Johnson et al., 2017 — Billion-scale similarity search with GPUs
  - Official docs: pgvector
---

## TL;DR
The dimension of an embedding is how many floats each vector holds, and it sets the storage, memory and
per-comparison cost of the whole index linearly. L2-normalizing vectors to unit length makes cosine
similarity equal to the dot product and makes Euclidean ranking identical to both, so the metric choice
stops mattering and scores stop rewarding long, verbose chunks. Matryoshka-trained models let you
truncate dimensions after the fact; quantization shrinks the bytes per dimension. Both trade a little
recall for a lot of memory.

## Why it exists
Every stored vector costs `d × bytes_per_float`. At float32 a 1536-dimensional embedding is 6,144 bytes,
so ten million chunks are about 61 GB of raw vectors before any index overhead, and HNSW indexes want
those vectors resident in RAM. Query cost scales the same way: every candidate comparison is `d`
multiply-adds. Dimension is therefore the lever that connects the model choice to the infrastructure
bill, and until Matryoshka-style training it was fixed the moment a model was chosen.

Normalization solves a different problem. Raw encoder outputs have norms that vary with things you do
not care about: input length, token frequency, how "sure" the network happens to be. A dot product
between raw vectors therefore mixes meaning with magnitude, and a long chunk can outscore a short,
precisely relevant one on sheer norm. Cosine similarity divides the magnitude out. The cheapest way to
get cosine everywhere is to normalize once at write time and then use the plain dot product, which is
also the fastest operation an ANN index can run. Skip this step and you get a subtle bug rather than an
error: the index metric and the ranking you expected disagree, so the "nearest" neighbours are not the
ones you wanted and thresholds mean nothing.

## How it works
### Three metrics, one ranking
For vectors **a** and **b**: dot product `a·b`; cosine `a·b / (‖a‖‖b‖)`; Euclidean distance `‖a−b‖`.
When both are unit length, cosine *is* the dot product, and

```
‖a−b‖² = ‖a‖² + ‖b‖² − 2a·b = 2 − 2a·b
```

so Euclidean distance is a monotone function of the dot product. All three produce the same order. This
is why many embedding providers return normalized vectors and why "which metric should I pick" is only a
real question for unnormalized ones. The practical rule: normalize at write time, store unit vectors,
and build the index on inner product; that saves a division per comparison, and pgvector's `<#>`
(negative inner product), `<=>` (cosine distance) and `<->` (L2) then agree on order. The exception
is a model trained to be scored with an *unnormalized* dot product (the original DPR is one; some
sentence-transformers checkpoints say so on their model card). There the norm is part of the learned
score, and normalizing produces a different ranking from the one the model was trained for, so use the
similarity the model card names rather than normalizing by reflex.

### Where the norm comes from and why to discard it
Encoders are not trained to make norms meaningful. Contextual embeddings are also anisotropic: they
occupy a narrow cone of the space, so the cosine between two unrelated sentences is well above zero and
differs by model. The consequence is that absolute similarity values are model-specific; a score of 0.8
is "related" under one model and "noise floor" under another. Rankings are portable, thresholds are
not, so any threshold must be calibrated per model on labelled pairs.

### What dimension buys
Dimensions are capacity: the model needs enough axes to separate the concepts it was trained on.
Returns diminish quickly, doubling `d` does not double recall, while storage and compare time grow
linearly and ANN graph memory grows with them. Common sizes: 384 (MiniLM-class), 768 (BERT-base-derived
models such as E5-base and BGE-base), 1024 (large variants), 1536 and 3072 (OpenAI's text-embedding-3
small and large).

### Matryoshka representation learning
Standard training spreads information across all dimensions, so truncating a vector discards a random
slice of its meaning and recall collapses. MRL applies the training loss simultaneously to nested
prefixes of the vector (the first 64, 128, 256, … dimensions), which pushes the most important
information to the front. You can then keep the first `k` dimensions, renormalize, and search; keep the
full vectors on disk to rescore the top candidates. OpenAI's text-embedding-3 models expose this as a
`dimensions` request parameter and several open models (nomic-embed-text v1.5 among them) support it.
Renormalization after truncation is mandatory because a prefix of a unit vector is shorter than one.

### Quantization: fewer bytes per dimension
Orthogonal to truncation. float32 to float16 halves memory with essentially no recall change; int8
scalar quantization quarters it; binary quantization (one bit per dimension, scored by Hamming distance)
gives up to 32× and works surprisingly well on high-dimensional normalized vectors when the top
candidates are rescored with full-precision vectors. Product quantization (IVF-PQ in FAISS and Milvus)
compresses further by replacing sub-vectors with codebook ids. Normalization helps scalar quantization
and PQ, because unit vectors have bounded coordinates and the quantizer's range is predictable; binary
quantization keeps only the sign, so for it the norm is irrelevant.

### PCA and friends
PCA fitted on your own corpus can drop dimensions for any model, Matryoshka-trained or not, but it must
be fitted on representative data and applied identically to queries; it is a weaker, model-agnostic
version of the same idea.

## Example
A small numpy walkthrough: a query that is genuinely closest to document 1, and a document 3 that is
only weakly related but has an eight-times larger norm. Raw dot product ranks document 3 first; cosine,
and every metric after normalization, rank document 1 first. The last block shows the truncation
mechanics (truncate, then renormalize) and the storage arithmetic.

```python
import numpy as np

rng = np.random.default_rng(0)
d, n = 1536, 5
X = rng.normal(size=(n, d))                      # five "document" vectors
q = X[1] + 0.5 * rng.normal(size=d)              # a query genuinely closest to doc 1
X[3] = 8.0 * (0.2 * X[1] + rng.normal(size=d))   # doc 3: weakly related, 8x larger norm

dot = X @ q
cos = dot / (np.linalg.norm(X, axis=1) * np.linalg.norm(q))
print("raw dot ranking:", np.argsort(-dot))      # [3 1 4 2 0]  magnitude wins
print("raw cos ranking:", np.argsort(-cos))      # [1 3 4 2 0]  direction wins

Xn = X / np.linalg.norm(X, axis=1, keepdims=True)
qn = q / np.linalg.norm(q)
dot_n = Xn @ qn
l2 = np.linalg.norm(Xn - qn, axis=1)
assert np.allclose(l2**2, 2 - 2 * dot_n)        # unit vectors: ||a-b||^2 = 2 - 2 a.b
print(np.argsort(-dot_n), np.argsort(l2))        # identical orderings

k = 256                                          # Matryoshka-style truncation: keep a prefix, renormalize
Xk = Xn[:, :k] / np.linalg.norm(Xn[:, :k], axis=1, keepdims=True)
qk = qn[:k] / np.linalg.norm(qn[:k])
print("256-dim ranking:", np.argsort(-(Xk @ qk)), "storage ratio:", k / d)
print("10M x 1536 x 4 bytes =", 10_000_000 * 1536 * 4 / 1e9, "GB")   # 61.44 GB, before the index
```

With random vectors the truncation keeps the order only because the signal is spread evenly; for a real
model the prefix retains the order only if the model was trained Matryoshka-style.

## Trade-offs
- Fewer dimensions cut memory and compare cost linearly but reduce capacity; the loss is small for an
  MRL model truncated to a quarter and severe for naive truncation of a standard model.
- Normalizing and indexing on inner product is the fastest, simplest path, but it throws away the norm;
  in the rare setup where a model's norm carries a useful signal (some models correlate it with
  confidence or length) you would keep raw vectors and cosine, at a small cost per comparison.
- Binary or int8 quantization gives large memory wins with small recall loss when the top candidates are
  rescored, but adds a second lookup and requires keeping full vectors somewhere; without rescoring,
  recall on hard queries drops noticeably.
- A bigger model with more dimensions improves quality until the corpus is small enough that recall is
  already saturated; past that point the extra dimensions only cost money.

## Pitfalls
- Building an inner-product index over unnormalized vectors from a cosine-trained model; symptom: long,
  verbose chunks dominate the top-k regardless of relevance.
- Normalizing at index time but not at query time; symptom: rankings look fine but scores above 1.0
  appear and any similarity threshold silently stops working.
- Truncating a model that was not Matryoshka-trained; symptom: recall collapses far more than the
  dimension ratio would suggest.
- Forgetting to renormalize after truncation; symptom: a cosine index returns different scores from a
  dot-product index on the same data, and thresholds drift.
- Copying a cosine threshold such as 0.75 from another model or a blog post; symptom: after a model swap
  either nothing or everything passes the filter.
- Sizing storage from raw vectors alone; symptom: out-of-memory during index build, because HNSW graph
  links, payloads and copies of the chunk text all add to the vector bytes; graph links alone are small
  for high-dimensional vectors, but stored chunk text and payload indexes can add a large fraction.
- Writing vectors of mixed dimensions into one collection; symptom: insert errors where the store
  enforces the column dimension (pgvector does), or silently wrong scores where it does not.

## When to use
Use the arithmetic every time you size a vector store: `vectors × d × bytes` tells you whether the index
fits in RAM, and whether you need truncation, quantization, disk-based indexes or sharding. Always store
normalized vectors unless the model card specifies an unnormalized dot product, and make the
normalization part of the shared embed function so index and query paths agree. Consider Matryoshka truncation when the model
supports it and memory or latency binds; consider binary or int8 quantization with rescoring when the
corpus is tens of millions of vectors or more. Measure recall@k against the full-precision baseline
before and after any reduction. In interviews, expect to derive the cosine-equals-dot-product identity
and to size a 100-million-vector index from scratch.

## Role lens
- **Engineer:** Normalizes inside the embed function, picks the index metric to match, and writes a test
  that asserts a stored vector has unit norm before it reaches the database.
- **Architect:** Owns the memory budget: chooses dimension, quantization and rescoring strategy from the
  vector count and recall target, and documents which threshold calibrations belong to which model.
- **FDE:** Explains to a customer why their 3072-dimensional index does not fit the box they bought, and
  demonstrates truncation plus rescoring as the fix with a recall measurement on their data.

## Check yourself
**Q:** You have 50 million chunks embedded at 3072 dimensions in float32, and the search node has 256 GB
of RAM. What are your options, and how do you decide between them?
**A:** Raw vectors alone are 50M × 3072 × 4 bytes ≈ 614 GB, so nothing fits as is. Options, cheapest
first: if the model is Matryoshka-trained, truncate to 1024 dimensions (≈ 205 GB) or 768 (≈ 154 GB),
renormalize, and rescore the top candidates with full vectors read from disk; quantize to int8 (≈ 154
GB at full dimension) or binary (≈ 19 GB) with full-precision rescoring; combine truncation and
quantization; or move to a disk-based index and accept higher latency; or shard across nodes. HNSW graph
overhead and payloads must be added on top, so the target is well under 256 GB. The decision is made by
measuring recall@10 against the full-precision baseline for each variant on labelled queries, then
picking the cheapest configuration that meets the recall target.

## Quiz
### Q: Why does L2-normalizing all vectors make the choice between cosine, dot product and Euclidean distance irrelevant for ranking?
- [ ] Normalization removes the dimensions that carry noise
- [x] For unit vectors cosine equals the dot product and squared Euclidean distance equals 2 minus twice the dot product, so all three order candidates identically
- [ ] Vector databases only support one metric for normalized data
- [ ] Normalization makes all similarities fall between 0 and 1
> The identity ‖a−b‖² = 2 − 2a·b holds only when both vectors have unit length; it makes Euclidean
> distance a decreasing function of the dot product, and cosine is the dot product by definition. The
> scores still differ in value, which is why thresholds need calibrating, but the order does not.

### Q: What property of a Matryoshka-trained model makes truncating its vectors safe when truncating an ordinary model's vectors is not?
- [ ] Its vectors are already binary-quantized
- [ ] It produces fewer dimensions to begin with
- [x] The training loss was applied to nested prefixes of the vector, so the most important information is concentrated in the leading dimensions
- [ ] Its output norms are guaranteed to be one
> An ordinary model spreads information evenly, so dropping dimensions discards meaning at random. MRL
> optimizes the first 64, 128, 256 and so on dimensions as embeddings in their own right, so a prefix is a
> good lower-resolution version of the full vector.

### Q: Two clearly unrelated sentences score a cosine similarity of 0.82 under a new embedding model. What is the most likely explanation?
- [ ] The model is broken and should be replaced
- [ ] The sentences share a rare token the model over-weights
- [x] The model's embeddings are anisotropic, so its noise floor is high and absolute scores are not comparable with other models
- [ ] The vectors were accidentally stored without normalization
> Contextual encoders map text into a narrow cone, so even unrelated pairs have high cosine. That is
> normal and model-specific; rankings remain meaningful but any threshold has to be calibrated for this
> model on labelled pairs rather than carried over from another one.
