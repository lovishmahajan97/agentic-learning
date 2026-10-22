---
id: postgresql-transactions-and-isolation-levels
module: backend-fundamentals
name: PostgreSQL transactions & isolation levels
kind: concept
difficulty: core
est_minutes: 18
status: crisp-verified
related:
  - backend-fundamentals/database-design
  - backend-fundamentals/sql
  - backend-fundamentals/background-jobs-durable-workers
  - fastapi-for-ai/async-database-access-and-connection-pooling
  - fastapi-for-ai/idempotency-keys
  - node-ts-for-ai/postgresql-transactions-and-migrations
  - system-design/data-consistency
  - system-design/saga-pattern-and-distributed-transactions
sources:
  - Official docs: PostgreSQL
  - Berenson et al., 1995 — A Critique of ANSI SQL Isolation Levels
  - Ports and Grittner, 2012 — Serializable Snapshot Isolation in PostgreSQL
---

## TL;DR
A transaction makes statements atomic and durable; the isolation level decides what concurrent
transactions see. With MVCC, readers and writers do not block each other, but READ COMMITTED permits lost updates
and write skew; fix them with atomic updates, row locks, or SERIALIZABLE plus retries.

## Why it exists
- **Crashes split related writes.** Without transactions, a crash between "insert the message" and
  "increment the tenant's token counter" leaves the two permanently inconsistent.
- **Concurrent read-modify-writes lose updates.** Two requests that both read a balance, compute a new one
  and write it back overwrite each other. Budget checks before an LLM call, a counter of concurrent runs,
  "create the conversation if it does not exist" and "claim the next job" all run concurrently in any real service.
- **The naive fixes fail.** A global application lock serializes everything and does not survive a second
  server. Classic two-phase locking makes readers and writers block each other, so a long report query
  stalls every insert.
- **MVCC lets both proceed.** PostgreSQL keeps multiple versions of each row and gives each transaction a
  consistent snapshot, so readers and writers run concurrently.
- **Isolation levels say which anomalies remain.** That question is where most production data-corruption
  bugs in otherwise careful code come from; understanding the levels is cheaper than debugging a tenant
  whose token usage no longer adds up.

## How it works
### ACID in brief
| Property | Meaning in PostgreSQL |
|---|---|
| **Atomic** | a transaction (`BEGIN` ... `COMMIT`) makes all its writes visible together, or none via rollback |
| **Consistency** | constraints hold at commit |
| **Isolation** | the subject of this lesson |
| **Durability** | a committed transaction survives a crash, because the write-ahead log is fsynced before the commit is acknowledged |

- **That fsync is the floor on commit latency,** which is why batching many small writes into one
  transaction is faster than committing each.
- **`synchronous_commit = off`** (per transaction if you like) acknowledges before the flush, risking the
  last few commits on a crash, but never corruption.

### MVCC: versions instead of locks
1. **Versions carry ids.** Every row version records the transaction that created it (`xmin`) and, once
   updated or deleted, the one that ended it (`xmax`).
2. **Updates append.** An `UPDATE` writes a new version and marks the old one as ended.
3. **Snapshots decide visibility.** A transaction's **snapshot** is the set of transaction ids committed
   when it started looking; it sees exactly the versions committed in that snapshot. So a long `SELECT`
   never blocks an `UPDATE` and vice versa.
4. **`VACUUM` reclaims dead versions** once no snapshot can see them. Long-running or idle-in-transaction
   sessions pin old snapshots and prevent cleanup, which causes table bloat.

### The isolation levels in PostgreSQL
PostgreSQL offers three distinct behaviours. READ UNCOMMITTED is accepted but behaves as READ COMMITTED,
because MVCC never exposes uncommitted data.

