---
id: outlines
module: python-for-ai
name: Outlines
kind: tool
difficulty: advanced
est_minutes: 15
related:
  - python-for-ai/instructor
  - python-for-ai/pydantic
  - python-for-ai/hugging-face
  - serving-inference/structured-constrained-generation
  - prompting/structured-outputs-json-mode
  - model-internals/sampling-strategies
  - serving-inference/engines
sources:
  - Willard & Louf, 2023 — Efficient Guided Generation for Large Language Models
  - Official docs: Outlines
---

## TL;DR
Outlines makes a language model's output match a regex, a JSON Schema, a fixed set of choices or a
grammar *by construction*, by masking every token that would break the pattern before sampling. Invalid
output becomes impossible rather than detected and retried. The catch is that it needs access to the
model's logits, so it applies to models you run yourself (or engines that integrate it), not to closed
hosted APIs.

## Why it exists
A language model samples one token at a time from a distribution over its whole vocabulary. Nothing in
that process knows that you wanted a JSON object with an integer `age`. Prompting raises the
probability of valid output; it never makes it certain, and smaller open models, the ones you can afford
to self-host, break formats far more often than frontier APIs. The after-the-fact fix, parse and retry,
has two costs. Each retry is another full generation, so tail latency multiplies. And retries do not
converge on hard inputs: a model that keeps writing `"age": "about 40"` will keep doing it.

The observation behind Outlines is that if you control sampling, you can refuse to sample invalid
tokens at all. At each step, compute which vocabulary tokens could continue a valid string, set every
other token's logit to negative infinity, and sample from what remains. The output is valid on the first
pass, always. The naive version of this, checking every vocabulary token against the pattern at every
step, costs a full scan of a vocabulary of tens or hundreds of thousands of tokens per generated token,
which is too slow. Outlines' contribution was making the check effectively a lookup.

## How it works
### From pattern to automaton
1. **Compile the constraint to a regular expression.** A regex is used directly. A JSON Schema is
   translated into a regex that matches exactly the JSON strings that satisfy it (keys in schema order,
   types as sub-patterns, enums as alternations). A `choice(["yes","no"])` is an alternation.
2. **Regex to finite-state machine.** The regex becomes a deterministic finite automaton over
   *characters*: states, and transitions labelled by characters.
3. **Index the vocabulary against the FSM.** This is the key step. For every FSM state, walk each
   vocabulary token's string character by character from that state; if the walk never falls off the
   automaton, the token is allowed in that state, and you record the state it lands in. The result is a
   map `state -> {allowed token -> next state}`. It is built once per (pattern, tokenizer) pair *because*
   it is expensive, then cached and reused across every request with the same schema.
4. **Generate.** At each decoding step, look up the current state's allowed set, mask all other logits,
   sample (greedy, temperature, whatever you configured) from the survivors, then follow the transition
   to the next state. Per-step overhead is a lookup and a mask, not a vocabulary scan.

Working at the token level, not the character level, matters *because* tokens span several characters:
the token `"name"` might move the FSM four characters forward in one step, and the index records that.

### Grammars
Regular expressions cannot describe arbitrarily nested structures. JSON with recursive schemas, or
code in a programming language, needs a context-free grammar, which requires a pushdown automaton
(a state plus a stack). Outlines supports grammar-constrained generation too, but it is costlier and
less mature than the regex path; engines such as vLLM also offer alternative grammar backends for this
reason.

### Where it runs
Outlines wraps local model runtimes (Hugging Face Transformers, llama.cpp, MLX and others) and its core
FSM logic is used inside serving engines; vLLM's guided decoding has offered an Outlines backend. The
public Python API has changed between major versions (older code uses `outlines.generate.json(model,
Schema)`; the 1.x API passes the output type when calling the model), so check the version you install.

### What constraining does to quality
Masking renormalizes probability over the allowed tokens. If the model strongly wanted a token you
forbade, it is pushed onto a path it considered unlikely, and the content on that path can be worse even
though the format is perfect. Constraints guarantee syntax, never correctness.

## Example
A worked scenario: classifying 50,000 support tickets on a self-hosted 7B model into
`{"intent": one of 6 labels, "urgent": true|false}`.

Unconstrained with a careful prompt, suppose 4% of outputs fail to parse (an illustrative figure; measure
your own). With parse-and-retry, 2,000 tickets need at least one more generation, some need several, and
the p99 latency is now two or three generations long. With Outlines, the JSON Schema compiles once into an
index. The model can only emit `{"intent":"`, then the first token of one of the six labels, then the
closing quote, `,"urgent":`, then `true` or `false`, then `}`. Every output parses; no retries exist.

Two more things happen. First, the model emits far fewer free tokens: most of the structure is forced, and
when only one token is allowed in a state there is nothing to sample, and some implementations append
such forced tokens directly and process several of them in a single forward pass. Second, field order is generation order, so if you want the model to reason, put a bounded
`reasoning` string *before* `intent` in the schema; otherwise the label is chosen with no reasoning to
condition on.

