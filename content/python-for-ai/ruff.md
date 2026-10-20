---
id: ruff
module: python-for-ai
name: ruff
kind: tool
difficulty: intro
est_minutes: 12
status: fact-checked
related:
  - python-for-ai/mypy
  - python-for-ai/pytest
  - python-for-ai/uv-poetry-pip
  - python-for-ai/security-scanning-and-dependency-auditing
  - python-for-ai/asyncio
  - backend-fundamentals/ci-cd
sources:
  - Official docs: Ruff
  - Official docs: flake8-bugbear
---

## TL;DR
Ruff is a Python linter and formatter written in Rust that replaces a stack of separate tools (flake8 and
its plugins, isort, pyupgrade, most of bandit's checks and a subset of pylint's, and Black for formatting) with one
fast binary and one config section. It exists because a slow, fragmented lint toolchain gets skipped;
fast enough to run on every save makes it a habit. It checks style and likely bugs, not types.

## Why it exists
A typical Python project used to run five or six tools on every commit: flake8 for errors and style,
several flake8 plugins (bugbear for likely bugs, comprehensions, simplify), isort for import order,
pyupgrade for modern syntax, Black for formatting, sometimes pylint and bandit. Each had its own config
file or section, its own version pins, its own ideas about line length, and its own parse of every file.
On a large repository the combination took long enough that people disabled the pre-commit hook and let
CI find problems, which means finding them minutes later instead of while the code is still on screen.

This matters more than it sounds in AI code, because some lint rules catch real defects, not style.
A blocking `time.sleep` or `requests.get` inside an `async def` stalls every concurrent request in the
server. An `asyncio.create_task` whose result is not stored can be garbage-collected mid-flight. A mutable
default argument shares state across calls. SQL built with f-strings is an injection risk when a tenant
ID comes from a request. A blind `except Exception` swallows unexpected errors and hides
failures. All of these are cheap to detect statically and expensive to debug in production.

The naive alternative, "code review will catch it", does not scale: reviewers miss mechanical issues,
and review time spent on import order is time not spent on design.

## How it works
### One parse, many rules
Ruff parses each file once into an AST and token stream and runs every enabled rule over that single
representation, in parallel across files, with a per-file cache so files unchanged since the last run
(under the same settings) are skipped. Native Rust code and that architecture together explain the
speed: the old toolchain ran each Python tool as its own process and re-parsed each file once per tool. The maintainers publish benchmarks showing large speedups
over flake8; treat any specific multiple as illustrative and measure on your own repository.

### Rules are namespaced by origin
Each rule has a code whose prefix names the tool it re-implements: `F` (pyflakes: unused imports,
undefined names), `E`/`W` (pycodestyle), `I` (isort), `B` (bugbear), `UP` (pyupgrade), `S` (bandit
security checks), `ASYNC` (flake8-async), `PT` (pytest style), `SIM`, `C4`, plus `RUF` for Ruff's own
rules. You enable by prefix (`select = ["B"]`) or single code. The default set is deliberately small,
covering pyflakes and a subset of pycodestyle errors, because a linter that floods a new project with
warnings gets switched off. `ruff rule B006` prints the rationale for any rule.

### Fixes
Many rules have autofixes, split into **safe** fixes (behaviour-preserving, applied by `ruff check
--fix`) and **unsafe** ones (may change behaviour, such as removing an import with side effects),
applied only with `--unsafe-fixes`. The split exists because a tool that silently changes semantics
cannot be trusted in a pre-commit hook.

### Formatter
`ruff format` is designed as a near drop-in replacement for Black's style, so teams can switch without
reformatting debates. Linting and formatting are separate commands; when the formatter owns layout,
disable lint rules that fight it (line length is the usual one).

### Configuration
Everything lives in `pyproject.toml` (or `ruff.toml`): `[tool.ruff]` for shared settings such as
`target-version` and `line-length`, `[tool.ruff.lint]` for `select`, `ignore` and `per-file-ignores`.
Inline `# noqa: B006` suppresses one rule on one line, and Ruff can flag `noqa` comments that no
longer suppress anything. What Ruff does not do is type checking: it does not know that a value may be
`None`, which is mypy's or pyright's job, so the two run side by side.

## Example
A configuration for an async AI service, then a snippet with the findings Ruff reports under it. The
comments name the rule codes; each is a real defect class, not cosmetics.

```toml
[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC", "S", "RUF"]
ignore = ["E501"]                          # the formatter owns line length

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]                      # assert is the point of a test
```

```python
import asyncio, json, time                 # F401 json unused; I001 import block unsorted (E401 too)
from typing import List                    # UP035 deprecated typing alias import
import requests

async def log_done(n: int) -> None: ...

async def fetch_all(urls: List[str], seen: set = set()) -> list:   # UP006 List; B006 mutable default
    results = []
    for url in urls:
        if url in seen:
            continue
        time.sleep(0.1)                    # ASYNC251 blocks the event loop for every request
        r = requests.get(url, timeout=10)  # ASYNC210 blocking HTTP call inside async def
        results.append(r.json())
    asyncio.create_task(log_done(len(results)))   # RUF006 task reference not kept
    return results

def docs_for(cur, tenant: str):
    try:
        cur.execute(f"SELECT * FROM docs WHERE tenant = '{tenant}'")   # S608 SQL from string
    except Exception as e:                 # F841 e unused
        raise RuntimeError("query failed")   # B904 re-raise without `from e`
```

