---
id: model-context-protocol
module: agents
name: Model Context Protocol (MCP)
kind: tool
difficulty: core
est_minutes: 14
related:
  - agents/mcp-authentication-authorization-transport-security
  - agents/a2a-protocol
  - agents/tool-calling
  - agents/typed-tool-contracts
  - agents/tool-selection-accuracy
  - python-for-ai/mcp-python-sdk
  - node-ts-for-ai/mcp-typescript-sdk
  - agent-reliability/tool-permissions-and-least-privilege
sources:
  - Official docs: Model Context Protocol specification
  - Official docs: MCP Python SDK
---

## TL;DR
MCP is an open protocol, released by Anthropic in late 2024, that standardizes how an AI application
connects to external capability servers. A server exposes tools, resources and prompts over JSON-RPC;
any MCP-capable host can discover and call them, so an integration is written once instead of once
per framework. It solves the plumbing of connecting models to systems; it does not remove the need for
authentication, trust decisions about third-party servers, or keeping the tool set small.

## Why it exists
Before MCP, every agent framework and every chat application had its own plugin format. A vendor who
wanted their database, ticketing system or document store usable by agents wrote one connector per
framework, and every application team re-implemented the same connectors on their side: the classic
N applications times M integrations problem. The connectors also disagreed on basics such as how to
describe a tool, how to stream a result, how to expose a document for the model to read, and how to
authenticate.

The obvious alternative, point the model at an OpenAPI spec, does not work well in practice. An API
spec describes hundreds of endpoints for programmers, not a curated handful of actions with the
descriptions a model needs to select among them, and it says nothing about resources the host should
put into context, prompts a user can invoke, or how a server can ask the host's model for help. The
framework-specific registries solved the model-facing problem but locked the integration to one
framework.

MCP separates the two sides with a contract. The server side packages a system as tools with JSON
Schema inputs, resources with URIs and prompt templates; the client side is a small, generic piece of
the host that speaks the protocol. Once both exist, a new server works with every host and a new host
works with every server, which is why the ecosystem grew quickly and why customers increasingly already
have MCP servers you are expected to plug into.

## How it works
### Roles
A **host** is the application (an IDE, a chat app, your own agent). Inside the host, one **client**
holds one connection to one **server**. A server exposes capabilities; it may be a local process or a
remote service. The host can hold many clients, and it decides what the model sees from each.

### The primitives
- **Tools** are model-controlled: `tools/list` returns each tool's name, description and
  `inputSchema` (JSON Schema); `tools/call` runs one and returns a list of content blocks (text,
  image, embedded resource) plus an `isError` flag. These map one-to-one onto a provider's tool
  definition and `tool_result`, which is the whole point.
- **Resources** are application-controlled data identified by URIs (`file://…`, `db://…`), listed and
  read by the host, optionally with change subscriptions. The host decides what goes into the model's
  context; the model does not fetch resources on its own.
- **Prompts** are user-controlled templates (`prompts/list`, `prompts/get`), typically surfaced as
  slash commands or menu items.
- **Server-to-client requests**: *sampling* lets a server ask the host's model for a completion
  without holding its own API key; *roots* let the host tell a server which filesystem roots it may
  touch; newer revisions add *elicitation*, where a server asks the user for input mid-operation.
  Notifications such as `tools/list_changed` let a server announce that its capabilities changed.

### The wire
Messages are JSON-RPC 2.0. A session starts with an `initialize` request carrying the protocol version
and each side's capabilities, then an `initialized` notification, then normal operation. Two transports
are standard: **stdio**, where the host spawns the server as a subprocess and talks over its standard
streams (simple, local, inherits the host's environment), and **Streamable HTTP**, for remote servers,
which uses HTTP POST with optional server-sent events for streaming and replaced the earlier
HTTP-plus-SSE transport. Remote servers authenticate with OAuth 2.1 under the specification's
authorization section; a separate lesson covers that in depth.

### Plugging into the agent loop
The host lists tools from each connected server, converts them to the model provider's format (name,
description, `input_schema`), usually prefixes names with the server name to avoid collisions, and
passes the result in `tools`. When the model emits a `tool_use`, the host routes it to the right
client's `tools/call`, converts the returned content blocks into a `tool_result`, and sets `is_error`
from `isError`. The model never knows MCP is involved. Some providers can do this hop server-side:
the Anthropic API accepts `mcp_servers` plus an `mcp_toolset` entry so the connection is made from the
provider's side, and the Python SDK ships helpers that turn MCP tools into tool-runner tools.

