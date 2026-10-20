---
id: multi-model-architecture
module: system-design
name: Multi-model architecture
kind: pattern
difficulty: core
est_minutes: 14
status: crisp
related:
  - system-design/model-portfolio
  - system-design/model-routing-cascading
  - system-design/rag-architecture
  - system-design/agentic-architecture
  - rag-retrieval/reranking
  - evals/llm-as-a-judge
  - infra-capacity-economics/latency-budgets
  - observability/tracing
---

## TL;DR
Split the system into stages, each served by the model that fits its quality, latency and cost budget,
joined by typed contracts. The price is coupling: each stage's output is the next one's input, so version
and evaluate models together.

## Why it exists
- **Production requests pass through many jobs.** Safety classifier, PII redaction, query rewriting,
  embedding, retrieval, reranking, generation, structured extraction, perhaps a judge, sometimes speech or
  vision. Early LLM apps were one prompt and one model; production systems are not.
- **No single model is best at every job,** and the best model for each job is rarely the cheapest. Pushing
  everything through one frontier generator fails three ways:
  - **Cost:** embedding a hundred thousand chunks through a generative model is nonsense when an embedding
    model does it for a tiny fraction of the price.
  - **Capability:** a chat model does not emit vectors, and a prompted generalist ranks query–document pairs
    worse and far more expensively than a cross-encoder trained for exactly that.
  - **Latency:** every extra job on the critical path is another multi-second call.
- **The opposite naive design fails on operability.** "Best model for every job from whichever vendor,
  wired up ad hoc": each vendor brings its own authentication, limits, failure modes and tokeniser; nobody
  knows which versions are in production; and when quality drops nobody can tell which stage regressed.
- **What the architecture buys:** specialisation while the system stays one thing you can reason about: a
  governed portfolio of models, stage contracts, shared telemetry, and evaluation per stage *and* end to
  end.

## How it works
### Decompose by job, not by vendor
For each job a request needs, record output type (vector, label, score, free text, structured object),
quality sensitivity, volume, latency budget and data sensitivity. The mapping mostly writes itself:

| Job shape | Model class |
|---|---|
| High-volume, narrow output | Small specialised model, often self-hosted |
| Low-volume, open-ended generation | Frontier model |
| Vectors | Embedding model |
| Pairwise relevance | Cross-encoder reranker |
| Safety screening on every request | Fast classifier; a larger model only for ambiguous cases |

Routing within a job (see model routing / cascading) is orthogonal to decomposition across jobs.

### Patterns of composition
1. **Pipeline.** Guard → rewrite → embed → retrieve → rerank → generate → verify.
   - Independent stages (guard and embed) run concurrently.
   - A gate that almost always passes can run *speculatively*: start retrieval, or even generation, while
     the guard decides, and discard the work if it blocks.
   - You pay wasted calls on the few blocked requests to remove the guard's latency from every other one.
   - Unsafe only if the speculative stage has side effects or streams to the user before the gate passes.
2. **Generator plus verifier.** A second model checks grounding, schema or policy. Inline when it gates the
   response; sampled and asynchronous when it feeds monitoring.
3. **Planner plus executors.** A strong model decomposes a task and cheap models carry out subtasks; the
   backbone of most agent designs.
4. **Ensemble.** Several models answer and a selector or vote picks. Expensive; reserved for high-stakes
   decisions where the extra calls are cheaper than a mistake.

### Contracts between stages
- **Typed, versioned schemas:** each stage's input and output is a Pydantic or Zod model, not free text.
- **Model id and version in the trace** for every stage.
- **Why it is not bureaucracy:** the next stage was tuned against *this* stage's output distribution.
  - Swap the query rewriter and the embedder sees different text.
  - Swap the embedding model and every stored vector is in a different space.
  - Swap the generator and the citation format the verifier parses may change.
- **Typed contracts make drift detectable** at the boundary instead of three stages downstream; versions
  make it attributable.

