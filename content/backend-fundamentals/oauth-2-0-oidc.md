---
id: oauth-2-0-oidc
module: backend-fundamentals
name: OAuth 2.0 / OIDC
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - backend-fundamentals/authentication-vs-authorization
  - backend-fundamentals/rbac
  - backend-fundamentals/secrets-management
  - fastapi-for-ai/api-keys-oauth2-jwt
  - enterprise-integration/sso-iam-integration
  - enterprise-integration/saml-sso
  - agents/mcp-authentication-authorization-transport-security
sources:
  - Hardt (ed.), 2012 — The OAuth 2.0 Authorization Framework (RFC 6749)
  - Sakimura et al., 2014 — OpenID Connect Core 1.0
  - Sakimura, Bradley, Agarwal, 2015 — Proof Key for Code Exchange by OAuth Public Clients (RFC 7636)
  - Lodderstedt, Bradley, Labunets, Fett, 2025 — Best Current Practice for OAuth 2.0 Security (RFC 9700)
---

## TL;DR
OAuth 2.0 is a delegated-authorization framework: an application obtains a scoped, expiring
access token to call an API on a user's behalf without ever seeing the user's password. OpenID Connect
(OIDC) is a thin identity layer on top that adds a signed ID token so the application also learns *who*
logged in. OAuth answers "may this app do X for this user"; OIDC answers "who is this user". Most login
bugs come from confusing the tokens that answer each question.

## Why it exists
Before OAuth, a third-party app that wanted to read your calendar asked for your Google password. That
hands over everything, not just the calendar; it cannot be revoked per app (only by changing the
password, which breaks every app at once); it stops working the moment the account has MFA; and it
means dozens of apps store a credential that unlocks the whole account. The naive fix, a static per-user
API key, removes password sharing but keeps the rest: keys are long-lived, rarely scoped, hard to
rotate, and the user still copies a secret between systems by hand.

OAuth 2.0 (2012) restructured the problem into four roles: the **resource owner** (the user), the
**client** (the app), the **authorization server** (which authenticates the user, records consent and
issues tokens) and the **resource server** (the API, which accepts tokens). The user authenticates only
to the authorization server. The client receives a token that is scoped (`calendar.readonly`),
short-lived (minutes to an hour), revocable, and tied to that one client.

Then people started using OAuth for login: "if the app can call the profile API with this token, the
user must be who the profile says". That inference is wrong. An access token proves the *client was
authorized*, not *who is present at the keyboard*. An attacker can obtain a perfectly valid access token
for a victim through a malicious app and replay it against another app that trusts the profile endpoint
(token substitution). OIDC (2014) exists to close exactly that hole: it defines an **ID token**, a JWT
signed by the identity provider with the client's own id in the audience and a nonce bound to this login
attempt, so authentication becomes something you verify rather than something you infer.

## How it works
### Two channels, one principle
The **front channel** is the user's browser: untrusted, logged, visible in history and referrers. The
**back channel** is a TLS connection from the client's server to the authorization server. The design
rule that explains most of the protocol is *nothing valuable travels the front channel*. The browser
only ever carries a short-lived, single-use authorization code; tokens move on the back channel.

### Authorization code flow with PKCE, step by step
1. The client generates three random values: `state` (binds the callback to this browser session,
   defeating login CSRF), `nonce` (OIDC: binds the ID token to this attempt) and a PKCE pair, a
   `code_verifier` and `code_challenge = BASE64URL(SHA256(code_verifier))`. It stores all three in the
   user's server-side session.
2. It redirects the browser to the `/authorize` endpoint with `response_type=code`, `client_id`,
   `redirect_uri`, `scope=openid profile email …`, `state`, `nonce`, `code_challenge` and
   `code_challenge_method=S256`.
3. The authorization server authenticates the user however it likes (password, passkey, MFA, corporate
   SSO); the client sees none of it. It shows a consent screen listing the scopes.
