---
id: langfuse
module: observability
name: Langfuse
kind: tool
difficulty: core
est_minutes: 15
related:
  - observability/tracing
  - observability/token-cost-tracking
  - observability/opentelemetry
  - observability/langsmith
  - observability/arize-phoenix
  - evals/tooling
  - evals/llm-as-a-judge
  - prompting/prompt-versioning
sources:
  - Official docs: Langfuse
  - Official docs: OpenTelemetry Semantic Conventions for Generative AI
---

## TL;DR
Langfuse is an open-source, self-hostable LLM engineering platform. It records nested observations
(spans, generations, events) carrying the rendered prompt, the completion, token usage and cost, grouped
into traces, sessions and users, and closes the loop with scores (user feedback, human annotation,
LLM-as-a-judge), datasets and experiments, and versioned prompt management. Choose it for the
trace-to-eval-to-prompt loop in one framework-independent tool that can run in your own infrastructure.

## Why it exists
A generic APM sees a model call as a three-second `POST` to a provider that returned 200. Debugging an LLM
feature needs what was inside: the exact rendered prompt, the completion, the tool calls, the retrieved
chunks, how many tokens it cost and which conversation it belonged to. It also needs a quality signal,
because HTTP 200 says nothing about a wrong answer. And it needs a loop: find the bad traces, annotate
them, turn them into a dataset, run the next prompt version against that dataset, compare, promote.

Without a tool, teams rebuild this badly. Prompts and completions are dumped to JSON logs that nobody can
query by user; costs are reconstructed monthly from the provider invoice; prompts live in code, so every
wording change is a deployment; and "the new prompt is better" rests on three examples in a notebook.

Langfuse's position among the alternatives named in this module is specific. LangSmith is the deepest fit
for LangChain and LangGraph but is proprietary and hosted unless you buy enterprise self-hosting. Arize
Phoenix is OpenTelemetry-native and strongest on evaluation and retrieval diagnostics in notebooks. Langfuse
is framework-agnostic, product-complete (cost, sessions, scores, datasets, prompt management in one UI),
MIT-licensed at its core, and runs in your VPC with a Docker Compose or Helm deployment, which matters when
prompts contain customer data.

## How it works
### Data model
A **trace** is one unit of work, typically one request or one agent task. It carries `user_id`,
`session_id`, tags, metadata, an input and output, and a release or version label. Inside it, nested by
parent, are **observations** of three types: a **span** (any step: retrieval, parsing, a tool call), a
**generation** (a model call, with model name, parameters, input messages, output, usage split into input
and output tokens, cost, and a completion-start timestamp from which time to first token is derived), and an
**event** (a point in time). **Sessions** group the traces of one conversation so a reviewer reads it in
order; **users** roll up cost and quality per customer.

**Scores** attach a named numeric, categorical or boolean value, with an optional comment, to a trace or an
observation. They come from four places: your code or API (a thumbs-up, a programmatic check), human
**annotation queues** in the UI, managed **LLM-as-a-judge evaluators** that run a judge prompt over a
sampled share of incoming traces, and evaluators run during experiments. Because scores share one model,
the same dashboard compares human labels, judge labels and user feedback per prompt version.

**Prompts** are named, versioned objects with labels such as `production` or `staging`. The SDK fetches a
prompt by name and label, caches it with a TTL and falls back to the cached or a supplied default when the
server is unreachable; a generation records which prompt version produced it, so latency, cost and scores
are broken down by version. This exists *because* prompt edits are the most frequent "deploy" in an LLM
product and should not require a code release, while still being versioned and measurable.

**Datasets** hold items with an input, an expected output and metadata. A dataset run executes your
application on each item, links the resulting trace to the item, and attaches evaluator scores; the UI
compares runs side by side.

### Ingestion path
The SDK does not block your request. Events are created in memory, batched, and posted by a background
thread, which is why tracing adds negligible latency and why a script or serverless function must call
`flush()` before it exits. On the server, the current architecture writes raw events to object storage,
queues them through Redis and has workers materialise them into ClickHouse, a columnar store, because trace
analytics are large scans and aggregations over millions of observations, which the earlier Postgres-only
design handled poorly at scale; Postgres keeps transactional data such as projects, prompts and API keys.
Cost is computed per generation by matching the model name against a price table (built in for the major
providers, extensible with custom model definitions for fine-tunes, Azure deployment names or self-hosted
models); usage comes from the provider response when present, and can be estimated with a tokenizer for
some models when it is not.

