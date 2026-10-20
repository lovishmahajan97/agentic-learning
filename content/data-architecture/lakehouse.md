---
id: lakehouse
module: data-architecture
name: Lakehouse
kind: concept
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - data-architecture/etl-elt-for-rag
  - data-architecture/training-data-pipelines
  - data-architecture/data-lineage
  - data-architecture/data-retention-and-deletion-architecture
  - data-architecture/model-and-dataset-provenance
  - backend-fundamentals/database-design
  - system-design/data-consistency
  - security-governance-compliance/data-residency
sources:
  - Armbrust et al., 2021 — Lakehouse: A New Generation of Open Platforms that Unify Data Warehousing and Advanced Analytics
  - Armbrust et al., 2020 — Delta Lake: High-Performance ACID Table Storage over Cloud Object Stores
  - Official docs: Apache Iceberg
  - Official docs: Apache Parquet
---

## TL;DR
A lakehouse is a transactional table layer (Delta Lake, Apache Iceberg, Apache Hudi) over open columnar
files (Parquet) in object storage. It keeps the cheap, engine-agnostic storage of a data lake and adds
what a lake lacks: atomic commits, snapshot isolation, schema enforcement and evolution, time travel and
real deletes. For AI systems it is the natural home for raw documents, chunk and embedding tables, training
datasets and trace logs, because many different engines need the same data and none of them should own it.

## Why it exists
Before lakehouses, teams ran two tiers. A data lake (files in S3 or similar) held raw and semi-structured
data cheaply and let Spark or Python read it, but a directory of Parquet files is not a table: a writer
that dies halfway leaves readers seeing a partial dataset, two concurrent writers corrupt each other, there
is no schema to enforce, and deleting one user's rows means rewriting files by hand with no record of what
changed. This is the "data swamp". A data warehouse held curated tables with ACID guarantees and SQL
performance, but in a proprietary format, at a higher cost, and poorly suited to images, PDFs, embeddings
or the Python-first tooling ML teams use. So data was copied from the lake to the warehouse, the copies
drifted, and governance had to be enforced twice.

AI workloads make the swamp problem worse. A RAG pipeline wants to append millions of chunk rows while a
retrieval evaluation reads a consistent snapshot; a training job must be reproducible against exactly the
data it saw; a GDPR request requires deleting one person's documents and proving the deletion also reached
the derived tables. Each of these needs transactions and versioning on top of cheap storage. The
lakehouse idea, stated plainly, is: keep the files open and the storage cheap, and add a metadata layer
that turns a set of files into a versioned, transactional table that any engine can read.

## How it works
### Layer 1: open columnar files in object storage
Data lives as Parquet files on S3, GCS or ADLS. Parquet is columnar and compressed, split into row groups
with per-column statistics (min, max, null count), so an engine scanning for `tenant_id = 'acme'` can skip
whole row groups whose statistics exclude the value. Object storage gives durability and essentially
unlimited capacity at a low price per GB, but with high per-request latency and no in-place updates: a
file is written whole or not at all. Every other design choice follows from that constraint.

### Layer 2: the table format
A table format is a metadata log that says which files make up the table at each version. Delta Lake
writes a `_delta_log` of JSON commit entries with periodic Parquet checkpoints; Iceberg writes a tree of
metadata files, manifest lists and manifests pointing at data files; Hudi keeps a timeline of commits. In
all three, a write is: add new data files, then atomically publish a new log entry or metadata pointer
that references them. Because publishing is a single atomic operation, readers either see the old version
or the new one, never a mix, and concurrent writers use optimistic concurrency: each validates that no
conflicting commit landed since it started, and retries if one did. This is how ACID is achieved without a
database server sitting in front of the files. The atomic publish still needs one primitive from somewhere:
a put-if-absent write in the object store (which S3 lacked until it added conditional writes, so Delta on S3
long relied on a single writer or an external lock table), or an atomic pointer swap in the catalog, which
is how Iceberg commits.

The same log gives you:

- **Snapshot isolation and time travel.** A reader pins a version; a training job can record the
  snapshot id it read, and reading it again later returns identical rows, as long as that snapshot has not
  been expired and its files vacuumed. Default retention windows are days to weeks (Delta's default vacuum
  threshold is 7 days), so a snapshot needed for a year must be explicitly retained (an Iceberg tag, a
  longer retention setting on that table) or materialized as its own dataset.
- **Schema enforcement and evolution.** The current schema is in the metadata; writes with a mismatched
  schema are rejected, and adding a column is a metadata change rather than a rewrite.
- **Deletes and updates.** Copy-on-write rewrites the affected files and commits the replacement;
  merge-on-read records deletes in small side files (Iceberg delete files, Delta deletion vectors) that
  readers apply at scan time, deferring the rewrite to a later compaction. Copy-on-write favours readers,
  merge-on-read favours frequent small writes.
