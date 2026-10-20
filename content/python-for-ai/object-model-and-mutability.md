---
id: object-model-and-mutability
module: python-for-ai
name: Object model and mutability
kind: concept
difficulty: core
est_minutes: 15
related:
  - python-for-ai/dataclasses
  - python-for-ai/race-conditions-and-synchronization-primitives
  - python-for-ai/memory-and-performance-profiling
  - python-for-ai/numpy
  - python-for-ai/pytorch
  - agent-reliability/state-persistence-and-conversation-recovery
sources:
  - Official docs: Python Language Reference, Data model
  - Batchelder, 2015 — Facts and Myths about Python Names and Values (PyCon talk)
---

## TL;DR
In Python every value is an object with an identity, and names, attributes, container slots and
function parameters are all just references to objects. Assignment never copies. Mutable objects
(lists, dicts, sets, most instances) can be changed through any reference; immutable ones (ints,
strings, tuples) cannot, so "changing" them makes a new object. Most mysterious AI-pipeline bugs,
from conversation histories bleeding between requests to tensors changing under you, are aliasing
bugs that follow directly from these rules.

## Why it exists
The object model is not a feature you opt into; it is the semantics of the language, and the reason
to study it is that intuitions from other languages are wrong in ways that produce silent
corruption. An engineer coming from C expects assignment to copy a struct. One coming from
JavaScript expects objects to behave like Python's but is surprised that a default argument is
evaluated once. One coming from Rust expects the compiler to stop two names from mutating the same
value. Python does none of these: it hands out references freely and trusts you.

That trust is cheap and fast, which is why the model was chosen: no copying on assignment, no
ownership tracking, one allocation per object. The price is that the programmer must know, at
every mutation, who else holds a reference. In AI code the stakes are concrete. An agent loop that
appends to a `messages` list it received as an argument has just modified the caller's
conversation. A `messages=[]` default shared across requests leaks one user's chat into another's.
A prompt template stored as a module-level dict and "customized" per request by assignment mutates
it for every subsequent request. A NumPy slice or a PyTorch `view` shares memory with its source,
so normalizing the slice in place normalizes the original. Each of these produces no error, only
wrong outputs that appear under concurrency or after the second call.

The naive defence, "copy everything", costs memory and time and still misses nested sharing, since
a shallow copy of a list of dicts shares the dicts. Understanding the model lets you copy exactly
where needed and make the rest immutable.

## How it works
### Objects, names and references
Every object has an identity (`id(x)`, its address in CPython), a type and a value. A name is an
entry in a namespace dict that refers to an object; `a = b` makes `a` refer to the same object `b`
refers to, and nothing is duplicated. `is` tests identity (same object); `==` calls `__eq__` and
tests value. Containers hold references too: a list of three dicts is a list of three pointers,
which is *why* `copy.copy(lst)` yields a new list whose elements are the same dicts (shallow), and
`copy.deepcopy` must recurse to produce independent elements.

### Mutable versus immutable
Immutable types (`int`, `float`, `str`, `bytes`, `tuple`, `frozenset`, `bool`, `None`) have no
operation that changes them in place; `s += "x"` evaluates `s + "x"` into a new string and rebinds
`s`, leaving every other reference to the old string untouched. Mutable types (`list`, `dict`,
`set`, `bytearray`, user-defined instances by default) can be changed in place, and every reference
sees the change. The same operator differs by type: `lst += [x]` calls `list.__iadd__`, which
extends in place, while `tup += (x,)` has no `__iadd__` and rebinds. A tuple is immutable in its
references only; a tuple containing a list can still have that list mutated, which is also why such
a tuple is unhashable.

### Hashability
Dict keys and set members must be hashable: `__hash__` must return a value that never changes
while the object is alive, and must agree with `__eq__`. Mutable built-ins set `__hash__ = None`
*because* a key whose hash changed after insertion could never be found again. A user class is
hashable by identity by default; defining `__eq__` without `__hash__` removes hashability, since
value-equal objects would otherwise hash differently. Dataclasses and Pydantic encode these rules
in their `frozen` options.

