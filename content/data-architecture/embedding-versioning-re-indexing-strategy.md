---
id: embedding-versioning-re-indexing-strategy
module: data-architecture
name: Embedding versioning / re-indexing strategy
kind: pattern
difficulty: core
est_minutes: 18
status: typed
lesson_type: decision
agent_relevance: core
related:
  - rag-retrieval/embedding-models
  - rag-retrieval/embedding-pipelines
  - rag-retrieval/embedding-dimension-and-normalization
  - rag-retrieval/freshness-document-deletion-index-consistency
  - data-architecture/vector-database-architecture
  - data-architecture/vector-store-sizing
  - data-architecture/etl-elt-for-rag
  - evals/retrieval-evaluation
sources:
  - Kusupati et al., 2022 — Matryoshka Representation Learning
  - Official docs: pgvector
  - Official docs: Qdrant
---

## TL;DR
Your agent's retrieval tool only works if queries and stored vectors share one recipe (model, settings,
chunker). Version the recipe, build the new index beside the old, evaluate the agent, swap atomically,
and hash content to re-embed only changed chunks.

## For agent builders
- **Why it matters for agents.** A RAG agent or a memory tool calls `search` dozens of times per task.
  If the index and the query embedder drift apart, the tool still returns results, the agent still cites
  them confidently, and nothing errors. Answer quality silently falls to near-random retrieval.
- **Where it shows up in your stack.** The embedding call inside your retrieval tool, the vector store
  collection or table it searches (Qdrant aliases, OpenSearch aliases, a pgvector view), the long-term
  memory store of a memory-enabled agent, and the nightly ingestion job that feeds them.
- **What you control.** The recipe hash stamped on every vector, the alias the tool resolves at call
  time, the eval gate (retrieval metrics plus agent task success) before a swap, and the content-hash
  cache that keeps routine runs cheap.
- **What you can skip.** The internals of HNSW and IVF training. Know which parameters are fixed at build
  time and which are query-time knobs; that is enough to decide re-embed versus re-index.

## Decision case
### Scenario
A SaaS company runs a support agent whose `search_docs` tool searches 6 million chunks (about 500 tokens
each, 3 billion tokens) in a single pgvector cluster. The vendor is retiring the 1536-dimension embedding
model, so the team must move to a newer 1024-dimension model.

Constraints (figures illustrative):
- **Deadline.** The old model stops answering in 6 weeks; after that no new chunk can be embedded under v6.
- **No mixed-space window.** The agent serves about 40,000 searches a day and must never search two spaces.
- **Throughput.** A shared rate limit of 1 million tokens per minute, so a full re-embed takes 3,000 minutes,
  about 50 hours.
- **Change rate.** About 2% of chunks (120,000, 60 million tokens) change per day.
- **Memory.** Nodes have 96 GB of RAM. Raw vectors are 6M × 1536 × 4 bytes ≈ 36.9 GB today and
  6M × 1024 × 4 bytes ≈ 24.6 GB after, plus under 1 GB of HNSW links per index at `m = 16`.
- **Team.** Two engineers own retrieval; the ingestion service belongs to another team with a 3-week
  release queue. Source tables carry a reliable `updated_at` and a deletes log.

### Options
| Option | How it works | Mixed-space window | Deadline | Memory peak | Ingestion change |
|---|---|---|---|---|---|
| **A. Re-embed in place** | Overwrite vectors as the job runs | About 50 h, no rollback | Yes | One copy | None, but the column changes dimension |
| **B. Blue-green with dual-write** | Build `docs_v7` beside `docs_v6`; ingestion embeds new chunks under both | None | Tight: waits for the 3-week release queue | ≈ 63 GB, fits | Yes, owned by another team |
| **C. Blue-green with watermark replay** | Build `docs_v7` beside; record a start watermark; replay changes since | None | Yes: about 2 days build plus catch-up | ≈ 63 GB, fits | None |
| **D. Adapter now, rebuild later** | Map new-model queries into the v6 space with a learned adapter | None, but lower quality | Fails: new chunks cannot be embedded under v6 after retirement | One copy | None |

