---
id: property-based-testing
module: evals
name: Property-based testing
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - python-for-ai/property-based-testing
  - evals/metamorphic-testing
  - evals/regression-tests
  - rag-retrieval/chunking-strategies
  - prompting/structured-outputs-json-mode
  - safety-security/output-validation
  - python-for-ai/pytest
sources:
  - Claessen and Hughes, 2000 — QuickCheck: A Lightweight Tool for Random Testing of Haskell Programs
  - Official docs: Hypothesis
---

## TL;DR
Property-based testing replaces hand-picked examples with a generator that produces hundreds of random
inputs and a property that must hold for every one; when a case fails, the framework shrinks it to the
smallest input that still fails. In AI systems the deterministic scaffolding (chunkers, token budgeters,
prompt renderers, tool-argument parsers, output validators) is where silent bugs live, and nobody writes
the example that breaks it. Properties on model outputs are possible too, at the price of one model call
per generated example.

## Why it exists
Example-based tests encode what their author imagined. The chunker is tested on a three-paragraph
document and never on an empty string, a single 50,000-character line, text shorter than the overlap,
or a string of combining Unicode marks. The prompt renderer is tested with "What is the refund policy?"
and never with user content containing a triple backtick, a `{placeholder}`, or a line beginning with
`### ` that the template treats as a section header. The tool-argument parser is tested on the JSON the
author typed and never on the JSON a model actually emits, with a number as a string and a trailing
comma. The input space of an AI system is "anything a user sends, plus anything a model produces", and
both are adversarial in practice.

The naive fixes do not close the gap. Writing more examples is still bounded by imagination. Fuzzing
with random bytes finds crashes but not wrong answers, because without a property there is nothing to
assert, and without shrinking a failure is a 4 KB blob nobody can read. Property-based testing, which
began as QuickCheck, changes the division of labour: the engineer states what must be true for *all*
inputs, the framework explores the space, and when it finds a counterexample it minimises it into
something a human can understand in five seconds. For the parts of an AI system that are plain code,
this is the cheapest way to find the edge cases that would otherwise surface as a 3 a.m. production
exception inside a retrieval pipeline.

## How it works
### Three parts
1. **Generators** (strategies in Hypothesis) describe the input space: `st.text()`, `st.integers(min_value=0)`,
   `st.lists(...)`, `st.builds(MyDataclass, ...)`, `st.recursive(...)` for JSON-like trees. They compose,
   and the framework biases them toward boundary and simple values (empty collections, zero, the ends of
   a declared range, unusual Unicode) because that is where bugs cluster. Note that `st.text()` excludes
   surrogate code points by default, so ask for them explicitly if your code handles raw UTF-16 data.
2. **A property** is a test function with asserts that must hold for every generated input.
3. **The runner** executes a configured number of examples (Hypothesis defaults to 100), and on failure
   **shrinks**: it repeatedly tries simpler inputs that still fail, guided by the structure of the
   strategy, until it reaches a minimal reproducer. It stores that example in a local database so the
   next run replays it first, which turns a found bug into a regression test automatically.

### Finding properties worth stating
The property must not re-implement the function, or a bug in the logic appears in both. Useful shapes:
- **Round trip**: `parse(render(x)) == x` for tool-call arguments and structured outputs.
- **Invariant**: every chunk is at most `size` characters; a rendered prompt never exceeds the token
  budget for any user input under *N* characters.
- **Idempotence**: normalising a query twice equals normalising it once.
- **Oracle comparison**: a fast token estimator agrees with the real tokenizer within a tolerance; a
  cached scorer agrees with the uncached one.
- **Metamorphic relation**: the sibling lesson's relations, with the generator supplying the inputs.
- **No crash**: the weakest property, still worth having for parsers facing model output.

### AI-specific targets
- Chunking and splitting: coverage (concatenation reconstructs the text), size bound, overlap is real
  overlap, no empty chunks, Unicode safety.
