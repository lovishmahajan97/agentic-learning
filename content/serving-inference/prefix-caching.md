---
id: prefix-caching
module: serving-inference
name: Prefix caching
kind: pattern
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - prompting/prompt-caching
  - serving-inference/kv-cache-paging-and-eviction
  - serving-inference/prefill-vs-decode
  - serving-inference/ttft
  - serving-inference/caching
  - model-internals/kv-cache
  - prompting/system-prompt
  - agents/agent-loop
sources:
  - Zheng et al., 2023 — SGLang: Efficient Execution of Structured Language Model Programs
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Official docs: vLLM
---

## TL;DR
Prefix caching keeps the KV cache of a prompt prefix alive after the request that computed it finishes, so
the next request starting with the same tokens skips prefill for that part. It is exact because K and V
at a position depend only on the tokens before it. A shared system prompt, few-shot block or chat history
becomes a one-time cost instead of a per-request one, cutting time-to-first-token and compute, but only
when prompts share a byte-identical beginning.

## Why it exists
Real prompts are mostly repetition. A product's system prompt is the same thousands of tokens on every
call; a few-shot block is the same examples every time; turn *n* of a conversation begins with all of turns
1 to *n−1*; an agent loop re-sends the whole trajectory on every tool step. Prefill is compute-bound and
linear in prompt length, so a 3,000-token boilerplate prefix costs 3,000 tokens of matmul on every single
request, and that work produces exactly the same K and V vectors every time. Paying for it repeatedly is
pure waste.

The naive alternative, "shorten the prompt", helps but has a floor: the instructions, schema and examples
are there because quality needs them. The other naive alternative, "cache the response", only works for
identical full prompts, which almost never recur beyond FAQs. What recurs is the *beginning*. Causal
attention makes that exploitable: because position *i* attends only to positions ≤ *i*, the cache for a
prefix is a pure function of the prefix tokens and the weights. Nothing after it can change it. So the
cache computed for one request's first 3,000 tokens is bit-for-bit what the next request with the same
first 3,000 tokens would compute. The only engineering needed is to keep those blocks around, find them
again quickly, and reclaim them when memory is scarce. Paged KV memory made this cheap: blocks are
already the unit of allocation, so sharing them between requests is a reference count, not a copy.

## How it works
### Content-addressed blocks
With paging, a prefix is a sequence of full blocks. The engine hashes each block's content, typically the
hash of the previous block combined with the token ids in this block, so a block's key encodes everything
before it, not just its own 16 tokens. That matters: the same 16 tokens at a different position, or after
a different prefix, have different K and V (positions and preceding context both feed into them), so the
hash must be of the whole prefix up to and including the block.

### Lookup on arrival
When a request is admitted, the scheduler walks its prompt block by block, computing hashes and looking
each up in the cache table. The longest chain of hits from the start is the cached prefix; those physical
blocks are attached to the new request's block table with their reference counts incremented, and prefill
runs only over the remaining tokens (and, in some engines, a partial last block is always recomputed
because only full blocks are cached). TTFT drops in proportion to the fraction of the prompt that hit.

### Lifetime and eviction
When a request finishes, its blocks' reference counts drop. Blocks with zero references are not returned
to the free list immediately; they stay in the cache table, reclaimable in LRU order when live sequences
need memory. Under light load, a system prompt stays resident indefinitely; under heavy load, it is
evicted and recomputed, and the hit-rate metric tells you which regime you are in. SGLang represents
cached prefixes as a radix tree so that requests sharing a long ancestor and diverging late reuse the
ancestor and evict the leaves first; vLLM's hash table reaches the same behaviour with per-block LRU.

### What counts as "the same prefix"
Token identity, not semantic similarity. A single different token invalidates every block from that point
on. The classic mistakes are placing a timestamp, request id, user name or randomly ordered retrieval
results near the top of the prompt. The rule for prompt layout is: stable content first (system prompt,
tool definitions, few-shot examples), then slowly changing content (conversation history, document), then
the volatile part (the current question). Chat templates must also be stable: a template that emits the
date, or that formats the system prompt differently depending on the presence of tools, silently breaks
sharing.

