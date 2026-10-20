---
id: state-machines
module: agents
name: State machines
kind: pattern
difficulty: core
est_minutes: 13
related:
  - agents/agent-loop
  - agents/workflows-vs-agents
  - agents/frameworks
  - agents/approval-gates-for-consequential-actions
  - agent-reliability/durable-execution-and-checkpointing
  - agent-reliability/state-and-checkpoint-stores
  - agent-reliability/stopping-conditions-termination
  - system-design/workflow-orchestration
sources:
  - Official docs: LangGraph
  - Anthropic, 2024 — Building effective agents
---

## TL;DR
A bare agent loop has one state, "running", and all control lives in the model's head. A state machine
makes phases explicit: named states, the tools and transitions allowed in each, guards the harness
checks, and terminal states every path must reach. The model still chooses, but only among transitions
the machine permits, so an invariant like "never deploy before tests pass" is enforced in code, and the
pair (state, data) can be checkpointed and resumed after a crash.

## Why it exists
Watch a free-running agent do "investigate the bug, propose a fix, apply it, verify". Nothing in the
loop knows which of those four phases it is in. If the context grows long, or a tool result is
ambiguous, or an injected instruction says "skip the tests", the model can call `deploy` from anywhere,
because every tool is available at every step. The instruction "always run tests before deploying"
lives in the prompt, and prompt instructions are preferences the model weighs, not rules the system
enforces.

The same formlessness breaks recovery. When the process dies at step 37, the transcript tells you what
was said, not where the run is: has the migration already been applied? Is the fix half-written?
Restarting from the beginning repeats side effects; restarting from the last message assumes the model
can infer its own position from a log. It also breaks observability: you cannot plot "time spent in
verification" or "failure rate by phase" if phases do not exist.

Two naive alternatives bracket the problem. A hand-coded pipeline (extract, then validate, then route)
is fully enforceable but cannot branch on the model's judgement, so it only solves cases its author
foresaw. A free loop branches on anything but enforces nothing. A state machine is the middle: the
*set* of possible next steps is fixed by code, the *choice* among them is made by the model. This is
the same move protocol designers and UI frameworks made long ago, for the same reason: a finite set of
states and transitions is something you can check, test, draw and resume.

## How it works
### Anatomy
- **States** are a finite set, each with its own allowed tools and often its own prompt fragment.
  Because the tool list differs by state, the model in INVESTIGATE cannot call `deploy`: the tool is
  not in the request, so there is nothing to refuse.
- **Data** is a typed record (a Pydantic model or TypedDict) holding the facts guards need:
  `tests_passed`, `attempts`, `approved_by`, `plan`. It is separate from the transcript because the
  transcript is prose and guards need booleans.
- **Transitions** map (state, event) to the next state, optionally gated by a guard over the data.
  Events come from the model (a tool call or a structured decision), from tool results (success,
  failure), or from outside (a human approval, a timeout).
- **Terminal states** such as DONE, FAILED and NEEDS_HUMAN. Every path must reach one; that is the
  termination guarantee the free loop lacks.

### How the model drives it
The model's output has to become an event. The robust way is to expose the phase changes themselves as
tools (`submit_plan`, `mark_verified`, `finish`) with schemas, so the event is a structured call rather
than something parsed out of prose. The harness looks up (state, event) in the table. An illegal event
is rejected with a message ("finish is not allowed until tests pass") and the model is re-prompted;
rejections count toward a retry budget, because a model that keeps proposing illegal transitions is
stuck and the run should end in FAILED rather than loop.

### Guards hold the invariants
A guard is a predicate over the data that must be true for a transition to fire. "Cannot enter DONE
unless `tests_passed`", "cannot enter APPLY more than three times", "cannot enter DEPLOY without
`approved_by`". Guards live in the harness because that is the only place the model cannot talk its
way around. They are also where approval gates attach: a consequential transition's guard is "an
approval exists for these exact arguments".

### Persistence and resumption
After every transition the harness persists `(run_id, state, data, step)`. On restart it reloads and
re-enters the state, with the data it had. This is what makes a run resumable without re-reading the
transcript. It is not sufficient alone: a side effect may have completed between the tool call and the
checkpoint, so tools need idempotency keys so that re-entering APPLY does not apply twice. State machine
plus checkpoint plus idempotent tools is the recipe for durable execution.

