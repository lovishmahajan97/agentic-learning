---
id: multi-agent-systems
module: system-design
name: Multi-agent systems
kind: pattern
difficulty: advanced
est_minutes: 16
status: reviewed
related:
  - agents/multi-agent-patterns
  - agents/task-decomposition
  - agents/a2a-protocol
  - agents/tool-selection-accuracy
  - system-design/agent-orchestration
  - agent-runtime-architecture/policy-enforcement-across-agents-and-tools
  - agent-reliability/audit-logs-execution-traces
  - observability/distributed-tracing-across-services
sources:
  - Wu et al., 2023 — AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation
  - Anthropic, 2024 — Building Effective Agents
  - Anthropic, 2025 — How we built our multi-agent research system
---

## TL;DR
A multi-agent system splits a task across several model-driven loops, each with its own instructions,
tools and context, coordinated by a topology (supervisor–workers, pipeline, parallel fan-out, critic).
It exists because a single agent's context, tool list and privileges do not scale with task breadth.
The price is coordination: duplicated context, compounding errors, harder tracing and higher cost, so a
split must buy isolation, parallelism or specialization you can name.

## Why it exists
A single agent degrades predictably as a task grows. Its context fills with tool definitions,
instructions for every sub-case and the accumulated trace, and quality falls as the useful signal is
diluted. Tool selection gets worse as the tool list gets longer, since the model must pick among many
similar schemas. Privileges accumulate: an agent that both reads untrusted web content and holds write
credentials to internal systems is a prompt-injection path by construction. And it is sequential: one
loop, one step at a time, even when the task has ten independent branches.

The naive scale-up is a bigger prompt and a longer context window. That buys some headroom and then hits
the same walls, now more expensively per step. The opposite naive move, splitting everything into many
agents because it looks modular, is just as common and more damaging: each agent re-reads context the
others already had, each hand-off loses information, each boundary is a place to disagree, and the
system's success rate becomes the product of its parts. Five sequential stages at 95% reliability each
give roughly 0.95⁵ ≈ 77% end to end.

Multi-agent architecture is the deliberate version: split where the split buys something specific.
Isolation of context, so each worker sees only what its sub-task needs. Isolation of privilege, so the
agent reading untrusted input has no write tools. Parallelism, so independent branches run concurrently.
Specialization, so each agent's instructions and evals stay small and sharp. If a proposed split buys
none of those, it is overhead.

## How it works
### Topologies and when each fits
- **Supervisor (orchestrator–workers).** One agent decomposes the task, dispatches sub-tasks to workers
  and synthesizes. Fits when sub-tasks are not known in advance and must be derived from the task. The
  supervisor holds the plan; workers hold the detail.
- **Pipeline.** Agents in sequence, each transforming the previous output (research, draft, review).
  Fits when stages need distinct skills and outputs can be checked between them. Watch the
  compounding-error arithmetic.
- **Parallel fan-out with aggregation.** The same or different agents run on independent partitions
  (one per document, region or hypothesis), then an aggregator merges. Fits breadth-first work; the
  aggregator's context budget is the limit.
- **Evaluator–optimizer (critic).** One agent produces, another critiques against explicit criteria,
  looping until the critic passes or a budget ends. Fits when quality is checkable but hard to get right
  first time.
- **Peer hand-off.** Agents transfer control to one another based on the conversation (triage to a
  billing specialist). Fits conversational routing where each specialist needs its own persona and tools.

### What crosses the boundary
Decide explicitly what a sub-agent receives and returns. Passing the full transcript is the default in
many frameworks and the main source of cost and confusion; passing a typed task (goal, constraints,
relevant artifacts, output schema) is what makes workers cheap and testable. Returns should be
structured and compact *because* the coordinator's context is the scarce resource and raw worker traces
will exhaust it. Large intermediate results go to a shared artifact store and are passed by reference,
*because* copying them through model context costs tokens on both sides and degrades attention.

This compression is the main thing a sub-agent buys. Illustratively, a worker may read 40k tokens of
pages and tool output to answer its sub-question and return a 400-token structured finding; five such
workers let the supervisor reason over 2k tokens of distilled evidence instead of 200k of raw material
that would not fit, or would fit and drown the signal. The price is that the total token bill is the
sum over all agents, which is why multi-agent runs cost several times a single agent's (Anthropic's
write-up of its research system reports on the order of 15× the tokens of a chat interaction).

