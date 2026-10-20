---
id: protocols-generics-structural-typing
module: python-for-ai
name: Protocols, generics and structural typing
kind: concept
difficulty: core
est_minutes: 16
related:
  - python-for-ai/type-hints
  - python-for-ai/mypy
  - python-for-ai/dependency-injection-and-interface-boundaries
  - python-for-ai/test-doubles-mocks-fixtures
  - python-for-ai/pydantic
  - agents/typed-tool-contracts
  - node-ts-for-ai/typescript-strict-mode-and-advanced-types
sources:
  - Levkivskyi, Lehtosalo, Langa, 2017 — PEP 544: Protocols: Structural subtyping (static duck typing)
  - Traut, 2022 — PEP 695: Type Parameter Syntax
  - Official docs: typing module
  - Official docs: mypy (Protocols and structural subtyping; Generics)
---

## TL;DR
A `Protocol` describes an interface by *shape* (which methods and attributes exist, with which
signatures) rather than by inheritance, so any class that fits is accepted by the type checker without
importing or subclassing your base. Generics (`TypeVar`, `Generic[T]`, or `class Box[T]` on 3.12+)
let a container or function keep track of *which* type flows through it. Together they let AI code
swap providers, embedders and stores freely while the checker still proves the pieces fit.

## Why it exists
AI applications are mostly adapters around interchangeable backends: three LLM providers, two embedding
models, a vector store you will replace next quarter, and a fake of each for tests. The application
core should say "give me something I can call `embed(texts)` on" and not care who implements it.

