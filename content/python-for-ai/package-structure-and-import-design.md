---
id: package-structure-and-import-design
module: python-for-ai
name: Package structure & import design
kind: practice
difficulty: core
est_minutes: 14
related:
  - python-for-ai/dependency-injection-and-interface-boundaries
  - python-for-ai/protocols-generics-structural-typing
  - python-for-ai/uv-poetry-pip
  - python-for-ai/pytest
  - python-for-ai/ruff
  - python-for-ai/mypy
  - agent-reliability/tool-adapters-service-interfaces
sources:
  - Official docs: Python import system (Python Language Reference)
  - PEP 420 — Implicit Namespace Packages
  - PEP 562 — Module __getattr__ and __dir__
  - Official docs: Python Packaging User Guide (src layout vs flat layout)
---

## TL;DR
In Python, the import graph is your architecture: whatever imports what decides which code can be
tested alone, what runs at startup and what breaks when a dependency changes. Use a `src/` layout so
tests exercise the installed package, keep imports pointing one way (domain ← services ← adapters ←
entrypoints), keep side effects out of import time, and import heavy SDKs and model libraries lazily or
only at the edges.

## Why it exists
AI codebases usually start as one notebook, then a `utils.py`, then forty modules where everything imports
everything. Three failures follow. First, **circular imports**: `agent.py` imports `tools.py`, which imports
`agent.py` for a type, and one day Python raises `ImportError: cannot import name 'Agent' from partially
initialized module`. Second, **slow, fragile startup**: importing your package pulls in `torch`,
`transformers` and three vendor SDKs, so a CLI that only lists prompts takes seconds to start, and a
module-level `client = OpenAI()` fails at import time when `OPENAI_API_KEY` is missing, which also breaks
test collection. Third, **tests that pass for the wrong reason**: with a flat layout, `pytest` imports the
package from the working directory rather than from what `pip install` would ship, so a missing file in the
wheel or a wrong package-data declaration is invisible until production.

The naive fix, "just be careful", does not scale because nothing enforces it. Import structure has to be
designed, given a direction, and checked by a tool, the same way you would design service boundaries.

## How it works
### What an import actually does
`import pkg.mod` looks in `sys.modules`; if absent, it walks `sys.path` for `pkg`, creates the module object,
**inserts it into `sys.modules` before executing its body**, then runs the module top to bottom. Two
consequences drive every rule below. A module's body runs once per process, at first import, so anything at
top level (network clients, file reads, model loading, `logging.basicConfig`) is an import-time side effect.
And because the half-built module is already registered, a cycle does not loop forever: the second module
gets the partially initialized first one, and `from a import X` fails if `X` has not been defined yet.

### src layout
Put code in `src/myapp/` with `tests/` beside `src/`, and install it in editable mode (`uv sync`, or
`pip install -e .`). Because the project root no longer contains an importable `myapp` directory, tests can
only import the installed package. This removes the "works in the repo, broken in the wheel" class of bug.
A flat layout (`myapp/` at the root) is fine for scripts and small tools, but it lets the current directory
shadow the installed package.

### Layering and direction
Pick layers and allow imports only downward:
- `domain/` — pure types and logic (Pydantic models, prompt templates, scoring). Imports nothing from the app.
- `services/` — orchestration (the RAG pipeline, the agent loop). Depends on domain and on *interfaces*
  (Protocols) for LLMs, vector stores and tools.
- `adapters/` — concrete clients (OpenAI, Anthropic, pgvector, Redis). Implement those Protocols.
- `entrypoints/` — FastAPI app, CLI, workers. The composition root that wires adapters into services.

The direction is chosen *because* the parts that change most (vendor SDKs) should be the parts nothing else
depends on. A tool such as `import-linter` can turn this into a CI check with "layers" and "forbidden"
contracts, so a stray `from myapp.adapters.openai import ...` inside `domain/` fails the build.

### Breaking cycles properly
A cycle is a design signal: two modules share a concept that belongs in a third. Move the shared types down
into `domain/`. If the only reason for the import is a type annotation, guard it with
`if TYPE_CHECKING:` and use string or postponed annotations, because type checkers read it but the runtime
never executes it. Function-local imports also break cycles, but use them deliberately, not as a habit.

### Public surface and lazy imports
`__init__.py` defines what callers see. Re-exporting a small API there (`from .client import Client`, plus
`__all__`) lets you reorganize internals without breaking users, but re-exporting everything makes
`import myapp` load everything. For heavy optional dependencies, import inside the function that needs them,
or use a module-level `__getattr__` (PEP 562) to defer loading until first attribute access.
`python -X importtime -c "import myapp"` prints per-module import cost, so you can see what to defer.

### Absolute vs relative imports
Absolute imports (`from myapp.domain import Query`) are explicit and survive copying a module elsewhere;
explicit relative imports (`from .models import Query`) are shorter and survive renaming the top package.
Either is fine; pick one style and let `ruff` enforce it. Implicit relative imports do not exist in Python 3.

## Example
A layered layout plus two techniques: a type-only import that removes a runtime cycle between the agent and
its tool registry, and an embedding adapter that defers the expensive `torch` import until the first call, so
importing the package (or collecting tests) stays fast and side-effect free.

