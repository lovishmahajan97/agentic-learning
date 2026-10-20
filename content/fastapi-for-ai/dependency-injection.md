---
id: dependency-injection
module: fastapi-for-ai
name: Dependency injection (Depends)
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
related:
  - python-for-ai/dependency-injection-and-interface-boundaries
  - fastapi-for-ai/request-scoped-vs-application-scoped-resources
  - fastapi-for-ai/lifespan
  - fastapi-for-ai/api-keys-oauth2-jwt
  - fastapi-for-ai/testclient-plus-pytest-asyncio
  - python-for-ai/test-doubles-mocks-fixtures
  - python-for-ai/context-managers-resource-cleanup
  - fastapi-for-ai/async-database-access-and-connection-pooling
sources:
  - Official docs: FastAPI
---

## TL;DR
`Depends` lets an endpoint declare what it needs — an authenticated principal, a DB session, the LLM
client, a tenant's config — as typed parameters. FastAPI resolves them per request, caches each within
the request, runs their cleanup after the handler, and lets tests swap any of them with
`dependency_overrides`. It replaces global singletons and copy-pasted auth checks with a tree of small
callables, which is what makes an AI service testable without calling the real model.

## Why it exists
Every endpoint in an AI service needs the same things: who is calling, which tenant they belong to, a
client for the model provider, a retrieval index, a database session, a request ID for logs. The first
version of the code gets these from module-level globals and helper calls at the top of each handler.
That fails in three predictable ways. Tests cannot replace the globals cleanly, so unit tests hit the
real provider or the suite monkeypatches half the module. Auth and quota checks are duplicated and one
route forgets them. And resources with a lifecycle — a session that must be closed, a transaction that
must be committed or rolled back — are cleaned up inconsistently, usually with a `try/finally` that one
handler lacks.

"Write a decorator" fixes duplication but not typing or testing, and decorators cannot easily pass
values into the handler. "Use a DI container library" works but adds a second configuration language
and runs outside the framework's request model, so it knows nothing about request scope, OpenAPI
security schemes or the thread pool.

FastAPI's `Depends` is function-level DI that is part of the request pipeline. Dependencies are
ordinary callables with their own typed parameters, which can themselves be dependencies, so the
framework builds a tree per route and resolves it per request *because* the request is the natural
unit of scope for auth, sessions and tracing. Generator dependencies (`yield`) attach cleanup to the
same tree *because* teardown must run even when the handler raises. `dependency_overrides` exists
*because* tests need a seam to replace the model client without touching production code. Resolved
values appear in OpenAPI when they carry security metadata, so the documentation follows the code.

## How it works
### Declaring and resolving
A dependency is any callable: a function, an `async def`, a class, or an object with `__call__`. The
endpoint declares `client: Annotated[LLMClient, Depends(get_llm)]`. At request time FastAPI inspects
the signature, resolves `get_llm`'s own parameters (path, query, header and body parameters, or further
`Depends`), calls it, and passes the result in. Resolution is a depth-first walk of the tree, leaves
first, so an endpoint depending on `get_user`, which depends on `get_token`, which reads the
`Authorization` header, receives a fully built user object.

Siblings are resolved one after another, not concurrently, even when both are `async def`. Two
independent dependencies that each await a 200 ms lookup add 400 ms before the handler starts. If
they must overlap, make one dependency that runs both in a task group and returns the pair.

### Per-request caching
If the same dependency appears more than once in one request's tree — `get_settings` under both
`get_llm` and `get_retriever` — it is called once and the value reused, *because* most dependencies are
deterministic for a request and calling them twice would double DB lookups or token verifications.
`Depends(fn, use_cache=False)` opts out for dependencies whose repeated evaluation is the point (a
fresh timestamp, a new sub-span).

### Sync and async
`async def` dependencies run on the event loop; `def` dependencies run in the thread pool, exactly like
endpoints. A dependency that does blocking work (verifying a JWT with a sync library, opening a sync DB
session) should be `def`, or it stalls the loop for every request in the process.

### Cleanup with yield
A dependency written as a generator runs up to `yield`, hands the value to the handler, and resumes
after the handler finishes. Exit code runs in reverse order of setup, and an exception raised in the
endpoint is re-raised at the `yield` point, so `try/except/finally` around it can roll back a
transaction and still close the session. Exactly when the exit code runs relative to sending the
response bytes has changed between FastAPI versions, so never use a yielded resource from a background
task and never rely on teardown having finished before the client sees the response.

