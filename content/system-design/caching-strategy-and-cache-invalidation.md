---
id: caching-strategy-and-cache-invalidation
module: system-design
name: Caching strategy & cache invalidation
kind: pattern
difficulty: core
est_minutes: 16
status: reviewed
related:
  - serving-inference/caching
  - prompting/prompt-caching
  - serving-inference/prefix-caching
  - model-internals/kv-cache
  - fastapi-for-ai/redis-caching
  - rag-retrieval/freshness-document-deletion-index-consistency
  - system-design/multi-tenancy
  - system-design/ai-gateway
sources:
  - Official docs: Anthropic prompt caching
  - Official docs: vLLM
  - Official docs: GPTCache
---

## TL;DR
Model calls are the slowest, most expensive step in an AI system, and much of what they process repeats:
the same system prompt, documents and questions. Caching happens at several layers, prefix caching of
computation, exact and semantic response caches, embedding and retrieval caches, each with its own key
and its own way of going stale. The design question is always the key: what must be identical for a
cached result to still be correct, and how you find out when it no longer is.

## Why it exists
A cache hit returns in milliseconds and costs almost nothing; a miss takes seconds and bills every token.
Prompts are dominated by content that repeats across requests: a system prompt, tool definitions,
few-shot examples and retrieved documents can be thousands of tokens that are identical for every user
of a feature. That makes caching the single biggest lever on cost and latency after model choice.

The naive approach, "hash the prompt and cache the response", fails in ways specific to AI systems.
Outputs are sampled, so caching only makes sense when you want the same answer and the key must include
the sampling parameters. Prompts embed context, so an identical question from two tenants, or from the
same tenant a week apart, can require different correct answers. Semantic near-duplicates look like hits
and differ in one decisive detail: a negation, a date, an account number. And the things that make a
cached answer wrong are numerous and silent: the provider updates the model, someone edits the prompt
template, a document is re-ingested, the embedding model changes, an external tool's data moves.

Without a deliberate strategy teams land in one of two bad places: caching nothing and paying for every
repeated token, or caching eagerly and serving stale or wrong answers that cost more trust than the
tokens saved. Both of the famous hard problems, naming things and invalidating caches, turn out to be the
same problem here: the name of the cache entry is the list of everything the answer depends on.

## How it works
### Layer 1: prefix and prompt caching (caching computation)
A provider or a self-hosted engine reuses the KV cache for a byte-identical token prefix across requests,
so the prefill of the shared part is not recomputed. The key is the exact prefix; any change *before or
within* the cached span invalidates everything after it, because each cached key and value depends on all
earlier positions. Design consequence: put static content first (system prompt, tools, few-shot, long
documents) and dynamic content last, keep the ordering stable, and never inject a timestamp or the user's
name near the top. Providers bill cached input tokens at a lower rate with short time-to-live windows,
some charge a premium for the cache *write*, and most enforce a minimum cacheable prefix length of
roughly a thousand tokens or more, so a short system prompt alone may never cache (all figures are
vendor- and model-specific and change); engines such as vLLM offer automatic prefix caching across
requests that share a prefix. The metric is cached-token share in usage reports, checked after every
change to prompt assembly, because a regression there is silent: requests keep succeeding and only the
bill moves.

### Layer 2: exact response cache
Key = hash of model id and version, sampling parameters, the full normalised message list, tool schemas,
response format, and the tenant or user scope whenever the prompt contains private context. Value =
the response plus its usage. Only sensible for deterministic intent (temperature 0, or a product that
accepts identical answers to identical requests). Hit rates in open-ended chat are low; in form-like,
classification and extraction features, where identical inputs recur, they are high.

### Layer 3: semantic cache
Embed the query, search cached queries within the same scope (tenant, prompt version, index version),
and return the cached answer when similarity exceeds a threshold. The gain is a hit rate exact matching
cannot reach; the risk is a paraphrase that is actually a different question. Mitigations: a high
threshold; scope keys; excluding or entity-keying queries that contain numbers, dates, names or
negations; a cheap verifier on borderline hits ("does this cached answer address this query?"); and a
sampled false-hit audit. The economics: savings = hit_rate × (cost_miss − cost_hit) − false_hit_rate ×
cost_of_a_wrong_answer − embedding cost per lookup. The last two terms are what people forget.

### Layer 4: embedding cache
Key = hash of embedding model id plus normalised text. Cheap and high-value in ingestion (unchanged
chunks are not re-embedded on re-index) and for repeated queries. A new embedding model invalidates
everything, which is fine *because* you must re-embed the whole index anyway; version the index.

### Layer 5: retrieval and tool caches
Query → document ids, keyed by index version; external tool results with a TTL set by data volatility
(exchange rates seconds, a product catalogue hours); memoised agent steps keyed by tool name and
arguments.

