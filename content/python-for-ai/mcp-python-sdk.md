---
id: mcp-python-sdk
module: python-for-ai
name: MCP Python SDK
kind: tool
difficulty: core
est_minutes: 15
status: reviewed
related:
  - agents/model-context-protocol
  - agents/mcp-authentication-authorization-transport-security
  - agents/tool-schemas-and-argument-validation
  - node-ts-for-ai/mcp-typescript-sdk
  - python-for-ai/async-await
  - python-for-ai/pydantic
  - python-for-ai/type-hints
sources:
  - Official docs: Model Context Protocol
  - Official docs: MCP Python SDK
---

## TL;DR
The MCP Python SDK (the `mcp` package) implements the Model Context Protocol so you can expose
Python functions, data and prompt templates to any MCP-capable host, and call other MCP servers from
Python. Its FastMCP layer turns a decorated, type-hinted function into a tool with a generated JSON
Schema; the lower layers handle JSON-RPC, the initialize handshake and transports (stdio, Streamable
HTTP). This lesson is about the SDK; the protocol itself is taught in the agents module.

## Why it exists
Before MCP, every agent host defined tools its own way: an OpenAI function schema here, a LangChain tool
class there, a custom plugin format in each IDE. A team that wanted its internal search, ticketing and
database tools available in three assistants wrote three integrations and kept them in sync. MCP fixes
the interface: a server advertises tools, resources and prompts over a standard protocol, and any
compliant host can use them.

Implementing that protocol by hand is not trivial, though. A server must speak JSON-RPC 2.0, negotiate a
protocol version and capabilities during `initialize`, describe every tool's input as JSON Schema, route
requests to handlers, handle notifications and cancellation, and do all of this over at least two
transports with different framing. Hand-rolled implementations get the details wrong: schemas drift from
the actual function signature, a stray `print` corrupts the stdio stream, version negotiation is skipped.
The SDK exists so that the protocol is handled once, correctly, and your code is just the tool logic.

## How it works
### Two layers
- **FastMCP (high level).** `FastMCP("name")` creates a server; decorators register capabilities. It
  inspects each function's signature and type hints, builds a Pydantic model from them, and emits that
  model's JSON Schema as the tool's `inputSchema`. The docstring becomes the tool description the LLM
  reads. It does this *because* the function signature is already the source of truth, so deriving the
  schema from it means the two cannot drift. Incoming arguments are validated against the same model
  before your function runs.
- **Low-level `Server`.** Explicit handlers for `list_tools`, `call_tool` and so on, for when you need
  dynamic tool lists or full control over the wire format.

(A separately maintained project, also called FastMCP, extends the original API that was folded into the
official SDK; imports differ, so check which one a codebase uses.)

### Three primitives
- `@mcp.tool()` exposes an action the model may choose to call, for example `create_ticket`.
- `@mcp.resource("tickets://{id}")` exposes readable data addressed by URI, which the host (not
  necessarily the model) decides to load into context.
- `@mcp.prompt()` exposes a reusable prompt template a user can pick.

The split exists *because* control differs: tools are model-controlled, resources are application-
controlled and prompts are user-controlled, and hosts present them differently.

### The session lifecycle
1. The client starts or connects to the server through a transport.
2. `initialize`: client and server exchange protocol versions and capabilities.
3. Discovery: `tools/list`, `resources/list`, `prompts/list`.
4. Use: `tools/call` with JSON arguments; the result is a list of content items (text, image,
   embedded resource), with an `isError` flag for tool-level failures. Recent protocol versions also
   allow structured output validated against an output schema, and the SDK can derive it from a typed
   return value.

On the wire, step 4 is one JSON-RPC request and one response matched by `id`, which is what lets many
calls be in flight on one connection:

```
-> {"jsonrpc":"2.0","id":3,"method":"tools/call",
    "params":{"name":"create_ticket","arguments":{"title":"VPN down","priority":"high"}}}
<- {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\"id\": 1, ...}"}],
    "isError":false}}
```

Note the two failure channels. A malformed request or unknown tool is a JSON-RPC `error` (a protocol
failure the host handles). A tool that ran and failed returns a normal `result` with `isError: true`,
*because* that result is fed back to the model, which can read the message and try something else. Logs
and progress travel as notifications (messages with no `id`) and need no reply.

