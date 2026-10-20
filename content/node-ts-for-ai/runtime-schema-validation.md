---
id: runtime-schema-validation
module: node-ts-for-ai
name: Runtime schema validation (Zod)
kind: practice
difficulty: core
est_minutes: 15
related:
  - node-ts-for-ai/typescript-strict-mode-and-advanced-types
  - node-ts-for-ai/openai-anthropic-node-sdks
  - python-for-ai/pydantic
  - agents/tool-schemas-and-argument-validation
  - prompting/structured-outputs-json-mode
  - safety-security/output-validation
  - fastapi-for-ai/pydantic-request-response-models
sources:
  - Official docs: Zod
  - Official docs: JSON Schema specification
---

## TL;DR
TypeScript types vanish at compile time, so nothing checks the JSON that arrives from an HTTP client, a
webhook, an environment variable or a language model. Runtime schema validation defines the shape once
as a value (a Zod schema), derives the static type from it, and parses untrusted input into a typed
value or a structured list of errors. The same schema becomes the JSON Schema sent to the model for
tool calling, so the contract the model follows is the contract you enforce.

## Why it exists
Every interesting input to an AI service is untrusted. A request body can be anything the client sent.
A model's "JSON" output can be a markdown-fenced blob with a trailing comment, a number where a string
was asked for, or an argument object with a field the tool never defined. An environment variable is
a string even when the code needs a port number. A compile-time type on any of these is a comment,
not a check: `JSON.parse(body) as ChatRequest` tells the compiler to trust you, and the first missing
field becomes `undefined` deep inside business logic, usually as a `TypeError` after the model call
was already billed.

The naive alternatives fail in predictable ways. Hand-written `if (typeof x.messages !== "object")`
checks drift from the type the rest of the code uses and are never complete. Writing a TypeScript
interface *and* a separate JSON Schema for the model doubles the surface and lets them disagree, so the
model is instructed to produce one shape and the code checks another. Validating only at the HTTP
layer misses the inputs that bypass it: queue messages, model outputs, tool results. The schema-as-value
approach exists to make one definition serve three purposes: the static type (`z.infer`), the runtime
parser (`schema.parse`), and the machine-readable contract handed to the model (JSON Schema). The
principle is "parse, don't validate": the output of a parse is a *new* value of a narrower type, so
code after it cannot forget that the check happened.

## How it works
### Schema as a value
Zod schemas are built by composition: `z.object({ model: z.string(), temperature:
z.number().min(0).max(2).default(1), messages: z.array(Message).min(1) })`. Each combinator returns a
schema object carrying both the runtime checks and the phantom static type, which `z.infer<typeof
Schema>` extracts. The type is derived from the schema rather than the reverse *because* a type
cannot be inspected at runtime, while a value can be both executed and reflected on.

### `parse` versus `safeParse`
`Schema.parse(input)` returns the typed value or throws a `ZodError`; `Schema.safeParse(input)` returns
`{ success: true, data }` or `{ success: false, error }` without throwing. Use `parse` at boundaries
where failure should become an HTTP 400 through a central error handler, and `safeParse` where failure
is an expected branch, such as a model output you intend to repair. `error.issues` is an array of
`{ path, code, message }`, which is both what you return to a client and what you feed back to a model.

### Transform, refine, coerce
Validation often needs to change the value: `z.string().transform((s) => s.trim())`, `z.coerce.number()`
for a port read from `process.env`, `.refine((v) => v.end > v.start, { message })` for cross-field
rules, `.pipe()` to chain a transform into a further schema. A transform makes the output type differ
from the input type, which is exactly what `parse` is for: the result is a domain value, not the wire
shape.

### Objects: strict, strip, passthrough
By default Zod *strips* unknown keys from objects. `.strict()` rejects them, which is what you want for
tool arguments (an unexpected key usually means the model misread the schema) and for request bodies
where silently dropping a typo'd field hides a client bug. `.passthrough()` keeps them, useful for
forwarding provider payloads you do not fully model. Discriminated unions (`z.discriminatedUnion("type",
[...])`) validate tagged variants like stream events and give the compiler the same narrowing that a
hand-written union would.

