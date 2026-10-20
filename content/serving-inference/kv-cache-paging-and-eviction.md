---
id: kv-cache-paging-and-eviction
module: serving-inference
name: KV-cache paging & eviction
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - serving-inference/pagedattention
  - serving-inference/kv-cache-memory-estimation
  - serving-inference/prefix-caching
  - serving-inference/continuous-batching
  - serving-inference/cpu-offloading
  - model-internals/kv-cache
  - model-internals/context-window
sources:
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Zheng et al., 2023 — SGLang: Efficient Execution of Structured Language Model Programs
  - Xiao et al., 2023 — Efficient Streaming Language Models with Attention Sinks
---

## TL;DR
Paging stores each request's KV cache in fixed-size blocks mapped through a per-request block table, the
way an operating system pages virtual memory, so sequences of unknown length can grow without reserving
their maximum up front and without fragmenting the pool. Eviction is what happens when the pool still runs
out: the scheduler preempts whole sequences (recompute or swap them out) or drops reusable cached prefixes,
and a separate family of approximate methods discards tokens inside a sequence at a quality cost.

## Why it exists
A request's cache grows by one token per decode step and the final length is unknown until the model
emits a stop token. The straightforward allocator reserves a contiguous region sized to the maximum
context length for every admitted request. That fails in two ways. First, internal waste: a request
allowed 8k tokens that stops at 600 has pinned 8k tokens' worth of memory for its whole life, so the pool
is mostly reserved-but-empty and concurrency is a fraction of what the hardware could hold. Second,
external fragmentation: requests of different lengths finish at different times, leaving holes that are
each too small for the next arrival even though the free total is large.

The next idea, allocate contiguously but only what is used so far and grow in place, fails because the
neighbour's region is right behind you; growing means copying the whole cache to a bigger hole, which is
expensive and still fragments. Attention kernels were written to read K and V as one contiguous tensor,
which is why everyone accepted the waste for years.

Paging breaks the contiguity assumption. If the kernel can follow a table from logical token positions to
physical blocks, memory can be handed out in small fixed units on demand, waste is bounded by one block
per sequence, and any free block fits any request. The same indirection then gives you something the
contiguous design never could: two sequences can point at the same physical blocks, which is the basis of
prefix sharing. Eviction exists because paging makes the pool usable but not infinite; a policy is still
needed when demand exceeds supply, and the choice of policy is what decides whether overload shows up as
graceful queueing or as latency collapse.

