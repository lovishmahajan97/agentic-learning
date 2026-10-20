---
id: inverted-indexes
module: rag-retrieval
name: Inverted indexes
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
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
An inverted index flips the document→words mapping into words→documents: for every term, a sorted
posting list of the documents that contain it, with counts and positions. A query then touches only the
lists for its terms instead of every document, which is what makes lexical search over hundreds of
millions of documents answerable in milliseconds on CPUs (sharded across machines at the high end). The design choices (sorted ids, compression,
immutable segments, skip pointers) all exist to make those list scans fast and updates safe.

## Why it exists
The forward index is the natural one: document id → the words it contains. Answering "which documents
mention indemnification" against a forward index means opening every document and scanning it, O(total
tokens) per query. At a million documents of a thousand tokens each that is a billion token comparisons
per query, and it gets worse linearly as the corpus grows. Caching does not help, because queries are
too diverse.

The observation that fixes it is that queries are short and vocabularies, though large, are far smaller
than the corpus. If for each term you precompute the list of documents containing it, a five-term query
reads five lists. The cost is proportional to how many documents contain the query terms, not to how
many documents exist. Rare terms, which carry most of the ranking signal, have short lists, so the
decisive part of the query is also the cheap part to compute.

Doing this well raises engineering problems that define the structure: posting lists for common terms
are huge and must be compressed; intersecting lists must not require reading all of both; ranking needs
per-document statistics stored alongside the ids; documents arrive and change constantly while queries
are running; and the term dictionary itself must be looked up millions of times a second. Every feature
of a modern inverted index (Lucene underneath Elasticsearch and OpenSearch, or the engines inside Vespa
and Tantivy) is an answer to one of these.

## How it works
### Analysis defines the terms
Before indexing, text passes through an analyzer: tokenize, lowercase, maybe strip accents, stem
("running" → "run"), drop stopwords, split or keep hyphenated tokens. The output tokens are the keys of
the index. Because a query is analyzed the same way and then looked up by exact key, the analyzer is the
entire semantic contract: whatever it does not normalize cannot match. This is why the same text in two
fields with different analyzers behaves like two different indexes, and why a change of analyzer
requires a full re-index.

### Dictionary and postings
The index has two parts.

- The **term dictionary** maps each term to the location of its posting list and to collection
  statistics such as document frequency. It must support exact lookup and, for prefix and wildcard
  queries, ordered traversal. Lucene keeps a finite-state transducer (a compressed, trie-like automaton)
  over term prefixes in memory, which points into on-disk blocks of sorted terms; that is how a dictionary
  of many millions of terms stays fast without being fully resident.
- The **posting list** for a term is the sequence of documents containing it, sorted by document id,
  each entry carrying the term frequency, optionally the positions of each occurrence (for phrase and
  proximity queries) and payload data. Sorted order is what makes everything else possible.

### Why sorted ids and compression
Sorted ids let two lists be intersected with a merge: two cursors, advance the smaller, O(|A| + |B|)
instead of O(|A|·|B|). They also let the list be stored as gaps between consecutive ids rather than
absolute values; gaps are small numbers, especially for common terms, and variable-byte or bit-packed
encodings (PForDelta and relatives) store them in a byte or two, often fewer bits, instead of four. A
list for a term in ten million documents shrinks several-fold and, more importantly, streams through the
CPU cache. Skip data stored per block of roughly a hundred entries (Lucene encodes postings in blocks of
128) lets an intersection jump over stretches of one list that
cannot match the other, so intersecting a rare term with a common one costs about the size of the rare
list.

### Query evaluation
Two strategies exist. Term-at-a-time walks each query term's list fully and accumulates partial scores
per document; document-at-a-time advances all lists in lockstep and scores each document completely
before moving on. Document-at-a-time is what Lucene and most production engines mostly use, because it enables early
termination: with BM25 you can precompute, per term, the maximum score any document can get from it.
Algorithms like WAND and MaxScore use those upper bounds to skip documents that cannot enter the current
top k, so a top-10 query over a term in ten million documents reads a small fraction of the list. This is
why "return the top 10" is far cheaper than "return all matches sorted".

