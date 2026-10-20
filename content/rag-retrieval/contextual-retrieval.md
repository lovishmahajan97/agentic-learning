---
id: contextual-retrieval
module: rag-retrieval
name: Contextual retrieval
kind: pattern
difficulty: core
est_minutes: 14
related:
  - rag-retrieval/chunking-strategies
  - rag-retrieval/parent-document-retrieval
  - rag-retrieval/hybrid-search
  - rag-retrieval/bm25-internals
  - rag-retrieval/reranking
  - rag-retrieval/embedding-pipelines
  - prompting/prompt-caching
  - data-architecture/embedding-versioning-re-indexing-strategy
sources:
  - Anthropic, 2024 — Introducing Contextual Retrieval (engineering blog post)
---

## TL;DR
Chunking destroys context: "revenue grew 3% over the previous quarter" no longer says which company or
which quarter, so neither its embedding nor its BM25 terms can match a query that names them. Contextual
retrieval fixes the index rather than the query: at ingestion an LLM reads the whole document plus each
chunk and writes a short context that is prepended to the chunk before embedding and before BM25
indexing. It costs one LLM call per chunk, made affordable by prompt caching, and adds no query-time
latency.

## Why it exists
A chunk is embedded and indexed in isolation, but it was written as part of a document. The author relied
on the surrounding pages to supply the subject: "the company", "this method", "Table 3", "the previous
quarter", "it". Once the chunk is cut out, those references dangle. A query like "ACME Corp revenue
growth Q2 2023" shares no terms with a chunk that says "The company's revenue grew by 3% over the
previous quarter", so BM25 scores it near zero, and the chunk's embedding lands in a generic
"revenue growth" region that hundreds of other companies' chunks also occupy. The evidence is in the
index and still cannot be retrieved. This is a recall failure caused by the indexing unit, and it shows
up most in corpora of many similar documents: quarterly filings, per-customer contracts, per-service
runbooks.

The cheap fix, prepending the document title and heading path to every chunk, recovers a lot and should
always be tried first. It fails when the title does not carry the facts a chunk needs (the quarter, the
product variant, the entity under discussion three paragraphs up). Bigger chunks blur every embedding.
Parent-document retrieval gives the generator the context but does nothing for matching, because the
child is still what gets embedded. Document-level summaries are not specific to each chunk. What is
needed is a chunk-specific, document-grounded sentence that says what this chunk is about in terms a
query would use, and an LLM reading the whole document can write exactly that.

## How it works
### The ingestion pipeline
For each document, for each chunk:

1. Prompt an LLM with the entire document and the chunk, asking for a short context that situates the
   chunk within the document for the purpose of improving search retrieval, and nothing else. The output
   is typically a sentence or two (the post suggests on the order of 50–100 tokens).
2. Prepend the context to the chunk text: `contextualised = context + "\n" + chunk`.
3. Embed the contextualised chunk for the dense index ("contextual embeddings").
4. Index the contextualised chunk in BM25 ("contextual BM25").
5. Store the original chunk (and, separately, the context) for display and citation.

Each choice has a reason. The whole document is in the prompt because only it can supply the entity,
date and section the chunk omits. The context is kept short because the chunk's own content must still
dominate the embedding; a long context makes every chunk of a document look alike. It is prepended to
both legs because dense and sparse retrieval fail on the same missing terms, and the reported gains were
larger when both were fixed. The original chunk is kept because the generated sentence is not source
text and must not be quoted as if it were.

### The cost model, and why prompt caching makes it viable
Naively, contextualising a document with c chunks and d tokens costs about c × d input tokens, since
each call carries the full document: a 100-chunk document means the document is read 100 times. Prompt
caching changes this. The document is placed as a fixed prefix, the chunk and instruction vary, and
after the first call the prefix is served from cache at a much lower rate. The post's own estimate was
roughly one dollar per million document tokens with caching; treat the exact figure as model- and
pricing-dependent, but the order of magnitude is what makes "an LLM call per chunk" acceptable.

