---
id: tool-adapters-service-interfaces
module: agent-reliability
name: Tool adapters / service interfaces
kind: pattern
difficulty: core
est_minutes: 13
status: reviewed
related:
  - agent-reliability/tool-permissions-and-least-privilege
  - agent-reliability/idempotent-tool-execution
  - agent-reliability/tool-call-success-rate
  - agents/typed-tool-contracts
  - agents/tool-schemas-and-argument-validation
  - agents/model-context-protocol
  - agent-runtime-architecture/cross-language-api-contracts
  - serving-inference/retry-strategies
sources:
  - Cockburn, 2005 — Hexagonal Architecture (Ports and Adapters)
  - Official docs: Model Context Protocol (tools)
---

## TL;DR
The model speaks in small typed tool calls; real services speak HTTP with auth, pagination, rate limits
and 40-field responses. A tool adapter is the layer that owns that translation: it validates arguments,
enforces policy, injects credentials, applies timeouts, retries and idempotency, and turns every outcome,
including failure, into a compact structured result the model can reason about. Keeping that layer
explicit is what makes tools testable, swappable and safe.

## Why it exists
Hand a model the raw OpenAPI spec of a ticketing system and three things go wrong at once. The schema is
enormous, so it eats context and degrades tool selection; the responses are enormous, so each result eats
more context and buries the two fields that matter; and the failure modes are the vendor's, not yours: a
429 with a Retry-After header, a 502 HTML page, a timeout with no body. The model will either hallucinate
a fix for an error it cannot parse or loop on the same call. Meanwhile the API key has to live somewhere,
and "in the prompt" is the one place it must not be.

The naive alternative in the other direction is to write each tool as a quick function that calls the SDK
directly. That works for a demo and fails the first time you need to swap the vendor, run the agent in an
eval harness without hitting production, apply a permission check, or figure out why 8% of calls fail. The
logic is scattered across twenty functions, each with its own idea of what an error looks like.

The adapter pattern is older than agents. Hexagonal architecture calls the consumer-facing contract a
*port* and the vendor-specific implementation an *adapter*; the application depends only on ports. What is
new is that the consumer of the port is a language model, which changes what a good contract looks like:
fewer arguments, enums over opaque IDs, outcomes that are explicit rather than implied by status codes,
and absolutely no secrets on the model side of the boundary.

## How it works
### Two interfaces, one component
An adapter has an inner and an outer face.

- The **model-facing contract** is the tool definition the model sees: name, one-paragraph description, a
  JSON schema for arguments, and an implied result shape. It is designed for a reader that
  pattern-matches on descriptions and cannot look anything up: three to six arguments, enums where the
  backend wants IDs (`project: "billing"` rather than `project_id: 10432`), sensible defaults, and a
  stable shape across backend changes.
- The **service interface** is the client that talks to the real system: SDK or HTTP, auth, base URLs,
  pagination cursors, vendor error codes. It changes when the vendor changes and the model never notices.

### What the adapter does on every call
1. **Validate** the arguments against the schema before any side effect, *because* the model emits text
   and a missing field is cheaper to reject now than to discover as a 400 after half the work is done.
   Validation failures return a structured error that names the field, so the model can correct it.
2. **Authorize** against the run's permission policy. The adapter is the natural enforcement point because
   it is the last code that sees the parsed arguments before they become effects.
3. **Translate** intent into service calls. One model-facing call may be several service calls: resolve
   the project name to an ID, create the ticket, fetch its URL. Hiding that sequence is the point; the
   model should not plan around the vendor's data model.
4. **Inject credentials** from a vault, scoped to the user and tool, at call time. The token exists only
   inside the adapter's stack frame.
5. **Bound the call**: a timeout, a retry policy for retryable classes only, and an idempotency key derived
   from the run and step so that a retry after a lost response cannot create a second ticket. If the
   vendor does not honour an idempotency header, the adapter deduplicates locally by that key. Transient
   failures are retried *inside* the adapter, a small bounded number of times with jittered backoff,
   *because* a retry the model has to decide on costs a full model turn of latency and tokens; only when
   that budget is spent does the failure reach the model, as a result it can plan around.
6. **Normalize the outcome** into one shape: `{"ok": true, ...}` or `{"ok": false, "error": <code>,
   "retryable": <bool>, ...}`. The error code comes from a small taxonomy you own (`invalid_arguments`,
   `not_found`, `denied`, `rate_limited`, `upstream_timeout`, `upstream_error`), *because* the model should
   learn one vocabulary of failure across all tools, and because your success-rate metrics need the same
   classes.
7. **Project the response** down to what the next step needs, with stable identifiers the model can refer
   back to, and a cursor if the full result is paged. Large payloads go to a blob store and the model gets
   a reference plus a summary.
8. **Emit telemetry**: a span with latency and error class, and an event on the run's stream.

