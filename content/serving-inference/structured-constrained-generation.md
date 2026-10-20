---
id: structured-constrained-generation
module: serving-inference
name: Structured / constrained generation
kind: pattern
difficulty: core
est_minutes: 15
related:
  - prompting/structured-outputs-json-mode
  - prompting/function-tool-calling
  - python-for-ai/outlines
  - python-for-ai/instructor
  - python-for-ai/pydantic
  - model-internals/logits
  - model-internals/tokenization
  - agents/tool-schemas-and-argument-validation
sources:
  - Willard & Louf, 2023 — Efficient Guided Generation for Large Language Models
  - Dong et al., 2024 — XGrammar: Flexible and Efficient Structured Generation Engine for Large Language Models
  - Tam et al., 2024 — Let Me Speak Freely? A Study on the Impact of Format Restrictions on Performance of Large Language Models
  - Official docs: vLLM
---

## TL;DR
Structured generation makes a model's output conform to a schema, regex, grammar or fixed set of
choices. The reliable way is constrained decoding: at every step the engine masks the logits of tokens
that would violate the grammar, so the sampler can only pick valid continuations and the output parses
by construction. That removes the parse-and-retry loop, but it guarantees shape, not truth, costs a
grammar compile step, and can subtly change what the model says, so schema design still matters.

## Why it exists
Code downstream of a model needs something it can parse: a JSON object with known fields, a function
call with typed arguments, one of five labels. Asked nicely, models mostly comply, and "mostly" is the
problem. They wrap JSON in Markdown fences, add a trailing comma, rename a field, emit a comment, or
start with "Sure, here is the JSON". At a 2% failure rate, a pipeline running a million calls a day
breaks twenty thousand times, and the failures cluster on the hardest inputs.

The naive fix is validate-and-retry: parse the output, and if it fails, send it back with the error
message. That works at low volume and is still worth having for semantic checks, but each retry doubles
latency and cost on exactly the requests that were already slow, the failure rate is never zero, and
small or heavily quantized models can loop without converging. Post-processing with regex repair is
brittle in the other direction: it silently accepts malformed intent.

Constrained decoding attacks the root. The model proposes a distribution over next tokens; the engine
knows, from the grammar and what has been emitted so far, which tokens are legal; it zeroes the rest
before sampling. Invalid output becomes impossible rather than unlikely. The mechanism exists in open
engines (vLLM, SGLang, llama.cpp, TensorRT-LLM) and behind hosted "structured outputs" and strict
tool-calling modes, so the pattern is now the default way to get machine-readable output.

## How it works
### Where the constraint sits
Each decode step produces a logits vector over the vocabulary. A constraint is a *logits processor*: it
computes the set of allowed token ids for the current state and adds −∞ to every other entry. Softmax
then assigns those tokens zero probability, and temperature, top-p and the rest apply only among the
allowed ones. The model is never asked to do anything; it is simply not permitted to make certain moves.

### The token-versus-character problem
Grammars are defined over characters, but the model emits tokens, and a vocabulary of 100k+ entries
contains many tokens that straddle grammar boundaries: `"na`, `me":`, ` {"`. The brute-force check,
"for each of 128k tokens, would appending its characters keep the output valid?", costs a full grammar
walk per token per step, which is far too slow for a decode step budgeted in milliseconds.

Outlines' insight was to do that work once. Compile the regex (or the regex derived from a JSON schema)
into a deterministic finite automaton, then, for every automaton state, walk every vocabulary token
through the automaton and record which tokens are legal from that state and which state each leads to.
At generation time the engine tracks the current state and the allowed set is a table lookup; the
expensive vocabulary scan happened at compile time *because* it depends only on the grammar and the
tokenizer, not on the request. Non-recursive JSON schemas compile to regexes; recursive or deeply nested
grammars need a pushdown automaton (a context-free grammar), which is what llama.cpp's GBNF grammars and
XGrammar implement, the latter caching the tokens whose legality does not depend on the stack so that
most steps are still a lookup.

### What the automaton controls
Three things fall out of the state machine. End-of-sequence is allowed only in an accepting state, so the
model cannot stop halfway through an object. When exactly one token is legal (a closing brace, a fixed
key name), some engines skip sampling and append it directly, which saves decode steps on boilerplate.
And enums, numeric ranges and string patterns become hard constraints: a `"status"` field declared as
`enum: ["open", "closed"]` cannot come back as `"Closed"`.

