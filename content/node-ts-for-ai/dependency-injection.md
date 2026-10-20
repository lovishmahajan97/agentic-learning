---
id: dependency-injection
module: node-ts-for-ai
name: Dependency injection
kind: pattern
difficulty: core
est_minutes: 13
related:
  - node-ts-for-ai/vitest-jest-and-integration-testing
  - node-ts-for-ai/typescript-strict-mode-and-advanced-types
  - node-ts-for-ai/openai-anthropic-node-sdks
  - node-ts-for-ai/api-clients-with-retries-and-circuit-breakers
  - agent-runtime-architecture/secrets-and-credential-isolation
  - agent-runtime-architecture/multi-tenant-state-isolation
sources:
  - Fowler, 2004 — Inversion of Control Containers and the Dependency Injection pattern
  - Official docs: NestJS
  - Official docs: Awilix
---

## TL;DR
Dependency injection means a module receives its collaborators (LLM client, database pool, cache, clock,
config) from the caller instead of importing or constructing them itself. AI services are mostly glue
around expensive, non-deterministic, credentialed external calls; code that hard-wires those calls cannot
be tested without the network, cannot vary per tenant, and cannot swap providers without a rewrite. In
TypeScript the pattern needs no framework: a typed `Deps` object and one composition root cover most
services.

## Why it exists
A typical AI backend handler does five things: load a tenant's config, fetch context from Postgres and a
vector store, call an LLM through an SDK client, write the result plus some usage numbers, and emit a
trace. The obvious way to write it is `import { openai } from './clients'` where `clients.ts` does
`export const openai = new OpenAI()`. This works on day one and hurts on day ten.

The problems appear in a predictable order. Tests: every unit test of the handler now either calls the
real API (slow, paid, flaky, needs secrets in CI) or reaches for module mocking (`vi.mock('./clients')`),
which couples tests to file paths and silently diverges from the real wiring. Per-tenant variation: an
enterprise customer wants their traffic routed to an Azure endpoint with their own key; a module-level
singleton has exactly one key. Provider fallback and A/B tests: "use Anthropic when OpenAI returns 529"
cannot be expressed when the dependency is a fixed import. Lifecycle: the singleton is created at import
time, before config is validated and before the process knows whether it is a web server, a worker or a
one-off script, so startup order becomes accidental.

The naive fix, "export a `setClient()` setter", is global mutable state: concurrent requests from
different tenants race on it. Dependency injection instead makes the dependency an explicit parameter,
decided once at the edge of the program, so the core logic is pure with respect to its collaborators.
The important word is *explicit*: you can read a function's signature and know everything it touches.

## How it works
### The shape of the pattern
1. **Define the dependency by the capability you need, not the library you use.** An interface
   `LLM { complete(req): Promise<Completion> }` is far narrower than the OpenAI SDK's surface; the handler
   depends on that interface *because* then a fake, a caching wrapper or a different vendor can satisfy
   it. TypeScript's structural typing means no `implements` keyword is required: any object with the
   right shape is accepted.
2. **Inject through the constructor or a factory argument.** `createChatService({ llm, db, clock })` or
   `class ChatService { constructor(private deps: Deps) {} }`. Setter and field injection are avoided
   *because* an object that can exist half-wired is a bug waiting for a code path.
3. **Compose at one root.** A `main.ts` (or `createApp()`) builds the concrete graph: reads env,
   constructs the pool, the Redis client and the SDK client, then the services, then the HTTP routes.
   All `new` calls for infrastructure live here, so the rest of the codebase has none. The root is also
   where lifecycle lives: it starts things in dependency order and shuts them down in reverse.
4. **Tests build a different graph.** `createChatService({ llm: fakeLLM, db: testDb, clock: fixed })`.
   No module mocks, no monkey-patching, no environment variables.

### Scopes
Infrastructure clients (pool, Redis, SDK) are singletons: constructing them is expensive and they own
sockets. Request-scoped objects carry tenant id, trace context, the user's remaining budget and
per-tenant model routing; they are built per request from the singletons plus the request. A clean way
in Node is to create a `RequestContext` in middleware and pass it down, or to use `AsyncLocalStorage`
for ambient values such as the trace id. `AsyncLocalStorage` is complementary, not a replacement: it is
good for cross-cutting context you would otherwise thread through every signature, and bad for real
dependencies, *because* hidden ambient dependencies recreate the testability problem DI solves.

