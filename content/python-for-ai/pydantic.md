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
  - Official docs: Pydantic (v2)
  - Official docs: pydantic-core
---

## TL;DR
Pydantic turns a class with type hints into a validator, parser, serialiser and JSON Schema generator.
It exists because the data entering AI systems (HTTP bodies, LLM output, config, queue messages) is
untrusted and type hints alone check nothing at runtime. One `BaseModel` gives you the schema you send
to the model or the API docs, the parser that rejects bad output with a precise error list, and the
object your code can trust afterwards. Validation is compiled once per class into Rust
(`pydantic-core`), so the per-instance cost is small but not zero.

## Why it exists
The boundary problem is simple to state: a function annotated `def handle(order: Order)` is only safe
if something made sure the thing passed in really is an `Order`. Type hints are read by the checker
and ignored by the interpreter, so `json.loads` output annotated as `Order` is a lie the moment the
upstream changes a field name. In AI systems the upstream is often a language model, which adds
hallucinated keys, writes `"2"` where an integer was asked for, and omits required fields a few
percent of the time. "A few percent" across a million calls is tens of thousands of silent corruptions
if nothing checks.

The naive alternatives each fail in a specific way. Hand-written `if "qty" not in d or not isinstance(
d["qty"], int)` chains grow with every field, report only the first problem, and drift from the type
hints they duplicate. `dataclasses` give structure but accept `Order(qty="two")` without complaint.
`jsonschema` validation gives errors but no typed object afterwards, so you validate and then still
index into dicts. And writing a JSON Schema by hand for the model's structured output, separately from
the Python class that will parse the response, guarantees the two drift apart.

Pydantic's premise is "parse, don't validate": a single declaration is the schema, the parser and the
type. Errors are collected, not short-circuited, and come back as a structured list with the path to
each failure, which is exactly what you need to send back to a model for a retry or to a client as a
422. That closed loop, schema out and validated object in, is why nearly every LLM SDK and framework
in Python is built on it.

## How it works
### Compile once, validate many
When the class body of a `BaseModel` subclass executes, the metaclass reads the annotations, `Field()`
metadata and validators, and builds a *core schema*: a tree describing every field's type, constraints
and defaults. `pydantic-core` compiles that tree into a validator and a serialiser implemented in
Rust. This happens once per class, at import time, *because* validation is the hot path and
interpreting the schema on every call would be too slow. Instances are then created by walking the
compiled validator over the input.

### Validation and modes
`Order(**data)` and `Order.model_validate(data)` validate Python objects; `Order.model_validate_json
(raw)` parses JSON text directly inside `pydantic-core`, skipping the intermediate `json.loads`
dicts, which is the fast path for LLM and HTTP responses. By default Pydantic runs in *lax* mode:
`"2"` becomes `2` for an `int` field, `"true"` becomes `True`, an ISO string becomes a `datetime`.
`ConfigDict(strict=True)` or `Field(strict=True)` turns that off so a type mismatch is an error.
Lax is the right default at a JSON boundary where everything is a string; strict is right when the
input is already typed and coercion would mask a bug.

Every failure is appended to a list rather than raised immediately, and the whole list is raised as
one `ValidationError`. `e.errors()` yields dicts with `loc` (the path, e.g. `("items", 0, "qty")`),
`type` (a stable code such as `int_parsing` or `missing`), `msg` and the offending `input`. Stable
codes let you count failure modes across thousands of LLM calls and spot systematic prompt problems.

### Fields, constraints and validators
`Field()` carries constraints (`ge`, `min_length`, `pattern`), defaults, aliases and a `description`;
the description goes into the JSON Schema, which is how it reaches the model. `Annotated[int,
Field(ge=0)]` is the same thing attached to the type. `@field_validator("name")` runs custom logic
after (default) or before (`mode="before"`) the type validation of one field; `@model_validator
(mode="after")` sees the whole object for cross-field rules. Validators raise `ValueError` and
Pydantic wraps it into the same error list. Unlike dataclasses, a mutable default such as `[]` is
safe because Pydantic copies defaults per instance.

