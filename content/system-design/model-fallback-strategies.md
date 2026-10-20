---
id: model-fallback-strategies
module: system-design
name: Model fallback strategies
kind: pattern
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - serving-inference/fallbacks
  - serving-inference/retry-strategies
  - serving-inference/timeouts
  - reliability/circuit-breakers
  - system-design/graceful-degradation
  - system-design/ai-gateway
  - infra-capacity-economics/api-provider-quotas-and-regional-limits
  - agent-reliability/idempotent-tool-execution
sources:
  - Nygard, 2007 — Release It!
  - Dean and Barroso, 2013 — The Tail at Scale
---

## TL;DR
Every model endpoint fails sometimes: outages, 429 throttling, latency spikes, regional capacity limits,
deprecated model ids. A fallback strategy decides in advance what to call instead, in what order, and on
which errors, so a provider's bad hour does not become your outage. Fallbacks are not free: the
substitute behaves differently, retries can amplify an incident, and an untested fallback is a second
failure waiting behind the first.

## Why it exists
A model API is a remote dependency with several distinct ways of failing: hard errors (5xx, connection
resets), throttling (429, sometimes with a `Retry-After`), slow responses that blow the deadline when the
provider is overloaded, capability changes (a model id retired, behaviour shifted by a silent update),
and exhausted quota in one region while another has capacity. Each surfaces to the user as an error or a
spinner that never resolves. If your availability target is higher than the provider's, arithmetic alone
says you need a second, independent path.

The naive alternative, "retry with backoff", only covers transient blips on the same endpoint. During a
real incident it makes things worse: every client retries, load on the failing provider multiplies, and
each user waits through several timeouts before seeing an error. The other naive alternative, "add a
second provider", fails more quietly: the backup returns malformed JSON, ignores the tool schema, or
truncates at a smaller context window, so the error rate looks fine while the product silently breaks.

Fallback is a specific thing, distinct from its neighbours. A *retry* repeats the call on the same
target. A *fallback* keeps the function but changes the target: another region, another provider,
another model. *Degradation* changes the function itself. Getting the three confused is how teams end up
with a fallback chain that is slower than the outage it was meant to hide.

## How it works
### Classify the failure first
Every error maps to one of three actions, and the mapping is the heart of the design.
- **Retry the same target** for 429 (honour `Retry-After`), 5xx, connection errors and timeouts on
  idempotent calls; once or twice, with jittered backoff, never unbounded.
- **Fail over** on repeated 5xx or timeouts, an open circuit, a 404 for a retired model id, or regional
  capacity errors.
- **Neither** for 400 invalid request, 401, content-policy refusals and context-too-long: these recur
  identically on every target, so failing over just multiplies latency and cost.

### The ladder, ordered by behavioural distance
1. Same model, another deployment or region: identical behaviour, different capacity.
2. Same model through another provider (a cloud platform serving the same weights): identical weights,
   possibly different limits and features.
3. Same family, adjacent version: small drift in style and formatting.
4. Comparable model from another vendor: prompts, tool-call formats, structured output and tokeniser
   limits must all be re-validated.
5. A smaller model: a quality drop, but still the same function.
6. Hand off to graceful degradation: cache, non-AI path, honest unavailability.
Order *because* each rung down changes behaviour more; you want the first rung that works to be the one
users cannot tell from normal.

### Deadlines, not timeouts
Each attempt needs its own timeout, and the attempts must fit inside one user-facing deadline. Without
that, a 10 s primary timeout plus a 10 s fallback is a 20 s worst case nobody signed off. Pass the
remaining budget down the ladder and skip rungs that cannot complete in time.

### Circuit breakers
Per target: after N failures in a window the breaker opens, the target is skipped for a cooldown, then a
single probe decides whether to close it. Without a breaker, every request during an outage waits the
full primary timeout *before* falling back, so latency equals timeout plus fallback for the whole
incident, and the failing provider keeps receiving your traffic.

### Hedging for latency
Dean and Barroso's hedged request sends the same call to a second target after the p95 delay has elapsed
and takes whichever answers first. It trims the tail at the cost of roughly 5% extra calls, but only for
idempotent calls, never once a stream has been committed to the user, and it must switch off when the
breaker shows the primary is overloaded, or it doubles the load that caused the problem.

