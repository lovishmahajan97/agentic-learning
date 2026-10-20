---
id: routers
module: fastapi-for-ai
name: Routers (APIRouter)
kind: pattern
difficulty: intro
est_minutes: 11
status: fact-checked
related:
  - fastapi-for-ai/dependency-injection
  - fastapi-for-ai/middleware-ordering
  - fastapi-for-ai/openapi-docs
  - fastapi-for-ai/api-keys-oauth2-jwt
  - fastapi-for-ai/httpexception-handling
  - backend-fundamentals/api-versioning-and-backward-compatibility
  - backend-fundamentals/rest-graphql-api-design
sources:
  - Official docs: FastAPI
  - Official docs: Starlette
---

## TL;DR
An `APIRouter` is a group of path operations that you declare in its own module and attach to the app
with `include_router(prefix=..., tags=..., dependencies=...)`. It exists so a growing AI service (chat,
embeddings, admin, eval endpoints) can be split by feature, versioned by prefix, and protected by
group-level dependencies, while still producing one app, one route table and one OpenAPI document.

## Why it exists
A FastAPI service usually starts as one `main.py` with `@app.post("/chat")`. Within a few months it has
chat, completions, embeddings, document upload, retrieval debugging, an admin surface for prompt
versions and a `/v2` of the chat API. Keeping all of that on the single `app` object forces every module
to import `app`, which creates circular imports (the app imports routes, the routes import the app) and
makes it impossible to test a feature in isolation.

The naive fixes fail in specific ways. Repeating `"/v1/admin"` and `Depends(require_admin)` on every
decorator works until someone adds one admin route and forgets the dependency, which is an
authorization hole that no test catches because nothing is broken from the happy path. Splitting into
separate FastAPI apps per feature and mounting them gives isolation, but each mounted app has its own
OpenAPI document, its own exception handlers and its own additional middleware stack, so clients and
generated SDKs see several APIs instead of one. Routers are the middle ground: modular declaration, shared
cross-cutting settings applied once per group, and a single merged application at runtime.

## How it works
### Declaration and inclusion
You create `router = APIRouter()` in a feature module and decorate functions with `@router.post(...)`
exactly as you would with `@app`. Nothing is served yet: the router is only a list of route
definitions. `app.include_router(router, prefix="/v1/chat", tags=["chat"])` walks that list and adds
each route to the app's route table with the prefix prepended, the tags merged and any extra
dependencies or `responses` attached. Because inclusion copies routes at that moment, routes added to a
router *after* it was included are not served; define everything first, include last (usually in an
app factory).

### What a router can carry
`prefix`, `tags`, `dependencies`, `responses`, `default_response_class`, `deprecated` and
`include_in_schema` can be set either on `APIRouter(...)` or on `include_router(...)`, and they stack.
Dependencies are the important one: `dependencies=[Depends(require_api_key)]` on the router runs that
dependency for every route in the group, before the endpoint, so authorization is a property of the
group rather than of each author's memory. Order of resolution is app-level dependencies, then router
dependencies (outer router first, then nested), then the route's own.

### Nesting
A router can include another router: `v1.include_router(chat.router, prefix="/chat")` then
`app.include_router(v1, prefix="/v1")`. This is how versioning is usually organised: each version is a
router built from feature routers, so `/v2/chat` can reuse the embeddings router unchanged while
replacing the chat one.

### What a router cannot carry
Middleware and exception handlers belong to the app (Starlette applies them to the whole ASGI stack),
so there is no `router.add_middleware`. If you need per-group behaviour that must wrap the request,
such as timing or a request-size check, express it as a router dependency, or mount a separate
sub-application with `app.mount` and accept that it has its own docs and handlers. Note the direction of
isolation: the parent app's middleware still wraps a mounted sub-app (it sits outside the routing step),
while the sub-app's own middleware applies only to requests under its mount path.

### Matching order
The app's route table is checked in insertion order and the first path that matches wins. Path
matching happens before parameter validation, so `/models/{model_id}` declared before `/models/latest`
captures `latest` as an id and then fails validation (422) or looks up a model called "latest".
Literal paths must be registered before parameterised siblings, including across routers, which makes
the order of `include_router` calls part of your API's behaviour.

## Example
A small AI service split into feature routers, with versioning by prefix and an admin group protected
once at the router level.

```python
# app/routers/chat.py
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["chat"])

class ChatIn(BaseModel):
    messages: list[dict]
    model: str = "default"

@router.post("/completions")
async def complete(body: ChatIn) -> dict:
    return {"model": body.model, "content": "..."}

# app/routers/admin.py
from fastapi import APIRouter, Depends, Header, HTTPException

async def require_admin(x_api_key: str = Header()):
    if x_api_key != "admin-key-from-settings":      # illustrative; load from settings in real code
        raise HTTPException(status_code=403, detail="admin only")

router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin)])

@router.get("/prompts/active")          # literal path first
async def active_prompt() -> dict:
    return {"version": "v14"}

@router.get("/prompts/{version}")       # parameterised sibling second
async def get_prompt(version: str) -> dict:
    return {"version": version}

# app/main.py
from fastapi import APIRouter, FastAPI
from app.routers import admin, chat

def create_app() -> FastAPI:
    v1 = APIRouter(prefix="/v1")
    v1.include_router(chat.router, prefix="/chat")
    v1.include_router(admin.router, prefix="/admin")
    app = FastAPI(title="assistant-api")
    app.include_router(v1)              # include after all routes are declared
    return app
```