### Serialisation and schema
`model_dump()` returns Python objects, `model_dump(mode="json")` returns JSON-compatible ones
(datetimes become strings), and `model_dump_json()` returns text; `exclude_none`, `by_alias` and
`include` shape the output. `model_json_schema()` emits JSON Schema that FastAPI publishes as OpenAPI
and that you pass as a tool's `parameters` or a structured-output format. Field descriptions,
`Literal`s and `Enum`s become `description` and `enum` entries, so the prompt and the parser come from
one place.

### Configuration that matters for LLM work
`extra="forbid"` makes unknown keys an error, which is how you detect hallucinated or misspelled
fields that the default `extra="ignore"` would silently drop. `frozen=True` makes instances immutable
and hashable. `validate_assignment=True` re-validates on attribute set; it is off by default because
of cost. Discriminated unions (`Field(discriminator="kind")`) select the right model for polymorphic
payloads like tool calls by a tag field instead of trying every variant. `TypeAdapter(list[Item])`
validates and serialises types that are not models, such as a top-level JSON array.

## Example
An extraction schema for an LLM, the schema sent with the request, and the error list that a bad
response produces. The nested `"qty": "2"` is coerced in lax mode; the wrong currency and the
hallucinated `note` key are reported together with their paths.

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")        # hallucinated keys are errors, not silence
    sku: str = Field(min_length=3, description="Catalogue SKU, e.g. 'AB-123'")
    qty: int = Field(ge=1, description="Units ordered")
    unit_price: float = Field(ge=0)

