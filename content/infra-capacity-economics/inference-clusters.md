---
id: inference-clusters
module: infra-capacity-economics
name: Inference clusters
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - infra-capacity-economics/gpu-infrastructure
  - infra-capacity-economics/ray-kuberay
  - infra-capacity-economics/keda-autoscaling
  - infra-capacity-economics/capacity-planning
  - serving-inference/continuous-batching
  - serving-inference/tensor-parallelism-at-inference
  - serving-inference/prefix-caching
  - serving-inference/engines
sources:
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Zhong et al., 2024 — DistServe: Disaggregating Prefill and Decoding for Goodput-optimized LLM Serving
  - Official docs: vLLM
  - Official docs: Kubernetes
---

## TL;DR
An inference cluster is a fleet of model-server replicas (vLLM, TGI, TensorRT-LLM, Triton) behind a
router, scheduled onto GPU nodes, scaled on LLM-specific signals. It exists because a single GPU box
caps out on memory and bandwidth long before real traffic does, and because LLM requests are
long-lived, variable-length and stateful (the KV cache), which breaks the stateless web-tier playbook
of round-robin load balancing and CPU-based autoscaling.

## Why it exists
One model server on one GPU has a hard ceiling: weights plus KV cache must fit in VRAM, decode is
bound by memory bandwidth, and the number of concurrent sequences that fit sets both throughput and
tail latency. Once traffic exceeds what one replica serves within its latency budget, the only moves
are a bigger GPU (there is a largest one) or more replicas. More replicas means a cluster, and a
cluster immediately raises questions that a stateless web fleet never had to answer.

The naive answer is "put the model servers behind the same load balancer we use for the API tier and
autoscale on CPU". It fails for three reasons. First, requests are not interchangeable: one request
may be a 200-token chat turn and the next a 60,000-token document summary; round-robin sends the
long one to a replica that is already saturated and the tail latency of everyone on that replica
collapses. Second, the useful capacity signal is not CPU or even GPU utilization but how full the KV
cache is and how deep the engine's waiting queue is, since a GPU at 40% SM utilization can still be
unable to admit another sequence. Third, the cache makes replicas stateful: a request whose prefix
is cached on replica A costs a full prefill on replica B, so routing affects cost, not just balance.
Also, a model that needs more memory than one GPU has (a 70B model in fp16 is ~140 GB of weights)
must be split across several GPUs that act as one replica, which is a topology decision, not a
scaling knob. An inference cluster is the structure that answers all of these on purpose.

## How it works
### The layers
A cluster is organised as nodes → replicas → engines, with a router in front and a scheduler
behind. Each layer answers a different question.

