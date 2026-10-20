---
id: state-persistence-and-conversation-recovery
module: agent-reliability
name: State persistence & conversation recovery
kind: pattern
difficulty: core
est_minutes: 13
related:
  - agent-reliability/durable-execution-and-checkpointing
  - agent-reliability/state-and-checkpoint-stores
  - agent-reliability/conversation-execution-identifiers
  - agents/agent-memory
  - agents/context-compaction-summarization
  - system-design/memory-architecture
  - fastapi-for-ai/cancellation-on-client-disconnect
---

## TL;DR
Conversation state (the transcript a user returns to) and execution state (one in-flight agent run)
have different lifetimes and must be persisted separately. Store the transcript as an append-only,
sequence-numbered event log that is the source of truth, and derive the model's context from it as a
view. Recovery then means reconnecting clients catch up from their last sequence number, interrupted
turns are repaired into a well-formed history, and the user never sees a dangling or duplicated turn.

## Why it exists
A multi-turn assistant lives for days or months; the HTTP request that carries one turn lives for
seconds, and the agent run inside it for minutes. Three things break when those timescales are
confused. First, the user comes back tomorrow: if the transcript lived in a process variable or a
client-side array, it is gone or inconsistent across devices. Second, the client disconnects
mid-stream (mobile network, closed tab, load-balancer timeout): if the run is tied to the request it
is cancelled or orphaned, and if it continues the client has no way to pick up the rest. Third, the
server dies mid-turn: the transcript now holds an assistant message that was never finished or a
tool call with no result, and the next model call rejects the malformed history or, worse, the user
stares at a turn that never completes.

The naive design keeps the message array in memory and re-sends it from the client on each request.
It works in demos. In production it loses history on restart, cannot resume streams, lets a retried
POST append the user's message twice, and makes the model's context the storage format, so any
compaction (summarizing old turns to fit the window) permanently destroys the original record.

Persistence needs an explicit model: what is the canonical record, what is derived from it, and what
state machine a turn goes through so recovery can reason about where it stopped.

## How it works
### Two kinds of state
- **Conversation state**: the ordered sequence of user messages, assistant messages, tool calls and
  tool results, plus metadata (title, participants, settings). Lifetime: the conversation. Owner: a
  conversation store keyed by conversation id.
- **Execution state**: the in-flight run producing one assistant turn: step index, budgets, pending
  tool calls, loop-guard counters, scratch data. Lifetime: the turn. Owner: the checkpoint store
  keyed by run id. When the turn completes, its outputs are appended to the conversation and the
  execution state can be archived.

### Append-only event log
Persist the conversation as events `(conversation_id, seq, type, payload, created_at)` with `seq`
increasing monotonically per conversation. Append-only because it is cheap to write, it makes "what
happened" auditable, it gives clients a cursor to resume from, and it never loses information to
compaction. The model's context is *built* from the log: select events, apply summaries, format as
messages for the provider. Because the view is derived, you can change the compaction strategy, swap
models, or debug by re-deriving any past context exactly.

### Turn state machine
`accepted → running → awaiting_tool | awaiting_approval → running → completed | failed |
interrupted | cancelled`. Persist the current state and the run id on the turn record so a
reconnecting client can decide what to render: a spinner (running), a prompt (awaiting approval),
the result (completed), or a retry affordance (failed or interrupted).

### Reconnect and catch-up
The client remembers the last `seq` it rendered. On reconnect it requests the events after that
sequence number; the server returns everything appended since and then continues streaming live
events over SSE or a websocket. Because tokens are streamed, buffer the in-progress assistant message
as deltas in a resumable stream keyed by run id, so a reconnect in the middle of a message replays the
deltas, not just completed events. Submission is idempotent: the client generates a turn id before
sending, and the server ignores a duplicate POST carrying the same id, so a retry after a timeout does
not create two turns.

### Repairing interrupted turns
On server restart, scan turns in `running` or `awaiting_tool` whose run lease has expired. Either
resume from the run's checkpoint (if durable execution is in place) or mark the turn `interrupted`,
append an event saying so, and make the log well-formed: every tool call event gets a matching result
event (synthesize `{"error": "interrupted"}` when the real one is missing) and no assistant message
is left half-written without an end marker. Model APIs reject a history in which a tool call has no
result, so an unrepaired log makes the conversation permanently unusable.

### Compaction and the window
When the derived context exceeds the window, summarize older events into a summary event, store it,
and build later contexts from summary plus recent events. The original events stay. Record which
summary version each turn used so behaviour can be reproduced later.

### Concurrency across devices
Allow one active turn per conversation: reject or queue a new submission while one is running, using
a conversation-level lock or an optimistic version check. Otherwise two runs interleave events and the
model sees a history that contradicts itself.

## Example
The store appends sequence-numbered events, serves catch-up by sequence number, dedupes submissions
on the client-generated turn id, repairs dangling tool calls after a crash, and derives the model
context from the log plus the latest summary.

