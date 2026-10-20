---
id: multi-agent-patterns
module: agents
name: Multi-agent patterns (orchestrator-worker, handoffs)
kind: pattern
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - agents/task-decomposition
  - agents/planning
  - agents/reflection-self-critique
  - agents/a2a-protocol
  - agents/state-machines
  - agents/workflows-vs-agents
  - agent-reliability/conversation-execution-identifiers
  - observability/distributed-tracing-across-services
sources:
  - Anthropic, 2024 — Building Effective Agents
  - Anthropic, 2025 — How We Built Our Multi-Agent Research System
  - Official docs: OpenAI Agents SDK (handoffs)
---

## TL;DR
Multi-agent systems split work across several model loops, each with its own context, prompt and
tools. **Orchestrator-worker**: one agent plans, delegates subtasks to parallel workers and
synthesises their structured results. **Handoffs**: one active agent at a time, with control passed
to a specialist when the conversation needs its tools. The gain is context isolation and
specialisation; the cost is coordination, multiplied tokens and harder debugging, so a single
well-built agent is the default until it runs out of room.

## Why it exists
A single agent loop has one context window and one set of tools. As a task grows, three things break
at once. The context fills with tool output from earlier steps, crowding out the goal. The tool list
grows until tool selection accuracy falls and the system prompt becomes a manual nobody follows. And
permissions blur: the same loop that reads public docs also holds the credential that can issue
refunds, so one prompt injection reaches everything.

The naive fix is a bigger context and a longer prompt. It postpones the problem and raises cost per
step, because every step re-reads everything. The structural fix is the one every other engineering
discipline uses: separate concerns into units with narrow interfaces. A research worker needs the
search tool and a brief; it does not need the conversation history or the payments API. A billing
specialist needs the invoice tools and the last few turns; it does not need the triage prompt.

Multi-agent patterns are also how you buy parallelism: ten workers researching ten sources finish in
roughly the time of the slowest one plus orchestration, not ten times one. But the write-ups from teams
that built these systems are candid that cost rises steeply (Anthropic's research-system write-up
reports agents using about 4x the tokens of a chat interaction and its multi-agent system about 15x)
and that
coordination failures (duplicated work, conflicting results, lost context at boundaries) are the main
source of bugs. So the pattern exists for tasks whose value justifies that bill and whose structure
tolerates partition.

## How it works
### Orchestrator-worker
1. The **orchestrator** receives the goal, decomposes it into subtasks (dynamically or from a
   template), and dispatches each to a **worker** with a self-contained brief: objective, inputs, tools
   allowed, output schema, and a budget.
2. Workers run their own agent loops in isolated contexts, often in parallel, and return a
   **structured result**, not their transcript. This is the central design choice *because* it keeps
   the orchestrator's context small and makes workers interchangeable: the orchestrator never sees
   search noise, only findings.
3. The orchestrator checks results against the plan, may dispatch follow-up workers (gaps, conflicts),
   and finally **synthesises** the deliverable, optionally through a separate writer or critic.

Why a central orchestrator rather than peer agents chatting? Because someone must own the plan, the
budget and the stopping rule; without one owner, systems loop, duplicate and never finish. The
orchestrator also becomes the natural place for human approval and for the trace root.

Practical rules that the research-system write-ups emphasise: tell workers how much effort a subtask
deserves (number of searches or tool calls), because workers cannot infer scale; give each worker a
distinct, non-overlapping objective, or they converge on the same sources; have searches start with
short, broad queries and then narrow, rather than opening with long, specific queries that return
little; and make the orchestrator robust to a worker failing,
since with parallelism, failures are routine.

### Handoffs
A handoff transfers the conversation from one agent to another. Implementation is almost always a
special tool: the triage agent's tool list includes `transfer_to_billing`; when the model calls it,
the runtime swaps the active agent's system prompt and tool set, and the conversation continues. The
history can be passed whole, filtered (drop tool calls, keep user turns) or summarised, and a handoff
can carry a structured payload (the account id, the reason).

Why one active agent at a time instead of an orchestrator? Because for user-facing conversations the
user talks to *someone*, and a specialist that sees the dialogue directly gives better answers than
one fed a summary by a coordinator. Handoffs keep the mental model simple: a state machine whose
states are agents and whose transitions are tool calls. The cost is that there is no parallelism and
the receiving agent inherits whatever context was passed, so filtering decisions matter.

