---
id: python-dotenv
module: python-for-ai
name: python-dotenv
kind: tool
difficulty: intro
est_minutes: 9
related:
  - python-for-ai/pydantic-settings
  - backend-fundamentals/secrets-management
  - agent-runtime-architecture/secrets-and-credential-isolation
  - backend-fundamentals/docker
  - enterprise-integration/sandbox-vs-production-environments
  - python-for-ai/security-scanning-and-dependency-auditing
sources:
  - Official docs: python-dotenv
  - Wiggins, 2011 — The Twelve-Factor App
---

## TL;DR
python-dotenv reads `KEY=value` lines from a `.env` file into `os.environ`, so code can read
configuration from environment variables everywhere while developers keep their local values in a
gitignored file. It is a development convenience, not a secrets manager: it does no typing, no
validation and no encryption, and by default it never overrides variables that are already set, which is
exactly what lets production inject real values without the file.

## Why it exists
An AI service needs a handful of values that differ per environment and must never be committed: the
OpenAI or Anthropic API key, a vector-database URL, a Langfuse key, the model name you are trialling.
The Twelve-Factor convention is to read all of these from environment variables, because every
deployment target (Docker, Kubernetes, a PaaS, CI) knows how to inject them and none of them require
code changes.

The friction is on the developer laptop. Exporting eight variables in every new terminal is tedious, and
the workarounds are worse: hardcoding the key "just for now" ends in a leaked key in git history;
putting it in `~/.zshrc` leaks one project's key into every other project and every subprocess; a
`config.py` with real values gets committed by accident. What people want is a per-project file that
behaves like environment variables, is easy to edit, and is ignored by git.

python-dotenv is that bridge. The code path stays `os.environ["OPENAI_API_KEY"]` in every environment;
only the *source* of the variable changes. Locally a file fills it in; in production the platform does,
and the library steps aside.

## How it works
### Finding the file
`load_dotenv()` with no arguments calls `find_dotenv()`, which starts in the directory of the calling
module (or the current directory in an interactive session) and walks up parent directories until it
finds a `.env`. It walks *up* because scripts are often run from a subfolder (`scripts/`, `notebooks/`)
while the file sits at the project root. You can pass an explicit path instead, and
`find_dotenv(usecwd=True)` searches from the working directory. If nothing is found, `load_dotenv()`
returns `False` rather than raising, because "no file" is the normal production case.

### Parsing
Each line is `KEY=value`, optionally prefixed with `export ` so the same file can be `source`d by a
shell. Comments start with `#`. Single-quoted values are literal; double-quoted values support escapes
such as `\n` and can span lines, which is how people store PEM keys. With interpolation on (the default
for `load_dotenv`), `${OTHER}` expands from earlier lines or the existing environment, so
`DB_URL=postgres://${DB_USER}@localhost/app` works.

### The precedence rule
`load_dotenv(override=False)` is the default, and it is the key design choice: a variable already in
the process environment wins over the file. This is *because* the file is meant to be the fallback for
the laptop. In CI or production, the platform-injected value is authoritative, and a stray `.env` baked
into an image must not silently replace it. Pass `override=True` only when you deliberately want the
file to win.

### Two ways to use the values
- `load_dotenv()` mutates `os.environ`, so every library that reads env vars (the OpenAI SDK reads
  `OPENAI_API_KEY` on its own) sees the value, as do subprocesses you spawn.
- `dotenv_values(".env")` returns a plain dict and touches nothing. This is better for tests and for
  merging several files (`{**dotenv_values(".env.shared"), **dotenv_values(".env.local")}`), because
  global mutable state is avoided.

There is also a CLI (`dotenv run -- python app.py`, from the `cli` extra) that loads the file and then
execs the command, useful for tools you cannot edit.

### What it does not do
Every value is a string. `DEBUG=false` gives you the non-empty string `"false"`, which is truthy. There
is no schema, no required-field check and no secret masking; that is the job of a settings layer such
as pydantic-settings, which can itself read `.env` files.

## Example
A small entry point that loads local config, then fails fast and explicitly on what is missing.

```python
# .env (gitignored)                     .env.example (committed, no real values)
#   OPENAI_API_KEY=sk-local-dev           OPENAI_API_KEY=
#   MODEL=gpt-4o-mini                     MODEL=gpt-4o-mini
#   MAX_TOKENS=512                        MAX_TOKENS=512
#   DEBUG=false                           DEBUG=false
import os
from dotenv import load_dotenv, dotenv_values

found = load_dotenv()          # walks up to find .env; existing env vars win
print("loaded .env:", found)   # False in production, where the platform sets vars

REQUIRED = ["OPENAI_API_KEY", "MODEL"]
missing = [k for k in REQUIRED if not os.environ.get(k)]
if missing:
    raise SystemExit(f"missing config: {missing}; copy .env.example to .env")

model = os.environ["MODEL"]
max_tokens = int(os.environ.get("MAX_TOKENS", "256"))      # strings only: convert yourself
debug = os.environ.get("DEBUG", "false").lower() in {"1", "true", "yes"}
print(model, max_tokens, debug)  # bool("false") would have been True

# Without touching os.environ (good in tests):
cfg = dotenv_values(".env.example")
assert set(REQUIRED) <= cfg.keys(), "example file is out of date"
```

