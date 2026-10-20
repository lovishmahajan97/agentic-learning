---
id: embedding-versioning-re-indexing-strategy
module: data-architecture
name: Embedding versioning / re-indexing strategy
kind: pattern
difficulty: core
est_minutes: 16
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
A vector is only meaningful relative to the model that produced it: two embedding models, or two
versions of one, put the same text at unrelated points in space. So the model, its settings and the
chunker are part of the index's identity, and changing any of them means re-embedding everything. The
strategy: version that identity explicitly, build the new index beside the old, evaluate, swap atomically,
and hash content so routine runs re-embed only changed chunks.

## Why it exists
Every RAG index silently depends on three things: which model turned text into vectors, how that model was
called (dimension, normalization, instruction prefix, input truncation) and how documents were cut into
chunks. None of those appear in the vector itself. A query embedded with model B against an index built
with model A does not return "slightly worse" results; cosine similarity between vectors from different
spaces is noise, so retrieval degrades to near-random while the pipeline reports no error at all.

This is forced on you, not optional. Embedding models improve and vendors deprecate old ones; you will
want longer context, a multilingual model, or a cheaper one; you will tune the chunker; you may quantize
vectors to save memory. Each of those invalidates every stored vector.

The naive alternative is "just re-embed in place": point the ingestion job at the new model and overwrite
rows. It fails three ways. During the hours or days the job runs, the index contains a mix of two spaces and
live queries hit both. If the new model turns out worse on your eval set, there is nothing to roll back to.
And it re-embeds unchanged content, paying full cost for a corpus where usually only a small fraction
changed since the last run. Versioning the index and swapping it as a unit removes all three problems.

## How it works
### What makes up an embedding version
Treat the index as a product of a recipe and give the recipe a version identifier:

```
embedding_version = hash(model_id, model_revision, dimension, normalization,
                         instruction_prefix, max_input_tokens, chunker_id, chunker_params)
```

Store this on every vector row and on the collection itself, *because* the only reliable way to know
whether a query is compatible with an index is to compute the query-side recipe and compare. The query
path reads the collection's version and refuses, or routes elsewhere, on mismatch. Model *revision*
matters, not just name: hosted models can be silently updated, and open-weight models on a hub have
commit hashes. Pin them.

Chunker parameters belong in the recipe because retrieval granularity changes what a vector means. A
512-token chunk and a 128-token chunk of the same paragraph are different items even under the same
model, and the citation offsets you return to users change with them.

### Content hashing to avoid re-embedding the unchanged
Give each chunk a stable key: `chunk_key = hash(source_id, chunk_text)`. Keep a table mapping
`(chunk_key, embedding_version) -> vector`. On each ingestion run, chunk the source, compute keys and
skip any key already embedded under the current version. Chunks whose text changed get new keys and are
embedded; keys that disappear are deleted. This works *because* embedding is a pure function of (text,
recipe): the same inputs always yield the same vector, so a cache keyed on both is safe. When the
embedding version changes, every key is a miss and you pay for a full rebuild, which is correct.

### Blue-green indexes and the alias swap
Build the new version into a separate collection or table (`docs_v7` beside `docs_v6`) while the old one
keeps serving. Most stores support an alias or a pointer the application resolves at query time (Qdrant
collection aliases, OpenSearch index aliases; in pgvector a view or a config row naming the live table).
When the build finishes and passes evaluation, repoint the alias in one operation. Queries never see a
mixed index, rollback is repointing the alias back, and the old collection is deleted after a soak
period. The price is temporarily holding two copies of vectors and two ANN graphs, which must be planned
into memory and disk sizing.

### Catching up on writes during the build
Documents keep changing while the rebuild runs. Two patterns: *dual-write*, where ingestion embeds every
new chunk under both versions until the swap; or *replay*, where the build records its start watermark,
and after finishing, re-processes everything changed since. Dual-write is simpler to reason about;
replay costs less during long builds.

### Evaluate before you swap
A new model is not better by assumption. Run the retrieval eval set (queries with known relevant chunks)
against both collections and compare recall@k and MRR. Also check the downstream answer quality on a
sample, because a model that retrieves different-but-also-relevant chunks can shift answers in ways
retrieval metrics miss. The swap is gated on this comparison, which is why the build must be separate.

