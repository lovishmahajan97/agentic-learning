---
id: idempotency-keys
module: fastapi-for-ai
name: Idempotency keys
kind: pattern
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - fastapi-for-ai/timeouts
  - fastapi-for-ai/redis-caching
  - fastapi-for-ai/job-queues-and-durable-workers
  - fastapi-for-ai/dependency-injection
  - serving-inference/idempotency
  - agent-reliability/idempotent-tool-execution
  - system-design/exactly-once-processing-limitations
  - enterprise-integration/retry-deduplication-reconciliation
sources:
  - IETF HTTPAPI Working Group — The Idempotency-Key HTTP Header Field (Internet-Draft)
  - Official docs: Stripe API (Idempotent requests)
---

## TL;DR
Networks fail after the server has done the work but before the client hears back, so clients retry,
and a retried POST can bill twice, send two emails or run an expensive generation twice. An idempotency
key is a client-chosen unique ID sent with the request. The server records the first outcome under
that key and replays it for any retry, which turns at-least-once delivery into an effectively-once
effect.

## Why it exists
Retries are essential: timeouts, dropped connections and 502s from proxies are routine, and a client
that never retries is fragile. But a client that sees a timeout can't tell "the request never arrived"
from "the request succeeded and the response was lost". For GET, PUT or DELETE this doesn't matter,
because HTTP defines them as idempotent: doing them twice leaves the same state as doing them once.
POST makes no such promise, and most AI endpoints are POSTs with side effects: create a chat turn and
charge for its tokens, start a document-ingestion job, run an agent action that sends a message or
files a ticket.

LLM endpoints make this worse in two ways. Calls are slow and expensive, so timeouts and therefore
retries are common, and each duplicate costs real money. Outputs are also non-deterministic, so a
retry doesn't just duplicate the work, it produces a *different* answer. The client may then show one
answer while your history stores another.

The naive alternatives fail. "Deduplicate on request body" breaks when a user legitimately sends
"yes" twice, and misses a retry whose body has a new timestamp. "Don't retry POSTs" pushes the failure
onto the user. "Check whether a record exists first" races: two retries arriving 50 ms apart both see
nothing and both proceed. What you need is an identity for the *intent*, chosen by the client before
the first attempt, plus an atomic claim on the server.

## How it works
### The contract
The client generates a high-entropy key (a UUIDv4) once per logical operation and sends it on every
attempt, typically as an `Idempotency-Key` header. This is the convention used by payment APIs and
described in an IETF HTTPAPI Internet-Draft. The key identifies the operation, not the attempt, so a
new user action gets a new key and a retry reuses the old one.

### The server's state machine
For each key the server stores a record: `{fingerprint, state, status_code, response_body, created_at}`.
1. **Claim atomically.** Insert the key with `state = in_progress` using a primitive that fails if it
   already exists: Redis `SET key value NX EX ttl`, or a Postgres `INSERT ... ON CONFLICT DO NOTHING`
   on a unique key. Atomic because the check and the claim must be one step, or two concurrent retries
   both win.
2. **If the claim succeeded**, do the work, then store the status code and response body and mark
   it `completed`.
3. **If the key exists and is completed**, compare fingerprints and replay the stored response
   byte-for-byte without redoing anything. Replaying the stored response, rather than recomputing it,
   is essential for LLM output, which wouldn't come out the same twice.
4. **If the key exists and is in progress**, a concurrent retry is racing the original. Return
   `409 Conflict` (optionally with `Retry-After`) instead of waiting or running it twice.
5. **If the fingerprint differs**, the client reused a key for a different request, which is a client
   bug. Reject it (the draft suggests `422`) instead of replaying an unrelated response.

### Design choices and their reasons
- **Fingerprint** = hash of method, path and canonicalised body. Without it a reused key silently
  returns someone else's result.
- **Scope keys per caller** (`tenant:user:key`). Otherwise one tenant could collide with, or probe for,
  another's keys.
- **TTL** a bit longer than the client's maximum retry window. Stripe, for example, keeps keys for
  at least 24 hours. Shorter saves storage but lets a late retry run twice.
