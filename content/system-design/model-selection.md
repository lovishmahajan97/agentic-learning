---
id: model-selection
module: system-design
name: Model selection
kind: decision
difficulty: core
est_minutes: 16
related:
  - evals/model-evaluation
  - evals/benchmarking
  - evals/golden-dataset
  - evals/dataset-contamination
  - infra-capacity-economics/quality-latency-cost-optimization
  - system-design/model-portfolio
  - system-design/model-routing-cascading
  - system-design/slm-vs-llm-trade-offs
sources:
  - Chiang et al., 2024 — Chatbot Arena: An Open Platform for Evaluating LLMs by Human Preference
  - Chen et al., 2023 — FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance
---

## TL;DR
Model selection is choosing, per task, the cheapest and fastest model that clears a quality bar you
have measured on your own data, after filtering by hard constraints such as modality, context length,
deployment location, licensing and tool support. It is a funnel, not a leaderboard lookup, because
public benchmarks measure a different distribution from your traffic, and because quality has a
threshold ("good enough for this task") while cost and latency are paid linearly on every call.

## Why it exists
At any moment there are dozens of plausible models, spanning one to two orders of magnitude in price
per token and several-fold in latency, and whichever one you pick is multiplied by every request for
the life of the feature. The decision therefore deserves a method, and the obvious method is wrong.

"Pick the top of the leaderboard" fails for four reasons. Public benchmarks measure broad ability on
public data; your task is a narrow distribution with its own format, domain vocabulary and failure
costs, and the benchmark questions may have leaked into training data. The quality gap between the top
two or three models on a narrow task is frequently smaller than their cost gap, so the ranking is
unstable exactly where the money is. Non-quality dimensions often decide feasibility outright: context
length at your p95 prompt size, reliable structured output, tool-calling behaviour, language coverage,
rate limits at your peak, data residency, and the provider's deprecation cadence. And your prompt was
tuned on the model you started with, so a naive swap systematically underrates every other candidate.

"Pick the cheapest and hope" fails on the tail: the errors a weaker model makes concentrate in the
hard, high-stakes cases, which are precisely the ones that generate support tickets or compliance
findings.

The reasoning that resolves this: quality has a threshold value. Below the bar the feature does not
work; above it, extra points rarely change the business outcome. Cost and latency, by contrast, are
linear in volume. So the optimum is not the best model but the cheapest model that clears the bar with
a margin, and the only way to know which one that is to measure your task.

## How it works
### Step 1: apply hard constraints first
Filter before you evaluate quality, because no quality number rescues a model that cannot run where
the data is. The usual gates: modality (text, vision, audio); context window versus your *measured*
p95 prompt size, not the mean; native structured output or JSON-schema support; tool calling;
languages; deployment constraints (a region, a VPC, on-prem or air-gapped, which narrows the field to
open-weight models or private endpoints); licence terms; quota and rate limits at launch peak; the
provider's version pinning and deprecation policy; data-use and retention terms.

### Step 2: build the task evaluation before touching candidates
Fifty to a few hundred examples drawn from real or realistic traffic, with expected outputs or a
scoring rubric, and a metric that is the task's own: exact or normalised match for extraction and
classification, citation faithfulness for retrieval-augmented answers, a calibrated LLM-as-judge rubric
for free-form generation, pass@k for code. Build it first *because* otherwise the prompt gets tuned to
the first model tried and every later comparison is biased toward the incumbent. The set should
over-sample the hard and high-cost cases, since that is where candidates differ.

### Step 3: run candidates fairly
Same examples, same prompt structure, then a bounded, equal amount of prompt adaptation per model,
because format sensitivity differs between families and zero adaptation punishes the newcomer. Fix
sampling settings, run several seeds, and report a mean with a confidence interval: on 100 examples a
proportion near 0.9 has a 95% half-width of roughly ±6 points, so a 2-point gap is noise. Keep the set
private; a model that has seen your examples is not being evaluated.

### Step 4: measure cost and latency on real request shapes
Cost per request is input tokens × input price plus output tokens × output price, adjusted for cached
prefixes. Measure the tokens from the eval runs rather than estimating, *because* tokenizers differ
across families (the same document can be 20–30% more tokens in one than another, and more for
non-English text) and because output verbosity differs. For reasoning models the hidden reasoning
tokens are billed as output and can dominate, so price per token alone misleads. Measure time to
first token and total latency at p50 and p95, under realistic concurrency, at the hours you will run.