### Decision
Choose **C**, blue-green with watermark replay, gated on evals:
- **It is the only option with no mixed window that the retrieval team can ship alone.** A searches two
  spaces for about 50 hours, and B depends on another team's release queue.
- **Memory fits.** 36.9 + 24.6 GB of vectors plus two graphs is about 63 GB on a 96 GB node, leaving
  headroom for the HNSW build.
- **The catch-up is small.** 50 hours of edits is about 250,000 chunks, 125 million tokens, so roughly
  2 hours of replay, then a short second pass to close the gap right before the swap. Documents edited
  several times are embedded once in their final state.
- **D does not solve the problem.** After retirement, new documents still need old-space vectors.
- **The swap is gated.** Swap only if recall@5 and MRR on the 400-question eval set are at least as good,
  and 100 replayed agent tasks hold task success and tool calls per task.

What would change it:
- **No trustworthy watermark or deletes log.** Then replay can miss edits, so take B and wait for the
  ingestion release.
- **Less memory headroom** (for example 48 GB nodes). Then add a node for the build window, or build on a
  separate cluster and move the agent's connection string instead of a view.
- **The new model loses the eval.** Keep v6 live, test another model or chunker before the deadline, and use
  an adapter only as a short bridge that passes the same gate.

### Trade-offs accepted
- **Double storage for the soak week.** Watch node memory and disk; delete `docs_v6` once the week is clean.
- **A short lag before the swap.** The new collection trails live edits until the final pass; watch the
  count of rows with `updated_at` after the watermark and swap only when it is near zero.
- **Rate limit shared with nightly ingestion.** The 50-hour job slows routine runs; watch nightly job
  duration and pause the backfill if it would overrun.
- **Query-side cutover risk.** The tool must switch embedder and cache key with the alias; watch
  `embedding_version_mismatch` errors, which should stay at zero.

### Review rubric
- **The recipe change is a new version.** The write-up hashes model, revision, dimension and chunker into a
  new `embedding_version` and says the query side switches with the alias.
- **Duration and memory are computed.** It derives build time from tokens and throughput, and peak memory
  from both copies, with the arithmetic shown.
- **Catch-up is explicit.** It picks dual-write or replay with a reason and closes the gap before the swap.
- **The swap is gated on evals.** It names retrieval metrics and agent task metrics with a pass threshold.
- **Rollback and retirement are planned.** It keeps the old collection for a stated soak and names the
  signal that triggers a rollback.
- **Reversal conditions are concrete.** It names the fact (watermark, memory, eval result) that would change
  the option.

## Why it exists
- **The index silently depends on three things.** Which model turned text into vectors; how it was called
  (dimension, normalization, instruction prefix, input truncation); and how documents were cut into chunks.
  None of those appear in the vector itself, and none appear in the tool result the agent reads.
- **Mixing models fails silently.** A query embedded with model B against an index built with model A is
  not "slightly worse". If dimensions differ, the store at least rejects the query. If they happen to
  match, cosine similarity across spaces is noise, so retrieval degrades to near-random while the pipeline
  reports no error at all, and the agent grounds its answer on irrelevant chunks.
- **Change is forced on you.** Embedding models improve and vendors deprecate old ones; you will want
  longer context, a multilingual or a cheaper model; you will tune the chunker; you may quantize vectors to
  save memory. Each of those invalidates every stored vector, including an agent's long-term memories.
- **"Just re-embed in place" fails three ways.** During the hours or days the job runs, live agent
  queries hit a mix of two spaces. If the new model is worse on your eval set, there is nothing to roll
  back to. And it pays full cost to re-embed unchanged content, when usually only a small fraction
  changed since the last run.
- **What versioning buys.** Versioning the index and swapping it as a unit removes all three problems.

## How it works
### What makes up an embedding version
Treat the index as the product of a recipe and give the recipe a version identifier:

```
embedding_version = hash(model_id, model_revision, dimension, normalization,
                         instruction_prefix, max_input_tokens, chunker_id, chunker_params)
```

- **Store it on every vector row and on the collection,** *because* the only reliable compatibility check
  is to compute the query-side recipe and compare. The retrieval tool reads the collection's version and
  refuses, or routes elsewhere, on mismatch.
