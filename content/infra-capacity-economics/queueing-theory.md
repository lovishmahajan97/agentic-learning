---
id: queueing-theory
module: infra-capacity-economics
name: Queueing theory
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - infra-capacity-economics/capacity-planning
  - infra-capacity-economics/latency-budgets
  - infra-capacity-economics/scalability
  - serving-inference/batching-and-concurrency-limits
  - serving-inference/continuous-batching
  - reliability/backpressure-and-load-shedding
  - observability/latency-percentiles
sources:
  - Kleinrock, 1975 — Queueing Systems, Volume 1: Theory
  - Little, 1961 — A Proof for the Queuing Formula L = λW
  - Harchol-Balter, 2013 — Performance Modeling and Design of Computer Systems
---

## TL;DR
Whenever requests arrive at random and take a variable time to serve, they wait, and the wait grows
non-linearly as utilization approaches 100%: in the 16-slot example below, going from 75% to 95% busy
multiplies mean queue wait about twentyfold. Queueing theory gives you the formulas (Little's law, M/M/c, Kingman) to predict that knee
before production finds it, which is why you never plan LLM capacity at "average load fits".

## Why it exists
The naive capacity model is division: a replica handles 4 requests per second, peak traffic is 40, so
buy 10 replicas. That model assumes requests arrive evenly spaced and each takes exactly the average
time. Neither is true. Users arrive in bursts that no one coordinates, and LLM service times are
wildly variable because output length varies: one request generates 20 tokens, the next generates
2,000. When three long requests land together, everything behind them waits, and the server cannot
"bank" the idle time it had a minute earlier to pay for the burst now.

The consequence is that latency is not flat until capacity and then a cliff; it bends upward long
before 100% utilization. Teams that size to the mean see fine dashboards at 60% load and then
mysterious p99 explosions at 85%, with no code change. Queueing theory exists to quantify that curve:
how much waiting a given utilization buys, how variability makes it worse, and how pooling, admission
control and headroom make it better. It is also the bridge between the throughput numbers the serving
team measures and the latency numbers the product team promises. Without it you cannot answer "how
much headroom do we need" with anything better than a guess.

## How it works
### Vocabulary
- **λ (lambda)**: arrival rate, requests per second.
- **μ (mu)**: service rate of one server, so mean service time is 1/μ.
- **c**: number of servers (for an LLM, think *concurrency slots*, not machines; see below).
- **ρ = λ / (c·μ)**: utilization, the fraction of time servers are busy. Must stay below 1 or the
  queue grows without bound.
- **W, Wq, L**: mean time in system, mean time waiting in queue, mean number of requests in system.

### Little's law: L = λ·W
The one law that holds for almost any stable system, regardless of arrival or service distribution:
the average number of requests in flight equals the arrival rate times the average time each spends
inside. It holds *because* every request that enters eventually leaves, so long-run averages must
balance. It is the most useful formula in capacity work: at 50 req/s and 6 s mean latency, you have
300 requests in flight on average, so you need at least 300 concurrent slots across the fleet before
any headroom. It also runs backwards: if a gateway caps in-flight requests at L and latency is W, the
best throughput you can see is L/W.

### Why waiting explodes: the 1/(1−ρ) term
For the simplest model, one server with random (Poisson) arrivals and exponential service times
(M/M/1), mean time in system is W = 1/(μ − λ) and queue wait is Wq = ρ/(μ − λ). Rewrite it as
Wq = (service time) × ρ/(1 − ρ). At ρ = 0.5 you wait one service time; at 0.9, nine; at 0.99,
ninety-nine. The denominator is the reason headroom is not waste: the last 10% of utilization costs
more latency than the first 80%.

### Variability: Kingman's approximation
Real systems are not exponential. For a single server with general distributions (G/G/1), Kingman's
formula approximates the wait as:

```
Wq ≈ ( ρ / (1 − ρ) ) × ( (Ca² + Cs²) / 2 ) × mean_service_time
```

where Ca and Cs are the coefficients of variation (std dev / mean) of inter-arrival times and service
times. This splits the problem into three levers: utilization, *arrival* variability (bursts, retry
storms, batch jobs released on the hour) and *service* variability (output-length spread, tool calls,
cache hits vs misses). LLM workloads often have Cs around or above 1 (an exponential has exactly 1),
and agentic traffic with tool calls and long-tail outputs can push it higher; when Cs > 1 their queues
are worse than a textbook M/M/1 at the same utilization. Measure Cs from your own traffic rather than
assuming it. Capping `max_tokens` or splitting long jobs onto a separate queue lowers Cs and so reduces
latency for everyone else.

### Many servers: M/M/c and pooling
With c servers sharing one queue, the Erlang C formula gives the probability an arriving request must
wait and the mean wait. Two results matter in practice. First, larger pools tolerate higher
utilization at the same wait, *because* a burst is more likely to find some server free; this is
statistical multiplexing. Second, one shared queue beats several separate ones of the same total
capacity, because separate queues can have one pool idle while another is backed up. Every dedicated
per-tenant or per-model pool pays this tax.

### Mapping it onto LLM serving
An inference replica with continuous batching is not one server; it serves many sequences at once,
and each sequence slows slightly as the batch grows. A workable approximation is to treat each replica
as roughly `max_concurrent_sequences` servers whose service time is the measured latency *at that
concurrency*. The queue is wherever requests wait for a slot: the engine's waiting list, the gateway,
or a provider's rate limiter. Measure service time and its variance from real traffic, not from a
single-request benchmark, because per-request speed depends on load.

## Example
A fleet exposes 16 concurrency slots in total; a request holds a slot for 4 s on average. Erlang C
shows how waiting behaves as arrival rate rises, and what pooling buys. Exponential service is an
idealization; LLM traffic whose service-time variance exceeds the exponential's (Cs > 1) will wait
longer than this at every row.

```python
from math import factorial

def mmc(lam, mu, c):
    """M/M/c: returns (utilization, P(wait), mean queue wait Wq, mean time in system W)."""
    a = lam / mu                      # offered load, in busy servers
    rho = a / c
    if rho >= 1:
        return rho, 1.0, float("inf"), float("inf")
    top = a**c / factorial(c) / (1 - rho)
    p_wait = top / (sum(a**k / factorial(k) for k in range(c)) + top)   # Erlang C
    wq = p_wait / (c * mu - lam)
    return rho, p_wait, wq, wq + 1 / mu

mu = 1 / 4.0                          # 4 s mean service per slot
for lam in (2.0, 3.0, 3.5, 3.8, 3.95):
    rho, pw, wq, w = mmc(lam, mu, c=16)
    print(f"lam={lam}/s rho={rho:.2f} P(wait)={pw:.2f} Wq={wq:.2f}s L={lam*w:.1f}")
# lam=2.0/s rho=0.50 P(wait)=0.01 Wq=0.00s L=8.0
# lam=3.0/s rho=0.75 P(wait)=0.20 Wq=0.20s L=12.6
# lam=3.5/s rho=0.88 P(wait)=0.51 Wq=1.02s L=17.6
# lam=3.8/s rho=0.95 P(wait)=0.78 Wq=3.90s L=30.0
# lam=3.95/s rho=0.99 P(wait)=0.94 Wq=18.84s L=90.2

print(mmc(3.5, mu, 16)[2], mmc(1.75, mu, 8)[2])   # pooled 16 slots: ~1.0 s; split 2x8: ~2.5 s each
```

Read the output: raising load from 3.0 to 3.8 req/s (27% more traffic) raises mean queue wait from
0.2 s to 3.9 s, about 20×. Splitting the same 16 slots into two dedicated pools of 8 at the same total
traffic more than doubles the wait. L follows Little's law throughout.

## Trade-offs
- Running at high utilization lowers cost per request, but queueing delay grows as ρ/(1−ρ); the trade
  flips toward headroom as soon as a latency SLO, not throughput, is the binding requirement.
- Dedicated pools per tenant or model give isolation and predictable noisy-neighbour behaviour, but
  lose pooling efficiency; they win only when isolation or compliance is worth the extra capacity.
- Larger batches raise throughput per GPU but lengthen each request's service time, so the optimal
  concurrency cap depends on whether your SLO is on time-to-first-token or total completion time.
- Separating long jobs onto their own queue cuts service variability for interactive traffic, at the
  cost of a second pool that can sit idle; it pays off when output-length spread is large.

## Pitfalls
- Sizing capacity to mean traffic; the symptom is p99 latency that is fine at off-peak and spikes
  sharply during ordinary daily peaks with no deploy involved.
- Benchmarking service time with one request at a time; under real concurrency each request is slower,
  so the model underestimates ρ and the fleet saturates earlier than predicted.
- Letting clients retry immediately on timeout; retries raise λ exactly when the system is already
  saturated, visible as request rate climbing while successful completions stay flat.
- Treating an unbounded queue as safety; requests wait past their useful deadline, so the system does
  work nobody is waiting for, seen as high latency together with high client-side cancellation.

## When to use
Reach for queueing reasoning whenever someone proposes a utilization target, a replica count, a
concurrency cap or a per-tenant pool. Use Little's law first, because it needs no distribution
assumptions; use M/M/c or Kingman to sanity-check headroom, then validate with a load test that
replays real request-length distributions. Measure arrival rate, slot occupancy, queue depth and
queue wait separately from service time, since only the split tells you whether to add capacity or
reduce variability. In interviews, explaining why 90% utilization is dangerous, and deriving
in-flight concurrency from rate times latency, signals real production experience.

## Role lens
- **Engineer:** Sets engine concurrency caps and gateway queue limits, and instruments queue wait
  separately from model latency so saturation is visible before users report it.
- **Architect:** Chooses utilization targets, pooling versus per-tenant isolation, and admission
  control, using the utilization-latency curve to justify headroom in the capacity budget.
- **FDE:** Explains to a customer why their batch job at 9 a.m. slows the interactive assistant, and
  proposes spreading arrivals or a separate queue rather than buying more GPUs.

## Check yourself
**Q:** Your assistant runs at 60% average utilization with acceptable p99. Marketing plans a launch
that should add 40% traffic. Leadership asks whether latency will rise about 40%. What do you say?
**A:** No: queueing delay is non-linear. 60% × 1.4 is 84% utilization, and the ρ/(1−ρ) factor goes from
1.5 to about 5.25, so queue wait could rise three to four times even before accounting for bursts, and
LLM service-time variance makes it worse (Kingman). Service time itself may also lengthen if batches
grow. I would compute required in-flight slots with Little's law at the new rate, add capacity to keep
utilization near the current level, put a concurrency cap and fast rejection at the gateway, and
validate with a load test replaying real output-length distributions before launch.

## Quiz
### Q: A service handles 20 req/s with a mean latency of 3 s. How many requests are in flight on average?
- [ ] About 7, because 20 divided by 3 is roughly 7
- [x] 60, by Little's law L = λ·W
- [ ] It cannot be known without the service-time distribution
- [ ] 20, because that is the arrival rate per second
> Little's law holds for any stable system regardless of distributions: L = 20 × 3 = 60. The
> distribution matters for waiting time and tails, not for this average.

### Q: Two teams each run a dedicated pool of 8 identical slots at 85% utilization. What happens if they merge into one pool of 16?
- [ ] Waiting time is unchanged because total capacity and load are unchanged
- [ ] Waiting time doubles because each request now competes with twice as many others
- [x] Mean waiting time drops, because a burst in one team's traffic can use slots the other is not using
- [ ] Utilization falls to about 42% because the slots are now shared
> Pooling is statistical multiplexing: one shared queue never leaves a slot idle while requests wait
> elsewhere. Utilization stays 85%, but the probability and length of waits fall.

### Q: Kingman's approximation separates utilization from variability. Which change shrinks the variability term (Ca² + Cs²)/2 rather than utilization?
- [ ] Raising the batch size so each replica processes more tokens per step
- [x] Capping output length or routing long jobs elsewhere to reduce service-time variability
- [ ] Adding retries with no backoff so failed requests finish sooner
- [ ] Moving to faster GPUs so the mean service time drops
> Reducing service-time spread lowers Cs, so waiting falls even at the same utilization. Bigger
> batches and faster GPUs act on capacity and mean service time, i.e. on ρ, not on the variability
> term; immediate retries raise arrival burstiness (Ca) and make waiting worse.
