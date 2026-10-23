---
id: deploying-remote-mcp-servers
module: mcp
name: deploying remote MCP servers (OAuth, sessions, hosting, scaling)
kind: practice
difficulty: advanced
est_minutes: 22
status: typed-verified
lesson_type: lab
agent_relevance: core
related:
  - agents/mcp-authentication-authorization-transport-security
  - mcp/mcp-transports
  - mcp/building-an-mcp-server-end-to-end
  - python-for-ai/mcp-python-sdk
  - backend-fundamentals/kubernetes
  - backend-fundamentals/secrets-management
  - serving-inference/rate-limiting
sources:
  - Model Context Protocol specification (Transports, Authorization, Security Best Practices)
  - MCP Python SDK documentation
  - OAuth 2.1 draft, RFC 9728 (Protected Resource Metadata), RFC 8707 (Resource Indicators)
---

## TL;DR
A remote MCP server is an HTTP service on the Streamable HTTP transport that acts as an OAuth resource
server. Deploying it well means validating tokens for its own audience, choosing stateless or sticky
sessions, and running it behind proxies that do not break streaming.

## For agent builders
- **Why it matters for agents.** Remote servers are how one tool surface serves many agents and users:
  claude.ai connectors, ChatGPT, IDEs and your own agents all reach it over HTTPS with per-user tokens.
- **Where it shows up in your stack.** An ASGI app from the MCP SDK (`streamable_http_app()`), an identity
  provider issuing tokens, a load balancer, and a container or serverless platform.
- **What you control.** Token validation (issuer, audience, scopes), session mode, replica count and
  routing, timeouts for streamed responses, rate limits per client and logs per tool call.
- **What you can skip.** Writing your own authorization server. Use an existing identity provider and keep
  the MCP server a resource server that only validates tokens.

## Lab
### Task
Write the resource-server gate in front of a remote MCP endpoint: the Host check, the public Protected
Resource Metadata route, token validation for this server's audience, and the `401` and `403`
challenges that let a client like claude.ai or an OpenAI Agents SDK agent discover how to get a token.
Time box: 45-60 min.
- **Prerequisites:** MCP transports; MCP authentication, authorization and transport security; the
  first two examples in this lesson.
- **Setup:** Python 3.10+ and `pip install pytest`; stdlib only otherwise (checked with Python 3.13 and
  pytest 9.1). Save the starter as `lab.py` and the tests as `test_lab.py`.
- **No IdP or network:** `mint()` plays the authorization server with an HS256 JWT. In production the SDK's
  `AuthSettings` plus your verifier do this against the IdP's keys; the decisions are the same.

