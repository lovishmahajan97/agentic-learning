---
id: pydantic-settings
module: python-for-ai
name: pydantic-settings
kind: tool
difficulty: core
est_minutes: 12
status: reviewed
related:
  - python-for-ai/pydantic
  - python-for-ai/python-dotenv
  - python-for-ai/dependency-injection-and-interface-boundaries
  - backend-fundamentals/secrets-management
  - fastapi-for-ai/dependency-injection
  - fastapi-for-ai/lifespan
  - enterprise-integration/tenant-specific-configuration-and-feature-flags
sources:
  - Official docs: pydantic-settings
  - Official docs: Pydantic
---

## TL;DR
pydantic-settings is a Pydantic model whose fields are filled from environment variables, `.env` files
and secret files instead of a request body. You declare configuration once, with types, defaults and
constraints, and the app either starts with a fully validated settings object or refuses to start with
an error naming the bad field. That moves config mistakes from 2 a.m. runtime failures to deploy-time
crashes.

## Why it exists
Reading configuration with `os.environ.get` scattered through the code has three failure modes that
bite AI services in particular. Everything is a string, so `TIMEOUT="30"` must be converted by hand at
each call site, and `"false"` is truthy. Nothing is checked up front, so a missing `VECTOR_DB_URL` or a
typo in `RERANK_TOP_K` is discovered only when the first request reaches that code path, possibly hours
after deploy. And there is no single list of what the service needs, so a new environment is configured
by trial and error.

The naive fix, a hand-written `config.py` that reads and converts every variable, works until it has
thirty fields, nested groups (retrieval, model, tracing), lists of allowed models and secrets that must
not appear in logs. At that point you are reimplementing a validation library. Pydantic already is one,
so pydantic-settings reuses it: the same type coercion, constraints and error messages, aimed at the
process environment. It was split out of Pydantic into its own package with Pydantic v2, so it is a
separate install (`pip install pydantic-settings`).

## How it works
### Declaring settings
Subclass `BaseSettings` and annotate fields exactly as in a normal Pydantic model. Behaviour is set with
`model_config = SettingsConfigDict(...)`: `env_prefix` (so `APP_MODEL` fills `model`, avoiding collisions
with other software's variables), `env_file` (one path or a tuple, later files winning), `case_sensitive`
(off by default), `env_nested_delimiter` and `extra`.

### Where values come from, in priority order
When you instantiate `Settings()`, each field is resolved from the highest-priority source that has it:

1. Keyword arguments passed to the constructor (used by tests).
2. Environment variables.
3. The dotenv file(s).
4. Files in `secrets_dir`, one file per field, which is how Docker and Kubernetes secrets are mounted.
5. Field defaults.

The ordering is deliberate: the platform's environment beats a local file, which matches python-dotenv's
default and means a stale `.env` cannot override production. You can reorder or add sources (a vault,
a YAML file) by overriding the `settings_customise_sources` classmethod.

Mechanically, each source is just a callable that returns a dict of raw values for the fields it knows
about. pydantic-settings calls them, deep-merges the dicts so a higher-priority source overwrites a
lower one key by key, and then runs one ordinary Pydantic validation over the merged dict. That design
is why everything Pydantic can do (coercion, validators, nested models, aliases) works unchanged for
config, and why a custom source only has to produce a dict. Note the asymmetry with python-dotenv:
within `env_file=(".env", ".env.local")` the *later* file wins, whereas repeated `load_dotenv` calls
keep the first value.

When a variable's name is fixed by someone else (the OpenAI SDK's `OPENAI_API_KEY`, a platform's
`DATABASE_URL`), give the field `validation_alias="OPENAI_API_KEY"` (or `AliasChoices` for several
accepted names) instead of bending your prefix convention.

### Parsing beyond strings
Simple types are coerced with Pydantic's normal rules, so `"30"` becomes `30.0` for a `float` field and
`"false"` becomes `False` for a `bool`. Complex fields (`list`, `dict`, nested models) are parsed from a
JSON string, so `APP_ALLOWED_MODELS='["a","b"]'` works. With `env_nested_delimiter="__"`,
`APP_RETRIEVAL__TOP_K=20` sets one field of a nested model without restating the rest. Constraints
(`Field(gt=0)`, `Literal["dev","prod"]`) are enforced exactly as for API input, and a failure raises a
single `ValidationError` listing every bad field at once, because one deploy should report all problems
rather than one per restart.

### Secrets
Type sensitive fields as `SecretStr`. The value is masked in `repr`, `str` and default serialization, so
`print(settings)` or a crash log shows `**********`; you must call `.get_secret_value()` at the point of
use. This does not encrypt anything; it prevents the most common leak, which is logging a config object.

### Where it lives at runtime
Construct settings once and share them: a cached `get_settings()` function, or an object built in the
FastAPI lifespan and injected as a dependency. That gives you one validation at startup and an easy
override in tests (construct a `Settings(...)` with explicit kwargs and `_env_file=None`).

## Example
A settings class for a RAG service, and what happens with real environment values.

```python
from functools import lru_cache
from typing import Literal
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Retrieval(BaseModel):
    top_k: int = Field(8, ge=1, le=100)
    rerank: bool = True

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_", env_file=".env", env_nested_delimiter="__", extra="ignore")
    env: Literal["dev", "staging", "prod"] = "dev"
    openai_api_key: SecretStr                     # required: no default
    model: str = "gpt-4o-mini"
    request_timeout_s: float = Field(30.0, gt=0)
    allowed_models: list[str] = ["gpt-4o-mini"]
    retrieval: Retrieval = Retrieval()

@lru_cache
def get_settings() -> Settings:
    return Settings()

# Environment:
#   APP_OPENAI_API_KEY=sk-live-...      APP_ENV=prod
#   APP_RETRIEVAL__TOP_K=20             APP_ALLOWED_MODELS='["gpt-4o-mini","gpt-4o"]'
s = get_settings()
print(s.retrieval)          # top_k=20 rerank=True   (nested override, default kept)
print(s.openai_api_key)     # **********             (masked)
client_key = s.openai_api_key.get_secret_value()

# In a test, ignore the developer's .env (real env vars still apply, so
# kwargs or monkeypatch.delenv must cover anything the shell might set):
t = Settings(_env_file=None, openai_api_key="test", env="dev")
```

