---
id: memory-architecture
module: system-design
name: Memory architecture
kind: concept
difficulty: core
est_minutes: 16
related:
  - agents/agent-memory
  - agents/memory-retrieval-update-deletion-policies
  - agents/context-compaction-summarization
  - prompting/context-engineering
  - agent-reliability/state-persistence-and-conversation-recovery
  - agent-runtime-architecture/multi-tenant-state-isolation
  - data-architecture/data-retention-and-deletion-architecture
  - system-design/rag-architecture
sources:
  - Park et al., 2023 — Generative Agents: Interactive Simulacra of Human Behavior
  - Packer et al., 2023 — MemGPT: Towards LLMs as Operating Systems
---

## TL;DR
Models are stateless and context windows are finite, so anything an application must "remember" across
turns, sessions or users has to be stored outside the model and brought back selectively. Memory
architecture is the design of those stores and the policies that write, read, update and delete them:
working memory in the context, session state in a database, long-term episodic and semantic memory in
retrievable stores, each scoped to a user and tenant. The hard decisions are what to remember and what
to forget.

## Why it exists
Every model call starts from zero. The only thing the model knows about this user, this task and the
last hour is what the application puts in the prompt. For a single question that is fine. For an
assistant that runs over weeks it means that preferences, decisions, half-finished tasks and facts
learned in earlier sessions vanish unless something carries them forward.

The naive design is to carry everything forward: append the whole conversation on every call. Cost and
latency grow linearly with history, because every turn re-reads all previous turns; the window
eventually fills; and quality drops before that, because relevant facts get buried among hundreds of
turns of chatter. It also does nothing across sessions or across the user's other devices and agents.

