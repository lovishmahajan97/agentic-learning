---
id: tenant-specific-configuration-and-feature-flags
module: enterprise-integration
name: Tenant-specific configuration & feature flags
kind: pattern
difficulty: core
est_minutes: 15
related:
  - system-design/tenant-isolation
  - enterprise-integration/sandbox-vs-production-environments
  - customer-data-evaluation/per-customer-guardrails
  - customer-data-evaluation/customer-specific-regression-suites
  - prompting/prompt-versioning
  - deployment/rollout-criteria-and-rollback-triggers
  - llmops-platform/canary-shadow-deployments
  - backend-fundamentals/secrets-management
sources:
  - Hodgson, 2017 — Feature Toggles (aka Feature Flags)
  - Official docs: OpenFeature
---

## TL;DR
Every enterprise customer wants something slightly different: another model, a stricter guardrail, a
different connector, a feature switched off until their legal team signs off. Tenant-specific
configuration expresses those differences as typed, versioned data resolved at runtime, and feature
flags decide who gets which code path, so one codebase serves every customer. The alternative, a branch
or fork per customer, works for the first three and becomes unmaintainable by the tenth.

## Why it exists
The first enterprise customer gets the product as built. The second needs answers in German, a
retrieval source that is Confluence rather than SharePoint, and a refusal policy for HR questions. The
third is in a regulated industry and cannot use one of your model providers at all. The fastest way to
ship each of these is an `if customer == "acme":` in the code, or a branch deployed only for them. Both
work at first and both decay quickly.

Hard-coded customer checks scatter policy across the codebase, so nobody can answer "what is different
about Acme?" without grepping, and every change has to consider every customer's special case by hand.
Per-customer branches are worse: a security fix or a model upgrade must be merged into N branches,
testing multiplies by N, and the branches drift until some customers run code months old. The naive
alternative of "one configuration for everyone" fails the other way, because enterprise deals are often
won on exactly these differences.

The pattern that survives is to separate *what varies per tenant* (data, resolved at runtime) from
*the code that reads it* (one version, deployed everywhere), and to separate *configuration* (a durable
statement about how this tenant works) from *feature flags* (a usually temporary decision about who sees
a code path). Doing so makes differences inspectable, testable and auditable, and lets you change a
tenant's behaviour without a deploy, which in an AI product is frequent because prompts, models and
thresholds change far more often than code.

## How it works
### What goes in tenant configuration
Typical per-tenant settings in an AI product: allowed model providers and the default model; prompt
template version; retrieval sources and their connector settings; guardrail policy and thresholds;
language and tone; data residency region; rate limits and token budgets; enabled integrations; and
which tools an agent may call. Secrets (API keys, connector credentials) are *referenced* by name from
configuration and stored in a secrets manager, because configuration is read widely, logged and diffed,
and secrets must not be.

### Layered resolution
Configuration is resolved by merging layers from general to specific, with the most specific winning:

```
global defaults  <  plan tier  <  region / deployment  <  tenant  <  environment override
```

This exists *because* most tenants differ in a handful of keys. Storing full configurations per tenant
means a new default must be copied into every tenant record; storing only overrides means a new default
reaches everyone who has not opted out. The resolved result is validated against a typed schema before
use, because a typo in a key or an out-of-range threshold should fail at write time in an admin tool,
not at 2 a.m. inside a request.

### Versioning and audit
Each change to a tenant's configuration creates a new immutable version with author, timestamp and
reason, and requests log the configuration version they ran under. This matters more for AI than for
ordinary software: when a customer reports that answers got worse on Tuesday, the first question is what
changed, and a prompt version or retrieval threshold changed by an admin is as likely a cause as a deploy.
A config change that alters model behaviour should go through the same evaluation gate as code: run the
tenant's regression suite against the new version before activating it.

### Feature flags, and their four kinds
A flag is a runtime decision about which code path runs, evaluated against a context (tenant, user,
environment, percentage bucket). Hodgson's taxonomy separates them by lifetime and owner, which is the
useful distinction:
- **Release flags** hide unfinished or unproven work; short-lived, removed once the feature is on for
  everyone.
- **Experiment flags** split traffic to compare variants (two prompts, two rerankers); removed when the
  experiment concludes.
