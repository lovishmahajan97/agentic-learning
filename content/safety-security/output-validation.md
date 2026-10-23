---
id: output-validation
module: safety-security
name: Output validation (Pydantic, Instructor, Outlines)
kind: practice
difficulty: core
est_minutes: 16
status: mastery-verified
agent_relevance: core
mastery: implement
code_tested: partial
prerequisites:
  - python-for-ai/pydantic
  - prompting/structured-outputs-json-mode
  - prompting/function-tool-calling
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
Every tool call, plan and handoff your agent emits is a string until proven otherwise. Pydantic defines
the contract, Instructor retries with the error fed back, and Outlines constrains decoding. Shape can be
guaranteed; meaning must be checked.

## For agent builders
- **Why it matters for agents.** Tool arguments, plans, routing decisions and sub-agent handoffs are all
  model output that code acts on. One unvalidated argument can write the wrong row, call the wrong
  account or crash the loop at step 14.
- **Where it shows up in your stack.** Tool input schemas sent to the provider, Pydantic models in the
  tool executor, Instructor `response_model=` for extraction steps, provider structured outputs, and
  Outlines or XGrammar when you self-host with vLLM.
- **What you control.** The schema (closed enums, `null` or `unknown` options, field order), strict
  versus lax coercion, how a validation error is returned to the model as a tool error, the retry budget,
  and where a failing step goes after retries.
- **What you can skip.** Building FSM token indexes yourself; know that constrained decoding guarantees
  shape and nothing more, and spend your effort on semantic validators and reference-data checks.

## Why it exists
- **Downstream code is typed; free text is not.** The failures are mundane and constant: JSON wrapped in
  markdown fences, a helpful sentence after the closing brace, `"42"` where an integer was required, an
  invented enum value, a missing required field, an extra field nobody asked for, and JSON cut off
  mid-string because `max_tokens` was reached.
- **The naive fix is dangerous.** `json.loads` inside a try/except either crashes the request or, worse,
  lets a wrong-but-parseable object through to a database write.
- **Model output is untrusted input.** It feeds SQL builders, shell commands, tool arguments and HTML
  templates. You would never pass a web form's fields straight into those, and a model is less
  predictable than a browser.
- **Validity has layers, so three tools exist.** *Syntactic*: the text parses. *Structural*: the right
  keys and types are present. *Semantic*: the values make sense (the date is not in the future, the order
  id exists, the line items sum to the total).
- **Only domain code checks meaning.** Constrained decoding can guarantee the first two at generation
  time; when a semantic check fails the only remedy is to ask the model again with a precise error.

## How it works
### The three layers and who enforces them
| Layer | Example failure | Enforced by |
|---|---|---|
| Syntactic | trailing prose, truncated string | constrained decoding, provider structured outputs |
| Structural | missing key, wrong type, unknown enum | schema (provider, Outlines) and Pydantic |
| Semantic | future date, totals do not add up | Pydantic validators plus retry (Instructor) |

### Pydantic: the contract
- **Declare fields and types** with a `BaseModel`; `Field` adds bounds (`ge=0`), lengths and regex
  patterns; `Literal` closes an enum.
- **Rules that need code** go in `field_validator` and `model_validator`, including cross-field rules.
- **Parse straight from text** with `model_validate_json`. In Pydantic v2 the core is compiled, so
  validation cost is negligible next to a model call.
- **Errors are the asset.** On failure it raises `ValidationError`, whose `.errors()` is a
  machine-readable list with the failing location, message and type, specific enough to feed back to a
  model.
- **One definition, three uses.** `model_json_schema()` emits JSON Schema, so the same model serves the
  provider, the parser and the documentation.

### Instructor: validation in the loop
1. **Patch a provider client** (`instructor.from_anthropic`, `instructor.from_openai`, ...) so the call
   accepts `response_model=YourModel` and `max_retries=N`.
2. **Derive the schema** and request structured output through the provider's tool-call or JSON mode. A
   `mode` setting chooses which, since providers differ in what they support.
3. **Parse the reply with Pydantic.**
4. **On `ValidationError`,** append the failed attempt plus a user message containing the error text, and
   ask again.

- **Why it works:** models are good at fixing a named error in a named field; most retries converge in
  one round.
- **The cost:** extra calls on failure. A high retry rate is a signal to fix the prompt or schema, not to
  raise `max_retries`.
- **Semantic rules become retryable:** any Python assertion in a validator becomes an instruction the
  model can act on.

### Outlines: constraints inside decoding
1. **Compile the constraint.** A regex, or a JSON schema first converted to a regex, becomes a
   finite-state machine (FSM) over characters.