### Loops and hierarchy
Retries are self-loops with a counter in the data and a bound that transitions to FAILED. Subtasks are
sub-machines: a VERIFY state can itself be a small machine (run tests, read failures, decide). This
turns "max step budget" from one global counter into per-phase structure, which is both tighter and
more explainable.

### Relationship to graph frameworks
LangGraph's nodes, conditional edges and shared typed state are exactly this: a state machine whose data
is the graph state, with checkpointers for persistence and interrupts for human input. Hand-rolling one
is about fifty lines; frameworks add persistence, streaming, visualization and time travel. Either way
the design work, choosing states and guards, is yours.

### Choosing the states
A state is a phase in which the allowed actions differ, or a point at which you would want to resume.
It is not "one state per prompt". Four to eight top-level states is typical; if you need thirty, you
are describing a workflow and should write it as code with no model choosing transitions.

## Example
A bug-fix agent as a small machine. The model only sees the tools of its current state, the transition
table decides what each tool call means, and two guards enforce the invariants: no finishing without
passing tests, and no more than three apply attempts.

```python
from enum import Enum
from pydantic import BaseModel

class S(str, Enum):
    INVESTIGATE = "investigate"; PROPOSE = "propose"; APPLY = "apply"
    VERIFY = "verify"; DONE = "done"; FAILED = "failed"

class Data(BaseModel):
    plan: str | None = None
    tests_passed: bool = False
    attempts: int = 0

TOOLS = {S.INVESTIGATE: ["read_file", "grep", "submit_plan"],
         S.PROPOSE: ["approve_plan", "revise_plan"],
         S.APPLY: ["edit_file", "run_tests"],
         S.VERIFY: ["read_file", "finish", "retry"]}
TABLE = {(S.INVESTIGATE, "submit_plan"): S.PROPOSE, (S.PROPOSE, "approve_plan"): S.APPLY,
         (S.PROPOSE, "revise_plan"): S.INVESTIGATE, (S.APPLY, "run_tests"): S.VERIFY,
         (S.VERIFY, "retry"): S.APPLY, (S.VERIFY, "finish"): S.DONE}

def transition(state: S, event: str, d: Data) -> S:
    if event not in TOOLS[state]:
        raise ValueError(f"{event} is not allowed in {state.value}")
    nxt = TABLE.get((state, event), state)             # tools that do not change phase self-loop
    if nxt is S.DONE and not d.tests_passed:            # guard: the invariant lives in code
        raise ValueError("cannot finish: tests have not passed")
    if (state, event) == (S.VERIFY, "retry"):
        d.attempts += 1
        if d.attempts >= 3:
            return S.FAILED                             # bounded retry ends in a terminal state
    return nxt

def run(model, execute, store, run_id):
    state, d = store.load(run_id) or (S.INVESTIGATE, Data())   # resume where we left off
    while state not in (S.DONE, S.FAILED):
        call = model.next_action(state, d, tools=TOOLS[state])   # only this state's tools are offered
        result = execute(call, idempotency_key=f"{run_id}:{store.step(run_id)}")
        if call.name == "run_tests": d.tests_passed = result.ok
        if call.name == "submit_plan": d.plan = call.args["plan"]
        state = transition(state, call.name, d)
        store.save(run_id, state, d)                             # checkpoint after every transition
    return state
```

## Trade-offs
- Enforceable invariants and resumability in exchange for up-front design: you must know the phases
  before you ship, which is cheap for well-understood tasks and wrong for genuinely open-ended ones.
- Restricting tools per state removes whole classes of error, but a state with too few tools starves
  the model; the symptom is repeated illegal-transition rejections, and the fix is a better state split,
  not a looser one.
- Per-transition checkpoints make crashes recoverable but add a write per step; for fast, cheap,
  side-effect-free runs the persistence cost can exceed the value.
- A typed data record makes guards precise but must be versioned: a resumed run from last week carries
  last week's schema.
- Frameworks save the plumbing (persistence, streaming, interrupts) at the cost of learning their
  abstractions and inheriting their churn; a hand-rolled machine is smaller and fully understood.

## Pitfalls
- Designing states around prompts ("the summarizing state") instead of around what is allowed and where
  you would resume; the symptom is a machine with twenty states and no useful guards.
