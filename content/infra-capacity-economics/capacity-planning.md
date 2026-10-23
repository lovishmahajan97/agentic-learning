---
id: capacity-planning
module: infra-capacity-economics
name: Capacity planning (tokens/sec, requests/sec, concurrent sessions)
kind: practice
difficulty: core
est_minutes: 18
status: typed-verified
lesson_type: decision
agent_relevance: useful
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
An agent run is many model calls with growing prompts and tool waits between them, so plan in linked
units: runs and calls per second, input and output tokens/sec, in-flight sequences. Little's Law links
them; a load test sets replica capacity.

## For agent builders
- **Why it matters for agents.** "1,200 users" says almost nothing: what loads the fleet is runs per
  second × steps per run, prompts that grow with every tool result, and runs that sit idle in tools while
  their cache may stay resident. Agents are where the RPS habit fails worst.
- **Where it shows up in your stack.** Your traces (steps per run, tokens per step, tool time per step),
  the engine's running and waiting counts (vLLM publishes both), provider TPM and RPM quotas, and the
  autoscaler or reservation you size from them.
- **What you control.** Step budgets, context compaction, `max_tokens` per step type, fan-out limits,
  prefix caching, and whether long agent runs share a pool with interactive chat.
- **What you can skip.** Queueing-theory derivations. Little's Law, the latency knee and the headroom rule
  are enough to size an agent fleet.

## Decision case
### Scenario
A team self-hosts the research agent from the multi-step example below and must decide what to order.
All figures are illustrative and reuse that example.
- **Peak traffic:** 2 runs/sec, 10 steps per run, a 4,000-token prefix growing by 1,000 per step, 200
  output tokens and 2 s of tool time per step; a model call stays in flight ~6.8 s at peak. So 20
  calls/sec, 20 × 6.8 = 136 sequences in flight, and 2 × 10 × (6.8 + 2) = 176 live runs (L = λW).
- **Replica capacity from a load test of recorded runs:** 49 sequences (KV-bound) and ~25,000 prefill
  tokens/sec within the TTFT budget; plans use 70% target utilization and one spare.
- **Today:** 5 replicas, sized from in-flight calls; p95 TTFT breaches at peak.
- **Constraints:** p95 TTFT under 2 s; traffic grows 10% a month and reserved GPUs take a quarter to
  arrive (growth ≈ 1.33×); the budget allows at most 10 replicas.

### Options
| Option | How it works | Binding unit | Replicas today | Replicas at lead time | Budget |
|---|---|---|---|---|---|
| A. Keep 5, sized on calls | 136 ÷ (49 × 0.7) rounded up, plus 1; waiting runs' prefixes are evicted, so steps re-prefill their transcript | Prefill: 170,000 ÷ 5 = 34,000 tok/s per replica, above the 25,000 tested | 5, already breaching | n/a | Within, but fails the SLO |
| B. Evict prefixes, size for prefill | 170,000 ÷ (25,000 × 0.7) = 9.7, rounded up, plus 1 | Prefill | 11 | 226,000 ÷ 17,500 → 13 + 1 = 14 | Over 10 |
| C. Keep prefixes resident, size on live runs | 176 ÷ 34.3 = 5.1, rounded up, plus 1; only ~20,000 new tokens/sec to prefill | KV memory for live runs | 7 | 234 ÷ 34.3 = 6.8 → 7 + 1 = 8 | Within |

### Decision
Choose **C** and order **3 more replicas now** (8 − 5), because:
- **It is the only option that meets the TTFT SLO inside the budget at lead time:** B needs 14 by the
  time GPUs arrive; A is the current breach.
- **The breach is a prefill problem in disguise.** Sizing on in-flight calls ignored that evicted
  prefixes force re-prefill; the replica formula takes the larger of the sequence and prefill terms.
- **Resident caches turn prefill into a small term:** 20,000 new tokens/sec needs about 2 replicas of
  prefill work today (3 with the spare), so KV memory for live runs binds instead.
- **Ordering on lead time** avoids a second breach: 8 covers the peak expected a quarter from now.

**What would change it:**
- **Longer tool waits:** at 6 s of tool time per step a run lasts 128 s, live runs reach 256, and C needs
  9 replicas today and 11 at lead time, over budget. Then evict on long waits and compact transcripts.
- **Growth faster than 10% a month:** re-run the plan and order earlier.
- **A load test showing a different knee** after an engine or model change: every number above moves.