- **Ops flags / kill switches** let on-call turn off an expensive or misbehaving path (a costly model, an
  external tool) without a deploy; long-lived by design.
- **Permission or entitlement flags** gate features by plan or contract; effectively long-lived tenant
  configuration and better modelled as such.

Treating these the same causes trouble: release flags that are never removed become permanent
branching, and entitlements implemented as ad hoc flags get lost when someone cleans up "old flags".

### Evaluation mechanics
Flag and config lookups sit on the request path, so they are served from an in-process cache refreshed
by polling or streaming from the flag service, with a safe default if the service is unreachable. The
default must be the conservative path (feature off, stricter guardrail) *because* an outage of the
config service should degrade capability, not remove protections. Percentage rollouts hash a stable key
(tenant ID, or user ID within a tenant) so a given user stays in the same bucket across requests.
Vendor-neutral SDK interfaces such as OpenFeature let application code evaluate flags without binding
to one flag vendor.

### Keeping the matrix testable
Each independent boolean doubles the number of possible combinations, so you cannot test them all. The
practical answer is to test the combinations tenants actually run (pull the resolved configs of live
tenants into CI), keep the number of live flags small, and give each release flag an owner and a removal
date.

## Example
A minimal layered resolver with validation, versioning and a conservative fallback. The schema is the
contract; overrides are partial dictionaries.

```python
from dataclasses import dataclass, replace, fields
import hashlib

@dataclass(frozen=True)
class TenantConfig:
    model: str = "default-large"
    prompt_version: str = "support-v7"
    retrieval_top_k: int = 8
    pii_redaction: bool = True
    allowed_providers: tuple = ("provider-a", "provider-b")
    new_reranker_pct: int = 0           # release flag: % of users on the new reranker

    def validate(self):
        assert 1 <= self.retrieval_top_k <= 50, "retrieval_top_k out of range"
        assert 0 <= self.new_reranker_pct <= 100
        assert self.model_provider() in self.allowed_providers, "model not allowed for tenant"

    def model_provider(self):
        return "provider-b" if self.model.startswith("b-") else "provider-a"

LAYERS = {  # in production: rows in a versioned config store
    "plan:enterprise": {"retrieval_top_k": 12},
    "tenant:acme": {"prompt_version": "support-v7-de", "allowed_providers": ("provider-a",)},
    "tenant:globex": {"new_reranker_pct": 25},
}

def resolve(tenant: str, plan: str) -> TenantConfig:
    cfg, known = TenantConfig(), {f.name for f in fields(TenantConfig)}
    for layer in (f"plan:{plan}", f"tenant:{tenant}"):
        override = LAYERS.get(layer, {})
        unknown = set(override) - known
        if unknown: raise ValueError(f"{layer}: unknown keys {unknown}")   # typo caught here
        cfg = replace(cfg, **override)
    cfg.validate()
    return cfg

def in_rollout(user_id: str, pct: int) -> bool:   # stable bucket per user
    return int(hashlib.sha256(user_id.encode()).hexdigest(), 16) % 100 < pct

cfg = resolve("globex", "enterprise")
print(cfg.retrieval_top_k, in_rollout("globex:u-1842", cfg.new_reranker_pct))
```

Acme gets a German prompt and a single allowed provider; Globex gets a quarter of users on the new
reranker; both inherit the enterprise `top_k`. Setting `model="b-large"` for Acme would fail validation
before any request runs, because the contract forbids that provider for that tenant.

## Trade-offs
- Runtime configuration lets you change tenant behaviour without a deploy, but a config change can break
  a tenant as surely as a code change, so it needs the same review and evaluation gate once it touches
  prompts, models or thresholds.
- Layered overrides keep tenant records small and let new defaults propagate, but they make "what does
  this tenant actually run?" a computed answer; you need a tool that shows the resolved config and which
  layer set each key.
- Feature flags decouple deploy from release and give on-call kill switches, but each live flag doubles
  the theoretical test matrix; the benefit flips to a cost once flags outlive their purpose.
- A managed flag service gives targeting, audit and UI for free, but adds a dependency on the request
  path; for a handful of long-lived entitlements a typed config table you own is simpler.
