---
id: protocols-generics-structural-typing
module: python-for-ai
name: Protocols, generics and structural typing
kind: concept
difficulty: core
est_minutes: 16
related:
  - python-for-ai/type-hints
  - python-for-ai/dependency-injection-and-interface-boundaries
  - python-for-ai/decorators
  - python-for-ai/mypy
  - python-for-ai/test-doubles-mocks-fixtures
  - agent-reliability/tool-adapters-service-interfaces
  - node-ts-for-ai/typescript-strict-mode-and-advanced-types
sources:
  - Levkivskyi, Lehtosalo, Langa, 2017 — PEP 544: Protocols: Structural subtyping (static duck typing)
  - Traut, 2022 — PEP 695: Type Parameter Syntax
  - Official docs: typing module
---

## TL;DR
A Protocol says "anything with these methods is acceptable", so a type checker can verify duck
typing without inheritance. Generics let you write a function or class once and have the checker
track the element type through it, so `first(xs: list[Message])` is known to return a `Message`.
Together they are how you type the swappable parts of an AI system (embedders, LLM clients, vector
stores, retry decorators) without forcing third-party classes into your class hierarchy.

## Why it exists
Python code is written against behaviour, not names. "Anything with an `embed(texts)` method" is a
complete contract, and half the value of the language is that an OpenAI client, a
sentence-transformers model and a test stub can all satisfy it without agreeing on a base class.
The original type-hint design was nominal: a value was compatible with `Embedder` only if its class
inherited from `Embedder`. That forces one of two bad outcomes. Either you write adapter classes
whose only purpose is to inherit from your abstract base so the checker is satisfied, or you type
the parameter as `Any` and lose checking exactly where it matters most, at the seam between your
code and a vendor's.

Generics solve a different hole. Without them, a utility like `first(xs)` must be typed
`list[Any] -> Any`, and the element type is lost the moment it passes through. The checker then
cannot tell you that `first(messages).content` is wrong when `messages` holds tool-call records.
Writing one copy of the function per element type is obviously absurd. What is needed is a
placeholder type that is bound per call: "whatever the elements are, that is what comes out".

Protocols (PEP 544) and type variables give the checker both capabilities while keeping runtime
behaviour unchanged. The design goal was explicit: make the existing duck-typed idioms of the
standard library (`Iterable`, `Sized`, file-like objects) expressible, rather than forcing Python
into a Java-shaped type system.

## How it works
### Structural versus nominal compatibility
Nominal typing asks "is this class a declared subclass?". Structural typing asks "does this object
have the required members with compatible types?". `collections.abc.Iterable` is already treated
structurally by checkers: any class with `__iter__` is an `Iterable`, no registration needed. A
`Protocol` class lets you define your own such interfaces:

```python
from typing import Protocol
class Embedder(Protocol):
    dim: int
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
```

Any class with an `int` attribute `dim` and a compatible `embed` method is an `Embedder` to the
checker. Compatibility is checked member by member, including parameter names, because a caller
might pass `texts=` by keyword; declare parameters positional-only with `/` when names should not
matter. The checker verifies the match at the point where an object is used as an `Embedder`, which
is *why* errors show up at call sites rather than at the implementing class: the implementation
never declared any intent. Adding `impl: Embedder = MyEmbedder()` in a test moves the error earlier.

`@runtime_checkable` additionally makes `isinstance(obj, Embedder)` work. It only checks that the
named attributes exist, not their signatures or types, because a full structural check at runtime
would be slow and ambiguous. Treat it as a cheap plugin-discovery check, not validation.

Compare this with an abstract base class (`abc.ABC` plus `@abstractmethod`): the ABC is nominal,
enforces at instantiation time (a subclass missing a method cannot be constructed), and can carry
shared default implementations. Protocols enforce only statically and carry no behaviour. Use the
ABC when you own every implementation; use the Protocol when implementations come from outside.

### Generics: type variables
A `TypeVar` is a placeholder the checker binds per use. `def first[T](xs: Sequence[T]) -> T` (PEP
695 syntax, Python 3.12+) or the older `T = TypeVar("T")` plus `def first(xs: Sequence[T]) -> T`
tells the checker that the return type equals the element type. A generic class,
`class Index[T]:` or `class Index(Generic[T]):`, carries the binding across its methods, so
`Index[Document].nearest(...)` returns a `Document`.

Two refinements exist because unconstrained `T` is often too loose. A bound,
`T = TypeVar("T", bound=BaseModel)`, says T can be any subclass of `BaseModel`, so the checker
allows `.model_dump()` on it. A constrained TypeVar, `TypeVar("N", int, float)`, says T is exactly
one of those types, which is how `sum`-like helpers avoid accepting strings.

