---
id: stopping-conditions-termination
module: agent-reliability
name: Stopping conditions / termination
kind: concept
difficulty: core
est_minutes: 13
status: reviewed
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
An agent loop is `while True: call model; run tools`. Nothing in it ends on its own, so termination
has to be designed. A stopping condition is a function over the run's state that returns continue,
done or abort, combining the model's own "I am finished" signal with programmatic goal checks and hard
resource limits. It has to be built this way because a model deciding alone stops too early, too late
or never, and each failure looks like success unless the outcome is labelled.

## Why it exists
The agent loop is deceptively simple. Prompt the model; if it emits tool calls, execute them, append
the results and call again; if it emits none, return the text. The only exit is "the model did not
ask for a tool". That makes termination a *model decision*, and models are unreliable judges of their
own completion in both directions.

Too early: the model narrates "I will now run the tests" without actually emitting a tool call, the
loop sees no call and returns, and the user receives a plan instead of a result. Or it declares the
job done after a tool error it did not read carefully. Too late: a task with a verifiable finish
(tests pass) keeps going because the model wants to "double-check" or refactor, each pass costing
more tokens than the last because the context has grown. Never: the model alternates between two
failing approaches, or polls a status endpoint indefinitely.

The naive fixes fail. "Trust the model" fails as above. "Cap at N steps" alone converts a
never-ending run into a truncated one but tells nobody whether the result is complete, so downstream
code treats a budget-exhausted draft as a finished answer. "Stop when the output contains DONE" is a
string match on free text, which the model emits inconsistently and which cannot carry a result
payload.

Termination therefore needs three ingredients: an explicit, structured signal from the model;
independent checks that do not depend on the model's judgement; and hard limits as a backstop. And
every exit must record *why* it exited.

## How it works
### Sources of stop signals
1. **API stop reason.** Claude returns `stop_reason`: `tool_use` (the model wants tools run),
   `end_turn` (it finished its turn), `max_tokens` (output was cut off at the limit) and
   `stop_sequence`; newer API versions add values such as `pause_turn` (a long server-side tool turn
   was paused and should be sent back to continue) and `refusal`, so the set is vendor- and
   version-specific and an unknown value should fail closed. OpenAI's Chat Completions
   `finish_reason` has the analogous `tool_calls`, `stop`, `length` and `content_filter`. The loop
   must branch on these values, not on "was there a tool call", because `max_tokens` / `length` means
   truncation, not completion. Treating truncation as done is one of the most common silent bugs in
   agent code.
2. **Terminal tool.** Give the model a `submit_result(answer, confidence, open_issues)` tool and
   define "done" as "the model called it". Because it is a tool call with a schema, it is
   structured, validated, logged, and cannot be emitted by accident in the middle of prose. The
   absence of any tool call then means "the model stopped without submitting", which you can treat
   as a soft failure and nudge once ("call submit_result or continue working").
3. **Programmatic verifiers.** Anything checkable outside the model: the test suite passes, the
   output parses against a schema, the ticket exists in the tracker, a rubric-based grader approves.
   Verifiers decide *correctness*; the model only decides *when to claim*.
4. **Budgets.** Steps, tokens, wall-clock, money. They should fire rarely and always mean "stopped,
   not finished".
5. **Progress checks.** Loop detection and no-new-information counters.
6. **External signals.** User cancel, client disconnect, a human denying an approval, a policy guard
   tripping, graceful shutdown of the worker.

### The decision function
After every step evaluate the signals in a fixed order: external aborts, then hard budgets, then
progress checks, then the verifier, then the model's signal. Cheap and safe checks come first because
a budget breach should win even if the model simultaneously claims success; the claim can be recorded,
but the status is "exhausted". One refinement: if the response that crosses the budget also carries a
submit, run the verifier on it before declaring exhaustion. A *verified* result on the last step is a
success; an unverified claim is still `partial`. The verifier can jump the queue this way because it
is the one check that does not depend on the model's judgement. The function returns one of `continue`, `done(result)` or
`abort(reason)`, and the run's final record carries `status ∈ {succeeded, partial, aborted, failed}`
plus a reason code.

### Why "done" must be a labelled outcome
Consumers (the UI, downstream workflows, evaluation pipelines) need to distinguish "verified
success" from "model says success" from "budget ran out with a draft". Collapsing these into one
return value means your task-completion metric counts truncations as completions. Make the status a
first-class field and let the caller decide what to show.

### Ending cleanly
Deciding to stop is half the job; the other half is stopping without leaving debris. An abort can
arrive while tool calls are in flight, so termination has two modes. **Graceful**: stop issuing new
model calls, let in-flight tool calls finish or hit their per-call timeout, record their results,
then write the outcome; use it for budgets and verifier decisions, because the in-flight work is
legitimate and its results belong in the record. **Hard**: cancel in-flight calls (cooperative
cancellation tokens, killing subprocesses), because the reason to stop is that continuing is unsafe
or unwanted: a user cancel, a policy guard, a denied approval. Either way, release what the run holds
(leases, sandboxes, temp branches, locks) in a `finally`-style cleanup, and remember that a
cancelled call may still have taken effect downstream, so the outcome should list such calls as
"possibly executed" rather than "not run".

