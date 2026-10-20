---
id: dataclasses
module: python-for-ai
name: Dataclasses
kind: concept
difficulty: intro
est_minutes: 13
related:
  - python-for-ai/type-hints
  - python-for-ai/pydantic
  - python-for-ai/typeddict-literal-enum
  - python-for-ai/object-model-and-mutability
  - python-for-ai/protocols-generics-structural-typing
sources:
  - Smith, 2017 — PEP 557: Data Classes
  - Official docs: dataclasses module
---

## TL;DR
`@dataclass` reads a class's type annotations and generates `__init__`, `__repr__`, `__eq__` and
optionally ordering, hashing and `__slots__` from them, so a record type is declared once instead of
written four times. It exists because AI code is full of small value objects (configs, chunks, run
results, trace events) and hand-written or dict-based versions drift and hide typos. Dataclasses add
structure, not validation: for untrusted input, pair them with Pydantic.

## Why it exists
A generation config has a model name, a temperature and a token limit. The options for carrying it
around are all bad in a specific way. A tuple is positional, so `cfg[1]` means nothing to a reader and
swapping two fields is invisible. A dict is readable but `cfg["temprature"]` is a `KeyError` at runtime
and the checker cannot help. A `namedtuple` is immutable and iterable, so `for x in cfg` silently
walks its fields and `cfg == ("m", 0.0, 512)` is `True` against an unrelated tuple. A hand-written
class is correct on the day it is written and then `__repr__` forgets the field you added, `__eq__`
compares three of four fields, and the constructor's parameter order no longer matches the docstring.

The underlying problem is duplication: the field list is the single fact, and a class expresses it
four or five times. `@dataclass` makes the annotations the only statement of that fact and derives
everything else from them *because* generated code cannot drift. It arrived in Python 3.7 after the
`attrs` library had demonstrated the design for years, and it deliberately stopped short of
validation and serialisation: those belong to libraries like Pydantic, which in fact offers its own
`pydantic.dataclasses.dataclass` that adds validation to the same declaration style.

## How it works
### What the decorator generates
When `@dataclass` runs, it walks `cls.__annotations__` in definition order, turns each annotated
name into a `Field` (default value, `default_factory`, and flags such as `init`, `repr`, `compare`,
`hash`, `kw_only`), and then synthesises methods by building their source text and `exec`-ing it once
at class creation. `__init__` takes the fields as parameters in order; `__repr__` prints
`ClassName(field=value, ...)`; `__eq__` compares the tuple of fields and only against the same class.
`order=True` adds `__lt__` and friends using the same tuple, which is why field order determines
sort order.

### Hashing rules and why
With the default `eq=True, frozen=False`, the decorator sets `__hash__ = None`, making instances
unhashable. The reason is an invariant of Python's data model: objects that compare equal must hash
equal, and a mutable object whose fields can change after insertion into a set would corrupt the
set. `frozen=True` makes the instance immutable by overriding `__setattr__` to raise
`FrozenInstanceError`, and then a `__hash__` over the fields is safe, so frozen dataclasses can be
dict keys and cache keys. `unsafe_hash=True` forces a hash on a mutable class and is named that way
for a reason.

### Defaults and the mutable-default trap
A default is evaluated once at class creation and shared by every instance, so `meta: dict = {}`
would give all instances the *same* dict. The decorator refuses such defaults with
`ValueError: mutable default ... use default_factory`, and `field(default_factory=dict)` calls the
factory per instance instead. Fields with defaults must come after fields without, because they map
onto positional parameters; `kw_only=True` (3.10) lifts that restriction by making fields
keyword-only, which also makes inheritance with defaults workable.

### Post-init, derived fields and frozen
`__post_init__` runs after the generated `__init__` and is the place for validation and derived
values. On a frozen class it must use `object.__setattr__(self, "name", value)` to set anything,
because the normal path raises. `InitVar[T]` declares a constructor-only parameter that is passed to
`__post_init__` and not stored. `field(init=False)` declares a stored field the constructor does not
accept.

### Helpers
`dataclasses.replace(obj, **changes)` returns a new instance with some fields changed, which is the
idiom for "modify" a frozen config. `asdict` and `astuple` convert recursively and deep-copy
non-dataclass values, so they are convenient for logging and slow for large nested structures.
`fields(cls)` lists the `Field` objects for generic code. `slots=True` (3.10) generates `__slots__`,
removing the per-instance `__dict__`: smaller objects and faster attribute access, at the price of no
dynamic attributes. `match_args` (on by default since 3.10) lets `case Chunk(doc_id, text):` pattern
match positionally.

### What it does not do
There is no type checking at runtime: `Chunk(doc_id=123, text=None)` is accepted. Equality, repr and
hashing are structural conveniences; correctness of the values is still your job or Pydantic's.

## Example
A frozen, slotted config used as a cache key, with `__post_init__` validation and `replace` for
variants, and a mutable `Chunk` whose equality ignores the retrieval score.

```python
from dataclasses import FrozenInstanceError, asdict, dataclass, field, replace

@dataclass(frozen=True, slots=True)
class GenConfig:
    model: str
    temperature: float = 0.0
    max_tokens: int = 512

    def __post_init__(self) -> None:                 # validation hook; runs after generated __init__
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError(f"temperature out of range: {self.temperature}")

@dataclass
class Chunk:
    doc_id: str
    text: str
    meta: dict[str, str] = field(default_factory=dict)   # `= {}` is rejected at class creation
    score: float = field(default=0.0, compare=False)     # equality ignores the retrieval score

base = GenConfig("small-fast-model")
short = replace(base, max_tokens=64)                     # new object; base is untouched
cache: dict[GenConfig, str] = {base: "cached answer"}    # frozen => hashable => usable as a key
try:
    base.temperature = 0.5
except FrozenInstanceError as e:
    print("immutable:", e)

a = Chunk("d1", "refunds within 30 days", score=0.91)
b = Chunk("d1", "refunds within 30 days", score=0.12)
print(a == b, short in cache)                            # True False
print(asdict(short))     # {'model': 'small-fast-model', 'temperature': 0.0, 'max_tokens': 64}
try:
    GenConfig("x", temperature=3.0)
except ValueError as e:
    print(e)                                             # temperature out of range: 3.0
# {a, b}  -> TypeError: unhashable type: 'Chunk'   (eq=True without frozen sets __hash__ = None)
```

