---
id: langsmith
module: observability
name: LangSmith
kind: tool
difficulty: core
est_minutes: 15
status: typed-verified
lesson_type: lab
agent_relevance: core
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
LangSmith is LangChain Inc.'s closed-source platform for tracing, evaluating and monitoring agents: run
trees of every node, model call and tool call, feedback, online evaluators and dataset experiments. Any
code can use `@traceable`; for LangGraph agents tracing is an environment-variable switch.

## For agent builders
- **Why it matters for agents.** A LangGraph agent's behaviour lives in node state and tool choices.
  LangSmith shows each node, the `llm` run that chose the next action and every `tool` run, so loops,
  wrong tool picks and slow steps are visible without extra instrumentation.
- **Where it shows up in your stack.** `LANGSMITH_TRACING=true` for LangChain and LangGraph, `@traceable`
  and `wrap_openai` for plain Python agents, run-tree headers for remote tool services, and `evaluate`
  in CI for agent regression tests.
- **What you control.** Project per environment, thread keys on the root run, `run_type` on tools and
  retrievers, input and output hiding, online evaluator sampling, and the evaluators that score trajectories.
- **What you can skip.** The deployment side of the LangSmith brand and the transport internals. Know that
  runs post on start and end, and that `dotted_order` rebuilds the tree.

## Lab
### Task
Turn a recorded LangSmith trace of a looping refund agent into evidence, then build the release gate that
would have stopped it. The run posts arrive out of order, the process was killed mid-refund, and the
current gate ships any candidate whose pass rate did not drop. Time box: 60 minutes. Prerequisites:
Golden dataset and Agent trajectory evaluation.
- **Trajectory:** tool calls in execution order, rebuilt from `dotted_order`, never from arrival order.
- **Crash signal:** runs with a start record and no end record.
- **Gate:** fixed and broken examples by id; no ship when a critical example breaks.

### Starter code
Save as `lab_langsmith.py`. Plain Python 3.12 plus `pip install pytest` (checked with pytest 9.1.1). No
API key is needed: `POSTS` is a recorded trace in a simplified form of LangSmith's start (post) and end
(patch) records, with run ids shortened from UUIDs.

