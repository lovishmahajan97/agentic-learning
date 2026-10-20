---
id: nosql
module: backend-fundamentals
name: NoSQL
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - backend-fundamentals/database-design
  - backend-fundamentals/sql
  - backend-fundamentals/redis
  - system-design/data-consistency
  - system-design/distributed-systems
  - agent-reliability/state-and-checkpoint-stores
  - rag-retrieval/vector-databases
sources:
  - DeCandia et al., 2007 — Dynamo: Amazon's Highly Available Key-value Store
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Official docs: Amazon DynamoDB
  - Official docs: MongoDB
---

## TL;DR
NoSQL stores give up general joins and cross-record transactions so that data can be partitioned across
many machines by a key and served with predictable latency at any size. The price is that you must design
the data around the exact queries you will run, and accept weaker consistency on some reads. Choose one
for a known, narrow access pattern at scale, not because "schemaless" sounds easier.

## Why it exists
A relational database on one machine answers any question you can write in SQL, but it has a ceiling:
one machine's disk, memory and write throughput. Past that you must partition, and the moment rows live
on different machines, a join between them becomes a network operation and a transaction across them
becomes a distributed commit. Both are possible, but they are slow and fragile, and they are exactly the
operations the relational model makes casual. Early web-scale systems hit this wall and responded by
restricting the data model: if every operation touches one partition, identified by a key, then each
machine can serve its partition independently, nodes can be added without coordination, and a machine
failure affects only its share of keys.

The naive alternative, sharding a relational database by hand, pushes the join and transaction problem
into application code where every engineer must know the sharding scheme, and it keeps the schema rigid
while the data is spread across nodes that cannot be migrated atomically. NoSQL systems make the
constraint explicit instead: you declare a partition key, the store guarantees cheap operations within a
partition, and anything across partitions is either unsupported or clearly expensive. A second
motivation is heterogeneous records: a document store lets items of one collection carry different
fields, which suits event payloads and tool outputs whose shape varies by type.

The cost is real. Every query you did not design a key or index for is a full scan or impossible, and
"schemaless" simply means the schema lives in your code, unenforced.

## How it works
### The families
- **Key-value** (Redis, DynamoDB used simply, Riak): get and put a value by key. The simplest and the
  fastest; no queries other than by key.
- **Document** (MongoDB, Couchbase, DynamoDB with nested attributes): the value is a JSON-like document
  with nested fields; you can index and query on fields inside it, within a collection.
- **Wide-column** (Cassandra, ScyllaDB, Bigtable): rows grouped by a partition key and ordered within the
  partition by clustering columns; built for very high write rates and time-ordered reads within a key.
- **Graph** (Neo4j): nodes and edges, for queries that are mostly traversals ("friends of friends").
Search engines and vector databases are specialised stores that behave like document stores with a
different index at the core; they are covered in their own lessons.

### Partitioning by key
The store hashes the partition key to pick a node (or a range of keys maps to a node). Every operation
that carries the key is routed to one node and costs one lookup regardless of total data size, which is
the whole point. Operations without the key must fan out to every node, so a design question is: "which
key will every hot query have in hand?" In DynamoDB this is the partition key plus an optional sort key;
in Cassandra the partition key plus clustering columns; in MongoDB the shard key.

