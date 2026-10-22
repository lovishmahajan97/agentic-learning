---
id: ruff
module: python-for-ai
name: ruff
kind: tool
difficulty: intro
est_minutes: 12
status: agentic
agent_relevance: useful
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
Ruff is a fast Rust-written Python linter and formatter replacing flake8 and its plugins, isort,
pyupgrade, most of bandit, part of pylint, and Black. For agents its value is catching blocking calls,
lost tasks and unsafe tool code; it checks likely bugs, not types.

## For agent builders
- **Why it matters for agents.** Agent tools are where model-chosen input meets your process: a shell
  tool with `shell=True`, a calculator built on `eval`, a sync HTTP call inside an async tool, a memory
  write fired as an unreferenced task. Ruff flags each by rule code before the model ever reaches it.
- **Where it shows up in your stack.** One `[tool.ruff.lint]` block in `pyproject.toml`, the editor, a
  pre-commit hook and CI, all on one pinned version, next to mypy and pytest.
- **What you control.** The rule families: `ASYNC` for async agent services, `S` for tool code that runs
  processes, SQL or `eval`, `B` and `RUF` for likely bugs. Also which findings are fixed automatically and
  which need a design change.
- **What you can skip.** Style debates and the full rule catalogue; `ruff rule <code>` explains any rule
  when it appears.

## Why it exists
- **The old toolchain was five or six tools.** flake8 for errors and style, several flake8 plugins
  (bugbear for likely bugs, comprehensions, simplify), isort for import order, pyupgrade for modern
  syntax, Black for formatting, sometimes pylint and bandit.
- **Each had its own config, pins and parse.** Each had its own config file or section, its own version
  pins, its own ideas about line length, and its own parse of every file.
- **Slow, fragmented linting gets skipped.** On a large repository people disabled the pre-commit hook and
  let CI find problems, minutes later instead of while the code is still on screen. Fast enough to run on
  every save makes it a habit.
- **In AI code some lint rules catch real defects, not style:**
  - a blocking `time.sleep` or `requests.get` inside an `async def` stalls every concurrent request;
  - an `asyncio.create_task` whose result is not stored can be garbage-collected mid-flight;
  - a mutable default argument shares state across calls;
  - SQL built with f-strings is an injection risk when a tenant ID comes from a request;
  - a blind `except Exception` swallows unexpected errors and hides failures.
  All are cheap to detect statically and expensive to debug in production.
- **"Code review will catch it" does not scale:** reviewers miss mechanical issues, and review time spent
  on import order is time not spent on design.

## How it works
### One parse, many rules
1. **Parse once:** each file becomes an AST and token stream.
2. **Run every enabled rule** over that single representation.
3. **In parallel across files.**
4. **With a per-file cache**, so files unchanged since the last run (under the same settings) are skipped.

- **Why it is fast:** native Rust code plus that architecture; the old toolchain ran each Python tool as
  its own process and re-parsed each file once per tool.
- **Benchmarks:** the maintainers publish benchmarks showing large speedups over flake8; treat any
  specific multiple as illustrative and measure on your own repository.

### What it can and cannot see
- **One file at a time, with a semantic model** of that file's scopes, bindings and imports: enough to
  know that `requests.get` refers to the `requests` package, or that a name is unused.
- **No cross-module type inference.** That is why Ruff recognises a blocking call by its qualified name
  but cannot follow it through your own wrapper function in another module.
- **Hence no whole-program pylint checks:** those that depend on whole-program inference are not among
  the ones it re-implements.

### Rules are namespaced by origin
Each rule code's prefix names the tool it re-implements.

| Prefix | Origin | Catches |
|---|---|---|
| `F` | pyflakes | unused imports, undefined names |
| `E`/`W` | pycodestyle | style errors and warnings |
| `I` | isort | import order |
| `B` | bugbear | likely bugs |
| `UP` | pyupgrade | outdated syntax |
| `S` | bandit | security checks |
| `ASYNC` | flake8-async | blocking calls in coroutines |
| `PT` | pytest style | test conventions |
| `SIM`, `C4` | simplify, comprehensions | simpler equivalent code |
| `RUF` | Ruff's own rules | e.g. dangling tasks, stale `noqa` |

- **Enable by prefix** (`select = ["B"]`) or single code; `extend-select` adds to the inherited selection
  instead of replacing it.
- **Preview gate.** Newly added rules start in **preview** and run only with `preview = true`, so
  upgrading Ruff does not silently add findings to your CI; rules graduate to stable in later releases.
- **Small default set**, covering pyflakes and a subset of pycodestyle errors, because a linter that
  floods a new project with warnings gets switched off.
