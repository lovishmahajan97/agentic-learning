---
id: async-cancellation-and-exception-propagation
module: python-for-ai
name: Async cancellation & exception propagation
kind: concept
difficulty: advanced
est_minutes: 18
status: fact-checked
related:
  - python-for-ai/asyncio
  - python-for-ai/async-await
  - python-for-ai/context-managers-resource-cleanup
  - python-for-ai/exceptions-and-custom-exception-hierarchies
  - serving-inference/cancellation
  - serving-inference/timeouts
  - fastapi-for-ai/graceful-shutdown-and-worker-draining
  - agents/parallel-tool-calls
sources:
  - Official docs: asyncio (Coroutines and Tasks, Task Groups, Timeouts)
  - PEP 654 — Exception Groups and except*
---

## TL;DR
Cancelling an asyncio task does not kill it: it schedules a `CancelledError` to be raised at the
task's next `await`, so the task can run its `finally` blocks and release connections. Errors in
concurrent children must also travel somewhere. `TaskGroup` and `asyncio.timeout` build on this one
mechanism, which is why swallowing `CancelledError` silently breaks deadlines, sibling cleanup and
shutdown.

## Why it exists
An agent turn fans out: three tool calls and a retrieval query run concurrently, and the user then
closes the browser tab, or the 30-second deadline passes, or one tool returns a fatal error that
makes the others pointless. In every case the remaining work must stop, and stopping it badly has
real costs: an LLM stream that keeps generating (and, depending on the provider, keeps billing), a
connection that never goes back to the pool, a half-written row in a checkpoint store.

The naive alternatives fail. Killing a thread or coroutine from outside at an arbitrary instruction
would leave locks held and sockets half-written, which is why Python offers no safe way to kill a
thread. Polling a `stop` flag only works if every piece of code checks it, and library code you call
never will. Ignoring errors in background tasks is worse: a task that raised but was never awaited
loses its exception until garbage collection logs "Task exception was never retrieved", long after
the request returned a wrong answer.

asyncio's answer is cooperative cancellation delivered as an exception. Because a coroutine can only
be suspended at an `await`, that is the only place it can be interrupted, so cancellation arrives at a
known point and travels up the stack through ordinary `try/finally` and `async with` cleanup. Error
propagation then needs rules for what happens to siblings when one child fails, which is what
structured concurrency (`TaskGroup`) supplies.

## How it works
### Cancellation is a request, delivered at an await
`task.cancel()` sets a flag and, when the task is next resumed, throws `CancelledError` into it at
the `await` where it is suspended. Code between awaits runs to completion; a CPU loop with no await
cannot be cancelled at all. The exception unwinds the coroutine like any other: `finally` blocks run,
`async with` exits run (the `httpx` client closes the stream, the lock is released). Since Python 3.8
`CancelledError` subclasses `BaseException`, not `Exception`, *because* the common
`except Exception:` retry or logging block used to catch it by accident and keep the task alive.

The contract: you may catch `CancelledError` to clean up or log, but you must re-raise it. A task
that swallows it reports as completed, and whoever cancelled it (a timeout, a TaskGroup, the server
shutting down) believes the cancellation failed or never happened.

### Timeouts are cancellation plus translation
`asyncio.timeout(delay)` (3.11+) schedules a cancel of the current task at the deadline. When the
resulting `CancelledError` reaches the `async with` exit, the context manager recognises it as its own
and converts it into `TimeoutError`. The older `asyncio.wait_for` is built on `asyncio.timeout` since
3.12; before that it ran the awaitable as a separate inner task and cancelled that task instead, with
the same cancel-then-translate shape. That is why a swallowed
cancellation inside a timeout block makes the deadline silently not fire. Python 3.11 added
`Task.cancelling()` and `Task.uncancel()` so these constructs can tell their own cancellation apart
from one requested from outside.

### Propagation: gather versus TaskGroup
`asyncio.gather(*aws)` with the default `return_exceptions=False` propagates the *first* exception
to the awaiting caller, but does not cancel the other children: they keep running, orphaned. With
`return_exceptions=True` it returns exceptions as list items, so you must inspect every result.
Cancelling the gather itself does cancel all children.

`asyncio.TaskGroup` (3.11+) implements structured concurrency: no child outlives the `async with`
block. When any child raises a non-cancellation exception, the group cancels all remaining children,
waits for them to finish unwinding, then raises an `ExceptionGroup` holding every child failure.
You handle those with `except*`, which matches by type inside the group, *because* concurrent code
can genuinely fail in several ways at once and a single exception would hide the others.

### Shielding
`asyncio.shield(aw)` protects an inner awaitable from a cancellation aimed at the outer task. The
outer `await` still raises `CancelledError`; the inner work continues. Use it narrowly, for work that
must complete even when the requester is gone, such as committing an audit record.

## Example
```python
import asyncio

async def call(name: str, delay: float, fail: bool = False) -> str:
    try:
        await asyncio.sleep(delay)            # stands in for an HTTP call
        if fail:
            raise ValueError(f"{name}: malformed tool output")
        return f"{name}: ok"
    except asyncio.CancelledError:
        print(f"{name}: cancelled, closing stream")
        raise                                 # re-raise, always
    finally:
        print(f"{name}: connection released")

async def agent_turn() -> None:
    try:
        async with asyncio.timeout(2.0):      # overall turn deadline
            async with asyncio.TaskGroup() as tg:
                tg.create_task(call("retriever", 0.5))
                tg.create_task(call("calculator", 0.2, fail=True))
                tg.create_task(call("llm", 5.0))
    except* ValueError as eg:
        for exc in eg.exceptions:
            print("tool failed:", exc)
    except* TimeoutError:
        print("turn deadline exceeded")

asyncio.run(agent_turn())
```
At 0.2 s the calculator raises. The TaskGroup cancels the retriever and the LLM call; each prints
"cancelled" and "connection released" as `CancelledError` unwinds it; then the group raises an
`ExceptionGroup` that `except* ValueError` handles. Change `fail=True` to `False` and the 2-second
timeout fires instead: the timeout cancels `agent_turn`, the group passes that cancellation to its
children, the LLM call unwinds, and the caller sees `TimeoutError`. Deleting the `raise` in the child
would not break this particular run, because the timeout belongs to `agent_turn`, not the child; but
the cancelled child would then report success, and the same swallow in the task that owns a timeout
makes the block exit normally so no `TimeoutError` is ever raised.

