---
id: retrieval-evaluation
module: evals
name: Retrieval evaluation (recall@k, MRR, NDCG)
kind: metric
difficulty: core
est_minutes: 16
status: reviewed
related:
  - rag-retrieval/recall-vs-precision-trade-off
  - rag-retrieval/reranking
  - rag-retrieval/retrieval-failure-diagnosis
  - rag-retrieval/hybrid-search
  - evals/golden-dataset
  - evals/faithfulness-groundedness
  - ml-fundamentals/precision-recall-f1
sources:
  - Järvelin and Kekäläinen, 2002 — Cumulated Gain-Based Evaluation of IR Techniques
  - Manning, Raghavan and Schütze, 2008 — Introduction to Information Retrieval
  - Thakur et al., 2021 — BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models
---

## TL;DR
A RAG answer can only be as good as the passages the retriever puts in front of the model, so the
retriever is measured on its own against queries with known relevant documents. recall@k asks whether
the relevant passages made it into the top k the generator will see; MRR asks how high the first
relevant hit was; NDCG asks whether the ranking is right when relevance comes in grades. Choosing k to
match the context budget and building honest relevance judgments matter more than the formulas.

## Why it exists
When a RAG system answers wrongly, there are two suspects: the retriever did not surface the evidence,
or the generator mishandled evidence that was there. An end-to-end answer score cannot tell them apart,
so it cannot tell you what to fix. Retrieval evaluation isolates the first suspect, and it does so
cheaply: no LLM call is needed, so a change to chunking, embedding model, hybrid weights or filters can
be scored on hundreds of queries in seconds and run on every commit.

Three naive alternatives fail. Eyeballing results for a few queries does not scale and misses
regressions. Reading the retriever's similarity scores as quality fails because similarity is not
relevance and scores are not comparable across models or even across queries. Treating retrieval as
classification and reporting precision and recall at a score threshold fails because the consumer of the
results is a model that reads a *ranked* top-k list: a relevant chunk at rank 3 helps, the same chunk at
rank 40 does not exist. The metrics therefore have to be rank-aware and cut off at the k that the
generator actually receives.

Three metrics exist because they answer three different questions. Did everything needed get in
(recall@k)? How quickly does the list reach something useful (MRR)? Is the ordering right when some
passages are more relevant than others (NDCG)? Which one is primary depends on what sits downstream.

## How it works
### Ingredients: queries and relevance judgments
You need a query set that resembles production traffic, including the short, ambiguous and multi-part
queries that real users send, and for each query a set of relevant chunks or documents, the **qrels**.
Relevance can be binary or graded (0–3). Sources: golden question–answer pairs where the answer's source
passage is known; SME labeling; LLM-assisted labeling with a human audit; and **pooling**, where the
top results of several systems are merged and judged so no system is penalised for finding something the
labelers never looked at. Judge at the granularity the retriever returns: document-level labels applied
to chunk-level retrieval mark the wrong chunk of the right document as a hit.

### recall@k
recall@k = |relevant ∩ top-k| / |relevant|, averaged over queries. It is the primary metric for the
first retrieval stage because the generator can ignore an irrelevant passage but cannot recover a
missing one. Two conventions to be explicit about: if a query has more relevant items than k, recall@k
cannot reach 1, so either choose k above the typical number of relevant items or report R-precision; and
when each query has exactly one relevant item, recall@k equals **hit rate@k** (was any relevant item in
the top k), which is what many RAG dashboards actually compute under the name recall. **precision@k** =
|relevant ∩ top-k| / k measures how much of the context window is noise, which matters for cost and for
the generator's sensitivity to distractors.

### MRR
For each query, the reciprocal rank is 1 / (rank of the first relevant item), or 0 if none appears in
the top k; MRR is the mean. The reward is steep: rank 1 scores 1, rank 2 scores 0.5, rank 3 scores
0.33. Use it when a single good hit is enough and position matters, such as showing one citation or
when the generator attends mostly to the first passages. It ignores everything after the first hit, so
it says nothing about queries that need several passages.

### NDCG@k
Discounted cumulative gain sums the relevance of each result with a position discount:

