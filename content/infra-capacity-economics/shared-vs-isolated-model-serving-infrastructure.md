---
id: shared-vs-isolated-model-serving-infrastructure
module: infra-capacity-economics
name: Shared vs isolated model-serving infrastructure
kind: decision
difficulty: core
est_minutes: 15
related:
  - infra-capacity-economics/inference-clusters
  - infra-capacity-economics/cost-allocation-by-tenant-and-feature
  - system-design/multi-tenancy
  - system-design/tenant-isolation
  - reliability/tenant-level-quotas-and-noisy-neighbor-isolation
  - reliability/bulkheads
  - serving-inference/continuous-batching
  - safety-security/cross-tenant-data-leakage
sources:
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Sheng et al., 2023 — S-LoRA: Serving Thousands of Concurrent LoRA Adapters
  - Official docs: vLLM
---

## TL;DR
Shared serving puts every tenant's or team's traffic through one pool of model replicas; isolated
serving gives a tenant its own replicas, nodes, cluster or account. Sharing wins on cost because
continuous batching needs many concurrent requests to fill a GPU and because tenants' peaks rarely
coincide. Isolation wins when a tenant can hurt others (noisy neighbour), must be protected from
others (compliance, data separation) or needs a different model configuration. The decision is a
spectrum, not a switch, and most platforms end up with both.

## Why it exists
A GPU serving an LLM is efficient only when busy. Continuous batching reaches its throughput at
tens of concurrent sequences; at two or three the GPU spends most of each decode step reading weights
for almost no output, so the cost per token can be an order of magnitude higher than at full batch.
A tenant whose traffic peaks at five concurrent requests cannot fill a replica on their own. Pooling
many such tenants fills it, and because their peaks fall at different times, the pool needs capacity
for the peak of the sum, which is much less than the sum of the peaks. That is the economic pull
toward sharing, and it is strong: for a platform serving dozens of internal teams, dedicated
replicas per team can multiply the GPU bill several times for the same total tokens.

The naive conclusion, "share everything", fails the first time one tenant submits a batch job of
50k-token documents. Those requests fill the KV cache, every other tenant's TTFT jumps, and the
platform has no way to say whose fault it is or to stop it, because the engine's scheduler is
first-come-first-served and knows nothing about tenants. It fails again when a regulated tenant asks
whether their prompts ever shared a GPU with another company's, or when a tenant needs a different
model, a longer context limit, a fine-tuned adapter or a different region. Sharing without
isolation mechanisms is a cost optimisation that quietly removes fairness, blast-radius control and
contractual guarantees. The decision therefore is which of those properties each tenant actually
needs, and the cheapest level of isolation that provides them.

## How it works
### The isolation spectrum
From cheapest to most isolated, each level buys a specific guarantee:

1. **Shared pool, no tenant awareness.** Maximum utilization, no guarantees. Acceptable only for
   one team's own traffic or a prototype.
2. **Shared pool with logical isolation.** Per-tenant rate limits and token budgets at the
   gateway, priority classes, fair-share admission so one tenant cannot hold more than a share of
   in-flight sequences, and per-tenant metrics and chargeback. This buys fairness and attribution
   while keeping the batching economics. It exists *because* the engine scheduler is FCFS; fairness
   must be enforced before requests reach it.
3. **Reserved capacity inside a shared cluster.** A tenant gets a guaranteed minimum (their own
   replicas, or a reserved share of the pool) and may burst into the shared remainder. This is the
   self-hosted analogue of what hosted providers sell as provisioned throughput next to a
   pay-as-you-go pool.
4. **Dedicated replicas or node pool.** Separate engine processes, possibly separate GPUs, so a
   tenant's KV cache and queue are physically theirs. Buys latency isolation and lets the tenant run
   a different model, context limit or quantization. Costs idle capacity whenever the tenant is quiet.
5. **Dedicated cluster, VPC or cloud account.** Separate control plane, network and identity. Buys
   the strongest compliance story (data residency, no shared control plane, independent blast
   radius) and the highest operating cost, because every platform component is duplicated.

### What is actually shared at each level
Sharing a *replica* means sharing GPU memory: tenants' KV caches sit in the same pool of blocks,
and the batch holds their sequences side by side. Within a correct engine, one sequence cannot read
another's cache, so this is not a data-leak path in itself; the risks are side-channel timing,
engine bugs and, more practically, prefix caching: if a cached prefix keyed only by its text could
be hit by another tenant's identical text, you have cross-tenant reuse of computation, not data,
but it still has to be explicitly decided and keyed. Sharing a *node* but not a replica removes
cache co-residency and leaves shared host RAM, network and the node's failure domain. Sharing a
*cluster* leaves shared control plane and shared operators. The compliance question is always "what
exactly is shared", and the answer should be precise, because "dedicated" is sold at every level.