### What was reported
The post reported reductions in the top-20 retrieval failure rate on its evaluation set: around 35% with
contextual embeddings alone, around 49% when contextual BM25 was added, and around 67% when a reranker
was added on top. These are the authors' numbers on their corpora and embedding models; the shape of
the result (each layer helps, and they stack) is the transferable lesson, the exact percentages are not.

### The serving path is unchanged
At query time nothing new happens: hybrid search over the contextualised chunks, rank fusion, reranking,
top-n to the generator. That is the main attraction compared with query-side techniques: all the cost is
paid at ingestion, and query latency does not move.

### Variants and neighbours
- Structural context (title, heading path, metadata) prepended by rule: zero LLM cost, often enough.
- Documents longer than the model's context: pass the enclosing section or a window around the chunk
  instead of the whole document, accepting weaker context.
- Corpus-specific prompts: for code, ask for file, class and function; for contracts, the parties and
  the clause.
- Late chunking (embedding the whole document with a long-context encoder and pooling per chunk) attacks
  the same problem at the embedding level without an LLM; it is a different trade and worth comparing.
- Below a certain corpus size the post's own advice applies: if everything fits in the context window,
  put it in the prompt with caching and skip retrieval.

## Example
A contextualisation step that produces the two index inputs and keeps the original for display. `llm`
accepts a cached prefix (the document) and a per-call suffix; `embed`, `dense.add` and `bm25.add` are
stubs for your encoder and indexes.

```python
CONTEXT_INSTRUCTION = """Here is the chunk we want to situate within the whole document:
<chunk>
{chunk}
</chunk>
Give a short, succinct context that situates this chunk within the overall document for the
purpose of improving search retrieval of the chunk. Answer only with the context."""

def contextualise_document(doc_id, document, chunks):
    prefix = f"<document>\n{document}\n</document>\n"          # fixed across calls: cache it
    for i, chunk in enumerate(chunks):
        context = llm(prefix=prefix, suffix=CONTEXT_INSTRUCTION.format(chunk=chunk),
                      cache_prefix=True, max_tokens=120).strip()
        contextualised = f"{context}\n{chunk}"
        cid = f"{doc_id}#{i}"
        dense.add(cid, embed(contextualised, kind="passage"))  # contextual embeddings
        bm25.add(cid, contextualised)                          # contextual BM25
        store[cid] = {"chunk": chunk, "context": context, "doc": doc_id}

chunk = "The company's revenue grew by 3% over the previous quarter."
# Illustrative generated context: "This chunk is from ACME Corp's Q2 2023 SEC filing, in the
# section on quarterly financial performance; the previous quarter is Q1 2023."
# After prepending, a query for "ACME Q2 2023 revenue growth" matches on both the BM25 terms
# (ACME, Q2, 2023, revenue) and the embedding, which the bare chunk could never do.
```

## Trade-offs
- Retrieval quality rises with zero added query latency, but ingestion now includes an LLM call per
  chunk; for a corpus that churns daily the recurring cost can outweigh the gain.
- Because each context depends on the whole document, any edit can change every chunk's context, so
  re-indexing granularity becomes the document rather than the chunk.
- The generated context can be wrong (a misread date or entity) and then actively misleads retrieval;
  documents with clear metadata produce reliable contexts, scanned or messy ones do not.
- Compared with parent-document retrieval it fixes matching rather than generation context; the two
  compose, and using only one leaves the other failure mode in place.

## Pitfalls
- Embedding only the context or only the chunk instead of the concatenation; the symptom is no measurable
  improvement after a costly ingestion run.
- Applying contexts to the dense index but not to BM25; the symptom is that exact-term queries naming the
  entity still miss, and roughly half the reported gain is absent.
- Contexts that are too long; the symptom is that every chunk from one document looks alike and retrieval
  returns clusters of neighbours from the same document instead of the specific passage.
- Running ingestion without prompt caching; the symptom is an ingestion bill many times higher than
  expected, scaling with document length squared.
