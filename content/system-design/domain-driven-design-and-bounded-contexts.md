---
id: domain-driven-design-and-bounded-contexts
module: system-design
name: Domain-driven design & bounded contexts
kind: concept
difficulty: core
est_minutes: 16
status: crisp
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
Domain-driven design splits a system along the meanings of its business language: each bounded context
owns one consistent model, and contexts talk through explicit contracts, not a shared schema. For AI,
contexts decide tool boundaries, data ownership and who translates meaning for the agent.

## Why it exists
- **One word, three meanings.** Ask billing, support and sales what a "customer" is. Billing: a legal
  entity with a payment method and invoices. Support: a person with a login and a ticket history. Sales:
  an account with an owner, a pipeline stage and a renewal date. Each is right inside its own work and
  wrong inside the others'.
- **One Customer table for everyone fails.** Nobody fully understands it, a change for billing breaks
  support, and every team must coordinate with every other team to ship anything.
- **The enterprise canonical model fails too.** A committee defines the single true schema and everyone
  conforms, but meaning is local: the model becomes a lowest common denominator with dozens of optional
  fields, and the committee becomes the bottleneck.
- **Splitting by technical layer or table recreates the coupling.** A "customer service" that owns the
  Customer table for everyone just moves the same coupling behind an API.
- **DDD models the inconsistency instead of fixing it.** Each area uses its own language consistently
  inside a boundary; between boundaries, translation is explicit. Conway's observation (systems mirror
  their builders' communication structures) is why it works: a boundary matching a team's responsibility
  is one that team can own and change alone. AI raises the stakes: an agent reaches across many contexts
  through tools, retrieval indexes mix vocabularies, and without drawn boundaries the agent resolves every
  ambiguity by guessing.

## How it works
### Ubiquitous language
- **One vocabulary, everywhere in the area.** Developers and domain experts use the same terms, and so
  does the code: class names, tool names, event names, prompts.
- **The boundary test.** If a term means two things, you have found a boundary; if two terms mean one
  thing, pick one.
- **For AI products** this language also goes into system prompts, tool descriptions and evaluation
  rubrics, so inconsistency there shows up directly as model errors.

### Bounded contexts
- **Definition.** The boundary inside which a model and its language are consistent. It owns its data,
  its invariants and its storage; nothing outside writes to that storage directly.
- **Draw by language and responsibility,** not by data volume or technology. Billing, Support and Sales
  are three contexts, each with its own Customer, and that is correct.
- **Deployment is a separate decision.** A context may be a service, a module in a modular monolith, or a
  schema in one database; the boundary is about ownership and meaning.

### Finding the boundaries
Boundaries are discovered, not decreed. Map the business process with domain experts as a timeline of
domain events (event storming), then look for seams. Four signals mark one:

| Signal | Example |
|---|---|
| A term changes meaning | "customer", "active", "account" |
| Who decides changes | finance owns the dunning rule, support owns the escalation rule |
| Rate of change differs | pricing rules change weekly, ledger rules almost never |
| Rules must hold together at the same instant | they belong inside one context |

- **Wrong seam test:** when two candidate contexts must constantly ask each other questions to make any
  decision, the seam is in the wrong place.

### Subdomain classification
| Subdomain | What it is | Investment |
|---|---|---|
| *Core* | where the business differentiates | most careful modelling, your best people |
| *Supporting* | necessary but not differentiating | adequate, build simply |
| *Generic* | authentication, invoicing | buy or borrow |

- **For most AI products the core is the domain logic and the data, not the model call.** This
  classification tells you where custom work belongs and where a vendor is fine.

### Context mapping: how boundaries relate
- **Shared kernel:** two contexts share a small model and change it together. Cheap and dangerous; keep
  it tiny.
- **Customer–supplier:** the downstream team's needs feed the upstream team's roadmap.
- **Conformist:** downstream adopts the upstream model wholesale because it has no leverage, typical with
  a vendor API.
- **Anticorruption layer (ACL):** downstream translates the upstream model into its own at the boundary,
  *because* a leaking foreign model corrupts your language one field at a time. The most important
  pattern for AI integrations: the agent's context sees its own concepts, with adapters translating each
  external system.
- **Open host service with a published language:** upstream exposes a stable, documented interface in a
  well-defined language for many consumers. A tool schema an agent calls is exactly a published language.
- **Separate ways:** the contexts do not integrate at all. Sometimes correct.