### Instrumentation options
In Python the `@observe()` decorator wraps a function in a span, nests calls through context variables and
turns the outermost call into the trace; `as_type="generation"` marks a model call. The OpenAI drop-in
(`from langfuse.openai import openai`) captures model, messages, usage and latency automatically. There are
a LangChain callback handler, LlamaIndex and LiteLLM integrations, a JS/TS SDK with Vercel AI SDK support,
and an OTLP endpoint, so spans produced by any OpenTelemetry SDK, OpenLLMetry or OpenInference
instrumentation are mapped onto generations using the GenAI attribute conventions. The current Python SDK
is itself built on the OpenTelemetry SDK. A `mask` callback redacts fields before data leaves the process.

## Example
A support bot traced with the Python SDK: the decorator builds the trace, the OpenAI drop-in records the
generation, the prompt comes from prompt management and is linked to the generation, and the trace is tagged
with user and session so cost and conversations can be reviewed per customer. Method names follow the
current (v3) Python SDK; the earlier SDK exposed the same ideas through `langfuse_context`.

```python
# pip install langfuse openai ; env: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and the host URL if self-hosted
from langfuse import observe, get_client
from langfuse.openai import openai         # drop-in client: every call becomes a 'generation' observation

langfuse = get_client()
PROMPT = langfuse.get_prompt("support-answer", label="production")   # versioned in the UI, cached client-side

@observe()                                  # a span, nested under whatever observation is current
def retrieve(question: str) -> list[str]:
    return ["Reset your password under Settings > Security > Reset password."]

@observe()                                  # the outermost @observe becomes the trace
def answer(question: str, user_id: str, session_id: str) -> str:
    langfuse.update_current_trace(user_id=user_id, session_id=session_id, tags=["support"])
    docs = retrieve(question)
    resp = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=PROMPT.compile(context="\n".join(docs), question=question),   # chat prompt -> messages
        langfuse_prompt=PROMPT,             # links this generation to the prompt name + version
    )
    trace_id = langfuse.get_current_trace_id()      # hand this to the client so feedback can be scored later
    return resp.choices[0].message.content

print(answer("How do I reset my password?", user_id="u_42", session_id="s_9"))

# Later, when the user clicks thumbs-up: langfuse.create_score(trace_id=..., name="user_feedback", value=1)
langfuse.flush()                            # required in scripts and serverless: events are sent in background batches
```

In the UI the trace shows `answer` → `retrieve` and `answer` → generation, with tokens, cost, latency and the
prompt version; the session view shows the whole conversation `s_9`; the user view sums cost for `u_42`.

## Trade-offs
- Self-hosting keeps prompts and customer data in your VPC with no licence cost, but you now operate
  Postgres, ClickHouse, Redis and object storage; the balance flips to Langfuse Cloud when the team has no
  platform capacity or the volume is small.
- Framework-agnostic decorators work with any code, but you place them yourself; LangSmith produces
  node-level LangGraph traces with no decorators, which matters when the stack is entirely LangGraph.
- Capturing full inputs and outputs is what makes a trace debuggable, and is also PII and storage; masking
  and sampling cut both the risk and the debuggability, so decide per field rather than globally.
- Managed LLM-as-a-judge evaluators give a continuous quality signal on production without labelling, at a
  judge-model cost per evaluated trace and with judge bias; the sample rate is the knob, and a human
  annotation queue is the calibration.
- Prompt management decouples wording changes from deploys, but adds a runtime dependency on Langfuse;
  the SDK cache and fallback mitigate it, and the trade is poor if prompts change rarely and must go
  through code review anyway.

## Pitfalls
- Missing or truncated traces from Lambda, Cloud Run jobs or scripts because `flush()` was never called;
  the symptom is that everything works locally in a long-lived process and vanishes in production.
- Tokens present but cost blank, because the model name (an Azure deployment name, a fine-tune id, a
  vLLM-served open model) matched nothing in the price table; the fix is a custom model definition.
- Flat traces: the decorator sits on the wrong boundary or async context was lost, so generations appear as
  separate root traces and sessions look empty.
