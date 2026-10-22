---
id: ai-solution-architecture
module: system-design
name: AI / solution architecture
kind: concept
difficulty: core
est_minutes: 16
status: agentic-verified
agent_relevance: core
related:
  - system-design/reference-architectures
  - system-design/nfrs
  - system-design/trade-off-analysis
  - system-design/c4-diagrams
  - system-design/adrs
  - system-design/rag-architecture
  - system-design/agentic-architecture
  - scoping/ai-use-case-discovery
sources:
  - Sculley et al., 2015 — Hidden Technical Debt in Machine Learning Systems
  - Zaharia et al., 2024 — The Shift from Models to Compound AI Systems (BAIR blog)
---

## TL;DR
An agent is mostly not the model: it is the loop, tools, context assembly, gateway, guardrails, traces
and evals around a probabilistic, expensive, stateless, rented model call. Solution architecture decides
where each piece lives so the agent stays testable, safe and affordable.

## For agent builders
- **Why it matters for agents.** An agent multiplies every weakness of a single call: each step is
  another paid, probabilistic call, each tool result is untrusted input, and each retry re-sends the
  growing transcript. Without layers, the loop becomes a 4,000-token prompt nobody can test.
- **Where it shows up in your stack.** The orchestration layer is your agent framework or hand-written
  loop; the model layer is a gateway such as LiteLLM proxy or Portkey; the control layer is schema
  validation and approval gates on tools; observability is OpenTelemetry GenAI spans and an eval set.
- **What you control.** Which work stays in code (lookups, arithmetic, routing), which tools the agent
  may call and with whose permissions, the step and token budgets per run, and the eval gate a prompt or
  model change must pass.
- **What you can skip.** Enterprise architecture frameworks and heavyweight modelling notation. A C4
  sketch, an NFR table with budgets, a threat model and a few ADRs are enough for an agent system.

## Why it exists
- **The prototype is one function, and its failures are not in the model.** Build a prompt, call the
  API, show the answer: it demos and ships. Then answers vary between runs (regression or noise?), cost
  scales with every user and retry, the prompt swells to 3,000 tokens of rules for lack of retrieval and
  tools, a pasted contract gets an invented clause, and security asks who sees whose documents.
- **Agents add a loop to the same failures.** A provider deprecation breaks the tuned prompt, and in an
  agent it breaks tool-call formatting on step 3 of 8, which is harder to spot than a bad answer.
- **A model breaks three classical assumptions.** Classical components are deterministic, unit-testable
  and cheap per call. A model is specified by data (prompts, examples, retrieved context) rather than
  code, its quality is measurable only statistically over a dataset, and latency and per-call cost are
  first-order design constraints.
- **The model is the small box.** Sculley et al. called the classical version hidden technical debt:
  the model is a small box and plumbing is the rest of the diagram. With rented, general-purpose LLMs
  the ratio is more extreme, because your competitor rents the same model. Differentiation lives in
  context assembly, tools, output constraints and the evaluation loop.
- **"Better prompts will fix it" fails.** A prompt is a configuration surface, not an architecture. It
  cannot give you access control, retrieval freshness, caching, retries, fallbacks, schema guarantees,
  evaluation or cost accounting.

## How it works
### The layers and why each is separate
1. **Experience and integration layer.** UI, public API, chat channels, IDE plugins; owns identity,
   sessions and streaming of agent progress to the client. It never holds a prompt or a model key,
   *because* otherwise every surface couples to one model's behaviour and cannot be changed centrally.
2. **Orchestration layer (the agent loop).** Turns a request into one or more model calls: context
   assembly (retrieval, memory, tool results), prompt rendering, routing, the tool-execution loop with a
   step limit, output parsing. Most application code lives here *because* this is the deterministic
   part you can test.
3. **Model layer.** A portfolio of models behind a gateway: pinned versions, fallbacks, rate limits,
   cost metering, provider credentials. Separate *because* models change on the provider's schedule,
   not yours, and the gateway is the one place to enforce budgets and policy across every agent.
4. **Knowledge and data layer.** Ingestion, chunking, indexes, agent memory stores, attribute lookups,
   each carrying access-control metadata. Separate *because* freshness, permissions and provenance are
   data engineering problems; a prompt cannot know a document was revoked an hour ago.
