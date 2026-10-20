---
id: agentic-architecture
module: system-design
name: Agentic architecture
kind: pattern
difficulty: core
est_minutes: 16
status: reviewed
related:
  - agents/agent-loop
  - agents/workflows-vs-agents
  - agents/tool-calling
  - agents/approval-gates-for-consequential-actions
  - agent-reliability/durable-execution-and-checkpointing
  - agent-reliability/max-step-budgets
  - agent-runtime-architecture/agent-runtime-and-orchestration-layer
  - system-design/synchronous-vs-asynchronous-execution
sources:
  - Yao et al., 2022 — ReAct: Synergizing Reasoning and Acting in Language Models
  - Anthropic, 2024 — Building Effective Agents
---

## TL;DR
An agentic architecture is a system in which a model chooses the next action at runtime, observes the
result, and repeats until a stopping condition. The system-design job is to wrap that loop in boundaries
the model does not control: a typed tool layer with least privilege, externalized state so runs survive
crashes, step and cost budgets, approval gates for consequential actions, and tracing. The model owns
judgment; the architecture owns guarantees.

## Why it exists
Most software encodes its control flow in advance: a request comes in, a known sequence of steps runs,
a response goes out. That works when the path through the task is known when you write the code. A
large class of valuable tasks is not like that. "Resolve this support ticket", "find out why this deploy
failed", "reconcile these two ledgers" take a different number of steps each time, the next step depends
on what the previous one revealed, and the space of possible paths is too large to enumerate as
branches.

The first naive answer is a fixed chain of prompts: classify, then extract, then look up, then draft. It
is predictable and testable, and for many tasks it is the right design (see workflows vs agents). It
fails when the chain needs a branch you did not anticipate: the lookup returns two candidates, the
document is in a language the extractor was not prompted for, the ticket is actually three tickets.
Every such case becomes another hard-coded branch and the chain ossifies.

The second naive answer is the opposite: hand the model every tool and a long system prompt, and let it
run. This produces demos quickly and incidents shortly after. Nothing bounds how many steps it takes or
how much it costs. A crash mid-task loses everything, because the state lived in a Python list. A tool
with write access to production is one bad tool call away from damage. And when it goes wrong, nobody
can reconstruct what happened.

Agentic architecture is the discipline between those two: let the model decide *what* to do next, and
build a system that decides what it is *allowed* to do, how long it may keep going, what happens when
it fails, and how every step is recorded.

## How it works
### The core loop
The unit of execution is a loop: build the context (instructions, task, state, recent observations),
call the model with a set of tool definitions, and then branch. If the model returns a tool call,
validate the arguments, execute the tool, append the result as an observation and repeat. If it returns
a final answer, stop. ReAct-style interleaving of reasoning and acting is the pattern underneath; every
agent framework is a wrapper around this loop plus the boundaries below. A common variant,
plan-and-execute, asks the model for an explicit plan first and then runs steps against it, re-planning
when an observation contradicts the plan; it costs an extra call but gives the runtime something to
checkpoint, show a human and compare progress against, which a pure ReAct loop does not.

### The cost shape of the loop
Each step re-sends the accumulated context, so input tokens grow with the *sum* of context sizes, not the
final size. Illustratively, a run that starts at 3k tokens and adds 1k per step sends
Σ(3k + i·1k) for i = 0…11 ≈ 102k input tokens over 12 steps, though its final context is only 14k. That
near-quadratic growth is why the architecture caps steps, stores large tool outputs as references, and
keeps the stable prefix (instructions, tool definitions) byte-identical so provider prompt caching can
discount it.

### The layers around the loop
1. **Interface layer.** Where tasks enter (chat UI, API, event, schedule) and where results and
   intermediate events leave. For anything that may take more than a few seconds this is asynchronous:
   accept the task, return a run id, stream events, deliver the result later. Agents are long-running by
   nature *because* each step is a model call plus a tool call and the number of steps is unknown;
   holding an HTTP request open across that is fragile.
2. **Orchestration and runtime layer.** Owns the loop and its policies: step budget, token and cost
   budget, wall-clock timeout, loop detection (same tool with the same arguments repeatedly), retries
   with backoff for transient tool failures, cancellation. It persists state after every step *because*
   a run that cannot resume after a worker restart will, at scale, fail constantly for reasons unrelated
   to the task. Durable execution engines are one way to get this; a checkpoint store plus idempotent
   steps is another.
3. **Model gateway.** A single place that calls models: routing between models, rate limiting,
   caching, cost accounting, fallbacks. Agents multiply model calls by the number of steps, so cost
   control lives here, not in prompts.
4. **Tool layer.** Each tool is a typed contract: schema-validated inputs, structured outputs, an
   explicit side-effect classification (read-only versus consequential), and its own credentials scoped
   to what that tool needs. The agent never holds credentials; the tool adapter does. Consequential tools
   route through an approval gate, human or policy, *because* the model's confidence is not a
   permission. Tools that execute code or browse run in a sandbox *because* their inputs may come from
   untrusted content, which makes prompt injection a system-level threat rather than a prompt-level one.
