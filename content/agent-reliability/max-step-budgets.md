---
id: max-step-budgets
module: agent-reliability
name: Max-step budgets
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
related:
  - agent-reliability/stopping-conditions-termination
  - agent-reliability/loop-detection
  - agent-reliability/durable-execution-and-checkpointing
  - agents/agent-loop
  - observability/token-cost-tracking
  - infra-capacity-economics/latency-budgets
  - serving-inference/timeouts
---

## TL;DR
A max-step budget caps how many model calls (and, more usefully, how many tokens, seconds and
dollars) one run may spend before the harness stops it. It is a backstop against runs that never
terminate, not a target: set it from the step distribution of successful runs so it rarely fires,
warn the model before it fires so it can hand back a labelled partial result, and treat every
exhaustion as a stuck-agent signal worth investigating.

## Why it exists
A model that is stuck will keep calling tools until something external stops it, and each additional
step costs more than the last because the accumulated context is re-sent on every call. Without a
cap, a single stuck run can burn hours of wall-clock and a surprising amount of money before anyone
notices, and a burst of stuck runs (a tool outage that makes every call fail) multiplies that across
every concurrent user. The cap is the one guard that needs no intelligence to work: it cannot be
fooled by a confident model, a mislabelled tool result or a failure mode nobody anticipated.

The obvious implementation, `for step in range(20)`, is where most teams start and where many
problems hide. Twenty of what? A model call that emits five parallel tool calls is one step or five.
A step at the start costs a few thousand input tokens; step forty, with the whole history attached,
may cost tens of thousands. So a step count is a loose proxy for what you actually want to bound,
which is cost and time. And hitting the limit by raising an exception loses the work in flight: the
model never gets to say "here is what I have so far and what is blocking me", which is often the
most useful output of a failed run.

Budgets therefore need several dimensions, a soft threshold before the hard one, and an exhaustion
path that preserves state instead of discarding it.

## How it works
### Dimensions
- **Steps**: model invocations, one per loop iteration. Cheap to count, coarse in meaning.
- **Tool calls**: finer-grained, and per-tool caps ("no more than five web searches") encode task
  knowledge the model lacks.
- **Tokens**: cumulative input plus output. Because the input is re-sent each turn, cumulative input
  grows roughly quadratically with steps when context is not compacted. Illustrative arithmetic: if
  each step adds about 2k tokens of context, twenty steps re-send about 2k × (1 + 2 + … + 20) ≈ 420k
  input tokens. This is why token budgets, not step budgets, are what protect cost. Prompt caching,
  where the provider offers it, discounts the re-sent prefix but does not shrink the token count or
  the context-window limit, so the budget should still count tokens and price them separately.
- **Wall-clock**: bounds user-facing latency and protects against tools that hang. It must be paired
  with per-call timeouts; a ten-minute budget does nothing if a single tool call blocks for thirty.
- **Money**: tokens × price plus tool costs (paid APIs, compute). The dimension finance cares about.
- **Depth and fan-out**: for sub-agent trees, how deep delegation may recurse and how many children
  a parent may spawn.

### Setting the number
Measure the step and token distribution of *successful* runs per task class. Set the hard limit
above a high percentile of that distribution (p95 to p99 plus margin), not at the mean: at the mean
you cut off the hard-but-solvable tail, which is exactly where agents earn their keep. Then watch the
exhaustion rate; a backstop that fires on more than a few percent of runs is not a budget problem but
a termination or loop problem. Different task classes get different budgets: "fix a typo" and
"migrate a module" should not share one.

### Soft threshold
At some fraction of the budget (around 75–80% is a common starting point) inject a message: "You have
used 16 of 20 steps. Finish up: submit your best result and list what remains." Because a model can
usually produce a coherent partial answer when told to, this converts a hard failure into a labelled
partial result. Below the soft line the model should not need to know the budget exists, or it may
ration effort unnecessarily; whether to disclose it at all is a judgement call worth measuring.

### Hard threshold
When any dimension is exceeded: stop issuing model calls, persist the state (checkpoint) so a human
or a retry with a larger budget can resume, and return status `partial` or `exhausted` with the last
draft, never `succeeded`. Do not raise and lose the transcript.

### Hierarchy
A parent agent delegating to sub-agents gives each a sub-budget, and the sum of the children's
allowances must not exceed the parent's remaining budget. Children charge against their own budget
and report usage upward; the parent debits on return. Without this a single sub-agent can spend the
parent's whole allowance. Above the run, enforce tenant-level and platform-level caps (a tenant's
daily token quota, a global concurrency cap), because a thousand runs each within budget can still be
an incident.

