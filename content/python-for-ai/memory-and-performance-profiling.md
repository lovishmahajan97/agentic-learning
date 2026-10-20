---
id: memory-and-performance-profiling
module: python-for-ai
name: Memory & performance profiling
kind: practice
difficulty: advanced
est_minutes: 16
status: fact-checked
related:
  - python-for-ai/gil
  - python-for-ai/asyncio-vs-threads-vs-multiprocessing
  - python-for-ai/generators
  - python-for-ai/numpy
  - python-for-ai/pytorch
  - observability/latency-percentiles
  - observability/tracing
  - serving-inference/gpu-memory-optimization
sources:
  - Official docs: Python profile/cProfile and tracemalloc modules
  - Official docs: py-spy
  - Official docs: memray
  - Official docs: Scalene
---

## TL;DR
Profiling replaces guessing with measurement: a CPU profiler tells you which functions consume time, a
memory profiler tells you which lines allocate the bytes that are still alive. In AI services the answer is
often surprising: most wall time is spent waiting on the network or a GPU, and much memory is held by native
buffers (PyTorch tensors, tokenizers) that Python's own tools cannot see. Pick the tool by the
question: CPU or wall time, Python heap or native memory, dev box or live process.

## Why it exists
"The pipeline is slow" and "the worker gets OOM-killed after a few hours" are the two most common
performance tickets in an AI codebase, and intuition is a poor guide to either. Engineers rewrite a loop in
NumPy when the real cost was a tokenizer re-created per request; they add a cache when the real cost was
waiting sequentially on twelve embedding calls; they blame "a memory leak in the model" when an unbounded
`lru_cache` or a conversation list that is never trimmed is holding every request's text.

The naive approach, sprinkling `time.time()` prints and watching `top`, fails for structural reasons. Prints
only measure code you already suspect. `top` reports resident memory (RSS) for the whole process, which
mixes the Python heap, native library allocations, memory-mapped model weights and allocator fragmentation,
so it can tell you *that* memory grows but never *where*. A profiler attributes cost to code systematically,
including code you did not think to suspect.

## How it works
### First question: CPU-bound or waiting?
Compare wall time with CPU time (`time.perf_counter()` vs `time.process_time()`). If CPU time is a small
fraction of wall time, the process is waiting on I/O, locks or a GPU, and a CPU profiler will mostly show
time inside the event loop's selector. The fix there is concurrency, batching or caching, and the right
measurement is a trace with spans per remote call, not a function profile.

### Deterministic profiling: cProfile
`cProfile` (stdlib) hooks every function call and return, recording call counts, total time inside each
function (`tottime`) and time including callees (`cumtime`). It is exact about call counts, which is how
you discover "this tokenizer was constructed 10,000 times". The cost is overhead on every call, which
inflates functions that make many small calls, and it attributes poorly across `await` points and threads.
Read results with `pstats` sorted by `cumulative` to find the expensive subtree, then by `tottime` to find
the hot leaf; `snakeviz` renders them visually.

### Sampling profilers: py-spy, Scalene
A sampling profiler interrupts at intervals and records the current stack, so overhead stays low and
roughly constant, *because* cost depends on the sampling rate rather than on how many calls you make.
`py-spy` reads the target process's memory from outside, so it can attach to a running production worker
(`py-spy top --pid`, `py-spy record -o flame.svg --pid`, `py-spy dump` for a hung process) without code
changes; `--idle` includes waiting threads and `--native` includes C extension frames. Scalene separates
time spent in Python from time in native code and also reports memory and copy volume per line, which
answers "is my NumPy code actually vectorized?". `line_profiler` (`@profile` plus `kernprof -l`) gives
per-line timings for one function once you know which function matters.