### Step 5: find the frontier and apply the rule
Lay candidates out on quality versus cost versus latency and discard any model that another beats on
all three. Among the rest, choose the cheapest whose *lower* confidence bound clears the bar and whose
p95 fits the latency budget. If the cheap model fails only on an identifiable tail, consider a cascade:
cheap model by default with escalation to a stronger one on low confidence or a trigger condition,
which is often cheaper than running the strong model everywhere.

### Step 6: operational factors and re-evaluation
Pin an exact model version, never a floating alias, so quality cannot change under you. Check for a
second source: a fallback model that also clears the bar, even at higher cost, turns an outage or a
deprecation notice into a configuration change. Then schedule re-selection: when a deprecation is
announced, when a new model could move the frontier, or on a fixed cadence. The eval set is what
makes each re-run cheap; this is the compounding payoff of having built it first.

## Example
A small harness that applies the funnel's quantitative steps. Each candidate exposes a `call` that
returns the text plus the measured token counts; prices are placeholders you fill from the provider's
current sheet. The selection rule requires the lower confidence bound, not the mean, to clear the bar.

```python
import statistics, time
from dataclasses import dataclass
from typing import Callable

@dataclass
class Candidate:
    name: str
    price_in: float          # currency per 1M input tokens (fill from current price list)
    price_out: float         # currency per 1M output tokens
    call: Callable           # prompt -> (text, input_tokens, output_tokens)

def evaluate(c: Candidate, golden, score, runs=3):
    scores, lat, cost = [], [], []
    for ex in golden:
        for _ in range(runs):
            t0 = time.perf_counter()
            text, n_in, n_out = c.call(ex["prompt"])
            lat.append(time.perf_counter() - t0)
            scores.append(score(text, ex["expected"]))
            cost.append((n_in * c.price_in + n_out * c.price_out) / 1e6)
    mean = statistics.mean(scores)
    half_width = 1.96 * statistics.pstdev(scores) / len(scores) ** 0.5   # rough 95% CI
    p95 = sorted(lat)[int(0.95 * (len(lat) - 1))]
    return {"model": c.name, "quality": mean, "lower": mean - half_width,
            "p95_s": p95, "cost_per_1k": 1000 * statistics.mean(cost)}

def select(results, bar, max_p95):
    eligible = [r for r in results if r["lower"] >= bar and r["p95_s"] <= max_p95]
    return min(eligible, key=lambda r: r["cost_per_1k"]) if eligible else None

# results = [evaluate(c, golden_set, exact_match) for c in candidates]
# print(select(results, bar=0.92, max_p95=2.5))
```

If `select` returns `None`, no candidate clears the bar within budget; the next moves are prompt work,
retrieval improvements, a cascade, or fine-tuning a smaller model, in roughly that order of cost.

## Trade-offs
- "Cheapest that clears the bar" minimises spend but leaves little headroom when the traffic
  distribution drifts harder; if your task changes often, buy margin with a stronger model or a cascade.
- A private eval set gives a trustworthy comparison but costs expert time and goes stale; a small set
  has wide confidence intervals, so differences of a few points cannot be acted on.
- Adapting the prompt per candidate makes the comparison fair but multiplies the effort; skipping it
  is cheaper and systematically favours the incumbent model.
- Standardising on one provider's best model simplifies operations but concentrates deprecation and
  outage risk; a second source doubles evaluation work and halves the blast radius.
- Reasoning-heavy models raise quality on genuinely hard tasks but bring high output-token cost and
  latency variance; the choice flips as soon as the task is not reasoning-bound.

## Pitfalls
- Choosing on public leaderboard rank. Symptom: the "better" model scores lower on your eval, or wins
  it and then fails on JSON formatting or tool calls in production.
- Estimating cost from list price without measuring tokens. Symptom: the bill differs from the forecast
  by a large fraction because of tokenizer differences or verbose outputs.
- Comparing single runs and treating a 2-point gap as real. Symptom: the ranking flips when someone
  re-runs the comparison the following week.
