---
id: structured-outputs-json-mode
module: prompting
name: Structured outputs / JSON mode
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
related:
  - prompting/function-tool-calling
  - prompting/prompt-templates
  - prompting/chain-of-thought
  - serving-inference/structured-constrained-generation
  - agents/tool-schemas-and-argument-validation
  - agents/typed-tool-contracts
  - safety-security/output-validation
  - evals/regression-tests
sources:
  - Official docs: Anthropic structured outputs
  - Willard and Louf, 2023 — Efficient Guided Generation for Large Language Models
---

## TL;DR
Downstream code needs a parseable object with known keys and types, but a language model emits free
text. JSON mode guarantees syntactically valid JSON; structured outputs go further and guarantee
conformance to a schema you supply, by constraining which tokens the model may emit at each step. This
removes a class of parse failures, not a class of wrong answers: schema-valid output can still be wrong,
so semantic validation and evals remain your job.

## Why it exists
Most production LLM calls are not chat. They extract fields from a document, classify a ticket, route a
request, or produce arguments for another system, and the result is consumed by code. The prompt-only
approach, "respond only with JSON matching this shape", works most of the time, and "most" is the
problem. The model wraps the object in markdown fences, adds a closing sentence, uses single quotes,
returns `"42"` where an integer was expected, drops a field it could not find, or invents a key. A 1%
failure rate on 100,000 calls a day is 1,000 crashes or silently dropped records a day.

The usual mitigations are weak. Regex extraction of the first `{...}` block is brittle. A
validate-and-retry loop doubles cost and latency on the failing tail and still fails when the model
repeats the mistake. Few-shot examples help but cannot promise anything.

The decisive observation is that the model produces its output one token at a time by sampling from a
probability distribution that your serving layer controls. If you can forbid tokens that would break the
required format before sampling, invalid output cannot be produced at all. JSON mode applies that idea
to the grammar of JSON; structured outputs apply it to your specific schema.

## How it works
### Three levels of guarantee
1. **Prompting** ("return JSON with these keys"): no guarantee, only a strong tendency.
2. **JSON mode** (the name comes from OpenAI's `response_format` option; other providers offer
   equivalents): the output is guaranteed to parse as JSON. Keys, types and required fields are not
   enforced, so `{"result": "I could not find the invoice"}` is a valid answer to a request for
   `{"invoice_id", "total"}`.
3. **Schema-constrained output** (structured outputs, strict mode): the output is guaranteed to validate
   against the JSON Schema you pass with the request.

