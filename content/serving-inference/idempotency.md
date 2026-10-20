---
id: idempotency
module: serving-inference
name: Idempotency
kind: concept
difficulty: core
est_minutes: 13
status: reviewed
related:
  - serving-inference/retry-strategies
  - serving-inference/timeouts
  - fastapi-for-ai/idempotency-keys
  - agent-reliability/idempotent-tool-execution
  - system-design/exactly-once-processing-limitations
  - system-design/outbox-inbox-patterns
  - enterprise-integration/retry-deduplication-reconciliation
sources:
  - Official docs: Stripe API (idempotent requests)
  - IETF HTTPAPI working group — The Idempotency-Key HTTP Header Field (draft)
---

## TL;DR
An operation is idempotent when doing it twice has the same effect as doing it once. LLM systems need
this because retries, timeouts, at-least-once queues and agent re-execution all produce duplicates, and
duplicates cost money (a second generation), corrupt state (a second reply in a chat) or act twice on the
world (a second email). The standard mechanism is a client-chosen idempotency key stored with the outcome,
so a repeat returns the saved result instead of redoing the work.

## Why it exists
A timeout is ambiguous. The request may never have arrived, may still be running, or may have completed
after the client stopped listening. The client cannot tell which, so its only options are to give up or
to retry, and giving up is poor availability. Exactly-once delivery is impossible in general across a
network; what distributed systems achieve instead is at-least-once delivery combined with idempotent
processing, which produces effectively-once results. That is why every durable queue, webhook and payment
API is built around deduplication rather than around preventing duplicates.

LLM systems have three properties that make this urgent. Generation is expensive: a retried 30-second,
10k-token call doubles cost and latency, and if the first call actually completed you paid twice for one
answer. Generation is non-deterministic: the second attempt produces different text, so a user who
clicked once may see two different replies to the same message. And agents execute tools with side
effects: a retried step that calls `create_ticket` or `send_email` without protection does it twice,
which is the failure that turns a flaky network into a customer-visible incident.

The naive alternatives fail predictably. "Never retry" sacrifices availability. "Dedupe by content"
breaks when identical content is legitimately repeated ("send another reminder"). "Rely on a unique
database constraint" works only when a natural key exists, which an LLM call rarely has. Idempotency keys
supply the missing key explicitly.

## How it works
### The key
The client generates a unique key, usually a UUID, per *logical operation* and reuses it on every retry
of that operation. The key is scoped to the caller (tenant, API key) so collisions between callers are
impossible, and it expires after a retention window; Stripe, the canonical example, keeps keys for 24
hours. The distinction between operation and attempt is the whole idea: a new key means "do it again",
the same key means "make sure it happened once".

### The server algorithm
1. Fingerprint the request body (a hash of its canonical serialisation).
2. *Claim* the key: atomically insert `(key, fingerprint, status=in_progress)`. If the insert conflicts:
   a different fingerprint means the key was reused for a different request, which is a client bug and
   returns 422; `status=completed` means replay the stored response, same status code and body, with no
   work done; `status=in_progress` means a duplicate is racing the original, so return 409 or wait for
   the first to finish. Returning 409 rather than running a second copy is why the claim must be the
   first write.
3. Execute the operation.
4. Store the outcome atomically with `status=completed`. Transient failures should release the claim so
   a retry can run; permanent failures (4xx) can be stored and replayed.

The claim needs a lease. If the process crashes between steps 3 and 4 the key stays `in_progress`
forever, so the row carries a short expiry after which a retry may take over. The lease length is a
genuine tension: it must outlast the slowest legitimate execution (a long generation or a slow tool),
or a retry will take over a key whose first attempt is still running and you get the duplicate you were
preventing; but every second of lease is a second a crashed attempt blocks recovery. Long operations
therefore renew the lease periodically (a heartbeat), and the completing write checks that it still
holds the lease, for example by matching an attempt id stored in the row, before recording the outcome. This is also the honest
limit of the pattern: unless the side effect and the key store share one transaction, there is a window
in which the effect happened but was not recorded, and the retry repeats it. Outbox patterns and
reconciliation jobs exist to close that window.

