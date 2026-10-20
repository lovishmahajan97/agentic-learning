---
id: reference-architectures
module: system-design
name: Reference architectures
kind: pattern
difficulty: intro
est_minutes: 14
status: fact-checked
related:
  - system-design/ai-solution-architecture
  - system-design/rag-architecture
  - system-design/agentic-architecture
  - system-design/workflow-orchestration
  - system-design/ai-gateway
  - system-design/c4-diagrams
  - system-design/adrs
  - agents/workflows-vs-agents
sources:
  - Lewis et al., 2020 — Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks
  - Anthropic, 2024 — Building Effective Agents
---

## TL;DR
A reference architecture is a validated, reusable blueprint for a recurring system shape: its
components, their responsibilities, the interfaces between them, the default non-functional
requirements, and the known failure modes. In AI systems a handful of shapes recur constantly (RAG
assistant, extraction pipeline, tool-using agent, multi-step workflow, platform gateway). Starting from
the matching blueprint means you deviate deliberately instead of rediscovering the same missing pieces
after launch.

## Why it exists
Every team building its first document assistant draws roughly the same diagram, and leaves out roughly
the same boxes: evaluation, tracing, access control on retrieval, a gateway with version pinning, output
validation. They find out which boxes were missing in production, one incident at a time. The second
team in the same company does it again, with different names for the same components, so the two
systems cannot share a review checklist, a monitoring dashboard or an engineer.

A reference architecture solves three problems at once. It is a **memory**: the accumulated knowledge of
which components a given shape needs and why, including the ones that are invisible in a demo. It is a
**vocabulary**: when a reviewer says "where is your control layer" everyone knows what is meant and can
point at the diagram. And it is a **baseline for deviation**: a team that omits the reranker or the
approval gate has to say why, which is exactly the conversation an architecture review should be.

The naive alternative is to let each team design from first principles. It is slower, and worse, it
is inconsistent in the places that matter most for risk, because the omitted parts are precisely the
ones that only hurt later (evals, guardrails, cost metering). The other naive alternative, a single
mandated architecture for everything, fails because a batch extraction job and an interactive agent
have different latency, state and risk profiles; forcing one shape onto both produces a bad version
of each. Reference architectures sit between these: a small catalogue, each matched to a problem shape.

## How it works
### What a reference architecture contains
A useful one is more than a diagram. It states the **problem shape** it fits (interaction pattern,
latency class, risk class), the **components** with one-line responsibilities, the **data flows**
including what reaches the model and what is stored, **default NFRs** (typical latency budget, cost
driver, availability target), the **variation points** where legitimate choices exist, and the
**anti-patterns** seen in the wild. It should fit on a page and map directly onto a C4 container
diagram; if it needs twenty pages it is a design document, not a reference.

### The recurring shapes in AI systems
1. **Retrieval-augmented assistant.** Ingestion pipeline → chunk and embed → index with metadata and
   ACLs → query path: rewrite, retrieve, rerank, assemble context → model → cite and validate → UI.
   Latency class interactive, risk is wrong or leaked information. Variation points: hybrid versus
   dense retrieval, reranker or not, long context versus retrieval. The deep treatment is
   `rag-architecture`; the point here is that every one of these boxes exists for a reason that
   surfaces only when it is missing.
2. **Extraction and classification pipeline.** Queue or batch scheduler → document parsing → model with
   a strict output schema → validation and confidence scoring → human review queue for low-confidence
   items → system of record. Latency class batch, risk is silent data corruption. Variation points:
   small fine-tuned model versus general model, sampling rate for review. This shape is where small
   models and distillation pay off most.
3. **Tool-using agent or copilot.** Orchestrator running a loop: plan, call tools through typed
   contracts, observe, repeat, with step budgets, approval gates before consequential actions, and a
   sandbox for execution. Latency class seconds to minutes, risk is an unintended action. Variation
   points: single agent versus multi-agent, how much autonomy per action class. Deep treatment in
   `agentic-architecture`; the Anthropic guidance on building effective agents is the standard
   argument for starting with the simplest shape that works.
