---
id: pydantic
module: python-for-ai
name: Pydantic
kind: tool
difficulty: core
est_minutes: 16
related:
  - python-for-ai/type-hints
  - python-for-ai/dataclasses
  - python-for-ai/typeddict-literal-enum
  - python-for-ai/pydantic-settings
  - python-for-ai/instructor
  - fastapi-for-ai/pydantic-request-response-models
  - prompting/structured-outputs-json-mode
  - agents/tool-schemas-and-argument-validation
sources:
  - Official docs: Pydantic
  - Official docs: JSON Schema
---

## TL;DR
Pydantic turns type-annotated classes into validators, parsers, serializers and JSON Schema
generators. You declare the shape once as a `BaseModel`; untrusted input (API payloads, LLM output,
config files) is validated into typed objects with structured error reports, and the same class
emits the JSON Schema you hand to a model for structured output or tool calling. It exists because
AI systems are mostly untrusted JSON crossing boundaries, and type hints alone do nothing at runtime.

## Why it exists
Type hints are inert: a function typed `-> Ticket` will happily return a dict with a misspelled
key. The data that flows through an AI system is almost entirely untrusted at the point it arrives:
HTTP request bodies, model completions that are supposed to be JSON, tool arguments a model
invented, environment variables, YAML configs, rows from a customer's export. Every one of those
can be malformed, and a language model will malform them creatively: a string where an int was
asked for, an extra field, a value outside the allowed set, valid-looking JSON with a trailing
sentence.

The naive alternatives are each inadequate. Hand-written `if "title" not in data` checks grow into
hundreds of lines that drift from the types they claim to enforce, and their error messages are
whatever each author bothered to write. A `TypedDict` or `dataclass` documents the shape for the
checker but validates nothing. A separate schema language (a JSON Schema file maintained next to
the class) duplicates the definition and the two disagree within a month.

Pydantic collapses these into one artifact. The annotated class is simultaneously the static type,
the runtime validator, the serializer and the JSON Schema source. For LLM work this is the lever:
the schema you send to the model and the validator you run on its answer are generated from the
same class, so they cannot disagree, and the validation error list is structured enough to feed
back to the model for a repair attempt. FastAPI, the OpenAI and Anthropic SDK structured-output
helpers, `instructor`, LangChain and most agent frameworks build on exactly this property.

## How it works
### From annotations to a validator
At class creation, `BaseModel`'s metaclass reads `__annotations__` and each `Field(...)` and
compiles them into a *core schema*, a tree describing every field's type and constraints. In
Pydantic v2 that schema is executed by `pydantic-core`, a Rust extension, which is why validation is
fast enough to run on every request. The compile happens once per class, *because* building the
validator is far more expensive than running it, and models are defined once and used millions of
times.

`Model.model_validate(obj)` validates a Python object (dict, attributes), `model_validate_json(s)`
parses JSON text directly in Rust without an intermediate `json.loads`, and constructing
`Model(**kwargs)` validates keyword arguments. On failure a `ValidationError` carries a list of
errors, each with `loc` (the path to the bad field), `msg` and a machine-readable `type`. That
structure, not a single string, is the design choice that makes "send the errors back to the model
and retry" practical.

### Lax versus strict
By default Pydantic validates in *lax* mode: `"1200"` becomes `1200` for an `int` field, `"true"`
becomes `True`. This is deliberate, because the inputs it was built for (query strings, env vars,
model output) arrive as text. `ConfigDict(strict=True)` or `Field(strict=True)` turns coercion off
when exact types matter, such as distinguishing `1` from `"1"` in an ID field. V2 tightened some
rules relative to v1; for example an `int` is no longer accepted for a `str` field by default.
Unknown fields are *ignored* by default; `ConfigDict(extra="forbid")` makes them errors, which is
what you want for tool arguments, where a hallucinated field is a signal, not noise.

### Validators, serialization, schema
`@field_validator("name")` runs custom logic on one field (before or after type coercion);
`@model_validator(mode="after")` checks cross-field invariants such as `start < end`.
`@computed_field` exposes derived values in dumps. `model_dump()` produces a dict and
`model_dump_json()` a string, with `exclude`, `by_alias` and `mode="json"` to control the output.
`model_json_schema()` emits a JSON Schema for the class, including `enum` for `Literal` and `Enum`
fields, `minimum`/`maximum` from `ge`/`le`, and `description` from `Field(description=...)`, which
is text the model actually reads. `TypeAdapter(list[Ticket])` gives validation and schema for
types that are not models, including `TypedDict`s and plain containers. Discriminated unions
(`Field(discriminator="type")`) route a payload to the right variant by a tag field, which is how
you validate "one of these N tool calls".

