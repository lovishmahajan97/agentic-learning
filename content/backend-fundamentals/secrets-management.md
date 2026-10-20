---
id: secrets-management
module: backend-fundamentals
name: Secrets management
kind: practice
difficulty: core
est_minutes: 15
related:
  - backend-fundamentals/oauth-2-0-oidc
  - backend-fundamentals/docker
  - backend-fundamentals/kubernetes
  - backend-fundamentals/ci-cd
  - python-for-ai/pydantic-settings
  - python-for-ai/python-dotenv
  - agent-runtime-architecture/secrets-and-credential-isolation
  - security-governance-compliance/zero-trust
sources:
  - Official docs: HashiCorp Vault
  - Official docs: Kubernetes (Secrets)
  - OWASP — Secrets Management Cheat Sheet
---

## TL;DR
A secret is any value whose disclosure grants access: provider API keys, database passwords, signing
keys, OAuth client secrets. Secrets management keeps those values out of code, images and logs, hands
them to workloads at runtime from a store that authenticates the *workload* rather than trusting a
pasted key, and makes them rotatable or short-lived so a leak has a bounded blast radius. In AI
systems the LLM provider key is usually the most expensive secret to leak, because it converts
directly into someone else's bill.

## Why it exists
The first version of every service has `API_KEY = "sk-..."` in a config file, or a `.env` committed
"just for now". The failure modes are not hypothetical. Git history is permanent: deleting the line in
a later commit leaves the key in every clone, fork, CI cache and backup, and attackers scan public
repositories for key patterns continuously, so a pushed key should be assumed compromised. One key
shared across development, staging and production means a developer's laptop is a production breach.
A key with no named owner is never rotated, and nobody can answer "which services would break if we
revoked it". A leaked provider key is worse than a leaked password: it is spendable (tokens billed to
you), it may expose uploaded files and fine-tunes, and the abuse looks like normal traffic.

Environment variables are the usual next step and are genuinely better, but not sufficient. They are
inherited by every child process, readable from `/proc/<pid>/environ` by anything running as the same
user, captured by crash reporters and `docker inspect`, and printed by the "log config at startup"
helper somebody added for debugging. They also do nothing about rotation: the value still arrived by a
human pasting it somewhere.

The structural answer is to separate *identity* from *secrets*. A workload should be able to prove who
it is using something the platform gave it (an IAM role, a service-account token), then fetch what it
needs from a store that checks policy, logs the access, and can rotate or revoke the value. Secrets
become something the system issues to workloads, not something people carry around.

## How it works
### The lifecycle
1. **Generate.** By the system that will verify the secret, from a cryptographic random source, never
   typed by a person. Human-chosen secrets are guessable and get reused.
2. **Store.** In a secret store that encrypts at rest with envelope encryption: a key-encryption key
   (KEK) held in a KMS or HSM wraps a per-secret data-encryption key (DEK), and the DEK encrypts the
   value. This is done *because* the KEK then never leaves the KMS, and rotating it means re-wrapping
   small DEKs rather than re-encrypting every stored value.
