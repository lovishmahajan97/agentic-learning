---
id: authentication-vs-authorization
module: backend-fundamentals
name: Authentication vs authorization
kind: concept
difficulty: intro
est_minutes: 14
status: reviewed
related:
  - backend-fundamentals/oauth-2-0-oidc
  - backend-fundamentals/rbac
  - fastapi-for-ai/api-keys-oauth2-jwt
  - enterprise-integration/sso-iam-integration
  - enterprise-integration/rbac-abac-mapping-to-customer-identity
  - agent-runtime-architecture/approval-and-authorization-boundaries
  - agents/mcp-authentication-authorization-transport-security
  - rag-retrieval/acl-aware-retrieval
sources:
  - OWASP, 2021 — Top 10: A01 Broken Access Control and A07 Identification and Authentication Failures
  - OWASP Cheat Sheet Series — Authentication and Authorization Cheat Sheets
  - Hardt, 2012 — RFC 6749: The OAuth 2.0 Authorization Framework
  - Pang et al., 2019 — Zanzibar: Google's Consistent, Global Authorization System
---

## TL;DR
Authentication turns a credential into a verified *principal*: who is making this request.
Authorization decides whether that principal may perform *this action* on *this resource*. They are
different questions, answered at different points in the request, with different failure codes: 401
means "I do not know who you are", 403 means "I know, and no". Most serious access-control bugs come
from doing the first and assuming it implies the second.

## Why it exists
The forcing problem is the second user. With one user, "logged in" and "allowed" are the same thing.
With two, a request for `/documents/42` from a logged-in user says nothing about whether document 42 is
theirs, and the moment the server stops asking, user A reads user B's data by editing a number in the
URL. That single mistake, a valid identity treated as sufficient permission, is what OWASP ranks as the
top web application risk in its 2021 Top 10 (broken access control), and it is a staple finding of
penetration tests.

The naive alternative is to fold the two together: check at login, then trust everything after. It
fails because the questions have different inputs and change at different rates. Identity is
established once per session from a credential; permission depends on the specific resource, the
action, the tenant, the user's current role and sometimes the time of day, and must be re-evaluated on
every request because any of those can change between two calls. Folding them also pushes authorization
into the user interface (hide the button), which any client that is not your UI simply ignores.

Keeping them separate buys three things. Several authentication methods (password, single sign-on, API
key, service identity) can feed one authorization model. Permission policy can change without touching
login. And audit becomes possible: *who* did *what* is only answerable when the principal is explicit.
For AI systems the separation is sharper still: an agent calling tools acts *on behalf of* a user, so
authentication must say which user, and authorization must be evaluated per tool call against that
user's permissions rather than the agent's own, broad service account.

## How it works
### Authentication: credential to principal
A credential is evidence: something the caller knows (password), has (hardware key, phone, signed
token), or is (biometric). The authentication layer verifies the evidence and produces a **principal**,
a small object such as `{user_id, tenant_id, roles, auth_method, auth_time}`, attached to the request
context. Everything downstream trusts that object and never re-examines the credential, which is why
this layer must be a single, well-tested chokepoint (middleware or a dependency), not code repeated in
each handler.

