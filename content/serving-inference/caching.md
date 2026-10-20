---
id: caching
module: serving-inference
name: Caching (semantic, prompt)
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - serving-inference/prefix-caching
  - prompting/prompt-caching
  - model-internals/kv-cache
  - serving-inference/ttft
  - system-design/caching-strategy-and-cache-invalidation
  - fastapi-for-ai/redis-caching
  - observability/token-cost-tracking
  - infra-capacity-economics/cost-optimization
sources:
  - Official docs: Anthropic prompt caching
  - Official docs: OpenAI prompt caching
  - Zheng et al., 2024 — SGLang: Efficient Execution of Structured Language Model Programs (RadixAttention)
  - Bang, 2023 — GPTCache: An Open-Source Semantic Cache for LLM Applications
---

## TL;DR
LLM caching comes in three layers that are routinely conflated. An exact-match response cache skips
generation when an identical request repeats. A semantic cache skips generation when a *similar enough*
request repeats, judged by embedding distance. Prompt caching reuses the KV cache of a shared prompt
prefix so prefill is not recomputed, but the answer is still generated fresh. They save different things,
fail in different ways and need different keys, so "add a cache" is not one decision.

## Why it exists
The cost structure forces it. Every call pays for prefill in proportion to prompt length, and in agent and
RAG systems most of the prompt is identical from call to call: the system prompt, tool schemas, few-shot
examples, a long document. A 40-turn agent session with a 12k-token prefix re-processes roughly half a
million tokens of bytes the model has already seen, and at long contexts prefill dominates time to first
token. Separately, many products see repeated questions ("what is your refund policy"), where the whole
generation repeats.

The naive answer, "cache responses like any HTTP response", fails in three ways. Exact hits are rare in
conversational traffic, because prompts contain user-specific and time-specific content, so the hit rate
rounds to zero. When a hit does occur, non-determinism and personalisation mean the cached text may be
wrong for the new context: a cached answer about *this* customer's order is a data leak for the next one.
And response caches do nothing for the dominant cost in agent loops, where every turn is new but the
prefix is identical.

Prompt caching exists for the third problem: it attacks prefill compute without touching the answer.
Semantic caching exists for the first: it trades exactness for hit rate. Each introduces a specific
failure mode, no speedup for one and wrong answers for the other, which is why the layers must be
understood separately before being combined.

## How it works
### Layer 1: exact-match response cache
Key: a hash of everything that affects the output: model id and version, system prompt, messages, tool
definitions, sampling parameters, and any retrieved context. Hit: return the stored response. It is most
valuable in deterministic pipelines (classification, extraction, embedding of the same chunk) where
identical inputs really recur. Because the key includes the prompt template, bump a template version to
invalidate on change. Product semantics decide whether a cached answer is acceptable at temperature above
zero; a "regenerate" action must bypass it.

### Layer 2: semantic cache
Embed the incoming query (and optionally a normalised context), run a nearest-neighbour search over past
queries, and if the best match's similarity exceeds a threshold τ, return its stored answer. GPTCache is a
well-known open-source implementation. The mechanism has four load-bearing parts. The threshold is a
precision/recall knob: "how do I cancel my order" and "how do I cancel my subscription" embed close
together and have different answers, so a threshold tuned only for hit rate ships wrong answers. The scope
must include tenant, corpus version and prompt version, because a similar question in a different
tenant's context must never hit. Invalidation must fire when the underlying knowledge changes, since the
cache does not know the policy page was edited. And measurement must track hit *precision* (the fraction
of hits judged correct) alongside hit rate, because the failure mode is a confident wrong answer. A cheap
verifier call ("does this cached answer answer this question?") can raise precision at some latency cost.
It fits FAQ-like, high-repeat, low-personalisation traffic, and fits chat with history or user-specific
context poorly.

### Layer 3: prompt caching (prefix KV reuse)
The engine keeps the key and value tensors computed for a prompt prefix and, when a new request's token
sequence begins with the same prefix, skips prefill for those tokens and processes only the suffix.
Providers expose this as a billing and latency feature; self-hosted engines expose it as a scheduler
feature (vLLM's automatic prefix caching by block hash, SGLang's RadixAttention tree with LRU eviction).

The rules follow from the mechanism. Matching is on the exact token sequence in order, so the request is
rendered as tools, then system, then messages, and any change at position *i* invalidates everything
after it; static content goes first and volatile content (timestamps, user names, per-request retrieved
documents) goes last. The cache is per model and isolated per organisation or workspace. Entries have a
time to live, and hits are not guaranteed under load, so the only ground truth is the usage report: on
Anthropic, `cache_creation_input_tokens` and `cache_read_input_tokens` alongside the uncached
`input_tokens`; OpenAI reports cached tokens inside its usage details. An entry becomes readable only
once the writing request has begun responding, so N parallel identical requests all pay full price.

