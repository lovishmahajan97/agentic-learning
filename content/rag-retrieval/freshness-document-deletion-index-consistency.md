---
id: freshness-document-deletion-index-consistency
module: rag-retrieval
name: Freshness, document deletion and index consistency
kind: practice
difficulty: core
est_minutes: 15
status: crisp
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
A retrieval index is a derived copy that goes stale the moment it is built. Keep it fresh, delete
completely and never mix versions with one discipline: stable document identity, versioned chunk ids,
write-new-then-delete-old, and a reconciliation job.

## Why it exists
- **Everything retrieved is a materialized view.** Chunks, embeddings, keyword postings, parent
  documents, generated chunk contexts and cached answers were all computed from a past snapshot. None of
  it is the source of truth.
- **The source moves.** A pricing page is edited, an HR policy replaced, a contract terminated, an
  employee's files removed on offboarding, an erasure request received. A stale index answers with full
  confidence *and a citation*, which is worse than no answer because the user was given a reason to trust it.
- **Deletion raises the stakes.** An embedding of a chunk with personal data is still derived personal
  data; a keyword index still holds the words; a cached answer still quotes the paragraph. "We deleted the
  document" is only true when every derived artifact is gone, and a retrieval probe can prove it either way.
- **A full nightly rebuild fails four ways.** Freshness is capped at the rebuild interval (hours). Cost
  scales with the whole corpus even when a fraction of a percent changed. Delete-then-insert leaves the
  index partly empty; insert-then-delete duplicates every document. And a crawler only sees what exists
  *now*, so a deleted document never comes up again and its chunks live on forever.
- **"The model will ignore stale passages" is false.** It cannot tell which of two near-identical policy
  paragraphs is current; they look equally authoritative.

## How it works
### The three properties
| Property | Meaning |
|---|---|
| Freshness | how long a source change takes to become visible in retrieval |
| Deletion | removing every derived artifact of a document when the source goes away |
| Consistency | never serving a mix of old and new versions of the same document |

