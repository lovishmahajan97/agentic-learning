---
id: testclient-plus-pytest-asyncio
module: fastapi-for-ai
name: TestClient + pytest-asyncio
kind: tool
difficulty: core
est_minutes: 15
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
FastAPI apps are ASGI callables, so tests can call them in-process without a server. `TestClient`
does it from ordinary synchronous tests by running the app on its own event loop in a helper thread;
`httpx.AsyncClient` with `ASGITransport`, under pytest-asyncio, does it from `async def` tests on the
test's own loop. Pick by whether the test itself must await things, and replace the LLM with a
dependency override so tests are fast, free and deterministic.

## Why it exists
The behaviour worth testing in an AI endpoint is mostly not the model: request validation, auth,
how a provider error becomes a status code, whether a streaming endpoint emits a well-formed final
event, whether a cached path skips the model. Testing that by starting Uvicorn and sending real HTTP
is slow, needs free ports and makes failures hard to debug. Testing it by calling the endpoint
function directly skips exactly the layers that break: dependency resolution, Pydantic validation,
middleware, exception handlers and response serialisation.

An in-process client gives you the full HTTP path with none of the network. The complication is the
event loop. A normal pytest test is synchronous and has no loop; your app is async. If the test also
needs to await things, such as seeding an async database or inspecting an async fake, it needs a loop
of its own, and objects created on one loop cannot safely be used on another. That is the split this
lesson is about: `TestClient` hides the loop from you, pytest-asyncio gives you one, and mixing them
carelessly produces the most common async-testing errors.

## How it works
### TestClient
`fastapi.testclient.TestClient` is Starlette's test client, built on httpx's request API. When you
call `client.post(...)` it converts the request into ASGI messages and runs your app through an anyio
portal: a separate thread with its own event loop. Your test stays synchronous, *because* the portal
blocks until the app has produced the response. Two details matter. Lifespan startup and shutdown run
only when the client is used as a context manager (`with TestClient(app) as client:`), *because*
constructing the client alone does not start the app; without it, anything created in the lifespan,
such as a shared httpx client or a Redis pool, is missing and endpoints fail with `AttributeError` on
`app.state`. And streaming endpoints can be consumed with `client.stream(...)` and `iter_lines()`.

### httpx.AsyncClient + ASGITransport
For an `async def` test, create `httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
base_url="http://test")`. Requests run on the test's own event loop, so the test can await other
async code before and after. `ASGITransport` does not run lifespan events, so wrap the app with a
lifespan manager (for example the `asgi-lifespan` package) or enter the app's lifespan yourself.

### pytest-asyncio
pytest does not run coroutines by itself. pytest-asyncio provides the loop: mark tests with
`@pytest.mark.asyncio` (strict mode, the default) or set `asyncio_mode = "auto"` so every `async def`
test and fixture is handled. Async fixtures use `@pytest_asyncio.fixture` in strict mode. Each test
gets a fresh loop by default, *because* isolation beats speed; a session-scoped async fixture (a
database engine, a container client) must share a loop with the tests that use it, which recent
versions express with a `loop_scope` argument. The loop-scope API has changed across releases, so
follow the docs for your installed version. AnyIO's own pytest plugin (`@pytest.mark.anyio`) is the
alternative when the codebase already uses AnyIO.

### Replacing the model
`app.dependency_overrides[get_llm] = lambda: FakeLLM()` swaps the real client for a fake at the
dependency boundary, *because* that is where the app already expects an injectable object. For code
that calls httpx directly, `httpx.MockTransport` or the `respx` library intercepts requests at the
transport and returns canned responses. Clear overrides after each test, or one test's fake leaks into
the next.

## Example
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

Both tests exercise routing, dependency injection and serialisation, run in milliseconds and cost
nothing. Real-model behaviour belongs in a separate, smaller suite.

## Trade-offs
- `TestClient` keeps tests synchronous and simple, but runs the app on a different loop in another
  thread, so it breaks as soon as the test shares async objects with the app; then use `ASGITransport`.
- `ASGITransport` runs everything on one loop and allows awaiting in the test, at the cost of async
  fixtures, a pytest plugin and manual lifespan handling.
- Overriding dependencies with fakes gives fast, deterministic tests of your logic, but proves nothing
  about the real provider's behaviour, which needs contract or integration tests.
- Function-scoped event loops isolate tests well; session-scoped loops make expensive async fixtures
  reusable, but state leaks between tests become possible and must be cleaned up explicitly.

## Pitfalls
- Constructing `TestClient(app)` without `with`; lifespan never runs and endpoints fail with
  `AttributeError` on `app.state` resources that exist in production.
- Using `TestClient` inside an `async def` test that also created an async engine or client; the
  symptom is "attached to a different loop" or a hang, because two loops share one object.
- Forgetting to clear `app.dependency_overrides`; a later test passes or fails depending on test order.
- Missing the asyncio marker in strict mode; the coroutine is never awaited, and depending on the
  pytest version the test is skipped with a warning or fails, but its assertions never run.
- Asserting on time-to-first-chunk through an in-process transport; buffering behaviour differs from
  a real server and proxy, so streaming latency must be measured against a running server.

## When to use
Use `TestClient` as the default for endpoint tests: validation, auth, error mapping, response shape
and streaming format, with the model replaced by a fake. Switch to `AsyncClient` plus `ASGITransport`
under pytest-asyncio when the test must await other async work or share async fixtures with the app.
Neither replaces integration tests against real databases or a recorded provider, nor load tests
against a real server. Track suite runtime and flakiness. In interviews, expect questions on lifespan
in tests, dependency overrides, and why loop mismatches happen.

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
