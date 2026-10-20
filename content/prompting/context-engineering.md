---
id: context-engineering
module: prompting
name: Context engineering
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - prompting/system-prompt
  - prompting/prompt-caching
  - prompting/few-shot
  - model-internals/context-window
  - rag-retrieval/lost-in-the-middle
  - rag-retrieval/long-context-vs-rag
  - agents/context-compaction-summarization
  - agents/agent-memory
sources:
  - Liu et al., 2023 — Lost in the Middle: How Language Models Use Long Contexts
  - Anthropic, 2025 — Effective context engineering for AI agents
---

## TL;DR
The context window is the model's entire working memory for one call: finite, billed per token and
attended to unevenly. Context engineering is deciding, for every call, what goes in, in what order, in
what form and under what budget: instructions, tool definitions, retrieved documents, memory, history and
tool results. Prompt engineering writes the instructions; context engineering manages everything around
them, and in multi-step systems that is where quality and cost are decided.

## Why it exists
A single chat turn fits comfortably in any modern window. The problem appears when a system has more
relevant material than fits, or more than the model can use well: an agent on step 60 with sixty tool
results behind it, a retrieval pipeline with forty candidate chunks, a customer with a 300-page manual.
Three forces make "put it all in" the wrong default even when it fits.

First, cost and latency scale with tokens, and in a loop the whole history is re-sent every turn, so a
context that grows linearly produces a bill that grows quadratically. Second, attention is not uniform:
models use material at the start and end of a long context better than material in the middle, and
irrelevant tokens do not merely cost money, they distract, so accuracy can fall as context grows (often
called "context rot"; how fast it sets in varies by model and task). Third,
without management the window eventually overflows mid-task, usually at the worst moment.

The opposite naive answer, aggressive truncation, fails the other way: it drops the one earlier fact
the next step needed, and the agent redoes work or contradicts a decision it made ten steps ago. The
resolution is to treat context as a managed resource with a budget, a priority order and a lifecycle,
the way an operating system treats memory.

## How it works
### What the context contains, in layers
Every API renders the request in a fixed order (on Anthropic's: tool definitions, then the system
prompt, then messages; other providers fix their own). Inside that, the
material has very different stability: identity and rules (never change), tool definitions (rarely),
long-lived reference and memory (per deployment or per user), session state such as retrieved chunks and
a user profile (per task), conversation history (grows), the latest tool results (short-lived), and the
current question. Stability drives placement *because* caching is a prefix match and because the model
should meet the durable rules before the volatile data.

### Budgeting
Give each layer a token budget that sums to less than the window minus a reserve for output. Count
tokens with the tokenizer or the provider's count endpoint, never characters, since code and non-Latin
text produce more tokens per character than English prose. Trigger compaction at a threshold well below the limit (say 70% of the window, an
illustrative figure) rather than on overflow, because compaction itself needs room to run.

### Placement
Stable content first so it caches. Critical instructions at the start and the actual question at the
end, exploiting primacy and recency. Retrieved documents ordered so the most relevant sit at the edges
and the weakest in the middle, which is where attention is weakest (a heuristic from the lost-in-the-middle
results; how much it helps varies by model, so verify it on your own evals). Explicit delimiters (XML tags, headed
sections) so the model can tell instruction from data, and labels on untrusted content so it is less
likely to be followed as instruction.

### Selection: preload or fetch just in time
Preloading everything a task might need is simple and avoids retrieval misses; fetching on demand through
tools (search, read file, query) keeps the context small and current. Agents increasingly keep
lightweight references, such as file paths and record ids, and fetch content when a step needs it.
Preload when the corpus is small and stable and latency matters; fetch when it is large, changing or
only partly relevant to any one task.

### Lifecycle: pruning, compaction, memory
Tool results are the highest-volume, shortest-lived content. Once a result has been consumed, clear it or
replace it with a one-line note, keeping the decision it informed. When history nears the threshold,
compact: summarise the older turns into a structured note (goal, decisions made, open items, current
state of files or records) and keep the recent turns verbatim. Compaction is lossy, so keep the raw log
outside the context where it can be retrieved. Facts that must survive across sessions go to a memory
store and are loaded selectively, which the agents module covers.

