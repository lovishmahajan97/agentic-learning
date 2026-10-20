---
id: rag-architecture
module: system-design
name: RAG architecture
kind: pattern
difficulty: core
est_minutes: 16
related:
  - rag-retrieval/chunking-strategies
  - rag-retrieval/hybrid-search
  - rag-retrieval/reranking
  - rag-retrieval/acl-aware-retrieval
  - rag-retrieval/freshness-document-deletion-index-consistency
  - rag-retrieval/long-context-vs-rag
  - data-architecture/vector-database-architecture
  - system-design/caching-strategy-and-cache-invalidation
sources:
  - Lewis et al., 2020 — Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks
  - Cormack et al., 2009 — Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods
  - Liu et al., 2023 — Lost in the Middle: How Language Models Use Long Contexts
---

## TL;DR
RAG is two pipelines joined by an index: an offline path that turns documents into searchable chunks,
and an online path that retrieves the few chunks relevant to a query and hands them to the model with
instructions to answer from them. It exists because model weights are frozen, general and
permission-blind, while the facts a product needs are changing, private and access-controlled. The
architecture decisions are about where each pipeline runs, what the index stores, and how failures are
measured.

## Why it exists
A language model answers from two places: its weights and its context window. The weights are a
compressed snapshot of public text at training time. They do not contain your pricing table, last week's
incident report or the customer's contract, and they cannot be told that user A may see document X and
user B may not. Fine-tuning does not fix this: it teaches style and format reliably and facts
unreliably, it is stale the moment a document changes, and it has no notion of provenance or per-user
access, so you can neither cite a source nor revoke one.

The context window is the other place, and the naive design is to put everything there. For a handful
of pages that is the right call, and the long-context-vs-RAG lesson covers when. It stops working for
three reasons. Cost and latency scale with input tokens on every request, so a 500-page knowledge base
read on every question is unaffordable even when it fits. Attention quality degrades when relevant
material is buried in the middle of a very long prompt (the lost-in-the-middle effect), so more context
can mean worse answers. And access control still has to happen somewhere before the prompt is built.

Retrieval solves the selection problem: pick the few passages that matter for this query, for this
user, right now, and let the model do what it is good at, which is reading and synthesizing. The
architecture exists to make that selection fast, fresh, permission-aware and measurable.

## How it works
### The offline pipeline: documents to index
1. **Ingest.** Connectors pull documents from sources (wikis, tickets, object storage, databases)
   together with metadata: owner, ACL, timestamps, source URL. Ingestion is event- or schedule-driven
   *because* freshness is a property of this path and nothing downstream can repair a stale index.
2. **Parse and normalize.** PDFs, HTML and slides become clean text with structure preserved (headings,
   tables). This is where most silent quality loss happens; garbage in here is unrecoverable at query
   time.
3. **Chunk.** Text is split into retrieval units, typically a few hundred tokens, with overlap or
   parent–child linking. Chunks are the unit of retrieval *because* an embedding summarizes one idea well
   and a whole document badly, and because you want to pay for a paragraph of context, not a chapter.
4. **Embed and index.** Each chunk gets a dense vector from an embedding model and usually a sparse
   (BM25) representation too. Both are stored with the chunk text and metadata in a store that supports
   approximate nearest-neighbour search plus filtering. Record the embedding model version with every
   vector *because* vectors from different models are not comparable, so a model upgrade means a
   re-index.

### The online pipeline: query to answer
1. **Query understanding.** Optionally rewrite the query: expand acronyms, resolve "it" from the
   conversation, split multi-part questions. The raw user message is often a poor search query.
2. **Retrieve.** Run dense and sparse search with metadata and ACL filters applied *inside* the search,
   not after, *because* filtering after top-k can leave a user with zero permitted results and leaks the
   existence of forbidden ones. Fuse the ranked lists; reciprocal rank fusion is the common default.
3. **Rerank.** A cross-encoder scores (query, chunk) pairs jointly and reorders the top 50–100
   candidates. First-stage retrieval optimizes recall cheaply; the reranker buys precision for the
   handful of chunks you will actually pay to put in the prompt.
4. **Assemble context.** Select the top chunks under a token budget, drop near-duplicates, order them
   deliberately (most relevant first or last, never buried), and attach citation handles.
