---
id: exceptions-and-custom-exception-hierarchies
module: python-for-ai
name: Exceptions and custom exception hierarchies
kind: concept
difficulty: core
est_minutes: 16
related:
  - python-for-ai/tenacity
  - python-for-ai/async-cancellation-and-exception-propagation
  - python-for-ai/logging-structlog
  - python-for-ai/context-managers-resource-cleanup
  - serving-inference/retry-strategies
  - fastapi-for-ai/httpexception-handling
  - system-design/model-fallback-strategies
sources:
  - Yee, 2005 — PEP 3134: Exception Chaining and Embedded Tracebacks
  - Katriel, Selivanov, van Rossum, 2021 — PEP 654: Exception Groups and except*
  - Official docs: Built-in Exceptions
---

## TL;DR
Exceptions carry a failure up the call stack until something that knows what to do catches it. A
custom hierarchy, rooted in one base class per package with branches for retryable versus
permanent failures, lets callers catch by *meaning* instead of by vendor class or message text.
It exists because LLM systems fail constantly and differently (rate limits, timeouts, context
overflow, invalid output), and the response to each must be chosen by code, not guessed from a
string.

## Why it exists
A call to a model provider can fail in a dozen ways: the network dropped, the provider returned
429, the prompt exceeded the context window, the JSON came back malformed, the API key expired. The
correct reaction differs for each: retry with backoff, wait for `retry-after`, truncate and retry,
ask the model to repair its output, stop and page a human. Code that returns `None` or an error
string on failure forces every caller to inspect the result, forgets to half the time, and loses
the stack trace entirely. Exceptions solve the propagation problem: the failure travels upward,
running cleanup (`finally`, context managers) along the way, until a frame that can act on it does.

The second problem is classification. Catching the vendor's exception classes directly couples
every call site to one SDK; swapping providers or adding a fallback means touching all of them.
Catching `Exception` and matching on the message is worse: messages change between SDK versions and
the same text means different things from different providers. The retry loop ends up retrying
authentication failures and giving up on transient ones.

A custom hierarchy fixes both. The adapter that talks to the SDK translates vendor failures into
your own classes with structured fields (`retryable`, `status`, `retry_after`, `request_id`) at the
one place that knows the vendor. Everything above catches `TransientLLMError` or `LLMError` and
never sees a vendor name. The hierarchy is the vocabulary your retry policy, fallback logic and
dashboards share.

## How it works
### Propagation and the four clauses
`raise` creates an exception object and starts unwinding: each frame is abandoned in turn, running
any `finally` block and every context manager's `__exit__`, until a `try` with a matching `except`
is found. `except` matches by `isinstance`, so catching a base class catches every subclass; this
is *why* hierarchies work and why the order of `except` clauses matters (a base class listed first
shadows its subclasses). `else` runs only when no exception occurred and keeps the success path out
of the `try` body, so a bug in it is not mistaken for the failure you meant to catch. `finally`
always runs and is for cleanup, not for swallowing errors.

### The built-in tree
`BaseException` is the root. `Exception` is the branch for "something went wrong in the program".
`KeyboardInterrupt`, `SystemExit` and `GeneratorExit` sit *beside* `Exception`, not under it,
because they signal control flow, not errors, and `except Exception` must not intercept Ctrl-C or
interpreter shutdown. `asyncio.CancelledError` was moved to the same position in Python 3.8 for the
same reason: catching `Exception` inside a coroutine must not swallow a cancellation. A bare
`except:` catches `BaseException` and therefore everything, which is why it is almost always wrong.

### Chaining and context
Raising inside an `except` block records the original on the new exception. `raise New(...) from
e` sets `__cause__` (explicit: "this happened because of that"); raising without `from` sets
`__context__` (implicit: "this happened while handling that"). Tracebacks print both, so the
vendor's stack trace survives translation into your class. `raise ... from None` suppresses the
context when the original is noise. `e.add_note("request_id=...")` (3.11) appends text to the
traceback without subclassing, useful for attaching correlation IDs as the exception climbs.

### Exception groups
Concurrent code can fail in several ways at once. `ExceptionGroup` (3.11) bundles multiple
exceptions and `except*` matches against the group's members by type, so a `TaskGroup` with three
failed tasks reports all three instead of the first. Without this, parallel tool calls would lose
every failure but one.

