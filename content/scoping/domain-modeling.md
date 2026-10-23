---
id: domain-modeling
module: scoping
name: Domain modeling
kind: practice
difficulty: core
est_minutes: 15
status: typed
lesson_type: decision
agent_relevance: core
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
Domain modeling captures the customer's entities, relationships, lifecycles, rules and vocabulary in one
agreed model. For an agent it is the source of tool schemas, structured outputs, prompt glossary,
retrieval filters and eval labels, so "active" or "closed" never silently mean the wrong thing.

## For agent builders
- **Why it matters for agents.** A model knows generic meanings; the customer's words have specific ones.
  An agent acting on the wrong meaning pays the wrong amount or closes the wrong claim, fluently. The
  domain model is what its tools and outputs are typed against.
- **Where it shows up in your stack.** Pydantic or JSON Schema tool definitions, structured-output schemas,
  enums for classification, the glossary section of the system prompt, retrieval metadata filters, and
  state-machine checks inside write tools.
- **What you control.** Which entities the agent can act on, which transitions its tools allow, which
  invariants are enforced in code, and how identities are resolved before a tool is called.
- **What you can skip.** Heavyweight DDD tactical patterns (repositories, domain events, full context maps)
  unless you are building the system of record; a glossary, a few entities, one or two state machines and
  the invariants are enough.

## Decision case
### Scenario
A wholesale distributor wants an email agent that answers "where is my order?" and handles cancellations.
All figures are illustrative.
- **Two meanings of "order":** sales calls the customer's purchase a sales order (`SO-` numbers in the
  CRM); the warehouse and finance work per shipment (`SH-` numbers in the ERP), each invoiced separately.
  18% of sales orders split into 2 to 4 shipments.
- **Volume and error cost:** 300 cancellation requests a week; cancelling something already picked costs
  about $180 in return handling.
- **Spike:** on 60 labelled emails, a prompt-only agent acted on the wrong entity in 9 (15%).
- **Constraints:** 6 weeks to pilot; sales ops and finance SMEs can give two 2-hour sessions each; the
  agent must not write to the ERP except through existing APIs.

### Options
| Option | How it works | Wrong-entity actions | SME time | Fits 6 weeks |
|---|---|---|---|---|
| A. One canonical "order" | Force one definition on both teams | Low for one team, wrong for the other | High: the definition is debated | Unlikely |
| B. Prompt glossary only | Explain both meanings and let the model infer | 15%: 45 a week, about $8,100 a week at $180 | Low | Yes |
| C. Two bounded contexts plus guarded tools | `SalesOrder` and `Shipment` models, an explicit mapping, identity resolution by key pattern, `cancel_sales_order` refusing anything picked | Spike: 1 in 60 wrong, 6 in 60 sent to a person; cancelling a picked shipment is refused in code | Moderate: four sessions | Yes |
| D. Knowledge graph of all entities | Model customers, orders, shipments, invoices and returns as a graph | Low | High | No: build and sync cost |

### Decision
Choose **C**: model sales and fulfilment as two bounded contexts with an explicit translation, and give
the agent tools on aggregate roots with the lifecycle guard inside.
- **Identity resolution does most of the work:** the `SO-` and `SH-` patterns in the tool schemas resolve
  the entity by key; emails without either go to the strong-attribute lookup or a person.
- **The lifecycle guard caps the error cost:** a cancellation after "picked" is refused and returned to the
  agent as data, so the $180 return case cannot happen through the tool.
- **The mapping is small and testable:** one sales order to 1 to 4 shipments; "where is my order" for a
  split order lists every shipment.
- **B's error rate is too costly** at about $8,100 a week; A and D do not fit the SME time or deadline.

**What would change it:**
- **Sales and finance agree one definition in the first session:** collapse to one context and drop the
  translation code.
- **Customers rarely quote a number** (most emails reach a person): add order lookup by customer and date,
  and re-check escalation volume.
- **Questions start crossing returns, credits and invoices routinely:** reconsider a graph for those
  traversals.

### Trade-offs accepted
- **About 10% of requests go to a person** (6 in 60, about 30 a week): track the share per resolution rule.
- **Translation code to maintain:** test the sales-order-to-shipment mapping on every ERP release.
- **The agent cannot cancel part of a split order:** count refused partial cancellations and add a tool
  deliberately if they are frequent.

### Review rubric
- Lists each term with its meaning per team, with an example and a counter-example.
- Decides between one context and several from evidence (SME disagreement, spike errors), not taste.
- Puts identity resolution and lifecycle guards in tools and validators, not in the prompt.
- Prices the wrong-entity error rate per week with the arithmetic shown.
- States the escalation path and its expected volume.
- Names the evidence that would collapse or extend the model.

