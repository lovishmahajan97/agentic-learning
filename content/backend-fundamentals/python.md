---
id: python
module: backend-fundamentals
name: Python
kind: tool
difficulty: intro
est_minutes: 10
status: fact-checked
related:
  - python-for-ai/async-await
  - python-for-ai/gil
  - python-for-ai/asyncio-vs-threads-vs-multiprocessing
  - python-for-ai/pydantic
  - python-for-ai/type-hints
  - python-for-ai/uv-poetry-pip
  - backend-fundamentals/fastapi
  - backend-fundamentals/node-js-typescript
sources:
  - Official docs: Python (asyncio, concurrent.futures)
  - PEP 703 — Making the Global Interpreter Lock Optional in CPython
  - Official docs: Pydantic
---

## TL;DR
This is an orientation lesson; the deep dive is the `python-for-ai` module. Python is the default
language of AI backends not because it is fast but because every model library, tokenizer and
provider SDK is Python-first, and because an AI service spends almost all of its time waiting on a
model rather than executing Python. `asyncio` turns that waiting into cheap concurrency; the GIL
only bites on CPU-bound pure-Python work, which you push to native libraries or other processes.

## Why it exists
An AI service is glue: it takes a request, builds a prompt, calls a model (or several), validates the
structured answer, maybe hits a vector store and a tokenizer along the way, and streams a result back.
Each of those pieces exists first, and usually only, as a Python library: PyTorch, Hugging Face,
sentence-transformers, tiktoken, every provider's official SDK, the eval harnesses, the notebooks the
research team used to find the prompt that works. Choosing another language for the backend means
reimplementing those pieces, shelling out to Python anyway, or maintaining two stacks.

The obvious objection is "Python is slow, serve it in Go or Java". It fails on arithmetic. A model call
takes hundreds of milliseconds to tens of seconds; the request-handling overhead that a faster
language would shave is measured in microseconds to low milliseconds. The hot loops that matter, matrix multiplies and
tokenization, already run in C, C++, Rust or CUDA underneath the Python API. What Python must do well
is wait on many model calls at once, and `asyncio` does exactly that.

The opposite objection, "Python cannot do concurrency because of the GIL", fails for the same reason:
the GIL is released while a thread is blocked on a socket, and an event loop does not need threads at
all to hold thousands of in-flight requests. The GIL is a real limit only for CPU-bound pure-Python
loops, and the answer there is a native library or a separate process, not a different language.

## How it works
### Execution model and the GIL
CPython compiles source to bytecode and interprets it. The Global Interpreter Lock lets only one
thread execute Python bytecode at a time *because* the reference-counting memory model is not
thread-safe without it. Threads still help for I/O: the interpreter drops the GIL during blocking
system calls, and well-behaved C extensions (NumPy, PyTorch, hashing, compression) release it during
long native work. For CPU-bound pure-Python code, use `multiprocessing` or `ProcessPoolExecutor`, which
sidestep the GIL by running separate interpreters. Python 3.13 added an experimental free-threaded
build (PEP 703) that removes the GIL, and 3.14 made that build officially supported, but it is still a
separate opt-in build rather than the default and not every C extension supports it yet, so plan as if
the GIL is there.

### asyncio: waiting without threads
An `async def` function is a coroutine; `await` hands control back to the event loop until the awaited
I/O completes. One thread can therefore hold hundreds of pending model calls, each costing a small
object rather than a thread stack. This is the shape of an LLM backend: fan out calls, cap concurrency
with a `Semaphore` to respect provider rate limits, gather results. The one rule that matters is that
a blocking call inside a coroutine (a sync HTTP client, `time.sleep`, a heavy loop) freezes the whole
loop, because nothing else can run until it returns.

