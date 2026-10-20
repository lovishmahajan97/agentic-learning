---
id: rapid-prototyping-poc
module: scoping
name: Rapid prototyping / PoC
kind: practice
difficulty: core
est_minutes: 14
status: reviewed
related:
  - scoping/timeboxing
  - scoping/demos
  - scoping/pilot-to-production-plan
  - scoping/success-metrics-kpis
  - evals/eval-driven-development
  - evals/golden-dataset
  - enterprise-integration/data-profiling-and-quality-assessment
---

## TL;DR
A rapid prototype is a cheap, usually disposable build that answers "what should this be?" by putting
something in front of users. A proof of concept (PoC) answers "can this work?" by testing the single
riskiest technical assumption against pass/fail criteria on real data. Both exist to buy information
cheaply; they fail when they test the easy part, use clean sample data, or never end in a decision.

## Why it exists
An AI project carries two kinds of uncertainty that specifications cannot remove. The first is
*desirability*: will users actually want an assistant that drafts replies, or do they want a better
search box? The second is *feasibility*: can a model achieve the accuracy, latency and cost the use case
needs on this customer's data? Neither can be settled by discussion. A written spec for "an AI that
summarizes claims files" hides whether the summaries are useful and whether the model gets the dates
right on scanned forms.

The naive approach is to commit to the full build and discover the answers along the way. That is
expensive in the worst way: the integration, UI, auth and deployment work, most of the budget, gets done
before anyone learns that the core model step reaches 70% when the business needs 95%. The opposite
mistake is endless exploration in notebooks with no criteria, which produces impressive anecdotes and no
decision.

Prototypes and PoCs sit in between. They are deliberately incomplete builds aimed at one uncertainty at a
time, so you spend days rather than months finding out whether the idea survives contact with users and
data. They are cheap because they skip everything that is not under test, and that same omission is why
they must not be mistaken for the product.

## How it works
### Prototype vs. PoC: different questions
A **prototype** tests the experience: the workflow, the interaction, what output format is useful. It can
be crude, even partly faked (a "Wizard of Oz" prototype where a person produces the "AI" output behind the
scenes), *because* the question is about the human, not the model. A **PoC** tests technical feasibility:
it must use the real model, real data and a real metric, *because* faking any of those invalidates the
answer. Mixing the two questions into one build usually answers neither well. Neither is a pilot (real
users doing real work, measuring business impact) or an MVP (the smallest version you would actually
ship and support); a PoC that passes earns a pilot, not a launch.

### 1. Find the riskiest assumption
List what must be true for the project to succeed (the data is accessible, the model can read these
documents, latency fits the workflow, cost per transaction fits the budget, users trust the output). Rank
by likelihood of being false times cost if false. The PoC targets the top item; everything else can be
stubbed.

### 2. State a hypothesis with a threshold
"Using a general-purpose model with retrieval over the policy manual, at least 85% of answers to 100
real employee questions will be judged correct and grounded." The threshold comes from the business
need, *because* "it works pretty well" cannot be passed or failed.

### 3. Build the eval set from real data first
Collect real inputs, including messy ones, and label expected outputs with domain experts. Fifty to a few
hundred examples is a common scale for a PoC, enough to see error categories, though small sets give
wide confidence intervals. This comes first *because* every later decision is read off this set. Size it
with the binomial interval for a measured accuracy *p* on *n* examples:

```
95% half-width ≈ 1.96 × sqrt(p(1 − p) / n)
n ≈ 1.96² × p(1 − p) / E²     (examples needed for half-width E)
```

At *p* = 0.85, 100 examples give about ±7 points and 400 give about ±3.5; resolving ±3 points needs
roughly 550. If the business threshold and the likely result are close, the set has to be large; if
they are far apart, 100 is plenty.

Split the set before tuning: a *dev* portion you read and iterate on, and a *held-out* portion nobody
looks at until the readout. Every prompt change chosen because it fixed dev failures fits the system to
those examples, so the dev score drifts upward faster than true accuracy; the held-out number is the one
the decision uses.

