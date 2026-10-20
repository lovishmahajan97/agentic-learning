---
id: domain-modeling
module: scoping
name: Domain modeling
kind: practice
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - system-design/domain-driven-design-and-bounded-contexts
  - enterprise-integration/data-mapping-and-canonical-schemas
  - prompting/structured-outputs-json-mode
  - customer-data-evaluation/domain-prompt-engineering
  - rag-retrieval/knowledge-graphs
  - scoping/problem-decomposition
  - scoping/workflow-analysis
sources:
  - Eric Evans, 2003 — Domain-Driven Design
---

## TL;DR
Domain modeling captures the customer's entities, relationships, lifecycles, rules and vocabulary in an
explicit, agreed form, then reuses that one model in schemas, prompts, retrieval metadata and evaluation
labels. It exists because a language model is fluent in generic meanings while the customer's words have
specific ones, and an unstated mismatch ("account", "active", "closed") produces confident outputs that
are wrong in ways nobody notices.

## Why it exists
Every organisation has a private dialect. In one insurer "claim" means a single loss event; in another it
means each coverage line within it. "Active customer" might mean "has a contract", "logged in this
quarter" or "billed this month", depending on whether you ask sales, product or finance. Experienced
staff carry these distinctions in their heads, and the documents they write assume them.

A language model brings the opposite: a broad, average sense of each word learned from public text. Ask
it to "count active customers" or "extract the claim date" and it picks a plausible generic meaning. The
output is fluent and well formatted, so the error is silent: the number is wrong, or the date is the
report date rather than the loss date, and the pipeline passes it on.

The naive alternatives fail. Letting the model infer meaning from context works only when documents are
consistent, and they rarely are across departments. Patching each misunderstanding with a sentence in the
prompt produces a growing pile of special cases that disagree with the database schema, the retrieval
filters and the evaluation labels, because each was written separately. Domain modeling replaces the
patches with one explicit model, agreed with subject-matter experts (SMEs), that every component reads
from. It also forces the questions that change scope: which entity has the identity, which states exist,
and which rules the system must never violate.

## How it works
### 1. Build the glossary first
List the terms that appear in interviews, documents and screens, and write a one-line definition for
each, with an example and a counter-example. Domain-driven design calls the result the *ubiquitous
language*: one vocabulary shared by experts, code and documentation. Start here *because* most domain
errors in AI systems are vocabulary errors, and a glossary is cheap to validate with SMEs.

### 2. Identify entities and value objects
An **entity** has an identity that persists while its attributes change (a claim, a policy, a customer).
A **value object** is defined only by its attributes (an address, a money amount with currency). The
distinction matters for AI *because* extraction must resolve entities to an existing identifier (which
customer is this email about?), while value objects only need to be parsed correctly.

### 3. Map relationships and cardinality
Record how entities relate: a policy has many claims, a claim has many coverage lines, a customer can
hold several policies. Cardinality decides retrieval and reasoning: a question about "the customer's
deductible" is ambiguous when the customer has three policies, so the system must ask or choose by rule.

### 4. Model lifecycles as state machines
For each key entity, list its states and the permitted transitions, along with who or what triggers
each. This is what lets an AI system reason about "what can happen next" and stops an agent from taking
an action the business forbids (paying a claim that was never approved).

### 5. Write down invariants and business rules
Invariants always hold (a loss date is never after the report date; the payment total never exceeds the
approved amount). Rules guide decisions (claims above a threshold need a second approver). Enforce
invariants in code as validators, *because* a model can be asked to respect them but cannot be trusted
to always do so.

### 6. Draw bounded contexts
Where the same word means different things in different parts of the organisation, do not force one
definition. Model each context separately and define the translation between them. In AI terms, each
context often gets its own schema, prompt glossary and retrieval index.

### 7. Project the model into every artefact
The same model becomes: typed schemas for structured output and extraction; enums that constrain
classification labels; a glossary section in prompts; metadata fields for retrieval filters; entity and
relationship types if a knowledge graph is warranted; and the label set and edge cases for evaluation.
One source, many projections, *because* divergence between them is where silent errors come from.

### 8. Validate with instances, not diagrams
SMEs rarely critique a class diagram well, but they spot errors instantly in concrete examples: "this
claim would never be in that state". Walk real records through the model.

## Example
A slice of an insurer's claims domain, agreed with claims SMEs, expressed as a Pydantic v2 schema. The
same class constrains LLM extraction output, documents the glossary in field descriptions, enforces an
invariant and guards lifecycle transitions that an agent might attempt.

```python
from datetime import date
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel, Field, model_validator

class ClaimStatus(str, Enum):           # lifecycle agreed with claims SMEs
    FNOL = "first_notice_of_loss"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    DENIED = "denied"
    PAID = "paid"

ALLOWED = {                             # which transitions the business permits
    ClaimStatus.FNOL: {ClaimStatus.UNDER_REVIEW},
    ClaimStatus.UNDER_REVIEW: {ClaimStatus.APPROVED, ClaimStatus.DENIED},
    ClaimStatus.APPROVED: {ClaimStatus.PAID},
    ClaimStatus.DENIED: set(), ClaimStatus.PAID: set(),
}

class Claim(BaseModel):
    claim_id: str = Field(pattern=r"^CLM-\d{8}$")         # identity comes from the claims system
    policy_number: str = Field(description="Policy, not the customer account number")
    loss_date: date = Field(description="Date the loss happened, not the date it was reported")
    reported_date: date
    status: ClaimStatus
    reserve_amount: Decimal = Field(ge=0, description="Estimated liability, not the amount paid")

    @model_validator(mode="after")
    def loss_before_report(self):
        if self.loss_date > self.reported_date:           # invariant stated by SMEs
            raise ValueError("loss_date cannot be after reported_date")
        return self

def transition(claim: Claim, new: ClaimStatus) -> Claim:
    if new not in ALLOWED[claim.status]:
        raise ValueError(f"{claim.status.value} -> {new.value} is not a permitted transition")
    return claim.model_copy(update={"status": new})

c = Claim(claim_id="CLM-00412345", policy_number="HX-778", loss_date="2026-03-02",
          reported_date="2026-03-05", status="under_review", reserve_amount=4200)
print(transition(c, ClaimStatus.APPROVED).status.value)   # approved
transition(c, ClaimStatus.PAID)   # raises: under_review -> paid is not a permitted transition
```

