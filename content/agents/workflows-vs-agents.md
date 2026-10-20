---
id: workflows-vs-agents
module: agents
name: Workflows vs agents
kind: decision
difficulty: intro
est_minutes: 12
status: reviewed
related:
  - agents/agent-loop
  - agents/state-machines
  - agents/frameworks
  - agents/planning
  - agents/approval-gates-for-consequential-actions
  - agent-reliability/max-step-budgets
  - agent-reliability/agent-trajectory-evaluation
  - system-design/workflow-orchestration
sources:
  - Anthropic, 2024 — Building effective agents
---

## TL;DR
A workflow is LLM calls orchestrated along paths your code defines; an agent is a loop in which the
model decides the next step from what it observes. The choice is about path predictability: if you can
draw the flowchart, build the workflow, because it is cheaper, bounded and unit-testable. Reach for an
agent when the next step depends on observations you cannot foresee. Most production systems are
workflows with agentic islands, not the other way round.

## Why it exists
"Agent" gets attached to everything from a prompt template to a fully autonomous loop, and the word
hides the decision that actually determines cost, latency, reliability and how you test the thing: who
chooses the next step, your code or the model? When the code chooses, every run follows a path you
wrote, the number of model calls is fixed, each step can be tested in isolation, and failures are
localized to a step. When the model chooses, runs vary, call counts have a heavy tail, failures are
trajectories rather than steps, and the only honest evaluation is end to end.

The two naive positions both lose. "Make everything an agent" is seductive because the demo is
impressive: one prompt, tools, and it figures things out. In production it means unbounded cost per
request, p99 latencies that can be many times the median, regressions you cannot localize, and an eval story that
amounts to "run it and see". "Never use agents" is safe but caps you at the cases you foresaw: a
document-processing pipeline cannot recover when the document is a kind it has never seen, and a
support flow cannot investigate an account anomaly that requires three lookups whose order depends on
the results.

The reason the decision is worth a lesson is that it is rarely binary. A workflow can contain an
agentic step for the one part of the task that is open-ended, and an agent can call workflow-shaped
tools that do a fixed job reliably. Knowing where to draw that boundary is the core skill: autonomy is
spent, like a budget, exactly where the environment is unpredictable, and nowhere else.

## How it works
### The spectrum of control
Think of a dial for "who decides the next step". At one end, a single call to an *augmented* model (one
with retrieval, tools and memory available): no control-flow decision at all. Then the workflow
patterns, where code owns control flow and the model fills in steps:
- **Prompt chaining:** a fixed sequence of calls, each transforming the previous output, with
  programmatic checks between them (generate outline, check length, write, check tone). Use when a
  task decomposes into fixed stages.
- **Routing:** a classifier call picks one of several specialized downstream paths. Use when inputs
  fall into distinct categories handled differently.
- **Parallelization:** the same input to several calls at once, either sectioned (each handles a part)
  or voted (same question, majority or any-flag). Use for speed or for confidence.
- **Orchestrator-workers:** an orchestrator call decomposes the task and dispatches sub-tasks to
  workers, then synthesizes. The decomposition is dynamic, but the orchestrator's role and the workers'
  tools are fixed.
- **Evaluator-optimizer:** one call produces, another critiques against criteria, loop until pass or
  budget. Use when you can state the criteria and iteration measurably improves output.
At the far end, the agent: a loop in which the model, given tools and the environment's feedback,
decides each next action and when to stop.

### What the choice changes
- **Cost:** workflow cost is roughly fixed per request; agent cost is a distribution with a long tail.
  Do not plan from the typical (median) run: the mean sits well above it, so plan totals from the mean
  measured on a realistic sample that includes the tail, and set per-request caps from the p95 or p99.
- **Latency:** workflows are predictable and can be parallelized by design; agent loops are serial by
  nature.
- **Reliability:** workflow failures are per step, retriable in place; agent failures are per
  trajectory and often only visible at the end. Both compound across steps (three steps at 99% each
  succeed about 97% of the time), but a workflow has a fixed, short chain with a check after each link,
  while an agent's chain length is itself a random variable, and its long runs are both the costly ones
  and the least reliable ones.
- **Testing:** workflow steps have unit tests with fixed inputs; agents need trajectory evals and
  golden task sets.