### Idempotent by design
Often you can avoid the key store by making the operation naturally idempotent. Derive resource ids
deterministically (`job_id = hash(tenant, document_id, prompt_version)`) and insert with
`ON CONFLICT DO NOTHING`. Prefer set semantics ("the summary of document X is S") to append or increment
semantics ("add a summary"). Use conditional writes with version numbers so a stale duplicate is rejected.

### Where it lands in an LLM system
- **Chat turns.** The client generates a message id when the user presses send; the server appends the
  user turn and the assistant reply under that id only if absent, and stores the generated text under
  it. A double click or a retried request returns the same reply instead of a second, different one, and
  the second generation is never billed.
- **Agent tool calls.** The key is derived from the run id plus the step or tool-call id and passed to
  the tool. The tool implementation dedupes on it, and if it calls an external API that accepts an
  idempotency header, it forwards the key. A regenerated model turn produces new tool-call ids, so the
  derivation must include something stable about the step, not only the model's id.
- **Queues and webhooks.** Consumers record processed message or event ids (the inbox pattern) because
  deliveries are at-least-once by contract.
- **Provider calls.** Most generation endpoints do not accept an idempotency key themselves (check the
  specific provider), so your layer owns it: coalesce concurrent duplicates onto one in-flight call, and
  replay the stored completion to late retries.

### Streaming
Partial output complicates replay. Persist the final text under the key when the stream completes; a
retry that arrives mid-stream either attaches to the in-flight generation or restarts under the same
message id so the client can discard the partial render.

## Example
A minimal idempotency layer: claim first, fingerprint check, replay on completion, 409 on a racing
duplicate, release on transient failure so the client can retry. In production the dictionary is a Redis
`SET NX PX` or a table with a unique index, and `claim` is a single atomic operation.

```python
import hashlib, json, time

class IdempotencyStore:
    """In production: Redis SET NX PX, or a table with a unique index on key."""
    def __init__(self, ttl_s=86_400, lease_s=120):
        self.rows, self.ttl_s, self.lease_s = {}, ttl_s, lease_s
    def claim(self, key, fingerprint):
        row = self.rows.get(key)
        if row and row["expires"] > time.monotonic():
            if row["fp"] != fingerprint:
                return "mismatch", None
            return row["status"], row.get("response")      # "in_progress" or "done"
        self.rows[key] = {"fp": fingerprint, "status": "in_progress",
                          "expires": time.monotonic() + self.lease_s}   # short lease while running
        return "claimed", None
    def complete(self, key, response):
        self.rows[key].update(status="done", response=response, expires=time.monotonic() + self.ttl_s)
    def release(self, key):
        self.rows.pop(key, None)

store = IdempotencyStore()

def handle_generate(idem_key: str, tenant: str, body: dict):
    key = f"{tenant}:{idem_key}"                              # scope keys per caller
    fp = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    status, saved = store.claim(key, fp)
    if status == "mismatch":    return 422, {"error": "idempotency key reused with a different body"}
    if status == "done":        return 200, saved             # replay: no second generation, no second bill
    if status == "in_progress": return 409, {"error": "already in flight; retry shortly"}
    try:
        resp = llm.generate(**body)       # expensive, non-deterministic; must finish within lease_s
    except TransientError:
        store.release(key)                                    # let the client retry with the same key
        raise                             # a permanent error could be stored and replayed instead
    store.complete(key, resp)
    return 200, resp
```

## Trade-offs
- A key store adds a write and a read to every request and a dependency on the store; when a
  deterministic natural id exists, an upsert achieves the same guarantee for free.
- Replaying the stored response gives consistency but means a retry can never get a better answer;
  "regenerate" must therefore be a new key, not a retry.
- Returning 409 to a racing duplicate is simple and safe; coalescing it onto the in-flight call gives a
  better experience but holds connections and needs more state.
- Stored responses double as a cache, but they are now retained user content with privacy and retention
  obligations, so the TTL is a policy decision as well as a technical one.

## Pitfalls
- Generating the key inside the retry loop so each attempt gets a fresh one; the symptom is duplicates
  persisting despite "idempotency" being implemented.
- Check-then-insert instead of an atomic claim; the symptom is duplicates that appear only under
  concurrency or load tests and never in local runs.
