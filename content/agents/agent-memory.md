---
id: agent-memory
module: agents
name: Agent memory (short-term, long-term, episodic)
kind: concept
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - agents/memory-retrieval-update-deletion-policies
  - agents/context-compaction-summarization
  - agents/agent-loop
  - model-internals/context-window
  - prompting/context-engineering
  - rag-retrieval/vector-databases
  - agent-reliability/state-persistence-and-conversation-recovery
  - agents/reflection-self-critique
sources:
  - Sumers et al., 2023 — Cognitive Architectures for Language Agents
  - Park et al., 2023 — Generative Agents: Interactive Simulacra of Human Behavior
  - Packer et al., 2023 — MemGPT: Towards LLMs as Operating Systems
---

## TL;DR
A language model is stateless: it "remembers" only what is in the current context window. Agent
memory decides what goes into that window and what lives outside it. **Short-term** memory is the
window itself, managed by truncation and compaction. **Long-term** memory survives sessions and holds
semantic facts and procedures. **Episodic** memory records specific timestamped events so the agent
recalls what happened, not just what is true. Memory is RAG over the agent's own history, plus a
write path.

## Why it exists
A model call is a pure function of its input. Close the session and the agent forgets the user's name,
the fact that the staging database is read-only, and that the last deployment failed because of a
missing environment variable. Users experience this as an assistant that asks the same questions every
day. Inside one session the problem is the mirror image: the context fills with tool output, and once
the window is full something must go, so the agent "forgets" the start of the task it is still doing.

The naive fix is to keep everything in context. It fails on three axes. Cost: every token is re-read
on every step, so a 100k-token history makes each step expensive (prompt caching discounts a repeated
prefix, but cached tokens are still billed and still add latency). Capacity: the window is finite and
long sessions exceed it. Quality: attention over very long contexts is uneven, so a relevant fact in
the middle of a huge history is attended to less reliably than the same fact placed near the question.

The next naive fix is a vector store of all past messages, retrieved by similarity. That is a start,
but raw transcripts retrieved by similarity return chatter rather than facts, contradict each other
when the user changes their mind, and never forget. What is needed is a system with distinct memory
types, each with its own write rule, storage, retrieval rule and lifetime. The cognitive-architecture
framing (working, episodic, semantic, procedural) is useful not because agents are brains but because
it separates "what happened" from "what is true" from "how to act", and those need different handling.

## How it works
### Short-term (working) memory
The context window: system prompt, the current conversation, tool results, the plan and any scratch
notes. It is the only memory the model reads directly, so everything else must be loaded into it. It is
managed by budgets: reserve space for the system prompt and retrieved memories, truncate or summarise
old turns, drop verbose tool outputs after they have been used, and keep the plan near the end where
attention is strongest. Compaction (summarising older turns into a short note) is the standard way to
extend a session beyond the window; the trade is fidelity for room.

### Long-term memory
Persisted outside the model and loaded on demand. Two sub-types matter in practice:

