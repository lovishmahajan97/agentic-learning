---
id: pandas
module: python-for-ai
name: pandas
kind: tool
difficulty: core
est_minutes: 15
status: agentic-verified
agent_relevance: useful
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
pandas is where you read your agent's behaviour: load traces and eval results into a DataFrame and ask
which tool fails, which step burns tokens, which model passes. It is the default for data that fits in
RAM; watch alignment, copies and memory.

## For agent builders
- **Why it matters for agents.** Agent quality is found in the traces: one row per step or per run with
  tool name, status, latency, tokens and cost. Error analysis ("which tool errors most in the refund
  category?") is one `groupby`, and model-choice decisions come from the summary table.
- **Where it shows up in your stack.** Notebooks over trace exports from LangSmith, Langfuse or your own
  JSONL logs; eval harness outputs; cost reports; scikit-learn inputs. Some agents also expose a
  DataFrame as a tool, such as LangChain's experimental pandas agent, which executes model-written Python
  and needs a sandbox.
- **What you control.** Dtypes at read time, `validate=` on every join (a price or label table), explicit
  `dropna=False` in reports, and vectorized column math instead of `apply`.
- **What you can skip.** MultiIndex gymnastics, time-series resampling and most of the API. Reading,
  joining, grouping and safe assignment cover agent analysis.

## Why it exists
- **Agent data is mixed-type rows.** An eval run produces a case ID, model name, category string, pass/fail
  boolean, latency, token counts, steps taken, tools called and a timestamp. NumPy arrays hold one dtype
  and address data by integer position.
- **The questions are tabular.** "Pass rate and p95 latency per model per category", "error rate per
  tool", join in a price table, filter to failures to read them.
- **The naive tools fail.** Lists of dicts mean hand-written loops for every group-by and join, slow and
  easy to get wrong; raw NumPy means keeping parallel arrays aligned by hand.
- **SQL is friction here.** Loading data into a database for a ten-minute analysis is overhead, and SQL is
  awkward for the iterative, look-at-it-and-adjust workflow of a notebook.
- **pandas brought R's data-frame model into Python.** Named columns of different types, row labels and a
  large vocabulary of vectorized operations, in process with your other code, which is why so much of the
  ecosystem, from scikit-learn inputs to plotting libraries, accepts DataFrames.

## How it works
### Columns, dtypes and the index
- **Structure.** An ordered collection of columns (each a `Series`) sharing one `Index` of row labels.
- **Storage.** Each column is a NumPy array or an extension array, so numeric columns get NumPy speed.

| Column kind | Storage | Note |
|---|---|---|
| Numeric | NumPy array | Compiled speed |
| Strings, historically | `object`: pointers to Python objects | Slow and memory-hungry; trace text (prompts, tool outputs) lives here |
| Strings, newer | Dedicated string dtypes | pandas 3.0 makes a string dtype the default for text |
| Arrow-backed (since pandas 2.0, optional) | `dtype_backend="pyarrow"` | Arrow's columnar format |

### Missing values
- **Float columns use `NaN`,** so an integer column that gains one missing value silently becomes
  `float64` (a historical compromise), for example `tokens` on a step that timed out.
- **Nullable dtypes (`Int64`, `boolean`, `string`)** use a separate mask instead, keeping the type.

### Alignment
- **Arithmetic aligns on index *labels*, not positions.** `a + b` matches rows with the same label and
  yields `NaN` where a label exists on only one side.
- **Why.** It was designed for time series, where aligning by date is what you want.
- **The cost.** On freshly filtered frames with gappy indexes it causes silent NaNs, which is why
  `reset_index(drop=True)` appears so often.

### Vectorized operations and their escape hatch
- **Column expressions** such as `df["cost"] = df["tokens"] / 1000 * df["price"]` run as NumPy operations
  over whole columns.
- **`df.apply(f, axis=1)`** calls a Python function per row, bringing back interpreter speed: a last resort.
- **String and datetime operations** live under `.str` and `.dt`, for example
  `df["tool_output"].str.contains("timeout")` to tag failed steps.

### Group-by, joins, reshaping
| Tool | What it does |
|---|---|
| `groupby` | Split-apply-combine: split rows by key, aggregate per group, combine into a new frame |
| Named aggregation `agg(pass_rate=("passed", "mean"))` | Keeps output columns explicit |
| `merge` | SQL-style joins; `validate=` checks cardinality so a duplicate key cannot silently multiply rows |
| `pivot_table` / `melt` | Switch between wide and long shapes, e.g. one column per model for a side-by-side eval |

How `groupby` runs:
1. **Factorize** the key columns: one hash pass maps each distinct key to an integer group code.
2. **Aggregate.** Built-ins named by string (`"mean"`, `"sum"`, `"size"`) run a compiled loop adding each
   row into its group's accumulator, so cost is roughly linear in rows whatever the number of groups.
3. **Custom functions are slower.** A lambda is called once per group with a Series: fine for tens of
   groups, slow for a million (one group per agent run is easily that many).

Two defaults matter: `dropna=True` drops rows whose key is missing, and for categorical keys `observed=`
decides whether empty categories appear as rows.

### Indexing, copies and views
- **Select and assign with `.loc[row_mask, "col"]`** for labels, `.iloc` for positions.
- **Chained indexing** such as `df[mask]["col"] = x` may assign into a temporary copy and leave `df`
  unchanged, the origin of the `SettingWithCopyWarning`.
- **pandas 3.0 made Copy-on-Write the default.** Every derived object behaves as a copy and real copying
  is deferred until a write.
- **That removes the ambiguity in one direction.** Chained assignment *never* modifies `df`, and pandas
  emits a `ChainedAssignmentError` warning instead. Code for pandas 2.x that relied on chained assignment
  silently stops working.

### Execution model
- **Eager.** Each operation materializes a full result immediately, mostly on a single core.
- **RAM rule of thumb.** With intermediates, have several times the dataset's size available; traces with
  full prompts and tool outputs are mostly text and get large fast.
- **Bigger than memory?** Chunked reading (`chunksize=`), a columnar engine such as Polars or DuckDB, or a
  database.

## Diagram
A typical agent-trace analysis pipeline, with the group-by split shown: built-in aggregations stay in
compiled code, while a lambda drops back to one Python call per group.

```mermaid
flowchart TD
    R["Read trace JSONL, pinned dtypes"] --> J["merge, validate=many_to_one"]
    J --> C["Vectorized column math"]
    C --> G["groupby: keys to group codes"]
    G --> Q{"Aggregation kind?"}
    Q -->|"built-in: mean, sum, size"| F["Compiled loop, linear in rows"]
    Q -->|"lambda or custom"| S["One Python call per group"]
    F --> O["Summary frame, reset_index"]
    S --> O
    O --> P[("Parquet hand-off")]
```

## Examples
### Agent trace analysis: which tool fails, and what does a run cost?
One row per agent step, as exported from a tracing tool or your own JSONL. Two group-bys answer the
questions an agent team asks every week.

```python
import pandas as pd

steps = pd.DataFrame({       # in practice: pd.read_json("traces.jsonl", lines=True)
    "run_id":   ["r1", "r1", "r1", "r2", "r2", "r3", "r3", "r3", "r3"],
    "step":     [1, 2, 3, 1, 2, 1, 2, 3, 4],
    "tool":     ["search", "lookup_order", None, "search", None,
                 "lookup_order", "lookup_order", "lookup_order", None],
    "status":   ["ok", "error", "ok", "ok", "ok", "error", "error", "ok", "ok"],
    "in_tokens":  [3000, 3600, 4100, 2900, 3400, 3100, 3700, 4300, 4900],
    "out_tokens": [80, 60, 220, 70, 180, 50, 50, 60, 240],
}).astype({"tool": "string", "status": "category"})

per_tool = (steps.dropna(subset=["tool"])
                 .groupby("tool")
                 .agg(calls=("step", "size"),
                      error_rate=("status", lambda s: (s == "error").mean()))
                 .reset_index())

per_run = (steps.groupby("run_id")
                .agg(n_steps=("step", "max"), in_tokens=("in_tokens", "sum"),
                     out_tokens=("out_tokens", "sum"))
                .reset_index())
per_run["cost_usd"] = (per_run["in_tokens"] / 1000 * 0.00015
                       + per_run["out_tokens"] / 1000 * 0.0006)   # illustrative prices
print(per_tool, per_run, sep="\n")
```

| Finding | Number | Action |
|---|---|---|
| `lookup_order` error rate | 3 of 4 calls = 0.75 | Read those tool results; likely a bad argument schema |
| Run `r3` | 4 steps, 16,000 input tokens | Two retries of the same failing tool added two steps of re-sent context |
| Input tokens per step | grows ~500-600 per step | The transcript grows each step; see prompt caching |

### Eval summary per model and category
Summarising an agent eval run: pass rate, tail latency and cost per model and category.

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

| model | category | pass_rate | cost_usd |
|---|---|---|---|
| small | refund (2 cases) | 0.0 | 0.00015 |
| small | billing | 1.0 | |
| large | refund | 1.0 | (180 + 200) / 1000 × 0.01 = 0.0038 |
| large | billing | 1.0 | |

That is the shape of a real model-choice discussion: the cheap model fails one category, so route that
category to the large one.

### Worked scenario: loading a large usage log without exhausting memory
A token-usage export from a busy agent fleet is too big to load with default dtypes. Read only needed
columns, pin compact dtypes, and aggregate chunk by chunk.

```python
import pandas as pd

cols = ["ts", "model", "route", "out_tokens", "latency_ms"]
dtypes = {"model": "category", "route": "category",
          "out_tokens": "int32", "latency_ms": "float32"}

parts = []
for chunk in pd.read_csv("usage.csv", usecols=cols, dtype=dtypes,
                         parse_dates=["ts"], chunksize=1_000_000):
    parts.append(chunk.groupby(["model", "route"], observed=True)
                      .agg(calls=("latency_ms", "size"), out_tokens=("out_tokens", "sum")))

usage = pd.concat(parts).groupby(level=["model", "route"], observed=True).sum()  # combine partial sums
print(usage.sort_values("out_tokens", ascending=False).head())
```

Why the dtypes matter, per 10 million rows (bytes are exact for fixed-width types):

| Column | Default dtype | Pinned dtype | Memory |
|---|---|---|---|
| `out_tokens` | int64, 8 B/row = 80 MB | int32, 4 B/row | 40 MB |
| `latency_ms` | float64 = 80 MB | float32 | 40 MB |
| `model` (a handful of values) | `object`: 8 B pointer per row plus Python string objects (~50 B each) | `category`: 1 B code per row | ~10 MB |

Check the real numbers with `df.memory_usage(deep=True)`.

## Production patterns
- **Weekly trace error analysis.** Export a sample of production traces, group by tool and by category,
  read the failing rows, and file fixes against the tool or prompt. Track per-tool error rate and steps
  per run week over week; a rising step count often means a tool that silently started failing.
- **Pin dtypes and columns at read time.** Pass `usecols=`/`dtype=` to `read_csv`, or `columns=` to
  `read_parquet`, and use `category` for repeated strings such as tool and model names. Log
  `df.memory_usage(deep=True).sum()` per stage; a jump means a column fell back to `object` or `float64`.
- **Guard every join.** Use `merge(..., validate="many_to_one")` and assert the row count is unchanged
  after a left join to a price, label or rubric table. Monitor rows-before versus rows-after in pipeline
  logs, since inflated totals are the classic silent failure.
- **Nullable IDs everywhere.** Read ID columns as `Int64` or `string` (`dtype={"case_id": "Int64"}`, or
  `df.convert_dtypes()`), so one missing value cannot turn IDs into `1234.0`. Watch join match rates.
- **Opt into Copy-on-Write before upgrading.** On pandas 2.x set `pd.set_option("mode.copy_on_write",
  True)` in tests and fix every chained assignment it flags. Track `ChainedAssignmentError` and
  `SettingWithCopyWarning` counts in CI output; aim for zero.
- **Hand off as Parquet, not CSV.** Write `df.to_parquet(path, index=False)` so dtypes survive the trip
  to the next notebook or job. Monitor file sizes and read times, and keep `dropna=False` explicit in
  report group-bys so missing categories (and steps with no tool) stay visible.

## Trade-offs
- **Huge API vs consistency.** Most tasks are one line away, but there are many ways to do the same thing
  and inconsistent historical defaults, so code quality varies wildly across a team.
- **Eager vs lazy.** Eager execution is easy to inspect cell by cell, yet materializes every intermediate;
  near RAM size, lazy engines such as Polars or DuckDB optimize and stream instead.
- **Label alignment vs surprise NaNs.** Ideal for time series, but on ad-hoc tables it silently introduces
  NaNs, so many teams treat the index as a plain row number and join explicitly.
- **`object` flexibility vs cost.** It holds anything and tolerates messy data (nested tool arguments,
  long outputs) at the cost of speed and memory; converting to string, categorical or Arrow dtypes flips
  that once data grows.
- **DataFrame as an agent tool vs safety.** Letting the model write pandas code answers open questions
  over a table, but it is arbitrary code execution; a fixed set of query tools is safer when the questions
  are known.

## Pitfalls
- **`apply(axis=1)` or `iterrows()` for arithmetic.** Symptom: a cell taking minutes on a few million rows
  where the vectorized expression takes under a second. Fix: column expressions, `map` or `merge`.
- **Chained assignment like `df[mask]["col"] = x`.** Symptom: `SettingWithCopyWarning` (pandas 2.x) or
  `ChainedAssignmentError` (pandas 3.x, Copy-on-Write) and a frame unchanged afterwards. Fix: one `.loc`.
- **Merging on a key not unique on the side you assumed.** Symptom: row count grows after a join and
  totals inflate. Fix: `validate="many_to_one"` catches it.
- **One missing value turning an ID column into `float64`.** Symptom: IDs printed as `1234.0` and failed
  joins against integer keys. Fix: nullable `Int64`.
- **A multi-gigabyte CSV with default dtypes.** Symptom: memory exhaustion. Fix: specify dtypes, read only
  needed columns, or switch to Parquet; `df.memory_usage(deep=True)` shows which columns (usually `object`
  strings) are responsible.
- **Grouping a column with missing values under default `dropna=True`.** Symptom: per-group counts sum to
  fewer rows than the frame, because cases with no category (or steps with no tool, the final answer)
  vanished from the report. Fix: pass `dropna=False` or fill the key first.

## When to use
- **Use when:** exploratory analysis and reporting on data that fits comfortably in memory: agent traces,
  eval results, annotation exports, token and cost logs, small training sets, anything feeding
  scikit-learn or a plot. It is also the lingua franca for handing tables between libraries.
- **Prefer Polars or DuckDB when:** data approaches RAM size, a pipeline runs repeatedly and speed
  matters, or you want lazy, query-optimized execution.
- **Prefer a database when:** data is shared, updated concurrently or needs durability, such as live agent
  state or memory.
- **Interviews:** expect group-by, joins, vectorization and why chained assignment fails.

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

## Interview questions
### What is a DataFrame, and why reach for pandas instead of lists of dicts or raw NumPy?
- **Definition:** an ordered set of typed columns, each a `Series`, sharing one `Index` of row labels,
  with numeric columns stored as NumPy arrays.
- **Why:** eval and log data are mixed-type rows, and the questions (pass rate per model per category,
  join a price table) are one `groupby` or `merge` instead of hand-written loops or parallel arrays kept
  aligned by hand.
- **Ecosystem:** scikit-learn, plotting libraries and most readers accept or produce DataFrames.

### Your agent's average cost per run rose 40% this week. How would you use traces to find out why?
- **Shape the data:** load one row per step with run ID, tool, status and token counts; aggregate per run
  (steps, input and output tokens, cost) and compare the two weeks' distributions, not just means.
- **Slice:** group by tool and status to find a tool whose error rate rose, since failing tools cause
  retries and longer runs; group by route or category to see whether traffic mix changed.
- **Confirm in rows:** read the failing traces themselves before fixing, then add those cases to the eval
  set so the regression is caught next time.

### Cost totals inflated after a join. How do you find and prevent the cause?
- **Likely cause:** the key is not unique on the side you assumed, so a left join multiplied rows.
- **Find it:** compare row counts before and after the merge, and check duplicate keys with
  `df.duplicated(subset=[key]).sum()` on the lookup table.
- **Prevent it:** pass `validate="many_to_one"` so pandas raises instead of multiplying rows, and log
  rows-before versus rows-after in the pipeline.

### Why does `df[mask]["col"] = x` fail to update `df`, and what changed in pandas 3.0?
- **Chained indexing:** the first `[]` may return a temporary copy, so the assignment lands there and
  `df` is unchanged; pandas 2.x warns with `SettingWithCopyWarning`.
- **pandas 3.0 Copy-on-Write:** every derived object behaves as a copy, so chained assignment never
  modifies `df` and pandas emits `ChainedAssignmentError` instead.
- **Fix:** one `.loc[mask, "col"] = x` call; enable `mode.copy_on_write` on 2.x in tests before upgrading.

### Would you give an agent a pandas DataFrame as a tool? What are the risks?
- **The appeal:** a model writing pandas code can answer open-ended questions over a table without you
  pre-building every query.
- **The risk:** it is arbitrary code execution driven by model output (and by any text the model read),
  so it needs a sandbox with no secrets, no network and resource limits.
- **The usual alternative:** a few typed tools (filter, aggregate by column, top-n) with validated
  arguments, which are safer and easier to evaluate when the question types are known.

### A 10-million-row usage CSV exhausts memory. How do you cut it down, and when would you leave pandas?
- **Read less:** pass `usecols=` for the needed columns and `chunksize=` to aggregate chunk by chunk.
- **Pin dtypes:** `int32` and `float32` take 40 MB per column instead of 80 MB in int64 or float64, and a
  repeated string as `category` costs about 1 byte per row; `df.memory_usage(deep=True)` shows offenders.
- **Leave pandas when:** data nears RAM size or the pipeline repeats (Polars or DuckDB optimize lazily and
  stream), or data is shared and concurrently updated (a database).

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

## Use cases & projects
### Use cases
- **Agent error analysis.** Group step-level traces by tool and status to find which tool fails most,
  then filter to those rows and read the transcripts.
- **Model comparison.** Pass rate, p95 latency and cost per run, per model and per category, from one
  eval results table.
- **Cost reports.** Join token counts to a per-model price table with `validate="many_to_one"` and sum
  cost per tenant or per day.
- **Building eval sets.** Sample failed production runs by category into a labelled dataset for the next
  eval round.

### Mini project
**Error analysis notebook over agent traces (1-3 hours).**
- **Goal:** turn a JSONL trace export of an agent into a table that says where it fails and what it
  costs.
- **Steps:** load the traces with explicit dtypes; compute per-run steps, tokens and pass or fail; join
  a price table with `validate=`; group by model and category with `dropna=False`; list the five tools
  with the highest error rate; pull ten failing runs for manual reading.
- **Done when:** one summary table shows pass rate, p95 latency and cost per model and category, the
  join raises if the price table has duplicate keys, and you have written down two concrete fixes from
  the failing runs.
