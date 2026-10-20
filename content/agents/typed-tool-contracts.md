---
id: typed-tool-contracts
module: agents
name: Typed tool contracts
kind: pattern
difficulty: core
est_minutes: 12
status: fact-checked
related:
  - agents/tool-schemas-and-argument-validation
  - agents/tool-result-interpretation
  - agents/model-context-protocol
  - python-for-ai/pydantic
  - python-for-ai/contract-testing
  - agent-reliability/tool-adapters-service-interfaces
  - agent-reliability/idempotent-tool-execution
  - backend-fundamentals/api-versioning-and-backward-compatibility
sources:
  - Official docs: Pydantic
  - Official docs: Anthropic Claude API tool use
---

## TL;DR
A typed tool contract defines a tool once, as types: an input model, an output model, an enumerated set
of error kinds, and metadata about side effects. Everything else is derived from it: the JSON Schema the
model sees, the handler's signature, the serialization of results, the tests. Because the schema and the
handler share one source of truth they cannot drift apart, and because the output is typed the model
reads results of a stable shape instead of whatever the upstream API happened to return.

## Why it exists
In most first-generation agent codebases the tool schema is a hand-written dictionary in one file and
the handler reads a `dict` in another. Someone renames a parameter in the handler and forgets the
schema; the model keeps sending the old name; the handler raises `KeyError` in production on the
calls that matter. Outputs drift the same way: the handler returns the upstream API's JSON unchanged,
the upstream vendor renames `state` to `status`, and the agent silently starts reporting empty values.
Errors drift worst of all: one handler returns "not found", another raises, a third returns `None`,
and the model has no way to tell a transient failure it should retry from a permission failure it
should not.

The naive alternative, "document the tools in a wiki and be careful", fails because nothing enforces
it and because the failures are silent: the model does not crash on a wrong field name, it adapts,
badly. The contract pattern borrows the fix that API engineering settled on years ago: a machine-
readable interface definition from which clients, servers and tests are generated. For tools the
"client" is the model, the "server" is your handler, and JSON Schema is the shared language, which is
also why cross-process protocols such as MCP transport tools as schemas rather than code.

## How it works
### The four parts of a contract
1. **Input type.** A typed model (Pydantic, Zod, a dataclass with annotations) whose JSON Schema is
   generated, not written. Field names, enums and descriptions live in one place, so the model's view
   and the handler's view are the same object.
2. **Output type.** A typed model for what the tool returns, with stable field names, units in names
   (`total_cents`), an explicit status, counts and truncation flags. It is serialized the same way on
   every call, because the model is reading the result as a prompt and consistency is what lets it
   learn the shape from one call to the next.
3. **Error taxonomy.** A small enum of error kinds, each with a `retryable` flag: `not_found`,
   `invalid_argument`, `permission_denied`, `transient`, `rate_limited`. Errors are returned as
   `is_error: true` results carrying the kind and a message, because the model's correct recovery
   differs per kind: fix the argument, give up, wait and retry, ask the user.
4. **Metadata the harness uses.** Side-effect class (read-only, idempotent write, destructive),
   whether the tool is safe to run in parallel, a timeout, perhaps a cost tag. The model never sees
   these; the harness uses them to decide gating, concurrency and retries without parsing descriptions.

### Derivation, not duplication
A registry maps each tool name to its contract. At startup the registry produces the provider-format
tool list (`name`, `description`, `input_schema` from the input type). At call time it validates the raw
input into the input type, runs the handler, validates the handler's return value against the output
type, and serializes it. Anthropic's SDK tool runners (beta at the time of writing: `@beta_tool`
generating a schema from a Python signature, `betaZodTool` from a Zod schema) and similar helpers in
other SDKs are this pattern in miniature; the registry version adds the output
type, the error taxonomy and the harness metadata.

### Versioning
Treat the contract like a public API. Additive changes (a new optional field with a default) are safe.
Renames and type changes are breaking: ship them as a new tool name (`search_orders_v2`), or add the
new parameter as optional and keep accepting the old one for a transition period before removing it, because prompts, few-shot examples, evals and cached
conversations all encode the old shape. A snapshot test that compares the generated schema against a
committed copy makes an unintended change fail CI; an intended one updates the snapshot in the same PR.