class Order(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    customer: str
    currency: Literal["USD", "EUR"]
    items: list[LineItem] = Field(min_length=1)

    @field_validator("customer")
    @classmethod
    def strip_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("customer must not be blank")
        return v.strip()

    @property
    def total(self) -> float:
        return sum(i.qty * i.unit_price for i in self.items)

schema = Order.model_json_schema()                   # goes into the tool / response_format request
print(sorted(schema["properties"]), schema["required"])

raw = ('{"customer": " ACME ", "currency": "GBP", '
       '"items": [{"sku": "AB-123", "qty": "2", "unit_price": 9.5, "note": "rush"}]}')
try:
    Order.model_validate_json(raw)
except ValidationError as e:
    for err in e.errors():
        print(err["loc"], err["type"])               # ('currency',) literal_error
                                                     # ('items', 0, 'note') extra_forbidden
good = Order.model_validate_json(raw.replace("GBP", "USD").replace(', "note": "rush"', ""))
print(good.customer, good.items[0].qty, good.total)  # ACME 2 19.0   ("2" was coerced to 2)
```

## Trade-offs
- Validation costs CPU per object; it is negligible for request-sized payloads but wrong for hot
  numeric loops, where arrays should stay NumPy or tensors and be validated at the edge only.
- Lax coercion makes JSON boundaries forgiving, yet it can hide a model writing strings for numbers;
  switch to strict mode when you want to *measure* the model's format compliance rather than tolerate it.
- `extra="forbid"` catches hallucinated fields in LLM output, but on third-party API responses it turns
  every benign new field upstream adds into an outage; use `ignore` for data you do not control.
- Generating the schema from the model keeps parser and prompt in sync, but the emitted JSON Schema
  (nested `$defs`, `anyOf` for optionals) is not accepted verbatim by every provider's strict
  structured-output mode, so some flattening may be needed per provider.

## Pitfalls
- Mixing v1 and v2 APIs (`.dict()`, `@validator`, `class Config`) after an upgrade; the symptom is a
  wall of deprecation warnings and, for `parse_obj`-style calls, outright `AttributeError`s.
- Annotating `Optional[int]` and expecting it to be optional; without `= None` the field is required,
  and the error list shows `type: missing` for it.
- Assuming assignment is validated: `order.qty = -5` succeeds silently unless `validate_assignment=
  True`, so an object that passed on entry can be invalid when it leaves.
- Naming a field `model_name` or similar; the `model_` prefix is a protected namespace in v2 and
  emits a warning until you rename or override `protected_namespaces`.
- Calling `json.dumps(obj.model_dump())` with datetimes or enums inside and hitting `TypeError`; use
  `model_dump(mode="json")` or `model_dump_json()`.
- Building a response model from `model_validate` on a dict that was already the model's own
  `model_dump()` output and losing information through aliases; set `by_alias` consistently on both sides.

## When to use
Use Pydantic at every trust boundary: request and response bodies, LLM structured output and tool
arguments, configuration, messages pulled from a queue, rows loaded from a file. Inside the boundary,
plain dataclasses or TypedDicts are lighter and sufficient. Measure validation time only when payloads
are large or per-request object counts are high; otherwise the cost is noise next to a network call.
Day to day it shows up as the model you hand to the OpenAI or Anthropic SDK for structured output, the
FastAPI body type, and the thing whose `ValidationError` you feed back into a retry prompt. In
interviews, expect "how would you make LLM output reliable", and the strong answer is a Pydantic model
with constrained fields, `extra="forbid"`, and an error-driven retry loop with a bounded attempt count.

## Role lens
- **Engineer:** Declares the output model, passes `model_json_schema()` to the provider, parses with
  `model_validate_json`, and turns `e.errors()` into a retry message with a cap on attempts.
- **Architect:** Decides where the trust boundaries are, which models are the canonical contracts
  shared across services, and how strict versus lax mode and `extra` policies differ per boundary.
- **FDE:** Encodes a customer's domain rules as field constraints and validators so integration
  failures surface as readable error paths during a pilot instead of silent data corruption.

## Check yourself
**Q:** Three percent of your extraction calls fail to parse. Walk through how you would use Pydantic
to drive a retry loop, and which configuration choices change what you learn from the failures.
**A:** Parse with `Model.model_validate_json(text)` inside `try/except ValidationError`, then format
`e.errors()` (each `loc`, `msg` and `input`) into a short correction message and re-prompt with the
original schema, capped at two or three attempts before escalating. Keep `Field(description=...)` on
every field so the schema itself teaches the model. Set `extra="forbid"` to catch hallucinated or
misspelled keys that `ignore` would hide. Choose lax mode if you want tolerance for `"2"` instead of
`2`, or strict mode if you want to count those as failures and fix the prompt. Aggregate `type` codes
per field across calls: a cluster of `literal_error` on one field means the prompt's allowed values
are unclear, which is a prompt fix, not a retry fix. Libraries such as Instructor package this loop.

## Quiz
### Q: What does `Model.model_validate_json(text)` do that `Model(**json.loads(text))` does not?
- [ ] It skips validation for fields with defaults to save time
- [x] It parses and validates in one pass inside pydantic-core without first building Python dicts
- [ ] It accepts JSON with trailing commas and comments
- [ ] It runs validators in strict mode automatically
> Parsing JSON directly in the Rust core avoids materialising intermediate Python objects and is the
> intended fast path for API and LLM responses. Mode and leniency are governed by configuration either way.

### Q: In default (lax) mode, what happens when an `int` field receives the JSON string `"2"`?
- [ ] A `ValidationError` with type `int_type` is raised
- [x] The value is coerced to the integer `2`
- [ ] The field is set to `None` and a warning is logged
- [ ] The string is kept and the field's type becomes `str`
> Lax mode coerces numeric strings to integers because JSON boundaries routinely stringify numbers.
> Strict mode would reject it; a non-numeric string such as `"two"` fails in both with `int_parsing`.

### Q: Why set `extra="forbid"` on models that parse LLM output specifically?
- [ ] It makes parsing faster by skipping unknown keys
- [ ] It is required for `model_json_schema()` to emit `required` lists
- [x] It turns hallucinated or misspelled keys into errors instead of silently dropping them
- [ ] It forces the model to return every optional field
> The default `ignore` discards unknown keys, so a model writing `"quantity"` instead of `"qty"` would
> pass with the field missing or defaulted. `forbid` surfaces the discrepancy so the retry loop can fix it.
