---
id: property-based-testing
module: python-for-ai
name: Property-based testing (Hypothesis)
kind: practice
difficulty: core
est_minutes: 15
related:
  - python-for-ai/pytest
  - python-for-ai/pydantic
  - python-for-ai/json-jsonl
  - python-for-ai/contract-testing
  - evals/property-based-testing
  - evals/metamorphic-testing
sources:
  - Claessen & Hughes, 2000 — QuickCheck: A Lightweight Tool for Random Testing of Haskell Programs
  - MacIver et al., 2019 — Hypothesis: A new approach to property-based testing
  - Official docs: Hypothesis
---

## TL;DR
Instead of writing example inputs by hand, you state a property that must hold for every input
("chunks never exceed the size limit and rejoin to the original text"), and Hypothesis generates
hundreds of inputs, including the nasty edge cases you would not think of, then shrinks any failure to
a minimal counterexample. It exists because the bugs in parsing, chunking and serialisation code hide in
inputs nobody writes examples for: empty strings, Unicode, boundaries.

## Why it exists
Example-based tests check the cases their author imagined. The author of a text chunker writes tests
for "a paragraph", "exactly one chunk long" and "three chunks", and the chunker passes. Then
production hits an empty document, a string of combining characters, a chunk size of 1, or an overlap
equal to the chunk size minus one, and it crashes or silently drops text. The bug was always there; the
example set simply never visited that part of the input space.

AI glue code is dense with exactly this kind of function: tokenisation-adjacent splitting, prompt
templating that must escape user text, parsers for model output, JSON round-trips for tool arguments,
deduplication, score normalisation, pagination over retrieval results. Their input spaces are huge and
their correctness conditions are easy to state but hard to enumerate.

The naive alternative, "generate random inputs in a loop", finds some bugs but has two problems.
Uniform random data rarely hits the boundaries that matter (an empty list, zero, the maximum value, a
lone surrogate), and when it does find a failure the input is a 1,400-character random string from
which you must reverse-engineer the bug by hand. Property-based testing fixes both: generators are
biased toward edge cases, and failures are automatically shrunk to the smallest input that still fails.

## How it works
### Properties, not examples
A property is a universally quantified claim about your code: for all valid inputs *x*, some predicate
holds. Common shapes, roughly in order of how often they pay off:
- **Round-trip:** `decode(encode(x)) == x` (JSON, serialisers, tokenizer encode/decode where lossless).
- **Invariant:** the output always satisfies a condition (every chunk length ≤ size; scores in [0, 1];
  output sorted; no duplicate IDs).
- **Idempotence:** `f(f(x)) == f(x)` (normalisation, deduplication, cleaning).
- **Oracle / differential:** a fast implementation agrees with a slow obviously-correct one, or the new
  version agrees with the old one.
- **Metamorphic:** a known change to the input produces a known change to the output (reordering
  documents does not change the set retrieved).

### Strategies
A strategy describes how to generate values: `st.integers(0, 100)`, `st.text()`, `st.lists(...)`,
`st.dictionaries(...)`, combined with `.map`, `.filter`, `st.one_of`, `st.recursive` and `st.builds` (call
a constructor with generated arguments). `st.from_type` derives a strategy from type hints, which works
with dataclasses and many annotated classes. `st.data()` lets a test draw values interactively, so one
parameter can depend on another (an overlap smaller than the chunk size). Strategies deliberately
over-sample edge cases: empty collections, boundary integers, unusual Unicode.

### Running, shrinking and remembering
`@given(...)` turns a test into a loop. By default Hypothesis runs about 100 examples per test (tunable
with `@settings(max_examples=...)`) and flags examples that run too long via a deadline setting. When an
example fails, Hypothesis shrinks it: it repeatedly tries simpler versions of the input and keeps any
that still fail. Unlike classic QuickCheck, which shrinks values by type-specific rules, Hypothesis
shrinks the underlying sequence of random choices that produced the value. That is a design choice with
a real payoff: shrinking works automatically for any composed strategy, including ones with `.map` and
`.filter`, without you writing a shrinker.

Failing examples are saved in a local database (the `.hypothesis/` directory) and replayed first on the
next run, so a bug, once found, is checked every time until fixed. `@example(...)` pins a specific input
permanently, which is how you turn a found counterexample into a documented regression case.
`assume(cond)` discards inputs that do not meet a precondition; use it sparingly, because heavy
filtering makes generation slow and Hypothesis will report a health-check failure.

### Stateful testing
For objects with operations (a cache, a conversation memory, a rate limiter), `RuleBasedStateMachine`
generates sequences of operations and checks invariants after each step against a simple model, which
finds bugs that only appear after a specific order of calls.

## Example
A chunker for retrieval with overlap. Two properties fully specify it: no chunk exceeds `size`, and
dropping each later chunk's overlap rejoins the original text exactly. An earlier version used
`range(0, len(text), step)`; Hypothesis failed it immediately and shrank the counterexample to
`text=''`, where that version returned no chunks at all.

