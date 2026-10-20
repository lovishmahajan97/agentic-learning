---
id: embedding-pipelines
module: rag-retrieval
name: Embedding pipelines
kind: pattern
difficulty: core
est_minutes: 14
related:
  - rag-retrieval/chunking-strategies
  - rag-retrieval/embedding-models
  - rag-retrieval/vector-databases
  - rag-retrieval/freshness-document-deletion-index-consistency
  - rag-retrieval/document-parsing
  - data-architecture/ingestion-pipelines
  - data-architecture/embedding-versioning-re-indexing-strategy
  - fastapi-for-ai/job-queues-and-durable-workers
sources:
  - Sculley et al., 2015 — Hidden Technical Debt in Machine Learning Systems
  - Official docs: LlamaIndex ingestion pipeline
  - Official docs: LangChain indexing API
---

## TL;DR
An embedding pipeline is the job that turns source documents into indexed vectors and keeps them that
way: parse, chunk, enrich, embed, upsert, and later update and delete. It exists because the "loop over
files and call the API" script fails the moment a document changes, a batch half-fails, or the model is
upgraded. The design goals are idempotency (same input, same chunk id, no duplicates), incremental
updates, and strict parity between how text is embedded at index time and at query time.

## Why it exists
The demo version is ten lines: read every file, split it, embed each piece, insert. It works once. Three
things break it. The first is change: documents are edited, re-uploaded and deleted, and re-running the
script either duplicates every chunk or leaves ghosts of removed content that keep showing up in
answers. The second is scale and failure: two million chunks through a rate-limited API takes hours, a
single timeout halfway through leaves the index half-built with no record of which half, and the naive
fix, "run it again", doubles the cost and the duplicates. The third is drift: the query service must
embed text exactly the way the indexer did, with the same model version, the same prefix, the same
normalization and the same text cleaning, or every search compares vectors from two different spaces.
This is the retrieval version of training/serving skew, and it is just as invisible: no error is raised,
recall simply decays.

A pipeline makes these properties explicit. Every chunk gets a deterministic identity, every run knows
what already exists, the embedding call lives in one place shared with the query path, and failures are
retried per batch instead of per corpus. The cost of not having this shows up as the most common RAG
incident: "search got worse and nobody changed anything".

## How it works
### The stages
1. **Ingest.** Pull documents from their sources (object storage, a CMS, a wiki, a database) and record
   `doc_id`, source URI, last-modified time and access-control metadata. Detect change by content hash
   or by change-data-capture events so unchanged documents cost nothing.
2. **Parse and normalize.** Convert each document to text plus structure (headings, tables, page numbers).
   This step must be deterministic: the same bytes must always produce the same text, because the chunk
   ids below depend on it.
3. **Chunk.** Apply the chunking strategy and attach `chunk_index`, heading path and character offsets.
4. **Enrich.** Add the metadata that filters will need later (tenant, ACL groups, document date, section
   title) and any contextual prefix the retrieval design calls for.
5. **Embed.** Batch the chunks, apply the model's prefix, call the model with a concurrency limit, retry
   on rate limits and transient errors with backoff, normalize, and record the model name and version.
6. **Upsert.** Write vector, payload and text (or a pointer to the text) under the deterministic id.
7. **Reconcile.** Delete the chunks that belonged to the previous version of the document, then verify
   counts match what the chunker produced.
8. **Observe.** Emit documents processed, chunks embedded, tokens spent, failures, and the lag between a
   source change and its visibility in search.

### Idempotency through content-addressed ids
The central trick is `chunk_id = hash(model_version, doc_id, chunk_index, chunk_text)`. Re-running the
pipeline on an unchanged document finds every id already present and embeds nothing. An edited paragraph
changes the text of one or two chunks, so only those get new ids and new embeddings; the old ids become
stale and are deleted. Because the model version is part of the hash, a model upgrade naturally produces
a complete new set of ids, which makes the migration a normal run rather than a special case. LangChain's
indexing API and LlamaIndex's ingestion pipeline implement this same idea with a record manager or
docstore that maps document ids to content hashes.

### Write new before deleting old
Upsert the new chunks first, then delete the stale ones. If the process dies in between, the index holds
both versions for a while, which is a mild duplicate problem; the reverse order would leave a gap where
the document is unsearchable. For stricter atomicity, index into a new collection and switch an alias.

