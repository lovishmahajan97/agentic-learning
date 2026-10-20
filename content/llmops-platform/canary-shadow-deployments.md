---
id: canary-shadow-deployments
module: llmops-platform
name: Canary / shadow deployments
kind: pattern
difficulty: core
est_minutes: 15
status: crisp
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
Shadow runs a candidate model, prompt or agent on copied live traffic without serving it; canary serves
it to a small sticky slice and ramps while guard metrics hold. Together they turn "ship and hope" into
measured exposure.

## Why it exists
- **Offline evals only say "probably better".** They score a few hundred curated cases; they cannot tell
  you what the candidate does on live traffic.
- **An AI change is non-local.** Swapping the model, editing three lines of the system prompt or
  tightening a tool schema changes *every* request, including the long tail your golden set never
  sampled: odd phrasings, an untested language, longer-than-expected tool outputs, a tenant whose
  documents are all scanned PDFs. A service change touches one code path a unit test proves; an LLM
  change touches a probability distribution only traffic reveals.
- **"The eval gate passed, deploy to 100%" fails three ways.** The golden set is small and ages (it
  reflects what users asked when it was built). Judge scores are noisy, so a two-point gain on 300 cases
  is often within noise. Some regressions only exist in production: cold prompt caches, rate limits on
  the new model, latency under real concurrency, a tool behaving differently with real credentials.
- **Big-bang puts everyone in the blast radius** for the hours a quality regression takes to show up in
  feedback.
- **The two patterns buy different information at bounded cost.** Shadow buys a like-for-like comparison
  with no exposure but no user feedback; canary buys real user outcomes at the price of real exposure.

## How it works
### What a "version" is
The rollout unit is a **bundle**, not a model, addressable as one immutable unit:

| Bundle part | Example |
|---|---|
| Model id | pinned snapshot |
| Prompt template | version |
| Decoding parameters | temperature, max tokens |
| Tool schemas | versioned |
| Retrieval index | version |
| Guardrail configuration | version |

- **Why immutable:** a canary that mixes a new prompt with an old index tests a combination you never
  evaluated and cannot roll back to.

### Shadow (traffic mirroring)
1. **Serve normally.** The gateway serves the request with the incumbent as usual.
2. **Fork asynchronously.** It sends a copy to the candidate after the user response is on its way, so
   the user never waits on the candidate.
3. **Log both under one trace id**, with latency, tokens, cost, schema validity, refusal flags and tool
   calls.
4. **Score pairs offline:** deterministic checks on 100% of pairs, LLM-judge pairwise preference on a
   sample, and disagreement analysis (where the two differ most, by intent or tenant).

Shadow is safe *because* nothing the candidate produces leaves the system. That same property is its
limit:
- **Side-effecting tools must be stubbed or sandboxed**, because an agent in shadow must not send the
  email twice.
- **Multi-turn effects are invisible**, because the user's next message responds to the incumbent's
  answer, not the candidate's.
- **No user feedback**, so shadow answers "is it different, cheaper or slower", not "do users prefer it".
- **Cost doubles on the mirrored slice**, so most teams shadow 5–20% of traffic (illustrative) or shadow
  100% for a short window.

**The LLM-specific trap: the incumbent disagrees with itself.**
- At non-zero temperature, and often even at temperature zero on hosted APIs, the same request run twice
  yields different text.
- So "B differs from A on 30% of requests" is meaningless until you know A-vs-A differs on, say, 25%.
- **Fix:** mirror a small slice to a second copy of the *incumbent* too, and read the candidate's
  disagreement as the excess over that self-disagreement floor, broken down by intent.

### Canary (partial live exposure)
1. **Sticky assignment.** The router hashes a stable key (usually user or session id) into a bucket and
   routes buckets below the canary percentage to the candidate. Hashing *because* a conversation must not
   flip models mid-thread; random per-request assignment produces incoherent sessions and contaminates
   the comparison.
