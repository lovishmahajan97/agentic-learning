---
id: clustering
module: ml-fundamentals
name: Clustering
kind: concept
difficulty: core
est_minutes: 13
status: fact-checked
related:
  - ml-fundamentals/supervised-vs-unsupervised-vs-self-supervised-learning
  - ml-fundamentals/classification
  - model-internals/embeddings
  - rag-retrieval/embedding-models
  - rag-retrieval/embedding-dimension-and-normalization
  - rag-retrieval/similarity-search
  - training-adaptation/data-curation
  - customer-data-evaluation/representative-test-case-sampling
sources:
  - Lloyd, 1982 — Least Squares Quantization in PCM
  - Arthur & Vassilvitskii, 2007 — k-means++: The Advantages of Careful Seeding
  - Ester et al., 1996 — A Density-Based Algorithm for Discovering Clusters in Large Spatial Databases with Noise
  - Official docs: scikit-learn
---

## TL;DR
Clustering groups items by similarity without labels. It answers "what kinds of things are in this
data?" when nobody has defined the kinds yet. In AI engineering it mostly runs on embeddings: discovering
intents in chat logs, deduplicating training data, choosing diverse evaluation samples, finding failure
modes. The algorithm matters less than two choices made before it runs, the distance metric and the
question you want answered; k-means, hierarchical and density-based methods differ mainly in what cluster
shapes they assume.

## Why it exists
You have 500,000 chat transcripts and no taxonomy. What are users asking for? Reading them all is
impossible. Keyword search requires knowing what to search for. A classifier requires the categories you
do not have. Clustering exploits a single fact: similar items are close together in some feature space,
and with modern embeddings "close" means "about the same thing".

The naive alternatives fail in specific ways. Random sampling and reading misses rare groups entirely and
cannot tell you how large any group is. Asking an LLM to summarise everything hits context and cost
limits long before 500,000 documents, and a summary of a random subset inherits the sampling problem.
Counting by existing metadata (product, region) only confirms the categories you already knew. What is
needed is a method that partitions the data by content, scales to the whole corpus, and reports group
sizes so attention goes where the volume is. Clustering plus an LLM to name each cluster is the
production version of this, and it is cheap because the LLM reads a few central examples per cluster
instead of every document.

Two further jobs fall out of the same machinery. Near-duplicate detection is clustering with a very tight
threshold, essential for cleaning training data and evaluation sets. And watching for a new dense region
in the embeddings of production traffic is the simplest useful drift detector.

## How it works
### The space and the distance come first
Every item becomes a vector: TF-IDF counts, hand-built features, or an embedding from a pre-trained
encoder. Then you pick a distance. For embeddings the standard is cosine similarity, and the usual trick
is to L2-normalise the vectors and run Euclidean algorithms, because for unit vectors
||a − b||² = 2 − 2·cos(a, b): Euclidean distance on normalised vectors ranks pairs exactly as cosine
does. For hand-built features, scale each column, or the feature with the largest range decides the
clusters by itself. In very high dimensions pairwise distances concentrate, which is why density-based
methods are often preceded by dimensionality reduction (PCA or UMAP), while k-means on embeddings
usually works directly because the encoder was trained to make cosine meaningful.

### k-means (Lloyd's algorithm)
Objective: minimise the within-cluster sum of squared distances, Σ ||x − μ_c(x)||². The algorithm
alternates two steps: assign every point to its nearest centroid, then move every centroid to the mean of
its points. Each step can only lower the objective, so it converges, but to a local minimum that depends
on the starting centroids. k-means++ initialisation picks each new seed with probability proportional to
its squared distance from the nearest seed chosen so far, spreading them out, and running several
initialisations and keeping the best is standard. Cost is O(n·k·d) per iteration, so it scales to
millions of points, and mini-batch variants scale further. The assumptions are the price: clusters are
roughly spherical, similar in size, and you choose k. Choosing k is by the elbow of the objective curve,
by silhouette, or, most honestly, by what the result is for: 20 for a taxonomy draft, 2,000 for
deduplication buckets.