3. **Distribute.** To a workload at runtime, after the workload authenticates. The catch is "secret
   zero": to fetch secrets you need a credential, and if that credential is itself a stored secret you
   have moved the problem. Platforms solve it by issuing identity the workload did not have to be
   given: an IAM role attached to the VM, pod or function; a Kubernetes service-account token projected
   into the pod and verified by the store (Vault's Kubernetes auth method does this); an OIDC token
   issued to a CI job. Each is short-lived, signed by the platform, and bound to the workload.
4. **Use.** Read once into process memory, hold it in a typed config object, never serialise or log it.
   Wrapping values in a type whose `repr` is masked (pydantic's `SecretStr`) makes the accidental
   `print(settings)` harmless.
5. **Rotate.** The store issues a new version; consumers re-read; the old version stays valid for an
   overlap window so the switch is not an outage. Dynamic secrets go further: the store creates a
   database user with a TTL for each workload and revokes it on expiry, so every credential in flight
   is unique and short-lived, and a leaked one is useless within minutes.
6. **Revoke and audit.** Every read is logged with the caller's identity, so an incident can be scoped
   to "who read version 7 between these two timestamps" instead of "rotate everything and hope".

### Injection patterns, from simplest to safest
- **Environment variables at container start.** Universal, trivially supported, and inherit every
  weakness above; fine for a prototype, acceptable in production when the value is injected by the
  platform rather than a file.
- **Mounted files.** Kubernetes Secrets or a CSI secrets driver mount values as files on a tmpfs; file
  permissions limit who reads them and the file can be updated in place on rotation, which env vars
  cannot.
- **Sidecar or operator.** An agent (Vault Agent, External Secrets Operator) authenticates with the
  workload's identity, fetches from the store and renders files or Kubernetes Secrets, so application
  code never talks to the store.
- **SDK fetch at startup.** The process calls the store directly with a TTL cache. Fewest moving parts,
  but every service now depends on store availability, so it needs a cached last-good value or a
  deliberate fail-closed decision.

### Kubernetes Secrets are not encrypted by default
A Secret object is stored in etcd base64-encoded, which is an encoding, not encryption. Unless
encryption at rest is configured on the API server, anyone with access to etcd or a backup reads them
in plaintext, and anyone with `get secrets` RBAC in the namespace reads them through the API. Treat
them as a delivery mechanism for values that live in a real store, restrict the RBAC verb, and enable
encryption at rest.

### Build and CI boundaries
`ENV` and `ARG` values in a Dockerfile are recorded in the image's history and config, so anyone who
can pull the image has them; build-time secrets go through BuildKit's `--mount=type=secret`, which
exposes a file during one `RUN` and leaves nothing in a layer. In CI, prefer OIDC federation (the job
presents a signed identity token and the cloud exchanges it for a short-lived role credential) over a
stored long-lived cloud key, mask secrets in logs, and never make secrets available to jobs triggered
by untrusted pull requests.

### What is specific to AI systems
Issue provider keys per environment and per service with spend limits and usage alerts, so a leak is
attributable and capped. Anything in the model's context window can be exfiltrated by prompt
injection, so a tool implementation holds its credentials server-side and the model sees only a tool
name and a result. Tracing tools record prompts and tool outputs verbatim, so redact `Authorization`
headers and known secret patterns before traces leave the process.

## Example
A settings object loads non-secret configuration from the environment, but only the *name* of the
secret; the value is fetched at runtime from AWS Secrets Manager using the pod or VM identity, cached
with a TTL so rotation is picked up without a restart, and wrapped so it cannot be logged by accident.
If the store is unreachable the last good value keeps serving. The secret is a JSON document with an
`api_key` field, a common convention for storing several fields under one name.

```python
import json, time, boto3
from pydantic import SecretStr
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    env: str = "dev"                          # non-secret config can come from env vars
    llm_secret_id: str = "prod/llm/openai"    # *where* the secret lives, never the secret
    secret_ttl_s: int = 300

settings = Settings()
_sm = boto3.client("secretsmanager")          # credentials come from the workload identity
_cache: dict[str, tuple[float, SecretStr]] = {}

def get_secret(secret_id: str) -> SecretStr:
    hit = _cache.get(secret_id)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    try:
        raw = _sm.get_secret_value(SecretId=secret_id)["SecretString"]
    except Exception:
        if hit:                               # store unreachable: keep the last good value
            return hit[1]
        raise                                 # never started with a secret: fail closed
    value = SecretStr(json.loads(raw)["api_key"])
    _cache[secret_id] = (time.monotonic() + settings.secret_ttl_s, value)
    return value

def llm_client():
    from openai import OpenAI
    key = get_secret(settings.llm_secret_id)
    return OpenAI(api_key=key.get_secret_value())   # unwrap only at the point of use

print(get_secret(settings.llm_secret_id))     # SecretStr('**********'): safe in logs
# Rotation: update the secret in the store; every replica picks it up within secret_ttl_s,
# and the old key is revoked only after that window has passed.
```

## Trade-offs
- A central store gives rotation, audit and one place to revoke, but it becomes a runtime dependency:
  if it is down and clients hold no cached value, nothing starts; cache with a TTL and fail open to the
  last value, or fail closed when the secret guards something regulated.
- Short-lived dynamic credentials bound the blast radius, but add churn: connection pools must
  re-authenticate and every consumer needs rotation-aware code; static-but-rotated keys are the
  pragmatic middle for third-party APIs that only issue long-lived keys.
- Environment-variable injection is universal and simple, while file mounts and sidecars are safer and
  support in-place rotation but tie you to a platform; the balance flips as soon as you run more than
  one environment or face an audit.
- One key per service per environment multiplies the keys you manage, but makes revocation surgical
  and spend attributable; a single shared key is defensible only for a one-person prototype.

## Pitfalls
- Committing a `.env` or hard-coding a key; the symptom is a secret-scanning alert or a provider bill
  for usage you cannot attribute, and the only fix is rotation, because rewriting history does not
  reach existing clones.
- Logging the config object or request headers at startup or in debug mode; the symptom is keys
  showing up in log search; wrap values in `SecretStr` and redact `Authorization` headers.
- Baking secrets into image layers through `ENV`, `ARG` or copying a `.env`; the symptom is the value
  visible in `docker history`, available to anyone who can pull the image.
- Passing secrets through the model, by putting a token in the system prompt or returning raw
  credentials from a tool; the symptom is the key appearing in a trace or being echoed by the model.
- Sharing one key across dev, staging and prod; the symptom is a laptop compromise forcing a production
  rotation, or a load test consuming production rate limits.
- Treating Kubernetes Secrets as encrypted; the symptom is an etcd backup or a broad `get secrets`
  grant exposing every credential in the namespace.
- Never rotating because "it might break something"; the symptom is that the first forced rotation
  during an incident becomes a second incident, since no client was ever proven rotation-safe.

## When to use
Always; the question is which tier. A prototype needs a git-ignored `.env` loaded through
`pydantic-settings`, plus a pre-commit secret scanner. A team with staging needs a managed store
(Vault, AWS Secrets Manager, GCP Secret Manager, Azure Key Vault) with workload identity and per-
environment keys. Production adds dynamic or short-lived credentials where the backend supports them,
scheduled rotation, audit logs shipped to the SIEM, and spend alerts on provider keys. Track the age of
the oldest secret, the number with no owner, time-to-rotate, and the fraction of workloads using
workload identity rather than static keys. In interviews, explain secret zero, envelope encryption, and
why environment variables are better than code but worse than identity-based fetch.

## Role lens
- **Engineer:** Wires settings so secrets are fetched by workload identity, wrapped in `SecretStr`,
  redacted from traces, and never reach prompts, tool outputs or image layers.
- **Architect:** Picks the store and identity scheme per platform, sets rotation policy and per-service
  key boundaries, and decides fail-open versus fail-closed when the store is unavailable.
- **FDE:** Runs inside the customer's vault and KMS, maps their IAM to your workloads, and answers the
  questionnaire line "how are our API keys stored, rotated and audited" with evidence.

## Check yourself
**Q:** Your agent service on a customer's Kubernetes cluster in AWS needs an LLM provider key, a
Postgres password and a Slack bot token. Design how each reaches the process and what happens on
rotation.
**A:** Give the pod a cloud identity first, through IAM roles for service accounts, so the process can
call AWS without any stored key. Postgres is the best candidate for dynamic credentials: use IAM
database authentication or Vault's database engine to issue a per-pod user with a TTL, and have the
connection pool re-authenticate on expiry; failing that, store the password in Secrets Manager and
sync it into the pod as a mounted file via External Secrets Operator, rebuilding the pool when the
file changes. The LLM key and Slack token are third-party static keys, so store them per environment,
fetch them at startup through the SDK with a TTL cache, attach spend alerts, and rotate on a schedule
with an overlap: create the new key, update the secret, wait for every replica to refresh within the
TTL, then revoke the old one. Keep all three inside tool implementations so the model never sees them,
and redact authorization headers from traces.

## Quiz
### Q: What is the "secret zero" problem, and how do modern platforms solve it?
- [ ] The first secret created in a store cannot be rotated without downtime
- [x] A workload needs a credential to fetch its secrets, so platforms give it a signed identity (IAM role, service-account token, OIDC) instead of a stored bootstrap secret
- [ ] Secrets must be versioned from zero so rotation can be tracked
- [ ] The root token of the secret store must be destroyed after initial setup
> If the bootstrap credential is itself a pasted secret, nothing has improved. Platform-issued,
> short-lived identity breaks the regress because the workload proves who it is without carrying a
> long-lived value. Destroying root tokens is good hygiene but a different concern.

### Q: Why is deleting a committed API key in a later commit not enough?
- [ ] Because Git compresses secrets into pack files that cannot be rewritten
- [ ] Because providers only revoke keys automatically after a force-push
- [x] Because the key remains in history, clones, forks and CI caches, so it must be treated as compromised and rotated
- [ ] Because pre-commit hooks cannot run retroactively on old commits
> History rewriting can remove the bytes from one repository, but every copy made before that keeps
> them, and scanners may already have the value. Rotation is the only action that actually ends the
> exposure.

### Q: Which design keeps a provider API key out of reach of a prompt-injection attack on an agent?
- [ ] Encrypting the key before placing it in the system prompt
- [ ] Placing the key in the system prompt, which end users cannot see
- [x] Holding the key inside the tool implementation so the model only ever sees tool names and results
- [ ] Rotating the key after every conversation
> Anything in the context window can be extracted by an injected instruction, including "encrypted"
> text the model is asked to repeat. Keeping credentials on the server side of the tool boundary means
> there is nothing in the prompt to leak; rotation limits damage but does not prevent it.
