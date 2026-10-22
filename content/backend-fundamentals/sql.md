---
id: sql
module: backend-fundamentals
name: SQL
kind: concept
difficulty: core
est_minutes: 16
status: crisp-verified
related:
  - backend-fundamentals/database-design
  - backend-fundamentals/postgresql-transactions-and-isolation-levels
  - backend-fundamentals/nosql
  - fastapi-for-ai/sqlalchemy-sqlmodel
  - node-ts-for-ai/postgresql-transactions-and-migrations
  - observability/token-cost-tracking
  - rag-retrieval/hybrid-search
sources:
  - Codd, 1970 — A Relational Model of Data for Large Shared Data Banks
  - Official docs: PostgreSQL
  - Winand — Use The Index, Luke
---

## TL;DR
SQL is declarative: you state which rows you want and a cost-based planner picks the algorithm. With the
right index a query survives 1000× data growth; without it, it collapses. Think in sets, and read the plan
instead of guessing.

## Why it exists
- **Navigational databases coupled code to layout.** Programs walked pointers from record to record, so
  every program encoded the physical layout, and changing the layout meant changing every program.
- **Codd separated what from how.** Data became relations (tables of tuples) and queries became algebra
  over them. SQL is the practical language from that, and the planner turns the *what* into a *how* at run
  time using statistics about the actual data.
- **The naive loop is N+1.** Fetch conversations, then each one's messages, then count tokens per message.
  Each round trip is a network hop (hundreds of microseconds to milliseconds), so 1,000 conversations cost
  1,001 queries and between half a second and several seconds of wall time.
- **The loop also hides the question.** The database never sees the whole request, so it cannot pick a hash
  join or use an index to do the work in one pass. One query with a join and an aggregate is one scan.
- **AI backends are heavy SQL users.** Per-tenant token spend, latency percentiles per model, the last N
  messages of a conversation, eval results joined to prompt versions, and hybrid retrieval mixing vector
  similarity with a metadata filter are each one query when written well, a slow fan-out when not.

## How it works
### Logical evaluation order
A `SELECT` is written in one order and evaluated in another:

1. `FROM` and `JOIN`: build the row set.
2. `WHERE`: filter rows.
3. `GROUP BY`: collapse into groups.
4. `HAVING`: filter groups.
5. `SELECT`: compute output columns, including window functions.
6. `DISTINCT`.
7. `ORDER BY`.
8. `LIMIT`/`OFFSET`.

This explains the classic confusions:
- **No `SELECT` alias in `WHERE`,** because `WHERE` runs first.
- **`HAVING` exists** because aggregates do not exist yet when `WHERE` runs.
- **Window functions see filtered, grouped rows** because they run during `SELECT`.

### Joins are set operations, not loops
- **Inner join** keeps rows that match on both sides.
- **Left join** keeps every left row and fills the right side with NULLs when nothing matches; that is how
  you find conversations with no messages (`WHERE m.id IS NULL`).
- **`EXISTS`/`NOT EXISTS`** express "has at least one" without duplicating rows the way a join would.

The engine picks the physical algorithm; you make the good ones possible with indexes and accurate statistics:

| Algorithm | Chosen when |
|---|---|
| **Nested loop** | one side is tiny, or has an index on the join key |
| **Hash join** | both sides are large and unsorted |
| **Merge join** | both sides are already sorted on the key |

### Aggregates and window functions
- **`GROUP BY` collapses rows.**
- **A window function computes per row without collapsing:**
  `SUM(...) OVER (PARTITION BY tenant_id ORDER BY day)` gives running totals, rankings and "latest row per
  group" in a single pass.
- **PostgreSQL idiom for "newest message per conversation":** `DISTINCT ON (conversation_id) ... ORDER BY
  conversation_id, created_at DESC`.

### How the planner decides
`EXPLAIN ANALYZE` shows the tree the planner chose, with real row counts and timings. Cost estimates come
from table statistics (row counts, value distributions gathered by `ANALYZE`).

| Scan | What it does | Cheapest when |
|---|---|---|
| Sequential | read everything | most rows qualify |
| Index | walk a B-tree to matching rows | few rows qualify |
| Bitmap | collect matching pages from one or more indexes, then read them in physical order | in between |
| Index-only | answer from the index alone, skipping the table | the index holds every column read, e.g. via `INCLUDE (input_tokens)` |

