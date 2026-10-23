---
id: api-clients-with-retries-and-circuit-breakers
module: node-ts-for-ai
name: API clients with retries & circuit breakers
kind: pattern
difficulty: core
est_minutes: 17
status: typed
lesson_type: lab
agent_relevance: core
related:
  - serving-inference/retry-strategies
  - reliability/circuit-breakers
  - serving-inference/timeouts
  - serving-inference/idempotency
  - node-ts-for-ai/abortcontroller-and-cancellation
  - node-ts-for-ai/openai-anthropic-node-sdks
  - serving-inference/rate-limiting
  - enterprise-integration/retry-deduplication-reconciliation
sources:
  - Nygard, 2007 — Release It!
  - Brooker, 2015 — Exponential Backoff And Jitter (AWS Architecture Blog)
  - Official docs: Anthropic TypeScript SDK
  - Official docs: OpenAI Node SDK
---

## TL;DR
Every model and tool call an agent makes can fail transiently (429, 5xx, resets), so each needs a
per-attempt timeout, classified retries with jittered backoff and a deadline. A circuit breaker fails fast
while a provider or tool is down.

## For agent builders
- **Why it matters for agents.** An agent makes dozens of model and tool calls per run: at a 1% transient
  failure rate per call, a 50-call run fails about 40% of the time (1 − 0.99^50) unless calls retry.
  Badly layered retries do the opposite: they multiply load and cost across every step during an outage.
- **Where it shows up in your stack.** The Anthropic and OpenAI SDK options `maxRetries` and `timeout`,
  your tool wrappers around SaaS and internal APIs, `opossum` or `cockatiel` policies, and the queue's
  `attempts` setting above the whole step.
- **What you control.** Who owns retries (SDK, tool wrapper or queue), which errors retry, idempotency
  keys for side-effecting tools, the run's deadline, and what the model sees when a tool fails.
- **What you can skip.** Writing your own retry loop around the vendor SDK, and shared-state breakers
  across the fleet. Per-process breakers and SDK retries cover most agent services.

## Lab
### Task
Build the call path one agent tool goes through: classified retries with full jitter, a
consecutive-failure breaker with a single half-open probe, and a wrapper that turns every failure into
a tool result. The first draft retries everything, including a 400 and a ticket-creating POST, and
lets a down CRM crash every agent step.
- **Agent goal:** a transient blip is absorbed, a wrong request fails once, a non-idempotent tool is
  never repeated, and a dead dependency costs milliseconds while the model is told why.
- **Time box:** 60-90 minutes.
- **Prerequisites:** this lesson's "Classify before retrying", "Exponential backoff with full jitter",
  "Circuit breaker" and "In an agent loop: failures become tool results"; AbortController &
  cancellation for the per-attempt timeout you would add in production.
- **Needs:** Node 20+ with `npm i -D tsx vitest`. A fake clock replaces timers, so backoff is
  deterministic and the tests take milliseconds. Checked with Node 24.21, tsx 4.23 and vitest 5.0.5.

