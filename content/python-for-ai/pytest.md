---
id: pytest
module: python-for-ai
name: pytest
kind: tool
difficulty: intro
est_minutes: 14
related:
  - python-for-ai/pytest-asyncio
  - python-for-ai/test-doubles-mocks-fixtures
  - python-for-ai/property-based-testing
  - python-for-ai/integration-and-end-to-end-testing
  - python-for-ai/ruff
  - backend-fundamentals/ci-cd
  - evals/regression-tests
sources:
  - Official docs: pytest
  - Okken, 2022 — Python Testing with pytest (2nd ed.)
---

## TL;DR
pytest is the de facto Python test runner: plain `assert` statements that print the values involved
when they fail, fixtures that inject setup by argument name, parametrization that turns one function
into a table of cases, and a hook system that asyncio, Hypothesis, xdist and coverage tools plug into.
It exists because unittest's class-and-method ceremony and opaque assertion failures made tests
expensive to write and slow to debug; pytest makes the cheap path the correct one.

## Why it exists
Python's standard `unittest` is a port of JUnit. Every test lives in a `TestCase` subclass, every
assertion is a method (`assertEqual`, `assertIn`, `assertRaises`, dozens more), and setup is
`setUp`/`tearDown` per class with no way to compose shared resources. A failing `assertEqual(a, b)`
prints `3 != 4` and nothing about where 3 came from. Writing a plain `assert a == b` instead gives you
even less: Python's own `AssertionError` carries no message at all.

The cost of that friction is not aesthetic. AI code is full of small pure functions that need many
cases: a chunker with boundary conditions, a prompt builder that must escape user text, a parser for
model output that may or may not be fenced JSON, a tool dispatcher that must reject malformed
arguments, a retry wrapper that must stop after N attempts. Each needs a table of inputs and expected
outputs, and each needs some shared, expensive setup: a tokenizer loaded once, a fake LLM, a temporary
directory, a container. If the table costs a class per row and the setup costs a method per class,
people write three tests where they needed thirty.

pytest's design answers both problems. It rewrites `assert` so a bare comparison yields a useful
failure, and it treats setup as dependency injection: a test declares what it needs as parameters,
and the runner builds and tears it down with the right lifetime. Everything else, from async support
to parallelism, arrives as plugins through the same hook mechanism.

## How it works
### Discovery and collection
`pytest` walks `testpaths` (or the current directory), imports files matching `test_*.py` or
`*_test.py`, and collects functions prefixed `test_` and methods of classes prefixed `Test` that have
no `__init__`. It also collects `unittest.TestCase` subclasses, which is how a legacy suite migrates
incrementally. Collection happens before any test runs, so a syntax error anywhere fails the whole
session early, and `--collect-only` shows the exact test ids that would run.

### Assertion rewriting
The useful failure messages come from an import hook. When pytest imports a test module (and
`conftest.py` files, and any package registered with `pytest.register_assert_rewrite`), it rewrites the
module's AST so each `assert expr` becomes code that evaluates the sub-expressions, keeps their values,
and builds a message on failure. That is why `assert len(chunks) == n` fails with
`assert 4 == 3` and ` +  where 4 = len([...])`. It is done at import time rather than with tracing
*because* tracing would slow every test and could not see inside a single expression; it is limited to
test modules *because* changing bytecode in production code would be a surprising side effect. The
rewritten modules are cached as `.pyc` files tagged with the pytest version.

### Fixtures
A fixture is a function decorated with `@pytest.fixture`. A test that names it as a parameter gets its
return value; pytest resolves the dependency graph (fixtures can depend on fixtures) per test. Scope
controls lifetime: `function` (default, rebuilt per test), `class`, `module`, `package`, `session`. A
`yield` inside the fixture splits setup from teardown, and teardown runs even when the test fails.
Fixtures declared in a `conftest.py` are visible to every test in that directory tree without
imports, which is how a project shares its `fake_llm` or `db` fixture. Built-ins cover the common
needs: `tmp_path` (fresh directory), `monkeypatch` (undoable attribute and env-var changes), `capsys`
and `caplog` (captured stdout and log records), `request` (introspection of the calling test).
The rule that a higher-scoped fixture cannot depend on a lower-scoped one exists *because* a
session-wide object cannot be rebuilt once per test; violating it raises `ScopeMismatch` at setup.

