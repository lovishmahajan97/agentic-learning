---
id: cross-language-api-contracts
module: agent-runtime-architecture
name: Cross-language API contracts
kind: pattern
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - agents/typed-tool-contracts
  - agents/tool-schemas-and-argument-validation
  - agents/model-context-protocol
  - python-for-ai/pydantic
  - node-ts-for-ai/runtime-schema-validation
  - fastapi-for-ai/openapi-contract-testing
  - python-for-ai/contract-testing
  - backend-fundamentals/api-versioning-and-backward-compatibility
sources:
  - Official docs: JSON Schema
  - Official docs: Protocol Buffers — Language Guide
  - Official docs: Pydantic
---

## TL;DR
Agent systems are polyglot by construction: Python for the loop, TypeScript for the product and tool
servers, Go or Java behind them, and the model itself consuming JSON Schema. Tool definitions, event streams
and run state cross those boundaries constantly. A contract is one machine-readable schema every side
generates its types and runtime validators from, with explicit rules for what JSON leaves ambiguous (64-bit
integers, dates, null versus absent, unknown enum values) and for evolving without breaking old consumers.

## Why it exists
Look at what crosses language boundaries in a typical runtime. The Python executor sends the model a tool
definition as JSON Schema. The model returns arguments. The arguments are dispatched to a tool server
written in TypeScript. The result flows back as JSON, into the step log, and out over a server-sent event
stream to a web client. Meanwhile the run's checkpoint is serialised to storage and must be readable by next
week's deploy. Each arrow is a contract whether or not anyone wrote it down.

When nobody writes it down, the types are hand-maintained on each side and drift. Python renames
`due_date` to `due_at`; the TypeScript tool still reads `due_date` and gets `undefined`, silently. The JSON
Schema sent to the model allows `priority: "urgent"` but the executor's validator only knows `low|high`, so
the model produces a legal call that fails at dispatch, and tool-call success rate degrades with no code
path to blame. A Python `int` id above 2^53 arrives in JavaScript rounded. A naive `datetime` serialises
without an offset and shifts by hours in another service. A new event type appears on the stream and an
old client throws.

The naive alternative, "JSON is self-describing, we'll be careful", fails for the same reason untyped code
fails at scale: there is no compiler across the boundary. TypeScript types vanish at runtime, Python type
hints are not enforced, and the model is a third participant that follows whatever schema it was shown.
Only a shared schema, generated into every language and validated at runtime on both sides, closes the gap.

## How it works
### One source of truth, generated everywhere
Choose one schema language and generate the rest. The practical options:

- **JSON Schema**, directly or via **OpenAPI** for HTTP surfaces. It is the natural choice for agent
  systems because tool definitions sent to models *are* JSON Schema, so the same document can drive the
  model, the executor's validator and the TypeScript types.
- **Protocol Buffers** with gRPC for high-volume internal RPC (executor to tool workers, event fan-out).
  Binary, compact, with strict and well-documented evolution rules; converted to JSON Schema at the edge
  where the model or browser needs it.
- **Language-native first**: define models in Pydantic (Python) or Zod (TypeScript), emit JSON Schema from
  them, and generate the other language's types from that. Ergonomic for two-language teams; it quietly
  makes one language the owner, which is fine as long as everyone knows which.

Whichever you pick, the rule is the same: the tool schema the model sees, the validator the executor runs
and the type the tool server compiles against are all derived from one file in one repository, checked by
CI that regenerates and fails on diff.

### The contract surfaces in an agent runtime
Name them explicitly, because each has a different consumer and evolution pace: tool definitions (name,
argument schema, result schema, error shape); the step event stream (a discriminated union on `type`,
versioned); run state and checkpoints (versioned, because old checkpoints must load after a deploy);
internal RPC between loop and workers; and the run context that policy relies on. MCP is itself an
example: a JSON-RPC contract with a published schema that lets Python and TypeScript servers and clients
interoperate without sharing code.

### Representation rules JSON does not give you
- **64-bit integers**: JavaScript numbers are exact only to 2^53 − 1. Serialise ids and large counts as
  strings; Protobuf's JSON mapping does this for `int64` by default for the same reason.
- **Dates and times**: ISO 8601 with an explicit offset, always. Reject naive datetimes at the producer.
- **Null versus absent**: Python `None`, TypeScript `undefined` and JSON `null` are three things. Decide
  per field whether "absent" and "null" mean the same, and encode that in the schema; the usual answer is
  to omit absent fields and forbid `null` unless it carries meaning ("clear this value").
