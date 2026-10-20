---
id: agent-orchestration
module: system-design
name: Agent orchestration
kind: pattern
difficulty: core
est_minutes: 15
related:
  - agents/workflows-vs-agents
  - agents/state-machines
  - agents/frameworks
  - agents/human-in-the-loop
  - agent-reliability/durable-execution-and-checkpointing
  - agent-reliability/structured-event-streams
  - agent-runtime-architecture/agent-runtime-and-orchestration-layer
  - system-design/workflow-orchestration
sources:
  - Anthropic, 2024 — Building Effective Agents
  - Official docs: LangGraph
---

## TL;DR
Agent orchestration is the control layer that decides what runs next in an LLM application: which model
call, tool, sub-agent or human step, with what state, and what happens on failure. It exists because
once a system has more than one model call plus tools, ordering, state, retries, concurrency and
interruption need an owner. The core design choice is how much of that control flow is deterministic
code and how much is left to the model.

## Why it exists
A single prompt needs no orchestration. The moment you add a second call that depends on the first, a
tool whose result feeds a third call, and a user who may need to approve something in the middle, you
have a program whose steps are spread across model outputs, and something must own it.

The naive implementation is a Python function that calls things in order, with results in local
variables. It works until the first production requirement. A tool times out at step four: do you redo
steps one to three, with their side effects? A user closes the tab while the agent waits for approval:
where is the run? Traffic triples: can you run steps concurrently, and which ones? Someone asks what the
agent did for a given ticket last Tuesday: which log line was that? Each answer bolts another ad-hoc
mechanism onto the function until it is an orchestrator written by accident, untested and undocumented.

A second naive answer is to let the model orchestrate everything: give it all the tools and let it
decide every transition. That is the right choice for genuinely open-ended steps and the wrong one for
steps that must happen (validation, logging, a compliance check), that must not happen twice
(payments), or that must happen in a fixed order. Model-chosen control flow is also the hardest to
evaluate, since every run may take a different path.

Orchestration as a deliberate layer makes the control flow explicit: state is a typed object, steps are
nodes, transitions are either fixed edges or router decisions (made by code or by the model), and a
runtime executes it with persistence, events and policies attached. The reason is the same one behind
every explicit state machine: you can inspect, resume, test and reason about what you have written
down, and you cannot do any of that with control flow buried in prompts.

## How it works
### State, nodes, edges
Represent a run as a state object (the task, messages, intermediate results, counters) and the
application as a graph. A node is a function from state to updated state: call the model, call a tool,
run a sub-agent, ask a human. Edges are either static (A always leads to B) or conditional, decided by a
router. Routers can be pure code (`if state.score < 0.7`), a classifier, or the main model's tool
choice. This representation is used *because* it makes the possible paths enumerable: you can draw it,
test each node in isolation, and know where loops can occur.

### The orchestration patterns, from most to least deterministic
1. **Prompt chaining.** A fixed sequence where each step checks the previous output. Use when the task
   decomposes cleanly and predictably.
2. **Routing.** Classify first, then dispatch to a specialized path (different prompt, model or tool
   set). Use when inputs fall into distinct categories that want different handling.
3. **Parallelization.** Run independent nodes concurrently and join (sectioning), or run the same node
   several times and vote (ensembling). Use when latency matters and branches are independent.
4. **Orchestrator–workers.** A model decides the sub-tasks at runtime; code runs the fan-out and join.
   Use when sub-tasks cannot be known in advance.
5. **Evaluator–optimizer.** Generate, evaluate against explicit criteria, revise, bounded by a budget.
   Use when quality is checkable.
6. **Autonomous agent loop.** The model picks the next node every step until it decides it is done. Use
   when the path is unknown and the environment gives trustworthy feedback.

Each step down this list trades predictability and cost for flexibility, which is why the right system
usually mixes them: a routed, chained outer workflow with an autonomous loop for the one step that needs
it.

### Runtime responsibilities
The orchestrator executes the graph and owns the cross-cutting concerns, *because* putting them in nodes
means every node reimplements them differently:
- **Persistence.** Checkpoint state after each node so a run can resume at the node where it stopped.
  This is also what makes human-in-the-loop possible: an interrupt is a checkpoint plus a status, and
  resumption is loading it and continuing.
