---
id: task-completion-rate
module: agent-reliability
name: Task completion rate
kind: metric
difficulty: core
est_minutes: 12
related:
  - agent-reliability/agent-trajectory-evaluation
  - agent-reliability/tool-call-success-rate
  - agent-reliability/max-step-budgets
  - evals/pass-k
  - evals/task-specific-acceptance-criteria
  - evals/offline-vs-online-evaluation
  - infra-capacity-economics/unit-economics-per-successful-task
  - reliability/slas-slos
sources:
  - Chen et al., 2021 — Evaluating Large Language Models Trained on Code
  - Yao et al., 2024 — τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains
---

## TL;DR
Task completion rate is the fraction of tasks an agent finishes successfully, judged against a defined
success criterion rather than its own claim of being done. It is the number product, finance and customers
care about, and it is only as good as three definitions behind it: what a task is, what counts as
complete, and what goes in the denominator. Report it with an interval, segmented by task type, and
alongside pass^k for unattended use.

## Why it exists
An agent product lives or dies on one question: does it actually finish the job? Latency, token cost and
tool metrics are inputs; completion rate is the output, and unit economics (cost per *successful* task)
divide by it directly. Without a disciplined completion metric, every other number floats free: a 30%
cost reduction means nothing if completion fell from 80% to 60%.

The naive measurement is to count the runs that ended with the model saying "done". It fails because the
model's self-report is the least reliable signal in the system; agents confidently declare completion on
tasks they half-did, misread, or abandoned, and the gap between claimed and verified completion is
routinely large enough to change a launch decision. The second naive move is to use whatever runs happened
in production as the denominator without care: abandoned sessions, out-of-scope requests and clarifying
questions all end up counted as failures or quietly excluded, depending on who ran the query, so two
dashboards show two different rates for the same week.

The metric therefore needs the same discipline as a service-level indicator: a precise definition of the
unit, the success predicate, the denominator and the measurement method, and a statistical treatment
that respects how few tasks you actually have.

## How it works
### Define the unit
A task is a user goal with a clear end condition: "refund order A1", "fix the failing test in repo R",
"answer the question with a cited source". Multi-turn sessions may contain several tasks; a long task may
be decomposed into subtasks whose completion is not the same as the parent's. Pick the grain that matches
what the user wants done and keep it fixed, *because* changing the grain changes the rate without
changing the agent.

### Define success
In descending order of trust:

1. **Verifiable end state.** The order shows refunded, the tests pass, the file exists with the right
   contents. Automatable, objective, preferred wherever the environment can be inspected.
2. **Human acceptance.** The user or a reviewer confirms the task is done. Ground truth for subjective
   tasks, expensive, and biased by who bothers to respond.
3. **LLM judge with a rubric.** Scales, but must be calibrated against (1) or (2) on a sample and
   re-calibrated when the judge model changes.
4. **Self-report.** The agent said done. Useful only as a leading indicator of the gap between claimed and
   verified success.

Partial completion is tempting and usually wrong as a headline: a task that is 80% done often has zero
value to the user. Keep success binary at the task grain and track partial progress as a separate
diagnostic.

### Define the denominator
Decide explicitly, and write it down, how each of these counts: tasks the agent declined as out of scope
(usually excluded, tracked separately as *scope rate*), tasks that ended in a clarifying question the user
never answered (excluded from completion, counted in *abandonment*), tasks that hit a step budget or a
timeout (failures), tasks completed only after a human intervened (a separate *assisted completion
rate*). The unassisted rate is the autonomy number; the assisted rate is the containment number a
support organisation cares about; mixing them hides both.

### Attempts versus tasks
Per-attempt completion is the probability one run succeeds. For agents with retries or a human picking the
best of several, pass@k applies. For unattended agents the relevant number is pass^k, the probability that
every one of k attempts succeeds, because each user gets one attempt and consistency is what they
experience. With per-attempt rate p and independence, pass^k ≈ p^k; the unbiased estimate from n trials
with c successes is C(c, k) / C(n, k), averaged over tasks. An agent at 90% per attempt is at about 73%
for pass^3 and 59% for pass^5.

### Statistics
Completion rates come from small samples. With n tasks and c successes, use a Wilson interval rather than
the normal approximation, which misbehaves near 0 and 1 and at small n. Segment by task type and
difficulty, *because* the overall rate moves when the task mix shifts, which looks like a regression and
is not. Track it as a time series per release and gate releases on a stated bar with a stated interval.

### Offline and online
Offline, the rate comes from a task suite with known success predicates, run N times. Online, it comes
from production runs with verified outcomes where possible and sampled human review where not. The two
should be reported side by side; a large gap between them is the most useful signal that the suite is not
representative.

## Example
A worked scenario, then the code that computes it. A release candidate runs a 200-task suite: 150 verified
successes, 30 failures, 12 clarification dead ends, 8 declined as out of scope. Excluding declined and
abandoned tasks, the denominator is 180 and the per-attempt rate is 150/180 = 83.3%. The Wilson 95%
interval is roughly 77% to 88%. The agent runs unattended, so the team also estimates pass^3 ≈ 0.833³ ≈
58% under independence, and segments: billing tasks 92% (n = 90), code tasks 74% (n = 90). The headline is
not "83%"; it is "billing is shippable, code is not, and consistency across attempts is below the bar".
The second block in the code shows the unbiased pass^k estimate from repeated per-task trials.

