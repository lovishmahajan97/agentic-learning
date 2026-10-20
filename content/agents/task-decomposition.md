---
id: task-decomposition
module: agents
name: Task decomposition
kind: pattern
difficulty: core
est_minutes: 13
status: reviewed
related:
  - agents/planning
  - agents/multi-agent-patterns
  - agents/parallel-tool-calls
  - agents/workflows-vs-agents
  - rag-retrieval/query-decomposition
  - prompting/chain-of-thought
  - agent-reliability/max-step-budgets
  - model-internals/context-window
sources:
  - Zhou et al., 2022 — Least-to-Most Prompting Enables Complex Reasoning in Large Language Models
  - Khot et al., 2022 — Decomposed Prompting: A Modular Approach for Solving Complex Tasks
  - Anthropic, 2024 — Building Effective Agents
---

## TL;DR
Task decomposition splits a large job into subtasks that each fit comfortably in one model call's
attention, can be verified on their own, and expose a small, explicit interface to the rest. Planning
decides the *order* of work; decomposition decides its *shape*: granularity, dependencies and
contracts. Done well it unlocks parallelism, cheaper models per piece and isolated failures. Done badly
it multiplies coordination overhead and loses context at every boundary.

## Why it exists
A single model call has a fixed budget of attention and a fixed context. Ask it to "produce a
competitive analysis of five vendors with pricing, features and a recommendation" in one shot and the
output is shallow where it matters, confidently wrong on details, and impossible to check except by
redoing the work. The model is not failing at any one sub-skill; it is failing because the task asks
it to hold too many threads at once, and quality degrades gracefully enough that nobody notices until a
customer does.

The naive alternatives are a longer prompt or a stronger model. Both raise the ceiling slightly and
leave the structure of the failure untouched: you still get one monolithic answer with no seam at which
to verify, retry or parallelise. Chain-of-thought helps a single reasoning path but does not reduce the
amount of distinct work in context.

Decomposition changes the economics. Five vendor-research subtasks run in parallel with small
contexts; a comparison step reads five structured summaries instead of five raw web dumps; a writing
step receives a comparison table rather than everything that was ever retrieved. Each piece can be
checked, cached, retried or handed to a cheaper model. Least-to-most and decomposed prompting showed
that splitting problems into sub-problems the model can solve reliably beats asking for the whole answer
at once on compositional benchmarks (symbolic manipulation, compositional generalisation, math word
problems). Those papers test short reasoning tasks; applying the same logic to long agent tasks is
standard engineering practice rather than something they measured.

## How it works
### Three dimensions of a decomposition
1. **Granularity.** How big each subtask is. Too coarse and a subtask inherits the monolith's
   problems. Too fine and you pay a model call, a context setup and a coordination step for trivial
   work, and context that should be shared gets re-explained in every call. A useful heuristic: a
   subtask is right-sized when one model call can finish it with a clear success criterion and no more
   than a handful of tool calls.
2. **Dependency structure.** Independent subtasks (fan-out), a chain (each needs the previous), or a
   directed acyclic graph. The structure matters *because* independence is what buys parallelism and
   isolation, while every dependency edge is a place where context must be passed and can be lost.
   Draw the DAG explicitly; if everything depends on everything, the decomposition is wrong.
3. **Interface contract.** What each subtask takes in and returns. Returning structured data (a JSON
   summary with fixed fields) rather than a transcript is the single most effective choice *because*
   downstream steps then receive exactly what they need and the orchestrator's context stays small. The
   contract should also say what "done" means so a verifier can check it.

### Who decomposes, and when
- **Static, at design time.** An engineer writes the subtasks and the graph in code. This is a
  workflow: predictable, testable, cheap. Use it whenever the shape of the task is known in advance.
- **Dynamic, at run time.** A model reads the goal and emits the subtasks. This is what makes an agent
  general, and it is where decomposition quality varies most. Constrain it: a schema for subtasks, a
  maximum count, a requirement that each has a `done_when`, and examples of good splits for this
  domain. Least-to-most prompting is the simplest dynamic form: first ask "what sub-questions must be
  answered first?", then answer them in order, feeding each answer forward.
- **Hybrid.** Fixed top-level phases (research, compare, write) with dynamic decomposition inside one
  phase. Most production systems land here.

