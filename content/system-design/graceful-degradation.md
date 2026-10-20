---
id: graceful-degradation
module: system-design
name: Graceful degradation
kind: pattern
difficulty: core
est_minutes: 14
status: crisp-verified
related:
  - system-design/model-fallback-strategies
  - reliability/backpressure-and-load-shedding
  - reliability/circuit-breakers
  - reliability/bulkheads
  - reliability/slas-slos
  - system-design/nfrs
  - llmops-platform/rollback
  - agent-reliability/max-step-budgets
sources:
  - Beyer et al., 2016 — Site Reliability Engineering
  - Nygard, 2007 — Release It!
---

## TL;DR
When the AI path fails, slows, overspends or overloads, step down to a cheaper, simpler, honestly labelled
version instead of falling over, using a designed ladder with triggers, hysteresis and exercised paths.
Fallbacks change the provider; degradation keeps users moving by changing the function.

## Why it exists
- **Most AI features enhance a product that already worked:** search, support, editors, dashboards. A
  model incident, an exhausted rate limit, a runaway cost day or a traffic spike will happen.
- **If the feature's failure breaks the workflow around it,** an enhancement becomes a new failure point
  and lowers the whole product's availability. Users who could have found the document themselves now see
  a spinner.
- **The naive responses each miss something.**
  - "Retry and show an error" makes the user wait and then gives them nothing.
  - "Fall back to another model" handles a provider outage, not overload, budget exhaustion or a latency
    SLO the fallback also misses.
  - "Turn the feature off" is one binary switch, so the smallest incident costs the whole feature.
- **AI adds a failure the others ignore:** a response that was produced but should not be trusted,
  because the grounding check failed or the judge rejected it. Show less and say why; do not fail.
- **AI adds an axis to SRE practice.** Load shedding and priority classes came from site reliability
  engineering; AI adds quality levels, because one request can be answered well, adequately, or not at
  all, at different costs. Degradation decides those levels before the incident.

## How it works
### Define the ladder per feature
| Level | What the user gets | Typical contents |
|---|---|---|
| **0, full** | The best answer | Strong model, tools, reranking, a verifier |
| **1, reduced** | Visibly still an answer; cheaper and faster | Smaller model, fewer retrieved passages, no reranker, lower `max_tokens`, agent loops capped at one step |
| **2, cached or precomputed** | A known-good answer | Cached answer to a similar question, precomputed summary, FAQ entry, scoped to the same tenant |
| **3, non-AI** | The product as it was before the feature | Keyword search results, a template, the raw documents, a form |
| **4, honest unavailability** | A way to proceed | A clear message, the rest of the product working, a human handoff, a link, try later |

For each level write down what the user sees, what it costs, and who gets it.

### Triggers
- **Dependency health:** breaker state, error rate, p95 latency.
- **Load:** queue depth, concurrency.
- **Budget burn:** spend today against the cap.
- **Rate-limit headroom.**
- **Quality signals:** a failed grounding check demotes *this* response to sources-only.
- **A human-operated kill switch.**

### Hysteresis
- **Evaluate triggers where the decision is made.**
- **Step down quickly on a bad signal; step back up only after sustained health,** *because* flapping
  between levels is worse for users than staying one level down.
- **Concretely:** separate enter and exit thresholds plus a minimum dwell time. Illustrative numbers: enter
  Reduced when p95 exceeds 4 s; leave only after p95 has stayed under 2.5 s for ten minutes.
- **The gap between 4 s and 2.5 s** stops a signal hovering near one threshold from toggling the level on
  every sample.

### Budget triggers: projected burn, not spend so far
| Trigger | When it fires on a heavy day | Effect |
|---|---|---|
| `spent + current_rate × hours_left > cap` | Mid-morning | Step down one level gently |
| `spent ≥ cap` | Late in the day | Every user drops straight to the cached level for the rest of it |

