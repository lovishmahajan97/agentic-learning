---
id: workflow-orchestration
module: system-design
name: Workflow orchestration (Temporal, Prefect, Airflow)
kind: tool
difficulty: core
est_minutes: 17
status: reviewed
related:
  - reliability/durable-workflow-execution
  - agent-reliability/durable-execution-and-checkpointing
  - backend-fundamentals/background-jobs-durable-workers
  - backend-fundamentals/message-queues
  - data-architecture/ingestion-pipelines
  - system-design/agent-orchestration
  - system-design/saga-pattern-and-distributed-transactions
  - system-design/exactly-once-processing-limitations
sources:
  - Official docs: Temporal
  - Official docs: Apache Airflow
  - Official docs: Prefect
---

## TL;DR
Workflow orchestrators run multi-step processes reliably outside a single request: they persist
progress, retry steps, schedule work and show where each run is. Airflow schedules batch DAGs of data
tasks; Prefect orchestrates dynamic Python dataflows with less ceremony; Temporal provides durable
execution, replaying workflow code from an event history so long-running stateful processes survive
crashes. For AI systems: indexing pipelines fit Airflow or Prefect; long-lived agent runs and
approval flows fit Temporal-style engines.

## Why it exists
Any process with several steps that take time, can fail independently and have side effects needs the
same machinery: remember which steps completed, retry the ones that failed without repeating the ones
that succeeded, wait (for a timer, a human, an external callback) without holding a process open, run on
a schedule or on an event, and expose state so a human can see what happened. Every team that starts
with a cron job and a script ends up reinventing this, badly, one incident at a time.

The naive design is a background job with a loop and a database row for status. It works for one step.
With five steps it needs per-step status, partial retries, idempotency for every side effect, a way to
resume after a worker dies between steps, and a dashboard. The subtle failure is not a missing feature
but a wrong guarantee: a job retried after a crash re-executes the "send email" step because the crash
happened after the send and before the status write. Exactly-once is impossible across systems; what you
can have is a durable record of which steps were attempted plus idempotent steps, and that is precisely
what orchestrators provide.

AI systems make this acute. Ingestion pipelines are multi-stage, scheduled and expensive to redo
(parsing and embedding a million chunks). Agent runs take minutes to hours, wait for approvals, call
flaky external tools and must not repeat consequential actions. Both are workflow orchestration problems
with different shapes, which is why the three tools in this lesson coexist rather than compete.

## How it works
### Airflow: scheduled DAGs of tasks
Airflow (started at Airbnb, now an Apache project) models work as a directed acyclic graph of tasks
defined in Python. A scheduler evaluates DAGs against their schedule, creates task instances per run and
hands them to an executor (local, Celery, Kubernetes) that runs them on workers; a metadata database
records every task state. Tasks are meant to be idempotent, coarse units (run this SQL, launch this
Spark job, call this API) and exchange only small values through XCom, *because* the DAG is a scheduling
plan, not a data-flow program. Each DAG run is bound to a *data interval* (its logical date), and
well-written tasks read only that interval's data and overwrite only that interval's output. That
convention is what makes a retry or a backfill safe: re-running "2024-03-14" replaces that day's
partition instead of appending a duplicate. Retries, SLAs, backfills over past dates and a mature UI are
its strengths. Its shape fits periodic batch: nightly re-embedding, daily data-quality checks, hourly
connector syncs. It fits poorly when the graph depends on runtime data or when a run must wait on a
human for days.

### Prefect: dynamic Python dataflows
Prefect takes the opposite stance on structure: a flow is a Python function and tasks are decorated
functions called from it, so loops, conditionals and fan-out driven by runtime data are ordinary code
rather than a static graph. The orchestration layer (Prefect server or Cloud) tracks states, retries,
caching of task results, schedules and concurrency limits, while your own workers execute the code, a
hybrid model chosen *because* data and credentials then never leave your infrastructure. It suits the
many AI pipelines where the number of documents, the branches and the parameters are discovered as the
flow runs. It is still a pipeline tool: a flow run can be paused or suspended to wait for human input,
but there is no replayable event history. Recovery is by retrying tasks and by re-running the flow
function while skipping tasks whose results were persisted under a cache key; a suspended run resumes by
executing the flow from the top in this way. That works well when tasks are coarse and their results are
persisted, and poorly for a process with many small stateful steps that must survive anything for
weeks, which is not its design centre.

### Temporal: durable execution
Temporal (descended from Uber's Cadence) solves a different problem: making ordinary code durable. You
write a *workflow* function that orchestrates calls to *activities*, which are the functions that do side
effects: call an LLM, write a database row, send an email. The Temporal server records every event in the
workflow's life (started, activity scheduled, activity completed with its result, timer fired, signal
received) in an append-only history. Workers poll task queues and execute workflow code; when a worker
crashes, another worker picks up the workflow and *replays* its code against the recorded history.
Because completed activity results come from history instead of re-execution, the code deterministically
reaches the exact point where it stopped and continues. This is why workflow code must be deterministic:
no wall-clock reads, random numbers or direct I/O inside it, only activities, timers and the SDK's
side-effect APIs, *because* replay must reproduce the original decisions. Changing workflow code while
runs are in flight therefore needs versioning (patch markers in the code).

