---
id: problem-decomposition
module: scoping
name: Problem decomposition
kind: practice
difficulty: core
est_minutes: 15
related:
  - scoping/workflow-analysis
  - scoping/domain-modeling
  - scoping/requirements-traceability
  - agents/task-decomposition
  - agents/workflows-vs-agents
  - evals/task-specific-acceptance-criteria
  - evals/error-analysis
---

## TL;DR
Problem decomposition breaks a fuzzy business goal into sub-problems that each have a defined input,
output, technique and test, so you can tell which parts need a model, which are plain code, and where
the end-to-end system will fail. It exists because a single "LLM does everything" step cannot be
evaluated, debugged or improved piece by piece, and because errors in chained probabilistic steps
multiply.

## Why it exists
A customer goal arrives as one sentence: "automate invoice exception handling". Handed to a model as one
big prompt, it produces a demo that works on the three invoices you tried and fails opaquely on the
fourth. When it fails you cannot tell whether the PDF was misread, the wrong field was extracted, the
purchase order lookup missed, or the model reasoned badly, so every fix is a prompt tweak that might
break something else.

Two forces make decomposition essential for AI work specifically. First, **errors compound**: if a task
silently requires five steps that each succeed 95% of the time independently, the whole chain succeeds
about 0.95⁵ ≈ 77% of the time. You only see this, and only know which step to improve, if the steps are
explicit. Second, **most of a business process is not a model problem**: lookups, joins, threshold checks
and API calls are deterministic, and putting them inside a prompt makes exact operations approximate.

The naive alternative, letting an agent work out the steps at run time, moves the decomposition from
design time, where you can test it, to run time, where it varies per request. Agent planning has its
place (see task decomposition in the agents module), but you still need a design-time decomposition to
know what the agent is supposed to do, what tools it needs and how to judge whether it did it.

## How it works
### 1. State the goal as an observable output
Write down what exists when the problem is solved: "each invoice exception is routed to the right owner
with a reason code and a drafted resolution, within one business day". An output you can observe can be
tested; an aspiration ("streamline AP") cannot.

### 2. Work backwards from the output
Ask what must be true immediately before the output can be produced, and repeat. Working backwards keeps
every sub-problem tied to the goal, *because* forward brainstorming produces steps nobody needs.

### 3. Split at natural seams
Good cut points are where the **data type changes** (scanned image to text, text to structured fields),
where a **decision** is made, where a **system boundary** is crossed (reading the ERP, sending an
email), and where **responsibility** passes between people. Seams are where you can put a test and an
interface, *because* the artefact passed across them is concrete and inspectable.

### 4. Write a contract for each node
For each sub-problem record: input schema, output schema, technique, owner and acceptance test. The
technique choice is the heart of the exercise:
- **Deterministic code** for lookups, joins, arithmetic, rules and API calls.
- **Classical ML or a classifier** for high-volume, fixed-label decisions with labelled history.
- **Retrieval** where the answer exists in a corpus and must be cited.
- **LLM generation or extraction** for unstructured input and open-ended output.
- **Human** for high-consequence or low-volume judgements.
Push work toward the top of this list wherever it suffices, *because* every probabilistic node needs an
evaluation set and contributes compounding error.

### 5. Check coverage and independence
The sub-problems should cover the goal with no gaps and little overlap (the MECE test from consulting:
mutually exclusive, collectively exhaustive). Gaps show up as "and then somehow" in the walkthrough;
overlaps show up as two components that both decide the same thing.

### 6. Estimate per-node quality and find the critical node
Give each probabilistic node a rough expected accuracy, multiply along the chain, and see which node
dominates the loss. Add mitigations where they are cheapest: a human review on one node, a deterministic
validator after an extraction (does the amount equal the sum of line items?), or a confidence threshold
that routes uncertain cases to a person.

### 7. Prototype the riskiest node first
The node with the most uncertainty, usually the one with the messiest input, gets a spike before
anything else is built, *because* if it cannot reach the required quality the whole design changes.

### 8. Keep the tree as a living artefact
The decomposition becomes the backbone of the statement of work, the requirements trace and the
evaluation plan: one test suite per node plus an end-to-end suite.

## Example
Invoice exception handling, decomposed into five nodes with rough per-node success rates a team might
estimate from a spike (illustrative). The script multiplies along the chain, assuming independent
failures, and shows which single improvement raises end-to-end success most.

```python
from math import prod

nodes = [  # (name, technique, estimated success rate)
    ("parse PDF to text",                 "OCR + layout parser",   0.98),
    ("extract vendor, amount, PO number", "LLM extraction",        0.93),
    ("match invoice to PO in ERP",        "deterministic join",    0.99),
    ("classify exception reason",         "LLM classifier",        0.88),
    ("draft resolution email",            "LLM generation",        0.95),
]

def end_to_end(rates):
    return prod(rates)

base = [r for _, _, r in nodes]
print(f"end-to-end: {end_to_end(base):.1%}")          # about 75%

for i, (name, tech, r) in enumerate(nodes):
    improved = base.copy()
    improved[i] = 0.99                                 # what if this node were near-perfect?
    print(f"{name:36} {tech:20} {r:.0%} -> e2e {end_to_end(improved):.1%}")
```