### Inside a context: aggregates, entities, value objects, domain events
- **Aggregate.** A cluster of objects changed together under one transaction through one entry point (the
  root), with invariants that hold at the end of every transaction. Example: an `Order` with its lines and
  the invariant "total must not exceed the approved credit of 10,000".
- **One entry point.** Lines change only through `order.add_line(...)`, *because* if code could insert a
  line row directly, nothing would recheck the total.
- **Keep aggregates small** as the true invariants allow, *because* the aggregate is the unit of locking
  and a large one serializes unrelated edits.
- **Reference other aggregates by id,** not by object, so loading an order does not drag in the customer.
- **One aggregate per transaction;** the others catch up through *domain events* ("OrderPlaced",
  "InvoicePaid"). Cross-context changes are always eventually consistent, *because* a single transaction
  spanning contexts would couple their storage and their uptime. Here DDD hands over to event-driven
  architecture and sagas.
- **Entities vs value objects.** *Entities* have identity over time (the order is the same order after its
  lines change). *Value objects* are defined by their attributes and are immutable (`Money(120, "EUR")`),
  which makes them safe to share and pass across boundaries.
- **Repository.** Loads and saves whole aggregates, one per aggregate type, which keeps the invariant check
  from being bypassed by partial writes.

### Applying it to an AI system
- **One tool, or tool group, per bounded context,** speaking that context's published language; the
  agent's tool list then mirrors the context map.
- **Translation lives in tool adapters (the ACL), never in the prompt.** Asking the model to reconcile
  three meanings of "customer" at runtime is asking it to redo your architecture per request.
- **Scope retrieval indexes and memory per context** when vocabularies conflict, or tag them with the
  context so the prompt can say which "account" a passage refers to.
- **Consequential actions respect aggregate boundaries:** an agent step that must update two contexts is
  a saga with compensation, not one transaction.
- **Evals are per context,** written in that context's language by that context's experts.

## Examples
### Two contexts, one anticorruption layer, one published tool
Each context has its own Customer; the support agent sees only its own concept. The translation from
billing's model happens in the adapter, not in the prompt. `billing_api`, `mapping_repo` and `tickets`
stand in for the real clients.

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

PLAN_TO_TIER = {"ent": "enterprise", "pro": "pro", "free": "free"}

class BillingAdapter:              # anticorruption layer: billing model -> support language
    def __init__(self, billing_api, mapping_repo):
        self.api, self.map = billing_api, mapping_repo
    def payment_status(self, customer_id: str) -> dict:
        acct: BillingAccount = self.api.get_account(self.map.billing_id_for(customer_id))
        blocked = acct.past_due_since is not None and (date.today() - acct.past_due_since).days > 14
        if acct.plan not in PLAN_TO_TIER:            # fail loudly: a silent default corrupts meaning
            raise UnmappedUpstreamValue(f"billing plan {acct.plan!r}")
        return {"payment_blocked": blocked, "tier": PLAN_TO_TIER[acct.plan]}

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

### Running one lookup, then an upstream change
| Moment | What happens |
|---|---|
| Lookup | billing returns `BillingAccount("B-881", "Acme GmbH", "ent", 2026-09-30)` |
| On 2026-10-20 | 20 days past due, more than 14, so the agent sees `SupportCustomer(tier="enterprise", payment_blocked=True, open_tickets=3)` and never the words "ent" or `past_due_since` |
| Billing launches a "team" plan, with a `.get(plan, "free")` default | every team-plan customer is silently treated as free tier: a wrong answer with no error anywhere |
| Same launch, with the explicit map | the upstream change becomes a failing call and an alert on day one |

- **The 14-day rule is a support-context rule,** so it lives in the support side's adapter, versioned and
  tested there; neither billing nor the prompt needs to know it.
- **The point of an ACL:** translation is a contract, and an unmapped value is a contract break.

### Runnable: an aggregate that protects its invariant
Pure Python. `Money` is a value object; `Order` is the aggregate root and the only way to add lines.