### The tension between pruning and caching
Every edit to earlier context (stubbing a tool result, compacting, deleting a reminder) changes the
prefix from that point, so the next request re-writes everything after the edit instead of reading it
from cache. Work the numbers with the usual multipliers (writes about 1.25×, reads about 0.1× of base
input; provider-dependent): in a 100k-token history, stubbing a 30k-token result that sits at position
20k forces an 80k re-write, about 80k × (1.25 − 0.1) ≈ 92k token-equivalents extra, once. The saving is
30k tokens no longer read per later turn, which at the cached rate is only about 3k-equivalents a turn,
so the edit takes roughly thirty turns to pay back in money. The conclusion is not "never prune": the
real reasons to prune cached content are attention quality and window headroom, not cost. It is to
prune in batches when a threshold is crossed (one re-write, many tokens removed), to keep edits close
to the tail where little follows them, and never to edit history on every turn.

### Isolation through sub-agents
A read-heavy subtask (explore a codebase, survey twenty documents) can run in a sub-agent with its own
context that returns only a distilled result. The parent's context stays small and focused; the cost is
coordination and the loss of shared detail.

### Progressive disclosure
Rather than loading every tool definition and every instruction set, expose an index and load on demand:
a tool-search tool over a large catalogue, skill instructions pulled in when the task matches. The model
sees what the current step needs, not everything it might ever need.

### Measuring
Tokens per turn and per task, cache hit ratio, task success as a function of context size, and needle
tests on your own documents rather than published benchmarks, because position sensitivity varies by
model and by content.

## Example
A budgeted context assembler. Retrieved chunks are kept while they fit and ordered best-at-the-edges,
consumed tool results are stubbed (not deleted, because every `tool_use` block in history must keep its
matching `tool_result`), history is compacted at a plain user-text turn when over budget, and the total
is asserted against the window minus the output reserve. `count` wraps your tokenizer or the
provider's token-count endpoint; `summarize` is a model call that writes a structured state note.

```python
WINDOW, OUTPUT_RESERVE = 200_000, 16_000
BUDGET = {"system": 6_000, "tools": 8_000, "retrieved": 40_000, "history": 100_000}   # illustrative split

def stub(m):        # same tool_use_ids, one-line content: the call/result pairing stays valid
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": b["tool_use_id"],
            "content": "[consumed; see state note]"} for b in m["content"]]}

def fresh_user_turn(m):    # a turn of plain user text, never a tool_result (its tool_use would be cut off)
    return m["role"] == "user" and isinstance(m["content"], str)

def assemble(system, tools, retrieved, history, consumed, question, count, summarize):
    assert count(system) <= BUDGET["system"] and count(tools) <= BUDGET["tools"]
    # 1. retrieval: keep the best chunks that fit, then put the strongest at the edges, weakest in the middle
    kept, used = [], 0
    for chunk in retrieved:                                    # sorted by relevance, best first
        if used + count(chunk.text) > BUDGET["retrieved"]:
            break
        kept.append(chunk); used += count(chunk.text)
    ordered = kept[0::2] + kept[1::2][::-1]                     # best, 3rd, 5th ... 4th, 2nd
    docs = "\n".join(f"<doc id='{c.id}' source='{c.source}'>\n{c.text}\n</doc>" for c in ordered)
    # 2. history: stub consumed tool results (indexes in `consumed`), then compact if still over budget
    hist = [stub(m) if i in consumed else m for i, m in enumerate(history)]
    if count(hist) > BUDGET["history"]:
        cut = max(len(hist) - 10, 1)
        while cut < len(hist) and not fresh_user_turn(hist[cut]):
            cut += 1
        if cut == len(hist):
            raise RuntimeError("no safe compaction point in the recent turns")
        head, tail = hist[:cut], hist[cut:]
        hist = [{"role": "user", "content": f"<summary>{summarize(head)}</summary>"},
                {"role": "assistant", "content": "Understood. Continuing from the summary."}] + tail
    messages = hist + [{"role": "user", "content": f"<documents>\n{docs}\n</documents>\n\n{question}"}]
    total = count(system) + count(tools) + count(messages)
    assert total <= WINDOW - OUTPUT_RESERVE, f"{total} tokens over budget"
    return {"system": system, "tools": tools, "messages": messages}
```

In a cached agent loop, call `assemble` with a `consumed` set that changes only when the history
budget trips, not every turn; otherwise each newly stubbed index rewrites the prefix from that message
on and the cache never covers the history (see the pruning-versus-caching arithmetic above).

## Trade-offs
- Loading whole documents is simpler and has no retrieval misses, but costs tokens every turn and
  degrades attention; it flips to retrieval once the corpus exceeds a fraction of the window or the same
  context is re-sent across many turns.
- Compaction saves tokens but is lossy; it flips to harmful when the task depends on exact earlier
  details such as diffs or numbers, unless the raw log stays retrievable.
- Just-in-time fetching keeps context small and fresh but adds tool round trips and latency; preload wins
  for small, stable corpora on latency-sensitive paths.