### Starter code
```python
"""Lab: the resource-server checks in front of a remote MCP endpoint. Run: python lab.py"""
import base64, hashlib, hmac, json

PUBLIC_URL = "https://mcp.example.com/mcp"                  # canonical URL = expected audience
ISSUER = "https://auth.example.com"
ALLOWED_HOSTS = {"mcp.example.com"}
METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"
TOOL_SCOPES = {"get_ticket": "tickets:read", "close_ticket": "tickets:write"}
KEY = b"stand-in for the IdP signing key"                  # real servers verify against the IdP's JWKS

def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def mint(**claims) -> str:
    """Plays the authorization server: an HS256 JWT. Never part of a real MCP server."""
    head, body = b64(b'{"alg":"HS256","typ":"JWT"}'), b64(json.dumps(claims).encode())
    sig = hmac.new(KEY, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(sig)}"

def decode(token: str) -> tuple[dict, bool]:
    """Returns (claims, signature_ok). Raises ValueError on a malformed token."""
    head, body, sig = token.split(".")
    good = hmac.compare_digest(b64(hmac.new(KEY, f"{head}.{body}".encode(), hashlib.sha256).digest()), sig)
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))), good

def metadata() -> dict:
    """RFC 9728 Protected Resource Metadata, served without auth."""
    return {"resource": PUBLIC_URL, "authorization_servers": [ISSUER],
            "scopes_supported": sorted(set(TOOL_SCOPES.values())), "bearer_methods_supported": ["header"]}

def verify_token(token: str, now: int) -> dict | None:
    # YOUR CODE HERE (1): return the claims only if the token decodes, the signature is good, iss == ISSUER,
    # aud == PUBLIC_URL (a token minted for another API must fail) and now < exp. Else None.
    raise NotImplementedError

def handle(req: dict, now: int) -> tuple[int, dict, str]:
    """req: {"path", "headers", "tool"}. Returns (status, response headers, body)."""
    # YOUR CODE HERE (2), in this order:
    # - Host header not in ALLOWED_HOSTS -> 421 (DNS rebinding guard)
    # - path == METADATA_PATH -> 200 with json.dumps(metadata()), no token needed
    # - no "Bearer " Authorization header or verify_token fails -> 401 with
    #   WWW-Authenticate: Bearer error="invalid_token", resource_metadata="<origin of PUBLIC_URL><METADATA_PATH>"
    #   (built from configuration, never from the Host header a proxy rewrote)
    #   (an Mcp-Session-Id header is NOT authentication)
    # - token lacks TOOL_SCOPES[tool] in its space-separated "scope" claim -> 403 with
    #   WWW-Authenticate: Bearer error="insufficient_scope", scope="<needed>", resource_metadata="..."
    # - otherwise 200 with body f"ran {tool} for {sub}"
    raise NotImplementedError

if __name__ == "__main__":
    now = 1_000
    good = dict(iss=ISSUER, aud=PUBLIC_URL, sub="user-42", scope="tickets:read", exp=now + 300)
    cases = [
        ("no token", {"Host": "mcp.example.com", "Mcp-Session-Id": "abc"}, "get_ticket"),
        ("valid read", {"Host": "mcp.example.com", "Authorization": "Bearer " + mint(**good)}, "get_ticket"),
        ("other audience", {"Host": "mcp.example.com",
                            "Authorization": "Bearer " + mint(**{**good, "aud": "https://crm.example.com"})}, "get_ticket"),
        ("expired", {"Host": "mcp.example.com", "Authorization": "Bearer " + mint(**{**good, "exp": now})}, "get_ticket"),
        ("needs write", {"Host": "mcp.example.com", "Authorization": "Bearer " + mint(**good)}, "close_ticket"),
        ("rebinding", {"Host": "evil.example", "Authorization": "Bearer " + mint(**good)}, "get_ticket"),
    ]
    for label, headers, tool in cases:
        status, hdrs, body = handle({"path": "/mcp", "headers": headers, "tool": tool}, now)
        print(f"{label:15} {status} {hdrs.get('WWW-Authenticate', body)}")
    print(handle({"path": METADATA_PATH, "headers": {"Host": "mcp.example.com"}, "tool": None}, now)[0])
```

### Expected output
`python lab.py` with a correct solution prints exactly (the 421 body text is your choice; the tests
check only the status):

```
no token        401 Bearer error="invalid_token", resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
valid read      200 ran get_ticket for user-42
other audience  401 Bearer error="invalid_token", resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
expired         401 Bearer error="invalid_token", resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
needs write     403 Bearer error="insufficient_scope", scope="tickets:write", resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
rebinding       421 Misdirected Request
200
```

- **Read the lines:** the CRM token is perfectly valid at the IdP, yet it gets the same `401` as no token,
  because its audience is another API. The `403` tells the client which scope to step up to.

### Tests
Run `pytest -q test_lab.py`. On the starter all 6 fail with `NotImplementedError`; a correct solution
passes all 6 (Python 3.13, pytest 9.1).