### Python heap memory: tracemalloc
`tracemalloc` (stdlib) records a traceback for every allocation made through Python's allocator. Take a
snapshot, run the workload, take another, and `compare_to` shows which source lines' live allocations grew.
That is the right tool for leaks in Python objects: growing lists, dicts, caches and closures. It slows the
program, and it cannot see allocations that native libraries make with their own allocators unless the
library reports them explicitly. NumPy is the notable exception: it registers its array data buffers with
tracemalloc (via `PyTraceMalloc_Track`), so large arrays do show up; PyTorch CPU tensors, Hugging Face
`tokenizers` (Rust) and most C/C++ parsers do not.

### Native and GPU memory
`memray` intercepts the allocator calls themselves (`malloc`, `mmap` and friends), so it sees native
allocations as well (C extensions, PyTorch CPU tensors, tokenizers), records to a file and
produces flame graphs of what was live at peak (`memray run`, then `memray flamegraph`). For PyTorch on GPU,
use `torch.cuda.memory_allocated()`, `max_memory_allocated()` and `memory_summary()`; RSS and tracemalloc
know nothing about VRAM, and the caching allocator keeps freed blocks reserved, so `nvidia-smi` shows more
than tensors actually use.

### Why RSS may not go down
CPython's small-object allocator groups objects into arenas and can only return an arena to the OS when
every object in it is freed, and native allocators have similar behaviour. A process that peaked at 6 GB and
freed most objects may still report a high RSS. Growth that plateaus is usually fragmentation or a warm
cache; growth that never stops under steady load is a leak.

## Example
Find a memory leak and a hot spot in a RAG worker. tracemalloc compares two snapshots around a burst of
requests; cProfile then shows where the CPU time goes.

```python
import cProfile, pstats, tracemalloc
from functools import lru_cache

HISTORY: list[str] = []                          # the bug: grows forever

@lru_cache(maxsize=None)                         # second bug: unbounded cache keyed by full text
def normalize(text: str) -> str:
    return " ".join(text.lower().split())

def handle(i: int) -> int:
    doc = f"Retrieved chunk {i} " * 200
    HISTORY.append(doc)                          # kept "for debugging"
    return len(normalize(doc).split())

tracemalloc.start(10)                            # keep 10 frames per allocation
before = tracemalloc.take_snapshot()
for i in range(5_000):
    handle(i)
after = tracemalloc.take_snapshot()
for stat in after.compare_to(before, "lineno")[:3]:
    print(stat)    # top lines by growth: the `doc = ...` line (strings kept alive by HISTORY),
                   # normalize's join (cached results) and the call site (lru_cache entries)
tracemalloc.stop()

prof = cProfile.Profile()
prof.enable()
for i in range(5_000, 10_000):
    handle(i)
prof.disable()
pstats.Stats(prof).sort_stats("cumulative").print_stats(5)
# Fix: bound the history (collections.deque(maxlen=100)) and give lru_cache a maxsize,
# then re-run the snapshot diff and confirm growth is flat across bursts.
```

## Trade-offs
- Deterministic profilers give exact call counts but distort timings of call-heavy code; sampling profilers
  keep timings honest and are safe in production, but miss rare short calls and give no exact counts.
- tracemalloc pinpoints Python-object leaks to a line, but slows the program noticeably and misses native
  memory; switch to memray once RSS grows while tracemalloc's totals stay flat.
- Profiling in production (py-spy attach) shows real behaviour under real traffic, but needs
  `ptrace`-style permissions that hardened containers often deny; locally reproduced load is easier but may
  not trigger the real hot path.
- Micro-optimizing a hot Python function can halve its time, but if wall time is dominated by remote calls
  the user sees no difference; batching or parallelizing those calls is the larger lever then.

## Pitfalls
- Profiling an I/O-bound async service with cProfile and "optimizing" whatever tops the list; the symptom is
  that the selector or event loop dominates the profile and latency does not improve after the change.
- Treating high RSS after a peak as a leak; the symptom is memory that plateaus under steady load rather than
  climbing, which points to fragmentation or a cache, not unbounded growth.
