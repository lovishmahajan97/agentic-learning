---
id: typescript-strict-mode-and-advanced-types
module: node-ts-for-ai
name: TypeScript strict mode & advanced types
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - node-ts-for-ai/runtime-schema-validation
  - node-ts-for-ai/promises-and-async-error-propagation
  - python-for-ai/type-hints
  - python-for-ai/protocols-generics-structural-typing
  - agents/typed-tool-contracts
  - backend-fundamentals/node-js-typescript
sources:
  - Official docs: TypeScript Handbook (tsconfig reference, Narrowing, Conditional and Mapped Types)
---

## TL;DR
TypeScript types are erased before the code runs, so they only protect you if the compiler is allowed
to be strict. `strict` turns on null checks, bans implicit `any` and makes function types sound; the
advanced features (discriminated unions, constrained generics, conditional/mapped/template-literal
types, `satisfies`, `unknown`) let you encode the shapes AI code actually handles — stream events, tool
contracts, provider responses — so mismatches fail at compile time instead of as `undefined` crashes
in the middle of a streamed answer.

## Why it exists
A Node AI service is mostly glue between things that have no static shape: JSON from a model, tool-call
arguments the model invented, server-sent events that come in four or five variants, SDK response
objects with deeply optional fields (in OpenAI's Chat Completions shape, `choices[0]?.message?.content`
is legitimately `null` when the model returned a tool call instead of text). JavaScript lets you read any property of anything, so the
natural failure mode is `TypeError: Cannot read properties of undefined` thrown halfway through a
request, after tokens have been billed and partially streamed to the user.

The naive answer is "write tests". Tests exercise the paths you thought of; a type checker exercises
every call site of every function on every change, including the provider SDK upgrade that silently
made a field optional. The second naive answer is "use TypeScript, but leave the defaults": without
`strict`, untyped parameters become `any`, `null` is a member of every type, and `JSON.parse` returns
`any` that flows unchecked into your domain objects. That TypeScript catches almost nothing, and worse,
it creates a false sense that the shapes are checked. Strict mode plus the advanced type features exist
to make the compiler say *no* at the exact places where a dynamic language would have said *maybe*:
null handling, variant handling, and the boundary where untrusted data becomes typed data.

## How it works
### What `strict` actually switches on
`"strict": true` is an umbrella for a family of flags, each closing a specific hole:

- **`noImplicitAny`** — an untyped parameter or variable is an error rather than silently `any`.
  Because `any` is viral: one untyped parameter turns everything derived from it into `any`.
- **`strictNullChecks`** — `null` and `undefined` are their own types, not members of every type. A
  `string | null` must be narrowed before `.trim()`. This is the flag that turns "Cannot read
  properties of undefined" from a runtime surprise into a compile error.
- **`strictFunctionTypes`** — parameters of function-typed values are checked contravariantly. A handler
  typed to accept `TextEvent` cannot be passed where a handler of `StreamEvent` is expected, because it
  would receive events it cannot handle. Parameters declared with method syntax (`onEvent(e: T): void`
  in an interface) stay bivariant for compatibility, so prefer property syntax (`onEvent: (e: T) => void`)
  for callback contracts.
- **`strictPropertyInitialization`**, **`strictBindCallApply`**, **`noImplicitThis`**, **`alwaysStrict`**
  — class fields must be initialised at declaration or in the constructor, `.call/.apply` are type-checked, `this` must have a
  known type, and `"use strict"` is emitted.
- **`useUnknownInCatchVariables`** — `catch (e)` makes `e: unknown`, so you must narrow before reading
  `e.message`. Anything can be thrown in JS, including strings and `undefined`.

New TypeScript releases occasionally add flags to the `strict` family (5.6 added
`strictBuiltinIteratorReturn`), so upgrading the compiler can surface new errors; that is the point.

Three flags are *not* in `strict` and are worth adding: `noUncheckedIndexedAccess` (`arr[i]` and
`record[key]` are `T | undefined`, which is the truth — `choices[0]` can be missing),
`exactOptionalPropertyTypes` (an optional property may be absent but may not be explicitly
`undefined`; this matters because `{ ...defaults, ...overrides }` with `temperature: undefined`
*overwrites* the default, and `"temperature" in opts` is true for a present-but-undefined key), and
`noFallthroughCasesInSwitch`.

