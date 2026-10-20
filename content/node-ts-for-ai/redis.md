---
id: redis
module: node-ts-for-ai
name: Redis
kind: tool
difficulty: core
est_minutes: 14
related:
  - backend-fundamentals/redis
  - fastapi-for-ai/redis-caching
  - system-design/caching-strategy-and-cache-invalidation
  - node-ts-for-ai/bullmq-and-durable-jobs
  - serving-inference/rate-limiting
  - agents/agent-memory
  - node-ts-for-ai/graceful-shutdown
sources:
  - Official docs: Redis
  - Official docs: ioredis
  - Official docs: node-redis
---

## TL;DR
Redis is an in-memory data-structure server that executes commands one at a time, so each command is
atomic and sub-millisecond. In a Node AI service it is the shared, fast, expiring state a stateless
replica set needs: completion cache, rate limiter, provider-concurrency semaphore, short-lived session
state, distributed locks, pub/sub for streaming across instances, and the store behind BullMQ. The
fundamentals live in the backend module; this lesson covers Node usage and what goes wrong.

## Why it exists
Run two replicas of an agent API behind a load balancer and every in-process `Map` becomes a bug: the
rate limiter counts half the traffic, the response cache misses whenever the balancer picks the other
replica, the "is this run already in progress" flag is wrong half the time, and a streaming client that
reconnects lands on a replica that has no idea about its stream. Postgres could hold all of this, but a
token-bucket check on every request, with disk-backed transactions and MVCC overhead, costs
milliseconds and connections you want for real queries, and TTL-based expiry needs a cron job.

Redis sits in the gap: a network-attached data-structure library where `INCR`, `SET ... NX PX`, `ZADD`
and `XADD` are single atomic operations measured in microseconds server-side, keys expire on their own,
and memory is the only limit. The design choice that makes it work is single-threaded command
execution: no locks, no partial updates, and a Lua script or `MULTI` block runs as one unit *because*
nothing else can interleave. The cost of that choice is equally clear: one slow command (a `KEYS *`, a
huge `SMEMBERS`, a long script) stalls every client, and data that must survive a crash needs
persistence configured deliberately rather than assumed.

## How it works
### Data structures, picked by access pattern
- **Strings** with `SET key value EX ttl`: cached completions, feature flags, idempotency markers
  (`SET ... NX`).
- **Hashes**: one conversation's metadata or a session, updated field by field with `HSET`, expired as
  a whole.
- **Lists**: simple queues and recent-messages windows (`LPUSH` plus `LTRIM`).
- **Sets and sorted sets**: membership and ranking; sorted sets with timestamps as scores implement
  sliding-window rate limits (`ZADD`, `ZREMRANGEBYSCORE`, `ZCARD`).
- **Streams**: an append-only log with consumer groups; the basis for relaying token streams between
  replicas and for BullMQ's event log.
- **Pub/Sub**: fire-and-forget broadcast, used to relay streamed tokens from the replica running the
  LLM call to the replica holding the client's SSE connection. Messages are not stored, so a subscriber
  that is offline misses them; use Streams when that matters.

### Atomicity: single thread, MULTI, Lua
Each command is atomic. Compound logic ("increment, and if this is the first hit set the expiry")
needs either `MULTI`/`EXEC` (queued commands executed without interleaving, but no server-side
branching) or a Lua script via `EVAL`/`EVALSHA`, which runs atomically and can branch. Rate limiters,
token buckets and lock release ("delete only if the value is still my token") are Lua scripts *because*
a get-then-set from the client is a race between two round trips.

### Persistence and eviction
Redis persists by RDB snapshots (periodic, cheap, can lose the last minutes) or AOF (append-only log;
`appendfsync everysec` loses at most about a second). A cache can run with no persistence and
`maxmemory-policy allkeys-lru`; a queue or lock store must run `noeviction` with AOF, *because* evicting
a job key under memory pressure silently loses work. Decide per instance, and do not share one instance
between the two roles.

### From Node
Two mainstream clients: `ioredis` (long the de-facto standard and what BullMQ uses) and `redis`
(node-redis, the official client). Both are promise-based, pipeline automatically when commands are
issued without awaiting in between, and support Lua scripts, Cluster and Sentinel. Node-specific rules:
- A connection in subscriber mode or running a blocking command (`BLPOP`, `XREAD BLOCK`) cannot issue
  other commands, so pub/sub and blocking consumers get their own connection; BullMQ needs a dedicated
  connection for the same reason.
