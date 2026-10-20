---
id: exceptions-and-custom-exception-hierarchies
module: python-for-ai
name: Exceptions and custom exception hierarchies
kind: concept
difficulty: core
est_minutes: 15
related:
  - python-for-ai/tenacity
  - python-for-ai/async-cancellation-and-exception-propagation
  - python-for-ai/logging-structlog
  - python-for-ai/context-managers-resource-cleanup
  - serving-inference/retry-strategies
  - fastapi-for-ai/httpexception-handling
  - system-design/model-fallback-strategies
sources:
  - Official docs: Python Language Reference and Library Reference, Built-in Exceptions
  - Official docs: Python Tutorial, Errors and Exceptions
  - Katriel, Selivanov, van Rossum, 2021 — PEP 654: Exception Groups and except*
---

## TL;DR
Exceptions carry a failure up the call stack until something that knows what to do catches it. A
custom hierarchy organises failures by *how they should be handled* (retry, shrink the input,
re-prompt, give up) rather than by where they came from, so callers catch one base class or one
precise leaf and never string-match messages. It is what keeps a rate limit from being treated like
an invalid API key, and a bug in your own code from being retried at provider prices.

## Why it exists
An LLM call can fail a dozen different ways, and the correct response differs for each. A rate limit
wants a wait and a retry. A timeout wants a retry, maybe with a smaller request. A context-length
error wants the input shrunk and never a plain retry. Output that fails schema validation wants a
corrective re-prompt. An authentication error or content-policy refusal wants no retry at all and a
human. A `KeyError` in your own parsing code wants a bug fix, not a retry loop that quietly triples
your bill. If all of these arrive as "an exception", the handler can only pick one policy for all.

The naive alternatives each break. `except Exception: retry()` retries the unretryable, including
your own bugs, and hides them behind success-after-retry statistics. Return codes and `None`
results are ignored by the first caller that forgets to check, lose the stack trace, and spread
`if result is None` through every layer. Matching on message text (`if "rate limit" in str(e)`)
couples your logic to a vendor's wording and breaks on the next SDK release. Catching the vendor's
own exception classes everywhere couples the whole application to one provider, so switching or
adding a fallback model means touching every call site.

Custom hierarchies solve this because `except` matches by class *and all subclasses*. A tree whose
inner nodes are handling policies (`Transient`, `Permanent`) and whose leaves carry structured data
(`RateLimited.retry_after`, `ContextTooLong.limit`) lets each layer catch at the granularity it can act
on. Translating vendor exceptions into that tree once, at the adapter, keeps the rest of the code
vendor-neutral.

## How it works
### Raising, unwinding and catching
`raise X(...)` creates an exception instance, attaches a traceback, and unwinds the stack frame by
frame until a `try` block with a matching `except` is found. Matching uses `isinstance`, so
`except Transient` catches every subclass; handlers are tested in order, which is why a subclass
handler must appear before its base or it is unreachable. `else` runs when no exception occurred,
`finally` always runs, and a bare `raise` inside a handler re-raises the current exception with its
original traceback intact. Since Python 3.11 a `try` block costs nothing when no exception is raised;
raising and unwinding still costs, so exceptions are for failures, not for routine control flow in
hot loops.

### The built-in tree and what `except Exception` means
Everything derives from `BaseException`. `Exception` is the branch for errors a program may want to
recover from. `KeyboardInterrupt`, `SystemExit`, `GeneratorExit` and `asyncio.CancelledError` hang
directly off `BaseException` *so that* `except Exception` does not swallow them: a handler that
catches "any error" must not stop Ctrl-C, interpreter shutdown or task cancellation. Catching
`BaseException` or writing a bare `except:` breaks exactly those mechanisms.

### Designing the hierarchy
Start with one base per subsystem (`LLMError(Exception)`) so a caller can always say "anything from
the LLM layer". Under it, split by handling policy, not by source: `Transient` (safe to retry) versus
`Permanent` (do not retry), optionally `Recoverable` (retry after changing something). Leaves carry
the data the handler needs as attributes: `retry_after` seconds, token counts and limits, the list of
validation errors. Give leaves a clear `__str__` for logs. Keep the tree shallow; three levels is
usually enough, and every class must correspond to a different decision somewhere, otherwise it is
noise.

