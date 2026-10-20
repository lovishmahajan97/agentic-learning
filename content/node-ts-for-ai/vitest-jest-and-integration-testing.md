---
id: vitest-jest-and-integration-testing
module: node-ts-for-ai
name: Vitest / Jest & integration testing
kind: practice
difficulty: core
est_minutes: 15
related:
  - node-ts-for-ai/dependency-injection
  - node-ts-for-ai/openai-anthropic-node-sdks
  - node-ts-for-ai/docker-ci-cd-deployment
  - node-ts-for-ai/abortcontroller-and-cancellation
  - python-for-ai/pytest
  - python-for-ai/test-doubles-mocks-fixtures
  - python-for-ai/integration-and-end-to-end-testing
  - evals/regression-tests
sources:
  - Official docs: Vitest
  - Official docs: Jest
  - Official docs: Mock Service Worker (msw)
  - Official docs: Testcontainers for Node
---

## TL;DR
A model call is slow, billed and non-deterministic, so it cannot sit inside a test that must pass on
every commit. The working split is three layers: unit tests with an injected fake model, integration
tests that run the real SDK, database and queue against intercepted or containerised dependencies, and
evals that call the real model and are *scored*, not asserted. Vitest and Jest are the runners that
make the first two layers fast; the design that makes them possible is injecting the model client.

## Why it exists
An AI service is ordinary backend code wrapped around a call that behaves unlike anything else in the
process. The call takes seconds, costs money per token, needs a secret, and returns different text for
the same input. Every property breaks a conventional test suite: a suite that hits the real model is
too slow to run on every push, too expensive to run in parallel, leaks a production key into CI logs,
and fails randomly whenever the provider is degraded or the model phrases an answer differently. Teams
that start this way end up with `it.skip` all over the repo and no safety net at all.

The opposite naive answer, "mock everything", fails more quietly. A `vi.mock("@anthropic-ai/sdk")`
that returns a canned object tests only your own branches; it says nothing about whether the request
you build is one the API accepts. The bug class that actually bites, a renamed field, a tool schema the
provider rejects, a stream event type you forgot to handle, lives in the serialization and parsing
layer that a module mock skips entirely. The same is true for the database: an in-memory stand-in does
not enforce unique constraints, JSON column semantics or transaction isolation, so the test passes and
production throws.

The practice therefore has to separate *what you can assert* from *what you can only measure*. Logic
around the model (prompt assembly, retries, parsing, tool dispatch, persistence) is deterministic once
the model is faked, so assert it. The model's output quality is statistical, so measure it in an eval
with a threshold and run it on a schedule, not as a merge gate. Getting that boundary right is the
whole skill; the runner is secondary.

## How it works
### The runners
Vitest and Jest share the `describe` / `it` / `expect` vocabulary, the `beforeAll` / `afterEach` hooks,
module mocking, fake timers, snapshots and coverage, which is deliberate: Vitest was built to be
largely Jest-compatible. They differ in how code reaches the test. Jest predates native ES modules and
TypeScript in Node; it runs each file through a transform you configure (`ts-jest`, `babel-jest` or an
swc plugin) and its ESM support has long sat behind an experimental flag, which is why Jest projects
accumulate `moduleNameMapper` and `transformIgnorePatterns` entries. Vitest reuses the Vite pipeline:
esbuild strips types in milliseconds, ESM is the default, `vite.config.ts` aliases apply to tests too,
and watch mode re-runs only the files whose import graph changed. For a TypeScript-first AI service
started today, Vitest is the default; Jest remains the right answer when the repo already runs on it,
because migrating a large suite buys little.

Both run test files in parallel workers with an isolated module registry per file, *because* module
state (a cached client, a memoised config) would otherwise leak between tests. That isolation is also
why `vi.mock` / `jest.mock` calls are hoisted above imports by the transform: ESM imports resolve
before any statement runs, so a mock registered in normal source order would arrive too late.

### Three kinds of double for the model
1. **An injected fake.** Define the narrowest interface your code needs (`complete(prompt): Promise<string>`,
   or a stream of events) and pass an implementation in, rather than importing the SDK at module scope.
   Tests then hand in a function that returns scripted answers, including malformed ones, in a known
   order. This tests your parsing, repair loops, budgets and persistence, and it runs in microseconds.
2. **HTTP interception.** Construct the real SDK and intercept at the network layer with `msw`
   (`setupServer` from `msw/node`), `nock`, or undici's `MockAgent`. Now the request body you assert on
   is the exact JSON the provider would receive, and the response you return is parsed by the real SDK
   code. This is the layer that catches a wrong field name or a stream-event handler that never fires.
   Set `maxRetries: 0` on the client so a deliberate 500 does not trigger three retries with backoff.
3. **Recorded fixtures.** Capture a handful of real responses once (including tool-call turns and a
   full stream transcript), store them as JSON, and replay them through the interceptor. They are the
   only cheap way to test against the real response shape of a provider, and the only way to notice
   that an SDK upgrade changed a type. Refresh them deliberately, not on every run.

