---
id: litellm
module: python-for-ai
name: LiteLLM
kind: tool
difficulty: core
est_minutes: 14
status: reviewed
related:
  - python-for-ai/openai-sdk
  - python-for-ai/anthropic-sdk
  - system-design/ai-gateway
  - system-design/model-routing-cascading
  - system-design/model-fallback-strategies
  - observability/token-cost-tracking
  - infra-capacity-economics/cost-allocation-by-tenant-and-feature
  - security-governance-compliance/vendor-exit-strategy
sources:
  - Official docs: LiteLLM
---

## TL;DR
LiteLLM is an open-source Python library and proxy server that puts many LLM providers behind one
OpenAI-style interface: `completion(model="anthropic/...", messages=...)` returns the same response shape
whichever provider served it. On top it adds routing, retries, fallbacks, cost tracking and, as a proxy,
virtual keys and budgets. It exists because multi-provider code otherwise means one integration per
vendor, and its main cost is that translation can hide or lag provider-specific features.

## Why it exists
Teams rarely stay on one model. They want a cheap model for classification and a strong one for
reasoning, a second provider for when the first has an outage or rate-limits them, a self-hosted model
for sensitive data, and the ability to evaluate a new model without rewriting call sites. Each provider
has its own SDK, message format, tool-call shape, error classes, streaming events and usage fields. If
product code calls them directly, every new provider touches every call site, fallbacks are implemented
ad hoc in each service, and nobody can answer "what did we spend on model X for feature Y last month"
because usage is logged in five shapes.

The naive fix is an internal wrapper. That works for two providers and one capability, but it grows
into a maintained translation layer for tool schemas, streaming, error mapping and pricing tables,
which is precisely the boring, fast-changing work a shared library amortises. LiteLLM centralises that
translation, and its proxy mode moves it out of application code entirely: services talk to one
OpenAI-compatible endpoint, and the platform team controls which models exist, who may use them, and
how much they may spend. The trade is a dependency that sits on every LLM call, so its correctness,
latency and update cadence become yours.

## How it works
### Unified call
`litellm.completion(model="<provider>/<model>", messages=[...])` (and `acompletion` for asyncio) parses
the provider prefix (`openai/`, `anthropic/`, `bedrock/`, `vertex_ai/`, `ollama/`, `hosted_vllm/` and
many more), translates the OpenAI-format request into that provider's native request, calls it, and
translates the response back into an OpenAI-format object: `choices[0].message`, `tool_calls`, and
`usage`. Credentials come from provider-specific environment variables. Parameters a provider does not
support raise an error by default; `drop_params=True` silently drops them instead, which is convenient
and dangerous in equal measure.

### What translation actually does
Take one OpenAI-format request sent to `anthropic/...`. The `system` message is pulled out of `messages`
into Anthropic's top-level `system` field; a `max_tokens` value is supplied if you omitted one, because
Anthropic requires it; each OpenAI `tools[].function` becomes a `name`/`description`/`input_schema`
tool; an assistant message's `tool_calls` become `tool_use` blocks, and each `role: "tool"` message
becomes a `tool_result` block inside a user message. On the way back, `tool_use` blocks become
`tool_calls` with `arguments` re-serialised as a JSON string, `stop_reason` maps to `finish_reason`
(`end_turn` to `stop`, `tool_use` to `tool_calls`, `max_tokens` to `length`), and `input_tokens` /
`output_tokens` become `prompt_tokens` / `completion_tokens`. Every one of these mappings is a place where
a provider feature with no OpenAI equivalent (thinking blocks, cache breakpoints, citations) has to be
carried in an extra field or is lost, which is why translation fidelity is the thing to test.

### Errors
Provider errors are mapped onto OpenAI-style exception types (rate limit, context window exceeded,
authentication, bad request), so retry and fallback logic can be written once against one hierarchy.