```python
import json

class ConversationStore:
    def __init__(self):
        self.events, self.turns = {}, {}

    def append(self, cid, type_, payload):
        log = self.events.setdefault(cid, [])
        ev = {"seq": len(log) + 1, "type": type_, "payload": payload}
        log.append(ev)
        return ev

    def since(self, cid, after_seq):                     # reconnect catch-up
        return [e for e in self.events.get(cid, []) if e["seq"] > after_seq]

    def submit(self, cid, turn_id, text):                # idempotent on turn_id
        if turn_id in self.turns:
            return self.turns[turn_id]
        self.append(cid, "user", {"text": text, "turn_id": turn_id})
        self.turns[turn_id] = {"state": "accepted", "run_id": None}
        return self.turns[turn_id]

    def repair(self, cid):                               # after a crash
        log = self.events.get(cid, [])
        calls = {e["payload"]["id"] for e in log if e["type"] == "tool_call"}
        results = {e["payload"]["id"] for e in log if e["type"] == "tool_result"}
        for call_id in calls - results:
            self.append(cid, "tool_result", {"id": call_id, "error": "interrupted"})
        return len(calls - results)

def build_context(store, cid, recent=40):
    """Derive the model view: latest summary (if any) plus the events after it."""
    log = store.events.get(cid, [])
    summary = next((e for e in reversed(log) if e["type"] == "summary"), None)
    tail = [e for e in log if summary is None or e["seq"] > summary["seq"]][-recent:]
    msgs = [{"role": "system", "content": summary["payload"]["text"]}] if summary else []
    # mapping event types to the provider's message format is omitted for brevity
    return msgs + [{"role": e["type"], "content": json.dumps(e["payload"])} for e in tail]
```

## Trade-offs
- An append-only log with derived views preserves every detail and enables resumable streams, but
  costs more storage and a derivation step per model call than storing the model-ready array directly.
- Continuing a run after the client disconnects delivers results users would otherwise lose, but
  spends tokens on turns nobody may read; cancel on disconnect for cheap chat, continue for expensive
  agent tasks.
- Marking interrupted turns failed with a retry button is simple and honest, while resuming from
  checkpoints is seamless but requires the full durable-execution machinery; choose by run cost and
  side effects.
- Locking a conversation to one active turn prevents interleaving, but blocks eager users who send a
  follow-up mid-run; queueing the follow-up as the next turn keeps both properties.

## Pitfalls
- Using the model's context array as the storage format, so compaction erases history; the symptom
  is users asking "what did I say earlier" and the assistant having no record.
- Retried POSTs without a client-generated turn id; the symptom is duplicate user messages and two
  concurrent runs in one conversation after a timeout.
- Leaving a tool call without a result after a crash; the symptom is every later request on that
  conversation failing with an API error about unmatched tool calls.
- Replaying only completed events on reconnect; the symptom is the client missing the first half of
  the assistant's answer and showing a message that starts mid-sentence.
- Keeping the turn state only in memory; the symptom is spinners that never stop after a deploy, for
  turns no worker is running.

## When to use
Any assistant used more than once by the same person needs the conversation store; anything that
streams or runs longer than a few seconds per turn needs the turn state machine and the catch-up
endpoint; anything with tools needs the repair step. Decide early whether a turn survives client
disconnect, because it shapes the whole API. Measure reconnect success rate, the share of turns
ending as interrupted, malformed-history errors from the model API (which should be zero), and the
duplicate-turn rate. In interviews, be ready to explain why the transcript and the model context are
different objects, and what you do about a tool call whose result never arrived.

## Role lens
- **Engineer:** Builds the event log, the catch-up-by-sequence endpoint, resumable token streams and
  the crash-repair job that closes dangling tool calls.
- **Architect:** Defines the conversation and turn schemas, the lifetime boundary between
  conversation and execution state, and the disconnect policy for each product surface.
- **FDE:** Maps the customer's existing session and audit requirements onto the event log, and
  diagnoses "my chat lost its history after your deploy" tickets from the sequence numbers.

## Check yourself
**Q:** A user on a flaky mobile connection sends a message, the agent runs for ninety seconds with
three tool calls, and the connection drops at second forty. Describe what happens and how the user
still gets the answer.
**A:** The POST carried a client-generated turn id, so the server accepted it once and started a run
with its own run id; the turn record is persisted as running. Because the run is decoupled from the
request, the disconnect does not cancel it; the harness keeps appending tool call, tool result and
assistant delta events to the conversation log with increasing sequence numbers. When the client
reconnects it sends the last sequence it rendered; the server returns the missed events, replays the
buffered deltas of the in-progress message, and continues streaming live. If the client never
returns, the completed answer waits in the log. Had the server died instead, the repair job would
close any dangling tool call and mark the turn interrupted with a retry affordance.

## Quiz
### Q: Why should the model's context be derived from the stored transcript rather than being the stored transcript?
- [ ] Model contexts cannot be serialized to JSON
- [x] Compaction and formatting are lossy and provider-specific, so storing the raw event log keeps the full record and lets the view change independently
- [ ] Storing the context would violate provider terms of service
- [ ] Derived views are always faster to query
> The context is a lossy projection: summaries drop detail and formats differ by provider. Keeping the
> append-only log as the source of truth preserves history and the ability to re-derive any past context.

### Q: A worker crashes after the model emitted a tool call and before the tool result was stored. What must happen before the conversation can continue?
- [ ] Delete the whole conversation and ask the user to start over
- [ ] Re-send the same messages and hope the model ignores the gap
- [x] Append a synthesized tool result (for example an interrupted error) or resume and complete the call, so every tool call has a matching result
- [ ] Switch to a model that accepts incomplete histories
> Model APIs require each tool call to be paired with a result; an unmatched call makes the history
> invalid. Repairing or completing the pair restores a well-formed transcript without losing earlier turns.

### Q: What makes a message submission safe to retry after a network timeout?
- [ ] The server deduplicates by comparing message text
- [x] The client attaches a generated turn id and the server treats a repeated id as the same submission
- [ ] The server rejects any message within five seconds of the previous one
- [ ] The client waits for the run to finish before retrying
> A client-generated id lets the server recognise the retry as the same turn regardless of timing or
> text, which is the standard idempotency approach. Text comparison fails for legitimately repeated messages.
