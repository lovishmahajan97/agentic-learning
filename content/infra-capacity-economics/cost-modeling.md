---
id: cost-modeling
module: infra-capacity-economics
name: Cost modeling (cost per request, token economics)
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - infra-capacity-economics/unit-economics-per-successful-task
  - infra-capacity-economics/cost-optimization
  - infra-capacity-economics/break-even-analysis
  - observability/token-cost-tracking
  - prompting/prompt-caching
  - model-internals/tokenization
  - agent-reliability/max-step-budgets
  - infra-capacity-economics/tco
sources:
  - Official docs: provider pricing pages (OpenAI, Anthropic, Google)
---

## TL;DR
A cost model turns a request's shape into dollars: tokens in (fresh and cached), tokens out (including
hidden reasoning tokens), times their prices, plus the non-model calls around them. Token economics
explains the shape of those prices: output tokens cost more because decode is sequential, and in agents
and chats the whole history is re-sent every turn, so cost grows roughly quadratically with turns. You
model the distribution per request, not the average, because the tail sets the bill.

## Why it exists
LLM spend is metered per token, so cost is set by the shape of each request, not by servers running.
Two features that look alike on a product roadmap can differ in cost by a hundred times: a one-shot
classification with a 300-token prompt versus a 20-turn agent that pastes tool results into its context
each step. Teams that estimate cost as "requests per day × a typical price" are routinely surprised,
because they miss three things.

First, input and output are priced differently, and models that reason emit billed tokens the user never
sees. Second, conversation and agent loops re-send everything each turn, so a long session is not N
times a short one but closer to N². Third, the cost distribution is heavy-tailed: a small share of long
documents, runaway agent loops or retries can dominate the invoice. A cost model makes these visible
*before* launch, which is when you can still change the design (cap turns, cache prefixes, route to a
smaller model) instead of explaining an invoice. It is also the input to break-even analysis, pricing
and unit economics: none of those are possible without a defensible cost per request.

## How it works
### The per-call formula
```
cost = fresh_input_tokens × p_in
     + cached_input_tokens × p_cached
     + output_tokens × p_out            # includes reasoning/thinking tokens where billed
     + non-model costs (embeddings, rerank, vector search, tool APIs, guardrail models)
```
Some providers also charge a premium to *write* a prompt-cache entry, a discount for asynchronous batch
processing, or different rates above a context-length threshold. Pricing structures are vendor- and
model-specific and change often, so a cost model should read prices from configuration, never hard-code
them.

### Why output costs more
Prefill processes the whole prompt in one parallel pass, which uses GPU compute efficiently. Decode
produces output one token at a time, and each step reads the full weights and KV cache for very little
arithmetic, so a GPU produces far fewer output tokens per second than it ingests input tokens. Providers
price that: output is commonly several times the input price. The design consequence is that verbosity
is expensive, and a reasoning model that "thinks" for thousands of tokens can cost more than a larger
model that answers directly.

### Tokens are not words, and not portable
Token counts depend on the tokenizer. The same text yields different counts on different model
families, and code, non-English text and JSON tend to tokenize less efficiently than English prose. A
per-word estimate built on one model is wrong on another; measure with the target model's tokenizer.

### Multi-turn growth
In a chat or agent loop, turn t sends the system prompt, tool schemas and every earlier message. If each
turn adds d tokens, the total input over T turns is about T·prefix + d·T²/2. The quadratic term dominates
long sessions. Two levers attack it: prompt (prefix) caching, which bills repeated prefixes at a
discount, and context compaction, which shrinks the history itself.

### Self-hosted token cost
For your own GPUs, the "price" is derived: cost per token = (GPU $/hour) / (tokens produced per hour at
the utilization you actually achieve). The utilization term is decisive. A fleet that is busy a third
of the time pays three times as much per token as the same fleet kept full.

### Model the distribution
Build the model from logged traces: for each feature, the distribution of input, cached and output
tokens and of turns per session. Report p50, p95 and p99 cost per request and the share of spend from the
top 1% of requests. The mean alone hides the runaway sessions that budgets and step caps must bound.

## Example
Illustrative prices: $3 per million input tokens, $15 per million output, $0.30 per million cached input
(cache-write premiums ignored). Substitute your provider's current sheet.

```python
P_IN, P_OUT, P_CACHED = 3.00, 15.00, 0.30          # $ per million tokens (illustrative)

def call_cost(fresh_in, out, cached_in=0):
    return (fresh_in * P_IN + cached_in * P_CACHED + out * P_OUT) / 1e6

def agent_run(turns, prefix=4_000, delta=600, out=200, cache=False):
    """Each turn re-sends the history: prefix + all earlier tool results and outputs."""
    total, history = 0.0, prefix
    for t in range(turns):
        reused = history - delta - out if (cache and t > 0) else 0   # last turn's prompt
        total += call_cost(history - reused, out, reused)
        history += delta + out
    return total

print(f"single RAG call: ${call_cost(6_000, 400):.4f}")              # $0.0240
for turns in (4, 12, 24):
    print(turns, round(agent_run(turns), 3), round(agent_run(turns, cache=True), 3))
# 4 turns: $0.074 vs $0.036 cached | 12: $0.338 vs $0.101 | 24: $1.022 vs $0.228

gpu_hr, tok_per_s = 4.00, 1_500                     # illustrative $/GPU-hour, aggregate output tok/s
for util in (0.9, 0.3):
    print(util, round(gpu_hr / (tok_per_s * 3600 * util) * 1e6, 2), "$/M output tokens")
# 0.9 -> $0.82/M ; 0.3 -> $2.47/M
```