- **Debuggability:** a workflow trace is the same shape every time; an agent trace is unique per run.
- **Capability:** agents handle what you did not foresee, which is the only reason to pay the above.

### The decision criteria
Ask, in order. Can I draw the flowchart? If yes, workflow. Does the next step depend on information
that only exists after earlier steps run, in ways I cannot enumerate? If yes, that part is agentic.
What is the cost of an error and is it reversible? High and irreversible pushes toward workflows, or
toward agents with gates and budgets. What volume and latency budget? High volume with tight latency
pushes toward workflows. Can I evaluate it? If you cannot build a task set with success criteria, you
cannot safely run an agent on it.

### Moving the boundary over time
The boundary is not fixed at design time; logged trajectories tell you where to move it. If most of an
agent's runs follow the same two or three tool sequences, those sequences are a flowchart you did not
know you had: codify them as workflow branches, route matching inputs to them, and keep the agent for
the residue. In the other direction, a workflow whose exception queue or router branch count keeps
growing is telling you that part of the path is not predictable, and that step should become agentic.
Both moves are driven by the same data: path frequencies from traces and the exception rate.

### The hybrid, which is the usual answer
A workflow skeleton with agentic nodes: intake and routing by code, an agent for the investigation step
with a step budget and a restricted tool set, then fixed validation and delivery by code. Or an agent
whose tools are themselves workflows ("run the full reconciliation") so each tool call is reliable and
the agent only has to make a few high-level choices. The pattern is the same either way: put the
model's judgement where the path is unpredictable, and surround it with code where it is not.

## Example
A team automates invoice processing: extract fields, validate against the purchase-order system, route
for payment or exception handling. All figures are illustrative.

As a workflow: one extraction call with a structured-output schema, one validation step in code against
the PO database, one classification call to pick the route when validation fails. Three model calls or
fewer per invoice, about 6k tokens, roughly two seconds, deterministic shape. Each step has unit tests
with fixed inputs; a regression is a failing test on a step. At 50,000 invoices a month the cost is
predictable to within a few percent.

As an agent: one prompt with tools for OCR, PO lookup, vendor lookup and routing, and the instruction
to "process this invoice". It handles the strange cases well: a scanned invoice with two POs, a vendor
renamed since the PO was issued. But the median run is 9 calls and 30k tokens, the p95 is 25 calls, and
3% of runs hit the 40-step budget with no decision. Monthly cost is five to eight times the workflow's
and varies month to month; a regression is a drop in task-completion rate on a golden set of 300
invoices, and finding the cause means reading trajectories.

The hybrid the team ships: the workflow for the 92% of invoices that validate cleanly, and the agent,
with a 15-step budget and read-only tools, only for the exception route, with its proposed resolution
gated for human approval. The added cost is roughly 8% of invoices × five to eight times the per-invoice
workflow cost, so about 40–65% above the pure workflow rather than 400–700%; the hard cases get handled,
and the two parts are tested separately: unit tests for the workflow, a trajectory eval for the
exception agent.

## Trade-offs
- Workflows give fixed cost and testable steps but handle only foreseen paths; the moment input variety
  exceeds what routing can classify, the balance flips toward an agentic step.
- Agents handle novelty but pay in variance: p95 cost and latency can be many times the median, which
  flips back toward workflows at high volume or tight latency budgets.
- A hybrid localizes autonomy to the unpredictable step at the cost of two testing strategies and an
  explicit interface between code and agent; worth it as soon as the agent-only cost tail or the
  workflow's exception rate becomes visible.
- Orchestrator-worker and evaluator-optimizer patterns capture much of an agent's adaptivity with
  bounded structure, but only when you can state the decomposition or the criteria up front.
- Starting with the agent gets a demo fastest and a production system slowest; starting with the
  workflow is the reverse, and the exception rate tells you when to add autonomy.

## Pitfalls
- Building an agent for a task whose flowchart fits on a napkin; the symptom is a bill several times
  what a three-call pipeline would cost and regressions nobody can localize.
- Building a workflow with a router that has 40 branches and growing; the symptom is a new branch
  every week, which means the path is not actually predictable and an agentic step is overdue.
- Evaluating an agent with step-level unit tests; the symptom is green tests and a falling
  task-completion rate, because the failures are in the trajectory, not in any step.
