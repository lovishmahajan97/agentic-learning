---
id: output-validation
module: safety-security
name: Output validation (Pydantic, Instructor, Outlines)
kind: practice
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - python-for-ai/pydantic
  - python-for-ai/instructor
  - python-for-ai/outlines
  - prompting/structured-outputs-json-mode
  - serving-inference/structured-constrained-generation
  - agents/tool-schemas-and-argument-validation
  - safety-security/input-output-guardrails
  - node-ts-for-ai/runtime-schema-validation
sources:
  - Official docs: Pydantic
  - Official docs: Instructor
  - Willard & Louf, 2023 — Efficient Guided Generation for Large Language Models
---

## TL;DR
A model's output is a string until something proves otherwise. Output validation turns it into typed
data your code can trust. Pydantic defines the contract and rejects what does not fit; Instructor wraps
the model call so a validation failure becomes a retry with the error fed back; Outlines constrains
decoding so the model cannot emit a token that breaks the schema. Choose by where you can intervene:
after the fact, in the loop, or inside decoding. Shape can be guaranteed; meaning must be checked.

## Why it exists
Downstream code is typed and free text is not. The failure modes are mundane and constant: JSON wrapped
in markdown fences, a helpful sentence after the closing brace, `"42"` where an integer was required, an
enum value the model invented, a required field missing, an extra field nobody asked for, and JSON cut
off mid-string because `max_tokens` was reached. The naive handling, `json.loads` inside a try/except,
either crashes the request or, worse, lets a wrong-but-parseable object through to a database write.

There is also a security framing. Model output is untrusted input to whatever consumes it: a SQL
builder, a shell command, a tool-call argument, an HTML template. You would never pass a web form's
fields straight into those without validation, and the model is a far less predictable client than a
browser.

Three tools exist because validity has layers. *Syntactic* validity means the text parses. *Structural*
validity means the right keys and types are present. *Semantic* validity means the values make sense:
the date is not in the future, the order id exists, the line items sum to the total. Constrained
decoding can guarantee the first two at generation time. Only code that knows your domain can check the
third, and when that check fails the only remedy is to ask the model again with a precise error.

## How it works
### Pydantic: the contract
A `BaseModel` declares fields and types; `Field` adds bounds (`ge=0`), lengths and regex patterns;
`Literal` closes an enum; `field_validator` and `model_validator` express rules that need code, including
cross-field rules. `model_validate_json` parses straight from JSON text, and in Pydantic v2 the core is
compiled, so validation cost is negligible next to a model call. On failure it raises `ValidationError`
whose `.errors()` is a machine-readable list with the failing location, message and type. That list is
the asset: it is specific enough to feed back to a model. `model_json_schema()` emits JSON Schema, so one
definition serves the provider, the parser and the documentation.

### Instructor: validation in the loop
Instructor patches a provider client (`instructor.from_anthropic`, `instructor.from_openai`, ...) so the
call accepts `response_model=YourModel` and `max_retries=N`. Under the hood it derives the schema,
requests structured output through the provider's tool-call or JSON mode, parses the reply with Pydantic
and, on `ValidationError`, appends the failed attempt plus a user message containing the error text and
asks again. It works *because* models are good at fixing a named error in a named field; most retries
converge in one round. The cost is extra calls on failure, so a high retry rate is a signal to fix the
prompt or schema, not to raise `max_retries`. Any Python assertion in a validator becomes a retryable
instruction, which is how semantic rules get enforced.

### Outlines: constraints inside decoding
Outlines compiles a JSON schema, regex or grammar into a finite-state machine over the tokenizer's
vocabulary. At each decode step it computes which tokens keep the automaton in a valid state and masks
the logits of every other token before sampling. The output therefore matches the grammar by
construction, with no retries. The index is built once per schema and cached. The requirement is logit
access, which means local or open-weight models (Transformers, vLLM integrations); hosted APIs offer the
same idea as provider-operated "structured outputs" or strict schemas. Two limits matter. It guarantees
syntax, not truth: a model forced to fill a field it does not know fills it with something plausible, so
schemas should allow `null` or an `"unknown"` enum where ignorance is a legitimate answer. And a tight
grammar can steer the model into low-probability continuations, which is why schema design (field
order, descriptions, optionality) still affects quality.

