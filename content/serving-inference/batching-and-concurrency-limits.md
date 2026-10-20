---
id: batching-and-concurrency-limits
module: serving-inference
name: Batching & concurrency limits
kind: practice
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - serving-inference/continuous-batching
  - serving-inference/prefill-vs-decode
  - serving-inference/throughput-vs-latency
  - serving-inference/tpot
  - serving-inference/ttft
  - serving-inference/backpressure
  - serving-inference/rate-limiting
  - infra-capacity-economics/queueing-theory
sources:
  - Yu et al., 2022 — Orca: A Distributed Serving System for Transformer-Based Generative Models
  - Agrawal et al., 2024 — Taming Throughput-Latency Tradeoff in LLM Inference with Sarathi-Serve
  - Pope et al., 2022 — Efficiently Scaling Transformer Inference
  - Official docs: vLLM
---

## TL;DR
Decode is memory-bandwidth bound: each step streams the whole weight set through the GPU once, no
matter how many sequences are in the batch, so batching multiplies throughput almost for free until the
GPU becomes compute-bound or the KV cache fills. Concurrency limits exist because past that knee each
added request raises everyone's latency, and because unbounded admission ends in preemption thrash or
OOM. The work is to find the knee for your traffic shape and enforce it at the engine, the gateway and
the client.

## Why it exists
Take a Llama-3-8B-class model in fp16: about 16 GB of weights. On a GPU with roughly 3.35 TB/s of memory
bandwidth (an H100 SXM's published figure), simply reading the weights once takes about 4.8 ms, which
caps a single sequence at roughly 200 tokens per second regardless of how fast the tensor cores are. The
FLOPs for that one token, about 2 × 8 billion = 16 GFLOP, take a small fraction of that time. The GPU is
mostly waiting on memory.

Now put 64 sequences in the step. The weights are read once and reused for all 64 tokens, so the step
takes about the same 4.8 ms plus the extra KV-cache reads, and aggregate throughput rises toward 64×.
That is the whole reason serving systems batch: arithmetic intensity grows linearly with batch size until
it reaches the hardware's ridge point (peak FLOPs ÷ bandwidth, roughly 300 FLOP/byte for that GPU in
bf16), after which the step becomes compute-bound and latency per token starts growing with the batch.

So why not admit everything? Three reasons. KV memory is finite and each admitted sequence consumes it
for its whole lifetime, so over-admission leads to preemption, swap traffic and eventually OOM. Latency
objectives: beyond the knee, time per output token (TPOT) climbs with every extra sequence and time to
first token (TTFT) climbs with queue depth, so throughput keeps rising while the fraction of requests
meeting their SLO, the goodput, falls. And blast radius: one tenant sending 10,000-token prompts can
stall everyone's decode if nothing bounds how many prefill tokens enter a step. Limits turn an engine
that degrades for everyone into one that queues or rejects predictably.

## How it works
### Static versus continuous batching
Static batching groups N requests, runs them until the longest finishes, and only then admits more; the
GPU idles on finished slots. Continuous (iteration-level) batching, introduced by Orca, re-forms the
batch every decode step: finished sequences leave, waiting ones join. The continuous-batching lesson
covers the scheduler; here the point is that it makes "batch size" a dynamic quantity bounded by limits
rather than a fixed number you pick.

