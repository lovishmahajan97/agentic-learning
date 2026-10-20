---
id: mcp-typescript-sdk
module: node-ts-for-ai
name: MCP TypeScript SDK
kind: tool
difficulty: core
est_minutes: 15
related:
  - agents/model-context-protocol
  - agents/mcp-authentication-authorization-transport-security
  - python-for-ai/mcp-python-sdk
  - node-ts-for-ai/runtime-schema-validation
  - node-ts-for-ai/openai-anthropic-node-sdks
  - node-ts-for-ai/streams-and-backpressure
  - agents/typed-tool-contracts
  - agent-reliability/tool-permissions-and-least-privilege
sources:
  - Official docs: Model Context Protocol specification
  - Official docs: MCP TypeScript SDK (@modelcontextprotocol/sdk)
  - Official docs: Zod
---

## TL;DR
`@modelcontextprotocol/sdk` is the reference TypeScript implementation of the Model Context Protocol:
a server side that turns Zod-typed handlers into `tools/list`, `tools/call`, resource and prompt
endpoints over JSON-RPC, and a client side that connects to any server, discovers its capabilities
and calls them. Transports are pluggable: stdio for local subprocesses, Streamable HTTP for remote
servers. The protocol lesson in the agents module explains *why* MCP exists; this one is about what
the SDK does for you and where it bites.

## Why it exists
MCP is a wire protocol: JSON-RPC 2.0 messages with an `initialize` handshake, capability negotiation,
a fixed set of request methods and notifications, and two transport bindings with their own framing
and session rules. Implementing that by hand for one server is a week of work and a long tail of
interoperability bugs: a missing `initialized` notification, a tool whose JSON Schema the host rejects,
an HTTP endpoint that does not honour the session header, a stdout log line that corrupts the stream.
Every server author would repeat the work and every host would discover a different subset of
mistakes. The SDK exists so that a server author writes handlers and schemas, and a host author writes
`connect`, `listTools` and `callTool`, while the protocol machinery, versioning and transport details
are shared code maintained alongside the specification.

The naive alternatives both lose. Writing a bespoke HTTP API for your tools and asking each agent to
integrate it reintroduces the per-framework connector problem MCP was created to remove, and it offers
nothing for resources, prompts or server-initiated requests. Using a framework's proprietary tool
format works inside that framework only. The SDK's design choice, schemas as Zod values that become
JSON Schema on the wire and TypeScript types in the handler, is the same single-source-of-truth idea
as the schema-validation lesson, applied at a protocol boundary: the model sees the schema the
handler enforces, and the host can validate what it sends.

## How it works
### Server side: two levels
`McpServer` (from `@modelcontextprotocol/sdk/server/mcp.js`) is the high-level API. You construct it
with a name and version, then register capabilities: `registerTool(name, { description, inputSchema,
outputSchema? }, handler)`, `registerResource(name, uriOrTemplate, metadata, reader)` and
`registerPrompt(name, { argsSchema }, builder)`. `inputSchema` is a Zod shape; the SDK converts it to
JSON Schema for `tools/list` and validates incoming `tools/call` arguments against it before your
handler runs, so the handler receives a typed object. A handler returns `{ content: [...] }` where
content items are `text`, `image`, `audio` or embedded resources, optionally `structuredContent` when
an `outputSchema` is declared, and optionally `isError: true`. That flag is the important design
decision: a *tool-level* failure (order not found, validation of business rules failed) is returned as
a normal result with `isError`, so the model sees the message and can recover, whereas *throwing* from
a handler becomes a JSON-RPC error that signals a broken call to the host. Conflating the two means
either the model never learns why a call failed or the host treats a routine "not found" as an outage.

`Server` (the low level) exposes `setRequestHandler(RequestSchema, handler)` for every method in the
spec and is what `McpServer` is built on; drop to it for custom capabilities, dynamic tool lists with
`tools/list_changed` notifications, or when you need to intercept raw requests. Both levels implement
the handshake, capability advertisement, progress and logging notifications and cancellation for you.

### Transports
A transport moves JSON-RPC messages; the server object is transport-agnostic. `StdioServerTransport`
reads requests from `stdin` and writes responses to `stdout`, which is why a stdio server must never
`console.log`: anything on stdout that is not a JSON-RPC message corrupts the stream. Log to `stderr`
or via the protocol's logging notification. Stdio is the right choice for local tools launched by an
IDE or desktop host, *because* the host owns the process, its environment and its lifetime, and no
network, authentication or multi-tenancy question arises.

