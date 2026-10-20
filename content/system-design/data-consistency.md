---
id: data-consistency
module: system-design
name: Data consistency
kind: concept
difficulty: core
est_minutes: 17
status: reviewed
related:
  - system-design/distributed-systems
  - system-design/saga-pattern-and-distributed-transactions
  - system-design/outbox-inbox-patterns
  - system-design/caching-strategy-and-cache-invalidation
  - backend-fundamentals/postgresql-transactions-and-isolation-levels
  - rag-retrieval/freshness-document-deletion-index-consistency
  - reliability/multi-region-architecture
  - data-architecture/data-contracts
sources:
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Herlihy & Wing, 1990 — Linearizability: A Correctness Condition for Concurrent Objects
  - Bailis et al., 2014 — Highly Available Transactions: Virtues and Limitations
  - Vogels, 2009 — Eventually Consistent
---

## TL;DR
"Consistency" names two things: invariants inside one database (the C in ACID, enforced by constraints
and isolation levels) and the guarantee about *which* value a read may return when copies exist
(linearizable down to eventual). Strong guarantees cost coordination and latency, so good designs make a
few invariants strong (permissions, quotas, idempotency keys) and let everything else lag, visibly. In AI
systems the usual victims are the search index, the cache and the agent's memory, all copies of a source
of truth.

## Why it exists
A single-node database with serializable transactions lets you forget this topic: every read sees the
result of every committed write, and constraints keep invariants true. The moment you add a second copy
of any data, you have to say what a reader may see, because the copies cannot be updated at the same
instant. Copies are everywhere in an AI product: a read replica behind the API, a Redis cache in front of
the database, a vector index built from documents, an agent memory store summarised from conversations,
a permissions service mirrored into index metadata, a data warehouse fed by nightly jobs.

The naive position is "it will catch up in a second, nobody will notice". Then a user uploads a contract,
immediately asks a question about it, and the assistant answers "I don't have that document" because the
index refresh had not run; or an admin revokes a contractor's access to a folder and the contractor's
next query still retrieves chunks from it for thirty seconds; or an agent reads its own memory on a
replica, misses the note it wrote a moment ago, and repeats a tool call. Each is a consistency anomaly
with a precise name, and each has a precise fix, but only if you know which guarantee you need.

The opposite naive position, "make everything strongly consistent", fails on physics and economics.
Strong consistency across copies requires coordination: a write must be acknowledged by a quorum, or
reads must go to the leader, before anyone can proceed. Round trips within a region cost roughly a
millisecond; across continents tens to over a hundred milliseconds; and under a network partition the
system must refuse requests to stay consistent. Paying that on every read of a product catalogue or a
chunk embedding is waste. The skill is choosing per invariant, which requires knowing the ladder of
guarantees and what each one rules out.

## How it works
### Consistency inside one database: isolation
Within a single database, concurrent transactions are isolated to a configurable degree. **Read
committed** prevents dirty reads (seeing uncommitted data) but allows a row to change between two reads
in the same transaction. **Snapshot isolation** (Postgres's `REPEATABLE READ`) gives each transaction a
frozen view of the database as of its start, which rules out non-repeatable reads and phantoms but
permits **write skew**: two transactions each read a condition, both find it true, and both write, leaving
the invariant violated (two on-call doctors each see "at least one other doctor on call" and both go
off duty). **Serializable** guarantees the outcome equals some serial order, at the cost of aborts under
contention. The decisive question is whether the invariant spans rows that one transaction reads and
another writes; if it does, you need serializable isolation or an explicit lock. This is covered in depth
in the backend-fundamentals module.

### Consistency across copies: the ladder
Ordered from strongest to weakest, each level is defined by the anomaly it forbids.

- **Linearizable.** The system behaves as if there is one copy and every operation takes effect at a
  single instant between its start and end. Once any client sees a write, every later read sees it.
  Needed for locks, leader election, unique-name registration, quota debits. Costs a quorum or leader
  round trip per operation and is unavailable to the minority side of a partition.
- **Sequential, then causal.** Sequential consistency: every client sees the same total order of writes,
  consistent with each client's program order, but not necessarily aligned with real time. Causal
  consistency relaxes that further: only causally related writes must be seen in order (a reply never
  appears before the message it answers); concurrent writes may be seen in different orders by
  different clients. Cheaper than linearizable, and causal is enough for most collaborative and
  conversational data.
- **Session guarantees.** Promises scoped to one client's session: **read-your-writes** (you see your own
  updates), **monotonic reads** (you never see time go backwards), **monotonic writes** (your writes apply
  in order), **writes-follow-reads**. These are what users actually notice, and they are cheap to provide
  by routing a session's reads to the leader for a short period after a write, or by carrying a version
  token the replica must have caught up to before serving.
