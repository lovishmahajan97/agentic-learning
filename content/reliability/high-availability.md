---
id: high-availability
module: reliability
name: High availability
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - reliability/fault-tolerance
  - reliability/multi-region-architecture
  - reliability/disaster-recovery
  - reliability/slas-slos
  - reliability/backpressure-and-load-shedding
  - deployment/health-checks
  - system-design/model-fallback-strategies
  - fastapi-for-ai/graceful-shutdown-and-worker-draining
sources:
  - Beyer et al., 2016 — Site Reliability Engineering
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Nygard, 2018 — Release It! (2nd ed.)
---

## TL;DR
High availability (HA) is the property that a system keeps answering requests through the failure of
any single component, measured as the fraction of time or requests for which it works. You get it by
removing single points of failure with redundancy, detecting failures quickly with health checks, and
moving traffic automatically (failover). It costs money and complexity because every redundant part
must be kept consistent and exercised, and an AI product's availability is capped by its least
available dependency, which is usually the model provider.

## Why it exists
A single server is a single point of failure: one disk, one kernel panic, one bad deploy, one
availability-zone incident, and the product is down. The naive answer, "buy a more reliable machine",
fails on arithmetic. A system is a chain of components, and if each one must be up for a request to
succeed, the chain's availability is the *product* of the parts: three dependencies at 99.9% each give
0.999³ ≈ 99.7%, roughly 26 hours of downtime a year instead of 9. Adding components only makes this
worse, and modern systems have dozens.

Hardware is also not the main enemy. Most outages in mature organisations are triggered by change:
deploys, configuration pushes, schema migrations, certificate rotations. The Google SRE book puts the
share of outages caused by changes to a live system at roughly 70%. A perfect machine does nothing for
that; what helps is being able to take any one instance out of service, including on purpose, without
users noticing. That is the real definition of HA in practice: *no single component is required*.

For AI systems the problem is sharper. Your request path includes an external model API with its own
incidents, rate-limit spikes and regional capacity limits, plus GPU hosts that have more things to fail
than a commodity CPU box. If the provider is 99.9% and you promise 99.95%, you have promised something
you cannot deliver, no matter how many replicas of your own code you run.

## How it works
### Measuring it
Availability is either time-based (`uptime / total time`) or request-based
(`successful requests / total requests`). Request-based is what users feel and what SLOs usually use,
because a 10-minute outage at 3 a.m. and one at peak are not the same. The "nines" convert directly
to a yearly budget (8,760 hours):

```
99%     → 87.6 h/year     99.9%  → 8.76 h/year
99.99%  → 52.6 min/year   99.999% → 5.26 min/year
```

Two composition rules do most of the reasoning. Components in **series** (all must work) multiply:
`A = A₁ × A₂ × …`. Components in **parallel** (any one suffices, with perfect failover and independent
failures) compose as `A = 1 − (1 − A₁)(1 − A₂)…`, so two 99% replicas give 99.99%. The parallel formula
is wildly optimistic whenever failures are correlated, and they usually are: both replicas run the same
code, received the same deploy, sit in the same zone, and call the same provider.

A third formula explains where the effort should go. For a single component,
`A = MTBF / (MTBF + MTTR)`: mean time between failures over the whole cycle including mean time to
repair. A component that fails once a month (MTBF ≈ 730 h) and takes 45 minutes to recover is about
99.9% available; cutting recovery to 4.5 minutes makes it about 99.99% without failing any less often.
That is the arithmetic behind HA's emphasis on fast detection and automatic failover: making failures
rare is hard and slow, making them short is engineering you control.

### Removing single points of failure
- **Stateless service tier.** Run at least two instances behind a load balancer, spread across
  availability zones. Statelessness matters *because* it makes any instance interchangeable; session
  state, conversation history and job progress move to replicated stores.
- **Replicated state.** A database primary with a synchronous or asynchronous replica and automatic
  promotion; a vector store with replicas; a cache that the service can live without.
- **Redundant plumbing.** Managed load balancers and DNS are themselves redundant; a self-managed
  single nginx box in front of a redundant fleet quietly undoes the fleet.
- **Capacity headroom.** N+1 (or N+2) sizing, *because* a surviving replica that is already at 80%
  cannot absorb its dead sibling's traffic; redundancy without headroom just moves the outage.