- **`ruff rule B006`** prints the rationale for any rule.

### Fixes
- **Safe fixes** are behaviour-preserving and applied by `ruff check --fix`.
- **Unsafe fixes** may change behaviour (such as removing an import with side effects) and are applied
  only with `--unsafe-fixes`.
- **Why the split:** a tool that silently changes semantics cannot be trusted in a pre-commit hook.

### Formatter
- **`ruff format`** is designed as a near drop-in replacement for Black's style, so teams can switch
  without reformatting debates.
- **Separate commands:** linting and formatting are run separately; when the formatter owns layout,
  disable lint rules that fight it (line length is the usual one).

### Configuration
- **One place:** `pyproject.toml` (or `ruff.toml`). `[tool.ruff]` holds shared settings such as
  `target-version` and `line-length`; `[tool.ruff.lint]` holds `select`, `ignore` and `per-file-ignores`.
- **Suppressions:** inline `# noqa: B006` suppresses one rule on one line, and `RUF100` flags `noqa`
  comments that no longer suppress anything, so suppressions do not outlive the code they excused.
- **Not a type checker:** Ruff does not know that a value may be `None`, which is mypy's or pyright's job,
  so the two run side by side.

### Rules that map to agent incidents
| Agent incident | Typical code | Rule (family) |
|---|---|---|
| One slow tool stalls every concurrent agent run | `requests.get` or `subprocess.run` inside an async tool | `ASYNC210`, `ASYNC221` (`ASYNC`) |
| A background memory or trace write vanishes | `asyncio.create_task(...)` with no stored reference | `RUF006` (`RUF`) |
| Prompt injection becomes command execution | shell tool using `shell=True` | `S602` (`S`) |
| Model-written "math" runs arbitrary code | calculator tool calling `eval` | `S307` (`S`) |
| Tool failures hidden from the model | `except Exception: return ""` | `BLE001` (`BLE`, not in the baseline below) |
| Tenant data leaks across agents | SQL tool built with f-strings | `S608` (`S`) |

- **Rules are a floor, not a sandbox.** A lint rule catches `eval` and `shell=True` by name; isolating
  what a tool can reach is still the sandbox's and permission layer's job.

## Diagram
Follow one `ruff check` run: a single parse per file feeds every enabled rule, and each finding ends in a
safe fix, an opt-in unsafe fix, or a human decision.

```mermaid
flowchart TD
    F["Python file"] --> CH{"Unchanged since last run, same settings?"}
    CH -->|"yes"| SKIP["Skip, reuse cached result"]
    CH -->|"no"| P["One parse: AST plus tokens"]
    P --> SM["Per-file semantic model: scopes, bindings, imports"]
    CFG["pyproject.toml: select, extend-select, ignore, preview"] --> R
    SM --> R["Run every enabled rule, files in parallel"]
    R --> FD["Findings with codes, e.g. B006, ASYNC251, S608"]
    FD -->|"safe fix"| FX["Applied by ruff check --fix"]
    FD -->|"unsafe fix"| UF["Only with --unsafe-fixes, reviewed"]
    FD -->|"design change needed"| H["Human: async client, parameterised query, None default"]
    FX --> FMT["ruff format lays out the result"]
```

## Examples
### Agent scenario: linting a tool module
Four tools an agent can call, linted with the configuration in the next example. Findings below are
from `ruff check --output-format concise tools.py` on Ruff 0.17.1.

```python
import asyncio
import subprocess

import requests


async def save_memory(note: str) -> None: ...


async def run_shell(command: str) -> str:
    out = subprocess.run(command, shell=True, capture_output=True, text=True)  # ASYNC221, S602
    asyncio.create_task(save_memory(command))                                  # RUF006
    return out.stdout


async def fetch_page(url: str) -> str:
    return requests.get(url, timeout=10).text                                  # ASYNC210


def calculator(expression: str) -> float:
    return eval(expression)                                                    # S307


def read_file(path: str) -> str:
    try:
        return open(path).read()
    except Exception:                      # BLE001 only if you add the BLE family
        return ""
```

| Finding | Why it matters in an agent | Fix |
|---|---|---|
| `S602` | the model chooses `command`, so injected text runs in a shell | argument list without a shell, an allowlist, a sandbox |
| `ASYNC221`, `ASYNC210` | each call blocks the loop for every concurrent run | `asyncio.create_subprocess_exec`, an async HTTP client |
| `RUF006` | the memory write can be garbage-collected mid-flight | keep the task in a set or a `TaskGroup` |
| `S307` | model-written expressions execute as Python | a restricted parser such as `ast.literal_eval` or a math library |

