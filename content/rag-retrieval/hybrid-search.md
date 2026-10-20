---
id: hybrid-search
module: rag-retrieval
name: Hybrid search (BM25 + dense)
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
related:
  - rag-retrieval/dense-vs-sparse-retrieval
  - rag-retrieval/bm25-internals
  - rag-retrieval/reranking
  - rag-retrieval/recall-vs-precision-trade-off
  - rag-retrieval/vector-databases
  - rag-retrieval/metadata-filtering
  - evals/retrieval-evaluation
sources:
  - Cormack, Clarke & Buettcher, 2009 — Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods
  - Official docs: Elasticsearch and OpenSearch hybrid search
  - Official docs: pgvector
---

## TL;DR
Hybrid search runs a lexical retriever (BM25) and a dense retriever (embeddings) on the same query and
merges their results, because the two fail on different queries: BM25 misses paraphrase, dense misses
exact identifiers and out-of-domain jargon. The hard part is the merge: BM25 and cosine scores live on
unrelated scales, so you fuse by rank (reciprocal rank fusion) or normalize per query before a weighted
sum, and verify the gain with recall@k on labeled queries.

## Why it exists
The dense-vs-sparse lesson ends with the observation that neither retriever dominates. Put illustrative
numbers on it: for a labeled query set, suppose BM25 alone gets the relevant chunk into the top 10 for
70% of queries and dense alone for 75%. If their failures were identical, hybrid would be 75%; in
practice their failures overlap only partly, so the union often lands well above either. The recovered
queries are the identifier searches dense fumbles and the paraphrases BM25 cannot see. A generation step
cannot answer from a chunk that was never retrieved, so first-stage recall is the ceiling on everything
downstream; hybrid raises that ceiling cheaply.

The naive merge, "concatenate both lists and sort by score", fails because the scores are
incommensurable. A BM25 score is an unbounded sum that grows with query length and term rarity, often
in the single or double digits for a multi-term query (the exact range depends on corpus and engine); a cosine similarity sits in a narrow model-specific band.
Sorting them together just returns the BM25 list followed by the dense list. Even normalizing each list
to [0, 1] per query is fragile: the top BM25 hit for a one-word query may be a weak match normalized to
1.0. Fusion methods exist to combine evidence from rankers that do not share a scale.

## How it works
### Two retrievers, one query, in parallel
The query goes to both arms at once. The lexical arm analyzes it into terms and runs BM25 over an
inverted index; the dense arm embeds it and runs an ANN search. Each returns its top-n candidates with
scores. n is deliberately larger than the final k (commonly 50 to 200 per arm for a final 5 to 10),
because fusion needs overlap and depth to work with, and a reranker, if present, wants a wide pool. Any
metadata filter must be applied identically in both arms; otherwise one arm returns documents the other
was forbidden to.

### Reciprocal rank fusion
RRF ignores scores and uses only ranks:

```
RRF(d) = Σ_arms 1 / (k + rank_arm(d))        k = 60 in the original paper
```

A document found by both arms gets two contributions; one found by only one arm gets one. The constant
k damps the top: without it, rank 1 would be worth 1.0 and rank 2 only 0.5, which lets a single arm's
top hit dominate. With k = 60, rank 1 scores 1/61 ≈ 0.0164 and rank 10 scores 1/70 ≈ 0.0143, so a
document that is rank 10 in both arms (≈ 0.0286) beats one that is rank 1 in a single arm (≈ 0.0164).
That is the design intent: agreement between retrievers is stronger evidence than a confident vote from
one. RRF needs no tuning, no normalization and no knowledge of the score scales, which is why most engines
with hybrid search offer it (Elasticsearch, OpenSearch, Qdrant and Weaviate among them, in recent versions)
and many RAG frameworks use it by default. Defaults differ and change between releases, so check yours:
Weaviate, for example, moved its default to a normalized score fusion. Its weakness is that
it throws away magnitude: a dense hit at cosine 0.92 and the next at 0.55 are treated as adjacent ranks.
Two refinements are common. Per-arm weights (`w_arm / (k + rank)`) let you trust one arm more without
touching scores, and k itself is a knob: smaller k makes the curve steeper, so a single arm's top ranks
count for more; larger k flattens it further toward pure agreement counting.

### Weighted score fusion
The alternative keeps scores: normalize each arm's scores per query (min–max to [0, 1], or z-scores),
then combine as `α·dense + (1 − α)·sparse`. This can exploit a decisive dense score or a decisive exact
match, and α lets you bias toward the arm that wins on your traffic. The price is fragility:
normalization depends on the candidate pool (a longer list changes the minimum), per-query normalization
inflates weak top hits, and α must be tuned on labeled data and re-tuned when either model or the corpus
changes. A concrete case: for a vague query, BM25's top three might score 2.1, 2.0 and 1.9, a near-tie
among weak matches, while dense returns 0.81, 0.62 and 0.55, a clear winner. Min–max maps the BM25 list
to 1.0, 0.5, 0.0 and the dense list to 1.0, 0.27, 0.0, so the BM25 arm's essentially arbitrary first
place now carries exactly the same weight as the dense arm's decisive one, and its second place
outweighs the dense second by almost 2×, purely as an artifact of stretching a 0.2-wide range to 1. Z-scores soften this but still assume each arm's top-n has a
comparable spread, which is the thing that varies most from query to query.

