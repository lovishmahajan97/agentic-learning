---
id: idempotent-tool-execution
module: agent-reliability
name: Idempotent tool execution
kind: pattern
difficulty: core
est_minutes: 14
related:
  - agent-reliability/durable-execution-and-checkpointing
  - agent-reliability/conversation-execution-identifiers
  - serving-inference/idempotency
  - fastapi-for-ai/idempotency-keys
  - system-design/exactly-once-processing-limitations
  - system-design/outbox-inbox-patterns
  - enterprise-integration/retry-deduplication-reconciliation
  - agents/approval-gates-for-consequential-actions
sources:
  - Official docs: Stripe (idempotent requests)
  - Official docs: Model Context Protocol (tool annotations)
---

## TL;DR
Agents retry constantly: the model retries after errors, the harness retries after timeouts, the
durable runtime re-executes after crashes, and each retry of a side-effecting tool risks a duplicate.
Exactly-once execution across a network boundary is impossible, so the design target is at-least-once
execution with idempotent effects: classify tools, derive a deterministic idempotency key per call,
pass it downstream or dedupe locally, and gate what cannot be made idempotent behind approvals or an
outbox.

## Why it exists
Consider a tool `send_invoice(customer, amount)`. The call times out after thirty seconds. Did the
invoice send? If the request was lost, no; if the response was lost, yes. The caller cannot tell:
this is the classic two-generals situation and it has no protocol solution. The harness has two
choices, retry and risk a duplicate, or give up and risk that nothing happened. Agents make this
worse than ordinary services because retries come from three layers that do not coordinate: the
model, which on seeing an error will often call the tool again, sometimes with slightly different
arguments; the harness, with its HTTP retry policy; and the durable runtime, which re-executes pending
calls after a crash. One ambiguous timeout can become three invoices.

Conventional advice, "only retry safe methods", does not help, because the actions that make agents
valuable are the unsafe ones: creating, sending, charging, deploying. The alternative, "never retry
writes", leaves runs failing on every transient error, which in practice means the model retries
anyway, uncontrolled.

The way out is to make the *effect* idempotent rather than trying to make the *delivery*
exactly-once: repeating the call with the same identity must produce the same outcome and the same
response. Then at-least-once delivery, which is cheap and robust, yields effectively-once results.
That requires an identity for each logical call, a place to remember what it produced, and tools (or
adapters around them) that honour the identity.

## How it works
### Classify every tool
- **Read-only** (`search`, `get_record`): naturally idempotent. Retry freely, and never replay cached
  results across retries because freshness matters.
- **Idempotent writes** (`set_status(id, "closed")`, upsert by natural key, HTTP PUT to a known
  resource): repeating is harmless by construction. Retry freely.
- **Non-idempotent writes** (`create_ticket`, `send_email`, `charge`, `increment`): must be wrapped.
Record the classification in the tool schema so the harness can pick the retry policy automatically.
MCP tool annotations (`readOnlyHint`, `idempotentHint`, `destructiveHint`) exist for exactly this,
and they are hints, not guarantees: verify them in integration tests.

### Derive the key
The key must be identical across every retry of the same logical call and different for distinct
calls. Derive it from the execution identifiers, for example `f"{run_id}:{step}:{call_index}"`,
optionally hashed. Not a random UUID, because a retry would mint a new one; and not a hash of the
arguments alone, because the model may reword an email body on retry and two legitimately different
calls with equal arguments would collide. For consequential actions some teams additionally include a
semantic identity (recipient plus turn id) so a model-level retry with changed wording still dedupes;
that is a product decision, not a runtime default.

### Three ways to honour the key
1. **Downstream support.** Pass the key as the API's idempotency header or field. Stripe's
   `Idempotency-Key` header is the canonical example, and many payment, messaging and ticketing APIs
   offer an equivalent. The provider stores the first response and replays it for repeats. This is
   the strongest option because the dedup lives where the effect lives.
2. **Natural key on the resource.** Write the key into the created object (an `external_ref` field,
   a tag, part of the subject line) and look it up before creating, or use a conditional create
   (`If-None-Match`, `INSERT ... ON CONFLICT DO NOTHING`). Lookup-then-create is racy across
   concurrent workers, so prefer conditional writes where the API offers them.