### Containers and decorators
Frameworks automate the composition root. NestJS uses class decorators (`@Injectable()`) and constructor
parameter types to resolve dependencies through `reflect-metadata`; this requires the TypeScript
`experimentalDecorators` and `emitDecoratorMetadata` compiler options, and because interfaces are erased
at compile time, anything that is not a class must be registered under an explicit token (a string or a
`Symbol`). tsyringe and InversifyJS work the same way; Awilix resolves by parameter name or by an
explicit registration map and needs no decorators. A container buys automatic graph construction,
scoped lifetimes (request scope in NestJS, `createScope()` in Awilix) and lazy construction; it costs a
runtime resolution step, errors that surface at boot instead of at compile time, and a learning curve.
For a service with a dozen dependencies a hand-written root is usually clearer; past a few dozen with
request scopes, a container pays for itself.

### What it is not
DI is not "make everything an interface". Pure functions and value types are not injected. Inject the
things that are slow, stateful, non-deterministic or credentialed: network clients, databases, caches,
clocks, randomness, the file system.

## Example
Three files compressed into one listing: a capability interface, a service that depends only on it, the
composition root that knows about vendors, and a test that swaps in a fake. The service never imports a
vendor SDK, so the test runs with no network and no API key.

```ts
// llm.ts — the capability, not the vendor
export interface LLM { complete(p: { system: string; user: string; maxTokens: number }): Promise<{ text: string; inputTokens: number; outputTokens: number }> }

// chat-service.ts — depends only on interfaces
export interface Deps { llm: LLM; db: { saveUsage(tenant: string, tokens: number): Promise<void> }; now: () => Date }
export function createChatService({ llm, db, now }: Deps) {
  return {
    async answer(tenant: string, question: string) {
      const r = await llm.complete({ system: `Today is ${now().toISOString()}`, user: question, maxTokens: 512 });
      await db.saveUsage(tenant, r.inputTokens + r.outputTokens);
      return r.text;
    },
  };
}

// main.ts — the composition root: the only place that knows about vendors
import OpenAI from "openai";
import { Pool } from "pg";
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
const openai = new OpenAI();                                  // reads OPENAI_API_KEY here, nowhere else
const llm: LLM = {
  async complete({ system, user, maxTokens }) {
    const res = await openai.chat.completions.create({ model: process.env.MODEL ?? "gpt-4o-mini", max_tokens: maxTokens,
      messages: [{ role: "system", content: system }, { role: "user", content: user }] });
    return { text: res.choices[0].message.content ?? "", inputTokens: res.usage?.prompt_tokens ?? 0, outputTokens: res.usage?.completion_tokens ?? 0 };
  },
};
const db = { saveUsage: async (t: string, n: number) => { await pool.query("insert into usage(tenant, tokens) values ($1, $2)", [t, n]); } };
export const chat = createChatService({ llm, db, now: () => new Date() });

// chat-service.test.ts — no network, no mocking of module paths
const calls: string[] = [];
const svc = createChatService({
  llm: { complete: async (p) => { calls.push(p.user); return { text: "42", inputTokens: 10, outputTokens: 1 }; } },
  db: { saveUsage: async () => {} },
  now: () => new Date("2026-01-01T00:00:00Z"),
});
// expect(await svc.answer("acme", "meaning?")).toBe("42"); expect(calls).toEqual(["meaning?"]);
```

## Trade-offs
- Explicit wiring makes every dependency visible and testable, but signatures and the composition root grow; past a few dozen services with request scopes, a container (NestJS, Awilix) becomes cheaper than hand-wiring.
- Decorator-based containers give automatic resolution and scoped lifetimes, but they rely on `emitDecoratorMetadata`, need tokens for anything that is not a class, and move "missing dependency" errors from the compiler to boot time; in small services and libraries the trade flips back to manual wiring.
- Narrow capability interfaces let you swap vendors and wrap caching or retry decorators around them, but every vendor feature you need (tool calls, streaming, prompt caching) must be surfaced through the interface, so over-abstracting early costs real functionality.
- Fakes in unit tests are fast and deterministic, but they only prove the service calls the interface correctly; a thin layer of integration tests against the real SDK (recorded or sandboxed) is still needed or the fake drifts from reality.