- **Two TTLs, not one.** The `in_progress` claim is a *lease*: give it a TTL just above the longest the
  work can take (say a couple of minutes for a generation), and only the completed record gets the long
  retention TTL. If the claim carried the 24-hour TTL, a worker that crashed or was killed mid-request
  would leave the key stuck in `in_progress`, and every retry would get 409 for a day.
- **What to store on failure.** Store deterministic outcomes, including 4xx validation errors and
  successful 2xx results. For 5xx or crashes mid-work, delete the claim (or let it expire) so the
  retry can run. If the work had partly happened, the work itself must be resumable or transactional.
- **Commit the effect and the record together** when you can. If both live in Postgres, write the
  side effect and the idempotency row in one transaction. With an external effect such as an LLM call
  or an email, you get at-most-once *re-execution* per key, but a crash between the effect and the
  record write can still duplicate it. Pass the key downstream (many payment and messaging APIs accept
  one) to close that gap.

## Example
A FastAPI dependency backed by Redis, for an endpoint that runs a billed generation.

```python
import hashlib, json
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
import redis.asyncio as redis

app = FastAPI()
r = redis.Redis()
TTL, LEASE = 24 * 3600, 120     # keep results a day; an in-progress claim expires after 2 min

async def idem(request: Request, tenant: str = Header(...),  # really: from the auth principal
               idempotency_key: str = Header(...)):
    body = await request.body()     # raw bytes; canonicalise JSON if clients re-serialise on retry
    fp = hashlib.sha256(b"|".join([request.method.encode(), request.url.path.encode(), body])).hexdigest()
    k = f"idem:{tenant}:{idempotency_key}"
    if await r.set(k, json.dumps({"fp": fp, "state": "in_progress"}), nx=True, ex=LEASE):
        return k, fp                                   # we own it: do the work
    rec = json.loads(await r.get(k) or "{}")
    if rec.get("fp") != fp:
        raise HTTPException(422, "Idempotency-Key reused with a different request")
    if rec.get("state") == "in_progress":
        raise HTTPException(409, "request with this key is still processing",
                            headers={"Retry-After": "2"})
    raise Replay(rec["status"], rec["body"])           # completed: replay

class Replay(Exception):
    def __init__(self, status, body): self.status, self.body = status, body

@app.exception_handler(Replay)
async def replay(_, e: Replay):
    return JSONResponse(e.body, status_code=e.status, headers={"Idempotent-Replayed": "true"})

@app.post("/v1/generations", status_code=201)
async def create(payload: dict, claim=Depends(idem)):
    k, fp = claim
    try:
        result = {"id": "gen_123", "text": "..."}       # call the model, charge tokens
    except Exception:
        await r.delete(k); raise                        # let a retry run again
    await r.set(k, json.dumps({"fp": fp, "state": "done", "status": 201, "body": result}), ex=TTL)
    return result
```

Scenario: the client posts with key `7f3c...`, the model takes 12 s, and the client's 10 s timeout
fires. It retries at 11 s with the same key and gets 409. At 13 s it retries again and gets the stored
201 with the same `gen_123` text. One generation is billed and one answer is shown. (This relies on the
server finishing the work after the client disconnects, which is FastAPI's default for a non-streaming
handler. If the pod dies mid-generation instead, the 120 s lease expires and a later retry runs the
work afresh.) The `tenant` header is a stand-in: in production take the tenant from the authenticated
principal, or a caller can scope keys into someone else's namespace.

## Trade-offs
- Keys make retries safe and cheap, but every mutating request now costs a store round trip and storage
  for the TTL. That is negligible next to an LLM call but noticeable on a high-volume, cheap endpoint.
- Returning 409 for in-progress duplicates is simple and avoids holding connections. Making the retry
  wait for the original's result is friendlier to clients, but it ties up a worker per waiting retry
  and needs pub/sub or polling.
- Redis gives fast atomic claims and automatic expiry, but a Redis failover can lose recent keys.
  Postgres gives durability and lets you commit the record with the side effect, at higher latency.
  The trade flips toward Postgres when a duplicate costs money.
- Requiring the header on every POST catches client bugs early, but it breaks naive integrations.
  Optional keys are friendlier but leave unkeyed retries unprotected.