### Compared with the alternatives
Plain in-process function tools remain the right choice for a single codebase with a few tools; MCP
earns its hop when the same capability must reach several hosts, come from a third party, or run in a
different process or language. OpenAPI describes HTTP APIs; MCP servers often *wrap* an OpenAPI
surface into a curated, well-described tool set. A2A is a different layer, agents delegating tasks to
agents rather than hosts calling tools; the two compose rather than compete.

### Sharp edges
Each server tends to expose dozens of tools, so three servers can put a hundred definitions into
context, with the token cost and selection-accuracy loss that implies; allow-list the tools you need.
A server's tool descriptions become part of your prompt, so a malicious or compromised server can
inject instructions through them ("tool poisoning"), and a server can change descriptions after you
approved it; pin versions and review descriptions like code. Results are untrusted content. Local stdio
servers inherit whatever environment and credentials the host process has, so run them with least
privilege. And every call is an extra hop with its own latency and failure modes.

## Example
A Python host that connects to a local reference filesystem server over stdio, converts its tools to
the provider format with a namespace prefix, and runs the standard agent loop, routing each
`tool_use` back to `session.call_tool`. The `mcp` package is the official Python SDK.

```python
import asyncio, anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = StdioServerParameters(command="npx", args=["-y", "@modelcontextprotocol/server-filesystem",
                                                    "/data/reports"])          # sandboxed root

async def main(question: str):
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()                                   # version + capability handshake
            listed = await session.list_tools()
            tools = [{"name": f"fs__{t.name}", "description": t.description or "",
                      "input_schema": t.inputSchema} for t in listed.tools]   # 1:1 mapping, namespaced
            client = anthropic.Anthropic()
            messages = [{"role": "user", "content": question}]
            for _ in range(12):                                          # step budget
                resp = client.messages.create(model="claude-opus-5-5", max_tokens=8192,
                                              tools=tools, messages=messages)
                messages.append({"role": "assistant", "content": resp.content})
                if resp.stop_reason != "tool_use":
                    return "".join(b.text for b in resp.content if b.type == "text")
                results = []
                for b in resp.content:
                    if b.type != "tool_use":
                        continue
                    r = await session.call_tool(b.name.removeprefix("fs__"), arguments=b.input)
                    text = "\n".join(c.text for c in r.content if getattr(c, "type", "") == "text")
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": text[:8000], "is_error": bool(r.isError)})
                messages.append({"role": "user", "content": results})
            raise RuntimeError("step budget exhausted")

print(asyncio.run(main("Which reports under /data/reports mention Q3 churn, and what do they say?")))
```

## Trade-offs
- Write-once integrations and a large ecosystem, against an extra process or network hop and a protocol
  surface to secure; for one in-house tool in one codebase, a direct function is simpler and faster.
- Servers bring their own tool descriptions, which saves work but hands a third party influence over
  your prompt; curating and allow-listing tools is the price of using someone else's server.
- stdio servers are simple and private but tied to the host machine; Streamable HTTP enables shared
  remote servers at the cost of real authentication, multi-tenancy and network policy.
- Dynamic discovery (`tools/list_changed`) keeps tool sets current but complicates prompt caching and
  approval; pin server versions and tool sets where predictability matters more than freshness.

## Pitfalls
- Connecting several servers and exposing every tool; the symptom is a jump in tokens per request and a
  drop in selection accuracy that looks like a model regression.
