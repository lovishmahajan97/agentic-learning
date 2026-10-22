---
id: pagedattention
module: serving-inference
name: PagedAttention
kind: concept
difficulty: core
est_minutes: 14
status: agentic-verified
agent_relevance: useful
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
Agent transcripts grow unpredictably, step by step. PagedAttention stores each request's KV cache in
fixed-size blocks (usually 16 tokens), allocated on demand and shareable. Decode is memory-bound, so
the recovered memory becomes more concurrent agents per GPU.

## For agent builders
- **Why it matters for agents.** You never know how long an agent run will get: a 3-step lookup and a
  20-step research loop hit the same server. Paged allocation charges each agent only for the tokens it
  actually holds, so short runs do not pay for the longest one.
- **Where it shows up in your stack.** Inside vLLM, SGLang and TensorRT-LLM when you self-host the model
  behind your agent framework. Its knobs are `gpu_memory_utilization`, `max_model_len`, prefix caching
  and the KV-cache dtype; its symptoms are preemption counters and latency jitter.
- **What you control.** Prompt layout (shared system prompt and tool schemas first, byte-identical, so
  blocks are shared across agents), the context cap per route, and when to compact long transcripts.
- **What you can skip.** The kernel internals and online-softmax math. Know blocks, block tables,
  copy-on-write and preemption; that is enough to size and debug an agent deployment.

## Why it exists
- **Every token in flight costs memory.** About 0.5 MB per token for a Llama-2-7B-style model in fp16
  (see the KV-cache lesson for the derivation).
- **Output length is unknown at admission.** Kernels before PagedAttention expected each sequence's K and
  V to be contiguous, so the only safe move was a contiguous slab for the maximum length. For a
  2,048-token limit that is about 1 GB per request, whether it generates 20 tokens or 2,000.
- **Reservation wastes memory three ways.** *Reserved* slots hold tokens not generated yet; *internal
  fragmentation* is the part a request never reaches because it stops early; *external fragmentation* is
  holes between slabs of different sizes, too small for the next arrival.
- **Most memory was waste.** The vLLM paper measured that in prior systems only roughly 20–40% of
  KV-cache memory actually held token state.
- **The naive fixes fail.** Exact-length allocation is impossible (length unknown). Reallocate-and-copy
  copies hundreds of megabytes every few tokens and still fragments. A lower length limit refuses
  legitimate long requests. The answer was the OS move for RAM: drop physical contiguity and translate
  addresses through a table.

## How it works
### Why agent traffic is the worst case for contiguous allocation
- **Length variance is extreme.** One agent answers in 2 steps at 7.5k tokens; another loops 20 times to
  34.5k tokens. A contiguous allocator must reserve the route's maximum for both.
- **Growth happens between calls.** Each step appends a tool call and its result, then the engine
  decodes again, so the cache grows in bursts the engine cannot predict at admission.
- **Agents share a lot.** Every run starts with the same system prompt and tool schemas, often thousands
  of tokens. Block sharing stores that prefix once instead of once per agent.

### Blocks and block tables
- **Physical blocks.** The cache is carved into blocks of a fixed number of tokens, B. For one layer a
  block holds K and V for B tokens across all KV heads: `2 × n_kv_heads × head_dim × B ×
  bytes_per_element`.
- **Worked size.** Llama-2-7B, fp16, B = 16: 2 × 32 × 128 × 16 × 2 = 262,144 bytes, 256 KB per layer, or
  8 MB per block summed over 32 layers. That is exactly 16 tokens × 0.5 MB.
- **Logical blocks.** Each request owns a sequence of logical blocks (block 0 holds tokens 0–15, block 1
  holds 16–31, and so on).
- **Block table.** Maps each logical block to the physical block holding it. Logical blocks are
  contiguous; physical blocks can be anywhere in the pool. This is a page table, and the lookup is the
  address translation.

