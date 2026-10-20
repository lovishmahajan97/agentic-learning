---
id: object-model-and-mutability
module: python-for-ai
name: Object model and mutability
kind: concept
difficulty: core
est_minutes: 16
related:
  - python-for-ai/dataclasses
  - python-for-ai/memory-and-performance-profiling
  - python-for-ai/race-conditions-and-synchronization-primitives
  - python-for-ai/numpy
  - python-for-ai/pytorch
  - python-for-ai/decorators
  - agent-reliability/idempotent-tool-execution
sources:
  - Official docs: The Python Language Reference, Data model
  - Official docs: copy module
---

## TL;DR
In Python a variable is a name bound to an object, never a box holding a value, and assignment never
copies. Objects are either mutable (list, dict, set, most classes, arrays and tensors) or immutable
(int, str, tuple, frozenset). Nearly every "the value changed by itself" bug in AI code is aliasing: two
names, a default argument, a class attribute or a slice sharing one mutable object. Knowing which
operations rebind a name and which mutate the object is the difference between a leak across tenants
and a correct program.

## Why it exists
A chat helper is written as `def ask(question, history=[])`. It works in the unit test, then in
production every user sees every other user's messages. A prompt template dict is `.copy()`-ed per
tenant and the `tools` list inside it still accumulates everyone's tools. An embedding matrix is
sliced for normalisation and the original rows change too. A tensor is updated with an in-place
`add_` and autograd raises on the backward pass. None of these are library bugs; they are the same
fact seen four times: Python passes and stores *references to objects*, and mutating through any
reference is visible through all of them.

The intuition most engineers bring from C-like languages, "a variable holds a value and assignment
copies it", is wrong here and nothing in the syntax warns you. `a = b` is indistinguishable from a
copy until someone mutates. The naive defence, "copy everything defensively", costs memory and time
that AI workloads cannot spare (copying a gigabyte of embeddings per request) and still fails because
shallow copies share their contents. The real defence is a correct model: know what an object is, know
which names point at it, know which operations create a new object and which change the existing one,
and prefer immutable values for anything shared. That model is also what makes concurrency with
threads and async tasks tractable, because immutable data cannot race.

## How it works
### Objects, names and identity
Every value is an object with an identity (`id()`, its address in CPython), a type and a value.
Namespaces (module globals, function locals, instance `__dict__`) are mappings from names to
objects. `x = expr` binds the name `x` to whatever object `expr` produced; `y = x` binds a second name
to the *same* object, so `x is y` is `True`. Nothing was copied, because the language is designed so
that binding is O(1) regardless of what is bound. `==` asks whether two objects have equal values;
`is` asks whether they are the same object. Use `is` only for singletons like `None`: CPython caches
small integers and interns some strings, so `is` on numbers or text gives answers that depend on
implementation details.

### Mutable versus immutable
Immutable objects (`int`, `float`, `str`, `bytes`, `tuple`, `frozenset`, `bool`, `None`) cannot
change; every operation yields a new object. `s += "x"` therefore *rebinds* `s` and any other name
still sees the old string. Mutable objects (`list`, `dict`, `set`, `bytearray`, user-defined class
instances, NumPy arrays, PyTorch tensors) change in place: `lst += [1]` calls `__iadd__`, which
extends the existing list, and every alias sees the change. The same `+=` spelling does opposite
things, which is why the distinction has to live in your head and not in the syntax. Methods that
mutate conventionally return `None` (`list.sort`, `dict.update`, `random.shuffle`) precisely so
that `x = lst.sort()` fails loudly rather than letting you believe you got a copy.

### Function arguments
Python passes object references by assignment. Inside a function, a parameter is a new local name
bound to the caller's object. Mutating that object (`items.append`) is visible to the caller;
rebinding the name (`items = []`) is not. Default argument values are evaluated *once*, at `def`
time, and stored on the function object, which is why a mutable default is shared by all calls and
`None` with an in-body `if x is None: x = []` is the idiom.

### Containers hold references
A list of lists built with `[[0] * 3] * 3` holds three references to one inner list; changing
`grid[0][0]` changes every row. Shallow copies (`list(x)`, `x[:]`, `dict.copy()`, `copy.copy`)
create a new outer container that references the *same* inner objects. `copy.deepcopy` recurses, at
O(size) cost, and cannot copy things like locks, sockets or SDK clients. Class attributes are shared
by all instances: `class Agent: history = []` is one list for every agent; the per-instance version
must be created in `__init__`.

