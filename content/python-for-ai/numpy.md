---
id: numpy
module: python-for-ai
name: numpy
kind: tool
difficulty: core
est_minutes: 15
related:
  - python-for-ai/pandas
  - python-for-ai/pytorch
  - python-for-ai/pyarrow
  - rag-retrieval/similarity-search
  - rag-retrieval/embedding-dimension-and-normalization
  - python-for-ai/memory-and-performance-profiling
  - python-for-ai/gil
sources:
  - Harris et al., 2020 — Array programming with NumPy
  - Official docs: NumPy
---

## TL;DR
NumPy's `ndarray` is a block of typed, contiguous memory plus a shape and strides, and its operations
run compiled loops over that memory instead of Python bytecode per element. That is why embedding math,
metrics and feature arrays are written as whole-array expressions: you get compiled speed and compact
memory, but you must think in shapes, dtypes, broadcasting and views, where most NumPy bugs live.

## Why it exists
A Python list of floats is an array of pointers to separate heap objects, each boxed with a type and a
reference count. Computing a dot product over it means, per element, two pointer chases, two type checks,
an allocation for the result and an interpreter dispatch. For a retrieval prototype scoring a query
against 100,000 embeddings of 768 dimensions, that is about 77 million multiply-adds through the
interpreter, which takes seconds to minutes in pure Python where the same work in compiled code takes
milliseconds (illustrative orders of magnitude; measure on your machine).

The memory cost is just as bad. A boxed Python float is far larger than its 8 bytes of payload, so lists
of numbers use several times the memory of the raw values and scatter them across the heap, which ruins
CPU cache behaviour.

NumPy's answer is to store numbers unboxed in one buffer with a single dtype, and to expose operations
that act on the whole buffer. The loop still exists, but it runs in C (or in an optimized BLAS library
for matrix products) over contiguous memory. Nearly the whole scientific Python stack, pandas,
scikit-learn, and the tensor interop in PyTorch, is built on or speaks this array model.

## How it works
### The ndarray: buffer, dtype, shape, strides
An array is a pointer to raw bytes plus metadata: `dtype` (for example `float32`, 4 bytes), `shape`
(for example `(100000, 768)`) and `strides`, the number of bytes to step to move one position along each
axis. For a C-ordered (row-major) float32 matrix of shape `(100000, 768)`, strides are `(3072, 4)`.
Element `[i, j]` lives at `offset + i*3072 + j*4`. Memory is exactly `n × d × itemsize`:
100,000 × 768 × 4 = 307,200,000 bytes, about 307 MB. In float64 it would be twice that, which is why
embeddings are usually kept in float32.

### Views versus copies
Because indexing is just arithmetic on strides, many operations return a *view* that shares the buffer:
basic slicing (`a[:10]`, `a[:, ::2]`), `reshape` when possible, and transpose (`a.T` just swaps the
strides). This is a design choice for speed: slicing a 307 MB array costs nothing. The consequence is
that writing into a view writes into the original. Fancy indexing with integer arrays or boolean masks
(`a[[1, 5, 9]]`, `a[a > 0]`) must gather scattered elements, so it returns a copy.

### Vectorization and ufuncs
Element-wise operations (`+`, `np.exp`, comparisons) are *ufuncs*: compiled loops applied over every
element. Reductions (`sum`, `max`, `argmax`) take an `axis`, the dimension that collapses: `a.sum(axis=1)`
on `(N, d)` gives `(N,)`. Matrix products (`@`) dispatch to BLAS, which uses SIMD and multiple cores.
Many of these release the GIL while they run, so threads can overlap NumPy work.

### Broadcasting
To combine arrays of different shapes without copying, NumPy aligns shapes from the right; each pair of
dimensions must be equal or one of them 1, and size-1 dimensions are stretched virtually (stride 0).
`(N, d) / (N, 1)` divides each row by its own norm; `(N, d) - (d,)` subtracts a mean vector from every
row. This is powerful and also the top source of silent bugs: `(N,) - (N, 1)` broadcasts to an `(N, N)`
matrix rather than raising an error.

### Dtypes and numerical behaviour
Integer arrays have fixed width and wrap on overflow without an exception (a `uint8` pixel of 250 plus 10
becomes 4). Float32 has about 7 significant decimal digits, enough for similarity scores but risky for
long accumulations. NumPy's `float32` scalars are not Python floats, so they do not serialize to JSON
until you call `.item()` or `.tolist()`.

### Randomness
Use `rng = np.random.default_rng(seed)` and pass `rng` around rather than seeding a global state, because
explicit generators make experiments reproducible and do not interfere across libraries.

## Example
Exact top-k cosine search over 100,000 embeddings, the core of every retrieval prototype.