1. **READ COMMITTED (default).**
   - Each *statement* takes a fresh snapshot, so two consecutive selects can differ if another
     transaction committed in between (non-repeatable read).
   - An `UPDATE` whose target row was changed by a concurrent committed transaction waits, then
     re-evaluates its `WHERE` on the new version and proceeds.
   - Fast and rarely aborts, but a read-modify-write in application code is not protected at all.
2. **REPEATABLE READ.**
   - The snapshot is taken at the first statement and held for the whole transaction, so repeated reads
     agree and, in PostgreSQL, phantoms do not appear either.
   - Updating a row another transaction modified and committed since the snapshot fails with serialization
     error `40001`, and must be retried.
   - Still permits **write skew**: two transactions each read a condition, both see it true, each writes a
     *different* row, and the combined result violates the condition.
3. **SERIALIZABLE.**
   - Serializable snapshot isolation (SSI) tracks read/write dependencies between concurrent transactions
     and aborts one on the "dangerous structure" (two consecutive read-write dependencies) that any
     non-serializable cycle must contain.
   - The check is conservative, so it sometimes aborts transactions that would have been fine.
   - Result: as if transactions ran one at a time, with no explicit locking, at the cost of extra
     bookkeeping and more `40001` aborts under contention. Any code using it must retry.

### The anomalies, side by side
PostgreSQL behaviour, which is stricter than the SQL standard's minimums:

| Anomaly | What it is | READ COMMITTED | REPEATABLE READ | SERIALIZABLE |
|---|---|---|---|---|
| **Dirty read** | seeing another transaction's uncommitted write | impossible | impossible | impossible |
| **Non-repeatable read, phantom** | a re-read returns changed values or new rows | possible | prevented | prevented |
| **Lost update** | two read-modify-writes, one overwrites the other | possible | second writer gets `40001` | second writer gets `40001` |
| **Write skew** | two transactions read an overlapping condition and write *different* rows | possible | possible | prevented |

### The lost update, concretely
Tenant budget 1,000,000 tokens, 999,000 used, two requests costing 800 each:

1. Both run `SELECT tokens_used` and read 999,000.
2. Both compute 999,800 ≤ 1,000,000.
3. Both run `UPDATE tokens_used = 999800`.
4. Final value 999,800; real usage 1,000,600. The budget was exceeded without any error.

Under READ COMMITTED this is fully allowed. The second `UPDATE` does wait for the first's row lock and
re-checks its `WHERE` on the new version, but its `SET` assigns the constant 999,800 computed from a stale
read, so re-evaluation changes nothing. The fixes move the read inside the protected step:

- **Atomic update:** `UPDATE tenants SET tokens_used = tokens_used + 800 WHERE id = $1 AND
  tokens_used + 800 <= budget RETURNING tokens_used`. Check and write are one statement that waits for the
  row lock, re-evaluates, and returns zero rows if the budget is now blown.
- **Atomic update, cost by level:** cheapest, and needs no retry at READ COMMITTED; at REPEATABLE READ or
  SERIALIZABLE the second statement gets `40001` instead of re-evaluating, so it is still safe but must be retried.
- **Pessimistic lock:** `SELECT ... FOR UPDATE` locks the row for the transaction, so the second request
  waits until the first commits, then reads the new value. The lock lasts the whole transaction, so never
  hold it across a network call.
- **Optimistic:** read a `version` column, write with `WHERE version = $old`, and retry when zero rows
  are affected; or run at SERIALIZABLE and retry on `40001`.

### Row locks, SKIP LOCKED and deadlocks
- **Row lock strengths:** `FOR UPDATE` and the weaker `FOR NO KEY UPDATE` / `FOR SHARE` lock individual rows.
- **`FOR UPDATE SKIP LOCKED`** returns only rows nobody else holds: the primitive behind database-backed job
  queues, where many workers each claim a different row with one statement.
- **`NOWAIT`** errors instead of waiting.
- **Deadlocks:** when two transactions each hold a lock the other wants, PostgreSQL detects the cycle after
  `deadlock_timeout` (one second by default) and aborts one with `40P01`. The usual cause is locking in
  different orders; the fix is a consistent ordering (sort ids before locking).