End-to-end success is about 75%, worse than any single node. Raising the classifier from 88% to 99% lifts
the chain to about 85%; perfecting the ERP join adds almost nothing because it is already deterministic.
That points effort at the classifier, perhaps by adding a fixed reason-code list, few-shot examples, or
routing low-confidence cases to a human.

## Trade-offs
- Fine-grained decomposition makes each node testable and replaceable, but adds interfaces, latency and
  integration code; when one model call handles two steps reliably, merging them is simpler.
- Moving logic into deterministic code gives exactness and auditability, but brittle rules break on
  variation; it flips when the inputs are too varied to enumerate rules for.
- Explicit pipelines are predictable and debuggable, but cannot adapt to cases the designer did not
  foresee; when the space of paths is genuinely open-ended, an agent with tools fits better.
- Per-node evaluation finds the weak link quickly, but node metrics can all look fine while the
  end-to-end result is poor; always keep an end-to-end test alongside the node tests.

## Pitfalls
- Decomposing by org chart or by existing system instead of by data flow; the symptom is nodes whose
  outputs nobody consumes and handoffs that recreate today's delays.
- Putting deterministic steps inside the prompt; the symptom is arithmetic errors, invented IDs or wrong
  dates in otherwise fluent output.
- Treating per-node accuracy as end-to-end accuracy; the symptom is a team reporting 90%+ on every
  component while users see a large share of cases go wrong.
- Leaving node outputs unstructured; the symptom is downstream code parsing free text with regexes and
  failing whenever the model rephrases.
- Building the easy nodes first; the symptom is a mostly finished pipeline blocked for weeks on the one
  hard extraction that should have been spiked on day one.

## When to use
Decompose before estimating, before writing a statement of work and before choosing a model. It is
worth doing even for small builds, because the output (a list of nodes with contracts and tests) is the
evaluation plan. Skip heavy decomposition only for a genuinely single-step task, such as summarising one
document type for internal reading. Measure success by whether every node has an owner, a technique and
a test, and whether you can name the node that dominates end-to-end error. In interviews, decomposing a
vague goal aloud, marking which parts are deterministic, and multiplying error rates is a strong signal
of senior judgement.

## Role lens
- **Engineer:** Implements each node behind a typed interface with its own tests, and adds validators
  between nodes so failures are caught at the seam where they occur.
- **Architect:** Owns the decomposition itself: where the seams go, which nodes are deterministic, where
  humans sit, and how node contracts map to services and evaluation suites.
- **FDE:** Decomposes the customer's goal in a workshop with their subject experts, and uses the tree to
  agree scope, ownership and what "done" means for each piece.

## Check yourself
**Q:** A customer wants a system that "answers any question about our 10,000 contracts". How would you
decompose it, and which node would you prototype first?
**A:** Start from observable outputs: an answer with citations to clauses, or "not found". Split into:
document ingestion and parsing (scans, tables); metadata extraction (counterparty, dates, contract type)
into structured fields; question routing, since "which contracts expire next quarter?" is a structured
query over extracted fields, not retrieval; clause retrieval for open questions; answer generation
constrained to retrieved text; and citation checking. Deterministic parts are the date queries and
filters. I would spike parsing and metadata extraction on a sample of the ugliest real contracts first,
because everything else depends on them and scanned legal documents are where quality is most uncertain.

## Quiz
### Q: A pipeline has four independent probabilistic steps with success rates 0.95, 0.90, 0.97 and 0.92. What is the approximate end-to-end success rate?
- [ ] About 93%, the average of the four
- [ ] About 90%, the weakest step
- [x] About 76%, the product of the four
- [ ] About 97%, the strongest step
> With independent failures the chain succeeds only when every step succeeds, so rates multiply:
> 0.95 × 0.90 × 0.97 × 0.92 ≈ 0.76. Averages and the weakest step both overstate end-to-end quality.

### Q: Which is the best seam at which to split a sub-problem?
- [ ] Between two teams' sprint boundaries
- [x] Where the data changes form, such as scanned image to text or text to structured fields
- [ ] After every model call, regardless of what it produces
- [ ] At the point where the prompt exceeds a fixed token budget
> A change of data form produces a concrete, inspectable artefact that can carry a contract and a test.
> Splits by team or token count do not correspond to anything you can verify.

### Q: In the decomposition, why move a purchase-order lookup out of the LLM and into code?
- [ ] Because LLMs cannot read purchase-order numbers
- [ ] Because code is always cheaper to maintain than prompts
- [x] Because the lookup is exact and deterministic, and doing it in a prompt makes an exact operation approximate
- [ ] Because ERP systems forbid access from LLMs
> A join or lookup has one correct answer that code returns every time. Inside a prompt it gains error
> rate, cost and latency while adding nothing, and becomes a compounding failure point.
