---
id: tokens-sec
module: serving-inference
name: Tokens per second
kind: metric
difficulty: core
est_minutes: 13
related:
  - serving-inference/tpot
  - serving-inference/throughput-vs-latency
  - serving-inference/continuous-batching
  - serving-inference/batching-and-concurrency-limits
  - infra-capacity-economics/capacity-planning
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
  - infra-capacity-economics/unit-economics-per-successful-task
  - evals/benchmarking
sources:
  - Zhong et al., 2024 — DistServe: Disaggregating Prefill and Decoding for Goodput-optimized Large Language Model Serving
  - Official docs: vLLM benchmarking
---

## TL;DR
"Tokens per second" names two different numbers: the decode speed of one stream (roughly `1 / TPOT`,
what a user sees) and the aggregate output of a whole server (all concurrent streams summed, what you
pay for). They move in opposite directions as concurrency rises. A quoted figure means nothing
without which tokens (input, output or both), the concurrency, prompt and output lengths, hardware,
precision and percentile. Use it for capacity and cost per token; never compare it across tokenizers.

## Why it exists
Serving an LLM is selling tokens produced per unit of GPU time, so the business, the capacity plan
and the user experience all need a rate. The problem is that the obvious rate is overloaded. A
vendor slide says "1,200 tokens/s"; a user complains the assistant "only does 30 tokens/s"; an
engineer sizes a cluster from a benchmark at concurrency 1. All three are using the same words for
different quantities, and the mistakes compound: sizing from single-stream speed can undershoot
capacity by an order of magnitude, while judging user experience from aggregate throughput hides
that each stream is crawling.

The naive fix, "just report one tokens/s per deployment", fails because the number is not a property
of the deployment. It depends on how many requests are in flight, how long their prompts and answers
are, whether the measurement counts prompt tokens (which prefill chews through tens of times faster
than decode produces output), and even which tokenizer defines a token. Two models given the same
English text can differ in token count by double-digit percentages, and far more on code or
non-Latin scripts. Without pinning those variables down, tokens/s is marketing, not measurement.
Pinning them down is what this lesson is about.

## How it works
### The four numbers hiding in one phrase
1. **Per-stream output tokens/s.** One request's decode rate after its first token: about `1 / TPOT`.
   This is what a reader perceives.
2. **Aggregate output tokens/s.** Output tokens produced by the whole replica per second across all
   concurrent requests. In steady state it is roughly `concurrency × per-stream rate`, and it is the
   number that sets cost per token.
3. **Input (prefill) tokens/s.** Prompt tokens processed per second. Prefill is compute-bound and
   batched, so this is typically one to two orders of magnitude higher than output rate on the same
   hardware.
4. **"Total" tokens/s.** Input plus output over wall time. It is dominated by the cheap input term, so
   it makes any system look fast; treat a quote that does not say "output" as this number.

Alongside these sit requests per second and *goodput*: the rate of requests (or tokens) served
*within* the latency SLO. Goodput is the honest capacity figure because it refuses to count tokens
that arrived too late to matter.

### How they relate
Per-stream rate falls slowly as concurrency rises, because each decode step streams the weights once
for all sequences and only KV-cache reads grow with the batch. Aggregate rate therefore climbs almost
linearly at first, then bends as KV reads dominate or the GPU becomes compute-bound, and finally
stops when KV memory is full and new requests queue. The two curves cross whatever SLO you have at
some concurrency; that crossing, not the plateau, is the capacity you can sell for interactive
traffic.

### What moves the numbers
- **Bytes per step:** model size and weight precision set the floor of per-stream speed.
- **Memory bandwidth:** the hardware term; FLOPs matter only once compute binds at high batch.
- **Concurrency:** trades per-stream for aggregate, as above.
- **Context length:** longer sequences mean more KV bytes per step and fewer sequences that fit, so
  both per-stream and aggregate fall as conversations grow.
- **Output length distribution:** many short answers mean more scheduling churn and a larger prefill
  share; aggregate output rate falls even though the GPU is busy.
- **Tokenizer:** changes the denominator. Comparing models by tokens/s rewards the one with the
  coarser vocabulary; compare characters or words per second for user-facing speed.

