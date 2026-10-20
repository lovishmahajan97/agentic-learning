---
id: cost-optimization
module: infra-capacity-economics
name: Cost optimization
kind: practice
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - infra-capacity-economics/cost-modeling
  - infra-capacity-economics/quality-latency-cost-optimization
  - infra-capacity-economics/unit-economics-per-successful-task
  - system-design/model-routing-cascading
  - prompting/prompt-caching
  - serving-inference/caching
  - evals/regression-tests
  - infra-capacity-economics/finops
sources:
  - Chen et al., 2023 — FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance
---

## TL;DR
Cost optimization is reducing spend per unit of useful work without breaking quality or latency, and
it works best as an ordered search: measure where money goes, then avoid calls entirely, send fewer
tokens, use a cheaper model for the easy share, buy capacity more cheaply, and only then tune serving.
Every change is gated by an eval, because the easiest way to cut cost is to quietly make the product
worse.

## Why it exists
AI features have a cost structure unlike most software: every request has a marginal cost, often cents,
that scales directly with usage and with design choices such as context size and model tier. A feature
that is cheap in a pilot can become the largest line on the cloud bill at production volume, and
gross margin on an AI product can be negative if no one looks.

The naive responses both fail. "Switch to the cheapest model" saves money per call but often lowers task
success, which pushes work into retries, escalations and human review, so cost per *successful* task can
rise. "Optimize the GPU kernels" or "negotiate a discount" works on the smallest lever first: a 20%
price cut is dwarfed by not sending 60% of the tokens in the first place. Cost work also fails when it
is done blind: without attribution by feature and request type, teams optimize the endpoint they happen
to be looking at, not the one that dominates spend. The discipline exists to apply effort where the
money actually is, in the order that yields the most per unit of risk, with quality held constant by
measurement rather than by hope.

## How it works
### 0. Measure and attribute first
Tag every model call with feature, tenant, model and request type, and record tokens by type (fresh
input, cached input, output, reasoning). Rank spend by feature and look at the cost distribution: often a
few features and a long tail of large requests dominate. Pair cost with an outcome metric (task success,
acceptance rate) so every later change can be judged on cost per successful task.

### 1. Do not call the model
Cheapest token is the one never sent. Exact-match response caches for repeated queries (keyed on
normalized input plus the version of every input that affects the answer), deterministic code paths for
cases rules handle reliably, and skipping steps that do not change the outcome. Semantic caches (reuse
an answer for a *similar* question) save more but can return a wrong answer for a subtly different
question, so they need a strict threshold and evaluation.

### 2. Send fewer tokens
Input usually dominates RAG and agent cost. Retrieve fewer, better chunks (reranking lets you cut top-k),
trim system prompts and tool schemas, compact long histories, and stop pasting entire tool outputs into
context. Turn on prompt caching for stable prefixes: put fixed instructions and schemas first and
variable content last *because* caching only matches identical prefixes. Cap output with concise
formats and `max_tokens`, since output tokens cost more per token.

### 3. Use a cheaper model where it is good enough
Route easy requests to a smaller model and escalate hard or low-confidence ones (routing or cascading).
Distill or fine-tune a small model for a narrow, high-volume task. The gain is large because price per
token differs by one to two orders of magnitude across model tiers, but routing errors cost quality, so
measure success per route.

### 4. Buy capacity more cheaply
Use asynchronous batch APIs for work that can wait (many providers discount them), committed-use or
reserved capacity for steady baseline load, spot or preemptible instances for interruptible batch jobs,
and self-hosting only where volume keeps GPUs highly utilized.

### 5. Tune serving
For self-hosted models: raise utilization with continuous batching and right-sized concurrency, quantize
weights or the KV cache where evals allow, enable prefix caching, right-size GPUs, and scale down idle
replicas. These matter most once the fleet is large.

### Guardrails on the process
Each change ships behind an eval and, ideally, an A/B or shadow comparison; the acceptance criterion is
cost per successful task at equal or better quality and within the latency budget.

## Example
A document-QA feature (all figures illustrative): 1M requests per month, 8,000 input tokens per request
(1,500 fixed instructions plus 6,500 retrieved context), 500 output tokens, on a large model at $3 per
million input and $15 per million output. Cost per request is $0.024 + $0.0075 = $0.0315, or $31,500 a
month. Apply levers in order, each confirmed by the eval suite:

1. **Exact-match cache** on normalized question plus document version: measured 10% hit rate. Billable
   requests fall to 900k: $28,350.
2. **Prompt caching** of the 1,500-token fixed prefix, read at $0.30 per million (a 90% discount; the
   discount and any cache-write premium vary by provider and are ignored here): saves 1,500 × $2.70/M ≈
   $0.004 per request, giving $0.02745 per request and $24,705 a month.