- **Enums**: closed enums break the moment a producer adds a value. Model them as strings with a known set
  and have consumers map unknown values to a fallback rather than throw.
- **Floats**: `NaN` and `Infinity` are not JSON; decide on `null` or an error. Money is never a float.
- **Bytes**: base64 in a string field, with the encoding stated in the schema description.
- **Unknown fields**: service-to-service consumers ignore them (tolerant reader) so producers can add
  fields; model-to-executor arguments *forbid* them, because an extra field from a model is a hallucination,
  not an upgrade.

### Evolution without breaking anyone
Additive changes only: new optional fields, new event types that old consumers can skip, new enum values
with a fallback. Breaking changes (renames, type changes, new required fields) get a new version, signalled
by a `schema_version` field on events and stored state, or by a new endpoint. Never reuse a name or
Protobuf field number with a different meaning. Keep the old version readable for as long as a checkpoint
or client of that version can exist, and write the migration code when you introduce the new version, not
when the first old checkpoint fails to load.

### Validate at runtime on both sides, and test the contract
Compile-time types catch programmer errors; only runtime validation catches drift and model output.
Pydantic on the Python side, Zod or ajv on the TypeScript side, both generated from the same schema. Then
test the contract itself: a directory of golden JSON fixtures that both CI pipelines must accept, or
consumer-driven contract tests where each consumer publishes what it relies on and the producer's CI
verifies it still holds.

## Example
Pydantic models are the source of truth here. The same definition yields the tool specification sent to the
model, the executor's runtime validator and a JSON Schema file consumed by the TypeScript side (types via a
generator, runtime checks via ajv). Note the representation rules baked into the fields.

```python
import json
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

class Priority(str, Enum):
    low = "low"; high = "high"

class CreateTicketArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")        # model -> executor: unknown fields are hallucinations
    title: str = Field(max_length=200)
    priority: Priority = Priority.low
    due_at: datetime | None = None                   # ISO 8601 with offset on the wire
    account_id: str                                  # ids are strings: JS numbers are exact only to 2^53-1

    @field_validator("due_at")
    @classmethod
    def must_have_tz(cls, v):
        if v is not None and v.tzinfo is None:
            raise ValueError("due_at must carry a UTC offset")
        return v

class ToolCallEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")        # service -> service: tolerant reader
    type: Literal["tool_call"]; schema_version: Literal[2] = 2
    run_id: str; call_id: str; name: str; args: dict

class ToolResultEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["tool_result"]; schema_version: Literal[2] = 2
    run_id: str; call_id: str; ok: bool
    output: dict | None = None; error_code: str | None = None

Event = TypeAdapter(Annotated[Union[ToolCallEvent, ToolResultEvent], Field(discriminator="type")])

# One definition, three consumers.
tool_spec = {"name": "create_ticket", "input_schema": CreateTicketArgs.model_json_schema()}  # to the model
with open("contracts/agent.schema.json", "w") as f:                                          # to TypeScript
    json.dump({"CreateTicketArgs": CreateTicketArgs.model_json_schema(),
               "ToolResultEvent": ToolResultEvent.model_json_schema()}, f, indent=2)
args = CreateTicketArgs.model_validate({"title": "Renewal", "account_id": "9007199254740993"})  # executor
evt = Event.validate_python({"type": "tool_result", "run_id": "r1", "call_id": "c1", "ok": True})
# TS side: generate types from contracts/agent.schema.json and validate the same file with ajv in CI.
```

## Trade-offs
- JSON Schema and OpenAPI are universal, readable and identical to what models consume, but they are
  verbose on the wire and weak on evolution discipline; Protobuf and gRPC win for high-volume internal RPC
  and lose at the model and browser edge, where you convert anyway.
- Language-native-first schemas (Pydantic, Zod) are the fastest to write and keep in sync for a
  two-language team, but they make one language the owner; move to a neutral IDL when a third language or an
  external consumer appears.
- `extra="forbid"` on model-supplied arguments catches hallucinated fields immediately, while the same
  setting on service events breaks every additive change; the strictness has to match who the producer is.
- A codegen pipeline with CI diff checks costs setup time and occasional friction, but it is the only thing
  that keeps three codebases and a model agreeing after the tenth change.
- Versioning every event and checkpoint adds a field and migration code you may never need, yet the
  first time an old checkpoint must load after a schema change you will not have time to add it.

## Pitfalls
- Serialising 64-bit ids as JSON numbers; the symptom is a TypeScript client that looks up the wrong record
  by one or two, only for ids above 2^53.
