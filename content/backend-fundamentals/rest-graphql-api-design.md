---
id: rest-graphql-api-design
module: backend-fundamentals
name: REST / GraphQL API design
kind: decision
difficulty: core
est_minutes: 15
status: reviewed
related:
  - backend-fundamentals/api-versioning-and-backward-compatibility
  - backend-fundamentals/pagination-rate-limits-api-quotas
  - fastapi-for-ai/openapi-docs
  - fastapi-for-ai/server-sent-events
  - fastapi-for-ai/httpexception-handling
  - serving-inference/idempotency
  - serving-inference/streaming
  - agent-runtime-architecture/cross-language-api-contracts
sources:
  - Fielding, 2000 — Architectural Styles and the Design of Network-based Software Architectures
  - IETF RFC 9110 — HTTP Semantics
  - IETF RFC 9457 — Problem Details for HTTP APIs
  - Official docs: GraphQL specification
---

## TL;DR
An API is a contract that outlives the code behind it. REST models a system as resources addressed by
URLs and manipulated with HTTP methods whose semantics (safe, idempotent, cacheable) every proxy,
cache and SDK already understands. GraphQL models it as a typed graph that clients query through one
endpoint for exactly the shape they need. Choose REST for service-to-service and model-facing
endpoints (simple, cacheable, streamable, tool-callable); choose GraphQL when many client shapes read
one rich domain and over- and under-fetching is the real pain.

## Why it exists
The moment a client ships against your interface, that interface is frozen. Design decisions made
casually on day one, an action-style endpoint named `/getUserOrders2`, an error returned as 200 with
`{"ok": false}`, a delete that is not safe to retry, cost forever. API styles exist to make those
decisions by convention instead of by accident.

REST's insight is to lean on HTTP rather than tunnel through it. If `GET` is safe and idempotent,
caches can store it and clients can retry it blind. If `PUT` and `DELETE` are idempotent, a timeout
can be retried without creating duplicates. If status codes carry meaning, load balancers can count
5xx responses and retry policies can act on 429 and 503 without reading bodies. The naive alternative,
one `POST /api` endpoint with an `action` field in the body, throws all of that away: nothing can be
cached, nothing is retry-safe by inspection, and every request looks identical to observability.

GraphQL arose at Facebook (open-sourced in 2015) from a different pain: mobile clients needed many
different shapes of the same data, and REST either over-fetched (one fat resource) or under-fetched
(three round trips to assemble a screen), with endpoints multiplying per screen. A typed schema plus
client-authored queries fixes that by moving shape selection to the client. The cost is giving up
HTTP's per-resource semantics, which is why neither style dominates.

For AI services the question is sharper: model calls are big `POST` bodies with streaming responses,
and tools exposed to an agent are easiest to describe as REST operations with an OpenAPI schema.

## How it works
### REST: resources, methods, status codes
Model nouns, not verbs: `/conversations/{id}/messages`, not `/sendMessage`. Collections and items
nest at most a level or two. Methods carry semantics defined by HTTP: `GET` is safe and idempotent and
may be cached; `PUT` replaces a resource and is idempotent; `DELETE` is idempotent; `PATCH` applies a
partial change; `POST` creates or triggers and is *not* idempotent, which is why non-trivial APIs
accept an `Idempotency-Key` header on it. Status codes are the first thing a client branches on:
`200` and `201 Created`, `202 Accepted` for work that continues after the response, `204` for empty
success, `400`/`422` for invalid input, `401`/`403` for identity and permission, `404`, `409` for state
conflicts, `429` for rate limits, `5xx` for your faults. Use one error envelope everywhere; Problem
Details (RFC 9457) gives a standard JSON shape with `type`, `title`, `status` and `detail`.

Idempotency is about server state, not identical responses: a second `DELETE` of the same job may
return `204` or `404` and both are idempotent, because the resource ends up gone either way. Pick one
and document it, since clients write retry logic against it.

Concurrent writers are HTTP's other built-in mechanism. A `GET` returns an `ETag` (a version tag for
the representation); a client that wants to update sends `If-Match: <etag>` with its `PUT` or `PATCH`,
and the server answers `412 Precondition Failed` if someone else changed the resource in between.
That is optimistic concurrency without a custom `version` field, and it is what stops two editors (or
two agents) from silently overwriting each other's changes to the same prompt template or config.
The same tag sent as `If-None-Match` on a `GET` lets the server answer `304 Not Modified` with no
body, which is how polling stays cheap.

Fielding's original definition also requires hypermedia (responses carrying links that tell the
client what it can do next, often called HATEOAS). Most production "REST" APIs skip it; the useful
remnants are the `Location` header, `next` links in paginated responses and job resources that link
to their result.

