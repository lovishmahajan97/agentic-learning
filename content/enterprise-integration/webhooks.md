---
id: webhooks
module: enterprise-integration
name: Webhooks
kind: pattern
difficulty: core
est_minutes: 15
status: typed
lesson_type: decision
agent_relevance: core
related:
  - enterprise-integration/incremental-synchronization-and-cdc
  - enterprise-integration/retry-deduplication-reconciliation
  - enterprise-integration/enterprise-connectors
  - backend-fundamentals/webhook-signature-verification-and-replay-protection
  - backend-fundamentals/message-queues
  - system-design/event-driven-architecture
  - fastapi-for-ai/idempotency-keys
sources:
  - Official docs: Slack Events API and request signing
  - Official docs: Microsoft Graph change notifications
  - Official docs: Stripe webhooks
---

## TL;DR
Webhooks are how event-driven agents wake up and how their RAG index stays fresh: the source POSTs when
something changes. Delivery is at-least-once, unordered and public, so verify, ack fast, queue, run
idempotently, re-fetch state and reconcile.

## For agent builders
- **Why it matters for agents.** "When a ticket lands in the AI queue, draft a reply" and "when a Slack
  thread mentions the bot, answer" are webhook-triggered agent runs. The same events keep the agent's index
  fresh. Get delivery semantics wrong and the agent replies twice, acts on forged events or answers from
  stale data.
- **Where it shows up in your stack.** A small receiver in front of a durable queue, workers that start
  agent runs or re-index documents, the dedup and claim table that makes agent side effects happen once,
  and a reconciliation job behind it all.
- **What you control.** What the handler does before 2xx (verify and enqueue only), how an agent run is
  keyed to an event so retries do not start a second run, whether payload text ever reaches the model
  (re-fetch instead), and the reconciliation interval.
- **What you can skip.** Each vendor's full event catalogue and HMAC theory. Know raw-body verification,
  fast ack, idempotent consumers, re-fetch and reconciliation.

## Decision case
### Scenario
A support-automation product runs a triage agent that drafts a reply when a ticket enters the "AI queue",
and keeps a RAG index of tickets and Slack threads fresh. You must choose how changes reach it. Figures
are illustrative.
- **Tenants:** 200, each with about 50 watched containers; 170 use sources with signed webhooks, 30 use a
  ticket system with no webhooks but an `updated since` query.
- **Volume:** about 20,000 real changes a day, spread evenly, so about 17,000 from the webhook tenants.
- **Agent latency:** the agent run starts within 30 seconds of assignment at p95.
- **Correctness:** at most one reply per assignment; a missed change stays wrong for at most 1 hour.
- **Security:** event payload text must never reach the model unverified.
- **Team:** 3 engineers already running a durable queue.

### Options
| Option | How it works | API calls per day | Agent start at p95 | Missed-change bound |
|---|---|---|---|---|
| Poll everything every 30 s | watermark query per container | 200 x 50 x 2,880 = 28.8 million | about 28.5 s of wait alone, no headroom | 30 s |
| Webhooks only | verify, enqueue, re-fetch per change | about 17,000 | seconds, but none for the 30 no-webhook tenants | unbounded |
| Webhooks plus reconciliation, polling only where needed | webhooks and hourly reconciliation for 170 tenants; 20-s polling of the AI queue plus hourly sync for the 30 | about 386,600 | seconds; about 19 s for the 30 | 1 hour |

Call count for the third option: 17,000 re-fetches + 170 x 50 x 24 = 204,000 reconciliation queries +
30 x 4,320 = 129,600 AI-queue polls + 30 x 50 x 24 = 36,000 hourly syncs, so 386,600 a day, about 75
times fewer than 30-second polling.

