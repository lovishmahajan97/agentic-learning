---
id: testclient-plus-pytest-asyncio
module: fastapi-for-ai
name: TestClient + pytest-asyncio
kind: tool
difficulty: core
est_minutes: 15
status: typed-verified
lesson_type: lab
agent_relevance: useful
related:
  - python-for-ai/pytest
  - python-for-ai/pytest-asyncio
  - python-for-ai/test-doubles-mocks-fixtures
  - fastapi-for-ai/dependency-injection
  - fastapi-for-ai/lifespan
  - fastapi-for-ai/integration-tests-with-real-dependencies
  - fastapi-for-ai/shared-httpx-asyncclient
sources:
  - Official docs: FastAPI (Testing, Async Tests, Testing Dependencies with Overrides)
  - Official docs: Starlette (TestClient)
  - Official docs: pytest-asyncio
  - Official docs: HTTPX (Transports, ASGITransport)
---

## TL;DR
Test an agent endpoint in-process with a scripted fake LLM: `TestClient` for synchronous tests,
`httpx.AsyncClient` with `ASGITransport` for `async def` tests. You prove the tool loop, error mapping and
streaming without paying for or waiting on a model.

## For agent builders
- **Why it matters for agents.** Most agent bugs are in your code, not the model: a tool result never
  appended, a loop that does not stop, a provider error turned into a 500, a malformed final stream event.
  A fake LLM that returns scripted tool calls reproduces each one deterministically in milliseconds.
- **Where it shows up in your stack.** `app.dependency_overrides` swapping the LLM client and tool
  registry, `TestClient` for request and streaming tests, `ASGITransport` plus pytest-asyncio when the
  test awaits shared async state such as an agent memory store.
- **What you control.** The seam (inject the model and tools through `Depends`), the scripted responses,
  which loop each test runs on, and when to clear overrides.
- **What you can skip.** anyio portal internals. Know the lifespan-under-`with` rule, the one-loop rule
  and the exact-callable override rule; that prevents almost every agent-test flake.

## Lab
### Task
Make an agent endpoint testable without a model, then make a given suite pass: fix a factory-built
dependency that overrides cannot reach, map provider errors to 502, and write the scripted fake LLM
the tests drive. Time box: 45-75 minutes.
- **The async part:** one test runs on the test's own loop through `ASGITransport` and reads the
  agent's async memory store.
- **Prerequisites:** the dependency injection and lifespan lessons, and pytest fixtures.
- **Setup:** `pip install fastapi httpx pytest pytest-asyncio`; checked with FastAPI 0.161, httpx 0.28,
  pytest 9.1, pytest-asyncio 1.4 (strict mode) and Python 3.12. No model or network is used.
- **Save** the starter as `lab.py` and the tests as `test_lab.py`, then run `pytest -q`.

