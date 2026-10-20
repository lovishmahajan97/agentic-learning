---
id: pytest-asyncio
module: python-for-ai
name: pytest-asyncio
kind: tool
difficulty: core
est_minutes: 13
status: reviewed
related:
  - python-for-ai/pytest
  - python-for-ai/asyncio
  - python-for-ai/async-cancellation-and-exception-propagation
  - python-for-ai/test-doubles-mocks-fixtures
  - python-for-ai/race-conditions-and-synchronization-primitives
  - fastapi-for-ai/testclient-plus-pytest-asyncio
sources:
  - Official docs: pytest-asyncio
  - Official docs: Python asyncio
---

## TL;DR
pytest calls test functions synchronously, so an `async def` test is just a coroutine object nobody
awaits. pytest-asyncio is the plugin that runs async tests and async fixtures on an asyncio event loop
it owns, with markers and config that decide which tests are async and how long each loop lives. Most
of its sharp edges come from that loop lifetime: a resource made on one loop cannot be used on another.

## Why it exists
AI services are mostly I/O: a request fans out to an LLM, a vector store, a reranker and a database,
usually through `httpx.AsyncClient`, async database drivers and `asyncio.gather`. The interesting bugs
live in that async code: a semaphore that does not actually cap concurrency, a timeout that leaves the
underlying request running, a cancelled stream that never closes its connection, a retry that swallows
`CancelledError`. You cannot test those properties by calling the sync wrappers, because the bug only
exists while the event loop is scheduling tasks.

The naive approach is to write `def test_x(): asyncio.run(main())` in every test. It works for one
coroutine, but it creates and destroys a loop per call, gives you no way to write async setup and
teardown that the test shares (an async client opened before the test and closed after it), and
cannot share an expensive async resource such as a connection pool across tests. Writing `async def
test_x()` without a plugin is worse: pytest has no idea how to run it. Older pytest versions warned
and skipped such tests; recent versions fail them. Either way, the plugin is the piece that turns a
coroutine into a test run.

## How it works
### What the plugin hooks into
pytest-asyncio registers hooks that recognise coroutine test functions and async fixtures. For a test
it treats as async, it obtains an event loop for the configured scope, then drives the coroutine to
completion on that loop and reports the outcome as an ordinary pass, fail or error. Async fixtures
(plain `async def` returning a value, or an async generator using `yield`) are wrapped the same way:
the part before `yield` runs on the loop as setup, the part after runs on the same loop as teardown,
because closing an async client on a different loop than the one it was opened on fails.

### Strict vs auto mode
The plugin has two modes, set with `asyncio_mode` in pytest config.
- **strict** (the default): only tests marked `@pytest.mark.asyncio` run as asyncio tests, and async
  fixtures must be declared with `@pytest_asyncio.fixture`. It exists because a project may use
  several async frameworks (asyncio, trio via anyio) and the plugin should not claim tests it does not
  own.
- **auto**: every `async def` test is treated as an asyncio test and plain `@pytest.fixture` works on
  async functions. It removes boilerplate in a project that is asyncio-only.

### Loop scope
Each async test runs on a loop with a scope: function (a fresh loop per test, the default), class,
module, package or session. A fresh loop isolates tests, because leftover tasks and callbacks from
one test cannot leak into the next. A wider loop is needed when an async fixture with a wide scope (a
session-wide database pool) must be used by tests, because those tests have to run on the same loop
the pool was created on. Recent versions express this with `loop_scope` arguments on the marker and
on `pytest_asyncio.fixture`, plus a default-fixture-loop-scope config key; older versions used an
overridable `event_loop` fixture, which has been deprecated and removed. Check the docs for the
version you have pinned, because this API changed more than once.