### Trade-offs accepted
- **Resident caches cost 2 replicas** over calls-only sizing (7 versus 5 today). Watch KV-cache
  utilization and prefix-cache hit rate per agent type.
- **The plan depends on traced step counts.** Watch steps per run at p95 after every prompt or tool
  change; one extra step adds a tenth to calls/sec.
- **Ordering ahead pays for idle GPUs** until growth arrives. Watch peak in-flight and live runs against
  provisioned capacity each week.
- **Predicted and observed concurrency can drift.** Watch the engine's running and waiting counts against
  L = λW.

### Review rubric
- **Demand is expressed in linked units** (calls/sec, input and output tokens/sec, in-flight and live
  runs), each recomputing from the stated traces.
- **Replica capacity comes from a load test at the SLO,** naming whether KV memory or prefill binds.
- **Both terms of the sizing formula are computed,** and the larger one is used.
- **Cache residency for waiting runs is an explicit choice** with its replica cost.
- **The order covers peak at the procurement lead time,** with growth stated.
- **Reversal triggers are numbers** such as tool time per step or the growth rate.

## Why it exists
- **The web habit breaks.** A classic web service has fairly uniform requests: if one pod handles 500 RPS
  at p99 under 100 ms, ten pods handle about 5,000. Teams carry that habit into LLM serving and it breaks
  in three ways.
- **Cost per request is wildly uneven.** A 200-token classification and a 30,000-token document summary
  are both "one request", yet the second processes 150 times the input tokens (more in attention work,
  which grows faster than linearly with context) and holds GPU memory far longer. An RPS figure says
  nothing about which mix you will get.
- **A request occupies a GPU for seconds, not milliseconds.** Output tokens come one at a time, so a
  500-token answer at 30 ms per token holds a batch slot and its KV cache for 15 seconds. What saturates a
  replica is sequences *in flight at once*, bounded by KV memory and promised per-token latency, not by
  arrivals per second.
- **The limiting units come from different places.** A self-hosted engine runs out of KV memory or decode
  bandwidth; a managed API throttles requests and tokens per minute; your websocket tier and session store
  care about open conversations. A plan in one unit misses the constraint that binds.
- **The naive fixes fail.** Averaging tokens/sec over a day or adding GPUs until the alerts stop either
  under-provisions the peak or pays for idle hardware without knowing which.

## How it works
### 1. Describe the workload as distributions, not averages
Capacity starts from traffic, not hardware. Collect, per use case:

- **Peak concurrent active users** and how often each sends a message.
- **Model calls per message** (an agent may make several).
- **Input tokens per call** (system prompt + history + retrieved context) and **output tokens per call**,
  at p50 and p95.
- **Peak-to-average ratio** of traffic over a day and a week.
- **Why the tails:** a long-context minority can consume most of the KV memory while being a small share
  of requests.

### 2. Convert between the three units
| Unit | Formula | What it sizes |
|---|---|---|
| **Requests/sec (λ)** | active users × messages per user per second × calls per message | Arrivals, RPM quotas |
| **Tokens/sec** | input TPS = λ × mean input tokens; output TPS = λ × mean output tokens | Prefill and decode work, TPM quotas |
| **Concurrent in-flight sequences (L)** | Little's Law: L = λ × W, with W ≈ queue wait + TTFT + output tokens × TPOT (time per output token) | Batch slots and KV memory: the GPUs |
| **Concurrent sessions** | Open conversations, mostly idle; sessions × duty cycle (fraction of wall time a session has a call running) ≈ in-flight sequences | Connection tier, session store, prefix-cache retention |

- **Keep input and output separate** *because* they hit different resources: input is prefill,
  compute-bound and parallel; output is decode, bandwidth-bound and sequential per request. Planning on
  "total tokens" hides which one binds.
- **Little's Law holds for any stable system in steady state,** whatever the arrival or service
  distribution, which is *why* it is the safe bridge between units. It does not tell you queueing delay;
  that is the job of the queueing-theory lesson.

### Agent runs: two levels of Little's Law
- **Calls per second:** λ = runs per second × steps per run (plus sub-agent calls if the agent fans out).
- **Input per call is not constant:** with a prefix of P tokens growing by g per step, the mean prompt over
  n steps is P + g × (n − 1) ÷ 2, and the last step is the largest. Plan with the mean for tokens/sec and
  the last step for KV memory.
