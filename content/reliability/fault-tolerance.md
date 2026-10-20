---
id: fault-tolerance
module: reliability
name: Fault tolerance
kind: concept
difficulty: core
est_minutes: 17
status: reviewed
related:
  - reliability/high-availability
  - reliability/circuit-breakers
  - reliability/bulkheads
  - reliability/fmea
  - serving-inference/retry-strategies
  - serving-inference/timeouts
  - system-design/graceful-degradation
  - agent-reliability/idempotent-tool-execution
sources:
  - Avižienis, Laprie, Randell, Landwehr, 2004 — Basic Concepts and Taxonomy of Dependable and Secure Computing
  - Gray, 1985 — Why Do Computers Stop and What Can Be Done About It?
  - Nygard, 2018 — Release It! (2nd ed.)
  - Beyer et al., 2016 — Site Reliability Engineering
---

## TL;DR
Fault tolerance is a system's ability to keep delivering correct, or explicitly degraded, service while
some of its components are failing. It combines redundancy with mechanisms that contain a fault before
it becomes a user-visible failure: timeouts, bounded retries, idempotency, circuit breakers, bulkheads,
fallbacks and replicated state. The design question is never "how do we avoid faults" but "which faults
do we tolerate, how do we detect them, and what does the system do in the window before recovery".

## Why it exists
Components fail at rates that cannot be engineered away. A fleet of 1,000 disks with a 1% annual
failure rate loses about ten a year; a service making 10 million remote calls a day at a 0.01% failure
rate sees a thousand failures daily. Distributed systems add a failure mode local code never has: a
remote call can be lost on the way out, lost on the way back, or merely slow, and the caller cannot
tell which. Jim Gray's analysis of Tandem outages found software and operations, not hardware, were the
dominant causes, which is why "buy better hardware" never solved the problem.

The naive alternative is `try: call() except: call()`. It fails in three ways. It retries faults that
retrying cannot fix (bad input, auth errors), it duplicates side effects when the first call actually
succeeded but the reply was lost, and under a real outage it multiplies load on the thing that is
already struggling, turning a partial failure into a total one. Retries without timeouts, budgets and
idempotency are not fault tolerance; they are fault amplification.

Agentic AI systems make this urgent. A 20-step agent whose steps each succeed 99% of the time finishes
cleanly only 0.99²⁰ ≈ 82% of the time. Most of the 18% are transient: a 429 from the provider, a tool
timeout, a malformed JSON response. Converting those into successes, without double-charging a card or
sending an email twice, is precisely the fault-tolerance toolkit applied to LLM calls and tools.

## How it works
### Vocabulary that keeps designs honest
Avižienis et al. separate **fault** (the underlying defect: a bug, a dead NIC, a provider incident),
**error** (the incorrect internal state it produces: a timed-out call, a null field) and **failure**
(the service no longer doing what it promised, as seen by users). Fault tolerance is the set of
mechanisms that stop the chain between error and failure. Naming which link you are breaking is the
difference between a design and a hope.

### Choose the fault model first
You tolerate what you design for. **Crash-stop**: a node dies and stays dead. **Crash-recovery**: it
comes back with or without its state, so you must handle duplicate work. **Omission and timing**: a
message or reply is lost or late, which is why every remote call needs a timeout, because "slow" is
indistinguishable from "dead" from the outside. **Byzantine** (arbitrary): a component returns
well-formed but wrong output, or different answers to different callers. Classic systems rarely
engineer against Byzantine faults because masking them is expensive: Byzantine agreement needs
3f + 1 replicas to tolerate f faulty ones, against 2f + 1 for crash faults. An LLM returning a
confident, schema-valid, wrong tool argument is not malicious, so strictly it is a value fault, but it
has the Byzantine property that matters: nothing about the reply looks broken. That is why output
validation against ground truth and approval gates belong in the same toolbox as retries.