### Across process boundaries
When tools live in another service or language, the contract is what crosses the wire. MCP's
`tools/list` returns a name, description and `inputSchema` per tool; since the 2025-06-18 revision a
server can also declare an `outputSchema` and return matching `structuredContent`, and optional tool
annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`) carry side-effect metadata. A typed
contract on your side maps onto that directly, with one caution: annotations from a server you do not
control are claims, not guarantees, so the harness should not relax a gate on their word alone. The same contract
also feeds the eval harness: expected arguments in a selection eval are instances of the input type,
so a schema change breaks the eval loudly rather than invalidating it silently.

## Example
A registry entry derived from types. The handler receives a validated `SearchOrdersIn`, must return
something that validates as `SearchOrdersOut`, and any failure is converted into a typed error. The
`read_only` and `parallel_safe` flags are read by the harness, not by the model.

```python
import json
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

class ErrorKind(str, Enum):
    not_found = "not_found"; invalid_argument = "invalid_argument"; transient = "transient"
    internal = "internal"

class ToolError(Exception):
    def __init__(self, kind: ErrorKind, message: str, retryable: bool = False):
        super().__init__(message)
        self.body = {"kind": kind.value, "message": message, "retryable": retryable}

class SearchOrdersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str = Field(description="Customer id, e.g. C-10492")
    status: Literal["open", "shipped", "delivered"] | None = Field(default=None, description="Filter")
    limit: int = Field(default=10, ge=1, le=50, description="Max rows, 1-50")

class OrderRow(BaseModel):
    order_id: str; status: str; total_cents: int; placed_on: str

class SearchOrdersOut(BaseModel):
    orders: list[OrderRow]; total_matches: int; truncated: bool

@dataclass
class Tool:
    name: str; description: str; input_model: type[BaseModel]; output_model: type[BaseModel]
    handler: Callable; read_only: bool = True; parallel_safe: bool = True   # harness-only metadata

    def spec(self) -> dict:                                 # what the model sees, derived from types
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_model.model_json_schema()}

    def run(self, raw_input: dict) -> tuple[str, bool]:     # (content, is_error)
        try:
            args = self.input_model.model_validate(raw_input)
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            return json.dumps({"kind": "invalid_argument", "message": msg, "retryable": False}), True
        try:
            out = self.output_model.model_validate(self.handler(args))   # output is checked too
            return out.model_dump_json(), False
        except ToolError as e:
            return json.dumps(e.body), True
        except ValidationError:                             # handler broke its own output contract
            return json.dumps({"kind": "internal", "message": "tool returned malformed data",
                               "retryable": False}), True  # our bug, not the model's: log and alert

CUSTOMERS = {"C-10492"}
ALL_ORDERS = [{"order_id": "O-1", "customer_id": "C-10492", "status": "shipped",
               "total_cents": 12_900, "placed_on": "2026-10-02"}]

def search_orders(args: SearchOrdersIn) -> dict:
    rows = [r for r in ALL_ORDERS if r["customer_id"] == args.customer_id
            and (args.status is None or r["status"] == args.status)]
    if not rows and args.customer_id not in CUSTOMERS:
        raise ToolError(ErrorKind.not_found, f"customer {args.customer_id} not found")
    return {"orders": rows[:args.limit], "total_matches": len(rows), "truncated": len(rows) > args.limit}

SEARCH = Tool("search_orders", "Find a customer's orders. Call when the user asks about their "
              "orders without giving an order id.", SearchOrdersIn, SearchOrdersOut, search_orders)
