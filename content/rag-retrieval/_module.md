---
id: rag-retrieval
title: RAG & retrieval
---

## Overview
This module is about getting the right evidence in front of a model, and only that evidence, so its
answers come from your documents instead of its memory. It follows one document through the whole
system. Parsing turns PDFs, scans and tables into clean ordered text. Chunking, overlap and granularity
decide what counts as a retrievable unit. Embedding models, similarity metrics, normalization, ANN
indexes and vector databases make dense search fast. An idempotent embedding pipeline keeps the index
in step with its sources. Inverted indexes and BM25 provide the lexical half, and dense-versus-sparse
analysis, hybrid fusion and cross-encoder reranking combine the two arms into a staged pipeline tuned on
recall and precision. Metadata and ACL filters make certain results impossible to return. Parent-document
and contextual retrieval repair chunks that lost their context. Query rewriting, HyDE, decomposition and
multi-hop loops repair queries that were never going to match. On the generation side the module covers
lost-in-the-middle position effects, grounding with citations, and measuring whether each citation
actually supports its claim. It closes with production concerns: freshness, deletion and index
consistency, staged failure diagnosis, the long-context-versus-RAG decision, knowledge graphs, GraphRAG
and agentic RAG. Afterwards you can design, build, measure and debug a production retrieval system,
and defend each stage with a number. It matters most for AI Engineers, who spend most of their RAG
time in these stages, and for FDEs, who have to make retrieval work on a customer's messy, permissioned
corpus.

## Why it matters
- **Engineer:** You build the ingestion pipeline with content-hashed chunk ids and query-time parity, run BM25 and dense arms fused with RRF in front of a reranker, and, when an answer is wrong, trace the gold chunk stage by stage to find where it was lost instead of swapping the embedding model on a hunch.
- **Architect:** You choose the vector store from vector count, dimension, filter selectivity and operational capacity, place ACL enforcement inside the search rather than in the prompt, set freshness and deletion SLOs per content class, and decide from arithmetic and evals when long context, a knowledge graph, GraphRAG or an agent loop earns its cost over plain hybrid retrieval.
- **FDE:** You open the customer's parsed text before tuning anything, map their SharePoint or Confluence permissions into per-chunk principals, build a labelled query set from their own users' questions, and show recall@k and citation correctness on their documents so sign-off rests on measurements rather than a polished demo.

## Mental model
Picture retrieval as a funnel with a leak test at every stage. The whole corpus goes in at the top. The
parser decides what text exists. The chunker decides what a hit means. The index (dense vectors and an
inverted index side by side) decides what can be found. Filters remove what this user must never see.
A wide first stage keeps a few dozen candidates for recall, a reranker keeps a few for precision, and
the prompt builder decides where they sit and how they are cited. Each stage can only lose evidence; no
later stage can recover what an earlier one dropped. The techniques in this module either widen a
stage that loses too much (hybrid, rewriting, HyDE, decomposition, multi-hop, contextual chunks) or
narrow one that lets too much through (reranking, filtering, a smaller k). So you diagnose by finding
the first stage where the gold evidence disappears, fix that stage, and measure it with its own metric.
Around the funnel runs a sync loop that keeps the index a faithful, current, permission-correct copy of
its sources.