### Budgets compose, tails do not average
- **Latency adds along the serial path,** so parallelise what you can and budget with tail numbers.
- **Bounds:** the p99 of a sum of four stages is at least the largest single-stage p99 and can exceed the
  sum of all four p99s, because tail events in different stages combine. The sum of the p99s is only
  guaranteed to be at least the p96 of the total, by a union bound over the four stages.
- **Independent slowness:** the sum of the p99s is a pessimistic budget, because tails rarely coincide.
- **Correlated slowness** (two stages on one GPU pool or one provider, a retry policy at every stage, a
  shared overloaded network path) drives the real p99 towards or past that sum.
- **Rule:** budget with the sum, then measure the end-to-end distribution.
- **Cost adds per request.** Find the dominant stage: usually generation, sometimes a reranker scoring 50
  candidates with a large cross-encoder.
- **Failure is per stage.** Each needs its own timeout, fallback and degraded mode (drop the reranker,
  skip the judge).

### Evaluate per stage and end to end
| Level | Examples | Tells you |
|---|---|---|
| Component | recall@k for retrieval, precision for the guard, schema pass rate for extraction | *Which* stage moved |
| End task | Task success, judged answer quality | Whether it mattered |

- **You need both** *because* a stage can improve on its own metric and hurt the task: a reranker that
  prefers long passages raises nDCG and pushes the generator past its useful context.
- **Any model change** ships behind a flag and a rerun of both evaluations.

### Run it as a portfolio
- **A registry** of approved models with versions, owners and deprecation dates.
- **A gateway as the single egress,** so cost and usage are attributable per stage.
- **A stage upgrade is a release,** with a canary, not a config edit.

## Diagram
A typical RAG pipeline as stages: guard and embed run concurrently, each later stage depends on the
previous one's typed output, and the sampled judge sits off the hot path.

```mermaid
flowchart LR
    Q["User question"] --> G["Guard: fast safety classifier"]
    Q --> E["Embed: embedding model"]
    G -->|"verdict allow or block"| GATE{"Allowed?"}
    E -->|"query vector"| VS[("Vector search, k=50")]
    GATE -->|"block"| REF["Refusal"]
    GATE -->|"allow"| RR["Rerank: cross-encoder, top 5"]
    VS -->|"50 candidates"| RR
    RR -->|"typed passages"| GEN["Generate: frontier model"]
    GEN --> A["Answer plus sources"]
    GEN -.->|"5% sample, async"| J["Judge: small model"]
    J -.->|"scores by model version"| T[("Trace and per-stage metrics")]
```

## Examples
### Typed pipeline skeleton with a trace
Each stage names its model and records timing and version; independent stages run concurrently; the judge
runs off the hot path on a sample. `classify`, `embed`, `rerank`, `generate`, `judge` and `vector_search`
are thin adapters around whatever providers or servers you use.

```python
import asyncio, time, random
from dataclasses import dataclass, field

MODELS = {"guard": "safety-classifier-v3", "embed": "text-embed-v2",
          "rerank": "cross-encoder-v1", "generate": "frontier-chat-2026-06", "judge": "small-chat"}
BACKGROUND = set()                 # references to fire-and-forget tasks (asyncio only keeps weak ones)

@dataclass
class Trace:                       # one record per request: which model served which stage, how long
    stages: list = field(default_factory=list)
    def add(self, stage, model, t0):
        self.stages.append({"stage": stage, "model": model, "ms": round((time.perf_counter() - t0) * 1000)})

async def stage(name, fn, trace, **kw):
    t0 = time.perf_counter()
    out = await fn(MODELS[name], **kw)              # every adapter takes the model id as its first argument
    trace.add(name, MODELS[name], t0)
    return out

async def answer(question):
    trace = Trace()
    verdict, qvec = await asyncio.gather(          # guard and embedding are independent: run concurrently
        stage("guard", classify, trace, text=question),
        stage("embed", embed, trace, text=question))
    if verdict != "allow":
        return {"answer": "I can't help with that request.", "trace": trace.stages}
    candidates = await vector_search(qvec, k=50)                                 # a database call, not a model
    top = await stage("rerank", rerank, trace, query=question, docs=candidates, k=5)
    draft = await stage("generate", generate, trace, question=question, context=top)
    if random.random() < 0.05:                                                   # sampled judge, off the hot path
        t = asyncio.create_task(stage("judge", judge, trace, question=question, answer=draft, context=top))
        BACKGROUND.add(t); t.add_done_callback(BACKGROUND.discard)              # keep a reference or it may be GC'd
    return {"answer": draft, "sources": [d.id for d in top], "trace": trace.stages}
```

