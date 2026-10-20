---
id: vendor-exit-strategy
module: security-governance-compliance
name: Vendor exit strategy
kind: decision
difficulty: advanced
est_minutes: 16
status: reviewed
related:
  - system-design/ai-gateway
  - system-design/build-vs-buy
  - system-design/open-weight-vs-proprietary
  - system-design/multi-model-architecture
  - data-architecture/embedding-versioning-re-indexing-strategy
  - llmops-platform/model-lifecycle-and-deprecation-policy
  - security-governance-compliance/ai-risk-management
  - security-governance-compliance/compliance-architecture
sources:
  - Regulation (EU) 2022/2554 — Digital Operational Resilience Act (DORA)
---

## TL;DR
A vendor exit strategy is a tested plan for leaving an AI provider (model API, vector database, agent
platform or AI SaaS) within a target time and cost, without losing data or quality. It exists because AI
lock-in hides in places contracts do not mention: prompts tuned to one model, embeddings only that model
can read, fine-tunes you cannot export, and conversation state stored on the vendor's side. Planning exit
early is cheap; discovering lock-in during a price rise, outage or deprecation is not.

## Why it exists
Organizations leave AI vendors for many reasons: price changes, a model deprecated on the vendor's
schedule, a better model elsewhere, a data-residency or sovereignty requirement, a security incident, an
acquisition, or a regulator asking how a critical service would continue if the provider failed. In
financial services this last point is formal: the EU's DORA regulation expects exit strategies for ICT
third-party services that support critical or important functions.

AI makes exit harder than for most SaaS because the coupling is behavioural, not just technical. Swapping
the HTTP client to another provider takes a day. But the prompts were tuned against one model's quirks;
the evaluation thresholds were set on its outputs; every document in the vector store was embedded with
its embedding model, and vectors from different embedding models are not comparable; a fine-tuned model
may exist only inside the vendor's platform; and "assistant" or "thread" APIs may hold conversation
history and files you have no copy of.

The naive alternative, "we use an abstraction layer, so we can switch any time", covers the API shape and
nothing else. The first time a team tries to switch, it discovers quality dropped on a third of its eval
cases, re-embedding the corpus takes days under rate limits, and nobody has the system prompts' history.
An exit strategy turns "we could switch" from a belief into a measured property.

## How it works
### 1. Map the lock-in surface
For each vendor, list what would have to move or be rebuilt, *because* you can only plan for coupling you
can name:
- **API and features**: request formats, tool calling conventions, structured output modes, proprietary
  features (built-in retrieval, server-side state, batch endpoints).
- **Behaviour**: prompts, few-shot examples and thresholds tuned to this model.
- **Derived data**: embeddings, fine-tuned weights, cached outputs, vendor-side indexes.
- **State**: conversation threads, uploaded files, agent memory held by the vendor.
- **Operational**: monitoring, billing and identity integrations, data processing agreements,
  certifications that customers rely on.

### 2. Keep what you need to leave, under your control
- **Own the source of truth.** Original documents, prompts (in a prompt registry), eval sets, labelled
  data and conversation history live in your systems; anything vendor-side is a copy. This is the single
  most important control *because* everything else can be regenerated from it.
- **Thin abstraction at the boundary.** An AI gateway or provider interface that normalizes requests,
  logging and auth, so the switch is configuration plus prompt changes, not a code rewrite. Keep it thin;
  abstracting away every proprietary feature costs you their benefits.
- **Portable evaluation.** A provider-independent eval suite with task-specific acceptance criteria is
  what makes a switch measurable. Without it, you cannot tell whether the new vendor is good enough.
- **Version and record embedding model IDs** with every vector, *because* a migration needs dual indexes
  and a cutover, not an in-place mix of incompatible vectors.
- **Prefer exportable artifacts**: fine-tune with methods and platforms that let you keep the weights or
  at least the training data and recipe so you can reproduce the fine-tune elsewhere.

