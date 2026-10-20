---
id: uv-poetry-pip
module: python-for-ai
name: uv / poetry / pip
kind: tool
difficulty: intro
est_minutes: 13
status: fact-checked
related:
  - python-for-ai/lockfiles-and-dependency-pinning
  - python-for-ai/security-scanning-and-dependency-auditing
  - python-for-ai/package-structure-and-import-design
  - python-for-ai/pytorch
  - backend-fundamentals/docker
  - backend-fundamentals/ci-cd
sources:
  - Official docs: pip
  - Official docs: Poetry
  - Official docs: uv (Astral)
  - PEP 621 — Storing project metadata in pyproject.toml
---

## TL;DR
pip installs packages; Poetry and uv manage whole projects: declared dependencies in `pyproject.toml`, a
resolved lockfile, virtual environments and builds. The reason to care is reproducibility: an AI service
depends on a deep, fast-moving stack (SDKs, torch, tokenizers), and "it worked on my machine" is almost
always two environments resolving to different versions. uv does what pip, pip-tools, virtualenv, pyenv and
pipx do, much faster, which is why many new projects now default to it.

## Why it exists
A Python project needs four things: a list of what it depends on, an exact resolved set of versions that
satisfies all constraints, an isolated environment holding those versions, and a way to build and publish
itself. pip historically did only the install step. It has a real resolver (it backtracks to satisfy
constraints), but out of the box it does not record the result in a lockfile, does not create environments,
and does not distinguish "what I asked for" from "what I ended up with". (A standard lockfile format,
`pylock.toml`, was only accepted as PEP 751 in 2025, and pip's `pip lock` command that writes it is still
marked experimental; most teams therefore still get locking from pip-tools, Poetry or uv.)

The naive workflow, `pip install` things into a venv and `pip freeze > requirements.txt`, mixes those
concerns. The freeze captures whatever happens to be installed, including leftovers from experiments, for
one OS and one Python version, with no record of which packages were direct requirements. Upgrading one
library means hand-editing pins and hoping the set is still consistent. Tools such as pip-tools, Poetry and
uv exist to separate *intent* (ranges in `pyproject.toml`) from *resolution* (an exact lockfile), and to make
recreating the environment one deterministic command.

## How it works
### pip (+ venv, + pip-tools)
`python -m venv .venv` creates an isolated interpreter environment; `pip install -r requirements.txt` resolves
and installs into it from PyPI or another index. pip prefers wheels (pre-built archives) and falls back to
building from source distributions, which is where compiler and CUDA errors come from. pip-tools adds
`pip-compile`, which turns a short `requirements.in` (or `pyproject.toml`) into a fully pinned, optionally
hashed `requirements.txt`, and `pip-sync`, which makes the environment match it exactly. This is the minimal,
widely understood baseline, and every other tool can export to it.

### Poetry
Poetry is a project manager: `poetry add httpx` updates `pyproject.toml`, re-resolves and writes
`poetry.lock`; `poetry install` creates or reuses a virtual environment and installs exactly what the lock
says; dependency groups separate dev and test tools; `poetry build` and `poetry publish` handle packaging.
Recent major versions also read standard PEP 621 `[project]` metadata rather than only its own
`[tool.poetry]` tables. Poetry's strength is a mature, integrated workflow for libraries and applications;
its historical weaknesses were resolution speed on large graphs and friction with non-PyPI wheel indexes.

### uv
uv, from Astral, is written in Rust and covers the whole toolchain:
- **Project workflow:** `uv init`, `uv add`, `uv lock` (writes `uv.lock`), `uv sync` (makes `.venv` match the
  lock), `uv run <cmd>` (syncs if needed, then runs inside the environment). In CI, `uv sync --locked` fails if
  the lock is out of date with `pyproject.toml`, and `--frozen` uses the lock without checking.
- **Universal lockfile:** `uv.lock` records resolutions for all platforms and Python versions the project
  supports, so one lock serves macOS laptops and Linux containers.
- **Python versions:** `uv python install 3.12` downloads interpreters, replacing pyenv for many teams.
- **pip-compatible interface:** `uv pip install`, `uv pip compile`, `uv pip sync` work as drop-ins for
  existing requirements-based projects, which makes adoption incremental.
- **Tools and scripts:** `uvx ruff` runs a CLI tool in a throwaway environment; `uv run script.py` honors
  inline script metadata (PEP 723) so a one-file script can declare its own dependencies.
- **Indexes:** per-package index pinning (`[tool.uv.sources]`) handles the classic AI pain of installing
  `torch` from a CUDA-specific index while everything else comes from PyPI.

uv is fast *because* of a parallel resolver and downloader, a global cache whose files are cloned
(copy-on-write, the default on macOS), hard-linked (the default on Linux) or copied into environments instead
of being re-downloaded and re-unpacked, and reading wheel metadata without fetching whole wheels
where indexes allow. The practical effect is that recreating an environment becomes cheap enough to do on
every CI run and in every Docker build.

## Example
The same project, three ways. The scenario: a FastAPI RAG service that needs `openai`, `httpx` and
`torch` (CPU wheels in CI), plus `pytest` as a dev dependency.

- **pip + pip-tools:** keep `requirements.in` with `openai>=1.40`, `httpx`, `torch`; run
  `pip-compile --generate-hashes requirements.in` to get a pinned, hashed `requirements.txt`; a separate
  `dev.in` with `-c requirements.txt` and `pytest`. CI runs `python -m venv .venv` then
  `pip install --require-hashes -r requirements.txt`. Two files per environment, and a Mac developer who
  needs different wheels may need a separately compiled file.
