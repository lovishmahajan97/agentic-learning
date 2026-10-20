---
id: ttft
module: serving-inference
name: TTFT (time to first token)
kind: metric
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - serving-inference/prefill-vs-decode
  - serving-inference/streaming
  - serving-inference/tpot
  - serving-inference/prefix-caching
  - serving-inference/continuous-batching
  - serving-inference/batching-and-concurrency-limits
  - observability/latency-percentiles
  - infra-capacity-economics/latency-budgets
sources:
  - Pope et al., 2022 — Efficiently Scaling Transformer Inference
  - Agrawal et al., 2024 — Taming Throughput-Latency Tradeoff in LLM Inference with Sarathi-Serve
  - Zhong et al., 2024 — DistServe: Disaggregating Prefill and Decoding for Goodput-optimized Large Language Model Serving
---

## TL;DR
Time to first token (TTFT) is the delay between sending a request and receiving the first generated
token. It is made of queueing wait plus prefill, the single compute-bound pass over the whole prompt,
so it grows with prompt length and with load, not with answer length. In a streaming UI it is the
latency users actually feel. You cut it with shorter prompts, prefix caching, admission control and
scheduling; faster decode tricks such as speculative decoding do nothing for it.

## Why it exists
"Request latency" is a useless number for an LLM call because it mixes two phases with different
physics. Prefill processes every prompt token in one batched forward pass; it is dominated by large
matrix multiplications and scales with prompt length. Decode then produces tokens one at a time,
each step bound by memory bandwidth and scaling with output length. If you only track end-to-end
latency, a regression could be either phase and you cannot tell which lever to pull.

TTFT isolates the first phase. It matters on its own because once a stream has started the user has
something to read and tolerates the rest; before it starts they see nothing and assume the product is
broken. The same request can have a perfectly acceptable total time and an unacceptable TTFT if the
first second is spent queueing or chewing through a 20k-token retrieved context.

The naive alternative, "just watch mean latency", fails twice. Means hide the queueing tail, which is
where TTFT breaks first under load. And a team that measured TTFT with 200-token test prompts will
ship a RAG feature that stuffs 8k tokens into the context and never understand why the product
suddenly feels slow: the answer is in the prefill arithmetic, which this metric forces you to look at.

## How it works
### What is inside the number
From the client's clock, TTFT is the sum of: network to the server; time spent waiting for
admission (a free slot in the batch and free KV-cache memory); tokenisation; the prefill forward
pass over all prompt tokens; sampling the first token from the logits of the last prompt position
(the first token falls out of the prefill pass itself, there is no separate decode step for it);
serialisation and the first flush of the response; and network back. Only two of those terms are
large and variable: the queue and prefill.

### Prefill cost
For a dense transformer with *N* parameters, the forward pass costs roughly `2 × N` FLOPs per token
for the weight matrices, plus an attention term that grows with the square of the prompt length but
is small until contexts get very long. So prefill FLOPs ≈ `2 × N × n_prompt`. Illustrative
arithmetic: an 8-billion-parameter model on a 4,000-token prompt needs about 2 × 8e9 × 4e3 = 6.4e13
FLOPs. An H100-class GPU has a dense bf16 peak around 1e15 FLOP/s; at a realistic 40–50 % utilisation
that is roughly 130–160 ms of pure prefill. Double the prompt and you double it. A 70B model is
nearly nine times more. This is why TTFT tracks prompt length almost linearly in practice and why
"we added RAG and TTFT went from 300 ms to 2 s" is expected, not a bug.

### Queueing under continuous batching
A continuous-batching engine admits a new request only when it has a scheduling slot and enough KV
memory for its prompt. When either is exhausted, the request waits for others to finish or be
preempted, and that wait lands entirely in TTFT. So TTFT p99 is a capacity signal: if it climbs
while prompt lengths have not changed, you are queueing. Prefill also interacts with running
requests: a long prompt entering the batch makes that step expensive, which stalls every other
stream's next token. Chunked prefill splits a long prompt across several steps to protect those
streams, at the cost of slightly longer TTFT for the long request itself. Disaggregated serving goes
further and runs prefill on separate GPUs so decode latency and TTFT stop competing at all, paying
with a KV-cache transfer between machines.

