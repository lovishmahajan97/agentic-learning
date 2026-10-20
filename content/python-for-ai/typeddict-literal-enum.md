---
id: typeddict-literal-enum
module: python-for-ai
name: TypedDict, Literal and Enum
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
related:
  - python-for-ai/type-hints
  - python-for-ai/pydantic
  - python-for-ai/dataclasses
  - python-for-ai/json-jsonl
  - agents/tool-schemas-and-argument-validation
  - prompting/structured-outputs-json-mode
  - agents/state-machines
sources:
  - Lehtosalo, 2019 — PEP 589: TypedDict
  - Lee, Levkivskyi, Lehtosalo, 2019 — PEP 586: Literal Types
  - Warsaw, Bendersky, Furman, 2013 — PEP 435: Adding an Enum type to the Python standard library
  - Official docs: enum module
---

## TL;DR
`TypedDict` types a plain dict with fixed keys, for JSON-shaped data you do not want to wrap in
objects. `Literal` restricts a value to a small fixed set of constants at zero runtime cost. `Enum`
turns such a set into real runtime objects with identity, iteration and methods. AI code is full of
wire-format dicts and tiny closed vocabularies (roles, finish reasons, tool names), and the type
checker can only protect those if you name them; these three are how you do it.

## Why it exists
Provider responses, tool-call arguments and agent state arrive as dicts. Typed as
`dict[str, Any]`, the checker accepts `msg["contnt"]` and `msg["role"] = "User"`, and both bugs
surface as a 400 from the API or a silently skipped branch. Converting every dict into a class
fixes the checking but costs allocations, forces conversion in both directions, and is often the
wrong representation: the dict *is* what the SDK consumes, what gets stored in a graph state
object, and what gets logged. What is missing is a type for "a dict with exactly these keys and
these value types". That is `TypedDict`.

The second problem is small vocabularies. `role: str` admits `"User"`, `"assistant "` and
`"function"`; `finish_reason: str` admits anything. Without a way to say "one of these three
strings", the checker cannot flag a typo, cannot tell you a `match` statement is missing a case,
and cannot stop a new team member inventing a fourth role. `Literal` names the set with no runtime
object at all, which is the right cost when the strings only travel over the wire.

Sometimes the vocabulary needs behaviour: map each finish reason to an action, attach a retry
policy to each error class, refer to the set from twenty modules. A Literal's values are plain
strings at runtime, so there is nothing to hang a method on, and the mapping ends up in a separate
dict that can drift from the type. `Enum` makes each member a singleton object with a name, a value,
and whatever methods you add, at the cost of a conversion step at the JSON boundary.

## How it works
### TypedDict: a dict with a declared shape
A `TypedDict` is declared like a class but creates nothing new at runtime: `Message(role="user",
content="hi")` returns an ordinary `dict`, and `isinstance(x, Message)` raises `TypeError`. The
class exists only so the checker knows the keys and value types. This design was chosen *because*
the whole point is to type data you already hold as dicts without changing a single runtime
object; a wrapper class would defeat that. Consequences follow: there is no validation, so a dict
from `json.loads` (typed `Any`) must be validated by something (Pydantic's `TypeAdapter`
can validate a `TypedDict`); otherwise it is simply trusted, because the checker accepts `Any`
wherever a `TypedDict` is expected without a word of complaint.

Keys are all required by default. `total=False` makes all of them optional; `NotRequired[T]` and
`Required[T]` (3.11) mark individual keys, which matters for API payloads where `tool_calls` is
present only on some turns. Reading an optional key with `d.get("k")` yields `T | None`, so the
checker forces a narrowing branch. `ReadOnly[T]` (3.13) marks keys that must not be assigned.
TypedDicts are structural: one with a superset of keys is assignable where the subset is expected
(provided the shared keys have the same value types and required-ness), which lets a generic `Message` function accept richer provider-specific dicts. Keys that are not
valid identifiers (`"content-type"`) use the functional form `TypedDict("Headers", {...})`.

### Literal: a finite set, checked statically
`Literal["system", "user", "assistant"]` is a type whose only inhabitants are those exact strings
(ints, bools, bytes and enum members are also allowed). The checker narrows on comparison: inside
`if role == "user":` the type of `role` is `Literal["user"]`. The payoff is exhaustiveness: in a
`match` over a Literal (or an Enum), a final `case _: assert_never(x)` makes the checker report an
error if any member is unhandled, which is how you learn at check time, not in production, that a
new role was added and one handler forgot it. At runtime the *values* carry no trace: `role` is just
a `str`, so nothing stops `"robot"` arriving from JSON unless something validates it. The alias
itself does survive as an object, and `typing.get_args(Role)` returns `("system", "user",
"assistant")`; that is how Pydantic turns a Literal into a schema `enum`, and how you can iterate
the set when you must, though the checker sees that tuple only as `tuple[Any, ...]`. That is why
Literal is the right tool for values that are only compared and serialized.