### 3. Negotiate the exit into the contract
Deprecation notice periods for models, data export in usable formats, deletion with written confirmation,
transition assistance, price-change notice, and continued service during a wind-down. These cost little to
ask for at signing and a lot afterwards.

### 4. Plan two exits, not one
A **planned exit** (price rise, better model, renewal) runs on your timeline: weeks to months, with the
old vendor still serving while you migrate. A **stressed exit** (vendor outage, insolvency, sanctions,
a security incident, a model pulled with no notice) runs on nobody's timeline, and the old vendor may
already be gone. They need different preparation, *because* steps that are fine in a planned exit, such
as five days of re-embedding or two weeks of prompt rework, are an outage in a stressed one. For a
stressed exit you pre-build what cannot be built quickly: a secondary model already passing the eval
suite for the critical workflows, a degraded mode that does not need the vendor at all (keyword search,
templated answers, a human queue), and, if retrieval is critical, a standby index built with another
embedding model.

### 5. Decide how ready to be
Exit readiness is insurance, so price it like insurance. Roughly, readiness is worth buying when
P(exit within horizon) × (cost of exiting unprepared − cost of exiting prepared) exceeds the cost of
staying prepared, where "cost" includes the business impact of the time to exit, not just engineering
hours. That is why a prototype needs only owned data and prompts, while a revenue-critical workflow
justifies a warm second provider: the probability may be similar, but the cost of a slow exit is not.

### 6. Write and test the plan
Define exit triggers, a target time-to-exit, the alternative vendor or self-hosted option per component,
and the steps. Test it: periodically run the eval suite against the alternative, and do at least a partial
migration (one workflow, one index shard) so the estimate is measured, not guessed.

## Example
An insurer's claims assistant uses one vendor for generation and embeddings. The board asks how fast they
could exit. The team sizes it (prices and rates are illustrative assumptions, not quotes):

**Re-embedding.** 2 million documents, about 10 chunks each of 400 tokens: 20M chunks × 400 = 8 billion
tokens. At an illustrative $0.02 to $0.13 per million tokens that is roughly $160 to $1,040, so money is not
the constraint. Throughput is: at an assumed 1 million tokens per minute of rate limit, 8,000 minutes is
about 5.6 days of continuous embedding, plus building and validating a second index while the first keeps
serving.

**Prompts and quality.** 38 production prompts. Running the 1,200-case eval suite against the alternative
model shows 31 prompts meet their acceptance criteria unchanged, 7 need rework (mostly structured-output
and refusal behaviour). Estimated two engineer-weeks.

**Fine-tune.** A classification fine-tune lives only on the vendor platform; weights are not exportable.
The training data and recipe are in their own repository, so it can be reproduced on an open-weight model
in about a week, including evaluation.

**State.** Conversation history is stored in their own database, not the vendor's thread API: nothing to
migrate.

**Result.** Measured planned time-to-exit about 4 weeks, dominated by prompt rework and parallel
indexing. The board's real question, though, is the stressed case: if the vendor were unavailable
tomorrow, claims intake would stop for at least the 5.6 days of re-embedding. The team therefore keeps a
small standby index of the 50,000 most-retrieved documents built with a second embedding model (about
200 million tokens, a few hours of embedding), routes the 5 highest-volume prompts through the
alternative model in a weekly eval run, and defines a degraded mode where adjusters get keyword search
and no generated summaries. Stressed time-to-degraded-service drops from about a week to hours. The
team records this in the exit plan, adds model deprecation notice and data deletion confirmation to the
renewal negotiation, and schedules a quarterly run of the eval suite against the alternative so the
estimate stays current.

## Trade-offs
- A thin gateway keeps switching cheap but forgoes some proprietary features; using a vendor-specific
  capability is fine when its value exceeds the extra exit cost you have consciously recorded.
- Multi-vendor in production gives a live fallback and constant exit readiness, but doubles evaluation,
  prompt maintenance and security review; it pays off only for critical or high-volume workloads.
- Self-hosting open-weight models removes model-vendor lock-in but moves it to GPU capacity, serving
  software and in-house skills; it flips in favour when residency or cost at scale dominates.