### Shed by priority, isolate by class
1. **Classify requests:** interactive versus batch, paying versus free, critical versus nice-to-have.
2. **Shed batch and low tiers first.**
3. **Reserve capacity** for critical interactive traffic in its own bulkhead, so a batch backfill can never
   starve the chat box.
4. **Shed at admission, before any tokens are spent.** A request abandoned after generation started has
   already paid for the work it throws away, and under overload that waste is exactly the capacity you
   were short of.

### Deadlines and partial results
- **Give each request a deadline** and return what you have when it expires: retrieved passages without
  the synthesis, or a streamed partial answer with a visible marker.
- **Streaming makes partial output useful** rather than confusing.
- **Partial answers are dangerous where a half-statement misleads;** there, sources-only is the right
  degraded form.

### Honesty in the interface
- **Label degraded output:** "Showing matching documents; the AI summary is unavailable".
- **Never present a Level 1 answer as Level 0** when the quality difference matters, since users act on
  outputs and silent degradation transfers the risk to them.

### Keep the degraded paths alive
- **Unused paths rot:** the non-AI path's index goes stale, its credentials expire, its flag is removed.
- **Exercise them:** send a small share of traffic through each level routinely, run synthetic checks, and
  hold game days.
- **Every level sits behind a flag** that flips without a deploy, and the runbook names the flags.

### Measure
- Time spent at each level and requests served per level.
- User outcomes per level (completion rate, handoff rate).
- Time to recover to full.
- **A good SLO:** "Percentage of sessions served at Level 0".

## Diagram
The ladder as states: each trigger steps down at once, a failure inside a level moves the request one level
down, and the only way back up to Full is sustained health for the dwell time.

```mermaid
stateDiagram-v2
    [*] --> Full
    Full --> Reduced: p95 over 4 s, budget at 80 percent, queue over 200
    Reduced --> Full: p95 under 2.5 s for ten minutes
    Full --> Cached: breaker open or budget spent
    Reduced --> Cached: model timeout or error on this request
    Cached --> Reduced: breaker closed and budget recovered
    Cached --> NonAI: no tenant-scoped cache hit
    Full --> NonAI: kill switch flipped
    NonAI --> Unavailable: search itself is down
    Unavailable --> NonAI: search recovers
    NonAI --> Full: kill switch cleared and health sustained
```

## Examples
### Support-answer endpoint with a degradation ladder
A background task refreshes `h`, a health snapshot, every few seconds; the handler picks a level, enforces a
deadline, and steps down rather than raising when a stage fails. `keyword_or_vector_search`, `generate`,
`semantic_cache_lookup` and `ModelError` are your adapters.

```python
import asyncio, time
from enum import IntEnum

class Level(IntEnum):
    FULL = 0; REDUCED = 1; CACHED = 2; NON_AI = 3; UNAVAILABLE = 4

def current_level(h):                            # h: health snapshot refreshed by a background task
    if h.kill_switch:                                           return Level.NON_AI
    if h.budget_used >= 1.0 or h.llm_breaker_open:              return Level.CACHED
    if h.budget_used >= 0.8 or h.llm_p95_ms > 4000 or h.queue_depth > 200:
        return Level.REDUCED
    return Level.FULL

async def answer(q, user, h, deadline_s=6.0):
    level = current_level(h)
    if user.tier == "free" and h.queue_depth > 50:              # shed low priority first
        level = max(level, Level.REDUCED)
    t0 = time.monotonic()
    try:
        docs = await keyword_or_vector_search(q, k=20 if level == Level.FULL else 5)
    except Exception:                                           # even the non-AI path is down
        docs, level = [], Level.UNAVAILABLE
    if level <= Level.REDUCED:
        full = level == Level.FULL
        try:
            remaining = deadline_s - (time.monotonic() - t0)
            text = await asyncio.wait_for(
                generate("strong" if full else "small", q, docs if full else docs[:3],
                         max_tokens=600 if full else 200), remaining)
            return {"level": int(level), "answer": text, "sources": docs[:5],
                    "label": None if full else "Reduced-quality answer"}
        except (asyncio.TimeoutError, ModelError):
            level = Level.CACHED                                # step down, do not raise
    if level == Level.CACHED:
        hit = await semantic_cache_lookup(q, tenant=user.tenant)
        if hit:
            return {"level": 2, "answer": hit.answer, "sources": hit.sources, "label": "Cached answer"}
        level = Level.NON_AI
    if level == Level.NON_AI:
        return {"level": 3, "answer": None, "sources": docs[:5],
                "label": "AI assistant unavailable; showing matching documents"}
    return {"level": 4, "answer": None, "sources": [], "label": "Search is temporarily unavailable"}
```