### Starter code
```python
"""Lab: make an agent endpoint testable without a real model. Needs fastapi and httpx."""
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

class ProviderError(Exception):
    """Raised by the model client on a provider failure (429, 5xx, timeout)."""

class RealLLM:
    def __init__(self, model: str): self.model = model
    async def step(self, messages: list[dict]) -> dict:
        raise RuntimeError("RealLLM called: a test reached the paid provider")

def make_llm(model: str):
    def dep() -> RealLLM:                      # a new closure: tests cannot reference it
        return RealLLM(model)
    return dep

def get_llm() -> RealLLM:                      # the named dependency tests override
    return RealLLM("model-x")

async def weather(city: str) -> str:
    return "18C"

def get_tools() -> dict:
    return {"weather": weather}

class MemoryStore:
    """Async store the agent writes final answers to (stands in for Redis or Postgres)."""
    def __init__(self): self.items: list[tuple[str, str]] = []
    async def save(self, q: str, answer: str): self.items.append((q, answer))
    async def all(self): return list(self.items)

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.memory = MemoryStore()
    yield

app = FastAPI(lifespan=lifespan)
# LAB 2: register an exception handler that turns ProviderError into
# JSONResponse({"error": "upstream_failed"}, status_code=502).

@app.post("/agent")
async def run_agent(body: dict, request: Request,
                    llm=Depends(make_llm("model-x")),      # LAB 1: fix the seam: Depends(get_llm)
                    tools=Depends(get_tools)):
    messages = [{"role": "user", "content": body["q"]}]
    for _ in range(5):                                    # hard step cap
        out = await llm.step(messages)
        if out["type"] == "final":
            await request.app.state.memory.save(body["q"], out["text"])
            return {"answer": out["text"], "steps": len(messages)}
        result = await tools[out["name"]](**out["args"])
        messages += [{"role": "assistant", "tool": out["name"]},
                     {"role": "tool", "content": result}]
    return {"answer": None, "error": "step_limit"}

class ScriptedLLM:
    """LAB 3: a fake model. Pops the next scripted response on each step() call and records a
    copy of the messages it was given in self.seen. If the script runs out, repeat the last item.
    If an item is an Exception instance, raise it instead of returning it."""
    def __init__(self, script: list):
        raise NotImplementedError("lab step 3: ScriptedLLM")
    async def step(self, messages: list[dict]) -> dict:
        raise NotImplementedError("lab step 3: ScriptedLLM.step")

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    try:
        llm = ScriptedLLM([{"type": "tool", "name": "weather", "args": {"city": "Oslo"}},
                           {"type": "final", "text": "It is 18C in Oslo."}])
        app.dependency_overrides[get_llm] = lambda: llm
        with TestClient(app) as client:
            print(client.post("/agent", json={"q": "Weather in Oslo?"}).json())
            print(f"model saw the tool result: {llm.seen[1][-1]}")
    except (NotImplementedError, RuntimeError) as e:
        print(f"not done yet: {e}")
    finally:
        app.dependency_overrides.clear()
```

### Expected output
`python lab.py` with a correct solution prints:

```
{'answer': 'It is 18C in Oslo.', 'steps': 3}
model saw the tool result: {'role': 'tool', 'content': '18C'}
```

### Tests
```python
import httpx, pytest
from fastapi.testclient import TestClient
import lab
from lab import ProviderError, ScriptedLLM, app, get_llm

TOOL = {"type": "tool", "name": "weather", "args": {"city": "Oslo"}}
FINAL = {"type": "final", "text": "It is 18C in Oslo."}

@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()               # never leak a fake into the next test

def use(llm):
    app.dependency_overrides[get_llm] = lambda: llm
    return llm

def test_tool_result_reaches_model_and_loop_stops():
    llm = use(ScriptedLLM([TOOL, FINAL]))
    with TestClient(app) as client:                 # with: lifespan creates app.state.memory
        r = client.post("/agent", json={"q": "Weather in Oslo?"})
    assert r.json() == {"answer": "It is 18C in Oslo.", "steps": 3}
    assert llm.seen[1][-1] == {"role": "tool", "content": "18C"}   # appended before turn 2
    assert len(llm.seen) == 2
    assert llm.seen[0] == [{"role": "user", "content": "Weather in Oslo?"}]  # a copy, not a reference

def test_runaway_tool_loop_hits_step_limit():
    llm = use(ScriptedLLM([TOOL]))                  # only ever asks for tools
    with TestClient(app) as client:
        r = client.post("/agent", json={"q": "loop"})
    assert r.json() == {"answer": None, "error": "step_limit"} and len(llm.seen) == 5

def test_provider_error_maps_to_502():
    use(ScriptedLLM([ProviderError("rate limited")]))
    with TestClient(app) as client:
        r = client.post("/agent", json={"q": "x"})
    assert r.status_code == 502 and r.json() == {"error": "upstream_failed"}

def test_unhandled_bug_is_500_for_clients():
    use(ScriptedLLM([KeyError("bug")]))
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/agent", json={"q": "x"})
    assert r.status_code == 500

@pytest.mark.asyncio
async def test_async_answer_is_saved_to_memory():
    use(ScriptedLLM([FINAL]))
    async with app.router.lifespan_context(app):    # ASGITransport does not run lifespan
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/agent", json={"q": "Oslo?"})
        assert r.status_code == 200
        assert await app.state.memory.all() == [("Oslo?", "It is 18C in Oslo.")]   # same loop
```