5. **Control layer.** Input and output guardrails, tool-argument schema validation, policy checks,
   approval gates before consequential actions. Exists *because* model output is untrusted input: a
   retrieved document or tool result can carry an injected instruction, and the agent will sometimes
   follow it.
6. **Evaluation and observability.** Traces of every call and tool step (prompt, context, output,
   tokens, latency, cost), golden datasets, offline eval gates in CI, online metrics, feedback capture.
   Exists *because* quality is statistical: a test case tells you nothing, a dataset tells you a
   distribution.

### Design moves specific to agent systems
- **Push determinism out of the model.** Lookups, arithmetic, routing on known fields, formatting and
  validation belong in code or in tools: cheaper, faster, testable, no hallucination. Reserve the model
  for language understanding, planning and generation.
- **Treat context assembly as the primary data flow.** Quality is bounded by what is in the context
  window. The architecture decides what is retrieved, which tool results are kept, in what order, under
  what token budget and with which permissions; most agent quality bugs originate here.
- **Make budgets non-functional requirements.** A latency budget (say p95 ≤ 3 s per interactive turn,
  split across retrieval, prefill and decode), a token budget and a step limit per run, a cost per
  successful task. These drive model choice, streaming and caching.
- **Use typed output contracts.** Schema-validated tool calls and structured outputs give downstream
  code a real boundary; a parse failure is an error you can count, not silent corruption.
- **Place humans deliberately.** Where a wrong action is expensive (refunds, emails, writes), add review
  or approval. The architecture decides where that risk boundary sits, not the prompt.
- **Keep the feedback loop cheap.** Traces → labelled failures → golden set → eval → change in prompt,
  model, tools or retrieval → release gate. If the loop takes a week, the agent will not improve.

### The two budgets as formulas
Latency and cost are the two NFRs the model call dominates; both decompose into terms the architecture
controls:

```
p95_latency  ≈ t_auth + t_retrieval + t_prefill(input_tokens) + output_tokens / decode_rate
               + t_validation + P(retry) × t_retry
cost_per_successful_task = Σ over calls (in_tokens × price_in + out_tokens × price_out)
                           ÷ fraction of tasks that succeed
```

- **Output length is usually the biggest latency lever, not model choice.** Decode time is linear in
  tokens generated, so an answer-length cap, or streaming (which moves perceived cost to
  time-to-first-token), beats shaving milliseconds off retrieval.
- **Trap 1: input tokens multiply.** They are paid again on every call in an agent loop or retry, so an
  agent's sum runs over every step, each with a longer transcript.
- **Trap 2: the denominator.** A cheaper model with a lower success rate can cost *more* per useful
  outcome once retries, escalations and human rework are counted.

### Solution architecture versus AI architecture
| Half | Covers |
|---|---|
| *Solution* (enterprise integration) | single sign-on, systems of record the context and tools come from, deployment constraints such as VPC or data residency, compliance evidence, operations and support boundaries |
| *AI* (model-facing decisions) | retrieval versus fine-tuning, model tiers, agent versus workflow, tool permissions, guardrails |

A working agent needs both; the FDE usually lives exactly at their seam.

### Artefacts a reviewer expects
- **C4 diagrams** for structure.
- **ADRs** for hard-to-reverse decisions: model family, RAG versus fine-tune, agentic versus workflow.
- **An NFR table** with the budgets, including step limits for agent runs.
- **A data-flow diagram** showing every path by which data reaches a model or a tool and where it is
  stored.
- **A threat model** covering prompt injection through tool results and cross-tenant leakage.
- **A cost model** tied to expected traffic and average steps per run.

## Diagram
Follow one agent request through the six layers: only the gateway talks to models, retrieval enforces
permissions before context is built, tool calls pass the control layer, and every step feeds the trace
and eval loop.

```mermaid
flowchart TD
    U["User via UI or API"] --> E["Experience layer"]
    E -->|"identity and session"| O["Orchestration: agent loop"]
    O -->|"query with caller entitlements"| K[("ACL-aware knowledge layer")]
    K -->|"permitted chunks only"| O
    O -->|"rendered prompt"| G["Model gateway"]
    G -->|"pinned version or fallback"| M["Model portfolio"]
    M -->|"answer or tool call"| C["Control layer"]
    C -->|"validated tool call"| O
    C -->|"valid answer, streamed"| E
    C -.->|"parse failure: retry once, then fail closed"| O
    O -.->|"trace: steps, tokens, cost"| T[("Traces and golden set")]
    T -.->|"eval gate on every change"| O
```