## Why it exists
- **Every organisation has a private dialect.** In one insurer "claim" means a single loss event; in
  another it means each coverage line within it. "Active customer" might mean "has a contract", "logged in
  this quarter" or "billed this month", depending on whether you ask sales, product or finance.
- **The knowledge lives in heads.** Experienced staff carry these distinctions, and the documents they
  write assume them.
- **A model brings the opposite: a broad, average sense of each word** learned from public text. Asked to
  "count active customers" or "extract the claim date", it picks a plausible generic meaning.
- **The error is silent.** The output is fluent and well formatted, so the wrong number, or the report date
  instead of the loss date, flows down the pipeline or into the agent's next tool call.
- **The naive fixes fail.**
  - *Letting the model infer meaning* works only when documents are consistent, and they rarely are across
    departments.
  - *Patching each misunderstanding in the prompt* piles up special cases that disagree with the database
    schema, retrieval filters and evaluation labels, because each was written separately.
- **One explicit model replaces the patches.** Agreed with subject-matter experts (SMEs), every component
  reads from it. It also forces scope-changing questions: which entity has the identity, which states
  exist, and which rules the system must never violate.

## How it works
### 1. Build the glossary first
- **List the terms** from interviews, documents and screens, each with a one-line definition, an example and
  a counter-example.
- **Domain-driven design calls the result the *ubiquitous language*:** one vocabulary shared by experts,
  code, documentation and the agent's prompt.
- **Start here** because most domain errors in AI systems are vocabulary errors, and a glossary is cheap to
  validate with SMEs.

### 2. Identify entities and value objects
| Kind | Definition | Examples | What AI must do |
|---|---|---|---|
| **Entity** | Identity persists while attributes change | A claim, a policy, a customer | Resolve to an existing identifier (which customer is this email about?) |
| **Value object** | Defined only by its attributes | An address, a money amount with currency | Parse correctly |

**Identity resolution deserves its own design**, because it is where extraction most often silently fails,
and where an agent most often calls a tool with the wrong ID. Resolve in order of reliability:

1. **Exact key found in the text** (a claim number matching the agreed pattern).
2. **Deterministic lookup on strong attributes** (policy number plus loss date).
3. **Fuzzy matching on weak attributes** (a name and a postcode).
4. **Ask a person.**

- **Record which rule matched**, because a record linked by a fuzzy name match should be treated with less
  trust than one linked by key, and should not unlock write tools on its own.

### 3. Map relationships and cardinality
- **Record how entities relate:** a policy has many claims, a claim has many coverage lines, a customer can
  hold several policies.
- **Cardinality decides retrieval and reasoning.** "The customer's deductible" is ambiguous when the
  customer has three policies, so the agent must ask or choose by rule.

### 4. Model lifecycles as state machines
- **For each key entity, list its states, the permitted transitions, and who or what triggers each.**
- **This lets an AI system reason about "what can happen next"** and stops an agent from taking an action
  the business forbids (paying a claim that was never approved).

### 5. Write down invariants and business rules
| Type | Meaning | Example |
|---|---|---|
| **Invariant** | Always holds | A loss date is never after the report date; the payment total never exceeds the approved amount |
| **Rule** | Guides decisions | Claims above a threshold need a second approver |

- **Enforce invariants in code as validators**, because a model can be asked to respect them but cannot be
  trusted to always do so.
- **Some invariants span several entities.** "The sum of payments across a claim's coverage lines never
  exceeds its approved amount" cannot be checked by looking at one line.
- **DDD groups such entities into an aggregate** with one root (the claim) through which all changes go, so
  the root can check the rule on every change.
- **For an agent, the aggregate is the unit a tool acts on.** Expose `add_payment(claim_id, …)` on the
  claim, not a raw "update coverage line" tool that can break the total.

### 6. Draw bounded contexts
- **Where the same word means different things, do not force one definition.** Model each context
  separately and define the translation between them.
- **In AI terms,** each context often gets its own schema, prompt glossary, retrieval index, and in
  multi-agent systems its own agent and tool set.

### 7. Project the model into every artefact
| Model element | Projection |
|---|---|
| Entities and fields | Typed schemas for tool arguments, structured output and extraction |
| Allowed values | Enums that constrain classification labels and tool parameters |
| Glossary | A glossary section in prompts |
| Key attributes | Metadata fields for retrieval filters |
| Relationships | Entity and relationship types, if a knowledge graph is warranted |
| States, rules, edge cases | Guards inside write tools, plus the label set and edge cases for evaluation |