- Measuring once, on a cold process; the symptom is profiles dominated by imports, model loading and JIT or
  CUDA initialization that never recur in steady state.
- Reading `nvidia-smi` as tensor usage; the symptom is apparent "GPU leaks" that are actually the PyTorch
  caching allocator holding reserved but free blocks.
- Leaving tracemalloc or a deterministic profiler enabled in a production build; the symptom is a large,
  otherwise unexplained latency regression.

## When to use
Profile before optimizing, and whenever a latency SLO, a cost target or an OOM is in play. Start with the
cheapest discriminating measurement: wall vs CPU time, then a sampling profile, then a deterministic or
line profile of the function that matters. For memory, watch RSS over time under steady load first; if it
climbs, use tracemalloc snapshot diffs for Python objects and memray for native buffers, and the CUDA memory
APIs for GPU. Measure after every fix to confirm it moved the number you cared about. In interviews,
distinguishing CPU-bound from waiting, and Python heap from native and GPU memory, is the core skill.

## Role lens
- **Engineer:** Runs py-spy on a slow worker, uses tracemalloc diffs to find the unbounded cache, and
  verifies each fix with a before-and-after measurement.
- **Architect:** Decides where profiling hooks and memory limits live, and sets the capacity model from
  measured per-request CPU, memory and GPU cost rather than estimates.
- **FDE:** Diagnoses performance inside the customer's environment, often without root access, so relies on
  sampling, logs of wall vs CPU time and reproducible load scripts.

## Check yourself
**Q:** A document-ingestion worker's RSS climbs from 1.5 GB to 9 GB over six hours and the pod is
OOM-killed. tracemalloc shows the Python heap stable at about 300 MB. How do you proceed?
**A:** A stable traced heap with growing RSS means the growth is outside what tracemalloc sees: native
buffers (PDF parsers, PyTorch tensors, tokenizers; NumPy arrays would have shown up because NumPy reports
them to tracemalloc), memory-mapped files, or fragmentation. I would run the worker under
memray, or attach it in a staging replica, and look at what is live at the high-water mark, checking for
native objects kept alive by Python references such as a cache of parsed documents or arrays. I would also
check whether growth plateaus (fragmentation, possibly mitigated by recycling workers after N jobs) or is
unbounded (a real leak). Then fix, rerun the same workload, and confirm RSS stays flat.

## Quiz
### Q: An async RAG endpoint's cProfile output is dominated by the event loop's select call. What does that indicate?
- [ ] The event loop implementation is inefficient and should be replaced
- [x] The process spends most wall time waiting on I/O, so CPU optimization will not help much
- [ ] cProfile cannot run on async code at all
- [ ] There is a memory leak in the selector
> Time in the selector is time spent waiting for sockets. The levers are concurrency, batching and
> caching of remote calls, measured with traces; replacing the loop rarely moves waiting-dominated latency.

### Q: Why can py-spy safely profile a live production process when cProfile usually is not used there?
- [ ] py-spy only records memory, not CPU
- [ ] cProfile requires a debug build of Python
- [x] py-spy samples stacks from outside the process at low constant overhead, while cProfile instruments every call
- [ ] py-spy rewrites bytecode to remove its own overhead
> Sampling cost depends on the sampling rate, not on how many calls the program makes, and py-spy reads
> the target's memory externally, so no code change or restart is needed.

### Q: RSS keeps growing but tracemalloc's traced total is flat. Most likely explanation?
- [ ] tracemalloc is reporting incorrectly and should be restarted
- [ ] Python's garbage collector is disabled
- [ ] The leak is in a Python list that tracemalloc ignores
- [x] Memory is allocated by native code or the allocator is fragmented, outside what tracemalloc traces
> tracemalloc sees allocations through Python's allocator. Native libraries using their own allocators,
> mmaps and fragmentation all raise RSS invisibly to it, which is when a native-aware tool like memray helps.
