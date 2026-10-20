---
id: freshness-document-deletion-index-consistency
module: rag-retrieval
name: Freshness, document deletion and index consistency
kind: practice
difficulty: core
est_minutes: 15
related:
  - rag-retrieval/embedding-pipelines
  - rag-retrieval/metadata-filtering
  - rag-retrieval/acl-aware-retrieval
  - rag-retrieval/vector-databases
  - rag-retrieval/ann-indexes
  - data-architecture/embedding-versioning-re-indexing-strategy
  - enterprise-integration/incremental-synchronization-and-cdc
  - data-architecture/data-retention-and-deletion-architecture
---

## TL;DR
A retrieval index is a derived copy of the source documents, and it starts going stale the moment it is
built. Freshness is how long a source change takes to become visible in retrieval; deletion is removing
every derived artifact of a document when the source goes away; consistency is never serving a mix of
old and new versions of the same document. All three reduce to one discipline: stable document identity,
versioned chunk ids, write-new-then-delete-old, and a reconciliation job for whatever the incremental
path missed.

## Why it exists
Everything a RAG system retrieves is a materialized view: chunks, embeddings, keyword postings, parent
documents, generated chunk contexts, cached answers. None of it is the source of truth, and all of it was
computed from some past snapshot of the source. The source moves: a pricing page is edited, an HR policy
is replaced, a contract is terminated, an employee's files are removed on offboarding, a legal team
receives an erasure request. If the index does not move with it, the model retrieves the old text and
answers with full confidence *and a citation*. That is worse than no answer, because the user has been
given a reason to trust it.

Deletion raises the stakes further. An embedding of a chunk containing personal data is still derived
personal data; a keyword index still contains the words; a cached answer still quotes the paragraph.
"We deleted the document" is only true when every derived artifact is gone, and a retrieval probe can
prove it either way.

The naive answer is a full nightly rebuild: drop the index, re-crawl, re-embed. It fails on four counts.
Freshness is capped at the rebuild interval, which is hours for anything that matters. Cost scales with
the whole corpus every night even when a fraction of a percent changed. The rebuild itself creates an
inconsistency window: delete-then-insert leaves the index partially empty for the duration, insert-then-
delete duplicates every document. And a crawler only sees what exists *now*; without a record of what was
indexed last time, a document deleted at the source simply never comes up again and its chunks live on
forever. The second naive answer, "the model will ignore stale passages", assumes the model can tell which
of two near-identical policy paragraphs is current. It cannot; they look equally authoritative.

