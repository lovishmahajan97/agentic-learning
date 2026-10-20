---
id: distributed-systems
module: system-design
name: Distributed systems
kind: concept
difficulty: core
est_minutes: 17
related:
  - system-design/data-consistency
  - system-design/exactly-once-processing-limitations
  - system-design/event-driven-architecture
  - reliability/fault-tolerance
  - reliability/high-availability
  - reliability/multi-region-architecture
  - reliability/circuit-breakers
  - observability/distributed-tracing-across-services
  - serving-inference/retry-strategies
sources:
  - Kleppmann, 2017 — Designing Data-Intensive Applications
  - Lamport, 1978 — Time, Clocks, and the Ordering of Events in a Distributed System
  - Fischer, Lynch & Paterson, 1985 — Impossibility of Distributed Consensus with One Faulty Process
  - Ongaro & Ousterhout, 2014 — In Search of an Understandable Consensus Algorithm
---

## TL;DR
A distributed system is several machines cooperating over a network, and its defining property is
**partial failure**: any node or link can fail or stall while the rest continue, and no observer can tell
"slow" from "dead" except by waiting. Every AI product is one, because model API, vector store, queue,
agent workers and customer tools are separate machines. The discipline is a short list: timeouts with
budgets, idempotent retries, quorums, leases with fencing, consensus where agreement is required, and
tracing.

