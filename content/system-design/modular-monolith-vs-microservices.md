---
id: modular-monolith-vs-microservices
module: system-design
name: Modular monolith vs microservices
kind: decision
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - system-design/domain-driven-design-and-bounded-contexts
  - system-design/event-driven-architecture
  - system-design/saga-pattern-and-distributed-transactions
  - system-design/outbox-inbox-patterns
  - system-design/distributed-systems
  - system-design/adrs
  - system-design/trade-off-analysis
  - observability/distributed-tracing-across-services
sources:
  - Fowler, 2015 — MonolithFirst
  - Newman, 2021 — Building Microservices (2nd ed.)
  - Richardson, 2018 — Microservices Patterns
  - Conway, 1968 — How Do Committees Invent?
---

## TL;DR
The real decision is where your *deployment and ownership* boundaries go, not how your code is
organised. A modular monolith is one deployable whose internal modules have enforced public APIs and
own their data; microservices turn those same boundaries into network boundaries, buying independent
deployment, scaling and failure isolation at the price of distributed-systems problems (partial failure,
eventual consistency, operational surface). Start modular, and split a boundary only when it has a
proven, independent reason to exist: a different scaling profile, a different team, or a different blast
radius.

## Why it exists
Two failure modes forced this decision into existence. The first is the **big ball of mud**: a monolith
with no internal structure, where any table can be joined from anywhere and any function can call any
other. It is easy to deploy and impossible to change; every release needs a full regression and nobody
can say which code owns which data. The second is the **distributed monolith**: a system split into
services too early or along the wrong lines, so that a single feature touches four repositories, releases
must be coordinated in lock-step, and a database schema change in one service breaks two others. You pay
for the network (latency, retries, timeouts, serialisation, tracing) and get none of the independence.

Microservices were a response to the first failure mode at organisations with many teams; the second
failure mode is what happens when smaller teams copy the result without the cause. The naive answers,
"monoliths are legacy" and "microservices are best practice", both skip the question that actually
matters: *which boundaries in this system need to be independently deployable, scalable or owned, today?*

AI systems make this sharper, because they genuinely contain components with different physics: a
GPU-bound model server, a CPU-bound orchestration API, bursty batch ingestion and embedding, and
long-running agent workflows. That tempts teams to split everything on day one. The modular monolith is
the discipline that lets you keep the internal boundaries sharp without paying the network tax until a
specific boundary earns it.

## How it works
### What a module boundary is inside one deployable
A module in a modular monolith has four properties, each chosen to make later extraction cheap:

1. **A narrow public API.** Other modules import one facade (an `api.py`, an interface, a package's
   exported surface) and nothing else. This exists *because* a service boundary is exactly a public API
   over a network; if the in-process API is already narrow, extraction is a transport change.
2. **Owned data.** Each module owns its tables (a schema per module, or at minimum a naming and ownership
   rule) and no other module queries them directly. This matters *because* a shared database is the
   coupling that makes services inseparable later; cross-module reporting goes through the owning
   module's API or an event-fed read model.