3. **Local dedup table.** `tool_executions(key primary key, status, result, started_at,
   finished_at)`. Before executing, insert the key with status `in_flight`; if the insert conflicts,
   return the stored result when the status is `done`, or wait briefly when it is `in_flight` because
   another worker is on it. After executing, update to `done` with the result. The table must be
   durable and ideally lives in the same store as the checkpoint. This covers tools with no
   downstream support, but cannot cover a crash between the effect and the `done` update: the resume
   finds `in_flight` and must look the effect up downstream or treat the tool as "possibly executed"
   and escalate.

### Return the same response
Idempotency applies to the response too. The second call must return the first result, marked as
replayed (`{"replayed": true, ...}`), so the model receives the ticket id it needs and knows not to
try again. Returning an error on a duplicate pushes the model into a retry loop.

### Concurrency
Two workers, or two parallel tool calls, with the same key: the dedup table's primary key or the
downstream's own lock makes one wait or fail. Design the `in_flight` branch explicitly, a bounded
wait and then the stored result, or an immediate error that the harness treats as "retry later"
rather than as a tool failure shown to the model.

### What cannot be made idempotent
Some effects have no key and no undo: an SMS already delivered, a physical action. Options: an
**outbox**, where the intent is written to a durable table inside the checkpoint transaction and a
single-consumer worker delivers it with its own dedup; an **approval gate**, so a human confirms
consequential actions, which also bounds the blast radius; or **compensation** (refund, delete) when
the effect can be reversed, as in sagas. Pick per tool and record the choice in the tool's adapter.

### Interaction with the model
Tell the model in the tool description that retries are safe and that a replayed result means the
action already happened. Do not expose the key to the model; the harness injects it. If the model
retries with different arguments, the harness sees a new key and a new effect, which is correct when
the arguments genuinely differ and wrong when the model merely reworded; the semantic-key option and
loop detection address that case.

## Example
The wrapper claims the key before executing, waits briefly if another worker holds it, replays the
stored result on repeats with a `replayed` flag, and forwards the key to the downstream API so the
dedup also lives where the effect lives.

```python
import hashlib, time

class DedupStore:                 # key -> {status, result}; back with a durable table in prod
    def __init__(self):
        self.rows = {}
    def begin(self, key):         # returns the existing row, or None if we now own the key
        row = self.rows.get(key)
        if row is None:
            self.rows[key] = {"status": "in_flight", "started": time.time()}
        return row
    def finish(self, key, result):
        self.rows[key] = {"status": "done", "result": result}

def idempotent(store: DedupStore, wait_s: float = 10):
    def wrap(tool):
        def run(args: dict, *, key: str):
            row, deadline = store.begin(key), time.time() + wait_s
            while row and row["status"] == "in_flight" and time.time() < deadline:
                time.sleep(0.2)                     # another worker holds the key
                row = store.rows.get(key)
            if row and row["status"] == "done":
                return {**row["result"], "replayed": True}
            if row:                                 # still in flight after the wait
                raise RuntimeError("in_flight: retry later, do not re-issue")
            result = tool(**args, idempotency_key=key)   # key goes downstream too
            store.finish(key, result)
            return result
        return run
    return wrap

def key_for(ids, call_index: int) -> str:
    return hashlib.sha256(f"{ids.run_id}:{ids.step}:{call_index}".encode()).hexdigest()

@idempotent(DedupStore())
def create_ticket(title, body, idempotency_key):
    return api.post("/tickets", json={"title": title, "body": body},
                    headers={"Idempotency-Key": idempotency_key})

# in the agent loop:  create_ticket({"title": t, "body": b}, key=key_for(ids, i))
```

## Trade-offs
- Downstream idempotency keys give the strongest guarantee with the least code, but only where the
  API supports them; everything else needs an adapter-level dedup table that cannot see effects
  completed just before a crash.
- A local dedup table makes any tool retry-safe from the harness's point of view, but adds a durable
  write per side-effecting call and a stale in-flight problem that needs expiry and reconciliation.