## Examples
### Worked scenario: claims-handling agent for an insurer
All figures illustrative. Requirements: answer adjusters' questions over policy documents and the claim
file, draft customer letters, never expose another customer's data, p95 under 4 s. Load: 2,000
adjusters at about 20 requests each per day, roughly 40k requests per day.

**Request path (one Q&A turn):**
1. SSO token → orchestration service.
2. ACL-aware retrieval scoped to the claim and the adjuster's entitlements: top 8 chunks ≈ 2,500 tokens.
3. Prompt = 600-token system prompt + context + 100-token question ≈ 3,200 input tokens.
4. Gateway routes Q&A to a mid-tier model and letter drafting to a frontier model.
5. Output parsed into `{answer, citations[]}`; a citation checker verifies every citation points at a
   retrieved chunk.
6. Output guardrail scans for identifiers belonging to other claims.
7. Streamed to the UI; trace written with tokens, latency and cost.

**Latency budget:**

| Term | Value |
|---|---|
| Retrieval | 300 ms |
| Prefill | ≈ 500 ms |
| Decode, 300-token answer at ≈ 50 tokens/s | 6 s, which blows the 4 s target |
| Fix 1: stream | user perceives time to first token (≈ 1 s) |
| Fix 2: cap Q&A answers at 200 tokens | with a "show more" follow-up; decode falls to 4 s, so the full answer (≈ 4.8 s) still leans on streaming |

**Cost and where to optimise:**
- **Volume.** 40k × 3.2k ≈ 128M input tokens per day, plus 40k × 200 ≈ 8M output tokens.
- **Tiering.** That volume justifies the mid-tier model for Q&A: at an illustrative 10× price ratio
  between tiers, sending Q&A to the frontier model multiplies the daily bill by ten for answers the eval
  shows are no better.
- **Input breakdown.** Caching the fixed 600-token prefix touches only about 19% of input. Retrieved
  context is 2,500 tokens, about 78%, so cutting top-k from 8 to 5 chunks (≈ 940 fewer tokens, about 29%
  of input) is the larger saving, *if* the eval shows citation recall does not drop.
- **Evaluation.** 150 real adjuster questions with reviewed answers, faithfulness measured by citation
  match, run on every prompt or model change and weekly in production.

### Budget calculator: one call versus a 4-step agent run
Runnable Python; prices are illustrative placeholders, not any provider's list price. The agent case
appends ~550 tokens of tool call and result per step and spends ~0.4 s per tool.

```python
def p95_latency_s(t_auth, t_retrieval, t_prefill, output_tokens, decode_rate,
                  t_validation=0.0, p_retry=0.0, t_retry=0.0):
    return (t_auth + t_retrieval + t_prefill + output_tokens / decode_rate
            + t_validation + p_retry * t_retry)

def cost_per_successful_task(calls, price_in, price_out, success_rate):
    """calls: list of (in_tokens, out_tokens); prices in $ per token."""
    spend = sum(i * price_in + o * price_out for i, o in calls)
    return spend / success_rate

print(p95_latency_s(0.0, 0.3, 0.5, 300, 50))   # 6.8 s: blows the 4 s target
print(p95_latency_s(0.0, 0.3, 0.5, 200, 50))   # 4.8 s total, but streamed TTFT is ~1 s

M = 1e-6                                       # $ per million tokens -> $ per token
mid = cost_per_successful_task([(3200, 200)], 0.5 * M, 2 * M, 0.9)
cheap = cost_per_successful_task([(3200, 200)], 0.15 * M, 0.6 * M, 0.25)
print(f"mid ${mid:.5f}  cheap ${cheap:.5f} per successful task")
# mid $0.00222  cheap $0.00240: the model with a 70% lower price costs more per useful outcome

agent_calls = [(3200 + step * 550, 150) for step in range(4)]   # transcript grows each step
agent = cost_per_successful_task(agent_calls, 0.5 * M, 2 * M, 0.85)
agent_latency = sum(0.5 + 150 / 50 + 0.4 for _ in agent_calls)  # prefill + decode + tool
print(f"agent ${agent:.5f} per success, {agent_latency:.1f} s end to end")
# agent $0.01088 per success (~5x one call), 15.6 s: needs progress streaming or async
```