Long-running AI work maps cleanly: `POST /summaries` returns `202` with a `Location` to a job resource
that clients `GET` until it is `succeeded`, or the response itself streams as `text/event-stream`.
Both keep proxies and timeouts happy *because* no single request has to live as long as the job.

### GraphQL: schema, resolvers, one endpoint
A schema in SDL declares types and fields and three roots: `Query`, `Mutation`, `Subscription`. A
client sends a query naming exactly the fields it wants; the server executes it by calling a resolver
per field, walking the tree. Because resolvers run per field and per parent object, a list of 100
conversations each resolving `lastMessage` naively issues 101 database queries; the standard fix is a
per-request DataLoader that batches and caches lookups. In practice almost everything travels as
`POST` to one endpoint, so HTTP caching does not apply unless you send queries (typically persisted
ones) over `GET`. Field errors are returned alongside partial data in an `errors` array, conventionally
with a 200 status, so clients must inspect the body; the GraphQL-over-HTTP spec lets servers using the
`application/graphql-response+json` media type answer 4xx when a request fails before execution. Because
clients author queries, the server must enforce depth and cost limits, *because* an unbounded nested
query is a denial-of-service vector. The spec does not fix a transport for subscriptions; most
servers use WebSockets (the `graphql-ws` protocol), and SSE is a common alternative.

GraphQL also changes how the contract evolves. There are no URL versions: you add fields freely
(clients only receive what they ask for, so additions never break anyone), mark old fields
`@deprecated(reason: ...)`, and remove them once field-level usage telemetry shows zero queries
selecting them. That telemetry is something REST cannot give you per field without extra work, and it
is one of GraphQL's quieter advantages for a fast-moving product schema.

### The decision
Ask who the clients are and how many shapes they need. One product UI with screens assembled from a
rich domain, owned by a team that iterates weekly: GraphQL pays off. Service-to-service calls, public
APIs for integrators, anything cached by CDNs, anything exposed to an LLM as a tool: REST with an
OpenAPI schema, because the schema generates clients and tool definitions directly. Many teams run
both: GraphQL in the product's BFF, REST underneath and outward.

## Example
A REST design for a long-running summarisation job in FastAPI: `POST` returns `202` with a `Location`,
`GET` is safe to poll, `DELETE` is idempotent. The GraphQL SDL after it expresses the same domain for a
client that wants to pick fields and subscribe to updates.

```python
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel

app = FastAPI()
JOBS: dict[str, dict] = {}                          # stand-in for a database

class SummarizeRequest(BaseModel):
    document_id: str
    max_words: int = 200

@app.post("/v1/summaries", status_code=status.HTTP_202_ACCEPTED)
async def create_summary(body: SummarizeRequest, response: Response):
    job_id = f"sum_{uuid4().hex[:12]}"
    JOBS[job_id] = {"id": job_id, "status": "queued", "input": body.model_dump(), "result": None}
    # enqueue to a worker here; the HTTP request returns immediately
    response.headers["Location"] = f"/v1/summaries/{job_id}"   # where to poll
    return JOBS[job_id]

@app.get("/v1/summaries/{job_id}")                  # GET is safe and idempotent: poll freely
async def get_summary(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        # FastAPI wraps this as {"detail": {...}}; a real service maps errors to RFC 9457 in a handler
        raise HTTPException(status_code=404, detail={"type": "not_found", "job_id": job_id})
    return job                                       # 200 whether queued or done; status is in the body

@app.delete("/v1/summaries/{job_id}", status_code=204)   # idempotent: a second DELETE is also 204
async def cancel_summary(job_id: str):
    JOBS.pop(job_id, None)
    return Response(status_code=204)
```

```graphql
enum JobStatus { QUEUED RUNNING SUCCEEDED FAILED }
type Summary { id: ID!  status: JobStatus!  result: String }
type Query { summary(id: ID!): Summary }
type Mutation { createSummary(documentId: ID!, maxWords: Int = 200): Summary! }
type Subscription { summaryUpdated(id: ID!): Summary! }
```

## Trade-offs
- REST gets HTTP caching, retry-safety by method and tooling for free, but a screen needing five
  resources costs five round trips; this flips to GraphQL when client-side shape variety dominates.
- GraphQL gives clients exact shapes and one evolving schema, but you give up HTTP caching, must
  defend against expensive queries and must batch resolvers; it usually flips back to REST for public
  APIs, where caching and per-endpoint limits matter (GitHub and Shopify are notable GraphQL exceptions).
