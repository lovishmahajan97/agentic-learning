---
id: recall-vs-precision-trade-off
module: rag-retrieval
name: Recall vs precision trade-off
kind: concept
difficulty: core
est_minutes: 13
status: fact-checked
related:
  - ml-fundamentals/precision-recall-f1
  - rag-retrieval/reranking
  - rag-retrieval/hybrid-search
  - rag-retrieval/lost-in-the-middle
  - rag-retrieval/retrieval-failure-diagnosis
  - rag-retrieval/chunking-strategies
  - evals/retrieval-evaluation
  - evals/golden-dataset
sources:
  - Manning, Raghavan and Schütze, 2008 — Introduction to Information Retrieval
  - Liu et al., 2023 — Lost in the Middle: How Language Models Use Long Contexts
---

## TL;DR
Recall is the share of the answer-bearing passages you actually retrieved; precision is the share of what
you retrieved that is answer-bearing. Widening top-k raises recall and lowers precision at once, and in
RAG both failures hurt: a missing passage means the model cannot answer, a noisy context means it may
answer from the wrong passage. The standard resolution is to stage retrieval: cheap and wide for recall
first, expensive and narrow for precision second, each measured with its own metric.

## Why it exists
A retriever returns a ranked list and you must cut it somewhere. Cut early (top-3) and the list is clean
but incomplete: any answer passage ranked fourth is gone, and nothing downstream can recover it. Cut late
(top-50) and every relevant passage is probably in there, buried among forty-odd irrelevant ones that the
generator now has to read, pay for, and be distracted by. There is no cut that is best on both axes,
because the ranking function is imperfect: if it were perfect, every relevant passage would sit above
every irrelevant one and a single threshold would give recall 1.0 and precision 1.0 together.

Classical search tolerated this because a human scanned the list. In RAG, the consumer of the list is a
language model with a finite context and a known weakness: with many passages, accuracy on the relevant
one drops when it sits in the middle of the prompt (the "lost in the middle" result), and passages that
look similar but conflict (an old version of a policy next to the new one) can steer the answer wrong.
So precision is not just a cost concern; it affects correctness. At the same time recall is a hard
floor: barring answers the model already knows from pretraining, a RAG system cannot be more accurate
than the fraction of questions whose evidence reached the prompt. The naive fix, "just retrieve more and let the model sort it out", therefore converts a recall
problem into a precision problem instead of solving either. The way out is to recognise that cheap
retrievers and expensive rerankers have different cost-per-candidate profiles, and to let each one
optimise the metric it is good at.

## How it works
### The two numbers
For a query with relevant set R (the chunks that actually contain the answer) and the retrieved top-k
set S_k:

```
precision@k = |R ∩ S_k| / k
recall@k    = |R ∩ S_k| / |R|
```

As k grows, recall@k can only stay flat or rise, since S_k only gains members. Precision@k usually falls,
because each extra slot is filled by a lower-ranked item that is less likely to be relevant. Plotting
recall@k against k gives a curve that rises steeply then flattens; the knee is where additional
candidates stop buying recall. F1 (the harmonic mean) and F-beta (recall-weighted or
precision-weighted) collapse the pair into one number when you must, but in RAG you rarely should,
because the two numbers belong to different stages of the pipeline.

Rank-aware metrics refine the picture: MRR (reciprocal rank of the first relevant hit) and nDCG (graded
relevance, discounted by position) reward putting the right passage near the top, which matters when
the generator reads the prompt in order.

### Why both numbers hurt a generator
Recall failure is unrecoverable downstream: no prompt engineering retrieves a passage that is absent.
Precision failure is partially recoverable, because the model can ignore noise, but only partially. Three
costs are concrete. Tokens: k chunks of 300 tokens each is 300 × k input tokens per call (15,000 at
k = 50). Position: the
relevant passage lands at an arbitrary position among distractors, and position matters. Distractors:
near-duplicates and stale versions are exactly the items a similarity search ranks highly, so the noise is
not random noise; it is the most confusable material in the corpus.

### The staged resolution
1. **Candidate generation, optimised for recall.** An ANN index over embeddings, an inverted index for
   BM25, or both fused (hybrid) return a wide list, say 50–200 candidates. These are cheap per candidate
   because the query is encoded once and the index does the rest.