### Starter code
```ts
// retry-lab.ts: classified retries, a circuit breaker and tool failures the model can read
// A fake clock replaces timers, so every backoff is deterministic and the demo runs instantly.
export type Clock = { now(): number; sleep(ms: number): Promise<void> };
export function fakeClock() {
  const c = { t: 0, sleeps: [] as number[], now: () => c.t, async sleep(ms: number) { c.sleeps.push(ms); c.t += ms; } };
  return c;
}

// What SDKs and fetch wrappers surface: an HTTP status (maybe with Retry-After) or a socket error code.
export type CallError = Error & { status?: number; retryAfterSec?: number; code?: string };
export const httpError = (status: number, retryAfterSec?: number): CallError =>
  Object.assign(new Error(`HTTP ${status}`), { status, retryAfterSec });
export const netError = (code: string): CallError => Object.assign(new Error(code), { code });

export const RETRYABLE = new Set([408, 429, 500, 502, 503, 504, 529]);
export const NEVER_SENT = ["ECONNREFUSED", "UND_ERR_CONNECT_TIMEOUT"];       // safe for any request
export const MAYBE_SENT = ["ECONNRESET", "ETIMEDOUT", "UND_ERR_SOCKET"];     // idempotent requests only

export type Policy = { attempts: number; baseMs: number; capMs: number; deadline: number;
                       random: () => number; clock: Clock };

// YOUR CODE 1: the first draft retries every error after a fixed delay. Make it:
// - retry only RETRYABLE statuses, NEVER_SENT codes, and MAYBE_SENT codes when `idempotent`;
// - wait Retry-After seconds when the error carries one, else full jitter:
//   random() * min(capMs, baseMs * 2 ** n) for retry n = 0, 1, 2, ...;
// - never sleep past `deadline` (clock time): rethrow the last error instead.
export async function retry<T>(fn: () => Promise<T>, p: Policy, idempotent: boolean): Promise<T> {
  for (let n = 0; ; n++) {
    try {
      return await fn();
    } catch (e) {
      if (n + 1 >= p.attempts) throw e;
      await p.clock.sleep(p.baseMs);
    }
  }
}

export class BreakerOpenError extends Error {}

// YOUR CODE 2: a consecutive-failure breaker. Closed: count failures, reset on success. At `threshold`
// failures it opens: calls throw BreakerOpenError without running fn, until resetMs of clock time has
// passed. Then exactly one probe runs (half-open): success closes it, failure re-opens it.
export class Breaker {
  state: "closed" | "open" | "half-open" = "closed";
  failures = 0;
  openedAt = 0;
  constructor(private clock: Clock, private threshold = 3, private resetMs = 30_000) {}
  async call<T>(fn: () => Promise<T>): Promise<T> {
    throw new Error("not implemented: Breaker.call");
  }
}

export type ToolSpec = { name: string; idempotent: boolean; run: (args: unknown, key: string) => Promise<string> };
export type ToolResult = { content: string; isError: boolean };

// YOUR CODE 3: the first draft lets failures escape and crash the agent step. Run the tool through
// its breaker (one per tool name, kept in `breakers`, created as new Breaker(p.clock, 2, 30_000))
// around retry(), and turn every failure into
// { isError: true, content: "Tool <name> failed: <message>" }, or "Tool <name> is temporarily
// unavailable" when the breaker is open, so the loop can hand it back to the model.
export const breakers = new Map<string, Breaker>();
export async function callTool(spec: ToolSpec, args: unknown, key: string, p: Policy): Promise<ToolResult> {
  return { content: await retry(() => spec.run(args, key), p, spec.idempotent), isError: false };
}

export function failing(errors: CallError[], ok = "ok") {      // fails with each error in turn, then succeeds
  const t = { calls: 0, run: async () => { const e = errors[t.calls++]; if (e) throw e; return ok; } };
  return t;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const clock = fakeClock();
  const p = (): Policy => ({ attempts: 3, baseMs: 100, capMs: 1_000, deadline: clock.now() + 10_000, random: () => 0.5, clock });
  const cases: [string, boolean, CallError[]][] = [
    ["search", true, [httpError(503), httpError(503)]],
    ["quota", true, [httpError(429, 2)]],
    ["lookup", true, [httpError(400), httpError(400), httpError(400)]],   // the request is wrong every time
    ["create_ticket", false, [netError("ECONNRESET")]],
  ];
  for (const [name, idempotent, errors] of cases) {
    const t = failing(errors, `${name} done`);
    clock.sleeps = [];
    try {
      const r = await callTool({ name, idempotent, run: t.run }, {}, `run-1-step-0-${name}`, p());
      console.log(`${name}: ${r.isError ? "error" : "ok"} "${r.content}" attempts ${t.calls} sleeps [${clock.sleeps}]`);
    } catch (e) { console.log(`${name}: threw ${(e as Error).message}`); }
  }
  const down = failing(Array(20).fill(httpError(503)));
  for (let i = 1; i <= 4; i++) {
    try {
      const r = await callTool({ name: "crm", idempotent: true, run: down.run }, {}, `k${i}`, { ...p(), attempts: 2 });
      console.log(`crm call ${i}: "${r.content}" total attempts ${down.calls}`);
    } catch (e) { console.log(`crm call ${i}: threw ${(e as Error).message}`); }
  }
}
```

Run it with `npx tsx retry-lab.ts`. As given, `create_ticket` is retried after a reset (two tickets
if the first POST landed), `lookup` throws its 400 at the agent, and all four CRM calls throw.

### Expected output
```
search: ok "search done" attempts 3 sleeps [50,100]
quota: ok "quota done" attempts 2 sleeps [2000]
lookup: error "Tool lookup failed: HTTP 400" attempts 1 sleeps []
create_ticket: error "Tool create_ticket failed: ECONNRESET" attempts 1 sleeps []
crm call 1: "Tool crm failed: HTTP 503" total attempts 2
crm call 2: "Tool crm failed: HTTP 503" total attempts 4
crm call 3: "Tool crm is temporarily unavailable" total attempts 4
crm call 4: "Tool crm is temporarily unavailable" total attempts 4
```

