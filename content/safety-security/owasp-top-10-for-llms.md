---
id: owasp-top-10-for-llms
module: safety-security
name: OWASP Top 10 for LLMs
kind: concept
difficulty: intro
est_minutes: 15
status: reviewed
related:
  - safety-security/prompt-injection
  - safety-security/excessive-agency
  - safety-security/supply-chain-security-for-models-and-datasets
  - safety-security/rag-poisoning
  - safety-security/data-leakage
  - safety-security/output-validation
  - security-governance-compliance/threat-modeling
  - deployment/security-questionnaires
sources:
  - Official docs: OWASP Top 10 for LLM Applications (2025 edition)
  - Official docs: OWASP Top 10 for LLM Applications v1.1 (2023)
---

## TL;DR
The OWASP Top 10 for LLM Applications lists the most consequential risk classes in systems that embed a
language model, shaped like the web Top 10 so security teams, vendors and auditors share a vocabulary.
Its value is the framing: the model is an untrusted component that follows instructions found in data,
so every classic boundary (input validation, output encoding, least privilege, supply chain, resource
limits) must be redrawn around the prompt. This lesson is the map; the deep lessons are elsewhere in
this module.

## Why it exists
Security reviews run on checklists, and before 2023 there was no shared one for LLM features. Each review
re-derived the threat model from scratch and missed different classes, and procurement questionnaires
had nothing to ask. The web Top 10 was the obvious template but misses the point in one crucial place:
web injection means data being interpreted as code because escaping was missing, and the fix is to
separate the two syntactically. In an LLM there is no escaping. The model is designed to interpret text
as instructions, so "sanitise your inputs" offers false assurance and a different mental model is needed.

The project published v1.0 in August 2023 and v1.1 that October, then a restructured 2025 edition in late
2024 that reflected what had actually gone wrong in deployed systems: agents with too much authority,
retrieval pipelines leaking across users, system prompts treated as secrets, and cost blow-ups from
unbounded usage. Because vendors, security questionnaires and increasingly regulators reference the list
by item number, an engineer who cannot say what LLM01 or LLM06 means, and how their system addresses it,
will struggle in an enterprise security review regardless of how good the system is.

## How it works
### The 2025 list, grouped by the boundary it concerns
The ten items are easier to remember as six trust boundaries than as a numbered list.

**Instruction boundary: who gets to tell the model what to do.**
- *LLM01 Prompt Injection.* Untrusted text, from the user (direct) or from documents, pages and tool
  results (indirect), changes the model's behaviour. There is no reliable detector, so controls limit what
  a hijacked model can do. Deep lesson: prompt injection.
- *LLM07 System Prompt Leakage.* The system prompt will be extracted, so it must contain no secrets or
  authority. Deep lesson: data leakage.

**Data boundary: what the model can see and repeat.**
- *LLM02 Sensitive Information Disclosure.* PII, secrets and other users' data in completions, logs or
  training data. Deep lessons: data leakage, PII redaction, cross-tenant leakage.
- *LLM08 Vector and Embedding Weaknesses.* Retrieval stores without access control, cross-tenant
  retrieval, invertible embeddings, poisoned documents. Deep lessons: RAG poisoning, cross-tenant leakage.
- *LLM04 Data and Model Poisoning.* Manipulated training, fine-tuning or retrieval data that implants
  behaviour or bias. Deep lessons: supply-chain security, RAG poisoning.

**Action boundary: what the model's output is allowed to cause.**
- *LLM05 Improper Output Handling.* Treating completions as trusted: rendering them as HTML, passing them
  to a shell or SQL engine, or deserialising them unchecked. The LLM version of output encoding. Deep
  lesson: output validation.
- *LLM06 Excessive Agency.* Too many tools, too much permission, too little oversight, so a mistaken or
  injected model causes real damage. Deep lessons: excessive agency, tool privilege escalation.

**Trust-in-output boundary.**
- *LLM09 Misinformation.* Confident wrong answers, fabricated citations and insecure generated code
  consumed without verification; the risk is in the workflow around the model as much as in the model.

**Resource boundary.**
- *LLM10 Unbounded Consumption.* No limits on tokens, requests, context length or tool loops, leading to
  denial of service, denial of wallet, or model extraction through high-volume querying.

**Build boundary.**
- *LLM03 Supply Chain.* Compromised models, datasets, adapters and the packages that load them. Deep
  lesson: supply-chain security for models and datasets.