2. **Reranking, optimised for precision.** A cross-encoder reads (query, passage) jointly and scores
   relevance far more accurately than vector distance, but it costs a full forward pass per pair, so it
   is only affordable on the short list stage 1 produced. It reorders the candidates so the top-n is
   dense with relevant material.
3. **Selection under a budget.** Take the top-n that fit the context budget, optionally dropping anything
   below a relevance threshold so that an unanswerable question yields an empty context rather than a
   confident answer built on junk.

Each stage is measured on its own terms: recall@k for stage 1 (did the evidence make it into the
candidate set?), precision@n or nDCG@n for stage 2 (is the evidence at the top?), and answer accuracy
or faithfulness for the whole. When end-to-end accuracy drops, these numbers tell you which stage broke.

### The other levers
Chunk size moves both numbers: smaller chunks make sharper keys (precision per hit rises) but spread a
multi-part answer over more chunks (recall needs more hits). Hybrid search raises recall by catching
lexical matches dense retrieval misses. Query expansion raises recall and lowers precision. Metadata
filters raise precision by restricting the candidate pool and destroy recall if the filter is wrong.
Score thresholds raise precision but, because similarity scores are not calibrated across queries, a
fixed threshold silently returns nothing for some queries.

## Example
A worked evaluation on a small golden set. Each question has human-labelled answer-bearing chunk ids;
the code computes recall@k and precision@k for stage 1 (hybrid retrieval) and for stage 2 (reranked).

```python
from statistics import mean

golden = {  # question -> set of chunk ids that contain the answer (labelled by hand)
    "What is the SLA credit for 99.5% availability?": {"sla-table", "credit-defs", "credit-exclusions"},
    "How long are audit logs retained?": {"retention-policy"},
}

def precision_recall(retrieved, relevant, k):
    top = retrieved[:k]
    hit = len(set(top) & relevant)
    return hit / k, hit / len(relevant)

def evaluate(search, k):
    p, r = [], []
    for q, relevant in golden.items():
        pk, rk = precision_recall(search(q), relevant, k)
        p.append(pk); r.append(rk)
    return mean(p), mean(r)

# search_hybrid(q) -> ranked chunk ids; rerank(q, ids) -> ids reordered by a cross-encoder
stage1 = lambda q: search_hybrid(q)
stage2 = lambda q: rerank(q, search_hybrid(q)[:50])
for k in (5, 20, 50):
    print("stage1", k, evaluate(stage1, k))
print("stage2", 5, evaluate(stage2, 5))
```

Illustrative output for the first question: stage-1 top-5 finds `sla-table` and `credit-defs` but not
`credit-exclusions`, whose text says "service credits shall not apply" without the word SLA, so
precision@5 = 0.4 and recall@5 = 0.67. Hybrid top-50 catches all three (recall 1.0) at precision 0.06.
Reranking those 50 and keeping 5 puts all three in the top-5: recall 1.0, precision 0.6, and the prompt
carries 5 × 300 = 1,500 tokens instead of 15,000. Over the whole golden set you would then report
recall@50 for stage 1 and precision@5 for stage 2 as separate dashboards.

## Trade-offs
- A larger k raises recall but costs tokens, latency and position sensitivity; it stops paying once the
  recall curve has flattened, and it flips to harmful when distractors outnumber evidence.
- A reranker buys precision at the cost of a forward pass per candidate pair, typically tens to hundreds
  of milliseconds for 50–100 pairs on a GPU and more on CPU; it is not worth it when stage 1 is already precise or latency is tight.
- A relevance threshold raises precision and lets the system say "no evidence", but similarity scores are
  not calibrated across queries, so a fixed cut-off that works for one query returns nothing for another.
- Smaller chunks raise precision per hit and sharpen embeddings, but a multi-part answer then needs several
  hits, so recall at fixed k drops; the balance depends on how self-contained the corpus units are.

## Pitfalls
- Measuring only end-to-end answer accuracy, so a failure cannot be attributed to recall (evidence never
  retrieved) or precision (evidence retrieved but ignored); the symptom is that every fix is a guess.
