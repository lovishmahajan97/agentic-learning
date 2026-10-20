---
id: durable-workflow-execution
module: reliability
name: Durable workflow execution
kind: pattern
difficulty: advanced
est_minutes: 16
status: fact-checked
related:
  - agent-reliability/durable-execution-and-checkpointing
  - agent-reliability/state-and-checkpoint-stores
  - agent-reliability/idempotent-tool-execution
  - system-design/workflow-orchestration
  - system-design/saga-pattern-and-distributed-transactions
  - system-design/exactly-once-processing-limitations
  - backend-fundamentals/background-jobs-durable-workers
  - reliability/fault-tolerance
sources:
  - Official docs: Temporal
  - Garcia-Molina & Salem, 1987 — Sagas
  - Helland, 2007 — Life beyond Distributed Transactions
---

## TL;DR
Durable workflow execution makes a long, multi-step process survive crashes, deploys and
days-long waits by persisting its progress as an append-only event history and replaying that history
to resume exactly where it stopped. The orchestration code must be deterministic so replay is faithful;
every side effect lives in a retried, idempotent *activity*. It replaces hand-built state machines in
database columns and cron jobs, and it is the natural runtime for agent loops whose steps are slow,
flaky and expensive to redo.

## Why it exists
Real processes span time and systems: draft a reply with a model, wait for a human to approve, send
it, poll a vendor for confirmation, retry tomorrow if the vendor is down. A worker running that as one
function dies halfway during a deploy, and the process is simply lost: the draft was made and paid
for, the approval request was sent, nobody knows which step completed.

The naive fix is a `status` column plus a cron job that scans for stuck rows and a retry loop around
each step. Every team builds this once, and it grows the same bugs: steps that run twice because the
status update raced with the crash, timers reimplemented as polling, "waiting for approval" rows that
nobody sweeps, a state machine spread across three services with no single place to see where a
process is, and no way to change the logic while thousands of processes are mid-flight. Message
queues help with retries but not with the orchestration: a queue cannot express "wait 48 hours, then
if no signal arrived, escalate".

Agentic systems make this acute. An agent run is a loop of model calls and tool calls, each of which
can take seconds to minutes, fail transiently, and cost real money. Re-running the whole loop after a
crash duplicates tool side effects and token spend; abandoning it loses the user's work. Durable
execution exists so that a crash at step 14 of 20 resumes at step 14 with steps 1 to 13 already
decided, without re-calling the model for any of them.

## How it works
### The core idea: event sourcing applied to a program's execution
A durable workflow engine (Temporal and its predecessor Cadence, AWS Step Functions, Azure Durable
Functions, Restate, Inngest, DBOS and others differ in surface but share this model) records every
decision a workflow makes as an event: "activity X scheduled", "activity X completed with result R",
"timer T started", "signal S received". The history lives in a durable store. Workflow code never
holds state that matters only in memory; its memory is reconstructible from the history.

### Replay
When a worker crashes or is redeployed, another worker picks up the workflow by re-executing its code
from the beginning *against the recorded history*. Each time the code reaches a step whose result is
already in the history, the engine returns the recorded result instead of running the step again.
The code therefore reaches the same point it was at, with identical local variables, and continues
with the first step that has no recorded outcome. This is why workflow code must be
**deterministic**: no wall-clock reads, random numbers, direct network or database calls, or
iteration over unordered maps inside the workflow function, *because* a different decision during
replay would diverge from history and the engine would have to fail the workflow. Engines provide
deterministic substitutes (`workflow.now()`, seeded random, versioned patches).

### Activities: where the real world lives
Anything non-deterministic or side-effecting, including every LLM call, tool call, HTTP request and
database write, is an **activity**. Activities run outside the replay machinery, have timeouts and a
retry policy, and their *results* are what gets recorded. The engine guarantees an activity is
retried until it succeeds or the policy gives up, and that its result is never recomputed on replay.
What the engine cannot guarantee is that an activity ran exactly once: a worker can complete the side
effect and crash before reporting, and the retry runs it again. Activities must therefore be
**idempotent**, with a key derived from the workflow and step identity, which is the same
exactly-once limitation that applies to every distributed system.

