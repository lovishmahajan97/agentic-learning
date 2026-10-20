---
id: retry-deduplication-reconciliation
module: enterprise-integration
name: Retry / deduplication / reconciliation
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
related:
  - enterprise-integration/webhooks
  - enterprise-integration/incremental-synchronization-and-cdc
  - serving-inference/retry-strategies
  - serving-inference/idempotency
  - system-design/outbox-inbox-patterns
  - system-design/exactly-once-processing-limitations
  - agent-reliability/idempotent-tool-execution
  - backend-fundamentals/message-queues
sources:
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Hohpe & Woolf, 2003 — Enterprise Integration Patterns
  - Official docs: Stripe API (idempotent requests)
---

## TL;DR
An integration with a customer's systems crosses a boundary you do not control: calls fail, writes
succeed without telling you, and events never arrive. **Retry** handles the first, **deduplication**
makes retries safe against the second, and **reconciliation**, the scheduled comparison of source and
target, catches the third. Each covers a failure the other two cannot see, which is why mature
integrations run all three and treat the reconciliation job's repair count as the health metric of the
whole pipeline.

## Why it exists
Take a concrete integration: your assistant ingests ServiceNow incidents into a retrieval index through
webhooks and writes resolution notes back through the REST API. Three things go wrong in a normal week,
none of them your bug.

First, calls fail transiently. The customer's instance returns 429 because your sync shares the API
quota with their own users, a 503 during their maintenance window, or a connection reset through their
proxy. These need a retry, and a careful one: synchronised retries from every worker become a thundering
herd that throttles the customer's own staff, which is the fastest way to get an integration switched
off.

Second, outcomes are ambiguous. A write times out after the customer's server committed it but before
the response reached you. Retrying creates a second resolution note; not retrying may lose the first.
No retry policy fixes this; only a key that lets the target recognise the repeat does.

Third, losses are silent. A webhook delivery fails and the customer's platform exhausts its own retry
budget; a CDC connector is down for forty minutes; a record changes between two pages of a paginated
pull and lands on neither; a deletion produces no event at all. Your system has no record of an event
it never received, so no amount of retrying or deduplication helps. The only way to find the gap is to
compare what the source has with what you have.

The naive alternatives each fail on one leg. "Retry until success" creates duplicates and retry storms.
"Trust the event feed" accumulates drift that surfaces weeks later as a confident answer about a ticket
that was closed. "Re-sync everything nightly" is expensive, re-embeds unchanged documents, and still does
nothing about duplicated writes. Retry, deduplication and reconciliation are three mechanisms for three
distinct failures, and the integration is only correct with all three.

## How it works
### Retry: classify, then back off
Not every error should be retried. Retryable: 408, 429, 5xx, connection resets and timeouts (where the
request may nevertheless have succeeded). Not retryable: 400 and 422 (the payload is wrong and will be
wrong again), 401 once a token refresh has been tried, 403 (a permission the customer must grant), 404
on a record that was deleted at source. Retrying a non-retryable error wastes quota and hides a bug
behind a delay.

For the retryable class the delay grows exponentially with jitter and is capped, `Retry-After` is
honoured when the platform sends it, and both attempts and total elapsed time are bounded. Two
enterprise-specific additions matter. A **retry budget** per integration (for example, retries may be at
most a small percentage of calls over a rolling window) prevents an outage at the customer from turning
your workers into a load generator against the instance their employees share. A **circuit breaker** per
endpoint stops calling an instance that has been failing and probes it occasionally, *because* SaaS API
quotas are commonly metered per customer instance per day, so a burst of failed calls during an incident
can lock the customer's own integrations out until the quota resets.

Reads can be retried freely. Writes can be retried only when the target can detect a repeat, which is
the next mechanism. The serving module's retry lesson covers the backoff arithmetic; this lesson is about
which calls are safe to retry at all.

### Deduplication: make at-least-once look like once
Why deduplication is unavoidable: a sender that gets no acknowledgement cannot tell "the request was
lost" from "the request was applied and the reply was lost". It must either resend (at-least-once, with
possible duplicates) or give up (at-most-once, with possible loss). No protocol removes that ambiguity
over an unreliable network, so "exactly once" is never a property of delivery; it is an *effect* you
build from at-least-once delivery plus a receiver that recognises repeats. Every reliable feed therefore
duplicates, and the only design question is where the repeat is detected.

