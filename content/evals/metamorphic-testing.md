---
id: metamorphic-testing
module: evals
name: Metamorphic testing
kind: concept
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - evals/property-based-testing
  - evals/regression-tests
  - evals/adversarial-evaluation
  - evals/judge-calibration-and-bias
  - evals/pairwise-comparison
  - evals/golden-dataset
  - rag-retrieval/lost-in-the-middle
sources:
  - Chen, Cheung and Yiu, 1998 — Metamorphic Testing: A New Approach for Generating Next Test Cases
  - Ribeiro et al., 2020 — Beyond Accuracy: Behavioral Testing of NLP Models with CheckList
---

## TL;DR
Metamorphic testing checks a system without knowing the right answer. Instead of asserting that an output
equals an expected value, you transform the input in a way whose effect is predictable (paraphrase: same
answer; add an irrelevant paragraph: same answer; swap two options: the choice swaps) and assert that
relation between the two outputs. It exists because most LLM inputs have no label, and relation
violations are cheap to generate and expose brittleness a golden set, which sees each item once in one
form, cannot.

## Why it exists
Testing needs an oracle, something that says what the correct output is. For an LLM system the oracle
is a human label, and labels are expensive, so a golden set has a few hundred items while the input
space is every sentence a user might type. Worse, each golden item is tested in one surface form. A
system can pass all of them and still fail the user who asks the same question with a typo, in a
different order, or with a longer preamble, because models are sensitive to surface form in ways that
have nothing to do with the task. "It worked in the demo" is usually an invariance failure.

Metamorphic testing, proposed in software testing before machine learning needed it, sidesteps the
oracle. You may not know the correct answer to "what is our refund window for annual plans", but you
know that the answer must not change when the question is rephrased, when an unrelated newsletter is
added to the retrieved context, or when the documents are reordered. Each such *metamorphic relation*
turns one input into a family of checks with no labelling cost, and it tests a property, stability,
that accuracy on a fixed set cannot measure at all.

The naive alternatives are weaker. "Label more data" scales linearly in cost and still tests one form
per item. "Have an LLM judge every output" needs a rubric or a reference, so it has the same oracle
problem, and the judge is itself sensitive to surface form. Metamorphic relations need neither labels
nor a trustworthy judge of correctness: they need only a reliable way to say whether two outputs agree.

## How it works
### A relation, not an expected value
A metamorphic test has three parts: a source input *x*, a transformation *T* that produces a follow-up
input *T(x)*, and a relation *R* that must hold between the outputs *f(x)* and *f(T(x))*. The test
passes if *R* holds. Nothing says whether *f(x)* was correct, which is the point: provided the relation
genuinely holds for that input (the paraphrase really kept the meaning), a violation proves at least one
of the two outputs is wrong without saying which, and that is still a bug report.

CheckList (Ribeiro et al.) organised the same idea for NLP into invariance tests (INV: the output should
not change), directional expectation tests (DIR: the output should move in a known direction), and
minimum functionality tests (MFT), which are ordinary labelled unit tests and the complement, not a
metamorphic relation.

### A catalogue of relations for LLM systems
- **Invariance.** Paraphrase; casing, whitespace and punctuation; bounded typos; the language of the
  instruction (not of the content); reordering independent list items or retrieved documents; adding an
  irrelevant document or an irrelevant earlier turn; changing a date format. Each is chosen *because*
  the task is defined independently of that surface feature, so sensitivity to it is a defect.
- **Mapping.** Rename an entity consistently in the input and the same rename must appear in the
  output; translate the question and the answer must translate back to the same content. The relation
  is an equivalence up to a known substitution.
- **Directional.** Add a constraint and the set of valid answers must shrink or the constraint must be
  acknowledged; add stronger supporting evidence and the stated confidence must not fall; raise a
  length budget and the summary must keep every fact it kept before; insert a strongly negative sentence
  and a sentiment score must not rise.
- **Symmetry and consistency.** In a pairwise judge, swapping A and B must swap the verdict (this is the
  standard position-bias check); asking a question and its negation must give consistent answers;
  asking the model to verify its own answer must not contradict that answer.