### Enum: a finite set of runtime objects
`class Reason(Enum): STOP = "stop"` creates singleton members. Each has `.name` (`"STOP"`) and
`.value` (`"stop"`); `Reason("stop")` looks up by value, `Reason["STOP"]` by name, and `for r in
Reason` iterates members in definition order. Identity comparison (`r is Reason.STOP`) works because
there is exactly one object per member. A member whose value duplicates an earlier one becomes an
*alias* rather than a new member, so iteration skips it, which is a documented feature that
surprises people. `auto()` assigns values when you do not care what they are.

Plain `Enum` members are not strings: `Reason.STOP == "stop"` is `False` and `json.dumps` of a
member raises `TypeError`. `StrEnum` (3.11) and `IntEnum` mix in the primitive type so members
compare equal to and serialize as their value, which is what you want for any enum that crosses a
JSON boundary. Because members are objects, you can attach methods and properties (`reason.action`)
and the behaviour lives next to the vocabulary rather than in scattered `if` chains.

### Choosing
TypedDict for dict-shaped data whose runtime form must stay a dict (SDK payloads, graph state,
tool arguments); switch to a Pydantic model when you need validation or the shape is consumed in
many places. Literal for small string sets that are only compared and serialized. Enum when you
need iteration, identity, methods, or a value referenced from many modules; prefer `StrEnum` when
it meets JSON. Pydantic accepts all three and renders both Literal and Enum as JSON Schema `enum`
constraints (an Enum as a named entry under `$defs` referenced by `$ref`, a multi-value Literal
inline, a single-value Literal as `const`), so the choice barely changes what the model is shown.

## Example
A wire-format chat message with an optional key, a `StrEnum` carrying behaviour, and an
exhaustive `match` the checker will police. Requires Python 3.11+ for `NotRequired`, `StrEnum`
and `assert_never`.

```python
import json
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict, assert_never

Role = Literal["system", "user", "assistant", "tool"]

class ToolCall(TypedDict):
    id: str
    name: str
    arguments: str                 # JSON text, exactly as the API sends it

class ChatMessage(TypedDict):
    role: Role
    content: str | None
    tool_calls: NotRequired[list[ToolCall]]

class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"

    @property
    def truncated(self) -> bool:
        return self is FinishReason.LENGTH

def next_action(reason: FinishReason) -> Literal["return", "continue", "call_tools", "refuse"]:
    match reason:
        case FinishReason.STOP: return "return"
        case FinishReason.LENGTH: return "continue"
        case FinishReason.TOOL_CALLS: return "call_tools"
        case FinishReason.CONTENT_FILTER: return "refuse"
        case _: assert_never(reason)   # add a member above without a case: checker error here

msg: ChatMessage = {"role": "assistant", "content": None,
                    "tool_calls": [{"id": "c1", "name": "search", "arguments": '{"q": "kv cache"}'}]}
print(type(msg))                                   # <class 'dict'>: no wrapper object exists
reason = FinishReason("tool_calls")                # lookup by wire value
print(reason.truncated, next_action(reason))       # False call_tools
print(json.dumps({"finish": reason}))              # {"finish": "tool_calls"}: StrEnum is a str
# msg["role"] = "robot"   -> checker error: "robot" is not a Role
```

Remove the `CONTENT_FILTER` case and mypy or pyright reports that `assert_never` received
`Literal[FinishReason.CONTENT_FILTER]` (the member left over after narrowing), not `Never`. That is the exhaustiveness guarantee in action.

## Trade-offs
- TypedDict costs nothing at runtime and keeps data SDK-ready, but gives no validation or methods;
  once the same shape is consumed in several modules, a Pydantic model's validation pays for itself.
- Literal gives exhaustiveness checks for free, but its values are bare strings with no behaviour,
  and iterating means `get_args` on the alias; once you want methods, per-member data or the set
  referenced from many modules, switch to an Enum.
- Enum members have identity and can carry methods, but a plain Enum needs `.value` at every JSON
  boundary and compares unequal to raw strings; `StrEnum` removes that cost for string vocabularies.
- `NotRequired` keys model optional payload fields precisely, but every `.get()` returns `T | None`
  and forces narrowing; when the field is always present but nullable, a required `T | None` key is
  simpler.

## Pitfalls
- Calling `isinstance(x, SomeTypedDict)` to validate input; it raises `TypeError` at runtime because
  TypedDict is not a real class, so validate with Pydantic or check keys explicitly.
- Comparing a plain `Enum` member to the raw wire string (`reason == "stop"`); it is always `False`,
  and the symptom is a branch that never executes. Use `StrEnum` or compare `.value`.
- Passing a plain Enum member into `json.dumps`; it fails with "Object of type ... is not JSON
  serializable", typically deep inside a logging or request-serialization call.