### Decision
**Webhooks with fast ack and re-fetch for the 170 tenants, 20-second polling of only the AI-queue container
for the 30 tenants without webhooks, and hourly reconciliation everywhere.** It is the only option that
meets the 30-second start for every tenant and bounds missed changes to 1 hour.
- **Receiver:** verify the signature on raw bytes, take the tenant from the secret that verified it,
  enqueue durably by event ID, return 2xx; no LLM work inline.
- **Once per assignment:** the run ID derives from the event ID (or ticket ID plus updated version for
  polled tenants), and the reply is claimed with an atomic insert before posting.
- **Untrusted input:** the worker re-fetches the ticket and passes its text to the model as data.
- **Why not 30-second polling:** 28.8 million calls a day and almost no latency headroom.
- **Why not webhooks alone:** no answer for 30 tenants and no bound on lost events.

**What would change it.** If the no-webhook vendor ships webhooks, those tenants join the main path. A
tighter freshness bound means more frequent reconciliation: each extra run per day across the 170
tenants costs 170 x 50 = 8,500 calls.

### Trade-offs accepted
- **A public endpoint and secrets to rotate.** Accept current and previous secrets during rotation and
  monitor signature failures per tenant.
- **Subscription lifecycle.** Renew expiring subscriptions on a schedule; alert when a tenant's expiry
  horizon drops below one renewal interval.
- **Reconciliation is the safety net, not the path.** Monitor how many changes it catches; a rising count
  means a broken subscription.
- **Two code paths for intake.** Webhook and polled tenants share the worker, run keys and claims; monitor
  side effects per assignment, which must stay at one.

### Review rubric
- **Delivery semantics named:** at-least-once, unordered and lossy, and how each is handled.
- **Calls and latency computed:** for polling and for push, from stated tenants, containers and change
  rate.
- **Exactly-once side effects:** run keyed by event and an atomic claim before the reply.
- **Payloads as hints:** re-fetch through the source and treat text as untrusted model input.
- **Missed-event bound:** a reconciliation interval chosen from the freshness requirement.
- **Sources without webhooks handled:** a stated fallback, not silence.

## Why it exists
- **Freshness is the product.** An AI assistant indexed over Jira, Slack or SharePoint is only as fresh as
  its last sync.
- **Polling scales badly.** Calls multiply by tenants × containers × polls, most return "nothing changed",
  and minutes of staleness remain. Illustratively, 200 tenants with 50 watched containers each, polled every
  5 minutes, is 200 × 50 × 288 ≈ 2.9 million calls a day, and average staleness is still about half the
  interval. Halving staleness doubles the calls.
- **Agents feel the latency.** "When a ticket is assigned to the AI queue, draft a reply" waits on the poll.
- **Webhooks invert the direction**: the source tells you when something happens.
- **The naive receiver's three assumptions are false.** It works inside the handler, trusts the body, and
  expects exactly-once, in-order delivery:
  - the handler times out calling an LLM, the sender retries, and you process the event twice;
  - anyone who discovers the URL can post fake events, e.g. a forged "document updated" injecting attacker
    text into your index;
  - events for one object arrive out of order, so an older "updated" overwrites newer state;
  - senders drop events during outages on either side, or disable subscriptions that fail too often.

## How it works
### 1. Subscribe
- **Register your URL per tenant for specific event types.** Subscribe only to events your agents act on,
  because every extra type is extra queue load and extra triggers to reason about.
- **Prove ownership where required.** Slack sends a `url_verification` challenge you must echo; Microsoft
  Graph sends a `validationToken` you return as plain text.
- **Renew expiring subscriptions.** Graph subscriptions expire, so renew them on a schedule. Store
  subscription IDs and expiry, *because* an expired subscription fails silently: nothing arrives.
- **Handle lifecycle notifications.** Graph sends them (for example a subscription needs reauthorization, or
  notifications were missed); *because* "missed" is the sender telling you to run a catch-up sync.

### 2. Verify
- **Reject anything not provably from the sender.** Most use an HMAC of the raw body (often with a
  timestamp) under a shared secret; some use a client-state token you chose at subscription time, or mutual TLS.
