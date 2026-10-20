---
id: capacity-planning
module: infra-capacity-economics
name: Capacity planning (tokens/sec, requests/sec, concurrent sessions)
kind: practice
difficulty: core
est_minutes: 18
related:
  - infra-capacity-economics/queueing-theory
  - infra-capacity-economics/latency-budgets
  - infra-capacity-economics/gpu-sizing
  - infra-capacity-economics/inference-clusters
  - infra-capacity-economics/api-provider-quotas-and-regional-limits
  - serving-inference/kv-cache-memory-estimation
  - serving-inference/batching-and-concurrency-limits
  - serving-inference/throughput-vs-latency
sources:
  - Little, 1961 — A Proof for the Queuing Formula L = λW
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Official docs: vLLM
---

## TL;DR
LLM capacity cannot be planned in requests per second alone, because one request can cost a hundred
times more than another. You plan in three linked units: requests/sec (arrivals), tokens/sec split into
input and output (work), and concurrent sequences or sessions (what occupies GPU memory and quota).
Little's Law connects them, a load test at your latency SLO gives per-replica capacity, and replicas =
peak demand ÷ capacity, plus headroom.

## Why it exists
A classic web service has fairly uniform requests: if one pod handles 500 RPS at p99 under 100 ms, ten
pods handle about 5,000. Teams carry that habit into LLM serving and it breaks in three ways.

First, cost per request is wildly uneven. A 200-token classification and a 30,000-token document
summary are both "one request", yet the second does a hundred times the prefill work and holds GPU
memory for far longer. An RPS figure says nothing about which mix you will get.

Second, a request occupies a GPU for seconds, not milliseconds. Generation produces output tokens one at
a time, so a 500-token answer at 30 ms per token holds a batch slot and its KV cache for 15 seconds.
What saturates a replica is the number of sequences *in flight at once*, and that is bounded by KV-cache
memory and by the per-token latency you promised, not by arrivals per second.

Third, the units that limit you come from different places. A self-hosted engine runs out of KV memory
or decode bandwidth; a managed API throttles you on requests per minute and tokens per minute; your
websocket tier and session store care about open conversations. A plan written in only one unit
misses the constraint that actually binds. The naive fixes, averaging tokens/sec over a day or adding
GPUs until the alerts stop, either under-provision the peak or pay for idle hardware without knowing
which.

## How it works
### 1. Describe the workload as distributions, not averages
Capacity starts from traffic, not hardware. Collect, per use case: peak concurrent active users, how
often each sends a message, how many model calls one message triggers (an agent may make several),
input tokens per call (system prompt + history + retrieved context) and output tokens per call. Record
p50 and p95 for the token counts, and the peak-to-average ratio of traffic over a day and a week. You
need the tails because a long-context minority can consume most of the KV memory while being a small
share of requests.

### 2. Convert between the three units
- **Requests/sec (λ):** active users × messages per user per second × calls per message.
- **Tokens/sec:** input TPS = λ × mean input tokens; output TPS = λ × mean output tokens. Keep them
  separate *because* they hit different resources: input is prefill, compute-bound and parallel; output
  is decode, bandwidth-bound and sequential per request. Planning on "total tokens" hides which one binds.
- **Concurrent in-flight sequences (L):** by Little's Law, L = λ × W, where W is mean time in the system.
  For one call, W ≈ queue wait + TTFT + output tokens × TPOT (time per output token). This is the number
  that must fit into batch slots and KV memory.
- **Concurrent sessions:** open conversations, most of them idle between turns. Sessions × duty cycle
  (fraction of wall time a session has a call running) ≈ in-flight sequences. Sessions size the
  connection tier, session store and prefix-cache retention; in-flight sequences size the GPUs.

Little's Law holds for any stable system in steady state, whatever the arrival or service
distribution, which is *why* it is the safe bridge between units. It does not tell you queueing delay;
that is the job of the queueing-theory lesson.