### Durable timers, signals and queries
`sleep(30 days)` in a workflow is an event, not a blocked thread: the worker is free, and any worker
can resume the workflow when the timer fires, across deploys. **Signals** deliver external input, such
as a human approval or a webhook, into a running workflow; **queries** read its current state without
affecting history. Together they replace the cron-plus-polling pattern with code that reads as a
straight-line program.

### Task queues and workers
Workflows and activities are dispatched through named task queues; workers poll them. This gives
natural bulkheads (GPU-bound activities on one queue, email on another), per-queue concurrency limits
that act as backpressure, and **heartbeats** for long activities so a dead worker is detected and its
activity rescheduled rather than waiting for the full timeout.

### Living with long-running code
Because deploying new workflow code while old histories exist can break replay, engines offer
**versioning** primitives: a patch marker that lets new code take the old branch for histories
recorded before the change. Very long or unbounded loops, like an agent that may take thousands of
steps, use **continue-as-new**, which closes the current history and starts a fresh one carrying
forward only the needed state, *because* replaying a history of millions of events is slow and
memory-hungry.

### Compared with the alternatives
A saga implemented by hand gives compensation but not resumption; a queue gives retries but not
orchestration; a declarative state-machine service (Step Functions style) gives durability with
JSON-defined states, which is excellent for fixed pipelines and awkward for branching agent logic.
The code-first engines trade that simplicity for ordinary programs with determinism rules.

## Example
A Temporal-style workflow (Python SDK conventions; check names against the current docs) in which the
LLM call and the send are activities, and the approval wait is a durable timer plus signal. A crash
after `draft_reply` completes resumes at the wait without calling the model again.

```python
import asyncio
from datetime import timedelta
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

@activity.defn
async def draft_reply(ticket_id: str) -> str:
    return await llm.complete(f"Draft a reply for ticket {ticket_id}")       # your model client

@activity.defn
async def send_reply(ticket_id: str, text: str) -> None:
    # idempotency key = workflow identity + step, so an activity retry cannot send twice
    await mailer.send(ticket_id, text, idempotency_key=f"reply:{ticket_id}")

@workflow.defn
class TicketReplyWorkflow:
    def __init__(self) -> None:
        self.approved: bool | None = None

    @workflow.signal
    def approve(self, ok: bool) -> None:          # delivered by the review UI
        self.approved = ok

    @workflow.run
    async def run(self, ticket_id: str) -> str:
        draft = await workflow.execute_activity(
            draft_reply, ticket_id,
            start_to_close_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)),
        )
        try:   # durable wait: no worker is blocked, and it survives restarts and deploys
            await workflow.wait_condition(lambda: self.approved is not None, timeout=timedelta(days=2))
        except asyncio.TimeoutError:
            return "expired"
        if not self.approved:
            return "rejected"
        await workflow.execute_activity(send_reply, args=[ticket_id, draft],
                                        start_to_close_timeout=timedelta(seconds=30))
        return "sent"
```

Notice what is *not* in the workflow: no `datetime.now()`, no HTTP, no database. Those would be
re-executed on replay. The model is called inside an activity precisely because it is
non-deterministic; its recorded result is what the replayed workflow sees.

## Trade-offs
- Crash-proof resumption and readable straight-line orchestration, in exchange for determinism rules
  that are easy to violate; the trade pays off once a process has more than two steps or waits on
  humans or timers.
- Every step's inputs and outputs are persisted, giving a complete audit trail, but large payloads
  (full documents, long transcripts) bloat history; pass references to blob storage instead.
- Activity retries lift success rates, yet retrying an LLM activity re-spends tokens and may produce
  a different answer; make the activity idempotent on its own cache or accept the cost knowingly.
- Workflow versioning lets you change logic under running instances, but each patch is permanent
  complexity until old histories drain; short-lived workflows can skip it, multi-week ones cannot.
- Declarative state-machine services need no determinism discipline and integrate natively with cloud
  services; they become painful when the branching is data-dependent, as in agent loops.

## Pitfalls
- Calling the model or a tool directly from workflow code; it works in tests and then replays into a
  second real call, visible as duplicated emails or doubled token bills after a worker restart.