### 4. Build the thinnest end-to-end slice
Wire input to output with the least code possible. Start with the strongest available model to establish
the ceiling, *because* if the best general-purpose model cannot do it with your prompt and retrieval,
cheaper off-the-shelf models almost certainly will not; cost optimization comes after feasibility. The
ceiling is approach-specific, not absolute: fine-tuning, a specialized component (say, a dedicated OCR or
table parser) or a redesigned pipeline can beat it, so a failed PoC rules out the approach tested, not
the use case.

### 5. Iterate with error analysis, inside a timebox
Read failures on the dev split, group them, fix the largest group, re-run. Largest group first *because*
fixes are roughly one category at a time, so the biggest category is the biggest available gain. Track
latency and cost per example alongside accuracy, since a design that passes on quality but takes 40
seconds per answer has not passed. Stop when the threshold is met, the curve flattens, or the box closes.

### 6. Write the findings and decide
Record the result, the error breakdown, what was stubbed, and what production would require. Decide:
proceed to pilot, pivot the design, or stop. A disposable prototype gets thrown away; an evolutionary one
is kept only if it was built with that intent.

## Example
A minimal PoC harness: real labeled examples, one function under test, a pre-written threshold, and a
decision printed at the end. The model call is a placeholder for whichever provider SDK you use.

```python
import json, math, collections, time

THRESHOLD = 0.85  # written down before any tuning, from the business requirement

def call_model(question: str, context: str) -> str:
    """Placeholder: call your LLM provider here with a fixed prompt version."""
    raise NotImplementedError

def retrieve(question: str) -> str:
    """Placeholder: the thinnest retrieval that could work (e.g. BM25 over the manual)."""
    raise NotImplementedError

def judge(pred: str, expected: str) -> bool:
    """Start with expert labels or a strict string/regex check; add an LLM judge later if needed."""
    return expected.lower() in pred.lower()

def run_poc(path: str = "eval_set.jsonl", split: str = "dev") -> None:
    rows = [json.loads(l) for l in open(path)]           # {"q", "expected", "category", "split"}
    rows = [r for r in rows if r["split"] == split]      # iterate on dev; read held-out once
    errors, correct, secs = collections.Counter(), 0, []
    for r in rows:
        t0 = time.monotonic()
        pred = call_model(r["q"], retrieve(r["q"]))
        secs.append(time.monotonic() - t0)
        if judge(pred, r["expected"]):
            correct += 1
        else:
            errors[r["category"]] += 1
    n = len(rows); acc = correct / n
    half = 1.96 * math.sqrt(acc * (1 - acc) / n)          # binomial 95% half-width
    p95 = sorted(secs)[int(0.95 * (n - 1))]
    print(f"[{split}] accuracy {acc:.1%} ±{half:.1%} on {n} real examples, p95 {p95:.1f}s")
    print("errors by category:", errors.most_common())
    if split == "heldout":
        verdict = ("pass" if acc - half >= THRESHOLD else
                   "fail" if acc + half < THRESHOLD else "inconclusive: need more data")
        print("DECISION:", verdict)
```

The design choices are the point: the threshold is a constant set before the loop; errors are grouped by
category so the next iteration targets the biggest one; latency is measured, not assumed; and the
decision is only printed for the held-out split, with an explicit "inconclusive" band when the interval
straddles the threshold. Run on a 200-example held-out split, 88% gives about ±4.5 points, so the verdict
is "inconclusive" against 85%, which is the honest answer and tells you to label more data, not to
celebrate.

## Trade-offs
- Disposable prototypes are fast because they ignore quality, but teams are tempted to ship them; keep
  the code only when it was built evolutionarily with tests and structure from the start.
- Starting with the strongest model establishes feasibility quickly, but it can hide a cost problem;
  this flips when cost per transaction is itself the riskiest assumption and must be tested first.
- Wizard-of-Oz prototypes answer desirability without any model work, but they say nothing about
  feasibility; use them only when the user experience is the main unknown.
- Small eval sets let a PoC finish in days, but with 100 examples the sampling error on accuracy is
  several points, so a near-threshold result needs more data before a firm decision.

