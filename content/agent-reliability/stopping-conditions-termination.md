---
id: stopping-conditions-termination
module: agent-reliability
name: Stopping conditions / termination
kind: concept
difficulty: core
est_minutes: 16
status: typed
lesson_type: lab
agent_relevance: core
mastery: implement
code_tested: yes
prerequisites:
  - agents/agent-loop
  - agents/tool-calling
related:
  - agent-reliability/max-step-budgets
  - agent-reliability/loop-detection
  - agent-reliability/durable-execution-and-checkpointing
  - agents/agent-loop
  - agents/state-machines
  - agents/human-in-the-loop
  - safety-security/excessive-agency
sources:
  - Official docs: Anthropic Claude API (stop_reason)
  - Official docs: OpenAI API (finish_reason)
---

## TL;DR
An agent loop never ends on its own, so termination is designed: a function over run state returns
continue, done or abort by combining the model's structured done signal, independent verifiers and hard
limits, and every exit records a labelled reason.

## For agent builders
- **Why it matters for agents.** Every tool-calling loop you write needs an exit you designed. Without
  one, truncated answers, narrated plans and endless re-checking all come back as "returned text", and
  your app shows them as finished work.
- **Where it shows up in your stack.** The provider stop field (`stop_reason` for Claude,
  `finish_reason` for OpenAI Chat Completions), framework turn caps such as `max_turns` in the OpenAI
  Agents SDK or `recursion_limit` in LangGraph, and the run status your UI, retries and evals read.
- **What you control.** The terminal tool and its schema, the verifier a submit must pass, the order of
  checks in `decide()`, the single nudge, the reserved wrap-up call, and the outcome taxonomy every run
  emits.
- **What you can skip.** Clever prompt wording that asks the model to "know when it is done". A
  structured submit plus an external check beats any instruction to self-assess.

## Lab
### Task
Build the fail-closed `decide()` that the first example leaves as a note: every provider stop reason goes
through `normalise()`, only a verified `submit_result` ends as success, and no tool runs after the
terminal decision. Time box: 45-60 min.
- **Prerequisites:** The agent loop and Tool calling; the two examples in this lesson.
- **Setup:** Python 3.10+ and `pip install pytest` (checked with Python 3.13 and pytest 9.1). Save the
  starter as `lab.py` and the tests as `test_lab.py`.
- **No model calls:** each scenario is a scripted fake model that returns one response per step.

### Starter code
```python
"""Lab: fail-closed termination for a tool-calling loop. Run: python lab.py"""
from dataclasses import dataclass
from types import SimpleNamespace as NS

@dataclass
class Outcome:
    status: str                       # succeeded | partial | aborted | failed
    reason: str
    result: dict | None = None

STOP_MAP = {"tool_use": "tools", "end_turn": "ended", "stop_sequence": "ended",
            "max_tokens": "truncated", "pause_turn": "resend", "refusal": "refused",
            "tool_calls": "tools", "stop": "ended", "length": "truncated",
            "content_filter": "refused"}

def normalise(raw: str | None) -> str:
    # YOUR CODE HERE: look raw up in STOP_MAP; None or any unmapped value -> "unknown"
    raise NotImplementedError

class State:
    def __init__(self, max_steps=6):
        self.messages, self.executed, self.steps = [], [], 0
        self.max_steps, self.cancelled, self.nudged = max_steps, False, False
    def push_tool_result(self, call, out): self.messages.append(("tool", call.name, out))
    def push_user(self, text): self.messages.append(("user", text))
    def wrap_up(self): return {"summary": f"stopped after {self.steps} steps"}

def decide(state: State, resp, verify) -> Outcome | None:
    """Return an Outcome to stop or None to continue. Checks, in this order:
    1. state.cancelled -> aborted/user_cancelled
    2. normalise(resp.stop_reason): truncated -> failed/output_truncated,
       refused -> failed/refused, unknown -> failed/unknown_stop_reason
    3. a submit_result call whose args pass verify(args) -> succeeded/verified (even on the last step)
    4. state.steps >= state.max_steps -> partial/budget_exhausted with result=state.wrap_up()
    5. a rejected submit -> push_tool_result(call, "rejected: <detail>"), continue
    6. "ended" with no tool calls -> nudge once (push_user), then failed/ended_without_submit
    7. anything else ("tools", "resend") -> continue
    """
    # YOUR CODE HERE
    raise NotImplementedError

def run(state: State, model, tools: dict, verify) -> Outcome:
    while True:
        resp = model(state.messages)
        state.steps += 1
        out = decide(state, resp, verify)
        if out:
            return out                         # decided on the whole response: nothing else runs
        for call in resp.tool_calls:
            if call.name != "submit_result":
                state.executed.append(call.name)
                state.push_tool_result(call, tools[call.name](**call.args))

def call(name, **args): return NS(name=name, args=args)
def resp(stop, *calls): return NS(stop_reason=stop, tool_calls=list(calls))
def script(*resps):
    it = iter(resps)
    return lambda messages: next(it)

verify = lambda a: (a.get("failing") == 0, f"{a.get('failing')} tests failing")
TOOLS = {"run_tests": lambda: "2 failed", "send_email": lambda: "sent"}

SCENARIOS = {
    "reject_then_accept": script(resp("tool_use", call("submit_result", failing=2)),
                                 resp("tool_use", call("run_tests")),
                                 resp("tool_use", call("submit_result", failing=0))),
    "truncated": script(resp("max_tokens")),
    "refused": script(resp("refusal")),
    "unmapped_value": script(resp("model_context_window_exceeded")),
    "narrated_twice": script(resp("end_turn"), resp("end_turn")),
    "submit_with_parallel_call": script(resp("tool_use", call("send_email"),
                                             call("submit_result", failing=0))),
    "stuck": script(*[resp("tool_use", call("run_tests"))] * 6),
}

if __name__ == "__main__":
    for name, model in SCENARIOS.items():
        st = State()
        out = run(st, model, TOOLS, verify)
        print(f"{name:<26} {out.status}/{out.reason} steps={st.steps} tools_run={len(st.executed)}")
```

