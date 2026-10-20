---
id: postgresql-transactions-and-migrations
module: node-ts-for-ai
name: PostgreSQL transactions & migrations
kind: practice
difficulty: core
est_minutes: 18
status: reviewed
related:
  - backend-fundamentals/postgresql-transactions-and-isolation-levels
  - backend-fundamentals/sql
  - fastapi-for-ai/alembic-migrations
  - fastapi-for-ai/async-database-access-and-connection-pooling
  - system-design/outbox-inbox-patterns
  - node-ts-for-ai/bullmq-and-durable-jobs
  - node-ts-for-ai/docker-ci-cd-deployment
sources:
  - Official docs: PostgreSQL
  - Official docs: node-postgres (pg)
  - Official docs: Drizzle ORM
  - Official docs: Prisma
---

## TL;DR
An AI backend's Postgres rows (conversations, agent runs, tool results, token budgets, embeddings)
change in groups, and the schema changes every sprint. Transactions make a group of writes atomic and
let concurrent agents contend safely; in Node the trap is that a pool hands out a different connection
per query, so a transaction must pin one client. Migrations version the schema as ordered, recorded,
forward-only scripts so every environment reaches the same state and a deploy never locks a live table
for minutes.

## Why it exists
Consider finishing one step of an agent run: insert the tool result, append the assistant message,
decrement the tenant's monthly token budget, and enqueue the next step. If the process dies between the
second and third statement the budget is never charged. If two parallel runs for the same tenant both
check "1,200 tokens left", both pass, and both run `remaining = remaining - 1000`, the tenant ends at
minus 800; if instead each writes back the value it computed in JavaScript (`remaining = 200`), one
charge is silently lost. Without transactions you write compensating code for every interleaving and
still miss some.

The Node-specific failure is subtler. `pg`'s `Pool.query()` borrows a connection per call.
`await pool.query('BEGIN'); await pool.query('INSERT ...'); await pool.query('COMMIT')` is three
statements on potentially three different connections. Under concurrency the INSERT can autocommit on
connection B while connection A goes back to the pool still inside an open transaction, where it either
sits idle in transaction or silently absorbs some other request's statements until a stray `COMMIT`
lands on it. Nothing errors. In a single-user test the pool often hands back the same connection each
time, so the bug passes locally; in production the data is simply not atomic, and an idle-in-transaction
connection holds its locks until it blocks a migration.

Schema change has the same "it works locally" trap. Running `ALTER TABLE` by hand on production means
staging and local disagree, nobody can reproduce last month's schema for a bug report, and a replica
starting mid-deploy sees columns its code does not know. Migrations exist to make schema a versioned
artifact that moves through environments with the code, and to make the operational properties of each
change (locks, rewrites, index builds on million-row embedding tables) explicit before they hit
production.

## How it works
### Transactions on one client
Postgres transactions are per connection. In Node you check a client out of the pool, run `BEGIN`,
execute every statement on that same client, then `COMMIT` or `ROLLBACK`, and release the client in
`finally` *because* a leaked client is a leaked connection: the pool silently shrinks until requests
hang on `connect()`. Every library wraps this: `pg` leaves it to you, Drizzle exposes
`db.transaction(async (tx) => { ... })`, Prisma has interactive `$transaction(async (tx) => { ... })`,
Kysely and Knex have equivalents. The callback form exists *because* it makes rollback-on-throw and
release impossible to forget. Inside the callback use `tx`, never the outer `db`: the outer handle goes
back to the pool and escapes the transaction. Postgres has no true nested transactions; a nested
`tx.transaction(...)` in Drizzle or Knex becomes a `SAVEPOINT`, so an inner failure can roll back to the
savepoint while the outer transaction continues. If the client errors at the socket level, release it
with `client.release(err)` so the pool destroys it instead of handing a broken connection to the next
caller.

### Isolation and contention
Postgres defaults to READ COMMITTED: each statement sees data committed before it started. That is
enough for most inserts but not for read-modify-write on a shared row such as a budget counter. Three
tools:
- `SELECT ... FOR UPDATE` locks the row so concurrent transactions queue behind you. Cheap, explicit,
  and the usual answer for counters and "claim this job" patterns.
