---
id: reasoning-models-test-time-compute
module: model-internals
name: Reasoning models / test-time compute
kind: concept
difficulty: core
est_minutes: 18
status: fact-checked
related:
  - prompting/chain-of-thought
  - model-internals/sampling-strategies
  - model-internals/scaling-laws
  - model-internals/kv-cache
  - training-adaptation/grpo
  - training-adaptation/knowledge-distillation
  - agents/reflection-self-critique
  - system-design/model-routing-cascading
sources:
  - Wei et al., 2022 — Chain-of-Thought Prompting Elicits Reasoning in Large Language Models
  - Wang et al., 2022 — Self-Consistency Improves Chain of Thought Reasoning in Language Models
  - Snell et al., 2024 — Scaling LLM Test-Time Compute Optimally Can Be More Effective Than Scaling Model Parameters
  - DeepSeek-AI, 2025 — DeepSeek-R1: Incentivizing Reasoning Capability in LLMs via Reinforcement Learning
---

## TL;DR
Test-time compute means spending more inference computation on a query to get a better answer: a longer
chain of thought, several sampled attempts with a vote or verifier, or search over partial solutions.
Reasoning models are LLMs trained, mostly with reinforcement learning on verifiable tasks, to do this
internally in a long thinking trace before answering. A transformer does fixed computation per token, so
hard problems need more tokens, and extra inference compute often buys more accuracy per dollar than a
bigger model.

## Why it exists
A decoder-only transformer spends the same compute on every token: L layers, one pass. Whether the next
token completes "2 + 2 =" or "the smallest prime factor of 8,633 is", the depth of computation is
identical. Problems that need many sequential steps therefore cannot be solved in one forward pass once
they exceed what the layer stack can compose; the model guesses. Chain-of-thought prompting (Wei et al.,
2022) was the first clear evidence: asking the model to write intermediate steps lets it use its own output
as working memory, turning one hard prediction into many easy ones. Each emitted token is another forward
pass, so a longer trace is literally more compute spent on the problem.

Two further observations turned a prompting trick into a scaling axis. Self-consistency (Wang et al., 2022)
samples several chains and majority-votes the final answer; accuracy rises with samples because wrong paths
tend to disagree with each other while correct paths converge. And coverage keeps growing: the chance that
at least one of N samples is correct rises roughly log-linearly in N over a wide range, so anything that
can recognise a correct answer, from a unit test to a reward model, can harvest it. Snell et al. then
showed in 2024 that for a fixed compute budget, spending it at inference time can beat spending it on a
larger model, with the best allocation depending on how hard the question is.

