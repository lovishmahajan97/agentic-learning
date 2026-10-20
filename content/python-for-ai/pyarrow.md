---
id: pyarrow
module: python-for-ai
name: pyarrow
kind: tool
difficulty: core
est_minutes: 14
status: crisp
related:
  - python-for-ai/pandas
  - python-for-ai/polars
  - python-for-ai/numpy
  - python-for-ai/hugging-face
  - python-for-ai/json-jsonl
  - data-architecture/lakehouse
  - data-architecture/training-data-pipelines
sources:
  - Official docs: Apache Arrow
  - Official docs: Apache Parquet
---

## TL;DR
pyarrow binds Apache Arrow, the shared columnar memory format, and is Python's standard Parquet reader
and writer. Arrow lets pandas, Polars, DuckDB and Hugging Face swap tables without serializing; Parquet
stores them compressed with statistics so readers fetch only what they need.

## Why it exists
- **Every tool had its own memory layout.** Moving a table from a database client to pandas, then Spark,
  then a model's data loader meant converting each value at every hop: row-by-row serialization, often
  through Python objects, that could cost more than the analysis itself.
- **Text files waste reads.** CSV and JSONL store numbers as text with no types, so every read re-parses
  every byte and must read every column even when the query needs two.
- **Arrow fixes memory with a shared spec.** If every library lays out a column the same way, a table
  moves from C++ to Python to Rust by passing pointers to buffers.
- **Parquet fixes storage.** A columnar file format with types, encodings, compression and statistics, so
  a reader can skip columns and whole blocks of rows.
- **They are complementary.** Parquet is optimized for being small on disk and selectively readable,
  Arrow for fast in-memory compute, and pyarrow converts between them efficiently. You rarely write much
  pyarrow directly, but it sits under most of your data stack.

## How it works
### The Arrow memory layout
An Arrow array is a few contiguous buffers:

| Column type | Buffers |
|---|---|
| Fixed-width (`int64`, `float32`) | One values buffer + optional *validity bitmap*, one bit per row marking nulls |
| Variable-length (`string`) | Adds an *offsets* buffer: row *i* is the bytes between `offsets[i]` and `offsets[i+1]` in one shared data buffer |
| Nested (`list`, `struct`, `fixed_size_list`) | Compose the above |

- **Nulls without type changes.** The validity bitmap is why missing values do not force a type change as
  NaN does in pandas.
- **Strings are three buffers,** not a million Python objects for a million strings.
- **Embeddings fit naturally.** A fixed-size list of 768 float32 values is one contiguous float buffer
  that converts to a NumPy `(n, 768)` array without copying.

### Tables, batches and chunks
- **`RecordBatch`:** a set of equal-length arrays with a `Schema`.
- **`Table`:** a schema plus columns that are `ChunkedArray`s (sequences of arrays), *because* tables are
  built by concatenating batches from files or streams, and chunking avoids copying them into one buffer.
- **Chunk-wise by default.** Many operations work chunk by chunk; `combine_chunks()` makes a single
  contiguous array when a consumer needs one.

### Sharing without copying
- **In process.** The Arrow C Data Interface lets libraries exchange buffers by pointer, which is how
  Polars, DuckDB and pandas' Arrow-backed dtypes interoperate cheaply.
- **In Python,** this is increasingly exposed through the Arrow PyCapsule protocol (`__arrow_c_stream__`
  and friends), so a library can accept "anything Arrow-shaped" without importing the producer.
- **Zero-copy holds only while both sides keep the Arrow layout.** `table.to_pandas()` with default
  settings turns a string column into an `object` column of Python strings: a full copy, often several
  times larger. `to_pandas(types_mapper=pd.ArrowDtype)` keeps Arrow-backed columns instead.
- **Across processes.** The Arrow IPC format (also called Feather v2 on disk) writes the in-memory layout
  directly, so a file can be memory-mapped and read with almost no decoding.
- **Hugging Face `datasets`** stores data as Arrow files and memory-maps them, which is why a dataset
  larger than RAM can still be indexed randomly.

### Parquet on disk
1. **Row groups.** A file is split into *row groups*, each holding a *column chunk* per column.
2. **Encoding.** Dictionary encoding for repetitive values such as tenant IDs or model names; run-length
   and bit-packing for small integers.
3. **Compression.** Snappy by default in pyarrow, Zstandard for a better ratio.
4. **Footer.** Stores the schema and per-column-chunk statistics such as min and max.
5. **Selective reads.** Asked for two columns with a filter, a reader reads the footer, skips row groups
   whose statistics cannot match, and fetches only the needed column chunks. That is why one file serves
   cheap selective queries.

The `pyarrow.dataset` module extends this to directories of files, including hive-style partitions such
as `tenant=acme/`, which prune whole files.

