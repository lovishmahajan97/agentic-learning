---
id: structured-event-streams
module: agent-reliability
name: Structured event streams
kind: pattern
difficulty: core
est_minutes: 14
related:
  - agent-reliability/durable-execution-and-checkpointing
  - agent-reliability/state-persistence-and-conversation-recovery
  - agent-reliability/audit-logs-execution-traces
  - agent-reliability/deterministic-replay-and-reproducibility
  - agent-reliability/conversation-execution-identifiers
  - serving-inference/streaming
  - fastapi-for-ai/server-sent-events
  - system-design/event-driven-architecture
sources:
  - Fowler, 2005 — Event Sourcing
  - Official docs: Anthropic (streaming Messages)
  - Official docs: LangGraph (streaming)
---

## TL;DR
An agent run is not a function call that returns a string; it is a sequence of things that happened:
model requests, token deltas, tool calls, permission decisions, results, retries, completions. Emitting
those as one ordered, typed, versioned stream of events, and deriving the UI, the persisted state, the
trace and the eval trajectory from that stream, replaces four drifting ad hoc formats with one source of
truth that can be replayed.

## Why it exists
The first agent you build returns a final answer. The second needs to show progress in a UI, because a
90-second silent wait reads as a hang. The third needs to survive a worker restart, which means someone
has to reconstruct where the run was. The fourth needs traces for debugging and trajectories for evals.
Each consumer, built separately, invents its own format: the UI parses print statements, the checkpoint
serializes a Python object, the tracer wraps functions, the eval harness scrapes logs. They disagree about
what happened, and none of them can answer "what did the model see when it chose that tool" without a
second instrumentation pass.

The deeper problem is that an agent's state is not a value, it is a history. The next prompt is built from
everything that happened before, so the only faithful representation of the run is the ordered list of
what happened. Event sourcing says exactly this for business systems: store the facts, derive the views.
Agent runtimes rediscovered it because every feature they needed, live streaming, resumption, audit,
replay and evaluation, is a different projection of the same facts.

The naive alternative, logging free text, fails on three counts: it cannot be parsed reliably, it carries
no ordering guarantee across processes, and the token stream from the model, which is the one thing the
UI needs most, does not fit in a log line at all.

## How it works
### The envelope
Every event carries the same header, and the header is what makes the stream usable by machines:

```
run_id      which run this belongs to
seq         monotonic integer per run, assigned by the single writer
ts          wall-clock time, for humans; never used for ordering
type        discriminator, e.g. "tool.call.started"
version     schema version of this type's payload
step_id     the agent step (plan -> act -> observe cycle) this belongs to
parent_id   optional, for nesting (sub-agent runs, parallel tool calls)
payload     type-specific body
```

`seq` exists *because* timestamps are not a total order: two processes with skewed clocks, or two
parallel tool calls, produce ties and inversions. One writer per run assigns `seq`, consumers sort by it,
and `(run_id, seq)` is the idempotency key that lets at-least-once delivery dedupe.

### The vocabulary
A workable minimum, named as dotted lifecycle pairs:

- `run.started` / `run.completed` / `run.failed` / `run.cancelled`
- `llm.request` (model id, prompt reference, parameters) and `llm.response` (content, tool calls, usage)
- `llm.delta` for streamed tokens: high volume, usually not persisted
- `tool.call.requested` (name, arguments), `tool.call.permission` (allow/ask/deny, policy version),
  `tool.call.started`, `tool.call.result` (ok, error class, duration, result reference)
- `step.completed` with the step's summary
- `input.requested` / `input.received` for human-in-the-loop pauses

Payloads reference large blobs (full prompts, tool outputs) by content hash or storage key rather than
inlining them, *because* the stream must stay cheap to transport, cheap to index, and redactable: PII
lives in the blob store with its own retention, while the event history stays intact.

### State is a fold
The run's state at any point is `reduce(apply, events[:n], initial)`. `apply` is a pure function that,
for example, appends an `llm.response` to the message list, records a pending tool call on
`tool.call.requested`, and resolves it on `tool.call.result`. Resumption after a crash is replaying the
persisted events through `apply` and continuing from the resulting state. A checkpoint is just a cached
fold at some `seq`, kept so that replay does not start from zero.