```python
import json
from lab import ISSUER, METADATA_PATH, PUBLIC_URL, handle, mint, verify_token

NOW = 1_000
GOOD = dict(iss=ISSUER, aud=PUBLIC_URL, sub="user-42", scope="tickets:read", exp=NOW + 300)
CHALLENGE = ('Bearer error="invalid_token", '
             'resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"')

def req(tool="get_ticket", host="mcp.example.com", token=None, **extra):
    headers = {"Host": host, **extra}
    if token:
        headers["Authorization"] = "Bearer " + token
    return {"path": "/mcp", "headers": headers, "tool": tool}

def test_verify_checks_signature_issuer_audience_and_expiry():
    assert verify_token(mint(**GOOD), NOW)["sub"] == "user-42"
    assert verify_token(mint(**{**GOOD, "aud": "https://crm.example.com"}), NOW) is None
    assert verify_token(mint(**{**GOOD, "iss": "https://other-idp.example"}), NOW) is None
    assert verify_token(mint(**{**GOOD, "exp": NOW}), NOW) is None
    head, body, _ = mint(**GOOD).split(".")
    assert verify_token(f"{head}.{body}.forged", NOW) is None
    assert verify_token("not-a-jwt", NOW) is None

def test_missing_token_gets_the_discovery_challenge_even_with_a_session_id():
    status, headers, _ = handle(req(**{"Mcp-Session-Id": "abc"}), NOW)
    assert (status, headers["WWW-Authenticate"]) == (401, CHALLENGE)

def test_token_for_another_audience_is_401():
    status, headers, _ = handle(req(token=mint(**{**GOOD, "aud": "https://crm.example.com"})), NOW)
    assert (status, headers["WWW-Authenticate"]) == (401, CHALLENGE)

def test_missing_scope_is_403_insufficient_scope():
    status, headers, _ = handle(req("close_ticket", token=mint(**GOOD)), NOW)
    assert status == 403
    assert headers["WWW-Authenticate"].startswith('Bearer error="insufficient_scope", scope="tickets:write"')

def test_valid_token_runs_the_tool():
    token = mint(**{**GOOD, "scope": "tickets:read tickets:write"})
    assert handle(req("close_ticket", token=token), NOW) == (200, {}, "ran close_ticket for user-42")

def test_host_check_runs_before_auth_and_metadata_is_public():
    assert handle(req(host="evil.example", token=mint(**GOOD)), NOW)[0] == 421
    status, _, body = handle({"path": METADATA_PATH, "headers": {"Host": "mcp.example.com"}, "tool": None}, NOW)
    assert status == 200 and json.loads(body)["resource"] == PUBLIC_URL
```

### Failure scenarios
- **Skip the audience check.** The CRM-token test returns `200`: any service that can get a token from
  your IdP can now call your tools. Debug by decoding a rejected and an accepted token side by side and
  comparing `aud` with `PUBLIC_URL`.
- **Build `resource_metadata` from the `Host` header.** Behind a proxy that rewrites Host to an internal
  name, clients are sent to an unreachable metadata URL and discovery fails. Reproduce by sending
  `Host: mcp-internal:8000` after adding it to `ALLOWED_HOSTS`; the challenge must not change.
- **Put the metadata route behind auth.** Every client loops on `401` because it cannot read where to get
  a token. Probe with the unauthenticated `curl` from the examples.
- **Check the scope as a substring of the claim.** A token with `scope="tickets:writer-preview"` now
  passes as `tickets:write`. The suite does not catch this, so write the test that does: split the claim
  on spaces and compare whole scopes.

### Submission
- **`lab.py` and `test_lab.py`** with `pytest -q` showing 6 passed.
- **The `python lab.py` output** matching the expected block.
- **A short mapping note** from each check to the Python SDK setting that does it in production
  (`validate_token_resource`, `required_scopes`, `TransportSecuritySettings(allowed_hosts=...)`).
- **One extra test** for a case you thought of, for example a token with no `exp` claim.
- **Evaluated on:** green tests, challenges that match the spec format byte for byte, and no token value
  in any response or log line.

## Why it exists
- **Local stdio does not share.** A stdio server runs on one machine for one user; a team or a public
  product needs one HTTPS endpoint with per-user identity.
- **API keys pasted into configs do not scale.** They leak, cannot be scoped per user and cannot be revoked
  cleanly, so the spec standardises OAuth-based authorization for HTTP transports.