### Router
`litellm.Router` takes a `model_list` where several deployments can share one logical `model_name`
(for example two Azure regions plus OpenAI direct). It load-balances across them with a configurable
`routing_strategy` (random `simple-shuffle` by default, or options that prefer the least busy, lowest
latency or least used deployment), tracks failures and puts a deployment in cooldown once it exceeds
`allowed_fails` (for `cooldown_time` seconds), retries with `num_retries`, and applies `fallbacks` that
map one logical model to others, plus specialised `context_window_fallbacks` (overflow to a
longer-context model) and `content_policy_fallbacks`. Callers ask for the logical name; topology lives
in config. The order matters: retries happen within the group first, and fallback to another group only
when the group as a whole has failed, because a different model is a bigger behavioural change than a
different deployment of the same one.

### Cost and callbacks
LiteLLM maintains a model price map and computes per-call cost from usage (`completion_cost`), as
prompt tokens × input price + completion tokens × output price, looked up by model name. A custom
deployment name (an Azure deployment called `my-gpt4o-mini-eu`) is not in that map, so you declare its
`base_model` in the deployment's `model_info` for the lookup to work.
Callbacks send each call's inputs, outputs, latency and cost to logging and observability backends.

### Proxy (gateway) mode
The proxy runs the same machinery as a server exposing OpenAI-compatible endpoints, configured by a
YAML file of models, routing and fallbacks. It adds virtual API keys mapped to teams or users, budgets
and rate limits per key, spend logs in a database, and central guardrail hooks. Applications then use any
OpenAI-compatible client, in any language, pointed at the proxy's `base_url`. The core is open source,
but some proxy features aimed at enterprises (for example SSO and certain audit and admin controls) sit
behind a commercial licence, so check which ones you need before standardising on it.

## Example
A router with a logical "chat" model, two deployments, retries and a cross-provider fallback.
Model names are illustrative; take them from config.

```python
import litellm
from litellm import Router

router = Router(
    model_list=[
        {"model_name": "chat", "litellm_params": {"model": "openai/gpt-4o-mini"}},
        {"model_name": "chat", "litellm_params": {"model": "azure/my-gpt4o-mini-eu",
                                                  "api_base": "https://example-eu.openai.azure.com"}},
        {"model_name": "chat-backup", "litellm_params": {"model": "anthropic/claude-haiku-5-5"}},
    ],
    num_retries=2,                                # retries within the group; may pick another deployment
    fallbacks=[{"chat": ["chat-backup"]}],        # if all "chat" deployments fail
    timeout=20,
)

resp = router.completion(
    model="chat",
    messages=[{"role": "user", "content": "Summarise: the refund was issued on 3 May."}],
    metadata={"feature": "ticket-summary", "tenant": "acme"},   # for cost attribution in callbacks
)
print(resp.model, resp.choices[0].message.content)
print(resp.usage.prompt_tokens, resp.usage.completion_tokens, litellm.completion_cost(resp))
```

Scenario: OpenAI returns 429s for a minute. By default the router spreads "chat" traffic across both
deployments (simple shuffle); the failing calls are retried, the OpenAI deployment is put in cooldown
after repeated failures, so traffic lands on the EU Azure deployment, and only if the "chat" group as a
whole fails does the request go to the Anthropic backup, with the caller still receiving an
OpenAI-shaped response.

## Trade-offs
- One interface across providers makes switching and A/B testing cheap, but the lowest common
  denominator hides features; Claude cache breakpoints or Responses-API tools may need provider-specific params.
- The in-process library adds no network hop, but every service needs credentials and config; the proxy
  centralises keys, budgets and logs at the price of an extra hop and a component you must run highly available.
- Automatic fallbacks raise availability, but a fallback model may behave differently on your prompts;
  without evals per fallback pair, an outage silently becomes a quality regression.
- Built-in price maps make cost visible immediately, but they can lag price changes and negotiated rates;
  reconcile against provider invoices before using them for chargeback.

## Pitfalls
- Enabling `drop_params=True` globally: a parameter you rely on (structured output, a stop sequence) is
  quietly discarded for one provider, and outputs change format only when a fallback fires.
- Assuming tool calling and streaming behave identically everywhere: translation edge cases show up as
  malformed tool arguments or missing usage in streamed responses from one provider.
