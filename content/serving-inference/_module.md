---
id: serving-inference
title: Serving & inference
---

## Overview
This module covers what happens between a trained checkpoint and a token arriving in a user's browser,
and why serving an LLM is a different job from serving a classifier. It starts with the physics. Each
request runs a compute-bound prefill followed by a long, memory-bandwidth-bound decode loop whose working
set, the KV cache, grows while it runs. TTFT, TPOT and tokens per second are the measurements of those
two phases, and the roofline model tells you which bound you are hitting. Memory comes next: estimating
the KV cache from a model config, writing the four-line GPU memory ledger, and seeing how PagedAttention,
paging and eviction turn a fragmented pool into usable concurrency. Scheduling builds on that. Continuous
batching, prefix caching and concurrency limits decide where a replica sits on the throughput-versus-latency
curve. Then the levers for fitting and speeding up a model: tensor parallelism, CPU offloading, quantization
checked against its full-precision baseline, constrained decoding, and the engines that package all of it.
The second half is the request path around the engine: streaming over SSE, layered timeouts, cancellation,
idempotency, retries, fallbacks, rate limiting, backpressure and the three layers of LLM caching. Together
they turn a fast engine into a service that degrades predictably. By the end you can size a GPU deployment
from a config file and a traffic model, find the latency knee in a load test, pick an engine and its
settings, and build a gateway that streams, cancels and sheds load correctly. Engineers carrying the pager
need it most, followed by Architects deciding between self-hosting and hosted APIs.

## Why it matters
- **Engineer:** You set `max_num_seqs`, `max_model_len` and the memory fraction from arithmetic rather than defaults. You write the SSE handler and the client wrapper with layered timeouts, abort-on-disconnect, idempotency keys and jittered retries. You also export the TTFT, TPOT, queue-wait and preemption metrics that show which phase or limit is failing.
- **Architect:** You decide between self-hosting and a hosted API, and you own the GPU type, the tensor-parallel degree, the engine, the quantization format and the per-workload operating point on the throughput-latency curve. Those choices fix cost per million tokens, the latency SLO you can promise, and how much fallback and quota capacity has to be provisioned.
- **FDE:** Customers arrive with "it feels slow", a vendor's tokens-per-second slide, or an on-premises GPU budget that has to be justified. You need to break latency down into queue, prefill and decode on the customer's own prompts, size their hardware from the KV-cache arithmetic, and tune proxies and gateways so streaming and timeouts survive their network.

## Mental model
Picture one GPU as a fixed budget of two resources, memory bandwidth and memory capacity, with a queue in
front of it and a gateway in front of the queue. Each request takes two kinds of turn. Prefill is a single
compute-heavy pass over the prompt that sets TTFT. Decode is a long run of steps that each re-read every
weight plus the request's KV cache, and that sets TPOT. Since a decode step costs about the same for 1
sequence as for 64, throughput comes from packing many sequences into each step. KV memory caps that
packing, which is why paging, prefix sharing, quantization and tensor parallelism all reduce to "more
tokens in flight per byte". Pushing concurrency past the knee makes every request a little slower and the
queue a lot longer, so you choose an operating point and enforce it. The gateway's job is to keep the queue
honest. It streams so users see the first token early, times out and cancels so dead requests free their
slots, retries only idempotent and transient failures, falls back when the primary is down, and rejects
early when demand exceeds what the GPU can serve. Every serving question resolves to the phase, the memory
term, or the boundary it belongs to.

## Suggested order
1. model-serving — the map of the request path (API, router, scheduler, memory, kernels) that every later lesson zooms into.
2. prefill-vs-decode — the compute-bound versus bandwidth-bound split that explains every latency and cost number that follows.
3. ttft — isolates the prefill-plus-queue phase as the latency users feel first, and how prompt length drives it.
4. tpot — derives the decode floor from bytes read per step over memory bandwidth, and explains why batching barely moves it.
5. tokens-sec — separates per-stream speed from aggregate server throughput, so later benchmarks are read correctly.
6. inference-optimization — the roofline view that matches each optimization lever to the bound it can actually relieve.
7. kv-cache-memory-estimation — turns a model config into bytes per token and a concurrency ceiling, the core capacity arithmetic.
8. gpu-memory-optimization — extends that estimate into the four-line VRAM ledger and a fixed order of levers for when it does not fit.
9. pagedattention — shows how fixed-size blocks remove reservation waste and fragmentation, so recovered memory becomes concurrency.
10. kv-cache-paging-and-eviction — adds what happens when the paged pool still runs out: preemption, swap versus recompute, and prefix eviction.
11. continuous-batching — iteration-level scheduling, which depends on paged memory and is what keeps decode batches full.
12. prefix-caching — reuses shared prompt blocks across requests, the scheduling-layer fix for repeated prefill and high TTFT.
13. batching-and-concurrency-limits — finds the latency knee and enforces sequence and token limits at the engine, gateway and client.
14. throughput-vs-latency — generalises the knee into choosing an operating point per workload, with queueing as the tail.
15. tensor-parallelism-at-inference — splits a model across GPUs to fit it and pool bandwidth, limited by the interconnect.
16. cpu-offloading — computes the PCIe penalty to show when spilling weights or KV cache to host memory is acceptable.
17. quantization-quality-evaluation — the measurement ladder that decides whether a smaller, faster checkpoint is still the model you tested.
18. structured-constrained-generation — logit masking in the engine for output that parses by construction, and what that costs.
19. engines — compares vLLM, SGLang, TGI, TensorRT-LLM, llama.cpp and Ollama once you know which features to judge them on.
20. streaming — carries tokens from engine to browser over SSE, which makes TTFT the felt latency and disconnects observable.
21. timeouts — layered connect, first-token, idle and total timers derived from TPOT and `max_tokens`, propagated as one deadline.
22. cancellation — carries "stop" through every hop so expired or abandoned requests release their decode slots and KV blocks.
23. idempotency — the key-and-replay mechanism that makes an ambiguous timeout safe to retry without paying twice or acting twice.
24. retry-strategies — classified, jittered, budgeted retries that rely on idempotency and avoid layered retry storms.
25. fallbacks — what to do when retrying will not help: compatible, pre-evaluated alternatives that share one deadline.
26. rate-limiting — cost-aware per-tenant and outbound token buckets that protect shared GPUs and provider quotas.
27. backpressure — bounds aggregate admission with Little's law and fast 429/503 rejections, the overload complement to rate limits.
28. caching — closes the module by separating prompt, exact-match and semantic caches, each saving a different cost at a different risk.

