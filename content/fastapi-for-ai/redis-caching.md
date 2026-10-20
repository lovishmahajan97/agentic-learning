---
id: redis-caching
module: fastapi-for-ai
name: Redis caching
kind: pattern
difficulty: core
est_minutes: 15
related:
  - backend-fundamentals/redis
  - serving-inference/caching
  - prompting/prompt-caching
  - fastapi-for-ai/lifespan
  - fastapi-for-ai/shared-httpx-asyncclient
  - fastapi-for-ai/rate-limiting
  - fastapi-for-ai/uvicorn-gunicorn-workers
sources:
  - Official docs: Redis (SET, EXPIRE, eviction policies)
  - Official docs: redis-py (asyncio client)
---

## TL;DR
An LLM or embedding call costs real money and seconds of latency, and many requests repeat exactly.
Redis caching puts a shared, in-memory key-value store in front of those calls: hash the full,
normalised request into a key, return the stored result on a hit, and call the model and store with
a TTL on a miss. It is shared across workers, which an in-process dict is not, and it is safe only when
the key captures everything that changes the answer.

## Why it exists
A FastAPI service in production runs several worker processes, often on several hosts, behind a load
balancer. The expensive thing it does, calling a hosted model, an embedding API or a reranker, is slow
(hundreds of milliseconds to tens of seconds) and billed per token. A meaningful share of that traffic
repeats: the same FAQ question, the same document embedded on every re-index, the same classification
prompt over the same ticket, the same tool lookup inside an agent loop that retries.

The naive fix is a module-level dict or `functools.lru_cache`. It fails in three ways. It is per
process, so with eight workers the hit rate is divided across eight cold caches and a deploy wipes all
of them. It lives in the same heap as the app, so an unbounded cache becomes a memory leak that ends
in an OOM kill. And `lru_cache` on an `async def` caches the coroutine object, not its result, so the
second await fails. Caching in the provider's own prompt cache is different again: it discounts the
prefix of a prompt, but you still pay for and wait on the generated output.

Redis solves the sharing and bounding problems: one store all workers see, with per-key expiry, a
memory ceiling and an eviction policy, and sub-millisecond reads on a local network. What it does not
solve is correctness; that is entirely about the key.

## How it works
### Cache-aside, step by step
1. **Build the key** from everything that determines the output: model ID, prompt template version,
   the rendered messages, sampling parameters, tool schema, and the tenant if data is tenant-scoped.
   Serialise them canonically (sorted JSON keys, no whitespace variance) and hash with SHA-256, *because*
   raw prompts are long and keys should be bounded, and canonical serialisation makes semantically
   identical requests collide on purpose.
2. **Read** with `GET`. On a hit, deserialise and return. Record a hit metric.
3. **On a miss**, call the model, then `SET key value EX ttl`. The TTL is the staleness contract: it
   bounds how long a wrong or outdated answer can survive, *because* nobody reliably remembers to
   invalidate model outputs when a prompt or a source document changes.
4. **Namespace and version** keys, for example `llm:v3:{tenant}:{sha}`. Bumping `v3` to `v4` when the
   prompt or model changes invalidates everything at once without a scan, *because* old keys simply stop
   being read and age out under TTL.

### What is safe to cache
Deterministic or near-deterministic work is the sweet spot: embeddings (same model and text give the
same vector), classification and extraction at temperature 0, retrieval results for a fixed index
version, and tool lookups. Creative generation at temperature above 0 is a product decision: caching
it means every user gets the same "random" answer. Even temperature 0 is not bit-for-bit deterministic
on most hosted models, so a cache *imposes* consistency rather than preserving it, which is often what
you want.

### Wiring it into FastAPI
Create one `redis.asyncio` client in the lifespan, store it on `app.state`, and hand it to endpoints
through a dependency. The client owns a connection pool, *because* opening a TCP connection per
request is slower than the cache read it serves. Use the asyncio client, not the sync one, *because*
a sync call inside `async def` blocks the event loop for every request on that worker.

### Stampede protection
When a hot key expires, every concurrent request misses at once and all of them call the model: a
cache stampede that can multiply provider spend and trip rate limits. Defences, cheapest first: add
random jitter to TTLs so keys do not expire together; let one request recompute while others wait,
using a short lock taken with `SET lock:key token NX PX 30000` (`NX` makes the take atomic, the expiry
means a crashed holder cannot block forever); or serve the stale value while one request refreshes.

### Memory and eviction
Set `maxmemory` and an eviction policy. For a pure cache, `allkeys-lru` (or `allkeys-lfu`) lets
Redis drop cold keys when full. If the same Redis also holds rate-limit counters or sessions that must
not vanish, use `volatile-*` policies, which only evict keys that have a TTL, or better, a separate
instance, *because* a cache filling up should never evict state that something else relies on.

### Failure mode
Treat Redis as optional on the read path: on a connection error or timeout, log, count, and fall
through to the model. A cache outage should cost money and latency, not availability.

## Example
A cache-aside wrapper for an embedding call, with a canonical key, TTL jitter and fail-open reads.
`embed_remote` stands in for the real provider call.