- Returning `202` and a job resource keeps requests short and proxy-friendly, but clients must poll
  or subscribe, which costs more code than one long request when jobs are reliably under a few seconds.
- A single error envelope and strict status codes make clients simple, but they force you to decide
  the code for every failure up front rather than leaking exceptions as 500s.

## Pitfalls
- Verbs in URLs and everything as `POST`; the symptom is that nothing can be cached or safely retried
  and observability shows one undifferentiated endpoint.
- Returning errors with status 200 and a flag in the body; the symptom is retry and alerting
  policies that never fire and SDKs that treat failures as success.
- GraphQL resolvers without DataLoader; the symptom is query count scaling with list length in the
  database logs (the N+1 pattern).
- Holding a request open for a multi-minute AI job; the symptom is intermittent 504s from a proxy
  whose idle timeout you never configured, and clients retrying work that already ran.
- Read-modify-write updates without `ETag`/`If-Match` or a version field; the symptom is "my change
  disappeared" reports when two users or two agents edit the same resource within seconds.
- Leaving GraphQL query depth and cost unlimited; the symptom is one client's nested query pinning
  the database while everything else times out.

## When to use
Default to REST with an OpenAPI schema for AI service endpoints, internal service-to-service calls
and anything an agent will call as a tool; the schema becomes the client, the docs and the tool
definition. Reach for GraphQL when a product team owns a UI that assembles many shapes from a shared
domain and is paying for it in round trips and endpoint sprawl. Measure round trips per screen,
cache hit rate and p99 latency before switching either way. Day to day, the work is naming resources,
choosing status codes deliberately and designing the long-running-job pattern. In interviews, expect
"design the API for X" questions that test whether you reach for idempotency, `202` plus polling or
streaming, and a consistent error shape without being prompted.

## Role lens
- **Engineer:** Designs resource paths and status codes, implements `202` plus polling or SSE for
  model calls, and keeps the OpenAPI schema accurate enough to generate clients and tool definitions.
- **Architect:** Decides which style each boundary uses (GraphQL in the BFF, REST between services and
  outward), sets the error envelope and idempotency conventions, and reviews breaking changes.
- **FDE:** Maps a customer's existing REST or GraphQL conventions onto the AI service's contract and
  explains why model endpoints need async job patterns their CRUD APIs never required.

## Check yourself
**Q:** You are designing the API for a document-analysis service whose jobs take 30 seconds to 5
minutes and whose results are read by a web UI, a partner integration and an internal agent. Which
style and request pattern do you choose, and why?
**A:** REST with an OpenAPI schema, because three different clients including an agent need a
contract that generates clients and tool definitions, and partners expect it. `POST /analyses` returns
`202 Accepted` with a `Location` and a job resource; clients `GET` it until `status` is terminal, or
subscribe to an SSE stream of progress events for the UI. The `POST` accepts an `Idempotency-Key` so a
partner's retry after a timeout does not run the job twice. Results live at `/analyses/{id}/result`
and are cacheable once terminal. I would not pick GraphQL here: the shapes are few, the clients are
mostly machines, and long-running work plus caching favour HTTP semantics.

## Quiz
### Q: Why return `202 Accepted` with a `Location` header for a five-minute AI job instead of holding the request open until it finishes?
- [ ] Because `200` cannot carry a JSON body larger than the proxy buffer
- [x] Proxies and clients have idle timeouts, and a short request plus a pollable job resource survives them and is retry-safe
- [ ] Because `202` enables HTTP caching of the eventual result
- [ ] Because FastAPI cannot await for more than 60 seconds
> Long-held connections hit proxy and client timeouts and make retries ambiguous. A job resource
> turns the work into state that can be polled, cached when terminal and cancelled.

### Q: Why can an ordinary HTTP cache not store GraphQL responses by default?
- [ ] GraphQL responses are always compressed
- [ ] The GraphQL spec forbids caching
- [x] Queries go as `POST` to one endpoint, so the URL does not identify the resource being read
- [ ] GraphQL responses never include a `Content-Type` header
> HTTP caches key on method and URL. With every query a `POST` to `/graphql`, there is nothing to key
> on; persisted queries over `GET` are the workaround.

### Q: A GraphQL list of 100 conversations resolving `lastMessage` issues 101 database queries. What is the standard fix?
- [ ] Switch the field to a REST endpoint
- [x] A per-request DataLoader that batches and caches the lookups
- [ ] Increase the database connection pool to 101
- [ ] Mark the field as `@deprecated`
> Resolvers run per parent object, so without batching each one queries alone. DataLoader collects the
> keys requested in one tick and issues a single batched query.