### Read and write patterns
Most AI response caches are **cache-aside**: the application looks up the key, calls the model on a
miss and writes the result. That keeps the cache optional (a cache outage means slower, not broken), at
the price of the stampede problem handled below. A gateway cache is effectively **read-through**:
callers do not know it exists, which is convenient and is also why it cannot see tenants or document
versions nobody put in the request. **Precomputation** (answers for the few hundred most frequent
questions generated by a batch job, summaries written at ingestion) moves model cost off the request
path entirely and suits features whose question set is predictable. **Stale-while-revalidate** serves
an expired entry while one background call refreshes it, trading a bounded extra window of staleness for
never making a user wait on a hot key; it is wrong for content where stale means incorrect, such as
prices or permissions. Eviction under a memory cap is usually LRU or LFU; both ignore that entries
differ greatly in what they cost to recompute, so a long-context answer is worth more cache space than a
short classification.

### Invalidation strategies
1. **Versioned keys.** Include the model id, prompt template version, index version and embedding model
   id in every key. Changing any of them yields a cold cache with no purge logic at all. Simplest and
   most robust; the cost is cold misses after each deploy, which a replay of recent queries can pre-warm.
2. **TTL.** Bounds staleness per data class; refresh ahead of expiry for hot keys; jitter expiries so
   they do not align.
3. **Event-driven purge.** When a document changes, purge the answers that used it. Needs a reverse
   index from document id to cache keys, which the generation step must write. Precise, more complex,
   and required when a deleted document must stop appearing in answers (erasure requests). It has a
   race: a generation that read the old document can finish *after* the purge and write a stale answer
   back. Close it by storing the version (a content hash) of each document an answer used alongside the
   answer, and treating a hit as a miss when any of those versions is no longer current.
4. **Scope isolation.** Tenant and user in the key wherever the prompt carries private context; a shared
   cache across trust boundaries is a leak, not an optimisation.
5. **Do not cache failures.** Errors, refusals, truncated streams and low-judge-score answers are not
   cached, or an outage gets remembered after it ends.
6. **Stampede control.** Single-flight locking or request coalescing on popular keys so a thousand
   simultaneous misses produce one model call.

### Where each layer lives
Prefix caching lives in the provider or engine. Exact and semantic caches can live in a gateway, which
is convenient but context-blind, or in the application, which knows tenants and document versions and is
usually the better place. Observe hit rate, cost saved and latency saved per layer, sampled false-hit
rate, and the age of served entries.

## Example
An exact-match response cache in Redis with versioned, tenant-scoped keys, single-flight on misses, a
rule against caching partial or refused answers, and a reverse index that lets the ingestion pipeline
purge every cached answer that used a document when that document changes.

```python
import hashlib, json, time, redis
r = redis.Redis()
VERSIONS = {"prompt": "support-v14", "index": "kb-2026-10-19", "embed": "text-embed-v2"}

def exact_key(tenant, model, params, messages):
    body = json.dumps({"v": VERSIONS, "t": tenant, "m": model, "p": params, "msgs": messages},
                      sort_keys=True, separators=(",", ":"))      # canonical form: same input, same hash
    return "resp:" + hashlib.sha256(body.encode()).hexdigest()

def cached_complete(tenant, model, messages, params={"temperature": 0}, ttl=6 * 3600, tries=10):
    k = exact_key(tenant, model, messages=messages, params=params)
    if (hit := r.get(k)):
        return json.loads(hit) | {"cache": "exact"}
    if not r.set(k + ":lock", "1", nx=True, ex=30):              # single-flight: one caller fills the key
        if tries == 0:
            return call_model(model, messages, **params) | {"cache": "bypass"}
        time.sleep(0.2)
        return cached_complete(tenant, model, messages, params, ttl, tries - 1)
    try:
        out = call_model(model, messages, **params)
        if out["finish_reason"] == "stop" and not out.get("refusal"):   # never cache partial or refused
            r.set(k, json.dumps(out), ex=ttl)
            for doc_id in out.get("source_doc_ids", []):             # reverse index for event-driven purge
                r.sadd(f"deps:{tenant}:{doc_id}", k)
                r.expire(f"deps:{tenant}:{doc_id}", ttl)
        return out | {"cache": "miss"}
    finally:
        r.delete(k + ":lock")

def on_document_updated(tenant, doc_id):                         # called by the ingestion pipeline
    keys = r.smembers(f"deps:{tenant}:{doc_id}")
    if keys:
        r.delete(*keys)
    r.delete(f"deps:{tenant}:{doc_id}")
```

Bumping `VERSIONS["prompt"]` at deploy time cold-starts the cache without any purge code; the reverse
index handles the case versioning cannot, a single document changing under an unchanged prompt. Two
simplifications to remove before production. The lock is released unconditionally, so a model call that
outlives the lock's 30 s expiry deletes a lock another caller now holds; store a random token in the
lock and delete only if it still matches (a small Lua compare-and-delete). And the purge has the
in-flight race described above, which needs the document versions stored with each answer and checked
on read.

## Trade-offs
- Looser keys and lower similarity thresholds raise the hit rate and the false-hit rate together; the
  right point depends on what a wrong answer costs, which differs between an FAQ bot and a billing
  assistant.
- Longer TTLs save more and serve staler data; acceptable when you also have event-driven purge for the
  content that matters, dangerous when TTL is the only invalidation you have.