```python
import json

def seg(t: str, run_id: str) -> str:
    return f"20251020T1015{t}Z{run_id}"                 # start time (SSffffff) + run id; ids shortened

ROOT = seg("00000000", "r1")
def child(t, run_id, parent=ROOT):
    return f"{parent}.{seg(t, run_id)}"

ORDER = "1182"
# Recorded run posts for one production trace, in ARRIVAL order: background batches interleave,
# children end (and are patched) before their parent, and the process was killed during the refund.
POSTS = [
    {"op": "post", "id": "r1", "trace_id": "r1", "name": "refund_agent", "run_type": "chain", "dotted_order": ROOT},
    {"op": "post", "id": "r2", "trace_id": "r1", "name": "decide", "run_type": "llm", "dotted_order": child("00100000", "r2")},
    {"op": "patch", "id": "r2", "outputs": {"tool": "lookup_order"}},
    {"op": "post", "id": "r3", "trace_id": "r1", "name": "lookup_order", "run_type": "tool",
     "dotted_order": child("01000000", "r3"), "inputs": {"order_id": ORDER}},
    {"op": "post", "id": "r4", "trace_id": "r1", "name": "decide", "run_type": "llm", "dotted_order": child("01300000", "r4")},
    {"op": "patch", "id": "r3", "outputs": {"status": "shipped", "days_late": 3}},
    {"op": "post", "id": "r6", "trace_id": "r1", "name": "decide", "run_type": "llm", "dotted_order": child("02400000", "r6")},
    {"op": "post", "id": "r7", "trace_id": "r1", "name": "issue_refund", "run_type": "tool",
     "dotted_order": child("03100000", "r7"), "inputs": {"order_id": ORDER}},
    {"op": "post", "id": "r5", "trace_id": "r1", "name": "lookup_order", "run_type": "tool",
     "dotted_order": child("02100000", "r5"), "inputs": {"order_id": ORDER}},
    {"op": "patch", "id": "r4", "outputs": {"tool": "lookup_order"}},
    {"op": "patch", "id": "r5", "outputs": {"status": "shipped", "days_late": 3}},
    {"op": "patch", "id": "r6", "outputs": {"tool": "issue_refund"}},
]
# Per-example results of the refund_decision_correct evaluator in two experiments.
BASELINE = {"e1": True, "e2": False, "e3": True, "e4": False, "e5": True}
CANDIDATE = {"e1": True, "e2": True, "e3": False, "e4": True, "e5": True}
CRITICAL = {"e3"}                                         # tagged examples that must never regress

def merge_runs(posts: list) -> dict:
    """Start records create a run; end records (patches) add outputs and mark it ended."""
    runs: dict = {}
    for p in posts:
        if p["op"] == "post":
            runs[p["id"]] = {**p, "ended": False}
        else:
            runs[p["id"]].update(outputs=p["outputs"], ended=True)
    return runs

def tool_calls(runs: dict, trace_id: str) -> list:
    # BUG: arrival order is not execution order, so the trajectory is scrambled.
    # YOUR CODE 1: return "name:json-args" for the trace's tool runs in execution order (dotted_order).
    return [f'{r["name"]}:{json.dumps(r["inputs"])}' for r in runs.values()
            if r["run_type"] == "tool" and r["trace_id"] == trace_id]

def pending_runs(runs: dict) -> list:
    # BUG: assumes every run that started also ended.
    # YOUR CODE 2: names of runs with a start record but no end record, in dotted_order.
    return []

def no_repeated_calls(outputs: dict) -> bool:             # trajectory evaluator, as in this lesson
    return len(outputs["tool_calls"]) == len(set(outputs["tool_calls"]))

def gate(baseline: dict, candidate: dict, critical: set) -> dict:
    # BUG: ships whenever the pass rate does not drop, hiding a critical regression.
    # YOUR CODE 3: line results up by example id; return fixed (fail -> pass) and broken (pass -> fail)
    #              as sorted lists, and ship=False if any critical example broke or the pass rate fell.
    ship = sum(candidate.values()) >= sum(baseline.values())
    return {"fixed": [], "broken": [], "ship": ship}

if __name__ == "__main__":
    runs = merge_runs(POSTS)
    calls = tool_calls(runs, "r1")
    print("tool calls:", calls)
    print("no_repeated_calls:", no_repeated_calls({"tool_calls": calls}))
    print("pending:", pending_runs(runs))
    g = gate(BASELINE, CANDIDATE, CRITICAL)
    print(f"gate: fixed={g['fixed']} broken={g['broken']} ship={g['ship']}")
```

The starter lists the refund before the second lookup, reports `pending: []`, and prints
`gate: fixed=[] broken=[] ship=True` although the candidate broke the critical example `e3`.

### Expected output
With a correct solution, `python lab_langsmith.py` prints:

```
tool calls: ['lookup_order:{"order_id": "1182"}', 'lookup_order:{"order_id": "1182"}', 'issue_refund:{"order_id": "1182"}']
no_repeated_calls: False
pending: ['refund_agent', 'issue_refund']
gate: fixed=['e2', 'e4'] broken=['e3'] ship=False
```

### Tests
Save as `test_lab_langsmith.py` and run `pytest -q`. On the starter all five fail: the trajectory is in
arrival order, nothing is pending, and the gate returns empty lists and ships.

