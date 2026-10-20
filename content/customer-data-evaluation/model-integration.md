---
id: model-integration
module: customer-data-evaluation
name: Model integration
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - system-design/ai-gateway
  - system-design/model-selection
  - system-design/model-fallback-strategies
  - python-for-ai/litellm
  - prompting/function-tool-calling
  - llmops-platform/model-registry
  - customer-data-evaluation/customer-specific-regression-suites
  - customer-data-evaluation/fine-tuning-pipelines-on-client-data
---

## TL;DR
Model integration is the work of putting a different model behind a delivered system: a new provider,
a fine-tuned adapter, or the model a customer mandates (their own cloud deployment or an on-prem open-
weight model). A model is not a drop-in part: prompts, tool-call formats, tokenizers, context limits,
error semantics and quotas all differ. So integration means a normalising adapter, a capability check,
re-tuned prompts, and the customer's eval suite as the gate before traffic moves.

## Why it exists
In customer work the model is often not your choice. A bank requires inference inside its own cloud
tenancy, so you must use the models its cloud offers. A defence customer is air-gapped, so it is an
open-weight model on its GPUs. Another customer has already negotiated an enterprise agreement with a
different provider. And your own fine-tuning pipeline produces a new adapter every few months. Each
time, the same application has to run on a model it was not built against.

The naive approach is to change the endpoint and model name in config and run a smoke test. It fails in
ways a smoke test does not catch. The prompt was tuned against the old model's habits, so instructions
the old model followed reliably are now followed loosely. Tool calls come back in a different structure,
or the model emits arguments as a JSON string instead of an object. The new tokenizer counts the same
text as more tokens, so a context budget that fit now truncates retrieved documents. A self-hosted
model served with the wrong chat template produces outputs that look plausible but are measurably worse,
with no error anywhere. A "max tokens" parameter means something different. Rate-limit errors carry a
different status code, so the retry logic treats them as fatal.

None of these crash in a smoke test; they show up as quality loss on the customer's traffic weeks later.
Integration has to make differences explicit and measure their effect.

## How it works
### 1. Normalise behind an adapter
The application talks to one internal interface: messages in; text, tool calls, usage, finish reason
and a typed error out. Each provider or serving stack gets an adapter that maps to and from it. Because
the application depends on the interface, not the vendor SDK, swapping models changes one adapter and a
config entry instead of every call site. Gateways and libraries (an AI gateway, LiteLLM and similar)
give you much of this mapping, but you still own the contract and its tests.

### 2. Declare capabilities, check them at startup
Models differ in context window, maximum output, tool calling, parallel tool calls, schema-constrained
output, vision input and streaming events. Record these per model in a capability entry and have the
application check what it needs when it loads its config, so "this deployment needs strict JSON schema
and the configured model does not support it" fails at deploy time rather than in production.

### 3. Recompute budgets with the right tokenizer
Context budgeting (how many retrieved chunks fit, when to compact history) must use the target model's
tokenizer or token-counting endpoint. The same text can tokenize to noticeably different counts across
model families, and that difference is the whole safety margin.

### 4. Get the serving details right for self-hosted models
Use the model's own chat template (from its tokenizer config), the correct stop tokens and the
recommended sampling defaults. Template mismatches degrade quality silently, so a template unit test that
renders a known conversation and compares it with the expected string is worth having.

### 5. Port the prompts, do not copy them
Treat prompts as model-specific artifacts, versioned per model in the prompt registry. Run the
customer's regression suite, read the failures, adjust instructions and examples, and repeat. Expect
format and refusal behaviour to need the most work.

### 6. Map errors and quotas
Translate each provider's errors into your own taxonomy (rate limited, overloaded, context too long,
content filtered, invalid request, auth) so retries, fallbacks and alerts behave the same on every model.
Size concurrency to the customer's quota on their deployment, which is often lower than yours.

### 7. Pin versions and gate the switch
Configure an exact model version or snapshot, not a moving alias, so the model cannot change under you.
Then: adapter contract tests, the customer's regression suite, shadow mode on their traffic, and a
per-tenant routing switch with the old model as fallback. Register what was deployed (model id, version,
adapter, prompt version) so incidents can be traced to a specific combination.

## Example
A minimal normalised interface and a capability check. The application only sees `Completion`, and a
deployment refuses to start if the configured model lacks a capability it depends on.

```python
from dataclasses import dataclass, field
from typing import Optional, Protocol
import json

@dataclass
class ToolCall:
    name: str
    args: dict

@dataclass
class Completion:
    text: str
    tool_calls: list = field(default_factory=list)
    finish: str = "stop"            # normalised: stop | length | tool_use | filtered
    input_tokens: int = 0
    output_tokens: int = 0

class ModelError(Exception):
    def __init__(self, kind: str, retryable: bool):  # kind: rate_limited | context_length | ...
        super().__init__(kind); self.kind, self.retryable = kind, retryable

class ChatModel(Protocol):
    capabilities: set
    context_window: int
    def complete(self, messages: list, tools: Optional[list] = None) -> Completion: ...

def parse_args(raw) -> dict:
    """Some APIs return tool arguments as a JSON string, others as an object."""
    return json.loads(raw) if isinstance(raw, str) else dict(raw)

def check_deployment(model: ChatModel, needs: set, min_context: int) -> None:
    missing = needs - model.capabilities
    if missing:
        raise RuntimeError(f"model lacks required capabilities: {sorted(missing)}")
    if model.context_window < min_context:
        raise RuntimeError(f"context {model.context_window} < required {min_context}")

# At startup for tenant "acme", whose deployment uses the customer's own model endpoint:
# check_deployment(acme_model, needs={"tools", "json_schema"}, min_context=32_000)
```