### Attacks cross rows, so read the matrix across
Real incidents rarely sit in one item. A typical chain: an attacker plants instructions in a document
that gets indexed (LLM04/LLM08); a user's question retrieves it and the model follows it (LLM01); the
model writes a markdown image whose URL carries the user's data, and the chat client renders it (LLM05);
the data reaches the attacker's server (LLM02). Four rows, one attack. The useful observation is that the
cheapest reliable control is often in a different row from the one the incident gets filed under: you
cannot reliably stop LLM01, but not rendering remote images (an LLM05 control) breaks this chain
completely. That is why the list works best as a set of boundaries with layered controls, and why the
action and resource boundaries, which do not depend on detecting the attack, carry most of the weight.

### What changed from v1.1 to 2025, and why it matters
The 2023 list had Insecure Plugin Design, Model Denial of Service, Overreliance and Model Theft as
separate items. In 2025, plugin design folded into Excessive Agency and Supply Chain, because the failure
was authority and provenance rather than plugins specifically; Model DoS broadened into Unbounded
Consumption to cover cost and extraction, absorbing Model Theft on the way; Overreliance became
Misinformation; and two new items, System
Prompt Leakage and Vector and Embedding Weaknesses, were added because RAG and agent deployments made
them routine. The direction of travel is from "the model misbehaves" to "the system around the model
grants it too much", which is also the direction your controls should take.

### How to use the list
It is a threat-modelling prompt, not a compliance standard. There is no certification and "OWASP
compliant" has no formal meaning. Nor is the numbering your priority order: it reflects what is common
across the industry, while your ranking should come from exposure and blast radius in your system. An
internal summariser with no tools barely has an LLM06 problem; a customer-facing agent that can move money
has little else. The practical use is a table with one row per item and three columns:
how the risk manifests in *this* system, the control, and the evidence (a test, a config, a log). Items
that do not apply get a one-line reason rather than a blank, because reviewers read blanks as gaps. OWASP
has since published agentic-specific guidance under its agentic security initiative; the ten items above
remain the baseline vocabulary. Two neighbouring frameworks answer different questions: MITRE ATLAS
catalogues adversary tactics and techniques against ML systems, which is what red teams plan from, and
the NIST AI Risk Management Framework describes the organisational process for governing AI risk. The
OWASP list sits between them as the engineering checklist for one application.

## Example
Threat-model a contract-review assistant: RAG over each customer's uploaded contracts, a tool that creates
tickets in the customer's tracker, used by legal teams at 300 tenants. Walking the list produces concrete
controls rather than abstractions.

LLM01: contracts are untrusted content, so the model may read injected instructions inside them; the
ticket tool is therefore gated by a confirmation step showing the structured title and body, and no
network tools exist. LLM07: the system prompt holds formatting rules only; tenant and permissions come
from the token. LLM02 and LLM08: retrieval filters on `tenant_id` inside the ANN query, traces are redacted
before export, and a per-tenant canary test runs in CI. LLM04: the embedding model is pinned by hash and
pulled from the internal registry; uploaded contracts are scanned for hidden text in white font and zero-
width characters. LLM05: completions are rendered as plain text, never HTML; the ticket body is validated
against a schema with a length cap. LLM06: the only write tool is `create_ticket`, scoped to the
requesting user's tracker project. LLM09: every clause-level claim must carry a citation to a retrieved
span, and a groundedness eval runs weekly. LLM10: illustrative limits of 8k input tokens, 2k output
tokens, 60 requests per user per hour, a per-tenant monthly token budget, and a ten-step cap on the agent
loop. LLM03: the base model, embedding model and reranker each have recorded hashes, signatures and an
entry in the AI bill of materials. Each row now has a control and a piece of evidence a reviewer can ask
for. Ranking them for this system: LLM01 with LLM06 first, because the one write path lands in 300
customers' trackers and contracts are attacker-supplied by construction; LLM08 and LLM02 next, because a
single missing filter exposes one customer's contracts to another; LLM10 and LLM03 after that. A chatbot
over public documentation, by contrast, would rank LLM09 and LLM10 near the top and LLM06 near the bottom.

## Trade-offs
- A shared checklist speeds reviews and questionnaires, but filled-in rows can substitute for thinking;
  the list flips from asset to liability when teams answer "LLM01: we have a guardrail" and stop there.
- Grouping risks by trust boundary makes controls reusable across items, yet a questionnaire will ask by
  item number, so maintain the mapping in both directions.
- The list evolves between editions, which keeps it relevant, but a control matrix written against v1.1
  item numbers no longer lines up with 2025 references; version your matrix explicitly.