### Multi-replica serving
A cache lives on one GPU. With several replicas behind a load balancer, the second request for a tenant
lands on a replica that has never seen its prefix unless routing is cache-aware. Options are sticky
routing by tenant or conversation, consistent hashing on a prefix hash computed at the router, or a
router that queries replicas' cache state. Without one of these, measured hit rate on a fleet is far
below what a single replica shows, and the problem is routing, not the engine.

### Managed APIs
Providers expose the same mechanism with different control surfaces: some cache automatically for prompts
above a minimum length, others require explicit cache breakpoints in the request, and most price cached
input tokens at a fraction of normal input price while charging a premium to write a cache entry. Cache
lifetime is short (minutes) unless refreshed, and the rules differ per provider and change over time, so
treat the exact numbers as something to look up, not memorize. The engineering discipline is identical:
stable prefix first, measure hit rate, do not let volatile content leak upward.

### Sampling independence and correctness
The cache stores K and V, not outputs, so it is independent of temperature, seed and sampling settings;
reusing it never changes the distribution the model samples from. The one subtlety is numerical: with
batched kernels, a cached prefix computed in one batch shape may differ in the last bits from a fresh
prefill in another, which is why cached and uncached runs are not always bit-identical even at
temperature 0.

## Example
Enable prefix caching in vLLM's offline engine and measure the effect of prompt layout. The two prompt
styles contain identical content; only the position of the volatile part changes.

```python
import time
from vllm import LLM, SamplingParams

llm = LLM("meta-llama/Meta-Llama-3-8B-Instruct", enable_prefix_caching=True,
          max_model_len=8192, gpu_memory_utilization=0.9)
sp = SamplingParams(temperature=0, max_tokens=32)

system = "You are a contracts assistant.\n" + ("Policy clause.\n" * 400)   # ~2k tokens, stable
questions = [f"Question {i}: does clause {i} allow early termination?" for i in range(20)]

def run(prompts):
    llm.generate(prompts[:1], sp)                      # warm: first call pays full prefill
    t = time.perf_counter(); llm.generate(prompts[1:], sp)
    return (time.perf_counter() - t) / (len(prompts) - 1)

good = [f"{system}\n{q}" for q in questions]                               # stable prefix first
bad  = [f"Request-id: {i}\n{system}\n{q}" for i, q in enumerate(questions)]  # volatile token first

print(f"stable-first : {run(good)*1000:.0f} ms/request")
print(f"volatile-first: {run(bad)*1000:.0f} ms/request")
# Expect the stable-first layout to be markedly faster per request: the 2k-token
# policy block hits the cache on every call. The volatile-first layout misses on
# every block because the first few tokens differ, so every request re-prefills ~2k tokens.
# llm.llm_engine metrics (or the server's /metrics endpoint) expose the prefix hit rate.
```

## Trade-offs
- Skips prefill for shared prefixes, cutting TTFT and GPU time, but holds cached blocks in VRAM that live
  sequences could use; the gain reverses when the pool is tight and cached blocks keep getting evicted.
- Forces a prompt layout discipline (stable first, volatile last) that improves hit rate, but constrains
  prompt design; it flips to irrelevant when prompts share nothing beyond a short system line.
- Cache-aware routing raises fleet-wide hit rate, but concentrates a tenant's traffic on one replica,
  reducing load balancing; worth it when prefixes are long and tenants are many.
- On managed APIs, cache writes cost more than plain input and reads cost less; it pays once a prefix is
  reused a few times within the cache lifetime, and loses money on one-off prompts.

## Pitfalls
- A timestamp, request id or user name at the top of the prompt; symptom is a prefix hit rate near zero
  despite a long identical system prompt.
- Retrieval results inserted before the question in a non-deterministic order; symptom is hits only on
  the system prompt and never on the document block, with TTFT still scaling with retrieved length.
- Round-robin load balancing across replicas; symptom is hit rate on a single replica far higher than
  the fleet's, and TTFT that depends on which replica the request landed on.
- Measuring hit rate only on a benchmark that replays the same prompt; symptom is a dashboard showing 95%
  hits and production showing 20%, because real conversations diverge after the system prompt.