Activities get retry policies, timeouts (schedule-to-start, start-to-close) and heartbeats for long
tasks. Workflows can sleep for days with no process alive, receive *signals* (an approval arrived),
answer *queries* (which step are you on), start child workflows, and run indefinitely by
continuing-as-new to keep history bounded. The cost is the determinism discipline, a server to run or
buy, and keeping payloads small by passing large data by reference, because every activity input and
output lands in history.

### The same crash, three recovery models
Put a worker crash in the middle of step 3 of a five-step run, and the tools differ in what they know.
Airflow knows task instances: tasks 1–2 are `success`, task 3 is marked failed (or found as a zombie and
failed) and retried as a whole unit, so correctness rests on task 3 being idempotent for its interval.
Prefect knows task runs and persisted results: retrying the flow re-enters it from the top, steps 1–2
return cached results, step 3 runs again. Temporal knows every decision the workflow made: a new worker
replays the code, steps 1–2 return their recorded results instantly, and step 3's activity is retried
after its timeout. The finer the recorded state, the smaller the unit of redo, and the more discipline
the code must obey (idempotent tasks; cacheable tasks; deterministic workflow code respectively).

### Which fits an AI system where
- Re-chunk and re-embed a corpus nightly, with backfill over past days: **Airflow**.
- Parse, enrich and index whatever arrived in the last hour, branching per document type: **Prefect**
  (or Airflow with dynamic task mapping).
- An agent run that calls tools over an hour, pauses for a manager's approval until Monday, must never
  issue a refund twice, and must be inspectable mid-run: **Temporal**.
- A ten-second chain of three model calls inside a request: none of them; an in-process orchestrator is
  enough.

### What all three give you, and what none of them do
All three give persistence of step state, retries, scheduling, a UI and observability. None of them make
your steps idempotent for you, and none guarantee a side effect happens exactly once: a retry after a
crash between "effect done" and "result recorded" will re-run the step. Every activity or task that acts
on the outside world needs an idempotency key derived from the run and the step.

## Example
A Temporal Python workflow for document extraction with an LLM call. Activities carry the side effects
and retry policies, the workflow carries only deterministic orchestration, and the approval wait is a
durable signal rather than a blocked thread. `blob_store`, `db` and `llm_extract` are stand-ins for your
own clients.

```python
import asyncio
from datetime import timedelta
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

@activity.defn
async def extract_fields(doc_id: str) -> dict:
    text = await blob_store.read_text(doc_id)            # large payloads stay out of workflow history
    return await llm_extract(text)                        # non-deterministic work belongs in an activity

@activity.defn
async def write_record(doc_id: str, fields: dict) -> None:
    await db.upsert(key=f"extract:{doc_id}", value=fields)   # idempotent by key: safe to retry

@workflow.defn
class DocumentExtraction:
    def __init__(self) -> None:
        self.approved: bool | None = None

    @workflow.signal
    def approve(self, decision: bool) -> None:            # delivered durably whenever it arrives
        self.approved = decision

    @workflow.run
    async def run(self, doc_id: str) -> str:
        fields = await workflow.execute_activity(
            extract_fields, doc_id, start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=4, initial_interval=timedelta(seconds=5)))
        if fields.get("confidence", 1.0) < 0.8:
            try:   # durable wait: no worker is held; survives restarts and deploys
                await workflow.wait_condition(lambda: self.approved is not None, timeout=timedelta(days=3))
            except asyncio.TimeoutError:
                return "approval_timed_out"
            if not self.approved:
                return "rejected"
        await workflow.execute_activity(write_record, args=[doc_id, fields],
                                        start_to_close_timeout=timedelta(seconds=30))
        return "written"

# Worker(client, task_queue="extraction", workflows=[DocumentExtraction], activities=[extract_fields, write_record])
```

Walk one low-confidence document through it. The server's history for the run reads, simplified:
(1) workflow started with `doc_id`; (2) activity `extract_fields` scheduled; (3) activity started,
recorded with attempt 2 because the server retried a provider 503 per the retry policy (intermediate
attempts are not written as separate events); (4) activity completed with result
`{..., "confidence": 0.62}`; (5) a 3-day timer started for the wait. No worker holds anything now. A deploy restarts every worker overnight. The next morning a
reviewer sends the `approve(True)` signal, which is appended as event (6). A fresh worker replays `run`
from the top: `execute_activity(extract_fields)` is matched to events 2–4 and returns the recorded
fields without calling the LLM; `wait_condition` is matched to the timer; the signal handler applies
event (6), so the condition is now true; and the code reaches `write_record`, which is the first genuinely
new command. If someone had put `datetime.now()` in the workflow to stamp the record, the replay would
compute a different value than the original run, which is exactly the class of bug the determinism rule
and the SDK's `workflow.now()` exist to prevent.

