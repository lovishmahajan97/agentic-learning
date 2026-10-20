---
id: tool-schemas-and-argument-validation
module: agents
name: Tool schemas & argument validation
kind: practice
difficulty: core
est_minutes: 13
related:
  - agents/tool-calling
  - agents/typed-tool-contracts
  - agents/tool-selection-accuracy
  - python-for-ai/pydantic
  - prompting/structured-outputs-json-mode
  - serving-inference/structured-constrained-generation
  - safety-security/output-validation
  - agent-reliability/tool-call-success-rate
sources:
  - Official docs: Anthropic Claude API tool use
  - Official docs: Pydantic
---

## TL;DR
The input schema is the model's entire specification of a tool's arguments, and validation at the
harness boundary is where the model's mistakes are caught before they become side effects. Design
schemas small, flat and explicit (types, enums, required fields, descriptions that state units and
formats); validate in layers, shape, then meaning, then permission; and send failures back as error
results the model can act on, because a model that sees "amount must be in cents" fixes itself, while
a 500 from your handler ends the run.

## Why it exists
Model-generated arguments are plausible, not correct. They fail in three distinct ways. Shape errors:
a string where an integer was expected, a missing required field, an invented field. Semantic errors:
a date in the past for a future booking, an order id that does not exist, a quantity of 1000 where 10
was meant. Authorization errors: a perfectly well-formed request for a record the current user may not
see. Constrained decoding ("strict" schema mode) eliminates the first class on providers that offer it,
and does nothing for the other two.

The naive approach, pass `input` straight to the downstream API, produces a 400 at best and a wrong
write at worst, and either way the exception kills the loop before the model gets a chance to correct
itself. The second naive approach, validate and crash on failure, is only slightly better. What the
model needs is the same thing a junior engineer needs from a code review: a specific message about what
was wrong and what would be right.

The schema is also documentation the model reads. A parameter named `date` typed as a bare string
invites "next Friday"; the same parameter described as "ISO 8601 date, e.g. 2026-11-03" gets the right
value from the same model. Many "the model passes bad arguments" complaints are schema bugs.

## How it works
### What the model sees
The declared `input_schema` is a JSON Schema object: `type`, `properties`, `required`, `enum`,
`description` per property, `default`, `items` for arrays, nested objects, and `additionalProperties`.
The model reads every description. Put units, formats and examples there: "amount in minor currency
units (cents)", "ISO 4217 code", "max 50 results". `additionalProperties: false` tells the model not to
invent fields and is required by strict mode.

### Design rules, and the reason for each
- **Flat over nested**, because every level of nesting is another place to misplace a key.
- **Enums over free strings** for anything with a fixed set of values, because they turn a
  classification into a choice the API can enforce.
- **Few parameters with sensible defaults**, because every optional parameter is a decision the model
  can get wrong; expose only what the task needs, not the downstream API's whole surface.
- **Units and formats in names and descriptions** (`total_cents`, `starts_at_utc`), because "amount"
  can mean cents or dollars and the model will pick one.
- **Avoid unions, recursion and deep polymorphism**, because models fill them poorly and strict modes
  typically do not support recursion or numeric and length constraints at all.

### Where the shape guarantee comes from
Without strict mode the model generates JSON as text and the API parses it; wrong types and missing
fields are possible. With strict mode the provider compiles the schema into a grammar and masks tokens
during decoding so the output must conform (constrained generation; open-weight servers do the same with
libraries like Outlines). You pay with a restricted schema vocabulary and a one-time compilation cost
for new schemas. Strict is worth it for write tools; it is unnecessary for a single free-text query
parameter. Treat availability and the exact supported subset as vendor-dependent.

### Validation layers in the harness
1. **Shape**: parse `input` into a typed model (Pydantic, Zod). Do this even with strict mode, because
   a streamed input cut off at `max_tokens` can parse as a valid but incomplete object, and because the
   model you run next year may not have strict mode.
2. **Meaning**: ranges, cross-field rules, existence checks against your data ("order not found").
3. **Permission**: tenant and user scoping come from the authenticated session in the harness, never
   from an argument the model supplied, because the model will cheerfully pass whatever id appears in
   the conversation.
4. **Consequence**: destructive or expensive calls get an approval gate regardless of validation.

A failure at any layer returns `is_error: true` with a message naming the field, the problem and the
fix, and nothing else (no stack traces, no table names). Cap validation retries per call, two or three
is typical, so a model that cannot satisfy the schema does not loop forever.

## Example
A Pydantic model is the single source for the schema the model sees and the validator the handler runs.
The regex and bounds are shape rules; the existence check is a semantic rule; the tenant check is an
authorization rule that uses the session, not the model's argument. Errors are turned into results.

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ORDERS = {"ORD-104233": {"tenant": "acme", "paid_cents": 12_900}}

class RefundInput(BaseModel):
    model_config = ConfigDict(extra="forbid")            # -> additionalProperties: false
    order_id: str = Field(pattern=r"^ORD-\d{6}$", description="Order id, format ORD-123456")
    amount_cents: int = Field(gt=0, description="Refund amount in cents (minor units)")
    reason: Literal["damaged", "late", "wrong_item", "other"]

    @field_validator("order_id")
    @classmethod
    def must_exist(cls, v: str) -> str:                   # semantic layer: hits data, not just regex
        if v not in ORDERS:
            raise ValueError(f"order {v} not found; ids look like ORD-123456")
        return v

