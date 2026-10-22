---
id: dependency-injection-and-interface-boundaries
module: python-for-ai
name: Dependency injection & interface boundaries
kind: pattern
difficulty: core
est_minutes: 16
status: crisp-verified
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
A component receives its dependencies (LLM client, vector store, clock) from outside, typed as a small
`Protocol` in your own vocabulary. AI code becomes testable without network calls and swappable across
providers, for the price of one thin layer of indirection.

## Why it exists
- **AI apps are glue around external services.** Those services are slow, expensive, non-deterministic
  and owned by someone else. The first version is always a module-level `client = OpenAI()` plus a
  `client.chat.completions.create(...)` call inside whichever function needs an answer.
- **Testing breaks.** Every unit test of that function needs an API key and a network, costs money,
  takes seconds and returns a different string each run, so the test either hits production or gets
  skipped.
- **Swapping and configuration break.** Evaluating Anthropic, a self-hosted vLLM endpoint or a cheaper
  model means vendor shapes scattered through forty files. Reading `OPENAI_API_KEY` at import time makes
  a script that only wanted a helper either crash or silently talk to the wrong environment.
- **The naive fixes fail.** `unittest.mock.patch("app.summarize.client")` breaks when a refactor moves the
  module, because it couples to an internal path, not a contract. `if os.getenv("FAKE_LLM")` puts test
  branches in production. A global registry (`get_llm_client()`) hides the dependency one hop away:
  nothing in the function signature says it needs a model.
- **DI flips the direction.** The code that needs a completer is given one; the boundary names the need
  in types your code owns; one composition root wires it. The cost is explicitness; the payoff is a
  visible, testable dependency graph changeable in one place.

## How it works
### Move 1: name the dependency as an interface you own
- **Declare a narrow port.** A `Protocol` with exactly the methods your use case calls:
  `Completer.complete(system, user, *, max_tokens) -> Completion`.
- **`typing.Protocol`, not an ABC,** *because* protocols are structural: the vendor adapter and the test fake
  satisfy it by having the right method, without inheriting from your package, and mypy checks the
  match statically.
- **It lives next to the domain code,** so the dependency arrow points from the adapter toward your
  code, never the other way round.

### Move 2: translate at the edge
- **The adapter returns your own `Completion` dataclass** (text, token counts, finish reason). If
  `openai.types.chat.ChatCompletion` objects flow through your services, the vendor is still a
  dependency of everything that touches them, and a provider switch becomes a rewrite.
- **The boundary is only as strong as the types that cross it.**
- **One file per vendor imports the SDK.** It handles the SDK's exception types and maps status codes
  into your error hierarchy: `openai.RateLimitError` becomes your `ProviderRateLimited`, so the upstream
  retry policy is written once and works for every vendor.
- **This is dependency inversion in practice:** high-level policy and the low-level SDK both depend on an
  abstraction the high-level side owns.

### Move 3: inject at construction, wire at the root
1. **Constructor injection** (`Summarizer(completer)`) is the Python default *because* the dependency is
   in the signature: readers, type checkers and test authors all see it.
2. **The composition root** (`main()`, an app factory, a FastAPI lifespan) is the single place that reads
   settings, instantiates real clients and connects them.
3. **Tests build the same graph with fakes.**
4. **Nothing else reads environment variables or creates network clients,** which removes import-time
   side effects.

### Not every dependency needs a class
- **Inject a callable for a single operation:**
  `def __init__(self, now: Callable[[], datetime] = datetime.now)` or
  `embed: Callable[[list[str]], list[list[float]]]`. Still DI, *because* the caller decides what runs;
  it is the smallest possible interface.
- **Promote to a Protocol** when a second method appears or the call needs named, typed options.
- **Default-argument trap.** A default is evaluated once, when the `def` runs at import time, so
  `completer: Completer = OpenAICompleter(OpenAI())` quietly reintroduces an import-time client. Defaults
  are safe only for cheap, side-effect-free values such as `datetime.now` itself.

### Sync or async is part of the contract
| Mismatch | What happens |
|---|---|
| Async service calls a sync adapter | event loop blocks on every network call |
| Sync service handed an async adapter | gets an un-awaited coroutine; mypy flags it, the runtime does not |

- `def complete(...)` returns a `Completion`; `async def complete(...)` returns a coroutine. They are
  different interfaces, so decide at the port.
- **Need both?** Define two ports (`Completer`, `AsyncCompleter`), not one that tries to be both.

### Why Python rarely needs a container
- **Java-style containers** reflect on constructors to auto-wire graphs.
- **Python already has the tools:** first-class functions, closures, `functools.partial` and dataclasses
  cover almost every wiring need in a dozen lines.
- **FastAPI's `Depends`** is a request-scoped injector that handles per-request lifetime for you.
- **Reach for a container** (`dependency-injector`, `punq`, `lagom`) only with dozens of services of mixed
  lifetimes (singleton HTTP clients and loaded models vs per-request sessions) and a hand-written root
  that has become the hardest file to read.

