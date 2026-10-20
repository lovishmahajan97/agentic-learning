---
id: tool-call-success-rate
module: agent-reliability
name: Tool-call success rate
kind: metric
difficulty: core
est_minutes: 12
related:
  - agent-reliability/task-completion-rate
  - agent-reliability/tool-adapters-service-interfaces
  - agent-reliability/agent-trajectory-evaluation
  - agent-reliability/idempotent-tool-execution
  - agents/tool-schemas-and-argument-validation
  - agents/tool-selection-accuracy
  - serving-inference/retry-strategies
  - observability/monitoring
sources:
  - Official docs: OpenTelemetry (semantic conventions for generative AI)
  - Patil et al., 2023 — Gorilla: Large Language Model Connected with Massive APIs
---

## TL;DR
Tool-call success rate is the fraction of tool invocations that succeed, and it is only useful when
"succeed" is split into layers: the call was well-formed, it was permitted, it executed without an
infrastructure error, and it achieved what the model intended. Because a task is a chain of calls,
per-call failure compounds: ten steps at 95% is about 60% end to end. The metric localizes failures to
model, policy, tool or environment, which task completion rate cannot.

## Why it exists
Task completion rate tells you the agent is failing; it does not tell you why. Most agent failures are
tool failures in disguise: a malformed argument, a 429 nobody retried, a search that returned nothing
useful, a result the model misread. These are invisible in an outcome metric and obvious in a per-tool
breakdown. The compounding is the reason the number matters so much. If a task needs ten sequential
calls and each succeeds with probability p, the chance all ten succeed is p¹⁰ (ignoring recovery):
0.99¹⁰ ≈ 0.90, 0.95¹⁰ ≈ 0.60, 0.90¹⁰ ≈ 0.35. A tool that fails one call in twenty does not sound broken;
it caps a ten-step agent at about 60%.

The naive measurement counts HTTP 200s, and it misleads in both directions. It overstates success because
a 200 with an empty result list, a wrong record, or a truncated payload is counted as fine while the model
proceeds on bad data. It understates it because a retry that eventually succeeds is counted as one or more
failures and one success, hiding that the step was in fact fine. It also says nothing about the calls that
never reached the service: schema-invalid arguments rejected by the adapter, or calls denied by policy,
which are model and policy problems rather than tool problems.

The metric only becomes actionable when every call is classified into a small, shared error taxonomy by
the one component that sees everything, the tool adapter, and when rates are computed per tool, per error
class and per cause.

## How it works
### The four layers of "success"
1. **Well-formed.** The arguments parse and validate against the schema. Failure here is the model's:
   wrong type, missing field, hallucinated enum value. Rate: schema-valid calls / calls attempted.
2. **Permitted.** The policy allowed the call (or it was approved). Failure here is a policy or prompt
   problem: the model keeps asking for what it cannot have. Rate: allowed / well-formed.
3. **Executed.** The service returned a non-error result within the timeout. Failure here is
   infrastructure: timeouts, 5xx, rate limits, auth expiry. Rate: executed / permitted. This is the layer
   the 200-count measures.
4. **Effective.** The result served the intent: the search had relevant hits, the lookup found the record,
   the write changed state. Failure here is subtler, a right call with wrong arguments, or a tool that
   technically worked and returned nothing. It is measured with cheap heuristics (empty result, zero rows
   affected, result never referenced by the next step) and, in evaluation, with reference checks or a
   judge.

The overall rate is the product of the four, and that product is usually less informative than the
layers.

### Instrumentation
The adapter emits one `tool.call.result` event per call with `tool`, `error_class` from a fixed taxonomy
(`ok`, `invalid_arguments`, `denied`, `upstream_timeout`, `upstream_error`, `rate_limited`, `not_found`,
`empty_result`), `attempt` number, `latency_ms` and `retryable`. Rates are then simple aggregations, and
the taxonomy maps each class to its layer and its likely owner. Keeping the taxonomy small and shared
across tools is the design choice that makes the metric comparable across tools and over time.

### Attempts versus steps
Count both. *Per-attempt* success tells you how healthy the tool is. *Per-step* success, where a step is
the model's intent and may include retries, tells you how often the agent got past it. A tool at 85% per
attempt with automatic retry on retryable classes might be 98% per step; the gap is the value of the retry
policy, and a widening gap means the tool is degrading while the agent still copes.

