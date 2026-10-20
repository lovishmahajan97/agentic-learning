---
id: langsmith
module: observability
name: LangSmith
kind: tool
difficulty: core
est_minutes: 15
related:
  - observability/tracing
  - observability/langfuse
  - observability/arize-phoenix
  - observability/opentelemetry
  - agents/frameworks
  - evals/tooling
  - evals/golden-dataset
  - agent-reliability/agent-trajectory-evaluation
sources:
  - Official docs: LangSmith
  - Official docs: LangChain and LangGraph
---

## TL;DR
LangSmith is LangChain Inc.'s closed-source platform for tracing, evaluating and monitoring LLM
applications, hosted with enterprise self-hosting available. It records every unit of work as a run
(LLM, chain, tool, retriever) in a tree with inputs, outputs, tokens, latency and cost, attaches feedback
and online evaluators, and runs offline experiments against datasets. Any Python or JavaScript code can
send traces through `@traceable`, but it is the natural choice for LangChain and LangGraph apps, where
tracing is an environment-variable switch.

## Why it exists
LangChain's abstractions made it hard to see what actually went to the model: a chain composed prompt
templates, retrievers and parsers, and when the answer was wrong you could not tell which piece produced
the bad input. The framework already had a callback system that fired on every chain, LLM and tool start
and end; LangSmith grew out of turning those callbacks into persistent, queryable run trees. The naive
alternative, `verbose=True` printing to the console, does not persist, cannot be searched by user or
compared across versions, and vanishes in production.

The second half of the problem is evaluation. "The new prompt seems better" is not evidence. Teams need a
dataset of inputs with reference outputs, a way to run a candidate against it, evaluators that score each
result, and a side-by-side view of two experiments. LangSmith ties this to production tracing: a bad trace
becomes a dataset example with one click, an experiment is itself a set of traces, and feedback from users,
annotators and judges lives on the same runs.

Against the module's alternatives, LangSmith is the most integrated with LangGraph (node-level state,
tool calls and token streams appear without instrumentation) and the least portable: it is proprietary,
your data sits in LangChain's cloud unless you self-host under an enterprise agreement, and its data model
is its own. Langfuse offers the comparable loop as open source; Phoenix offers it on plain OpenTelemetry.

## How it works
### Runs, traces, projects and threads
Each unit of work is a **run** with a `run_type` (`llm`, `chain`, `tool`, `retriever`, `embedding`,
`prompt`, `parser`), inputs, outputs, start and end times, an error if any, tags, metadata and a parent run
id. The root run is the **trace**. A **project** is a container for traces, usually one per application
and environment. **Threads** are traces that share a conversation key in metadata (`session_id`,
`thread_id` or `conversation_id`); the UI reassembles them into a conversation view, which is how you read
a multi-turn chat the way the user experienced it.

### How runs get created
With LangChain or LangGraph, setting `LANGSMITH_TRACING=true` plus an API key and project name installs a
tracer callback handler, and every chain, node, model call and tool invocation becomes a run with no code
change. Outside the framework, `@traceable(run_type=...)` wraps any function: it reads the current run from
a context variable, creates a child run, records arguments, return value and exceptions, and restores the
parent on exit. `wrap_openai` and `wrap_anthropic` wrap provider clients so each completion becomes an `llm`
run with token usage, including streamed responses, which the wrapper reassembles. A `RunTree` object gives
manual control when decorators do not fit, and helpers carry the run context in headers so a trace can
continue in another service.

### Transport
Runs are queued and posted in background batches to the LangSmith API, so tracing stays off the request
path; a short-lived process must wait for the queue to drain (`wait_for_all_tracers()` or the client's
flush) or child runs are lost. LangSmith also exposes an OTLP endpoint, so OpenTelemetry-instrumented
services can send spans into a project without the SDK.

### Feedback and online evaluation
**Feedback** is a record with a key, a numeric score or categorical value and an optional comment, attached
to a run. Sources: end-user signals sent with the run id, human **annotation queues**, **online evaluators**
(LLM-as-a-judge or code rules applied to a sampled percentage of incoming traces, writing feedback
automatically), and **automation rules** that act on a filter: add matching traces to a dataset, send them to
an annotation queue, or call a webhook. Because all of these produce feedback on runs, monitoring charts can
plot them over time next to latency, token and cost series.