- **In-flight sequences** come from calls: L = λ × W per call, where W excludes tool time.
- **Live runs** come from runs: runs per second × run duration, where duration includes tool time. That
  sizes the session store and, if you keep each waiting run's prefix resident, KV memory too.
- **Prefix caching changes prefill, not decode:** with an append-only transcript only the ~g new tokens per
  step are prefilled, so prefill load falls by roughly (mean prompt ÷ g) while decode is unchanged.

### The circularity: W depends on load
- **TPOT rises as the batch grows,** so more in-flight sequences make each call longer, which makes L
  larger still.
- **Break the loop** by using TPOT and TTFT measured *at the concurrency you plan to run*, not at batch 1.
  An unloaded TPOT undercounts in-flight sequences exactly when it matters.
- **Consistency identity:** a replica running B sequences at TPOT t produces about B ÷ t output tokens per
  second, so in-flight sequences, TPOT and output tokens/sec must agree with each other.

### 3. Measure capacity per replica at the SLO
Per-replica capacity is not a spec-sheet number.

1. **Load-test with a realistic token mix and sweep concurrency.** Throughput rises with batch size while
   per-request TPOT and TTFT degrade.
2. **Find the latency knee.** Usable capacity is the highest concurrency (and its tokens/sec) where p95
   TTFT and p95 TPOT still meet the latency budget, sometimes called goodput.
3. **Cross-check against KV memory:** max sequences ≈ free memory after weights ÷ (KV bytes per token ×
   mean context length).
4. **The lower of the two is the replica's capacity.** Know which one it is, because the fixes differ: more
   memory or a GQA/quantized cache vs. more replicas.

### 4. Size, then add headroom
```
replicas = max( peak in-flight sequences ÷ (per-replica sequence capacity × target utilization),
                peak input tokens/sec ÷ per-replica prefill capacity at that load )   rounded up
         + spares for N+1, rolling deploys and autoscaler lag
```

- **Usually one term dominates,** but a prefill-heavy mix (long RAG contexts, short answers) can be bound by
  prefill while sequence slots sit free.
- **Target utilization sits below 100%** *because* queueing delay grows sharply as utilization approaches
  one.
- **Add spares** for N+1 failure tolerance, rolling deploys and autoscaler lag (GPU nodes and model loading
  can take minutes).
- **Plan over the lead time.** The horizon must be at least as long as your procurement or quota lead time:
  if reserved GPUs take a quarter to arrive and traffic grows 10% a month, today's plan must cover the peak
  expected a quarter from now.
- **For a managed API,** the same numbers become requests-per-minute and tokens-per-minute quotas per
  region and per model. Check whether your provider counts the requested `max_tokens` against the token
  quota at admission, because some do, which makes generous `max_tokens` settings consume quota you never
  use.

## Diagram
Read it top to bottom: agent traffic (runs, steps, tokens per step) becomes three units, a load test
turns hardware into a capacity number, and replicas fall out of peak demand divided by capacity, then
grow for headroom and lead time.

```mermaid
flowchart TD
    T["Agent traffic: runs, steps, tokens per step"] --> A["Arrivals: lambda model calls per second"]
    A --> TK["Input and output tokens per second"]
    A --> L["In-flight sequences: L = lambda times W"]
    LT["Load test at the latency SLO"] --> C["Replica capacity: latency knee or KV bound"]
    L --> S["Replicas from the binding unit"]
    TK --> S
    C --> S
    S --> H["Add N+1, deploy and autoscaler headroom"]
    H --> G["Project to peak at procurement lead time"]
    G -.->|"managed API"| Q["RPM and TPM quotas per region and model"]
```

## Examples
### A capacity plan in code
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

- **Reading the result:** 60 calls/sec, each lasting about 8 s (0.5 s TTFT + 250 × 30 ms), means 480
  sequences in flight, so only 40% of the 1,200 sessions are busy at any instant.
- **Replicas:** at 64 sequences per replica, 7.5 replicas would run at 100%; at a 70% target that is 11,
  plus one spare gives 12.

### A capacity plan for a multi-step research agent
Same model shape and GPU as the cross-check below (128 KiB of KV per token, ~57 GB free after weights
with margin). Figures are illustrative: 2 runs/sec at peak, 10 steps per run, a 4,000-token prefix
growing by 1,000 tokens per step, 200 output tokens per step, 2 s of tool time per step.