### Where to draw boundaries
- **At I/O and non-determinism:** LLM and embedding providers, vector stores, databases, HTTP,
  filesystem, the clock, randomness.
- **Not around pure logic:** a `PromptBuilder` that renders a dataclass into a string needs no
  interface, just a direct test.
- **Working rule:** if you would replace it in a test or at deployment time, it is a port; otherwise it
  is just code.
- **Keep ports narrow, widen deliberately** (add `stream()` when a use case streams). A port mirroring the
  SDK's fifteen methods is the SDK with extra steps, and nobody can write a fake for it.

### Lifetimes
- **Shared, created once at the root:** an `httpx.AsyncClient` with its connection pool, a loaded
  sentence-transformer.
- **Per-request, created by the handler:** a bound logger with `request_id`, a DB session.
- **Constructor injection makes sharing cheap,** because the same instance is simply passed to several
  consumers.

### Test doubles at the boundary
- **Hand-written fake:** returns scripted answers and records calls, so business-logic tests are fast
  and deterministic.
- **Contract test for the real adapter:** against the real API or a recorded response, so fake and
  reality cannot drift unnoticed.
- **Keep fakes dumb:** a fake that re-implements vendor logic will pass tests the real thing fails.

## Diagram
The composition root (`main()`, an app factory or a FastAPI lifespan) is the only place that reads settings
and builds real clients; every adapter's arrow points toward the port your domain code owns, and tests plug
a fake into the same port.

```mermaid
flowchart TD
    S["Settings and env vars"] --> R["Composition root"]
    R -->|"builds"| A1["OpenAICompleter adapter"]
    R -->|"builds"| A2["vLLM or Anthropic adapter"]
    R -->|"injects via constructor"| D["Summarizer domain service"]
    D -->|"calls complete, gets Completion"| P["Completer Protocol"]
    A1 -->|"implements"| P
    A2 -->|"implements"| P
    F["FakeCompleter in tests"] -->|"implements"| P
    A1 -.->|"only file importing the SDK"| V["Vendor SDK and network"]
```

## Examples
### Port, adapter, fake and composition root
`Summarizer` and the `Completer` protocol import nothing from any SDK, the vendor import is deferred into
the root, and the test needs no network.

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

### Injecting the clock and translating errors at the edge
A rate-limited retry wrapper that depends only on your exception and an injected `sleep`, so the test
runs instantly and works for every vendor adapter. Runnable with the standard library alone.

```python
from typing import Callable

class ProviderRateLimited(Exception):            # your error type, raised by every adapter
    pass

class RetryingCompleter:
    def __init__(self, inner, sleep: Callable[[float], None], attempts: int = 3) -> None:
        self._inner, self._sleep, self._attempts = inner, sleep, attempts
    def complete(self, system, user, *, max_tokens):
        for i in range(self._attempts):
            try:
                return self._inner.complete(system, user, max_tokens=max_tokens)
            except ProviderRateLimited:
                if i == self._attempts - 1:
                    raise
                self._sleep(2 ** i)               # 1s, 2s ... in production; recorded in tests

class FlakyFake:
    def __init__(self, failures: int) -> None:
        self.failures = failures
    def complete(self, system, user, *, max_tokens):
        if self.failures:
            self.failures -= 1
            raise ProviderRateLimited()
        return "ok"

slept: list[float] = []
c = RetryingCompleter(FlakyFake(failures=2), sleep=slept.append)
assert c.complete("s", "u", max_tokens=10) == "ok" and slept == [1, 2]
```

### Worked scenario: adding a provider, before and after
A team must add a self-hosted vLLM endpoint for one feature.

| Measure | Module-level client | Port + adapters |
|---|---|---|
| Files touched | every file that calls `client.chat.completions.create` | one new adapter + the root |
| Unit tests needing a key | all of them | none (fakes) |
| Retry logic | per call site, per vendor exception | once, against `ProviderRateLimited` |
| Proof the adapter works | manual | one contract test per adapter |

## Production patterns
- **One composition root per entry point.** Build the graph in `main()`, an app factory or a FastAPI
  lifespan, and store shared clients there; handlers receive them via `Depends`. Monitor startup time and
  that no module creates clients at import (a CLI `--help` should not need credentials).
- **Provider selected by config, at the root only.** Read a setting such as the provider name once and
  construct the matching adapter; the service never branches on it. Watch per-provider error rates and
  latency, tagged by adapter.
- **Error translation in every adapter.** Map each SDK's rate-limit, timeout and auth exceptions to your
  own hierarchy so retries, fallbacks and alerts are written once. Monitor counts per translated error
  type.
- **Contract tests per adapter.** Run a small suite against the real API (nightly or behind a marker) or
  replay recorded responses, and assert the adapter returns a valid `Completion`. Alert when it fails,
  since that is the drift your fakes cannot see.
- **Shared resources injected, never re-created.** One `httpx.AsyncClient` and one loaded embedding model
  per process, passed to every consumer. Monitor open connections and process memory.