- **Sessions fight horizontal scaling.** A stateful session lives on one replica; without sticky routing
  or shared state the next request lands elsewhere and fails.
- **Streaming breaks behind ordinary proxies.** Server-Sent Events responses need unbuffered, long-lived
  connections, which default proxy and serverless timeouts cut off.

## How it works
### The transport: Streamable HTTP
- **One endpoint** (for example `/mcp`). Clients POST JSON-RPC messages; the server answers with JSON or
  with an SSE stream when it needs to send progress or server requests first. An optional GET opens a
  stream for server-initiated messages.
- **Sessions (2025-03-26 onwards).** The server may return `Mcp-Session-Id` on `initialize`; the client
  sends it on every later request, and DELETE ends the session.
- **Version header (2025-06-18 onwards).** Clients send `MCP-Protocol-Version` on requests after
  initialisation, so servers know which revision to speak.
- **Resumability.** With SSE event ids, a client can reconnect with `Last-Event-ID` and the server replays
  missed messages if it kept them in an event store.
- **Later revisions.** The Python SDK 2.x implements a 2026-07-28 revision in which each request is
  self-contained, with no `initialize` handshake or `Mcp-Session-Id`. Check which revisions your clients speak.

### Authorization, step by step (2025-06-18 and 2025-11-25 revisions)
1. **Challenge.** A request without a valid token gets `401` with `WWW-Authenticate` pointing at the
   server's Protected Resource Metadata (RFC 9728).
2. **Discovery.** The metadata lists the authorization servers; the client fetches their metadata
   (RFC 8414 or OpenID Connect discovery).
3. **Registration.** Pre-registered clients, Client ID Metadata Documents (added in 2025-11-25) or Dynamic
   Client Registration (RFC 7591) give the client an identity.
4. **Authorize.** Authorization code flow with PKCE, passing `resource` (RFC 8707) set to the MCP server's
   canonical URL so the token is bound to it.
5. **Call.** Requests carry `Authorization: Bearer <token>`; the server validates signature, expiry,
   audience and scopes, and answers `403` with `insufficient_scope` when a tool needs more.
6. **Downstream.** The server never forwards that token to other APIs; it uses its own credentials or a
   token exchange, because the spec forbids token passthrough.

### Sessions and scaling
| Mode | How | Scales by | Gives up |
|---|---|---|---|
| Stateless | `stateless_http=True`, often with `json_response=True` | Any replica serves any request | Server-to-client requests mid-call, resumable streams |
| Stateful, sticky | Load balancer routes on `Mcp-Session-Id` | Adding replicas; sessions pinned | Sessions die with their replica |
| Stateful, shared | Session and event state in Redis or similar | Any replica, resumable | More moving parts, store latency |

### Hosting choices
- **Containers** (Cloud Run, ECS, Kubernetes, Fly) fit most servers: long-lived connections, normal timeouts,
  easy sticky routing.
- **Serverless functions** fit stateless, short tool calls; check the platform's maximum response duration
  before relying on streamed responses.
- **Edge platforms** with durable state (for example Cloudflare Workers with Durable Objects) are a common
  home for remote MCP servers; their SDKs wrap sessions for you, so check their docs.

## Diagram
The first call fails on purpose: the `401` tells the client where to get a token bound to this server.

```mermaid
sequenceDiagram
    participant C as MCP client
    participant S as MCP server
    participant A as Authorization server
    C->>S: POST /mcp without token
    S-->>C: 401 plus resource_metadata URL
    C->>S: GET protected resource metadata
    S-->>C: authorization_servers, scopes
    C->>A: auth code plus PKCE, resource=server URL
    A-->>C: access token for this audience
    C->>S: POST /mcp with Bearer token
    Note over S: check issuer, audience, expiry, scopes
    S-->>C: initialize result, then tool calls
```

## Examples
### A token-protected, stateless server with the Python SDK
Python `mcp` SDK 2.x; on 1.x import `FastMCP` from `mcp.server.fastmcp` and pass `stateless_http`,
`json_response` and `transport_security` to the constructor instead. Swap the toy verifier for JWT
validation against your identity provider.