Why not just prompt for chain of thought? Prompted reasoning is shallow: the model imitates what reasoning
looks like in its pre-training data, rarely backtracks, and does not know when to stop. Reasoning models
(OpenAI's o-series, DeepSeek-R1, and the extended-thinking modes of Anthropic and Google models) train
the behaviour with reinforcement learning on tasks whose answers can be checked, such as maths, code and
logic. Rewarding the correct final answer is enough for the policy to learn to explore alternatives, verify
its own steps and self-correct inside the trace; the DeepSeek-R1 report describes these behaviours emerging
without being demonstrated. The naive alternative, a bigger base model, is expensive to pre-train and still
does fixed compute per token, so it does not remove the underlying constraint.

## How it works
### Tokens as computation
Each generated token costs a forward pass, about 2N FLOPs plus attention over the growing context, and its
result is written into the context where later steps can read it through attention. A 2,000-token thinking
trace is therefore 2,000 sequential passes of extra computation with an explicit scratchpad. The serial
depth comes from the token dimension, not the layer dimension, which is why chain of thought provably
extends what a fixed-depth transformer can compute. The exchange rate is explicit: cost and latency grow
linearly with trace length, and the trace occupies context and KV-cache memory.

### Four strategies for spending inference compute
1. **Sequential.** One path, more steps: longer chain of thought, self-critique, revision loops.
2. **Parallel.** N independent samples at temperature above zero, aggregated by majority vote on the final
   answer (which needs a canonical answer) or by a verifier or reward model choosing the best (best-of-N).
3. **Search.** Expand solutions step by step, score partial solutions with a process reward model, and
   keep the best candidates (beam search or tree search over steps). Most useful on hard problems where whole
   samples rarely contain a fully correct path.
4. **Trained reasoning.** The model internalises the first three in a single long trace through RL, and
   at inference you control a budget rather than orchestrating samples yourself.

Snell et al. found the best strategy depends on difficulty: sequential revision wins on easy questions where
the first attempt is nearly right, parallel sampling and search win on hard ones where diverse attempts are
needed, and a difficulty-aware policy that picks per question is reported to be several times more
compute-efficient than any fixed strategy.

### Verifiers
Outcome reward models score only the final answer; process reward models score each step, and Lightman et
al. found step-level supervision more reliable for maths. Wherever a ground-truth checker exists, such as
unit tests, a calculator or a symbolic solver, use it: it is exact and free. That is also why maths and code
are where reasoning models improved most; the reward signal there is verifiable, so RL has something clean
to optimise. Majority voting needs discrete answers; for free-form outputs you fall back to an LLM judge or
pairwise comparison, which brings its own error rate.

### How a reasoning model is trained, in outline
Start from a pre-trained and usually instruction-tuned model. Run RL on problems with checkable answers.
DeepSeek-R1 used GRPO: sample a group of responses per prompt, score each with rule-based rewards for
correctness and format, compute each response's advantage relative to the group's mean, and update the
policy toward higher-advantage responses. Trace length grows over training as the model discovers that
checking its work pays. A later stage adds supervised fine-tuning on curated traces plus another RL round
for readability and general tasks. Distillation then transfers much of the capability: fine-tuning smaller
models on traces from the big reasoner gives small models that reason far better than their size suggests,
with the gains concentrated on the distilled domains.

### What you control at inference
A thinking budget or effort level, under vendor-specific names; whether the trace is returned, hidden or
summarised; temperature and N for your own parallel sampling; and stopping rules such as an agreement
threshold. Thinking tokens are billed as output tokens, the whole trace precedes the first answer token, and
the trace competes with your prompt for context.

### Limits
- **Overthinking.** On easy questions long traces add cost without accuracy and can even hurt; some models
  loop in very long traces.
- **Faithfulness.** The visible trace is not guaranteed to reflect the factors that actually drove the
  answer (Turpin et al., 2023); it is not an audit log.
- **Domain.** Gains concentrate where answers are verifiable; on open-ended writing or on questions
  bottlenecked by knowledge rather than reasoning, retrieval helps more than thinking.
- **Variance.** Results fluctuate between runs more than single-shot answers do, so evaluate with several
  runs and report pass@k or pass^k, not one number.

## Example
Self-consistency with early stopping and an escalation signal. If a single chain is right 70% of the time
and wrong chains scatter across different values, a majority of five is right well above 70% (with
independent samples and a binary outcome the figure is about 84%, and scattered wrong answers make it
better). Agreement doubles as a confidence signal: high agreement stops spending early, low agreement
routes the question to a stronger model or a human, which is a practical version of difficulty-aware
allocation.

```python
import collections, re

def llm(prompt, temperature=0.7):
    """Call your model provider here and return the text."""
    raise NotImplementedError

def solve(question, n_max=8, n_min=3, stop_agreement=0.8):
    prompt = f"{question}\nReason step by step, then finish with a line 'ANSWER: <value>'."
    votes, spent = collections.Counter(), 0
    for i in range(1, n_max + 1):
        out = llm(prompt)
        spent += len(out.split())                       # proxy; use the provider's token count in practice
        m = re.search(r"ANSWER:\s*(.+)", out)
        if m:
            votes[m.group(1).strip().rstrip(".")] += 1   # normalise the answer string before voting
        if i >= n_min and votes:
            best, count = votes.most_common(1)[0]
            if count / i >= stop_agreement:              # paths converged: stop spending
                return {"answer": best, "agreement": count / i, "samples": i, "tokens": spent}
    best, count = votes.most_common(1)[0] if votes else (None, 0)
    return {"answer": best, "agreement": count / n_max, "samples": n_max, "tokens": spent,
            "escalate": count / n_max < 0.5}             # low agreement: route to a stronger model or review

print(solve("A train leaves at 09:40 and arrives at 13:05. How many minutes is the journey?"))
```

Swap the vote for a verifier when one exists: run the unit tests on each sampled patch and return the first
that passes, which turns coverage directly into accuracy.

## Trade-offs
- Accuracy on verifiable multi-step tasks rises with inference budget, but cost and latency rise linearly
  with tokens; the trade flips when the task is easy, high-volume or latency-bound, where a fine-tuned small
  model wins.
- Parallel sampling parallelises well, so latency stays near one sample, but cost multiplies by N; sequential
  long traces can need fewer total tokens for the same gain on some problems, but the latency is the bill.
- Verifier-based selection extracts far more from N samples than majority vote, but needs a verifier; with
  tests or solvers it is nearly free, with an LLM judge you have added error and cost.
- Reasoning models give large out-of-the-box gains on maths, code and planning, but their traces are billed
  and consume context, and on extraction or classification they add cost with little gain.
- Distilled small reasoners deliver much of the capability cheaply, but inherit the teacher's trace style
  and tendency to overthink, and the gains are strongest on the domains they were distilled on.

## Pitfalls
- Turning reasoning on for every request; the symptom is cost per request rising several-fold and p95
  latency exploding with no measurable quality change on the simple tasks that dominate traffic.
- Treating the thinking trace as an explanation; the symptom is a trace that reads soundly while the answer
  was driven by a bias the trace never mentions.
- Majority-voting free-text answers; the symptom is near-zero agreement because no two strings match;
  normalise the answer or use a judge.
- Evaluating with a single run; the symptom is swings of several points between runs of the "same" eval;
  report means over several seeds or pass^k.
- Forgetting the trace in context budgeting; the symptom is truncated answers or length errors deep in agent
  loops as thinking tokens fill the window.
- Loading a reasoning model's prompt with chain-of-thought instructions and reasoning few-shots; several
  vendors advise against it because it can degrade results; keep the prompt plain and let the model think.
- Assuming the budget knob is linear in quality; the symptom is paying for 4× the tokens and getting the
  accuracy of 2× because nobody measured the accuracy-versus-tokens curve on the real task.

## When to use
Reach for test-time compute when the task has multi-step structure and a checkable, or at least judgeable,
answer: maths, code generation and debugging, planning inside agents, complex data transformations, and
hard classifications where a rationale changes the verdict. Measure the accuracy-versus-tokens curve on
your own eval at several budgets and sample counts, pick the knee, and add difficulty-aware routing: a cheap
model first, escalation on low agreement or a failed verifier. Do not use it for latency-bound chat, simple
extraction, or questions bottlenecked by knowledge, where retrieval wins. In interviews, explain why extra
tokens are extra compute, contrast parallel and sequential strategies and when each wins, sketch how
R1-style RL works, and raise the faithfulness caveat unprompted.

## Role lens
- **Engineer:** Implements thinking budgets, sampling with early stopping and verifier gates, tracks thinking
  tokens in cost dashboards, and keeps traces out of the next prompt unless they are needed.
- **Architect:** Decides which tasks earn a reasoning tier, defines routing between fast and reasoning models
  by difficulty signals, and sets latency and cost budgets per tier.
- **FDE:** Shows a customer the accuracy-versus-cost curve on their own hard cases, so the choice between a
  bigger model, a reasoning mode and more samples is made with their numbers rather than a benchmark.

## Check yourself
**Q:** A team's coding agent solves 60% of tasks with a standard model. They can switch to a reasoning
model at roughly five times the token cost, sample the standard model five times and pick with the test
suite, or fine-tune. How do you reason about the choice?
**A:** Start from the verifier: they have unit tests, so best-of-five with the suite as selector is cheap
and exact, coverage rises steeply with N, the five samples parallelise so latency stays near one attempt,
and the cost is similar to the reasoning model's. Its weakness is incomplete tests, which let wrong patches
pass. The reasoning model likely raises single-shot accuracy and helps most on tasks needing long-horizon
planning, where sampling rarely finds a complete solution, and where tests are weak. The pragmatic design is
to combine them: standard model first with test-based selection, escalation to the reasoning model on
tasks that fail. Fine-tuning helps only if failures are stylistic or convention-related and there is data.
Decide on pass@1, pass@5 and cost per solved task measured on their task set.

## Quiz
### Q: Why does writing out intermediate steps let a transformer solve problems it fails on when asked for the answer directly?
- [ ] The steps are copied from similar problems in the training data
- [x] Each generated token is another forward pass, so the trace adds sequential computation and an external working memory beyond the fixed per-token depth
- [ ] It lowers the effective sampling temperature
- [ ] Intermediate steps reduce the size of the KV cache
> A forward pass has fixed depth, so multi-step problems need more passes. Emitting tokens provides them,
> and attention lets later steps read earlier results. The trace grows the KV cache rather than shrinking
> it, and temperature is unchanged.

### Q: When does parallel sampling with majority vote fail to help, even with many samples?
- [ ] When the problem is a maths word problem
- [ ] When the temperature is above zero
- [x] When the model's errors are systematic, so wrong paths converge on the same wrong answer, or when answers are free text that cannot be matched
- [ ] When the model is a reasoning model
> Voting works because independent wrong paths disagree while correct ones agree. A shared misconception
> breaks that assumption, and free-text answers break the matching step; a verifier or a judge is needed in
> both cases.

### Q: Which claim about a reasoning model's thinking trace is correct?
- [ ] It is a faithful record of the computation and can serve as an audit log
- [x] It is billed as output tokens, consumes context, and may not reflect the factors that actually drove the answer
- [ ] It is free because it is discarded before the answer is produced
- [ ] Longer traces always improve accuracy
> Traces are generated tokens with all the usual costs, and research on chain-of-thought faithfulness shows
> they can omit the real reasons behind an answer. Longer is not always better; on easy tasks it adds cost
> and can reduce accuracy.
