---
id: state-and-checkpoint-stores
module: agent-reliability
name: State & checkpoint stores
kind: decision
difficulty: core
est_minutes: 13
status: fact-checked
related:
  - agent-reliability/durable-execution-and-checkpointing
  - agent-reliability/state-persistence-and-conversation-recovery
  - backend-fundamentals/redis
  - backend-fundamentals/postgresql-transactions-and-isolation-levels
  - backend-fundamentals/nosql
  - agent-runtime-architecture/multi-tenant-state-isolation
  - data-architecture/data-retention-and-deletion-architecture
sources:
  - Official docs: LangGraph (checkpointers)
  - Official docs: PostgreSQL
---

## TL;DR
Where agent state lives determines how fast each step is, what survives a crash, and how you isolate
tenants and delete data. The decision weighs per-step write latency against durability, blob size
against query needs, and short TTLs against audit retention. A sound default is a relational store
(Postgres) for run records, events and small snapshots, object storage for large tool outputs, and
Redis only for ephemeral leases and stream buffers, with a tenant id on every row.

## Why it exists
Checkpointing puts a durable write on the critical path of every agent step. That single fact drives
the storage decision: a store that takes hundreds of milliseconds per write (illustrative) adds
minutes to a hundred-step run and is felt in every streaming response, while a store that
acknowledges writes before they are durable silently loses the last step on a crash, which is
precisely the step you needed. Agent state also has an awkward shape. It is mostly small structured
records (run status, step index, budgets) plus a long append-only log plus occasional very large
blobs (a tool returned megabytes of HTML, a sub-agent returned a full report). It must be queried by
run id (resume), by conversation id (history), sometimes by step (time-travel and debugging), always
scoped by tenant, and it must expire: operational checkpoints have a short useful life while audit
records may need to be kept for years.

The naive choice is whichever store is already there. A Redis instance is fast and has TTLs, so teams
start there and discover that its persistence settings were never tuned, that multi-megabyte blobs
eat memory, and that "the agent forgot everything after the cache restarted" is now a support ticket.
Or they pick a document database for flexibility, write the checkpoint and the run status update as
two independent operations because multi-document transactions are off the default path (MongoDB
needs a replica set for them, DynamoDB's transactional writes are capped per call and cost extra
capacity), and get half-updated runs after crashes. The store has to be
chosen against the actual access pattern and durability needs, not convenience.

## How it works
### The data model
Three logical tables (names illustrative) plus a blob store:
- `runs(run_id, conversation_id, tenant_id, status, attempt, owner, lease_until, schema_version,
  created_at, updated_at)`: one row per execution, the thing you lease and poll.
- `events(run_id, seq, type, payload, created_at)`: append-only model responses, tool calls and tool
  results. Small payloads inline as JSONB; large ones replaced by a reference.
- `checkpoints(run_id, step, state, created_at)`: a small snapshot of the non-transcript state
  (budgets, pending intents, loop-guard counters). Because the transcript lives in `events`, each
  snapshot stays small and total storage grows linearly with steps rather than quadratically.
- Blobs in object storage at `tenant/run_id/seq`, referenced from events by key and size.

### Write pattern per step
One transaction: insert the step's events, upsert the checkpoint row, update the run's status and
timestamp. Transactional because a crash between "events written" and "checkpoint written" produces
a resume whose pending intents disagree with the log, so it re-issues calls that already have
results. Relational stores give this for free; with a key-value store you emulate it with a single
document per run (and then size becomes the problem) or accept a repair step on resume.

### Leases, not locks
Resume must be exclusive. A lease is a conditional update: set `owner` and `lease_until` only where
the row is unowned or the lease has expired, returning the row on success. A lease expires if the
worker dies; a lock would not. Renew on a heartbeat and stop working if renewal fails, because another
worker may have taken over.

