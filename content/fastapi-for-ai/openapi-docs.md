---
id: openapi-docs
module: fastapi-for-ai
name: OpenAPI docs
kind: tool
difficulty: intro
est_minutes: 12
related:
  - fastapi-for-ai/pydantic-request-response-models
  - fastapi-for-ai/response-model
  - fastapi-for-ai/routers
  - fastapi-for-ai/openapi-contract-testing
  - fastapi-for-ai/server-sent-events
  - agents/tool-schemas-and-argument-validation
  - backend-fundamentals/api-versioning-and-backward-compatibility
sources:
  - Official docs: FastAPI
  - Official docs: OpenAPI Specification
  - Official docs: Pydantic
---

## TL;DR
FastAPI derives an OpenAPI document from your route signatures and Pydantic models, serves it at
`/openapi.json`, and renders it as interactive docs at `/docs` (Swagger UI) and `/redoc`. Because the
schema comes from the same type hints that validate requests, it cannot drift from the code the way a
hand-written spec does, which makes it a reliable source for client SDKs, contract tests and even LLM
tool definitions.

## Why it exists
Every API has a contract whether or not it is written down: field names, types, which fields are
required, which status codes mean what. Without a machine-readable contract, front-end teams read your
handlers, customers reverse-engineer payloads from examples, and the SDK someone wrote by hand breaks
the day you rename a field. Hand-maintained specs (a YAML file in a docs repo) solve readability but
introduce a second source of truth that drifts within weeks, because nothing fails when the code
changes and the YAML does not.

FastAPI's answer is to make the contract a by-product of the code. The type hints that parse and
validate a request are the same objects that generate the schema, so if the handler accepts a field,
the schema documents it. For AI services this matters more than usual: chat and completion payloads are
nested (messages, tool calls, content parts), they change often as providers add features, and agents
increasingly consume APIs through tool definitions generated from OpenAPI, where a vague description or
a missing enum directly degrades the model's ability to call you correctly.

## How it works
### Generation
On the first request to `/openapi.json`, FastAPI calls `app.openapi()`, which walks the route table and,
for each path operation, collects the path, method, parameters (path, query, header, cookie with their
types and defaults), the request body model and the response model. Pydantic generates JSON Schema for
each model, and FastAPI places shared models under `components/schemas` with `$ref` pointers. The result
is cached on `app.openapi_schema`, so it is generated once per process; recent FastAPI versions emit
OpenAPI 3.1, which aligns with JSON Schema.

### What feeds it
- **Models and fields:** `Field(description=..., examples=[...])`, constraints such as `ge`, `max_length`
  and `Literal`/`Enum` types all appear in the schema. These are what client generators and LLMs read.
- **Operation metadata:** `summary`, `description` (or the docstring), `tags` (which group operations in
  the UI), `operation_id`, `deprecated=True` and `include_in_schema=False`.
- **Responses:** `response_model` defines the 200 body; `responses={404: {"model": ErrorOut}}` documents
  errors that the handler raises. FastAPI cannot infer those from `raise HTTPException`, because
  exceptions are not part of a signature, so undocumented error shapes are the most common gap.
- **App metadata:** `FastAPI(title=..., version=..., openapi_url=..., docs_url=..., redoc_url=...)`;
  setting a URL to `None` disables that endpoint.

### Operation IDs
Generated clients name methods after `operationId`. FastAPI's default concatenates the function name
with path and method, giving names like `complete_v1_chat_completions_post`. Passing
`generate_unique_id_function` (for example, tag plus function name) produces stable, readable SDK
methods, and renaming a Python function then becomes a visible SDK change rather than an accident.

### Input versus output schemas
A model can serialize differently from how it validates (defaults, computed fields). With Pydantic v2,
FastAPI can emit separate `Model-Input` and `Model-Output` schemas when they differ, so a field that is
optional on input but always present on output is described honestly on both sides.

### What it describes poorly
OpenAPI describes request/response pairs. A Server-Sent Events stream of tokens or a WebSocket protocol
has no first-class representation; you can declare `text/event-stream` as the content type, but the
per-event schema has to be documented in prose or a separate model. Customising beyond this means
overriding `app.openapi` to post-process the dict, which is powerful but now hand-maintained.

## Example
Documenting an embeddings endpoint so the schema is useful to a client generator and to an LLM tool
definition: described fields, a constrained enum, a documented error and a stable operation id.

```python
from typing import Literal
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

def op_id(route: APIRoute) -> str:
    return f"{route.tags[0]}_{route.name}" if route.tags else route.name

app = FastAPI(title="retrieval-api", version="1.4.0", generate_unique_id_function=op_id)

class EmbedIn(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=256,
                             description="Texts to embed; each is truncated to the model's limit.")
    model: Literal["small", "large"] = Field("small", description="Embedding size tier.")

class EmbedOut(BaseModel):
    vectors: list[list[float]]
    model: str
    dimensions: int

class ErrorOut(BaseModel):
    code: str
    message: str

@app.post("/v1/embeddings", tags=["embeddings"], response_model=EmbedOut,
          responses={429: {"model": ErrorOut, "description": "Tenant quota exceeded"}},
          summary="Embed a batch of texts")
async def create(body: EmbedIn) -> EmbedOut:
    if len(body.texts) > 200:
        raise HTTPException(status_code=429, detail="quota")
    return EmbedOut(vectors=[[0.0] * 8 for _ in body.texts], model=body.model, dimensions=8)

schema = app.openapi()
print(schema["paths"]["/v1/embeddings"]["post"]["operationId"])   # embeddings_create
print(schema["components"]["schemas"]["EmbedIn"]["properties"]["model"]["enum"])  # ['small', 'large']
```