On the starter all five fail with `NotImplementedError` from `ScriptedLLM`. With only the fake written,
four fail with `RuntimeError: RealLLM called`, which is the override-missed bug; a correct solution
passes all five.

### Failure scenarios
- **Leave `Depends(make_llm("model-x"))` in place.** The override on `get_llm` is silently ignored and
  `RealLLM` raises. In a real suite this is a slow test or a provider auth error. Debug by printing
  `run_agent`'s dependency callables, or by blocking network in CI so the miss fails fast.
- **Drop the `with` around `TestClient`.** The endpoint fails with `AttributeError` on
  `app.state.memory`, because lifespan never ran.
- **Remove the `autouse` cleanup fixture, then add a test that forgets to call `use()`.** Every test
  here sets its own override, so nothing changes until then; the new test inherits whichever fake ran
  last and passes or fails depending on order. Detect it by running the suite in random order with the
  pytest-randomly plugin.
- **Drop `@pytest.mark.asyncio` from the async test.** In strict mode pytest-asyncio does not run the
  coroutine: pytest 8.4 and later fail it with "async def functions are not natively supported" (older
  versions skipped it with a warning), and no assertion executes.

### Submission
- `lab.py` with the three lab steps done, and `test_lab.py` unchanged.
- A `pytest -q` run showing 5 passed, run at least three times with no event-loop errors.
- One extra test of your own: a script whose tool raises, asserting what the agent returns.
- A short note on which of your own agent's dependencies are injected through `Depends` and which are
  still module globals a test cannot override.
- Evaluation: tests pass unchanged, no test can reach `RealLLM`, and the fake records every prompt.

## Why it exists
- **The behaviour worth testing is mostly not the model.** Request validation, auth, how a provider error
  becomes a status code, whether a streaming endpoint emits a well-formed final event, whether a cached
  path skips the model, whether the agent loop executes a tool call and stops.
- **Starting Uvicorn and sending real HTTP is the wrong tool.** It is slow, needs free ports and makes
  failures hard to debug.
- **Calling the endpoint function directly skips what breaks.** Dependency resolution, Pydantic
  validation, middleware, exception handlers and response serialisation never run.
- **An in-process client gives the full HTTP path with none of the network.** The complication is the
  event loop: a normal pytest test is synchronous and has no loop, your app is async, and a test that
  awaits things (seeding an async database, inspecting an async fake) needs a loop of its own.
- **Objects created on one loop cannot safely be used on another.** `TestClient` hides the loop,
  pytest-asyncio gives you one, and mixing them carelessly produces the most common async-testing errors.

## How it works
### TestClient
`fastapi.testclient.TestClient` is Starlette's test client, built on httpx's request API.
1. `client.post(...)` converts the request into ASGI messages.
2. It runs your app through an anyio portal: a separate thread with its own event loop.
3. Your test stays synchronous, *because* the portal blocks until the app has produced the response.

Two details matter:
- **Lifespan runs only under `with`.** Startup and shutdown run only when the client is used as a context
  manager (`with TestClient(app) as client:`), *because* constructing the client alone does not start the
  app. Without it, anything created in the lifespan (a shared httpx client, a Redis pool, the agent's tool
  registry) is missing and endpoints fail with `AttributeError` on `app.state`.
- **Streaming works.** Consume streaming endpoints with `client.stream(...)` and `iter_lines()`, which is
  how you assert an agent's token and tool-call events arrive in order.

### httpx.AsyncClient + ASGITransport
- **Construct** `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")`.
- **Same loop as the test,** so the test can await other async code before and after, such as reading
  what the agent wrote to an async memory store.
- **No lifespan.** `ASGITransport` does not run lifespan events, so wrap the app with a lifespan manager
  (for example the `asgi-lifespan` package) or enter the app's lifespan yourself.