### Composite calls and partial failure
Hiding a three-call sequence behind one tool creates a new failure mode: call two succeeds and call
three fails. The adapter must not report a plain failure, *because* the model will try again and create
a second ticket. It either compensates (undo the earlier calls, if they can be undone) or reports the
partial state explicitly: `{"ok": false, "error": "partial", "created": {"ticket_id": "T-88"},
"failed_step": "attach_file"}`. The rule of thumb is that one model-facing tool should map to at most one
non-idempotent service write; when a workflow needs several, either make it a durable sub-workflow with
its own checkpoints or expose the writes as separate tools.

### Evolving the contract
The model-facing contract is an API with a fussy client. Additive changes (a new optional argument with
a default, a new result field) are safe. Renaming an argument, narrowing an enum or changing what a field
means is a breaking change, *because* prompts, few-shot examples and recorded eval trajectories all
encode the old shape, and the model will keep producing it. Version breaking changes as a new tool name
(`create_ticket_v2`), run the trajectory suite against both, and retire the old one when its call volume
reaches zero. The service interface behind it can change freely as long as the contract tests, which run
the adapter against recorded vendor responses, keep passing.

### Where the boundary lives
In-process adapters are plain classes, fastest and simplest. MCP standardizes the boundary as a protocol:
the server exposes tool contracts, the host's client calls them, and the eight responsibilities above move
into the server. That buys reuse across agents and languages at the cost of a network hop and a
deployment. Either way, the adapter is also the seam for evaluation: swap the service interface for a
recorded or fake one and the agent runs deterministically against the same contract.

### Why not just let the model emit HTTP
A generic `http_request` tool is the zero-adapter design. It maximizes flexibility and minimizes
everything else: no argument validation, no permission check on semantics, vendor errors passed straight
through, and credentials that have to be reachable by the model to be usable. It is the right tool for a
sandboxed research agent and the wrong one for anything with side effects.

## Example
A `create_ticket` adapter: typed arguments on the outside, a vendor HTTP API on the inside, one error
vocabulary, and a projected result. Nothing vendor-specific leaks to the model.

```python
from typing import Literal
from pydantic import BaseModel, Field, ValidationError
import httpx

class CreateTicketArgs(BaseModel):
    title: str = Field(max_length=120)
    body: str
    priority: Literal["low", "normal", "high"] = "normal"
    project: Literal["billing", "platform", "support"]     # enum for the model, ID for the vendor

class TicketAdapter:
    def __init__(self, client: httpx.Client, project_ids: dict[str, int], vault, web_base: str):
        self.client, self.project_ids, self.vault = client, project_ids, vault
        self.web_base = web_base.rstrip("/")                 # UI host, not the API base_url

    def create_ticket(self, raw_args: dict, *, user: str, idempotency_key: str) -> dict:
        try:
            a = CreateTicketArgs.model_validate(raw_args)                  # 1. validate before side effects
        except ValidationError as e:     # same args will fail again: the model must fix them
            return {"ok": False, "error": "invalid_arguments", "retryable": False,
                    "detail": [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]}
        payload = {"summary": a.title, "description": a.body,            # 3. translate to vendor shape
                   "priority_code": {"low": 3, "normal": 2, "high": 1}[a.priority],
                   "project_id": self.project_ids[a.project]}
        headers = {"Authorization": f"Bearer {self.vault.token_for(user, 'tickets', ttl_s=300)}",  # 4.
                   "Idempotency-Key": idempotency_key}                     # 5. retry cannot double-create
        try:
            r = self.client.post("/v2/tickets", json=payload, headers=headers, timeout=10.0)
        except httpx.TimeoutException:
            return {"ok": False, "error": "upstream_timeout", "retryable": True}
        except httpx.TransportError:                       # DNS, refused, reset: never raise to the loop
            return {"ok": False, "error": "upstream_error", "retryable": True}
        if r.status_code == 429:                  # Retry-After may be seconds or an HTTP date
            ra = r.headers.get("Retry-After", "")
            return {"ok": False, "error": "rate_limited", "retryable": True,
                    "retry_after_s": int(ra) if ra.isdigit() else 5}
        if r.status_code >= 400:                                          # 6. one error vocabulary
            err = {401: "denied", 403: "denied", 404: "not_found"}.get(r.status_code, "upstream_error")
            return {"ok": False, "error": err, "retryable": r.status_code >= 500,
                    "http_status": r.status_code}       # raw vendor body goes to the trace, not the model
        d = r.json()                                                       # 7. project the response
        return {"ok": True, "ticket_id": d["id"], "status": d["status"],
                "url": f"{self.web_base}/tickets/{d['id']}"}
```

## Trade-offs
- An adapter per tool is code to write and maintain, but it is the only place where validation, policy,
  retries and error normalization can live once rather than being re-implemented per call site; the cost
  flips in favour of a generic HTTP tool only for sandboxed, read-only exploration.
- A narrow contract improves selection accuracy and safety yet cannot express the request you did not
  anticipate; when tasks are open-ended, offer a few composable primitives rather than one tool per
  workflow.
- Projecting responses saves context tokens and keeps the model focused, but every dropped field is a
  field the model cannot use; add a `fields` argument or a follow-up `get` tool rather than widening the
  default.
