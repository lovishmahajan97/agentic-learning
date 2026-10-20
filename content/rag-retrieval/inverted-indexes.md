---
id: inverted-indexes
module: rag-retrieval
name: Inverted indexes
kind: concept
difficulty: core
est_minutes: 14
status: crisp
related:
  - rag-retrieval/bm25-internals
  - rag-retrieval/dense-vs-sparse-retrieval
  - rag-retrieval/hybrid-search
  - rag-retrieval/metadata-filtering
  - rag-retrieval/freshness-document-deletion-index-consistency
  - backend-fundamentals/database-design
sources:
  - Manning, Raghavan & Schütze, 2008 — Introduction to Information Retrieval
  - Broder et al., 2003 — Efficient query evaluation using a two-level retrieval process
  - Official docs: Apache Lucene
---

## TL;DR
An inverted index maps each term to a sorted posting list of the documents containing it, so a query
reads only its terms' lists. Sorted ids, compression, skips and immutable segments make lexical search
over hundreds of millions of documents answer in milliseconds.

## Why it exists
- **The forward index is too slow.** Document id → words means "which documents mention indemnification"
  opens and scans every document: O(total tokens) per query. A million documents of a thousand tokens each
  is a billion token comparisons per query, growing linearly with the corpus.
- **Caching does not help,** because queries are too diverse.
- **Queries are short; vocabularies are smaller than corpora.** Precompute, per term, the documents that
  contain it, and a five-term query reads five lists. Cost tracks how many documents contain the query
  terms, not how many exist.
- **The decisive part is the cheap part.** Rare terms carry most of the ranking signal and have short
  lists.
- **Doing it well defines the structure.** Common-term lists are huge (compress them); intersections must
  not read all of both lists; ranking needs per-document statistics; documents change while queries run;
  the dictionary is hit millions of times a second. Every feature of Lucene (under Elasticsearch and
  OpenSearch) or the engines inside Vespa and Tantivy answers one of these. At the high end it is sharded
  across machines, still on CPUs.

## How it works
### Analysis defines the terms
1. Tokenize, lowercase, maybe strip accents.
2. Stem ("running" → "run"), drop stopwords, split or keep hyphenated tokens.
3. The output tokens are the index keys.

- **The analyzer is the entire semantic contract.** A query is analyzed the same way and looked up by
  exact key, so whatever the analyzer does not normalize cannot match.
- **Consequences:** the same text in two fields with different analyzers behaves like two different
  indexes, and changing an analyzer requires a full re-index.

### Dictionary and postings
- **Term dictionary.** Maps each term to its posting list's location and to collection statistics such as
  document frequency. It needs exact lookup and, for prefix and wildcard queries, ordered traversal.
  Lucene keeps a finite-state transducer (a compressed, trie-like automaton) over term prefixes in memory,
  pointing into on-disk blocks of sorted terms, so many millions of terms stay fast without being fully
  resident.
- **Posting list.** The documents containing the term, sorted by document id; each entry carries term
  frequency, optionally positions (for phrase and proximity queries) and payload data. Sorted order makes
  everything else possible.

### Why sorted ids and compression
- **Merge intersection.** Two cursors, advance the smaller: O(|A| + |B|) instead of O(|A|·|B|).
- **Gap encoding.** Store differences between consecutive ids; gaps are small, especially for common
  terms. Variable-byte or bit-packed encodings (PForDelta and relatives) use a byte or two, often fewer
  bits, instead of four.
- **Cache-friendly.** A list for a term in ten million documents shrinks several-fold and, more
  importantly, streams through the CPU cache.
- **Skip data.** Stored per block of roughly a hundred entries (Lucene encodes postings in blocks of 128),
  it lets an intersection jump over stretches that cannot match. Rare AND common costs about the size of
  the rare list.

### Query evaluation
| Strategy | How | Consequence |
|---|---|---|
| Term-at-a-time | walk each term's list fully, accumulate partial scores per document | every list is read to the end |
| Document-at-a-time | advance all lists in lockstep, score each document completely | what Lucene and most production engines mostly use, because it enables early termination |

- **Upper bounds:** with BM25 you can precompute, per term, the maximum score any document can get from it.
- **WAND and MaxScore** use those bounds to skip documents that cannot enter the current top k, so a
  top-10 query over a term in ten million documents reads a small fraction of the list.
