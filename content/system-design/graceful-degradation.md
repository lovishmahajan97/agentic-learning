---
id: graceful-degradation
module: system-design
name: Graceful degradation
kind: pattern
difficulty: core
est_minutes: 14
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
When the AI path is failing, slow, over budget or overloaded, the product should step down to a
cheaper, simpler and honestly labelled version of itself rather than fall over. Graceful degradation is a
designed ladder (full → reduced → cached → non-AI → clear unavailability) with explicit triggers,
hysteresis, labelled output and degraded paths that are exercised so they still work when needed.
Fallbacks keep the function by changing the provider; degradation keeps the user moving by changing the
function.

## Why it exists
Most AI features are enhancements on a product that worked before them: search, support, editors,
dashboards. A model incident, an exhausted rate limit, a runaway cost day or a traffic spike will happen.
If the feature's failure also breaks the workflow around it, you have turned an enhancement into a new
failure point and lowered the availability of the whole product. Users who could have found the document
themselves now see a spinner.

The naive responses each miss something. "Retry and show an error" makes the user wait and then gives
them nothing. "Fall back to another model" handles a provider outage but not overload, budget exhaustion
or a latency SLO that the fallback also misses. "Turn the feature off" is a single binary switch, so the
smallest incident costs the whole feature. There is also an AI-specific failure the others ignore: a
response that was produced but should not be trusted, because the grounding check failed or the judge
rejected it. The right move there is to show less and say why, not to fail.

Load shedding and priority classes came from site reliability engineering; AI systems add a new axis,
quality levels, because the same request can be answered well, adequately, or not at all, at different
costs. Degradation is the discipline of deciding those levels before the incident.

## How it works
### Define the ladder per feature
- **Level 0, full:** the strong model, tools, reranking, a verifier.
- **Level 1, reduced:** a smaller model, fewer retrieved passages, no reranker, a lower `max_tokens`,
  agent loops capped at one step. Visibly still an answer; cheaper and faster.
- **Level 2, cached or precomputed:** a cached answer to a similar question, a precomputed summary, an
  FAQ entry, scoped to the same tenant.
- **Level 3, non-AI:** keyword search results, a template, the raw documents, a form. The product as it
  was before the feature.
- **Level 4, honest unavailability:** a clear message, the rest of the product working, and a way to
  proceed (a human handoff, a link, try later).
For each level write down what the user sees, what it costs, and who gets it.

### Triggers
Dependency health (breaker state, error rate, p95 latency), load (queue depth, concurrency), budget burn
(spend today against the cap), rate-limit headroom, quality signals (a failed grounding check demotes
*this* response to sources-only), and a human-operated kill switch. Evaluate triggers where the decision
is made and with hysteresis: step down quickly on a bad signal, step back up only after the signal has
been healthy for a sustained period, *because* flapping between levels is worse for users than staying
one level down.

### Shed by priority, isolate by class
Classify requests: interactive versus batch, paying versus free, critical versus nice-to-have. Shed
batch and low tiers first and keep a reserved share of capacity for critical interactive traffic in its
own bulkhead, so a batch backfill can never starve the chat box.

### Deadlines and partial results
Give each request a deadline and return what you have when it expires: retrieved passages without the
synthesis, a streamed partial answer with a visible marker. Streaming makes partial output useful rather
than confusing, but partial answers are dangerous where a half-statement misleads; there, sources-only is
the right degraded form.

### Honesty in the interface
Label degraded output ("Showing matching documents; the AI summary is unavailable"). Never present a
Level 1 answer as Level 0 when the quality difference matters, since users act on outputs and silent
degradation transfers the risk to them.

### Keep the degraded paths alive
A non-AI path that nobody runs rots: its index goes stale, its credentials expire, its flag is removed.
Send a small share of traffic through each level routinely, run synthetic checks against them, and hold
game days. Every level sits behind a flag that flips without a deploy, and the runbook names the flags.

### Measure
Time spent at each level, requests served per level, user outcomes per level (completion rate, handoff
rate), and time to recover to full. "Percentage of sessions served at Level 0" is a good SLO.

## Example
A support-answer endpoint with a degradation ladder. A background task refreshes `h`, a health snapshot,
every few seconds; the handler picks a level, enforces a deadline, and steps down rather than raising
when a stage fails. `keyword_or_vector_search`, `generate`, `semantic_cache_lookup` and `ModelError` are
your adapters.

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
    docs = await keyword_or_vector_search(q, k=20 if level == Level.FULL else 5)
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

The `level` field in every response is the metric: a histogram of it over time is the dashboard that
shows whether the product has been quietly running degraded.

## Trade-offs
- The product survives incidents, but you maintain several code paths and the eval for each; when the
  AI feature *is* the product, degrade within AI levels and then be honest, since no non-AI path exists.
- Honest labelling preserves trust but makes degradation visible to users and competitors; where outputs
  are acted on, visibility is the correct default and the trade is rarely worth reversing.
- Priority shedding protects paying and critical users at the expense of the free tier, which may be the
  acquisition funnel; if growth is the KPI, shed batch work before shedding free users.
- Partial results at the deadline beat nothing, but a half answer can mislead; in medical, legal or
  financial contexts return sources only rather than a truncated synthesis.
- Eager triggers save cost and latency, but set too low they keep the product one level down most of the
  day without anyone noticing; hysteresis thresholds need the same review as the SLO.

## Pitfalls
- Degraded paths never exercised; the symptom is the non-AI path failing with an expired credential or
  stale index at the exact moment of the incident.
- Silent degradation with no time-in-level metric; the symptom is weeks of reduced answers discovered
  from quality complaints or a bill that dropped for no known reason.
- Flapping between levels; the symptom is a user getting a fast reduced answer, then a slow full one,
  then a cached one within a single session, which reads as randomness.
- Degrading to something more expensive, such as retries or a larger fallback model; the symptom is
  cost rising during the incident instead of falling.
- A kill switch that requires a deploy; the symptom is a 40-minute mitigation for a 2-minute decision.
- Triggers measured globally when load is per tenant; the symptom is one noisy tenant degrading
  everyone, or nobody being degraded while that tenant's queue grows unbounded.

## When to use
Design the ladder for any user-facing AI feature with an SLO, at the same time as the non-functional
requirements, because the levels shape the architecture (what stays deterministic, where caches live,
which flags exist). Offline batch work usually needs only retry-later. Agree the degraded behaviour with
product and, in customer deployments, with the customer, before go-live. Measure time per level,
outcomes per level and recovery time, and set an SLO on the share of sessions served at full quality. In
interviews, distinguish degradation from fallback, describe the ladder, the triggers with hysteresis,
priority shedding, honest labelling and the discipline of exercising the degraded paths.

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