### Measuring it without fooling yourself
Count tokens from the server's usage report, not from the number of streamed chunks. Warm up and
measure in steady state, excluding model load and cache fill. Decide between a closed-loop test
(fixed concurrency, each client sends the next request when the previous finishes) and an open-loop
test (requests arrive at a set rate regardless of completion); closed-loop can never show queue
build-up, so capacity numbers from it are optimistic. Use realistic prompt and output length
distributions, report failures and timeouts as part of the run, and publish percentiles of TTFT and
TPOT next to the throughput figure. vLLM ships a serving benchmark script and NVIDIA's GenAI-Perf
exists for the same job; a 30-line asyncio harness is enough to start.

### From tokens/s to money
Cost per million output tokens on self-hosted hardware is
`gpu_hourly_cost / (aggregate_output_tokens_per_second × 3600) × 1e6`. Illustrative: a replica
sustaining 3,000 output tokens/s at an assumed $3 per GPU-hour produces 10.8 million tokens per hour,
about $0.28 per million output tokens, before idle time, redundancy and prefill. Run the same replica
at concurrency 1 for 200 tokens/s and the same GPU-hour yields $4.17 per million. That spread is the
entire economics of batching, and it is why the concurrency you choose is a pricing decision.

## Example
A small closed-loop load generator against any OpenAI-compatible streaming server. It reports
aggregate output tokens/s, median per-stream rate and TTFT percentiles at several concurrency levels,
which is the curve you need before setting a per-replica concurrency limit.

```python
import asyncio, statistics, time
from openai import AsyncOpenAI

client = AsyncOpenAI(base_url="http://localhost:8000/v1", api_key="unused")
MODEL, PROMPT = "my-model", "Explain how a KV cache works in detail."

async def one(max_tokens=256):
    t0 = time.perf_counter(); t_first = None; n = 0
    stream = await client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": PROMPT}], max_tokens=max_tokens,
        stream=True, stream_options={"include_usage": True})
    async for ch in stream:
        if ch.usage:                      # authoritative count, sent in the final chunk
            n = ch.usage.completion_tokens
        if ch.choices and ch.choices[0].delta.content and t_first is None:
            t_first = time.perf_counter()
    return t_first - t0, n, time.perf_counter() - t0

async def level(c: int):
    t0 = time.perf_counter()
    res = await asyncio.gather(*(one() for _ in range(c)))
    wall = time.perf_counter() - t0
    out = sum(n for _, n, _ in res)                                   # total output tokens
    per_stream = [n / (total - ttft) for ttft, n, total in res if n > 1]
    ttfts = sorted(ttft for ttft, _, _ in res)
    print(f"c={c:3d}  aggregate={out / wall:7.0f} tok/s  per-stream p50={statistics.median(per_stream):5.0f} tok/s"
          f"  TTFT p50={ttfts[len(ttfts)//2]*1e3:5.0f} ms  p_max={ttfts[-1]*1e3:5.0f} ms")

async def main():
    await one()                                                       # warm-up
    for c in (1, 4, 16, 64):
        await level(c)
asyncio.run(main())
# Typical shape: aggregate climbs steeply then flattens; per-stream falls gently then faster;
# TTFT max jumps when the engine starts queueing. Pick the largest c that still meets your SLOs.
```

## Trade-offs
- Raising concurrency raises aggregate tokens/s and lowers cost per token, but lowers per-stream
  speed; the right point flips from "maximise aggregate" to "cap concurrency" the moment a per-stream
  or TTFT SLO exists.
- Quoting total (input + output) tokens/s gives a bigger, more flattering number that is fair for
  prefill-heavy jobs like summarisation, and misleading for chat where output dominates the cost.
- Comparing engines on peak throughput is cheap to run but rewards configurations that queue
  aggressively; goodput at your SLO is harder to measure and the only figure that predicts capacity.
- Speculative decoding raises per-stream tokens/s at low concurrency but can lower aggregate at high
  concurrency, so a benchmark at one setting can reverse at the other.
- Long-context workloads cut both numbers because KV reads grow and fewer sequences fit; a model that
  wins at 1k context can lose at 32k.

## Pitfalls
- Comparing providers or models by tokens/s across different tokenizers; the symptom is the "faster"
  model taking longer to finish the same paragraph.
