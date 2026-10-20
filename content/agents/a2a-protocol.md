---
id: a2a-protocol
module: agents
name: A2A protocol (Agent2Agent)
kind: concept
difficulty: core
est_minutes: 14
related:
  - agents/model-context-protocol
  - agents/mcp-authentication-authorization-transport-security
  - agents/multi-agent-patterns
  - agent-reliability/structured-event-streams
  - agent-reliability/conversation-execution-identifiers
  - backend-fundamentals/rest-graphql-api-design
  - serving-inference/streaming
sources:
  - Official docs: A2A (Agent2Agent) protocol specification
  - Official docs: Model Context Protocol specification
---

## TL;DR
A2A is an open protocol (announced by Google in 2025, later moved to the Linux Foundation) for one
agent to discover another agent, hand it a task, and follow that task to completion over HTTP without
seeing inside it. Where MCP connects an agent to *tools* with fixed schemas, A2A connects an agent to
*other agents* that are opaque, long-running and may ask clarifying questions. Its core objects are the
Agent Card for discovery and the Task with a state machine for the work itself.

## Why it exists
Once organisations build more than one agent, the agents need to talk to each other: a procurement
agent must ask a legal-review agent whether a contract is acceptable; a travel agent must ask an
airline's booking agent for seats. The naive approach, "wrap the other agent as a tool", fails on three
counts. A tool call is a synchronous function with a typed schema, but an agent's job may run for
minutes, require a follow-up question ("which cardholder?"), produce several artifacts, and fail
halfway. Tool schemas also assume the caller knows the exact parameters, whereas another agent's
capabilities are best described in natural language and may change daily. And each pair of teams would
otherwise invent its own REST shape, auth scheme and streaming format, so every new integration costs
a bespoke adapter.

The alternative of sharing memory or prompts between agents fails for a different reason: agents built
by different vendors cannot and should not expose internal state, tool lists or system prompts to each
other. A2A was designed around opacity: agents cooperate by exchanging messages and artifacts about a
task, never by inspecting one another. That makes the protocol usable across organisational boundaries
and lets a team swap an agent's internals without breaking its callers.

## How it works
### Discovery: the Agent Card
Each A2A server publishes a JSON **Agent Card** at a well-known URL (early versions used
`/.well-known/agent.json`; later revisions renamed it to `/.well-known/agent-card.json`). The card
declares the agent's name and description, its service endpoint URL, the protocol version, supported
input and output modes (text, files, structured data), capabilities such as streaming and push
notifications, a list of **skills** (each with an id, description and examples) and the security
schemes it accepts, described in the OpenAPI style. Skills are described in prose *because* the client
is itself an LLM-driven agent: it selects a remote agent the way it would select a tool, by reading
descriptions, not by matching a function signature.

### The Task as the unit of work
A client sends a **Message** (role plus one or more **Parts**: text, file or structured data). The
server either answers directly with a message or opens a **Task** with an id and a `contextId` that
groups related tasks. The task carries a status with a state from a fixed lifecycle: `submitted`,
`working`, `input-required`, `completed`, `failed`, `canceled`, plus `rejected` and `auth-required` in
later revisions. The `input-required` state is the design choice that distinguishes A2A from a tool
call: the remote agent can pause and ask the caller for more information, and the caller continues the
same task by sending another message with the same task id. Outputs are returned as **Artifacts**, each
a list of parts, so a task can produce a report, a spreadsheet and a JSON summary as separate items.

### Transport and messaging
The reference binding is JSON-RPC 2.0 over HTTPS. Core methods include `message/send` for
request-response, `message/stream` for a Server-Sent Events stream of task status and artifact updates,
`tasks/get` to poll, `tasks/cancel`, and `tasks/pushNotificationConfig/set` to register a webhook so
long tasks can notify the caller without an open connection. Later protocol revisions added gRPC and
plain HTTP+JSON bindings with the same data model. Streaming exists *because* agent tasks are slow and
users want partial output; push notifications exist *because* a task may outlive any HTTP connection.