### Reliability arithmetic: why topology matters
Compounding is about *undetected* errors. In a pipeline, stage 3 receives stage 2's output and usually
cannot tell a fluent wrong answer from a right one, so success is roughly the product p₁·p₂·…·pₙ. A
fan-out whose join needs every worker is also a product (five workers at 0.9 each: 0.9⁵ ≈ 0.59), but
two properties change the arithmetic. Workers are independent, so a failed one can be retried alone:
with one retry each, a worker succeeds with probability 1 − 0.1² = 0.99, and all five with
0.99⁵ ≈ 0.95. And the join can be a quorum (synthesize if at least k of n succeeded). Both only work if
failure is *detectable*, which is why workers return a schema with an explicit "not found" or low
confidence rather than prose, and why pipelines need a validator between stages before any retry
logic helps. (Retries assume independent failures; a worker that fails because the sub-question is
unanswerable will fail again, so cap them.)

### Coordination state
There must be one place that knows which sub-tasks exist, their status and their results: a task table
keyed by run id. The coordinator can be a model (flexible, derives the plan) or code (predictable, fixed
fan-out); mixed is common, where code owns the fan-out and join and a model owns what each worker does.
Termination is decided at two levels *because* a worker can loop forever just as a single agent can,
and the coordinator must also stop when the task is done, over budget, or when further delegation stops
improving the result.

### Privilege and policy across agents
Each agent gets its own tool set and credentials, scoped to its role. A policy layer enforces this
independently of prompts: the web researcher cannot call `send_email` even if a page it read tells it
to. Delegation must not escalate: a worker is never granted more than the coordinator had for this
tenant and user.

### Observability across agents
All agents emit events under one run id with a parent–child span relationship, exactly like distributed
tracing across services. Without this, debugging means reconstructing a conversation among five
participants from five logs; with it, you can see which worker produced the wrong fact and what it was
given.

### Protocols
When agents are built by different teams or vendors, an interoperability protocol (A2A is the
orientation point) standardizes discovery, task hand-off and status. Inside one codebase, a typed
function call is simpler and sufficient.

## Example
A supervisor that fans out independent research questions to workers with a narrow, read-only tool set,
in parallel, with a per-worker budget, and aggregates compact structured results. `agent_run` is the
single-agent loop from the agentic-architecture lesson, `call_model` is a stand-in for your model API,
and `task_table` is the persisted coordination state.

```python
import asyncio, json

READ_ONLY = ["search_docs", "fetch_page"]            # workers never receive write tools
FAILED = {"answer": None, "sources": [], "confidence": 0.0}

async def worker(run_id, subtask, budget_usd=0.10, retries=1):
    for attempt in range(retries + 1):               # independent worker -> retry it alone
        try:
            result = await agent_run(
                run_id=f"{run_id}/{subtask['id']}/{attempt}",   # child span under the parent run
                system="Answer the sub-question using the tools. Return JSON: {answer, sources, confidence}. "
                       "Use answer=null if the tools do not support an answer.",
                task=json.dumps(subtask), tools=READ_ONLY, max_steps=6, budget_usd=budget_usd)
            out = json.loads(result)
            if out.get("answer"):
                return {"id": subtask["id"], **out}
        except (json.JSONDecodeError, TimeoutError):
            pass                                     # malformed or slow: a detectable failure
    return {"id": subtask["id"], **FAILED}

async def supervise(run_id, question, max_workers=5):
    plan = call_model(f"Split into at most {max_workers} independent sub-questions as JSON "
                      f"[{{id, question, why}}]. Question: {question}").json[:max_workers]
    task_table.create(run_id, [p["id"] for p in plan])                  # coordination state, persisted
    results = await asyncio.gather(*(worker(run_id, p) for p in plan))  # parallel fan-out
    for r in results:
        task_table.mark(run_id, r["id"], "done" if r["answer"] else "failed")
    evidence = [r for r in results if r["answer"] and r["confidence"] >= 0.5]
    if len(evidence) < max(1, (len(plan) + 1) // 2):                    # quorum: a majority must succeed
        return {"status": "insufficient_evidence", "results": results}
    synthesis = call_model("Synthesize an answer with citations from these findings only. Name any "
                           "sub-question that has no finding:\n" + json.dumps(evidence)
                           + f"\n\nQuestion: {question}").text
    return {"status": "ok", "answer": synthesis, "sub_results": results}
```

The architectural choices that matter are visible: workers receive a typed sub-task rather than the
supervisor's transcript and return a schema rather than a trace; a worker's failure is detectable
(null answer, bad JSON, timeout) so it can be retried in isolation; and the supervisor applies a
quorum, declining to synthesize when most workers failed instead of producing a confident answer from
thin evidence. For the data-residency question, a plan of three sub-questions (one per market) where one
worker returns null twice yields two findings, which meets the quorum of two, and the synthesis is told
to say that the third market is unanswered rather than fill it in.

## Trade-offs
- Parallel workers cut wall-clock time for breadth-first tasks, but total tokens rise because each worker
  rebuilds context; for narrow sequential tasks one agent is cheaper.
- Narrow per-agent tool sets improve selection accuracy and contain blast radius, at the cost of a
  coordinator that must route correctly; mis-routing becomes the new failure mode.
