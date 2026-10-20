---
id: flashattention
module: model-internals
name: FlashAttention
kind: concept
difficulty: advanced
est_minutes: 15
related:
  - model-internals/self-attention
  - model-internals/multi-head-attention
  - model-internals/context-window
  - serving-inference/pagedattention
  - serving-inference/gpu-memory-optimization
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
  - ml-fundamentals/mixed-precision
  - python-for-ai/pytorch
sources:
  - Dao et al., 2022 — FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness
  - Dao, 2023 — FlashAttention-2: Faster Attention with Better Parallelism and Work Partitioning
  - Milakov and Gimelshein, 2018 — Online normalizer calculation for softmax
---

## TL;DR
FlashAttention computes exactly the same softmax attention as the textbook formula but never writes the
n×n score matrix to GPU memory. It streams blocks of keys and values through the fast on-chip SRAM,
keeps a running softmax per query row, and stores only O(n) extra state. Attention was slow not because
of its FLOPs but because of reading and writing that n² matrix in high-bandwidth memory, so an IO-aware
kernel gives large wall-clock speedups and makes long contexts fit in memory at all.

## Why it exists
The standard implementation of attention does three things in sequence: `S = Q·Kᵀ`, `P = softmax(S)`,
`O = P·V`. Each step is a separate kernel, so **S** is written to GPU memory, read back for the softmax,
**P** is written, then read again for the final matmul. For 32 heads at 8k tokens in fp16, **S** alone is
about 4.3 GB per layer, written and re-read several times per forward pass and again in backward.

The FLOPs of attention are modest, about 4·n²·d per layer, and the two matmuls run on tensor cores. The
trouble is everything between them. A GPU like the A100 can do on the order of 300 TFLOPs in fp16 but
move only about 1.5–2 TB/s from HBM, so an operation needs well over a hundred FLOPs per byte to be
compute-bound. Softmax, masking and dropout do about one FLOP per byte. They are purely memory-bound,
and the n² traffic they generate dominated attention time while the n² memory capped sequence length;
models of that era trained at 512–2,048 tokens.

The naive fix was approximation: sparse patterns, low-rank projections, kernel feature maps. They reduce
FLOPs, but they often did not reduce wall-clock time because the memory traffic stayed, and they changed
the model's behaviour. FlashAttention's insight was that the bottleneck was IO, so the right thing to
optimize was the number of HBM reads and writes, the way external-memory algorithms are designed, while
leaving the math exact.

## How it works
### The memory hierarchy the kernel is written for
HBM is large and slow relative to compute: tens of gigabytes at a few TB/s. On-chip SRAM (shared memory
per streaming multiprocessor) is tiny, around 20 MB in total on an A100 in the paper's accounting, but an
order of magnitude faster. Kernel fusion means doing as much work as possible on data while it sits in
SRAM before writing anything back.

### Tiling
Queries are split into blocks of B_r rows and keys and values into blocks of B_c rows, sized so that one
block of each fits in SRAM. For each query block, the kernel iterates over the key/value blocks: load
K_j and V_j, compute the partial scores `S_ij = Q_i·K_jᵀ` (a B_r × B_c tile that lives in SRAM), fold it
into the running output, and move on. The full **S** and **P** never exist in HBM.

### Online softmax
Softmax needs each row's maximum and the sum of exponentials over *all* n columns, which are unknown
until the last block. The trick, from Milakov and Gimelshein, is to maintain per row a running max *m*
and running denominator *l*, and to rescale when a new block raises the max:

```
m_new = max(m, rowmax(S_ij))
l_new = exp(m - m_new) · l + rowsum(exp(S_ij - m_new))
O_new = exp(m - m_new) · O + exp(S_ij - m_new) · V_j
```

Everything accumulated so far was computed relative to the old max, and multiplying by `exp(m − m_new)`
re-expresses it relative to the new one. After the last block, `O / l` is the exact softmax-weighted sum;
it is a re-association of the same sum, not an approximation. FlashAttention-2 defers the division by
*l* to the very end to cut non-matmul work.

### Backward pass by recomputation
A standard backward pass needs **P** saved from the forward pass, n² per head. FlashAttention saves only
the output **O** and one number per row, the log-sum-exp `L = m + log l`. In backward it recomputes the
tiles of **S** and **P** from **Q**, **K** and *L* on the fly, inside SRAM. That costs extra FLOPs, roughly
one more matmul's worth, yet it is faster than the standard backward because recomputation is cheaper
than reading n² values from HBM. It is the same compute-for-memory trade as gradient checkpointing,
applied at the kernel level.