- No fingerprint check; the symptom is a client bug that reuses keys silently returning one request's
  answer to an unrelated request.
- Storing transient failures as the final outcome; the symptom is a momentary 503 becoming permanent for
  that key until it expires.
- Agent tool calls keyed only on the model's tool-call id; the symptom is duplicate tickets after a
  regenerated turn, because the regenerated call carries a new id.
- A lease shorter than the slowest generation or tool call; the symptom is duplicates that appear
  only on long requests, with two attempts for the same key overlapping in the trace.
- Assuming temperature zero makes generation idempotent; the symptom is subtly different replayed
  outputs, since determinism is not guaranteed across hardware and batch composition.

## When to use
Require an idempotency key on every endpoint that creates something, costs money or triggers a side
effect, which in an LLM product means message creation, job submission and every tool with an external
effect. Skip the key store where a natural deterministic id makes the write an upsert, and where the
operation is a pure read. Pair it with the retry policy: retries are only safe because of this. Measure
replay rate (how often a key is reused) and 409 rate; a high replay rate points at an overly aggressive
client, a nonzero duplicate-effect rate points at a window in the claim-execute-store sequence. In
interviews, the expected answer defines idempotency precisely, explains why exactly-once is impossible
and at-least-once plus idempotency is the substitute, and walks through claim, fingerprint, replay and
lease.

## Role lens
- **Engineer:** Adds the key header to the API, implements the atomic claim and replay in Redis or
  Postgres, and threads run and step ids into every agent tool call.
- **Architect:** Decides which operations must be idempotent, where the key store lives relative to the
  side effect, and how reconciliation closes the gap the store cannot.
- **FDE:** Audits a customer's agent integration for tools that email, charge or create records without
  keys, and proves the fix with a chaos test that injects timeouts mid-step.

## Check yourself
**Q:** An agent step calls a `create_invoice` tool and the LLM request times out after the tool has
already run. How do you make the whole system safe to retry end to end?

**A:** Treat each layer separately. The user's turn carries a client-generated message id, so the retried
turn replays or attaches to the in-flight run instead of starting a second one. Inside the run, each
tool call gets an idempotency key derived from the run id and a stable step identifier, not only the
model's tool-call id, which changes if the turn is regenerated. The `create_invoice` implementation
claims that key atomically before acting, forwards it as the idempotency header to the billing API if
that API supports one, and stores the invoice id under the key; a retried step then returns the existing
invoice instead of creating another. The LLM call itself is wrapped so a retry after timeout coalesces
onto the in-flight generation or replays the stored completion. Finally, because the key store and the
billing system are not one transaction, a reconciliation job compares invoices against recorded steps to
catch the rare window where the effect happened but the record did not.

## Quiz
### Q: Which operation is idempotent by construction, with no key store needed?
- [ ] Append the model's reply to the conversation
- [x] Set the summary of document 42 to the generated text
- [ ] Increment the tenant's token counter by the response usage
- [ ] Send the generated digest email to the user
> Setting a value to the same thing twice leaves one state; appending, incrementing and sending accumulate
> with each repeat. Designing writes as sets instead of appends removes the need for deduplication. If
> the text is regenerated on the retry the final value may differ, but there is still exactly one summary.

### Q: A client sends a request with idempotency key K, times out, and retries with K while the first attempt is still generating. What should the server do?
- [ ] Start a second generation, since the first may have failed
- [x] Detect the in-progress claim on K and return 409 (or attach the retry to the in-flight result) instead of generating again
- [ ] Return the stored response for K, which is empty
- [ ] Reject the retry with 422 because the key was reused
> The atomic claim exists precisely to catch this race: the key is in progress, so a second execution
> would duplicate cost and effects. 422 is for a key reused with a different body, which this is not.

### Q: Why should the server store a fingerprint of the request body alongside the idempotency key?
- [ ] To allow the key to be shorter than a UUID
- [ ] To let the server compress the stored response
- [x] To detect a key being reused for a different request, which is a client bug that would otherwise return the wrong stored response
- [ ] To make the key unique across tenants
> Keys are generated by clients, and clients have bugs. Without the fingerprint a reused key silently
> replays a response to an unrelated request; with it the server can reject the mismatch explicitly.