- **Eventual.** If writes stop, all copies converge. No bound on *when*, and no promise about what a read
  returns in the meantime. Right for analytics, recommendations, derived indexes, caches with TTLs.

### Converging needs a merge rule
"All copies converge" hides a decision: when two copies accepted different writes to the same item
(multi-leader replication, offline clients, two regions), something must decide the merged value.
**Last-writer-wins** keeps the write with the highest timestamp; it always converges, *because* every
replica applies the same rule, but it silently discards the other write and, with wall-clock
timestamps, can discard the genuinely later one. **Version vectors** detect that two writes were
concurrent rather than ordered and keep both as siblings for the application to merge (two edits to a
shared prompt template surface as a conflict to resolve). **CRDTs** are data types whose merge is
defined so that any order of applying updates yields the same result: a grow-only counter sums per-node
counts, an observed-remove set keeps an add unless the remove saw it, and collaborative text editors
use sequence CRDTs. They converge without coordination, but only for operations the type defines; an
invariant such as "balance never below zero" cannot be kept by merging after the fact and needs
coordination before the write.

### Where lag comes from and how it is bounded
Replication lag is the time between a commit on the leader and its visibility on a copy. Asynchronous
database replicas lag milliseconds to seconds, more under load. Search and vector indexes commonly make
new writes visible only after a periodic refresh (Elasticsearch's default refresh interval is one
second). Caches lag until invalidation or TTL. Event-driven projections lag by queue depth. Bounding lag
means measuring it (leader LSN minus replica LSN, event timestamp minus processing time) and routing
sensitive reads around it.

### Derived data and the permission problem
A vector index is a *derived* copy of documents plus access metadata. Two consistency questions follow.
Freshness: how long after a document changes or is deleted do stale chunks remain retrievable? Deletion
must propagate as an explicit tombstone event, and the retention of embeddings for deleted documents is a
compliance issue, not only a quality issue. Authorisation: if access control lists are copied into the
index as filter metadata, a revocation is eventually consistent, and the window is a leak. The robust
design checks permissions at query time against the authoritative store (a strongly consistent read of a
small set), or filters by document id against a fresh ACL lookup after retrieval, and uses index metadata
only as a pre-filter for efficiency.

### Choosing per invariant
List each piece of state, name the invariant, and ask what a stale read costs. Money, permissions,
uniqueness, quotas and idempotency records: strong, in one place, read from the leader. User-visible
"did my action take" state: session guarantees. Everything derived: eventual, with a measured and
displayed lag ("index updated 4 s ago"). Aggregates in domain-driven design express the same rule: strong
consistency inside an aggregate, eventual between aggregates.