### Expected output
`python lab.py` with a correct solution prints exactly:

```
reject_then_accept         succeeded/verified steps=3 tools_run=1
truncated                  failed/output_truncated steps=1 tools_run=0
refused                    failed/refused steps=1 tools_run=0
unmapped_value             failed/unknown_stop_reason steps=1 tools_run=0
narrated_twice             failed/ended_without_submit steps=2 tools_run=0
submit_with_parallel_call  succeeded/verified steps=1 tools_run=0
stuck                      partial/budget_exhausted steps=6 tools_run=5
```

### Tests
Run `pytest -q test_lab.py`. On the starter all 7 fail with `NotImplementedError`; a correct solution
passes all 7 (Python 3.13, pytest 9.1).

```python
from lab import SCENARIOS, State, TOOLS, call, normalise, resp, run, script, verify

def outcome(name, **kw):
    st = State(**kw)
    return run(st, SCENARIOS[name], TOOLS, verify), st

def test_normalise_fails_closed():
    assert normalise("length") == "truncated" and normalise("end_turn") == "ended"
    assert normalise(None) == "unknown" and normalise("model_context_window_exceeded") == "unknown"

def test_rejected_submit_gets_a_result_then_verified_success():
    out, st = outcome("reject_then_accept")
    assert (out.status, out.reason, st.steps) == ("succeeded", "verified", 3)
    assert ("tool", "submit_result", "rejected: 2 tests failing") in st.messages

def test_truncated_refused_and_unknown_never_succeed():
    for name, reason in [("truncated", "output_truncated"), ("refused", "refused"),
                         ("unmapped_value", "unknown_stop_reason")]:
        out, _ = outcome(name)
        assert (out.status, out.reason) == ("failed", reason)

def test_one_nudge_then_fail():
    out, st = outcome("narrated_twice")
    assert (out.status, out.reason) == ("failed", "ended_without_submit")
    assert sum(1 for m in st.messages if m[0] == "user") == 1

def test_no_tool_runs_after_the_terminal_event():
    out, st = outcome("submit_with_parallel_call")
    assert out.status == "succeeded" and st.executed == []

def test_budget_gives_partial_with_wrap_up():
    out, st = outcome("stuck")
    assert (out.status, out.reason) == ("partial", "budget_exhausted")
    assert out.result == {"summary": "stopped after 6 steps"} and len(st.executed) == 5

def test_verified_submit_on_last_step_wins_and_cancel_wins_over_all():
    last = script(resp("tool_use", call("submit_result", failing=0)))
    assert run(State(max_steps=1), last, TOOLS, verify).status == "succeeded"
    st = State(); st.cancelled = True
    out = run(st, script(resp("tool_use", call("submit_result", failing=0))), TOOLS, verify)
    assert (out.status, out.reason) == ("aborted", "user_cancelled")
```

