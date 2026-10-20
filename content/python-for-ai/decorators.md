---
id: decorators
module: python-for-ai
name: Decorators
kind: concept
difficulty: core
est_minutes: 15
related:
  - python-for-ai/tenacity
  - python-for-ai/async-await
  - python-for-ai/context-managers-resource-cleanup
  - python-for-ai/logging-structlog
  - python-for-ai/mcp-python-sdk
  - agents/tool-calling
  - observability/tracing
  - fastapi-for-ai/dependency-injection
sources:
  - PEP 318 — Decorators for Functions and Methods
  - PEP 3129 — Class Decorators
  - Official docs: functools (wraps, cache, lru_cache)
---

## TL;DR
A decorator is a callable that takes a function and returns a replacement, applied with `@name` above the
definition. It exists so cross-cutting behaviour (retries, timing, tracing, caching, registering a function as
an LLM tool or an HTTP route) is written once and attached declaratively instead of copied into every function.
The sharp edges are metadata loss without `functools.wraps`, wrapping async functions with sync wrappers, and
stacking order.

## Why it exists
An AI service has dozens of functions that call models, tools and stores, and almost all of them need the
same surrounding behaviour: retry on 429 and timeouts with backoff, record latency and token counts,
open a trace span, check permissions, cache identical calls. Written inline, every function starts with the
same fifteen lines of try/except/sleep/log, the copies drift apart, and one forgotten retry is an outage.

The naive alternatives are worse. Helper calls at the top and bottom of each function (`start = timer();
...; log(start)`) still repeat in every function, and they miss early returns and exceptions unless each
is wrapped in `try/finally`. Base-class inheritance forces unrelated functions into class hierarchies.
Higher-order wrapping by hand, `fetch = with_retry(fetch)`, works, but it is separated from the definition,
easy to forget and hard to see.

Python functions are first-class objects, so a function can take another function and return a new one
that adds behaviour around the call. The `@` syntax (PEP 318) puts that wrapping on the definition
itself, where readers see it. Frameworks lean on the same mechanism for registration: `@app.get("/chat")`
in FastAPI, `@mcp.tool()` in the MCP SDK and the tool decorators in agent frameworks record your function
and read its signature and docstring to build a schema.

## How it works
### Desugaring
```
@timed
def embed(texts): ...
```
is exactly `embed = timed(embed)`, executed once, at definition time (usually import time). `timed`
typically defines an inner `wrapper(*args, **kwargs)` that does work before and after calling the
original, and returns `wrapper`. The wrapper is a closure: it keeps a reference to the original function
in its enclosing scope, which is how it can call it later.

### Preserve identity with functools.wraps
The returned wrapper has its own `__name__`, `__doc__` and signature, so without care every decorated
function looks like `wrapper(*args, **kwargs)` to logs, debuggers and, critically, to frameworks that
build tool or API schemas from the signature and docstring. `@functools.wraps(fn)` copies the name,
docstring, module and annotations, and sets `__wrapped__` so `inspect.signature` reports the original
signature.

### Decorators with arguments are factories
`@retry(attempts=3)` calls `retry(attempts=3)` first, which returns the real decorator, which then
receives the function. That is three levels of nesting: factory, decorator, wrapper. Class decorators
(PEP 3129) work the same way on classes; `@dataclass` is one.

### Async functions need async wrappers
Calling an `async def` function returns a coroutine without running it. A plain sync wrapper around it
measures how long it took to *create* the coroutine (microseconds) and its `try/except` never sees the
real errors, which happen later at `await`. A decorator meant for both must check
`inspect.iscoroutinefunction(fn)` and return an `async def` wrapper that awaits the call.

### Stacking order
```
@traced
@retry(attempts=3)
def call(): ...
```
equals `call = traced(retry(attempts=3)(call))`. Decorators apply bottom-up, so at call time the
outermost one, `traced`, runs first: here one span covers all three attempts. Swap them and each attempt
gets its own span. Order is a design decision, not style.

## Example
```python
import asyncio, functools, inspect, random, time

class Transient(Exception): ...

def retry(attempts: int = 3, base_delay: float = 0.5):
    def decorator(fn):
        if not inspect.iscoroutinefunction(fn):
            raise TypeError("retry() here supports async functions only")

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            for attempt in range(1, attempts + 1):
                start = time.perf_counter()
                try:
                    return await fn(*args, **kwargs)
                except Transient as exc:
                    ms = (time.perf_counter() - start) * 1000
                    print(f"{fn.__name__} attempt {attempt} failed after {ms:.0f} ms: {exc}")
                    if attempt == attempts:
                        raise
                    delay = base_delay * 2 ** (attempt - 1)
                    await asyncio.sleep(delay * random.uniform(0.5, 1.5))  # jitter
        return wrapper
    return decorator

@retry(attempts=4, base_delay=0.1)
async def complete(prompt: str) -> str:
    """Call the model provider."""
    await asyncio.sleep(0.05)
    if random.random() < 0.5:
        raise Transient("429 rate limited")
    return f"answer to {prompt!r}"

print(complete.__name__, complete.__doc__)     # complete Call the model provider.
print(asyncio.run(complete("hi")))
```
With 4 attempts and a 0.1 s base delay, the waits between failures are about 0.1, 0.2 and 0.4 s with
±50% jitter, so a call that never succeeds gives up after roughly 0.7 s of backoff plus four call
durations. In production you would usually use `tenacity` rather than hand-roll this.

