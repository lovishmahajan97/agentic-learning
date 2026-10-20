---
id: promises-and-async-error-propagation
module: node-ts-for-ai
name: Promises & async error propagation
kind: concept
difficulty: core
est_minutes: 14
related:
  - node-ts-for-ai/event-loop-and-microtask-queue
  - node-ts-for-ai/abortcontroller-and-cancellation
  - node-ts-for-ai/api-clients-with-retries-and-circuit-breakers
  - python-for-ai/async-cancellation-and-exception-propagation
  - python-for-ai/exceptions-and-custom-exception-hierarchies
  - serving-inference/retry-strategies
sources:
  - Official docs: Node.js — process 'unhandledRejection' event and --unhandled-rejections
  - Official docs: MDN — Promise, Promise.allSettled, Promise.any, Error cause
---

## TL;DR
A promise is a one-shot result that is either fulfilled or rejected, and `await` turns a rejection back
into a thrown exception at the awaiting site, so errors travel up the async call chain just like
synchronous ones. The failure modes are promises nobody awaits (rejections vanish or crash the process),
combinators that hide which call failed, and error objects that lose their cause. Getting this right is
what makes a multi-provider LLM call fail loudly, precisely and recoverably.

## Why it exists
Callback-style Node passed errors as the first argument of every callback, and the result was error
handling that was easy to forget and impossible to compose: a forgotten `if (err)` meant silent
corruption, and sequencing five dependent I/O calls meant five levels of nesting with five separate
error paths. Promises fixed the composition problem by making the result a value: a chain of `.then`
calls has one `.catch` at the end, and a rejection anywhere skips straight to it. `async`/`await`
then made the control flow readable again, with `try`/`catch` working across awaits.

What promises did not fix, and what still bites AI code specifically, is the *ownership* of a failure.
An agent loop makes dozens of concurrent calls: the model, three tool executions, an embedding lookup,
a logging sink. Each returns a promise. If one is started and never awaited, its rejection has no
handler; modern Node treats that as fatal and exits the process, taking every other in-flight request
with it. If several are awaited through `Promise.all`, the first rejection wins and the others keep
running unobserved. If a low-level `fetch` error is caught and rethrown as `new Error("model call
failed")`, the status code and the provider's error body are gone by the time it reaches a log. The
discipline in this lesson exists because, in a system whose failures are dominated by upstream
timeouts, rate limits and malformed outputs, the difference between a retryable 429 and a permanent
400 must survive the trip up the call stack.

## How it works
### States and chaining
A promise is pending, then settles exactly once as fulfilled (with a value) or rejected (with a reason,
conventionally an `Error`). `.then(onFulfilled, onRejected)` returns a *new* promise whose outcome is
whatever the handler returns or throws; a returned promise is adopted (flattened). This is why a chain
propagates: a throw in any step rejects the step's promise, every following `.then` without a rejection
handler passes the rejection through unchanged, and the first `.catch` receives it. Handlers always run
as microtasks, never synchronously, even when the promise is already settled, which keeps ordering
consistent (see the event-loop lesson).

### What `async`/`await` desugars to
An `async` function always returns a promise. `return x` fulfils it; a thrown exception, including one
thrown synchronously before the first `await`, rejects it rather than escaping to the caller. `await p`
suspends the function, and when `p` rejects, the rejection is re-thrown at the `await` expression, so
an enclosing `try`/`catch` catches it. This is the single rule that makes propagation work: errors cross
`await` boundaries as exceptions, so a stack of async functions behaves like a stack of sync ones.

One subtlety: `return fetchModel()` inside a `try` block does **not** catch a rejection of `fetchModel()`
because the promise is returned before it settles; `return await fetchModel()` does. Outside a `try`,
the two are equivalent apart from a stack frame.

### Unhandled rejections
A rejected promise with no handler attached by the end of the current microtask drain triggers
`'unhandledRejection'`. Since Node 15 the default mode is `throw`: the rejection is raised as an uncaught
exception and the process exits. The usual cause is a *floating promise*: `logToSink(event)` called
without `await` or `.catch`, or a `.then` with no rejection branch. Fix the cause, not the symptom:
every promise must be awaited, returned, passed to a combinator, or given an explicit `.catch` that
decides what to do. Lint for it (`@typescript-eslint/no-floating-promises`). Installing a global
`process.on('unhandledRejection')` handler that only logs turns a crash into silent data loss; it is
acceptable as a last-resort reporter, not as a strategy.

