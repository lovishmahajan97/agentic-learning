---
id: agent-trajectory-evaluation
module: agent-reliability
name: Agent trajectory evaluation
kind: practice
difficulty: core
est_minutes: 15
related:
  - agent-reliability/task-completion-rate
  - agent-reliability/tool-call-success-rate
  - agent-reliability/structured-event-streams
  - agent-reliability/deterministic-replay-and-reproducibility
  - agents/tool-selection-accuracy
  - evals/llm-as-a-judge
  - evals/pass-k
  - evals/eval-harness
sources:
  - Liu et al., 2023 — AgentBench: Evaluating LLMs as Agents
  - Yao et al., 2024 — τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains
---

## TL;DR
Scoring an agent on its final answer alone misses most of what can go wrong: it may have reached the
answer by a forbidden action, by forty steps instead of four, or by luck. Trajectory evaluation scores
the whole path, the sequence of model decisions, tool calls and observations, against outcome checks,
process checks and policy checks, over many runs per task, so you learn not just whether the agent
succeeded but where and why it fails.

## Why it exists
Single-turn LLM evaluation compares an output to a reference. An agent produces a trajectory: a plan, a
tool call, an observation, a revised plan, another call, and eventually some end state in the world. Three
things make the single-turn approach inadequate. First, the final output may be unobservable or trivial:
"Done, the ticket is created" is not the thing to check; the ticket in the ticketing system is. Second,
two trajectories that reach the same end state can differ enormously in cost, latency, risk and policy
compliance, and a product cannot ship an agent that gets there by calling `delete` first and recreating.
Third, when the agent fails, an outcome score of 0 tells you nothing about which step went wrong, which is
the only information that lets you fix it.

The naive fix, asking an LLM judge to read the transcript and rate it, helps but is not enough by itself:
judges are lenient on long transcripts, cannot verify side effects they cannot see, and drift with the
judge model. The other naive fix, matching the trajectory exactly against a golden one, is too strict:
many valid paths exist, and an agent that found a shorter one would be penalized.

Trajectory evaluation combines verifiable end-state checks, structured process metrics computed from the
event stream, rubric-based judgments for the parts only a reader can assess, and enough repetitions to
make the numbers mean something.

## How it works
### The unit of evaluation
A trajectory is the list of events for one run of one task: `llm.response` events with their tool calls,
`tool.call.result` events with outcomes, and the final state. If the runtime emits a structured event
stream, the trajectory is free; the harness records it and the scorers consume it. Tasks come with a
specification: initial environment state, the user goal, a success predicate, optional reference actions,
and a policy (forbidden tools, budgets).

### Four families of scores
1. **Outcome.** A success predicate evaluated against the end state, not the agent's words: the row exists
   with the right fields, the file compiles and the tests pass, the order status changed. Where the
   environment is real, the harness snapshots it before and after; where it is simulated, the simulator
   exposes state. This is the primary score and should be binary per run.
2. **Process.** Computed from the trajectory with no judgment involved. Steps taken versus a reference
   count (efficiency ratio); number of tool calls; tool-call success rate within the run; redundant calls
   (same tool, same arguments, no state change between); recovery, meaning whether a failed tool call was
   followed by a sensible alternative; whether required actions occurred in a valid order, checked as an
   ordered subsequence rather than an exact match so alternative valid paths pass.
3. **Policy.** Count of forbidden or gated actions attempted, budget violations, and attempts to access
   out-of-scope resources. A single violation usually zeroes the run regardless of outcome, *because* a
   correct answer obtained by a forbidden action is a failure of the product.
4. **Judgment.** An LLM judge with a rubric for what cannot be computed: was the clarification question
   reasonable, did the final summary reflect what actually happened, was the user told about the failed
   step. Judges are calibrated against human labels on a sample and used for diagnosis more than for gates.

