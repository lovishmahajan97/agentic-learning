---
id: frameworks
module: agents
name: Frameworks (LangGraph, LangChain, LlamaIndex, Pydantic AI, DSPy)
kind: tool
difficulty: core
est_minutes: 15
related:
  - agents/agent-loop
  - agents/state-machines
  - agents/workflows-vs-agents
  - agents/typed-tool-contracts
  - agent-reliability/durable-execution-and-checkpointing
  - python-for-ai/pydantic
  - prompting/prompt-engineering
  - rag-retrieval/embedding-pipelines
sources:
  - Khattab et al., 2023 — DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines
  - Official docs: LangGraph
  - Official docs: LlamaIndex
  - Official docs: Pydantic AI
---

## TL;DR
Agent frameworks package the plumbing around model calls: control flow, state, tool wiring, retries,
persistence, observability. LangChain is the broad integration layer; LangGraph is a typed
state-machine runtime for agents; LlamaIndex is the data and retrieval framework with agent workflows
on top; Pydantic AI is a small, type-first agent library; DSPy replaces hand-written prompts with
declarative modules compiled against a metric. Each trades speed for abstraction you must understand,
so choose by the plumbing problem you actually have.

## Why it exists
A production agent is mostly not model calls. It is the loop, tool schemas and dispatch, argument
validation, retries and backoff, streaming, state that survives a crash, pausing for a human, tracing
every step, swapping providers, and a retrieval pipeline behind it all. Teams writing this from scratch
write the same two thousand lines, with the same bugs: tool results appended in the wrong order, a
resume that double-executes, a provider migration that touches every file. Frameworks exist to make
that plumbing reusable.

They also exist because the problems cluster differently. Some teams' hard problem is data: parsing,
chunking, indexing and querying documents; LlamaIndex grew from that. Some teams' problem is control
flow that must be inspectable and resumable; LangGraph grew from that after LangChain's early agent
abstractions proved too opaque for production. Some teams want typed, validated inputs and outputs with
no magic; Pydantic AI is that position. And some teams discovered that the prompts are the fragile part
and wanted to stop hand-tuning them; DSPy's answer is to treat prompts as parameters to be optimized.

The naive alternatives both have costs. Writing everything yourself is slower at the start and fine
later, if you are disciplined about the loop and persistence. Adopting a framework without
understanding what it does under the hood is fast at the start and painful later: hidden prompts you
cannot see, abstractions that leak the moment you need something slightly different, and version churn
that breaks your code. The skill is knowing which problem each framework actually solves, so you adopt
the layer you need and no more.

## How it works
### LangChain
The broad integration layer: a common interface over chat models, embeddings, vector stores, document
loaders and tools, plus composition primitives (prompt templates, output parsers, and the Runnable
pipe syntax, LCEL, for chaining them with streaming, batching and async handled uniformly). Under the
hood each integration is an adapter to a provider SDK; the value is that swapping providers or vector
stores is a one-line change. Its early agent abstractions (a ReAct loop driven by parsing the model's
text) are what pushed the project toward LangGraph for anything stateful. Sharp edges: deep class
hierarchies, prompts embedded in components you did not write, and a history of breaking changes
between versions.

### LangGraph
A runtime for agents as graphs: nodes are functions (often model calls or tool executions), edges are
fixed or conditional, and all nodes read and write a shared typed state (TypedDict or Pydantic model)
through reducers that define how updates merge. Execution proceeds in supersteps, inspired by
Pregel-style bulk synchronous processing, which is what makes parallel branches and fan-in
well-defined. The parts that matter for production: checkpointers persist the state after every step
to a store, which gives resume after crash, time travel for debugging, and interrupts, which pause a
graph at a node for human input and resume later from the checkpoint. It is the state-machine lesson
made concrete. Sharp edges: verbosity for simple cases, a learning curve around reducers and streaming
modes, and a pull toward the vendor's hosted platform and tracing product for the full experience.

### LlamaIndex
A data framework first: readers for many sources, node parsers (chunking), indexes (vector, keyword,
summary, knowledge graph), retrievers, response synthesizers and query engines, with an emphasis on
structured and multi-document retrieval. Agents came later: tool-using agents over query engines, and
an event-driven Workflows abstraction in which steps are functions that consume and emit typed events,
a different shape from LangGraph's state graph. Choose it when the hard problem is getting documents
into a model well; its agent layer is adequate, but the retrieval layer is the reason it exists. Sharp
edges: many abstractions with overlapping names, and defaults (chunk sizes, prompts) that work for
demos and need tuning for production.

