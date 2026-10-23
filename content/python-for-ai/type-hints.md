---
id: type-hints
module: python-for-ai
name: Type hints
kind: concept
difficulty: intro
est_minutes: 14
status: typed
lesson_type: lab
agent_relevance: core
related:
  - python-for-ai/mypy
  - python-for-ai/protocols-generics-structural-typing
  - python-for-ai/typeddict-literal-enum
  - python-for-ai/pydantic
  - python-for-ai/dataclasses
  - agents/typed-tool-contracts
  - node-ts-for-ai/typescript-strict-mode-and-advanced-types
sources:
  - van Rossum, Lehtosalo, Langa, 2014 — PEP 484: Type Hints
  - Official docs: typing module
  - Official docs: mypy
---

## TL;DR
In an agent, type hints are the tool contract: the interpreter ignores them, but mypy checks your loop
before it runs, and Pydantic, FastAPI and agent frameworks turn them into the JSON Schema the model sees
and the validator its arguments must pass.

## For agent builders
- **Why it matters for agents.** An agent loop is glue between untyped JSON (model tool calls, tool
  results, SDK responses) and your code. Hints catch `None` content and wrong field names before a
  thirty-minute eval run, and they are the source the tool schema is generated from.
- **Where it shows up in your stack.** LangChain's `@tool` decorator and the OpenAI Agents SDK's
  `@function_tool` read a function's annotations and docstring to build the tool schema; LangGraph state
  is usually a `TypedDict` with `Annotated` reducers; Pydantic AI and `instructor` use them for outputs.
- **What you control.** Annotate every tool handler and the function that receives raw model JSON,
  choose `X | None` honestly where the API can omit a field, and run a checker in CI.
- **What you can skip.** Variance theory and the full PEP history. Know `Any` versus `object`,
  narrowing, `Annotated` and when annotations are evaluated; that covers agent code.

## Lab
### Task
Build the piece of a tool decorator that turns a handler's hints into the JSON Schema the model sees
and a checker for the arguments it sends back. This is what LangChain's `@tool` or a function-tool
decorator does for you.

Writing it once shows why resolved hints, `Literal` and `X | None` matter. Time box: 60-90 minutes.
Do "How it works" in this lesson first.
- **Build** `json_type`, `tool_schema` and `validate_args` in `lab.py` (the starter below).
- **Constraint:** the module uses `from __future__ import annotations`, so annotations are strings
  until you resolve them.
- **Run** with Python 3.10+ (checked on 3.12.14 with pytest 9.1.1), standard library only.

### Starter code
Save as `lab.py`. It runs as given and reports which parts are not implemented yet.

```python
"""lab.py: build a tool's JSON Schema and argument checker from its type hints."""
from __future__ import annotations      # every annotation below is stored as a STRING

import inspect
import json
from collections.abc import Callable
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

def search(query: Annotated[str, "What to search for"], limit: int = 5,
           source: Literal["docs", "tickets"] = "docs") -> list[str]:
    """Search internal docs or tickets."""
    return [f"{source}:{query}:{i}" for i in range(limit)]

def convert(amount: float, unit: Literal["c", "f"], note: str | None = None) -> str:
    """Convert a temperature."""
    return f"{amount}{unit}"

PRIMITIVES: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}

def json_type(tp: Any) -> dict[str, Any]:
    """One resolved annotation -> a JSON Schema fragment.
    str/int/float/bool -> {"type": ...}; Literal[...] -> {"enum": [...]};
    X | None -> the fragment for X (optionality is expressed by `required`);
    Annotated[T, "text"] -> the fragment for T plus {"description": "text"}."""
    raise NotImplementedError("LAB: json_type")        # LAB: use get_origin / get_args

def tool_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    """{"name", "description" (first docstring line), "input_schema": {"type": "object",
    "properties", "required" (params without a default, in order), "additionalProperties": False}}."""
    raise NotImplementedError("LAB: tool_schema")      # LAB: resolve hints, do not read raw strings

def validate_args(fn: Callable[..., Any], raw: dict[str, object]) -> dict[str, object] | str:
    """Checked kwargs with defaults filled in, or ONE error string for the model, checked in this order:
    'unknown argument: <k>', 'missing argument: <k>', then per parameter in signature order
    '<k>: expected <json type>, got <python type name>' or '<k>: expected one of [...], got <repr>'.
    No coercion: "3" is not an integer, True is not an integer, 3 is a valid number, None only if X | None."""
    raise NotImplementedError("LAB: validate_args")

if __name__ == "__main__":
    steps: list[Callable[[], object]] = [
        lambda: json.dumps(tool_schema(search)),
        lambda: validate_args(search, {"query": "kv cache", "limit": 3}),
        lambda: validate_args(search, {"query": "kv cache", "limit": "3"}),
        lambda: validate_args(convert, {"amount": 21, "unit": "k"}),
        lambda: validate_args(convert, {"amount": 21.5, "unit": "c", "notes": "x"}),
    ]
    for step in steps:
        try:
            print(step())
        except NotImplementedError as e:
            print("not implemented:", e)
```

