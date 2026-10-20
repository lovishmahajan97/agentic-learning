---
id: fallbacks
module: serving-inference
name: Fallbacks
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
related:
  - system-design/model-fallback-strategies
  - reliability/circuit-breakers
  - system-design/graceful-degradation
  - serving-inference/retry-strategies
  - serving-inference/timeouts
  - serving-inference/idempotency
  - system-design/ai-gateway
  - system-design/model-routing-cascading
sources:
  - Nygard, 2007 — Release It! (circuit breaker and stability patterns)
  - Beyer et al., 2016 — Site Reliability Engineering (Addressing Cascading Failures)
  - Official docs: Anthropic API errors and rate limits
---

## TL;DR
A fallback is a pre-decided alternative path taken when the primary one fails or is too slow: the same
model on another endpoint or region, a smaller model, a cached or templated answer, or an honest
"unavailable". It exists because LLM dependencies fail partially and often, and retrying a broken target
only burns the latency budget. The hard parts are deciding fast, keeping the alternative behaviourally
compatible, and not letting everyone's fallback crush the backup.

## Why it exists
Hosted models fail in ways a retry cannot fix: a provider returns 529 overloaded or 429 for minutes
during a demand spike, a region has an incident, a deployment pushes elevated 5xx, a model is deprecated,
a content filter refuses, latency triples under load. Self-hosted engines add their own: a GPU node dies,
a rollout ships a bad build, a long-context burst preempts everything. A retry assumes the target will be
healthy a second later; during an incident it is not, and three attempts against a 30-second timeout is a
90-second path to the same error. The alternative to a fallback is showing the user an error, which is
sometimes right (a medical triage assistant should fail closed) but for most product surfaces a slightly
worse answer beats none.

The naive implementation, a `try/except` that calls model B, fails for reasons that only show up in
production. Model B may not support your tool schema, JSON mode or context length, and you discover this
when A is already down. Your evaluation suite never ran against B, so quality in fallback mode is
unknown. When A fails for all your traffic at once, B's rate limit, which you never provisioned, is
exceeded within seconds: the fallback storm. A timeout on A may have fired after A executed a side effect
inside an agent loop, so B duplicates it. And if tokens were already streamed from A, you cannot switch
mid-sentence. Fallbacks are a designed subsystem, not an exception handler.

## How it works
### The ladder
Order alternatives by behavioural distance from the primary, not by cost. A typical ladder: the same
model on a different endpoint or region (the major models are available through more than one cloud,
which keeps prompts and evals valid); a sibling model from the same family one size down; a different
vendor's model with a translated request; a non-generative degraded answer (cached response, retrieval
results without synthesis, a template); and finally an explicit error with a retry affordance. Each rung
costs quality, so the ladder is built to exhaust the cheapest-in-quality options first.

### Trigger classification
Only some failures justify a fallback. Transient capacity errors (429, 500, 502, 503, 504, 529 (Anthropic's
overloaded status), connection failures, timeouts before the first token) justify one retry on the same target and then a
fallback. Request-specific errors (400 for a malformed body, a context-length overflow, an unsupported
parameter) will fail identically on any equivalent target; the one exception is a context overflow,
which may legitimately route to a model with a larger window. Authentication and billing errors are
configuration faults and should page a human rather than fail over. Latency is also a trigger: if time
to first token exceeds a budget, cancel and try the next rung, because the user cannot tell the
difference between slow and down. A softer variant is a *hedge*: once the primary has been silent past
roughly its p95 time to first token, start the fallback in parallel, keep whichever produces a first
token first and cancel the other. Because only the slowest few percent of calls spawn a hedge, the extra
load is small, but each hedge is billed on both targets until the loser is cancelled.

### Circuit breaker per target
Without a breaker, every request during an outage pays the primary's full timeout before falling back,
so latency doubles at exactly the moment the system is under stress. A breaker tracks the failure *rate*
over a sliding window of recent calls per target, opens when that rate crosses a threshold (with a
minimum call count, so three failures out of three at 2 a.m. do not trip it), routes straight to the
fallback while open, and after a cooldown moves to *half-open*, letting a single probe through whose
result decides whether to close or reopen. It converts "try and wait" into "skip, because we already
know".

How much a breaker buys depends on how failures arrive. A slow failure (a 30-second timeout, a stalled
stream) is what makes "try the primary first" expensive, so breakers pay off most there. A fast failure
(a 529 or 503 returned in tens of milliseconds) costs almost nothing to attempt, so for a *partial*
outage it is often better to leave the breaker closed and let each request try the primary once, keeping
the healthy fraction of capacity in use. Count-of-consecutive-failures breakers, the common naive
version, behave badly here: at a 20% independent error rate five failures in a row happen only once every few
thousand calls, so the breaker almost never opens, while at a 60% rate it flaps.

