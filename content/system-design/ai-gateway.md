---
id: ai-gateway
module: system-design
name: AI gateway (LiteLLM proxy, Portkey)
kind: tool
difficulty: core
est_minutes: 14
related:
  - python-for-ai/litellm
  - system-design/model-fallback-strategies
  - system-design/model-routing-cascading
  - system-design/caching-strategy-and-cache-invalidation
  - observability/token-cost-tracking
  - infra-capacity-economics/cost-allocation-by-tenant-and-feature
  - serving-inference/rate-limiting
  - security-governance-compliance/pii-protection-dlp
sources:
  - Official docs: LiteLLM
  - Official docs: Portkey
---

## TL;DR
An AI gateway is a proxy that every model call passes through: one OpenAI-style API in front of many
providers, with key custody, budgets, rate limits, routing, fallbacks, caching and logging handled once
instead of re-implemented in every application. LiteLLM's proxy is the open-source, Python,
config-file-driven option you run yourself; Portkey is a hosted gateway driven by declarative JSON
configs, with an open-source core. Both buy control and visibility at the price of an extra hop and a
new single point of failure.

## Why it exists
Without a gateway, the second team to ship an LLM feature copies the first team's retry loop, pastes
the same provider key into its own secret store, and starts spending against the same organisation
quota with no coordination. By the fifth team nobody can answer basic questions: how much does feature X
cost per month, which models are in production, what data is sent to which vendor, and how do we switch
everything off if a key leaks or a provider has an incident. Rate limits hit unpredictably because five
services share one quota. A provider migration touches every codebase.

The naive fix is a shared client library. It helps with code reuse but cannot *enforce* anything: teams
pin old versions, polyglot stacks need one library per language, keys still live in every service, and
org-wide rate-limit and budget coordination needs shared state a library does not have. The same
problem in microservices produced the API gateway; the AI gateway is that idea with model-specific
concerns added: token accounting, model aliasing, provider translation, streaming passthrough, prompt
logging and guardrails. It moves cross-cutting policy to a network boundary where it applies to every
caller whether or not they remembered to.

## How it works
### The request path
A client sends an OpenAI-compatible request (`/v1/chat/completions`, embeddings and so on) to the gateway
with a *virtual* key. The gateway authenticates the key, resolves the requested model alias to one or
more deployments, applies policy (budget remaining, requests and tokens per minute, guardrails), picks a
deployment by its routing strategy, translates to the provider's native API, streams the response back,
and records usage (tokens, computed cost, latency, key, tags) for logging and export. The API is
OpenAI-shaped *because* most SDKs already speak it, so adoption is changing a base URL and a key.

### LiteLLM proxy
An open-source Python server (`litellm --config config.yaml`) and a matching in-process `Router` class.
The YAML config has a `model_list` of deployments (an alias name plus `litellm_params` with the provider
and model, keys referenced as `os.environ/VAR`), `router_settings` (routing strategy, retries, fallbacks,
cooldowns for failing deployments), `litellm_settings` (caching, logging callbacks to tracing backends)
and `general_settings` (the master key, the database). Virtual keys with budgets and rate limits per key,
team or user are stored in Postgres; Redis holds shared state such as rate-limit counters, cache entries
and cooldowns across replicas. The same alias can point at several deployments, which is how
load-balancing and same-model failover work.

### Portkey
A hosted gateway (the core gateway is also open source, written in TypeScript) where behaviour is
declared in a config: a `strategy` (fallback or load-balance) over `targets`, each with its own virtual key
and optional retry, cache (exact or semantic) and timeout settings. Clients pass a config id or inline
config in request headers; provider keys live in Portkey's vault as virtual keys; traces, cost and
metadata tags appear in its dashboards; guardrails attach to configs. Hosted removes the operational
burden but adds a third-party processor to your data path, which compliance must review, unless you
deploy the gateway inside your own environment.

### What it centralises
Key custody; budgets and quotas per team or customer; rate-limit coordination; retries and fallbacks;
caching; PII redaction and guardrails; logging and cost attribution via tags; and model aliases, so
applications call `fast` or `strong` while the platform team decides what those resolve to, which makes
migrations and deprecations configuration changes rather than code changes.

### Sharp edges
It is a lowest-common-denominator API: provider-specific features (prompt-cache controls, extended
reasoning parameters, tool-call nuances, vision input formats) lag or are dropped unless passthrough is
verified. It is a new single point of failure and a new hop: run several replicas per region, keep it
close to callers, and decide what happens when its Postgres or Redis is unavailable (fail open on budget
checks, or fail closed and take every AI feature down with the database). Streaming must be forwarded
without buffering or time-to-first-token suffers. Token counts across tokenisers are approximations.
Gateway logs contain prompts, so they need a retention policy. And someone must own the config, or
aliases rot and fallbacks go untested.

## Example
A LiteLLM configuration with two deployments behind one alias, a stronger fallback, caching and logging,
followed by the application code, which only needs the OpenAI SDK pointed at the proxy. Replace the
model ids with current ones for your providers.

```yaml
# config.yaml: applications call "fast" or "strong"; the platform team decides what those mean
model_list:
  - model_name: fast
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
  - model_name: fast                          # second deployment of the same alias shares the traffic
    litellm_params:
      model: azure/gpt-4o-mini-eu             # azure/<your-deployment-name>
      api_base: os.environ/AZURE_EU_BASE
      api_key: os.environ/AZURE_EU_KEY
  - model_name: strong
    litellm_params:
      model: anthropic/claude-sonnet-4-20250514
      api_key: os.environ/ANTHROPIC_API_KEY
router_settings:
  routing_strategy: simple-shuffle
  num_retries: 2
  fallbacks: [{"fast": ["strong"]}]           # when every "fast" deployment fails, try "strong"
litellm_settings:
  cache: true                                 # exact-match response cache (Redis when configured)
  success_callback: ["langfuse"]              # usage and cost per request to your tracing backend
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL       # virtual keys, teams and budgets live here
```