```python
import numpy as np

rng = np.random.default_rng(0)
docs = rng.standard_normal((100_000, 768), dtype=np.float32)   # ~307 MB
query = rng.standard_normal(768, dtype=np.float32)
print(docs.nbytes, docs.strides)          # 307200000 (3072, 4)

# Normalize rows: (N, 768) / (N, 1) broadcasts per row; /= writes into docs in place
docs /= np.linalg.norm(docs, axis=1, keepdims=True)
query /= np.linalg.norm(query)

scores = docs @ query                     # (100000,) via one BLAS matrix-vector product
k = 5
top = np.argpartition(-scores, k)[:k]     # O(N) selection of the k best, unordered
top = top[np.argsort(-scores[top])]       # sort only those k
print(top, scores[top].round(3))

# Pitfall demo: forgetting keepdims gives shape (N,), which cannot broadcast against (N, 768)
norms = np.linalg.norm(docs, axis=1)      # (100000,)
# docs / norms would raise: operands could not be broadcast together
head = docs[:10]                          # a view: head[:] = 0 would zero rows of docs
picked = docs[[0, 5, 9]]                  # fancy indexing: a copy, safe to modify
print(np.shares_memory(head, docs), np.shares_memory(picked, docs))  # True False
```

`argpartition` avoids sorting 100,000 scores to find 5, and `keepdims=True` keeps the norm as
`(N, 1)` so the division broadcasts per row. Past a few million vectors, exact search like this is
replaced by an approximate index.

## Trade-offs
- Whole-array operations run at compiled speed, but every intermediate is a full temporary array, so
  memory can peak at several copies of your data; in-place operators and `out=` arguments help.
- Views make slicing and transposes free, at the cost of aliasing: a mutation through a view changes
  the original, which is a correctness hazard in shared code paths.
- float32 halves memory and speeds up BLAS compared with float64, but loses precision in long sums
  or ill-conditioned linear algebra, where float64 is worth the cost.
- NumPy runs on CPU in a single process; once arrays exceed RAM or you need GPUs or autograd, PyTorch,
  memory-mapped arrays or a vector database take over.

## Pitfalls
- Writing Python loops over array elements; the symptom is a NumPy script that is no faster than pure
  Python, revealed by a profiler showing time in the interpreter, not in NumPy calls.
- Accidental broadcasting of `(N,)` against `(N, 1)`; the symptom is an unexpectedly huge array, a
  `MemoryError`, or a metric that looks plausible but is averaged over N² pairs.
- Modifying a slice and corrupting the source array; the symptom is embeddings or features changing
  "by themselves" after an unrelated function ran.
- Integer overflow in fixed-width dtypes; the symptom is negative counts or wrapped pixel values with
  no warning raised.
- Default float64 from `np.array(list_of_floats)` for embeddings; the symptom is double the expected
  memory and slower matrix products.

## When to use
Use NumPy for anything numerical that fits in memory on one machine: embedding similarity, metric
computation, feature arrays for scikit-learn, image and audio buffers, and as the interchange format
between libraries. Think about shapes first: write the expected shape next to each line. Reach for
pandas or Polars when your data is tabular with named, mixed-type columns; for PyTorch when you need
GPUs or gradients; and for a vector index when exact search over millions of vectors becomes too slow.
Measure with `nbytes`, a profiler and `%timeit` rather than guessing. Interviews often ask you to
vectorize a loop or explain broadcasting.

## Role lens
- **Engineer:** Vectorizes scoring and metrics, tracks shapes and dtypes explicitly, and uses float32 and
  `argpartition` for retrieval prototypes.
- **Architect:** Uses `n × d × bytes` arithmetic to size in-memory indexes and decides when exact NumPy
  search must give way to an approximate vector store.
- **FDE:** Loads a customer's embedding dump with NumPy, checks norms, dtypes and NaNs, and runs exact
  search as a ground-truth baseline for their vector database.

## Check yourself
**Q:** You need cosine similarity between 50,000 queries and 1,000,000 documents, both 768-d float32.
A colleague writes `Q @ D.T` in one line. What happens and what would you do?
**A:** The result matrix is 50,000 × 1,000,000 float32 values, 200 GB, so it will not fit in memory, on
top of 3 GB for D and about 154 MB for Q. Normalize both once, then process queries in batches (say
1,000 at a time, a 4 GB result per batch, or smaller), and for each batch take `argpartition` top-k per
row and keep only those indices and scores, discarding the full block. If this runs regularly, an
approximate nearest-neighbour index is the right tool, with the exact batched computation kept as the
recall baseline.

## Quiz
### Q: Which operation returns a view sharing memory with the original array `a` of shape (1000, 64)?
- [ ] `a[[0, 10, 20]]`
- [ ] `a[a[:, 0] > 0]`
- [x] `a[:, ::2]`
- [ ] `np.sort(a, axis=0)`
> Basic slicing with step sizes only changes offset and strides, so it shares the buffer. Integer and
> boolean indexing must gather scattered rows into new memory, and sort returns a new array.

### Q: `x` has shape (500,) and `y` has shape (500, 1). What is the shape of `x - y`?
- [ ] (500,)
- [ ] (500, 1)
- [x] (500, 500)
- [ ] An error, because the shapes differ
> Broadcasting aligns trailing dimensions: (1, 500) against (500, 1) stretches both to (500, 500). No
> error is raised, which is why this silent shape blow-up is a common bug in metric code.

### Q: Why is `np.argpartition` preferred over `np.argsort` for top-5 retrieval over a million scores?
- [ ] It returns the results already sorted by score
- [x] It finds the k best in linear time without fully sorting the array
- [ ] It runs on the GPU automatically
- [ ] It handles NaN scores while argsort cannot
> A full sort is O(N log N) work for an answer that needs only k items. Partitioning is linear and leaves
> the top k unordered, so you sort just those k afterwards, which is cheap.
