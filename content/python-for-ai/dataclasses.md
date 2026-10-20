---
id: dataclasses
module: python-for-ai
name: dataclasses
kind: concept
difficulty: intro
est_minutes: 13
related:
  - python-for-ai/type-hints
  - python-for-ai/pydantic
  - python-for-ai/object-model-and-mutability
  - python-for-ai/typeddict-literal-enum
  - python-for-ai/memory-and-performance-profiling
  - agent-reliability/state-and-checkpoint-stores
sources:
  - Smith, 2017 — PEP 557: Data Classes
  - Official docs: dataclasses module
---

## TL;DR
`@dataclass` generates `__init__`, `__repr__`, `__eq__` and optionally ordering, hashing and
`__slots__` from a class's annotated fields, so a record type is five lines instead of thirty. It
exists because AI code is full of small structured records (generation configs, agent steps,
retrieved chunks) that need equality, readable reprs and sometimes immutability, and hand-writing
those methods is where bugs and inconsistencies creep in. It validates nothing; that is Pydantic's
job.

## Why it exists
Every system accumulates classes that are mostly data: a `GenerationConfig` with a model name and a
temperature, a `RetrievedChunk` with text and a score, an agent `Step` with a tool name, arguments
and output. Written by hand, each needs an `__init__` that assigns every field, a `__repr__` so
logs are readable, an `__eq__` so tests can compare them, and sometimes `__hash__` so they can be
cache keys. That is thirty lines of boilerplate per class, and the boilerplate is where the bugs
live: a field added to `__init__` but forgotten in `__eq__`, a repr that omits the one field you
needed in the incident log, a `__hash__` that disagrees with `__eq__` and silently breaks sets.

The naive alternatives each fail in a specific way. A plain dict has no field names the checker
can verify, no repr beyond braces and no way to attach methods. A tuple or `NamedTuple` is
immutable and hashable but also iterable and indexable, so `chunk[1]` compiles and the meaning of
position 1 changes when you add a field. Writing the dunder methods by hand is correct until the
second person edits the class.

Dataclasses generate the methods from the single source of truth, the annotated field list, so
they cannot disagree with it. The design choice to generate code at class creation (rather than
use a dynamic `__getattr__` trick) was made so the resulting class is an ordinary class: fast
attribute access, normal inheritance, nothing magic at call time.

## How it works
### What the decorator generates
`@dataclass` inspects the class's `__annotations__` in definition order and, for each annotated
name, creates a field. It then synthesizes methods and attaches them to the class:

- `__init__(self, a, b=default)` assigning each field, with fields that have defaults after those
  that do not (the same rule as function parameters, and for the same reason).
- `__repr__` printing `ClassName(a=..., b=...)`, which is what makes agent traces readable.
- `__eq__` comparing instances field by field as tuples, only when the other object is the same
  class.
- With `order=True`, `__lt__` and friends comparing field tuples in order.
- With `frozen=True`, `__setattr__` and `__delattr__` that raise `FrozenInstanceError`.
- `__hash__` only when it is safe: `frozen=True` with `eq=True` gives a hash of the field tuple;
  `eq=True` alone sets `__hash__ = None`, making instances unhashable *because* a mutable object
  whose hash can change would corrupt any set or dict it lives in. `unsafe_hash=True` overrides
  this when you promise not to mutate.
- With `slots=True` (3.10), a `__slots__` declaration, which removes the per-instance `__dict__`,
  cuts memory per instance and speeds attribute access, and rejects assignment to undeclared
  attributes. That last property catches typos like `cfg.temprature = 0.5`.
- With `kw_only=True` (3.10), all fields become keyword-only, which lets you add fields in any
  order and avoids positional-argument mistakes on classes with many same-typed fields.

### Defaults and `field()`
`x: list[str] = []` is rejected with `ValueError: mutable default ... use default_factory`,
because a class-level default is evaluated once and would be shared by every instance; the module
checks for this explicitly since the aliasing bug is otherwise silent. `field(default_factory=list)`
calls the factory per instance. `field()` also controls participation: `repr=False` hides large
payloads from logs, `compare=False` excludes a timestamp from equality, `init=False` marks a
derived field set in `__post_init__`.

