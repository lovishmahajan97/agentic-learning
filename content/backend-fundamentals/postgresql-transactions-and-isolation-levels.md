---
id: postgresql-transactions-and-isolation-levels
module: backend-fundamentals
name: PostgreSQL transactions & isolation levels
kind: concept
difficulty: core
est_minutes: 18
status: reviewed
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
A transaction makes a group of statements atomic and durable; the isolation level decides what each
transaction may see of the others while they run. PostgreSQL implements isolation with multi-version
concurrency control, so readers never block writers, and the default READ COMMITTED level is fast but
permits lost updates and write skew in read-modify-write code. You fix those with explicit row locks,
atomic updates, or SERIALIZABLE plus a retry loop.

## Why it exists
Backends run many requests at once against the same rows. Without transactions, a crash between "insert
the message" and "increment the tenant's token counter" leaves the two permanently inconsistent, and
without isolation, two requests that both read a balance, compute a new one and write it back will
overwrite each other and lose one of the updates. These are not rare races: a budget check before an
LLM call, a counter of concurrent runs, "create the conversation if it does not exist", and "claim the
next job" are all read-then-write sequences that run concurrently in any real service.

The naive defence, a global lock in the application, serializes everything and does not survive a second
server. The classic database answer, two-phase locking, makes readers and writers block each other, so a
long report query stalls every insert. PostgreSQL instead keeps multiple versions of each row and gives
each transaction a consistent snapshot, which lets readers and writers proceed concurrently. The
remaining question, which anomalies a snapshot still permits, is what isolation levels answer, and it is
where most production data-corruption bugs in otherwise careful code come from. Understanding the
levels is cheaper than debugging a tenant whose token usage no longer adds up.

## How it works
### ACID in one paragraph
A transaction (`BEGIN` ... `COMMIT`) is **atomic**: all of its writes become visible together or none do,
via rollback. **Consistency** means constraints hold at commit. **Isolation** is the subject of this
lesson. **Durability** means a committed transaction survives a crash, which PostgreSQL achieves by
writing to the write-ahead log and fsyncing it before acknowledging the commit. That fsync is the floor
on commit latency, which is why batching many small writes into one transaction is faster than committing
each, and why `synchronous_commit = off` (per transaction if you like) exists: it acknowledges before the
flush, risking the loss of the last few commits on a crash, but never corruption.

### MVCC: versions instead of locks
Every row version carries the id of the transaction that created it (`xmin`) and, once updated or
deleted, the one that ended it (`xmax`). An `UPDATE` does not overwrite; it writes a new version and
marks the old one as ended. A transaction takes a **snapshot** (the set of transaction ids that were
committed when it started looking) and sees exactly the versions that were committed in that snapshot.
This is why a long `SELECT` never blocks an `UPDATE` and vice versa. Old versions become dead once no
snapshot can see them, and `VACUUM` reclaims them, which is why long-running or idle-in-transaction
sessions cause table bloat: they pin old snapshots and prevent cleanup.

### The isolation levels in PostgreSQL
PostgreSQL offers three distinct behaviours (READ UNCOMMITTED is accepted but behaves as READ COMMITTED,
because MVCC never exposes uncommitted data):

1. **READ COMMITTED (default).** Each *statement* takes a fresh snapshot, so a transaction can see
   different data in two consecutive selects if another transaction committed in between
   (non-repeatable read). An `UPDATE` that finds its target row changed by a concurrent committed
   transaction waits, then re-evaluates its `WHERE` on the new version and proceeds. This is fast and
   rarely aborts, but a read-modify-write in application code is not protected at all.
2. **REPEATABLE READ.** The snapshot is taken at the first statement and held for the whole transaction,
   so repeated reads agree and, in PostgreSQL, phantoms do not appear either. If the transaction tries to
   update a row that another transaction modified and committed since the snapshot, it fails with
   serialization error `40001` and must be retried. It still permits **write skew**: two transactions
   each read a condition, both see it true, each writes a *different* row, and the combined result
   violates the condition.
3. **SERIALIZABLE.** Serializable snapshot isolation (SSI) tracks read/write dependencies between
   concurrent transactions and aborts one when it sees the "dangerous structure" (two consecutive
   read-write dependencies) that any non-serializable cycle must contain. The check is conservative, so it
   sometimes aborts transactions that would have been fine. The result is as if transactions ran one at
   a time, with no explicit locking, at the cost of extra bookkeeping and more `40001` aborts under
   contention. Any code using it must retry.