### Datasets and experiments
A **dataset** holds examples with inputs, reference outputs and metadata, with versions and splits.
`evaluate(target, data, evaluators, experiment_prefix)` runs the target on every example, traces each run,
applies the evaluators (plain functions or LLM judges that receive inputs, outputs and reference outputs)
and records their results as feedback; the result is an **experiment** you compare with others in the UI.
Pairwise evaluators score two experiments against each other for preference judgements. The same
experiments can be launched from the playground to test prompt variants and models without code.

### Prompts and monitoring
The prompt hub versions prompts by commit; `client.pull_prompt("name")` returns a LangChain prompt
template, and runs record which commit they used. Project dashboards show trace volume, error rate,
latency percentiles, tokens, cost (from a model price map you can extend) and feedback trends, with
threshold alerts.

## Example
Plain Python with no LangChain: a traced retriever and chain, a wrapped OpenAI client so the model call is
an `llm` run with tokens, a `session_id` so the traces form a thread, then an offline experiment with a code
evaluator against a one-example golden dataset. Evaluator signatures and the `{"output": ...}` wrapping
follow the current SDK; older releases used `(run, example)` evaluators.

```python
# pip install langsmith openai ; env: LANGSMITH_TRACING=true LANGSMITH_API_KEY=... LANGSMITH_PROJECT=support-bot
from langsmith import Client, traceable, evaluate
from langsmith.wrappers import wrap_openai
from openai import OpenAI

llm = wrap_openai(OpenAI())          # each completion becomes an 'llm' run with tokens, latency and cost

@traceable(run_type="retriever")
def retrieve(question: str) -> list[str]:
    return ["Reset your password under Settings > Security > Reset password."]

@traceable(run_type="chain", name="support_answer")
def answer(question: str) -> str:
    docs = retrieve(question)
    resp = llm.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": "Answer only from this context:\n" + "\n".join(docs)},
                  {"role": "user", "content": question}])
    return resp.choices[0].message.content

# Production call: metadata.session_id groups this trace with others into a Thread in the UI
answer("How do I reset my password?", langsmith_extra={"metadata": {"session_id": "conv-17"}})

# Offline experiment against a golden dataset
ls = Client()
if not ls.has_dataset(dataset_name="support-golden"):
    ds = ls.create_dataset("support-golden")
    ls.create_examples(dataset_id=ds.id,
                       inputs=[{"question": "How do I reset my password?"}],
                       outputs=[{"answer": "Settings > Security > Reset password"}])

def mentions_reference(outputs: dict, reference_outputs: dict) -> bool:      # a code evaluator
    return reference_outputs["answer"].lower() in outputs["output"].lower()

evaluate(lambda inputs: answer(inputs["question"]),      # a str result is stored as {"output": ...}
         data="support-golden", evaluators=[mentions_reference],
         experiment_prefix="system-prompt-v2")
```

The production trace appears as `support_answer` → `retrieve` and `support_answer` → `ChatOpenAI`-style
`llm` run; the experiment appears under the dataset with one traced run per example and a
`mentions_reference` feedback score you can compare with the next prompt version.

## Trade-offs
- Deepest LangChain and LangGraph integration (node state, tool calls, streamed tokens for free) against
  lock-in to a proprietary data model and hosted service; flips when procurement requires data residency
  and enterprise self-hosting is not on the table.
- Managed SaaS means zero operations, but pricing follows ingested traces with retention tiers, so at high
  volume you either sample, shorten retention, or move to a self-hosted open-source alternative.
- `@traceable` records arguments and return values automatically, which is high fidelity and also captures
  personal data and large payloads; input and output processors or hiding inputs trade that fidelity back.
- Online evaluators give a continuous quality signal on production traffic at judge-model cost per sampled
  trace and with judge bias; annotation queues are the calibration, not an optional extra.
- The prompt hub decouples prompt edits from deploys for LangChain templates, but your runtime now depends
  on a pull at start-up or request time; cache it and fall back to a bundled version.

## Pitfalls
- No traces at all because the wrong environment-variable family is set (the older `LANGCHAIN_TRACING_V2`
  and `LANGCHAIN_API_KEY` names versus the current `LANGSMITH_*` names) or the key belongs to another
  workspace; the symptom is a working app and an empty project.
- Truncated traces from serverless functions because the process returned before the background queue
  drained; the symptom is root runs with missing children, fixed by waiting for tracers before returning.
