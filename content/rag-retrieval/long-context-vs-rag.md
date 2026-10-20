---
id: long-context-vs-rag
module: rag-retrieval
name: Long-context vs RAG
kind: decision
difficulty: core
est_minutes: 14
status: reviewed
related:
  - model-internals/context-window
  - rag-retrieval/lost-in-the-middle
  - prompting/prompt-caching
  - serving-inference/prefix-caching
  - training-adaptation/fine-tune-vs-rag-vs-prompt-decision
  - infra-capacity-economics/cost-modeling
  - system-design/rag-architecture
  - rag-retrieval/parent-document-retrieval
sources:
  - Liu et al., 2023 — Lost in the Middle: How Language Models Use Long Contexts
---

## TL;DR
With context windows of hundreds of thousands of tokens, you can sometimes skip retrieval and put the
whole corpus in the prompt. That wins when the corpus is small, static and reused, or when the task needs
whole-document reasoning. RAG wins when the corpus is large, changing, permissioned or high-volume, because
you pay per input token, prefill latency grows with length, and models use long contexts less reliably than
the advertised window suggests.

## Why it exists
RAG was born partly out of necessity: early context windows held a few thousand tokens, so you had no
choice but to select. As windows grew to the hundreds of thousands and, for some models, around a million
tokens, a fair question arose: why maintain chunking, embeddings, indexes and rerankers, each a source of
failure, when the model could just read everything?

The naive answer in either direction is wrong. "Always stuff the context" ignores that every token is paid
for on every call, that prefill time grows with input length, and that recall of a specific fact inside a
very long prompt is less reliable than recall from a short one, especially when the context is full of
near-duplicate distractors. "Always retrieve" ignores that retrieval itself fails (see retrieval failure
diagnosis), that chunking destroys cross-references, and that questions like "list every clause in this
contract that conflicts with section 4" need the whole document, not the top five chunks.

So this is a genuine engineering decision with measurable inputs: corpus size, change rate, query volume,
permission model, latency budget, and whether answers need local facts or global synthesis.

## How it works
### The cost and latency model
For long-context, input tokens per query are roughly the corpus size *C*; for RAG they are the retrieved
budget *R* plus the question, with *R* often a few thousand tokens. Cost per query scales with input tokens,
so the ratio is about *C / R*. Prefill compute grows at least linearly with input length (attention adds a
quadratic term), so time-to-first-token also grows with *C*. Prompt caching changes the arithmetic: if the
corpus is an identical prefix across calls, many providers bill cached input tokens at a discount and skip
much of the prefill. That only holds while the prefix is byte-identical and the cache stays warm, so a
corpus that changes hourly or differs per user gets little benefit.

Written out, with per-token input price *p*, cached-read discount factor *d* (the fraction of full price a
cached token costs) and cache hit rate *h*:

```
cost_long ≈ p × C × (h × d + (1 − h))        + cache-write cost on each miss
cost_rag  ≈ p × (R + q)                       + embedding, index and rerank costs
```

The hit rate *h* is not a free parameter. Provider caches expire after a short idle period (minutes, and
vendor-specific), so *h* depends on whether requests sharing the prefix arrive closer together than the
TTL, and some providers price the cache *write* above a normal input token. A busy single-tenant workload
can sit near *h* ≈ 1; a workload with few queries per prefix pays the write premium again and again.

### The quality model
A model accepting N tokens is not the same as a model using N tokens well. Known effects: facts in the
middle of long contexts are recalled less reliably than those at the start or end (lost in the middle),
performance degrades more on multi-fact and reasoning tasks than on single-needle lookups, and irrelevant
but similar passages act as distractors. Effective context is model-specific and task-specific, so it must
be measured on your own data, not read off a spec sheet. RAG has its own quality ceiling: if the retriever
misses the evidence, the generator never sees it.

### The non-functional constraints
- **Permissions:** with one shared prompt, every user sees every document. RAG filters per user at query
  time; long-context needs a per-permission-set prompt, which kills cache reuse.
- **Freshness:** RAG updates one document's chunks; long-context rebuilds the whole prompt (and cache).
- **Citations:** RAG returns chunk IDs naturally; long-context needs the model to cite positions, which is
  harder to verify.
- **Scale:** past the window, long-context simply does not apply; most enterprise corpora are far larger.

### The middle ground
The two combine. Retrieve at the *document* level, then put whole retrieved documents into a large context
instead of tiny chunks. This keeps retrieval's selectivity and permission filtering while giving the model
full sections to reason over, and it tolerates imprecise ranking because the window is generous.

There is also a third shape that neither pure approach handles: *global* questions over a corpus larger
than the window ("what are the recurring complaints across 30,000 tickets?"). Top-k retrieval returns a
handful of examples and calls it an answer; long-context cannot fit the corpus. The workable pattern is
map-reduce (process each document or batch independently, then aggregate the partial results) or
precomputed summaries that retrieval can target. It is expensive per question, which is why it is usually
run offline or on a schedule.

## Example
An illustrative calculation. Prices and latencies below are made-up round numbers for the arithmetic, not
any vendor's rates.

A support team has a 300-page product manual: about 150,000 tokens. They expect 20,000 questions a day.
Assume input costs $3 per million tokens, cached input is billed at 10% of that, and RAG sends 6,000 tokens
of retrieved context per question.

- **Long-context, no cache:** 150k × 20,000 = 3 billion tokens/day → $9,000/day.
- **Long-context, cached prefix:** 3B × $0.30/M ≈ $900/day, plus periodic cache writes. Only valid if every
  user may see the whole manual and it changes rarely.
- **RAG:** 6k × 20,000 = 120M tokens/day → $360/day, plus embedding and vector store costs.

