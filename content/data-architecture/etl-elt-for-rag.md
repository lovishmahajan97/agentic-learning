---
id: etl-elt-for-rag
module: data-architecture
name: ETL / ELT for RAG
kind: pattern
difficulty: core
est_minutes: 15
related:
  - data-architecture/ingestion-pipelines
  - data-architecture/document-processing-pipelines
  - data-architecture/embedding-versioning-re-indexing-strategy
  - data-architecture/lakehouse
  - rag-retrieval/embedding-pipelines
  - rag-retrieval/chunking-strategies
  - rag-retrieval/contextual-retrieval
  - system-design/rag-architecture
sources:
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Official docs: dbt
  - Anthropic, 2024 — Introducing Contextual Retrieval
---

## TL;DR
ETL transforms data before loading it into the target; ELT loads raw data first and transforms inside or
beside the target. For RAG the right shape is ELT into a lake or landing store followed by a staged
transformation DAG (parse → clean → chunk → enrich → embed → index) whose stages are materialized,
content-addressed tables, because the transformations change far more often than the sources do and
re-transforming from raw is the operation you will run most. The vector index is a derived, rebuildable
serving structure, never the system of record.

## Why it exists
The terms come from data warehousing. ETL was the norm when warehouse storage and compute were expensive:
you cleaned and shaped data on a separate server and loaded only the final tables. ELT took over when
cloud warehouses and lakes made storage cheap and put elastic compute next to the data: load everything
raw, then transform with SQL (the dbt workflow) and keep every intermediate table. The reason ELT won is
that transformations are where requirements change, and keeping raw data means a changed transformation
is a re-run, not a re-extraction.

RAG pipelines rediscover this the hard way. The first implementation is one function: fetch a document,
split it into 512-token chunks, embed, upsert. Then retrieval quality disappoints and the team wants to
try semantic chunking, add section titles to each chunk, switch embedding models, or prepend an
LLM-generated summary to each chunk (contextual retrieval). Each of these means re-running everything
from the source, because nothing in between was kept. There is no way to A/B two chunkers against the
same parsed text, no intermediate artifact to inspect when a retrieval failure is traced to a garbled
chunk, and no cache of embeddings so the second attempt costs as much as the first. Worse, a vector
database is a poor transformation engine: it cannot join, deduplicate or re-chunk, so transforming
"inside the target" in the warehouse sense is not even an option. The resolution is to treat the
chunk-and-embedding tables as the real data product and the index as a projection of them.

## How it works
### The stage tables
Model the pipeline as a chain of derived tables, each keyed so that it can be recomputed incrementally:

1. `documents`: raw bytes (or a pointer) plus `doc_id`, `doc_version` (content hash) and source metadata
   including ACLs. Produced by ingestion; immutable per version.
2. `parsed`: text plus structure (headings, tables, page numbers) per `(doc_id, doc_version, parser_version)`.
   Parsing is the most expensive and most bug-prone step, so its output is stored, not recomputed.
3. `chunks`: `chunk_id`, `doc_id`, `doc_version`, `chunker_version`, `position`, `text`, `section_path`,
   `metadata`. Make `chunk_id = hash(doc_id, doc_version, chunker_version, position)` so a new chunker
   produces new ids and old chunks can be found and deleted as orphans rather than overwritten in place.
4. `enriched` (optional): the chunk text with additions that improve retrieval, such as the document
   title and section path prepended, extracted entities, or a short LLM-written context sentence placed
   before the chunk (contextual retrieval). Keep this separate from `chunks` because enrichment calls an
   LLM and is the step most likely to be re-tuned.
5. `embeddings`: `(chunk_id, embedding_model, vector)`. Cache by `(embedding_model, text_hash)` so an
   unchanged chunk is never embedded twice, and so two embedding models can coexist during a migration.
6. The index: an upsert of `embeddings` joined to `chunks` metadata, keyed by `chunk_id`, followed by a
   delete of ids present in the index but absent from the current `chunks` for that document.

Each table is a deterministic function of the previous one plus a version parameter. That is what makes
the pipeline incremental: when `chunker_version` changes, only `chunks`, `enriched`, `embeddings` and the
index are recomputed, and only for documents (all of them, in this case, but from `parsed`, not from the
source); when one document changes, only its rows flow through every stage.