### Types and Pydantic
Type hints are annotations; the interpreter does not enforce them. That is fine inside your code with
`mypy` checking it, but data crossing a boundary (an HTTP body, a model's JSON output, a config file)
needs runtime validation. Pydantic models validate and coerce at runtime and emit JSON Schema, so one
class can be the API contract, the structured-output schema you hand to an LLM, and the parsed result.
This single-source-of-truth role is why Pydantic became the lingua franca of AI services.

### Packaging and environments
Each project gets a virtual environment and a lockfile; `uv`, Poetry or plain `pip` with a
requirements lock all work. The characteristic pain is heavy native dependencies (PyTorch with a
specific CUDA build), which is why AI services pin exact versions and ship in containers.

### Where it sits in the stack
Typically: a FastAPI service for the API, worker processes for long jobs, scripts and notebooks for
evals and data prep. The product UI and sometimes a thin gateway live in TypeScript. The boundary
between them is an HTTP contract, usually generated from the Python service's OpenAPI schema.

## Example
Fan out a batch of classification calls concurrently with a concurrency cap, validate each model
answer at runtime, and push a CPU-bound step out of the GIL-bound main process. The `Semaphore` is the
rate-limit guard; the `ProcessPoolExecutor` is the GIL escape hatch.

```python
import asyncio
from concurrent.futures import ProcessPoolExecutor
import httpx
from pydantic import BaseModel, ValidationError

class Verdict(BaseModel):
    label: str
    confidence: float

SEM = asyncio.Semaphore(8)                      # at most 8 model calls in flight

async def classify(client: httpx.AsyncClient, text: str) -> Verdict | None:
    async with SEM:                             # waits without holding a thread
        r = await client.post("http://localhost:8000/v1/classify", json={"text": text}, timeout=30)
    try:
        return Verdict.model_validate_json(r.content)  # runtime check; bad JSON is a ValidationError too
    except ValidationError:
        return None

def count_words(text: str) -> int:              # stand-in for CPU-heavy pure-Python work
    return sum(1 for _ in text.split())

async def main(texts: list[str]):
    async with httpx.AsyncClient() as client:   # one connection pool for the whole batch
        verdicts = await asyncio.gather(*(classify(client, t) for t in texts))
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor() as pool:         # CPU-bound work runs in separate interpreters
        counts = await asyncio.gather(*(loop.run_in_executor(pool, count_words, t) for t in texts))
    return list(zip(verdicts, counts))

if __name__ == "__main__":                      # required when workers are spawned, not forked
    print(asyncio.run(main(["refund my order", "great product"] * 50)))
```

## Trade-offs
- You get the entire ML ecosystem and the fastest path from notebook to service, but you pay with a
  runtime that is slow for CPU-bound pure-Python code; this flips only when your service does heavy
  per-request computation in Python itself rather than in native libraries or a model.
- `asyncio` gives cheap concurrency for I/O-bound work, but every library on the request path must
  be async-aware; one blocking call undoes it, and mixing sync and async code is a constant tax.
- Dynamic typing speeds experimentation, but production code needs `mypy` plus runtime validation
  at boundaries; skipping either shows up as type errors discovered by users instead of CI.
- Rich native dependencies (PyTorch, CUDA) are the ecosystem's strength and its deployment weakness:
  images are large and version pinning is non-negotiable.

## Pitfalls
- Calling a synchronous client (`requests`, a sync DB driver) inside an `async def` handler; the
  symptom is throughput collapsing to one request at a time while CPU sits idle.
- Treating the GIL as the reason a service is slow when it is I/O-bound; profiling shows threads
  blocked on sockets, and the fix is concurrency, not multiprocessing.
- Trusting type hints as validation; a model returns `"confidence": "high"` and the code crashes three
  functions later instead of at the boundary.
- Unpinned dependencies in an AI service; a transitive upgrade of a tokenizer or SDK changes outputs
  and the symptom is evals drifting with no code change.

## When to use
Default to Python for anything that touches models, tokenizers, embeddings, evals or data preparation,
and for the service layer that orchestrates them: the library support decides it. Keep CPU-bound hot
paths in native code or separate processes, and measure before assuming Python is the bottleneck;
in most LLM services the model call dominates by two or three orders of magnitude. Consider another
language for the product edge (TypeScript next to the UI) or for a tiny, extremely high-throughput
proxy where microseconds matter. In interviews, the expected depth is explaining the GIL precisely
(what it blocks, when it is released), why `asyncio` fits LLM workloads, and why Pydantic sits at
every boundary. The `python-for-ai` module covers each of these in detail.

## Role lens
- **Engineer:** Writes async service code, keeps blocking work off the event loop, validates every
  boundary with Pydantic, and pins the environment so evals stay reproducible.
- **Architect:** Decides the language split: Python for the model core and data work, TypeScript or
  another language where the product or throughput argument wins, joined by a typed contract.
- **FDE:** Lands inside a customer's existing Python (or non-Python) stack and must explain why the AI
  core stays Python and how it integrates with their Java or .NET services over HTTP.

## Check yourself
**Q:** A colleague argues the new LLM gateway should be written in Go because "Python's GIL cannot handle
2,000 concurrent requests". How do you respond, and what would change your answer?
**A:** The GIL serialises Python bytecode execution, not waiting. A gateway's work per request is
building a payload and awaiting a model call that takes seconds; with `asyncio`, one process holds
thousands of pending awaits on a single thread, and the GIL is irrelevant while they wait on sockets.
Multiple uvicorn workers add CPU headroom for serialisation. I would ask what CPU-bound work the
gateway does per request; if it must tokenize or transform megabytes in pure Python per call, that is
the argument for native code or another language. I would also weigh the cost of a second language
when the model, eval and SDK ecosystem is Python-first.

## Quiz
### Q: Why does the GIL rarely limit an LLM-serving backend?
- [ ] Modern Python releases the GIL for all threads automatically
- [x] The service is I/O-bound, and the GIL is released while waiting on sockets, so asyncio or threads overlap the waits
- [ ] Uvicorn runs each request in its own interpreter
- [ ] LLM responses are small, so bytecode execution is negligible regardless of concurrency
> The GIL blocks concurrent execution of Python bytecode, not concurrent waiting. An LLM backend
> mostly awaits network I/O, during which the GIL is not held, so one process serves many requests.

### Q: Which piece of work should leave the main Python process rather than run inside a coroutine?
- [ ] Awaiting 200 concurrent HTTP calls to a model provider
- [ ] A NumPy matrix multiply over a batch of embeddings
- [x] A pure-Python loop that parses a 500 MB log file line by line
- [ ] Reading configuration from environment variables at startup
> Pure-Python CPU work holds the GIL, so neither the event loop nor a thread can overlap it; only a
> separate process can. A long NumPy multiply also blocks the loop if run inline, but it releases the GIL,
> so a thread in the same process (`asyncio.to_thread`) suffices. Awaited HTTP calls are I/O-bound.

### Q: What does a Pydantic model add that a type hint alone does not?
- [ ] It makes the interpreter reject wrongly typed arguments at call time
- [x] Runtime validation and coercion of external data plus JSON Schema generation
- [ ] Faster attribute access through compiled slots
- [ ] Automatic retries when validation fails
> Type hints are inert annotations checked only by tools like mypy. Pydantic validates data at runtime,
> which is what an HTTP body or a model's JSON output needs, and emits the schema you give the LLM.
