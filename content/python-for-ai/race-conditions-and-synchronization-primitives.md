---
id: race-conditions-and-synchronization-primitives
module: python-for-ai
name: Race conditions & synchronization primitives (asyncio.Lock, Semaphore)
kind: concept
difficulty: core
est_minutes: 16
related:
  - python-for-ai/asyncio
  - python-for-ai/async-cancellation-and-exception-propagation
  - python-for-ai/asyncio-vs-threads-vs-multiprocessing
  - python-for-ai/gil
  - serving-inference/batching-and-concurrency-limits
  - serving-inference/rate-limiting
  - serving-inference/backpressure
  - agents/parallel-tool-calls
sources:
  - Official docs: asyncio (Synchronization Primitives)
---

## TL;DR
asyncio runs on one thread, yet it still has race conditions: any `await` between reading shared state
and writing it lets another coroutine change that state in between. `asyncio.Lock` makes such a
check-then-act sequence exclusive, and `asyncio.Semaphore` caps how many coroutines may be inside a
section at once, which is how you bound concurrent calls to an LLM provider. Neither is thread-safe,
and a semaphore limits concurrency, not requests per second.

## Why it exists
A common belief is that single-threaded async code cannot race. It is half true: between two awaits a
coroutine runs uninterrupted, so `self.count += 1` on its own is safe in asyncio, unlike in threaded
code. But real logic spans awaits. "If the auth token is expired, fetch a new one and store it" reads
state, awaits a network call, then writes. While it waits, a hundred other requests run the same check,
see the same expired token, and each starts its own refresh. That is a thundering herd against the
auth server, and if refreshes invalidate earlier tokens, some requests end up holding revoked ones.

The second forcing problem is resource limits. `gather` over 5,000 prompts starts 5,000 HTTP requests
immediately. The provider answers most with 429s, the client's connection pool queues the rest, memory
fills with pending responses, and timeouts fire on requests that never left the process. You need a
gate that lets N coroutines through and makes the rest wait.

Naive fixes fail. A boolean `refreshing` flag is itself check-then-act and needs a way to wait for
the refresh to finish, which is a lock with extra steps. A `threading.Lock` blocks the whole event loop
when contended, stalling every coroutine instead of just the waiter. asyncio's primitives exist because
waiting must itself be an `await` that yields to the loop.

## How it works
### Where races come from
In asyncio a race needs three things: shared mutable state, a read and a dependent write, and an
`await` between them. Every `await` is a point where the loop may run any other ready coroutine. So
the audit question for a coroutine is: "what shared state did I read before this await, and do I still
rely on it after?"

### asyncio.Lock
A lock is owned by at most one coroutine. `async with lock:` awaits `acquire()`; if the lock is held,
the coroutine is parked in a FIFO queue of waiters and the loop runs other work. On release the next
waiter is woken. Waiting costs nothing but a queued future, *because* no thread is blocked. The
standard pattern for a shared cache is **double-checked locking**: check without the lock on the fast
path, take the lock on a miss, then check again inside, *because* another coroutine may have refreshed
the value while you waited for the lock. The lock is not reentrant: acquiring it again from the
coroutine that holds it deadlocks.