### What it does not control
A valid schema says nothing about whether the values are true. A hallucinated invoice number passes a
regex for invoice numbers. Semantic validation (Pydantic validators, cross-field rules, database
lookups) still belongs after generation, and retry-with-feedback remains the right tool for *those*
failures. Constrained decoding also changes the distribution: at a point where the model wanted to write
prose, it must choose among whitespace or a quote, and may take a low-probability path. Tokenization
artefacts matter too; forcing output to begin with `{` rather than ` {` can land the model in a token
sequence it rarely saw in training. One 2024 study (Tam et al.) reported that strict format restrictions
can degrade reasoning performance in some settings, which is why the mitigations below exist.

### Design rules that follow
- Put a free-text `reasoning` or `analysis` field *before* the answer fields, or run a two-stage
  pipeline: think freely, then extract under constraint. The model reasons in prose; the schema captures
  the result.
- Keep schemas flat and small; prefer enums to open strings; make field order match the order evidence
  appears.
- Compile and cache grammars by schema hash. Compilation ranges from milliseconds to seconds for large
  schemas, and a per-request dynamic schema throws that away every call.
- Check backend coverage: hosted strict modes and open-engine backends support a *subset* of JSON Schema
  (for example requiring every field and forbidding additional properties), so validate the schema
  against the backend before relying on a feature.

### Tool calling is the same mechanism
Function-calling APIs are structured generation with a schema per tool plus a choice over which tool to
call. Hosted "strict" tool modes and open engines' tool parsers run the same automaton idea over the
argument schema, which is why argument validity is high while argument *correctness* still needs checks.

## Example
A from-scratch constrained decoder for the simplest grammar: the output must be exactly one of a fixed
set of choices, then stop. The allowed-token table is a trie built once from the tokenizer, so each step
is a lookup rather than a vocabulary scan, which is the same structure Outlines and XGrammar generalise
to regexes and grammars.

```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, LogitsProcessor, LogitsProcessorList

name = "Qwen/Qwen2.5-0.5B-Instruct"
tok = AutoTokenizer.from_pretrained(name)
model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.float16).cuda().eval()

class ChoiceConstraint(LogitsProcessor):
    """Allow only token sequences that spell one of `choices`, then EOS."""
    def __init__(self, choices, prompt_len):
        self.trie, self.prompt_len = {}, prompt_len
        for c in choices:                                   # built ONCE: grammar x tokenizer
            node = self.trie
            for t in tok(c, add_special_tokens=False).input_ids:
                node = node.setdefault(t, {})
            node[tok.eos_token_id] = {}                     # accepting state: EOS becomes legal
    def __call__(self, input_ids, scores):
        node = self.trie
        for t in input_ids[0, self.prompt_len:].tolist():  # replay emitted tokens to find the state
            node = node.get(t, {})
        allowed = list(node) or [tok.eos_token_id]
        mask = torch.full_like(scores, float("-inf"))
        mask[0, allowed] = 0.0
        return scores + mask                                # every other token gets probability 0

msgs = [{"role": "user", "content": "Is the sky green? Answer with one word."}]
ids = tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt").cuda()
proc = LogitsProcessorList([ChoiceConstraint(["yes", "no", "unknown"], ids.shape[1])])
out = model.generate(ids, max_new_tokens=5, do_sample=False, logits_processor=proc)
print(repr(tok.decode(out[0, ids.shape[1]:], skip_special_tokens=True)))   # -> 'no'
```

## Trade-offs
- Syntactic validity becomes guaranteed and retries vanish, but the constraint can push the model down
  low-probability paths; the cost is small for flat schemas and grows with rigid, deeply nested ones.
- Compile-once tables make per-step masking nearly free, but a new schema per request pays compilation
  each time; the win flips to a loss without a grammar cache.
- Hosted strict modes are zero-effort, yet they support a schema subset and hide the mechanism; open
  engines expose regex, grammar and choice modes but you own backend selection and version drift.
- Validate-and-retry handles semantic errors that no grammar can express, but it costs a full extra call
  per failure; combining both gives shape for free and spends retries only on meaning.