## Capstone
### Brief
A mid-sized SaaS company wants to move its internal support assistant off a hosted API onto a self-hosted
open-weight model on one or two rented GPUs. Usage has grown, the per-token bill is high, and legal wants
customer tickets kept inside the company's cloud account. The assistant has a 3,000-token system prompt
with tool schemas, multi-turn chat that streams to a web UI, and a nightly job that classifies the day's
tickets into structured JSON. Over a weekend to a week, plan, deploy and harden that service. Choose a
model and GPU, then write the capacity plan from the KV-cache arithmetic and the memory ledger before
launching anything. Deploy it on a datacenter engine with deliberate settings, decide whether to quantize
or use tensor parallelism and back that choice with evidence, and sweep concurrency with production-shaped
prompts to find the latency knee. Put a thin gateway in front that streams over SSE, applies layered
timeouts, cancels on disconnect, enforces idempotency keys on message creation, retries with a budget,
falls back to the hosted API when the replica is unhealthy, rate-limits per tenant, and sheds load with
`Retry-After` once queue wait exceeds its budget. Enable prefix caching and show what it buys on the chat
traffic, and run the nightly job through constrained decoding at the throughput end of the curve.

### Deliverables
- A capacity plan that derives bytes per token from the model config, writes the four-line VRAM ledger for the chosen GPU, and converts the target request rate into a replica count using Little's law, with the resulting `max_model_len`, `max_num_seqs` and memory-fraction settings.
- A load-test report that sweeps concurrency (for example 1, 4, 16, 32, 64) with production-shaped prompt and output lengths, records TTFT p95, TPOT p95, per-stream and aggregate tokens/s and preemptions at each point, and states the chosen interactive and batch operating points.
- A quantization decision record that compares the quantized checkpoint against its full-precision baseline using KL divergence or perplexity and a golden-set task eval with identical prompts and decoding. It reports the chat and JSON-classification categories separately and gives an accept or reject verdict.
- Gateway code with SSE streaming, connect, first-token, idle and total timeouts, propagated cancellation, an idempotency-key store, classified retries with full jitter and a retry budget, a circuit-broken fallback to the hosted API, per-tenant token buckets, and queue-wait-based load shedding.
- A dashboard and runbook covering TTFT, TPOT, queue wait, KV-cache utilisation, preemptions, prefix hit rate, fallback rate and tokens generated after disconnect, with one alert per overload signal and the first action to take for each.

### Rubric
- The measured KV-cache block count and peak memory under load fall within about 10% of the ledger's prediction, and any gap is explained by a named term such as activation peak or runtime overhead.
- Enabling prefix caching cuts chat TTFT p95 by a measured, reported amount on multi-turn traffic, and the report shows that changing one early token in the system prompt removes the gain.
- When a client disconnects mid-stream, the engine aborts the sequence within about a second, as shown by engine logs and the tokens-after-disconnect metric, and the freed KV blocks show up in the cache-utilisation graph.
- At twice the planned peak load the gateway returns fast 429 or 503 responses with `Retry-After` to the excess traffic, while admitted requests stay within the TTFT and TPOT SLOs and the preemption counter stays near zero.
- Replaying a message-creation request with the same idempotency key after a forced timeout returns the stored result with no second generation, and provider calls per user request stay at or below the stated amplification limit during an injected 429 burst.
- With the primary replica killed, interactive traffic moves to the hosted fallback within the stated breaker threshold and every response carries a served-by tag. The nightly constrained-decoding job produces 100% schema-valid JSON, and its task accuracy is reported against an unconstrained baseline.