The nominal answer is an abstract base class: `class Embedder(ABC)` with an `@abstractmethod`, and
every implementation subclasses it. That fails in exactly the cases that matter. You cannot make a
third-party class (an SDK client, a `SentenceTransformer`, a customer's in-house gateway wrapper)
inherit from your ABC without writing a wrapper whose only purpose is to inherit. Test doubles must
import the production base, which drags production dependencies into the test suite. And a function
that is happy with "anything that has `.embed`" is forced to demand a specific ancestry it never used.

Plain duck typing is the other extreme: accept anything, find out at runtime. That is how Python
worked for twenty years and it is why "`AttributeError: 'OpenAI' object has no attribute 'embed'`"
appears forty minutes into a batch job rather than in the editor. Protocols keep duck typing's
flexibility and add the checker's proof: the shape is written down once, and every call site is
verified against it before anything runs.

Generics solve a different hole. Without them, a `VectorIndex` that stores "some payload" has to type
it as `Any` (or `object`), and the moment a payload comes back out the checker has forgotten what it
was. A generic `VectorIndex[Document]` remembers, so `index.nearest(q).title` is checked, and
`VectorIndex[dict[str, str]]` is a different, equally checked, instantiation of the same code.

## How it works
### Nominal versus structural
Nominal typing asks "is this class, or one of its ancestors, the named type?" That is what
`isinstance` and ABCs do. Structural typing asks "does this object have the members the type
requires?" PEP 544 added structural typing to the static checkers *because* that is what idiomatic
Python code already assumed; the checker now verifies the assumption instead of trusting it.

### Defining a Protocol
Write a class that inherits from `typing.Protocol` and list the members with their signatures; bodies
are `...`. Members can be methods, plain attributes (`dim: int`) or properties. A class satisfies the
protocol when it has every member with a compatible signature: parameter types may be wider
(contravariant) and return types may be narrower (covariant), the same rule as overriding a method.
The implementing class never names the protocol. It *may* inherit from it explicitly to get default
method bodies and an immediate error if a member is missing, which is a reasonable choice for classes
you own and impossible for classes you do not.

`@runtime_checkable` lets `isinstance(obj, Embedder)` work, but the runtime check only confirms that
attributes with the right *names* exist; it cannot inspect signatures or types. Treat it as a cheap
sanity check at a plugin boundary, not as validation. Protocols with data members cannot be used with
`issubclass` at all.

A protocol whose only member is `__call__` types a callback: `class ToolFn(Protocol): def
__call__(self, *, query: str) -> str: ...` accepts any function or callable object with that exact
keyword signature, which is more precise than `Callable[..., str]`.

### Generics
A `TypeVar` is a placeholder that the checker solves at each use. `def first(xs: Sequence[T]) -> T`
means "whatever element type you pass, that is what you get back". A class that subclasses
`Generic[T]` gets a type parameter that its methods can reference, and `Index[Document]` binds it.
Python 3.12's PEP 695 syntax (`class Index[T]:`, `def first[T](xs: Sequence[T]) -> T:`) is sugar for
the same thing and scopes the variable to the definition. A bound (`T: Hashable`, or
`TypeVar("T", bound=Hashable)`) says what operations the body may rely on; constraints
(`TypeVar("S", str, bytes)`) restrict it to a closed list. Protocols can be generic too:
`class Retriever(Protocol[T])` with `def search(self, q: str) -> list[T]: ...`.

### Variance, the part that bites
`list[Child]` is **not** accepted where `list[Parent]` is expected, even though `Child` subclasses
`Parent`. The reason is that a `list` can be written to: the callee could append a different `Parent`
subclass into what the caller still believes is a `list[Child]`. Mutable containers are therefore
*invariant*. Read-only views are *covariant*: `Sequence[Child]` is a `Sequence[Parent]`, because
nothing can be put in. The practical rule: accept `Sequence`, `Mapping` or `Iterable` in parameters
and return the concrete `list` or `dict`. Callables are contravariant in their arguments for the
mirror-image reason.

### Where the runtime stands
None of this runs. `Index[Document]` evaluates to a `_GenericAlias` object that constructs plain
`Index` instances; `T` is never substituted at runtime. The guarantees come from running mypy or
pyright in CI, which is why those tools sit next to this lesson in the module.

## Example
A structural `Embedder` protocol, a generic index parameterised by payload type, and a fake that
satisfies the protocol without inheriting. The checker verifies the `FakeEmbedder` shape and knows
that `nearest` returns a `dict[str, str]`.

```python
from typing import Generic, Protocol, Sequence, TypeVar, runtime_checkable

@runtime_checkable
class Embedder(Protocol):
    dim: int
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

class FakeEmbedder:                      # no base class: the shape alone satisfies Embedder
    dim = 3
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(len(t)), 0.0, 1.0] for t in texts]

T = TypeVar("T")                         # Python 3.12+: `class Index[T]:` and drop Generic[T]

class Index(Generic[T]):
    def __init__(self, embedder: Embedder) -> None:
        self._e = embedder
        self._items: list[tuple[list[float], T]] = []

    def add(self, text: str, payload: T) -> None:
        self._items.append((self._e.embed([text])[0], payload))

    def nearest(self, query: str) -> T:
        q = self._e.embed([query])[0]
        dot = lambda v: sum(a * b for a, b in zip(v, q))
        return max(self._items, key=lambda item: dot(item[0]))[1]

idx: Index[dict[str, str]] = Index(FakeEmbedder())
idx.add("refund policy", {"doc": "policy.md"})
idx.add("pricing", {"doc": "pricing.md"})
hit = idx.nearest("how do refunds work")           # checker: hit is dict[str, str]
print(hit["doc"], isinstance(FakeEmbedder(), Embedder))   # policy.md True
# idx.add("x", 42)  -> mypy: Argument 2 has incompatible type "int"; expected "dict[str, str]"
```

## Trade-offs
- Structural interfaces decouple callers from implementations, but you lose "find all subclasses" as
  a discovery tool; when a team needs an explicit registry of implementations, explicit subclassing
  of the protocol (allowed) restores it.
- Generics give precise element types through containers, at the cost of variance reasoning; if a
  container is only ever read, typing parameters as `Sequence` avoids the invariance errors entirely.
- ABCs fail fast at instantiation when a method is missing; Protocols only fail under the checker, so
  in a codebase without mypy or pyright in CI an ABC is the safer choice.
- `@runtime_checkable` adds a cheap plugin-boundary check, but it verifies names only; when inputs
  are untrusted, a Pydantic model or explicit validation is the right tool, not `isinstance`.

## Pitfalls
- Passing `list[OpenAIMessage]` to a parameter typed `list[Message]` and getting "incompatible type";
  the fix is `Sequence[Message]` in the signature, not a cast, because `list` is invariant.
- Implementing a protocol method with different parameter *names* or a positional/keyword mismatch;
  mypy reports the class as not satisfying the protocol and lists the conflicting members.
- Trusting `isinstance(obj, Proto)` to validate a plugin; an object with an `embed` attribute that is
  a string passes the check and fails at the first call.
- Writing `def get(self) -> T` on a generic class that never receives a `T` in its constructor or
  methods; the checker infers `Index[Never]` or `Index[Any]` and the generic silently stops helping.
- Forgetting that `Protocol` classes cannot be instantiated and that explicit subclasses inherit
  default bodies while implicit ones do not; the symptom is a `NotImplementedError`-style surprise
  only on the implicit path.

## When to use
Reach for a Protocol whenever a function or class depends on behaviour rather than ancestry: the LLM
client, the embedder, the retriever, the tool executor, the cache. It is the natural port in a
ports-and-adapters design and the cheapest way to make tests use fakes. Use an ABC instead when you
own every implementation, want runtime enforcement at construction time, and want shared concrete
code. Use generics when a container or pipeline stage carries a payload type that callers need back
intact; measure success by whether mypy or pyright reports zero `Any` leaking out of those paths. In
interviews, being able to explain why `list` is invariant and when to prefer `Sequence` is the
discriminating question.

## Role lens
- **Engineer:** Writes `LLMClient` and `Embedder` protocols, codes the app against them, and ships
  fakes for tests that never import a vendor SDK.
- **Architect:** Owns the port boundaries: which capabilities are protocols, how vendor adapters map
  onto them, and how generics keep document and tool payload types checked across layers.
- **FDE:** Wraps a customer's unmodifiable in-house gateway class to satisfy the product's protocol,
  proving compatibility with the checker before any integration test runs.

## Check yourself
**Q:** Your RAG library exposes `Retriever` as an ABC. A customer wants to plug in an internal search
class they cannot modify, and your own tests are slow because fakes import the production module.
What would you change, and what do you give up?
**A:** Convert `Retriever` to a `Protocol`, generic in the document type (`Retriever[T]` with
`search(q) -> list[T]`), so the customer's class satisfies it by shape and test fakes need no imports.
Keep an optional concrete base (explicitly subclassing the protocol) for implementations you own that
want shared helpers. Type the inputs as `Sequence`/`Mapping` to avoid invariance errors. What you give
up: construction-time enforcement, so a missing method is only caught by mypy or pyright, which must
run in CI; and `@runtime_checkable` checks at the plugin boundary verify names only, so argument
validation at that boundary still needs explicit code.

## Quiz
### Q: What does `isinstance(obj, SomeProtocol)` check when the protocol is `@runtime_checkable`?
- [ ] That `obj`'s class explicitly inherits from the protocol
- [x] That attributes with the protocol's member names exist on `obj`, ignoring their signatures and types
- [ ] That every method signature on `obj` matches the protocol exactly
- [ ] That `obj` was validated by the type checker at import time
> The runtime check is structural but shallow: it looks for the member names only. Signature and type
> conformance exist only in the static checker, which is why `isinstance` is not a validation tool.

### Q: Why does mypy reject passing a `list[Child]` to a parameter annotated `list[Parent]`?
- [ ] Because `Child` must be declared as a Protocol to be substitutable
- [ ] Because mypy does not support subclass relationships inside containers
- [x] Because `list` is invariant: the callee could insert another `Parent` subtype into the caller's list
- [ ] Because generic aliases are erased at runtime, so the relationship cannot be proved
> A mutable container accepted as `list[Parent]` may be written to with any `Parent`, which would
> corrupt the caller's `list[Child]`. Read-only `Sequence[Parent]` is covariant and accepts it.

### Q: Which statement about a third-party class and your `Protocol` is true?
- [x] The class satisfies the protocol if its members match, even though it never imports your module
- [ ] The class must be registered with `Protocol.register()` before the checker accepts it
- [ ] The class must subclass the protocol, otherwise the checker treats it as `Any`
- [ ] The protocol must be decorated with `@runtime_checkable` for static checking to work
> Structural subtyping is the whole point of PEP 544: conformance is decided by shape, so classes you
> cannot modify fit without wrappers. `register()` belongs to ABCs, and `@runtime_checkable` only
> affects `isinstance`.