- Atomic updates with a predicate: `UPDATE budgets SET remaining = remaining - $1 WHERE tenant = $2 AND
  remaining >= $1`, then check the row count. There is no lock to reason about *because* the condition
  and the write are one statement. The reason it is safe even at READ COMMITTED: when two such updates
  hit the same row, the second blocks on the row lock until the first commits, then re-evaluates its
  `WHERE` against the newly committed row version. With 1,200 remaining and two charges of 1,000, the
  first leaves 200, the second re-checks `200 >= 1000`, matches zero rows, and the caller sees
  "exhausted". A `SELECT` followed by a JavaScript check and an `UPDATE ... SET remaining = $computed`
  has no such re-check, which is exactly the lost-update bug.
- REPEATABLE READ gives the whole transaction one snapshot; if it tries to update a row that a
  concurrent transaction changed and committed after the snapshot, it aborts with `40001` rather than
  overwrite. It prevents lost updates but not every multi-row anomaly (write skew).
- SERIALIZABLE isolation makes the database detect anomalies and abort one transaction with SQLSTATE
  `40001`. Your code must retry the whole transaction on `40001` (and on `40P01`, deadlock) in a small
  bounded loop, *because* the abort is the mechanism, not a bug.

Keep transactions short and never hold one open across an LLM call: a 20-second completion inside a
transaction pins a connection, holds locks and blocks the next migration. Do the model call, then open a
transaction for the writes. Back the rule with server-side safety nets: `idle_in_transaction_session_timeout`
kills a session that sits inside an open transaction doing nothing (the leaked-client case), and
`statement_timeout` bounds any single query. Both can be set per role or per connection, so the
application role can be strict while the migration role is not.

### Idempotency and the outbox
Retries from queues and clients mean the same logical write arrives twice. A unique constraint plus
`INSERT ... ON CONFLICT DO NOTHING` (or `DO UPDATE`) turns the second arrival into a no-op inside the
same transaction. When a write must also trigger a job or a webhook, insert an outbox row in the same
transaction and let a relay worker publish it, *because* "commit, then enqueue" can lose the enqueue and
"enqueue, then commit" can run the job before the data exists.

### Migrations
A migration tool (Drizzle Kit, Prisma Migrate, Knex, node-pg-migrate, or SQL-first tools such as dbmate
and Flyway) keeps ordered files plus a table recording which have been applied, and `migrate` applies
the missing ones in order, usually inside a transaction (tools differ: some wrap each file, some the
whole batch, and some need an explicit opt-out per file). The practices that matter in production:
- **Forward-only, expand/contract.** Renaming a column is: add the new column; deploy code that writes
  both and reads new-or-old; backfill in batches; deploy code that uses only the new one; drop the old.
  Each step is compatible with the version before and after *because* during a rolling deploy both
  versions run at once. Down migrations exist in most tools but are rarely the production rollback:
  once new code has written data in the new shape, reversing the schema destroys it, so you roll back
  the *code* (which the expand step keeps compatible) and fix forward.
- **Backfill in batches, outside the DDL.** `UPDATE ... WHERE id BETWEEN $1 AND $2` in chunks of a few
  thousand rows, each its own short transaction, *because* one giant `UPDATE` holds row locks on
  every touched row until it commits, generates a burst of WAL that replicas must replay, and leaves
  dead tuples that vacuum cannot reclaim while the transaction is open.
- **Know which statements lock.** `ADD COLUMN` with a constant (non-volatile) default is a
  metadata-only change since Postgres 11; `ALTER COLUMN ... SET NOT NULL` on an existing column, or
  adding a CHECK or foreign key, scans the table under a lock that blocks writes. Use
  `ADD CONSTRAINT ... NOT VALID` and then `VALIDATE CONSTRAINT` in a later step so the scan runs under a
  weaker lock. For NOT NULL specifically: add `CHECK (col IS NOT NULL) NOT VALID`, validate it, then
  `SET NOT NULL`, which since Postgres 12 uses the validated check instead of rescanning.
- **Indexes concurrently.** `CREATE INDEX CONCURRENTLY` does not block writes but cannot run inside a
  transaction block, so the migration file must opt out of the tool's wrapping transaction. This matters
  for pgvector HNSW or IVFFlat indexes on large embedding tables, which can take minutes to hours to
  build depending on row count, dimensions and `maintenance_work_mem`.
- **`lock_timeout`.** Set `SET lock_timeout = '5s'` at the top of a migration: a DDL statement waiting
  for a lock queues *every* later query behind it, so failing fast and retrying off-peak is safer than
  waiting.