### Security
A2A deliberately reuses web standards: the Agent Card advertises which schemes it accepts (OAuth 2,
API key, HTTP bearer, OpenID Connect), credentials travel in standard HTTP headers, and the task body
never carries them. The server authenticates the *calling agent* (or the user it acts for) and makes
its own authorization decisions per skill. Because agents are opaque, the caller never receives the
remote agent's internal tool results, only the artifacts it chooses to return.

### How it relates to MCP
The two protocols are complementary and are commonly used together. MCP is vertical: an agent calling
tools and reading resources with JSON schemas, where the server executes a deterministic function. A2A
is horizontal: an agent delegating to a peer that reasons, may take minutes, may ask questions and
returns artifacts. A single agent typically speaks MCP to its tools and A2A to other agents. Both use
JSON-RPC 2.0, both support SSE streaming, and both lean on OAuth for HTTP security, so the operational
skills transfer.

## Example
A minimal client with `httpx`: read the Agent Card, pick a skill, send a message, poll until the task
reaches a terminal state, and handle `input-required`. Field names follow the 0.2-era JSON-RPC
binding; check the version in the card before relying on them.

```python
import httpx, time, uuid

BASE = "https://billing-agent.example.com"
TOKEN = "..."                                             # obtained per the card's securitySchemes
card = httpx.get(f"{BASE}/.well-known/agent-card.json").json()
print(card["name"], "skills:", [s["id"] for s in card["skills"]])
assert card["capabilities"].get("streaming") is not None

def rpc(method, params):
    r = httpx.post(card["url"], timeout=30,
                   headers={"Authorization": f"Bearer {TOKEN}"},
                   json={"jsonrpc": "2.0", "id": str(uuid.uuid4()),
                         "method": method, "params": params})
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]

def user_message(text, task_id=None):
    msg = {"role": "user", "messageId": str(uuid.uuid4()),
           "parts": [{"kind": "text", "text": text}]}
    if task_id:
        msg["taskId"] = task_id                          # continue the same task
    return {"message": msg}

task = rpc("message/send", user_message("Refund invoice INV-2291, duplicate charge"))
while task["status"]["state"] in ("submitted", "working"):
    time.sleep(2)
    task = rpc("tasks/get", {"id": task["id"]})
if task["status"]["state"] == "input-required":
    question = task["status"]["message"]["parts"][0]["text"]   # e.g. "Which card ending?"
    task = rpc("message/send", user_message("Card ending 4242", task_id=task["id"]))
print(task["status"]["state"], [a.get("name") for a in task.get("artifacts", [])])
```

A real client would use `message/stream` to receive status and artifact events incrementally instead
of polling, and would persist `task["id"]` and `contextId` so the conversation survives a restart.

## Trade-offs
- Opaque agents let vendors cooperate without exposing internals, but the caller cannot inspect why a
  task failed beyond the message it is given; debugging needs correlation IDs on both sides.
- A standard task lifecycle removes bespoke adapters, yet every integration still needs semantic
  agreement on what a skill does; the protocol standardises the envelope, not the meaning.
- Streaming and push notifications handle long tasks gracefully, at the cost of running a webhook
  endpoint and handling retries and ordering, which simple request-response avoids for fast tasks.
- Natural-language skill descriptions make discovery flexible, but they are less reliable than typed
  tool schemas when the task needs exact parameters; for those, an MCP tool is the better fit.

## Pitfalls
- Wrapping an A2A agent as a synchronous tool and timing out; the symptom is tasks that complete
  server-side after the caller already gave up and retried, creating duplicates.
- Ignoring `input-required` and treating it as a failure; the symptom is a remote agent that always
  seems to "fail" on ambiguous requests when it was asking a reasonable question.