### Equivalence is a test, not an assumption
Validate outputs against a schema regardless of which model served them; keep per-model prompt variants
when formats differ; send a small continuous slice (around 1%) through each fallback so it is exercised
in daily traffic; run game days where the primary is blocked on purpose. Tag every response with the
target that served it, and alert when the fallback share rises, otherwise the primary can be down for a
week and the only signal is the bill.

### Side effects and streams
A retry or failover after a tool call executed (an email sent, a record written) duplicates the side
effect; tool calls need idempotency keys and agent state needs checkpoints so the fallback resumes after
the action rather than replaying it. A stream that fails midway restarts on the fallback with the partial
output discarded; models do not reliably continue each other's text.

### Capacity and cost
Failing all traffic over to a secondary hits the secondary's rate limits unless quota was provisioned in
advance; you cannot buy it during the incident. Failing over to a larger model is a cost spike. Both
belong in the runbook before the first outage.

## Example
A fallback ladder with per-target circuit breakers (closed, open, half-open with a single probe), error
classification and a shared deadline. `call_model`, `ModelError` (with an HTTP `status`) and
`ServiceDegraded` are the adapter types your client layer provides; the point is the control flow.

```python
import asyncio, random, time

class Breaker:                       # closed -> open (after N failures in window) -> half-open (one probe)
    def __init__(self, fails=5, window=30, cooldown=60):
        self.fails, self.window, self.cooldown = fails, window, cooldown
        self.errors, self.open_until, self.probing = [], 0.0, False
    def allow(self):
        now = time.time()
        if now < self.open_until: return False          # open: skip this target without waiting
        if self.open_until and self.probing: return False   # half-open: one probe is already in flight
        if self.open_until: self.probing = True         # cooldown over: let exactly one probe through
        return True
    def record(self, ok):
        now = time.time()
        if self.probing:                                # half-open: the probe alone decides
            self.probing = False
            self.open_until, self.errors = (0.0, []) if ok else (now + self.cooldown, [])
            return
        self.errors = [t for t in self.errors if now - t < self.window] + ([] if ok else [now])
        if len(self.errors) >= self.fails:
            self.open_until, self.errors = now + self.cooldown, []

RETRYABLE = {408, 429, 500, 502, 503, 504}
LADDER = [("primary-us", 3.0), ("primary-eu", 3.0), ("vendor-b-equivalent", 6.0)]   # (target, timeout s)
breakers = {t: Breaker() for t, _ in LADDER}

async def complete(messages, deadline=10.0):
    start, last_err = time.time(), None
    for depth, (target, timeout) in enumerate(LADDER):
        remaining = deadline - (time.time() - start)
        if remaining <= 0 or not breakers[target].allow():
            continue                                    # out of budget, or breaker open: skip this rung
        try:
            resp = await asyncio.wait_for(call_model(target, messages), timeout=min(timeout, remaining))
            breakers[target].record(ok=True)
            return {"served_by": target, "fallback_depth": depth, **resp}
        except ModelError as e:
            breakers[target].record(ok=False); last_err = e
            if e.status not in RETRYABLE:
                raise                                   # 400/401/policy errors recur on every rung
            await asyncio.sleep(random.uniform(0, 0.2))  # jitter before the next rung
        except asyncio.TimeoutError:
            breakers[target].record(ok=False); last_err = "timeout"
    raise ServiceDegraded(last_err)                     # hand off to the graceful-degradation layer
```

`served_by` and `fallback_depth` go into metrics and traces; the alert on fallback depth greater than zero
is what tells you the primary is unhealthy before customers do.

## Trade-offs
- Availability rises, but output consistency falls as the ladder descends to other vendors; for audited
  or regulated outputs, restrict the ladder to same-model rungs and degrade honestly after that.
- Hedged requests cut tail latency for a few per cent extra calls, yet they double load exactly when the
  provider is struggling; disable hedging whenever the breaker indicates overload.
- A longer ladder raises the chance of success but stretches the worst-case latency; with a tight
  deadline a short ladder with a breaker beats a long one.
- Cross-vendor fallback sends data to a second processor; it flips from asset to liability when that
  vendor is not on the approved list for the data class involved.