```python
import os
from urllib.parse import urlsplit
from mcp.server.mcpserver import MCPServer                 # mcp SDK 2.x
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000/mcp")
ISSUER = os.environ.get("ISSUER_URL", "https://auth.example.com")

class IntrospectingVerifier:
    """Replace with JWT validation against your IdP's JWKS or token introspection."""
    async def verify_token(self, token: str) -> AccessToken | None:
        if token != os.environ.get("DEV_TOKEN", "dev-token"):
            return None                                          # -> 401 with WWW-Authenticate
        return AccessToken(token=token, client_id="dev", scopes=["tickets:read"],
                           expires_at=None, resource=PUBLIC_URL)

mcp = MCPServer(
    "tickets",
    token_verifier=IntrospectingVerifier(),
    auth=AuthSettings(issuer_url=ISSUER, resource_server_url=PUBLIC_URL,
                      required_scopes=["tickets:read"], validate_token_resource=True),
)

@mcp.tool()
def get_ticket(ticket_id: str) -> dict:
    """Fetch one support ticket by id, for example 'T-1'."""
    return {"id": ticket_id, "status": "open"}

app = mcp.streamable_http_app(
    stateless_http=True, json_response=True,                 # any replica can serve any request
    # by default only localhost Host headers pass (DNS rebinding guard); allow your public host
    transport_security=TransportSecuritySettings(allowed_hosts=[urlsplit(PUBLIC_URL).netloc]),
)
# run: uvicorn remote_server:app --host 0.0.0.0 --port 8000 --workers 4
```

### What a client sees on the wire
Probing the server above with `curl` shows the discovery chain the spec requires.

```bash
curl -si -X POST http://localhost:8000/mcp -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' -d '{}'
# HTTP/1.1 401 Unauthorized
# www-authenticate: Bearer error="invalid_token", error_description="Authentication required",
#   resource_metadata="http://localhost:8000/.well-known/oauth-protected-resource/mcp"

curl -s http://localhost:8000/.well-known/oauth-protected-resource/mcp
# {"resource":"http://localhost:8000/mcp","authorization_servers":["https://auth.example.com"],
#  "scopes_supported":["tickets:read"],"bearer_methods_supported":["header"]}

curl -si -X POST http://localhost:8000/mcp -H 'Host: evil.example' -H 'authorization: Bearer dev-token' \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' -d '{}'
# HTTP/1.1 421 Misdirected Request      (Host not in allowed_hosts)
```

### Worked scenario: sticky sessions during a rolling deploy
A team ran a stateful server on 4 replicas with sticky routing and deployed during business hours.

| Step | Effect |
|---|---|
| Replica 1 drains; its sessions are cut | Agents mid-run get `404` for their `Mcp-Session-Id` |
| Clients re-initialize | Some frameworks retry, others fail the run |
| Measured impact (illustrative) | ~25% of active agent runs saw one failed tool call |
| Fix | Tools did not need server-to-client requests, so the team moved to stateless mode and dropped sticky routing |

## Production patterns
- **Validate tokens for your audience.** Check issuer, signature, expiry, scopes and audience (the server's
  canonical URL), and reject anything else; in the Python SDK set `validate_token_resource=True`. Monitor
  `401` and `403` rates by client.
- **Default to stateless.** Use stateless mode unless tools need sampling or elicitation mid-call; it
  removes sticky routing and lets autoscaling work. Progress notifications still stream on the POST's SSE
  response if you leave `json_response` off. Monitor replica count against request rate.
- **Make streaming survive the proxy.** Disable response buffering for the MCP path, raise idle timeouts
  above your longest tool call, and send keep-alives on SSE. Monitor dropped streams and tool-call duration.
- **Rate limit and isolate per client.** Key limits on token subject or client id, scope data by tenant
  claims in the token, and cap concurrent calls per user. Monitor `429`s and the noisiest clients.
