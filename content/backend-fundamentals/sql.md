---
id: sql
module: backend-fundamentals
name: SQL
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
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
SQL is declarative: you state which rows and columns you want, and a cost-based planner chooses the join
order, scan type and algorithm. That separation is why one query can survive a 1000× growth in data if the
right index exists, and why the same query collapses when it does not. Thinking in sets rather than loops,
and reading the plan rather than guessing, is the whole skill.

## Why it exists
Before the relational model, databases were navigational: a program walked pointers from record to record,
so every program encoded the physical layout, and changing the layout meant changing every program. Codd's
insight was to describe data as relations (tables of tuples) and queries as algebra over them, so the
*what* could be stated independently of the *how*. SQL is the practical language that came out of that,
and the planner is the component that turns the what into a how at run time, using statistics about the
actual data.

The naive alternative in application code is the loop: fetch the conversations, then for each one fetch
its messages, then for each message count tokens. That is the N+1 pattern, and it fails for two reasons.
Each round trip to the database costs a network hop (hundreds of microseconds to milliseconds), so 1,000
conversations cost 1,001 queries and somewhere between half a second and several seconds of wall time. And the database never sees the whole
question, so it cannot pick a hash join or use an index to do the work in one pass. A single query with a
join and an aggregate lets the engine do it in one scan.

AI backends are heavy SQL users even when the model is the star: per-tenant token spend, latency
percentiles per model, the last N messages of a conversation, eval results joined to prompt versions, and
hybrid retrieval that mixes a vector similarity with a metadata filter are all one query each when
written well and a slow fan-out of calls when written badly.

## How it works
### Logical evaluation order
A `SELECT` statement is written in one order and evaluated in another. The logical order is:
`FROM` and `JOIN` (build the row set), `WHERE` (filter rows), `GROUP BY` (collapse into groups),
`HAVING` (filter groups), `SELECT` (compute output columns, including window functions), `DISTINCT`,
`ORDER BY`, then `LIMIT`/`OFFSET`. This explains the classic confusions: you cannot reference a `SELECT`
alias in `WHERE` because `WHERE` runs first; `HAVING` exists because aggregates do not exist yet when
`WHERE` runs; window functions see the filtered, grouped rows because they run during `SELECT`.

### Joins are set operations, not loops
An inner join keeps rows that match on both sides; a left join keeps every left row and fills the right
side with NULLs when nothing matches, which is how you find conversations with no messages
(`WHERE m.id IS NULL`). `EXISTS`/`NOT EXISTS` express "has at least one" without duplicating rows the way
a join would. The engine picks the physical algorithm: a **nested loop** when one side is tiny or has an
index on the join key, a **hash join** when both sides are large and unsorted, a **merge join** when both
sides are already sorted on the key. You do not pick these; you make them possible with indexes and
accurate statistics.

### Aggregates and window functions
`GROUP BY` collapses rows; a window function (`SUM(...) OVER (PARTITION BY tenant_id ORDER BY day)`)
computes an aggregate *per row* without collapsing, which is how you get running totals, rankings and
"latest row per group" in a single pass. PostgreSQL's `DISTINCT ON (conversation_id) ... ORDER BY
conversation_id, created_at DESC` is the idiomatic "newest message per conversation".

### How the planner decides
`EXPLAIN ANALYZE` shows the tree the planner chose and the real row counts and timings. The planner
estimates cost from table statistics (row counts, value distributions gathered by `ANALYZE`) and chooses
between a sequential scan (read everything; cheapest when most rows qualify), an index scan (walk a B-tree
to the matching rows; cheapest when few qualify) and a bitmap scan (collect matching pages from one or
more indexes, then read them in physical order). A predicate is **sargable** (can use an index) when the
indexed column appears bare on one side of a comparison: `created_at >= $1` can use an index;
`date(created_at) = $1` cannot, because the index stores `created_at`, not its date. `LIKE 'abc%'` can use
a B-tree, but in PostgreSQL only if the index uses the `C` collation or the `text_pattern_ops` operator
class (a default index under a locale such as `en_US.UTF-8` is not used for `LIKE`); `LIKE '%abc%'` cannot
use a B-tree at all and needs a trigram (`pg_trgm`) index. The planner is only as good as its statistics, so a table that grew 100×
since the last `ANALYZE` gets bad plans.

### Parameters, not string formatting
Queries take parameters (`$1`, `%s`, `?` depending on the driver) that are sent separately from the SQL
text. This is the only reliable defence against SQL injection *because* the database never parses user
input as SQL; it also lets the server reuse a prepared plan. String interpolation of user input into a
query is a vulnerability regardless of how well you escape.

### Pagination and the OFFSET trap
`LIMIT 20 OFFSET 100000` still reads and discards 100,000 rows; cost grows linearly with page depth.
Keyset pagination (`WHERE (created_at, id) < ($last_ts, $last_id) ORDER BY created_at DESC, id DESC
LIMIT 20`) seeks directly to the next page using the index and costs the same on page 1 and page 5,000.

### Where the ORM fits
ORMs generate SQL from objects, which is convenient for CRUD and dangerous for reporting: lazy-loaded
relationships are the usual source of N+1, and complex aggregations are often clearer as SQL. The
working rule is to let the ORM handle simple writes and reads by key, and to write (and `EXPLAIN`) the
hot or analytical queries by hand.

## Example
Two queries that an LLM backend runs constantly. The first is a per-tenant spend report with a running
total, done in one pass with a window function over the grouped rows. The second is "newest message per
conversation" for an inbox view using a `LATERAL` subquery (the `DISTINCT ON` form above is the
alternative), with a keyset cursor so page depth does not change the cost. Each page still evaluates the
lateral lookup for every conversation the user owns; when users have thousands of conversations, the
usual next step is a denormalized `conversations.last_message_at` with an index on `(tenant_id, user_id,
last_message_at, id)`, so the cursor seeks straight to the page.

```sql
-- 1. Daily token spend per tenant for the last 7 days, with a running total.
--    No tenant filter, so a (tenant_id, created_at) index gives no simple range scan;
--    an index on created_at (or a BRIN index on an append-only table) serves this.
--    Day boundaries follow the session TimeZone setting.
SELECT tenant_id,
       date_trunc('day', created_at) AS day,
       SUM(input_tokens + output_tokens)                                 AS tokens,
       SUM(SUM(input_tokens + output_tokens))
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
         ORDER  BY created_at DESC
         LIMIT  1                                     -- hits (conversation_id, created_at)
       ) m ON true