`POST /v1/chat/completions` is public; every `/v1/admin/...` route requires the header without any
admin endpoint mentioning it. Swapping the two prompt routes would make `/v1/admin/prompts/active`
return version `"active"`, a bug that only shows up when someone calls the literal path.

## Trade-offs
- Router-level dependencies make security a group property, but they run on every route in the
  group, so an expensive dependency (a DB lookup) becomes a per-request tax for routes that never needed it.
- One merged app gives one OpenAPI document and one SDK, but it also means one middleware stack; when
  a group genuinely needs different middleware, a mounted sub-app is the better tool despite split docs.
- Versioning by router prefix is cheap and visible, but it duplicates handlers when versions diverge
  slightly; once differences are small and many, version inside the schema or handler instead.
- Splitting by feature keeps modules small, but too many tiny routers scatter related logic and make
  the inclusion order (and thus matching) harder to reason about.

## Pitfalls
- Declaring a parameterised path before a literal sibling; the symptom is a 422 or a "not found" for
  a route that clearly exists, such as `/models/latest`.
- Adding routes to a router after `include_router` ran (for example from a plugin imported later); the
  symptom is a 404 locally that disappears when import order changes.
- Putting the auth dependency on individual routes instead of the router; the symptom is a newly added
  endpoint that is reachable without credentials and passes every test that only checks the happy path.
- Doubling prefixes by setting `prefix="/chat"` on both the router and `include_router`; the symptom
  is routes served at `/chat/chat/...` and broken client calls.
- Expecting `@router.exception_handler` or router middleware to exist; handlers and middleware must be
  registered on the app, so per-router error formatting silently does not happen.

## When to use
Use routers as soon as a service has more than one feature area or more than one audience (public API,
internal admin, eval harness). Organise by feature, not by HTTP verb, and assemble everything in an
app factory so tests can build the app with overridden dependencies. Put authentication, tenant
resolution and rate-limit dependencies on routers so they cannot be forgotten. Reach for `app.mount`
instead only when a group needs its own middleware, its own docs, or is a different framework entirely
(a Gradio demo, a metrics app). In code review, check inclusion order and literal-before-parameter
paths; in interviews, routers are the vocabulary for "how would you structure a FastAPI service".

## Role lens
- **Engineer:** Splits endpoints into feature routers, attaches auth and tenant dependencies at router
  level, and writes an app factory that tests can call with dependency overrides.
- **Architect:** Decides the URL and versioning scheme, which groups share one app versus become mounted
  sub-apps or separate services, and where authorization boundaries sit.
- **FDE:** Adds a customer-specific router (an integration webhook or a tenant admin view) behind its own
  prefix and dependency without forking or destabilising the core service.

## Check yourself
**Q:** Your team wants the `/internal/eval` endpoints to use a different authentication scheme and to log
full request bodies, while the public API must never log bodies. How do you structure this, and why is a
router alone not enough?
**A:** Authentication can be a router-level dependency, so the eval router gets `Depends(require_service_token)`
and the public router gets its API-key dependency; that part routers handle well. Body logging is
different: it wraps the request, which is middleware territory, and middleware is app-wide, so a router
cannot scope it. Options are a router dependency that reads and logs the body only for that group
(acceptable for small bodies), or mounting the eval surface as a separate sub-app with its own logging
middleware, accepting separate OpenAPI docs. Given the privacy stakes, I would mount it, or move it to a
separate internal service, so the public stack cannot accidentally inherit body logging.

## Quiz
### Q: Why does `GET /v1/models/latest` return 422 in a service that defines both `/v1/models/latest` and `/v1/models/{model_id}` with `model_id: int`?
- [ ] `latest` is a reserved word in FastAPI path parameters
- [x] `/v1/models/{model_id}` was registered first and matched before the literal route was tried
- [ ] The router prefix was applied twice so the literal route lives elsewhere
- [ ] Routers sort routes alphabetically, putting `{model_id}` ahead of `latest`
> Routes are matched in insertion order and path matching precedes validation, so the parameterised route
> captures `latest` and then fails integer validation. Registering the literal path first fixes it.

### Q: You need every admin endpoint to require an admin token. What is the most robust approach?
- [ ] Add `Depends(require_admin)` to each admin route decorator
- [ ] Add an app-wide middleware that checks paths starting with `/admin`
- [x] Set `dependencies=[Depends(require_admin)]` on the admin router or its `include_router` call
- [ ] Check the token inside each handler's body before doing work
> A router-level dependency applies to every current and future route in the group, so a new endpoint
> cannot forget it. Path-prefix middleware works but duplicates routing logic and is easy to desynchronise.

### Q: What is the main consequence of using `app.mount("/eval", eval_app)` instead of `include_router`?
- [ ] Mounted apps cannot use Pydantic models
- [ ] Mounted routes are slower because each request is proxied over HTTP
- [x] The mounted app has its own middleware, exception handlers and OpenAPI document
- [ ] Mounted apps share the parent's dependencies automatically
> Mounting attaches a separate ASGI application in-process, so it is isolated: its own docs, handlers and
> middleware (the parent's middleware still wraps it). That is the point when you need isolation, and the
> cost when you wanted one merged API. There is no HTTP proxying, and dependencies are not inherited.
