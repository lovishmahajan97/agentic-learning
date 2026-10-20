---
id: similarity-search
module: rag-retrieval
name: Similarity search (cosine, dot product)
kind: concept
difficulty: core
est_minutes: 13
related:
  - rag-retrieval/embedding-models
  - rag-retrieval/embedding-dimension-and-normalization
  - rag-retrieval/ann-indexes
  - rag-retrieval/vector-databases
  - rag-retrieval/dense-vs-sparse-retrieval
  - model-internals/embeddings
sources:
  - Karpukhin et al., 2020 — Dense Passage Retrieval for Open-Domain Question Answering
  - Official docs: pgvector
  - Official docs: FAISS
---

## TL;DR
Dense retrieval reduces to one operation: score a query vector against every stored vector and keep the
top k. Dot product and cosine are the two scores that matter; cosine is the dot product of unit-length
vectors, and squared Euclidean distance is the same quantity rearranged. The right choice is whichever
the embedding model was trained against, because training shaped the geometry. Pick the wrong one and
rankings degrade silently, with no error to catch.

## Why it exists
Lexical search answers "which documents contain these words"; it cannot see that "cancel my
subscription" and "stop billing me" ask for the same thing. Embedding models fix that by mapping text to
vectors such that inputs with similar meaning land near each other. But "near" is not a given. A vector
is just an array of floats, and retrieval needs a scalar score per candidate that (a) is cheap enough to
evaluate millions of times per query and (b) agrees with what the model learned.

The naive answer, Euclidean distance on raw outputs, fails on (b). Modern embedding models are trained
with a contrastive objective: the query vector is pulled toward its positive passage and pushed away from
in-batch negatives, and the loss is computed on one specific similarity, most often cosine scaled by a
temperature, sometimes a plain dot product. The model never sees Euclidean distance, so vector magnitudes
are whatever the optimizer happened to leave behind. Comparing raw distances then mixes a signal the model
learned (angle) with one it did not (length).

That is why this lesson has two halves: the algebra that relates the three scores, and the rule that the
score must match the training objective. The algebra tells you when the choice does not matter; the rule
tells you what to do when it does.

## How it works
### The three scores and how they relate
For query **q** and document **d** in ℝᵈ:

```
dot(q, d)   = Σᵢ qᵢ·dᵢ
cos(q, d)   = dot(q, d) / (‖q‖·‖d‖)
‖q − d‖²    = ‖q‖² + ‖d‖² − 2·dot(q, d)
```

Two consequences fall straight out. If every vector has unit length, `cos = dot` exactly and
`‖q − d‖² = 2 − 2·cos`, so all three produce the identical ranking; only sign and scale differ. If lengths
vary, dot product prefers long vectors regardless of direction, cosine ignores length entirely, and
Euclidean penalizes both angle and length mismatch. The whole metric debate is about what to do with ‖d‖.

### Why normalization at index time is the standard move
Normalizing is one division per vector, done once when the vector is written. After that, the cheapest
score (a dot product: d multiplies and adds, no square root) returns the cosine ranking, and any index
built for inner product or L2 gives cosine results too. Databases exploit this: when you store unit
vectors, the engine can skip the per-candidate norm computation. This is why most embedding pipelines
normalize before writing and why several API providers return unit-length vectors already; check the
model card rather than assuming.

### When magnitude carries meaning
Some retrievers are deliberately trained with an unnormalized dot product (DPR is the classic example).
There the model can learn to give "generally useful" passages a larger norm, a kind of built-in prior,
and the query norm can act as a confidence scale. Normalizing those vectors throws that information away.
Conversely, a cosine-trained model has no incentive to keep norms meaningful, so dot product on its raw
outputs adds noise. The rule: use the similarity the model card names. If it is silent, normalize and use
cosine; it is the choice that cannot be badly wrong.

### How the computation runs
Exact search is a matrix–vector product: stack N document vectors into a matrix D (N × d) and compute
`D·q`. The cost is N·d multiply-adds, but in practice the bound is memory bandwidth, because every byte
of D is read once per query. One million 1024-dimensional fp32 vectors is 4 GB. As an illustrative order
of magnitude, streaming that through a single CPU core takes hundreds of milliseconds; a GPU with roughly
a terabyte per second of bandwidth does it in a few milliseconds. Batching queries into a matrix–matrix
product (D·Qᵀ) amortizes the read across many queries, which is why offline evaluation is cheap while
online single-query latency is not. Past a few million vectors you stop scanning and build an approximate
index (see ANN indexes), trading a little recall for sublinear search.