- **The trace makes it debuggable:** group judge scores by the `generate` model version, or latency by
  stage, instead of guessing.
- **Emit the trace late:** the sampled judge appends *after* the response returns, so emit the trace when
  background work finishes, not at return.

### Worked latency budget
Illustrative p95s, read off the structure:

| Stage | p95 | On the serial path? |
|---|---|---|
| Guard | 80 ms | Yes (parallel with embed, the longer of the two) |
| Embed | 40 ms | No, hidden behind guard: concurrency saved these 40 ms |
| Vector search | 30 ms | Yes |
| Rerank | 150 ms | Yes |
| Generation | 2.5 s | Yes, nine-tenths of the total |
| **Serial budget** | 80 + 30 + 150 + 2,500 ≈ 2.8 s | |

- **Stream the generator first.** It moves the user-visible number to time-to-first-token and does more for
  perceived latency than any change to the other stages.
- **Cost usually has the same shape,** until someone raises the rerank candidate count from 50 to 500 and
  the reranker becomes the dominant line.

### Why correlated slowness breaks the budget
A runnable simulation: four stages at 200 ms that occasionally take 1,500 ms.

```python
import random

def p(xs, q):
    xs = sorted(xs); return xs[int(q * (len(xs) - 1))]

def stage(slow):                              # 200 ms normally, 1,500 ms when slow
    return random.uniform(180, 220) + (1300 if slow else 0)

random.seed(0)
N, P_SLOW, STAGES = 100_000, 0.02, 4
indep, corr = [], []
for _ in range(N):
    indep.append(sum(stage(random.random() < P_SLOW) for _ in range(STAGES)))  # independent causes
    shared = random.random() < P_SLOW                                           # one shared GPU pool
    corr.append(sum(stage(shared) for _ in range(STAGES)))

one = [stage(random.random() < P_SLOW) for _ in range(N)]
print(f"single-stage p99 ~ {p(one, .99):,.0f} ms; sum of 4 p99s ~ {4 * p(one, .99):,.0f} ms")
print(f"end-to-end p99, independent slowness: {p(indep, .99):,.0f} ms")
print(f"end-to-end p99, correlated slowness:  {p(corr, .99):,.0f} ms")
# single-stage p99 ~ 1,499 ms; sum of 4 p99s ~ 5,997 ms
# independent: ~2,130 ms (tails rarely coincide); correlated: ~6,000 ms (the full sum)
```

## Production patterns
- **Model registry checked in CI.** Keep one file listing every stage's approved model id, version, owner
  and deprecation date; fail the build if code references a model not in it. Monitor days until each
  model's deprecation and the number of stages on unregistered or retired versions.
- **Single egress with stage tags.** Send every model call through the gateway with the stage name and
  pipeline version attached as request metadata, so spend and errors split by stage. Monitor cost per
  stage per request and which stage dominates.
- **Re-index behind an alias on embedder change.** Build a new index with the new embedding model, compare
  recall@k on the golden set, then switch a read alias in one step; never mix query and document vectors
  from different models. Monitor recall@k and the share of documents re-embedded.
- **Stage upgrades as canaries.** Put each stage's model id behind a flag, send a small share of traffic
  to the new version, and compare both the component metric and the end-task metric before widening.
  Monitor the two metrics side by side; divergence means hidden coupling.