### Against the alternatives
Stdlib `dataclasses` are lighter and have no validation; use them for internal records you
construct yourself. `TypedDict` is static-only and keeps data as dicts; use it for wire shapes that
must stay dicts, and validate them with a `TypeAdapter` if needed. `attrs` offers validators but no
schema generation. `msgspec` is faster with a smaller feature set. Pydantic's edge is the single
definition serving checker, validator, serializer and schema, which is why the AI ecosystem
standardized on it. Settings management lives in the separate `pydantic-settings` package.

### Sharp edges
Validation has a cost: do it at boundaries, not inside hot loops where the data was already
validated. Lax coercion can mask bugs (a float `1.0` silently becoming `1`). `Optional` fields
render as `anyOf` with `null` in the schema, and some providers' strict structured-output modes
require every property to be listed as required with `additionalProperties: false`; the SDK helpers
rewrite the schema for you, a hand-built schema must do it itself. V1 and v2 APIs differ
(`validator` became `field_validator`, `.dict()` became `.model_dump()`, `parse_obj` became
`model_validate`, the inner `Config` class became `model_config = ConfigDict(...)`), so copying
old snippets produces deprecation warnings or errors.

## Example
Structured extraction with a schema generated from the model and a validation-error message fed
back for a repair attempt. `extra="forbid"` makes an invented field fail loudly.

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

class Ticket(BaseModel):
    model_config = ConfigDict(extra="forbid")          # hallucinated keys are errors
    title: str = Field(min_length=3, max_length=120, description="Short summary of the issue")
    severity: Literal["low", "medium", "high"]
    affected_users: int = Field(ge=0)
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def normalise(cls, v: list[str]) -> list[str]:
        return sorted({t.strip().lower() for t in v if t.strip()})

schema = Ticket.model_json_schema()      # send this as the tool/response schema
print(schema["properties"]["severity"]["enum"])          # ['low', 'medium', 'high']
print(schema["properties"]["affected_users"]["minimum"]) # 0

def parse_or_feedback(text: str) -> Ticket | str:
    try:
        return Ticket.model_validate_json(text)
    except ValidationError as e:
        issues = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        return f"Your JSON was rejected. Fix these fields and resend: {issues}"