```python
import math

def plan_agent(runs_per_s, steps, prefix_tok, growth_tok, out_tok, ttft_s, tpot_s, tool_s,
               kv_bytes_per_tok, kv_free_gb, target_util=0.7, spare=1):
    calls = runs_per_s * steps                                   # λ in model calls per second
    mean_in = prefix_tok + growth_tok * (steps - 1) / 2          # prompt grows every step
    w_call = ttft_s + out_tok * tpot_s                           # seconds per model call
    run_s = steps * (w_call + tool_s)                            # model time plus tool time
    in_flight = calls * w_call                                   # Little's Law on calls
    live_runs = runs_per_s * run_s                               # Little's Law on runs
    seqs_per_replica = int(kv_free_gb * 1e9 // (kv_bytes_per_tok * (mean_in + out_tok)))
    def reps(load): return math.ceil(load / (seqs_per_replica * target_util)) + spare
    return dict(calls=calls, mean_in=mean_in, in_tps=calls * mean_in, new_in_tps=calls * growth_tok,
                w_call=round(w_call, 1), run_s=round(run_s), in_flight=round(in_flight),
                live_runs=round(live_runs), seqs_per_replica=seqs_per_replica,
                replicas_calls_only=reps(in_flight), replicas_keep_cache=reps(live_runs))

print(plan_agent(2, 10, 4000, 1000, 200, 0.8, 0.03, 2.0, 2 * 32 * 8 * 128 * 2, 57))
# {'calls': 20, 'mean_in': 8500.0, 'in_tps': 170000.0, 'new_in_tps': 20000, 'w_call': 6.8,
#  'run_s': 88, 'in_flight': 136, 'live_runs': 176, 'seqs_per_replica': 49,
#  'replicas_calls_only': 5, 'replicas_keep_cache': 7}
```

| Reading | Meaning |
|---|---|
| 20 calls/sec from 2 runs/sec | ten times the arrivals a per-user count would suggest |
| 170,000 input tokens/sec, but 20,000 new | prefix caching on an append-only transcript removes most prefill, but only while each run's prefix stays cached between steps (the 7-replica case) |
| 136 in flight, 176 live runs | 40 runs are in a tool at any instant |
| 5 vs 7 replicas | keeping every waiting run's cache resident costs 2 more replicas; letting it be evicted costs re-prefill instead |

### Worked scenario: cross-checking the plan
Three checks on the same plan; if any disagreed badly, one of the inputs would be wrong.

| Check | Calculation | Verdict |
|---|---|---|
| **Memory** | Llama-3-8B-shaped model (32 layers, 8 KV heads, head dim 128, fp16): 2 × 32 × 8 × 128 × 2 bytes = 128 KiB per token; 64 sequences × 3,250 tokens ≈ 27 GB | Fits beside ~16 GB of weights on an 80 GB GPU |
| **Decode consistency** | 480 sequences over 12 replicas = 40 per replica; 40 ÷ 0.03 s ≈ 1,330 output tokens/sec per replica, about 16,000 across the fleet | Slightly above the 15,000 demanded, because part of each call's 8 s is TTFT rather than decode |
| **Prefill** | 180,000 input tokens/sec across 12 replicas = 15,000 per replica | The load test must confirm it; if the 2,000-token system prompt is shared, prefix caching removes most of that work |

### Worked scenario: planning over the lead time
Same workload, but traffic grows 10% a month and reserved GPUs take a quarter to arrive.

| Step | Calculation | Result |
|---|---|---|
| Growth over the lead time | 1.1 × 1.1 × 1.1 | ≈ 1.33× |
| Peak in-flight sequences in a quarter | 480 × 1.33 | ≈ 639 |
| Replicas for that peak | 639 ÷ (64 × 0.7) = 14.3, rounded up, plus 1 spare | 16 replicas |
| What to order today | 16 − 12 | 4 more replicas, ordered now, not when alerts fire |

## Production patterns
- **Plan from agent traces, not user counts.** Pull steps per run, tokens per step and tool time per step
  from production traces into the capacity script, per agent type. Monitor steps-per-run p95 after every
  prompt or tool change, since one extra step raises calls/sec by a tenth on a 10-step agent.
