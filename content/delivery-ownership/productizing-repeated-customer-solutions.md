---
id: productizing-repeated-customer-solutions
module: delivery-ownership
name: Productizing repeated customer solutions
kind: practice
difficulty: advanced
est_minutes: 16
status: fact-checked
related:
  - customer-data-evaluation/feedback-loops-to-product
  - adoption/feature-request-triage
  - strategy/platform-strategy
  - scoping/productionization
  - prompting/prompt-templates
  - customer-data-evaluation/customer-specific-regression-suites
  - agent-runtime-architecture/multi-tenant-state-isolation
  - delivery-ownership/technical-documentation
---

## TL;DR
Forward-deployed work produces bespoke solutions; when the same solution is built for the third customer,
it should become product: one shared core, per-customer configuration, and defined extension points for
the genuinely unique parts. Doing it too early bakes in the wrong abstraction; doing it too late leaves N
diverging forks whose maintenance, model upgrades and bug fixes scale with customer count, turning a
product company into a consultancy.

## Why it exists
A forward-deployed engineer is paid to make one customer successful fast, so the first build is bespoke:
their connector, their prompt, their output format. The second customer asks for "the same thing", and
the fastest path is to copy the repository and change what differs. By the fifth customer there are five
forks. A security fix has to be applied five times, and a model upgrade means re-tuning and re-evaluating
five prompts that have drifted apart. Every improvement made for one customer is unavailable to the
others, and the engineers who know each fork become permanent bottlenecks. Delivery cost per customer
stops falling, which is the economic signature of a services business, not a product.

The naive opposite fails too. Building a general platform after the first customer means guessing which
parts will vary, and with one example you cannot tell an essential requirement from an accident of that
customer's environment. The result is an abstraction with knobs nobody uses and no seam where the second
customer actually differs.

Productizing exists to find the moment in between: enough repetition to see the real shape, early
enough that the forks have not diverged beyond recovery. It is also a feedback channel. The field sees
demand before the product team does, and productizing is how that evidence becomes roadmap.

## How it works
### 1. Detect repetition
Keep an inventory of every deployment and what each component does. A common heuristic is the rule of
three: build the first, copy the second while recording every difference, extract on the third,
*because* with two instances you cannot tell which of their differences will recur, while a third
usually shows what genuinely varies and what was coincidence. Three is a heuristic, not a law: a
well-understood domain can justify extracting at two, and a very heterogeneous one may need more.

### 2. Classify every difference
Diff the instances component by component and put each difference in one bucket:
- **Configuration**: differences in data, not logic. Field lists, thresholds, clause taxonomies, prompt
  variables, few-shot examples, output destinations. These become a validated, versioned config schema.
- **Extension**: customer-specific logic that cannot be data, such as a connector to a proprietary system.
  These go behind a defined interface, *because* an interface lets the core evolve without breaking them.
- **Accident**: differences with no reason beyond who wrote it and when. Pick one behaviour and converge.
- **Genuine one-off**: a requirement no other customer shares. It stays outside the product as paid
  services work, *because* pulling it into the core taxes every other customer.

### 3. Build the core and its seams
The core owns everything shared: orchestration, the prompt template, model routing and version pinning,
evaluation harness, observability, security controls. Prompts become one template with slots rather than
N forked prompts, so a model upgrade is tuned once. Evaluation splits into a shared core suite plus a
per-customer regression suite, *because* the core must prove it did not break anyone in particular.

### 4. Migrate the forks
Move existing customers onto the product, least-divergent first, each gated by their regression suite
showing parity with the fork's behaviour. Stopping halfway is the worst state: you now maintain a product
and forks. Set a date after which forks receive security fixes only.

### 5. Transfer ownership
A product team owns the core's roadmap, versioning and deprecation; field engineers consume it, contribute
extensions, and file evidence-backed requests. Track time to onboard a new customer, the share of each
deployment that is configuration rather than custom code, and engineering hours per customer per month.

## Example
Three customers run contract-review pipelines built by the field team. Customer A pulls from SharePoint
and writes to Salesforce, B from Box into Jira, C from S3 into a webhook. Diffing the forks shows the
ingestion connectors and output sinks are extensions, the clause types, risk threshold and few-shot
examples are configuration, two different chunking strategies are an accident, and C's requirement to
score clauses against its internal playbook is a genuine one-off. The resulting customer config:

```python
from typing import Literal, Protocol
from pydantic import BaseModel, Field

class Connector(Protocol):                 # extension point: custom code lives behind this
    def fetch(self, since: str) -> list[bytes]: ...

class ClauseType(BaseModel):
    name: str
    description: str                       # injected into the shared extraction prompt template
    examples: list[str] = Field(default_factory=list, max_length=5)

class CustomerConfig(BaseModel):           # configuration: data, validated, versioned per tenant
    schema_version: Literal[2]
    tenant_id: str
    source: Literal["sharepoint", "box", "s3", "custom"]
    clause_types: list[ClauseType]
    risk_threshold: float = Field(0.7, ge=0.0, le=1.0)
    sink: Literal["salesforce", "jira", "webhook"]
    model_alias: str = "contract-extraction-default"   # pinned and upgraded centrally
```