### Hashing and equality
Dict keys and set members must be hashable, and the data model requires equal objects to hash
equally. Mutable built-ins are unhashable for that reason; a user class is hashable by identity
unless it defines `__eq__` without `__hash__`, in which case it becomes unhashable, which is the rule
`@dataclass` applies. Mutating an object after using it as a key (through a custom `__hash__`) makes
it unfindable.

### Arrays and tensors
NumPy basic slicing (`a[:, :10]`, `a[::2]`) returns a *view* sharing the buffer, so normalising the
slice normalises the original; `a.copy()` or fancy indexing returns an independent array. PyTorch
distinguishes in-place methods by a trailing underscore (`t.add_()`), and autograd refuses in-place
changes to values it still needs for the backward pass. In both libraries in-place operations exist
to avoid allocating gigabytes, so the trade is explicit: memory versus aliasing risk.

### Lifetime
CPython frees an object when its reference count drops to zero; cycles are collected by the garbage
collector later. A large tensor stays resident while any name, container, closure, cached traceback
or notebook output history references it, which is why `del x` sometimes frees nothing.

## Example
Four aliasing bugs that occur in real LLM code, each followed by the fix: a shared mutable default
argument that leaks conversation history between callers, a shallow-copied prompt template that
leaks tools between tenants, the `+=` difference between a list and a string, and nested-list
multiplication that aliases rows. Every print shows the surprising result and, where applicable, the
correct version beside it.

```python
import copy

def ask(question: str, history: list[dict] = []) -> list[dict]:   # BUG: one list for every call
    history.append({"role": "user", "content": question})
    return history

print(len(ask("a")), len(ask("b")))              # 1 2  -> the second caller sees the first turn

def ask_ok(question: str, history: list[dict] | None = None) -> list[dict]:
    history = [] if history is None else history  # fresh list per call unless one is passed
    return [*history, {"role": "user", "content": question}]

BASE = {"system": "You are a support bot.", "tools": ["search"]}

def per_tenant(tenant: str) -> dict:
    cfg = BASE.copy()                            # shallow: cfg["tools"] IS BASE["tools"]
    cfg["tools"].append(f"{tenant}_crm")         # leaks into every later tenant
    return cfg

per_tenant("acme"); print(BASE["tools"])        # ['search', 'acme_crm']

def per_tenant_ok(tenant: str) -> dict:
    cfg = copy.deepcopy(BASE)                    # or rebuild; or store BASE as tuples
    cfg["tools"] = [*cfg["tools"], f"{tenant}_crm"]
    return cfg

a = [1, 2]; b = a; c = a[:]
b.append(3)
print(a is b, a == c, c)                        # True False [1, 2]  (c is a shallow copy)
s = "x"; t = s; s += "y"                        # str is immutable: += rebinds s only
print(t)                                        # x
grid = [[0] * 2] * 2; grid[0][0] = 9            # both rows are the same inner list
print(grid, [[0] * 2 for _ in range(2)])        # [[9, 0], [9, 0]] [[0, 0], [0, 0]]
```

## Trade-offs
- Immutable values (tuples, frozen dataclasses, `MappingProxyType`) are safe to share across tenants,
  threads and tasks and usable as cache keys, but every change allocates; for hot loops over large
  state, controlled in-place mutation wins.
- In-place array and tensor operations avoid copying gigabytes, at the cost of aliasing and autograd
  hazards; when memory is not the constraint, out-of-place operations are easier to reason about.
- `deepcopy` guarantees isolation but is O(size), can be slower than reconstructing the object, and
  fails on unpicklable members such as clients and locks; rebuild from a factory instead.
- Freezing a container is shallow: a tuple of lists still has mutable lists inside, so the protection
  must be applied at every level or the invariant is only cosmetic.

## Pitfalls
- A mutable default argument; the symptom is state leaking between calls, and tests that pass in
  isolation but fail when run together.
- A mutable class attribute used as per-instance state; every `Agent` shares one `history` and the
  symptom is cross-conversation contamination.
- Shallow-copying a nested config; the symptom is tenant A's tools or headers appearing in tenant
  B's requests.
- `x = lst.sort()` or `cfg = d.update(...)`; the symptom is `AttributeError: 'NoneType' object has no
  attribute`, because mutating methods return `None` by convention.
