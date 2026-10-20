---
id: api-keys-oauth2-jwt
module: fastapi-for-ai
name: API keys / OAuth2 / JWT
kind: decision
difficulty: core
est_minutes: 20
related:
  - fastapi-for-ai/dependency-injection
  - fastapi-for-ai/cors
  - fastapi-for-ai/rate-limiting
  - backend-fundamentals/authentication-vs-authorization
  - backend-fundamentals/oauth-2-0-oidc
  - backend-fundamentals/secrets-management
  - enterprise-integration/sso-iam-integration
  - agents/mcp-authentication-authorization-transport-security
sources:
  - Hardt, 2012 — RFC 6749: The OAuth 2.0 Authorization Framework
  - Jones, Bradley, Sakimura, 2015 — RFC 7519: JSON Web Token (JWT)
  - Official docs: FastAPI (Security)
  - Official docs: PyJWT
---

## TL;DR
An AI API has to know who is calling, both to bill tokens and to scope data. API keys are long-lived
shared secrets, fine for server-to-server calls. OAuth2 is a framework for *delegating* access, so a
user or service gets a token from an identity provider instead of handing you a password. A JWT is a
signed, self-contained token format that lets your API verify that token without a database lookup.
They solve different problems and are often used together.

## Why it exists
Every request to an LLM endpoint costs money and touches data, so you need an answer to two questions:
*who is this* (authentication) and *what may they do* (authorization: which tenant's documents, which
models, how many tokens). Different callers need different answers. A customer's backend calling
your inference API is a machine with no human present. A user in your chat UI is a person who logged
in through their company's SSO. An agent calling a tool on behalf of that user needs access limited
to what the user allowed.

The naive approaches each fail at one of these. Shipping a username and password with every request
means every service handles raw passwords, and you can't revoke one integration without changing
the password everywhere. A single shared static token for "the front end" can't tell users apart,
which breaks tenant isolation and per-user billing. Opaque session IDs checked against a database on
every call work, but each microservice and tool then needs access to the session store, which adds a
lookup to every hop.

The three mechanisms answer those failures. API keys give machines a revocable, per-integration
credential. OAuth2 lets an identity provider authenticate the human, possibly with MFA and SSO, and
hand your API a scoped token, so your code never sees a password. JWTs carry identity and scopes in a
signed payload that any service holding the public key can verify locally.

## How it works
### API keys
Generate 32 or more random bytes (`secrets.token_urlsafe(32)`), show the key to the user once, and
store only a hash. A fast hash like SHA-256 is fine here, unlike for passwords, because a random
256-bit key can't be brute-forced. A visible prefix (`sk_live_ab12...`) lets you identify the key in
logs and leaked-secret scanners without storing it in plaintext. On each request, hash the presented
key and look it up, which gives you the tenant, scopes and rate-limit tier. Revocation is immediate
because you check the store every time. The weakness is that the key is a bearer secret with no
expiry: anyone who copies it from a log, a front-end bundle or a notebook is that customer until you
rotate it. So keys never go into browsers, and you support several active keys per account so
rotation needs no downtime.

### OAuth2
OAuth2 (RFC 6749) defines *roles* (resource owner, client, authorization server, resource server) and
*grants* for getting an access token:
- **Authorization code + PKCE**, for users. The browser is redirected to the identity provider (Okta,
  Entra ID, Auth0, Google), the user logs in there, and the client exchanges a one-time code for tokens.
  PKCE binds the code to the client that started the flow, so an intercepted code is useless.
- **Client credentials**, for machine-to-machine calls. A service authenticates with its own client ID
  and secret and gets a short-lived token, with no user involved.
