---
id: parent-document-retrieval
module: rag-retrieval
name: Parent-document retrieval
kind: pattern
difficulty: core
est_minutes: 13
related:
  - rag-retrieval/chunking-strategies
  - rag-retrieval/retrieval-granularity
  - rag-retrieval/chunk-overlap
  - rag-retrieval/contextual-retrieval
  - rag-retrieval/lost-in-the-middle
  - rag-retrieval/citations-grounding
  - rag-retrieval/recall-vs-precision-trade-off
  - rag-retrieval/freshness-document-deletion-index-consistency
sources:
  - Official docs: LangChain ParentDocumentRetriever
  - Official docs: LlamaIndex sentence-window and auto-merging retrievers
---

## TL;DR
The best unit for matching a query is small, because a sentence or short paragraph embeds as a sharp
key; the best unit for answering is larger, because the surrounding section carries the definitions,
table headers and caveats the passage depends on. Parent-document retrieval separates the two: index
small child chunks, each linked to a larger parent, search on the children, and hand the generator the
parents. You get the recall of small chunks and the context of big ones, paying in context tokens.

## Why it exists
A single chunk size is a compromise that is wrong in both directions. Take the sentence "The limit is 50
requests per minute." As a chunk of its own it is an excellent key: a query about rate limits lands on
it with a high score. As an answer it is useless, because it does not say which endpoint, which plan,
or that bursts up to 100 are tolerated for ten seconds; those facts sit in the surrounding 1,500-token
section. Embed that whole section instead and the key goes blurry: the section also discusses
authentication and pagination, the vector is an average of all three, and the rate-limit query now
scores middling against it and may lose to a short FAQ entry that merely mentions limits.

The usual workarounds patch one side and break the other. Large chunks give context but lower recall
and mix topics. Small chunks with a large k return a pile of fragments that the generator must stitch
together, with no guarantee that the fragments it needs come from the same section, and with duplicates
of the same neighbourhood crowding the prompt. Overlap smears a little context across boundaries but
cannot carry a table header 1,000 tokens back to the row that needs it.

The observation that resolves this is that retrieval and generation do not have to consume the same
unit. The index can hold whatever unit matches best, and each indexed unit can carry a pointer to the
unit that answers best. Retrieval becomes a lookup on small keys followed by a join to large values.

## How it works
### Data model
Two stores with a join key:

- **Vector index of children.** Small chunks (a sentence, or roughly 100–300 tokens as an illustrative
  range) with metadata `{parent_id, doc_id, offset}`. Only children are embedded.
- **Parent store.** A plain key-value or document store mapping `parent_id` to the parent text (a section,
  a page, or roughly 1–2k tokens) and its metadata. Parents are never searched, so they need no vectors.

Children carry `parent_id` because retrieval is a join; parents live outside the vector index because
embedding them would waste space and tempt someone to search them.

### Indexing
Split each document into parents, preferably at structural boundaries (headings, sections) so each
parent is coherent, with a size cap so a runaway section is cut. Split each parent into children, embed
the children, store the parents. Illustratively, a 40-page manual might become 60 parents and 900
children; the index holds 900 vectors, the parent store holds 60 texts.

### Query path
1. Search the child index with a k larger than the number of parents you will return, say 20, because
   several children usually belong to the same parent.