### Hierarchical (agglomerative)
Start with every point as its own cluster and repeatedly merge the closest pair under a linkage rule:
single (nearest members), complete (farthest members), average, or Ward (smallest increase in
within-cluster variance, which chases the k-means objective). The output is a dendrogram you can cut at
any height, so no k is fixed in advance and the same run gives coarse and fine views. The cost is O(n²)
memory for the distance matrix, which caps it at tens of thousands of points in practice.

### Density-based (DBSCAN, HDBSCAN)
A point is a core point if at least `min_samples` neighbours lie within radius `eps`; clusters are
connected sets of core points plus their border points; everything else is labelled noise. This finds
clusters of arbitrary shape, decides their number itself, and gives you explicit outliers, which is
exactly what "show me the weird requests" needs. The weakness is one global `eps` on data whose density
varies: sparse regions become noise or dense regions merge. HDBSCAN removes `eps` by building a
hierarchy over density levels and extracting the most stable clusters. The recipe "embed, reduce with
UMAP, cluster with HDBSCAN, name each cluster from its top terms or with an LLM" is the common pattern
for topic discovery on text.

### Soft clustering
A Gaussian mixture model assigns each point a probability of belonging to each cluster and fits by
expectation–maximisation; k-means is the limiting case with hard assignments and equal spherical
covariances. Use it when membership is genuinely graded.

### Evaluating without labels
Silhouette for a point is (b − a) / max(a, b), where a is its mean distance to its own cluster and b
the mean distance to the nearest other cluster; it ranges from −1 to 1 and averages over points. Check
stability across seeds and subsamples: a cluster that disappears when you reshuffle was never there. If
you have labels for a sample, adjusted Rand index or normalised mutual information compare partitions.
None of these replaces reading the ten most central examples of each cluster, which is the only test of
whether a cluster means anything.

## Example
Discovering intents in chat logs: embed, normalise, sweep k with a silhouette check, then print the most
central messages per cluster. The silhouette picks a k; the printed examples tell you whether the
clusters are real.

```python
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import normalize

texts = [line.strip() for line in open("messages.txt") if line.strip()]   # e.g. 20k user messages
model = SentenceTransformer("all-MiniLM-L6-v2")
E = normalize(model.encode(texts, batch_size=256))     # unit vectors: Euclidean k-means == cosine geometry

best = None
for k in (10, 20, 40, 80):
    km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(E)
    s = silhouette_score(E, km.labels_, sample_size=5000, random_state=0)
    print(f"k={k:3d}  silhouette={s:.3f}  inertia={km.inertia_:.1f}")
    if best is None or s > best[0]:
        best = (s, k, km)

s, k, km = best
sizes = np.bincount(km.labels_)
for c in np.argsort(-sizes)[:5]:                               # five largest clusters
    members = np.where(km.labels_ == c)[0]
    d = np.linalg.norm(E[members] - km.cluster_centers_[c], axis=1)
    print(f"\ncluster {c}: {sizes[c]} messages ({sizes[c] / len(texts):.1%})")
    for i in members[np.argsort(d)[:3]]:                       # most central examples: read these
        print("  -", texts[i][:100])
```

## Trade-offs
- k-means is fast and scales to millions of vectors, but needs k and assumes convex, similar-sized
  clusters; density-based methods win as soon as clusters vary in density or shape, or you need outliers.
- Hierarchical clustering needs no k and gives every granularity at once, but its quadratic memory
  makes it impractical beyond tens of thousands of points without sampling first.
- Clustering embeddings captures meaning that TF-IDF misses, but inherits the encoder's notion of
  similarity: a model that groups by topic will put "cancel my subscription" and "how do I upgrade"
  together because both are about accounts; choosing or fine-tuning the encoder for the facet you care
  about (intent, sentiment, product) changes the clusters more than any algorithm choice.
- More clusters are purer but more to review; fewer are coarser but digestible; the right number is set
  by the downstream use, not by a statistic.

## Pitfalls
- Running Euclidean k-means on unnormalised embeddings; the symptom is clusters organised by vector
  norm, which often tracks text length, rather than by meaning.
- Treating cluster labels as categories without inspection; the symptom is a "cluster" that mixes three
  intents united by shared boilerplate such as a greeting template.