## Trade-offs
- Generated methods never drift from the field list, but they are generic: a custom `__eq__` that
  compares case-insensitively means writing it yourself and passing `eq=False`.
- `frozen=True` buys hashability and safe sharing across threads and tasks, yet every "change" is a
  `replace` that allocates; for objects mutated in tight loops, a mutable dataclass is the right call.
- `slots=True` cuts memory and speeds attribute access, which matters for millions of chunk objects,
  but removes dynamic attributes and `__dict__`, which some serialisers and mocking tools rely on.
- Compared with Pydantic, dataclasses are lighter and have no dependencies, but they validate nothing;
  the moment input is untrusted the saved cost is paid back in debugging.

## Pitfalls
- Writing `tags: list[str] = []` and getting `ValueError: mutable default` at import; or, with a
  non-container mutable default the decorator cannot detect, instances silently sharing state.
- Putting instances of a default (non-frozen) dataclass into a set or dict key and hitting
  `TypeError: unhashable type`; either freeze it or decide explicitly what identity means.
- Assuming `frozen=True` protects nested data; the `meta` dict inside a frozen chunk is still mutable,
  and the symptom is a "constant" config whose list grew.
- Adding a field without a default to a subclass of a dataclass that has defaults and getting
  `TypeError: non-default argument follows default argument`; use `kw_only=True`.
- Calling `asdict` on objects holding large arrays or tensors for logging; it deep-copies them, and
  the symptom is a logging line that takes seconds and doubles memory.
- Treating a dataclass as validated because it has types; `GenConfig(model=None)` constructs fine and
  fails later inside the SDK call with an unhelpful error.

## When to use
Use a dataclass for in-process value objects you construct yourself: configs, chunk and span records,
evaluation rows, intermediate pipeline results, and anything you want a readable `repr` and sane
equality for. Make it frozen when it is shared, cached or used as a key. Reach for Pydantic instead
when the data arrives from outside (HTTP, LLM output, files), and for a `TypedDict` when the value
must stay a dict for an SDK. In day-to-day work the tell is a function with eight keyword arguments
that travel together; in interviews, the questions are why mutable defaults are rejected and why
`eq=True` without `frozen` makes instances unhashable, both of which come down to shared state and
the hash/equality invariant.

## Role lens
- **Engineer:** Declares configs and records as dataclasses, freezes the shared ones, and uses
  `replace` to derive per-experiment variants without copy-paste.
- **Architect:** Sets the convention for which types are plain dataclasses (internal) versus Pydantic
  models (boundaries) so teams do not validate twice or not at all.
- **FDE:** Captures a customer's run parameters in a frozen dataclass that doubles as the cache and
  audit key, so identical requests are provably identical.

## Check yourself
**Q:** A teammate proposes caching LLM responses in a dict keyed by a `RequestConfig` dataclass and is
surprised by `TypeError: unhashable type`. Explain the error and the correct fix, including a case
where the obvious fix is still wrong.
**A:** With defaults (`eq=True`, not frozen) the decorator sets `__hash__` to `None`, because a
mutable object that is equal to another today could be mutated after insertion and break the
dict's invariant that equal keys hash equally. The fix is `@dataclass(frozen=True)`, which blocks
attribute assignment and generates a field-based hash. It is still wrong if any field holds a mutable
value such as a `list` of stop sequences: the instance then raises `TypeError` on hashing because the
list is unhashable, and even a tuple would not stop someone mutating a nested dict. Convert such
fields to tuples or frozensets, and make sure every field that should not affect caching (timestamps,
trace ids) is declared `compare=False` so it is excluded from both equality and the hash.

## Quiz
### Q: Why does `@dataclass` reject `items: list[str] = []` instead of just allowing it?
- [ ] Lists cannot be type-annotated as defaults in Python
- [x] The default is evaluated once and would be shared by all instances, so a per-instance factory is required
- [ ] Dataclass defaults must be immutable so `__eq__` can compare them
- [ ] The decorator cannot generate a `__repr__` for list defaults
> Class-body defaults are single objects. Sharing one list across instances is the classic mutable
> default bug, so the decorator refuses and `field(default_factory=list)` creates a fresh one each time.

### Q: Which configuration makes dataclass instances usable as dictionary keys?
- [ ] `order=True`, because keys must be sortable
- [ ] `slots=True`, because it removes the mutable `__dict__`
- [x] `frozen=True` (with the default `eq=True`), which generates a field-based `__hash__`
- [ ] `init=False`, because keys must not be constructed with arguments
> Hashability requires that equal objects stay equal, which only an immutable object guarantees.
> Freezing enables the generated hash; slots and ordering do not affect hashing at all.

### Q: What does a dataclass NOT do that Pydantic's `BaseModel` does?
- [ ] Generate `__init__` from annotations
- [ ] Support default values and `__eq__`
- [x] Validate and coerce field values at construction time
- [ ] Allow frozen, immutable instances
> Dataclasses trust the caller: `Chunk(doc_id=123)` is accepted. Pydantic validates against the
> annotations and reports structured errors, which is why it belongs at trust boundaries and
> dataclasses inside them.
