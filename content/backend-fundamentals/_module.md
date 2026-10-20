---
id: backend-fundamentals
title: Backend fundamentals
---

## Overview
This module covers the backend engineering underneath every AI product: the parts that decide whether
a model call becomes a dependable feature or a demo that falls over at the first retry, deploy or
tenant. It opens with the languages and frameworks of the stack (Python and FastAPI at the core,
Node.js with TypeScript at the product edge) and the HTTP contracts that join them. Next come API
design (REST and GraphQL, versioning and backward compatibility, pagination, rate limits and quotas)
from the provider's side, and integrating third-party APIs, SDKs and signed webhooks from the
consumer's side. The data layer follows: schema design, SQL and query plans, PostgreSQL transactions
and isolation, and when NoSQL stores or Redis belong beside the relational system of record. Work that
cannot finish inside a request moves to message queues and durable background workers, and both rely
on idempotency. Identity runs through authentication, OAuth 2.0 and OIDC, RBAC and secrets
management. The module ends with packaging and delivery: Docker images, Kubernetes, and CI/CD
pipelines that also gate prompts and models. Afterwards you can design, build, secure and ship a
multi-tenant AI service that survives retries, crashes, bursts and deploys, and you can defend each
choice in a design review or interview. It matters most for AI Engineers who own production services,
and for FDEs who have to plug those services into a customer's identity provider, data and
infrastructure.

## Why it matters
- **Engineer:** You keep the request path fully async, wrap every model and vendor call in explicit timeouts, retries and idempotency keys, claim jobs with `SKIP LOCKED` and leases, and put an authorization check scoped to the tenant at the top of every mutating handler.
- **Architect:** You choose the contract (REST with OpenAPI or GraphQL), the system of record versus caches and specialised stores, queue versus job table, the token and role model, and the deploy target, and you know which failure mode each choice prevents and what it costs to operate.
- **FDE:** You connect the product to a customer's SSO through OIDC, map their identity-provider groups to roles, verify their webhooks, respect their API quotas, keep their credentials in a secrets manager, and ship into the Kubernetes cluster and CI rules they already run.

## Mental model
Picture one request travelling through a set of trust and time boundaries. At the edge, the request
crosses a trust boundary: authentication turns a credential into a principal, authorization checks
that principal against this action on this resource, and signatures prove that inbound webhooks are
genuine. At the contract, the API bounds what crosses: a versioned schema, a bounded page, a rate
limit and a quota. At the core, the handler waits cheaply on a model, keeps the facts that must stay
true in PostgreSQL behind constraints and short transactions, and keeps rebuildable fast state in
Redis. Anything slower than the caller can wait for crosses a time boundary into a queue or a durable
job, where at-least-once delivery makes idempotency mandatory. Around all of it, the platform
boundary: secrets arrive through workload identity, code ships as one immutable image, Kubernetes
reconciles desired state, and CI turns every change into evidence. Most backend bugs are a boundary
that someone assumed was not there.

## Suggested order
1. python — the language of the AI core; asyncio and the GIL explain how every later service waits on models.
2. fastapi — the HTTP layer built on that async model, where Pydantic contracts and dependency injection live.
3. node-js-typescript — the product-edge runtime and where the boundary with Python sits, before designing the contract between them.
4. rest-graphql-api-design — resources, status codes, idempotency and the long-running-job pattern that every later API topic refines.
5. api-versioning-and-backward-compatibility — how a published contract changes without breaking clients you cannot redeploy.
6. pagination-rate-limits-api-quotas — the three bounds every API needs, designed as a provider and handled as a consumer.
7. api-and-sdk-integration — the consumer side of APIs: timeouts, retries, error taxonomy and idempotency around vendor and model SDKs.
8. database-design — the schema, keys, constraints and indexes that outlive every version of the code.
9. sql — set-based queries and reading `EXPLAIN` plans against the schema you just designed.
10. postgresql-transactions-and-isolation-levels — what concurrent writers see, and how locks and SERIALIZABLE retries prevent lost updates.
11. nosql — when access-pattern-first key design beats the relational default, judged against the SQL foundation.
12. redis — the fast, shared, short-lived state beside PostgreSQL: caches, rate-limit counters, locks and sessions.
13. message-queues — durable buffers between producers and consumers, at-least-once delivery and the choice of broker.
14. background-jobs-durable-workers — job tables, leases and idempotent job bodies, built from transactions and queues.
15. webhook-signature-verification-and-replay-protection — HMAC verification, replay windows and ack-then-queue, which depend on idempotency and workers.
16. authentication-vs-authorization — the principal versus the permission check, and why 401 differs from 403.
17. oauth-2-0-oidc — delegated access and federated login, building on the authentication model.
18. rbac — roles and scoped permissions that bound what users and the agents acting for them may do.
19. secrets-management — keeping API keys, signing keys and OAuth client secrets out of code, images, logs and prompts.
20. docker — packaging the service as one immutable image, which is the unit the remaining topics deploy.
21. kubernetes — reconciling those images across a fleet with probes, resource requests and graceful termination.
22. ci-cd — build once, promote many: the pipeline that tests, scans, evaluates and ships everything above.