- **Per-stage timeout with a degraded mode.** Give optional stages a hard timeout and a defined fallback
  (rerank times out, so keep vector-search order; the judge is skipped). Monitor degraded-mode rate per
  stage; a rising rate is an incident before users notice.
- **Sampled asynchronous judge.** Judge a fixed share (for example 5%) of production answers off the hot
  path and group scores by each stage's model version. Monitor judge score per version and judge
  agreement with periodic human labels.

## Trade-offs
- **Right-sized models vs moving parts.** Each stage gets a right-sized model for quality and cost, but
  there are more vendors and versions to manage; for low-volume products one strong model's simplicity can
  win outright.
- **Independent upgrades vs hidden coupling.** A swap is safe only with an end-to-end eval; independence
  evaporates when stages have been tuned to each other's quirks.
- **Concurrency vs burst cost.** Running independent stages concurrently cuts latency, but raises peak
  concurrency and cost during bursts, and complicates per-stage rate-limit budgeting.
- **Vendor diversity vs compliance load.** Best-of-breed quality and resilience, but every new processor
  needs a security review and a data-processing agreement; in regulated settings fewer vendors is the
  better trade.
- **Inline verifier vs sampled judge.** Inline raises the quality floor on every request at a latency and
  cost premium; a sampled asynchronous judge is nearly free but only catches problems after users have
  seen them.

## Pitfalls
- **Upgrading the embedding model without re-indexing.** Symptom: retrieval recall collapses because query
  vectors and stored document vectors now live in different spaces.
- **Budgeting latency with averages.** Symptom: an end-to-end p99 far above the sum of stage p50s while
  every stage looks healthy on its own dashboard.
- **No per-stage telemetry.** Symptom: "the answers got worse" with no way to tell whether the rewriter,
  retriever, reranker or generator regressed.
- **Free text between stages instead of typed objects.** Symptom: parse failures after a model update and
  prompt-injection text riding from one stage into the next.
- **An LLM judge inline on all traffic.** Symptom: cost roughly doubling and latency rising with no gain
  the product can show, when a 5% sample would have caught the same regressions.
- **Treating two vendors' "same" capability as interchangeable.** Symptom: dimension mismatches, silent
  truncation at a different token limit, or changed tool-call formats after a swap.

## When to use
- **Always, explicitly:** any system beyond a prototype is already multi-model; the question is whether
  the decomposition is explicit.
- **Make it explicit when** you have more than one model class in the path, or more than one team touching
  the pipeline.
- **Measure:** cost and latency per stage (p50, p95, p99), each stage's component metric and the end-task
  metric; alert on divergence between them.
- **Avoid** adding a stage without a measured gain; each one is a dependency with its own failure mode.
- **Interviews:** draw the pipeline, name the model class and budget per stage, and explain how you would
  localise a regression after an upgrade. That coupling-and-eval story distinguishes architecture from a
  diagram.

## Role lens
- **Engineer:** Implements typed stage adapters, records model versions in traces, writes component evals
  and makes sure independent stages actually run concurrently.
- **Architect:** Owns the portfolio: which model classes are approved for which jobs, the version and
  deprecation policy, and how the latency and cost budget is split across stages.
- **FDE:** Assembles the pipeline from models the customer has already approved, often under procurement
  limits, and explains why the reranker and the generator come from different vendors.

## Check yourself
**Q:** A RAG product swapped its generator for a newer model that benchmarks higher, and answer quality
dropped. How does thinking in stages explain this, and how would you diagnose it?
**A:** The generator is one stage whose prompt and context handling were tuned against the old model's
behaviour: citation format, response length, how it treated conflicting passages, how literally it
followed the system prompt. First confirm the other stages did not move: retrieval recall@5 and reranker
scores on the golden set should be unchanged, which isolates the regression to generation. Then compare
judge scores old versus new on the same fixed set, check structured-output and citation parse failure
rates, check for context truncation from a different tokeniser or limit, and read a sample of
disagreements. The fix is usually prompt re-tuning plus an end-to-end eval before a canary rollout. The
lesson is procedural: a stage upgrade is a release that needs per-stage telemetry, end-to-end evaluation
and a feature flag, regardless of what the public benchmark says.

