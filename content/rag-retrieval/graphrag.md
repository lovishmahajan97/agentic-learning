---
id: graphrag
module: rag-retrieval
name: GraphRAG
kind: pattern
difficulty: advanced
est_minutes: 16
status: reviewed
related:
  - rag-retrieval/knowledge-graphs
  - rag-retrieval/multi-hop-retrieval
  - rag-retrieval/query-decomposition
  - rag-retrieval/chunking-strategies
  - rag-retrieval/hybrid-search
  - rag-retrieval/long-context-vs-rag
  - agents/context-compaction-summarization
  - infra-capacity-economics/cost-modeling
sources:
  - Edge et al., 2024 — From Local to Global: A Graph RAG Approach to Query-Focused Summarization
  - Traag, Waltman & van Eck, 2019 — From Louvain to Leiden: guaranteeing well-connected communities
  - Official docs: Microsoft GraphRAG
---

## TL;DR
GraphRAG uses an LLM at index time to extract entities and relationships from every chunk, builds a graph,
clusters it into a hierarchy of communities, and pre-writes a summary of each community. Queries then
retrieve over entities and summaries instead of raw chunks. It exists because chunk-level vector search
cannot answer corpus-wide questions ("what are the main themes?") or join facts spread across documents;
the price is a large, LLM-heavy indexing cost that must be repaid by those query types.

## Why it exists
Standard RAG retrieves the top-k chunks most similar to the query. That works when the answer lives in a
few chunks that resemble the question. Two classes of question break it.

*Global, sensemaking questions:* "What are the recurring complaints across these 5,000 support tickets?" No
single chunk is similar to that question, and the answer is a property of the whole corpus. Top-k returns a
handful of arbitrary tickets and the model summarises those, confidently and unrepresentatively. Stuffing
the whole corpus into a long context fails on size, cost, and the model's uneven use of long inputs.

*Connective questions:* "Which suppliers are linked to the plant that had the recall?" The plant is
mentioned in one document, its suppliers in another, and the documents share an entity, not vocabulary or
embedding neighbourhood. Vector search finds one side of the join and misses the other.

The naive fixes, larger k or map-reduce summarisation of every chunk at query time, either dilute context
or pay the full corpus-reading cost on every query. GraphRAG moves that cost to index time: read everything
once, extract the structure (who and what is connected to whom), summarise groups of related things ahead
of time, and let queries reuse that work. The design bet is that structure and pre-summarisation are worth
paying for up front when many queries need a corpus-level view.

## How it works
### Indexing pipeline
1. **Chunk** the corpus. Chunk size matters more than in ordinary RAG because each chunk becomes an LLM
   extraction call: smaller chunks extract more entities but cost more calls.
2. **Extract** with an LLM prompt per chunk: entities (name, type, description) and relationships (source,
   target, description, strength), optionally claims. The reference implementation can run extra
   "gleaning" passes asking the model whether it missed anything, *because* single-pass extraction
   under-recalls on dense text.
3. **Merge and summarise elements.** The same entity appears in many chunks with different descriptions; they
   are grouped by name and type, and an LLM writes one consolidated description. This is a crude form of
   entity resolution, and name variants ("IBM" vs "International Business Machines") remain a known weak
   spot.
