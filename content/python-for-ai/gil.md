---
id: gil
module: python-for-ai
name: GIL (Global Interpreter Lock)
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - python-for-ai/asyncio-vs-threads-vs-multiprocessing
  - python-for-ai/race-conditions-and-synchronization-primitives
  - python-for-ai/asyncio
  - python-for-ai/numpy
  - python-for-ai/pytorch
  - python-for-ai/memory-and-performance-profiling
  - node-ts-for-ai/worker-threads-for-cpu-bound-tasks
sources:
  - PEP 703 — Making the Global Interpreter Lock Optional in CPython
  - PEP 684 — A Per-Interpreter GIL
  - Official docs: Python/C API (Thread State and the Global Interpreter Lock)
---

## TL;DR
The GIL is a mutex in standard CPython that lets only one thread execute Python bytecode at a time.
It exists because CPython's memory management (reference counting) and much C-extension code are not
thread-safe, and one lock made both simple and fast single-threaded. Threads still help with I/O and with
native code that releases the GIL, but not with pure-Python CPU work. It also does not make your code thread-safe.

## Why it exists
Every CPython object carries a reference count that is incremented and decremented constantly, on
nearly every assignment, call and attribute access. If two threads update the same count at once
without coordination, the count goes wrong, and the object is either freed while still in use (a
crash) or never freed (a leak). The same is true of the interpreter's internal structures: dicts
backing modules and classes, the allocator's free lists, the import machinery.

The alternatives are expensive. Fine-grained locks on every object, or atomic instructions on every
refcount change, slow single-threaded code, which was and is the common case. They also force every C
extension author to reason about concurrency. One global lock made the interpreter and its huge C
extension ecosystem (NumPy, database drivers, compression) easy to write correctly, and kept
single-threaded Python fast. The cost was paid later, when multi-core machines became standard: a
CPU-bound Python program using threads still uses roughly one core.

For AI engineers the GIL matters because their workloads straddle the line. Calling LLM APIs is
I/O-bound and threads or asyncio handle it fine. Tokenising a corpus, parsing documents or running
a pure-Python scoring loop is CPU-bound, and adding threads there gives almost nothing. Knowing which
side of the line a workload sits on decides whether to use threads, processes or native code.

## How it works
### Holding and handing over
A thread must hold the GIL to run bytecode or touch Python objects. A running thread gives it up in
two ways. **Voluntarily** around blocking operations: socket reads, file I/O, `time.sleep`, waiting on
a lock. CPython's I/O code releases the GIL before the system call and reacquires it after, *because*
there is no reason to block other threads while the kernel waits. **On request**: if another thread
has waited longer than the switch interval (`sys.getswitchinterval()`, 5 ms by default), it sets a
flag, and the running thread drops the GIL at its next check point in the eval loop so the waiter can
run. That forced handover is what keeps threads fair, and it is also why CPU-bound threads add
switching overhead rather than speed.

### The hidden cost: CPU threads slow down I/O threads
The handover rule has a consequence that bites async services. When an I/O thread's socket becomes
ready, the kernel wakes it, but it still has to reacquire the GIL before it can run any Python. If a
CPU-bound thread holds the GIL, the I/O thread waits until the running thread reaches a check point
and yields, which is bounded by roughly the switch interval each time it needs the lock back. An
event-loop thread needs the GIL for every task step, so running pure-Python CPU work in
`asyncio.to_thread` beside the loop keeps the loop *unblocked* but not *unaffected*: each step can be
delayed by milliseconds, and streaming responses get visibly jittery. The work belongs in a process.

### Native code can opt out
C, C++ and Rust extensions can release the GIL while working on their own memory, as long as they
touch no Python objects. In C this is the `Py_BEGIN_ALLOW_THREADS` / `Py_END_ALLOW_THREADS` pair around
the native work, and the extension must reacquire the GIL before it builds the Python result. NumPy does this for many operations on large arrays, PyTorch for most tensor
ops, `hashlib` for large inputs, and compression libraries for large buffers. So "Python is
single-core" is wrong for these libraries: a thread pool running NumPy-heavy or PyTorch CPU work can
use several cores. Whether a given call releases the GIL is a property of that library and function,
so check or measure rather than assume.