### Expected output
`python lab.py` with a correct solution prints exactly:

```
{"name": "search", "description": "Search internal docs or tickets.", "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "What to search for"}, "limit": {"type": "integer"}, "source": {"enum": ["docs", "tickets"]}}, "required": ["query"], "additionalProperties": false}}
{'query': 'kv cache', 'limit': 3, 'source': 'docs'}
limit: expected integer, got str
unit: expected one of ['c', 'f'], got 'k'
unknown argument: notes
```

### Tests
Save as `test_lab.py` next to `lab.py` and run `python -m pytest -q`. On the starter all six fail
with `NotImplementedError`.

```python
from lab import convert, search, tool_schema, validate_args

def test_search_schema():
    assert tool_schema(search) == {
        "name": "search", "description": "Search internal docs or tickets.",
        "input_schema": {"type": "object",
                         "properties": {"query": {"type": "string", "description": "What to search for"},
                                        "limit": {"type": "integer"},
                                        "source": {"enum": ["docs", "tickets"]}},
                         "required": ["query"], "additionalProperties": False}}

def test_optional_is_not_required_and_unwrapped():
    s = tool_schema(convert)["input_schema"]
    assert s["required"] == ["amount", "unit"]
    assert s["properties"]["note"] == {"type": "string"}
    assert s["properties"]["amount"] == {"type": "number"}

def test_valid_call_fills_defaults():
    assert validate_args(search, {"query": "kv"}) == {"query": "kv", "limit": 5, "source": "docs"}

def test_no_coercion_and_bool_is_not_int():
    assert validate_args(search, {"query": "kv", "limit": "3"}) == "limit: expected integer, got str"
    assert validate_args(search, {"query": "kv", "limit": True}) == "limit: expected integer, got bool"

def test_int_is_a_valid_number_and_none_allowed_for_optional():
    assert validate_args(convert, {"amount": 21, "unit": "c", "note": None}) == \
        {"amount": 21, "unit": "c", "note": None}

def test_literal_unknown_and_missing():
    assert validate_args(convert, {"amount": 1.0, "unit": "k"}) == "unit: expected one of ['c', 'f'], got 'k'"
    assert validate_args(convert, {"amount": 1.0, "unit": "c", "notes": "x"}) == "unknown argument: notes"
    assert validate_args(convert, {"amount": 1.0}) == "missing argument: unit"
```

### Failure scenarios
- **Read `fn.__annotations__` instead of `get_type_hints(fn, include_extras=True)`.** You see
  a `KeyError` from `PRIMITIVES` on a string key (`'float'` for `convert`, the whole
  `"Annotated[str, ...]"` text for `search`), because the future import stored every annotation as a
  string. Debug by printing `fn.__annotations__` and noting every value is a `str`.
- **Drop `include_extras=True`.** The `query` description disappears from the schema, because
  `get_type_hints` strips `Annotated` by default. `test_search_schema` shows the missing key; print the
  resolved hint to confirm it is plain `str`.
- **Check `isinstance(v, int)` for integers.** `{"limit": True}` passes, because `bool` subclasses `int`.
  The bool assertion fails; the fix is an explicit `bool` exclusion.
- **Compare `get_origin(tp) is Union` only.** `str | None` is not unwrapped and `PRIMITIVES` raises
  `KeyError`, because `X | None` has origin `types.UnionType`, not `typing.Union`, on 3.10 to 3.13.