### asyncio.Semaphore
A semaphore holds a counter initialised to N. `acquire()` decrements it, or waits if it is zero;
`release()` increments it and wakes a waiter. `async with sem:` around an LLM call therefore keeps at
most N calls in flight no matter how many tasks exist. `BoundedSemaphore` additionally raises if you
release more times than you acquired, which catches bookkeeping bugs. A semaphore caps *concurrency*.
Throughput is concurrency divided by latency (Little's law), so if latency rises, the same semaphore
yields fewer requests per second. A provider limit expressed in requests or tokens per minute needs a
rate limiter, such as a token bucket, often combined with a semaphore.

### The other primitives
`asyncio.Event` lets many coroutines wait until one sets a flag, useful for "model warmed up" or
"shutdown started". `asyncio.Condition` combines a lock with wait/notify for more complex predicates.
`asyncio.Queue(maxsize=N)` is often the best primitive of all: a bounded producer/consumer queue
gives you concurrency limits and backpressure without explicit locking.

### What they do not cover
These primitives coordinate coroutines on one event loop. They are not thread-safe and do nothing
across processes or replicas. Two Uvicorn workers each holding their own `Semaphore(8)` allow 16
concurrent calls; a cross-replica limit needs a shared store such as Redis or a gateway.

## Example
```python
import asyncio, time

class TokenCache:
    def __init__(self) -> None:
        self._token: str | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()
        self.refreshes = 0

    def _valid(self) -> bool:
        return self._token is not None and time.monotonic() < self._expires

    async def get(self) -> str:
        if self._valid():
            return self._token            # fast path, no lock
        async with self._lock:
            if not self._valid():         # re-check: someone may have refreshed
                self.refreshes += 1
                await asyncio.sleep(0.1)  # stands in for the auth call
                self._token, self._expires = "tok", time.monotonic() + 300
            return self._token

async def call_llm(cache: TokenCache, sem: asyncio.Semaphore, i: int) -> int:
    async with sem:                       # at most 8 requests in flight
        await cache.get()
        await asyncio.sleep(0.05)         # stands in for the model call
        return i

async def main() -> None:
    cache, sem = TokenCache(), asyncio.Semaphore(8)
    results = await asyncio.gather(*(call_llm(cache, sem, i) for i in range(100)))
    print(len(results), "calls,", cache.refreshes, "token refresh")

asyncio.run(main())
```
This prints `100 calls, 1 token refresh`. Remove the lock and the first eight coroutines through the
semaphore all see no token, all await the auth call, and you get eight refreshes. Raise the semaphore
to 100 and you get 100. The bug scales with your concurrency setting, which is why it often appears
only under load.

## Trade-offs
- A lock makes a critical section correct, but serialises it; if the section contains a slow await,
  throughput collapses to one call at a time, so keep only the minimum inside the lock.
- A tight semaphore protects the provider and your memory, but leaves capacity unused; when the provider
  quota is generous and latency is high, a larger limit gives more throughput.
- In-process primitives are fast and simple, but are invisible to other workers and replicas; once
  you scale horizontally, limits must move to a shared store or an API gateway.
- A bounded `asyncio.Queue` with fixed workers is easier to reason about than many tasks behind a
  semaphore, at the cost of more structure; it wins when work arrives continuously rather than in batches.

## Pitfalls
- Assuming single-threaded means race-free; the symptom is duplicate refreshes, duplicate writes or
  double-spent budgets that only show up under concurrent load.
- Creating a primitive at module import time and using it from different event loops, as tests often
  do; recent Python versions bind a primitive to the loop where it is first used and raise a
  `RuntimeError` when another loop touches it.
- Using a semaphore to enforce a requests-per-minute quota; the symptom is 429s returning when
  provider latency drops, because the same concurrency then produces more requests per minute.
- Re-acquiring the same `asyncio.Lock` in a nested call; the coroutine waits on itself forever and the
  request hangs with no error.
- Holding a lock or semaphore across a call with no timeout; one hung upstream request starves
  every waiter behind it.

## When to use
Reach for a lock when a coroutine reads shared state, awaits, then writes based on what it read:
token and client caches, lazy model or index loading, per-conversation state in an agent, budget
counters. Reach for a semaphore (or a bounded queue) whenever you fan out to an external dependency
with finite capacity: LLM providers, embedding APIs, vector stores, scrapers. Do not add locks around
code with no await inside; it is already atomic with respect to other coroutines. Measure in-flight
requests, 429 rate and queue wait time; if wait time dominates latency, the limit is too tight or the
dependency is saturated. In interviews, be ready to explain why asyncio can race at all.

## Role lens
- **Engineer:** Wraps provider calls in a semaphore sized from the quota, and uses double-checked
  locking for shared clients and token caches.
- **Architect:** Decides where concurrency limits live, per process, per replica or in a shared gateway,
  so the total stays inside provider quotas as the fleet scales.
- **FDE:** Tunes a customer's batch-ingestion concurrency against their own API quota and explains why
  doubling workers doubled their 429s instead of their throughput.

## Check yourself
**Q:** A team wraps OpenAI calls in `asyncio.Semaphore(50)` per worker, runs 4 Uvicorn workers, and still
gets 429s on a 3,000 requests-per-minute quota. Explain and propose a fix.
**A:** The semaphore is per process, so the fleet allows 200 concurrent calls, and it limits concurrency
rather than rate. By Little's law, 200 in flight at 2 seconds average latency is about 6,000 requests per
minute (illustrative numbers), double the quota, and if latency drops the rate rises further. The fix is
a rate limiter based on requests and tokens per minute, shared across workers through Redis or enforced
by a gateway, with the semaphore kept only to bound memory and connections. Retries on 429 should honour
`Retry-After` with jittered backoff so they do not amplify the burst.

## Quiz
### Q: In single-threaded asyncio code, when can two coroutines interfere with each other's view of shared state?
- [ ] Never, because only one coroutine runs at a time
- [ ] Only when the GIL is released by a C extension
- [x] When one reads the state, awaits something, and then acts on what it read
- [ ] Only when the shared state is a list or dict rather than an int
> The event loop switches coroutines only at awaits, so a read and a dependent write separated by an
> await can interleave with another coroutine doing the same. Code with no await in between is atomic.

### Q: Why does the token cache re-check validity after acquiring the lock?
- [ ] Because asyncio.Lock does not guarantee mutual exclusion
- [x] Because another coroutine may have refreshed the token while this one waited for the lock
- [ ] Because the clock may have moved backwards during the await
- [ ] Because the fast-path check is not allowed to read private attributes
> Several coroutines can miss on the fast path at once; they queue on the lock. The first refreshes,
> and without the second check every waiter behind it would refresh again on entry.

### Q: A semaphore of 10 wraps an API with 1-second latency. Latency improves to 0.25 seconds. What happens to request rate?
- [ ] It stays at 10 requests per second because the semaphore is unchanged
- [x] It rises to about 40 requests per second because the semaphore bounds concurrency, not rate
- [ ] It falls, because shorter requests release the semaphore less often
- [ ] It becomes unbounded because the semaphore stops blocking
> Throughput equals concurrency divided by latency. Ten slots each finishing in 0.25 seconds allow
> about 40 requests per second, which is why semaphores cannot enforce per-minute quotas.
