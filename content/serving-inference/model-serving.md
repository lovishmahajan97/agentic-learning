---
id: model-serving
module: serving-inference
name: Model serving
kind: concept
difficulty: intro
est_minutes: 12
related:
  - serving-inference/engines
  - serving-inference/prefill-vs-decode
  - serving-inference/continuous-batching
  - serving-inference/throughput-vs-latency
  - serving-inference/streaming
  - infra-capacity-economics/shared-vs-isolated-model-serving-infrastructure
  - infra-capacity-economics/serverless-gpus
  - model-internals/kv-cache
sources:
  - Pope et al., 2022 — Efficiently Scaling Transformer Inference
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Official docs: vLLM
---

## TL;DR
Model serving is everything between a trained checkpoint and an HTTP response: loading weights onto
accelerators, turning requests into token streams, scheduling many of them onto one GPU, and exposing
latency and throughput you can reason about. LLM serving is harder than classic ML serving because each
request is a stateful loop of unknown length whose working set (the KV cache) grows while it runs, so the
scheduler, not the model, decides what the hardware actually delivers.

## Why it exists
A trained model is a file of weights. Nothing about it answers a request. Someone has to put the weights
on a device, tokenize input, run the forward pass, sample, detokenize, stream the result back and do all
of that for hundreds of users at once without one user's 20k-token document starving everyone else.
That set of responsibilities is "serving", and for LLMs it is its own discipline because the obvious
approach fails on three fronts.

The obvious approach is to treat the model like any other prediction service: one request in, one
forward pass, one response out, scale horizontally. Classic ML serving works this way because a
classifier's cost is fixed per request. An LLM request is not a forward pass; it is a loop of forward
passes, one per generated token, and you do not know in advance how many there will be. A request that
asked for a one-word answer and one that asked for a 2,000-word essay arrive looking identical.

Second, the loop is stateful. Each step needs the KV cache of every earlier token in that request, so the
state lives on the GPU for the life of the request and grows every step. Memory, not compute, usually
caps how many requests a card can hold.

Third, GPUs are efficient only when busy with large matrix multiplications. A single request in decode
reads all the weights to produce one token, which leaves the arithmetic units almost idle. Only batching
many requests into each step recovers that efficiency, and batching variable-length, variable-lifetime
loops is a scheduling problem the one-request-one-pass model never had to solve. The whole module exists
to work through those three facts; this lesson is the map.

## How it works
### The layers of a serving stack
From the outside in, a production LLM deployment looks like this:

1. **API layer.** An HTTP server speaking a chat/completions protocol (most self-hosted engines expose an
   OpenAI-compatible endpoint). It handles auth, rate limits, request validation, streaming via SSE and
   cancellation when the client disconnects.
2. **Router.** When there are several replicas, something picks one. Simple round-robin works until you
   care about prefix-cache locality or model-per-tenant isolation, at which point routing becomes
   content-aware.
3. **Engine.** The process that owns the GPU(s): vLLM, SGLang, TGI, TensorRT-LLM, llama.cpp and others.
   It loads weights (optionally quantized, optionally sharded across GPUs with tensor parallelism), owns the
   KV-cache memory pool, and runs the scheduler.
4. **Scheduler.** Each iteration it decides which waiting requests to admit for prefill and which running
   requests get a decode step, subject to a token budget and free KV-cache memory. This is the heart of
   the system; continuous batching and paged memory live here.
5. **Model executor and kernels.** The forward pass itself: attention kernels that read the paged cache,
   fused matmuls, CUDA graphs to cut launch overhead, sampling on-device.

### The request lifecycle
A request arrives, is tokenized, and waits in a queue. When the scheduler admits it, its prompt is run in
one **prefill** pass, which is compute-heavy and populates the KV cache. The first token comes out of that
pass; the time it took is the **time to first token (TTFT)**. The request then joins the running batch and
receives one **decode** step per iteration alongside every other running request; the spacing between its
tokens is the **time per output token (TPOT)**. It leaves the batch when it emits an end-of-sequence token,
hits `max_tokens`, or the client cancels, and its cache blocks are freed for the next request. Because
requests join and leave at iteration granularity rather than waiting for a whole batch to finish, the GPU
stays full; that is continuous batching.