- **Policies.** Step and cost budgets, per-node timeouts, retry with backoff for transient failures, and
  idempotency keys for nodes with side effects so a retry does not repeat them.
- **Concurrency.** Fan-out and join primitives with limits, so parallel branches do not exhaust rate
  limits.
- **Events.** A structured stream per run (node started, model called, tool result, waiting for
  approval, done) that clients subscribe to, *because* long runs need progress and operators need traces.
- **Cancellation.** A cooperative stop signal checked between nodes, with in-flight tool calls either
  finished or compensated.

### Request-driven versus event-driven orchestration
For short runs an orchestrator can execute inside a request. For runs that wait (approvals, slow tools,
scheduled follow-ups) orchestration must be event-driven: a run advances when an event arrives, workers
are stateless, and the durable state is in the store. This is where agent orchestration meets workflow
engines such as Temporal; the graph is the same, the executor is a durable one.

### Frameworks
Graph libraries such as LangGraph give you the state graph, checkpointing and interrupts; vendor agent
SDKs give you the autonomous loop with hand-offs; durable execution engines give you the event-driven
executor. The abstraction matters less than making state explicit and keeping runtime concerns out of
the nodes.

## Example
A small graph executor that makes the ideas concrete: typed state, nodes as functions, a static edge, a
code router, a model-driven node, a checkpoint after every node, and an interrupt for human approval
that is resumed by calling `run` again with the same id. `call_model`, `mail` and `store` are stand-ins.

```python
from dataclasses import dataclass, field, asdict

@dataclass
class State:
    ticket: str
    draft: str = ""
    score: float = 0.0
    approved: bool | None = None
    step: int = 0
    status: str = "running"
    history: list = field(default_factory=list)

def draft_reply(s): s.draft = call_model(f"Draft a reply to: {s.ticket}").text; return s
def grade(s):       s.score = float(call_model(f"Score 0-1 for policy compliance:\n{s.draft}").text); return s
def approval(s):    # interrupt node: nothing to compute until a human acts
    if s.approved is None: s.status = "waiting_approval"
    elif not s.approved:   s.status = "rejected"
    return s
def send(s):        mail.send_idempotent(key=f"{s.ticket}:send", body=s.draft); s.status = "done"; return s

NODES = {"draft_reply": draft_reply, "grade": grade, "approval": approval, "send": send}
EDGES = {"draft_reply": "grade",                                                          # static edge
         "grade": lambda s: "draft_reply" if s.score < 0.7 and s.step < 6 else "approval",  # code router
         "approval": lambda s: "send" if s.approved else None,
         "send": None}

def run(run_id, store, max_steps=10):
    s = store.load(run_id) or State(ticket=store.task(run_id))
    node = store.cursor(run_id) or "draft_reply"
    while node and s.step < max_steps:
        s = NODES[node](s); s.step += 1; s.history.append(node)
        if s.status != "running":                                   # interrupted, rejected or done
            store.save(run_id, asdict(s), cursor=node); break
        edge = EDGES[node]; node = edge(s) if callable(edge) else edge
        store.save(run_id, asdict(s), cursor=node)                  # checkpoint after every node
        emit(run_id, node=s.history[-1], score=s.score)
    return s
# A human sets approved=True and status="running" in the store; run(run_id, store) then resumes at "approval".
```

## Trade-offs
- Explicit graphs make paths enumerable and testable, but every new behaviour is a code change; a
  model-driven loop adapts without redeploys at the price of unpredictable paths.
- Checkpointing after each node enables resume and human interrupts, but adds a store round-trip per
  node; for sub-second, side-effect-free chains, in-memory execution is fine.
- Parallel branches cut latency but multiply concurrent model calls, so rate limits and cost spikes
  arrive sooner; cap fan-out and reserve it for genuinely independent work.
- Putting policies (budgets, retries, idempotency) in the runtime keeps nodes simple, but makes the
  runtime a critical dependency that must itself be reliable and well understood.
