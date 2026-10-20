---
id: type-hints
module: python-for-ai
name: Type hints
kind: concept
difficulty: intro
est_minutes: 14
status: fact-checked
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
Type hints are annotations on parameters, return values and variables that describe what a value
is expected to be. The interpreter stores them and ignores them; static checkers (mypy, pyright)
and runtime libraries (Pydantic, FastAPI, dataclasses) read them. They exist because AI code is
mostly glue between untyped JSON, provider SDKs and your own functions, and the cheapest place to
catch a shape mismatch is before the code runs, not after a thirty-minute batch job fails.

## Why it exists
Python is dynamically typed: a name can hold anything, and a function discovers what it was given
only when it tries to use it. That is fine for a fifty-line script. It falls apart in exactly the
code AI engineers write all day: pipelines that pass dictionaries of dictionaries from a provider
SDK through five helper functions into a prompt template, where the first symptom of a wrong shape
is `KeyError: 'content'` deep inside a retry loop, or a `TypeError` about concatenating `str` and
`NoneType` two hours into an evaluation run.

The naive alternative is documentation and discipline: write the expected shape in a docstring and
trust callers. Docstrings drift, nobody checks them, and a refactor that renames a field silently
breaks every caller. The second alternative, runtime assertions everywhere, catches the error but
only on the code path that executes, and it clutters every function with checks that belong to the
contract, not the logic.

Type hints solve this by making the contract machine-readable. A static checker walks the whole
call graph without running anything and reports every place where a `Message | None` flows into a
parameter typed `Message`. The same annotations let libraries do runtime work for free: Pydantic
builds validators from them, FastAPI builds request parsers and OpenAPI schemas, and the OpenAI and
Anthropic SDKs ship fully typed so your editor knows that `response.choices[0].message.content` can
be `None`. One source of truth serves the checker, the runtime and the human reader.

## How it works
### What the interpreter does with an annotation
An annotation is an expression written after a colon. For a function, `def f(x: int) -> str`
records `int` and `str` as metadata, exposed as `f.__annotations__`, a dict
`{"x": int, "return": str}`. For a class or module, `name: list[str] = []` stores the type in
`__annotations__` of that scope. Nothing else happens. Calling `f("hello")` runs normally, because
CPython performs no checking; the annotation is metadata. This design choice is deliberate: the
language keeps near-zero runtime cost and full backward compatibility, and leaves enforcement to
tools.

*When* the annotation expression is evaluated depends on the interpreter version. Up to Python 3.13
it is evaluated eagerly at definition time, so a forward reference to a class defined later in the
module fails with `NameError` unless it is quoted (`"Message"`) or the module opts into postponed
evaluation with `from __future__ import annotations`, which stores every annotation as a string.
Libraries that need the real type then call `typing.get_type_hints(obj)` to resolve the strings.
Python 3.14 (PEP 649 and PEP 749) makes evaluation lazy by default: annotations are computed only
when something first reads `__annotations__` (or calls `annotationlib.get_annotations`), so a
forward reference no longer fails at `def` time, but it can still fail later if the name is
undefined when a library finally inspects it. Check which version your project runs when a library
complains about unresolved forward references.

### What a checker does with it
A static checker builds a model of every expression's type from the annotations plus inference. The
gradual-typing rule is the key design choice: any unannotated value has type `Any`, and `Any` is
compatible with everything in both directions. That is *because* the authors needed typing to be
adoptable one function at a time in a huge untyped ecosystem; a checker that refused to run on
partially typed code would never have been adopted. The cost is that `Any` is a hole: once a value
is `Any`, every downstream error is invisible. Most "mypy said it was fine" surprises trace back to
an `Any` that entered from `json.loads`, an untyped library or a missing annotation.

The important vocabulary:

- `X | None` (older spelling `Optional[X]`) says the value may be absent; the checker forces you to
  narrow it with `if x is not None` before using it. Provider SDK responses are full of these.
- Built-in generics `list[str]`, `dict[str, float]`, `tuple[int, ...]` describe containers. Prefer
  abstract inputs (`Sequence[str]`, `Mapping[str, float]`, `Iterable[str]`) and concrete outputs
  (`list[str]`) so callers can pass tuples or generators.
- `Callable[[str, int], bool]` types a function value; `Awaitable[T]` and `Coroutine` type async
  results, which matters when wrapping an async client.
