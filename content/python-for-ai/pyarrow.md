---
id: pyarrow
module: python-for-ai
name: pyarrow
kind: tool
difficulty: core
est_minutes: 14
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
pyarrow is the Python binding to Apache Arrow, a language-independent columnar memory format, and the
standard Python reader and writer for Parquet. Arrow lets pandas, Polars, DuckDB, Spark and Hugging Face
datasets hand tables to each other without serializing, often without copying; Parquet stores those
tables compressed on disk with per-column statistics so readers fetch only what a query needs. You rarely
write much pyarrow directly, but it sits under most of your data stack.

## Why it exists
Every data tool used to have its own in-memory layout. Moving a table from a database client to pandas,
then to Spark, then to a model's data loader meant converting each value at every hop: row-by-row
serialization, often through Python objects, that could cost more than the analysis itself. And on disk,
CSV and JSONL store numbers as text with no types, so every read re-parses every byte and must read every
column even when the query needs two.

Arrow attacks the in-memory problem with a shared specification. If every library lays out a column the
same way, a table can be handed from C++ to Python to Rust by passing pointers to buffers. Parquet
attacks the storage problem: a columnar file format with types, encodings, compression and statistics, so
a reader can skip columns and whole blocks of rows. The two are complementary: Parquet is optimized for
being small on disk and selectively readable, Arrow for being fast to compute on in memory, and pyarrow
converts between them efficiently.

## How it works
### The Arrow memory layout
An Arrow array is a few contiguous buffers. A fixed-width column such as `int64` or `float32` is one
values buffer plus an optional *validity bitmap*, one bit per row marking nulls, which is why missing
values do not force a type change as NaN does in pandas. A variable-length column such as `string` adds an
*offsets* buffer: row *i* is the bytes between `offsets[i]` and `offsets[i+1]` in one shared data buffer,
so a million strings are three buffers rather than a million Python objects. Nested types (`list`,
`struct`, `fixed_size_list`) compose these. A fixed-size list of 768 float32 values is a natural type for
embeddings: one contiguous float buffer that converts to a NumPy `(n, 768)` array without copying.

### Tables, batches and chunks
A `RecordBatch` is a set of equal-length arrays with a `Schema`. A `Table` is a schema plus columns that
are `ChunkedArray`s, sequences of arrays, *because* tables are built by concatenating batches from files
or streams, and chunking avoids copying them into one buffer. Many operations work chunk by chunk;
`combine_chunks()` makes a single contiguous array when a consumer needs one.

### Sharing without copying
Within one process, the Arrow C Data Interface lets libraries exchange buffers by pointer, which is how
Polars, DuckDB and pandas' Arrow-backed dtypes interoperate cheaply. Across processes, the Arrow IPC
format (also called Feather v2 on disk) writes the in-memory layout directly, so a file can be
memory-mapped and read with almost no decoding. Hugging Face `datasets` stores data as Arrow files and
memory-maps them, which is why a dataset larger than RAM can still be indexed randomly.

### Parquet on disk
A Parquet file is split into *row groups*, each holding a *column chunk* per column. Column chunks are
encoded (dictionary encoding for repetitive values such as tenant IDs or model names, run-length and
bit-packing for small integers) and then compressed (Snappy by default in pyarrow, Zstandard for a better
ratio). The footer stores the schema and per-column-chunk statistics such as min and max. A reader asked
for two columns with a filter reads the footer, skips row groups whose statistics cannot match, and
fetches only the needed column chunks, which is why the same file serves cheap selective queries. The
`pyarrow.dataset` module extends this to directories of files, including hive-style partitions such as
`tenant=acme/`, which prune whole files.

### Compute
`pyarrow.compute` provides vectorized kernels (filters, arithmetic, string functions, aggregations) over
Arrow arrays. It is useful for light transformations, but for analytical queries you usually reach for
Polars or DuckDB on top of Arrow.

## Example
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

The read skips the `chunk` column entirely. It cannot skip row groups here, because every row group
contains all three tenants; had the data been sorted or partitioned by tenant, the min/max statistics
would let the reader skip two-thirds of the row groups.

## Trade-offs
- Parquet's compression and column pruning make selective reads cheap, but files are immutable and
  poor at single-row updates; frequently mutated data belongs in a database or a table format on top.
- Arrow's shared layout makes library hand-offs nearly free, but zero-copy only holds when types match;
  nulls, string columns or non-contiguous chunks can force a copy when converting to NumPy or pandas.
- Large row groups compress better and scan faster, while small ones allow finer skipping and lower
  memory when reading; the right size depends on whether workloads scan or seek.
- Explicit schemas catch type drift at write time, at the cost of maintaining them as fields evolve;
  schema inference is convenient until two files disagree and a dataset read fails.

## Pitfalls
- Writing many tiny Parquet files from a streaming job; the symptom is slow reads dominated by opening
  files and parsing footers, fixed by compacting into fewer, larger files.
- Using 32-bit-offset `string` for huge text columns; the symptom is an offset overflow error once one
  array's text exceeds about 2 GB, fixed with `large_string` or smaller chunks.
- Expecting filters to skip data on unsorted files; the symptom is a "filtered" read taking as long as
  a full scan, because row-group statistics overlap every predicate.
- Letting schemas be inferred per file; the symptom is a dataset read failing because one file has an
  int column as double or a field missing entirely.
- Assuming `to_numpy()` is always zero-copy; the symptom is an error or memory spike on columns with
  nulls or multiple chunks.

## When to use
Use Parquet via pyarrow as the default storage for any tabular dataset you will read more than once:
eval results, traces, processed training data, chunk and embedding tables for RAG. Use Arrow IPC or
Feather when you want fast, memory-mappable local files with no decoding. Use pyarrow directly when you
need explicit schemas, control over row groups and compression, or to move data between libraries;
otherwise let pandas, Polars or DuckDB call it for you. Keep JSONL for human-readable interchange and
append-only logs, and convert to Parquet for analysis. Measure file size, read time for typical queries
and peak memory.

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