In practice, the first run of a customer's regression suite on a newly integrated model usually shows
format failures before reasoning failures: these are fixed in the adapter (argument parsing, finish-reason
mapping) or the per-model prompt, then the suite is re-run until the paired flips against the old model
are acceptable.

## Trade-offs
- A normalising adapter makes models swappable, but a lowest-common-denominator interface hides features
  only one model has; expose such features as optional capabilities rather than dropping them.
- Using a gateway or library saves mapping work, but adds a dependency whose own updates can change
  behaviour; pin its version and keep your contract tests, especially for tool calls and streaming.
- Pinning exact model versions gives stable behaviour, but you must plan migrations before the provider
  retires them; aliases avoid that work only when unannounced behaviour changes are acceptable.
- The customer's mandated model satisfies their data and procurement rules, but may score lower; show the
  measured gap on their suite so the trade-off is their informed decision, not a surprise.

## Pitfalls
- Reusing the old prompt unchanged; the symptom is a sudden rise in format and instruction-following
  failures on the regression suite with no code change.
- Counting tokens with the wrong tokenizer; the symptom is context-length errors or silently truncated
  retrieved documents on long requests.
- Serving an open-weight model with the wrong chat template; the symptom is outputs that look fluent but
  score clearly below the model's expected quality, with no errors logged.
- Configuring a moving alias instead of a pinned version; the symptom is a quality or format change on a
  date when nothing was deployed.
- Not mapping provider errors; the symptom is retries hammering a model on non-retryable errors, or
  giving up immediately on rate limits.

## When to use
Treat every model change as an integration project: a new provider, a customer-mandated deployment, a
fine-tuned adapter, or a version bump of the same model. Scale the effort to the size of the change: a
version bump may need only the regression suite and shadow; a new model family needs the adapter,
capability entry, tokenizer, prompt porting and errors. Measure paired flips on the customer suite,
format-failure rate, latency, cost and error rates. In interviews, listing the non-obvious differences
(templates, tokenizers, tool-call formats, error semantics) shows hands-on experience.

## Role lens
- **Engineer:** Writes the adapter and its contract tests, maps errors and tool-call formats, fixes chat
  templates and token counting, and ports prompts against the regression suite.
- **Architect:** Defines the internal model interface, the capability registry and per-tenant model
  routing, so customer-mandated models plug in without forking the application.
- **FDE:** Gets the customer's endpoint, quotas and allowed models, runs their suite on that model, and
  explains measured quality differences to their stakeholders.

## Check yourself
**Q:** A customer requires you to move from your hosted provider to an open-weight model on their GPUs.
After switching the endpoint, answers look fine in a demo but the regression suite drops sharply. What
do you check, in order?
**A:** First the mechanical causes, because they are cheap and common: that the server applies the
model's own chat template and stop tokens, that tool-call output is parsed correctly, and that context
budgets use the new tokenizer so documents are not being truncated. Then sampling settings and maximum
output. Only after those are clean would I attribute the remaining drop to the model, and then port the
prompt by reading failures, and if needed consider a larger model or a fine-tuned adapter. Throughout I
compare paired flips against the old model on the same suite.

## Quiz
### Q: Why should context budgeting use the target model's own tokenizer?
- [ ] Tokenizers determine the model's sampling temperature
- [x] The same text can produce different token counts across model families, which changes what fits
- [ ] Only the target tokenizer can detect prompt injection
- [ ] Token counts are identical everywhere, but the API requires it
> Budgets sized with another model's tokenizer can overflow the context or truncate retrieved documents.
> Counting with the target tokenizer keeps the safety margin real.

### Q: A self-hosted model produces fluent but noticeably worse answers than expected, and no errors are logged. What is a likely integration cause?
- [ ] The GPU is too slow for the model
- [ ] The new model's context window is larger than the old model's
- [ ] The rate limiter is rejecting half the requests
- [x] Conversations are rendered with the wrong chat template
> The model was trained on a specific conversation format; a mismatched template still yields fluent
> text, so it degrades quality silently. Slow GPUs and rate limits cause latency and errors, not this.

### Q: What is the main reason to configure a pinned model version rather than a moving alias?
- [x] So the model cannot change underneath a validated deployment without a deliberate, tested switch
- [ ] Pinned versions are always cheaper per token
- [ ] Aliases are not allowed in customer contracts
- [ ] Pinned versions have larger context windows
> A moving alias can point at a new model at any time, invalidating the evals you gated on. Pinning makes
> every change an explicit integration step with its own regression run.
