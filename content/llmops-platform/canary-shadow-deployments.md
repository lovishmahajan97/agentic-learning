---
id: canary-shadow-deployments
module: llmops-platform
name: Canary / shadow deployments
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
related:
  - llmops-platform/rollback
  - llmops-platform/evaluation-gates-for-production-releases
  - llmops-platform/ci-cd-for-prompts-and-models
  - evals/a-b-testing
  - evals/offline-vs-online-evaluation
  - customer-data-evaluation/shadow-mode-validation
  - deployment/rollout-criteria-and-rollback-triggers
  - system-design/ai-gateway
sources:
  - Kohavi, Tang & Xu, 2020 — Trustworthy Online Controlled Experiments
  - Humble & Farley, 2010 — Continuous Delivery
---

## TL;DR
Offline evals tell you a new model, prompt or agent version is *probably* better on a few hundred
curated cases; they cannot tell you what it does on live traffic. A **shadow** deployment runs the
candidate on a copy of real requests without serving its answers, so you can compare it to the
incumbent at zero user risk. A **canary** serves the candidate to a small, sticky slice of users and
ramps up only while guard metrics hold. Together they turn "ship and hope" into a measured exposure.

## Why it exists
A change to an AI system is non-local. Swapping the model, editing three lines of the system prompt or
tightening a tool schema changes behaviour on *every* request, including the long tail your golden set
never sampled: odd phrasings, a language you did not test, tool outputs that are longer than expected,
a tenant whose documents are all scanned PDFs. A conventional service change touches one code path and
a unit test proves it; an LLM change touches a probability distribution and only traffic reveals it.

The naive approach is "the eval gate passed, deploy to 100%". It fails for three reasons. The golden
set is small and ages: it reflects what users asked when it was built. Judge-based scores are noisy, so
a two-point gain on 300 cases is often within noise. And some regressions are invisible offline because
they depend on production conditions: cold prompt caches, rate limits on the new model, latency under
real concurrency, or a tool that behaves differently with real credentials. A big-bang deploy puts the
whole user base inside the blast radius for the hours it takes a quality regression to show up in
feedback.

Shadow and canary deployments exist to buy information about live behaviour at a bounded cost. They
differ in what they buy: shadow buys a like-for-like comparison with no exposure but no user feedback;
canary buys real user outcomes at the price of real exposure.

## How it works
### What a "version" is
The thing you roll out is a bundle, not a model: model id (pinned snapshot), prompt template version,
decoding parameters, tool schemas, retrieval index version and guardrail configuration. Both patterns
need the bundle to be addressable as one immutable unit, because a canary that mixes a new prompt with
an old index is testing a combination you never evaluated and cannot roll back to.

### Shadow (traffic mirroring)
1. The gateway receives a request and serves it with the incumbent as usual.
2. It forks a copy to the candidate asynchronously, after the user response is on its way, so the
   user never waits on the candidate.
3. Both outputs are logged under the same trace id along with latency, tokens, cost, schema validity,
   refusal flags and tool calls.
4. An offline comparison job scores pairs: deterministic checks on 100% of pairs, LLM-judge pairwise
   preference on a sample, and disagreement analysis (where do the two differ most, by intent or tenant).

Shadow is safe *because* nothing the candidate produces leaves the system. That same property is its
limit. Side-effecting tools must be stubbed or sandboxed, because an agent in shadow must not send the
email twice. Multi-turn effects are invisible, because the user's next message responds to the
incumbent's answer, not the candidate's. And there is no user feedback at all, so shadow answers "is it
different and is it cheaper or slower", not "do users prefer it". The mirrored slice also doubles
inference cost on that slice, so most teams shadow 5–20% of traffic (illustrative) or shadow 100% for a
short window.

One more trap is specific to LLMs: the incumbent disagrees *with itself*. At non-zero temperature, and
often even at temperature zero on hosted APIs, running the same request twice yields different text. So
"B's answer differs from A's on 30% of requests" is meaningless until you know A-vs-A differs on, say,
25%. Mirror a small slice to a second copy of the *incumbent* as well, and read the candidate's
disagreement rate as the excess over that self-disagreement floor, broken down by intent.

### Canary (partial live exposure)
1. The router assigns each request to a bucket by hashing a stable key, usually user or session id, and
   routes buckets below the canary percentage to the candidate. Hashing *because* a conversation must not
   flip models mid-thread; random per-request assignment produces incoherent sessions and contaminates
   the comparison.
2. Guard metrics are watched on both arms over a bake window: error rate, p95 latency, schema-failure
   rate, refusal rate, cost per request, tool-failure rate, and whatever quality proxy you trust
   (thumbs-down rate, escalation rate, judge score on a sample).
