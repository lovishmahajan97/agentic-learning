---
id: model-routing-cascading
module: system-design
name: Model routing / cascading
kind: pattern
difficulty: core
est_minutes: 15
related:
  - system-design/model-selection
  - system-design/slm-vs-llm-trade-offs
  - system-design/multi-model-architecture
  - system-design/ai-gateway
  - system-design/model-fallback-strategies
  - infra-capacity-economics/quality-latency-cost-optimization
  - evals/llm-as-a-judge
  - infra-capacity-economics/cost-modeling
sources:
  - Chen, Zaharia, Zou, 2023 — FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance
  - Ong et al., 2024 — RouteLLM: Learning to Route LLMs with Preference Data
---

## TL;DR
Requests vary enormously in difficulty, so sending everything to the strongest model overpays and
sending everything to the cheapest underdelivers. Routing picks a model per request *before* the call,
using rules, a classifier or a learned router; cascading calls the cheap model first and escalates only
when a quality check fails. Both trade some router or check error and extra tail latency for large cost
savings, and both are only as good as the quality signal that drives them.

## Why it exists
The price gap between a small model and a frontier model is commonly one to two orders of magnitude per
token (treat that as an illustrative range; it changes with every price sheet), and the latency gap is
several-fold. Yet in most products a large share of requests are easy: a lookup, a reformulation, a
yes/no classification, a short summary. Paying frontier prices for those is waste, and it is waste that
scales linearly with success.

The naive alternatives all fail in a specific way. One big model for everything makes cost and latency
the product's ceiling. One small model for everything fails on the hard tail, and the hard tail is
exactly the set of requests users remember and escalate. Letting a product manager choose a model per
feature is better but coarse: within a single feature, difficulty still varies by an order of magnitude,
so the per-feature choice is always too expensive for some requests and too weak for others.

What forced the pattern into existence is that the decision has to be made per request, and it has to
be made cheaply. FrugalGPT showed that a cascade of models with a learned "is this answer good enough"
scorer can match the strongest model's accuracy on several benchmarks at a fraction of its cost;
RouteLLM showed that a router trained on human preference data can send most traffic to a cheaper model
with little quality loss. The engineering question is where the quality signal comes from.

## How it works
### Routing: decide before the call
The router sees the request and its metadata (feature, tenant tier, language, length, conversation
state) and returns a model. In rough order of sophistication:

1. **Static rules.** Feature or tenant maps to a model. Zero latency, no learning; the right first step.
2. **Heuristics.** Token count, presence of code, number of constraints in the instruction, whether
   tools are needed. Cheap and surprisingly effective for coarse splits.
3. **Learned classifier.** An embedding plus a small classifier trained on past traffic labelled with
   "which model was needed". Labels come from offline evals: run both models, judge the small model's
   answer, and record whether it passed.
4. **LLM-as-router.** Ask a small model to grade difficulty. Useful when you have no labels yet, but it
   adds a model call to every request.

Routers make two kinds of error. Routing a hard request down costs quality; routing an easy request up
costs money. The threshold is set by the cost ratio and the quality penalty you are willing to accept,
tuned on an eval set, and then monitored per route in production because the error rates drift as prompts
and models change. The router's own cost must be small next to the saving *because* it runs on every
request: a 30 ms classifier is free, a 2 s LLM call usually is not.

### Cascading: decide after a cheap attempt
Call the small model; run a check; return if it passes, escalate if it fails. The check is the whole
design. Options, from cheapest to most expensive: a deterministic gate (JSON parses, required fields are
present, the cited document id exists, the generated SQL executes); agreement across *n* sampled answers
(disagreement is a usable proxy for uncertainty); a verifier model or LLM judge; a grounding check against
retrieved passages. The small model's self-reported confidence is the tempting option and the least
reliable one, because models are poorly calibrated about their own correctness.