| Shape | Input tokens | Cost per success | End-to-end time |
|---|---|---|---|
| One Q&A call | 3,200 | ~$0.0022 | 4.8 s (streamed) |
| 4-step agent run | 16,100 | ~$0.0109 | ~15.6 s |

The architecture answer is not "use a faster model": stream step progress to the UI, run independent
tool calls in parallel, cap steps, and keep the transcript append-only so prefix caching applies.

## Production patterns
- **Gateway with pinned versions and fallback.** When more than one agent or surface calls a model.
  Route a logical model name through a gateway (for example LiteLLM proxy or Portkey) to a dated, pinned
  model version with a configured fallback list. Monitor fallback rate, error rate, tool-call parse
  rate and p95 per provider.
- **Eval gate in CI.** On every prompt, tool, model or retrieval change. Run the golden set of agent
  tasks in the pipeline and fail the build when faithfulness or task success drops below the agreed bar.
  Monitor the eval score trend and the weekly production re-run.
- **Trace every step with cost attribution.** From day one in production. Emit one span per model call
  and per tool call with prompt and context references, input and output tokens, latency, cost and
  tenant, using OpenTelemetry's GenAI attributes where your stack supports them. Monitor cost per
  successful task, steps per run and token growth per route.
- **Typed tool calls with a counted failure path.** Whenever code consumes model output. Validate tool
  arguments and final answers against a schema (for example a Pydantic model), retry once on parse
  failure, then fail closed. Monitor parse failure rate per model version; a jump usually signals a
  silent model change.
- **Permissions enforced at retrieval and tool execution, not in the prompt.** For any multi-user
  corpus. Pass the caller's entitlements as a metadata filter in the index query and run tools with the
  caller's credentials, so unauthorised data never reaches the context. Monitor with cross-tenant canary
  queries that must return nothing.
- **Stream, cap output and cap steps per route.** For interactive agents. Set a maximum output-token
  limit and a maximum step count per route, and stream tokens and step progress to the UI. Monitor time
  to first token, total p95 and the share of runs hitting the step cap.

## Trade-offs
- **Layering vs latency and operations.** Layers give testability and swappable models, but each hop
  adds latency and another service to run, and an agent pays the hop on every step. A single internal
  agent with one model is better served by a modular monolith that keeps the same boundaries as modules
  rather than services.
- **Logic in code vs iteration speed.** Code gives determinism and tests but iterates more slowly. While
  the problem is still being discovered a prompt-heavy agent prototype is right; the architecture
  hardens into tools and code as behaviour stabilises.
- **Guardrails vs friction.** Guardrails and approval gates reduce risk but add latency and user
  friction. The flip point is the cost of a wrong action: low-stakes drafting needs far fewer gates than
  an agent that moves money.
- **Full tracing vs data exposure.** Tracing prompts, tool results and outputs makes evaluation possible
  but creates a store of sensitive data, so retention, redaction and access policies are designed with
  it, not after. The flip is data class: for regulated data, keep redacted traces by default and a small
  sampled full-fidelity set under tighter access, accepting slower debugging for a smaller breach surface.

## Pitfalls
- **Prompt-as-architecture.** A 4,000-token system prompt holding business rules, data and formatting.
  Symptom: every edit breaks something else and nobody can say what the agent does. Fix: move rules
  to code and tools, and data to retrieval.
- **No evaluation set before launch.** Symptom: whether a new model or prompt is better is argued from
  anecdotes, and users discover regressions. Fix: a golden set of agent tasks and a release gate.
- **Trusting model output.** Feeding tool arguments to SQL, a shell, or an email-send without
  validation. Symptom: a retrieved document containing an instruction causes an unintended action. Fix:
  validate, run tools with least privilege, and gate consequential actions.
- **Designing for average latency.** Symptom: the demo is snappy; production p95 is 15 s because of
  long contexts, extra agent steps, retries and queueing on the provider. Fix: budget p95 per layer and
  per step.
- **Forgetting the model is rented.** No version pinning, no fallback. Symptom: a provider deprecation
  or regional outage becomes a product incident with no runbook. Fix: pin dated versions in the
  gateway, configure a fallback, and write the runbook before launch.
