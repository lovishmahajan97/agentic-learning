---
id: pagedattention
module: serving-inference
name: PagedAttention
kind: concept
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - model-internals/kv-cache
  - serving-inference/kv-cache-memory-estimation
  - serving-inference/kv-cache-paging-and-eviction
  - serving-inference/prefix-caching
  - serving-inference/continuous-batching
  - serving-inference/gpu-memory-optimization
  - serving-inference/engines
sources:
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Official docs: vLLM
---

## TL;DR
PagedAttention stores each request's KV cache in fixed-size blocks (16 tokens is the usual size)
addressed through a per-request block table, instead of one contiguous buffer reserved for the maximum
length. Output lengths are unknown when a request arrives, so contiguous allocation must over-reserve
and wastes most of the memory; paging allocates blocks on demand, removes external fragmentation, and
lets requests share blocks. Decode is memory-bound, so the memory you recover becomes more concurrent
requests, which is where vLLM's throughput gains come from.

## Why it exists
Every token in flight costs KV-cache memory: about 0.5 MB per token for a Llama-2-7B-style model in
fp16 (see the KV-cache lesson for the derivation). The awkward fact is that when a request arrives, the
server has no idea how many tokens it will produce. Attention kernels before PagedAttention expected the
K and V tensors of a sequence to be contiguous in memory, so the only safe thing to do was to reserve a
contiguous slab for the maximum possible length at admission time. For a 2,048-token limit that is about
1 GB per request on the model above, whether the request ends up generating 20 tokens or 2,000.

That reservation strategy wastes memory in three ways. *Reserved* slots are held for tokens that have
not been generated yet, so nothing else can use them. *Internal fragmentation* is the part of the
reservation the request never reaches because it stops early. *External fragmentation* appears between
slabs of different sizes once requests with different limits come and go, leaving holes too small for the
next arrival. The vLLM paper measured that in prior systems only a minority of KV-cache memory, roughly
20–40%, actually held token state; the rest was waste.

The naive fixes do not work. Allocating the exact length is impossible because the length is unknown.
Growing a contiguous buffer by reallocate-and-copy means copying hundreds of megabytes every few tokens
and still fragments. Lowering the length limit refuses legitimate long requests. What was needed was the
same move operating systems made decades ago for RAM: stop requiring physical contiguity and translate
logical addresses through a table.

## How it works
### Blocks and block tables
The KV cache is carved into *physical blocks* of a fixed number of tokens, call it B. For one layer, a
block holds K and V for B tokens across all KV heads, so its size is `2 × n_kv_heads × head_dim × B ×
bytes_per_element`. For Llama-2-7B in fp16 with B = 16 that is 2 × 32 × 128 × 16 × 2 = 262,144 bytes, 256
KB per layer, or 8 MB per block summed over 32 layers, which is exactly 16 tokens × 0.5 MB.

Each request owns a sequence of *logical blocks* (block 0 holds tokens 0–15, block 1 holds 16–31, and
so on) and a *block table* mapping each logical block to the physical block that currently holds it.
Logical blocks are contiguous; physical blocks can be anywhere in the pool. This is a page table, and the
block table lookup is the address translation.

### Allocation on demand
A request is admitted with only the blocks its prompt needs. When the last block fills during decode,
the scheduler hands out one more. The only waste is the unused tail of a request's final block, which is
strictly less than B tokens per sequence, and because every block has the same size there is no external
fragmentation at all: any free block fits any request. This is *because* fixed-size units are the one
allocation scheme where freed space is always reusable, which is why the kernel's memory manager and the
OS page allocator work the same way.

### The attention kernel
The decode kernel takes the new token's query, walks the request's block table, gathers K and V from
each physical block, and accumulates attention block by block. The indirection costs something: memory
accesses are less coalesced than for a single contiguous tensor, so the kernel itself is somewhat slower
than a contiguous one. The design accepts that *because* decode throughput is limited by how many
sequences fit, not by per-sequence kernel speed; a slightly slower kernel over far more sequences wins.