### Constrained decoding
At each generation step the model produces a logit for every token in the vocabulary. The server
compiles your schema into a grammar and, from the grammar, a state machine over *tokens* rather than
characters, which is the hard part because a single token can span several characters and several
grammar transitions (this is the contribution of Willard and Louf's guided-generation work). At each
step the state machine yields the set of tokens that keep the output valid; every other token's logit is
set to negative infinity before sampling. The model still chooses among the allowed tokens according to
its own probabilities, so it decides the *content*, while the mask decides the *form*. Compilation is
paid once per new schema and cached by the provider, so the first request with a schema is slower than
the rest. The serving module covers the implementation inside open-source engines.

A concrete step: the schema says `severity` is one of `low`, `medium`, `high`, and the output so far
is `{"evidence": ["..."], "severity": "`. The automaton now allows only tokens that begin one of those
three strings (`low`, `med`, `high`, `h`, ...). If the model's top choice was `crit` (for "critical"),
that token is masked, the remaining probability is renormalised over the allowed tokens, and the model
emits whichever allowed value it found most plausible. That is the key consequence: **the mask can
force an answer but cannot make the model abstain unless the schema has a way to say so**. If the
document contains no total and `total` is a required number, the model must still write some number.
Nullable fields and explicit `unknown` or `other` enum values are the escape hatches that keep a
forced format from turning into forced fabrication.

### Why masking beats rejection sampling
Generate-validate-retry spends a full generation on each failure and offers no bound on attempts.
Masking makes invalid output impossible in a single pass and costs almost nothing per step, because the
allowed-token set is computed from the automaton state, not by re-running the model.

### Schema limits
Providers accept a subset of JSON Schema. Typical rules: every object needs `additionalProperties:
false` and a `required` list; enums, `anyOf` and references are supported; recursion, numeric bounds
(`minimum`, `maximum`) and string length constraints often are not, and the SDKs strip those and
validate them client-side after the response. Treat the provider's list as the contract and keep your
own validation for anything it cannot express.

### Field order is prompt engineering
Generation is left to right, so a field that appears earlier is produced before, and conditions, a field
that appears later. Putting an `evidence` or `reasoning` field before the `label` field lets the model
commit to the answer after it has written its justification; putting the label first produces a label
followed by a rationalisation. This is chain-of-thought expressed as schema layout, and it is often the
most effective lever once the format is fixed. The effect is smaller on models run with extended or
adaptive thinking, which reason in a separate block before the first output token, but it costs nothing
and still helps when that reasoning is short. Where the task needs long free reasoning, reason first in
an unconstrained step (or with extended thinking) and extract in a second, constrained call.

### Tool calling as structured output
Before native structured outputs, the standard trick was to define a tool whose input schema is the
object you want and force the model to call it. It still has a place: when the model should choose among
several output shapes, one tool per shape expresses the choice naturally, and strict tool schemas give
the same conformance guarantee for arguments. For a single fixed shape, native structured output is the
cleaner mechanism.

### What you still own
Three things survive the guarantee. A `max_tokens` stop truncates the JSON mid-object, so check the stop
reason before parsing. A safety refusal may not match the schema. And schema-valid values can be wrong:
a hallucinated but well-formed email address passes every check, which is why fields for data that may
be absent should be nullable rather than required.

## Example
A ticket triage call with a Pydantic model as the schema. The `evidence` field comes first so the label
is conditioned on quoted justification, `customer_tier` is nullable so the model is not forced to invent
one, the stop reason is checked before the parsed object is trusted, and a semantic rule the schema
cannot express is enforced in code.

```python
from enum import Enum
from pydantic import BaseModel, Field
import anthropic

class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"

class Triage(BaseModel):
    evidence: list[str] = Field(description="Quotes from the ticket that justify the decision")
    product_area: str
    severity: Severity
    customer_tier: str | None          # nullable: absent data must not be invented
    needs_human: bool

client = anthropic.Anthropic()

def triage(ticket: str) -> Triage:
    r = client.messages.parse(
        model="claude-opus-5-5", max_tokens=4096,   # thinking tokens also count against this cap
        system="Classify support tickets. Quote evidence before deciding.",
        messages=[{"role": "user", "content": ticket}],
        output_format=Triage,          # SDK helper; sent as output_config.format, compiled server-side
    )
    if r.stop_reason != "end_turn":    # max_tokens => truncated JSON; refusal => may not match schema
        raise RuntimeError(f"unusable output: {r.stop_reason}")
    t = r.parsed_output                # validated Triage instance; SDK re-validates client-side
    if t.severity == Severity.high and not t.evidence:
        raise ValueError("high severity without evidence")   # semantic rule the schema cannot state
    return t

print(triage("Checkout returns 500 for every EU customer since 09:00 UTC. We are an enterprise account."))
# Triage(evidence=['Checkout returns 500 for every EU customer'], product_area='checkout',
#        severity=<Severity.high: 'high'>, customer_tier='enterprise', needs_human=True)  (illustrative)
```

## Trade-offs
- Guaranteed parseability costs expressiveness; it flips when the task is a summary or an explanation,
  where a schema with one free-text field adds nothing and a plain response is better.
- Constraining the token distribution can shift quality on reasoning-heavy tasks; if a golden-set eval
  drops after switching, reason in an unconstrained step first and extract in a second constrained call.
- Schema compilation adds latency to the first request with a new schema; negligible for a few stable
  schemas, material when schemas are generated per user or per request.
- Tool-call-as-output lets the model pick among several shapes but mixes "take an action" semantics with
  "produce a result"; native output format is simpler when there is exactly one shape.
- JSON mode alone is cheap and portable across providers but enforces nothing about keys or types; fine
  for a prototype, not for a production contract that other services depend on.
- Strict schemas forbid extra keys, so the model cannot volunteer useful information; add an explicit
  `notes` field if that matters, and version the schema because changing it changes behaviour.

## Pitfalls
- Parsing without checking the stop reason; the symptom is `JSONDecodeError` only on long inputs, where
  `max_tokens` truncated the object.
- Marking fields required when the data is often absent; the symptom is plausible invented values
  (emails, dates, ids) that pass validation and corrupt the database.
- Putting the label before the reasoning field; the symptom is accuracy below the old chain-of-thought
  prompt even though parse failures went to zero.
- Relying on `minimum`, `maxLength` or regex patterns the provider does not enforce; the symptom is
  out-of-range values that passed server-side and were never checked client-side.
- Treating "valid" as "correct" and dropping the eval; the symptom is silent mislabeling discovered by a
  downstream team weeks later.
- No escape value in a required enum or number field; the symptom is confident labels or figures
  on inputs that do not contain the information, visible when you feed the golden set blank or
  off-topic documents.
- Enums with overlapping or too many categories; the symptom is near-identical tickets receiving different
  labels, visible as low agreement on paraphrased test cases.
- Prompt text that contradicts the schema (asking for markdown while forcing JSON); the symptom is fences
  and formatting characters appearing inside string fields.

## When to use
Use schema-constrained output whenever code consumes the result: extraction, classification, routing,
tool arguments, and eval judges that return scores. Use plain JSON mode only for prototypes or when the
provider offers nothing stronger. Avoid both for user-facing prose and for open-ended generation, and
avoid schemas that are deeply nested or recursive, since providers reject them and the model handles them
poorly anyway. Measure the parse failure rate (it should be zero), semantic accuracy against a golden
set before and after the switch, and the latency and token delta. In interviews, the expected depth is:
how constrained decoding works, why it guarantees form and not truth, and why field order matters.

## Role lens
- **Engineer:** Defines the Pydantic or Zod model, uses the SDK parse helper, checks stop reasons, adds
  semantic validation and keeps the schema under version control next to the prompt.
- **Architect:** Treats typed model outputs as API contracts between the LLM layer and the services that
  consume them, and decides where validation lives and how schema changes are rolled out.
- **FDE:** Maps the customer's CRM or ticketing fields and legacy enum values onto the schema, and uses
  the parse failure rate dropping to zero as the adoption argument with the customer's engineers.

## Check yourself
**Q:** You replaced a "respond in JSON" prompt with schema-constrained output. Parse errors went from 2%
to zero, but accuracy on the classification golden set fell four points. What happened and what do you do?
**A:** Constrained decoding changes the form of the output, not what the model knows, so the drop comes
from what the schema took away. The most common cause is field order: the old prompt let the model reason
in prose before answering, and the new schema asks for the label first, so the label is produced before
any reasoning and the rest is rationalisation. Other causes are enum names that no longer match the
wording the old prompt used, and the loss of extended reasoning on hard cases. The fix is to add an
evidence or reasoning field ahead of the label, check the enum names and descriptions, and for hard
cases split into an unconstrained reasoning call followed by a constrained extraction. Re-run the golden
set per category rather than in aggregate, and keep the schema versioned so the change is attributable.

## Quiz
### Q: What does schema-constrained decoding actually guarantee?
- [ ] That the values in the output are factually correct
- [x] That the output conforms to the schema, with the model still choosing the content
- [ ] That the model will never refuse or hit the token limit
- [ ] That the output is identical across repeated requests
> The token mask forbids tokens that would break the grammar; among allowed tokens the model samples as
> usual. Correctness, refusals and truncation remain the caller's responsibility to check.

### Q: Which schema design choice most directly improves classification accuracy?
- [ ] Adding more enum values to cover every edge case
- [ ] Setting additionalProperties to true so the model can add context
- [x] Placing a reasoning or evidence field before the label field
- [ ] Placing the label first so it is not influenced by the reasoning
> Generation is left to right, so earlier fields condition later ones. Reasoning before the label is
> chain-of-thought in schema form; label first produces a commitment followed by a rationalisation.

### Q: Output from a structured-output call fails to parse. What is the most likely cause?
- [ ] The schema compilation cache expired mid-request
- [x] The response stopped on max_tokens and the JSON was truncated
- [ ] The model ignored the schema because the prompt contradicted it
- [ ] JSON mode was not enabled alongside the schema
> With constrained decoding the model cannot emit invalid tokens, but it can run out of output budget
> mid-object. The stop reason tells you; raise max_tokens or shorten the schema.