- A framework accelerates the first version while shaping your state and persistence model; if you later
  need a durable, event-driven executor, migrating a framework-specific graph can be a rewrite.

## Pitfalls
- Hiding control flow in prompts ("after you search, always validate, then..."); the symptom is runs
  that skip required steps and no way to assert they happened.
- Retrying a node with side effects without an idempotency key; the symptom is duplicate emails, tickets
  or payments after a transient failure.
- Unbounded loops in evaluator–optimizer or self-correction edges; the symptom is a run that revises
  forever at increasing cost with no termination.
- Treating human approval as a blocking call in a worker; the symptom is held connections, worker
  exhaustion and runs lost when the process restarts.
- No per-run event stream; the symptom is users seeing a spinner for minutes and operators unable to say
  which node a stuck run is in.

## When to use
Introduce an explicit orchestration layer as soon as a feature has more than one model call with
dependencies, any tool with side effects, or any step that waits on a human or an external system.
Choose the most deterministic pattern that handles the task, and confine model-driven control flow to the
steps that need judgment. Measure per-node latency and failure rates, run completion versus interruption
versus budget exhaustion, and retries per run. Day to day this is where you decide what is a node, where
checkpoints go, and what is idempotent. In interviews, the expected depth is to describe the pattern
ladder from chaining to autonomous loop, say which runtime concerns the orchestrator owns, and explain how
an approval interrupt is implemented without blocking a worker.

## Role lens
- **Engineer:** Defines nodes, routers and state types, adds checkpoints and idempotency keys, and emits
  the event stream that clients and traces consume.
- **Architect:** Chooses where control flow is code versus model, whether execution is request- or
  event-driven, and which engine or framework owns persistence and policies.
- **FDE:** Adapts the graph to a customer's approval chains and systems, and explains why a human step
  pauses the run rather than holding a connection open.

## Check yourself
**Q:** A team built an agent that handles refund requests end to end. Compliance now requires that every
refund above a threshold is approved by a person, sometimes hours later. How would you change the
orchestration, and what would break if you simply added a blocking approval call?
**A:** A blocking call holds a worker and a connection for hours, exhausts the worker pool under load,
and loses the run entirely if the process restarts, so it is not an option. I would make the run's state
explicit and persisted, add an approval node that checkpoints the state with status "waiting_approval"
and returns, and make the run event-driven: the approval decision arrives as an event that loads the
checkpoint and resumes at the next node. The refund-executing node gets an idempotency key so a resumed
or retried run cannot pay twice, the threshold check is code in the router rather than a prompt
instruction, and the event stream shows the run as waiting so support can see why it is pending. If runs
can wait days, a durable workflow engine is the natural executor.

## Quiz
### Q: What is the main reason to represent an agent application as explicit state plus nodes and edges?
- [ ] Models produce better answers when the prompt describes a graph
- [x] It makes the possible paths enumerable, so you can test nodes, resume runs and know where loops can occur
- [ ] Graphs execute faster than sequential function calls
- [ ] It removes the need for a model to make any decisions
> Control flow buried in prompts or local variables cannot be inspected, tested or resumed. An explicit
> graph can be drawn, checkpointed and reasoned about, while still leaving judgment calls to the model
> at router nodes.

### Q: How should a human approval step be implemented in an orchestrated run?
- [ ] Block the worker thread until the human responds
- [ ] Ask the model to wait and poll for the decision
- [x] Checkpoint the state with a waiting status, return, and resume from the checkpoint when the approval event arrives
- [ ] Restart the run from the beginning once approved
> Approvals can take hours. Holding a worker wastes capacity and loses the run on restart; a checkpoint
> plus event-driven resumption costs nothing while waiting and survives deploys.

### Q: Which pattern fits when sub-tasks cannot be known until the task is inspected, but you still want predictable fan-out and join behaviour?
- [ ] Prompt chaining
- [x] Orchestrator–workers, with a model deciding the sub-tasks and code running the fan-out and join
- [ ] Routing to a fixed set of specialists
- [ ] A single autonomous agent loop
> Orchestrator–workers places model judgment exactly where it is needed, deciding the decomposition,
> while the concurrency, limits and aggregation stay deterministic in code.