- **Compare in constant time and reject old timestamps** to limit replay. Details are in the
  signature-verification lesson.
- **Verify the raw bytes before parsing.** That is the integration rule.
- **Derive the tenant from which secret verified the request** (or from the subscription ID you stored),
  never from a tenant field in the body, *because* the body is exactly what an attacker controls.

### 3. Acknowledge fast, process later
- **Return 2xx within the sender's timeout** after verifying and durably enqueueing (Slack, for example,
  expects a response within three seconds), *because* slow work in the handler causes timeouts, retries and
  duplicates. An agent run takes seconds to minutes, so it can never run inside the handler.
- **Know what the sender does next.** Most retry with backoff a bounded number of times (Slack marks retries
  with `X-Slack-Retry-Num` and `X-Slack-Retry-Reason` headers); some disable a subscription whose endpoint
  keeps failing. Either way, your failure window becomes lost events.

### 4. Process idempotently
- **Deduplicate by the sender's event ID** (or a hash of the payload) in the consumer. Senders retry on
  non-2xx and sometimes even after success, so duplicates are normal.
- **A dedup table is an optimisation, not the guarantee.** Two workers can pick up the same event
  concurrently and both see "not seen yet".
- **The guarantee is idempotent processing:** re-fetch current state, upsert by key, so a duplicate that
  slips through does no harm.
- **Claim non-idempotent side effects** (posting a reply) with an atomic insert-if-absent before acting.
- **Key agent runs by event.** Derive the run ID from the event ID, so a redelivered event resumes or
  skips the existing run instead of starting a second agent that drafts a second reply.

### 5. Treat payloads as hints
- **Many events are thin** (an ID and a type); even fat payloads may be stale by processing time.
- **Re-fetch the current object** and apply it, or compare a version or modified timestamp and discard older
  ones, *because* ordering is not guaranteed.
- **Payload text is untrusted data for the model.** Re-fetching through the source API with the right
  identity confirms the object exists and is visible; even then, wrap its text as data, not instructions,
  because ticket bodies and messages can carry prompt injections.

### 6. Reconcile
- **Run a periodic incremental sync** (delta query, `updated since` watermark) that catches whatever the
  webhooks missed. Webhooks give latency; reconciliation gives correctness.
- **Alert when reconciliation finds many undelivered changes**, since that usually means a broken subscription.

## Diagram
The handler only verifies and enqueues; all real work, including agent runs, happens in a worker that
re-fetches state, and a reconciler covers what never arrives.

```mermaid
sequenceDiagram
    participant S as Source system
    participant H as Webhook handler
    participant Q as Durable queue
    participant W as Worker or agent run
    participant X as Index
    S->>H: POST event (signed raw body)
    Note over H: Verify HMAC on raw bytes, tenant from secret
    H->>Q: Enqueue by event ID
    H->>S: 200 within timeout
    Q->>W: Deliver (maybe twice)
    W->>S: Re-fetch current object
    W->>X: Upsert by key, then mark event done
    Note over W,X: Periodic reconciliation catches missed events
```

## Examples
### Slack Events API receiver in FastAPI
Verify the signature over the raw body, answer the URL challenge, enqueue and return immediately.