- **Sargable predicates.** A predicate can use an index when the indexed column appears bare on one side:
  `created_at >= $1` can; `date(created_at) = $1` cannot, because the index stores `created_at`, not its date.
- **Prefix `LIKE`.** `LIKE 'abc%'` can use a B-tree, but in PostgreSQL only if the index uses the `C`
  collation or the `text_pattern_ops` operator class. A default index under a locale such as
  `en_US.UTF-8` is not used for `LIKE`.
- **Infix `LIKE`.** `LIKE '%abc%'` cannot use a B-tree at all and needs a trigram (`pg_trgm`) index.
- **Index-only scans need vacuum.** They work only for pages the visibility map marks all-visible, one more
  reason vacuum health shows up in query latency.
- **Statistics go stale.** A table that grew 100× since the last `ANALYZE` gets bad plans; autovacuum
  usually re-analyzes, but can lag after a bulk load.

### NULL is not a value
SQL uses three-valued logic: a comparison with NULL is *unknown*, and `WHERE` keeps only rows where the
predicate is true.

- **`WHERE model <> 'gpt-x'`** silently drops every row whose `model` is NULL.
- **`x = NULL` never matches;** use `IS NULL` or `IS DISTINCT FROM`.
- **`NOT IN (subquery)` returns nothing** once the subquery yields a single NULL, because
  `a NOT IN (b, NULL)` expands to `a <> b AND a <> NULL`, which can never be true.
- **Arithmetic propagates NULL:** `input_tokens + output_tokens` is NULL if either side is.
- **`SUM` skips NULLs,** so a row with a missing count vanishes from a spend total instead of erroring.
- **`COUNT(*)` counts rows; `COUNT(col)` counts non-NULL values.** The difference is a common source of
  reports that disagree.

### Parameters, not string formatting
- **Parameters travel separately.** Placeholders (`$1`, `%s`, `?` depending on the driver) are sent apart
  from the SQL text.
- **The only reliable injection defence,** *because* the database never parses user input as SQL. It also
  lets the server reuse a prepared plan.
- **Interpolation is a vulnerability** regardless of how well you escape.

### Pagination and the OFFSET trap
- **`LIMIT 20 OFFSET 100000` still reads and discards 100,000 rows;** cost grows linearly with page depth.
- **Keyset pagination** (`WHERE (created_at, id) < ($last_ts, $last_id) ORDER BY created_at DESC, id DESC
  LIMIT 20`) seeks directly to the next page using the index and costs the same on page 1 and page 5,000.

### Where the ORM fits
- **Convenient for CRUD, dangerous for reporting.** Lazy-loaded relationships are the usual source of
  N+1, and complex aggregations are often clearer as SQL.
- **Working rule:** let the ORM handle simple writes and reads by key; write (and `EXPLAIN`) the hot or
  analytical queries by hand.

## Diagram
Follow a query from text to rows: the planner, fed by statistics, picks scans and joins; stale statistics
or a non-sargable predicate is where it goes wrong.

```mermaid
flowchart TD
    Q["SQL text plus parameters"] --> P["Parse and rewrite"]
    P --> PL["Cost-based planner"]
    ST[("Table statistics from ANALYZE")] --> PL
    IX[("Indexes")] --> PL
    PL --> S{"Few rows qualify?"}
    S -->|"yes, sargable"| IS["Index or index-only scan"]
    S -->|"no"| SS["Sequential or bitmap scan"]
    IS --> J["Join: nested loop, hash or merge"]
    SS --> J
    J --> A["Group, window, sort, limit"]
    A --> R["Result rows"]
```

## Examples
### Two queries an LLM backend runs constantly
- **Spend report:** per-tenant spend with a running total, one pass, a window function over the grouped rows.
- **Inbox view:** "newest message per conversation" via a `LATERAL` subquery (the `DISTINCT ON` form above
  is the alternative), with a keyset cursor so page depth does not change the cost.
- **Scaling note:** each page still evaluates the lateral lookup for every conversation the user owns.
  With thousands of conversations, the next step is a denormalized `conversations.last_message_at` with an
  index on `(tenant_id, user_id, last_message_at, id)`, so the cursor seeks straight to the page.