- **Five findings, all design changes:** none has a safe autofix, which is why tool modules deserve the
  `S` and `ASYNC` families even when the rest of the repo uses a lighter selection.

### Config and snippet for an async AI service
A configuration, then a snippet with the findings Ruff reports under it. The comments name the rule
codes; each is a real defect class, not cosmetics.

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

- **What `--fix` handles:** `ruff check --fix` resolves the import, `List` and some other findings safely.
- **What needs a human:** the async, SQL and default-argument findings, because the fix is a design
  change (an async client, a parameterised query, a `None` default).

### Worked run: what Ruff actually reported
Running `ruff check svc.py` with the config above (Ruff 0.17.1) gives 12 findings. `ruff check --fix`
then fixes 6 and leaves 6, and reports 1 more fix available only with `--unsafe-fixes`:

| Group | Codes | Who fixes it |
|---|---|---|
| Imports and syntax | `E401`, `I001`, `F401`, `UP006` | `--fix`, safely |
| Unused binding | `F841` | `--fix` drops `as e` |
| Deprecated import | `UP035` | gone after `--fix`, because the now-unused `List` import is removed |
| Mutable default | `B006` | unsafe fix offered (a `None` default plus `if seen is None`); review it |
| Likely bugs | `B904`, `RUF006` | human: `raise ... from e`, keep a task reference |
| Event-loop blockers | `ASYNC251`, `ASYNC210` | human: `await asyncio.sleep`, an async HTTP client |
| Security | `S608` | human: parameterised query |

Half of the findings are design decisions, which is why lint findings belong in review, not in a silent
autofix job.

### Hook and CI order
In a pre-commit hook, run `ruff check --fix` before `ruff format`, because a lint fix (removing an
import, rewriting a comprehension) can leave code the formatter would lay out differently. In CI, run
`ruff check` and `ruff format --check`; neither writes files, so CI fails on findings instead of quietly
fixing them in a throwaway checkout.

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.17.1               # same version as CI and the editor
    hooks:
      - id: ruff-check
        args: [--fix]          # lint fixes first
      - id: ruff-format        # then layout
```

## Production patterns
- **One pinned version everywhere.** Pin Ruff in the dev dependencies, use the same `rev` in the
  pre-commit hook, and run that version in CI, so findings and fixes do not flip between machines.
  Monitor CI-only findings, which signal version drift.
- **Shared baseline rule selection.** Keep one `[tool.ruff.lint]` block (for example `E`, `F`, `I`, `B`,
  `UP`, `ASYNC`, `S`, `RUF`) across services, and use `extend-select` for per-repo additions so the
  baseline is not silently replaced. Review it when Ruff graduates preview rules to stable.
- **Baseline a legacy repo, then burn it down.** Enable the families you want, run
  `ruff check --add-noqa` once to suppress existing findings with per-code comments, and keep `RUF100` on
  so comments are removed as code is fixed. Track the count of `noqa` comments over time.
- **CI annotations, no writes.** Run `ruff check --output-format github` and `ruff format --check` in the
  pipeline, so findings appear inline on the pull request and nothing is fixed in a throwaway checkout.
- **Stricter rules for tool code.** Agent tools run on model-chosen input, so give the tools package the
  full `S`, `ASYNC` and `BLE` families with a nested config in that directory that inherits the root one
  and uses `extend-select`, and require review of any `noqa` there.
  Monitor `noqa` comments in the tools package separately from the rest of the repo.
- **Triage with statistics.** Before enabling a new family, run `ruff check --select ASYNC --statistics`
  to see counts per rule, fix the defect classes first (`ASYNC`, `B006`, `RUF006`, `S608`), then enable
  the family as an error.

## Trade-offs
- **One fast tool vs re-implementations.** It replaces a fragmented chain and runs on every save, but you
  depend on Ruff's re-implementations; a niche flake8 plugin or pylint check it lacks still needs the
  original tool.
- **Broad rule families vs legacy noise.** Selecting broad families catches more real bugs, but floods
  legacy code with findings; start with `F`, `B`, `ASYNC`, `S` and grow the selection once the backlog is
  cleared.
- **Autofix in pre-commit vs behaviour changes.** Autofix removes mechanical review churn, but unsafe
  fixes can change behaviour; keep unsafe fixes manual and reviewed, which flips only for rules you have
  audited.
- **Formatter switch vs one big diff.** Ruff's formatter matches Black closely enough to switch in place,
  but small output differences mean one large reformatting commit; do it once, then add that commit to
  the blame-ignore list.

## Pitfalls
- **Running only the default rules and assuming the code is checked.** Symptom: blocking calls in async
  handlers and mutable defaults shipping, because `ASYNC` and `B` were never enabled.
- **Blanket `# noqa` without a code.** Symptom: a line that silently suppresses every future finding,
  including the real bug someone adds to it later.