### Tests
```ts
// retry-lab.test.ts: run with `npx vitest run`
import { beforeEach, describe, expect, it } from "vitest";
import { Breaker, BreakerOpenError, breakers, callTool, failing, fakeClock, httpError, netError, retry,
         type Policy } from "./retry-lab";

let clock = fakeClock();
const policy = (o: Partial<Policy> = {}): Policy =>
  ({ attempts: 3, baseMs: 100, capMs: 1_000, deadline: 60_000, random: () => 0.5, clock, ...o });
beforeEach(() => { clock = fakeClock(); breakers.clear(); });

describe("retry", () => {
  it("retries transient statuses with full jitter", async () => {
    const t = failing([httpError(503), httpError(529)]);
    expect(await retry(t.run, policy(), true)).toBe("ok");
    expect(clock.sleeps).toEqual([50, 100]);                    // 0.5 x 100, 0.5 x 200
  });
  it("caps the exponential term", async () => {
    const t = failing([httpError(500), httpError(500), httpError(500)]);
    await retry(t.run, policy({ attempts: 4, capMs: 150, random: () => 1 }), true);
    expect(clock.sleeps).toEqual([100, 150, 150]);
  });
  it("honours Retry-After", async () => {
    await retry(failing([httpError(429, 2)]).run, policy(), true);
    expect(clock.sleeps).toEqual([2_000]);
  });
  it("never retries a request that is wrong", async () => {
    for (const s of [400, 401, 404, 422]) {
      const t = failing([httpError(s), httpError(s)]);
      await expect(retry(t.run, policy(), true)).rejects.toThrow(`HTTP ${s}`);
      expect(t.calls).toBe(1);
    }
  });
  it("retries ambiguous socket errors only for idempotent calls", async () => {
    await expect(retry(failing([netError("ECONNRESET")]).run, policy(), false)).rejects.toThrow("ECONNRESET");
    expect(await retry(failing([netError("ECONNRESET")]).run, policy(), true)).toBe("ok");
    expect(await retry(failing([netError("ECONNREFUSED")]).run, policy(), false)).toBe("ok");
  });
  it("stops before sleeping past the deadline", async () => {
    const t = failing([httpError(503), httpError(503), httpError(503)]);
    await expect(retry(t.run, policy({ deadline: 250, random: () => 1 }), true)).rejects.toThrow("HTTP 503");
    expect([t.calls, clock.sleeps]).toEqual([2, [100]]);         // the next wait (200) would end at 300
  });
});

describe("Breaker", () => {
  it("opens at the threshold, fails fast, then lets one probe through", async () => {
    const b = new Breaker(clock, 2, 1_000);
    const down = failing(Array(10).fill(httpError(503)));
    for (let i = 0; i < 2; i++) await expect(b.call(down.run)).rejects.toThrow("HTTP 503");
    await expect(b.call(down.run)).rejects.toBeInstanceOf(BreakerOpenError);
    expect(down.calls).toBe(2);
    await clock.sleep(1_000);
    await expect(b.call(down.run)).rejects.toThrow("HTTP 503");  // the probe fails: open again
    expect([b.state, down.calls]).toEqual(["open", 3]);
    await clock.sleep(1_000);
    expect(await b.call(async () => "back")).toBe("back");
    expect(b.state).toBe("closed");
  });
  it("rejects other callers while the probe is in flight", async () => {
    const b = new Breaker(clock, 1, 1_000);
    await expect(b.call(failing([httpError(500)]).run)).rejects.toThrow();
    await clock.sleep(1_000);
    let release!: (v: string) => void;
    const probe = b.call(() => new Promise<string>((r) => { release = r; }));
    await expect(b.call(async () => "second")).rejects.toBeInstanceOf(BreakerOpenError);
    release("probe ok");
    expect(await probe).toBe("probe ok");
  });
});

describe("callTool", () => {
  it("returns failures as error results the model can read", async () => {
    const r = await callTool({ name: "lookup", idempotent: true, run: failing([httpError(400)]).run }, {}, "k", policy());
    expect(r).toEqual({ isError: true, content: "Tool lookup failed: HTTP 400" });
  });
  it("keeps one breaker per tool and reports it open", async () => {
    const down = failing(Array(20).fill(httpError(503)));
    const spec = { name: "crm", idempotent: true, run: down.run };
    for (let i = 0; i < 2; i++) await callTool(spec, {}, `k${i}`, policy({ attempts: 2 }));
    expect(await callTool(spec, {}, "k3", policy())).toEqual({ isError: true, content: "Tool crm is temporarily unavailable" });
    expect(down.calls).toBe(4);
    const other = await callTool({ name: "search", idempotent: true, run: async () => "hits" }, {}, "k4", policy());
    expect(other).toEqual({ isError: false, content: "hits" });
  });
});
```

All 10 fail on the starter: the retry tests with `AssertionError` (fixed 100 ms waits, a 400 and a
reset retried, no deadline check), the breaker tests on `not implemented: Breaker.call`, and the
`callTool` tests because errors escape (`HTTP 503`) or a 400 is retried into a success.

### Failure scenarios
- **Inject the breaker inside `retry`** (wrap each attempt, not the whole sequence). The CRM opens after
  one logical call instead of two, as the lesson's layering table predicts. Log breaker transitions
  with the attempt number; each logical call should count once.
- **Inject `Math.random` instead of `p.random`.** The jitter tests flake and the demo's sleeps change
  per run. Every source of non-determinism the code reads (clock, randomness) belongs in the policy.
- **Inject a half-open state that lets every caller through.** The in-flight-probe test fails: after a
  reset, all queued agent steps hit the recovering CRM at once. Count calls that reach the dependency
  while the state is `half-open`; it must be one.
- **Inject the deadline check after `sleep`.** The deadline test sees a 200 ms sleep that ends at
  300 ms against a 250 ms budget; in a run this is how a queue job outlives its lock.

### Submission
- **Your `retry-lab.ts`** with the three `YOUR CODE` parts done; the fakes and demo unchanged.
- **A `npx vitest run` log** showing 10 passed.
- **The `npx tsx retry-lab.ts` output**, matching the expected lines.
- **A note of 3-5 sentences:** the worst-case added wall-clock time of one `callTool` with the demo
  policy, and which layer (SDK, this wrapper or the queue) owns retries in your real agent.
- **Evaluated on:** classification by status and code, full jitter with a cap and Retry-After, a
  deadline that is never overrun, one probe at a time, and no exception escaping `callTool`.

## Why it exists
- **Unbounded latency.** A naive client does `await fetch(url)`. A provider that accepts the TCP
  connection and then stalls leaves the handler waiting until the socket gives up; for Node's built-in
  `fetch` (undici, with 300-second header and body timeouts) that is minutes by default. A few hundred of
  those hold every worker slot, pooled DB connection and queue lock.
- **Transient errors become user-facing failures.** A 429 from a shared quota or a 529
  `overloaded_error` from Anthropic often clears within seconds, so not retrying throws away a request
  that would have succeeded.
- **"Retry everything, immediately, five times" is worse.** During an incident a thousand clients
  retrying in lockstep multiply load five-fold at the wrong moment, synchronised by the same schedule,
  and non-idempotent calls (a tool that sends an email, a POST that creates a resource) execute more than
  once.
- **Each layer fixes one problem.** Timeouts fix the first; backoff with jitter fixes the second and
  defuses the third; idempotency keys make retries safe.