### Designing the hierarchy
One root per package, `class LLMError(Exception)`, so callers can always say "anything from the LLM
layer". Branches by *what the caller should do*, not by what the vendor said: `TransientLLMError`
(retry), `ContextTooLong` (shrink the prompt), `OutputInvalid` (repair loop), `AuthError` (stop).
Structured attributes rather than parsing messages: a `retryable` flag, an optional `retry_after`,
the provider name and the request ID. Always call `super().__init__(message)` so `str(e)`, logging
and pickling (needed for multiprocessing) work. Translate at the adapter boundary and nowhere else;
`raise Ours(...) from vendor_exc` preserves the chain for debugging.

### Cost and style
`try` blocks are essentially free when no exception occurs (CPython 3.11 made the no-exception
path zero-cost); raising and unwinding is comparatively expensive, so exceptions are for
exceptional paths, not for per-item control flow in a hot loop. Python prefers EAFP, "easier to ask
forgiveness than permission", because checking first and then acting is racy and duplicates the
check the operation performs anyway. Log with `logger.exception()` inside the `except` so the
traceback is captured, and let the exception continue upward unless this frame genuinely resolves
it.

## Example
A small hierarchy with structured fields, an adapter that translates a vendor's exception while
preserving the chain, and a caller that branches on meaning.

```python
class LLMError(Exception):
    """Root of everything the LLM layer raises; code catches this, humans read the message."""
    retryable: bool = False
    def __init__(self, msg: str, *, provider: str, request_id: str | None = None) -> None:
        super().__init__(msg)                      # keeps str(e), logging and pickling working
        self.provider, self.request_id = provider, request_id

class TransientLLMError(LLMError):                 # timeouts, 5xx, connection resets
    retryable = True

class RateLimited(TransientLLMError):
    def __init__(self, msg: str, *, retry_after: float | None = None, **kw) -> None:
        super().__init__(msg, **kw); self.retry_after = retry_after

class ContextTooLong(LLMError):                    # retrying the same request cannot succeed
    pass

def translate(exc: Exception, provider: str) -> LLMError:
    """Adapter boundary: map one SDK's failures onto our vocabulary."""
    status = getattr(exc, "status_code", None)
    rid = getattr(exc, "request_id", None)
    if status == 429:
        return RateLimited(str(exc), provider=provider, request_id=rid,
                           retry_after=getattr(exc, "retry_after", None))
    if (status is not None and status >= 500) or type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}:
        return TransientLLMError(str(exc), provider=provider, request_id=rid)
    if status == 400 and "context" in str(exc).lower():
        return ContextTooLong(str(exc), provider=provider, request_id=rid)
    return LLMError(str(exc), provider=provider, request_id=rid)

def call(provider_fn, provider: str):
    try:
        return provider_fn()
    except Exception as e:                         # Exception, never BaseException: cancellation passes through
        raise translate(e, provider) from e        # 'from e' keeps the vendor traceback as __cause__

class FakeVendorError(Exception):                  # stands in for an SDK's status error
    status_code, request_id = 429, "req_123"

def failing():
    raise FakeVendorError("slow down")

try:
    call(failing, "fake")
except TransientLLMError as err:                   # catches RateLimited by inheritance
    print(type(err).__name__, err.retryable, err.provider, err.request_id, repr(err.__cause__))
    # RateLimited True fake req_123 FakeVendorError('slow down')
```

A retry decorator can now be written once as `retry_on=TransientLLMError` and never learn a vendor
class name; a `ContextTooLong` handler shrinks the prompt instead of retrying.

## Trade-offs
- A custom hierarchy decouples callers from vendor SDKs, but every new failure mode must be mapped
  in the adapter; an unmapped vendor error falls to the root class and loses its `retryable` signal.
- Catching broad classes (`LLMError`) keeps call sites simple, yet it hides which failure occurred
  unless the structured fields are logged; catch narrowly where the reaction differs.
- Chaining with `from e` preserves full diagnostics, but tracebacks grow long and may leak vendor
  internals into user-facing error messages; separate what is logged from what is shown.
- Exceptions make the happy path clean and propagate cleanup correctly, but on a hot per-item path
  (parsing a million rows) a result type or a pre-check can be cheaper than raising.
- Exception groups report every concurrent failure instead of the first, at the cost of handlers
  that must iterate a group rather than catch one exception.

## Pitfalls
- `except Exception: pass` or logging and continuing; the symptom is an agent that reports success
  with empty output, discovered hours later from a user complaint rather than an alert.
