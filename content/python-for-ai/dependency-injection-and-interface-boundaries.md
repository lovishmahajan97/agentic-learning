---
id: dependency-injection-and-interface-boundaries
module: python-for-ai
name: Dependency injection & interface boundaries
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
related:
  - python-for-ai/protocols-generics-structural-typing
  - python-for-ai/test-doubles-mocks-fixtures
  - python-for-ai/package-structure-and-import-design
  - python-for-ai/pydantic-settings
  - fastapi-for-ai/dependency-injection
  - node-ts-for-ai/dependency-injection
  - agent-reliability/tool-adapters-service-interfaces
sources:
  - Fowler, 2004 — Inversion of Control Containers and the Dependency Injection pattern
  - Cockburn, 2005 — Hexagonal Architecture (Ports and Adapters)
  - Official docs: typing.Protocol
---

## TL;DR
Dependency injection means a component receives the things it depends on (an LLM client, a vector
store, a clock) from outside instead of importing or constructing them itself. An interface boundary
names that dependency as a small `Protocol` in your own vocabulary rather than the vendor's. Together
they make AI code testable without network calls, swappable across providers, and honest about what it
needs, at the price of one layer of indirection that you must keep thin.

## Why it exists
AI applications are mostly glue around external services that are slow, expensive, non-deterministic
and owned by someone else. The first version of such code looks the same in every codebase: a
module-level `client = OpenAI()` and a `client.chat.completions.create(...)` call inside whichever
function needs an answer. It works, and then three things go wrong at once.

Testing: every unit test of that function now needs an API key and a network, costs money, takes
seconds, and returns a different string each run, so the test either hits production or gets skipped.
Swapping: when the team wants to evaluate Anthropic, a self-hosted vLLM endpoint or a cheaper model
for one feature, the vendor's request and response shapes are scattered through forty files.
Configuration: the module reads `OPENAI_API_KEY` at import time, so importing it from a script that
only needed a helper function either crashes or silently talks to the wrong environment.

The naive fixes each fail. `unittest.mock.patch("app.summarize.client")` works until a refactor
moves the module, because the test is coupled to an internal path rather than a contract, and each
patch re-implements a slice of the vendor response object. An environment flag such as
`if os.getenv("FAKE_LLM")` puts test branches in production code. A global registry
(`get_llm_client()`) hides the dependency again, just one hop away: nothing in the function signature
says it needs a model.

Dependency injection flips the direction: the code that needs a completer is given one. The interface
boundary says what is needed, in terms your code owns, so the domain never imports the vendor. One
composition root does the wiring. The cost is explicitness; the payoff is that the dependency graph is
visible, testable and changeable in one place.

## How it works
### Move 1: name the dependency as an interface you own
Declare a `Protocol` with exactly the methods your use case calls:
`Completer.complete(system, user, *, max_tokens) -> Completion`. Use `typing.Protocol` rather than an
abstract base class *because* protocols are structural: the vendor adapter and the test fake satisfy
it by having the right method, without inheriting from anything in your package, and mypy checks the
match statically. The protocol lives next to the domain code, so the dependency arrow points from the
adapter toward your code, never the other way round.

### Move 2: translate at the edge
The adapter turns the vendor's response into your own `Completion` dataclass (text, token counts,
finish reason). This matters more than it looks: if `openai.types.chat.ChatCompletion` objects flow
through your services, the vendor is still a dependency of everything that touches them, and a
provider switch becomes a rewrite. The boundary is only as strong as the types that cross it. The
adapter is the one file per vendor that imports the SDK, handles its exception types, and maps its
status codes into your error hierarchy: `openai.RateLimitError` becomes your `ProviderRateLimited`, so
the retry policy upstream is written once against your exception and works for every vendor. This is
the dependency inversion principle in practice: the high-level policy and the low-level SDK both
depend on an abstraction that the high-level side owns.

### Move 3: inject at construction, wire at the root
Constructor injection, `Summarizer(completer)`, is the default in Python *because* it makes the
dependency part of the signature: a reader, a type checker and a test author all see it. The
composition root (`main()`, an app factory, a FastAPI lifespan) is the single place that reads
settings, instantiates real clients and connects them. Tests build the same graph with fakes. Because
everything else receives its collaborators, nothing else reads environment variables or creates
network clients, which is what removes import-time side effects.

### Not every dependency needs a class
When the dependency is a single operation, inject a callable instead of a Protocol:
`def __init__(self, now: Callable[[], datetime] = datetime.now)` or
`embed: Callable[[list[str]], list[list[float]]]`. This is still dependency injection, *because* the
caller decides what runs; it is just the smallest possible interface. Promote it to a Protocol when a
second method appears or when the call needs named, typed options. One trap: a default argument is
evaluated once, when the `def` runs at import time, so `completer: Completer = OpenAICompleter(OpenAI())`
quietly reintroduces an import-time client. Defaults are safe only for cheap, side-effect-free values
such as `datetime.now` itself.

