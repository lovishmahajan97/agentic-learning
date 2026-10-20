---
id: generators
module: python-for-ai
name: Generators
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
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
A generator is a function that `yield`s values one at a time and pauses between them, keeping its local
state. It exists so you can process data lazily: a 10 GB JSONL corpus or an endless token stream becomes
a pipeline of small stages, each holding one item or batch in memory instead of the whole dataset. Async
generators do the same for streams that arrive over the network, which is how LLM token streaming is
usually exposed.

## Why it exists
AI work is full of sequences too large or too slow to materialise: a corpus to chunk and embed, an eval
set of 50,000 prompts, rows from a vector store export, server-sent events from a model. The naive
approach builds lists: read every line, parse every document, chunk everything, then embed. Memory grows
with the dataset, the first embedding call waits until the entire corpus is parsed, and a crash at item
900,000 loses all the work in flight.

The classic alternative is a hand-written iterator class with `__iter__`, `__next__` and explicit state
fields for "which file, which line, which chunk offset". It works, but the control flow that would be a
simple nested loop gets turned inside out into a state machine, which is error-prone and hard to read.

Generators let you write the natural loop and have the interpreter keep the state for you. The function's
frame is suspended at `yield` and resumed on the next request, so local variables, the open file and the
loop position survive between items. The consumer pulls items as fast as it can use them, which gives
streaming, early results and bounded memory for free.

## How it works
### The protocol
Calling a function that contains `yield` runs none of its body; it returns a generator object, which is
an iterator. Each `next()` resumes execution until the next `yield`, which hands a value out and freezes
the frame. When the function returns, the generator raises `StopIteration` (carrying the return value),
and `for` loops treat that as the end. A generator is single-pass: once exhausted, iterating it again
yields nothing, with no error.

Mechanically, the generator object owns a frame: the local variables, the evaluation stack and the
position of the last executed instruction. An ordinary call creates a frame and discards it on
return; a generator keeps its frame alive between `next()` calls, which is the whole trick. You can
watch the lifecycle with `inspect.getgeneratorstate(g)`: `GEN_CREATED` before the first `next()`,
`GEN_RUNNING` while executing, `GEN_SUSPENDED` at a `yield`, `GEN_CLOSED` once finished. A generator
cannot be resumed while it is running, so calling `next()` on one already executing, for example from
a second thread, raises `ValueError: generator already executing`.

### Pipelines compose lazily
Generators chain: `chunks(read_jsonl(path))` builds a pipeline, but no work happens until the consumer
asks for the first item. Then one line is read, parsed, chunked and passed down. Memory is bounded by
what each stage holds, not by the dataset, *because* items flow one at a time. The flow is
**pull-based**: the consumer's `next()` propagates up the chain, so a slow consumer (an embedding API
at its rate limit) automatically slows reading, and nothing piles up between stages. That is
backpressure without any queue. Generator expressions,
`(f(x) for x in xs)`, are the inline form; `itertools` (`islice`, `chain`, and `batched` from 3.12)
supplies the standard pipeline stages.

### Two-way communication and delegation
`yield` is an expression: `gen.send(value)` resumes the generator and makes the paused `yield` evaluate
to that value; `gen.throw(exc)` raises an exception at that point; `gen.close()` raises `GeneratorExit`
there so `finally` blocks run. `yield from sub` (PEP 380) delegates to a sub-generator, passing sends and
throws through and evaluating to its return value, *because* refactoring a generator into helpers would
otherwise need hand-written forwarding. This send-and-suspend machinery is the ancestor of coroutines:
`async`/`await` grew out of generator-based coroutines.

### Cleanup and async generators
A generator that holds a resource (an open file inside `with`) releases it when it finishes, when
`close()` is called, or when it is garbage-collected, since `close()` runs the `finally`. If a consumer
breaks out early and keeps a reference, the resource stays open until that happens. `async def` with
`yield` makes an async generator (PEP 525), consumed with `async for`; its `finally` may need to await,
so early exit should be handled with `contextlib.aclosing()` (3.10+) to close it deterministically
rather than leaving cleanup to the event loop's finaliser.

## Example
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
Illustrative sizes: for a 4 GB JSONL corpus, the list-based version holds the parsed corpus plus every
chunk in memory before the first API call. This pipeline holds one line, one parsed document and one
batch of 64 × 800 characters (about 50 KB of text) at a time, and the first batch is ready to send within
milliseconds of starting, instead of after the whole corpus is parsed. A malformed line is skipped and logged rather than killing a long job.

To see the laziness, put a `print` in each stage and run it on a two-document file with 2,000-character
texts. The output interleaves: read doc 1, chunk 1a, chunk 1b, chunk 1c, read doc 2, chunk 2a, and so on,
with the batch closing once 64 chunks (or the end of input) arrive. A list pipeline would print every
"read" before the first "chunk". The interleaving is the mechanism: `batched` pulls a chunk, `chunks`
needs a document, `read_jsonl` resumes inside its `for` over the file, reads one line, and suspends
again at `yield`, with the file still open and the line counter intact.

## Trade-offs
- Lazy pipelines keep memory flat and deliver first results early, but each item is produced once; if
  you need random access, `len()` or several passes, materialise to a list or reopen the source.
- Generators make streaming code read like a plain loop, but errors surface at consumption time, not
  at the call, so a traceback may point at the consumer far from the faulty stage.
- Per-item generator overhead is negligible for I/O-bound work, but for numeric hot loops over millions
  of small values, vectorised NumPy or Polars operations are far faster than yielding Python objects.
- Async generators expose network streams naturally, at the cost of trickier cleanup when consumers
  stop early; for complex flows a bounded `asyncio.Queue` between tasks can be clearer.

## Pitfalls
- Iterating the same generator twice, for example once to count and once to process; the second pass
  silently yields nothing and an eval reports zero results.
- Calling a generator function and expecting side effects; nothing runs until it is iterated, so a
  "write records" generator that is never consumed writes nothing.
- Breaking out of an async generator that wraps an HTTP stream without `aclosing`; the symptom is
  connection-pool exhaustion or "unclosed" warnings under load.
- Wrapping a generator call in `try/except` expecting to catch its errors; exceptions are raised later
  during iteration, outside the handler, so the guard never triggers.
- Converting the pipeline to a list for debugging (`list(gen)`) and leaving it in; memory use returns
  to dataset-sized and the job slows on large inputs.
- Calling `next(it)` inside a generator on an iterator that may be exhausted; since PEP 479 the stray
  `StopIteration` is converted to `RuntimeError: generator raised StopIteration` instead of silently
  ending the outer generator, so use `next(it, None)` or catch it explicitly.
- Sharing one generator between threads or DataLoader-style workers; the symptom is
  `ValueError: generator already executing` or duplicated and missing items, so give each worker its own
  source or put a `queue.Queue` between them.

## When to use
Use generators whenever you process sequences that are large, slow to arrive or potentially unbounded:
reading JSONL and Parquet exports row by row, chunking documents for embedding, batching requests,
paginating through an API, and streaming model output to a client. FastAPI's `StreamingResponse` and most
SDK streaming interfaces accept or return (async) iterators. Prefer a list when the data is small and you
need it more than once. Measure peak memory before and after converting a list pipeline. In interviews,
expect to explain laziness, single-pass behaviour, `yield from`, and how async generators relate to
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
