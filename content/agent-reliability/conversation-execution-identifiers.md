---
id: conversation-execution-identifiers
module: agent-reliability
name: Conversation / execution identifiers
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
related:
  - agent-reliability/state-persistence-and-conversation-recovery
  - agent-reliability/idempotent-tool-execution
  - agent-reliability/durable-execution-and-checkpointing
  - agent-reliability/audit-logs-execution-traces
  - observability/distributed-tracing-across-services
  - agent-runtime-architecture/multi-tenant-state-isolation
  - fastapi-for-ai/idempotency-keys
sources:
  - IETF RFC 9562 — Universally Unique IDentifiers (UUIDs)
  - Official docs: OpenTelemetry (context propagation)
---

## TL;DR
An agent system needs a hierarchy of identifiers, not one "session id": tenant and user for scope,
conversation id for the long-lived transcript, turn id for one user request, run id for one execution
attempt of that turn, step and tool-call ids within it, plus trace ids for cross-service correlation.
Each id answers a different question (resume what, dedupe what, bill whom, trace which), and
conflating them is why retries double-act and logs cannot be joined.

## Why it exists
Imagine debugging "the agent created two tickets" with a single session id. The logs show two create
calls in session S. Were they two user requests? One request retried by the client? One run resumed
after a crash? Two parallel tool calls? Without ids that distinguish those cases the logs cannot tell
you, and neither can your dedup logic, because dedup needs a key that is the same across a retry and
different across a genuine second request.

Each capability of a reliable runtime relies on a specific identity. Resuming requires knowing which
execution to load; idempotent tools require a key stable across retries; billing and quotas require
the tenant; reconnecting clients require a conversation cursor; distributed tracing requires an id
that crosses service boundaries; evaluation requires addressing "step 7 of run X" repeatably. These
have different lifetimes: a conversation spans days, a turn spans one request, a run spans one attempt
at that request (a failed turn retried gets a new run under the same turn), a step spans one model
call.

The naive design uses the model provider's conversation or thread id, or a random id minted per
request. Provider ids tie your data model to a vendor and know nothing about retries or runs;
per-request random ids cannot dedupe because a retry mints a new one. The fix is to decide, per id,
who mints it, when, and what it is stable across.

## How it works
### The hierarchy
```
tenant_id — user_id — conversation_id — turn_id — run_id — step — tool_call_id
                        (transcript)   (request)  (attempt)  (model call)  (one call)
```
- **tenant_id / user_id**: the scope for isolation, quotas, billing and deletion. Present on every
  record and every log line.
- **conversation_id** (thread id): the unit of memory. Keys the event log and the sequence numbers
  clients resume from.
- **turn_id**: one user submission. Minted by the *client* before sending, because then a retried
  POST carries the same id and the server can recognise it. Stable across network retries.
- **run_id** (execution id): one attempt to produce the turn's response. Minted by the server when
  work starts. A crash-resume continues the same run; a user-initiated "regenerate" or an automatic
  retry after failure starts a new run under the same turn with `attempt = n + 1`. Keys checkpoints
  and leases.
- **parent_run_id**: for sub-agents. A child run gets its own run id (its own checkpoints, budget
  and lease) plus a pointer to the parent run and the parent's tool call that spawned it. Because
  then a crash-resume of the parent can find the child that already exists instead of spawning a
  second one, and the trace shows the delegation tree rather than unrelated runs.
- **step**: the index of the model call within the run; with run_id it addresses a checkpoint.
- **tool_call_id**: assigned by the model API to each requested call (both the Claude and OpenAI
  APIs include one) and echoed back on the result so the API can pair them. It is an opaque,
  vendor-formatted string whose only documented job is pairing within the conversation, and it is
  stable across a resume only if the response that carried it was persisted; scope it with run_id
  and step rather than relying on it alone for global uniqueness.
- **idempotency_key**: derived rather than minted, for example `f"{run_id}:{step}:{call_index}"`
  hashed, so a resumed step regenerates the same key.
- **trace_id / span_id**: OpenTelemetry ids propagated across services (gateway, agent, tool
  services, model proxy) so one trace shows the whole turn. A run usually maps to one trace, with
  each model call and tool call a span. Store trace_id on the run record so a support ticket leads to
  the trace.
- **provider request id**: returned by the model API per call; log it, because it is what the vendor
  needs when you open a support case.

### Generation
Random UUIDv4 is fine for uniqueness; time-ordered ids (UUIDv7 as standardised in RFC 9562, or ULID)
sort by creation time and keep B-tree inserts sequential, which matters for the high-write events
table. Never use model-generated strings as identifiers: a model will happily invent plausible ids
and reuse them. Never derive ids from content either; the same question asked twice is two turns.

