---
id: webhooks
module: enterprise-integration
name: Webhooks
kind: pattern
difficulty: core
est_minutes: 15
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
A webhook is an HTTP POST a source system sends to your endpoint when something changes, so you learn about
changes in seconds without polling. Delivery is at-least-once, unordered and from the public internet, so a
correct receiver verifies the signature, acknowledges fast, queues the event, processes it idempotently,
treats the payload as a hint to re-fetch current state, and runs periodic reconciliation for the events that
never arrive.

## Why it exists
An AI assistant indexed over Jira, Slack or SharePoint is only as fresh as its last sync. Polling every few
minutes for every tenant and every container multiplies API calls by tenants × containers × polls, most of
which return "nothing changed", and still leaves minutes of staleness. For agent workflows ("when a ticket
is assigned to the AI queue, draft a reply") polling also adds latency the user feels.

Webhooks invert the direction: the source tells you when something happens. The naive receiver, though,
does the work inside the HTTP handler, trusts the body, and assumes each event arrives exactly once and in
order. All three assumptions are false. The handler times out while calling an LLM, so the sender retries and
you process the event twice. Anyone who discovers the URL can post fake events, for example injecting a
forged "document updated" event with attacker text into your index. Events for the same object arrive out
of order, so an older "updated" overwrites a newer state. And senders drop events during outages on either
side, or disable subscriptions that fail too often. The webhook pattern is the set of habits that make an
unreliable push channel safe to build on.

## How it works
### 1. Subscribe
Register your URL with the source, per tenant, for specific event types. Some sources confirm ownership
first: Slack sends a `url_verification` challenge you must echo; Microsoft Graph sends a `validationToken`
that you return as plain text, and Graph subscriptions expire, so you must renew them on a schedule.
Store subscription IDs and expiry, *because* an expired subscription fails silently: nothing arrives.

### 2. Verify
Reject anything not provably from the sender. Most use an HMAC of the raw body (often with a timestamp)
under a shared secret; some use a client-state token you chose at subscription time, or mutual TLS.
Compare in constant time and reject old timestamps to limit replay. The details belong to the
signature-verification lesson; the integration rule is: verify the **raw bytes** before parsing.

### 3. Acknowledge fast, process later
Return 2xx within the sender's timeout (often a few seconds) after verifying and durably enqueueing,
*because* any slow work in the handler causes timeouts, retries and duplicates.

### 4. Process idempotently
Use the sender's event ID (or a hash of the payload) as a deduplication key in the consumer. Senders retry
on non-2xx and sometimes even after success, so duplicates are normal.

### 5. Treat payloads as hints
Many events are "thin" (an ID and a type). Even fat payloads may be stale by the time you process them.
Re-fetch the current object from the API and apply it, or compare a version or modified timestamp and
discard older ones, *because* ordering is not guaranteed.

### 6. Reconcile
Run a periodic incremental sync (delta query, `updated since` watermark) that catches whatever the
webhooks missed. Webhooks give latency; reconciliation gives correctness. Alert when reconciliation finds
many changes webhooks did not deliver, since that usually means a broken subscription.

## Example
A FastAPI receiver for Slack Events API requests: verify the signature over the raw body, answer the URL
challenge, enqueue and return immediately.

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
    if not await dedup.first_time(msg.key):                  # event_id seen before: retry duplicate
        return
    ev = msg.payload["event"]
    if ev["type"] == "message":
        current = await slack.conversations_replies(ev["channel"], ev.get("thread_ts") or ev["ts"])
        await indexer.upsert_thread(msg.payload["team_id"], ev["channel"], current)  # re-fetched state
```

The consumer re-fetches the whole thread rather than trusting the single message in the event, which also
handles edits and out-of-order delivery for that thread.

## Trade-offs
- Webhooks cut freshness lag to seconds and save API calls, but add a public endpoint, subscription
  lifecycle and secret management; polling wins when changes are rare or the source has no webhooks.
- Fat payloads avoid an API call per event, but can be stale or out of order; thin payloads plus re-fetch
  cost a call each but always apply current state.
- Acknowledging before processing keeps the sender happy, but makes your queue the durability boundary; if
  the enqueue is not durable, an acknowledged event can be lost for good.
- Per-tenant subscriptions isolate failures and secrets, but multiply renewal jobs; one shared app-level
  subscription is simpler when the source supports routing by tenant ID.

## Pitfalls
- Calling an LLM or indexer inside the handler; the symptom is sender timeouts, retries and the same
  document embedded or the same reply drafted twice.
- Verifying the signature over re-serialized JSON instead of raw bytes; the symptom is every request failing
  verification, after which someone disables the check.
- No reconciliation job; the symptom is a few documents permanently stale after an outage, found only when
  a user complains.
- Letting subscriptions expire or get disabled after repeated failures; the symptom is a tenant's freshness
  quietly dropping to the reconciliation interval.
- Trusting payload content as instructions or index text without fetching from the source; the symptom is
  forged events injecting attacker-controlled text into retrieval.

## When to use
Use webhooks when users or agents need changes within seconds and the source supports them; keep a
reconciliation sync regardless. Prefer polling or delta sync alone for low-change sources, sources without
webhooks, or customers who will not allow inbound internet traffic (then consider a relay or a pull-based
event API). Measure end-to-end lag from source change to index, duplicate rate, signature failures, queue
depth, subscription expiry horizon, and how many changes reconciliation catches that webhooks missed.
In interviews, the expected answer names at-least-once delivery, idempotency, fast ack and reconciliation.

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
