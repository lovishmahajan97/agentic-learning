---
id: alembic-migrations
module: fastapi-for-ai
name: Alembic migrations
kind: tool
difficulty: core
est_minutes: 16
related:
  - fastapi-for-ai/sqlalchemy-sqlmodel
  - fastapi-for-ai/async-database-access-and-connection-pooling
  - fastapi-for-ai/integration-tests-with-real-dependencies
  - backend-fundamentals/database-design
  - backend-fundamentals/ci-cd
  - node-ts-for-ai/postgresql-transactions-and-migrations
  - llmops-platform/rollback
  - data-architecture/embedding-versioning-re-indexing-strategy
sources:
  - Official docs: Alembic (Tutorial; Auto Generating Migrations; Cookbook)
  - Official docs: PostgreSQL (ALTER TABLE; CREATE INDEX)
---

## TL;DR
Alembic is SQLAlchemy's migration tool: each schema change is a versioned Python script with an
`upgrade()` and `downgrade()`, chained by revision IDs, and the database records which revision it is
at. That turns "what schema does production have?" into a fact you can query and replay. Autogenerate
drafts scripts by diffing your models against the live database, but it is a draft: renames, data
moves and lock-heavy operations still need a human, and safe deploys need expand-contract.

## Why it exists
An AI backend's schema changes constantly: a `tool_calls` JSONB column on messages, a `status` and
`progress` on jobs, an `embedding vector(1024)` on chunks, then a second embedding column when you
switch models. Every environment (laptops, CI, staging, each customer's VPC) must end up with exactly
the same schema, and each must get there from wherever it is today.

The naive alternatives fail in predictable ways. `Base.metadata.create_all()` at startup only creates
tables that do not exist; it never adds a column to an existing table, so the first schema change
after launch silently does nothing and the app crashes on a missing column. Hand-run SQL files
pasted into a console have no record of which ones ran where, so staging and production drift, and
nobody can say whether a given customer deployment has the `idx_chunks_tenant` index. Writing ad-hoc
`ALTER` statements in app code that run "if the column is missing" spreads schema logic across the
codebase and races when ten replicas start at once.

Alembic's answer is the standard one from Rails and Django: an ordered, version-controlled list of
changes plus a one-row table in each database saying how far along the list it is. Because it reads
SQLAlchemy metadata, it can also propose the next change from your model diff instead of making you
type every column twice.

## How it works
### The revision chain
`alembic init migrations` creates `alembic.ini`, a `migrations/env.py` and a `versions/` folder. Each
file in `versions/` declares `revision = "7c1e..."` and `down_revision = "3d5f..."`, so the scripts form
a linked list (a directed graph, once branches appear). IDs are random hashes rather than sequence
numbers *because* two developers on separate branches would otherwise both create "migration 12".

The database stores its position in a table named `alembic_version` holding the current revision ID.
`alembic upgrade head` walks the graph from that ID to the newest revision, running each `upgrade()`
in order, and updates the row. `alembic downgrade -1` runs the latest `downgrade()`. `alembic current`
and `alembic history` show where you are. On PostgreSQL, DDL is transactional, so by default a failed
migration rolls back cleanly instead of leaving a half-altered table; on MySQL most DDL commits
implicitly, so a failure midway can leave you between two revisions.

### env.py: the bridge to your app
`env.py` is ordinary Python that Alembic runs for every command. It sets `target_metadata =
Base.metadata` (or `SQLModel.metadata`) so autogenerate knows the desired schema, and it builds the
connection. Read the URL from the same settings object the app uses, *because* a hard-coded URL in
`alembic.ini` is how migrations end up pointed at the wrong database. For an async app,
`alembic init -t async migrations` generates an `env.py` that creates an async engine and calls
`await connection.run_sync(do_run_migrations)`, since Alembic's operations themselves are synchronous.

### Autogenerate: a diff, not an oracle
`alembic revision --autogenerate -m "add embedding_model"` connects to a database at head, reflects its
schema, compares it to `target_metadata`, and writes `op.add_column`, `op.create_index` and similar
calls. It reliably sees added and dropped tables and columns, nullability changes, and indexes and
foreign keys that have names. It cannot see intent: a renamed column looks like one column dropped
and another added, which would destroy data if run as written. Server-default comparison is off
unless you enable it, and custom types (pgvector's `Vector`, enum changes on some backends) may need
manual imports or edits. Set a `naming_convention` on your `MetaData` *because* constraints then get
deterministic names that a later migration can drop by name.

### Operations that are not schema diffs
`op.execute()` runs raw SQL for things autogenerate never produces: `CREATE EXTENSION IF NOT EXISTS
vector`, a backfill `UPDATE`, or `SET lock_timeout`. Some PostgreSQL statements refuse to run inside a
transaction block, notably `CREATE INDEX CONCURRENTLY`, so Alembic provides
`op.get_context().autocommit_block()` for them. SQLite cannot alter most columns in place; Alembic's
`op.batch_alter_table` works around this by copying the table. `alembic upgrade head --sql` (offline
mode) renders the SQL without connecting, for DBAs who must review or run it themselves.

### Branches and merges
When two pull requests each add a revision on the same parent, the graph has two heads and
`alembic upgrade head` refuses to guess an order. `alembic merge heads` creates an empty revision
whose `down_revision` is a tuple of both, restoring a single head. A CI check that `alembic heads`
prints exactly one line catches this before deploy.

## Example
An expand step for re-embedding chunks with a new model, safe to run while the old code still serves
traffic. The new column is nullable with no default, so old code that never writes it keeps working.

```python
"""chunks: add embedding_model (expand step)

Revision ID: 7c1e2a9b4f10
Revises: 3d5f0e8a1b22
"""
from alembic import op
import sqlalchemy as sa

revision = "7c1e2a9b4f10"
down_revision = "3d5f0e8a1b22"


def upgrade() -> None:
    # Fail fast instead of queueing behind a long query and blocking every reader.
    op.execute("SET lock_timeout = '5s'")
    # Nullable, no default: a catalog-only change in PostgreSQL, no table rewrite.
    op.add_column("chunks", sa.Column("embedding_model", sa.Text(), nullable=True))
    # CREATE INDEX CONCURRENTLY is rejected inside a transaction block.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_chunks_embedding_model",
            "chunks",
            ["embedding_model"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_chunks_embedding_model",
            table_name="chunks",
            postgresql_concurrently=True,
        )
    op.drop_column("chunks", "embedding_model")
```

The deploy then runs in three releases. Release 1 applies this migration as a pre-deploy job, then
ships code that writes `embedding_model` for new chunks. A background job backfills old rows in
batches of a few thousand, committing per batch so no single transaction holds locks for minutes.
Release 2, once the backfill reports zero NULLs, adds `NOT NULL` (on a large table, via a `CHECK ...
NOT VALID` constraint validated separately, to avoid a long scan under a strong lock). Release 3, the
contract step, drops whatever the old column was, after no running code reads it.

## Trade-offs
- Autogenerate saves typing and catches forgotten columns, but it produces destructive drafts for
  renames; it is a net win only when every generated script is read in review.
- Running migrations as a separate pre-deploy job keeps replicas from racing, at the cost of a
  deploy step; tiny single-instance apps can run `upgrade head` at startup instead.
- Expand-contract gives zero-downtime changes but triples the releases per change; for an internal
  tool that can take a two-minute maintenance window, a single breaking migration is simpler.
- Writing real `downgrade()` functions buys fast rollback of schema-only changes, but a downgrade
  that drops a populated column destroys data; past the first deploy, roll forward instead.
- Offline `--sql` mode satisfies customers whose DBAs must review every statement, but you lose
  Python-side logic and must keep data migrations expressible as plain SQL.

## Pitfalls
- Accepting an autogenerated rename as written; the symptom is a migration containing
  `drop_column("content")` and `add_column("body")`, followed by an empty column in production.
- Importing app ORM models inside a data migration; months later the model has changed and the
  old migration fails on a fresh database with an attribute or column error.
- Adding an index without `CONCURRENTLY` on a large, hot table; writes block for the whole build
  and the symptom is a spike of request timeouts exactly during the deploy.
- Merging two branches that each added a revision; `alembic upgrade head` fails with a "multiple
  head revisions" error on the deploy pipeline, not on either developer's laptop.
- Running `alembic stamp head` to "fix" a failing upgrade; the version row now claims changes that
  were never applied, and the missing column surfaces later as an application crash.
- Forgetting `CREATE EXTENSION vector` or the `pgvector` import in the script; the upgrade fails
  with an unknown type error on a fresh customer database but passes on yours.

## When to use
Use Alembic for any SQLAlchemy or SQLModel service whose database outlives a single deploy, which is
every real one; `create_all()` is acceptable only in throwaway prototypes and test fixtures. Wire it in
early: an empty initial revision costs nothing, while retrofitting onto a drifted production database
means hand-writing a baseline and `stamp`ing it. In CI, run `upgrade head` against a real Postgres
from empty, assert a single head, and fail if autogenerate would still produce changes (recent Alembic
versions offer `alembic check` for this). In production, run migrations once per deploy as a job
before the new code rolls out, and design each migration to be compatible with the code already
running. In interviews, the expected depth is explaining expand-contract for a column rename and
why autogenerate cannot be trusted with it.

## Role lens
- **Engineer:** Writes and reviews each revision, fixes autogenerate's renames, adds lock timeouts
  and `CONCURRENTLY`, and keeps data migrations independent of current ORM models.
- **Architect:** Owns the migration policy: pre-deploy job versus startup, expand-contract as the
  default, roll-forward over downgrade, and which tables need a DBA review.
- **FDE:** Ships migrations into customer VPCs where DBAs demand offline SQL, extensions need
  approval, and the version table is the first thing checked when an install misbehaves.

## Check yourself
**Q:** Your chat service runs six replicas with rolling deploys. You need to rename `messages.content`
to `messages.body`. Autogenerate produced a script. What do you ship, and in what order?
**A:** Not the autogenerated script: it drops `content` and adds an empty `body`, losing every message,
and even a true `ALTER ... RENAME` breaks old replicas mid-rollout. Use expand-contract. Release 1:
migration adds nullable `body`; code writes both columns and reads `body` falling back to `content`.
Backfill `body` from `content` in batches. Release 2: code reads and writes only `body`; add the `NOT
NULL` constraint once the backfill is complete. Release 3: drop `content`. Each migration runs as a
pre-deploy job with a lock timeout, and each step is compatible with the code still running.

## Quiz
### Q: You rename the `content` attribute and column to `text` on a model and run autogenerate. What does the generated script typically contain?
- [ ] An `op.alter_column` call with `new_column_name="text"`
- [x] An `op.drop_column` for `content` and an `op.add_column` for `text`
- [ ] Nothing, because the column type and nullability did not change
- [ ] An error asking you to confirm the rename interactively
> Autogenerate compares reflected schema with metadata and has no record of intent, so it sees one
> column vanish and another appear. Running that draft would discard the data; a rename must be
> written by hand, or done via expand-contract.

### Q: After two feature branches merge, the deploy fails with "multiple head revisions". What is the correct fix?
- [ ] Run `alembic stamp head` on production so the version table matches the newest file
- [ ] Downgrade production by one revision and then upgrade again
- [x] Create a merge revision with `alembic merge heads` so the graph has a single head again
- [ ] Delete the `alembic_version` table and let Alembic rebuild it
> Both branches created revisions off the same parent, so the graph has two leaves and Alembic will not
> guess an order. A merge revision whose down_revision lists both restores one head. Stamping would
> mark unapplied changes as applied.

### Q: Why does a migration that runs `CREATE INDEX CONCURRENTLY` on PostgreSQL wrap it in `op.get_context().autocommit_block()`?
- [ ] Alembic cannot emit index operations without it
- [ ] It makes the index build faster by disabling write-ahead logging
- [ ] It lets `downgrade()` drop the index automatically
- [x] PostgreSQL rejects `CREATE INDEX CONCURRENTLY` inside a transaction block, and Alembic normally runs migrations in one
> Concurrent builds avoid blocking writes but cannot run in a transaction, while Alembic wraps each
> migration in one by default on PostgreSQL. The autocommit block commits first and runs that
> statement outside a transaction; it does not change logging or speed.
