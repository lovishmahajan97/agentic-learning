---
id: secrets-management
module: backend-fundamentals
name: Secrets management
kind: practice
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - agent-runtime-architecture/secrets-and-credential-isolation
  - backend-fundamentals/oauth-2-0-oidc
  - backend-fundamentals/ci-cd
  - backend-fundamentals/kubernetes
  - backend-fundamentals/docker
  - deployment/terraform
  - python-for-ai/security-scanning-and-dependency-auditing
  - safety-security/prompt-injection
sources:
  - Official docs: HashiCorp Vault
  - Official docs: AWS Secrets Manager
  - Official docs: Kubernetes Secrets
  - OWASP — Secrets Management Cheat Sheet
---

## TL;DR
A secret is any value whose disclosure lets someone act as your system: database passwords,
model-provider API keys, signing keys, OAuth client secrets. Secrets management keeps them out of code,
images, logs and prompts; stores them encrypted in a purpose-built store; delivers them to workloads
that authenticate with a platform identity rather than yet another secret; and makes them short-lived,
scoped and rotatable. The goal is not perfect hiding but a small blast radius when, not if, one leaks.

## Why it exists
Secrets leak through boring paths: a `.env` committed "just once", an `ENV API_KEY=` line in a
Dockerfile, a build argument captured in image history, a key pasted into a ticket or a chat with an
LLM, an exception trace that logs the request headers, a notebook checked into git, a CI log that echoed
the environment. For AI teams the stakes are specific: a model-provider key is a billing key, and keys
pushed to public repositories are routinely found and abused by scanners within minutes (illustrative;
the exact window varies), turning into unexpected bills, exhausted quotas and rate limits for your real
traffic. Unless you deliberately create narrower project- or workspace-scoped keys, a provider key often
reaches a whole organisation's quota and data, so one leak exposes every project.

The naive answers each fail in a specific way. Environment variables are a delivery mechanism, not a
store: they are inherited by every child process, readable from `/proc/<pid>/environ`, and dumped into
crash reports. Encrypted files in the repository (sops, git-crypt) are a reasonable middle ground, but
the decryption key is itself a secret that must be bootstrapped somewhere, and rotation means
re-encrypting in every environment. "We will be careful" fails at the first contractor laptop.

The deeper problem is rotation. If a secret has been copied into fourteen places, nobody can rotate it
without an outage, so nobody does, and a three-year-old key sits in a dozen laptops. The first goal of
secrets management is therefore one source of truth referenced by name everywhere else, and the second
is to need as few long-lived secrets as possible in the first place.

## How it works
### 1. Inventory and minimise
List every credential and ask whether it needs to exist. Platform identity (an IAM role on the VM or
pod, a managed identity) replaces a static cloud key; OIDC federation replaces the cloud key stored in
CI; short-lived tokens replace static API keys where the provider supports them. Every secret you do not
have is one you never rotate or leak.

### 2. Store in a manager backed by a KMS
Vault, AWS Secrets Manager or Parameter Store, GCP Secret Manager and Azure Key Vault all do the same
core job: values are encrypted with a data key, the data key is encrypted by a root key the service
never exposes (envelope encryption; the cloud services hold it in an HSM-backed KMS, while Vault
protects it through its seal, which can itself be a cloud KMS or HSM), access is an IAM policy per
identity per secret path, recent versions are kept for rollback, and every read is audited. Kubernetes `Secret` objects are *not* this:
they are base64-encoded, stored in etcd unencrypted unless an `EncryptionConfiguration` with a KMS
provider is enabled, and readable by anyone with `get secrets` in the namespace, and in effect by
anyone who can create a Pod there, since a Pod can mount any Secret in its namespace. Treat them as the last
hop, populated from a real manager by the External Secrets Operator, the Secrets Store CSI driver or a
Vault agent sidecar.

### 3. Authenticate the workload: the secret-zero problem
To fetch secrets a workload needs a credential, and if that credential is a static key you have gained
nothing. The way out is identity that the platform can attest without a stored secret: an EC2 instance
profile; on EKS, IAM Roles for Service Accounts (the pod's projected service-account token is
exchanged for a role via OIDC federation) or EKS Pod Identity (a node agent exchanges the same token
through the EKS Auth API); GKE Workload Identity; Azure managed identity; Vault's Kubernetes
and JWT auth methods; and in CI, GitHub Actions' `id-token: write`, which lets a job present a short-lived
OIDC token that a cloud role trusts for a specific repository and branch. Trust now lives in
configuration (`sub = repo:org/app:ref:refs/heads/main`) instead of in a key, so keep those conditions
narrow.

### 4. Deliver to the process
Three options. Fetch through the SDK at startup: simple, needs network and identity, cache with a TTL.
Mount as files on a tmpfs volume: preferred on Kubernetes because the manager can rewrite the file and
the app re-reads it without a restart. Inject as environment variables: universal, but frozen for the
life of the process and leaky as above. Never bake into the image; `docker build --secret` mounts a value
during one build step without writing it to any layer.

