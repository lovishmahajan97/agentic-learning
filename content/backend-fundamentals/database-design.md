---
id: database-design
module: backend-fundamentals
name: Database design
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - backend-fundamentals/sql
  - backend-fundamentals/nosql
  - backend-fundamentals/postgresql-transactions-and-isolation-levels
  - fastapi-for-ai/sqlalchemy-sqlmodel
  - fastapi-for-ai/alembic-migrations
  - system-design/multi-tenancy
  - agent-reliability/state-and-checkpoint-stores
  - rag-retrieval/vector-databases
sources:
  - Codd, 1970 — A Relational Model of Data for Large Shared Data Banks
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Official docs: PostgreSQL
---

## TL;DR
A schema is the one part of a system that outlives every version of the code, so it has to encode the
facts that must stay true (keys, constraints, ownership) rather than the shape of today's screens.
Normalize so each fact lives in one place and cannot drift, then denormalize deliberately where a measured
read path needs it. Design indexes from the queries you will actually run, not from the columns you have.

## Why it exists
Code is rewritten every year; the data it produced is still there a decade later. If the storage layout is
whatever the first endpoint happened to need, three things break quickly. First, the same fact ends up
stored in several rows (a user's email copied into every order), and the copies disagree after the first
update that misses one. That is an *update anomaly*, and no amount of application discipline fixes it,
because the next engineer does not know the copies exist. Second, invariants that live only in code
("a message always belongs to exactly one conversation") get violated by the one script, migration or
manual fix that bypasses the code, leaving orphans that crash reads months later. Third, queries that were
fast at ten thousand rows become table scans at ten million, and by then the data layout is load-bearing
for a dozen services.

The naive alternative, "store JSON blobs and figure it out later", postpones all three problems to the
moment the data is largest and the original authors are gone. Database design is the practice of deciding
up front which facts exist, which entity owns each one, which rules the database itself must enforce, and
which access paths need to be cheap. For AI systems this matters more, not less: conversation history,
tool-call logs, embeddings and evaluation results are the product's memory, and they are queried by
humans, dashboards and retrieval code long after the request that wrote them.

## How it works
### Entities, keys and relationships
Start by naming the nouns the business reasons about (tenant, user, conversation, message, tool call, run,
document, chunk) and the relationships between them with their cardinality: a conversation has many
messages, a message has zero or more tool calls, a document has many chunks. Each entity gets a table
and a primary key. A **surrogate key** (bigint identity or UUID) is the default *because* natural keys
(email, external IDs) change, get reused, or turn out not to be unique. If you use UUIDs, prefer a
time-ordered variant (UUIDv7) over random UUIDv4 for primary keys, because a B-tree index on random keys
inserts into random pages, which defeats cache locality and bloats the index as the table grows.
Relationships become foreign keys, and the database enforces them so an orphan cannot exist even when
a script bypasses the application.

### Normalization: one fact, one place
Normal forms are rules for removing redundancy, and they follow from one idea: a table should describe
one kind of thing, and every non-key column should depend on the whole key and nothing else. In practice:

1. **1NF:** no repeating groups or lists packed into a column; `tags = "a,b,c"` becomes a `tags` table or
   an array column with proper indexing.
2. **2NF:** with a composite key, no column may depend on only part of it; in a
   `(conversation_id, user_id)` membership table, `user_name` depends on `user_id` alone and belongs in
   `users`.
3. **3NF:** no non-key column may depend on another non-key column. If `messages` stores `user_id` and
   `user_email`, the email depends on `user_id` rather than on the message, so it belongs in `users`.
   Otherwise changing an email means rewriting every message row.

Normalization is the default *because* writes are where consistency is decided; a normalized schema makes
the correct write the only possible write. The cost is joins on read, which is where denormalization comes
in: a `conversations.last_message_at` column duplicated from `messages` makes the inbox query cheap, but
now it must be maintained in the same transaction as the insert. Denormalize when a specific query is
measured to be slow, and write down the invariant you are now responsible for.

### Constraints are invariants the database guarantees
`NOT NULL`, `UNIQUE`, `CHECK (tokens_used >= 0)`, foreign keys with explicit `ON DELETE` behaviour, and
enum-like `CHECK (role IN ('user','assistant','tool'))` are the cheapest tests you will ever write: they
run on every write path, including the ones you forgot. A partial unique index (`CREATE UNIQUE INDEX ... ON
documents (tenant_id, external_id) WHERE deleted_at IS NULL`; a plain `UNIQUE` constraint cannot take a
`WHERE`) encodes "unique among live rows", which code alone gets wrong under concurrent inserts.

### Types carry meaning
Use `timestamptz`, never bare `timestamp`. A `timestamptz` is stored as an absolute instant (UTC
internally); a bare `timestamp` stores only a wall-clock reading, so which instant it means depends on
whatever zone the writer happened to assume, and rows written from servers in different zones cannot be
compared. Use `numeric` for money and token prices, never `float`, because binary floats cannot
represent 0.1 exactly and the rounding errors accumulate in invoices. Use `jsonb` for genuinely
semi-structured payloads (raw model responses, tool arguments), and pull out any field you filter or join
on into a real column with a real index. Store a `schema_version` next to any jsonb you will parse later.

### Multi-tenancy and deletion from day one
Every tenant-owned row carries `tenant_id`, every index that serves a tenant-scoped query leads with it,
and every query filters on it; PostgreSQL row-level security can enforce the filter when you do not trust
every code path. A plain foreign key on `conversation_id` still lets a message carry tenant A's id while
pointing at tenant B's conversation, because nothing compares the two columns. Make the parent's
`(tenant_id, id)` unique and have the child reference that pair, so the database itself refuses a
cross-tenant link. Decide how deletion works before the first user asks for it: a `deleted_at` soft-delete
column keeps audit history but every query must exclude it; hard deletes with `ON DELETE CASCADE` are
simpler but irreversible. Retention and right-to-erasure requests are much easier when data is owned by
one clear parent row.

### Indexes follow queries
An index is a sorted copy of some columns that makes one access path cheap and every write slightly more
expensive. The design question is never "which columns are important" but "which `WHERE`/`ORDER BY`
patterns run often". A composite index on `(conversation_id, created_at)` serves "messages of this
conversation in order" in one range scan; the same two columns in the other order do not. Equality
columns go first, the range or sort column last, *because* the B-tree is sorted by the first column, then
the second within ties: equality on the leading column narrows to one contiguous slice, inside which the
next column is already in order. Put the range column first and the matching entries for one conversation
are scattered across the whole range. Foreign-key columns usually need an index because joins
and cascading deletes look rows up by them, and PostgreSQL does not create one automatically on the
referencing side.

### Evolving the schema
The schema will change while the old code is still running, so every change is designed to be compatible
with both versions for the length of a deploy. That is the **expand/contract** rule: add the new column or
table, write to both, backfill in batches, move reads, and only then drop the old one. Renaming a column
in one step breaks every instance still running the previous release. Some operations are cheap and some
rewrite or lock the table: since PostgreSQL 11, adding a column with a constant default does not rewrite
existing rows, while changing a column's type generally does; `CREATE INDEX CONCURRENTLY` builds an index
without blocking writes at the cost of a slower build that can fail and leave an invalid index to drop.
Review every migration for which locks it takes on a large, hot table.

## Example
A minimal schema for an LLM application. Note the tenant column on every row (and the composite foreign
key that keeps a message in its conversation's tenant), the constraints that encode
rules, the jsonb for raw payloads next to the extracted columns (`model`, token counts) that queries
actually filter and aggregate on, the composite index built for the one hot query (load a conversation's
messages in order), and the plain index on the foreign key that cascading deletes need.

```sql
CREATE TABLE conversations (
  id           uuid PRIMARY KEY,                 -- UUIDv7 generated by the app
  tenant_id    uuid NOT NULL,
  user_id      uuid NOT NULL,
  title        text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz,
  UNIQUE (tenant_id, id)                         -- target for tenant-consistent FKs
);

CREATE TABLE messages (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  conversation_id uuid NOT NULL,
  FOREIGN KEY (tenant_id, conversation_id)       -- a message cannot point into another tenant
    REFERENCES conversations (tenant_id, id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('system','user','assistant','tool')),
  content         text NOT NULL,
  model           text,
  input_tokens    integer CHECK (input_tokens >= 0),
  output_tokens   integer CHECK (output_tokens >= 0),
  raw_response    jsonb,                     -- provider payload, never filtered on directly
  schema_version  smallint NOT NULL DEFAULT 1,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- the hot path: "messages of one conversation, oldest first"
CREATE INDEX messages_by_conversation ON messages (conversation_id, created_at);
-- the tenant-scoped dashboard query: spend per tenant per day
CREATE INDEX messages_by_tenant_time ON messages (tenant_id, created_at);

CREATE TABLE tool_calls (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id     uuid NOT NULL,                -- child tables carry the tenant too
  message_id    bigint NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  tool_name     text NOT NULL,
  arguments     jsonb NOT NULL,
  status        text NOT NULL CHECK (status IN ('pending','succeeded','failed')),
  idempotency_key text,
  UNIQUE (tenant_id, idempotency_key)        -- a retried call cannot be recorded twice
);
-- FK lookup for joins and for ON DELETE CASCADE from messages
CREATE INDEX tool_calls_by_message ON tool_calls (message_id);
```

## Trade-offs
- Normalization guarantees one fact in one place at the cost of joins; it flips toward denormalization
  only when a specific read path is measured to be slow and its duplicate can be maintained transactionally.
- Surrogate keys are stable and cheap to join, but you must still add a unique constraint on the natural
  identifier or duplicates will silently appear.
- UUID keys can be generated client-side and merged across systems, but random UUIDs cost index locality
  and 16 bytes per reference versus 8 for bigint; time-ordered UUIDs recover most of the locality.
- `jsonb` absorbs schema changes without migrations, but every field you later filter on without a
  column and index becomes a sequential scan, and nothing enforces its shape.
- Every index speeds one read pattern and taxes every insert and update; on write-heavy tables such as
  event logs, each extra index is a measurable throughput cost.
- Foreign keys and `CHECK`s cost a lookup per write but catch bugs at the only point that sees every path;
  dropping them "for speed" trades a few microseconds for data you can no longer trust.

## Pitfalls
- Omitting `tenant_id` from child tables and relying on joins to the parent for scoping; the symptom is a
  query that returns another tenant's rows the first time someone forgets the join.
- Using `float` for money or token prices; the symptom is invoices that are off by a cent and tests that
  compare with tolerances.
- Bare `timestamp` columns; the symptom is rows written before and after a server or container time-zone
  change that are hours apart in meaning but adjacent in value, and "yesterday" reports that disagree.
- Indexing columns rather than queries; the symptom is ten single-column indexes, slow writes, and the one
  hot query still doing a sort because no composite index matches its `WHERE` and `ORDER BY`.
- Entity-attribute-value tables ("one table of key/value pairs for everything") to avoid migrations; the
  symptom is five-way self-joins to reconstruct one object and no constraints on anything.
- Storing multi-megabyte payloads (full prompts, document text) in the hottest table; the symptom is
  bloated tables, slow vacuums and a cache that no longer holds the working set.
- Shipping a one-step rename or type change on a large table; the symptom is errors from instances still
  on the previous release, or a migration that holds a lock for minutes while every write queues behind it.
- Designing deletion last; the symptom is a right-to-erasure request that takes a week because data is
  scattered across tables with no ownership chain.

## When to use
Reach for deliberate design whenever data will be read by anything other than the code that wrote it:
dashboards, evaluations, audits, other services, or future you. For a throwaway prototype, a single jsonb
column is fine; the moment a second query pattern appears, extract real columns. Measure before
denormalizing: look at `EXPLAIN ANALYZE` on the slow query and at the write rate of the table you would
duplicate into. In day-to-day work this shows up as migration reviews ("why is there no index for the
query this endpoint runs?", "what happens to messages when a conversation is deleted?"). In interviews,
expect to be handed a product description and asked to draw tables, keys and indexes, then defend each
constraint and explain which query each index serves.

## Role lens
- **Engineer:** Writes the migration, picks types and constraints, and adds the composite index that
  matches the query the new endpoint runs, verified with `EXPLAIN`.
- **Architect:** Owns the ownership model: which service owns which tables, how tenancy is enforced, the
  retention and deletion policy, and when a read model may be denormalized.
- **FDE:** Maps the customer's existing schema and identifiers onto the product's model, finds the missing
  tenant or ownership keys, and plans how their history will be imported and later erased.

## Check yourself
**Q:** A chat product stores each conversation as one jsonb document containing all its messages. Reads
are fast today. What breaks as it grows, and how would you restructure it?
**A:** Appending a message rewrites the whole document, so write cost grows with conversation length and
long conversations create bloat and lock contention. You cannot index or constrain individual messages,
so "all messages by this tool across tenants" or "token spend per day" become full scans that parse JSON.
Deleting one message or one user's data means rewriting documents. The fix is a `messages` table with
`tenant_id`, `conversation_id`, `role`, token counts and `created_at`, a composite index on
`(conversation_id, created_at)` for the conversation view, and a second on `(tenant_id, created_at)` for
reporting; keep the raw provider response in a jsonb column on the message row, where it is not filtered
on. If the inbox view needs `last_message_at`, denormalize that single column and maintain it in the same
transaction as the insert.

## Quiz
### Q: Why is a composite index on (conversation_id, created_at) better than two single-column indexes for loading a conversation's messages in order?
- [ ] Composite indexes are stored in memory while single-column indexes are on disk
- [x] One range scan over the composite index returns the rows already in the right order, while separate indexes require combining results and then sorting
- [ ] Single-column indexes cannot be used together in the same query
- [ ] The composite index takes less space than two single-column indexes
> The planner can walk the composite index from the first matching conversation_id in created_at order, so
> no sort step is needed. With single-column indexes the planner must either fetch the conversation's rows
> and sort them, or combine indexes in a bitmap scan, which also loses order and needs a separate sort.

### Q: A team drops foreign keys "to make inserts faster". Which failure is this most likely to cause?
- [ ] Queries become slower because the planner can no longer use joins
- [ ] The database refuses to create indexes on the former foreign-key columns
- [x] Orphan rows appear whenever any script or bug deletes a parent without its children, and reads fail later
- [ ] Transactions can no longer be rolled back on those tables
> A foreign key is checked on every write path, including the ones the application code does not cover.
> Without it, orphans accumulate silently and surface as missing-join bugs or crashes much later.

### Q: When is denormalizing a column such as conversations.last_message_at the right call?
- [ ] Whenever a join would otherwise be required, because joins are always expensive
- [ ] Never, because it violates third normal form
- [x] When a specific frequent query is measured to be slow and the duplicate can be updated in the same transaction as the source row
- [ ] Only when the database does not support indexes on the child table
> Denormalization buys read speed by taking on a consistency obligation. It is justified by measurement
> and made safe by maintaining the copy transactionally; doing it by default reintroduces update anomalies.