4. **Multi-step workflow.** A fixed DAG where some nodes are model calls and others are code; each node
   has a contract and a test. Latency class depends on the longest path, risk is low because the
   control flow is deterministic. This is the default when the steps are known in advance;
   `workflow-orchestration` covers the engine choices. The agent shape is justified only when the
   steps cannot be enumerated up front.
5. **Conversational assistant with memory.** The RAG assistant plus session state, memory retrieval
   and update policies, context compaction for long sessions, and streaming. The new risk is memory
   poisoning and stale personalisation; see `memory-architecture`.
6. **Platform layer.** A gateway in front of every model: authentication, routing, version pinning,
   rate limits, caching, cost attribution, tracing, central guardrails. It is the shape that makes the
   other five governable at company scale; see `ai-gateway`.

### Cross-cutting components every shape shares
Evaluation harness and golden set, tracing with cost per call, a prompt and model registry, and a
control layer. They are drawn once in each reference so that no team can claim they were optional.

### How to apply one
Match the problem to a shape by its interaction pattern (batch, request-response, long-running) and
its risk class (information versus action). Start from the blueprint, then record each deviation as an
ADR with the reason: "no reranker because the corpus is 400 documents and recall is already above the
bar" is a fine decision; silence is not. Use the component list as the review checklist and the
variation points as the agenda for the design discussion. Revisit the catalogue when a system fails in
a way the blueprint did not anticipate, and fold the lesson back in; a reference architecture that is
never updated becomes folklore.

## Example
A logistics company asks for "AI to handle inbound supplier emails". Discovery shows two distinct
jobs: (a) classify each email (invoice, delay notice, dispute, other) and extract fields such as PO
number and new delivery date into the ERP; (b) let a coordinator ask questions like "which suppliers
have slipped twice this quarter" over the resulting data plus contracts.

Job (a) is shape 2, the extraction pipeline. Components: mail connector → queue → parser for PDF
attachments → model call with a JSON schema of about ten fields → validation (PO number must exist in
the ERP, dates must be in the future) → confidence from a second cheap check → anything below the
threshold goes to a review queue; everything else writes to the ERP with provenance. Default NFRs:
processing within 15 minutes, zero silent writes of invalid data, cost dominated by attachment pages.
Variation point taken: start with a general model and collect labels from the review queue, then
evaluate a small fine-tuned model once a few thousand reviewed examples exist.

Job (b) is shape 1 plus a structured-data tool, not an agent: the questions are answerable by a
retrieval step over contracts and a parameterised SQL query over the extracted table, so a two-node
workflow (route, then answer with citations) is enough. The deviation recorded in the ADR is "no
free-form SQL generation; queries are templates with parameters" because the risk class is read-only
information, but the coordinators are not able to check generated SQL. Both jobs sit behind the same
gateway, share the tracing stack, and each has its own golden set: 300 labelled emails for (a), 80
question-answer pairs for (b).

## Trade-offs
- A shared blueprint speeds design and review, but it biases teams toward the known shape; a genuinely
  new problem may need a new reference, and forcing it into an old one hides the novelty.
- Rich references with many mandatory components raise the quality floor, but they also raise the
  cost of a prototype; keep a lighter "exploration" variant and insist on the full shape only at the
  production gate.
- Standardising on the platform gateway shape centralises governance and cost control, but it creates
  a shared dependency whose outage or latency affects every product; it flips in favour of per-team
  gateways only when teams have truly independent risk and release cadences.
- Catalogues that are owned centrally stay consistent but go stale; ones owned by whoever last had an
  incident stay current but drift apart. A small owning group with a lightweight change process is
  the usual compromise.

## Pitfalls
- Picking the agent shape for a problem whose steps are known in advance. Symptom: unpredictable
  latency and cost, and debugging sessions that end in "the model decided to".
- Copying the diagram and dropping the cross-cutting boxes because "we will add evals later".
  Symptom: the first model upgrade is a coin flip and nobody can say whether quality moved.