### Why memory arithmetic comes first
Weights are a fixed cost; the cache is variable. For an 8B-parameter model in fp16, weights are about
16 GB, and each token of context costs a model-specific amount (around 128 KB for a GQA model with
8 KV heads and 32 layers). The engine reserves a fraction of VRAM (vLLM's default is 90%) and gives what
is left after weights to the cache pool. Free cache tokens divided by typical sequence length is the real
concurrency limit, and it is why "how many users can one H100 serve" has no answer until you know
sequence lengths. The KV-cache memory estimation lesson does this arithmetic in full.

### Where the trade-offs live
Every serving knob trades latency against throughput. A bigger batch raises tokens per second for the
whole GPU but lengthens each user's TPOT, because each decode step now reads more cache. A larger
`max_model_len` admits longer documents but reduces how many requests fit. Quantizing weights or cache
frees memory and bandwidth at a quality cost you must measure. Speculative decoding spends extra compute to
cut per-token latency. The point of the rest of this module is to make each of those trades deliberately.

### Managed API versus self-hosting
Calling a provider's API is serving outsourced: the same machinery runs on their side, and their pricing,
rate limits and prompt-caching rules are the visible surface of it. Self-hosting buys control (data
residency, custom models, predictable cost at high utilization) at the price of owning all of the above.
The decision is covered in the infrastructure and economics module; what matters here is that the
mechanics are identical, so understanding them makes you better at both.

## Example
The smallest realistic stack: an OpenAI-compatible engine on one GPU, and a client that streams. The
server flags are the ones that encode the memory and concurrency decisions discussed above.

```python
# Terminal (one GPU, a GQA model that fits in fp16):
#   vllm serve meta-llama/Meta-Llama-3-8B-Instruct \
#       --max-model-len 8192 --gpu-memory-utilization 0.9 \
#       --max-num-seqs 128 --enable-prefix-caching
# The startup log prints how many KV-cache blocks were allocated; that number
# times the block size is the pool of context tokens the scheduler can hand out.

import time
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")

t0 = time.perf_counter()
first = None
n = 0
with client.chat.completions.stream(
    model="meta-llama/Meta-Llama-3-8B-Instruct",
    messages=[{"role": "system", "content": "You are a terse assistant."},
              {"role": "user", "content": "Explain continuous batching in 3 sentences."}],
    max_tokens=200,
) as stream:
    for event in stream:
        if event.type == "content.delta":
            if first is None:
                first = time.perf_counter() - t0      # time to first token
            n += 1
total = time.perf_counter() - t0
print(f"TTFT={first*1000:.0f} ms, chunks={n}, "
      f"TPOT≈{(total-first)/max(n-1,1)*1000:.1f} ms/chunk")
# Run two copies of this script at once: TTFT barely moves, TPOT rises slightly,
# and the server's tokens/sec roughly doubles — the signature of a batched decode loop.
```

## Trade-offs
- Batching more requests per step raises GPU throughput but lengthens each request's per-token latency;
  flips in favour of smaller batches when the product is interactive and TPOT is the user-visible metric.
- Allowing long `max_model_len` serves document workloads but shrinks concurrency because every admitted
  request may claim that much cache; flips when p99 prompt length is far below the cap and admission uses
  actual lengths.
- Self-hosting gives data control and a lower cost per token at high, steady utilization; a managed API wins
  when traffic is bursty or small, because idle GPUs cost the same as busy ones.
- Streaming tokens improves perceived latency and lets clients cancel early, but it complicates proxies,
  retries and observability; it stops being worth it for short structured outputs consumed by machines.

## Pitfalls
- Capacity-planning from weight size alone and forgetting the cache; symptom is the engine admitting far
  fewer concurrent requests than expected, or preempting them mid-generation under load.
- Measuring latency with a single request and shipping; symptom is TPOT doubling the first time real
  concurrency arrives, because decode steps now read many caches per iteration.
- Putting a generic HTTP load balancer in front of replicas with prefix caching enabled; symptom is a
  cache hit rate near zero because identical system prompts land on different GPUs.
- Treating `max_tokens` as cosmetic; symptom is a few runaway requests holding cache blocks for minutes
  and starving the queue, visible as TTFT spikes unrelated to request rate.
- Ignoring client disconnects; symptom is GPU time spent generating tokens nobody reads, showing up as
  throughput that does not translate into completed requests.

## When to use
You meet serving the moment a model leaves a notebook. Use this lesson as the map: when a question is
about *why* a number looks the way it does, find the layer it belongs to (API, router, scheduler, memory,
kernels) and go to the deep lesson for that layer: prefill vs decode for the latency split, KV-cache
memory estimation for capacity, continuous batching and paging for scheduling, engines for which software
to run. Measure TTFT, TPOT, tokens per second per GPU and cache utilization from day one, because every
later decision is made against those four. In interviews, the expected depth is to explain the request
lifecycle end to end and to name which resource (memory, bandwidth, compute) binds at each phase.

## Role lens
- **Engineer:** Stands up an engine with an OpenAI-compatible endpoint, sets memory and concurrency flags
  from measured sequence lengths, and instruments TTFT and TPOT before tuning anything.
- **Architect:** Owns the managed-versus-self-hosted decision, the GPU tier and replica count, and the
  routing design that keeps tenant isolation and cache locality from fighting each other.
- **FDE:** Deploys the stack inside a customer's VPC, explains why their latency differs from the demo,
  and translates their document sizes and user counts into a concrete GPU footprint.

## Check yourself
**Q:** A customer runs an 8B model on one 80 GB GPU and reports that p50 latency is fine but p99 TTFT
spikes to several seconds every few minutes with no change in request rate. Walk through where in the
serving stack you would look, and why.
**A:** TTFT is dominated by queueing plus prefill, so periodic spikes with flat request rate point at
admission, not load. First suspect is a few very long prompts: a 20k-token prefill is compute-heavy and,
without chunked prefill, blocks a whole iteration so every waiting request's TTFT jumps. Second is memory:
if long-running generations fill the KV-cache pool, new requests wait for blocks to free, and the engine
may preempt running sequences, which shows as both TTFT and TPOT spikes. Check the engine's metrics for
queue depth, cache utilization and preemption count at the spike times. Fixes, in order: cap `max_tokens`
and prompt length per endpoint, enable chunked prefill so long prompts are interleaved with decode steps,
and if cache utilization is pinned at 100%, either lower concurrency, quantize the cache, or add a replica.

## Quiz
### Q: Why is serving an LLM fundamentally different from serving a classification model?
- [ ] LLM weights are larger, so they must be loaded lazily from disk per request
- [x] Each LLM request is a stateful loop of unknown length whose working set grows while it runs
- [ ] LLM requests cannot be batched, so each one needs a dedicated GPU
- [ ] Classifiers return probabilities while LLMs return text, which changes the serialization layer
> A classifier costs one fixed forward pass; an LLM request runs one forward pass per generated token,
> holds a growing KV cache on the GPU throughout, and finishes at an unpredictable time. Batching is not
> only possible but essential, which is the opposite of the third option.

### Q: Which single quantity most directly determines how many concurrent requests one GPU can hold?
- [ ] Peak FLOPS of the GPU divided by model parameter count
- [ ] Number of CPU cores available to the tokenizer
- [x] Free VRAM after weights, divided by per-request KV-cache size at typical sequence length
- [ ] Network bandwidth between the API server and the engine
> Concurrency is a memory question: each running request pins its KV cache for its whole lifetime. FLOPS
> affects how fast steps run, not how many requests fit, and the tokenizer and network are rarely the
> binding resource.

### Q: A team doubles concurrent users on the same GPU and sees per-token latency rise while total tokens/sec also rises. What is happening?
- [ ] The GPU is thermally throttling under the extra load
- [ ] The tokenizer has become the bottleneck
- [x] Decode is bandwidth-bound; larger batches amortize weight reads but each step now reads more KV cache
- [ ] Prefix caching is evicting entries faster than it can fill them
> Batching lets one weight read serve many requests, so aggregate throughput climbs. But each decode step
> also reads every running request's cache, so step time grows and every user's TPOT lengthens. That is
> the expected shape of the throughput-versus-latency curve, not a fault.