- Allowing deep per-tenant customization wins deals, but past some point you are running N products; the
  trade flips when customization requests start requiring code paths no other tenant uses.

## Pitfalls
- Customer names in code (`if tenant == "acme"`); the symptom is that nobody can list a tenant's
  differences without grepping, and a refactor silently drops one customer's behaviour.
- Release flags that are never removed; the symptom is dozens of stale flags, dead code paths that
  still need testing, and an incident when someone flips one nobody remembers.
- A fallback default that is the permissive path; the symptom shows during a flag-service outage, when
  guardrails or redaction quietly switch off for every tenant.
- Not logging the config version per request; the symptom is a quality regression report you cannot
  attribute, because a prompt version changed by an admin left no trace in the trace.
- Putting secrets or connector credentials directly in tenant configuration; the symptom is API keys
  appearing in admin UIs, config diffs and debug logs.
- Unvalidated free-form JSON config; the symptom is a typoed key that is silently ignored, so the tenant
  runs the default and nobody notices until the customer does.

## When to use
Introduce tenant configuration as soon as a second customer needs something the first does not, and
before the first customer-specific `if` lands. Keep it typed and validated from day one; retrofitting a
schema onto a pile of free-form overrides is painful. Use release flags for every risky AI change
(a new model, prompt or retriever), enabling it tenant by tenant while watching each tenant's eval and
feedback metrics. Use kill switches for anything expensive or external. Measure the number of live
flags, their age, and how many tenants run non-default values for each key; a key that every tenant
overrides should become the new default. In interviews, expect to be asked how you would roll out a new
model to fifty enterprise tenants without a fork per customer.

## Role lens
- **Engineer:** Implements the typed resolver, caches lookups on the request path, logs config version
  and flag decisions in every trace, and deletes release flags when the rollout completes.
- **Architect:** Decides what may vary per tenant and what may not, sets the layering model, and
  requires config changes affecting model behaviour to pass the same eval gate as code.
- **FDE:** Translates each customer requirement into configuration rather than custom code, and pushes
  back when a request would need a tenant-only code path the product team must then maintain.

## Check yourself
**Q:** You must move fifty enterprise tenants from one LLM to another. Three are contractually barred
from the new provider, and two have had quality complaints in the past. How do you structure the rollout?
**A:** Make the model a tenant configuration key constrained by each tenant's allowed-providers list, so
the three barred tenants cannot be switched even by mistake: validation rejects it. Gate the switch with
a release flag targeted by tenant. Run each tenant's regression suite against the new model before
enabling it for them, start with low-risk tenants, and leave the two sensitive ones until last, ideally
with shadow traffic first. Log model and config version per request so any regression is attributable,
keep the old model as the conservative fallback and a kill switch, and remove the flag once every
eligible tenant is migrated.

## Quiz
### Q: Why should a feature-flag client fall back to the conservative path when the flag service is unreachable?
- [ ] Because the conservative path is always faster to execute
- [x] Because an outage of the config service should reduce capability, not remove protections such as guardrails
- [ ] Because flag services guarantee the last value is stale after a timeout
- [ ] Because percentage rollouts cannot be computed without the service
> If the default is permissive, a flag-service outage silently disables redaction or guardrails for every
> tenant. A conservative default turns the same outage into a temporary loss of new features instead.

### Q: What is the main advantage of storing tenant overrides rather than a full configuration per tenant?
- [ ] Overrides are cheaper to store in the database
- [ ] Overrides remove the need for schema validation
- [x] A new global default reaches every tenant that has not explicitly overridden that key
- [ ] Overrides make the resolved configuration easier to see at a glance
> With full copies, every new default must be backfilled into each tenant record. Overrides keep records
> small and let defaults propagate, at the cost of needing a tool to show the resolved result.

### Q: Which flag is expected to be long-lived by design?
- [ ] A release flag for a new reranker
- [ ] An experiment flag comparing two prompts
- [x] An ops kill switch that disables an expensive external tool
- [ ] A flag hiding a half-built admin page
> Kill switches exist for incidents that can happen at any time, so they stay. Release and experiment
> flags should be removed once the rollout or experiment ends, or they become permanent branching.
