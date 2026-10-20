---
id: durable-execution-and-checkpointing
module: agent-reliability
name: Durable execution & checkpointing
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - agent-reliability/state-persistence-and-conversation-recovery
  - agent-reliability/state-and-checkpoint-stores
  - agent-reliability/idempotent-tool-execution
  - agent-reliability/conversation-execution-identifiers
  - reliability/durable-workflow-execution
  - backend-fundamentals/background-jobs-durable-workers
  - system-design/workflow-orchestration
sources:
  - Official docs: Temporal
  - Official docs: LangGraph (persistence and checkpointers)
---

## TL;DR
An agent run is a long, multi-step program whose process can die at any moment (deploys, OOMs, spot
preemption) and whose steps cost real money and cause real side effects. Durable execution means the
run's progress outlives the process; checkpointing is the mechanism: persist state at step boundaries
so a resume continues from the last boundary instead of from scratch. The hard part is the boundary
around side effects, which is why checkpointing is inseparable from idempotent tools.

## Why it exists
A chat completion is a single request: if the process dies, the client retries and nothing is lost
but one call. An agent run differs in three ways. It is long: tens of steps, minutes to hours, with
waits on tools, humans and rate limits. It is expensive: restarting re-pays every token already
spent. And it has side effects: by step twelve it has created a branch, opened a ticket and sent a
message; restarting from scratch creates a second branch, a second ticket and a second message. Add
that the model is nondeterministic, so a restart does not even reproduce the same path: the "retry"
is a different run that may make different decisions on top of the first run's half-finished effects.

Process death is not rare. Every deploy restarts workers; autoscalers scale in; spot instances get
reclaimed; one oversized tool output triggers an OOM that takes the whole process; a dependency bug
raises somewhere unexpected. In a system running thousands of runs a day, "the process died mid-run"
is a daily event.

The naive answers are "run it in a background job with retries" (which restarts from zero, with the
problems above) or "keep the state in memory and hope" (which is where most prototypes live). Neither
answers the real question: when the process comes back, how does it know what already happened? That
requires state written somewhere durable before the process can lose it, and a resume procedure that
reads it and continues without redoing side effects.

## How it works
### What a checkpoint contains
Everything needed to continue as if nothing happened: the message transcript (or a reference to it),
the step index, budget counters, tool calls that were issued but not yet completed, pending human
approvals, tool-side state (temp directories, branch names), the prompt and model version, and any
random seed. Omit any of these and the resume is subtly different: budgets reset, a pending approval
is forgotten, or a new prompt version runs against old context.

### Two architectures
**Snapshot checkpointing.** After each step, write the full state keyed by (run id, step). Resume
means load the latest snapshot and continue the loop. It is simple and framework-agnostic, and it is
what LangGraph's checkpointers do after each super-step, keyed by a thread id; LangGraph also stores
the pending writes of nodes that finished inside a super-step that failed, so on resume those nodes
are not run again, which is the same intent-and-result idea applied to graph nodes. The cost is writing a
growing state on every step, mitigated by storing the transcript as append-only events and
snapshotting only the small non-transcript part.

**Event-sourced replay.** Persist each event (model response, tool result, timer fired, signal
received) to an append-only history. On resume, re-run the orchestration code from the beginning;
whenever it reaches a call whose result is already in the history, substitute the recorded result
instead of executing. This is the Temporal model: workflow code must be deterministic (no direct
clock reads, randomness or I/O; time and random values come from the engine's deterministic APIs,
which are recorded in the history, and all I/O goes through activities) so replay follows the same
path, while model calls and
tool calls are activities with recorded results and their own retry policies. The pay-off is that
orchestration code reads like ordinary sequential code while being crash-proof; the price is the
determinism discipline and a learning curve.

Both land in the same place: completed steps are never re-executed, and the only re-execution risk
is the step in flight at the moment of death.

### The side-effect boundary
Consider the sequence: (1) the model asks for `create_ticket`; (2) the harness calls the API; (3) the
ticket now exists; (4) the harness records the result. Death between 2 and 4 leaves a ticket with no
record. On resume the pending call has no result, so it runs again: two tickets. No checkpoint
placement fixes this, because the external effect and the local record cannot be made atomic. The
answer is a three-phase protocol: record the *intent* (a pending call with a deterministic
idempotency key derived from run id and step) before executing; execute, passing the key; record the
result. On resume, a pending intent without a result is re-executed with the *same* key, and an
idempotent tool or a dedup table turns the second execution into a no-op that returns the first
result. Durable execution without idempotent tools gives at-least-once side effects; with them,
effectively once.

### Resume protocol
1. A worker claims the run with an expiring lease, so two workers cannot resume it concurrently.
2. Load the latest checkpoint, or replay the history.
3. Validate the transcript: every tool call has a result; synthesize an error result for any that
   cannot be re-executed, because model APIs reject histories with unmatched calls.
