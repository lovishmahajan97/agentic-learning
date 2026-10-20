---
id: context-compaction-summarization
module: agents
name: Context compaction / summarization
kind: pattern
difficulty: core
est_minutes: 14
related:
  - agents/agent-loop
  - agents/agent-memory
  - agents/memory-retrieval-update-deletion-policies
  - model-internals/context-window
  - prompting/prompt-caching
  - prompting/context-engineering
  - rag-retrieval/lost-in-the-middle
  - agent-reliability/state-persistence-and-conversation-recovery
sources:
  - Liu et al., 2023 — Lost in the Middle: How Language Models Use Long Contexts
  - Anthropic, 2025 — Effective context engineering for AI agents
---

## TL;DR
An agent's transcript grows by one action and one observation per step, and every step re-sends all
of it. Left alone it hits the context limit, costs O(n²) tokens over a run, and degrades reasoning well
before the hard limit. Compaction replaces older material with something shorter: pruned tool outputs,
a structured summary, or a pointer to state kept outside the context. The hard part is not shortening;
it is deciding what must survive so the agent keeps solving the task it was given.

## Why it exists
The agent loop is append-only. Each iteration adds the model's reasoning, a tool call and the tool's
result, and the next model call receives the whole transcript again. Tool results dominate: a file read,
a search returning fifty hits, a test log. Illustratively, if a step adds 3,000 tokens, step 40 sends
about 120,000 tokens, and the run as a whole has sent 3,000 × (1 + 2 + … + 40) ≈ 2.5 million tokens,
for work whose *useful* information grew linearly. Cost is quadratic in run length while value is linear.

Three things break before the budget does. First, the hard limit: a 200k-token window ends the run
mid-task, usually at the worst moment, because long runs are the hard ones. Second, quality: models do
not use long contexts uniformly; information buried in the middle of a long prompt is used less reliably
than the same information near the start or end ("lost in the middle"), so the task statement at
position 2 competes with 100k tokens of logs. Third, distraction: stale observations (an error that was
already fixed, a plan that was abandoned) stay visible and keep pulling the model back toward them.

The naive fixes fail in instructive ways. Dropping the oldest messages removes the system prompt, task
statement and constraints, which are the most important tokens in the transcript. Buying a bigger window
moves the wall, keeps the quadratic cost and does nothing for attention dilution. Asking the model to
"be concise" does not control tool output sizes, which are where the tokens are. Compaction is the
harness taking ownership of what the model sees, with an explicit policy for what must be preserved.

## How it works
### Weigh the transcript by class
A transcript has three kinds of content. *Pinned* material (system prompt, task statement, constraints,
tool schemas) is small and must never be dropped. *Working memory* (the last few turns) is what the
model is reasoning about right now and must stay verbatim. *History* (old tool outputs and old
reasoning) is the bulk and is mostly dead once acted on: the model read the file, edited it, and moved
on. Compaction targets history because that is where the tokens are and where the loss is cheapest.