`ruff check --fix` resolves the import, `List` and some other findings safely; the async, SQL and
default-argument findings need a human, because the fix is a design change (an async client, a
parameterised query, a `None` default).

## Trade-offs
- One fast tool replaces a fragmented chain and runs on every save, but you depend on Ruff's
  re-implementations; a niche flake8 plugin or pylint check it lacks still needs the original tool.
- Selecting broad rule families catches more real bugs, but floods legacy code with findings; start
  with `F`, `B`, `ASYNC`, `S` and grow the selection once the backlog is cleared.
- Autofix in pre-commit removes mechanical review churn, but unsafe fixes can change behaviour; keep
  unsafe fixes manual and reviewed, which flips only for rules you have audited.
- Ruff's formatter matches Black closely enough to switch in place, but small output differences mean
  one large reformatting commit; do it once, then add that commit to the blame-ignore list.

## Pitfalls
- Running only the default rules and assuming the code is checked; the symptom is blocking calls in
  async handlers and mutable defaults shipping, because `ASYNC` and `B` were never enabled.
- Blanket `# noqa` without a code; the symptom is a line that silently suppresses every future
  finding, including the real bug someone adds to it later.
- Different Ruff versions locally, in pre-commit and in CI; the symptom is findings that appear only in
  CI or fixes that flip back and forth between machines, so pin one version everywhere.
- Treating a clean Ruff run as type safety; the symptom is `None` attribute errors in production in code
  Ruff passed, because Ruff does not reason about types.
- Enabling both a line-length lint rule and the formatter with different limits; the symptom is
  findings the formatter can never fix, and contributors adding `noqa` comments to get past them.

## When to use
Use Ruff in every Python project, from the first commit: it is cheap enough to run in the editor, in a
pre-commit hook and in CI with the same pinned version. Choose rule families deliberately: `F` and `E`
for correctness, `B` and `RUF` for likely bugs, `ASYNC` for any service built on asyncio, `S` for code
that touches SQL, subprocesses or secrets, `UP` to keep syntax current with your minimum Python. Run it
next to mypy, not instead of it, and next to dependency auditing, which it does not do. In interviews, a
good answer explains which rules catch production defects in async AI services, not just that Ruff is fast.

## Role lens
- **Engineer:** Fixes findings as they appear in the editor and knows why `ASYNC`, `B006` and `RUF006`
  matter in an event-loop service, rather than silencing them.
- **Architect:** Owns the shared rule selection and pinned version across repositories, so every
  service enforces the same correctness and security baseline in CI.
- **FDE:** Runs Ruff on a customer's existing Python integration code on day one to surface blocking
  calls, injection risks and swallowed errors before building on top of it.

## Check yourself
**Q:** An async FastAPI service for LLM calls has latency spikes under load, yet CPU is low. The team
already runs Ruff with its default rules. How could Ruff have helped, and what are its limits here?
**A:** Low CPU with spikes under concurrency suggests the event loop is being blocked: a `time.sleep`,
a sync HTTP client or a sync database driver called inside `async def`. The default rule set does not
include the flake8-async family, so enabling `ASYNC` would flag blocking sleeps and HTTP calls inside
coroutines, and `RUF006` would flag fire-and-forget tasks. The limits: Ruff only recognises known
blocking calls by name; a sync call hidden behind your own helper, or a CPU-heavy function such as
tokenising a huge document, is invisible to it. You confirm with asyncio debug mode or profiling.

## Quiz
### Q: What mainly explains Ruff's speed advantage over the older multi-tool lint chain?
- [ ] It skips checking files larger than a fixed size
- [x] It parses each file once and runs all rules on that parse, in parallel, with caching
- [ ] It only runs rules on lines changed in the last commit
- [ ] It uses type inference to prune rules that cannot apply
> The old chain re-parsed every file once per tool. Ruff shares one parse across all rules, runs files
> in parallel and caches unchanged files; it does not do type inference.

### Q: Which issue is Ruff not designed to catch?
- [ ] A blocking time.sleep call inside an async function
- [ ] A mutable list used as a default argument
- [x] Calling .strip() on a value whose declared type is str | None
- [ ] An SQL query assembled with an f-string
> Optional-type errors need a type checker such as mypy or pyright. The other three are covered by
> Ruff's ASYNC, bugbear and bandit-derived rule families when those are enabled.

### Q: Why does Ruff separate safe fixes from unsafe fixes?
- [ ] Unsafe fixes are slower to compute
- [ ] Safe fixes only apply to formatting, never to lint findings
- [x] Unsafe fixes may change program behaviour, so they are not applied by default
- [ ] Unsafe fixes require an internet connection to download rule data
> A fix such as removing an import with side effects can alter runtime behaviour. Keeping such fixes
> opt-in lets `--fix` run automatically in hooks without silently changing semantics.