### Parametrization
`@pytest.mark.parametrize("raw,expected", cases, ids=[...])` expands one function into one test per
row, each with its own id such as `test_parse[fenced-json]`. Stacking two decorators yields the
Cartesian product. `pytest.param(..., marks=pytest.mark.xfail)` marks a single row. Fixtures can be
parametrized too (`params=`), which runs every dependent test once per parameter: a `store` fixture
parametrized over `["memory", "pgvector"]` runs the whole store suite against both backends.

### Markers and selection
`@pytest.mark.skip`, `skipif`, and `xfail(strict=True)` document known gaps; custom markers such as
`integration` or `e2e` are registered in configuration and selected with `-m "not e2e"`. The
`--strict-markers` option turns a typo in a marker name into an error, because an unregistered marker
is otherwise silently ignored and the test runs in the wrong lane. `-k` selects by substring or
boolean expression over test names, `-x` stops at the first failure, `--lf` reruns only the last
failures, `--durations=10` lists the slowest tests.

### Configuration and plugins
Settings live under `[tool.pytest.ini_options]` in `pyproject.toml`: `testpaths`, `addopts`,
`markers`, `filterwarnings`. Plugins are ordinary packages that implement `pytest_*` hook functions
and register an entry point; `conftest.py` can implement the same hooks locally. The ones an AI
engineer meets weekly: `pytest-asyncio` (coroutine tests), `pytest-xdist` (`-n auto` runs files across
CPU cores), `pytest-cov` (coverage), `pytest-timeout` (kill hung tests), `pytest-mock` (the `mocker`
fixture), and Hypothesis, which registers its own settings and database handling.

## Example
A chunker test file showing a session-scoped fixture from `conftest.py`, parametrized cases with
readable ids, and `pytest.raises`. The chunker here counts whitespace tokens so the arithmetic in the
ids is checkable by hand.

```python
# tests/conftest.py
import pytest
from myapp.chunking import Chunker

@pytest.fixture(scope="session")
def chunker():                              # built once per run: construction is the slow part
    return Chunker(max_tokens=50, overlap=5)

# tests/test_chunking.py
import pytest

@pytest.mark.parametrize("text,n_chunks", [
    ("", 0),
    ("short", 1),
    ("word " * 120, 3),                     # windows 0-49, 45-94, 90-119 -> three chunks
], ids=["empty", "single", "overlapping"])
def test_chunk_count(chunker, text, n_chunks):
    assert len(chunker.split(text)) == n_chunks

def test_every_chunk_respects_limit(chunker):
    chunks = chunker.split("word " * 1000)
    assert all(chunker.count_tokens(c) <= 50 for c in chunks)

def test_overlap_is_shared_tokens(chunker):
    first, second = chunker.split("word " * 120)[:2]
    assert first.split()[-5:] == second.split()[:5]

@pytest.mark.parametrize("bad", [None, 42, b"bytes"])
def test_rejects_non_text(chunker, bad):
    with pytest.raises(TypeError):
        chunker.split(bad)
```

Run `pytest -q tests/test_chunking.py` and a wrong overlap implementation fails as
`test_chunk_count[overlapping]` with `assert 4 == 3` followed by `where 4 = len([...])`, which tells
you the case and the value without a debugger. `pytest -k overlap` runs just the two tests whose
names contain the word.

## Trade-offs
- Fixture injection by name is concise, but the implicit wiring through `conftest.py` hides where a
  value comes from; in a large suite you pay with `--fixtures` lookups and the occasional surprise
  override from a nearer `conftest.py`.
- Assertion rewriting makes bare `assert` informative, but it only applies to modules pytest imports;
  helper assertions in a non-test package get the plain `AssertionError` unless you register them.
- Session-scoped fixtures save minutes when the resource is a loaded model or a container, but any
  mutable state they hold leaks between tests; the saving flips into flakiness the moment one test
  writes to the shared object.
- Plugins give you parallelism, coverage and async support for free, but each adds configuration
  interactions (xdist and session fixtures, asyncio loop scopes) that you must understand when a test
  passes alone and fails in the suite.

## Pitfalls
- Running tests in the wrong lane because a marker was misspelled; the symptom is an `e2e` test
  hitting a real API on every pull request. `--strict-markers` turns this into a collection error.
- Tests that pass in isolation and fail together, usually a shared fixture mutated by an earlier test;
  `pytest-randomly` or `-p no:cacheprovider` plus running the pair alone exposes the order dependence.