### Submission
- **`lab.py` and `test_lab.py`** in a repo or gist, with the output of `python -m pytest -q` showing
  6 passed.
- **The output of `python lab.py`,** matching the expected block line for line.
- **A third tool of your own** (for example `get_order(order_id: str, include_items: bool = False)`)
  with its printed schema, generated by your code, not written by hand.
- **A note of 3-5 sentences:** which failure scenario you hit, and one thing a real validator such as
  Pydantic does that yours does not (coercion, nested models, structured error lists).
- **Evaluation:** tests pass unmodified, the schema output is exact, and the note names a concrete gap.

## Why it exists
- **Python is dynamically typed.** A name can hold anything, and a function discovers what it got only
  when it uses it. Fine for a fifty-line script; it falls apart in agent loops and AI pipelines.
- **AI glue code fails late.** Dicts of dicts flow from a provider SDK through five helpers into a prompt
  template. The first symptom is `KeyError: 'content'` inside a retry loop, or a `TypeError` about
  concatenating `str` and `NoneType` two hours into an eval run.
- **Docstrings do not hold.** They drift, nobody checks them, and a refactor that renames a field
  silently breaks every caller, including the hand-written tool schema your prompt sends the model.
- **Runtime assertions everywhere are not enough.** They catch the error only on the path that executes,
  and clutter every function with checks that belong to the contract, not the logic.
- **One machine-readable contract serves three readers.** A checker walks the call graph without running
  anything and flags every `Message | None` flowing into a `Message` parameter. Pydantic builds
  validators, FastAPI builds parsers and OpenAPI schemas, and typed OpenAI and Anthropic SDKs tell your
  editor `response.choices[0].message.content` can be `None`.

## How it works
### What the interpreter does with an annotation
- **It stores metadata, nothing more.** `def f(x: int) -> str` records `int` and `str` in
  `f.__annotations__`, a dict `{"x": int, "return": str}`.
- **Class and module scope too.** `name: list[str] = []` stores the type in that scope's
  `__annotations__`.
- **No checking.** `f("hello")` runs normally, because CPython performs no checking. This is deliberate:
  near-zero runtime cost and full backward compatibility, with enforcement left to tools.
- **Agent consequence.** A model that sends `{"limit": "5"}` to a tool typed `limit: int` is not stopped
  by Python; only a validator built from the hint (Pydantic, or your framework) rejects or coerces it.

### When the annotation is evaluated
| Python version | Behaviour | Forward reference to a later class |
|---|---|---|
| Up to 3.13 | Eager, at definition time | `NameError` unless quoted (`"Message"`) or the module uses `from __future__ import annotations` |
| Any version, with the future import | Postponed: every annotation stored as a string | Libraries that need the real type resolve strings with `typing.get_type_hints(obj)` |
| 3.14 (PEP 649 and PEP 749) | Lazy: computed when something first reads `__annotations__` (or calls `annotationlib.get_annotations`) | No failure at `def` time, but can still fail later if the name is undefined when a library inspects it |

Check which version your project runs when a library (or a tool decorator building a schema) complains
about unresolved forward references.

### What a checker does with it
1. **Builds a type for every expression** from annotations plus inference.
2. **Applies the gradual-typing rule.** Any unannotated value has type `Any`, and `Any` is compatible with
   everything in both directions. This exists *because* typing had to be adoptable one function at a
   time in a huge untyped ecosystem; a checker that refused partially typed code would never have been
   adopted.
3. **Pays for it with holes.** Once a value is `Any`, every downstream error is invisible. Most "mypy
   said it was fine" surprises trace back to an `Any` from `json.loads`, an untyped library or a
   missing annotation. In agents the classic source is the tool-call `arguments` string or `input` dict.

### The vocabulary that matters
- **`X | None` (older spelling `Optional[X]`)** says the value may be absent; the checker forces you to
  narrow it with `if x is not None` before use. Provider SDK responses are full of these: text content
  is absent on tool-call turns.