- The **password grant** (the client collects the user's password) is discouraged by current security
  guidance. FastAPI's tutorial uses `OAuth2PasswordBearer` because it is easy to demonstrate, but that
  class only *extracts* a bearer token from the `Authorization` header; it doesn't tie you to that flow.

Your FastAPI app is usually just the **resource server**: it receives `Authorization: Bearer <token>`
and validates it. OpenID Connect adds an ID token and standard user claims on top of OAuth2. It is
what "Sign in with SSO" usually means.

### JWT
A JWT (RFC 7519) is `base64url(header).base64url(payload).signature`. The payload carries claims:
`sub` (subject), `iss` (issuer), `aud` (audience), `exp` (expiry), `iat`, plus custom ones like
`tenant_id` or `scope`. It is signed, not encrypted, so anyone can read it and nothing secret goes in
it. Verifying it means:
1. **Pin the algorithm** to the expected one (`RS256`/`ES256` for IdP-issued tokens). Accepting the
   header's `alg` blindly enables `alg: none` and RS-to-HS key-confusion attacks.
2. Fetch the issuer's public keys from its **JWKS** endpoint (cache them, refresh on unknown `kid`)
   and check the signature.
3. Check `exp`/`nbf` (with small clock-skew leeway), `iss`, and **`aud`**, so a token minted for
   another API can't be replayed against yours.
4. Authorize from the claims: scopes, tenant.

Verification needs no network call per request, which is why JWTs scale across services. The cost is
**revocation**: a stolen token stays valid until `exp`. Hence the standard design is short-lived
access tokens (minutes) plus longer-lived refresh tokens that the IdP *can* revoke.

### In FastAPI
`fastapi.security` provides `APIKeyHeader`, `HTTPBearer` and `OAuth2PasswordBearer`. They extract
credentials and document the scheme in OpenAPI, so the docs UI gets an "Authorize" button. They don't
verify anything themselves. Verification lives in a dependency that returns a typed principal, and
`Security(dep, scopes=[...])` with `SecurityScopes` lets each route declare the scopes it needs.

## Example
One dependency that accepts either a customer API key (server-to-server) or an IdP-issued JWT
(users), and returns the same principal type.

```python
import hashlib, jwt
from dataclasses import dataclass
from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials

ISSUER, AUDIENCE = "https://login.acme-idp.com/", "api://ai-platform"
jwks = jwt.PyJWKClient(ISSUER + ".well-known/jwks.json")   # caches keys; fetch is sync, warm it at startup
api_key_hdr = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer = HTTPBearer(auto_error=False)
KEYS = {}   # sha256(key) -> {"tenant": ..., "scopes": [...]}, really a DB table

@dataclass
class Principal:
    tenant: str; subject: str; scopes: set[str]

async def principal(key: str | None = Security(api_key_hdr),
                    cred: HTTPAuthorizationCredentials | None = Security(bearer)) -> Principal:
    if key:
        rec = KEYS.get(hashlib.sha256(key.encode()).hexdigest())
        if rec:
            return Principal(rec["tenant"], "key:" + key[:12], set(rec["scopes"]))
    elif cred:
        try:
            signing_key = jwks.get_signing_key_from_jwt(cred.credentials).key
            c = jwt.decode(cred.credentials, signing_key, algorithms=["RS256"],
                           audience=AUDIENCE, issuer=ISSUER, leeway=30)
            return Principal(c["tenant_id"], c["sub"], set(c.get("scope", "").split()))
        except (jwt.PyJWTError, KeyError):
            pass
    raise HTTPException(401, "invalid or missing credentials",
                        headers={"WWW-Authenticate": "Bearer"})

def require(scope: str):
    def check(p: Principal = Depends(principal)) -> Principal:
        if scope not in p.scopes:
            raise HTTPException(403, f"missing scope {scope}")
        return p
    return check

app = FastAPI()

@app.post("/v1/chat")
async def chat(body: dict, p: Principal = Depends(require("chat:write"))):
    return {"tenant": p.tenant, "reply": "..."}    # every query below filters by p.tenant
```

401 means "I don't know who you are" (with `WWW-Authenticate`). 403 means "I know, and you can't
do this".

## Trade-offs
- API keys are trivial for customers to use and revoke instantly, but they are long-lived bearer
  secrets. Prefer OAuth client credentials when customers already run an IdP or demand short-lived
  credentials.
- JWTs verify locally and scale across services, but revocation waits for expiry. Opaque tokens with
  introspection revoke instantly at the cost of a network call per request, and they win for
  high-risk actions.
- Short access-token lifetimes shrink the window for a stolen token, but they mean more refresh
  traffic and more client complexity. The right lifetime flips shorter as the blast radius grows.
- Delegating to the customer's IdP (OIDC/SSO) offloads MFA and offboarding to them, but you inherit
  their claim formats and outages. That is usually a requirement in enterprise deals, not a choice.

## Pitfalls
- Decoding a JWT without verifying, or without pinning `algorithms`. The symptom is a pentest finding
  where a hand-crafted token with a forged `tenant_id` is accepted.
- Skipping the `aud` check. The symptom is that a token issued for a sibling internal API works against
  your AI endpoint, which is a lateral-movement path security reviewers flag.
- Putting API keys in a browser bundle or a client-side notebook. The symptom is token spend from
  unknown IPs and the key turning up in a public repository scanner alert.
- Authenticating but not scoping queries by tenant. The symptom is one customer's retrieved documents
  appearing in another customer's answers, which is the worst possible AI incident.
- Logging full `Authorization` headers or keys in request logs. The symptom is credentials sitting in
  your log platform, readable by everyone with log access.

## When to use
Offer API keys for programmatic access by customers' backends, scoped per key with rate-limit tiers.
Use OAuth2/OIDC with the authorization-code + PKCE flow for human users, especially enterprise ones
who expect SSO, and validate the resulting JWTs at your API. Use client credentials for
service-to-service traffic inside your platform or from customers with an IdP. Use JWTs as the access
token format when many services must verify cheaply, and keep them short-lived. Don't build your own
login or token issuer unless that is your product. Measure 401/403 rates per client and alert on
spikes, which mean either a broken integration or an attack. In interviews, be ready to explain 401
versus 403, why `aud` matters, and how you revoke a JWT.

## Role lens
- **Engineer:** Writes the principal dependency, pins JWT algorithms and audience, hashes API keys, and
  makes every data access path take the tenant from the principal, never from the request body.
- **Architect:** Chooses the identity model: IdP, token formats and lifetimes, key-versus-token per
  client type, and how user identity propagates to agents and tools without over-broad credentials.
- **FDE:** Wires the product into the customer's Entra ID or Okta, maps their group claims to scopes,
  and handles the security questionnaire about key storage and rotation.

## Check yourself
**Q:** An agent in your platform calls a document-search tool service on behalf of a user. Should the
tool service trust a header `X-User-Id` set by the agent service, accept the user's original JWT, or
something else? Reason about it.
**A:** A bare `X-User-Id` header is only as trustworthy as the network. Anything that can reach the
tool service can impersonate any user, and a prompt-injected agent could set it to anyone. Forwarding
the user's JWT works if the tool service validates it with the right audience, but it gives the tool
service the user's full access token. The better pattern is token exchange or a downscoped token: the
agent service presents the user's token to the IdP and gets a short-lived token whose audience is the
tool service and whose scope is only the needed read permission. The tool verifies signature, `aud`
and scope, and filters by the tenant in the claims. The blast radius stays small and audit logs show
both user and agent.

## Quiz
### Q: Why is SHA-256 acceptable for storing API keys when it is unacceptable for passwords?
- [ ] SHA-256 is slower than bcrypt
- [x] API keys are long random values, so brute-forcing the hash is infeasible, whereas passwords are low-entropy and guessable
- [ ] API keys are never stolen
- [ ] SHA-256 hashes can be reversed with the server's secret
> Slow password hashes exist to resist guessing low-entropy human passwords. A 256-bit random key has
> no guessable structure, so a fast hash suffices and keeps per-request lookup cheap.

### Q: What does `aud` validation in JWT verification prevent?
- [ ] Tokens being read by third parties
- [ ] Expired tokens being accepted
- [x] A valid token minted for a different API being replayed against yours
- [ ] Clock skew between servers
> The signature proves the IdP issued the token, not that it was meant for you. Checking the audience
> ensures the token's intended recipient is your API, blocking cross-service replay.

### Q: Your API validates JWTs locally. A user's laptop is stolen and their token must stop working now. What is the realistic answer?
- [ ] Change the JWT signing algorithm
- [ ] Delete the user's row; the token will fail verification
- [x] Revoke the refresh token at the IdP and rely on a short access-token expiry, or add a denylist/introspection check for high-risk routes
- [ ] JWTs can be revoked by sending a DELETE to the token
> Local verification never consults a store, so an issued access token stays valid until exp. That is
> why access tokens are short-lived, refresh tokens are revocable, and sensitive routes may introspect.
