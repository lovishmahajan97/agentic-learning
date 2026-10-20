---
id: instructor
module: python-for-ai
name: Instructor
kind: tool
difficulty: core
est_minutes: 14
related:
  - python-for-ai/pydantic
  - python-for-ai/outlines
  - python-for-ai/tenacity
  - python-for-ai/openai-sdk
  - python-for-ai/anthropic-sdk
  - prompting/structured-outputs-json-mode
  - prompting/function-tool-calling
  - serving-inference/structured-constrained-generation
sources:
  - Official docs: Instructor
  - Official docs: Pydantic
---

## TL;DR
Instructor wraps an LLM provider client so that a call takes a Pydantic model as `response_model`
and returns a validated instance of it instead of raw text. Under the hood it turns the model into a
tool or JSON schema, parses the reply, and when validation fails it sends the error back to the LLM
and retries. It exists because "ask for JSON and `json.loads` it" breaks in production, and the fix is
the same loop in every codebase.

## Why it exists
Most LLM features in real systems are extraction or classification: pull an invoice's line items,
label a ticket's intent, turn an email into a calendar event. The downstream code wants a typed object,
not prose. The naive approach, a prompt that says "respond in JSON with fields a, b, c" followed by
`json.loads`, fails in several distinct ways: the model wraps the JSON in a Markdown fence or adds a
preamble; a field comes back as `"twelve"` instead of `12`; an enum value is invented; a required field
is missing; a date is in the wrong format; or the JSON is truncated because the output hit the token
limit. Each failure needs its own handling, and teams end up writing the same bundle by hand: build a
schema from the type, put it in the request, strip fences, parse, validate, and if it fails, tell the
model what was wrong and try again.

Provider-native structured output (JSON mode, strict schemas, forced tool calls) closes part of this gap,
because it can guarantee syntactically valid JSON that matches the schema's shape. It cannot enforce
*business* rules: that `end_date >= start_date`, that a cited quote actually appears in the source, that
line items sum to the total. Those live in Pydantic validators, and only a validate-then-retry loop can
use them to correct the model. Instructor is that loop, packaged once, across many providers.

## How it works
### Patching the client
You wrap an existing SDK client, for example `instructor.from_openai(OpenAI())` or
`instructor.from_anthropic(Anthropic())` (recent versions also offer a `from_provider("openai/...")`
shortcut). The wrapped client keeps the provider's normal call signature and adds a few keyword
arguments, chiefly `response_model` and `max_retries`. It wraps rather than replaces the SDK *because*
the SDK already handles auth, HTTP, streaming and provider quirks; Instructor only owns the schema and
validation layer.

### Schema in, object out
1. **Schema generation.** Pydantic's `model_json_schema()` turns your `BaseModel` into JSON Schema,
   including field descriptions, enums from `Literal` or `Enum`, and nested models. Field docstrings and
   `Field(description=...)` become prompt text the model actually reads, which is why good descriptions
   measurably matter.
2. **Mode selection.** The `mode` decides how the schema reaches the model. `Mode.TOOLS` (the usual
   default for OpenAI) sends it as a single tool definition and forces that tool to be called; there are
   provider-specific tool modes for Anthropic and others; `Mode.JSON` uses the provider's JSON mode; and
   `Mode.MD_JSON` asks for JSON in a Markdown block for models with no native support. Tool calling is
   preferred where available *because* providers train models to emit well-formed arguments for tools.
3. **Parse and validate.** The reply's arguments are parsed into your model with
   `model_validate_json`, which runs type coercion and every `field_validator` and `model_validator`.
4. **Reask.** If validation raises, Instructor appends the model's previous output and the
   `ValidationError` text to the conversation as a new message and calls again. The error message is
   the correction signal: a validator that raises `ValueError("end_date is before start_date")` tells
   the model precisely what to fix. Retries are driven by Tenacity, so `max_retries` accepts an integer
   or a full Tenacity retry policy. After the last attempt it raises an `InstructorRetryException` that
   carries the attempts made.

### Beyond a single object
`create_partial` streams a model whose fields fill in as tokens arrive, useful for UIs that render a
form progressively. `create_iterable` extracts a stream of objects (every person mentioned in a
document). `create_with_completion` returns both the parsed object and the raw provider response, which
you need for token usage and logging.

### What it is not
Instructor does not constrain decoding. The model can still emit anything; Instructor detects bad output
after the fact and pays for another call. Libraries such as Outlines mask tokens during generation so
invalid output is impossible, but that requires access to the logits, which hosted APIs do not expose.

## Example
Extracting a meeting from an email, with a business rule the schema alone cannot express.

```python
from datetime import date
from typing import Literal
import instructor
from openai import OpenAI
from pydantic import BaseModel, Field, model_validator

class Meeting(BaseModel):
    title: str = Field(description="Short title, max 8 words")
    start: date
    end: date
    kind: Literal["internal", "customer", "interview"]
    attendees: list[str] = Field(description="Email addresses only")

    @model_validator(mode="after")
    def end_not_before_start(self):
        if self.end < self.start:
            raise ValueError(f"end {self.end} is before start {self.start}; re-read the dates")
        return self

client = instructor.from_openai(OpenAI())

meeting, raw = client.chat.completions.create_with_completion(
    model="gpt-4o-mini",                 # any tool-calling model
    response_model=Meeting,
    max_retries=2,                       # up to 2 corrective reasks after the first try
    messages=[{"role": "user", "content":
        "Offsite with Acme from 3 to 5 March 2025. Invite ana@acme.com and raj@us.io."}],
)
print(meeting.model_dump())
print(raw.usage.total_tokens)            # bill every attempt, not only the last
```