- Keeping the state only in the transcript or in a Python variable; the symptom is a crash that cannot
  be resumed and a run that re-applies side effects on restart.
- Inferring events by string-matching the model's prose ("if 'done' in text"); the symptom is spurious
  transitions when the model says "not done yet".
- No terminal failure state or unbounded self-loops; the symptom is a run that cycles APPLY → VERIFY →
  APPLY for hours and only stops at the global token budget.
- Checking guards in the prompt ("only finish when tests pass") rather than in code; the symptom is a
  successful-looking run whose tests never ran.
- Changing the data schema without migrating persisted runs; the symptom is validation errors on
  resume, a week after the deploy.

## When to use
Reach for an explicit state machine as soon as an agent has distinct phases with different allowed
actions, any consequential action, or a need to resume after interruption, which covers most production
agents. Skip it for short, read-only, single-phase loops (answer a question with two searches) where the
overhead buys nothing, and for genuinely open-ended exploration where you cannot name the phases; there,
use a step budget and stopping conditions instead. Measure time and failure rate per state, the rate of
rejected transitions (a design smell) and resume success after induced crashes. In interviews, expect to
be asked how you would stop an agent from deploying without tests; the answer is a guard in code, and
the follow-up is how you would resume it after a crash.

## Role lens
- **Engineer:** Writes the state enum, the transition table and the guards, exposes per-state tool
  lists to the model, and checkpoints after every transition using the framework's persistence or a
  small store.
- **Architect:** Owns which invariants must be enforced in code rather than prompts, where terminal and
  human-wait states sit, and the persistence and idempotency contract that makes resumption safe.
- **FDE:** Maps a customer's process (triage, approve, execute, reconcile) onto states and guards, and
  uses per-state metrics to show where runs stall in their environment.

## Check yourself
**Q:** A deployment agent occasionally pushes a release without the integration tests having run, even
though the system prompt says to always run them first. How would you redesign it so this cannot
happen, and how would you make it survive a restart mid-run?
**A:** Make the phases explicit: BUILD, TEST, DEPLOY, VERIFY, DONE, FAILED. Offer the deploy tool only
in DEPLOY, and guard the TEST → DEPLOY transition on a `tests_passed` flag that the harness sets from
the test tool's actual result, not from the model's claim. That moves the invariant from a prompt
preference to code the model cannot bypass, including under prompt injection. Persist (run_id, state,
data) after every transition so a restart reloads the state and re-enters it; give the deploy and
migration tools idempotency keys derived from the run and step so re-entering DEPLOY after a crash does
not deploy twice. Bound retries with a counter in the data that transitions to FAILED, and add a
NEEDS_HUMAN state for approvals.

## Quiz
### Q: Where should the rule "the agent may not finish until tests have passed" be enforced?
- [ ] In the system prompt, repeated at the end for emphasis
- [x] As a guard in the harness's transition function, checked against a flag set from the test tool's result
- [ ] In the model's chain-of-thought, by asking it to verify before finishing
- [ ] In the test tool itself, by having it refuse to return until the agent finishes
> Only the harness can make the rule unconditional. Prompt instructions are weighed by the model and can
> be overridden by long context or injection; the guard checks a boolean the model does not control.

### Q: What does checkpointing (state, data) after each transition buy that the transcript alone does not?
- [ ] Lower token cost per model call
- [ ] A guarantee that tools are idempotent
- [x] The ability to resume a crashed run at the right phase with the facts guards need, without inferring position from prose
- [ ] Protection against prompt injection
> The transcript records what was said, not where the run is or whether side effects completed. A
> persisted state and typed data record re-enters the correct phase; idempotency is a separate property
> the tools must provide.

### Q: An agent has been given thirty states, one per prompt it uses, and almost no guards. What has most likely gone wrong?
- [ ] The state machine needs more states to be safe
- [x] States were chosen around prompts rather than around differing allowed actions and resume points; this is a workflow written as a machine
- [ ] The model should be allowed to choose transitions freely
- [ ] Guards are unnecessary when there are many states
> States earn their place by restricting tools or marking resume points. Thirty prompt-shaped states
> with nothing enforced is a code-defined workflow in disguise and should be written as plain code.