## How it works
### Blocks and block tables
The engine carves the cache pool into blocks of a fixed number of tokens (vLLM's default is 16). Each block
holds K and V for every layer and KV head for those token positions. Each running sequence owns a block
table: logical block *i* of the sequence maps to some physical block number. When the sequence's current
last block is full, the allocator pops a free block from a free list and appends it to the table. The
attention kernel receives the table and, for each query, gathers keys and values block by block. This is
PagedAttention; the dedicated lesson covers the kernel in detail.

Consequences that follow directly:
- **Waste is bounded.** Only the last block of each sequence can be partially filled, so internal waste is
  under one block per sequence rather than up to a full context length.
- **Any free block serves any request.** External fragmentation disappears because allocation is in
  uniform units.
- **Admission is optimistic.** The scheduler admits a request based on the blocks its prompt needs plus a
  small reserve, not its maximum output. Far more requests run at once, with the risk that they
  collectively outgrow the pool.

### Sharing and copy-on-write
Because mapping is indirect, several sequences can map logical blocks to the same physical block with a
reference count. Parallel sampling (n candidates from one prompt) and beam search share the prompt's blocks;
when a sequence needs to write into a shared block, the engine copies it first. Prefix caching generalizes
this across requests by keeping blocks alive after their owner finishes, keyed by a hash of their content.

### Running out: sequence-level preemption
When the free list is empty and a running sequence needs a new block, something must give. Engines preempt
at the granularity of whole sequences because a sequence needs all of its blocks to take even one step;
freeing half of one helps nobody. Two mechanisms:
- **Recomputation.** Free the victim's blocks entirely, put it back in the waiting queue, and when it is
  readmitted re-run prefill over its prompt plus the tokens it had generated so far. It needs no host
  memory or transfer machinery, and the re-prefill is a compute-bound pass that batches well, which is
  why it is vLLM's default.
- **Swapping.** Copy the victim's blocks to pinned CPU memory and copy them back on resumption. Saves
  compute but is limited by PCIe bandwidth and host memory, and copying many small blocks is less
  efficient than one large transfer.

Which is cheaper is arithmetic, not doctrine. Recompute costs roughly `2 × params` FLOPs per token plus
an attention term that grows with the square of the sequence length; swap costs `bytes_per_token` over
PCIe, twice. Swap therefore gains as sequences get long (the quadratic attention term) and as the cache
per token gets small relative to the model (GQA, fp8 cache); recompute gains when the cache is fat (MHA)
or the host link is slow or shared. Measure resume latency for both on your hardware before choosing.

Victim selection is typically the most recently admitted sequences first (so that older requests, which
have already consumed the most resources, finish), and preemption is all-or-nothing per sequence. The
user-visible symptom is a pause in the token stream and a jump in TTFT-like latency for the preempted
request.

### Running out: evicting cached prefixes
Blocks kept alive for prefix reuse have a reference count of zero and are reclaimable. The engine evicts
them before preempting anything, usually in LRU order, so a prefix cache degrades gracefully under memory
pressure: hit rate falls, nobody is preempted. Some deployments add a tier below VRAM, demoting evicted
prefix blocks to host memory or local disk instead of discarding them, which turns a miss into a
transfer rather than a full re-prefill; it pays when prefixes are long and reused across minutes. SGLang organizes cached prefixes as a radix tree and evicts
leaves least-recently-used, which keeps shared ancestors (the system prompt) resident longest.

### Eviction inside a sequence
A different question is whether a single sequence must keep every token's K and V. Sliding-window
attention models only attend to the last *W* positions, so older blocks can be released as soon as the
window moves past them; this is exact for that architecture. For full-attention models, research methods
drop tokens judged unimportant (low accumulated attention, or everything except the first few "sink"
tokens plus a recent window) to bound memory for very long streams. These are approximations: they
change the model's output, sometimes imperceptibly and sometimes badly on recall-heavy tasks, and must be
evaluated like any lossy optimization. Production engines treat them as opt-in or do not ship them at all.

### What to watch
Engines expose the number of free and used blocks, the preemption count and the prefix-cache hit rate.
Preemptions above zero under normal load mean the admission policy is too optimistic for the sequence
length distribution; falling prefix hit rate with rising cache usage means the pool is too small for
both duties.

## Example
A toy block allocator with a block table per sequence and LRU reclaim of unreferenced cached blocks. It is
not an attention kernel; it shows the bookkeeping that makes paging and eviction work.

```python
from collections import OrderedDict

BLOCK = 16                      # tokens per block

class Pool:
    def __init__(self, n_blocks):
        self.free = list(range(n_blocks))
        self.refs = {}                 # physical block -> reference count
        self.cached = OrderedDict()    # content_hash -> block, LRU order (ref 0)

    def alloc(self):
        if not self.free and self.cached:            # evict an unreferenced cached block
            _, blk = self.cached.popitem(last=False)
            self.free.append(blk)
        if not self.free:
            raise MemoryError("pool exhausted: preempt a sequence")
        blk = self.free.pop(); self.refs[blk] = 1
        return blk

    def release(self, blk, content_hash=None):
        self.refs[blk] -= 1
        if self.refs[blk] == 0:
            if content_hash: self.cached[content_hash] = blk   # keep for prefix reuse
            else: self.free.append(blk)

class Seq:
    def __init__(self, pool): self.pool, self.table, self.n = pool, [], 0
    def append_token(self):
        if self.n % BLOCK == 0: self.table.append(self.pool.alloc())
        self.n += 1

pool = Pool(n_blocks=4)
a, b = Seq(pool), Seq(pool)
for _ in range(40): a.append_token()           # a uses 3 blocks (last one 8/16 full)
for _ in range(16): b.append_token()           # b uses the 4th block; pool is now full
try:
    b.append_token()                           # needs a 5th block -> no free, no cached
except MemoryError as e:
    print(e)                                   # scheduler would now preempt b (recompute or swap)
```

With 4 blocks of 16 tokens the pool holds 64 tokens, and internal waste is the 8 empty slots in `a`'s
last block. If `a` had finished and its blocks had been released with a content hash, `b`'s next
allocation would have evicted the least-recently-used of them instead of raising.

Scale the same bookkeeping to a real deployment: `max_model_len` 8,192, an 8B GQA model at 128 KB per
token, and a request that stops after 600 tokens. A contiguous allocator reserves 8,192 slots (1 GB) for
it; the request uses 600 (75 MB), so over 90% of the reservation is dead for the request's whole life.
Paged, it holds 38 blocks = 608 slots, wasting 8. On a ~50 GB pool that is the difference between about
50 resident requests and, if typical requests look like this one, several hundred, which is why paging
is what made large continuous batches possible at all.

## Trade-offs
- Smaller blocks cut internal waste and make prefix matching finer-grained, but enlarge block tables and
  reduce kernel efficiency; the balance flips toward larger blocks on long contexts with few sequences.
- Optimistic admission packs many more sequences per GPU, but makes preemption possible; it flips to
  harmful when sequence lengths are long-tailed and preemption storms recompute more than they save.
- Recomputation-based preemption is simple and batches well, but wastes prefill compute; swapping saves
  compute at the cost of PCIe traffic and host memory, and wins as sequences grow long, because
  re-prefill cost grows faster than linearly with length while transfer cost grows linearly.
- Keeping finished prefixes resident raises hit rate but competes with live sequences for blocks; under
  sustained load the LRU eviction turns prefix caching into a weak benefit rather than a guarantee.
- Token-level eviction inside a sequence bounds memory for unbounded streams but changes outputs; it
  is acceptable for chatty, low-recall workloads and not for retrieval or long-document QA.

## Pitfalls
- Reading "memory utilization 95%" as a healthy sign while the preemption counter climbs; symptom is
  periodic multi-second stalls on individual streams with no error logged.
- Setting `max_model_len` far above real prompt lengths and assuming it is free; symptom is a preemption
  storm the first time a handful of requests actually grow into that headroom, because optimistic
  admission let them in on their prompt size and the pool was sized for the typical case.
- Enabling swap-based preemption on hosts with little pinned memory or slow PCIe; symptom is preempted
  requests taking longer to resume than recomputation would have.
- Changing block size on a tuned deployment without re-measuring; symptom is a surprise drop in prefix
  hit rate or kernel throughput that nobody attributes to the flag.
- Turning on an approximate token-eviction method for long conversations; symptom is the model forgetting
  instructions from early in the chat while memory charts look great.

## When to use
Paging is not optional in a modern engine; what you decide is the policy around it. Use the preemption
counter as your primary overload signal and tune admission (`max_num_seqs`, per-iteration token budget,
`max_model_len`) until it is near zero at target load. Choose recomputation unless your sequences are so
long that re-prefill is the dominant cost. Keep prefix caching on when prompts share prefixes and watch its
hit rate under load; if it collapses, the pool is too small for both duties and you need memory, not
flags. Treat in-sequence token eviction as a research option to evaluate, not a default. In interviews,
explaining why preemption is per-sequence, how blocks bound waste, and what LRU prefix eviction buys is the
expected depth.

## Role lens
- **Engineer:** Tunes block size, admission limits and the preemption mode, and alerts on preemption count
  and free-block count rather than on raw memory utilization.
- **Architect:** Decides whether the pool is shared between live sequences and cached prefixes or split by
  replica class, and sets the context-length policy that bounds worst-case reservations.
- **FDE:** Reads a customer's engine metrics to explain mid-stream pauses as preemption, then proposes
  the admission or hardware change that removes them without breaking their latency promise.

## Check yourself
**Q:** A deployment runs at 92% KV-cache utilization with 3–5 preemptions per minute and a prefix-cache hit
rate that has fallen from 60% to 15% since traffic grew. Explain what is happening inside the pool and
what you would change, in order.
**A:** The pool is oversubscribed. Live sequences have grown until there are almost no free blocks, so the
engine first evicted unreferenced cached prefixes (hit rate collapsed) and, once those were gone, began
preempting the most recently admitted sequences, which then recompute their prefill and consume the
compute that was supposed to go to decode. First, make admission less optimistic: lower `max_num_seqs` or
the per-iteration token budget so that live sequences fit without eviction; queueing at admission is
cheaper than preemption mid-stream. Second, reduce per-sequence cost: cap `max_model_len` to the real
p99, and quantize the cache to fp8 if an eval allows. Third, if hit rate matters to TTFT, add memory
(another replica or a larger GPU) so that cached prefixes survive; prefix caching is only a benefit when
there is slack. Measure preemptions per minute after each change and stop when it is near zero at peak.

## Quiz
### Q: Why does paging bound internal fragmentation to less than one block per sequence?
- [ ] Because blocks are allocated in sorted order of sequence length
- [x] Because a sequence only ever has one partially filled block, its most recent one; all earlier blocks are full
- [ ] Because the engine compacts the cache after every decode step
- [ ] Because block size is chosen to divide the maximum context length
> Tokens are appended sequentially, so every block except the last is completely used. Contiguous
> reservation, by contrast, wastes the whole unused remainder of the maximum context for each request.

### Q: When the pool is exhausted, why do engines preempt entire sequences rather than freeing some blocks from several?
- [ ] Because block tables cannot be modified while a sequence is running
- [ ] Because partial frees would corrupt the attention mask
- [x] Because a sequence needs all of its blocks to take even one decode step, so partially freeing it makes it useless
- [ ] Because recomputation is only possible from the beginning of a sequence
> Attention for the next token reads every cached position. Removing some blocks from a live sequence
> means it cannot proceed at all, so the only useful units of eviction are whole sequences or blocks nobody
> references.

### Q: Which eviction is lossless for a full-attention model?
- [ ] Dropping tokens with the lowest accumulated attention scores
- [ ] Keeping only the first few tokens and a recent window
- [x] Reclaiming cached prefix blocks that no running sequence references
- [ ] Discarding every other block once context exceeds a threshold
> Unreferenced prefix blocks are pure optimization state; losing them costs a recompute on the next
> matching request but changes no output. Token-dropping methods alter what the model attends to and are
> approximations that must be evaluated.