- Mutating a list or dict while iterating over it; lists skip elements silently, dicts raise
  `RuntimeError: dictionary changed size during iteration`.
- Normalising a NumPy slice or a tensor view in place and discovering the source matrix changed;
  the symptom is retrieval scores that drift between runs.
- GPU memory that does not come back after `del`; another reference (a list, an exception traceback,
  a notebook `Out[]` entry) is still holding the tensor.
- Using `is` to compare strings or integers; it works in tests because of interning and fails on
  values built at runtime.

## When to use
This is not a tool you choose; it is the model you need every time a value is shared. Reach for
immutability by default for anything passed between layers, stored in a cache, or used by more than
one task: tuples instead of lists for fixed sequences, frozen dataclasses for configs, `None` plus
in-body construction for defaults. Reach for deliberate mutation when memory or speed demands it,
and contain it inside one function with an explicit docstring note. The measures are simple: no
mutable defaults in the codebase (linters flag them), no class-level mutable attributes, copies at
every tenant boundary. In interviews, "what does this print" questions about `+=`, default
arguments and nested list multiplication are the standard probe of this model.

## Role lens
- **Engineer:** Builds per-request message lists from scratch, keeps shared prompt templates
  immutable, and uses views and in-place tensor ops only where the memory saving is measured.
- **Architect:** Sets the rule that cross-tenant and cross-request state is immutable or copied at
  the boundary, and designs caches around hashable, frozen keys.
- **FDE:** Diagnoses "customer B saw customer A's context" incidents by tracing which shared object
  was mutated, usually a template or default argument in a customisation layer.

## Check yourself
**Q:** A multi-tenant service builds each request's config with `cfg = DEFAULTS.copy()` then
`cfg["headers"]["X-Tenant"] = tenant`. Under load, requests intermittently carry the wrong tenant
header. Explain the mechanism and give two fixes with their costs.
**A:** `dict.copy()` is shallow: the new `cfg` is a different outer dict, but `cfg["headers"]` is the
very same inner dict object that `DEFAULTS["headers"]` references, so every request writes its
tenant into one shared dict. With concurrency, request B's write lands between A's write and A's
HTTP call, so A sends B's header. Fix one: `copy.deepcopy(DEFAULTS)` per request, which isolates
everything at O(size) cost per call and breaks if DEFAULTS ever holds a client or lock. Fix two,
usually better: make DEFAULTS immutable (tuples, `MappingProxyType`, a frozen dataclass) and build the
per-request headers as a new dict `{**DEFAULTS["headers"], "X-Tenant": tenant}`, which costs only the
small structure that actually changes and makes the sharing impossible rather than merely avoided.

## Quiz
### Q: What does `def f(x, acc=[]): acc.append(x); return acc` return on the second call `f(2)` after `f(1)`?
- [ ] `[2]`, because the default is re-evaluated on every call
- [x] `[1, 2]`, because the default list was created once at definition time and is shared
- [ ] A `TypeError`, because mutable defaults are rejected
- [ ] `[1, 2]` only if both calls happen in the same thread
> Default values are evaluated once when `def` executes and stored on the function object. Every call
> that omits `acc` mutates that one list, which is why `None` plus in-body creation is the idiom.

### Q: Why does `s += "a"` leave other names bound to the old string unchanged, while `lst += [1]` changes every alias of the list?
- [ ] Strings are copied on assignment and lists are not
- [ ] The `+=` operator is only defined for mutable types
- [x] `str` is immutable so `+=` rebinds the name to a new object, whereas `list.__iadd__` extends the existing object in place
- [ ] Lists are stored by reference and strings by value in the interpreter
> Same spelling, different semantics: immutable types have no in-place operator, so a new object is
> created and only the assigned name moves. Mutable types implement in-place augmentation and all
> references observe it.

### Q: After `row = emb[0]; row /= row.sum()` with a NumPy array `emb`, what happened to `emb`?
- [ ] Nothing; indexing returns an independent copy of the row
- [x] Its first row was modified, because basic indexing returns a view sharing the same buffer
- [ ] It was converted to a float array but its values are unchanged
- [ ] A `ValueError` was raised because views cannot be modified in place
> Basic slicing and indexing in NumPy return views, so an in-place operation on the view writes into
> the original buffer. `emb[0].copy()` or an out-of-place expression avoids the side effect.