- **Load test by replaying recorded agent runs.** Drive the test with real transcripts, so token lengths
  follow production distributions (not fixed-length synthetic prompts), prompts grow step by step and
  tool gaps are realistic; sweep concurrency, recording p95 TTFT and TPOT at each level. Monitor where the
  latency knee and the KV-memory bound fall versus chat-shaped tests, and re-run after every model or
  engine change.
- **Reconcile predicted and observed concurrency.** Export the engine's running and waiting request counts
  (vLLM publishes both) and compare them with L = λW from your telemetry. Monitor the gap; a large one means
  W, the token mix or fan-out is mis-measured.
- **Living capacity model.** Keep the plan as a script fed weekly by production histograms of arrivals,
  tokens, TTFT and TPOT, and alert when projected peak at lead time exceeds provisioned capacity. Monitor
  peak-to-average ratio and growth rate.
- **Cap work per call.** Set `max_tokens` and a context cap per route so W and KV bytes per sequence stay
  bounded. Monitor input and output token p95 per route after each release.
- **Headroom as a policy, not a guess.** Fix a target utilization (the example uses 70%) and N+1 per pool,
  and widen headroom where scale-up is slower than the traffic ramp. Monitor queue wait p95 and preemption
  counts.
- **Order on lead time.** File quota increases and reservation requests for the peak expected at now plus
  lead time. Monitor provider throttle counts and remaining quota per region and model.

## Trade-offs
- **Resident caches for waiting runs vs replica count.** Keeping each run's prefix in KV memory while it
  waits on a tool saves re-prefill on the next step, but sizes memory by live runs instead of in-flight
  calls (7 replicas instead of 5 in the example); it flips toward eviction when tool waits are long.
- **Higher target utilization vs queueing delay.** It cuts GPU cost, but queueing delay rises steeply near
  saturation; it flips toward low utilization when traffic is bursty or the TTFT budget is tight.
- **p95 token counts vs means.** Planning to p95 over-provisions on quiet days but protects the tail; means
  are acceptable only when context length is tightly capped.
- **Larger batches vs TPOT.** Larger per-replica batches raise tokens/sec per GPU but lengthen TPOT for
  every user; interactive chat favours smaller batches, offline batch jobs favour the largest batch memory
  allows.
- **Static peak provisioning vs autoscaling.** Static is simple and fast to respond, but wastes money when
  peak-to-average is high; autoscaling saves cost only if scale-up time is shorter than the ramp of your
  traffic.

## Pitfalls
- **Planning in RPS only.** Symptom: a plan that looks fine until one tenant sends long documents and
  KV-cache preemptions spike while RPS is unchanged.
- **Using the engine's peak tokens/sec from a benchmark at huge batch.** Symptom: real interactive traffic
  misses its TPOT SLO at a fraction of that throughput.
- **Forgetting agent fan-out,** counting user messages instead of model calls. Symptom: provider 429s on
  requests-per-minute at a user load the plan said was safe.
- **Using the first step's prompt length for every step.** Symptom: KV preemptions late in long runs,
  because the last step's context is far larger than the plan's per-call figure. Fix: model prompt growth
  per step and size memory from the last step.
- **Counting tool time as in-flight model work.** Symptom: an oversized GPU fleet for an agent that spends
  most of its run waiting on APIs. Fix: W per call excludes tool time; live runs include it.
- **Sizing to the daily average.** Symptom: queue depth and TTFT climb every weekday morning while the
  dashboard shows modest mean utilization.
- **Load-testing with fixed-length synthetic prompts.** Symptom: a replica that met its SLO in the test
  misses it in production, where the long tail of contexts fills KV memory sooner.
- **Treating open sessions as concurrent load.** Symptom: a GPU fleet several times larger than needed
  because idle conversations were counted as in-flight sequences.

## When to use
- **Do it before** any launch, before signing a reserved-capacity or provisioned-throughput commitment, and
  whenever the traffic mix changes: a new feature with longer context, an agent that adds tool-calling
  steps, a large tenant onboarding.
- **Keep it alive** as a spreadsheet or script fed by production telemetry: arrivals, input and output token
  histograms, TTFT and TPOT percentiles, in-flight sequences, KV-cache utilization and provider throttle
  counts.
- **For agents, re-plan** whenever steps per run, tool latency or context compaction changes, since each
  moves calls/sec, prompt size or live runs.
- **Managed APIs only?** The plan is shorter but still needed, because quotas are per region and per model
  and raising them can take days.
- **Interviews:** expect "how many GPUs for N users" questions; the strong move is to ask for the token
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