### Things that surprise people
- **An error poisons the transaction.** PostgreSQL rejects every further statement until `ROLLBACK` (or
  rollback to a `SAVEPOINT`). Catching the exception in code and continuing does not help.
- **Transaction-mode poolers drop session state.** PgBouncer in transaction mode reuses server connections
  between transactions, so session state such as `SET` and session-level advisory locks leaks or vanishes.
- **Prepared statements and PgBouncer.** These were in the same list until PgBouncer 1.21 added
  protocol-level support (`max_prepared_statements`); check your pooler version and setting.
- **Isolation is per transaction:** `BEGIN ISOLATION LEVEL SERIALIZABLE`, or `SET TRANSACTION` as the
  first statement; `SET default_transaction_isolation` changes the session default.

## Diagram
A decision path for a concurrent write: pick the cheapest mechanism that moves the read inside the
protected step, and keep every transaction short.

```mermaid
flowchart TD
    W["Concurrent read-modify-write"] --> S{"Fits one statement?"}
    S -->|"yes"| A["Atomic conditional UPDATE"]
    S -->|"no"| J{"Claiming queued work?"}
    J -->|"yes"| K["FOR UPDATE SKIP LOCKED"]
    J -->|"no"| R{"One row, short transaction?"}
    R -->|"yes"| F["SELECT FOR UPDATE, then write"]
    R -->|"no, multi-row invariant"| Z["SERIALIZABLE plus retry on 40001"]
    A --> C["COMMIT before any LLM call"]
    K --> C
    F --> C
    Z --> C
```

## Examples
### The budget race in psycopg (3.x)
- **`reserve_atomic`** is the single-statement fix.
- **`reserve_serializable`** shows SERIALIZABLE plus retry for logic that genuinely needs several
  statements, such as checking a budget and then inserting the run that consumes it.

```python
import time, psycopg
from psycopg.errors import SerializationFailure

DSN = "postgresql://app@localhost/app"

def reserve_atomic(conn, tenant_id, cost):
    row = conn.execute(
        """UPDATE tenants SET tokens_used = tokens_used + %s
           WHERE id = %s AND tokens_used + %s <= budget
           RETURNING tokens_used""", (cost, tenant_id, cost)).fetchone()
    return row is not None            # None means the budget would be exceeded

def reserve_serializable(conn, tenant_id, cost, run_id, attempts=5):
    for attempt in range(attempts):
        try:
            with conn.transaction():          # call on an idle connection: SET must come first
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                used, budget = conn.execute(
                    "SELECT tokens_used, budget FROM tenants WHERE id = %s",
                    (tenant_id,)).fetchone()
                if used + cost > budget:
                    return False
                conn.execute("UPDATE tenants SET tokens_used = %s WHERE id = %s",
                             (used + cost, tenant_id))
                conn.execute("INSERT INTO runs (id, tenant_id, cost) VALUES (%s, %s, %s)",
                             (run_id, tenant_id, cost))   # unique id makes retries idempotent
                return True
        except SerializationFailure:      # SQLSTATE 40001: another txn won; retry
            time.sleep(0.01 * 2 ** attempt)
    raise RuntimeError("could not reserve budget after retries")

with psycopg.connect(DSN) as conn:
    ok = reserve_atomic(conn, tenant_id=42, cost=800)
    conn.commit()
    print("reserved" if ok else "over budget")
```

Run two copies of either function concurrently against a tenant at 999,000 of 1,000,000 with a cost of
800: each lets exactly one through.
- **Atomic version:** the loser's `WHERE` is re-evaluated on the new row and fails.
- **Serializable version:** the loser aborts, retries, and then sees the budget is exhausted.
- **Naive select-then-update at READ COMMITTED** (the same body as `reserve_serializable` without the
  isolation level) lets both through.

