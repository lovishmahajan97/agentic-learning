---
id: scalability
module: infra-capacity-economics
name: Scalability
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - infra-capacity-economics/capacity-planning
  - infra-capacity-economics/queueing-theory
  - infra-capacity-economics/keda-autoscaling
  - infra-capacity-economics/api-provider-quotas-and-regional-limits
  - serving-inference/continuous-batching
  - serving-inference/prefix-caching
  - reliability/backpressure-and-load-shedding
sources:
  - Amdahl, 1967 — Validity of the Single Processor Approach to Achieving Large Scale Computing Capabilities
  - Gunther, 2007 — Guerrilla Capacity Planning
---

## TL;DR
Scalability is how throughput grows as you add resources, and it is almost never linear: shared
resources serialize part of the work (contention), and keeping shared state consistent adds cost that
grows with the number of workers (coherency), so past some point more replicas make things slower. In
AI systems the hard limits are rarely the stateless app tier; they are GPU memory, provider quotas,
cache affinity and shared stores such as vector indexes.

## Why it exists
The intuitive model of scaling is "twice the machines, twice the throughput". It holds for perfectly
independent work and almost nothing else. Every real system has a part that cannot be parallelized: a
single primary database, a global rate limit, a lock around a shared index, a coordinator that every
request touches. Amdahl showed that if a fraction of the work is serial, speedup is capped at one over
that fraction no matter how many workers you add. Worse, distributed systems also pay a coordination
cost that grows with the square of the worker count, because workers must exchange state with each other
(cache invalidation, consensus, connection fan-in). That cost produces *retrograde* scaling: throughput
peaks and then falls.

AI systems add their own non-linear limits. A model replica needs enough accelerator memory for its
weights plus a useful KV cache, so capacity comes in large, indivisible steps (one or several whole GPUs
for a large model); a new replica can take minutes to start because weights must be loaded; a hosted API has a
tokens-per-minute quota that no amount of client hardware raises; and caches that make inference cheap
(KV and prefix caches) are local to one replica, so spreading traffic evenly destroys their hit rate.
Without a scalability model, teams discover these walls during a launch, after adding hardware that
did not help.

## How it works
### Vertical vs horizontal
**Scaling up** (bigger GPU, more memory, tensor parallelism across GPUs in one node) raises what one
replica can do and is often the only option for a model that does not fit smaller hardware. It has a
ceiling and usually a price per unit that rises with size. **Scaling out** (more replicas behind a load
balancer) raises total throughput and fault tolerance, but only for work that can be partitioned and
only until a shared component saturates. For LLM serving, tensor or pipeline parallelism is scale-up of
one model instance; data-parallel replicas are scale-out.

### Amdahl and the Universal Scalability Law
Amdahl's law: speedup(N) = 1 / ((1 − p) + p/N), with p the parallel fraction. The ceiling is
1/(1 − p): if 5% of each request's time is spent holding one shared lock or primary, no fleet size gets
past 20×, and 64 workers already give only about 15×. Gunther's Universal
Scalability Law (USL) adds coherency:

```
C(N) = N / (1 + α(N − 1) + β·N(N − 1))
```

α is contention (queueing on a shared resource), β is coherency (pairwise coordination). With β = 0
this is exactly Amdahl rewritten (α = 1 − p), and capacity flattens toward 1/α. With β > 0,
capacity peaks at N* = √((1 − α)/β) and declines after. The value of the model is diagnostic: fit α and
β from a few load-test points, and they tell you whether to remove a serialization point (α) or reduce
cross-node chatter (β), and roughly where adding nodes stops paying.

### Where AI systems stop scaling
- **Batch is the first axis, inside one replica**: with continuous batching, a replica's throughput
  rises steeply with concurrent sequences, because each weight read from memory is shared by the whole
  batch, while each request's per-token latency rises more slowly. The useful operating point is the
  largest batch that still meets the per-token latency target; adding replicas before reaching it
  buys GPUs you are not using.
- **GPU memory per replica**: concurrency per replica is bounded by KV-cache memory, so throughput per
  replica plateaus; you scale by adding replicas, not by pushing more requests into one.
- **Provider quotas**: tokens-per-minute and requests-per-minute limits per key, per model and per region
  form a hard ceiling; scaling past it means quota increases, more regions, or multiple providers.
- **State and affinity**: prefix caches and conversation state live on a replica. Pure round-robin loses
  the cache hit rate; affinity routing (by session or prefix hash) keeps it but makes load uneven.
- **Shared stores**: vector indexes, metadata databases, and the logging pipeline that records every
  prompt are classic α sources once the model tier scales.
- **Elasticity lag**: GPU nodes and model loads take minutes, so autoscaling on CPU utilization reacts
  too late. Scale on leading signals such as queue depth, in-flight requests or KV-cache utilization, and
  keep warm capacity for the time it takes a new replica to become ready.

### Designing for it
Keep the request tier stateless and push state into stores designed to scale; partition by tenant or
key where work is independent; make the serial parts small and fast; add backpressure so overload
produces fast rejection instead of collapse; and test scaling explicitly rather than assuming it.

## Example
A retrieval service is load-tested with N workers sharing one vector index and metadata database. The
measurements are illustrative; the fitting method is standard USL linearization.