- Configure reconnect and failure behaviour explicitly: ioredis's `maxRetriesPerRequest` and
  `enableOfflineQueue` decide whether a command waits for reconnection or fails fast. On a request path
  you usually want fail-fast *because* a hung Redis should degrade the cache, not take down the API.
- Namespace keys per tenant and per purpose (`t:{tenantId}:cache:{hash}`); hash tags in braces keep
  related keys on one Cluster slot.
- Never block the event loop building huge payloads; values beyond a few hundred kilobytes belong in
  object storage with a Redis pointer.

### Typical AI-service uses
- **Completion cache**: key = SHA-256 of (model, system prompt, messages, parameters), serialised with
  stable key order; cache only deterministic requests (temperature 0 or explicitly cacheable) and store
  usage alongside so cost dashboards stay honest.
- **Provider rate limit / semaphore**: a token bucket in Lua keyed by provider and model so N replicas
  together stay under the vendor's requests-per-minute and tokens-per-minute limits.
- **Run state and locks**: `SET run:{id}:lock token NX PX 30000` to ensure one worker per run, released
  by a check-and-delete script; refresh the TTL while working.
- **Short-term memory**: last-N turns for a conversation with a TTL, while long-term memory lives in
  Postgres or a vector store.

## Example
A completion cache and a fixed-window rate limiter using ioredis. The limiter is a Lua script so the
increment and the expiry are set atomically; the cache key hashes everything that affects the output,
and every Redis failure on the hot path degrades to calling the provider rather than failing the
request.

```ts
import Redis from "ioredis";
import { createHash } from "node:crypto";

const redis = new Redis(process.env.REDIS_URL!, { maxRetriesPerRequest: 1, enableOfflineQueue: false });
redis.on("error", (e) => console.error("redis", e.message));   // an unhandled 'error' event would crash the process

// Atomic fixed-window limiter: the first hit in a window sets the TTL.
redis.defineCommand("hitWindow", {
  numberOfKeys: 1,
  lua: `local n = redis.call('INCR', KEYS[1])
        if n == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
        return n`,
});
export async function allow(tenant: string, limit = 60, windowMs = 60_000): Promise<boolean> {
  const bucket = Math.floor(Date.now() / windowMs);
  const n: number = await (redis as any).hitWindow(`rl:${tenant}:${bucket}`, windowMs);
  return n <= limit;
}

// Cache-aside for deterministic completions.
export async function cachedComplete(req: { model: string; messages: unknown[]; temperature: number },
                                     call: () => Promise<string>, ttlSec = 3600): Promise<string> {
  if (req.temperature !== 0) return call();                      // non-deterministic: do not cache
  const key = "llm:" + createHash("sha256").update(JSON.stringify(req)).digest("hex");
  try {
    const hit = await redis.get(key);
    if (hit !== null) return hit;
  } catch { /* Redis down: fall through to the provider */ }
  const out = await call();
  redis.set(key, out, "EX", ttlSec).catch(() => {});             // best effort, never awaited on the hot path
  return out;
}
```

## Trade-offs
- Microsecond atomic operations and built-in expiry, but everything lives in RAM; a completion cache holding 20 KB responses for a million prompts is 20 GB, so entry size and TTL must be budgeted, not assumed.
- Single-threaded execution gives atomicity for free, but one slow command stalls all clients; the moment anyone needs scans over large keyspaces or long Lua loops, the instance must be split by role.
- Pub/Sub is the simplest way to relay streamed tokens across replicas, but it drops messages for absent subscribers; when a reconnecting client must resume, Streams with consumer groups cost more code and keep the data.
- Running Redis as a cache (`allkeys-lru`, no persistence) is cheap and safe to lose; the same configuration under a queue or lock store silently discards work, so durability costs money (AOF, replicas) and a separate instance.
- Managed Redis (or Valkey, the Linux Foundation fork) removes operations work but adds network hops; placing it in another availability zone turns sub-millisecond calls into milliseconds on every request.