- **Partitioning and clustering.** Rows are laid out by a partition value (date, tenant) and optionally
  clustered within partitions so that statistics-based skipping works. Iceberg's hidden partitioning
  derives the partition from a column transform so queries need not know the layout.

### Layer 3: the catalog
A catalog maps table names to the current metadata location and is where access control, discovery and
lineage attach: Hive Metastore, AWS Glue, Unity Catalog, Nessie, or an Iceberg REST catalog. It is the
component that makes "the same table" mean the same thing to Spark, Trino, DuckDB, Flink, Ray and a
pandas script, which is the whole point: no copies, one access policy.

### Maintenance the format does not do for you
Streaming appends create many small files, and object listing plus per-file overhead then dominates query
time; periodic compaction rewrites them into large files. Old snapshots and the files only they reference
accumulate until expired and vacuumed, which is also the moment a "deleted" row is physically gone, so
retention policy and deletion guarantees depend on running this job.

### Where AI data sits in it
A common layering is bronze (raw ingested bytes and metadata), silver (parsed, cleaned, conformed) and gold
(serving-ready tables). For an AI platform the concrete tables are: documents (raw pointer, hash, ACL),
parsed text, chunks, embeddings (an array column alongside `model_id`), training datasets as pinned
snapshots, and evaluation and trace logs. The vector index and any feature-serving store are derived from
these tables, rebuilt from them, and never the only copy. The lakehouse is an analytical and batch store:
point lookups and sub-100 ms serving belong to Postgres, a key-value store or the vector database.

## Example
A chunk table in Delta Lake using the `deltalake` Python package: append new chunks, upsert changed ones by
`chunk_id`, read an older snapshot for a reproducible evaluation, and compact small files. The same table
can be read by Spark, DuckDB or Trino through a catalog.

```python
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

path = "s3://ai-lake/silver/chunks"           # or a local directory while developing

batch = pa.table({
    "chunk_id":   ["c-001", "c-002"],
    "doc_id":     ["d-1", "d-1"],
    "doc_version":["9f3a", "9f3a"],
    "text":       ["Refund window is 30 days.", "Returns need a receipt."],
    "model_id":   ["emb-v2", "emb-v2"],
    "embedding":  [[0.01, 0.42, -0.13], [0.33, 0.05, 0.27]],   # real width: hundreds to ~3,000 floats
})
write_deltalake(path, batch, mode="append", partition_by=["model_id"])

# Upsert re-chunked rows: match on chunk_id, update text/embedding, insert new ids.
dt = DeltaTable(path)
(dt.merge(source=batch, predicate="t.chunk_id = s.chunk_id", source_alias="s", target_alias="t")
   .when_matched_update_all()
   .when_not_matched_insert_all()
   .execute())

# Reproducible evaluation: pin the snapshot the eval ran against.
eval_version = dt.version()
frozen = DeltaTable(path, version=eval_version).to_pyarrow_table()
print(eval_version, frozen.num_rows)

# Maintenance: fold small streaming files into large ones, then expire old files.
dt.optimize.compact()
dt.vacuum(retention_hours=168, dry_run=False)
```

Every training or evaluation run that reads this table records `eval_version`; a later audit re-reads the
same rows even after the table has moved on, provided the retention policy keeps that version. Note that
`vacuum(retention_hours=168)` deletes files that only versions older than seven days still reference, so
time travel to those versions stops working: retention and reproducibility are one decision, not two.

## Trade-offs
- Open files and a shared catalog let any engine read the data without copies, but you now operate
  compaction, snapshot expiry and vacuuming yourself unless a managed platform does it for you.
- Object storage makes capacity nearly free, but per-request latency makes the lakehouse wrong for point
  lookups or request-path serving; the moment a query needs sub-100 ms, the data needs a derived store.
- Copy-on-write keeps reads simple and fast, while merge-on-read makes frequent small updates and deletes
  cheap; the choice flips toward merge-on-read when CDC-style streams feed the table continuously.
- Time travel gives reproducibility and audit cheaply, but every retained snapshot pins files on disk,
  and a deletion is not a deletion until snapshot expiry runs; long-lived reproducibility and prompt
  physical deletion pull in opposite directions, so training snapshots that must outlive retention are
  usually materialized separately with deletion handled explicitly.
- Choosing Delta, Iceberg or Hudi is mostly an ecosystem decision (Databricks, Spark and Trino, or a
  streaming-first stack); the formats converge on features, so engine support and catalog integration
  should decide.

## Pitfalls
- Streaming small appends without compaction; the symptom is queries whose time is dominated by listing
  and opening thousands of tiny files, visible as low bytes-scanned but long wall-clock.