### Dimension and quantization changes
Changing dimension (for example truncating a Matryoshka-trained model to fewer dimensions) or storage
precision (float32 to int8 or binary) changes the stored representation and the ANN index parameters; it
is a new embedding version even if the model is the same. Rebuild rather than convert in place, and
re-run the eval, because quantization costs recall that must be measured, not assumed.

### Cost and duration (illustrative arithmetic)
A corpus of 10 million chunks averaging 400 tokens is 4 billion tokens. At an illustrative hosted price of
a few cents per million tokens that is on the order of a hundred dollars, which is rarely the problem. The
constraint is throughput: at an illustrative rate limit of a million tokens per minute the embedding
calls alone take roughly 67 hours, before HNSW build time. That duration is what makes dual-write or
replay necessary and makes content hashing worth having for the routine runs in between.

## Example
A minimal version of the three mechanisms: a recipe hash, a content-keyed embedding cache, and an alias
swap. The vector store is abstracted as a dict of collections so the logic is visible.

```python
import hashlib, json

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
`docs_v7` is at least as good as for `docs_v6`.

## Trade-offs
- Blue-green rebuilds give atomic cutover and instant rollback but double vector and index memory during
  the build; if the corpus barely fits one GPU or one node, you need a staged or tiered rebuild instead.
- Content hashing makes routine runs cheap, but a hash on exact text means a one-character edit re-embeds
  the chunk and its neighbours when overlap is involved; that is correct, just not free.
- Pinning a model revision protects consistency but means you consciously own upgrades; letting a vendor
  update silently is cheaper to operate right up until retrieval quality shifts with no deploy to blame.
- Dual-write keeps the new index current with no catch-up step but doubles embedding cost for the build
  window; replay is cheaper and flips to being the better choice when builds take days.
- Keeping old versions for rollback is safe but each held version is storage and an ANN graph; set an
  explicit retirement window rather than letting versions accumulate.

## Pitfalls
- Upgrading the query-side model without rebuilding the index; the symptom is retrieval that returns
  plausible-looking but irrelevant chunks with no errors and eval recall that collapses overnight.
- Changing the chunker and reusing the old vectors because "the model is the same"; symptom is citations
  pointing at wrong offsets and duplicated or truncated passages in context.
- Re-embedding in place against the live collection; symptom is a window of mixed-space results and
  user complaints that correlate with the ingestion job's schedule.
- Swapping on build completion rather than on eval results; symptom is a regression discovered by users,
  with the old index already deleted.
- Forgetting that filters and metadata live beside vectors; a rebuild that drops ACL or tenant fields
  produces an index that passes retrieval eval and fails a security review.
- Sizing memory for one copy of the index; symptom is out-of-memory or evictions precisely during the
  rebuild, the moment you can least afford it.

## When to use
Reach for explicit versioning from the first production index, because the first model upgrade arrives
sooner than planned and the retrofit is painful. The alias-swap pattern is the default for any corpus that
takes longer than a few minutes to rebuild; below that, a rebuild-and-replace in a transaction is fine.
Content hashing pays for itself as soon as ingestion runs on a schedule rather than once. Measure three
things: fraction of chunks re-embedded per run (should be small except on version changes), build duration
versus the rate of upstream change (decides dual-write versus replay), and eval recall before and after
every swap. In interviews, the expected answer to "how do you upgrade the embedding model in production"
is this exact sequence: version the recipe, build beside, evaluate, swap the alias, retire later.

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

## Quiz
### Q: Why can't you query an index built with embedding model A using vectors from model B?
- [ ] Model B produces vectors that are too large to compare efficiently
- [x] The two models define unrelated vector spaces, so similarity between their vectors is meaningless
- [ ] Model B normalizes vectors differently, which the index can correct for at query time
- [ ] The ANN index stores the model name and rejects the query
> Each model learns its own geometry; the same text lands at unrelated points. Cosine similarity across
> spaces is noise, which is why retrieval degrades to near-random without any error. Indexes do not
> validate this for you unless you store and check the version yourself.

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
