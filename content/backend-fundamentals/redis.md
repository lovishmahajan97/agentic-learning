---
id: redis
module: backend-fundamentals
name: Redis
kind: tool
difficulty: core
est_minutes: 15
status: reviewed
related:
  - fastapi-for-ai/redis-caching
  - node-ts-for-ai/redis
  - backend-fundamentals/nosql
  - backend-fundamentals/message-queues
  - backend-fundamentals/background-jobs-durable-workers
  - fastapi-for-ai/rate-limiting
  - system-design/caching-strategy-and-cache-invalidation
  - serving-inference/caching
sources:
  - Official docs: Redis
  - Kleppmann, 2017 — Designing Data-Intensive Applications
---

## TL;DR
Redis is an in-memory data-structure server: strings, hashes, lists, sets, sorted sets and streams,
addressed by key, with every command executed atomically on a single thread. That combination makes it
the default place for the fast, shared, short-lived state a backend needs (caches, rate limits, locks,
sessions, queues) at the cost of memory-sized data and durability that is weaker than a database unless
you configure and accept the trade.

## Why it exists
Every multi-instance backend needs some state that is shared across processes and read far more often
than it changes: the cached answer to an expensive call, the count of requests a client made this minute,
who currently holds a lock, the session for a logged-in user. Putting that in the primary database works
until it does not: a relational database stores rows on disk with transaction logging and MVCC, so a
simple counter increment costs a write-ahead-log append and a row version, and ten thousand increments a
second from a rate limiter compete with real transactions for the same CPU and I/O. Keeping it in process
memory is fast but wrong as soon as there are two processes, because each has its own copy and a load
balancer sends consecutive requests to different ones.

The naive middle ground, a plain key-value cache like memcached, solves the shared-lookup problem but
offers only get and set, so anything that needs structure, such as "add this item to the user's
sorted-by-time list and trim it to 100", becomes a read-modify-write race between two servers. Redis
exists to make those operations single, atomic commands on rich data structures, executed in memory
with sub-millisecond latency, and to add just enough persistence, replication and clustering that it can
be trusted with more than a cache. In AI backends it ends up holding response caches keyed by prompt
hash, per-tenant token-rate limits, semaphores that cap concurrent model calls, and the broker behind
job queues.