### Chaining: keep the cause
Raising a domain exception inside an `except` block links the original automatically as
`__context__`, and the traceback prints "During handling of the above exception, another exception
occurred", which misreads as a second bug. `raise Domain(...) from e` sets `__cause__` and prints
"The above exception was the direct cause", which is the honest story and keeps the vendor stack
trace for debugging. `from None` suppresses the context when the original is irrelevant noise.
`e.add_note("request_id=...")` (3.11) attaches context without changing the type.

### Translate at the boundary, decide at the top
Wrap vendor SDK or HTTP errors into your hierarchy in the one adapter that talks to the vendor. Let
exceptions propagate through the layers that cannot act on them; do not catch-log-reraise at every
level, which produces the same stack trace five times. Decide at the layer that owns the policy: the
retry loop catches `Transient`, the request handler maps `Permanent` to an HTTP status, the top-level
catches `LLMError` for the user-facing message. Keep `__init__` signatures compatible with pickling
(pass the constructor arguments to `super().__init__`) if exceptions cross process boundaries.

### Concurrent failures
When several tasks fail at once (an `asyncio.TaskGroup` fanning out tool calls), there is no single
exception to raise. `ExceptionGroup` (3.11) bundles them and `except* Transient` selects the matching
members while re-raising the rest, which keeps the retry/no-retry split correct even for parallel work.

## Example
A small hierarchy, translation of HTTP failures at the adapter, and a retry loop that only retries
what is marked retryable. The error-body shape is illustrative; map your provider's codes the same way.

```python
import time, httpx

class LLMError(Exception): ...
class Transient(LLMError): ...                      # safe to retry
class Permanent(LLMError): ...                      # do not retry

class RateLimited(Transient):
    def __init__(self, retry_after: float):
        super().__init__(retry_after)               # args == ctor args keeps it picklable
        self.retry_after = retry_after
    def __str__(self) -> str:
        return f"rate limited, retry after {self.retry_after:.1f}s"

class ContextTooLong(Permanent):
    def __init__(self, tokens: int, limit: int):
        super().__init__(tokens, limit)
        self.tokens, self.limit = tokens, limit

def call_provider(client: httpx.Client, prompt: str) -> str:
    try:
        r = client.post("/v1/complete", json={"prompt": prompt}, timeout=30)
        r.raise_for_status()
    except httpx.TimeoutException as e:
        raise Transient("provider timeout") from e  # keep the cause for logs
    except httpx.HTTPStatusError as e:
        status, body = e.response.status_code, e.response.json()
        if status == 429:
            raise RateLimited(float(e.response.headers.get("retry-after", 2))) from e
        if status >= 500:
            raise Transient(f"provider returned {status}") from e
        if body.get("error", {}).get("code") == "context_length_exceeded":
            raise ContextTooLong(body["error"]["tokens"], body["error"]["limit"]) from e
        raise Permanent(f"provider returned {status}: {body}") from e   # 400/401/403: stop
    return r.json()["text"]

def answer(client: httpx.Client, prompt: str, attempts: int = 3) -> str:
    for n in range(attempts):
        try:
            return call_provider(client, prompt)
        except RateLimited as e:                    # subclass handler before its base
            time.sleep(e.retry_after)
        except Transient:
            time.sleep(2 ** n)
        except ContextTooLong as e:                 # recoverable: shrink, then try again
            prompt = prompt[: len(prompt) * e.limit // (2 * e.tokens)]
    raise Permanent(f"gave up after {attempts} attempts")
```

## Trade-offs
- A policy-based tree lets callers catch exactly what they can handle, but every class must map to a
  distinct decision; a hierarchy mirroring the vendor's twenty error codes is coupling in disguise.
- Translating at the adapter keeps the application vendor-neutral, at the cost of a mapping that
  must be maintained as providers add codes; unmapped errors should land in `Permanent`, not vanish.
- Carrying structured data on exceptions (`retry_after`, `limit`) makes handlers precise, but
  non-trivial constructors must stay pickle-compatible if the exception ever crosses a process pool.
- Exception groups model parallel failures faithfully, but `except*` semantics (partial matching,
  re-raising the remainder) are harder to read than a sequential loop; use them only where fan-out
  genuinely happens.

## Pitfalls
- `except Exception:` around the provider call with a retry; the symptom is a `KeyError` in your own
  parser appearing in logs five times, each with a paid request behind it.
- A bare `except:` or `except BaseException:`; the symptom is Ctrl-C being ignored and async tasks
  that refuse to cancel, because `CancelledError` and `KeyboardInterrupt` were swallowed.
- Listing `except Transient` before `except RateLimited`; the leaf handler is unreachable and the
  `retry_after` hint is ignored, so you hammer a rate-limited endpoint with exponential backoff instead.