The generated SDK method is `embeddings_create`, the `model` field is an enum a client or LLM can pick
from, and the 429 shape is documented even though the handler only raises an exception.

## Trade-offs
- Code-first schemas never drift from validation, but the API design is only as deliberate as the type
  hints; when several teams must agree on a contract before code exists, design-first YAML wins.
- Public `/docs` speeds up integration for partners, but it also maps your attack surface for anyone;
  for internet-facing services disable it in production or put it behind auth.
- Rich descriptions and examples make SDKs and agent tool definitions better, but they lengthen the
  schema; when an LLM consumes it as tools, every description costs prompt tokens on each call.
- Overriding `app.openapi` lets you add vendor extensions or SSE event docs, but the override becomes
  hand-maintained code that can drift exactly like the YAML you were avoiding.

## Pitfalls
- Raising `HTTPException(404)` or a custom error without listing it in `responses`; the symptom is client
  SDKs that crash or return untyped blobs on errors that the docs never mentioned.
- Returning a dict without `response_model` or a return annotation; the symptom is an empty response
  schema and a generated client whose return type is `Any`.
- Leaving default operation IDs; the symptom is SDK method names like `complete_v1_chat_completions_post`
  that change whenever a path is edited, breaking downstream code on a refactor.
- Documenting a streaming endpoint as JSON; the symptom is generated clients that wait for a complete
  JSON body and hang or fail to parse an event stream.

## When to use
You get the schema for free, so the decision is how much to invest in it. Invest heavily (descriptions,
enums, error responses, stable operation IDs) when external clients, generated SDKs or agents consume the
API; invest lightly for a single internal caller. Decide explicitly whether `/docs` is reachable in
production. Treat `/openapi.json` as an artifact: export it in CI, diff it on every pull request and use
it for contract tests. In interviews, the useful point is that FastAPI's docs are derived from validation
types, which is why they stay accurate, and that error responses and streams are the parts you must add.

## Role lens
- **Engineer:** Writes field descriptions, constrains types with `Literal` and bounds, declares error
  `responses`, and sets an operation-id function so generated clients are stable.
- **Architect:** Decides code-first versus design-first, whether the schema is a published contract with
  versioning rules, and whether docs are exposed per environment.
- **FDE:** Hands the customer's integration team the OpenAPI URL or a generated SDK, and turns the schema
  into tool definitions for the customer's agent platform.

## Check yourself
**Q:** A customer's agent framework imports your OpenAPI document as tools, and the model keeps calling
`/v1/search` with invalid `filter` values and ignoring a 429 retry hint. What would you change in the
FastAPI code, and why does it help?
**A:** The model only sees what the schema says, so I would tighten the schema. Replace a free-form
`filter: str` with a `Literal` or enum of allowed values, or a structured model, and add field
descriptions with an example, because the tool definition is generated from exactly those fields. Declare
the 429 response with a typed error model that includes a `retry_after_seconds` field so the framework can
surface it. Give the operation a clear `summary` and stable `operation_id`, since that becomes the tool
name. I would also keep descriptions short, because they are sent as prompt tokens each call, and verify
the change by exporting the schema and diffing it.

## Quiz
### Q: Why does FastAPI's OpenAPI document tend to stay accurate as the code changes?
- [ ] FastAPI re-reads the docstrings of every module at startup and validates them
- [x] It is generated from the same type hints and Pydantic models that validate requests
- [ ] A CI hook in FastAPI rejects commits where the schema and code differ
- [ ] Swagger UI inspects live traffic and updates the schema
> The schema is derived from the signatures that also parse and validate requests, so changing a model
> changes both at once. Nothing checks docstrings; prose descriptions can still go stale.

### Q: A handler raises `HTTPException(status_code=409)` but clients' generated SDKs do not know about it. Why?
- [ ] FastAPI only documents status codes below 400
- [ ] The exception must subclass `ValueError` to be documented
- [x] Raised exceptions are not part of the function signature, so they must be declared in `responses`
- [ ] Generated SDKs ignore all non-200 responses by design
> FastAPI can infer the success schema from `response_model` or the return annotation, but it cannot see
> which exceptions a body might raise. Declaring `responses={409: {"model": ...}}` adds them to the schema.

### Q: What is the main reason to set `generate_unique_id_function`?
- [ ] It is required for Swagger UI to render endpoints
- [ ] It speeds up schema generation for large apps
- [x] Generated clients name methods after operation IDs, so readable, stable IDs give a stable SDK
- [ ] It prevents two routes from sharing the same path
> Default IDs combine the function name, path and method, so they are long and change when paths change.
> A custom function keyed on tag and name keeps SDK method names readable and stable across refactors.