### Variance, or why `list[Dog]` is not a `list[Animal]`
A function typed `def rename(xs: list[Animal]) -> None` may legally `xs.append(Cat())`. If the
checker accepted a `list[Dog]` there, the caller's list would now contain a `Cat`. So mutable
containers are *invariant*: `list[Dog]` is compatible only with `list[Dog]`. Read-only views are
*covariant*: a `Sequence[Dog]` is a `Sequence[Animal]` because nothing can be inserted. Function
parameters are *contravariant*: a callback accepting `Animal` can be used where one accepting `Dog`
is required. This is the single most common "but it obviously should work" checker error, and the
fix is almost always to accept `Sequence[T]` or `Iterable[T]` for inputs. When you declare your own
generic classes with the old syntax you must state variance (`TypeVar("T_co", covariant=True)`);
the PEP 695 syntax infers it from how the parameter is used.

### ParamSpec: typing decorators
Every retry, tracing and caching decorator in an AI codebase wraps arbitrary functions. Typed as
`Callable[..., Any]`, the decorator erases the wrapped function's signature and every call through
it becomes unchecked. `ParamSpec` captures "whatever parameters the wrapped function has" so that
`Callable[P, R] -> Callable[P, R]` preserves argument and return types through the wrapper. `Self`
(3.11) solves the related problem of fluent builders and `copy`-style methods returning the subclass
type rather than the base.

## Example
A structural `Embedder` satisfied by two unrelated classes, a generic index that remembers what it
stores, and a signature-preserving decorator. On Python 3.12+ the `Generic[T]` base can be replaced
by `class Index[T]:`.

```python
import functools
from collections.abc import Callable, Sequence
from typing import Generic, ParamSpec, Protocol, TypeVar, runtime_checkable

@runtime_checkable
class Embedder(Protocol):
    dim: int
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

class HashEmbedder:                       # no inheritance from Embedder anywhere
    dim = 8
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float((hash(t) >> i) & 1) for i in range(self.dim)] for t in texts]

class OpenAIEmbedder:                     # wraps a vendor SDK; also structurally an Embedder
    dim = 1536
    def __init__(self, client) -> None: self.client = client
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        r = self.client.embeddings.create(model="text-embedding-3-small", input=list(texts))
        return [d.embedding for d in r.data]

T = TypeVar("T")
class Index(Generic[T]):
    def __init__(self, embedder: Embedder) -> None:
        self.embedder, self.items, self.vecs = embedder, [], []
    def add(self, item: T, text: str) -> None:
        self.items.append(item); self.vecs.append(self.embedder.embed([text])[0])
    def nearest(self, text: str) -> T:              # the checker knows this is a T
        q = self.embedder.embed([text])[0]
        dot = lambda v: sum(a * b for a, b in zip(q, v))
        return self.items[max(range(len(self.vecs)), key=lambda i: dot(self.vecs[i]))]

P = ParamSpec("P"); R = TypeVar("R")
def logged(fn: Callable[P, R]) -> Callable[P, R]:   # keeps fn's exact signature
    @functools.wraps(fn)
    def inner(*args: P.args, **kwargs: P.kwargs) -> R:
        print("calling", fn.__name__); return fn(*args, **kwargs)
    return inner

idx: Index[int] = Index(HashEmbedder())
idx.add(42, "the answer"); idx.add(7, "a prime")
print(idx.nearest("answer"))                       # 42 (int, per the checker)
print(isinstance(HashEmbedder(), Embedder))        # True: attribute presence only
```

Change `nearest` to return `self.vecs[0]` and the checker reports `list[float]` is not `T`; pass
`idx: Index[int] = Index(object())` and it reports `object` is missing `embed`.

## Trade-offs
- Protocols decouple your contracts from vendor classes, but mismatch errors surface at call sites
  instead of at the implementation, so without an explicit `impl: Proto = Impl()` check in tests a
  broken implementation is only reported where it is first used.
- `@runtime_checkable` gives a cheap plugin check, but it verifies attribute names only, so using
  it as validation of untrusted objects becomes harmful the moment signatures matter.
- Generics make element types flow through containers and pipelines, but nested type variables and
  variance annotations raise the reading cost; for a small private helper `list[Any]` can be the
  honest choice.
- An ABC enforces at instantiation and can hold shared defaults, which a Protocol cannot; when you
  own all implementations and want shared behaviour, the ABC wins despite being nominal.