## Trade-offs
- TaskGroup's fail-fast semantics stop wasted work and guarantee no orphans, but one flaky optional
  tool cancels the whole fan-out; when partial results are acceptable, catch errors inside each child.
- `gather(return_exceptions=True)` keeps every result and never cancels siblings, at the cost of
  manual inspection; it flips to the better choice when children are independent and best-effort.
- Cooperative cancellation keeps cleanup safe and predictable, but it cannot interrupt CPU-bound or
  blocking code; such work must run in a thread or process where cancellation only abandons the wait.
- `shield` guarantees critical work finishes, but that work then outlives the request and can pile
  up during an incident; it is only safe when the shielded work is short and bounded.

## Pitfalls
- A bare `except:` or `except BaseException:` without re-raise (or `except Exception` on Python 3.7
  and earlier) swallows cancellation; the symptom is timeouts that never fire and shutdowns that hang.
- Fire-and-forget `asyncio.create_task(...)` without keeping a reference or awaiting it; the symptom
  is "Task exception was never retrieved" in logs and occasionally tasks garbage-collected mid-flight.
- Using `gather` and assuming a failure stops the other calls; the symptom is token spend and tool
  side effects continuing after the request already returned an error.
- Awaiting inside `finally` during cancellation without a bound; a slow cleanup call can itself hang,
  so shutdown stalls until the process manager kills it.
- Expecting cancel to stop a blocking `requests.get` or a tight loop; the symptom is a task that
  stays "cancelling" until the blocking call returns on its own.

## When to use
You meet this whenever code starts more than one task or has a deadline, which in an AI backend is
almost always: per-request timeouts on model calls, client disconnects on streaming endpoints,
parallel tool calls, and graceful shutdown. Default to `TaskGroup` for fan-out where all results
matter and `asyncio.timeout` for deadlines; use `gather(return_exceptions=True)` for best-effort
batches. Every coroutine that holds a resource should release it in `finally` or `async with`. Test
it: write a test that cancels mid-call and asserts the connection pool and locks are back to idle.
In interviews, expect to explain why `CancelledError` is a `BaseException` and how TaskGroup differs
from gather when one child fails.

## Role lens
- **Engineer:** Writes children that re-raise `CancelledError`, wraps fan-out in `TaskGroup`, and
  tests that a client disconnect actually closes the upstream LLM stream.
- **Architect:** Sets the deadline budget across layers so inner timeouts are shorter than outer
  ones, and decides which side effects are shielded versus abandoned on cancel.
- **FDE:** Diagnoses a customer's hanging worker or runaway token bill by finding the swallowed
  cancellation or orphaned gather in their integration code.

## Check yourself
**Q:** An agent runs five tool calls with `asyncio.gather`. One raises; the endpoint returns 500, yet
logs show the other four tools finishing later and one writing to a CRM. Explain and fix.
**A:** `gather` without `return_exceptions` propagates the first exception to the caller but does not
cancel the remaining children, so they keep running as orphans and complete their side effects after
the response was sent. Switching to `asyncio.TaskGroup` cancels siblings when one fails and waits for
them to unwind before raising an `ExceptionGroup`, so nothing outlives the request. Each tool must
re-raise `CancelledError` and release resources in `finally`. Because cancellation only lands at an
await, a CRM write already sent cannot be undone, so consequential tools also need idempotency keys
or a compensating action.

## Quiz
### Q: Why does `CancelledError` inherit from `BaseException` rather than `Exception` in modern Python?
- [ ] So that it cannot be caught by any handler at all
- [x] So generic `except Exception` blocks for retries and logging do not swallow cancellation by accident
- [ ] Because it is raised by the interpreter rather than by user code
- [ ] So that it propagates across threads as well as tasks
> Broad `except Exception` handlers are common in retry and logging code; when cancellation was an
> `Exception` they absorbed it and kept the task alive. It can still be caught explicitly, and must then be re-raised.

### Q: A child inside `asyncio.TaskGroup` raises `ValueError` while two siblings are awaiting network calls. What happens?
- [ ] The ValueError propagates immediately and the siblings continue in the background
- [ ] The siblings are killed instantly without running their finally blocks
- [x] The siblings are cancelled, the group waits for them to unwind, then raises an ExceptionGroup
- [ ] The group retries the failed child once before failing
> TaskGroup guarantees that no child outlives the block: it cancels the others, waits for their cleanup
> to run, and reports all failures together. The first option describes `gather`, not TaskGroup.

### Q: A coroutine running `async with asyncio.timeout(5):` catches `CancelledError` and returns `None`. What is the effect?
- [ ] The timeout raises TimeoutError as normal after cleanup
- [ ] The event loop logs a warning and re-raises automatically
- [ ] Nothing changes, because timeouts do not use cancellation
- [x] The block exits normally, so the expected TimeoutError is never raised and the deadline is silently lost
> `asyncio.timeout` works by cancelling the task and converting the arriving `CancelledError` into
> `TimeoutError` at block exit; swallowing the exception means there is nothing to convert.