2. **Bridge characters and tokens.** The model emits *tokens*, and one token can span several characters
   or cross a field boundary. So Outlines builds an index ahead of time: for every FSM state, the set of
   vocabulary tokens whose whole string walks the FSM to another valid state.
3. **Mask at each step.** Look up the current state, set the logits of every token outside that set to
   −∞, and sample from what remains.

- **Concretely:** for the regex `\d{3}` after the model has emitted `4`, the tokens `2` and `27` are
  allowed, but `273` (one digit too many) and `a` are masked.
- **No retries, cheap per step.** The output matches by construction, and each step is a lookup *because*
  the expensive work was done once per schema and cached; that precomputation is the contribution of
  Willard and Louf.
- **Regular languages cannot nest without bound.** Recursive schemas are bounded to a fixed depth; full
  context-free grammars need a pushdown automaton or incremental parser, which engines such as XGrammar
  provide.
- **Requires logit access:** local or open-weight models (Transformers, vLLM integrations). Hosted APIs
  offer the same idea as provider-operated "structured outputs" or strict schemas.

Two limits matter:
- **Syntax, not truth.** A model forced to fill a field it does not know fills it with something
  plausible, so allow `null` or an `"unknown"` enum where ignorance is a legitimate answer.
- **Tight grammars can steer into low-probability continuations,** so schema design (field order,
  descriptions, optionality) still affects quality. Field order matters *because* decoding is left to
  right: `reason` before `decision` lets the model justify and then commit; the reverse makes it commit
  first and rationalise afterwards.

### Provider-native structured outputs
- **Vendor-run constrained decoding.** Most major APIs accept a JSON schema and constrain generation
  server-side.
- **Keyword support varies.** Regex patterns, numeric bounds and string lengths are often not enforced, so
  treat the provider as guaranteeing *shape* and keep richer rules in Pydantic.
- **Check `stop_reason`:** a schema-valid prefix truncated by `max_tokens` is still invalid JSON.

### Putting the layers together
1. Define the model once in Pydantic.
2. Derive the shape schema for the provider or Outlines.
3. Parse the result with Pydantic so semantic validators run.
4. On `ValidationError`, retry with the error text (Instructor, or ten lines of your own).
5. After N failures, route to a fallback or a review queue rather than guessing.

### Inside an agent loop
- **Validate in the tool executor, not in the tool.** Parse every tool call's arguments into a Pydantic
  model before running anything, *because* the executor is the one chokepoint every call passes through.
- **Return the error as a tool result.** Instead of raising, send the `ValidationError` text back as an
  error tool result; the model reads it on the next step and repairs the call, so the retry loop is the
  agent loop itself.
- **Count consecutive repair attempts per tool.** Cap them (one or two), then stop the run or escalate,
  so a model that cannot form valid arguments does not burn the step budget.

## Diagram
Shape is enforced during generation; meaning is enforced by Pydantic afterwards, and only semantic
failures loop back with the error text.

```mermaid
flowchart TD
    P["Pydantic model"] -->|"model_json_schema"| S["Shape schema"]
    S --> G["Constrained generation"]
    G --> T{"stop_reason is max_tokens?"}
    T -->|"yes"| X["Raise budget, do not retry"]
    T -->|"no"| V["Pydantic validate: types and rules"]
    V -->|"valid"| OK["Typed object to code"]
    V -->|"ValidationError"| R{"Retries left?"}
    R -->|"yes, send error text"| G
    R -->|"no"| Q["Fallback or review queue"]
```

## Examples
### One contract, three jobs
Its shape is sent to the provider so the JSON is structurally valid by construction, its validators
enforce domain rules the provider cannot, and its error messages drive a retry loop, which is exactly
what Instructor packages behind `response_model=` and `max_retries=`.