| Spelling | Meaning | Why you care in agent code |
|---|---|---|
| `list[str]`, `dict[str, float]`, `tuple[int, ...]` | Built-in generic containers | Prefer abstract inputs (`Sequence[str]`, `Mapping[str, float]`, `Iterable[str]`) and concrete outputs (`list[str]`) so callers can pass tuples or generators |
| `Callable[[str, int], bool]` | A function value | Typing callbacks, hooks and tool handlers in a registry |
| `Awaitable[T]`, `Coroutine` | Async results | Matters when wrapping an async client or async tools |
| `Any` | Opt out of checking | Every use is a hole |
| `object` | Anything, but you may only use what every object supports until you narrow it (for example with `isinstance`) | Choosing `object` over `Any` for genuinely unknown inputs keeps the hole small |
| `Annotated[T, meta]` | Extra data on a type without changing it | Pydantic's `Field` and FastAPI's `Depends` carry runtime constraints; tool frameworks read descriptions from it |
| `TYPE_CHECKING` | Import a type only for the checker | Breaks import cycles; avoids loading torch or transformers just for an annotation |
| `cast(T, value)` | "Trust me", no runtime effect | Escape hatch; use sparingly |
| `reveal_type(x)` | Prints what the checker thinks `x` is | Fastest way to debug an inference surprise |

### Narrowing: how the checker learns more inside a branch
- **A declared type is an upper bound.** After `if msg.content is not None:`, `msg.content` is `str` in
  that block, *because* the checker treats the test as evidence.
- **Tests that narrow:** `isinstance(x, ToolCall)`, `if not x: return`, `assert x is not None`, and a
  `match` case. You pay one explicit check and the rest of the block is precise, which is what makes
  `X | None` usable rather than a nuisance.
- **Helpers do not narrow by default.** The checker cannot see inside `def is_tool_turn(m) -> bool`.
  Annotate it `-> TypeIs[ToolTurn]` (3.13, or `typing_extensions`) or the older
  `-> TypeGuard[ToolTurn]` so a call narrows like an `isinstance`.
- **Narrowing ends** when the name is reassigned. Checkers are conservative about carrying it into a
  nested function or lambda that runs later, which is why you sometimes copy `msg.content` into a local
  first.

### Where types come from for libraries you did not write
- **Inline types** with a `py.typed` marker file (PEP 561).
- **Stub packages** such as `types-requests` or `types-PyYAML`, containing signature-only `.pyi` files.
- **Neither:** every import from the package is `Any`, silently. mypy reports "missing library stubs or
  py.typed marker"; silencing it with `ignore_missing_imports` hides the error and keeps the hole.
- **In practice:** the major LLM SDKs ship inline types; many older data and ML packages are partially
  typed or not at all, so that boundary is where your own typed wrapper goes.

### Why runtime libraries read them
- **Who reads them:** Pydantic, dataclasses and `attrs` at class creation; FastAPI when a route is
  registered; libraries such as `instructor` reuse Pydantic's models; agent frameworks read a tool
  function's signature when it is registered.
- **What it buys:** a `BaseModel` with `temperature: float = 0.7` becomes a validator that coerces `"0.7"`
  to `0.7` and rejects `"hot"`.
- **The bridge:** the annotation you wrote for the checker becomes the schema that validates a model's
  JSON output and the JSON Schema you hand to a tool-calling API. Write the type once, get all three
  behaviours; that is the main practical payoff in agent work.

## Diagram
One annotation, three readers: the interpreter only stores it in `__annotations__`, a checker reads it
before the run, and runtime libraries turn it into the tool's JSON Schema and the validator for the
model's arguments (coerce `"0.7"`, reject `"hot"`). The dotted `Any` edge is where checking stops.

```mermaid
flowchart TD
    A["Tool signature with hints"] --> I["CPython: store only"]
    A --> C["Static checker: mypy, pyright"]
    A --> R["Runtime: Pydantic, tool decorator"]
    C -->|"declared type plus inference"| N["Narrowing in branches"]
    N --> E["Errors before tokens spent"]
    C -.->|"value is Any"| H["Hole: errors invisible"]
    R --> S["JSON Schema sent to model"]
    R --> V["Validator on tool arguments"]
    V -->|"error text"| M["Model retries the call"]
```

## Examples
### A typed tool: one hint, schema plus validation
The argument model is the single source for the schema the model sees and the check on what it sends
back. The shape matches the Anthropic `tools` entry (`name`, `description`, `input_schema`).

