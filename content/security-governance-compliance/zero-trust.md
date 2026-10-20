---
id: zero-trust
module: security-governance-compliance
name: Zero trust
kind: concept
difficulty: core
est_minutes: 15
related:
  - security-governance-compliance/ai-security
  - security-governance-compliance/prompt-injection-defense
  - agent-runtime-architecture/secrets-and-credential-isolation
  - agent-runtime-architecture/approval-and-authorization-boundaries
  - agents/mcp-authentication-authorization-transport-security
  - agent-reliability/tool-permissions-and-least-privilege
  - backend-fundamentals/oauth-2-0-oidc
  - deployment/private-endpoints
sources:
  - NIST, 2020 — SP 800-207, Zero Trust Architecture
  - Ward & Beyer, 2014 — BeyondCorp: A New Approach to Enterprise Security
  - IETF, 2020 — RFC 8693, OAuth 2.0 Token Exchange
---

## TL;DR
Zero trust drops the assumption that anything inside the network perimeter is trustworthy. Every request
is authenticated, authorised against policy for that specific resource, and granted the least privilege
for the shortest time, *because* attackers who get inside, or components that are tricked, otherwise
inherit everything the perimeter protected. For AI systems, the model and the agent are exactly such
components: each tool call is a request to verify, not a trusted insider.

## Why it exists
The perimeter model drew a line around the corporate network: outside was hostile, inside was trusted.
Once past the VPN or firewall, a user or service could reach databases and internal APIs largely on the
strength of network location. That model failed in practice for well-understood reasons: phished
credentials put attackers inside, a single compromised server could move laterally to everything on the
flat network, and cloud services, SaaS and remote work dissolved the line that the perimeter assumed.

AI systems make the perimeter assumption actively dangerous. An agent running inside your VPC typically
holds a service credential that can read the CRM, the document store and the ticketing system. It also
reads untrusted text: emails, web pages, uploaded files. Prompt injection means that text can steer the
agent's actions. Under perimeter thinking the agent is "internal" and its broad credential is fine; in
reality it is a confused deputy that an outsider can direct. Its network location says nothing about
whose intent it is currently carrying out.

The naive fix, a tighter firewall or a separate subnet for AI workloads, does not address this, because
the attacker's instructions arrive through legitimate data channels the agent is supposed to read. What
is needed is per-request verification of who is asking, on whose behalf, for what resource, with what
scope, so that a misled agent can only do what the current user was allowed to do for the current task.

## How it works
### The core principles
NIST SP 800-207 frames zero trust around a few tenets, paraphrased here: treat all data sources and
services as resources; secure all communication regardless of network location; grant access per
session, per resource; decide access by dynamic policy using identity, device or workload posture and
other signals; continuously monitor assets; and enforce authentication and authorisation strictly before
access. "Assume breach" summarises the attitude: design so that any one compromised component has a
small blast radius.

### The architecture: PDP and PEP
Every access passes a **policy enforcement point** (PEP), such as a gateway, a sidecar proxy or the tool
dispatcher. The PEP asks a **policy decision point** (PDP), which evaluates identity, resource, action and
context against policy and answers allow or deny. Separating them means policy is written once and
enforced uniformly, *because* scattering `if user.is_admin` checks across services is how gaps appear.

### Identity for everything, including workloads
Humans authenticate through SSO with MFA. Services authenticate with workload identities, for example
SPIFFE-style identities carried in mutual TLS certificates or cloud IAM roles, rather than shared static
API keys. Credentials are short-lived *because* a stolen token that expires in minutes is worth far less
than a key valid for a year.

### Applying it to agents and models
- **The agent is a principal, and it acts on behalf of a user.** Use delegated credentials, for example
  OAuth token exchange (RFC 8693), so a downstream API sees both the agent and the user and can apply the
  user's permissions. A shared service account that can read every customer's data turns any injection
  into a cross-tenant breach.
- **Scope per task.** Mint tokens with the narrowest scopes and audiences the current tool call needs,
  rather than one token with every scope for the whole session.
- **Authorise every tool call at the PEP.** The model proposing a call is not authorisation; the PEP
  checks the delegated user's rights, the resource, the arguments and session context such as whether
  untrusted content has been read.
- **Treat model output as untrusted input** to every downstream system: validate, parameterise, never
  execute it with ambient privileges.
- **Control egress.** Agents and code sandboxes get default-deny outbound network access with explicit
  allowlists, since exfiltration needs a way out.
- **Keep secrets out of the context.** Credentials are attached by the runtime at the PEP, never placed
  in the prompt where the model could repeat them.

### Continuous verification
Decisions are re-evaluated per request, so revoking a user, quarantining a workload or raising a risk
signal takes effect on the next call rather than at the next login. Every decision is logged with
principal, delegator, resource and outcome, which is what makes incident reconstruction possible.

## Example
A PEP in front of tools. The agent presents a delegated token; the PEP checks audience, expiry, scope
and the *user's* rights to the specific record before attaching the real backend credential.