- Non-deterministic workflow code (`time.time()`, `uuid4()`, dict iteration order, reading config at
  runtime), which surfaces as workflows failing with non-determinism errors only after a deploy.
- Activities without idempotency keys, so an at-least-once retry after a crash performs the side
  effect twice; the symptom is duplicates that correlate with deploys and worker restarts.
- Unbounded agent loops in one history, discovered as slowly growing replay latency and memory until
  the workflow stalls; use continue-as-new on a step budget.
- Missing heartbeats on long activities, so a dead worker's multi-minute inference is not rescheduled
  until the full timeout expires, inflating latency on every infrastructure hiccup.

## When to use
Reach for durable execution when a process spans more than one side-effecting step, waits on timers
or humans, or must survive deploys mid-flight: approvals, multi-step agent tasks, document pipelines,
onboarding flows, anything the business would describe as "a case". Do not use it for a single
request-response call or for pure data transformations where a queue and an idempotent consumer are
enough. Measure workflow completion rate, time spent in each activity, retry counts per activity type
and history size, and alert on non-determinism errors after deploys. In day-to-day work the pattern
shows up as the discipline "model and tools in activities, decisions in the workflow". The
agent-reliability module covers checkpoint stores and agent-specific recovery; this lesson is the
general runtime they rest on. In interviews, explaining replay and why determinism follows from it
is the expected depth.

## Role lens
- **Engineer:** Writes activities for every model and tool call with timeouts, retries and idempotency
  keys, keeps workflow code deterministic, and applies continue-as-new on long agent loops.
- **Architect:** Chooses between a code-first engine and a declarative state machine, defines task-queue
  bulkheads and concurrency limits, and sets the payload and history-size policies.
- **FDE:** Maps a customer's multi-day approval and escalation processes onto workflows with signals,
  and shows the history as the audit trail their compliance team asked for.

## Check yourself
**Q:** A team runs a 30-step research agent as one function inside a job queue with retries. After a
deploy, users report duplicated documents and bills roughly double for the hour. Diagnose it and
explain how durable execution changes the picture.
**A:** The queue restarted the job from step one after the deploy killed the worker mid-run. Every
model call and tool call before the crash point was repeated, so tokens were spent twice and
non-idempotent tool calls such as "create document" ran again. Retries at the job level are
at-least-once for the whole job, which is the wrong granularity. Under durable execution each of the
30 steps is an activity whose result is recorded in the workflow history as it completes. On restart
the workflow replays: steps one to the crash point return their recorded results without executing,
so no tokens are re-spent and no tools re-run, and only the interrupted step is retried. That step
still needs an idempotency key, because the crash could have happened after the side effect and
before the result was recorded. The team should also move the loop under a step budget with
continue-as-new and add heartbeats to long model calls.

## Quiz
### Q: Why must durable workflow code be deterministic?
- [ ] Because the engine executes it on a single thread for performance
- [x] Because recovery works by re-executing the code against the recorded history, and any divergence makes replay fail
- [ ] Because non-deterministic code cannot be serialised to the database
- [ ] Because activities require deterministic inputs to be retried
> Replay reconstructs state by running the code again and substituting recorded results. If the code
> takes a different branch the second time, the history no longer matches and the engine cannot continue.

### Q: Where should an LLM call live in a durable workflow, and why?
- [ ] In the workflow function, so its result is recorded in history
- [ ] In a signal handler, so it runs only when triggered
- [x] In an activity, because it is non-deterministic and side-effecting; its recorded result is what replay sees
- [ ] In a query handler, so it does not affect workflow state
> Activities run outside replay, with timeouts and retries, and only their results enter the history.
> Calling the model in workflow code would re-invoke it on every replay and break determinism.

### Q: An activity performs a payment and the worker crashes before reporting completion. What does the engine do, and what must the activity therefore guarantee?
- [ ] The engine detects the completed payment and records it automatically
- [ ] The engine marks the workflow failed to avoid any risk of duplication
- [x] The engine retries the activity, so the activity must be idempotent to avoid charging twice
- [ ] The engine rolls back the payment using the recorded history
> Durable execution gives at-least-once activity execution; the engine cannot see external side effects.
> Exactly-once effects come from idempotency keys the receiver deduplicates on, not from the engine.
