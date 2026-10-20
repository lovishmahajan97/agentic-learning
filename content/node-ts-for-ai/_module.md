---
id: node-ts-for-ai
title: Node.js / TypeScript for AI
---

## Overview
This module is about building the backend of an AI product in Node.js and TypeScript to production
standard. Most of that backend is glue around slow, billed, non-deterministic model calls, and the
module is organised around what that glue needs. It starts with types and trust: strict TypeScript
makes the compiler reject null and variant mistakes, and runtime schema validation with Zod checks
the data the compiler cannot see, including model output and tool arguments. Next comes the runtime.
The event loop explains why one process can hold thousands of open model streams and why one
synchronous step stalls all of them. Promise error propagation, AbortController cancellation, streams
with backpressure and worker threads are the tools for working with that single thread. The third
group is the code that talks to the outside world. Dependency injection keeps clients swappable. The
OpenAI and Anthropic SDKs, the Vercel AI SDK and resilient API clients with retries and circuit
breakers handle model calls. Postgres transactions, Redis and BullMQ hold state and durable jobs, and
the MCP TypeScript SDK exposes tools to any agent host. The last group covers running the service:
OpenTelemetry tracing, layered testing that keeps the real model out of the merge gate, graceful
shutdown and a Docker and CI pipeline. After the module you can ship a typed, observable,
cancellable, streaming AI service that survives deploys, provider outages and restarts without lost
work or runaway token spend. It matters most for Engineers who write and operate these services day to
day, and for FDEs who build integrations at customer sites, often on a customer's Node stack.

## Why it matters
- **Engineer:** You write the handlers, workers and tool servers, so a floating promise that crashes the process, a stream that keeps billing after the client left, a pool transaction split across connections or a 300 ms tokenizer call on the main thread is a bug in your code.
- **Architect:** You decide which layer owns retries, where cancellation and deadlines propagate, what runs in a request versus a BullMQ job or a worker thread, what lives in Redis versus Postgres, and whether to standardise on the Vercel AI SDK or the native provider SDKs.
- **FDE:** Customer integrations are often a TypeScript service or MCP server built in days against their APIs, and you answer for it with typed contracts, a trace of each request, safe secret handling and deploys that do not cut off users mid-answer.

## Mental model
Picture one Node process as a single cashier serving thousands of customers who are each waiting on
a slow kitchen, the model provider. Waiting costs almost nothing, so the cashier can juggle thousands
of open orders. Cooking at the counter, meaning any synchronous CPU work, makes everyone wait, so heavy
work goes to worker threads or to a queue. Every request carries three things down its call tree: a
type that was parsed at the boundary rather than assumed, an AbortSignal that can stop all of its work
at once, and a trace context that ties its spans together across awaits and services. Every call that
leaves the process goes through an injected client with a timeout, a classified retry policy and a
breaker. Every piece of state that must outlive the request goes to Postgres, Redis or a durable job.
Shutdown runs the same picture in reverse: stop taking orders, finish or hand off the open ones, flush,
then exit.

## Suggested order
1. typescript-strict-mode-and-advanced-types — the compiler settings and union types every later lesson's code relies on.
2. runtime-schema-validation — fills the gap types leave at runtime and gives one Zod schema for types, parsing and tool definitions.
3. event-loop-and-microtask-queue — the execution model that explains concurrency, ordering and stalls in everything that follows.
4. promises-and-async-error-propagation — how failures travel through awaits and combinators, needed before cancellation and retries.
5. abortcontroller-and-cancellation — makes abandoned and timed-out work actually stop, building on promise semantics.
6. streams-and-backpressure — token streaming to clients with bounded memory, wired to the request's abort signal.
7. worker-threads-for-cpu-bound-tasks — the answer to the event-loop stalls lesson 3 identified for sustained CPU work.
8. dependency-injection — the explicit wiring pattern that makes provider clients, stores and queues swappable and testable.
9. openai-anthropic-node-sdks — the native model clients, their retry, timeout, streaming and tool-call mechanics, injected behind an interface.
10. api-clients-with-retries-and-circuit-breakers — layers deadlines, classified retries and breakers over the SDKs' own retries without multiplying attempts.
11. vercel-ai-sdk — the provider-agnostic alternative, best judged once the native SDKs and their trade-offs are familiar.
12. postgresql-transactions-and-migrations — durable run and budget state, including the pool-per-statement trap and safe schema changes.
13. redis — shared fast state across replicas for caches, rate limits and locks, and the store BullMQ needs.
14. bullmq-and-durable-jobs — moves long agent runs and batch work off the request, built on Redis and idempotent Postgres writes.
15. mcp-typescript-sdk — exposes the service's tools over MCP using the Zod schemas, transports and error handling covered earlier.
16. opentelemetry-instrumentation — traces one request across HTTP, model calls, Postgres, Redis and queue hops, so it comes after those pieces exist.
17. vitest-jest-and-integration-testing — layered tests with injected fakes, containerised dependencies and scored evals over the full stack.
18. graceful-shutdown — the SIGTERM drain ordering across servers, workers, pools and exporters introduced above.
19. docker-ci-cd-deployment — packages and ships the service with a gated pipeline, depending on shutdown and tests being in place.