- **Pin the model *revision*, not just its name.** Hosted models can be silently updated, and open-weight
  models on a hub have commit hashes.
- **Chunker parameters belong in the recipe,** because retrieval granularity changes what a vector means. A
  512-token chunk and a 128-token chunk of the same paragraph are different items even under the same
  model, and the citation offsets your agent returns to users change with them.

### Re-embedding versus re-indexing
Two operations hide behind the word "re-index", and they cost very different amounts.

| Operation | Trigger | What is rebuilt | Typical duration |
|---|---|---|---|
| *Re-embedding* | the recipe changed | new vectors, then the ANN structure | days of API throughput |
| *Re-indexing* | index parameters, data drift, churn | only the ANN structure over existing vectors | hours of index build |

Re-indexing is needed even when the model never changes:
- **Parameter changes.** HNSW's `M` and `ef_construction` are fixed when the graph is built, so raising them
  to buy recall means a rebuild. `ef_search` is a query-time knob and needs no rebuild, which is why it is
  the first thing to tune.
- **Centroid staleness.** IVF-style indexes assign each vector to the nearest of a set of centroids learned
  from the data present at build time. pgvector's documentation advises creating an IVFFlat index only after
  the table holds data for this reason. As the corpus grows into topics the original sample did not cover,
  lists become unbalanced and recall at a fixed `nprobe` falls, so the centroids must be retrained.
- **Accumulated deletes and updates.** Many implementations mark deleted vectors rather than removing them,
  so a heavily churned index (agent memory stores churn a lot) carries dead entries that cost memory and
  can weaken graph connectivity until compaction or rebuild.
- **Same swap, no embedding calls.** Re-indexing uses the same blue-green swap but skips the embedding
  calls. Keep two version counters (`embedding_version` for the vectors, an index build id for the
  structure) so you can tell which one moved when recall changes.

### The query side is part of the version
- **The alias resolves to a pair:** the collection and the recipe that built it.
- **The retrieval tool embeds with exactly that recipe** (model, prefix, dimension), *because* swapping the
  collection while the query embedder stays on the old model recreates the cross-space mismatch in reverse.
- **Key query-embedding caches on `(query_text, embedding_version)`,** never on text alone. Agents often
  re-issue the exact same search strings, so these caches get high hit rates and, after a swap, serve
  old-space vectors widely.
- **Adapters are a temporary bridge only.** A learned mapping from the new model's space into the old one
  (a linear or small adapter trained on pairs of embeddings of the same texts) lets a new query model run
  against the old index before the rebuild finishes. It recovers only part of the quality, so it must pass
  the same eval gate and is not an alternative to rebuilding.

### Content hashing to avoid re-embedding the unchanged
1. Give each chunk a stable key: `chunk_key = hash(source_id, chunk_text)`.
2. Keep a table mapping `(chunk_key, embedding_version) -> vector`.
3. On each ingestion run, chunk the source, compute keys and skip any key already embedded under the
   current version.
4. Chunks whose text changed get new keys and are embedded; keys that disappear are deleted.

- **Why it is safe:** embedding is, for practical purposes, a pure function of (text, recipe). The same
  inputs yield the same vector up to small floating-point noise from batching and hardware.
- **Version changes are full misses:** every key misses and you pay for a full rebuild, which is correct.

### Blue-green indexes and the alias swap
1. **Build beside.** Build the new version into a separate collection or table (`docs_v7` beside
   `docs_v6`) while the old one keeps serving the agent.
2. **Resolve through a pointer.** Most stores support an alias the application resolves at query time:
   Qdrant collection aliases, OpenSearch index aliases; in pgvector a view or a config row naming the live
   table.
3. **Repoint in one operation** when the build finishes and passes evaluation. Agent tool calls never see
   a mixed index.
4. **Roll back** by repointing the alias back; delete the old collection after a soak period.

- **The price:** temporarily holding two copies of vectors and two ANN graphs, which must be planned into
  memory and disk sizing.

### Catching up on writes during the build
Documents (and agent memories) keep changing while the rebuild runs.

