---
id: agent-runtime-and-orchestration-layer
module: agent-runtime-architecture
name: Agent runtime & orchestration layer
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - agents/agent-loop
  - agents/state-machines
  - agent-reliability/durable-execution-and-checkpointing
  - agent-reliability/structured-event-streams
  - agent-reliability/idempotent-tool-execution
  - system-design/agent-orchestration
  - llmops-platform/ai-control-plane-vs-data-plane
  - agent-runtime-architecture/policy-enforcement-across-agents-and-tools
sources:
  - Anthropic, 2024 — Building Effective Agents
  - Official docs: Temporal
  - Official docs: LangGraph
---

## TL;DR
An agent is a loop: call the model, execute what it asked for, feed the result back. The runtime owns that
loop as a long-lived, crash-safe, policy-checked unit of work instead of a function in a request handler;
the orchestration layer above it coordinates many runs (sub-agents, handoffs, queues, budgets). Both exist
because loop state must outlive any process, tool calls have side effects, and every cross-cutting rule
(approval, tenancy, secrets) needs one enforcement point.

## Why it exists
The first agent anyone builds is a `while` loop in a request handler: call the model, if it returned a tool
call run it, append the result, repeat until it answers. It works in a demo and fails in production for
reasons that have nothing to do with the model.

The loop is long-lived. A real task takes dozens of steps and minutes to hours, far beyond an HTTP timeout
or a pod's expected lifetime. When the process restarts, the in-memory message list is gone and the task
starts over, re-executing side effects it already performed. The loop also has to *pause*: for a human
approval, for a slow external job, for a rate limit. A function on the call stack cannot pause for a day.

Then the cross-cutting concerns arrive. Every tool call should be checked against policy, attributed to a
tenant and user, executed with the right credentials, logged for audit, and counted against a budget. If the
loop is bespoke code in each service, each of those rules is reimplemented, slightly differently, in every
agent, and a sub-agent spawned by another agent inherits none of them. Finally, you need to *see* the loop:
stream its steps to a UI, trace it, replay it when something went wrong.

The naive alternative, "add retries and a bigger timeout", fixes none of this. What is missing is a
separation of concerns: the model decides, but a distinct runtime owns the lifecycle, the state, the
dispatch and the rules. That runtime is the same idea as a workflow engine or an operating-system scheduler,
specialised for a loop whose next step is chosen by a model.

## How it works
### The run as a persisted state machine
The runtime's unit of work is a **run**: an identity (run id, tenant, user, parent run), a status, and an
append-only log of **steps** (model decisions, tool calls, tool results, approvals, errors). The prompt for
the next model call is a *projection* of that log, not a mutable list held in memory. This is an
event-sourcing design, chosen because it gives you three things for free: resume after a crash (replay the
log, rebuild the prompt), audit (the log is the record), and compaction (summarise old steps into one
synthetic step without losing the originals).

Run status is an explicit state machine: `created → running → waiting_for_tool | waiting_for_approval |
waiting_for_event → running → completed | failed | cancelled`. Making the waiting states explicit is what
lets the loop pause for a day: the run is parked in storage, nothing is on a call stack, and an external
event (approval granted, webhook arrived) re-schedules it.

### Write-ahead ordering inside a step
A step cannot be one database transaction, because the tool's side effect (an email sent, a ticket opened)
happens in another system that cannot join your transaction. The runtime instead brackets the side effect
with two durable writes, the way a database write-ahead log does: it records the model's decision (the tool
call it wants) *before* executing the tool, and records the tool result *before* calling the model again.
That ordering makes every crash point distinguishable on recovery:

| Log ends with | Meaning | Recovery action |
|---|---|---|
| tool result | step complete | call the model for the next decision |
| decision, no result | tool never ran, or ran with unknown outcome | re-issue with the same idempotency key, or ask the tool for the outcome |
| nothing new | model call lost | call the model again (costs tokens, no side effect) |