## Interview questions
### Why not run every job in the pipeline through one frontier model?
- **Cost:** embedding a hundred thousand chunks through a generative model costs far more than an
  embedding model built for it.
- **Capability:** a chat model does not emit vectors, and a prompted generalist ranks query–document pairs
  worse and more expensively than a cross-encoder.
- **Latency:** every extra job on the critical path is another multi-second call, so specialised small
  models keep the serial path short.

### Design the model stages for a support assistant with retrieval and a safety check.
- **Decompose by job:** guard (fast classifier), embed (embedding model), retrieve (vector search), rerank
  (cross-encoder), generate (frontier model), sampled judge (small model, asynchronous).
- **Compose:** run guard and embed concurrently, or start retrieval speculatively while the guard decides,
  since retrieval has no side effects.
- **Contracts:** typed, versioned schemas between stages, with model id and version in the trace for every
  stage.

### How would you budget end-to-end latency across four serial stages?
- **Budget with the sum of the stage tail numbers,** then measure the end-to-end distribution, because
  tails do not average.
- **Correlated slowness matters:** stages sharing one GPU pool or provider, or retries at every stage, push
  the real p99 towards or past that sum.
- **Find the dominant stage:** usually generation, so stream it to move the user-visible number to
  time-to-first-token.

### When would you deliberately avoid adding another model stage?
- **When there is no measured gain** on the end-task metric, because each stage is a dependency with its own
  failure mode, timeout and version to govern.
- **For low-volume products,** where one strong model's simplicity can win outright.
- **In regulated settings,** where each new vendor needs a security review and a data-processing agreement,
  so fewer vendors is the better trade.

### You must change the embedding model in production. What is your rollout plan?
- **Re-index, never mix spaces:** query vectors and stored document vectors from different models make
  recall collapse.
- **Build the new index alongside,** compare recall@k on the golden set, then run the end-task eval, since a
  better component metric can still hurt the task.
- **Switch a read alias in one step** behind a flag and canary, and keep the old index until the end-task
  metric holds.

## Quiz
### Q: Why do multi-model systems need typed, versioned contracts between stages?
- [ ] Because providers require JSON between calls
- [x] Because each stage's output is the next stage's input distribution, so drift must be detectable at the boundary and attributable to a version
- [ ] Because typed contracts reduce token usage
- [ ] Because it allows all stages to share one model
> The coupling between stages is the central risk of the pattern. Schemas catch shape changes
> immediately and versions tell you which upgrade caused a downstream regression; neither has anything to
> do with provider requirements or token counts.

### Q: What is the strongest reason to rerank with a cross-encoder rather than asking the generator to rank candidates?
- [ ] Generators cannot read more than five documents
- [x] A cross-encoder scores query–document pairs jointly, cheaply and well, and leaves the generator's context for answering
- [ ] Rerankers remove the need for embeddings entirely
- [ ] Rerankers are always more accurate than generators at every task
> Specialisation is the point: the cross-encoder is trained for pairwise relevance and costs a fraction
> per pair, so the expensive model sees only the best few passages. Embeddings are still needed for the
> first-stage retrieval.

### Q: Four serial model stages each have p50 = 200 ms and p99 = 1 s. What can you say about the end-to-end p99?
- [ ] It is 800 ms, the sum of the p50s
- [ ] It is 1 s, since the slowest stage dominates
- [ ] It is exactly 4 s, the sum of the p99s
- [x] It is at least 1 s, may exceed 4 s, and cannot be derived from the p50s; measure it end to end
> Latencies add pointwise, so the total is at least as slow as any stage. Tail events in different stages
> combine: the total can only exceed the sum of the p99s when at least one stage exceeds its own p99, which
> by a union bound happens on at most about 4% of requests, so the sum of the p99s is guaranteed only to be
> at least the p96 of the total, and the true p99 can be higher still.
