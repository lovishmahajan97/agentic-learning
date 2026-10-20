---
id: tooling
module: evals
name: Tooling (Promptfoo, RAGAS, DeepEval, Braintrust, LangSmith, Langfuse)
kind: tool
difficulty: core
est_minutes: 16
status: reviewed
related:
  - evals/eval-harness
  - evals/llm-as-a-judge
  - evals/judge-calibration-and-bias
  - evals/retrieval-evaluation
  - evals/faithfulness-groundedness
  - observability/langfuse
  - observability/langsmith
  - llmops-platform/evaluation-frameworks
sources:
  - Es et al., 2023 — RAGAS: Automated Evaluation of Retrieval Augmented Generation
  - Official docs: Promptfoo
  - Official docs: DeepEval
  - Official docs: Langfuse
---

## TL;DR
Every eval tool implements the same pipeline: dataset → run the system → grade → store and compare.
They differ in where that state lives and which graders come prebuilt. Promptfoo, RAGAS and DeepEval
are libraries you run locally (config-driven prompt×model matrices, RAG metrics, pytest-style tests);
Braintrust, LangSmith and Langfuse are platforms that keep traces, datasets and experiments on a server
and attach evaluators to production traffic. Choose by where your traces already live and how much of
the grading logic you need to own.

## Why it exists
Every team that takes evals seriously ends up writing the same code: a runner with concurrency,
retries and caching; judge prompts for faithfulness and relevance; a table to compare two runs side by
side; and somewhere to keep the raw outputs so Tuesday's run can be compared with Friday's. The first
version takes a week; keeping it alive for a year is the real cost, and it is undifferentiated work.

Two more needs push teams toward tools. Evals need to connect to traces: the production conversation
that failed should become a dataset item in one click, and a judge score should be visible on the trace
it graded, otherwise error analysis and the eval suite drift apart. And the people who know what "good"
looks like, product owners and subject-matter experts, need to read outputs and annotate without
cloning a repo, which means a UI.