## Pitfalls
- Running the PoC on clean sample data the vendor or customer prepared, not on production-like data; the
  symptom is accuracy that drops sharply in the pilot on scanned, partial or messy inputs.
- Proving the easy part, such as showing a chat UI works, while the hard assumption (data access, a
  rare document type, latency) stays untested; the symptom is surprise blockers after the contract.
- Defining success after seeing the results, so the PoC always "succeeds"; the symptom is a criterion
  that suspiciously matches the number achieved.
- PoC purgatory: a successful PoC with no owner, budget or plan for the next stage; the symptom is a
  well-received demo followed by months of silence.
- Shipping the prototype code into production because "it already works", which brings hard-coded
  credentials, no retries and no tests; the symptom is the first production incident.
- Tuning and reporting on the same examples, so the reported accuracy is fitted to the eval set; the
  symptom is a readout number several points above what a fresh sample or the pilot shows.

## When to use
Build a prototype when the main unknown is what users need or how they will interact; build a PoC when
the main unknown is whether the model can meet a quantified bar on real data. Do both before committing
to an integration-heavy build, and inside a timebox. Skip them when the task is well understood and the
same pattern already runs in production elsewhere with similar data; a pilot may then be the cheaper first
step. Measure a PoC by the decision it enabled and the risks it retired, not by how polished it looks.

## Role lens
- **Engineer:** Builds the eval harness and thinnest slice, tracks error categories per iteration, and
  keeps prototype code clearly separated from anything meant to survive.
- **Architect:** Picks which assumption the PoC must retire and judges whether a passing result actually
  transfers to the production architecture, data volumes and constraints.
- **FDE:** Gets real customer data into the PoC early, negotiates the success threshold with the
  customer up front, and turns the findings into a pilot proposal.

## Check yourself
**Q:** A customer wants a PoC of an LLM that answers questions over their 40,000-page engineering archive.
Their IT team offers a curated folder of 200 clean PDFs to make it fast. What do you do, and why?
**A:** Accept the curated folder only for wiring the slice, not for the decision. The riskiest assumptions
are probably document parsing (scans, drawings, tables) and retrieval over a large, near-duplicate corpus,
which a clean 200-PDF set would hide. Ask for a random sample across the archive, including old scans, and
build the eval questions with engineers who actually ask them. State the threshold up front. If full
access is impossible in the timebox, report results as conditional on the sample and list what remains
untested. Otherwise the PoC passes and the pilot fails on the very data the curated set excluded.

## Quiz
### Q: What best distinguishes a PoC from a rapid prototype?
- [ ] A PoC is always longer and more expensive than a prototype
- [x] A PoC tests technical feasibility against criteria on real data; a prototype tests what the experience should be
- [ ] A prototype must use the production model while a PoC can use a mock
- [ ] A PoC is shown to executives while a prototype is internal only
> The difference is the question asked. A prototype can fake the AI because it studies users; a PoC cannot
> fake the model or data because feasibility is exactly what it measures.

### Q: Why start a PoC with the strongest available model rather than the cheapest?
- [ ] Cheap models are not allowed on customer data
- [ ] The strongest model is always cheapest per correct answer
- [x] It establishes a ceiling for the approach: if the best model fails, cheaper off-the-shelf ones almost certainly will too
- [ ] Strong models need no evaluation set
> Feasibility first: the strongest model bounds what this prompt-and-retrieval approach can achieve, and cost
> can be optimized later. The exception is when cost itself is the riskiest assumption, in which case it
> must be tested directly. Fine-tuning or specialized components can still exceed that ceiling.

### Q: A PoC reaches 86% against an 85% threshold on 100 examples. What is the soundest conclusion?
- [ ] The PoC has clearly passed and the team should commit to full production
- [ ] The PoC failed because results should exceed the threshold by at least ten points
- [x] It is a marginal pass; sampling error of several points means more data is needed before a firm commitment
- [ ] The threshold was wrong and should be raised to 90%
> With 100 examples, accuracy near 85% has a 95% interval of roughly ±7 points, so 86% is consistent with
> true performance below threshold. Collect more examples or proceed to a pilot with that risk stated.