```python
from typing import Annotated, Literal
from pydantic import BaseModel, Field, ValidationError

class SearchArgs(BaseModel):
    query: Annotated[str, Field(min_length=1, description="What to search for")]
    limit: Annotated[int, Field(ge=1, le=20)] = 5
    source: Literal["docs", "tickets"] = "docs"

TOOL = {"name": "search", "description": "Search internal docs or tickets.",
        "input_schema": SearchArgs.model_json_schema()}

def search(args: SearchArgs) -> list[str]:
    return [f"{args.source}:{args.query}:{i}" for i in range(args.limit)]

def run_tool_call(name: str, raw_input: dict[str, object]) -> str:
    if name != "search":
        return f"error: unknown tool {name}"
    try:
        args = SearchArgs.model_validate(raw_input)     # raw model JSON becomes a typed object
    except ValidationError as e:
        return f"error: invalid arguments: {e.errors()[0]['msg']}"   # fed back so the model retries
    return "\n".join(search(args))

print(run_tool_call("search", {"query": "kv cache", "limit": "3"}))   # "3" coerced to 3
print(run_tool_call("search", {"query": "kv cache", "limit": 50}))    # error: ... less than or equal to 20
```

### A typed wrapper around a chat completion
The checker flags any caller that forgets `content` can be `None`, and `Annotated` carries a constraint
Pydantic enforces at runtime.

```python
from __future__ import annotations
from collections.abc import Sequence
from typing import Annotated, Any, Literal
from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]

class Message(BaseModel):
    role: Role
    content: str

class GenConfig(BaseModel):
    model: str
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.7
    max_tokens: Annotated[int, Field(gt=0)] = 512

def to_wire(messages: Sequence[Message]) -> list[dict[str, str]]:
    # Sequence accepts list or tuple; the return is a concrete list for the SDK.
    return [m.model_dump() for m in messages]

def first_text(response: Any) -> str | None:
    # SDK responses are typed in the real SDK; Any here stands in for "unknown JSON".
    choice = response["choices"][0]
    return choice["message"].get("content")   # may be None for tool-call turns

def reply(messages: Sequence[Message], cfg: GenConfig, response: Any) -> str:
    text = first_text(response)
    if text is None:                 # the checker forces this narrowing
        raise ValueError("model returned no text content")
    return text.strip()

raw = {"model": "gpt-4o-mini", "temperature": "0.2"}       # e.g. parsed from a YAML config
cfg = GenConfig.model_validate(raw)                        # "0.2" coerced to 0.2 by Pydantic
print(cfg.temperature, type(cfg.temperature))            # 0.2 <class 'float'>
# GenConfig(model="x", temperature=3.0) raises a ValidationError: le=2.0
```

### Worked experiment: the `Any` hole in one file
Run `mypy` on the file above after each edit.

| Edit | mypy result | Why |
|---|---|---|
| Delete the `if text is None` branch | Error: the optional string has no attribute `strip` | The value proposition, delivered before the first API call |
| Keep it deleted and also delete `first_text`'s return annotation | Silent | A missing return annotation defaults to `Any` (a fully unannotated function is not checked at all); `Any.strip()` is accepted |
| Call `GenConfig(model="x", temperature="0.2")` directly | Error: `str` where `float` expected (the call still runs, because Pydantic coerces) | A checker that understands Pydantic's `__init__` through `dataclass_transform` flags the `str` argument |
| Same call with Pydantic's mypy plugin enabled | Silent by default | The plugin deliberately types `__init__` parameters as `Any` unless you set `init_typed = True` |

- **The first error is the whole point:** `str | None` has no attribute `strip`, reported before any
  tokens are spent. On a tool-call turn that `None` is the normal case, not an edge case.
- **Lesson 1:** the annotation on the function that touches raw JSON matters more than any other.
- **Lesson 2:** untyped input goes through `model_validate` at the boundary, not through `__init__`.

### Narrowing helper with `TypeIs`
Without `TypeIs` the checker cannot see inside the helper, so `turn.tool_name` would still be an error.
This is the shape of the dispatch step in an agent loop.