5. **Generate.** The prompt instructs the model to answer only from the provided passages and to cite
   them; structured output can enforce the citation format. A grounding check (does each claim map to
   a passage?) can run as a post-step.
6. **Observe.** Log the query, retrieved ids, scores, the final prompt and the answer under one trace
   id. Without this you cannot distinguish "retrieval missed" from "model ignored the passage", and
   those are fixed in different places.

### Where the boundaries go
Treat retrieval as its own service with a typed interface, roughly `search(query, user, filters, k)
-> [chunk]`, *because* it is shared by chat, agents and batch jobs, it has its own scaling profile
(CPU- and IO-bound, cacheable), and it is the component you evaluate independently with recall@k. Keep
the index derived, never authoritative: the source systems stay the source of truth and the index is a
rebuildable cache, which is what makes re-chunking and re-embedding a routine job rather than a
migration.

### Sizing intuition (illustrative)
50,000 documents at 20 chunks each is 1,000,000 chunks. A 1024-dimension fp32 vector is 4 KB, so raw
vectors alone are about 4 GB before the ANN index and the chunk text; this is why pgvector is
comfortable at this scale and a dedicated vector database earns its keep one or two orders of magnitude
later. Retrieving 50 candidates and keeping 8 chunks of roughly 300 tokens puts about 2,400 tokens of
evidence in the prompt, a fixed cost per question regardless of corpus size, which is the whole
economic argument for retrieval over stuffing.

## Example
A compact version of the online path: hybrid retrieval with reciprocal rank fusion, a cross-encoder
reranker, and token-budgeted context assembly with the ACL filter inside the search. The `store`,
`reranker`, `embed` and `log_trace` calls stand in for your vector database, BM25 index, embedding model
and tracing; the shape of the pipeline is what matters.

```python
from collections import defaultdict

def rrf(ranked_lists, k=60):
    """Reciprocal rank fusion: ids near the top of any list win."""
    score = defaultdict(float)
    for lst in ranked_lists:
        for rank, chunk_id in enumerate(lst, start=1):
            score[chunk_id] += 1.0 / (k + rank)
    return sorted(score, key=score.get, reverse=True)

def retrieve(query, user, store, reranker, k_dense=40, k_sparse=40, budget_tokens=2400):
    acl = {"tenant": user.tenant, "groups": user.groups}          # filter inside the search
    dense = store.vector_search(embed(query), k=k_dense, filters=acl)
    sparse = store.bm25_search(query, k=k_sparse, filters=acl)
    candidates = rrf([dense, sparse])[:60]
    chunks = store.fetch(candidates)                                # text + metadata
    scores = reranker.score(query, [c.text for c in chunks])       # joint (query, chunk) scoring
    ordered = [c for _, c in sorted(zip(scores, chunks), key=lambda p: -p[0])]
    context, used = [], 0
    for c in ordered:                                               # dedupe step omitted for brevity
        if used + c.tokens > budget_tokens:
            break
        context.append(c); used += c.tokens
    return context

def answer(query, user, store, reranker, llm):
    ctx = retrieve(query, user, store, reranker)
    passages = "\n\n".join(f"[{i+1}] ({c.source}) {c.text}" for i, c in enumerate(ctx))
    prompt = ("Answer using only the passages below and cite passage numbers. "
              "If the passages do not contain the answer, say so.\n\n"
              f"{passages}\n\nQuestion: {query}")
    out = llm(prompt)
    log_trace(query=query, chunk_ids=[c.id for c in ctx], answer=out)   # one trace id per request
    return out
```

## Trade-offs
- Fresh, citable, permission-aware answers versus an extra retrieval hop on every request; when the
  corpus is small and static enough to fit the window cheaply, long context wins and the index is
  overhead.
- Hybrid search plus reranking raises answer quality measurably, but adds two more models and services
  to operate; if queries are dominated by exact identifiers (SKUs, error codes), sparse retrieval alone
  may be enough.
- Small chunks improve retrieval precision while losing surrounding context; parent–document retrieval
  buys both at the cost of a second lookup and more prompt tokens.
- A derived, rebuildable index makes re-embedding routine, but consistency with sources is eventual; for
  data where staleness is a correctness bug (prices, permissions) you need synchronous invalidation or a
  live lookup tool instead.