5. **State and memory.** Task state (plan, completed steps, artifacts) is separate from conversation
   history and from long-term memory. Keep it external and addressable by run id. Large tool outputs are
   stored as artifacts and referenced, not pasted into the context, *because* context is the agent's
   scarcest resource and raw tool results are the fastest way to exhaust it.
6. **Observability and audit.** Every step emits a structured event: model input hash, tool name,
   arguments, result size, latency, cost, under a run id and tenant id. This is what makes trajectory
   evaluation and incident review possible.

### Where control flow should be code, and where model
A useful rule: put the model in decisions that require judgment about content, and put code in
decisions that require guarantees. "Which document is relevant" is judgment. "May this user issue
refunds above 500" is a guarantee, enforced in the tool layer regardless of what the model asks. Mixed
systems, where a deterministic workflow calls an agent for one open-ended step, or an agent calls a
deterministic sub-workflow as a tool, are the norm in production, not the exception.

### Stopping
Every agent needs explicit termination: a final-answer action, a maximum number of steps, a budget, and
a stuck detector. Without all four, the loop's failure mode is not a wrong answer but an unbounded one.
Each terminal state should be distinct in the run record (`done`, `budget_exhausted`, `stuck`,
`awaiting_approval`, `cancelled`) *because* they demand different follow-ups: a retry, a human, or a
bug report.

## Example
A minimal runtime that shows the boundaries rather than the cleverness: a budget, schema-validated
tools with a side-effect flag, an approval gate for consequential actions that interrupts rather than
blocks, and a checkpoint after every step. `call_model` is whatever model API you use and returns either
a tool call or a final answer; `validate` is a JSON Schema check.

```python
import json

TOOLS = {   # name -> (input schema, needs_approval(args), implementation(args, idem_key))
    "search_tickets": ({"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                       lambda a: False, lambda a, key: tickets.search(a["q"])),
    "issue_refund":   ({"type": "object", "properties": {"ticket": {"type": "string"},
                        "amount": {"type": "number", "exclusiveMinimum": 0, "maximum": 5000}},
                        "required": ["ticket", "amount"]},
                       lambda a: a["amount"] > 500,          # policy threshold, enforced in code
                       lambda a, key: payments.refund(a["ticket"], a["amount"], idempotency_key=key)),
}

def execute(run_id, state, name, args):
    if name not in TOOLS:
        return {"error": f"unknown tool {name}"}               # an observation, not a crash
    schema, needs_approval, fn = TOOLS[name]
    validate(args, schema)                                     # rejects amount > 5000 outright
    if needs_approval(args) and not approvals.granted(run_id, name, args):
        return "PAUSE"
    try:
        return fn(args, f"{run_id}:{state['step']}")         # same key on retry -> no double refund
    except TransientError as e:
        return {"error": str(e), "retryable": True}           # the model decides: retry or give up

def run(run_id, task, store, max_steps=12, budget_usd=0.50):
    state = store.load(run_id) or {"messages": [{"role": "user", "content": task}], "step": 0, "cost": 0.0}
    while state["step"] < max_steps and state["cost"] < budget_usd:
        if state.get("pending"):                              # resuming after a pause: no new model call
            msg, name, args = state["pending"]
        else:
            reply = call_model(state["messages"], tools=[{"name": n, "schema": t[0]} for n, t in TOOLS.items()])
            state["cost"] += reply.cost_usd
            if reply.final is not None:
                store.save(run_id, {**state, "status": "done", "answer": reply.final}); return reply.final
            msg, name, args = reply.as_message(), reply.tool_name, reply.tool_args
        result = execute(run_id, state, name, args)
        if result == "PAUSE":
            store.save(run_id, {**state, "status": "awaiting_approval", "pending": [msg, name, args]})
            return None                                        # interrupt; an approval event re-invokes run()
        state.pop("pending", None)
        state["messages"] += [msg, {"role": "tool", "name": name, "content": json.dumps(result)[:4000]}]
        state["step"] += 1
        store.save(run_id, state)                              # checkpoint: a crash here loses nothing
    store.save(run_id, {**state, "status": "budget_exhausted"})
```

Three boundaries are visible. The hard cap (`maximum: 5000`) lives in the schema, so a larger refund is
rejected however persuasively the model argued for it. The policy threshold (above 500 needs approval) is
a predicate in code, and the gate *interrupts* the run, saving the exact pending call, rather than holding
a thread while a human decides; on resume the approved call runs without asking the model again, so the
human approved precisely what executes. And the idempotency key is derived from run id and step, so a
worker that crashes after the refund but before the checkpoint re-executes the step without paying twice.
Walk one run: step 0 searches tickets (read-only, runs), step 1 proposes a 750 refund, the predicate
fires, the run is saved as `awaiting_approval` with zero further cost; an approver accepts, `run()` is
re-invoked, the refund executes with key `run_id:1`, and the model writes the final answer at step 2.