- Covering all ten for a low-risk internal tool is more process than the risk warrants; scale the depth
  of each row to blast radius, but still write the row.

## Pitfalls
- Treating "OWASP LLM Top 10 compliant" as a meaningful claim from a vendor; the symptom is an inability to
  produce a per-item control and evidence table when asked.
- Mapping LLM01 to an input classifier and declaring it solved; the symptom is a red-team finding that a
  paraphrased injection reaches the tool layer unchanged.
- Ignoring LLM10 because "the provider handles scaling"; the symptom is a month-end invoice several times
  the forecast after a scripted client or a runaway agent loop.
- Reading LLM05 as a model problem; the symptom is cross-site scripting or command execution from a
  completion that was rendered or executed without validation, which no model change would have fixed.
- Writing the matrix once at launch; the symptom is a new tool or data source shipped months later with
  no row and no review.
- Assessing each row in isolation; the symptom is every row marked "mitigated" while a red team chains an
  injection through rendering into disclosure, because no single row owned the chain.

## When to use
Use the list at three moments: when designing a new LLM feature, as the skeleton of the threat model;
before an enterprise security review or questionnaire, as the structure of your answers; and after any
incident, to classify it and check which row's evidence was missing. Do not use it as a scoring scheme or
a certification. Measure coverage (every item has a row with control and evidence), freshness (rows
updated when the architecture changes), and red-team results per item. It shows up in interviews as "walk
me through the OWASP LLM Top 10 for this design", where the strong answer groups items by boundary and
names a concrete control and test for each rather than reciting the list.

## Role lens
- **Engineer:** Turns each applicable item into a test or configuration that can be pointed at as
  evidence, from tenant-filtered retrieval to token caps and output schema validation.
- **Architect:** Owns the per-item control matrix for each system and the trust-boundary diagram it derives
  from, and keeps it versioned against the edition of the list referenced by customers.
- **FDE:** Answers customer security questionnaires item by item with evidence, and translates a customer's
  item-numbered concern into the specific control in the deployment.

## Check yourself
**Q:** A vendor's sales deck says their chatbot is "OWASP LLM Top 10 compliant". What does that claim
actually mean, and what would you ask to evaluate their security posture?

**A:** Formally it means nothing: the Top 10 is an awareness and threat-modelling document with no
certification scheme, so the claim at best signals that someone has read it. I would ask for a per-item
table: for each of the ten, how the risk manifests in their architecture, the control, and the evidence.
Specifically, how tool actions are authorised and gated (LLM06), what the model can do if a retrieved
document contains instructions (LLM01), how tenants are separated in the vector store and caches (LLM08,
LLM02), whether anything in the system prompt would matter if leaked (LLM07), how completions are
rendered or executed (LLM05), what rate and token limits exist per tenant (LLM10), and where their models
and embeddings come from with hashes (LLM03). I would also ask which edition they mapped against, and for
red-team or penetration-test reports that reference those items, since evidence rather than assertion is
what the list is meant to elicit.

## Quiz
### Q: Why does input sanitisation, which solves SQL injection, not solve prompt injection?
- [ ] LLM inputs are too long to sanitise within latency budgets
- [x] A prompt has no syntactic separation between instructions and data; the model is built to treat text as instructions
- [ ] Sanitisation removes the few-shot examples the model needs
- [ ] Prompt injection only occurs through tool outputs, which are not user inputs
> SQL injection is fixed by keeping code and data in separate channels; in a prompt they are the same
> channel by design, so controls must limit what a steered model can do rather than try to filter the text.

### Q: An LLM's completion is passed directly to a shell command and later rendered as HTML in a dashboard. Which item covers this?
- [ ] LLM01 Prompt Injection
- [ ] LLM06 Excessive Agency
- [x] LLM05 Improper Output Handling
- [ ] LLM09 Misinformation
> Improper Output Handling is about downstream components trusting the completion; the fix is validation
> and encoding at the consumer, exactly as with any untrusted input, regardless of how the completion was
> produced.

### Q: What changed in the 2025 edition relative to v1.1?
- [ ] Prompt injection was removed because guardrail models solved it
- [ ] The list became a formal certification with audited levels
- [x] Model DoS broadened into Unbounded Consumption, and System Prompt Leakage and Vector and Embedding Weaknesses were added
- [ ] The list was merged into the web application Top 10
> The 2025 edition reflected real deployments: cost and extraction joined denial of service, and RAG and
> agent failures earned their own items, while prompt injection stayed at number one.