### Provider-native structured outputs
Most major APIs now accept a JSON schema and constrain generation server-side, which is constrained
decoding run by the vendor. Support for schema keywords varies (regex patterns, numeric bounds and
string lengths are often not enforced), so treat the provider as guaranteeing *shape* and keep the richer
rules in Pydantic. Also check `stop_reason`: a schema-valid prefix truncated by `max_tokens` is still
invalid JSON.

### Putting the layers together
Define the model once in Pydantic. Derive the shape schema for the provider or Outlines. Parse the
result with Pydantic so semantic validators run. On `ValidationError`, retry with the error text
(Instructor, or ten lines of your own). After N failures, route to a fallback or a review queue rather
than guessing.

## Example
One Pydantic contract serves three jobs: its shape is sent to the provider so the JSON is structurally
valid by construction, its validators enforce domain rules the provider cannot, and its error messages
drive a retry loop, which is exactly what Instructor packages behind `response_model=` and `max_retries=`.

```python
from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
import anthropic

class RefundDecision(BaseModel):
    order_id: str = Field(pattern=r"^ORD-\d{6}$")
    decision: Literal["approve", "deny", "escalate"]
    amount_cents: int = Field(ge=0)
    reason: str = Field(min_length=10, max_length=300)
    order_date: date

    @field_validator("order_date")
    @classmethod
    def not_in_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("order_date cannot be in the future")
        return v

    @model_validator(mode="after")
    def deny_means_zero(self):
        if self.decision == "deny" and self.amount_cents != 0:
            raise ValueError("a denied refund must have amount_cents == 0")
        return self

SHAPE = {"type": "object", "additionalProperties": False,          # shape only: providers enforce this
         "required": ["order_id", "decision", "amount_cents", "reason", "order_date"],
         "properties": {"order_id": {"type": "string"}, "decision": {"type": "string", "enum": ["approve", "deny", "escalate"]},
                        "amount_cents": {"type": "integer"}, "reason": {"type": "string"}, "order_date": {"type": "string"}}}
client = anthropic.Anthropic()

def decide(ticket: str, max_retries: int = 2) -> RefundDecision:
    messages = [{"role": "user", "content": f"Today is {date.today()}. Decide the refund for:\n{ticket}"}]
    for _ in range(max_retries + 1):
        r = client.messages.create(model="claude-opus-5-5", max_tokens=1024, messages=messages,
                                   output_config={"format": {"type": "json_schema", "schema": SHAPE}})
        text = next(b.text for b in r.content if b.type == "text")       # parses and has the right keys
        try:
            return RefundDecision.model_validate_json(text)                # semantic rules run here
        except ValidationError as e:                                       # the loop Instructor automates
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"Fix these validation errors and return the full object:\n{e}"}]
    raise RuntimeError("no valid decision after retries; route to review")

print(decide("ORD-104233, placed 2026-09-30, blender arrived cracked, paid $89.99.").model_dump())
```

## Trade-offs
- Constrained decoding removes retries and guarantees shape, but needs logit access or provider support
  and can hide uncertainty behind a confident-looking value; this flips in your favour once the schema
  allows `null` or `unknown` wherever the model may legitimately not know.
- Retry-on-error is simple and provider-agnostic, but worst-case cost and latency multiply by the retry
  count; when the failure rate climbs past a few percent the fix is the prompt or schema, not more retries.
- Strict schemas (closed enums, no extra fields) catch drift the day it starts, but every new category
  in the domain becomes a schema release; version the contract rather than loosening it.
- Pydantic's default lax coercion (`"42"` to `42`) is convenient and hides model errors; use strict mode
  where the type carries meaning, such as money or identifiers.
- Semantic validators that hit a database make rules retryable but couple validation to I/O and latency;
  keep in-validator checks cheap and run reference-data lookups as a separate step.