```python
from dataclasses import dataclass
from typing_extensions import TypeIs   # typing.TypeIs on 3.13+

@dataclass
class TextTurn:
    text: str

@dataclass
class ToolTurn:
    tool_name: str
    args: dict[str, object]

def is_tool_turn(t: TextTurn | ToolTurn) -> TypeIs[ToolTurn]:
    return isinstance(t, ToolTurn)

def describe(turn: TextTurn | ToolTurn) -> str:
    if is_tool_turn(turn):
        return f"call {turn.tool_name}"    # narrowed to ToolTurn
    return turn.text                       # narrowed to TextTurn in the else branch

print(describe(ToolTurn("search", {"q": "kv cache"})))   # call search
```

## Production patterns
- **Generate tool schemas from types, never by hand.** Define each tool's arguments as a Pydantic model
  (or let a framework decorator read the signature) and send `model_json_schema()` as the tool schema.
  Hand-written JSON drifts from the handler. Monitor the tool-argument validation-error rate per tool; a
  spike after a prompt or schema change means the model and contract disagree.
- **Strict at the edges, gradual inside.** Set `strict = true` under `[tool.mypy]` (it is a global-only
  option, so it cannot be scoped to one package), then add `[[tool.mypy.overrides]]` with a `module`
  pattern for legacy code that relaxes it, for example `ignore_errors = true` or
  `disallow_untyped_defs = false`. SDK wrappers and tool handlers stay strict. Track the count of
  overridden modules and shrink it each sprint.
- **Validate at the boundary.** Parse every `json.loads` result, raw SDK dict or tool-call input with
  `Model.model_validate(...)` (or `TypeAdapter(list[Message]).validate_python(...)`) in the first
  function that receives it. Monitor validation-error rate per provider; a spike means an API changed
  shape.
- **Hunt `Any` leaks in CI.** Enable `warn_return_any` and `disallow_any_generics`, and run
  `mypy --strict` on new packages. Watch the error count; a jump after adding a dependency usually means
  missing stubs, so install the `types-*` package rather than `ignore_missing_imports`.
- **Enable the Pydantic mypy plugin deliberately.** In `pyproject.toml` add `plugins = ["pydantic.mypy"]`
  under `[tool.mypy]` and `init_typed = true` under `[tool.pydantic-mypy]` if you want constructor
  arguments checked. Without that setting the plugin treats `__init__` parameters as `Any`, so
  `GenConfig(temperature="0.2")` passes the checker.
- **Keep heavy imports behind `TYPE_CHECKING`.** Wrap torch or transformers imports used only in
  annotations in `if TYPE_CHECKING:`, so an agent worker or CLI starts fast. Measure import time
  (`python -X importtime`) before and after.

## Trade-offs
- **Early detection vs annotation effort.** You catch shape bugs across the call graph before running,
  but pay effort up front. The payoff flips negative for throwaway notebooks and one-off scripts whose
  whole life is a single run, where the runtime error *is* the cheapest check.
- **Gradual adoption vs leaks.** You can adopt incrementally, but every `Any` leak silently disables
  checking downstream, so a codebase "typed" at 60% often catches far fewer bugs than it looks.
- **One source of truth vs library coupling.** Annotations double as Pydantic, FastAPI and tool schemas,
  saving duplicated validation code. But your static types are now coupled to a library's interpretation
  (for example, how a `float` field coerces strings), which can surprise you on a major version change.
- **Precise SDK types vs verbosity.** They make the editor useful and catch `None` cases, but deeply
  nested unions make simple code verbose. Wrapping them in your own small models pays off once the same
  response shape is consumed in more than one place, as it is in any agent loop.
- **Rich tool types vs what the model handles.** Nested unions and many constrained fields make an exact
  schema but a harder one for the model to fill. Flat argument models with a few `Literal` choices tend
  to produce fewer invalid calls; measure on your eval set.

## Pitfalls
- **Over-specific parameters.** Annotating `list[str]` when callers pass tuples or generators gives a wall
  of checker errors at call sites. Fix: accept `Sequence[str]` or `Iterable[str]`.
- **Treating annotations as enforcement.** A function typed `-> int` happily returns a string at runtime
  because nothing checked it; you find out much later in a `TypeError`. A tool handler called with
  model-supplied arguments is exactly this case unless a validator sits in front of it.