### pytest-asyncio
- **pytest does not run coroutines by itself;** pytest-asyncio provides the loop.
- **Two modes:** mark tests with `@pytest.mark.asyncio` (strict mode, the default), or set
  `asyncio_mode = "auto"` so every `async def` test and fixture is handled.
- **Async fixtures** use `@pytest_asyncio.fixture` in strict mode.
- **Fresh loop per test by default,** *because* isolation beats speed.
- **Session-scoped async fixtures** (a database engine, a container client) must share a loop with the
  tests that use them, which recent versions express with a `loop_scope` argument. The loop-scope API has
  changed across releases, so follow the docs for your installed version.
- **Alternative:** AnyIO's own pytest plugin (`@pytest.mark.anyio`) when the codebase already uses AnyIO.

### Replacing the model
| Code calls the model through | Replace it with |
|---|---|
| A dependency | `app.dependency_overrides[get_llm] = lambda: FakeLLM()` |
| httpx directly | `httpx.MockTransport` or the `respx` library, which intercept at the transport |

- **Override at the dependency boundary,** *because* that is where the app already expects an injectable
  object.
- **Script the fake for agents.** Give it a list of responses (a tool call, then a final answer) and
  record every prompt it receives, so the test can assert the tool result was appended before the second
  model call.
- **Clear overrides after each test,** or one test's fake leaks into the next.
- **The key is the exact callable in `Depends(...)`.** FastAPI looks up each dependency function in
  `dependency_overrides` while resolving the request. Overriding a lookalike silently does nothing and
  the real client runs: a wrapper, a re-import of a different function, or a dependency produced by a
  factory call such as `Depends(make_llm("gpt"))`, which creates a new closure at import time.

### Exceptions surface differently in tests
- **Unhandled exceptions are re-raised into the test** by default (`raise_server_exceptions=True`),
  instead of becoming a 500, *because* a traceback is more useful than a status code while debugging.
- **`ASGITransport` behaves the same** through `raise_app_exceptions`.
- **Handled exceptions still become responses:** `HTTPException` and registered exception handlers.
- **To assert what a client actually sees** when something unhandled breaks, construct the client with
  `raise_server_exceptions=False`; otherwise the test fails with the exception rather than checking the
  status.

## Diagram
The decision is about event loops: a synchronous test lets `TestClient` run the app on its own portal
loop, while a test that awaits must keep the app on the test's loop through `ASGITransport` (under
pytest-asyncio). Streaming latency needs a real server either way.

```mermaid
flowchart TD
    T["New agent endpoint test"] --> L{"Measuring time to first chunk?"}
    L -->|"yes"| SRV["Real running server, not in-process"]
    L -->|"no"| F["Override the LLM dependency with a fake"]
    F --> Q{"Test awaits objects shared with the app?"}
    Q -->|"no"| TC["with TestClient(app) as client"]
    TC --> PT["App runs on a portal thread loop"]
    PT --> LS1["Lifespan runs via the with block"]
    Q -->|"yes"| AC["AsyncClient plus ASGITransport"]
    AC --> ONE["App runs on the test loop"]
    ONE --> LS2["Lifespan only via asgi-lifespan"]
    LS1 --> CLR["Clear dependency_overrides after the test"]
    LS2 --> CLR
```

## Examples
### A fake LLM, tested sync and async
A fake LLM via dependency override, tested once synchronously with `TestClient` and once asynchronously
with `ASGITransport`. Requires `pytest-asyncio`.