2. **Watch guard metrics on both arms** over a bake window: error rate, p95 latency, schema-failure rate,
   refusal rate, cost per request, tool-failure rate, and a quality proxy you trust (thumbs-down rate,
   escalation rate, judge score on a sample).
3. **Ramp in steps**, for example 1% → 5% → 25% → 50% → 100%, each held long enough to collect a
   minimum sample. Small first steps *because* the first step exists to catch catastrophic breakage
   (wrong schema, 429s from the new model's quota) with the fewest users exposed.
4. **Roll back automatically** on any guard breach by flipping the pointer to the incumbent. Promotion to
   100% requires success metrics that are not worse, not merely that nothing exploded.

### What the numbers can and cannot tell you
Detecting a shift of size δ in a metric with standard deviation σ needs, per arm, roughly

```
n ≈ 16 · σ² / δ²        (rule of thumb: two-sided α = 0.05, 80% power)
σ² = p(1 − p) for a rate p
```

- **Halving the effect quadruples the traffic** you need.
- **A 1% canary** on 20,000 requests a day gives 200 canary requests a day.

| Signal | Inputs | Samples per arm | Time at 1% |
|---|---|---|---|
| Schema failures 0.5% → 5% | σ² ≈ 0.027 at the midpoint, δ = 0.045 | 16 × 0.027 / 0.002 ≈ 210 | about a day, faster in practice because the incumbent baseline is known precisely |
| Judge score 4.1 → 4.0 | σ ≈ 0.8 | 16 × 0.64 / 0.01 ≈ 1,000 | a week (an afternoon at 25%) |

- **So early steps are for catastrophic signals**; later, larger steps are where quality comparisons
  become meaningful.
- **It is a controlled experiment only** when arms are assigned randomly and compared on the same window.
  Comparing this week's canary to last week's baseline confounds the version with time.

### Canary vs A/B test vs blue-green
These are often conflated.

| | Question | Decision | Ends |
|---|---|---|---|
| **Canary** (safety mechanism) | "Is anything broken?" | asymmetric: roll back on harm, promote on *no harm* | at 100% |
| **A/B test** (decision mechanism) | "Which is better on the success metric?" | either arm may win; fixed split until the pre-registered sample is reached | at the planned sample |
| **Blue-green** | "Swap environments" | flip all traffic at once; instant rollback | immediately |

- **A large canary step can double as an A/B test** only if you pre-commit to the metric and sample size.
- **Blue-green has no gradual exposure**, so it suits infrastructure changes better than behaviour
  changes.

### Where it lives
- **Routing, hashing, mirroring and the rollout percentage** belong in the AI gateway or control plane as
  configuration, not in application code, so a rollout or rollback is a config change with no deploy.

## Diagram
Every request is bucketed by a salted user hash; incumbent-served requests may also be mirrored to the
shadow candidate with side effects off, and guard metrics on both arms drive the ramp or rollback.

```mermaid
flowchart TD
    R["Request + user id"] --> H["Hash bucket 0..99 with salt"]
    H -->|"bucket below canary pct"| C["Canary bundle"]
    H -->|"otherwise"| I["Incumbent bundle"]
    C --> U["Response to user"]
    I --> U
    I -.->|"async fork, tools off"| S["Shadow candidate"]
    S --> L[("Paired log by trace id")]
    L --> J["Pair scoring and disagreement"]
    C --> G["Guard metrics, both arms"]
    I --> G
    G -->|"breach"| RB["Rollback pointer to incumbent"]
    G -->|"holds with min sample"| RP["Ramp to next step"]
```

## Examples
### Gateway router with sticky canary and async shadow
A minimal gateway router: sticky canary assignment by user hash, an asynchronous shadow fork for a
separate candidate, and paired logging.
- **The shadow call is a background task**, so the user's response never waits on it.
- **Only incumbent-served requests are mirrored**, so every shadow pair is candidate-vs-incumbent.
- **The shadow candidate runs with side-effecting tools disabled.**
- **Each experiment salts the hash**, so the same 5% of users are not the guinea pigs for every rollout.

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

### Ramp planner: how long each step must bake
Pure Python; plug in your traffic and the effect you need to see before trusting a step.

```python
import math

def n_per_arm(var: float, delta: float) -> int:
    return math.ceil(16 * var / delta ** 2)          # two-sided alpha 0.05, 80% power

def days_at(n: int, daily_requests: int, pct: float) -> float:
    return n / (daily_requests * pct / 100)

p_mid = (0.005 + 0.05) / 2
schema_n = n_per_arm(p_mid * (1 - p_mid), 0.045)     # rate 0.5% -> 5%
judge_n = n_per_arm(0.8 ** 2, 0.1)                   # score 4.1 -> 4.0, sigma 0.8

for pct in (1, 5, 25):
    print(f"{pct:>2}%: schema {days_at(schema_n, 20_000, pct):.2f} d, "
          f"judge {days_at(judge_n, 20_000, pct):.2f} d")
# schema_n = 212, judge_n = 1024
#  1%: schema 1.06 d, judge 5.12 d
#  5%: schema 0.21 d, judge 1.02 d
# 25%: schema 0.04 d, judge 0.20 d
```

| Step | What it can detect in a day at 20,000 req/day | Use it for |
|---|---|---|
| 1% | a 10x schema-failure jump, 429s, timeouts | catastrophic breakage |
| 5% | the same within hours; judge shifts of 0.1 in about a day | early quality read |
| 25% and up | 0.1-point judge shifts within an afternoon | promotion decision |

## Production patterns
- **Mesh-level mirroring for stateless calls.** When the candidate has no side effects, mirror at the
  proxy: Istio's `VirtualService` supports a `mirror` destination with a `mirrorPercentage`, and Envoy has
  request mirror policies. Mirrored responses are discarded. Monitor candidate error rate and the cost of
  the mirrored slice. For agents with tools, mirror in the application so tools can be disabled.
- **Declarative canary steps with automated analysis.** Argo Rollouts expresses the ramp as `setWeight`
  and `pause` steps and can run an analysis template against Prometheus queries between steps, aborting on
  failure. Monitor the analysis run results per step, not just the final state.
- **Self-disagreement baseline.** Send a small slice (for example 1-2%, illustrative) of shadow traffic to
  a second incumbent replica and report candidate disagreement as excess over A-vs-A, per intent. Monitor
  the A-vs-A floor itself; if it jumps, the provider changed something.
- **Minimum-sample guards.** Encode "roll back if refusal rate exceeds baseline by X *and* n ≥ N" so
  quality alerts cannot fire on 150 samples; fire hard-error guards (5xx, schema failures, 429s)
  immediately. Monitor how often a rollback was later judged a false alarm.
- **Percentage flags keyed on a salted user id.** Feature-flag systems with percentage rollouts by user
  attribute (LaunchDarkly, Unleash and similar) give sticky assignment and a one-click kill switch without
  a deploy. Monitor the actual cohort size against the configured percentage.

## Trade-offs
- **Shadow: zero exposure vs double cost and blind spots.** You get a like-for-like comparison, but pay
  roughly double inference on the mirrored slice and learn nothing about user preference or multi-turn
  behaviour.
- **Canary: real outcomes vs real exposure.** Every percent is real users seeing a possibly worse system;
  once the change is low-risk and well-evaluated offline, the extra caution costs more than it saves.
- **Sticky assignment: clean experiment vs concentrated harm.** Conversations stay coherent, but a bad
  canary hits the same users repeatedly, so a 5% canary is a 100% regression for those users.
- **Small early steps: bounded blast radius vs slow rollout.** For a hard deadline (a vendor deprecation
  date) you may accept larger steps and lean on shadow results instead.
- **Automatic rollback: no humans at 3 a.m. vs false alarms.** With noisy quality metrics and small
  samples it will roll back good releases unless thresholds carry a minimum sample.

## Pitfalls
- **Shadowing an agent with live tools.** Symptom: duplicate emails, tickets or payments with no user
  action behind them. Fix: stub or sandbox side-effecting tools in the shadow arm.
- **Random per-request routing instead of per-session hashing.** Symptom: users report the assistant
  "changed personality" mid-conversation while aggregate metrics look fine. Fix: hash user or session id.
- **Comparing the canary to a historical baseline.** Symptom: a "regression" that disappears once you
  notice the canary window included a product launch. Fix: a concurrent control on the same window.
- **Judging quality after 150 samples.** Symptom: rollbacks and promotions that flip day to day with no
  change in the system. Fix: compute the required sample first.
- **Ignoring warm-up effects.** The candidate has a cold prompt cache and a fresh rate-limit bucket.
  Symptom: a latency and 429 spike in the first minutes that disappears, then gets blamed on the version.
  Fix: pre-warm and exclude a short warm-up window.
- **Canarying only internal users.** Symptom: a clean canary, then complaints from the segment that writes
  in a different language or uploads different documents. Fix: a cohort representative of real traffic.
- **Reading shadow disagreement without an A-vs-A baseline.** Symptom: a "40% disagreement" alarm that is
  ordinary sampling variation in both versions. Fix: measure the self-disagreement floor.

## When to use
- **Shadow first when the change is large or structurally different:** a new model family, a new
  provider, a rewritten agent loop, a new retrieval pipeline. It tells you whether the candidate is safe
  enough to show anyone and where it disagrees with the incumbent, which is where to look.
- **Canary when you need user outcomes** to decide, or when the change is incremental and offline evals
  already pass.
- **Skip both** for trivial, fully reversible changes with strong offline coverage, where the process
  costs more than the risk.
- **Measure:** guard metrics on both arms; demand a minimum sample before any quality verdict; automate
  the rollback path before the first ramp.
- **Interviews:** explain why sticky assignment matters, why shadow cannot test multi-turn agents, and how
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

## Interview questions
### What is the difference between a shadow deployment and a canary, and when do you use each?
- **Shadow** mirrors live requests to the candidate and discards its output: zero user exposure, like-for-
  like comparison, but no user feedback and no multi-turn signal.
- **Canary** serves the candidate to a small sticky cohort and ramps on guard metrics: real outcomes at
  the price of real exposure.
- **Order:** shadow first for structurally new changes (new provider, new agent loop), then canary for
  user outcomes; skip both for trivial reversible changes.

### Your shadow report says the candidate disagrees with production on 35% of requests. Is that bad?
- **Not yet knowable.** LLM outputs vary run to run, often even at temperature zero on hosted APIs, so the
  incumbent disagrees with itself.
- **Measure the floor:** mirror a slice to a second incumbent copy. If A-vs-A is 30%, the real signal is
  the 5-point excess, and where it concentrates by intent.
- **Then read the pairs** in the high-excess intents with a pairwise judge and a human spot check.

### A 1% canary looked clean for two days. Why might you still not promote?
- **Sample size.** At 20,000 requests a day, 1% gives 200 per day; a 0.1-point judge shift with σ ≈ 0.8
  needs about 1,000 per arm, so two days cannot resolve it.
- **Asymmetric bar.** "Nothing exploded" clears catastrophic risk only; promotion needs success metrics
  not worse at a step large enough to measure them, such as 25%.
- **Cohort coverage.** Check the hashed cohort includes the languages, tenants and document types of real
  traffic.

### How would you shadow an agent that can send emails and issue refunds?
- **Disable or stub side effects in the shadow arm**, recording the intended tool calls instead, because
  a mirrored agent must not act twice.
- **Compare the intended tool-call mix** and arguments against the incumbent's actual calls.
- **Accept the blind spot:** real refund behaviour can only be observed in canary, so give refund rate
  the tightest guard and the smallest first step.

### When is a canary the wrong tool and a pre-registered A/B test the right one?
- **When the question is "which is better", not "is anything broken".** A canary is asymmetric and ends
  at 100%; an A/B test holds a fixed split until a pre-registered sample is reached and either arm may
  win.
- **Example:** choosing between two prompt styles that both pass safety; you need a success metric and a
  sample size committed in advance, otherwise you are peeking at noise.

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