### Failure scenarios
- **Map unknown values to `"ended"` instead of `"unknown"`.** `unmapped_value` no longer fails at step 1:
  it is nudged, the scripted model has nothing left and raises `StopIteration`. In production the run
  idles or is shown as finished. Log the raw stop reason per response and alert on unmapped values.
- **Check the budget before the verifier.** The last-step test returns `partial` for a submit that
  passed, so a correct answer is counted as not done. Print the order in which `decide()` evaluates each
  check for that one response.
- **Return `None` for a rejected submit without pushing its result.** The test for the rejected submit
  fails, and against a real API the next request is rejected because a tool call has no result. Diff the
  tool call names in the response against the tool results in `messages`.
- **Execute tool calls before deciding.** `submit_with_parallel_call` reports `tools_run=1`: the email
  went out in the response that ended the run. Compare tool timestamps with the terminal event in the
  trace.

### Submission
- **`lab.py`** with `normalise()` and `decide()` implemented; `run()` and the scenarios unchanged.
- **A green run** of `pytest -q test_lab.py` (7 passed) and the output of `python lab.py`.
- **One new scenario** of your own (for example `pause_turn` followed by a verified submit) with its
  expected status.
- **A short note:** which line of `decide()` you would change to make a child agent's `partial` reach its
  parent as a tool result instead of an answer.
- **Evaluated on:** no truncated, refused or unknown response ever yields `succeeded`, one nudge at
  most, and no tool executed after the terminal decision.

## Why it exists
- **The loop has no natural exit.** An agent loop is `while True: call model; run tools`. Prompt the
  model; if it emits tool calls, run them, append results and call again; if it emits none, return the
  text. The only exit is "the model did not ask for a tool", so termination becomes a *model decision*.
- **Models misjudge completion in both directions.** *Too early:* it narrates "I will now run the tests"
  without emitting a call, so the user gets a plan instead of a result, or it declares done after a tool
  error it did not read. *Too late:* tests already pass but it keeps "double-checking", each pass costlier
  because context grew. *Never:* it alternates two failing approaches or polls a status endpoint forever.
- **So "trust the model" fails,** and each failure looks like success unless the outcome is labelled.
- **"Cap at N steps" alone hides the truth.** It turns a never-ending run into a truncated one but tells
  nobody whether the result is complete, so downstream code treats a budget-exhausted draft as finished.
- **"Stop when the output contains DONE" is brittle.** It is a string match on free text, which the model
  emits inconsistently and which cannot carry a result payload. Hence the three ingredients in the
  TL;DR, plus a recorded reason on every exit.

## How it works
### Sources of stop signals
1. **API stop reason.** Branch on it, not on "was there a tool call", because truncation is not
   completion. Treating truncation as done is one of the most common silent bugs in agent code.

   | Meaning | Claude `stop_reason` | OpenAI Chat Completions `finish_reason` |
   |---|---|---|
   | Wants tools run | `tool_use` | `tool_calls` |
   | Finished its turn | `end_turn` | `stop` |
   | Cut off at output limit (truncation) | `max_tokens` | `length` |
   | Hit a stop sequence | `stop_sequence` | (folded into `stop`) |
   | Filtered or refused | `refusal` (newer versions) | `content_filter` |
   | Long server-side tool turn paused; send it back to continue | `pause_turn` (newer versions) | n/a |

   - The set is vendor- and version-specific, so an unknown value should **fail closed**.