| | Dual-write | Replay |
|---|---|---|
| How | ingestion embeds every new chunk under both versions until the swap | the build records a start watermark, then re-processes everything changed since |
| Embedding cost | each changed chunk embedded once per version | same, slightly cheaper when a document changes several times in the window, because only its final state is embedded |
| New collection currency | current at every moment | short lag to close just before the swap |
| Operational risk | backfill and live writes race on the same collection: a backfill must not overwrite a newer live write, and deletes must reach both | needs a reliable change watermark and a final catch-up pass |

### Evaluate before you swap
- **A new model is not better by assumption.** Run the retrieval eval set (queries with known relevant
  chunks) against both collections and compare recall@k and MRR.
- **Also check agent answers on a sample,** because a model that retrieves different-but-also-relevant
  chunks can shift answers, tool-call counts and citations in ways retrieval metrics miss.
- **The swap is gated on this comparison,** which is why the build must be separate.

### Dimension and quantization changes
- **These are new embedding versions too.** Truncating a Matryoshka-trained model to fewer dimensions, or
  changing storage precision (float32 to int8 or binary), changes the stored representation and the ANN
  index parameters, even if the model is the same.
- **Rebuild rather than convert in place, and re-run the eval,** because quantization costs recall that must
  be measured, not assumed.

### Cost and duration (illustrative arithmetic)
| Quantity | Value |
|---|---|
| Corpus | 10 million chunks averaging 400 tokens = 4 billion tokens |
| Cost at an illustrative few cents per million tokens | on the order of a hundred dollars, rarely the problem |
| Time at an illustrative rate limit of a million tokens per minute | roughly 67 hours of embedding calls, before HNSW build time |

That duration is what makes dual-write or replay necessary, and makes content hashing worth having for the
routine runs in between.

## Diagram
A recipe change never touches the collection the agent searches: the new one is built beside it, caught
up, and only promoted if it wins the eval.

```mermaid
flowchart TD
    R["Recipe change: model, dim, chunker"] --> V["New embedding_version hash"]
    V --> B["Build docs_v7 beside docs_v6"]
    L["Live writes"] -->|"dual-write or replay"| B
    B --> E{"Eval: recall at k, agent answers"}
    E -->|"worse"| X["Discard v7, keep v6 live"]
    E -->|"at least as good"| S["Repoint alias docs to v7"]
    S --> Q["Retrieval tool embeds with v7 recipe"]
    S --> K["Soak period"]
    K -->|"regression"| RB["Repoint alias back to v6"]
    K -->|"stable"| D["Delete docs_v6"]
```

## Examples
### A retrieval tool that fails loud on a recipe mismatch
The agent's `search_docs` tool reads the alias target and its recipe, and returns a structured error
instead of silently searching the wrong space. The store is a dict so the logic is visible.

```python
import hashlib, json

def recipe_hash(r: dict) -> str:
    return hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()[:12]

QUERY_RECIPE = {"model": "text-embedding-3-small", "revision": "2024-01", "dim": 1536,
                "normalize": True, "chunker": "recursive", "chunk_tokens": 400, "overlap": 40}
alias = {"docs": "docs_v7"}
collections = {"docs_v7": {"emb_version": recipe_hash(QUERY_RECIPE), "rows": []}}

def search_docs(query: str, k: int = 5) -> dict:
    """Tool exposed to the agent. Returns results or an error the agent can report."""
    target = collections[alias["docs"]]
    if target["emb_version"] != recipe_hash(QUERY_RECIPE):
        # never search across spaces; surface it so the agent says "retrieval unavailable"
        return {"error": "embedding_version_mismatch", "collection": alias["docs"]}
    return {"collection": alias["docs"], "emb_version": target["emb_version"], "results": []}

print(search_docs("refund window"))                  # ok: versions match
QUERY_RECIPE["model"] = "newer-embedding-model"      # someone upgrades the query side only
print(search_docs("refund window"))                  # {'error': 'embedding_version_mismatch', ...}
```

Log `emb_version` with every tool call in the agent trace, so a bad answer can be traced to the index
version it was grounded on.