- **Letting `json.loads` output (typed `Any`) flow untouched.** Symptom: a clean mypy run, then
  `KeyError` in production, because `Any` suppressed every downstream error. Fix: validate at the
  boundary with Pydantic or a `TypedDict` so the type becomes concrete.
- **Forward-reference `NameError` at import time** (Python 3.13 and earlier) when one class annotates a
  field with a class defined below it. Fix: quote the name or use `from __future__ import annotations`,
  and know some runtime libraries then need `get_type_hints` to resolve the strings.
- **Expecting a `bool` helper to narrow.** After `if is_valid(x):` with `is_valid(x) -> bool`, you get the
  same "may be None" error. Fix: annotate the helper with `TypeIs`/`TypeGuard` or inline the test.
- **Reading `Optional[X]` as "optional argument".** `Optional` only means "may be None"; without a
  default, the checker still requires the argument be passed. In a tool schema the field is then
  required too, so the model must send it.

## When to use
- **Always annotate boundaries:** public module functions, anything taking or returning provider SDK
  objects, tool handlers an agent calls, and data structures that get serialized into prompts or traces.
- **Skip inside tight private helpers:** inference is usually enough.
- **Run a checker in CI once annotations exist:** hints without a checker are documentation that rots.
- **Measure the `Any` surface:** mypy's `--strict` flag or `warn_return_any` shows where typing leaks.
- **Day to day:** type hints let you refactor a prompt-assembly pipeline or tool registry with confidence
  and make Pydantic models and tool schemas possible at all.
- **Interviews:** know that annotations are inert at runtime, how gradual typing and `Any` interact, and
  how Pydantic turns an annotation into a validator and a JSON Schema.

## Role lens
- **Engineer:** Writes typed wrappers around SDK calls and tool handlers so mypy catches `None`
  content and wrong field names before an eval run burns tokens.
- **Architect:** Sets the policy: which boundaries must be fully typed, whether CI blocks on the
  checker, and that shared message and tool schemas live in one typed package.
- **FDE:** Reads a customer's untyped integration code and adds annotations at the seams first,
  turning vague "it sometimes breaks" reports into concrete checker errors within a day.

## Check yourself
**Q:** A colleague says "we added type hints everywhere and mypy passes, but we still get
`KeyError` and `AttributeError: 'NoneType'` in production from our LLM pipeline". What is the most
likely cause and how would you fix it?
**A:** Passing mypy with those runtime errors almost always means `Any` is leaking. The provider
response or `json.loads` output enters as `Any`, so every attribute access and key lookup
downstream is unchecked; the pipeline is "typed" syntactically but not semantically. The fix is to
make the boundary concrete: parse the raw response into a Pydantic model or a `TypedDict`
immediately, with `content: str | None` where the API can omit it, then let the checker force
narrowing. Enable `warn_return_any` and `disallow_any_generics` to find remaining holes, and check
that third-party stubs are installed so SDK calls are not silently `Any` either.

## Interview questions
### What do type hints actually do at runtime in Python, and why were they designed that way?
- **Almost nothing.** CPython stores annotations in `__annotations__` and never checks them, so a
  function typed `-> int` can return a string without complaint.
- **Deliberately so:** near-zero runtime cost and full backward compatibility, with enforcement left to
  static checkers and to libraries such as Pydantic that choose to read the annotations.

### How do type hints on a tool function become something the model can use, and what still has to happen at runtime?
- **Schema generation.** A framework decorator or a Pydantic argument model reads the annotations (plus
  `Annotated` descriptions and constraints) and emits the JSON Schema sent with the request.
- **Runtime validation is separate.** The model can still send `"5"` or omit a field, so validate the
  call's input with the same model before running the handler.
- **Close the loop:** return the validation message to the model as the tool result so it can retry,
  and count those errors per tool as a signal of a confusing schema.

### You call `if is_tool_turn(turn):` and mypy still says `turn` has no attribute `tool_name`. Why, and what is the fix?
- **The checker does not look inside helpers.** A `-> bool` return says nothing about the argument, so
  `turn` keeps its declared union type inside the branch.
- **Annotate the helper `-> TypeIs[ToolTurn]`** (3.13, or `typing_extensions`). It narrows like
  `isinstance`: to `ToolTurn` in the true branch and to the remaining members in the else branch.