### Propagation
Carry the id set as a context object (Python `contextvars`, Node `AsyncLocalStorage`) so every log
line, metric, tool call and outbound request is tagged without threading parameters by hand. Put
run_id, tool_call_id and the trace headers into outbound tool requests so the tool service's logs join
back. Put the full id set into every event in the event log.

### Identifiers are not authorization
Every id the client sends (conversation id, turn id, run id in a reconnect URL) is a claim, not a
credential. A server that loads `/conversations/{id}/events` without checking that the conversation
belongs to the caller's tenant and user has an insecure direct object reference, and unguessable
UUIDs only make the bug harder to find, not absent: ids leak through logs, shared links and browser
history. Resolve the caller's identity from the auth token, then look up the object *scoped by*
that identity (`where tenant_id = $1 and conversation_id = $2`). The same rule applies to tool
services receiving `X-Run-Id`: it is for correlation, never for deciding what the caller may touch.

### What each id lets you do
- Resume after a crash: run_id plus latest step.
- Dedupe a retried submission: turn_id.
- Dedupe a retried side effect: the idempotency key derived from (run_id, step, index).
- Reconnect and catch up: conversation_id plus sequence number.
- Bill, enforce quotas, delete: tenant_id.
- Debug across services: trace_id.
- Pair a tool result with its request: tool_call_id.

### Attempt semantics
Decide what is shared across attempts of a turn: the user's message and prior conversation (yes), the
previous attempt's side effects (they happened, so the new attempt must be told or must check), and
the budget (per attempt or per turn; per turn stops a retry storm). Record `attempt` on the run and
keep failed runs: they are evidence.

## Example
The frozen `Ids` object travels in a context variable; the client-minted turn id survives retries,
the server mints a run id per attempt, and the idempotency key is derived from run, step and call
index so a resumed step reproduces it exactly.

```python
import contextvars, hashlib, logging, uuid
from dataclasses import asdict, dataclass, replace

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class Ids:
    tenant_id: str
    user_id: str
    conversation_id: str
    turn_id: str                          # minted by the client, stable across retries
    run_id: str = ""                      # minted by the server per attempt
    attempt: int = 1
    step: int = 0
    trace_id: str = ""

    def idem_key(self, call_index: int) -> str:     # identical on resume of this step
        raw = f"{self.run_id}:{self.step}:{call_index}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def fields(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v != ""}   # keep step=0

CURRENT: contextvars.ContextVar[Ids] = contextvars.ContextVar("ids")

def start_run(ids: Ids, attempt: int = 1) -> Ids:
    run = replace(ids, run_id=str(uuid.uuid4()), attempt=attempt, step=0)
    CURRENT.set(run)
    return run

def next_step() -> Ids:
    ids = replace(CURRENT.get(), step=CURRENT.get().step + 1)
    CURRENT.set(ids)
    return ids

def call_tool(tool, name, args, call_index):
    ids = CURRENT.get()
    headers = {"X-Run-Id": ids.run_id,             # correlation only, never authorization
               "Idempotency-Key": ids.idem_key(call_index)}
    # the trace context travels as the W3C `traceparent` header; let the OpenTelemetry
    # propagator inject it (propagate.inject(headers)) rather than inventing a header
    log.info("tool_call", extra={"tool": name, **ids.fields()})
    return tool(name, args, headers=headers)
```

Trace the three cases through this code. A user sends turn `t_41` and the POST times out; the client
re-sends `t_41`, the server finds a run already exists for it and returns that run's stream, so
nothing is minted. The run `r_a` crashes at step 3 while calling `create_ticket` as call 0; the
resume restores `Ids(run_id="r_a", step=3)` from the checkpoint, so `idem_key(0)` hashes
`"r_a:3:0"` again and the ticket API replays the first ticket. The user then clicks regenerate:
`start_run` mints `r_b` with `attempt=2`, and step 3's key now hashes `"r_b:3:0"`, a different key,
so a second ticket is possible by design. Whether that is right depends on whether `r_b` was told
that `r_a` already created one, which is a product decision the ids make visible rather than hide.

## Trade-offs
- Client-minted turn ids make submissions retry-safe, but you must validate them (format, uniqueness
  per conversation) because a buggy or malicious client can collide them; server-minted ids are safe
  but cannot dedupe retries.