### Where to cut
Cut along verification boundaries: if you can test a piece (unit tests pass, a schema validates, a
number reconciles), it is a good subtask. Cut along data boundaries: one subtask per source, document
or entity gives natural parallelism and keeps each context focused. Cut along tool boundaries: a
subtask that needs only the search tool is easier to secure and cheaper than one that needs all tools.
Do not cut along narrative boundaries ("introduction", "body") because those pieces share almost all
their context and will contradict each other.

### Passing context across the seams
Each subtask gets the goal, its own inputs and only the upstream results it depends on. Pass summaries
and structured results, not raw transcripts, *because* the downstream model will otherwise attend to
irrelevant detail and the token bill grows with every edge. Keep a shared, read-only "task brief" that
every subtask sees so that terminology and constraints stay consistent.

### The arithmetic of seams
Three numbers decide whether a decomposition pays. **Reliability compounds along chains:** if each of
*n* sequential subtasks succeeds independently with probability *p*, the chain succeeds with *pⁿ*, so
ten steps at 95% each finish cleanly only about 60% of the time (0.95¹⁰ ≈ 0.60). That is why each
subtask needs a verifier and a retry: a step that is checked and retried once fails only when both
attempts fail, roughly (1 − p)² if failures are independent, which lifts the chain back up. It is also
why a long chain is a worse decomposition than a wide fan-out of the same size. **Latency follows the
critical path:** with unlimited concurrency, wall-clock time is the longest dependency chain, not the
number of subtasks, so flattening a five-deep chain into one fan-out plus a merge can cut latency by
several times while doing the same work. **Cost follows the edges:** total tokens ≈ Σ (task brief +
own inputs + upstream results passed in), so a fat transcript on one edge is paid by every consumer of
that edge. Sketch these three numbers for a proposed graph before building it.

### Recomposition
The final step merges results. It is a subtask like any other, with its own contract: it must resolve
conflicts between pieces (two researchers found different prices), enforce consistency and produce the
deliverable. Reserve the strongest model for recomposition and for dynamic decomposition, since those
are where judgement concentrates.

## Example
A small DAG executor that runs independent subtasks concurrently and passes only declared dependencies
downstream. `call_model` is any async model call returning structured output.

```python
import asyncio

GRAPH = {
    "pricing_A":  {"deps": [], "task": "Find current list pricing for vendor A; return {plans:[...]}"},
    "pricing_B":  {"deps": [], "task": "Find current list pricing for vendor B; return {plans:[...]}"},
    "features_A": {"deps": [], "task": "List vendor A features for SSO, audit, SLA; return {features:{}}"},
    "features_B": {"deps": [], "task": "List vendor B features for SSO, audit, SLA; return {features:{}}"},
    "compare":    {"deps": ["pricing_A", "pricing_B", "features_A", "features_B"],
                   "task": "Build a comparison table; flag conflicts; return {table:[...], conflicts:[...]}"},
    "recommend":  {"deps": ["compare"],
                   "task": "Recommend one vendor for a 200-seat team; cite table rows; return {text:...}"},
}
BRIEF = "Buyer: 200-seat fintech, needs SSO and audit logs, budget 40k/year."

async def run_subtask(name, upstream):
    spec = GRAPH[name]
    inputs = {d: upstream[d] for d in spec["deps"]}        # only declared dependencies cross the seam
    return await call_model(brief=BRIEF, task=spec["task"], inputs=inputs, max_tool_calls=6)

async def execute(graph):
    done = {}
    while len(done) < len(graph):
        ready = [n for n, s in graph.items()
                 if n not in done and all(d in done for d in s["deps"])]
        if not ready:
            raise RuntimeError("cycle or unsatisfiable dependency in graph")
        results = await asyncio.gather(*(run_subtask(n, done) for n in ready))
        done.update(zip(ready, results))                    # one wave of independent work per loop
    return done

final = asyncio.run(execute(GRAPH))
print(final["recommend"]["text"])
```

The four research subtasks run in one wave, `compare` runs when all four finish, and `recommend` sees
only the comparison table, never the raw research. Wave scheduling is the simplest correct executor, but
a ready node waits for the slowest node of the previous wave; on uneven graphs, start each node as soon
as its own dependencies finish (one task per node awaiting its parents) to cut wall-clock time.

## Trade-offs
- Smaller subtasks improve accuracy and verifiability per piece, but every extra subtask adds a call,
  a context setup and a chance to lose shared context; past a point, overhead dominates.
- Dynamic decomposition handles novel goals, yet it is the least reliable step in the system; static
  decomposition is more robust whenever the task shape is known.