### Repetition and the statistics
Agent runs are high-variance, so a task is scored over N runs, not one. Two aggregates matter and they
are different. pass@k is the probability that at least one of k attempts succeeds, which is the right
number when a human or a verifier will pick the best attempt. pass^k is the probability that all k
attempts succeed, which is the right number for an agent that runs unattended, because the user who gets
attempt three does not care that attempt one would have worked. With per-run success p and independence,
pass^k ≈ p^k: an agent at 80% per run is at about 51% for pass^3. The unbiased estimate from n trials
with c successes per task is C(c, k) / C(n, k), averaged over tasks. Report confidence intervals; with ten
tasks and three runs each you cannot distinguish 70% from 80%.

### Reference trajectories without brittleness
When a reference exists, compare at the level of required actions and their order, not exact arguments.
"Must call `lookup_order` before `refund`; must not call `refund` more than once; may call `search` any
number of times" is a specification the harness can check on any valid path. Exact-match baselines are
useful only as a diagnostic for drift.

### Live versus simulated environments
Simulated or recorded tools make runs cheap, deterministic and safe, and let the suite run in CI. Live
environments catch the failures simulation hides: real latency, real error shapes, real data mess. A
common split is a large simulated suite on every change and a small live suite before release.

### Reading the results
The goal is a failure taxonomy: wrong tool selected, right tool with wrong arguments, misread result, gave
up early, looped, policy violation, environment error. Each failed run is labelled with the first failing
step and its class, by rule where possible and by judge otherwise. The histogram of classes is what drives
the next prompt, tool or policy change.

## Example
Scorers over a recorded trajectory and an aggregate over repeated runs. The outcome check inspects end
state; the process checks are computed from events; the ordered-subsequence check accepts any valid path.

```python
from collections import Counter

def ok_subsequence(actions: list[str], required: list[str]) -> bool:
    """required actions appear in order; other actions may be interleaved."""
    it = iter(actions)
    return all(any(a == r for a in it) for r in required)

def score_run(trajectory: list[dict], task: dict, end_state: dict) -> dict:
    calls = [e for e in trajectory if e["type"] == "tool.call.requested"]
    results = [e for e in trajectory if e["type"] == "tool.call.result"]
    actions = [c["name"] for c in calls]
    violations = [a for a in actions if a in task["forbidden_tools"]]
    outcome = task["success"](end_state) and not violations          # policy violation zeroes the run
    redundant = sum(v - 1 for v in Counter((c["name"], str(c["arguments"])) for c in calls).values())
    return {
        "success": bool(outcome),
        "steps": len(calls),
        "efficiency": task["reference_steps"] / max(len(calls), 1),
        "tool_success_rate": sum(r["ok"] for r in results) / max(len(results), 1),
        "order_ok": ok_subsequence(actions, task["required_order"]),
        "redundant_calls": redundant,
        "violations": len(violations),
    }

def aggregate(runs: list[dict], k: int = 3) -> dict:
    n = len(runs); p = sum(r["success"] for r in runs) / n
    return {"n": n, "pass_rate": p, "pass_hat_k": p ** k,             # pass^k under independence
            "mean_steps": sum(r["steps"] for r in runs) / n,
            "violation_runs": sum(r["violations"] > 0 for r in runs)}

task = {"forbidden_tools": {"delete_order"}, "reference_steps": 3,
        "required_order": ["lookup_order", "refund"],
        "success": lambda s: s["orders"]["A1"]["refunded"] == 40.0}
# runs = [score_run(record(agent, task), task, env.state()) for _ in range(10)]
# aggregate(runs) -> e.g. {"pass_rate": 0.8, "pass_hat_k": 0.512, "mean_steps": 3.4, ...}
```

## Trade-offs
- End-state checks are objective and cheap but need an environment you can inspect and reset; when the
  environment is a customer's live system, you fall back to recorded tools and lose fidelity.
- Process metrics are precise and judgment-free, yet they encode assumptions about the right path; a
  strict step budget penalizes a legitimately harder instance, so treat them as diagnostics unless the
  budget is a product requirement.
- Repeating each task N times gives trustworthy rates at N times the cost; for expensive live suites, use
  fewer tasks with more repeats rather than many tasks run once, because variance is the thing you are
  trying to measure.
- LLM judges scale to transcripts no human will read but are lenient and drift; calibrate on a labelled
  sample and never let a judge be the only gate on a side-effecting agent.

