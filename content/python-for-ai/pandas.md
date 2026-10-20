---
id: pandas
module: python-for-ai
name: pandas
kind: tool
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - python-for-ai/numpy
  - python-for-ai/polars
  - python-for-ai/pyarrow
  - python-for-ai/json-jsonl
  - python-for-ai/jupyter
  - evals/error-analysis
  - python-for-ai/memory-and-performance-profiling
sources:
  - McKinney, 2010 — Data Structures for Statistical Computing in Python
  - Official docs: pandas
---

## TL;DR
pandas gives Python a labelled, in-memory table, the DataFrame, built from typed column arrays plus an
index, with vectorized column math, group-by, joins, reshaping and readers for nearly every file format.
It is the default tool for exploring eval results, logs and datasets that fit in RAM. Its sharp edges
come from its age and flexibility: index alignment, copy-versus-view semantics, eager execution and
memory use several times the data size.

## Why it exists
NumPy arrays hold one dtype and address data by integer position. Real AI-engineering data is not like
that: an eval run produces rows with a case ID, model name, category string, pass/fail boolean, latency,
token counts and a timestamp. You want to ask "pass rate and p95 latency per model per category", join
in a price table, and filter to failures to read them. Doing that with lists of dicts means hand-written
loops for every group-by and join, slow and easy to get wrong; doing it with raw NumPy means keeping
parallel arrays aligned by hand.

SQL answers these questions well, but loading data into a database for a ten-minute analysis is
friction, and SQL is awkward for the iterative, look-at-it-and-adjust workflow of a notebook. pandas
brought the data-frame model familiar from R into Python: named columns of different types, row labels,
and a large vocabulary of vectorized operations, all in process with your other Python code. That is
why so much of the ecosystem, from scikit-learn inputs to plotting libraries, accepts DataFrames.

## How it works
### Columns, dtypes and the index
A DataFrame is essentially an ordered collection of columns (each a `Series`) sharing one `Index` of row
labels. Each column is backed by a NumPy array or an extension array, so numeric columns get NumPy speed.
Strings were historically stored in `object` columns, arrays of pointers to Python objects, which are
slow and memory-hungry; newer pandas versions offer dedicated string dtypes (pandas 3.0 makes a
string dtype the default for text) and, since pandas 2.0, optional Arrow-backed columns
(`dtype_backend="pyarrow"`) that store data in Arrow's columnar format.

Missing values are a historical compromise: a float column uses `NaN`, so an integer column that gains
one missing value silently becomes `float64`. Nullable dtypes (`Int64`, `boolean`, `string`) use a
separate mask instead, keeping the type.

### Alignment
Arithmetic between Series aligns on index *labels*, not positions. `a + b` matches rows with the same
label and yields `NaN` where a label exists on only one side. This was designed for time series, where
aligning by date is what you want; for freshly filtered frames with gappy indexes it causes silent NaNs,
which is why `reset_index(drop=True)` appears so often.

### Vectorized operations and their escape hatch
Column expressions such as `df["cost"] = df["tokens"] / 1000 * df["price"]` run as NumPy operations over
whole columns. `df.apply(f, axis=1)` calls a Python function per row, which brings back interpreter
speed, so it should be a last resort. String and datetime operations live under `.str` and `.dt`.

### Group-by, joins, reshaping
`groupby` implements split-apply-combine: split rows by key, apply an aggregation per group, combine
into a new frame. Named aggregation (`agg(pass_rate=("passed", "mean"))`) keeps output columns explicit.
`merge` performs SQL-style joins; its `validate=` argument checks the expected cardinality so a
duplicate key cannot silently multiply rows. `pivot_table` and `melt` switch between wide and long shapes.

### Indexing, copies and views
Use `.loc[row_mask, "col"]` for label-based selection and assignment and `.iloc` for positions. Chained
indexing such as `df[mask]["col"] = x` may assign into a temporary copy and leave `df` unchanged, the
origin of the `SettingWithCopyWarning`. pandas 3.0 made Copy-on-Write the default: every derived
object behaves as a copy and real copying is deferred until a write. That removes the ambiguity in one
direction: chained assignment *never* modifies `df`, and pandas emits a `ChainedAssignmentError`
warning instead. Code written for pandas 2.x that relied on chained assignment silently stops working.

### Execution model
pandas is eager: each operation materializes a full result immediately, mostly on a single core. With
intermediates, a common rule of thumb is to have several times the dataset's size available as RAM.
Data larger than memory needs chunked reading (`chunksize=`), a columnar engine such as Polars or DuckDB,
or a database.

## Example
Summarising an eval run: pass rate, tail latency and cost per model and category.

```python
import pandas as pd

df = pd.DataFrame({          # in practice: pd.read_json("results.jsonl", lines=True)
    "case_id":    [1, 1, 2, 2, 3, 3],
    "model":      ["small", "large"] * 3,
    "category":   ["refund", "refund", "billing", "billing", "refund", "refund"],
    "passed":     [False, True, True, True, False, True],
    "latency_ms": [420, 1310, 380, 1250, 450, 1400],
    "out_tokens": [120, 180, 90, 150, 130, 200],
})
price = pd.DataFrame({"model": ["small", "large"],
                      "usd_per_1k_out": [0.0006, 0.01]})     # illustrative prices

df = df.merge(price, on="model", how="left", validate="many_to_one")
df["cost_usd"] = df["out_tokens"] / 1000 * df["usd_per_1k_out"]   # column math, no loop

summary = (df.groupby(["model", "category"])
             .agg(pass_rate=("passed", "mean"),
                  p95_ms=("latency_ms", lambda s: s.quantile(0.95)),
                  cost_usd=("cost_usd", "sum"),
                  n=("case_id", "size"))
             .reset_index())
print(summary)

# Wrong: chained indexing may write into a temporary copy
# df[df["model"] == "small"]["passed"] = True
df.loc[df["model"] == "small", "reviewed"] = True             # right: one .loc call
failures = df.loc[~df["passed"], ["case_id", "model", "category"]]
```