### Hooks and helpers
`__post_init__` runs after the generated `__init__` and is where derived fields and cheap
invariant checks go (`if not 0 <= self.temperature <= 2: raise ValueError`). `InitVar[T]` declares
an init-only parameter that is passed to `__post_init__` but not stored. `dataclasses.replace(obj,
**changes)` returns a new instance with some fields changed, which is the idiom for "modifying" a
frozen instance. `asdict(obj)` converts recursively into dicts and lists, deep-copying along the
way, so it is convenient for logging but expensive for large nested structures; `fields(cls)`
exposes the field metadata for generic code. `match_args` (3.10) lets instances be destructured in
`match` statements positionally.

### Where it stops
A dataclass does not validate types: `GenConfig(temperature="hot")` constructs happily. It does not
parse JSON, generate schemas or coerce input. Those are Pydantic's responsibilities, and Pydantic
even offers `pydantic.dataclasses.dataclass` that adds validation to the stdlib form. The dividing
line: dataclasses for records *you* construct from already-trusted values; Pydantic for data that
arrives from outside.

## Example
A frozen, slotted config that works as a cache key, and a mutable step record with a per-instance
default list. `replace` produces a varied config without touching the original.

```python
import time
from dataclasses import dataclass, field, replace, asdict
from functools import lru_cache

@dataclass(frozen=True, slots=True)
class GenConfig:
    model: str
    temperature: float = 0.0
    max_tokens: int = 256
    stop: tuple[str, ...] = ()            # tuple, not list, so the instance stays hashable

    def __post_init__(self) -> None:
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError(f"temperature out of range: {self.temperature}")

@dataclass(slots=True)
class Step:
    tool: str
    args: dict[str, object]
    started: float = field(default_factory=time.time)
    output: str | None = field(default=None, repr=False)   # keep big payloads out of logs
    notes: list[str] = field(default_factory=list)         # a fresh list per instance

@lru_cache(maxsize=1024)
def completion(prompt: str, cfg: GenConfig) -> str:        # cfg is hashable, so it can be a key
    return f"<{cfg.model}@{cfg.temperature}> {prompt[:24]}"

base = GenConfig(model="gpt-4o-mini")
hot = replace(base, temperature=0.9)                       # new object; base unchanged
completion("Summarise the KV cache", base)
completion("Summarise the KV cache", GenConfig("gpt-4o-mini"))
print(completion.cache_info().hits)                        # 1: equal configs are the same key
try:
    base.temperature = 1.0
except Exception as e:
    print(type(e).__name__)                                # FrozenInstanceError

s1, s2 = Step("search", {"q": "a"}), Step("search", {"q": "b"})
s1.notes.append("retried"); print(s2.notes)                # []: default_factory did its job
print(s1)                                                  # Step(tool='search', args={'q': 'a'}, started=..., notes=['retried'])
print(asdict(s1)["notes"] is s1.notes)                     # False: asdict deep-copies
```

Change `stop` to `list[str]` with a `default_factory` and `lru_cache` raises `TypeError:
unhashable type`, because a frozen dataclass's hash is built from its fields and a list has none.

## Trade-offs
- You get correct, consistent dunder methods for free, but no validation; the moment input comes
  from outside the process, a Pydantic model is the right tool despite its extra weight.
- `frozen=True` gives hashability and safe sharing across threads and cache keys, but every update
  allocates a new object via `replace`, which matters only in tight loops over large records.
- `slots=True` saves memory and catches attribute typos, but instances lose `__dict__`, so
  libraries that stash attributes on objects or rely on `vars(obj)` break.
- `order=True` gives sorting for free, but it compares fields positionally in declaration order,
  which is rarely the ordering you actually mean for a domain object.
- `asdict` is convenient for logging and serialization, yet it deep-copies the whole tree; for large
  nested records a hand-written `to_dict` or `vars(obj)` is cheaper.

## Pitfalls
- Using a mutable default via `field(default=[])` or a class attribute on a non-dataclass base; the
  symptom is one instance's appended notes showing up in every other instance.
