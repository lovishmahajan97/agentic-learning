---
id: data-ingestion-from-client-systems
module: enterprise-integration
name: Data ingestion from client systems
kind: pattern
difficulty: core
est_minutes: 16
related:
  - data-architecture/ingestion-pipelines
  - enterprise-integration/incremental-synchronization-and-cdc
  - enterprise-integration/enterprise-connectors
  - enterprise-integration/webhooks
  - enterprise-integration/retry-deduplication-reconciliation
  - enterprise-integration/legacy-system-integration
  - deployment/private-endpoints
  - scoping/data-access-and-permissions-discovery
sources:
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Hohpe & Woolf, 2003 — Enterprise Integration Patterns
---

## TL;DR
Ingesting from a client's systems means pulling data you do not own, through interfaces the client
chooses, across a network boundary they control, from systems their business runs on. The durable
design picks the least invasive channel the client can offer (API, replica, file drop, events or CDC),
lands an immutable raw copy, advances a checkpoint only after a successful write, throttles to protect
the source, and reconciles because some changes, especially deletes, are invisible to the channel.

## Why it exists
Inside your own company, ingestion is an engineering problem. At a client it is also a negotiation: the
data sits in their CRM, ERP, ticketing system or file shares; their security team decides which network
paths exist; their platform team decides whether you may touch a database at all; and their operations
team will notice if your job slows the system their staff use all day. You rarely get the interface you
would design. You get what they can approve, often a REST API with rate limits, a nightly CSV on SFTP, a
read replica, or an on-premises system reachable only from an agent inside their network.

The naive approach is a script that calls the API, paginates through everything, and writes straight
into the index, run on a schedule. It breaks in a characteristic order. Rate limits return 429s and the
script dies halfway, so the next run starts over. A full re-pull each night takes longer than the night
as volume grows. Records updated during the run are skipped or duplicated because pagination shifted
under it. Deleted records stay in the index forever because a full pull of "what exists" says nothing
about what stopped existing unless you diff it. And because raw responses were never kept, a parser
bug found a month later means re-pulling everything from a source that took weeks to get access to.

The pattern below exists to make ingestion resumable, incremental, gentle on the source, and replayable
without going back to the client.

## How it works
For the generic landing-zone and pipeline design, see the data-architecture ingestion lesson; this
lesson covers what changes when the source belongs to a client.

### 1. Choose the channel from what the client can offer
- **Vendor API pull** (Salesforce, ServiceNow, Zendesk, Graph for SharePoint): the usual default because
  it respects the vendor's permission model and is supported. Costs: rate limits, pagination quirks,
  and per-object API shapes.
- **Database read replica or read-only views:** fastest for bulk data and allows SQL filtering, but
  couples you to an internal schema that can change without notice; ask for views as a stable contract.
- **File drop** (SFTP, a shared object-storage bucket): common for legacy systems and for teams that
  cannot grant live access; simple and auditable, but batch-only and prone to format drift.
- **Events or webhooks:** low latency, but delivery is at-least-once and can be lost, so it needs a
  periodic pull or reconciliation behind it.
- **CDC from the database log:** captures inserts, updates and deletes in order with low source load, but
  needs DBA cooperation and connector infrastructure, so it is usually a phase-two upgrade.

### 2. Decide where the connector runs
If the source is only reachable inside the client network, run a small agent there that pushes outbound
to you, *because* outbound connections usually pass security review far more easily than inbound ones;
otherwise use private connectivity (private endpoints, VPN). Credentials are a dedicated service
account or OAuth client with read-only scope, stored in a secrets manager and rotated.

### 3. Backfill, then incremental
A historical backfill runs once, in bounded batches, off-peak, resumable per batch. Incremental runs
then fetch only what changed since a **watermark**, typically a `modified_at` timestamp or a sequence
number from the source.