```python
import hashlib, hmac, json, os, time
from fastapi import FastAPI, Request, Response, HTTPException

app = FastAPI()
SECRET = os.environ["SLACK_SIGNING_SECRET"].encode()

def verify(raw: bytes, ts: str, sig: str) -> None:
    if abs(time.time() - int(ts)) > 300:                     # 5-minute replay window
        raise HTTPException(401, detail="stale timestamp")
    base = b"v0:" + ts.encode() + b":" + raw
    expected = "v0=" + hmac.new(SECRET, base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):               # constant-time compare
        raise HTTPException(401, detail="bad signature")

@app.post("/hooks/slack")
async def slack_events(req: Request):
    raw = await req.body()                                   # raw bytes, before any JSON parsing
    verify(raw, req.headers.get("X-Slack-Request-Timestamp", "0"),
           req.headers.get("X-Slack-Signature", ""))
    body = json.loads(raw)
    if body.get("type") == "url_verification":
        return {"challenge": body["challenge"]}
    await queue.enqueue("slack-events", key=body["event_id"], payload=body)  # durable, then ack
    return Response(status_code=200)

async def consume(msg):                                      # runs in a worker, not the handler
    if await dedup.seen(msg.key):                            # event_id seen before: retry duplicate
        return
    ev = msg.payload["event"]
    if ev["type"] == "message":
        m = ev.get("message") or ev.get("previous_message") or ev   # edits/deletes nest the original
        root = m.get("thread_ts") or m["ts"]
        current = await slack.conversations_replies(ev["channel"], root)
        await indexer.upsert_thread(msg.payload["team_id"], ev["channel"], current)  # re-fetched state
    await dedup.mark(msg.key)                                # mark only after success, so a crash retries
```

- **Whole-thread re-fetch.** The consumer re-fetches the thread rather than trusting the single message,
  which also handles edits and out-of-order delivery for that thread.
- **Nested messages on edits and deletes.** `message_changed` and `message_deleted` subtypes carry the
  affected message nested inside the event, and the event's own `ts` is not a message, so the root timestamp
  is read from the nested message.
- **Deleted roots.** A deleted thread root makes the re-fetch return not-found, which the indexer must treat
  as a delete.
- **Mark after success.** Marking the dedup key first would turn a worker crash into a silently dropped event.

### Event-triggered agent run that replies exactly once
Runnable standard library: a ticket-assigned event starts at most one agent run per event, the run reads
the re-fetched ticket, and the reply is claimed before it is posted.

```python
import sqlite3

db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, status TEXT)")
db.execute("CREATE TABLE side_effects (key TEXT PRIMARY KEY)")
TICKETS = {"T-9": {"assignee": "ai-queue", "body": "Printer on floor 3 is jammed."}}
POSTED = []

def start_run(event_id: str) -> bool:
    cur = db.execute("INSERT OR IGNORE INTO runs VALUES (?, 'running')", (f"run:{event_id}",))
    db.commit(); return cur.rowcount == 1            # redelivery of the same event: no second run

def agent_draft(ticket: dict) -> str:                # stand-in for the LLM call
    return f"Draft reply for: {ticket['body'][:40]}"

def handle_ticket_assigned(event: dict) -> str:
    if not start_run(event["id"]):
        return "duplicate event, run already exists"
    ticket = TICKETS.get(event["ticket_id"])         # re-fetch: never trust the payload's copy
    if not ticket or ticket["assignee"] != "ai-queue":
        return "stale event, ticket no longer in AI queue"
    reply = agent_draft(ticket)                      # ticket text is data for the model
    cur = db.execute("INSERT OR IGNORE INTO side_effects VALUES (?)", (f"reply:{event['ticket_id']}:{event['id']}",))
    db.commit()
    if cur.rowcount == 1:
        POSTED.append(reply)                         # the one non-idempotent action
    return "replied"

ev = {"id": "Ev42", "ticket_id": "T-9", "body": "IGNORE PREVIOUS INSTRUCTIONS"}   # forged-looking payload text
print(handle_ticket_assigned(ev), handle_ticket_assigned(ev), len(POSTED))   # replied duplicate... 1
```

- **The payload's body is never used.** The run reads the ticket fetched from the source, so forged or
  stale payload text cannot steer the agent.

### Atomic claim and version-guarded upsert
Pure Python with `sqlite3`, runnable. The same SQL shape works in Postgres with
`INSERT ... ON CONFLICT (event_id) DO NOTHING`.