- **One source, many projections**, because divergence between them is where silent errors come from.

### 8. Validate with instances, not diagrams
- **SMEs rarely critique a class diagram well**, but they spot errors instantly in concrete examples: "this
  claim would never be in that state".
- **Walk real records through the model**, and replay real agent traces past an SME.

## Diagram
The claim lifecycle from the example: only these transitions are permitted, so an agent's attempt to pay a
claim still under review is rejected in code.

```mermaid
stateDiagram-v2
    [*] --> FNOL: loss reported
    FNOL --> UnderReview: adjuster assigned
    UnderReview --> Approved: cover confirmed
    UnderReview --> Denied: not covered
    Approved --> Paid: payment issued
    Denied --> [*]
    Paid --> [*]
```

## Examples
### One schema for extraction, glossary, invariant and lifecycle
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
try:
    transition(c, ClaimStatus.PAID)
except ValueError as e:
    print(e)                     # under_review -> paid is not a permitted transition
```

- **Field descriptions carry the glossary** into the JSON schema the model sees.
- **The enum restricts status** to the agreed states.
- **The validator rejects** an extraction that swaps the loss and report dates.
- **The reserve is a `Decimal` rather than a `float`** because money is a value object whose arithmetic must
  be exact; binary floats cannot represent most decimal amounts and accumulate rounding error in totals.

### The same model as an agent tool
Continuing the example above: the tool's argument schema is generated from the domain model, and the tool
returns the guard's error to the agent as data, so the model can recover (ask, or pick another action)
instead of crashing the loop.

```python
from pydantic import BaseModel, Field

class SetClaimStatusArgs(BaseModel):
    claim_id: str = Field(pattern=r"^CLM-\d{8}$", description="Claim number, never a policy number")
    new_status: ClaimStatus

TOOL = {"name": "set_claim_status",
        "description": "Move a claim to its next lifecycle state. Only permitted transitions succeed.",
        "input_schema": SetClaimStatusArgs.model_json_schema()}   # one source for the schema

CLAIMS = {c.claim_id: c}                                         # stand-in for the claims system

def set_claim_status(raw_args: dict) -> dict:
    try:
        args = SetClaimStatusArgs.model_validate(raw_args)       # rejects bad IDs and unknown states
        if args.claim_id not in CLAIMS:
            return {"ok": False, "error": f"no claim {args.claim_id}"}
        CLAIMS[args.claim_id] = transition(CLAIMS[args.claim_id], args.new_status)
        return {"ok": True, "status": CLAIMS[args.claim_id].status.value}
    except ValueError as e:                                      # pydantic's ValidationError is a ValueError
        return {"ok": False, "error": str(e)}                    # goes back to the model as the tool result

print(set_claim_status({"claim_id": "CLM-00412345", "new_status": "paid"}))
# {'ok': False, 'error': 'under_review -> paid is not a permitted transition'}
```

- **The enum in the schema** means the model cannot even name a state the business does not have.
- **Log every `ok: False`** result; it shows where the agent's plan disagrees with the business rules.

### An aggregate that guards a cross-line invariant
The claim is the aggregate root, so the only way to pay a coverage line is through it. Each payment would
pass a per-line check; only the root sees the total. Pure Python, amounts illustrative.

```python
from dataclasses import dataclass, field
from decimal import Decimal

@dataclass
class CoverageLine:
    line_id: str
    paid: Decimal = Decimal("0")

@dataclass
class ClaimAggregate:                       # the root: every change goes through it
    claim_id: str
    approved_amount: Decimal
    lines: dict[str, CoverageLine] = field(default_factory=dict)

    def total_paid(self) -> Decimal:
        return sum((l.paid for l in self.lines.values()), Decimal("0"))

    def add_payment(self, line_id: str, amount: Decimal) -> None:
        if amount <= 0:
            raise ValueError("payment must be positive")
        if self.total_paid() + amount > self.approved_amount:   # cross-line invariant
            raise ValueError(f"would pay {self.total_paid() + amount}, approved {self.approved_amount}")
        self.lines[line_id].paid += amount

claim = ClaimAggregate("CLM-00412345", Decimal("5000"),
                       {"building": CoverageLine("building"), "contents": CoverageLine("contents")})
claim.add_payment("building", Decimal("3000"))
claim.add_payment("contents", Decimal("1500"))
print(claim.total_paid())                   # 4500
try:
    claim.add_payment("contents", Decimal("800"))   # each line alone looks fine; the total does not