## Pitfalls
- Check-then-insert instead of an atomic claim. The symptom is rare duplicates that only appear under
  concurrent retries, often from mobile clients on flaky networks.
- Generating the key per attempt (inside the retry loop) instead of per operation. The symptom is that
  the dedupe table fills up but duplicates still happen, because every retry has a fresh key.
- Not scoping keys by tenant or not fingerprinting the body. The symptom is a client receiving a
  replayed response that belongs to a different request or a different user.
- Storing a 500 as the final outcome. The symptom is a request that fails forever with the same
  replayed error even after the provider has recovered.
- Giving the in-progress claim the same long TTL as completed records. The symptom is that after a
  crash or deploy, retries for the interrupted keys return 409 for hours instead of re-running.
- Re-running generation on replay instead of returning the stored body. The symptom is users seeing
  two different answers for one question and token bills that don't match request counts.

## When to use
Use idempotency keys on any non-idempotent endpoint where a duplicate is costly or visible: anything
that charges, creates a resource, kicks off a long job, or lets an agent take an external action. They
matter most where clients retry automatically (SDKs, queues, mobile apps, webhook senders). You don't
need them for GET, or for writes that are naturally idempotent (PUT of a full resource, upserts keyed
by a natural ID). Measure replay and conflict counts: a high 409 rate means client timeouts are
shorter than your latency. In interviews, the expected answer covers the atomic claim, the
in-progress state, fingerprinting, TTL, and the honest limit that external side effects need the key
passed downstream.

## Role lens
- **Engineer:** Implements the claim-and-replay dependency, decides which outcomes to store, and makes
  the SDK generate one key per logical call and reuse it across retries.
- **Architect:** Decides the idempotency store, key scope and TTL, and how keys propagate across
  services, queues and agent tool calls so the whole chain stays effectively-once.
- **FDE:** Diagnoses duplicate tickets or double charges in a customer's integration by checking
  whether their retry middleware reuses keys or mints new ones.

## Check yourself
**Q:** A client sends a generation request, times out at 10 s, and retries with the same key at 10.5 s
while the first attempt is still running. Walk through what your server should do, and explain why
"just wait for the first one to finish" may not be the best answer.
**A:** The retry finds the key in the `in_progress` state with a matching fingerprint. The server
must not start a second generation. The simplest correct response is 409 with `Retry-After`, telling
the client to come back. When the first attempt finishes, it stores status and body, and the next
retry gets that response replayed exactly. Waiting inside the retry request is possible, but it holds
a connection and a worker per duplicate, can exceed the client's timeout again, and needs a
notification mechanism. Under a retry storm it amplifies load. The fingerprint check guards against a
reused key with a different body.

## Quiz
### Q: Why must an idempotency claim be an atomic insert rather than "read, and insert if absent"?
- [ ] Atomic inserts are faster in Redis
- [x] Two concurrent retries can both read "absent" and both execute the side effect
- [ ] Reads are not allowed on keys with a TTL
- [ ] The HTTP spec requires atomic storage for POST
> Check-then-act is a race. Concurrent duplicates both observe no record and both proceed. SET NX or a
> unique-constraint insert makes claiming and checking a single step, so exactly one attempt wins.

### Q: A completed request's key is retried. Why replay the stored response instead of recomputing it?
- [ ] Recomputing would violate the TTL
- [ ] Stored responses are smaller than fresh ones
- [x] LLM output is non-deterministic and the work has side effects, so recomputing gives a different answer and a second bill
- [ ] Replaying is required to set the Idempotent-Replayed header
> Idempotency means the retry observes the same outcome as the original. Re-running generation charges
> again and returns different text, which defeats the purpose even when no other side effect occurs.

### Q: A client reuses a key with a different request body. What should the server do?
- [ ] Replay the original response, since the key matches
- [ ] Process the new request and overwrite the stored record
- [x] Reject it as a client error, because the fingerprint does not match
- [ ] Return 409 Conflict and ask the client to retry later
> A key identifies one operation. A different body means the client misused it. Replaying would return
> an unrelated result and overwriting would break the earlier retry, so reject it (the draft suggests 422).
