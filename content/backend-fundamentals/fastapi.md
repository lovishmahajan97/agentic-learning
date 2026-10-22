---
id: fastapi
module: backend-fundamentals
name: FastAPI
kind: tool
difficulty: intro
est_minutes: 11
status: agentic-verified
agent_relevance: core
related:
  - fastapi-for-ai/asgi-lifecycle
  - fastapi-for-ai/async-endpoints
  - fastapi-for-ai/pydantic-request-response-models
  - fastapi-for-ai/dependency-injection
  - fastapi-for-ai/streamingresponse
  - fastapi-for-ai/openapi-docs
  - backend-fundamentals/python
  - backend-fundamentals/rest-graphql-api-design
sources:
  - Official docs: FastAPI
  - Official docs: Starlette
  - Official docs: Pydantic
---

## TL;DR
FastAPI is the usual way to put a Python agent behind HTTP: async ASGI holding long agent runs, Pydantic
contracts, SSE streaming of steps, and DI for shared clients. Sharp edge: one blocking call in
`async def` stalls the worker. (Deep dive: `fastapi-for-ai`.)

## For agent builders
- **Why it matters for agents.** An agent run holds a connection for seconds to minutes, makes many model
  and tool calls, and should stream its progress. FastAPI's async handlers, `StreamingResponse` and
  `lifespan`-scoped clients are exactly that shape, which is why most Python agent backends use it.
- **Where it shows up in your stack.** Your own agent API wrapping a LangGraph graph, a LangChain chain or
  a hand-written loop; LangServe was built on FastAPI; MCP tool servers can be served over HTTP from an
  ASGI app; webhook receivers that start agent runs.
- **What you control.** Request schemas with step and token budgets, streaming event format, shared
  provider clients, disconnect handling, worker count, and where long runs go (inline stream versus a
  durable job queue).
- **What you can skip.** ASGI protocol internals and Starlette's routing code. Know the request path, the
  `def` versus `async def` rule and the threadpool cap.

## Why it exists
- **An agent endpoint is unusual HTTP.** A request may hold its connection open for ten seconds or far
  longer, stream hundreds of chunks and step events, carry large structured bodies both ways, and
  share expensive clients (a provider connection pool, a vector store) across every request.
- **WSGI was built for the opposite.** Flask and traditional Django dedicate one thread or process per
  request. With 200 in-flight model calls of five seconds each, a WSGI server needs 200 workers that sit
  idle on sockets; one ASGI event loop holds all 200 pending awaits, bounded by memory and the upstream.
- **Contract drift.** Hand-written validation and docs diverge the week after launch; a client sends
  `temperature: "0.7"` and the handler crashes deep in a provider call.
- **One declaration, many uses.** FastAPI reads Pydantic types on the handler signature and derives
  validation, a precise 422, response filtering and the OpenAPI schema, so docs cannot lie. The same class
  is the JSON Schema for structured output, closing the loop between API and prompt contracts.
- **The naive alternatives cost more.** Raw Starlette or aiohttp with hand validation reinvents those
  layers; Flask-with-async-bolted-on keeps the WSGI mental model where it hurts most.

## How it works
### ASGI and the request path
1. **Uvicorn accepts the connection** and hands an ASGI `scope` to the app.
2. **Middleware runs** (CORS, auth, tracing).
3. **The router matches** path and method.
4. **FastAPI resolves the handler's dependencies.**
5. **Path, query and body parameters are validated** against the declared types. A failure stops at the
   boundary with a 422 and a field-level error list, before the handler body runs.
6. **The handler runs**, and the return value is serialised through `response_model`.

- **Caveat:** dependencies may already have executed when validation fails, so keep side effects out
  of them.