4. Re-execute pending intents with their recorded keys.
5. Continue the loop under the *same* run id, incrementing a resume counter (or lease epoch) on the
   run record. A crash-resume is not a new attempt: a new attempt would mint a new run id and with
   it new idempotency keys, which is exactly what must not happen to a half-finished step.

What is *not* protected: a model call that completed but whose response was not yet saved is simply
made again on resume. That costs tokens and may produce a different decision, but it is safe,
because no side effect has happened until the intents are recorded and executed. This is why the
save sits between "model decided" and "tools run", not after the tools.

### Durable waits
Agents wait: for a human approval, a CI pipeline, a rate-limit window, a scheduled follow-up the next
morning. Holding a worker (and its memory, connections and lease) for hours of waiting wastes
capacity and bets the run on that process surviving. A durable runtime instead *parks* the run: it
records "waiting for signal `approval:42`" or "timer due at 09:00" and releases the worker. When the
signal arrives or the timer fires, any worker claims the run and continues from the checkpoint.
Temporal exposes this as durable timers and signals; in a snapshot design it is a status
(`awaiting_approval`) plus a wake-up row a scheduler polls. Either way, waiting costs storage, not a
process.

### Timeouts and retries per step
Each model call and tool call is the unit of retry, with its own timeout and retry policy, because a
retry of the whole run would repeat completed work. Separate the timeouts: one per attempt of the
call, and one for the call overall, including retries. For long tools, a heartbeat (the tool reports
"still working" every few seconds) lets the runtime detect a dead worker in seconds instead of
waiting out a timeout sized for the slowest legitimate case; Temporal activities support exactly
this. Retries apply automatically only to calls classified as idempotent or keyed; for the rest,
an ambiguous failure becomes a reconciliation step rather than a blind retry.

### Versioning
A checkpoint written by last week's code must load in this week's. Store a schema version in each
checkpoint and keep loaders for old versions or migrate on read. In replay-based systems, changing
orchestration code changes the path, so in-flight runs need versioned branches in the code or must be
drained before deploy.

### Checkpoint frequency
Per step is the natural unit: the step is the boundary between "model decided" and "world changed".
Checkpointing less often widens the window of lost work. Inside a step, the only extra writes worth
making are one per completed tool result, so a step with several parallel calls does not re-run the
finished ones on resume; anything finer (saving partial model output) buys little, because a model
call can be repeated safely: it costs tokens but has no side effects.
The write sits on the critical path of every step, so store latency matters; that is the topic of the
state and checkpoint stores lesson.

## Example
The loop saves the model's decision and the tool-call intents, with deterministic keys, *before*
executing anything, and saves each result as it lands. On restart, only intents without a result are
re-executed, with the same keys, so idempotent tools return the original result instead of acting
twice.

```python
def key_for(run_id, step, idx):                 # deterministic -> identical on resume
    return f"{run_id}:{step}:{idx}"

def load_or_start(store, run_id, initial_messages):
    cp = store.load_latest(run_id)              # None on first start
    if cp is None:
        cp = {"step": 0, "messages": initial_messages, "pending": [], "status": "running"}
        store.save(run_id, cp)
    return cp

def run(store, run_id, model, tools, initial_messages):
    cp = load_or_start(store, run_id, initial_messages)    # caller holds the run's lease
    while True:
        for call in cp["pending"]:              # empty unless we died mid-step
            if "result" not in call:            # completed calls are not re-run
                call["result"] = tools[call["name"]](**call["args"], idempotency_key=call["key"])
                store.save(run_id, cp)          # record each result as soon as it exists
        if cp["pending"]:
            cp["messages"] += [{"role": "tool", "tool_call_id": c["id"], "content": c["result"]}
                               for c in cp["pending"]]
            cp["pending"] = []
            store.save(run_id, cp)
        resp = model(cp["messages"])            # lost on a crash before the next save: re-asked
        cp["messages"].append(resp.message)
        if not resp.tool_calls:
            cp["status"] = "done"
            store.save(run_id, cp)
            return cp
        cp["pending"] = [{"id": c.id, "name": c.name, "args": c.args,
                          "key": key_for(run_id, cp["step"], i)}
                         for i, c in enumerate(resp.tool_calls)]
        cp["step"] += 1
        store.save(run_id, cp)                  # intent recorded BEFORE execution
```

Kill the worker at each boundary and see what the resume does. Killed during the model call: no
intents exist, so the call is simply made again. Killed in the fourth step of run `run_9` (step
index 3), whose model response asked for three parallel calls, after call 0 finished and was
recorded and while call 1 was executing: the resume skips call 0, re-runs call 1 with the same key
`run_9:3:1` so the downstream API returns the first execution's result, then runs call 2 for the
first time. Killed after all three results were recorded but before the messages were updated: the
resume re-runs nothing and only finishes the bookkeeping. At no boundary does a side effect happen
twice, provided the tools honour the key.