Running `MODEL=claude-test python app.py` prints `claude-test`, because the shell's variable was set
before `load_dotenv()` ran and `override` defaults to `False`.

## Trade-offs
- One code path (`os.environ`) for every environment, at the cost of a second, implicit source of truth
  that can drift from what production injects; a committed `.env.example` keeps the drift visible.
- Zero dependencies beyond the library and trivial to adopt, but you hand-roll type conversion and
  required checks; once there are more than a few settings, pydantic-settings pays for itself.
- Mutating `os.environ` makes values visible to third-party SDKs and subprocesses, which is convenient
  until a child process or crash dump exposes a key it never needed; `dotenv_values` avoids that.
- A plaintext file on disk is fine for a dev key with low limits, but for production secrets a secrets
  manager with rotation and audit wins regardless of team size.

## Pitfalls
- Committing `.env` because `.gitignore` was added later; the symptom is a provider's leaked-key alert
  or a secret scanner hit, and rotating the key is mandatory because git history keeps it.
- Expecting the file to win over a stale exported variable; the symptom is "I changed the key in .env
  and nothing changed" because an old `export` in the shell takes precedence under `override=False`.
- Calling `load_dotenv()` after importing a module that read `os.environ` at import time; the symptom
  is a `None` key even though the file is correct, fixed by loading first thing in the entry point.
- Treating `"false"` or `"0"` as falsy; the symptom is debug mode or a feature flag stuck on.
- Copying `.env` into a Docker image with `COPY . .`; the symptom is real keys visible in a pulled
  image layer, prevented with `.dockerignore`.

## When to use
Use python-dotenv for local development, notebooks, demos and small scripts where the alternative is
exporting variables by hand. Call it once, at the top of the entry point, and never inside library code,
which should only read the environment it is given. Commit a `.env.example` listing every key. Stop
relying on it when settings need types, validation, nesting or per-environment profiles: move to
pydantic-settings, which reads the same file. Do not use it as the production secret delivery
mechanism; production should get variables from the platform or a secrets manager, and your code should
work identically when no `.env` exists. In interviews, the signal is knowing the override default and why.

## Role lens
- **Engineer:** Loads `.env` once in `main` or `conftest.py`, keeps `.env.example` current, and converts
  string values explicitly instead of trusting truthiness.
- **Architect:** Sets the rule that `.env` is a local fallback only, production secrets come from a
  managed store, and environment precedence is documented.
- **FDE:** In a customer VPC, ships a `.env.example` and runbook, and checks the customer's platform
  injects variables rather than mounting a plaintext file.

## Check yourself
**Q:** Your service reads `OPENAI_API_KEY` via python-dotenv. In staging it uses the wrong key even
though the Kubernetes secret is correct. What are the likely causes and how do you fix it?
**A:** First check whether a `.env` file got into the image (no `.dockerignore`) and whether the code
calls `load_dotenv(override=True)`; together those make the baked file beat the injected secret. If
`override` is the default `False`, the injected variable should win, so the next suspect is that the pod
does not actually receive it (wrong secret key name or namespace) and the file supplies a fallback. Fix
by excluding `.env` from images, keeping the default precedence, and failing fast at startup when a
required variable is absent, so no silent fallback exists.

## Quiz
### Q: What does `load_dotenv()` do by default when `OPENAI_API_KEY` is already set in the shell?
- [ ] Replaces it with the value from `.env`
- [x] Leaves the existing value in place and only fills variables that are not already set
- [ ] Raises an error about a conflicting definition
- [ ] Keeps both values and joins them with a separator
> The default is `override=False`, so the process environment wins. That lets production inject real
> values while `.env` only fills gaps on a laptop; `override=True` reverses the precedence.

### Q: A `.env` has `ENABLE_CACHE=false`, and the code does `if os.environ["ENABLE_CACHE"]:`. What happens?
- [ ] The branch is skipped because python-dotenv parses booleans
- [ ] A TypeError is raised because the value is not a bool
- [x] The branch runs, because the value is the non-empty string "false"
- [ ] The variable is ignored because lowercase booleans are invalid
> python-dotenv only produces strings, and any non-empty string is truthy in Python. Convert explicitly
> or use a typed settings layer such as pydantic-settings that parses "false" into False.

### Q: When is `dotenv_values()` a better choice than `load_dotenv()`?
- [ ] When you need third-party SDKs to pick up the key automatically
- [x] When you want the values as a dict without mutating global process state, such as in tests
- [ ] When the file uses `export` prefixes that `load_dotenv` cannot parse
- [ ] When running in production without a `.env` file present
> `dotenv_values` returns a dict and leaves `os.environ` untouched, which avoids leakage between tests
> and makes merging files explicit. SDKs that read the environment themselves need `load_dotenv` instead.