## Suggested order
1. document-parsing — every later stage sees only what the parser produced, so learn to inspect parsed text first.
2. chunking-strategies — defines the retrievable unit and why whole documents embed as blurry averages.
3. chunk-overlap — a cheap patch for boundary damage, best understood right after the splitters it patches.
4. retrieval-granularity — separates the unit you match on from the unit you return, framing later small-to-big work.
5. embedding-models — explains bi-encoders, prefixes and versioning, the basis of dense retrieval.
6. similarity-search — shows how dot product, cosine and Euclidean relate and why the trained metric must be used.
7. embedding-dimension-and-normalization — adds the storage arithmetic, normalization, truncation and quantization behind index sizing.
8. ann-indexes — explains HNSW and IVF-PQ and the recall–latency knobs once exact search stops fitting.
9. vector-databases — maps pgvector, Qdrant, Weaviate, Milvus, Pinecone and Chroma onto the operations you now understand.
10. embedding-pipelines — turns one-off indexing into an idempotent, incremental, versioned job.
11. inverted-indexes — introduces posting lists, the data structure underneath lexical search.
12. bm25-internals — explains IDF, saturation and length normalization, the lexical baseline every retriever must beat.
13. dense-vs-sparse-retrieval — compares the two families' structural failure modes to motivate combining them.
14. hybrid-search — fuses the BM25 and dense arms by rank and verifies the gain with recall@k.
15. recall-vs-precision-trade-off — frames retrieval as a wide recall stage followed by a narrow precision stage.
16. reranking — supplies the cross-encoder precision stage that the trade-off calls for.
17. metadata-filtering — places hard predicates inside the search and explains why post-filtering returns too few results.
18. acl-aware-retrieval — applies filtering to per-user permissions, the security requirement of every enterprise deployment.
19. parent-document-retrieval — implements small-to-big retrieval so matching and answering use different units.
20. contextual-retrieval — repairs chunks that lost their subject by enriching them at ingestion.
21. query-rewriting — repairs the query side: follow-ups, vocabulary mismatch and pasted noise.
22. hyde — bridges the question-versus-passage gap in embedding space with a hypothetical answer.
23. query-decomposition — splits compound questions so each facet gets its own retrieval.
24. multi-hop-retrieval — chains dependent sub-questions through bridging facts under a hop limit.
25. lost-in-the-middle — shows why more context can lower accuracy and how to order evidence in the prompt.
26. citations-grounding — constrains the answer to the sources with stable ids, per-claim citations and abstention.
27. citation-correctness — measures whether each citation actually supports its claim, the check on the previous lesson.
28. freshness-document-deletion-index-consistency — keeps the index current, deletion-safe and version-consistent in production.
29. retrieval-failure-diagnosis — ties the funnel together as a staged trace that finds where evidence was lost.
30. long-context-vs-rag — decides with cost and accuracy arithmetic when retrieval is needed at all.
31. knowledge-graphs — adds typed entities and relationships for questions that are joins and aggregations.
32. graphrag — builds community summaries over an extracted graph for corpus-wide and thematic questions.
33. agentic-rag — puts retrieval tools in a bounded loop, earned only when the eval set shows the simpler pipelines fall short.

## Capstone
### Brief
Build a permission-aware question-answering assistant over a realistic internal corpus of 500 to 2,000
documents: a mix of born-digital PDFs, at least a few scanned pages, table-heavy spreadsheets or reports,
and wiki pages, each assigned to one of three or four access groups. Start by parsing a sample of every
document type and fixing what breaks. Then build an idempotent ingestion pipeline with structure-aware
chunking, content-hashed chunk ids, contextual prefixes, and per-chunk metadata holding source, version,
date and allowed principals. Index each chunk in both a vector store and a BM25 index, fuse the two arms
with reciprocal rank fusion, rerank the top 30 to 50 with a cross-encoder, and return parent sections to
a generator that must cite a source id for every claim or abstain. Add a query rewriter for
conversational follow-ups and decomposition for compare-style questions. Build a labelled golden set of
at least 100 questions, sliced into lookup, identifier, paraphrase, compound, multi-hop and unanswerable
types. Measure each stage, then make the pipeline survive change: edit, revoke and delete documents and
prove that retrieval reflects each change within a stated lag. Finish with a written analysis of when
long context or GraphRAG would beat what you built on this corpus.

### Deliverables
- A parsing report with sampled pages per document type, the observed failures, the fixes applied, and OCR character error rate and table cell accuracy on a hand-checked subset.
- The ingestion pipeline, runnable from one command, whose second run on an unchanged corpus re-embeds nothing, plus a CI golden-vector test proving the index-time and query-time embed paths match.
- The retrieval service with BM25, dense, RRF fusion, reranking, metadata and ACL filters, parent expansion, query rewriting and decomposition, each stage switchable by configuration.
- The labelled golden set and an evaluation report giving recall@50, recall@5 after reranking, MRR, citation recall and precision, and abstention rate, per slice and per ablation (BM25 only, dense only, hybrid, plus reranker, plus contextual prefixes).
- A freshness and deletion test suite that edits, revokes and deletes documents and reports staleness lag, deleted-document leak probes and orphan counts from a reconciliation job.
- A failure-diagnosis sheet of at least 30 wrong answers, each labelled with the first stage where the gold evidence was lost, with counts per stage and the fix chosen for the largest bucket.
- A two-page design memo on long context versus RAG and on whether GraphRAG or an agent loop is warranted for this corpus, with cost-per-query and accuracy arithmetic.

### Rubric
- A red-team query run as a low-privilege test user never returns a chunk from a restricted document, and the same holds within the stated lag after a permission is revoked at the source.
- The ablation table shows the measured contribution of each stage, and any stage that does not improve its target metric on the golden set is either removed or justified in writing.
- Recall at candidate depth, recall after reranking, and gold-in-context rate are reported separately, so a generation failure is never misread as a retrieval failure.
- Every cited source id in a sampled set of answers resolves to a passage that supports its claim, as checked by an entailment model or calibrated judge whose agreement with human labels is reported.
- Deleting a source document removes every derived chunk from both indexes, which is verified by a probe query and a reconciliation run reporting zero orphans.
- The diagnosis sheet's largest failure bucket is the one the final change targeted, and the before-and-after numbers for that slice are shown.