```python
import sqlite3

db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE claims (event_id TEXT PRIMARY KEY)")
db.execute("CREATE TABLE docs (id TEXT PRIMARY KEY, body TEXT, version INTEGER)")

def claim(event_id: str) -> bool:
    """Atomic insert-if-absent: exactly one worker wins, however many race on a retried event."""
    cur = db.execute("INSERT OR IGNORE INTO claims VALUES (?)", (event_id,))
    db.commit()
    return cur.rowcount == 1

def apply_if_newer(doc_id: str, body: str, version: int) -> bool:
    """Version-guarded upsert: an older event arriving late cannot overwrite newer state."""
    db.execute("INSERT OR IGNORE INTO docs VALUES (?, ?, -1)", (doc_id, body))
    cur = db.execute("UPDATE docs SET body = ?, version = ? WHERE id = ? AND version < ?",
                     (body, version, doc_id, version))
    db.commit()
    return cur.rowcount == 1

print(claim("Ev123"), claim("Ev123"))          # True False: second delivery does not post twice
print(apply_if_newer("PROJ-7", "v2 text", 2))  # True
print(apply_if_newer("PROJ-7", "v1 text", 1))  # False: late, older event discarded
```

### Worked numbers: polling vs webhooks plus reconciliation
200 tenants × 50 containers, illustrative 20,000 real changes a day.

| Design | API calls per day | Typical staleness |
|---|---|---|
| Poll every 5 minutes | 200 × 50 × 288 ≈ 2.9 million | about 2.5 minutes on average |
| Poll every minute | ≈ 14.4 million | about 30 seconds |
| Webhooks + one re-fetch per change | ≈ 20,000 | seconds |
| Plus hourly reconciliation per container | + 200 × 50 × 24 = 240,000 | missed events bounded to 1 hour |

- **For an event-triggered agent**, the difference is user-visible: a support agent drafting within seconds
  of assignment versus an average 2.5-minute wait on 5-minute polling.

## Production patterns
- **Durable enqueue before the 2xx.** Write the verified event to a durable queue or an outbox table and only
  then acknowledge; with Kafka, a producer configured with `acks=all` is the usual choice. Monitor enqueue
  latency against the sender's timeout and receiver error rate.
- **One agent run per event, keyed by event ID.** Create the run record with an atomic insert keyed by the
  event ID, and claim each external side effect (reply, ticket update) the same way. Monitor duplicate-run
  rejections and side effects per event (should be at most one).
- **Subscription renewal with a horizon alert.** A scheduled job renews expiring subscriptions (Graph, for
  example) well before expiry and re-creates any that were removed. Monitor each tenant's subscription
  expiry horizon and alert when it falls below one renewal interval.
- **Lifecycle notifications trigger catch-up.** Set a lifecycle notification URL on Graph subscriptions and
  treat "missed" or "reauthorization required" as a signal to reauthorize and run reconciliation now. Monitor
  lifecycle events per tenant.
- **Dead-letter queue with replay.** After a bounded number of worker attempts, move the event to a DLQ with
  the error, and give operators a replay command. Monitor DLQ depth and age of the oldest message.
- **Dual secrets during rotation.** Accept signatures from the current and previous signing secret for a
  short overlap, then retire the old one. Monitor signature failures by tenant, which spike when a rotation
  goes wrong.

## Trade-offs
- **Freshness vs operational surface.** Webhooks cut lag to seconds and save API calls, but add a public
  endpoint, subscription lifecycle and secret management. Polling wins when changes are rare or the source
  has no webhooks.
- **Fat vs thin payloads.** Fat payloads avoid an API call per event but can be stale or out of order; thin
  payloads plus re-fetch cost a call each but always apply current state, and give the agent verified input.
- **Ack-before-process vs durability.** Acknowledging first keeps the sender happy but makes your queue the
  durability boundary; if the enqueue is not durable, an acknowledged event can be lost for good.