4. It redirects back to `redirect_uri?code=…&state=…`. The client checks `state` matches the session.
5. **Back channel.** The client POSTs `code`, `redirect_uri`, `code_verifier` and, for a confidential
   client, its `client_secret` to `/token`. The server checks the code is unused and unexpired, that
   `SHA256(code_verifier)` equals the challenge from step 2, and that `redirect_uri` is identical to the
   one sent in step 2 (which `/authorize` already matched exactly against the registered list; exact
   matching matters *because* the redirect URI is where the code is delivered, so a wildcard or prefix
   match lets an attacker steer codes to a page they control). PKCE exists *because* public clients (SPAs, mobile apps, CLIs) cannot keep a secret and a
   code can be intercepted (a malicious app registering the same custom URL scheme, a proxy log). Binding
   the code to a value the interceptor never saw makes the stolen code worthless. The IETF security
   best current practice (RFC 9700) makes PKCE mandatory for public clients and recommends it for
   confidential clients too, since it also stops code injection.
6. The response carries `access_token`, `token_type`, `expires_in`, optionally `refresh_token`, and,
   when the `openid` scope was requested, `id_token`.

### Three tokens, three audiences
- **Access token.** For the resource server; its audience is the API. It may be opaque or a JWT, and
  the client must treat it as opaque either way. Sent as `Authorization: Bearer …`. The API validates it:
  for a JWT, signature against the issuer's JWKS plus `iss`, `aud`, `exp` and the required scopes; for an
  opaque token, a call to the introspection endpoint (RFC 7662).
- **ID token.** For the client; always a JWT. Claims: `iss`, `sub` (the stable user identifier, unique
  only *within* that issuer, so key your user table on the pair `(iss, sub)`, not on email, which can
  change or be re-issued), `aud` (must equal your
  `client_id`), `exp`, `iat`, plus `nonce` (present when the request sent one) and `auth_time` (when requested), and
  optionally `email`, `name`. Never send it to an
  API as a credential; its audience is you.
- **Refresh token.** Lets the client get fresh access tokens without the user present. It is the
  longest-lived secret in the system and therefore the most valuable to steal. Mitigations: rotation
  (each use returns a new one and reuse of an old one revokes the whole family), sender-constraining
  (DPoP or mTLS), and keeping it server-side only.

### Discovery and key rotation
An OIDC provider publishes `/.well-known/openid-configuration` listing its endpoints and a `jwks_uri`.
The JWKS holds public signing keys with a `kid`. Clients cache the set and refetch on an unknown `kid`,
which is how a provider rotates signing keys without breaking anyone.

### The other grants
- **Client credentials.** No user. A service authenticates as itself (secret, private-key JWT or mTLS)
  and gets a token carrying its own identity. Use it for batch jobs and for agents acting as themselves.
- **Device code.** For TVs and CLIs that cannot host a browser: the device polls while the user
  approves on a phone.
- **Token exchange (RFC 8693).** A service holding a user's token trades it at the authorization server
  for a new token with a different audience and usually narrower scope ("on-behalf-of"). This is the
  clean way for an API to call a downstream API, or for an agent's tool runtime to obtain a token for
  one tool, *because* forwarding the original token sends a credential whose audience is wrong and
  whose scope is wider than the downstream call needs.
- **Deprecated.** The implicit grant (token in the URL fragment, front channel) and the resource-owner
  password grant (the client sees the password) are both removed in the OAuth 2.1 draft. Do not build
  anything new on them.

### Scopes are not permissions
A scope is the upper bound the *user* granted the *client*. The resource server still applies the user's
own authorization underneath: `scope=repo` never lets an app delete a repository the user could not
delete themselves. Both checks run on every request (see RBAC).

## Example
A confidential web client (server-side) doing the authorization code flow with PKCE and validating
the ID token against the provider's JWKS. The pieces that matter are the three random values, the
back-channel exchange, and the claims that are checked before trusting `sub`.