```python
import hashlib, json, random
import redis.asyncio as redis
from redis.exceptions import RedisError
from fastapi import Depends, FastAPI, Request
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = redis.from_url("redis://localhost:6379/0", socket_timeout=0.2)
    yield
    await app.state.redis.aclose()          # close() on older redis-py releases

app = FastAPI(lifespan=lifespan)

def get_redis(request: Request) -> redis.Redis:
    return request.app.state.redis

def cache_key(model: str, text: str, tenant: str) -> str:
    payload = json.dumps({"m": model, "t": text}, sort_keys=True, separators=(",", ":"))
    return f"emb:v2:{tenant}:{hashlib.sha256(payload.encode()).hexdigest()}"

async def embed_cached(r: redis.Redis, model: str, text: str, tenant: str) -> list[float]:
    key = cache_key(model, text, tenant)
    try:
        if (hit := await r.get(key)) is not None:
            return json.loads(hit)
    except RedisError:
        pass                                   # fail open: cache down means slower, not broken
    vec = await embed_remote(model, text)      # the expensive call
    ttl = 7 * 86400 + random.randint(0, 3600)  # jitter spreads expiries
    try:
        await r.set(key, json.dumps(vec), ex=ttl)
    except RedisError:
        pass
    return vec

@app.post("/embed")
async def embed(body: dict, r: redis.Redis = Depends(get_redis)):
    return {"vector": await embed_cached(r, "text-embedding-x", body["text"], body["tenant"])}
```

Re-indexing 100,000 chunks where 90% are unchanged now pays for roughly 10,000 embedding calls instead
of 100,000; the exact saving depends on how much content really changes between runs.

## Trade-offs
- A shared cache raises hit rate across all workers and survives deploys, but adds a network hop and a
  dependency; for tiny, process-local, rarely-changing data an in-process LRU is faster and simpler.
- Long TTLs maximise savings and stability but extend how long a stale or wrong answer is served; short
  TTLs flip the balance when sources or prompts change often.
- Exact-match keys are always correct when the key is complete, but miss paraphrases; semantic
  (embedding-similarity) caching catches paraphrases at the risk of returning a near-miss answer.
- Caching temperature-above-zero generations cuts cost but removes variety users may expect; it pays
  off for FAQ-style traffic and backfires for creative or personalised output.
- One Redis for cache, limits and sessions is cheaper to run, but under memory pressure eviction can
  remove non-cache state; separate instances cost more and fail independently.

## Pitfalls
- Leaving the tenant or user out of the key on tenant-scoped data; the symptom is one customer seeing
  an answer generated from another customer's documents, which is a data leak, not a bug.
- Forgetting to include the model ID or prompt version, so a prompt fix appears to do nothing; the
  symptom is old outputs persisting after a deploy until TTLs expire.
- Using the sync `redis` client inside `async def`; p99 latency climbs for every endpoint on the
  worker because each cache call blocks the event loop.
- No `maxmemory` or no TTL on cache keys; Redis memory grows until the host OOMs or, with
  `noeviction`, writes start failing with errors the app treats as outages.
- Caching error responses or truncated generations; the symptom is a transient provider failure being
  replayed to every user for the full TTL.

## When to use
Reach for Redis caching when the same expensive, deterministic-enough call repeats across users or
workers: embeddings, temperature-0 classification and extraction, retrieval results, slow tool
lookups. Do not use it to paper over a missing database index, for personalised or creative
generations, or where serving a stale answer is a correctness or compliance problem. Measure hit
rate, latency on hit versus miss, provider spend before and after, and Redis memory and evictions.
In interviews, expect to design the key, choose the TTL, explain stampede protection and say what
happens when Redis is down.

## Role lens
- **Engineer:** Writes the canonical key builder, the async cache-aside wrapper, the lifespan-managed
  client and the fail-open error handling, and adds hit-rate metrics.
- **Architect:** Decides what may be cached, the staleness contract per data class, tenant isolation in
  keys, and whether cache shares an instance with rate limits and sessions.
- **FDE:** Finds the repeated calls in a customer's traffic, estimates savings from real logs, and checks
  that their compliance rules allow storing model outputs and for how long.

## Check yourself
**Q:** After adding a Redis cache to a support-bot endpoint, spend drops 40% but a customer reports
that answers ignore a policy document updated yesterday. What went wrong and how do you fix it
without losing the savings?
**A:** The key captures the question and prompt but not the state of the retrieved sources, so a
cached answer built from the old document is still valid under its TTL. Fixes: include the index or
document-set version in the key (or a hash of the retrieved chunk IDs and their versions), so a
re-index changes the key; bump the key namespace on source updates; or shorten TTL for data classes
that change often. I would cache retrieval and generation separately, keyed on their own inputs, and
add a hit metric per namespace to confirm savings survive.

## Quiz
### Q: Why is `functools.lru_cache` a poor choice for caching LLM calls in a multi-worker FastAPI service?
- [ ] It cannot hash strings longer than a few kilobytes
- [x] Each worker process has its own cache, so hit rate is split across workers and lost on every restart
- [ ] It is not thread-safe, so concurrent requests corrupt it
- [ ] It always evicts the most recently used entry first
> `lru_cache` lives in one process's memory, so N workers keep N cold copies and a deploy empties all of
> them. It also caches coroutine objects on `async def`, not results. Redis gives one shared, bounded store.

### Q: A hot cached key expires and 200 concurrent requests all call the model at once. What is the most direct fix?
- [ ] Switch the eviction policy to `noeviction`
- [ ] Remove the TTL so the key never expires
- [x] Let one request recompute under a short `SET NX` lock while others wait or serve the stale value, and jitter TTLs
- [ ] Increase the Redis connection pool size
> This is a cache stampede. A single-flight lock or stale-while-revalidate collapses 200 misses into one
> model call; jitter prevents many keys expiring together. Removing TTLs trades it for unbounded staleness.

### Q: Which item is least important to include in the cache key for a temperature-0 extraction endpoint?
- [ ] The model ID
- [ ] The prompt template version
- [ ] The tenant ID when inputs are tenant-scoped documents
- [x] The request's arrival timestamp
> The key must contain everything that changes the output. Model, prompt version and tenant all do;
> the arrival timestamp does not, and including it would make every key unique and the hit rate zero.
