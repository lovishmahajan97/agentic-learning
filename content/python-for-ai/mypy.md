---
id: mypy
module: python-for-ai
name: mypy
kind: tool
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - python-for-ai/type-hints
  - python-for-ai/protocols-generics-structural-typing
  - python-for-ai/typeddict-literal-enum
  - python-for-ai/pydantic
  - python-for-ai/ruff
  - agents/typed-tool-contracts
  - backend-fundamentals/ci-cd
sources:
  - Official docs: mypy
  - PEP 484 — Type Hints
  - PEP 561 — Distributing and Packaging Type Information
---

## TL;DR
mypy is a static type checker: it reads your type hints, infers the rest, and reports code that would
pass the wrong type, read a missing key or call a method on `None`, without running anything. Python
ignores hints at runtime, so without a checker they are comments that drift. mypy is gradual: unannotated
code is mostly unchecked, which lets large codebases adopt it module by module but also hides gaps.

## Why it exists
Python's runtime does not enforce annotations. `def call_model(prompt: str) -> str` happily accepts a
list and returns `None`; you find out when a downstream `.strip()` raises `AttributeError` in production,
often on a rare path: the model returned no content, an env var was unset, a tool call had no arguments.
AI code is especially exposed because so much of it passes loosely shaped data between layers: provider
responses where `content` may be `None`, dicts of tool arguments, optional fields in config, results
that are either a value or an error.

Tests catch some of this, but only on the paths you test, and the `None` paths are exactly the ones
people forget. Runtime validation with Pydantic catches it at boundaries, but not inside your own code
between boundaries. The naive alternative, "we have annotations, that is enough", gives you
documentation that silently becomes wrong after the third refactor, because nothing checks it.

A static checker turns annotations into a cheap, whole-program test that runs in seconds and covers
every path, including the ones no test exercises. It also makes refactoring safe: change a function's
signature and the checker lists every caller you broke.

## How it works
### Checking
mypy parses each module, builds types for every definition from annotations and from inference
(`x = []` followed by `x.append(1)` infers `list[int]`), and checks every expression against them.
Errors carry a code in brackets (`[arg-type]`, `[union-attr]`, `[return-value]`), which you use for
targeted ignores and configuration.

**Narrowing** is what makes optional types usable: after `if content is None: return`, mypy knows
`content` is `str`. The same works for `isinstance`, comparisons against `Literal` values, and
`TypeGuard`/`TypeIs` functions. Calling `assert_never(x)` in the final branch makes mypy prove a
chain of checks is exhaustive, so adding a new literal value breaks the build where it is not handled.

### Gradual typing
Anything typed `Any` is compatible with everything, in both directions, so errors stop propagating
through it. By default mypy does not check the bodies of functions that have no annotations at all,
because adopting a checker on an untyped codebase would otherwise produce thousands of errors. Both
choices make adoption possible and both hide bugs, which is why `--strict` (and flags such as
`disallow_untyped_defs`, `check_untyped_defs`, `warn_return_any`, `warn_unused_ignores`) exist.

### Third-party types
For libraries, mypy uses inline hints if the package ships a `py.typed` marker (PEP 561), otherwise
stub packages: the standard library's from typeshed (bundled), others from `types-*` packages. Untyped
libraries produce "missing library stubs" errors, silenced per module with `ignore_missing_imports`.
Some libraries need plugins to be understood, such as Pydantic's mypy plugin for model constructors.

### Configuration and speed
Configure in `pyproject.toml`; per-module overrides let you be strict in new code and lenient in legacy:

```toml
[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = ["legacy.*"]
disallow_untyped_defs = false

[[tool.mypy.overrides]]
module = ["some_untyped_sdk.*"]
ignore_missing_imports = true
```

mypy caches results in `.mypy_cache` and rechecks only what changed; `dmypy` keeps a daemon running for
faster editor-style feedback. The main alternative is pyright (the engine behind Pylance), which is
faster and infers more aggressively, so the two disagree at the edges; newer Rust-based checkers such as
Astral's ty and Meta's Pyrefly are much faster but younger, so check their current maturity and
typing-spec conformance before making one a gate. Pick one checker as the CI gate.

## Example
Bugs a test suite can easily miss, each reported by mypy with its error code, plus one it misses. The comments show
mypy's messages (paths and line numbers omitted).

```python
import os
from typing import Literal, TypedDict, assert_never   # assert_never: Python 3.11+

class ToolCall(TypedDict):
    name: Literal["search", "fetch_url"]
    arguments: dict[str, str]

def run(model: str, prompt: str) -> str: ...

def answer(prompt: str) -> str:
    model = os.environ.get("MODEL")          # str | None
    return run(model, prompt)
    # error: Argument 1 to "run" has incompatible type "str | None"; expected "str"  [arg-type]

def first_text(content: str | None) -> str:
    return content.strip()
    # error: Item "None" of "str | None" has no attribute "strip"  [union-attr]

def query_of(call: ToolCall) -> str:
    return call["argumnets"]["query"]
    # error: TypedDict "ToolCall" has no key "argumnets"  [typeddict-item]

def route(call: ToolCall) -> str:
    name = call["name"]
    if name == "search":
        return "search-service"
    elif name == "fetch_url":
        return "fetcher"
    else:
        assert_never(name)   # adding "run_sql" to the Literal makes this line an error

def helper(resp):                  # unannotated: mypy skips this body by default
    return first_text(resp) + 1    # str + int: reported only with check_untyped_defs (or strict)
    # resp.choises would pass even then: an unannotated parameter is Any until you annotate it
```