- **That is why** "return the top 10" is far cheaper than "return all matches sorted".

MaxScore step by step, OR query "iphone15 case" (rare term max 9.0, common term max 1.5):
1. Fill the top-10 heap from the first documents seen; its weakest entry, the threshold θ, is now 4.0.
2. A document containing only "case" scores at most 1.5 < θ, so it can never enter the results.
3. "case" becomes a *non-essential* list: the engine stops iterating it and only advances "iphone15".
4. It probes "case" (via skips) only for the candidate ids that list produces; four million postings
   shrink to a few thousand lookups.

- **WAND** reaches the same effect by ordering cursors by current document id and summing upper bounds
  until they exceed θ.
- **Block-max variants** bound the score within each block of postings, not only per term (Lucene stores
  these as *impacts*, frequency and length pairs), so even a common term can skip low-scoring blocks.
- **The pruning is exact,** not approximate: the top k is identical to scoring everything, only cheaper.
  It improves as θ rises, which is why engines prefer not to compute an exact total hit count.

Ranking data beyond postings:
- BM25 needs document length, stored in a separate per-field norms structure, plus collection-wide N and
  average length as index statistics.
- In a sharded engine each shard holds its own statistics unless the coordinator fetches global ones first.

### Segments, immutability and updates
- **Write-once,** because mutating a compressed, sorted list in place is impractical.
- **Segments.** New documents accumulate in memory and flush as a new immutable **segment**, a complete
  mini-index. Queries run against every segment and merge results.
- **Deletes** go into a per-segment bitset of live documents; postings stay until a background merge
  rewrites the segments without them. **Updates** are a delete plus a re-add.
- **Easy to forget, 1:** a document is not searchable until its segment is flushed (the refresh interval).
- **Easy to forget, 2:** deleted documents still affect statistics like document frequency until merged,
  so scores drift slightly on churning indexes.

### Phrase and positional queries
- **Phrase** "fuser overheating": intersect the two lists, then check within each shared document whether
  a position of the first term is immediately followed by one of the second.
- **Proximity** relaxes "immediately" to a window.
- **Cost:** positions substantially enlarge the index, which is why engines let you turn them off per field.

### Relationship to filtering and to IVF
- **Filters are postings too.** Tenant id, date ranges and document type are posting lists or bitsets
  intersected with term lists, so lexical search gets filtering almost for free; vector indexes struggle
  to match this.
- **IVF is an inverted index** whose "terms" are cluster ids, which is why the name carries over.

## Examples
### A tiny index by hand
Build postings with positions, intersect sorted lists with two cursors, and answer a phrase query from
positions: the three operations every engine performs.

```python
import re
from collections import defaultdict

docs = {1: "pgvector supports hnsw and ivfflat indexes",
        2: "hnsw graphs trade memory for recall",
        3: "ivfflat partitions vectors with k-means",
        4: "recall measures how many true neighbors the index returns"}

analyze = lambda text: re.findall(r"[a-z0-9]+", text.lower())   # shared by index and query
postings = defaultdict(list)          # term -> [(doc_id, tf, [positions]) ...], doc ids ascending
doc_len = {}
for doc_id, text in sorted(docs.items()):                          # sorted => lists stay sorted
    tokens = analyze(text); doc_len[doc_id] = len(tokens)
    where = defaultdict(list)
    for i, t in enumerate(tokens): where[t].append(i)
    for t, pos in where.items(): postings[t].append((doc_id, len(pos), pos))

def intersect(a, b):                  # AND of two sorted lists: O(|a| + |b|), no hashing
    i = j = 0; out = []
    while i < len(a) and j < len(b):
        if a[i][0] == b[j][0]: out.append(a[i][0]); i += 1; j += 1
        elif a[i][0] < b[j][0]: i += 1
        else: j += 1
    return out

def phrase(t1, t2):                   # positions make "hnsw graphs" differ from "graphs hnsw"
    p1 = {d: p for d, _, p in postings[t1]}; p2 = {d: p for d, _, p in postings[t2]}
    return [d for d in sorted(p1.keys() & p2.keys()) if any(x + 1 in p2[d] for x in p1[d])]

print(postings["hnsw"])                                    # [(1, 1, [2]), (2, 1, [0])]
print(intersect(postings["hnsw"], postings["recall"]))     # [2]
print(phrase("hnsw", "graphs"), phrase("graphs", "hnsw"))  # [2] []
print({t: len(p) for t, p in postings.items() if len(p) > 1})  # df>1: {'hnsw': 2, 'ivfflat': 2, 'recall': 2}
```