- **`TypeGuard` is the older form:** it narrows only the true branch, and does not require the guarded
  type to be a subtype of the input, so prefer `TypeIs` for plain type tests.

### When would you choose `object` over `Any` for an unknown input?
- **`object` keeps checking on.** You may only use what every object supports until you narrow it with
  `isinstance`, so mistakes are still caught.
- **`Any` turns checking off** in both directions for that value and everything derived from it.
- **Use `Any` only** as a temporary escape hatch at an untyped library seam, and wrap it quickly.

### How would you introduce type checking into a 50,000-line untyped agent service without stopping feature work?
- **Gradual by design:** unannotated code is `Any`, so you can start with zero errors and tighten module
  by module.
- **Order:** type the boundaries first (SDK wrappers, tool handlers, serialized models and agent
  state), set `strict = true` globally, and relax untouched legacy modules with `[[tool.mypy.overrides]]`
  (`strict` itself cannot be set per module).
- **Track progress** by the number of overridden modules and the `Any` surface, not by the share of
  functions with annotations.

### Why might a forward reference to a class break at import time, and how does Python 3.14 change that?
- **Up to 3.13 annotations are evaluated eagerly** at definition time, so naming a class defined further
  down raises `NameError` unless quoted or the module uses `from __future__ import annotations`.
- **3.14 evaluates lazily** (PEP 649 and PEP 749), on first read of `__annotations__`, so the `def` no
  longer fails, but a library inspecting the annotation can still fail if the name is undefined then.

## Quiz
### Q: What does CPython do with the annotation in `def f(x: int) -> str` when `f("a")` is called?
- [ ] Raises TypeError because "a" is not an int
- [ ] Coerces "a" to int if possible, otherwise raises
- [x] Nothing; the annotation is stored in `f.__annotations__` and never checked
- [ ] Emits a RuntimeWarning about a type mismatch
> Annotations are inert metadata (evaluated at definition time up to 3.13, lazily from 3.14). Enforcement is the job of static
> checkers or libraries that explicitly read the annotations, never the interpreter itself.

### Q: Why can a fully "type-hinted" codebase still let shape bugs through mypy?
- [ ] mypy only checks functions that have docstrings
- [x] Values typed `Any` (from `json.loads`, untyped libraries or missing stubs) disable checking downstream
- [ ] mypy ignores `Optional` types by default
- [ ] Type hints are only checked inside classes, not module-level functions
> Gradual typing makes `Any` compatible with everything, so once a value is `Any` every operation
> on it is accepted. Untyped JSON and missing stubs are the usual sources.

### Q: Which annotation best fits a parameter that will only be iterated over, so callers may pass a list, tuple or generator?
- [ ] `list[str]`
- [ ] `Any`
- [x] `Iterable[str]`
- [ ] `dict[str, str]`
> `Iterable[str]` is the least demanding contract that still guarantees iteration, so all those
> callers type-check. `list[str]` rejects tuples and generators; `Any` discards checking entirely.

## Use cases & projects
### Use cases
- **Tool schemas from signatures.** Agent frameworks read a handler's annotations and docstring to build
  the schema the model sees, so the hint is the contract the model must satisfy.
- **Typing the raw-JSON boundary.** The function that receives provider responses declares `X | None`
  where content can be missing, so the checker forces the tool-call-only branch to be handled.
- **Refactoring an agent core safely.** Renaming a field in a message or step type surfaces every stale
  caller in CI instead of in a long eval run.
- **Typed graph state.** LangGraph-style state declared with `TypedDict` and `Annotated` reducers lets
  the checker flag nodes that return keys the graph does not have.

### Mini project
**Type-check a small tool-calling loop (1-2 hours).**
- **Goal:** take an untyped agent loop with two tools and make the checker catch the bugs before a run.
- **Steps:** write the loop with plain dicts and plant two bugs (a misspelled message key and an
  unhandled `None` content); annotate the tool handlers, the message type and the response parser; run
  mypy or pyright in strict mode and fix what it reports; generate each tool's schema from its signature.
- **Done when:** the checker reports both planted bugs before any model call, the fixed loop passes clean,
  and the generated tool schemas match the annotated parameters.
