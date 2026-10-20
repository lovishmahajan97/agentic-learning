---
id: quality-latency-cost-optimization
module: infra-capacity-economics
name: Quality–latency–cost optimization
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - system-design/model-routing-cascading
  - system-design/model-selection
  - prompting/prompt-caching
  - infra-capacity-economics/latency-budgets
  - infra-capacity-economics/unit-economics-per-successful-task
  - infra-capacity-economics/cost-optimization
  - training-adaptation/knowledge-distillation
sources:
  - Chen et al., 2023 — FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance
---

## TL;DR
Every LLM feature lives in a three-dimensional space: how good the answers are, how long the user waits,
and what each request costs. The levers that improve one axis almost always degrade another, so you cannot
maximize all three. The practice is to measure all three per task, throw away every configuration that is
worse on every axis, and choose from the remaining Pareto frontier under the product's hard constraints.
This is what separates features that scale from features that get switched off when the invoice arrives.

## Why it exists
The usual history: a prototype is built on the largest model with a long, example-stuffed prompt. It works,
it ships, and then three things happen at once. The bill grows linearly with traffic; p95 latency of several
seconds makes the interface feel broken; and when someone finally runs an eval, the "best" model turns out
to be no better on this task than one an order of magnitude cheaper, because the task saturates.

The naive strategies each fail on one axis. "Use the best model for everything" ignores that quality is
task-relative and plateaus, while cost and latency are paid on every single request. "Use the cheapest
model" ignores that a wrong answer has a cost too: a retry, a human escalation, a lost customer. The right
denominator is cost per *successful* task, and on that measure the cheap model can be the expensive one.
"Tune one axis at a time" is the most common failure: a team trims the prompt to cut cost, quality quietly
drops, and nobody notices until a customer does, because quality was never a number on a dashboard.

The axes are coupled by mechanism, not by accident. More reasoning tokens means better answers on hard
problems and more decode time and spend. More retrieved context means more grounding and more prefill. A
bigger model means more capability and more memory bandwidth per token. You therefore need a method that
holds all three in view, makes the exchange rate between them explicit, and is re-run whenever a model,
price or prompt changes.

## How it works
### Make all three measurable
Nothing below works without numbers for each axis on the *same* set of requests.

- **Quality** is a task-specific eval: a golden set with a pass criterion (exact match for classification,
  a rubric or LLM judge for open text, human grading for the ambiguous slice). It is defined first
  *because* every cost or latency cut is a bet that quality survives, and bets need a scoreboard.
- **Latency** must be the latency the user feels: time-to-first-token for streamed chat, end-to-end time
  for a classifier or a tool call, and always a percentile (p95 or p99), not a mean. Means hide the tail,
  and the tail is what users remember.
- **Cost** is cost per successful task, including retries, fallbacks, judge or verifier calls, cache
  misses and the downstream cost of failures. Per-request cost is what the provider bills; per-successful
  task is what the business pays.

### Where latency and cost actually come from
Before pulling levers, decompose each axis, *because* the decomposition tells you which lever can move it:

```
cost_per_call ≈ n_in_uncached·p_in + n_in_cached·p_cached + n_out·p_out
TTFT          ≈ network + queueing + prefill(n_in)
end_to_end    ≈ TTFT + n_out · TPOT          (TPOT = time per output token)
```

Prefill processes the whole prompt in parallel, so input tokens are cheap in time; decode emits output
tokens one at a time, so `n_out · TPOT` usually dominates end-to-end latency. Output tokens are also
typically priced several times higher than input tokens. With an illustrative TPOT of 20 ms, a
300-token answer spends about 6 s in decode alone, whatever the prompt length; cutting the answer to
100 tokens saves about 4 s, while halving a 2,000-token prompt saves a fraction of a second of prefill.
This is why output caps and terse formats are often the strongest latency lever, and why prompt trimming
mainly buys cost and TTFT. Agents and chains multiply all of it by the number of sequential calls.

