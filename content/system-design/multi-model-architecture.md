---
id: multi-model-architecture
module: system-design
name: Multi-model architecture
kind: pattern
difficulty: core
est_minutes: 14
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
No single model is the best embedder, reranker, classifier, generator, vision model and judge at once,
and the best model for each job is rarely the cheapest. A multi-model architecture decomposes the system
into stages, each served by the model that fits that stage's quality, latency and cost budget, joined by
explicit typed contracts. The price is coupling: every stage's output is the next stage's input
distribution, so models have to be versioned and evaluated together, not one at a time.

## Why it exists
Early LLM applications were one prompt and one model. Production systems are not: a request passes
through a safety classifier, PII redaction, query rewriting, embedding, retrieval, reranking, generation,
structured extraction, perhaps a judge, and sometimes speech or vision on the way in or out. Pushing all
of that through one frontier generator fails three ways. Cost: embedding a hundred thousand document
chunks through a generative model is nonsense when an embedding model does it for a tiny fraction of the
price. Capability: a chat model does not emit vectors, and a prompted generalist ranks query–document
pairs worse and far more expensively than a cross-encoder trained for exactly that. Latency: every extra
job on the critical path is another multi-second call.

The opposite naive design, "best model for every job from whichever vendor, wired up ad hoc", fails on
operability. Each vendor brings its own authentication, limits, failure modes and tokeniser; nobody can
say which versions are in production; and when quality drops there is no way to tell which stage
regressed. The architecture exists to get the benefits of specialisation while keeping the system one
thing you can reason about: a governed portfolio of models, stage contracts, shared telemetry, and an
evaluation that runs per stage *and* end to end.

## How it works
### Decompose by job, not by vendor
List the jobs a request needs. For each, record the output type (vector, label, score, free text,
structured object), quality sensitivity, volume, latency budget and data sensitivity. The mapping then
mostly writes itself: high-volume narrow outputs go to small specialised models, often self-hosted;
low-volume open-ended generation goes to a frontier model; vectors go to an embedding model; pairwise
relevance goes to a cross-encoder reranker; safety screening goes to a fast classifier that runs on every
request, with a larger model consulted only for ambiguous cases. Routing within a job (see model
routing / cascading) is orthogonal to decomposition across jobs.

### Patterns of composition
1. **Pipeline.** Guard → rewrite → embed → retrieve → rerank → generate → verify. Stages that do not
   depend on each other (guard and embed) run concurrently.
2. **Generator plus verifier.** A second model checks grounding, schema or policy. Inline when it gates
   the response; sampled and asynchronous when it feeds monitoring.
3. **Planner plus executors.** A strong model decomposes a task and cheap models carry out subtasks; the
   backbone of most agent designs.
4. **Ensemble.** Several models answer and a selector or vote picks; expensive, reserved for high-stakes
   decisions where the extra calls are cheaper than a mistake.

### Contracts between stages
Each stage has a typed, versioned input and output schema (a Pydantic or Zod model, not free text), and
each stage records the model id and version it used in the trace. This is not bureaucracy: the next stage
was tuned against *this* stage's output distribution. Swap the query rewriter and the embedder sees
different text; swap the embedding model and every stored vector is in a different space; swap the
generator and the citation format the verifier parses may change. Typed contracts make the drift
detectable at the boundary instead of three stages downstream, and versions make it attributable.

### Budgets compose, tails do not average
Latency adds along the serial path, so parallelise what you can and budget with tail numbers: the p99 of a
sum of four stages is at least the largest single-stage p99 and can exceed the sum of all four p99s,
because tail events in different stages combine. Cost adds per request; find the dominant stage, which is
usually generation but is sometimes a reranker scoring 50 candidates with a large cross-encoder. Failure
is per stage: each needs its own timeout, fallback and degraded mode (drop the reranker, skip the judge).

### Evaluate per stage and end to end
Component metrics (recall@k for retrieval, precision for the guard, schema pass rate for extraction) tell
you *which* stage moved; the end-task metric tells you whether it mattered. You need both *because* a
stage can improve on its own metric and hurt the task: a reranker that prefers long passages raises
nDCG and pushes the generator past its useful context. Any model change ships behind a flag and a rerun of
both evaluations.

### Run it as a portfolio
A registry of approved models with versions, owners and deprecation dates; a gateway as the single egress
so cost and usage are attributable per stage; and a rule that a stage upgrade is a release, with a
canary, not a config edit.

## Example
A typed pipeline skeleton: each stage names its model, records timing and model version into a trace,
independent stages run concurrently, and the judge runs off the hot path on a sample. `classify`,
`embed`, `rerank`, `generate`, `judge` and `vector_search` are thin adapters around whatever providers
or servers you use.

```python
import asyncio, time, random
from dataclasses import dataclass, field

MODELS = {"guard": "safety-classifier-v3", "embed": "text-embed-v2",
          "rerank": "cross-encoder-v1", "generate": "frontier-chat-2026-06", "judge": "small-chat"}

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
        asyncio.create_task(stage("judge", judge, trace, question=question, answer=draft, context=top))
    return {"answer": draft, "sources": [d.id for d in top], "trace": trace.stages}
```

The trace is what makes the architecture debuggable: when quality drops you can group judge scores by the
`generate` model version, or latency by stage, instead of guessing.

## Trade-offs
- Each stage gets a right-sized model for quality and cost, but there are more moving parts, vendors and
  versions to manage; for low-volume products one strong model's simplicity can win outright.
- Stages can be upgraded independently, yet hidden coupling means a swap is safe only with an end-to-end
  eval; the independence evaporates when stages have been tuned to each other's quirks.
- Running independent stages concurrently cuts latency, but raises peak concurrency and cost during
  bursts, and complicates per-stage rate-limit budgeting.
- Vendor diversity gives best-of-breed quality and resilience, but every new processor needs a security
  review and a data-processing agreement; in regulated settings fewer vendors is the better trade.
- An inline verifier raises the quality floor on every request at a latency and cost premium; a sampled
  asynchronous judge is nearly free but only catches problems after users have seen them.

## Pitfalls
- Upgrading the embedding model without re-indexing; the symptom is retrieval recall collapsing because
  query vectors and stored document vectors now live in different spaces.
- Budgeting latency with averages; the symptom is an end-to-end p99 far above the sum of stage p50s
  while every stage looks healthy on its own dashboard.
- No per-stage telemetry; the symptom is "the answers got worse" with no way to tell whether the
  rewriter, retriever, reranker or generator regressed.
- Passing free text between stages instead of typed objects; the symptom is parse failures after a
  model update and prompt-injection text riding from one stage into the next.
- Running an LLM judge inline on all traffic; the symptom is cost roughly doubling and latency rising
  with no gain the product can show, when a 5% sample would have caught the same regressions.
- Treating two vendors' "same" capability as interchangeable; the symptom is dimension mismatches,
  silent truncation at a different token limit, or changed tool-call formats after a swap.

## When to use
Any system beyond a prototype is already multi-model; the question is whether the decomposition is
explicit. Make it explicit as soon as you have more than one model class in the path, or more than one
team touching the pipeline. Measure cost and latency per stage (p50, p95, p99), each stage's component
metric and the end-task metric, and alert on divergence between them. Resist adding a stage without a
measured gain; each one is a dependency with its own failure mode. In interviews, draw the pipeline, name
the model class and budget for each stage, and explain how you would localise a regression after an
upgrade. That coupling-and-eval story is what distinguishes architecture from a diagram.

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
> combine, and by a union-bound argument the sum of the p99s is only about the p96 of the total, so the
> true p99 can be higher still.