### 3. Measure capacity per replica at the SLO
Per-replica capacity is not a spec-sheet number. Run a load test with a realistic token mix and sweep
concurrency. Throughput rises with batch size while per-request TPOT and TTFT degrade; the usable
capacity is the highest concurrency (and its tokens/sec) where p95 TTFT and p95 TPOT still meet the
latency budget, sometimes called goodput. Cross-check it against KV memory: max sequences ≈ free
memory after weights ÷ (KV bytes per token × mean context length). Whichever is lower, the memory bound
or the latency knee, is the replica's capacity, and you should know which one it is because the fixes
differ (more memory or GQA/quantized cache vs. more replicas).

### 4. Size, then add headroom
Replicas = peak in-flight sequences ÷ per-replica capacity, rounded up, then divided by a target
utilization below 100% *because* queueing delay grows sharply as utilization approaches one, and
increased again for N+1 failure tolerance, rolling deploys and autoscaler lag (GPU nodes and model
loading can take minutes). For a managed API, the same numbers become requests-per-minute and
tokens-per-minute quotas per region and per model; check whether your provider counts the requested
`max_tokens` against the token quota at admission time, because some do, which makes generous
`max_tokens` settings consume quota you never use.

## Example
```python
import math

def plan(active_users, msgs_per_user_min, calls_per_msg, in_tok, out_tok,
         ttft_s, tpot_s, replica_max_seqs, target_util=0.7, spare=1):
    rps = active_users * msgs_per_user_min / 60 * calls_per_msg     # λ
    in_tps, out_tps = rps * in_tok, rps * out_tok
    w = ttft_s + out_tok * tpot_s                                   # seconds per call
    in_flight = rps * w                                             # Little's Law L = λW
    duty = in_flight / active_users                                 # share of sessions busy
    replicas = math.ceil(in_flight / (replica_max_seqs * target_util)) + spare
    return dict(rps=rps, in_tps=in_tps, out_tps=out_tps, w=w,
                in_flight=in_flight, duty=duty, replicas=replicas)

# Support agent at peak: 1,200 open sessions, one message per user per minute,
# 3 model calls per message, 3,000 input / 250 output tokens per call.
# Load test (illustrative): one replica holds 64 sequences at p95 TPOT <= 30 ms.
print(plan(1200, 1, 3, 3000, 250, ttft_s=0.5, tpot_s=0.03, replica_max_seqs=64))
# {'rps': 60.0, 'in_tps': 180000.0, 'out_tps': 15000.0, 'w': 8.0,
#  'in_flight': 480.0, 'duty': 0.4, 'replicas': 12}
```
Reading the result: 60 calls/sec, each lasting about 8 s (0.5 s TTFT + 250 × 30 ms), means 480
sequences in flight, so only 40% of the 1,200 sessions are busy at any instant. At 64 sequences per
replica, 7.5 replicas would run at 100%; at a 70% target that is 11, plus one spare gives 12.
Now cross-check memory: for a Llama-3-8B-shaped model (32 layers, 8 KV heads, head dim 128, fp16) the
cache is 2 × 32 × 8 × 128 × 2 bytes = 128 KiB per token, so 64 sequences × 3,250 tokens ≈ 27 GB, which
fits beside ~16 GB of weights on an 80 GB GPU. Then check prefill: 180,000 input tokens/sec across 12
replicas is 15,000 per replica, which the load test must confirm; if the 2,000-token system prompt is
shared, prefix caching removes most of that work.

## Trade-offs
- A higher target utilization cuts GPU cost, but queueing delay rises steeply near saturation; it flips
  toward low utilization when traffic is bursty or the TTFT budget is tight.
- Planning to p95 token counts instead of means over-provisions on quiet days but protects the tail;
  means are acceptable only when context length is tightly capped.
- Larger per-replica batches raise tokens/sec per GPU but lengthen TPOT for every user; interactive chat
  favours smaller batches, offline batch jobs favour the largest batch memory allows.
- Static provisioning for peak is simple and fast to respond, but wastes money when peak-to-average is
  high; autoscaling saves cost only if scale-up time is shorter than the ramp of your traffic.

## Pitfalls
- Planning in RPS only; the symptom is a capacity plan that looks fine until one tenant starts sending
  long documents and KV-cache preemptions spike while RPS is unchanged.
