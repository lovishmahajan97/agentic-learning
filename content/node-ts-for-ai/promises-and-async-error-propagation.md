---
id: promises-and-async-error-propagation
module: node-ts-for-ai
name: Promises & async error propagation
kind: concept
difficulty: core
est_minutes: 14
status: agentic
agent_relevance: core
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
An agent step is a bundle of concurrent promises: model call, tool calls, logging. `await` re-throws a
rejection where you wait, so errors travel up like sync ones; floating promises, lossy combinators and lost
causes make agents crash or retry the wrong thing.

## For agent builders
- **Why it matters for agents.** Each step fans out (parallel tool calls, multi-provider fallback, trace
  writes). How you combine and classify those promises decides whether one failed tool becomes a message
  the model can react to, a correct retry, a duplicated side effect, or a crash that kills every session.
- **Where it shows up in your stack.** Parallel tool execution, provider SDK calls, retry wrappers,
  fire-and-forget trace and audit sinks, and the process-level `'unhandledRejection'` behaviour of your
  agent server.
- **What you control.** `allSettled` versus `all` per fan-out, error classes with `status`, `retryable` and
  `cause`, lint for floating promises, and which errors go back to the model versus end the run.
- **What you can skip.** The spec's job-queue internals; know that handlers run as microtasks and that a
  rejection needs a handler attached before it happens.

## Why it exists
- **Callbacks made errors easy to forget.** Callback-style Node passed errors as the first argument, so a
  forgotten `if (err)` meant silent corruption, and five dependent I/O calls meant five levels of nesting
  with five separate error paths.
- **Promises made the result a value.** A chain of `.then` calls has one `.catch` at the end, and a
  rejection anywhere skips straight to it. `async`/`await` made control flow readable again, with
  `try`/`catch` working across awaits.
- **What promises did not fix: ownership of a failure.** An agent loop makes dozens of concurrent calls (the
  model, three tool executions, an embedding lookup, a logging sink). A started-but-never-awaited one has no
  handler; modern Node treats that as fatal and exits, taking every other in-flight request with it.
- **Combinators and wrappers hide detail.** With `Promise.all` the first rejection wins and the others keep
  running unobserved. Rethrowing a `fetch` error as `new Error("model call failed")` loses the status code
  and the provider's error body before it reaches a log.
- **AI failures demand precision.** Failures are dominated by upstream timeouts, rate limits and malformed
  outputs, so the difference between a retryable 429 and a permanent 400 must survive the trip up the stack.

## How it works
### States and chaining
- **Settles exactly once.** A promise is pending, then fulfilled (with a value) or rejected (with a reason,
  conventionally an `Error`).
- **`.then(onFulfilled, onRejected)` returns a *new* promise** whose outcome is whatever the handler returns
  or throws; a returned promise is adopted (flattened).
- **That is why chains propagate.** A throw in any step rejects that step's promise, every following
  `.then` without a rejection handler passes it through unchanged, and the first `.catch` receives it.
- **Handlers always run as microtasks,** never synchronously, even when the promise is already settled,
  which keeps ordering consistent (see the event-loop lesson).

### What `async`/`await` desugars to
1. **An `async` function always returns a promise.** `return x` fulfils it.
2. **A thrown exception rejects it,** including one thrown synchronously before the first `await`; it does
   not escape to the caller.
3. **`await p` suspends the function;** when `p` rejects, the rejection is re-thrown at the `await`
   expression, so an enclosing `try`/`catch` catches it.
4. **The single rule:** errors cross `await` boundaries as exceptions, so a stack of async functions behaves
   like a stack of sync ones.

- **The `return await` subtlety.** `return fetchModel()` inside a `try` block does **not** catch a rejection
  of `fetchModel()`, because the promise is returned before it settles; `return await fetchModel()` does.
  Outside a `try`, the two are equivalent apart from a stack frame.

### Unhandled rejections
- **The trigger.** A rejected promise with no handler attached by the end of the current microtask drain
  fires `'unhandledRejection'`.
- **Default since Node 15: `throw`.** The rejection is raised as an uncaught exception and the process exits,
  unless an `'unhandledRejection'` listener is installed, which suppresses the throw.
- **What the crash shows.** The original error and stack when the reason is an `Error`; only a non-Error
  reason (a thrown string, say) is wrapped in an error with code `ERR_UNHANDLED_REJECTION`, which carries no
  useful stack.
- **The usual cause: a *floating promise*.** `logToSink(event)` called without `await` or `.catch`, or a
  `.then` with no rejection branch.