- A wrapped client called outside any traceable context, so every model call is its own trace; the symptom
  is hundreds of one-run traces and no threads.
- Threads that never group because the metadata key is missing or was set on a child run rather than the
  root; the symptom is a conversation view with one turn per thread.
- Evaluators that read `outputs["answer"]` when the target returned a string (stored as `{"output": ...}`)
  or a dict with a different key; the symptom is every example erroring or scoring zero.
- Development, CI and production traffic in one project; the symptom is dashboards and online evaluators
  polluted by test runs, and sampled judges spending budget on fixtures.

## When to use
Pick LangSmith when the application is built on LangChain or LangGraph and a hosted platform is acceptable:
you get agent-level traces, threads and evaluations with almost no instrumentation work, and the
dataset-to-experiment loop is mature. It still works for plain Python or TypeScript through `@traceable`,
but then the integration advantage shrinks and Langfuse (open source, self-hostable) or Phoenix
(OpenTelemetry-native) deserve a direct comparison on data residency and cost. Measure trace completeness
per environment, the fraction of production traces that receive feedback, and judge-versus-human agreement
on the annotation queue. In day-to-day work it is where you reproduce a customer's bad conversation and
where a prompt change proves itself against the golden dataset before release; in interviews, expect to
explain the run tree, how threads are formed, and the difference between online evaluators and offline
experiments.

## Role lens
- **Engineer:** Wraps provider clients, decorates service boundaries with `@traceable`, sets session metadata
  on the root run, and writes code evaluators that read the right output keys.
- **Architect:** Decides whether a proprietary hosted tracing store is acceptable for the data involved,
  defines project-per-environment conventions, and sets the sampling and retention budget.
- **FDE:** Uses threads to walk a customer through a failing conversation, builds the customer's golden
  dataset from annotated production traces, and runs experiments as acceptance evidence.

## Check yourself
**Q:** A LangGraph agent in production sometimes calls the same tool repeatedly before answering, and users
complain about slow responses. How would you use LangSmith to quantify the problem, find the cause and
verify a fix?
**A:** Quantify first: filter the project for traces whose tool-run count or latency exceeds a threshold, and
add an automation rule that routes matching traces to an annotation queue and a "looping" dataset so the
sample accumulates. Open a few run trees: LangGraph traces show each node's state and the `llm` run that
chose the next action, so you can read the prompt and output at the step where it re-selected the tool,
which usually reveals a missing observation in state, an ambiguous tool description or no stopping
instruction. Fix the prompt or add a step budget, then run `evaluate` against the looping dataset with a code
evaluator that counts tool runs in the trace, and compare the experiment with the baseline. Ship behind a
canary project, and keep an online evaluator or feedback key tracking the loop rate over time.

## Quiz
### Q: Which setup produces full traces for a LangGraph application with the least code?
- [ ] Wrap every node function in `@traceable` and every model client in `wrap_openai`
- [x] Set `LANGSMITH_TRACING=true` with an API key and project name; the framework's callbacks already emit runs
- [ ] Add OpenTelemetry auto-instrumentation and point the exporter at LangSmith
- [ ] Export the graph to JSON and upload it as a dataset
> LangChain and LangGraph already fire callbacks for every chain, node, model and tool; the tracer handler
> turns them into runs. Decorators and wrappers are for code outside the framework, and OTLP works but is not the minimal path.

### Q: What is a Thread in LangSmith?
- [ ] A Python thread the SDK uses to post runs in the background
- [x] A set of traces grouped by a shared conversation key in their metadata, shown as one conversation
- [ ] The tree of runs under a single root run
- [ ] A queue of traces waiting for human annotation
> Each turn of a chat is its own trace; a thread stitches turns together through `session_id`, `thread_id`
> or `conversation_id` metadata on the root run, which is why the key must be set there.

### Q: How do online evaluators differ from offline `evaluate` experiments?
- [x] Online evaluators score a sample of live production traces and write feedback; `evaluate` runs a target over dataset examples to produce a comparable experiment
- [ ] Online evaluators use LLM judges while `evaluate` only supports code evaluators
- [ ] `evaluate` runs in the browser playground while online evaluators run in the SDK
- [ ] They are the same mechanism with different names in Python and JavaScript
> Online evaluation answers "how is production doing right now" on traffic you did not choose; offline
> experiments answer "is this change better" on a fixed dataset. Both can use code or judge evaluators.
