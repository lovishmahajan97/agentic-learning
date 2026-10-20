---
id: continuous-batching
module: serving-inference
name: Continuous batching
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - serving-inference/batching-and-concurrency-limits
  - serving-inference/prefill-vs-decode
  - serving-inference/kv-cache-paging-and-eviction
  - serving-inference/throughput-vs-latency
  - serving-inference/tpot
  - serving-inference/engines
  - ml-fundamentals/batch-size
  - infra-capacity-economics/queueing-theory
sources:
  - Yu et al., 2022 — Orca: A Distributed Serving System for Transformer-Based Generative Models
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Agrawal et al., 2023 — SARATHI: Efficient LLM Inference by Piggybacking Decodes with Chunked Prefills
---

## TL;DR
Continuous batching schedules at the granularity of a single decode iteration instead of a whole request:
every step, finished sequences leave the batch and waiting ones join, so the GPU runs a full batch
continuously rather than idling while the longest request in a static batch finishes. It is what makes
bandwidth-bound decode efficient, because one read of the weights then serves dozens of tokens, and it
is the reason modern engines report throughput an order of magnitude above naive servers.

## Why it exists
Decode is memory-bandwidth-bound: one request produces one token per full read of the weights. The only
way to use the hardware is to produce many tokens per read, which means batching requests so that one
step advances all of them. Classic request-level batching does this the way every ML server did: collect
*B* requests, run them together until all are done, then collect the next *B*. For fixed-cost models that is
fine. For generation it fails because requests do not finish together. One stops after 20 tokens, another
runs to 2,000. In a static batch the early finisher's slot sits idle, padded with nothing useful, until the
longest request ends, and no new request can start until the whole batch drains. Measured utilization
collapses toward that of the single longest sequence, and arriving requests see queueing delay equal to
the remaining life of the current batch.

The patch, "make batches smaller or time-box them", trades one waste for another: small batches lose the
bandwidth amortization that was the whole point, and time-boxing kills long generations. The actual fix
is to stop thinking of the batch as a unit of work. The model does not care which sequences are in a step;
every step is just "take one token from each of these sequences". So the batch membership can change
between steps. A finished sequence frees its slot and its KV memory immediately; a waiting request is
prefilled and joins the next step. The scheduler becomes a loop that runs once per iteration, which is why
the original paper called it iteration-level scheduling. Orca, which introduced it, still reserved each
sequence's maximum length contiguously, so only a small batch fit in memory; paged KV memory (vLLM) was
the enabler that let the batch grow to the sizes the hardware could use, because joining and leaving
sequences no longer require contiguous allocation or copying and no slot is reserved for tokens that
may never be generated.

## How it works
### The scheduler loop
Each iteration the engine does roughly this:

1. **Retire.** Remove sequences that emitted a stop token, reached `max_tokens`, or were cancelled; free
   their KV blocks.
2. **Admit.** Pull waiting requests from the queue, oldest first (or by priority), as long as two budgets
   hold: the number of running sequences stays under `max_num_seqs`, and the tokens to be processed this
   iteration stay under `max_num_batched_tokens`. A new request's prompt counts its full length against the
   token budget because its prefill will run in this step; a running request counts one token.
3. **Check memory.** Ensure every running sequence can get at least one more block; if not, preempt the
   most recently admitted ones (recompute or swap) until the rest fit.
4. **Run one forward pass** over the union: prefill tokens for newcomers and one decode token for each
   running sequence, packed into one batch with per-sequence block tables.
5. **Sample and stream** one token per sequence to its client, then loop.

### Why one forward pass can mix prefill and decode
A transformer layer applies the same weights to every token in the batch regardless of which sequence it
belongs to; only attention needs to know the boundaries, and with block tables each query reads its own
sequence's cache. So a step with 3 newcomers of 500 tokens each plus 60 decoding sequences is a single
batch of 1,560 tokens, which is close to compute-bound and therefore efficient. The decode tokens ride
along almost free, because the weights were going to be read anyway.

### The interference problem and chunked prefill
That same mixing has a cost: a 10k-token newcomer makes the iteration take as long as its prefill, and
all 60 decoding users see a pause. Early implementations chose a policy: prefill-first (admit eagerly,
accept TPOT spikes, minimize TTFT) or decode-first (defer admissions until decode is quiet, accept TTFT
growth). Chunked prefill removes the dilemma by capping the prefill tokens per iteration and splitting a
long prompt across several steps, each of which also carries every decode token. TPOT stays smooth, the
long prompt's TTFT grows a little, and utilization is high every step because each step is filled up to
the token budget.