### Sync or async is part of the contract
`def complete(...)` and `async def complete(...)` are different interfaces: a caller of the first gets
a `Completion`, a caller of the second gets a coroutine. Decide at the port, *because* an async service
calling a sync adapter blocks the event loop on every network call, and a sync service handed an async
adapter receives an un-awaited coroutine that mypy will flag but the runtime will not. If both are
needed, define two ports (`Completer`, `AsyncCompleter`) rather than one that tries to be both.

### Why Python rarely needs a container
Java-style containers reflect on constructors to auto-wire graphs. Python has first-class functions,
closures, `functools.partial` and dataclasses, which cover almost every wiring need in a dozen lines.
FastAPI's `Depends` is a request-scoped injector that handles the per-request lifetime for you.
Reach for a container library (`dependency-injector`, `punq`, `lagom`) only when the graph has dozens
of services with mixed lifetimes (singleton HTTP clients and loaded models versus per-request
sessions) and the hand-written root has become the hardest file to read.

### Where to draw boundaries
At I/O and non-determinism: LLM and embedding providers, vector stores, databases, HTTP, filesystem,
the clock, randomness. Not around pure logic: a `PromptBuilder` that renders a dataclass into a string
needs no interface, just a direct test. The working rule: if you would want to replace it in a test or
at deployment time, it is a port; otherwise it is just code. Keep ports narrow, the methods a use case
actually calls, and widen them deliberately (add `stream()` when a use case streams). A port that
mirrors the vendor SDK's fifteen methods is the SDK with extra steps, and nobody can write a fake
for it.

### Lifetimes
Shared objects (an `httpx.AsyncClient` with its connection pool, a loaded sentence-transformer) are
created once at the root and passed in; per-request objects (a bound logger with `request_id`, a DB
session) are created by the request handler. Constructor injection makes sharing cheap because the
same instance is simply passed to several consumers.

### Test doubles at the boundary
A hand-written fake that returns scripted answers and records calls tests business logic quickly and
deterministically. The real adapter is covered by a small contract test against the real API or a
recorded response, so the fake and reality cannot drift unnoticed. Keep fakes dumb: a fake that
re-implements vendor logic will pass tests the real thing fails.

## Example
A domain service, a vendor adapter, a fake and a composition root. Note that `Summarizer` and the
`Completer` protocol import nothing from any SDK, the vendor import is deferred into the root, and the
test needs no network.

```python
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int

class Completer(Protocol):                       # the port: your vocabulary, your types
    def complete(self, system: str, user: str, *, max_tokens: int) -> Completion: ...

class OpenAICompleter:                           # the adapter: the only file that imports the SDK
    def __init__(self, client, model: str) -> None:
        self._client, self._model = client, model
    def complete(self, system, user, *, max_tokens):
        r = self._client.chat.completions.create(
            model=self._model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return Completion(r.choices[0].message.content or "",
                          r.usage.prompt_tokens, r.usage.completion_tokens)

class FakeCompleter:                             # test double: scripted answers, records calls
    def __init__(self, answers: list[str]) -> None:
        self.answers, self.calls = list(answers), []
    def complete(self, system, user, *, max_tokens):
        self.calls.append((system, user, max_tokens))
        return Completion(self.answers.pop(0), 10, 5)

class Summarizer:                                # domain service: knows nothing about vendors
    def __init__(self, completer: Completer, max_tokens: int = 200) -> None:
        self._c, self._max = completer, max_tokens
    def summarize(self, doc: str) -> str:
        return self._c.complete("Summarize in two sentences.", doc, max_tokens=self._max).text

def build_summarizer() -> Summarizer:            # composition root: the one place real things are chosen
    from openai import OpenAI
    return Summarizer(OpenAICompleter(OpenAI(), model="gpt-4o-mini"))

def test_summarizer_passes_document_through():
    fake = FakeCompleter(["Short summary."])
    assert Summarizer(fake).summarize("long text") == "Short summary."
    assert fake.calls[0][1] == "long text" and fake.calls[0][2] == 200
```

## Trade-offs
- You gain testability and swappability but pay an extra file and an extra hop per boundary; this
  flips for a 200-line script with one call site, where calling the SDK directly is the right choice.
- Owning the types that cross the boundary frees you from the vendor, but a lowest-common-denominator
  port hides provider-specific features (tool-call formats, prompt caching controls, logprobs); when
  you deliberately depend on those, widen the port on purpose rather than pretending neutrality.
- Fakes make unit tests fast and deterministic, but a fake can drift from the real adapter, so you
  must keep a thin integration or contract test that exercises the real adapter against the real
  API or a recorded response.
- Constructor injection makes dependencies explicit, but a class taking eight collaborators is a sign
  the class does too much, not that injection is wrong; split it or group related settings.