### Recipe hash, content-keyed cache and alias swap
A minimal version of the three mechanisms. The vector store is abstracted as a dict of collections so the
logic is visible.

```python
import hashlib, json

# "revision" is your own pin; hosted APIs may not expose one, so record the date you validated it
RECIPE = {"model": "text-embedding-3-small", "revision": "2024-01", "dim": 1536,
          "normalize": True, "chunker": "recursive", "chunk_tokens": 400, "overlap": 40}
EMB_VERSION = hashlib.sha256(json.dumps(RECIPE, sort_keys=True).encode()).hexdigest()[:12]

def chunk_key(source_id: str, text: str) -> str:
    return hashlib.sha256(f"{source_id}\x00{text}".encode()).hexdigest()[:16]

collections = {}                 # name -> {chunk_key: (vector, meta)}
alias = {"docs": "docs_v6"}      # what queries resolve against
cache = {}                       # (chunk_key, EMB_VERSION) -> vector

def embed(text):                 # stand-in for the model call
    return [float(b) for b in hashlib.md5(text.encode()).digest()[:4]]

def build(target: str, sources: dict[str, list[str]]):
    col = collections.setdefault(target, {})
    seen, calls = set(), 0
    for sid, chunks in sources.items():
        for text in chunks:
            k = chunk_key(sid, text); seen.add(k)
            if (k, EMB_VERSION) not in cache:
                cache[(k, EMB_VERSION)] = embed(text); calls += 1
            col[k] = (cache[(k, EMB_VERSION)], {"source": sid, "emb_version": EMB_VERSION})
    for k in set(col) - seen:    # chunks that vanished upstream
        del col[k]
    return calls

docs = {"kb/1": ["Refunds take 5 days.", "Contact support via chat."]}
print("first build, calls:", build("docs_v7", docs))          # 2
docs["kb/1"][0] = "Refunds take 3 days."
print("incremental, calls:", build("docs_v7", docs))          # 1 (only the changed chunk)

def swap(name, new_target):      # gate this on the eval comparison in real life
    old, alias[name] = alias.get(name), new_target
    return old
print("retired:", swap("docs", "docs_v7"), "live:", alias["docs"])
```

The eval gate is deliberately a comment: in production `swap` runs only after recall@k on the eval set for
`docs_v7` is at least as good as for `docs_v6`, and the agent eval suite passes against it.

### Worked scenario: full rebuild versus routine run
Using the illustrative rate limit above (a million tokens per minute) on the 10-million-chunk corpus
behind a support agent's knowledge tool.

| Run | Chunks embedded | Tokens | Embedding time |
|---|---|---|---|
| Model upgrade (every key misses) | 10,000,000 | 4 billion | ~4,000 min, about 67 h |
| Nightly run, 1% of chunks changed (illustrative) | 100,000 | 40 million | ~40 min |
| Nightly run without content hashing | 10,000,000 | 4 billion | ~67 h, longer than a day, so it overruns the next run |

- **Reading it:** content hashing turns the nightly job from impossible into minutes, so the agent sees
  yesterday's policy changes today; the full rebuild is rare but long enough that it needs dual-write or
  replay.

### Production snippet: blue-green in pgvector with a view as the alias
The retrieval tool queries `docs`; the swap is a single statement, and rollback is the same statement
naming the old table.

```sql
-- build v7 beside v6, index it without blocking writes
CREATE TABLE docs_v7 (LIKE docs_v6 INCLUDING ALL);
-- ... backfill embeddings into docs_v7, then:
CREATE INDEX CONCURRENTLY docs_v7_hnsw ON docs_v7
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- after the eval gate passes: atomic repoint
CREATE OR REPLACE VIEW docs AS SELECT * FROM docs_v7;
```

## Production patterns
- **Recipe-stamped collections with a fail-loud retrieval tool.** Use from the first production index.
  Store `embedding_version` on the collection and each row; inside the tool, compare it with the
  embedder's recipe hash and return an error instead of searching. Monitor mismatch errors (should be
  zero) and which version each agent tool call was served from.
