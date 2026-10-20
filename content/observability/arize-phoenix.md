---
id: arize-phoenix
module: observability
name: Arize Phoenix
kind: tool
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - observability/opentelemetry
  - observability/tracing
  - observability/langfuse
  - observability/langsmith
  - evals/tooling
  - evals/hallucination-evaluation
  - evals/retrieval-evaluation
  - rag-retrieval/retrieval-failure-diagnosis
sources:
  - Official docs: Arize Phoenix
  - Official docs: OpenInference (specification and instrumentors)
  - Official docs: OpenTelemetry
---

## TL;DR
Arize Phoenix is a self-hostable (source-available, Elastic License 2.0) LLM observability and evaluation
tool built directly on OpenTelemetry: applications send ordinary OTel spans annotated with OpenInference conventions (LLM,
retriever, tool, agent span kinds) and Phoenix stores, visualises and evaluates them. Its strengths are
ingestion without a proprietary SDK, an evaluation library that runs LLM-as-a-judge over traces as
DataFrames and writes verdicts back onto spans, and embedding visualisation for retrieval failures. Arize
AX is the separate commercial platform.

## Why it exists
Debugging a retrieval-augmented system means answering three questions per request: which chunks were
retrieved, were they relevant, and did the answer stay inside them. That needs traces that carry the
documents and the messages, plus a quality judgement next to each span, and classic APMs offered neither.
Early LLM tools solved it with their own SDKs and wire formats, which meant one more agent in the process
and traces that could not join the rest of the system's telemetry.

Phoenix made a different bet: do not invent a tracing protocol. Standardise the *attribute names* for LLM
work on top of OpenTelemetry (that specification is OpenInference, Apache-2.0 licensed, unlike the ELv2
Phoenix server), publish instrumentors that emit those
spans for OpenAI, Anthropic, Bedrock, LangChain, LlamaIndex, DSPy and others, and compete on what you do with
the data. Because the spans are plain OTel, a Collector can send the same stream to Phoenix and to Tempo or
Datadog, and switching away costs nothing.

The second bet is that evaluation belongs next to traces. A trace says what happened; an evaluation says
whether it was good; hand-labelling does not scale. Phoenix ships judge templates (hallucination, QA
correctness, retrieval relevance, toxicity) and a runner that scores spans in bulk and attaches the result
as a span evaluation, so you can filter a project to "hallucinated = true" and read those traces. Its
embedding visualisation comes from Arize's ML-monitoring origins: retrieval failures cluster, and a UMAP
projection of query embeddings coloured by evaluation shows the empty region of the corpus at a glance.

## How it works
### Deployment
`pip install arize-phoenix` and `phoenix serve` (or the Docker image) start a single process serving the UI
and an OTLP HTTP receiver on port 6006 (`/v1/traces`) plus OTLP gRPC on 4317. Storage is SQLite by default,
which is fine for a laptop; production points `PHOENIX_SQL_DATABASE_URL` at Postgres. Projects separate
applications and environments. In a notebook, `px.launch_app()` runs the same server in-process. Optional
authentication and API keys are enabled through environment variables.

### Ingestion: OpenTelemetry plus OpenInference
`from phoenix.otel import register` configures a standard OTel `TracerProvider` with an OTLP exporter and
a resource attribute naming the project; `auto_instrument=True` activates whichever OpenInference
instrumentors are installed. An instrumentor patches a client library and emits spans whose attributes
follow the OpenInference specification: `openinference.span.kind` set to `LLM`, `CHAIN`, `RETRIEVER`,
`EMBEDDING`, `TOOL`, `AGENT`, `RERANKER`, `GUARDRAIL` or `EVALUATOR`; `input.value` and `output.value` with
MIME types; `llm.model_name`; messages flattened as `llm.input_messages.{i}.message.role` and
`.content`; `llm.token_count.prompt`, `.completion`, `.total`; `retrieval.documents.{i}.document.content`
and `.score`; `tool.name` and `tool.parameters`. Lists are flattened into indexed keys *because* OTel
attributes are flat key–value pairs, and the span kind is an attribute rather than the OTel `SpanKind`
because OTel's kinds describe RPC roles, not LLM roles. Any OTel SDK can send spans; those using other
conventions still arrive but render with less structure.

### The evaluation library
`phoenix.evals` is a DataFrame-oriented runner. `llm_classify` applies a judge prompt template to each row
with a model wrapper (`OpenAIModel`, `AnthropicModel`, `LiteLLMModel`, …), constrains the answer to a set of
rails such as `["factual", "hallucinated"]`, optionally asks for an explanation, and runs rows concurrently.
Pre-built evaluators (`HallucinationEvaluator`, `QAEvaluator`, `RelevanceEvaluator`,
`ToxicityEvaluator`, …) wrap tested templates; `run_evals(dataframe, evaluators)` returns one DataFrame per
evaluator. Helper queries build the rows from traces: `get_qa_with_reference` yields one row per answered
question with its retrieved context, `get_retrieved_documents` one row per retrieved document for relevance
scoring. Results go back with `log_evaluations(SpanEvaluations(...))` or `DocumentEvaluations(...)`, after
which the UI shows per-span labels and aggregates such as precision@k or NDCG derived from document
relevance labels.