### From schema to the model, and back
Tool calling and structured outputs require a JSON Schema describing the arguments or the response.
Generating it from the Zod schema (Zod 4 ships `z.toJSONSchema`; earlier versions use the
`zod-to-json-schema` package, and the provider SDKs and the Vercel AI SDK accept Zod schemas directly)
guarantees the model sees the same contract the code enforces, with `.describe()` strings becoming the
field descriptions the model reads. The round trip is: schema → JSON Schema → model → JSON text →
`safeParse` → typed value. Even with provider-side constrained decoding, keep the parse: it is the only
check for `refine` rules, it guards against provider bugs, and it is where a failed parse becomes a
repair prompt ("the field `due_date` must be ISO 8601; you returned `tomorrow`").

### Alternatives in the ecosystem
Valibot offers the same model with smaller bundles; TypeBox and Ajv work JSON-Schema-first, which suits
teams whose source of truth is an OpenAPI document; ArkType trades Zod's chaining for a type-syntax
string. The choice rarely matters as much as the discipline: one schema per boundary, the type derived
from it, and no `as` casts on untrusted data.

## Example
A tool definition whose Zod schema produces both the TypeScript argument type and the JSON Schema sent
to the model, with a repair loop that feeds validation issues back when the model's arguments do not
parse. Unknown keys are rejected because an extra key from a model usually means it misread the schema.

```ts
import { z } from "zod";

const ScheduleArgs = z.object({
  title: z.string().min(1).describe("Short title for the calendar event"),
  start: z.string().datetime().describe("ISO 8601 start time with timezone offset"),
  durationMinutes: z.number().int().min(5).max(480).default(30),
  attendees: z.array(z.string().email()).max(20).default([]),
}).strict();
type ScheduleArgs = z.infer<typeof ScheduleArgs>;       // { title: string; start: string; ... }

export const scheduleTool = {
  name: "schedule_event",
  description: "Create a calendar event",
  parameters: z.toJSONSchema(ScheduleArgs),             // Zod 4; use zod-to-json-schema on Zod 3
  run: async (args: ScheduleArgs) => ({ id: "evt_123", ...args }),
};

type ModelCall = (messages: Array<{ role: string; content: string }>) => Promise<string>;

export async function callToolWithRepair(rawArgs: string, model: ModelCall, maxRepairs = 2) {
  let text = rawArgs;
  for (let attempt = 0; attempt <= maxRepairs; attempt++) {
    let json: unknown;
    try { json = JSON.parse(text); } catch { json = undefined; }
    const result = ScheduleArgs.safeParse(json);
    if (result.success) return scheduleTool.run(result.data);   // result.data is typed and trusted
    const issues = result.error.issues
      .map((i) => `${i.path.join(".") || "(root)"}: ${i.message}`).join("\n");
    if (attempt === maxRepairs) throw new Error(`tool args invalid after repairs:\n${issues}`);
    text = await model([
      { role: "system", content: "Return only JSON matching the schema; fix the listed problems." },
      { role: "user", content: `Previous arguments:\n${text}\n\nProblems:\n${issues}` },
    ]);
  }
  throw new Error("unreachable");
}

const port = z.coerce.number().int().min(1).max(65535).parse(process.env.PORT ?? "3000");
void port;
```

## Trade-offs
- One schema gives type, parser and model contract together, but it couples the wire format to the
  domain type; when they must diverge, add a `transform` or a second schema rather than casting.
- Validating every boundary costs CPU proportional to payload size, negligible against a model call
  but noticeable on hot internal paths with large arrays; validate at the edge, trust inside.
- `.strict()` catches model and client mistakes early, yet it breaks when a provider adds a harmless
  field; use `strip` for payloads you forward and `strict` for arguments you act on.
- Repairing invalid model output with a retry prompt improves success rates at the cost of another
  model call and latency; bound the attempts and prefer provider structured-output modes that reduce
  the failure rate in the first place.
- JSON-Schema-first tools (TypeBox, Ajv) align with OpenAPI-driven teams, while Zod's TypeScript-first
  style is more ergonomic for application code; mixing both in one service is where contracts drift.

## Pitfalls
- Casting untrusted JSON with `as` instead of parsing; the symptom is a `TypeError` on an `undefined`
  field deep in business logic, after the model call was billed.
- Maintaining a TypeScript interface and a hand-written JSON Schema separately; the symptom is the
  model returning a field the code rejects, or the code expecting a field the model was never told about.
- Using default `strip` behaviour on tool arguments; the symptom is a model hallucinating an extra
  parameter that is silently dropped, so the tool runs with half of what the model intended.
