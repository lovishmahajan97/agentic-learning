---
id: evaluation-frameworks
module: llmops-platform
name: Evaluation frameworks
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - evals/eval-harness
  - evals/tooling
  - llmops-platform/golden-dataset-strategy
  - llmops-platform/reproducible-evaluations
  - llmops-platform/evaluation-gates-for-production-releases
  - evals/llm-as-a-judge
  - observability/langfuse
  - evals/benchmarking
sources:
  - Liang et al., 2022 — Holistic Evaluation of Language Models
  - Zheng et al., 2023 — Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena
  - Official docs: Promptfoo
  - Official docs: lm-evaluation-harness
---

## TL;DR
An evaluation framework turns "run the eval" from a script one engineer owns into a shared service with
five fixed interfaces: versioned datasets, a target adapter that calls the real application, versioned
scorers (deterministic, model-graded, human), a runner that pins configuration and stores every raw
output, and a results store that diffs any two runs item by item. It exists because a harness per team
produces numbers nobody else can compare; the framework makes an eval score a currency the whole
organisation accepts.

## Why it exists
The `evals/eval-harness` lesson explains the engine: dataset in, pinned configuration, raw outputs,
grades, report. The problem this lesson addresses appears the moment a second team builds its own engine.
Team A stores items as CSV with a `label` column and reports "87% pass"; team B keeps JSONL with a
`reference` field and reports "judge score 0.71". When the platform group asks whether the company can
move to a new model snapshot, every team re-runs something different, the results cannot be added up, and
the decision is made on anecdotes. Worse, the numbers are not even stable within one team: the scoring
rubric lives in a notebook, the dataset was edited last Tuesday, and the "baseline" was measured against
a model alias that has since been updated.

The naive alternatives each fail in a specific way. Evaluating in notebooks is not repeatable, so
Friday's number cannot be compared with Tuesday's. A spreadsheet of hand-checked outputs is honest but
stale within a sprint and cannot gate a pipeline. Public benchmarks (MMLU, HumanEval and friends) measure
the base model, not your prompt, retrieval, tools and guardrails, and the popular ones are widely
suspected to have leaked into frontier-model training data. Buying a dashboard and declaring the problem solved fails more quietly: without dataset
discipline, scorers calibrated to your task and a runner that pins every version, the dashboard renders
noise with good typography.

What the framework actually contributes is standardisation of the interfaces, not cleverness in any one
of them. When datasets, targets, scorers, runs and results have one schema, pieces become swappable (a
new judge model, a new dataset version), runs become comparable across teams and weeks, caching makes
per-PR evaluation affordable, and every score links back to the trace that produced it.

## How it works
### The five interfaces
1. **Dataset.** Items of `{id, input, expected or reference, slice metadata}`, stored as immutable,
   content-hashed versions. Immutable *because* a score only means something relative to a dataset
   version; an item relabeled in place silently moves every baseline. Strategy for which datasets exist
   and how items get in is the `golden-dataset-strategy` lesson.
2. **Target.** An adapter that calls the production code path with a pinned bundle (model snapshot,
   prompt version, decoding parameters, index snapshot). It calls the real application *because*
   evaluating a copy of the prompt evaluates the copy: the app's template rendering, post-processing and
   retrieval are part of what ships.
3. **Scorers.** Three families, each with a name and a version. *Deterministic* scorers (exact match,
   regex, JSON-schema validity, unit tests that execute generated code) are cheap and unambiguous but only
   apply to structured outputs. *Model-graded* scorers (an LLM judging against a rubric, against a
   reference answer, or pairwise between two outputs) cover free text, cost a model call per item, and are
   themselves a model that drifts, so they must be calibrated against human labels and pinned to a
   snapshot. *Human* scorers are the ground truth, used to calibrate judges and to sign off high-risk
   changes. Every score is stored as `{item_id, scorer, scorer_version, value, rationale}`, the version
   *because* a judge upgrade changes scores without any change to the application.
4. **Runner.** Executes dataset × target with concurrency, retries and timeouts, caches model calls keyed
   by the exact request sent (model id, decoding params, rendered messages), writes raw outputs *before* scoring, and records a
   manifest of every pin. Raw outputs first *because* scorers change: re-scoring stored outputs with a new
   judge should cost judge calls only, never a re-run of the target. Timeouts are recorded as errors, not
   as zero scores, so a provider incident does not masquerade as a regression.
5. **Results store and comparison.** Per-run aggregates per slice with confidence intervals, and a
   per-item diff between any two runs that share a dataset version. The item-level diff is the artifact a
   reviewer reads; the aggregate is what the gate reads.