- Raising a domain error without `from e`; the traceback says "during handling of the above
  exception, another exception occurred", and the on-call engineer chases a phantom second bug.
- Matching on `str(e)` from an SDK; it works until a minor SDK release rewords the message and every
  rate limit becomes a permanent failure overnight.
- Logging with `logger.exception` at every layer and re-raising; the symptom is one failure producing
  a dozen stack traces, which trains people to ignore logs.
- A custom `__init__` that takes keyword-only arguments but passes a formatted string to `super()`;
  the symptom is `TypeError` when a worker process tries to unpickle the exception.

## When to use
Introduce a hierarchy as soon as a subsystem has more than one failure policy, which for anything
that calls a model or an external API is immediately. Keep it to one base plus policy branches plus
data-carrying leaves; add a class only when a handler would treat it differently. Translate at the
adapter, propagate through the middle, decide at the top. Measure by counting retries per error type
and by checking that no handler in the codebase catches `Exception` around a paid call. In
day-to-day work this is the shape of every provider client wrapper; in interviews, the question is
"how do you decide what to retry", and the answer is "by exception class, with the mapping done once
where the vendor error enters the system".

## Role lens
- **Engineer:** Writes the adapter that maps SDK and HTTP errors into `Transient`/`Permanent` leaves
  and the retry loop that catches only `Transient`, with backoff hints read from the exception.
- **Architect:** Owns the shared hierarchy across services so retry, fallback and circuit-breaker
  policies key off the same classes, and decides which failures reach the user as which status.
- **FDE:** Extends the mapping for a customer's gateway or proxy that rewrites provider errors, so a
  429 hidden inside a 502 is still classified as transient.

## Check yourself
**Q:** A colleague wraps every provider call in `except Exception as e: log(e); retry()` with three
attempts and reports that reliability improved. What is wrong with that picture, and how would you
restructure it?
**A:** The improvement is partly fake: transient failures were already retryable, while the policy
also retries permanent ones (bad API key, content refusal, context overflow) and your own bugs,
tripling cost and latency on exactly the calls that cannot succeed, and hiding `KeyError`-style bugs
behind eventual success or a generic failure. The log line at that layer also duplicates whatever the
top level logs. Restructure with a small hierarchy under `LLMError`: `Transient` for timeouts, 429s
and 5xx with a `retry_after` attribute, `Permanent` for 4xx auth and policy errors, and a recoverable
`ContextTooLong` carrying the limit. Translate vendor exceptions into it once in the adapter with
`raise ... from e`, retry only `Transient`, shrink on `ContextTooLong`, and let everything else
propagate to a single top-level handler that logs once with the full chain and maps to a status.

## Quiz
### Q: Why does `except Exception:` not catch `KeyboardInterrupt` or `asyncio.CancelledError`?
- [ ] Those are warnings, not exceptions, and are handled by the `warnings` module
- [x] They derive from `BaseException` directly, so a handler for recoverable errors cannot accidentally block shutdown or cancellation
- [ ] They are raised in a different thread than the handler
- [ ] They are only catchable with `except*` because they arrive as exception groups
> The split between `BaseException` and `Exception` is deliberate: "any error I might recover from"
> must not include interrupts and cancellations. Catching `BaseException` or using a bare `except`
> does swallow them and breaks Ctrl-C and task cancellation.

### Q: What does `raise ContextTooLong(...) from e` change compared with a plain `raise ContextTooLong(...)` inside an `except` block?
- [ ] It discards the original exception to keep the traceback short
- [ ] It converts `e` into an instance of `ContextTooLong`
- [x] It sets `__cause__` so the traceback shows the original as the direct cause instead of an unrelated error that occurred during handling
- [ ] It makes the new exception retryable
> Both forms keep the original reachable, but implicit chaining prints "during handling ... another
> exception occurred", which reads as two bugs. Explicit `from e` records intent and keeps the vendor
> trace for debugging.

### Q: Which principle should organise a custom exception tree for an LLM client?
- [ ] One class per vendor error code so nothing is lost
- [ ] One class per module that can raise, mirroring the package layout
- [x] Branches by handling policy (retry, recover, stop) with leaves that carry the data handlers need
- [ ] A flat list of classes all inheriting directly from `Exception`
> Handlers catch by class, so the tree should let a retry loop say `except Transient` and a request
> handler say `except Permanent`. Mirroring vendor codes or modules forces every caller to know
> details that belong in the adapter.