### How narrowing works
Strictness is livable only because the checker does *control-flow analysis*: it tracks, per code path,
what a variable can be. After `if (x === null) return;` the type of `x` below is `T`, not `T | null`.
Narrowing comes from `typeof x === "string"`, `x instanceof Error`, `"tool_calls" in msg`, equality on
a literal field (`ev.type === "done"`), truthiness, and two user-written forms: a type predicate
(`function isToolCall(x: unknown): x is ToolCall`) narrows in the caller's `if`, and an assertion
function (`function assertDefined<T>(x: T): asserts x is NonNullable<T>`) narrows everything after the
call or throws. Both are trusted blindly: if the body of a type guard is wrong, the compiler believes
the lie, so keep guards tiny or generate them from a schema.

### `unknown` versus `any`
`any` switches the checker off for that value. `unknown` is the honest top type: you can hold it, but
you cannot call, index or dereference it until you narrow it with `typeof`, `instanceof`, a user-defined
type guard (`function isToolCall(x: unknown): x is ToolCall`), or a runtime validator. The rule that
follows: wrap `JSON.parse` so it returns `unknown`, and make every boundary (HTTP body, model output,
env var, webhook) go through a parse step that produces a typed value or throws. Types inside the
boundary can then be trusted *because* nothing crosses it unvalidated.

### Discriminated unions and exhaustiveness
Model a stream as a union of object types sharing a literal `type` field:
`{ type: "text_delta"; text: string } | { type: "tool_call"; name: string; args: unknown } |
{ type: "done"; usage: Usage }`. A `switch (ev.type)` narrows `ev` in each branch, and a `default`
branch that assigns `ev` to a variable of type `never` fails to compile the day a provider adds a new
variant. This is why unions beat class hierarchies for protocol data: the compiler enumerates the
call sites you must update.

### Generics with constraints
`function defineTool<A, R>(spec: { parse: (x: unknown) => A; run: (args: A) => Promise<R> })` ties the
argument parser to the handler's parameter type, so a handler cannot be registered with a parser that
produces a different shape. `extends` constrains what a type parameter may be (`K extends keyof T`),
and inference from arguments means callers rarely write the type parameters by hand.

### Conditional, mapped and template-literal types
Mapped types transform every property (`Partial<T>`, `Readonly<T>`, `Record<K, V>`, or a custom
`{ [P in keyof T]: T[P] | null }`). Conditional types branch on a type
(`T extends Promise<infer U> ? U : T` is a simplified `Awaited`; the built-in one also recurses and
unwraps any thenable; `ReturnType` and `Parameters` are built the same way with `infer`). Template-literal types build string unions
(`` `${Provider}:${Model}` ``). They are for library and contract code — typing an SDK wrapper or a
tool registry — not for everyday application logic, where they cost readability.

### Branded types
TypeScript is structural: any `string` fits any `string` parameter, so an unvalidated user string and
a sanitized SQL fragment, or a token count and a dollar amount, are interchangeable. A brand adds a
phantom property that only one function can produce: `type ValidatedPrompt = string & { readonly
__brand: "ValidatedPrompt" }`. Only `validatePrompt(s: string): ValidatedPrompt` returns one (via a
single internal cast), so `callModel(p: ValidatedPrompt)` cannot be handed raw input by mistake. The
brand costs nothing at runtime because it is erased; it encodes "this check already happened" in the
type, which is the same idea as parsing at the boundary.

### `as const` and `satisfies`
`as const` makes a literal deeply readonly and keeps literal types (`"gpt-4o"` instead of `string`).
`satisfies` checks a value against a type *without* widening it, so a model table can be validated
as `Record<string, ModelSpec>` while `keyof typeof table` still yields the literal model names. A plain
annotation would widen the keys to `string` and lose that.

### What the compiler cannot do
`tsc` emits JavaScript with every type removed; no check runs at runtime. A strict program that casts
`JSON.parse(body) as Request` is exactly as unsafe as untyped JS. Strictness pays off only when paired
with runtime validation at the edges, which is the subject of the schema-validation lesson.