```python
import httpx, pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

class LLM:
    async def complete(self, prompt: str) -> str: raise NotImplementedError

def get_llm() -> LLM: return LLM()

app = FastAPI()

@app.post("/summarise")
async def summarise(body: dict, llm: LLM = Depends(get_llm)):
    if not body.get("text"):
        return {"summary": ""}
    return {"summary": await llm.complete(f"Summarise: {body['text']}")}

class FakeLLM(LLM):
    def __init__(self): self.prompts: list[str] = []
    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt); return "short version"

@pytest.fixture
def fake():
    f = FakeLLM(); app.dependency_overrides[get_llm] = lambda: f
    yield f
    app.dependency_overrides.clear()          # never leak overrides between tests

def test_sync(fake):
    with TestClient(app) as client:          # context manager runs lifespan
        r = client.post("/summarise", json={"text": "long doc"})
    assert r.json() == {"summary": "short version"} and len(fake.prompts) == 1

@pytest.mark.asyncio
async def test_async_skips_model_on_empty(fake):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/summarise", json={"text": ""})
    assert r.json() == {"summary": ""} and fake.prompts == []
```

- **Cost:** both tests exercise routing, dependency injection and serialisation, run in milliseconds and
  cost nothing.
- **The sync test proves** the request reached the fake exactly once through the override.
- **The async test proves a negative:** the empty-input branch never touched the model, which no amount
  of mocking the HTTP layer would show as clearly.
- **Real-model behaviour** belongs in a separate, smaller suite.

### Testing an agent's tool loop with a scripted fake
The fake returns a tool call on the first turn and a final answer on the second. The test asserts the
tool ran once, its result reached the model, and the loop stopped. The message shapes are this app's own,
not a provider's.

```python
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

agent_app = FastAPI()

def get_agent_llm(): raise NotImplementedError        # real client injected in production
def get_tools(): raise NotImplementedError

@agent_app.post("/agent")
async def run_agent(body: dict, llm=Depends(get_agent_llm), tools=Depends(get_tools)):
    messages = [{"role": "user", "content": body["q"]}]
    for _ in range(5):                                # hard step cap
        out = await llm.step(messages)
        if out["type"] == "final":
            return {"answer": out["text"], "steps": len(messages)}
        result = await tools[out["name"]](**out["args"])
        messages += [{"role": "assistant", "tool": out["name"]},
                     {"role": "tool", "content": result}]
    return {"answer": None, "error": "step_limit"}

class ScriptedLLM:
    def __init__(self, script): self.script, self.seen = list(script), []
    async def step(self, messages):
        self.seen.append(list(messages)); return self.script.pop(0)

def test_tool_result_reaches_model_and_loop_stops():
    calls = []
    async def weather(city): calls.append(city); return "18C"
    llm = ScriptedLLM([{"type": "tool", "name": "weather", "args": {"city": "Oslo"}},
                       {"type": "final", "text": "It is 18C in Oslo."}])
    agent_app.dependency_overrides[get_agent_llm] = lambda: llm
    agent_app.dependency_overrides[get_tools] = lambda: {"weather": weather}
    try:
        with TestClient(agent_app) as client:
            r = client.post("/agent", json={"q": "Weather in Oslo?"})
        assert r.json()["answer"] == "It is 18C in Oslo." and calls == ["Oslo"]
        assert llm.seen[1][-1] == {"role": "tool", "content": "18C"}   # appended before turn 2
    finally:
        agent_app.dependency_overrides.clear()
```

- **Add a second script** that only ever returns tool calls and assert `error == "step_limit"`, which
  proves the runaway-loop guard works without spending a token.

### Failure paths: mapped errors and unhandled bugs
The same fake pattern extends to failures. A `FailingLLM` raises your provider-error type, and the test
asserts the endpoint maps it to, say, 502 with your error body. Builds on the first app above.

```python
from fastapi import Request
from fastapi.responses import JSONResponse

class ProviderError(Exception): ...

@app.exception_handler(ProviderError)
async def provider_error(request: Request, exc: ProviderError):
    return JSONResponse({"error": "upstream_failed"}, status_code=502)

class FailingLLM(LLM):
    async def complete(self, prompt: str) -> str: raise ProviderError("rate limited")

class BuggyLLM(LLM):
    async def complete(self, prompt: str) -> str: raise KeyError("bug")

def test_provider_error_maps_to_502():
    app.dependency_overrides[get_llm] = lambda: FailingLLM()
    try:
        with TestClient(app) as client:                      # handled: a normal response
            r = client.post("/summarise", json={"text": "x"})
        assert r.status_code == 502 and r.json() == {"error": "upstream_failed"}
    finally:
        app.dependency_overrides.clear()

def test_unhandled_bug_is_500_for_clients():
    app.dependency_overrides[get_llm] = lambda: BuggyLLM()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:   # see the status
            r = client.post("/summarise", json={"text": "x"})
        assert r.status_code == 500
    finally:
        app.dependency_overrides.clear()
```