### Attribution
Each error class points at an owner. `invalid_arguments` rising after a prompt change points at the prompt
or the tool description. `denied` rising points at a policy that is tighter than the task needs or a model
probing. `upstream_*` rising for one tool points at the vendor, and it is the earliest signal of an API
change. `empty_result` rising points at the retrieval index or at argument quality. Alerts are set per
tool and per class *because* an aggregate rate averages a broken tool against nine healthy ones.

### Relation to selection accuracy
Success rate measures the calls that were made. It does not catch the call that should have been made and
was not, or the wrong tool chosen with valid arguments. Those are tool selection accuracy, measured in
evaluation against reference trajectories. The two together cover "did it choose right" and "did it
work".

### Targets
There is no universal number; the useful bar comes from the compounding arithmetic. Decide the end-to-end
completion you need and the typical step count, and solve for the per-step rate: a 90% ten-step task
needs roughly 99% per step after retries. That is the target for the layer-3 and layer-4 rates, and it is
why idempotent retries matter so much.

## Example
Per-tool, per-class rates from the event stream, with the layered breakdown and the per-attempt versus
per-step distinction. Running this over a day's events gives the table that identifies which tool, and
which owner, is dragging completion down. The sample data is illustrative.

```python
from collections import defaultdict

LAYER = {"invalid_arguments": "well_formed", "denied": "permitted",
         "upstream_timeout": "executed", "upstream_error": "executed", "rate_limited": "executed",
         "not_found": "effective", "empty_result": "effective"}

def tool_rates(events: list[dict]) -> dict:
    by_tool = defaultdict(lambda: defaultdict(int))
    steps = defaultdict(lambda: defaultdict(set))                 # tool -> step_id -> {ok?}
    for e in events:
        if e["type"] != "tool.call.result":
            continue
        t, cls = e["tool"], e["error_class"]
        by_tool[t]["attempts"] += 1
        by_tool[t][cls] += 1
        steps[t][e["step_id"]].add(cls == "ok")
    out = {}
    for t, c in by_tool.items():
        n = c["attempts"]
        layers = defaultdict(int)
        for cls, k in c.items():
            if cls in LAYER:
                layers[LAYER[cls]] += k
        per_step = sum(True in s for s in steps[t].values()) / max(len(steps[t]), 1)
        out[t] = {"attempts": n, "per_attempt": c["ok"] / n, "per_step": per_step,
                  "model_fail": layers["well_formed"] / n, "policy_fail": layers["permitted"] / n,
                  "infra_fail": layers["executed"] / n, "semantic_fail": layers["effective"] / n}
    return out

def mk(tool, cls, ids):
    return [{"type": "tool.call.result", "tool": tool, "step_id": f"{tool}{i}", "error_class": cls} for i in ids]

events = (mk("search", "ok", range(95)) + mk("search", "empty_result", range(95, 100))
        + mk("lookup", "invalid_arguments", range(12)) + mk("lookup", "ok", range(12, 100))
        + mk("refund", "rate_limited", range(20)) + mk("refund", "ok", range(60)))   # refund0..19 retried after 429

for tool, r in tool_rates(events).items():
    print(tool, {k: round(v, 3) if isinstance(v, float) else v for k, v in r.items()})
# search: per_attempt 0.95, semantic_fail 0.05           -> index or argument quality
# lookup: per_attempt 0.88, model_fail 0.12              -> prompt or tool description
# refund: per_attempt 0.75, per_step 1.0, infra_fail 0.25 -> vendor rate limit; retries are carrying it
```

## Trade-offs
- A small shared error taxonomy makes rates comparable across tools and attributable to owners, but it
  loses vendor-specific detail; keep the raw error in the payload reference and the class in the metric.
- Per-step success (after retries) reflects what the agent experiences, while per-attempt reflects tool
  health; reporting only the former hides a degrading vendor until retries stop saving you.
- Measuring the effectiveness layer with heuristics like empty results is cheap and catches real problems,
  but it misclassifies legitimately empty answers; when the base rate of true empties is high, move that
  check into evaluation with references instead of production monitoring.
- Automatic retries raise per-step success but cost latency and, for non-idempotent tools, risk
  duplicates; retry only classes marked retryable and only through adapters with idempotency keys.