### Function calls and defaults
Arguments are passed by assignment: the parameter name is bound to the caller's object. Mutating
the object inside the function is visible to the caller; rebinding the parameter is not. Default
values are evaluated once, when `def` runs, and stored on the function object, so a mutable default
is one object shared by every call that omits the argument. The idiom `param=None` followed by
`param = [] if param is None else param` exists purely because of this evaluation rule.

### Class attributes and instance attributes
Attributes assigned in the class body live on the class and are shared by all instances. Reading
`self.cache` finds the class attribute if the instance has none; `self.cache["k"] = v` then mutates
the shared dict, while `self.cache = {}` creates an instance attribute that shadows it. Instance
attributes live in the instance's `__dict__` unless the class declares `__slots__`, which stores
them in fixed slots, saving memory and forbidding new attribute names.

### Identity caching and lifetime
CPython caches small integers and interns many strings, so `a is b` may be `True` for equal ints by
accident of implementation. Use `is` only for singletons (`None`, `True`, `False`, sentinel objects)
and `==` for values. Objects live while any reference exists: CPython frees them by reference
counting immediately when the count hits zero, plus a cycle collector for reference cycles. `del
name` removes one reference; it does not destroy the object if another name, a container, a closure
or an exception traceback still points to it, which is how "deleted" tensors keep GPU memory alive.

## Example
The shared-default bug, aliasing through a shallow copy, and the `+=` asymmetry, each shown with the
output that reveals it.

```python
import copy

SYSTEM = {"role": "system", "content": "You are terse."}

def run_agent(user_text: str, history: list[dict] = []) -> list[dict]:   # bug: one default list
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": f"echo: {user_text}"})
    return history

a = run_agent("hi"); b = run_agent("again")
print(len(a), len(b), a is b)        # 4 4 True: the second caller sees the first caller's chat

def run_agent_ok(user_text: str, history: list[dict] | None = None) -> list[dict]:
    history = [] if history is None else list(history)   # new list; caller's list untouched
    history.append({"role": "user", "content": user_text})
    return history

base = [SYSTEM]
h1 = run_agent_ok("hi", base)
print(len(base), len(h1))            # 1 2: the outer list was not modified...
h1[0]["content"] = "You are verbose."
print(SYSTEM["content"])             # 'You are verbose.': ...but the inner dict is shared (shallow copy)

h2 = copy.deepcopy(base)
h2[0]["content"] = "something else"
print(SYSTEM["content"])             # still 'You are verbose.': deepcopy broke the alias

x = [1, 2]; y = x; y += [3]; print(x)      # [1, 2, 3]: += on a list mutates the shared object
t = (1, 2); u = t; u += (3,); print(t)     # (1, 2): += on a tuple rebinds u only

class Agent:
    tools: dict[str, str] = {}             # class attribute, shared by every Agent
    def register(self, name: str) -> None:
        self.tools[name] = "registered"    # mutates the shared dict
Agent().register("search"); print(Agent().tools)   # {'search': 'registered'} on a brand-new instance
```

Every surprising line prints without an exception. That is the point: the object model never warns
you; it just does what the references say.

## Trade-offs
- Reference semantics make assignment and argument passing free, but every mutation must consider
  who else holds the object; the cost flips to the copying side only when records are small and
  shared widely, where defensive copies are cheap insurance.
- Immutable records (frozen dataclasses, tuples, strings) are safe to share across tasks and usable
  as keys, but every "update" allocates a new object, which matters in tight loops over large data.
- Deep copies guarantee independence but walk the whole object graph; for a conversation of a few
  hundred messages this is negligible, for a multi-gigabyte tensor it is a disaster, so copy at the
  smallest boundary that needs it.
- Class-level mutable attributes give a cheap shared cache or registry, but they are process-global
  state that leaks between requests and tests; move them to instances or explicit singletons when
  isolation matters.
- `__slots__` cuts per-instance memory and blocks attribute typos, but removes `__dict__`, which
  breaks libraries that attach attributes dynamically or pickle by `__dict__`.

## Pitfalls
- A mutable default argument (`messages=[]`, `cache={}`); the symptom is state from an earlier call
  appearing in a later one, often only visible under concurrency or in the second test.