### Levers, in order of leverage
1. **Send fewer tokens.** Tighter retrieval (fewer, shorter chunks), summarised history, and no
   boilerplate. Linear payoff.
2. **Prefix caching.** If requests share a byte-identical prefix (system prompt, tool schemas, a
   document in a multi-turn chat), the engine reuses its KV cache and only prefills the suffix.
   Dynamic content at the top of the prompt (timestamps, user names) breaks the match.
3. **Admission control and capacity.** Bound concurrency per replica so requests fail fast or queue
   visibly instead of silently inflating TTFT; scale on queue depth, not GPU utilisation.
4. **Scheduling.** Chunked prefill, priority classes for interactive traffic, separate pools for
   long-context jobs.
5. **Hardware and parallelism.** Prefill is compute-bound, so a faster GPU or tensor parallelism
   across GPUs does shorten it, unlike decode. Speculative decoding, KV-cache quantisation and
   weight-only quantisation mostly do not; they target the decode phase.

### Measuring it honestly
Measure at the client from "request sent" to the first *non-empty content* delta; the first SSE
frame is often a role-only chunk or a `message_start` event and arrives before any prefill has
finished. Report percentiles bucketed by prompt length, and keep a separate "time to first visible
token" when a model emits hidden reasoning before its answer, because that gap can be many seconds
while TTFT looks fine. Server-side TTFT, measured from arrival, strips out network and is the number
to compare across engine configurations.

## Example
A short probe against any OpenAI-compatible streaming server (a provider, or vLLM/SGLang locally)
that measures TTFT to the first content delta and TPOT over the rest, then shows how TTFT grows with
prompt length while TPOT stays flat.

```python
import time
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")  # any OpenAI-compatible server
MODEL = "my-model"

def measure(prompt: str, max_tokens: int = 200):
    t0 = time.perf_counter(); t_first = t_last = None; chunks = 0; usage_tokens = None
    stream = client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens, stream=True, stream_options={"include_usage": True})
    for chunk in stream:
        if chunk.usage:                                      # final usage-only chunk, if the server sends one
            usage_tokens = chunk.usage.completion_tokens
        if chunk.choices and chunk.choices[0].delta.content:  # skip the empty role-only first chunk
            now = time.perf_counter()
            t_first = t_first or now
            t_last = now; chunks += 1
    n = usage_tokens or chunks                               # chunks ~ tokens only as a fallback
    ttft = t_first - t0
    tpot = (t_last - t_first) / max(n - 1, 1)
    return ttft, tpot, n

for words in (50, 500, 5000):
    ttft, tpot, n = measure("Summarise this text: " + "word " * words)
    print(f"prompt~{words:5d} words  TTFT={ttft*1000:6.0f} ms  TPOT={tpot*1000:5.1f} ms  ({n} out tokens)")
# Expect TTFT to rise roughly linearly with prompt length (prefill) and TPOT to stay nearly constant.
# Run it again with 20 concurrent copies: TTFT climbs (queueing) long before TPOT does.
```

## Trade-offs
- Prefix caching slashes TTFT for repeated prefixes but only when they are byte-identical and still
  resident; it buys nothing for one-off prompts or when dynamic fields sit before the shared part.
- Chunked prefill protects the inter-token latency of running streams but lengthens TTFT for the
  long prompt being chunked; the balance flips for prefill-heavy workloads with few concurrent
  decodes, such as summarisation batches, where you would rather prefill at full speed.
- Prioritising short prompts improves median TTFT at the risk of starving long ones; it only works
  with ageing or a separate pool for long-context requests.
- Trimming retrieved context lowers TTFT linearly but can cut answer quality, so the cut must be
  checked against an eval set rather than assumed harmless.
- Disaggregating prefill and decode isolates TTFT from decode load, but adds machines and a
  KV-cache transfer whose cost is only worth it at high, steady traffic.

## Pitfalls
- Measuring to the first SSE frame rather than the first content token; the symptom is a TTFT that
  looks great and does not move when prompt length triples.
- Reporting mean TTFT; the symptom is a green dashboard while users complain, because queueing shows
  up only in p95 and p99.
