---
id: fastapi-for-ai
title: FastAPI for AI
---

## Overview
This module is about building the HTTP service that sits in front of a model: the FastAPI process that
accepts prompts, files and chat sessions, calls a provider or a local model, and returns tokens without
falling over under real traffic. It starts with the runtime underneath. The ASGI lifecycle explains
what a request, a stream, a disconnect and a startup actually are, and async endpoints set the rule
that decides everything else: await remote models, never block the event loop. Next comes the contract
layer. Pydantic request and response models, `response_model`, error handling, routers and dependency
injection define what goes in, what may come out and who is calling. Lifespan and resource scoping then
decide what is built once per worker (model weights, the shared httpx client, the database engine) and
what lives for one request (session, principal, budget). The middle of the module covers the shapes AI
traffic takes: streaming responses, Server-Sent Events, WebSockets and cancellation when the user closes
the tab; async database access, SQLAlchemy and Alembic; uploads and their size and safety limits;
background tasks versus durable job queues. Protection follows: timeouts, idempotency keys, CORS,
authentication, Redis caching, rate limits, concurrency caps and backpressure. The last group makes the
service operable and provable: structured logs, OpenTelemetry traces, health and readiness probes,
worker sizing, graceful draining, OpenAPI docs and three layers of tests. Afterwards you can ship an
authenticated, streaming, cost-bounded LLM API that survives deploys, overload and retries, and explain
its behaviour from a trace. It matters most for Engineers who build and run these services, and for
FDEs who deploy them into customer environments.

## Why it matters
- **Engineer:** You write the endpoints, dependencies, streaming generators and worker configuration, so a blocking SDK call that freezes every request, a pool exhausted by a held connection, a double-billed retry or a stream that keeps generating for a closed tab is a bug in your code.
- **Architect:** You decide what is application-scoped versus request-scoped, where slow work goes (BackgroundTasks or a durable queue), how concurrency, rate and token budgets are enforced, and how workers map to memory and GPUs, and those choices fix the service's cost and failure behaviour.
- **FDE:** Customers deploy the service behind their own proxies, identity providers and Kubernetes clusters, so you are the one tuning CORS and auth, fixing buffering that breaks streaming, setting probe and drain timeouts, and proving with traces and contract tests that it behaves under their load.

## Mental model
Picture one worker process as an event loop with two kinds of objects and three gates. The application-
scoped objects are built once in lifespan and shared by every request: the model or provider client,
its connection pool, the database engine, the Redis client, the semaphore. The request-scoped objects
are created by dependencies and destroyed when the request ends: the session, the principal, the
budget, the trace span. Every request passes three gates on the way to the model. At the edge,
middleware, size limits, CORS, auth and rate limits decide whether it gets in at all. At admission,
concurrency limits, idempotency keys and caches decide whether it needs to call the model. At
execution, timeouts, cancellation and backpressure decide how long it may hold its slot and what
happens if the client goes away. Anything slower than a request leaves the loop for a durable worker.
Logs, traces and probes are how you see each gate working, and tests pin the contract to the schema.