### Allocation on demand
1. **Admit** a request with only the blocks its prompt needs.
2. **Grow** by one block when the last block fills during decode.
3. **Waste** is only the unused tail of the final block, strictly less than B tokens per sequence.
4. **No external fragmentation**, because every block has the same size, so any free block fits any
   request. Fixed-size units are the one scheme where freed space is always reusable, which is why the
   OS page allocator works the same way.

### Background: the attention kernel
- **Inputs.** A block-table tensor of shape [num_seqs, max_blocks_per_seq] plus each sequence's current
  length.
- **Per sequence.** Take the new token's query, walk that row of the table, load K and V from each
  physical block, and accumulate attention block by block.
- **Online softmax.** Softmax needs the maximum and sum over *all* scores, unknown until the last block.
  The kernel keeps a running maximum *m*, a running denominator *l* and a running weighted sum of V, and
  rescales by `exp(m_old − m_new)` whenever a later block raises the maximum (the FlashAttention trick).
  The output is exact; only the layout changed.
- **The indirection costs something.** Accesses are less coalesced than for one contiguous tensor, so the
  kernel is somewhat slower. The design accepts that *because* decode throughput is limited by how many
  sequences fit, not per-sequence kernel speed.
- **The name outlived the kernel.** Current engines often run FlashAttention- or FlashInfer-family kernels
  that accept a block table directly, so "PagedAttention" now names the memory-management scheme.

### Sizing the pool
1. Measure what the weights take.
2. Run a dummy forward pass at the maximum batch to find peak activation memory.
3. Give the rest of the configured budget (vLLM's `gpu_memory_utilization`, historically defaulting to
   0.9) to the block pool.
4. Capacity in tokens is `num_blocks × B`; the scheduler admits a request only when enough free blocks
   exist for its prompt.

### Sharing and copy-on-write
- **Reference counts.** Requests reference blocks indirectly, so two can map the same physical block.
- **Within a request.** Parallel sampling (`n > 1`) and beam search share the prompt's blocks instead of
  copying them.
- **Across requests (prefix caching).** Full blocks are hashed by their content and the prefix before
  them, so a matching system prompt reuses resident blocks and skips that part of prefill.
- **Copy-on-write.** Before writing into a shared block the engine copies it, the same rule as `fork()`.

### Running out of blocks
- **Preemption.** On-demand allocation can run the pool dry mid-generation. The scheduler then swaps a
  sequence's blocks to CPU memory or frees them and recomputes the prefix later. Which an engine offers
  varies by version: vLLM's V1 engine recomputes only.

| Option | Cost grows with | Rough fit |
|---|---|---|
| Swap | cache bytes over PCIe | long sequences |
| Recompute | prefill FLOPs to rebuild | short sequences |

- **Neither wins universally.** The crossover depends on hardware and how busy the GPU already is.
- **The signal.** Preemption counters rising in the metrics mean the pool is too small for the traffic.

### Choosing the block size
| Block size | Gains | Costs |
|---|---|---|
| Larger | fewer table entries, better coalescing | more waste in the last block, coarser prefix sharing (only complete blocks are shared) |
| Smaller | less tail waste, finer sharing | more table entries, worse coalescing |

Sixteen tokens is the common compromise; deviate only after measuring with your own length distribution.

## Diagram
Each agent step is a request whose logical blocks map through its block table to scattered physical
blocks; a full last block triggers one more allocation, and an empty pool preempts some agent's step.

```mermaid
flowchart TD
    R["Request admitted with prompt"] --> T["Block table: logical to physical"]
    T --> P[("Physical block pool")]
    T --> D["Decode: append K, V"]
    D --> F{"Last block full?"}
    F -->|"no"| W["Write into tail block"]
    F -->|"yes"| A{"Free block available?"}
    A -->|"yes"| N["Allocate one block"]
    N --> T
    A -->|"no"| X["Preempt: swap or recompute"]
    X -.->|"frees blocks"| P
    K["Attention kernel"] -->|"walks table row"| P
```

## Examples
### Capacity: contiguous vs paged on an 80 GB GPU
A Llama-3-8B-class model (32 layers, 8 KV heads, head_dim 128, fp16).