## Pitfalls
- Checking only that the text parses; the symptom is `KeyError` and `TypeError` deep in downstream code
  on fields that were present but of the wrong type or shape.
- Ignoring truncation; validation errors mentioning an unterminated string cluster on long outputs
  because `max_tokens` cut the JSON, and the fix is checking `stop_reason`, not retrying blindly.
- Retrying with the identical prompt and no error feedback; the symptom is the same failure three times
  in a row and a tripled bill.
- Sending schema keywords the provider's strict mode does not enforce (patterns, bounds, lengths) and
  assuming they held; the symptom is values that violate the rule arriving with a valid-looking object.
- Making every field `Optional` to reduce failures; the model learns that `null` escapes validation
  and the symptom is a rising null rate on fields you actually needed.
- Trusting well-formed but hallucinated identifiers; an `order_id` that matches the regex but does not
  exist produces downstream 404s, so validate against reference data for anything used as a key.

## When to use
Validate anything a program consumes: extraction results, classifications, tool arguments, agent plans,
routing decisions. Use provider structured outputs or Outlines whenever available to guarantee shape at
no retry cost; always parse into Pydantic so semantic rules and typed errors exist; add an Instructor-style
retry loop when validators can fail in ways the model can fix. Free-form chat text does not need a
schema but still needs guardrails. Measure validation failure rate per field, retries per request and
truncation rate; those three numbers tell you whether the prompt, the schema or the token budget is the
problem. In interviews, be able to separate syntactic from semantic validity and place each tool on that
line.

## Role lens
- **Engineer:** Owns the Pydantic models, keeps schema and prompt in sync, exports `ValidationError` types
  as metrics and wires the retry loop or Instructor.
- **Architect:** Sets the rule that no model output crosses a service boundary unvalidated, chooses
  provider-native versus local constrained decoding, and versions the contracts between services.
- **FDE:** Maps the customer's record formats and real enum values into schemas, and tunes retries
  against their latency budget using failure rates from their own traffic.

## Check yourself
**Q:** An extraction pipeline returns invalid JSON 2% of the time and valid JSON with wrong values
(future dates, totals that do not add up) 7% of the time. Which tool fixes which, and why?
**A:** These are two failure classes. The 2% is syntactic: switch on provider structured outputs or
Outlines so the shape is guaranteed at decode time, and check `stop_reason` because some of that 2% is
probably truncation. The 7% is semantic and no decoder can fix it: encode the rules as Pydantic
validators (date not after today, line items summing to total) and run a retry loop that feeds the exact
error back, which is what Instructor does. Measure how many converge after one retry and send the rest
to a review queue. A high semantic failure rate also points at missing context, such as today's date not
being in the prompt, so fix the prompt before raising retry limits, and track failures per field.

## Quiz
### Q: What does constrained decoding with Outlines actually guarantee?
- [ ] That every value in the output is factually correct
- [x] That the generated tokens match the schema or grammar, with no guarantee about the truth of the values
- [ ] That it works with any hosted API without provider support
- [ ] That generation is always faster than unconstrained decoding
> The automaton masks tokens that would leave a valid state, so syntax and structure are guaranteed by
> construction. Whether the order id exists or the date is plausible is a semantic question that only
> validation code can answer.

### Q: What does Instructor do when the parsed response fails Pydantic validation?
- [ ] Lowers the temperature and resends the identical prompt
- [ ] Truncates the response until it validates
- [x] Resends the request with the validation error messages so the model can correct the specific fields
- [ ] Automatically switches to a larger model
> The retry carries the failed attempt and the exact error text, which is why most retries converge in
> one round; an identical resend would mostly reproduce the same mistake.

### Q: Which failure needs validation code, rather than the shape schema a provider or constrained decoder enforces?
- [ ] A missing required key
- [ ] A string where an integer was expected
- [x] A cross-field rule such as "a denied refund must have an amount of zero"
- [ ] An enum value outside the allowed set
> Keys, types and enums are structural and a schema or constrained decoder enforces them. A relationship
> between two fields is semantic, lives in a `model_validator`, and when it fails the remedy is a retry
> with the error message.