- Fallback chains without evals: during an incident, users see a different model's style or lower
  accuracy, and support tickets spike with no error in the logs.
- Unpinned versions of a fast-moving dependency on every LLM call: an upgrade changes defaults or
  pricing data, and the symptom is a behaviour or cost change nobody deployed deliberately.
- Stacking retry layers: application retries × router `num_retries` × any retries the underlying
  client performs × each fallback group; the symptom is a single user request turning into a dozen upstream
  calls during an incident, amplifying the provider's rate limiting. Own retries in exactly one layer.
- Cost reports showing zero or raising for some deployments: the custom deployment name is not in the
  price map; declare `base_model` so spend attribution is not silently undercounted.
- Running the proxy as a single instance: it becomes the single point of failure for every AI feature,
  and one restart takes them all down.

## When to use
Reach for LiteLLM when you genuinely use, or must be able to switch between, several providers: routing
by cost or capability, cross-provider fallbacks, evaluation harnesses comparing many models, or a
platform team that needs central keys, budgets and spend attribution across many services (proxy mode).
Stay with a native SDK when one provider is strategic and you depend on its newest features, or when the
call volume is small and an extra abstraction is not worth owning. Measure fallback rate, per-model
latency and error rates, cost per feature and tenant, and quality on an eval set for each fallback pair.
Interviewers often ask how you would build provider failover and where the abstraction should stop.

## Role lens
- **Engineer:** Calls models through one interface, writes retry and fallback config, attaches
  metadata for cost attribution, and handles provider-specific parameters where translation falls short.
- **Architect:** Decides library versus gateway, which providers and fallback pairs are allowed, how
  budgets and keys are governed, and keeps a vendor-exit path that does not depend on one SDK.
- **FDE:** Deploys the proxy inside a customer's network to route between their approved cloud models
  and a local model, giving their security team one place to audit and cap usage.

## Check yourself
**Q:** Your product runs on one provider. Leadership asks for "no single point of failure for AI". A
colleague proposes adding LiteLLM with a fallback to a second provider. What else must be true for this
to actually deliver resilience?
**A:** The fallback must be tested, not just configured. Prompts, tool schemas and output parsing need
to work on the second model, so I would run the eval suite against it and fix prompt or schema
differences first. Features that do not translate, such as provider-specific caching or structured
output modes, need equivalents or graceful degradation. Rate limits and quotas on the backup must cover
failover traffic. The routing layer itself must not become the new single point of failure, so the
proxy runs with multiple replicas or the library is embedded. Finally, alert on fallback rate, so a
silent permanent failover with worse quality is noticed.

## Quiz
### Q: What does LiteLLM's response translation give application code?
- [ ] A guarantee that every provider produces identical outputs for the same prompt
- [x] One OpenAI-format response shape, including usage and tool calls, regardless of provider
- [ ] Automatic prompt rewriting optimised for each model
- [ ] Access to every provider-specific feature through standard OpenAI parameters
> Translation normalises the shape of requests and responses, so parsing and logging are written once.
> It cannot make models behave the same, and some provider-specific features have no standard equivalent.

### Q: When is LiteLLM's proxy mode a better choice than the in-process library?
- [ ] When a single Python service calls one provider
- [ ] When the lowest possible latency is the only goal
- [x] When many services and languages need shared keys, budgets, rate limits and spend logs
- [ ] When you need offline access to models without any server
> The proxy centralises governance behind an OpenAI-compatible endpoint any client can use. For one
> service and one provider, the extra hop and operational burden rarely pay off.

### Q: Why is `drop_params=True` risky as a global default?
- [ ] It doubles the token cost of each request
- [ ] It disables retries on rate-limit errors
- [x] Unsupported parameters are silently removed, so behaviour can change only when a different provider serves the call
- [ ] It prevents streaming responses from working
> Silently dropping a parameter such as a response format or stop sequence turns a loud error into a
> quiet behaviour change, often visible only during a fallback when debugging is hardest.