## Capstone
### Brief
Build and ship "DocBrief", a multi-tenant service that summarises uploaded documents with an LLM. Write
the core in FastAPI on PostgreSQL. Expose a REST API, versioned under `/v1` and described by OpenAPI.
`POST /v1/documents` returns `202` with a job id and accepts an `Idempotency-Key` header, and
`GET /v1/jobs/{id}` reports status. List endpoints use cursor pagination. Design the schema with
tenant-scoped foreign keys and indexes justified by `EXPLAIN ANALYZE`. Summaries run in a durable
worker that claims jobs with `SKIP LOCKED` and a lease, calls the model provider through an adapter
with explicit timeouts, bounded retries and a client-side token bucket, and writes results
idempotently. Per-tenant rate limits and a monthly token quota live in Redis, and the authoritative
usage ledger stays in PostgreSQL. A billing provider notifies plan changes by webhook: verify the HMAC
over the raw body, reject stale timestamps, deduplicate by event id and process the event in the
worker. Users log in through OIDC against a local identity provider (Keycloak or a dev tenant).
Tenant `admin`, `member` and `viewer` roles gate every endpoint, and each provider key comes from a
secrets manager, never from the image or the repository. Package the API and the worker as Docker
images, deploy them to a local Kubernetes cluster (kind or k3d) with probes and graceful shutdown, and
drive everything from a CI pipeline.

### Deliverables
- A repository with the FastAPI service, the worker, migrations and a schema diagram, plus an OpenAPI document that CI checks for breaking changes against the previous release.
- A design note covering the job-state machine, the idempotency strategy for the API, worker and webhook, the isolation level or lock chosen for the quota decrement, and why Redis holds counters while PostgreSQL holds the ledger.
- A test suite with integration tests for concurrent idempotent submits, a worker killed mid-job, a replayed webhook, a tampered signature, a cross-tenant read attempt, and a tenant pushed over its rate limit and quota.
- Dockerfiles for the API and the worker, plus Kubernetes manifests (or a Helm chart) with readiness and liveness probes, resource requests, a PodDisruptionBudget and a termination grace period longer than the longest model call.
- A CI workflow that lints, tests, builds one image per commit, scans it, runs the OpenAPI compatibility check and deploys that same image digest to the cluster, authenticating without a stored long-lived cloud key.
- A secrets runbook showing where each secret lives, which workload identity can read it, and a timed end-to-end rotation of the model-provider key with no failed requests.

### Rubric
- Submitting the same document 50 times concurrently with one `Idempotency-Key` creates exactly one job and one model call, and every response returns the same job id.
- Killing a worker pod mid-summary leaves no job stuck: the lease expires, another worker finishes the job, and exactly one summary row exists for it.
- A webhook with a modified body or a timestamp outside the window gets `401` and changes no state; replaying a valid event changes nothing, and logs show it as a duplicate.
- A `viewer` attempting a write gets `403`, an unauthenticated call gets `401`, and a valid token for tenant A requesting tenant B's document gets `404` with no data in the body.
- Sustained traffic above a tenant's limit returns `429` with `Retry-After` and rate-limit headers, other tenants' p99 latency stays flat, and the quota never goes negative under concurrent requests.
- A rolling deploy during a load test drops zero requests, and `git grep` together with an image-layer scan finds no secret in the repository or any published image.