### Scores are not probabilities
Cosine lies in [−1, 1], but for a given text embedding model almost all query–document pairs land in a
narrow band whose location depends on the model, and unrelated pairs are rarely near zero (embedding
spaces are anisotropic: everything points roughly the same way). A score of 0.78 means nothing without a
calibration set from the same model and the same kind of pairs. Thresholds must be measured, not copied,
and they are invalid the moment the model changes.

### Operators in real systems
pgvector exposes `<->` (L2 distance), `<=>` (cosine distance, 1 − cos) and `<#>` (negative inner
product, negated so that `ORDER BY ... ASC` works for all three). FAISS has `IndexFlatL2` and
`IndexFlatIP`. An index is built for exactly one operator class, and a query that uses a different
operator cannot use the index; the database silently falls back to a full scan.

## Example
Random vectors stand in for embeddings; 5% of the corpus is given a three-times-larger norm, the way a
dot-product-trained model might inflate "important" passages. Watch the dot-product top-k fill up with
those rows while cosine ignores length, and see that normalizing at index time makes the cheap dot
product reproduce the cosine ranking exactly.

```python
import numpy as np
rng = np.random.default_rng(0)
N, d = 100_000, 768
D = rng.standard_normal((N, d)).astype(np.float32)
D[:5000] *= 3.0                               # 5% of the corpus has a 3x larger norm
q = rng.standard_normal(d).astype(np.float32)

def topk(scores, k=5):
    return np.argsort(-scores)[:k]

dot = D @ q                                   # N*d multiply-adds, one pass over D
cos = dot / (np.linalg.norm(D, axis=1) * np.linalg.norm(q))
l2  = np.linalg.norm(D - q, axis=1)

print("dot    :", topk(dot))                  # expect ids < 5000: length wins
print("cosine :", topk(cos))                  # angle only; ids spread over the corpus
print("l2     :", topk(-l2))                  # smaller distance = better

# Normalize once at write time; the cheap dot product now equals cosine.
Dn = D / np.linalg.norm(D, axis=1, keepdims=True)
qn = q / np.linalg.norm(q)
print("dot on unit vectors == cosine ranking:", np.array_equal(topk(Dn @ qn), topk(cos)))
print("l2 on unit vectors  == cosine ranking:",
      np.array_equal(topk(-np.linalg.norm(Dn - qn, axis=1)), topk(cos)))
```

## Trade-offs
- Dot product is the cheapest score and preserves any magnitude prior the model learned; it flips to
  harmful the moment the model was cosine-trained, because then norms are noise and long vectors win for
  no reason.
- Cosine is scale-invariant, so verbose and terse passages compete on direction alone; the cost is a
  normalization pass and the loss of any information the model encoded in vector length.
- Exact brute-force scoring has perfect recall and zero index maintenance; it stops being viable when
  N·d·bytes can no longer be streamed within your latency budget, typically somewhere in the low millions
  of vectors on CPU.
- Storing vectors in fp16 or int8 halves or quarters the bandwidth per query at a small ranking cost; the
  cost stops being small when you threshold scores, because near-ties at the cutoff move first.

## Pitfalls
- Building the index with one operator class (`vector_cosine_ops`) and querying with another (`<->`); the
  symptom is query time that grows linearly with corpus size and `EXPLAIN` showing a sequential scan.
- Running L2 or dot product on unnormalized outputs from a cosine-trained model; the symptom is rankings
  that disagree with the vendor's playground for the same query, usually favoring long passages.
- Copying a similarity threshold (say 0.8) from a blog post or a previous model; after an embedding
  upgrade either everything or nothing passes the filter, because the score distribution shifted.
- Treating score as confidence across queries; a 0.72 for one query and a 0.72 for another say nothing
  comparable, so "answer only if score > t" needs per-model calibration on labeled pairs.
- Mixing vectors from two embedding models or versions in one collection; the symptom is that documents
  indexed after the switch are never retrieved (or only they are), since the two spaces are unrelated.