## How it works
### Data structures, not just values
Each key maps to one typed value: a **string** (also used for integers, with `INCR`), a **hash** (field
to value map, good for an object), a **list** (push/pop at either end: a queue), a **set** and **sorted
set** (members with a score, kept ordered: leaderboards, sliding windows, scheduled jobs), and a
**stream** (an append-only log with consumer groups, the closest thing to Kafka inside Redis). Separately,
**pub/sub** broadcasts a message to whoever is subscribed at that instant and stores nothing, so a
subscriber that is disconnected for a second misses what was published in that second; it suits live
notifications (push a token stream to whichever web node holds the user's socket), never work that must
be done. Commands
operate on the structure in place, so "push onto the list and trim it" is two commands rather than a
round trip to fetch, modify and write back. Keys can carry a TTL, after which they are deleted; TTLs are
how caches and rate-limit windows expire without a cleanup job.

### Single-threaded execution is the point
Commands are executed one at a time on a single thread (newer versions use extra threads for network
I/O, but command execution stays serialized). That is why every command is atomic with no locking, and
why `INCR`, `SET ... NX`, `ZADD` and friends can be used as the primitives of counters, locks and
schedulers across many application servers. It is also why one slow command (`KEYS *`, a huge `SMEMBERS`,
a long Lua script) stalls every client. For multi-command atomicity there are `MULTI`/`EXEC` transactions
and Lua scripts, which run as a single unit on the server.

### Persistence and what "durable" means here
Redis is memory-first; persistence is a configuration choice. **RDB** writes periodic snapshots, cheap
but losing everything since the last snapshot on a crash. **AOF** appends every write to a log, and its
`fsync` policy decides the window of loss (every write, every second, or left to the OS). Neither makes
Redis a system of record: treat data in Redis as rebuildable, or at least as acceptable to lose a second
of, and put anything that must survive in a database.

### Memory limits and eviction
`maxmemory` caps the dataset; when it is reached, the eviction policy decides what happens:
`noeviction` (writes fail), `allkeys-lru` (evict least recently used anything), `volatile-lru` and
`volatile-ttl` (evict only keys with a TTL), plus LFU and random variants. LRU is approximated by sampling
a few keys rather than tracking exact order. A cache wants `allkeys-lru` or `allkeys-lfu`; a queue or lock
store wants `noeviction`, because evicting a job silently is worse than failing loudly. `maxmemory` and
the policy apply to the whole instance, including every numbered logical database, which is why mixing a
cache and a queue in one instance is a design error.

### Replication, Sentinel and Cluster
A primary replicates asynchronously to replicas; Sentinel monitors and promotes a replica on failure.
Because replication is asynchronous, a failover can lose the last writes, which matters for locks and
queues. Redis Cluster shards keys across nodes by hashing the key into 16,384 slots; multi-key commands
then only work when all keys hash to the same slot, which you control with hash tags such as
`{tenant42}:limit`. Managed offerings (ElastiCache, Memorystore, Upstash, Redis Cloud) run these for you.
Note that Redis changed its licence in 2024 (to source-available terms) and the Linux Foundation fork
Valkey appeared; Redis 8 in 2025 added AGPLv3 as an option. The core commands are the same, but newer
features diverge, so check which one your platform ships.

### The patterns that matter in AI backends
- **Cache-aside:** look up `cache:{model}:{sha256(prompt+params)}`; on miss, call the model, `SET` with
  a TTL. Only safe when the call is deterministic enough for your use (temperature 0, or you accept a
  stored sample).
- **Rate limiting:** `INCR` a per-tenant-per-window key and set its TTL on first increment. Atomic, so it
  is correct across all servers, but a *fixed* window lets a client spend its whole limit in the last
  second of one window and again in the first second of the next: up to 2× the limit in two seconds.
  When that burst matters, keep a sorted set of request timestamps per client (`ZREMRANGEBYSCORE` the
  expired ones, `ZCARD`, `ZADD`, in one script) for an exact sliding window, at one entry of memory per
  request, or weight the previous window's count to approximate one in constant memory.
- **Concurrency semaphore:** cap concurrent calls to a model provider with a counter bounded by a Lua
  script, so a burst does not trip the provider's limits.
- **Locks:** `SET lock:key token NX PX 30000` acquires only if absent and expires if the holder dies.
  Release by checking the token in a Lua script, *because* a plain `DEL` from a holder whose lease
  already expired would delete the next holder's lock. Expiry means a slow holder can lose the lock while
  still working, so protected resources need a fencing token if correctness matters: a number from `INCR`
  issued with each acquisition, which the resource stores and uses to reject writes carrying an older one.
  Redlock (acquiring on a majority of independent nodes) addresses losing the lock on failover, but its
  safety under clock drift and process pauses is disputed (Kleppmann's critique is the usual reference),
  so treat Redis locks as an efficiency mechanism, not a correctness guarantee, unless fenced.
- **Queues and streams:** lists for simple job queues (`LPUSH`/`BRPOP`), streams with consumer groups
  for acknowledged, replayable delivery; BullMQ, Celery and RQ build on these.

## Example
Three primitives in redis-py: an LLM response cache, a fixed-window rate limiter made atomic with a Lua
script, and a lock with a safe release. The script runs server-side so the increment and the TTL cannot
be separated by a crash: with two client calls, a process that dies after `INCR` and before `EXPIRE`
leaves a key with no TTL, and that tenant is rate-limited forever once it reaches the limit. The cache key
hashes every parameter that changes the output, because two requests that differ only in temperature or
system prompt must not share an entry.

```python
import hashlib, json, redis, time, uuid
from contextlib import contextmanager

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def cached_completion(model, prompt, call, **params):
    spec = json.dumps({"prompt": prompt, **params}, sort_keys=True)   # stable across dict order
    key = f"cache:{model}:" + hashlib.sha256(spec.encode()).hexdigest()
    hit = r.get(key)
    if hit is not None:
        return json.loads(hit)
    out = call(model, prompt, **params)       # the expensive provider call
    r.set(key, json.dumps(out), ex=3600)      # 1-hour TTL; eviction handles the rest
    return out

RATE_LUA = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n
"""
rate_script = r.register_script(RATE_LUA)

def allow(tenant, limit=60, window_s=60):
    key = f"rl:{tenant}:{int(time.time() // window_s)}"   # e.g. rl:t42:29361600, one key per minute
    return rate_script(keys=[key], args=[window_s]) <= limit   # 61st call this minute -> False

RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""
release_script = r.register_script(RELEASE_LUA)

@contextmanager
def with_lock(name, ttl_ms=30000):
    token = str(uuid.uuid4())
    if not r.set(f"lock:{name}", token, nx=True, px=ttl_ms):
        raise RuntimeError("busy")
    try:
        yield                                  # with with_lock("reindex:tenant42"): ...
    finally:
        release_script(keys=[f"lock:{name}"], args=[token])   # only deletes if still ours
```

## Trade-offs
- Sub-millisecond operations in memory versus a dataset that must fit in RAM; when the working set
  exceeds memory, either eviction or a move to a disk-based store is forced.
- AOF with fsync every write approaches database durability but cuts throughput; fsync every second is
  the usual compromise, accepting up to a second of loss on a crash.
- Lua scripts and `MULTI` give atomicity across commands, but a long script blocks every other client;
  the trade flips to application-side logic when the script would touch thousands of keys.
- Cluster mode scales memory and throughput across nodes, but multi-key operations require hash tags and
  cross-slot transactions are unavailable, which complicates scripts written for a single node.
- Expiring locks never deadlock after a crash, but they can be lost by a slow holder; adding fencing
  tokens restores safety at the cost of making the protected resource check the token.
- Using Redis as a queue is simple and fast, but asynchronous replication and eviction can drop jobs;
  when loss is unacceptable, a durable broker or a database-backed queue is the better trade.

## Pitfalls
- Running `KEYS *` or a huge `SMEMBERS` in production; the symptom is every client stalling for seconds
  while the single thread walks the keyspace. Use `SCAN`.
- Caching without a TTL or a sensible eviction policy; the symptom is memory growth until `maxmemory`
  and then either write failures (`noeviction`) or surprising evictions of non-cache keys.
- Sharing one instance between cache and queue; the symptom is jobs vanishing under memory pressure
  because `allkeys-lru` evicted them.
- Setting a lock without an expiry; the symptom is a permanent "busy" after one worker crashes mid-task.
- Treating a lock with expiry as exclusive without fencing; the symptom is two workers writing the same
  resource after a GC pause or slow model call exceeded the TTL.
- Hot keys and large values (a 5 MB cached response read 1,000 times a second); the symptom is one node
  saturating its network while others idle.
- Opening a new connection per request instead of pooling; the symptom is connection-count spikes and
  latency dominated by handshakes.
- Cache stampede when a popular key expires; the symptom is a burst of identical provider calls at the
  same second. Use a short lock or early refresh on the miss path.

## When to use
Use Redis for state that is shared across processes, read-heavy or counter-shaped, and rebuildable or
tolerable to lose: caches, rate limits, semaphores, sessions, short-lived coordination, and simple job
queues where occasional loss is acceptable. Do not use it as the system of record for conversations,
billing or anything you cannot recompute; use PostgreSQL for that and put Redis in front. Before adding
it, confirm that the database genuinely cannot carry the load (a rate limit on a few hundred requests per
second may be fine in PostgreSQL). Watch memory usage, evicted keys, hit ratio and slow-log entries. In
interviews, be ready to explain why single-threaded execution makes `INCR` a correct distributed counter,
and where an expiring lock is unsafe.

## Role lens
- **Engineer:** Implements cache-aside, rate limiting and semaphores with atomic commands or Lua, sets
  TTLs on every key, and pools connections in FastAPI or Node services.
- **Architect:** Decides what may live only in Redis versus the database, the eviction policy and
  persistence mode per instance, and whether the queue needs a durable broker instead.
- **FDE:** Checks a customer's managed Redis tier, memory ceiling and eviction policy before deploying a
  cache or limiter, and explains what is lost on failover of their setup.

## Check yourself
**Q:** A team uses one Redis instance with `allkeys-lru` for both the LLM response cache and the Celery
job queue. Jobs occasionally disappear under load. Explain the mechanism and the fix.
**A:** Under memory pressure `allkeys-lru` evicts keys that have not been touched recently, regardless of
what they are. Queue libraries keep state in many keys besides the main queue list: Celery keeps
unacknowledged and scheduled (ETA) messages and task results in separate keys, and libraries such as RQ
and BullMQ store one hash per job. A job that has been waiting is untouched since it was enqueued, and
a queue for a rarely used task type may be idle as a whole. The cache fills memory with fresh entries,
eviction samples and removes idle keys, and some of those are queue state, so jobs vanish without an
error. The fix is separation into two instances: numbered logical databases do not help, because
`maxmemory` and the eviction policy are instance-wide. The queue instance gets `noeviction` and AOF
persistence, so running out of memory fails enqueues loudly instead of silently discarding work; the
cache instance gets `allkeys-lru` and TTLs on every key. If loss is still unacceptable, move the queue to
a durable broker or a database-backed queue.

## Quiz
### Q: Why can a plain INCR in Redis be used as a correct rate-limit counter across many application servers?
- [ ] Redis replicates the increment to every application server's memory
- [x] Commands execute one at a time on a single thread, so each INCR is atomic without client-side locking
- [ ] INCR acquires a row lock like a relational UPDATE
- [ ] The client library retries INCR until the value is consistent
> Serial execution means no two increments interleave, so the returned count is exact. A memcached-style
> get-then-set would race between servers; INCR's atomicity is a property of Redis's execution model.

### Q: A lock is acquired with SET key token NX PX 30000. What problem remains if the holder takes longer than 30 seconds?
- [ ] The lock becomes permanent and other workers block forever
- [ ] The token is rotated automatically and the holder cannot release it
- [x] The key expires, another worker acquires the lock, and two holders can act on the resource at once
- [ ] Redis returns an error to the original holder on its next command
> Expiry protects against crashed holders but not slow ones. Once the key is gone a second worker
> acquires it; only a fencing token checked by the protected resource makes this safe.

### Q: Which persistence and eviction configuration is appropriate for a Redis instance used as a job queue?
- [ ] RDB snapshots every hour with allkeys-lru
- [ ] No persistence with volatile-lru, since jobs are short-lived
- [x] AOF persistence with noeviction, so a full instance fails writes rather than discarding jobs
- [ ] AOF persistence with allkeys-lru, so old jobs make room for new ones
> A queue must fail loudly, never drop work silently. noeviction makes memory exhaustion an error the
> producer sees, and AOF limits what a crash loses; LRU policies would evict idle waiting jobs.