### Datasets, experiments, prompts, sessions, annotations
Spans or DataFrames can be turned into datasets; `run_experiment(dataset, task, evaluators)` executes a
task over the examples and scores it, and the UI compares experiments. Prompts are versioned, and the
playground replays a captured span against an edited prompt or a different model. A `session.id` attribute
groups traces into conversations; humans add labels and scores as annotations in the UI or via the API.

### Embeddings
`px.Inferences` with embedding columns produces a UMAP projection with clustering, coloured by evaluation
label or by drift between a primary and a reference dataset, which is the original Phoenix feature and still
the quickest way to see that a cluster of user queries has no nearby documents.

## Example
A RAG answer traced with plain OpenTelemetry plus OpenInference attributes, with the OpenAI instrumentor
producing the `LLM` span, followed by a notebook step that judges every answered question for hallucination
against its retrieved context and writes the verdicts back onto the spans. Function names follow the
long-standing evaluator/`run_evals` interface of `phoenix.evals`; newer releases add a redesigned API
alongside it, so pin the version and check the import paths against the release you install.

```python
# pip install arize-phoenix openinference-instrumentation-openai openai
# terminal: phoenix serve   -> UI http://localhost:6006, OTLP http://localhost:6006/v1/traces
from phoenix.otel import register
from openinference.instrumentation.openai import OpenAIInstrumentor
from openinference.semconv.trace import SpanAttributes, OpenInferenceSpanKindValues
from openai import OpenAI

tracer_provider = register(project_name="support-bot", endpoint="http://localhost:6006/v1/traces")
OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)   # LLM spans with messages and token counts
tracer = tracer_provider.get_tracer(__name__)
client = OpenAI()

def retrieve(question: str) -> list[str]:
    with tracer.start_as_current_span("retrieve") as span:
        docs = ["Reset your password under Settings > Security > Reset password."]
        span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, OpenInferenceSpanKindValues.RETRIEVER.value)
        for i, d in enumerate(docs):                            # flattened list: one key per document
            span.set_attribute(f"retrieval.documents.{i}.document.content", d)
        return docs

def answer(question: str) -> str:
    with tracer.start_as_current_span("support.answer") as span:
        span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, OpenInferenceSpanKindValues.CHAIN.value)
        span.set_attribute(SpanAttributes.INPUT_VALUE, question)
        docs = retrieve(question)
        resp = client.chat.completions.create(model="gpt-4o-mini", messages=[
            {"role": "system", "content": "Answer only from:\n" + "\n".join(docs)},
            {"role": "user", "content": question}])
        out = resp.choices[0].message.content
        span.set_attribute(SpanAttributes.OUTPUT_VALUE, out)
        return out

answer("How do I reset my password?")

# Later, in a notebook: judge each answered question against its retrieved context, write results back
import phoenix as px
from phoenix.evals import HallucinationEvaluator, OpenAIModel, run_evals
from phoenix.session.evaluation import get_qa_with_reference
from phoenix.trace import SpanEvaluations
px_client = px.Client()                                    # talks to the running server
qa = get_qa_with_reference(px_client)                      # columns: input, output, reference
(halluc,) = run_evals(dataframe=qa, provide_explanation=True,
                      evaluators=[HallucinationEvaluator(OpenAIModel(model="gpt-4o-mini"))])
px_client.log_evaluations(SpanEvaluations(eval_name="Hallucination", dataframe=halluc))
```

In the UI the trace reads `support.answer` → `retrieve` and `support.answer` → `ChatCompletion`, the chain
span now carries a `Hallucination` label with the judge's explanation, and the project view lets you filter
to the hallucinated ones.

## Trade-offs
- OTel-native ingestion means no lock-in and one instrumentation for every backend, against less product
  polish on per-user cost accounting and prompt-release workflows than Langfuse or LangSmith offer at the
  time of writing; flips when the team wants a managed product loop more than portability.
- A single container with SQLite is zero-ops for development, but production needs Postgres and you own
  retention, backups and access control; multi-team scale and RBAC are what the commercial platform sells.
- Evaluations as DataFrames in notebooks are flexible, scriptable and cheap to iterate, but the loop is one
  you run or schedule yourself, whereas server-side online evaluators in other tools score traffic
  continuously without a job.
- Pre-built judge templates save prompt engineering and are tested by the maintainers, yet still need
  calibration against a human-labelled sample from your own domain before anyone trusts the numbers.
- Embedding visualisation is the fastest way to find corpus gaps, but only if you log query and document
  embeddings, which multiplies storage per trace.

## Pitfalls
- Calling an instrumentor after the client library is already in heavy use, or never calling it; the
  symptom is `CHAIN` and `RETRIEVER` spans with no `LLM` child and no token counts.