- **No cost model.** Symptom: usage grows, the bill grows faster because steps per run creep up, and
  the feature is throttled or quietly removed. Fix: a cost model tied to expected traffic, tracked as
  cost per successful task.

## When to use
This is less a pattern you choose than the way to think about any system with a model in it, applied
at the right weight.
- **First question:** is a model needed at all, and is an agent needed? If a rule or a lookup solves it,
  that is the better architecture; if the steps are fixed, a workflow beats an agent.
- **Prototype:** sketch the layers and implement them as modules in one process.
- **Moving to production, add in this order:** tracing; an evaluation set and gate; a gateway with
  pinned versions and fallback; retrieval with access control; output contracts and tool schemas; then
  guardrails sized to the risk.
- **Measure:** p95 latency per layer, steps per run, cost per successful task, eval score, and failure
  rate by category.
- **Interviews:** "design a document assistant for X" expects the layers above, explicit budgets, the
  eval loop and the trust boundary around model output.

## Role lens
- **Engineer:** Builds the orchestration layer: context assembly, schema-validated outputs, tracing,
  and a single model interface so the model can be swapped without touching business logic.
- **Architect:** Owns the layer boundaries, the NFR budgets for latency, cost and quality, the
  RAG-versus-fine-tune and agent-versus-workflow decisions as ADRs, and the evaluation gate.
- **FDE:** Maps the reference shape onto the customer's identity provider, data sources and deployment
  constraints, and explains to stakeholders why the model call is the smallest box on the diagram.

## Check yourself
**Q:** A team has a working prototype: a web form sends the user's question plus a pasted document to
a frontier model and shows the answer. They want to ship it to 5,000 enterprise users next quarter.
What do you change in the architecture, and in what order, and why that order?
**A:** First move the model call behind an orchestration service and a gateway so the UI holds no
prompts or keys, pin the model version and add a fallback; that is the cheapest change and removes the
single biggest operational risk. Second, build the evaluation loop before touching quality: capture
traces, collect one to two hundred real questions with reviewed answers, define the bar. Without it
every later change is a guess. Third, replace pasted documents with ingestion and ACL-aware retrieval,
which bounds context size, enforces permissions and enables caching, fixing cost and the data-leakage
exposure at once. Fourth, add typed outputs with citations and input and output guardrails, with human
review wherever an answer drives a decision. Then set budgets, p95 with streaming and cost per task,
and choose the cheapest model that clears the eval bar. Record each decision as an ADR.

## Interview questions
### What makes architecting around an LLM different from architecting around a normal service dependency?
- **It breaks three classical assumptions.** A model is specified by data rather than code, its quality
  is measurable only statistically over a dataset, and latency and per-call cost are first-order
  constraints.
- **So the design surrounds it with deterministic code.** Lookups, arithmetic, routing and validation
  move into code and tools, and an evaluation set replaces unit tests as the measure of quality.
- **And it is rented.** Versions change on the provider's schedule, so pinning and fallback live in a
  gateway rather than in every caller.

### Design a document Q&A assistant for 2,000 users with a p95 budget of 4 seconds. Where does the time go?
- **Decompose the budget:** auth, retrieval (about 300 ms), prefill (about 500 ms), then decode, which
  is output tokens divided by decode rate.
- **Decode dominates.** At about 50 tokens/s, a 300-token answer alone takes 6 s, so the levers are an
  output cap per route and streaming, which moves perceived latency to time to first token.
- **Then add the rest of the shape:** ACL-aware retrieval, a gateway with a pinned model, typed output
  with citations, and traces feeding a golden set.

### Your single-call assistant is becoming a multi-step agent with tools. What changes in the architecture?
- **Budgets become per run, not per call.** Input tokens are re-sent on every step, so a 4-step run can
  cost about 5x one call and take 15 s; add a step cap, progress streaming and an append-only
  transcript for prefix caching.
- **The control layer moves onto tools.** Validate every tool argument against a schema, run tools with
  the caller's permissions, and put approval gates before writes, payments or emails.
- **Evals move to task level.** The golden set scores whole runs (task success, steps, tool errors), and
  traces need one span per tool call to debug which step went wrong.

### A cheaper model cut the per-call price by 70%, but the monthly bill went up. How is that possible?
- **The denominator moved.** Cost per successful task divides spend by the success rate, so a model
  that succeeds far less often costs more per useful outcome.