### Multi-LoRA: sharing the base, isolating the behaviour
When tenants need customised models, the expensive choice is a dedicated replica per fine-tune. The
cheaper design keeps one shared base model and loads each tenant's LoRA adapter into the same
engine; the adapters are small, swapped per request, and the base weights are shared. This gives
behavioural isolation (each tenant's outputs come from their adapter) with shared economics. The
limit is that all tenants must agree on the base model, context limit and quantization.

### Logical isolation mechanics
At the gateway: per-tenant token-bucket limits in requests and tokens per minute, a concurrency cap
(in-flight sequences per tenant), and a priority tag. At the pool: weighted fair queueing across
tenants before admission, so a tenant with a backlog of batch work cannot starve interactive users,
and the option to route a tenant's batch traffic to a separate low-priority pool. In observability:
every request carries a tenant id into metrics and traces so cost and tail latency can be attributed.
Without attribution you cannot even argue about whether a tenant deserves dedicated capacity.

### How the decision is made
Score each tenant on four questions. Does their traffic shape threaten others (long contexts,
bursty batch jobs)? Do they need guarantees others would violate (latency SLO, data separation,
region)? Do they need a different model configuration? Would their dedicated capacity sit idle most
of the day? Tenants with "yes" on the first three and "no" on the last get dedicated capacity; the
rest live in the shared pool behind logical isolation. The platform ends up with a shared pool plus
a short list of exceptions, each with a documented reason.

## Example
A worked comparison for a platform with ten internal product teams on one 70B model. Numbers are
illustrative and come from the kind of measurements you would make yourself.

Each team peaks at 20 concurrent sequences, but the peaks are spread over the day; the measured
peak of the combined traffic is 80 concurrent sequences, not 200. A replica holds 48 concurrent
sequences within the latency budget.

- **Isolated per team:** every team needs its own replica, since no team's traffic can be combined
  with another's. 10 replicas, each averaging perhaps 6 of 48 slots busy over the day.
  Utilization near 12%.
- **Shared with logical isolation:** peak of the sum is 80, so ceil(80 / 48) = 2 replicas, plus
  one for N+1 and headroom gives 3. Average utilization is several times higher, and the bill is
  about 30% of the isolated design for the same tokens.
- **Hybrid:** one team runs nightly document batches with 60k-token prompts. In the shared pool
  those jobs would fill the KV cache during the batch window. Give that team a dedicated replica
  for batch (or route their batch traffic to a low-priority pool), keep their interactive traffic
  in the shared pool. Total: 4 replicas, fairness preserved, the regulated team that asked for
  "no shared GPU" is still not satisfied and needs level 4 or 5 with a cost line attached.

The arithmetic that matters is peak-of-sum versus sum-of-peaks, and whether a tenant's shape
breaks the shared pool. Whenever the second is true, the first stops mattering for that tenant.

## Trade-offs
- Sharing raises utilization and lowers cost per token, but it removes latency isolation; the
  balance flips when one tenant's traffic shape (long prompts, batch bursts) dominates the pool.
- Logical isolation at the gateway preserves batching economics and gives fairness, but it cannot
  prevent cache contention once requests are admitted, so it does not satisfy hard latency SLOs.
- Dedicated replicas give predictable latency and freedom to change model configuration, but
  capacity sits idle outside the tenant's peak; it flips to worthwhile when the tenant's own traffic
  can fill a replica most of the day.
- Multi-LoRA sharing lets many customised models ride one base at shared cost, but constrains every
  tenant to the same base model, context and quantization, which breaks when one needs a different
  model.
- Dedicated clusters or accounts give the strongest compliance story, yet duplicate every platform
  component and operator burden; they are justified by contract or regulation, rarely by
  performance.

## Pitfalls
- Promising "dedicated" without stating the level; the symptom is a security review discovering the
  tenant's "dedicated" model shares a node, control plane or prefix cache with others.
- Running a shared pool without per-tenant concurrency caps; the symptom is platform-wide TTFT
  spikes that line up with one tenant's cron schedule.
- Keying a shared prefix cache on prompt text alone and not deciding whether cross-tenant hits are
  acceptable; the symptom is an auditor asking why tenant B's request was faster because tenant A had
  sent the same prompt.
- No tenant id in metrics; the symptom is a cost-allocation argument nobody can settle and an
  isolation decision made on anecdotes instead of load shape.
- Giving every tenant who asks their own replica; the symptom is a fleet averaging single-digit
  utilization and a GPU bill that scales with the number of customers rather than with tokens.
- Treating isolation as permanent; the symptom is a tenant that outgrew or shrank below the
  threshold still paying (or still being subsidised) a year later.

## When to use
Start shared with logical isolation: gateway limits, fair-share admission, per-tenant metrics. That
is the default for internal platforms and for SaaS tiers without hard SLOs. Move a tenant to
reserved or dedicated capacity when their traffic shape measurably damages others, when a contract
specifies latency or data-separation guarantees you cannot meet in the pool, or when they need a
different model configuration. Move to a dedicated cluster or account only for regulatory or
contractual reasons, and attach the cost to the contract. Measure per-tenant p95 TTFT, in-flight
sequences and tokens, and pool utilization; revisit each exception quarterly. In interviews, be
ready to explain why continuous batching makes sharing cheap and why that same batching makes
noisy neighbours hard to contain after admission.

## Role lens
- **Engineer:** Implements tenant-tagged rate limits, concurrency caps and fair-share queueing at
  the gateway, and ensures tenant ids flow into engine metrics and traces for attribution.
- **Architect:** Owns the isolation policy: which guarantees each tier of tenant gets, at which level
  of the spectrum, and the cost line that goes with every dedicated exception.
- **FDE:** Translates a customer's "we need our own model" into the precise isolation level they
  need, prices it, and configures reserved or dedicated capacity inside their environment.

## Check yourself
**Q:** A SaaS company serves forty customers from one shared vLLM pool. One enterprise customer
demands "dedicated infrastructure" after a week of slow responses. How do you decide what to give
them, and what would you check first?
**A:** First check whether the slowness is caused by them or by someone else: per-tenant p95 TTFT
and in-flight sequences over the week, correlated with pool-wide KV utilization. If another tenant's
batch jobs caused it, the fix is logical isolation for everyone (concurrency caps, fair-share
admission, a low-priority batch pool), which solves the enterprise customer's problem without
dedicated hardware. If their own traffic shape is the cause, or their contract names a latency SLO,
give them reserved capacity: a dedicated replica for their guaranteed minimum with burst into the
pool. Only if the demand is about data separation or residency do you move to a dedicated node
pool, cluster or account, and then the precise level of what is not shared goes into the contract
along with its cost. Dedicated capacity is reviewed against their utilization every quarter.

## Quiz
### Q: Why does a shared model pool usually cost far less per token than dedicated replicas per tenant?
- [ ] Shared pools can use cheaper GPUs because the model is loaded once
- [x] Continuous batching only reaches full efficiency at high concurrency, and tenants' peaks rarely coincide, so capacity is sized for the peak of the sum
- [ ] Dedicated replicas must each keep a separate copy of the training data
- [ ] Shared pools skip the prefill phase for every tenant after the first
> A GPU at low concurrency spends most of each decode step reading weights for little output.
> Pooling fills the batch and sizes capacity for combined peak rather than the sum of individual
> peaks, which is where the saving comes from.

### Q: Per-tenant rate limits and concurrency caps at the gateway are in place, yet one tenant's long-context jobs still slow others. Why?
- [ ] Rate limits only count requests, never tokens
- [ ] The gateway cannot see which tenant a request belongs to
- [x] Once admitted, sequences share the replica's KV cache and batch, so long contexts consume memory and bandwidth that gateway limits do not govern
- [ ] Long-context requests bypass the gateway and reach the engine directly
> Logical isolation controls admission, not what happens inside the batch; long sequences take many
> KV blocks and lengthen every step. Latency isolation needs separate replicas or routing long jobs
> to a separate pool.

### Q: Which situation most justifies a fully dedicated cluster or cloud account rather than dedicated replicas?
- [ ] The tenant's p95 latency target is tighter than the pool's
- [ ] The tenant wants a different context length limit
- [x] The tenant's contract requires data residency or no shared control plane
- [ ] The tenant's traffic is the largest in the pool
> Latency and configuration needs are met by dedicated replicas inside a shared cluster. A separate
> cluster or account is justified by what must not be shared at all: region, control plane,
> operators and network.