## Example
A typed stream handler, an exhaustiveness check, a model table that keeps literal keys, and a branded
type that makes "validated" visible to the compiler. Compile with
`"strict": true` and `"noUncheckedIndexedAccess": true` in `tsconfig.json`.

```ts
type Usage = { inputTokens: number; outputTokens: number };
type StreamEvent =
  | { type: "text_delta"; text: string }
  | { type: "tool_call"; name: string; args: unknown }
  | { type: "done"; usage: Usage };

const models = {
  fast: { id: "small-model", maxOutput: 4096 },
  smart: { id: "large-model", maxOutput: 16384 },
} as const satisfies Record<string, { id: string; maxOutput: number }>;
type ModelKey = keyof typeof models;            // "fast" | "smart", not string

function parseJson(text: string): unknown {      // never let `any` escape JSON.parse
  return JSON.parse(text);
}

function handle(ev: StreamEvent, out: string[], tools: Map<string, (a: unknown) => void>) {
  switch (ev.type) {
    case "text_delta": out.push(ev.text); break;          // ev narrowed: .text exists
    case "tool_call": {
      const fn = tools.get(ev.name);                        // Map.get is T | undefined
      if (!fn) throw new Error(`unknown tool ${ev.name}`);
      fn(ev.args); break;
    }
    case "done": console.log(ev.usage.outputTokens); break;
    default: { const never: never = ev; return never; }   // compile error if a variant is added
  }
}

function pick(key: ModelKey) { return models[key].id; }    // models["typo"] is a compile error

type ValidatedPrompt = string & { readonly __brand: "ValidatedPrompt" };
function validatePrompt(s: string): ValidatedPrompt {
  if (!s.trim() || s.length > 32_000) throw new Error("bad prompt");
  return s as ValidatedPrompt;                              // the one sanctioned cast
}
declare function callModel(p: ValidatedPrompt): Promise<string>;
// callModel(rawInput) fails to compile; callModel(validatePrompt(rawInput)) compiles
const first = ["a", "b"][0];                                // string | undefined under noUncheckedIndexedAccess
try { parseJson("{"); } catch (e) {                        // e: unknown
  if (e instanceof SyntaxError) console.error(e.message);
}
```

## Trade-offs
- Strict mode front-loads work: every `null` must be handled explicitly, which costs time on a fresh
  codebase but pays back the first time a provider makes a field optional; it flips against you only
  on throwaway scripts where no second reader exists.
- Advanced types (conditional, mapped, template-literal) make contracts precise but raise compile times
  and the reading cost for teammates; they pay off when many call sites consume one contract (an SDK
  wrapper, a tool registry) and cost more than they save when a type has one or two users.
- `unknown` at every boundary forces a parse step, which is extra code and a small runtime cost, but the
  alternative is trusting casts; the trade only flips for data your own process produced and never
  serialized, where the compiler already knows the shape.
- Branded types prevent mixing up same-shaped values (raw vs validated input, tokens vs dollars), but
  every construction site needs the brand's factory; worth it where a mix-up is a security or billing
  bug, noise where it is merely a naming issue.
- Discriminated unions give exhaustiveness checks, while class hierarchies give polymorphic dispatch;
  unions win for protocol data that crosses the wire, classes win when behaviour outweighs shape.
- Gradual adoption (`strict` off, flags turned on one by one) keeps a migration shippable but leaves the
  codebase in a state where the types lie; set a deadline or the migration never finishes.

## Pitfalls
- Casting with `as` to silence an error (`JSON.parse(x) as Response`); the symptom is a runtime
  `TypeError` on a field the type said was present, usually after an upstream API change.
- Leaving `strict: false` and assuming the red squiggles mean the code is checked; the symptom is a
  codebase full of implicit `any` where renaming a field breaks nothing at compile time and everything
  at runtime.
- Forgetting the `never` default branch in a `switch` over a union; the symptom is a new event type
  silently dropped on the floor, often noticed as "the stream ends early for tool calls".
- Annotating instead of using `satisfies`, so a config map loses its literal keys; the symptom is
  `keyof typeof config` becoming `string` and typos in model names compiling cleanly.