```python
from math import sqrt

N = [1, 2, 4, 8, 16, 24]                 # workers
X = [40, 78, 148, 262, 410, 485]         # measured req/s

# USL: X(N) = X1*N / (1 + a(N-1) + b*N(N-1)).  Linearise: X1*N/X(N) - 1 = a(N-1) + b*N(N-1)
rows = [(n - 1, n * (n - 1), X[0] * n / x - 1) for n, x in zip(N, X)]
s11 = sum(u * u for u, v, y in rows); s12 = sum(u * v for u, v, y in rows)
s22 = sum(v * v for u, v, y in rows)
t1 = sum(u * y for u, v, y in rows); t2 = sum(v * y for u, v, y in rows)
det = s11 * s22 - s12 * s12              # 2x2 least squares, no intercept
a = (t1 * s22 - t2 * s12) / det
b = (s11 * t2 - s12 * t1) / det

usl = lambda n: X[0] * n / (1 + a * (n - 1) + b * n * (n - 1))
print(f"contention a={a:.3f} coherency b={b:.5f} peak near N={sqrt((1 - a) / b):.0f}")
for n in (16, 32, 48, 64, 96):
    print(f"N={n:3d} predicted {usl(n):4.0f} req/s efficiency {usl(n) / (X[0] * n):.0%}")
# contention a=0.026 coherency b=0.00069 peak near N=38
# N= 32 predicted 514 req/s ...  N= 64 predicted 473 req/s ...  N= 96 predicted 394 req/s
```

The fit says throughput tops out near 38 workers at roughly 520 req/s (per-worker efficiency is
already down to about a third), and doubling from 32 to 64
workers would *lower* throughput. Raising capacity here means attacking β (for example, sharding the
index so workers stop coordinating on one store) or α (caching hot metadata), not buying replicas.

## Trade-offs
- Scale-out buys fault tolerance and near-linear throughput for independent work, but adds
  coordination; it flips to a loss once coherency cost dominates, visible as a USL peak.
- Affinity routing preserves prefix and session caches, cutting cost and TTFT, but creates hot
  replicas; it stops paying when traffic is dominated by a few large tenants or sessions.
- Keeping warm GPU capacity hides cold-start minutes during spikes, but you pay for idle hardware; it
  is worth it when spikes are frequent and the latency SLO is strict.
- Sharding a shared store removes a contention point, but cross-shard queries and rebalancing add
  complexity; it is premature while the store is far from saturation.

## Pitfalls
- Load-testing one replica and multiplying by N; the symptom is a launch where adding replicas leaves
  throughput flat because a shared store or quota was the real limit.
- Autoscaling GPU replicas on CPU or GPU utilization; visible as replicas arriving after the spike has
  already caused timeouts, because model loading takes minutes.
- Round-robin load balancing across replicas with prefix caching; symptom is cache hit rate falling as
  replicas are added, and TTFT and cost per request rising with scale.
- Ignoring provider quotas in the scaling plan; seen as 429 errors at a traffic level far below what
  the application tier could handle.

## When to use
Use this reasoning before any launch, migration or "just add replicas" proposal. Run load tests at
several sizes (at least four or five points), fit a USL curve, and find which shared resource sets α
and β. Track per-replica throughput as you scale, cache hit rate, queue depth and quota headroom.
Do not invest in elaborate scale-out architecture for workloads that fit comfortably on one replica
with headroom; simplicity is cheaper. In interviews, naming the specific shared bottleneck and the
signal you would autoscale on beats generic "make it stateless and horizontal".

## Role lens
- **Engineer:** Writes stateless services, adds affinity routing and backpressure, and runs multi-size
  load tests so scaling claims come from measurements rather than assumptions.
- **Architect:** Decides partitioning keys, scale-up versus scale-out for models, multi-region and
  multi-provider strategy, and where shared state lives so it does not cap growth.
- **FDE:** Finds the customer-side ceilings, such as an on-prem search cluster or a single API key's
  quota, that will limit rollout long before the vendor's platform does.

## Check yourself
**Q:** Going from 8 to 16 inference replicas raised throughput by only 15%, and GPU utilization per
replica fell. What do you investigate, and in what order?
**A:** Falling per-replica utilization means replicas are waiting on something shared, so the GPU tier is
not the bottleneck. I would trace one request's critical path under load and look for the component
whose latency rose: a vector store or metadata DB (contention), a gateway or rate limiter, a provider
quota producing 429s, or connection pools sized for 8 replicas. I would also check load balancing:
round-robin can collapse prefix-cache hit rates, increasing prefill work. Then fit α and β from load
tests at several sizes to confirm whether the limit is a serial point or coordination cost before
redesigning.

## Quiz
### Q: In the Universal Scalability Law, what does a positive coherency coefficient β imply?
- [ ] Throughput grows linearly but with a lower slope
- [x] Throughput peaks at some N and then declines as more workers are added
- [ ] Throughput plateaus at 1/β and stays flat
- [ ] The system is limited by its serial fraction only
> β models pairwise coordination cost growing roughly with N², so beyond N* = √((1 − α)/β)
> each added worker costs more than it contributes. Contention alone (α) gives a plateau, not a decline.

### Q: Why is CPU utilization a poor autoscaling signal for GPU inference replicas?
- [ ] CPU metrics are not exposed inside containers
- [ ] GPU replicas never use the CPU
- [x] It does not track the real constraint, and new replicas take minutes to load, so scaling must react to leading signals like queue depth
- [ ] Autoscalers can only read GPU metrics
> Inference load shows up in queue depth, in-flight requests and KV-cache utilization. Because model
> loading is slow, a lagging signal adds capacity after users have already timed out.

### Q: A team adds replicas and sees prefix-cache hit rate fall. What is the most likely cause?
- [ ] Larger fleets use smaller cache sizes per replica by default
- [x] Requests sharing a prefix are spread across replicas, so each replica's local cache sees fewer repeats
- [ ] Prefix caching only works with a single replica
- [ ] More replicas increase prompt lengths
> Prefix caches are local to a replica. Without prefix- or session-aware routing, traffic spreads
> thinly, so each cache sees fewer repeats; affinity routing restores the hit rate.