### 5. Rotate, and prefer dynamic
Static secrets rotate on a schedule and immediately after any suspected exposure, using two-key overlap:
issue the new value, let consumers pick it up, then revoke the old, *because* an atomic swap across many
consumers is impossible. Dynamic secrets remove the schedule: Vault's database engine creates a
per-workload database user with a TTL and drops it on expiry; cloud STS tokens expire within hours.
Rotation only works if consumers re-read, so cache with a TTL and refetch on an authentication failure.

### 6. Detect and contain
Pre-commit and CI scanning (gitleaks, trufflehog) and GitHub push protection catch the commit before it
lands. A secret that reached git history is compromised even after the commit is removed: rotate it.
Redact known secret values from logs, allowlist logged fields, and alert when a secret is read by an
identity that never read it before.

### 7. Rules specific to AI systems
Use one model-provider key per service and environment with a spend cap, kept server-side only and
ideally behind a gateway so rotation happens in one place and spend is attributable. Never place a secret
in the prompt or in a tool result: a model can be induced to repeat its context, and traces in your
observability tool would store it. Tools should receive opaque references and resolve credentials in the
tool runtime, outside the model's context. Agents that reach into customer systems hold per-customer
credentials isolated by tenant; the agent's own identity is never the customer's key.

## Example
A small resolver that reads a secret from a mounted file when one exists (Vault agent or CSI driver),
otherwise from AWS Secrets Manager using the pod's or instance's ambient IAM identity, re-reads after a
TTL so rotations take effect without restarts, and installs a logging filter that redacts the value.

```python
import logging, os, time, boto3
from requests import post

class Secrets:
    """No static credential anywhere: boto3 picks up the IAM role from the environment (IRSA / instance profile)."""
    def __init__(self, mount="/var/run/secrets/app", ttl_s=300):
        self.mount, self.ttl_s, self._cache = mount, ttl_s, {}
        self._sm = boto3.client("secretsmanager")

    def get(self, name: str, force=False) -> str:
        hit = self._cache.get(name)
        if hit and not force and time.monotonic() - hit[1] < self.ttl_s:
            return hit[0]
        path = os.path.join(self.mount, name.replace("/", "_"))
        if os.path.exists(path):                                   # file mount rotates in place
            value = open(path).read().strip()
        else:                                                      # manager call, audited per identity
            value = self._sm.get_secret_value(SecretId=name)["SecretString"]
        self._cache[name] = (value, time.monotonic())
        return value

class Redact(logging.Filter):
    def __init__(self, values): super().__init__(); self.values = [v for v in values if v]
    def filter(self, record):
        msg = record.getMessage()
        if any(v in msg for v in self.values):
            for v in self.values: msg = msg.replace(v, "[REDACTED]")
            record.msg, record.args = msg, ()
        return True

secrets = Secrets()
LLM_KEY = secrets.get("prod/llm-gateway/api-key")        # per-service key with a spend cap
logging.basicConfig(level=logging.INFO)
for h in logging.getLogger().handlers:                   # on handlers: logger filters skip propagated records
    h.addFilter(Redact([LLM_KEY]))                       # re-register with the new value after a rotation

def call_provider(payload):
    key = secrets.get("prod/llm-gateway/api-key")        # cheap: served from cache until ttl
    r = post("https://llm-gateway.internal/v1/chat", json=payload, headers={"Authorization": f"Bearer {key}"})
    if r.status_code == 401:                             # rotated under us: refetch once, then retry
        key = secrets.get("prod/llm-gateway/api-key", force=True)
        r = post("https://llm-gateway.internal/v1/chat", json=payload, headers={"Authorization": f"Bearer {key}"})
    return r
```

When the key is rotated in the manager, the file mount updates or the next TTL expiry refetches; the
401 branch covers the window in between. Nothing in the repository, image or environment contains the
value.

## Trade-offs
- A central manager gives one place to rotate and audit but is a runtime dependency on startup; cache
  with a TTL and fail fast at boot. For a two-person project without infrastructure, encrypted files
  in the repo are an acceptable step up from nothing.
- Dynamic, short-lived credentials shrink the blast radius but require every consumer to handle expiry
  and refresh, and create churn in database users; static secrets with scheduled rotation are simpler
  where consumers cannot refresh.
- File mounts allow hot rotation but need the application to re-read; environment variables work with
  every runtime but are frozen for the process lifetime and inherited by children.
- Platform identity removes secret zero but ties you to that platform's IAM model; in air-gapped or
  on-premises deployments you may need Vault with AppRole and a bootstrap token delivered out of band.

## Pitfalls
- `ENV` or `ARG` with a secret in a Dockerfile; the symptom is that `docker history` or any pull of the
  image reveals it. Use build secrets.