- A full id hierarchy costs discipline, since every log line, event and outbound call must carry it
  and retrofitting is tedious; a context-propagation layer added early makes it nearly free.
- Deriving idempotency keys from (run, step, index) is stable across crash resumes, but a new attempt
  gets new keys by design, so a retried turn can repeat effects unless the new attempt checks the
  previous attempt's results.
- Time-ordered ids improve index locality and sortability, but reveal creation time and ordering to
  anyone who sees them; random ids reveal nothing but fragment indexes.

## Pitfalls
- One "session id" for both conversation and execution; the symptom is a regenerate request
  overwriting the first run's checkpoints or deduping against it incorrectly.
- Letting the model produce or echo identifiers in free text; the symptom is tool results attached to
  the wrong call, or ids that look valid but do not exist.
- Minting a new run id on crash resume; the symptom is idempotency keys that change, so the dedup
  layer never matches.
- Naming ids inconsistently across services (run_id in one, execution_id in another); the symptom is
  traces that cannot be joined when an incident needs them.
- Forgetting to echo tool_call_id on the result; the symptom is provider API errors about unmatched
  tool calls and silently dropped results.
- Loading objects by a client-supplied id without scoping by the caller's tenant; the symptom shows
  up in a security review or a bug report, where one customer's conversation renders for another
  after an id was pasted into a URL.
- Sub-agents that reuse the parent's run id, or have no link to it; the symptom is children
  overwriting the parent's checkpoints, or a resumed parent spawning a duplicate child.

## When to use
Define the hierarchy on day one of any agent project; it costs nothing then and a migration later.
It shows up whenever you design the API (turn id in the request), the store (run id keys checkpoints,
conversation id keys events), the tools (idempotency key and trace headers) and the logs (every line
tagged). Measure the percentage of log lines carrying the full id set, the share of turns whose trace
spans all services, and duplicate-effect incidents traced to id confusion. In interviews, being able
to say what stays stable across a retry versus a resume versus a regenerate, and which id dedupes
each, is the depth expected of an engineer who has run agents in production.

## Role lens
- **Engineer:** Implements the Ids context object, mints turn ids in the client SDK, propagates
  headers to tool services and tags every log line and event with the full set.
- **Architect:** Specifies the id hierarchy, who mints each id, its stability guarantees across retry
  and resume, and the trace model that spans services and vendors.
- **FDE:** Maps customer-side correlation ids (ticket numbers, case ids, SSO subject ids) onto the
  hierarchy so the customer's own logs join with the agent's.

## Check yourself
**Q:** Explain which identifier stays the same and which changes across a client network retry, a
crash-resume of a run, and a user clicking regenerate. Why does it matter for duplicate side effects?
**A:** A client retry carries the same client-minted turn id, so the server recognises it and does
not start a second run; nothing new is minted. A crash-resume continues the same run id and step, so
the idempotency keys derived from them are identical and a re-executed pending tool call dedupes
against the first attempt. A regenerate is a new attempt: same conversation and turn, new run id, new
keys, because the user wants new work. That last case is why a new attempt must either see the
previous attempt's results in its context or check before acting; otherwise it repeats effects that
are legitimate from the runtime's perspective. The ids encode exactly which repeats are errors and
which are intended.

## Quiz
### Q: Who should mint the turn id, and why?
- [ ] The model, because it understands the request
- [x] The client, before sending, so a retried request carries the same id and the server can deduplicate it
- [ ] The server, after validating the request
- [ ] The model provider, via its request id
> Deduplicating a retried submission needs an id that exists before the first attempt and is reused on
> retry; only the client has that information. Server or provider ids are minted too late.

### Q: How should an idempotency key for a tool call be produced in a durable agent runtime?
- [ ] A fresh random UUID per execution of the tool
- [ ] A hash of the tool's arguments only
- [x] Derived deterministically from run id, step and call index, so a crash resume regenerates the same key
- [ ] A per-process counter incremented on each tool dispatch
> A resume must reproduce the key to dedupe the re-execution; random keys change, a process counter
> restarts from zero on a new worker, and argument-only hashes collide across legitimate repeats.

### Q: What distinguishes a run id from a conversation id?
- [ ] Run ids are numeric while conversation ids are UUIDs
- [x] A conversation id keys the long-lived transcript, while a run id keys one execution attempt of one turn and is what checkpoints and leases are scoped to
- [ ] They are the same value in most systems
- [ ] Run ids are issued by the model provider
> Conversations span many turns and days; runs span one attempt lasting minutes. Resuming, leasing and
> deriving idempotency keys all need the attempt-level id, which a conversation id cannot provide.