- Appending to a `messages` list received as a parameter; the symptom is the caller's conversation
  growing with every agent invocation and prompts getting longer until the context overflows.
- Shallow-copying a list of dicts and editing an inner dict; the symptom is a change in one request
  showing up in another request's system prompt or tool definition.
- Comparing with `is` for values (`if x is 5`, `if s is "stop"`); it may work for small ints or
  interned strings and then fail in production, and recent Pythons emit a `SyntaxWarning` for it.
- Normalizing a NumPy slice or a PyTorch view in place and corrupting the original array or tensor;
  the symptom is training data or embeddings that change after the first batch.
- Holding a reference to an exception or a traceback in a long-lived structure, so every local in
  that frame, including large tensors, stays alive; the symptom is memory that never returns.

## When to use
You do not choose the object model; you choose how to live with it. Default to immutability for
anything shared: frozen dataclasses for configs, tuples for sequences that do not change, strings
for prompts. Copy at boundaries, not everywhere: a function that must not alter its input makes
one copy on entry and mutates that. Treat module-level and class-level mutable state as a bug
unless it is an explicit, documented cache with a lock. When debugging "the data changed and nobody
changed it", reach for `id()` and `is` to find the shared object, and `copy.deepcopy` or an
immutable type to break the alias. Interviews probe this with the mutable-default question and the
`+=` asymmetry; the expected depth is explaining them from "assignment binds a name to an object"
rather than as isolated trivia.

## Role lens
- **Engineer:** Never mutates a caller's message list, uses `default_factory` and copies on entry,
  and knows a PyTorch view shares storage with its source before writing in-place ops.
- **Architect:** Specifies that conversation state and shared configs are immutable values passed
  between components, so concurrency and caching never depend on who mutates what.
- **FDE:** Diagnoses a customer's "responses contain another user's data" report by tracing the
  shared mutable default or module-level dict that aliases state across their requests.

## Check yourself
**Q:** A FastAPI service builds a prompt by taking a module-level `BASE_MESSAGES` list, appending
the user's turn, and sending it to the model. Under load, users occasionally receive answers that
reference other users' questions. No locks, no threads in your code. Explain the bug and the fix.
**A:** `BASE_MESSAGES` is one object. Each request appends to it, so the list grows and every
subsequent request sends a prompt containing earlier users' turns; under concurrent requests the
interleaving makes it look random. There is no exception because appending to a shared list is
perfectly legal. The fix is to never mutate the shared object: build a fresh list per request
(`messages = [*BASE_MESSAGES, user_turn]`), and make the base immutable (a tuple of frozen message
objects) so an accidental append raises. The general rule is that module-level mutable state is
process-wide state and must be treated as such.

## Quiz
### Q: What does `b = a` do when `a` refers to a list?
- [ ] Creates a shallow copy of the list and binds `b` to it
- [ ] Creates a deep copy of the list and binds `b` to it
- [x] Binds `b` to the same list object; both names refer to one object
- [ ] Creates an immutable view of the list
> Assignment binds a name to an object and never copies. Any mutation through `b` is visible through
> `a`, which is the root of most aliasing bugs. Copies must be requested explicitly.

### Q: Why does a mutable default argument retain changes between calls?
- [ ] Python caches function results when arguments are omitted
- [x] The default expression is evaluated once at `def` time and the same object is reused for every call
- [ ] Lists are passed by reference but ints are passed by value
- [ ] The function's closure captures the default
> Defaults live on the function object and are evaluated once. Each call that omits the argument
> binds the parameter to that same stored object, so mutations accumulate.

### Q: After `x = [1]; y = x; y += [2]` versus `s = "a"; t = s; t += "b"`, what are `x` and `s`?
- [ ] `[1]` and `"ab"`
- [x] `[1, 2]` and `"a"`
- [ ] `[1, 2]` and `"ab"`
- [ ] `[1]` and `"a"`
> `list.__iadd__` mutates in place, so `x` sees the change. Strings are immutable: `+=` builds a new
> string and rebinds only `t`, leaving `s` as `"a"`.