- Asserting on floating-point results with `==`; embedding similarities and loss values differ in the
  last bits across BLAS builds. The fix is `pytest.approx(expected, rel=1e-6)`.
- Catching too broadly with `pytest.raises(Exception)`, so a `TypeError` from a typo passes as the
  intended `ValueError`; use the specific class and `match=` on the message.
- Leaving `print` debugging in tests and wondering where it went: pytest captures stdout and only
  shows it for failures. `-s` disables capture; `capsys` asserts on it.

## When to use
Use pytest for every Python test, including the unittest-style classes you inherit; there is no
scenario in modern Python where a different runner is the better default. What you decide is how to
structure the suite: which fixtures live in the root `conftest.py` (fake LLM, settings override,
temporary directory layout) versus a package-local one, which resources deserve session scope, and
which markers split fast unit tests from slower integration and model-calling lanes. Measure the
suite's wall time with `--durations` and keep the pull-request lane under a few minutes, because a
slow suite is one people stop running. In interviews, the expected depth is being able to explain
assertion rewriting, fixture scopes and the `conftest.py` lookup, and to sketch a parametrized test
for a parser of model output on the spot.

## Role lens
- **Engineer:** Writes parametrized tests for chunkers, prompt builders and output parsers, keeps a
  `fake_llm` fixture in `conftest.py`, and splits lanes with markers so unit tests stay fast.
- **Architect:** Sets the suite's shape: which dependencies are allowed in the unit lane, the marker
  taxonomy, time budgets per lane, and the plugins the whole organisation standardises on.
- **FDE:** Lands inside a customer repo with an unfamiliar suite and uses `--collect-only`,
  `--fixtures` and `-k` to find the tests covering the integration they are changing before touching
  code.

## Check yourself
**Q:** A colleague argues that `unittest` is "good enough" and pytest is just syntax sugar. Using the
mechanics rather than taste, what does pytest provide that changes how a test suite is built?
**A:** Two mechanisms, not sugar. First, assertion rewriting: pytest rewrites the AST of `assert`
in test modules at import time so a bare comparison reports the intermediate values, which removes
the need for a dozen assertion methods and makes failures diagnosable without a debugger. Second,
fixtures as dependency injection with scopes: a test names what it needs, pytest resolves a graph of
fixtures with function, module or session lifetimes and runs teardown after `yield`, so expensive
resources (a loaded tokenizer, a container) are built once and composed rather than duplicated in
`setUp` methods. Add parametrization, which turns a table of cases into individually identifiable
tests, and a hook system that lets plugins own concerns like async loops and parallel workers. Those
change the economics of writing tests, which is why suites built on pytest have more cases.

## Quiz
### Q: Why can pytest print the values inside a failing plain `assert` statement?
- [ ] Python's `AssertionError` always carries the evaluated sub-expressions
- [x] pytest rewrites the `assert` statements of test modules at import time into code that records intermediate values
- [ ] pytest runs every test under `sys.settrace` and reconstructs the expression from the trace
- [ ] The values come from the fixture that produced them, which pytest tracks by name
> Plain Python raises a bare `AssertionError`. pytest's import hook rewrites the AST of `assert` in
> collected test modules so the failure message includes each operand; tracing would be far too slow
> and could not see inside a single expression.

### Q: A session-scoped fixture declares a dependency on a function-scoped fixture. What happens?
- [ ] It works; the function-scoped fixture is rebuilt for each test and injected into the shared one
- [ ] It works but pytest emits a warning and caches the first value
- [x] pytest raises a `ScopeMismatch` error at setup time
- [ ] The session fixture is silently downgraded to function scope
> A session-wide object is built once and cannot be rebuilt per test, so it cannot depend on something
> that changes per test. pytest refuses the graph with `ScopeMismatch` rather than guessing a lifetime.

### Q: A test function carries two stacked `parametrize` decorators with 3 and 4 cases. How many tests run?
- [ ] 4, because the inner decorator takes precedence
- [ ] 7, one per case across both decorators
- [x] 12, the Cartesian product of the two case lists
- [ ] It fails at collection because stacked parametrization is not allowed
> Each `parametrize` multiplies the existing set of test instances by its own cases, so stacking
> produces every combination. Use a single decorator with explicit tuples when only specific pairs
> are meaningful.