### 4. Make the watermark safe
Timestamps lie at the boundaries: several records can share the watermark's exact timestamp, clocks
skew, and long transactions commit with earlier timestamps. So query from `watermark - overlap`, accept
that some records arrive twice, and deduplicate by `(source_id, version or content_hash)`. Advance the
watermark only *after* the batch is durably written, so a crash re-reads rather than skips. This is the
whole reason ingestion can be at-least-once without being wrong.

### 5. Land raw, immutably
Write each source record as received to a raw store, keyed by source system, object type, ID and content
hash, before any parsing or mapping. Everything downstream (mapping, parsing, chunking, embedding) can
be re-run from raw without re-contacting the client, which matters because access is slow to get and
downstream logic will change.

### 6. Protect the source
Honour rate limits and `Retry-After`, cap concurrency, schedule heavy work outside business hours,
prefer replicas over primaries, and expose a client-side switch to pause ingestion. Being a good
neighbour is what keeps your access approved.

### 7. Catch what the channel misses
Polling by `modified_at` does not see hard deletes and some permission changes. Use the source's
deleted-items endpoint or tombstones where it has one, CDC where available, and otherwise a periodic ID
reconciliation comparing source and target key sets.

## Example
An incremental pull from a paginated REST API with a safe watermark, backoff on rate limits, raw
landing and deduplication. `api`, `raw_store` and `state` stand in for your HTTP client, object store and
checkpoint table.

```python
import time, hashlib, json
from datetime import datetime, timedelta

OVERLAP = timedelta(minutes=10)

def get_with_backoff(api, path, params, max_tries=6):
    for attempt in range(max_tries):
        r = api.get(path, params=params, timeout=30)
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(wait, 120)); continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"gave up on {path} after {max_tries} tries")

def sync_tickets(api, raw_store, state, tenant):
    wm = datetime.fromisoformat(state.get(tenant, "tickets_wm", "1970-01-01T00:00:00+00:00"))
    since, newest, cursor = (wm - OVERLAP).isoformat(), wm, None
    while True:
        params = {"updated_since": since, "page_size": 200, "order": "updated_at"}
        if cursor: params["cursor"] = cursor
        page = get_with_backoff(api, "/tickets", params)
        for rec in page["items"]:
            body = json.dumps(rec, sort_keys=True).encode()
            key = f"{tenant}/tickets/{rec['id']}/{hashlib.sha256(body).hexdigest()[:16]}.json"
            if not raw_store.exists(key):          # dedupe the overlap window
                raw_store.put(key, body)
            newest = max(newest, datetime.fromisoformat(rec["updated_at"]))
        cursor = page.get("next_cursor")
        if not cursor: break
    state.set(tenant, "tickets_wm", newest.isoformat())  # advance only after all writes succeeded
```

The overlap re-reads ten minutes of records each run; the content-hash key makes those re-reads no-ops,
and an unchanged record never produces a second raw object. A crash mid-run leaves the watermark where
it was, so the next run resumes. Note what this cannot see: a ticket hard-deleted in the source never
appears in `updated_since`, which is why a separate reconciliation job compares ID sets.

## Trade-offs
- API pull respects the vendor's permission model and is supported, but rate limits make large backfills
  slow; a read replica flips the balance for bulk history when the client's DBAs will provide one.
- Polling by watermark is simple and works with almost any API, but adds latency equal to the interval
  and misses deletes; events or CDC win once freshness or delete propagation becomes a requirement.
- An agent inside the client network reaches on-premises systems and passes security review more
  easily, but you now operate software in an environment you cannot log into freely, so upgrades,
  monitoring and support get harder.
- Keeping an immutable raw copy makes every downstream fix replayable, but stores sensitive client data
  a second time; retention, encryption and deletion propagation must cover the raw zone too.
- File drops are the easiest channel to get approved, but you inherit the export job's failures, such as
  partial files, changed column orders and missed nights, so validate each file before loading it.