`StreamableHTTPServerTransport` serves remote clients from a single endpoint, conventionally `/mcp`:
the client POSTs JSON-RPC requests, the server replies either with a JSON body or with a server-sent
event stream for responses that take time or carry notifications, and an optional GET opens a
long-lived stream for server-initiated messages. Sessions are identified by an `Mcp-Session-Id`
header that the server issues during `initialize`; the SDK supports stateful mode (a
`sessionIdGenerator`, one transport instance per session, kept in a map) and stateless mode (no
session id, every request self-contained). Stateless is what you want behind a load balancer with
several replicas, since a stateful session pins a client to the replica holding its transport; the
cost is that server-initiated requests and resumable streams are unavailable. The older HTTP+SSE
transport is still shipped for compatibility but is superseded. Remote servers authenticate with
OAuth 2.1 per the specification's authorization section; the SDK ships helpers for both sides, and the
dedicated security lesson covers the trust decisions.

### Client side
`Client` (from `@modelcontextprotocol/sdk/client/index.js`) is constructed with a name and version
and connected to a transport: `StdioClientTransport({ command, args, env })` spawns the server as a
child process, `StreamableHTTPClientTransport(url)` talks to a remote one. `connect` performs the
handshake and records the server's capabilities. From there `listTools()`, `callTool({ name,
arguments })`, `listResources()`, `readResource({ uri })`, `listPrompts()` and `getPrompt()` map
one-to-one onto protocol methods and return typed results. The client is also where a host handles
server-initiated requests: sampling (the server asks the host's model for a completion), roots (the
host tells the server which directories it may touch) and elicitation (the server asks the user for
input), each opt-in through capability declarations.

### Bridging to a model
Nothing in the SDK calls a language model. The host does the mapping: each tool from `listTools()`
becomes a provider tool definition (`inputSchema` is already JSON Schema, so it drops into Anthropic's
`input_schema` or OpenAI's `parameters`); a `tool_use` or `tool_calls` entry in the model's reply
becomes `callTool`; the returned `content` becomes the tool result sent back. Name collisions across
servers are the host's problem, usually solved with a server prefix. Anthropic's API additionally
offers a server-side MCP connector that performs this bridging for remote servers inside the request,
which removes the loop from your code but also removes your chance to filter or approve calls, so it
suits trusted servers and simple hosts. Keep the exposed tool set small: a host that forwards forty
tools from three servers pays in tokens and in tool-selection accuracy on every turn.

## Example
A stdio server exposing one tool with a Zod-typed input, and a host that discovers the tool, hands it
to Claude, and routes the resulting `tool_use` back through `callTool`. The server logs to stderr
because stdout is the wire. Written against the 1.x `registerTool` API; older minors used `tool()`.

```ts
// server.ts — one tool over stdio. Nothing but JSON-RPC may touch stdout.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { db } from "./db";

const server = new McpServer({ name: "orders", version: "1.0.0" });
server.registerTool(
  "lookup_order",
  { description: "Fetch an order by id", inputSchema: { orderId: z.string().regex(/^ord_\w+$/) } },
  async ({ orderId }) => {                                    // typed from the Zod shape, already validated
    const order = await db.orders.find(orderId);
    if (!order) return { isError: true, content: [{ type: "text", text: `no order ${orderId}` }] };
    return { content: [{ type: "text", text: JSON.stringify(order) }] };
  },
);
console.error("orders server starting");                      // stderr, never console.log
await server.connect(new StdioServerTransport());

// host.ts — discover tools, give them to the model, route tool_use back to the server.
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import Anthropic from "@anthropic-ai/sdk";