```python
from dataclasses import dataclass, field
from decimal import Decimal

@dataclass(frozen=True)
class Money:                                   # value object: immutable, equal by value
    amount: Decimal
    currency: str
    def __add__(self, other):
        if other.currency != self.currency:
            raise ValueError("currency mismatch")
        return Money(self.amount + other.amount, self.currency)

class CreditLimitExceeded(Exception):
    pass

@dataclass
class Order:                                   # aggregate root: the only way to change lines
    order_id: str
    customer_id: str                           # another aggregate, referenced by id
    credit: Money
    lines: list = field(default_factory=list)
    events: list = field(default_factory=list)

    def total(self) -> Money:
        t = Money(Decimal(0), self.credit.currency)
        for _, price in self.lines:
            t = t + price
        return t

    def add_line(self, sku: str, price: Money) -> None:
        if (self.total() + price).amount > self.credit.amount:   # invariant checked on every change
            raise CreditLimitExceeded(f"{self.order_id} would exceed {self.credit.amount}")
        self.lines.append((sku, price))
        self.events.append(("OrderLineAdded", self.order_id, sku))  # other contexts react later

order = Order("O-1", "C-7", Money(Decimal(10000), "EUR"))
order.add_line("seats", Money(Decimal(9880), "EUR"))
order.add_line("support", Money(Decimal(120), "EUR"))
print(order.total().amount)                    # 10000: equal to the credit is allowed
try:
    order.add_line("extra", Money(Decimal(1), "EUR"))
except CreditLimitExceeded as e:
    print("rejected:", e)                      # rejected: O-1 would exceed 10000
```

## Production patterns
- **Storage ownership enforced by the database.** When contexts share one database, give each its own
  schema and database role, and grant no role write access to another context's schema. Other contexts
  read through an API or events. Monitor permission-denied errors from cross-schema access attempts.
- **Strict ACL mappings with contract tests.** Translate every upstream enum through an explicit map that
  raises on unknown values, and run contract tests against the upstream sandbox in CI. Alert on unmapped
  value errors in production, because each one is an upstream change you have not modelled yet.
- **Domain events through a transactional outbox.** Write the aggregate change and an outbox row in the
  same transaction; a relay publishes the row to the broker. Other contexts consume and update their own
  models. Monitor outbox lag and consumer lag per context.
- **Tool groups that mirror the context map.** Register agent tools per context with that context's
  published language and owners, and run per-context evals written by its experts. Monitor tool-selection
  errors and eval scores by context, so a regression points at one owner.
- **Coupling metrics on change.** Tag pull requests and releases by the contexts they touch, and track how
  many contexts a typical change spans and how often releases must be coordinated. A rising number means a
  seam is in the wrong place.

## Trade-offs
- **Separate models vs reconciliation.** Separate models per context remove cross-team coupling and keep
  each model small, but the same real-world thing exists in several places and must be reconciled
  through events or adapters.
- **ACL vs maintenance.** An anticorruption layer protects your language from upstream churn, at the cost
  of a translation that changes whenever either side changes; for a tiny, stable upstream, conforming is
  cheaper.
- **Eventual consistency vs disagreement windows.** Independent deployability and uptime come with
  windows where contexts disagree; when the business cannot tolerate that window, the boundary is
  probably drawn in the wrong place.
- **Rich core model vs wasted rigor.** A rich core-domain model repays deep investment, while the same
  rigor on generic subdomains wastes it; misclassifying the core is the expensive mistake.
- **One tool per context vs tool count.** Meanings stay clean and privileges scoped, but tools multiply;
  when contexts are many, a facade context that speaks the agent's language may be justified.

## Pitfalls
- **One shared database schema behind several "services".** Symptom: a migration in one team's context
  breaks another team's deployment.
- **Contexts drawn by technical layer or by noun** ("the customer service"). Symptom: every feature
  touches every service and release trains must be coordinated.
- **An upstream model leaking through without an ACL.** Symptom: vendor field names and enums appear in
  your prompts, your UI and your tests.
- **Asking the model to reconcile conflicting vocabularies at runtime.** Symptom: answers mix the billing
  and support meanings of a term, and evals cannot agree on correctness.
- **Translation tables with catch-all defaults.** Symptom: a new upstream enum value (a new plan, a new
  status) is quietly mapped to the wrong meaning and found weeks later through customer complaints
  rather than errors.
- **Transactions that span contexts.** Symptom: distributed locks, timeouts under load, and partial
  updates nobody can compensate.

## When to use
- **Use when:** more than one team or vocabulary touches the system, an agent must integrate several
  enterprise systems, or a shared schema has become the bottleneck for change.
- **Avoid when:** it is a small product with one team and one language; one context is correct, so do
  not fragment prematurely.
- **Measure coupling:** how many contexts a typical change touches, how often cross-context releases must
  be coordinated, and how many tool adapters contain translation versus pass-through.
- **Day to day:** naming decisions, where a rule lives, and whether a new field belongs to you.
- **Interviews:** draw a context map for a given business, place an agent and its tools on it, and explain
  why an anticorruption layer belongs in the adapter rather than the prompt.

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
