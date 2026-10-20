---
id: webhook-signature-verification-and-replay-protection
module: backend-fundamentals
name: Webhook signature verification & replay protection
kind: practice
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - backend-fundamentals/api-and-sdk-integration
  - backend-fundamentals/message-queues
  - backend-fundamentals/secrets-management
  - backend-fundamentals/authentication-vs-authorization
  - enterprise-integration/webhooks
  - enterprise-integration/retry-deduplication-reconciliation
  - fastapi-for-ai/idempotency-keys
  - system-design/exactly-once-processing-limitations
sources:
  - IETF RFC 2104 — HMAC: Keyed-Hashing for Message Authentication
  - Official docs: Stripe webhooks
  - Official docs: Standard Webhooks specification
  - Official docs: Slack API (verifying requests)
---

## TL;DR
A webhook endpoint is a public URL that anyone on the internet can POST to, so the payload proves
nothing by itself. Providers therefore sign each delivery: an HMAC-SHA256 over a timestamp and the raw
body using a secret only you and they hold, which you recompute and compare in constant time. A
signature alone stays valid forever, so replay protection adds a timestamp window and deduplication by
event id, and because providers retry on any failure, the handler has to be idempotent anyway.

## Why it exists
`POST /webhooks/payments` is reachable from every machine on the internet. If the handler trusts the
body, an attacker sends `{"type": "payment.succeeded", "customer": "them"}` and gets the product for
free. An agent platform that receives Slack events will happily execute instructions from anyone who
can craft a request, which is prompt injection with a delivery mechanism. A pipeline waiting for a
provider's "batch job finished" event will ingest a forged result. The sender's identity has to come
from somewhere, and nothing in plain HTTP provides it: TLS authenticates your server to the sender, not
the sender to you.

The naive defences each fail in a specific way. A secret URL is a bearer token that leaks through access
logs, proxies, browser history and a screenshot in a support ticket, and rotating it means touching
every integration. An IP allowlist breaks because providers send from large, changing cloud ranges and
your own ingress may rewrite source addresses. A `"source": "billing"` field in the JSON is written by
whoever sends the JSON. Mutual TLS would settle the question, but few SaaS providers offer it for
webhooks. What
remains is a shared secret used to compute a message authentication code over the exact bytes
received: cheap, verifiable offline, and proof that whoever produced the message held the secret.

Signing stops forgery but not replay. A captured legitimate delivery is still correctly signed next
week, so "credit 100 units" can be resent until the attacker is satisfied. And the provider itself
redelivers on any non-2xx response or timeout, so duplicates are normal operation, not an edge case.
A timestamp inside the signed content bounds the window, an event id bounds duplicates inside it, and
an idempotent handler makes whatever slips through harmless.

## How it works
### What the provider signs
Each endpoint gets its own secret: Stripe calls it a signing secret, GitHub a webhook secret you type
into the settings page. On delivery the provider computes `HMAC-SHA256(secret, signed_content)` and
sends the result in a header. Schemes differ in what `signed_content` is. Stripe signs
`"{timestamp}.{raw_body}"` and sends `Stripe-Signature: t=<unix>,v1=<hex>`. The Standard Webhooks
convention (used by Svix and adopted by several vendors) signs `"{msg_id}.{timestamp}.{raw_body}"` and
sends `webhook-id`, `webhook-timestamp` and `webhook-signature` headers. GitHub signs the body alone in
`X-Hub-Signature-256: sha256=<hex>` with a separate `X-GitHub-Delivery` id and no timestamp. Slack signs
`"v0:{timestamp}:{raw_body}"` into `X-Slack-Signature`. The common core is an HMAC with a per-endpoint
secret over raw bytes. It is an HMAC rather than a bare hash *because* a hash of public bytes can be
computed by anyone; it is in a header rather than the body *because* the body is the thing being
authenticated. A few providers use asymmetric signatures with a published public key instead, which lets
many parties verify without any of them being able to forge.

### Verifying, in order
1. Read the raw bytes of the body before any framework parses it. Re-serialising parsed JSON changes
   whitespace, key order, float formatting and unicode escaping, so the MAC will not match.
2. Parse the header into a timestamp and one or more signatures; reject anything malformed.
3. Check `|now − timestamp| ≤ tolerance`. The tolerance is a few minutes (Stripe's libraries default to
   300 seconds), not zero, *because* clocks drift and deliveries queue.
4. Recompute the MAC over the provider's exact signed content and compare with `hmac.compare_digest`,
   never `==`. An ordinary comparison returns at the first differing byte, so response time leaks how
   many leading bytes of a guess were right; a constant-time compare removes that side channel. Accept
   the delivery if *any* supplied signature matches, because during secret rotation providers send one
   signature per active secret.
5. Only now parse the JSON and dispatch on the event type.