- A `.env` deleted from the working tree but still in git history; the symptom is a scanner alert or a
  surprise provider bill weeks later. Rotate, do not just rewrite history.
- Logging whole request objects or headers; the symptom is `Authorization: Bearer …` lines in the log
  store. Allowlist fields and add redaction filters.
- One shared production key used by twelve services; the symptom is that rotation causes an outage and
  spend cannot be attributed. Issue per-service keys.
- Secrets placed in prompts, tool arguments or tool outputs; the symptom is the model echoing a key in
  chat or a trace containing it. Resolve credentials inside the tool runtime.
- Consumers that read secrets only at startup; the symptom is authentication failures after every
  rotation until a restart. Re-read on a TTL and on 401.
- Assuming Kubernetes Secrets are encrypted; the symptom is any namespace reader dumping them. Enable
  encryption at rest, restrict RBAC on secrets, and source them from a manager.

## When to use
There is no setting where this does not apply; what varies is the tooling. Day to day, adding a secret
means creating it in the manager, referencing it by name in configuration, granting one identity access,
and never pasting it anywhere. Measure the number of static long-lived secrets, the age of the oldest,
the time it takes to rotate one end to end, and scanner findings per month. In interviews, expect to
explain the secret-zero problem and how workload identity solves it, how to rotate without downtime, and,
for AI roles, where model-provider keys live, why they never reach the browser or the prompt, and how a
prompt-injected agent could exfiltrate a credential that was left in its context.

## Role lens
- **Engineer:** Reads secrets through a resolver with TTL and refetch-on-401, adds log redaction, uses
  build secrets in Dockerfiles, and keeps provider keys out of tool arguments and traces.
- **Architect:** Chooses the manager and the identity model per environment, defines the rotation policy
  and key-per-service boundaries, and sets the rule that credentials resolve in the tool runtime rather
  than in the model's context.
- **FDE:** Fits the product into the customer's Vault or cloud KMS, gets workload identity granted in
  their cluster, and answers the security questionnaire's questions on storage, rotation and audit.

## Check yourself
**Q:** A deployment on Kubernetes stores the model-provider key in a Kubernetes Secret created by hand,
reads it into an environment variable, and the same key is used by the API, the batch evaluator and a
developer's notebook. What are the risks, and how would you fix it in order of impact?
**A:** First, the key is shared, so a leak from the notebook exposes production and rotation would
break all three consumers at once; issue a per-service key with its own spend cap, kept behind a
gateway. Second, the Kubernetes Secret is base64 in etcd, unencrypted unless at-rest encryption is
configured, and readable by anyone with namespace access; source it from a managed secrets store through
the External Secrets Operator or CSI driver, enable etcd encryption, and restrict RBAC on secrets. Third,
the environment variable is frozen and inherited by child processes; mount it as a file and re-read on a
TTL so rotation needs no restart. Fourth, no identity story exists for the workload; use IRSA or workload
identity so the pod authenticates to the manager without a stored credential. Finally, add scanning,
redaction and a read-audit alert, and rotate the current key now because it has lived in a notebook.

## Quiz
### Q: What is the "secret zero" problem and what solves it?
- [ ] The first secret created in a vault is unencrypted until the vault is sealed; sealing solves it
- [x] A workload needs a credential to fetch its credentials; platform-attested identity (IAM roles, workload identity, OIDC federation) solves it without a stored secret
- [ ] Environment variables are numbered from zero and the first one is visible to all users
- [ ] The root token of a secrets manager cannot be rotated; a hardware module solves it
> If the bootstrap credential is a static key, the manager only relocates the problem. Identity that
> the platform asserts about the running workload breaks the chain, because nothing durable exists to
> steal.

### Q: Why are Kubernetes Secret objects not a sufficient secrets store on their own?
- [ ] They can hold at most one value per object
- [ ] They are only available to pods on the control-plane nodes
- [x] They are base64-encoded, unencrypted in etcd unless at-rest encryption is configured, and readable by any identity with namespace secret access
- [ ] They cannot be mounted as files, only as environment variables
> Base64 is an encoding, not encryption. Without an EncryptionConfiguration and tight RBAC, a
> namespace reader or an etcd backup exposes every value; a managed store feeding Secrets fixes the
> storage and audit gap.

### Q: An agent's tool for querying a customer's CRM needs an API token. Where should the token be handled?
- [ ] Passed to the model in the system prompt so it can include it in tool calls
- [ ] Returned to the model inside the tool's JSON result for subsequent calls
- [x] Resolved inside the tool runtime from the secrets store, keyed by tenant, so it never enters the model's context
- [ ] Stored in the conversation memory so multi-turn sessions can reuse it
> Anything in the model's context can be echoed back through prompt injection or captured in traces.
> The tool runtime looks the credential up by the tenant and the tool, uses it, and returns only data.