### Throughput versus latency inside the loop
Per-step time grows slowly with batch size (weights are read once; cache reads and compute grow with the
number of tokens), while tokens produced per step grow linearly. So throughput rises steeply with
concurrency until the step becomes compute- or cache-bound, and every user's TPOT rises along the way.
`max_num_seqs` and the token budget are the two dials: raise them to sell throughput, lower them to sell
latency. Queueing theory applies in front of the loop. Little's law sizes the batch you will actually
need: running sequences ≈ arrival rate × time each spends generating. At 5 requests/s and 300 output
tokens at ~20 ms TPOT (6 s each), about 30 sequences are in flight on average, so a `max_num_seqs` of 16
guarantees a growing queue however fast each step is. And if arrivals exceed what the loop retires, the
queue grows without bound and TTFT explodes, which is the signal for backpressure or more replicas.

### What it does not fix
Continuous batching maximizes GPU use; it does not shorten any single request's decode below the
bandwidth floor, it cannot make a batch of one efficient, and it makes memory the binding constraint
because the batch is now limited by how many caches fit rather than by a fixed batch size. That is why
this lesson and the KV-cache lessons are inseparable.

## Example
A simulation of static versus continuous batching for the same workload, using an illustrative step-time
model: a step costs a fixed weight-read time plus a small per-token cost. The output lengths are
deliberately skewed, as real traffic is.

```python
import random
random.seed(0)
N, MAX_SEQS = 200, 32
lengths = [random.choice([20, 50, 100, 400, 1500]) for _ in range(N)]   # output tokens per request

def step_ms(n_tokens):               # illustrative: 5 ms weight read + 0.02 ms per token
    return 5.0 + 0.02 * n_tokens

def static_batching():
    t = 0.0
    for i in range(0, N, MAX_SEQS):
        batch = lengths[i:i + MAX_SEQS]
        for step in range(max(batch)):                       # batch lives until its longest finishes
            active = sum(1 for L in batch if L > step)       # idle slots are wasted
            t += step_ms(active)
    return t

def continuous_batching():
    t, waiting, running = 0.0, list(lengths), []
    while waiting or running:
        running = [L for L in running if L > 0]              # retire finished sequences
        while waiting and len(running) < MAX_SEQS:           # admit newcomers into freed slots
            running.append(waiting.pop(0))
        t += step_ms(len(running))
        running = [L - 1 for L in running]
    return t

total_tokens = sum(lengths)
for name, fn in (("static", static_batching), ("continuous", continuous_batching)):
    ms = fn()
    print(f"{name:11s}: {ms/1000:6.1f} s total, {total_tokens/ms*1000:8.0f} tok/s")
# With this seed: static ~54 s (~1.6k tok/s), continuous ~20 s (~4.5k tok/s), close to 3x,
# because static batches spend most steps at low occupancy waiting for the 1500-token
# outliers. Make the length skew harsher and the gap widens; make lengths uniform and it vanishes.
```

The simulation ignores prefill and memory, which is exactly why it is clean: it isolates the one effect
that continuous batching is responsible for. Add a random long prefill to `step_ms` for newcomers and you
reproduce the TPOT spike that chunked prefill exists to smooth.

## Trade-offs
- Keeps the GPU saturated and multiplies throughput, but every admitted request lengthens each step and
  therefore every user's TPOT; the balance flips toward smaller `max_num_seqs` for interactive products.
- Admitting newcomers eagerly minimizes TTFT but injects prefill into decode steps; deferring them does the
  opposite; chunked prefill blunts the trade at the cost of slightly longer TTFT on long prompts.
- Iteration-level scheduling makes memory, not batch size, the limit, which packs far more requests but
  introduces preemption as a failure mode when the sequence-length tail is long.
- A large per-iteration token budget raises utilization on long-prompt workloads but makes each step
  heavier; on chat workloads with short prompts a smaller budget gives smoother streaming.

## Pitfalls
- Benchmarking with uniform output lengths; symptom is a load test that shows little gain over static
  batching, followed by surprise when production's skewed lengths behave completely differently.
- Raising `max_num_seqs` until throughput peaks and shipping that; symptom is excellent tokens/sec with
  user complaints that text streams slowly, because TPOT was never on the dashboard.
- Leaving chunked prefill disabled on a mixed workload; symptom is periodic stalls across all streams that
  correlate with the arrival of long prompts rather than with request rate.