## Interview questions
### A product manager asks how many GPUs you need for 5,000 users of a new agent. What do you ask before answering?
- **Traffic shape:** peak concurrent active users, messages per user per minute, and model calls per message
  (agents fan out).
- **Token distribution:** input and output tokens per call at p50 and p95, because one request can cost a
  hundred times another.
- **The SLO:** p95 TTFT and TPOT targets, since replica capacity is defined at the latency budget.
- **Then reason:** λ, tokens/sec and L = λW, divided by a load-tested per-replica capacity, plus headroom.

### An agent runs 10 steps with a 4,000-token prefix growing 1,000 tokens per step and 2 s of tool time per step. How do you size for 2 runs per second?
- **Calls:** 2 × 10 = 20 calls/sec; the mean prompt is 4,000 + 1,000 × 9 ÷ 2 = 8,500 tokens, so 170,000
  input tokens/sec, of which ~20,000 are new if the transcript is append-only and prefix-cached.
- **Concurrency:** each call takes 0.8 s + 200 × 30 ms = 6.8 s, so 136 in flight; runs last ~88 s, so 176
  are live, 40 of them in tools.
- **Replicas:** about 49 sequences fit per replica, so 5 with a spare, or 7 if every waiting run keeps its
  cache resident.

### Why is requests per second not enough to plan LLM capacity?
- **Uneven cost:** a 200-token classification and a 30,000-token summary are both one request.
- **Long occupancy:** a 500-token answer at 30 ms per token holds a batch slot and KV cache for 15 seconds,
  so in-flight sequences, not arrivals, saturate a replica.
- **Different limits:** engines run out of KV memory or bandwidth, APIs throttle on RPM and TPM, and session
  tiers care about open conversations.

### A benchmark says one replica does several thousand tokens per second, but production misses its TPOT SLO at a fraction of that. Why?
- **Benchmark batch:** peak tokens/sec is usually measured at a huge batch where TPOT is far above an
  interactive budget.
- **Unrealistic prompts:** fixed-length synthetic prompts miss the long tail of contexts that fills KV
  memory sooner.
- **Fix:** sweep concurrency with production-shaped prompts and take the highest load where p95 TTFT and
  TPOT still pass.

### Why plan for a target utilization below 100%, and how would you choose it?
- **Queueing:** delay grows sharply as utilization approaches one, so a fleet planned at 100% misses its
  TTFT budget at every burst.
- **Choose by burstiness and scale-up time:** bursty traffic or a tight TTFT budget push the target down;
  steady traffic and fast scale-up allow it higher.
- **Spares are separate:** N+1, rolling deploys and autoscaler lag are added on top of the utilization
  target.

### Traffic grows 10% a month and reserved GPUs take a quarter to arrive. How does that change your plan?
- **Plan for the future peak:** over three months 1.1³ ≈ 1.33, so 480 in-flight sequences today become
  about 639.
- **Size for it now:** at 64 sequences per replica and a 70% target that is 15 replicas plus a spare, 16
  instead of today's 12.
- **Order today,** because anything ordered when alerts fire arrives a quarter too late.

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

## Use cases & projects
### Use cases
- **Launching a self-hosted agent.** Runs per second, steps and tokens per step from staging traces set
  the replica count and the reservation to order.
- **Adding a step or a tool to an existing agent.** The plan shows how much one more step raises calls,
  tokens and in-flight sequences before the change ships.
- **Hosted-API agents.** The same numbers become TPM and RPM quota requests per region and model, filed
  with lead time.
- **Separating agent and chat pools.** Planning both shows when long agent runs would push chat past its
  TTFT budget on a shared fleet.

### Mini project
**Build a trace-driven capacity plan for an agent (2-3 hours).**
- **Goal:** turn real agent traces into calls/sec, tokens/sec, in-flight sequences, live runs and a
  replica count.
- **Steps:** run a small tool-calling agent on 30 tasks and log per step: prompt tokens, output tokens,
  model time and tool time; fit steps per run and prompt growth; feed them into `plan_agent` above at
  three peak run rates; compare predicted in-flight calls with a short load test or a concurrency count.
- **Done when:** you have a one-page plan with replicas at each run rate, which term binds (sequence
  slots from `plan_agent`, or prefill: its `in_tps` against a per-replica prefill rate you measure or
  state as an assumption), and how the answer changes with prefix caching and a 20% shorter transcript.