## Trade-offs
- Decorators remove duplicated cross-cutting code and make it visible at the definition, but hide control
  flow; when behaviour depends on per-call context, an explicit wrapper or context manager is clearer.
- Registration decorators make tool and route definitions concise, but run at import time, so import
  order and module side effects start to matter; that bites in large packages and test setups.
- `functools.lru_cache` and `cache` give instant memoisation, but key on arguments and live for the
  process; they are wrong for unhashable inputs, per-user data or anything that must expire.
- Generic decorators for both sync and async functions are convenient, at the cost of branching and
  harder typing; separate decorators are simpler when a codebase is all-async.

## Pitfalls
- Omitting `functools.wraps`; the symptom is every tool in an agent's schema named `wrapper` with no
  description, or FastAPI dependencies resolving the wrong parameters.
- Wrapping an `async def` with a sync wrapper; latency metrics show microseconds and retries never fire,
  because the error only appears when the coroutine is awaited elsewhere.
- Applying `lru_cache` or `cache` to a method; the cache holds a reference to `self` in every key, so
  instances stay alive while cached (forever under `cache`), which shows up as steadily growing memory.
- Stacking retry outside a timeout when you meant per-attempt timeouts, or the reverse; traces show either
  one timeout killing all retries or retries continuing far past the deadline.
- Doing expensive work in the decorator body rather than in the wrapper; it runs once at import, which
  slows startup or freezes a value that should be read per call.

## When to use
Use a decorator when the same behaviour wraps many functions and does not depend on their internals:
retries, timeouts, tracing spans, metrics, auth checks, caching, and registration with a framework. Prefer
established libraries (tenacity for retries, OpenTelemetry or your tracing SDK's decorator, `functools.cache`)
over hand-rolled versions. Avoid decorators for logic that varies per call or that the reader must see to
understand the function. When reviewing code, check that every wrapper uses `wraps`, handles async
correctly and is stacked in the intended order. In interviews, expect to write a parameterised decorator
and explain the desugaring.

## Role lens
- **Engineer:** Writes async-aware decorators with `functools.wraps` for retries and tracing, and uses
  framework decorators to register tools and routes.
- **Architect:** Standardises the cross-cutting layer, one retry policy and one tracing decorator, so
  every model and tool call is observable and resilient by default.
- **FDE:** Adds tracing or retry decorators to a customer's existing functions without rewriting them,
  quickly instrumenting a pilot to find where latency goes.

## Check yourself
**Q:** A team adds `@timed` (sync wrapper, no `wraps`) to their async tool functions and registers them with an
agent framework. Latency dashboards show 0.01 ms per tool and the model starts calling tools with wrong
arguments. Explain both symptoms.
**A:** The sync wrapper calls the async function, which only creates a coroutine and returns immediately, so
the timer measures coroutine creation, not the awaited work; the real latency happens later when the
framework awaits the returned coroutine. Without `functools.wraps`, the wrapper exposes its own name,
missing docstring and `(*args, **kwargs)` signature, so the framework generates tool schemas with no
parameter names or descriptions, and the model guesses the arguments. The fix is an `async def` wrapper
that awaits the call, decorated with `functools.wraps(fn)` so name, docstring and signature survive.

## Quiz
### Q: What is `@a` stacked above `@b` above `def f` equivalent to?
- [ ] `f = b(a(f))`, so `b` runs first on each call
- [x] `f = a(b(f))`, so `a`'s wrapper runs first on each call
- [ ] `f = a(f); f = b(f)` with `b` outermost
- [ ] It depends on which decorator was imported first
> Decorators apply bottom-up at definition time, so the top decorator wraps everything below it and
> its wrapper is entered first at call time. That determines, for example, whether a span covers all retries.

### Q: Why does an agent framework that reads function signatures need decorated tools to use `functools.wraps`?
- [ ] Because `wraps` makes the function run faster
- [ ] Because frameworks reject any function that has been decorated
- [x] Because otherwise the framework sees the wrapper's generic name, docstring and `*args, **kwargs` signature
- [ ] Because `wraps` converts the function into a coroutine automatically
> Tool schemas are built from name, docstring and parameters. `wraps` copies metadata and sets
> `__wrapped__`, so `inspect.signature` reports the original parameters instead of the wrapper's.

### Q: A sync timing decorator wraps an `async def` model call. What does it actually measure?
- [ ] The full network round-trip of the model call
- [ ] Nothing, because Python refuses to decorate coroutines
- [ ] The time until the first token arrives
- [x] Only the time to create the coroutine object, since the call runs later when it is awaited
> Calling an async function returns a coroutine without executing its body. Timing, retries and
> exception handling must happen in an async wrapper that awaits the call.