- Raising a new exception inside `except` without `from e`; the symptom is a traceback with a
  misleading "during handling of the above exception" line and no clear root cause.
- Classifying failures by substring (`if "rate limit" in str(e)`); the symptom is retries that stop
  working after an SDK upgrade changes the wording.
- Defining `__init__` with required keyword arguments but not calling `super().__init__`; the symptom
  is `TypeError` when the exception is pickled across a process pool or copied by a framework.
- Catching `BaseException` or using bare `except:` in async code; the symptom is tasks that refuse
  to cancel and shutdowns that hang because `CancelledError` was swallowed.
- Listing a base class before its subclass in a chain of `except` clauses; the specific handler is
  unreachable and the generic one runs, usually retrying something that should not be retried.

## When to use
Raise exceptions for failures the current function cannot resolve; return values for expected
outcomes such as "no results". Build one hierarchy per package or service and translate vendor
exceptions into it at the adapter, with `retryable` and `retry_after` as fields so the retry policy
is data, not string matching. Catch at the level that can act: the retry loop catches
`TransientLLMError`, the prompt builder catches `ContextTooLong`, the API layer catches `LLMError`
to produce a response, and nothing catches `Exception` silently. Measure by counting exceptions by
class in your logs; a rising root-class count means unmapped vendor failures. In interviews, expect
questions on why `CancelledError` is not an `Exception`, the difference between `__cause__` and
`__context__`, and how you would classify failures for retry.

## Role lens
- **Engineer:** Writes the adapter that maps SDK exceptions to the internal hierarchy and the retry
  decorator that keys on `retryable`, never on vendor class names.
- **Architect:** Owns the shared exception vocabulary across services so fallback, circuit-breaker
  and alerting logic agree on what "transient" means.
- **FDE:** Extends the hierarchy with the customer's own failure modes (their gateway's 503s, their
  auth proxy's timeouts) so the product's retry policy works inside their network.

## Check yourself
**Q:** A retry decorator wraps every LLM call and retries on any `Exception` up to five times with
backoff. Production shows calls that fail for a minute then succeed, and others that fail five times
and give up. What is wrong with this design and how would you restructure it?
**A:** Retrying on `Exception` treats every failure as transient. Authentication errors, context
overflow and invalid-output failures are retried uselessly, burning time and tokens, while the
backoff masks the real cause. Restructure around a hierarchy: an adapter translates vendor errors
into `TransientLLMError` (retry, honouring `retry_after`), `ContextTooLong` (shrink the prompt, do
not retry as-is), `OutputInvalid` (repair loop with the validation errors) and `AuthError` (fail
fast and alert). The decorator then retries only `TransientLLMError`, and the minute-long failures
become visible as rate limiting with a measurable `retry_after` rather than anonymous retries.

## Quiz
### Q: Why is `asyncio.CancelledError` a subclass of `BaseException` rather than `Exception`?
- [ ] Because cancellation can only happen on the main thread
- [x] So that `except Exception` handlers in coroutines do not accidentally swallow a cancellation
- [ ] Because it carries no message and therefore cannot be an `Exception`
- [ ] Because `BaseException` subclasses are faster to raise
> Cancellation is control flow, like `KeyboardInterrupt`. Placing it beside `Exception` means
> ordinary error handling lets it propagate, so tasks actually stop when cancelled.

### Q: What does `raise OurError("x") from vendor_exc` achieve that a plain `raise OurError("x")` inside an `except` does not?
- [ ] It suppresses the vendor exception entirely
- [ ] It retries the original operation before raising
- [x] It records `vendor_exc` as the explicit `__cause__`, so the traceback shows "the direct cause" with the vendor stack
- [ ] It makes `OurError` a subclass of the vendor exception at runtime
> Without `from`, the original is attached only as implicit `__context__` ("during handling of").
> `from` states causation explicitly and is the idiom for translating exceptions at a boundary.

### Q: Which design lets a retry policy decide what to retry without knowing any vendor SDK?
- [ ] Matching on the exception message text for "rate limit" or "timeout"
- [ ] Catching the vendor's `RateLimitError` directly in the retry decorator
- [x] Translating vendor errors at the adapter into an internal hierarchy with a `retryable` attribute
- [ ] Returning `None` from failed calls and retrying when the result is `None`
> The adapter is the only place that knows the vendor. Above it, the policy keys on the internal
> class and its structured fields, so swapping providers never touches the retry logic.