### Why materialize instead of streaming through
Storing intermediates costs disk and adds a write per stage. It buys four things. Debuggability: a bad
answer traces to a chunk, the chunk to its parsed text, the parsed text to the raw bytes, each inspectable.
Experimentation: two chunkers or two embedding models run against the same `parsed` table and are
compared with the same retrieval eval set. Cost control: embedding and LLM enrichment, the expensive
calls, are cached by content hash and never repeated for unchanged text. Late binding: the embedding
model is a parameter of the last stage, so upgrading it is a re-run of one stage, not of the pipeline.

### Where ETL still applies
Some transformations must happen before anything is stored, which is ETL in the strict sense: redacting
PII that may not be persisted, dropping documents whose classification forbids them in this system, and
enforcing tenant boundaries at the landing store. The rule is that compliance-driven transformations
belong before the load and quality-driven transformations belong after it.

### Quality gates between stages
Because every stage always produces *something*, failures are silent unless measured. Standard gates:
characters per page from the parser (a scanned PDF parsed as text yields near zero), empty or
near-duplicate chunk ratio, embedding failure rate, and orphan count after index reconciliation. A gate
that trips stops promotion of that stage's output, which is cheaper than finding out from users.

### Orchestration and incrementality
A DAG scheduler (Dagster, Airflow, Prefect, or dbt-style SQL models over a lakehouse) runs stages in
dependency order on partitions of changed documents. Writes to the index are idempotent upserts by
`chunk_id`, so a retried stage is harmless; the version columns are what make "re-run everything from
`parsed` with chunker v3" a one-line change rather than a project.

## Example
A content-addressed transform stage: chunk ids include the chunker version, embeddings are cached by
`(model, text_hash)`, and index reconciliation deletes orphaned chunks of re-chunked documents. The
stores are stand-ins for lakehouse tables and a vector database.

```python
import hashlib, json

CHUNKER_VERSION, EMBED_MODEL = "v3-semantic", "text-embedding-3-small"

def h(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:24]

def build_chunks(doc_id, doc_version, parsed_sections, chunker):
    rows = []
    for pos, (section_path, text) in enumerate(chunker(parsed_sections)):
        rows.append({"chunk_id": h(doc_id, doc_version, CHUNKER_VERSION, pos),
                     "doc_id": doc_id, "doc_version": doc_version,
                     "chunker_version": CHUNKER_VERSION, "position": pos,
                     "text": f"{section_path}\n{text}",            # cheap enrichment: section path
                     "text_hash": h(section_path, text)})
    return rows

def embed_stage(chunks, cache, embed_fn):
    todo = [c for c in chunks if (EMBED_MODEL, c["text_hash"]) not in cache]
    for c, vec in zip(todo, embed_fn([c["text"] for c in todo])):   # batched, rate-limited call
        cache[(EMBED_MODEL, c["text_hash"])] = vec
    return [(c["chunk_id"], cache[(EMBED_MODEL, c["text_hash"])], c) for c in chunks]

def reconcile_index(index, doc_id, rows):
    live = {cid for cid, _, _ in rows}
    for cid, vec, meta in rows:
        index.upsert(cid, vec, {k: meta[k] for k in ("doc_id", "doc_version", "position")})
    stale = index.ids_for_doc(doc_id) - live                       # chunks from an older chunker version
    index.delete(stale)
    return {"upserted": len(live), "deleted": len(stale)}
```

Switching chunkers is now `CHUNKER_VERSION = "v4"` plus a re-run from the parsed table; unchanged text
still hits the embedding cache, and `reconcile_index` removes the v3 chunks per document as v4 lands.

## Trade-offs
- Materializing every stage costs storage and one extra write per stage, but it flips to a clear win as
  soon as the team changes a chunker or embedding model for the second time.
- ELT keeps raw data available for any future transformation, but compliance rules that forbid storing
  certain content force those specific transformations back before the load.
- Content-addressed chunk ids make orphans detectable and re-runs idempotent, yet any change to the
  chunker version re-embeds the whole corpus; a cache keyed by text hash limits that to chunks whose text
  actually changed.
- LLM-based enrichment (summaries, contextual prefixes) can raise retrieval quality noticeably, but it
  adds an LLM call per chunk per version, which dominates pipeline cost at corpus scale unless cached and
  applied selectively.

## Pitfalls
- Deriving chunk ids from `(doc_id, position)` only; the symptom after a chunker change is old and new
  chunks of the same document both ranking in the top-k, often with overlapping text.
- Mixing vectors from two embedding models in one index; the symptom is retrieval quality dropping right
  after a model "upgrade", because similarity scores from different models are not comparable.