- Partitioning on a high-cardinality column such as `doc_id`; the symptom is millions of directories, slow
  metadata operations and no skipping benefit.
- Treating the lakehouse as the serving database for retrieval or features; the symptom is p99 latency in
  seconds and a request path that depends on object-store availability.
- Storing embeddings without a `model_id` column; the symptom is an index rebuilt from mixed vectors after
  a model upgrade, with retrieval quality that silently degrades.
- Never expiring snapshots or vacuuming; the symptom is storage growing without bound and a GDPR deletion
  that an auditor can still find in an old version.
- Letting every writer evolve the schema freely; the symptom is downstream jobs failing on a renamed
  column, which the data-contracts lesson addresses.

## When to use
Use a lakehouse when more than one engine or team needs the same large tables, when reproducibility and
audit matter (training sets, evaluation snapshots, deletion proof), or when raw documents and derived
chunk and embedding tables must coexist with analytics. For a single service with a few gigabytes, a
Postgres schema is simpler and should win. Measure file count and average file size per table, query bytes
scanned versus rows returned (skipping effectiveness), snapshot retention versus storage growth, and time
from a deletion request to physical removal. In day-to-day work the lakehouse is where the RAG pipeline's
stage tables and the training manifest live; in interviews, be ready to explain how an atomic commit over
immutable files yields ACID, and why the vector index is derived from a table rather than being the source.

## Role lens
- **Engineer:** Writes stage outputs as Delta or Iceberg tables with partition keys and `model_id` columns,
  pins snapshot versions in evaluation and training configs, and schedules compaction and vacuum jobs.
- **Architect:** Chooses the table format and catalog to match the engines in use, defines the bronze,
  silver and gold boundaries, and decides which derived serving stores are rebuilt from which tables.
- **FDE:** Lands customer data into the customer's own lakehouse account and region, respects their
  catalog's access policies, and uses time travel to show exactly which data a delivered model was
  trained on.

## Check yourself
**Q:** A team keeps parsed documents and chunk embeddings as plain Parquet files in S3, written by a nightly
Spark job and read by a Python evaluation script and a Trino dashboard. They see occasional evaluation
runs with partially missing data and cannot honour a customer's deletion request confidently. What does
moving to a lakehouse table format change, mechanically, and what still remains their responsibility?
**A:** Plain Parquet directories have no commit boundary, so the evaluation script sometimes lists files
while Spark is mid-write and reads a mix of old and new. A table format records which files form each
version in a log and publishes a new version atomically, so readers see a complete snapshot and writers
detect conflicts through optimistic concurrency. Deletion becomes a `DELETE` or `MERGE` that commits a new
version excluding the rows, with copy-on-write or deletion files handling the physical layout, and the
log records what changed. What stays with the team: the deleted rows persist in prior snapshots until
expiry and vacuum run, so a retention job and a check against derived stores such as the vector index are
still needed; compaction of the nightly job's small files; and a catalog so Trino, Spark and Python all
resolve the same table and access policy.

## Quiz
### Q: How does a lakehouse table format provide atomic commits on object storage that has no in-place updates?
- [ ] By locking the S3 bucket for the duration of a write
- [x] By writing new immutable data files first and then atomically publishing a metadata entry that references them, so readers see the old or new version entirely
- [ ] By buffering writes in a database server that fronts the files
- [ ] By using Parquet row-group checksums to detect partial writes
> The files themselves are immutable and written whole; the only mutable thing is the pointer or log
> entry naming the current version. Making that single publish step atomic, with optimistic conflict
> checks for concurrent writers, yields snapshot isolation without a server in the path.

### Q: Which workload is a poor fit for a lakehouse and should use a derived store instead?
- [ ] Rebuilding a vector index from the embeddings table every night
- [ ] Pinning the exact training set a model run read
- [x] Serving per-request vector similarity lookups within a 100 ms latency budget
- [ ] Running an evaluation across a consistent snapshot while new chunks are appended
> Object storage has high per-request latency and tables are optimized for scans, not point lookups.
> Request-path retrieval belongs in a vector database or OLTP store that is rebuilt from the lakehouse
> table, which remains the system of record.

### Q: After deleting a user's rows from a Delta or Iceberg table, why might an auditor still find them?
- [ ] Parquet files cannot be modified, so deletes are impossible
- [ ] The catalog caches query results for 24 hours
- [x] Prior snapshots still reference the old files until snapshot expiry and vacuum physically remove them
- [ ] Merge-on-read tables never apply deletes to readers
> Time travel works precisely because old files are retained. A delete commits a new version without the
> rows, but the previous version remains readable until its files are expired, so the retention job is
> part of the deletion guarantee.