## Example
Read-your-writes across a Postgres primary and an asynchronous replica. After a write commits, the API
returns a version token (the primary's write-ahead-log position at or after the commit record); later
reads carry it, and the replica is used
only if it has replayed past that position, otherwise the read is routed to the primary.

```python
import psycopg

def save_memory(primary, run_id, note):
    with primary.transaction():
        primary.execute("INSERT INTO agent_memory (run_id, note) VALUES (%s, %s)", (run_id, note))
    # read the LSN AFTER commit: the commit record is what makes the row visible on the replica,
    # and it is written after any LSN observed inside the transaction
    (token,) = primary.execute("SELECT pg_current_wal_lsn()::text").fetchone()
    return token                       # hand this back to the client as a 'consistency token'

def replica_is_fresh(replica, token):
    (ok,) = replica.execute(
        "SELECT pg_last_wal_replay_lsn() >= %s::pg_lsn", (token,)).fetchone()
    return bool(ok)

def read_memory(primary, replica, run_id, token=None):
    conn = replica if (token is None or replica_is_fresh(replica, token)) else primary
    return conn.execute("SELECT note FROM agent_memory WHERE run_id = %s ORDER BY id",
                        (run_id,)).fetchall()

# Usage inside an agent step:
#   token = save_memory(primary, run_id, "user prefers formal tone")
#   notes = read_memory(primary, replica, run_id, token)   # never misses its own write
# Reads with no token (another user's dashboard) go to the replica and accept lag.
```

The cost is one extra round trip to the replica when the token is present, and a primary read only when
the replica is behind, which is exactly the small fraction of reads that would otherwise have been
wrong. A cheaper variant with the same guarantee is to route all reads of a session to the primary for a
fixed window (a few seconds) after any write, at the price of some unnecessary primary load, and a
guarantee that breaks if replica lag ever exceeds the window. The same token also buys **monotonic
reads** when there are several replicas: if the client keeps the highest LSN it has *read* as well as
written, and sends that, a request load-balanced to a more-lagged replica is redirected instead of
showing older data than the previous page did.

## Trade-offs
- Linearizable reads make correctness trivial to reason about, but each costs a leader or quorum round
  trip and disappears on the minority side of a partition; once an operation's error cost is lower than
  its latency cost, drop to a weaker level.
- Session guarantees give users almost everything they notice at a fraction of the cost of global strong
  consistency, yet they require sticky routing or version tokens, which complicates stateless load
  balancing.
- Copying permissions into index metadata makes retrieval filters fast and self-contained, while turning
  every revocation into an eventually consistent leak window; query-time checks against the source are
  slower per request but close the window entirely.
- Serializable isolation catches write skew automatically, but aborts rise with contention; for a hot
  counter or quota, an explicit row lock or an atomic single-statement update is both faster and
  simpler.
- Displaying lag ("updated 4 s ago", "indexing…") costs UI work and admits imperfection, but it converts
  a bug report into an understood state and lets you keep the cheap eventual path.

## Pitfalls
- Reading from a replica right after writing to the primary; users see their new document, setting or
  memory vanish and reappear, and the bug cannot be reproduced on a single-node dev setup.
- Treating snapshot isolation as serializable; two concurrent requests both pass a "seat is free" or
  "usage is under quota" check, each inserts its own row, both commit, and the invariant is found
  broken days later (a conflicting update to the *same* row would have been caught; disjoint writes
  are not).
- Enforcing authorisation only through copied index metadata; a revoked user still retrieves content
  for the lag window, which audit logs reveal as access after revocation.
- Deleting source documents without emitting a tombstone to derived stores; embeddings of deleted or
  redacted content keep surfacing in answers, sometimes months later.
- Assuming the cache is coherent with the database; after a write, one request path reads the fresh row
  and another the stale cache entry, producing responses that contradict each other within one page
  load.
- Using wall-clock last-writer-wins to merge concurrent updates across replicas; an edit silently
  disappears and no error is ever logged.

## When to use
You are always choosing a consistency level, whether you name it or not; the default of "whatever the
replica returns" is a choice. Make it explicit per invariant: write down which state is strong, which
has session guarantees, and which is eventual with what bound, and encode the routing (primary versus
replica, query-time ACL versus metadata filter, synchronous versus asynchronous indexing) accordingly.
Measure replication lag, index freshness lag and cache hit-on-stale rates, and alert when the lag exceeds
what the product promises. In day-to-day work this is the `read_from_primary=True` flag on a repository
call, the tombstone event on document deletion, and the ACL check after retrieval. In interviews, be able
to place linearizable, causal, read-your-writes and eventual on one ladder with the anomaly each forbids,
explain write skew, and apply the ladder to a RAG index that mirrors permissions.

## Role lens
- **Engineer:** Routes post-write reads to the primary or carries version tokens, emits tombstones for
  deletions, and chooses isolation levels or locks for invariants that span rows.
- **Architect:** Classifies every data store as source of truth or derived copy, assigns a consistency
  level and lag budget to each, and decides where permissions are enforced.
- **FDE:** Explains to a customer why a document is searchable seconds after upload rather than
  instantly, and verifies that their access revocations propagate to the index within the agreed window.

## Check yourself
**Q:** A customer's security team asks for a guarantee that when a user loses access to a SharePoint
folder, the assistant can never surface content from it. Your index stores ACLs as chunk metadata and
syncs them hourly. What do you tell them, and how do you redesign?
**A:** The current design is eventually consistent with an hour-long leak window, which cannot meet
"never". Redesign in layers. First, keep the metadata filter as a cheap pre-filter but add a query-time
authorisation check: after retrieval, look up the candidate documents' current permissions against the
authoritative source (SharePoint's API or a near-real-time mirror with a measured lag) and drop anything
the user cannot access now. That makes the guarantee as strong as the permission source, which is where
the customer already trusts it. Second, replace the hourly sync with change events or incremental
synchronisation so the pre-filter is seconds behind rather than an hour, and emit tombstones on document
deletion so chunks are removed, not merely hidden. Third, state the residual: the query-time check is
only as fresh as the permission source's own consistency, and a strongly consistent read there costs
latency per query, which you can bound by caching allow decisions for a few seconds at most. Measure
revocation-to-enforcement time and report it.

## Quiz
### Q: Which guarantee ensures a user always sees the memory note their agent wrote a moment ago, without requiring every read in the system to go to the leader?
- [ ] Linearizability for all reads
- [x] Read-your-writes, implemented by routing that session's reads to the primary or carrying a version token
- [ ] Eventual consistency with a short TTL
- [ ] Serializable isolation on the replica
> Read-your-writes is a session guarantee scoped to the writer, so only the writer's post-write reads pay
> for freshness. Global linearizability would solve it at far higher cost; eventual consistency gives no
> promise; isolation levels govern concurrent transactions, not replica staleness.

### Q: Two concurrent requests each sum a tenant's usage rows, find the total below the quota, and each insert a new usage row, under snapshot isolation. What anomaly can occur?
- [ ] Dirty read
- [ ] Non-repeatable read
- [x] Write skew: both see headroom in their snapshots and both insert, pushing the total over the quota
- [ ] Lost update, because the second insert overwrites the first
> Each transaction reads a consistent snapshot in which the quota has headroom and then writes a *different*
> row; snapshot isolation only detects write-write conflicts on the same row, so both commit. The fix is
> serializable isolation, locking the tenant row (`SELECT ... FOR UPDATE`) before the check, or keeping the
> usage as one counter updated by a single atomic conditional statement.

### Q: Why is a query-time permission check more robust than relying on ACL metadata copied into a vector index?
- [ ] Metadata filters are too slow for vector search
- [ ] Vector indexes cannot store string metadata
- [x] Copied ACLs are eventually consistent, so a revocation leaves a window in which content is still retrievable
- [ ] Query-time checks make the index linearizable
> The index is a derived copy, so its ACL metadata lags the source by the sync interval. Checking current
> permissions against the authoritative store at query time closes that window; the metadata filter stays
> useful as a pre-filter for efficiency, not as the enforcement point.