```sql
-- 1. Daily token spend per tenant for the last 7 days, with a running total.
--    No tenant filter, so a (tenant_id, created_at) index gives no simple range scan;
--    an index on created_at (or a BRIN index on an append-only table) serves this.
--    Day boundaries follow the session TimeZone setting.
SELECT tenant_id,
       date_trunc('day', created_at) AS day,
       SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0))        AS tokens,
       SUM(SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)))   -- window over groups
         OVER (PARTITION BY tenant_id ORDER BY date_trunc('day', created_at)) AS running_tokens
FROM   messages
WHERE  created_at >= now() - interval '7 days'      -- sargable: bare column vs constant
  AND  role = 'assistant'
GROUP  BY tenant_id, date_trunc('day', created_at)
ORDER  BY tenant_id, day;

-- 2. Inbox: the newest message of each conversation for one user, 20 at a time.
--    Keyset pagination: pass the last row's (created_at, id) back instead of an OFFSET.
SELECT c.id, c.title, m.content, m.created_at
FROM   conversations c
JOIN   LATERAL (
         SELECT content, created_at, id
         FROM   messages
         WHERE  conversation_id = c.id
         ORDER  BY created_at DESC, id DESC           -- id breaks timestamp ties
         LIMIT  1                                     -- hits (conversation_id, created_at)
       ) m ON true
WHERE  c.tenant_id = $1 AND c.user_id = $2 AND c.deleted_at IS NULL
  AND  (m.created_at, m.id) < ($3, $4)                -- previous page's last row; first page: ('infinity', 0)
ORDER  BY m.created_at DESC, m.id DESC
LIMIT  20;
```

Run each with `EXPLAIN (ANALYZE, BUFFERS)` and check:
1. **The scan on `messages` is an index scan,** not a sequential scan.
2. **Estimated rows are within an order of magnitude of actual.** A large mismatch means stale statistics
   or a predicate the planner cannot estimate.

- **The `id` tie-breaker matters** in both the `ORDER BY` and the cursor: two messages with the same
  `created_at` would otherwise have no defined order, and a page boundary between them could skip or
  repeat a row.
- **The `COALESCE` calls** stop a single row with a NULL count from dropping out of the total.

### Runnable: NULL silently changes answers
Standard-library SQLite follows the same three-valued logic as PostgreSQL here.

```python
import sqlite3
db = sqlite3.connect(":memory:")
db.executescript("""
CREATE TABLE messages (id INTEGER PRIMARY KEY, model TEXT, input_tokens INTEGER);
INSERT INTO messages (model, input_tokens) VALUES ('gpt-x', 10), ('other', 20), (NULL, 30), ('other', NULL);
CREATE TABLE blocked (model TEXT);
INSERT INTO blocked VALUES ('gpt-x'), (NULL);
""")
q = lambda sql: db.execute(sql).fetchall()
print(q("SELECT COUNT(*) FROM messages WHERE model <> 'gpt-x'"))          # [(2,)]  NULL row dropped
print(q("SELECT COUNT(*), COUNT(input_tokens), SUM(input_tokens) FROM messages"))  # [(4, 3, 60)]
print(q("SELECT COUNT(*) FROM messages WHERE model NOT IN (SELECT model FROM blocked)"))  # [(0,)]
print(q("""SELECT COUNT(*) FROM messages m
           WHERE NOT EXISTS (SELECT 1 FROM blocked b WHERE b.model = m.model)"""))   # [(3,)]
```

### Worked scenario: what N+1 costs an inbox endpoint
An endpoint lists 1,000 conversations and lazily loads each one's latest message (illustrative round-trip
times, not a benchmark).

| Approach | Queries | At 0.5 ms per round trip | At 3 ms per round trip |
|---|---|---|---|
| ORM loop (N+1) | 1,001 | ~0.5 s | ~3 s |
| One `LATERAL` join | 1 | one round trip plus query time | one round trip plus query time |

Each of the 1,001 queries looks fast in the log; the sum is the latency. The join lets the planner use
`(conversation_id, created_at)` once per conversation inside a single statement.

## Production patterns
- **Rank by total time, not mean.** Enable the `pg_stat_statements` extension and sort by `total_exec_time`
  weekly; a 2 ms query run a million times outranks a 5 s nightly report. Monitor the top ten after each
  release.
- **Log slow plans automatically.** Load the `auto_explain` module with `auto_explain.log_min_duration`
  set (for example 500ms) so production plans of slow queries land in the logs. Monitor for sequential
  scans on large tables and estimate-versus-actual row gaps.