- Keying on execution ids is precise and stable across resumes, while keying on semantic identity
  also catches model-level reworded retries at the risk of suppressing a legitimately different
  second action.
- Replaying the stored result keeps the model moving, but a replayed result may be stale if the
  resource has changed since; for reads prefer fresh execution, for writes prefer the replay.

## Pitfalls
- Random idempotency keys generated at call time; the symptom is dedup infrastructure in place while
  duplicates still appear after crashes and retries.
- Returning an error on a duplicate instead of the original result; the symptom is the model looping
  to "fix" an action that already succeeded.
- Applying the replaying wrapper to read tools; the symptom is the agent acting on stale data because
  a repeated read returned a cached result.
- Trusting schema or MCP hints that a tool is idempotent without testing; the symptom is a tool
  labelled idempotent creating duplicates under retry in integration tests.
- Never expiring in-flight rows; the symptom is a key stuck in flight after a crash so every retry
  errors forever.

## When to use
Every tool that writes, from the first prototype that touches a real system. Start by classifying
tools and refusing automatic retries on non-idempotent ones until they are wrapped; then add keys
derived from execution ids, pass them downstream where supported and dedupe locally elsewhere;
reserve outboxes and approval gates for effects that cannot be keyed. Measure duplicate-effect
incidents per thousand runs, the replay rate (how often the dedup actually fired), and in-flight rows
older than the tool's timeout. In interviews the question is "your tool call timed out, what do you
do", and the expected answer covers the key, the dedup table, the replayed response, and the honest
statement that exactly-once delivery is impossible.

## Role lens
- **Engineer:** Classifies tools, writes the idempotent wrapper and dedup table, forwards keys to
  downstream APIs and tests every tool under injected timeouts and process kills.
- **Architect:** Sets the policy that no side-effecting tool ships without an idempotency strategy,
  and chooses between downstream keys, outbox and approval gates per class of effect.
- **FDE:** Audits the customer's APIs for idempotency support, builds adapters with natural-key
  lookups where there is none, and explains why the duplicate tickets stopped after the fix.

## Check yourself
**Q:** A `create_ticket` tool call times out. The ticketing API has no idempotency header. Design
the retry so the run neither loses the ticket nor creates two.
**A:** Before the first call, the harness inserted a dedup row keyed by (run id, step, call index)
with status in_flight and wrote the same key into the ticket payload as an external reference. On
timeout the harness does not blindly retry; it queries the API for a ticket carrying that external
reference. If one is found, it records the result as done and returns it to the model marked
replayed. If none is found, it retries the create with the same reference, so a race in which the
first request completes late still yields a findable duplicate to reconcile rather than a silent
one. Expire stale in_flight rows, and if the API cannot be queried at all, route the call to an
approval or an outbox worker rather than guessing.

## Quiz
### Q: Why is "exactly-once tool execution" the wrong design target?
- [ ] Models cannot be prevented from retrying
- [x] A lost response is indistinguishable from a lost request, so delivery cannot be exactly-once; the achievable target is at-least-once delivery with idempotent effects
- [ ] Idempotency keys are not supported by most APIs
- [ ] Exactly-once is only possible with message queues
> Across a network boundary the caller cannot know whether a timed-out request took effect. Making the
> effect idempotent lets retries be safe, which is the only route to effectively-once outcomes.

### Q: What should a duplicate (same key) call to an idempotent tool return?
- [ ] An error indicating the action was already performed
- [x] The original result, flagged as replayed, so the model has the ids it needs and does not retry again
- [ ] Nothing, since the effect already happened
- [ ] A fresh execution with a new key
> Returning an error pushes the model into retry loops; returning the stored result gives it the same
> information the first call would have, which is the definition of idempotent behaviour.

### Q: Which tools should NOT be wrapped with a result-replaying dedup layer?
- [ ] Tools that create resources
- [ ] Tools that send messages
- [x] Read-only tools, because a replayed result may be stale and repeated reads are harmless
- [ ] Tools that charge payments
> Reads are naturally idempotent and the agent needs current data, so replaying a cached result risks
> acting on stale state. Writes are where replaying the first result prevents duplicates.