### Layer 1: prune observations
Cap tool outputs at ingestion: return the first and last N lines with a marker and write the full output
to a file the model can grep, because a 12,000-line log is almost never read in full. After the model
has acted on an observation, replace it in the transcript with a short stub ("output of 40 KB pruned;
re-run the tool if needed"). Keep the last K observations intact, because the model may still be reading
them. Pruning is lossless in a practical sense when tools are idempotent: the information is one call
away. This layer alone often doubles the achievable run length, because tool output is typically the
large majority of tokens in a tool-heavy run.

### Layer 2: summarize across a threshold
When the transcript crosses a trigger (a fraction of the window, say 60–80%, leaving headroom for the
summary call's own output and the next step's response), summarize the *oldest span* and keep the
recent turns verbatim: pinned + summary + last K turns. The summary must be structured, because a
free-form "summarize the conversation" reliably drops the specifics an agent needs to avoid redoing
work: exact paths, IDs it created, the error string it saw, the decision it made and why. A schema such
as goal, constraints, decisions-with-reasons, state of the world (files changed, resources created,
commands run and their results), open issues and next step forces those to be present, and lets you
validate the output before accepting it. A cheaper model can write the summary if the schema is strict
and validated; the same model is safer when nuance matters.

### Layer 3: externalize state
Beyond a certain run length, the context should be a cache of durable state, not its only copy. Agents
that write a progress file or a memory store ("done: A, B; next: C; gotchas: …") and are told to read it
back can be compacted aggressively, because the summary only needs to point at the file. This is where
compaction meets agent memory: the summary is short-term memory, the file is long-term.

### Boundaries and the prompt cache
Cut only at message boundaries, and never separate a tool call from its result: APIs reject orphaned
tool results, and a call whose result vanished invites the model to hallucinate one. Compaction rewrites
the prefix of the prompt, so the next call misses the prompt cache entirely; do it rarely and in large
chunks rather than a little every step. Tell the model it happened ("earlier context was compacted;
summary follows"), because otherwise it will refer to things it believes it can still see.

### What must survive
The test is: would forgetting this cause a wrong action? Constraints ("do not touch production"),
decisions already made and their reasons, identifiers of things created, user preferences and partial
results survive. Anything cheaply re-derivable (file contents, search results) can go.

## Example
A compaction step that runs before each model call. It prunes old tool outputs first, because that is
cheap and leaves most of the cached prefix intact, and only summarizes when pruning is not enough. The
summary is JSON and is validated before it replaces the old span.

```python
import json

LIMIT, TRIGGER, KEEP_RECENT = 100_000, 0.7, 6   # window budget, compaction threshold, verbatim tail

SUMMARY_PROMPT = """Summarize the conversation so far for an agent that will continue it.
Return JSON with keys: goal, constraints, decisions (each with its reason),
state (files changed, ids created, commands run and their results), open_issues, next_step.
Be exact with paths, ids and error strings. Never invent."""

def prune_old_outputs(messages, keep_recent):
    for m in messages[:len(messages) - keep_recent]:
        if m["role"] == "tool" and len(m["content"]) > 500:
            m["content"] = f"[tool output of {len(m['content'])} chars pruned; re-run the tool if needed]"
    return messages

def compact(messages, llm, count_tokens):
    if count_tokens(messages) < TRIGGER * LIMIT:
        return messages
    messages = prune_old_outputs(messages, KEEP_RECENT)
    if count_tokens(messages) < TRIGGER * LIMIT:
        return messages                      # pruning was enough; most of the cached prefix survives
    pinned, old, recent = messages[:2], messages[2:-KEEP_RECENT], messages[-KEEP_RECENT:]
    while recent and recent[0]["role"] == "tool":       # never split a tool call from its result
        old.append(recent.pop(0))
    raw = llm(pinned + old + [{"role": "user", "content": SUMMARY_PROMPT}])
    data = json.loads(raw)                               # a broken schema must fail loudly, not silently
    missing = [k for k in ("goal", "state", "next_step") if not data.get(k)]
    assert not missing, f"summary missing {missing}"
    note = {"role": "user", "content": "Earlier context was compacted. Summary:\n" + json.dumps(data, indent=1)}
    return pinned + [note] + recent          # system prompt + task statement stay pinned
```

## Trade-offs
- Compaction is lossy by construction: a bounded, cheaper context in exchange for the guarantee that
  every earlier fact is still visible. It flips when the task depends on exact earlier text (a long diff,
  contract wording); then externalize to a file rather than summarize.
- A summary costs one model call over the whole old span. It pays for itself only if the run continues
  for many more steps; a run that ends two steps after the threshold paid for nothing.
- Rewriting the prefix invalidates the prompt cache. Rare, large compactions keep hit rates high;
  frequent small ones can cost more in cache misses than they save in tokens.
- Pruning tool outputs assumes re-running the tool is cheap and side-effect-free. It flips when the
  tool is a paid API, a slow query, or non-idempotent.
- A cheaper summarizer model lowers cost but drops specifics more often; a strict schema with validation
  recovers most of that, at the price of a retry loop when validation fails.

## Pitfalls
- Summarizing away the task statement or a constraint; the symptom is an agent that confidently
  finishes a slightly different task, or asks what it was supposed to do.
- Summaries that say "created a ticket" without the ID; the symptom is duplicates: a second ticket, a
  second pull request, a second charge.
- Cutting between a tool call and its result; the symptom is API errors about unmatched tool results,
  or a model that invents the missing result.
- Compacting on every step; the symptom is a collapsing cache hit rate, rising cost per step, and a
  summary that drifts because it is a summary of a summary of a summary.
- Not telling the model compaction happened; the symptom is references to "the file above" that no
  longer exists, followed by made-up contents.
- Letting secrets or personal data flow from a tool output into a summary that is then persisted as
  memory; the symptom is credentials in a memory store or a log.

## When to use
Any agent that runs more than a handful of steps needs at least Layer 1, and anything long-running
(coding agents, research agents, multi-hour operations tasks) needs all three. Do not reach for
summarization first: measure where the tokens are (almost always tool outputs) and prune at the source.
Track tokens per step, prompt-cache hit rate, the fraction of runs that hit the window limit, and task
success before and after compaction, because a compaction policy that saves 40% of tokens and loses 10%
of task completions is a net loss. In interviews, being able to derive the quadratic cost of an
uncompacted run and explain why the summary must be structured and cut at tool-call boundaries is the
expected depth.

## Role lens
- **Engineer:** Implements the pruning and summary step in the harness, chooses thresholds, validates
  summaries against a schema, and watches cache hit rate and context-limit failures.
- **Architect:** Decides what state is durable (files, memory store) versus transient (context), and
  sets the policy for what must never be compacted, including secrets and constraints.
- **FDE:** Diagnoses customer runs that "forget" instructions mid-task, and tunes what the summary
  preserves for the customer's domain, such as case IDs or regulatory constraints.

## Check yourself
**Q:** You are building a coding agent that routinely runs 200+ steps against a 200k-token window, with
test logs and file reads as the dominant tool outputs. Design its compaction policy and explain what
must survive each compaction.
**A:** Start at the source: cap tool outputs (head and tail of logs, full output written to a file the
agent can grep), because that removes most tokens without losing anything. After the model acts on an
observation, stub it out, keeping the last five or six intact. Trigger summarization at roughly 70% of
the window, summarizing only the oldest span and keeping the system prompt, task statement and recent
turns verbatim, cut at message boundaries so tool calls and results stay paired. The summary must be
structured: goal and acceptance criteria, decisions with reasons, files changed, commands run with
outcomes, errors seen and fixes, open issues, next step. Persist a progress file so the summary can
point to it. Measure cache hit rate and task success, not just token savings.

## Quiz
### Q: Why should a compaction summary follow a fixed schema rather than being a free-form paragraph?
- [ ] Because JSON is cheaper to tokenize than prose
- [x] Because free-form summaries reliably drop the specific identifiers, paths and decisions the agent needs to avoid redoing work
- [ ] Because the model cannot read its own earlier reasoning in prose form
- [ ] Because the API requires tool results to be JSON
> A schema forces the presence of goal, decisions, created IDs and next steps, and lets the harness
> validate the summary. Prose is not more expensive to tokenize; it is just unreliable about what it keeps.

### Q: What is the main reason not to compact a little on every step?
- [ ] The model becomes confused by frequent summaries
- [ ] Summaries are only valid once per session
- [x] Each compaction rewrites the prompt prefix, so the prompt cache misses and the summary drifts into a summary of summaries
- [ ] Tool results cannot be pruned more than once
> Compaction changes the start of the prompt, which invalidates cached prefixes, and repeated
> re-summarization compounds loss. Rare, larger compactions keep the cache hot and the summary close to
> the original facts.

### Q: Which item is safest to drop during compaction?
- [ ] The constraint "do not modify the production database"
- [ ] The ID of the pull request the agent opened
- [x] The full contents of a source file the agent already edited
- [ ] The user's stated preference for a particular library
> File contents are cheaply re-derivable with a tool call. Constraints, created identifiers and user
> preferences are not, and forgetting them causes wrong actions or duplicate side effects.
