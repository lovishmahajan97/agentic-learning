---
id: json-jsonl
module: python-for-ai
name: JSON / JSONL
kind: concept
difficulty: intro
est_minutes: 12
status: fact-checked
related:
  - prompting/structured-outputs-json-mode
  - python-for-ai/pydantic
  - python-for-ai/generators
  - python-for-ai/pyarrow
  - evals/golden-dataset
  - training-adaptation/supervised-fine-tuning
  - agent-reliability/structured-event-streams
  - observability/logging
sources:
  - Bray, 2017 — RFC 8259: The JavaScript Object Notation (JSON) Data Interchange Format
  - Official docs: Python json module
  - Official docs: JSON Lines
---

## TL;DR
JSON is the lingua franca of LLM systems: API payloads, tool arguments, structured outputs. JSONL (JSON
Lines) puts one complete JSON value on each line, which makes a file appendable, streamable and
splittable, so it is the standard container for eval sets, fine-tuning data, batch requests and logs.
Use JSON for one document, JSONL for many records; and know the edges (NaN, big integers, encoding)
where Python's defaults produce output other parsers reject.

## Why it exists
Almost everything an AI engineer moves around is a record with nested, loosely structured fields: a
chat transcript with a variable number of messages, a tool call with arguments, a model response with
metadata. CSV cannot represent nesting without inventing an escaping scheme, and binary formats need a
schema up front and are not readable in a terminal. JSON represents nested objects and arrays directly,
every language parses it, and LLMs produce it reliably enough that providers expose JSON modes.

But a single JSON document is a bad container for *many* records. A file holding one big array
`[{...}, {...}, ...]` must be parsed in full before you can use the first record, cannot be appended to
without rewriting the closing bracket, cannot be split across workers without parsing, and is corrupted
as a whole if a writer crashes before the final `]`. With a million eval cases that means holding the
whole dataset in memory just to read one.

JSONL fixes this with one rule: each line is a self-contained JSON value, separated by `\n`. Appending
is writing a line. Reading is iterating lines. Splitting is `split -l`. A crash damages at most the last
line. Because standard JSON encoders escape newlines inside strings as `\n`, any JSON value can be
serialized onto one line, so the rule costs nothing in expressiveness.

## How it works
### JSON's data model
RFC 8259 defines six types: object, array, string, number, `true`/`false`, `null`. There are no comments,
no trailing commas, no dates, no bytes and no NaN or Infinity. Strings are Unicode and, for interchange,
the text must be UTF-8. Python's `json` maps these to `dict`, `list`, `str`, `int`/`float`, `bool` and
`None`. Anything else (datetimes, `Decimal`, sets, most numpy scalars such as `np.int64` and
`np.float32`) raises `TypeError` on `dumps` unless you convert it (`np.float64` slips through only
because it subclasses Python `float`), typically with a `default=` function or by serializing a
Pydantic model with `model_dump_json()`.

### The edges where Python's defaults bite
- **NaN and Infinity.** `json.dumps(float("nan"))` emits `NaN`, which is *not* valid JSON; JavaScript's
  `JSON.parse` and strict parsers reject it. Pass `allow_nan=False` to fail at write time instead.
- **Big integers.** Python round-trips any integer exactly, but parsers that use IEEE doubles (JavaScript)
  lose precision above 2^53. IDs from Snowflake-style generators should travel as strings.
- **Non-ASCII.** `ensure_ascii=True` is the default, so `café` is written as `caf\u00e9`. That is valid,
  but inflates files and hurts readability for multilingual data; `ensure_ascii=False` writes UTF-8, which
  is correct as long as the file is opened with `encoding="utf-8"`.
- **Duplicate keys.** The RFC leaves them undefined; Python keeps the last one silently.

### JSONL mechanics
Write: `f.write(json.dumps(obj) + "\n")` per record, with no pretty-printing, because `indent=` inserts
newlines and breaks the one-record-per-line rule. Read: iterate the file object line by line and
`json.loads` each, which keeps memory proportional to one record. Writers should treat the file as
append-only and flush per record or per batch; readers should report errors with line numbers so a bad
record can be located, and decide explicitly whether to skip or stop. Compression composes cleanly:
`gzip.open(path, "wt")` gives `.jsonl.gz` with the same line semantics.

### Where you meet it
OpenAI-style fine-tuning datasets are JSONL with one `{"messages": [...]}` object per line; the OpenAI
Batch API takes a JSONL file of requests, each with a `custom_id` so results can be matched back; eval
harnesses store cases and results as JSONL; structured logs are usually one JSON object per line. For
analytics over millions of rows, JSONL is usually converted to a columnar format such as Parquet, since
re-parsing text on every query is slow.

## Example
Writing an eval set safely and reading it back as a stream, with failures pinned to a line.

```python
import json, math
from pathlib import Path

cases = [
    {"id": "q1", "messages": [{"role": "user", "content": "Refund policy?\nAsk twice."}],
     "expected": "policy", "score": 0.9},
    {"id": "q2", "messages": [{"role": "user", "content": "Café opening hours"}],
     "expected": "hours", "score": math.nan},          # a bad value we must catch
]

def write_jsonl(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            # allow_nan=False refuses to emit the invalid token NaN
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")

def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: {e.msg}") from e

cases[1]["score"] = None                               # fix: null is valid JSON
p = Path("eval.jsonl")
write_jsonl(p, cases)
print(p.read_text(encoding="utf-8").count("\n"))       # 2: the embedded newline was escaped
for case in read_jsonl(p):                             # one record in memory at a time
    print(case["id"], len(case["messages"]))
```