- Emitting naive datetimes; the symptom is due dates that shift by the producer's UTC offset when read in
  another service or displayed in the browser.
- Adding a required field to an event or result; the symptom is old consumers failing validation the
  moment the producer deploys, with no code change on their side.
- Letting the model's tool schema and the executor's validator diverge; the symptom is a falling tool-call
  success rate with "invalid arguments" errors on calls that match the schema the model was shown.
- Closed enums on the consumer; the symptom is a crash on the first new status value, usually at night.
- Treating `null` and missing as interchangeable on one side only; the symptom is validation errors or
  values silently cleared when a field round-trips through the other language.

## When to use
The moment two languages, or one language and a model, exchange structured data, write the schema down and
generate from it; retrofitting after drift is far more expensive. Start with JSON Schema derived from your
primary language's models and promote to a neutral IDL when a third consumer arrives. Measure contract CI
failures (good: they are drift caught early), runtime validation failures per boundary in production
(bad: they are drift that escaped), and tool-call argument rejection rate as the agent-specific signal. In
day-to-day work this is a `contracts/` directory, a generate-and-diff step in CI, and validators at every
ingress. Interviewers probe the JSON ambiguities (int64, dates, null versus absent, enums) and evolution
rules; a strong answer also notes that the model is a consumer of the contract too.

## Role lens
- **Engineer:** Defines schemas once, wires codegen into CI, puts runtime validation at every ingress, and
  fixes the int64 and timezone issues before they reach production.
- **Architect:** Picks the schema language per surface, sets the evolution policy and versioning scheme,
  and decides which language owns which contract.
- **FDE:** Negotiates contracts with the customer's integration teams, often in Java or .NET, and uses golden
  fixtures to prove interoperability before either side deploys.

## Check yourself
**Q:** A Python agent service and a TypeScript tool server share hand-written types. After a release, the
agent's tool-call success rate drops 15% with "invalid arguments" errors, although the model's calls look
correct. Diagnose and fix structurally.
**A:** The likely cause is drift between the schema shown to the model and the validator on the tool server:
the Python side changed an enum or a field name and regenerated the model-facing schema, but the TypeScript
validator kept the old shape, so legal calls are rejected. I would confirm by diffing the schema in the tool
definition against the server's validator on a failing call. The structural fix is a single contract: one
schema file in a `contracts/` directory, the model's tool definition and Pydantic validator derived from it
in Python, TypeScript types and an ajv validator generated from the same file, and a CI step that fails on
any uncommitted regeneration diff. Add golden fixtures both sides must accept, forbid extra fields on
model-supplied arguments, make enums tolerant on consumers, and alert on argument rejection rate so the next
drift is caught in minutes.

## Quiz
### Q: Why should 64-bit identifiers be serialised as strings in a JSON contract consumed by a TypeScript service?
- [ ] JSON does not allow integers larger than 32 bits
- [x] JavaScript numbers are exact only up to 2^53 − 1, so larger integers are silently rounded
- [ ] Strings compress better than numbers on the wire
- [ ] TypeScript's type system cannot represent integers
> JSON itself permits arbitrary integers, but the receiving language's number type does not; values above
> 2^53 − 1 lose precision without an error. Strings preserve the exact id, which is why Protobuf's JSON
> mapping does the same for `int64`.

### Q: A Python runtime validates model-supplied tool arguments with `extra="forbid"` and validates incoming service events with `extra="ignore"`. Why the difference?
- [ ] Events are larger, so ignoring extras saves memory
- [x] An unexpected field from a model is a hallucination to reject, while an unexpected field from a service is usually an additive upgrade to tolerate
- [ ] Pydantic cannot forbid extras on discriminated unions
- [ ] Service events are already validated by the producer
> Strictness should match the producer's trust and evolution model. Rejecting unknown model fields stops
> invented arguments reaching tools; tolerating unknown event fields is what lets producers add fields
> without coordinating every consumer's deploy.

### Q: Which change to a shared event schema is backward compatible for existing consumers?
- [ ] Renaming `error_code` to `errorCode` for consistency with TypeScript style
- [ ] Changing `schema_version` from an integer to a string
- [x] Adding a new optional field with a default that tolerant consumers can ignore
- [ ] Adding a new required field that every event must now include
> Only additive, optional changes leave old consumers working. Renames and type changes alter fields they
> already read, and a new required field fails validation on every message they had no part in changing;
> those need a new version.