### The options
- **In-memory** (a dict, LangGraph's `InMemorySaver`, formerly `MemorySaver`): zero latency, zero
  durability. Tests and
  notebooks only.
- **Redis**: sub-millisecond writes, native TTL, good for leases, rate counters and resumable stream
  buffers. As the checkpoint store only if persistence is configured deliberately (append-only file
  with an fsync policy you understand) and blobs stay small; memory cost per byte is high, and most
  people treat it as a cache, which is how it gets flushed.
- **Postgres or another relational store**: durable by default, transactions, JSONB for flexible
  payloads, indexes on (tenant, conversation), row-level security for tenant isolation, mature
  backups, low-millisecond writes on a local network. The default for most teams; LangGraph ships
  both a Postgres checkpointer and a SQLite one for local use.
- **Document and wide-column stores** (DynamoDB, MongoDB, Cassandra): horizontal scale and per-item
  TTL, and key-value access matches run id lookups; cross-item transactions are more limited
  (bounded in size, extra cost, or single-partition only in Cassandra) and the operational knowledge
  heavier. Choose them when a single relational primary becomes the bottleneck.
- **Object storage** (S3-style): cheap and effectively unlimited but slower and unqueryable. For blobs
  and long-term archive of event logs, never alone on the hot path.
- **Workflow engines** (Temporal and similar): own the event history and the resume logic; you store
  application state only. The decision becomes "adopt the engine" rather than "pick a database".

### Retention and deletion
Separate hot from cold. Checkpoints and leases expire within hours or days of run completion; events
may be kept longer for audit and evaluation, then archived to object storage and deleted on a
schedule. Tenant-scoped deletion (right to erasure, contract end) must reach events, blobs and
archives, which is why `tenant_id` is a column on every row and a prefix on every blob key, never
something inferred by joining through conversations. Encrypt blobs at rest: tool outputs routinely
contain customer data.

### Sizing
Estimate events per run × average payload × runs per day × retention days, plus blob volume.
Illustrative arithmetic: a thousand runs a day with fifty events of 4 KB each is about 200 MB a day
of JSONB before blobs, comfortably within a single Postgres for a long time; the blob side is what
grows unexpectedly.

## Example
One transaction per step first checks that this worker still holds the lease (a fencing check, so a
worker whose lease expired cannot overwrite its successor), then writes the events (offloading large
payloads to object storage behind a reference) and upserts the small snapshot; `claim` takes an
expiring lease so only one worker resumes a given run. Every table carries `tenant_id`.

```python
import json, psycopg

DDL = """
create table if not exists runs (
  run_id text primary key, tenant_id text not null, conversation_id text not null,
  status text not null, owner text, lease_until timestamptz,
  schema_version int not null default 1, updated_at timestamptz default now());
create table if not exists events (
  run_id text references runs, tenant_id text not null, seq int, type text, payload jsonb,
  blob_key text, created_at timestamptz default now(), primary key (run_id, seq));
create table if not exists checkpoints (
  run_id text references runs, tenant_id text not null, step int, state jsonb,
  primary key (run_id, step));
"""
# conn = psycopg.connect(dsn, autocommit=True): claim() commits at once and
# conn.transaction() below is a real BEGIN/COMMIT rather than a savepoint

def claim(conn, run_id, worker, ttl_s=30) -> bool:
    row = conn.execute(
        """update runs set owner = %s, lease_until = now() + make_interval(secs => %s)
           where run_id = %s and (owner is null or lease_until < now())
           returning run_id""", (worker, ttl_s, run_id)).fetchone()
    return row is not None

def save_step(conn, run_id, tenant_id, worker, step, new_events, state, blobs, big=64_000):
    with conn.transaction():                              # all or nothing
        if conn.execute("""update runs set updated_at = now() where run_id = %s
                           and owner = %s and lease_until > now()""",
                        (run_id, worker)).rowcount == 0:
            raise RuntimeError("lease lost: stop, another worker owns this run")
        for ev in new_events:
            raw, key = json.dumps(ev["payload"]), None
            if len(raw) > big:                            # offload large outputs
                key = f"{tenant_id}/{run_id}/{ev['seq']}.json"
                blobs.put(key, raw)
                raw = json.dumps({"ref": key, "bytes": len(raw)})
            conn.execute("""insert into events (run_id, tenant_id, seq, type, payload, blob_key)
                            values (%s, %s, %s, %s, %s::jsonb, %s)""",
                         (run_id, tenant_id, ev["seq"], ev["type"], raw, key))
        conn.execute("""insert into checkpoints (run_id, tenant_id, step, state)
                        values (%s, %s, %s, %s::jsonb) on conflict (run_id, step)
                        do update set state = excluded.state""",
                     (run_id, tenant_id, step, json.dumps(state)))
```

## Trade-offs
- Postgres gives transactions and durability with simple operations, but a single primary caps
  write throughput; shard by tenant or move the event log to a wide-column store once writes per
  second become the bottleneck.
- Redis makes each step's write nearly free and handles leases and stream buffers well, but
  durability depends on persistence configuration and memory cost limits payload size; use it
  beside a durable store, not instead of one.
- Offloading large payloads to object storage keeps the database small and fast, but adds a second
  write and a fetch on resume; set the threshold from your payload distribution rather than
  offloading everything.
- Short TTLs on checkpoints control growth and limit exposure of sensitive tool outputs, but shorten
  the window for resume and debugging; keep events longer than checkpoints and archive rather than
  delete when audit demands it.

## Pitfalls
- Treating Redis as durable without checking persistence; the symptom is every in-flight run
  vanishing after a cache node restart.
- Writing events and checkpoint in separate statements without a transaction; the symptom is resumed
  runs re-issuing tool calls that already have results in the log.
- Storing the full transcript in every snapshot; the symptom is checkpoint storage and write latency
  growing with the square of run length.
- Omitting tenant_id from events and blobs; the symptom is a deletion request that needs multi-table
  joins and still misses archived objects.
- Locks instead of leases; the symptom is a run stuck forever as "owned" by a worker that died,
  resumable only by manual intervention.

## When to use
Make the decision when you first persist a run, because migrating state stores later is painful.
Default to a relational store with object storage for blobs unless you already run a workflow engine
or your write volume clearly exceeds a single primary. Add Redis for leases, counters and resumable
stream buffers. Measure checkpoint write p95 latency (it is inside every step), store growth per day,
resume success rate and deletion completeness. Day to day this shows up when a run cannot resume, a
storage bill arrives, or a customer asks how their data is deleted. In interviews, expect "where do
you keep agent state and why" and answer with the access pattern, durability and tenant requirements,
not a product name.

## Role lens
- **Engineer:** Implements the schema, the per-step transaction, blob offloading and lease renewal,
  and load-tests write latency at the expected concurrency.
- **Architect:** Owns the store selection, retention and encryption policy, the tenant isolation
  model, and the migration plan for when the first choice is outgrown.
- **FDE:** Fits the design to the customer's approved databases and data-residency rules, and proves
  to their security team how tenant data is scoped and deleted.

## Check yourself
**Q:** A team checkpoints to Redis with a 24-hour TTL and stores full tool outputs inline. Runs now
last hours and tool outputs reach megabytes. What breaks first, and how would you restructure?
**A:** Memory breaks first: inline multi-megabyte outputs across thousands of runs exhaust the Redis
instance, and eviction or a restart without tuned persistence loses in-flight runs entirely, which
also means hours-long runs cannot be resumed. Restructure into a durable relational store for run
records, an append-only events table and small per-step snapshots, with any payload above a size
threshold offloaded to object storage behind a reference whose key carries tenant and run. Keep Redis
for leases, rate counters and resumable stream buffers, where its speed matters and loss is
tolerable. Set retention separately: checkpoints expire soon after completion, events are archived per
audit policy, and tenant-scoped deletion covers the blobs too.

## Quiz
### Q: Why should the per-step events and checkpoint be written in one transaction?
- [ ] It reduces the number of network round trips
- [x] A crash between the two writes would otherwise leave a resume that re-issues tool calls already recorded in the log
- [ ] Transactions are required for JSONB columns
- [ ] It lets the checkpoint be larger
> The checkpoint's pending intents and the event log must agree; if one is written and the other is
> not, the resume either repeats a side effect or loses one. Atomicity removes that window.

### Q: What is the main reason to prefer a lease over a lock when a worker claims a run to resume?
- [ ] Leases are faster to acquire in Postgres
- [x] A lease expires automatically if the worker dies, so another worker can claim the run without manual cleanup
- [ ] Locks are not supported on JSONB tables
- [ ] Leases allow two workers to share a run
> Workers die without releasing locks; an expiring lease, renewed by heartbeat, bounds how long a dead
> worker's run stays unclaimed, while still guaranteeing a single active owner during the lease.

### Q: Why should tenant_id be a column on every row and a prefix on every blob key?
- [ ] It makes JSON payloads smaller
- [ ] Postgres requires it for foreign keys
- [x] Tenant-scoped queries, isolation policies and deletion must work without joining through other tables or scanning every object
- [ ] It allows TTLs to be set per tenant only
> Deletion requests and isolation rules apply per tenant across events, snapshots and archives;
> inferring tenancy through joins is slow and error-prone, and object stores cannot join at all.
