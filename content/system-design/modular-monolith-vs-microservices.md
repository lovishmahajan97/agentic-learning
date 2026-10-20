---
id: modular-monolith-vs-microservices
module: system-design
name: Modular monolith vs microservices
kind: decision
difficulty: core
est_minutes: 16
status: crisp
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
The real decision is where *deployment and ownership* boundaries go. Start with a modular monolith
(enforced module APIs, owned data) and turn a boundary into a network boundary only when it has a
proven, independent reason: scaling, team or blast radius.

## Why it exists
- **Failure mode 1: the big ball of mud.** A monolith with no internal structure: any table joined from
  anywhere, any function calls any other. Easy to deploy, impossible to change; every release needs a full
  regression and nobody can say which code owns which data.
- **Failure mode 2: the distributed monolith.** Services split too early or along the wrong lines. One
  feature touches four repositories, releases ship in lock-step, and one service's schema change breaks two
  others. You pay for the network (latency, retries, timeouts, serialisation, tracing) and get none of the
  independence.
- **Both naive answers skip the question.** Microservices answered failure mode 1 at many-team
  organisations; failure mode 2 is smaller teams copying the result without the cause. "Monoliths are
  legacy" and "microservices are best practice" both skip: *which boundaries need to be independently
  deployable, scalable or owned, today?*
- **AI systems sharpen it.** They genuinely mix different physics: a GPU-bound model server, a CPU-bound
  orchestration API, bursty batch ingestion and embedding, long-running agent workflows. That tempts teams
  to split everything on day one.
- **What the modular monolith buys.** Internal boundaries stay sharp without paying the network tax until
  a specific boundary earns it.

## How it works
### What a module boundary is inside one deployable
Four properties, each chosen to make later extraction cheap:

1. **A narrow public API.** Other modules import one facade (an `api.py`, an interface, a package's
   exported surface) and nothing else, *because* a service boundary is exactly a public API over a
   network. If the in-process API is already narrow, extraction is a transport change.
2. **Owned data.** Each module owns its tables (a schema per module, or at minimum a naming and ownership
   rule); no other module queries them directly, *because* a shared database is the coupling that makes
   services inseparable later. Cross-module reporting goes through the owning module's API or an
   event-fed read model.
3. **Events for side effects.** When module A needs B to react ("document ingested", "chat answered"), A
   publishes an in-process event instead of calling B's internals. The bus is a function call today and an
   outbox plus broker tomorrow; the contract does not change.
4. **Enforcement in CI.** Boundaries that live only in a wiki erode in weeks. `import-linter` (Python),
   ArchUnit (JVM), Nx module-boundary lint rules (TypeScript) and Packwerk (Ruby) fail the build when a
   module reaches into another's internals.

**What you keep from one process:**
- In-process calls cost microseconds, versus a network round trip of roughly a millisecond or more
  (illustrative order of magnitude).
- One transaction boundary when you need atomicity.
- One build, one deploy, one log stream and one debugger.

### What changes when a boundary becomes a service
The added problems are the same every time:

| Problem | What it forces |
|---|---|
| **Network and partial failure** | Calls time out, succeed without the caller knowing, or get retried, so every mutating call needs idempotency, timeouts and circuit breakers |
| **Data ownership becomes physical** | No shared database: an operation spanning two services becomes a saga with compensating actions, or an outbox-driven eventually consistent flow; joins become data pipelines |
| **Contracts become versioned** | Callers deploy on their own schedule, so the API must evolve backward-compatibly; contract tests replace the compiler |
| **Operational surface multiplies** | Per-service CI/CD, dashboards, alerts, on-call, secrets, distributed tracing with propagated correlation IDs, and usually a platform team to make it repeatable |

None of these are reasons never to split; they are the price, and it is paid **per boundary**.

### The arithmetic of a hop
The price compounds rather than adding up linearly:

- **Availability multiplies along a synchronous chain.** *n* services in series, each available a
  fraction *a*, succeed roughly *aⁿ* of the time.
  - Five services at 99.9% give 0.999⁵ ≈ 99.5%: the ~43 minutes of monthly downtime one service is allowed
    becomes ~3.6 hours for the path.
  - Real failures are correlated, so treat this as the shape rather than a forecast.
  - Five modules in one process fail together but do not multiply.
  - This is *why* service designs lean on async messaging, caches and fallbacks: each cuts a link out of
    the synchronous chain.
- **Fan-out amplifies tail latency.** A request that calls *n* services in parallel and waits for all is
  slow whenever any one is.
  - If each call exceeds its own p99 1% of the time, the request exceeds it with probability 1 − 0.99ⁿ,
    about 10% at *n* = 10. The callees' p99 becomes the caller's p90 (Dean and Barroso's "The Tail at
    Scale" is the classic treatment).
  - Standard mitigations are timeouts with partial results and hedged requests, both extra code a modular
    monolith never needs.

### The reasons that justify paying it
A boundary is worth a service when at least one holds and the modular monolith cannot deliver it:

- **Different scaling or hardware profile.** A vLLM model server needs GPUs and scales on tokens per
  second; the orchestration API scales on request concurrency on cheap CPU nodes. Co-deploying wastes the
  GPU.
- **Different failure domain.** An ingestion worker that can OOM on a 400-page PDF must not take the chat
  API down with it.
- **Different team and release cadence.** Conway's law is descriptive: when two teams own two things,
  their interface becomes a service boundary whether planned or not. The "inverse Conway manoeuvre" runs it
  the other way: shape teams around the boundaries you want, then split.
- **Different security or compliance envelope.** A component handling raw PII or running untrusted agent
  code may need its own network segment and credentials.
- **Different runtime.** Python for the ML path and TypeScript for the product surface is a common and
  legitimate split.

**The middle option many teams miss: one codebase, several deployables.** The same artifact runs as
`api`, `worker` or `scheduler` depending on its entrypoint. Processes are isolated and scale
independently, but there is one build, one set of contracts and no network between modules that do not
need it.

### Extraction when it is time
Because the module already has a facade, owned data and events, extraction follows a fixed path:

1. Put the facade behind an interface.
2. Implement a remote client with the same interface.
3. Route a percentage of traffic to the new service (strangler pattern).
4. Move the tables.
5. Delete the in-process implementation.

Contract tests written against the facade keep passing throughout.

## Examples
### Enforced module boundary in Python
`chat` depends only on retrieval's public contract, side effects go through an in-process event bus, and
CI forbids reaching into retrieval internals. Extracting retrieval later means writing a second
`Retriever` implementation that speaks HTTP; `ChatService` does not change.

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

### Hop calculator: what a proposed split costs
Run this before a design review to put numbers on a synchronous chain and a parallel fan-out.

```python
MINUTES_PER_MONTH = 30 * 24 * 60   # 43,200

def chain(per_service: float, hops: int) -> tuple[float, float]:
    avail = per_service ** hops                       # independent failures: the optimistic shape
    downtime_h = (1 - avail) * MINUTES_PER_MONTH / 60
    return avail, downtime_h

def fanout_slow(n: int, p_slow: float = 0.01) -> float:
    return 1 - (1 - p_slow) ** n                      # P(at least one callee beyond its p99)

for hops in (1, 3, 5, 10):
    a, h = chain(0.999, hops)
    print(f"{hops:>2} hops: {a:.3%} available, ~{h:.1f} h/month down, "
          f"fan-out slow {fanout_slow(hops):.1%}")
#  1 hops: 99.900% available, ~0.7 h/month down, fan-out slow 1.0%
#  5 hops: 99.501% available, ~3.6 h/month down, fan-out slow 4.9%
# 10 hops: 99.004% available, ~7.2 h/month down, fan-out slow 9.6%
```

### Worked scenario: six engineers, seven proposed services
A RAG startup proposes separate services for ingestion, chunking, embedding, retrieval, chat, tenancy and
evaluation.

| Question | Answer | Result |
|---|---|---|
| Different hardware? | Only the self-hosted embedding model (GPU) | Own serving process |
| Different failure domain? | Ingestion is bursty and can OOM on bad PDFs | `worker` deployable, same artifact |
| Different team? | No: one team of six | No service boundary |
| Already remote? | The hosted LLM | Already behind HTTP |

Outcome: three runtime units instead of seven, one build, and written triggers (second team, different
data store) for any further split.

## Production patterns
- **Boundary contracts in CI.** Use when a codebase has more than a couple of modules. Add one
  `import-linter` `forbidden` contract per module (as above) and run `lint-imports` as a required check.
  Monitor the number of contract exceptions; a growing ignore list means the boundary is wrong.
- **One image, many entrypoints.** Use when roles need isolation but share code. Build one container image
  and start it with a different command per role (`api`, `worker`, `scheduler`), each with its own replica
  count and resource limits. Monitor per-role CPU, memory and restart counts.
- **Strangler routing for extraction.** Use when a module has earned a service. Put the remote client behind
  the same `Retriever` interface and route a small, rising percentage of calls to it with a feature flag.
  Compare error rate and p99 latency between the two paths before moving the tables.
- **Separate the model-serving tier from day one.** Use when you self-host models. Run the model server
  (for example vLLM) as its own deployment on GPU nodes and call it over HTTP from the API. Monitor GPU
  utilisation and queue depth separately from API concurrency.
- **Track the distributed-monolith signal.** Count coordinated multi-service deploys and repositories
  touched per feature from your deploy and PR history. A rising count is the earliest sign a boundary
  provides no independence.

## Trade-offs
- **One deploy, one transaction vs shared limits.** A modular monolith gives in-process latency and one
  transaction, but every module shares one process's resource limits and release train. It flips once a
  module needs different hardware or a team needs to ship without waiting for others.
- **Independence vs per-boundary tax.** Microservices give independent scaling and failure isolation, but
  each boundary adds network failure handling, versioned contracts and eventual consistency. Worth it only
  for boundaries whose independence you will actually exercise.
- **Enforced boundaries vs friction.** They cost up-front design and occasional friction when a quick
  cross-module query is forbidden. That is cheaper than untangling a shared database later, unless the
  product is a short-lived prototype that will be thrown away.
- **Several deployables vs coupled versions.** "One codebase, several deployables" keeps contracts in one
  place while isolating processes, but couples release versions across roles. It becomes a problem when
  roles need genuinely different dependency sets (for example CUDA libraries only in the serving role).

## Pitfalls
- **Splitting by technical layer.** An "LLM service" and a "database service" instead of business
  capabilities. Symptom: every user-facing change touches every service. Fix: split by capability.
- **A shared database behind "separate" services.** Symptom: a schema migration in one repository breaks
  another team's deploy with no compiler to warn anyone. Fix: one owner per table, access via API or events.
- **"Modular" without CI enforcement.** Symptom: six months later the dependency graph is a single strongly
  connected component. Fix: boundary lint as a required check.
- **Splitting because components *feel* different.** Then most requests fan out across three services.
  Symptom: p99 latency dominated by hop count and retries, and incidents that need three on-call rotations
  to debug.
- **Model server as "just another module" of the API process.** Symptom: a GPU box running web handlers at
  5% GPU utilisation, or an API deploy that evicts a loaded model. Fix: separate serving tier.

## When to use
- **Default:** a modular monolith for a new AI product. One Python codebase with enforced modules for
  ingestion, retrieval, orchestration, evaluation and tenancy, run as separate processes (`api`, `worker`)
  from the same artifact.
- **Separate from the start:** the model-serving tier when you self-host, because its hardware and scaling
  profile are provably different.
- **Add a service boundary when:** you can name the specific independence needed (team, scaling, blast
  radius, compliance) and have measured that the monolith cannot provide it.
- **Measure before and after:** deploy frequency per module, change failure rate, p99 latency per request
  path, synchronous hops on the critical path, and repositories a typical feature touches. A rising count of
  coordinated multi-service deploys is the earliest sign of a distributed monolith.
- **Interviews:** the expected answer is not "monolith" or "microservices" but a sequence: which
  boundaries, in what order, justified by which observed pressure, and how extraction stays reversible.

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