- Assuming the cache stores responses; symptom is confusion when the same prompt yields different text
  at non-zero temperature, which is expected because only K and V are reused.

## When to use
Turn it on whenever prompts share a long common beginning: product system prompts, few-shot blocks, tool
schemas, multi-turn chat, and agent loops that re-send the trajectory each step; those are the majority
of production workloads. Measure prefix hit rate and TTFT before and after, and check the preemption
counter to confirm cached blocks are not starving live sequences. Skip it, or expect little, for
single-shot workloads with unique prompts such as bulk document summarization. On managed APIs, restructure
prompts so the stable part is first and add explicit cache breakpoints where the provider requires them.
In interviews, the depth expected is to explain why causal attention makes prefix reuse exact, why a
single changed token invalidates everything after it, and how routing affects hit rate on a fleet.

## Role lens
- **Engineer:** Restructures prompts so stable content comes first, enables the flag or cache
  breakpoints, and tracks hit rate and TTFT as a pair in the serving dashboard.
- **Architect:** Decides on cache-aware routing versus simple load balancing and how much of the pool
  cached prefixes may hold, trading hit rate against live concurrency.
- **FDE:** Audits a customer's prompt templates for volatile tokens near the top and shows the TTFT and
  cost difference with a before-and-after measurement on their traffic.

## Check yourself
**Q:** A customer's agent loop sends a 4k-token system prompt plus the growing trajectory on every tool
step, and complains that each step's latency grows and that prefix caching "does not work" on their
four-replica deployment. What would you check, and what would you change?
**A:** First confirm the prefix really is identical across steps: look for a timestamp, step counter or
freshly serialized tool list near the top, and check that the chat template emits the system prompt
identically whether or not tools are present. Second, check routing: four replicas behind round-robin
means each step lands on a replica that likely has not seen the prefix, so the fleet hit rate is poor
even if the engine's is fine; route by conversation id or prefix hash so a trajectory sticks to one
replica. Third, verify the trajectory is append-only: if the agent rewrites or summarizes earlier steps,
every block after the edit is invalidated, which is correct behaviour and must be weighed against context
savings. Finally check the preemption and eviction metrics; if the pool is saturated the cached blocks are
being evicted between steps, and the fix is memory or admission limits, not the cache flag. With those in
place, each step should prefill only the newest tool result, and step latency should stop growing with
trajectory length except for the decode-side cache-read term.

## Quiz
### Q: Why can the KV cache of a prompt prefix be reused exactly by a different request?
- [ ] Because the engine stores the model's output for that prefix
- [x] Because with causal attention, K and V at each position depend only on the tokens before it, so an identical prefix yields identical cache
- [ ] Because the attention weights are averaged across requests
- [ ] Because prefix tokens are not attended to by later tokens
> Causality makes the cache for positions 1..n a pure function of tokens 1..n. Nothing that follows can
> change it, so the vectors computed once are correct for any request beginning with those tokens. Outputs
> are not stored; later tokens do attend to the prefix.

### Q: A system prompt is 3,000 tokens and byte-identical across requests, but the measured hit rate is near zero. What is the most likely cause?
- [ ] The system prompt is too long to cache
- [ ] Temperature is set above zero, which disables caching
- [x] A volatile token such as a timestamp or request id appears before the system prompt in the rendered prompt
- [ ] The model uses grouped-query attention, which is incompatible with prefix caching
> Block hashes include everything before the block, so any difference at the start changes every hash.
> Length is not a problem, sampling settings do not affect K and V, and GQA simply makes the cache smaller.

### Q: What is the main reason prefix caching under-delivers on a multi-replica deployment?
- [ ] Each replica has a smaller KV pool, so eviction is more frequent
- [x] Without cache-aware routing, repeat requests land on replicas that have not computed the prefix
- [ ] Hashes are computed differently on each replica
- [ ] Replicas cannot share GPU memory over the network
> The cache is local to a GPU. If a load balancer spreads a tenant's requests across replicas, most of them
> are cold on arrival. Sticky or hash-based routing fixes it; the engine was never the problem.