- Negotiating strong exit terms costs leverage at signing; for small spend vendors may refuse, so
  compensate with technical controls such as owning all source data.

## Pitfalls
- Treating an SDK abstraction as the whole strategy; the symptom is a switch attempt that compiles in a
  day and fails quality review for a month.
- Storing conversation state or files only in a vendor's managed thread or assistant API; the symptom is an
  export request that returns partial data or nothing usable.
- Mixing vectors from two embedding models in one index during migration; the symptom is retrieval
  quality collapsing for no obvious reason.
- Having no provider-independent eval suite; the symptom is a migration decision made on vibes from a
  handful of demo prompts.
- Ignoring model deprecation schedules; the symptom is a forced migration on the vendor's timeline with no
  tested alternative.
- Planning only the orderly exit; the symptom is an exit plan whose first step assumes the old vendor is
  still answering requests during a vendor outage.

## When to use
Write an exit plan for any vendor on the critical path of a production AI system, before signing or
renewing, and scale its depth with criticality and spend. For a prototype, owning your data and prompts is
enough. For regulated, customer-facing or high-volume systems, add contract terms, a measured
time-to-exit, and regular tests against an alternative. Revisit it when the vendor changes pricing, terms
or models, or when you adopt a vendor-specific feature. In interviews, show you know lock-in is
behavioural and data-level, not just API-level, and that exit readiness is measured with evals.

## Role lens
- **Engineer:** Keeps prompts, evals and embeddings versioned and provider-independent, and makes the
  gateway the only place that knows provider-specific request formats.
- **Architect:** Decides which vendor-specific features are worth their recorded exit cost, sets the
  time-to-exit target per component, and owns the exit plan and its periodic tests.
- **FDE:** Answers the customer's procurement and risk teams on portability, data export and deletion, and
  often builds deployments that must run on the customer's preferred provider instead.

## Check yourself
**Q:** Leadership says "we're not locked in, everything goes through our LLM gateway." What questions
would you ask to test that claim?
**A:** The gateway covers request format, not behaviour or data. I would ask: do we have a provider-independent
eval suite and have we run it against an alternative recently, and with what result per prompt? Which
embedding model produced our vectors, how large is the corpus, and how long would re-embedding take under
realistic rate limits? Do any fine-tunes, files or conversation threads live only on the vendor's
platform? Which vendor-specific features do we rely on? What do our contracts say about deprecation
notice, data export and deletion? The honest answer is a measured time-to-exit, not a yes or no.

## Quiz
### Q: Why does switching embedding providers usually require re-embedding the whole corpus?
- [ ] Vector databases are tied to the embedding vendor's licence
- [x] Vectors from different embedding models live in different spaces and cannot be meaningfully compared
- [ ] Old embeddings expire after a fixed period
- [ ] New providers refuse to accept vectors created elsewhere
> Each embedding model learns its own geometry, so a query vector from model B cannot be compared with
> document vectors from model A. Migration therefore needs a full re-embed into a new index and a cutover.

### Q: Which control most reduces the cost of exiting an AI vendor?
- [ ] Using every proprietary feature to maximize value before leaving
- [ ] Signing a longer contract for a lower price
- [x] Keeping source documents, prompts, eval sets and conversation history in your own systems
- [ ] Caching the vendor's outputs indefinitely
> Owned source data lets you regenerate embeddings, rebuild fine-tunes and re-evaluate on any provider.
> Cached outputs from the old model do not help produce a working system on a new one.

### Q: A team claims portability because their code calls models through a common interface. What is the most likely gap?
- [ ] The interface adds too much latency
- [ ] Common interfaces cannot handle streaming
- [x] Prompts and thresholds tuned to one model may fail quality checks on another, which only an eval suite reveals
- [ ] The interface requires the same pricing across vendors
> API compatibility is the easy part. Behavioural coupling means the same prompt can produce different
> structure, refusals and quality, so portability must be demonstrated by running acceptance evals.