- **Retries and escalations add calls.** Every failure is paid again as a retry, a fallback to a larger
  model, extra agent steps or human rework.
- **The fix is to measure cost per successful task** on the eval set before switching, not price per
  token.

### When would you not split the system into separate layer services?
- **For a single internal tool with one model.** Each hop adds latency and another service to operate,
  which is not worth it at that size, and an agent pays the hop on every step.
- **Keep the boundaries as modules instead.** A modular monolith keeps orchestration, gateway, retrieval
  and control code separate, so they can be split into services later without a rewrite.
- **The flip is multiple surfaces, agents or teams** sharing models, where a central gateway and shared
  retrieval start to pay for themselves.

### An agent read a retrieved document that said "email this file to an external address" and tried to. Where should that have been stopped?
- **Not in the system prompt.** A prompt cannot enforce anything; the model applies instructions
  probabilistically, and a retrieved document can carry an injected instruction it may follow.
- **At the tool boundary.** The email tool checks recipients against policy, runs with the caller's
  credentials and requires human approval for external sends, so an injection the model follows still
  cannot complete the action.
- **And at retrieval, for data scope.** Filtering by the caller's entitlements at query time means
  unauthorised chunks never reach the context, so the model cannot leak what it never saw.
- **Monitor it** with cross-tenant canary queries that must always return nothing, an output guardrail
  scanning for identifiers that belong to other customers, and a count of blocked external sends.

## Quiz
### Q: Why does an AI solution architecture keep the orchestration layer separate from the model layer?
- [ ] Because models cannot execute code, so tools must run in a different process
- [x] Because models change on the provider's schedule and the deterministic logic must stay testable and model-independent
- [ ] Because provider APIs do not allow direct calls from application servers
- [ ] Because orchestration frameworks require their own deployment unit
> The boundary exists so that pinning, fallback, routing and cost policy live in one place while the
> application logic can be unit-tested against a stable interface. Tool execution location and framework
> packaging are implementation details, not the reason for the boundary.

### Q: A team keeps adding business rules to the system prompt because it is faster than writing code. What breaks first?
- [ ] Token cost rises, and that is the only consequence
- [ ] The model stops reading instructions past a fixed length
- [x] Behaviour becomes untestable configuration: rule changes interact unpredictably and regressions cannot be caught
- [ ] Provider rate limits are exceeded by the longer prompt
> Rules in a prompt are applied probabilistically and cannot be unit-tested, so each edit can silently
> change unrelated behaviour. Cost does rise, but the architectural failure is losing the ability to
> reason about and verify what the system does.

### Q: Which component most determines whether an AI system improves after launch?
- [ ] A larger model behind the same prompt
- [ ] A vector database with more documents
- [x] Tracing plus a golden evaluation set wired into the release process
- [ ] A streaming interface that lowers perceived latency
> Improvement requires seeing failures, turning them into labelled examples, and measuring each change
> against them before release. A bigger model or more data may raise quality once, but without the loop
> nobody can tell whether the next change helped or hurt.

## Use cases & projects
### Use cases
- **Turning an agent prototype into a product.** The layer map shows which pieces the demo skipped
  (gateway, control layer, traces, eval gate) before the first customer finds them.
- **Design reviews for a new agent feature.** A C4 sketch, an NFR table with step and token budgets and
  a threat model give reviewers one page to challenge instead of a prompt file.
- **Moving deterministic work out of the loop.** Lookups, arithmetic and routing move into code or
  tools, which shortens transcripts and makes failures reproducible.
- **Preparing for a model or provider change.** A gateway seam plus an eval gate turns a deprecation
  into a measured swap rather than an outage on step 3 of 8.

### Mini project
**Layer a toy agent (2-3 hours).**
- **Goal:** refactor a single-function agent into orchestration, model gateway, control layer and
  observability layers, and prove each layer is separately testable.
- **Steps:** start from a loop with two tools and one hard-coded provider call; put the model call
  behind a small gateway interface; validate every tool argument against a schema and add an approval
  gate on the one write tool; emit a span per step with tokens and latency; write 10 golden tasks.
- **Done when:** you can swap the model by changing one config value, the 10 tasks run as a test with a
  pass rate, a malformed tool argument is rejected before execution, and a one-page C4 sketch plus an
  NFR table sit in the repo.