### Compute
- **`pyarrow.compute`** provides vectorized kernels (filters, arithmetic, string functions, aggregations)
  over Arrow arrays.
- **Light work only.** For analytical queries you usually reach for Polars or DuckDB on top of Arrow.

## Examples
### A RAG chunk-and-embedding table in Parquet
Storing chunk text and embeddings for a RAG corpus in Parquet with an explicit schema, then reading back
only what retrieval needs.

```python
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

n, d = 10_000, 384
emb = np.random.default_rng(0).standard_normal((n, d), dtype=np.float32)  # 15.4 MB

schema = pa.schema([
    ("doc_id", pa.string()),
    ("tenant", pa.string()),                       # Parquet dictionary-encodes it on disk
    ("chunk", pa.large_string()),                  # 64-bit offsets: no 2 GB-per-array limit
    ("embedding", pa.list_(pa.float32(), d)),      # fixed_size_list<float32>[384]
])
table = pa.table({
    "doc_id": [f"doc-{i}" for i in range(n)],
    "tenant": [f"t{i % 3}" for i in range(n)],
    "chunk": [f"text of chunk {i}" for i in range(n)],
    "embedding": pa.FixedSizeListArray.from_arrays(pa.array(emb.ravel()), d),
}, schema=schema)

pq.write_table(table, "chunks.parquet", compression="zstd", row_group_size=2_000)
print(pq.ParquetFile("chunks.parquet").metadata.num_row_groups)    # 5

t = pq.read_table("chunks.parquet", columns=["doc_id", "embedding"],
                  filters=[("tenant", "=", "t1")])                 # chunk text never read
vecs = t.column("embedding").combine_chunks().flatten().to_numpy().reshape(-1, d)
print(t.num_rows, vecs.shape, vecs.dtype)                           # 3333 (3333, 384) float32
```

What the read skips depends on layout:

| Layout | `chunk` column | Row groups read |
|---|---|---|
| As written (tenants interleaved) | Skipped entirely | All 5: every row group contains all three tenants |
| Sorted by tenant before writing | Skipped | `t1` occupies rows 3,334 to 6,666 (0-based), in the 2nd, 3rd and 4th groups, so min/max statistics skip the other 2 of 5 |
| Partitioned (`tenant=t1/` directories) | Skipped | Only `t1`'s files; the two-thirds of data for other tenants is never opened |

### Partitioned dataset for per-tenant reads
The production version of the third row: write one directory per tenant, then read one tenant with a
filter that prunes whole files.

```python
import pyarrow.dataset as ds

ds.write_dataset(table, "chunks/", format="parquet",
                 partitioning=["tenant"], partitioning_flavor="hive",   # chunks/tenant=t1/...
                 existing_data_behavior="overwrite_or_ignore")

dset = ds.dataset("chunks/", format="parquet", partitioning="hive")
t1 = dset.to_table(columns=["doc_id", "embedding"],
                   filter=ds.field("tenant") == "t1")                 # other directories never opened
print(t1.num_rows)                                                    # 3333
```

- **The partition column lives in the path,** not in the files, and is restored on read.
- **Watch cardinality.** Partition by a column with tens to thousands of values; one directory per
  document would recreate the tiny-files problem.

## Production patterns
- **Explicit schema on every write.** Define a `pa.schema` per table (with `large_string` for big text and
  `pa.list_(pa.float32(), d)` for embeddings) and pass it to `pa.table` or the writer. Monitor write-time
  type errors; they replace dataset reads failing weeks later on one odd file.
- **Partition by the dominant filter.** Use `ds.write_dataset(..., partitioning=["tenant"],
  partitioning_flavor="hive")` for tenant or date filters, and sort within partitions by the next filter
  key. Watch files opened and bytes read per query.
- **Compact small files on a schedule.** Streaming writers produce many tiny files; a periodic job
  rewrites them with `ds.write_dataset` using its `max_rows_per_file` and `min_rows_per_group` limits.
  Monitor file count and average file size per partition.
- **Keep Arrow types across hand-offs.** Convert with `to_pandas(types_mapper=pd.ArrowDtype)`, or hand the
  table to Polars or DuckDB, instead of default `to_pandas()` on text-heavy tables. Track peak memory
  against `table.nbytes`.
- **Zstandard for archives, Feather for local caches.** Write long-lived Parquet with
  `compression="zstd"`; for scratch data reread many times on one machine, write Arrow IPC with
  `pyarrow.feather.write_feather` and memory-map it on read. Compare file size and read time per format.

## Trade-offs
- **Cheap selective reads vs immutability.** Parquet's compression and column pruning make selective
  reads cheap, but files are immutable and poor at single-row updates; frequently mutated data belongs in
  a database or a table format on top.
