---
id: retrieval-failure-diagnosis
module: rag-retrieval
name: Retrieval failure diagnosis
kind: practice
difficulty: advanced
est_minutes: 16
status: reviewed
related:
  - evals/retrieval-evaluation
  - evals/error-analysis
  - rag-retrieval/recall-vs-precision-trade-off
  - rag-retrieval/reranking
  - rag-retrieval/lost-in-the-middle
  - rag-retrieval/document-parsing
  - observability/tracing
  - rag-retrieval/hybrid-search
---

## TL;DR
When a RAG system gives a wrong answer, the evidence was lost at one specific stage: never ingested,
parsed or chunked badly, missing from the index, filtered out, ranked too low, dropped from the
context, or ignored by the model. Diagnosis means tracing the gold evidence through each stage and
finding the *first* one where it disappears, because each stage has a different fix and tuning the wrong
one changes nothing.

## Why it exists
A RAG pipeline is a chain of lossy steps, and the only thing the user sees is the end of it. "The bot
said our refund window is 14 days, it is 30" could mean the policy PDF was never synced, the OCR turned
"30" into "3O", the number landed in a chunk with no mention of refunds, an ACL filter excluded the
document, the embedding ranked a blog post above it, the reranker ranked it 6th when only the top 5 are kept, or it was in
the prompt and the model still preferred an older FAQ. These have nothing in common except the symptom.

The naive response is to change something global: swap the embedding model, raise top-k, rewrite the
prompt, add a reranker. Without knowing where the evidence was lost this is a coin toss, and worse, it can
look like it worked because the handful of examples you tried happen to improve while others regress.
Teams burn weeks re-embedding a corpus when the real problem was a parser dropping every table.

Answer-level metrics cannot separate these causes either. An LLM judge scoring "correct / incorrect" tells
you the rate of failure, not its location. You need per-stage visibility: for each failing query, where
was the right chunk at every step? That turns an argument about models into a counting exercise.

## How it works
### Step 1: get failures with known gold evidence
Collect failing queries from evals, thumbs-down feedback or support escalations. For each, have someone
(or an LLM, spot-checked by a human) identify the *gold* source: the document and passage that actually
answers it. Without a gold target you can only guess, so this labelling is the expensive but
non-negotiable part. A few dozen labelled failures usually reveal the dominant cause.

Label gold as *document plus answer span* (the sentence or cell that settles the question), not as a chunk
ID, because chunk IDs are an artifact of the current chunker and change the moment you re-chunk; map the
span to whichever chunks contain it at diagnosis time. Some questions need several pieces of evidence (a
limit in one section, the exception in another, or two documents to compare). Track each gold span
separately, because the answer fails if *any* of them is lost, and the two may be lost at different stages.

### Step 2: walk the stages in pipeline order
For each failure, ask the same questions in the order data flows, stopping at the first "no":

1. **Corpus coverage.** Is the answer in any source document at all? If not, it is a content gap, not a
   retrieval bug, and the right behaviour is abstention.
2. **Ingestion and freshness.** Was the current version of the document synced, or is the index serving a
   stale or deleted copy?
3. **Parsing.** Does the extracted text contain the answer intact? Check tables, scanned pages, headers,
   multi-column layouts and numbers.
4. **Chunking.** Is the answer inside one chunk together with enough context to be found, or split across
   a boundary, or orphaned from its heading?
5. **Indexing.** Can you look up that chunk ID in the vector and keyword indexes? Embedding jobs fail
   silently; a dimension mismatch or a crashed batch leaves holes.
6. **Filtering.** Do metadata, tenant or ACL filters exclude it for this user? Run the query with and
   without filters.
7. **Query transformation.** If the pipeline rewrites, expands, routes or decomposes the query, compare
   the raw and transformed queries. A rewriter that drops the product code, a router that sends a billing
   question to the HR index, or a decomposition that loses the constraint all look like recall failures
   downstream. Re-run retrieval with the raw query: if gold appears, the loss is here.
8. **Candidate recall.** At a generous depth (say k=100), what rank does the gold chunk get from dense
   search, from BM25, and after fusion? Absent at k=100 is a representation problem: vocabulary
   mismatch, IDs and codes that embeddings blur, or a query that needs rewriting or decomposition. Look
   at what *did* rank above it: a near-duplicate older version or a generic overview page outranking the
   specific answer is a different fix (dedup, freshness metadata) from a list of unrelated chunks.