### IO complexity
The paper counts HBM accesses: the standard algorithm needs Θ(n·d + n²); FlashAttention needs
Θ(n²·d² / M), where *M* is the SRAM size. For typical head dimensions (64–128) and SRAM sizes, that is
many times fewer, and the measured speedups follow the IO count rather than the FLOP count.

### Versions
FlashAttention (2022) is the algorithm above. FlashAttention-2 (2023) parallelizes across the sequence
dimension as well as batch and heads, so long sequences at small batch still fill the GPU, reduces
non-matmul FLOPs and partitions work between warps to cut shared-memory traffic; it roughly doubled
throughput over the first version. FlashAttention-3 (2024) targets Hopper GPUs with asynchronous
overlap of tensor-core matmuls and softmax, and adds FP8 support. Exact figures vary by shape and
hardware, so measure on yours.

### Where you meet it
PyTorch's `F.scaled_dot_product_attention` dispatches to a flash kernel when dtype, head dimension and
mask allow, otherwise to a memory-efficient or plain math backend. Hugging Face exposes
`attn_implementation="flash_attention_2"` or `"sdpa"`. vLLM and SGLang use FlashAttention-family or
FlashInfer kernels, and PagedAttention is FlashAttention-style tiling where the K/V tiles are fetched
through a block table instead of a contiguous buffer.

### What it does not do
It is exact softmax attention, so the FLOPs are still quadratic; it changes the constant and the memory,
not the asymptotics. FlashAttention-2 requires fp16 or bf16 inputs, head dimensions up to 256 and Ampere
or newer GPUs. It supports causal, sliding-window, variable-length (packed) sequences and ALiBi slopes,
but not an arbitrary dense additive mask; such masks fall back to other backends. PyTorch's FlexAttention
compiles custom mask patterns into fused kernels for the cases FlashAttention does not cover.

## Example
Naive attention against the flash backend on one GPU, comparing peak memory, time and the outputs. The
naive version allocates two 4.3 GB tensors for the scores and probabilities; the flash version adds only
a few hundred megabytes beyond the inputs.

```python
import time, torch, torch.nn.functional as F
from torch.nn.attention import sdpa_kernel, SDPBackend          # PyTorch 2.3+

B, h, n, dk = 1, 32, 8192, 128
q, k, v = (torch.randn(B, h, n, dk, device="cuda", dtype=torch.float16) for _ in range(3))
future = torch.triu(torch.ones(n, n, device="cuda", dtype=torch.bool), 1)

def naive():
    s = (q @ k.transpose(-1, -2)) / dk ** 0.5             # [1, 32, 8192, 8192] fp16 -> ~4.3 GB in HBM
    p = s.masked_fill(future, float("-inf")).softmax(-1)  # another ~4.3 GB, read back for the matmul
    return p @ v

def flash():
    with sdpa_kernel(SDPBackend.FLASH_ATTENTION):          # raises if the flash kernel cannot be used
        return F.scaled_dot_product_attention(q, k, v, is_causal=True)

for name, fn in (("naive", naive), ("flash", flash)):
    torch.cuda.synchronize(); torch.cuda.reset_peak_memory_stats()
    fn(); torch.cuda.synchronize(); t = time.perf_counter()
    for _ in range(5):
        out = fn()
    torch.cuda.synchronize()
    print(f"{name}: {(time.perf_counter() - t) / 5 * 1e3:6.1f} ms/call  "
          f"peak {torch.cuda.max_memory_allocated() / 2**30:5.1f} GiB")

print(torch.allclose(naive(), flash(), atol=2e-3, rtol=2e-3))   # True: same result up to fp16 rounding
# Expect roughly 9-13 GiB peak for naive versus well under 1 GiB for flash, and flash several times faster;
# the exact numbers depend on the GPU. Passing an explicit attn_mask instead of is_causal would disable flash.
```

## Trade-offs
- Exact results with O(n) extra memory instead of O(n²), paid for with constraints on dtype (fp16/bf16),
  head dimension and GPU generation; the trade flips only when you genuinely need fp32 attention or a
  dense custom bias the kernel does not support.
- The backward pass recomputes probabilities rather than storing them, which is more FLOPs for less
  memory; at very short sequences, where the n² term is small anyway, the gain over a good fused
  baseline is modest.
- The fused kernel is a black box that never materializes attention weights, so interpretability and
  debugging that need the maps must run a separate, slow eager path.
- It makes long contexts fit, but it does not make them cheap: compute is still quadratic, so beyond
  roughly 100k tokens you still need windowing, sparsity, sequence parallelism or retrieval.