raw = '{"title": "Login broken", "severity": "critical", "affected_users": "1200", "tags": ["Auth", "auth "]}'
print(parse_or_feedback(raw))
# Your JSON was rejected. Fix these fields and resend: severity: Input should be 'low', 'medium' or 'high'
t = parse_or_feedback(raw.replace("critical", "high"))
print(t)        # title='Login broken' severity='high' affected_users=1200 tags=['auth']
print(type(t.affected_users))   # <class 'int'>: "1200" was coerced in lax mode
print(t.model_dump_json())      # {"title":"Login broken","severity":"high","affected_users":1200,"tags":["auth"]}
```

The second call succeeds because the only error was the `severity` literal; the string `"1200"`
was coerced and the tags were normalized by the validator. The feedback string is what an
`instructor`-style retry loop sends back to the model.

## Trade-offs
- One class gives you static type, validator, serializer and schema, but it couples all four to
  Pydantic's interpretation; a major-version change (v1 to v2) touches every boundary at once.
- Lax coercion makes real-world text inputs just work, but it can hide type confusion upstream; flip
  to strict mode for identifiers, money and anything where `"1"` and `1` must differ.
- Rust-backed validation is fast per call, yet running it on every object in a tight loop still
  dominates when the data was already validated; validate once at the boundary and pass models.
- Rich validation and nested models make schemas expressive, but deeply nested or recursive
  schemas can confuse models and some providers' strict modes; flatten or simplify when the model's
  output quality drops.
- `extra="forbid"` catches hallucinated fields early, but on *inputs you do not control* (a vendor
  adding a field) it turns a harmless addition into an outage; use it for model output, not for
  third-party webhooks.

## Pitfalls
- Leaving `extra` at the default `ignore` for tool arguments, so a model's misspelled key vanishes
  silently; the symptom is a tool receiving a default value and behaving as if the argument was
  never supplied.
- Hand-editing a JSON Schema instead of generating it from the model; the symptom is validation
  rejecting responses that the schema allowed, or vice versa, after one side changed.
- Catching `ValidationError` and logging `str(e)` only; you lose the structured `loc`/`type` list
  that would let you retry the model with precise feedback or aggregate failure modes.
- Mixing v1 and v2 idioms (`@validator`, `.dict()`, class `Config`) in a v2 project; the symptom is
  a wall of deprecation warnings, then breakage when the compatibility shims are removed.
- Using a mutable default like `tags: list[str] = []`; Pydantic copies defaults per instance so this
  does not share state, but readers assume it does, and `default_factory` states the intent.
- Validating inside a per-token streaming loop; the symptom is CPU-bound latency that disappears
  when you accumulate chunks and validate once at the end.

## When to use
Reach for Pydantic at every trust boundary: HTTP requests and responses, LLM structured output,
tool-call arguments, configuration, and any file or message produced by another system. Define the
model, generate the schema from it, validate the input with it, and pass the resulting object
inward. Do not reach for it for small internal records you construct yourself in a hot path; a
`dataclass` is lighter there. Measure the error distribution from `ValidationError.errors()` over
time: the `type` field tells you whether the model keeps failing on the same constraint, which is
a prompt or schema problem, not a code problem. In day-to-day AI work Pydantic is the connective
tissue between prompts, SDKs and your code; in interviews, be ready to explain lax versus strict,
how the schema and the validator stay in sync, and what you do when the model's JSON fails.

## Role lens
- **Engineer:** Defines response and tool-argument models, generates schemas from them, and builds
  the validate-then-retry loop around model output with structured error feedback.
- **Architect:** Decides where validation happens (edge, service, agent core), which models are
  shared across services, and the strictness policy for inputs you control versus vendor inputs.
- **FDE:** Models a customer's messy export or API payload as Pydantic classes on day one, so
  every malformed record is reported with a field path instead of a stack trace.

## Check yourself
**Q:** A model is asked to return JSON matching a Pydantic class. About 5% of responses fail
validation. Walk through how you would diagnose and reduce that rate without just retrying blindly.
**A:** First log `ValidationError.errors()` as structured data and aggregate by `loc` and `type`.
If one field dominates, it is usually a schema or prompt problem: an ambiguous `description`, an
enum the model keeps paraphrasing, or a nested optional the provider's strict mode handles badly;
fix the schema or the field description. Check that the schema sent to the model is generated from
the same class that validates, not a stale copy. Consider `extra="forbid"` so hallucinated fields
surface. Then add a bounded repair loop that sends the exact error list back to the model, and
measure the residual failure rate; if it is still material, simplify the schema or split the
extraction into two smaller calls.

## Quiz
### Q: Why does Pydantic compile a model's annotations into a core schema once at class creation?
- [ ] To register the model with FastAPI automatically
- [x] Building the validator is expensive while running it is cheap, and models are defined once but validated many times
- [ ] Because annotations are only available at class creation time
- [ ] To make the model immutable
> Compilation happens once per class and the compiled validator (executed in Rust) runs on every
> call. Paying the build cost per validation would make request-time validation far too slow.

### Q: With default settings, what happens when an LLM returns `{"title": "x", "severty": "high"}` for a model whose field is `severity`?
- [ ] A `ValidationError` for the unknown field `severty`
- [x] `severty` is ignored and validation fails only because `severity` is missing
- [ ] `severty` is matched to `severity` by fuzzy matching
- [ ] The model instance is created with `severity=None`
> `extra` defaults to `ignore`, so the misspelled key is dropped. The required `severity` is then
> missing. Set `extra="forbid"` to make the hallucinated field itself an error.

### Q: Which is the strongest reason to generate the JSON Schema from the Pydantic model rather than writing it by hand?
- [ ] Generated schemas are smaller
- [ ] Hand-written schemas cannot express enums
- [x] The schema the model is given and the validator applied to its answer cannot drift apart
- [ ] Providers reject schemas that were not produced by Pydantic
> One definition serves both roles. A hand-maintained copy will eventually allow something the
> validator rejects or vice versa, producing failures that are hard to attribute.
