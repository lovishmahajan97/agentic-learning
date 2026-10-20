---
id: planning
module: agents
name: Planning
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - agents/agent-loop
  - agents/task-decomposition
  - agents/reflection-self-critique
  - agents/workflows-vs-agents
  - agents/state-machines
  - prompting/react
  - prompting/chain-of-thought
  - agent-reliability/max-step-budgets
sources:
  - Yao et al., 2022 — ReAct: Synergizing Reasoning and Acting in Language Models
  - Xu et al., 2023 — ReWOO: Decoupling Reasoning from Observations for Efficient Augmented Language Models
  - Yao et al., 2023 — Tree of Thoughts: Deliberate Problem Solving with Large Language Models
  - Wang et al., 2023 — Plan-and-Solve Prompting
---

## TL;DR
Planning is how an agent decides *which* actions to take and in *what order* before, or while,
taking them. A purely reactive loop picks the next step from whatever is in context and drifts on long
tasks; an explicit plan gives the loop a target to measure progress against and a place to replan when
an action fails. The design choice is where planning happens: interleaved with actions, up front, as a
search over alternatives, or hierarchically, and each choice trades tokens for robustness.

## Why it exists
The basic agent loop is greedy: look at the conversation so far, choose one tool call, observe, repeat.
For a three-step task that is fine. For "migrate this service to the new auth library and make the
tests pass", greedy choice fails in predictable ways. The model fixes the first compile error it sees,
then the next, loses track of the original goal as tool output fills the context, repeats a step it
already did because the evidence scrolled out of view, and declares victory when the most recent
output looks good rather than when the goal is met. Every step is locally reasonable and the trajectory
is still wrong, *because* nothing in the loop represents the whole task.

The naive fix, "just use a bigger context window and a stronger model", helps less than expected.
Long contexts dilute attention, each step still conditions on the recent past more than on the goal,
and cost grows with every token re-read per step. What the loop lacks is a compact, explicit
representation of intent: the sub-goals, their order, which are done, and what to do when one fails.
That representation is a plan. It gives the agent something to check progress against, lets a human
review the approach before side effects happen, and provides the hook for replanning rather than
flailing when the world disagrees with the model's expectations.

## How it works
### Four places planning can live
1. **Interleaved (ReAct-style).** The model writes a short reasoning step, then an action, then reads
   the observation, and repeats. Planning is implicit and local. It works *because* each decision uses
   the freshest information, which matters when the environment is unpredictable. It fails on long
   horizons for the reasons above and spends one model call per action.
2. **Plan-then-execute.** A planner call produces an explicit list of steps; an executor (often a
   smaller model, or plain code) runs them; a check after each step decides whether to continue or
   send control back to the planner. The split exists *because* planning benefits from seeing the whole
   problem with a clean context, while execution benefits from narrow focus and cheap calls.
3. **Plan with placeholders (ReWOO-style).** The planner writes every step up front, referencing
   outputs it has not seen yet as variables (`#E1`, `#E2`). A worker runs all tool calls, and a solver
   call combines the evidence. This cuts model calls and tokens sharply *because* observations are never
   fed back through the planner; the price is that the plan cannot adapt to surprising results.
4. **Search over plans (Tree of Thoughts, MCTS-like).** Generate several candidate next steps, score
   them with a value prompt or an external checker, expand the best, backtrack from dead ends. This is
   worth its cost only when a partial state can be evaluated cheaply and reliably, such as puzzles,
   code with tests, or formal constraints. Without an evaluator it degenerates into expensive sampling.
   At the formal extreme, the LLM only translates the goal into a planning language (PDDL) and a
   classical planner finds a provably valid plan, which works when the domain's actions and
   preconditions can be written down exactly, and not otherwise.

**Hierarchical** planning layers these: a top-level planner emits goals, each goal is handled by its
own interleaved loop. It keeps each context small and matches how task decomposition is done.

### What a plan looks like
A plan can be free text ("1. find the config, 2. …") or structured data. Structured plans win in
production *because* code can act on them: a JSON list of steps with `id`, `goal`, `depends_on`,
`done_when`, and `status` gives you dependency ordering, parallel execution of independent steps, a
place to store results, and an easy progress display. The `done_when` field is the most valuable one:
it turns "did I finish?" from a vibe into a check that can be delegated to a tool or a critic.