2. **Terminal tool.** Give the model `submit_result(answer, confidence, open_issues)` and define "done" as
   "the model called it".
   - It is a tool call with a schema, so it is structured, validated, logged, and cannot be emitted by
     accident mid-prose.
   - No tool call at all then means "stopped without submitting": a soft failure you nudge once ("call
     submit_result or continue working").
3. **Programmatic verifiers.** Anything checkable outside the model: the test suite passes, the output
   parses against a schema, the ticket exists in the tracker, a rubric-based grader approves. Verifiers
   decide *correctness*; the model only decides *when to claim*.
4. **Budgets.** Steps, tokens, wall-clock, money. They should fire rarely and always mean "stopped, not
   finished".
5. **Progress checks.** Loop detection and no-new-information counters.
6. **External signals.** User cancel, client disconnect, a human denying an approval, a policy guard
   tripping, graceful shutdown of the worker.

### The decision function
After every step, evaluate signals in a fixed order, cheap and safe checks first:
1. **External aborts.**
2. **Hard budgets.** A breach wins even if the model simultaneously claims success; record the claim, but
   the status is "exhausted".
3. **Progress checks.**
4. **The verifier.**
5. **The model's signal.**

- **Refinement: verify a submit that crosses the budget.** If that response carries a submit, run the
  verifier before declaring exhaustion: a *verified* last-step result is a success, an unverified claim
  is still `partial`. The verifier may jump the queue because it does not depend on the model's judgement.
- **Return values.** The function returns `continue`, `done(result)` or `abort(reason)`.
- **Final record.** It carries `status ∈ {succeeded, partial, aborted, failed}` plus a reason code.

### Why "done" must be a labelled outcome
- **Consumers need the distinction.** The UI, downstream workflows and evaluation pipelines must tell
  "verified success" from "model says success" from "budget ran out with a draft".
- **Collapsing them corrupts metrics.** One return value means your task-completion metric counts
  truncations as completions. Make status a first-class field and let the caller decide what to show.

### Ending cleanly
Deciding to stop is half the job; stopping without debris is the other half. An abort can arrive while
tool calls are in flight, so there are two modes:

| Mode | What happens | Use for | Because |
|---|---|---|---|
| **Graceful** | Stop new model calls; let in-flight tool calls finish or hit their per-call timeout; record results; write the outcome | Budgets, verifier decisions | In-flight work is legitimate and its results belong in the record |
| **Hard** | Cancel in-flight calls (cooperative cancellation tokens, killing subprocesses) | User cancel, policy guard, denied approval | Continuing is unsafe or unwanted |

- **Always release resources.** Free leases, sandboxes, temp branches and locks in a `finally`-style
  cleanup.
- **Cancelled is not "not run".** A cancelled call may still have taken effect downstream, so list such
  calls in the outcome as "possibly executed".

### The wrap-up turn
- **The best artefact is often the model's own account.** When a budget or progress check fires, do not
  return the last raw message.
- **Make one final call with tools withheld.** Omit the tool list, or set tool choice to none where the
  API supports it, and ask for what was done, what remains and what blocked it. Withholding tools matters
  because otherwise the model treats the request as another chance to act.
- **Reserve budget for it**, or it cannot be afforded at exactly the moment it is needed.

### Sub-agents and termination
- **A child returns the same labelled status, never a bare string**, because its outcome is an input to
  its parent.
- **The parent decides explicitly on `partial`:** retry with more budget, try another approach, or
  propagate `partial` upward, instead of treating the child's draft as an answer.
- **Without this,** a truncation three levels down surfaces at the top as a confident success.

### The state-machine view
- **Termination is a set of transitions:**
  `running → awaiting_tool → running → … → {succeeded | partial | aborted | failed}`.
- **Terminal states are absorbing.** A resumed or retried run gets a new attempt rather than re-entering
  a terminal state.
- **Pairs with checkpointing,** which persists the state so a crash does not lose the outcome.

## Diagram
The checks run in a fixed order after every model response; only a verified submit ends as success.

```mermaid
flowchart TD
    R["Model response"] --> C{"Cancelled?"}
    C -->|"yes"| AB["aborted: user_cancelled"]
    C -->|"no"| T{"max_tokens?"}
    T -->|"yes"| F["failed: output_truncated"]
    T -->|"no"| V{"Submit passes verifier?"}
    V -->|"yes"| S["succeeded: verified"]
    V -->|"no"| B{"Budget exhausted?"}
    B -->|"yes"| P["partial: wrap-up summary"]
    B -->|"no"| E{"Rejected submit or bare end_turn?"}
    E -->|"submit rejected or first nudge"| L["Continue loop"]
    E -->|"second bare end_turn"| F2["failed: ended_without_submit"]
    E -->|"tool calls"| L
```

## Examples
### A decision function with a verified terminal tool
This loop makes "done" a tool call that an external check must accept, treats truncation as failure, and
nudges exactly once when the model ends its turn without submitting.
- **Order:** cancellation first; a verified submit is accepted even on the last allowed step (see
  Trade-offs); only then does budget exhaustion produce a `partial` built from the wrap-up turn.
- **Walk one run through it:** step 6 submits with 2 of 40 tests red and is rejected with the failure
  names; step 9 submits all green and ends `succeeded/verified`. Had step 9 been the last allowed step
  with tests red, it would end `partial/budget_exhausted`, counted as not done. The scripted runs at
  the bottom run these paths.
- **Wire in the fail-closed map.** As written, `decide()` checks raw Claude values only, so a `refusal`
  with no tool calls falls through to `None` and the run idles until the budget fires. In real code, pass
  the stop reason through `normalise()` (next example) and return `failed` on `refused` or `unknown`.

```python
from dataclasses import dataclass

@dataclass
class Outcome:
    status: str                      # succeeded | partial | aborted | failed
    reason: str
    result: dict | None = None

def decide(state, resp, verify) -> Outcome | None:
    """Return an Outcome to stop, or None to continue. Order matters."""
    if state.cancelled:
        return Outcome("aborted", "user_cancelled")
    if resp.stop_reason == "max_tokens":
        return Outcome("failed", "output_truncated")
    submit = next((c for c in resp.tool_calls if c.name == "submit_result"), None)
    ok, detail = verify(submit.args) if submit else (False, "")   # independent check
    if ok:                                           # verified success wins even on the last step
        return Outcome("succeeded", "verified", submit.args)
    if state.steps >= state.max_steps or state.tokens >= state.max_tokens:
        return Outcome("partial", "budget_exhausted", state.wrap_up())  # no-tools summary call
    if submit:
        # every tool call needs a matching result, or the next API request is rejected
        state.push_tool_result(submit, f"rejected: {detail}. Keep working.")
        return None
    if resp.stop_reason == "end_turn":               # stopped talking, did not submit
        if state.nudged:
            return Outcome("failed", "ended_without_submit")
        state.nudged = True
        state.push_user("Call submit_result when finished, or continue working.")
    return None

def run(state, model, tools, verify):
    while True:
        resp = model(state.messages)
        state.account(resp)                          # steps, tokens from usage
        out = decide(state, resp, verify)
        if out:
            return out
        for call in resp.tool_calls:
            if call.name != "submit_result":     # a rejected submit already got its result
                state.push_tool_result(call, tools[call.name](**call.args))

# --- runnable check with a scripted fake model (Python 3.10+, no dependencies) ---
from types import SimpleNamespace as NS

class State:
    def __init__(self, max_steps=12):
        self.messages, self.steps, self.tokens = [], 0, 0
        self.max_steps, self.max_tokens = max_steps, 100_000
        self.cancelled = self.nudged = False
    def account(self, resp): self.steps += 1; self.tokens += resp.tokens
    def push_tool_result(self, call, out): self.messages.append(("tool", call.name, out))
    def push_user(self, text): self.messages.append(("user", text))
    def wrap_up(self): return {"summary": "done / remaining / blocked"}

def scripted(*failing):                   # each step submits with this many red tests
    resps = iter(NS(stop_reason="tool_use", tokens=900,
                    tool_calls=[NS(name="submit_result", args={"failing": f})]) for f in failing)
    return lambda messages: next(resps)

verify = lambda a: (a["failing"] == 0, f"{a['failing']} tests failing")
print(run(State(), scripted(2, 0), {}, verify))           # succeeded / verified
print(run(State(max_steps=1), scripted(2), {}, verify))   # partial / budget_exhausted
cut = lambda m: NS(stop_reason="max_tokens", tokens=4096, tool_calls=[])
print(run(State(), cut, {}, verify))                       # failed / output_truncated
```

### Fail-closed mapping of provider stop reasons
Normalise vendor values into one internal enum before `decide()` sees them, so a new or unknown value
stops the run instead of being read as "finished".

```python
STOP_MAP = {
    # Anthropic stop_reason
    "tool_use": "tools", "end_turn": "ended", "stop_sequence": "ended",
    "max_tokens": "truncated", "pause_turn": "resend", "refusal": "refused",
    # OpenAI Chat Completions finish_reason
    "tool_calls": "tools", "stop": "ended", "length": "truncated",
    "content_filter": "refused",
}

def normalise(raw: str | None) -> str:
    return STOP_MAP.get(raw or "", "unknown")    # unknown -> caller treats as failed

assert normalise("length") == "truncated"
assert normalise("some_future_value") == "unknown"
```

### Worked scenario: what a labelled outcome reveals
A team ships 1,000 runs a day of a ticket-triage agent. Before labelling, every run that returned text
counted as "completed".

| Outcome after labelling | Runs | What it means |
|---|---|---|
| `succeeded/verified` | 780 | Ticket exists and passes schema check |
| `partial/budget_exhausted` | 120 | Draft only; needs retry or a human |
| `failed/output_truncated` | 60 | Answer cut mid-JSON, previously shown as done |
| `failed/ended_without_submit` | 30 | Narrated a plan, never acted |
| `aborted/user_cancelled` | 10 | User stopped the run |

The old dashboard said 99% complete (everything but the cancels); the honest number is 78%. The numbers
are illustrative, but the shape is typical: truncations and narrated plans hide inside "returned text".

## Production patterns
- **One outcome taxonomy for every agent.** When several teams ship agents on one platform, define
  `status` plus a reason-code enum in a shared library and reject runs that return a bare string. Monitor
  the termination-reason distribution per task type.
- **Fail-closed stop-reason mapping.** On every provider integration, map raw `stop_reason` /
  `finish_reason` values through an explicit table with an "unknown" default (as above), and alert when
  "unknown" appears, which usually means an API version bump.
- **Verifier-gated submit.** For tasks with a checkable finish (tests, schema, record exists), run the
  verifier inside the loop on every `submit_result` and push the failure details back as the tool
  result. Monitor the reject rate per run and verifier latency, since the budget has to cover it.
- **Reserved wrap-up budget.** Hold back one model call's worth of tokens below the hard cap, and when a
  budget fires, call the model with no tools (omit the tool list or set tool choice to none) for a
  "done / remaining / blocked" summary. Monitor how often the wrap-up call itself fails or truncates.
- **Cleanup in `finally`.** Release leases, sandboxes and temp branches in a cleanup block that runs on
  every exit path, and record cancelled-in-flight calls as "possibly executed". Monitor orphaned
  sandbox counts and lease-expiry waits on the next attempt.

## Trade-offs
- **Terminal tool vs prompt overhead.** A structured, loggable done signal costs a description in every
  prompt, and models occasionally forget to call it, so you need the one-nudge fallback, which costs a
  step.
- **Verifier gating vs availability.** It rejects false claims of success, but only when a verifier
  exists; for open-ended tasks such as writing a memo you fall back to rubric-based LLM judges, which
  carry their own error rate and cost.
- **Budget-first ordering vs last-step successes.** Budgets first guarantee a cap but can reject a
  last-step success. Verifying a present submit first (as the example does) recovers it, safely only when
  verification is cheap and bounded; an expensive verifier (a long test suite) must fit in the budget.
- **Graceful vs hard termination.** Graceful preserves in-flight results and a coherent record, but a hung
  tool delays the stop until its timeout; for user cancels and safety aborts the trade flips toward hard
  cancellation and accepting "possibly executed" calls.
- **Nudging vs wasted calls.** Nudging after a bare `end_turn` rescues "narrated but did not act" turns,
  but on a genuinely finished task it wastes a model call; cap it at one nudge per run.

## Pitfalls
- **Treating truncation as completion.** Symptom: answers ending mid-sentence or JSON that fails to
  parse, marked successful. Cause: `stop_reason == "max_tokens"` (or `finish_reason == "length"`) read as
  done. Diagnose: log the raw stop reason per response and count `succeeded` runs whose last one was a
  truncation. Fix: map it to `truncated` and fail or continue.
- **Same shape for "verified done" and "budget exhausted".** Symptom: a healthy completion dashboard
  while users complain about half-finished work. Diagnose: sample "completed" traces and count those
  that hit the step cap or never passed a verifier. Fix: a labelled status field.
- **The model as its own verifier** ("are you sure you are done?"). Symptom: a confident yes on failing
  tests, because self-assessment correlates poorly with success on the hard cases. Diagnose: re-run the
  verifier offline on model-claimed successes and measure disagreement. Fix: an external check.
- **Terminal tool that performs the final side effect** (submit = send the email). Symptom: duplicate or
  premature sends when the model resubmits, because a rejected verification has already acted.
  Diagnose: count side effects per run against accepted submits. Fix: submit only proposes; the harness
  acts after verification.
- **Ignoring parallel tool calls next to `submit_result`.** Symptom: tools executing after the run is
  marked done. Diagnose: find tool spans timestamped after the terminal event. Fix: decide on the whole
  response before running any of its calls.
- **Wrap-up summary with tools still enabled.** Symptom: the wrap-up call emits another tool call, which
  runs past the budget or is dropped, leaving no summary. Diagnose: check the wrap-up response's stop
  reason. Fix: withhold tools.
- **Aborting without cleanup.** Symptom: orphaned sandboxes, leases blocking the next attempt until they
  expire, temp branches piling up in the customer's repository. Diagnose: compare live sandboxes and
  leases with running runs. Fix: `finally`-style cleanup.

## When to use
- **Always:** every agent loop needs this; the choice is how rich to make it.
- **Minimum for a single-tool chatbot:** branch correctly on the stop reason plus a step cap.
- **Writes to external systems or more than a handful of steps:** add a terminal tool and a verifier, and
  emit a labelled outcome.
- **Measure:** the distribution of termination reasons per task type (verified, model-claimed, exhausted,
  aborted). A rising "exhausted" share means the agent is getting stuck more often, not that the budget is
  too small.

## Exercise
**Goal:** add labelled termination to a parent agent that delegates to one sub-agent, and prove every
exit path. No solution is given.
- **Inputs:** a loop you own (or the example above), a real verifier (tests, schema check or record
  lookup) and a scripted fake model for both levels.
- **Constraints:** the fail-closed stop map inside `decide()`, one nudge per run, a reserved no-tools
  wrap-up call, and a child that returns a labelled status the parent must branch on.
- **Deliver:** scripted runs forcing truncation, refusal, narration, a rejected then accepted submit,
  `submit_result` next to a parallel tool call, budget exhaustion, cancel, and a child `partial`.
- **Acceptance:** tests show no truncated, refused or `unknown` response ever yields `succeeded`; no tool
  runs after the terminal event; a child `partial` never surfaces as parent success.

## Role lens
- **Engineer:** Writes the `decide()` function, maps provider stop reasons correctly, adds the submit
  tool to the schema and asserts in tests that a truncated response never returns `succeeded`.
- **Architect:** Defines the outcome taxonomy and reason codes that every agent on the platform must
  emit, so metrics, retries and user interfaces can reason about runs uniformly.
- **FDE:** Translates a customer's notion of "done" (ticket closed in ServiceNow, record updated in
  SAP) into a verifier the loop can call, and explains why the agent's own claim is not enough for
  sign-off.