- **Different Ruff versions locally, in pre-commit and in CI.** Symptom: findings that appear only in CI or
  fixes that flip back and forth between machines. Fix: pin one version everywhere.
- **Treating a clean Ruff run as tool safety.** Symptom: a tool that passes lint because it calls a
  wrapper around `subprocess` defined in another module, which Ruff cannot follow. Fix: sandbox and
  allowlist tools regardless, and keep dangerous calls inside one reviewed module.
- **Treating a clean Ruff run as type safety.** Symptom: `None` attribute errors in production in code
  Ruff passed, because Ruff does not reason about types.
- **A line-length lint rule and the formatter with different limits.** Symptom: findings the formatter can
  never fix, and contributors adding `noqa` comments to get past them.

## When to use
- **Use in every Python project, from the first commit:** it is cheap enough to run in the editor, in a
  pre-commit hook and in CI with the same pinned version.
- **Choose rule families deliberately:** `F` and `E` for correctness, `B` and `RUF` for likely bugs,
  `ASYNC` for any service built on asyncio, `S` for code that touches SQL, subprocesses or secrets, `UP`
  to keep syntax current with your minimum Python.
- **Run it next to mypy, not instead of it**, and next to dependency auditing, which it does not do.
- **Interviews:** a good answer explains which rules catch production defects in async AI services, not
  just that Ruff is fast.

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

## Interview questions
### Why is Ruff so much faster than the flake8-based toolchain it replaces?
- **One parse per file:** every enabled rule runs over a single AST and token stream, where the old chain
  ran each Python tool as its own process and re-parsed every file once per tool.
- **Parallel and cached:** files are linted in parallel, and unchanged files under the same settings are
  skipped.
- **Native Rust code** adds the rest; quote your own repository's measurement, not a published multiple.

### Which Ruff rules would you enable for an async LLM service, and why those?
- **`ASYNC`:** flags blocking sleeps and HTTP calls inside `async def`, which stall every concurrent
  request; the default set does not include it.
- **`B` and `RUF`:** mutable defaults (`B006`), lost exception context (`B904`) and fire-and-forget tasks
  that can be garbage-collected (`RUF006`).
- **`S`:** SQL built from strings (`S608`) and other bandit checks, plus the defaults `E` and `F`, `I` for
  imports and `UP` for current syntax.

### What would Ruff flag in an agent's tool implementations, and what would it miss?
- **It flags dangerous calls by name:** `S602` for a shell tool with `shell=True`, `S307` for `eval` in a
  calculator tool, `ASYNC210` and `ASYNC221` for blocking calls in async tools, `RUF006` for
  unreferenced background tasks.
- **It misses anything indirect:** a dangerous call behind your own helper in another module, or a tool
  that is safe in code but has too broad a permission.
- **So pair it with a sandbox and an allowlist**, and treat its findings in tool code as review blockers.

### You turn on ASYNC and B in a legacy repo and get 900 findings. How do you roll it out?
- **Triage first** with `ruff check --statistics`, and fix the defect classes that matter in production
  (blocking calls, dangling tasks, SQL injection) before cosmetic ones.
- **Baseline the rest** with `ruff check --add-noqa`, which writes per-code `noqa` comments, so CI can
  gate new code immediately.
- **Burn down** with `RUF100` enabled, so each suppression disappears once its line is fixed, and track the
  `noqa` count.

### What can Ruff not catch, and what do you pair it with?
- **Types:** it does not know a value may be `None`, so `.strip()` on `str | None` needs mypy or pyright.
- **Cross-module behaviour:** a blocking call hidden behind your own helper in another module, or a
  CPU-heavy function in a coroutine, is invisible because rules see one file at a time.
- **Dependencies:** vulnerable packages need a dependency auditor; runtime blocking is confirmed with asyncio
  debug mode or profiling.

### When would you not apply Ruff's fixes automatically?
- **Unsafe fixes:** they may change behaviour, such as removing an import with side effects, so they stay
  behind `--unsafe-fixes` and get reviewed.
- **In CI:** run `ruff check` and `ruff format --check`, which never write files, so findings fail the build
  instead of being fixed in a throwaway checkout.
- **Design findings:** async, SQL and default-argument findings have no autofix because the fix is a design
  change.

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