- Using the engine's peak tokens/sec from a benchmark at huge batch; the symptom is that real
  interactive traffic misses its TPOT SLO at a fraction of that throughput.
- Forgetting agent fan-out, counting user messages instead of model calls; the symptom is provider
  429s on requests-per-minute at a user load the plan said was safe.
- Sizing to the daily average; the symptom is queue depth and TTFT climbing every weekday morning while
  the dashboard shows modest mean utilization.
- Treating open sessions as concurrent load; the symptom is a GPU fleet several times larger than needed
  because idle conversations were counted as in-flight sequences.

## When to use
Do this before any launch, before signing a reserved-capacity or provisioned-throughput commitment, and
whenever the traffic mix changes: a new feature with longer context, an agent that adds tool-calling
steps, a large tenant onboarding. Keep the model as a living spreadsheet or script fed by production
telemetry: arrivals, input and output token histograms, TTFT and TPOT percentiles, in-flight sequences,
KV-cache utilization and provider throttle counts. If you consume only managed APIs, the plan is shorter
but still needed, because quotas are per region and per model and raising them can take days. In
interviews, expect "how many GPUs for N users" questions; the strong move is to ask for the token
distribution and SLO first, then reason through Little's Law and a measured per-replica capacity.

## Role lens
- **Engineer:** Instruments token counts, TTFT, TPOT and in-flight sequences per route, and runs the
  load test that finds the replica's knee at the latency SLO.
- **Architect:** Owns the capacity model, headroom policy and the choice between reserved capacity,
  autoscaling and managed API quotas, including regional failover capacity.
- **FDE:** Turns a customer's "5,000 users" into calls/sec and token mix from their real documents, and
  files quota increase requests in their cloud account before go-live, not after.

## Check yourself
**Q:** Traffic stays at 40 RPS, but after a release p95 TTFT doubles and the engine reports frequent
preemptions. Nothing changed in hardware. What happened, and how do you confirm and fix it?
**A:** RPS is unchanged, so look at the work per request. Likely the release raised input or output
tokens per call: more retrieved chunks, longer history, or a higher `max_tokens`. Longer outputs raise W,
so in-flight sequences L = λW rise even at constant λ; longer contexts raise KV bytes per sequence. Both
push the replica past its memory bound, causing preemption and queueing. Confirm by comparing input and
output token histograms and in-flight sequence counts before and after. Fix by trimming context, capping
output, enabling prefix caching for shared prompts, or adding replicas sized from the new token mix.

## Quiz
### Q: A service receives 20 calls/sec and each call takes 6 s end to end. How many sequences are in flight on average?
- [ ] 20, because concurrency equals arrival rate
- [ ] 3.3, because 20 divided by 6
- [x] 120, by Little's Law L = λW
- [ ] It cannot be estimated without the arrival distribution
> Little's Law gives L = λ × W = 20 × 6 = 120 for any stable system in steady state, whatever the
> distribution. The distribution matters for queueing delay and tails, not for the mean in-flight count.

### Q: Why should a capacity plan keep input and output tokens/sec separate instead of summing them?
- [ ] Providers bill them at the same rate, so splitting adds nothing
- [x] Input tokens drive compute-bound prefill while output tokens drive bandwidth-bound, sequential decode that holds GPU memory longer
- [ ] Output tokens are always fewer, so they can be ignored
- [ ] Input tokens are cached and therefore free after the first request
> The two phases hit different resources. A summary workload is prefill-heavy, a long-generation workload
> is decode-heavy and occupies batch slots longer; summing them hides which constraint binds. Only shared
> prefixes, not all input, benefit from caching.

### Q: A chat product has 10,000 open sessions, but each session has a model call running 5% of the time. What should size the GPU fleet?
- [ ] 10,000 concurrent sequences, since every session might send at once
- [x] About 500 in-flight sequences at peak, plus headroom for bursts and failures
- [ ] The number of sessions divided by the number of GPUs
- [ ] Requests per second alone, since sessions are irrelevant to GPUs
> GPUs are occupied by in-flight sequences, which here are sessions × duty cycle ≈ 500. Open sessions
> still size connections and session storage, and the plan adds headroom because peaks exceed averages.