Real engines add compression, skip pointers and segments on top, but the shape is exactly this: a
dictionary of terms, sorted lists, and merge-style traversal.

### Worked scenario: "iphone15 case" over ten million items
| Step | Postings touched | Why |
|---|---|---|
| Naive OR, score every match | ~4,008,000 | every document containing either term |
| AND with skips | ~8,000 plus skip jumps in "case" | driven by the rare list; skip data jumps blocks of 128 in the common list |
| Top-10 OR with MaxScore, θ = 4.0 | ~8,000 plus up to ~8,000 skip-assisted probes into "case" | "case" alone maxes at 1.5 < θ, so it becomes non-essential |
| Same, with an exact total hit count | ~4,008,000 | counting every match defeats the pruning |

The ranking is identical in every row; only the work differs.

### Production snippet: index settings that follow from the structure
```json
PUT /products
{
  "settings": { "index": { "refresh_interval": "1s" } },
  "mappings": {
    "properties": {
      "title":     { "type": "text" },
      "body":      { "type": "text", "index_options": "freqs" },
      "tenant_id": { "type": "keyword" },
      "sku":       { "type": "keyword" }
    }
  }
}
```

- `body` keeps frequencies for BM25 but drops positions, so no phrase queries on it and a smaller index.
- `tenant_id` and `sku` are `keyword` fields: one exact term each, ideal for filters.

## Production patterns
- **Reindex behind an alias after analyzer changes.** Create a new index with the new analyzer, copy data
  with the `_reindex` API, then switch the read alias atomically. Monitor document counts in old and new
  indexes before the swap, so no partially re-analyzed index serves traffic.
- **Wait for refresh in tests, not sleep.** Index test documents with `refresh=wait_for` (or call
  `_refresh`) so integration tests see their writes deterministically. Monitor flaky-test rate in CI.
- **Pause refresh during bulk loads.** Set `refresh_interval` to `-1` for a large backfill and restore it
  afterwards, which avoids many tiny segments. Monitor segment count and merge time after the load.
- **Watch deleted-document bloat.** On high-churn indexes track the deleted-docs count (the `_cat/indices`
  API shows it) and run a force merge with `only_expunge_deletes` in a quiet window if merges lag.
  Monitor index size and p99 latency over weeks.
- **Keep top-k cheap.** Leave `track_total_hits` at its bounded default rather than demanding exact
  counts, and avoid leading wildcards on large dictionaries. Monitor p99 latency against posting-list
  length of the query terms.

## Trade-offs
- **Postings make queries cost matching documents, not corpus size,** but indexing becomes the expensive,
  latency-adding step; the balance flips for write-heavy, rarely queried data where a scan is cheaper.
- **Positions enable phrase and proximity queries** at a substantial index-size cost; drop them on fields
  only ever filtered or scored as a bag of words.
- **Immutable segments give lock-free reads and crash safety,** at the price of refresh latency and merge
  I/O; a tighter refresh interval for near-real-time search trades CPU and more small segments for
  freshness.
- **Heavier analysis (stemming, synonyms, decompounding)** raises recall on natural-language queries but
  lowers precision on identifiers and names; which side wins depends on the query mix, so measure per
  query type.

## Pitfalls
- **Partial re-index after an analyzer change.** Old segments hold tokens from the old rules. Symptom: a
  query matches a document indexed last month but not its identical twin indexed today.
- **Expecting writes to be searchable immediately.** Symptom: flaky integration tests that pass only with
  a sleep, caused by the refresh interval and segment flush.
- **Ignoring deleted-document bloat.** Symptom: index size never shrinks and query latency slowly rises
  until a forced merge.
- **Wildcard or leading-wildcard queries on large dictionaries.** Symptom: one query pins a CPU, because it
  expands to thousands of terms and thousands of posting-list reads.
- **Reading per-shard scores as globally comparable.** Symptom: the same document scores differently after
  a reshard, because document frequency was computed per shard.