- No `user_id` or `session_id` on traces, so nobody can answer "which customer burned $300 yesterday" or
  review a conversation end to end.
- Feedback scored against the wrong trace id because the id was regenerated or never returned to the
  client; the symptom is scores that attach to nothing or to another user's trace.
- Treating a judge score as ground truth; the symptom is a quality dashboard that disagrees with the humans
  in the annotation queue, which nobody set up.
- Sending prompts containing secrets or personal data to a hosted instance without `mask`; the symptom is a
  compliance finding, not an error message.

## When to use
Choose Langfuse when you need traces with prompts, tokens and cost per user and session, want evaluation
and prompt versioning in the same place as the traces, and either need self-hosting for data residency or
want to avoid lock-in to a framework vendor. Prefer LangSmith when the application is LangGraph end to end
and the hosted model is acceptable; prefer Phoenix when the work is evaluation-heavy notebook analysis over
OpenTelemetry traces you already produce. Measure ingestion lag, the share of generations with a matched
cost, the fraction of traces carrying `user_id`, and agreement between judge scores and human annotations.
Day to day it is the tab you open when a customer forwards a bad answer; in interviews, be ready to explain
the data model, why cost needs a price table, and how you would run a prompt change through a dataset
before promoting its label.

## Role lens
- **Engineer:** Adds `@observe` at the request and tool boundaries, tags traces with user and session,
  defines custom model prices, and wires thumbs-up feedback to `create_score`.
- **Architect:** Decides self-host versus cloud, what masking is mandatory, which prompts are governed by
  Langfuse labels versus Git, and how judge sampling fits the evaluation budget.
- **FDE:** Stands up a self-hosted instance inside the customer's network, uses sessions to walk through a
  failing conversation with their team, and exports datasets from real traces for acceptance testing.

## Check yourself
**Q:** A support bot handles 50,000 conversations a day. Product wants cost per customer, a weekly quality
number, and the ability to change the tone of answers without a deployment. How would you set this up with
Langfuse, and where would you be careful?
**A:** Trace every request with `user_id` and `session_id`, and define custom model prices for any model the
built-in table misses so per-user cost rolls up correctly. For quality, combine three score sources: user
feedback sent with the trace id, a managed LLM-as-a-judge evaluator on a sampled slice (a few percent is an
illustrative starting point; the judge costs tokens per trace), and a weekly annotation queue of around a
hundred traces that calibrates the judge and gives the human number product trusts. For tone, move the
system prompt into prompt management: edit a new version, run it as a dataset experiment against a golden
set built from annotated traces, compare scores, then move the `production` label. Be careful about
flushing in any serverless component, masking personal data if using the hosted service, SDK prompt
caching with a fallback so an outage of Langfuse never takes the bot down, and keeping dev and prod in
separate projects.

## Quiz
### Q: In Langfuse, what distinguishes a generation from a span?
- [ ] A generation is the root of the trace and spans are its children
- [x] A generation represents a model call and carries model, usage, cost and completion-start time; a span is any other step
- [ ] A span is synchronous and a generation is asynchronous
- [ ] Generations are created only by the OpenAI integration, spans only by the decorator
> Both are observations that nest freely. The generation type exists so token usage, cost and time to first
> token can be computed and aggregated; a retrieval or tool step is a span because it has none of those.

### Q: Why must a serverless function call `flush()` before returning when using the Langfuse SDK?
- [ ] Because traces are only created when `flush()` is called
- [ ] Because scores cannot be attached to an unflushed trace
- [x] Because events are batched and sent by a background thread, which may not run before the process is frozen or exits
- [ ] Because the SDK compresses traces only at flush time to reduce storage cost
> Asynchronous batching is what keeps tracing off the request path. The price is that a process that ends
> early takes its unsent batch with it; `flush()` forces the send.

### Q: A generation shows input and output tokens but no cost. What is the most likely cause?
- [ ] The generation is not nested inside a trace
- [ ] The trace has no `user_id`, so cost cannot be attributed
- [x] The model name did not match any entry in the price table, so no price could be applied
- [ ] Cost is only computed for traces that received a score
> Cost is tokens multiplied by a per-model price. Deployment aliases, fine-tune ids and self-hosted models are
> not in the built-in table; define a custom model that matches the name and the cost appears.