- **Fix the cause, not the symptom.** Every promise must be awaited, returned, passed to a combinator, or
  given an explicit `.catch` that decides what to do. Lint for it (`@typescript-eslint/no-floating-promises`).
- **A log-only global handler is not a strategy.** A `process.on('unhandledRejection')` handler that only logs
  turns a crash into silent data loss; it is acceptable as a last-resort reporter only.

### The handler-attachment window
"Unhandled" is judged per promise at the moment it rejects, not at the end of the function. That makes a
tempting concurrency pattern dangerous:

```
const a = callModel(p1);   // both start now: good
const b = callTool(t1);
const x = await a;         // while suspended here, b rejects -> nobody is listening yet
const y = await b;         // too late: the process may already have crashed
```

- **What goes wrong.** If `b` rejects while the function is still suspended on `a`, `b` has no handler, the
  `'unhandledRejection'` check fires, and in the default mode the process exits. Attaching a handler later
  only triggers the `'rejectionHandled'` event.
- **The fix.** `await Promise.all([a, b])` (or `allSettled`) attaches handlers to both immediately, which is
  the reason to use a combinator for concurrent work rather than staggered awaits.

### Where propagation stops
`await` carries errors only along promise edges. Four places break the chain:

- **Callbacks scheduled from async code.** A `throw` inside `setTimeout(() => ...)`, an event-emitter
  listener or a stream `'data'` handler is not inside your `try`; the async function has already moved on, so
  it becomes an uncaught exception. Wrap the callback API in a promise (or use `events.once`,
  `timers/promises`, `stream/promises`) so the error re-enters the chain.
- **Emitter `'error'` events.** An `EventEmitter` that emits `'error'` with no listener throws. Streams and
  SDK clients that expose events need a listener, or a promise-returning wrapper.
- **Non-async functions that return promises.** `function call() { validate(x); return fetch(...) }` throws
  *synchronously* if `validate` fails, so `call().catch(...)` never sees it. Marking it `async` converts the
  throw into a rejection, making error handling uniform.
- **Floating promises,** covered above: no edge means no propagation.

### Combinators and their error semantics
| Combinator | Fulfils | Rejects | Use when |
|---|---|---|---|
| `Promise.all(ps)` | With an array when all fulfil | With the **first** rejection; the rest run to completion unobserved, not cancelled | Any failure makes the whole result useless |
| `Promise.allSettled(ps)` | Always, with one status object per input (below) | Never | Partial results are useful: fan-out to providers or tools where you want every outcome |
| `Promise.any(ps)` | With the first fulfilment | Only when all reject, with an `AggregateError` whose `.errors` holds each reason | Racing equivalent providers |
| `Promise.race(ps)` | Whichever settles first | Whichever settles first | Mostly timeouts, which `AbortSignal.timeout` does more cleanly because it also stops the work |

- **`allSettled` result shape:** one `{status, value|reason}` object per input, in input order.
- **Combinators do not cancel losers,** so pair them with an `AbortController` when the losing calls are
  expensive (every model call is).

### Keeping the cause
- **Wrap, do not replace.** `new Error("tool failed", { cause: err })` adds context; the `cause` option
  (ES2022) keeps the chain; a custom error class keeps classification too.
- **Carry classification.** A custom `class ProviderError extends Error` can carry `status`, `retryable` and
  the provider's error body.
- **Narrow before reading.** `catch (e: unknown)` forces narrowing with `instanceof` before reading fields.
- **Prefer `await` for stack traces.** Node links async stack frames across `await`, but only when the
  promise is awaited directly; a `.then` chain hides the originating frame.

## Diagram
How a rejection travels: through every `await`, until a `catch` handles it, or out to the process-level
check that crashes Node when nothing is listening.

```mermaid
flowchart TD
    F["fetch rejects: 429"] --> W["Wrap: ProviderError with cause"]
    W --> A1["await in callProvider rethrows"]
    A1 --> A2["await in agent step rethrows"]
    A2 --> Q{"try/catch on the path?"}
    Q -->|"yes"| H["Handle: retry if retryable"]
    Q -->|"no, promise floating"| U["unhandledRejection check"]
    U -->|"default mode throw"| X["Process exits"]
    U -.->|"listener installed"| L["Logged only, work lost"]
```

## Examples
### Provider fan-out with classified errors
Fan-out to three providers with `allSettled` so every outcome is known, classify each failure with a status
and a retryable flag, preserve the original cause when wrapping, abort the losers once a winner arrives,
and give the fire-and-forget audit call an explicit `.catch` so it can never become an unhandled rejection.

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
    if (e instanceof ProviderError || signal.aborted) throw e; // classified, or a cancellation: not retryable
    throw new ProviderError(`${name} transport failure`, 0, true, { cause: e });
  }
}