- **The `level` field in every response is the metric:** a histogram of it over time shows whether the
  product has been quietly running degraded.
- **Hysteresis lives in the background task.** `current_level` is stateless for brevity; the task that
  builds `h` should hold a level until its exit condition has persisted for the dwell time, so every
  request in a window sees the same level.
- **Each step down is cheaper than the one above,** and a failure inside a level moves the request down
  rather than retrying, so an incident lowers spend instead of raising it.

### Hysteresis controller for the background task
Runnable, pure Python, using the illustrative 4 s / 2.5 s / ten-minute thresholds.

```python
class Hysteresis:
    """Enter REDUCED fast when p95 > enter_ms; leave only after p95 < exit_ms for dwell_s straight."""
    def __init__(self, enter_ms=4000, exit_ms=2500, dwell_s=600):
        self.enter_ms, self.exit_ms, self.dwell_s = enter_ms, exit_ms, dwell_s
        self.reduced, self.healthy_since = False, None

    def update(self, p95_ms, now_s):
        if p95_ms > self.enter_ms:
            self.reduced, self.healthy_since = True, None      # step down immediately
        elif self.reduced and p95_ms < self.exit_ms:
            self.healthy_since = self.healthy_since or now_s
            if now_s - self.healthy_since >= self.dwell_s:
                self.reduced, self.healthy_since = False, None  # step up after sustained health
        elif self.reduced:
            self.healthy_since = None                           # in the gap: hold the level, reset dwell
        return "REDUCED" if self.reduced else "FULL"

h = Hysteresis()
samples = [3000, 4200, 3800, 4100, 2400, 3000, 2300] + [2000] * 10   # one sample per minute
print(" ".join(h.update(p, 60 * i)[0] for i, p in enumerate(samples)))
# F R R R R R R R R R R R R R R R F : no flapping around 4 s; full only after 10 healthy minutes
```

A single 4 s threshold on the same samples would flip F R F R F F F, which users read as randomness.

### Worked scenario: projected-burn budget trigger
Illustrative: daily cap $500, a heavy day where spend reaches $150 by 10:00 and runs at $40 per hour.

| Trigger | Fires at | What users get |
|---|---|---|
| Projected: 150 + 40 × 14 h left = $710 > $500 | 10:00 | Step to Reduced; if the smaller model halves the rate, 150 + 20 × 14 = $430 and the cap holds |
| Spend so far: $150 + $40/h reaches $500 | about 18:45 | Everyone drops straight to Cached for the rest of the day |

## Production patterns
- **Levels as runtime flags.** Read the forced level and the kill switch per request from your
  feature-flag service or a config key, never from code, so mitigation is a flag flip, not a deploy. Name
  each flag in the runbook. Monitor flag changes with who and when, and time from alert to flip.
- **Admission-time bulkheads.** Give each request class its own concurrency pool (for example a separate
  semaphore or worker pool for batch with a hard cap) and reject over-limit batch work with 503 and a
  `Retry-After` before any model call. Monitor shed rate per class and interactive p95 during batch runs.