### Detecting failure fast
Automatic failover is only as fast as detection. A health check that probes every 10 s and requires
3 failures declares death after ~30 s, and that interval is pure downtime for traffic routed to the
dead node. Checks must test the ability to serve, not just the ability to answer HTTP: a model server
whose GPU is wedged will happily return 200 from `/health` while every real request times out. The
standard split is a cheap *liveness* probe (restart me) and a *readiness* probe that reflects
dependencies and warm-up (send me traffic). Readiness is what keeps a freshly started model server,
still loading weights, out of rotation. The dependency part needs care: readiness should reflect what
*this* instance can fix by being removed. If every replica's readiness checks the same shared model
provider, a provider blip marks the whole fleet unready at once and the load balancer has nowhere to
send traffic, turning a degraded dependency into a total outage. Shared dependencies belong in
circuit breakers and fallbacks, not in the probe that removes instances.

### Failover and its hidden costs
**Active-passive** keeps a standby that takes over; failover costs detection time plus warm-up, and a
cold standby that has to load a 70B-parameter model from object storage can take minutes to be
useful. **Active-active** has every node serving all the time, so a failure merely reduces capacity,
and it doubles as proof that the second node actually works. In both cases in-flight work is lost:
a streaming generation dies with its node, so clients need retry logic and, for long agent runs,
durable checkpoints.

### Stateful failover is where HA gets hard
Stateless replicas are interchangeable; a database primary is not. Promoting a replica raises two
questions the stateless tier never asks. **What is lost?** An asynchronous replica trails the primary,
so promotion discards whatever had not yet replicated; a synchronous replica loses nothing but adds a
round trip to every commit. **Is the old primary really dead?** If it was only slow or partitioned and
keeps accepting writes after the promotion, you have two primaries (split brain). Managed databases
solve this with a single arbiter and fencing, which is a large part of what you pay them for; a
self-built failover script that promotes on a missed heartbeat usually does not.

### Change as the biggest availability risk
Because changes cause most outages, HA includes the deployment process: rolling updates that drain
in-flight requests (the drain timeout must exceed your longest generation), canaries that take a
fraction of traffic first, flags that can disable a feature without a deploy, and rollbacks that take
seconds. A system that is redundant at runtime but deploys to all replicas at once is not HA.

### Where AI systems differ
Model providers are a shared dependency, so multi-provider fallback raises availability only if your
prompts and evals are portable and you accept that the fallback model gives different answers:
"available but different" is a product decision, not just an infrastructure one. GPU capacity is
expensive enough that N+1 redundancy may be replaced by graceful degradation to a smaller model, and
for self-hosted serving the readiness probe should include a real tiny inference.

## Example
The script composes the availability of a chat product from its parts and shows how adding a second
model provider moves the bottleneck rather than removing it. All inputs are illustrative targets, not
measured figures, and the parallel rule assumes independent failures and perfect failover.

```python
from functools import reduce

def series(*parts):        # every part must be up
    return reduce(lambda acc, a: acc * a, parts, 1.0)

def parallel(*parts):      # up if any part is up (independent failures, perfect failover)
    return 1 - reduce(lambda acc, a: acc * (1 - a), parts, 1.0)

def downtime_hours_per_year(a):
    return (1 - a) * 8760

lb, api_replica, vector_db, provider = 0.9999, 0.999, 0.9995, 0.999

single = series(lb, parallel(api_replica, api_replica), vector_db, provider)
dual   = series(lb, parallel(api_replica, api_replica), vector_db, parallel(provider, provider))

for label, a in (("one provider", single), ("two independent providers", dual)):
    print(f"{label}: {a:.4%}  ->  {downtime_hours_per_year(a):.1f} h/year")
# one provider:               99.8400%  ->  14.0 h/year   (provider dominates)
# two independent providers:  99.9398%  ->   5.3 h/year   (vector DB now dominates)
```

With one provider the whole product is pinned near 99.84% even though the API tier is 99.9999%. Adding
a second provider lifts it to ~99.94%, and now the single vector database is the weakest link, which
is exactly where the next engineering dollar should go. If the two providers share a failure mode
(same upstream GPU cloud, your own shared gateway), the real number sits between the two lines.

## Trade-offs
- Every extra nine roughly multiplies cost and complexity, so a 99.9% target with fast rollback is often
  the right call for internal tools; it flips to 99.99%+ when revenue or safety rides on each request.
- Active-active uses the standby capacity all the time and proves it works, but forces you to solve
  state sharing between nodes; active-passive is simpler and wastes the standby until the day it fails.
- Multi-provider fallback raises model availability only when prompts, tool schemas and evals are
  portable; if outputs must be byte-stable, the fallback is a degraded mode, not a transparent one.
- Aggressive health checks fail over faster but misdiagnose slow nodes as dead under load, which
  turns a latency problem into a capacity problem as traffic piles onto fewer survivors.