### What it does not do
Three misconceptions are worth killing early. First, async tests do not run concurrently with each
other: pytest still executes one test at a time, and the plugin runs each test's coroutine to
completion before the next starts, so a slow async suite is not sped up by being async (use
pytest-xdist for that). Concurrency exists only *inside* a test, among the tasks that test creates.
Second, the plugin gives you no fake clock: `asyncio.sleep(10)` really waits ten seconds, which is
why timeout and retry tests either use tiny delays or inject the delay or clock as a parameter.
Third, it does not clean up after you: tasks a test spawned and never awaited are still pending when
the loop closes, which surfaces as warnings or as errors attributed to a later test. To run on uvloop
or another loop implementation, use the plugin's documented mechanism for your pinned version (an
event-loop policy fixture in recent releases; Python 3.14 deprecates loop policies, so expect this to
move again) rather than constructing and swapping loop objects yourself.

### The alternative
anyio ships its own pytest plugin (`@pytest.mark.anyio`) that can run the same test under asyncio and
trio. If your code is written against anyio (as Starlette and httpx internals are), that plugin is a
reasonable alternative. Pick one per suite: with pytest-asyncio in auto mode both plugins can try to
claim the same coroutine tests, and the resulting errors are confusing to debug.

## Example
A fan-out helper must never exceed a concurrency limit, and a timeout must leave no request in
flight. Both properties only exist while the loop is running, so they need real async tests. A fake
LLM records peak concurrency; the async fixture checks in teardown that nothing leaked.

```python
# pyproject.toml: [tool.pytest.ini_options] asyncio_mode = "strict"
import asyncio
import pytest
import pytest_asyncio

class FakeLLM:
    def __init__(self, delay: float):
        self.delay, self.in_flight, self.peak = delay, 0, 0
    async def complete(self, prompt: str) -> str:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            return prompt.upper()
        finally:
            self.in_flight -= 1          # runs on success and on cancellation

async def fan_out(llm, prompts, limit: int):
    sem = asyncio.Semaphore(limit)
    async def one(p):
        async with sem:
            return await llm.complete(p)
    return await asyncio.gather(*(one(p) for p in prompts))

@pytest_asyncio.fixture
async def llm():
    client = FakeLLM(delay=0.01)
    yield client
    assert client.in_flight == 0          # teardown, same loop as the test

@pytest.mark.asyncio
async def test_fan_out_respects_limit(llm):
    out = await fan_out(llm, [f"p{i}" for i in range(20)], limit=4)
    assert out == [f"P{i}" for i in range(20)]   # gather preserves order
    assert llm.peak == 4

@pytest.mark.asyncio
async def test_timeout_cancels_inner_call(llm):
    llm.delay = 10
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):        # Python 3.11+
            await llm.complete("x")
    assert llm.in_flight == 0
```

The suite runs in well under a second because the delays are tiny; a real limit bug shows up as
`peak == 20`, and a timeout that does not cancel shows up as `in_flight == 1`.

## Trade-offs
- Function-scoped loops isolate every test from leaked tasks, but force expensive async resources to
  be rebuilt per test; widen the loop scope only for the fixtures that are genuinely costly.
- Auto mode removes markers and special fixture decorators, but claims every coroutine test, so it
  fits asyncio-only projects and breaks once anyio or trio tests appear in the same suite.
- Real `asyncio.sleep` delays keep tests faithful to production timing, but make suites slow and
  timing-sensitive; injecting a clock or tiny delays flips that once the suite grows past a few minutes.
- Testing through the real event loop catches scheduling bugs that sync wrappers hide, at the price
  of tests that are harder to read and debug; if the property under test does not depend on
  scheduling (a prompt builder that happens to be `async`), extract it and test it synchronously.
- Session-scoped loops let one connection pool serve the whole suite, but one test's leaked task or
  closed client now poisons every later test; that trade flips to function scope as soon as failures
  start appearing only in particular test orders.

## Pitfalls
- A session-scoped async fixture (pool, client) used by function-loop tests; the symptom is
  `RuntimeError` mentioning a Future or Task "attached to a different loop", often only in CI ordering.
- Forgetting the marker or the `pytest_asyncio.fixture` decorator in strict mode. A missing marker
  makes recent pytest fail the test as an unsupported `async def` (older pytest warned and skipped it);
  a missing fixture decorator hands the test an un-awaited coroutine or async generator object instead
  of the value, so it fails on the first attribute access or with a "never awaited" warning.
- Calling blocking code (`time.sleep`, sync HTTP, sync DB drivers) inside an async test or fixture;
  the test passes but serialises everything, so concurrency assertions are silently meaningless.