If the first reply sets `end` before `start`, the validator's message goes back to the model and the
second call usually fixes it; if both retries fail, the exception tells you which validation kept failing.

## Trade-offs
- One `response_model` argument replaces schema, parsing and retry glue, but you inherit a dependency
  whose mode defaults and provider adapters change between releases; pin it and test upgrades.
- Validate-and-reask enforces arbitrary Python rules, at the cost of extra full LLM calls; when failures
  are mostly syntactic, native strict structured output removes them for free and the reask loop is idle.
- Running on hosted APIs means no logit access is needed, but also no guarantee; if you self-host and
  need validity on the first pass every time, constrained decoding (Outlines, vLLM guided decoding) wins.
- Rich Pydantic schemas improve extraction quality, but deep nesting and long descriptions add input
  tokens on every call; for high-volume classification, a flat model with a `Literal` field is cheaper.

## Pitfalls
- Leaving `max_retries` high with no budget: one pathological input triggers several full-price calls;
  the symptom is a cost spike traced to a handful of requests with repeated identical errors.
- Validators that raise vague messages like "invalid"; the model cannot act on them, and the symptom is
  retries that return the same wrong value each time.
- Counting tokens only from the final response; usage from failed attempts is invisible unless you log
  each attempt, so cost dashboards under-report against the provider invoice.
- Placing the answer field before a reasoning field in the model; field order is generation order, so
  the answer is committed before the reasoning exists and accuracy drops on harder cases.
- Treating a validated object as true; validation proves the shape and your rules, not that the
  extracted facts are in the source, which shows up as confident, well-typed hallucinations in evals.

## When to use
Reach for Instructor when you call hosted LLMs and need typed objects with rules beyond what a JSON
schema can say, especially across more than one provider. If your only need is valid JSON of a fixed
shape from one provider, the SDK's native structured output (for example OpenAI's `parse` helper) may
be enough without another dependency. If you self-host and need hard guarantees or very low latency,
prefer constrained decoding. Measure first-pass validation rate, retries per request and cost per
successful extraction; a falling first-pass rate is an early signal of prompt or model drift. In
interviews, expect to explain why validation-plus-reask and constrained decoding solve different halves
of the structured-output problem.

## Role lens
- **Engineer:** Writes the Pydantic models, validators with actionable error messages, and retry limits,
  and logs every attempt with its token usage.
- **Architect:** Decides where structure is enforced: provider strict mode, reask loops or constrained
  decoding, and sets the retry and cost budget per feature.
- **FDE:** Turns a customer's messy documents into a typed schema quickly, using validators to encode
  their business rules so extraction errors surface as explicit failures.

## Check yourself
**Q:** Your extraction pipeline uses provider strict JSON mode and still produces wrong records. A
colleague says adding Instructor's retries is pointless because the JSON is already valid. Are they right?
**A:** Partly. Strict mode guarantees syntax and schema shape, so Instructor's reask will never fire on
those failures. But the wrong records are semantic: dates swapped, totals not matching line items,
values not present in the source. Those can be expressed as Pydantic validators, and Instructor feeds
each validator's message back so the model can correct itself. The trade is extra calls per failure, so
you would cap retries, measure first-pass and post-retry accuracy on a labelled set, and keep a
human-review path for records that still fail validation.

## Quiz
### Q: What does Instructor do when the model's output fails Pydantic validation?
- [ ] It masks invalid tokens and resamples only the failing field
- [x] It sends the validation error back to the model in a new message and calls again, up to max_retries
- [ ] It silently fills missing fields with their Pydantic defaults and returns the object
- [ ] It switches automatically to a larger model for the next attempt
> Instructor works on hosted APIs without logit access, so it cannot mask tokens. It reasks: the
> ValidationError text becomes a corrective message, and each retry is a full new LLM call.

### Q: Why is the tool-calling mode usually preferred over plain JSON-in-prompt mode?
- [ ] Tool calls are billed at a lower token rate than normal completions
- [ ] Tool calls let Instructor skip Pydantic validation entirely
- [x] Providers train models to emit well-formed arguments for declared tools, so first-pass validity is higher
- [ ] JSON-in-prompt mode cannot represent nested models
> Tool modes send the schema as a tool definition the provider is trained to honour, which reduces
> malformed output. Validation still runs afterwards; nothing about billing or nesting differs.

### Q: Which requirement can Instructor enforce but provider strict schema mode alone cannot?
- [ ] That a field is an integer rather than a string
- [ ] That an enum field only takes listed values
- [x] That the sum of extracted line items equals the stated invoice total
- [ ] That every required field is present
> Types, enums and required fields are expressible in JSON Schema and strict mode enforces them.
> A cross-field arithmetic rule needs code, which lives in a Pydantic validator that drives the reask.