## When to use
Every dense retrieval system makes this choice, usually implicitly. Decide it explicitly when you create a
collection: read the model card, pick its similarity, normalize at write time unless the model relies on
magnitude, and create the index with the matching operator class. Measure recall@k against an exact
brute-force scan on a labeled set whenever you change the model, the metric or the precision, because a
wrong metric degrades quality without raising an error. Reach for brute force when the corpus is small or
when you need an exact reference; move to an approximate index when latency or memory forces it. In
interviews, expect to derive the relationship between the three scores and to explain why a
cosine-trained model should not be queried with dot product on raw vectors.

## Role lens
- **Engineer:** Chooses `normalize_embeddings=True` and the matching operator class, writes the
  brute-force recall check, and catches the metric mismatch in `EXPLAIN` output before it reaches
  production.
- **Architect:** Owns the embedding contract: one model, one metric, one normalization rule per
  collection, versioned so a model change triggers re-indexing instead of a silently mixed space.
- **FDE:** Diagnoses "search got worse after we moved to Postgres" at a customer by checking which
  operator the ORM emits against which index exists, often a five-minute fix.

## Check yourself
**Q:** A customer's pgvector table has an HNSW index created with `vector_cosine_ops`, but their ORM
generates queries with the `<->` operator. Latency rose from 20 ms to 2 s as the table grew to five
million rows. Explain what is happening, how to fix it, and whether the fix will change the results users
see.
**A:** The index is built for cosine distance but the query asks for L2 distance, so the planner cannot
use it and falls back to a sequential scan over all five million vectors; that is why latency scales with
table size. The fix is either to query with `<=>` or to build a second index with `vector_l2_ops`.
Whether results change depends on normalization: if the stored vectors and the query are unit length, L2
and cosine produce the same ordering, so switching operators changes only speed. If they are not
normalized, the L2 ranking was mixing magnitude into the score, and moving to cosine will change rankings,
probably for the better if the model was cosine-trained. A strong answer also mentions validating with a
brute-force recall check on a labeled sample before and after.

## Quiz
### Q: Why do cosine similarity, dot product and Euclidean distance produce the same ranking on unit-length vectors?
- [ ] Because normalization removes the angular component, leaving only magnitude to compare
- [x] Because with ‖q‖ = ‖d‖ = 1, cosine equals the dot product and ‖q − d‖² equals 2 − 2·dot, a monotonic transform
- [ ] Because unit vectors are orthogonal, so all three scores reduce to a count of shared dimensions
- [ ] Because databases always convert to cosine internally regardless of the operator used
> Expanding ‖q − d‖² gives ‖q‖² + ‖d‖² − 2·dot; with both norms equal to one it becomes 2 − 2·dot, which
> orders candidates exactly like the dot product, and the dot product is the cosine once the norms are
> one. Nothing about orthogonality or database internals is involved.

### Q: An embedding model was trained with a cosine objective. Which query-time choice is most likely to degrade retrieval quality?
- [ ] Normalizing vectors at index time and using an inner-product index
- [ ] Using a cosine-distance operator with unnormalized stored vectors
- [x] Using an unnormalized dot product, so that vectors with larger norms rank higher regardless of direction
- [ ] Converting the stored vectors to fp16 before building the index
> A cosine-trained model never saw its vector magnitudes in the loss, so norms are uninformative; dot
> product on raw vectors rewards them anyway. The first two options both produce cosine rankings, and
> fp16 storage changes results only at the margin.

### Q: A team sets a "relevant if cosine > 0.8" filter that works well. They switch to a different embedding model and the filter now rejects almost everything. What is the most likely explanation?
- [ ] The new model produces vectors that are not unit length, so cosine cannot exceed 0.8
- [ ] The new model has a smaller dimension, which caps the maximum cosine value
- [x] Score distributions are model-specific, so the old threshold sits at a different place in the new model's distribution
- [ ] The new model requires the dot product operator and cosine is returning distances instead
> Cosine is bounded by one regardless of dimension or normalization, so neither mechanical explanation
> holds. Each model places query–document pairs in its own band of scores; a threshold calibrated on one
> model is an arbitrary line for another and must be re-estimated on labeled pairs.