### Accounting
Charge after each model response using the usage fields the API returns (input and output tokens),
not estimates; charge tool costs as they occur; and store the counters in the checkpoint so a resumed
run does not start from zero.

Post-hoc charging has a gap: the check runs *before* a call, but the cost is only known *after* it.
At 390k of a 400k-token budget, a check that asks "are we over?" says no, and the next call re-sends
a 60k-token context and lands at 450k. Close the gap by projecting: the next call's input is the
current context size (known, or counted with the provider's token counter) and its output is bounded
by `max_tokens`, so refuse the call if `used + context + max_tokens` would exceed the limit. The same
projection tells you when to fire the wrap-up turn instead of another working step.

### Reserve for the wrap-up
The partial result that makes exhaustion useful costs a model call of its own. If the hard limit is
the whole allowance, that call cannot be afforded at exactly the moment it is needed. Hold back a
reserve (one context-sized call is the natural unit) that only the wrap-up may spend, and send that
call with tools withheld so the model summarises instead of acting.

## Example
The budget tracks three dimensions at once, fires a single soft warning at 80% of whichever is
closest to its limit, refuses a call whose *projected* cost would breach the token limit, and returns
a labelled partial rather than raising. `child()` hands a sub-agent a slice of what remains and
`absorb()` debits the parent when the child returns, so delegation cannot spend more than the parent
had.

Why the warning also fires on `last`: trace a run whose context grows by about 15k tokens per
step, so step *n* sends roughly 15k × n and cumulative spend after *n* steps is 15k × n(n+1)/2.
After five steps it has used 225k (56%). Step six would send 90k, which fits (225k + 90k + 4k ≤ 400k),
but six and seven together would not, so the warning goes out with step six. After it the run has
used 315k, still under the 80% line, and step seven would project to 424k, so the loop stops with a
partial. A purely fractional soft line would never have fired: with quadratic growth the jump from
"under 80%" to "cannot afford another call" happens in one step, which is why the warning must be
driven by the projection, not only by the fraction.

```python
import time
from dataclasses import dataclass, field

@dataclass
class Budget:
    max_steps: int = 20; max_tokens: int = 400_000; max_seconds: float = 600
    soft_fraction: float = 0.8; steps: int = 0; tokens: int = 0; warned: bool = False
    started: float = field(default_factory=time.monotonic)

    def charge(self, usage):                     # usage block from the API response
        self.steps += 1
        self.tokens += usage.input_tokens + usage.output_tokens

    def fraction_used(self) -> float:
        return max(self.steps / self.max_steps, self.tokens / self.max_tokens,
                   (time.monotonic() - self.started) / self.max_seconds)

    def can_afford(self, ctx: int, max_out: int, calls: int = 1) -> bool:  # project first
        return (self.steps + calls <= self.max_steps
                and time.monotonic() - self.started < self.max_seconds
                and self.tokens + calls * (ctx + max_out) <= self.max_tokens)

    def child(self, share: float = 0.25) -> "Budget":   # slice of what is left, never 0
        left = max(0.0, 1 - self.fraction_used()) * share
        return Budget(max_steps=max(1, int(self.max_steps * left)),
                      max_tokens=max(1, int(self.max_tokens * left)),
                      max_seconds=max(1.0, self.max_seconds * left))

    def absorb(self, child: "Budget"):          # parent pays for the child on return
        self.steps += child.steps; self.tokens += child.tokens

def run(state, model, budget: Budget, max_out=4_000):
    while True:
        ctx = state.context_tokens()
        if not budget.can_afford(ctx, max_out):  # wrap_up: no-tools call, paid from a
            return {"status": "partial", "reason": "budget_exhausted",   # reserve kept
                    "draft": state.wrap_up()}                            # outside max_tokens
        last = not budget.can_afford(ctx, max_out, calls=2)   # room for one more call only
        if not budget.warned and (last or budget.fraction_used() >= budget.soft_fraction):
            budget.warned = True
            state.push_user("Budget nearly used. Submit your best result now "
                            "and list anything unfinished.")
        resp = model(state.messages)
        budget.charge(resp.usage)
        if state.handle(resp):                   # True once submit_result is accepted
            return {"status": "succeeded", "result": state.result}
```

## Trade-offs
- A tight budget bounds cost and latency precisely, but truncates the long-tail tasks that justify
  using an agent; it flips when the exhaustion rate on solvable tasks costs more than the spend saved.
- Warning the model about remaining budget yields usable partials, but warning too early makes it
  rush or skip verification; keep the soft line late and measure quality on runs that got the warning.
- Step counts are trivial to implement and explain, yet poorly correlated with cost once contexts
  grow; token and money budgets track the real constraint at the price of accurate usage accounting.
- Hierarchical sub-budgets keep a delegation tree bounded, but starve deep sub-tasks when the parent
  has already spent most of its allowance; dynamic reallocation helps but complicates accounting.
- Projecting cost before each call never overshoots the limit, but stops runs a step early whenever
  `max_tokens` is set far above typical output; size `max_tokens` from observed output lengths, or
  the projection becomes the binding constraint instead of real spend.

## Pitfalls
- Counting steps but not tokens, then being surprised by the bill; the symptom is cost per run
  growing far faster than steps per run as tasks lengthen.
- Raising an exception on exhaustion and discarding the transcript; the symptom is support unable to
  see what the agent did, and users receiving a bare "failed" with no partial output.
- Resetting the budget to zero on resume from a checkpoint; the symptom is a stuck run that is
  retried automatically consuming several budgets instead of one.
- Sharing one budget constant across every task type; the symptom is trivial tasks with absurd
  headroom while complex ones are cut off at a fixed step count regardless of progress.
- A wall-clock budget without per-call timeouts; the symptom is a run that "times out" long after
  its budget because one tool call hung and nothing interrupted it.
- Checking the budget only after charging; the symptom is runs that end 10–20% over their token
  limit, because the final call's cost was unknown until it had already been paid.

## When to use
Always on, from the first prototype: the prototype without a cap is the one that runs overnight.
Choose dimensions by what you are protecting: tokens or money for cost, wall-clock for interactive
latency, depth and fan-out for multi-agent systems, per-tool caps for expensive or rate-limited
tools. Measure the exhaustion rate per task class, the median and p95 steps and tokens of successful
runs, and the quality of partial results produced after the soft warning. Day to day this is the
parameter you revisit after every "why did this run cost that much" conversation. In interviews,
explain why steps are a proxy, how you would pick the number from data, and what happens to the state
when the limit fires.

## Role lens
- **Engineer:** Implements the Budget object, wires usage accounting from API responses, persists the
  counters in checkpoints and writes the soft-warning message the model actually responds to.
- **Architect:** Sets the budget policy per task class and the tenant and platform caps above it,
  and decides what "exhausted" means for automatic retries and billing.
- **FDE:** Calibrates budgets against the customer's real task mix and cost ceiling, and reports
  exhaustion rates as the early sign that a workflow has outgrown its current design.

## Check yourself
**Q:** Your agent's step cap is 15. Finance reports that cost per run doubled last month while median
steps rose only from 8 to 10. What is happening, and what do you change?
**A:** Cost is driven by cumulative tokens, and cumulative input grows roughly with the square of the
step count when the whole history is re-sent on every call. Do the arithmetic, though: with a constant
amount added per step, input scales with n(n+1)/2, which goes from 36 to 55 units between 8 and 10
steps, about 1.5x. So the step rise explains part of the doubling, not all of it. The rest has to come
from what each step carries (larger tool outputs, a bigger system prompt, a dropped cache) or from the
tail: cost per run is a mean, and a few long runs near the cap move it far more than the median
reveals. First confirm with per-run token accounting split by input and output, by step and by
percentile.
Then add a token budget alongside the step cap, with a soft warning, and attack the cause: truncate
or compact tool outputs, summarize older context, and check whether a tool started returning much
larger payloads. Report the exhaustion rate by task class rather than lowering the step cap blindly.

## Quiz
### Q: Why is a token or cost budget a better primary guard than a step count?
- [ ] Steps cannot be counted reliably when the model emits parallel tool calls
- [x] Per-step cost grows as the context accumulates, so steps correlate poorly with the money and latency you actually want to bound
- [ ] Token budgets are enforced automatically by the model provider
- [ ] Step counts cannot be stored in checkpoints
> Each model call re-sends the growing history, so late steps cost many times what early ones do; a
> step cap bounds iterations but not spend. Token and money budgets measure the real constraint.

### Q: How should the hard budget number be chosen for a task class?
- [ ] Set it to the mean step count across all runs
- [ ] Set it as low as possible to minimise cost
- [x] Set it above a high percentile of successful runs' step and token usage, then monitor the exhaustion rate
- [ ] Let the model choose its own budget per task
> The budget is a backstop that should rarely fire; cutting at the mean removes the hard-but-solvable
> tail. A budget that fires often signals a termination or loop problem, not a budget problem.

### Q: What should happen when the hard budget is exceeded mid-run?
- [ ] Raise an exception and discard the run
- [ ] Silently return the last model message as the final answer
- [x] Stop calling the model, checkpoint the state and return a result labelled partial or exhausted
- [ ] Double the budget and continue
> Exhaustion must be visible to callers and metrics, and the state must survive so a human or a retry
> can resume. Returning the last message as success hides the failure; discarding loses the work.