- Trusting tool descriptions and results from a third-party server; the symptom is the agent taking an
  action that a description or a result told it to, not the user.
- Running stdio servers with the developer's full environment; the symptom is a server reading
  credentials or files it never needed, discovered during a security review.
- Ignoring `isError` and mapping every result to a successful `tool_result`; the symptom is the model
  narrating error text as if it were data.
- Building against the deprecated HTTP-plus-SSE transport; the symptom is new remote servers that will
  not connect.
- Assuming two servers cannot both have a tool named `search`; the symptom is calls routed to the wrong
  server until names are prefixed.

## When to use
Reach for MCP when a capability must be shared across hosts, comes from a vendor or another team, or
lives in another process or language, and when a customer already runs MCP servers you are expected to
integrate with. Prefer in-process tools for a small set owned by one codebase, and wrap an existing
OpenAPI service in an MCP server only after curating which operations the model should see. Measure
tools in context per request, per-server call latency and error rate, and selection accuracy with the
combined tool set, since adding a server changes it. Day to day, the work is allow-listing, namespacing,
reviewing descriptions and configuring credentials with least privilege. In interviews, expect "what
problem does MCP solve and what does it not solve"; the strong answer names the N-times-M problem, the
three primitives, the two transports, and the trust and tool-bloat caveats.

## Role lens
- **Engineer:** Writes and consumes servers with the official SDKs, maps tools to the provider format
  with namespaces, handles `isError`, and allow-lists which tools reach the model.
- **Architect:** Decides which capabilities are exposed through MCP, where the trust boundary sits for
  third-party servers, the authentication model for remote servers, and whether a gateway fronts them.
- **FDE:** Connects a customer's systems through servers running inside their environment, reviews
  every exposed tool and credential with the customer, and evaluates selection accuracy before go-live.

## Check yourself
**Q:** A customer wants to plug three vendor-provided MCP servers into their internal agent this week.
What do you review before enabling them, and in what order?
**A:** First the tool inventory: list every tool from each server, read the descriptions as if they
were code, and allow-list the handful the use case needs, because the full set inflates context and
invites selection errors. Then trust and authentication: which transport, what credentials each server
receives and with what scopes, whether a remote server uses the specification's OAuth flow, and what
data leaves the customer's network. Then runtime policy: results from these servers are untrusted
content, so consequential actions that follow them need approval gates, and stdio servers run with least
privilege. Then stability: pin server versions so descriptions cannot change silently, prefix names to
avoid collisions, and log every call. Finally measure: re-run the selection eval with the combined tool
set and check latency and error rates per server before go-live.

## Quiz
### Q: Which MCP primitive is "model-controlled", meaning the model decides when to invoke it?
- [ ] Resources
- [ ] Prompts
- [x] Tools
- [ ] Roots
> Tools are listed to the model and called at its discretion. Resources are chosen by the host
> application for inclusion in context, prompts are invoked by the user, and roots are host-to-server
> configuration about allowed filesystem locations.

### Q: What does the model see when an agent uses tools from an MCP server?
- [ ] JSON-RPC messages that it must format correctly
- [x] Ordinary tool definitions and results in the provider's format; MCP is invisible to the model
- [ ] A list of server URLs it can call directly
- [ ] Only the resources, since tools are executed by the server
> The host converts `tools/list` output into provider tool definitions and routes `tool_use` blocks to
> `tools/call`. The protocol lives entirely between host and server.

### Q: What is the most common operational problem when connecting several MCP servers to one agent?
- [ ] JSON-RPC message size limits
- [x] Tool bloat: too many definitions in context, raising token cost and lowering selection accuracy, plus trust exposure to each server's descriptions
- [ ] Servers cannot run on the same machine
- [ ] The model refuses to use more than one server
> Each server tends to expose many tools, and every definition is rendered into the prompt. Allow-listing
> the needed tools and reviewing descriptions addresses both the cost and the trust problem.