- Trusting the Agent Card without verifying the server's identity and TLS; the symptom is an agent
  happily handing customer data to whoever answers at that hostname.
- Losing the task id or `contextId` between turns; the symptom is the remote agent starting from
  scratch every message, with no memory of earlier clarifications.
- Assuming field names are stable across protocol versions; the symptom is clients breaking when a
  server upgrades, because part type keys or well-known paths moved.

## When to use
Use A2A when the thing on the other side reasons rather than merely executes: it may take long, ask
back, return several artifacts, or belong to another team or company that will not expose internals.
Use MCP when you need a deterministic function with a schema. Do not introduce A2A for agents you own
inside one process; a direct function call or an orchestrator-worker pattern is cheaper. Measure task
completion rate, time in each state, and how often `input-required` occurs, since that reveals unclear
skill descriptions. In interviews, the discriminating answer is "MCP for tools, A2A for peers", with a
clear account of why the Task state machine and opacity matter.

## Role lens
- **Engineer:** Publishes an Agent Card, implements the task state machine and streaming endpoint, and
  writes clients that persist task ids across turns.
- **Architect:** Draws the boundary between agents that are tools (MCP) and agents that are peers (A2A),
  and defines the security schemes and correlation ids across organisations.
- **FDE:** Connects a customer's existing agents to yours through published cards, and negotiates skill
  descriptions and auth with their platform team.

## Check yourself
**Q:** A customer wants your support agent to delegate refund decisions to their in-house finance
agent, which can take up to ten minutes and sometimes needs the invoice number confirmed. Would you
integrate it as an MCP tool or an A2A agent, and what would the integration look like?
**A:** A2A. A ten-minute, possibly interactive process does not fit a synchronous tool call: the tool
would time out, retries would duplicate refunds, and there is no channel for "please confirm the
invoice". With A2A the finance agent publishes an Agent Card listing a `refund-review` skill and the
OAuth scheme it accepts. My agent sends a message, receives a task id and `contextId`, and subscribes
to the stream or registers a push notification so it is not holding a connection open. When the task
enters `input-required`, my agent surfaces the question to the user and replies on the same task id.
On `completed` I read the artifacts, which might include a structured decision and a PDF. I would
persist task ids for recovery, log both agents' correlation ids, and treat `failed` and `rejected`
differently from `input-required` in the user-facing flow.

## Quiz
### Q: What primarily distinguishes delegating to an A2A agent from calling an MCP tool?
- [ ] A2A uses gRPC while MCP uses JSON-RPC
- [x] An A2A task is long-running, can pause in `input-required` to ask the caller a question, and returns artifacts from an opaque agent
- [ ] MCP tools cannot be secured with OAuth
- [ ] A2A requires both agents to share a vector store for context
> MCP tools are schema-defined functions executed on demand. A2A models work as a task with a state
> machine, which is what allows pausing for input and multi-artifact results. Both use JSON-RPC in
> their reference bindings, and both support OAuth.

### Q: What does the Agent Card provide?
- [ ] The remote agent's system prompt so the caller can align instructions
- [ ] A cached copy of the remote agent's recent task results
- [x] Discovery metadata: endpoint URL, skills with descriptions, supported modes, capabilities and accepted security schemes
- [ ] The list of MCP tools the remote agent uses internally
> The card is the discovery document at a well-known path. It deliberately excludes internals such as
> prompts and tools, because A2A agents are opaque to each other by design.

### Q: Your client polls `tasks/get` every two seconds for a task that runs for twenty minutes. What is the protocol-native alternative?
- [ ] Increase the HTTP timeout to thirty minutes on `message/send`
- [x] Use `message/stream` for SSE updates, or register a push-notification webhook for the task
- [ ] Split the request into many small MCP tool calls
- [ ] Send the message again with a higher priority flag
> Streaming delivers status and artifact events as they happen, and push notifications let a task that
> outlives any connection notify the caller. Holding one long request open is fragile behind proxies.