- Treating `catch (e: any)` as harmless; the symptom is `e.message` being `undefined` because a library
  threw a string or a non-Error object, which then hides the real failure in logs.
- Writing a type guard or assertion function whose body checks less than its signature claims (`x is
  ToolCall` after only checking `typeof x === "object"`); the symptom is a crash on a field the guard
  "proved" existed, with no cast anywhere to grep for.

## When to use
Turn on `strict` on day one of any Node AI service and add `noUncheckedIndexedAccess`; the cost of
retrofitting grows with every file. Reach for discriminated unions whenever data has variants (stream
events, tool results, provider error shapes) and for constrained generics when you build a registry or
SDK wrapper that must tie an input type to a handler. Use conditional and mapped types only when a plain
type would have to be repeated or kept in sync by hand. Measure success by the share of `any` and `as`
in the codebase (lint for both with `@typescript-eslint/no-explicit-any` and
`consistent-type-assertions`) and by how often runtime `TypeError`s reach production. In interviews,
being able to explain *why* `strictNullChecks` matters, what `unknown` buys over `any`, and how
exhaustiveness checking works is the expected depth; writing a mapped type from memory is not.

## Role lens
- **Engineer:** Models provider stream events and tool contracts as discriminated unions, keeps
  `JSON.parse` behind an `unknown`-returning wrapper, and fixes compile errors instead of casting past them.
- **Architect:** Sets the compiler baseline (`strict`, index-access checks, lint bans on `any`) as a
  repository standard and decides which shared packages own the cross-service type contracts.
- **FDE:** Reads a customer's `tsconfig.json` first when debugging "random undefined" incidents, and
  frames turning on `strict` as the cheapest reliability fix available in their stack.

## Check yourself
**Q:** A teammate argues that since TypeScript types are erased at runtime, enabling `strict` is
cosmetic and runtime validation with a schema library is all that matters. How do you respond?
**A:** Both are needed because they catch different failures. Runtime validation guards the boundary:
it turns untrusted JSON into a value you can trust, but it only runs where someone remembered to call
it. Strict typing guards everything inside the boundary: once a value is typed, every call site is
checked on every change, including the SDK upgrade that made a field nullable or the new stream
variant nobody handled. Without `strict`, a validated value can still be dereferenced as `any` two
functions later. The right design is `unknown` at the edge, a schema parse that produces a typed value,
and strict checking everywhere after that, so a mismatch surfaces either at compile time or at the
boundary, never mid-request.

## Quiz
### Q: Why is `unknown` preferred over `any` for the result of `JSON.parse`?
- [ ] `unknown` is faster because the engine skips property lookups
- [x] `unknown` cannot be used until narrowed, so the compiler forces a check before the value spreads through typed code
- [ ] `any` cannot hold `null`, so parsed JSON with nulls would fail
- [ ] `unknown` automatically validates the JSON against an interface
> `any` disables checking and propagates silently through every assignment. `unknown` holds the value
> but refuses every operation until a type guard or validator narrows it, which is exactly the discipline
> a boundary needs. Neither type performs any runtime validation.

### Q: What does a `default: const never: never = ev` branch in a switch over a discriminated union achieve?
- [ ] It throws at runtime if an unexpected event arrives
- [ ] It tells the bundler the branch is dead code and removes it
- [x] It makes the code fail to compile when a new union member is added but not handled
- [ ] It converts the union into an enum for faster dispatch
> If every variant is handled, `ev` has type `never` in the default branch and the assignment type-checks.
> Add a variant without a case and `ev` is no longer `never`, so the compiler reports every switch that
> needs updating. It provides no runtime behaviour on its own.

### Q: Why use `satisfies` instead of a type annotation for a constant config table?
- [ ] `satisfies` performs runtime validation of the object
- [ ] Annotations are not allowed on `const` declarations
- [x] `satisfies` checks the value against the type while keeping its literal keys and values, so `keyof typeof table` stays precise
- [ ] `satisfies` makes the object deeply immutable
> An annotation widens the value to the declared type, turning literal keys into `string`. `satisfies`
> only verifies compatibility, so the inferred type keeps `"fast" | "smart"` as keys and the compiler
> can reject typos in model names. Immutability comes from `as const`, not `satisfies`.