- Deploying a floating "latest" alias. Symptom: quality or format changes overnight with no deploy and
  no entry in the change log.
- Ignoring quotas and rate limits at peak. Symptom: 429 errors at launch, and the fallback path has
  never been exercised.
- Evaluating in English only for a multilingual product. Symptom: context overflows and higher cost for
  languages whose tokenization is two to three times less efficient.

## When to use
Run the funnel whenever a new task is introduced, when a deprecation notice arrives, when a model
release plausibly moves the quality-cost frontier, or when the latency or cost target changes. Do not
re-select on every release announcement; the eval harness makes a scheduled re-run cheap, so put it on
a cadence instead. Track eval score with its interval, cost per successful task, p95 latency, and
failure rates by category (format, refusal, timeout). Day to day this shows up as "can we move this
to the cheaper model", and the honest answer is always "the eval says". In interviews, articulating
the threshold-versus-linear reasoning, the hard-constraint filter, and "I would build the eval set
first" is the expected depth; naming a favourite model is not.

## Role lens
- **Engineer:** Owns the eval harness, measures real token counts and latency percentiles, and keeps
  the model behind one interface so the next re-selection is a configuration change.
- **Architect:** Sets the quality bar and the latency and cost budgets per task, decides on second
  sourcing and cascades, and records the selection and its evidence as an ADR.
- **FDE:** Runs the funnel on the customer's own data under their deployment constraints, and shows
  stakeholders the quality-cost table rather than a vendor's benchmark slide.

## Check yourself
**Q:** You are asked to replace an expensive frontier model with one at a third of the price for a
retrieval-augmented assistant. On your 100-example eval the cheaper model scores 2 points lower. How do
you decide, and what would make you say no?
**A:** First, 2 points on 100 examples is inside the noise: the 95% interval around a score near 0.9 is
roughly ±6 points, so the gap is not established. Expand the set toward several hundred examples or
run multiple seeds and compare the lower bounds against the bar, not against each other. Second, read
the failures by category: if the cheaper model's extra misses cluster in a high-cost class such as
wrong citations on compliance questions, that is disqualifying even if the total is small; if they
are stylistic, it is not. Third, check the dimensions the score ignores: format compliance, p95
latency, rate limits at peak, and the real cost ratio measured from tokens, since tokenizers differ.
Fourth, consider a cascade, cheap by default with escalation on low confidence, which may capture most
of the saving while covering the tail. I would say no if the lower bound misses the bar or the misses
land in the costly category.

## Quiz
### Q: Why is "pick the model at the top of a public leaderboard" a poor selection rule?
- [ ] Leaderboards are updated too infrequently to be useful
- [x] Benchmarks measure a different distribution from your traffic and may be contaminated, while your cost and latency are exact, so the rank says little about which model clears your bar most cheaply
- [ ] Top-ranked models are always rate-limited more aggressively
- [ ] Leaderboard models do not support structured output
> Leaderboards estimate broad ability on public data. Your task is narrow, has its own failure costs,
> and must fit a budget; the question is which eligible model clears your measured bar at the lowest
> cost, which only a task-specific evaluation answers.

### Q: Two candidate models differ by 3 points on a 60-example evaluation set. What is the right conclusion?
- [ ] The higher-scoring model is better and should be chosen
- [ ] The cheaper model should be chosen because 3 points never matters
- [x] The difference is probably within noise; enlarge the set or repeat runs and compare confidence intervals against the quality bar before deciding
- [ ] Both models should be rejected for a larger model
> With 60 examples the sampling interval is wide, so a 3-point gap cannot be distinguished from
> run-to-run variance. The decision needs tighter intervals and should be made against the bar with
> margin, not by comparing two noisy means.

### Q: Which of these must be applied as a hard filter before any quality comparison?
- [ ] Price per million output tokens
- [ ] Position on a human-preference leaderboard
- [x] Whether the model can be run in the region or environment where the data is allowed to be
- [ ] How recently the model was released
> Deployment and residency constraints are binary: a model that cannot legally or technically process
> the data is not a candidate regardless of quality or price. Price and recency are inputs to the
> later optimisation step, not gates.