- Publishing cluster ids as stable production categories; the symptom is next week's "cluster 7" being a
  different thing after a re-run, and dashboards built on it silently breaking.
- Choosing k by the elbow when there is no elbow, which is the normal case for embeddings; the symptom is
  a smooth curve and an arbitrary choice, so use silhouette plus reading, or a hierarchical view.
- Letting templated or duplicated messages dominate; the symptom is one giant cluster holding 40% of the
  data, fixed by deduplicating and stripping boilerplate before embedding.
- Using DBSCAN with a single `eps` on data of varying density; the symptom is either one cluster
  containing everything or almost everything labelled noise.

## When to use
Use clustering to understand data before categories exist, to sample diverse items for labelling and
evaluation sets, to deduplicate, to detect emerging issues as new dense regions in production
embeddings, and to summarise large logs cluster by cluster with an LLM. Do not use it when you already
have a taxonomy and labels (train a classifier) or when you need stable, auditable categories in a
production path, because clusters drift and renumber. Measure silhouette and seed stability as sanity
checks, but the real test is whether the clusters changed a decision: which categories were added, which
examples were labelled, which failure mode was found. Interview questions centre on how you choose k,
what k-means assumes, and how you would find out what users are asking about.

## Role lens
- **Engineer:** Runs embed, normalise, k-means or HDBSCAN over logs in a notebook that prints central
  examples per cluster, then promotes the stable clusters into a labelled classifier.
- **Architect:** Positions clustering as an offline exploration and drift-detection tool rather than a
  production decision path, and decides which embedding model defines "similar" across the platform.
- **FDE:** Uses clustering in the first week at a customer to map an unlabelled corpus, show the size of
  each group, and pick the examples that go into the expert labelling session.

## Check yourself
**Q:** A customer hands you 500,000 support chats on Monday and asks for "the top issues" by Friday.
Walk through your approach and the ways it could mislead you.
**A:** Deduplicate and strip greetings and signatures first, or boilerplate becomes the biggest cluster.
Embed with a general sentence encoder and L2-normalise. Run mini-batch k-means with k around 50, or UMAP
plus HDBSCAN if you want outliers surfaced, and for each cluster read the ten most central chats, name the
cluster (an LLM can draft names, a human verifies), and report cluster sizes as shares of volume. The
ways it misleads: the encoder groups by topic, so two different problems with the same product merge and
one problem phrased two ways splits; a low silhouette in high dimensions is normal, not proof of failure;
sizes depend on k; some "issues" are really sentiment or channel differences. Validate by sampling 50
chats from each top cluster and measuring how many actually match the name, checking stability across
seeds, and comparing against any tags the customer already has.

## Quiz
### Q: Why L2-normalise embeddings before running k-means with Euclidean distance?
- [ ] k-means requires all inputs to lie in the range 0 to 1
- [x] For unit vectors squared Euclidean distance is a monotone function of cosine similarity, so clusters reflect direction (meaning) rather than vector magnitude
- [ ] Normalisation guarantees convergence in a single iteration
- [ ] It removes the need to choose the number of clusters
> After normalisation ||a − b||² = 2 − 2·cos(a, b), so the Euclidean objective and cosine similarity
> agree. Without it, differences in vector norm, which often track text length, drive the partition.

### Q: The data has clusters of very different densities and many outliers. Which method fits best?
- [ ] k-means with k-means++ initialisation and many restarts
- [ ] Agglomerative clustering with Ward linkage
- [x] HDBSCAN
- [ ] A Gaussian mixture with spherical covariances
> k-means and Ward assume compact, similar clusters and assign every point somewhere, so outliers distort
> centroids. HDBSCAN handles varying density by working across density levels and labels sparse points as
> noise instead of forcing them into a cluster.

### Q: What does k-means guarantee at convergence?
- [ ] The global minimum of the within-cluster sum of squares
- [x] A local minimum where no reassignment or centroid update lowers the objective further, which can differ between seeds
- [ ] Clusters of approximately equal size
- [ ] The maximum possible silhouette score for that k
> Each Lloyd step is non-increasing in the objective, so the algorithm stops at a fixed point, but that
> fixed point depends on initialisation. This is why k-means++ seeding and multiple restarts are standard.