3. The percentage ramps in steps, for example 1% → 5% → 25% → 50% → 100%, each step held long enough to
   collect a minimum sample. Small first steps *because* the point of the first step is to catch
   catastrophic breakage (wrong schema, 429s from the new model's quota) with the fewest users exposed.
4. Any guard breach triggers automatic rollback of the pointer to the incumbent; promotion to 100%
   requires that the success metrics are not worse, not merely that nothing exploded.

### What the numbers can and cannot tell you
Detecting a shift of size δ in a metric with standard deviation σ needs, per arm, roughly

```
n ≈ 16 · σ² / δ²        (rule of thumb: two-sided α = 0.05, 80% power)
σ² = p(1 − p) for a rate p
```

so halving the effect you want to see quadruples the traffic you need. A 1% canary on a service doing
20,000 requests a day gives 200 canary requests a day. A schema-failure rate jumping from 0.5% to 5%
(σ² ≈ 0.027 at the midpoint, δ = 0.045) needs about 16 × 0.027 / 0.002 ≈ 210 samples: about a day, and
in practice faster because the incumbent's baseline is known precisely. A judge score moving from 4.1 to
4.0 with σ ≈ 0.8 needs about 16 × 0.64 / 0.01 ≈ 1,000 samples per arm: a week at 1%, an afternoon at 25%.
So the early steps are for catastrophic signals, and the later, larger steps are where quality
comparisons become meaningful. Treat the canary as a controlled experiment only when the two
arms are assigned randomly and compared on the same window; comparing this week's canary to last
week's baseline confounds the version with time.

### Canary vs A/B test vs blue-green
These are often conflated. A **canary** is a safety mechanism: the question is "is anything broken?",
the decision is asymmetric (roll back on harm, promote on *no harm*), and it ends at 100%. An **A/B
test** is a decision mechanism: the question is "which is better on the success metric?", it holds a
fixed split until the pre-registered sample is reached, and either arm may win. A large canary step can
double as an A/B test only if you pre-commit to the metric and sample size. **Blue-green** keeps two full
environments and flips all traffic at once; it gives instant rollback but no gradual exposure, so it
suits infrastructure changes better than behaviour changes.

### Where it lives
Routing, hashing, mirroring and the rollout percentage belong in the AI gateway or control plane as
configuration, not in application code, so a rollout or rollback is a config change with no deploy.

## Example
A minimal gateway router: sticky canary assignment by user hash, an asynchronous shadow fork for a
separate candidate, and paired logging. The shadow call is scheduled as a background task so the user's
response never waits on it, only incumbent-served requests are mirrored (so every shadow pair is
candidate-vs-incumbent), and the shadow candidate runs with side-effecting tools disabled. Each
experiment salts the hash, so the same 5% of users are not the guinea pigs for every rollout.

```python
import asyncio, hashlib, json, time

ROLLOUT = {"incumbent": "bundle-v12", "canary": "bundle-v13", "canary_pct": 5,
           "shadow": "bundle-v14", "shadow_pct": 20, "salt": "exp-2026-10-v13"}
_background: set[asyncio.Task] = set()            # keep refs so tasks are not GC'd mid-run

def bucket(key: str, salt: str) -> int:           # stable 0..99 bucket per user per experiment
    return int(hashlib.sha256(f"{salt}:{key}".encode()).hexdigest(), 16) % 100

async def call_bundle(bundle: str, req: dict, allow_side_effects=True) -> dict:
    t = time.perf_counter()
    out = await llm_app.run(bundle, req, allow_side_effects=allow_side_effects)
    return {"bundle": bundle, "text": out.text, "schema_ok": out.schema_ok,
            "refused": out.refused, "cost": out.cost, "ms": (time.perf_counter() - t) * 1e3}

async def shadow(req: dict, served: dict, trace_id: str):
    cand = await call_bundle(ROLLOUT["shadow"], req, allow_side_effects=False)
    log.info(json.dumps({"trace": trace_id, "served": served, "shadow": cand}))

async def handle(req: dict, user_id: str, trace_id: str) -> dict:
    b = bucket(user_id, ROLLOUT["salt"])
    bundle = ROLLOUT["canary"] if b < ROLLOUT["canary_pct"] else ROLLOUT["incumbent"]
    served = await call_bundle(bundle, req)
    metrics.observe(bundle=bundle, ms=served["ms"], schema_ok=served["schema_ok"],
                    refused=served["refused"], cost=served["cost"])
    if bundle == ROLLOUT["incumbent"] and bucket(user_id, "shadow") < ROLLOUT["shadow_pct"]:
        task = asyncio.create_task(shadow(req, served, trace_id))  # off the critical path
        _background.add(task); task.add_done_callback(_background.discard)
    return served
# ROLLOUT is loaded from the control plane; promoting or rolling back is a config write, not a deploy.
```

## Trade-offs
- Shadow gives a like-for-like comparison with zero user exposure, but you pay roughly double inference
  on the mirrored slice and learn nothing about user preference or multi-turn behaviour.
- Canary gives real outcomes, but every percent of exposure is real users seeing a possibly worse
  system; once the change is low-risk and well-evaluated offline, the extra caution costs more than
  it saves.
- Sticky (hashed) assignment keeps conversations coherent and the experiment clean, but it means a bad
  canary hits the same users repeatedly, so a 5% canary is a 100% regression for those users.
- Small early steps limit blast radius but slow the rollout; for a change with a hard deadline (a vendor
  deprecation date) you may have to accept larger steps and lean on shadow results instead.
- Automatic rollback on guard metrics removes humans from the loop at 3 a.m., but with noisy quality
  metrics and small samples it will roll back good releases unless thresholds carry a minimum sample.

## Pitfalls
- Shadowing an agent with live tools; the symptom is duplicate emails, tickets or payments appearing
  with no user action behind them.
- Random per-request routing instead of per-session hashing; the symptom is users reporting that the
  assistant "changed personality" mid-conversation and metrics that look fine in aggregate.
- Comparing the canary to a historical baseline rather than a concurrent control; the symptom is a
  "regression" that disappears when you notice the canary window included a product launch.
- Judging the canary on a quality score after 150 samples; the symptom is rollbacks and promotions that
  flip from day to day with no change in the system.
- Ignoring warm-up effects: the candidate model has a cold prompt cache and a fresh rate-limit bucket;
  the symptom is a latency and 429 spike in the first minutes that disappears, then gets blamed on the
  version.
- Canarying only internal users; the symptom is a clean canary followed by a wave of complaints from
  the customer segment that writes in a different language or uploads different documents.
- Reading the shadow disagreement rate without an incumbent-vs-incumbent baseline; the symptom is a
  "40% disagreement" alarm that turns out to be ordinary sampling variation in both versions.

## When to use
Shadow first when the change is large or structurally different: a new model family, a new provider, a
rewritten agent loop, a new retrieval pipeline. Its output tells you whether the candidate is safe
enough to put in front of anyone and where it disagrees with the incumbent, which is where to look. Move
to a canary when you need user outcomes to decide, or when the change is incremental and offline evals
already pass. Skip both for trivial, fully reversible changes with strong offline coverage, where the
cost of the process exceeds the risk. Measure guard metrics on both arms, demand a minimum sample before
any quality verdict, and automate the rollback path before the first ramp. In interviews, the expected
depth is to explain why sticky assignment matters, why shadow cannot test multi-turn agents, and how
sample size limits what a 1% canary can detect.

## Role lens
- **Engineer:** Implements the hashed router and asynchronous mirroring in the gateway, tags every trace
  with the bundle id, and builds the paired-comparison job that scores shadow output.
- **Architect:** Decides what the deployable bundle is, where rollout state lives (control plane, not
  code) and which guard metrics gate each ramp step for which risk class of change.
- **FDE:** Runs shadow mode against a customer's live traffic before cutover so the customer sees a
  disagreement report instead of a surprise, and negotiates which users form the first canary cohort.

## Check yourself
**Q:** You are migrating a customer-support agent from model A to model B. The agent can issue refunds.
Offline evals show B slightly ahead. Design the rollout and justify each step.
**A:** Start with shadow at a meaningful slice of traffic with the refund tool disabled for the shadow
arm, because a side-effecting tool cannot be mirrored safely. Score pairs on deterministic checks
(schema validity, refusal rate, tool-call mix, latency, cost) at 100% and judge preference on a sample;
analyse where B disagrees most with A by intent. If nothing is alarming, canary B to about 1% of
sessions by user hash, with automatic rollback on error rate, schema failures, latency and
refund-rate anomalies; hold until a minimum sample accrues, then ramp through 5, 25 and 50%. Use
thumbs-down and escalation rate as success metrics at the larger steps, where samples allow it. Keep
A's bundle warm and the routing in control-plane config so rollback is a pointer flip, and note that
B's refund behaviour in canary is the one thing shadow could not test, so it gets the tightest alert.

## Quiz
### Q: Why is a shadow deployment unable to evaluate a multi-turn conversational agent properly?
- [ ] Because shadow traffic cannot be logged alongside the served response
- [x] Because the user's next turn responds to the incumbent's answer, so the candidate never gets the conversation it would have produced
- [ ] Because shadow deployments only work for stateless classification models
- [ ] Because the candidate's latency makes the mirrored copy time out
> Shadow forks the same input to both versions, but a conversation diverges after the first reply.
> The candidate sees follow-ups shaped by the incumbent, so its multi-turn behaviour is never observed.

### Q: A team runs a 1% canary on a service with 10,000 requests per day and wants to know within a day whether the judge score moved from 4.2 to 4.1. What is the right assessment?
- [ ] Sufficient: 100 samples is enough for any metric
- [ ] Sufficient, provided the judge runs at temperature zero
- [x] Insufficient: detecting a small shift in a noisy score needs far more samples; the 1% step is for catching catastrophic failures
- [ ] Insufficient only because the canary should run for a week regardless of traffic
> Sample size needed grows roughly with the inverse square of the effect size. A 100-request slice can reveal a schema-failure spike but cannot resolve a 0.1-point judge difference; use larger ramp steps for quality verdicts.

### Q: Why should canary assignment hash on user or session id rather than pick randomly per request?
- [ ] Hashing is faster than random number generation at gateway scale
- [ ] Random assignment would exceed the canary percentage over time
- [x] Per-session stickiness keeps conversations coherent and prevents the arms from contaminating each other's results
- [ ] Hashing lets the canary cohort be chosen from internal users only
> If consecutive turns of one conversation hit different versions, users see inconsistent behaviour and each arm's metrics include context produced by the other arm, which invalidates the comparison.