- **Alias-based blue-green swap.** Use for any corpus that takes more than minutes to rebuild. Qdrant
  collection aliases, OpenSearch's `_aliases` endpoint with remove and add actions in one request, or a
  Postgres view repointed with `CREATE OR REPLACE VIEW`. Monitor recall@k on the eval set before and after
  the swap, plus p95 latency of the retrieval tool for the first hour.
- **Agent eval in the swap gate.** Beyond recall@k and MRR, replay a fixed set of agent tasks against the
  new alias and compare task success, citation accuracy and tool calls per task. A new index that makes
  the agent search three times instead of once is a regression even if recall looks flat.
- **Content-hash cache table for incremental runs.** Use once ingestion runs on a schedule. Keep a table
  keyed on `(chunk_key, embedding_version)` and skip hits. Monitor fraction of chunks re-embedded per run:
  near zero normally, 100% only on version changes. A sudden jump with no recipe change usually means the
  chunker or text extraction changed.
- **Tune query-time recall before rebuilding.** When recall dips, raise `hnsw.ef_search` (pgvector session
  setting) or the IVF probe count first, because they need no rebuild. Rebuild with higher `M` or
  `ef_construction` only if that cannot reach the target within the latency budget.
- **Weekly fixed-eval recall tracking and a retirement window.** Run the same eval set against the live
  alias on a schedule; a slow decline with no recipe change signals stale centroids or accumulated deletes,
  so schedule a re-index, not a re-embed. Keep the previous collection for an explicit soak (for example a
  week, set by your rollback needs), then delete it, watching disk and memory headroom.

## Trade-offs
- **Atomic cutover vs double memory.** Blue-green rebuilds give atomic cutover and instant rollback but
  double vector and index memory during the build. If the corpus barely fits one GPU or one node, you need
  a staged or tiered rebuild instead.
- **Cheap routine runs vs edit blast radius.** Content hashing makes routine runs cheap, but a hash on exact
  text means a one-character edit re-embeds the chunk and its overlapping neighbours. With fixed-size
  chunking an insertion shifts every later boundary in that document, so all following chunks re-embed too.
  Structure-aware chunking that cuts on headings or paragraphs keeps the blast radius local.
- **Pinned revision vs owning upgrades.** Pinning a model revision protects consistency but means you
  consciously own upgrades. Letting a vendor update silently is cheaper to operate right up until your
  agent's answers shift with no deploy to blame.
- **Dual-write vs replay.** Dual-write keeps the new index current with no catch-up step but couples live
  ingestion to the new collection and makes it race the backfill. Replay keeps ingestion untouched at the
  cost of a catch-up pass, and tends to win when builds take days and the change watermark is trustworthy.
- **Rollback safety vs storage.** Keeping old versions for rollback is safe, but each held version is
  storage and an ANN graph; set an explicit retirement window rather than letting versions accumulate.

## Pitfalls
- **Upgrading the query-side model without rebuilding the index.** Symptom: the agent cites plausible-looking
  but irrelevant chunks with no errors, and eval recall collapses overnight. Fix: the retrieval tool reads
  the recipe from the alias target.
- **Changing the chunker and reusing the old vectors** because "the model is the same". Symptom: citations
  pointing at wrong offsets and duplicated or truncated passages in context. Fix: chunker params are in the
  recipe hash.
- **Re-embedding in place against the live collection.** Symptom: a window of mixed-space results and user
  complaints that correlate with the ingestion job's schedule. Fix: build beside and swap.
- **Swapping on build completion rather than on eval results.** Symptom: a regression discovered by users,
  with the old index already deleted. Fix: gate the swap on the eval and keep a soak window.
- **Forgetting that filters and metadata live beside vectors.** A rebuild that drops ACL or tenant fields
  produces an index that passes retrieval eval and lets an agent retrieve another tenant's documents. Fix:
  diff schemas and test a filtered query before the swap.
- **Sizing memory for one copy of the index.** Symptom: out-of-memory or evictions precisely during the
  rebuild, the moment you can least afford it. Fix: plan for two.
- **Caching query embeddings keyed on query text alone.** Symptom after a swap: popular (cached) queries
  retrieve garbage while rare, uncached queries work fine. Fix: key on `(query_text, embedding_version)`.