The second naive design is to embed every utterance into a vector store and retrieve by similarity. This
brings back stale and contradictory facts side by side ("user lives in Berlin" from last year, "moved to
Lisbon" from last week), has no notion of updating or deleting a fact, retrieves things the user told a
different agent in a different context, and cannot answer "what did we decide", because decisions are
not textually similar to the question that needs them.

Memory architecture exists to replace both with explicit stores and policies. It borrows the
operating-system picture: a small, expensive working set (the context window), a larger persistent
store, and a controller that decides what to page in and out. The "what" is the design problem.
Remember too little and the assistant is amnesiac; remember too much and it is expensive, confused and
a privacy liability.

## How it works
### Memory types and where each lives
1. **Working memory** is the context window: system prompt, the current task, recent turns, retrieved
   items. It is rebuilt per call by code; the model never stores anything itself.
2. **Session state** is the full transcript and task state of one conversation or run, kept in a
   database keyed by conversation id. It is the source of truth for the session *because* the context is
   a view of it, not the other way around; compaction summarizes it for the prompt without deleting it.
3. **Episodic memory** records what happened: past sessions, outcomes, decisions, with timestamps. It
   answers "what did we do last time".
4. **Semantic memory** holds distilled facts and preferences: "prefers concise answers", "account tier:
   enterprise", "project X uses Postgres". It is small, structured where possible, and updated in place.
5. **Procedural memory** is learned how-to: instructions, tool-usage notes, corrections that should
   change future behaviour. It lands in prompts, skills or configuration, usually after human review.
6. **External knowledge** (documents, databases) is not memory; it is retrieval, covered by the RAG
   architecture lesson. Keeping them separate matters *because* memory is per user and mutable while
   knowledge is shared and versioned.

### The four operations
- **Write.** Decide what is worth keeping. Options: the model calls a `remember` tool explicitly; a
  background extractor reads the session after each turn or at session end and proposes facts; or the
  application records structured events (purchases, settings changes) directly. Extraction at write time
  keeps reads cheap *because* a read happens on every call and a write only when something changes.
- **Read.** Build the working set: the last *n* turns verbatim, a rolling summary of older turns,
  semantic facts for this user that match the current task, and episodic items ranked by relevance,
  recency and importance. Scope filters (tenant, user, agent, purpose) apply inside the query, not
  after, for the same reason as ACLs in RAG.
- **Update and consolidate.** New facts that contradict old ones must supersede them, not sit beside
  them; duplicates must merge; many episodes collapse into one summary. This runs as a background job
  *because* it needs a model call and should not sit in the request path.
- **Forget.** Time-to-live for episodic items, explicit deletion on user request, purpose limits (memory
  collected for support is not used for marketing), and tenant off-boarding. Deletion must reach every
  derived copy, including embeddings and summaries, which is why memory rows need lineage back to their
  source events.

### A read policy, concretely
A common ranking, from the generative-agents line of work, scores each candidate as a weighted sum of
relevance (embedding similarity to the current task), recency (exponential decay on age) and importance
(a score assigned at write time), then takes the top *k* under a token budget. The weights are a product
decision: a coding assistant weights recency, a CRM assistant weights importance.

### Scoping and isolation
Every memory row carries tenant id, user id, the agent or purpose it was collected for, a source event
id and timestamps. Cross-user leakage through memory is the most embarrassing failure in this area, and
it happens when the key is the agent instead of the user, or when a shared summary is built across
users.

### Budget arithmetic (illustrative)
A 200-turn conversation at roughly 150 tokens per turn replays about 30,000 tokens per call if sent
verbatim. Keeping the last 10 turns verbatim (about 1,500 tokens), a 400-token rolling summary and 800
tokens of retrieved memories puts the working set under 3,000 tokens, a tenfold reduction that stays
flat as the conversation grows. That flat line is the architectural goal.

## Example
A memory layer with the tiers wired together: a session store as the source of truth, a write policy
that extracts facts after each turn and supersedes contradictions, and a read policy that assembles a
bounded working set using relevance, recency and scope. `llm`, `embed`, `cosine` and `store` are
stand-ins for your model, embedding model and database.

```python
import math, time

RECENT_TURNS, SUMMARY_EVERY, MEM_BUDGET_TOKENS = 10, 20, 800

def build_context(user, tenant, convo_id, query, store, w_rel=1.0, w_rec=0.5, w_imp=0.3):
    turns = store.turns(convo_id)                                    # session state: full transcript
    recent, older = turns[-RECENT_TURNS:], turns[:-RECENT_TURNS]
    summary = store.summary(convo_id) if older else ""
    q, now = embed(query), time.time()
    cands = store.memories(tenant=tenant, user=user, purpose="assistant")   # scope inside the query
    def score(m):
        recency = math.exp(-(now - m.updated_at) / (7 * 86400))     # decays over about a week
        return w_rel * cosine(q, m.vec) + w_rec * recency + w_imp * m.importance
    picked, used = [], 0
    for m in sorted(cands, key=score, reverse=True):
        if used + m.tokens > MEM_BUDGET_TOKENS: break
        picked.append(m); used += m.tokens
    return {"summary": summary, "memories": [m.text for m in picked], "recent": recent}

def after_turn(user, tenant, convo_id, store):
    turns = store.turns(convo_id)
    if len(turns) % SUMMARY_EVERY == 0:                              # compaction: summarize, never delete
        store.set_summary(convo_id, llm(f"Update this summary:\n{store.summary(convo_id)}\n\n"
                                        f"New turns:\n{turns[-SUMMARY_EVERY:]}").text)
    facts = llm("Extract durable facts or preferences as JSON [{text, importance 0-1}] "
                f"from:\n{turns[-2:]}").json
    for f in facts:                                                  # update in place, not append-only
        vec = embed(f["text"])
        prior = store.find_similar(tenant, user, vec, threshold=0.9)
        store.upsert_memory(tenant=tenant, user=user, purpose="assistant", text=f["text"], vec=vec,
                            importance=f["importance"], supersedes=prior.id if prior else None,
                            source_event=turns[-1].id)              # lineage: deletion can follow it back
```

## Trade-offs
- Extracting facts at write time keeps every read cheap, but spends a model call per turn and can store
  things the user never meant to be remembered; read-time extraction from raw transcripts is cheaper to
  write and far more expensive to query.
- Structured semantic memory (typed fields) is precise and easy to update or delete, while free-text
  memories capture nuance; most systems need both, with structure for anything that drives behaviour or
  billing.
- Rolling summaries keep context flat as conversations grow, but each compaction removes detail from the
  prompt irreversibly; keeping the full transcript in the session store and retrieving from it recovers
  the detail on demand.
- Letting the model decide what to remember through a tool gives it agency and transparency, but it
  under- and over-remembers unpredictably; a background extractor is more consistent and less visible to
  the user.
- Long retention makes the assistant feel continuous and raises privacy and compliance exposure; TTLs and
  purpose scoping trade some continuity for a defensible deletion story.

## Pitfalls
- Keying memory by agent or conversation instead of tenant and user; the symptom is one user's
  preferences or facts appearing in another user's session.
- Append-only memory without supersession; the symptom is the assistant confidently stating two
  contradictory facts, usually the stale one first.
- Treating the context window as the system of record; the symptom is that after compaction, details the
  user mentioned are gone forever and the transcript cannot be audited.
- Deleting a memory row but not its embedding, its mention in a summary or the facts derived from it;
  the symptom is a "forgotten" fact resurfacing weeks later, which is a compliance incident.
- Shipping memory without measuring it; the symptom is higher cost per call with no gain in task
  success, because the retrieved memories are rarely relevant.

## When to use
Add memory beyond the current session when users return and expect continuity, when the agent performs
multi-session tasks, or when personalization measurably improves outcomes. Start with session state and
compaction, which every multi-turn product needs; add semantic memory when you can name the facts that
change behaviour; add episodic memory when "what happened before" is a real query. Do not add memory to
compensate for a weak retrieval layer or missing product data; those belong in RAG and in your database.
Measure working-set tokens per call, memory hit rate (retrieved items actually used by the answer),
contradiction rate, and task success with memory on versus off. In interviews, the expected depth is the
tier picture, the four operations, scoping, and a concrete deletion story.

## Role lens
- **Engineer:** Implements the session store, compaction, the extraction and consolidation jobs, and the
  scoped, budgeted read path that assembles each prompt.
- **Architect:** Decides the memory tiers and stores, the ownership of each memory type, retention and
  purpose limits, and how deletion propagates to every derived copy.
- **FDE:** Negotiates with the customer what the assistant may remember about whom and for how long, and
  demonstrates deletion end to end for their compliance team.

## Check yourself
**Q:** A customer's support assistant should remember each support agent's preferences and past cases
across sessions, but their legal team requires that any case data be purgeable within 24 hours of a
request. Design the memory architecture and explain how you would meet the purge requirement.
**A:** I would separate the tiers. Session state lives in a transcript store keyed by tenant, user and
conversation, with a rolling summary for the prompt. Semantic memory holds structured preferences per
user, updated in place with supersession. Episodic memory holds case summaries with a case id, tenant,
user, purpose, source event ids and timestamps, with a TTL. Every derived artifact, embeddings, summaries
and extracted facts, records the source event ids it came from. A purge request for a case then becomes
a lineage query: find every row derived from events of that case, delete rows and vectors, and
regenerate any summary that referenced them. Reads filter by tenant and user inside the query so nothing
crosses accounts. I would prove the purge with a test that plants a unique fact, purges, and shows that
no retrieval, summary or extraction can surface it afterwards.

## Quiz
### Q: Why is a session transcript store, rather than the context window, treated as the source of truth for a conversation?
- [ ] The context window cannot hold more than a few turns
- [x] Compaction removes detail from the prompt, so without a persistent transcript that detail is lost and cannot be retrieved or audited later
- [ ] Models require transcripts to be stored before answering
- [ ] Transcripts are cheaper to send than summaries
> The prompt is a view built per call. Summarizing older turns keeps it small, but only works safely if
> the full record exists elsewhere to retrieve from and to audit.

### Q: What problem does supersession in semantic memory solve?
- [ ] It reduces embedding dimensionality for faster search
- [ ] It prevents the model from storing more than one fact per turn
- [x] It ensures a new fact that contradicts an old one replaces it, so the assistant does not retrieve both and state the stale one
- [ ] It allows memories to be shared across tenants
> Append-only memory accumulates contradictions ("lives in Berlin", "moved to Lisbon"). Linking the new
> fact to the one it replaces keeps reads consistent and makes deletion traceable.

### Q: Which design most directly prevents one user's memories from appearing in another user's session?
- [ ] Encrypting memory rows at rest
- [ ] Using a separate embedding model per user
- [x] Keying every memory by tenant and user and applying those filters inside the retrieval query
- [ ] Limiting memories to the last seven days
> Leakage comes from wrong keys and post-filtering, not from encryption or recency. Scope must be part of
> the row and enforced where candidates are selected.
