---
id: data-lineage
module: data-architecture
name: Data lineage
kind: concept
difficulty: core
est_minutes: 15
related:
  - data-architecture/model-and-dataset-provenance
  - data-architecture/data-contracts
  - data-architecture/data-retention-and-deletion-architecture
  - data-architecture/etl-elt-for-rag
  - data-architecture/training-data-pipelines
  - observability/tracing
  - security-governance-compliance/audit-trails
  - rag-retrieval/citations-grounding
sources:
  - Official docs: OpenLineage
  - Kleppmann, 2017 — Designing Data-Intensive Applications
---

## TL;DR
Data lineage is the recorded graph of where every dataset, chunk, feature and model came from: which
inputs, which job, which code version, which run. It exists because the questions that matter in
production ("why did this answer change", "which indexes contain this customer's data", "what was this
model trained on") are graph traversals, and without a recorded graph the only way to answer them is to
read code and guess. In AI systems, lineage is what makes deletion, debugging and reproducibility
possible at all.

## Why it exists
An AI system is a long chain of transformations: source documents become parsed text, text becomes
chunks, chunks become vectors, vectors join with metadata into an index; separately, raw records become
cleaned datasets, datasets become training mixes, mixes become checkpoints, checkpoints become served
models. Each hop is written by a different job, often by a different team, and each hop loses information
about the one before it. A vector in the index does not know which parser version produced its text; a
model checkpoint does not know which rows were filtered out of its training set.

Then something goes wrong, and the question is always upstream. A customer asks for their data to be
deleted: which chunks, which caches, which fine-tuning sets, which checkpoints contain it? Retrieval
quality drops on Tuesday: did the source change, the parser, the chunker, the embedding model, or the
index build? A model produces an output that should have been impossible: was the offending example in
the training data, and which pipeline let it through? Each question is "walk backward from this artifact
to its inputs" or "walk forward from this input to everything derived from it".

The naive alternative is reading the pipeline code. It fails because code describes what *could* run,
not what *did* run with which inputs at which version. The second alternative, file naming conventions and
README files, fails because they are not updated by the jobs themselves and drift immediately. Lineage
has to be emitted by the job at run time, as a side effect of doing the work, or it will not be accurate.

## How it works
### The graph
Lineage is a directed graph with two node types: **datasets** (a table, a file prefix, a vector
collection, a model artifact, a prompt template) and **jobs** (a pipeline step). A **run** is one
execution of a job, and it carries edges: run *reads* datasets A and B, run *writes* dataset C. Dataset
versions are nodes, not just dataset names, *because* "the training table" is not an input; "the training
table as of snapshot 2024-06-01T00:00Z, 4.2 million rows, content hash h" is. Without versioned nodes,
the graph tells you a job touched a table and nothing about which state of the table.

### Granularity
Table-level lineage says job J read table T. Column-level lineage says column `price_usd` in C derives
from `price_cents` in A through J. For AI pipelines a third level is needed: **record-level** or
**chunk-level** lineage, where each chunk in the index carries the identifier of its source document, the
document version, the parser version and the chunker version. This is heavier, but it is the only
granularity at which "delete this person's data" and "why was this passage retrieved" can be answered.
The practical compromise: table-level for the warehouse, record-level for anything that feeds retrieval,
fine-tuning or a model's outputs.

### Capture at run time, not by declaration
The job emits lineage events as it runs: a start event naming the job, run id and inputs with their
versions; a complete event naming outputs with their versions and facets such as row counts, schema and
the code commit. This design is chosen *because* declared lineage (a YAML file describing the DAG) tells
you the intended graph, not the actual one for a specific run. OpenLineage is the common open standard for
these events; orchestrators and transformation tools can emit them, and catalogs such as Marquez, DataHub
or Atlas store and render the graph. Even without the tooling, a table of `(run_id, job, direction,
dataset, version, facets)` rows gives you the same power.

### Facets that make lineage useful
A bare edge is not enough. Each run records the code version (commit hash, container digest), the
parameters (chunk size, embedding model id), the environment, and per-dataset statistics (row count,
schema fingerprint, content hash). These are what turn "the index was rebuilt" into "the index was rebuilt
by commit abc with chunk size 512 from document snapshot h". When two runs produce different results, you
diff their facets.

### Traversals
- **Impact analysis (forward):** from a dataset version, everything derived from it. Used before changing
  a source schema and for deletion propagation: a document id walks forward to chunks, vectors, caches,
  eval sets and fine-tuning examples.
- **Root cause (backward):** from an artifact, its ancestors. Used when quality regresses: compare the
  ancestor facets of the good run and the bad run.
- **Reproduction:** the backward closure plus the code versions is the recipe to rebuild an artifact.

### Lineage for RAG specifically
Each chunk stored in the vector index carries `source_id`, `source_version`, `parser_version`,
`chunker_version`, `embedding_version` and `ingest_run_id`. Each retrieved chunk is then logged with the
request trace, which connects the serving-time trace to the ingestion-time graph. That join is the
mechanism behind trustworthy citations: the citation is not "a chunk that looks like page 12" but "chunk
c, from document d at version v, ingested by run r".

### Lineage for training
A training run's inputs are dataset versions and a base model version; its outputs are checkpoints and
eval results. Recording the exact data snapshot hash is what lets you answer "was example x in the
training set" without re-running the filter pipeline, and it is what dataset contamination checks need.

## Example
A minimal lineage recorder for a RAG ingestion step. It writes versioned dataset nodes and run edges to an
in-memory store, then answers the two traversals that matter: forward impact from a document and backward
root cause from a chunk.

```python
import hashlib, uuid, time
from collections import defaultdict

edges = []          # (run_id, direction, dataset_id)
runs = {}           # run_id -> facets
chunk_lineage = {}  # chunk_id -> facets

def version_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:12]

def ingest(doc_id: str, text: str, commit: str, chunker="recursive-v3", emb="emb-v7"):
    run_id = str(uuid.uuid4())[:8]
    doc_ver = version_of(text.encode())
    runs[run_id] = {"job": "ingest", "commit": commit, "chunker": chunker,
                    "embedding_version": emb, "started": time.time()}
    edges.append((run_id, "reads", f"doc:{doc_id}@{doc_ver}"))
    for i, piece in enumerate(text.split(". ")):            # stand-in chunker
        chunk_id = f"chunk:{doc_id}:{doc_ver}:{i}"
        chunk_lineage[chunk_id] = {"source": f"doc:{doc_id}@{doc_ver}", "run": run_id,
                                   "chunker": chunker, "embedding_version": emb}
        edges.append((run_id, "writes", chunk_id))
    return run_id

def forward(dataset_id: str):
    """Everything derived from a dataset version (deletion propagation / impact)."""
    out, frontier = set(), {dataset_id}
    while frontier:
        d = frontier.pop()
        for run_id in {r for r, dir_, ds in edges if dir_ == "reads" and ds == d}:
            for r, dir_, ds in edges:
                if r == run_id and dir_ == "writes" and ds not in out:
                    out.add(ds); frontier.add(ds)
    return out

def backward(chunk_id: str):
    """Why does this chunk look the way it does?"""
    lin = chunk_lineage[chunk_id]
    return {**lin, "run_facets": runs[lin["run"]]}

r1 = ingest("kb/42", "Refunds take 5 days. Chat support is 24/7.", commit="a1b2c3")
doc_node = next(ds for r, d, ds in edges if r == r1 and d == "reads")
print(sorted(forward(doc_node)))      # the chunks to delete if kb/42 must be erased
print(backward("chunk:kb/42:%s:0" % doc_node.split("@")[1]))
```

In a real system the same records would be OpenLineage events to a catalog, and the serving trace would
log the chunk ids it retrieved so a user-visible answer links back to `backward(...)`.

## Trade-offs
- Record-level lineage makes deletion and citation exact, but it adds a handful of metadata fields to
  every chunk and row; at billions of records the storage is real, and table-level may be all the
  warehouse side needs.
- Run-time capture is accurate but requires every job to be instrumented; one uninstrumented job creates
  a hole in the graph exactly where you later need it, so coverage is worth enforcing in CI.
- A full lineage catalog gives impact analysis across teams, but it is another system to run; a single
  lineage table owned by the AI platform is enough until multiple teams need to query it.
- Versioned dataset nodes give precise reproduction but mean immutable snapshots or content hashes for
  every input, which changes how pipelines write data; mutable tables with no snapshot are the enemy.

## Pitfalls
- Recording dataset names instead of dataset versions; the symptom is a graph that says "the training
  job read the events table" and cannot tell you which rows were present.
- Documenting lineage in a diagram or README rather than emitting it from jobs; the symptom is a graph
  that is confidently wrong six weeks later.
- Storing chunk metadata without the ingest run id or source version; the symptom is being unable to
  explain why two retrievals of the "same" page differ after a re-index.
- Treating the serving trace and the ingestion lineage as separate systems; the symptom is a citation
  that cannot be linked to a specific document version when a user disputes it.
- Skipping lineage for manual one-off fixes ("I just patched that table by hand"); the symptom is an
  unexplained discontinuity nobody can reproduce.

## When to use
Lineage becomes mandatory the moment a system must honour deletion requests, must explain its outputs,
or has more than one pipeline feeding retrieval or training, which describes nearly every production AI
system. Start with the minimum: a run table with inputs, outputs, versions and code commit, plus source
and run identifiers on every chunk. Adopt a catalog when more than one team needs the graph. Measure
coverage (fraction of jobs emitting lineage), and test it by running a deletion drill: pick a document,
traverse forward, and verify every derived artifact is found. In interviews, "how would you handle a
right-to-erasure request across a RAG and fine-tuning stack" is answered by describing forward traversal
over a lineage graph; without it the honest answer is "we could not".

## Role lens
- **Engineer:** Instruments ingestion and training jobs to emit run events with versions and facets, and
  stamps every chunk with its source version and ingest run id.
- **Architect:** Decides the lineage granularity per data domain, picks declarative versus run-time capture
  and the catalog, and makes lineage coverage a release requirement.
- **FDE:** Uses the graph to answer a customer's auditor or DPO: which of their records are in which
  indexes and datasets, and proves deletion with a forward traversal.

## Check yourself
**Q:** Retrieval quality for one product area dropped sharply after last night's pipeline runs. Nothing was
deployed. How does lineage help you find the cause, and what would you need to have recorded?
**A:** Take a failing query, find the chunks that should have been retrieved, and walk backward: each chunk
records its source document version, parser version, chunker version, embedding version and ingest run.
Compare those facets against the same chunks from the last good index. If the source version changed, the
upstream content moved or was reformatted; if parser or chunker versions changed, a dependency upgrade
slipped in without a deploy of your code; if the embedding version changed, a model revision moved under
you; if only the run differs, compare run facets such as row counts and schema fingerprints to find a
partial or failed load. For this to work, jobs must emit run events with code and container versions and
per-dataset statistics, and chunks must carry their run and source versions. Without versions, you only
know that "ingestion ran", which is where the investigation would stall.

## Quiz
### Q: Why must lineage nodes be dataset versions rather than dataset names?
- [ ] Names are not unique across teams
- [x] A job reads a specific state of a dataset, and reproduction or deletion requires knowing which state
- [ ] Versions are easier to store in a graph database
- [ ] Names change too often to be reliable identifiers
> "Job J read table T" cannot answer which rows were present or let you rebuild the output. Recording the
> snapshot or content hash as the node makes the graph a recipe rather than a sketch.

### Q: Which lineage traversal answers "which artifacts must be purged when this document is deleted"?
- [ ] Backward traversal from the index to the document
- [x] Forward traversal from the document version to everything derived from it
- [ ] A search of the vector index for similar content
- [ ] A diff of the last two ingestion runs
> Deletion propagation starts from the source and follows writes downstream: chunks, vectors, caches,
> eval examples, fine-tuning rows. Backward traversal explains a single artifact; similarity search would
> miss derived data that no longer resembles the original.

### Q: Why is lineage captured by jobs at run time preferred over a maintained diagram of the pipeline?
- [ ] Diagrams cannot represent cycles in the pipeline
- [ ] Run-time capture is cheaper to implement
- [x] A diagram describes the intended graph; run-time events describe what actually ran, with which versions
- [ ] Regulators require machine-readable formats
> Declared lineage drifts the moment someone changes a job, and it never knows which input version a given
> run used. Events emitted as a side effect of doing the work are accurate by construction.