- **Semantic memory**: facts about the user, the environment and the domain, stored as short
  declarative statements with provenance and a timestamp ("user's team deploys on Tuesdays", "prod DB
  is Postgres 15"). Written by an extraction step that reads a turn or a session and emits candidate
  facts, deduplicated against what is stored. Stored as text with embeddings, often alongside a
  key-value or graph layer for exact lookups.
- **Procedural memory**: how to do things, from the system prompt and tool descriptions to learned
  recipes ("to reset the cache, call X then wait for Y"). It usually lives in prompts, skill files or a
  small library the agent can read, *because* procedures are read whole rather than retrieved by
  similarity.

### Episodic memory
Records of specific past events: a conversation, a task attempt, a tool failure, with time, context
and outcome. It is kept separate from semantic memory *because* an event is evidence, not a fact: "on
Oct 3 the deploy failed with a missing variable" is useful even if the cause has been fixed, and a
dozen episodes can be consolidated into one semantic fact ("deploys often fail on missing env vars;
check first"). Reflexion-style lessons are episodic memory distilled into guidance. Episodes are
retrieved by a blend of relevance, recency and importance, as in the Generative Agents memory stream,
so recent and significant events outrank old trivia.

### The read and write paths
1. **Write**: at turn or session end, an extraction prompt proposes facts and episodes. Each is
   checked against existing entries (add, update, ignore), tagged with source and time, embedded and
   stored. Write gating matters: only store from trusted sources, never from retrieved third-party
   text, or the memory becomes a persistence layer for prompt injection.
2. **Read**: at the start of a turn, build a query from the current input (and plan), retrieve top-k
   memories within a token budget, and place them in a labelled block in the system prompt or first
   user turn, with timestamps so the model can judge staleness.
3. **Consolidate**: periodically merge duplicates, summarise episodes into semantic facts, and expire
   what is stale. This is what keeps retrieval quality from degrading as the store grows.

### Model-managed memory
MemGPT-style designs expose memory operations as tools: the model itself decides to save a fact,
search archival memory or page old context out. This works *because* the model knows what it will
need, but it costs tool calls and is only as reliable as the model's judgement; many production
systems combine a small model-managed "core" block with automatic extraction and retrieval.

## Example
A minimal memory layer: extract facts at session end, store them with embeddings and timestamps,
retrieve by cosine similarity weighted by recency, and inject them into the next session's prompt.

```python
import time, numpy as np

store = []                                       # [{user, text, vec, ts, source, kind}]

def embed(text):                                 # any embedding model; returns unit vector
    v = np.asarray(embedding_model(text), dtype=float)
    return v / np.linalg.norm(v)

def write_memories(user_id, transcript):
    facts = llm_json(f"From this conversation extract durable facts about the user or their "
                     f"environment as a JSON list of short statements. Ignore chit-chat.\n{transcript}")
    for f in facts:
        vec = embed(f)
        mine = [m for m in store if m["user"] == user_id]
        near = max(mine, key=lambda m: float(vec @ m["vec"]), default=None)
        if near is not None and float(vec @ near["vec"]) > 0.92:   # threshold is model-specific
            near.update(text=f, vec=vec, ts=time.time())  # crude "newest wins" supersede
            continue
        store.append({"user": user_id, "text": f, "vec": vec, "ts": time.time(),
                      "source": "user_conversation", "kind": "semantic"})

def recall(user_id, query, k=5, half_life_days=30):
    q = embed(query)
    scored = []
    for m in store:
        if m["user"] != user_id:                 # scope first: never cross users
            continue
        age_days = (time.time() - m["ts"]) / 86400
        recency = 0.5 ** (age_days / half_life_days)
        scored.append((0.8 * float(q @ m["vec"]) + 0.2 * recency, m))
    top = sorted(scored, key=lambda s: -s[0])[:k]
    return "\n".join(f"- ({time.strftime('%Y-%m-%d', time.localtime(m['ts']))}) {m['text']}"
                     for _, m in top)

system = "You are a deployment assistant.\n\nKnown about this user (may be stale):\n" \
         + recall("u42", "help me ship the payments service")
```

The weights, the 0.92 threshold and the half-life are illustrative; the shape to copy is scoping by
user before scoring, blending relevance with recency, and surfacing timestamps so the model can discount
old facts. Note the write path: "plan is Pro" and "plan is Basic" embed as near-duplicates, so simply
*skipping* near-duplicates would keep the stale fact and drop the new one. The crude fix here lets the
newest statement supersede; the update-policy lesson replaces it with an explicit add/update/ignore
decision and keeps the superseded value for audit.

## Trade-offs
- Externalising memory keeps the context small and cheap, but every retrieval is a chance to miss the
  fact that mattered; when sessions are short and the history fits, keeping it in context is simpler.
- Extracting facts at write time makes retrieval precise, yet extraction is lossy and biased by the
  prompt; storing raw episodes too lets you re-extract later at storage cost.
- Model-managed memory tools adapt to what the task needs, at the cost of extra tool calls and
  reliance on the model remembering to save; automatic extraction is more consistent but less targeted.
- Recency weighting keeps memories fresh, but it buries stable long-term facts under recent trivia
  unless importance or a semantic/episodic split counterbalances it.

## Pitfalls
- Storing raw transcripts and retrieving by similarity alone; the symptom is "memories" that are
  chatter, contradictions surfacing together, and the model picking the wrong one.
- Writing memories from untrusted content such as web pages or documents; the symptom is an
  instruction that persists across sessions after a single injected page.
- No timestamps or provenance on memories; the symptom is the agent confidently acting on a preference
  the user changed months ago.
- Missing tenant or user scoping in retrieval; the symptom is one user's facts appearing in another
  user's session, a privacy incident rather than a quality bug.
- Treating compaction summaries as lossless; the symptom is an agent that forgets a constraint stated
  early in a long session right after the context was compacted.

## When to use
Add long-term memory when the same user or environment returns across sessions and re-asking costs
trust or time: personal assistants, support agents, developer tools tied to one codebase. Add episodic
memory when past attempts carry lessons: recurring tasks, flaky environments, anything with a failure
history. Skip persistent memory for one-shot tasks, and prefer in-context history when sessions are
short. Measure retrieval hit rate on a labelled set of "should have remembered" cases, staleness
incidents, and token cost per turn. Day to day, this is the extraction prompt, the store schema and the
retrieval budget. In interviews, be able to separate the three types, explain the write and read paths,
and say why memory needs scoping and provenance.

## Role lens
- **Engineer:** Builds the extraction and recall steps, the store schema with timestamps and scope,
  and the context budget that decides how much memory each turn receives.
- **Architect:** Chooses which memory types the product needs, where they live, how they are isolated
  per tenant, and how memory interacts with compaction and state persistence.
- **FDE:** Decides with the customer what the agent may remember about their users and systems,
  demonstrates the recall quality, and handles their privacy review.

## Check yourself
**Q:** A customer-support agent remembers users well for a week, then starts giving answers that mix
up current and outdated account details. Diagnose the likely memory design flaws and fix them.
**A:** The symptoms point to an append-only semantic store with no update or recency handling. When
a user's plan changes, a new fact is added beside the old one, and similarity retrieval returns both
because they are nearly identical text; the model then picks arbitrarily. Fix the write path: on each
candidate fact, retrieve near-duplicates and decide add, update or ignore, superseding the old entry
and keeping history for audit. Fix the read path: store timestamps and provenance, blend recency into
scoring, and show dates in the injected block so the model can prefer the newer fact. Separate
episodic records ("plan changed on date X") from the semantic fact ("plan is Pro") so events do not
masquerade as current truth. Finally, add a staleness test: a labelled set of users with changed details
where the agent must answer with the latest value.

## Quiz
### Q: Why keep episodic memory separate from semantic memory instead of storing everything as facts?
- [ ] Episodic memories are too long to embed
- [x] An event is evidence with a time, not a current truth, and many events can be consolidated into one fact
- [ ] Semantic memory cannot be timestamped
- [ ] Vector databases only support one collection per agent
> "The deploy failed on Oct 3" is useful history even after the cause is fixed; treating it as a fact
> would make the agent believe deploys currently fail. Keeping events separate lets consolidation derive durable facts.

### Q: What is the most important property of the memory write path from a security standpoint?
- [ ] Facts are embedded with a high-dimensional model
- [ ] Memories are stored in the same database as the application data
- [x] Only trusted sources such as user statements and verified tool results are allowed to create memories
- [ ] Writes happen at the end of every turn
> If retrieved documents or web pages can create memories, a single injected instruction persists into
> every future session. Gating writes by source is what keeps memory from becoming a persistence layer for injection.

### Q: Why is "keep the whole history in context" not a substitute for a memory system?
- [ ] Models cannot read more than a few thousand tokens
- [x] Cost scales with tokens re-read per step, the window is finite, and attention over long contexts is uneven
- [ ] Context windows cannot contain tool results
- [ ] History in context cannot be scoped per user
> Every step re-reads the full context, long sessions eventually exceed it, and facts buried in a huge
> history are attended to less reliably than the same facts retrieved and placed near the question.