Vendor specifics at the time of writing, which change: Anthropic uses explicit `cache_control`
breakpoints (up to four per request) with a default five-minute TTL refreshed on every read and an
optional one-hour TTL; cache writes cost more than ordinary input (1.25× for five minutes, 2× for one
hour) and reads cost a fraction of input (around 0.1×, lower on newer models); the minimum cacheable
prefix is model-dependent, from a few hundred to a few thousand tokens, and shorter prefixes silently do
not cache. OpenAI caches automatically for prompts above a minimum length and discounts cached input
tokens. On both, caching reduces input cost and time to first token; it does nothing for output tokens or
decode speed.

### Which layer for which traffic
FAQ bot: semantic plus exact. Extraction or classification pipeline: exact. Agent loops, multi-turn chat
and RAG with a large static context: prompt caching. Embedding generation: exact. Most real systems run
prompt caching always and add a response-level cache only where repeats are measured.

## Example
Explicit prompt caching on the Anthropic API, with the static manual placed before the breakpoint and the
volatile question after it; the usage fields confirm whether the second call read the prefix. A worked
cost scenario follows the code.

```python
import anthropic

client = anthropic.Anthropic()
MANUAL = open("policy_manual.md").read()          # ~20k tokens, identical on every request

def ask(question: str) -> str:
    resp = client.messages.create(
        model="claude-opus-5-5",
        max_tokens=1024,
        system=[
            {"type": "text", "text": "Answer questions using only the policy manual below."},
            {"type": "text", "text": MANUAL,
             "cache_control": {"type": "ephemeral"}},   # breakpoint: everything up to here is cacheable
        ],
        messages=[{"role": "user", "content": question}],   # volatile content after the breakpoint
    )
    u = resp.usage
    print(f"uncached={u.input_tokens} cache_write={u.cache_creation_input_tokens} "
          f"cache_read={u.cache_read_input_tokens} output={u.output_tokens}")
    return next(b.text for b in resp.content if b.type == "text")

ask("What is the refund window?")      # first call: cache_write ≈ 20k, cache_read = 0
ask("Who can approve an exception?")   # within the TTL: cache_read ≈ 20k, uncached ≈ the question
```

Worked scenario with illustrative prices. Take a 20,000-token manual, a 200-token question, a 400-token
answer, and 1,000 questions a day arriving more often than every five minutes, so the entry stays warm.
Assume 3 per million input tokens, 3.75 per million for cache writes (1.25×), 0.30 per million for cache
reads (0.1×) and 15 per million output tokens. Without caching, daily input cost is
1,000 × 20,200 × 3 / 10⁶ ≈ 60.6. With caching it is one write (20,000 × 3.75 / 10⁶ ≈ 0.08), 1,000 reads
(1,000 × 20,000 × 0.30 / 10⁶ = 6.0) and the uncached questions (1,000 × 200 × 3 / 10⁶ = 0.6), about
6.7, a nine-fold reduction on input. Output cost, 1,000 × 400 × 15 / 10⁶ = 6.0, is unchanged, so the
total falls from about 66.6 to about 12.7. Time to first token drops by roughly the time it took to
prefill 20,000 tokens. The break-even is instructive: at 1.25× write and 0.1× read, a prefix reused even
once within the TTL is already cheaper than not caching (1.35× versus 2×).

## Trade-offs
- An exact cache can never return content that was wrong for its key, but its hit rate on free-form
  conversation is close to zero; it earns its keep in pipelines and embedding jobs, not in chat.
- A semantic cache can reach high hit rates on FAQ traffic, but every hit is a bet that similarity implies
  same answer; it flips to a liability when answers are personalised, when the corpus changes often, or
  when a wrong answer is costly.
- Prompt caching makes reads cheap but writes cost more than plain input, so a prefix used once per TTL
  costs more than no caching; with a 1.25× write and 0.1× read the break-even is a single reuse, but a
  one-hour TTL at a 2× write needs more.
- Prefix ordering constraints fight prompt-engineering habits such as putting the date or the user's name
  at the top; every byte of volatile content moved earlier shortens the cacheable prefix.
- On self-hosted engines a resident prefix cache occupies KV memory that could hold running sequences, so
  cache retention competes with batch size under load.