The common mechanisms differ in what is verified and where state lives. Passwords are checked against a
slow, salted hash (bcrypt, scrypt or Argon2) *because* a leaked database must not be crackable offline
at GPU speed. After login the browser holds a **session** cookie: an opaque id looked up in a server-side
store, which makes revocation instant but requires every server to reach the store. Because the browser
attaches cookies automatically, the cookie needs `HttpOnly` (script cannot read it, so an XSS bug cannot
exfiltrate it), `Secure` (never sent over plain HTTP) and `SameSite=Lax` or `Strict` (not sent on most
cross-site requests), and state-changing endpoints still need CSRF protection where `SameSite` alone does
not cover the case. Issue a fresh session id at login rather than upgrading the anonymous one, or an
attacker who planted a known id before login inherits the authenticated session (session fixation). A
second factor (TOTP, or a phishing-resistant WebAuthn key) raises the bar from "knows the password" to
"also holds the device", which matters because reused and phished passwords are the common way in. API clients send a
**bearer token**: either opaque (looked up like a session) or a self-contained **JWT**, which the server
verifies locally with the issuer's public key. A JWT check must cover four things: signature, issuer,
audience and expiry. Signature verification includes pinning the accepted algorithm rather than trusting
the token's own `alg` header, which is how `none`-algorithm and key-confusion attacks get in. Audience is
the one people skip, and skipping it means a token minted for another service is accepted by yours. Because a JWT cannot be revoked before it expires without server state,
access tokens are short-lived and paired with a refresh token. **API keys** identify machines and are
stored only as a hash. Unlike passwords they are long random strings, so a fast hash such as SHA-256 or
an HMAC is enough (a slow password hash would add latency to every request for no gain); compare in
constant time, and give keys a recognisable prefix so secret scanners can spot a leaked key and you can
tell which key it is without exposing the secret. Service-to-service calls use mutual TLS or platform
identities. Single sign-on delegates all of this to an identity provider through OIDC or SAML; the
OAuth/OIDC lesson covers the flows.

### Authorization: (principal, action, resource) to allow or deny
Authorization is a function of three inputs plus context, evaluated with **default deny**. The simplest
form is an ownership check, `resource.owner_id == principal.user_id`, and its absence is the bug above.
Beyond ownership there are three families. **RBAC** maps principals to roles and roles to permissions,
and is what most products start with. **ABAC** evaluates policies over attributes of the principal, the
resource and the environment ("analysts may read documents whose classification is at most internal,
from the corporate network"). **ReBAC** answers through relationships ("user is an editor of this
document via membership of this group"), which is the Zanzibar model behind systems such as OpenFGA and
suits deeply shared, hierarchical data.

Collections need the same care as single resources. `GET /documents/42` is authorized by loading 42 and
checking it; `GET /documents?q=refund` must put the permission into the query itself
(`WHERE tenant_id = $1 AND (owner_id = $2 OR ...)`), because fetching a page and filtering afterwards
returns short or empty pages, leaks totals through counts, and on a vector search lets disallowed chunks
crowd out allowed ones in the top-k.

Two placement rules matter more than the model. First, enforce on the server, as close to the data as
possible, on every request; the UI hiding a button is not authorization. In multi-tenant systems every
row carries a `tenant_id`, every query filters by it, and database row-level security acts as a backstop
for the query someone forgets. Second, separate the **enforcement point** (the handler or middleware that
asks) from the **decision point** (the library or policy service that answers: Casbin, OPA, Cedar,
OpenFGA, or your own module), so policy is written once and testable in isolation.

OAuth **scopes** are often mistaken for permissions. A scope bounds what a *client application* was
allowed to request on the user's behalf; a permission is what the *user* actually holds. The effective
right is the intersection: a `documents:write` scope on a token for a user who can only read grants
nothing.

### Order inside a request
Authenticate first, in middleware: invalid or missing credential, respond 401 with a `WWW-Authenticate`
header saying how to authenticate. Then, in the handler, load the resource and authorize: denied,
respond 403, or 404 when revealing that the resource exists would itself leak information (a document id
belonging to another tenant). Never reverse the order; an authorization check cannot run without a
principal, and an unauthenticated caller must not learn which ids exist.

### Agents are principals too
An agent runtime introduces a new principal (the agent, or the tool server) that acts for an existing one
(the user). If the runtime calls tools with its own powerful credentials and relies on the prompt to stay
within the user's permissions, it is a **confused deputy**: the model can be talked into fetching what
the user may not see. The fix is to carry the user's identity into every tool call (token exchange, or
passing the principal to the tool) and let the tool's authorization layer decide, which also makes
retrieval permission-aware. Human approval gates for consequential actions are authorization decisions
made at a different time, by a different decider.

