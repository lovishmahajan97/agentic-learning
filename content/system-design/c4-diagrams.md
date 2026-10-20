---
id: c4-diagrams
module: system-design
name: C4 diagrams
kind: practice
difficulty: intro
est_minutes: 11
status: reviewed
related:
  - system-design/ai-solution-architecture
  - system-design/reference-architectures
  - system-design/adrs
  - system-design/domain-driven-design-and-bounded-contexts
  - delivery-ownership/technical-documentation
  - deployment/security-and-architecture-reviews
  - strategy/stakeholder-alignment
sources:
  - Brown — The C4 model for visualising software architecture (c4model.com)
  - Official docs: Structurizr DSL
---

## TL;DR
C4 is a small set of conventions for architecture diagrams at four nested zoom levels, System Context,
Container, Component and Code, where every box states its name, type, technology and purpose and every
arrow states its intent. It exists because ad-hoc boxes-and-arrows mix abstraction levels and leave
readers guessing what a box is and which way data flows. For AI systems it is the quickest way to show
where the model, the customer's data and the trust boundaries actually sit.

## Why it exists
Most architecture diagrams fail at the same thing: nobody can tell what level they are looking at. One
picture shows a Kubernetes cluster, a "Users" stick figure, a PostgreSQL cylinder, an `AuthService`
class and a team name, connected by unlabelled lines that may mean "calls", "deploys to" or "is owned
by". Every review then spends its first twenty minutes establishing what the boxes are. UML tried to fix
this with a rigorous notation that almost nobody learned, so teams fell back to free-form whiteboards
and the ambiguity returned.

C4 borrows the idea of a map. A country map, a city map and a street map use different symbols and a
different density of detail, and you know which one you are holding. C4 fixes four zoom levels and a
tiny notation, so a reader knows that a box on a Container diagram is a deployable unit and a box on a
Component diagram is a part of one. Everything else, tooling, layout, colours, is left free.

AI systems raise the stakes. The first question from security, legal and the customer's architects is
"where does our data go, and which parts talk to the model provider?" A diagram that cannot answer that
in one glance costs you a review cycle. C4's insistence on labelling technology and the direction and
meaning of each flow is exactly what that audience needs.

## How it works
### The four levels
1. **System Context.** Your system is one box. A *software system* here is the thing one team builds
   and owns; anything owned by someone else, including another team's platform, is drawn as external.
   Around the box are the people who use it and the external systems it depends on: the identity provider, the CRM it reads tickets from, the hosted model
   provider. No technology inside the box yet. This exists because the first thing any audience needs is
   the system's place in the world, and it is the diagram you show executives and customers.