```python
from math import sqrt, comb

def wilson(c: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = c / n; d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))

def pass_hat_k(trials: list[tuple[int, int]], k: int) -> float:
    """trials: per-task (successes c, attempts n). Unbiased pass^k averaged over tasks."""
    return sum(comb(c, k) / comb(n, k) for c, n in trials if n >= k) / len(trials)

results = {"verified_success": 150, "failed": 30, "abandoned": 12, "declined": 8}
n = results["verified_success"] + results["failed"]           # denominator policy: exclude abandoned, declined
p = results["verified_success"] / n
lo, hi = wilson(results["verified_success"], n)
print(f"completion {p:.1%}  95% CI [{lo:.1%}, {hi:.1%}]  n={n}")
print(f"scope rate {1 - results['declined'] / 200:.1%}   abandonment {results['abandoned'] / 200:.1%}")

# per-task repeated trials for the unattended bar: (successes, attempts)
trials = [(5, 5)] * 120 + [(4, 5)] * 40 + [(2, 5)] * 20
print(f"pass^1 {pass_hat_k(trials, 1):.1%}  pass^3 {pass_hat_k(trials, 3):.1%}")
# -> completion 83.3%  95% CI [77.2%, 88.1%]  n=180 ; pass^1 88.9%  pass^3 75.6%
```

## Trade-offs
- Verified end-state success is objective but limits you to tasks whose outcome you can inspect; for
  advisory or creative tasks you must fall back to human acceptance or a calibrated judge and accept wider
  error bars.
- Binary success at the task grain is honest about user value but hides progress on long tasks; track
  partial progress as a diagnostic, never as the headline, or the rate inflates without the product
  improving.
- Excluding declined and abandoned tasks from the denominator isolates agent quality, but an agent that
  declines aggressively will look better while helping less; report scope and abandonment rates beside it.
- pass^k is the honest number for unattended agents and it is always lower than the per-attempt rate,
  which makes it unpopular; the condition that justifies reporting pass@k instead is a real verifier or
  human choosing among attempts.

## Pitfalls
- Counting "the model said done" as completion; the symptom is a dashboard at 90% and a support queue full
  of half-finished tasks.
- Changing the task grain between releases; the symptom is a rate that jumps or drops with no change in
  agent behaviour.
- Reporting a single rate across a shifting task mix; the symptom is a "regression" that turns out to be a
  week with more hard tasks.
- Quoting 85% from 50 tasks without an interval; the symptom is launch decisions reversed by the next run
  of the same suite.
- Mixing assisted and unassisted completions; the symptom is an autonomy number that looks fine until the
  humans who were quietly fixing tasks go on holiday.
- An offline suite that never gets compared to online outcomes; the symptom is a suite at 95% and
  customers at 70%.

## When to use
Make completion rate the headline metric of any agent from the first prototype, and define its unit,
success predicate and denominator in a document before anyone quotes a number. Use the offline rate with
an interval as the release gate, the online verified rate as the health signal, and the gap between them
as the signal to refresh the suite. Pair it with cost per successful task and with tool-call success rate,
which explains most movements in it. In interviews, "how would you measure whether the agent works"
should produce the definitions, the denominator policy, the Wilson interval, segmentation, and the pass^k
argument for unattended agents, not a single percentage.

## Role lens
- **Engineer:** Implements the success predicates and the verification step in the harness, and makes sure
  the event stream carries the labels the rate is computed from.
- **Architect:** Writes the metric definition (unit, success, denominator, interval, segmentation) and sets
  the release bar and the pass^k requirement for unattended deployments.
- **FDE:** Agrees the success criteria with the customer's domain owners, reconciles offline and online
  rates in their environment, and reports the assisted and unassisted numbers separately.

## Check yourself
**Q:** Two teams report completion rates for the same agent over the same week: 88% and 71%. Neither is
lying. List the definitional differences that could explain the gap and say which number you would put in
front of a customer.
**A:** The candidates are the denominator and the success criterion. One team may exclude declined and
abandoned tasks, the other may count them as failures. One may count assisted completions, where a human
fixed the result, the other only unassisted ones. One may use the agent's self-report, the other verified
end state. One may measure per attempt, the other pass^k over repeated attempts. They may also use
different task grains, for example sessions versus subtasks, or different task mixes. For a customer I
would present the unassisted, verified, per-attempt rate with a Wilson interval and the denominator policy
stated, segmented by task type, and beside it the pass^k figure for the unattended case, because that is
the experience their users will actually have. The assisted rate goes in as a secondary line for their
support organisation.

## Quiz
### Q: Why is the agent's own "task complete" message a poor basis for completion rate?
- [ ] Because the message is not stored in the event stream
- [x] Because self-reported completion is unreliable and routinely overstates verified success
- [ ] Because it is only emitted at temperature 0
- [ ] Because completion must be judged by an LLM, not by the agent
> Models declare completion on tasks they misread or half-finished; the metric needs an independent
> success predicate, ideally a verified end state.

### Q: An agent succeeds on 90% of attempts. Roughly what is its pass^3, and why does it matter?
- [ ] 90%, because pass^k equals the per-attempt rate
- [ ] About 97%, because three attempts give more chances to succeed
- [x] About 73%, because all three attempts must succeed and unattended users each get one attempt
- [ ] About 30%, because pass^k divides the rate by k
> Under independence pass^k ≈ p^k, so 0.9³ ≈ 0.73; it measures consistency, which is what a user who gets
> a single run experiences. The 97% figure would be pass@3.

### Q: Which denominator policy best isolates agent quality while staying honest?
- [ ] Count every session, including out-of-scope requests, as a task
- [x] Exclude declined and abandoned tasks from completion but report scope and abandonment rates alongside
- [ ] Exclude all failed tasks that hit a step budget
- [ ] Include only tasks the agent marked as complete
> Removing declined and abandoned tasks keeps the rate about agent execution, and reporting those rates
> separately stops an agent from looking better by declining more.