### `async def` versus `def` handlers
| Handler | Runs on | Use when | Concurrency cap per worker |
|---|---|---|---|
| `async def` | the event loop; every `await` yields | everything you call is async (httpx, async DB drivers, async SDK clients) | memory and the upstream's limits |
| `def` | a bounded worker threadpool, so it cannot block the loop | you must call a blocking library | the threadpool (AnyIO's default limiter is 40 tokens) |

- **The failure mode is the mix.** A sync SDK call inside `async def` blocks the loop, and every other
  request on that worker waits.
- **Same rule for dependencies.** A plain `def` dependency is also run in the threadpool.
- **Capacity arithmetic.** If each `def` call holds a thread for 5 seconds, one worker serves at most
  40 / 5 = 8 requests per second before requests queue for a thread. That is why the async path is the
  default for model-calling endpoints.

### Workers and processes
- **One uvicorn process is one event loop on one core.**
- **Run several worker processes per machine** (uvicorn `--workers`, gunicorn with uvicorn workers, or
  one process per container behind the orchestrator) *because* JSON parsing, validation and
  serialisation still use CPU, and one loop cannot use a second core.
- **Workers share nothing**, so in-memory caches, rate-limit counters and "which streams are open" state
  belong in Redis or the database, not in a module-level dict.

### Pydantic as the contract
- **Request:** declaring `body: ChatRequest` validates and parses the JSON body.
- **Response:** `response_model=Answer` filters output to the declared fields, so a leaked internal
  attribute never reaches a client, and documents it.
- **Constraints:** `Field` constraints (`max_length`, `ge`, `le`) become both runtime checks and schema
  metadata.

### Dependency injection and lifespan
- **`Depends`** lets a handler ask for a current user, a DB session or a shared HTTP client without
  constructing it; dependencies compose and can be overridden in tests.
- **`lifespan`** runs startup and shutdown code: build application-scoped resources there (one
  `httpx.AsyncClient`, one model client) instead of recreating them per request.

### Streaming
- **`StreamingResponse` takes an async generator** and writes each yielded chunk as it arrives.
- **`text/event-stream` framing gives server-sent events**, the standard way to stream tokens to browsers
  and SDKs.
- **A slow stream does not block others**, because each `yield` happens between awaits.

### Serving an agent run
1. **Validate the run request** with a Pydantic model that carries the task plus budgets (`max_steps`,
   a token or cost cap), so a client cannot ask for an unbounded run.
2. **Run the loop inside an async generator.** Each model call and tool call is an `await`, so one worker
   holds many concurrent runs.
3. **Yield typed events** (`tool_call`, `tool_result`, `final`) as SSE, so the UI can show progress and a
   trace viewer can replay the run.
4. **Check for client disconnect** between steps (Starlette's `await request.is_disconnected()`) and stop,
   because every extra step costs tokens nobody will read.
5. **Hand off when runs get long.** If a run can exceed a few minutes or must survive a deploy, enqueue it
   as a job and stream progress from the job's event log instead.

### Background tasks are not a job queue
- **`BackgroundTasks` runs a function after the response is sent, in the same process.** Fine for a few
  hundred milliseconds of follow-up (an audit row, a metric).
- **Wrong home for a two-minute summarisation**, *because* the work dies with the process on a deploy or
  crash, nothing retries it, and it competes with live requests for the same loop or threadpool.
- **Long or must-complete work** goes to a durable queue and a separate worker, with a `202` and a job
  resource on the API side. Multi-step agent runs that call slow tools usually fall in this bucket.

### Compared with the alternatives
| Framework | Strength | Fit |
|---|---|---|
| Flask | simple, sync-first | small internal tools; poor for long-lived streaming calls |
| Django | ORM, admin, auth | when those matter more than async purity; async support is partial |
| Starlette | the ASGI core | FastAPI without validation, DI and docs |
| Litestar | comparable ASGI framework, different DI style | the reasoning in this lesson applies too |

## Diagram
Follow one agent-run request: validation can reject it (a bad budget, an empty task) before the handler,
and the handler's type decides whether the run loop lives on the event loop or in the threadpool.

```mermaid
flowchart TD
    C["Client starts agent run"] --> U["Uvicorn: ASGI scope"]
    U --> M["Middleware: CORS, auth, tracing"]
    M --> R["Router: path and method"]
    R --> D["Resolve dependencies"]
    D --> V{"Params valid?"}
    V -->|"no"| E["422 field errors"]
    V -->|"yes"| H{"Handler type"}
    H -->|"async def"| L["Runs on event loop"]
    H -->|"def"| T["Runs in threadpool, cap 40"]
    L --> S["Serialise via response_model"]
    T --> S
```

## Examples
### Agent-run endpoint that streams steps
A validated run request with a step budget, a loop inside an async generator, typed SSE events per step,
and a disconnect check so an abandoned run stops spending. `plan_step` and `run_tool` stand in for a
model call and an async tool; a LangGraph graph's async streaming method would slot into the same place.

```python
import asyncio, json
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

app = FastAPI()

class RunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8000)
    max_steps: int = Field(8, ge=1, le=20)        # step budget is part of the contract

async def plan_step(task: str, step: int) -> dict:  # stand-in for a model call that picks a tool
    await asyncio.sleep(0.05)
    return {"tool": "search_docs", "args": {"query": task}} if step < 2 else {"final": "Refunds take 5 days."}

async def run_tool(name: str, args: dict) -> str:   # stand-in for an async tool
    await asyncio.sleep(0.05)
    return f"3 docs for {args['query']!r}"

def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"

@app.post("/v1/agent/runs")
async def run_agent(body: RunRequest, request: Request):
    async def events():
        for step in range(body.max_steps):
            if await request.is_disconnected():     # user closed the tab: stop paying for steps
                return
            action = await plan_step(body.task, step)
            if "final" in action:
                yield sse({"type": "final", "text": action["final"]})
                break
            yield sse({"type": "tool_call", "step": step, **action})
            result = await run_tool(action["tool"], action["args"])
            yield sse({"type": "tool_result", "step": step, "content": result})
        yield "data: [DONE]\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")
# POST {"task": "refund policy"} streams 2 tool_call/tool_result pairs, a final event, then [DONE].
# POST {"task": "x", "max_steps": 50} returns 422 before any model call is made.
```

### Streaming chat endpoint
One shared upstream client created in `lifespan`, a validated request body, and an SSE stream from an
async generator. Every `await` inside `events()` releases the loop to other requests, which is the
whole point of the async design.

```python
import json
from contextlib import asynccontextmanager
import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(base_url="http://model-server:8000", timeout=60)
    yield                                          # the app serves requests here
    await app.state.http.aclose()                  # shutdown: close the pool cleanly

app = FastAPI(lifespan=lifespan)

class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    temperature: float = Field(0.2, ge=0, le=2)   # validated and documented from one declaration

def http(request: Request) -> httpx.AsyncClient:   # dependency: one shared pool, not one per request
    return request.app.state.http

@app.post("/v1/chat")
async def chat(body: ChatRequest, client: httpx.AsyncClient = Depends(http)):
    async def events():
        async with client.stream("POST", "/generate", json=body.model_dump()) as r:
            async for line in r.aiter_lines():     # each await yields the loop to other requests
                if line:
                    yield f"data: {json.dumps({'token': line})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")
```

### Testing it without a model server
`dependency_overrides` swaps the shared client for one backed by `httpx.MockTransport`, so the test
exercises validation, the stream framing and the 422 path with no network.

```python
import json
import httpx
from fastapi.testclient import TestClient
from main import app, http                         # the module above

def fake_http() -> httpx.AsyncClient:
    def upstream(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["prompt"] == "hi"
        return httpx.Response(200, text="hello\nworld\n")
    return httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://test")

app.dependency_overrides[http] = fake_http        # swap the real pool for a fake upstream

def test_chat_streams_tokens():
    with TestClient(app) as c:                     # the with-block runs lifespan
        r = c.post("/v1/chat", json={"prompt": "hi"})
        assert r.status_code == 200
        assert 'data: {"token": "hello"}' in r.text
        assert r.text.endswith("data: [DONE]\n\n")

def test_rejects_out_of_range_temperature():
    with TestClient(app) as c:
        r = c.post("/v1/chat", json={"prompt": "hi", "temperature": 5})
        assert r.status_code == 422                # stopped at the boundary, handler never runs
```

### Worked scenario: sizing one worker for 5-second model calls
Target: 30 requests per second of a model call that takes 5 seconds.

| Design | Per-worker ceiling | Workers needed for 30 rps |
|---|---|---|
| `def` handler, default 40-thread limiter | 40 / 5 = 8 rps | 4, with threads mostly idle on sockets |
| `def` handler, limiter raised to 200 | 200 / 5 = 40 rps | 1, but 200 threads of memory and switching |
| `async def` with an async client | memory and upstream limits; ~150 awaits in flight | 1 for I/O; add workers only for CPU |

By Little's law, 30 rps × 5 s means ~150 requests in flight at once whatever the design; the question is
only whether each one costs a thread or a coroutine.

### Worked scenario: the same arithmetic for agent runs
An agent run makes 6 model calls of ~5 s each plus tools, so it holds its connection ~30 s. Figures are
illustrative.

| Load | In flight (Little's law) | `def` handler, 40 threads | `async def` |
|---|---|---|---|
| 2 runs started per second | 2 × 30 = 60 runs | 2 workers just for threads, all idle on sockets | 1 worker holds all 60 |
| 10 runs per second | 300 runs | 8 workers | 1-2 workers; the provider rate limit binds first |

At 10 runs per second the provider sees 60 model calls per second, so the real limit is the rate limit
and token quota, not FastAPI.

## Production patterns
- **Budgets in the request schema.** Put `max_steps`, a token or cost cap and a wall-clock timeout on the
  run request with `Field` bounds, and enforce them in the loop. Monitor steps per run and cost per run;
  a creeping p95 means the agent is looping.
- **Typed step events over SSE, with a run id.** Stream `tool_call`, `tool_result` and `final` events,
  tag each with the run id that your tracing (LangSmith, Langfuse or OpenTelemetry) also records, and log
  the same events server side. Monitor time to first event and disconnect rate.
- **Stop work on disconnect.** Check `request.is_disconnected()` between steps and cancel in-flight model
  calls, because an abandoned run keeps billing tokens. Monitor tokens spent after disconnect.
- **Shared clients in `lifespan`.** Create one `httpx.AsyncClient` per process at startup, with explicit
  `timeout` and `httpx.Limits(max_connections=...)`, inject it via `Depends`, and close it on shutdown.
  Monitor connection-pool waits and new TLS handshakes per second.
- **A canary for loop lag.** Run a background coroutine that sleeps 100 ms and records how late it wakes;
  export that as a metric and alert on it. A health check whose latency tracks the model endpoint's is
  the same signal from outside.
- **Workers sized to cores, state external.** Run roughly one uvicorn worker per core (`--workers`, or
  one process per container), keep rate-limit counters and caches in Redis, and monitor per-worker CPU.
  Uneven CPU across workers points to a hot CPU-bound handler.
- **Durable jobs for long work.** For anything beyond a few hundred milliseconds or that must finish,
  return `202` with a job id and hand the work to a queue worker (Celery, RQ, Arq or a cloud queue).
  Monitor queue depth, job age and failure rate.
- **Explicit `response_model` on every route.** Declare the response schema so internal fields are
  filtered, and publish the generated OpenAPI as the contract other teams codegen from. Diff the schema
  in CI to catch accidental breaking changes.

## Trade-offs
- **Concurrency vs async discipline.** High concurrency for I/O-bound handlers, but every library on
  the hot path must be async or explicitly offloaded; one blocking call turns the advantage into a
  liability.
- **Validation vs parsing cost.** Pydantic removes boilerplate and keeps docs honest, but adds per-request
  parsing; for very large bodies or extreme throughput that cost is measurable and worth profiling.
- **Thin framework vs batteries.** No ORM, admin or auth framework keeps it flexible, but you assemble
  those pieces yourself; Django wins when a full-stack CRUD app is the actual job.
- **I/O scaling vs CPU scaling.** Single-process asyncio scales I/O, not CPU; you still run several
  uvicorn workers per machine and design them to be stateless.

## Pitfalls
- **Sync SDK or `requests` inside `async def`.** Symptom: latency on every endpoint of that worker grows
  with the sum of blocking upstream calls queued ahead of it. Fix: an async client or `def`.
- **An `httpx.AsyncClient` per request.** Symptom: a new TLS handshake per call and socket exhaustion
  under load. Fix: one client in `lifespan`.
- **Returning ORM objects or internal dicts without `response_model`.** Symptom: an internal field (a
  cost figure, a secret flag) appears in client responses. Fix: declare `response_model` on every route.
- **CPU-heavy work in the handler** (tokenizing megabytes, re-ranking in pure Python). Symptom: loop
  stalls visible as every endpoint slowing together. Fix: a thread, process or worker.
- **Unbounded agent runs.** No step or token cap on the endpoint. Symptom: a few runs that loop on a
  failing tool consume the provider quota and hold connections for minutes. Fix: budgets in the request
  model, enforced in the loop, with a clear `final` event when the budget runs out.
- **Long jobs through `BackgroundTasks`.** Symptom: work silently vanishes on every deploy or pod restart,
  with no error logged because the request already returned 200. Fix: a durable queue and a worker.
- **State in module-level variables.** Symptom: behaviour depends on which worker served the request (a
  rate limit that allows N times the configured rate with N workers). Fix: keep shared state in Redis
  or the database.

## When to use
- **Use when** building the HTTP layer of a Python agent or AI service: agent runners, model gateways,
  RAG endpoints, MCP or tool servers, anything that awaits a model and benefits from streaming and typed
  contracts.
- **Move runs to a queue when** a run can exceed a few minutes, must survive deploys, or needs human
  approval mid-run; keep FastAPI as the front door that starts runs and streams their events.
- **Prefer Django** when the application is mostly conventional CRUD with an ORM and admin, and Flask only
  for tiny sync tools.
- **Measure:** event-loop lag (schedule a no-op every 100 ms and record how late it fires), health-check
  latency under load (it should stay flat; if it tracks the model endpoint's latency, something is
  blocking the loop), threadpool saturation for `def` handlers, and upstream connection reuse.
- **Day-to-day skill:** keep the request path fully async, design Pydantic models that double as
  structured-output schemas, and wire shared clients through dependencies.
- **Interviews:** probe whether you know why `def` and `async def` handlers behave differently. The
  `fastapi-for-ai` module covers each mechanism in depth.

## Role lens
- **Engineer:** Builds endpoints with Pydantic contracts, shared async clients via `Depends`, and SSE
  streaming, and keeps anything blocking out of the event loop.
- **Architect:** Decides that the AI service layer is ASGI and stateless, sets worker and concurrency
  limits per process, and treats the OpenAPI schema as the published contract for other teams.
- **FDE:** Stands up a FastAPI service inside a customer's network, exposes `/docs` so their
  integrators can self-serve, and explains why blocking code in their middleware (a sync auth lookup)
  stalls every async handler behind it.

## Check yourself
**Q:** A FastAPI service has one `async def` endpoint that calls a provider through a synchronous SDK.
Under light load it is fine; under load every endpoint, including the health check, becomes slow.
Explain the mechanism and two fixes with their trade-offs.
**A:** The sync SDK call blocks the event loop thread for the full duration of the model request, so
no other coroutine on that worker, including the health check, can run until it returns; under load
requests queue behind each other. Fix one: switch to the SDK's async client (or httpx) so the call
yields to the loop; this is the right long-term fix but requires the whole path to be async. Fix two:
change the handler to plain `def` so FastAPI runs it in the threadpool; this unblocks the loop
immediately but caps concurrency at the threadpool size (AnyIO's default limiter is 40 threads,
tunable) and adds thread overhead; at 5 seconds per call, 40 threads is only 8 requests per second per
worker. A middle path is to keep the handler async and wrap just the sync call in
`await asyncio.to_thread(...)`, with the same thread cap. Either way, add a metric for event-loop lag
so a regression is visible, and treat a slow health check as the first sign of a blocked loop.

## Interview questions
### What does FastAPI derive from the Pydantic types on a handler signature?
- **Validation and parsing:** path, query and body values are checked and coerced before the handler
  runs, and failures return a 422 with field-level errors.
- **Response filtering:** `response_model` drops undeclared fields, so internal attributes do not leak.
- **Documentation:** the OpenAPI schema comes from the same declarations, so docs and behaviour cannot
  drift; the model can also be the structured-output schema given to an LLM.

### How would you expose a multi-step agent over HTTP with FastAPI?
- **Contract:** a `POST /v1/agent/runs` body validated by Pydantic with the task and budgets (steps,
  tokens, timeout), so runaway runs are rejected or capped at the boundary.
- **Execution:** an `async def` handler returning a `StreamingResponse` whose generator runs the loop
  and yields typed SSE events per step; shared provider and tool clients come from `lifespan` via
  `Depends`.
- **Lifecycle:** stop on client disconnect, and move runs that can take minutes or need approval to a
  durable queue with a run resource the client polls or subscribes to.

### Users close the chat tab mid-run, yet your token bill shows the agents kept working. Why, and what do you change?
- **Why:** the generator keeps running the loop after the client leaves, unless something checks for the
  disconnect or the server cancels the task; each further step bills tokens.
- **Fix:** check `await request.is_disconnected()` between steps, cancel in-flight model calls, and record
  a `cancelled` status on the run.
- **Measure:** tokens spent after disconnect per day, which should drop to near zero.

### A streaming endpoint works locally but users see the whole answer at once in production. What do you check?
- **Buffering in front of the app:** a reverse proxy or load balancer buffering responses holds chunks
  until the end; disable response buffering for the route (in nginx, for example, the
  `X-Accel-Buffering: no` response header).
- **Compression middleware:** gzip can buffer small chunks; exclude `text/event-stream` from it.
- **The generator itself:** make sure it yields per token and does not collect a list first, and that the
  upstream call is a streaming call.

### Where would you run a two-minute document summarisation triggered by an API call?
- **Not in `BackgroundTasks`:** it dies with the process on deploy or crash, nothing retries it, and it
  competes with live requests.
- **Durable queue plus worker:** return `202` with a job id, enqueue, and let a separate worker run it
  with retries and idempotency.
- **Client contract:** a `GET /jobs/{id}` status resource, or a webhook or SSE notification on completion.

### When would you choose `def` over `async def`, and how many uvicorn workers would you run?
- **`def` only when the client is blocking** (a sync SDK or DB driver you cannot switch yet): it runs in
  the threadpool so the loop stays free, but AnyIO's default 40 threads means 8 rps per worker at 5 s per
  call. A blocking call inside `async def` is worse than either, because it stalls the whole worker.
- **Workers: roughly one per core,** because each worker is one event loop on one core and the CPU work is
  parsing, validation and serialisation.
- **Going from one to four breaks in-process state:** rate-limit counters, caches or open-stream tracking
  now exist four times; move them to Redis or the database.
- **Monitor** per-worker CPU and loop lag; more workers do not help if the upstream is the bottleneck.

## Quiz
### Q: Why does FastAPI run a plain `def` handler in a threadpool but an `async def` handler on the loop?
- [ ] Threadpools are faster for JSON serialisation
- [x] A sync function would block the single event loop, so it is isolated in a thread while coroutines yield at each await
- [ ] Pydantic validation only works in threads
- [ ] The threadpool enables HTTP/2 multiplexing
> The event loop is one thread; a blocking function on it stops every other request. Running sync
> handlers in threads protects the loop, while async handlers cooperate by yielding at awaits.

### Q: What do you get from declaring the request body as a Pydantic model?
- [ ] Automatic caching of identical request bodies
- [ ] Guaranteed backward compatibility when fields are renamed
- [x] Runtime validation with a 422 error, parsed typed access, and the matching OpenAPI schema from one declaration
- [ ] Automatic retries of the upstream model call on validation failure
> The handler's type annotations drive validation, parsing and documentation together, so the docs and
> the behaviour cannot diverge. Caching, compatibility and retries are separate concerns.

### Q: When is Django a better choice than FastAPI for a service that also calls an LLM?
- [ ] When the service must stream tokens to a browser
- [x] When the application is mostly CRUD with an ORM, admin and auth, and the LLM call is a small part of it
- [ ] When the service needs OpenAPI documentation
- [ ] When the provider SDK is async-only
> FastAPI's strengths are async I/O and typed contracts; Django's are batteries for conventional web
> apps. If the bulk of the work is CRUD and admin, Django's ecosystem outweighs async purity.

## Use cases & projects
### Use cases
- **Agent run API.** A `POST` endpoint takes a validated task with step and token budgets and streams
  each tool call and partial answer back as server-sent events.
- **Tool server for agents.** Internal tools (search, ticket lookup, database reads) are exposed as typed
  routes whose OpenAPI schema can be turned into tool definitions.
- **Webhook-triggered agents.** A receiver verifies an incoming event, records it and hands it to a
  worker that runs the agent, returning quickly to the sender.
- **Shared clients via lifespan.** One provider client and one HTTP pool per worker process, injected
  into every agent route through dependencies.

### Mini project
**Stream an agent run over HTTP (1-2 hours).**
- **Goal:** wrap a two-tool agent loop in a FastAPI endpoint that streams step events and stops cleanly
  when the client disconnects.
- **Steps:** create the provider client in `lifespan`; define a Pydantic request with a max-steps field;
  return a `StreamingResponse` that yields one event per model call and tool result; check for client
  disconnect between steps and stop the loop; add a test that the endpoint rejects a request over the
  step budget with 422.
- **Done when:** `curl -N` shows events arriving step by step, closing curl mid-run stops further model
  calls in the logs, and the test passes.

### Major project
This lesson feeds the module Capstone (see the module guide): DocBrief's REST API is a FastAPI app with
`/v1` routes, `202` job submission, OpenAPI contracts and lifespan-managed clients.