```python
from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
import anthropic                     # checked against anthropic 2.1.0 and pydantic 2.14 (HTTP mocked)

class RefundDecision(BaseModel):
    order_id: str = Field(pattern=r"^ORD-\d{6}$")
    order_date: date
    reason: str = Field(min_length=10, max_length=300)   # before the verdict: justify, then commit
    decision: Literal["approve", "deny", "escalate"]
    amount_cents: int = Field(ge=0)

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
         "required": ["order_id", "order_date", "reason", "decision", "amount_cents"],
         "properties": {"order_id": {"type": "string"}, "order_date": {"type": "string"}, "reason": {"type": "string"},
                        "decision": {"type": "string", "enum": ["approve", "deny", "escalate"]},
                        "amount_cents": {"type": "integer"}}}
client = anthropic.Anthropic()

def decide(ticket: str, max_retries: int = 2) -> RefundDecision:
    messages = [{"role": "user", "content": f"Today is {date.today()}. Decide the refund for:\n{ticket}"}]
    for _ in range(max_retries + 1):
        r = client.messages.create(model="claude-opus-5-5", max_tokens=1024, messages=messages,
                                   output_config={"format": {"type": "json_schema", "schema": SHAPE}})
        if r.stop_reason == "max_tokens":
            raise RuntimeError("truncated output: raise max_tokens, retrying will not help")
        text = next(b.text for b in r.content if b.type == "text")       # parses and has the right keys
        try:
            return RefundDecision.model_validate_json(text)                # semantic rules run here
        except ValidationError as e:                                       # the loop Instructor automates
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"Fix these validation errors and return the full object:\n{e}"}]
    raise RuntimeError("no valid decision after retries; route to review")

print(decide("ORD-104233, placed 2026-09-30, blender arrived cracked, paid $89.99.").model_dump())
# A failure this loop repairs: {"decision": "deny", "amount_cents": 8999, ...} passes the provider's
# shape check, then model_validator raises "a denied refund must have amount_cents == 0"; the retry
# carries that exact sentence and the model returns either approve/8999 or deny/0.
```

### The same call with Instructor
Instructor replaces the hand-written loop: pass the Pydantic class as `response_model` and a retry count.

```python
import anthropic, instructor         # checked against instructor 1.17.0 (sends the schema as a tool)

client = instructor.from_anthropic(anthropic.Anthropic())

decision = client.messages.create(
    model="claude-opus-5-5",
    max_tokens=1024,
    response_model=RefundDecision,   # the contract above, validators included
    max_retries=2,                   # each retry carries the ValidationError text
    messages=[{"role": "user", "content": "Decide the refund for: ORD-104233, blender arrived cracked."}],
)
print(decision.decision, decision.amount_cents)   # a typed RefundDecision, not a dict
```

### Lax vs strict coercion (pure Pydantic, no model call)
Lax mode quietly turns `"8999"` into `8999`; strict mode reports it, which matters for money and ids.

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

class Lax(BaseModel):
    amount_cents: int = Field(ge=0)
    decision: Literal["approve", "deny", "escalate"]

class Strict(Lax):
    model_config = ConfigDict(strict=True)

print(Lax.model_validate({"amount_cents": "8999", "decision": "deny"}).amount_cents)  # 8999, coerced
try:
    Strict.model_validate({"amount_cents": "8999", "decision": "refund"})
except ValidationError as e:
    for err in e.errors():                 # machine-readable: loc, type, msg
        print(err["loc"], err["type"])     # ('amount_cents',) int_type / ('decision',) literal_error
```

### Agent scenario: validating tool arguments in the executor
The agent proposes `issue_refund`; the executor validates before acting and returns errors as tool
results, so the model repairs its own call on the next step.

```python
from pydantic import BaseModel, Field, ValidationError

class IssueRefundArgs(BaseModel):
    order_id: str = Field(pattern=r"^ORD-\d{6}$")
    amount_cents: int = Field(ge=0, le=50_000)       # hard cap: larger refunds need a human
    reason: str = Field(min_length=10, max_length=300)

TOOLS = {"issue_refund": (IssueRefundArgs, lambda a: f"refunded {a.amount_cents} on {a.order_id}")}
MAX_REPAIRS = 2

def execute(tool_use, repairs: dict) -> dict:
    """Turn one tool_use block into a tool_result block; never run unvalidated arguments."""
    name, tid = tool_use["name"], tool_use["id"]
    if name not in TOOLS:                              # a hallucinated tool name is also repairable
        return {"type": "tool_result", "tool_use_id": tid, "is_error": True,
                "content": f"Unknown tool {name!r}; available: {sorted(TOOLS)}"}
    model, fn = TOOLS[name]
    try:
        args = model.model_validate(tool_use["input"])
    except ValidationError as e:
        repairs[name] = repairs.get(name, 0) + 1       # consecutive failures for this tool in this run
        if repairs[name] > MAX_REPAIRS:
            raise RuntimeError("tool arguments still invalid after repairs; escalate the run")
        return {"type": "tool_result", "tool_use_id": tid, "is_error": True,
                "content": f"Invalid arguments, fix and call again:\n{e}"}
    repairs.pop(name, None)                            # a valid call resets the repair count
    return {"type": "tool_result", "tool_use_id": tid, "content": fn(args)}