```python
import base64, hashlib, secrets, urllib.parse, requests, jwt   # pip install requests pyjwt[crypto]

ISSUER, CLIENT_ID, CLIENT_SECRET = "https://login.example.com", "my-web-app", "<from secrets manager>"
REDIRECT_URI = "https://app.example.com/callback"
disc = requests.get(f"{ISSUER}/.well-known/openid-configuration").json()   # endpoints + jwks_uri
jwks = jwt.PyJWKClient(disc["jwks_uri"])   # caches keys; refetches when it meets an unknown kid

def start_login(session):            # 1. PKCE pair, state, nonce -> stored server-side, never in the URL
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    session.update(verifier=verifier, state=secrets.token_urlsafe(16), nonce=secrets.token_urlsafe(16))
    return disc["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
        "scope": "openid profile email", "state": session["state"], "nonce": session["nonce"],
        "code_challenge": challenge, "code_challenge_method": "S256"})

def handle_callback(code, returned_state, session):   # 2. browser returns ?code=...&state=...
    if returned_state != session["state"]:
        raise PermissionError("state mismatch: login CSRF or stale session")
    tok = requests.post(disc["token_endpoint"], data={             # back channel, TLS, server-to-server
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "code_verifier": session["verifier"]})
    tok.raise_for_status(); tok = tok.json()                   # error responses carry no tokens
    # 3. Validate the ID token: signature via JWKS, pinned algorithm, issuer, audience = our client_id, expiry
    key = jwks.get_signing_key_from_jwt(tok["id_token"])
    claims = jwt.decode(tok["id_token"], key.key, algorithms=["RS256"],
                        audience=CLIENT_ID, issuer=disc["issuer"])
    if claims.get("nonce") != session["nonce"]:
        raise PermissionError("nonce mismatch: token not minted for this login")
    user_key = (claims["iss"], claims["sub"])     # sub is unique per issuer; email is a display attribute
    return user_key, tok["access_token"], tok.get("refresh_token")   # keep tokens server-side
```

After `handle_callback` the app issues its own HttpOnly session cookie; the access token is used only
when the backend calls the provider's APIs on the user's behalf, and the refresh token never leaves
the server.

## Trade-offs
- Delegation without password sharing, paid for with redirect complexity, more moving parts and an
  authorization server on your login path; it flips to "a session cookie is enough" for an internal tool
  that talks only to its own backend and never to third parties.
- JWT access tokens let a resource server validate locally with no network call, but cannot be revoked
  before `exp` without adding a denylist lookup, which gives back the network call; opaque tokens with introspection revoke instantly at the cost of a call (or short cache)
  per request. Choose by how long a stolen token may stay valid.
- Short-lived access tokens shrink the damage from a leak, but move the risk onto the refresh token and
  the session store; without rotation and server-side storage you have only relocated the problem.
- An external identity provider (Okta, Entra ID, Google, Auth0) takes passwords, MFA and recovery out of
  your codebase, but adds a vendor on the critical path and provider-specific quirks in claim names and
  refresh policies. For B2B customers SSO is a requirement, so this is rarely a real choice.

## Pitfalls
- Treating an access token as proof of identity by calling `/userinfo` and trusting the result; the
  symptom is that a token issued to app A logs the same user into app B. Require an ID token and check
  `aud` and `nonce`.
- Omitting or not checking `state`; the symptom is login CSRF, where a victim's browser completes the
  attacker's login and the victim then uploads data into the attacker's account.
- Keying users on `email` instead of `(iss, sub)`; the symptom is duplicate accounts after a rename, or
  account takeover at providers that re-issue addresses, or when a second, multi-tenant issuer lets
  anyone assert an email they do not own.
- Registering wildcard or prefix-matched redirect URIs ("any path under app.example.com"); the symptom
  is codes delivered to an open-redirect or user-content page on your own domain, from where an
  attacker reads them out of the URL. Register exact URIs only.
- Decoding the JWT without pinning `algorithms`, `audience` and `issuer`; the symptom is that any token
  from the same provider for any other app is accepted, or that an `alg: none` token passes.
- Storing tokens in browser `localStorage`; the symptom is that one XSS exfiltrates every user's tokens.
  Use a backend-for-frontend with an HttpOnly session cookie and keep tokens server-side.
- Handing an agent or integration a broad, long-lived refresh token "to be safe"; the symptom is one
  compromised worker that can act as every user. Request minimal scopes, and use client credentials for
  work the agent does as itself.