- **Log one structured line per tool call.** Include tool name, client id, subject, latency, `isError` and
  protocol version, with no token values. Alert on error rate per tool and on unknown protocol versions.
- **Protect the endpoint itself.** Serve only HTTPS, validate `Host` and `Origin` (the Python SDK's
  `TransportSecuritySettings(allowed_hosts=..., allowed_origins=...)`), restrict CORS to known origins, and
  never treat `Mcp-Session-Id` as authentication. Monitor `421` responses and rejected origins.

## Trade-offs
- **Stateless vs stateful.** Stateless scales trivially and survives deploys but cannot ask the client
  for input mid-call or resume streams; stateful supports richer interactions at the cost of routing and storage.
- **Your IdP vs an MCP-specific auth service.** Reusing the company IdP keeps one identity system, but it
  may lack the dynamic or metadata-based registration MCP clients expect; a dedicated service fills that gap.
- **Containers vs serverless.** Containers handle long streams and warm connections; serverless is cheaper
  for bursty, short calls but timeouts and cold starts hurt long tool runs.
- **One multi-tenant server vs one per customer.** Shared servers are cheaper to run; per-customer
  deployments simplify data isolation and compliance at a higher operating cost.

## Pitfalls
- **Accepting any valid token from the IdP.** A token minted for another API works on your server, so a
  compromised service can call your tools. Fix: enforce the audience and resource binding.
- **Forwarding the user's token downstream.** The spec forbids it, and it breaks audit and scoping. Fix:
  obtain separate downstream credentials or use token exchange.
- **Buffered SSE behind a proxy.** Symptom: long tool calls hang then arrive all at once, or time out.
  Fix: disable buffering for the MCP route and raise idle timeouts.
- **Stateful sessions with round-robin balancing.** Symptom: random `400` or `404` responses after the first
  request. Fix: stateless mode, sticky routing or a shared session store.
- **Wrong public URL in metadata.** Behind a proxy the server advertises an internal host, so clients fail
  discovery. Fix: set the resource URL from configuration, not from the incoming request.
- **Shipping the localhost-only Host check.** The Python SDK turns on DNS rebinding protection for
  localhost by default. Symptom: every request through the public hostname gets `421 Invalid Host header`.
  Fix: list the public host in `allowed_hosts`.

## When to use
- **Deploy remotely when:** many users or agents share the server, users authenticate with their own
  accounts, or the server must be reachable from hosted clients such as web chat apps.
- **Stay local (stdio) when:** the server touches one developer's files or tools and never needs
  per-user identity.
- **Choose stateful when:** tools need sampling, elicitation or resumable long-running streams; otherwise
  stateless.
- **Measure:** auth failures, tool-call latency p95, dropped streams, `429`s per client and error rate per tool.

## Role lens
- **Engineer:** Builds the HTTP app from the SDK, wires token validation to the IdP, sets session mode and
  proxy settings, and adds per-call structured logs and rate limits.
- **Architect:** Chooses hosting, session strategy and tenancy model, integrates the MCP server with the
  company identity provider, and defines how downstream credentials are obtained.
- **FDE:** Deploys the server into a customer's cloud and IdP, debugs discovery and redirect issues with
  their security team, and proves per-user scoping works before go-live.

## Check yourself
**Q:** Your remote MCP server works with the Inspector using a static token, but claude.ai fails to connect,
and behind the load balancer some tool calls return `404`. What do you check?
**A:** For the connection failure, walk the discovery chain: an unauthenticated POST must return `401` with
a `WWW-Authenticate` header whose `resource_metadata` URL is public and correct, that metadata must list a
reachable authorization server, and that server must support a registration method the client uses (such
as Dynamic Client Registration or client metadata documents) plus PKCE and the `resource` parameter. For the
`404`s, the server is stateful while the balancer spreads requests, so sessions are unknown on other
replicas; switch to stateless mode, add sticky routing on `Mcp-Session-Id`, or share session state.

## Interview questions
### What role does an MCP server play in OAuth, and why does that matter?
- **It is a resource server.** It validates access tokens issued by an authorization server and does not
  need to issue tokens itself, so teams reuse an existing identity provider.