## Trade-offs
- Airflow's static DAGs give predictable scheduling, backfills and a mature ecosystem, but once the graph
  depends on runtime data you are fighting the model; dynamic task mapping helps, Prefect removes the
  constraint.
- Prefect's plain-Python flows are fast to write and natural for data-dependent branching, but flows are
  expected to complete; a run that must wait weeks for an external event is not its design centre.
- Temporal's durable execution makes long-lived, stateful processes crash-proof and resumable, in
  exchange for the determinism discipline in workflow code, versioning for in-flight runs, and operating
  or buying a server.
- Storing every activity input and output in history is what enables replay, but it caps payload sizes;
  passing blobs by reference keeps histories small at the cost of a second store to manage.
- Any orchestrator adds an operational component and a learning curve; for a short chain inside a
  request, in-process orchestration with good logging wins on simplicity.

## Pitfalls
- Doing I/O, reading the clock or generating random values inside Temporal workflow code; the symptom is
  non-determinism errors on replay after a worker restart or a code deploy.
- Assuming the orchestrator makes side effects exactly-once; the symptom is duplicate emails or writes
  after a retry, fixed only by idempotency keys in the activity or task.
- Using Airflow for per-request or event-driven work; the symptom is noticeable scheduler latency
  between tasks (it was built for batch, not milliseconds) and DAG files sprouting conditionals to fake
  dynamism.
- Writing Airflow tasks that append rather than overwrite their interval's output; the symptom is
  doubled rows for exactly the days that were retried or backfilled.
- Passing megabytes of document text or embeddings through task results or workflow history; the symptom
  is slow UIs, size limits being hit, and failed runs on large documents.
- Deploying changed workflow code with runs in flight and no versioning; the symptom is those runs
  failing or taking a different path on replay than they did originally.

## When to use
Reach for a workflow orchestrator when a process has multiple steps with independent failure, runs
longer than a request, needs scheduling or backfill, or waits on humans or external systems. Pick
Airflow for scheduled batch with a fixed shape and a data-engineering team already running it; Prefect
when the pipeline's shape is decided at runtime and you want plain Python; Temporal when runs are
long-lived, stateful and must survive anything, which describes production agent runs and
approval-gated business processes. Measure run success rate, retries per step, time spent waiting versus
executing, and queue latency. In interviews, be able to explain deterministic replay in one paragraph,
say why exactly-once is still your job, and place each tool against an AI pipeline and an agent run.

## Role lens
- **Engineer:** Writes idempotent tasks and activities, keeps workflow code deterministic, sets retry
  policies and timeouts, and keeps large payloads in object storage rather than history.
- **Architect:** Chooses the engine per workload class, decides what state lives in history versus a
  database, and sets versioning policy and operational ownership of the orchestrator.
- **FDE:** Fits customer approval flows and nightly data syncs onto the engine the customer can operate,
  and explains replay and retries when they ask why a step ran twice.

## Check yourself
**Q:** An agent run calls an LLM activity, then a payment activity. A worker crashes immediately after
the payment succeeded but before the activity completion was recorded. What happens on Temporal, and
what must you have done to be safe?
**A:** Temporal replays the workflow on another worker from the event history. The LLM activity's result
is in history, so it is not called again. The payment activity has a scheduled event but no completion
event, so once its timeout elapses it is retried according to its retry policy, and the payment code
runs a second time. Temporal guarantees the workflow's progress is durable; it cannot know whether the
external side effect happened. Safety therefore depends on the payment activity being idempotent: it
must use an idempotency key derived from the workflow id and step so the payment provider deduplicates
the second call. This is the general rule for every orchestrator: durable progress plus idempotent steps
is the achievable form of exactly-once.

## Quiz
### Q: Why must Temporal workflow code be deterministic?
- [ ] Because the server executes workflow code and cannot run arbitrary Python
- [x] Because a workflow is recovered by replaying its code against recorded history, which only works if the code makes the same decisions each time
- [ ] Because activities cannot be retried otherwise
- [ ] Because determinism is required for the UI to display progress
> Durable execution rebuilds in-memory state by re-running the workflow function and feeding it recorded
> results. Any divergence, from clocks, randomness or I/O, breaks the match between code and history.

### Q: Which workload is the poorest fit for Apache Airflow?
- [ ] Nightly re-embedding of a document corpus with backfill
- [ ] Hourly data-quality checks on ingested tables
- [x] An agent run that pauses for a human approval that may take days
- [ ] A weekly report-generation pipeline
> Airflow is a scheduler for batch DAGs with a fixed shape. Long-lived, event-driven runs that wait on
> humans belong to durable execution engines such as Temporal.

### Q: What does a workflow orchestrator NOT provide, regardless of which one you choose?
- [ ] Persisting which steps have completed
- [ ] Retrying failed steps with backoff
- [x] Exactly-once execution of side effects against external systems
- [ ] A view of where each run currently is
> Orchestrators make progress durable, but a crash between performing a side effect and recording its
> completion leads to a retry. Idempotent steps remain the application's responsibility.
