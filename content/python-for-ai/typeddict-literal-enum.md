---
id: typeddict-literal-enum
module: python-for-ai
name: TypedDict, Literal and Enum
kind: concept
difficulty: core
est_minutes: 15
related:
  - python-for-ai/type-hints
  - python-for-ai/pydantic
  - python-for-ai/dataclasses
  - python-for-ai/json-jsonl
  - python-for-ai/mypy
  - prompting/structured-outputs-json-mode
  - agents/tool-schemas-and-argument-validation
sources:
  - Lehtosalo, 2019 — PEP 589: TypedDict
  - Lee, Levkivskyi, Lehtosalo, 2019 — PEP 586: Literal Types
  - Warsaw, Bendersky, Furman, 2013 — PEP 435: Adding an Enum type to the Python standard library
  - Official docs: typing and enum modules
---

## TL;DR
Three tools for "small, fixed vocabularies" that LLM code is full of. `TypedDict` gives a plain dict a
checked set of keys and value types while it stays a dict, which is what JSON and SDK parameters want.
`Literal["user", "assistant"]` restricts a value to specific constants so the checker catches typos and
missing branches. `Enum` turns constants into real objects with identity, iteration, methods and a
runtime lookup that rejects unknown values. Pick by whether you need a dict, a static constraint, or
a runtime one.

## Why it exists
Every LLM API speaks JSON: a message is `{"role": "user", "content": "..."}`, a tool call is a dict
of arguments, a response format is one of a few strings. Typing all of that as `dict[str, Any]` and
`str` is honest but useless: the checker cannot tell you that `{"roel": "user"}` is wrong, that
`role="User"` will be rejected by the provider, or that your `match` on the role forgot the new
`"tool"` case added last sprint. The failures surface as HTTP 400s from the provider or, worse, as a
silently ignored message.

The opposite reflex, "make everything a class", fights the boundary. Provider SDKs accept dicts for
request parameters; JSON files, queue payloads and logs are dicts; converting back and forth at every
edge adds code and copies without adding safety where it is actually missing, which is inside your
own functions that pass those dicts around.

`TypedDict` exists so a dict can carry a static contract without ceasing to be a dict. `Literal`
exists because many parameters are strings from a closed set and the set is the whole contract; the
checker can enforce membership and exhaustiveness for free. `Enum` exists for the cases where you
need the constants at *runtime*: validating user or model input, iterating all options, attaching
behaviour, or guaranteeing that two modules mean the same thing by `is`. Each covers a gap the other
two leave open, which is why all three are in the standard library.

## How it works
### TypedDict: a dict with a declared shape
`class Message(TypedDict): role: str; content: str` declares that a `Message` is a dict with exactly
those keys. At runtime it is a normal `dict`; the class is only metadata, and `isinstance(x, Message)`
raises `TypeError` because there is nothing to check against. The static checker verifies three
things: literal constructions `{"role": "user", "content": "hi"}` have the required keys and no
unknown ones, `m["content"]` is a `str`, and `m["contnet"]` is an error. Keys are required by default;
`total=False` makes them all optional, and `NotRequired[str]` / `Required[str]` (Python 3.11, PEP 655)
mark individual keys. The functional form `TypedDict("Params", {"max-tokens": int})` handles keys that
are not valid identifiers. TypedDicts can inherit from each other to compose shapes.

Two consequences follow from "it is just a dict". There is no validation: a dict that arrived from
`json.loads` is whatever it is, and the checker will happily let you annotate it as a `Message`. For
untrusted input, parse with Pydantic first. And a `TypedDict` is compatible with `Mapping[str, object]`
but not with `dict[str, Any]`, *because* a function taking `dict[str, Any]` could insert arbitrary
keys and break the contract; this surprises people who pass a TypedDict to a helper typed with
`dict`. Provider SDKs lean on this design: request parameters are typically typed as TypedDicts (so
you pass plain dicts) while responses are validated model objects.