- **Learned sparse vectors (SPLADE-style) in an engine tuned for BM25.** Expanded queries have many terms
  with similar weights, leaving dynamic pruning little to skip, so latency can be far higher than BM25 on
  the same index. Measure p99 before assuming "it is just an inverted index".

## When to use
- **Use whenever** text must be found by the terms it contains: lexical search, BM25, keyword filters, log
  search, and the sparse half of hybrid retrieval.
- **Prefer it over scanning** once the corpus is more than a few thousand documents or queries are
  frequent; **over a vector index** whenever exact matching, filtering and explainability dominate.
- **Know its limits:** no meaning without an analyzer or a learned sparse model, and a re-index when the
  analyzer changes.
- **Measure:** index size, refresh latency, p99 query latency against posting-list length, and merge I/O.
- **Day to day** it surfaces as analyzer configuration, mapping decisions and refresh settings.
- **Interviews:** explain why posting lists are sorted and how a top-k query avoids reading entire lists.

## Role lens
- **Engineer:** Defines field mappings and analyzers, keeps query-time analysis identical to index-time,
  and tunes refresh and merge settings against measured freshness and latency targets.
- **Architect:** Decides what counts as a document, which fields carry positions, how sharding interacts
  with global statistics, and how the lexical index stays consistent with the vector store.
- **FDE:** Explains to a customer why their log search returns last week's events but not the last
  minute's, and why an analyzer change means a full re-index window.

## Check yourself
**Q:** A product search handles ten million items. The term "case" appears in four million of them and
"iphone15" in eight thousand. A user searches "iphone15 case". Explain how the engine evaluates this
query without reading four million postings, and what structures make that possible.
**A:** The engine analyzes the query into two terms and opens both posting lists, which are sorted by
document id. A document-at-a-time traversal advances the two cursors in lockstep; because the "iphone15"
list has only eight thousand entries, the engine uses skip pointers in the "case" list to jump ahead to
each candidate id from the rare list rather than reading the common list sequentially, so the
intersection costs on the order of the small list plus skips. For top-k ranking, per-term score upper
bounds let WAND or MaxScore skip whole blocks of the "case" list whose best possible BM25 contribution
cannot lift a document into the current top ten. Compression keeps the common list cache-friendly, and
the term dictionary lookups are two trie traversals. If the query were OR rather than AND, the same upper
bounds still prune most of the common list for a top-10 result.

## Quiz
### Q: Why are posting lists kept sorted by document id?
- [ ] So that the most relevant documents appear first in the list
- [x] So that lists can be intersected with a linear merge, compressed as small gaps, and skipped through with pointers
- [ ] Because document ids are assigned in order of relevance by the analyzer
- [ ] So that BM25 scores can be stored as a running cumulative sum
> Sorted order turns AND queries into a two-cursor merge, makes consecutive ids differ by small gaps that
> compress well, and allows skip pointers to jump past non-matching stretches. Relevance is computed at
> query time; the list has no notion of it.

### Q: Why does a Lucene-based engine write immutable segments and record deletes in a bitset instead of editing posting lists in place?
- [x] Because compressed, sorted lists cannot be modified efficiently, and immutable files allow lock-free concurrent reads and simple crash recovery
- [ ] Because deleted documents must remain searchable for audit purposes
- [ ] Because the term dictionary cannot be rebuilt after a delete
- [ ] Because bitsets are the only structure that supports phrase queries
> Inserting into a bit-packed, delta-encoded list would mean rewriting it; appending whole segments and
> masking deletes keeps readers lock-free and recovery simple, at the cost of refresh latency and merge
> work. Deleted documents are hidden from results, not kept for audit.

### Q: After changing the stemmer on a field, a team re-indexes only documents modified in the last week. What symptom should they expect?
- [ ] No change, because stemming is applied at query time only
- [ ] All older documents become unsearchable until the index is restarted
- [x] Queries analyzed with the new stemmer match recently indexed documents but miss older ones whose postings still hold the old tokens
- [ ] Older documents rank higher because their posting lists are shorter
> The analyzer is applied at both index and query time, and lookups are exact-key matches on the stored
> tokens. Documents indexed under the old rules hold old tokens, so the new query tokens do not find
> them; a full re-index is required.