Expected cost per request is `c_small + c_check + p_escalate × c_large`. With illustrative units of
1 for the small model, 0.5 for a judge and 15 for the large model, and a 25% escalation rate, a cascade
costs 1 + 0.5 + 3.75 = 5.25 units against 15 for always-large, a 65% saving. The price is latency: the
25% of requests that escalate pay the small call, the check and the large call *in series*, so p95
latency gets worse even as mean cost falls.

### Routing versus cascading
Routing makes one call, so latency is bounded, but its risk is prediction error and it needs labelled
data to train. Cascading needs no training data and has a quality floor set by the check, but it pays
serial latency and the check's cost on escalations. In practice they combine: route the obvious cases,
cascade the ambiguous band in the middle.

### Operating it
Log the route taken and why. Evaluate quality *per route*, not only in aggregate, since a healthy
average can hide a failing hard tail. Keep a small random slice (a few per cent) always served by the
strong model as a continuous baseline, so you can see the quality gap move when a provider silently
updates the small model. Put a budget and an alert on the escalation rate, because a degraded small model
turns into 100% escalation and a cost spike on the large one. Decide the consistency policy: the same
question may get different answers depending on the route, which is acceptable for chat and not for an
audit trail.

## Example
A two-rung cascade with a deterministic gate followed by a small judge, using LiteLLM so the same code
talks to several providers. The number to watch in production is `escalated`; its rate is what you tune.

```python
import json, litellm

SMALL, LARGE, JUDGE = "openai/gpt-4o-mini", "anthropic/claude-sonnet-4-20250514", "openai/gpt-4o-mini"

def ask(model, messages, **kw):
    r = litellm.completion(model=model, messages=messages, temperature=0, **kw)
    return r.choices[0].message.content, r.usage

def passes_check(question, answer):
    try:                                              # 1) deterministic gate: valid JSON, required fields
        obj = json.loads(answer)
        if not {"answer", "confidence"} <= obj.keys():
            return False
    except json.JSONDecodeError:
        return False
    verdict, _ = ask(JUDGE, [{"role": "user", "content":   # 2) cheap judge, only after the gate passes
        f"Question: {question}\nAnswer: {obj['answer']}\n"
        "Is the answer complete and correct? Reply PASS or FAIL only."}], max_tokens=2)
    return verdict.strip().upper().startswith("PASS")

def cascade(question):
    msgs = [{"role": "system", "content": 'Reply as JSON: {"answer": str, "confidence": number 0-1}'},
            {"role": "user", "content": question}]
    answer, usage = ask(SMALL, msgs)
    if passes_check(question, answer):
        return {"model": SMALL, "answer": answer, "escalated": False, "usage": usage}
    answer, usage = ask(LARGE, msgs)                  # escalate: same prompt, stronger model
    return {"model": LARGE, "answer": answer, "escalated": True, "usage": usage}

print(cascade("Our invoice says net-45 but the signed contract says net-30. Which applies, and why?"))
# Emit `escalated`, both usages and the judge verdict as metrics; alert when the escalation rate jumps.
```

Note that the deterministic gate runs first *because* it is free and catches the most common failure
(unparseable output); the judge only spends tokens on answers that are at least well-formed.

## Trade-offs
- Mean cost falls substantially, but tail latency rises on escalated requests; this flips when the
  product has a strict p95 (voice, autocomplete), where routing or hedging beats cascading.
- Routing needs labelled data and a classifier you must retrain; cascading needs only a reliable cheap
  check; which is cheaper depends on whether you already have an eval set with judgments.
- Per-request model choice means the same question can get different answers; fine for chat, a
  problem for reproducible or audited workflows where one pinned model is required.
- Savings scale with the share of easy traffic; a workload that is uniformly hard yields little and
  adds a moving part, so measure the difficulty distribution before building.
- The check bounds the cascade's quality: a judge that favours verbose answers or misses subtle errors
  lets bad small-model answers through, so judge calibration is part of the system's eval.

## Pitfalls
- Using the small model's self-reported confidence as the escalation signal; the symptom is an
  escalation rate near zero while judged quality on the small route is poor.