### Model versioning and re-indexing
Two strategies. Blue/green: embed the whole corpus into a second collection with the new model, evaluate
it side by side, flip the query service, drop the old one; it doubles storage during the migration but is
clean and reversible. In place: store `model` on every record and filter on it at query time while the
backfill runs; cheaper but easy to get wrong. Estimate the backfill before choosing: chunks × average
tokens gives total tokens, and as an illustrative order of magnitude a million 300-token chunks is 300
million tokens, which at typical hosted embedding prices costs tens of dollars; the real constraint is
usually the API rate limit, which turns that into hours, or GPU throughput for a self-hosted model.

### Batch versus streaming
A nightly batch is enough for slowly changing corpora. When users notice staleness, run the same
per-document function from a queue consumer triggered by change events. Keeping one shared function for
both modes is what keeps the two paths identical.

### Query-time parity
Put the embedding call, prefix logic and normalization in one module imported by both the indexer and the
query service. Add a "golden vector" test: embed a fixed sentence and compare against a stored vector
with a tight tolerance, so a silent model change behind a hosted endpoint or a dependency bump fails CI
instead of degrading production.

## Example
An idempotent per-document indexing step. The store is any vector database with upsert, delete and a
way to list ids by document; `embed_fn` is the shared function the query service also imports. Only
changed chunks are embedded, stale chunks are removed after the new ones are live, and the model version
is part of every id.

```python
import hashlib, time
from itertools import islice

MODEL = "text-embedding-3-small@2024-01"      # pinned; part of every chunk's identity
class RateLimited(Exception): ...             # raised by embed_fn on HTTP 429

def chunk_id(doc_id, idx, text):
    return hashlib.sha256(f"{MODEL}\x00{doc_id}\x00{idx}\x00{text}".encode()).hexdigest()[:32]

def batched(items, n):
    it = iter(items)
    while batch := list(islice(it, n)):
        yield batch

def embed_with_retry(embed_fn, texts, tries=5):
    for attempt in range(tries):
        try:
            return embed_fn(texts)                # returns unit-norm vectors, prefix already applied
        except RateLimited:
            time.sleep(min(2 ** attempt, 30))     # exponential backoff, capped
    raise RuntimeError("embedding failed after retries")

def index_document(doc_id, chunks, store, embed_fn, batch_size=64):
    wanted = {chunk_id(doc_id, i, c["text"]): (i, c) for i, c in enumerate(chunks)}
    existing = set(store.ids_for_doc(doc_id))
    todo = [(cid, i, c) for cid, (i, c) in wanted.items() if cid not in existing]
    for batch in batched(todo, batch_size):
        vecs = embed_with_retry(embed_fn, [c["text"] for _, _, c in batch])
        store.upsert([{"id": cid, "vector": v, "doc_id": doc_id, "chunk_index": i,
                       "model": MODEL, "text": c["text"], **c.get("meta", {})}
                      for (cid, i, c), v in zip(batch, vecs)])
    stale = existing - wanted.keys()
    if stale:
        store.delete(list(stale))                 # only after the replacements are live
    return {"embedded": len(todo), "deleted": len(stale), "unchanged": len(existing & wanted.keys())}
```

## Trade-offs
- Content-hashed ids make re-runs free and safe, but any change to the chunker configuration, parser or
  model version re-embeds the entire corpus by design; that is what you want for a model upgrade and a
  surprise when a whitespace change in the parser triggers it.
- Larger batches mean fewer calls and higher throughput, but one bad input fails and retries more work,
  and per-request token limits cap the batch size; smaller batches isolate failures at some throughput cost.
- Streaming per-document updates deliver minutes-level freshness at the cost of queues, ordering and
  deduplication logic; a nightly batch is far simpler and wins until users notice stale answers.
- Storing chunk text inside the vector store makes retrieval a single hop but duplicates data and
  inflates memory; a pointer to a document store saves memory and adds a lookup per result.
- Blue/green re-indexing is clean and allows side-by-side evaluation but doubles storage during the
  migration; in-place migration is cheaper and riskier.

## Pitfalls
- Non-deterministic chunking, for example a splitter whose boundaries depend on library version or on
  randomness; symptom: every run re-embeds everything and the index grows with no new documents.