## Check yourself
**Q:** A coding agent sometimes replies "I have completed the task" without running the tests, and
other times keeps refactoring for twenty more steps after the tests already pass. Design the
termination logic that fixes both behaviours.
**A:** Separate the claim from the verification. Add a `submit_result` terminal tool and define
completion as "submit called and the test suite passes" via a verifier the loop runs itself; a submit
with failing tests is rejected with the failure output pushed back as a message, which removes the
premature-done case. For the over-working case, run the verifier after every tool step rather than
only on submit: once tests pass, inject "tests pass; call submit_result now" and refuse further edit
tools, or cap post-pass steps at two. Keep budgets as a backstop, and record outcomes as verified,
partial or exhausted so the completion metric reflects reality rather than the model's confidence.

## Interview questions
### Why can't the model alone decide when an agent run is finished?
- **Its judgement fails both ways.** Models stop early (narrating a plan without a tool call, or declaring
  done after an unread error) and late (re-checking work that already passes), and some never stop.
- **Its claim is not evidence.** Self-assessment correlates poorly with success on hard cases, so
  correctness has to come from a verifier the harness runs, with budgets as a backstop.

### Your completion dashboard says 98% but users report half-finished answers. How do you debug it?
- **Check how "complete" is computed.** If any returned text counts, truncations (`max_tokens`, `length`)
  and budget-exhausted drafts are being counted as successes.