### Cost per success, and when quality *is* cost
If each attempt succeeds with probability *q* and a verifier lets you retry until success, expected
attempts are `1/q`, so `cost_per_success = (c_call + c_verify) / q`. That assumes independent attempts; in practice the
same hard input tends to fail again, so retries recover less than `1/q` predicts. If failures instead go to a human,
`cost_per_task = c_call + (1 − q) · c_human`. Illustratively, a $0.001 model at 86% with a $2 human
escalation costs 0.001 + 0.14 × 2 ≈ $0.28 per task; a $0.018 model at 94% costs 0.018 + 0.06 × 2 ≈ $0.14.
When failure is expensive, the quality axis collapses into the cost axis and the "expensive" model is the
cheap one. When failure is cheap (a user rephrases), the axes stay separate and the frontier below matters.

### Know which axis each lever moves
| Lever | Quality | Latency | Cost | Note |
|---|---|---|---|---|
| Bigger model tier | up | up | up | coarsest lever; quality gain saturates per task |
| Shorter prompt / less context | risk | down (prefill) | down | every token is paid on every call |
| Capped or terser output | neutral if instructed | down (decode) | down | decode is sequential, so output length often dominates total time |
| Reasoning / thinking budget | up on hard tasks | up a lot | up a lot | wasted on easy tasks; gate by difficulty |
| Prompt caching (shared prefix) | none | down (TTFT) | down (input) | removes work instead of trading it |
| Response / semantic cache | none on hits | near zero on hits | near zero on hits | only for repeat queries; staleness risk |
| Routing / cascade | ≈ large model | up for escalated requests | down on average | depends on escalation rate |
| Batch / async endpoints | none | up (minutes to hours) | down sharply | for non-interactive work only |
| Fine-tune / distill small model | up for small model | down | down | front-loaded effort; shifts the frontier |
| Quantization, speculative decoding | small risk | down | down | serving-side; measure per model |
| Streaming | none | perceived down | none | changes perception, not the number |

Two groups matter. Most levers move you *along* the frontier: you give up some of one axis for another.
A few, caching, distillation, fine-tuning and task redesign, *shift* the frontier, because they remove work
or make a cheaper model genuinely better at this task. When no point on the current frontier satisfies the
constraints, only the second group can help; more tuning of the first group cannot.

### Build the frontier
Run every candidate configuration against the same eval set and a replay of real traffic, and record the
triple (quality, p95 latency, cost per successful task). Configuration A is *dominated* if some B is at
least as good on all three and strictly better on one; discard the dominated ones. What remains is the
Pareto frontier.

Respect measurement noise when declaring dominance. A pass rate on *n* items has a standard error of about
`√(q(1−q)/n)`; at q = 0.92 and n = 200 that is roughly 2 points, so 0.92 and 0.93 are a statistical tie.
Treat configurations within the noise band as equal on quality (compare them on the same items with a
paired test, or grow the eval) and let latency and cost decide. Latency percentiles need the same care:
a p95 from a few hundred requests is itself noisy.

The point of the exercise is that it turns a three-way argument into a short list and makes
the exchange rate visible: "moving from B to C buys two points of quality for $0.003 per task and 450 ms
of p95". People can argue about whether that trade is worth it; they can no longer argue about facts.

### Choose under constraints
In practice two axes are constraints and one is the objective. Either *maximize quality subject to
p95 ≤ L and cost ≤ C*, or *minimize cost subject to quality ≥ Q and p95 ≤ L*. The quality floor is nearly
always a product requirement rather than a tunable, so the second form is the common one. Pick the
feasible frontier point with the best objective. If the feasible set is empty, stop tuning and reach for a
frontier-shifting lever.

### Do it per task, not per application
A single application contains features with different frontiers. Intent classification saturates on a
small model; multi-document synthesis does not. Splitting traffic by task and optimizing each separately is
usually the single largest win, which is why routing comes before most other work.

### Keep measuring
Quality drifts when the provider updates a model or the input mix changes; prices fall; traffic mix
shifts; a prompt edit makes the model chattier. Re-run the frontier on a cadence and on every change to
prompt, model or retrieval, and alert on cost per task and p95 in production, not just on total spend.

## Example
A small, runnable version of the frontier step. The measurements are illustrative numbers for a
support-ticket triage task, not benchmarks; the structure is what matters.