4. **Build the graph:** nodes are entities, edges are relationships, weighted by how many times the
   relationship was extracted across chunks (some versions also fold in the LLM's strength score).
5. **Detect communities** hierarchically with the Leiden algorithm, which partitions the graph into
   densely connected groups and recursively subdivides them. Leiden is used *because* it guarantees
   well-connected communities, which the older Louvain method does not.
6. **Summarise communities.** For each community at each level, an LLM writes a report covering its key
   entities, relationships and findings. Lower levels are fine-grained; the root level gives a handful of
   broad themes. These reports are what make global questions answerable cheaply.

### Query modes
- **Global search** answers whole-corpus questions by map-reduce over community reports at a chosen level.
  Map: each report (or batch) produces a partial answer plus a helpfulness score. Reduce: the highest-scoring
  partial answers are packed into the context and synthesised. Choosing a higher level is cheaper and
  broader; a lower level is more detailed and costs more calls. The map step reads every report at the
  chosen level whatever the question, so cost scales with the number of communities, not with relevance;
  newer versions can first rate reports for relevance and skip whole branches of the hierarchy that do
  not matter to the query.
- **Local search** answers questions about specific entities. It finds entities whose descriptions match the
  query (by embedding), then fans out to their relationships, neighbouring entities, the community reports
  they belong to and the original source chunks, ranks all of that, and fits it into a context budget. This
  is where multi-hop connection pays off.
- Hybrid variants combine the two. Microsoft's DRIFT search, for example, starts from the community
  reports most relevant to the query to draft a broad answer and follow-up questions, then refines those
  with local search over entities and source chunks.

### Variants that move the cost
The expensive part is LLM extraction and summarisation of everything at index time, so most variants
move or shrink it. LazyGraphRAG (Microsoft) builds its graph from cheap NLP noun-phrase co-occurrence
instead of LLM extraction and defers all LLM summarisation to query time, which makes indexing roughly
as cheap as vector RAG and pays per query instead; it fits corpora that change often or are queried
rarely. LightRAG and similar systems keep LLM extraction but skip the community hierarchy, retrieving
over entity-level and theme-level keys and merging new documents into the graph incrementally rather
than re-clustering. The choice is the same in every case: pay once at index time when many queries need
the corpus-level view, pay at query time when they do not.

### Why each piece is there
Extraction converts unstructured text into joinable keys (entities). Communities give the corpus a
table of contents that no chunk contains. Pre-written summaries turn an O(corpus) read per query into an
O(communities) read. And keeping links back to source chunks preserves grounding: a community report is a
model-written paraphrase, so answers should still cite original text where possible.

## Example
An illustrative cost walkthrough (orders of magnitude, not benchmarks) for a corpus of 2,000 internal
incident reports totalling about 4 million tokens.

**Indexing.** With 1,200-token chunks there are roughly 3,300 chunks. One extraction call per chunk plus one
gleaning pass is about 6,600 calls, each reading a chunk plus a long extraction prompt and writing structured
output: on the order of 15 to 25 million tokens processed. Entity merging and community summarisation add
thousands more calls. Plain vector RAG over the same corpus costs one embedding pass of 4 million tokens.
So GraphRAG processes several times more tokens, and every one of them goes through a generative LLM,
which is typically one to three orders of magnitude more expensive per token than an embedding model
(output tokens more still). The indexing bill is therefore commonly two or more orders of magnitude larger
in dollars, with the exact multiple set by the extraction model you choose, and parts of it must be rerun
as documents change.

**Global query:** "What are the most common root causes across all incidents?" Vector RAG retrieves 10
chunks, about 0.3% of the corpus, and reports whatever those happen to say. GraphRAG uses, say, the 40
level-1 community reports: 40 map calls produce scored partial answers ("deployment config drift, seen in
communities about service X and Y"), and one reduce call synthesises the top-scoring ones. Fewer than 50
calls give an answer grounded in a summary of the whole corpus. The index-free way to get the same
coverage, map-reduce over every chunk at query time, is about 3,300 calls per question; the community
reports are that reading done once and reused, which is the whole economic argument.

**Local query:** "Which teams were involved in incidents touching the payments database?" Local search
matches the entity PAYMENTS-DB, follows its edges to incidents and from incidents to TEAM entities, and
pulls the source chunks for those edges, joining facts that never co-occur in one chunk.

**Decision:** if most traffic is lookup questions ("what is the on-call rotation for X?"), the indexing bill
buys little; if analysts ask thematic and relational questions daily, it is repaid.

## Trade-offs
- Corpus-wide and multi-hop answers become possible, but indexing costs far more than embedding; the
  balance flips toward plain RAG when queries are mostly specific lookups answerable from one chunk.
- Pre-computed community summaries make global queries cheap, but they are frozen interpretations; when the
  corpus changes daily, recomputation cost and staleness can outweigh the benefit.
- LLM extraction needs no hand-built schema, but produces noisy, inconsistent entity types and duplicate
  nodes; when the domain has a stable ontology, a curated knowledge graph is more precise.
- Summaries compress well for themes, but add a paraphrase layer between source and answer; for
  audit-heavy use, require answers to cite underlying chunks rather than community reports.
- Higher community levels are cheaper and broader per global query, while lower levels give detail at
  more map calls; the right level depends on whether users want an overview or specifics.

## Pitfalls
- Adopting GraphRAG for fact lookup; the symptom is a large indexing bill and answer quality no better than
  hybrid search with reranking on the eval set.
- Skipping entity resolution, so one real entity becomes several nodes; local search returns partial
  neighbourhoods and communities split along spelling variants.
- Treating extraction as done once; documents are added and edited, and the graph silently drifts from the
  source, visible as answers that omit recent incidents.
- Citing community reports as evidence; reviewers cannot find the claimed statement in any source document
  because it was synthesised by the indexing model.
- Evaluating only with a judge on "comprehensiveness"; broad, fluent answers win those comparisons even
  when specific facts are wrong, so add fact-level checks.
- Building one graph and one set of community reports over documents with different permissions; the
  symptom is a theme summary that mentions a restricted project to users who could not open any of its
  source documents, because a report inherits the content of every chunk it summarises.

## When to use
Reach for GraphRAG when a meaningful share of questions is thematic ("what are the main issues"),
comparative across the corpus, or relational across documents, and the corpus is stable enough that
indexing cost amortises. Prototype on a representative slice first: measure indexing cost per thousand
documents, then compare against tuned hybrid retrieval with reranking and query decomposition on a labelled
question set split by question type. If the gains appear only on global questions, consider routing: send
those to GraphRAG and the rest to plain RAG. In interviews, explain the indexing-for-query trade and when
you would not use it.

## Role lens
- **Engineer:** Tunes chunk size, extraction prompts and entity types, runs incremental re-indexing, and
  adds citation links from community reports back to source chunks.
- **Architect:** Decides whether the corpus and query mix justify the indexing cost, owns the refresh
  strategy, and designs routing between global, local and plain retrieval.
- **FDE:** Identifies early whether the customer's questions are really thematic, and runs a scoped pilot
  with a cost estimate before committing to indexing their whole archive.

## Check yourself
**Q:** A customer wants GraphRAG over 50,000 frequently updated support tickets so agents can look up past
solutions. Would you recommend it?
**A:** Probably not as the main path. Agents mostly ask lookup questions ("has anyone seen error X with
product Y?"), which hybrid search with reranking handles well, and the corpus changes daily, so expensive
extraction and community summaries would be stale or constantly rebuilt. GraphRAG's advantage is global and
relational questions. If product managers also want "what are the top recurring problems this quarter", run
a periodic GraphRAG or clustering job over a snapshot for that use, route those questions to it, and keep
plain retrieval for lookups. Validate with an eval split by question type before committing spend.

## Quiz
### Q: What lets GraphRAG answer "what are the main themes in this corpus?" when vector RAG cannot?
- [ ] It embeds the query with a larger embedding model
- [x] Pre-computed community summaries over the whole entity graph, combined by map-reduce at query time
- [ ] It retrieves many more chunks per query
- [ ] It fine-tunes the model on the corpus during indexing
> No single chunk is similar to a themes question, so top-k fails. Community reports summarise the entire
> corpus ahead of time, and global search map-reduces over them, reading summaries instead of every chunk.

### Q: Where does most of GraphRAG's extra cost come from compared with plain vector RAG?
- [ ] Storing the graph in a graph database
- [ ] Running Leiden community detection
- [x] LLM calls at index time for extraction and summarisation across the whole corpus
- [ ] Larger embedding vectors for entities
> Community detection and storage are cheap. The dominant cost is reading every chunk with an LLM to
> extract entities and relationships, plus generating element and community summaries.

### Q: Which query type benefits most from GraphRAG's local search rather than plain vector search?
- [ ] A question whose answer is a verbatim sentence in one document
- [ ] A question about the current date
- [x] A question requiring facts about related entities that appear in different documents
- [ ] A question answered by a keyword match on a product code
> Local search starts at matched entities and follows relationships to neighbours and their source chunks,
> joining facts that never co-occur. Single-chunk and keyword lookups are served as well by hybrid search.