- **Per-tenant vs shared subscriptions.** Per-tenant subscriptions isolate failures and secrets but multiply
  renewal jobs; one shared app-level subscription is simpler when the source supports routing by tenant ID.
- **Agent per event vs batched runs.** One run per event gives the lowest latency; batching a burst of
  events for the same object (debouncing a few seconds) saves LLM cost and avoids several agents racing on
  one thread.

## Pitfalls
- **Calling an LLM or indexer inside the handler.** Symptom: sender timeouts, retries, and the same document
  embedded or the same reply drafted twice.
- **Verifying over re-serialized JSON instead of raw bytes.** Symptom: every request fails verification,
  after which someone disables the check.
- **No reconciliation job.** Symptom: a few documents permanently stale after an outage, found only when a
  user complains.
- **Letting subscriptions expire or get disabled after repeated failures.** Symptom: a tenant's freshness
  quietly drops to the reconciliation interval.
- **Check-then-mark dedup for non-idempotent side effects.** Symptom: occasional double replies or double
  tickets under load, when two workers race on the same retried event.
- **Trusting payload content as instructions or index text** without fetching from the source. Symptom:
  forged events inject attacker-controlled text into retrieval or straight into the agent's prompt.
- **Agent replies triggering their own events.** Symptom: the bot's reply fires a new message event and the
  agent answers itself in a loop. Fix: ignore events authored by your own bot identity.

## When to use
- **Use when** users or agents need changes within seconds and the source supports webhooks; keep a
  reconciliation sync regardless.
- **Prefer polling or delta sync alone** for low-change sources, sources without webhooks, or customers who
  will not allow inbound internet traffic (then consider a relay or a pull-based event API).
- **Measure:** end-to-end lag from source change to index or agent reply, duplicate rate, signature
  failures, queue depth, subscription expiry horizon, and how many changes reconciliation catches that
  webhooks missed.
- **Interviews:** name at-least-once delivery, idempotency, fast ack and reconciliation.

## Role lens
- **Engineer:** Writes verified, fast-ack receivers, idempotent consumers keyed by event ID, re-fetch logic
  and subscription renewal jobs with alerting on expiry.
- **Architect:** Decides webhook versus polling per source, where the durable queue sits, and the
  reconciliation cadence that bounds worst-case staleness.
- **FDE:** Gets inbound endpoints allowed through the customer's network policy, configures subscriptions in
  their tenant and proves freshness with a live edit during acceptance testing.

## Check yourself
**Q:** After a two-hour outage of your queue, a customer reports that some Jira tickets edited during the
outage still show old content in the assistant. Webhooks are back. Why, and how do you prevent it?
**A:** The sender retried for a while, then gave up, or your endpoint returned errors and those events were
never redelivered; webhooks are not a durable log you can replay at will. Nothing will re-send those
changes, so they stay stale until the item changes again. Prevention is a reconciliation job: an incremental
query for items updated since the last confirmed watermark, run periodically and after any incident, which
re-fetches and upserts them. Also alert on receiver error rate and queue health so outages trigger an
immediate catch-up sync.

## Interview questions
### What delivery guarantees do webhooks give, and how does that shape the receiver?
- **At-least-once, unordered, best-effort:** retries create duplicates, events can arrive out of order, and
  some are lost for good.
- **So the receiver** verifies, acks fast after a durable enqueue, processes idempotently, re-fetches current
  state and relies on reconciliation for completeness.

### The assistant drafted the same Slack reply twice for one message. How do you debug and fix it?
- **Check for retries:** `X-Slack-Retry-Num` on the second delivery usually means the handler was slow and
  missed the timeout.
- **Check the dedup:** a check-then-mark table lets two workers race on the same retried event.
- **Fix:** ack after enqueue only, and claim the side effect with an atomic insert-if-absent on the event ID
  before posting.

