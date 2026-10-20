---
id: domain-driven-design-and-bounded-contexts
module: system-design
name: Domain-driven design & bounded contexts
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - system-design/modular-monolith-vs-microservices
  - system-design/event-driven-architecture
  - system-design/saga-pattern-and-distributed-transactions
  - system-design/data-consistency
  - system-design/c4-diagrams
  - enterprise-integration/data-mapping-and-canonical-schemas
  - agents/typed-tool-contracts
  - agent-reliability/tool-adapters-service-interfaces
sources:
  - Evans, 2003 — Domain-Driven Design: Tackling Complexity in the Heart of Software
  - Vernon, 2013 — Implementing Domain-Driven Design
  - Conway, 1968 — How Do Committees Invent?
---

## TL;DR
Domain-driven design splits a large system along the meanings of its business language, not along
technical layers: each bounded context owns one consistent model and vocabulary, and contexts talk
through explicit contracts instead of a shared schema. It exists because words like "customer" mean
different things in different parts of a business, and forcing one model onto all of them produces
systems nobody can change. For AI systems, bounded contexts decide tool boundaries, data ownership and
who translates meaning for the agent.

## Why it exists
Ask billing, support and sales what a "customer" is. Billing says a legal entity with a payment method
and invoices. Support says a person with a login and a ticket history. Sales says an account with an
owner, a pipeline stage and a renewal date. All three are right inside their own work and wrong inside
each other's. A system that tries to define one Customer table with every field from every department
gets a model nobody fully understands, where a change for billing breaks support, and where every team
must coordinate with every other team to ship anything.

The naive alternatives are both common. One is the enterprise canonical model: a committee defines the
single true schema for every concept and everyone conforms. It fails because meaning is local; the
canonical model becomes a lowest common denominator with dozens of optional fields, and the committee
becomes the bottleneck. The other is splitting by technical layer or by table, a "customer service" that
owns the Customer table for everyone, which recreates the same coupling behind an API.

Domain-driven design starts from the observation that the inconsistency is not a defect to be fixed but
a fact about the business to be modelled. Each area has its own language, used by its own people, and
the software for that area should use exactly that language, consistently, inside a boundary. Between
boundaries, translation is explicit. Conway's observation that systems mirror the communication
structures of the organizations that build them is why this works: a boundary that matches a team's
responsibility is one that team can own and change alone.

AI systems raise the stakes. An agent is usually a consumer that reaches across many contexts at once,
through tools, and retrieval indexes mix documents written in several vocabularies. If nobody has drawn
the boundaries, the agent inherits every ambiguity and resolves it by guessing.

## How it works
### Ubiquitous language
Within one area, developers and domain experts use the same terms, and the code uses them too: class
names, tool names, event names, prompts. The discipline is that if a term means two things, you have
found a boundary; if two terms mean one thing, you pick one. For an AI product this language is also
what goes into system prompts, tool descriptions and evaluation rubrics, so inconsistency there shows up
directly as model errors.

### Bounded contexts
A bounded context is the boundary inside which a model and its language are consistent. It owns its
data, its invariants and its storage; nothing outside writes to that storage directly. Draw contexts by
language and by responsibility, not by data volume or by technology. Billing, Support and Sales from the
example are three contexts, each with its own Customer, and that is correct. A context may be a service,
a module in a modular monolith, or a schema in one database; the boundary is about ownership and
meaning, and deployment is a separate decision.

### Subdomain classification
Not every context deserves the same investment. The *core* subdomain is where the business
differentiates, and it gets the most careful modelling and your best people. *Supporting* subdomains are
necessary but not differentiating, and *generic* subdomains (authentication, invoicing) should be bought
or borrowed. For most AI products the core is the domain logic and the data, not the model call; this
classification is what tells you where custom work belongs and where a vendor is fine.

### Context mapping: how boundaries relate
The relationships between contexts are named so teams can discuss them:
- **Shared kernel**: two contexts share a small model and change it together. Cheap and dangerous; keep
  it tiny.
- **Customer–supplier**: the downstream team's needs feed the upstream team's roadmap.
- **Conformist**: downstream adopts the upstream model wholesale because it has no leverage, typical with
  a vendor API.