- Letting the waiting queue grow unbounded; symptom is TTFT climbing linearly over minutes under steady
  overload, because the loop retires fewer sequences per second than arrive.
- Assuming continuous batching makes single-request latency better; symptom is disappointment when a
  lightly loaded server's TPOT equals the bandwidth floor regardless of settings.

## When to use
Every production LLM engine does this; what you choose is the dials. Set `max_num_seqs` and the token
budget from a load test that records TTFT, TPOT and tokens/sec together, and pick the point where TPOT is
still inside the product's budget. Enable chunked prefill unless the workload is purely short prompts.
Watch queue depth and preemptions as the overload signals, and add replicas or backpressure when the
queue grows faster than it drains. If you are building a thin server over a raw model library rather than
an engine, this is the first feature you are missing, and the reason to switch to an engine. In interviews,
explaining why static batching wastes slots, how iteration-level scheduling fixes it, why it needs paged
memory, and what chunked prefill adds is the expected narrative.

## Role lens
- **Engineer:** Tunes `max_num_seqs`, the per-iteration token budget and chunked prefill from load tests,
  and plots TPOT against concurrency to find the knee for the product's latency budget.
- **Architect:** Sets the throughput-versus-latency policy per endpoint class, decides replica counts from
  the loop's retirement rate versus arrival rate, and defines the backpressure contract.
- **FDE:** Demonstrates to a customer why their homegrown batching server caps out early, and reproduces
  their stutter by injecting long prompts into a loaded replica before proposing chunked prefill.

## Check yourself
**Q:** A team's engine shows 85% KV-cache utilization, average batch size 12, and users report that
responses stream fast at night and slowly during the day. Request rate doubles during the day. Explain
what the scheduler is doing and how you would tune it without buying hardware.
**A:** (Note the metric: KV-cache utilization, not GPU memory, which an engine pre-allocates and which
therefore always reads ~90%.) At night the running batch is small, so each decode step reads the
weights plus a few caches and TPOT sits near the bandwidth floor. During the day the loop admits more sequences up to `max_num_seqs` or
the memory limit; average batch 12 is a daily average hiding daytime batches several times larger. Each
step now reads many caches and carries newcomers' prefill, so TPOT grows and streaming slows; that is the
expected throughput-for-latency trade, not a bug. First, put TPOT and batch size per step on the
dashboard to confirm. Then enable chunked prefill so daytime admissions stop injecting stalls. Next,
decide the TPOT budget and cap `max_num_seqs` at the concurrency where the load test crosses it, accepting
that excess requests queue and TTFT rises; if the queue then grows without bound, the fleet is undersized
and backpressure or a replica is the honest answer. Prefix caching for the shared system prompt reduces
prefill tokens per iteration and helps both metrics. Only after those should quantization or hardware
enter the conversation.

## Quiz
### Q: What is the core change continuous batching makes relative to static request-level batching?
- [ ] It pads shorter sequences so that all sequences in a batch finish together
- [x] It lets sequences join and leave the batch at every decode iteration rather than waiting for the whole batch to finish
- [ ] It runs prefill and decode on separate GPUs
- [ ] It increases the batch size until the GPU is compute-bound
> The unit of scheduling becomes one iteration. Finished sequences free their slots immediately and
> waiting requests fill them, so occupancy stays high regardless of length skew. Padding is what static
> batching does; disaggregation and batch sizing are separate techniques.

### Q: Why does continuous batching gain so much from paged KV-cache memory?
- [ ] Paging compresses the cache so more sequences fit in a static batch
- [x] Without paging each sequence reserves its maximum length contiguously, so few fit and the batch stays small; blocks let sequences join, grow and leave without reservations or copies
- [ ] Paging allows prefill to be skipped for new sequences
- [ ] Paging makes decode compute-bound
> Iteration-level scheduling works without paging (Orca did it), but contiguous worst-case reservations
> cap the number of resident sequences far below what memory could hold, and growing in place forces
> copies. Block-based allocation removes both limits. Skipping prefill is prefix caching, a different feature.

### Q: What problem does chunked prefill solve inside a continuous-batching scheduler?
- [ ] It reduces the total compute required to prefill a long prompt
- [ ] It lets prompts exceed the model's context window
- [x] It prevents a long newcomer's prefill from making one iteration so slow that every decoding user sees a stall
- [ ] It allows prefill to run on the CPU while decode runs on the GPU
> Mixing a long prefill into one step stretches that step for everyone. Chunking spreads the prefill
> across several steps, each still carrying all decode tokens, so TPOT stays smooth. Total work and
> context limits are unchanged.