### Parameterised dependencies
A dependency is looked up by the callable object, so an *instance* with `__call__` gives one class
many configurations: `Depends(RateLimit(per_minute=60))` on the chat route, `Depends(RateLimit(5))`
on an expensive batch route. Each instance is a distinct dependency for caching and for
`dependency_overrides`, *because* the key is the object itself; to override it in tests, keep a
reference to the instance you registered.

### Side-effect dependencies
`dependencies=[Depends(verify_api_key)]` on a route, a router or the whole app runs the dependency
without binding its return value, which is how you enforce auth or quotas on an entire router without
threading a parameter through every handler.

### Security dependencies
`Security(get_user, scopes=["chat:write"])` is `Depends` plus scope metadata; combined with
`OAuth2PasswordBearer`, `APIKeyHeader` or `HTTPBearer` it also emits the security scheme into OpenAPI,
so the docs page shows the lock icon and clients know what to send.

### Overrides
`app.dependency_overrides[get_llm] = lambda: FakeLLM()` replaces a dependency everywhere it is used, for
as long as the entry exists. Because dependencies are identified by the callable object, the override
reaches nested uses too. This is the seam for running an AI service's tests against a canned model, a
SQLite session or a fixed tenant.

### Where dependencies meet lifespan
Dependencies do not create expensive resources; they *fetch* them. The provider client, model, engine
or index is created once in lifespan and stored on `app.state`; a dependency reads
`request.app.state.llm` and returns it, so every handler gets the shared object through the same typed,
overridable interface. Request-scoped objects (a session, a per-tenant configuration, a trace span)
are created inside the dependency and torn down after `yield`.

## Example
An API-key dependency, a shared LLM client fetched from `app.state`, a yield dependency holding a
per-request spend cap (here $0.50) whose spend is recorded even when the handler raises a 429,
and the test override that keeps the suite from spending tokens. `get_tenant` appears twice in the tree (directly and under `budget`)
but runs once per request.

```python
from typing import Annotated
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.testclient import TestClient

app = FastAPI()
TENANTS = {"key-acme": {"id": "acme", "max_request_usd": 0.50}}

def get_tenant(x_api_key: Annotated[str, Header()]) -> dict:
    tenant = TENANTS.get(x_api_key)
    if tenant is None:
        raise HTTPException(401, "invalid api key")
    return tenant

def get_llm(request: Request):
    return request.app.state.llm       # created once in lifespan (not shown); fetched per request

class Budget:
    def __init__(self, limit): self.limit, self.spent = limit, 0.0
    def charge(self, usd):
        self.spent += usd
        if self.spent > self.limit: raise HTTPException(429, "budget exceeded")

async def budget(tenant: Annotated[dict, Depends(get_tenant)]):
    b = Budget(tenant["max_request_usd"])     # request-scoped: fresh per request
    try:
        yield b
    finally:
        print("record spend", tenant["id"], b.spent)   # persist to the ledger; runs even on 429

@app.post("/chat")
async def chat(prompt: str, tenant: Annotated[dict, Depends(get_tenant)],
               b: Annotated[Budget, Depends(budget)], llm=Depends(get_llm)):
    text, cost = await llm.complete(prompt)
    b.charge(cost)                  # post-hoc; a real cap also pre-checks an estimated cost
    return {"tenant": tenant["id"], "text": text}

class FakeLLM:                                       # test seam: no production code changes
    async def complete(self, prompt): return f"echo: {prompt}", 0.001
app.dependency_overrides[get_llm] = lambda: FakeLLM()
with TestClient(app) as c:
    r = c.post("/chat", params={"prompt": "hi"}, headers={"x-api-key": "key-acme"})
    assert r.json()["text"] == "echo: hi"
```

## Trade-offs
- Typed, per-request resolution makes every handler's needs explicit and testable, but deep dependency
  trees hide the cost of each request (three DB lookups behind one parameter) until you trace them.
- Per-request caching avoids repeated work within a request and flips into a bug when a dependency is
  meant to produce fresh values each call, which is what `use_cache=False` is for.
- `dependency_overrides` gives a zero-boilerplate test seam, but because overrides are global on the
  app object, a test that forgets to clear them leaks a fake into the next test.
- Yield dependencies tie cleanup to the request, which is the right scope for sessions and spans, but
  their teardown timing relative to the response has shifted across versions, so they are the wrong
  tool for anything a background task needs.