Some systems go one step further and make the weight query-dependent, leaning lexical when the query
looks like an identifier (digits, hyphens, quoted strings) and dense when it reads as a question. It
works when the query classes are cleanly separable in your logs; it is one more component that must be
evaluated per slice. Engines expose both styles: Weaviate's `alpha`, OpenSearch's normalization processors,
Elasticsearch's RRF and weighted retrievers.

### Where the arms live
- **One engine, two indexes.** Elasticsearch and OpenSearch hold a BM25 field and a dense vector field in
  the same document, run both queries and fuse server-side. Postgres with pgvector plus `tsvector` does
  the same with two indexes and a SQL join plus RRF in the query, with one caveat: Postgres's built-in
  `ts_rank` is not BM25 (it has no IDF and no saturation in the BM25 sense), so a true BM25 arm needs an
  extension that implements it. Qdrant stores sparse vectors (BM25-style or learned sparse) beside dense
  ones, and Weaviate keeps a BM25 inverted index beside its vector index.
- **Two engines.** A search engine for lexical, a vector database for dense, fused in the application.
  More moving parts and two consistency boundaries, but each engine is used for what it is best at.

The consistency issue is identical either way: a document must be written to both arms before it is
"indexed", and deleted from both, or a query can return a chunk that exists in one and is gone from the
other.

### After fusion
Fusion produces a merged candidate list ordered by combined evidence. Most pipelines then pass the top
20 to 50 to a cross-encoder reranker, which reads query and chunk together and produces the final top k.
Hybrid is the recall stage; the reranker is the precision stage; fusion is the handshake between them.

### Measuring whether it helped
Hybrid is a hypothesis, not a guarantee. On a labeled set, compute recall@k for BM25 alone, dense alone,
and the fusion; then look at the queries where fusion beat the best single arm and the ones where it
lost. A fusion that adds BM25 noise to a strong dense arm can lower precision at small k even while
raising recall at large k; the reranker usually recovers it, but you need to see it.

## Example
Reciprocal rank fusion in a dozen lines, applied to the ranked ids returned by a BM25 arm and a dense
arm. The numbers show the agreement effect: document 3 is the BM25 arm's top hit but absent from the
dense top 10, and it falls to fifth behind four documents both arms agreed on, including document 8,
which was only rank 4 and rank 6.

```python
from collections import defaultdict

def rrf(ranked_lists, k=60):
    """ranked_lists: iterable of lists of doc ids, best first. Returns [(doc_id, score)]."""
    scores = defaultdict(float)
    for ranking in ranked_lists:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: -kv[1])

bm25_top  = [3, 17, 42, 8, 21, 5, 11, 9, 30, 2]      # best first, from the inverted index
dense_top = [42, 17, 7, 21, 13, 8, 19, 25, 6, 1]     # best first, from the ANN index

for doc_id, score in rrf([bm25_top, dense_top])[:5]:
    print(doc_id, f"{score:.5f}")
# 42 0.03227   rank 3 + rank 1
# 17 0.03226   rank 2 + rank 2
# 21 0.03101   rank 5 + rank 4
# 8  0.03078   rank 4 + rank 6
# 3  0.01639   rank 1, BM25 only
```

In Postgres the same shape is two subqueries (one ordered by the `tsvector` rank, one by cosine
distance), each with a `ROW_NUMBER()` rank, joined on chunk id with a full outer join and ordered by the
sum of `1.0 / (60 + rank)`.

## Trade-offs
- Hybrid raises first-stage recall for mixed query traffic, but doubles query-time work (an embedding
  call plus an ANN search plus a BM25 query) and index storage; if your labeled set shows one arm already
  at the recall ceiling, the second arm is cost without benefit.
- RRF is tuning-free and robust to scale differences, but discards score magnitude; when one arm is far
  stronger on your traffic, weighted score fusion with a tuned α can beat it, at the price of re-tuning
  on every model or corpus change.
- Fusing server-side in one engine keeps consistency and latency simple; splitting across a search
  engine and a vector database lets each scale independently but makes every write a two-phase problem.
- Deeper candidate lists per arm improve fusion quality and give the reranker more to work with, but
  latency and reranker cost grow with depth; past the point where recall@depth is flat, extra depth is
  waste.

## Pitfalls
- Summing raw BM25 and cosine scores; the symptom is results that are simply the BM25 list, because its
  scores are an order of magnitude larger.
- Applying a metadata or ACL filter in one arm only; the symptom is a document appearing in results
  that the user should not see, or a filtered query returning items from the unfiltered arm.
- Writing to the vector index and the lexical index without a shared commit or reconciliation; the
  symptom is "I just deleted that document and it still shows up" from one arm, hours after the other
  dropped it.