- Passing structured summaries across seams keeps contexts small and cheap, at the risk of dropping a
  detail a downstream step needed; when details matter, pass references so steps can fetch raw data.
- Parallel fan-out cuts wall-clock time dramatically, but it multiplies concurrent tool and API usage,
  so rate limits and cost per task rise with the branching factor.

## Pitfalls
- Decomposing along narrative sections instead of data or verification boundaries; the symptom is
  pieces that contradict each other and a merge step that rewrites everything.
- Subtasks without a success criterion; the symptom is every subtask "succeeding" and the final output
  still missing what was asked.
- Passing full transcripts between subtasks; the symptom is token cost growing with the number of
  edges and downstream steps fixating on irrelevant upstream detail.
- Unbounded dynamic decomposition; the symptom is an agent generating forty subtasks for a question
  that needed two, and a budget blown before any work happens.
- Hidden dependencies not declared in the graph; the symptom is intermittent failures when a subtask
  happens to run before the one it silently relied on.
- Long unverified chains; the symptom is end-to-end success far below what any single step's
  success rate suggests, because per-step failures multiply.

## When to use
Decompose when a task has more than a few distinct pieces of work, when parts can be verified
independently, when pieces can run in parallel, or when a single context would exceed what the model
handles well. Do not decompose short tasks, tightly coupled reasoning, or anything where the pieces
need almost all of the same context. Measure per-subtask success, merge conflict rate and total tokens
versus the monolithic baseline. In day-to-day work this is designing the subtask schema and the DAG for
a research or migration agent. In interviews, the strong answer talks about granularity, dependency
structure and interface contracts, and explains where to cut and why.

## Role lens
- **Engineer:** Writes the subtask schema, the DAG executor and the verifiers, and tunes granularity
  by measuring per-subtask success and token cost.
- **Architect:** Decides which phases are static workflow and which are dynamic, and where the seams
  must align with security and data boundaries.
- **FDE:** Maps the customer's real process onto subtasks their domain experts can review individually,
  so trust is built one verifiable piece at a time.

## Check yourself
**Q:** A document-processing agent that extracts fields from 80-page contracts gets the parties and
dates right but misses clauses deep in the document, and a single run costs more than the team
expected. How would you restructure it?
**A:** Decompose along data boundaries. Split the contract into sections or clause-sized chunks and
run one extraction subtask per chunk with a fixed output schema and a `done_when` such as "every
clause type in the taxonomy is marked present or absent". These chunks are independent, so they run in
parallel with small contexts, which fixes both the deep-clause misses (each chunk gets full attention)
and the cost (no step re-reads the whole document). Add a recomposition subtask that merges per-chunk
results, resolves conflicts such as a defined term redefined later, and flags clauses found in two
places. Chunking cuts across one real dependency: clauses refer to definitions and other sections
("as defined in Section 1.4"), so either give every chunk the definitions section as part of the shared
brief, or overlap chunks at boundaries; otherwise a chunk extracts a clause whose meaning it cannot see. Pass the merge step only the structured extractions plus chunk references so it can fetch the
raw text when needed. Measure recall per clause type before and after, and tokens per contract.

## Quiz
### Q: Which boundary is the best place to cut a task into subtasks?
- [ ] Along the sections of the final document, such as introduction and conclusion
- [x] Along verification or data boundaries, where each piece can be checked and needs little shared context
- [ ] Wherever the model's output exceeds a fixed token count
- [ ] At every tool call
> Pieces that can be verified independently and that work on distinct data give parallelism and
> isolation. Narrative sections share almost all context, so they drift apart and must be rewritten on merge.

### Q: Why should subtasks return structured summaries rather than their full transcripts?
- [ ] Transcripts cannot be serialised to JSON
- [ ] Structured output is required by the DAG executor
- [x] Downstream steps receive exactly what they need, keeping contexts small and attention on relevant detail
- [ ] It prevents subtasks from calling tools
> Every dependency edge passes context. Raw transcripts grow token cost per edge and pull downstream
> attention toward irrelevant detail; a contract with fixed fields does neither, with references for raw data.

### Q: When is static (design-time) decomposition preferable to letting the model decompose at run time?
- [ ] When the task is open-ended and the goal changes per request
- [x] When the shape of the task is known in advance and predictability matters more than generality
- [ ] When the model is strong enough to plan reliably
- [ ] Never; dynamic decomposition is always more capable
> Dynamic decomposition is the least reliable step in an agent. If the phases are known, encoding them
> in code gives testability and cost control; keep dynamic splitting for the genuinely open parts.