### Pydantic AI
A deliberately small agent library from the Pydantic team: an `Agent` with a model, a system prompt,
tools registered with a decorator, a dependency type injected into tools through a run context, and a
typed output validated by Pydantic, with retries driven by validation failures (the model is shown the
validation error and asked again). It is model-agnostic, integrates natively with Logfire for tracing,
and ships a companion graph library for explicit state machines. Under the hood it is the plain agent
loop with schemas generated from type hints; nothing is hidden. Sharp edges: younger than the others,
so fewer integrations, and a fast-moving API in which names have been renamed between releases.

### DSPy
A different kind of thing: a programming model in which you declare *signatures* (typed input and
output fields with a docstring describing the task) and compose *modules* (Predict, ChainOfThought,
ReAct and others) into a program, then *compile* the program with an optimizer against a metric and a
small training set. Optimizers such as bootstrapped few-shot selection and instruction search generate
and select the demonstrations and instructions that maximize the metric; some can also fine-tune
weights. The prompt is an artifact the compiler produces, not something you write. It solves prompt
fragility and lets you re-optimize when the model changes. Sharp edges: you need a metric and labelled
examples before you get value, optimization costs model calls, and the resulting prompts are less
legible than hand-written ones.

### Comparing them
Ask which plumbing problem is yours. Integration breadth: LangChain. Inspectable, resumable control
flow with human interrupts: LangGraph. Document ingestion and retrieval quality: LlamaIndex. Typed tools
and outputs with minimal magic: Pydantic AI. Prompt optimization against a metric: DSPy. They combine:
DSPy modules inside a LangGraph node, LlamaIndex retrievers as Pydantic AI tools. What none of them
removes is the need to understand the loop, persistence and gating yourself, because when the
abstraction leaks, that is what you are debugging.

## Example
Two philosophies on the same task, support-ticket triage. Pydantic AI makes the tool and the output
type explicit and validates them; DSPy declares the task and leaves the prompt to the compiler. Model
identifiers are configuration, not code. Exact parameter names follow each library's current release.

```python
import os
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

class Triage(BaseModel):
    category: str            # billing | bug | feature
    priority: int            # 1 (urgent) to 4
    reason: str

agent = Agent(os.environ["MODEL"], deps_type=dict, output_type=Triage,
              system_prompt="Triage support tickets using the account data available through tools.")

@agent.tool
def account_plan(ctx: RunContext[dict], account_id: str) -> str:
    return ctx.deps["plans"][account_id]          # dependencies are injected, not global

result = agent.run_sync("Account 42: I was charged twice this month",
                        deps={"plans": {"42": "enterprise"}})
print(result.output)       # a validated Triage; a malformed answer triggers a retry with the error shown

# ---- DSPy: declare the task, compile the prompt ----
import dspy
dspy.configure(lm=dspy.LM(os.environ["MODEL"]))

class TriageSig(dspy.Signature):
    """Classify a support ticket and assign a priority."""
    ticket: str = dspy.InputField()
    category: str = dspy.OutputField(desc="one of billing, bug, feature")
    priority: int = dspy.OutputField(desc="1 (urgent) to 4")

triage = dspy.ChainOfThought(TriageSig)
print(triage(ticket="I was charged twice this month"))

# With ~50 labelled tickets and a metric, an optimizer rewrites the prompt and picks demonstrations:
# optimized = dspy.MIPROv2(metric=lambda ex, pred, trace=None: ex.category == pred.category) \
#                 .compile(triage, trainset=examples)
```

## Trade-offs
- A framework buys the loop, persistence and tool wiring in an afternoon but makes you a dependent of
  its release cadence; it flips toward hand-rolling when the agent is small and the team can own two
  hundred lines of loop.
- LangGraph's typed state and checkpointers give resumability and interrupts for free, at the cost of
  verbosity and a learning curve; for a three-call workflow plain functions are clearer.
- LlamaIndex's defaults get a retrieval demo running in minutes, but production quality requires tuning
  chunking, retrievers and prompts the framework hides, at which point the abstraction can be in the
  way.
- Pydantic AI's minimalism means you can read everything it does, but fewer integrations and a young
  API mean more code you write and more renames you absorb.
- DSPy's compiled prompts are more robust and portable across models than hand-written ones, but only
  once you have a metric and labelled data; without them it is just another way to write prompts.
- Vendor-hosted companions (tracing, deployment platforms) are convenient and commercially coupled; the
  frameworks work without them, and the decision to adopt them is separate.