```python
from openai import OpenAI
client = OpenAI(base_url="http://litellm.internal:4000/v1", api_key="sk-<support-team-virtual-key>")
r = client.chat.completions.create(
    model="fast", messages=[{"role": "user", "content": "Summarise ticket #4821 in two lines."}],
    extra_body={"metadata": {"tags": ["support-bot", "tenant:acme"]}})   # tags drive cost attribution
print(r.choices[0].message.content)
```

The application never sees a provider key, and moving `fast` to a different model is a config change
and a canary, not a deploy of every service.

## Trade-offs
- One place for keys, budgets and logs, against one more network hop and a shared failure point; the
  balance tips badly if you run a single replica or place it in a different region from callers.
- Aliases make provider migrations cheap, but the unified API lags provider features; it flips when a
  provider-specific capability such as cache controls is core to your product and is not passed through.
- A hosted gateway removes operations and gives dashboards on day one, while a self-hosted LiteLLM keeps
  prompts in your VPC but makes you run Postgres, Redis and upgrades; compliance usually decides.
- Central routing policy lets a platform team move traffic without touching applications, at the cost
  of application teams losing fine control; a single app with unusual needs may be better served
  in-process.
- Gateway-level caching is a one-line win but is context-blind; its safety depends entirely on keys and
  tags scoping entries per tenant.

## Pitfalls
- A fail-closed dependency on the gateway's database; the symptom is every AI feature in the company
  going down when Postgres restarts for maintenance.
- Buffering streamed responses in the proxy; the symptom is time-to-first-token jumping from hundreds
  of milliseconds to seconds right after adoption.
- A shared cache without tenant scoping; the symptom is a cached answer containing one customer's data
  served to another, which is an incident, not a bug.
- Assuming feature parity across providers; the symptom is provider-specific parameters silently
  dropped and cost or quality regressing after a migration that "worked".
- One master key shared by all applications; the symptom is zero cost attribution and an unbounded blast
  radius when the key leaks.
- Nobody owning the configuration; the symptom is dozens of aliases, half pointing at deprecated models,
  with fallbacks that have never been exercised.

## When to use
Adopt a gateway once more than one team or more than one provider is involved, or as soon as finance or
compliance asks for spend by team and a list of what is sent where; for a single application an
in-process router (LiteLLM's SDK or a thin client) is lighter. Choose the LiteLLM proxy when you want an
open-source component inside your own network and can operate a small Python service with Postgres and
Redis; choose Portkey when hosted observability and declarative configs matter more and your data rules
allow a processor. Measure added latency at p50 and p99, gateway availability, cache hit rate and spend
per key. In interviews, be able to say what a gateway centralises and what it cannot do, which is make
outputs correct.

## Role lens
- **Engineer:** Points the SDK at the gateway, calls aliases rather than model ids, tags requests for
  attribution, and debugs with gateway logs instead of five provider dashboards.
- **Architect:** Decides placement per region, fail-open versus fail-closed policy, config ownership, and
  the boundary between what belongs in the gateway (routing, keys) and in the application (prompt logic).
- **FDE:** Deploys a gateway inside the customer's cloud to satisfy key-custody and egress policies, and
  uses its per-key reports to show each business unit its own spend.

## Check yourself
**Q:** A platform team proposes routing all LLM traffic through a single LiteLLM proxy in one region,
backed by one Postgres instance, and failing requests whenever the budget check cannot run "so budgets
are never exceeded". Critique the design.
**A:** Three problems. A single replica in one region is a company-wide single point of failure and adds
cross-region latency for every service elsewhere; run at least two replicas per region behind a load
balancer, with Redis for shared counters. Failing closed on the budget check couples every AI feature to a
database's uptime for a soft financial control; budgets should fail open with asynchronous
reconciliation and alerts, reserving fail-closed for hard compliance limits on untrusted keys. Finally,
the proposal says nothing about operations: verify streaming passthrough, test the fallback chains, set a
retention policy for logged prompts, assign an owner for aliases, and document an emergency bypass. The
gateway is the right idea; the single-instance, fail-closed shape is what needs to change.

## Quiz
### Q: Which concern is an AI gateway least able to solve?
- [ ] Attributing spend to teams and features
- [ ] Failing over to another provider during an outage
- [x] Making a feature's outputs correct for its task
- [ ] Keeping provider keys out of application code
> A gateway controls where requests go and what is recorded about them. Output quality depends on
> prompts, retrieval, models and evaluation, which live in the application and its eval harness, not at
> the network boundary.

### Q: What most distinguishes the LiteLLM proxy from Portkey?
- [ ] Portkey only supports OpenAI models
- [x] LiteLLM is an open-source Python proxy you run with a YAML config; Portkey is primarily a hosted gateway driven by declarative configs, with an open-source core
- [ ] LiteLLM cannot do fallbacks or load balancing
- [ ] Portkey requires you to operate your own Postgres
> Both offer routing, fallbacks, caching and observability across many providers. The real difference is
> operating model and where your data flows: self-hosted Python with your own database versus a hosted
> service with dashboards.

### Q: Why do applications call model aliases such as "fast" and "strong" instead of concrete model ids?
- [ ] Aliases reduce the number of tokens billed
- [ ] Aliases make model outputs deterministic
- [x] Aliases let the platform team remap, load-balance and migrate models by configuration without changing application code
- [ ] The OpenAI API specification requires aliases
> Indirection is what makes deprecations and migrations cheap: the mapping from alias to deployments
> changes in one place and can be canaried, while the application code stays untouched.