- Benchmarking at concurrency 1 and dividing a fleet by that; the symptom is capacity that is wildly
  under- or over-provisioned depending on which number was used.
- Counting streamed chunks as tokens; the symptom is tokens/s that disagrees with usage and with the
  bill, since servers coalesce or split tokens per event.
- Closed-loop load tests only; the symptom is a capacity estimate that collapses in production when
  arrivals burst and queues form.
- Ignoring prompt tokens in capacity math; the symptom is a RAG workload saturating prefill compute
  while the output tokens/s dashboard says the GPU has headroom.
- Averaging per-request tokens/s instead of dividing total tokens by wall time; the symptom is an
  aggregate figure that short requests inflate and that no capacity model can reproduce.

## When to use
Reach for tokens/s whenever the question is capacity or cost: how many replicas for a launch, what a
GPU-hour yields, whether a quantised model pays for itself, whether a vendor's claim covers your
workload. Always state which of the four numbers you mean and the conditions behind it. Reach for
TPOT and TTFT instead when the question is how one request feels. Day to day it appears in capacity
reviews, vendor comparisons and pricing models; in interviews the strong move is to refuse to answer
"how many tokens/s does it do" until concurrency, lengths, hardware and which tokens are specified,
then to connect the number to cost per million tokens.

## Role lens
- **Engineer:** Builds the load harness, reads usage rather than chunk counts, and produces the
  concurrency-versus-throughput curve for each model and hardware pairing.
- **Architect:** Converts aggregate tokens/s at the SLO into replica counts and cost per token, and
  rejects vendor numbers that omit concurrency and lengths.
- **FDE:** Re-runs the benchmark on the customer's hardware and prompt mix, because their
  tokenizer, context lengths and traffic pattern invalidate the reference numbers.

## Check yourself
**Q:** A vendor claims their endpoint delivers 1,200 tokens per second. What questions do you ask
before deciding whether it will serve your product, and why does each one matter?
**A:** Which tokens: output only, or input plus output, since prefill inflates totals? At what
concurrency: one stream or the whole deployment, and is 1,200 what one user sees or what a GPU
produces? With what prompt and output lengths, because long contexts shrink both numbers and short
outputs raise scheduling overhead? On which hardware and precision, so I can compare to a bandwidth
floor? Closed- or open-loop, and what were TTFT and TPOT percentiles at that rate, since throughput
without an SLO is not capacity? Which tokenizer, since I will compare to my current model by words
or characters, not tokens? Finally, is it sustained or peak, and does it include failures? The answers
turn a slogan into a point on a curve I can place my workload on.

## Quiz
### Q: A replica produces 4,000 output tokens/s in aggregate at a steady concurrency of 64. Roughly what does each user see?
- [ ] About 4,000 tokens/s, since every stream runs at full speed
- [x] About 60 tokens/s, because the aggregate is shared across the concurrent streams
- [ ] About 256,000 tokens/s, the product of the two numbers
- [ ] It cannot be estimated without the TTFT distribution
> In steady state aggregate ≈ concurrency × per-stream rate, so 4,000 / 64 ≈ 62 tokens/s per
> user. TTFT affects when a stream starts, not its decode rate once running.

### Q: Which comparison between two models is NOT apples-to-apples?
- [ ] Aggregate output tokens/s on the same GPU at the same concurrency and lengths
- [x] Output tokens/s when the two models use different tokenizers
- [ ] Characters of output per second on the same prompts
- [ ] Cost per million output tokens at the SLO on the same hardware
> A tokenizer with a coarser vocabulary emits fewer tokens for the same text, so a slower model can
> post a higher tokens/s figure. Characters or words per second, or cost at the SLO, remove that
> distortion.

### Q: Which single change increases a replica's aggregate output tokens/s while lowering each user's per-stream rate?
- [ ] Weight-only int4 quantisation of the model
- [ ] Moving to a GPU with higher memory bandwidth
- [x] Raising the maximum number of concurrent sequences
- [ ] Enabling speculative decoding at batch size 1
> Higher concurrency shares each weight read among more sequences, raising total output while each
> step takes slightly longer. Quantisation and more bandwidth raise both numbers, and speculative
> decoding at batch 1 raises per-stream speed.