### Production SQL: claiming jobs and finding stuck transactions
A job claim with `SKIP LOCKED` (each worker gets a different row in one statement), and the query to find
sessions that hold snapshots open.

```sql
-- Claim one due job; concurrent workers skip rows another worker has locked.
WITH next AS (
  SELECT id FROM jobs
  WHERE  status = 'queued' AND run_at <= now()
  ORDER  BY run_at
  LIMIT  1
  FOR UPDATE SKIP LOCKED
)
UPDATE jobs SET status = 'running', locked_at = now(), attempts = attempts + 1
FROM   next
WHERE  jobs.id = next.id
RETURNING jobs.id, jobs.payload;

-- Sessions idle inside a transaction: they pin old row versions and block VACUUM cleanup.
SELECT pid, usename, now() - xact_start AS open_for, left(query, 60) AS last_query
FROM   pg_stat_activity
WHERE  state = 'idle in transaction'
ORDER  BY xact_start;
```

### Worked scenario: write skew on "at most one active run"
Rule: a tenant may have at most one active run. Both transactions run at REPEATABLE READ.

| Time | Transaction A | Transaction B |
|---|---|---|
| t1 | `SELECT count(*)` of active runs: 0 | |
| t2 | | `SELECT count(*)` of active runs: 0 |
| t3 | `INSERT` run a1 | |
| t4 | | `INSERT` run b1 |
| t5 | `COMMIT` succeeds | `COMMIT` succeeds: 2 active runs |

Neither updated a row the other touched, so REPEATABLE READ sees no conflict. At SERIALIZABLE, SSI sees
the read-write dependencies and aborts one with `40001`.

## Production patterns
- **Timeouts as guard rails.** Set `idle_in_transaction_session_timeout` (for example a minute) so a leaked
  transaction is killed instead of pinning snapshots, `lock_timeout` for migrations and request paths, and
  `statement_timeout` per role. Monitor `pg_stat_activity` for `idle in transaction` and lock waits.
- **Reserve, commit, call, settle.** Never wrap an LLM call in a transaction: reserve budget with an atomic
  `UPDATE` and commit, call the provider, then record actual usage in a second short transaction. Monitor
  transaction duration p99, which should not track provider latency.
- **A shared retry wrapper.** One helper retries on `40001` and `40P01` with exponential backoff and jitter
  and a small attempt cap, and requires idempotent bodies (unique ids). Monitor retries per second and
  `deadlocks` in `pg_stat_database`.
- **Consistent lock order.** Lock multiple rows with `SELECT ... WHERE id = ANY($1) ORDER BY id FOR
  UPDATE`, and turn on `log_lock_waits` so waits longer than `deadlock_timeout` are logged. Monitor the log
  for recurring lock-wait statements.
- **Transaction-scoped advisory locks behind a pooler.** Under PgBouncer transaction mode, use
  `pg_advisory_xact_lock` (released at commit), never session-level advisory locks. Monitor lock wait time.

## Trade-offs
- **READ COMMITTED throughput vs races.** High throughput and almost no aborts, but every read-modify-write
  in code is a race; flip to a stricter level or explicit locks once money or quotas are involved.
- **SERIALIZABLE safety vs aborts.** Correctness without reasoning about every race, but aborts rise with
  contention and every caller must retry; on a hot counter row it can thrash.
- **`FOR UPDATE` simplicity vs blocking.** Never aborts, but blocks other writers for the length of the
  transaction, so a transaction that also calls an LLM holds the row for seconds.
- **Optimistic versions vs contention.** Version columns avoid locks and work through a pooler, but under
  heavy contention most attempts fail and the retry loop becomes the bottleneck.
- **Long consistent reads vs bloat.** Long transactions give reports a consistent view but pin old row
  versions, block `VACUUM` and cause bloat; flip to a replica or a snapshot export for long analytics.