- **Add labelled outcomes and reason codes**, then backfill from traces: look for responses ending
  mid-sentence, unparseable JSON and runs that hit the step cap.
- **Fix the loop** to branch on the stop reason and to require a verified `submit_result` for `succeeded`.

### When would you put the verifier ahead of the budget check, and when not?
- **Ahead when it is cheap and bounded.** A schema check or quick test run lets a success that lands on
  the last step count as verified instead of `partial`.
- **Not when it is expensive.** A long test suite can blow through the wall-clock or money cap, so either
  budget for it explicitly or keep the budget check first and record the claim as unverified.

### A user hits cancel while three tool calls are running. What does your agent do?
- **Hard termination:** cancel the in-flight calls with cooperative cancellation tokens or by killing
  subprocesses, because continuing is unwanted.
- **Clean up in `finally`:** release leases, sandboxes, temp branches and locks.
- **Record honestly:** mark the run `aborted/user_cancelled` and list the cancelled calls as "possibly
  executed", since they may already have taken effect downstream.

## Quiz
### Q: The model returns stop_reason "max_tokens" with no tool calls. What should the loop do?
- [ ] Return the text as the final answer, since no tool was requested
- [x] Treat it as a truncation: either continue the generation or mark the run failed, never succeeded
- [ ] Retry the same request with temperature 0
- [ ] Ask the model whether it was finished
> `max_tokens` means the output was cut off at the limit, so the content is incomplete regardless of
> tool calls. Returning it as done produces half-answers and broken JSON marked successful; the loop
> must continue the generation or fail explicitly.