## Pitfalls
- Counting HTTP 200 as success; the symptom is a tool at 99% while the trajectory evals show the model
  acting on empty or wrong results.
- Aggregating across tools; the symptom is a healthy-looking 96% overall while one critical tool sits at
  70% and caps every task that needs it.
- Not distinguishing schema-invalid from upstream failures; the symptom is an infrastructure team paged
  for a problem caused by a prompt change.
- Counting each retry as an independent failure without a per-step view; the symptom is panic over a 429
  storm that the agent is actually surviving.
- Ignoring the compounding arithmetic; the symptom is a team happy with 95% per tool and puzzled that
  ten-step tasks complete 60% of the time.
- Treating denied calls as tool failures; the symptom is loosening a permission policy to improve a metric
  that was signalling a model probing beyond its scope.

## When to use
Instrument it from the first tool, because the adapter already sees every outcome and the cost is one
event per call. Use per-tool, per-class dashboards for operations, with alerts per tool rather than on the
aggregate, and use the layered breakdown in post-mortems and in trajectory evaluation to assign each
failure an owner. Derive targets from the compounding formula and the step counts of your real tasks.
Watch per-attempt and per-step side by side so vendor degradation is visible before it reaches users. In
interviews, the expected answer to "how do you know your tools work" is the four layers, the taxonomy,
attribution to an owner, and the p^n argument that links tool reliability to task completion.

## Role lens
- **Engineer:** Emits a classified result event from every adapter, builds the per-tool breakdown, and
  wires retries only to retryable classes with idempotency keys.
- **Architect:** Owns the error taxonomy and its layer mapping, sets per-tool targets from the compounding
  arithmetic, and decides which classes page which team.
- **FDE:** Uses the per-tool breakdown to show a customer that completion dropped because their API
  started rate-limiting, not because the model got worse.

## Check yourself
**Q:** Completion rate on ten-step tasks fell from 80% to 55% this week with no prompt or model change.
The aggregate tool success rate is still 96%. Where do you look, and what do you expect to find?
**A:** The aggregate is averaging nine healthy tools against one broken one, so I break it down per tool
and per error class. A drop from 80% to 55% on ten-step tasks is consistent with one tool used in most
tasks falling by a few points per step, because the per-step rates multiply across the chain. I would
expect to see a single tool with a rising `upstream_timeout`, `rate_limited` or `empty_result` class,
which points at a vendor change, a quota, or a retrieval index issue respectively, and I would compare
per-attempt with per-step rates to see whether retries are already masking part of it. If instead
`invalid_arguments` rose, something changed in the tool description or schema even though the prompt did
not. The fix follows the class: backoff and idempotent retries for infrastructure, a schema or description
change for arguments, an index check for empties.

## Quiz
### Q: Why does a tool with 95% per-call success matter more than it sounds for an agent?
- [ ] Because tool calls are billed at a higher rate than model tokens
- [x] Because multi-step tasks compound per-call failure, so ten sequential calls at 95% succeed end to end only about 60% of the time
- [ ] Because the model refuses to use tools below 99% success
- [ ] Because 95% is below the industry standard of 99.9%
> Task success is roughly the product of step successes; 0.95¹⁰ ≈ 0.60. Small per-tool losses become
> large task losses.

### Q: A tool returns HTTP 200 with an empty list for a query that should have matched. Which layer failed?
- [ ] Well-formed, because the arguments were wrong
- [ ] Executed, because the service did not return data
- [x] Effective, because the call ran but did not serve the intent
- [ ] Permitted, because the policy should have blocked an unproductive call
> The call parsed, was allowed and executed; it failed at the semantic layer, which a 200-count cannot
> see and which the model may not notice either.

### Q: After a 429 storm, per-attempt success for one tool is 75% while per-step success is still 99%. What does this tell you?
- [ ] The metric is broken and should be recomputed
- [x] Retries are absorbing the failures, so users are unaffected for now but the vendor or quota problem needs fixing before retries stop coping
- [ ] The model is sending invalid arguments on first attempts
- [ ] The policy is denying first attempts and approving retries
> Per-attempt measures tool health, per-step measures what the agent experiences; a widening gap is the
> early warning that a tool is degrading.