- **The breaker handles what backoff cannot.** When the dependency is down for minutes, the right
  behaviour is not "wait 30 s then fail" per request but "fail in a millisecond and try later", which
  protects your latency budget, frees resources and gives the provider room to recover.

## How it works
### Timeouts first
- **Every attempt gets an `AbortSignal`** with a per-attempt timeout (`AbortSignal.timeout(ms)` in
  current Node, or an `AbortController` you cancel), *because* a retry policy without a timeout retries
  nothing while the first attempt hangs.
- **Streaming needs two numbers.** Time-to-first-token differs from total completion time: time out the
  connection and first chunk, then use an idle timeout between chunks, not one giant budget that must
  cover a 90-second answer.
- **Carry a request-level deadline** through the call chain so attempt timeouts plus backoff never
  exceed what the caller can wait for.

### Classify before retrying
| Outcome | Retry? | Because |
|---|---|---|
| 408, 429, 500, 502, 503, 504, Anthropic's 529 | yes | transient: a later attempt can succeed |
| 400, 401, 403, 404, 422 | never | the request is wrong and will be wrong again |
| `ECONNREFUSED`, `UND_ERR_CONNECT_TIMEOUT` | always | the request never reached the server |
| `ECONNRESET`, read timeout, socket closed mid-body | only if idempotent | the server may have processed it and the reply was lost |

- **Ambiguous failures cost money.** The first attempt may have succeeded server-side after you stopped
  listening, and an LLM call that completed is billed whether or not you received it.

### Exponential backoff with full jitter
```
sleep(n) = random(0, min(cap, base × 2^n))      // attempt n
```
- **The exponential term** backs off fast enough to help a recovering provider.
- **The cap** keeps the wait sane.
- **The jitter** spreads clients out, *because* identical schedules re-synchronise the thundering herd on
  every attempt.
- **Honour `Retry-After`** (seconds or an HTTP date; the Anthropic and OpenAI SDKs also read a
  millisecond variant): the server knows its own recovery window better than your formula.
- **Bound both** attempt count and total elapsed time.

### Idempotency
- **A retried POST must be safe** when it creates a run, sends a message or charges a budget.
- **Pass an idempotency key** the server deduplicates on (many APIs accept an `Idempotency-Key` header),
  or make the operation naturally idempotent (PUT by id, upsert).
- **If neither is possible,** do not retry automatically; surface the ambiguity to the caller.

### The vendor SDKs
- **What the Anthropic TypeScript SDK retries:** connection errors, 408, 409, 429 and 5xx, with backoff,
  honouring `Retry-After`. The OpenAI Node SDK follows the same design with the same defaults.
- **Options:** `maxRetries` (default 2) and `timeout` (default 10 minutes, in milliseconds). For a
  non-streaming call whose `max_tokens` implies a longer generation, the SDK refuses and asks you to
  stream unless you set `timeout` explicitly.
- **Deadline arithmetic.** A timed-out attempt is itself retried, so wall-clock time can reach roughly
  `timeout × (maxRetries + 1)` plus backoff.
- **Typed errors.** `Anthropic.RateLimitError` for 429, `Anthropic.InternalServerError` for 5xx
  including 529, `Anthropic.APIConnectionError`, all subclasses of `Anthropic.APIError` with `.status`,
  `.headers` and the parsed error body on `.error` (whose inner error's `.type` is, for example,
  `overloaded_error`). Classification is an
  `instanceof` chain plus a status check, not string matching.
- **Configure, do not wrap.** Set those options rather than adding a second retry loop, *because* nested
  retries multiply: 3 × 3 = 9 attempts against a provider that is already overloaded.

### Circuit breaker
1. **Closed** (normal): calls pass through; failures are counted in a rolling window.
2. **Open**: once failures exceed a threshold (say, over half of at least 20 calls in 10 s), the breaker
   rejects calls immediately for a `resetTimeout` without touching the dependency.
3. **Half-open**: after the timeout, one trial call goes through; success closes the breaker, failure
   re-opens it.

- **Trips on rate over a minimum volume,** not one failure, *because* one bad response is noise.
- **Rejects fast** *because* a queued request that will fail anyway costs latency and a connection slot.
- **Probes instead of flipping back blindly** *because* you want evidence of recovery before releasing
  the full load.
- **Needs a fallback:** a smaller model, a cached answer, a degraded response, or a clear error with a
  `Retry-After` for your own callers.
- **Libraries:** `opossum` is the common Node breaker; `cockatiel` composes retry, timeout, breaker and
  bulkhead policies; `p-retry` covers retry alone.
- **State is per process.** With 20 replicas, each counts its own failures, trips on its own schedule and
  sends its own probe, so the provider sees up to 20 probes per reset period. Usually acceptable; sharing
  state through Redis makes the fleet trip together but puts a network call in front of every request
  to protect against a network failure.

### Bulkheads and retry budgets
- **Bulkhead:** limit concurrent in-flight calls per dependency (`p-limit` or a semaphore) so one slow
  provider cannot consume every worker slot. For LLM providers this also respects concurrency-based
  quotas, and backed by Redis it works across replicas.
- **Why a budget:** per-request limits do not bound fleet-wide amplification; if every request may make
  3 attempts, an outage can triple traffic.
- **Retry budget:** cap retries as a fraction of recent successful or total requests (for example, at most one retry per ten
  requests over the last minute, an illustrative ratio to tune). During a blip almost every failure gets
  its retry; during an outage the budget empties and failures surface immediately.
- **Prior art:** it is the per-client analogue of a breaker; some service meshes and gRPC clients
  implement it as retry throttling.

