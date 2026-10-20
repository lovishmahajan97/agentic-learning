---
id: polars
module: python-for-ai
name: polars
kind: tool
difficulty: core
est_minutes: 14
status: reviewed
related:
  - python-for-ai/pandas
  - python-for-ai/pyarrow
  - python-for-ai/json-jsonl
  - python-for-ai/memory-and-performance-profiling
  - data-architecture/training-data-pipelines
  - data-architecture/etl-elt-for-rag
  - python-for-ai/asyncio-vs-threads-vs-multiprocessing
sources:
  - Official docs: Polars
  - Official docs: Apache Arrow
---

## TL;DR
Polars is a DataFrame library written in Rust on the Arrow columnar memory model, with an expression API
and a lazy mode that turns your code into a query plan, optimizes it (pushing filters and column
selection down into the file scan) and runs it on all cores. For tables from a few gigabytes upward,
or pipelines that run repeatedly, it is typically much faster and leaner than pandas; the price is a
different, stricter API and no row index.

## Why it exists
pandas was designed when a single core and a few hundred megabytes were the norm. Its execution model
shows that: each operation runs eagerly, materializes a full intermediate result, mostly on one core,
and string columns have historically been arrays of Python objects. Point it at 50 GB of agent traces to
compute daily token usage per model and you read every column of every file into memory, filter
afterwards, and wait on one core while the others idle.

Two things are needed to do better. The first is a memory layout built for analytics: typed, contiguous
columns with a proper string representation and real null handling, so scans and aggregations are
cache-friendly and vectorizable. Apache Arrow defines exactly that. The second is a query engine rather
than a function library: if the library sees the whole computation before running it, it can skip
columns that are never used, apply filters while reading the file instead of after, and split work
across threads. Databases have done this for decades; Polars brings it to a DataFrame API that lives in
a Python process, with no server to run.

## How it works
### Expressions, not row functions
Computation is described with expressions: `pl.col("latency_ms").quantile(0.95)`,
`(pl.col("out_tokens") / 1000 * pl.col("price")).alias("cost")`, `pl.when(cond).then(a).otherwise(b)`.
An expression is a description of a column computation, not a result. Contexts apply them:
`select`, `with_columns`, `filter`, and `group_by(...).agg(...)`. Because expressions are declarative,
Polars can run independent expressions in parallel and optimize them, which a Python lambda executed per
row would prevent. Python UDFs (`map_elements`) exist but drop you back to interpreter speed.

### Eager and lazy
`pl.read_parquet` returns a `DataFrame` immediately, like pandas. `pl.scan_parquet` (also `scan_csv`,
`scan_ndjson`) returns a `LazyFrame`: each subsequent call adds a node to a logical plan and nothing is
read until `.collect()`. Before executing, the optimizer applies rules such as:

- **Projection pushdown:** only columns referenced anywhere in the plan are read from disk.
- **Predicate pushdown:** filters move into the scan, so Parquet row groups whose statistics rule them
  out can be skipped.
- **Simplification and common-subexpression elimination**, so repeated expressions are computed once.

`lf.explain()` prints the optimized plan, so you can verify what will be read.

### Memory and parallelism
Columns are Arrow-style buffers with a validity bitmap for nulls, so `null` is distinct from a float
`NaN` and an integer column with missing values stays an integer column. The engine is multi-threaded
in Rust, outside the GIL, so a group-by uses all cores without multiprocessing. A parallel group-by
works the way a distributed one does: threads hash rows into partitions, build partial aggregates
independently, and merge them. That is why output group order is not guaranteed unless you pass
`maintain_order=True` (which costs speed), and why it matters for tests and reports that compare
frames row by row. For data larger than RAM, Polars has a streaming engine that processes the plan in
batches, and `sink_parquet` writes a lazy result straight to disk without materializing it; how you
enable streaming has changed across releases, so check the docs for your version.

### No index, strict types
There is no row index and no label alignment: rows are positions, and joins are explicit. Schemas are
strict; mixing types in a column or comparing incompatible dtypes raises an error rather than silently
becoming `object`. This is a deliberate trade: fewer silent surprises, more errors up front.

### Interop
Because both speak Arrow, conversion to and from pyarrow is typically zero-copy, and `to_pandas()`
exists for libraries that only accept pandas. Many plotting and ML libraries accept Polars frames
directly now, but not all.

## Example
Daily token usage per model from a directory of trace files, read lazily.

```python
import polars as pl

lf = (
    pl.scan_parquet("traces/*.parquet")                    # builds a plan; reads nothing
      .filter(pl.col("ts") >= pl.datetime(2026, 10, 1))
      .filter(pl.col("status") == "ok")
      .with_columns(
          (pl.col("output_tokens") / 1000 * pl.col("usd_per_1k_out")).alias("cost_usd"))
      .group_by("model", pl.col("ts").dt.date().alias("day"))
      .agg(
          pl.len().alias("calls"),
          pl.col("input_tokens").sum(),
          pl.col("output_tokens").sum(),
          pl.col("cost_usd").sum(),
          pl.col("latency_ms").quantile(0.95).alias("p95_ms"),
      )
      .sort("day", "model")
)
print(lf.explain())   # both filters appear inside the Parquet scan; only 7 columns are projected
daily = lf.collect()  # multi-threaded execution happens here
daily.write_parquet("daily_usage.parquet")
```