```python
import json
from hypothesis import given, strategies as st

def chunk(text: str, size: int, overlap: int) -> list[str]:
    if not 0 <= overlap < size:
        raise ValueError("need 0 <= overlap < size")
    step = size - overlap
    return [text[i:i + size] for i in range(0, max(len(text) - overlap, 1), step)]

@given(text=st.text(max_size=2000), size=st.integers(1, 200), data=st.data())
def test_chunk_properties(text, size, data):
    overlap = data.draw(st.integers(0, size - 1), label="overlap")
    chunks = chunk(text, size, overlap)
    assert chunks, "always at least one chunk"
    assert all(len(c) <= size for c in chunks)
    assert chunks[0] + "".join(c[overlap:] for c in chunks[1:]) == text

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(),        # no floats: NaN != NaN
    lambda inner: st.lists(inner) | st.dictionaries(st.text(), inner),
)

@given(st.dictionaries(st.text(min_size=1), json_values))
def test_tool_args_round_trip(args):
    assert json.loads(json.dumps(args)) == args

def test_empty_text_regression():          # the shrunk counterexample, pinned as a plain test
    assert chunk("", size=4, overlap=1) == [""]
```

The chunker property encodes the whole specification in three assertions; no hand-written example
set would have covered size 1, overlap `size - 1`, empty text and non-ASCII text all at once. The
round-trip test excludes floats on purpose, because `NaN` breaks equality, which is itself the kind of
fact property tests force you to decide explicitly.

## Trade-offs
- Properties cover far more of the input space than examples, but are harder to write; for code whose
  correctness you cannot state as a rule, a handful of good examples remains the better investment.
- More examples find rarer bugs, but slow the suite; run the default count locally and a larger count
  in a nightly or CI profile, and flip that balance once the suite becomes a bottleneck.
- Generated inputs explore edge cases you did not imagine, but random exploration means a new failure
  can surface on an unrelated commit; the example database and pinned `@example`s keep that manageable.
- Restating the implementation as the property is easy but proves nothing; an independent oracle or
  invariant costs more thought and is the only version that actually catches bugs.

## Pitfalls
- Writing a property that re-implements the function under test; the symptom is a test that has never
  failed, even when you deliberately break the code.
- Overusing `filter` or `assume` to carve out valid inputs; the symptom is slow tests and Hypothesis
  health-check errors about too much data being filtered out.
- Generating floats for equality-based properties without thinking about NaN, infinities and rounding;
  the symptom is spurious failures on `nan != nan` or tiny representation differences.
- Calling a real LLM or network service inside `@given`; the symptom is a test that makes a hundred
  paid, slow, non-deterministic calls per run and fails on flaky responses, not on bugs.
- Deleting the `.hypothesis/` database in CI without pinning found failures; the symptom is a bug that
  reproduced once and then mysteriously stops reproducing until it hits production.

## When to use
Reach for property-based testing on deterministic code with a large input space and an easy-to-state
rule: parsers for model output, chunkers and splitters, serialisers, prompt template escaping, ID and
deduplication logic, ranking and score normalisation, and anything stateful like caches and memories.
It is not the tool for judging model quality; the model's outputs are not deterministic and their
"correctness" is a graded judgement, which is what evals and metamorphic evaluation address. Use it
alongside a few readable examples, which serve as documentation. In interviews, being able to name the
property for a given function (round-trip, invariant, idempotence, oracle) is the skill being tested.

## Role lens
- **Engineer:** Writes Hypothesis properties for parsers, chunkers and tool-argument serialisation, and
  pins every shrunk counterexample as a permanent regression test.
- **Architect:** Requires property tests on boundary code that turns untrusted text into structured data,
  because that is where malformed inputs enter the system.
- **FDE:** Turns a customer's odd documents (encodings, empty pages, enormous tables) into strategies
  that generate similar inputs, proving the pipeline handles the whole class.

## Check yourself
**Q:** You must test a function that extracts a JSON object from a model reply that may include markdown
fences and surrounding prose. What properties would you write, and what would you not use Hypothesis for?
**A:** Generate an arbitrary JSON-compatible dict, serialise it, then wrap it with generated prose
before and after, optionally inside a fenced block; the property is that extraction returns the
original dict. A second property: text with no JSON object raises a specific parse error rather than
returning garbage. A third: extraction is unchanged by leading and trailing whitespace. Generated
prose should be filtered so it cannot itself contain braces, or the property must define which object
wins. I would not use Hypothesis to decide whether the model produces good JSON in the first place;
that is an eval over real prompts, and replies would be recorded examples, not generated ones.

## Quiz
### Q: What does Hypothesis shrinking do after a property fails?
- [ ] Reruns the failing input until it passes, to detect flakiness
- [x] Searches for a simpler input that still fails and reports the minimal one
- [ ] Reduces max_examples so the test runs faster next time
- [ ] Removes the failing input from future runs to avoid noise
> Shrinking turns a large random failure into a small, readable counterexample such as an empty
> string. Failing inputs are saved and replayed first next time, not removed.

### Q: Which is the strongest property for a JSON serialiser of tool arguments?
- [ ] The output is a non-empty string
- [ ] The output is shorter than ten kilobytes
- [x] Deserialising the output returns a value equal to the original input
- [ ] Serialising twice produces the same string
> The round-trip property checks that no information is lost or corrupted. Non-empty output and
> determinism can hold while values are silently mangled, so they are much weaker checks.

### Q: Why can Hypothesis shrink values built with `.map` and `.filter` without a custom shrinker?
- [ ] It requires every strategy to define its own shrink function
- [ ] It only shrinks integers and strings, not composed values
- [x] It shrinks the underlying sequence of random choices, then regenerates the value from it
- [ ] It stores every generated value and picks the smallest one
> Shrinking the choice sequence works for any strategy composition automatically. Type-based
> shrinking, as in classic QuickCheck, needs a shrinker per type and struggles with mapped values.
