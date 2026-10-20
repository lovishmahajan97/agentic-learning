---
id: sso-iam-integration
module: enterprise-integration
name: SSO / IAM integration (Okta, Entra ID)
kind: practice
difficulty: core
est_minutes: 17
status: reviewed
related:
  - enterprise-integration/saml-sso
  - enterprise-integration/scim-provisioning
  - enterprise-integration/rbac-abac-mapping-to-customer-identity
  - backend-fundamentals/oauth-2-0-oidc
  - backend-fundamentals/authentication-vs-authorization
  - agents/mcp-authentication-authorization-transport-security
  - system-design/tenant-isolation
  - security-governance-compliance/zero-trust
sources:
  - Official docs: Microsoft identity platform (Entra ID)
  - Official docs: Okta Developer
  - OpenID Foundation, 2014 — OpenID Connect Core 1.0
---

## TL;DR
Enterprise customers will not let their employees create passwords in your product: identity must come
from their identity provider (IdP), usually Okta or Microsoft Entra ID, via OIDC or SAML. Integrating means
per-tenant IdP configuration, strict token validation, a stable user key (issuer plus subject, not email),
mapping IdP groups or app roles to permissions, and deciding how deprovisioning and agent delegation flow
from the IdP, because the customer's IdP, not your database, is now the source of truth for who someone is.

## Why it exists
An enterprise has thousands of employees joining, moving and leaving. Its security team enforces MFA,
device posture and conditional access in one place, the IdP, and audits who can reach which application.
A vendor with its own username/password store breaks every one of those controls: leavers keep access,
MFA policy is bypassed, and passwords get reused. So "supports SSO with our Okta / Entra tenant" is a
procurement checkbox, often a hard gate.

The naive implementation is "add a Sign in with Microsoft button". It works for one tenant and fails as a
B2B product. Accepting any Entra token means any organization's users can sign in to any customer's
workspace. Keying users by email breaks when someone's address changes or a mailbox is reassigned to a new
hire. Reading group names from tokens fails when groups are renamed, or when a user is in so many groups
that the token omits them. And signing someone in once tells you nothing about the day they are fired: your
session keeps working unless you wire deprovisioning in. SSO integration is the set of decisions that make
federated identity safe across many customer tenants.

## How it works
### Roles and protocols
The customer's IdP authenticates the user; your application is the relying party (OIDC) or service
provider (SAML). OIDC returns a signed JWT ID token and is the default for new integrations; SAML is
XML-based and still required by many enterprises (see the SAML lesson). Both Okta and Entra ID support both.

### The login flow (OIDC authorization code with PKCE)
1. Your app redirects the browser to the tenant's authorization endpoint with `client_id`, `redirect_uri`,
   `scope=openid profile email`, a random `state`, a random `nonce`, and a PKCE `code_challenge`. Store
   `state`, `nonce` and the PKCE verifier in the user's pre-login session, *because* each must later be
   matched against *this* browser: `state` defeats login CSRF, `nonce` ties the ID token to this request,
   PKCE stops a stolen authorization code from being redeemed by someone else.
2. The IdP authenticates the user and applies its policies (MFA, device compliance, conditional access),
   then redirects back with a short-lived `code`.
3. Your server exchanges the code at the token endpoint, authenticating as the client (secret or, better,
   a certificate or private-key JWT), and receives an ID token (and an access token if you asked for API scopes).
4. You validate the ID token (below), resolve the user, and issue **your own** session. The IdP's tokens
   are not your session, *because* their lifetime and revocation are controlled elsewhere and you still
   need your own idle and absolute timeouts.

If a tenant requires stronger authentication for some actions, the ID token's `acr`/`amr` claims say how
the user authenticated, and you can send them back to the IdP for step-up rather than trusting a stale login.

### Per-tenant configuration
Store, per customer tenant: protocol, issuer (or SAML entity ID), client ID, discovery or metadata URL,
allowed email domains, and claim mappings. *Because* each customer brings its own IdP, the login flow starts
with home realm discovery: the user types an email or picks a workspace, and you route them to that tenant's
IdP. In Entra ID, a SaaS app is typically registered once as multi-tenant and each customer's admin grants
consent; in Okta, each customer org adds your app (often from the Okta Integration Network) and you receive
an org-specific issuer. Okta also distinguishes the org authorization server (issuer
`https://<org>.okta.com`) from custom authorization servers (issuer `https://<org>.okta.com/oauth2/<id>`,
`default` being the common one); they have different issuers and keys, so record which one the customer
configured rather than assuming.