### Compatibility layer
The fallback must receive an equivalent request. That means normalising message formats, translating
tool schemas, mapping parameter names and ranges, re-checking that the prompt fits the target's context
window using the target's tokenizer, and knowing whether structured output is supported. Gateways
(self-built, or products such as LiteLLM, OpenRouter or Portkey) exist largely to own this translation.
Prompts should be evaluated on every rung; where a model needs a variant prompt, version it alongside the
primary's.

### One deadline for the whole chain
Give the request a single deadline, for example 20 seconds, and hand each rung the remainder rather than
its own timeout. Otherwise chained timeouts add up to a wait no user will tolerate.

### Keeping the backup real
Provision quota on the fallback deliberately and route a trickle of live traffic (a few percent) to it,
so its credentials, quota, prompts and evals are continuously verified. An unexercised fallback is an
untested one.

### Observability and streaming
Tag every response with the target that served it and the reason for any fallback; alert when the
fallback rate rises, because quality regressions in fallback mode are invisible otherwise. For streaming,
decide before the first byte: a failure after tokens have been emitted either restarts the response
under a new message id (so the client can discard the partial one) or surfaces an error with a retry
button.

## Example
The chain below tries each target in order, skips any whose breaker is open, gives each the remaining
share of one deadline, refuses to fail over on request-specific errors, and labels the response with what
served it. Per-target retries live inside `call`, where the SDK already does them.

```python
import time
from collections import deque

class Breaker:
    """Opens when >= `threshold` of the last `window` calls failed; one half-open probe per cooldown."""
    def __init__(self, window=20, min_calls=10, threshold=0.5, cooldown_s=30):
        self.results, self.min_calls, self.threshold = deque(maxlen=window), min_calls, threshold
        self.cooldown_s, self.state, self.open_until = cooldown_s, "closed", 0.0
    def available(self):
        if self.state == "open" and time.monotonic() >= self.open_until:
            self.state = "half_open"; return True      # exactly one probe gets through
        return self.state == "closed"
    def record(self, ok):
        if self.state == "half_open":                  # the probe's result decides
            self.state = "closed" if ok else "open"
            self.results.clear(); self.open_until = time.monotonic() + self.cooldown_s
            return
        self.results.append(ok)
        n, fails = len(self.results), self.results.count(False)
        if n >= self.min_calls and fails / n >= self.threshold:
            self.state, self.open_until = "open", time.monotonic() + self.cooldown_s

TRANSIENT = {408, 429, 500, 502, 503, 504, 529, "timeout", "connection"}
LADDER = [("anthropic", "primary-model"), ("bedrock", "primary-model"), ("anthropic", "smaller-model")]
breakers = {t: Breaker() for t in LADDER}

def generate(req, deadline_s=20.0):
    deadline, last_err = time.monotonic() + deadline_s, None
    for target in LADDER:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not breakers[target].available():
            continue
        try:
            resp = call(target, adapt(req, target), timeout=min(remaining, 10.0))
        except ProviderError as e:
            if e.code not in TRANSIENT:                # malformed request, policy, auth: no rung fixes this
                breakers[target].record(True); raise   # the target was healthy; our request was not
            breakers[target].record(False); last_err = e; continue
        breakers[target].record(True)
        resp.meta = {"served_by": target, "fallback": target != LADDER[0]}
        return resp
    return degraded_response(req, last_err)            # cached/template answer, or an explicit error
```

Trace it through an incident. With a 50% threshold over 20 calls, a primary returning 529s on 20% of
requests stays closed: each failing request pays one fast 529 and moves to the second endpoint, and the
80% that succeed keep using the primary. If the primary starts timing out on most calls, the window
crosses 50% after at least ten calls, the breaker opens, and for the next 30 seconds every request goes
straight to the second endpoint without paying a 10-second timeout; one probe then tests recovery. The
numbers are starting points to tune against your own error and latency distributions.

## Trade-offs
- Availability rises, but quality in fallback mode is lower and, unless you evaluate every rung, unknown;
  when the fallback cannot meet the product's accuracy bar, failing closed is the better design.
- Breakers cut incident latency sharply but can trip on a noisy error burst and divert healthy traffic;
  a low threshold and short window react fast but flap, a high threshold and minimum call count are
  stable but leave users paying timeouts for longer, and the right point moves toward "react fast" the
  slower the target's failures are.
- A second vendor removes correlated outage risk but doubles compliance scope (data residency, DPAs),
  prompt maintenance and eval cost; a second region of the same provider is cheaper when the risk you
  fear is regional rather than vendor-wide.
- Degraded non-LLM answers keep the interface alive, but presenting a cached or templated response as
  fresh can be worse than an honest error for time-sensitive questions.

## Pitfalls
- A fallback that has never served traffic; the symptom is fallback rate and error rate spiking together
  during the first real incident, because the key expired, the quota was zero or the schema never matched.