## Pitfalls
- Scoring the agent's own claim of success; the symptom is a high eval score and users reporting that
  "done" tasks were not done.
- Exact-matching trajectories against a golden path; the symptom is a shorter, better trajectory failing
  the eval and engineers tuning prompts to imitate the reference.
- Running each task once; the symptom is a "regression" or an "improvement" that disappears on the next
  run of the same suite.
- Ignoring policy violations when the outcome is correct; the symptom is a release that passes the gate
  and an incident caused by a forbidden action the eval counted as success.
- Evaluating only in simulation; the symptom is a suite at 95% and a production completion rate of 70%
  because real tools time out, paginate and return mess.
- No failure taxonomy; the symptom is a dashboard with a single number that goes down and a team with no
  idea which prompt, tool or policy to change.

## When to use
Build the trajectory suite as soon as the agent has more than one tool, and run it on every prompt, tool,
model or policy change. Use it for release gates (outcome and policy over N runs), for diagnosis (process
metrics and failure classes), and for model comparisons (same tasks, same environment, both models).
Measure pass rate with an interval, pass^k for unattended agents, violation count, mean steps and cost per
successful run, and track the failure-class histogram over time. In daily work the question "why did this
get worse" is answered by diffing failure classes between two suite runs. In interviews, the expected
depth is the distinction between outcome, process and policy scores, and why pass^k, not pass@k, is the
number for an autonomous agent.

## Role lens
- **Engineer:** Builds the harness that records trajectories, writes scorers over the event stream, and
  keeps a labelled set of failed runs for calibrating judges.
- **Architect:** Defines the task specification format, the policy rules that zero a run, the N and the
  confidence level a release gate requires, and the simulated-versus-live split.
- **FDE:** Builds the customer's task suite from real tickets, defines success predicates with their
  domain experts, and reports pass^k rather than a demo success story.

## Check yourself
**Q:** Your agent's outcome score is 85% on a 50-task suite, run once per task. The product owner wants
to ship. What is wrong with that number, and what do you report instead?
**A:** One run per task gives a point estimate with wide uncertainty; at n = 50 a 95% interval spans
roughly ten points either side, so 85% is not distinguishable from 75% or 93%, and a prompt tweak could
move it by noise alone. It is also the wrong statistic for an unattended agent: users get one attempt, so
the relevant question is consistency, pass^k, not the per-run average. And outcome alone hides policy
violations and expensive paths. Rerun the suite with at least five runs per task, report the pass rate
with an interval, report pass^3 (an 85% per-run agent is around 61% under independence), report the
number of runs with a policy violation, which should be zero, and show the failure-class histogram so the
decision is about the specific failures, not a single number. Then ship against a stated bar on those
figures.

## Quiz
### Q: Why is pass^k rather than pass@k the right headline metric for an unattended agent?
- [ ] pass^k is easier to compute from a single run
- [x] Unattended users get one attempt, so consistency across attempts matters; pass^k requires all k runs to succeed while pass@k rewards any one success
- [ ] pass@k only applies to code generation benchmarks
- [ ] pass^k includes policy violations and pass@k does not
> pass@k models "a verifier picks the best of k"; without a verifier or a human choosing, the probability
> that every attempt succeeds is the one users experience.

### Q: An agent reaches the correct end state but called a forbidden `delete_order` tool along the way. How should the run be scored?
- [ ] Success, because the end state is correct
- [ ] Partial credit proportional to the number of correct steps
- [x] Failure, because a policy violation is a product failure regardless of outcome
- [ ] Success, with the violation logged separately as a warning
> Trajectory evaluation exists precisely to catch correct outcomes reached by unacceptable paths; a gate
> that passes this run will pass the same behaviour into production.

### Q: What is the advantage of checking required actions as an ordered subsequence instead of an exact trajectory match?
- [ ] It makes the check run faster
- [x] It accepts any valid path that performs the required actions in order while still catching missing or misordered steps
- [ ] It removes the need for an outcome check
- [ ] It allows forbidden actions as long as the required ones are present
> Many trajectories are valid; the subsequence check encodes the constraints that matter (lookup before
> refund) without penalizing a shorter or different path.