### Identity first: doc_id, version, chunk id
- **Every chunk carries four fields.** `doc_id` (stable source identity: a CMS id, a file path plus
  drive id, a ticket number), `version` (a content hash or the source's revision number), its position
  `chunk_index`, and `indexed_at`.
- **The primary key is deterministic:** `f"{doc_id}:{version}:{chunk_index}"`, because replacing,
  deleting or proving a document is gone all need *all* its chunks without knowing how many there are.
- **The content hash makes change detection cheap.** Hash the normalized text, compare with the stored
  version, and skip the embedding call when nothing changed.
- **The hash does not cover metadata.** Permissions can change while the text stays identical, so ACL
  refresh needs its own path.

### Detecting change: push, pull, crawl
| Mechanism | Sees deletes? | Freshness ceiling | Cost |
|---|---|---|---|
| Push (webhooks, change-data-capture) | yes: creates, updates *and deletes* | seconds | needs source support; must tolerate missed events |
| Pull (poll a `modified_since` API) | usually not: most APIs do not list deletes | the poll interval | simple |
| Full crawl with hash comparison | only via a manifest | the crawl interval | lists the whole corpus; the only option for dumb sources |

- **Deletes are the universal weak point.** The only reliable way for a pull or crawl pipeline to notice
  one is a manifest of `doc_id → version` from the previous run, so `deleted = manifest.keys() − current_listing`.

### Updating: write new, flip, delete old
1. **Write** the new version's chunks under their new ids (embed first).
2. **Flip:** record the new version as current.
3. **Delete** chunks whose `doc_id` matches and whose `version` does not.

- **Why this order:** at no point is the document absent, and the old version is removed by filter rather
  than by an assumed chunk count.
- **The classic bug is overwriting by `doc_id:chunk_index`.** A document that shrinks from nine chunks to
  seven leaves chunks seven and eight of the old text in the index indefinitely.
- **The both-versions window is normally short:** the gap between two writes plus any engine refresh
  delay. A crash between steps 1 and 3 can stretch it until reconciliation runs.
- **Belt and braces: filter after retrieval.** Per `doc_id`, drop chunks whose `version` is not the
  newest seen (or not the manifest's current version). It is cheap and also hides reconciliation lag.

### Deleting: tombstone, fan-out, verify
- **A delete is a fan-out, not a single call.** Targets: the vector index, the keyword (BM25) index of a
  hybrid setup, the parent-document store, LLM-generated chunk contexts or summaries, semantic answer
  caches, and traces or logs that stored retrieved text.
1. **Tombstone** the document so the query path filters it out immediately.
2. **Delete physically**, asynchronously with retries, in every target.
3. **Verify with a probe:** query distinctive text from the deleted document and assert nothing returns.

- **Why the probe matters:** many ANN engines delete lazily. HNSW-style graphs typically mark nodes
  deleted and skip them at query time until a compaction or optimization rebuilds the segment, and some
  engines acknowledge a write before it is searchable.
- **Some managed vector stores delete only by id or id prefix**, not by metadata filter, which is one
  more reason the ids must be deterministic.

### Guarding the delete path
- **`deleted = manifest − listing` is only as trustworthy as the listing.** An API that times out, pages
  incorrectly or returns empty under a revoked token makes *every* document look deleted, and a naive
  reconciler wipes the index.
- **Require a complete listing:** every page fetched, no error.
- **Cap the blast radius:** refuse a deletion set larger than a threshold fraction of the corpus (for
  example a few percent) without a human or a second listing confirming it.
- **Deletes are the most cautious write**, because they are the only one re-running cannot repair.

### Consistency inside the index
- **One index, one embedding model.** Vectors from two models live in different spaces, so a partial
  re-embed after a model change silently breaks similarity for the un-migrated half (see embedding
  versioning / re-indexing).
- **Run a periodic reconciliation job.** Diff the source manifest against what the index actually holds
  and repair orphans in both directions, because incremental pipelines drift: a webhook is missed, a batch
  fails halfway, a delete fan-out times out on one of six targets.

### Superseded is not the same as deleted
- **Version ids only handle in-place edits.** The common enterprise case is a *different* replacement
  document: "Travel Policy 2025.pdf" next to "Travel Policy 2024.pdf", both live, near-identical text.
  Similarity cannot break the tie.
- **The fix is metadata, not embeddings.** Capture `effective_from` / `superseded_by` (or a
  `status: current | archived` field) at ingestion, filter archived documents out by default, and keep
  them reachable for explicitly historical questions ("what was the policy last year?").
- **A softer alternative is a recency prior:** `score = similarity × exp(−age / τ)`. It prefers newer text
  without hiding older text: right for news-like corpora, wrong for reference material, where a
  ten-year-old standard can still be authoritative.
- **Recency must use the source's `modified_at` or `effective_from`**, never `indexed_at`.

### Measuring freshness
Lag has three terms, each with a different owner:

```
lag = detection delay (webhook latency or poll interval)
    + processing (parse, chunk, embed, write)
    + engine visibility (refresh interval, segment flush, replica sync)
```

- **Worked number:** a five-minute poll with a thirty-second pipeline gives a worst case near five and a
  half minutes. Shortening the poll is pointless if a batch embedding queue holds work for an hour.
- **Staleness lag in practice:** insert a canary document with a unique token, poll retrieval until it
  appears, record the delay; do the same for a canary delete.
- **Other signals:** orphan count from reconciliation, deleted-document leak probes on a schedule, and
  re-embedding cost per day as a proxy for how well change detection works.

## Diagram
Follow a source change from detection to a verified index: edits take the write-new-then-delete-old path,
deletes take the tombstone and fan-out path, and reconciliation catches whatever the incremental path missed.

```mermaid
flowchart TD
    S["Source change: edit or delete"] --> D{"Detected by push, poll or manifest diff"}
    D -->|"edited, new hash"| W["Write new version chunks"]
    W --> F["Flip manifest to new version"]
    F --> O["Delete old version by doc_id filter"]
    D -->|"deleted at source"| T["Tombstone doc_id on query path"]
    T --> X["Fan-out delete: vector, BM25, parent store, caches"]
    X --> P["Probe: search distinctive text, expect nothing"]
    O --> I[("Index serves one version")]
    P --> I
    R["Scheduled reconciliation: manifest vs index"] -.->|"repair orphans both ways"| I
```

## Examples
### Versioned upsert, fan-out delete and reconciliation
Runs against a generic vector store and keyword index. Any engine that supports upsert by id and delete by
metadata filter (or by id prefix) fits this shape.

```python
import hashlib, time

def chunk_ids(doc_id, version, n):
    return [f"{doc_id}:{version}:{i}" for i in range(n)]

def index_document(store, bm25, embed, split, doc_id, text, manifest, tombstones):
    version = hashlib.sha256(text.encode()).hexdigest()[:16]
    tombstones.discard(doc_id)                              # a re-created document must become visible
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

def delete_document(store, bm25, caches, doc_id, manifest, tombstones):
    tombstones.add(doc_id)                                  # query path filters these ids out immediately
    for target in (store, bm25):
        target.delete(filter={"doc_id": doc_id})            # every version, every chunk
    for c in caches:                                        # answer cache, parent store, chunk contexts
        c.invalidate(doc_id=doc_id)
    manifest.pop(doc_id, None)                              # only after every target succeeded: a crash
                                                            # above leaves it for reconcile to retry

def reconcile(source_listing, manifest, tombstones, store, bm25, caches, max_frac=0.05):
    gone = set(manifest) - set(source_listing)              # gone at the source: only the manifest sees it
    if len(gone) > max_frac * max(len(manifest), 1):        # a failed or truncated listing looks like
        raise RuntimeError(f"refusing to delete {len(gone)} docs")  # mass deletion: stop and alert
    for doc_id in gone:
        delete_document(store, bm25, caches, doc_id, manifest, tombstones)
    return [d for d in source_listing if d not in manifest]  # never indexed: feed back to index_document
```

### The shrinking-document orphan, reproduced
Runnable with plain Python: a dict stands in for the index. A nine-chunk document is edited down to seven.

```python
index = {}

def upsert_positional(doc_id, chunks):
    for i, c in enumerate(chunks):
        index[f"{doc_id}:{i}"] = c                       # overwrite by position

def upsert_versioned(doc_id, version, chunks):
    for i, c in enumerate(chunks):
        index[f"{doc_id}:{version}:{i}"] = c             # 1. write new alongside old
    stale = [k for k in index if k.startswith(f"{doc_id}:") and k.split(":")[1] != version]
    for k in stale:
        del index[k]                                     # 3. delete old by filter, not by count

v1 = [f"old para {i}" for i in range(9)]                 # 9 chunks
v2 = [f"new para {i}" for i in range(7)]                 # edited down to 7 chunks

upsert_positional("policy", v1); upsert_positional("policy", v2)
print(sorted(v for v in index.values() if v.startswith("old")))  # ['old para 7', 'old para 8']

index.clear()
upsert_versioned("policy", "v1", v1); upsert_versioned("policy", "v2", v2)
print(len(index), all(v.startswith("new") for v in index.values()))  # 7 True
```

### Worked scenario: a freshness lag budget
A pricing page must be correct in retrieval within 10 minutes of an edit (illustrative SLO).

| Term | Poll design | Webhook design |
|---|---|---|
| Detection delay | 5 min poll interval (worst case) | seconds |
| Processing | 30 s parse, chunk, embed, write | 30 s |
| Engine visibility | refresh interval, a few seconds | a few seconds |
| Worst case | ≈ 5.5 min: meets the SLO | well under 1 min |
| If embeddings go through an hourly batch queue | ≈ 65 min: misses it | ≈ 60 min: misses it |

The lesson: the slowest term owns the lag, so measure each term before tuning the poll interval.

## Production patterns
- **Canary freshness probes.** On a schedule, write a canary document containing a unique token, poll
  retrieval until it appears, then delete it and poll until it vanishes. Emit both delays as metrics and
  alert when p95 exceeds the content class's freshness SLO.
- **Deleted-document leak probes.** Keep a list of distinctive phrases from documents deleted in the
  last N days; query them in CI and hourly against every retrieval leg (vector, BM25, answer cache).
  Any hit pages the owner; track hits per leg to find the store missing from the fan-out.
- **Mass-delete circuit breaker.** Apply reconciliation deletes only when the listing completed every
  page without error and the set is under a fraction such as `max_frac=0.05`; otherwise stop and alert.
  Monitor deletion-set size per run and listing completeness.
- **Read-time version guard.** After retrieval, keep only chunks whose `version` equals the manifest's
  current version for their `doc_id`, and drop tombstoned ids. Count dropped chunks per query; a rising
  count means reconciliation or delete fan-out is lagging.
- **Reconcile and compact on a schedule.** Run the manifest-versus-index diff at a fixed cadence, repair
  orphans both ways, and, where a compliance SLA requires physical erasure, schedule the engine's
  compaction or optimization afterwards and re-run the probe. Track orphan count over time.
- **Default-filter archived documents.** Set `status: current | archived` at ingestion and apply
  `status = current` as a default filter, lifting it only when the query is explicitly historical.
  Monitor how often both a document and its `superseded_by` successor appear in one result set.

## Trade-offs
- **Incremental upserts vs full rebuild.** Upserts give minutes-level freshness but add a manifest,
  deterministic ids and a reconciliation job. A full rebuild is simpler and wins when the corpus is small
  enough to re-embed cheaply, or when you are changing the embedding model anyway.
- **Tombstone-then-async-delete vs physical erasure.** The query path is correct instantly, but data is
  physically present until the engine compacts. If a compliance SLA demands physical erasure, also
  schedule compaction and verify with a probe.
- **Content-hash change detection vs metadata changes.** It skips re-embedding unchanged text but misses
  metadata-only changes such as permission edits, so ACL changes need a separate refresh path or inclusion
  in the hash.
- **Push CDC vs polling.** CDC gives the lowest staleness lag but depends on the source emitting events and
  on handling missed ones. Polling is more robust and simpler but burns API quota and is capped by the
  poll interval.
- **Post-retrieval dedup vs result count.** Deduping by `doc_id` keeping the newest version hides
  mixed-version windows cheaply, at the cost of occasionally returning fewer than k distinct passages.

## Pitfalls
- **Overwriting chunks by position (`doc_id:index`) instead of by version.** Symptom: retrieval returns
  paragraphs that no longer exist, which users report as "that is not in the file". Fix: versioned ids and
  delete-by-filter.
- **Leaving stores out of the delete fan-out.** Symptom: a deleted document is still cited, often only via
  the BM25 leg of hybrid search or a cached answer. Fix: list every derived store as a fan-out target.
- **Treating the delete acknowledgement as proof of removal.** Symptom: a probe still retrieves the text
  minutes later because the delete is lazy or awaits a refresh. Fix: verify with a probe.
- **Changing embedding models and upserting only changed documents.** Symptom: recall collapses for older
  documents and similarity scores become bimodal. Fix: re-embed everything into a new index.
- **Running a pull-based crawler without a manifest.** Symptom: an index that only ever grows, with retired
  documents accumulating until someone notices a stale citation. Fix: diff a manifest each run.
- **Trusting a listing that failed silently.** Symptom: the document count drops by most of the corpus
  right after a connector outage or token expiry, then "I could not find anything" answers everywhere.
  Fix: require a complete listing and cap the deletion fraction.
- **Treating a replacement as unrelated to what it supersedes.** Symptom: answers alternate between old and
  new policy depending on phrasing, both cited as current. Fix: `superseded_by` metadata and a default filter.
- **Confusing chunk-level `indexed_at` with document-level `modified_at` in recency filters.** Symptom: a
  re-indexed old document is treated as "new" by date-sorted retrieval.

## When to use
Every production index needs a freshness tier, a deletion contract and a reconciliation loop; what changes
is how strict each is.
- **Strict tier:** pricing, policy, HR and legal content usually needs minutes-level freshness and
  provable deletion.
- **Relaxed tier:** static manuals can tolerate a daily crawl.
- **Decide per content class**, write the numbers down as SLOs, and measure them.
- **Measure:** staleness lag via canaries, deleted-document leak probes, orphan counts from
  reconciliation, and re-embedding cost per day.
- **Day to day:** this is the stage where the ingestion pipeline earns its complexity.
- **Interviews:** "what happens when a document is deleted?" separates people who have run RAG in
  production from people who have demoed it. Expected answer: fan-out, tombstone, verification and
  reconciliation, not "we delete the vector".

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

## Interview questions
### Why do chunk ids need to include a document version, not just doc_id and position?
- **Replacement must find every old chunk.** With `f"{doc_id}:{version}:{chunk_index}"` you delete the old
  version by filter without knowing how many chunks it had.
- **Positional ids leak orphans.** A document that shrinks from nine chunks to seven keeps old chunks seven
  and eight forever, because the upsert only touched ids 0 to 6.
- **Deterministic ids also matter for stores that delete only by id or id prefix**, since there is no
  metadata filter to fall back on.

### Your nightly reconciliation job just deleted 80% of the index. What happened and how do you stop it recurring?
- **Most likely the source listing failed silently:** a timeout, broken paging or a revoked token returned
  an empty or truncated list, so `deleted = manifest − listing` covered almost everything.
- **Recover** by re-running ingestion from the source once the connector is healthy; the manifest diff then
  reports those documents as never indexed.
- **Prevent** by requiring a complete listing (every page, no error) and capping the deletion fraction, for
  example `max_frac=0.05`, above which the job stops and alerts a human.

### When would you choose a full rebuild over incremental upserts?
- **Small corpus:** when re-embedding everything is cheap, a rebuild avoids the manifest, deterministic ids
  and reconciliation job entirely.
- **Embedding model change:** vectors from two models live in different spaces, so you re-embed everything
  into a new index anyway.
- **Not when freshness matters:** a rebuild caps freshness at the rebuild interval (hours) and its cost
  scales with the whole corpus even when a fraction of a percent changed.

### A compliance team asks you to prove an erased customer record is gone from the RAG system. What do you show them?
- **The fan-out list:** vector index, keyword index, parent-document store, generated chunk contexts,
  semantic answer caches and any traces or logs that stored retrieved text.
- **Probe results:** queries for distinctive text from the record against every retrieval leg returning
  nothing, because a delete acknowledgement is not proof on engines that delete lazily.
- **Physical erasure evidence:** if the SLA demands it, the scheduled compaction or optimization run after
  the delete, followed by a repeated probe.

### How do you set and measure a freshness SLO for a pricing page?
- **Break lag into three terms:** detection delay, processing, and engine visibility; the slowest term owns
  the total.
- **Worked number:** a five-minute poll plus a thirty-second pipeline is about 5.5 minutes worst case, which
  meets a 10-minute SLO, but an hourly batch embedding queue pushes it to about 65 minutes.
- **Measure with canaries:** write a document with a unique token, poll retrieval until it appears, record
  the delay, then do the same for a canary delete and alert on p95.

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
