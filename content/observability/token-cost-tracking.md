---
id: token-cost-tracking
module: observability
name: Token / cost tracking
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - observability/monitoring
  - observability/spans
  - infra-capacity-economics/cost-modeling
  - infra-capacity-economics/cost-allocation-by-tenant-and-feature
  - prompting/prompt-caching
  - python-for-ai/tiktoken
  - model-internals/tokenization
  - agent-reliability/max-step-budgets
sources:
  - Official docs: Anthropic API
  - Official docs: OpenAI API
---

## TL;DR
LLM spend is metered per token, priced differently for input, output and cached tokens, and varies
by model, so the only way to know what a feature, tenant or agent run costs is to record the
provider's reported usage on every call and multiply by the price in force at that moment. Track it
at span level, roll it up by the dimensions you make decisions on, and alert on budgets; retries,
agent loops and growing conversation history make cost non-linear in requests, so per-request
averages mislead.

## Why it exists
The invoice arrives monthly as one number per model. It cannot tell you that the summarisation
feature costs three times the chat feature, that one tenant's agent workflow accounts for half of it,
that a prompt change last Tuesday added 40% to input tokens, or that retries on an overloaded provider
doubled the bill for a day. Every one of those is a decision you need to make during the month, and
each requires attribution that only your own instrumentation can provide.

The naive approach, "count requests and multiply by an average cost", fails because cost per request
is not stable. Input and output tokens are priced differently (output is typically several times more
expensive per token), and the ratio depends on the feature. Agent runs make a variable number of
calls, with a long tail where one run loops for hundreds of steps. Conversations resend their history
each turn, so the cost of turn *n* grows with *n*. Retrieval stuffs context. Retries and fallbacks are
extra calls that never appear as extra user requests. Prompt caching changes the price of the same
tokens depending on whether the prefix was recently seen.

There is also a control problem: you want to stop a runaway agent or a misbehaving tenant before the
money is spent, which requires knowing the cost of a run while it is still running. That means usage
has to be recorded per call, attributed in real time and compared against budgets, not reconciled
from an invoice weeks later.

## How it works
### Where the numbers come from
The authoritative token counts are in the provider's response: a `usage` object with input and output
token counts and, on most providers, further fields for cached input tokens and, for reasoning
models, reasoning tokens. Use these, not your own estimates, *because* tokenisers differ between model
families and because the provider's count is what they bill. Two details trip people up. With
streaming, usage arrives at the end of the stream or split across the first and last events, and some
APIs include it only if the request asks for it. And providers differ on whether the headline input
count includes cached tokens or reports them separately, so the formula for billable input must be
written per provider after reading its documentation.

### Estimating before the call
Sometimes you need a number before spending: to enforce a per-request budget, to decide whether a
context fits, or to route long inputs to a cheaper model. A provider-specific tokeniser (such as
`tiktoken` for OpenAI models) or a provider's token-counting endpoint gives that estimate. Treat it
as an estimate: it is used for gating, while the response usage is used for accounting.

### Turning tokens into money
Keep a versioned price table: for each model, prices per million tokens for input, output, cache
write, cache read and any other class, each with an effective-from date, *because* prices change and
models are deprecated, and recomputing last quarter's cost with this quarter's prices is simply wrong.
Compute cost at ingest time and store the tokens, the computed cost and the price version used. Batch
or discounted tiers are separate rows in the table, not a multiplier applied later.

### Attribution dimensions
Every usage record carries the dimensions you will later group by: model and provider, feature or
route, prompt version, tenant, user (as an opaque id), conversation or session id, agent run id and
step number, and whether the call was a retry or a fallback. Attach them as attributes on the
generation span, and emit the same values into metrics with bounded labels only (model, route, tenant
where the tenant count is small); the unbounded ones (user, conversation, run) live in the per-call
records and the trace.

### Roll-ups that match decisions
- **Per request** answers "is this feature priced sensibly".
- **Per conversation** sums the turns and exposes the history-growth problem.
- **Per agent run** sums every step, retry and tool-related call; its distribution, not its mean, is
  what matters.
- **Per tenant per day** feeds chargeback, quotas and anomaly alerts.
- **Per successful task** divides total cost by completed tasks, which is the number product and
  finance actually want and which charges retries and abandoned runs correctly.