- **Poetry:** `poetry add openai httpx torch` and `poetry add --group dev pytest`; commit `pyproject.toml` and
  `poetry.lock`; CI runs `poetry install`. Pointing torch at the PyTorch CPU index needs a configured source
  and an explicit source on that dependency.
- **uv:** `uv init`, `uv add openai httpx torch`, `uv add --dev pytest`, an index entry for the PyTorch CPU
  wheel index marked `explicit = true`, with `torch` mapped to it in `[tool.uv.sources]`; commit
  `pyproject.toml` and `uv.lock`. CI runs `uv sync --locked` then `uv run pytest`. The Docker build copies the
  two files first and runs `uv sync --locked --no-dev`, so the dependency layer is cached until the lock
  changes.

In all three, the rule is the same: humans edit ranges, a tool writes the exact lock, and CI and production
install only from the lock.

## Trade-offs
- pip with pip-tools is the most portable and universally understood option, but you assemble venv, pinning,
  syncing and Python versions yourself; it wins when an organization's tooling is already built around
  requirements files.
- Poetry gives an integrated, mature workflow with good publishing support, but is slower on large graphs and
  adds its own conventions; it remains a reasonable choice for teams already standardized on it.
- uv is dramatically faster and replaces several tools, but is younger, and new features have evolved
  quickly; pin the uv version in CI so a tool upgrade does not change lock behaviour under you.
- A universal lockfile serves every platform from one file, but resolves for platforms you may never deploy
  to, which can surface conflicts (often around CUDA wheels) that a single-platform lock would never hit.

## Pitfalls
- Installing into the system or base conda interpreter instead of a project environment; the symptom is a
  package upgrade for one project breaking another, permission errors on `pip install`, or (on distro and
  Homebrew Pythons that adopt PEP 668) pip refusing with `externally-managed-environment`.
- Committing `pyproject.toml` but not the lockfile for an application; the symptom is CI and production
  resolving newer transitive versions than developers tested.
- Mixing tools in one project (running `pip install` inside a uv- or Poetry-managed venv); the symptom is an
  environment that drifts from the lock and gets silently reset or reports a different set on the next sync.
- Letting the default index provide `torch`; the symptom is a multi-gigabyte CUDA wheel in a CPU-only image,
  or a CPU-only build on a GPU host, discovered as slow images or `torch.cuda.is_available()` being False.

## When to use
For a new application or service, uv is a strong default: one tool for Python versions, environments,
locking and running, fast enough for CI and Docker. Keep Poetry where a team already uses it well; migrating
buys speed but costs churn. Use pip with pip-tools, or uv's pip interface, when your deployment platform or
organization expects requirements files. Whatever the tool, measure success by one property: a fresh clone
plus one command reproduces the exact environment CI tested. In interviews, explain the separation of
intent from resolution rather than tool preferences.

## Role lens
- **Engineer:** Uses `uv add`/`uv sync` daily, keeps dev tools in a dev group, and configures the torch index
  so local and CI environments get the right wheels.
- **Architect:** Chooses one toolchain across repos and defines how locks are updated, reviewed and consumed
  in CI and container builds.
- **FDE:** Works inside whatever the customer mandates, often pip against a private mirror, and exports a
  pinned requirements file from uv or Poetry when that is the only accepted format.

## Check yourself
**Q:** Your service's CI installs with `pip install -r requirements.txt` generated by `pip freeze` on a
developer's Mac. Linux builds fail intermittently and images contain unexpected packages. What would you
change, and why does it fix both symptoms?
**A:** A freeze captures one machine's whole environment: experiment leftovers, macOS-specific packages and
markers, and no distinction between direct and transitive dependencies. I would declare direct dependencies
with ranges in `pyproject.toml`, generate a lock with a resolver (uv lock, Poetry, or pip-compile run for
the target platform), commit it, and have CI install only from it (`uv sync --locked`). The lock contains
only what the declared dependencies need, so stray packages disappear, and it is resolved for Linux, so
platform mismatches stop.

## Quiz
### Q: What does `uv sync --locked` do in CI that plain `uv sync` does not?
- [ ] It skips installing dev dependencies
- [x] It fails if uv.lock is out of date with pyproject.toml instead of silently re-locking
- [ ] It installs packages globally instead of into .venv
- [ ] It upgrades every dependency to the newest allowed version
> --locked asserts the lockfile already matches the declared dependencies, so a developer who edited
> pyproject.toml without re-locking gets a failing build rather than CI resolving a new, untested set.

### Q: Why is `pip freeze > requirements.txt` a poor lockfile for a deployed service?
- [ ] pip freeze cannot output version numbers
- [ ] pip refuses to install files generated by freeze
- [x] It captures the whole local environment for one platform, without separating direct from transitive dependencies
- [ ] It always includes hashes that break installation on other machines
> A freeze records whatever is installed, including unrelated packages, resolved on one OS and Python
> version. A resolver-generated lock is derived from declared requirements and can target the deploy platform.

### Q: A main reason uv installs are fast on repeated builds is that it:
- [ ] Skips dependency resolution entirely
- [ ] Downloads only source distributions and compiles in parallel
- [x] Reuses a global cache, linking or copying files into environments instead of re-downloading them
- [ ] Installs packages without metadata
> uv still resolves, but caches packages globally and materializes them into each environment cheaply,
> alongside parallel downloads. Building from source would be slower, not faster.