## Trade-offs
- **Testability and swappability vs an extra file and hop per boundary.** Flips for a 200-line script with
  one call site, where calling the SDK directly is right.
- **Vendor independence vs provider features.** A lowest-common-denominator port hides tool-call formats,
  prompt caching controls and logprobs; when you deliberately depend on those, widen the port on purpose
  rather than pretending neutrality.
- **Fast, deterministic fakes vs drift.** A fake can drift from the real adapter, so keep a thin
  integration or contract test against the real API or a recorded response.
- **Explicit constructors vs long signatures.** A class taking eight collaborators does too much; split it
  or group related settings, rather than blaming injection.
- **Container vs hand-written root.** A container automates wiring for a large graph with mixed
  lifetimes but hides it behind decorators and configuration; below a few dozen services the hand-written
  root is clearer.

## Pitfalls
- **Clients created at import time** (`client = OpenAI()` at the top of a file). Symptom: tests fail on
  import with a missing API key, or a CLI `--help` takes seconds and needs production credentials. Fix:
  build clients in the root.
- **Vendor objects in domain code.** Symptom: `response.choices[0].message.content` in business logic and
  a provider switch touching dozens of files. Fix: translate to your dataclass in the adapter.
- **Patch-heavy tests** (`@patch("app.services.summarize.openai.OpenAI")` stacked three deep). Symptom:
  thirty tests break when a module is renamed, each re-implementing a slice of the vendor response. Fix:
  inject a fake.
- **A port mirroring the SDK surface with one implementation.** Symptom: nobody can write a fake, so
  tests mock the port's internals. Fix: narrow it to what the use case calls.
- **Clever fakes** that parse the prompt and generate plausible answers. Symptom: green tests for
  behaviour the real provider does not exhibit. Fix: scripted answers only.
- **A sync port in an async service** (or an adapter wrapping an async SDK in `asyncio.run`). Symptom:
  request latency that grows with concurrency because each LLM call blocks the event loop, or
  `RuntimeError: asyncio.run() cannot be called from a running event loop`. Fix: an `AsyncCompleter` port.
- **Threading config values** (`api_key`, `model`, `temperature`) through five layers. Symptom:
  signatures change whenever a setting is added. Fix: inject the configured adapter instead.

## When to use
- **Use when:** code meets something slow, paid, non-deterministic or third-party: model providers,
  embedding services, vector stores, databases, HTTP, time and randomness.
- **Avoid when:** the logic is pure; test it directly.
- **Measure:** can the core use case run in a test with no network and no credentials, and how many files
  would a provider switch touch?
- **Day to day:** the adapter you write when adding a provider, the fake you hand to a test, the one root
  you edit when configuration changes.
- **Interviews:** explain why Protocols beat ABCs here, why the boundary's types must be yours, and why a
  composition root exists at all.

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

## Interview questions
### What is dependency injection, and why does it matter more in LLM applications than in typical CRUD code?
- **Definition:** a component receives its collaborators from outside (usually through its constructor)
  instead of creating or importing them, so the caller decides what actually runs.
- **Why it matters here:** LLM providers, embedding services and vector stores are slow, paid,
  non-deterministic and third-party, so unit tests need a fake and deployments need to swap providers.
- **Result:** business logic runs in a test with no network and no API key, and a provider switch is one
  new adapter plus a change in the composition root.

### Your test suite fails at import with a missing `OPENAI_API_KEY`. How do you debug and fix it?
- **Find the import-time side effect:** look for a module-level `client = OpenAI()`, a settings object read
  at import, or a default argument such as `completer: Completer = OpenAICompleter(OpenAI())`.
- **Move construction to the root:** build clients in `main()`, an app factory or a FastAPI lifespan and pass
  them into constructors.
- **Verify:** importing every module, or running a CLI `--help`, should succeed with no credentials set.

### Why use `typing.Protocol` for a port instead of an ABC, and when would you widen the port?
- **Structural typing:** an adapter or a fake satisfies a Protocol by having the right methods, checked
  statically by mypy, without inheriting from your package.
- **Narrow by default:** expose only the methods the use case calls, so a fake is a few lines.
- **Widen on purpose:** add `stream()` or tool-call support when a use case needs it, accepting that the
  port is no longer lowest-common-denominator.

### When would you not introduce an interface boundary?
- **Pure logic:** a prompt renderer or a token-budget calculator has no I/O, so test it directly.
- **Small scripts:** a 200-line script with one call site gains nothing from a port and an adapter.
- **Single-method needs:** inject a callable such as `now: Callable[[], datetime]` rather than a full
  Protocol class.

### How do you make sure a fake does not drift from the real provider adapter?
- **Keep fakes dumb:** scripted answers and recorded calls, never logic that parses the prompt.
- **Add a contract test per adapter:** run it against the real API nightly or behind a marker, or replay
  recorded responses, and assert it returns a valid `Completion`.
- **Alert on failure,** because a failing contract test is exactly the drift green unit tests cannot see.

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