### What the GIL does not give you
The GIL protects the interpreter's integrity, not your invariants. `counter += 1` compiles to
several bytecodes (load, add, store), and a switch can occur between them, so two threads can lose an
update. Check-then-act on a dict across threads needs a `threading.Lock`. Whether you can reproduce
the race depends on the Python version and timing, which makes it more dangerous, not less.

The GIL does make some single operations on built-in types atomic as a side effect: one
`list.append` or one `d[key] = value` cannot be torn. Code that relies on this "works" by accident of
implementation. It is why `queue.Queue` and explicit locks are the documented tools, and why the
free-threaded build had to add per-object locking inside built-in containers to keep such single
operations safe.

### Ways around it, and its future
Processes (`multiprocessing`, `ProcessPoolExecutor`) each have their own interpreter and GIL. PEP 684
(Python 3.12) gave each sub-interpreter its own GIL, enabling parallelism within one process with
isolated state; in 3.12 this was reachable only through the C API, and a Python-level
`concurrent.interpreters` module followed in 3.14 (PEP 734). PEP 703 introduced an optional
free-threaded CPython build with no GIL, using biased reference counting and other techniques
(experimental in 3.13, officially supported but still a separate build in 3.14); extensions must be
built and marked compatible with it, and it has some single-threaded overhead. Its support status changes by release, so check the docs for
your version and your dependencies before planning around it.

## Example
```python
import hashlib, time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

def py_work(n: int) -> int:            # pure Python: holds the GIL throughout
    total = 0
    for i in range(n):
        total += i * i % 7
    return total

def c_work(data: bytes) -> str:        # hashlib releases the GIL on large inputs
    return hashlib.sha256(data).hexdigest()

def timed(label, run):
    t = time.perf_counter()
    run()
    print(f"{label:<20}{time.perf_counter() - t:6.2f}s")

def pool(cls, fn, args):
    with cls(max_workers=4) as ex:
        list(ex.map(fn, args))

if __name__ == "__main__":
    nums, blobs = [5_000_000] * 4, [b"x" * 200_000_000] * 4
    timed("python, sequential", lambda: [py_work(n) for n in nums])
    timed("python, 4 threads", lambda: pool(ThreadPoolExecutor, py_work, nums))
    timed("python, 4 procs", lambda: pool(ProcessPoolExecutor, py_work, nums))
    timed("sha256, sequential", lambda: [c_work(b) for b in blobs])
    timed("sha256, 4 threads", lambda: pool(ThreadPoolExecutor, c_work, blobs))
```
On a standard (GIL) CPython on a multi-core laptop, the pattern is: four threads of pure Python take
about the same time as sequential, four processes take roughly a third of it (less than 4× because of
process startup), and four threads hashing large buffers run about 4× faster than sequential, because
`hashlib` releases the GIL. Absolute times depend on the machine; the ratios are the lesson.

## Trade-offs
- The GIL keeps single-threaded code and C extensions simple and fast, but caps pure-Python CPU work at
  one core per process; the trade flips when your hot loop lives in GIL-releasing native code.
- Processes sidestep the GIL completely, at the cost of separate memory, pickled arguments and slower
  startup; they win when each task carries far more compute than data.
- Moving a hot loop into NumPy, a compiled extension or a Rust library removes both the GIL limit and
  interpreter overhead, but adds a build and maintenance burden worth paying only for real hot spots.
- A free-threaded build allows true thread parallelism for Python code, but trades some single-thread
  speed and depends on every compiled dependency supporting it, so it suits measured, controlled deployments.

## Pitfalls
- Adding threads to speed up a pure-Python tokenising or parsing loop; the symptom is CPU pinned near
  100% of one core and no wall-clock improvement.
- Assuming the GIL makes shared counters or dicts thread-safe; the symptom is occasional lost updates
  or inconsistent state that disappears when you add logging.
- Blaming the GIL for a slow I/O-bound service; if CPU is low and latency high, the problem is
  waiting or blocking calls, and the GIL is not the bottleneck.