- Budgeting agent cost from the typical run (the median, or a mean from a handful of demo runs); the
  symptom is a monthly bill well above plan, dominated by the few percent of runs that ran to the step
  limit.
- Letting an agent step run without a budget, a restricted tool set or gates inside an otherwise fixed
  workflow; the symptom is the one agentic node producing all of the incidents.
- Never revisiting the boundary after launch; the symptom is an agent whose traces show the same three
  tool sequences in nine runs out of ten, paying agent prices for a path that could be code.
- Treating "agent" as a product requirement rather than a design choice; the symptom is a system that
  is autonomous where it should be deterministic and vice versa.

## When to use
Default to a workflow, and add autonomy only at the steps where you cannot enumerate the path:
investigation, open-ended research, repair after unexpected failures. Keep the agentic islands small,
budgeted, tool-restricted and gated, and measure them separately: task completion rate on a golden set,
steps and cost per run at the p95, and the fraction of runs that end at the budget. For the workflow
parts measure per-step accuracy and latency, plus the exception rate; for the agentic parts, review the
distribution of tool sequences in traces every few weeks to find paths that can be codified. In day-to-day work the question arrives as "should this
be an agent?", and the right first answer is "show me the flowchart"; in interviews, the expected depth
is naming the workflow patterns, explaining what the choice changes for cost, testing and failure
modes, and describing a hybrid.

## Role lens
- **Engineer:** Implements the workflow patterns as plain code with structured outputs, isolates
  agentic steps behind budgets and tool allowlists, and writes unit tests for steps and trajectory
  evals for agents.
- **Architect:** Draws the boundary between deterministic and autonomous parts, sets cost and latency
  caps at the p95 (with totals planned from the measured mean), and decides which exception paths
  justify an agent at all.
- **FDE:** Translates a customer's process into a flowchart first, identifies the genuinely
  unpredictable steps, and sets expectations on cost variance and evaluation before promising autonomy.

## Check yourself
**Q:** A product manager asks for "an agent that handles all customer support tickets end to end". How
do you turn that request into an architecture, and what would you measure to justify each part?
**A:** Start with the flowchart. Intake, language detection, category classification, retrieval of the
relevant policy and account data, drafting, and sending are predictable and become a workflow: routing
plus prompt chaining with structured outputs, unit-tested per step, with fixed cost and latency. The
unpredictable part is investigation when the account state is anomalous or the ticket does not fit a
category; that becomes an agentic step with read-only tools, a step budget, and any consequential
action (refund, account change, reply committing to something) behind an approval gate. Measure the
workflow with per-step accuracy and latency and the agent with task-completion rate on a golden ticket
set, p95 steps and cost, and budget-exhaustion rate. Justify the agent by the exception rate the
workflow cannot handle, and widen autonomy only as those metrics and human agreement data allow.

## Quiz
### Q: What is the single question that most determines whether to build a workflow or an agent?
- [ ] Whether the model supports tool calling
- [x] Whether the sequence of steps can be defined in advance or depends on observations that only exist after earlier steps run
- [ ] Whether the task involves more than one model call
- [ ] Whether a framework is available
> Workflows are code-defined paths; agents let the model choose the path from feedback. If you can draw
> the flowchart, the predictable, cheaper, testable workflow wins; unforeseeable paths are the only
> reason to pay for autonomy.

### Q: Why is the cost of a typical (median) agent run a poor basis for an agent's budget?
- [ ] Because token prices change over time
- [x] Because agent run length has a heavy tail, so the mean sits well above the median and a small fraction of runs that hit the step budget can dominate total cost
- [ ] Because the median ignores the cost of the system prompt
- [ ] Because workflows always cost more than agents
> Model-chosen paths vary in length; most runs are short and a few are very long. Plan totals from a
> mean measured on enough runs to include the tail, and cap individual runs at the p95 or p99. Fixed-path
> workflows have little variance, which is one of their main advantages.

### Q: Which is the best description of a well-designed hybrid?
- [ ] An agent that occasionally calls a classifier
- [ ] A workflow in which every step is an agent
- [x] A code-defined workflow for the predictable path, with a budgeted, tool-restricted, gated agentic step only where the next action cannot be foreseen
- [ ] Two agents that check each other's work
> Autonomy is spent where the environment is unpredictable and nowhere else. Surrounding a small
> agentic island with deterministic code keeps cost bounded and lets each part be tested appropriately.