Duplicates arrive from two directions.

**Inbound events** (webhooks, CDC, polled changes) are delivered at least once by every reliable
mechanism, and sometimes out of order. The dedupe key is either the delivery's own id or, better,
`(tenant, record_id, source_version)`, where the version is a sequence number or the source's
last-modified stamp. The consumer stores the last applied version per record *in the same transaction*
as the effect (the inbox pattern) and rejects anything whose version it has already applied or exceeded.
Keying on the version rather than the delivery id handles both redelivery and reordering: a stale update
that arrives after a newer one is dropped instead of overwriting it. Keys are scoped per tenant because
two customers' systems can legitimately produce the same record id.

The choice of key also decides how long the dedupe state must live. A store of seen *delivery ids* only
works if it remembers each id for longer than the sender's redelivery window (the source's retry
schedule, plus any manual "replay events" button an admin can press), so it needs a TTL chosen from that
window and grows with traffic; a redelivery after the TTL is applied twice. A *last-applied version per
record* is bounded by the number of records, never expires, and rejects an arbitrarily late replay,
which is another reason to prefer it whenever the source provides a version.

**Outbound writes** need the target to recognise a repeat. In order of preference: an idempotency key
header where the API supports one (payment and messaging APIs usually do; most enterprise SaaS APIs do
not); an *upsert on an external id* field you control, so a repeated write with the same external id
updates instead of inserting; and, as the fallback, your own **operation ledger**: claim an operation id
with status `unknown` before calling, record the remote id after, and never issue a write the ledger does
not know about. The ledger has a race window (a crash between the remote commit and your record), which
is exactly what reconciliation closes.

**Content duplicates** are the third kind: the same policy document uploaded to two SharePoint sites, or
the same attachment on three tickets. Deduplicate by a hash of the normalised content before chunking and
embedding, otherwise retrieval returns the same passage three times and crowds out everything else.

### Reconciliation: assume the first two leak
Reconciliation is a scheduled job that compares source and target directly and repairs the difference.
It runs in tiers *because* full comparison is expensive: first compare *counts* per partition (records by
day of last modification), then the *set of ids and versions* for partitions that disagree or fall inside
a recent window, then *field-level content hashes* for ids whose versions match but which are suspected
of partial writes. The output is a diff with three classes: missing in target (fetch and apply), stale in
target (version behind; re-fetch), and extra in target (deleted at source without an event; tombstone
and remove from the index). Deletes only show up under a full-id sweep, so the job keeps a slow
background sweep over everything alongside the frequent window over the last few days.

Repairs go through the same idempotent apply path as live events, which makes a reconciliation run safe
to retry and safe to overlap with the live feed. The job also closes the outbound ledger: every operation
still marked `unknown` is resolved by querying the target for its external id, then recorded or
re-issued.

The numbers the job emits are the health signal of the whole integration. A healthy feed repairs a
handful of records per run; a rising repair count means the event path is broken (an expired webhook
subscription, a filter that silently excludes a record type, a stopped connector), usually well before
any user notices. The reconciliation report is also the artefact you show the customer when they ask
whether your copy matches their system of record.

### How the three fit
Live path: event arrives → dedupe by `(tenant, record, version)` → apply and commit the version together.
Write path: claim a ledger entry → call with retry → record the result. Audit path: reconciliation
compares source and target on a schedule, repairs through the apply path, resolves unknown ledger
entries, and reports drift. The first two make the system fast and mostly right; the third makes it
eventually exactly right and tells you when the first two have failed.

## Example
The sketch below puts the three mechanisms around one customer integration. The stores (`versions`,
`records`, `ledger`) stand in for tables committed in the same transaction as the effect; `client` wraps
the customer-side API. Each function is safe to call twice, including `reconcile` itself.