### Literal: the value is the type
`Role = Literal["system", "user", "assistant"]` means a `Role` is one of those three strings, and the
checker tracks the exact value. Comparisons narrow: inside `if role == "user":` the checker knows the
value. Combined with `match` and `typing.assert_never` (3.11), you get exhaustiveness: if a new
literal is added and a `match` does not handle it, the `case _: assert_never(role)` line fails type
checking. Literals work for `str`, `int`, `bool`, `bytes` and enum members. They cost nothing at
runtime, which also means nothing at runtime checks them; `typing.get_args(Role)` returns the tuple of
allowed values if you need a quick membership test on external input.

### Enum: constants that exist at runtime
`class Role(Enum): USER = "user"` creates one singleton member per name. Members have `.name` and
`.value`, iterate in definition order, compare by identity, and support methods. `Role("user")` looks
a member up by value and raises `ValueError` for anything else, which is the runtime validation that
`Literal` lacks. A plain `Enum` member is *not* a string: `Role.USER == "user"` is `False` and
`json.dumps({"role": Role.USER})` raises `TypeError`. `StrEnum` (3.11) and `IntEnum` subclass `str`
and `int`, so members compare equal to and serialise as their values, which is almost always what you
want at a JSON boundary. `auto()` gives the lowercased member name in a `StrEnum` and increasing
integers in a plain `Enum`.

### Choosing
Keep a value a `TypedDict` when it must stay a dict (SDK parameters, JSON payloads) and has no
behaviour. Use `Literal` when the set is small, string-shaped, and static checking is enough. Use an
`Enum` when you need runtime validation, iteration, methods, or when the same constant is passed
between modules and identity matters. In structured-output schemas all three map cleanly: Pydantic
renders both `Literal` and `Enum` fields as JSON Schema `enum` constraints, so the model is told the
allowed values either way.

## Example
A message type that stays a dict, a role literal checked for exhaustiveness, and a `StrEnum` that
validates at runtime and serialises as a plain string. Needs Python 3.11+.

```python
import json
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict, assert_never, get_args

Role = Literal["system", "user", "assistant"]

class Message(TypedDict):
    role: Role
    content: str
    name: NotRequired[str]            # optional key; the object is still a plain dict

class Format(StrEnum):
    TEXT = "text"
    JSON = "json_object"

def render(m: Message) -> str:
    match m["role"]:
        case "system":    return f"[sys] {m['content']}"
        case "user":      return f"> {m['content']}"
        case "assistant": return m["content"]
        case other:       assert_never(other)   # mypy error here if Role grows a value

msgs: list[Message] = [{"role": "user", "content": "hi"}]
# msgs.append({"role": "User", "content": "x"})   # checker: "User" is not a Role
# msgs.append({"roel": "user", "content": "x"})   # checker: extra key "roel"

fmt = Format("json_object")                       # runtime lookup by value
try:
    Format("yaml")
except ValueError as e:
    print("rejected:", e)                         # 'yaml' is not a valid Format

print(render(msgs[0]), isinstance(msgs[0], dict))        # > hi True
print(json.dumps({"format": fmt}), fmt == "json_object")  # {"format": "json_object"} True
print("tool" in get_args(Role), [f.value for f in Format])  # False ['text', 'json_object']
```

## Trade-offs
- `TypedDict` keeps zero-copy interoperability with JSON and SDKs, but gives no runtime guarantee;
  once data crosses a trust boundary, Pydantic validation is worth its cost.
- `Literal` costs nothing and catches typos statically, yet it cannot validate values that arrive as
  `str` from a request; if inputs are external, an `Enum` or explicit `get_args` check is required.
- A plain `Enum` gives identity and prevents accidental string comparisons, at the price of explicit
  `.value` at every JSON edge; `StrEnum` removes that friction but reintroduces string equality.
- Exhaustive `match` with `assert_never` turns "added a role" into a compile-time task list across
  the codebase, which is a benefit when the set is yours and a maintenance burden when it is a
  provider's set that changes under you.

## Pitfalls
- Serialising a plain `Enum` member with `json.dumps` and hitting `TypeError: Object of type Role is
  not JSON serializable`; switch to `StrEnum`, or dump `.value`.
- Comparing `Role.USER == "user"` with a plain `Enum` and silently getting `False` in a branch that
  then never executes; the symptom is a message type that is "never seen" in logs.
- Treating a `TypedDict` annotation as validation and getting `KeyError: 'content'` deep in a pipeline
  from a malformed upstream dict; parse untrusted input with Pydantic at the boundary.