- Documents longer than the model's context being silently truncated; the symptom is contexts for late
  chunks that refer to the wrong section or entity.
- Displaying the contextualised text as a quotation; the symptom is "cited" sentences that do not exist in
  the source document.
- Shipping contexts and a reranker in the same release without a golden set; the symptom is not knowing
  which change produced the gain or the regression.

## When to use
Use it when failure analysis shows chunks that cannot be matched because they lost their subject: many
similar documents, heavy use of pronouns and references, tables and figures whose rows are meaningless
without headers. Try structural prefixes first; they are free. Skip it for corpora that fit in the
context window, for corpora that change faster than you can afford to re-contextualise, and for
collections of short self-contained records that never lost context in the first place. Measure
retrieval failure rate or recall@20 on a golden set before and after, with and without contextual BM25,
and track ingestion cost per million tokens. In interviews it is the answer to "the right chunk is in
the index but never retrieved", and the strong candidate explains both legs, the caching cost model and
the re-indexing consequence.

## Role lens
- **Engineer:** Adds the contextualisation step to the ingestion pipeline with prompt caching, indexes the
  concatenation in both legs, and keeps original text separate for citations.
- **Architect:** Owns the cost model (tokens per document, re-index triggers on edits) and decides where
  contextual retrieval sits relative to structural prefixes, parent retrieval and reranking.
- **FDE:** Identifies customer corpora with many look-alike documents (filings, contracts, runbooks) where
  this pays off, and runs the before and after retrieval measurement on their data.

## Check yourself
**Q:** A RAG system over thousands of quarterly reports cannot answer "what was ACME's Q2 2023 revenue
growth" even though the sentence exists in the index. Explain the failure and design a fix with its
costs.
**A:** The chunk says "the company's revenue grew by 3% over the previous quarter": the entity and the
quarter live elsewhere in the document, so BM25 has no matching terms and the embedding is a generic
revenue-growth vector shared with every other filing. Fix at ingestion: for each chunk, have an LLM read
the whole filing and write a one- or two-sentence context naming the company, period and section;
prepend it to the chunk; embed and BM25-index the concatenation; keep the original chunk for citations.
Use prompt caching with the document as the fixed prefix so cost is roughly linear in document tokens.
Costs: an LLM call per chunk at ingestion, document-level re-indexing on edits, and the risk of a wrong
generated context, so validate on a golden set and try title-plus-heading prefixes first as the cheap
baseline.

## Quiz
### Q: Why is the generated context prepended before BM25 indexing as well as before embedding?
- [ ] BM25 cannot index chunks shorter than a paragraph
- [x] Sparse and dense retrieval fail on the same missing terms, and the reported gain was larger when both legs were fixed
- [ ] The embedding model requires the BM25 tokens as input
- [ ] Reranking only works on BM25 candidates
> A query naming the entity and quarter needs those exact tokens present for BM25 and their meaning
> present for the embedding; fixing only one leg leaves exact-term queries failing.

### Q: What makes "one LLM call per chunk with the whole document attached" affordable?
- [ ] Using a smaller chunk size so there are fewer calls
- [ ] Sending only the chunk and asking the model to infer the document
- [x] Prompt caching the document as a fixed prefix so each call pays mainly for the chunk and the output
- [ ] Batching all chunks into a single call per document
> Without caching, cost scales with chunks × document length; with the document cached as a prefix, each
> call re-reads it at a much lower rate, which is what turns a quadratic bill into a near-linear one.

### Q: Every chunk from a given document is now retrieved together and the specific passage ranks no higher than its neighbours. What most likely went wrong?
- [ ] The reranker was trained on a different domain
- [x] The generated contexts are too long and dominate the embeddings, making the document's chunks look alike
- [ ] BM25 was not updated with the contexts
- [ ] Chunk overlap was set too high
> The context is meant to add a few disambiguating facts, not to replace the chunk's own content; when it
> dominates, all chunks of a document collapse toward one vector and intra-document discrimination is lost.