## Pitfalls
- Creating the expensive object inside the dependency (`httpx.AsyncClient()` or a model load per
  request); symptom: connection pools never warm up, TLS handshakes on every call, memory climbing with
  concurrency.
- Doing blocking work in an `async def` dependency, such as sync JWT verification with a network JWKS
  fetch; symptom: the whole process stalls during login spikes, not just the authenticating requests.
- Using a yield dependency's DB session inside a `BackgroundTasks` function; symptom: intermittent
  "session is closed" errors whose frequency depends on timing and FastAPI version.
- Forgetting `dependencies=[Depends(auth)]` on a new router; symptom: one unauthenticated route found
  in a pentest, because auth was attached per route instead of per router.
- Leaving `app.dependency_overrides` set after a test; symptom: a later test passes against the fake
  model locally and fails when CI runs the files in a different order.

## When to use
Use `Depends` for anything a handler needs that is not part of the request body: authentication and
authorisation, tenant resolution, sessions and transactions, access to shared clients and models,
per-request budgets, pagination parameters, feature flags. Put enforcement-only dependencies at router
level. Keep dependencies small and composable, and have them fetch rather than build long-lived
resources. In tests, override at the boundary where the real world starts: the LLM client, the vector
store, the database. Count dependency calls per request when latency is unexplained. In interviews,
the questions are "how would you test this endpoint without calling the model?" and "where does the
DB session get closed if the handler raises?"; `dependency_overrides` and `yield` with `try/finally`
are the answers.

## Role lens
- **Engineer:** Writes auth, session and client dependencies, uses `Annotated` types for them, and
  overrides the model client in tests so the suite never spends tokens.
- **Architect:** Defines the dependency boundaries every service shares — principal, tenant, budget,
  tracing — so teams compose them rather than reinvent them.
- **FDE:** Swaps a customer-specific auth or data-source dependency into an existing service without
  forking handlers, using overrides to demo against sandbox systems.

## Check yourself
**Q:** A service has `get_db` (yield: opens a session, commits on success, rolls back on exception),
`get_user` (depends on `get_db`), and a `/generate` endpoint depending on both plus `get_llm`. A
teammate adds a background task that writes the generation result using the session from `get_db`,
and tests now hit the real provider. Explain what is wrong and how you would restructure.
**A:** The background task runs after the handler returns, while the session's lifetime ends after
`yield`; depending on FastAPI version the session may already be closed when the task runs, giving
intermittent errors, and in any case a request-scoped resource must not outlive the request. The task
should open its own session, or the write should move into the handler before returning, or the work
should go to a durable queue. Tests hitting the provider means `get_llm` is not overridden: register
`app.dependency_overrides[get_llm]` with a fake in a fixture and clear it on teardown. Note that
`get_db` is resolved once per request thanks to caching, so `get_user` and the handler share one
session and one transaction, which is the intended scope.

## Quiz
### Q: `get_settings` is a dependency of both `get_llm` and `get_retriever`, and an endpoint depends on both. How many times does `get_settings` run per request by default?
- [ ] Twice, once per dependent
- [x] Once; the result is cached for the rest of that request
- [ ] Once per worker process, at startup
- [ ] Zero times until a handler accesses the value
> FastAPI caches a dependency's result within a single request when the same callable appears multiple
> times; `use_cache=False` disables this for dependencies that must run every time.

### Q: Where should an `httpx.AsyncClient` for the model provider be created, and what should the dependency do?
- [ ] Created inside the dependency on every request, so each request has a clean client
- [x] Created once in lifespan and stored on `app.state`; the dependency returns that shared instance
- [ ] Created as a module-level global at import time
- [ ] Created in a `BaseHTTPMiddleware` and attached to the request
> The client owns a connection pool that must persist across requests; the dependency's job is to
> provide typed, overridable access to the shared object, not to build it.

### Q: How do tests replace the real LLM client dependency without modifying production code?
- [ ] Monkeypatch `httpx.AsyncClient.post` globally
- [ ] Set an environment variable that the dependency checks
- [x] Register a fake in `app.dependency_overrides[get_llm]` and clear it afterwards
- [ ] Subclass `FastAPI` and override `__call__`
> Overrides are keyed by the dependency callable and apply everywhere it is used, including nested
> dependencies, which makes them the designated seam for test doubles.