## Suggested order
1. asgi-lifecycle — the scope/receive/send protocol underlies lifespan, middleware, streaming and disconnects, so every later lesson refers back to it.
2. async-endpoints — the async versus def rule and the blocking-call trap decide how every handler, client and database call must be written.
3. pydantic-request-response-models — defines the validated input contract that stops malformed or oversized prompts before they cost money.
4. response-model — completes the contract on the way out so internal fields and raw provider payloads never reach clients.
5. httpexception-handling — maps validation, domain and upstream LLM failures onto one consistent error shape built on those models.
6. routers — splits the growing service by feature and version once there are enough endpoints and errors to organise.
7. dependency-injection — introduces Depends, the mechanism that delivers auth, sessions, clients and test overrides to handlers.
8. lifespan — shows where expensive shared resources are built and closed once per worker, which dependencies then hand out.
9. request-scoped-vs-application-scoped-resources — names the scoping rule that lifespan and dependencies implement, and the leaks when it is wrong.
10. shared-httpx-asyncclient — applies that rule to the most common resource in an AI service, the pooled client calling model providers.
11. middleware-ordering — explains the wrapping stack that request IDs, CORS, auth and timing all sit in before you add any of them.
12. cors — the first middleware most browser-facing AI apps need, and why it is not authentication.
13. api-keys-oauth2-jwt — adds real caller identity for billing and data scoping, implemented as dependencies and middleware.
14. timeouts — bounds every outbound model call and the request as a whole before streams make requests long-lived.
15. streamingresponse — sends tokens as they are generated, with the frozen-status rule that shapes mid-stream error handling.
16. server-sent-events — gives the stream message boundaries and reconnection, the format chat UIs and vendor APIs use.
17. cancellation-on-client-disconnect — stops generation and frees slots when a streaming client goes away, using the ASGI disconnect message.
18. websockets — covers the two-way case (interrupts, voice, approvals) once one-way streaming and its limits are understood.
19. async-database-access-and-connection-pooling — introduces async drivers, pool sizing and the rule never to hold a connection across a model call.
20. sqlalchemy-sqlmodel — models conversations, jobs and chunks on top of the async engine and session dependency.
21. alembic-migrations — evolves that schema safely across every environment once models exist.
22. uploadfile — accepts documents, images and audio for RAG and multimodal endpoints.
23. request-size-limits-and-upload-security — caps and isolates the large, untrusted inputs that uploads and long prompts bring.
24. backgroundtasks — handles cheap after-response work and shows where in-process follow-up stops being safe.
25. job-queues-and-durable-workers — moves long or must-complete work to a queue with 202 and a status endpoint, building on the database layer.
26. idempotency-keys — makes retried POSTs and at-least-once job delivery produce one effect instead of double billing.
27. redis-caching — avoids repeated model and embedding calls with a shared cache whose key captures everything that changes the answer.
28. rate-limiting — caps request and token spend per caller, typically counted in the same Redis.
29. concurrency-limits — caps in-flight model calls per worker so the provider or GPU is not overrun by an async loop.
30. backpressure — ties limits, bounded queues, 429/503 with Retry-After and stream flow control into one overload story.
31. structured-logging — binds request, tenant, model and token fields into every log line so behaviour can be filtered and costed.
32. opentelemetry-instrumentation — adds traces across FastAPI, httpx, SQLAlchemy and Redis plus manual LLM spans, joined to those logs.
33. health-readiness-endpoints — separates liveness from readiness so orchestrators restart and route traffic correctly, including during model loading.
34. uvicorn-gunicorn-workers — sizes processes by memory and GPUs rather than cores, which determines how per-worker limits add up.
35. graceful-shutdown-and-worker-draining — drains long streams within the orchestrator's budget using readiness flips and lifespan cleanup.
36. openapi-docs — turns the typed routes and models into a published schema for clients, SDKs and LLM tool definitions.
37. testclient-plus-pytest-asyncio — tests the app in-process with the LLM replaced by a dependency override, now that the whole surface exists.
38. integration-tests-with-real-dependencies — proves SQL, pgvector, Redis TTLs and migrations against real containers, which fakes cannot.
39. openapi-contract-testing — closes the loop by checking the schema for breaking changes and fuzzing responses against it.

## Capstone
### Brief
You are delivering a document question-answering API for a customer pilot, and it must survive their
staging cluster rather than a laptop. Build a FastAPI service with versioned routers where an
authenticated caller (API key for servers, JWT for the browser app) uploads PDFs, the service ingests
them through a durable job queue (202 Accepted plus a job-status endpoint), and a chat endpoint streams
answers over Server-Sent Events from a real or mock LLM provider called through one shared httpx
client created in lifespan. Conversations, jobs and chunks live in Postgres through an async
SQLAlchemy engine with Alembic migrations, and repeated questions are served from a Redis cache.
Protect the model with per-tenant request and token rate limits, a per-worker concurrency semaphore
that sheds load with 503 and Retry-After, per-phase timeouts, upload size caps and idempotency keys on
the ingest endpoint. The stream must stop generating when the client disconnects. Make it operable:
JSON logs with request and tenant fields, OpenTelemetry traces showing where each second went,
separate liveness and readiness probes, a Gunicorn/Uvicorn worker configuration you can justify, and a
drain path that lets in-flight streams finish on SIGTERM. Finish with a test suite that a reviewer can
run in CI.

### Deliverables
- A repository with the FastAPI service, a docker-compose file (API, worker, Postgres with pgvector, Redis, an OTel collector) and Alembic migrations that build the schema from empty.
- Endpoints for upload, job status, streaming chat over SSE and health/readiness, with Pydantic request and response models and one consistent error envelope, published at `/docs`.
- A test suite with in-process tests using dependency overrides for the LLM, integration tests against containerised Postgres and Redis, and an OpenAPI snapshot diff plus a Schemathesis run in CI.
- A load-test script and short report showing behaviour at and beyond the concurrency cap, with p50/p95 time to first token and the rate of 429 and 503 responses.
- A one-page operations note covering worker count and pool sizing arithmetic, timeout layering, probe settings, drain budget and how to read a trace for a slow request.

### Rubric
- A blocking-call check passes: under load, `/health` stays responsive while 50 chat streams run, and no sync SDK or database call appears on the event loop.
- Closing the browser mid-answer stops the upstream generation within a second and releases the concurrency slot, visible in logs and the trace.
- Replaying an ingest request with the same idempotency key returns the original job id and creates no second job or charge.
- Beyond the cap, excess requests receive 503 or 429 with `Retry-After` while memory stays flat and accepted requests keep their latency, rather than every request slowing down.
- A rolling restart during active streams drops no in-flight answer that fits within the drain budget, and readiness goes false before the process stops accepting.
- Responses never contain fields outside the declared response models, and a deliberately removed field fails the OpenAPI contract check in CI.
- Every log line for a request carries the same request id and tenant as its trace, and the trace shows separate spans for retrieval, the LLM call and database work.