- **Projected-burn budget job.** Every few minutes compute `spent + current_rate × hours_left` per feature
  and tenant, and write the resulting level into the health snapshot. Monitor projected versus actual
  end-of-day spend and hours spent at each budget-driven level.
- **Synthetic checks per level.** Run a canary query against each level on a schedule by forcing the level
  with a test-only header or flag, and fail the check if the non-AI index is stale or a credential is
  expired. Monitor canary success per level, not only for Level 0.
- **Level-mix SLO.** Emit the served level as a metric label on every response and set an SLO on the share
  of sessions at Level 0, alerting on burn rate. Monitor the level histogram per tenant, so one noisy
  tenant's degradation is visible.

## Trade-offs
- **Survival vs code-path cost.** The product survives incidents, but you maintain several code paths and
  an eval for each. When the AI feature *is* the product, degrade within AI levels and then be honest,
  since no non-AI path exists.
- **Trust vs visible degradation.** Honest labelling preserves trust but shows degradation to users and
  competitors; where outputs are acted on, visibility is the correct default and the trade is rarely worth
  reversing.
- **Protecting paying users vs the funnel.** Priority shedding protects paying and critical users at the
  expense of the free tier, which may be the acquisition funnel; if growth is the KPI, shed batch work
  before shedding free users.
- **Partial results vs misleading halves.** Partial results at the deadline beat nothing, but a half
  answer can mislead; in medical, legal or financial contexts return sources only rather than a truncated
  synthesis.
- **Eager triggers vs permanent degradation.** Eager triggers save cost and latency, but set too low they
  keep the product one level down most of the day without anyone noticing; hysteresis thresholds need the
  same review as the SLO.

## Pitfalls
- **Degraded paths never exercised.** Symptom: the non-AI path fails with an expired credential or stale
  index at the exact moment of the incident.
- **Silent degradation with no time-in-level metric.** Symptom: weeks of reduced answers discovered from
  quality complaints or a bill that dropped for no known reason.
- **Flapping between levels.** Symptom: a user gets a fast reduced answer, then a slow full one, then a
  cached one within a single session, which reads as randomness.
- **Degrading to something more expensive,** such as retries or a larger fallback model. Symptom: cost
  rises during the incident instead of falling.
- **A kill switch that requires a deploy.** Symptom: a 40-minute mitigation for a 2-minute decision.
- **Triggers measured globally when load is per tenant.** Symptom: one noisy tenant degrades everyone, or
  nobody is degraded while that tenant's queue grows unbounded.

## When to use
- **Use for** any user-facing AI feature with an SLO; design the ladder alongside the non-functional
  requirements, because the levels shape the architecture (what stays deterministic, where caches live,
  which flags exist).
- **Skip the ladder for** offline batch work, which usually needs only retry-later.
- **Agree degraded behaviour before go-live** with product and, in customer deployments, with the customer.
- **Measure:** time per level, outcomes per level and recovery time; set an SLO on the share of sessions
  served at full quality.
- **Interviews:** distinguish degradation from fallback, then describe the ladder, triggers with
  hysteresis, priority shedding, honest labelling and the discipline of exercising the degraded paths.

## Role lens
- **Engineer:** Implements levels as flagged code paths with deadlines, emits the level on every response,
  and keeps a test per level in CI so the non-AI path cannot rot unnoticed.
- **Architect:** Writes the ladder and priority classes into the NFRs, decides what "acceptable degraded"
  means per feature, and owns the kill-switch and hysteresis policy.
- **FDE:** Agrees with the customer what their users will see when the model is down, configures the
  tenant-level triggers, and runs the drill before launch.