## How it works
### Identity first: doc_id, version, chunk id
Every chunk carries `doc_id` (the stable identity of the source: a CMS id, a file path plus drive id, a
ticket number), a `version` (a content hash or the source's revision number), its position `chunk_index`,
and `indexed_at`. The chunk's primary key is deterministic, `f"{doc_id}:{version}:{chunk_index}"`, because
every later operation (replace this document, delete this document, prove this document is gone) needs
to find *all* chunks of a document without knowing how many there are. The content hash is what makes
change detection cheap: hash the normalized text, compare with the stored version, and skip the embedding
call when nothing changed. Note what the hash does not cover: permissions and other metadata can change
while the text stays identical, so ACL refresh needs its own path.

### Detecting change: push, pull, crawl
Three mechanisms, each with a different freshness ceiling. Push (webhooks, change-data-capture from the
source database) tells you about creates, updates *and deletes* within seconds, but needs source
support and must tolerate missed events. Pull (poll a `modified_since` API) is simple and bounded by the
poll interval, but most APIs do not list what was deleted. Full crawl with hash comparison catches
everything and is the only option for dumb sources, at the cost of listing the whole corpus. Deletes are
the universal weak point: the only reliable way for a pull or crawl pipeline to notice one is a manifest
of `doc_id → version` from the previous run, so that `deleted = manifest.keys() − current_listing`.

### Updating: write new, flip, delete old
Replace a changed document in this order: embed and write the new version's chunks under their new ids,
record the new version as current, then delete chunks whose `doc_id` matches and whose `version` does not.
The order matters because at no point is the document absent, and the old version is removed by filter
rather than by assumed chunk count. Overwriting by `doc_id:chunk_index` is the classic bug: a document
that shrinks from nine chunks to seven leaves chunks seven and eight of the old text in the index
indefinitely. The brief window in which both versions are searchable is usually milliseconds; if even that
is unacceptable, group results by `doc_id` after retrieval and keep the newest `indexed_at`, a cheap
belt-and-braces filter that also hides reconciliation lag.

### Deleting: tombstone, fan-out, verify
A delete is a fan-out, not a single call. Targets include the vector index, the keyword (BM25) index of a
hybrid setup, the parent-document store, any LLM-generated chunk contexts or summaries, semantic answer
caches, and traces or logs that stored retrieved text. Tombstone first (mark the document deleted so the
query path filters it out immediately), then run the physical deletes asynchronously with retries, then
verify. Verification is a probe: query with distinctive text from the deleted document and assert nothing
from it returns. The probe matters because many ANN engines apply deletes lazily: HNSW-style graphs
typically mark nodes deleted and skip them at query time until a compaction or optimization rebuilds the
segment, and some engines acknowledge a write before it is searchable. Also note that some managed vector
stores only delete by id or id prefix rather than by metadata filter, which is one more reason the ids
must be deterministic.

### Consistency inside the index
Two further consistency rules. First, one index, one embedding model: vectors from two models live in
different spaces, so a partial re-embed after a model change silently breaks similarity for the
un-migrated half (see embedding versioning / re-indexing). Second, run a periodic reconciliation job that
diffs the source manifest against what the index actually contains and repairs orphans in both directions,
because incremental pipelines drift: a webhook is missed, a batch fails halfway, a delete fan-out hits a
timeout on one of six targets.

### Measuring freshness
Staleness lag: insert a canary document with a unique token, poll retrieval until it appears, record the
delay; do the same for a canary delete. Orphan count from reconciliation. Deleted-document leak probes on a
schedule. Re-embedding cost per day as a proxy for how well change detection is working.

## Example
A versioned upsert, a fan-out delete and a manifest-based reconciliation against a generic vector store and
keyword index. Any engine that supports upsert by id and delete by metadata filter (or by id prefix) fits
this shape.

```python
import hashlib, time

def chunk_ids(doc_id, version, n):
    return [f"{doc_id}:{version}:{i}" for i in range(n)]

def index_document(store, bm25, embed, split, doc_id, text, manifest):
    version = hashlib.sha256(text.encode()).hexdigest()[:16]
    if manifest.get(doc_id) == version:
        return "unchanged"                                  # content hash: no embedding call needed
    chunks = split(text)
    ids = chunk_ids(doc_id, version, len(chunks))
    meta = [{"doc_id": doc_id, "version": version, "chunk": i, "indexed_at": time.time()}
            for i in range(len(chunks))]
    store.upsert(ids, embed(chunks), meta)                  # 1. write the new version alongside the old
    bm25.upsert(ids, chunks, meta)
    old = manifest.get(doc_id)
    manifest[doc_id] = version                              # 2. flip the pointer
    if old:                                                 # 3. delete old chunks by filter, not by count
        store.delete(filter={"doc_id": doc_id, "version": old})
        bm25.delete(filter={"doc_id": doc_id, "version": old})
    return "updated"

def delete_document(store, bm25, caches, doc_id, manifest):
    manifest.pop(doc_id, None)                              # tombstone: query path hides it immediately
    for target in (store, bm25):
        target.delete(filter={"doc_id": doc_id})            # every version, every chunk
    for c in caches:                                        # answer cache, parent store, chunk contexts
        c.invalidate(doc_id=doc_id)

def reconcile(source_listing, manifest, store, bm25, caches):
    for doc_id in set(manifest) - set(source_listing):      # gone at the source: only the manifest sees it
        delete_document(store, bm25, caches, doc_id, manifest)
    return [d for d in source_listing if d not in manifest]  # never indexed: feed back to index_document
```

## Trade-offs
- Incremental upserts give minutes-level freshness but add a manifest, deterministic ids and a
  reconciliation job; a full rebuild is simpler and wins when the corpus is small enough to re-embed
  cheaply, or when you are changing the embedding model anyway.
- Tombstone-then-async-delete makes the query path correct instantly, but the data is still physically
  present until the engine compacts; if a compliance SLA demands physical erasure, you must also schedule
  compaction and verify with a probe.
- Content-hash change detection skips re-embedding unchanged text, but misses metadata-only changes such
  as permission edits, so ACL changes need a separate refresh path or inclusion in the hash.
- Push-based CDC gives the lowest staleness lag but depends on the source emitting events and on your
  handling of missed ones; polling is more robust and simpler but burns API quota and is capped by the
  poll interval.
- Post-retrieval dedup by `doc_id` keeping the newest version hides mixed-version windows cheaply, at the
  cost of occasionally returning fewer than k distinct passages.

## Pitfalls
- Overwriting chunks by position (`doc_id:index`) instead of by version; the symptom is retrieval returning
  paragraphs that no longer exist in the document, which users report as "that is not in the file".
- Leaving the keyword index, parent-document store or answer cache out of the delete fan-out; the symptom
  is a deleted document still being cited, often only via the BM25 leg of hybrid search or from a cached
  answer.
- Treating the engine's delete acknowledgement as proof of removal; the symptom is a deleted-document probe
  still retrieving the text minutes later because the delete is applied lazily or awaits a refresh.
- Changing embedding models and upserting only changed documents; the symptom is recall collapsing for
  older documents and a bimodal distribution of similarity scores.
- Running a pull-based crawler without a manifest; the symptom is an index that only ever grows, with
  retired documents accumulating until someone notices a stale citation.
- Confusing chunk-level `indexed_at` with document-level `modified_at` in recency filters; the symptom is a
  re-indexed old document being treated as "new" by date-sorted retrieval.

## When to use
Every production index needs a freshness tier, a deletion contract and a reconciliation loop; what changes
is how strict each is. Pricing, policy, HR and legal content usually needs minutes-level freshness and
provable deletion; static manuals can tolerate a daily crawl. Decide per content class, write the numbers
down as SLOs, and measure them: staleness lag via canaries, deleted-document leak probes, orphan counts
from reconciliation, and re-embedding cost per day. In day-to-day work this is the stage where the
ingestion pipeline earns its complexity, and in interviews "what happens when a document is deleted?" is
the question that separates people who have run RAG in production from people who have demoed it: the
expected answer is fan-out, tombstone, verification and reconciliation, not "we delete the vector".

## Role lens
- **Engineer:** Implements deterministic chunk ids, the write-new-then-delete-old upsert, the delete
  fan-out across every derived store, and the canary and probe tests that run in CI and on a schedule.
- **Architect:** Owns the freshness SLO per content class and the deletion contract: which systems are in
  the fan-out, what physical-erasure deadline applies, and which system is the source of truth for identity.
- **FDE:** Lives this through customer connectors (SharePoint, Confluence, ServiceNow): negotiating
  webhooks versus polling, demonstrating deletion to the customer's compliance team, and explaining why the
  assistant still knew about a retired policy.

## Check yourself
**Q:** A customer reports that the assistant cited a pricing document retired three days ago. Your pipeline
re-crawls the source hourly. Walk through where the stale content could still be living and how you would
fix it so it cannot happen again.
**A:** The crawler only lists documents that exist now, so without a manifest diff it never noticed the
retirement and the chunks were never deleted; that is the most likely cause. Other candidates: chunks were
upserted by position, so a trailing chunk of an old version survived a shrinking edit; the keyword index or
parent store was not in the delete path, so hybrid search still surfaces it; a semantic answer cache is
replaying an old answer; or the delete was issued but the engine applies deletes lazily and never
compacted. The fix is structural: keep a manifest of indexed `doc_id → version`, compute deletions as
manifest minus current listing, delete by `doc_id` filter across every derived store, tombstone on the
query path first, verify with a probe that searches for distinctive text from the retired document, and
run a scheduled reconciliation so a missed event is repaired within a bounded time. Then add the probe to
monitoring so the next leak pages someone instead of waiting for a customer.

## Quiz
### Q: Why is "write the new version, then delete the old" preferred over "delete the old, then write the new"?
- [ ] It uses fewer vector-store write operations
- [x] The document is never absent from the index during the swap
- [ ] It avoids re-embedding chunks whose text did not change
- [ ] It lets the vector store reuse the old chunk ids
> Delete-then-write leaves a window in which queries about the document find nothing. Writing first and
> removing the old version by filter keeps the document retrievable throughout; re-embedding is avoided by
> the content hash, not by the ordering.

### Q: A document shrinks from nine chunks to seven and the pipeline upserts by `doc_id:index`. What happens?
- [ ] The vector store rejects the upsert because the chunk count changed
- [ ] The seven new embeddings overwrite all nine old entries
- [x] Chunks seven and eight of the old version remain in the index as orphans
- [ ] Retrieval automatically prefers the newer `indexed_at` timestamp
> Upsert by position only touches ids 0–6, so the two trailing ids keep their old text and embeddings.
> Versioned ids plus a delete-by-filter on the old version are what remove them; no engine reconciles
> chunk counts on its own.

### Q: For a pull-based crawler, what is the only reliable way to detect that a document was deleted at the source?
- [ ] Re-index everything whose `modified_at` is newer than the last run
- [ ] Query the vector store for documents similar to the deleted one
- [x] Compare the manifest of previously indexed `doc_id`s with the current source listing
- [ ] Give every chunk a short TTL so stale entries expire by themselves
> A deleted document never appears in a listing again, so `modified_at` filters never see it. Only the
> set difference between what was indexed and what exists now reveals it. TTLs are a fallback that
> bounds damage but cannot distinguish deleted from merely unchanged documents.