- Returning raw `ZodError` messages to end users or logging full payloads in issues; the symptom is
  leaked internal field names or PII in client responses and logs.
- Skipping the parse because the provider "guarantees" the schema; the symptom is `refine` rules never
  running and a provider-side change or bug reaching production unchecked.
- Reading `process.env` without coercion; the symptom is `"3000" + 1` style bugs and a port compared as a
  string.

## When to use
Parse at every boundary where data enters the process: HTTP bodies and query strings, webhook payloads,
queue messages, environment configuration, model outputs and tool-call arguments, and responses from
third-party APIs you depend on. Derive types from schemas rather than the reverse, generate the model's
JSON Schema from the same source, and use `safeParse` with a bounded repair loop where the model is the
producer. Do not validate internal function calls between already-typed modules; that is what the
compiler is for. Measure the validation failure rate per boundary (a rising rate on model outputs
usually means a prompt or model change) and the repair success rate. Interviewers ask why types alone
are insufficient, how a Zod schema becomes a tool definition, and how to handle invalid model output.

## Role lens
- **Engineer:** Defines one Zod schema per tool and per endpoint, derives types with `z.infer`, generates
  the tool's JSON Schema from it, and routes `ZodError` issues into 400 responses or repair prompts.
- **Architect:** Decides where the schemas live (a shared contracts package), which boundaries are
  strict versus permissive, and how schema versions evolve alongside prompts and tool definitions.
- **FDE:** Audits a customer's agent for `as` casts on model output, replaces them with parsed schemas,
  and uses the validation failure rate to show which tools the model misuses most.

## Check yourself
**Q:** A team argues that since their provider supports structured outputs with constrained decoding,
validating the model's JSON with Zod on their side is redundant work. What do you tell them?
**A:** Constrained decoding makes the output syntactically match the JSON Schema, which removes most
failures, but it does not remove the need to parse. First, the JSON Schema cannot express everything the
Zod schema checks: cross-field `refine` rules, date validity, semantic ranges. Second, the parse is what
produces a typed value; without it the code is still operating on `unknown` or on an `as` cast, so the
compiler cannot help. Third, the provider is an external system that can change or have bugs, and the
parse is the only place that failure is caught before a tool runs with bad arguments. Finally, the
schema is already written, so the validation is a single `safeParse` call whose cost is negligible next
to the model call. The constrained mode reduces repairs; it does not replace the boundary check.

## Quiz
### Q: Why derive the TypeScript type from the Zod schema with `z.infer` rather than writing the interface first and the schema to match?
- [ ] `z.infer` produces faster code because it skips type checking
- [x] A schema is a runtime value that can both validate and be reflected into JSON Schema, while a type is erased, so the schema must be the single source of truth
- [ ] Interfaces cannot describe optional fields, but schemas can
- [ ] Zod refuses to compile when an interface exists for the same shape
> Types exist only at compile time; nothing can execute or inspect them. Making the schema primary means
> the type, the parser and the model-facing JSON Schema cannot drift apart. Interfaces describe optional
> fields fine; they simply cannot check anything at runtime.

### Q: When should a tool-argument schema use `.strict()` instead of Zod's default of stripping unknown keys?
- [ ] Never; stripping is always safer because it avoids rejecting valid calls
- [ ] Only when the tool has no optional parameters
- [x] When an unexpected key likely means the model misread the contract, so rejecting and repairing is better than silently dropping it
- [ ] When the JSON Schema is generated by the provider instead of the code
> A model that adds an undefined parameter has misunderstood the tool, and stripping it would run the tool
> with a different intent than the model had. Strict mode surfaces the mismatch so the issue can be fed
> back for repair. Stripping remains appropriate for payloads you merely forward.

### Q: A model's tool arguments fail `safeParse`. What is the most robust next step?
- [ ] Run the tool with whatever fields parsed correctly
- [ ] Cast the raw JSON to the argument type and continue
- [x] Send the parse issues back to the model in a bounded repair loop, and fail the call if it still does not parse
- [ ] Retry the identical prompt until the output parses
> The issues carry the path and reason for each failure, which is exactly the feedback a model needs to
> correct its output. Bounding the attempts prevents runaway cost, and failing explicitly beats running a
> side-effecting tool with partial or unchecked arguments.