- `Any` means "opt out"; `object` means "anything, but you may only use what every object supports
  until you narrow it (for example with `isinstance`)". Choosing
  `object` over `Any` for genuinely unknown inputs keeps the hole small.
- `Annotated[T, meta]` attaches extra data to a type without changing it; Pydantic's `Field` and
  FastAPI's `Depends` use this so the same annotation carries both the static type and runtime
  constraints.
- `TYPE_CHECKING` lets you import a type only for the checker, breaking import cycles and avoiding
  loading heavy packages (torch, transformers) just for an annotation.
- `cast(T, value)` tells the checker "trust me" with no runtime effect; `reveal_type(x)` prints what
  the checker thinks `x` is, which is the fastest way to debug an inference surprise.

### Why runtime libraries read them
Pydantic, dataclasses and `attrs` introspect annotations at class creation, FastAPI does it when a
route is registered, and libraries such as `instructor` reuse Pydantic's models. A `BaseModel` with `temperature: float = 0.7` becomes a
validator that coerces `"0.7"` to `0.7` and rejects `"hot"`. This is the bridge between static and
runtime typing: the annotation you wrote for the checker becomes the schema that validates a model's
JSON output and the JSON Schema you hand to a tool-calling API. Writing the type once and getting
all three behaviours is the main practical payoff in AI work.

## Example
A thin typed wrapper around a chat completion call. The checker will flag any caller that forgets
`content` can be `None`, and `Annotated` carries a constraint Pydantic enforces at runtime.

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

Run `mypy` on this file and delete the `if text is None` branch: the checker reports that
`str | None` has no attribute `strip`. That is the whole value proposition, delivered before the
first API call. Note the division of labour on the last lines: writing
`GenConfig(model="x", temperature="0.2")` directly would run fine (Pydantic coerces it) but a
checker that understands Pydantic's `__init__` flags the `str` argument, which is why untyped input
goes through `model_validate` at the boundary instead.

## Trade-offs
- You gain compile-time detection of shape bugs across the call graph, but you pay the annotation
  effort up front; the payoff flips negative only for throwaway notebooks under a few hundred lines.
- Gradual typing lets you adopt incrementally, but every `Any` leak silently disables checking
  downstream, so a codebase that is "typed" at 60% often catches far fewer bugs than it looks.
- Annotations double as runtime schemas for Pydantic and FastAPI, saving duplicated validation code,
  but it couples your static types to a library's interpretation of them (for example, how a
  `float` field coerces strings), which can surprise you when the library changes major version.
- Precise types on third-party SDKs make the editor useful and catch `None` cases, yet the SDKs'
  deeply nested union types can make simple code verbose; wrapping them in your own small models
  usually pays for itself once the same response shape is consumed in more than one place.

## Pitfalls
- Annotating a parameter as `list[str]` when callers pass tuples or generators; the symptom is a wall
  of checker errors at call sites, fixed by accepting `Sequence[str]` or `Iterable[str]`.
- Treating annotations as enforcement; the symptom is a function typed `-> int` that happily returns
  a string at runtime because nothing checked it, discovered much later in a `TypeError`.
- Letting `json.loads` output (typed `Any`) flow through the pipeline untouched; the symptom is a
  clean mypy run followed by `KeyError` in production, because `Any` suppressed every downstream
  error. Validate at the boundary with Pydantic or a `TypedDict` so the type becomes concrete.
- Forward-reference `NameError` at import time (Python 3.13 and earlier) when one class annotates a
  field with another class defined below it; fix by quoting the name or using `from __future__ import annotations`, and know
  that some runtime libraries then need `get_type_hints` to resolve the strings.
- Using `Optional[X]` without a default and expecting it to mean "optional argument"; `Optional`
  only means "may be None", and the checker will still require the argument be passed.

## When to use
Annotate every function that crosses a boundary: public module functions, anything that takes or
returns provider SDK objects, tool handlers an agent calls, and data structures that get serialized.
Inside a tight private helper, inference is usually enough. Run a checker in CI once annotations
exist; hints without a checker are documentation that rots. Measure the `Any` surface: mypy's
`--strict` flag or `warn_return_any` shows where typing is leaking. In day-to-day AI work, type hints
are what let you refactor a prompt-assembly pipeline with confidence and what make Pydantic models
and tool schemas possible at all. In interviews, the expected depth is knowing that annotations are
inert at runtime, how gradual typing and `Any` interact, and how Pydantic turns an annotation into a
validator and a JSON Schema.

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