### The anomalies, side by side
Naming the anomalies makes the levels easy to compare (PostgreSQL behaviour, which is stricter than the
SQL standard's minimums):
- **Dirty read** (seeing another transaction's uncommitted write): impossible at every level.
- **Non-repeatable read and phantom** (a re-read returns changed values or new rows): possible at READ
  COMMITTED; prevented at REPEATABLE READ and SERIALIZABLE.
- **Lost update** (two read-modify-writes, one overwrites the other): possible at READ COMMITTED; at
  REPEATABLE READ the second writer gets `40001` instead.
- **Write skew** (two transactions read an overlapping condition and write *different* rows): possible
  at READ COMMITTED and REPEATABLE READ; prevented only at SERIALIZABLE.

### The lost update, concretely
Tenant budget 1,000,000 tokens, 999,000 used. Two requests costing 800 each run `SELECT tokens_used`,
both read 999,000, both compute 999,800 ≤ 1,000,000, both `UPDATE tokens_used = 999800`. Final value:
999,800, real usage 1,000,600, and the budget was exceeded without any error. Under READ COMMITTED this is
fully allowed: the second `UPDATE` does wait for the first's row lock and re-checks its `WHERE` on the
new version, but its `SET` assigns the constant 999,800 that the application computed from a stale read,
so re-evaluation changes nothing. The fixes below work by moving the read inside the protected step. Three fixes, each with a different cost:
- **Atomic update:** `UPDATE tenants SET tokens_used = tokens_used + 800 WHERE id = $1 AND
  tokens_used + 800 <= budget RETURNING tokens_used`. The check and the write are one statement that
  waits for the row lock, re-evaluates, and returns zero rows if the budget is now blown. Cheapest, and
  needs no retry at READ COMMITTED; at REPEATABLE READ or SERIALIZABLE the second statement gets `40001`
  instead of re-evaluating, so it is still safe but must be retried.
- **Pessimistic lock:** `SELECT ... FOR UPDATE` locks the row for the transaction, so the second request
  waits until the first commits, then reads the new value. Simple, but the lock is held for the whole
  transaction, so never hold it across a network call.
- **Optimistic:** read a `version` column, write with `WHERE version = $old`, and retry when zero rows
  are affected; or run the transaction at SERIALIZABLE and retry on `40001`.

### Row locks, SKIP LOCKED and deadlocks
`FOR UPDATE` and the weaker `FOR NO KEY UPDATE` / `FOR SHARE` lock individual rows. `FOR UPDATE SKIP
LOCKED` returns only rows nobody else holds, which is the primitive behind database-backed job queues:
many workers each claim a different row with one statement. `NOWAIT` errors instead of waiting. When two
transactions each hold a lock the other wants, PostgreSQL detects the cycle after `deadlock_timeout`
(one second by default) and aborts one of them with `40P01`; the usual cause is acquiring locks in
different orders, and the usual fix is a consistent ordering (sort ids before locking).

### Things that surprise people
- After any error inside a transaction, PostgreSQL rejects every further statement until `ROLLBACK`
  (or rollback to a `SAVEPOINT`). Catching the exception in code and continuing does not help.
- Transaction-level connection poolers (PgBouncer in transaction mode) reuse server connections between
  transactions, so session state such as `SET` and session-level advisory locks leaks or vanishes.
  Prepared statements were in the same list until PgBouncer 1.21 added protocol-level support
  (`max_prepared_statements`); check your pooler version and setting.
- Isolation level is per transaction: `BEGIN ISOLATION LEVEL SERIALIZABLE` or `SET TRANSACTION` as the
  first statement; a `SET default_transaction_isolation` changes the session default.

## Example
The budget race above, written with psycopg (3.x). `reserve_atomic` is the single-statement fix.
`reserve_serializable` shows the SERIALIZABLE-plus-retry pattern for logic that genuinely needs several
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
800: each lets exactly one through (the atomic version by re-evaluating its `WHERE`, the serializable one
by aborting and retrying the loser, which then sees the budget is exhausted). The naive select-then-update
at READ COMMITTED, the same body as `reserve_serializable` without the isolation level, lets both through.

## Trade-offs
- READ COMMITTED gives high throughput and almost no aborts, but every read-modify-write in application
  code is a race; it flips to a stricter level or explicit locks as soon as money or quotas are involved.
- SERIALIZABLE guarantees correctness without reasoning about every race, but aborts rise with
  contention and every caller must implement retries; on a hot counter row it can thrash.
- `FOR UPDATE` is simple and never aborts, but it blocks other writers for the length of the transaction,
  so a transaction that also calls an LLM holds the row for seconds.
- Optimistic version columns avoid locks and work through a pooler, but under heavy contention most
  attempts fail and the retry loop becomes the bottleneck.
- Long transactions give a consistent view for reports, but they pin old row versions, block `VACUUM`
  and cause bloat; the trade flips toward a replica or a snapshot export for long analytics.
- Transaction-mode pooling multiplies the clients one database can serve, but costs session features
  such as advisory locks and `SET`, which must be moved into the transaction.

## Pitfalls
- Select, compute, update in application code under the default level; the symptom is counters and
  balances that drift from the sum of their events, with no errors in the logs.
- Holding a transaction (and its row locks) across an LLM or HTTP call; the symptom is lock waits and
  idle-in-transaction sessions that scale with provider latency, then pool exhaustion.
- Not retrying on `40001` at REPEATABLE READ or SERIALIZABLE; the symptom is sporadic 500s under load
  that disappear when you retry by hand.
- Assuming REPEATABLE READ prevents write skew; the symptom is an invariant spanning two rows
  (for example "at most one active run per tenant") violated by concurrent transactions that each saw it
  satisfied.
- Catching an exception mid-transaction and continuing; the symptom is `current transaction is aborted,
  commands ignored until end of transaction block` on the next statement.
- Deadlocks from locking rows in different orders in two code paths; the symptom is intermittent `40P01`
  errors roughly one second after the conflicting requests start.
- Expecting READ UNCOMMITTED to behave as in other databases; in PostgreSQL it is READ COMMITTED.
- Forgetting that a pooler in transaction mode breaks `SET` and advisory locks across statements; the
  symptom is settings that seem to apply randomly to other requests.

## When to use
Start from the default level and make every write path safe by construction: atomic single-statement
updates for counters and quotas, `FOR UPDATE` when a multi-statement decision must be exclusive and the
transaction stays short, `SKIP LOCKED` for job claiming, and SERIALIZABLE with retries for genuinely
multi-row invariants that are hard to lock explicitly. Keep transactions short and never span a network
call. Watch `pg_stat_activity` for idle-in-transaction sessions, lock waits, and serialization failures
per second. In interviews, expect to be given a read-modify-write and asked which level or lock fixes it,
to explain write skew, and to say why a long transaction slows down a table it never wrote to.

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