- **Composition.** Extracting facts from a summary of a document must yield a subset of the facts
  extracted from the document itself.

### Deciding whether two text outputs agree
Outputs are text, so "equal" has to be defined per task, in increasing order of leniency: exact equality
for structured fields (a label, a number, a JSON field, a tool name), normalised comparison for short
answers, embedding similarity above a threshold, or an LLM judge asked the narrow question "do these two
answers say the same thing" (a far easier and more reliable judgment than "is this answer correct").
Choose the strictest definition the task allows, because a lenient comparator hides real violations.

### The noise floor
Models are nondeterministic, so first run the identity transformation: the same input twice. Its
violation rate is the noise floor. A relation reveals a real sensitivity only when its violation rate
sits clearly above that floor; a 4% paraphrase violation rate against a 3% identity rate is noise, a
25% document-order violation rate against 3% is a finding. Reporting per relation and per slice shows
*which* sensitivity you have, which is more actionable than one robustness number.

### From violations to action
A violated pair goes to a human or a correctness judge to decide which side is wrong, and the losing
side becomes a labelled regression case. Over time the relation suite also doubles as the generator for
adversarial evaluation: the transforms that violate most often are the ones worth hardening against.

## Example
Five relations against a RAG question-answering function. `same_meaning` is a narrow agreement
comparator (normalised equality for short answers, a "do these say the same thing" judge otherwise).
The identity relation measures the noise floor; the others are compared against it.

```python
import re
from statistics import mean

def rag_answer(question: str, docs: list[str]) -> str: ...     # system under test
def same_meaning(a: str, b: str) -> bool: ...                    # agreement comparator, not a correctness judge

NOISE_DOC = "Office newsletter: plants were watered on Tuesday. Parking lot B is closed this week."

def paraphrase(q: str) -> str:   # cheap and deterministic; a model paraphraser works too
    for a, b in [("What is", "Tell me"), ("How do I", "What's the way to"), ("?", ", please?")]:
        q = q.replace(a, b, 1)
    return q

RELATIONS = {
    "identity":   lambda q, d: (q, d),                               # noise floor
    "paraphrase": lambda q, d: (paraphrase(q), d),
    "noise_doc":  lambda q, d: (q, d + [NOISE_DOC]),
    "doc_order":  lambda q, d: (q, list(reversed(d))),
    "whitespace": lambda q, d: (re.sub(r"\s+", "  ", q) + "\n", d),
}

def violation_rates(cases: list[tuple[str, list[str]]]) -> dict[str, float]:
    rates = {}
    for name, transform in RELATIONS.items():
        violations = []
        for q, docs in cases:
            base = rag_answer(q, docs)
            q2, docs2 = transform(q, docs)
            violations.append(not same_meaning(base, rag_answer(q2, docs2)))
        rates[name] = mean(violations)
    return rates

# Read the table against "identity": a relation whose rate sits well above it is a real sensitivity.
# A high doc_order rate in RAG usually points at position effects in the context window.
```

## Trade-offs
- Relations need no labels, so coverage scales with compute instead of annotation budget, but a
  violation tells you something is wrong without telling you which output or how; triage is still human.
- Invariance relations are easy to write and interpret, but some sensitivity is legitimate (a casing
  change inside a code snippet changes meaning), so each relation must be scoped to where it truly holds.
- A strict comparator catches more violations but flags harmless rewording; a lenient one misses real
  drift; the right choice flips with how structured the output is.
- Automated transforms are cheap but can produce invalid follow-ups (a paraphrase that changes the
  question); using a model to transform adds cost and its own errors, so validate transforms on a sample.
- Measuring the noise floor adds one more relation's worth of model calls to every run, yet without it
  any violation rate is uninterpretable, so it can be skipped only when outputs are fully deterministic.

## Pitfalls
- Skipping the identity baseline; the symptom is a "12% paraphrase sensitivity" that turns out to be
  the model's own run-to-run variance.
- Using a correctness judge as the comparator; the symptom is violations that depend on the judge's
  opinion of the answer rather than on whether the two answers agree.