- Adding `__eq__` by hand or setting `eq=True` on a mutable class and then using instances as dict
  keys; the symptom is `TypeError: unhashable type` or, with `unsafe_hash`, items vanishing from
  sets after mutation.
- Putting a required field after a defaulted one in a subclass; the symptom is `TypeError:
  non-default argument follows default argument` at import, fixed by `kw_only=True`.
- Expecting `GenConfig(temperature="0.7")` to coerce or fail; it stores the string, and the error
  appears later as a `TypeError` in arithmetic. Validate in `__post_init__` or use Pydantic.
- Storing a list in a frozen dataclass and assuming the whole object is immutable; the reference is
  frozen but the list is not, and hashing fails or, worse, the "immutable" config changes.

## When to use
Use a dataclass for records you construct from trusted values inside your process: configs,
intermediate results, agent steps, cache keys, test fixtures. Prefer `frozen=True` for anything
shared across tasks or used as a key, and `slots=True` for anything created in large numbers
(retrieved chunks, token events). Switch to Pydantic when the data crosses a trust boundary or
needs a schema, and to `NamedTuple` only when tuple unpacking is genuinely the interface. Measure
memory with `tracemalloc` when instance counts reach the millions; slots often halve the footprint.
Day to day, dataclasses are the default container for anything with more than two fields; in
interviews, expect to explain `default_factory`, when `__hash__` is generated and why, and the line
between dataclasses and Pydantic.

## Role lens
- **Engineer:** Models generation configs and agent steps as frozen, slotted dataclasses so they
  are hashable cache keys with readable reprs in traces.
- **Architect:** Sets the rule that internal records are dataclasses and boundary types are
  Pydantic, so validation cost and schema generation stay where they belong.
- **FDE:** Turns a customer's ad-hoc dicts into dataclasses during an integration, giving their
  pipeline checked field names and reprs that make the first incident debuggable.

## Check yourself
**Q:** You want to memoize LLM calls keyed on the prompt plus a configuration object holding the
model, temperature and a list of stop sequences. The config is a mutable dataclass and `lru_cache`
raises `TypeError: unhashable type`. Explain why and how you would fix it.
**A:** A dataclass with `eq=True` (the default) and `frozen=False` sets `__hash__ = None`, because
a hashable object must not change its hash, and a mutable record can. Even with `frozen=True`, the
`list` of stop sequences is unhashable, so the generated hash, which hashes the field tuple, fails.
The fix is `@dataclass(frozen=True)` with `stop: tuple[str, ...]` instead of a list, which gives a
stable hash and lets `replace` produce variants. If the config really must be mutable, derive an
explicit immutable key (a tuple of the fields) and cache on that instead.

## Quiz
### Q: Why does `@dataclass` reject `tags: list[str] = []` with a ValueError?
- [ ] Lists cannot be dataclass fields
- [x] The default would be evaluated once and shared by every instance, so the module demands `default_factory`
- [ ] Empty lists are falsy and would break `__eq__`
- [ ] Type hints for lists require `List[str]` from `typing`
> A class-level default is a single object. Sharing it across instances is the classic aliasing bug,
> so dataclasses raise at definition time and `field(default_factory=list)` creates one per instance.

### Q: Under which setting does `@dataclass` generate a usable `__hash__`?
- [ ] `eq=True` alone
- [ ] `order=True` alone
- [x] `frozen=True` together with `eq=True`
- [ ] `slots=True` alone
> Hashing requires the hash to be stable, which a mutable object cannot guarantee; `eq=True` without
> `frozen` therefore sets `__hash__ = None`. Frozen plus eq hashes the field tuple safely.

### Q: What is the main thing a dataclass does not do that Pydantic does?
- [ ] Generate a readable `__repr__`
- [ ] Support default values
- [x] Validate and coerce field values at construction
- [ ] Support inheritance
> Dataclasses generate methods from annotations but never check them; `Config(temperature="hot")`
> succeeds. Pydantic validates, coerces and reports structured errors, which is why it owns boundaries.