The naive alternatives are a notebook (not reproducible), a spreadsheet of outputs (fine for fifty
rows, no automation, no history) and a hand-rolled harness (works, but has no trace link and no UI, and
becomes someone's unpaid second job). Tools remove that work, but they also bring sharp edges: a
prebuilt "faithfulness" metric is an LLM judge running someone else's prompt on a default model, a
hosted platform holds your prompts and customer data, and judge calls cost money on every run. The
point of understanding what each tool does underneath is knowing what its number means before you let
it gate a release.

## How it works
### The common shape
A dataset of rows (input, optional expected output, metadata such as slice); a target, which is a
function, a prompt plus provider, or an HTTP endpoint; stored outputs; scorers that are deterministic,
embedding-based, or an LLM judge; per-row scores; and aggregation with comparison across runs. Each tool
is packaging around that shape, and the comparison below is about the packaging.

### Promptfoo (library, config-driven)
An open-source TypeScript CLI run with `npx promptfoo`. A YAML file declares `prompts`, `providers`
(hosted APIs, local models, an HTTP endpoint, or a Python/JS script wrapping your own pipeline) and
`tests` with `vars` and `assert` entries. It runs the full matrix of prompts × providers × tests, caches
responses so re-runs are free, and `promptfoo view` opens a side-by-side web table. Assertions span
deterministic (`contains`, `regex`, `is-json`, custom `javascript`/`python`), semantic (`similar`
via embeddings) and model-graded (`llm-rubric`, `factuality`, context-faithfulness and recall style
checks). It also has a red-team mode that generates adversarial probes by plugin and strategy. Best fit:
fast prompt or model comparisons and a CI gate, including for teams that do not write Python. Sharp
edges: the YAML grows with the suite; model-graded assertions use a default grader you should pin
explicitly; a multi-step agent has to be wrapped as a custom provider.

### RAGAS (library, RAG metrics)
A Python library born from the paper of the same name, built for RAG. Its core metrics are LLM-judge
chains plus embeddings. *Faithfulness* splits the answer into statements and scores the fraction the
retrieved context supports. *Answer relevancy* generates questions from the answer and measures their
embedding similarity to the original question, penalising incomplete or padded answers. *Context
precision* asks whether the relevant chunks were ranked at the top; *context recall* measures how much
of a reference answer the retrieved context could support. Faithfulness and answer relevancy need no
reference labels; context recall does. It also generates synthetic test sets from your documents.
Best fit: bootstrapping RAG evaluation before labels exist. Sharp edges: "reference-free" means
"compared to the context, not to the truth", so a faithful answer built on the wrong chunk scores
perfectly; every score depends on the judge model and on the statement-splitting prompt; metric
definitions have changed across library versions, so pin the version and never compare scores across
versions.

### DeepEval (library, pytest-style)
Python, shaped like unit tests: an `LLMTestCase(input, actual_output, expected_output,
retrieval_context)` is checked with `assert_test(case, metrics)` and run through `deepeval test run`.
Metrics are classes with a `threshold`: `FaithfulnessMetric`, `AnswerRelevancyMetric`,
`ContextualPrecisionMetric`, `HallucinationMetric`, and `GEval` for custom criteria written in prose,
each returning a `score` and a `reason`. Best fit: evals that live in the repository next to the tests
and a custom rubric metric without writing a judge from scratch. Sharp edges: pass/fail per case hides
the score distribution; prebuilt metrics are judge prompts you do not own; the hosted Confident AI
platform is where the dashboards live.

### Braintrust (platform with an open-source scorer library)
A hosted platform whose SDK runs `Eval(project, data=..., task=..., scores=[...])` and uploads an
experiment with a per-row diff against previous experiments. The open-source `autoevals` package
provides scorers such as `Factuality`, `ClosedQA`, `Levenshtein`, embedding similarity and a generic
`LLMClassifier`. It also logs production calls as traces, curates datasets from logs, and has a prompt
playground. Best fit: a tight experiment-diff loop with a UI non-engineers can read. Sharp edge: it is a
hosted service, so where your traces and prompts may reside is a plan-level question to settle before
adopting.

### LangSmith (platform)
LangChain's platform: automatic tracing for LangChain and LangGraph code and a `@traceable` decorator
for anything else, datasets, experiments through `evaluate(target, data=..., evaluators=[...])`,
annotation queues for human review, online evaluators that sample production traces, and a prompt
hub with a playground. It does not require LangChain. Best fit: LangChain or LangGraph shops and
teams that need a human annotation workflow. Sharp edges: hosted by default with self-hosting on
enterprise tiers; pricing scales with trace volume; its native run/trace schema is LangSmith-specific
(OpenTelemetry ingestion exists, but the evaluation features are built around the native format).

### Langfuse (open-source platform)
Open source and self-hostable, with tracing SDKs for Python and JavaScript (recent versions build on
OpenTelemetry), prompt management with versions and labels, datasets with experiment runs, and a
scores API that attaches any numeric, categorical or boolean score to a trace, whether from a judge, a
human annotator or end-user feedback. Managed LLM-as-judge evaluators run on sampled production
traces, and token cost is tracked per trace. Best fit: data-residency requirements, an open-source
preference, and unifying production feedback with offline evals in one score table. Sharp edges: you
run a multi-service stack yourself; fewer prebuilt scorers than DeepEval or RAGAS, so you bring your
own graders.

### How to choose
Two axes decide it. Library versus platform: do you need a CI gate in a repository, or traces,
datasets and annotations shared across people? Prebuilt judges versus your own graders: are the
packaged metrics close enough to your rubric that calibrating them is cheaper than writing a judge? A
common combination is Promptfoo or DeepEval in CI, RAGAS metrics used as scorers inside whichever
harness, and one platform for traces, datasets and online evaluation. Keep one canonical dataset store;
two sources of truth for golden items is the most common way these stacks rot.

### What is under every prebuilt metric
A prompt, a model and a parsing step. Read the prompt before trusting the number, pin the judge model,
and calibrate against a few dozen human-labelled items from your own domain before the metric gates
anything. Faithfulness-style metrics are typically a ratio, supported statements ÷ extracted
statements, so the score inherits two judge decisions per item: how the answer was split into
statements and whether each was judged supported. A terse answer with one supported claim scores 1.0;
a thorough answer with nine supported claims and one unsupported aside scores 0.9, so the metric
mildly rewards saying less.

The same anatomy sets the bill. A judge metric costs one or more model calls per row, so a run costs
roughly rows × judge metrics × calls per metric × samples per row. An illustrative 500-row set with
three judge metrics at about two calls each and three samples per row is around 9,000 judge calls per
run, which is why the cost pyramid (deterministic checks per commit, judges per PR or nightly) is a
budgeting decision, not a style preference. Library caching only helps when the target's output is
unchanged, which is rarely true for the run that tests a change.

## Example
The same support assistant tested two ways: a DeepEval test that pairs a prebuilt faithfulness metric
with a custom G-Eval criterion, and a Promptfoo config that compares two prompt versions across two
providers with deterministic and rubric assertions.

```python
# test_support_bot.py — run with: deepeval test run test_support_bot.py
import pytest
from deepeval import assert_test
from deepeval.metrics import FaithfulnessMetric, GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from my_app import answer_with_context      # returns (answer: str, retrieved_chunks: list[str])

handoff = GEval(
    name="AccountDataHandoff",
    criteria="If the input asks for account-specific data (invoices, usage, balances), the output must "
             "offer a handoff to a person or a self-service link instead of stating a figure.",
    evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    threshold=0.8,
)

@pytest.mark.parametrize("question", ["How do I enable SCIM?", "What was my invoice total last month?"])
def test_answer(question):
    answer, chunks = answer_with_context(question)
    case = LLMTestCase(input=question, actual_output=answer, retrieval_context=chunks)
    assert_test(case, [FaithfulnessMetric(threshold=0.9), handoff])
```

```yaml
# promptfooconfig.yaml — run with: npx promptfoo eval && npx promptfoo view
prompts: [file://prompts/support_v7.txt, file://prompts/support_v8.txt]
providers: [openai:chat:MODEL_A, anthropic:messages:MODEL_B]   # substitute current model ids
tests:
  - vars: { question: "How do I enable SCIM?" }
    assert:
      - { type: contains, value: "SCIM" }
      - { type: llm-rubric, value: "Steps match the SCIM article; no invented settings." }
  - vars: { question: "What was my invoice total last month?" }
    assert:
      - { type: not-contains, value: "$" }      # must not invent a figure
      - { type: python, value: "'billing' in output.lower() or 'contact' in output.lower()" }
```

What the numbers mean underneath. Promptfoo expands the YAML into a matrix: 2 prompts × 2 providers ×
2 tests = 8 cells, each graded by its two assertions, and the view shows prompt v7 and v8 side by side
per provider, which is the comparison you wanted. On the DeepEval side, suppose the SCIM answer makes
four claims: SCIM lives under Settings → Security, it needs the Enterprise plan, you generate a token,
and you paste the token into the identity provider. The retrieved article supports three; nothing in
it mentions a plan requirement. Faithfulness is 3/4 = 0.75, below the 0.9 threshold, so the test fails
and the metric's `reason` names the unsupported claim. Now the trap: if retrieval had returned the SSO
article instead and the bot faithfully described SSO setup, faithfulness would be 1.0 and the test
would pass. The Promptfoo `contains: SCIM` and the rubric assertion are what catch that case, which is
the general lesson: pair every context-relative metric with a check against the task itself.

## Trade-offs
- Prebuilt metrics give faithfulness and relevance on day one without labels, but each is a judge whose
  prompt and model you do not control; once a metric gates a release you must calibrate it against
  human labels, and at that point writing your own judge is often as cheap.
- A platform connects production traces to datasets and lets non-engineers annotate, but it stores your
  prompts and customer data; this flips toward self-hosting (Langfuse, or enterprise self-hosted tiers)
  whenever data-residency rules apply.
- Config-driven Promptfoo makes prompt×model comparison trivial, but agents and multi-step pipelines
  must be wrapped as a custom provider, where a code-first `Eval` or pytest style is more natural.
- Pytest-style pass/fail slots into CI and feels familiar but hides distributions; score tables show
  distributions but need a separate gating rule before they stop anything. Per-case thresholds suit a
  small suite of must-pass behaviours; once the suite is large and noisy, gate on aggregate drops and
  regressed-item counts instead.
- Combining several tools covers each one's gaps but multiplies copies of the dataset and metric
  definitions; keep one canonical store and import into the rest.

## Pitfalls
- Reading a RAGAS faithfulness of 0.95 as "accurate". Symptom: answers are perfectly faithful to the
  wrong chunk, and only a retrieval metric against labelled queries reveals it.
- Comparing scores across tool versions or judge models. Symptom: a jump or drop after a dependency
  upgrade with no change to the system under test.
- Leaving the judge model at the tool's default. Symptom: CI and a developer's laptop disagree on the
  same run because each resolved a different default.
- Sending production traces with customer data to a hosted platform without redaction or a data
  agreement. Symptom: a security review blocks the rollout months after the team came to depend on it.
- Running judge metrics on every commit. Symptom: eval spend exceeds the feature's own inference cost;
  split deterministic checks per commit from judge runs per pull request or nightly.
- Treating packaged "hallucination" or "toxicity" metrics as safety guarantees. Symptom: a confident
  dashboard and an incident the judge's blind spots let through.

## When to use
Reach for a library when the eval is a CI gate owned by engineers and the data is small; reach for a
platform when you need traces, human annotation and online evaluators in one shared place. Start with
whatever is closest to where traces already live: LangChain code points to LangSmith, an
OpenTelemetry or self-hosted setup points to Langfuse. Do not adopt a platform for one prompt and
thirty test cases; a Promptfoo config is enough. Measure judge agreement with human labels, eval
runtime and cost per run, and how many production traces become dataset items each week. In
interviews, "what would you use to evaluate a RAG system?" is answered by naming the pipeline, saying
the tool is packaging, and explaining what RAGAS faithfulness measures and what it cannot see.

## Role lens
- **Engineer:** Wires the chosen scorers into the harness or CI, pins judge models and library
  versions, and writes custom graders where the packaged metric does not match the rubric.
- **Architect:** Decides library versus platform, hosted versus self-hosted storage of traces and
  datasets, and which store is the single source of truth for eval data across teams.
- **FDE:** Picks what can run inside the customer's environment, often self-hosted Langfuse or
  Promptfoo in their CI, and uses the platform's annotation UI so the customer's experts can label.

## Check yourself
**Q:** A team evaluating a RAG assistant reports RAGAS faithfulness 0.93 and answer relevancy 0.88 and
concludes it is ready to ship. What would you ask before agreeing, and which facts about the tooling
drive your questions?
**A:** Faithfulness measures support by the retrieved context, not truth, so the first question is what
the retrieval metrics are against a labelled query set: recall@k, context recall, or an error-analysis
count of "right chunk missing". Second, which judge model and which library version produced the
scores, and whether they were calibrated against human labels on a sample from this domain. Third, the
distribution: the mean hides a tail, and how many items were there and where did they come from, since
a synthetic test set generated from the documents is usually easier than real user queries. Fourth,
whether the suite contains the failure modes error analysis found, such as account-data questions, and
deterministic checks for them. Readiness is decided by the task's acceptance criteria; the two RAGAS
numbers are components, not the verdict.

## Quiz
### Q: What does a RAGAS faithfulness score of 1.0 tell you?
- [ ] The answer is factually correct
- [x] Every statement in the answer is supported by the retrieved context, which says nothing about whether the right context was retrieved
- [ ] The retriever returned all of the relevant chunks
- [ ] The answer matches the reference answer exactly
> Faithfulness decomposes the answer into statements and checks each against the retrieved chunks. A
> wrong chunk faithfully summarised scores perfectly, which is why it must be read next to a retrieval
> metric.

### Q: What is the main reason to choose a platform such as Langfuse, LangSmith or Braintrust over a library such as Promptfoo or DeepEval?
- [ ] Platforms ship more accurate judge models
- [ ] Libraries cannot run LLM-as-judge metrics
- [x] You need production traces, datasets, human annotation and online evaluators in one shared place rather than a CI gate in a repository
- [ ] Platforms are required for CI integration
> The grading logic is similar everywhere; what a platform adds is shared state: traces linked to
> dataset items, annotation queues and evaluators on live traffic. A repository-scoped CI gate does not
> need that and a library does it with less infrastructure.

### Q: A prebuilt "hallucination" metric from an eval library is about to become a release gate. What must happen first?
- [ ] Set its threshold to 1.0 so only perfect outputs pass
- [x] Calibrate it against human-labelled examples from your domain and pin the judge model, because the metric is an LLM judge with its own prompt and biases
- [ ] Replace it with a string-match assertion
- [ ] Run it on every commit for a month to build a history
> A packaged metric is a prompt, a model and a parser; its agreement with your definition of a
> hallucination is unknown until measured. Pinning the judge keeps the gate stable across environments
> and upgrades.