1. **Replica.** One running instance of a serving engine with one copy of the model. If the model
   does not fit on one GPU, the replica spans several GPUs on the same node using tensor parallelism
   (each layer's matrices are sliced across GPUs and the shards exchange activations every layer).
   TP stays within a node *because* it needs an all-reduce per layer per token, which is only
   affordable over NVLink-class bandwidth; crossing the network for it would make every decode
   step wait on the slowest link. (Rack-scale systems whose NVLink domain spans several trays move
   that boundary from "node" to "NVLink domain", but the rule is the same.) Pipeline parallelism
   across nodes is used only when a model cannot fit in one node or NVLink domain.
2. **Model pool.** All replicas of one model (and one configuration: quantization, max context,
   adapters). Scaling happens per pool, because a 7B chat model and a 70B reasoning model have
   completely different capacity curves and traffic.
3. **Router / gateway.** Receives requests, picks a pool by model name, picks a replica inside the
   pool, streams tokens back. This is where LLM-aware routing lives.
4. **Scheduler inside the engine.** Continuous batching admits new sequences into the running
   batch each step as others finish; it is the reason throughput grows with concurrency until the
   KV cache is full.
5. **Orchestrator.** Kubernetes (often with KubeRay or a serving layer such as KServe) places
   replicas on nodes with the right GPU type and count, restarts failures, and runs the autoscaler.

### Routing that knows what a request costs
A good router does not round-robin. The usual signals, in rough order of value:
- **Queue depth and in-flight sequences per replica** ("least-loaded"), so a long request does not
  land on a replica that is already at its concurrency cap. Replicas expose these as metrics
  (vLLM publishes running/waiting request counts and KV-cache usage).
- **Prefix-cache affinity.** Requests sharing a long system prompt or a conversation are hashed to
  the replica that already holds that prefix, which turns a full prefill into a cache hit. The
  simple version is consistent (or rendezvous) hashing on a fixed-length prompt prefix, so adding a
  replica remaps only a small share of keys; more advanced routers track which prefixes each replica
  actually holds (for example a radix tree of cached prefixes). Either way there is spillover to
  least-loaded when the preferred replica is saturated *because* affinity is worth little if it adds
  queueing delay.
- **Session stickiness for multi-turn chats**, a special case of the above.
- **Priority classes**, so interactive traffic is admitted ahead of batch jobs that share the pool.

### Disaggregated prefill and decode
Prefill is compute-bound (big matrix multiplies over the whole prompt); decode is bandwidth-bound
(one token per step, reading the weights and the whole cache). Running both on the same replica
makes a 40k-token prefill stall every decode stream on that GPU for hundreds of milliseconds to
several seconds depending on model size, which shows up as TPOT jitter. Chunked prefill, which
splits a long prompt into pieces interleaved with decode steps, is the first mitigation inside one
replica. Larger clusters go further and split the phases: prefill replicas compute the KV cache and ship it over
a fast interconnect to decode replicas, each class sized and scaled separately. The cost is the KV
transfer and more moving parts, so it pays only at scale with long prompts.

### Scaling on the right signal
The autoscaler should read engine metrics: waiting-queue length, KV-cache utilization, TTFT, or
tokens/second against the replica's measured capacity. CPU is meaningless and even GPU utilization
is misleading, since a replica with its KV cache full of idle-but-live sessions is "available" by
utilization and "full" by admission. Scale-up is slow (pulling tens of GB of weights and warming
the engine can take minutes), so the cluster needs headroom and predictive or scheduled scaling
rather than purely reactive rules; scale-down must drain in-flight streams before killing a pod.

### Node topology and placement
Replicas request whole GPUs (or MIG slices for small models) and must be pinned to node pools that
have the right card, because a 70B replica scheduled onto an L4 node simply fails. Spreading
replicas of a pool across nodes and zones limits blast radius from a node failure; packing them
raises the chance that a single node loss removes a whole pool. Both are deliberate choices.

### Admission control, failure and rollout
When every replica in a pool is at its concurrency cap, something must say no, and it should be the
gateway, not the engine. Engines queue internally without bound by default, so an overloaded pool
looks like slowly rising TTFT until clients time out and retry, which adds load. A gateway that
bounds the wait (reject with 429 and a `Retry-After` once the expected queue time exceeds the TTFT
budget, or shed low-priority traffic first) turns overload into a fast, explicit signal *because*
a request that will miss its deadline is cheaper to refuse than to serve late.

Rollouts are a capacity event. A rolling update of a pool sized with no spare removes one replica
per step, and the new replica arrives with a cold prefix cache, so the pool is both smaller and
slower until it warms. That is why the N+1 spare doubles as rollout surge capacity, and why a model
*version* change (new weights, new behaviour) is usually done blue/green as a separate pool with
traffic shifted by weight, so that rollback is a routing change rather than a redeploy that has to
pull tens of GB of weights again.

## Example
Sizing a pool and routing inside it. The capacity numbers are illustrative placeholders you would
replace with your own load-test results.

```python
import math, hashlib
from dataclasses import dataclass, field

@dataclass
class Replica:
    name: str
    max_seqs: int            # concurrency at which p95 TPOT still meets the budget (load-tested)
    running: int = 0
    hot_prefixes: set = field(default_factory=set)

def replicas_needed(peak_out_tok_s, out_tok_s_per_replica, headroom=1.3, n_plus=1):
    """Pool size from peak decode demand, with headroom for burst and N+1 for a node loss."""
    return math.ceil(peak_out_tok_s * headroom / out_tok_s_per_replica) + n_plus

# Peak 6,000 output tok/s; a 2xH100 TP=2 replica sustains ~1,500 tok/s within budget (illustrative).
print("replicas:", replicas_needed(6_000, 1_500))      # -> ceil(5.2)+1 = 7

pool = [Replica(f"r{i}", max_seqs=48) for i in range(7)]

def route(prompt: str, prefix_chars: int = 2048) -> Replica:
    # Affinity only works if the shared prefix (system prompt) is at least prefix_chars long.
    key = hashlib.sha1(prompt[:prefix_chars].encode()).hexdigest()
    preferred = pool[int(key, 16) % len(pool)]  # modulo for brevity; use consistent hashing in prod
    if preferred.running < preferred.max_seqs:
        preferred.hot_prefixes.add(key); preferred.running += 1
        return preferred
    # Affinity is not worth queueing for: spill to the least-loaded replica with room.
    cands = [r for r in pool if r.running < r.max_seqs]
    if not cands:
        raise RuntimeError("pool saturated: shed or queue at the gateway")
    best = min(cands, key=lambda r: r.running); best.running += 1
    return best

def finished(r: Replica):             # call when the stream ends, or slots leak and the pool "fills"
    r.running -= 1
```

The sizing function makes the two hidden assumptions explicit: a replica's capacity is the
concurrency at which it still meets the latency budget (not its raw maximum), and one extra replica
covers a node loss without breaching it (if one node hosts several replicas, N+1 must be counted in
nodes, not replicas). The router prefers the replica that already holds the prompt prefix, but only
while it has admission room. The modulo hash is a shortcut: when the pool resizes, almost every key
moves and the cache affinity resets, which is why real routers use consistent or rendezvous hashing.

## Trade-offs
- Many small replicas (TP=1) give fine-grained scaling and isolation; a few large TP replicas give
  higher per-replica throughput and are mandatory once weights exceed one GPU, but a single failure
  removes a bigger slice of capacity.
- Prefix-affinity routing cuts prefill cost for shared prompts, but it concentrates load; when one
  tenant's prefix is hot, you must spill to other replicas or that replica becomes the bottleneck.
- Disaggregating prefill from decode stabilises TPOT and lets each phase scale on its own signal,
  at the price of KV-cache transfer and a second pool to operate; below a few replicas it is not
  worth it.
- Headroom (idle capacity) buys tail-latency protection and absorbs slow scale-up; every point of
  headroom is paid GPU time, so the right level depends on how spiky traffic is and how long a cold
  replica takes to become ready.
- Spreading replicas across zones improves availability but costs cross-zone traffic and sometimes
  loses access to the cheapest GPU capacity pool, which flips the decision for batch workloads.

## Pitfalls
- Autoscaling on CPU or GPU utilization; the symptom is replicas reporting "healthy" and under
  50% utilization while TTFT climbs into seconds because the KV cache or waiting queue is full.
- Round-robin routing across replicas; the symptom is p99 latency far above p50 with no correlation
  to overall load, caused by long requests piling onto already-busy replicas.
- Killing pods on scale-down without draining; the symptom is a burst of truncated streams and
  client retries every time the autoscaler steps down.
- Treating a tensor-parallel replica as N independent GPUs in the capacity model; the symptom is a
  plan that promises N× the throughput a TP group actually delivers.
- Scheduling a replica onto a node pool with the wrong GPU type or too little host RAM for weight
  loading; the symptom is CrashLoopBackOff with OOM during model load rather than at serving time.
- No readiness gate that waits for the engine's warm-up; the symptom is a wave of errors and
  timeouts right after every scale-up or rollout because traffic hits a replica still loading
  weights.

## When to use
You need a cluster the moment one replica cannot meet peak demand within the latency budget, when
you need to survive a node loss without an outage, or when you serve more than one model. Below
that, one well-tuned replica (or a hosted API) is simpler and cheaper. When you do build one, decide
the replica shape per model (TP degree, quantization, max context), the routing policy (least-loaded
first, prefix affinity second), the scaling signal (queue depth and KV utilization, never CPU), and
the headroom. Measure a replica's capacity curve under load before writing any autoscaling rule, and
keep TTFT, TPOT, queue depth and KV usage on one dashboard. In interviews, expect to be asked why
you would not use the normal load balancer and autoscaler, and to reason from KV-cache memory and
prefill/decode asymmetry to the answer.

## Role lens
- **Engineer:** Configures the engine (TP degree, max sequences, max context), exposes its metrics,
  writes the router's selection logic and the autoscaler's queries, and load-tests each replica shape.
- **Architect:** Decides pool boundaries per model, TP versus more replicas, whether to disaggregate
  prefill and decode, zone spreading, and the headroom policy that balances cost against tail latency.
- **FDE:** Sizes a customer's cluster from their traffic shape and GPU inventory, explains why their
  existing Kubernetes autoscaler misbehaves with LLM pods, and sets up the right metrics and drain
  behaviour.

## Check yourself
**Q:** A team runs eight identical vLLM replicas behind an ordinary round-robin load balancer with
CPU-based autoscaling. Under load, p50 latency is fine but p99 TTFT is 20× p50 and the autoscaler
never fires. Diagnose the cause and describe the fix, including what you would measure.
**A:** Round-robin ignores that LLM requests vary 100× in length and occupy a replica for seconds to
minutes, so long requests pile onto replicas that are already at their concurrency cap; those
replicas queue new arrivals, which is the p99 tail. CPU never rises because decode is GPU-bandwidth
bound and the pods are waiting on the GPU, so a CPU rule sees an idle fleet. The fix is to route on
engine state (least in-flight sequences, with prefix affinity when a prefix is hot) and to scale on
engine metrics: waiting-queue length and KV-cache utilization, with a TTFT SLO as the alarm. Before
changing rules, load-test one replica to find the concurrency at which p95 TPOT still meets budget
and treat that as its capacity; size the pool from peak tokens/second with headroom and one spare
replica, and add a readiness gate so new replicas take traffic only after warm-up.

## Quiz
### Q: Why is tensor parallelism normally kept inside a single node rather than spread across nodes?
- [ ] Kubernetes cannot schedule a pod across two nodes
- [x] It needs an all-reduce every layer for every token, which is only fast enough over intra-node interconnects
- [ ] Cross-node tensor parallelism would duplicate the model weights on every node
- [ ] The KV cache must be contiguous in memory, which a network cannot provide
> Tensor parallelism slices each layer and exchanges partial results per layer per step; over a
> network that round-trip would dominate every decode step (rack-scale NVLink domains extend the
> boundary but do not change the reasoning). Pipeline parallelism is the cross-node
> option because it exchanges activations only at stage boundaries.

### Q: Which signal is the best basis for autoscaling an LLM model pool?
- [ ] Average CPU utilization of the serving pods
- [ ] GPU SM utilization reported by the driver
- [x] Engine queue depth and KV-cache utilization measured against the replica's load-tested capacity
- [ ] Number of HTTP connections open on the load balancer
> Admission is limited by KV-cache memory and the engine's scheduler, not by CPU; GPU utilization can
> be low while the cache is full of live sessions. Queue depth and cache usage reflect whether the
> replica can admit more work.

### Q: Prefix-affinity routing improves cost; under what condition should the router ignore it?
- [ ] When the prompt is shorter than the model's context window
- [ ] When the replica holding the prefix is in a different availability zone
- [x] When the preferred replica is at its concurrency cap, so spilling to a less-loaded replica avoids queueing delay
- [ ] When the request uses streaming, since streamed responses cannot reuse a cache
> A cache hit saves prefill compute, but sending a request to a saturated replica trades that saving
> for queueing time and tail-latency damage. Affinity should be a preference with a spillover rule,
> not a hard constraint.