### Q: Why is a terminal tool (submit_result) preferred over detecting the word DONE in the model's text?
- [ ] Tools are cheaper in tokens than text
- [ ] Models cannot write the word DONE reliably
- [x] A tool call is structured, schema-validated and logged, can carry the result payload, and gives the harness a hook to run verification
- [ ] Text output cannot be streamed to the client
> A string match on prose is brittle and carries no data. A tool call has a schema, appears in traces,
> cannot occur accidentally mid-sentence, and lets the harness verify before accepting completion.

### Q: Which ordering of checks in the decision function is safest?
- [ ] Model signal first, then verifier, then budgets
- [x] External aborts and hard budgets first, then progress checks, then verifier, then model signal
- [ ] Verifier first, then budgets, then model signal
- [ ] Any order, since each check is independent
> Hard limits and cancellations must win regardless of what the model claims, so they are evaluated
> first. The model's signal is the least trustworthy input and comes last, gated by the verifier.

## Mastery test
**Problem:** an agent migrates 2 million customer rows through a `write_batch` tool. A full row-by-row
comparison takes 20 minutes; the run cap is 40 steps and 30 minutes. Design how the run ends, including
a user cancel mid-batch and a provider stop reason you have never seen.

**A strong answer:**
- gates `submit_result` on a cheap in-loop check (counts, sampled checksums) and runs the full comparison
  after the loop, reporting `partial` until it passes, because the budget cannot cover it;