Each layer exists because the one below it cannot see the failure it catches. The fake cannot see
serialization bugs; the interceptor cannot see output-quality drift; evals cannot see a bug in your
retry logic because they are too noisy to attribute a failure to it.

### Integration tests with real dependencies
Postgres, Redis and a queue should be real in integration tests, *because* the behaviours you depend
on (constraint violations, `SELECT … FOR UPDATE`, JSONB queries, Redis expiry, BullMQ retries) are the
things fakes get wrong. Two patterns work: a `globalSetup` that starts containers with Testcontainers
and exports connection strings, or a service started by CI (a GitHub Actions `services:` block, a
compose file locally). Isolation then has to be designed, since the runner is parallel: give each
worker its own database or schema (derive the name from `VITEST_POOL_ID` / `JEST_WORKER_ID`), or run
every test inside a transaction that is rolled back in `afterEach`, or truncate between tests and
accept running those files serially. Shared mutable state across workers is the number-one source of
flaky integration suites.

### Async, streams, time and cancellation
Model code is asynchronous and often streamed, so the fakes must be too. An `async function*` is a
perfect fake stream, and a fake that waits on a `signal` lets you assert that aborting the request
stops iteration. Retries and backoff should run under fake timers (`vi.useFakeTimers()` plus
`vi.advanceTimersByTimeAsync`) so a 30-second backoff test finishes in milliseconds; forgetting to
await the async advance is the usual reason a retry test hangs. `expect.poll` and `vi.waitFor` cover
eventually-consistent assertions against a queue.

### Snapshots: prompts yes, completions no
Inline snapshots of the *rendered prompt* (system text plus serialized tool definitions) are cheap
regression tests: a prompt edit shows up as a diff in review rather than as a silent behaviour change.
Snapshotting model output is the opposite: it either fails on every run or is updated blindly with
`-u`, which trains people to ignore it.

### Where the real model lives
Real-model tests belong in a separate suite, selected by a tag or directory, run on a schedule or on
demand with a budget, and scored against a threshold (pass rate ≥ 0.9 on a golden set, judge score
above a floor). They publish a metric; they do not block a merge on one bad sample. The evals module
covers how to build that set and the harness around it.

## Example
A `summarize` service that depends on a narrow `ModelClient` interface. The first test uses an
injected fake to check the repair loop; the second constructs the real Anthropic SDK and intercepts
the HTTP call with `msw`, so the request body and the response parsing are the real ones.

```ts
import { describe, it, expect, vi, beforeAll, afterAll, afterEach } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import Anthropic from "@anthropic-ai/sdk";
import { summarize, anthropicAdapter } from "../src/summarize";

describe("summarize (unit, injected fake)", () => {
  it("feeds a malformed answer back once, then returns parsed bullets", async () => {
    const complete = vi.fn()
      .mockResolvedValueOnce("not json")
      .mockResolvedValueOnce('{"bullets":["a","b"]}');
    const out = await summarize("long text", { complete });
    expect(out.bullets).toEqual(["a", "b"]);
    expect(complete).toHaveBeenCalledTimes(2);
    expect(complete.mock.calls[1][0]).toContain("not json");   // repair prompt quotes the bad output
  });
});

const server = setupServer(
  http.post("https://api.anthropic.com/v1/messages", async ({ request }) => {
    const body = (await request.json()) as { max_tokens: number; messages: unknown[] };
    expect(body.max_tokens).toBeGreaterThan(0);               // asserts the real wire shape
    expect(body.messages).toHaveLength(1);
    return HttpResponse.json({
      id: "msg_test", type: "message", role: "assistant", model: "test",
      content: [{ type: "text", text: '{"bullets":["x"]}' }],
      stop_reason: "end_turn", usage: { input_tokens: 12, output_tokens: 6 },
    });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));  // any other network call fails loudly
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("summarize (integration, real SDK over intercepted HTTP)", () => {
  it("builds a request the API accepts and parses the response", async () => {
    const client = new Anthropic({ apiKey: "test-key", maxRetries: 0 });
    const out = await summarize("long text", anthropicAdapter(client));
    expect(out.bullets).toEqual(["x"]);
  });
});
```

## Trade-offs
- An injected fake makes tests fast and deterministic but can drift from the real client's behaviour;
  the interception layer is what keeps it honest, so you need both, not one.
- Real containers in integration tests catch database semantics fakes miss, at the cost of slower
  startup and CI complexity; below a handful of queries an in-memory repository is acceptable.
- Recorded fixtures pin the provider's response shape, which is the point, but they go stale when the
  provider adds fields; refresh them on SDK upgrades rather than letting them rot or auto-updating.
- Vitest's Vite pipeline gives fast TS/ESM with little config, while Jest's maturity and ecosystem win
  in large existing suites; switching an established Jest repo rarely pays for itself.