- Exporter and port mismatch, HTTP spans sent to 4317 or gRPC spans to 6006; the symptom is an empty
  project with no error in the application.
- SQLite under production concurrency; the symptom is a sluggish UI, locked-database errors and lost spans
  at peak traffic.
- Running the hallucination evaluator on traces whose retriever spans carry no documents, so the reference
  column is empty; the symptom is verdicts that are uniformly "factual" or evaluator errors.
- Judging a model's outputs with the same model; the symptom is suspiciously high scores that a human
  spot-check contradicts, the self-preference bias of LLM judges.
- Logging full documents and prompts on every span at high volume without sampling or truncation; the
  symptom is storage growth that outpaces the traces' usefulness.
- A project name on `register()` that differs from the one queried in the notebook; the symptom is
  "no data" despite a busy UI.

## When to use
Reach for Phoenix when you already emit or intend to emit OpenTelemetry traces and want LLM-aware analysis
without a second SDK, when the work is evaluation-heavy (hallucination, retrieval relevance, agent
trajectories) and your team lives in notebooks, or when self-hosting is a requirement and the budget for a
commercial platform is zero (check that the Elastic License 2.0 is acceptable to your legal team; it forbids
offering Phoenix itself as a managed service, which is rarely an issue for internal use). Prefer Langfuse when prompt management and per-user cost are
the daily workflow; prefer LangSmith when the stack is LangGraph and a hosted product is acceptable. Measure
span completeness per project, judge-versus-human agreement on a labelled sample, and the retrieval
relevance aggregates over time. Day to day it is the place you replay a bad RAG answer, see the chunks that
came back, and run a relevance judge over last week's retrievals; in interviews, be able to explain why
building on OTel and OpenInference matters and how a span evaluation gets back onto a trace.

## Role lens
- **Engineer:** Registers the tracer provider, installs the right OpenInference instrumentors, adds
  retriever document attributes, and schedules the evaluation job that writes labels back to spans.
- **Architect:** Decides to standardise on OpenTelemetry with OpenInference attributes so Phoenix is one
  consumer among several, and sets Postgres, retention and sampling for the deployment.
- **FDE:** Runs Phoenix inside a customer's environment to show their team which retrieved chunks drove a
  wrong answer, and uses relevance evaluations as evidence in retrieval tuning sessions.

## Check yourself
**Q:** You inherit a RAG service that already sends plain OpenTelemetry traces to Grafana Tempo. The team
wants hallucination and retrieval-relevance scores without replacing their stack. How do you add Phoenix?
**A:** Keep the OTel SDK and Tempo. Enrich the spans rather than re-instrumenting: install the OpenInference
instrumentor for the model client so `LLM` spans carry messages and token counts, and add retriever
attributes (`openinference.span.kind = RETRIEVER` and `retrieval.documents.{i}.document.content`) to the
existing retrieval span. In the Collector, add a second OTLP exporter pointing at a Phoenix deployment backed
by Postgres, so Tempo keeps the infrastructure view and Phoenix gets the same traces. Then schedule a job that
pulls question–answer–reference rows and retrieved-document rows, runs the hallucination and relevance
evaluators on a sample sized to the judge budget, and logs the results back as span and document
evaluations. Calibrate the judge against a hundred human-labelled examples before publishing the numbers, add
`session.id` for conversation review, and separate environments by project.

## Quiz
### Q: What most distinguishes Phoenix from an LLM observability tool with its own SDK?
- [ ] It can only trace LangChain applications
- [x] It ingests standard OpenTelemetry spans annotated with OpenInference attributes, so the same instrumentation can feed any backend
- [ ] It stores traces only in memory during a notebook session
- [ ] It requires the commercial Arize platform to view traces
> Phoenix standardises attribute names on top of OTel rather than defining a new protocol. That makes
> instrumentation portable and lets a Collector fan the same spans out to Phoenix and a general tracing store.

### Q: Why are evaluation results logged back as span evaluations rather than kept in the notebook?
- [ ] Because `run_evals` cannot return a DataFrame
- [ ] Because cost is computed from evaluation labels
- [x] So traces can be filtered and aggregated by quality in the UI and compared over time next to the spans they describe
- [ ] Because the judge model needs them to be stored before it can run again
> An evaluation detached from its trace is a spreadsheet. Attached to the span, "show me hallucinated answers
> from yesterday" becomes a filter, and relevance labels on documents roll up into retrieval metrics.

### Q: Which attributes mark a span as a retrieval step whose documents Phoenix should display and evaluate?
- [ ] `gen_ai.operation.name = "retrieve"` with the documents in the span's events
- [x] `openinference.span.kind = "RETRIEVER"` with `retrieval.documents.{i}.document.content` entries
- [ ] OTel `SpanKind.CLIENT` with a `db.system` attribute naming the vector store
- [ ] A `session.id` attribute and `input.value` set to the query
> OpenInference carries the LLM-specific role in an attribute, because OTel span kinds describe RPC roles,
> and flattens the document list into indexed keys so each document can be labelled individually.