- **Transaction pooling vs session features.** It multiplies the clients one database can serve, but costs
  session features such as advisory locks and `SET`, which must move into the transaction.

## Pitfalls
- **Select, compute, update in code under the default level.** Symptom: counters and balances drift from
  the sum of their events, with no errors in the logs.
- **Holding a transaction across an LLM or HTTP call.** Symptom: lock waits and idle-in-transaction
  sessions that scale with provider latency, then pool exhaustion.
- **Not retrying on `40001` at REPEATABLE READ or SERIALIZABLE.** Symptom: sporadic 500s under load that
  disappear when you retry by hand.
- **Assuming REPEATABLE READ prevents write skew.** Symptom: a two-row invariant (for example "at most one
  active run per tenant") violated by concurrent transactions that each saw it satisfied.
- **Catching an exception mid-transaction and continuing.** Symptom: `current transaction is aborted,
  commands ignored until end of transaction block` on the next statement.
- **Locking rows in different orders in two code paths.** Symptom: intermittent `40P01` deadlocks roughly
  one second after the conflicting requests start.
- **Expecting READ UNCOMMITTED to behave as in other databases.** In PostgreSQL it is READ COMMITTED, so
  code relying on dirty reads silently gets committed data only.
- **Forgetting a transaction-mode pooler breaks `SET` and advisory locks across statements.** Symptom:
  settings that seem to apply randomly to other requests.

## When to use
- **Default:** start at READ COMMITTED and make every write path safe by construction.
- **Counters and quotas:** atomic single-statement updates.
- **Exclusive multi-statement decisions:** `FOR UPDATE`, when the transaction stays short.
- **Job claiming:** `SKIP LOCKED`.
- **Multi-row invariants hard to lock explicitly:** SERIALIZABLE with retries.
- **Always:** keep transactions short and never span a network call.
- **Measure:** `pg_stat_activity` for idle-in-transaction sessions and lock waits, and serialization
  failures per second.
- **Interviews:** expect a read-modify-write and a question on which level or lock fixes it, an
  explanation of write skew, and why a long transaction slows a table it never wrote to.

## Role lens
- **Engineer:** Writes the atomic update or the `FOR UPDATE` block, adds the `40001` retry loop, and keeps
  the transaction boundary inside the request rather than around the model call.
- **Architect:** Sets the policy: which invariants are enforced by the database versus the application,
  the default isolation level, and the rule that transactions never cross service or provider calls.
- **FDE:** Diagnoses a customer's drifting usage counters or lock pileups from `pg_stat_activity`, and
  explains why their pooler configuration broke session-level settings.

## Check yourself
**Q:** A service enforces "at most three concurrent runs per tenant" by counting active runs in a
REPEATABLE READ transaction, then inserting a new run if the count is below three. Under load, tenants
end up with five active runs. Why, and what are two fixes?
**A:** This is write skew. Each transaction's snapshot shows two active runs, each inserts a *new* row
rather than updating a row the other touched, so neither conflicts with the other at REPEATABLE READ and
both commit, giving four or five runs. Fix one: run the transaction at SERIALIZABLE and retry on `40001`;
SSI detects the read-write dependency between the count and the inserts and aborts one. Fix two: make
the conflict explicit by locking something shared, for example `SELECT ... FROM tenants WHERE id = $1
FOR UPDATE` before counting, or a per-tenant `pg_advisory_xact_lock`, and drop to READ COMMITTED, so the
second transaction waits and its count statement takes a fresh snapshot after the first commits. At
REPEATABLE READ the lock alone is not enough: the snapshot was taken by the locking statement before it
waited, so the recount still cannot see the other transaction's insert. A third option is to keep an `active_runs` counter on the tenant row and use a
single conditional `UPDATE ... WHERE active_runs < 3`, which turns the invariant into one atomic
statement and avoids retries entirely.

## Interview questions
### How does MVCC let readers and writers avoid blocking each other in PostgreSQL?
- **Updates create new versions** stamped with `xmin`/`xmax` instead of overwriting, so the old version
  stays readable.
- **Each transaction reads from a snapshot** of committed transaction ids (a fresh one per statement at
  READ COMMITTED), so a long `SELECT` sees a stable view while `UPDATE`s proceed.
- **The cost is cleanup:** `VACUUM` must reclaim dead versions, and old open snapshots prevent it.

### Two concurrent requests both pass a budget check and overspend. Walk through your fix.
- **Diagnose:** it is a lost update; both read the same committed value at READ COMMITTED and wrote a
  constant computed from a stale read.
- **Fix:** one conditional `UPDATE ... SET used = used + cost WHERE used + cost <= budget RETURNING`, which
  waits for the row lock and re-evaluates, so exactly one succeeds without retries.
- **If several statements are needed,** use `FOR UPDATE` in a short transaction or SERIALIZABLE with a retry loop.

### When would you not use SERIALIZABLE everywhere?
- **On hot rows,** because aborts rise with contention and a busy counter can thrash in retries.
- **When callers cannot retry safely,** for example non-idempotent side effects inside the transaction.
- **Prefer** atomic statements and explicit locks for known hot paths, and reserve SERIALIZABLE for
  multi-row invariants that are hard to lock.

### Tables are bloating and queries slowing, but write volume is unchanged. What do you look for?
- **Old open snapshots:** query `pg_stat_activity` for `idle in transaction` sessions and long
  `xact_start` ages; they hold back the cleanup horizon for the whole database.
- **Fix the source** (a transaction wrapped around an HTTP or LLM call, a forgotten commit) and set
  `idle_in_transaction_session_timeout` as a backstop.

### What breaks when you put PgBouncer in transaction mode in front of an app?
- **Session state:** `SET`, session-level advisory locks and anything assumed to persist between
  transactions, because each transaction may get a different server connection.
- **Prepared statements** worked only from PgBouncer 1.21 with `max_prepared_statements` set.
- **Fix:** move settings into each transaction (`SET LOCAL`) and use `pg_advisory_xact_lock`.

## Quiz
### Q: Under PostgreSQL's default READ COMMITTED level, why can two transactions both pass a "balance is sufficient" check and overspend?
- [ ] READ COMMITTED allows reading uncommitted data from the other transaction
- [x] Each transaction reads the committed balance, computes a new value, and writes it; neither read locks the row, so both writes succeed and one update is lost
- [ ] The database merges both updates by adding them together
- [ ] READ COMMITTED serializes writes but not reads, so the second write is silently dropped
> A plain SELECT takes no lock and the UPDATE only waits for a concurrent writer of the same row. Both
> transactions read the same committed value, so the later write overwrites the earlier one.

### Q: What does SERIALIZABLE in PostgreSQL require from application code that READ COMMITTED does not?
- [ ] Explicit FOR UPDATE locks on every row that will be modified
- [ ] A single connection per transaction without pooling
- [x] A retry loop, because the database may abort a transaction with a serialization failure when a conflict is detected
- [ ] Disabling MVCC so that writers block readers
> SSI resolves dangerous dependency cycles by aborting one transaction with SQLSTATE 40001. The outcome is
> correct only if the caller retries; code that treats the abort as a fatal error turns safety into 500s.

### Q: Why does an idle session that opened a transaction an hour ago slow down tables it never touched?
- [ ] It holds an exclusive lock on the whole database
- [x] Its snapshot keeps old row versions alive, so VACUUM cannot reclaim dead tuples and tables bloat
- [ ] It consumes all available connections in the pool
- [ ] PostgreSQL pauses checkpoints while any transaction is open
> MVCC keeps a row version until no snapshot can see it. An old open snapshot (or an old assigned
> transaction id) holds back the cleanup horizon for the whole database, so dead versions accumulate in
> every table, indexes grow and scans read more pages; this is why idle-in-transaction is monitored.