## When to use
Reach for OIDC whenever humans log in and you do not want to own passwords, MFA and account recovery,
which for B2B products is nearly always: "do you support SSO" appears in the first security
questionnaire. Reach for the OAuth authorization code grant when your product must call APIs as the user
(Google Drive, GitHub, Slack, a CRM for an agent), and for client credentials when a service calls
another service. Skip it for a single internal app on a session cookie, and never use it as a substitute
for "the cron job needs a credential". Day to day the work is token validation middleware, mapping
provider claims to your roles, token storage and rotation, and provider quirks. In interviews, be able to
draw the code flow, explain why the exchange happens on the back channel, what PKCE prevents, and what
each of the three tokens is for and who validates it.

## Role lens
- **Engineer:** Implements the callback, validates ID tokens against the JWKS, stores refresh tokens
  server-side, and writes the bearer middleware that checks issuer, audience, expiry and scope on every
  API request.
- **Architect:** Decides client types and grants per surface (backend-for-frontend for SPAs, client
  credentials for workers, delegated user tokens for agents), picks JWT versus opaque access tokens by
  revocation needs, and owns the identity-provider boundary and the claim-to-role mapping.
- **FDE:** Wires the product into the customer's Okta or Entra tenant, debugs redirect-URI and claim
  mismatches, gets the agent's tool integrations consented with minimal scopes, and explains why the
  agent acts with the user's token rather than a shared admin key.

## Check yourself
**Q:** A single-page app logs users in with Google by obtaining an access token in the browser, calling
the userinfo endpoint, and posting the returned email to the backend, which trusts it. What is wrong,
and how would you redesign it?
**A:** Three independent failures. First, identity is inferred from an access token: a token minted for
any other app for the same user, or an attacker's own token, is accepted, because nothing ties it to this
client; the fix is to require an ID token and validate signature, `iss`, `aud` equal to your client id,
`exp` and `nonce`. Second, tokens live in JavaScript, so a single XSS steals them. Third, the backend
trusts a client-asserted email. Redesign as a backend-for-frontend: the browser hits `/login`, the server
builds the authorize URL with `state`, `nonce` and PKCE, handles the callback on the back channel,
validates the ID token, keys the user on `(iss, sub)`, and sets an HttpOnly session cookie. Access and refresh
tokens stay server-side and are used only for calls to Google on the user's behalf, with refresh-token
rotation enabled.

## Quiz
### Q: Why does the authorization code flow send a code through the browser and exchange it server-side instead of returning the access token directly?
- [ ] Codes are shorter than tokens and fit more reliably in a redirect URL
- [x] The browser is an untrusted channel; a single-use code bound to PKCE or a client secret is useless if intercepted, whereas a token would be directly usable
- [ ] Access tokens can only be issued over a connection authenticated with mTLS
- [ ] It lets the authorization server rate-limit how many tokens a client receives
> URLs leak through history, logs and referrers. A code that can only be redeemed with the verifier
> or secret the interceptor lacks limits the damage; the implicit grant that put tokens in the URL was
> deprecated for precisely this reason.

### Q: A mobile app calls its own backend API and also needs to know who the user is. Which token goes where?
- [ ] The ID token to the API, and the access token to establish identity
- [x] The access token to the API, and the ID token to establish identity; each has a different audience
- [ ] The refresh token to the API, and the ID token to establish identity
- [ ] Either token works for both purposes because both are signed JWTs
> The ID token's audience is the client, the access token's audience is the API. Sending the ID token
> to an API or using an access token as identity proof breaks the audience check that prevents token
> substitution between applications.

### Q: What does PKCE protect against that a client secret alone cannot?
- [ ] Brute-force guessing of the user's password at the authorization server
- [ ] Replay of an access token after it has expired
- [x] Interception of the authorization code by or on behalf of a public client that cannot hold a secret, and code injection generally
- [ ] Forging the signature of an ID token
> A public client ships its code to users, so any embedded secret is public. PKCE binds the code to a
> per-request verifier that never travels the front channel, so an intercepted code cannot be
> redeemed; the IETF best-practice now recommends it for all clients.