The middle row is why the runtime pairs its log with idempotent tool execution: the re-issued call carries
the same key (`run_id:call_id`) and the tool deduplicates on it. One without the other still duplicates
emails and tickets. The same reasoning applies to the event stream: publishing an event and writing the
log are two systems, so a runtime that must never lose events writes them to an outbox table in the same
transaction as the step and publishes from there.

### Ownership: leases and fencing
Re-scheduling a run after a crash creates a new hazard: the "crashed" worker may only have been slow (a long
GC pause, a network partition) and still be executing. Two workers advancing one run will fork its log and
double its side effects. Runtimes therefore hand out a run to one worker at a time under a **lease** with an
expiry, renewed by heartbeat, and every append carries the lease's monotonically increasing token; the store
rejects writes from a stale token. The lease length is a trade: short leases recover dead workers quickly but
falsely expire healthy ones during long tool calls, so long tools should heartbeat or run asynchronously
with the run parked in `waiting_for_tool`.

### Layers of the runtime
A production runtime is a stack of narrow components, each replaceable:

1. **Model gateway** — provider abstraction, retries, routing, token accounting. The loop never calls a
   vendor SDK directly, so a provider change or fallback does not touch agent logic.
2. **Executor (the loop)** — projects the log to a prompt, calls the gateway, parses the decision,
   advances the state machine. It is stateless between steps; all state is in the store.
3. **Policy and authorization gate** — every tool call passes through it before dispatch, because the
   model's output is untrusted input and the gate is the only place the organisation's rules are enforced.
4. **Tool dispatch** — hands calls to tool workers, usually out of process (queue or RPC), with the run
   context (tenant, user, budget, delegation chain) attached. Out-of-process dispatch gives isolation,
   independent scaling and freedom to write tools in another language.
5. **State store** — run records, step log, checkpoints, approval requests.
6. **Event stream** — each appended step is published so UIs, traces and evaluators observe the run live
   rather than polling the store.

The control plane (run lifecycle, scheduling, policy, budgets) is separated from the data plane (model
inference, tool execution) because they scale, fail and are secured differently: the control plane is
small and must be highly consistent; the data plane is where cost and latency live.

### The orchestration layer
Above a single run sits orchestration: deciding *which* runs exist and how they relate. Patterns are the
supervisor that spawns sub-agent runs and waits for them, the handoff that transfers a run from one agent
definition to another, fan-out over a list with a join, and the queue that admits runs under concurrency
and cost limits. A sub-agent is simply another run with a `parent_run_id`, and its context is *attenuated*
from the parent: scopes are intersected, budgets subtracted, deadlines inherited. This is a deliberate
design choice, because a child that starts fresh can do what its parent was forbidden to do.

Durable workflow engines (Temporal and similar) and graph frameworks (LangGraph and similar) provide many
of these pieces, notably persisted state and resumable execution; hosted "agent" APIs from model vendors
provide the loop and tool dispatch but typically leave policy, tenancy and credential handling to you.
Either way, the architecture above is what you are assembling.

## Example
A minimal runtime: the run is an append-only log, the prompt is projected from it, every tool call passes a
policy gate before dispatch, approvals park the run instead of blocking a thread, and the same `tick()`
resumes after a crash or an approval because it first looks for a decided-but-unexecuted call in the log.