async function bestEffort(prompt: string) {
  const ac = new AbortController();
  const names = ["alpha", "beta", "gamma"];
  let winner: string | undefined;
  const calls = names.map((n) => callProvider(n, prompt, ac.signal).then((text) => {
    winner ??= text; ac.abort(); return text;   // first success aborts the losers mid-flight
  }));
  const results = await Promise.allSettled(calls); // losers now settle quickly with AbortError
  if (winner !== undefined) return winner;
  const reasons = results.map((r) => (r as PromiseRejectedResult).reason);
  const retryable = reasons.every((r) => r instanceof ProviderError && r.retryable);
  throw new AggregateError(reasons, retryable ? "all providers failed (retryable)" : "all providers failed");
}

// Fire-and-forget logging is still a promise: attach a handler or the rejection becomes fatal.
void (async () => { /* audit sink */ })().catch((e) => console.error("audit sink failed", e));
```

### Watching the attachment window crash a process
Save as `window.mjs`. `node window.mjs` prints the caught error; `node window.mjs staggered` crashes,
because the fast tool rejects at 50 ms while the function is still suspended on the 200 ms model call.

```js
const delay = (ms, fail) => new Promise((resolve, reject) =>
  setTimeout(() => (fail ? reject(new Error(`tool failed at ${ms} ms`)) : resolve(ms)), ms));

async function staggered() {
  const a = delay(200);          // slow model call
  const b = delay(50, true);     // fast tool call that fails
  try {
    await a;                     // b rejects while we wait here: no handler yet
    await b;
  } catch (e) { console.log("caught", e.message); }   // never reached
}

async function combined() {
  try { await Promise.all([delay(200), delay(50, true)]); }
  catch (e) { console.log("caught", e.message); }     // caught tool failed at 50 ms
}

await (process.argv[2] === "staggered" ? staggered() : combined());
```

### Agent scenario: tool failures become tool results
In a tool-calling loop, a failed tool should usually be reported to the model, not thrown out of the loop.
`allSettled` gives one outcome per call, and classification decides what the model sees.

```ts
// ProviderError is the class from the first example; exec retries transient failures itself.
type ToolCall = { id: string; name: string; args: unknown };
type ToolResult = { toolCallId: string; content: string; isError: boolean };