### The three limits inside an engine
Every engine exposes some version of these (vLLM's names in parentheses):
1. **Maximum sequences per step** (`max_num_seqs`): bounds decode batch size and therefore worst-case
   TPOT once compute-bound, and bounds the per-step sampling buffers.
2. **Maximum tokens per step** (`max_num_batched_tokens`): bounds how many tokens, prompt and decode
   together, are processed in one step. With chunked prefill (Sarathi-Serve's idea), a long prompt is split into chunks that
   ride along with decode tokens, so a 20k-token prompt cannot monopolise a step and stall every
   in-flight conversation. Lower values give smoother TPOT; higher values give faster prefill.
3. **KV-cache capacity** (the block pool): the hard ceiling on tokens in flight. The scheduler admits a
   request only if its prompt fits, and preempts when generation outgrows the pool.
Each step the scheduler fills the step with decode tokens first, then admits waiting prompts up to the
token budget, *because* stalling in-flight sequences is visible to users while delaying a new one by a
few steps usually is not.

### Where the latency actually goes
Step time is roughly `max(bytes_read / bandwidth, FLOPs / peak_FLOPs) + fixed overhead`. Bytes read
include the weights *and the KV cache of every active sequence*, and the second term is the one people
forget. With 64 sequences each at 8k tokens on the model above (128 KB of KV per token), the step reads
64 × 8,192 × 128 KB ≈ 64 GB of cache on top of 16 GB of weights: about 24 ms, so roughly 40 tokens per
second per user instead of 200. The safe concurrency therefore depends on context length, not just
request count, which is why limits should be expressed in tokens.

### Little's law and the queue
Concurrency in the system equals arrival rate × residence time. If you need 10 requests/s and each
lives 8 s, you need 80 in flight. If the knee for your traffic shape is 48, the queue grows without
bound; no scheduler setting fixes that, only more replicas, a smaller model, shorter outputs or load
shedding. This is the calculation that converts an engine benchmark into a replica count.

### Enforcing the limit at three layers
- **Engine:** the parameters above plus a bounded waiting queue; when it is full, fail fast with 429/503
  rather than accepting a request that will time out anyway.
- **Gateway:** per-tenant concurrency and token-rate caps so one client cannot consume the whole knee,
  and priority or fair-share queuing where SLOs differ.
- **Client:** a semaphore sized to the share it was given, retries with jitter only on explicit
  retry-after signals, and timeouts longer than expected queueing time so retries do not amplify load.

### Measuring the knee
Sweep concurrency with prompts and output lengths that match production, record aggregate tokens/s, p50
and p95 TTFT and TPOT, and plot throughput against p95 latency. Throughput rises steeply, flattens, and
p95 latency bends upward; the bend is the knee. Re-run whenever the model, quantization, context
distribution or GPU changes, because every one of them moves it.

## Example
A load sweep against an OpenAI-compatible endpoint. The client-side semaphore is the concurrency
limit; the script reports throughput and tail latency per level so you can see where the curve flattens.
Streaming is used so TTFT and TPOT can be separated; each streamed chunk is counted as one token.

```python
import asyncio, time, httpx

URL, MODEL = "http://localhost:8000/v1/completions", "meta-llama/Llama-3.1-8B-Instruct"
PROMPT, MAX_TOK = "Explain why decode is memory-bandwidth bound.", 128

async def one(client, sem):
    async with sem:                                        # the client-side concurrency limit
        t0, first, n = time.perf_counter(), None, 0
        body = {"model": MODEL, "prompt": PROMPT, "max_tokens": MAX_TOK, "stream": True}
        async with client.stream("POST", URL, json=body, timeout=300) as r:
            async for line in r.aiter_lines():
                if line.startswith("data:") and "[DONE]" not in line:
                    n += 1
                    first = first or time.perf_counter() - t0
        total = time.perf_counter() - t0
        return first, (total - first) / max(n - 1, 1), n   # TTFT, TPOT, tokens

async def sweep(level, requests=64):
    sem, t0 = asyncio.Semaphore(level), time.perf_counter()
    async with httpx.AsyncClient() as client:
        res = await asyncio.gather(*(one(client, sem) for _ in range(requests)))
    wall = time.perf_counter() - t0
    ttft = sorted(r[0] for r in res); tpot = sorted(r[1] for r in res)
    print(f"conc={level:3d} tok/s={sum(r[2] for r in res)/wall:7.0f} "
          f"p50 TTFT={ttft[len(ttft)//2]*1e3:6.0f}ms p95 TPOT={tpot[int(len(tpot)*.95)-1]*1e3:5.1f}ms")

for level in (1, 4, 16, 64):
    asyncio.run(sweep(level))
# Expect tok/s to climb steeply then flatten while p95 TPOT bends upward: that bend is the knee.
# Re-run with your real prompt and output length distribution before choosing limits.
```

## Trade-offs
- A higher sequence limit raises throughput and lowers cost per token, but once the step is compute-bound
  or KV-read-bound every extra sequence adds to every user's TPOT.
- A lower tokens-per-step budget keeps TPOT smooth during prompt bursts, but stretches TTFT for long
  prompts because their prefill is spread over more steps.
- Rejecting at the queue (429/503) protects latency for admitted requests, but pushes retry logic and
  fairness onto clients; accepting everything is simpler and fails slowly for all.
- Per-tenant caps guarantee isolation, yet leave capacity idle when one tenant is quiet unless the
  gateway can lend unused share, which adds scheduling complexity.

## Pitfalls
- Limiting by request count only; a handful of 100k-token requests exhausts the KV pool while the count
  looks healthy. The symptom is preemption spikes and latency jitter at low request rates.
- Measuring the knee with short synthetic prompts, then deploying to traffic with long retrieved
  context. Throughput looks fine in the benchmark and TPOT doubles in production.
- Client timeouts shorter than queueing time under load, so clients retry while the original request is
  still running: load doubles exactly when the system is saturated.
- Disabling chunked prefill for peak prefill speed and then seeing in-flight conversations stall for
  hundreds of milliseconds each time a long prompt arrives.
- Setting the tokens-per-step budget very high. Engines that profile the activation peak at startup
  (vLLM) respond by shrinking the KV pool, so capacity silently drops; engines that do not profile OOM
  on a large prefill, a failure that only shows up when a big prompt and a full decode batch coincide.

## When to use
Every self-hosted deployment needs explicit limits; the question is their values. Start from the VRAM
budget to get a token ceiling, run the sweep above with production-shaped traffic to find the latency
knee, and set the sequence and token limits a little below it. Use Little's law to convert the target
request rate into replicas. Apply per-tenant caps whenever more than one team or customer shares an
engine, and always bound the queue. When using a hosted API the same thinking applies to provider rate
limits: a client-side semaphore and token budget avoid 429 storms. In interviews, be ready to derive why
batching is nearly free for decode, where it stops being free, and what you would measure to find out.

## Role lens
- **Engineer:** Sets engine limits, writes the load-sweep harness, sizes client semaphores, and adds
  queue-depth and preemption metrics so the knee is visible on a dashboard.
- **Architect:** Owns the SLO-to-capacity model: goodput targets, per-tenant isolation policy, replica
  count from Little's law, and when to split prefill and decode onto separate pools.
- **FDE:** Reproduces the customer's traffic shape in a sweep, explains why their long-document workload
  needs lower concurrency than the vendor benchmark suggested, and tunes limits on site.

## Check yourself
**Q:** A single-GPU deployment reports 3,000 tokens/s at concurrency 32 and 3,400 tokens/s at
concurrency 96, so the team wants to raise the limit to 96. What would you check before agreeing, and
what do those numbers suggest?
**A:** Throughput rose only 13% for a 3× increase in concurrency, so the engine is already past the
knee: it is compute-bound or, more likely with long contexts, bound by KV-cache reads. Per-user rate fell from
about 94 tokens/s (3,000 ÷ 32) to about 35 (3,400 ÷ 96), so each user's TPOT is roughly 2.5–3× worse at
96 for almost no extra throughput, so goodput against the
latency SLO probably fell. I would check p95 TPOT and TTFT at both levels, preemption and queue-depth
metrics, and KV block utilization, and I would re-run the sweep with production prompt and output length
distributions. If more throughput is needed, the answer is a second replica, a quantized or smaller
model, or shorter contexts, not a higher limit on the same GPU.

## Quiz
### Q: Why does adding sequences to a decode batch raise throughput almost linearly at first?
- [ ] Because the GPU runs each sequence on a separate streaming multiprocessor in parallel
- [x] Because the weights are read from memory once per step and reused for every sequence in the batch
- [ ] Because attention cost is shared across sequences that have similar prompts
- [ ] Because the sampler can pick tokens for all sequences in a single kernel launch
> Decode is bound by streaming the weights through memory bandwidth. One read serves all sequences in
> the step, so until the step becomes compute-bound or KV reads dominate, more sequences cost little
> extra time.

### Q: What does a tokens-per-step budget with chunked prefill primarily protect?
- [ ] Peak memory used by the model weights
- [ ] Time to first token for the longest prompts in the queue
- [x] Time per output token for conversations already in flight when long prompts arrive
- [ ] The accuracy of generated tokens under high load
> Without the budget, a long prompt's prefill occupies entire steps and stalls every active decode.
> Chunking spreads it over steps so TPOT stays smooth, at the cost of a slower TTFT for that long prompt.

### Q: You need 20 requests/s and each request lives about 6 s in the system. The measured knee for one
replica is 40 concurrent requests. How many replicas do you need, roughly?
- [ ] One, because 40 concurrent requests exceeds 20 requests per second
- [ ] Two, because 20 requests per second is half the knee
- [x] Three, because Little's law gives 120 concurrent requests and each replica handles 40
- [ ] Six, because 120 concurrent requests must be halved for headroom
> Concurrency = arrival rate × residence time = 20 × 6 = 120 in flight. At a knee of 40 per replica
> that is three replicas before headroom. Confusing requests per second with concurrent requests is the
> classic error.