### Combinators and their error semantics
- `Promise.all(ps)` — fulfils with an array when all fulfil; rejects with the **first** rejection. The
  remaining promises are not cancelled; they run to completion unobserved. Use it when any failure makes
  the whole result useless.
- `Promise.allSettled(ps)` — never rejects; yields `{status, value|reason}` per input. Use it when
  partial results are useful, e.g. fan-out to several providers or tools where you want every outcome.
- `Promise.any(ps)` — fulfils with the first fulfilment; rejects only when all reject, with an
  `AggregateError` whose `.errors` holds each reason. Use it for racing equivalent providers.
- `Promise.race(ps)` — settles with whichever settles first, success or failure; mostly used to impose a
  timeout, which `AbortSignal.timeout` does more cleanly because it also stops the work.

Because combinators do not cancel losers, pair them with an `AbortController` when the losing calls are
expensive (every model call is).

### Keeping the cause
Wrapping an error adds context but must not destroy the original. `new Error("tool failed", { cause:
err })` (ES2022) keeps the chain; a custom `class ProviderError extends Error` can carry `status`,
`retryable` and the provider's error body. `catch (e: unknown)` forces narrowing with `instanceof`
before reading fields. Node links async stack frames across `await` (async stack traces), but only when
the promise is awaited directly; a `.then` chain hides the originating frame, which is one more reason
to prefer `await`.

## Example
Fan-out to three providers with `allSettled` so every outcome is known, classify each failure with a
status and a retryable flag, preserve the original cause when wrapping, abort the losers once a winner
arrives, and give the fire-and-forget audit call an explicit `.catch` so it can never become an
unhandled rejection.

```ts
class ProviderError extends Error {
  constructor(msg: string, readonly status: number, readonly retryable: boolean, opts?: { cause?: unknown }) {
    super(msg, opts); this.name = "ProviderError";
  }
}

async function callProvider(name: string, prompt: string, signal: AbortSignal): Promise<string> {
  try {
    const res = await fetch(`https://${name}.example/v1/complete`, {
      method: "POST", body: JSON.stringify({ prompt }), signal,
    });
    if (!res.ok) {
      const body = await res.text();
      throw new ProviderError(`${name} returned ${res.status}: ${body.slice(0, 200)}`,
        res.status, res.status === 429 || res.status >= 500);
    }
    return await res.text();                 // `return await` so the try block sees a rejection
  } catch (e: unknown) {
    if (e instanceof ProviderError) throw e; // already classified
    throw new ProviderError(`${name} transport failure`, 0, true, { cause: e });
  }
}

async function bestEffort(prompt: string) {
  const ac = new AbortController();
  const names = ["alpha", "beta", "gamma"];
  const results = await Promise.allSettled(names.map((n) => callProvider(n, prompt, ac.signal)));
  const ok = results.find((r): r is PromiseFulfilledResult<string> => r.status === "fulfilled");
  if (ok) { ac.abort(); return ok.value; }   // stop the losers; they are billed per token
  const reasons = results.map((r) => (r as PromiseRejectedResult).reason);
  const retryable = reasons.every((r) => r instanceof ProviderError && r.retryable);
  throw new AggregateError(reasons, retryable ? "all providers failed (retryable)" : "all providers failed");
}