### Validating the token
For every ID token: verify the signature against the issuer's JWKS, check `iss` equals the configured issuer
for *this* tenant, `aud` equals your client ID, `exp`/`nbf` with small clock skew, and the `nonce` you sent.
For Entra multi-tenant apps the issuer contains the tenant ID (`tid`), so you must check that `tid` belongs
to a customer you onboarded; signature validity alone proves only that *some* Entra tenant issued it. This
is a common hole, *because* the multi-tenant discovery document (`/organizations` or `/common`) advertises
an issuer containing a `{tenantid}` placeholder, generic libraries cannot compare against it literally, and
the tempting fix is to switch issuer validation off. The correct fix is to substitute the onboarded tenant's
ID and compare exactly.

### Stable identity
Key users by issuer plus `sub`, or in Entra by `tid` plus `oid`, *because* those are immutable for the
account; email and UPN are display attributes that change.

### Authorization inputs
Prefer app roles (Entra) or a filtered groups claim (Okta) that the customer's admin assigns, over raw
group lists. Entra caps the groups it puts in a token (its documentation gives 200 for JWTs and 150 for
SAML assertions); above the cap it omits them and signals an overage (`_claim_names`/`_claim_sources` in a
JWT), in which case you must fetch membership from Microsoft Graph. Configuring the app to emit only groups
assigned to it avoids most overage, *because* users rarely hold more than a few of those. Map group or role *IDs*, not display names.

### Lifecycle and delegation
SSO controls sign-in, not existence. Pair it with SCIM so deactivation reaches you, and keep your session
lifetime short enough that a disabled user cannot ride an old cookie for days. The worst-case exposure after
termination is roughly the time for SCIM deactivation to arrive plus whatever your app does not revoke on
receipt; if you revoke sessions on SCIM events, session length matters only as a backstop. Where the IdP
supports OIDC back-channel logout, it can also tell you to end a session directly. When an agent calls downstream
APIs as the user, use a delegation flow such as Entra's on-behalf-of or OAuth token exchange, so the
downstream system sees the user's identity and scopes rather than a god-mode service account.

## Example
Validating OIDC ID tokens for many customer tenants with PyJWT. The tenant record comes from your
onboarding table; the issuer and JWKS come from that tenant's discovery document.

```python
import jwt, requests
from functools import lru_cache

TENANTS = {   # from your DB, written during customer onboarding
    "acme":   {"issuer": "https://login.microsoftonline.com/<acme-tenant-id>/v2.0",
               "client_id": "<our-app-client-id>"},
    "globex": {"issuer": "https://globex.okta.com/oauth2/default",
               "client_id": "<our-okta-client-id>"},
}

@lru_cache(maxsize=256)
def jwks_client(issuer: str) -> jwt.PyJWKClient:
    conf = requests.get(f"{issuer}/.well-known/openid-configuration", timeout=10).json()
    return jwt.PyJWKClient(conf["jwks_uri"])

def validate_id_token(tenant_slug: str, token: str, expected_nonce: str) -> dict:
    t = TENANTS[tenant_slug]                       # routed by home realm discovery, never by the token
    key = jwks_client(t["issuer"]).get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token, key, algorithms=["RS256"],
        audience=t["client_id"], issuer=t["issuer"],  # rejects tokens from any other tenant
        leeway=60, options={"require": ["exp", "iat", "iss", "aud", "sub"]},
    )
    if claims.get("nonce") != expected_nonce:
        raise PermissionError("nonce mismatch (possible replay)")
    user_key = (claims["iss"], claims.get("oid") or claims["sub"])  # stable, not email
    return {"user_key": user_key, "email": claims.get("email"),
            "roles": claims.get("roles", []), "groups": claims.get("groups", [])}
```

The crucial line is `issuer=t["issuer"]` chosen from the tenant the user was routed to: a valid token from
Globex's Okta presented to Acme's workspace fails validation instead of logging someone in.