- Measuring only aggregate quality; the symptom is a healthy dashboard metric while complaints
  concentrate in the hard tail that was misrouted or wrongly passed.
- No continuous baseline; the symptom is quality drift that nobody notices for weeks after a provider
  silently updates the small model, because nothing compares it with the strong one.
- Escalation storms: a small-model outage or regression sends everything to the large model; the
  symptom is 429s and a cost spike on the large model during an incident that was not about it.
- Unbounded cascades (retry small with a new prompt, then medium, then large); the symptom is p99
  latency several times the single-call latency and users abandoning before the answer arrives.
- A router trained on last quarter's prompts; the symptom is a misroute rate that creeps up after a
  prompt-template change with no code change in the router.

## When to use
Reach for routing or cascading when traffic has a visible easy/hard split and cost or latency is a
product KPI; skip it for low-volume features or uniformly hard tasks where a single pinned model is
simpler and more consistent. Before building, sample traffic, run both models, and judge the results: that
single experiment tells you the easy share, gives you router labels and sets expectations for the saving.
In production measure escalation rate, quality per route, cost per successful task and latency per route.
In interviews, derive the expected-cost formula, name the latency penalty, and explain where the quality
signal comes from and why self-reported confidence is not it.

## Role lens
- **Engineer:** Implements the router or the check, logs the route and reason on every request, builds the
  per-route eval, and tunes thresholds against cost and judge scores.
- **Architect:** Decides where routing lives (gateway versus application), the quality floor, budget
  guardrails on escalation, and the consistency policy for audited flows.
- **FDE:** Tunes thresholds to a customer's actual traffic mix, explains why two similar questions used
  different models, and shows cost per resolved task before and after.

## Check yourself
**Q:** You are asked to cut LLM spend on a support assistant by half without hurting customer
satisfaction. Design a routing or cascading approach and explain how you would prove it did no harm.
**A:** Start with data: sample a few thousand recent requests, answer each with both the small and the
large model, and have a judge plus a human sample label whether the small answer was adequate. That gives
the easy share, which bounds the achievable saving, and labelled data for a router. If the easy share is
around 60% or more, a cascade with a deterministic gate plus a cheap judge, or a trained router if p95
latency is tight, can reach the target; compute expected cost with the formula and the measured
escalation rate. To prove no harm: offline eval per route on the labelled set; an online A/B against the
all-large baseline on resolution rate and satisfaction; a permanent few-per-cent shadow slice served by
the large model to track the gap continuously; and alerts on escalation rate and judge disagreement. Also
check tail latency per route and that answer consistency is acceptable for the product.

## Quiz
### Q: What is the essential difference between routing and cascading?
- [ ] Routing uses a classifier, cascading uses rules
- [x] Routing chooses the model before any call; cascading calls a cheap model first and escalates on a failed quality check
- [ ] Routing saves cost, cascading saves latency
- [ ] Routing requires a gateway, cascading runs inside the application
> Both can live anywhere and use any decision mechanism. The structural difference is timing: a router
> predicts difficulty up front and pays prediction error; a cascade observes a real answer and pays
> serial latency and the cost of the check.

### Q: Small model costs 1 unit, the check 0.5 and the large model 12 units; 30% of requests escalate. Expected cost per request?
- [ ] 4.1 units
- [x] 5.1 units
- [ ] 13.5 units
- [ ] 3.6 units
> Expected cost is small + check + escalation share × large = 1 + 0.5 + 0.3 × 12 = 5.1. Forgetting the
> check gives 4.1; adding every component as if all ran every time gives 13.5; 3.6 is only the escalated
> portion.

### Q: Which escalation signal is least reliable for a cascade?
- [ ] The answer fails JSON schema validation
- [ ] Several sampled answers disagree with each other
- [x] The small model's own stated confidence is below a threshold
- [ ] Generated SQL fails to execute against the schema
> Models are poorly calibrated about their own correctness, so stated confidence is often high on wrong
> answers. Schema checks, execution and sample agreement are external signals that do not depend on the
> model knowing when it is wrong.
