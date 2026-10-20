---
id: generators
module: python-for-ai
name: Generators
kind: concept
difficulty: core
est_minutes: 15
status: crisp
related:
  - python-for-ai/async-await
  - python-for-ai/context-managers-resource-cleanup
  - python-for-ai/json-jsonl
  - python-for-ai/memory-and-performance-profiling
  - fastapi-for-ai/streamingresponse
  - serving-inference/streaming
  - rag-retrieval/embedding-pipelines
sources:
  - PEP 255 — Simple Generators
  - PEP 380 — Syntax for Delegating to a Subgenerator
  - PEP 525 — Asynchronous Generators
  - Official docs: Python language reference (yield expressions)
---

## TL;DR
A generator `yield`s values one at a time, pausing with its state intact. A 10 GB JSONL corpus or an
endless token stream becomes a lazy pipeline holding one item or batch at a time; async generators do
the same for LLM token streams.

## Why it exists
- **AI data is too big or too slow to materialise:** a corpus to chunk and embed, an eval set of 50,000
  prompts, a vector store export, server-sent events from a model.
- **Lists fail three ways.** Read every line, parse every document, chunk everything, then embed: memory
  grows with the dataset, the first embedding call waits for the whole corpus, and a crash at item
  900,000 loses all work in flight.
- **Hand-written iterator classes are inside-out.** `__iter__`, `__next__` and explicit state fields
  ("which file, which line, which chunk offset") turn a simple nested loop into an error-prone state
  machine.
- **Generators keep the natural loop.** The frame is suspended at `yield` and resumed on the next request,
  so locals, the open file and the loop position survive between items.
- **The consumer pulls**, so streaming, early results and bounded memory come for free.

## How it works
### The protocol
1. **Calling creates, does not run.** Calling a function that contains `yield` runs none of its body; it
   returns a generator object, which is an iterator.
2. **`next()` resumes** execution until the next `yield`, which hands a value out and freezes the frame.
3. **Return ends it.** The generator raises `StopIteration` (carrying the return value), and `for` loops
   treat that as the end.
4. **Single-pass.** Once exhausted, iterating again yields nothing, with no error.

**Mechanically:** the generator object owns a frame (locals, evaluation stack, position of the last
executed instruction). An ordinary call discards its frame on return; a generator keeps it alive between
`next()` calls, which is the whole trick.

| `inspect.getgeneratorstate(g)` | Meaning |
|---|---|
| `GEN_CREATED` | Before the first `next()` |
| `GEN_RUNNING` | Currently executing |
| `GEN_SUSPENDED` | Paused at a `yield` |
| `GEN_CLOSED` | Finished |

- **No re-entry.** A running generator cannot be resumed, so `next()` on one already executing (say from a
  second thread) raises `ValueError: generator already executing`.

### Pipelines compose lazily
- **Building does no work.** `chunks(read_jsonl(path))` builds a pipeline; nothing happens until the
  consumer asks for the first item. Then one line is read, parsed, chunked and passed down.
- **Memory is bounded by what each stage holds**, not by the dataset, *because* items flow one at a time.
- **Pull-based backpressure.** The consumer's `next()` propagates up the chain, so a slow consumer (an
  embedding API at its rate limit) automatically slows reading and nothing piles up between stages. That
  is backpressure without any queue.
- **Standard parts.** Generator expressions, `(f(x) for x in xs)`, are the inline form; `itertools`
  (`islice`, `chain`, and `batched` from 3.12) supplies the standard pipeline stages.

### Two-way communication and delegation
`yield` is an expression, so the caller can talk back:

| Call | Effect at the paused `yield` |
|---|---|
| `gen.send(value)` | Resumes; the `yield` evaluates to `value` |
| `gen.throw(exc)` | Raises the exception there |
| `gen.close()` | Raises `GeneratorExit` there, so `finally` blocks run |
| `yield from sub` (PEP 380) | Delegates to a sub-generator, forwarding sends and throws, evaluating to its return value |

- **`yield from` exists** *because* refactoring a generator into helpers would otherwise need hand-written
  forwarding.
- **Ancestor of coroutines.** This send-and-suspend machinery is where `async`/`await` came from:
  generator-based coroutines.

### Cleanup and async generators
- **When resources are released.** A generator holding a resource (an open file inside `with`) releases it
  when it finishes, when `close()` is called, or when it is garbage-collected, since `close()` runs the
  `finally`.
- **Early exit leaks until then.** If a consumer breaks out early and keeps a reference, the resource stays
  open.