2. **Container.** Zoom into the system box. A *container* here is a separately runnable or deployable
   unit: the web app, the API, the ingestion worker, the database, the vector store, the queue, the model
   server, the LLM gateway. The term predates Docker and is wider than it; a database is a container, a
   Docker image is not necessarily one container. Each shows its technology ("FastAPI", "PostgreSQL +
   pgvector", "vLLM"). This is the diagram engineers and operators live in.
3. **Component.** Zoom into one container and show its major structural parts and responsibilities:
   inside the API, an authentication middleware, a tenancy resolver, an orchestration component, a
   retrieval client, a guardrail layer, a usage meter. Components map to modules or packages, not to
   classes. Draw this only for containers whose internals matter to someone other than their authors.
4. **Code.** Class or entity diagrams for one component. Generate them from the code if you need them
   at all; hand-maintained code diagrams are the first to rot.

Three supplementary diagrams reuse the same elements: a **System Landscape** for several systems, a
**Dynamic** diagram that numbers the arrows to tell the story of one use case (a RAG request: resolve
tenant, embed query, search, assemble prompt, call gateway, stream, meter), and a **Deployment** diagram
that places containers onto infrastructure (region, VPC, cluster, GPU node pool, the customer's network
versus yours). The deployment view is the one forward-deployed engineers draw most, because it is where
trust boundaries and data residency become visible.

### The notation rules that do the work
Each rule exists to remove one specific ambiguity:
- Every element carries **name, type, technology and a one-line description**, so a reader never has to
  guess whether "Gateway" is an API gateway, an LLM gateway or a network device.
- Every relationship is a **single-direction arrow labelled with intent and, where useful, protocol**
  ("sends completion requests to, HTTPS/JSON"), so data-flow direction is never inferred.
- Every diagram has a **title stating its level and scope**, and a **legend** if colours or shapes carry
  meaning.
- Keep a diagram to roughly twenty elements. Past that, the right move is a new diagram one level down,
  not a smaller font.
- Colour and shape are consistent across diagrams: people, your containers, external systems and data
  stores each look the same everywhere.

### Diagrams as code
The C4 authors' tooling, Structurizr, separates the **model** (elements and relationships, defined once)
from **views** (which subset to draw at which level). This exists because consistency is the hard part:
when the model is one source of truth, renaming a container updates every diagram, and a relationship
cannot appear on the Container view but be missing from the Context view. C4-PlantUML and Mermaid's C4
syntax give the same notation in text form with less model discipline. Text diagrams live in the
repository, change in pull requests next to the code they describe, and can be regenerated in CI, which
is the practical answer to diagram rot.

### What to make visible in an AI system
Treat the model provider as an external system on the Context diagram, because that arrow is a data
egress a reviewer must see. Show the gateway, the vector store and the evaluation pipeline as containers,
because each is where a decision (routing, isolation, release gating) is enforced. On the Deployment
view, draw the boundary between the customer's environment and yours and mark which flows carry
customer documents or prompts. A Dynamic diagram of one request is usually worth more than a Component
diagram of everything.

C4 has no first-class trust-boundary element, and it does not pretend to be a threat model. Draw
boundaries with deployment nodes (the customer's VPC, your cloud account, the provider's region) or with
named groups, and state in the legend what crossing one means. A security team running a STRIDE-style
review will still want a data-flow diagram; a good Deployment view gives them most of it.

## Example
A Structurizr DSL workspace for a support assistant. One model feeds three views, so the Context
diagram for stakeholders, the Container diagram for engineers and the Dynamic view of one request cannot
disagree. The model provider is external, the vector-capable database is tagged so it renders as a
cylinder, and every relationship states intent and protocol. The dynamic view numbers the steps of one
reply in order, and Structurizr checks each step against the relationships in the model, so if someone
deletes the `api -> gateway` arrow the workspace fails to build instead of the request story silently
going stale. Notice also
what the Context view reveals once rendered: two arrows leave the system, to the CRM and to the model
provider, and the second is the data egress a privacy review will ask about first.

```
workspace "Support Copilot" "Drafts ticket replies grounded in the knowledge base" {
    model {
        agent   = person "Support Agent" "Answers customer tickets"
        crm     = softwareSystem "CRM" "System of record for tickets and articles" "External"
        llm     = softwareSystem "Model Provider" "Hosted LLM API" "External"
        copilot = softwareSystem "Support Copilot" "Suggests grounded replies" {
            web     = container "Web App" "Agent-facing UI" "React"
            api     = container "API" "Auth, tenancy, orchestration, guardrails" "FastAPI"
            worker  = container "Ingestion Worker" "Chunks and embeds articles" "Python"
            gateway = container "LLM Gateway" "Routing, quotas, prompt cache" "Python"
            db      = container "Database" "Tenants, chunks, embeddings, feedback" "PostgreSQL + pgvector" "Database"
        }
        agent   -> web     "Uses"
        web     -> api     "Sends chat requests to" "HTTPS/JSON"
        api     -> db      "Reads and writes" "SQL"
        api     -> gateway "Requests completions from" "HTTPS"
        gateway -> llm     "Sends prompts and receives completions" "HTTPS"
        worker  -> crm     "Pulls articles from" "REST"
        worker  -> db      "Writes chunks and embeddings to" "SQL"
    }
    views {
        systemContext copilot "Context"    { include * autolayout lr }
        container     copilot "Containers" { include * autolayout lr }
        dynamic copilot "DraftReply" "One grounded reply" {
            agent -> web "Opens a ticket and asks for a draft"
            web -> api "Sends ticket text"
            api -> db "Fetches top-8 chunks for this tenant"
            api -> gateway "Requests a draft grounded in the chunks"
            gateway -> llm "Forwards prompt; streams completion"
            autolayout lr
        }
        styles {
            element "External" { background #999999 color #ffffff }
            element "Database" { shape Cylinder }
        }
    }
}
```

## Trade-offs
- Fixed abstraction levels make diagrams instantly readable to anyone who knows C4, but they constrain
  you when a system does not decompose neatly; the fix is usually a Dynamic or Deployment view, not a
  bespoke notation.
- Diagrams as code stay in sync with the repository and survive reviews, but auto-layout rarely looks as
  good as a hand-arranged picture; hand layout wins only for a diagram that will be shown far more often
  than it changes.
- A single model with many views guarantees consistency, but it demands that someone own the model;
  without that owner, C4-PlantUML files per diagram are easier to keep alive.
- C4 is deliberately lighter than UML or ArchiMate, which makes it learnable in an afternoon, but it
  says nothing about behaviour beyond numbered arrows or about formal semantics; for enterprise-wide
  capability mapping or protocol-level design, those heavier notations (or sequence diagrams) earn their cost.
- Component diagrams explain a container's internals, but they rot fastest and are read least; draw them
  for the containers whose structure others depend on and skip the rest.

## Pitfalls
- Mixing levels in one diagram; the symptom is a picture that shows "Kubernetes" and "the retry helper
  class" at the same time, and a review that spends its time asking what the boxes are.
- Unlabelled or double-headed arrows; the symptom is a security reviewer asking "which direction does
  customer data flow here?" and nobody being sure.
- Reading *container* as Docker container; the symptom is a diagram with a managed database and a hosted
  model provider missing because "they are not containers".
- Leaving the model provider off the Context diagram because it is "just an API"; the symptom is a
  procurement or privacy review discovering the data egress late.
- Diagrams in a slide deck disconnected from the code; the symptom is a Container view that still shows
  a service deleted two quarters ago.
- Drawing everything; the symptom is a forty-box Container diagram nobody can read, instead of a
  Container view plus two Dynamic views of the requests that matter.

## When to use
Draw a Context and a Container diagram for every system that more than one team or one organisation
needs to understand, and keep them in the repository as text. Add a Deployment view the moment the
system crosses a trust boundary, which for an AI product is usually immediately (a hosted model provider,
a customer VPC). Add Dynamic views for the two or three request paths that reviewers and new engineers
ask about most, typically a chat request and the ingestion flow. Skip Component and Code diagrams unless
a specific audience needs them. A useful test: can a new engineer, a security reviewer and a customer
architect each find the diagram meant for them and answer their first question without you in the
room? In interviews, sketching a Context then a Container diagram is a clean way to structure any
system-design answer, and naming technologies and flow directions as you go signals the discipline.

## Role lens
- **Engineer:** Keeps the Container and Dynamic diagrams in the repo as code, updates them in the same
  pull request that adds a queue or a model server, and uses them to onboard teammates.
- **Architect:** Owns the model behind the views, decides which containers and boundaries exist, and
  uses the Context and Deployment diagrams as the shared picture in design and security reviews.
- **FDE:** Produces the Deployment and Context views for each customer environment, marking where
  customer data crosses into the vendor's systems, which is often the document that unblocks procurement.

## Check yourself
**Q:** A customer's security team asks for "an architecture diagram" of your RAG product before approving
a pilot in their VPC. Which C4 views do you send, what must each make explicit, and why not just one
picture?
**A:** Three views, each for a different question. A System Context diagram showing their users, your
system as one box, and every external system including the hosted model provider, because their first
question is which third parties touch their data. A Container diagram naming each deployable with its
technology, API, worker, gateway, database with vector extension, model server if self-hosted, with
arrows labelled by intent and protocol, so they can see where prompts and documents are stored and
processed. And a Deployment diagram placing those containers inside their VPC versus your environment,
with the trust boundary drawn and the flows that cross it marked. One picture would have to mix the
three levels, which is exactly what makes reviewers unsure what each box is and which way data flows.

## Quiz
### Q: In the C4 model, what does "container" mean?
- [ ] A Docker or OCI container image
- [x] A separately runnable or deployable unit such as an application, database, queue or model server
- [ ] A bounded context from domain-driven design
- [ ] A cloud account or Kubernetes namespace
> C4's container predates Docker and is broader: anything that runs or is deployed on its own, including
> managed databases and queues. Bounded contexts and namespaces are different concepts that may or may
> not align with containers.

### Q: Why does the C4 model insist on labelling each relationship with its intent and direction?
- [ ] So that diagram tools can auto-layout more efficiently
- [ ] Because UML requires it for sequence diagrams
- [x] So readers never have to infer which way data flows or what one element does to another
- [ ] To make diagrams compatible with Structurizr's licensing model
> An unlabelled line can mean calls, deploys to, reads from or owns, and a two-headed arrow hides who
> initiates. In security and data-flow reviews that ambiguity is precisely the question being asked.

### Q: Which view best shows a customer where its data crosses from its environment into the vendor's?
- [ ] A Component diagram of the API
- [ ] A Code diagram generated from the data model
- [x] A Deployment diagram placing containers on infrastructure with the trust boundary drawn
- [ ] A System Landscape diagram of all the vendor's products
> Only the deployment view maps containers onto environments, so it is the one where "inside your VPC"
> versus "in our cloud" and the flows between them become visible. Component and code views are below
> that level of concern.