2. Group hits by `parent_id`. Score each parent, typically by its best child's score (good for pinpoint
   questions) or by the number or fraction of its children that hit (good for "which section is about
   this" questions).
3. Deduplicate and take the top-n parents that fit the context budget: n × parent tokens must fit.
4. Optionally rerank the parents, or rerank children first and then regroup, with a cross-encoder.
5. Send parents to the generator, but keep the matched child spans for citations and highlighting, since
   a 2k-token citation is not something a user can verify.

### Variants
- **Sentence-window retrieval.** The child is a single sentence; the "parent" is not a fixed node but a
  window of a few sentences either side of the hit, assembled at query time. The window is centred on the
  match, which suits dense prose where fixed section boundaries are arbitrary.
- **Hierarchical or auto-merging retrieval.** Three or more levels (document, section, paragraph). If
  enough children of one node are retrieved, they are replaced by the node itself. The rule exists because
  many hits in one section are evidence that the section, not the fragments, is the right unit.
- **Document as parent.** The parent is the whole document. Fine for short, self-contained records (FAQ
  entries, tickets); for long documents it collapses back into context stuffing.

### Choosing the score aggregation
Max-child scoring finds the parent containing the single best passage. Count-based scoring favours
parents with many moderate hits, which can be broad overview sections. Pick by question type, and
measure: the two rules rank parents differently on the same child hits.

## Example
A minimal two-store implementation and the query-side join. `embed`, `index.add`, `index.search` and
`split` are stubs for your encoder, vector store and chunker.

```python
from collections import defaultdict

parents = {}                                   # parent_id -> {"text": ..., "doc": ...}

def ingest(doc_id, text):
    for p_i, section in enumerate(split(text, by="heading", max_tokens=1500)):
        pid = f"{doc_id}#p{p_i}"
        parents[pid] = {"text": section, "doc": doc_id}
        for c_i, child in enumerate(split(section, max_tokens=200)):
            index.add(id=f"{pid}#c{c_i}", vector=embed(child, kind="passage"),
                      meta={"parent_id": pid, "child_text": child})

def retrieve_parents(query, k_children=20, n_parents=4):
    hits = index.search(embed(query, kind="query"), k_children)   # [(score, meta)]
    by_parent = defaultdict(list)
    for score, meta in hits:
        by_parent[meta["parent_id"]].append((score, meta["child_text"]))
    ranked = sorted(by_parent.items(), key=lambda kv: max(s for s, _ in kv[1]), reverse=True)
    out = []
    for pid, children in ranked[:n_parents]:
        out.append({"parent_id": pid, "text": parents[pid]["text"],
                    "matched_spans": [t for _, t in children]})   # keep spans for citations
    return out

ctx = retrieve_parents("What is the rate limit for the search endpoint on the Pro plan?")
# A 200-token child "Pro plan: 50 requests/minute, bursts to 100 for 10 s" scores highest; the
# generator receives its 1,500-token parent section, which also names the endpoint and the
# exceptions, and the citation points at the matched child span rather than the whole section.
```

## Trade-offs
- Each hit now costs parent-sized context rather than child-sized, so fewer distinct sources fit a
  budget; this favours depth and flips against you when questions need breadth across many documents.
- Children give sharp keys but the index holds many more vectors per document, raising storage and ANN
  cost; the trade is worth it until index size, not quality, is the constraint.
- Deduplication removes redundant parents but collapses several strong hits into one source; for
  aggregation questions that want evidence from many places, return more parents or use sentence windows.
- Structural parents (by heading) are coherent but vary wildly in size; fixed-size parents are
  predictable but cut mid-topic, and the right choice follows the corpus's formatting discipline.

## Pitfalls
- Making the whole document the parent for long documents; the symptom is context overflow, lost-in-the-
  middle effects and a cost spike, with no recall gain over stuffing.
- Skipping deduplication; the symptom is the same parent repeated several times in the prompt and fewer
  unique sources than the budget allowed.
- Citing the parent instead of the matched child span; the symptom is citations pointing at a 2k-token
  section that users cannot verify, and reviewers calling the citations wrong.
- Reranking children and then emitting parents in child order without regrouping; the symptom is parent
  order that does not reflect the strongest evidence.
- Parent store and child index falling out of sync after re-indexing or deletion; the symptom is
  `parent_id` lookups that miss and silently produce an empty context.
- Children so small they are generic ("See below.", "Note:"); the symptom is high similarity scores on
  sentences that carry no information.

## When to use
Use it for long, structured documents where answers depend on surrounding context: manuals, contracts,
runbooks, codebases with file-level context, research papers. Skip it for corpora of short,
self-contained records such as tickets, FAQ entries or product listings, where the record already is the
right unit for both matching and answering. Measure child-level recall@k against a single-chunk-size
baseline, then answer faithfulness and the tokens per request, since the gain in context is paid for in
budget. Day to day it is the first structural change to try when answers are "right passage, wrong
details" because the generator lacked the surrounding section. In interviews it appears as
"small-to-big" retrieval, and the strong answer covers the join, the score aggregation and the citation
granularity.

## Role lens
- **Engineer:** Builds the two-store pipeline, the parent grouping and deduplication, and keeps child
  spans for citations while sending parents to the generator.
- **Architect:** Sets parent and child sizes against the context budget and the corpus structure, and
  decides whether the parent store is the system of record or a derived cache.
- **FDE:** Recognises "the bot found the right page but missed the caveat" in a customer's failures and
  maps their document structure (headings, sections) onto parent boundaries.

## Check yourself
**Q:** A RAG system over 200-page product manuals retrieves the right sentence but answers wrongly because
the generator never sees the table header or the plan name. Design the fix and name the new costs.
**A:** Switch to parent-document retrieval: split each manual into section-level parents at headings
with a size cap, split sections into small children, embed only the children with `parent_id` metadata,
and store parents in a key-value store. At query time search the children with a generous k, group by
parent, score by best child, deduplicate, take the top few parents that fit the context budget, and keep
the matched child spans for citations. New costs: each hit consumes a section's worth of tokens so fewer
sources fit, the index holds many more vectors, and the parent store must stay in sync with the index on
every re-index or deletion. Validate with child recall@k and answer faithfulness against the old
single-size baseline.

## Quiz
### Q: Why are parents stored outside the vector index rather than embedded alongside the children?
- [ ] Vector databases cannot store documents longer than 512 tokens
- [x] Parents are never searched, only fetched by id after a child hit, so embedding them wastes space and invites blurry whole-section matches
- [ ] Embedding parents would make the children's vectors less accurate
- [ ] Parents must be stored in a relational database for citations
> The pattern deliberately matches on sharp child keys and answers with parent values; the parent store is
> a join target, and a plain key-value lookup is all it needs.

### Q: Which score aggregation ranks a parent by the single best passage it contains?
- [ ] Mean of all child scores in the parent
- [ ] Number of children retrieved from the parent
- [x] Maximum child score within the parent
- [ ] Fraction of the parent's children that were retrieved
> Max-child scoring suits pinpoint questions; count or fraction rules favour broad sections with many
> moderate hits and are what auto-merging uses to decide a whole node is the right unit.

### Q: A team sets the parent to the entire document for 200-page manuals. What is the most likely result?
- [ ] Higher precision, because the whole document is always relevant
- [ ] Lower index size, because fewer vectors are stored
- [x] Context overflow and lost-in-the-middle effects, collapsing back into context stuffing
- [ ] Faster retrieval, because parent lookups replace vector search
> The parent must fit the context budget alongside other parents; a whole long manual per hit defeats the
> purpose, which is sharp matching with just enough surrounding context.
