---
id: ai-solution-architecture
module: system-design
name: AI / solution architecture
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
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
An AI solution architecture is the design of everything around the model call: how a request becomes
context, which model runs, what validates the output, what is logged, and how quality and cost are
measured. It exists because a model is a probabilistic, expensive, stateless, rented dependency, and
nothing about correctness, safety, cost or enterprise integration is solved by the call itself. The
architect's job is to put deterministic engineering where it belongs and leave the model only the
part it is uniquely good at.

## Why it exists
The first version of almost every AI feature is one function: build a prompt string, call the API,
show the answer. It demos well and it ships. Then the failures arrive, and none of them is in the
model. Answers vary between runs, so nobody can tell a regression from noise. Cost scales with every
user and every retry. The prompt swells to 3,000 tokens of rules because there is no retrieval and no
tool access. A user pastes a contract and gets a confidently invented clause. Security asks where the
data goes and who can see whose documents. The provider deprecates the model version and the carefully
tuned prompt stops working. Each failure lives in the system the model was supposed to be surrounded by.

Classical solution architecture already exists, so why treat this as its own discipline? Because a
model breaks three assumptions that classical architecture leans on. Components are deterministic:
same input, same output. Correctness can be unit-tested. Cost per call is negligible. A model's
behaviour is instead specified by data (prompts, examples, retrieved context) rather than by code, its
quality can only be measured statistically over a dataset, and its latency and per-call cost are
first-order constraints that shape the design. Sculley et al. described the classical version of this
as hidden technical debt: the model is a small box and the plumbing is the rest of the diagram. With
rented, general-purpose LLMs the ratio is even more extreme, because the model is the same one your
competitor rents. Differentiation lives entirely in how you assemble context, constrain outputs, and
close the evaluation loop.

The naive alternative, "better prompts will fix it", fails because a prompt is a configuration
surface, not an architecture. It cannot give you access control, retrieval freshness, caching,
retries, fallbacks, schema guarantees, evaluation, or cost accounting.

## How it works
### The layers and the reason each one is separate
1. **Experience and integration layer.** UI, public API, chat channels, IDE plugins. Owns identity,
   sessions and streaming to the client. It never holds a prompt or a model key, *because* otherwise
   every surface becomes coupled to a specific model's behaviour and cannot be changed centrally.
2. **Orchestration layer.** Turns a request into one or more model calls: context assembly (retrieval,
   memory, tool results), prompt rendering, routing to a model, the tool-execution loop, output
   parsing. Most application code lives here *because* this is the deterministic part you can test.
3. **Model layer.** A portfolio of models behind a gateway: pinned versions, fallbacks, rate limits,
   cost metering, provider credentials. Separate *because* models change on the provider's schedule,
   not yours, and because the gateway is the one place to enforce budgets and policy.
4. **Knowledge and data layer.** Ingestion, chunking, indexes, memory stores, attribute lookups, each
   carrying access-control metadata. Separate *because* freshness, permissions and provenance are data
   engineering problems; a prompt cannot know that a document was revoked an hour ago.
5. **Control layer.** Input and output guardrails, schema validation, policy checks, approval gates
   before consequential actions. Exists *because* model output is untrusted input to the rest of your
   system: a retrieved document can carry an injected instruction, and the model will sometimes follow it.
6. **Evaluation and observability.** Traces of every call (prompt, context, output, tokens, latency,
   cost), golden datasets, offline eval gates in CI, online metrics and feedback capture. Exists
   *because* quality is statistical: a test case tells you nothing, a dataset tells you a distribution.

### The design moves specific to AI systems
- **Push determinism out of the model.** Anything that can be code (lookups, arithmetic, routing on
  known fields, formatting, validation) should be code. Code is cheaper, faster, testable and does not
  hallucinate; the model is reserved for language understanding and generation.
- **Treat context assembly as the primary data flow.** Answer quality is bounded by what is in the
  context window. The architecture decides what is retrieved, in what order, under what token budget,
  and with which permissions; this is where most quality bugs originate.
- **Budgets as non-functional requirements.** A latency budget (say p95 ≤ 3 s, split across retrieval,
  prefill and decode), a token budget per request, and a cost per successful task. These numbers drive
  model choice, whether to stream, and whether to cache.
- **Typed output contracts.** Structured outputs validated against a schema so downstream code has a
  real boundary and a parse failure is an error you can count, not a silent corruption.
- **Deliberate placement of humans.** Where a wrong output is expensive, add review or approval. The
  architecture decides where that risk boundary sits, not the prompt.