Going from 4 to 24 turns is 6× the turns but about 14× the cost without caching; prefix caching cuts the
24-turn run by more than 4×. The self-hosted number shows why utilization, not GPU list price, decides
whether owning hardware is cheap.

## Trade-offs
- A detailed per-feature model catches design choices that will be expensive, but costs time to build
  and maintain; for a prototype a spreadsheet of token counts per call is enough until traffic is real.
- Prompt caching cuts repeated-prefix cost sharply, but only pays when prefixes are byte-identical and
  reused within the cache lifetime; it flips to a small loss where writes carry a premium and reuse is rare.
- Reasoning models raise output tokens per task, but can lower total cost when they replace several
  retries or a multi-step chain; measure cost per *successful* task, not per call.
- Self-hosting lowers marginal token cost at high utilization, but turns cost fixed; it flips to more
  expensive than an API when traffic is low or spiky.

## Pitfalls
- Estimating with average tokens per request; the symptom is an invoice several times the forecast,
  driven by a long tail of large documents or long agent sessions.
- Forgetting hidden reasoning tokens or tool-schema tokens in the prompt; visible as billed input or
  output counts far above what the visible text suggests.
- Reusing a token-per-word ratio from one model family on another; the forecast drifts after a model
  switch even though the traffic did not change.
- Ignoring non-model costs such as embeddings, reranking, guardrail models and trace storage; total
  spend per request comes out well above the LLM line item.

## When to use
Build a cost model before committing to any feature design that calls a model in a loop, ingests
user-supplied documents, or will run at volume; rebuild it from real traces after launch. Use it to set
max-turn and max-token limits, to decide where prompt caching and smaller models pay off, and as the
input to pricing and break-even decisions. Measure tokens by type (fresh, cached, output, reasoning) per
feature and per request, and watch the p95 and p99, not only the mean. In interviews, deriving the
quadratic growth of agent cost and the utilization term in self-hosted cost shows real understanding.

## Role lens
- **Engineer:** Logs token counts by type for every call, tags them with feature and request IDs, and
  writes the cost function that dashboards and budget guards share.
- **Architect:** Uses the model to choose between designs, set turn and context limits, and decide
  where caching, smaller models or self-hosting change the economics.
- **FDE:** Builds a cost estimate from the customer's real document sizes and usage volumes, so the
  commercial proposal survives contact with production traffic.

## Check yourself
**Q:** A support agent averages 8 turns, but finance's forecast assumed cost = 8 × a single-call cost.
The invoice is 4× the forecast. Explain, and say what you would change.
**A:** Each turn re-sends the system prompt, tool schemas and full history, so input tokens grow with
the turn number and total input grows roughly with the square of turns; multiplying a single call by 8
undercounts badly. The tail also matters: a few 30-turn sessions dominate. I would rebuild the model from
traces with token distributions per turn, enable prompt caching for the stable prefix, compact or
summarize history past a threshold, cap turns and tokens per session, and route simple tickets to a
smaller model, then track p95 cost per resolved ticket rather than per call.

## Quiz
### Q: Why do hosted providers typically price output tokens higher than input tokens?
- [ ] Output tokens are larger than input tokens in bytes
- [x] Output is generated sequentially in decode steps that use the GPU far less efficiently than the parallel prefill of input
- [ ] Output tokens must be checked by a safety model that input tokens skip
- [ ] Input tokens are cached for free on every request
> Prefill processes the prompt in one parallel, compute-efficient pass; decode yields one token per
> step and is memory-bandwidth bound, so each output token consumes more GPU time.

### Q: An agent's turns each add about the same number of tokens to history. How does total input cost scale with the number of turns, without caching or compaction?
- [ ] Linearly, since each turn adds the same number of tokens
- [x] Roughly quadratically, because each turn re-sends all earlier turns
- [ ] Logarithmically, because providers discount long prompts
- [ ] It stays constant after the first turn
> Turn t's prompt contains all t earlier increments, so total input is a sum of an arithmetic series,
> proportional to T². Each turn adding a constant amount is exactly what makes the total quadratic.

### Q: A self-hosted GPU costs the same per hour regardless of load. What most changes its cost per token?
- [ ] The model's parameter count only
- [ ] The provider's published per-token price
- [x] The utilization actually achieved, since cost per token is hourly cost divided by tokens produced per hour
- [ ] Whether the prompt uses JSON output
> Fixed hourly cost spread over fewer tokens raises the per-token price; a fleet at 30% utilization pays
> three times more per token than the same fleet at 90%.