```python
from dataclasses import dataclass, field

@dataclass
class Run:
    id: str; tenant: str; user: str
    status: str = "running"
    steps: list = field(default_factory=list)          # append-only; the prompt is derived from it

def project(steps):                                    # log -> messages for the next model call
    return [s["message"] for s in steps if "message" in s]

def pending_call(steps):                               # decision recorded, result not: resume point
    last = steps[-1] if steps else None
    return last.get("call") if last and last["type"] == "model" else None

class Runtime:
    def __init__(self, gateway, tools, policy, store, events):
        self.gateway, self.tools, self.policy, self.store, self.events = gateway, tools, policy, store, events

    def append(self, run, step):
        run.steps.append(step)
        self.store.append(run.id, step)                # durable before anything else happens
        self.events.publish(run.id, step)              # UIs, traces and evals subscribe here

    def tick(self, run: Run, max_steps=40):
        while run.status == "running" and len(run.steps) < max_steps:
            call = pending_call(run.steps)             # after a crash or an approval, don't re-ask the model
            if call is None:
                d = self.gateway.complete(project(run.steps), tenant=run.tenant)
                self.append(run, {"type": "model", "message": d.message, "call": d.tool_call})
                if d.tool_call is None:
                    run.status = "completed"; break
                call = d.tool_call
            verdict = self.policy.check(run, call)     # allow | deny | needs_approval (reads approval records)
            if verdict.effect == "needs_approval":
                run.status = "waiting_for_approval"    # parked; the approval event re-schedules tick()
                self.store.request_approval(run.id, call); break
            out = ({"error": f"denied: {verdict.reason}"} if verdict.effect == "deny" else
                   self.tools.dispatch(call, ctx=run, idempotency_key=f"{run.id}:{call.id}"))
            self.append(run, {"type": "tool_result", "call_id": call.id,
                              "message": {"role": "tool", "tool_call_id": call.id, "content": out}})
        if run.status == "running":
            run.status = "failed"                      # budget exhausted is a terminal state, not a hang
        self.store.save(run)
```

Trace a run that asks to issue a refund: step 1 records the decision `refund(order=812, amount=40)`; the
policy returns `needs_approval`, so the run is saved as `waiting_for_approval` and the worker is free. A
day later the approval is recorded and the run is re-queued. `tick()` finds the pending call in the log,
the policy now sees the approval and returns `allow`, and the refund is dispatched with key `run_7:call_1`.
If the worker dies mid-dispatch, the next worker repeats exactly that path, and the payment service sees the
same key and refunds once.

## Trade-offs
- A separate runtime buys durability, observability and one enforcement point, but adds a persistence
  round-trip per step and real operational surface; for a single-shot, side-effect-free call that finishes
  in seconds, an inline loop is the right call.
- Out-of-process tool dispatch gives isolation, independent scaling and polyglot tools, at the cost of
  serialisation, queue latency and more failure modes; keep cheap pure functions in-process.
- An append-only step log makes replay and audit trivial but grows without bound, so you must own
  compaction and retention; a mutable state blob is simpler until the first "what happened?" incident.
- Adopting a durable workflow engine saves you from writing checkpointing and timers, but its programming
  model (deterministic replay, versioned workflow code) constrains how agent code can change; the constraint
  bites when runs live for days while agent code ships daily, and matters little when runs are short enough
  to drain before a deploy.
- Hosted vendor agent runtimes remove the loop entirely, but you inherit their state model, pay their per-call and
  per-tool pricing, and still have to build policy, tenancy and credential handling around them.

## Pitfalls
- Executing the tool before persisting the model's decision; the symptom is duplicated side effects
  (two tickets, two emails) after every deploy or OOM kill.
- Building the prompt from an in-memory list rather than the persisted log; the symptom is runs that
  silently restart from scratch after a pod restart.
- Letting the orchestrator or a sub-agent call tools through a path that skips the policy gate; the
  symptom is audit logs showing executed actions with no corresponding policy decision.
- Enforcing step and cost budgets per model call but not per run tree; the symptom is a supervisor that
  spawns ten children, each within budget, and spends ten times the limit.
- Re-scheduling a run without a lease or fencing token; the symptom is a run log with two interleaved
  branches and duplicated tool calls after a worker stalled rather than died.
- Blocking a worker thread while waiting for a human approval; the symptom is a thread pool exhausted
  by runs that are "running" but doing nothing.