- makes submit only propose, so a rejected claim has written nothing extra;
- lets the in-flight batch commit or roll back in its transaction on cancel, then lists it as possibly
  executed and releases locks in cleanup;
- maps the unseen stop reason to `unknown`, fails closed and alerts.

## Use cases & projects
### Use cases
- **Coding agents.** "Done" means a `submit_result` call plus a green test run that the harness executes,
  not the model writing "I have completed the task".
- **Ticket and CRM agents.** A verifier confirms the record exists and passes a schema check before the
  run counts as `succeeded`; everything else is labelled partial or failed.
- **Research and report agents.** With no hard verifier, a rubric-based judge gates the submit, and budget
  exits return a labelled `partial` carrying the no-tools wrap-up summary.
- **Multi-agent orchestrators.** Sub-agents return the same labelled status, so the parent retries, switches
  approach or propagates `partial` instead of trusting a child's draft.

### Mini project
**Add labelled termination to a toy agent (2-3 hours).**
- **Goal:** turn a bare `while True` tool loop into one that ends only through `decide()` with a status and
  a reason code.
- **Steps:** give a small agent two tools (read a file, run a test command) plus `submit_result`; add the
  fail-closed stop-reason map, a verifier that runs the tests, one nudge after a bare `end_turn`, a step cap
  and a no-tools wrap-up call; then force each exit with a fake model (truncate, narrate, loop, succeed).
- **Done when:** a test suite proves a truncated response never returns `succeeded`, every exit path shows
  up with its reason code in a per-run log, and a table counts outcomes over 20 scripted runs.