### In an agent loop: failures become tool results
- **Model calls and tool calls fail differently.** A failed model call stops the step; a failed tool call
  can be reported back to the model, which may pick another tool, fix its arguments or answer without it.
- **Return, do not throw, after retries are spent.** Wrap each tool so an exhausted retry, an open breaker
  or a 4xx becomes an error tool result (both major APIs let a tool result be marked as an error), and the
  loop continues.
- **Bound the whole run.** Give the run a deadline and pass the remaining time into each step, so retries
  late in a run cannot outlive what the caller or the queue job allows.

### Composing the layers
From the outside in:

| Order | Layer | Because |
|---|---|---|
| 1 | request deadline | it is the caller's promise; every inner wait must fit inside it |
| 2 | bulkhead | caps concurrency before any work starts |
| 3 | breaker | one exhausted retry sequence is one logical failure; inside, it would count each attempt and trip three times faster, and an open breaker should stop retries from starting |
| 4 | retry with backoff | acts on classified errors |
| 5 | per-attempt timeout | bounds a single attempt, so a hung attempt becomes a classifiable error |

## Diagram
The composed call path: the breaker sees one logical outcome per call, while the retry loop inside it
handles individual attempts within the deadline.

```mermaid
flowchart TD
    C["Caller with deadline"] --> B["Bulkhead: take a slot"]
    B --> CB{"Breaker open?"}
    CB -->|"yes"| FB["Fallback or fast error"]
    CB -->|"no, or half-open probe"| T["Attempt with own timeout"]
    T --> P[("Provider API")]
    T -->|"success or non-retryable"| OK["Return to caller"]
    T -->|"429, 5xx, safe network error"| K{"Attempts and deadline left?"}
    K -->|"yes: jitter or Retry-After"| T
    K -->|"no"| F["One logical failure"]
    F -->|"counts toward"| CB
```