- **A cheap feedback loop.** Traces → labelled failures → golden set → eval → change in prompt, model
  or retrieval → release gate. If this loop takes a week, the system will not improve.

### Solution architecture versus AI architecture
The *solution* half is enterprise integration: single sign-on, the systems of record the context comes
from, deployment constraints such as VPC or data residency, compliance evidence, operations and support
boundaries. The *AI* half is the model-facing decisions: retrieval versus fine-tuning, model tiers,
agent versus workflow, guardrails. A working architecture needs both, and the FDE usually lives exactly
at their seam.

### Artefacts a reviewer expects
C4 diagrams for structure; ADRs for the decisions that are hard to reverse (model family, RAG versus
fine-tune, agentic versus workflow); an NFR table with the budgets; a data-flow diagram showing every
path by which data reaches a model and where it is stored; a threat model covering prompt injection and
cross-tenant leakage; and a cost model tied to expected traffic.

## Example
A claims-handling assistant for an insurer (all figures illustrative). Requirements: answer adjusters'
questions over policy documents and the claim file, draft customer letters, never expose another
customer's data, p95 under 4 s, 2,000 adjusters at about 20 requests each per day, so roughly 40k
requests per day.

Request path: SSO token → orchestration service → ACL-aware retrieval scoped to the claim and the
adjuster's entitlements, top 8 chunks ≈ 2,500 tokens → prompt rendered from a 600-token system prompt
plus context plus a 100-token question ≈ 3,200 input tokens → gateway routes Q&A to a mid-tier model and
letter drafting to a frontier model → output parsed into `{answer, citations[]}` → citation checker
verifies every citation points at a retrieved chunk → output guardrail scans for identifiers belonging
to other claims → streamed to the UI → trace written with tokens, latency and cost.

Latency budget: retrieval 300 ms, prefill ≈ 500 ms, decode of a 300-token answer at ≈ 50 tokens/s is
6 s, which blows the 4 s target. Two architectural responses: stream, so the user perceives time to
first token (≈ 1 s), and cap Q&A answers at 200 tokens with a "show more" follow-up. Cost: 40k × 3.2k
≈ 128M input tokens per day, which is the number that justifies the mid-tier model for Q&A and prompt
caching of the fixed 600-token prefix. Evaluation: 150 real adjuster questions with reviewed answers,
faithfulness measured by citation match, run on every prompt or model change and weekly in production.

## Trade-offs
- Layering gives testability and swappable models, but each hop adds latency and another service to
  operate; a single internal tool with one model is better served by a modular monolith that keeps the
  same boundaries as modules rather than services.
- Logic in code instead of prompts gives determinism and tests, but iterates more slowly; while the
  problem is still being discovered a prompt-heavy prototype is the right call, and the architecture
  hardens as behaviour stabilises.
- Guardrails and approval gates reduce risk but add latency and user friction; the flip point is the
  cost of a wrong action, so low-stakes drafting needs far fewer gates than money movement.
- Full tracing of prompts and outputs is what makes evaluation possible, but it is also a store of
  sensitive data; retention, redaction and access policies have to be designed with it, not after.

## Pitfalls
- Prompt-as-architecture: a 4,000-token system prompt holding business rules, data and formatting.
  Symptom: every edit breaks something else and nobody can say what the system does.
- No evaluation set before launch. Symptom: whether a new model or prompt is better is argued from
  anecdotes, and regressions are discovered by users.
- Trusting model output: feeding it to SQL, a shell, or an email-send without validation. Symptom: a
  retrieved document containing an instruction causes an unintended action.
- Designing for average latency. Symptom: the demo is snappy; production p95 is 15 s because of long
  contexts, retries and queueing on the provider.
- Forgetting the model is rented: no version pinning, no fallback. Symptom: a provider deprecation or
  regional outage becomes a product incident with no runbook.
- No cost model. Symptom: usage grows, the bill grows faster, and the feature is throttled or quietly
  removed.

## When to use
This is less a pattern you choose than the way to think about any system with a model in it, applied
at the right weight. For a prototype, sketch the layers and implement them as modules in one process.
When moving to production, add in this order: tracing, an evaluation set and gate, a gateway with
pinned versions and fallback, retrieval with access control, output contracts, then guardrails sized to
the risk. Measure p95 latency per layer, cost per successful task, eval score, and failure rate by
category. The first architecture question is always whether a model is needed at all; if a rule or a
lookup solves it, that is the better architecture. In interviews, "design a document assistant for X"
expects the layers above, explicit budgets, the eval loop and the trust boundary around model output.

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