## Pitfalls
- Counting redundancy that shares a failure mode: two replicas in one zone, two providers behind one
  gateway, two databases on one deploy pipeline. The symptom is "redundant" components failing together.
- Health checks that test the process, not the service; the node stays in rotation while every real
  request fails, and the load balancer keeps sending it traffic.
- The opposite error: readiness probes that include a shared dependency such as the model provider or
  database, so one dependency hiccup marks every replica unready together and the load balancer
  returns errors from an empty pool even though your own code was fine.
- No headroom: N replicas each at 80% load, so losing one overloads the rest and the "failover" is a
  cascading failure. Visible as a second outage minutes after the first node died.
- Promising an SLA above the provider's: the math makes it impossible, and the symptom is a chronic
  error-budget deficit nobody can engineer away.
- Forgetting that deploys are downtime: rolling restarts that kill in-flight generations show up as
  a spike of client errors at every release, not as an "outage".

## When to use
Decide the target before the design. Ask what one hour of downtime costs, convert the answer into a
number of nines, and then pay for only that: an internal retrieval tool may be fine at 99.5% with a
15-minute rollback, while a customer-facing assistant inside a checkout flow needs multi-zone
active-active and multi-provider fallback. Measure availability the way users experience it
(successful requests, by endpoint, by tenant), not by host uptime. Day to day this shows up as
readiness probes, drain timeouts, N+1 capacity reviews and the question "what happens if this box
disappears right now?" in design reviews. In interviews, expect to compose availabilities, explain why
the provider caps yours, and distinguish HA (surviving a component failure with no visible downtime)
from disaster recovery (restoring service after a site or data loss, with a measured gap).

## Role lens
- **Engineer:** Implements readiness probes that run a real inference, retry-with-fallback on provider
  errors, and drain timeouts longer than the maximum generation time.
- **Architect:** Sets the availability target per surface, owns the dependency availability math, and
  decides between active-active, active-passive and degradation-instead-of-redundancy for GPU tiers.
- **FDE:** Translates a customer's "five nines" request into the provider's actual SLA and a
  multi-provider or self-hosted design, and documents the residual gap honestly.

## Check yourself
**Q:** A team promises 99.95% availability for an assistant whose path is: global load balancer
(99.99%), three stateless API replicas across zones (99.9% each), one managed vector database
(99.95%), and a single model provider (99.9%). Can they hit the target, and what are the first two
changes you would make?
**A:** No. The three replicas in parallel are effectively 100%, but the series product
0.9999 × 0.9995 × 0.999 ≈ 99.84%, roughly 14 hours a year, is already below target before counting
deploys and their own bugs. The provider is the dominant term, so the first change is an independent
second provider with portable prompts and evals, lifting the model term toward 99.9999% and the product
to about 99.94%. The vector database then dominates; the second change is either a replica with
automatic failover or a degraded mode (answer without retrieval, flagged) so a vector outage is not a
product outage. A strong answer adds that correlated failures make these numbers optimistic and that
the target should be renegotiated unless both changes are funded.

## Quiz
### Q: Two API replicas each at 99.9% sit behind a load balancer. Why is the combined availability usually worse than the 99.9999% the parallel formula predicts?
- [ ] The load balancer halves the effective capacity of each replica
- [x] Their failures are correlated: same deploy, same zone, same dependencies, so they fail together
- [ ] Parallel composition only applies to databases, not stateless services
- [ ] Health checks cannot distinguish between two identical replicas
> The parallel formula assumes independent failures. Replicas built from the same code and deployed
> together share most failure modes, so the real figure sits far closer to a single replica's.

### Q: A model server loads weights for three minutes after start-up. Which probe design keeps it from receiving traffic too early without causing restart loops?
- [ ] A single liveness probe with a 10-second timeout
- [ ] No probes; the load balancer retries failed requests automatically
- [x] A cheap liveness probe plus a readiness probe that only passes once a test inference succeeds
- [ ] A readiness probe that checks the process is listening on its port
> Readiness gates traffic, liveness gates restarts. A liveness probe that fails during warm-up would
> kill the pod repeatedly; a port check passes before the model can actually answer.

### Q: What distinguishes high availability from disaster recovery?
- [x] HA masks single-component failures with no visible downtime; DR restores service after a site or data loss, accepting a measured gap
- [ ] HA applies to stateless services and DR applies only to databases
- [ ] HA is about hardware failures while DR is about software bugs
- [ ] They are the same practice measured over different time windows
> HA keeps the system up through routine faults using redundancy and failover. DR plans for the
> cases redundancy cannot cover, such as a region loss or corrupted data, and is measured by RPO and RTO.