## Capstone
### Brief
Build a production-grade "document Q&A" service in TypeScript over a week. Users upload PDFs and ask
questions in a chat UI. Answers stream token by token and cite pages. The service runs as an API
process and a separate worker process. Uploads enqueue a BullMQ ingestion job. The worker parses and
tokenizes each document in a worker-thread pool, embeds chunks through a provider client with
timeouts, classified retries and a circuit breaker, and writes chunks, run records and a per-tenant
token budget to Postgres in transactions, with schema managed by migrations. The chat endpoint
validates requests with Zod and streams through either the Vercel AI SDK or a native SDK behind an
injected interface, with a fallback provider when the breaker is open. A single AbortSignal per request
stops generation when the client disconnects. Redis holds a rate limiter and an answer cache. The same
retrieval and citation tools are also exposed as an MCP server over stdio and Streamable HTTP. Add
OpenTelemetry traces across API, queue, worker and database, a layered test suite, graceful shutdown
for both processes and a multi-stage Docker image with a CI pipeline.

### Deliverables
- A repository with the API, worker and MCP server under `strict` plus `noUncheckedIndexedAccess`, a single composition root per process, and Zod schemas used for request bodies, environment config, model tool definitions and MCP tool inputs.
- Postgres migrations for documents, chunks, runs and budgets, a transactional budget-charge path that pins one client, and a BullMQ ingestion queue with idempotent handlers, retries with backoff and a dead-letter inspection script.
- A test suite with unit tests against a fake model client, integration tests running real Postgres and Redis in containers plus intercepted provider HTTP, and a separate scored eval of 20+ questions with a pass threshold, run outside the merge gate.
- A multi-stage Dockerfile running Node as PID 1 in exec form, a CI workflow gating on typecheck, lint (`no-floating-promises`, `no-explicit-any`) and tests, and a short runbook covering the shutdown sequence, secrets injection and dashboards.
- An OpenTelemetry setup that exports one trace per question showing the HTTP span, retrieval, model call with token usage, Redis and Postgres spans, plus a linked trace for each ingestion job across the queue hop.

### Rubric
- Closing the browser tab mid-answer ends the upstream model stream within a second, shown by the span ending with an abort status and no further token usage recorded for that request.
- Ingesting a 200-page PDF while 50 chats stream keeps p99 event-loop lag under 50 ms, measured with `monitorEventLoopDelay` and compared with a run that parses on the main thread.
- With the primary provider forced to return 529, requests fail over to the fallback after the breaker opens, total attempts per request stay within the configured cap, and breaker state transitions appear in metrics.
- Sending SIGTERM to the API during 20 active streams and to the worker mid-job finishes or cleanly ends every stream, records zero 5xx at the load balancer, and leaves the job either completed or retried exactly once with no duplicate chunks.
- Two concurrent questions for a tenant with budget for only one result in exactly one being charged and the other rejected with a typed budget error, and the unit and integration suites run with no network access or provider keys.
- An MCP host such as an IDE lists and calls the retrieval tool over stdio, returns `isError` results for bad arguments rather than protocol errors, and nothing other than JSON-RPC is written to stdout.