repairs = {}                                           # one dict per agent run
bad = {"id": "t1", "name": "issue_refund", "input": {"order_id": "104233", "amount_cents": "8999"}}
print(execute(bad, repairs)["content"][:80])   # pattern and missing-field errors go back to the model
```

### Worked scenario: reading the three metrics
Illustrative numbers from a week of an extraction pipeline, and what each one says to change.

| Metric | Value | Diagnosis | Fix |
|---|---|---|---|
| Truncation rate | 1.5% of calls | `max_tokens` too low for long documents | raise budget, check `stop_reason` |
| First-pass validation failures | 6% | mostly `order_date` in the future | put today's date in the prompt |
| Still failing after one retry | 0.4% | genuinely ambiguous tickets | send to the review queue |

## Production patterns
- **Validate at every service boundary.** Treat "no model output crosses a boundary unvalidated" as an
  architecture rule: every consumer parses into a versioned Pydantic model before acting. Monitor
  validation failure rate per field.
- **Export `ValidationError` as metrics.** Count `.errors()` entries by `loc` and `type` and alert on a
  jump, because a new failing field usually means prompt or model drift, not random noise.
- **Truncation guard before retry.** Check `stop_reason` first and treat `max_tokens` as a budget error
  that a retry will not fix. Monitor truncation rate by input length bucket.
- **Bounded retries, then a queue.** Keep `max_retries` small (one or two) and route the remainder to a
  fallback or human review; alert when retries per request climb, which points at the prompt or schema.
- **Tool errors as feedback, with a repair cap.** Agent teams validate every tool call in the executor,
  return the `ValidationError` text as an error tool result, and cap repairs per call. Monitor repair
  rate per tool and runs stopped for invalid arguments; a spike on one tool usually means its schema
  description drifted from the code.
- **Strict mode for money and identifiers.** Set strict validation on fields where a coerced value is a
  silent bug, and check ids against reference data in a separate step so validators stay cheap.

## Trade-offs
- **Constrained decoding vs hidden uncertainty.** It removes retries and guarantees shape, but needs
  logit access or provider support and can hide uncertainty behind a confident-looking value. This flips
  in your favour once the schema allows `null` or `unknown` wherever the model may legitimately not know.
- **Retry-on-error vs cost.** Simple and provider-agnostic, but worst-case cost and latency multiply by
  the retry count; past a few percent failure rate the fix is the prompt or schema, not more retries.
- **Strict schemas vs release churn.** Closed enums and no extra fields catch drift the day it starts, but
  every new domain category becomes a schema release; version the contract rather than loosening it.
  Loosen instead (an `other` value plus a free-text field) when categories churn faster than you release.
- **Lax coercion vs hidden errors.** Pydantic's default (`"42"` to `42`) is convenient and hides model
  errors; use strict mode where the type carries meaning, such as money or identifiers.
- **I/O in validators vs latency.** Database-backed validators make rules retryable but couple validation
  to I/O; keep in-validator checks cheap and run reference-data lookups as a separate step.

## Pitfalls
- **Checking only that the text parses.** Symptom: `KeyError` and `TypeError` deep in downstream code on
  fields that were present but of the wrong type or shape. Diagnose: group downstream exceptions by
  field. Fix: parse into Pydantic at the boundary.
- **Ignoring truncation.** Symptom: validation errors mentioning an unterminated string, because
  `max_tokens` cut the JSON. Diagnose: plot those errors against output length and `stop_reason`; they
  cluster on long outputs. Fix: check `stop_reason`, do not retry blindly.
- **Retrying with the identical prompt and no error feedback.** Symptom: the same failure three times in
  a row and a tripled bill. Diagnose: diff the retry request against the first; no error text means no
  feedback. Fix: append the `ValidationError` text.
- **Trusting unenforced schema keywords.** Patterns, bounds or lengths the provider's strict mode does not
  enforce arrive violated in a valid-looking object. Diagnose: re-validate a sample of provider outputs
  with Pydantic. Fix: keep those rules in validators.
- **Making every field `Optional` to reduce failures.** The model learns that `null` escapes validation.
  Diagnose: track null rate per field over time. Fix: make needed fields required, with an explicit
  `unknown` value.
- **Raising on bad tool arguments instead of returning an error.** Symptom: one malformed call kills a
  long run the model could have repaired. Fix: return the error as a tool result, within a repair cap.
- **Trusting well-formed but hallucinated identifiers.** An `order_id` that matches the regex but does not
  exist produces downstream 404s, so validate against reference data for anything used as a key.

## When to use
- **Use when:** a program consumes the output: extraction results, classifications, tool arguments,
  agent plans, routing decisions.
- **Always:** parse into Pydantic so semantic rules and typed errors exist; use provider structured
  outputs or Outlines whenever available to guarantee shape at no retry cost.
- **Add a retry loop** (Instructor-style) when validators can fail in ways the model can fix.
- **Skip the schema** for free-form chat text, which still needs guardrails.
- **Measure:** validation failure rate per field, retries per request and truncation rate; together they
  say whether the prompt, the schema or the token budget is the problem.
- **In agents:** validate every tool call in the executor before it runs, and treat plans and handoffs
  between sub-agents as typed contracts too.

## Exercise
**Goal:** build a validated extraction step that fails loudly, repairs what it can and tells you why the
rest failed. No solution is given.
- **Inputs:** 30 short invoices or tickets you write yourself, including 5 that tempt a future date, 5
  whose line items do not sum to the total and 3 long enough to truncate at a low `max_tokens`.
- **Deliver:** a Pydantic model with at least one `field_validator` and one `model_validator`, a retry
  loop (yours or Instructor) capped at 2, a truncation guard, and a table of failures by `loc` and `type`.
- **Acceptance:** a pytest suite feeds fixed bad JSON strings (no model call) and asserts each raises the
  expected error type; no truncated output is retried; every record either validates or lands in a
  review list with its last error.

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

## Interview questions
### What is the difference between JSON mode and validating with Pydantic?
- **JSON mode or structured outputs guarantee shape:** the text parses and has the right keys and types.
- **Pydantic checks meaning as well:** bounds, patterns, cross-field rules and custom validators, and it
  gives typed errors you can feed back to the model. You want both, because providers often do not
  enforce patterns or numeric bounds.

### Your retry rate jumped from 1% to 12% after a model upgrade. How do you debug it?
- **Break failures down by field and error type** from `ValidationError.errors()` to see whether one
  field or one rule dominates.
- **Check truncation first,** because a verbosity change can push outputs past `max_tokens`, which no
  retry fixes.
- **Then fix the prompt or schema** (descriptions, field order, an `unknown` option) rather than raising
  `max_retries`, and add the failing cases to the eval set.

### When would you not use constrained decoding?
- **When you cannot access logits** and the provider's schema support lacks the keywords you need.
- **When forcing a value would hide ignorance:** without a `null` or `unknown` option the model invents
  plausible values.
- **When a tight grammar hurts quality,** for example committing to a decision field before the reasoning
  field; reorder fields or loosen the grammar.

### Your agent sometimes calls a tool with malformed arguments. Where do you validate, and what happens next?
- **In the tool executor,** with a Pydantic model per tool, because every call passes through it and no
  argument should reach a side effect unparsed.
- **Return the error, do not crash:** send the `ValidationError` text back as an error tool result so the
  model repairs the call on its next step.
- **Bound it:** cap repairs per call, then stop or escalate, and track repair rate per tool to find
  schemas whose descriptions mislead the model.

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

## Mastery test
**Problem:** a planner agent emits a JSON plan of up to 12 steps, each naming a tool and its arguments,
which an orchestrator then runs. After a model upgrade, 3% of runs execute a step whose tool exists but
whose arguments belong to a different tool. Shape validation passes. Where is the gap and what do you
change?

**A strong answer:**
- sees the schema is too loose: arguments typed as a generic object, not tied to the named tool;
- switches to a discriminated union keyed on the tool name, so each step validates against that tool's
  own argument model, and re-validates in the executor before each step runs;
- returns errors to the planner with the step index, caps repairs, and breaks failures down by `loc`
  to confirm the fix;
- adds the failing plans to the eval set before the next model upgrade.

## Use cases & projects
### Use cases
- **Tool-calling agents with write tools.** Every refund, ticket update or database write is parsed into
  a typed argument model before it runs.
- **Extraction steps inside agent pipelines.** Instructor or provider structured outputs turn a document
  into a typed record the next step can trust.
- **Planner and router outputs.** A planner's step list or a router's choice is a closed enum or typed
  plan, so the orchestrator never acts on free text.
- **Multi-agent handoffs.** Sub-agents exchange versioned Pydantic contracts, so a drift in one agent's
  output fails loudly at the boundary.

### Mini project
**Self-repairing tool calls (1-2 hours).**
- **Goal:** make an agent recover from malformed tool arguments without crashing.
- **Steps:** define two tools with Pydantic argument models (one with a regex id, one with a cross-field
  rule); write an executor that returns `ValidationError` text as an error tool result with a repair cap
  of 2; run 20 tasks, including prompts that tempt bad arguments; log first-pass failures, repairs and
  escalations per tool.
- **Done when:** no invalid argument ever reaches a tool function, most bad calls are repaired within one
  retry and the rest escalate cleanly instead of crashing the run, and you have a per-tool table of
  failure type by `loc` and `type`.
