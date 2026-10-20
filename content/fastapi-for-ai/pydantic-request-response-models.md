---
id: pydantic-request-response-models
module: fastapi-for-ai
name: Pydantic request / response models
kind: pattern
difficulty: core
est_minutes: 14
related:
  - python-for-ai/pydantic
  - fastapi-for-ai/response-model
  - fastapi-for-ai/openapi-docs
  - fastapi-for-ai/httpexception-handling
  - prompting/structured-outputs-json-mode
  - agents/tool-schemas-and-argument-validation
  - backend-fundamentals/rest-graphql-api-design
  - backend-fundamentals/api-versioning-and-backward-compatibility
sources:
  - Official docs: FastAPI
  - Official docs: Pydantic
---

## TL;DR
A FastAPI endpoint declares its request body and its response as Pydantic models. The framework
validates inbound JSON against the request model (rejecting bad input with a 422 that names the field)
and validates, filters and documents outbound data against the response model. For AI APIs this is the
contract that stops malformed messages, out-of-range sampling parameters and oversized prompts before
they cost money, and stops internal fields (system prompts, costs, raw provider payloads) from leaking
out.

## Why it exists
An AI endpoint takes rich, nested input — a list of messages with roles, optional tool definitions,
sampling parameters, tenant metadata — and returns rich output. If the handler reads a raw dict, every
field needs a hand-written check: is `messages` a list, does each item have `role` and `content`, is
`temperature` a number between 0 and 2, is `max_tokens` an integer. Teams skip those checks, and the
failures arrive late and expensively: the provider rejects the call after a network round trip, or
accepts a nonsense value and returns garbage, or a typo like `"temprature"` silently falls back to the
default and nobody notices for a month. Output is worse: handlers return whatever dict they built, so
a refactor that adds `raw_provider_response` or `cost_usd` to an internal object leaks straight to the
customer.

Hand-rolled validation fails because it is unenforced, undocumented and inconsistent across endpoints:
three routes validate `messages` three ways, the OpenAPI document says nothing about any of them, and
client teams discover the rules by trial. A separate schema file enforces shape but lives outside the
code and drifts from it.

Pydantic models are Python classes, so the contract lives next to the handler, carries types the IDE
and mypy understand, and generates JSON Schema for OpenAPI automatically. FastAPI runs validation
before the handler is called, *because* the handler should only ever see data that already satisfies
the contract, and runs response validation after it, *because* the output contract must hold
regardless of which code path produced the data.

## How it works
### Inbound: body → model → handler
1. FastAPI reads the ASGI body events, parses JSON and, for each parameter annotated with a `BaseModel`
   subclass, calls Pydantic validation on the parsed data. Query, path and header parameters are
   validated the same way from their own sources.
2. Validation coerces and checks: `"0.7"` becomes `0.7` for a `float` field under Pydantic's default
   lax mode, missing required fields and wrong types produce errors, `Field(ge=0, le=2)` enforces
   ranges, `Literal["user", "assistant", "system"]` restricts enumerations, `max_length` bounds lists
   and strings, and `@field_validator` / `@model_validator` express cross-field rules such as "either
   `prompt` or `messages`, not both".
3. On failure FastAPI raises `RequestValidationError` and returns HTTP 422 with a `detail` list: each
   entry has `loc` (the path to the field, such as `["body", "messages", 1, "role"]`), `msg` and `type`.
   Clients can fix the exact field without reading server logs.
4. On success the handler receives a typed model instance, not a dict.

Extra fields are ignored by default. For AI parameters that is dangerous — a misspelt `max_tokens`
silently disappears — so request models usually set `model_config = ConfigDict(extra="forbid")`,
turning typos into 422s with an `extra_forbidden` error.

### Outbound: return value → response model → JSON
When a route declares a response model (via `response_model=` or the return annotation), the returned
object is validated against it and serialised through it: only declared fields are emitted, alias and
`exclude_none` options apply, and a return value that does not fit raises a server-side validation
error instead of shipping a wrong shape. The response model is also what OpenAPI shows clients. The
`response_model` lesson covers the options in detail; this one is about designing the pair.

### Design rules that matter for AI APIs
- **Separate models per direction.** `ChatRequest` and `ChatResponse` evolve independently; sharing one
  class forces every field to be optional and leaks internals. Internal objects (provider payloads,
  DB rows) get their own models that never reach a route signature.
- **Discriminated unions for message content.** A `content` that is either text or a list of parts
  (text, image URL, tool result) is a `Union` with `Field(discriminator="type")`, so validation picks
  the branch from the `type` field and errors point at the right branch instead of listing all of them.
- **Bound everything that costs money.** `max_tokens: int = Field(default=1024, ge=1, le=8192)`,
  `messages: list[Message] = Field(min_length=1, max_length=200)`, and a model validator that caps
  total characters, so prompt-size limits are enforced at the contract rather than discovered as
  provider errors or surprise bills.
- **Version by model, not by flag.** A breaking change gets a new request model on a new route;
  additive optional fields with defaults are backward compatible.
- **Strict where coercion would hide bugs.** Pydantic's strict mode (`ConfigDict(strict=True)` or
  `Strict*` types) stops `"1"` being accepted as `1` when a client sending strings is itself the bug.

### Why validation lives in the framework and not the handler
Running it before the handler means dependencies that need the body (a token-budget check, a tenant
quota) see validated data too; running response validation in the framework means a code path cannot
forget it. Pydantic's validation core is compiled, so for typical chat payloads validation costs
well under a millisecond and is negligible next to a model call.

## Example
A chat endpoint with a forbidding request model, bounded sampling parameters, a discriminated content
union, a cross-field rule and a separate response model that exposes usage but not cost. Sending
`{"messages": [{"role": "user", "content": "hi"}], "temprature": 0.1}` returns a 422 whose `loc` is
`["body", "temprature"]` and whose `type` is `extra_forbidden`.