const mcp = new Client({ name: "host", version: "1.0.0" });
await mcp.connect(new StdioClientTransport({ command: "node", args: ["dist/server.js"] }));
const { tools } = await mcp.listTools();
const claudeTools: Anthropic.Tool[] = tools.map((t) => ({
  name: t.name, description: t.description ?? "", input_schema: t.inputSchema as Anthropic.Tool.InputSchema,
}));
const anthropic = new Anthropic();
const reply = await anthropic.messages.create({
  model: "claude-opus-5-5", max_tokens: 1024, tools: claudeTools,
  messages: [{ role: "user", content: "What is the status of ord_42?" }],
});
for (const block of reply.content) {
  if (block.type !== "tool_use") continue;
  const result = await mcp.callTool({ name: block.name, arguments: block.input as Record<string, unknown> });
  console.log(result.isError ? "tool error" : "ok", result.content);  // becomes a tool_result next turn
}
```

## Trade-offs
- The SDK handles handshake, schemas and transports so a server is a few dozen lines, but you inherit
  its release cadence; the high-level API has been renamed between minors, so pin versions.
- Stdio is simple and secure by construction, yet it ties the server's lifetime to one host process;
  the moment two hosts or a remote user need the same server, Streamable HTTP and auth are required.
- Stateful HTTP sessions enable server-initiated requests and stream resumption, while stateless mode
  scales horizontally with no affinity; choose stateless unless you actually use sampling or
  elicitation.
- Returning `isError` results keeps the model in the loop on failures, but also lets a server leak
  internal detail into model context; format tool errors for the model, not from stack traces.
- A server-side MCP connector in the model API removes the bridging loop, at the cost of losing the
  host's ability to filter, approve or rate-limit calls; use it for trusted servers only.
- Exposing many tools through MCP is cheap to build and expensive per turn in tokens and selection
  accuracy; curate per host rather than forwarding everything.

## Pitfalls
- A `console.log` in a stdio server; the symptom is the host reporting a JSON parse error or hanging
  at `initialize` while the server appears to run fine.
- Throwing from a tool handler for expected failures; the symptom is the agent loop aborting with a
  protocol error instead of the model reading "not found" and trying something else.
- Deploying a stateful Streamable HTTP server behind a round-robin balancer; the symptom is
  intermittent "session not found" errors after the first request.
- Registering tools with vague descriptions or untyped `z.any()` inputs; the symptom is the model
  calling the wrong tool or passing arguments the handler cannot use.
- Forgetting to prefix tool names when a host connects to several servers; the symptom is one server's
  `search` silently shadowing another's.
- Building a public HTTP server with no authentication because the local stdio version needed none;
  the symptom is anyone with the URL calling your tools.
- Leaving the child process running when the host exits; the symptom is orphaned server processes
  accumulating on developer machines.

## When to use
Write an MCP server when a system should be usable by more than one host or agent framework, when a
customer or vendor already speaks MCP, or when you want tools, resources and prompts described once in
a typed way. Use stdio for local developer tooling and anything launched by an IDE; use Streamable
HTTP, stateless where possible, with OAuth, for shared or remote servers. Write a client when your own
agent must consume third-party servers. Do not reach for MCP for a single in-process tool used by one
service; a plain function with a Zod schema is less code. Measure the number of tools exposed per
host, tool-call error rate split by `isError` versus protocol error, and p95 call latency. Interviews
ask what the SDK does and does not do (it never calls a model), the `isError`-versus-throw
distinction, and why stdout is sacred.

## Role lens
- **Engineer:** Registers tools with Zod shapes and useful descriptions, returns `isError` results for
  expected failures, picks the transport, and writes the host loop that bridges tools to the model.
- **Architect:** Decides which systems get MCP servers, stateful versus stateless HTTP, how servers
  are authenticated and versioned, and which hosts may see which tools.
- **FDE:** Wraps a customer's internal API as an MCP server so their existing hosts can use it, and
  audits the servers they already run for stdout logging, missing auth and oversized tool sets.

## Check yourself
**Q:** A team ported their stdio MCP server to Streamable HTTP, deployed three replicas behind a load
balancer, and now see intermittent failures after the first request. What is wrong and what are their
options?
**A:** The server runs in stateful mode: `initialize` creates a session on one replica and returns an
`Mcp-Session-Id`, and the next request carrying that id lands on a different replica that has no such
transport, so it fails. Options: switch to stateless mode (no session id generator), which makes each
request self-contained and scales freely at the cost of server-initiated requests and resumable
streams; keep stateful mode and enable session affinity at the balancer, accepting uneven load and
session loss on replica restarts; or externalise session state so any replica can serve it, which the
SDK does not do for you and is rarely worth it. Most tool servers use neither sampling nor
elicitation, so stateless is the usual answer, together with OAuth now that the server is reachable.

## Quiz
### Q: Why must an MCP server using the stdio transport never write logs with `console.log`?
- [ ] Because the SDK disables `console` for performance reasons
- [x] Because stdout is the JSON-RPC channel to the host, so any non-protocol output corrupts the message stream
- [ ] Because logs would be visible to the model and leak information
- [ ] Because stdio servers run without a TTY and `console.log` throws
> The transport frames messages on stdout; a stray line breaks parsing on the host side, often showing
> up as a hang during `initialize`. Logs go to stderr or to the protocol's logging notifications. The
> model never sees server logs unless a tool returns them.

### Q: A tool handler cannot find the requested record. What should it do?
- [ ] Throw an error so the SDK returns a JSON-RPC error to the host
- [ ] Return an empty `content` array and let the model infer the failure
- [x] Return a normal result with `isError: true` and a message, so the model sees why the call failed and can adapt
- [ ] Call `process.exit(1)` to signal the failure unambiguously
> `isError` marks a tool-level failure the model is expected to handle; throwing signals a broken
> protocol call that typically aborts the agent step. An empty result hides the cause. Exiting kills
> the server for every other call.

### Q: What does the MCP TypeScript SDK not do for a host application?
- [ ] Perform the `initialize` handshake and capability negotiation
- [ ] Convert a server's Zod schemas into JSON Schema for `tools/list`
- [x] Call a language model and decide which tool to invoke
- [ ] Spawn a stdio server as a child process and talk to it
> The SDK implements the protocol on both sides and manages transports. Bridging to a model, mapping
> tool definitions into the provider's format, sending the model's tool call to `callTool` and
> returning the result, is the host's code, or a provider-side connector if one is used.