- Coupling the executor to one vendor's tool-call wire format; the symptom is a provider fallback that
  requires rewriting the loop under incident pressure.

## When to use
Reach for a real runtime as soon as an agent does any of the following: runs longer than a request
timeout, performs side effects, needs human approval mid-task, serves more than one tenant, or spawns other
agents. If none of those apply, a loop in a handler with a step cap is fine, and you should say so. When
designing one, measure steps per run, wall time per step split into model versus tool time, resume success
rate after a forced kill, and the fraction of tool calls that pass through the policy gate (it should be
100%). In interviews, the expected depth is to draw the layers, explain why persistence happens before
execution, show how an approval pauses a run without holding a thread, and describe how a sub-agent's
authority and budget derive from its parent.

## Role lens
- **Engineer:** Implements the executor against a gateway and tool dispatcher, writes the step log and
  projection, and proves resume works by killing the process mid-run in a test.
- **Architect:** Owns the control-plane/data-plane split, the choice between a workflow engine, a
  framework and a bespoke runtime, and the rule that all tool calls pass one gate.
- **FDE:** Maps the runtime's layers onto a customer's existing queue, identity and audit systems, and
  explains why their "just call the API in a cron job" design loses work on restart.

## Check yourself
**Q:** A team's agent sends a customer email as a tool call. After a deploy, several customers received the
same email twice. Walk through where in the runtime the bug most likely is and how a correct design prevents
it.
**A:** The loop almost certainly executed the email tool and then crashed (or was terminated by the deploy)
before the tool result was persisted, so on restart the run's log ended with a model decision that had no
result and the executor re-ran it. A correct design does two things. First, it persists the model's decision
before dispatch and the result immediately after, so recovery can distinguish "never attempted" from
"attempted, outcome unknown". Second, every dispatch carries an idempotency key derived from `run_id` and
`call_id`, and the email tool (or its adapter) deduplicates on it, so even a legitimate re-issue after an
unknown outcome sends once. A strong answer adds that side-effecting tools should be classified so recovery
asks the tool for the outcome rather than re-executing, and that graceful shutdown should drain in-flight
steps before the process exits.

## Quiz
### Q: Why does a production agent runtime build the model prompt from a persisted step log instead of an in-memory message list?
- [ ] Persisted logs reduce the number of tokens the model needs to read
- [x] So a run can be resumed, audited and compacted from durable state after a crash or pause
- [ ] Because model providers require a stored transcript for tool calling
- [ ] To avoid holding message objects that would exceed the process memory limit
> Deriving the prompt from the log means nothing of value lives only on a call stack: a run can be
> re-scheduled on any worker after a restart or a day-long approval wait, and the same log doubles as the
> audit trail. Token count and provider requirements are unaffected.

### Q: A sub-agent spawned by a supervisor is given a fresh context with the agent's default tool scopes. What is the architectural flaw?
- [ ] The child will not share the parent's KV cache, raising latency
- [ ] Sub-agents must always run in the same process as their parent
- [x] The child's authority and budget are not attenuated from the parent's, so it can do what the parent could not
- [ ] Fresh contexts prevent the supervisor from reading the child's final answer
> Delegation must be monotonic: a child's scopes should be the intersection with the parent's, and its budget
> a share of the parent's remainder. Starting fresh turns multi-agent orchestration into a privilege and
> cost escalation path.

### Q: Which design lets a run wait a day for a human approval without wasting resources?
- [ ] Sleep the worker thread with a long timeout and poll the approval table
- [ ] Keep the run in memory and retry the tool call every minute until approved
- [x] Persist the run in a waiting state with no thread attached, and re-schedule it when the approval event arrives
- [ ] Ask the model to re-plan without the tool so no approval is needed
> Making "waiting_for_approval" an explicit, persisted state means the loop holds no process resources while
> parked; the approval becomes an event that re-queues the run. Sleeping threads exhaust pools, and silently
> re-planning around the approval defeats the control.