- **Cap runaway queries.** Set `statement_timeout` per role or per transaction (for example a few seconds
  for the API role, longer for reporting), so one bad plan cannot pin connections. Monitor cancelled
  statements by route.
- **Keyset pagination everywhere users scroll.** Return an opaque cursor encoding `(created_at, id)` and
  back it with an index ending in those columns. Monitor p95 latency by page depth; it should be flat.
- **Detect N+1 in tests.** Count queries per request in integration tests (for example SQLAlchemy's
  `before_cursor_execute` event) and fail when a list endpoint's count grows with result size.

## Trade-offs
- **Declarative vs control.** The planner adapts to data growth, but you give up control of the
  algorithm; when it guesses wrong you fix statistics or indexes, not the query's "steps".
- **One big query vs several.** One joined query minimizes round trips but can produce a huge intermediate
  result; when a join multiplies rows (one conversation × many messages × many tool calls), two targeted
  queries are cheaper.
- **Index speed vs write cost.** Indexes make predicates cheap but each slows writes and uses memory; an
  hourly reporting query rarely justifies an index every insert pays for.
- **ORM convenience vs visibility.** ORMs speed CRUD and keep types in sync but hide the SQL; once a query
  touches more than two tables or an aggregate, hand-written SQL is usually faster and clearer.
- **Keyset vs OFFSET.** Keyset is constant-cost but cannot jump to "page 37"; OFFSET can, at linear cost,
  so pick by whether users actually jump or just scroll.
- **CTE readability vs fences.** Since PostgreSQL 12 the planner can inline a CTE referenced once, so
  readability no longer forces materialization, but `MATERIALIZED` is there when you need the optimisation fence.

## Pitfalls
- **The N+1 loop hidden behind an ORM relationship.** Symptom: hundreds of identical small queries in the
  log per request, each fast, together slow.
- **Wrapping an indexed column in a function** (`lower(email) = $1`, `date(created_at) = $1`). Symptom: a
  sequential scan in `EXPLAIN` despite an index. Rewrite as a range or add an expression index.
- **Mutable expression indexes.** The expression must be immutable, so on a `timestamptz` column index
  `((created_at AT TIME ZONE 'UTC')::date)`, not `date(created_at)`, which depends on the session time zone.
- **`NOT IN (subquery)` where the subquery can return NULL.** Symptom: silently zero rows. Use `NOT EXISTS`.
- **`SELECT *` in application code.** Symptom: a column added for a migration doubles network payload and
  breaks positional unpacking in a client.
- **Deep `OFFSET` pagination.** Symptom: page one in milliseconds, page two hundred in seconds, database CPU
  climbing as users scroll.
- **Building SQL with f-strings or template literals.** Symptom: a security finding, or a query that breaks
  on the first title containing an apostrophe.
- **Trusting plans from a small dev database.** Symptom: instant in staging, a sequential scan over 50
  million rows in production, because the planner's choice flips with size.

## When to use
- **Use when:** the question is relational: filtering, joining, aggregating or ranking rows across entities.
- **Write by hand:** anything that joins more than two tables, aggregates, or runs on a hot path; let the
  ORM handle simple key lookups and inserts.
- **Measure before shipping:** run `EXPLAIN ANALYZE` against production-sized data and confirm scan types
  and row estimates.
- **Measure after shipping:** watch `pg_stat_statements` for the queries with the highest total time, not
  the highest mean.
- **Interviews:** expect to write a query with a join, a `GROUP BY` and a window function, explain why a
  predicate cannot use an index, and diagnose an N+1 from a description of symptoms.

## Role lens
- **Engineer:** Writes the spend and inbox queries, replaces ORM loops with joins, and reads
  `EXPLAIN ANALYZE` to confirm each hot query uses the index it was designed for.
- **Architect:** Decides which questions are answered by the transactional database versus a warehouse or
  search index, and sets the rule that hot-path SQL is reviewed with its plan.
- **FDE:** Writes the one-off queries that prove value in a customer's data, and reads their slow-query
  logs to find the N+1 or missing index behind a complaint about latency.