## Pitfalls
- Module-level singletons created at import time (`export const openai = new OpenAI()`): the symptom is tests that fail with "missing API key" or make real calls, and startup crashes whose stack trace points at an import line.
- Service locator in disguise: passing one giant `container` object and calling `container.get('llm')` inside business logic; the symptom is that a signature no longer tells you what a function touches, and every test must build the whole container.
- Mixing scopes: injecting a request-scoped object (tenant config, trace span) into a singleton; the symptom is one tenant's model or API key leaking into another tenant's request under concurrency.
- Mocking by file path (`vi.mock('../clients')`) instead of injecting: the symptom is tests that break when files move, and mocks that stay green after the real wiring changed.
- Circular dependencies between services, which containers resolve lazily and hand-wiring cannot; the symptom is `undefined` at runtime or a NestJS circular-dependency boot error, and the fix is usually splitting an interface.

## When to use
Reach for explicit injection in any service that talks to an LLM, a database or a queue, which is nearly
all AI backend code. Decide the boundary by asking "would I want to replace this in a test or per
tenant?" If yes, inject it; if it is a pure function, do not. Start with a hand-written composition root
and a `Deps` type; move to a container when request-scoped lifetimes and the number of services make the
root hard to read, or when the team has standardised on NestJS. Measure success by whether unit tests
run with no network and no env vars, and whether adding a second LLM provider touches only the root. In
interviews, the expected depth is explaining why module singletons break tests and multi-tenancy, how
interface erasure affects containers, and where request scope lives.

## Role lens
- **Engineer:** Writes services against `LLM`, `Store` and `Clock` interfaces, wires concrete clients in one `createApp()`, and keeps unit tests free of network calls by passing fakes.
- **Architect:** Owns the capability interfaces and the composition root as the seam where provider fallback, per-tenant routing, caching and credential isolation are decided without touching business logic.
- **FDE:** Swaps the customer's private endpoint, proxy or on-prem model in at the root, and demonstrates the service against a fake LLM in the customer's CI before credentials are issued.

## Check yourself
**Q:** Your service imports a single OpenAI client at module scope. A customer now requires their
requests to go to an Azure OpenAI deployment with their own key, and the team wants automatic fallback
to Anthropic on 529 errors. How would you restructure, and where does each decision live?
**A:** Introduce a narrow `LLM` interface covering only the operations the service uses, and make the
service take it as a constructor or factory argument. At the composition root build concrete adapters:
one wrapping the OpenAI SDK, one for Azure with the customer's endpoint and key, one for Anthropic.
Compose them: a `FallbackLLM` that tries the primary and, on a 529 or 5xx, calls the secondary, and a
router that picks a per-tenant adapter from config at request time. The business logic never changes;
tests inject a fake `LLM` and a fixed clock. Credentials stay in the root, tenant routing is
request-scoped, and the fallback policy is a decorator around the interface, which keeps retry and
circuit-breaker logic out of the handler.

## Quiz
### Q: Why does TypeScript's type erasure matter for decorator-based DI containers?
- [ ] Because interfaces cannot be exported from modules that use decorators
- [x] Because an interface does not exist at runtime, so the container needs an explicit token or a class to know what to inject
- [ ] Because erased types make constructor parameters optional
- [ ] Because `emitDecoratorMetadata` only works with JavaScript files
> Containers resolve dependencies at runtime, and `emitDecoratorMetadata` can only record types that exist as values; an interface compiles to nothing, so a string or `Symbol` token (or a class) must stand in for it.

### Q: Which dependency is usually NOT worth injecting?
- [ ] A function returning the current time
- [ ] A Postgres connection pool
- [x] A pure function that formats a prompt template from its arguments
- [ ] A client for a vector database
> Injection pays off for collaborators that are slow, stateful, non-deterministic or credentialed. A pure function is deterministic and free to call, so injecting it only adds indirection; clocks look harmless but are non-deterministic and matter in tests.

### Q: A request-scoped tenant config object gets injected into a singleton LLM service. What is the likely symptom?
- [ ] Compile errors because scopes are checked by the TypeScript compiler
- [ ] Slower startup because the singleton is now rebuilt per request
- [x] Under concurrent traffic, one tenant's model routing or API key is used for another tenant's request
- [ ] The container refuses to start and logs a circular-dependency error
> A singleton is constructed once and shared, so it captures whichever request-scoped object it was first given; later requests from other tenants reuse that state, a correctness and security failure that only appears under concurrency.