```python
# src/myapp/
#   domain/models.py        <- pure Pydantic models, no app imports
#   services/agent.py       <- depends on domain + Protocols
#   adapters/embeddings.py  <- imports torch / sentence-transformers lazily
#   entrypoints/cli.py      <- composition root

# services/agent.py
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol
from myapp.domain.models import Message

if TYPE_CHECKING:                      # seen by mypy/pyright, never executed at runtime
    from myapp.services.tools import ToolRegistry

class LLM(Protocol):
    def complete(self, messages: list[Message]) -> Message: ...

class Agent:
    def __init__(self, llm: LLM, tools: ToolRegistry) -> None:
        self.llm, self.tools = llm, tools

# adapters/embeddings.py
class LocalEmbedder:
    def __init__(self, model_name: str) -> None:
        self.model_name, self._model = model_name, None   # nothing heavy at import

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # pays torch import on first use
            self._model = SentenceTransformer(self.model_name)
        return self._model.encode(texts).tolist()

# Measure: python -X importtime -c "import myapp.entrypoints.cli" 2> imports.log
```

## Trade-offs
- Strict layering makes adapters swappable and services testable with fakes, but adds indirection that feels
  like ceremony in a 500-line prototype; adopt it once a second adapter or a test suite appears.
- Lazy imports cut startup time and make optional extras optional, but move `ImportError` from startup to
  first use; for a long-running server, eager import at boot can be safer because it fails fast.
- A rich `__init__.py` gives users a tidy API, but couples import cost to the whole package; it flips toward
  minimal `__init__` files once the package contains heavy or optional dependencies.
- Splitting into many small packages (a monorepo with workspaces) isolates dependencies cleanly, but costs
  versioning and release overhead that a single package with clear layers usually does not need.

## Pitfalls
- Creating clients or loading models at module top level; the symptom is tests failing at collection with a
  missing API key, or a CLI `--help` that takes several seconds.
- Fixing a circular import by moving the import inside a function without fixing the shared concept; the
  symptom is the same cycle reappearing in a new form a few weeks later.
- Naming a module after a dependency (`openai.py`, `logging.py`, `datasets.py`) inside a directory on
  `sys.path`; the symptom is `AttributeError: module 'openai' has no attribute 'OpenAI'` because your file
  shadowed the library.
- Using a flat layout and never building the wheel in CI; the symptom is `ModuleNotFoundError` or missing
  prompt templates only after deployment, because package data was never declared.

## When to use
Apply these rules from the moment code leaves the notebook and gets a `pyproject.toml`. Use a src layout for
anything that is installed, deployed or shared; keep a flat layout for one-off scripts. Introduce explicit
layers when you have more than one implementation of an external dependency, or when tests start needing to
patch module globals to avoid network calls. Measure import time when startup matters (CLIs, serverless cold
starts, worker autoscaling). In code review, the question to ask is "which way does this import point?"; in
interviews, explaining why a cycle signals a misplaced concept, and how `sys.modules` makes a cycle fail
the way it does, shows real understanding.

## Role lens
- **Engineer:** Keeps vendor SDK imports in adapters, guards type-only imports, and runs `-X importtime`
  when a worker's cold start creeps up.
- **Architect:** Defines the layer contract and enforces it in CI, so the dependency direction survives team
  growth and vendor swaps.
- **FDE:** Restructures a customer's notebook-grown repo into an installable package that their platform team
  can deploy, test and own after handover.

## Check yourself
**Q:** Your FastAPI service imports `myapp`, and `myapp/__init__.py` re-exports everything. Cold starts on
your serverless platform take several seconds and unit tests fail without an `OPENAI_API_KEY`. What do you
change and why?
**A:** Both symptoms come from import-time work. Re-exporting everything means `import myapp` executes every
module, including ones that import `torch` and ones that construct SDK clients at top level. I would shrink
`__init__.py` to a small public API, move client construction into the composition root (the FastAPI lifespan
or a factory), and inject clients into services. Heavy libraries move behind function-local or PEP 562 lazy
imports. Then `-X importtime` confirms the cold-start gain, and tests run with fakes because nothing touches
the network or environment at import time.

## Quiz
### Q: Why does a circular import often fail with "cannot import name X from partially initialized module"?
- [ ] Python detects the cycle and refuses to load either module
- [x] The first module is registered in sys.modules before its body finishes, so the second gets it half-built
- [ ] Relative imports are not allowed inside packages that import each other
- [ ] The bytecode cache is invalidated when two modules reference each other
> Python inserts a module into sys.modules before executing it. The cyclic import returns that
> partially executed module, so names defined later in its body do not exist yet; Python does not refuse cycles outright.

### Q: What is the main reason to prefer a src layout for a deployable package?
- [ ] It makes imports run faster at startup
- [ ] It is required by pyproject.toml build backends
- [x] Tests import the installed package instead of the working-directory copy, exposing packaging mistakes
- [ ] It prevents circular imports between subpackages
> With src/, the repository root has no importable package directory, so tests use what installation
> provides. It does not affect import speed or cycles, and build backends support flat layouts too.

### Q: A type annotation is the only reason module A imports module B, and this creates a cycle. Best fix?
- [ ] Merge A and B into one module
- [ ] Replace the import with importlib.import_module at call time
- [x] Import B under `if TYPE_CHECKING:` and use postponed or string annotations
- [ ] Catch the ImportError and set the name to None
> TYPE_CHECKING is False at runtime, so the import exists only for type checkers and the runtime cycle
> disappears. Merging modules or dynamic imports work but are heavier changes than the cause needs.