- **Treating every recall drop as a model problem.** When recall decays slowly with no recipe change, the
  cause is usually stale IVF centroids or accumulated deletes, and a re-index (no re-embedding) fixes it.

## When to use
- **Version from the first production index,** including agent memory stores, because the first model
  upgrade arrives sooner than planned and the retrofit is painful.
- **Use the alias swap** for any corpus that takes longer than a few minutes to rebuild; below that, a
  rebuild-and-replace in a transaction is fine.
- **Add content hashing** as soon as ingestion runs on a schedule rather than once; it pays for itself.
- **Measure four things:** fraction of chunks re-embedded per run (small except on version changes); build
  duration versus the rate of upstream change (decides dual-write versus replay); eval recall before and
  after every swap; and recall on a fixed eval set tracked weekly between swaps, since a slow decline with
  no recipe change is the signal to re-index rather than re-embed.
- **Interviews:** the expected answer to "how do you upgrade the embedding model in production" is this
  exact sequence: version the recipe, build beside, evaluate, swap the alias, retire later.

## Role lens
- **Engineer:** Stamps every vector with the recipe hash, writes the content-keyed embedding cache, and
  wires the alias lookup into the query path so mismatches fail loudly.
- **Architect:** Owns the capacity plan that includes two live indexes during rebuilds and the policy for
  how many versions are retained and for how long.
- **FDE:** Explains to a customer why a "quick model upgrade" is a multi-day rebuild with an eval gate,
  and designs the replay window around their document change rate.

## Check yourself
**Q:** Your team wants to move a 20-million-chunk index from a 1536-dimension model to a newer
1024-dimension model. Walk through the plan and the two biggest risks.
**A:** First, define the new recipe (model id, pinned revision, 1024 dims, normalization, same chunker if
chunking is unchanged) and hash it as a new embedding version. Build it into a new collection while the
old serves; estimate duration from token count divided by the embedding throughput limit, and because
that is likely days, either dual-write new chunks under both versions or record a watermark and replay
changes afterward. Run the retrieval eval set against both collections and compare recall@k and MRR,
plus a sample of end-to-end answers. Swap the alias only if the new one is at least as good, keep the old
collection for a rollback window, then delete it. The two biggest risks are memory: both collections and
both ANN graphs coexist, so the cluster must have headroom; and silent quality regression: the new model
may retrieve differently on your domain, which only the eval gate catches.

## Interview questions
### Why can't you just point ingestion at the new embedding model and overwrite vectors in place?
- **Mixed spaces during the run.** For hours or days the index holds vectors from two unrelated spaces, so
  live queries get near-random results with no error.
- **No rollback.** If the new model is worse on your eval set, the old vectors are already gone.
- **Wasted cost.** It re-embeds unchanged content; building beside, gating on eval and swapping an alias
  fixes all three.

### Your RAG agent started citing irrelevant documents this morning, with no errors anywhere. How do you debug it?
- **Check versions first.** Compare the `emb_version` logged on the agent's retrieval tool calls with the
  query embedder's recipe hash; a query-side model upgrade or an unpinned vendor update is the usual cause.
- **Check the swap log.** If an alias moved overnight, compare the eval results that gated it and roll the
  alias back while you investigate.
- **Check caches and filters.** Query-embedding caches keyed on text alone, or a rebuild that dropped
  metadata fields, produce the same symptom.

### Recall has drifted down 6 points over three months and nobody changed the model. How do you debug it?
- **Rule out a recipe change first:** compare the stored `embedding_version` with the query embedder's, and
  check whether a vendor silently updated an unpinned model.
- **Then suspect the index structure:** stale IVF centroids as the corpus grew into new topics, or
  accumulated deletes in a churned HNSW graph.
- **Try the cheap knob, then re-index:** raise `ef_search` or probes; if that recovers recall within the
  latency budget, schedule a rebuild of the structure, with no re-embedding.

### Dual-write or watermark replay for a five-day rebuild: which do you pick and why?
- **Replay** if the change watermark is trustworthy: ingestion stays untouched, and documents edited several
  times are embedded once in their final state.