```
DCG@k  = Σ_{i=1..k} gain(rel_i) / log2(i + 1)
NDCG@k = DCG@k / IDCG@k        (IDCG = DCG of the ideal ordering of the same judgments)
```

gain is either rel_i itself or 2^rel_i − 1, the latter emphasising highly relevant items. The log
discount exists because position matters with diminishing weight, smoother than MRR's 1/rank cliff.
The normalisation exists so that queries with many relevant items do not dominate the average and so
every query scores in [0, 1]. NDCG is the standard for graded judgments and for comparing rankers; IR
benchmarks such as BEIR report NDCG@10.

### Picking k and splitting stages
Set k to the number of passages the generator receives after any reranking, because that is where
missing evidence becomes a wrong answer. A two-stage pipeline gets two evaluations: the first-stage
retriever at a larger k (what the reranker has to work with) scored by recall, and the reranker at the
final k scored by NDCG or MRR. Raising k raises recall but costs tokens and adds distractors, so measure
answer quality against k to find the knee.

### Statistical hygiene
Compare two systems on the same queries with a paired bootstrap over queries and report intervals;
means on a few dozen queries move by several points on resampling. Segment by query type, because an
average of 0.85 can hide a category at 0.3.

## Example
All three metrics for one query, then the numbers worked by hand. Relevant chunks are c3 (grade 2, the
policy clause that answers the question) and c7 (grade 1, a related FAQ entry).

```python
import math

def recall_at_k(ranked, relevant, k):
    return len(set(ranked[:k]) & set(relevant)) / len(relevant)

def reciprocal_rank(ranked, relevant, k):
    for i, d in enumerate(ranked[:k], start=1):
        if d in relevant:
            return 1.0 / i
    return 0.0

def ndcg_at_k(ranked, grades, k):
    """grades: {doc_id: relevance grade}; anything missing is grade 0."""
    dcg = sum(grades.get(d, 0) / math.log2(i + 1) for i, d in enumerate(ranked[:k], start=1))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 1) for i, g in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 0.0

ranked = ["c1", "c3", "c5", "c7", "c2"]          # retriever output, best first
grades = {"c3": 2, "c7": 1}
relevant = list(grades)
print("recall@3 =", recall_at_k(ranked, relevant, 3))          # 0.5   (c7 sits at rank 4)
print("recall@5 =", recall_at_k(ranked, relevant, 5))          # 1.0
print("RR       =", reciprocal_rank(ranked, relevant, 5))      # 0.5   (first hit at rank 2)
print("NDCG@5   = %.3f" % ndcg_at_k(ranked, grades, 5))       # 0.643
# Average each metric over the query set; compare two systems with a paired bootstrap over queries.
```

By hand: DCG@5 = 2 / log2(3) + 1 / log2(5) ≈ 1.262 + 0.431 = 1.693. The ideal order puts c3 first and
c7 second: IDCG = 2 / log2(2) + 1 / log2(3) ≈ 2 + 0.631 = 2.631, so NDCG@5 ≈ 0.643. recall@5 is perfect
and MRR is 0.5, yet NDCG shows the ranking is far from ideal: an irrelevant chunk occupies the top slot
and the second relevant item is pushed to rank 4. If the generator receives three passages, recall@3 is
the number that predicts the wrong answer.

## Trade-offs
- recall@k is the right target for a first-stage retriever but is blind to ordering and to noise; when
  the generator is sensitive to position or distractors, add NDCG and precision@k.
- MRR is easy to explain and rewards getting the top slot right, but it ignores everything after the
  first hit; for multi-hop questions that need several passages, use recall@k or NDCG instead.
- NDCG uses graded relevance, which is more informative but costlier to label and has lower annotator
  agreement than binary labels; with a small labeling budget, binary judgments and recall are safer.
- A larger k raises recall but adds tokens and distractors; once answer quality stops improving with k,
  the extra recall is paying for nothing.
- LLM-generated qrels scale far beyond SME labeling but can over-credit passages that merely repeat the
  query's terms; when the retriever under test is lexical, audit a human-labeled slice before trusting them.

## Pitfalls
- Incomplete qrels, with unjudged chunks counted as irrelevant; the symptom is a new retriever that finds
  different but valid passages scoring *lower*, which pooling and re-judging its top results corrects.