- **Async generators** (PEP 525): `async def` with `yield`, consumed with `async for`. Their `finally` may
  need to await, so handle early exit with `contextlib.aclosing()` (3.10+) to close deterministically rather
  than leaving cleanup to the event loop's finaliser.

## Examples
### A lazy embedding pipeline
```python
import itertools, json
from collections.abc import Iterator

def read_jsonl(path: str) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:      # stays open while iterated
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                print(f"skipping malformed line {line_no}")

def chunks(docs: Iterator[dict], size: int = 800) -> Iterator[tuple[str, str]]:
    for doc in docs:
        text = doc["text"]
        for start in range(0, len(text), size):
            yield doc["id"], text[start:start + size]

def embed_corpus(path: str, batch_size: int = 64) -> int:
    sent = 0
    for batch in itertools.batched(chunks(read_jsonl(path)), batch_size):  # 3.12+
        # vectors = embed_api([text for _, text in batch])  -> upsert by id
        sent += len(batch)
    return sent
```

**Worked sizes (illustrative), 4 GB JSONL corpus:**
| | List-based | This pipeline |
|---|---|---|
| Held in memory | Parsed corpus plus every chunk, before the first API call | One line, one parsed document, one batch of 64 × 800 characters (about 50 KB of text) |
| First batch ready | After the whole corpus is parsed | Within milliseconds of starting |
| Malformed line | Often kills the job | Skipped and logged |

**See the laziness:** put a `print` in each stage and run it on a two-document file with 2,000-character
texts.
1. Output interleaves: read doc 1, chunk 1a, chunk 1b, chunk 1c, read doc 2, chunk 2a, and so on.
2. The batch closes once 64 chunks (or the end of input) arrive.
3. A list pipeline would print every "read" before the first "chunk".
4. The mechanism: `batched` pulls a chunk, `chunks` needs a document, `read_jsonl` resumes inside its `for`
   over the file, reads one line, and suspends again at `yield`, with the file still open and the line
   counter intact.

### Stop a token stream early, cleanly
An async generator stands in for a provider's SSE stream. With `aclosing`, breaking out runs the
generator's async `finally` before the next line, so the connection is released at a known point.

```python
import asyncio
from contextlib import aclosing

async def token_stream(prompt: str):
    print("open connection")
    try:
        for tok in ["The", " answer", " is", " 42", "."]:
            await asyncio.sleep(0.01)          # stands in for an SSE chunk arriving
            yield tok
    finally:
        await asyncio.sleep(0)                 # async cleanup, e.g. await response.aclose()
        print("connection closed")

async def first_n(n: int) -> str:
    out = []
    async with aclosing(token_stream("q")) as stream:
        async for tok in stream:
            out.append(tok)
            if len(out) == n:
                break                          # aclosing awaits aclose() as the block exits
    print("after block")
    return "".join(out)

print(asyncio.run(first_n(2)))
# open connection / connection closed / after block / The answer
```
Without `aclosing`, "connection closed" would print only when the event loop finalises the generator,
which under load means connections held open well past the request.

## Production patterns
- **Batch at the boundary to the API.** Feed generator stages into `itertools.batched(it, n)` (3.12+), or on
  older Pythons `while batch := tuple(islice(it, n)): ...`, sized to the provider's max inputs per call.
  Monitor peak RSS and items per second.
- **Checkpoint the position, not the data.** Yield the line number or record id with each item and persist the
  last fully processed one every N batches, so a crash at item 900,000 resumes instead of restarting.
  Monitor checkpoint age.
- **Count and quarantine bad records.** Replace the `print` in `read_jsonl` with a counter and a write to a
  dead-letter file. Alert on malformed-record rate, because a spike usually means an upstream format change.
- **Stream responses with an async generator.** Pass an async generator to FastAPI's `StreamingResponse`,
  open the upstream stream inside it, and release it in `finally`. Monitor open upstream connections and
  stream duration after client disconnects.
- **Paginate APIs as a generator.** Wrap cursor pagination in a generator that yields items and fetches the
  next page only when the consumer needs it, so `islice(items(), 100)` stops after the pages it used. Monitor
  pages fetched per job.

## Trade-offs
- **Flat memory vs single pass.** Lazy pipelines keep memory flat and deliver first results early, but each
  item is produced once. For random access, `len()` or several passes, materialise to a list or reopen the
  source.
- **Plain-loop readability vs late errors.** Streaming code reads like a plain loop, but errors surface at
  consumption time, not at the call, so a traceback may point at the consumer far from the faulty stage.