```python
from dataclasses import dataclass

@dataclass
class Config:
    name: str
    quality: float        # pass rate on the task's golden set, 0..1
    p95_ms: int           # end-to-end p95 on a traffic replay
    cost_per_task: float  # USD per successful task, retries and judge calls included

configs = [
    Config("large, long prompt",     0.94, 3800, 0.0180),
    Config("large, trimmed prompt",  0.93, 2900, 0.0110),
    Config("small, trimmed prompt",  0.86,  900, 0.0012),
    Config("small, fine-tuned",      0.92,  950, 0.0014),
    Config("small -> large cascade", 0.93, 1400, 0.0038),
    Config("medium, trimmed prompt", 0.90, 1700, 0.0045),
]

def dominated(c, others):  # B dominates A: no worse on all three, strictly better on one
    return any(o.quality >= c.quality and o.p95_ms <= c.p95_ms
               and o.cost_per_task <= c.cost_per_task
               and (o.quality > c.quality or o.p95_ms < c.p95_ms
                    or o.cost_per_task < c.cost_per_task) for o in others)

frontier = [c for c in configs if not dominated(c, configs)]
feasible = [c for c in frontier if c.quality >= 0.90 and c.p95_ms <= 2000]
best = min(feasible, key=lambda c: c.cost_per_task)
for c in frontier:
    print(f"{c.name:<24} q={c.quality:.2f}  p95={c.p95_ms:>5} ms  ${c.cost_per_task:.4f}/task")
print("choose:", best.name)
# large, long prompt       q=0.94  p95= 3800 ms  $0.0180/task
# small, trimmed prompt    q=0.86  p95=  900 ms  $0.0012/task
# small, fine-tuned        q=0.92  p95=  950 ms  $0.0014/task
# small -> large cascade   q=0.93  p95= 1400 ms  $0.0038/task
# choose: small, fine-tuned
```

Two configurations vanish immediately: the trimmed large model is dominated by the cascade (same quality,
faster, cheaper), and the medium model is dominated by the fine-tuned small one. Under the constraints
(quality ≥ 0.90, p95 ≤ 2 s) only the fine-tuned small model and the cascade survive, and the fine-tuned
model wins on cost. Note what the table does not show: the one-off cost of producing the fine-tuned model,
which must be amortized over expected volume before the choice is final, and the fact that the cascade's
p95 is driven by the escalated minority, so its p50 is close to the small model's. And the quality gap
between the fine-tuned model (0.92) and the cascade (0.93) is inside the noise of a few-hundred-item eval,
which is exactly why cost, not quality, should break the tie here.

## Trade-offs
- A cascade cuts average cost but raises tail latency and adds a confidence gate to maintain; with
  small-model cost *s*, large-model cost *l* and escalation rate *e*, it beats the large model alone only
  while `s + e·l < l`, i.e. while `e < 1 − s/l`. At a 10× price gap that is 90%; at 2× it is only 50%.
- Trimming context and few-shot examples lowers cost and TTFT on every call, but each removed token is a
  bet that it was not needed; the trade flips the moment the eval shows quality under the floor.
- Thinking or reasoning budgets buy real quality on hard inputs and pure waste on easy ones; the trade is
  favourable only when gated by task difficulty rather than applied uniformly.
- Fine-tuning or distilling a small model shifts the whole frontier but front-loads engineering and must
  be repeated when the task or base model changes; it pays only when monthly savings exceed the amortized
  effort, which is a volume threshold.
- Response caching gives near-zero cost and latency on hits, but is worthless on long-tail inputs and
  harmful when answers must reflect fresh data; the trade flips with hit rate and staleness tolerance.
- Batch endpoints can cut cost substantially for offline work at the price of minutes-to-hours latency;
  the trade flips the instant a user is waiting on the result.

## Pitfalls
- Optimizing cost with no quality metric in place; the symptom is a falling bill followed weeks later by
  rising escalation and complaint rates that nobody connects to the change.
- Reporting mean latency; the symptom is a green dashboard while users report hangs, because the p95
  nobody plotted is three times the mean.
- Comparing cost per request instead of per successful task; the symptom is a "cheaper" model whose
  retries, judge calls and human review make the total higher.