### Replay protection
The timestamp window turns "valid forever" into "valid for five minutes", which is still plenty for a
double credit. So deduplicate by event id: `SET webhook:seen:{id} 1 NX EX <ttl>` in Redis, or an insert
into a `processed_events(event_id UNIQUE)` table. Retention must cover the provider's retry horizon
(often days), not just the tolerance window, *because* legitimate retries arrive later with the same
id. Check whether your provider re-signs retries with a fresh timestamp; if it does not, the tolerance
has to cover its retry schedule as well. The robust version puts the uniqueness insert in the same
database transaction as the side effect, so either both commit or neither, and a crash between "marked
seen" and "credited" cannot lose an event. A Redis flag set before the work is cheaper but can mark an
event seen that then fails; that is the exactly-once limitation, and the cure is an idempotent handler
plus periodic reconciliation against the provider's API.

### Acknowledge fast, process later, trust the API over the payload
Providers time out deliveries after seconds to tens of seconds, retry on any non-2xx with backoff over
hours or days, and may disable an endpoint that keeps failing. So: verify, dedupe, enqueue the raw
event, return 200, and let a worker do the real work. Deliveries are not ordered (a `completed` can
arrive before `started`) and payload contents can be stale, so for anything that moves money or grants
access, fetch the current object from the provider's authenticated API by id and act on that. "Thin"
webhooks that carry only ids make this pattern the default.

### Secrets
The signing secret lives in your secret manager, per endpoint, never in code. It is symmetric: anyone
holding it can forge deliveries, so do not share one secret across services or environments, and rotate
with an overlap window during which both the old and the new secret are accepted.

## Example
A FastAPI receiver for a Stripe-style scheme: raw-body HMAC, timestamp tolerance, multiple signatures
for rotation, constant-time compare, Redis dedupe keyed by event id with retention longer than the
tolerance, and a hand-off to a queue so the provider gets its 200 in milliseconds. Test it with three
fixtures: a recorded good delivery, the same body with one byte changed, and the same delivery twice.

```python
import hashlib, hmac, json, os, time
from fastapi import FastAPI, HTTPException, Request
import redis.asyncio as redis

app = FastAPI()
SECRET = os.environ["WEBHOOK_SIGNING_SECRET"].encode()   # per-endpoint secret from the secret manager
TOLERANCE_S = 300                                        # clock skew + delivery delay we accept
SEEN_TTL_S = 3 * 24 * 3600                               # must cover the provider's retry horizon
r = redis.from_url(os.environ["REDIS_URL"])

def verify(raw: bytes, header: str) -> None:
    # Stripe-style header: "t=<unix>,v1=<hex>[,v1=<hex>]"; two v1 values appear during secret rotation
    fields = [p.split("=", 1) for p in header.split(",") if "=" in p]
    ts = next((v for k, v in fields if k == "t"), None)
    sigs = [v for k, v in fields if k == "v1"]
    if ts is None or not ts.isdigit() or not sigs:
        raise HTTPException(400, "malformed signature header")
    if abs(time.time() - int(ts)) > TOLERANCE_S:
        raise HTTPException(400, "timestamp outside tolerance")        # bounds replay to a short window
    expected = hmac.new(SECRET, f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    # constant-time compare on bytes: compare_digest raises TypeError on non-ASCII str input
    if not any(hmac.compare_digest(expected.encode(), s.encode()) for s in sigs):
        raise HTTPException(400, "bad signature")

@app.post("/webhooks/provider")
async def receive(request: Request):
    raw = await request.body()                     # the exact bytes that were signed, never re-serialised JSON
    verify(raw, request.headers.get("Provider-Signature", ""))
    event_id = json.loads(raw).get("id")
    if not event_id:
        raise HTTPException(400, "missing event id")
    key = f"webhook:seen:{event_id}"
    if not await r.set(key, 1, nx=True, ex=SEEN_TTL_S):
        return {"status": "duplicate"}             # 2xx: a retry or a replay, either way do nothing again
    try:
        await r.lpush("webhook:inbox", raw)        # ack fast; a worker fetches the object by id and acts
    except Exception:
        await r.delete(key)                        # un-mark so the provider's retry is not dropped
        raise
    return {"status": "queued"}
```

## Trade-offs
- An HMAC with a shared secret is simple, fast and verifiable offline, but it is symmetric: every holder
  can forge, so asymmetric signatures become the better choice when many parties must verify or the
  verifier must never be able to sign.
- A tight timestamp tolerance shrinks the replay window, but clock skew and queued deliveries then cause
  false rejections and more retries; widen it when the provider does not re-sign retries and lean on id
  deduplication instead.
- Dedupe in Redis with a TTL is one cheap command, but it is not transactional with your side effect; a
  unique constraint inside the same database transaction costs a write per event and buys a real
  effectively-once guarantee.
- Acknowledging immediately and processing in a worker frees you from provider timeouts, but a 200 no
  longer means "done", so monitoring, dead-lettering and reconciliation move to the queue.