- **Anticorruption layer (ACL)**: downstream translates the upstream model into its own at the boundary,
  *because* letting a foreign model leak in corrupts your language one field at a time. This is the most
  important pattern for AI integrations: the agent's context should see its own concepts, with adapters
  translating each external system.
- **Open host service with a published language**: upstream exposes a stable, documented interface in a
  well-defined language for many consumers. A tool schema an agent calls is exactly a published language.
- **Separate ways**: the contexts do not integrate at all. Sometimes correct.

### Inside a context: aggregates, entities, value objects, domain events
An *aggregate* is a cluster of objects changed together under one transaction through one entry point
(the root), with invariants that hold at the end of every transaction. Cross-aggregate and cross-context
changes are eventually consistent through *domain events* ("InvoicePaid"), *because* a single transaction
spanning contexts would couple their storage and their uptime. This is where DDD hands over to
event-driven architecture and sagas. *Entities* have identity over time; *value objects* are defined by
their attributes and are immutable, which makes them safe to pass across boundaries.

### Applying it to an AI system
- One tool, or tool group, per bounded context, speaking that context's published language; the agent's
  tool list then mirrors the context map.
- Translation lives in tool adapters (the ACL), never in the prompt. Asking the model to reconcile three
  meanings of "customer" at runtime is asking it to redo your architecture per request.
- Retrieval indexes and memory are scoped per context when vocabularies conflict, or tagged with the
  context so the prompt can say which "account" a passage refers to.
- Consequential actions respect aggregate boundaries: an agent step that must update two contexts is a
  saga with compensation, not one transaction.
- Evals are per context, written in that context's language by that context's experts.

## Example
Two bounded contexts, each with its own Customer, and a support agent that sees only its own concept
through an anticorruption layer. The tool the agent calls is a published language; the translation from
billing's model happens in the adapter, not in the prompt. `billing_api`, `mapping_repo` and `tickets`
are stand-ins for the real clients.

```python
from dataclasses import dataclass
from datetime import date

# Billing context: its own language and storage, owned by the billing team
@dataclass(frozen=True)
class BillingAccount:
    account_id: str
    legal_name: str
    plan: str                      # billing's codes: "ent", "pro", "free"
    past_due_since: date | None

# Support context: a different "customer", shaped for support work
@dataclass(frozen=True)
class SupportCustomer:
    customer_id: str
    display_name: str
    tier: str                      # support's words: "enterprise", "pro", "free"
    payment_blocked: bool
    open_tickets: int

class BillingAdapter:              # anticorruption layer: billing model -> support language
    def __init__(self, billing_api, mapping_repo):
        self.api, self.map = billing_api, mapping_repo
    def payment_status(self, customer_id: str) -> dict:
        acct: BillingAccount = self.api.get_account(self.map.billing_id_for(customer_id))
        blocked = acct.past_due_since is not None and (date.today() - acct.past_due_since).days > 14
        return {"payment_blocked": blocked, "tier": {"ent": "enterprise", "pro": "pro"}.get(acct.plan, "free")}

def lookup_customer(customer_id: str, tickets, billing: BillingAdapter) -> SupportCustomer:
    """Published language for the agent: no billing field names leak into its context."""
    b = billing.payment_status(customer_id)
    return SupportCustomer(customer_id=customer_id, display_name=tickets.name(customer_id),
                           tier=b["tier"], payment_blocked=b["payment_blocked"],
                           open_tickets=tickets.count_open(customer_id))

TOOLS = [{"name": "lookup_customer", "description": "Fetch the support view of a customer.",
          "input_schema": {"type": "object", "properties": {"customer_id": {"type": "string"}},
                           "required": ["customer_id"]}}]
```

The rule "payment is blocked after 14 days past due" is a support-context rule, so it lives in the
support side's adapter, versioned and tested there, and neither billing nor the prompt needs to know it.

## Trade-offs
- Separate models per context remove cross-team coupling and keep each model small, but the same
  real-world thing now exists in several places and must be reconciled through events or adapters.
- An anticorruption layer protects your language from upstream churn, at the cost of maintaining a
  translation that changes whenever either side changes; for a tiny, stable upstream, conforming is
  cheaper.
- Eventual consistency across aggregates and contexts gives independent deployability and uptime, but
  introduces windows where contexts disagree; when the business cannot tolerate that window, the boundary
  is probably drawn in the wrong place.
- A rich core-domain model repays deep investment, while applying the same rigor to generic subdomains
  wastes it; misclassifying the core is the expensive mistake.