| Quantity | Value |
|---|---|
| KV per token | 2 × 32 × 8 × 128 × 2 = 131,072 bytes = 128 KB |
| One 16-token block, all layers | 2 MB |
| Weights | ~16 GB |
| Budget at 0.9 | 72 GB |
| Activation peak (illustrative) | 2 GB |
| Block pool | about 54 GB = 27,648 blocks ≈ 442,000 tokens |

Chats averaging 1,500 actual tokens under an 8,192-token limit:

| Allocator | Memory per request | Requests that fit |
|---|---|---|
| Contiguous | 8,192 × 128 KB = 1 GB, always | 54 |
| Paged | ~1,500 tokens + a partial block ≈ 188 MB | around 290 (about 5× more) |

The paged kernel also never copies anything when a chat runs long. The script reproduces the arithmetic:

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

### A toy block allocator with sharing and copy-on-write
Pure Python, runnable as is. It shows on-demand growth, `fork()`-style sharing for parallel sampling and
copy-on-write of a shared tail block.

```python
class BlockPool:
    def __init__(self, num_blocks, block=16):
        self.block, self.free = block, list(range(num_blocks))
        self.ref = [0] * num_blocks

    def alloc(self):
        if not self.free:
            raise MemoryError("out of blocks: preempt a sequence")
        b = self.free.pop()
        self.ref[b] = 1
        return b

    def release(self, b):
        self.ref[b] -= 1
        if self.ref[b] == 0:
            self.free.append(b)

class Sequence:
    def __init__(self, pool, n_prompt=0):
        self.pool, self.len, self.table = pool, 0, []
        for _ in range(n_prompt):
            self.append()

    def append(self):                              # store one new token's K and V
        p = self.pool
        if self.len % p.block == 0:                # last block full, or no block yet
            self.table.append(p.alloc())
        elif p.ref[self.table[-1]] > 1:            # copy-on-write a shared partial block
            p.release(self.table[-1])
            self.table[-1] = p.alloc()             # (a real engine also copies its K/V)
        self.len += 1

    def fork(self):                                # parallel sampling: share, do not copy
        child = Sequence(self.pool)
        child.len, child.table = self.len, list(self.table)
        for b in self.table:
            self.pool.ref[b] += 1
        return child

pool = BlockPool(num_blocks=8)
a = Sequence(pool, n_prompt=40)                    # 40 tokens -> 3 blocks (16 + 16 + 8)
b = a.fork()                                       # n=2 sampling shares all 3
print(a.table, b.table, "free:", len(pool.free))   # [7, 6, 5] [7, 6, 5] free: 5
a.append(); b.append()                             # both write into the shared tail block
print(a.table, b.table, "free:", len(pool.free))   # [7, 6, 4] [7, 6, 5] free: 4
```

### Worked scenario: how many research agents fit on one GPU
Same 8B GQA model and pool as above (27,648 blocks ≈ 442,000 tokens).

Each agent starts with a 6k-token prefix (system prompt plus tool schemas) and appends ~1.5k tokens per step after the first, up to 20 steps (34.5k
tokens, about 4.2 GB of KV). Agents are spread evenly across steps 1 to 20, so the average context is
about 20,250 tokens.

| Allocator | Memory charged per agent | Agents resident |
|---|---|---|
| Contiguous, `max_model_len` 32,768 | 32,768 tokens, always | 13 |
| Paged | ~20,250 tokens + a partial block | about 21 |
| Paged + prefix caching | 6k prefix stored once, ~14,250 unique tokens each | about 30 |

- **The win is smaller than for chat.** Long agents use much of their limit, so paging alone gives
  ~1.6×, not 5×. Prefix sharing adds most of the rest, because the 6k prefix is 375 shared blocks.
- **The real limit is the tail.** If all 30 agents reach step 20 together they need ~860,000 tokens,
  so the pool empties and the engine preempts. Cap steps or compact transcripts before that point.

## Production patterns
- **Lay out agent prompts for block sharing.** Turn on prefix caching (`--enable-prefix-caching` in
  vLLM; the V1 engine enables it by default). Put the system prompt and tool schemas first and
  byte-identical across every run, with dates and user IDs after them. Monitor prefix-cache hit rate per
  agent type and time to first token.