The field descriptions carry the glossary into the JSON schema the model sees, the enum restricts status
to the agreed states, and the validator rejects an extraction that swaps the loss and report dates. The
reserve is a `Decimal` rather than a `float` because money is a value object whose arithmetic must be
exact; binary floats cannot represent most decimal amounts and accumulate rounding error in totals.

## Trade-offs
- An explicit model prevents silent vocabulary errors, but takes SME time to agree; when the task is a
  narrow, single-team summarisation, a short glossary in the prompt may be all you need.
- Strict schemas and enums make outputs reliable and testable, but reject legitimate cases the model did
  not foresee; add an explicit "other" or escalation path when the domain is still being discovered.
- Separate bounded contexts keep each definition precise, but require translation code between them;
  that pays off when departments genuinely disagree, and is overhead when they do not.
- A knowledge graph makes relationships queryable and explainable, but is costly to build and keep in
  sync; it is worth it when questions routinely traverse several relationships, not for flat lookups.

## Pitfalls
- Defining terms from documents alone, without SMEs; the symptom is a glossary that reads well but gets
  corrected on almost every example walked through with the people who do the work.
- Letting prompts, schemas and evaluation labels drift apart; the symptom is a model that passes its own
  tests while users report it uses terms differently from the reporting dashboards.
- Forcing one meaning across bounded contexts; the symptom is endless debates over a field definition and
  outputs that are right for one department and wrong for another.
- Enforcing invariants only through prompt instructions; the symptom is a rare but recurring output that
  violates a rule the business considers impossible, discovered by an auditor.
- Modelling entities without identity resolution; the symptom is extracted records that name a customer
  or policy but cannot be linked to the system of record, so downstream steps fail.

## When to use
Model the domain whenever the AI system extracts, classifies or acts on business entities, whenever
several teams or systems share data, and whenever outputs feed reports or decisions. Do it right after
discovery and workflow analysis, before writing prompts or schemas, and keep it lightweight: a glossary,
key entities, one or two state machines and the invariants. Measure it by how many SME corrections
remain when you walk real records through it and by whether every schema, enum and evaluation label
traces back to it. In interviews, explaining how one domain model projects into prompts, schemas,
retrieval filters and evaluation labels shows architectural maturity.

## Role lens
- **Engineer:** Encodes the model as typed schemas, enums and validators used for structured outputs, and
  rejects or escalates model outputs that violate invariants instead of passing them on.
- **Architect:** Owns bounded-context boundaries and the canonical model, deciding where translation
  happens and whether a knowledge graph or relational schema represents the domain.
- **FDE:** Runs glossary and example-walkthrough sessions with the customer's experts, and catches the
  terms that mean different things in different departments before they reach a prompt.

## Check yourself
**Q:** A retail customer wants an assistant that answers "how many active customers do we have in each
region?" from their warehouse and documents. Sales, finance and marketing each define "active"
differently. How do you handle it?
**A:** Treat it as a bounded-context problem, not a prompt problem. Agree each definition with its owning
team, give each a distinct name in the glossary (contracted, billed in the last 30 days, engaged in the
last 90 days), and implement each as a deterministic metric in the warehouse or semantic layer, not as
something the model computes. The assistant's job becomes mapping a question to the right named metric
and asking for clarification when "active" is ambiguous, citing which definition it used. Evaluation
cases then include ambiguous phrasings to confirm it asks or chooses by an agreed default.

## Quiz
### Q: Why should invariants such as "loss date is never after report date" be enforced in code rather than only stated in the prompt?
- [ ] Prompts cannot contain dates
- [ ] Code validation is required by every regulator
- [x] A model can be instructed to respect a rule but cannot be guaranteed to, while a validator rejects every violation
- [ ] Validators make the model generate faster
> Instructions shift probabilities; they do not guarantee behaviour. A deterministic validator catches
> every violation, so impossible outputs are rejected or escalated instead of silently passed downstream.

### Q: What distinguishes an entity from a value object in a domain model?
- [ ] Entities are stored in databases, value objects only in memory
- [x] An entity keeps its identity while attributes change; a value object is defined only by its attributes
- [ ] Entities are nouns and value objects are verbs
- [ ] Value objects always come from external systems
> A claim remains the same claim when its status changes, so it needs an identifier that extraction
> must resolve. An address or money amount is equal to any other with the same values and only needs parsing.

### Q: Two departments use "order" to mean different things. What is the most robust modelling choice?
- [ ] Pick the larger department's definition and use it everywhere
- [ ] Let the model infer which meaning is intended from context
- [x] Model separate bounded contexts with distinct definitions and an explicit translation between them
- [ ] Remove the term from the glossary to avoid confusion
> Forcing one meaning makes outputs wrong for one department, and inference fails when context is thin.
> Separate contexts keep each definition precise, and explicit translation makes the mapping testable.