## Why it exists
One machine is predictable. A function call either returns or the process is dead; a write either
happens or the program crashes, and the operating system gives you a consistent clock and shared memory.
Systems leave that world for three reasons: capacity (one box cannot hold the model, the index and the
traffic), availability (one box is one failure domain), and geography (users and data live in many
places, and the model provider is on someone else's network anyway).

The price is that the comforting binary "it worked or it crashed" disappears. Send a request to a model
endpoint and get no reply within 30 seconds. Possibilities: the request was lost; it arrived and is
queued; it was processed and the response was lost; the server crashed mid-way; your own process paused
for garbage collection and the response is sitting in a buffer. Each possibility demands a different
reaction, and the only evidence is the absence of a reply. Peter Deutsch's "fallacies of distributed
computing" list the assumptions engineers bring from the single machine: the network is reliable,
latency is zero, bandwidth is infinite, topology does not change, there is one administrator. Each
fallacy becomes an outage.

The naive strategy, "add retries and bigger timeouts", converts invisible failures into duplicate
effects and cascading overload: ten services each retrying three times turn one slow dependency into
tens of thousands of extra requests. The alternative, "make everything strongly consistent and
coordinated", runs into mathematics: coordination costs round trips, and in an asynchronous network with
even one crashed process, no deterministic protocol can guarantee that agreement terminates (the FLP
result). Working systems live between those poles, and knowing where is the skill this lesson orients.

## How it works
### Failure detection is a timeout
Machines do not announce their deaths. A heartbeat or request that gets no answer within *T* is the
only signal, and it conflates a dead node, a partitioned network, an overloaded node and a paused
process. Choose *T* from measured latency distributions (p99 plus margin), propagate a **deadline** down
the call chain so a request that has 2 seconds left does not launch a 10-second sub-call, and treat a
timeout as "outcome unknown", never as "did not happen". Retries therefore need idempotency keys, jittered
backoff and a budget, and callers need circuit breakers so a dead dependency fails fast instead of tying
up every thread.

### Clocks lie
Wall clocks drift and NTP corrects them with jumps, so timestamps from two nodes cannot order events
reliably at millisecond granularity. Use monotonic clocks for durations, and for ordering use causality:
Lamport's logical clocks (and vector clocks, version numbers, log offsets) order events by what they could
have influenced rather than by when a clock said they happened. Last-writer-wins by wall clock silently
discards writes when the "earlier" writer had the faster clock.

### Replication
Copying data to several nodes buys availability and read throughput. **Leader-based** replication sends
writes to one node that ships them to followers; synchronous followers make writes slower but durable
across nodes, asynchronous ones create **replication lag**, the window in which a read from a follower
returns stale data. **Multi-leader** and **leaderless** designs accept writes in many places and must
resolve conflicts. Leaderless (Dynamo-style) systems use quorums: with *N* replicas, writing to *W* and
reading from *R* such that *W + R > N* guarantees at least one replica in every read saw the latest write,
which is how availability is traded against the size of the quorum.

### Partitioning
When data outgrows one node it is split by key. The partition key decides which operations stay local
(cheap, atomic) and which span partitions (expensive, no atomicity without extra protocol). Hot keys (one
tenant, one viral document) overload a single partition no matter how many exist, which is why tenant
id is often a poor sole key for an AI platform.

### Consensus and leases
Some decisions need all nodes to agree: who is the leader, what is the next log entry, who holds a lock.
Raft solves this with terms, leader election and majority-replicated logs; a majority quorum tolerates
the failure of fewer than half the nodes. Systems such as etcd, ZooKeeper and Consul package it so
applications store small, critical state (configuration, leadership, locks) there and keep bulk data
elsewhere, *because* each consensus write costs a majority round trip. Locks and leadership are granted
as **leases** with an expiry, since a holder that dies must not hold the lock forever. The subtle part is
that a holder may *believe* it still has the lease after it expired (it paused, or its clock lagged). A
**fencing token**, a number that increases with each grant and that the protected resource checks,
rejects writes from the stale holder.

### The CAP and PACELC framing
During a network partition a replicated system must choose: refuse some requests to stay consistent, or
serve them and diverge. Outside partitions the same system trades latency against consistency (PACELC):
every coordinated write costs a round trip to a quorum. The practical reading is that strong consistency
is bought per operation, so systems make a few operations strongly consistent (allocating an id, taking a
lock, debiting a quota) and the rest eventually consistent.

### Seeing it
With no single stack trace, correlation ids and distributed tracing are not optional; they are how a
"request" is reconstituted from the spans that ran on different machines.

## Example
A lease-based lock with fencing tokens, the canonical illustration of why "the lock holder" is not a
reliable concept. A worker takes the lease to re-index a document, pauses longer than the lease, and a
second worker takes over; the storage layer rejects the first worker's late write because its token is
stale.

```python
import time, threading

class LeaseLock:                       # stand-in for etcd/ZooKeeper/Redis-with-care
    def __init__(self, ttl): self.ttl, self.token, self.holder, self.expires = ttl, 0, None, 0.0
    def acquire(self, who):
        if self.holder is None or time.monotonic() > self.expires:
            self.token += 1; self.holder, self.expires = who, time.monotonic() + self.ttl
            return self.token         # strictly increasing fencing token
        return None

class IndexStore:                      # the protected resource checks the token
    def __init__(self): self.max_token, self.data = 0, {}
    def write(self, token, doc_id, chunks):
        if token < self.max_token:
            raise PermissionError(f"stale token {token} < {self.max_token}")
        self.max_token = token; self.data[doc_id] = chunks

lock, store = LeaseLock(ttl=0.5), IndexStore()

def worker(name, pause):
    tok = lock.acquire(name)
    if tok is None: return
    time.sleep(pause)                  # simulates a GC pause or a slow embedding call
    try:
        store.write(tok, "doc-42", f"chunks by {name} (token {tok})")
        print(f"{name}: wrote with token {tok}")
    except PermissionError as e:
        print(f"{name}: rejected ({e})")

a = threading.Thread(target=worker, args=("A", 1.0)); a.start()   # holds lease, then stalls
time.sleep(0.7)                                                   # lease has expired by now
b = threading.Thread(target=worker, args=("B", 0.0)); b.start()   # takes token 2, writes
a.join(); b.join()
print(store.data["doc-42"])            # B's chunks survive; A's late write was fenced off
```

Without the token check, worker A's write would land after B's and silently overwrite a newer index
with older chunks, and nothing in the logs would show a lock violation, because A really did hold the
lock when it started.

## Trade-offs
- Replicating data raises availability and read capacity, but every extra replica either slows writes
  (synchronous) or widens the stale-read window (asynchronous); the choice flips toward synchronous
  when a stale read has a business cost, such as permissions.
- Consensus gives safe leadership and locks at the cost of a majority round trip per write and a
  minimum of three nodes; it is worth it for metadata and coordination, wasteful for bulk data.
- Short timeouts detect failures fast but misclassify slow nodes as dead and trigger needless retries;
  long timeouts waste capacity holding connections; the right value comes from the latency
  distribution, not from a default.
- Retries raise success probability for transient faults, yet without jitter, budgets and idempotency
  they amplify outages into retry storms and duplicate effects.
- Partitioning by tenant keeps each tenant's operations local and simplifies isolation, but it
  concentrates a large tenant on one partition; compound keys spread load at the cost of cross-partition
  queries.

## Pitfalls
- Treating a timeout as "the request did not happen"; the symptom is duplicate side effects (two
  tickets, two charges, two emails) that cluster around latency spikes.
- Retrying at every layer (client, gateway, service, SDK) so one failure fans out multiplicatively; the
  symptom is a dependency that stays overloaded long after its original fault cleared.
- Ordering events by wall-clock timestamps from different hosts; records appear to go backwards in time
  and last-writer-wins drops updates that users swear they made.
- Holding a distributed lock without a fencing token; rare corruption after GC pauses or network
  blips, impossible to reproduce and blamed on "a race".
- Reading from a replica immediately after writing to the leader; users report that their upload or
  setting "disappeared" and then reappears seconds later.
- Running consensus-backed stores with an even number of nodes or across two data centres; a clean split
  leaves neither side with a majority and the whole cluster unavailable.

## When to use
You do not choose to build a distributed system; any product that calls a model API, keeps state in a
database and runs more than one process already is one. What you choose is how much coordination each
operation gets. Default to local, idempotent, eventually consistent operations and spend coordination on
the few invariants that must hold: quota debits, permission checks, idempotency keys, leader election
for singleton jobs. Measure timeout and retry rates per dependency, replication lag, lock hold times
versus lease length, and the fraction of requests that hit a circuit breaker. Day to day this is the
deadline you pass to the model client, the idempotency key on every tool call, and the choice of
Postgres primary versus replica for a given read. The reliability module covers the mitigation patterns
(circuit breakers, bulkheads, multi-region) in depth; the consistency lesson covers what reads may
return. In interviews, the expected depth is to reason from partial failure: what a timeout can and
cannot tell you, why retries need idempotency, and why a lock needs a token.

## Role lens
- **Engineer:** Sets per-call deadlines, jittered retry budgets and idempotency keys on model and tool
  calls, and propagates trace context so a failed agent run can be reconstructed across services.
- **Architect:** Decides which state needs consensus or strong consistency, how data is partitioned by
  tenant, and how many failure domains the system must survive, then budgets latency accordingly.
- **FDE:** Diagnoses customer-side symptoms (duplicate records, stale reads, "random" timeouts) as
  partial-failure effects and adjusts timeouts, keys and read routing in their integration.

## Check yourself
**Q:** An agent platform runs one "scheduler" process that must be the only instance assigning jobs to
workers. The team uses a Redis key with a 30-second expiry as the lock and renews it every 10 seconds.
Twice last month two schedulers ran at once for a few seconds. Explain how that happened and what to
change.
**A:** The lock is a lease, and a lease holder cannot know it still holds the lease when it acts. If the
scheduler paused for more than 30 seconds (a GC pause, a stalled Redis renewal, a VM migration, or a
clock-related expiry on the Redis side), the key expired, a standby acquired it and started scheduling,
and the original resumed believing it was still the leader until its next renewal failed. That window is
exactly the overlap observed. Two changes close it. First, issue a strictly increasing fencing token with
every acquisition and make the job-assignment write (the protected resource) reject any token lower than
the highest it has seen, so the stale leader's assignments are refused rather than applied. Second, move
leadership to a consensus-backed store (etcd, ZooKeeper) whose lease and token semantics are designed for
this, and alert on lock hold time approaching the TTL. Renewing more often helps only until the next pause
longer than the TTL.

## Quiz
### Q: A call to the embedding service times out after 10 seconds. Which statement is correct?
- [ ] The embedding was not computed, so a retry is always safe
- [ ] The embedding was computed, so the client should look it up instead of retrying
- [x] The outcome is unknown; a retry must carry an idempotency key or the client must query by key first
- [ ] The service is down and the circuit breaker should open immediately
> A timeout only says that no reply arrived in time. The request may have been lost, queued, processed
> with a lost reply, or delayed by a pause on either side. Safe retries therefore need idempotency, and
> breakers open on a rate of failures, not on one timeout.

### Q: With N = 5 replicas, which read/write quorum sizes guarantee a read sees the latest acknowledged write?
- [ ] W = 2, R = 2
- [x] W = 3, R = 3
- [ ] W = 1, R = 4
- [ ] W = 5, R = 0
> Overlap requires W + R > N. With 3 and 3 the sets must share at least one replica, so a read sees the
> newest write. 2 + 2 = 4 does not exceed 5, 1 + 4 = 5 does not either, and a read of zero replicas
> returns nothing.

### Q: Why is a monotonically increasing fencing token needed in addition to a lease-based lock?
- [ ] To make lock acquisition faster under contention
- [ ] To replace the need for a lease expiry
- [x] Because a holder may act after its lease expired, and the resource must be able to reject that stale action
- [ ] To order events across data centres by wall-clock time
> A paused or partitioned holder cannot know its lease lapsed. The token lets the protected resource
> compare the acting holder's grant against the newest grant it has seen and refuse the stale one; the
> lease still handles dead holders, and tokens have nothing to do with wall clocks.
