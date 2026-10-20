---
id: circuit-breakers
module: reliability
name: Circuit breakers
kind: pattern
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - reliability/bulkheads
  - reliability/fault-tolerance
  - reliability/backpressure-and-load-shedding
  - serving-inference/retry-strategies
  - serving-inference/timeouts
  - serving-inference/fallbacks
  - system-design/model-fallback-strategies
  - node-ts-for-ai/api-clients-with-retries-and-circuit-breakers
sources:
  - Nygard, 2007 — Release It!
  - Fowler, 2014 — CircuitBreaker (bliki)
  - Official docs: Resilience4j
---

## TL;DR
When an LLM provider, vector database or tool API starts failing or timing out, every caller that keeps
trying ties up threads and connections waiting for answers that will not come, and the retries make
recovery harder. A circuit breaker counts recent failures and, past a threshold, fails fast for a
cooling-off period, then lets a few trial calls through to test recovery. It trades a few wrongly
rejected requests for keeping the caller alive and giving the dependency room to heal.

## Why it exists
Cascading failure has a precise mechanism. Suppose an agent service calls a provider with a 30-second
timeout and handles 50 requests per second. The provider degrades to 100 percent timeouts. Each
in-flight call now holds a worker and a connection for the full 30 seconds, so by Little's law the
service accumulates 50 × 30 = 1,500 stuck calls, far more than any sensible pool. Within a minute the
pool is exhausted and the service cannot answer requests that never needed the provider at all: health
checks fail, the orchestrator restarts pods, and a partial outage upstream becomes a total outage here.
Meanwhile every client retry doubles the load on a dependency that is struggling precisely because it is
overloaded.

The obvious mitigations are each insufficient on their own. Timeouts bound a single call but not the
aggregate; 1,500 stuck calls at 30 seconds each is the result *with* timeouts. Retries with backoff make
the aggregate worse during an outage, because they multiply attempts exactly when attempts are
worthless. Waiting for the provider to fix itself is not a strategy when your own service dies first.

The breaker is the only one of these that does two things at once: it stops consuming the caller's
resources immediately, because rejected calls return in microseconds rather than seconds, and it
removes load from the dependency so that whatever is wrong there has a chance to clear. The name comes
from electrical breakers, which exist for the same reason: when a fault draws too much current, the
right response is to open the circuit, not to keep supplying power and hope.

## How it works
### Three states
A breaker is a small state machine wrapped around a call.

1. **Closed.** Calls flow through. Every outcome is recorded in a sliding window, either the last N calls
   or the last T seconds. If the failure ratio in the window crosses a threshold *and* the window holds
   at least a minimum number of calls, the breaker trips. The minimum exists because one failure out of
   one call is a 100 percent failure rate and should not trip anything.
2. **Open.** Every call is rejected immediately with a distinct error (`CircuitOpen`), no network
   involved, for a reset interval. This is where resources are freed and the dependency gets relief.
3. **Half-open.** When the interval elapses, a limited number of trial calls are allowed. If they succeed
   the breaker closes and the window is cleared; if any fails it reopens and the timer restarts. The
   limit matters because if every waiting caller rushed in on the first tick of recovery, a dependency
   that is only partly healed would be knocked down again.

### What counts as a failure
Timeouts, connection errors and 5xx responses are failures: they describe the dependency's health. A
429 is usually treated as a failure for tripping purposes because continuing to call a rate-limited
endpoint helps nobody, though its `Retry-After` should also inform the reset interval. 4xx client errors
such as a malformed request or an invalid schema are *not* failures of the dependency; they are your bug,
and counting them opens the breaker on a healthy provider. For LLM traffic add one more category: slow
success. A provider answering correctly in 25 seconds is, for an interactive product, down. Libraries
such as Resilience4j expose a slow-call-rate threshold for exactly this; if yours does not, a tight
timeout turns slowness into a countable failure.

### Choosing thresholds
A workable starting point is a window of 50 to 100 calls, a minimum of 20, trip at 50 percent failures,
reset after 30 seconds, and 3 half-open probes. The reasoning: the window should be large enough that a
single burst of errors does not trip it, the minimum large enough to be statistically meaningful at your
traffic level, the ratio high enough that ordinary error rates (a few percent) never approach it, and
the reset interval short enough that recovery is noticed within a minute. Low-traffic dependencies need
longer time-based windows; at two calls a minute a 20-call minimum would take ten minutes to trip.

### Granularity
One breaker per failure domain, not one per service name. A provider's `model-a` deployment in one
region can be failing while `model-b` is fine, so key the breaker by provider × model × region, and tool
APIs by endpoint. A single "the provider" breaker either trips on partial failures and takes healthy
paths with it, or never trips because healthy traffic dilutes the ratio.