- Assuming a library releases the GIL because it is written in C; some hold it for the whole call, and
  profiling shows threads queuing instead of running in parallel.
- Installing a free-threaded build and importing an extension not marked compatible with it; CPython
  then re-enables the GIL at runtime (with a warning), threads stop scaling, and
  `sys._is_gil_enabled()` (3.13+) returns `True` where you expected `False`.

## When to use
You do not use the GIL; you design around it. When a Python service is slow, first ask whether it is
waiting (I/O) or computing (CPU). For I/O, threads or asyncio are fine and the GIL is irrelevant. For
CPU-bound pure Python, use processes, vectorise with NumPy or Polars, or move the hot loop to native
code. For CPU work inside NumPy or PyTorch, threads can work, and you can measure it with a quick timing
like the example. The quickest diagnostic is the ratio of process CPU time to wall time with N threads:
about 1.0 means the GIL is serialising you, close to N means the work releases it. A sampling profiler
such as py-spy shows which thread holds the GIL when that ratio surprises you. Track the free-threaded build's status if you run CPU-heavy Python at scale. In
interviews, explain why the GIL exists (refcounting), when it bites (CPU-bound bytecode), and why it does
not make code thread-safe.

## Role lens
- **Engineer:** Profiles before parallelising, uses processes or vectorised libraries for CPU-bound
  preprocessing, and still guards shared state across threads with locks.
- **Architect:** Plans process-level scaling for CPU-heavy stages and pushes heavy compute into native
  libraries or separate services instead of fighting the GIL in one process.
- **FDE:** Explains to a customer why their sixteen-thread ingestion script uses one core, and fixes
  it with a process pool or a vectorised rewrite on their hardware.

## Check yourself
**Q:** A colleague's embedding-prep script runs a pure-Python text-cleaning function over 1M documents with a
32-thread pool on a 32-core machine and sees no speed-up. They propose 64 threads. What do you say?
**A:** The cleaning function is pure-Python CPU work, so in standard CPython the GIL lets only one thread
execute bytecode at a time; more threads only add switching overhead, which is why 32 threads barely beat
one. The fix is a `ProcessPoolExecutor` with roughly one worker per core, sending documents in chunks so
pickling is amortised, or moving the hot loop into native code: Polars string expressions or a compiled
library. Fewer, larger `re` calls also cut per-document interpreter overhead, but the `re` module holds the
GIL, so regex work still does not parallelise across threads. I would time a small sample sequentially and with processes to confirm the gain before running
the full million. Threads would be right only if the work were I/O or GIL-releasing native code.

## Quiz
### Q: Why did CPython adopt a single global lock rather than fine-grained locks?
- [ ] Because operating systems in the 1990s did not support threads
- [x] Because reference counting and C extensions needed protection, and one lock kept single-threaded code fast and simple
- [ ] Because a global lock makes all user code thread-safe automatically
- [ ] Because fine-grained locks are impossible to implement in C
> Every object's refcount changes constantly; protecting each one individually would slow the common
> single-threaded case and complicate every extension. The GIL protects interpreter state, not user invariants.

### Q: A thread pool running large NumPy matrix multiplications uses several cores. Why is that possible under the GIL?
- [ ] NumPy spawns hidden subprocesses for each call
- [ ] The GIL is only enforced for I/O operations
- [x] NumPy releases the GIL inside its native code while it works on its own buffers
- [ ] Threads automatically bypass the GIL after the switch interval
> C extensions may drop the GIL while not touching Python objects, so NumPy's compiled kernels run in
> parallel across threads. The switch interval only governs handover between threads running bytecode.

### Q: Two threads each run `counter += 1` a million times on a shared int. What can happen in standard CPython?
- [ ] The result is always exactly two million because of the GIL
- [ ] The program raises a RuntimeError for concurrent modification
- [ ] The result is always one million because the threads serialise
- [x] Updates can be lost, because the increment spans several bytecodes and a switch can occur between them
> The GIL makes each bytecode safe, not each statement. Load, add and store are separate steps, so an
> interleaving can overwrite another thread's increment; whether it shows up depends on version and timing.