### Masking versus recovery
There are two ways to stop an error becoming a failure. **Masking** hides it entirely: a replica
answers instead, or several components vote and the majority wins (N-modular redundancy; for LLMs,
sampling several answers and taking the consensus is the same idea, and it only helps when the
samples' errors are independent). **Recovery** lets the error happen and then repairs state, in one of
two directions. *Backward* recovery returns to a known-good earlier state and redoes the work:
checkpoint and restart, transaction rollback, replaying a workflow from its history. *Forward*
recovery moves to a new correct state without undoing: a compensating action (refund the charge,
retract the email), or a degraded answer. Side effects in the outside world usually cannot be rolled
back, which is why agent tools that act on real systems need forward recovery designed in.

### The toolbox, in the order you apply it
1. **Timeouts**, *because* a hung call holds a thread, a connection and often a GPU slot; nothing else
   in the list can act until the call returns. Derive them from the latency budget end to end, not
   per hop in isolation.
2. **Bounded retries with backoff and jitter**, *because* transient faults (429, 503, connection reset)
   usually clear within seconds, and jitter prevents every client from retrying in lockstep. Retry only
   transient classes, cap attempts, and give retries a budget (the SRE book's rule of thumb: retries
   should be a small fraction, around 10%, of total requests) so an outage cannot double your load.
3. **Idempotency**, *because* retries are only safe if a repeated operation has the same effect as one.
   A pure completion call is naturally idempotent apart from billing; a tool that sends money is not,
   and needs an idempotency key the receiver deduplicates on.
4. **Circuit breakers**, *because* once a dependency is clearly down, continuing to call it wastes your
   latency budget and its recovery capacity; fail fast and probe occasionally.
5. **Bulkheads**, *because* a failing dependency should exhaust its own pool of connections or
   workers, not the shared pool that serves everything else.
6. **Fallbacks and graceful degradation**, *because* "what do we do instead" is a product decision
   made in advance: a cached answer, a smaller model, retrieval-only mode, a static "try again" message.
   Each operation gets one explicit choice.
7. **Replication and consensus for state**, *because* stateless retries cannot recover data; replicas,
   quorum writes and leader election keep the truth alive when a node holding it dies.

### Fail-fast, fail-safe, fail-operational
A fail-fast component reports an error immediately rather than hanging, which protects callers'
budgets. A fail-safe system moves to a safe state on fault, such as an agent refusing to act when its
policy check is unavailable. A fail-operational system continues full service through the fault
through redundancy. Most AI features should be fail-fast internally and fail-safe at action boundaries.

### Detection and its own failure mode
Heartbeats, health checks and error-rate thresholds detect faults, but every detector has false
positives. Declaring a slow primary dead and promoting a replica while the old one is still writing
produces split brain. Fencing tokens or a single arbiter prevent two nodes from both believing they
lead. Detection thresholds are therefore a trade between failover speed and the risk of acting on a
wrong diagnosis.

## Example
A provider call wrapped in tools 1, 2 and 6 from the list: an overall deadline, per-attempt timeout,
retries only on transient statuses, full-jitter backoff that honours `Retry-After`, and a fallback that
degrades instead of failing. It assumes the call itself has no side effects, so repeating it is safe
(tool 3 satisfied by construction); the breaker and bulkhead (4 and 5) sit around this function, not
inside it.

```python
import random, time

class Transient(Exception):
    """Raised by the client on 408/429/5xx; carries Retry-After when the server sent one."""
    def __init__(self, status, retry_after=None):
        self.status, self.retry_after = status, retry_after

def tolerant_complete(primary, fallback, req, *, budget_s=45, attempt_timeout_s=20,
                      max_attempts=3, base_s=0.5, cap_s=8.0):
    deadline = time.monotonic() + budget_s             # one budget for the whole operation
    for attempt in range(max_attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            return primary(req, timeout_s=min(attempt_timeout_s, remaining))
        except Transient as e:                         # non-transient errors propagate: retrying cannot fix them
            if attempt == max_attempts - 1:
                break                                  # no attempts left: do not sleep for nothing
            sleep = e.retry_after if e.retry_after is not None \
                else random.uniform(0, min(cap_s, base_s * 2 ** attempt))   # full jitter
            if time.monotonic() + sleep >= deadline:
                break
            time.sleep(sleep)
    # primary exhausted or budget spent: degrade visibly rather than fail
    remaining = max(1.0, deadline - time.monotonic())  # floor: may overrun the budget by up to 1 s
    return fallback(req, timeout_s=remaining)          # smaller model, cached answer, or a typed "unavailable"
```

Why three attempts and not ten? If transient failures are independent at 5% per call, one attempt
succeeds 95% of the time, two 99.75%, three 1 − 0.05³ ≈ 99.99%; a fourth adds almost nothing. That
arithmetic only holds while failures are independent. During a real provider outage they are
perfectly correlated, every attempt fails, and the loop's only effect is to triple the load on the
provider and spend the user's budget, which is why the deadline, the breaker around this function and
the fallback matter more than the attempt count.

In production the `fallback` result should be tagged (a header or a field) so the UI and your metrics
know the answer came from the degraded path, and the retry count should be exported so you can see a
provider wobble before it becomes an outage. For tool calls with side effects, add an idempotency key
to `req` before the first attempt, never inside the loop.

## Trade-offs
- Retries raise success rates for transient faults but add load exactly when the dependency is weakest;
  the balance flips to "stop retrying" once the error rate suggests a real outage, which is the breaker's job.
- Redundant replicas tolerate crash faults but cost double and introduce consistency problems; for
  rarely used or cheap-to-recompute state, a fast restore beats a live replica.
- Fail-safe defaults protect users when a policy or guard service is down, but they turn a guard outage
  into a feature outage; fail-open is acceptable only for guards whose absence is low-risk.
- Tight timeouts free resources quickly but cut off legitimately slow responses such as long generations;
  the right value comes from the latency distribution, and streaming changes what "timeout" should mean.
- Fallback models keep the product available but change answer quality and behaviour; if customers
  depend on consistency, the fallback must be labelled and measured separately in evals.
- Masking by voting (several replicas or several sampled LLM answers) hides value faults but multiplies
  cost and latency by N; it pays only when errors are independent and a wrong answer is expensive, and
  it buys nothing when every sample shares the same misleading context.

## Pitfalls
- Retrying non-idempotent tool calls after a timeout: the first call succeeded, the reply was lost, and
  the symptom is duplicate emails, tickets or charges that correlate with provider latency spikes.
- Nested retries across layers (SDK retries × service retries × client retries) that multiply into tens
  of attempts per user action; visible as request volume spiking several-fold during an incident.
- Timeouts that add up: five sequential hops each with a 30 s timeout cannot meet a 30 s end-to-end
  promise, so users see failures even though every hop "met its timeout".
- Treating any exception as transient, so bad prompts or revoked API keys are retried for minutes;
  the symptom is latency without errors in logs until the budget finally expires.
- Fallbacks that were never exercised: the secondary provider's key expired or its tool schema drifted,
  discovered only during the primary's outage. Chaos tests exist for this.

## When to use
Every remote call needs the first three tools (timeout, bounded jittered retry on transient classes,
idempotency for anything with side effects); that is baseline, not a design choice. Add breakers and
bulkheads when a dependency's failure can starve shared resources, and define a fallback for every
user-facing operation where "nothing" is worse than "something degraded". Do not add fault tolerance
you cannot observe: export attempt counts, breaker state and fallback usage, or you will not know the
system is quietly running on its backup path. In day-to-day work it shows up as the client wrapper
everyone imports and the FMEA row that says what happens when each dependency fails. In interviews,
the expected depth is explaining why retries need budgets and idempotency, and walking a fault from
detection to degraded response.

## Role lens
- **Engineer:** Owns the shared provider and tool client: deadlines, jittered retries on transient
  statuses, idempotency keys for side-effecting tools, and metrics for every degraded path.
- **Architect:** Chooses the fault model and the degradation ladder per capability, and sets the
  rule that no retry layer exists without a budget and no action tool exists without idempotency.
- **FDE:** Maps which of the customer's systems can be retried safely, which need idempotency keys or
  reconciliation, and what degraded mode the customer's operations team will accept.

## Check yourself
**Q:** An agent's "create purchase order" tool call timed out at 30 seconds. The orchestrator retried,
and the customer's ERP now shows two orders. Walk through what went wrong and how the design should
change at each layer.
**A:** The timeout is an omission fault: the caller cannot know whether the first call completed, and
in this case it did. Retrying a non-idempotent operation after an ambiguous failure is the root
mistake. Fixes, from the inside out: the tool must accept an idempotency key generated once per
intended action, before the first attempt, and the ERP adapter must deduplicate on it or query for an
existing order with that key before creating. The orchestrator should classify timeouts on
side-effecting tools as "unknown outcome" and reconcile (read-after-timeout) rather than blindly
retry. The 30 s timeout should be derived from the ERP's actual latency distribution so it does not
trip on normal slow paths. Finally, an approval gate or a daily reconciliation report catches the
cases that still slip through, and the duplicate rate becomes a tracked metric.

## Quiz
### Q: Why must retries be combined with idempotency rather than used alone?
- [ ] Idempotency makes the retried call faster because the server can cache it
- [x] After a timeout the caller cannot know whether the first attempt took effect, so a repeat must be harmless
- [ ] Providers reject retries that do not carry an idempotency header
- [ ] Idempotency allows retries to skip the backoff delay
> A lost reply looks identical to a lost request. Without idempotency, retrying after an ambiguous
> failure duplicates side effects; with it, the second attempt either no-ops or returns the first result.

### Q: A dependency starts returning 503s for 40% of calls. Which combination best protects the rest of the system?
- [ ] Increase the retry count to five and remove the timeout so slow calls can finish
- [ ] Switch all calls to synchronous mode so failures are detected sooner
- [x] Keep a short timeout, retry within a budget, open a circuit breaker if errors persist, and serve a defined fallback
- [ ] Disable the dependency's health check so the load balancer keeps traffic flowing
> More retries and no timeout pour load onto a struggling dependency and tie up your own resources.
> Budgeted retries plus a breaker contain the fault, and the fallback keeps users served.

### Q: Which fault model best describes an LLM returning a syntactically valid tool call with the wrong account ID?
- [ ] Crash-stop, because the model stopped doing useful work
- [ ] Omission, because the correct answer was omitted
- [x] Byzantine, because the component produced well-formed but incorrect output that passes shallow checks
- [ ] Timing, because the model answered too quickly to have reasoned
> Byzantine (arbitrary) faults are wrong-but-plausible outputs from a component that has not crashed.
> They are caught by validation against ground truth and by approval gates, not by retries or timeouts.