The fixes are one line each (`os.environ["MODEL"]` or a default, an `is None` branch, the correct key),
and the last function shows why `strict` matters: unannotated bodies are skipped by default, and even
`check_untyped_defs` cannot see through parameters that are implicitly `Any`. Strict mode's
`disallow_untyped_defs` forces the annotation that makes the checker useful there.

## Trade-offs
- Strict mode catches the most bugs, but costs annotation effort and fights dynamic code; it flips from
  burden to bargain once a codebase is refactored often or shared by several teams.
- Gradual typing lets you adopt mypy incrementally, but `Any` and unannotated functions leave holes that
  look checked; track the untyped share and ratchet strictness module by module.
- Static checks cover every path at near-zero runtime cost, but say nothing about data from outside the
  process; model output and HTTP payloads still need runtime validation with Pydantic or similar.
- pyright is faster and more aggressive at inference, while mypy is the long-standing reference with a
  plugin system; mixing both as CI gates mostly produces arguments about edge cases.

## Pitfalls
- Sprinkling bare `# type: ignore` comments; the symptom is suppressed real errors that outlive their
  reason, which `warn_unused_ignores` and error-code-specific ignores make visible.
- Typing provider responses or tool arguments as `dict[str, Any]`; the symptom is mypy passing code that
  reads misspelled keys, because `Any` turns off every check downstream of it.
- Leaving functions unannotated and assuming they are checked; the symptom is obvious misuse of typed
  values in those bodies that mypy never reports until `check_untyped_defs` is on, and attribute typos
  on their implicitly `Any` parameters that stay invisible until the parameters are annotated.
- Running mypy only in editors, not in CI; the symptom is a main branch with hundreds of errors that
  nobody can turn on as a gate without a week of cleanup.
- Ignoring missing stubs globally rather than per module; the symptom is a typo in an import or a
  wrongly named function from a library that no longer produces any error.

## When to use
Run mypy (or one chosen alternative) on every service that will be maintained beyond a prototype, as a
CI gate alongside ruff and pytest. New projects should start in strict mode; existing ones should start
lenient, fix the error codes that find real bugs (`union-attr`, `arg-type`, `return-value`), and tighten
per module. Pair it with `TypedDict`, `Literal` and Pydantic models at the points where untyped data
enters, because types are only as good as the boundary that establishes them. In interviews, explain
gradual typing and why `Any` is contagious, not just which flags you set.

## Role lens
- **Engineer:** Annotates provider and tool boundaries precisely, narrows optional values explicitly,
  and treats every `type: ignore` as a reviewed exception with an error code.
- **Architect:** Sets the typing policy: which checker gates CI, strictness for new versus legacy
  modules, and how typed contracts flow between services and agent tools.
- **FDE:** Adds typed adapters around a customer's loosely shaped API early, so changes in their payloads
  surface as type errors in the integration code rather than as runtime failures.

## Check yourself
**Q:** A team says "we have type hints everywhere and mypy passes, yet we keep getting `NoneType has no
attribute` errors in production." Give two plausible causes and how to confirm each.
**A:** First, `Any` leakage: responses or JSON are typed `dict[str, Any]` or come from an untyped library,
so attribute access on them is never checked. Confirm with `reveal_type` at the failing site or mypy's
any-expression reports, and fix by modelling the data with `TypedDict` or Pydantic at the boundary.
Second, gradual-typing gaps: the failing code is in unannotated functions or under a lenient override,
so its body is not checked; turn on `check_untyped_defs` or strict for that module and see what appears.
A third possibility is that runtime data violates the declared types, such as a field the provider
documents as present arriving as null, which only runtime validation catches.

## Quiz
### Q: By default, what does mypy do with the body of a function that has no annotations?
- [ ] Infers all parameter types from call sites and checks the body fully
- [x] Treats it as dynamically typed and does not report errors inside the body
- [ ] Refuses to check the module until annotations are added
- [ ] Converts the parameters to object and reports every attribute access
> Skipping unannotated bodies makes gradual adoption possible; `check_untyped_defs` or strict mode
> changes that. mypy does not infer parameter types from call sites; unannotated parameters are
> treated as Any.

### Q: Why does typing a model response as `dict[str, Any]` weaken mypy's checking so much?
- [ ] mypy cannot check dictionaries at all
- [ ] It forces mypy into a slower checking mode
- [x] Values typed Any are compatible with everything, so errors downstream of them are not reported
- [ ] It makes mypy treat the whole module as untyped
> `Any` switches off checks wherever it flows, so a misspelled key or wrong attribute passes silently.
> Modelling the response with TypedDict or Pydantic restores checking downstream.

### Q: What does calling `assert_never(x)` in a final `else` branch let mypy verify?
- [ ] That x is never None at runtime
- [x] That every possible value of x's union or Literal type was handled by earlier branches
- [ ] That the function never raises an exception
- [ ] That the else branch is executed at least once in tests
> After narrowing, x in the final branch should have no remaining type; if a new Literal value is added
> and not handled, mypy reports an error at the assert_never call.
