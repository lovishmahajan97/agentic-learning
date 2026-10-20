---
id: numpy
module: python-for-ai
name: numpy
kind: tool
difficulty: core
est_minutes: 15
status: crisp
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
NumPy's `ndarray` is typed, contiguous memory plus a shape and strides, operated on by compiled loops
instead of per-element bytecode. Embedding math and metrics get compiled speed and compact memory, but
bugs live in shapes, dtypes, broadcasting and views.

## Why it exists
- **A Python list of floats is pointers to boxed objects.** Each element is a separate heap object with
  a type and a reference count. A dot product costs, per element, two pointer chases, two type checks, an
  allocation for the result and an interpreter dispatch.
- **That is slow at AI scale.** Scoring a query against 100,000 embeddings of 768 dimensions is about
  77 million multiply-adds: seconds to minutes in pure Python versus milliseconds in compiled code
  (illustrative orders of magnitude; measure on your machine).
- **It wastes memory too.** A boxed float is far larger than its 8 bytes of payload, so lists use several
  times the memory of the raw values and scatter them across the heap, ruining CPU cache behaviour.
- **NumPy's answer.** Store numbers unboxed in one buffer with one dtype, and expose whole-buffer
  operations. The loop still exists, but runs in C (or an optimized BLAS library for matrix products) over
  contiguous memory.
- **It is the common substrate.** Nearly the whole scientific Python stack, pandas, scikit-learn, and the
  tensor interop in PyTorch, is built on or speaks this array model.

## How it works
### The ndarray: buffer, dtype, shape, strides
An array is a pointer to raw bytes plus metadata:

| Field | Example | Meaning |
|---|---|---|
| `dtype` | `float32` | 4 bytes per element |
| `shape` | `(100000, 768)` | Size of each axis |
| `strides` | `(3072, 4)` | Bytes to step one position along each axis (C-ordered, row-major) |

- **Addressing.** Element `[i, j]` lives at `offset + i*3072 + j*4`.
- **Memory is exactly `n × d × itemsize`:** 100,000 × 768 × 4 = 307,200,000 bytes, about 307 MB. In
  float64 it would be twice that, which is why embeddings are usually kept in float32.

### Views versus copies
Indexing is arithmetic on strides, so many operations return a *view* sharing the buffer.

| Returns a view | Returns a copy |
|---|---|
| Basic slicing: `a[:10]`, `a[:, ::2]` | Fancy indexing: `a[[1, 5, 9]]` |
| `reshape` when possible | Boolean masks: `a[a > 0]` |
| Transpose: `a.T` just swaps the strides | |

- **Why views.** Speed: slicing a 307 MB array costs nothing.
- **The consequence.** Writing into a view writes into the original.
- **Why fancy indexing copies.** It must gather scattered elements into new memory.

### Contiguity and layout
- **Views leave arrays non-contiguous.** `a.T` of a C-ordered matrix is Fortran-ordered; `a[:, ::2]`
  skips every other element.
- **Strides cost cache.** NumPy accepts any strides, but a large stride wastes cache lines, so reducing
  along the contiguous axis is faster than along the strided one.
- **C extensions are stricter.** Code taking a raw pointer (FAISS, many C/CUDA kernels, some
  serialization paths) expects C-contiguous memory of a specific dtype. Check `a.flags["C_CONTIGUOUS"]`
  and convert once with `np.ascontiguousarray(a, dtype=np.float32)` at the boundary; that is the one place
  a copy is correct.
- **`reshape` may copy silently.** It returns a view only when existing strides can express the new
  shape, so after a transpose a reshape may cost a full copy.

### Vectorization and ufuncs
- **Ufuncs.** Element-wise operations (`+`, `np.exp`, comparisons) are compiled loops over every element.
- **Reductions take an `axis`,** the dimension that collapses: `a.sum(axis=1)` on `(N, d)` gives `(N,)`.
  Same for `sum`, `max`, `argmax`.
- **Matrix products (`@`) dispatch to BLAS,** which uses SIMD and multiple cores.
- **Many release the GIL,** so threads can overlap NumPy work.

### Broadcasting
1. Align shapes from the right.
2. Each pair of dimensions must be equal or one of them 1.
3. Size-1 dimensions are stretched virtually (stride 0), with no copy.

| Expression | Effect |
|---|---|
| `(N, d) / (N, 1)` | Divides each row by its own norm |
| `(N, d) - (d,)` | Subtracts a mean vector from every row |
| `(N,) - (N, 1)` | Silently becomes an `(N, N)` matrix: the top source of silent bugs |

### Dtypes and numerical behaviour
- **Integers wrap on overflow** without an exception: a `uint8` pixel of 250 plus 10 becomes 4.
- **float32 has about 7 significant decimal digits:** enough for similarity scores, risky for long
  accumulations.
- **NumPy scalars are not Python floats.** `float32` values do not serialize to JSON until you call
  `.item()` or `.tolist()`.
- **Mixed dtypes promote to the wider one.** `float32_array * float64_array` produces float64, so one
  float64 array (a weights vector built with `np.array([...])`, a result from an older file) can silently
  double the memory of everything downstream.
- **Scalars differ.** A plain Python scalar does not force the upcast (`emb * 0.5` stays float32). A
  NumPy `float64` scalar, such as a value pulled out of another array, does under NumPy 2's promotion
  rules, whereas NumPy 1.x let it slide, so upgrades can change dtypes. Check `result.dtype` at stage
  boundaries.

### Randomness
- **Use `rng = np.random.default_rng(seed)` and pass `rng` around** rather than seeding global state,
  because explicit generators make experiments reproducible and do not interfere across libraries.