### Projections
- **UI:** subscribe, render `llm.delta` as streaming text and `tool.call.*` as status rows. Transport is
  SSE or WebSocket; the client sends its last `seq` on reconnect and receives only what it missed.
- **Trace:** map `step` and `tool.call` pairs to spans with durations.
- **Audit:** filter to `tool.call.permission` and `tool.call.result`, append to the immutable store.
- **Eval trajectory:** the full list, minus deltas.

### Delivery and backpressure
The agent process is the single writer. It appends to a durable log (a database table, a Redis stream or
a Kafka topic keyed by `run_id`) and fans out to live subscribers. A slow UI subscriber must never stall
the agent, so live fan-out is buffered or dropped for deltas, while state-changing events are always
durable before the agent proceeds. The rule "durable first, then act" is what makes the stream
trustworthy: if a tool call ran, there is an event saying it was requested.

### Versioning
Event types evolve. The `version` field lets old runs replay through a reader that knows both shapes, and
a new consumer can refuse versions it does not understand instead of silently misparsing them.

## Example
A minimal typed stream: a discriminated union of events, a single-writer emitter, a pure fold that
rebuilds state, and SSE framing for the UI. The same list feeds persistence, replay and evaluation.

```python
from typing import Literal, Union
from pydantic import BaseModel
import json, itertools

class Base(BaseModel):
    run_id: str
    seq: int
    version: int = 1

class LLMResponse(Base):
    type: Literal["llm.response"] = "llm.response"
    text: str = ""
    tool_calls: list[dict] = []

class ToolRequested(Base):
    type: Literal["tool.call.requested"] = "tool.call.requested"
    call_id: str; name: str; arguments: dict

class ToolResult(Base):
    type: Literal["tool.call.result"] = "tool.call.result"
    call_id: str; ok: bool; error: str | None = None; result_ref: str | None = None

class RunCompleted(Base):
    type: Literal["run.completed"] = "run.completed"
    answer: str

Event = Union[LLMResponse, ToolRequested, ToolResult, RunCompleted]

def emit(run_id: str, counter: itertools.count, cls, **payload) -> Event:
    return cls(run_id=run_id, seq=next(counter), **payload)        # single writer assigns seq

def apply(state: dict, ev: Event) -> dict:
    """Pure fold: the run's state is reduce(apply, events)."""
    if ev.type == "llm.response":
        state["messages"].append({"role": "assistant", "content": ev.text, "tool_calls": ev.tool_calls})
    elif ev.type == "tool.call.requested":
        state["pending"][ev.call_id] = ev.name
    elif ev.type == "tool.call.result":
        state["pending"].pop(ev.call_id, None)
        state["messages"].append({"role": "tool", "call_id": ev.call_id, "ok": ev.ok, "ref": ev.result_ref})
    elif ev.type == "run.completed":
        state["done"], state["answer"] = True, ev.answer
    return state

def rebuild(events: list[Event]) -> dict:
    state = {"messages": [], "pending": {}, "done": False}
    for ev in sorted(events, key=lambda e: e.seq):                   # order by seq, never by timestamp
        state = apply(state, ev)
    return state

def to_sse(ev: Event) -> str:                     # UI transport; client resends last seq on reconnect
    return f"id: {ev.seq}\nevent: {ev.type}\ndata: {json.dumps(ev.model_dump())}\n\n"
```

## Trade-offs
- One canonical stream removes format drift between UI, checkpoint, trace and evals, but it forces every
  consumer through one schema and one team becomes the owner of that schema's evolution.
- Persisting fine-grained token deltas gives exact replay of what the user saw, at a storage and write-rate
  cost that is usually not worth it; persist the assembled `llm.response` and keep deltas live-only unless
  a product requirement says otherwise.
- Referencing payloads by hash keeps events small and redactable but adds a second store and a second
  failure mode; for small internal tools inlining payloads is simpler until retention or PII rules bite.
- Durable-before-act guarantees the history is trustworthy but adds a write on the critical path of every
  step; for sub-second interactive agents, batch the writes per step rather than per event.