- Treating fused scores as relevance thresholds; RRF values depend on list depth and the number of arms,
  so a cutoff tuned at depth 50 behaves differently at depth 100.
- Declaring victory on aggregate recall; the symptom is a regression on a query class (say, exact SKU
  lookups) hidden by gains elsewhere. Slice the evaluation by query type.

## When to use
Default to hybrid for any corpus that mixes prose with identifiers, names, codes or jargon, which is most
enterprise content, and for any user base that both searches and asks questions. Skip it when a labeled
evaluation shows one arm already captures the relevant chunk in nearly every case, or when serving
constraints rule out one arm (no GPU for embeddings, or no inverted index available). Decide with
measurements: recall@k per arm and fused, sliced by query type, plus end-to-end answer quality with and
without the second arm. Start with RRF; move to weighted fusion only when you have the labeled data to
tune it and a process to re-tune. In interviews, the expected depth is explaining why scores cannot be
summed, how RRF rewards agreement, and where the reranker sits.

## Role lens
- **Engineer:** Implements both arms behind one retrieval interface, writes the RRF or normalized fusion
  step, and keeps the candidate depth and fusion constants in configuration with tests on labeled
  queries.
- **Architect:** Chooses between one engine with two indexes and two specialized engines, and owns the
  write-path consistency and filtering contract that both arms must honor.
- **FDE:** Demonstrates the gain to a customer by replaying their real query log through BM25-only,
  dense-only and hybrid and showing which of their own queries each configuration recovers.

## Check yourself
**Q:** You are adding BM25 to an existing dense-only RAG system in Postgres with pgvector. Describe the
retrieval query, how you would merge the two result sets, what could go wrong with filters and deletes,
and how you would prove the change helped.
**A:** First, note that Postgres's native `ts_rank` is a term-frequency ranking without IDF, not BM25;
either accept it as the lexical arm (often good enough for identifier matching, which is what the dense arm
lacks) or install an extension that implements real BM25. With the native route, add a `tsvector` column
with a GIN index beside the existing vector column, and at query time run
two subqueries limited to the same tenant and document filters: one ordered by the full-text rank over
the `tsvector`, one ordered by cosine distance on the embedding, each returning its top 50 with a row
number. Join them on chunk id with a full outer join and compute reciprocal rank fusion, 1/(60 + rank)
summed across arms, ordering by that sum and taking the top 10 for the reranker or the prompt. Because
both arms live in one table, deletes and filters are naturally consistent, which is a real advantage over
a separate search engine; the risks are forgetting to apply the same `WHERE` clause to both subqueries,
and the HNSW arm returning fewer rows than asked for. pgvector's HNSW scan returns at most
`hnsw.ef_search` candidates (40 by default), so `LIMIT 50` needs ef_search raised to at least 50 even
with no filter, and a selective `WHERE` is applied after the graph search, starving the arm further
unless iterative index scans (pgvector 0.8 and later) are enabled. Also note
the GIN index accelerates only the match; `ts_rank` is computed for every matching row before sorting. To
prove it helped, run the labeled query set through dense-only and hybrid, compare recall@10 overall and
per query type, inspect queries that regressed, and check p95 latency, since the second subquery adds
time.

## Quiz
### Q: Why can BM25 scores and cosine similarities not simply be added together?
- [ ] Because BM25 scores are always negative and cosine scores are always positive
- [x] Because BM25 is an unbounded sum that varies with query length and term rarity, while cosine sits in a narrow model-specific band, so one scale swamps the other
- [ ] Because cosine similarity is a distance and must be inverted first
- [ ] Because the two retrievers return different document ids for the same chunk
> The two scores measure different things on unrelated scales; a sum is dominated by whichever is
> numerically larger, typically BM25. Fusion therefore uses ranks (RRF) or per-query normalization
> before combining.

### Q: In reciprocal rank fusion with k = 60, why can a document ranked 10th by both arms outrank one ranked 1st by a single arm?
- [ ] Because RRF penalizes documents that appear in only one list by halving their score
- [ ] Because rank 10 receives a bonus for stability across retrievers
- [x] Because 1/70 + 1/70 exceeds 1/61, so agreement from two arms contributes more than a top rank from one
- [ ] Because the constant k is subtracted from single-arm scores
> Each arm contributes 1/(k + rank); two moderate contributions sum to about 0.0286 while a lone rank-1
> contributes about 0.0164. The constant k flattens the top of the curve so that a single confident arm
> cannot dominate.

### Q: A hybrid system applies the tenant filter only in the dense arm. What is the most serious consequence?
- [ ] Latency doubles because BM25 must scan all tenants
- [x] The lexical arm can return chunks from other tenants, which fusion may rank highly and expose to the user
- [ ] RRF scores become negative for cross-tenant documents
- [ ] The dense arm returns fewer than k results and fusion fails
> Fusion merges whatever each arm returns; a filter missing from one arm lets forbidden documents into
> the candidate pool, and a strong lexical match can carry one to the top. Filters and ACLs must be
> enforced identically in both arms.