- Eval queries written by reading the chunks; the symptom is near-perfect offline recall and poor results
  on real user phrasing.
- Document-level labels with chunk-level retrieval; the symptom is "relevant document retrieved" while the
  chunk that reached the generator lacks the answer.
- Reporting only the mean; the symptom is average recall of 0.85 with short or ambiguous queries at 0.3.
- Changing chunk size and comparing recall@k against the old qrels; the symptom is numbers that are not
  comparable because the unit of relevance changed.
- Near-duplicate chunks in the index; the symptom is a top-k full of copies, recall of distinct relevant
  items stalling while precision looks fine.

## When to use
Run retrieval evaluation on every change to the embedding model, chunking, index, hybrid weights,
reranker or metadata filters, and reach for it first whenever answers are wrong, because "was the
evidence present?" is the cheapest question to answer. Track recall at the first-stage k and NDCG or MRR
at the final k, with paired intervals and a per-category breakdown. Build the query set from real
traffic, on the order of a few hundred queries (illustrative), stratified by type. In interviews, define
the three metrics, say which one you would make primary and why, and explain how you would construct
the judgments.

## Role lens
- **Engineer:** Keeps the qrels file and the metrics script in CI so an embedding-model swap that drops
  recall@20 blocks the merge, and traces failed queries to chunking, embedding or filtering.
- **Architect:** Sets the retrieval target at the k the context budget allows and designs the two-stage
  pipeline, recall-oriented first stage and precision-oriented reranker, that the metrics map onto.
- **FDE:** Builds the query set from the customer's search logs and tickets, has their SMEs judge
  relevance, and uses recall@k to show whether a failure is "not indexed" or "not found".

## Check yourself
**Q:** A customer's RAG assistant answers about 30% of questions wrongly. Before touching prompts, what
retrieval evaluation would you set up, which metric would you look at first, and how would you read it?
**A:** Build one to three hundred queries from real tickets and have SMEs mark the relevant chunks, then
run the current pipeline and compute recall at the k that actually reaches the generator, plus recall at
a larger k for the first stage. If recall@k is low, retrieval is the bottleneck, and the failures sort
into three buckets with different fixes: the passage is not in the index (ingestion), it is indexed but
ranked low (embedding, chunking, hybrid weighting), or it is filtered out (metadata or ACL rules). If
recall@k is high and answers are still wrong, the generator or faithfulness is the problem. MRR and NDCG
then say whether a reranker is worth adding. Every comparison between pipeline variants runs on the same
queries with a paired bootstrap.

## Quiz
### Q: Each query has one relevant chunk; the retriever ranks it first for half the queries and fourth for the rest, with k = 5. What are recall@5 and MRR?
- [ ] recall@5 = 1.0 and MRR = 1.0
- [ ] recall@5 = 0.5 and MRR = 0.625
- [x] recall@5 = 1.0 and MRR = 0.625
- [ ] recall@5 = 1.0 and MRR = 0.5
> The relevant chunk is always within the top 5, so recall@5 is 1.0. Reciprocal ranks are 1 and 0.25,
> averaging to 0.625; MRR exposes the ordering problem that recall@5 hides.

### Q: You are evaluating a reranker with graded relevance labels and several relevant passages per query. Which metric fits best?
- [ ] MRR
- [ ] Hit rate@k
- [x] NDCG@k
- [ ] precision@1
> NDCG is the only one of the four that uses relevance grades and credits every relevant item with a
> position discount; MRR and hit rate stop at the first hit, and precision@1 looks at one slot.

### Q: A new embedding model lowers recall@10 from 0.82 to 0.74 on your qrels, but SMEs reviewing its results say they look better. What is the most likely explanation?
- [ ] The SMEs are biased toward novelty
- [ ] The new model is genuinely worse and the SMEs are wrong
- [x] The qrels are incomplete: the new model surfaces relevant chunks that were never judged and are counted as irrelevant; pool its results and re-judge
- [ ] k = 10 is too small to measure a dense retriever
> Judgments made from the old system's results cannot credit passages only the new system finds. Pooling
> the top results of both systems and judging the union is the standard remedy.