- **Run migrations as a separate step** (CI job or init container), not inside every replica's startup,
  and make sure a `pg_advisory_lock` guards the run so two runners cannot apply the same file
  concurrently (many tools take one automatically; check yours rather than assuming). Point the
  migration runner at Postgres directly, not through a transaction-mode pooler such as PgBouncer:
  session-level advisory locks and `SET lock_timeout` belong to a server connection that the pooler may
  swap between statements.

## Example
A `withTransaction` helper on raw `pg` that pins one client, releases it on every path and retries
transient aborts, plus a step recorder that is idempotent (unique step id) and charges the budget with
an atomic predicate update so no explicit row lock is needed. The SQL file shows an expand step with a
concurrent index, which must run outside the tool's transaction.

```ts
import { Pool, PoolClient } from "pg";
const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 10 });

export async function withTransaction<T>(fn: (tx: PoolClient) => Promise<T>, retries = 3): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    const client = await pool.connect();                // one connection for the whole transaction
    let broken = false;
    try {
      await client.query("BEGIN");                      // add "ISOLATION LEVEL SERIALIZABLE" if you rely on it
      const result = await fn(client);
      await client.query("COMMIT");
      return result;
    } catch (err: any) {
      await client.query("ROLLBACK").catch(() => { broken = true; });   // socket gone: do not reuse
      const transient = err.code === "40001" || err.code === "40P01";   // serialization failure / deadlock
      if (!transient || attempt >= retries) throw err;
    } finally {
      client.release(broken);                           // always release; `true` destroys a broken client
    }
  }
}

export async function recordStep(tenant: string, runId: string, stepId: string, tokens: number, content: string) {
  return withTransaction(async (tx) => {
    const inserted = await tx.query(                    // idempotent: a redelivered job hits the unique key
      "INSERT INTO run_steps (run_id, step_id, content) VALUES ($1, $2, $3) ON CONFLICT (step_id) DO NOTHING",
      [runId, stepId, content]);
    if (inserted.rowCount === 0) return "duplicate";
    const charged = await tx.query(                     // atomic read-modify-write, no FOR UPDATE needed
      "UPDATE budgets SET remaining = remaining - $1 WHERE tenant = $2 AND remaining >= $1", [tokens, tenant]);
    if (charged.rowCount === 0) throw new Error("budget exhausted");    // rolls the insert back too
    await tx.query("INSERT INTO outbox (topic, payload) VALUES ('step.completed', $1)", [{ runId, stepId }]);
    return "recorded";
  });
}
```

```sql
-- 0007_add_embedding.sql  (expand step; tool configured NOT to wrap this file in a transaction)
SET lock_timeout = '5s';
ALTER TABLE documents ADD COLUMN embedding vector(1536);        -- nullable: metadata-only change
CREATE INDEX CONCURRENTLY documents_embedding_hnsw ON documents USING hnsw (embedding vector_cosine_ops);
```

## Trade-offs
- Pinning a client per transaction gives atomicity but holds a pooled connection for the transaction's lifetime; with `max: 10` and 20-second LLM calls inside transactions the pool becomes the bottleneck, so keep model calls outside.
- SERIALIZABLE removes whole classes of anomalies without hand-placed locks, but it aborts under contention and forces retry loops; at low contention it is nearly free, while on hot rows `FOR UPDATE` or atomic predicate updates perform better.
- ORMs and query builders (Prisma, Drizzle, Kysely) make transaction scoping hard to get wrong and generate migrations from a schema file, but generated migrations know nothing about locks, concurrency or backfills; large tables need hand-edited SQL.
- Expand/contract migrations are safe under rolling deploys but take several deploys instead of one; for a table of a few thousand rows on a single replica a direct change is fine, and the discipline pays off once the table is large or replicas overlap.
- Running migrations as a separate pipeline step avoids races between replicas, but code and schema can then be deployed out of order, so every migration must be compatible with the previous code version.

## Pitfalls
- Using `pool.query('BEGIN')`: statements land on different connections; the symptom is non-atomic data with no error, plus connections stuck `idle in transaction` in `pg_stat_activity`.
- Using the outer `db` instead of `tx` inside a Drizzle or Prisma transaction callback; the symptom is a write that persists although the transaction rolled back, or a deadlock when the outer query waits on a lock the transaction holds.
- Forgetting `client.release()` on an error path; the symptom is the service hanging on `pool.connect()` after exactly `max` failures.
- `CREATE INDEX CONCURRENTLY` inside the migration tool's transaction; the symptom is `cannot run inside a transaction block`, and a failed concurrent build leaves an INVALID index that must be dropped by hand.
- Adding a NOT NULL column with an inline backfill in one statement on a large table; the symptom is a deploy that holds an exclusive lock for minutes while every API request queues behind it.
- Running migrations through PgBouncer in transaction mode; the symptom is `lock_timeout` apparently ignored or an advisory lock that never seems to be held, because consecutive statements ran on different server connections.
- Running migrations on container start with several replicas and no advisory lock; the symptom is duplicate-apply errors or two runners racing on the same `ALTER`.