- Prompt templating: user content cannot escape its delimiters; include injection-shaped strings in the
  generator; token count stays within the context limit.
- Tool-call argument validation: any JSON the model could emit either validates into a typed object or
  raises one typed error, never a `KeyError` three frames deep inside the tool.
- Output parsers: markdown fence stripping, partial or streamed JSON, numbers emitted as strings.
- Retrieval plumbing: top-k returns at most k, scores are sorted, metadata filters are respected, for
  generated metadata and queries.
- Cost and budget accounting: non-negative, additive across turns.

### Properties on model outputs
The same shape applies to the model itself: generate inputs, call the model, assert a property (schema
valid, no echo of a planted secret, reply language matches the input, length within bounds). It works,
with three adjustments *because* each example costs a network call and the model is nondeterministic:
cap `max_examples` low and disable the per-example deadline; cache responses keyed by model, parameters
and prompt so re-runs are free; and treat failures as a measured rate reported by the eval harness
rather than a hard assertion that blocks every PR. Record-and-replay turns these into fast tests of the
parser around the model rather than of the model.

### Shrinking is the feature
A failing 2,000-character string shrinks to `""` or to a single `"́"`, and that minimal case *is*
the diagnosis: empty input, or a combining character split from its base. Pin shrunk counterexamples
with an explicit `@example(...)` decorator so they survive database cleanup and document the bug.

## Example
Three properties over a fixed-size overlapping chunker, the kind of helper every RAG pipeline carries.
A naive version without the empty-text guard fails the "no empty chunks" assertion and Hypothesis shrinks
the counterexample to `text=''`, which is exactly the bug.

```python
# pip install hypothesis  ->  pytest -q test_chunk.py
from hypothesis import given, settings, strategies as st

def chunk(text: str, size: int, overlap: int) -> list[str]:
    if not text:                          # without this guard the loop emits one empty chunk for ""
        return []
    step, chunks, i = size - overlap, [], 0
    while True:                           # emit windows until one reaches the end of the text
        chunks.append(text[i:i + size])
        if i + size >= len(text):
            return chunks
        i += step

params = st.integers(min_value=1, max_value=200).flatmap(
    lambda size: st.tuples(st.just(size), st.integers(min_value=0, max_value=size - 1)))

@settings(max_examples=500)
@given(text=st.text(max_size=2000), p=params)
def test_chunk_properties(text, p):
    size, overlap = p
    step = size - overlap
    chunks = chunk(text, size, overlap)
    assert all(0 < len(c) <= size for c in chunks)                 # size bound and no empty chunks
    rebuilt = "".join(c[:step] for c in chunks[:-1]) + (chunks[-1] if chunks else "")
    assert rebuilt == text                                         # coverage: nothing lost or duplicated
    for a, b in zip(chunks, chunks[1:]):
        assert b.startswith(a[step:])                              # the overlap is real overlap
    assert (len(chunks) == 0) == (text == "")
```

## Trade-offs
- Hundreds of generated inputs per property give coverage no example suite reaches, but the run is
  slower and non-deterministic in which cases it explores, so CI relies on the example database and
  pinned `@example` cases for the known failures.
- Properties catch classes of bugs instead of instances, but a weak property ("does not crash") passes
  wrong answers; the value scales with how specific the property is.
- Generating inputs with a model-shaped distribution (realistic JSON, realistic questions) finds more
  relevant bugs than uniform random text, at the cost of writing custom strategies.
- Model-output properties test the real system, but each example costs money and time and fails
  randomly, so they belong in a nightly job with caching rather than a PR gate; that flips if responses
  are recorded and replayed.
- Shrinking produces minimal reproducers, but for generators with complex constraints it can be slow or
  produce a minimal case that is valid but unrealistic, which still needs interpretation.

## Pitfalls
- Writing the property as a copy of the implementation; the symptom is a test that passes forever and a
  bug that ships in both.