## Examples
### `fetchWithRetry` and a minimal breaker
A per-attempt timeout (combined with the caller's own signal), classification that retries ambiguous
failures (timeouts, resets) only for idempotent requests, full-jitter backoff and `Retry-After`, plus a
consecutive-failure breaker that lets exactly one probe through when half-open.

Production code uses the SDK's built-in retries and `opossum` or `cockatiel`, but the mechanics are
these. After the last retryable status the response is returned, not thrown, so the breaker's wrapped
function must turn 5xx and 429 into failures itself, as the usage line shows.

```ts
const RETRYABLE = new Set([408, 429, 500, 502, 503, 504, 529]);
const NEVER_SENT = ["ECONNREFUSED", "UND_ERR_CONNECT_TIMEOUT"];           // safe for any method
const MAYBE_SENT = ["ECONNRESET", "ETIMEDOUT", "UND_ERR_SOCKET"];         // idempotent requests only
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const jitter = (n: number, base: number, cap: number) => Math.random() * Math.min(cap, base * 2 ** n);

export async function fetchWithRetry(url: string, init: RequestInit,
                                     o = { attempts: 4, baseMs: 500, capMs: 8_000, attemptTimeoutMs: 20_000 }) {
  const idempotent = ["GET", "HEAD", "PUT", "DELETE"].includes(init.method ?? "GET")
    || new Headers(init.headers).has("idempotency-key");
  for (let n = 0; ; n++) {
    const timeout = AbortSignal.timeout(o.attemptTimeoutMs);
    try {
      const res = await fetch(url, { ...init, signal: init.signal ? AbortSignal.any([init.signal, timeout]) : timeout });
      if (!RETRYABLE.has(res.status) || n + 1 >= o.attempts) return res;     // success, non-retryable, or out of attempts
      await res.body?.cancel();                                               // free the socket before retrying
      const retryAfter = Number(res.headers.get("retry-after"));             // seconds form; an HTTP date falls back to jitter
      await sleep(retryAfter > 0 ? retryAfter * 1000 : jitter(n, o.baseMs, o.capMs));
    } catch (err: any) {
      const code = err?.cause?.code;                                          // undici wraps socket errors in err.cause
      const transient = NEVER_SENT.includes(code) ||
        (idempotent && (err?.name === "TimeoutError" || MAYBE_SENT.includes(code)));
      if (!transient || n + 1 >= o.attempts) throw err;                       // caller aborts (AbortError) are never retried
      await sleep(jitter(n, o.baseMs, o.capMs));
    }
  }
}

export class CircuitBreaker {
  private failures = 0; private openedAt = 0; private state: "closed" | "open" | "half" = "closed";
  constructor(private threshold = 5, private resetMs = 30_000) {}
  async call<T>(fn: () => Promise<T>, fallback: () => T): Promise<T> {
    if (this.state !== "closed") {
      if (this.state === "half" || Date.now() - this.openedAt < this.resetMs) return fallback();  // fail fast; one probe at a time
      this.state = "half";                                                      // this caller is the probe
    }
    try {
      const out = await fn();
      this.failures = 0; this.state = "closed"; return out;
    } catch (e) {
      if (++this.failures >= this.threshold || this.state === "half") { this.state = "open"; this.openedAt = Date.now(); }
      throw e;
    }
  }
}
// await breaker.call(async () => { const r = await fetchWithRetry(url, init);
//   if (r.status >= 500 || r.status === 429) throw new Error(`upstream ${r.status}`); return r; }, () => degraded());
```

### Production snippet: SDK as the single retry owner, `opossum` above it
The SDK owns retries and per-attempt timeouts; the breaker counts one outcome per logical call and
ignores 400s, which reflect our bug rather than provider health.

```ts
import Anthropic from "@anthropic-ai/sdk";
import CircuitBreaker from "opossum";

const anthropic = new Anthropic({ maxRetries: 2, timeout: 20_000 });   // worst case ~60 s plus backoff

async function complete(prompt: string) {
  const msg = await anthropic.messages.create({
    model: process.env.MODEL!, max_tokens: 512,
    messages: [{ role: "user", content: prompt }],
  });
  return msg.content.flatMap((b) => (b.type === "text" ? [b.text] : [])).join("");
}

const breaker = new CircuitBreaker(complete, {
  timeout: false,                     // the SDK already bounds each attempt
  errorThresholdPercentage: 50,       // open when over half of calls fail...
  volumeThreshold: 20,                // ...once at least 20 calls were seen...
  rollingCountTimeout: 10_000,        // ...in a 10 s rolling window
  resetTimeout: 30_000,               // then allow a probe after 30 s
  errorFilter: (e: unknown) => e instanceof Anthropic.BadRequestError,   // 400s do not count
});
breaker.fallback(() => "The assistant is busy right now. Please try again in a minute.");
breaker.on("open", () => console.warn("breaker open: anthropic"));

export const answer = (prompt: string) => breaker.fire(prompt);
```

### Agent scenario: a tool wrapper that reports failures to the model
Each tool gets its own breaker (the `CircuitBreaker` class above) and its own retry policy; side-effecting
tools retry only with an idempotency key. Whatever happens, the loop receives a tool result it can hand
back to the model.

```ts
type ToolResult = { content: string; isError: boolean };
type ToolSpec = { name: string; idempotent: boolean; run: (args: unknown, signal: AbortSignal, key: string) => Promise<string> };

const breakers = new Map<string, CircuitBreaker>();

export async function callTool(spec: ToolSpec, args: unknown, idemKey: string, deadline: number): Promise<ToolResult> {
  const breaker = breakers.get(spec.name) ?? breakers.set(spec.name, new CircuitBreaker(3, 30_000)).get(spec.name)!;
  const attempts = spec.idempotent ? 3 : 1;                          // never blindly retry a tool that sends or charges
  try {
    const content = await breaker.call(async () => {
      for (let n = 0; ; n++) {
        const remaining = deadline - Date.now();
        if (remaining <= 0) throw new Error("run deadline reached");
        try {
          return await spec.run(args, AbortSignal.timeout(Math.min(remaining, 15_000)), idemKey);
        } catch (e: any) {
          const status = e?.status as number | undefined;
          const transient = status === undefined || status === 429 || status >= 500;
          if (!transient || n + 1 >= attempts) throw e;
          await sleep(jitter(n, 500, 4_000));
        }
      }
    }, () => { throw new Error(`${spec.name} is temporarily unavailable`); });
    return { content, isError: false };
  } catch (e: any) {
    return { content: `Tool ${spec.name} failed: ${e.message}. Try another approach or answer without it.`, isError: true };
  }
}
```

- **Caveat on classification.** Treating a missing `.status` as transient is a simplification; for tools
  that are not idempotent, `attempts` is 1, so an ambiguous timeout is reported instead of repeated.

### Worked scenario: a 12-step agent run during a search-API incident
The search tool is called on 6 of the 12 steps and is failing slowly (every attempt hits a 15 s timeout).

| Layout | Search attempts in the run | Added wall-clock time | What the model sees |
|---|---|---|---|
| Tool wrapper retries 3 and throws, queue retries the step 3 times | 9 on the first search step, then the run fails | ~135 s plus backoff | nothing: the run ends in `failed` |
| Tool wrapper retries 3, failure returned as tool result | 18 (6 steps × 3) | ~4.5 minutes | 6 error results, may stop calling search |
| Same, plus a per-tool breaker with threshold 3 | 9, then fast fallbacks | ~135 s | error results, then "unavailable" on the last 3 search calls |

- **The breaker counts logical calls.** It wraps the whole retry sequence, so 3 exhausted sequences (9
  attempts) trip it. The table assumes the last 3 search calls land inside the 30 s reset window.
- **Probes are full sequences.** A search call after the reset is the half-open probe, and because the
  breaker wraps the retry loop it runs all 3 attempts (~45 s) before re-opening the breaker.

### Worked scenario: how nested retries amplify an outage
One user action during a provider incident, where every attempt fails slowly.

| Retry layout | Attempts per user action | Worst-case wait with 20 s attempt timeouts |
|---|---|---|
| SDK `maxRetries: 2` only | 3 | ~60 s plus backoff |
| SDK 3 attempts × handler loop of 4 | 12 | ~240 s plus backoff |
| Same, plus a queue that re-runs the job 3 times | 36 | ~12 minutes, much of it billed if attempts completed server-side |
| SDK only, under an open breaker | 0 after the trip | about a millisecond, served by the fallback |

Full-jitter sleeps for `fetchWithRetry` defaults (`baseMs: 500`, `capMs: 8_000`, 4 attempts) are drawn
from 0-500 ms, 0-1,000 ms and 0-2,000 ms before attempts 2, 3 and 4.

## Production patterns
- **Tool failures as tool results.** When an agent calls external tools, wrap each one so exhausted
  retries, open breakers and 4xx responses come back to the model as error tool results instead of
  failing the step. Monitor error tool results per tool and whether runs still finish after one.
- **Idempotency keys from run and step.** For tools that create, send or charge, derive the key from run
  id, step and tool-call index, so a retried step or a redelivered job is deduplicated by the downstream
  API. Monitor duplicate side effects per tool.
- **One retry owner per call path.** Decide whether the SDK, the handler or the queue retries, and set
  the others to fail fast (for example SDK `maxRetries: 2` and a queue job with `attempts: 1` for that
  step). Monitor attempts per logical request and provider calls per user action during incidents.
- **Propagate the deadline.** Compute the remaining budget at each hop and pass it down as an
  `AbortSignal.timeout(remaining)` or a per-request `timeout` option, so inner retries never outlive the
  caller. Monitor deadline-exceeded counts per dependency.
- **Rate-based breaker with a fallback per provider.** Configure `opossum` with
  `errorThresholdPercentage`, `volumeThreshold` and `resetTimeout`, and route the fallback to a smaller
  model or cached answer. Alert on `open` events and track time spent open.
- **Bulkhead per dependency.** Wrap each provider in its own `p-limit(n)` sized to the quota; export
  `limit.activeCount` and `limit.pendingCount` as gauges. A growing pending count means the dependency,
  not your service, is the bottleneck.
- **Fault-injection tests.** Point the client at a stub server, or undici's `MockAgent`, that returns 429
  with `Retry-After`, 529, connection resets and stalls, and assert attempt counts, waits and fallbacks
  in CI. Monitor that the suite runs on every change to client code.

## Trade-offs
- **Success rate vs outage load.** Retries raise success during blips but multiply load during outages;
  with jitter and a total-time cap the amplification is bounded, without them a fleet-wide retry storm
  can keep a provider down.
- **Predictable latency vs long completions.** Aggressive per-attempt timeouts cut off slow-yet-successful
  long completions; for large outputs use a time-to-first-token timeout plus an idle timeout rather than
  one short total budget.
- **Protection vs false trips.** A breaker protects your latency and the provider, but a mis-tuned
  threshold trips on normal error noise and takes a healthy dependency offline; thresholds should be
  rate-based over a minimum call volume.
- **Availability vs quality.** Falling back to a cheaper model or cached answer keeps the product up at a
  quality cost users may notice; a chat assistant can degrade, a compliance extraction probably cannot.
- **Retry budget vs individual requests.** A budget bounds fleet-wide amplification, but denies retries
  to some requests during a genuine blip that a per-request policy would have saved; it becomes overhead
  when traffic is too low for amplification to hurt the provider.
- **SDK retry vs your policy.** The built-in retry is simpler and correct for its own error classes, but
  knows nothing about your deadline or breaker; integrate through `maxRetries` and `timeout`, not a
  wrapper loop.

## Pitfalls
- **Nested retry loops** (SDK retries inside your retries inside the queue's retries). Symptom: a single
  user action producing dozens of provider calls during an incident, visible as a cost spike in the same
  hour as the outage.
- **Retrying non-idempotent POSTs on timeout or `ECONNRESET`.** Symptom: duplicate runs, messages or
  charges that each carry a different request id but trace back to one user click.
- **No jitter.** Symptom: provider error rates oscillating in neat waves at your backoff intervals, as
  every client retries at the same instant.
- **Breaker without a minimum volume.** Symptom: the breaker opening at 03:00 because two of the night's
  three requests failed.
- **Ignoring `Retry-After`.** Symptom: 429s keep coming despite backoff, because the server told you its
  window and you retried earlier.
- **Replaying a stream that failed after the first tokens into the same response.** Symptom: clients
  receive an answer that restarts in the middle.

## When to use
- **Baseline, always:** every outbound call to an LLM, embedding, vector, search or SaaS API gets a
  timeout and a classified retry policy; this is not an optimisation.
- **Add a breaker** per dependency when the service has a meaningful fallback or when the dependency's
  failure would otherwise exhaust worker capacity.
- **Add a bulkhead** when one slow dependency can starve the others.
- **Avoid** retrying inside a request path when a queue above it already retries; pick the layer that
  owns retries and make the others fail fast.
- **Measure:** attempts per request, retry success rate, breaker state transitions, p99 latency with and
  without retries, and duplicate side effects.
- **Interviews:** derive the full-jitter formula, explain why breakers trip on rate, and say which layer
  should own retries.

## Role lens
- **Engineer:** Configures SDK `maxRetries` and `timeout`, wraps tool calls with `cockatiel` or `opossum`, passes `AbortSignal`s and idempotency keys, and writes tests that simulate 429s, 529s and timeouts.
- **Architect:** Assigns retry ownership per layer (client, service, queue), defines breaker thresholds and fallbacks per provider, and sets the latency budget each dependency is allowed to consume.
- **FDE:** Tunes timeouts and limits to a customer's negotiated quotas and their egress proxy's behaviour, and explains why their incident dashboard shows retry waves.

## Check yourself
**Q:** During a provider incident your service's p99 latency went from 3 s to 90 s and your bill
tripled for the hour, even though the provider was mostly returning errors. Explain the mechanics and
the fix.
**A:** Each attempt hung for tens of seconds on a provider that accepted connections but failed slowly
(the SDK's 10-minute default timeout never fired, so nothing cut the wait short), then the SDK retried
twice, then the handler's own loop retried three times, and the queue above re-enqueued failures: a
single user action became a dozen or more slow attempts (3 SDK attempts × 4 handler attempts before the
queue even acts), which explains the latency. The cost comes from the attempts that did complete
server-side, and are billed, after your client had already timed out or given up, plus work the queue
re-ran after it had in fact succeeded. The fix is layered: a
per-attempt timeout sized for the call, a single owner of retries (the SDK with `maxRetries` set and
nothing above it), full-jitter backoff honouring `Retry-After`, a total deadline per request, and a
circuit breaker per provider that opens on a high failure rate and fails fast with a fallback or a clear
error. Idempotency keys make any remaining retries safe to repeat.

## Interview questions
### Which errors should an LLM client retry, and which should it never retry?
- **Retry** 408, 429, 500, 502, 503, 504, Anthropic's 529, and connection errors where the request
  never left (`ECONNREFUSED`, connect timeouts).
- **Never retry** 400, 401, 403, 404 or 422, because the same request fails the same way.
- **Retry only if idempotent:** resets and read timeouts after sending, because the server may have
  completed and billed the call. For agent tools that send or charge, that means an idempotency key
  derived from run id, step and tool-call index.

### A tool your agent depends on starts timing out. What should the agent loop do?
- **Bound each call:** per-attempt timeout, a couple of jittered retries only if the tool is idempotent,
  all inside the run's remaining deadline.
- **Report, do not crash:** return an error tool result so the model can try another tool or answer with
  what it has, instead of throwing and letting the queue re-run the whole step.
- **Trip a per-tool breaker** so later steps and other runs get a fast "unavailable" result rather than
  paying the timeout again.

### Why put the circuit breaker outside the retry loop rather than inside it?
- **Outside,** one exhausted retry sequence counts as one logical failure, and an open breaker stops
  retries from starting at all.
- **Inside,** the breaker counts every attempt and trips about three times faster than its threshold
  implies, while the retry loop keeps hitting a breaker that is already open.
- **The deadline still wraps both,** so total time stays inside the caller's budget.

### A breaker keeps opening at night when traffic is low. What is wrong and how do you fix it?
- **It trips on counts without a minimum volume:** two failures out of three requests looks like a 67%
  error rate.
- **Fix:** trip on rate over a minimum call volume, for example `volumeThreshold` in `opossum`, and
  exclude caller bugs such as 400s from the failure count.
- **Then verify** with the history of breaker `open` events against request volume.

### When would you not add a circuit breaker to an outbound dependency?
- **No meaningful fallback and low traffic:** failing fast and failing slowly give the user the same
  error, and the breaker adds tuning burden.
- **A queue already owns failure handling:** background jobs can back off at the queue level instead.
- **Very low call volume,** where a rate threshold cannot gather enough signal to trip correctly.

### Your SDK has `maxRetries: 2` and `timeout: 20_000`. What is the worst-case wall-clock time, and how do you budget it?
- **Each timed-out attempt is retried,** so the worst case is about 20 s × 3 = 60 s plus backoff sleeps.
- **Budget from the caller's deadline:** if the endpoint promises 30 s, lower `timeout`, lower
  `maxRetries`, or pass a per-request signal derived from the remaining deadline.
- **For streaming,** use a first-token timeout plus an idle timeout instead of one total budget.

## Quiz
### Q: Why add random jitter to exponential backoff rather than retrying at fixed exponential intervals?
- [ ] Jitter makes individual retries succeed more often
- [x] Without jitter, clients that failed together retry together, so each wave of retries hits the provider at the same instant
- [ ] Jitter is required by the HTTP specification for 429 responses
- [ ] It reduces the total number of attempts per request
> Backoff alone keeps the schedule synchronised across clients; randomising each sleep spreads the retries over the window so the provider sees a smooth load instead of spikes that re-trigger the overload.

### Q: A circuit breaker is in the open state. What happens to a new call?
- [ ] It is queued until the dependency recovers
- [ ] It is retried with backoff until the reset timeout elapses
- [x] It is rejected immediately (or served a fallback) without contacting the dependency
- [ ] It is sent, and the breaker closes if it succeeds
> Open means "known bad": the breaker fails fast to protect latency and the dependency, and only after the reset timeout does it move to half-open and allow a single probe call.

### Q: Which response should a well-behaved client NOT retry automatically?
- [ ] 503 Service Unavailable with a `Retry-After` header
- [ ] A connection reset before any response bytes arrived
- [x] 400 Bad Request caused by an over-long prompt
- [ ] 429 Too Many Requests from a shared rate limit
> A 400 means the request itself is invalid and will fail identically on every attempt; retrying only adds load and latency, whereas 503, 429 and a reset before any response bytes are transient signals that a later attempt can succeed.

## Use cases & projects
### Use cases
- **Agents behind a shared provider quota.** SDK retries honour `Retry-After` on 429s so bursts of agent
  steps slow down instead of failing.
- **Flaky SaaS tools.** A per-tool breaker turns a CRM or search outage into a fast error tool result the
  model can work around.
- **Side-effecting tools.** Idempotency keys from run and step let an agent retry "create ticket" or
  "send email" without doing it twice.
- **Provider fallback.** A breaker on the primary model routes agent steps to a secondary model while the
  primary is down.

### Mini project
**Fault-injected agent tools (2 hours).**
- **Goal:** show that an agent finishes, or fails cleanly, when its tools misbehave.
- **Steps:** build a 3-tool agent (one read-only search, one side-effecting "create note", one lookup);
  put a stub server in front of the tools that returns 429 with `Retry-After`, 503, resets and 20 s
  stalls on demand; wrap tools with `callTool`; set the SDK to `maxRetries: 2` and nothing above it; run
  20 tasks per fault mode and log attempts per tool, breaker transitions and run outcomes.
- **Done when:** a table shows attempts per run, run success rate and wall-clock time for each fault mode,
  no "create note" side effect happens twice, and runs keep finishing while search is down.