## Pitfalls
- Sharing one connection between pub/sub or blocking commands and normal commands; the symptom is an error that only subscriber commands may be used, or requests that hang behind a `BLPOP`.
- No `error` listener on the client; the symptom is the whole Node process exiting on the first network blip with an unhandled `'error'` event.
- Caching non-deterministic completions or hashing a request object with unstable key order; the symptom is stale or wrong answers served as hits, or a hit rate far below what the traffic suggests.
- Using `KEYS pattern` in production to find a tenant's keys; the symptom is latency spikes on every client while the scan runs. Use `SCAN` with a cursor or maintain an index set.
- Lock release with a plain `DEL`; the symptom is worker A, whose lock expired during a long LLM call, deleting the lock worker B now holds, so two workers process one run.
- BullMQ or locks on an instance configured with `allkeys-lru`; the symptom is jobs that vanish under memory pressure with no error anywhere.

## When to use
Reach for Redis when state must be shared across replicas, must be fast, and is either rebuildable
(cache) or short-lived (rate windows, locks, run state, recent turns). Keep it out of the path for
anything you cannot afford to lose unless you have configured AOF, replicas and `noeviction` and
understand what each costs. Do not use it as the primary store for conversations or evaluation results;
that is Postgres. Measure hit rate, p99 command latency, memory against `maxmemory`, evicted keys and
connected clients per replica. In interviews, be ready to explain why rate limiting needs a Lua script,
why pub/sub loses messages, and why a cache and a queue should not share an instance.

## Role lens
- **Engineer:** Writes the cache-aside and token-bucket helpers, keeps separate connections for subscribers, and makes every Redis call on the request path fail open or fail fast.
- **Architect:** Decides which state is allowed in Redis, the persistence and eviction policy per instance, key namespacing for tenants, and whether Streams or a queue carry cross-replica events.
- **FDE:** Works with whatever managed Redis or Valkey the customer runs, checks eviction policy and version before wiring BullMQ to it, and sizes memory from entry size and TTL.

## Check yourself
**Q:** Your agent API runs four replicas. A streaming response is produced by the replica that called
the model, but the client's SSE connection may be on another replica, and clients sometimes reconnect
mid-stream. Design the Redis part and explain the trade-offs.
**A:** The producing replica appends each token chunk to a Redis Stream keyed by run id with a TTL, and
the replica holding the SSE connection reads it with `XREAD BLOCK` from the last id the client
acknowledged, which makes reconnects resumable because the chunks are stored, not merely broadcast.
Pub/Sub would be simpler and lower latency, but a client reconnecting would miss everything published
while it was away. Each blocking reader needs its own connection, and the stream must be trimmed or
expired so memory does not grow with every run. The instance should run `noeviction` so chunks are not
dropped under pressure, and the final entry should be a sentinel so the consumer knows the stream is
complete rather than stalled.

## Quiz
### Q: Why is a rate limiter implemented as a Lua script instead of `GET` then `SET` from Node?
- [ ] Lua runs faster than JavaScript on the Redis server
- [x] The read and the write from Node are separate round trips, so two replicas can both read the same count and both allow a request past the limit
- [ ] Redis rejects `SET` from clients that have recently called `GET`
- [ ] Lua scripts are the only way to set a TTL on a key
> Redis executes each command atomically, but a client-side get-then-set is two commands with a gap; a script (or `MULTI`) runs as one unit on the single-threaded server, which closes the race.

### Q: BullMQ jobs are disappearing from a shared Redis instance under load, with no errors logged. What is the most likely cause?
- [ ] The jobs exceeded Redis's maximum value size
- [ ] Workers are running with too much concurrency
- [x] The instance uses an `allkeys-lru` eviction policy and job keys are evicted when memory fills
- [ ] Pub/Sub messages were dropped because no subscriber was connected
> A cache-style eviction policy treats job state like any other key and silently removes it at `maxmemory`; queue and lock workloads need `noeviction` and ideally their own instance.

### Q: Which client setup is correct for a Node service that both caches completions and subscribes to a channel?
- [ ] One connection for everything, because ioredis multiplexes commands
- [x] Two connections: one for normal commands and a dedicated one in subscriber mode
- [ ] A new connection per request to avoid head-of-line blocking
- [ ] A connection pool of at least one per CPU core
> A connection that has issued `SUBSCRIBE` can only run subscriber commands, so `GET`/`SET` on it fail; a separate subscriber connection is required, while normal commands pipeline fine on one shared connection.