- Versioned keys are simple and safe but every deploy cold-starts the cache; when the cache is what meets
  the latency SLO, add a warm-up replay or a brief dual-version window.
- Prefix caching needs a static-first prompt layout, which constrains where personalisation can go; it
  flips only in the rare design where per-user content must precede the shared instructions.
- A global cache maximises hits and is a leak surface; a per-tenant cache is safer with a lower hit
  rate; the trust boundary, not the hit rate, decides.
- Event-driven purge is precise but requires dependency tracking storage and hooks in ingestion; TTL is
  cheap but can only bound staleness, never eliminate it.

## Pitfalls
- Caching across tenants or users; the symptom is a user seeing another customer's data inside an
  answer, which is a security incident rather than a bug.
- Dynamic content at the top of the prompt; the symptom is a cached-token share near zero while prompt
  caching is "enabled" and the bill is unchanged.
- Semantic false hits; the symptom is confident wrong answers whose logs show a cache hit with high
  similarity to a question that differed by a negation or a number.
- Leaving the model or prompt version out of the key; the symptom is half of users seeing the old
  behaviour for hours after a prompt change, and contaminated A/B results.
- Caching errors, refusals or partial streams; the symptom is users still receiving an error message
  long after the provider recovered.
- Stampedes on expiry; the symptom is periodic latency and cost spikes aligned with TTL multiples.
- Purging without guarding against in-flight generations; the symptom is a stale answer reappearing
  minutes after a purge that logged success, typically when a document is edited during busy hours.
- A deleted document still answered from cache; the symptom is a compliance finding after an erasure
  request that the index honoured and the cache did not.

## When to use
Prefix caching is always worth having and is mostly a matter of prompt layout. An exact response cache
pays off for form-like, classification and extraction traffic with deterministic settings. A semantic
cache suits high-volume FAQ-like traffic where a measured false-hit rate is tolerable, and should be
avoided for personalised or transactional queries. An embedding cache belongs in every ingestion
pipeline. Measure hit rate per layer, cost and latency saved, sampled false-hit rate and the age of served
entries, and treat erasure requests as a cache concern. In interviews, lead with the key: list what the
answer depends on, show how versions make invalidation trivial, and explain when you still need
event-driven purge.

## Role lens
- **Engineer:** Builds the versioned key function, scoping, single-flight and metrics, and lays prompts
  out static-first so provider prefix caching actually hits.
- **Architect:** Decides which layers exist and where they live, the staleness budget per data class,
  the cross-tenant policy and the purge obligations that compliance imposes.
- **FDE:** Tunes thresholds and TTLs to the customer's data volatility, demonstrates the cost saving with
  before-and-after usage, and verifies that erasure requests purge cached answers.

## Check yourself
**Q:** You added a semantic cache and cost fell by 30%. Two weeks later a customer reports that the
assistant stated a policy that changed last week. Diagnose and redesign.
**A:** The cache served a stale answer, and there are several candidate causes to check in the logs: a
TTL longer than the policy document's change cycle, no event-driven purge when the document was
re-ingested, an index version missing from the cache scope so the old answer survived the re-index, or a
semantic hit on a paraphrase of a question about the updated section. The redesign: put the index
version in the scope key so re-indexing cold-starts the relevant entries; have generation record the
document ids it used and purge dependents on document update; set TTLs per content class, short for
policy; verify borderline semantic hits with a cheap check or exclude queries mentioning dates and policy
names; sample hits for a false-hit rate; and show the source freshness on cached answers so users can
see what the answer was based on.

## Quiz
### Q: What is the simplest robust way to invalidate every cached response when the prompt template changes?
- [ ] Flush the whole cache by hand after each deploy
- [ ] Reduce the TTL to one minute
- [x] Include the prompt template version in every cache key
- [ ] Rely on semantic similarity to notice that the prompt changed
> Versioned keys make the old entries unreachable the moment the version changes, with no purge code and
> no risk of forgetting. A short TTL still serves stale answers for a minute and destroys the hit rate;
> manual flushes get skipped.

### Q: Why does placing a timestamp at the top of the system prompt hurt?
- [ ] It increases the token count significantly
- [x] It changes the prefix on every request, so provider or engine prefix caching never matches
- [ ] Models ignore timestamps and it is wasted space
- [ ] Providers reject prompts that contain dates
> Prefix caching requires a byte-identical prefix from the first token. A changing value near the top
> invalidates the entire shared span after it, which shows up as a cached-token share near zero.

### Q: Which query pair is the biggest false-hit risk for a semantic cache?
- [ ] "How do I cancel my subscription?" and "how do i cancel my subcription" (typo)
- [x] "How do I cancel my subscription?" and "How do I stop my subscription being cancelled?"
- [ ] "What is your refund policy?" and "Tell me about refunds"
- [ ] "Reset my password" and "I forgot my password"
> The second pair is nearly identical in surface form and embedding space but opposite in intent, so a
> cached answer to one is harmful for the other. The other pairs are genuine paraphrases, which is exactly
> what a semantic cache is meant to match.