- Assigning `json.loads(...)` output straight to a `TypedDict`-typed variable; because the result is
  `Any`, the checker accepts it silently and trusts a shape nobody verified, and the `KeyError`
  appears later. Validate with `TypeAdapter(ChatMessage).validate_python(...)` at the boundary.
- Using the pre-3.11 idiom `class Reason(str, Enum)` and interpolating members into prompts or log
  lines; on 3.11+ `f"{Reason.STOP}"` renders `Reason.STOP`, not `stop`, so a prompt says "finish
  reason Reason.STOP". `StrEnum` formats as its value; or interpolate `.value` explicitly.
- Passing a `TypedDict` to a helper typed `dict[str, Any]`; the checker rejects it, because that
  helper could insert arbitrary keys. Type read-only helpers as `Mapping[str, object]`.
- Giving two Enum members the same value; the second silently becomes an alias and disappears from
  iteration, which looks like a "missing" member in a dropdown or a prompt listing valid options.
- Adding a new Literal or Enum member without an `assert_never` guard in handlers; nothing warns, and
  the new value falls through to a default branch at runtime.

## When to use
Use `TypedDict` for the messages, tool-call records and state dicts that flow into and out of
provider SDKs and agent frameworks, where the dict is the runtime truth and validation already
happened at the boundary. Use `Literal` for roles, modes and finish reasons that are compared and
serialized but never iterated, and pair every `match` over them with `assert_never`. Use `Enum`
(usually `StrEnum`) when a vocabulary needs behaviour, iteration or a single definition shared
across modules, and when the same set appears in prompts, schemas and code. Measure: count the
`str` parameters in your codebase that really mean "one of N values"; each one is a latent typo.
In interviews, the expected depth is why TypedDict has no runtime, why Literal gives exhaustiveness,
and what `StrEnum` changes about equality and JSON.

## Role lens
- **Engineer:** Types the chat-message and tool-call dicts as TypedDicts and the role and finish
  reason fields as Literals, so a typo fails in CI instead of as an API 400.
- **Architect:** Decides which vocabularies become shared Enums in a common package, so every
  service agrees on the same finish-reason and error-class names and schemas.
- **FDE:** Maps a customer's status codes and category strings onto Enums with behaviour, turning
  their undocumented string conventions into one checked, iterable definition.

## Check yourself
**Q:** An agent's `finish_reason` field is a plain `str`. You want the checker to catch unhandled
values when a provider adds a new one, you need to serialize it to JSON in logs, and one module
needs to map each value to a retry policy. Which of TypedDict, Literal or Enum do you pick and why?
**A:** A `StrEnum`. Literal would give exhaustiveness checking, but its members are bare strings,
so the retry-policy mapping would live in a separate dict keyed by those strings that can drift
from the type (a checker will not tell you the dict lacks a new key). A plain
Enum carries the mapping as a method or property but fails `json.dumps` and compares unequal to the
wire string, costing a conversion at every boundary. `StrEnum` members are strings, so they
serialize and compare transparently, you can still `match` over them with `assert_never` for
exhaustiveness, and the policy lives on the member. TypedDict is irrelevant here: it types a
dict's shape, not a single value's vocabulary.

## Quiz
### Q: What does `Message(role="user", content="hi")` return at runtime if `Message` is a TypedDict?
- [ ] An instance of `Message` with attribute access
- [x] A plain `dict`; the TypedDict class exists only for the type checker
- [ ] A frozen mapping that rejects new keys
- [ ] A `types.SimpleNamespace`
> TypedDict adds no runtime type. The checker uses the declaration to verify keys and values, but
> the object you hold is an ordinary dict, which is why `isinstance` against it fails.

### Q: Why does `match` with a final `case _: assert_never(x)` help when `x` is a Literal or Enum?
- [ ] It raises at runtime for any unexpected value, replacing validation
- [ ] It makes the match statement faster by skipping unreachable cases
- [x] The checker reports an error if any member of the set is not handled by an earlier case
- [ ] It converts the Literal into an Enum automatically
> After every member is matched, the checker narrows `x` to `Never`; if one is missing, `x` still has
> a type and `assert_never` is a static error. It documents and enforces exhaustiveness.

### Q: Which statement about a plain `Enum` member such as `Reason.STOP = "stop"` is correct?
- [ ] `Reason.STOP == "stop"` is `True` because the value is a string
- [ ] `json.dumps(Reason.STOP)` produces `"stop"`
- [x] `Reason("stop")` returns the member, and two members with the same value are aliases of one
- [ ] Members can be created at runtime by assignment, such as `Reason.NEW = "new"`
> Lookup by value is supported and duplicate values create aliases. Equality with the raw string and
> direct JSON serialization only hold for `StrEnum`, which mixes in `str`.