### Transports
`mcp.run()` defaults to **stdio**: the host spawns your server as a subprocess and exchanges
newline-delimited JSON-RPC messages over stdin and stdout. It is simple and needs no network, which suits
local tools. **Streamable HTTP** (`mcp.run(transport="streamable-http")`) serves the same protocol over
HTTP for remote, multi-client servers and is where authentication becomes your concern. The older
HTTP+SSE transport is deprecated in favour of it. By default a Streamable HTTP server is *stateful*: it
issues a session ID header after `initialize`, and later requests must reach the process holding that
session, so behind a load balancer you need sticky routing. FastMCP can instead run stateless
(`stateless_http=True`), treating each request independently, which scales horizontally but gives up
server-initiated messages that depend on a live session.

### Context, lifespan and server-to-client requests
A tool can accept a `ctx: Context` parameter, which FastMCP injects (and hides from the schema), to send
log messages and progress notifications back to the client during long operations. MCP is
bidirectional, so the same session also lets a server *ask the client* for things the client has
advertised as capabilities: **sampling** (have the host's LLM generate text, so the server needs no model
key of its own) and **elicitation** (ask the user for missing input mid-call). Hosts vary in which of
these they support, so check capabilities rather than assuming.

Shared resources such as a database pool belong in a `lifespan` async context manager passed to
`FastMCP(...)`: it runs once at startup and shutdown and its yielded object is reachable from the
context, *because* opening a connection per tool call repeats setup and leaks under load.

### Client side
`ClientSession` plus a transport helper such as `stdio_client` lets Python code act as a host: initialize,
list tools, call them. Everything is async, built on `anyio`.

## Example
A small ticket server and a client that calls it over stdio.

```python
# server.py
from typing import Literal
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("tickets")
DB: dict[int, dict] = {}

@mcp.tool()
async def create_ticket(title: str, priority: Literal["low", "high"], ctx: Context) -> dict:
    """Create a support ticket. Use only after confirming the issue with the user."""
    tid = len(DB) + 1
    DB[tid] = {"id": tid, "title": title, "priority": priority}
    await ctx.info(f"created ticket {tid}")      # goes to the client, never to stdout
    return DB[tid]

@mcp.resource("tickets://{tid}")
def read_ticket(tid: int) -> str:
    return str(DB.get(int(tid), "not found"))

if __name__ == "__main__":
    mcp.run()                                    # stdio by default

# client.py
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command="python", args=["server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print([t.name for t in (await session.list_tools()).tools])
            res = await session.call_tool("create_ticket", {"title": "VPN down", "priority": "high"})
            print(res.content[0].text, res.isError)

asyncio.run(main())
```

The `priority` hint becomes an `enum` in the schema, so the model sees exactly two legal values, and a
call with `"urgent"` is rejected before your code runs. The generated `inputSchema` for `create_ticket`
is roughly `{"type":"object","properties":{"title":{"type":"string"},"priority":{"enum":["low","high"],
"type":"string"}},"required":["title","priority"]}`; `ctx` is absent because FastMCP injects it. With the
SDK's CLI extra installed, `mcp dev server.py` opens the MCP Inspector, where you can see that schema and
call the tool by hand before any LLM is involved.

## Trade-offs
- Writing one MCP server makes a tool usable from every compliant host, but you pay a process boundary
  and protocol overhead; for a tool used by exactly one in-process agent, a plain function call is simpler.
- stdio is zero-config and has no network attack surface, but runs one server process per client and
  cannot be shared; Streamable HTTP is shareable and remote, at the cost of auth, TLS and deployment.
- FastMCP derives schemas from type hints so they never drift, but anything the hints cannot express
  (cross-field rules) must be enforced in the function; drop to the low-level server for dynamic tools.
- Many small, focused tools give the model precise choices, but every tool's schema occupies context on
  every turn; past a few dozen tools, selection accuracy and token cost both suffer.

## Pitfalls
- Using `print()` in a stdio server; stdout is the protocol channel, and the symptom is the host
  reporting parse errors or the server "disconnecting" right after a log line.