REFUND_TOOL = {"name": "issue_refund",
               "description": "Refund part or all of a paid order. Call only after the user "
                              "has confirmed the order id and amount.",
               "input_schema": RefundInput.model_json_schema()}
# If you enable strict mode, strip constraints the provider does not support (pattern, gt)
# from the sent schema and keep enforcing them here; SDK helpers do this automatically.

def handle_refund(block, session_tenant: str) -> dict:
    try:
        args = RefundInput.model_validate(block.input)
    except ValidationError as e:
        msg = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                "content": f"invalid arguments: {msg}"}
    order = ORDERS[args.order_id]
    if order["tenant"] != session_tenant:                 # authorization from the session
        return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                "content": "order not accessible from this account"}
    if args.amount_cents > order["paid_cents"]:           # cross-field semantic rule
        return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                "content": f"amount_cents exceeds paid amount {order['paid_cents']}"}
    return {"type": "tool_result", "tool_use_id": block.id,
            "content": f'{{"status": "refunded", "amount_cents": {args.amount_cents}}}'}
```

## Trade-offs
- Strict mode gives a hard shape guarantee but limits the schema vocabulary and adds first-call
  compilation latency; it pays for itself on write tools and is overkill for a one-string search tool.
- Tight validation catches more errors, but each rejection costs a model round trip; for low-stakes
  arguments it is cheaper to coerce (trim whitespace, upper-case a currency code) than to reject.
- Fewer, flatter parameters make calls reliable but push complexity into more tools or into the
  handler; the balance flips when the domain genuinely needs a structured payload such as an address.
- Detailed error messages help the model self-correct but can leak internals; return what the model
  needs to fix the call, not what a developer needs to debug the service.

## Pitfalls
- Generating the schema from an internal database model with thirty fields; the symptom is the model
  filling optional fields with invented values or omitting the ones that matter.
- A bare `{"type": "string"}` for dates, money or ids with no format in the description; the symptom
  is "next Friday", "$12.90" and ids with the wrong prefix arriving in the handler.
- Taking `tenant_id` or `user_id` from the model's arguments; the symptom is cross-tenant data access
  that only shows up in a security review or an incident.
- Validating shape only; the symptom is downstream 400s and refunds that are off by a factor of 100
  because cents and dollars were confused.
- Unbounded retry on validation failure; the symptom is the same call rejected twenty times with the
  same message until the step budget trips.
- Reusing one schema across providers with different strict-mode subsets; the symptom is a 400 from one
  vendor about an unsupported keyword such as `pattern` or `minimum`.

## When to use
Always validate; what varies is how deep. A read-only search tool needs shape validation and little
else. A tool that moves money, deletes data or sends messages needs all four layers plus an approval
gate. Measure the argument validity rate (calls that pass shape validation), the semantic rejection
rate, and how many retries a rejected call needs; a rising rejection rate after a prompt or model change
usually means a description became ambiguous. In day-to-day work this is where most tool bugs live, so
keep the schema and the validator in one place and test them with the model's actual outputs from
traces, not hand-written fixtures. In interviews, be ready to explain exactly what strict mode does and
does not guarantee, and why authorization can never come from a model argument.

## Role lens
- **Engineer:** Defines each tool's input as a typed model, derives the JSON Schema from it, validates
  every call in layers, and returns specific `is_error` results instead of raising.
- **Architect:** Sets the rule that identity and tenant scope come from the session, decides which
  tools require strict schemas and approval gates, and standardizes the error format across teams.
- **FDE:** Inspects a customer's failing tool calls in traces, usually finds an ambiguous schema or a
  missing unit, and fixes the description before touching the prompt or the model.

## Check yourself
**Q:** You enable strict schema mode and argument validation errors drop to zero, yet refund amounts
are occasionally a hundred times too large. What happened, and what do you change?
**A:** Strict mode guarantees the arguments match the schema's shape: `amount` is an integer, every
required field is present. It cannot know that the integer was meant in dollars while the handler reads
cents, so the model sometimes sends 129 for a 129-dollar refund and sometimes 12900. The fix is in the
contract and the handler, not the decoder: rename the field to `amount_cents` and say "minor units" in
its description; add a semantic check that the amount does not exceed what the order paid; gate refunds
above a threshold behind human approval; and return a specific error when the check fails so the model
corrects itself. Then watch the distribution of amounts in traces to confirm the fix.

## Quiz
### Q: What does strict or constrained schema mode guarantee about a tool call's arguments?
- [ ] That the values are correct for the task
- [x] That the arguments conform to the declared schema's shape and types
- [ ] That the referenced records exist
- [ ] That the caller is authorized to use them
> Constrained decoding enforces the grammar of the output, so types and required fields are right by
> construction. Existence, meaning and permission are properties of your data and your session, which
> only the harness can check.

### Q: A parameter has exactly five valid values and the model keeps sending variants. Which change fixes it most directly?
- [ ] Increase the model's output token limit
- [ ] Add the five values to the system prompt
- [x] Declare the parameter as an enum of the five values in the schema
- [ ] Lower the temperature
> An enum turns a free-text guess into a choice the API validates, and strict modes enforce it during
> decoding. A prompt mention helps less because it is not attached to the parameter and is not enforced.

### Q: Where should the tenant scope for a tool call come from?
- [ ] A required tenant_id parameter the model fills in
- [ ] The tool description, which tells the model the current tenant
- [x] The authenticated session in the harness, applied regardless of the model's arguments
- [ ] The most recent tool result that mentioned a tenant
> The model will pass any id that appears in its context, including one injected by a document or a
> user. Authorization must be derived from who is actually calling, which only the harness knows.