### Other shapes, briefly
Pipelines (fixed sequence of specialists), evaluator-optimizer pairs (generator plus critic, the
multi-agent form of reflection), hierarchical orchestrators (an orchestrator whose workers are
themselves orchestrators), and debate or group chat (several agents argue, a judge decides). Most
production systems are an orchestrator-worker core with handoffs at the user-facing edge.

### Communication, state and tracing
Agents share information through structured messages and a shared store (a task table, files, a
blackboard), not by reading each other's contexts. Give every run a trace id propagated to each worker
so a failure can be followed across agents. Decide explicitly what each worker may write to shared
state, because concurrent writers produce the hardest bugs. Set a global budget the orchestrator
enforces, since each worker will happily spend its own.

### The cost model
If a single agent spends T tokens, an orchestrator with k workers spends roughly the orchestrator's
own tokens plus k worker runs plus synthesis; it is the parallelism, not the token count, that
improves. Using the published 4x (single agent) and 15x (multi-agent) figures relative to a chat turn,
expect a multi-agent research run to cost several times a single-agent run on the same task. These are
one vendor's research-task numbers, not a constant; measure your own and budget accordingly.

## Example
A minimal handoff runtime. Each agent is a prompt plus a tool list; transfers are tools; the loop
swaps the active agent and continues the same message list. `llm` returns an assistant message with
optional `tool_calls`.

```python
AGENTS = {
    "triage": dict(system="Classify the request and call a transfer tool. Do not solve it yourself.",
                   tools=["transfer_to_billing", "transfer_to_tech"]),
    "billing": dict(system="You handle invoices and refunds. Verify the invoice before refunding.",
                    tools=["lookup_invoice", "issue_refund", "transfer_to_triage"]),
    "tech": dict(system="You debug product issues. Ask for logs before guessing.",
                 tools=["search_docs", "create_ticket", "transfer_to_triage"]),
}

def handoff_history(messages, keep_last=6):
    user_turns = [m for m in messages if m["role"] in ("user", "assistant") and not m.get("tool_calls")]
    return user_turns[-keep_last:]            # specialists do not need triage's tool noise

def run(messages, agent="triage", max_turns=20):
    for _ in range(max_turns):
        spec = AGENTS[agent]
        reply = llm(system=spec["system"], messages=messages,
                    tools=[TOOL_SCHEMAS[t] for t in spec["tools"]])
        messages.append(reply)
        if not reply.get("tool_calls"):
            return messages, agent                  # agent produced a user-facing answer
        for call in reply["tool_calls"]:
            if call["name"].startswith("transfer_to_"):
                agent = call["name"].removeprefix("transfer_to_")
                messages.append(tool_result(call["id"], f"transferred to {agent}"))
                messages = handoff_history(messages) # filtered context crosses the boundary
                log_event(trace_id=TRACE, event="handoff", to=agent)
                break                                # drop the rest of this reply's calls
            else:
                out = TOOLS[call["name"]](**call["arguments"])
                messages.append(tool_result(call["id"], out))
    return messages, agent
```

The two deliberate choices are that a transfer is just a tool call the model can make, and that the
history is filtered at the boundary so the specialist starts with a small, relevant context. The
`break` matters: the filter removes the assistant message that issued the calls, so executing its other
tool calls afterwards would run them under the wrong agent and append results that reference a tool-call
id no longer in the history, which most chat APIs reject.

## Trade-offs
- Context isolation lets each agent stay focused and keeps tool lists short, but every boundary is a
  place where information is lost; when subtasks need most of the same context, a single agent is better.
- Parallel workers cut wall-clock time for research-style tasks, yet total tokens and API concurrency
  rise with the number of workers, so cost per task grows several-fold over a single agent.
- Handoffs keep user-facing conversations coherent with one active specialist, at the cost of no
  parallelism and a history-filtering decision that can drop what the specialist needed.
- Separate agents allow separate credentials and least privilege per role, but the orchestrator now
  holds the power to route work and must be hardened against injected instructions in worker results.