Without the fix, `write_jsonl` raises `ValueError: Out of range float values are not JSON compliant`
on the second record, at write time, instead of producing a file that a TypeScript dashboard later
refuses to parse.

## Trade-offs
- JSONL streams and appends record by record, but loses the single-document guarantees: there is no
  enclosing schema, so a malformed middle line is only found when a reader reaches it.
- Text JSON is human-readable and universal, yet it is several times larger and slower to parse than
  columnar binary formats; once you run repeated analytical queries over millions of rows, Parquet wins.
- Schemaless records make it easy to add fields to eval or log data, but readers drift silently
  unless a Pydantic model or JSON Schema validates each line at the boundary.
- `ensure_ascii=False` keeps multilingual data compact and readable, but any consumer that guesses the
  wrong encoding will mangle it; ASCII escapes are safer when you do not control the reader.

## Pitfalls
- Using `json.dump(obj, f, indent=2)` in a JSONL writer; the symptom is a `JSONDecodeError` on the very
  first line (a lone `{` is not a complete value) because each record now spans many lines.
- Serializing NaN from pandas or numpy metrics; the symptom is a JavaScript or Go consumer rejecting
  the file while Python reads it fine.
- Calling `f.readlines()` or building `[json.loads(l) for l in f]` on a multi-gigabyte JSONL file; the
  symptom is memory climbing to several times the file size (parsed Python objects are far larger than
  their text) and the process being OOM-killed. (`json.load` on JSONL fails outright with "Extra data".)
- Opening files without `encoding="utf-8"`; the symptom is `UnicodeDecodeError` or mojibake on
  Windows or in containers with a non-UTF-8 locale.
- Passing numpy types into `json.dumps`; the symptom is `TypeError: Object of type float32 is not JSON
  serializable` deep inside an eval run.

## When to use
Use JSON for single payloads: API requests and responses, tool arguments, config documents, a model's
structured output. Use JSONL whenever you have a collection of records that grows over time or is
processed one at a time: eval datasets, fine-tuning data, batch-inference inputs and outputs, agent
traces and logs. Validate each line at ingestion with a Pydantic model so bad records fail with a line
number. Move to Parquet or a database when you need column-wise queries, joins or typed schemas across
millions of rows. Interviewers often ask why JSONL rather than a JSON array; the answer is streaming,
appending and crash isolation.

## Role lens
- **Engineer:** Writes JSONL readers as generators with line-numbered errors, sets `allow_nan=False`, and
  validates every record against a Pydantic model before it enters an eval or training run.
- **Architect:** Chooses where JSONL is the interchange format and where data is promoted to Parquet or
  a database, and defines the record schema and versioning for datasets.
- **FDE:** Converts a customer's CSV or ticket export into a validated JSONL eval set, catching encoding
  and malformed-row problems before they poison results.

## Check yourself
**Q:** Your batch-inference job writes 2 million results to a single JSON array file, and a pod restart at
90% leaves it unusable. Redesign it and explain why each choice helps.
**A:** Write results as JSONL, one object per line, each carrying the request's `custom_id`. A restart
then damages at most the final partial line, and the reader can drop a last line that fails to parse. On
resume, read the existing file, collect completed IDs and only submit the rest, which makes the job
idempotent. Flush after each batch so durability matches progress. Downstream consumers can stream the
file without loading 2 million objects, and shards can be split by line for parallel scoring. If the
results feed analytics, convert the finished JSONL to Parquet.

## Quiz
### Q: Why is JSONL preferred over a single JSON array for a large, growing eval dataset?
- [ ] JSONL files are always smaller than equivalent JSON arrays
- [x] Each line parses independently, so the file can be streamed, appended to and split without parsing the whole thing
- [ ] JSONL supports comments and trailing commas that JSON forbids
- [ ] JSONL stores numbers in binary, which is faster to parse
> The size and syntax are essentially the same as JSON. The advantage is structural: one record per line
> means constant memory reads, appends without rewriting, line-based sharding and crash damage limited
> to one line.

### Q: What does Python's `json.dumps({"x": float("nan")})` produce by default?
- [ ] `{"x": null}`, because NaN maps to null
- [ ] A `TypeError` because NaN is not serializable
- [x] `{"x": NaN}`, which is not valid JSON and strict parsers reject
- [ ] `{"x": "NaN"}`, a string representation
> Python defaults to `allow_nan=True` and emits the JavaScript-style token NaN, which RFC 8259 does not
> allow. Setting `allow_nan=False` raises a ValueError instead, catching the bad value at write time.

### Q: A JSONL writer uses `json.dumps(record, indent=2)`. What breaks?
- [ ] Nothing; readers ignore whitespace inside JSON values
- [ ] Only non-ASCII characters are affected
- [ ] The file becomes valid JSON instead of JSONL
- [x] Each record spans several lines, so line-by-line readers fail to parse
> Indentation inserts newlines between keys. JSON parsers tolerate whitespace inside one document, but
> JSONL readers split on newlines first, so each fragment is an incomplete value and parsing fails.