## Trade-offs
- OIDC is simpler to implement and debug than SAML, but some customers' IdP policies or legacy setups
  mandate SAML; supporting both doubles the test matrix, so add SAML when the first deal requires it.
- Just-in-time provisioning on first login needs no extra integration, but never learns about leavers;
  SCIM adds that at the cost of another endpoint and per-IdP quirks.
- Emitting groups in tokens avoids extra calls, but large memberships bloat tokens or trigger overage;
  app roles assigned by the customer admin keep tokens small and intent explicit.
- Short application sessions make deprovisioning effective quickly, but force more IdP round-trips; long
  sessions are smoother and acceptable only when SCIM revocation kills sessions immediately.

## Pitfalls
- Accepting any Entra tenant's token in a multi-tenant app, often by disabling issuer validation to get
  past the `{tenantid}` placeholder; the symptom is a user from an unrelated company
  successfully reaching a customer workspace in a penetration test.
- Using email as the primary key; the symptom is a rehired or renamed employee getting a second account, or
  a new hire inheriting a predecessor's history.
- Mapping permissions by group display name; the symptom is access silently disappearing after the customer
  renames a group.
- Ignoring the groups overage case; the symptom is power users with many memberships having no permissions
  while everyone else works.
- Letting an agent call downstream systems with a shared service credential; the symptom is audit logs where
  every action belongs to one bot account.

## When to use
Any B2B product sold to companies with more than a handful of users needs IdP integration; plan it before
the first enterprise deal, not after. Start with OIDC plus per-tenant issuer validation, add SAML and SCIM as
customers require, and route authorization through a mapping layer rather than raw claims. Measure login
success rate per tenant, time from IdP deactivation to loss of access, and token-validation failures by
reason. In interviews, the signal is knowing that SSO is authentication only, and explaining tenant-bound
issuer checks, stable keys and deprovisioning.

## Role lens
- **Engineer:** Implements discovery, JWKS caching, issuer and audience checks per tenant, stable user keys,
  and the groups-overage fallback to Microsoft Graph.
- **Architect:** Owns the identity model: which protocols are supported, where tenant-to-IdP mapping lives,
  session lifetime policy and how agent delegation preserves the user's identity downstream.
- **FDE:** Walks the customer's IAM admin through app registration, consent, claim configuration and test
  users, then verifies a deactivated test account loses access within the agreed time.

## Check yourself
**Q:** Your multi-tenant app accepts Entra ID tokens. A pen tester signs in to a customer's workspace using an
account from their own personal Entra tenant. What went wrong and how do you fix it?
**A:** The app verified the signature against Microsoft's common keys and the audience, but did not bind the
token to the customer's tenant. Every Entra tenant's tokens are signed by the same key set, so a valid
signature proves nothing about which organization issued it. The fix is to resolve the tenant from the login
route (workspace or email domain), then require `tid` and `iss` to match the tenant ID stored at onboarding,
and to key users by `tid` plus `oid`. Add a regression test with a token from an unregistered tenant.

## Quiz
### Q: Why should users be keyed by issuer plus subject (or tid plus oid) rather than email?
- [ ] Email addresses are too long to index
- [x] Subject and object IDs are immutable for the account, while emails change and can be reassigned
- [ ] OIDC tokens never contain an email claim
- [ ] Email keys break the token signature
> Immutable identifiers keep one account per person across renames and prevent a recycled mailbox from
> inheriting someone else's data; email is a mutable display attribute, though usually present in tokens.

### Q: What does SSO by itself fail to handle when an employee leaves the customer?
- [ ] MFA enforcement on their next login
- [ ] Revoking their access at the IdP
- [x] Ending sessions and access already established in your application
- [ ] Signing the ID tokens they were issued
> Disabling the user at the IdP blocks new sign-ins, but your existing session lives on until it expires;
> SCIM deactivation plus session revocation or short sessions closes that gap.

### Q: An Entra user in many groups has an empty groups claim. What is the likely cause?
- [ ] The app registration is single-tenant
- [ ] The token signature failed verification
- [x] Group overage: Entra omitted the groups and expects the app to query Microsoft Graph for membership
- [ ] SCIM has not run yet
> When membership exceeds the token's limit, Entra sends an overage indicator instead of the list; apps must
> fall back to Graph, or better, use app roles assigned by the admin to keep tokens small.