## Trade-offs
- Flexibility on unknown task paths versus predictability and testability; when the path is known in
  advance, a deterministic workflow is cheaper, faster and far easier to evaluate.
- Externalized, checkpointed state makes runs resumable and auditable but adds a store write per step;
  for sub-second, read-only agents an in-memory loop is acceptable.
- Approval gates stop damage from bad tool calls but add latency and human load; the threshold for what
  counts as consequential is a product decision, not a technical one.
- Giving an agent many tools broadens what it can do while lowering tool-selection accuracy and inflating
  context; narrow tool sets per task, or sub-agents, win once the tool list grows large (the exact
  threshold is model-dependent, so measure tool-selection accuracy rather than assume a number).
- Asynchronous execution with streamed events fits long runs and survives restarts, but complicates
  clients, which must handle partial results, resumption and cancellation.

## Pitfalls
- Keeping run state in process memory; the symptom is runs that vanish on deploys or autoscale events,
  and users who retry, doubling side effects.
- No budget or step cap; the symptom is a single stuck run consuming a day's token budget while repeating
  the same failing tool call.
- Trusting the model to enforce permissions ("only refund if the policy allows"); the symptom is a
  prompt-injected document that convinces the agent to call a write tool, which then succeeds.
- Letting the model re-propose an action after approval instead of executing the stored pending call;
  the symptom is an approved 750 refund that executes as 800, because the second model call differed.
- Pasting full tool outputs into context; the symptom is cost growing step over step and quality falling
  as the window fills with raw JSON.
- Treating a demo loop as the architecture; the symptom is that the first production requirement
  (resume, audit, cancel, approve) requires a rewrite.

## When to use
Build an agentic system when the number and order of steps genuinely depend on intermediate results,
when tools exist to act on those results, and when you can define "done" well enough to evaluate it.
Prefer a workflow when steps are known, when latency must be tight, or when the cost of a wrong action
is high and you cannot gate it. Measure task completion rate, steps and cost per task, tool-call success
rate, and the fraction of runs that hit budgets or require approval. Day to day this appears as decisions
about which tools are read-only, what the step budget is, and where state lives. In interviews, be ready
to draw the layers, explain why state must be external, and argue where model judgment ends and
code-enforced guarantees begin.

## Role lens
- **Engineer:** Implements the loop with budgets and checkpoints, writes tool adapters with schemas and
  scoped credentials, and instruments every step for tracing and cost accounting.
- **Architect:** Defines the runtime boundary, the tool permission model, the sync-versus-async contract
  with clients, and which decisions are code-enforced rather than model-made.
- **FDE:** Fits the agent into a customer's identity, approval and audit requirements, and negotiates
  which actions may run autonomously versus with a human in the loop.

## Check yourself
**Q:** A team's agent works well in demos, but in production about 5% of runs disappear without a
result and some customers report duplicate actions. What architectural gaps would you suspect, and how
would you fix them?
**A:** Both symptoms point at in-process state and non-idempotent side effects. Runs that disappear are
most likely killed by deploys, autoscaling or worker crashes while the loop's state lived in memory; with
no checkpoint there is nothing to resume, and no event stream shows where it stopped. Duplicates come
from the obvious reaction: the client or a retry wrapper re-submits the task, and consequential tools
execute again. The fix is to externalize state by run id and checkpoint after every step so a worker can
resume rather than restart, make tool execution idempotent with a key derived from run id and step,
route consequential tools through an approval or at least a dedupe layer, and emit structured events so
disappearing runs become visible as "interrupted at step n" instead of silence.

## Quiz
### Q: Why should agent run state be persisted outside the process after every step?
- [ ] Because model APIs require state to be stored before each call
- [x] So a worker crash, deploy or autoscale event interrupts a run instead of losing it, and the run can resume
- [ ] To reduce the number of tokens sent to the model
- [ ] Because tool results cannot be held in memory
> Agent runs span many model and tool calls over seconds to hours. Anything that lives only in process
> memory is lost on restart; a checkpoint per step turns that loss into a resumable interruption.

### Q: Where should the rule "refunds above 500 require approval" be enforced?
- [ ] In the system prompt, so the model knows the policy
- [ ] In the model's reasoning, by asking it to double-check amounts
- [x] In the tool layer, by schema validation and an approval gate that runs regardless of what the model requested
- [ ] In the client, by warning the user after the refund
> The model may be wrong or manipulated through its inputs. Guarantees belong in code the model cannot
> bypass; the prompt can mention the policy, but the tool layer must enforce it.

### Q: When is a deterministic workflow a better choice than an agent?
- [ ] Whenever more than one tool is involved
- [x] When the sequence of steps is known in advance and predictability matters more than handling unanticipated paths
- [ ] When the task involves reading documents
- [ ] Never, because agents can emulate any workflow
> Agents trade predictability and cost for flexibility on unknown paths. If the path is known, that
> flexibility buys nothing and you pay for it in latency, cost and harder evaluation.