- Evaluating on a handful of hand-picked prompts; the symptom is a configuration that shines in the demo
  and degrades on the real traffic distribution.
- Reading a one- or two-point eval difference as a real win; the symptom is the "winning" configuration
  swapping places with the runner-up every time the eval is re-run or re-sampled.
- Leaving output length unconstrained; the symptom is cost and decode latency creeping up after a prompt
  or model change made the model chattier.
- Treating the frontier as static; the symptom is a once-optimal setup that became dominated after a
  provider price cut or model update that nobody re-tested.

## When to use
Run the full frontier exercise when a feature first approaches production scale, whenever a new model or
price list appears, and whenever quality, p95 or cost per task moves outside its band in monitoring. Skip
it for throwaway prototypes and low-volume internal tools, where the cost of measuring exceeds the cost of
being wrong, but still write down the quality floor so the floor exists when volume arrives. The day-to-day
form is a dashboard with three lines per feature (quality on a sampled eval, p95, cost per successful task)
and a habit of re-running the eval on every prompt, model or retrieval change. In interviews, the expected
depth is to name all three axes unprompted, insist on cost per successful task, separate frontier-moving
levers from frontier-shifting ones, and derive the cascade break-even condition from the escalation rate.

## Role lens
- **Engineer:** Owns the eval harness, the traffic replay and the per-config measurement; implements
  routing, caching and output caps; keeps max_tokens, prompt versions and model ids under version control.
- **Architect:** Sets the quality floor and latency budget per feature, decides which levers are allowed
  (cascade, distillation, batch paths) and signs off the frontier point the product will run on.
- **FDE:** Rebuilds the frontier inside the customer's constraints, where the eval set is their data, the
  latency budget is their workflow and the cost ceiling is their contract margin, then defends the choice.

## Check yourself
**Q:** A feature runs on a large model at p95 of 4 s, $0.02 per task and 94% on its eval. Product wants
p95 under 2 s and cost under $0.005 per task while keeping quality at 90% or better. Which levers do you
try, in what order, and how do you know when you are done?
**A:** Start with the levers that change the least: trim the prompt, cap and tighten the output, add
prompt caching for the shared prefix, and re-measure all three numbers on the full eval and a traffic
replay. If that does not clear both constraints, route: a small model first with a confidence or verifier
gate, escalating to the large model, and compute expected cost as small cost plus escalation rate times
large cost, while checking that the escalated requests do not blow the p95. If escalation stays high, the
frontier itself must move: distill the large model's outputs into a fine-tuned small model and amortize
that effort against monthly savings at the expected volume. Done means a configuration on the frontier
satisfies all three constraints on the eval and the replay, with production monitoring of p95 and cost per
successful task so a regression is caught in days rather than at the next invoice.

## Quiz
### Q: Which lever shifts the quality–latency–cost frontier rather than moving along it?
- [ ] Switching to a larger model tier
- [x] Distilling the large model's behaviour into a fine-tuned small model
- [ ] Raising max_tokens so answers are more complete
- [ ] Adding more few-shot examples to the prompt
> Model tier, output length and prompt length trade one axis for another and stay on the existing
> frontier. Distillation makes a cheap, fast model genuinely better at this task, so it improves quality
> without paying in latency or cost: the frontier itself moves.

### Q: Why is cost per successful task the right cost measure rather than cost per request?
- [ ] Because providers bill per task rather than per token
- [x] Because it includes retries, fallbacks, judge calls and the cost of failed outputs, which per-request cost hides
- [ ] Because it is easier to compute from the invoice
- [ ] Because latency is also measured per task
> A cheap model that fails more often incurs retries, verifier calls and human handling; per-request cost
> makes it look like a win while the business pays more. Per successful task counts what the outcome
> actually cost.

### Q: A small model costs $0.001 per task and a large one $0.010. Below what escalation rate does a small-first cascade beat sending everything to the large model?
- [ ] 10%
- [ ] 50%
- [x] 90%
- [ ] It always beats it, at any escalation rate
> Cascade cost is 0.001 + e × 0.010, which is below 0.010 only while e < 0.9. Above that, nearly every
> request pays for both models and the cascade is strictly worse on cost and on tail latency.