### Why conversations are expensive: a worked example
Suppose a system prompt plus tool definitions is 1,500 tokens and each turn adds about 300 user
tokens and 400 assistant tokens. Turn *n* sends the whole history, so its input is roughly
1,500 + 700 × (n − 1). Over a 20-turn conversation total input is 20 × 1,500 + 700 × (0 + 1 + … + 19)
= 30,000 + 133,000 = 163,000 tokens, against only 8,000 output tokens. Input dominates by a factor of
twenty even though output is priced higher per token, and the last turn alone sends about ten times
the input tokens of the first. This arithmetic is why prompt caching (the repeated prefix is billed at a discount) and
history compaction exist, and why cost per conversation must be tracked separately from cost per turn.

### Budgets and enforcement
With per-call records flowing, budgets become comparisons: soft alerts when a tenant or feature
reaches a fraction of its daily budget, hard stops enforced at the gateway or in the agent loop when a
run exceeds its token or dollar budget. The hard stop needs the running total synchronously, so keep
it in the agent's state or a fast store rather than in the analytics warehouse.

## Example
A provider-neutral usage record costed against a dated price table. The prices are illustrative
placeholders, not any vendor's list; load real ones from configuration. The key design choice is the
neutral `usage` dict: each provider's SDK fields are mapped into it in one adapter, with
`uncached_input` excluding cache reads and writes, so the formula never double counts cached tokens
whatever a provider's headline input number includes. The two printed costs show the same 8,400-token
prompt with and without a warm cache.

```python
from dataclasses import dataclass
from datetime import date

@dataclass(frozen=True)
class Price:        # USD per million tokens; illustrative placeholders, load from config
    input: float; output: float; cache_write: float; cache_read: float; effective: date

PRICES = {          # model -> prices, newest first
    "example-model": [Price(2.0, 10.0, 2.5, 0.2, date(2025, 1, 1))],
}

def price_for(model, on):
    return next(p for p in PRICES[model] if p.effective <= on)

def cost_usd(model, usage, on=None):
    p = price_for(model, on or date.today())
    return (usage["uncached_input"] * p.input
            + usage["cache_write"] * p.cache_write
            + usage["cache_read"] * p.cache_read
            + usage["output"] * p.output) / 1_000_000

def record(span, model, usage, **dims):
    """Attach tokens, cost and attribution dimensions to the generation span."""
    c = cost_usd(model, usage)
    total_in = usage["uncached_input"] + usage["cache_read"] + usage["cache_write"]
    span.set_attribute("gen_ai.usage.input_tokens", total_in)
    span.set_attribute("gen_ai.usage.output_tokens", usage["output"])
    span.set_attribute("app.cost_usd", round(c, 6))
    for k, v in dims.items():          # feature, prompt_version, tenant, run_id, is_retry
        span.set_attribute(f"app.{k}", v)
    return c

warm = {"uncached_input": 400, "cache_write": 0, "cache_read": 8000, "output": 300}
cold = {"uncached_input": 8400, "cache_write": 0, "cache_read": 0, "output": 300}
print(f"warm cache: {cost_usd('example-model', warm):.4f} USD")   # 0.0008+0.0016+0.0030 = 0.0054
print(f"cold cache: {cost_usd('example-model', cold):.4f} USD")   # 0.0168+0.0030        = 0.0198
```

## Trade-offs
- Provider-reported usage is exact and is what you are billed for, but it is only available after
  the call; tokeniser estimates are available before the call and are approximate, so use estimates
  for gating and provider usage for accounting.
- Storing one record per call gives full attribution and a replayable cost history, but at agent scale
  that is many rows per user action; it flips to pre-aggregated roll-ups plus sampled raw records once
  the per-call table outgrows the analytics budget.
- Hard budget caps protect spend and stop runaway loops, but they fail a user mid-task; soft alerts
  preserve the experience and risk the bill, so most teams cap per run and alert per tenant.
- Switching to a cheaper model cuts cost per token immediately, but it can raise tokens per task (more
  retries, longer outputs, more agent steps); cost per successful task is the only comparison that
  settles it.
- Prompt caching lowers input cost sharply for stable prefixes, but on some providers it adds a write
  premium, and it only pays off when the prefix is reused within the cache window; tracking cache-read against cache-write
  tokens tells you whether it is paying.

## Pitfalls
- Estimating cost with one provider's tokeniser for another provider's model; the symptom is a
  persistent gap between your dashboard and the invoice that grows with volume.