- Docstrings written for humans rather than models; the model picks tools from these descriptions, and
  the symptom is the wrong tool chosen or a destructive tool called without the intended precondition.
- Raising vague exceptions for expected failures; FastMCP catches an exception from a tool and returns it
  as an `isError` result carrying the exception's message, so `raise Exception("failed")` gives the model
  nothing to act on, and the symptom is it retrying the same bad call in a loop. Raise (or return) a
  message that says what was wrong and what to do instead.
- Blocking calls (sync database drivers, `time.sleep`) inside async tools; the whole server stalls, which
  shows up as every tool timing out whenever one slow call is in flight.
- Running a stateful Streamable HTTP server behind a round-robin load balancer; requests land on a
  process that never saw the session, and the symptom is intermittent "session not found" or 4xx errors
  that vanish with a single replica.
- Exposing a Streamable HTTP server without authentication or origin checks; the symptom is discovering
  that anything that can reach the port can invoke your tools.

## When to use
Use the SDK when you want tools or data usable across several agent hosts (IDE assistants, desktop
chat apps, your own agents), or when a customer's platform already speaks MCP. Prefer stdio for local,
single-user tools and Streamable HTTP for shared services behind proper auth. Do not wrap every internal
function as an MCP tool when one application calls it in-process. Measure tool-call error rate, latency
per tool and how often the model picks the intended tool. For the protocol design, security model and
when MCP is the right boundary, see the agents module's MCP lessons; in interviews, expect to explain the
lifecycle and why stdout matters on stdio.

## Role lens
- **Engineer:** Writes FastMCP tools with precise type hints and model-facing docstrings, logs through
  the context or stderr, and tests servers with a real client session.
- **Architect:** Decides which capabilities become shared MCP servers versus in-process functions, which
  transport each uses, and where authentication and authorization are enforced.
- **FDE:** Wraps a customer's internal APIs as an MCP server so their existing assistant hosts can use
  them without bespoke integrations per tool.

## Check yourself
**Q:** A stdio MCP server works when you run it by hand but the host shows "connection closed" as soon
as one particular tool is called. What do you suspect, and how do you confirm it?
**A:** On stdio, stdout carries the JSON-RPC stream, so anything else written there, a `print`, a
library's progress bar, a warning sent to stdout, corrupts the framing and the host drops the session.
Running by hand hides this because you are not parsing the stream. Confirm by checking the host's MCP
logs for a parse error, grepping that tool's code path for prints, or running it under a real
`ClientSession`. Fix by logging to stderr or through `ctx` logging, and redirecting noisy libraries.
A second suspect is the process itself dying in that code path (a native-extension crash, `sys.exit`,
an out-of-memory kill), which the server's stderr or exit code would show; an ordinary Python exception
inside a FastMCP tool does not do this, because it is returned to the client as an `isError` result.

## Quiz
### Q: How does FastMCP produce a tool's input schema?
- [ ] You must write the JSON Schema by hand in the decorator
- [x] It builds a Pydantic model from the function's type hints and emits that model's JSON Schema
- [ ] The host infers the schema by calling the tool with sample arguments
- [ ] It parses the docstring for parameter types
> FastMCP inspects the signature and type hints, so the schema cannot drift from the code. The docstring
> supplies the description the model reads, not the types.

### Q: Why should a stdio MCP server never write logs to stdout?
- [ ] stdout is buffered and logs would arrive out of order
- [ ] The protocol forbids any logging during a session
- [x] stdout carries the JSON-RPC messages, so extra output corrupts the stream the host parses
- [ ] Hosts discard stdout, so the logs would be lost
> On stdio the two processes talk over stdin and stdout. Logging belongs on stderr or in protocol log
> notifications through the context; stdout output breaks message framing.

### Q: When is Streamable HTTP the better transport than stdio?
- [ ] When the tool runs on the same laptop as a single desktop host
- [ ] When you want to avoid writing any authentication code
- [ ] When the server must print debugging output freely
- [x] When one server should be shared by many clients or run remotely as a service
> stdio spawns a private subprocess per client, which is ideal locally. A shared or remote server needs
> HTTP, which also brings authentication and network security into scope.