- Pipelines make each stage independently testable, but errors compound multiplicatively across stages;
  add checks between stages or shorten the chain when end-to-end reliability matters more than modularity.
- A model-driven supervisor adapts its plan to the task, while a code-driven fan-out is predictable and
  cheap; use the model only where the decomposition is genuinely unknown in advance.
- Passing artifacts by reference keeps contexts small but requires a shared store with access control;
  for tiny results inline JSON is simpler.

## Pitfalls
- Splitting into agents along an org chart or for "cleanliness" with no isolation, parallelism or
  specialization gain; the symptom is higher cost and latency than the single-agent baseline with no
  quality gain.
- Forwarding full transcripts between agents; the symptom is worker contexts that are mostly the
  supervisor's history and costs that grow with every added agent.
- Letting a worker that reads untrusted content hold write tools; the symptom is an action nobody asked
  for, traced back to instructions inside a fetched page.
- Synthesizing over failed or low-confidence workers; the symptom is fluent final answers whose
  citations point at sub-results that said "not found".
- No shared run id across agents; the symptom is an incident where five logs disagree about what
  happened and nobody can say which agent introduced the error.

## When to use
Go multi-agent when you can name the gain: independent branches that can run in parallel, a privilege
boundary that must separate reading untrusted input from acting, or a single agent whose tool list or
instruction set has grown past what one context handles well. Do not go multi-agent to look modular, to
fix a weak single agent, or when the task is a short sequential chain. Measure against the single-agent
baseline: task success, total tokens, wall-clock, and per-stage failure attribution. Day to day it shows
up as contract design between agents and as tracing work. In interviews, expect to be asked why not one
agent, how you prevent compounding errors and privilege escalation, and how you would debug a wrong
final answer.

## Role lens
- **Engineer:** Implements typed hand-offs, per-agent tool scopes and budgets, parallel fan-out with a
  join, and parent–child tracing so every worker's steps roll up under one run.
- **Architect:** Decides whether a split is justified, chooses the topology, owns the privilege model
  across agents, and sets the aggregate budget and termination policy.
- **FDE:** Maps customer workflows onto agents with the right data access each, and explains to
  stakeholders why the system refuses to answer when workers fail rather than guessing.

## Check yourself
**Q:** Your single research agent handles 20 tools and answers in about 90 seconds with 80% task
success. A colleague proposes five specialist agents. How would you decide whether that is an
improvement, and what would you watch for?
**A:** First I would name what the split buys. If sub-questions are independent, parallel workers can
cut wall-clock substantially, since latency becomes the slowest worker rather than the sum; if 20 tools are hurting tool selection, five agents with four tools each
may raise accuracy; if one tool reads untrusted web pages, separating it from write tools is a security
gain. Then I would run both designs on the same eval set and compare task success, total tokens, latency
and failure attribution per stage, because the multi-agent version will cost more tokens and introduce
routing and synthesis errors that did not exist before. I would watch for compounding: a pipeline of five
95% stages is roughly 77% end to end, so the topology should be parallel with a join rather than
sequential where possible, because independent workers can be retried alone and joined with a quorum,
provided their failures are detectable through a structured "not found". I would also check that the
baseline's failures are actually the kind the split fixes: if the single agent fails because it lacks
data, five agents will lack it too.

## Quiz
### Q: Which gain does NOT by itself justify splitting one agent into several?
- [ ] Running independent sub-tasks in parallel
- [ ] Separating an agent that reads untrusted content from one holding write credentials
- [x] Making the architecture diagram look more modular
- [ ] Reducing a tool list that has grown past what one context selects from accurately
> Multi-agent systems add coordination cost, duplicated context and compounding errors. The split must
> buy parallelism, privilege isolation or specialization; modularity for its own sake is pure overhead.

### Q: Why should a worker agent receive a typed sub-task instead of the supervisor's full transcript?
- [ ] Workers cannot parse long messages
- [x] It keeps worker context small and focused, makes workers testable in isolation, and stops cost growing with every added agent
- [ ] Transcripts contain secrets that workers must not see
- [ ] Typed tasks are required by agent protocols
> Forwarding transcripts means every worker pays for context it does not need, and the supervisor's
> noise dilutes its instructions. A typed task defines a contract that can be evaluated independently.

### Q: A five-stage agent pipeline has stages that each succeed about 95% of the time. What is the approximate end-to-end success rate, and what does it imply?
- [ ] About 95%, since the stages are independent
- [x] About 77%, so long sequential chains need intermediate checks or fewer stages
- [ ] About 99%, because later stages correct earlier errors
- [ ] About 50%, because errors always double at each hand-off
> Sequential stages multiply: 0.95 to the fifth power is roughly 0.77, because a later stage cannot
> usually detect an earlier stage's fluent error. Compounding is why pipelines need validation between
> stages, and why independent parallel workers, retried alone and joined by quorum, fare better.