## Pitfalls
- Advancing the watermark before writes are durable; the symptom is a small, permanent gap of records
  that were fetched during a crash and never re-fetched.
- Using a strict `>` comparison on a timestamp watermark with no overlap; the symptom is occasional
  missing records that share a timestamp with the previous run's last record.
- Offset pagination over a set that changes during the run; the symptom is records skipped or seen twice
  when inserts shift page boundaries, fixed by cursor or keyset pagination ordered on a stable key.
- Ignoring `Retry-After` and retrying immediately; the symptom is the client's API gateway throttling or
  blocking your service account, sometimes for the whole tenant's integrations.
- Writing straight to the index with no raw copy; the symptom is that a parser or mapping fix requires a
  full re-pull, which can take days against a rate-limited API.
- Assuming polling sees deletes; the symptom is answers citing documents or records the customer deleted
  weeks ago, often found by the customer.

## When to use
Use this pattern for any recurring pull from a system you do not own. Start by asking the client what
they can approve, not by proposing your ideal: an API with a service account is usually fastest to get,
a file drop is the fallback for legacy systems, and CDC is worth requesting once freshness or deletes
matter. Measure lag (source change to landed), records per run, retry and 429 rates, raw-versus-target
counts from reconciliation, and run duration against the window you have. In interviews, the tell of
experience is mentioning the watermark overlap, advancing the checkpoint after the write, and deletes.

## Role lens
- **Engineer:** Writes connectors with cursor pagination, backoff that honours `Retry-After`, overlap
  watermarks and content-hash deduplication into an immutable raw zone.
- **Architect:** Chooses the channel per source, where connectors run relative to the client network, and
  how raw data is retained, encrypted and deleted across the boundary.
- **FDE:** Negotiates service accounts, network paths and off-peak windows with the client's security and
  platform teams, and is the first to hear when ingestion load annoys their operations staff.

## Check yourself
**Q:** Your nightly sync uses `updated_at > last_watermark` against a client API. Users report a few
missing tickets each week and some deleted tickets still being cited. Diagnose both and fix them.
**A:** Missing tickets come from the strict comparison at the boundary: records sharing the watermark
timestamp, clock skew, or late-committing transactions fall behind it and are never re-read. Fix by
querying from the watermark minus an overlap, deduplicating by ID plus version or content hash, and
advancing the watermark only after writes succeed. Deleted tickets persist because a `updated_at`
query cannot return records that no longer exist. Fix with the API's deleted-records endpoint if it has
one, CDC if the client can provide it, or a scheduled reconciliation that diffs source and target ID sets
and tombstones the difference through to the index.

## Quiz
### Q: Why should the watermark be advanced only after the batch has been durably written?
- [ ] Because APIs reject requests whose watermark is in the future
- [x] Because a crash between fetch and write would otherwise skip those records permanently
- [ ] Because writing first makes the batch smaller
- [ ] Because the watermark must equal the earliest record in the batch
> If the checkpoint moves first and the write then fails, the next run starts after records that were
> never stored. Advancing after the write makes failures re-read instead of skip.

### Q: A client's security team will not allow any inbound connection to their on-premises database. What is the usual design?
- [ ] Ask for a nightly full database dump emailed to you
- [ ] Use the vendor's public API from your cloud regardless of the policy
- [x] Run a connector agent inside their network that makes outbound connections to your service
- [ ] Wait for them to migrate the database to the cloud
> Outbound-only agents are a common, reviewable compromise: no inbound ports open, the client controls
> the host, and data is pushed to you over a channel their security team can monitor.

### Q: What does a timestamp-watermark poll fundamentally fail to capture?
- [ ] Records created after the last run
- [ ] Records whose text changed but whose metadata did not
- [ ] Records returned on the second page of results
- [x] Records that were hard-deleted in the source
> A query for recently modified records can only return records that still exist. Hard deletes need a
> deleted-items endpoint, CDC, or a key-set reconciliation between source and target.