## Pitfalls
- Believing the flash path is active when it silently fell back to the math backend because of fp32
  inputs, an odd head dimension or an explicit dense mask; the symptom is memory that scales with n² and
  no speedup. Force the backend with `sdpa_kernel` during testing so it fails loudly.
- Passing an explicit causal mask tensor instead of `is_causal=True`; the symptom is the same n² memory
  curve because the kernel cannot use the fast path.
- Comparing flash and eager outputs with fp32-level tolerances; the symptom is spurious "mismatch"
  alarms caused by the different summation order under fp16 rounding.
- Leaving `output_attentions=True` or an eager attention implementation in a production config; the
  symptom is throughput that collapses and OOMs at lengths the model handled in testing.
- Padding batches to the longest sequence and running flash on dense tensors; the symptom is GPU time
  that scales with the longest prompt rather than the total number of real tokens. Use variable-length
  (packed) attention or a continuous-batching engine.

## When to use
Use it whenever it is available, which in practice means ensuring you have not accidentally disabled it:
keep inputs in bf16 or fp16, head dimensions in the supported set, masks expressed as `is_causal` or
sliding-window parameters rather than dense tensors, and sequences packed rather than padded. Verify
with a profiler that the attention kernels carry flash names and that peak memory grows linearly with
sequence length. When planning hardware, note that FlashAttention-2 needs Ampere or newer and the
Hopper-specific version needs H100-class parts, which affects whether a customer's older GPUs can run
long-context workloads. In interviews, the expected story is: attention was memory-bound, tiling plus
online softmax avoids the n² matrix, recomputation makes backward fit, and all of it is exact.

## Role lens
- **Engineer:** Keeps the model on the fast path (dtype, head_dim, mask form, packing) and checks kernel
  names and memory curves in the profiler rather than trusting a config flag.
- **Architect:** Sets maximum context and GPU generation knowing that long-context feasibility depends on
  these kernels existing for the target hardware, and decides when exact attention must give way to
  windowed or retrieval-based designs.
- **FDE:** Diagnoses a customer's out-of-memory at long prompts on V100 or T4 cards, where the flash
  kernels are unavailable, and explains the fp16 and hardware prerequisites.

## Check yourself
**Q:** At 2k context, attention is a small fraction of a transformer's total FLOPs, yet FlashAttention
delivered large end-to-end training speedups. Explain why.
**A:** FLOP share is the wrong lens for a memory-bound operation. Standard attention writes the n×n
scores to HBM, reads them for softmax, writes the probabilities, reads them again for the value matmul,
and repeats most of that in backward. Those elementwise passes do about one FLOP per byte moved, far
below the ratio a GPU needs to be compute-bound, so the streaming multiprocessors idle while waiting on
memory, and attention takes wall-clock time out of proportion to its arithmetic. Fusing the steps and
keeping the tiles in SRAM removes that traffic, and storing only O(n) state per head frees memory, which
lets you raise batch size or sequence length and improves utilization of the matmul-heavy layers too.
The speedup is a bandwidth saving, not a FLOP saving.

## Quiz
### Q: FlashAttention is faster than standard attention mainly because it
- [ ] Computes fewer floating-point operations by approximating the softmax
- [x] Performs far fewer reads and writes to high-bandwidth memory by never materializing the n×n matrix
- [ ] Uses lower precision than the standard implementation
- [ ] Skips attention for positions far from the query
> The math is exact and the FLOPs are slightly higher because of recomputation; the win is IO. Tiling
> keeps score tiles in SRAM and avoids writing and re-reading the quadratic matrix.

### Q: What allows the softmax to be computed block by block without seeing a whole row first?
- [ ] Rows are sorted before processing so the maximum comes first
- [ ] Each block's softmax is computed independently and averaged
- [x] A running row maximum and running denominator are kept and earlier partial sums are rescaled when the maximum changes
- [ ] The softmax is replaced by a linear normalization
> Online softmax maintains m and l per row and multiplies accumulated terms by exp(m_old − m_new) when a
> new block raises the maximum, which reproduces the exact result after the last block.

### Q: What does the FlashAttention backward pass store instead of the n×n probability matrix?
- [ ] Nothing; it recomputes everything including the forward output
- [x] The forward output and one log-sum-exp value per row, recomputing probability tiles on the fly
- [ ] A compressed low-rank copy of the probability matrix
- [ ] Only the attention scores of the diagonal blocks
> Keeping O and the per-row log-sum-exp L is enough to regenerate any tile of P from Q and K inside
> SRAM, which costs some FLOPs but avoids storing and reading n² values from memory.