- Spawning tasks with `asyncio.create_task` and never awaiting or cancelling them; the symptom is
  "Task was destroyed but it is pending" warnings and assertions failing in a later, unrelated test.
- Copying an `event_loop` fixture override from an old blog post into a pinned recent version; on
  0.2x releases the symptom is a deprecation warning, and on 1.x the plugin no longer consults that
  fixture at all, so your custom loop or policy silently stops applying.

## When to use
Use pytest-asyncio whenever the unit under test is a coroutine and the property you care about
depends on scheduling: concurrency caps, timeouts, cancellation, streaming, ordering of `gather`
results, backpressure. Pure logic that happens to sit inside async code is better extracted into a
sync function and tested without a loop. Pin the plugin version and set `asyncio_mode` and the default
fixture loop scope explicitly in config, so behaviour does not change on upgrade. For FastAPI apps,
pair it with an async HTTP client against the ASGI app. In interviews, being able to explain the "attached
to a different loop" error from first principles is a strong signal you understand both asyncio and
pytest fixtures.

## Role lens
- **Engineer:** Writes async tests for fan-out, retry and streaming code, choosing loop scopes so
  shared pools work without leaking state between tests.
- **Architect:** Sets the project-wide async testing convention: one async framework, pinned plugin,
  explicit mode and loop scope, and a rule that blocking calls in async code fail lint or review.
- **FDE:** Reproduces a customer's intermittent timeout or hung stream as a small async test with a
  fake slow dependency, so the fix can be shown working before redeploying.

## Check yourself
**Q:** Your suite has a session-scoped async fixture that opens an `httpx.AsyncClient`. Tests pass
when run one at a time but fail in CI with "attached to a different loop". What is happening and how
do you fix it?
**A:** The client lives for the whole session, but each test by default runs on its own
function-scoped loop that is closed when the test ends. The client's pooled connections are bound to
whichever loop was running when they were opened, typically the first test that made a request. The
next test pulls those keep-alive connections from the pool and awaits I/O tied to a dead loop. Run
alone, only one test loop ever exists, so nothing is shared across loops and it passes. Fixes: run the
fixture and the tests that use it on the same wider loop (`loop_scope="session"` on both
`pytest_asyncio.fixture` and the `asyncio` marker), or make the fixture function-scoped and accept
the cost, or create the client inside each test. The principle is that async resources are bound to
the loop they were made on, so fixture lifetime and loop lifetime must match.

## Quiz
### Q: In pytest-asyncio's strict mode, what happens to an unmarked `async def` test?
- [ ] It is run automatically on a fresh event loop
- [x] The plugin does not claim it, so it is not run as an asyncio test
- [ ] It runs on the session event loop instead of a function-scoped one
- [ ] It is converted into a synchronous test by wrapping it in asyncio.run
> Strict mode only treats tests with `@pytest.mark.asyncio` as asyncio tests, so multiple async
> plugins can coexist; an unclaimed coroutine test is then failed (or, on older pytest, skipped) by pytest
> itself. Automatic claiming of every coroutine test is what auto mode does.

### Q: Why does an async generator fixture run its teardown on the same loop as its setup?
- [ ] Because pytest requires all fixtures to share one thread
- [ ] Because a new loop would rerun the setup code
- [x] Because async resources such as clients and pools are bound to the loop they were created on
- [ ] Because teardown code is not allowed to await anything
> Closing a connection or awaiting a future from another loop raises errors, so setup, the test and
> teardown must all execute on one loop. Thread sharing is a separate concern from loop binding.

### Q: A concurrency-limit test passes, yet production exceeds the limit. Which test flaw best explains it?
- [ ] The test used a function-scoped event loop
- [x] The fake dependency blocked with time.sleep, so calls never overlapped and peak concurrency stayed low
- [ ] The test used auto mode instead of strict mode
- [ ] The fixture was declared with pytest_asyncio.fixture
> A blocking sleep stops the loop from switching tasks, so the test never produced concurrent calls
> to count. Loop scope and plugin mode affect which loop runs the test, not whether calls overlap.