- A container automates wiring across a large graph with mixed lifetimes, but it hides the graph
  behind decorators and configuration; below a few dozen services the hand-written root is clearer.

## Pitfalls
- Creating clients at module import time (`client = OpenAI()` at the top of a file); the symptom is
  tests failing on import with a missing API key, or a CLI `--help` that takes seconds and needs
  production credentials.
- Letting vendor response objects cross into domain code; the symptom is
  `response.choices[0].message.content` appearing in business logic and a provider switch touching
  dozens of files.
- Patch-heavy tests (`@patch("app.services.summarize.openai.OpenAI")` stacked three deep); the symptom
  is thirty tests breaking when a module is renamed, and each test re-implementing a slice of the
  vendor's response shape.
- A port with a single implementation that mirrors the SDK surface; the symptom is that nobody can
  write a fake for it, so tests mock the port's internals instead.
- Fakes that are too clever (parsing the prompt and generating plausible answers); the symptom is
  green tests for behaviour the real provider does not exhibit.
- An async service whose port is sync (or an adapter that wraps an async SDK in `asyncio.run`); the
  symptom is request latency that grows with concurrency because each LLM call blocks the event loop,
  or `RuntimeError: asyncio.run() cannot be called from a running event loop`.
- Threading individual config values (`api_key`, `model`, `temperature`) through five layers; the
  symptom is signatures that change whenever a setting is added. Inject the configured adapter instead.

## When to use
Draw a boundary wherever your code meets something slow, paid, non-deterministic or owned by a third
party: model providers, embedding services, vector stores, databases, HTTP, time and randomness. Do not
draw one around pure logic. Measure the health of the design by two questions: can the core use case
be exercised in a test with no network and no credentials, and how many files would a provider switch
touch? In day-to-day work this shows up as the adapter you write when adding a provider, the fake you
hand to a test, and the one composition root you edit when configuration changes. In interviews the
expected depth is explaining why Protocols beat ABCs here, why the boundary's types must be yours, and
why a composition root exists at all.

## Role lens
- **Engineer:** Writes the `Completer` and `VectorStore` protocols, one adapter per provider, dumb
  fakes for tests, and keeps the SDK import inside the adapter and the composition root.
- **Architect:** Owns where boundaries sit and what crosses them: which provider features are exposed
  through the port, how adapters report errors, and which objects are shared versus per-request.
- **FDE:** Swaps a customer's mandated provider, on-prem endpoint or internal vector database in by
  writing one adapter against the existing port, without touching the delivered business logic.

## Check yourself
**Q:** A service class imports both `openai` and `anthropic` and switches between them on a config
string inside its methods. The team says this is already "provider-agnostic". What is wrong, and how
would you restructure it?
**A:** The service still depends on both SDKs: any test imports both, any new provider edits the
service, and vendor response objects are handled inline in domain code. Provider choice belongs in the
composition root, not in the service. Define a `Completer` protocol in your own types, write one
adapter per vendor that imports its SDK and translates responses into your `Completion` dataclass,
and have the service take a `Completer` in its constructor. The root reads the config string once and
constructs the matching adapter. Tests pass a fake; adding a vendor adds one file; the service never
changes. A strong answer also mentions keeping the port narrow and a contract test per adapter so the
fakes cannot drift from reality.

## Quiz
### Q: Why is `typing.Protocol` usually preferred over an abstract base class for a port such as `Completer`?
- [ ] Protocols are checked at runtime, so a bad adapter fails at construction time
- [x] Protocols are structural, so vendor adapters and test fakes satisfy them without inheriting from your package
- [ ] Abstract base classes cannot have more than one concrete implementation
- [ ] Protocols are faster because they skip method resolution
> A class satisfies a Protocol by having the right methods, checked statically by mypy, so adapters
> stay decoupled from your code. Protocols are not enforced at runtime (`@runtime_checkable` only
> checks that method names exist, not signatures), and ABCs support many implementations; the
> difference is nominal versus structural typing.

### Q: Which of these should NOT get its own interface boundary in a typical RAG service?
- [ ] The vector store client used for retrieval
- [ ] The system clock used by a rate limiter
- [x] A pure function that renders a dataclass into a prompt string
- [ ] The embedding model that turns chunks into vectors
> Boundaries belong at I/O and non-determinism, where you want to substitute a fake in tests or a
> different implementation at deployment. Pure, deterministic logic is tested directly; wrapping it
> in a Protocol adds indirection with nothing to swap.

### Q: What is the main purpose of a composition root?
- [ ] To cache expensive objects so they are constructed only once per process
- [ ] To validate configuration before the application starts
- [x] To be the single place that chooses concrete implementations and wires them into the object graph
- [ ] To provide a global registry that any module can query for a dependency
> The root is where real adapters are instantiated and handed to the services that need them, so
> nothing else reads settings or builds clients. A queryable global registry is the service-locator
> anti-pattern, which hides dependencies instead of making them explicit.