## Check yourself
**Q:** A dashboard query filtering `messages` by `tenant_id` and the last 24 hours takes 8 seconds in
production but 20 ms in staging. There is an index on `(tenant_id, created_at)`. What do you check and why?
**A:** First run `EXPLAIN (ANALYZE, BUFFERS)` in production to see whether the index is used at all. If the
plan shows a sequential scan, the predicate is probably not sargable (a function wrapped around
`created_at`, or a type mismatch that makes PostgreSQL cast the column rather than the parameter, such as
an `integer` column compared with a `numeric` parameter) or the statistics are stale after rapid growth,
so `ANALYZE messages` and compare. If the
index is used but the query is still slow, look at actual versus estimated rows: one huge tenant can make
the range scan return millions of rows, in which case the aggregate itself is the cost and a pre-aggregated
daily table is the fix. Staging is fast simply because it is small; the planner's choice flips with data
volume, which is why plans must be checked against production-sized data.

## Interview questions
### Why can't you use a SELECT alias in WHERE, but you can in ORDER BY?
- **Logical order:** `FROM`/`JOIN`, `WHERE`, `GROUP BY`, `HAVING`, `SELECT`, `DISTINCT`, `ORDER BY`, `LIMIT`.
- **So `WHERE` runs before the alias exists,** while `ORDER BY` runs after `SELECT` has computed it. The
  same order explains why aggregates need `HAVING` rather than `WHERE`.

### A query with NOT IN suddenly returns zero rows after a data import. What happened?
- **The subquery now yields a NULL.** `a NOT IN (b, NULL)` becomes `a <> b AND a <> NULL`, which is never
  true under three-valued logic, so every row is dropped.
- **Fix:** rewrite with `NOT EXISTS`, which compares row by row and ignores the NULL, or filter NULLs out of
  the subquery explicitly.

### When would you split one big join into two queries?
- **When the join multiplies rows.** One conversation × many messages × many tool calls can produce a huge
  intermediate result shipped over the wire.
- **Two targeted queries** (conversations with messages, then tool calls for those message ids) move less
  data. Keep the count of queries constant, not proportional to rows, so it does not become N+1.

### Users report that page 200 of a list takes seconds while page 1 is instant. Diagnose and fix.
- **Cause:** `LIMIT/OFFSET` reads and discards every earlier row, so cost grows linearly with depth.
- **Fix:** keyset pagination on `(created_at, id)` with a matching index; every page is one index seek.
- **Trade-off:** no jumping to an arbitrary page number, so confirm users scroll rather than jump.

### How would you find the queries worth optimizing in a production PostgreSQL?
- **Use `pg_stat_statements` sorted by total time,** because a fast query called millions of times often
  costs more than a slow rare one.
- **Then read the plan** with `EXPLAIN (ANALYZE, BUFFERS)` on production-sized data, checking scan types and
  estimated versus actual rows; a gap of 10× or more points to stale statistics or a predicate the
  planner cannot estimate.

## Quiz
### Q: Why can WHERE date(created_at) = '2026-10-20' not use a B-tree index on created_at?
- [ ] Date comparisons are never indexable in PostgreSQL
- [x] The index stores created_at values, not the result of date(), so the planner cannot seek on the function's output
- [ ] The literal must be cast to timestamptz before the index can be used
- [ ] B-tree indexes only support equality on integer columns
> The index is ordered by the raw column. Applying a function hides the column from the planner; rewrite
> as a range (`created_at >= '2026-10-20' AND created_at < '2026-10-21'`) or create an index on an
> immutable expression such as `((created_at AT TIME ZONE 'UTC')::date)`.

### Q: An endpoint loads 500 conversations and then each conversation's last message via an ORM relationship. Which fix addresses the root cause?
- [ ] Add an index on messages.content to speed up each lookup
- [ ] Increase the connection pool size so the 500 queries run in parallel
- [x] Replace the per-row lookups with one query that joins or uses a lateral subquery to fetch the latest message per conversation
- [ ] Cache the conversation list in Redis so the loop runs less often
> The cost is 501 round trips, not slow individual queries. A single joined or lateral query lets the
> database do the work in one pass; more connections or a cache only hides the pattern.

### Q: What is the main advantage of keyset pagination over LIMIT/OFFSET?
- [ ] It returns rows in a stable order even without an ORDER BY clause
- [ ] It allows jumping directly to any page number
- [x] It seeks to the next page through the index, so deep pages cost the same as the first page
- [ ] It avoids the need for an index on the sort column
> OFFSET reads and discards every preceding row, so cost grows with page depth. A keyset cursor uses the
> index to start exactly where the previous page ended; the trade is losing random page jumps.
