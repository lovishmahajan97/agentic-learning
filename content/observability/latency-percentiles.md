---
id: latency-percentiles
module: observability
name: Latency percentiles (p50/p95/p99)
kind: metric
difficulty: core
est_minutes: 15
status: reviewed
related:
  - observability/monitoring
  - observability/tracing
  - serving-inference/ttft
  - serving-inference/throughput-vs-latency
  - reliability/slas-slos
  - infra-capacity-economics/queueing-theory
  - infra-capacity-economics/latency-budgets
sources:
  - Dean & Barroso, 2013 — The Tail at Scale
  - Official docs: Prometheus (histograms and summaries)
---

## TL;DR
Latency is a distribution, not a number. A handful of slow requests drags the mean up while most users
never see them, and a long tail hides behind a healthy-looking average. A percentile reports the value
below which p% of requests finish: p50 is the typical experience, p95 and p99 are the tail that users
notice and that fan-out amplifies. For LLM systems, measure percentiles per phase (time to first token,
per-token, end to end) and compute them by merging histograms, never by averaging percentiles.

## Why it exists
A request's latency has a hard floor (network round trip plus the minimum compute) and no ceiling: a
retry after a timeout, a garbage-collection pause, a cold start, a queue behind a 20,000-token prefill,
or simply a long answer can take seconds. So the distribution is right-skewed, and the mean is pulled
toward the few slow values. Take 100 requests where 95 finish in 200 ms and 5 take 6 s. The mean is
(95 × 0.2 + 5 × 6) / 100 = 0.49 s, a number that describes nobody: 95 users waited 200 ms and 5 waited
thirty times longer. The same data reads p50 = 200 ms, p95 = 200 ms, p99 = 6 s, which is exactly the
story: typical experience fine, one user in twenty badly served.

The tail matters more than its share of traffic because systems compose. A page, or one step of an agent,
that waits on *N* backend calls is as slow as the slowest of them. If each call independently exceeds its
own p99 with probability 1%, the chance the user waits at least that long is 1 − 0.99^N: about 10% for
10 calls, 40% for 50, 63% for 100. Dean and Barroso called this the tail at scale: at enough fan-out, a
dependency's p99 becomes the user's median. An agent making twenty sequential model and tool calls has the
additive version of the same problem.

The naive alternative, alert when average latency doubles, fails in both directions. It stays silent when
2% of requests get five times slower, because 2% barely moves the mean, and it pages you when a single
60-second outlier lands in a quiet minute. Service-level objectives are percentile statements by
construction ("99% of requests under 2 s"), so you need the machinery to compute them correctly.

## How it works
### Definition
Sort the *n* latencies of a window ascending. The p-th percentile is the value at rank ⌈p/100 × n⌉
(nearest-rank method; interpolating variants differ slightly and only matter for small *n*). p50 is the
median. Each extra nine in the tail needs roughly ten times more samples for the same stability: in a
window with 100 requests, p99 is the second-largest value, so a single retry defines it. With 1,000
requests the top 1% is ten samples, still noisy. This is why p99 over a one-minute window on a low-traffic
endpoint looks like random noise, and why percentiles should be paired with the sample count they came from.

### You cannot keep every sample
At 1,000 requests per second one endpoint produces 86 million latencies a day. Two standard compressions
exist, and both are chosen for the same property: they can be **merged** across hosts and across time.

1. **Fixed-bucket histograms** (Prometheus-style `_bucket` series with `le` boundaries). Each sample
   increments the counter of every bucket whose upper bound is at or above it (the counts are cumulative). A quantile is estimated by
   finding the bucket where the cumulative count crosses p × n and interpolating linearly inside it, so
   the error is the bucket width. Put bucket edges on your SLO thresholds (say 0.5, 1, 2, 5, 10 s),
   because the number you need exactly is "what fraction finished under the threshold", and that is exact
   at an edge. Merging is just summing counts.