MaxScore makes the idea concrete. Take an OR query "iphone15 case" where the rare term's maximum
possible contribution is 9.0 and the common term's is 1.5. Fill the top-10 heap from the first documents
seen; suppose its weakest entry, the threshold θ, is now 4.0. A document containing only "case" can
score at most 1.5 < θ, so it can never enter the results, and "case" becomes a *non-essential* list: the
engine stops iterating it and only advances the "iphone15" list, probing "case" (via skips) for the
candidate ids that list produces. The common list's four million postings shrink to a few thousand
lookups. WAND reaches the same effect by ordering cursors by current document id and summing upper
bounds until they exceed θ. Block-max variants keep enough per-block data to bound the score within each block of postings
rather than only per term (Lucene stores these as *impacts*, frequency and length pairs), so even a
common term can skip blocks whose local maximum is low.
The pruning is exact, not approximate: the top k is identical to scoring everything, only cheaper, and
it gets better as θ rises, which is why engines prefer not to compute an exact total hit count.

Ranking needs per-document data too: BM25 wants document length, which is stored in a separate per-field
norms structure, and collection-wide N and average length, which are index statistics. In a sharded
engine each shard holds its own statistics unless the query coordinator fetches global ones first.

### Segments, immutability and updates
Mutating a compressed, sorted posting list in place is impractical, so Lucene-style indexes are
write-once. New documents accumulate in memory and are flushed as a new immutable **segment**, a
complete mini-index. A query runs against every segment and merges results. Deletes are recorded in a
per-segment bitset of live documents; the deleted document's postings stay until a background merge
rewrites the segments without it. Updates are a delete plus a re-add. Two consequences are easy to
forget: a document is not searchable until its segment is flushed (the refresh interval), and deleted
documents still affect statistics like document frequency until merged, so scores drift slightly on
churning indexes.

### Phrase and positional queries
Because positions are stored, "fuser overheating" as a phrase is answered by intersecting the two lists
and then checking, within each shared document, whether some position of the first term is immediately
followed by a position of the second. Proximity queries relax "immediately" to a window. Storing
positions substantially enlarges the index, which is why engines let you turn them off per field.

### Relationship to filtering and to IVF
Structured filters (tenant id, date ranges, document type) are themselves posting lists or bitsets
intersected with the term lists, so lexical search gets filtering almost for free; this is the
capability vector indexes struggle to match. And an IVF vector index is literally an inverted index whose
"terms" are cluster ids, which is why the name carries over.

## Example
A tiny index built by hand shows the three operations every engine performs: build postings with
positions, intersect sorted lists with two cursors, and answer a phrase query from positions.

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

## Trade-offs
- Precomputing postings makes queries cost proportional to matching documents instead of corpus size,
  but indexing becomes the expensive, latency-adding step; the balance flips for write-heavy, rarely
  queried data where a scan is cheaper than maintaining the index.
- Storing positions enables phrase and proximity queries at a substantial index-size cost; drop them on
  fields that are only ever filtered or scored as a bag of words.
- Immutable segments give lock-free reads and crash safety, at the price of refresh latency and
  background merge I/O; tightening the refresh interval for near-real-time search trades CPU and more
  small segments for freshness.
- Heavier analysis (stemming, synonyms, decompounding) raises recall on natural-language queries but
  lowers precision on identifiers and names; which side wins depends on the query mix, so measure per
  query type.

## Pitfalls
- Re-indexing partially after an analyzer change, so old segments hold tokens produced by the old rules;
  the symptom is the same query matching a document indexed last month but not its identical twin
  indexed today.
- Expecting a freshly written document to be searchable immediately; the symptom is flaky integration
  tests that pass only with a sleep, caused by the refresh interval and segment flush.
- Ignoring deleted-document bloat on high-churn indexes; the symptom is index size that never shrinks and
  slowly rising query latency until a forced merge.
- Running wildcard or leading-wildcard queries on large term dictionaries; the symptom is a single query
  that pins a CPU, because it expands to thousands of terms and thousands of posting-list reads.
- Reading per-shard scores as globally comparable; the symptom is the same document scoring differently
  after a reshard, because document frequency was computed per shard.
- Loading learned sparse vectors (SPLADE-style expansions) into an engine tuned for BM25 postings; query
  latency can be far higher than BM25 on the same index, because expanded queries have many terms with
  similar weights, which leaves dynamic pruning little to skip. Measure p99 before assuming "it is just
  an inverted index".

## When to use
Any time text must be found by the terms it contains: lexical search, BM25, keyword filters, log search,
and the sparse half of hybrid retrieval. Prefer it over scanning once the corpus is more than a few
thousand documents or queries are frequent, and over a vector index whenever exact matching, filtering
and explainability dominate. Know its limits: it cannot match meaning without an analyzer or a learned
sparse model, and it needs re-indexing when the analyzer changes. Measure index size, refresh latency,
p99 query latency against posting-list length, and merge I/O. In day-to-day work it surfaces as analyzer
configuration, mapping decisions and refresh settings; in interviews, expect to explain why posting lists
are sorted and how a top-k query avoids reading entire lists.

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