```python
import random, time

class ApiError(Exception):
    def __init__(self, status, retry_after=None): self.status, self.retry_after = status, retry_after

RETRYABLE = {408, 429, 500, 502, 503, 504}

def call_with_retry(fn, max_attempts=5, base=0.5, cap=30.0):
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except (ApiError, TimeoutError, ConnectionError) as e:
            status = getattr(e, "status", None)         # None: timeout/reset, outcome unknown
            if (status is not None and status not in RETRYABLE) or attempt == max_attempts:
                raise                                   # 4xx: wrong request, do not loop on it
            delay = min(cap, base * 2 ** (attempt - 1)) * random.uniform(0.5, 1.5)
            time.sleep(getattr(e, "retry_after", None) or delay)  # honour Retry-After if sent

def apply_event(db, tenant, event):
    """Inbound: idempotent by (tenant, record id, source version); version committed with the write."""
    key = (tenant, event["record_id"])
    seen = db.versions.get(key)
    if seen is not None and event["version"] <= seen:
        return "duplicate-or-stale"                     # redelivery or out-of-order: no-op
    db.records[key] = event["payload"]
    db.versions[key] = event["version"]
    return "applied"

def write_with_ledger(db, client, tenant, op_id, payload):
    """Outbound: never issue a write the ledger does not know about."""
    if db.ledger.get(op_id) not in (None, "unknown", "needs-replay"):
        return db.ledger[op_id]                         # retry of a completed op returns the saved result
    db.ledger[op_id] = "unknown"                        # claimed before the call; survives a crash mid-call
    # Re-issuing an "unknown" op is safe only because upsert-by-external-id turns a repeat into an update.
    # Against an insert-only API, resolve "unknown" by lookup (as reconcile does) before any re-issue.
    remote_id = call_with_retry(lambda: client.upsert(external_id=op_id, body=payload))
    db.ledger[op_id] = remote_id
    return remote_id

def reconcile(db, client, tenant, since):
    """Catch what retry and dedupe cannot: events that never arrived, writes with unknown outcomes."""
    source = {r["id"]: r["version"] for r in client.list_changed(since=since)}   # paginated pull
    target = {rid: v for (t, rid), v in db.versions.items() if t == tenant}
    missing = [rid for rid in source if rid not in target]
    stale = [rid for rid, v in source.items() if rid in target and v > target[rid]]
    for rid in missing + stale:
        apply_event(db, tenant, client.fetch(rid))      # repairs reuse the idempotent path
    for op_id, status in list(db.ledger.items()):
        if status == "unknown":
            db.ledger[op_id] = client.find_by_external_id(op_id) or "needs-replay"
    return {"missing": len(missing), "stale": len(stale)}  # alert when this stops trending to zero
```

Deletes are not in this sketch: `list_changed` only sees records that still exist, so a separate full-id
sweep (or the platform's deleted-records endpoint) is what produces the "extra in target" class.

## Trade-offs
- Aggressive retries raise the chance that a transient failure heals itself, but they consume the
  customer's shared API quota; past a modest fraction of calls (the Google SRE book's example per-client
  budget is retries below 10% of requests) extra retries mostly add load to an instance that is already
  struggling, which is why a retry budget exists.
- Version-based dedupe handles reordering and redelivery, but it needs a trustworthy version from the
  source; when the source only offers a timestamp with second resolution, two edits in the same second
  collapse and you must fall back to comparing content hashes.
- Reconciliation buys certainty at the price of read load on the customer's system and a second code
  path to maintain; a narrow frequent window plus a slow full sweep is the usual balance, and the window
  widens only when measured drift shows the event path is unreliable.
- An operation ledger makes outbound writes safe to retry but adds a database write before every call;
  for high-volume, low-value writes (metrics, heartbeats) the cost is not worth it and an occasional
  duplicate is accepted instead.

## Pitfalls
- Acknowledging a webhook with 200 before the internal write is durable; symptom: the customer's delivery
  logs show success, the feed looks healthy, and reconciliation keeps finding the same class of record
  missing.
- Deduplicating by delivery id only; symptom: a stale update that arrived late overwrites a newer one and a
  ticket flips from closed back to open in your index.
- Retrying 4xx errors; symptom: a validation error loops through the full backoff schedule, burning
  quota and delaying every other call in the queue, with a real bug hidden behind what looks like a slow
  dependency.
- Treating the reconciliation output as a log nobody reads; symptom: the repair count has climbed for
  weeks because a webhook subscription expired, and no alert was ever set on it.
- Giving a delivery-id dedupe store a TTL shorter than the source's redelivery window; symptom: duplicate
  notes or double-applied updates that cluster after a source outage or an admin's bulk "resend
  events", exactly when redeliveries arrive late.