### Which client for which test
| Test needs | Use | Lifespan |
|---|---|---|
| Validation, auth, error mapping, response shape | `TestClient` | `with TestClient(app)` |
| Agent tool loop with a scripted fake | `TestClient` | `with TestClient(app)` |
| Streaming format | `TestClient` with `client.stream(...)` | `with TestClient(app)` |
| Await an async fixture shared with the app | `AsyncClient` + `ASGITransport` | lifespan manager |
| Time to first chunk | A real running server | real startup |

## Production patterns
- **One `conftest.py` client fixture per style.** When a suite grows past a handful of files, define a
  sync `client` fixture (`with TestClient(app) as c: yield c`) and an async one, so no test constructs
  clients by hand and forgets lifespan. Monitor suite runtime per fixture.
- **Scripted-fake regression tests for agent incidents.** When an agent misbehaves in production, copy
  the model outputs from the trace into a scripted fake and add a test that replays them, so the loop,
  parsing or tool bug stays fixed. Monitor how many incidents ship with a replay test.
- **Async client with lifespan via `asgi-lifespan`.** When async tests need lifespan-created state, wrap
  the app: `async with LifespanManager(app) as m:` then build the `AsyncClient` on
  `ASGITransport(app=m.app)`. Monitor "attached to a different loop" errors, which mean a fixture's loop
  scope does not match.
- **Autouse override cleanup.** Add an `autouse=True` fixture that yields and then calls
  `app.dependency_overrides.clear()`, so order-dependent passes disappear. Detect leaks by running the
  suite in random order (for example with the `pytest-randomly` plugin).
- **Transport-level fakes for raw httpx calls.** When code calls a provider through httpx rather than an
  injectable dependency, give the shared client an `httpx.MockTransport(handler)` in tests, or use
  `respx`, and return recorded provider payloads, including error bodies. Monitor that no test needs
  network by running the suite with outbound network blocked in CI.
- **A separate, small real-model suite.** Keep fakes for the fast suite and mark real-provider tests with
  a custom marker (for example `@pytest.mark.live`) run on a schedule, not per commit; agent quality
  belongs in evals, not here. Monitor its cost per run and its flake rate.

## Trade-offs
- **`TestClient` simplicity vs loop isolation.** It keeps tests synchronous and simple, but runs the app on
  a different loop in another thread, so it breaks as soon as the test shares async objects with the app;
  then use `ASGITransport`.
- **`ASGITransport` one-loop power vs setup.** It runs everything on one loop and allows awaiting in the
  test, at the cost of async fixtures, a pytest plugin and manual lifespan handling.
- **Fakes vs realism.** Overriding dependencies with fakes gives fast, deterministic tests of your agent
  logic, but proves nothing about whether the real model picks the right tool, which needs evals,
  contract or integration tests.
- **Function vs session loop scope.** Function-scoped event loops isolate tests well; session-scoped loops
  make expensive async fixtures reusable, but state leaks between tests become possible and must be
  cleaned up explicitly.

## Pitfalls
- **`TestClient(app)` without `with`.** Lifespan never runs, and endpoints fail with `AttributeError` on
  `app.state` resources that exist in production.
- **`TestClient` inside an `async def` test that also created an async engine or client.** Symptom:
  "attached to a different loop" or a hang, because two loops share one object.
- **Forgetting to clear `app.dependency_overrides`.** Symptom: a later test passes or fails depending on
  test order.
- **Overriding a different function object from the one in `Depends(...)`.** Symptom: a test that passes
  slowly, needs network, or fails with an authentication error from the real provider. Fix: override the
  exact function object, and block outbound network in CI so the mistake fails fast.