- Giving an agent one tool per context keeps meanings clean and privileges scoped, but multiplies tools;
  when contexts are many, a facade context that speaks the agent's language may be justified.

## Pitfalls
- One shared database schema behind several "services"; the symptom is a migration in one team's context
  breaking another team's deployment.
- Drawing contexts by technical layer or by noun ("the customer service"); the symptom is every feature
  touching every service and release trains that must be coordinated.
- Letting an upstream model leak through without an ACL; the symptom is vendor field names and enums
  appearing in your prompts, your UI and your tests.
- Asking the model to reconcile conflicting vocabularies at runtime; the symptom is answers that mix the
  billing and support meanings of a term, and evals that cannot agree on correctness.
- Transactions that span contexts; the symptom is distributed locks, timeouts under load, and partial
  updates nobody can compensate.

## When to use
Reach for explicit bounded contexts when more than one team or more than one vocabulary touches the
system, when an agent must integrate several enterprise systems, or when a shared schema has become the
bottleneck for change. For a small product with one team and one language, one context is correct; do
not fragment prematurely. Measure coupling: how many contexts a typical change touches, how often
cross-context releases must be coordinated, and how many tool adapters contain translation versus
pass-through. Day to day this shows up as naming decisions, where a rule lives, and whether a new field
belongs to you. In interviews, expect to be asked to draw a context map for a given business, to place an
agent and its tools on it, and to explain why an anticorruption layer belongs in the adapter rather than
the prompt.

## Role lens
- **Engineer:** Keeps each context's language consistent in code, tools and prompts, writes adapters that
  translate at the boundary, and publishes events instead of writing to another context's tables.
- **Architect:** Draws the context map, classifies core versus supporting versus generic subdomains,
  chooses the relationship pattern per boundary, and decides where the agent sits on the map.
- **FDE:** Discovers the customer's real vocabularies and system owners, maps each to a context and an
  adapter, and resolves "which system is the source of truth" before the agent is built.

## Check yourself
**Q:** You are building an agent that answers "can this customer upgrade?" using a CRM, a billing system
and a support desk, each with its own notion of customer and status. Where do you put the logic that
reconciles them, and why not in the prompt?
**A:** I would treat the agent's own domain as a bounded context with its own small model, say an
Eligibility view with fields like tier, payment standing and open escalations, and build one adapter per
upstream system acting as an anticorruption layer: the CRM adapter maps account stages to the agent's
tier, the billing adapter maps past-due rules to payment standing, the support adapter counts
escalations. The agent gets one tool in its own published language. Putting the reconciliation in the
prompt would make the model re-derive the business rules on every request, from three vocabularies it
only partly understands, with no tests and no owner; a mapping change would be a prompt edit with
unpredictable effects. In adapters the rules are code, owned by the team that owns each boundary,
unit-tested, and versioned independently of the model.

## Quiz
### Q: Billing, Support and Sales each define "customer" differently. What does domain-driven design recommend?
- [ ] Define one canonical Customer schema with every field and make all three conform
- [x] Keep a separate model per bounded context and translate explicitly at the boundaries
- [ ] Store all customer data in one service that the other three call
- [ ] Let the agent reconcile the differences at runtime
> Meaning is local to each area of the business. A single model becomes a lowest common denominator
> owned by a committee; separate models with explicit translation let each team change independently.

### Q: What is the role of an anticorruption layer when an agent integrates a vendor system?
- [ ] To scan vendor responses for malicious content
- [x] To translate the vendor's model into the agent's own language so foreign fields and semantics do not leak into prompts and code
- [ ] To cache vendor responses for performance
- [ ] To enforce rate limits on vendor calls
> An ACL protects a context's language. Without it, upstream names and enums spread through prompts,
> tests and UI, and every upstream change becomes your change.

### Q: Why are changes that span two bounded contexts usually made eventually consistent via events rather than one transaction?
- [ ] Databases cannot run transactions across more than one table
- [ ] Events are faster than transactions
- [x] A single transaction would couple the contexts' storage and availability, defeating the point of separate ownership
- [ ] Domain-driven design forbids transactions
> Aggregates are the unit of transactional consistency; across contexts, independent deployability and
> uptime matter more than instantaneous agreement, so domain events and sagas carry the change.