```python
import random
from lab_langsmith import BASELINE, CANDIDATE, CRITICAL, POSTS, gate, merge_runs, pending_runs, tool_calls

LOOKUP, REFUND = 'lookup_order:{"order_id": "1182"}', 'issue_refund:{"order_id": "1182"}'

def test_tool_calls_follow_execution_order():
    assert tool_calls(merge_runs(POSTS), "r1") == [LOOKUP, LOOKUP, REFUND]

def test_order_does_not_depend_on_arrival():
    starts = [p for p in POSTS if p["op"] == "post"]
    random.Random(7).shuffle(starts)                      # any arrival order of start records
    assert tool_calls(merge_runs(starts), "r1") == [LOOKUP, LOOKUP, REFUND]

def test_killed_process_leaves_pending_runs():
    assert pending_runs(merge_runs(POSTS)) == ["refund_agent", "issue_refund"]

def test_gate_blocks_a_critical_regression_despite_a_better_average():
    assert gate(BASELINE, CANDIDATE, CRITICAL) == {"fixed": ["e2", "e4"], "broken": ["e3"], "ship": False}

def test_gate_without_critical_tags_still_reports_regressions():
    assert gate(BASELINE, CANDIDATE, set()) == {"fixed": ["e2", "e4"], "broken": ["e3"], "ship": True}
    assert gate({"e1": True, "e2": True}, {"e1": False, "e2": False}, set())["ship"] is False
```

The candidate's pass rate rises from 3 of 5 to 4 of 5, which is exactly why an average-only gate ships it.

### Failure scenarios
- **Sort by end time.** The killed `issue_refund` run has no end and either crashes the sort or drops out,
  so the trajectory hides the step that was in flight. Debug: sort on `dotted_order`, which every start record has.
- **Drop runs without outputs.** `pending_runs` can never fire, and a crash looks like a short, clean run.
  Debug: count start records against end records per trace.
- **Compare pass rates only.** The gate ships `e3`'s regression. Debug: print fixed and broken ids, then open
  the broken run trees before shipping.
- **Treat the repeated lookup as harmless.** The answer can still be right while each loop costs a model
  call. Debug: chart tool runs per trace in the project dashboard.

### Submission
- `lab_langsmith.py` and a `pytest -q` run showing 5 passed.
- The four printed lines, matching the expected output exactly.
- A short note on what the pending `issue_refund` run means for the customer (was the refund issued?).
- The CI command you would run to apply `gate` to two real `evaluate` experiments.
- A reviewer checks that nothing in the solution depends on the arrival order of `POSTS`.

## Why it exists
- **Abstractions hid the model input.** A LangChain chain composed prompt templates, retrievers and parsers;
  when the answer was wrong you could not tell which piece produced the bad input.
- **Callbacks were already there.** The framework fired a callback on every chain, LLM and tool start and
  end; LangSmith grew out of turning those into persistent, queryable run trees.
- **`verbose=True` is not observability.** Console output does not persist, cannot be searched by user or
  compared across versions, and vanishes in production.
- **"The new prompt seems better" is not evidence.** Teams need a dataset with reference outputs, a way to
  run a candidate, evaluators that score each result, and a side-by-side view. LangSmith ties this to
  production: a bad trace becomes a dataset example with one click, an experiment is itself a set of traces,
  and feedback from users, annotators and judges lives on the same runs.
- **Most integrated, least portable.** Deepest LangGraph fit (node-level state, tool calls and token streams
  appear without instrumentation), but proprietary, its own data model, and your data sits in LangChain's
  cloud unless you self-host under an enterprise agreement. Langfuse offers the comparable loop as open
  source; Phoenix offers it on plain OpenTelemetry.

## How it works
### Runs, traces, projects and threads
| Object | What it is |
|---|---|
| **Run** | One unit of work with a `run_type` (`llm`, `chain`, `tool`, `retriever`, `embedding`, `prompt`, `parser`), inputs, outputs, start and end times, an error if any, tags, metadata and a parent run id |
| **Trace** | The root run and its tree |
| **Project** | A container for traces, usually one per application and environment |
| **Thread** | Traces sharing a conversation key in metadata (`session_id`, `thread_id` or `conversation_id`), reassembled into a conversation view so you read a multi-turn chat as the user experienced it |

