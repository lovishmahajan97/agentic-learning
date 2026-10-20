---
id: test-doubles-mocks-fixtures
module: python-for-ai
name: Test doubles, mocks and fixtures
kind: pattern
difficulty: core
est_minutes: 15
related:
  - python-for-ai/pytest
  - python-for-ai/pytest-asyncio
  - python-for-ai/dependency-injection-and-interface-boundaries
  - python-for-ai/protocols-generics-structural-typing
  - python-for-ai/integration-and-end-to-end-testing
  - python-for-ai/contract-testing
  - agent-reliability/deterministic-replay-and-reproducibility
sources:
  - Meszaros, 2007 — xUnit Test Patterns
  - Fowler, 2007 — Mocks Aren't Stubs
  - Official docs: unittest.mock
  - Official docs: pytest
---

## TL;DR
A test double stands in for a real collaborator (an LLM, an HTTP API, a database) so a test is fast,
deterministic and free. Doubles come in kinds: dummies, stubs, spies, mocks and fakes, which differ in
whether they return canned data, record calls, or behave like a small real implementation. Fixtures are
how pytest builds, shares and tears down those doubles and other test state. Choose the kind by what
the test must verify: the result, or the interaction.

## Why it exists
Code around a model talks to things you cannot afford to call in a unit test. A real LLM call costs
money, takes hundreds of milliseconds to seconds, returns different text on each run, rate-limits you
in CI, and needs a secret the CI runner may not have. A vector database needs a running server. A
payment or ticketing API has side effects. If every test of your retry logic, prompt assembly or tool
dispatch goes through those, the suite is slow, flaky and expensive, and people stop running it.

You also cannot easily make the real dependency misbehave on demand. The branches that matter most,
"the model returned an empty string", "the API timed out on the second attempt", "the tool returned
malformed JSON", are exactly the ones a live service rarely produces when you want it to.

The naive fix, patching everything with `MagicMock` until the test goes green, trades one problem for
another. A bare mock accepts any attribute and any call, so a test can pass while the code calls a
method that does not exist on the real client. And tests that assert on every internal call break on
every refactor even when behaviour is unchanged. The vocabulary of test doubles exists to pick the
smallest stand-in that verifies what you actually care about.

## How it works
### The kinds of double
Meszaros's taxonomy, which most of the industry uses loosely:
- **Dummy:** passed only to fill a parameter; never used. `client=None` when the path under test does
  not touch the client.
- **Stub:** returns canned answers. "Whatever you are asked, reply `'{"label": "spam"}'`." Used to drive
  the code down a branch. You assert on the code's output, not on the stub.