## Pitfalls
- Workers returning transcripts instead of structured findings; the symptom is an orchestrator context
  that bloats after two rounds and synthesis that ignores most of the input.
- Overlapping worker objectives; the symptom is several workers citing the same three sources and
  gaps nobody covered.
- No global budget or stopping owner; the symptom is runs that spawn follow-up workers indefinitely
  and bills that vary tenfold between similar tasks.
- Handing off the entire history including tool noise; the symptom is specialists that answer the
  triage agent's internal notes rather than the user.
- Treating worker output as trusted; the symptom is an orchestrator following instructions that
  arrived inside a worker's web search result.
- Reaching for multi-agent first; the symptom is a fragile system whose single-agent baseline was
  never measured and would have sufficed.

## When to use
Use orchestrator-worker when a task decomposes into parallel, mostly independent pieces whose value
justifies several times the token cost: broad research, large codebase changes, multi-source
reconciliation. Use handoffs when a conversation needs different specialists with different tools and
one user-facing voice: support, sales, onboarding. Stay single-agent when subtasks share most context,
when latency per step matters more than throughput, or when the baseline already works. Measure task
completion, tokens per task against the single-agent baseline, boundary losses (how often the
synthesis lacked something a worker found) and per-agent failure rates. In interviews, the strong
answer explains both patterns, when each flips to the other, and why structured hand-off payloads and
budgets are non-negotiable.

## Role lens
- **Engineer:** Implements the orchestrator loop, worker briefs with output schemas and budgets, the
  handoff tools and history filtering, and trace propagation across agents.
- **Architect:** Decides which roles get separate agents, credentials and models, sets the global
  budget and stopping ownership, and chooses between orchestration and handoffs per use case.
- **FDE:** Maps the customer's teams and systems onto agent roles, defines who approves what at each
  handoff, and proves cost per task against the customer's single-agent expectation.

## Check yourself
**Q:** A support bot built as one agent with fourteen tools picks the wrong tool about a fifth of the
time and sometimes issues refunds while investigating a bug. Which multi-agent pattern would you apply,
and what would you watch for?
**A:** Handoffs. The conversation is user-facing and sequential, so an orchestrator with parallel
workers adds nothing; what is needed is specialists with short tool lists and separate credentials. A
triage agent with only transfer tools routes to a billing agent (invoice lookup, refund) or a tech agent
(docs search, ticket creation). Tool-selection accuracy improves because each agent chooses among a
handful of tools, and the refund tool simply does not exist in the tech agent's context, which closes
the accidental-refund path structurally rather than by instruction. I would pass a filtered history
(user and assistant turns, last few) plus a structured payload with account id and reason, log every
transfer with a trace id, cap transfers per conversation to avoid ping-pong, and measure wrong-tool
rate and transfer loops before and after.

## Quiz
### Q: What is the most important property of a worker's return value in the orchestrator-worker pattern?
- [ ] It should include the worker's full reasoning so the orchestrator can audit it
- [x] It should be a structured result with only the findings, so the orchestrator's context stays small and workers are interchangeable
- [ ] It should be written directly into the final deliverable
- [ ] It should be a natural-language summary without any schema
> The orchestrator synthesises across many workers; transcripts would bloat its context with tool noise.
> Structured findings keep it focused and make it easy to check gaps, conflicts and completeness.

### Q: How is a handoff typically implemented?
- [ ] The orchestrator forks a new process for the specialist agent
- [ ] The specialist agent is called as an MCP tool with the full history as an argument
- [x] As a tool call that swaps the active agent's system prompt and tool set while the conversation continues
- [ ] By asking the user to open a new chat with the specialist
> A transfer tool lets the model decide to hand off; the runtime switches prompt and tools and keeps
> the message list, optionally filtered, so one specialist speaks to the user at a time.

### Q: Which situation argues against splitting a single agent into several?
- [ ] Subtasks can run in parallel on different sources
- [ ] Different steps need different credentials
- [x] The subtasks all need most of the same context to make good decisions
- [ ] The tool list has grown large enough to hurt selection accuracy
> Every agent boundary loses context. When pieces share nearly all their context, partitioning costs
> more than it saves; the other options are precisely the conditions where multi-agent pays off.