- Blaming the model for high TTFT when it is queueing; the tell is that TTFT correlates with
  concurrency, not with prompt length, and drops at night.
- Load-testing with short prompts and shipping long retrieved contexts; the symptom is a product that
  "got slow when we added RAG" with no change in the serving stack.
- Ignoring hidden reasoning tokens; TTFT is fine but the user sees a blank screen for ten seconds, so
  measure time to first visible token separately.
- Counting cold starts (model load on a fresh replica, serverless GPU wake-up) inside TTFT; the
  symptom is a p99 dominated by a handful of 30-second outliers after each scale-out.

## When to use
Give every user-facing surface a TTFT target before launch: a chat UI usually aims for well under a
second at p95, inline completion needs a few hundred milliseconds, and a nightly batch job has no
TTFT target at all. Budget it as network + queue + prefill and check which term you can actually
control. Watch it bucketed by prompt length and by concurrency so you can tell a prompt-size
regression from a capacity problem. In day-to-day work it is the first number to look at when
"the assistant feels slow", and in interviews the expected answer to "latency doubled after we added
retrieval" is a decomposition of TTFT into prefill arithmetic plus the levers above.

## Role lens
- **Engineer:** Instruments TTFT to the first content delta, enables prefix caching for the shared
  system prompt, and trims retrieved context when the prefill term dominates.
- **Architect:** Sets per-surface TTFT SLOs, decides concurrency limits and admission policy, and
  chooses whether long-context traffic gets its own pool or disaggregated prefill.
- **FDE:** Explains to a customer why their 200-page-document prompt costs seconds before the first
  word, and offers retrieval or chunking as the fix rather than a bigger GPU alone.

## Check yourself
**Q:** Your service's p50 TTFT is 400 ms but p99 is 9 s. The model, prompts and hardware have not
changed in a month. What is happening, how do you confirm it, and what do you change?
**A:** A gap that large with stable prompts is queueing: requests waiting for a batch slot or KV
memory, which lands entirely in TTFT. Confirm by plotting TTFT against concurrent requests and
engine queue depth; the p99 should rise with load and collapse off-peak, and the engine's
preemption or waiting-queue metrics should spike at the same times. Prompt-length buckets rule out
a few long prompts as the cause. Fixes, cheapest first: cap per-replica concurrency and return a fast
429 or visible queue position instead of a silent 9 s wait; enable prefix caching if prompts share a
system prefix; turn on chunked prefill so long prompts stop stalling admission; autoscale on queue
depth rather than GPU utilisation; and move long-context or batch traffic to a separate pool.

## Quiz
### Q: Which change most directly reduces TTFT for requests with a 10k-token prompt?
- [ ] Enabling speculative decoding with a small draft model
- [ ] Quantising the KV cache to 8-bit
- [x] Caching the KV state of the shared prompt prefix so only the suffix is prefilled
- [ ] Raising `max_tokens` so the engine schedules the request sooner
> TTFT is dominated by prefill over the prompt; prefix caching removes most of that work when the
> prefix repeats. Speculative decoding and KV quantisation target the decode phase, and `max_tokens`
> affects output length, not the time to the first token.

### Q: Holding hardware and model fixed, TTFT scales primarily with which two quantities?
- [ ] Output length and sampling temperature
- [x] Prompt length and time spent waiting for admission to the batch
- [ ] Number of KV heads and vocabulary size
- [ ] Batch size of the decode phase and the stop sequence
> Prefill cost is roughly linear in prompt tokens, and any wait for a scheduling slot or KV memory is
> added on top. Output length only affects total time after the first token, and temperature changes
> which token is sampled, not how long it takes.

### Q: Which measurement gives a correct client-side TTFT for a streamed chat completion?
- [ ] Time until the HTTP response headers arrive
- [ ] Time until the first SSE frame of any kind is received
- [x] Time until the first delta containing non-empty content is received
- [ ] Time until the final usage chunk divided by the number of tokens
> Headers and the first frame (often a role-only chunk or `message_start`) are sent before prefill
> completes, so they under-report. The first content delta is the first evidence that generation
> has produced a token.