- Passing a `TypedDict` to a helper typed `dict[str, Any]` and getting an incompatible-type error;
  the helper should accept `Mapping[str, object]` or the specific TypedDict.
- Using `auto()` in a plain `Enum` and discovering integers `1, 2, 3` in serialised payloads; use
  explicit string values or `StrEnum`, where `auto()` yields the member name.
- Forgetting `NotRequired` for optional keys, so every literal construction of the dict errors with a
  missing key, and reaching for `total=False`, which makes *all* keys optional and loses the contract.

## When to use
Default to `TypedDict` for the shapes you exchange with SDKs and JSON files, and for lightweight
internal records that have no behaviour; add `Literal` to any string field with a closed set of
values, and make the branches that switch on it exhaustive. Promote to an `Enum` when the set needs
to be validated at runtime, iterated (a settings UI, a CLI `--choices`), or carries behaviour such as
"which models support this format". Promote to a dataclass or Pydantic model when the record needs
methods or validation. The measure of success is how many shape errors move from the provider's 400
responses into mypy's output. In interviews, the question is usually "when would you not use an Enum",
and the answer is "when the value must stay a string the SDK accepts and static checking suffices".

## Role lens
- **Engineer:** Types provider message dicts as `TypedDict`s, roles and formats as `Literal`s, and
  switches on them with exhaustive `match` blocks that fail CI when a case is missing.
- **Architect:** Decides which vocabularies are owned enums (validated, versioned, iterable) and which
  are passthrough literals mirroring a provider's API that may change independently.
- **FDE:** Maps a customer's status codes and category strings into `StrEnum`s so integration payloads
  validate on arrival and serialise back unchanged.

## Check yourself
**Q:** A pipeline receives JSON events with a `kind` field that your code switches on in six places.
Product adds a seventh kind next month. How do you type `kind` so the change is safe, and what do you
do at the ingestion boundary?
**A:** Define `Kind` once, as a `StrEnum` if events are validated at ingestion or as a `Literal` if a
Pydantic model already validates them, and switch on it with `match` plus `assert_never` in the
default case so every one of the six sites fails type checking when the seventh value is added. At
the boundary, parse the raw JSON into a Pydantic model whose `kind` field is that `Enum` or `Literal`,
so an unknown value becomes a `ValidationError` with a clear location rather than a silent fall
through. Keep the event itself a `TypedDict` or model, not a bare `dict[str, Any]`, so downstream
helpers get checked key access. The outcome is that the new kind is a CI-visible checklist instead of
a production surprise.

## Quiz
### Q: Which of the three rejects an unknown value at runtime without extra code?
- [ ] `TypedDict`, because the class checks keys on construction
- [ ] `Literal`, because the interpreter compares against the allowed constants
- [x] `Enum`, because `Role("tool")` raises `ValueError` when no member has that value
- [ ] All three, since they are all enforced by the typing module
> `TypedDict` and `Literal` are pure annotations the interpreter ignores. Only `Enum` members exist
> at runtime, so lookup by value is a real check that fails loudly on unknown input.

### Q: Why does `json.dumps({"role": Role.USER})` fail for a plain `Enum` but work for a `StrEnum`?
- [ ] `json` only serialises enums whose values are integers
- [x] A plain `Enum` member is not a `str`, while a `StrEnum` member subclasses `str` and serialises as its value
- [ ] `StrEnum` registers a JSON encoder automatically on import
- [ ] Plain `Enum` members are lazily created and not yet available to the encoder
> `json` serialises `str` subclasses as strings. A plain `Enum` member is an opaque object, so the
> encoder raises `TypeError`; `StrEnum` members are strings, which is why they are the usual choice at
> JSON boundaries.

### Q: When is a `TypedDict` the better choice over a dataclass for a record?
- [x] When the value must remain a plain dict for JSON or SDK parameters and needs no methods
- [ ] When the record needs runtime validation of its field types
- [ ] When instances must be hashable so they can be dict keys
- [ ] When fields need default values computed in `__post_init__`
> A `TypedDict` adds a static contract to a dict without changing its runtime type, which is exactly
> what passthrough payloads want. Validation, hashing and post-init logic are reasons to use a
> dataclass or Pydantic model instead.