Suppose the trace files hold 40 columns, including large prompt and completion text fields, and only
rows from 1 October onward are needed. The plan reads 7 numeric or short columns and skips row groups whose `ts`
statistics fall before October, so the bytes read can be a small fraction of the dataset. The
equivalent eager pandas code would load all 40 columns, text included, before filtering.

## Trade-offs
- Lazy plans with pushdown and parallelism are fast on large data, but debugging means reading a
  query plan instead of inspecting intermediate frames; for small exploratory data, eager mode or pandas
  is just as quick to write.
- Strict types and no index remove pandas' silent alignment and dtype drift, at the cost of more upfront
  casting and errors when ingesting messy JSON or CSV.
- Faster and leaner than pandas on most analytical workloads, yet its ecosystem is younger, so some
  libraries still expect pandas and you pay a conversion at the boundary.
- An in-process engine needs no server, but it has no concurrency control, durability or access
  management; once many users share the data, a warehouse or database wins.

## Pitfalls
- Writing pandas habits in Polars, such as `map_elements` with Python lambdas; the symptom is Polars code
  running no faster than pandas and using one core.
- Calling `.collect()` early and continuing eagerly; the symptom is memory spikes, because pushdown can
  only apply to the part of the plan built before collecting.
- Confusing `null` and `NaN`; the symptom is `drop_nulls()` leaving NaN rows behind, or averages that
  come out NaN.
- Assuming code copied from older blog posts still runs; the symptom is `AttributeError` or deprecation
  warnings because names such as `groupby` became `group_by` and other APIs evolved quickly.
- Relying on `group_by` output order; the symptom is a test or a diff of a daily report that fails
  intermittently with the same data, because groups come back in a different order each run. Sort
  explicitly or pass `maintain_order=True`.
- Reading wide JSONL with inferred schemas; the symptom is a type error deep in the file when a field
  that was always an integer suddenly holds a string, fixed by passing an explicit schema.

## When to use
Reach for Polars when data is in the gigabytes, when a transformation runs on a schedule (token and
cost rollups, eval aggregation, dataset deduplication and filtering before fine-tuning), or when pandas
is running out of memory or time. Use lazy scans over Parquet whenever possible. Stay with pandas for
small notebooks, for code that leans on pandas-only libraries, or when the team's skills make it the
cheaper choice. Consider DuckDB if the team prefers SQL; it occupies similar ground. Measure wall time
and peak memory on your real data before migrating a pipeline.

## Role lens
- **Engineer:** Writes lazy Polars pipelines over Parquet trace and eval data, checks `explain()` for
  pushdown, and converts to pandas only at library boundaries.
- **Architect:** Chooses between pandas, Polars, DuckDB and a warehouse based on data size, run frequency
  and sharing needs, and standardizes Arrow and Parquet as the interchange layer.
- **FDE:** Processes a customer's large log export on a single VM without provisioning a cluster, getting
  usage and quality numbers within hours of receiving access.

## Check yourself
**Q:** A pandas job reads 30 GB of Parquet traces, filters to one customer, and sums tokens per day. It
OOMs on a 64 GB machine. Why might a Polars lazy rewrite succeed, and what would you verify?
**A:** As written, pandas reads every column of every file into memory before filtering (its
`read_parquet` accepts `columns=` and `filters=`, but the naive job uses neither), and decompression,
Python string objects and intermediates add more, so 30 GB on disk can exceed 64 GB in RAM. A lazy Polars plan built with `scan_parquet` pushes the
customer filter and the column selection into the scan, so only the few needed columns are read and
row groups for other customers can be skipped using Parquet statistics. The aggregation then runs in
parallel. I would verify with `explain()` that the filter and projection are in the scan, measure peak
memory, and if data is not sorted or partitioned by customer, expect less skipping and consider the
streaming engine.

## Quiz
### Q: What does projection pushdown do in a Polars lazy query over Parquet?
- [ ] It moves the group-by to run before filters
- [x] It reads only the columns the query actually references
- [ ] It caches the result for later queries
- [ ] It converts the plan into SQL for an external database
> The optimizer sees the whole plan, so it knows which columns are used and asks the Parquet reader for
> only those. Predicate pushdown is the related rule that moves filters into the scan.

### Q: Why can a Polars group-by use all CPU cores without multiprocessing?
- [ ] It forks one Python process per core automatically
- [ ] It uses asyncio to schedule groups concurrently
- [x] The engine runs in Rust threads outside the Python interpreter, so the GIL does not serialize the work
- [ ] It sends the work to a local Spark cluster
> Polars executes expressions in native code with its own thread pool. Python only builds the plan, so
> the GIL is not held during the heavy computation and parallelism needs no extra processes.

### Q: A teammate's Polars pipeline is no faster than the pandas version. What is the most likely cause?
- [ ] Polars is slower than pandas for CSV files in general
- [ ] Lazy mode adds planning overhead that dominates every query
- [ ] The machine has too few cores for Rust code
- [x] Row-wise Python functions via `map_elements` force interpreter-speed execution
> Expressions run in native, parallel code, but a Python UDF must be called per element under the GIL.
> Rewriting the logic as built-in expressions such as `when/then` or string methods restores the speedup.