- **It must check audience.** Tokens must be bound to the server's URL via the `resource` parameter, which
  stops tokens for other services being replayed against it.

### How would you scale a remote MCP server to many replicas?
- **Prefer stateless mode** so any replica serves any request and autoscaling works on request rate.
- **If you need sessions,** route on `Mcp-Session-Id` or keep session and event state in a shared store,
  and drain replicas gracefully during deploys.

### Long-running tool calls time out in production but not locally. Where do you look?
- **The path between client and server:** proxy buffering of SSE, load-balancer idle timeouts, and
  serverless maximum durations.
- **Then the design:** send progress notifications, raise idle timeouts on the MCP route, or make the tool
  start a job and return an id the agent can poll.

### Why is token passthrough forbidden, and what do you do instead?
- **Passthrough breaks the trust chain.** Downstream APIs see a token not meant for them, audit trails lose
  which service acted, and a stolen token works in more places.
- **Instead:** keep separate downstream credentials, or exchange the user's token for a downstream token
  scoped to that API, and log both identities.

### What would you log and alert on for a multi-tenant remote MCP server that many agents call?
- **Per call:** tenant, client id, subject, tool, latency, `isError` and protocol version, never token values.
- **Alerts:** error rate and p95 per tool, auth failures per client, `429` spikes and unknown protocol versions
  after a client update.

## Quiz
### Q: What does an MCP server return to an unauthenticated request so that a client can start OAuth?
- [ ] A redirect to the login page
- [x] A `401` with a `WWW-Authenticate` header pointing to its Protected Resource Metadata
- [ ] A JSON-RPC error with the client secret
- [ ] An `Mcp-Session-Id` with guest scopes
> The authorization spec uses RFC 9728 metadata: the challenge tells the client where to find the
> authorization servers, which starts discovery and the PKCE flow.

### Q: Why should clients send the `resource` parameter when requesting a token for an MCP server?
- [ ] It enables streaming responses
- [ ] It is required for stdio servers
- [x] It binds the token's audience to that server so it cannot be replayed against other services
- [ ] It replaces PKCE
> Resource Indicators (RFC 8707) let the authorization server mint a token for one specific resource; the
> MCP server then rejects tokens whose audience is not its own URL.

### Q: Which change most simply fixes random session errors when a stateful MCP server runs behind a round-robin load balancer?
- [ ] Increase the number of replicas
- [ ] Switch tools to return JSON instead of text
- [x] Run the server in stateless mode if no tool needs server-to-client requests
- [ ] Disable OAuth
> Stateful sessions live on one replica. If tools do not need sampling, elicitation or resumable streams,
> stateless mode lets any replica answer any request and removes the routing problem.

## Use cases & projects
### Use cases
- **SaaS vendors exposing their API to AI clients.** One remote MCP server with OAuth lets customers connect
  claude.ai, ChatGPT or IDE agents with their own accounts.
- **Internal tool platforms.** A company hosts MCP servers for ticketing, HR and data behind the corporate
  IdP, shared by every internal agent.
- **Per-customer deployments.** FDE teams deploy the same server into each customer's cloud with their IdP
  and data residency rules.
- **Agent backends at scale.** Stateless MCP servers autoscale with agent traffic like any other API.

### Mini project
**Deploy a protected, stateless MCP server (2-3 hours).**
- **Goal:** run the token-protected server above in a container behind HTTPS and connect an agent to it.
- **Steps:** replace the toy verifier with JWT validation against a free IdP tenant or a local Keycloak;
  containerise with uvicorn and two replicas behind a proxy; confirm the `401` and metadata responses with
  curl; connect with the Inspector and with an OpenAI Agents SDK agent using `MCPServerStreamableHttp`
  (set `max_retry_attempts` above its default of 0); then kill one replica mid-run.
- **Done when:** a token for another audience is rejected, the agent's run survives losing a replica, and
  logs show one structured line per tool call with no token values.