## Examples
### Exact top-k cosine search
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

- **`argpartition`** avoids sorting 100,000 scores to find 5.
- **`keepdims=True`** keeps the norm as `(N, 1)` so the division broadcasts per row.
- **Scale limit.** Past a few million vectors, exact search like this is replaced by an approximate index.

### Batched exact search that fits in memory
`Q @ D.T` for many queries materialises a huge block. Processing queries in batches bounds the
temporary, keeping only k results per row.

```python
import numpy as np

def batched_topk(Q, D, k=10, batch=1_000):
    """Q (m, d) and D (n, d), both L2-normalized float32. Returns (m, k) indices and scores."""
    idx = np.empty((len(Q), k), dtype=np.int64)
    val = np.empty((len(Q), k), dtype=np.float32)
    for s in range(0, len(Q), batch):
        S = Q[s:s + batch] @ D.T                        # (b, n) block of scores
        part = np.argpartition(-S, k, axis=1)[:, :k]     # (b, k), unordered
        top = np.take_along_axis(S, part, axis=1)
        order = np.argsort(-top, axis=1)                 # sort only k per row
        idx[s:s + batch] = np.take_along_axis(part, order, axis=1)
        val[s:s + batch] = np.take_along_axis(top, order, axis=1)
    return idx, val
```

| Plan (768-d float32, 1,000,000 docs) | Peak score block |
|---|---|
| 50,000 queries at once | 50,000 × 1,000,000 × 4 B = 200 GB: does not fit |
| batch=1,000 | 4 GB, plus a temporary of the same size from `-S` |
| batch=100 | 0.4 GB per block |

| Embedding store (768-d) | float64 | float32 | float16 |
|---|---|---|---|
| 100,000 vectors | 614 MB | 307 MB | 154 MB |
| 1,000,000 vectors | 6.1 GB | 3.1 GB | 1.5 GB |

## Production patterns
- **Normalize and pin layout at ingestion.** Convert once with `np.ascontiguousarray(x, dtype=np.float32)`,
  divide by row norms, and store that. Monitor `np.isnan(x).any()` and the min/max of row norms per batch;
  a zero or NaN norm usually means an empty input text upstream.
- **Assert shapes and dtypes at stage boundaries.** Put `assert x.shape == (n, d) and x.dtype ==
  np.float32` where arrays enter and leave a component. Track `x.nbytes` in logs, so a float64 upcast
  shows up as a doubled number before it shows up as an OOM.
- **Memory-map big arrays.** Save with `np.save` and open with `np.load(path, mmap_mode="r")`, so workers
  share pages from the OS cache instead of each loading a copy. Monitor resident memory per worker and
  disk read throughput.
- **Bound BLAS threads per process.** When running several worker processes, set `OMP_NUM_THREADS` (or
  the BLAS-specific variable, such as `OPENBLAS_NUM_THREADS`) so cores are not oversubscribed. Watch CPU
  load average against core count and matrix-product latency.
- **Keep exact search as the recall baseline.** Run `batched_topk` on a sample of queries and compare with
  the vector database's results. Alert when recall@k drops after an index rebuild or parameter change.

## Trade-offs
- **Compiled speed vs temporaries.** Whole-array operations are fast, but every intermediate is a full
  temporary, so memory can peak at several copies of your data; in-place operators and `out=` arguments
  help.
- **Free views vs aliasing.** Slicing and transposes cost nothing, but a mutation through a view changes
  the original, a correctness hazard in shared code paths.
- **float32 vs float64.** float32 halves memory and speeds up BLAS, but loses precision in long sums or
  ill-conditioned linear algebra, where float64 is worth the cost.
- **One CPU process vs scale.** Once arrays exceed RAM or you need GPUs or autograd, PyTorch,
  memory-mapped arrays or a vector database take over.

## Pitfalls
- **Python loops over array elements.** Symptom: a NumPy script no faster than pure Python; a profiler
  shows time in the interpreter, not in NumPy calls. Fix: vectorize.
- **Accidental broadcasting of `(N,)` against `(N, 1)`.** Symptom: an unexpectedly huge array, a
  `MemoryError`, or a plausible-looking metric averaged over N² pairs. Fix: `keepdims=True` or reshape.
- **Modifying a slice and corrupting the source.** Symptom: embeddings or features change "by themselves"
  after an unrelated function ran. Fix: `.copy()` before mutating.
- **Integer overflow in fixed-width dtypes.** Symptom: negative counts or wrapped pixel values with no
  warning. Fix: widen the dtype before arithmetic.
- **Default float64 or one float64 operand.** `np.array(list_of_floats)` for embeddings, or a float64
  operand promoting a float32 pipeline. Symptom: double the expected memory and slower matrix products,
  visible in `arr.dtype` and `arr.nbytes`.
- **Transposed or sliced arrays into a C extension.** Symptom: an explicit "array is not C-contiguous"
  error or, with careless bindings, garbage results. Fix: `np.ascontiguousarray` at the boundary.

## When to use
- **Use when:** anything numerical that fits in memory on one machine: embedding similarity, metrics,
  feature arrays for scikit-learn, image and audio buffers, and interchange between libraries.
- **Think shapes first:** write the expected shape next to each line.
- **Switch when:** data is tabular with named, mixed-type columns (pandas or Polars); you need GPUs or
  gradients (PyTorch); exact search over millions of vectors is too slow (a vector index).
- **Measure:** with `nbytes`, a profiler and `%timeit` rather than guessing.
- **Interviews:** often ask you to vectorize a loop or explain broadcasting.

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