### When would you not use webhooks?
- **Low-change sources**, where polling or delta sync is cheap and simpler.
- **Sources without webhooks**, or customers who will not allow inbound internet traffic; use a relay or a
  pull-based event API instead.
- **Never as the only mechanism**, because a missed event stays wrong until the item changes again.

### How do you size the reconciliation interval?
- **It is your worst-case staleness** for a missed event, so pick it from the freshness the customer needs.
- **Cost:** tenants × containers × runs per day of incremental queries; 200 × 50 hourly is 240,000 calls a
  day, far below 5-minute polling.
- **Watch the catch rate:** many changes found by reconciliation means a subscription is broken, not that
  the interval is wrong.

### How does a multi-tenant receiver know which tenant an event belongs to?
- **From the credential:** the secret that verified the signature, or the stored subscription ID.
- **Never from the body**, because a tenant field in the payload is exactly what a forger controls.

### Design a webhook-triggered support agent that drafts a reply when a ticket enters the AI queue.
- **Receiver:** verify the signature, enqueue by event ID, return 2xx; no LLM work inline.
- **Worker:** create the run keyed by event ID, re-fetch the ticket and confirm it is still in the AI queue,
  run the agent with the ticket text marked as untrusted data, and claim the reply before posting.
- **Safety nets:** ignore events authored by the bot, reconcile tickets in the queue that have no run, and
  alert on runs per ticket above one.

## Quiz
### Q: Why should a webhook handler return 2xx quickly and process the event asynchronously?
- [ ] Senders reject responses that contain a body
- [x] Slow handlers hit the sender's timeout, which triggers retries and duplicate processing
- [ ] Asynchronous processing makes the signature valid for longer
- [ ] 2xx responses guarantee exactly-once delivery
> Senders retry when they do not get a timely success; doing LLM or indexing work inline causes timeouts and
> duplicates. Fast ack after durable enqueue keeps delivery reliable, though still at-least-once.

### Q: Two events for the same document arrive out of order. What handling is safest?
- [ ] Apply them in arrival order
- [ ] Drop the second event as a duplicate
- [x] Re-fetch the current object, or compare versions and ignore older updates
- [ ] Ask the sender to resend both in order
> Ordering is not guaranteed, so applying payloads as they arrive can let stale data win; fetching current
> state or version-checking makes the outcome independent of delivery order.

### Q: Why keep a reconciliation sync if webhooks are working?
- [ ] Webhooks cannot deliver deletions
- [ ] Reconciliation is required to verify signatures
- [x] Events can be lost during outages or expired subscriptions, and only a sync recovers them
- [ ] Webhooks only work for the first tenant
> Push delivery gives low latency but no completeness guarantee; a periodic incremental sync bounds how long a
> missed change can stay wrong.

## Use cases & projects
### Use cases
- **Ticket triage agents.** A Jira or ServiceNow event starts one agent run that drafts a reply or labels the
  ticket within seconds.
- **Slack and Teams bots.** Mentions arrive as events; the agent answers once per event and ignores its own
  messages.
- **Fresh RAG indexes.** Document-changed events trigger re-fetch and re-embedding, with hourly
  reconciliation bounding misses.
- **Workflow agents on business events.** A Stripe or CRM event (payment failed, deal closed) kicks off a
  follow-up agent with an exactly-once side effect.

### Mini project
**Exactly-once Slack reply agent (2-3 hours).**
- **Goal:** build a Slack bot whose agent answers each mention exactly once, even under retries.
- **Steps:** create a Slack app with the Events API; write a FastAPI receiver that verifies the signature on
  raw bytes, enqueues by `event_id` and returns 200; in a worker, start a run keyed by event ID, re-fetch the
  thread, call your LLM and claim the reply before posting; add a 5-second sleep in the handler once to force
  Slack retries.
- **Done when:** with forced retries the thread shows one reply, logs show the duplicate deliveries rejected,
  the bot never answers its own messages, and a forged request with a bad signature gets 401.