- **A fake that always returns the final answer.** Symptom: the tool-calling branch of the agent loop has
  no coverage and breaks in production first. Fix: script at least one tool call, one tool error and one
  step-limit run.
- **Expecting a 500 from an unhandled error with default `TestClient` settings.** The test errors with the
  app's exception instead of seeing the status code. Fix: `raise_server_exceptions=False`.
- **Missing the asyncio marker in strict mode.** The coroutine is never awaited; depending on the pytest
  version the test is skipped with a warning or fails, but its assertions never run.
- **Asserting on time-to-first-chunk through an in-process transport.** Buffering differs from a real
  server and proxy, so streaming latency must be measured against a running server.

## When to use
- **Use `TestClient` as the default** for agent endpoint tests: validation, auth, error mapping, the tool
  loop, response shape and streaming format, with the model replaced by a fake.
- **Switch to `AsyncClient` plus `ASGITransport`** under pytest-asyncio when the test must await other
  async work or share async fixtures with the app.
- **Neither replaces** integration tests against real databases or a recorded provider, agent evals, nor
  load tests against a real server.
- **Measure** suite runtime and flakiness.
- **Interviews:** expect questions on lifespan in tests, dependency overrides, and why loop mismatches
  happen.

## Role lens
- **Engineer:** Writes a fixture layer with fake LLM, overrides and clients, and chooses sync or async
  tests per case without mixing loops.
- **Architect:** Requires that every external dependency enters through an overridable seam, so the
  test strategy does not depend on monkeypatching module globals.
- **FDE:** Adds a regression test reproducing a customer's failing request with a fake that returns
  the exact provider response that caused it, before shipping the fix.

## Check yourself
**Q:** An async test seeds data through an async SQLAlchemy engine fixture, then calls the API with
`TestClient`, and fails intermittently with "Future attached to a different loop". Why, and what is
the fix?
**A:** The engine fixture created its connection pool on pytest-asyncio's event loop. `TestClient`
runs the app on a different loop in a portal thread, so when the endpoint uses the same engine it
awaits futures that belong to the test's loop. The fix is to keep everything on one loop: use
`httpx.AsyncClient` with `ASGITransport` inside the async test, run the lifespan with a manager, and
make the engine fixture's loop scope match the tests that use it. Alternatively, keep the test fully
synchronous and let the app create its own engine in the lifespan.

## Interview questions
### Why test a FastAPI app through an in-process client instead of calling endpoint functions directly?
- **Calling the function skips what breaks:** dependency resolution, Pydantic validation, middleware,
  exception handlers and response serialisation never run.
- **Starting Uvicorn is the other extreme:** slow, needs free ports and makes failures hard to debug.
- **An in-process client** sends ASGI messages straight into the app, so you get the full HTTP path in
  milliseconds with no network.

### How would you test an agent endpoint's tool-calling loop without calling a real model?
- **Inject the model and tools through dependencies,** then override them with a scripted fake LLM that
  returns a tool call, then a final answer, and a fake tool that records its arguments.
- **Assert the mechanics:** the tool ran once with the right arguments, its result was appended before the
  second model call, and the loop stopped on the final answer.
- **Add scripts for the bad paths:** a tool error the agent must handle and an endless tool-call script that
  must hit the step limit.

### A test overrides get_llm with a fake, but the suite is slow and fails with an auth error from the real provider. What happened?
- **The override key is not the callable in `Depends(...)`.** FastAPI looks up each dependency function
  in `dependency_overrides`, so overriding a wrapper or a re-imported lookalike silently does nothing.
- **A common cause is a factory** such as `Depends(make_llm("gpt"))`, which creates a new closure at import
  time that the test cannot reference.
- **Fix:** override the exact function object, or refactor the factory into a named dependency, and block
  outbound network in CI so this fails fast.

### When would you pick TestClient over AsyncClient with ASGITransport, and what does each cost?
- **`TestClient` by default:** tests stay synchronous and lifespan runs under `with`, but the app runs on a
  different loop in a portal thread.