- Treating the reference as mandatory in every detail. Symptom: a 400-document corpus gets a
  reranker, a query rewriter and a multi-hop planner, and the simplest question takes eight seconds.
- Letting the reference describe a vendor's product rather than a shape. Symptom: switching a
  component requires rewriting the architecture document, and the exit cost is discovered at renewal.
- Never recording deviations. Symptom: six months later nobody knows whether the missing approval
  gate was a decision or an oversight.

## When to use
Reach for the catalogue at the start of any design, during an architecture review, and when onboarding
an engineer to an existing system; the first question is always "which shape is this, and where does
it deviate". Do not use a reference as a substitute for discovery: the shape follows from the
interaction pattern and risk class, which only requirements work can establish. Measure the catalogue
by how often reviews find missing cross-cutting components (should trend to zero) and how long design
takes for a standard shape. In interviews, naming the shape, its components and its default risks in
the first two minutes of a system-design question is what separates practised candidates from those
who start by drawing a box labelled "LLM".

## Role lens
- **Engineer:** Uses the blueprint as a build checklist and knows which components can be stubbed in a
  prototype and which must exist before anything is user-facing.
- **Architect:** Owns the catalogue, decides which shape a new problem belongs to, and insists that
  every deviation from the blueprint is written down as an ADR with its reasoning.
- **FDE:** Carries the blueprints into customer engagements to scope quickly, and adapts them to the
  customer's identity, data and deployment constraints without dropping the governance boxes.

## Check yourself
**Q:** A customer wants a system that reads incoming support tickets, looks up the customer's account,
decides whether to issue a refund under policy, and replies. Which reference shape or shapes apply, and
where would you place the human?
**A:** This is a workflow, not an agent: the steps are known in advance, so a fixed DAG with contracts
per node is the right shape. Classify the ticket and extract the claim (extraction shape with a strict
schema and validation), look up the account through a typed tool, evaluate the policy in code rather
than in the model because refund rules are deterministic and auditable, draft the reply with a model
grounded on the policy text and the account facts, and send. The human belongs at the action boundary:
the refund itself is a consequential, irreversible action, so it goes through an approval gate at
least until the system's precision on a golden set justifies auto-approval below a monetary threshold.
Replies that do not move money can be sent after output validation. Both halves share the gateway,
tracing and an evaluation set built from historical tickets with their resolutions.

## Quiz
### Q: What distinguishes a reference architecture from a design document for one system?
- [ ] A reference architecture names specific vendors for each component
- [x] A reference architecture describes a reusable shape with variation points and known failure modes, independent of any one system
- [ ] A reference architecture is produced after the system ships, as documentation
- [ ] A reference architecture only covers the model and prompt, not the infrastructure
> A reference is a blueprint for a class of problems: components, responsibilities, defaults and
> anti-patterns, with explicit places where choices are allowed. A design document instantiates one
> for a particular system and records its deviations.

### Q: A team proposes an autonomous agent for a process whose five steps are the same for every input. What is the architectural objection?
- [ ] Agents cannot call more than one tool per step
- [ ] Agents are only appropriate for batch workloads
- [x] A fixed workflow gives the same result with deterministic control flow, predictable cost and testable nodes, so the agent's flexibility buys nothing and costs reliability
- [ ] Agents require fine-tuned models, which the team does not have
> The agent shape is justified when the steps cannot be enumerated up front. When they can, a DAG of
> contracted nodes is cheaper to run, easier to test and easier to debug; choosing the agent adds
> variance without adding capability.

### Q: Which components appear in every AI reference architecture regardless of shape?
- [ ] A vector database and a reranker
- [ ] A planning loop and a sandbox
- [x] An evaluation harness with a golden set, tracing with cost attribution, and a control layer for validation and policy
- [ ] A fine-tuning pipeline and a distillation step
> Retrieval, planning and fine-tuning are shape-specific. Evaluation, tracing and output control are
> cross-cutting because every shape has probabilistic output whose quality must be measured and whose
> downstream effects must be bounded.