- **Free hand-offs vs type matching.** Zero-copy only holds when types match; nulls, string columns or
  non-contiguous chunks can force a copy when converting to NumPy or pandas.
- **Large vs small row groups.** Large ones compress better and scan faster; small ones allow finer
  skipping and lower memory when reading. The right size depends on whether workloads scan or seek.
- **Explicit schemas vs maintenance.** They catch type drift at write time but must evolve with fields;
  inference is convenient until two files disagree and a dataset read fails.

## Pitfalls
- **Many tiny Parquet files from a streaming job.** Symptom: slow reads dominated by opening files and
  parsing footers. Fix: compact into fewer, larger files.
- **32-bit-offset `string` for huge text columns.** Symptom: an offset overflow error once one array's
  text exceeds about 2 GB. Fix: `large_string` or smaller chunks.
- **Expecting filters to skip data on unsorted files.** Symptom: a "filtered" read takes as long as a full
  scan, because row-group statistics overlap every predicate. Fix: sort or partition first.
- **Letting schemas be inferred per file.** Symptom: a dataset read fails because one file has an int
  column as double or a field missing entirely. Fix: an explicit schema.
- **Assuming `to_numpy()` is always zero-copy.** Symptom: an error or memory spike on columns with nulls
  or multiple chunks. Fix: `combine_chunks()` and handle nulls first.
- **Default `to_pandas()` on a large text table.** Symptom: peak memory far above the Arrow table's
  `nbytes`, because every string became a Python object. Fix: `types_mapper=pd.ArrowDtype`.

## When to use
- **Parquet via pyarrow by default** for any tabular dataset read more than once: eval results, traces,
  processed training data, chunk and embedding tables for RAG.
- **Arrow IPC or Feather** when you want fast, memory-mappable local files with no decoding.
- **pyarrow directly** for explicit schemas, control over row groups and compression, or moving data
  between libraries; otherwise let pandas, Polars or DuckDB call it for you.
- **Keep JSONL** for human-readable interchange and append-only logs, and convert to Parquet for analysis.
- **Measure:** file size, read time for typical queries and peak memory.

## Role lens
- **Engineer:** Writes Parquet with explicit schemas and `fixed_size_list` embeddings, reads only needed
  columns, and converts to NumPy without copies for scoring.
- **Architect:** Makes Arrow and Parquet the interchange and storage contract across ingestion, training
  and analytics, including partitioning and file-size policy.
- **FDE:** Asks the customer for a Parquet export instead of CSV, cutting transfer size and avoiding a week
  of type and encoding bugs.

## Check yourself
**Q:** Your embedding store is 20 million rows of text plus 768-d float32 vectors in one directory of
Parquet files. Retrieval for one tenant reads everything and is slow. What do you change, and why?
**A:** Vectors alone are 20 M × 768 × 4 bytes, about 61 GB, so full scans are expensive. First, project:
read only the ID and embedding columns, never the text, until results are chosen. Second, make filters
skip data: partition the dataset by tenant (`tenant=x/` directories) or sort by tenant before writing so
row-group statistics exclude other tenants. Third, tune file and row-group sizes so there are not
thousands of tiny files. If queries are frequent and latency matters, Parquet is the source of truth but
not the serving layer; build an ANN index per tenant from it.

## Quiz
### Q: What lets a Parquet reader skip entire row groups when a query filters on `tenant = "acme"`?
- [ ] The validity bitmap marks irrelevant rows as null
- [x] Per-column-chunk min/max statistics in the file footer show no row in the group can match
- [ ] Dictionary encoding stores each tenant in a separate file
- [ ] Snappy compression indexes every distinct value
> Readers consult footer statistics before fetching data. If a row group's tenant range excludes
> "acme", it is skipped, which only works well when data is sorted or partitioned by that column.

### Q: How does Arrow represent a column of one million variable-length strings?
- [ ] As one million Python string objects referenced by pointers
- [ ] As fixed-width padded strings sized to the longest value
- [x] As an offsets buffer, a contiguous data buffer and an optional validity bitmap
- [ ] As a compressed Parquet column chunk kept in memory
> Offsets mark where each string starts and ends inside one shared byte buffer, so the column is a few
> contiguous buffers that any Arrow-speaking library can read without conversion.

### Q: What is the key difference between Arrow and Parquet?
- [ ] Arrow is row-oriented and Parquet is columnar
- [x] Arrow is an in-memory format for fast computation and exchange; Parquet is an encoded, compressed on-disk format
- [ ] Parquet is a Python library and Arrow is a file format
- [ ] They are the same format with different file extensions
> Both are columnar. Arrow optimizes random access and zero-copy sharing in memory, while Parquet uses
> encodings, compression and statistics to be small and selectively readable on disk.