If staging sets `APP_REQUEST_TIMEOUT_S=thirty` and `APP_ENV=production`, startup fails with one
`ValidationError` reporting two errors: `request_timeout_s` is not a valid number and `env` is not one
of `'dev', 'staging', 'prod'`. The pod crash-loops visibly instead of serving traffic with a broken
timeout.

## Trade-offs
- Fail-fast validation turns silent misconfiguration into a crash at startup, which is right for
  services but annoying for scripts that only need one value; there, plain `os.environ` is simpler.
- One typed object documents every setting, but it couples config to Pydantic's coercion rules, so
  lax coercion (`"1"` to `True`) can surprise; strict mode trades convenience for exactness.
- JSON-in-env for lists and nested models keeps everything in variables, yet quoting JSON inside shell,
  YAML and Helm templates is error-prone; past a few nested fields a mounted config file is cleaner.
- `SecretStr` masks accidental logging but adds `.get_secret_value()` noise, and it is not a substitute
  for a secrets manager with rotation; it only governs how the value is printed.

## Pitfalls
- Instantiating `Settings()` at import time in a library module; the symptom is tests failing to
  import because a required key is missing, fixed by constructing lazily or in the entry point.
- Forgetting `env_prefix` and naming a field `path` or `home`; the symptom is the field mysteriously
  filled from the system's own `PATH` or `HOME` variable.
- Leaving `extra` at its default (`"forbid"`) when sharing a `.env` with other tools; the symptom is a
  validation error about unexpected keys from the dotenv file (environment variables never trigger
  it), fixed with `extra="ignore"`.
- Expecting a nested override without setting `env_nested_delimiter`; the symptom is
  `APP_RETRIEVAL__TOP_K` being silently ignored and the default used.
- Caching settings with `lru_cache` and then changing env vars in a test; the symptom is old values,
  fixed with `get_settings.cache_clear()` or dependency overrides.

## When to use
Reach for pydantic-settings in any long-running service or worker with more than a few settings,
anything with secrets, and anything deployed to more than one environment. It is the default choice in
FastAPI codebases because settings plug straight into dependency injection. Keep plain `os.environ` or
python-dotenv for throwaway scripts and notebooks. Measure success by how config failures surface: they
should appear as startup errors naming the field, never as request-time `KeyError`s or wrong behaviour.
In interviews, explain the source precedence and why secrets are typed as `SecretStr`.

## Role lens
- **Engineer:** Writes the `Settings` class, injects it through a cached dependency, and overrides it
  in tests with explicit kwargs instead of patching environment variables.
- **Architect:** Decides which settings exist per environment, the prefix convention, which values come
  from a secrets store, and that services fail closed on invalid config.
- **FDE:** Hands the customer a documented settings schema and `.env.example`, and reads the startup
  `ValidationError` to diagnose their environment in minutes.

## Check yourself
**Q:** Your platform team rotated the API key by updating a Kubernetes secret mounted as a file in
`secrets_dir`. The pods restarted, but they still call the provider with the old key. An old ConfigMap
also sets `APP_OPENAI_API_KEY`. Explain what happened and two ways to fix it.
**A:** Sources are merged by priority: init kwargs, environment, dotenv, secret files, defaults. The
stale ConfigMap value arrives as an environment variable, which outranks the mounted secret file, so the
rotation was silently shadowed; the restart re-read both and the env var won again. Fix one: delete the
variable from the ConfigMap so the secret file is the only source. Fix two, if the team wants files to
be authoritative by design: override `settings_customise_sources` to return the file-secret source
ahead of the environment source. Either way, add a startup log of *which source* supplied each secret
(never the value), so the next shadowing is visible, and remember a running process never re-reads its
settings without a restart.

## Quiz
### Q: `APP_MODEL` is set in the environment, `.env` sets `APP_MODEL` differently, and the field has a default. Which value wins?
- [ ] The `.env` value, because files are read last
- [x] The environment variable, because it outranks dotenv files and defaults
- [ ] The default, because explicit defaults always win
- [ ] Startup fails because of the conflicting definitions
> Priority is constructor kwargs, then environment variables, then dotenv, then secret files, then
> defaults. This matches the deployment model: the platform's injected value beats a local file.

### Q: Why type an API key as `SecretStr` rather than `str`?
- [ ] It encrypts the value in memory
- [ ] It validates that the key has the provider's format
- [x] It masks the value when the settings object is printed, logged or serialized
- [ ] It loads the value from a secrets manager automatically
> `SecretStr` only changes representation, so logging the config shows asterisks. You still need a real
> secrets store and must call `.get_secret_value()` where the key is actually used.

### Q: How would you set only `retrieval.top_k` from the environment for a nested `Retrieval` model?
- [ ] `APP_RETRIEVAL.TOP_K=20`, which works by default
- [x] Set `env_nested_delimiter="__"` and use `APP_RETRIEVAL__TOP_K=20`
- [ ] Nested models cannot be configured from the environment at all
- [ ] Set `APP_TOP_K=20`, since nested fields are flattened automatically
> Without a configured delimiter, nested models are only settable as a whole JSON string. With
> `env_nested_delimiter`, a single nested field can be overridden while the others keep their defaults.