```python
from typing import Literal
from pydantic import BaseModel
import outlines
from transformers import AutoModelForCausalLM, AutoTokenizer

class Ticket(BaseModel):
    intent: Literal["billing", "bug", "how_to", "account", "feature", "other"]
    urgent: bool

name = "Qwen/Qwen2.5-0.5B-Instruct"   # any local causal LM
model = outlines.from_transformers(
    AutoModelForCausalLM.from_pretrained(name), AutoTokenizer.from_pretrained(name))
raw = model("Classify: 'I was charged twice this month, fix it today.'", Ticket, max_new_tokens=50)
print(Ticket.model_validate_json(raw))   # always parses; may still be the wrong label
```

## Trade-offs
- Validity is guaranteed on the first pass and retries vanish, but only when you own the inference
  stack; on hosted APIs you rely on the provider's own structured output or on validate-and-retry tools.
- Index construction is paid once per schema and tokenizer; for a few fixed schemas it amortizes to
  nothing, but per-request dynamic schemas pay compile latency each time unless cached.
- Hard constraints remove format errors yet can lower content quality when the mask fights the model's
  preferences; a looser pattern plus validation sometimes scores better on accuracy evals.
- Regex-based JSON is fast and robust for flat or bounded schemas; deeply recursive structures need
  grammar mode, which costs more per step and is where alternative backends may perform better.

## Pitfalls
- Unbounded strings or whitespace in the pattern let the model fill tokens until `max_new_tokens`;
  the symptom is outputs truncated at the limit, which then fail to parse despite the constraint.
- Assuming constrained output is correct output; the symptom is a perfect parse rate alongside poor
  accuracy on a labelled set, because format and truth are different properties.
- Putting the answer field before any reasoning field; the label is committed first, and the symptom is
  lower accuracy than the same model achieves in free-form chain-of-thought.
- Copying tutorial code across major versions; the API was reorganised, and the symptom is
  `AttributeError` on `outlines.generate` or `outlines.models` after an upgrade.
- Forgetting the chat template for instruct models; passing a bare prompt degrades quality, which shows
  up as worse labels than the same model gives through its usual chat interface.

## When to use
Use Outlines (or an engine's guided decoding) when you self-host open models and need structured output
at volume: classification, extraction, routing, agent tool arguments on a small model. It is most
valuable where retries are expensive or latency-bound, and where small models break formats often. Do not
reach for it with closed APIs, which do not expose logits; use their native structured output or a
validate-and-retry library like Instructor. Measure parse rate (should become 100%), task accuracy with
and without the constraint, and compile latency for new schemas. In interviews, explain the FSM index and
why constraining fixes syntax but not semantics.

## Role lens
- **Engineer:** Defines schemas with bounded strings and sensible field order, wires constrained decoding
  into local inference, and compares accuracy with and without the constraint.
- **Architect:** Decides whether structure is enforced at decode time on self-hosted models or by
  validation on hosted APIs, and which serving engine backend carries it.
- **FDE:** In air-gapped or on-prem deployments with only small open models, uses constrained decoding to
  get reliable structured output where retries would blow latency budgets.

## Check yourself
**Q:** Why does Outlines precompute an index from FSM states to allowed tokens instead of checking the
regex against each candidate token at generation time, and what does that cost you?
**A:** Checking at generation time means testing every vocabulary token, tens or hundreds of thousands,
against the pattern from the current state on every step, which adds a large per-token overhead. Since the
set of valid tokens depends only on the FSM state, the pattern and the tokenizer, it can be computed once:
for each state, walk each token's characters through the automaton and record whether it survives and
where it lands. Generation then becomes lookup plus masking. The cost moves to compile time and memory
per schema, which is cheap for a few stable schemas but painful for per-request dynamic schemas, so you
cache indexes and avoid generating schemas on the fly.

## Quiz
### Q: Why can't Outlines enforce a schema on a closed hosted model API?
- [ ] Hosted models use a different tokenizer format that Outlines cannot parse
- [x] It must mask logits before each token is sampled, and hosted APIs do not expose that step
- [ ] Hosted APIs reject requests containing regular expressions
- [ ] JSON Schema is only supported by open-weight models
> Constrained decoding intervenes inside the sampling loop. Hosted APIs return finished tokens, so the
> only options there are the provider's own structured output or validating and retrying afterwards.

### Q: A constrained classifier now parses 100% of outputs but its accuracy did not improve. What is the most likely explanation?
- [ ] The FSM index was built for the wrong tokenizer
- [ ] Constrained decoding always lowers temperature to zero
- [x] Constraints guarantee format, not correctness, so the model can still choose the wrong valid label
- [ ] The schema compiled to a context-free grammar instead of a regex
> Masking removes invalid tokens but leaves the model's choice among valid ones untouched. Previous parse
> failures may also have been the hard cases, so accuracy can stay flat or even drop.

### Q: Which schema design choice tends to cause truncated, unparseable outputs under constrained decoding?
- [ ] Using a Literal field with six allowed values
- [ ] Putting a boolean field last in the object
- [x] An unbounded free-text string field, which lets generation run until max_new_tokens
- [ ] Declaring all fields as required
> The constraint allows any length string, so a looping model keeps emitting valid characters until the
> token limit, leaving the JSON unclosed. Bounding length with maxLength prevents it.