3. **Rerank and cut context** from 6,500 to 3,000 tokens (eval shows no drop): saves $0.0105 per
   request, giving $0.01695 and $15,255.
4. **Route 60% of requests** (simple lookups) to a small model at $0.25/$1.25 per million, about
   $0.00175 per request, escalating 10% of those to the large model: blended $0.0088 per request,
   about $7,960 a month.

Total reduction is roughly 75%, and none of it came from GPU tuning or price negotiation. Step 4 is the
riskiest: if escalation is triggered by low confidence that misses wrong answers, success drops and the
saving is illusory, so the success rate per route is the number to watch.

## Trade-offs
- Smaller models and routing cut cost sharply, but add a routing error rate; the trade flips when
  failures are expensive (refunds, compliance) and cost per successful task rises.
- Aggressive context trimming saves input tokens and latency, but can drop the one chunk the answer
  needed; it stops paying when retrieval recall is already marginal.
- Batch APIs and spot capacity are cheaper, but add delay or interruption; they suit offline work and
  are wrong for anything on an interactive path.
- Semantic caching avoids calls for similar queries, but risks serving a wrong cached answer; it fits
  stable FAQ-like traffic and fails where small wording changes alter meaning.

## Pitfalls
- Optimizing without attribution; the symptom is a successful saving on one endpoint while total spend
  keeps rising because another feature dominated all along.
- Measuring cost per call instead of per successful task; visible as falling per-call cost alongside
  rising retries, escalations or human-review volume.
- Breaking prompt caching by putting timestamps, user names or request IDs at the start of the prompt;
  the cached-token share drops to near zero after a prompt change.
- Shipping a cheaper configuration without an eval gate; quality complaints or lower acceptance rates
  appear weeks later and are hard to trace to the change.

## When to use
Start cost work once a feature has real traffic and its spend is material, or earlier if the cost model
shows negative unit economics at target volume. Begin with attribution, then work down the lever list,
estimating savings from traces before building anything. Measure cost per successful task, cached-token
share, tokens per request by type, routing mix and quality per route. Avoid premature optimization in
prototypes, where learning what users need is worth more than a cheaper token. In interviews, ordering
the levers and insisting on an eval gate is what separates practitioners from people who only say
"use a smaller model".

## Role lens
- **Engineer:** Implements caches, prompt-prefix ordering, context trimming and routing, and wires
  per-call cost and success metrics so each change can be evaluated.
- **Architect:** Sets the cost target per unit of work, decides which levers the platform provides
  centrally (gateway caching, routing, batch lanes), and owns the quality bar changes must meet.
- **FDE:** Finds customer-specific waste, such as oversized documents or redundant calls in their
  integration, and proves savings against the customer's own acceptance metrics.

## Check yourself
**Q:** Finance asks you to cut a RAG assistant's model spend by half within a month without hurting
quality. How do you approach it?
**A:** First attribute spend: tokens by type per feature and request class, and the cost distribution,
so I know whether input context, output length, a few heavy tenants or an agent loop dominates. Then
estimate each lever from traces: exact caching for repeats, prompt caching with a stable prefix,
reranking to cut retrieved context, output caps, and routing easy queries to a smaller model. I would
ship them in order of savings per unit of risk, each behind the eval suite and a shadow or A/B check,
tracking cost per successful answer, not per call. Price negotiation and GPU tuning come last.

## Quiz
### Q: Which metric best guards against a cost optimization that secretly degrades the product?
- [ ] Cost per API call
- [ ] Total monthly token count
- [x] Cost per successful task, measured alongside a quality eval
- [ ] Average output length per response
> Per-call cost falls when a cheaper model fails more often, but retries, escalations and human review
> then push cost per success up. Tying cost to successful outcomes exposes that trade.

### Q: A prompt change moved the current date and user name to the top of the system prompt. What cost effect is most likely?
- [ ] Output tokens increase because the model writes longer answers
- [x] Prompt-cache hits collapse, because the prefix now differs across users and days
- [ ] Nothing, since the total prompt length is unchanged
- [ ] Batch API discounts no longer apply
> Prompt caching matches identical prefixes. Putting variable content first makes the prefix differ
> per user and per day, so the instructions after it are billed at the full input price again; total
> length being unchanged is irrelevant.

### Q: In what order should levers generally be tried for an API-based RAG feature?
- [ ] Negotiate price, tune GPU kernels, then trim prompts
- [ ] Switch everything to the smallest model, then fix quality later
- [x] Attribute spend, avoid calls, cut tokens, route to cheaper models, then buy capacity more cheaply
- [ ] Add semantic caching first because it removes the most calls
> Attribution shows where the money is; avoiding calls and tokens is low-risk and often large; routing
> has higher quality risk; cheaper capacity (batch lanes, commitments) only applies to deferrable or
> steady load, so it comes after removing work. Semantic caching is not first because it carries a
> wrong-answer risk and its hit rate is unknown until measured.