- Embedding before near-duplicate removal; the symptom is the top-k filled with copies of the same
  boilerplate paragraph and the useful passage pushed below the cutoff.
- Transforming inside the vector database's ingest hooks; the symptom is a re-index that takes days and a
  pipeline that cannot be moved to another vendor without a rewrite.
- No quality gate after parsing; the symptom is a retrieval failure traced, weeks later, to thousands of
  chunks containing nothing but page headers or OCR noise.
- Treating the index as the system of record; the symptom is an outage or a vendor migration that
  requires re-crawling every source because the chunk tables never existed.

## When to use
Use staged ELT for any RAG corpus you expect to re-chunk, re-embed or audit, which is every corpus that
lives longer than a prototype. The signal that you need it is a question like "what would it cost to try
a different chunk size?" being answered in weeks rather than hours. Measure embedding cache hit rate,
re-run time per stage, orphan count after reconciliation and parse yield; these show whether the pipeline
is incremental in practice. Skip the full apparatus for a static corpus of a few thousand files that will
be rebuilt from scratch when anything changes, but still keep the raw copy. In interviews, the expected
answer to "how would you upgrade the embedding model on a live system" is a re-run of the embedding stage
into a second index version from stored chunks, followed by an evaluated cutover, and that answer is only
possible if the chunk table exists.

## Role lens
- **Engineer:** Implements stage functions with version parameters and content hashes, caches embedding
  calls by text hash, and writes the reconciliation step that deletes orphans after re-chunking.
- **Architect:** Defines the chunk and embedding tables as the data product, sets which transformations
  must run before storage for compliance, and chooses the orchestrator and lakehouse that host the stages.
- **FDE:** Shows the customer the parsed and chunked intermediates when a retrieval answer is wrong, so
  the fix is traced to a stage rather than blamed on "the model", and estimates re-index cost from them.

## Check yourself
**Q:** Your team wants to move from fixed 512-token chunks to a semantic chunker and, a month later, to a
new embedding model, on a corpus of two million documents that is live for customers. What does the
pipeline need so each change is a bounded, reversible job rather than a quarter-long project?
**A:** It needs stored, versioned intermediates. The parsed table lets the semantic chunker run without
re-crawling or re-parsing two million documents. Chunk ids that include the chunker version let the new
chunks land beside the old ones and let reconciliation delete the old per document, so the cutover is
gradual and reversible. An embedding cache keyed by model and text hash means only genuinely new chunk
text is embedded. For the model change, the embedding stage is re-run with a new model parameter into a
second index version, evaluated against the same retrieval test set, and traffic is switched when it wins;
the old index remains for rollback. Quality gates on parse yield and orphan count catch regressions before
users do, and the vector index is treated as a projection that can always be rebuilt from the tables.

## Quiz
### Q: In a RAG pipeline, why is the vector index treated as a derived structure rather than the system of record?
- [ ] Vector databases cannot store document metadata reliably
- [x] Chunking, enrichment and embedding change often, and keeping the chunk tables lets any of them be re-run without re-extracting from sources
- [ ] Vector indexes are always rebuilt nightly regardless of changes
- [ ] Regulations require a relational copy of every embedded chunk
> The transformations are the volatile part of a RAG system. If the index is the only copy, every
> chunker or model change becomes a re-crawl; if chunk and embedding tables exist, the index is a cheap
> projection that can be rebuilt, versioned or moved to another vendor.

### Q: Which transformation genuinely belongs before the load (ETL) rather than after it (ELT) in a RAG pipeline?
- [ ] Splitting documents into chunks
- [ ] Prepending section titles to chunk text
- [x] Redacting personal data that policy forbids persisting anywhere in the system
- [ ] Computing embeddings with the current model
> ELT is preferable for quality-driven steps because they are re-tuned often. Compliance-driven steps are
> different: if the data may not be stored, it must be removed before the landing write, since a raw copy
> would itself be a violation.

### Q: After switching chunkers, users see overlapping passages from the same document twice in the top-k. What is the likely cause?
- [ ] The embedding model was changed at the same time
- [ ] The reranker was not retrained on the new chunk sizes
- [x] Chunk ids did not encode the chunker version, so new chunks were added without the old ones being identified and deleted
- [ ] The new chunker produces chunks that are too small for the index
> When ids depend only on document and position, a new chunker writes new rows while the old rows stay
> live or are partially overwritten. Encoding the chunker version in the id makes old chunks discoverable
> as orphans so reconciliation can remove them.