### Three categories of framework, chosen by the question
- **Capability benchmark harnesses** (EleutherAI's lm-evaluation-harness, HELM) run standard academic
  tasks against a model. They answer "which base model" and nothing about your application.
- **Application eval frameworks** (Promptfoo's config of prompts × providers × test cases with
  assertions; DeepEval's pytest-style tests; RAGAS's retrieval metrics such as faithfulness and context
  precision) run *your* dataset through *your* pipeline. They answer "did this prompt or pipeline change
  help".
- **Platform-integrated tools** (Braintrust, LangSmith, Langfuse) hold datasets, experiments and
  production traces in one store, so a bad trace becomes a dataset item with one click and an eval score
  attaches to the trace that produced it. They answer "how do we close the loop with production".
The `evals/tooling` lesson compares the tools; the architectural point is that a team usually needs one
from the second category wired to one from the third, and the first only when choosing a base model.

### Where it plugs into the platform
The prompt registry and model registry store a version's eval results as metadata, and promotion reads
them. CI calls the framework through a CLI or SDK and applies the gate policy (the
`evaluation-gates-for-production-releases` lesson); the framework reports, the pipeline decides. The
observability stack runs the *same* scorers on sampled production traces, so offline and online numbers
share a definition. The dataset pipeline flows the other way: failures mined from traces and error
analysis become candidate items.

### Build or adopt
The differentiating assets are the dataset and the scorers, never the runner or the results store. Adopt
a framework when the task shape is common (chat, RAG, classification) and write only the target adapter
and custom scorers. Build a thin layer of your own when the target does not fit the framework's data model
(long multi-turn agents with tool side effects, simulated users), when data residency forbids a SaaS
store, or when scorers need to run inside your network; even then, keep the five interfaces and reuse an
open-source store rather than inventing a results format.

## Example
A minimal framework skeleton showing the five interfaces as code. The point is the shape: scorers carry a
version, the target is the production entrypoint, and raw output is stored before any scoring under a
*generation id* that hashes only what determines the outputs (dataset version and bundle). The run id
adds the scorer versions on top. So an interrupted run resumes without paying again for finished items,
and swapping `judge@rubric-v3` for `judge@rubric-v4` on a 1,000-item set yields a new run id but reuses
all 1,000 stored outputs: 1,000 judge calls, zero target calls. A changed prompt changes the bundle, hence
the generation id, hence fresh outputs. Repeats for a noise-floor measurement need a repeat index in the
generation id; otherwise every "repeat" replays the same stored sample and the measured noise is zero.

```python
from dataclasses import dataclass, asdict
from typing import Any, Callable
import hashlib, json, pathlib, time

@dataclass
class Item:                      # one row of a content-hashed dataset version
    id: str; input: dict; expected: Any = None; slice: str = "default"

@dataclass
class Scorer:                    # name + version travel with every score it produces
    name: str; version: str; fn: Callable[[Item, str], tuple[float, str]]

Target = Callable[[dict], str]   # the production code path, with the pinned bundle inside

def json_field_equals(field: str) -> Scorer:            # deterministic family
    def fn(item, out):
        try:
            return float(json.loads(out).get(field) == item.expected), ""
        except json.JSONDecodeError:
            return 0.0, "invalid JSON"
    return Scorer(f"json_eq:{field}", "1", fn)

def rubric_judge(judge: Target, rubric_version: str) -> Scorer:   # model-graded family
    def fn(item, out):
        v = json.loads(judge({"input": item.input, "output": out, "reference": item.expected}))
        return float(v["score"]), v["reason"]          # judge prompt asks for {"score": 0..1, "reason": "..."}
    return Scorer("judge", rubric_version, fn)

def run(dataset_version: str, items: list[Item], target: Target, scorers: list[Scorer], bundle: dict) -> dict:
    h = lambda o: hashlib.sha256(json.dumps(o, sort_keys=True).encode()).hexdigest()[:12]
    gen_id = h({"dataset": dataset_version, "bundle": bundle})          # what determines the outputs
    run_id = h({"gen": gen_id, "scorers": [f"{s.name}@{s.version}" for s in scorers]})
    path = pathlib.Path(f"outputs/{gen_id}.jsonl"); path.parent.mkdir(exist_ok=True)
    done = {r["id"]: r["output"] for r in map(json.loads, path.open())} if path.exists() else {}
    rows = []
    with path.open("a") as raw:
        for it in items:
            if it.id not in done:                       # a new judge reuses these: re-scoring costs judge calls only
                done[it.id] = target(it.input)
                raw.write(json.dumps({"id": it.id, "output": done[it.id]}) + "\n"); raw.flush()
            out = done[it.id]                           # persisted before any scorer runs
            scores = [dict(zip(("value", "rationale"), s.fn(it, out)), scorer=f"{s.name}@{s.version}")
                      for s in scorers]                 # one call per scorer: a judge call costs money
            rows.append({"item": asdict(it), "output": out, "scores": scores})
    return {"run_id": run_id, "gen_id": gen_id, "started": time.time(), "rows": rows}
```

## Trade-offs
- One framework across teams makes numbers comparable, but forces every task into its data model; it
  flips when a team's target (a long tool-using agent, a simulated user) does not fit and they fork.
- Model-graded scorers cover free text that deterministic checks cannot, at the price of a judge call per
  item and a second model's drift; prefer deterministic scorers whenever the output is structured.
- SaaS platforms give datasets, traces and experiments in one place with no operations burden, but
  production traces (customer data) leave your boundary; self-host or build when residency or PII rules
  apply.
- Running the eval on every PR gives fast feedback, and the cache keeps it cheap only while most items are
  unaffected by the change; a prompt touched by every item invalidates the cache, so tiered datasets exist.

## Pitfalls
- Evaluating a copy of the prompt inside the framework instead of the production code path; the symptom
  is a green eval followed by a production regression caused by template or post-processing differences.
- Using a judge scorer without calibrating it against human labels; the symptom is "improvements" that
  turn out to be longer answers, or scores that jump when the judge model is upgraded.
- Storing aggregates only; the symptom is a two-point drop that nobody can explain because there are no
  raw outputs to diff against the champion.
- Letting the dataset schema drift as teams add fields ad hoc; the symptom is two runs "on the same
  dataset" with different item counts, which the comparison tool should refuse to compare.
- Reading a capability benchmark as application quality; the symptom is a model with the higher public
  score losing on your golden set because your task depends on retrieval and formatting, not trivia.

## When to use
Reach for a framework the moment a second person needs to run or read an eval, or the moment an eval
gates anything. Pick the category by the question: base-model choice means a capability harness,
prompt or pipeline change means an application framework over your golden set, closing the loop with
production means a platform-integrated store. Measure the framework itself: time from PR to eval result,
cost per run and cache hit rate, judge-to-human agreement, and the fraction of production incidents that
already had a dataset item. In interviews, expect to draw the five interfaces and explain why raw outputs
are stored before scoring and why a scorer carries a version.

## Role lens
- **Engineer:** Writes the target adapter against the production entrypoint and the custom scorers, and
  wires the framework's CLI into the PR pipeline with content-addressed caching.
- **Architect:** Decides which category of framework the organisation standardises on, where results and
  traces are stored given data-residency rules, and which interfaces every team must conform to.
- **FDE:** Stands up the framework inside a customer's environment with their golden set, so acceptance
  is argued from item-level diffs rather than from demos of hand-picked prompts.

## Check yourself
**Q:** Four product teams each have their own eval script, and leadership wants a company-wide decision on
moving to a new model snapshot next month. What do you build, and what do you refuse to build?
**A:** Build the shared interfaces, not a new harness per team: a dataset schema with content-hashed
versions, a target adapter contract that calls each team's production entrypoint with a pinned bundle,
a scorer library with names and versions (deterministic where outputs are structured, a calibrated judge
elsewhere), one runner that stores raw outputs and a manifest, and a results store that diffs runs item by
item. Then run champion and candidate snapshots through every team's golden set on identical items and
report per-slice deltas with confidence intervals. Refuse to decide from public benchmark scores, from
aggregate numbers computed by four different rubrics, or from runs whose judge versions differ.

## Quiz
### Q: Why does a well-designed framework persist raw outputs before any scoring happens?
- [ ] Because scoring is too slow to run inline with generation
- [x] Because scorers change and re-scoring stored outputs avoids paying for the target's model calls again
- [ ] Because raw outputs are needed to compute the cache key
- [ ] Because judges cannot score streamed responses
> Scorers are versioned and get replaced; with raw outputs stored, a new judge or rubric re-scores at judge
> cost only, and reviewers can diff candidate against champion. The cache key is built from the input and
> pins, not from the output.

### Q: Which framework category answers "should our RAG support bot move from model X to model Y"?
- [ ] A capability benchmark harness such as lm-evaluation-harness or HELM
- [x] An application eval framework running your golden set through your pipeline with your scorers
- [ ] A public leaderboard comparing the two models' chat ratings
- [ ] The provider's own published evaluation of model Y
> The decision depends on how the model behaves inside your prompt, retrieval and formatting; only your
> dataset and scorers measure that. Benchmarks and leaderboards measure the base model on other tasks.

### Q: All scores rise five points overnight with no change to code, prompts, model or dataset version. What is the most likely cause?
- [ ] The cache served stale outputs from a previous run
- [ ] The golden set gained easier items
- [x] The judge scorer's model or rubric changed, which is why scorers carry a version
- [ ] Production traffic shifted toward easier queries
> A model-graded scorer is itself a model; if its snapshot or rubric moves, every candidate and champion
> shifts together. Pinning the judge version and re-baselining the champion under the same scorer is the
> defence; a cache would reproduce old scores, not new ones, and an unchanged dataset version rules out new items.
