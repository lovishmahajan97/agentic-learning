---
id: tool-selection-accuracy
module: agents
name: Tool selection accuracy
kind: metric
difficulty: core
est_minutes: 12
related:
  - agents/tool-calling
  - agents/tool-schemas-and-argument-validation
  - agents/model-context-protocol
  - agent-reliability/tool-call-success-rate
  - agent-reliability/agent-trajectory-evaluation
  - evals/error-analysis
  - evals/golden-dataset
  - prompting/context-engineering
sources:
  - Patil et al., 2023 — Gorilla: Large Language Model Connected with Massive APIs
  - Qin et al., 2023 — ToolLLM: Facilitating Large Language Models to Master 16000+ Real-world APIs
---

## TL;DR
Tool selection accuracy is the fraction of decision points at which the model chooses the right tool,
including choosing *no* tool, with arguments that are valid and correct. It falls as the tool count
grows and as descriptions overlap, and it is the first agent metric worth instrumenting, because a wrong
selection wastes a step at best and causes a wrong side effect at worst, and because it isolates the one
decision the model makes at every step from everything else that can go wrong in a run.

## Why it exists
When a multi-tool agent "feels dumb", the complaint almost always decomposes into three concrete
failures: it should have called a tool and answered from memory instead; it called the wrong one of two
similar tools; or it called the right tool with the wrong arguments. End-to-end task success cannot
tell these apart from a flaky API, a bad result format or a weak final answer, so teams tuning on task
success alone change descriptions, prompts and the tool list at once and cannot tell which change
helped and which regressed something else.

The naive alternative is to judge by reading a few transcripts. That is slow, biased toward the cases
you happened to look at, and useless for catching a regression after a model upgrade or a description
edit. Selection accuracy gives a number that moves when the tool surface changes and stays still when
it does not, which is what you need to iterate safely. It is also cheap to measure: the decision is
made in the model's first response, so no tool has to actually execute to score it.

## How it works
### Decompose the decision
At each step the model makes three choices, and each gets its own metric:
1. **Call or not.** Over the cases where a tool is needed, *should-call recall* is the fraction where
   the model called one; over the cases where it did call, *should-call precision* is the fraction
   where a call was actually needed. Low recall shows up as hallucinated facts; low precision shows up
   as cost and latency on questions a sentence would have answered.
2. **Which tool.** Given that a call was needed and made, *name accuracy* is the exact match against
   the expected tool. A confusion matrix over (expected, chosen) pairs is more useful than the single
   number, because it points at the specific pair of tools whose descriptions overlap.
3. **Which arguments.** *Schema-valid rate* is automatic and cheap. *Argument accuracy* compares key
   fields with the expectation: exact match for ids and enums, normalized match for dates and codes, and
   a rule or an LLM judge for free-text fields such as search queries, where "shipping delay refund"
   and "refund for late delivery" are both right.

Step-level accuracy is the conjunction: right decision, right tool, right arguments.

### Build the dataset
Sample real traffic, not imagined prompts, and label each turn with the expected tool (or `none`) and
the expected key arguments. Include `none` cases in roughly their real proportion so precision is
measured, and deliberately include near-miss pairs that separate similar tools. A set in the low
hundreds of cases is enough to see large regressions; label it as a golden set, keep it out of any
few-shot examples embedded in tool descriptions, and version it with the tool definitions. For
mid-trajectory decisions, snapshot the conversation up to the decision point as the case input.

### Run it cheaply
Each case is one model request with the production system prompt and tool list and `tool_choice` left
on `auto`; score the first response's `tool_use` blocks. Because the prefix (tools plus system prompt)
is identical across cases, prompt caching makes a few hundred cases inexpensive, and the run fits in CI
on every change to a description, the tool list or the model version.

### What moves the number
- **Trigger conditions in descriptions.** "Call this when the user asks about an order's status; do
  not use it for returns" raises recall and reduces confusion more than any other single edit. Some
  recent models reach for tools more conservatively, which makes explicit "call when" language matter
  more, not less.
- **Fewer, more distinct tools.** Merge overlapping tools behind an enum parameter, split a do-anything
  tool whose `mode` parameter is really five tools, and give names that read as verbs on nouns.
- **Fewer tools in context.** Beyond a few dozen tools (the threshold is model-dependent and
  illustrative) accuracy degrades and token cost rises; defer rarely used tools and load them through
  tool search or dynamic registration when the task calls for them.
- **Better parameter descriptions and enums**, which move argument accuracy specifically.
- **System prompt guidance** about when to act versus answer, which moves should-call precision and recall.

### Model dependence
The same tool definitions score differently on different models and versions, because selection is
learned behaviour. Re-run the eval on every model change and treat a should-call shift as expected,
not as a bug in your code.

## Example
A minimal eval harness: a labelled case set, one request per case, and three scores plus a confusion
map. It uses the production `TOOLS` and `SYSTEM` and never executes a tool, so it is safe to run in CI.