```python
import time

def pdp_decide(claims: dict, tool: str, resource: dict) -> tuple[bool, str]:
    if claims["exp"] < time.time():
        return False, "token expired"
    if claims["aud"] != f"tool:{tool}":
        return False, "token minted for a different tool"
    if f"{tool}:{resource['action']}" not in claims["scope"].split():
        return False, "scope missing"
    if resource["tenant"] != claims["tenant"]:
        return False, "cross-tenant access"
    if resource["action"] == "write" and claims.get("session_tainted"):
        return False, "writes blocked after untrusted content"
    return True, "ok"

def pep_call(token_claims, tool, resource, backend):
    ok, why = pdp_decide(token_claims, tool, resource)
    audit = {"sub": token_claims["sub"], "act": token_claims["act"]["sub"], "tool": tool,
             "res": resource["id"], "decision": why}
    print(audit)                                   # ship to the audit log
    return backend(resource) if ok else None       # backend secret added here, never in the prompt

claims = {"sub": "user:ana", "act": {"sub": "agent:support-bot"}, "tenant": "t-42",
          "aud": "tool:crm", "scope": "crm:read", "exp": time.time() + 300,
          "session_tainted": True}
read_ok = pep_call(claims, "crm", {"id": "acct-9", "tenant": "t-42", "action": "read"}, lambda r: "row")
other   = pep_call(claims, "crm", {"id": "acct-1", "tenant": "t-7", "action": "read"}, lambda r: "row")
```

The `act` claim is how RFC 8693 records the actor in a delegated token. The second call fails even if an injected instruction convinced the model to fetch another tenant's
account, because the decision uses the delegated user's tenant, not the agent's network position.

## Trade-offs
- Per-request authorisation shrinks blast radius but adds latency and a PDP dependency; caching
  decisions briefly helps, and stops being acceptable when revocation must take effect immediately.
- Short-lived delegated tokens limit theft value but need a token service and refresh logic; static keys
  are simpler only for low-risk, single-tenant internal tools.
- Default-deny egress blocks exfiltration channels but breaks agents that legitimately browse; an
  allowlist works for known APIs and flips to a proxy with inspection for open-web tasks.
- User-delegated permissions keep agents within what the user may do, yet some workflows need actions
  the user cannot perform, which then require an explicit, audited elevation path.
- Central policy engines give consistency, but a policy bug now affects every service at once, so policy
  changes need review and tests like code.

## Pitfalls
- Running the agent under one broad service account; the symptom is that audit logs show only
  "support-bot" and you cannot tell which user's request caused an action.
- Putting API keys in the system prompt or tool descriptions; they appear in traces and can be echoed
  back by an injected instruction.
- Calling the setup "zero trust" because it uses mTLS, while authorisation is still coarse; encrypted
  lateral movement is still lateral movement.
- Trusting MCP servers or internal tools because they run inside the VPC; a compromised or malicious tool
  server returns poisoned results and receives whatever arguments the agent sends.
- Long-lived tokens cached in agent memory or checkpoints, which turn a stored-state leak into a
  credential leak weeks later.

## When to use
Adopt zero-trust principles for any AI system that holds credentials to real data or can take actions,
which means any agent or tool-using assistant, and especially multi-tenant products. Start with the
highest-value move: delegated, scoped, short-lived credentials and a single PEP in front of tools. Full
organisation-wide zero trust is a multi-year programme; you do not need it to apply the pattern to one
agent. Measure the share of tool calls carrying user delegation, token lifetimes, denied-decision rates
and egress destinations. In interviews, explain why network location says nothing about the intent an
agent is currently serving.

## Role lens
- **Engineer:** Implements token exchange, scoped tool tokens, the PEP check before each tool call, and
  injects backend secrets at dispatch rather than in prompts.
- **Architect:** Decides where the PDP and PEPs sit, which identities exist for agents and tools, and
  which actions require elevation or human approval.
- **FDE:** Integrates with the customer's IdP and IAM, maps their roles into delegated scopes, and
  justifies egress allowlists to their security team.

## Check yourself
**Q:** An agent in a customer's VPC reads emails and can query their ERP through a service account with
read access to all business units. Security says it is fine because the VPC has no inbound internet
access. What is wrong with that argument, and what would you change?
**A:** Inbound network controls do not address the threat, because the attacker's instructions arrive in
emails the agent is designed to read; injection steers the agent, which then uses its all-units
credential from inside. Network location says nothing about whose intent the agent is serving. Replace
the service account with delegated credentials so ERP queries run with the requesting user's
permissions, scope tokens per tool and short expiry, put a PEP in front of the ERP tool that checks the
user's business unit, restrict egress, and log principal plus delegator for every call.

## Quiz
### Q: What is the main reason a shared service account is a poor identity for a multi-user agent?
- [ ] Service accounts cannot use mutual TLS
- [ ] Service account tokens are always long-lived by design
- [x] Every request runs with the union of all users' access, so a misled agent can reach any user's data
- [ ] Identity providers do not allow service accounts to call APIs
> A shared account erases who the agent is acting for, so authorisation cannot apply the user's limits
> and logs cannot attribute actions. Delegated tokens keep both the agent and the user visible.

### Q: In a zero-trust architecture, what does the policy enforcement point do?
- [ ] It authors and stores the access policies for the organisation
- [x] It intercepts each access, asks the decision point for a verdict, and allows or blocks the request
- [ ] It issues long-lived credentials to trusted internal workloads
- [ ] It monitors network traffic for anomalies after access has been granted
> The PEP sits in the request path and enforces; the PDP evaluates policy. Separating them keeps policy
> consistent while enforcement happens at every gateway, proxy or tool dispatcher.

### Q: Which control applies zero trust most directly to an agent's tool calls?
- [ ] Moving the agent to a private subnet with no inbound access
- [ ] Encrypting the vector database at rest
- [ ] Adding a system prompt telling the agent to respect user permissions
- [x] Authorising each call with a short-lived token scoped to that tool and the delegating user's rights
> Zero trust verifies every request against identity and policy. Network isolation and encryption at
> rest are useful but do not decide whether a specific call is permitted, and prompts enforce nothing.