### Replication and consistency
Each partition is replicated to N nodes for durability. Writes are acknowledged after W replicas accept
them and reads consult R replicas; when W + R > N the read set must overlap the write set, so a read
reaches at least one replica holding the latest acknowledged write (a strict quorum). Edge cases weaken
this in practice: sloppy quorums that accept writes on stand-in nodes, concurrent writes resolved by
last-write-wins, and writes that failed on some replicas but were not rolled back.
Choosing W = 1 gives the fastest writes and the possibility that a read a moment later sees the old value:
**eventual consistency**. Most stores let you choose per operation (Cassandra consistency levels,
DynamoDB's `ConsistentRead`), and strongly consistent reads cost more and may fail when a replica is
down. This is the practical face of the CAP trade-off: during a partition, a store either refuses some
requests or serves possibly-stale data.

### Modelling: access patterns first
Relational design starts from entities and normalizes; NoSQL design starts from the list of queries and
shapes the data so each one is a single-partition read. Two decisions dominate:
1. **Embed or reference.** Embed child data inside the parent document when it is read together and
   bounded in size (a message with its tool calls). Reference by id when the child set is unbounded or
   shared (all messages of a conversation that may run to thousands). Documents have hard size limits
   (16 MB in MongoDB, 400 KB per item in DynamoDB), and an unbounded embedded array will hit them.
2. **Denormalize and duplicate.** Because there are no joins, data needed by two queries is often stored
   twice, in two key layouts, and the application updates both. DynamoDB's single-table design takes this
   to its conclusion: every entity type in one table with composite keys like `TENANT#t1#USER#u9` /
   `SESSION#2026-10-20T10:00`, and global secondary indexes (GSIs) that re-key the same items for a second
   access pattern. A GSI is maintained asynchronously, so reads through it are eventually consistent.

### Hot partitions
Capacity is configured per table, but each physical partition has its own throughput ceiling (in
DynamoDB, documented as roughly 3,000 read units and 1,000 write units per second per partition). If one
tenant produces 40% of all writes and tenant id is the partition key, that partition throttles while the
rest of the cluster idles. DynamoDB's adaptive capacity and split-for-heat soften this for some
workloads, but they are not something to design around. Fixes are
to add a high-cardinality component to the key (tenant + conversation, or tenant + a shard suffix) so a
busy tenant spreads over several partitions, at the cost of fan-out reads for "everything for this tenant".

### Transactions and indexes
Modern document stores support multi-document transactions, and DynamoDB offers transactional reads and
writes across a bounded set of items (up to 100 per transaction at the time of writing), but they cost
more capacity, are limited in scope and are not the default idiom. Secondary indexes exist in all of them,
and each one is a copy of the data maintained on every write; in DynamoDB every GSI also consumes its own
write capacity and storage, so "index everything" shows up directly on the bill.

## Example
Agent sessions and their messages in DynamoDB, designed from two access patterns: "list a user's recent
sessions" and "load the last 20 messages of a session". Each is a single-partition `Query`. Messages are
referenced, not embedded, because a session can grow without bound. The sort key carries a zero-padded
sequence so lexical order is chronological.

```python
import boto3, time
from boto3.dynamodb.conditions import Key

table = boto3.resource("dynamodb").Table("agent-app")
Duplicate = table.meta.client.exceptions.ConditionalCheckFailedException

def create_session(tenant, user, session_id, title):
    table.put_item(Item={
        "pk": f"TENANT#{tenant}#USER#{user}",
        "sk": f"SESSION#{int(time.time()*1000):013d}#{session_id}",
        "title": title, "session_id": session_id,
    })

def append_message(session_id, seq, role, content):
    try:
        table.put_item(
            Item={"pk": f"SESSION#{session_id}", "sk": f"MSG#{seq:08d}",
                  "role": role, "content": content},
            ConditionExpression="attribute_not_exists(sk)")   # never overwrite a message
    except Duplicate:
        pass                                  # a retry of a write that already landed

def recent_sessions(tenant, user, limit=10):
    r = table.query(
        KeyConditionExpression=Key("pk").eq(f"TENANT#{tenant}#USER#{user}")
                               & Key("sk").begins_with("SESSION#"),
        ScanIndexForward=False, Limit=limit)              # newest first
    return r["Items"]

def last_messages(session_id, limit=20):
    r = table.query(
        KeyConditionExpression=Key("pk").eq(f"SESSION#{session_id}")
                               & Key("sk").begins_with("MSG#"),
        ScanIndexForward=False, Limit=limit,
        ConsistentRead=True)                              # read-your-own-write after append
    return list(reversed(r["Items"]))
```

A query the design does not support, "all messages mentioning refunds across all tenants", would be a
full table scan; if it matters, the data also goes to a search index.

## Trade-offs
- Single-partition operations scale horizontally with no ceiling in practice, but any query that lacks
  the partition key becomes a scatter-gather or a scan; the trade flips when ad-hoc questions dominate.
- Flexible schemas let different record shapes coexist and evolve without migrations, but nothing
  enforces them, so validation moves into code and old shapes linger in the data forever.
- Eventual consistency gives low latency and availability during failures, but a client that writes then
  reads may see stale data; pay for consistent reads on the paths where users would notice.
- Denormalizing for read patterns makes each read one call, but every update must touch every copy, and a
  missed copy is a silent inconsistency with no foreign key to catch it.
- Managed stores like DynamoDB remove operations work and give predictable per-request cost, but a new
  access pattern can mean a new index or a data migration rather than a new query.
- A document store avoids the object-relational mismatch for nested, read-together data, but it loses the
  database's ability to join, constrain and aggregate across entities.

## Pitfalls
- Choosing NoSQL "for scale" at tens of gigabytes; the symptom is a team rebuilding joins, aggregates and
  constraints in application code for data that a single PostgreSQL instance would handle easily.
- Partitioning by tenant alone; the symptom is throttling errors on one partition while the cluster's
  overall utilisation is low.
- Embedding an unbounded array (all messages inside the session document); the symptom is writes that get
  slower as the conversation grows, then a hard document-size error.
- Expecting read-your-own-write on default eventually consistent reads; the symptom is a user who posts a
  message and does not see it on the next refresh, intermittently.
- Issuing scans or non-key queries from request paths; the symptom is latency that rises with table size
  and a bill dominated by read capacity.
- Letting document shapes drift (some records with `tokens`, some with `token_count`); the symptom is
  code full of defensive `get` calls and analytics that undercount.
- Relying on secondary indexes as if they were free; the symptom is write cost multiplying with each
  index and GSI reads that lag the base table.

## When to use
Reach for a NoSQL store when you can write down the handful of access patterns up front, when each one
can be keyed to a single partition, and when the write rate, data volume or availability requirement
exceeds what one relational node provides comfortably. Typical fits in AI systems: per-session agent
state and message logs keyed by session, high-volume event and trace ingestion, feature or cache lookups
by id. Stay relational while the data fits one machine and the questions are still changing; add a
specialised store (search, vector, key-value cache) beside PostgreSQL rather than replacing it. Measure
partition heat and consistent-read usage once live. In interviews, expect to design keys for a given set
of queries and to explain what happens to the query you forgot.

## Role lens
- **Engineer:** Designs partition and sort keys from the query list, writes conditional puts for
  idempotency, and chooses consistent reads only on the paths where staleness would be visible.
- **Architect:** Decides which data leaves the relational system of record, how copies stay in sync,
  and what consistency each read path promises to users and downstream services.
- **FDE:** Reads the customer's existing DynamoDB or Mongo model, spots the hot partition or the unbounded
  document, and plans how conversation data will be exported for evaluation and compliance.

## Check yourself
**Q:** You are storing agent runs in DynamoDB with `tenant_id` as the partition key and `run_id` as the
sort key. One enterprise tenant generates half of all traffic. What goes wrong and how do you fix it?
**A:** All of that tenant's reads and writes land on one partition, which has a fixed throughput ceiling,
so that tenant sees throttling while the table's total capacity is mostly unused. Raising provisioned
capacity does not help because the limit is per partition. The fix is to spread the tenant across
partitions by adding high-cardinality data to the key, for example `tenant_id#user_id` or
`tenant_id#shard` where shard is derived from the run id. The cost is that "all runs for the tenant" now
requires querying each shard and merging, so you keep a GSI or a separate summary item for tenant-wide
views, accepting that it is eventually consistent. The general lesson is that the key must distribute
load, not just identify ownership.

## Quiz
### Q: Why does a document store make joins across collections expensive or unsupported?
- [ ] JSON documents cannot contain foreign keys
- [x] Data is partitioned by key across nodes, so a join would require moving matching records between machines
- [ ] Document stores do not support indexes on nested fields
- [ ] Joins require a fixed schema that document stores lack
> Horizontal scaling relies on each operation touching one partition. A general join must combine records
> that live on different nodes, which is the network-heavy operation partitioning was designed to avoid.

### Q: A session document embeds every message and writes slow down as conversations grow. What is the correct restructuring?
- [ ] Compress the message array before storing it
- [ ] Switch the session document to a strongly consistent write
- [x] Store messages as separate items keyed by session id with an ordered sort key, and reference them from the session
- [ ] Add a secondary index on the embedded message array
> Embedding suits bounded, read-together data. An unbounded array makes every append rewrite the whole
> document and eventually hits the size limit; separate items keyed by session keep appends constant-cost.

### Q: With N = 3 replicas, which setting guarantees a read sees the latest acknowledged write?
- [ ] W = 1, R = 1, because the write is replicated eventually
- [ ] W = 1, R = 2, because two readers will outvote the stale replica
- [x] W = 2, R = 2, because the write and read replica sets must overlap
- [ ] Any setting, because replication is synchronous by default
> Overlap requires W + R > N. With W = 2 and R = 2 (a strict quorum), at least one replica consulted by the
> read holds the write. W = 1 with R = 2 can read the two replicas that both missed it.