## Check yourself
**Q:** A customer says "the assistant must never show an error". Design the degradation for a support
assistant and say what you would push back on.
**A:** Propose a ladder: full RAG with the strong model; reduced with a small model and three passages;
cached answers scoped to the tenant; search results with canned links; and a human handoff form.
Triggers come from breaker state, p95 latency, queue depth and the daily budget, with hysteresis so the
level steps down fast and recovers slowly; free-tier and batch traffic shed first; every level labelled
in the interface and recorded as a metric. Push back on "never": at the bottom of the ladder the product
must show a clear message and a path to a human, because an unlabelled, ungrounded answer is worse than
an honest unavailability and the customer will own the consequences of it. Replace the absolute with an
SLO, for example the share of sessions served at full quality, and agree the drill that proves each level
works.

## Interview questions
### Why is a model fallback not enough on its own to keep an AI feature available?
- **A fallback changes the provider, not the function:** it handles a provider outage, but not overload,
  budget exhaustion or a latency SLO the fallback also misses.
- **Degradation changes the function** to something cheaper and simpler (smaller model, cache, non-AI path,
  honest message), so an incident lowers spend instead of raising it.
- **AI adds a trust failure** too: a produced answer that failed its grounding check should be demoted to
  sources-only, not shown.

### Users report answers flipping between fast and slow, short and full, within one session. What is wrong?
- **No hysteresis:** a single threshold near the signal's typical value toggles the level on every sample,
  which users read as randomness.
- **Fix:** separate enter and exit thresholds plus a dwell time, for example enter Reduced above a p95 of
  4 s and leave only after ten minutes under 2.5 s.
- **Compute the level in one background task** so every request in a window sees the same level.

### Why trigger on projected budget burn rather than spend so far?
- **Spend so far fires too late:** on a heavy day at $40 per hour from $150 at 10:00, a $500 cap is hit around
  18:45 and everyone drops straight to Cached.
- **Projected burn fires at 10:00:** 150 + 40 × 14 = $710 exceeds the cap, so stepping to Reduced early (halving
  the rate gives $430) keeps the cap without a cliff.

### When would you not build a full degradation ladder?
- **Offline batch work** usually needs only retry-later, since no user is waiting.
- **When the AI feature is the product,** no non-AI path exists, so degrade within AI levels and then be
  honest.
- **In medical, legal or financial contexts,** skip partial synthesis as a level and return sources only,
  because a half answer can mislead.

### How do you prove each degraded level actually works before an incident?
- **Exercise it routinely:** send a small share of traffic through each level and run synthetic checks that
  fail on a stale non-AI index or an expired credential.
- **Make every level a runtime flag** named in the runbook, so mitigation is a flag flip, not a deploy.
- **Hold game days** and track time per level, outcomes per level and time to recover to full.

## Quiz
### Q: What is the difference between a fallback and graceful degradation?
- [ ] Fallbacks are automatic, degradation is manual
- [x] A fallback keeps the same function on a different target; degradation changes the function to a reduced, honest form
- [ ] Fallbacks apply to models, degradation applies only to databases
- [ ] They are the same thing with different names
> Switching provider or region preserves what the user gets. Degradation steps down to a smaller model,
> a cache, a non-AI path or a clear message. Both can be automatic and both apply to any dependency.

### Q: Why do degradation triggers need hysteresis?
- [ ] To reduce the cost of the health checks
- [ ] To make the full level recover as fast as possible
- [x] To stop the system flapping between levels, stepping down quickly but stepping up only after sustained health
- [ ] To ensure free-tier users are always degraded first
> A trigger that flips on every noisy sample produces alternating fast and slow, reduced and full
> answers inside one session. Asymmetric thresholds keep the system stable one level down until the
> signal has genuinely recovered.

### Q: Which practice most determines whether degradation actually works during an incident?
- [ ] Choosing the largest available fallback model
- [ ] Increasing timeouts so more requests complete
- [x] Routinely exercising every degraded path and controlling levels with flags that need no deploy
- [ ] Adding more retries before stepping down
> Unused paths rot and deploy-gated switches are slow. Routine traffic through each level, synthetic
> checks and runtime flags are what make the ladder real; bigger models, longer timeouts and more
> retries typically make the incident worse.
