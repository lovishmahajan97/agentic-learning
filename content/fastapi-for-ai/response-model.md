---
id: response-model
module: fastapi-for-ai
name: response_model
kind: concept
difficulty: core
est_minutes: 11
status: reviewed
related:
  - fastapi-for-ai/pydantic-request-response-models
  - python-for-ai/pydantic
  - fastapi-for-ai/openapi-docs
  - fastapi-for-ai/openapi-contract-testing
  - fastapi-for-ai/streamingresponse
  - safety-security/output-validation
  - backend-fundamentals/api-versioning-and-backward-compatibility
sources:
  - Official docs: FastAPI
  - Official docs: Pydantic
---

## TL;DR
`response_model` (or the endpoint's return annotation) tells FastAPI the exact shape a route may
return. The framework validates the returned data against it, serialises *through* it so only declared
fields leave the process, and publishes it in OpenAPI. It exists because what a handler happens to
return is not a contract; a declared output schema is. For AI services it is the cheapest guarantee
that internal fields and raw provider payloads never reach a client.

## Why it exists
A handler's return value is whatever the last line of code built. Over time that drifts: someone
attaches the raw provider response for debugging, an ORM object grows a `cost_usd` column, a dict picks
up a `debug_prompt` key behind a feature flag. Without an output contract, each of these ships to every
client the day it is merged, and the OpenAPI document — generated from nothing — describes an empty
object. Clients write code against the accidental shape, and the accidental shape becomes the API you
can never change.

The naive alternative, "be careful about what you return", has no enforcement: the leak happens in a
code path the reviewer did not read, or in an exception branch that returns a different dict. A second
naive alternative, "construct the output model by hand in every handler", works until one handler
returns the ORM object directly because it has the same fields, and now the ORM object's next
attribute is public too.

`response_model` moves the contract from the handler to the route declaration, where it applies to
every return path. Validation catches a shape bug on the server, as a 500 with a clear error in your
logs, rather than in a client's parser. Filtering turns the model into an allow-list: the handler can
return a superset — a dict, an ORM row, an internal object — and only declared fields are serialised.
And because the same declaration feeds OpenAPI, documentation cannot drift from behaviour. The
reasoning mirrors request models: the framework enforces the contract *because* handlers will not do
so reliably.

## How it works
### Declaring it
Either `@app.get("/x", response_model=Item)` or a return annotation `async def f() -> Item:`; recent
FastAPI versions treat the annotation as the response model, and the explicit parameter wins when both
are present. `response_model=None` turns the machinery off, which you need when the annotation is a type the
validator cannot build a field for, such as `Response | dict`. A plain `Response` subclass
annotation (`-> StreamingResponse`) is detected and skipped automatically.

### What happens to the return value
1. The handler returns something: a model instance, a dict, an ORM object, a list.
2. If it is already a `Response` (including `StreamingResponse`, `JSONResponse`, `RedirectResponse`),
   FastAPI sends it as-is. No validation, no filtering, no response-model serialisation, *because* you
   have taken over the wire format; streaming token endpoints therefore get no protection from
   `response_model`.
3. Otherwise the value is validated against the response model. A dict or object with extra keys is
   accepted and the extras dropped; a missing required field or wrong type raises
   `ResponseValidationError`, which surfaces as a 500 so the bug is visible to you rather than shipped
   silently as a wrong shape.
4. The validated data is serialised with the model's rules: aliases when `response_model_by_alias` is
   true (the default), and `response_model_include`, `response_model_exclude`,
   `response_model_exclude_unset`, `response_model_exclude_defaults` and `response_model_exclude_none`
   control which fields appear.
5. The result goes through the route's `response_class` (`JSONResponse` by default) to bytes. The
   serialisation fast path has changed across FastAPI releases (`ORJSONResponse` was the classic speed
   option), so check your version's docs before optimising it.

### The filtering semantics that matter
- Undeclared fields on the returned object are dropped. This is the allow-list behaviour and the
  security property.
- `response_model_exclude_unset=True` emits only fields the handler explicitly set, so model defaults
  do not appear; useful for partial responses, dangerous if clients rely on defaults being present.
- `response_model_exclude_none=True` drops `None` values; optional fields disappear rather than appear
  as `null`, which changes what clients must handle.
- Unions: `response_model=Union[Chunk, Done]` validates against the branches; a discriminated union
  makes the choice deterministic and the OpenAPI `oneOf` readable.
- Lists: `response_model=list[Embedding]` validates every element, which is where the cost shows up for
  large payloads.

### Interaction with OpenAPI
The response model becomes the `200` response schema in the generated document, so SDK generators and
contract tests work from the same source as the server. Other status codes are declared with
`responses={404: {"model": ErrorOut}}`.

### Cost
Validating and re-serialising the output is extra CPU proportional to payload size, spent on the event
loop for `async def` routes (FastAPI moves it to the thread pool for `def` routes).
For chat responses it is negligible. For an endpoint returning thousands of embedding vectors it is
measurable, and returning a pre-built `ORJSONResponse`, giving up validation on that route, is a
legitimate choice once you have measured it.

## Example
A public model, an internal subclass that adds fields which must never leave the process, and a
handler that returns the internal object unchanged. Subclassing keeps the type checker happy (a
`CompletionInternal` *is* a `CompletionOut`), and FastAPI still filters: it does not trust
`isinstance`, it dumps the returned object and re-validates the data through the declared model, so
only `CompletionOut`'s four fields survive. `finish_reason` is omitted when `None` because of
`response_model_exclude_none`. Returning a dict without `text` would raise `ResponseValidationError`
and produce a 500 rather than a malformed success.

```python
from typing import Annotated, Optional
from fastapi import Body, FastAPI
from pydantic import BaseModel

app = FastAPI()

class CompletionOut(BaseModel):               # what clients receive
    id: str
    text: str
    model: str
    finish_reason: Optional[str] = None

class CompletionInternal(CompletionOut):      # what the service works with
    cost_usd: float                           # never public
    provider_raw: dict                        # never public

def run_model(prompt: str) -> CompletionInternal:
    return CompletionInternal(id="cmpl_1", text=prompt[::-1], model="demo-1", cost_usd=0.0003,
                              provider_raw={"headers": {"x-ratelimit-remaining": "99"}})

@app.post("/complete", response_model=CompletionOut, response_model_exclude_none=True)
async def complete(prompt: Annotated[str, Body(embed=True)]) -> CompletionOut:
    internal = run_model(prompt)
    return internal        # superset is fine: cost_usd and provider_raw are dropped on the way out

# POST /complete {"prompt": "abc"} -> {"id": "cmpl_1", "text": "cba", "model": "demo-1"}
# GET /openapi.json shows CompletionOut as the 200 schema; cost_usd and provider_raw do not appear.
```

## Trade-offs
- Filtering gives a hard allow-list on output, but it also silently drops fields you forgot to declare;
  the protection flips into a debugging trap when a handler "returns the right thing" and the client
  never sees it.
- Validation surfaces shape bugs as 500s instead of malformed success responses, which is right for
  correctness, but means a benign internal change can take a route down until the model is updated.
- Declaring the model in the return annotation keeps code and contract together, yet an annotation
  that is not a valid Pydantic type (for example a union of `Response` and `dict`) needs
  `response_model=None` or an explicit model.
- Re-serialising through the model costs CPU per byte of output; negligible for chat, measurable for
  bulk embeddings, where a raw `ORJSONResponse` trades the guarantee for speed.

## Pitfalls
- Returning a `StreamingResponse` or `JSONResponse` and believing `response_model` still filters it;
  symptom: internal fields appear only on the streaming route, and OpenAPI claims a schema the route
  does not enforce.
- Using `response_model_exclude_unset` on a model whose defaults clients depend on; symptom: fields
  "randomly" missing from responses depending on which code path set them.
- Reusing the request model as the response model to save a class; symptom: clients must send fields
  that only make sense in output, and output exposes fields that only make sense in input.
- Treating a `ResponseValidationError` 500 as a library bug and disabling response validation; symptom:
  a handler branch returns the wrong shape and ships for months.
- Putting `extra="forbid"` on response models (often via a shared base class copied from request
  models); the superset a handler returns is now a validation failure instead of being filtered.
  Symptom: routes that return ORM rows or internal objects start failing with 500s and
  `extra_forbidden` in the `ResponseValidationError`. Forbid extras on input, ignore them on output.
- Annotating a handler `-> dict` or `-> Any`; symptom: the contract silently loosens and filtering
  stops, without anyone having touched `response_model=`.

## When to use
Declare a response model on every JSON route, separate from the request model and from internal
objects, and make it the only place output shape is defined. Turn it off deliberately, with
`response_model=None` and a comment, for routes that return a `Response` (streaming tokens, file
downloads) and for bulk routes where measurement shows serialisation cost matters; those routes need
another output check, such as a contract test. Review `response_model_exclude_*` options as API
decisions, not conveniences, since each changes what clients must handle. In interviews the classic
question is "what does `response_model` do that a return type does not?", and the answer is
validation, filtering and documentation on every return path, plus the `Response` escape hatch.

## Role lens
- **Engineer:** Declares `response_model` per route, keeps an `*Out` model per resource, and
  investigates `ResponseValidationError` logs as shape bugs rather than silencing them.
- **Architect:** Treats the set of response models as the versioned public contract and decides where
  filtering is relaxed for throughput and how that gap is covered.
- **FDE:** Shows a customer, via `/openapi.json`, exactly which fields a route returns, and uses
  `response_model` to hide the customer's own internal metadata from downstream consumers.

## Check yourself
**Q:** A team's `/complete` route returns an ORM object; the model it is declared with has `id`, `text`
and `model`. A new column `prompt_hash` is added to the ORM table, and separately the team adds a
`/complete/stream` route returning a `StreamingResponse` with the same `response_model`. What is and is
not protected, and why?
**A:** On `/complete` the new column is not exposed: FastAPI validates the ORM object against the
declared model and serialises only `id`, `text` and `model`, so `prompt_hash` is filtered as an
undeclared field; the declaration acts as an allow-list on every return path. On `/complete/stream`
nothing is protected: a `StreamingResponse` is a `Response`, and FastAPI sends `Response` objects as-is
without validation or filtering, so whatever the generator yields, including `prompt_hash` if the
chunks are built from the ORM row, reaches the client; the `response_model` there only affects OpenAPI
and misleads readers. The fix is to build stream chunks from an explicit chunk model, set
`response_model=None` with a comment on the streaming route, and cover it with a contract test.

## Quiz
### Q: A handler declared with `response_model=Out` returns a dict containing every `Out` field plus `cost_usd`. What does the client receive?
- [ ] A 500, because extra fields fail validation
- [x] A JSON object with only the `Out` fields; `cost_usd` is dropped
- [ ] All fields, because dicts bypass the response model
- [ ] The `Out` fields plus `cost_usd` under an `extra` key
> Response-model serialisation is an allow-list: declared fields are validated and emitted, undeclared
> ones are discarded, which is the security property the mechanism exists for.

### Q: Which return value is sent to the client without any `response_model` validation or filtering?
- [ ] A Pydantic model instance of a different class
- [ ] A plain dict
- [x] A `Response` object such as `StreamingResponse` or `JSONResponse`
- [ ] A list of Pydantic models
> FastAPI treats a returned `Response` as the final wire format and does not touch it; that is the
> escape hatch for streaming and custom encodings, and also the point where the contract stops applying.

### Q: What is the effect of `response_model_exclude_unset=True`?
- [ ] Fields with `None` values are omitted
- [x] Only fields the handler explicitly set are emitted; model defaults are left out
- [ ] Required fields may be omitted without a validation error
- [ ] The response skips validation to save CPU
> `exclude_unset` serialises just the fields that were set on the returned object, which suits partial
> responses but surprises clients that expect default-valued fields to be present.