```

## Trade-offs
- One source of truth prevents drift, but it couples the model-facing schema to internal types; when
  the internal model is wide you need a separate thin tool-facing type, which is extra code but the
  right boundary.
- Typed outputs give the model stable results, at the cost of mapping work whenever an upstream API
  changes; the alternative, passing upstream payloads through, pushes that change onto the model at
  runtime where it fails silently.
- An error taxonomy enables correct recovery but demands discipline in every handler; a half-adopted
  taxonomy, some errors typed and others raw strings, is worse than none because the model learns to
  ignore the kind.
- Snapshot tests catch accidental schema changes but add friction to intentional ones; keep updating
  the snapshot a one-command step or people will delete the test.

## Pitfalls
- Exposing the ORM or API model as the tool input; the symptom is a thirty-field schema and a model that
  invents values for fields it does not understand.
- Returning upstream JSON unchanged; the symptom appears weeks later, when an upstream field rename
  makes the agent report null values with full confidence.
- Renaming a parameter in place; the symptom is evals on fresh prompts passing while cached
  conversations and few-shot examples that embed the old name break in production.
- Error strings without kinds; the symptom is the model retrying permission errors and giving up on
  transient ones, the exact opposite of what you want.
- Marking a tool `parallel_safe` or `read_only` without checking the handler; the symptom is duplicated
  writes the first time the model batches two calls to it.

## When to use
Adopt the contract pattern as soon as more than one person edits tools, more than one agent shares
them, or any tool has side effects; that covers nearly every production system. For a throwaway
prototype with two read-only tools, an SDK decorator that derives the schema from a function signature
is enough and is still the same idea. Measure schema-validation failure rate per tool (should be near
zero with a good contract), the share of errors that carry a kind, and how often a contract change broke
an eval. In daily work the contract is where you look first when a tool "stopped working": compare the
committed schema snapshot with what the model is sending. In interviews, the question is usually "how
do you keep tools from drifting as the team grows"; the answer is derive, snapshot, version additively.

## Role lens
- **Engineer:** Writes each tool as an input type, an output type and a handler, lets the registry
  generate the schema, and adds a snapshot test so an accidental change fails CI.
- **Architect:** Owns the error taxonomy, the side-effect classes and the versioning rules that every
  team's tools must follow so one harness can gate and parallelize them uniformly.
- **FDE:** Wraps a customer's inconsistent internal APIs behind contracts with stable outputs, so the
  agent's behaviour does not change when the customer's backend does.

## Check yourself
**Q:** A team of six adds tools weekly. After a refactor the agent began "calling tools with the wrong
parameter names" and nobody noticed for three days. How do you prevent this class of failure?
**A:** The schema and the handler were separate artifacts, so a rename in one did not reach the other,
and because the model adapts rather than crashes the failure was silent. Prevent it structurally: define
each tool's input and output as typed models and generate the schema from them so the two cannot
disagree; snapshot the generated schemas and fail CI when one changes without a deliberate update;
treat renames as breaking and ship them under a new tool name or with a transition period; and run a
small selection-and-arguments eval on every PR so a change that confuses the model is caught before
merge. Finally, alert on the per-tool validation failure rate so a drift that slips through shows up in
hours, not days.

## Quiz
### Q: What is the main benefit of generating the tool schema from the handler's input type instead of writing it by hand?
- [ ] Generated schemas are shorter and cost fewer tokens
- [x] The model's view of the arguments and the handler's expectations cannot drift apart
- [ ] Generated schemas are automatically strict-mode compatible on every provider
- [ ] It removes the need for argument validation
> One source of truth means a renamed or retyped field changes both sides at once. Token cost, strict
> compatibility and validation are separate concerns; validation is still needed for meaning and
> permission.

### Q: Why should a tool's output be typed and stable rather than passed through from the upstream API?
- [ ] Typed outputs are required by the tool-calling API
- [ ] Upstream APIs cannot return JSON
- [x] The model reads results as a prompt, so a stable shape is what lets it interpret them reliably, and upstream changes are caught at your boundary
- [ ] Typed outputs are always smaller
> The result is text the model has to understand; a shape that changes with the vendor's release notes
> produces silent misreadings. Mapping at the boundary turns an upstream change into a failing test
> instead of a wrong answer.

### Q: Which part of a typed tool contract is used by the harness rather than shown to the model?
- [ ] The parameter descriptions
- [ ] The enum values of each field
- [x] The side-effect class and parallel-safety flags
- [ ] The tool name
> Names, descriptions and enums are rendered into the prompt for selection and argument filling. Side-
> effect and concurrency metadata drive gating, approval and scheduling decisions that belong to the
> harness, not the model.