- Running retrieval as a separate service gives independent scaling and evaluation but introduces a
  network boundary and a contract that must be versioned.

## Pitfalls
- Filtering by ACL after retrieval instead of inside it; the symptom is users getting "no results" for
  questions the system clearly has documents about, or worse, a forbidden title appearing in a citation.
- Treating parsing as solved; the symptom is retrieval that returns the right document whose chunk text
  is a mangled table, so the model answers confidently and wrongly.
- Upgrading the embedding model without re-indexing everything; the symptom is a sudden recall collapse
  that only affects documents ingested after the upgrade, or only those before it.
- Judging the system by end-to-end answer quality alone; without recall@k on a labeled query set you
  cannot tell retrieval failures from generation failures, and you end up tuning prompts to fix an index
  problem.
- Letting the index become the system of record (hand-edited chunks, data that exists nowhere else); the
  symptom is a re-chunking project that nobody dares to run.

## When to use
Reach for RAG when answers must come from content that is private, changing, large or access-controlled,
and when users need citations they can check. Do not reach for it when the knowledge is small and stable
(a long system prompt with prompt caching is simpler), when the task is precise querying of structured
data (give the model a SQL or API tool), or when the "knowledge" is really behaviour (prompt or
fine-tune). Measure retrieval and generation separately: recall@k and reranker precision on a golden
query set, plus faithfulness of answers to the retrieved passages. Day to day the architecture shows up
as two deployables with different cadences, indexing jobs and a query service, sharing one contract. In
interviews, the expected depth is to draw both pipelines, place the ACL filter and the reranker
correctly, and explain why the index is derived rather than authoritative.

## Role lens
- **Engineer:** Builds the chunk-embed-index jobs and the query service, wires hybrid search and
  reranking, and owns the retrieval eval harness that catches regressions before users do.
- **Architect:** Decides the service boundary, the vector store (pgvector versus dedicated), the freshness
  and consistency model, and how tenancy and ACLs are enforced inside search.
- **FDE:** Maps a customer's document sources, permissions and update frequency onto the pipeline, and
  explains why parsing quality and ACL propagation dominate the project timeline.

## Check yourself
**Q:** A customer reports that the assistant answers policy questions correctly for most employees but
tells contractors "I don't have information on that", even for public policies. Where in the RAG
architecture would you look first, and why?
**A:** The pattern, correct for one group and empty for another on the same public content, points at
access control interacting with retrieval, not at the model. The most likely cause is post-filtering:
the search returns a global top-k, then ACL filtering removes everything contractors cannot see, and
because employees' private documents outrank the public policy, contractors are left with nothing. The
fix is to push the ACL predicate into the search query so top-k is computed over permitted documents
only. I would verify by logging retrieved ids before and after filtering for a contractor query, and
check that public policies carry the correct ACL metadata at ingestion, since a mis-tagged document in
the offline pipeline would produce the same symptom.

## Quiz
### Q: Why is the index treated as derived data rather than the system of record?
- [ ] Vector databases cannot store the original text
- [x] It keeps re-chunking and re-embedding a safe, repeatable rebuild instead of a data migration
- [ ] Regulations prohibit storing documents in more than one place
- [ ] Derived indexes are faster to query than authoritative ones
> If sources remain authoritative, the index can be dropped and rebuilt whenever chunking or the
> embedding model changes. Making it authoritative turns every retrieval improvement into a risky
> migration.

### Q: Where should per-user access control be applied in the query path?
- [ ] After generation, by redacting the answer
- [ ] After top-k retrieval, by removing forbidden chunks
- [x] Inside the retrieval query, so top-k is computed over permitted documents only
- [ ] In the embedding model, by training it on permitted documents
> Filtering after top-k can leave a user with zero permitted results and still reveals what was ranked.
> Pushing the predicate into search keeps recall for permitted documents and leaks nothing.

### Q: What does a reranker add that first-stage hybrid retrieval does not?
- [ ] Higher recall across the whole corpus
- [x] Joint scoring of each (query, chunk) pair, buying precision for the few chunks that enter the prompt
- [ ] Access-control filtering
- [ ] Freshness, by re-fetching documents from their sources
> First-stage retrieval scores query and chunk independently for speed and recall. A cross-encoder
> reads them together and is more accurate, but is too slow to run over the whole corpus, so it is
> applied only to the shortlist.