## When to use
Open a transaction whenever two or more writes must succeed together or a read must be consistent with
a following write: run bookkeeping, budget charges, message plus embedding inserts, outbox rows. Do not
wrap LLM or HTTP calls in one. Prefer atomic predicate updates and `FOR UPDATE` for hot counters;
reserve SERIALIZABLE for invariants that span several rows. For schema, every change goes through a
migration file reviewed for its lock footprint; anything touching a table beyond a few hundred thousand
rows gets the expand/contract treatment and concurrent index builds. Watch `pg_stat_activity` for
idle-in-transaction sessions, transaction duration and lock waits. Interviewers probe exactly the
pool-per-statement trap, the retry on `40001`, and how to add a NOT NULL column to a live table.

## Role lens
- **Engineer:** Writes the `withTransaction` helper, keeps model calls outside it, handles `40001` retries, and reviews each generated migration for lock and backfill behaviour before merging.
- **Architect:** Sets the policy: which invariants are transactional, where idempotency keys and the outbox live, the default isolation level, and that migrations run as a gated pipeline step with expand/contract.
- **FDE:** Applies migrations inside a customer's VPC where a DBA controls maintenance windows, and explains why an embedding index must be built concurrently rather than during a deploy.

## Check yourself
**Q:** A worker charges a tenant's token budget and records an agent step. Under load, budgets
occasionally go negative and some steps appear twice. Diagnose the two bugs and fix them without
changing the isolation level.
**A:** Negative budgets mean a check-then-act race: the code reads `remaining`, checks it in JavaScript,
then decrements, so two transactions both pass the check against the same value and both subtract.
(Writing back a value computed in JavaScript would instead lose a charge, which is the same race with a
different symptom.) Replace it with a single predicate
update, `UPDATE budgets SET remaining = remaining - $1 WHERE tenant = $2 AND remaining >= $1`, and treat
a zero row count as "exhausted"; or lock with `SELECT ... FOR UPDATE` first. Duplicate steps mean
at-least-once delivery from the queue with no idempotency: add a unique constraint on the step id, use
`INSERT ... ON CONFLICT DO NOTHING`, and only charge when the insert actually inserted a row. Both must
run on one pinned client inside one transaction so a failure after the charge rolls the charge back.
Finally confirm the code is not calling `pool.query('BEGIN')`, which would scatter the statements across
connections.

## Quiz
### Q: Why is `await pool.query('BEGIN'); await pool.query('INSERT ...'); await pool.query('COMMIT')` wrong with node-postgres?
- [ ] `BEGIN` must be uppercase and parameterised to be recognised
- [x] Each `pool.query` may use a different connection, so the INSERT can run outside the transaction
- [ ] The pool rejects `COMMIT` unless autocommit is disabled
- [ ] Transactions require the `pg-transaction` extension
> Transactions are a property of a single connection. The pool borrows a connection per call, so the INSERT can autocommit on another connection while the first sits idle in transaction, and no error is raised.

### Q: You need an HNSW index on a 5-million-row embeddings table in production. Which approach avoids blocking writes?
- [ ] `CREATE INDEX` inside the migration's transaction so it can roll back
- [ ] Drop and recreate the table with the index defined inline
- [x] `CREATE INDEX CONCURRENTLY` in a migration configured to run outside a transaction block
- [ ] Add the index at application startup with a retry loop
> Concurrent index builds allow reads and writes during the build but cannot run inside a transaction block, so the migration tool's wrapping transaction must be disabled for that file; a failed build leaves an invalid index to drop.

### Q: Your transaction retry helper catches SQLSTATE 40001. When does that error actually occur?
- [ ] Whenever a unique constraint is violated
- [ ] When the pool has no free connections
- [x] When Postgres aborts a transaction under REPEATABLE READ or SERIALIZABLE because a concurrent commit made it unserializable
- [ ] When a statement exceeds `statement_timeout`
> `40001` is `serialization_failure`: the database detects that the transaction's reads are inconsistent with a concurrent commit and aborts it, expecting the application to retry; unique violations are `23505` and timeouts are `57014`.