- Sub-agent isolation keeps each context focused but spends tokens on coordination and loses shared
  detail; it pays when the subtask would otherwise flood the parent with raw material.
- Delimiters and labels cost tokens but improve the separation of data from instruction, which also
  raises resistance to injection; the cost is small next to the content they wrap.
- A large output reserve and compaction headroom reduce usable input; size them from the observed
  distribution of output lengths rather than a fixed guess.

## Pitfalls
- Letting tool results accumulate forever; the symptom is overflow errors mid-task or a quality collapse
  after a few dozen steps even though the window was never exceeded.
- Placing key constraints in the middle of a long prompt or the question before the documents; the
  symptom is the model ignoring an instruction it demonstrably received.
- Counting characters instead of tokens; the symptom is intermittent overflow on code, tables or
  non-English text that looked short.
- Compacting only at overflow; the symptom is a failed request with no room left to run the summary.
- Compaction that loses state (paths, ids, decisions); the symptom is the agent redoing finished work or
  contradicting an earlier choice right after a summary.
- Exposing every tool definition on every call; the symptom is wrong tool selection and a prefix that
  dwarfs the actual task.
- Rewriting earlier history for context reasons without regard for caching; the symptom is a cache write
  the size of the conversation on every turn.

## When to use
Every system does context engineering, deliberately or by accident. Do it deliberately as soon as any of
these appear: multi-turn agents, retrieval, long documents, more than a handful of tools, or sessions
that persist across days. Measure tokens per turn and per task, cache hit ratio, success rate against
context length, and retrieval precision; a rising token count with flat success is the signal to prune.
In day-to-day work the request builder is the most important file in an LLM product, and it deserves
tests. In interviews, "how would you keep a 200-step agent within budget without losing state" expects
budgets, pruning of tool results, threshold-triggered compaction with a structured state note, and
just-in-time retrieval.

## Role lens
- **Engineer:** Owns the assembler: per-layer budgets, placement order, pruning of consumed tool results,
  compaction triggers, token counting, and tests that the rendered prompt matches the design.
- **Architect:** Decides long context versus retrieval versus memory per workload, where sub-agent
  boundaries fall, and which state must survive compaction as a matter of correctness.
- **FDE:** Fits the customer's manuals, tickets and systems into the budget, chooses chunking and
  retrieval over wholesale loading, and shows how context size moves their cost and accuracy numbers.

## Check yourself
**Q:** A coding agent works well for the first twenty tool calls and degrades after about forty, yet the
context window is never exceeded. Diagnose it and describe the fix.
**A:** The window is not the limit; usable attention and budget are. By step forty the context is
dominated by stale tool results (file contents, test output), the original plan and constraints sit in
the middle where attention is weakest, and older outputs contradict the current state of the files, so
the model acts on stale information and re-reads what it already saw. The fix has four parts: clear or
stub tool results once consumed; keep a structured state note (goal, decisions, files touched, open
items) that is refreshed rather than appended; compact at a threshold while the recent turns stay
verbatim and the raw log stays retrievable; and switch from loading whole files to just-in-time reads of
the regions a step needs, with sub-agents for broad exploration. Measure tokens per step and success
against step count to confirm the curve flattens.

## Quiz
### Q: Why is "load everything that fits" a poor default even when the window is large enough?
- [ ] Large contexts are rejected by most providers above a few thousand tokens
- [x] Cost scales with tokens every turn, and irrelevant material degrades attention and accuracy
- [ ] The model reads only the first few thousand tokens of any request
- [ ] Tool definitions must be the only content in a long context
> Tokens are billed on every re-send and attention is uneven, so extra material costs money and can
> lower accuracy. The window size is a ceiling, not a target.

### Q: Where should the most important instructions and the current question be placed in a long prompt?
- [ ] Both in the middle, surrounded by supporting documents
- [ ] Instructions at the end and the question at the start
- [x] Instructions near the start and the question at the end, with weaker material in the middle
- [ ] Anywhere, because position has no measurable effect on modern models
> Models use the beginning and end of long contexts best. Stable instructions first also cache; the
> question last is what the model attends to immediately before generating.

### Q: When does compaction do more harm than good?
- [ ] Whenever the conversation exceeds ten turns
- [x] When later steps depend on exact earlier details that the summary dropped and cannot be retrieved
- [ ] When the summary is written by the same model that runs the task
- [ ] When tool results are included in the summary
> Compaction is lossy by design. It is safe when decisions and state are preserved and the raw log is
> retrievable; it fails when precision mattered and the detail is simply gone.