- Comparing source timestamps with timestamps generated on your side (different clocks, different time
  zones); symptom: records are perpetually judged stale and re-fetched every run, so the repair count
  never reaches zero.

## When to use
All three apply whenever your system holds a copy of customer data or writes into a customer system,
which is almost every enterprise AI deployment: a RAG index over their documents, an agent that files
tickets, a sync of CRM records into a feature table. Size them to the stakes. A read-only index over a
wiki can run a modest retry policy, content-hash dedupe and a nightly reconciliation. An agent that
writes into a system of record needs an operation ledger, upsert-by-external-id and a reconciliation
job that resolves every unknown outcome within hours. Measure retry rate, duplicate rate and repair
count per run; the last one is the one to alert on. In interviews, the expected depth is to explain why
exactly-once delivery is impossible, why dedupe therefore has to be keyed and committed with the effect,
and why reconciliation is still required afterwards.

## Role lens
- **Engineer:** Writes the error classifier, the inbox check keyed on source version, the ledger around
  every outbound write, and the tiered reconciliation job with its repair metrics and alerts.
- **Architect:** Decides which writes need a ledger and which may tolerate duplicates, sets retry budgets
  per customer instance, and owns the reconciliation SLA that the parity promise to the customer rests on.
- **FDE:** Negotiates with the customer's admin for an external-id field, a changes endpoint and a
  workable API quota, and uses the reconciliation report as the evidence that the integration is correct.

## Check yourself
**Q:** A customer's Jira webhook feed into your RAG index has run for six months with retries and dedupe
in place. A user reports that an issue closed three weeks ago still appears open in the assistant's
answers. How did this happen, and what do you change?
**A:** Retries and dedupe only act on events your system saw. The close event almost certainly never
arrived or was lost after acknowledgement: Jira's delivery to your endpoint failed and its own retries
were exhausted during an outage, or your endpoint returned 200 before the write was durable and then
crashed. Nothing in your system records an event it never processed, so the index drifted silently. The
fix is reconciliation: an hourly job that pulls issues updated in the last few days from Jira's search
API, compares ids and `updated` versions against the index, and repairs through the same apply path,
plus a slow full sweep that catches deletions and anything older. Alert on the repair count per run, and
move the webhook acknowledgement to after the durable write.

## Quiz
### Q: An integration already retries failed calls and deduplicates events by record id and version. Why does it still need a reconciliation job?
- [ ] To re-run retries that exceeded the attempt cap
- [x] Because events that were never delivered leave no trace in the target, and only comparing against the source reveals them
- [ ] To compress the dedupe store once the TTL expires
- [ ] Because retries are only safe for reads, so writes must be replayed nightly
> Retry and dedupe act on events the system received. A lost webhook, a connector outage or a delete
> with no event produce nothing to retry or dedupe; a source-versus-target comparison is the only
> mechanism that can see the gap.

### Q: A write to the customer's API timed out. Which approach is correct?
- [ ] Do not retry, because the write may have succeeded and a second attempt would duplicate it
- [ ] Retry immediately, because timeouts almost always mean the request never arrived
- [x] Retry only through a path the target can recognise as a repeat: an idempotency key, an upsert on an external id, or a ledger entry claimed before the call
- [ ] Retry after confirming by reading the record, which guarantees exactly one write
> A timeout is ambiguous, so the retry must be recognisable as a repeat by the target. A read-then-write
> check still has a race window between the read and the write; a ledger plus reconciliation closes it.

### Q: Every nightly reconciliation run reports the same 40 records as stale in the target, and each run "repairs" them. What is the most likely cause?
- [ ] The customer's API is rate-limiting the reconciliation job, so it cannot read the source
- [ ] The dedupe store's TTL is too short, so old events are being re-applied
- [x] The repair is not converging, for example the diff compares source versions against timestamps generated on your side, or the apply path rejects the re-fetched records
- [ ] The 40 records are duplicates in the source system
> A healthy job's repair set changes between runs and trends to zero. The same set recurring means the
> diff is detected but the repair never makes source and target compare equal, typically because the
> two sides are not compared like for like; monitor the job's repair outcome, not just its diff.