```python
from typing import Annotated, Literal, Union
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, model_validator

class TextPart(BaseModel):
    type: Literal["text"]
    text: str = Field(max_length=50_000)

class ImagePart(BaseModel):
    type: Literal["image_url"]
    url: str

Part = Annotated[Union[TextPart, ImagePart], Field(discriminator="type")]

class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: Union[str, list[Part]]

class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")          # "temprature" -> 422, not silently ignored
    messages: list[Message] = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1, le=8192)
    stream: bool = False

    @model_validator(mode="after")
    def first_message_not_assistant(self):
        if self.messages[0].role == "assistant":
            raise ValueError("conversation must start with a system or user message")
        return self

class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int

class ChatResponse(BaseModel):
    id: str
    content: str
    usage: Usage                                         # cost_usd and provider payload stay internal

app = FastAPI()

@app.post("/v1/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    # ... call the model here; stubbed ...
    return ChatResponse(id="msg_1", content="hi", usage=Usage(prompt_tokens=12, completion_tokens=1))
```

## Trade-offs
- Strict contracts catch client errors early and document themselves, but every provider feature you
  want to pass through needs a model change; the trade flips toward a permissive pass-through field
  when you proxy a fast-moving provider API.
- `extra="forbid"` turns typos into 422s, which protects money and correctness, but makes additive
  client changes fail against older servers; relax it only on fields explicitly designed as open bags.
- Validation on every request and response costs CPU on the loop; negligible for chat payloads, it
  flips to noticeable for responses carrying thousands of embedding vectors, where returning a raw
  `Response` can be justified.
- Separate request, response and internal models triple the class count, but a single shared model
  couples the API to storage and leaks fields the moment someone adds one.

## Pitfalls
- Leaving `extra` at its default and relying on clients to spell parameters correctly; symptom: a
  parameter "has no effect" for weeks because it never reached the provider.
- Using one model for the DB row, the request and the response; symptom: a new internal column
  (`cost_usd`, `system_prompt`) appears in API output after an unrelated change.
- Returning the provider's raw response dict as your response; symptom: your API's shape changes when
  the provider's does, and provider metadata reaches customers.
- Unbounded `max_tokens` and message lists; symptom: a single request produces an outsized provider
  bill or a timeout, and the 422 that should have stopped it never fires.
- Catching `RequestValidationError` and returning a generic "bad request"; symptom: client teams cannot
  tell which field was wrong and support tickets replace self-service fixes.

## When to use
Every JSON endpoint gets a request model and a response model; the question is how strict and how
many. Use `extra="forbid"` and ranged `Field`s for anything that controls model behaviour or cost. Use
discriminated unions when a field can take several shapes. Use separate models for input, output and
internal state from day one, because splitting them later is a breaking change. Watch the 422 rate per
field as a product metric: a spike after a client release points at a schema mismatch. In interviews,
be ready to design the request model for a chat endpoint out loud, including bounds and the 422
payload, and to explain why the response model is not the same class.

## Role lens
- **Engineer:** Writes the models with bounds and validators, keeps internal and external models apart,
  and reads 422 `loc` paths to debug client integrations quickly.
- **Architect:** Treats request and response models as the public API contract, owning its versioning
  policy and which provider features are exposed or hidden.
- **FDE:** Maps a customer's existing message format onto the request model with a discriminated union
  or an adapter, and uses the 422 output to speed up their integration.

## Check yourself
**Q:** Your chat endpoint proxies to a hosted model. A customer complains that setting `"top_p": 0.1`
changes nothing, and separately a security review finds the `system_prompt` your service injects
appears in API responses. Diagnose both using request and response models.
**A:** The `top_p` symptom means the request model does not declare the field and allows extra fields
by default, so Pydantic discards it silently and the provider never sees it; either add
`top_p: float = Field(ge=0, le=1)` and forward it, or set `extra="forbid"` so unknown fields return a
422 that tells the customer the parameter is unsupported. The leak means the response model is either
missing or the same class as the internal object that carries the injected prompt; define a dedicated
`ChatResponse` with only the public fields and declare it on the route, so FastAPI filters the output
and OpenAPI documents exactly what clients receive. Add a contract test asserting `system_prompt` is
absent from the serialised response.

## Quiz
### Q: A client sends `{"messages": [...], "temprature": 0.2}` to an endpoint whose request model does not set `extra`. What happens?
- [ ] A 422 lists `temprature` as an unknown field
- [x] The request succeeds and the misspelt field is silently dropped
- [ ] The value is assigned to `temperature` by fuzzy matching
- [ ] The handler receives it in a `model_extra` dict and must handle it
> Pydantic's default is to ignore extra fields, so the typo disappears; `ConfigDict(extra="forbid")`
> makes it a 422 with an `extra_forbidden` error pointing at the field.

### Q: Why should the response model be a different class from the request model and from internal objects?
- [ ] Pydantic cannot validate the same class in both directions
- [x] So output fields are an explicit allow-list and internal additions cannot leak to clients
- [ ] Because FastAPI requires unique class names per route
- [ ] To reduce JSON size by removing type information
> Response filtering only protects you if the response model lists exactly the public fields; reusing
> an internal class means every new internal attribute becomes public by default.

### Q: What does the `loc` entry `["body", "messages", 1, "role"]` in a 422 response tell the client?
- [ ] The request had one too many messages
- [ ] The second message is missing entirely
- [x] The `role` field of the second message (index 1) failed validation
- [ ] The body must be sent as form data, not JSON
> `loc` is the path from the request source down to the offending value, so clients can fix the precise
> field instead of guessing from a generic error message.