```python
import anthropic
from collections import Counter

client = anthropic.Anthropic()
CASES = [  # (user message, expected tool or None, expected key arguments)
    ("Where is order ORD-104233?",                  "get_order",     {"order_id": "ORD-104233"}),
    ("Show me all open orders for customer C-9",    "search_orders", {"customer_id": "C-9", "status": "open"}),
    ("What is your returns policy?",                None,            {}),
    ("Refund ORD-104233, it arrived broken",        "issue_refund",  {"order_id": "ORD-104233"}),
    ("Has C-9 ordered anything that shipped?",      "search_orders", {"customer_id": "C-9", "status": "shipped"}),
]

def first_call(user_msg: str):
    r = client.messages.create(model="claude-opus-5-5", max_tokens=1024, system=SYSTEM,
                               tools=TOOLS, messages=[{"role": "user", "content": user_msg}])
    return next(((b.name, b.input) for b in r.content if b.type == "tool_use"), (None, {}))

tally, confusion = Counter(), Counter()
for msg, want, want_args in CASES:
    got, args = first_call(msg)
    tally["decision_ok"] += (got is None) == (want is None)
    if want is not None and got is not None:
        tally["name_ok"] += got == want
        tally["args_ok"] += got == want and all(args.get(k) == v for k, v in want_args.items())
        if got != want:
            confusion[(want, got)] += 1

n, n_tool = len(CASES), sum(1 for _, w, _ in CASES if w)
print(f"call/no-call {tally['decision_ok']/n:.0%}  name {tally['name_ok']/n_tool:.0%}  "
      f"args {tally['args_ok']/n_tool:.0%}")
print("confusions (expected -> chosen):", dict(confusion))
```

## Trade-offs
- A first-turn eval is cheap and isolates the decision, but it misses selections made deep in a long
  context; when first-turn accuracy is high and task completion is still low, add trajectory evals.
- Making descriptions more insistent raises should-call recall but lowers precision; the right balance
  depends on whether a wasted call or a hallucinated answer costs more, and for write tools it is
  better to under-call and ask.
- Fewer tools improve accuracy but reduce capability; deferred loading and tool search keep the
  capability at the price of a discovery step and some latency.
- LLM-judged argument equivalence handles free-text parameters but adds noise and cost; use exact or
  normalized matching for ids, enums and dates and reserve the judge for queries.

## Pitfalls
- Measuring only task success; the symptom is a selection regression that stays invisible until many
  tasks fail and the cause has to be reconstructed from transcripts.
- Writing eval cases from the same examples that appear in the descriptions; the symptom is a perfect
  score on the eval and the old failures in production.
- Omitting `none` cases; the symptom is an agent that calls tools for greetings and small talk, and a
  bill that grows faster than usage.
- Changing several descriptions in one commit; the symptom is a moved number with no way to know which
  edit moved it or whether another edit cancelled a gain.
- Skipping the eval on a model upgrade; the symptom is a drop in should-call rate with no code change
  and a week of looking for the bug in the wrong place.

## When to use
Instrument selection accuracy as soon as an agent has more than two or three tools, and run it on every
change to descriptions, the tool list, the system prompt or the model. Use the decomposed scores to
decide what to edit: low recall means trigger conditions and system prompt; confusion between a pair
means those two descriptions; low argument accuracy means parameter descriptions and enums. Do not
chase the last few points on a small eval; label more cases instead. In production, track the ratio of
tool calls to turns and the schema-validation failure rate as cheap proxies that do not need labels. In
interviews, "how do you know your tool descriptions are good" is a common question, and the expected
answer is a labelled set, the three-way decomposition and a confusion matrix, not "we read transcripts".

## Role lens
- **Engineer:** Maintains the labelled case set, runs the selection eval in CI, and edits one
  description at a time with the confusion matrix open.
- **Architect:** Sets the maximum number of tools a single agent may hold in context and the policy
  for deferring the rest, based on measured accuracy rather than convenience.
- **FDE:** Builds the case set from the customer's real queries during onboarding and uses the score
  to justify consolidating the customer's forty endpoints into eight tools.

## Check yourself
**Q:** Step-level selection accuracy is 92%, yet users report that the agent "makes up delivery
dates". Which component of the metric would you look at first, and what would you change?
**A:** Making up a date is an answer given without a lookup, so the suspect is should-call recall for
the order-status tool, not name confusion. Check the confusion map for cases labelled `get_order` that
the model answered with no call; if they cluster around phrasings like "when will it arrive", the
description's trigger condition does not cover them. Rewrite the description to name those phrasings
and to state that delivery dates must never be given without a lookup, add the same rule to the system
prompt, and re-run the eval watching recall and precision together so the fix does not cause calls on
every message. In production, confirm by tracking the share of delivery-date answers preceded by a
`get_order` call.

## Quiz
### Q: Which metric captures "the agent answered from memory when it should have looked it up"?
- [ ] Tool-name accuracy
- [x] Should-call recall
- [ ] Schema-valid argument rate
- [ ] Should-call precision
> Recall measures, over the cases that needed a tool, how often one was called. Precision is the
> reverse error (calling when unnecessary), and name and argument metrics only apply once a call was made.

### Q: Two tools are repeatedly confused for each other. What is the highest-leverage fix?
- [ ] Move the more common tool to the front of the tools array
- [ ] Lower the sampling temperature
- [x] Rewrite both descriptions with explicit, disjoint trigger conditions, or merge them behind an enum parameter
- [ ] Add more required parameters to the less common tool
> Confusion comes from overlapping descriptions; making them disjoint or removing the choice entirely
> addresses the cause. Ordering and temperature have at most marginal effects, and extra parameters do
> not change which tool is chosen.

### Q: Why should the eval set include cases where the correct behaviour is to call no tool?
- [ ] To make the eval run faster
- [x] To measure should-call precision, so the agent is not rewarded for calling tools on every message
- [ ] Because the API requires at least one text-only response per batch
- [ ] To test the fallback model
> Without `none` cases an agent that calls a tool on every turn scores perfectly on recall while wasting
> cost and latency; the `none` cases are what expose over-calling.