Check the cache assumption before trusting the middle number: 20,000 questions a day is one every four
seconds or so, comfortably inside any cache TTL, so *h* stays near 1. At 50 questions a day spread across
office hours, gaps between requests often exceed the TTL, so many requests pay full price plus a cache
write, and the $900 figure is not reachable.

Now change one fact: the manual has per-customer editions and 40 customers. Long-context needs 40 cached
prefixes, each with its own warm-up cost and eviction risk. RAG just adds a `customer_id` filter.

Change another: a legal team asks five questions a day over one 200-page contract, each requiring
cross-references. Cost is trivial at that volume, and whole-document reasoning beats top-k chunks. Long
context wins.

## Trade-offs
- Long-context removes the retrieval failure surface entirely, but you pay per token on every call; it flips
  in favour of RAG as query volume times corpus size grows past your cost budget.
- Prompt caching makes a large static prefix cheap, but only for identical prefixes; per-user permissions,
  frequent edits or many tenants fragment the cache and erase the saving.
- Whole-document context preserves cross-references and global structure, while RAG chunks lose them; RAG
  regains the advantage when questions are local lookups of one fact among thousands of documents.
- RAG keeps prompts short and latency low, but every stage can drop evidence; when retrieval recall on your
  eval set is poor and the corpus fits, long-context is the faster way to a working system.

## Pitfalls
- Trusting the advertised window as the usable window; the symptom is a demo that works on the first and
  last sections and quietly misses facts from the middle of the corpus.
- Stuffing a multi-tenant corpus into one prompt; the symptom is a user getting an answer drawn from a
  document they are not entitled to see.
- Comparing approaches on a handful of easy questions; both look perfect, and the gap only shows on
  multi-fact, distractor-heavy or cross-document questions in a proper eval set.
- Forgetting latency; the symptom is a time-to-first-token of many seconds on every request because the
  full corpus is prefilled each time without a warm cache.
- Modelling cached long-context cost at a 100% hit rate; the symptom is a bill several times the forecast
  because traffic is bursty or low-volume and the cache keeps expiring between requests.
- Answering corpus-wide aggregate questions with top-k retrieval; the symptom is confident "trends" built
  from five retrieved examples that a count over the full corpus would contradict.
- Building heavy RAG infrastructure for a corpus of a few documents; the symptom is weeks spent tuning
  chunking for a problem the model could have solved by reading everything.

## When to use
Start with long-context when the corpus fits comfortably in the window with headroom, changes rarely,
has a single permission scope, and query volume is modest, or when tasks need synthesis across a whole
document. Prefer RAG when the corpus exceeds the window or grows, changes frequently, needs per-user
filtering, must cite verifiable sources, or volume makes per-token cost dominant. Measure both on the same
eval set: accuracy split by question type, cost per query, and p95 time-to-first-token, and log the real
cache hit rate rather than assuming one. If the questions are aggregates over a corpus that does not fit,
neither option is right; plan a map-reduce or offline summarisation job instead. In interviews,
the expected answer is "it depends on these five variables, here is the arithmetic", not a preference.

## Role lens
- **Engineer:** Builds both variants behind one interface, measures accuracy, cost and latency on the same
  eval set, and implements document-level retrieval into a large context as the hybrid.
- **Architect:** Owns the cost model and the permission boundary: decides which workloads justify retrieval
  infrastructure and where cached long prompts are safe given tenancy and change rate.
- **FDE:** Runs a quick long-context prototype on the customer's real documents to prove value in days, then
  explains with numbers when volume, permissions or corpus growth require moving to RAG.

## Check yourself
**Q:** A customer's entire knowledge base is 80,000 tokens today and the model window is far larger. They
ask why they should pay for a retrieval pipeline. What do you tell them?
**A:** At 80k tokens long-context is a strong default: no chunking or index to get wrong, and prompt
caching makes a static prefix cheap. But I would check four things. Volume: tokens per query times daily
queries against the budget, with and without cache hits. Permissions: if different users may see different
documents, one shared prompt leaks data. Change rate and growth: frequent edits invalidate the cache, and
if the base will grow past what the model uses reliably, we will need retrieval anyway. Quality: test
middle-of-context and multi-fact questions, not just easy ones. If all four pass, ship long-context and
revisit when one changes.

## Quiz
### Q: Why does prompt caching often fail to rescue the long-context approach in a multi-tenant SaaS product?
- [ ] Cached tokens are billed at a higher rate than uncached ones
- [x] Each tenant or permission set needs a different prefix, so caches fragment and are rarely reused
- [ ] Caching only works for prompts shorter than the RAG context
- [ ] Cached prompts cannot include document text
> Caching saves cost only when many calls share an identical prefix. Per-tenant or per-permission corpora
> create many distinct prefixes, each needing its own write and staying warm, so the discount rarely applies.

### Q: Which task most favours putting a whole document in context over top-k chunk retrieval?
- [ ] Finding the support phone number in a 10,000-document knowledge base
- [ ] Answering "what is our parental leave policy" across all HR documents
- [x] Listing every clause in one contract that contradicts its termination section
- [ ] Looking up a product's price by SKU
> Contradiction finding needs the model to compare many parts of one document, which chunk retrieval
> fragments. The others are local lookups over large corpora, where retrieval is cheaper and sufficient.

### Q: A model advertises a very large context window. What is the right way to decide how much of it to use?
- [ ] Use all of it; the provider guarantees uniform recall across the window
- [ ] Never exceed half the window, which is a universal rule of thumb
- [x] Measure accuracy on your own questions as context length grows, including middle-position and multi-fact cases
- [ ] Use whatever fits within the per-request rate limit
> Effective context depends on the model and the task; recall varies with position and with distractors.
> Only an eval on your data at increasing lengths tells you where quality starts to drop.