- Failing over on non-transient errors; the symptom is every request costing two or three calls and still
  failing, with identical 400s from each target in the trace.
- Independent timeouts per rung instead of one shared deadline; the symptom is p99 latency during
  incidents equal to the sum of all the timeouts.
- A fallback storm; the symptom is the secondary provider returning 429 within seconds of the primary's
  incident, because the whole fleet moved at once onto unprovisioned quota.
- Silent model swaps; the symptom is JSON parse errors or refusal-rate changes that correlate with the
  fallback rate, because output formatting quirks differ between models and the downstream parser was
  tuned to one.
- Falling back after a side effect; the symptom is duplicate tickets or emails whenever the primary times
  out mid-tool-call, which is an idempotency problem the fallback exposes.

## When to use
Add a fallback wherever an LLM call sits on a user-facing path with an availability target the single
provider cannot meet alone, and wherever a cheaper degraded answer is genuinely acceptable. Do not add one
where a worse answer is worse than no answer, or where the "fallback" would silently change compliance
posture. Start with the same model on a second endpoint, because that preserves prompts and evals; add a
smaller model only after evaluating it; add a non-LLM degraded response as the last rung. Measure fallback
rate, per-target error rate, latency per rung and quality by served-by tag. In interviews, the expected
depth is: classify the trigger, break the circuit, share one deadline, keep the fallback compatible and
warm, and explain why streaming and side effects make "just try B" unsafe.

## Role lens
- **Engineer:** Builds the target ladder, breaker state and request adapters, writes evals that run
  against every rung, and tags traces with the serving target and the fallback reason.
- **Architect:** Owns the policy: which failures fail over, which fail closed, which models are approved
  as fallbacks, and how much quota is reserved on each backup.
- **FDE:** Negotiates with the customer which degraded behaviour is acceptable for their use case, and
  verifies that their data-residency rules still hold when traffic moves to a second region or vendor.

## Check yourself
**Q:** Your primary provider starts returning 529 overloaded errors for about 20% of requests for half an
hour. Design the fallback behaviour for a customer-support chat product and explain what you would
deliberately avoid doing.

**A:** First, classify: 529 is transient and, importantly, fast; it comes back in milliseconds, so
attempting the primary costs little. At a 20% error rate the primary is still serving most traffic, so I
would *not* want the breaker to open and push all load onto the backup; a rate-based breaker with a
threshold well above 20% stays closed, and each failing request makes one quick attempt (or one short
jittered retry) and then moves to the next rung. The breaker exists for the case where the primary
degrades into timeouts or a majority error rate. Route to the
same model on a second endpoint or region first, because prompts and evals carry over; only if that is
also degraded, step down to a smaller model that has already been evaluated on support transcripts.
Confirm the backup has quota for about a fifth of peak traffic, since that is what will move. Give
the whole chain one deadline of a few seconds to first token, since chat users abandon quickly. Decide
before streaming starts; a mid-stream failure shows the partial answer with a retry button rather than
silently restarting. Tag every reply with served-by and watch quality metrics in fallback mode. Avoid:
failing over on 400s, letting the entire fleet shift onto a backup with no pre-provisioned quota, and
failing over tool-executing turns without idempotency keys, which would duplicate ticket creation.

## Quiz
### Q: Why put a circuit breaker in front of the primary model instead of simply catching its errors and calling the fallback?
- [ ] Breakers are required to satisfy provider terms of service during outages
- [x] Without one, every request during an outage waits for the primary's full timeout before falling back, so latency doubles when the system is already under stress
- [ ] Catching exceptions is unreliable for streaming responses
- [ ] Breakers reduce the number of distinct fallback targets you need
> A try/except still issues the doomed primary call. A breaker remembers recent failures and skips the
> primary while open, so fallback requests are fast instead of adding a full timeout to each response;
> the gain is largest when failures are slow timeouts rather than instant error codes.

### Q: Which failure should NOT trigger failover to a second provider?
- [ ] A 503 from the primary provider
- [ ] A timeout before the first token arrives
- [x] A 400 caused by a malformed tool schema in your request
- [ ] A 529 overloaded error
> A malformed request fails identically everywhere, so failing over doubles cost and latency for nothing;
> the fix is in your code. The other three are capacity or transient faults where another target can
> genuinely succeed.

### Q: What is a "fallback storm"?
- [ ] A loop where the fallback model's errors trigger retries against the primary
- [x] The whole fleet shifting onto the backup at once when the primary fails, exceeding quota the backup never had provisioned
- [ ] Repeated breaker trips caused by a noisy error threshold
- [ ] Clients retrying with no jitter after a 503
> During a primary outage every request moves simultaneously to the fallback. If its rate limit was sized
> for a trickle, it fails within seconds, turning one outage into two. Pre-provisioned quota and load
> shedding prevent it.