3. **Events for side effects.** When module A needs module B to react ("document ingested", "chat
   answered"), it publishes an in-process event rather than calling B's internals. The bus is a function
   call today and an outbox plus broker tomorrow; the contract does not change.
4. **Enforcement in CI.** Boundaries that live only in a wiki erode in weeks. Tools such as
   `import-linter` (Python), ArchUnit (JVM), Nx module-boundary lint rules (TypeScript) and Packwerk
   (Ruby) fail the build when a module reaches into another's internals.

In exchange you keep what a single process gives you for free: in-process calls that cost microseconds
rather than a network round trip of roughly a millisecond or more (illustrative order of magnitude),
one transaction boundary when you need atomicity, one build, one deploy, one log stream and one
debugger.

### What changes when a boundary becomes a service
Turning a module boundary into a service boundary adds a fixed set of problems, and the list is the
same every time:

- **Network and partial failure.** Calls can time out, succeed without the caller knowing, or be
  retried; every mutating call therefore needs idempotency, timeouts and circuit breakers.
- **Data ownership becomes physical.** With no shared database, a business operation spanning two
  services becomes a saga with compensating actions, or an outbox-driven eventually consistent flow.
  Joins become data pipelines.
- **Contracts become versioned.** The API must evolve backward-compatibly because callers deploy on
  their own schedule; contract tests replace the compiler.
- **Operational surface multiplies.** Per-service CI/CD, dashboards, alerts, on-call, secrets,
  distributed tracing with propagated correlation IDs, and usually a platform team to make all of it
  repeatable.

None of these are reasons never to split; they are the price, and the price is paid per boundary.

### The reasons that justify paying it
A boundary is worth a service when at least one of these holds and the modular monolith cannot deliver
it:

- **Different scaling or hardware profile.** A vLLM model server needs GPUs and scales on tokens per
  second; the orchestration API scales on request concurrency on cheap CPU nodes. Co-deploying them
  wastes the GPU.
- **Different failure domain.** An ingestion worker that can OOM on a 400-page PDF must not take the
  chat API down with it.
- **Different team and release cadence.** Conway's law is descriptive: when two teams own two things,
  their interface will become a service boundary whether or not you planned it.
- **Different security or compliance envelope.** A component that handles raw PII or runs untrusted
  agent code may need its own network segment and credentials.
- **Different runtime.** Python for the ML path and TypeScript for the product surface is a common and
  legitimate split.

Note the middle option many teams miss: **one codebase, several deployables**. The same artifact runs
as `api`, `worker` or `scheduler` depending on its entrypoint. Processes are isolated, scaling is
independent, but there is one build, one set of contracts and no network between modules that do not
need it.

### Extraction when it is time
Because the module already has a facade, owned data and events, extraction follows a fixed path:
put the facade behind an interface; implement a remote client with the same interface; route a
percentage of traffic to the new service (strangler pattern); move the tables; delete the in-process
implementation. Contract tests written against the facade keep passing throughout.

## Example
A Python layout in which `chat` depends only on the retrieval module's public contract, side effects go
through an in-process event bus, and CI forbids reaching into retrieval internals. Extracting retrieval
into a service later means writing a second `Retriever` implementation that speaks HTTP; `ChatService`
does not change.

```python
# app/retrieval/api.py — the only module other packages may import from retrieval
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class Passage:
    doc_id: str
    text: str
    score: float

class Retriever(Protocol):                      # the contract; unchanged by extraction
    def search(self, tenant_id: str, query: str, k: int = 8) -> list[Passage]: ...

def get_retriever() -> Retriever:
    from .service import PgVectorRetriever      # private: schema, SQL, embedding client
    return PgVectorRetriever()

# app/chat/service.py — depends on the contract, never on retrieval internals
from app.retrieval.api import Retriever, get_retriever
from app.events import publish                  # in-process today; outbox + broker later

class ChatService:
    def __init__(self, retriever: Retriever | None = None):
        self.retriever = retriever or get_retriever()

    def answer(self, tenant_id: str, question: str) -> str:
        passages = self.retriever.search(tenant_id, question)
        reply = compose_answer(question, passages)       # LLM call elided
        publish("chat.answered", {"tenant_id": tenant_id, "passages": len(passages)})
        return reply

# .importlinter — the build fails if chat imports retrieval's internals
# [importlinter:contract:retrieval-is-private]
# name = chat may only use retrieval.api
# type = forbidden
# source_modules = app.chat
# forbidden_modules = app.retrieval.service
# allow_indirect_imports = True
```

## Trade-offs
- A modular monolith gives one deploy, one transaction and in-process latency, but every module shares
  one process's resource limits and release train; that flips once a module needs different hardware
  or a team needs to ship without waiting for others.
- Microservices give independent scaling and failure isolation, but each boundary adds network failure
  handling, versioned contracts and eventual consistency; the price is worth it only for boundaries whose
  independence you will actually exercise.
- Enforced module boundaries cost up-front design and occasional friction when a quick cross-module
  query is forbidden; that cost is lower than untangling a shared database later, unless the product is
  a short-lived prototype that will be thrown away.
- "One codebase, several deployables" keeps contracts in one place while isolating processes, but it
  couples release versions across roles; that becomes a problem when roles need genuinely different
  dependency sets (for example CUDA libraries only in the serving role).

## Pitfalls
- Splitting along technical layers (an "LLM service", a "database service") instead of business
  capabilities; the symptom is that every user-facing change touches every service.
- Keeping a shared database behind "separate" services; the symptom is a schema migration in one
  repository breaking another team's deploy with no compiler to warn anyone.
- Declaring a monolith "modular" without CI enforcement; the symptom is a dependency graph that, six
  months later, is a single strongly connected component.
- Splitting early because the components *feel* different, then discovering that most requests fan out
  across three services; the symptom is p99 latency dominated by hop count and retries, and incidents
  that need three on-call rotations to debug.
- Treating the model server as "just another module" of the API process; the symptom is a GPU box
  running web handlers at 5% GPU utilisation, or an API deploy that evicts a loaded model.

## When to use
Default to a modular monolith for a new AI product: one Python codebase with enforced modules for
ingestion, retrieval, orchestration, evaluation and tenancy, run as separate processes (`api`, `worker`)
from the same artifact. Keep the model-serving tier separate from the start when you self-host, because
its hardware and scaling profile are provably different. Add a service boundary when you can name the
specific independence you need (team, scaling, blast radius, compliance) and have measured that the
monolith cannot provide it. Measure before and after: deploy frequency per module, change failure rate,
p99 latency per request path, and how many repositories a typical feature touches. In interviews, the
expected answer is not "monolith" or "microservices" but a sequence: which boundaries, in what order,
justified by which observed pressure, and how the extraction is kept reversible.

## Role lens
- **Engineer:** Writes the module facades, keeps the import-linter contracts green, and extracts a
  module into a service by swapping an adapter rather than rewriting callers.
- **Architect:** Owns the boundary map: which capabilities are modules, which are deployables, which
  are services, and the written criteria (scaling, team, blast radius) that promote one to the next.
- **FDE:** Fits the product into a customer's deployment constraints, often collapsing several services
  into one deployable for an on-prem or VPC install where a platform team does not exist.

## Check yourself
**Q:** A six-engineer startup is building a RAG product: document ingestion, a chat API, a self-hosted
embedding model and a hosted LLM. They propose seven microservices. How would you structure it and why?
**A:** I would start with one modular monolith in Python with enforced modules (ingestion, retrieval,
chat orchestration, tenancy, evaluation) and run it as two deployables from the same artifact: an `api`
process and a `worker` process for ingestion, because ingestion is bursty and can crash on bad
documents while chat must stay up. The embedding model gets its own serving process because it needs a
GPU and scales on throughput, not request concurrency. The hosted LLM is already a service behind an
HTTP boundary. That is three runtime units, not seven, with one build and one set of contracts. I would
write down the triggers for further splits, for example a second team taking ownership of ingestion or
retrieval needing a different data store, and keep module facades so extraction is an adapter swap.

## Quiz
### Q: What most distinguishes a modular monolith from an ordinary monolith?
- [ ] It is written in several programming languages
- [x] Modules expose narrow public APIs, own their data, and the boundaries are enforced by tooling
- [ ] It is deployed as several containers behind a load balancer
- [ ] It uses an event broker for all internal communication
> The defining property is enforced internal boundaries, including data ownership, inside one deployable.
> Multiple containers or a broker describe deployment and transport, which is exactly what the modular
> monolith avoids until a boundary needs it.

### Q: Which observation is the strongest evidence that a service boundary was drawn in the wrong place?
- [ ] The service has its own database
- [ ] The service is written in a different language from its callers
- [x] Most feature changes require coordinated deploys of this service and its callers
- [ ] The service receives more traffic than any other
> Lock-step deploys mean the boundary does not provide independence, which is the only thing a network
> boundary buys. A separate database and a different language are normal properties of a healthy
> service.

### Q: A team self-hosts an embedding model and runs it inside the same process as their FastAPI chat
API. What is the most likely consequence?
- [ ] Lower latency because no network hop exists, with no downside
- [x] Poor GPU utilisation and coupled scaling, because web concurrency and model throughput have different profiles
- [ ] The model cannot be loaded because FastAPI is single-threaded
- [ ] Requests must be made idempotent because of partial failure
> GPU serving scales on batched throughput while an API scales on connection concurrency; co-locating
> them wastes the expensive resource and makes every API deploy evict the model. Idempotency concerns
> arise from network boundaries, not from co-location.