## Example
A FastAPI endpoint with the two concerns in two places. `current_principal` is the authentication
chokepoint: it verifies signature, issuer, audience and expiry of a JWT via the identity provider's
published keys (PyJWT). PyJWT checks `exp` only if the claim is present, so `require` makes its absence
an error too. `authorize` is the decision function: tenant match first (returning 404 so other
tenants' ids are not discoverable), then ownership or an explicit permission.

```python
import jwt                                   # PyJWT
from fastapi import Depends, FastAPI, HTTPException, Request

app = FastAPI()
JWKS = jwt.PyJWKClient("https://idp.example.com/.well-known/jwks.json")

class Principal:
    def __init__(self, claims):
        self.user_id = claims["sub"]
        self.tenant_id = claims["tenant"]
        self.permissions = set(claims.get("permissions", []))

def current_principal(request: Request) -> Principal:        # AUTHENTICATION
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})
    token = auth.removeprefix("Bearer ")
    try:
        key = JWKS.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"],     # pinned, never from the header
                            issuer="https://idp.example.com", audience="docs-api",
                            options={"require": ["exp", "sub", "tenant"]})
    except jwt.PyJWTError:                                   # bad signature, issuer, audience or expiry
        raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})
    return Principal(claims)

def authorize(p: Principal, action: str, doc) -> None:       # AUTHORIZATION, default deny
    if doc.tenant_id != p.tenant_id:
        raise HTTPException(404)                             # do not reveal cross-tenant existence
    if doc.owner_id == p.user_id or f"documents:{action}" in p.permissions:
        return
    raise HTTPException(403)

@app.delete("/documents/{doc_id}")
def delete_document(doc_id: int, p: Principal = Depends(current_principal)):
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    authorize(p, "delete", doc)
    db.delete_document(doc_id, actor=p.user_id)              # audit records who, not just what
    return {"deleted": doc_id}
```

`db` is the application's data layer. Note what is *not* here: no permission logic in the token parsing,
no identity logic in the handler, and the tenant check runs before any content-dependent decision.

## Trade-offs
- Stateless JWTs avoid a store lookup per request, but cannot be revoked before expiry; the trade flips
  toward server-side sessions when you must log a user out instantly, as in offboarding or compromise.
- A central policy service gives one consistent, auditable model across services, but adds latency and a
  dependency on every request; inline checks are fine until a second service needs the same rules.
- Fine-grained permissions give precision and least privilege, but the policy surface grows and becomes
  hard to review; coarse roles are enough until customers demand custom roles or per-resource sharing.
- Returning 404 instead of 403 for cross-tenant resources hides existence from attackers, but confuses
  your own debugging and support unless the server logs the real reason.
- Long-lived API keys are simple for machine clients, but never expire on their own; the trade flips
  toward short-lived, scoped tokens once keys are shared across customer systems or appear in logs.

## Pitfalls
- Insecure direct object reference: authentication present, ownership check absent; the symptom is that
  changing an id in a URL returns another user's record, and it is the first thing a pen tester tries.
- Skipping the audience check on JWTs; the symptom is a token issued for one API being accepted by
  another, so a compromise of a low-value service unlocks a high-value one.
- Authorization only in the front end; the symptom is that `curl` with a valid token bypasses every
  restriction the UI shows.
- A single agent service account holding the union of all users' permissions; the symptom is a user
  asking the agent for a document they cannot open in the app and receiving it.
- Caching authorization decisions beyond revocation; the symptom is a removed employee or deleted share
  still working minutes or hours later.
- Treating "valid API key" as "known tenant" without binding keys to tenants and filtering every query;
  the symptom is cross-tenant data appearing in search or RAG results.
- Storing API keys or passwords in plaintext, or passwords under a fast unsalted hash; the symptom is
  that one database dump exposes every customer's credentials.
- Returning 403 for an unauthenticated request; the symptom is clients that never retry with
  credentials because the status told them they were already identified.

## When to use
Every multi-user system needs both, so the decisions are about shape. Use server-side sessions for
browser logins where instant logout matters; use short-lived JWTs with refresh tokens for APIs and
mobile clients; use hashed, prefixed, tenant-bound API keys for machine access; delegate login to an
identity provider as soon as an enterprise customer appears. Start authorization with ownership plus
RBAC, and introduce ABAC or ReBAC only when sharing rules or attribute policies outgrow roles. Measure
401 and 403 rates per endpoint, failed-login rates per account, and authorization decision latency. In
daily work this is the dependency that produces the principal and the check at the top of every
mutating handler. In interviews, expect to explain 401 versus 403, design the checks for a multi-tenant
endpoint, and describe how an agent should act on behalf of a user without a confused-deputy hole.

## Role lens
- **Engineer:** Builds the authentication dependency once, verifies all four JWT properties, writes a
  default-deny `authorize` function with tenant and ownership checks, and tests it with another tenant's ids.
- **Architect:** Owns the identity and permission model: session versus token, where policy is decided,
  how tenant isolation is enforced at the data layer, and how agents carry user identity into tools.
- **FDE:** Maps the customer's identity provider, groups and roles onto the product's principal and
  permission model, and proves to their security team that retrieval and tool calls respect existing ACLs.

## Check yourself
**Q:** An agent platform executes tools with one service account that can read every customer's
documents, and the system prompt instructs the model to "only access the current user's documents". The
customer's security team asks whether this is acceptable. What do you tell them and what would you change?
**A:** It is not acceptable, because the prompt is doing authorization and a prompt is not a security
boundary: the model can be induced through injection or error to request another user's document, and
the service account will be allowed to fetch it. Authentication is fine (the user is known at the API
edge) but that identity is dropped before the tool call, which is the confused-deputy pattern. The fix
is to carry the user's principal into every tool invocation, by token exchange for a user-scoped token
or by passing the principal explicitly, and to have the document service authorize each read against
that user's tenant and permissions with default deny. Retrieval should filter by the user's ACLs at
query time, and the agent's own account should hold only the minimal rights it needs for itself. Audit
logs then record the user, not the agent, as the actor.

## Quiz
### Q: A request arrives with no credential at all. Which response is correct and why?
- [ ] 403 Forbidden, because the caller is not allowed to access the resource
- [x] 401 Unauthorized with a WWW-Authenticate header, because the server cannot establish who is calling
- [ ] 404 Not Found, to avoid revealing that the endpoint exists
- [ ] 400 Bad Request, because the Authorization header is required
> 401 signals an authentication problem and tells the client how to supply credentials. 403 would claim
> identity was established and permission denied, which is false and stops clients from retrying with a token.

### Q: A JWT presented to your API has a valid signature from your identity provider and has not expired, but its audience claim names a different service. Should it be accepted?
- [ ] Yes, because the signature proves the identity provider issued it
- [ ] Yes, provided the subject exists in your user table
- [x] No, because a token minted for another service must not grant access to yours, even from the same issuer
- [ ] Only if it also carries the correct scopes
> Signature and expiry prove origin and freshness, not intended recipient. Accepting tokens regardless
> of audience lets a compromise of any service that shares the issuer become a compromise of all of them.

### Q: A logged-in user changes the id in `/invoices/118` to `/invoices/119` and sees another customer's invoice. Which layer failed?
- [ ] Authentication, because the session should have been bound to the invoice
- [x] Authorization, because the handler never checked that the principal may read that resource
- [ ] Transport security, because the id was visible in the URL
- [ ] Rate limiting, because enumeration of ids should have been blocked
> The user was correctly identified; the server then assumed identity implied permission. This is an
> insecure direct object reference, the classic broken-access-control bug, fixed by an ownership or tenant check.