// Fire-and-forget logging is still a promise: attach a handler or the rejection becomes fatal.
void (async () => { /* audit sink */ })().catch((e) => console.error("audit sink failed", e));
```

## Trade-offs
- `Promise.all` is simplest and fastest to fail, but it discards the other outcomes and leaves them
  running; it is the right choice only when a single failure invalidates the whole batch.
- `allSettled` gives complete information at the cost of handling every outcome yourself, which is more
  code but is what you want for provider fan-out, tool batches and anything partially useful.
- Wrapping errors with `cause` keeps diagnostics but makes error objects larger and may carry sensitive
  provider bodies into logs; redact at the logging boundary rather than by dropping the cause.
- Crashing on unhandled rejections loses in-flight requests but surfaces bugs immediately; swallowing
  them keeps the process up while silently losing work, which is worse in anything that bills tokens.
- Prefer `await` over `.then` chains for readable stack traces, accepting that sequential awaits can
  serialize work you meant to run concurrently; start promises first, await them after.

## Pitfalls
- Forgetting `await` on a call whose result you do not use (logging, metrics, cache writes); the symptom
  is a process exiting with `ERR_UNHANDLED_REJECTION` minutes after the real error, or data that never
  arrives.
- `return promise` inside `try` instead of `return await promise`; the symptom is a `catch` block that
  "never runs" while the error surfaces in the caller.
- Awaiting sequentially what could run concurrently (`const a = await x(); const b = await y();`); the
  symptom is latency equal to the sum of calls instead of the max.
- Rethrowing as a fresh `new Error(msg)` without `cause`; the symptom is logs saying "model call failed"
  with no status code, no request id and no stack from the real failure.
- Treating `Promise.all` rejection as "the batch failed" and retrying everything; the symptom is
  duplicated tool side effects because the calls that had succeeded are run again.

## When to use
Use `async`/`await` with `try`/`catch` as the default for all I/O, and reach for combinators only at
explicit fan-out points: `all` for all-or-nothing, `allSettled` for partial results, `any` for racing
equivalent providers, always paired with an `AbortController` so losers stop consuming tokens. Define
one error class per boundary (provider, tool, validation) carrying status and retryability, and wrap
with `cause`. Enforce `no-floating-promises` in lint and keep the default crash-on-unhandled-rejection
behaviour so bugs surface in staging. Measure the rate of unhandled rejections and of errors without a
cause chain in logs. In interviews, explaining why `return await` differs inside `try`, what `all`
does with the losers, and how an unhandled rejection becomes a crash shows the expected depth.

## Role lens
- **Engineer:** Writes provider and tool clients that classify errors, preserve causes, and never leave a
  promise floating, and runs with lint rules that catch missing awaits.
- **Architect:** Defines the error taxonomy that crosses service boundaries (retryable vs permanent,
  provider vs validation) and the fan-out policy for multi-provider calls.
- **FDE:** Traces a customer's "random crash every few hours" to an unawaited promise in a webhook
  handler, and shows how `cause` chains would have pointed at the upstream 429 immediately.

## Check yourself
**Q:** An agent service calls three tools concurrently with `Promise.all`. One tool rejects, the handler
catches it and retries the whole batch. Users report duplicated emails. Explain the bug and the fix.
**A:** `Promise.all` rejects as soon as one promise rejects, but the other two keep running and in this
case one of them already sent an email. Retrying the batch re-runs that tool and sends a second email.
The fix has three parts: use `Promise.allSettled` so each outcome is known and only the failed tool is
retried; give side-effecting tools an idempotency key so a retry cannot duplicate the effect; and pass an
`AbortSignal` so that when the batch is genuinely abandoned, still-running tools are cancelled instead of
completing unobserved. The error should also carry the original cause and a retryable flag so the loop
can decide whether a retry is even appropriate.

## Quiz
### Q: Why does `return fetchModel()` inside a `try` block fail to catch a rejection from `fetchModel()`, while `return await fetchModel()` catches it?
- [ ] `return` exits the try block synchronously before any promise is created
- [x] Without `await` the promise is returned still pending, so its rejection happens after the function has already left the try block
- [ ] `await` converts the rejection into a return value of `undefined`
- [ ] The two forms are identical; the difference is only in stack trace depth
> `try`/`catch` only sees exceptions thrown while control is inside the block. A returned pending promise
> settles later, outside the block, so the rejection propagates to the caller. `return await` suspends
> inside the block and re-throws there. Outside a try, the forms behave the same.

### Q: What happens to the other promises when `Promise.all` rejects because one input rejected?
- [ ] They are cancelled and their rejections are suppressed
- [ ] They are cancelled and their pending work is rolled back
- [x] They keep running to completion and their results are ignored
- [ ] They are converted into unhandled rejections and crash the process
> Promises cannot be cancelled by the combinator; it only stops listening. The remaining work, including
> paid model calls and side effects, continues. Cancellation must be done cooperatively through an
> `AbortSignal` passed into each call.

### Q: A service exits with `ERR_UNHANDLED_REJECTION` even though every `await` is inside a `try`. What is the most likely cause?
- [ ] A `catch` block rethrew the error
- [x] A promise was created without being awaited, returned, or given a `.catch`, and it rejected
- [ ] `Promise.allSettled` rejected because all inputs rejected
- [ ] The error object was not an instance of `Error`
> A rejection with no handler attached by the end of the microtask drain is unhandled, and Node's default
> is to raise it as a fatal exception. Floating promises from fire-and-forget calls are the usual source.
> `allSettled` never rejects, and non-Error reasons are still handled normally when awaited.