- **Spy:** a stub that also records how it was called, so the test can check afterwards ("was the
  second attempt sent with a lower temperature?").
- **Mock:** pre-programmed with expectations about calls; the test fails if the interactions differ.
  In Python, `unittest.mock.Mock` is technically a spy you assert on after the fact.
- **Fake:** a working, simplified implementation: an in-memory vector store, a dict-backed cache, a
  scripted LLM that pops replies off a list. Fakes are more code, but they make tests read like usage.

The distinction matters because it decides what the test verifies. Stubs and fakes support
**state verification**: run the code, check the result. Mocks and spies support **behaviour
verification**: check the calls. State verification survives refactoring; behaviour verification is
right only when the call *is* the behaviour (an email was sent, a tool was invoked with these
arguments, a charge was not made twice).

### unittest.mock mechanics
- `Mock` / `MagicMock` create attributes on access and record every call in `call_args_list`.
  `MagicMock` also supports dunder methods (`len`, iteration, context managers). `AsyncMock` returns
  awaitables, for async clients.
- `return_value` and `side_effect` script behaviour; `side_effect` can be a list (one result per call)
  or an exception class, which is how you simulate "timeout, then success".
- **Spec it.** `create_autospec(RealClass, instance=True)` or `Mock(spec_set=RealClass)` makes the
  double reject attributes and call signatures the real class lacks, because an unspecced mock will
  happily accept `client.compelte(...)` and the typo ships.
- `patch("pkg.module.name")` replaces a name *where it is looked up*, not where it is defined. If
  `app/summarize.py` does `from app.llm import call_llm`, you patch `app.summarize.call_llm`;
  patching `app.llm.call_llm` changes nothing the code under test sees.

### Fixtures
A pytest fixture is a function whose return value (or `yield`ed value) is injected into any test that
names it as a parameter. Code after `yield` is teardown, and runs even if the test fails. Scope
(`function`, `class`, `module`, `package`, `session`) controls how often it is rebuilt: wide scope for
expensive immutable things (a loaded tokenizer), function scope for anything a test can mutate.
Fixtures in `conftest.py` are shared by every test below that directory. A **factory fixture**
returns a function, so each test can build its own variant ("an LLM that replies with these three
strings"). Built-ins do common jobs: `monkeypatch` sets env vars and attributes and undoes them,
`tmp_path` gives an isolated directory.

### Prefer injection over patching
If a function takes its client as a parameter (or a constructor does), the test passes a fake and
needs no patching at all. Patching is the tool for code you cannot restructure; dependency injection
is what makes doubles cheap.

## Example
A summarizer retries once when the model returns empty output and truncates to a limit read from the
environment. Three tests use three kinds of double: a scripted fake for state checks, an autospecced
mock for one interaction that matters, and `monkeypatch` for configuration.

```python
import os
import pytest
from unittest.mock import create_autospec

class LLMClient:                                   # the real client's interface
    def complete(self, messages: list[dict], *, model: str) -> str: ...

def summarize(text: str, *, client, model: str = "large") -> str:
    limit = int(os.environ.get("SUMMARY_MAX_CHARS", "500"))
    for _ in range(2):
        out = client.complete([{"role": "user", "content": f"Summarize: {text}"}], model=model)
        if out.strip():
            return out[:limit]
    raise RuntimeError("model returned empty output twice")

class FakeLLM:                                     # fake: scripted replies, records calls
    def __init__(self, replies): self.replies, self.calls = list(replies), []
    def complete(self, messages, *, model):
        self.calls.append((messages, model))
        return self.replies.pop(0)

@pytest.fixture
def make_llm():                                    # factory fixture
    return lambda *replies: FakeLLM(replies)

def test_retries_once_on_empty(make_llm):
    llm = make_llm("", "a summary")
    assert summarize("long text", client=llm) == "a summary"
    assert len(llm.calls) == 2

def test_gives_up_after_two_empties(make_llm):
    with pytest.raises(RuntimeError):
        summarize("x", client=make_llm("", "  "))

def test_forwards_model_name():
    llm = create_autospec(LLMClient, instance=True)    # rejects calls the real class would reject
    llm.complete.return_value = "ok"
    summarize("text", client=llm, model="small")
    assert llm.complete.call_args.kwargs["model"] == "small"

def test_truncates_to_env_limit(monkeypatch, make_llm):
    monkeypatch.setenv("SUMMARY_MAX_CHARS", "5")       # undone after the test
    assert summarize("x", client=make_llm("abcdefgh")) == "abcde"
```

None of these tests hit the network, and none breaks if `summarize` changes how it builds its prompt.

## Trade-offs
- Fakes make tests readable and refactor-proof, but are code you must maintain and can drift from the
  real service; pair them with a contract or integration test once drift has bitten you.
- Mocks verify interactions precisely, but couple tests to implementation; they flip from liability to
  asset only when the interaction itself is the requirement, such as "never call the payment API twice".
- Wide fixture scopes make suites fast, but let one test's mutation leak into another; use them only
  for objects nothing mutates, and function scope for everything else.
- Patching lets you test code you cannot restructure, but hides the dependency; when you own the code,
  injecting the collaborator removes the need for patching entirely.

## Pitfalls
- Unspecced `MagicMock` standing in for a client; the symptom is a green test while production raises
  `AttributeError` or `TypeError` because the method name or signature is wrong.
- Patching the name where it is defined rather than where it is imported; the symptom is a test that
  still makes real network calls or fails with a missing API key despite the patch.
- Asserting on every call and argument; the symptom is dozens of tests failing after a refactor that
  changed no behaviour, so the team learns to update assertions without reading them.
- Stubbing the LLM with an idealised reply (perfect JSON, no fences, no preamble); the symptom is a
  parser that passes every test and fails on the first real response wrapped in markdown fences.
- Mutating a session-scoped fixture inside a test; the symptom is tests that pass alone and fail
  depending on order, or only under parallel runs.

## When to use
Use doubles at the boundary of your process: model APIs, HTTP services, databases, clocks and
randomness. Do not double your own pure functions or value objects; call them. Default to fakes and
stubs with state assertions, reach for spec'd mocks when the call is the requirement, and record real
model responses as stub data so your stubs look like what models actually return, including their
mess. Every double is a claim about the real dependency, so back the important ones with a contract
or integration test. In interviews, expect "how do you test code that calls an LLM?"; a good answer
separates deterministic logic tested with doubles from model quality measured with evals.

## Role lens
- **Engineer:** Builds a shared fake LLM client and factory fixtures so every test can script model
  replies, failures and timeouts without network access or keys.
- **Architect:** Defines client interfaces and injection points so doubles are cheap, and decides which
  boundaries also need contract or integration tests to keep fakes honest.
- **FDE:** Captures real responses from the customer's systems (sanitised) and turns them into stub data,
  so a failure seen on site becomes a fast local regression test.

## Check yourself
**Q:** A teammate's tests for an agent's tool dispatcher mock the LLM, the tool registry and every tool,
then assert the exact sequence of calls. They break weekly. What would you change, and what would you
keep?
**A:** Those tests verify implementation, not behaviour, so any refactor of call order breaks them. I
would replace the mocked LLM with a scripted fake that emits specific tool-call messages, use real tool
registry code, and use fake tools that record what they received. Tests then assert outcomes: the right
tool ran with validated arguments, malformed arguments were rejected, the final answer included the
tool result. I would keep interaction assertions only where the call is the requirement, such as a
destructive tool never running without approval, and spec every remaining mock against the real class.

## Quiz
### Q: Which double is best described as a working, simplified implementation?
- [ ] A stub that always returns the same canned response
- [x] A fake, such as an in-memory vector store or a scripted LLM client
- [ ] A mock configured with expected calls
- [ ] A dummy passed only to satisfy a parameter
> A fake actually behaves like the dependency, just simplified. A stub only returns canned answers and
> does not model behaviour, which is why fakes read more like real usage in tests.

### Q: `app/agent.py` does `from app.llm import call_llm`. Which patch target replaces it for code in agent.py?
- [ ] app.llm.call_llm
- [x] app.agent.call_llm
- [ ] call_llm
- [ ] app.llm
> The import copied a reference into app.agent's namespace, so that is where the name is looked up.
> Patching app.llm.call_llm rebinds the original module's name, which agent.py no longer consults.

### Q: Why prefer `create_autospec(RealClient, instance=True)` over a bare `MagicMock()` for a client?
- [ ] It runs faster than MagicMock
- [ ] It automatically records real responses from the API
- [x] It rejects attributes and call signatures the real class does not have
- [ ] It makes the test independent of the client's interface
> An autospecced double mirrors the real interface, so a typo or wrong argument fails the test. A bare
> MagicMock accepts anything, letting interface mistakes pass tests and fail in production.