2. **Quantile sketches** (t-digest, HDR Histogram, DDSketch, OpenTelemetry's exponential histogram).
   Buckets grow geometrically, giving bounded *relative* error from 1 ms to 10 minutes with a few hundred
   counters, which suits LLM calls whose latencies span four orders of magnitude. Also mergeable.

The tempting third option is to compute the quantile inside each process and export the number (a
Prometheus *summary*, or a `p99` gauge from an in-process library). It is precise per process and useless
for the fleet, because the raw distribution is gone by the time it reaches the backend: you cannot combine
40 per-process p99s into one fleet p99, for the reason in the next subsection. Use summaries only for a
single process you will never aggregate.

### Why you merge first and quantile last
Percentiles do not compose. The fleet p99 is not the mean of per-host p99s, nor a traffic-weighted mean of
them, and the daily p99 is not the mean of per-minute p99s. A canary host handling 1% of traffic with a
terrible p99 would dominate an "average p99" while barely moving the real fleet tail; a loaded host's tail
can be diluted the same way. The correct query sums the histograms first and takes the quantile once, which
in PromQL reads `histogram_quantile(0.99, sum by (le) (rate(latency_seconds_bucket[5m])))`.

### Windows
A percentile is always over a window. Short windows (1–5 min) react to incidents quickly but flap at low
traffic; long windows (1 h, 1 day) are stable but a ten-minute outage inside a day barely registers. For
alerting, "fraction of requests under the threshold" burning an error budget is more robust than alerting
on the p99 value itself, because it is a ratio with a known sample size.

### What makes the tail
Queueing is the usual culprit: in the simplest queue model the expected wait grows like ρ/(1−ρ) with
utilisation ρ, so the tail explodes as a system approaches saturation while the mean still looks fine.
Add retries (a retry after a 5-second timeout adds 5 seconds to that request), stop-the-world garbage
collection, cold starts, lock contention and cache misses that only some requests pay.

LLM serving adds its own sources. Time to first token (TTFT) tails come from prefill of unusually long
prompts and from waiting for a slot in a continuous-batching scheduler; per-token latency rises with batch
size and context length; provider rate limits turn into 429s plus backoff. Most importantly, end-to-end
latency is roughly TTFT + output_tokens × seconds_per_token, so its "tail" is often just long answers.
Measure three distributions, TTFT, inter-token latency (or tokens/s) and total, and when total regresses,
look at the output-length distribution before blaming infrastructure.

### Cutting the tail
Once you know where the tail comes from, the standard remedies follow from the arithmetic. **Hedged
requests** send a duplicate to another replica only if the first has not answered by about the p95 delay,
so at most roughly 5% of requests are duplicated while the slowest ones get a second chance; firing the
duplicate immediately would double load to fix 1% of requests. **Timeouts with one retry** do the same for
idempotent calls, but the retry adds the full timeout to that request, so set the timeout from the
measured distribution, not a round number. **Admission control** (rejecting or queueing work above a
utilisation target) attacks the ρ/(1−ρ) term directly. For LLM calls, capping `max_tokens` and streaming
change what users perceive more than any infrastructure fix, because they shorten or hide the decode term.
None of these is free near saturation: every duplicate is extra load arriving exactly when queues are long.

### Where you measure
Server-side timers miss load-balancer queueing, TLS handshakes, network and client retries. Measure at the
edge or client for the user-facing SLO and keep server-side percentiles for diagnosis. Record failures and
timeouts with their elapsed time too: if only successes are timed, the p99 *improves* during an incident
as the slowest requests become timeouts and vanish from the sample. Load generators that wait for a slow
response before sending the next request under-sample the bad period in the same way (coordinated omission).

## Example
A simulation of two hosts behind one load balancer. Host A is busy and mostly healthy; host B is a tiny
canary with a bad slow mode. The script prints the summary statistics, then shows the two mistakes this
lesson is about: averaging per-host p99s instead of pooling, and underestimating how often a fan-out
request meets a per-call p99. The numbers below are illustrative of the shape, not a benchmark.

```python
import numpy as np
rng = np.random.default_rng(0)

def service(n, slow_frac, slow_ms):
    """Mostly fast (log-normal around 300 ms) with a slow mode: retries, cold starts, long prompts."""
    fast = rng.lognormal(mean=np.log(300), sigma=0.3, size=n)
    slow = rng.random(n) < slow_frac
    return np.where(slow, slow_ms + rng.exponential(1000, size=n), fast)

def summary(x):
    p50, p95, p99 = np.percentile(x, [50, 95, 99])
    return f"n={len(x):>6} mean={x.mean():6.0f} p50={p50:5.0f} p95={p95:5.0f} p99={p99:6.0f} ms"

a = service(100_000, slow_frac=0.005, slow_ms=4000)   # busy host, 0.5% slow
b = service(1_000,   slow_frac=0.20,  slow_ms=4000)   # tiny canary, 20% slow
print("A", summary(a))      # expect p99 around 0.65 s: the slow mode sits below the 1% mark
print("B", summary(b))      # expect p99 around 7 s

# WRONG: average the per-host p99s.  RIGHT: pool the samples (or sum histogram buckets) first.
print("avg of p99s:", (np.percentile(a, 99) + np.percentile(b, 99)) / 2)   # roughly 3.7 s
print("pooled p99 :", np.percentile(np.concatenate([a, b]), 99))          # roughly 0.7 s

# Fan-out: a request that waits on k such calls meets a per-call p99 far more often than 1% of the time.
for k in (1, 10, 50, 100):
    print(f"k={k:3}: P(at least one call >= its p99) = {1 - 0.99**k:.1%}")
```

Note that the mean of host A (about 340 ms) sits between its p50 and p95 and says nothing about the 0.5%
of users who waited over four seconds; the p99 misses them too, because they are fewer than 1%. That is
the argument for also tracking p99.9, or the count of requests over a hard threshold, on user-facing paths.

## Trade-offs
- A higher percentile describes the worst experience more faithfully but needs about ten times more
  samples per extra nine; on a per-tenant endpoint doing 50 requests a minute, a one-minute p99 is the
  maximum, so fall back to p90 or a longer window.
- Fixed-bucket histograms are cheap, mergeable and exact at the bucket edges, but imprecise between them;
  when you need p99.9 across a millisecond-to-minutes range, switch to exponential histograms or t-digest.
- Tail-cutting techniques (hedged requests, retries, duplicate speculative calls) buy tail latency almost
  free when utilisation is low and make the tail worse near saturation, because they add load exactly when
  queueing is the cause.
- End-to-end percentiles are what users feel, but for LLM calls they conflate answer length with speed;
  TTFT and per-token percentiles are diagnosable at the cost of three charts instead of one.
- Short windows catch incidents in minutes but flap; long windows are stable but hide short outages, so
  dashboards usually need both.

## Pitfalls
- Averaging percentiles across hosts, shards or time buckets; the symptom is a dashboard p99 that moves
  when you add an idle instance and never matches what a user with a stopwatch sees.
- Alerting on mean latency; the symptom is a 5× regression on 2% of traffic that nobody notices until
  support tickets arrive, while one 60-second outlier pages the on-call at 3 a.m.
- Timing only successful requests; the symptom is a p99 that improves during an incident because the
  slowest requests turned into timeouts and dropped out of the sample.
- Mixing models, endpoints, tenants or prompt sizes in one percentile; the symptom is a shelf between p97
  and p99 that turns out to be the 3% of traffic routed to a slower model.
- Treating an end-to-end regression as an infrastructure problem; the symptom is unchanged TTFT and
  tokens/s while the median output length doubled after a prompt edit.
- Measuring only server-side; the symptom is a server p99 of 300 ms against customer reports of 4 s, the
  gap being load-balancer queueing, SDK retries or provider-side queueing nobody instrumented.

## When to use
Use percentiles for every latency you report, and pick the percentile by audience: p50 for capacity and
cost conversations, p95/p99 for user-facing SLOs, p99.9 or a hard-threshold count for paths where a single
bad experience is expensive. Instrument LLM calls as three histograms (TTFT, inter-token, total) labelled by
model, endpoint and tenant, with bucket edges at your SLO thresholds, and compute every fleet-level number by
summing histograms. Do not bother with p99 on anything that sees fewer than a few hundred requests per
window; report p90 and the count instead. Day to day this is the first chart you open in an incident and
the number in every capacity review. In interviews, expect to be asked why the mean is misleading, how to
aggregate percentiles across hosts, and how fan-out turns a dependency's p99 into your p50.

## Role lens
- **Engineer:** Emits latency histograms with sensible buckets from the LLM gateway, splits TTFT from
  decode, and checks output-length percentiles before declaring a latency regression.
- **Architect:** Sets the percentile SLO per path, allocates a latency budget across retrieval, model and
  tools, and decides where hedging or fallbacks are allowed given fan-out depth.
- **FDE:** Translates a customer's "it feels slow sometimes" into a measured p95 by tenant and phase, and
  uses the fan-out arithmetic to explain why their 15-call agent needs a faster per-call target.

## Check yourself
**Q:** After a release, a chat assistant's end-to-end p99 rose from 9 s to 16 s. Mean latency rose only 10%,
GPU utilisation is unchanged, and no infrastructure changed. How do you find the cause, and what do you
report?
**A:** First decompose the metric. Compare the output-token length distribution before and after: a prompt
edit that makes a minority of answers much longer raises the tail of total latency with no change in speed,
which fits a small move in the mean and unchanged GPU load. Then split TTFT from inter-token latency; if per-token speed and
TTFT are flat, it is length. Next slice by model, endpoint and tenant: a new feature sending 3% of traffic
to a slower model or a longer system prompt appears only above p97. Check retry and timeout settings in the
release, since a new retry adds a full timeout to the tail, and check the gateway queue-time span. Finally
confirm the measurement itself did not change, for example streaming end-time now including a post-processing
step. Report which segment and which phase moved, the fraction of requests affected, and the fix, not just
"p99 is up".

## Quiz
### Q: A service reports mean latency 480 ms, p50 200 ms and p99 6 s. Which conclusion is best supported?
- [ ] Most requests take about half a second
- [x] Half of requests finish within 200 ms and roughly 1% take 6 s or more, so a slow minority dominates the mean
- [ ] The service is at capacity, because p99 exceeds 10× p50
- [ ] The p95 must be about 3 s, halfway between p50 and p99
> The mean is pulled up by the tail; p50 says the typical request is fast. Nothing here fixes p95 (it could
> still be 200 ms), and a wide p50–p99 gap has many causes besides saturation, such as retries or long outputs.

### Q: You have the per-host p99 for 40 hosts and need the fleet p99 for an SLO report. What is correct?
- [ ] Average the 40 values
- [ ] Weight each host's p99 by its request count and average
- [x] Merge the underlying histograms or samples, then compute the quantile once
- [ ] Take the maximum of the 40 values
> Quantiles do not compose, so neither a plain nor a traffic-weighted average of percentiles is the fleet
> percentile; the maximum is a different statistic. Sum the bucket counts across hosts first, then take p99.

### Q: A page fans out to 30 backend calls in parallel and waits for all of them. If each call independently exceeds its own p99 with probability 1%, roughly how often does the page wait at least that long?
- [ ] About 1%
- [ ] About 3%
- [x] About 26%
- [ ] About 99%
> The page is slow if any call is slow: 1 − 0.99^30 ≈ 0.26. This is the tail-at-scale effect; the
> per-dependency p99 becomes a common user experience once fan-out is wide enough.