- Keeping real-model tests out of the merge gate protects velocity, but only if the scheduled eval
  actually runs and someone owns its threshold; otherwise quality regressions go unnoticed.

## Pitfalls
- Calling the real model in the PR suite; the symptom is intermittent red builds, a growing list of
  `it.skip`, and a provider key visible in CI logs.
- Mocking the SDK module instead of the network; the symptom is a green suite and a 400 from the
  provider in production because a field was renamed or a tool schema was invalid.
- Sharing one database between parallel workers; the symptom is tests that pass alone and fail in the
  full run, with unique-constraint or "row not found" errors that depend on ordering.
- Testing backoff with real timers; the symptom is a retry test that takes a minute or hits the default
  test timeout, which then gets raised instead of fixed.
- Forgetting `maxRetries: 0` on the client under test; the symptom is a test of a 500 response that
  takes several seconds and asserts on the wrong number of calls.
- Snapshotting model text; the symptom is a snapshot file updated with `-u` on every change, so the
  test no longer tests anything.
- Leaving `onUnhandledRequest` at its default; the symptom is a test that silently hit the real
  network because a URL did not match the handler.

## When to use
Reach for the injected fake whenever logic surrounds the model: prompt assembly, parsing, repair loops,
budgets, tool dispatch, persistence. Add the interception layer for each provider adapter and for any
stream-handling code, since that is where wire-format bugs live. Run real dependencies in integration
tests for anything touching SQL, Redis or a queue, and design worker isolation before the suite grows.
Keep the real model in a separate scored suite with a budget. Measure the suite's wall-clock time, its
flake rate, and how many bugs reach production in the serialization layer; those three numbers tell you
which layer is missing. Interviewers ask how you test non-deterministic code and what a fake cannot
catch; the layered answer, with the merge-gate versus eval distinction, is what they are listening for.

## Role lens
- **Engineer:** Writes the `ModelClient` interface, the fakes and the msw handlers, keeps the unit
  suite under a few seconds, and owns the Testcontainers setup for Postgres and Redis.
- **Architect:** Decides the boundary between asserted tests and scored evals, which dependencies must
  be real in CI, and the budget and cadence of the real-model suite.
- **FDE:** Builds a replayable fixture set from the customer's real traffic so their team can change
  prompts and upgrade SDKs with a safety net, without spending their model budget on CI.

## Check yourself
**Q:** Your team's suite calls the real model in twenty tests. It is slow, flaky and costs money, but
the lead argues that mocks would make the tests meaningless. How do you restructure it, and what does
each layer guarantee?
**A:** Split by what each layer can observe. Inject the model behind a narrow interface and rewrite the
twenty tests as unit tests with scripted fakes; they now assert parsing, retries, tool dispatch and
persistence deterministically. Add one interception test per provider adapter using the real SDK over
msw, asserting the request body and feeding back recorded responses; this catches the wire-format bugs
the lead is worried about. Keep real dependencies (Postgres, Redis) in integration tests with
per-worker isolation. Move the real-model cases into a scored eval with a golden set and a pass-rate
threshold, run on a schedule with a budget, so quality drift is measured rather than asserted. The
merge gate becomes fast and deterministic without pretending the model is deterministic.

## Quiz
### Q: What does an HTTP-interception test with the real SDK catch that an injected fake model cannot?
- [ ] Non-deterministic differences in the model's wording
- [x] Errors in the request the SDK actually sends and in parsing the real response shape
- [ ] Database constraint violations in the persistence layer
- [ ] Slow backoff logic that should run under fake timers
> The fake replaces everything below your interface, so a renamed field or an invalid tool schema never
> reaches a test. Running the real SDK against intercepted HTTP exercises serialization and parsing
> while remaining deterministic. Model wording belongs to evals, not either test layer.

### Q: Why are `vi.mock` and `jest.mock` hoisted above the import statements in a test file?
- [ ] To make the mock apply to every file in the worker pool
- [ ] Because mocks must be registered before the test runner starts its timers
- [x] Because ES module imports resolve before any statement runs, so a mock in source order would register too late
- [ ] To allow the mocked module to be type-checked by TypeScript
> Imports are evaluated before the module body executes. If the mock registration ran in normal order,
> the module under test would already hold the real dependency. Hoisting the mock call fixes the
> ordering; it does not change scope across files or affect type checking.

### Q: Where should tests that call the real model live, and how should they report?
- [ ] In the PR suite, retried three times to absorb non-determinism
- [ ] In the PR suite, with snapshots of the model's text updated when they change
- [x] In a separate scheduled, budgeted suite that scores results against a threshold rather than asserting each case
- [ ] Nowhere; faked tests plus interception tests are sufficient
> Model output is statistical, so a single failing case is not a regression and a single passing one is
> not proof. A scored suite with a golden set and a pass-rate floor measures quality drift without
> making every merge depend on provider latency, cost and randomness.