- **Dual-write** if the new collection must be current at every moment, accepting that backfill must not
  overwrite newer live writes and deletes must reach both versions.
- **Either way** close the gap before the alias swap, never after.

### How do you estimate how long a re-embed will take, and what dominates?
- **Tokens divided by throughput.** 10 million chunks at 400 tokens is 4 billion tokens; at an illustrative
  million tokens per minute that is roughly 67 hours.
- **Throughput, not price, is the constraint:** at a few cents per million tokens the bill is around a
  hundred dollars. Add HNSW build time on top.

### What has to change in the agent's retrieval tool when you swap the index?
- **The query embedder:** the alias must resolve to collection plus recipe, so queries use the same model,
  prefix and dimension.
- **Caches:** query-embedding caches keyed on text alone serve old-space vectors for popular queries; key
  them on `(query_text, embedding_version)`.
- **Traces:** log the served version on every tool call so eval regressions can be attributed to a swap.

## Quiz
### Q: Why can't you query an index built with embedding model A using vectors from model B?
- [ ] Model B produces vectors that are too large to compare efficiently
- [x] The two models define unrelated vector spaces, so similarity between their vectors is meaningless
- [ ] Model B normalizes vectors differently, which the index can correct for at query time
- [ ] The ANN index stores the model name and rejects the query
> Each model learns its own geometry; the same text lands at unrelated points. Cosine similarity across
> spaces is noise, which is why retrieval degrades to near-random, with no error when the dimensions
> happen to match. Indexes check dimension at most, never which model produced a vector, unless you store
> and check the version yourself.

### Q: What is the main purpose of keying the embedding cache on both chunk content hash and embedding version?
- [ ] To allow the same vector to be reused across different embedding models
- [ ] To detect duplicate documents across sources
- [x] To skip re-embedding unchanged chunks on routine runs while forcing a full rebuild when the recipe changes
- [ ] To make vector lookups faster at query time
> Embedding is a pure function of (text, recipe). Keying on both means an unchanged chunk under the same
> recipe is a cache hit, and any change to model, dimension or chunker makes every key miss, which is the
> correct behaviour.

### Q: During a multi-day rebuild, documents keep changing. Which approach keeps the new index current without a mixed-space window?
- [ ] Pause ingestion until the rebuild finishes
- [ ] Write new chunks only to the old index and copy them over after the swap
- [x] Dual-write new chunks under both versions, or record a watermark and replay changes after the build
- [ ] Embed new chunks with whichever model is faster and tag them
> Dual-write or watermark-and-replay both deliver a complete new collection at swap time. Pausing ingestion
> leaves users with stale data for days; copying after the swap creates a gap; mixing models in one
> collection is exactly the failure versioning exists to prevent.

## Use cases & projects
### Use cases
- **Upgrading the embedding model behind a RAG agent.** Build beside, gate on retrieval and agent evals,
  swap the alias the retrieval tool resolves, keep the old one for rollback.
- **Re-embedding an agent's long-term memory store.** Memories written under an old recipe must move as a
  unit, or recall of past user preferences becomes random.
- **Nightly knowledge-base refresh for a support agent.** Content hashing re-embeds only the policies that
  changed, so the agent's answers stay current within a day.
- **Shrinking vector memory for a multi-tenant agent platform.** Matryoshka truncation or int8 quantization
  is a new version that must pass the same eval gate before cutover.

### Mini project
**Blue-green swap behind an agent's search tool (2-3 hours).**
- **Goal:** upgrade the embedding recipe of a small RAG agent with zero mixed-space queries and a measured
  go/no-go decision.
- **Steps:** index about 500 chunks under recipe v1 in pgvector or Qdrant; expose a `search_docs` tool that
  checks the recipe hash and logs `emb_version`; write 30 questions with known relevant chunks; build v2
  (a different model or chunk size) beside v1; run recall@5 and the agent on all 30 questions against both;
  swap the alias, then roll it back.
- **Done when:** a table compares v1 and v2 on recall@5 and agent answer accuracy, the trace shows every
  tool call tagged with its version, and a deliberate query-side-only upgrade makes the tool return a
  mismatch error instead of results.