- Treating every violation as a bug in the transformed output; the symptom is "fixes" that make the
  original, actually-wrong answer the standard.
- Applying an invariance relation where the feature is meaningful; the symptom is a flood of violations
  on code, IDs or case-sensitive names that nobody acts on.
- Writing relations only for the question and never for the context; the symptom is a RAG system that
  passes paraphrase tests and still answers differently when one extra document is retrieved.

## When to use
Reach for metamorphic relations whenever you have many unlabelled inputs and a system whose stability
matters: RAG answers, classifiers, extractors, pairwise judges, agents reading tool output. They are the
cheapest way to measure robustness to surface form and to find position and context-length effects. Do
not use them as the only eval: they say nothing about correctness, so pair them with a golden set.
Measure violation rate per relation against the identity floor, track it per version like any other
metric, and feed violated pairs into the regression suite. In day-to-day work the relation suite is what
you run when a prompt change "feels" less stable. In interviews, the expected depth is to explain the
oracle problem, give three relations for a concrete system, and explain the noise-floor measurement.

## Role lens
- **Engineer:** Writes the transform functions and the agreement comparator, runs the relation table on
  every prompt change, and promotes violated pairs into labelled regression cases.
- **Architect:** Decides which relations are requirements (a pairwise judge must be order-invariant, a
  RAG answer must survive an irrelevant document) and sets the violation thresholds a release must meet.
- **FDE:** Uses the customer's real phrasing variants, templates and document mixes as the transforms, so
  the robustness measured is the robustness their users will experience.

## Check yourself
**Q:** You have 2,000 unlabelled customer questions and a RAG assistant. Without labelling anything,
how would you measure whether it is robust, and how would you know the numbers mean something?
**A:** Define metamorphic relations whose outputs must agree: paraphrased question, question with
bounded typos, retrieved documents reversed, one irrelevant document appended, an irrelevant earlier
turn prepended. Run each question through the original and the transformed form and compare answers
with a narrow agreement comparator, exact for extracted fields and a "same meaning" judge for prose.
First run the identity transform to get the noise floor from nondeterminism, then report each
relation's violation rate against it, per slice. A relation well above the floor is a real sensitivity;
document-order violations point at position effects, noise-document violations at weak grounding.
Violated pairs are then triaged by a human to decide which side is wrong and become labelled regression
cases. Correctness still needs a golden set; this measures stability, which the golden set cannot.

## Quiz
### Q: What does a metamorphic test require that a golden-set test does not, and what does it do without?
- [ ] It requires a larger labelled set and does without a judge
- [x] It requires a predictable relation between outputs of related inputs and does without knowing the correct output
- [ ] It requires deterministic outputs and does without a comparator
- [ ] It requires a reference answer and does without a transform
> The test asserts a relation such as "paraphrase gives the same answer", so it never needs the expected
> answer itself. A comparator is still needed to decide whether two outputs agree, and nondeterminism is
> handled by measuring a noise floor.

### Q: Why run the identity transformation (same input twice) before interpreting any other relation's violation rate?
- [ ] To warm the model's cache so later relations run faster
- [ ] Because the identity relation is the only one that can be graded exactly
- [x] To measure run-to-run nondeterminism, so a relation counts as a finding only when its rate exceeds that floor
- [ ] Because paraphrase relations are undefined without a baseline answer
> A model at non-zero temperature, or even at zero, disagrees with itself some of the time. A paraphrase
> violation rate equal to the identity rate is noise; only the excess over the floor is sensitivity to
> the transform.

### Q: A paraphrase relation is violated on an item. What can you conclude?
- [ ] The paraphrased output is wrong and should be added as a failing case
- [ ] The original output is wrong, since the paraphrase is a cleaner input
- [x] At least one of the two outputs is wrong, but which one needs a human or a correctness judge
- [ ] The comparator is too strict and should be relaxed
> The relation only states that the two outputs must agree. Assuming the paraphrase kept the meaning,
> disagreement proves inconsistency, not which side is correct; assuming either direction is a common mistake that can enshrine the wrong
> answer as the expectation.