except ValueError as e:
    print("rejected:", e)                   # rejected: would pay 5300, approved 5000
```

- **Expose `add_payment` as the agent's tool,** not a generic "update coverage line" write, so the tool
  surface cannot express a payment that breaks the total.

### Worked scenario: resolving the claim an email is about
Five inbound emails to a claims agent, resolved with the cascade from step 2 before any tool acts
(illustrative).

| Email contains | Rule that matched | Trust | Action |
|---|---|---|---|
| "CLM-00412345" | Exact key | High | Link automatically |
| Policy HX-778 and loss date 2 March | Strong-attribute lookup, one claim found | High | Link automatically |
| Policy HX-778 only, customer has two open claims | Strong lookup, two candidates | Ambiguous | Ask a person |
| Name and postcode | Fuzzy match | Low | Link with a review flag; no write tools |
| No identifiers | None | None | Ask a person |

- **Storing the matched rule** on each record lets downstream steps, and auditors, treat fuzzy links
  differently from key links.

## Production patterns
- **Glossary and schema in version control** — keep the glossary, enums and Pydantic models in one module
  and generate the prompt glossary section, the structured-output schema and the tool argument schemas
  (`model_json_schema()` in Pydantic v2) from it, so they cannot drift. Review changes with SMEs like any
  other code change.
- **Validate, then reject or escalate** — run every model output and every tool argument through the schema
  and invariant validators before it is written anywhere; send failures to a review queue rather than
  letting the agent retry silently forever. Monitor validation-failure rate per field, because a spike
  usually means a new document layout or vocabulary drift.
- **Escape hatch in enums** — while the domain is still being discovered, include an explicit "other" or
  "needs review" value in classification enums. Monitor its share; a growing share signals a missing
  category to add to the model.
- **Match provenance field** — store which identity-resolution rule linked each extracted record (key,
  strong lookup, fuzzy, human), and only allow write tools on key or strong links. Monitor the fuzzy share
  and audit a sample of fuzzy links regularly.
- **Aggregate-shaped agent tools** — give agents tools that act on aggregate roots and enforce invariants
  and state transitions inside the tool, never raw row-level writes. Log rejected tool calls; they show
  where the agent's plan disagrees with the business rules.
- **Record walkthroughs as eval cases** — every SME correction from a walkthrough becomes a labelled
  evaluation case, so the model's edge cases accumulate in the agent's test set. Track remaining
  corrections per walkthrough as the model's maturity measure.

## Trade-offs
- **Precision vs SME time.** An explicit model prevents silent vocabulary errors, but takes SME time to
  agree. When the task is a narrow, single-team summarisation, a short glossary in the prompt may be all you
  need.
- **Reliability vs coverage.** Strict schemas and enums make outputs and tool calls reliable and testable,
  but reject legitimate cases the model did not foresee. Add an explicit "other" or escalation path when the
  domain is still being discovered.
- **Separate contexts vs translation code.** Bounded contexts keep each definition precise, but require
  translation code between them. That pays off when departments genuinely disagree, and is overhead when
  they do not.
- **Narrow tools vs agent flexibility.** Aggregate-shaped tools make invalid actions unrepresentable, but
  the agent can only do what the tools express. Add tools deliberately rather than falling back to a
  generic write.
- **Graph vs maintenance.** A knowledge graph makes relationships queryable and explainable, but is costly
  to build and keep in sync. It is worth it when questions routinely traverse several relationships, not
  for flat lookups.

## Pitfalls
- **Defining terms from documents alone, without SMEs.** Symptom: a glossary that reads well but gets
  corrected on almost every example walked through with the people who do the work. Fix: validate every
  term against real records with SMEs.
- **Letting prompts, schemas and evaluation labels drift apart.** Symptom: an agent that passes its own
  tests while users report it uses terms differently from the reporting dashboards. Fix: generate all of
  them from one versioned domain module.
- **Forcing one meaning across bounded contexts.** Symptom: endless debates over a field definition and
  outputs that are right for one department and wrong for another. Fix: model separate bounded contexts
  with an explicit translation.
- **Enforcing invariants only through prompt instructions.** Symptom: a rare but recurring output or tool
  call that violates a rule the business considers impossible, discovered by an auditor. Fix: enforce it
  in a validator or inside the write tool.
- **Giving an agent write tools on individual records inside an aggregate.** Symptom: each update passes its
  own validation while the combination violates a cross-record rule, such as payments exceeding the
  approved total. Fix: expose tools on the aggregate root only.
- **Modelling entities without identity resolution.** Symptom: extracted records that name a customer or
  policy but cannot be linked to the system of record, so downstream steps or tool calls fail. Fix: run
  the resolution cascade and store which rule matched.

## When to use
- **Use when** the agent extracts, classifies or acts on business entities, when several teams or systems
  share data, and when outputs feed reports or decisions.
- **Timing:** right after discovery and workflow analysis, before writing prompts, schemas or tools.
- **Keep it lightweight:** a glossary, key entities, one or two state machines and the invariants.
- **Measure:** how many SME corrections remain when you walk real records through it, and whether every
  schema, tool argument, enum and evaluation label traces back to it.
- **Interviews:** explaining how one domain model projects into prompts, schemas, tools, retrieval filters
  and evaluation labels shows architectural maturity.

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

## Interview questions
### Why does an LLM system need an explicit domain model when the model already understands language?
- **The model knows generic meanings;** the customer's words have specific ones, such as "claim" meaning a
  loss event in one insurer and a coverage line in another.
- **Mismatches are silent:** fluent, well-formatted output with the report date instead of the loss date
  passes every format check.
- **One agreed model** feeds schemas, tools, prompts, retrieval filters and eval labels, so they cannot
  disagree.

### Extracted records keep failing to link to the claims system. How do you debug and fix it?
- **Check identity resolution first:** what identifiers do the documents actually contain, and which
  resolution rule is being attempted?
- **Apply the cascade:** exact key, then strong-attribute lookup, then fuzzy match, then a person, recording
  which rule matched.
- **Measure the share per rule;** a high "no match" share often means a key pattern in the schema is wrong
  or documents use a different identifier.

### How do you turn a domain model into an agent's tools?
- **Generate tool argument schemas from the model** (`model_json_schema()`), so IDs carry their pattern and
  states are enums the model cannot invent.
- **Shape tools around aggregate roots and lifecycles:** `set_claim_status` checks the state machine and
  `add_payment(claim_id, …)` checks the total, inside the tool.
- **Return guard failures as tool results** so the agent can ask or re-plan, and log them as signals.

### When would you not build a formal domain model?
- **For a narrow, single-team task** such as summarising one document type for internal reading, a short
  prompt glossary may be enough.
- **Avoid a knowledge graph** for flat lookups; it pays only when questions routinely traverse several
  relationships, because keeping it in sync is costly.

### An agent with a "pay coverage line" tool overpaid a claim. What went wrong in the design?
- **The tool acted inside an aggregate.** Each line payment passed its own validation, but the cross-line
  invariant (total never exceeds approved amount) was never checked.
- **Fix:** expose `add_payment(claim_id, …)` on the claim root, which checks the total on every change, and
  log rejected calls.

### Why store money as `Decimal` and enforce invariants in validators rather than in the prompt?
- **Money must be exact.** Binary floats cannot represent most decimal amounts and accumulate rounding error
  in totals.
- **Prompts shift probabilities; validators guarantee.** A rule like "loss date never after report date"
  stated only in the prompt will eventually be violated and found by an auditor.

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

## Use cases & projects
### Use cases
- **Typing an agent's tools.** Tool argument schemas, enums and ID patterns come from one domain module, so
  the agent cannot name states or IDs the business does not have.
- **Guarding write actions.** State machines and aggregate invariants inside tools stop an agent from paying
  an unapproved claim or overpaying across lines.
- **Grounding extraction and RAG.** Field descriptions, glossary and metadata filters make extraction and
  retrieval use the customer's meanings, not generic ones.
- **Multi-department assistants.** Bounded contexts give each department's "active" or "order" its own
  definition, metric and agent behaviour.

### Mini project
**Build a state-guarded claims tool for an agent (2-3 hours).**
- **Goal:** give an agent a small set of domain-typed tools whose schemas and guards come from one model.
- **Steps:**
  1. Write a glossary of eight claims terms with examples and counter-examples.
  2. Implement the `Claim` model, lifecycle and `ClaimAggregate`; generate tool schemas with
     `model_json_schema()` for `set_claim_status` and `add_payment`.
  3. Wire the tools into a tool-calling loop with any provider SDK, returning guard errors as tool results.
  4. Write 15 test instructions, including five that should be refused (pay an unapproved claim, exceed the
     approved total), and run them.
- **Done when:** every forbidden action is rejected by a tool guard, the agent recovers or asks on each
  rejection, and all rejections appear in a log.