WHERE  c.tenant_id = $1 AND c.user_id = $2 AND c.deleted_at IS NULL
  AND  (m.created_at, m.id) < ($3, $4)                -- previous page's last row; first page: ('infinity', 0)
ORDER  BY m.created_at DESC, m.id DESC
LIMIT  20;
```

Run each with `EXPLAIN (ANALYZE, BUFFERS)` and check two things: that the scan on `messages` is an index
scan rather than a sequential scan, and that the estimated row count is within an order of magnitude of
the actual one. A large mismatch means stale statistics or a predicate the planner cannot estimate.

## Trade-offs
- Declarative queries let the planner adapt to data growth, but you give up control of the algorithm; when
  the planner guesses wrong you fix statistics or indexes, not the query's "steps".
- One big joined query minimizes round trips but can produce a huge intermediate result; when a join
  multiplies rows (one conversation × many messages × many tool calls), two targeted queries are cheaper.
- Indexes make predicates cheap but each one slows writes and consumes memory; a reporting query that
  runs hourly rarely justifies an index that every insert pays for.
- ORMs speed up CRUD and keep types in sync, but they hide the SQL; the moment a query touches more than
  two tables or an aggregate, hand-written SQL is usually both faster and clearer.
- Keyset pagination is constant-cost but cannot jump to "page 37"; OFFSET can, at linear cost, so pick
  by whether users actually jump or just scroll.
- Common table expressions make queries readable; since PostgreSQL 12 the planner can inline a CTE that
  is referenced once, so readability no longer forces a materialization cost, but `MATERIALIZED` is there
  when you need the optimisation fence.

## Pitfalls
- The N+1 loop hidden behind an ORM relationship; the symptom is a request that issues hundreds of
  identical small queries in the log, each fast, together slow.
- Wrapping an indexed column in a function (`lower(email) = $1`, `date(created_at) = $1`); the symptom is
  a sequential scan in `EXPLAIN` despite an index existing. Rewrite as a range or add an expression index;
  the expression must be immutable, so on a `timestamptz` column index `((created_at AT TIME ZONE 'UTC')::date)`,
  not `date(created_at)`, which depends on the session time zone.
- `NOT IN (subquery)` where the subquery can return NULL; the symptom is a query that silently returns
  zero rows. Use `NOT EXISTS`.
- `SELECT *` in application code; the symptom is a column added for a migration that doubles network
  payload and breaks positional unpacking in a client.
- Deep `OFFSET` pagination; the symptom is page one in milliseconds and page two hundred in seconds, with
  the database CPU climbing as users scroll.
- Building SQL with f-strings or template literals; the symptom is a security finding, or a query that
  breaks on the first title containing an apostrophe.
- Trusting plans from a small dev database; the symptom is a query that was instant in staging and does
  a sequential scan over 50 million rows in production because the planner's choice flips with size.

## When to use
SQL is the right tool whenever the question is relational: filtering, joining, aggregating or ranking
rows across entities. Write it by hand for anything that joins more than two tables, aggregates, or runs
on a hot path, and let the ORM handle simple key lookups and inserts. Before shipping a new query, run
`EXPLAIN ANALYZE` against production-sized data and confirm the scan types and row estimates; after
shipping, watch `pg_stat_statements` for the queries with the highest total time, not the highest mean.
In interviews, expect to write a query with a join, a `GROUP BY` and a window function, explain why a
given predicate cannot use an index, and diagnose an N+1 from a description of symptoms.

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