- Fetching the authoritative object from the provider on every event defeats stale and out-of-order
  payloads, but it adds an API call per event and is subject to the provider's rate limits.

## Pitfalls
- Verifying over parsed-then-re-serialised JSON; the symptom is signatures that fail only for some
  payloads (non-ASCII text, floats, nested key order) while every local test passes.
- Comparing signatures with `==`; there is no functional symptom, which is exactly the danger, and it
  surfaces only in a pentest or security review.
- Checking the signature but neither the timestamp nor the event id; the symptom is double side effects
  (two credits, two emails) whenever the provider retries, and silent replayability.
- A gateway, body-parsing middleware or compression layer rewriting the body before your handler; the
  symptom is every signature failing after a deployment that changed nothing in your code.
- Doing the work inline and returning 200 only at the end; the symptom is provider-side timeouts,
  escalating retries and duplicate processing under load, sometimes ending with the endpoint disabled.
- Assuming ordered delivery; the symptom is a record stuck in `started` because a late `started` event
  overwrote `completed`.
- Rotating the secret without an overlap; the symptom is a burst of 400s and missed events during the
  switch.
- Logging full headers and bodies; the symptom is signatures, tokens and customer data sitting in the
  log platform for anyone with search access.

## When to use
Every inbound webhook, without exception, including events from your own services whenever they cross
a trust boundary; the cost is a few lines and the failure mode is an attacker driving your business
logic. If a sender cannot sign, require at least a secret header and prefer polling the provider's API
for state over trusting pushes. Measure the verification-failure rate (anything above zero is an attack
or a misconfiguration), the duplicate rate, the distribution of timestamp age, and processing lag. In
interviews, "how would you secure a webhook endpoint" is a standard question; a strong answer walks
raw-body HMAC, constant-time compare, timestamp window, id dedupe, idempotent handler, ack-then-queue
and fetching the authoritative object, and can say why each exists.

## Role lens
- **Engineer:** Writes the verification middleware on raw bytes, the dedupe and queue hand-off, and the
  three fixtures (good, tampered, duplicated) that keep it honest in CI.
- **Architect:** Defines the trust boundary for inbound events: which may trigger side effects, where the
  idempotency store lives, the retry horizon, and who owns and rotates each secret.
- **FDE:** Wires customer systems (Slack, GitHub, Salesforce, an in-house ERP) to the agent, checks each
  one's signing scheme, and agrees how secrets are exchanged and rotated inside the customer's security
  process.

## Check yourself
**Q:** A colleague says the billing webhook is already secure: it lives at a long random URL, the
handler checks that the JSON contains `"source": "billing"`, and the site is HTTPS-only. Explain what
each measure misses and what you would implement instead.
**A:** The random URL is a bearer secret that leaks through access logs, proxies and screenshots and
cannot be rotated without touching every integration. The `source` field is part of the body the
attacker writes. HTTPS encrypts in transit and proves your identity to the sender; it says nothing
about who sent the request. Instead: verify an HMAC-SHA256 over the timestamp and the raw body using a
per-endpoint secret from the secret manager, compare in constant time, reject timestamps older than a
few minutes, deduplicate on event id with retention covering the provider's retry schedule, make the
handler idempotent, return 200 quickly and process from a queue, and for anything that grants value,
fetch the current object from the billing API by id before acting.

## Quiz
### Q: Why must the HMAC be computed over the raw request bytes rather than over the parsed JSON object?
- [ ] Parsing JSON is too slow to do before authentication
- [x] Serialisation is not canonical, so re-encoded JSON can differ byte for byte from what the provider signed
- [ ] Raw bytes include the headers, which are part of the signature
- [ ] JSON objects cannot be hashed because they are unordered
> The provider signed a specific byte sequence. Whitespace, key order, escaping and number formatting
> can all change on a parse-and-dump round trip, producing a different MAC even though the data is
> "the same".

### Q: A delivery arrives with a valid signature and a timestamp two minutes old, but its event id is already in your seen-set. What should the handler do?
- [ ] Return 400 so the provider stops sending it
- [ ] Process it again, because the signature proves it is genuine
- [x] Return 2xx without running the side effect again
- [ ] Return 500 so the provider retries later with a fresh timestamp
> Whether it is a provider retry or an attacker's replay, the right action is the same: acknowledge and
> do nothing. A non-2xx would only trigger more retries of an event you have already handled.

### Q: What does a constant-time comparison such as `hmac.compare_digest` protect against that `==` does not?
- [ ] SHA-256 hash collisions
- [ ] Length-extension attacks on the hash
- [x] A timing side channel where early exit reveals how many leading bytes of the guess were correct
- [ ] Replay of a previously valid signature
> Ordinary string comparison stops at the first mismatch, so response time correlates with the number
> of correct leading bytes. Length-extension is already prevented by HMAC's construction, and replay is
> handled by timestamps and ids, not by the comparison.