async function runToolBatch(
  calls: ToolCall[],
  exec: (c: ToolCall, signal: AbortSignal) => Promise<string>,
  signal: AbortSignal,
): Promise<ToolResult[]> {
  const settled = await Promise.allSettled(calls.map((c) => exec(c, signal)));
  return settled.map((r, i) => {
    const id = calls[i]!.id;
    if (r.status === "fulfilled") return { toolCallId: id, content: r.value, isError: false };
    const e: unknown = r.reason;
    if (signal.aborted) throw e;                         // the run was cancelled: stop the loop
    if (e instanceof ProviderError && !e.retryable) {    // permanent: tell the model, let it adapt
      return { toolCallId: id, content: `error ${e.status}: ${e.message}`, isError: true };
    }
    return { toolCallId: id, content: "temporary failure after retries", isError: true };
  });
}
```

- **Why this shape:** one tool's rejection cannot lose the other results, cancellation still ends the run,
  and the model gets a message per tool call it made (most tool-calling APIs expect one result per call).

### Worked scenario: sequential vs concurrent latency
An agent step needs a model call, a tool call and an embedding lookup that do not depend on each other
(illustrative timings).

| Pattern | Latency | Failure behaviour |
|---|---|---|
| `await` each in turn (1.2 s + 0.8 s + 0.3 s) | 2.3 s, the sum | Safe, but slow |
| Start all, await one by one (model first) | 1.2 s, the max | Crashes (default mode) if the 0.8 s or 0.3 s call rejects during the 1.2 s wait |
| Start all, `await Promise.allSettled([...])` | 1.2 s, the max | Every outcome known; retry only what failed |

## Production patterns
- **Errors as tool results, cancellations as stops.** Agent teams settle each tool batch with `allSettled`,
  return permanent tool errors to the model as error tool results, retry transient ones within a budget,
  and rethrow only aborts and loop-level failures. Monitor error-result rate per tool and runs ended by
  errors versus by the model.
- **Lint floating promises in CI.** Enable `@typescript-eslint/no-floating-promises` (it needs type
  information) as an error, and require an explicit `void` plus `.catch` for intentional fire-and-forget.
  By default the rule accepts a bare `void p` (`ignoreVoid`), which still crashes if `p` rejects, so review
  that every `void` carries a `.catch`. Track disables per file in review.
- **Crash loudly, report first.** Keep Node's default `--unhandled-rejections=throw` (or use `strict`), and
  if you add a `process.on('unhandledRejection')` listener, make it log with the request context and then
  exit so the orchestrator restarts the process. Alert on restart count and on any such log line.
- **One error class per boundary.** Provider, tool and validation errors each carry `status`, `retryable`
  and `cause`; retry logic reads `retryable` instead of parsing messages. Monitor the share of logged errors
  that lack a cause chain.
- **Serialize the cause chain in logs.** Have the logger walk `err.cause` recursively and redact provider
  bodies at that boundary, rather than dropping the cause. Monitor log size per error event.
- **Fan-out with `allSettled` plus abort.** Use `Promise.allSettled` with one `AbortController` per batch, and
  give side-effecting tools idempotency keys so a retry of one failed call cannot duplicate an email or
  charge. Monitor duplicate-side-effect reports and per-provider failure rates.

## Trade-offs
- **`Promise.all`: simple vs lossy.** Fastest to fail, but discards the other outcomes and leaves them
  running; right only when a single failure invalidates the whole batch.
- **`allSettled`: complete vs more code.** Full information at the cost of handling every outcome yourself,
  which is what you want for provider fan-out, tool batches and anything partially useful.
- **`cause` chains: diagnostics vs exposure.** Wrapping keeps diagnostics but makes error objects larger and
  may carry sensitive provider bodies into logs; redact at the logging boundary rather than dropping the
  cause.
- **Crash vs swallow.** Crashing on unhandled rejections loses in-flight requests but surfaces bugs
  immediately; swallowing keeps the process up while silently losing work, worse in anything that bills
  tokens.
- **`await` vs `.then`.** Prefer `await` for readable stack traces, accepting that sequential awaits can
  serialize concurrent work. For concurrency, start the promises and await them through one combinator, not
  one by one, so every rejection has a handler from the start.

## Pitfalls
- **Letting one tool rejection end the agent run.** Symptom: a whole conversation fails because a search
  tool returned 404, and the other tool results are lost. Fix: settle the batch and send the error to the
  model as that call's tool result.
- **Forgetting `await` on a call whose result you ignore** (logging, metrics, cache writes). Symptom: a crash
  whose stack points into a logging or cache helper rather than the request that started it (or
  `ERR_UNHANDLED_REJECTION` with no stack when a non-Error was thrown), or data that never arrives. Fix:
  await it, or attach an explicit `.catch`.
- **`return promise` inside `try` instead of `return await promise`.** Symptom: a `catch` block that "never
  runs" while the error surfaces in the caller. Fix: `return await` inside `try`.
- **Awaiting sequentially what could run concurrently** (`const a = await x(); const b = await y();`).
  Symptom: latency equal to the sum of calls instead of the max. Fix: run independent calls concurrently
  through a combinator.
- **"Fixing" that with staggered awaits.** Symptom: an intermittent unhandled-rejection crash that only
  happens when the second call fails while the first is slow. Fix: start both, then await one combinator.
- **Throwing inside a `setTimeout` or event-listener callback** and expecting the surrounding `try` to catch
  it. Symptom: an `uncaughtException` crash whose stack has no request-handler frames. Fix: use the promise
  API (`timers/promises`, `events.once`) so the error re-enters the chain.
- **Rethrowing as a fresh `new Error(msg)` without `cause`.** Symptom: logs saying "model call failed" with
  no status code, no request id and no stack from the real failure. Fix: pass `{ cause: err }`.
- **Retrying the whole batch on `Promise.all` rejection.** Symptom: duplicated tool side effects, because
  the calls that had succeeded are run again. Fix: `allSettled`, retry only failures, idempotency keys.

## When to use
- **Default:** `async`/`await` with `try`/`catch` for all I/O.
- **At explicit fan-out points:** `all` for all-or-nothing, `allSettled` for partial results, `any` for
  racing equivalent providers, always paired with an `AbortController` so losers stop consuming resources
  (whether a provider stops generating, and billing, when the client disconnects is vendor-dependent).
- **Errors:** one error class per boundary (provider, tool, validation) carrying status and retryability,
  wrapped with `cause`.
- **Guardrails:** enforce `no-floating-promises` in lint and keep the default crash-on-unhandled-rejection
  behaviour so bugs surface in staging.
- **Measure:** the rate of unhandled rejections and of errors without a cause chain in logs.
- **Interviews:** explain why `return await` differs inside `try`, what `all` does with the losers, and how
  an unhandled rejection becomes a crash.

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

## Interview questions
### What happens, step by step, when an awaited promise rejects inside a nested async call?
- **At the innermost `await`:** the rejection is re-thrown as an exception at that expression.
- **Up the stack:** each `async` function without a matching `catch` rejects its own returned promise, and
  the next caller's `await` re-throws it, so it propagates like a synchronous exception.
- **At the top:** if nothing catches it and the outermost promise has no handler, `'unhandledRejection'`
  fires and, by default, the process exits.

### A service crashes every few hours with an unhandled rejection pointing at a metrics helper. How do you debug it?
- **Find the floating call:** search for the helper's call sites without `await`, `.catch` or a combinator;
  turn on `no-floating-promises` to list them all.
- **Fix the edge:** await it, or make fire-and-forget explicit with `.catch` that logs and drops.
- **Check the timing:** the crash appears only when the metrics backend fails, which explains the
  every-few-hours pattern.

### When would you use `Promise.any` instead of `allSettled` for a multi-provider call?
- **`any` when providers are equivalent** and you only need the first success; it rejects with an
  `AggregateError` only if all fail.
- **`allSettled` when every outcome matters,** for example to compare answers or to log every failure.
- **Either way, abort the losers,** because neither combinator cancels them and each still costs tokens.

### How do you decide whether a provider error should be retried, and keep that decision intact through the stack?
- **Classify at the boundary:** 429 and 5xx are retryable, other 4xx (such as 400) are permanent, and
  transport failures are usually retryable; record it on a `ProviderError` with `status` and `retryable`.
- **Wrap with `cause`** at each higher layer instead of creating a fresh error, so the retry loop and the
  logs still see the original status and body.

### In an agent loop, which tool failures go back to the model and which end the run?
- **Back to the model:** permanent, argument-related errors (a 400, not-found, a validation failure), as an
  error tool result, so it can fix arguments or pick another tool.
- **Retry first:** retryable ones (429, 5xx, transport) within a budget, with idempotency keys for side
  effects.
- **End the run:** cancellation, budget exhaustion and failures of the loop itself (the model call), which
  the model cannot act on.

### Your fan-out of five tool calls takes as long as their sum. What changed, and how do you fix it safely?
- **Likely cause:** someone wrote sequential `await`s, so latency is the sum rather than the max.
- **Safe fix:** start all five, then `await Promise.allSettled(...)` so every rejection has a handler from the
  start; staggered awaits would reintroduce the crash window.

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

### Q: A service crashes on an unhandled promise rejection even though every `await` is inside a `try`. What is the most likely cause?
- [ ] A `catch` block rethrew the error
- [x] A promise was created without being awaited, returned, or given a `.catch`, and it rejected
- [ ] `Promise.allSettled` rejected because all inputs rejected
- [ ] The error object was not an instance of `Error`
> A rejection with no handler attached by the end of the microtask drain is unhandled, and Node's default
> is to raise it as a fatal exception. Floating promises from fire-and-forget calls are the usual source.
> `allSettled` never rejects, and a non-Error reason only changes how the crash is reported
> (`ERR_UNHANDLED_REJECTION` without a stack), not whether it happens.

## Use cases & projects
### Use cases
- **Parallel tool calls.** When a model requests several tools in one turn, `allSettled` runs them together
  and returns one result per call, failed or not.
- **Multi-provider fallback.** `Promise.any` plus an `AbortController` races equivalent models and stops the
  losers from burning tokens.
- **Safe retries of side-effecting tools.** Classified errors and idempotency keys let an agent retry the one
  failed email or charge without repeating the ones that worked.
- **Trace and audit sinks.** Fire-and-forget writes with an explicit `.catch` keep an observability outage
  from crashing the agent server.

### Mini project
**Build a crash-proof tool batch for an agent loop (1-2 hours).**
- **Goal:** an agent step that runs three tools concurrently and survives any mix of failures correctly.
- **Steps:** write three fake tools (one slow, one that returns a 400, one that fails with a 503 on its first
  attempt); implement `runToolBatch` with `allSettled`, a `ProviderError` with `retryable` and `cause`, and
  one retry for retryable errors; add a fire-and-forget trace write that sometimes rejects; enable
  `no-floating-promises`.
- **Done when:** a test run shows the 400 returned to the model as an error result, the 503 retried once
  and succeeding, the trace failure logged without a crash, and lint passing with zero floating promises.