9. **Ranking.** Present at 40 but cut at final top-k 5? That is precision: a reranker, better fusion
   weights or a larger final k. Note the asymmetry with the previous step: recall at depth is a property
   of the representation, ordering within the candidates is a property of the scorer, which is why the
   two numbers point at different components.
10. **Context assembly.** Did it survive token budgeting, deduplication and ordering, or was it truncated or
   buried mid-context?
11. **Generation.** It was in the prompt, near the top, and the model still answered wrong: this is a
    grounding, prompt or model problem, not retrieval at all. Sub-cases are worth separating: the context
    also contained a conflicting passage the model preferred (a retrieval-precision problem in disguise),
    the model ignored the context in favour of its parametric knowledge, or the answer required arithmetic
    or combining two passages and the model got the reasoning wrong.

### Step 3: count, then fix the biggest bucket
Tally first-loss stage across failures. The distribution decides the work, *because* each fix only helps
its own bucket: a reranker cannot recover a chunk that was never indexed, and re-embedding cannot fix a
table the parser flattened. Then turn the labelled failures into regression tests with per-stage
assertions (`gold in candidates@100`, `gold in context`) so the fix is held.

### What makes this cheap
Log a trace per request: query, rewritten queries, filters applied, candidate IDs with scores from each
retriever, post-rerank order, and final context IDs. With that trace, steps 5 to 9 are lookups instead of
re-runs. Without it, every diagnosis means reproducing state that may have changed since.

## Example
A function that finds the first stage where the gold chunk is lost, given a logged trace.

```python
def first_loss(gold: str, trace: dict, store: dict) -> str:
    """trace: per-request log; store: chunk_id -> {'text':..., 'indexed': bool}."""
    if gold not in store:
        return "parse/chunk: gold passage not in any chunk"
    if not store[gold]["indexed"]:
        return "indexing: chunk exists but has no vector/keyword entry"
    if gold in trace["filtered_out"]:
        return f"filtering: excluded by {trace['filters']}"
    ranks = {name: ids.index(gold) + 1 if gold in ids else None
             for name, ids in trace["candidates"].items()}   # e.g. dense, bm25, fused @100
    if all(r is None for r in ranks.values()):
        return f"recall: absent from all candidate lists {ranks}"
    if gold not in trace["reranked"][: trace["final_k"]]:
        return f"ranking: candidate ranks {ranks}, cut by final_k={trace['final_k']}"
    if gold not in trace["context_ids"]:
        return "context assembly: dropped by token budget or dedup"
    pos = trace["context_ids"].index(gold) + 1
    return f"generation: in context at position {pos}, model did not use it"

trace = {
    "filters": {"tenant": "acme"}, "filtered_out": [],
    "candidates": {"dense": ["c9", "c4", "c7"], "bm25": ["c7", "c2", "c4"]},
    "reranked": ["c9", "c4", "c2", "c7"], "final_k": 3, "context_ids": ["c9", "c4", "c2"],
}
store = {c: {"text": "...", "indexed": True} for c in ["c2", "c4", "c7", "c9"]}
print(first_loss("c7", trace, store))
# ranking: candidate ranks {'dense': 3, 'bm25': 1}, cut by final_k=3
```

Run this across, illustratively, 60 labelled failures and you might find 25 ranking, 15 parsing, 10
generation, 6 content gaps, 4 filtering. That says: add or tune the reranker and fix the parser first;
the embedding model is not the bottleneck at all.

## Trade-offs
- Labelling gold evidence for every failure is slow, but it is the only thing that makes stage attribution
  possible; LLM-assisted labelling is faster yet needs spot checks because it shares the system's blind spots.
- Full per-request traces make diagnosis a lookup, at the cost of storage and of logging sensitive text,
  which flips to logging IDs and hashes only when the corpus contains regulated data.
- Fixing the largest bucket first gives the biggest win, but buckets interact: improving parsing creates new
  chunks that may shift ranking, so re-run attribution after each change instead of fixing several at once.
- Measuring recall at a deep k (100) isolates representation from ranking, but costs extra queries per eval;
  it is worth it whenever you are about to choose between an embedding change and a reranker.