- MCP-style out-of-process adapters give reuse across agents and languages at the price of a network hop, a
  deployment and a second place where auth must be configured; in-process adapters win for a
  single-service agent.

## Pitfalls
- Exposing a whole OpenAPI spec as tools; the symptom is a tool list of 80 entries, high token overhead
  per turn and the model picking near-miss tools.
- Letting the adapter raise; the symptom is agent loops that crash on a vendor 502 instead of telling the
  model the call failed and is retryable.
- Passing vendor error bodies straight through; the symptom is the model "fixing" an HTML error page by
  inventing a different argument, repeatedly.
- No timeout on the service call; the symptom is runs that hang for minutes on one tool while the step
  budget and the user's patience expire.
- Returning results without stable identifiers; the symptom is a follow-up call that refers to "the second
  ticket" and the adapter cannot resolve it.
- Hiding non-idempotent writes behind automatic retries; the symptom is duplicate tickets, emails or
  refunds after a transient timeout.
- Reporting a composite call's partial failure as a plain failure; the symptom is orphaned or duplicated
  records created by the model's well-meaning second attempt.
- Renaming an argument in place; the symptom is a jump in `invalid_arguments` for that tool right after
  deploy, because the model still emits the old name it learned from examples.

## When to use
Introduce an adapter the moment a tool does anything beyond a pure in-memory computation: any network
call, any credential, any side effect. For a prototype, a single adapter base class with validation,
timeout and error normalization is enough; for production, add idempotency keys, policy checks and
telemetry. Measure per-tool success rate by error class, p95 latency, and the fraction of responses
truncated, because those three numbers tell you whether the contract or the service is the problem. In
interviews, "how do you design a tool for an agent" should produce the two-face answer: a contract shaped
for a model and a client shaped for the vendor, with the adapter owning everything between.

## Role lens
- **Engineer:** Writes adapters with typed arguments, a shared error taxonomy and tests that run the agent
  against a fake service interface.
- **Architect:** Decides the contract conventions (naming, error codes, result envelopes), whether
  adapters are in-process or MCP servers, and which tools are forbidden from being generic.
- **FDE:** Builds the adapter to the customer's internal systems, hiding their legacy data model behind a
  contract the standard agent already understands.

## Check yourself
**Q:** A customer's CRM API returns 2 MB JSON documents per contact and rate-limits at 10 requests per
second. The agent needs to look up contacts and update a status field. How do you design the adapter, and
what do you return to the model?
**A:** Two tools, not one: `find_contact(query)` and `update_contact_status(contact_id, status)`. The find
adapter calls the CRM search endpoint, projects each hit to id, name, company and current status, caps the
list at perhaps ten entries with a cursor for more, and puts nothing else in context; the model can request
a `get_contact(contact_id, fields=[...])` when it needs detail. The update adapter validates `status`
against an enum, checks the run's policy, injects a per-user token, sends the PATCH with a timeout and an
idempotency key, and maps 429 to `rate_limited` with the server's retry-after so the runtime, not the
model, handles backoff. A client-side token bucket at slightly under 10 requests per second protects the
shared limit across concurrent runs, but only if it is shared by all of them (process-wide, or backed by
Redis when runs span workers); a bucket per run lets ten concurrent runs send ten times the limit. Every outcome returns in the same `ok/error/retryable` envelope and is recorded
with its error class so success rate per tool is visible from day one.

## Quiz
### Q: Why should a tool adapter return a structured error object instead of raising an exception when the upstream service times out?
- [ ] Exceptions cannot be serialized into the event stream
- [x] So the model receives a result it can reason about, including whether a retry makes sense, instead of the run crashing or looping
- [ ] Because timeouts are always retryable and the adapter should retry silently forever
- [ ] Because raising would reveal the vendor name to the model
> The model is the planner; a structured failure with an error class and a retryable flag lets it re-plan
> or wait, while an exception either kills the loop or surfaces as opaque text.

### Q: A team exposes the vendor's 60-endpoint OpenAPI spec directly as agent tools. Which symptom is most likely?
- [ ] Credentials become unnecessary because the spec is self-describing
- [ ] Response payloads shrink because the model asks for exactly the fields it needs
- [x] Tool selection accuracy drops and per-turn token cost rises because the model must read a large, vendor-shaped tool list every step
- [ ] Idempotency is automatically guaranteed by the spec's operation IDs
> Large, vendor-shaped tool menus are hard for a model to choose from and expensive to carry in context;
> an adapter presents a few intent-level tools instead.

### Q: What does mapping `project: "billing"` to `project_id: 10432` inside the adapter achieve?
- [ ] It makes the call idempotent
- [x] It keeps the model-facing contract stable and readable while the vendor's identifiers stay on the service side
- [ ] It removes the need for argument validation
- [ ] It allows the model to call the vendor API directly when the adapter is down
> Enums the model can read reduce argument errors and decouple the contract from vendor IDs that can
> change; the adapter owns the translation.