The summary shows `small` passing 0.0 on refund (2 cases) and 1.0 on billing, while `large` passes
everything; refund cost for `large` is (180 + 200) / 1000 × 0.01 = 0.0038 USD versus 0.00015 for
`small`. That is the shape of a real model-choice discussion: the cheap model fails one category, so
route that category to the large one.

## Trade-offs
- An enormous API and ecosystem make most tasks one line away, but the API has many ways to do the same
  thing and inconsistent historical defaults, so code quality varies wildly across a team.
- Eager execution is easy to inspect cell by cell in a notebook, yet it materializes every intermediate;
  on data near RAM size, lazy engines such as Polars or DuckDB optimize and stream instead.
- Index-label alignment is ideal for time series, but on ad-hoc tables it silently introduces NaNs,
  so many teams treat the index as a plain row number and join explicitly.
- `object` dtype columns can hold anything, which tolerates messy data at the cost of speed and memory;
  converting to proper string, categorical or Arrow dtypes flips that once data grows.

## Pitfalls
- Using `apply(axis=1)` or `iterrows()` for arithmetic; the symptom is a notebook cell taking minutes on
  a few million rows where the vectorized expression takes under a second.
- Chained assignment like `df[mask]["col"] = x`; the symptom is `SettingWithCopyWarning` (pandas 2.x) or
  `ChainedAssignmentError` (pandas 3.x, Copy-on-Write) and a frame that is unchanged afterwards.
- Merging on a key that is not unique on the side you assumed; the symptom is the row count growing
  after a join and inflated totals, caught by `validate="many_to_one"`.
- One missing value turning an ID column into `float64`; the symptom is IDs printed as `1234.0` and
  failed joins against integer keys.
- Loading a multi-gigabyte CSV with default dtypes; the symptom is memory exhaustion, fixed by
  specifying dtypes, reading only needed columns, or switching to Parquet.

## When to use
Use pandas for exploratory analysis and reporting on data that fits comfortably in memory: eval results,
annotation exports, token and cost logs, small training sets, and anything feeding scikit-learn or a
plot. It is also the lingua franca for handing tables between libraries. Prefer Polars or DuckDB when
data approaches RAM size, when a pipeline runs repeatedly and speed matters, or when you want lazy,
query-optimized execution. Prefer a database when data is shared, updated concurrently or needs
durability. In interviews, expect questions on group-by, joins, vectorization and why chained
assignment fails.

## Role lens
- **Engineer:** Loads JSONL eval and trace data into DataFrames, slices failures by category, and writes
  vectorized, `.loc`-based transformations that survive Copy-on-Write.
- **Architect:** Decides where notebook-scale pandas analysis ends and production pipelines in Polars,
  Spark or SQL begin, and standardizes Parquet as the hand-off format.
- **FDE:** Pulls a customer's CSV or ticket export into pandas on day one to profile volumes, categories
  and data quality before scoping the solution.

## Check yourself
**Q:** A notebook computes per-ticket cost with `df.apply(lambda r: price[r.model] * r.tokens, axis=1)`
over 5 million rows and takes many minutes. Why is it slow and how do you fix it?
**A:** `apply` with `axis=1` builds a Series for each row and calls the Python lambda 5 million times,
so the work runs in the interpreter rather than in compiled column operations. Fix it by expressing the
lookup as a join or a vectorized map: `df["unit"] = df["model"].map(price)` (or merge a price table),
then `df["cost"] = df["unit"] * df["tokens"]`, both of which operate on whole columns. Also convert
`model` to a categorical dtype if it repeats, and check the merge or map left no NaNs for unknown models.

## Quiz
### Q: `a` has index [0, 1, 2] and `b` has index [1, 2, 3]. What does `a + b` return?
- [ ] An error, because the indexes differ
- [ ] Three values computed by position
- [x] Four rows labelled 0 to 3, with NaN for labels present on only one side
- [ ] Two rows, labels 1 and 2 only
> pandas aligns on labels and takes the union of the indexes, so labels 0 and 3 have no partner and become
> NaN. Positional addition requires `.to_numpy()` or resetting the indexes first.

### Q: Why does `df[df.score < 0.5]["flag"] = True` often fail to modify `df`?
- [ ] Boolean masks are read-only in pandas
- [x] The first indexing step can return a new object, and the assignment goes into that temporary copy
- [ ] The column must exist before assignment
- [ ] pandas requires `inplace=True` for all assignments
> Chained indexing performs two separate operations; the second writes into whatever the first returned,
> which may be a copy (under pandas 3's Copy-on-Write it always behaves as one). A single
> `df.loc[mask, "flag"] = True` modifies the original frame.

### Q: After a merge, an eval summary reports more rows than there were test cases. What is the most likely cause?
- [ ] The merge defaulted to an outer join
- [ ] groupby dropped NaN keys
- [x] The join key was duplicated in the right-hand table, multiplying matching rows
- [ ] Copy-on-Write duplicated the frame
> A many-to-many match repeats each left row once per matching right row. Passing `validate="many_to_one"`
> raises an error when the right side's keys are not unique, catching the problem at the join.