### Sizing the pool
At startup the engine measures what the weights take, runs a dummy forward pass at the maximum batch to
find peak activation memory, and gives the remainder of its configured budget (vLLM's
`gpu_memory_utilization`, historically defaulting to 0.9) to the block pool. Capacity in tokens is simply
`num_blocks × B`, and the scheduler admits a request only when enough free blocks exist for its prompt.

### Sharing and copy-on-write
Because requests reference blocks indirectly, two requests can map the same physical block, tracked with
a reference count. Parallel sampling (`n > 1`) and beam search share the prompt's blocks instead of
copying them. Prefix caching extends this across requests: full blocks are hashed by their content (and
the prefix before them), so a new request whose system prompt matches an earlier one reuses the resident
blocks and skips that part of prefill. When a sequence needs to write into a shared block, the engine
copies it first, the same copy-on-write rule as `fork()`.

### Running out of blocks
On-demand allocation means the pool can run dry mid-generation. The scheduler then preempts a sequence,
either by swapping its blocks to CPU memory or by freeing them and recomputing the prefix later (which of
the two an engine offers varies by version: vLLM's V1 engine recomputes only). Recompute is often cheaper
for short sequences because prefill is compute-efficient; swap is cheaper for long ones.
Either way, preemption counters rising in the metrics is the sign the pool is too small for the traffic.

### Choosing the block size
Larger blocks mean fewer table entries and better memory coalescing in the kernel, but more waste in each
sequence's last block and coarser prefix-cache sharing (only complete blocks are shared). Smaller blocks
reverse the trade. Sixteen tokens is the common compromise.

## Example
Capacity for a Llama-3-8B-class model (32 layers, 8 KV heads, head_dim 128, fp16) on an 80 GB GPU.
KV per token is 2 × 32 × 8 × 128 × 2 = 131,072 bytes = 128 KB, so a 16-token block is 2 MB across all
layers. With weights at ~16 GB, a 0.9 memory budget (72 GB) and an illustrative 2 GB activation peak,
about 54 GB goes to the pool: 27,648 blocks, roughly 442,000 tokens of KV capacity.

Compare two allocators serving chats whose actual length averages 1,500 tokens under an 8,192-token
limit. Contiguous reservation holds 8,192 × 128 KB = 1 GB per request no matter what, so 54 requests fit.
Paged allocation holds about 1,500 tokens plus a partial block per request, roughly 188 MB, so around 290
requests fit in the same pool, about 5× more, and the kernel never has to copy anything when a chat runs
long. The script below reproduces the arithmetic so you can plug in your own model.

```python
def kv_capacity(gpu_gb, weights_gb, util=0.9, act_gb=2.0,
                layers=32, kv_heads=8, head_dim=128, dtype_bytes=2, block=16):
    per_token = 2 * layers * kv_heads * head_dim * dtype_bytes          # bytes
    pool = (gpu_gb * util - weights_gb - act_gb) * 2**30
    blocks = int(pool // (per_token * block))
    return per_token, blocks, blocks * block

per_tok, blocks, tokens = kv_capacity(80, 16)
print(f"{per_tok/1024:.0f} KB/token, {blocks} blocks, {tokens} tokens of KV")
for avg_len, max_len in [(1500, 8192), (6000, 8192)]:
    contiguous = tokens // max_len                       # reserves max_len per request
    paged = tokens // (avg_len + 15)                     # actual tokens + at most one partial block
    print(f"avg {avg_len}: contiguous fits {contiguous}, paged fits {paged}")
```

## Trade-offs
- You recover most of the memory lost to reservation and fragmentation, but the attention kernel pays an
  indirection and coalescing cost per block, which is a loss only when a GPU serves one long request.
- On-demand allocation admits many more requests, but it can run out of blocks mid-generation, so the
  system needs a preemption policy (swap or recompute) that contiguous reservation never needed.
- Block sharing makes beam search, parallel sampling and prefix caching nearly free in memory, at the cost
  of reference counting and copy-on-write logic that makes the memory manager harder to reason about.
- Larger blocks give faster kernels and fewer table entries; smaller blocks waste less in partial blocks
  and share prefixes at finer granularity. The best size flips as average request length shrinks.

## Pitfalls
- Reading the paper's throughput gain as "a faster attention kernel". The kernel is slightly slower; the
  2–4× comes from batching more sequences. If your batch is already small, you will not see it.
- Ignoring preemption metrics. A pool that is slightly too small shows up as a throughput cliff and
  jittery latency under load, not as an OOM, because sequences are silently swapped or recomputed.
- Expecting prefix caching to share partially identical prompts. Only whole blocks with identical content
  and prefix are shared, so a timestamp in the first line of the system prompt defeats it entirely.
- Setting `gpu_memory_utilization` to 0.98 to get more blocks and then OOMing when another process, a
  CUDA graph or a larger-than-profiled activation peak needs the headroom.

## When to use
You do not switch PagedAttention on; every mainstream open engine (vLLM, SGLang, TensorRT-LLM and
others) uses block-based KV management, so the decision is how to configure and observe it. Reach for the
block arithmetic when sizing a deployment: tokens of capacity, expected tokens per request, and therefore
concurrency. Watch block utilization and preemption counts in production; they tell you whether to lower
`max_model_len`, raise the memory fraction, add prefix caching, or add GPUs. In interviews, the expected
depth is to explain the three kinds of waste, why fixed-size blocks remove two of them, and why the win is
measured in concurrent sequences rather than kernel speed.

## Role lens
- **Engineer:** Tunes block pool size, `max_model_len` and prefix caching in the engine config, and reads
  preemption and cache-hit metrics to diagnose latency jitter under load.
- **Architect:** Translates block capacity into a concurrency and context-length policy per GPU tier, and
  decides whether prompt design should favour shared prefixes to exploit block sharing.
- **FDE:** Explains to a customer why their self-hosted engine serves 5× more chats than their old
  contiguous deployment, and why a dynamic header in their system prompt kills prefix reuse.

## Check yourself
**Q:** A team sees aggregate throughput collapse by half whenever traffic shifts from short Q&A to long
document chats, with no OOM errors. Using PagedAttention concepts, what is happening and what would you
change?
**A:** Long chats consume many more blocks per sequence, so the pool runs out mid-generation and the
scheduler starts preempting sequences, swapping their blocks to CPU or dropping them and recomputing the
prefix. Both cost time and the recomputation or swap traffic halves useful throughput while never raising
an OOM, because allocation is lazy. I would confirm with the preemption and block-usage metrics, then
either shrink demand per sequence (lower `max_model_len`, shorter retrieved context, fp8 KV cache),
increase supply (higher memory fraction after re-profiling activation peaks, a GQA model, more GPUs with
tensor parallelism), or cap concurrency so admitted requests finish instead of thrashing. If the chats
share a long system prompt, enabling prefix caching recovers blocks too.

## Quiz
### Q: Where does most of PagedAttention's throughput improvement come from?
- [ ] A faster attention kernel that reads memory in larger contiguous chunks
- [x] Fitting far more concurrent sequences in the same KV-cache memory because little is wasted
- [ ] Skipping attention over padding tokens inside each block
- [ ] Storing keys and values in 8-bit precision inside each block
> The paged kernel is actually slightly slower than a contiguous one because of the indirection. The
> gain comes from eliminating reservation and fragmentation waste, so many more sequences are decoded
> per step on a memory-bound workload.

### Q: Why does fixed block size eliminate external fragmentation?
- [ ] Because blocks are allocated at startup and never freed
- [ ] Because the block table compacts memory after each request finishes
- [x] Because any free block can satisfy any allocation, so freed space is always reusable
- [ ] Because blocks are always allocated in contiguous runs per sequence
> External fragmentation arises when freed holes are the wrong size for the next request. With one
> block size there is no wrong size; the only waste left is the unused tail of a sequence's last block.

### Q: Two requests share a 400-token system prompt but one adds a date on its first line. What happens
to block sharing under prefix caching?
- [ ] Both still share all prompt blocks because the hash ignores digits
- [ ] They share blocks after the differing line once the content realigns
- [x] They share nothing, because block hashes include the full prefix and diverge from the first block
- [ ] They share blocks only if both requests arrive in the same batch
> Blocks are identified by their content and everything before them, so a difference in the first block
> changes every subsequent hash. Shared content must be a byte-identical prefix to be reused.