## Pitfalls
- Changing the embedding model first because it is the most visible component; the symptom is a costly
  re-index that moves aggregate scores by noise while the same queries keep failing.
- Treating content gaps as retrieval bugs; the symptom is tuning that "improves" recall by surfacing loosely
  related chunks, which raises confident wrong answers instead of abstentions.
- Diagnosing against today's index for a failure logged last week; documents changed in between, so the
  reproduction succeeds and the bug is closed as "cannot reproduce".
- Reading only the final context and blaming the model; without candidate lists you cannot tell "never
  retrieved" from "retrieved and cut", and the symptom is prompt rewrites that do not help.
- Fixing failures one by one without counting; the symptom is whack-a-mole where each fix regresses another
  query because nobody saw the distribution.
- Recording gold evidence as chunk IDs; the symptom is measured recall collapsing to near zero right
  after a chunking change, because the labelled IDs no longer exist, while real answer quality is unchanged.

## When to use
Use this whenever answer quality is below target and the cause is not obvious, before any model or index
change, and as the standing triage process for production feedback. Measure recall@k at candidate depth,
recall at final k, and "gold in context" separately, so each stage has its own number. It is also the
right frame for an interview question like "your RAG bot is wrong 20% of the time, what do you do?": the
strong answer is a staged trace with counted buckets, not a list of techniques to try. If you lack gold
labels and traces, the first deliverable is building those, not tuning.

## Role lens
- **Engineer:** Instruments the pipeline to log candidate IDs, scores, filters and final context per request,
  and writes the attribution script and per-stage regression tests.
- **Architect:** Requires stage-level observability and gold-labelled eval sets as part of the RAG platform,
  so quality work is driven by measured failure distributions rather than opinion.
- **FDE:** Sits with customer SMEs to label failing questions, then shows them a bucket chart that separates
  "your documents lack this" from "our retrieval missed it", which changes the conversation.

## Check yourself
**Q:** Recall@5 on your eval set is 62%, recall@100 is 91%. A colleague proposes switching to a larger
embedding model. What do you conclude and what do you do instead?
**A:** The gold chunk is in the top 100 for 91% of queries but only reaches the top 5 for 62%, so most
misses are ranking failures, not representation failures. A better embedding might help a little, but the
cheaper and more targeted fix is a cross-encoder reranker over the top 50 to 100, better hybrid fusion, or
a larger final k if the context budget allows. The remaining 9% absent at depth 100 need separate
attribution: parsing, chunking, filtering or vocabulary mismatch, possibly helped by BM25 or query
rewriting. I would also check generation failures among queries where gold was in context, since those
would not move with any retrieval change.

## Quiz
### Q: The gold chunk, whose parsed text you have confirmed contains the answer intact, appears at rank 2 in BM25 results and nowhere in dense results, but the answer is still wrong. What do you check next?
- [ ] Re-embed the corpus with a larger embedding model
- [x] Whether it survived fusion, reranking and final top-k into the actual context
- [ ] Whether the document was parsed correctly
- [ ] Whether the LLM temperature is too high
> Parsing is ruled out by the stem, and a BM25 hit at rank 2 shows the chunk is indexed and passes the
> filters. The next stages in order are fusion, reranking and context assembly (a dense miss can still sink
> it in fusion); only if it reached the context does generation become the suspect.

### Q: Why is "first stage where the gold evidence is lost" the right unit of attribution?
- [ ] Because later stages are always more expensive to fix
- [ ] Because only the first stage is observable in logs
- [x] Because once evidence is lost, every later stage fails as a consequence, so only the first loss has a fix that helps
- [ ] Because LLM judges can only evaluate the first stage
> If a chunk was never indexed, the reranker and generator also "fail" on it, but changing them cannot help.
> Attributing to the first loss avoids double counting and points to the one fix that recovers the query.

### Q: A failure cannot be reproduced: today the same query retrieves the right chunk. What is the most likely reason and the fix to the process?
- [ ] The ANN index is nondeterministic; switch to exact search
- [x] The corpus or index changed since the failure; log per-request traces with candidate IDs and index version
- [ ] The user typed the query differently; normalize queries
- [ ] The reranker randomly drops candidates; add a seed
> Documents are synced and re-chunked continuously, so reproducing later tests a different system. Logging
> what was actually retrieved, with the index version, makes diagnosis a lookup on the real state.