- **Negligible overhead vs numeric hot loops.** Per-item overhead is negligible for I/O-bound work, but over
  millions of small values vectorised NumPy or Polars operations are far faster than yielding Python objects.
- **Natural streams vs cleanup.** Async generators expose network streams naturally, at the cost of trickier
  cleanup when consumers stop early. For complex flows, a bounded `asyncio.Queue` between tasks can be clearer.

## Pitfalls
- **Iterating the same generator twice**, for example once to count and once to process. The second pass
  silently yields nothing and an eval reports zero results.
- **Expecting side effects from a call.** Nothing runs until iteration, so a "write records" generator that
  is never consumed writes nothing.
- **Breaking out of an async generator wrapping an HTTP stream without `aclosing`.** Symptom:
  connection-pool exhaustion or "unclosed" warnings under load.
- **`try/except` around the generator call.** Exceptions are raised later during iteration, outside the
  handler, so the guard never triggers.
- **Leaving a debugging `list(gen)` in.** Memory returns to dataset-sized and the job slows on large inputs.
- **Bare `next(it)` inside a generator on an iterator that may be exhausted.** Since PEP 479 the stray
  `StopIteration` becomes `RuntimeError: generator raised StopIteration` instead of silently ending the outer
  generator. Use `next(it, None)` or catch it explicitly.
- **Sharing one generator between threads or DataLoader-style workers.** Symptom:
  `ValueError: generator already executing` or duplicated and missing items. Give each worker its own source
  or put a `queue.Queue` between them.

## When to use
- **Use when** sequences are large, slow to arrive or potentially unbounded: reading JSONL and Parquet exports
  row by row, chunking documents for embedding, batching requests, paginating an API, streaming model output
  to a client. FastAPI's `StreamingResponse` and most SDK streaming interfaces accept or return (async)
  iterators.
- **Prefer a list when** the data is small and you need it more than once.
- **Measure:** peak memory before and after converting a list pipeline.
- **Interviews:** explain laziness, single-pass behaviour, `yield from`, and how async generators relate to
  token streaming.

## Role lens
- **Engineer:** Writes ingestion and eval pipelines as generator stages with batching, and wraps
  streaming responses in async generators closed with `aclosing`.
- **Architect:** Designs data and streaming paths so memory stays bounded by batch size, not dataset
  size, and so backpressure flows from the consumer to the source.
- **FDE:** Rewrites a customer's notebook that loads a whole export into memory into a streaming script
  that runs on their modest VM without crashing.

## Check yourself
**Q:** An eval script does `cases = (load(x) for x in paths)`, logs `sum(1 for _ in cases)` cases, then runs
the model over `cases`. It reports 500 cases loaded but zero scored. Explain and fix it.
**A:** `cases` is a generator expression, a single-pass iterator. Counting consumes it completely, so when
the scoring loop iterates it the generator is already exhausted and yields nothing, silently. The fix depends
on size: if the cases fit in memory, materialise once with `cases = [load(x) for x in paths]` and use
`len(cases)`; if not, count while scoring in the same pass, or rebuild the generator from `paths` for each
pass. A good answer also notes that generators raise no error on reuse, so a test asserting the scored
count equals the loaded count would have caught this.

## Quiz
### Q: What happens when you call a function whose body contains `yield`?
- [ ] The body runs until the first `yield` and returns that value
- [x] None of the body runs; a generator object is returned that runs the body on iteration
- [ ] The whole body runs eagerly and the yielded values are collected into a list
- [ ] It raises TypeError unless called inside a for loop
> The presence of `yield` makes the function a generator function. Calling it only creates the
> generator; execution starts on the first `next()` and pauses at each `yield`.

### Q: Why does a generator pipeline over a large JSONL file keep memory roughly constant?
- [ ] Because Python compresses generator state automatically
- [ ] Because generators store their outputs on disk instead of in memory
- [ ] Because the file is memory-mapped by the generator
- [x] Because each stage holds only the current item or batch, pulled on demand by the consumer
> Items flow one at a time from the source through each stage, so peak memory depends on batch size
> and per-item size, not on how many items the file contains.

### Q: What is the safest way to stop consuming an async generator that wraps an HTTP stream early?
- [ ] Delete the variable and rely on reference counting to close it immediately
- [x] Iterate it inside `contextlib.aclosing()` so its cleanup is awaited when the block exits
- [ ] Raise StopIteration inside the async for loop body
- [ ] Call `next()` on it until it is exhausted
> Async generator cleanup may need to await, which garbage collection cannot do synchronously.
> `aclosing` awaits `aclose()` deterministically, releasing the connection at a known point.