The economics, with illustrative effort figures: if a model upgrade costs about 40 engineer-hours per
fork to re-tune and re-evaluate, three forks cost 120 hours and ten cost 400. On the product, the same
upgrade might cost 50 hours on the core plus 4 hours per customer to run and review its regression suite:
62 hours at three customers, 90 at ten. The fourth customer then needs a connector and a config file,
not a new codebase.

## Trade-offs
- Productizing early avoids fork divergence, but with fewer than three examples you will likely abstract
  the wrong seam; it flips toward early extraction only when the domain is already well understood.
- A rich configuration schema lets customers onboard without code, but every option multiplies the test
  matrix; prefer an extension point once options start interacting with each other.
- Migrating every fork onto the core cuts long-term maintenance, but costs revalidation effort and
  customer goodwill now; leave a fork in place only if that customer is winding down.
- Refusing one-offs keeps the core clean, but can lose a strategic deal; accept them as services work
  outside the core, priced to cover their ongoing maintenance.

## Pitfalls
- Abstracting from a single deployment; the symptom is the second customer needing changes inside the
  "generic" core rather than in its configuration.
- Letting configuration become a programming language; the symptom is config files with conditionals and
  expressions that only the original engineer can safely edit.
- Stopping the migration halfway; the symptom is the team maintaining the product plus most of the old
  forks, with fixes landing in one and not the others.
- Forking the shared prompt per customer "just for now"; the symptom is a model upgrade that passes core
  evals but regresses two customers whose prompt copies nobody re-tested.
- Productizing without per-customer regression suites; the symptom is a core release that silently breaks
  one customer's edge cases, discovered from their support ticket.

## When to use
Start productizing when the same solution shape appears for the third customer, when onboarding time per
customer has stopped falling, or when a model upgrade or security fix has to be applied in several places.
Hold off while the domain is still being discovered and each customer teaches you something new about the
shape. Measure onboarding time, custom code per deployment and maintenance hours per customer before and
after. In day-to-day field work, the habit that makes this possible is recording every difference you
introduce when copying a solution. In interviews, describe how you decided what became configuration,
what became an extension, and what you refused.

## Role lens
- **Engineer:** Builds the config schema, the extension interfaces and the per-customer regression suites
  that prove each migration preserved the behaviour the customer relied on.
- **Architect:** Decides where the seams go, which differences become configuration versus extension, and
  when a one-off is refused from the core to protect every other customer.
- **FDE:** Spots the repetition first, documents how each deployment diverges, and brings the evidence to
  the product team instead of quietly maintaining another fork.

## Check yourself
**Q:** Four customers run forks of your support-triage agent. One adds a custom escalation rule engine; the
other three differ only in categories, tone and destination queue. A new customer signs next month. What do you do?
**A:** The three similar forks show the shape: categories, tone guidance and queue mapping become a
validated config schema, the prompt becomes one template with slots, and destinations become sink
extensions behind an interface. I would build the core with a shared eval suite, write regression suites
from each customer's real tickets, and migrate the least-divergent fork first. The rule engine is either an
extension point, if others are likely to need rules, or a supported one-off outside the core. The new
customer onboards onto the product, not a fifth fork, which is the first proof the extraction worked.

## Quiz
### Q: Why is the third instance, rather than the first, the usual point to extract a product?
- [ ] Because contracts typically forbid reusing code before three customers
- [x] Because several instances are needed to tell essential variation from accidents of one environment
- [ ] Because model providers discount usage after three tenants
- [ ] Because the first two builds are always discarded
> With one example every detail looks essential. Comparing three shows which differences recur and
> belong in configuration and which were incidental, so the abstraction fits real demand.

### Q: A customer needs a connector to a proprietary document system nobody else uses. Where should it live?
- [ ] Inside the core pipeline, behind a customer-ID conditional
- [ ] In a new fork of the whole product for that customer
- [x] Behind a defined extension interface, implemented outside the core
- [ ] As new keys in the shared configuration schema
> Logic that cannot be expressed as data belongs behind an interface so the core can evolve without it.
> Customer-ID conditionals and forks recreate exactly the divergence productizing removes.

### Q: What best indicates productizing is working?
- [ ] The product exposes more configuration options than any single fork had
- [ ] Every customer request is accepted into the core
- [ ] The field team stops talking to customers about requirements
- [x] Onboarding time and per-customer maintenance hours fall as the customer count grows
> The point is that delivery cost per customer declines. More options or accepting everything usually
> raises cost, and field feedback is the input productizing depends on.