## Pitfalls
- Trusting a schema-valid output as correct. The symptom is clean JSON with invented identifiers or
  wrong numbers that no parser will ever flag.
- Placing the answer field first and a `reasoning` field last, so the model commits to an answer before
  it has "thought"; accuracy on reasoning tasks drops compared with unconstrained prompting.
- Sending a fresh, slightly different schema on every request (a timestamp or request id baked into an
  enum), visible as high time to first token from grammar compilation on each call.
- Using JSON Schema features the backend ignores (`format: date`, `minimum`, `pattern` in some modes)
  and discovering in production that values outside the range come through.
- Forgetting that whitespace is part of the grammar; an automaton that forbids newlines inside strings
  can make the model emit `\n` escapes in places that break a downstream naive parser.

## When to use
Use constrained decoding whenever output is consumed by code: extraction, classification into a fixed
label set, tool arguments, routing decisions, batch data transformation. Prefer the engine's or
provider's native structured-output mode over hand-rolled logits processors, and keep Pydantic (or
equivalent) validation plus bounded retries for semantics. Skip it for free-form writing, and be careful
with reasoning-heavy tasks, where a two-stage design keeps accuracy. Measure validity rate, retry rate,
time to first token with and without the grammar, and task accuracy against an unconstrained baseline.
In interviews, the test is explaining why masking happens at the logits, how the token-boundary problem
is solved by compile-time precomputation, and what the guarantee does not cover.

## Role lens
- **Engineer:** Defines Pydantic models, passes the schema to the engine's structured-output mode, caches
  compiled grammars, and adds semantic validators and bounded retries behind them.
- **Architect:** Sets the contract between model outputs and downstream services, chooses hosted strict
  modes versus open-engine backends, and mandates where free-text reasoning precedes structured answers.
- **FDE:** Turns a customer's extraction target into a schema the backend fully supports, then proves
  validity and accuracy on their documents against the unconstrained baseline.

## Check yourself
**Q:** A team switched an extraction pipeline from prompt-and-retry to constrained JSON decoding.
Validity went to 100%, but accuracy on a set of hard cases dropped several points. What would you
investigate and change?
**A:** Constrained decoding changed the distribution the model samples from, not just the format. I
would first check field order: if the answer fields come before any reasoning field, the model is forced
to commit before working through the evidence, and moving a free-text analysis field to the front or
splitting into a think-then-extract two-stage call usually recovers the loss. I would check whether the
grammar forces unusual tokenizations (no leading space, escaped newlines) that differ from how the model
saw JSON in training, and whether the schema is stricter than necessary, with enums that exclude valid
answers. Then I would compare accuracy on the same prompts under unconstrained decoding plus parsing to
quantify the gap, and keep semantic validators and bounded retries for the residual errors.

## Quiz
### Q: Why do libraries like Outlines precompute a table from automaton states to allowed tokens?
- [ ] To reduce the model's vocabulary size at load time
- [ ] Because logits cannot be modified after the forward pass
- [x] So each decode step is a lookup instead of checking every vocabulary token against the grammar
- [ ] To make sampling deterministic regardless of temperature
> Checking 100k+ tokens against a grammar every step is far too slow. The grammar and tokenizer are
> fixed, so the legality of each token from each state can be computed once and reused for every
> request.

### Q: What does constrained decoding guarantee about a generated JSON object?
- [ ] That every value is factually consistent with the input
- [x] That it parses and conforms to the schema's structure, types and enums
- [ ] That it is the most probable output the model could produce
- [ ] That it matches what the model would have produced without the constraint
> The automaton only forbids tokens that would break the grammar. Content is still the model's guess,
> so semantic validation and retries remain necessary for correctness.

### Q: A reasoning-heavy classification task loses accuracy under a strict schema. What is the most likely effective fix?
- [ ] Raise the temperature so the model explores more tokens
- [ ] Remove enums so the model can write any label
- [x] Add a free-text reasoning field before the answer field, or extract in a second constrained call
- [ ] Switch from JSON to YAML output
> Forcing an immediate answer removes the model's chance to work through the problem in prose. Letting
> it reason first, then constraining the final fields, restores the behaviour that unconstrained
> prompting had.