- **Every run carries `trace_id`** (the root's id) and a `dotted_order` string built from the start time and
  id of every ancestor.
- **Why `dotted_order` exists:** runs arrive out of order. A child usually finishes, and is posted, before its
  parent, and batches from two processes interleave. Sorting by `dotted_order` reconstructs the tree and
  sibling order deterministically without waiting for the parent.

### How runs get created
| Path | Mechanism |
|---|---|
| LangChain or LangGraph | `LANGSMITH_TRACING=true` plus an API key and project name installs a tracer callback handler; every chain, node, model call and tool invocation becomes a run with no code change |
| `@traceable(run_type=...)` | Wraps any function: reads the current run from a context variable, creates a child run, records arguments, return value and exceptions, restores the parent on exit |
| `wrap_openai`, `wrap_anthropic` | Wrap provider clients so each completion becomes an `llm` run with token usage, including streamed responses, which the wrapper reassembles |
| `RunTree` | Manual control when decorators do not fit |

- **Across services,** the caller serialises its run tree to headers (`get_current_run_tree().to_headers()`)
  and the callee opens `tracing_context(parent=headers)`, so its runs become children in the same trace.
- **This is LangSmith's own format, not W3C `traceparent`,** which matters when the hop also runs through
  OpenTelemetry-instrumented middleware.

### Transport
- **Background batches.** Runs are queued and posted to the LangSmith API, so tracing stays off the request
  path.
- **Posted twice.** Once on start (inputs, start time, parent) and once on end (outputs, error, end time,
  usage). That is why an in-flight agent step shows as pending, and why a crash leaves runs with inputs but
  no end.
- **Drain before exit.** A short-lived process must wait for the queue (`wait_for_all_tracers()` or the
  client's flush) or child runs are lost.
- **OTLP endpoint.** OpenTelemetry-instrumented services can send spans into a project without the SDK.

### Feedback and online evaluation
- **Feedback** is a record with a key, a numeric score or categorical value and an optional comment, attached
  to a run.
- **Sources:**
  1. End-user signals sent with the run id.
  2. Human **annotation queues**.
  3. **Online evaluators**: LLM-as-a-judge or code rules on a sampled percentage of incoming traces, writing
     feedback automatically.
  4. **Automation rules** acting on a filter: add matching traces to a dataset, send them to an annotation
     queue, or call a webhook.
- **One shape for all,** so monitoring charts plot feedback over time next to latency, token and cost series.

### Datasets and experiments
- **A dataset** holds examples with inputs, reference outputs and metadata, with versions and splits.
- **`evaluate(target, data, evaluators, experiment_prefix)`** runs the target on every example, traces each
  run, applies the evaluators (plain functions or LLM judges that receive inputs, outputs and reference
  outputs) and records results as feedback. The result is an **experiment** you compare with others.
- **Pairwise evaluators** score two experiments against each other for preference judgements.
- **The playground** launches the same experiments to test prompt variants and models without code.

### Branding scope
As of late 2025 LangChain uses the LangSmith name for its broader platform, including the agent deployment
product previously called LangGraph Platform. This lesson concerns only observability and evaluation;
tracing does not require deploying through LangSmith.

### Prompts and monitoring
- **Prompt hub** versions prompts by commit; `client.pull_prompt("name")` returns a LangChain prompt template,
  and runs record which commit they used.
- **Project dashboards** show trace volume, error rate, latency percentiles, tokens, cost (from a model price
  map you can extend) and feedback trends, with threshold alerts.

## Diagram
Production runs land in a project; online evaluators and rules turn some into feedback and dataset examples;
`evaluate` turns a candidate into an experiment you compare before shipping.

```mermaid
flowchart TD
    APP["Agent: traceable or LangGraph"] -->|"start and end posts"| PJ[("Project: run trees")]
    PJ --> TH["Threads and dashboards"]
    PJ --> OE["Online evaluators on a sample"]
    OE --> FB["Feedback on runs"]
    PJ --> AR["Automation rule on a filter"]
    AR --> AQ["Annotation queue"]
    AR --> DS[("Dataset")]
    DS --> EV["evaluate on candidate"]
    EV --> EXP["Experiment vs baseline"]
    EXP -->|"ship if better"| APP
```

## Examples
### Tracing plus an offline experiment, without LangChain
A traced retriever and chain, a wrapped OpenAI client so the model call is an `llm` run with tokens, a
`session_id` so traces form a thread, then an experiment with a code evaluator against a one-example golden
dataset. Evaluator signatures and the `{"output": ...}` wrapping follow the current SDK; older releases used
`(run, example)` evaluators.

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

- **In the UI:** `support_answer` → `retrieve` and `support_answer` → `ChatOpenAI`-style `llm` run; the
  experiment appears under the dataset with one traced run per example and a `mentions_reference` score.
- **Mechanically,** the production call creates three runs sharing one `trace_id`. `retrieve` starts and ends
  first, so its end record is often posted before `support_answer` has ended; `dotted_order` puts it in place.
- **The `llm` run** records the messages, the response and the provider's usage, from which cost is priced.
- **Thread grouping reads the root.** The `session_id` is set on the root call.
- **In the experiment,** `evaluate` calls the lambda once per example, so each example yields its own trace,
  and the evaluator's boolean becomes a feedback row keyed `mentions_reference` on that trace's root run.

### Joining a tool service to the caller's trace
LangSmith's propagation across an HTTP hop: the caller sends its run tree as headers, the callee continues it.
`do_work` is your own `@traceable` function.

```python
import httpx
import langsmith as ls
from fastapi import FastAPI, Request
from langsmith.run_helpers import get_current_run_tree

@ls.traceable(run_type="chain")
def call_tool_service(payload: dict) -> dict:
    headers = get_current_run_tree().to_headers()        # LangSmith trace headers, not W3C traceparent
    return httpx.post("http://tools:8000/run", json=payload, headers=headers).json()

app = FastAPI()                                          # the tool service

@app.post("/run")
async def run(request: Request):
    payload = await request.json()
    with ls.tracing_context(parent=request.headers):     # runs inside become children of the caller's run
        return await do_work(payload)
```

If a proxy strips unknown headers, the callee's runs appear as separate root traces.

### Evaluating an agent's trajectory, not just its answer
A plain-Python agent whose tools are `tool` runs, evaluated on whether it finished, whether its refund
decision matches the reference and whether it repeated a tool call. The target returns the tool sequence so
code evaluators can read it.

`decide` stands for one model call (through a wrapped client) that returns either a tool request or a final
answer; each `refund-tasks` example has a `should_refund` reference.

```python
from langsmith import traceable, evaluate

@traceable(run_type="tool")
def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id, "status": "shipped", "days_late": 3}

@traceable(run_type="tool")
def issue_refund(order_id: str) -> dict:
    return {"order_id": order_id, "refunded": True}

TOOLS = {"lookup_order": lookup_order, "issue_refund": issue_refund}

@traceable(run_type="chain", name="refund_agent")
def refund_agent(task: str, max_steps: int = 6) -> dict:
    calls, observations = [], []
    for _ in range(max_steps):
        step = decide(task, observations)                 # an 'llm' run under this trace
        if step["type"] == "final":
            return {"answer": step["text"], "tool_calls": calls}
        calls.append(f'{step["tool"]}:{step["args"]}')
        observations.append(TOOLS[step["tool"]](**step["args"]))
    return {"answer": None, "tool_calls": calls}

def no_repeated_calls(outputs: dict) -> bool:             # trajectory evaluator
    return len(outputs["tool_calls"]) == len(set(outputs["tool_calls"]))

def answered(outputs: dict) -> bool:
    return outputs["answer"] is not None

def refund_decision_correct(outputs: dict, reference_outputs: dict) -> bool:   # outcome evaluator
    refunded = any(c.startswith("issue_refund:") for c in outputs["tool_calls"])
    return refunded == reference_outputs["should_refund"]

evaluate(lambda inputs: refund_agent(inputs["task"]), data="refund-tasks",
         evaluators=[no_repeated_calls, answered, refund_decision_correct],
         experiment_prefix="refund-agent-v3")
```

- **Each example is one traced agent run,** so a failing `no_repeated_calls` score links straight to the
  run tree where the repeat happened.
- **The answer can be right while the trajectory is wrong;** scoring both catches agents that succeed by
  brute force and would be slow and expensive in production.

### Worked comparison: reading two experiments
Illustrative numbers for a 200-example golden set, baseline versus `system-prompt-v2`.

| | Baseline | v2 |
|---|---|---|
| Examples passing `mentions_reference` | 150 | 161 |
| Fixed by v2 (fail → pass) | | 14 |
| Broken by v2 (pass → fail) | | 3 |

The comparison view lines up 200 pairs of runs by example id, which is what makes "v2 fixed 14 examples and
broke 3" a readable statement rather than an average. Open the 3 regressions before shipping.

## Production patterns
- **Project per environment.** Set `LANGSMITH_PROJECT` per deployment (for example `support-bot-dev`,
  `-ci`, `-prod`) so test runs never pollute dashboards or spend online-evaluator budget. Monitor trace
  volume per project.
- **Experiments as a CI gate.** Run `evaluate` against the golden dataset on every prompt or model change and
  fail the build if the pass rate drops or any tagged critical example regresses. Monitor pass rate per
  experiment over time.
- **Rules that grow the dataset.** An automation rule on low user feedback or long traces routes matches to an
  annotation queue and a dataset, so the golden set tracks real failures. Monitor queue backlog.
- **Sampled online judges, calibrated.** Apply online evaluators to a small percentage of production traces
  and compare with annotation-queue labels weekly. Monitor judge-human agreement and judge cost.
- **Trajectory evaluators for agents.** Alongside answer checks, score tool-call count, repeated calls and
  whether required tools ran, using code evaluators over the agent's output or run tree. Monitor tool runs
  per trace and the repeated-call rate in production dashboards, because loops show up there before users
  complain.
- **Hide sensitive payloads.** Configure the client's `hide_inputs` and `hide_outputs` processors to redact
  personal data before runs are posted. Monitor a sampled audit of stored runs.
- **Drain in serverless.** Call `wait_for_all_tracers()` or the client's flush at the end of each handler.
  Monitor root runs with missing children.

## Trade-offs
- **Integration vs lock-in.** Deepest LangChain and LangGraph integration (node state, tool calls, streamed
  tokens for free) against a proprietary data model and hosted service. Flips when procurement requires data
  residency and enterprise self-hosting is not on the table.
- **Zero operations vs volume pricing.** Managed SaaS needs no operations, but pricing follows ingested traces
  with retention tiers, so at high volume you sample, shorten retention, or move to a self-hosted open-source
  alternative.
- **Fidelity vs privacy.** `@traceable` records arguments and return values automatically, which is high
  fidelity and also captures personal data and large payloads; input and output processors or hiding inputs
  trade that fidelity back.
- **Online judges vs cost and bias.** Online evaluators give a continuous quality signal on production
  traffic at judge-model cost per sampled trace and with judge bias; annotation queues are the calibration,
  not an optional extra.
- **Prompt hub vs runtime dependency.** It decouples prompt edits from deploys for LangChain templates, but
  your runtime now depends on a pull at start-up or request time; cache it and fall back to a bundled version.

## Pitfalls
- **Wrong environment-variable family.** The older `LANGCHAIN_TRACING_V2` and `LANGCHAIN_API_KEY` names versus
  the current `LANGSMITH_*` names, or a key from another workspace. Symptom: a working app and an empty
  project.
- **Serverless returns before the queue drains.** Symptom: root runs with missing children. Fix: wait for
  tracers before returning.
- **Wrapped client outside any traceable context.** Every model call is its own trace. Symptom: hundreds of
  one-run traces and no threads.
- **Thread key on the wrong run.** The metadata key is missing or set on a child rather than the root.
  Symptom: a conversation view with one turn per thread.
- **Evaluators reading the wrong key.** They read `outputs["answer"]` when the target returned a string
  (stored as `{"output": ...}`) or a dict with a different key. Symptom: every example erroring or scoring
  zero.
- **Dev, CI and production in one project.** Symptom: dashboards and online evaluators polluted by test runs,
  and sampled judges spending budget on fixtures.

## When to use
- **Use when** the app is built on LangChain or LangGraph and a hosted platform is acceptable: agent-level
  traces, threads and evaluations with almost no instrumentation, and a mature dataset-to-experiment loop.
- **Compare first when** the code is plain Python or TypeScript through `@traceable`: the integration
  advantage shrinks, and Langfuse (open source, self-hostable) or Phoenix (OpenTelemetry-native) deserve a
  direct comparison on data residency and cost.
- **Measure:** trace completeness per environment, the fraction of production traces that receive feedback,
  and judge-versus-human agreement on the annotation queue.
- **Day to day,** it is where you reproduce a customer's bad conversation and where a prompt change proves
  itself against the golden dataset before release.
- **Interviews:** explain the run tree, how threads are formed, and the difference between online evaluators
  and offline experiments.

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

## Interview questions
### What is a run tree, and why does each run carry a dotted_order?
- **Definition:** every unit of work is a run with a type, inputs, outputs, timing and a parent; the root run
  and its descendants form the trace.
- **Why `dotted_order`:** children usually finish and post before parents and batches interleave across
  processes; an ancestor-encoded sort key rebuilds the tree deterministically without waiting.

### Each chat turn shows as a separate conversation in the Threads view. How do you debug it?
- **Threads group by a metadata key** (`session_id`, `thread_id` or `conversation_id`) on the root run.
- **Check** the key exists on every turn, is set on the root rather than a child, and is stable across turns
  (not regenerated per request).
- **Also check** model calls are inside a traceable context, otherwise each becomes its own trace.

### When would you not choose LangSmith?
- **When data must stay in your network** and enterprise self-hosting is not available; prompts with customer
  data then rule out the hosted service.
- **When volume makes per-trace pricing dominant** and sampling would hide the failures you need.
- **When the stack is not LangChain,** the integration advantage shrinks, so compare Langfuse or Phoenix on
  cost and residency.

### Some agent tool and step runs show inputs but never an output or end time. What happened?
- **Runs are posted twice,** once on start and once on end, so a run with only a start record means the end
  post never arrived.
- **Likely causes:** the process crashed or was killed mid-step, or a serverless handler returned before the
  background queue drained.
- **Fix:** wait for tracers or flush at the end of each handler, and treat persistent pending runs as a crash
  signal worth alerting on.

### How would you build a regression gate for a LangGraph agent before each release?
- **Dataset of real tasks:** grow it from production failures through automation rules and annotation, with
  reference answers and, where it matters, the tools that must or must not be called.
- **Evaluators on answer and trajectory:** correctness, tool-call count, repeated calls and step budget hits,
  run with `evaluate` in CI against the candidate graph.
- **Gate on regressions,** not only averages: fail the build when tagged critical examples flip from pass to
  fail, and open those run trees before shipping.

### How would you size online judge evaluation for 20,000 traces a day?
- **Pick a sampled percentage,** for example 5% gives about 1,000 judged traces a day; multiply by judge prompt
  plus trace tokens and the judge model price (rough planning figures).
- **Budget human calibration** too: a weekly annotation queue sized to estimate judge-human agreement, because
  that agreement is what makes the automated number trustworthy.

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

## Use cases & projects
### Use cases
- **Debugging LangGraph agents.** Node-level run trees show the state and the `llm` run behind every tool
  choice, so a loop or a wrong tool pick is traced to its step.
- **Agent regression gates in CI.** `evaluate` over a dataset of real tasks, with answer and trajectory
  evaluators, blocks releases that break critical examples.
- **Multi-turn agent review.** Threads reassemble a customer's conversation so a reviewer reads every turn
  and tool call as the user experienced it.
- **Growing the eval set from production.** Automation rules route low-feedback or long agent traces into
  annotation queues and datasets.

### Mini project
**Catch a looping agent with a trajectory eval (2-3 hours).**
- **Goal:** detect and fix an agent that repeats tool calls, with evidence from experiments.
- **Steps:** build a small refund agent with two `@traceable(run_type="tool")` tools and a vague tool description
  that invites repeats; create a 20-task dataset with a `should_refund` reference per task; write
  `no_repeated_calls`, `answered` and `refund_decision_correct` evaluators; run a baseline experiment;
  tighten the tool descriptions or add a step budget; run a second experiment.
- **Done when:** the comparison view shows the repeated-call failures falling between experiments, with at
  least one run tree opened to explain the original loop, and no example that passed before now failing.