- The query service embeds with a different model, version or prefix than the indexer; symptom: recall
  collapses after a deploy that "only touched the API".
- Deletions never propagated to the index; symptom: answers cite documents that were removed weeks ago.
- No model version stored on the record; symptom: after an upgrade nobody can tell which chunks still
  need re-embedding.
- A poison-pill input, such as one chunk over the model's token limit, failing the whole batch on every
  retry; symptom: the pipeline stalls at the same document forever. Validate token counts before the call
  and route failures to a dead-letter queue.
- Unbounded worker concurrency against a rate-limited API; symptom: adding workers lowers throughput
  because most calls return 429 and retry.
- Embedding boilerplate (navigation, headers, legal footers) repeated across thousands of pages; symptom:
  top-k results are near-duplicate chunks of the same boilerplate.

## When to use
Build a real pipeline as soon as the corpus will be updated more than once, which is almost always. Keep
the single-script version only for a throwaway prototype on a static dump. Decide batch versus streaming
from how fast users need to see changes, and blue/green versus in-place from how often you expect to
change models. Measure index lag (source change to searchable), re-run cost on an unchanged corpus
(should be near zero), duplicate rate, and the golden-vector test in CI. In day-to-day work this is
where most RAG engineering time goes, and in interviews the expected answer to "how do you keep the
index in sync with the documents" is content-hashed ids, incremental upserts and versioned re-indexing.

## Role lens
- **Engineer:** Implements the per-document idempotent step, the batching and backoff around the model
  call, and the golden-vector test that catches silent model changes.
- **Architect:** Decides the freshness SLA, the batch-versus-streaming topology, the re-indexing strategy
  for model upgrades, and where the source of truth for documents lives.
- **FDE:** Wires the pipeline to the customer's actual sources and change events, and budgets the backfill
  in tokens, hours and dollars before the first full run.

## Check yourself
**Q:** A hosted embedding vendor silently changes the model behind an endpoint name you have pinned. How
would your pipeline detect this, and what would you do once it did?
**A:** Detection comes from the golden-vector test: embedding a fixed sentence in CI and on a schedule and
comparing against the stored vector within a tight tolerance flags any change in the model's output,
whether from the vendor, a dependency bump or a prefix change. Once detected, the index built with the
old behaviour and new queries no longer share a space, so recall is at risk. Treat it as a model upgrade:
bump the version string that is part of every chunk id, run a blue/green re-index into a new collection,
compare recall@k on labelled queries between old and new, and flip the query service only when the new
collection passes. Pin to a dated model identifier where the vendor offers one, and keep the old
collection until the comparison is done.

## Quiz
### Q: Why include the embedding model version in the hash that forms each chunk id?
- [ ] It makes the ids shorter and faster to look up
- [x] A model change then produces entirely new ids, so re-embedding becomes an ordinary run and old and new vectors never share one id space
- [ ] Vector databases require the model name in the primary key
- [ ] It lets the query service skip the embedding step
> The id is how the pipeline decides what already exists. If the version is part of it, upgrading the
> model automatically invalidates every chunk, which is exactly the behaviour you want; without it, a
> re-run after an upgrade sees every chunk as present and silently keeps the stale vectors.

### Q: During reconciliation, why upsert the new chunks before deleting the stale ones?
- [ ] Deletes are slower than upserts in most vector databases
- [ ] Upserts can fail if the old ids still exist
- [x] A crash between the two steps then leaves temporary duplicates instead of a window where the document cannot be found
- [ ] The vector database needs the old vectors to build the index for the new ones
> Both orders are correct when nothing fails; the difference is the failure mode. Duplicates are a mild,
> self-healing problem on the next run, while a gap means users get no results from that document until
> someone notices.

### Q: An indexing job's throughput drops when you double the number of workers calling a hosted embedding API. What is the most likely cause?
- [ ] The vector database cannot accept concurrent upserts
- [x] The workers exceed the API rate limit, so most calls return rate-limit errors and spend their time in retries
- [ ] Larger batches are being sent automatically
- [ ] The chunker is producing more chunks per document
> Hosted embedding APIs enforce requests-per-minute and tokens-per-minute limits. Past that limit, extra
> workers only generate 429 responses and backoff sleeps; throughput is bounded by the quota, so the
> right fix is a shared concurrency limiter or a higher quota, not more workers.