- Tuning k on a handful of queries that someone complained about; the symptom is regressions on the
  queries nobody was looking at, visible only once a golden set exists.
- Labelling relevance by embedding similarity instead of by whether the chunk answers the question; the
  symptom is excellent retrieval metrics alongside wrong answers.
- Counting a hit when any chunk of the right document is retrieved; the symptom is high "recall" while
  the specific clause that answers the question is still missing from the prompt.
- Treating precision as a cost-only concern; the symptom is answers that cite a plausible but outdated
  passage that sat next to the correct one in the context.

## When to use
This is not an optional technique but a lens to apply whenever a RAG system is being designed or
debugged. Reach for the two-stage shape by default when the corpus is large enough that stage 1 cannot
be precise on its own, which is almost always. Decide k by plotting recall@k on a golden set and picking
the knee, then decide n (what reaches the prompt) from the generator's context budget and the measured
precision after reranking. Day to day it shows up as a dashboard with recall@k for the candidate stage
and precision@n or nDCG@n for the final list, tracked separately so that a regression in one is not
masked by the other. In interviews, "how would you improve a RAG system that answers 70% of questions"
is a recall-versus-precision question in disguise: the strong answer starts by measuring which one is
failing before proposing a fix.

## Role lens
- **Engineer:** Builds the golden set and the metric script, tunes k and the reranker cut-off, and wires
  recall@k and precision@n into the eval harness so every retrieval change is scored.
- **Architect:** Owns the pipeline shape: whether a reranker is worth its latency, what context budget the
  generator gets, and which stage each team's metric belongs to.
- **FDE:** Explains to a customer why "retrieve more documents" made answers worse, and uses the two
  numbers to turn "the bot is wrong sometimes" into a specific, measurable retrieval failure.

## Check yourself
**Q:** A RAG assistant over internal policies answers about 70% of questions correctly with dense top-5
retrieval. Before changing anything, how do you decide between raising k, adding a reranker, changing
chunking, or fixing the prompt?
**A:** Build or reuse a golden set with labelled answer-bearing chunks, then compute recall@5 and recall@50
on the current retriever. If recall@50 is much higher than recall@5, the evidence is indexed but ranked
too low, so a reranker (or a larger k if the generator tolerates it) is the right lever. If recall@50 is
also low, the index itself cannot surface the evidence: look at chunking, hybrid search or the embedding
model. If recall@5 is already high but answers are wrong, retrieval is not the bottleneck; inspect
precision@5 for distractors and stale versions, check where the evidence sits in the prompt, and look at
the generation prompt. Measuring first prevents spending a week on a reranker when the passage was never
in the candidate set.

## Quiz
### Q: Why does a two-stage retrieve-then-rerank pipeline exist instead of a single better retriever?
- [ ] Rerankers are cheaper per candidate than ANN search
- [x] Cheap first-stage retrieval can afford to be wide for recall, while the expensive reranker can only be afforded on a short list, where it buys precision
- [ ] Vector databases cannot return more than a few results per query
- [ ] Rerankers improve recall by finding documents the first stage missed
> A cross-encoder scores each (query, passage) pair with a full forward pass, so it is only affordable on
> the candidates stage 1 already found; it reorders them for precision, which can raise recall@5, but it can never lift recall above what the
> candidate list already had (recall@50 here).

### Q: Recall@50 is 0.92 but recall@5 is 0.55 on your golden set. Which change addresses the gap most directly?
- [ ] Switching to smaller chunks
- [ ] Lowering the similarity threshold
- [x] Adding a reranker over the top-50 candidates
- [ ] Increasing chunk overlap
> The evidence is already in the candidate set but ranked low, which is exactly what a reranker fixes.
> Smaller chunks or overlap change what gets indexed, which is the fix when recall@50 itself is low.

### Q: What does a fixed similarity-score threshold across all queries most commonly cause?
- [ ] Higher recall on every query
- [x] Empty contexts for some queries, because similarity scores are not calibrated across queries
- [ ] Duplicate passages in the prompt
- [ ] Slower first-stage retrieval
> Cosine or dot-product scores are relative to each query's embedding, so a value that marks "relevant"
> for one query can be above every candidate for another, which silently returns nothing.