- `ParamSpec` preserves signatures through decorators at the cost of more boilerplate per decorator;
  skipping it turns every decorated function into `Callable[..., Any]` and silently disables checks.

## Pitfalls
- Declaring an input as `list[Message]` and getting "incompatible type list[ToolMessage]" from
  callers; the cause is invariance of mutable containers, the fix is `Sequence[Message]`.
- Implementing a Protocol method with a different parameter name (`def embed(self, docs)`) and
  seeing "incompatible with protocol" errors; parameter names are part of the signature unless
  declared positional-only.
- Relying on `isinstance(x, SomeProtocol)` to confirm a vendor object is safe to call; the check
  passes with the wrong signature and the failure appears later as a `TypeError` inside the call.
- Using a bare `TypeVar` in a signature where it appears only once (`def f(x: T) -> None`); it
  binds to nothing and checkers warn that it is meaningless, usually a sign `object` was intended.
- Writing a decorator typed `Callable[..., Any]` and wondering why mypy never catches wrong
  arguments to decorated LLM-call functions; the signature was erased.

## When to use
Reach for a Protocol whenever the *consumer* defines the interface and implementations come from
elsewhere: an `LLMClient` your agent loop calls, an `Embedder` your retriever uses, a `Store` your
memory layer writes to. It makes test doubles trivial, because a stub only needs the methods, and it
keeps vendor SDKs out of your type signatures. Use an ABC instead when you ship a base class with
shared behaviour that your own subclasses extend. Use generics for anything container-shaped whose
element type varies: `Cache[K, V]`, `Pipeline[In, Out]`, `Result[T]`. Use `ParamSpec` on every
decorator in a typed codebase. Measure success by how few `Any` and adapter-only classes remain. In
interviews, expect to explain structural versus nominal typing, why `list` is invariant and what
`ParamSpec` fixes.

## Role lens
- **Engineer:** Types the LLM client, embedder and store as Protocols so a fake can replace each in
  tests without monkeypatching vendor modules.
- **Architect:** Defines the handful of Protocols that form the boundary between agent core and
  providers, so swapping a vendor is an implementation change, not a refactor.
- **FDE:** Fits a customer's existing classes (their search service, their auth client) into the
  product's interfaces by matching method shapes instead of asking them to subclass anything.

## Check yourself
**Q:** Your team has a `Retriever` abstract base class with three in-house subclasses. A customer
wants to plug in their vendor's search client, which already exposes a compatible `search(query,
k)` method. A teammate proposes writing an adapter subclass. Is there a better option, and what do
you give up?
**A:** Define `Retriever` as a `Protocol` with the `search` signature; the vendor class then
satisfies it structurally with no wrapper, and the checker verifies the signature match at the
call site. What you give up is the ABC's instantiation-time enforcement and any shared default
methods, and you must make sure parameter names match or declare them positional-only. A common
compromise is both: a Protocol for consumers to depend on, and an ABC that your own
implementations inherit for shared behaviour, which still satisfies the Protocol.

## Quiz
### Q: What makes a class compatible with a `Protocol` in the eyes of a type checker?
- [ ] It must inherit from the Protocol class or be registered with it
- [x] It must have the Protocol's members with compatible types and signatures, regardless of inheritance
- [ ] It must be decorated with `@runtime_checkable`
- [ ] It must define `__protocol__` returning the Protocol name
> Protocols are structural: the checker compares members. Inheritance is allowed but never required,
> and `@runtime_checkable` only affects `isinstance`, not static compatibility.

### Q: Why does a checker reject passing a `list[Dog]` to a parameter typed `list[Animal]`?
- [ ] Because `Dog` is not a subclass of `Animal` in the checker's eyes
- [ ] Because lists cannot be generic over user-defined classes
- [x] Because the function could append a non-`Dog` `Animal`, corrupting the caller's list, so `list` is invariant
- [ ] Because `list` is covariant and `Animal` is more specific than `Dog`
> Mutable containers are invariant precisely to prevent writes of the wrong subtype. A read-only
> `Sequence[Animal]` accepts `list[Dog]` because nothing can be inserted through it.

### Q: What problem does `ParamSpec` solve for a retry decorator?
- [ ] It lets the decorator run the wrapped function in a thread
- [ ] It validates argument values at runtime before the call
- [x] It preserves the wrapped function's parameter types so calls through the decorator stay type-checked
- [ ] It makes the decorator usable on async functions
> Without `ParamSpec`, the decorator's return type is `Callable[..., Any]` and every call to a
> decorated function loses argument checking. `ParamSpec` carries the original signature through.