- Over-filtering generated inputs with `assume` until almost nothing survives; the symptom is a
  `FailedHealthCheck` error (`filter_too_much`), or, if someone suppresses it, a test that effectively
  ran ten examples.
- Letting a model-calling property run with the default deadline and example count; the symptom is a
  flaky, slow test that gets marked skip within a week.
- Ignoring the shrunk counterexample and patching for the original 2 KB input; the symptom is the same
  bug reappearing with a different long input.
- Testing the chunker on ASCII only by restricting the alphabet; the symptom is production failures on
  emoji, CJK text or combining marks that the generator would have found in seconds.

## When to use
Reach for property-based tests on every pure function that sits between the user and the model or
between the model and a tool: chunkers, renderers, parsers, validators, budgeters, routers. They pay
off most where inputs are open-ended and the author cannot enumerate the edge cases. Use them on the
model itself sparingly, with caching and as a rate, and use metamorphic relations as the properties
there. Do not use them for behaviour that only a label can judge; that is the golden set's job. Measure
how many distinct bugs the properties found and how often a shrunk counterexample became a pinned
case. In interviews, the expected depth is to name a non-trivial property for a chunker or parser,
explain shrinking, and say why model-output properties need different settings.

## Role lens
- **Engineer:** Writes strategies that resemble real user and model output, states round-trip and
  invariant properties for every parser and renderer, and pins shrunk counterexamples as examples.
- **Architect:** Requires property tests on the contract boundaries of the platform (tool schemas,
  prompt templates, chunking libraries) so every team's edge cases are found before integration.
- **FDE:** Generates inputs from the customer's real document formats and identifier patterns, because
  their edge cases (legacy encodings, odd date formats) are not in anyone's default strategy.

## Check yourself
**Q:** You inherit a prompt-rendering function that inserts user text between delimiters and a
tool-argument parser that turns model JSON into a typed object. What properties would you write, and
what would you avoid?
**A:** For the renderer: for any user string, the rendered prompt still contains the delimiters in the
expected positions and the user text appears only between them, with a generator that includes the
delimiter strings, backticks, braces and heading markers; and the rendered token count stays under the
budget for inputs up to the allowed length. For the parser: a round trip from a generated typed object
through JSON and back is identity; for arbitrary JSON-shaped input, including numbers as strings and
missing keys, the parser either returns a valid object or raises one typed validation error, never an
unexpected exception. Avoid restating the implementation as the property, avoid ASCII-only generators,
and keep any model-calling property out of the PR gate with caching and a low example count.

## Quiz
### Q: What does shrinking contribute beyond finding a failing input?
- [ ] It proves the property holds for all smaller inputs
- [x] It reduces the failure to a minimal case that directly points at the cause
- [ ] It removes the need for an example database
- [ ] It increases the number of examples run per test
> Shrinking turns a random 2 KB failing string into something like an empty string or a single
> combining character, which is the diagnosis itself. It does not prove anything about other inputs and
> the database is still what replays known failures.

### Q: Why do properties over model outputs need different settings from properties over pure code?
- [ ] Model outputs cannot be generated by strategies
- [ ] Hypothesis cannot assert on strings returned by a network call
- [x] Each example costs a slow, paid, nondeterministic model call, so examples must be capped, cached and treated as a rate
- [ ] Models always satisfy schema properties, so the tests are redundant
> A property that calls a model is still valid, but 100 examples with the default deadline is slow,
> expensive and flaky. Cap the count, disable the deadline, cache responses and report failures as an
> eval rate rather than a hard CI assertion.

### Q: Which property for a chunker is least useful?
- [ ] Concatenating the chunks (minus overlap) reconstructs the original text
- [ ] Every chunk is non-empty and at most the configured size
- [x] The output equals the output of a second copy of the same chunking code
- [ ] Each chunk begins with the previous chunk's overlap region
> Comparing against a copy of the implementation reproduces its bugs on both sides. Coverage, size bound
> and overlap are independent statements about what a correct chunker must do, so they can fail when the
> code is wrong.