## Pitfalls
- Ordering by timestamp instead of a per-run sequence; the symptom is parallel tool results that appear
  before their requests in the UI or the replay.
- Letting multiple processes write to the same run's stream; the symptom is duplicate or interleaved
  `seq` values and folds that produce different state on different replays.
- Inlining full tool outputs into events; the symptom is a stream table that grows by megabytes per run
  and a redaction request that requires rewriting history.
- Changing an event's payload shape without bumping `version`; the symptom is replay of last month's runs
  crashing in the fold.
- Blocking the agent on a slow websocket consumer; the symptom is step latency that tracks the user's
  network rather than the model's.
- Emitting events after the side effect instead of before; the symptom is a crash between the tool
  running and the event being written, leaving a run that resumes and runs the tool again.

## When to use
Adopt a structured stream as soon as more than one consumer needs to know what the agent is doing, which
in practice is the first time you add a progress UI or a resume-after-crash requirement. Keep it even for
batch agents, because the eval trajectory and the audit log come for free. Measure events per run, bytes
per run, fan-out lag to the slowest subscriber, and the fraction of runs that rebuild to identical state
on replay. In daily work the stream is where you look first when a run misbehaves. In interviews, being
able to say "state is a fold over events, the UI and the checkpoint are projections" is the signal that
you have built an agent that survived contact with production.

## Role lens
- **Engineer:** Defines the event types, assigns sequence numbers from a single writer, writes the fold,
  and wires SSE so the UI reconnects by last seq.
- **Architect:** Chooses the durable log (database table, Redis stream, Kafka), the retention and
  redaction policy, and the schema versioning rule all teams follow.
- **FDE:** Streams the same events into the customer's existing observability and ticketing systems so
  their operators see agent activity in tools they already trust.

## Check yourself
**Q:** Your agent runs two tool calls in parallel, and the UI occasionally shows a result row before its
"calling tool" row. A worker crash during that window also caused one tool to run twice on resume. Explain
both symptoms with one root cause and fix it.
**A:** Both come from ordering by wall-clock time and from writing events after the effect. Parallel calls
complete in any order, and if each thread stamps its own timestamp the result event can carry an earlier
`ts` than the request event emitted by another thread; the UI sorting by `ts` shows the inversion. The
duplicate execution comes from emitting `tool.call.requested` only after the call returns: a crash between
execution and the write leaves no record, so the fold on resume sees no pending call and issues it again.
The fix is a single writer per run that assigns a monotonic `seq`, consumers that sort by `seq`, and a
durable `tool.call.requested` event written before the adapter runs, paired with an idempotency key so
that even a retry after a crash cannot repeat the effect.

## Quiz
### Q: Why does each event carry a per-run sequence number in addition to a timestamp?
- [ ] Timestamps are too large to store efficiently
- [x] Timestamps do not give a total order across processes or parallel calls, while a single-writer sequence does and also serves as a dedup key
- [ ] Sequence numbers encrypt the event for the audit log
- [ ] Timestamps are only needed for the UI, so they are optional
> Clock skew and concurrency produce ties and inversions; a monotonic sequence from one writer is what
> replay and reconnect logic can rely on.

### Q: What is the relationship between the event stream and a checkpoint in this design?
- [ ] The checkpoint is the source of truth and events are derived from it
- [ ] They are independent; checkpoints store state while events store logs
- [x] A checkpoint is a cached fold of the events up to some sequence, kept so replay does not start from zero
- [ ] Checkpoints replace events once a run completes
> State is reduce(apply, events); a checkpoint memoizes that reduction at a point. It speeds resumption
> but the events remain the truth.

### Q: Why store large tool outputs by reference rather than inline in the event payload?
- [ ] Because JSON cannot represent strings longer than 64 KB
- [ ] Because the model cannot read inline payloads
- [x] To keep the stream small and indexable and to let PII be redacted or expired in the blob store without rewriting event history
- [ ] Because references are required for SSE transport
> Events are the permanent history; payloads carry the bulk and the sensitive content. Separating them
> keeps transport cheap and retention policies independent.