- **`ASGITransport` when the test awaits** async fixtures shared with the app, because one loop avoids
  "attached to a different loop" errors.
- **Its cost:** a pytest plugin, async fixtures and manual lifespan handling, since `ASGITransport` does
  not run lifespan events.

### An agent bug was reported from production. How do you turn the trace into a regression test?
- **Copy the model outputs from the trace** (tool calls and final text, in order) into a scripted fake, and
  the tool results into fake tools, so the run replays deterministically.
- **Assert what went wrong is now right:** for example the malformed tool arguments are rejected with a
  tool error, or the loop stops instead of repeating.
- **Keep it in the fast suite,** because it costs no tokens; judging whether the model now chooses better
  belongs in the eval set.

### How do you test that an unhandled bug in an endpoint returns 500 to clients?
- **By default the exception is re-raised into the test** (`raise_server_exceptions=True`), so the test
  errors with the traceback instead of seeing a status code.
- **Construct the client with `raise_server_exceptions=False`** and assert `status_code == 500`;
  `ASGITransport` has the equivalent `raise_app_exceptions`.
- **Handled errors need no flag:** an `HTTPException` or a registered handler mapping `ProviderError` to 502
  is a normal response.

## Quiz
### Q: Why do endpoints that read `app.state.http` fail with `AttributeError` in a test using `client = TestClient(app)`?
- [ ] `TestClient` strips `app.state` for isolation
- [x] Lifespan startup only runs when `TestClient` is used as a context manager
- [ ] `app.state` is only populated under Uvicorn
- [ ] Dependency overrides reset `app.state`
> Startup code in the lifespan is triggered by entering `with TestClient(app)`. Without it, nothing
> set during startup exists. Overrides affect dependencies, not state set at startup.

### Q: When is `httpx.AsyncClient(transport=ASGITransport(app=app))` the better choice over `TestClient`?
- [ ] When the endpoint is declared with `def` instead of `async def`
- [ ] When you need lifespan events to run automatically
- [x] When the test itself must await async fixtures or objects shared with the app on the same loop
- [ ] When you want to test against a real network socket
> `ASGITransport` runs requests on the test's own loop, so shared async resources work. It does not run
> lifespan automatically, and neither client uses a real socket.

### Q: What is the cleanest way to stop tests from calling a paid LLM provider?
- [ ] Set the API key environment variable to an empty string
- [ ] Monkeypatch the `requests` library globally
- [x] Override the dependency that provides the LLM client with a fake via `app.dependency_overrides`
- [ ] Mark all LLM tests as expected failures
> The dependency is the seam the app already uses, so overriding it swaps the real client for a
> controllable fake. An empty key just makes calls fail, and patching another library misses httpx.

## Use cases & projects
### Use cases
- **Regression tests for the tool loop.** A scripted fake LLM returns a tool call, then a final answer,
  proving the loop executes the tool, appends its result and stops.
- **Provider error mapping.** Scripting a timeout or 429 from the fake proves the endpoint returns the
  status code and error body you promised, not a 500.
- **Streaming contract tests.** `TestClient` reads the event stream and asserts the final event is
  well formed, so the frontend never hangs waiting for it.
- **Fast CI for agent services.** In-process tests run in milliseconds with no model spend, so every pull
  request can run the full suite.

### Mini project
**Test an agent endpoint with a scripted fake LLM (1-2 hours).**
- **Goal:** cover an agent endpoint's loop, error path and stream without calling a real model.
- **Steps:** inject the LLM client and tool registry through `Depends`; write a fake that returns a
  scripted list of responses; override it with `app.dependency_overrides` in a fixture that clears
  overrides afterwards; write sync tests with `TestClient` (inside `with` so lifespan runs) and one
  `async def` test using `httpx.AsyncClient` with `ASGITransport` that inspects an async fake memory store.
- **Done when:** tests cover tool-call-then-answer, a max-steps stop, a provider error mapped to the right
  status, and the final stream event, and the suite passes repeatedly with no event-loop errors.