- Pre-provisioned secondary quota costs money while idle, but without it the fallback fails with 429s
  at the exact moment it is needed.

## Pitfalls
- A fallback never exercised in normal traffic; the symptom is the first real outage revealing expired
  credentials, a wrong model id or a schema mismatch on the secondary.
- Retrying or failing over after a side effect; the symptom is duplicate emails, tickets or payments
  appearing during provider incidents.
- No circuit breaker; the symptom is every request during an outage taking primary timeout plus fallback
  time, and the failing provider being hammered by your retries.
- Silent fallback with no metric; the symptom is discovering the primary has been down for days from the
  invoice or from a slow drift in answer quality.
- Failing over on non-retryable errors; the symptom is a single bad request producing identical 400s
  from three providers with triple the latency and cost.
- Ignoring context-window and limit differences; the symptom is context-length errors appearing only on
  long requests and only when the fallback is active.

## When to use
Every production call should have at least the first two rungs and a documented ladder; how deep it goes
depends on the SLO, the audit constraints on outputs and the cost you can absorb during an incident.
Decide the error classification, the deadline and the breaker thresholds per use case, and provision
secondary quota before launch. Measure fallback rate by rung, success rate after fallback, latency by
rung, the cost delta during failover and the quality of fallback-served responses on your eval. In
interviews, distinguish retry, fallback and degradation cleanly, then talk about error classification,
breakers, deadline propagation and equivalence testing; that sequence is the expected depth.

## Role lens
- **Engineer:** Implements error classification, breakers and deadline propagation, tags responses with
  the serving target, and writes the game-day test that blocks the primary on purpose.
- **Architect:** Defines the ladder per use case under governance constraints, provisions secondary
  quota and hedging policy, and sets the SLO the fallback path itself must meet.
- **FDE:** Builds the ladder from models and regions the customer has approved, often inside their cloud
  platform, and runs a failover drill with them before go-live.

## Check yourself
**Q:** Your primary provider had a 40-minute outage. The fallback to a second vendor "worked" and the
error rate stayed flat, but the next morning support reports a spike in malformed outputs and several
customers received duplicate emails from the agent. What went wrong, and how do you fix it?
**A:** Two independent failures. First, the fallback model was never validated for these prompts: its
tool-call format or JSON adherence differs, so outputs that passed through unvalidated were malformed.
Fix with schema validation that runs regardless of model, per-model prompt variants where formats
differ, a permanent small slice of traffic through the fallback, and a quality metric split by serving
target. Second, the agent retried or failed over after a tool had already executed, replaying the
send-email step. Fix with idempotency keys on tool calls, a rule that non-idempotent steps are never
retried blindly, and checkpointed agent state so the fallback resumes after the completed action. Add an
alert on fallback share so someone is watching during the outage, not the morning after.

## Quiz
### Q: Which error should not trigger failover to another provider?
- [ ] A 503 from the primary endpoint
- [ ] A timeout after the per-attempt budget expired
- [x] A 400 invalid-request error or a content-policy refusal
- [ ] A 429 after the Retry-After period was honoured once
> Invalid requests and policy refusals are properties of the request, not the provider, so every rung
> returns the same error; failing over only multiplies latency and cost. The other three are capacity or
> transient failures that a different target can resolve.

### Q: What does a per-target circuit breaker add to a fallback ladder?
- [ ] It lowers the token cost of fallback calls
- [x] It skips a failing target immediately instead of waiting out its timeout on every request, and stops hammering it
- [ ] It guarantees identical outputs across targets
- [ ] It removes the need for retries
> Without a breaker, each request during an outage pays the primary's full timeout before falling back,
> and the retries add load to a provider that is already struggling. The breaker turns a slow failure into
> a fast one.

### Q: Which statement about hedged requests is correct?
- [ ] They halve the cost of the request
- [ ] They guarantee the p99 equals the p95
- [x] They trim tail latency for a few per cent extra calls and must be disabled when the primary is overloaded
- [ ] They are safe for calls with non-idempotent side effects
> Sending a second request after the p95 delay helps only the slow tail and costs roughly the fraction
> of requests that exceed that delay. During provider overload the extra load makes things worse, and
> duplicated side effects make hedging unsafe for non-idempotent calls.