### The wrap-up turn
When a budget or progress check fires, the most useful artefact is often the model's own account of
where it got to. Rather than returning the last raw message, make one final model call with tools
withheld (omit the tool list, or set tool choice to none where the API supports it) and a prompt
asking for a summary of what was done, what remains and what blocked it. Withholding tools matters
because otherwise the model treats the request as another chance to act. Reserve budget for this
call, or it cannot be afforded at exactly the moment it is needed.

### Sub-agents and termination
A sub-agent's outcome is an input to its parent, so it must return the same labelled status, never a
bare string. A parent that receives `partial` from a child should decide explicitly (retry with more
budget, try another approach, or propagate `partial` upward) instead of treating the child's draft as
an answer. Without this, a truncation three levels down surfaces at the top as a confident success.

### The state-machine view
Termination is a set of transitions in the run's state machine:
`running → awaiting_tool → running → … → {succeeded | partial | aborted | failed}`. Terminal states
are absorbing: a resumed or retried run gets a new attempt rather than re-entering a terminal state.
This pairs naturally with checkpointing, which persists the state so a crash does not lose the
outcome.

## Example
This loop makes "done" a tool call that an external check must accept, treats truncation as failure
instead of completion, and nudges exactly once when the model ends its turn without submitting.
Cancellation is checked first; a verified submit is accepted even if it lands on the last allowed
step (the trade-off below), and only then does budget exhaustion produce a `partial` built from the
wrap-up turn. Walk one run through it: step 6 submits with 2 of 40 tests failing, so the submit is
rejected with the failure names and the run continues; step 9 submits with all tests green and the
run ends `succeeded/verified`. Had step 9 instead been the twelfth and last allowed step with tests
still red, the outcome would be `partial/budget_exhausted` carrying the model's summary, and the
dashboard would count it as not done.

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
```

## Trade-offs
- A terminal tool gives a structured, loggable done signal, but every prompt must describe it and
  models occasionally forget to call it, so you need the one-nudge fallback, which costs a step.
- Verifier-gated termination rejects false claims of success, but only when a verifier exists; for
  open-ended tasks such as writing a memo you fall back to rubric-based LLM judges, which carry their
  own error rate and cost.
- Checking hard budgets before the model's signal guarantees a cap, but can reject a success that
  arrived on the last step; evaluating a present submit first (as the example does) recovers it, and
  is safe only because verification is cheap and bounded; if the verifier itself is expensive (a
  long test suite), the budget has to cover it too.
- Graceful termination preserves in-flight results and leaves a coherent record, but a hung tool
  delays the stop until its timeout; for user cancels and safety aborts the trade flips toward hard
  cancellation and accepting "possibly executed" calls.
- Nudging the model after a bare `end_turn` rescues "narrated but did not act" turns, but on a
  genuinely finished task it wastes a model call; cap it at one nudge per run.

## Pitfalls
- Treating `stop_reason == "max_tokens"` (or `finish_reason == "length"`) as completion; the symptom
  is answers that end mid-sentence or JSON that fails to parse, marked successful in the logs.
- Returning the same shape for "verified done" and "budget exhausted"; the symptom is a completion
  dashboard that looks healthy while users complain about half-finished work.
- Letting the verifier be the model itself ("are you sure you are done?"); the symptom is a confident
  yes on failing tests, because self-assessment correlates poorly with success on the hard cases.
- Making the terminal tool also perform the final side effect (submit = send the email), so a
  rejected verification has already acted; the symptom is duplicate or premature sends when the model
  resubmits after a rejection.
- Ignoring parallel tool calls where `submit_result` arrives alongside other calls; the symptom is
  tools executing after the run has already been marked done.
- Asking for a final summary with tools still enabled; the symptom is the "wrap-up" call emitting
  another tool call, which either runs past the budget or is dropped, leaving no summary at all.
- Aborting without cleanup; the symptom is orphaned sandboxes, leases that block the next attempt
  until they expire, and temp branches piling up in the customer's repository.

## When to use
Every agent loop needs this; the choice is how rich to make it. For a single-tool chatbot, branching
correctly on the stop reason plus a step cap is enough. For anything that writes to external systems
or runs for more than a handful of steps, add a terminal tool and a verifier, and emit a labelled
outcome. Measure the distribution of termination reasons per task type (verified, model-claimed,
exhausted, aborted): a rising "exhausted" share means the agent is getting stuck more often, not that
the budget is too small. In interviews, the expected answer to "how does your agent know it is done"
names all three ingredients and explains why the model alone cannot be trusted with the decision.

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