- **Compact long agent transcripts at a step boundary.** When a run nears its context cap, summarize old
  tool results into one block and continue, instead of letting long runs drain the pool and trigger
  preemptions for every other agent. Monitor context length per step and blocks in use.
- **Profile, then set the memory fraction.** When deploying a new model or GPU, start vLLM at the
  default `gpu_memory_utilization` (0.9) and raise it only after re-profiling activation peaks with your
  real `max_model_len` and batch size. Monitor KV-cache usage and OOMs.
- **Alert on preemptions, not OOMs.** Under lazy allocation a small pool shows up as preemptions and
  latency jitter. Scrape the engine's Prometheus metrics endpoint and alert when the preemption counter
  rises steadily while KV-cache usage sits near 100%.
- **Cap context per route.** When long-document chats share a deployment with short Q&A, lower
  `max_model_len` (or route long requests to their own deployment) so a few long chats cannot drain the
  pool. Monitor block usage per route and p95 prompt length.
- **Stretch the pool with an FP8 cache.** When block usage is the bottleneck, set the KV-cache dtype to
  fp8 (`--kv-cache-dtype fp8` in vLLM) and run your eval set before rollout. It roughly doubles tokens
  of capacity per GPU versus an fp16 cache.

## Trade-offs
- **Recovered memory vs kernel speed.** You recover most memory lost to reservation and fragmentation,
  but the kernel pays an indirection and coalescing cost per block. It is a net loss only when the batch
  is too small for extra sequences to matter, such as one long request alone on a GPU.
- **More admissions vs mid-generation exhaustion.** On-demand allocation admits many more requests, but
  can run out of blocks mid-generation, so the system needs a preemption policy (swap or recompute) that
  contiguous reservation never needed.
- **Free sharing vs memory-manager complexity.** Block sharing makes beam search, parallel sampling and
  prefix caching nearly free in memory, at the cost of reference counting and copy-on-write logic that
  is harder to reason about.
- **Large vs small blocks.** Larger blocks give faster kernels and fewer table entries; smaller ones
  waste less in partial blocks and share prefixes at finer granularity. The best size flips as average
  request length shrinks.
- **More concurrent agents vs tail latency.** Admitting more agents raises GPU throughput, but when many
  runs grow long at once the pool empties and preemption adds seconds to some agent's step. Admit fewer
  agents when a slow step breaks a user-facing deadline.

## Pitfalls
- **Reading the gain as "a faster attention kernel".** The kernel is slightly slower; the paper's 2–4×
  throughput gain comes from batching more sequences. If your batch is already small, you will not see it.
- **Ignoring preemption metrics.** A pool slightly too small shows up as a throughput cliff and jittery
  latency under load, not as an OOM, because sequences are silently swapped or recomputed.
- **Expecting prefix caching to share partially identical prompts.** Only whole blocks with identical
  content and prefix are shared, so a timestamp in the first line of the system prompt defeats it entirely. The
  symptom is a prefix-cache hit rate near zero for prompts that look identical.
- **Setting `gpu_memory_utilization` to 0.98 for more blocks.** It then OOMs when another process, a CUDA
  graph or a larger-than-profiled activation peak needs the headroom.
- **Sizing agent capacity from the first step.** Load tests with fresh runs show plenty of free blocks;
  production agents at step 15 do not. Symptom: preemptions appear only hours into real traffic. Fix:
  load-test with replayed full-length agent traces.

## When to use
You do not switch PagedAttention on: every mainstream open engine (vLLM, SGLang, TensorRT-LLM and others)
uses block-based KV management. The decision is how to configure and observe it.
- **Self-hosting agents:** this is the lesson to know when your agent framework calls a vLLM or SGLang
  endpoint; with a hosted API the provider manages blocks and you only see the prompt-caching effect.
- **Sizing a deployment:** use the block arithmetic for tokens of capacity, expected tokens per request,
  and therefore concurrency.