### What happens when it is open
A breaker without a fallback converts slow failures into fast failures. That is already worth having,
since it keeps the service and its health checks alive, but the user experience is rescued by what you do
with `CircuitOpen`: route to a secondary provider or smaller model, return a cached or degraded answer,
or enqueue the work for later and say so. The breaker is the detector; the fallback is the recovery.

### Composition with retries, timeouts and bulkheads
A common arrangement, and the one Resilience4j documents as its default, puts retry outermost, then the
breaker, then the timeout around the actual call. Each retry attempt is then counted by the breaker, and
once the breaker opens the remaining attempts short-circuit instantly, provided the retry policy treats
`CircuitOpen` as non-retryable. Put the retry *inside* the breaker and each counted "call" takes several
timeouts to register, delaying the trip while multiplying load. A bulkhead (a concurrency cap per
dependency) sits alongside: it bounds how many callers can be stuck while the breaker is still
accumulating its minimum calls.

## Example
A compact asyncio breaker with a count-based window, a minimum-calls guard, a capped number of half-open
probes, and a fallback at the call site. It is single-process; a fleet shares breaker state through a
library or a small Redis record, but the state machine is the same.

```python
import asyncio, collections, time

class CircuitOpen(Exception): pass
class UpstreamError(Exception): pass          # 5xx / connection-level failures from the provider

class CircuitBreaker:
    def __init__(self, window=50, min_calls=20, fail_ratio=0.5, reset_after=30.0, probes=3):
        self.window, self.min_calls, self.fail_ratio = window, min_calls, fail_ratio
        self.reset_after, self.probes = reset_after, probes
        self.results = collections.deque(maxlen=window)    # True = failed call
        self.state, self.opened_at, self.probe_ok, self.probe_inflight = "closed", 0.0, 0, 0

    def _allow(self):
        if self.state == "open":
            if time.monotonic() - self.opened_at < self.reset_after:
                return False                                # fail fast: no network call at all
            self.state, self.probe_ok, self.probe_inflight = "half_open", 0, 0
        if self.state == "half_open":
            if self.probe_inflight >= self.probes:
                return False                                # only a few trial calls at a time
            self.probe_inflight += 1
        return True

    def _trip(self):
        self.state, self.opened_at = "open", time.monotonic()

    def _record(self, failed):
        if self.state == "half_open":
            self.probe_inflight -= 1
            if failed: self._trip(); return                # still broken: reopen, timer restarts
            self.probe_ok += 1
            if self.probe_ok >= self.probes:                # enough evidence of recovery
                self.state, self.results = "closed", collections.deque(maxlen=self.window)
            return
        self.results.append(failed)
        n = len(self.results)
        if n >= self.min_calls and sum(self.results) / n >= self.fail_ratio:
            self._trip()

    async def call(self, fn, *args, timeout=10.0, **kw):
        if not self._allow():
            raise CircuitOpen("dependency marked unhealthy")
        try:
            result = await asyncio.wait_for(fn(*args, **kw), timeout)
        except (asyncio.TimeoutError, ConnectionError, UpstreamError):
            self._record(True); raise                       # 4xx client errors are deliberately not counted
        self._record(False)
        return result

breaker = CircuitBreaker()

async def answer(prompt):
    try:
        return await breaker.call(primary_llm, prompt, timeout=20)
    except CircuitOpen:
        return await secondary_llm(prompt)                   # other vendor, smaller model, or cached reply
```

The timeout lives inside `call` so that slow responses become failures the window can see. Emit a metric
on every state transition; a breaker that opens silently is an outage nobody is looking at.

## Trade-offs
- Aggressive thresholds protect the caller quickly but trip on transient blips and reject requests that
  would have succeeded; the balance shifts toward longer windows and higher minimums as traffic per
  breaker falls.
- Fine-grained breakers (per model, per region, per endpoint) isolate precisely, but each partition may
  never reach its minimum calls; coarse breakers trip reliably yet take healthy sub-dependencies down
  with the sick one. Partition only as finely as your traffic supports.
- A short reset interval notices recovery fast but re-hammers a dependency that is still down; a long one
  keeps you on the fallback longer than needed. The better your fallback, the longer you can afford to
  wait.
- Local per-process breakers need no coordination, but every replica discovers the outage independently
  and small replicas may never accumulate enough calls; shared state fixes that at the cost of a network
  hop and a new dependency on the store.

## Pitfalls
- Counting 4xx client errors as failures. The symptom: the breaker opens after you ship a bad prompt
  template or schema change while the provider's status page is green, and the fallback quietly serves
  degraded answers.