### Keeping the plan in view
Plans decay if they scroll out of context. Robust loops re-inject the current plan with step statuses
at the top of each turn, or keep it in a file or scratchpad the agent updates and re-reads (the "todo
list" pattern). This works *because* recency and position both affect what the model attends to; putting
the goal and remaining steps where the model will see them counteracts the pull of the latest tool
output.

### Replanning
Replanning triggers should be explicit: a step fails twice, an observation contradicts an assumption
listed in the plan, the step budget is half spent with less than half the plan done, or a human edits
the plan. On trigger, the planner receives the original goal, the plan with statuses, and a summary of
what went wrong, and returns a revised plan. Bounding replans (for example, three per task) prevents
the loop from planning forever instead of acting.

### Cost model
Interleaved planning costs one call per action with the whole history re-read each time. Because the
history grows by one observation per step, total input tokens grow roughly with steps² (prompt caching
lowers the price of re-reading but not the attention dilution). Plan-then-execute costs one large planner call plus cheap executor calls, and
ReWOO drops to a small constant number of model calls. Search multiplies cost by branching factor and
depth. Pick the cheapest scheme that survives your failure cases, then measure.

## Example
A plan-then-execute loop with structured steps, a verification check per step, and bounded replanning.
`llm_json` is any call that returns parsed JSON; `run_step` executes a step with tools and returns the
observation.

```python
MAX_REPLANS, MAX_STEPS = 3, 20

def make_plan(goal, done=(), history=""):
    finished = [s["goal"] for s in done]
    return llm_json(f"""Goal: {goal}
Already completed, do not repeat: {finished or 'none'}
Previous attempts and failures: {history or 'none'}
Return JSON: {{"steps":[{{"id":1,"goal":"...","done_when":"...","depends_on":[]}}]}}
Plan only the remaining work; steps independently verifiable; at most 8.""")["steps"]

def verify(step, observation):
    return llm_json(f'Step goal: {step["goal"]}\nDone when: {step["done_when"]}\n'
                    f'Observation: {observation}\nReturn {{"done": true|false, "why": "..."}}')

def agent(goal):
    plan, history, used = make_plan(goal), [], 0
    for replan in range(MAX_REPLANS + 1):
        for step in plan:
            if step.get("status") == "done":
                continue
            for attempt in (1, 2):             # retry once: one failure may be transient
                if used >= MAX_STEPS:
                    return {"status": "budget_exhausted", "plan": plan}
                obs = run_step(step, context=[s for s in plan if s.get("status") == "done"])
                used += 1
                check = verify(step, obs)
                if check["done"]:
                    step["status"], step["result"] = "done", obs
                    break
                history.append(f'step {step["id"]} try {attempt} failed: {check["why"]}')
            if step.get("status") != "done":
                break                          # failed twice: the plan's premise is suspect
        else:
            return {"status": "complete", "plan": plan}
        if replan == MAX_REPLANS:
            break                          # no replans left; do not pay for an unused plan
        done = [s for s in plan if s.get("status") == "done"]
        plan = done + make_plan(goal, done, "\n".join(history))  # replan only the rest
    return {"status": "gave_up", "plan": plan, "history": history}
```

The deliberate choices: the `done_when` criterion is written at planning time, *because* that is when
the intent is clearest; a step gets one retry before it triggers a replan, *because* a single failure is
often transient while two suggest the plan itself is wrong; and finished steps are kept across replans
*and named to the replanner*, so it plans only the remaining work and nothing verified is redone. Where a
step's `done_when` can be checked by code (a test passes, a row exists), replace the LLM `verify` call
with that check; a model judging its own executor's output is the weakest verifier available.

Trace it on "upgrade the auth library and make the tests pass". The planner returns four steps: bump
the dependency (`done_when`: lockfile shows the new version), fix compile errors (build exits 0), update
the deprecated token call sites (grep finds no old API), run tests (suite green). Steps 1 and 2 pass.
Step 3 fails twice because the "deprecated" call has no direct replacement, which contradicts the
plan's assumption. The replanner sees steps 1 and 2 done plus the failure history and returns two new
steps (add an adapter function, migrate call sites to it) followed by the test step. Total: two planner
calls, about seven executor calls, versus a reactive loop that would likely have kept patching call
sites one compile error at a time.

## Trade-offs
- Up-front plans give reviewability and progress tracking, but they are written with the least
  information; in highly uncertain environments interleaved planning wastes fewer steps on wrong plans.
- ReWOO-style placeholder plans cut model calls to a handful, yet they cannot react to surprising tool
  output; placeholders can pass an unknown result into a later step's arguments, but the moment *which*
  step comes next depends on what a result says, interleaving wins back its cost.
- Search over plans improves hard-problem success substantially, but only with a trustworthy evaluator;
  with a noisy value prompt you pay branching-factor times the cost for little gain.
- Structured JSON plans enable parallelism and validation, at the cost of a stricter planner prompt and
  occasional parse failures that free-text plans never have.

## Pitfalls
- Letting the plan scroll out of context; the symptom is the agent repeating completed steps or
  declaring success after finishing the most recent sub-goal rather than the whole task.
- Steps without a completion criterion; the symptom is an executor that "completes" every step and a
  final result that satisfies none of the goal.
- Replanning on every hiccup; the symptom is a trace full of planner calls and few tool calls, with
  the plan reshuffled each turn.
- Over-planning simple tasks; the symptom is five-step plans for one-tool questions and users who notice
  the latency.
- Planning without knowing the tools; the symptom is plans that call capabilities the agent does not
  have, which only surfaces at execution time.

## When to use
Reach for explicit planning when tasks exceed roughly five tool calls, when humans need to approve
the approach before side effects (deployments, data changes), when steps can run in parallel, or when
you need resumability after a crash. Stay with interleaved ReAct when tasks are short and the
environment is unpredictable, and prefer a fixed workflow when the steps are known in advance. Measure
task completion rate, steps per task against an ideal, replans per task and tokens per task. In daily
work this is the difference between an agent that migrates a codebase and one that fixes the first
error it sees. In interviews, explain the four placements and the cost model, then show you would pick
by failure mode rather than by fashion.

## Role lens
- **Engineer:** Writes the planner and verifier prompts, chooses the plan schema, re-injects plan state
  each turn and bounds replans and steps.
- **Architect:** Decides whether a use case is a fixed workflow, a planned agent, or a search, and sets
  where human approval of a plan is mandatory.
- **FDE:** Tunes plan granularity to the customer's tools and risk appetite, and exposes the plan in
  their UI so operators trust and correct the agent.

## Check yourself
**Q:** An agent that reconciles invoices against purchase orders works on small batches but on large
batches it keeps re-checking invoices it already matched and never finishes. Diagnose and propose a
fix with a cost estimate.
**A:** The trajectory shows classic plan decay: with a long context, the record of which invoices are
matched scrolls out of view and the agent re-derives work from recent tool output. Fix the
representation, not the model. Have a planner produce a structured plan where each invoice (or batch
of invoices) is a step with `done_when: "match stored for invoice X"`, keep the status table in a
scratchpad that is re-injected at the top of each turn, and gate completion on the table, not on the
last message. Independent invoices can run as parallel steps. Cost: one planner call, then one executor
call per invoice with a small context, instead of one growing-context call per action, so the dominant
cost falls from roughly O(n²) tokens to O(n) (the status table still grows with n, but at a few tokens
per invoice, or summarise it as counts plus the next pending ids), and replans are bounded to a few per
batch. Better still, "matched" should be a deterministic check on stored records, not the model's opinion.

## Quiz
### Q: Why does plan-then-execute often beat interleaved ReAct on long tasks?
- [ ] The executor model is always more accurate than the planner model
- [x] The plan keeps the whole goal and step statuses explicit, so progress is measured against intent rather than the latest observation
- [ ] It eliminates the need for tool calls
- [ ] It guarantees the first plan is correct
> Interleaved loops condition mostly on recent output and drift. An explicit plan with completion
> criteria gives the loop a target and a replanning hook; it does not make the first plan correct, which
> is why replanning exists.

### Q: When is a ReWOO-style plan with placeholder variables a poor choice?
- [ ] When tool calls are expensive
- [ ] When the task has many independent steps
- [x] When which steps to run, not just their arguments, depends on what earlier observations turn out to contain
- [ ] When the model supports structured outputs
> Placeholder plans are written before any observation and never revisited by the planner. A variable
> like `#E1` can carry step 1's output into step 3's arguments, but if step 1's result should change
> *what* step 3 is (or whether it runs), the fixed plan cannot adapt. Many independent steps suit ReWOO well.

### Q: Which trigger is a sound reason to replan rather than retry?
- [ ] Any tool returning an error once
- [ ] The user sending any new message, even one that leaves the goal unchanged
- [x] An observation contradicting an assumption the plan was built on, or a step failing repeatedly
- [ ] Every ten steps regardless of progress
> Replanning is for when the plan's premises are wrong. Single transient errors deserve a retry, and
> periodic replanning without a signal burns planner calls while shuffling work that was fine.