## Pitfalls
- A timestamp, request id or user name early in the system prompt; the symptom is
  `cache_read_input_tokens` stuck at zero while the bill is unchanged or slightly higher, because every
  request pays the write premium for an entry nothing ever reads.
- A prefix shorter than the model's minimum; the symptom is no cache-creation tokens ever reported despite
  the breakpoint.
- Tool definitions rendered in nondeterministic order, or a tool list that varies per request; the symptom
  is intermittent hits that correlate with which feature flags were on.
- A semantic cache without tenant scoping; the symptom is answers that reference another customer's
  documents, which is a security incident, not a quality bug.
- A similarity threshold tuned for hit rate only; the symptom is support tickets saying the assistant
  answered a different question.
- Caching responses at temperature above zero without a bypass on "regenerate"; the symptom is regenerate
  returning identical text.
- Reporting cache hit rate instead of cost and latency deltas; the symptom is a dashboard at 90% hits
  while the bill barely moves, because the hits were on short prefixes.
- Expecting decode to speed up; the symptom is unchanged tokens per second after enabling caching, which
  is correct, since only prefill is skipped.

## When to use
Turn prompt caching on wherever a prompt has a stable prefix above the model's minimum: agent loops, long
system prompts, document question answering, few-shot classifiers. Add an exact-match cache in
deterministic pipelines and for embeddings. Add a semantic cache only for repeat-heavy,
low-personalisation traffic, with tenant scoping, explicit invalidation and a measured hit precision.
Measure cache-read tokens as a fraction of total input, time to first token before and after, cost per
request, and for semantic caches both hit rate and hit precision. Day to day it shows up as "why did the
bill double after we added the date to the system prompt"; in interviews, the expected depth is
distinguishing the three layers, explaining why prompt caching needs ordered, byte-identical prefixes,
and naming the semantic cache's wrong-answer risk.

## Role lens
- **Engineer:** Orders the prompt static-first, places breakpoints, asserts in integration tests that a
  second identical request reports cache reads, and keys response caches on every input that affects output.
- **Architect:** Decides which layers each product surface gets, sets tenant isolation and retention
  rules for cached content, and models the cost and latency impact against provider pricing.
- **FDE:** Finds the one dynamic line a customer put at the top of their system prompt, restores their
  cache hits, and explains why their semantic cache must be partitioned per business unit.

## Check yourself
**Q:** An agent product makes about 40 model calls per session over a 12k-token system-and-tools prefix
followed by a growing conversation. Design its caching and state what each layer buys and cannot buy.

**A:** Prompt caching is the main lever. Render tools and system prompt first and keep them byte-stable;
put a breakpoint at the end of that 12k prefix so every call reads it instead of prefilling it, and let
the conversation tail be cached incrementally so each turn writes only its own delta. That removes most
input cost and most time to first token, but it does nothing for output tokens or decode speed, and it
needs the session to stay inside the TTL or to be re-warmed. An exact-match response cache buys little
here, since every turn is new, except for deterministic sub-calls such as embeddings or classification
inside tools. A semantic cache is inappropriate for the main loop because answers depend on conversation
state and would be wrong on a near-miss hit. I would verify with the usage fields, assert cache reads in
an integration test, and watch the bill for regressions when someone adds a dynamic line to the system
prompt.

## Quiz
### Q: What does provider-side prompt caching reduce?
- [ ] Output token cost and decode latency
- [x] Input token cost and time to first token for the cached prefix
- [ ] The number of model calls needed per conversation
- [ ] The size of the context window needed for the prompt
> The provider reuses the KV cache for the prefix so prefill is skipped and cached input is billed at a
> discount. Generation of the answer still happens in full, so output cost and decode speed are unchanged.

### Q: Why does changing one word at the start of the system prompt eliminate prompt-cache hits?
- [ ] Because the provider hashes the whole request and any change invalidates the hash
- [x] Because the cache matches the exact token sequence in order, so a change at position i invalidates every cached position after it
- [ ] Because system prompts are never cached, only messages
- [ ] Because changing the prompt resets the five-minute TTL
> KV entries for position i depend on all earlier tokens, so matching is a prefix match on the token
> sequence. An early change means no later position can be reused, which is why volatile content belongs
> at the end.

### Q: What is the primary risk of a semantic cache?
- [ ] It increases time to first token for cache misses
- [ ] It cannot be scoped per tenant
- [x] A query similar in embedding space but different in meaning receives a confident, wrong cached answer
- [ ] It requires the model to support prompt caching
> Embedding similarity is not answer equivalence. "Cancel my order" and "cancel my subscription" sit close
> together and need different answers, so hit precision must be measured and the threshold tuned for
> correctness, not hit rate.