## Pitfalls
- Adopting LangChain's early text-parsing agent patterns for production; the symptom is an agent that
  breaks when the model's phrasing changes and a loop you cannot inspect.
- Using LangGraph without a checkpointer because "we will add persistence later"; the symptom is a
  human-interrupt feature that cannot resume after a redeploy.
- Pinning nothing and upgrading casually; the symptom is import errors and renamed parameters on a
  Monday morning, across every framework named here.
- Treating LlamaIndex's default chunking and prompts as tuned; the symptom is retrieval that works on
  the demo PDF and fails on the customer's scanned contracts.
- Running DSPy optimization on a metric that does not match the business outcome; the symptom is a
  compiled prompt that scores well and ships worse answers.
- Letting a framework's hidden system prompts conflict with yours; the symptom is behaviour you cannot
  explain until you print the final rendered prompt.

## When to use
Choose by the problem, not the popularity. If the hard part is retrieval over documents, start with
LlamaIndex's data layer and keep the agent layer thin. If the hard part is resumable, inspectable
control flow with human interrupts, LangGraph is the strongest option and maps directly onto a
state-machine design. If you want typed tools and outputs with nothing hidden, Pydantic AI, or a
hand-rolled loop of similar size. If prompts are the fragile component and you have labelled examples
and a metric, DSPy. Use LangChain's integrations where they save real work, not its older agent
abstractions. Measure the same things regardless: task completion on a golden set, p95 cost and
latency, resume success, and how long it takes a new engineer to read the final rendered prompt. In
interviews, expect to be asked what a framework does under the hood and when you would not use one.

## Role lens
- **Engineer:** Picks the thinnest layer that solves the actual problem, pins versions, reads the
  rendered prompts and the loop code of whatever is adopted, and keeps tools and state typed.
- **Architect:** Owns the decision of framework versus hand-rolled runtime per system, the persistence
  and tracing story that must work with it, and the exit cost if the framework changes direction.
- **FDE:** Matches the customer's constraints (languages, hosting, data sensitivity, existing
  observability) to a framework they can operate after handover, and avoids vendor-coupled components
  they have not approved.

## Check yourself
**Q:** A team is choosing between LangGraph, Pydantic AI and DSPy for a document-review agent that must
pause for legal approval, resume days later, and produce a strictly structured verdict. How would you
reason about the choice?
**A:** Separate the requirements. Pausing for days and resuming is the decisive one: it needs persisted
state and an interrupt-and-resume mechanism, which LangGraph provides natively through checkpointers
and interrupts; Pydantic AI can do it with its graph library plus your own persistence, and DSPy does
not address it at all, because DSPy is about optimizing the model calls, not orchestrating them. The
structured verdict is served by any of them: typed state and structured outputs in LangGraph, a
validated output type with retries in Pydantic AI, typed output fields in DSPy. So the shape is
LangGraph, or a small hand-rolled state machine, for control flow, and if the review prompt proves
fragile across models and the team has labelled examples, a DSPy-compiled module inside the review
node. The retrieval side, if documents are large, is a separate decision where LlamaIndex's data layer
may fit.

## Quiz
### Q: Which framework's core abstraction is a typed shared state updated by nodes in a graph, persisted by checkpointers?
- [ ] LangChain
- [x] LangGraph
- [ ] LlamaIndex
- [ ] DSPy
> LangGraph models agents as state graphs with checkpointing, which is what makes interrupts and resume
> possible. LangChain is the integration layer; LlamaIndex centres on data; DSPy on prompt optimization.

### Q: What does DSPy "compile" when you call an optimizer?
- [ ] Python code into a faster executable
- [ ] Model weights into a smaller model
- [x] The prompts and few-shot demonstrations of each module, selected and rewritten to maximize a metric on a training set
- [ ] The retrieval index for faster lookup
> DSPy treats prompts as parameters: given signatures, a metric and examples, optimizers search
> instructions and demonstrations. Some optimizers can also fine-tune weights, but prompt artifacts are
> the default output.

### Q: A team wants typed tool arguments, a validated structured output with automatic retry on validation failure, and no hidden prompts. Which is the most direct fit?
- [ ] LangChain's legacy agent executor
- [x] Pydantic AI
- [ ] LlamaIndex query engines
- [ ] DSPy's ReAct module
> Pydantic AI generates tool schemas from type hints, validates the output type and retries by showing
> the model the validation error, with nothing hidden. The others solve different problems or carry
> more abstraction.