## Trade-offs
- Snapshot checkpointing is simple and framework-agnostic, but write size grows with the transcript;
  splitting append-only events from a small snapshot restores per-step writes of constant size at the
  cost of two tables.
- Replay-based durable execution makes orchestration code crash-proof with no explicit save calls,
  but demands deterministic workflow code and versioning discipline; worth it once runs are long and
  numerous, overkill for a ten-step helper.
- Checkpointing every step adds a durable write to each iteration's latency; acceptable with a
  nearby Postgres or Redis, noticeable with a remote store, which is why some teams checkpoint only
  before side effects.
- Deterministic idempotency keys make retries safe, but require every side-effecting tool to honour
  them; tools that cannot must be gated behind approvals or an outbox instead.

## Pitfalls
- Checkpointing only after tool execution; the symptom is duplicate tickets, emails or commits after
  worker restarts, each a few seconds apart.
- Regenerating idempotency keys randomly on resume; the symptom is a dedup layer that exists but
  duplicates that still appear after crashes.
- Resuming without a lease; the symptom is two workers continuing the same run and a transcript with
  interleaved, contradictory tool results.
- Changing the prompt or tool schema and resuming old checkpoints without a version check; the
  symptom is resumed runs failing with malformed-history errors or behaving differently from fresh ones.
- Storing the entire transcript in every snapshot; the symptom is checkpoint storage growing
  quadratically and store latency climbing as runs lengthen.
- Waiting for a human approval inside a live worker; the symptom is runs lost whenever a deploy
  happens during business hours, and worker pools sized for idle waiting rather than work.
- Treating a crash-resume as a new attempt with a fresh run id; the symptom is the same as random
  keys: duplicates after restarts, because every derived idempotency key changed.

## When to use
Reach for checkpointing as soon as a run can outlive a single request, touches external systems, or
costs enough that restarting hurts, which in practice is nearly every production agent. A chat
assistant with read-only tools and short turns can get by with transcript persistence alone. Choose
snapshot checkpointing when you control a simple loop and want minimal dependencies; choose a replay
engine when runs are long, span human waits of hours or days, and you already run workflow
infrastructure. Measure resume success rate, duplicate-side-effect incidents and checkpoint write
latency. In interviews the question is usually "what happens if the worker dies between the API call
and recording its result"; have the three-phase answer ready.

## Role lens
- **Engineer:** Writes the save-before-execute loop and the resume path with pending-intent
  re-execution, and tests that kill the process at every boundary and assert no duplicate effects.
- **Architect:** Chooses between snapshot checkpointing and a replay engine, and defines checkpoint
  schema versioning and the idempotency contract every tool must meet.
- **FDE:** Explains to a customer why their agent created two Jira tickets during a deploy, and
  delivers the checkpoint-plus-idempotency fix within their infrastructure constraints.

## Check yourself
**Q:** A worker dies right after calling an external API that creates a ticket but before recording
the result. Walk through how a durable design prevents a second ticket on resume.
**A:** Before calling the API the harness wrote a pending intent to the checkpoint containing a
deterministic idempotency key derived from the run id, step and call index. On restart a worker takes
the lease, loads the checkpoint, sees the intent without a result and re-executes it with the same
key. If the ticketing API supports idempotency keys, it returns the existing ticket; if not, the tool
adapter checks its own dedup table keyed by that value, or searches by an external reference it wrote
into the ticket, and returns the first result. The result is recorded, the intent cleared, and the
loop continues. Without the pre-execution intent, or with a random key, nothing links the two
attempts and a second ticket appears.

## Quiz
### Q: Why can a crash between executing a tool and recording its result not be fixed by checkpoint placement alone?
- [ ] Checkpoints are too slow to write synchronously
- [x] The external side effect and the local record cannot be made atomic, so the resume must re-execute with an idempotency key that makes the second attempt a no-op
- [ ] Models cannot be told that a tool was already executed
- [ ] Tool results are too large to store in a checkpoint
> No ordering of a remote call and a local write is atomic across the network boundary. Recording the
> intent with a deterministic key before executing, and making the tool idempotent, prevents duplicates.

### Q: What does event-sourced replay (the Temporal-style model) require of orchestration code?
- [ ] It must be written in a compiled language
- [ ] It must checkpoint explicitly after every step
- [x] It must be deterministic, with I/O in recorded activities and time or randomness taken only from the engine's recorded APIs, so replay follows the same path
- [ ] It must keep the whole transcript in memory
> Replay re-runs the code and substitutes recorded results; if the code could take a different path on
> replay, the substitution would be wrong. Determinism is the price of implicit checkpointing.

### Q: Which item is most often missing from a checkpoint and causes subtly wrong resumes?
- [ ] The system prompt text
- [x] Pending tool calls that were issued but not yet completed, together with their idempotency keys
- [ ] The user's display name
- [ ] The model's temperature setting
> Without recorded pending intents, a resume cannot tell whether an in-flight tool ran; it either
> repeats the side effect or loses it. Prompt text and settings are reconstructable from a version id.