- **In production:** watch block utilization and preemption counts. They tell you whether to lower
  `max_model_len`, raise the memory fraction, add prefix caching, or add GPUs.
- **Interviews:** explain the three kinds of waste, why fixed-size blocks remove two of them, and why the
  win is measured in concurrent sequences rather than kernel speed.

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

## Interview questions
### What problem does PagedAttention solve, and why is the analogy to virtual memory apt?
- **Problem:** output length is unknown at admission, so contiguous KV buffers must reserve the maximum
  length, wasting memory to reservation, internal and external fragmentation.
- **Analogy:** like OS paging, logical blocks map through a per-request table to physical blocks anywhere
  in the pool, so contiguity is no longer needed and any free block fits any request.

### How does paging make parallel sampling (n > 1) cheap, and what happens when two samples diverge?
- **Shared blocks by reference count:** all n samples point their block tables at the same prompt blocks,
  so the prompt's KV is stored once instead of n times.
- **Copy-on-write at divergence:** when a sample must write into a block another sample still references
  (the partially filled tail), the engine copies that block first and decrements the old one's count,
  like `fork()` in an OS.
- **Cost:** only the diverging tail blocks are duplicated, so memory grows with generated tokens per
  sample, not with prompt length times n.

### When would PagedAttention give you little or no benefit?
- **Small batches.** The win comes from fitting more sequences; one long request alone on a GPU only pays
  the indirection cost of the slower kernel.
- **Requests that always use their full length.** If actual length is close to the limit, contiguous
  reservation wastes little, so paging recovers little.

### Estimate how many 1,500-token chats fit on an 80 GB GPU for an 8B GQA model.
- **Per token:** 2 × 32 layers × 8 KV heads × 128 × 2 bytes = 128 KB.
- **Pool:** 80 × 0.9 − 16 GB weights − ~2 GB activations ≈ 54 GB, about 442,000 tokens.
- **Answer:** around 290 chats paged versus 54 with contiguous 8,192-token reservations, roughly 5× more.

### Your self-hosted agents get slow only in the afternoon, with no OOMs. How do you debug it?
- **Suspect the long tail of runs.** As the day goes on, more agents are mid-run with long transcripts,
  so the block pool empties and the engine preempts and recomputes steps.
- **Confirm with metrics:** preemption counter rising while KV-cache usage sits near 100%, and context
  length per step climbing.
- **Fix:** compact transcripts at a step boundary, cap steps per run, keep the prefix shareable, or
  admit fewer concurrent agents.

### How would you design agent prompts to get the most out of block sharing?
- **Stable prefix first:** system prompt and tool schemas byte-identical and at the top, because only
  whole blocks with identical content and prefix are shared.
- **Variable content later:** dates, user IDs and retrieved documents after the shared prefix.
- **Append only:** never rewrite earlier turns, since a change invalidates every block after it.

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

## Use cases & projects
### Use cases
- **Self-hosted agent platforms.** Paged allocation lets one GPU hold dozens of agent runs of wildly
  different lengths, because each run is charged only for the blocks it has filled.
- **Shared system prompts and tool schemas.** Many agents with the same byte-identical prefix share its
  blocks, so the per-agent memory cost is only the transcript after the shared part.
- **Parallel sampling and best-of-n planners.** Copy-on-write lets several candidate plans branch from one
  prompt without duplicating its KV cache.
- **Capacity debugging.** Preemption counts and KV-usage metrics explain latency spikes when long research
  agents and short lookups share a server.

### Mini project
**Watch blocks fill and share under an agent load (2-3 hours).**
- **Goal:** see how paged allocation and block sharing change how many agent runs fit on one GPU.
- **Steps:** serve a small instruct model with vLLM; write a script that starts 20 fake agent loops, each
  appending a tool result per step, all sharing one long system prompt; run it with prefix caching on and
  off; scrape the engine's metrics endpoint for KV-cache usage and preemptions every second.
- **Done when:** you have a chart of KV usage and preemptions over time for both runs, and a short note
  explaining the gap using block counts (prefix tokens divided by the block size).