- Omitting the minimum-calls guard. The symptom: open/closed flapping in the state metric overnight, when
  traffic is low and a single failure is a 100 percent rate.
- Retrying across an open breaker. The symptom: latency does not drop when the breaker opens because the
  retry policy keeps re-raising `CircuitOpen` with backoff, and logs fill with rejections.
- Ignoring slow success. The symptom: worker and connection pools exhaust during a latency incident while
  the breaker stays closed because nothing returned an error.
- Opening without alerting or fallback. The symptom: a sudden wall of fast 503s that nobody connects to
  an upstream problem, because the service's own error rate looks like the cause.
- Letting every replica probe at the same instant. The symptom: a dependency that recovers for ten
  seconds and collapses again on each reset tick; add jitter to the reset interval.

## When to use
Wrap any synchronous call to a dependency that can fail slowly and whose failure you can do something
about: provider and model endpoints, tool and partner APIs, vector stores, anything over a network. Do
not bother for in-process calls, and think twice when there is no fallback and the request genuinely
must wait; there, a tight timeout plus a bounded queue is more honest than a breaker that rejects work
you will have to redo. Measure state transitions, rejected-call counts, time spent open, and fallback
rate, and alert when a breaker has been open longer than its reset interval several times running. In
interviews the expected depth is the three states, why half-open exists, what should and should not count
as a failure, and how the breaker composes with timeouts, retries and bulkheads.

## Role lens
- **Engineer:** Wraps every provider, model and tool client in breaker plus timeout plus fallback, keys
  breakers by failure domain, and emits state-transition metrics.
- **Architect:** Defines the failure domains that get their own breaker, the fallback policy for each
  dependency, and demands the cascading-failure walk-through in design reviews.
- **FDE:** In a customer's environment the flaky dependencies are their own systems (CRM, ticketing,
  identity); the breaker keeps the agent responsive and the FDE explains why it said "tool unavailable"
  rather than hanging.

## Check yourself
**Q:** Your agent calls a customer's ticketing API. At their peak hour the API's p99 climbs to 40 seconds
without returning errors, your breaker (set to open at 50 percent failures) never opens, and your
service's worker pool exhausts. What is wrong, and how do you fix it?
**A:** The breaker only sees errors, and the failure mode here is slow success, so from its point of view
nothing is failing. Fix it in layers. Put a timeout appropriate to the product, perhaps 5 seconds, inside
the breaker so slow calls become countable failures, or enable a slow-call-rate threshold if the library
has one. Add a bulkhead that caps concurrent calls to that API, so even before the breaker trips the
stuck calls cannot take more than, say, 20 of 200 workers. Check the minimum-calls setting against this
API's traffic so the trip is reachable. Then give `CircuitOpen` a fallback: queue the ticket write for
asynchronous retry and tell the user it has been queued, rather than failing the whole turn. Finally,
verify with metrics: the breaker should now open within a minute of the latency spike and worker-pool
utilization should stay flat.

## Quiz
### Q: What is the purpose of the half-open state?
- [ ] To let all queued requests through at once when the reset interval ends
- [x] To test recovery with a limited number of trial calls without flooding a dependency that may still be unhealthy
- [ ] To count client errors separately from server errors
- [ ] To hold requests in memory until the dependency recovers
> If every waiting caller rushed in at the first sign of recovery, a partly healed dependency would be
> knocked down again. A few probes give evidence of recovery cheaply; one failure reopens the breaker.

### Q: Which outcome should NOT count toward tripping a breaker on an LLM provider call?
- [ ] A request that exceeded its timeout
- [ ] A 503 Service Unavailable response
- [x] A 400 Bad Request caused by a malformed payload from your own code
- [ ] A connection refused error
> The breaker measures the dependency's health. A 400 from your own bad request says nothing about the
> provider, and counting it would open the circuit, and switch to a fallback, while the provider is fine.

### Q: A team places a 3-attempt retry policy inside the breaker (breaker outermost, retries inside). During an outage, what is the effect?
- [ ] Nothing changes; the order of resilience wrappers is irrelevant
- [ ] The breaker trips faster because each attempt is counted
- [x] Each counted call takes roughly three timeouts to register as a failure, delaying the trip while retries triple load on the failing dependency
- [ ] The retries are skipped automatically once the first attempt fails
> With retries inside, the breaker records one outcome per request, and only after all three attempts
> have timed out, so it reaches its minimum-calls threshold a third as fast as when every attempt is
> counted, and until it trips every request keeps sending three attempts to the dependency. With retry
> outside and `CircuitOpen` non-retryable, each attempt is counted, the trip comes sooner, and the
> remaining attempts short-circuit instead of reaching the network.