- Forgetting to request usage on streaming calls, or reading it from the wrong event; the symptom is
  zero-token records for exactly the streaming routes.
- Counting only the successful final call and not retries, fallbacks or judge calls; the symptom is a
  dashboard that undercounts by the retry rate and never matches the invoice on bad-provider days.
- Recomputing historical cost with current prices after a price change; the symptom is last month's
  cost "changing" in reports.
- Reporting average cost per request for agent features; the symptom is a budget blown by a handful of
  runs that looped, invisible in the mean.
- Attributing tokens to a model but not to a feature or prompt version; the symptom is knowing the
  total went up and being unable to say which deploy did it.

## When to use
Instrument token usage from the first call you ship; the per-call record is cheap and impossible to
backfill. Attribution by feature and tenant becomes necessary the moment there is more than one of
either, and per-run budgets become necessary the moment an agent loop exists. Measure the gap between
your computed cost and the provider invoice each month (a small percentage is normal; more means
missing calls), cost per successful task per feature, and the p99 of cost per agent run. Day to day
this is the first dashboard you check after a prompt, model or retrieval change. In interviews, expect
to be asked how you would attribute an LLM bill to tenants, how caching changes the formula, and why
conversation cost grows faster than turn count; the worked example above is the reasoning expected.

## Role lens
- **Engineer:** Maps each provider's usage fields into one schema, records tokens and computed cost on
  the generation span, handles the streaming usage event, and enforces per-run budgets in the agent
  loop.
- **Architect:** Owns the price table and its versioning, the attribution dimensions, the chargeback
  model for tenants and features, and the policy for hard caps versus alerts.
- **FDE:** Reconciles the customer's invoice against the system's own numbers, explains why one
  workflow costs what it does, and tunes caching and compaction where the per-conversation roll-up
  shows history growth dominating.

## Check yourself
**Q:** Finance says the LLM bill rose 60% month over month while request volume rose 10%. Walk through
how you would find the cause using your tracking, and what you would need to have recorded for that
to be possible.
**A:** Start with the roll-ups by dimension, comparing the two months: by model (did traffic shift to
a more expensive model, or did a price change land), by feature and prompt version (did a prompt
deploy raise input tokens per request), by call type (did retries, fallbacks or judge calls increase
on days the provider was degraded), and by tenant (did one tenant's agent workflow start looping).
Then split tokens into input, output, cache read and cache write: a jump in input with flat output
usually means longer context, longer conversations or a lost cache hit rate; a jump in cache writes
with few reads means caching is being paid for but not used. For this to be answerable, every call
must have been recorded with provider usage split by token class, model, feature, prompt version,
tenant, run id and retry flag, costed with the price in force that day and stored with the price
version. Without the prompt-version and retry dimensions the question is unanswerable, which is the
argument for recording them from the start.

## Quiz
### Q: Why should cost be computed and stored at ingest time rather than recomputed from token counts when a report is run?
- [ ] Token counts are too large to store
- [x] Prices change and models are deprecated, so the stored cost must reflect the price in force when the call happened
- [ ] Recomputing is too slow for dashboards
- [ ] Providers do not report token counts after the call
> A price table with effective dates is the mechanism, and storing the computed cost with its price
> version means historical reports stay stable when prices move instead of silently rewriting.

### Q: A 20-turn conversation has a 1,500-token system prompt and adds about 700 tokens per turn. Which statement about its cost is correct?
- [ ] Each turn costs about the same, so cost scales linearly with turns
- [x] Later turns resend the whole history, so total input tokens grow roughly quadratically with turn count and dwarf output tokens
- [ ] Output tokens dominate because they are priced higher
- [ ] Prompt caching makes the history free after the first turn
> Turn n sends roughly 1,500 + 700(n−1) input tokens; summed over 20 turns that is about 163,000
> input tokens against 8,000 output. Caching discounts the repeated prefix but does not make it free,
> and only while the prefix is reused within the cache window.

### Q: Why is "cost per successful task" a better comparison than "cost per request" when evaluating a cheaper model for an agent feature?
- [ ] Requests cannot be counted for agents
- [ ] Cheaper models always use fewer tokens per request
- [x] A cheaper model may need more steps, retries or re-runs to finish a task, which cost per request hides and cost per successful task includes
- [ ] Cost per request ignores output tokens
> Agent cost is the sum over a variable number of calls, and abandoned or retried runs are real spend.
> Dividing total cost by completed tasks charges those failures to the model that caused them.
