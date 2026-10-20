---
id: cors
module: fastapi-for-ai
name: CORS
kind: concept
difficulty: intro
est_minutes: 12
related:
  - fastapi-for-ai/middleware-ordering
  - fastapi-for-ai/api-keys-oauth2-jwt
  - fastapi-for-ai/server-sent-events
  - fastapi-for-ai/httpexception-handling
  - backend-fundamentals/authentication-vs-authorization
  - security-governance-compliance/zero-trust
sources:
  - Official docs: MDN Web Docs (Cross-Origin Resource Sharing)
  - WHATWG — Fetch Standard
  - Official docs: Starlette (CORSMiddleware)
---

## TL;DR
Browsers block a page from reading responses from another origin unless that server opts in. CORS is
the set of response headers that opt in. The browser enforces it, not your server, so CORS controls
which websites' JavaScript may call your API with a user's browser. It is not authentication. curl,
scripts and other servers ignore it completely, so your API still needs real auth.

## Why it exists
The same-origin policy is the browser's basic defence: a script loaded from `evil.example` must not
be able to read your bank's responses using the cookies your browser holds for the bank. An origin is
the triple (scheme, host, port), so `https://app.acme.com` and `https://api.acme.com` are *different*
origins, and so are `http://localhost:3000` and `http://localhost:8000`. That is exactly the setup of
a typical AI product: a React or Next.js chat UI on one origin calling a FastAPI backend on another.
Without a relaxation mechanism, that legitimate front end can't read its own API's responses.

The naive fixes fail in opposite directions. Turning on "allow everything" (`*` for origins plus
credentials) would let any website make credentialed calls as your logged-in users, which is what
the same-origin policy exists to prevent. Browsers refuse that combination, so people then reach for
"reflect whatever Origin arrives", which recreates the hole on purpose. Proxying everything through
the front-end server avoids CORS but adds a hop and makes the front end a gateway. CORS is the
middle path: the server names exactly which origins may read its responses, with which methods,
headers and credentials, and the browser enforces that list.

The key insight is who CORS protects. It protects *users' browsers* from malicious sites. It does
not protect your API from attackers, who can call it with any HTTP client and simply ignore CORS
headers.

## How it works
### Simple requests versus preflight
For a "simple" request (GET, HEAD or POST with only CORS-safelisted headers and a content type of
`text/plain`, `multipart/form-data` or `application/x-www-form-urlencoded`), the browser sends it
directly with an `Origin` header. Then, if the response lacks a matching
`Access-Control-Allow-Origin`, it hides the response from the script. The request still reached your
server and still ran. CORS blocks reading, not sending, which is why CSRF protection is a separate
concern.

Almost every AI API call is *not* simple. `Content-Type: application/json` and an `Authorization`
header each trigger a **preflight**: before the real request, the browser sends
`OPTIONS /v1/chat` with `Origin`, `Access-Control-Request-Method: POST` and
`Access-Control-Request-Headers: authorization, content-type`. The server must answer with:
- `Access-Control-Allow-Origin`: the exact origin (or `*` when no credentials are used);
- `Access-Control-Allow-Methods` and `Access-Control-Allow-Headers` covering what was asked;
- optionally `Access-Control-Max-Age`, so the browser caches the preflight result instead of
  paying an extra round trip on every call (browsers cap how long they honour it);
- `Access-Control-Allow-Credentials: true` if cookies or HTTP auth are involved.

The preflight exists *because* non-simple requests were impossible from other origins before CORS,
and old servers may rely on that. Asking permission first keeps them safe.

### Credentials
With `fetch(url, {credentials: "include"})` or an `EventSource` created with `withCredentials`, the
browser sends cookies. It then requires an exact `Access-Control-Allow-Origin` (no `*`) plus
`Allow-Credentials: true`, and it should be paired with `Vary: Origin` so caches don't serve one
origin's headers to another. Bearer tokens in an `Authorization` header aren't "credentials" in this
sense, but the header still triggers preflight and must appear in `Allow-Headers`.

### Reading response headers
Scripts can read only safelisted response headers unless you list others in
`Access-Control-Expose-Headers`. If your UI needs `X-Request-ID`, `Retry-After` or a token-usage
header, you must expose it.

### In FastAPI
`CORSMiddleware` (from Starlette) answers preflights itself and adds headers to normal responses.
Its defaults are restrictive: no origins, only GET, no extra headers, no credentials. You have to
opt in to each one. Because middleware wraps the app in order, it must sit outside anything that might
short-circuit a request (auth middleware returning 401, rate limiters returning 429). Otherwise
those responses lack CORS headers and the browser reports a CORS error instead of the real status.

## Example
A chat UI at `https://chat.acme.com` (and a local dev server) calling a FastAPI backend with a bearer
token, reading a request ID and rate-limit hint from responses.

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI()

origins = ["https://chat.acme.com"]
if os.getenv("ENV") == "dev":
    origins += ["http://localhost:3000"]

app.add_middleware(                      # add last => outermost, wraps auth and rate limiting
    CORSMiddleware,
    allow_origins=origins,               # exact list; never "*" together with credentials
    allow_credentials=False,             # bearer tokens, not cookies
    allow_methods=["GET", "POST"],
    allow_headers=["authorization", "content-type", "idempotency-key"],
    expose_headers=["x-request-id", "retry-after"],
    max_age=600,                         # cache preflight results for 10 minutes
)

@app.post("/v1/chat")
async def chat(body: dict):
    return {"reply": "..."}
```

What the browser does: `fetch("https://api.acme.com/v1/chat", {method: "POST", headers:
{Authorization: "Bearer ...", "Content-Type": "application/json"}})` first sends `OPTIONS` from origin
`https://chat.acme.com`. The middleware replies 200 with the allowed methods and headers, and the
browser then sends the POST. A page at `https://evil.example` gets a preflight with no
`Allow-Origin`, so its script never sends the POST. `curl` from anywhere still reaches `/v1/chat`,
which is why the bearer token must be validated.

## Trade-offs
- An explicit origin allowlist is the safe default, but it needs a deploy for each new front end.
  `allow_origin_regex` (for example, for preview deployments) is more flexible, but one loose regex
  can match an attacker's domain.
- Cookie-based auth with credentials gives simpler UX and HttpOnly storage, but it needs exact
  origins, `Vary: Origin` and separate CSRF defences. Bearer tokens avoid CSRF but must be stored by
  front-end code.
- A long `max_age` cuts preflight round trips (which hurt chat latency on high-RTT mobile links), but
  policy changes take longer to reach browsers that cached the old answer.
- Serving the UI and API from one origin (a path-based reverse proxy) removes CORS entirely, at the
  cost of coupling their deploy and routing infrastructure.

## Pitfalls
- Treating CORS as access control. The symptom is an "internal" API with only a CORS allowlist that
  scripts and partners call freely, because only browsers honour CORS.
- Putting CORS middleware inside auth or rate-limit middleware, or relying on it for unhandled
  exceptions (Starlette's server-error handler sits outside user middleware). The symptom is the
  browser console showing "CORS error" when the real response was a 401, 429 or 500.
- Forgetting `Access-Control-Expose-Headers`. The symptom is front-end code reading `null` for
  `Retry-After` or a request ID that is visible in the network tab.
- Reflecting any `Origin` with credentials enabled. The symptom only shows up in a security review or
  pentest: any website can read logged-in users' conversations.
- Expecting a trailing slash or a different port to match. The symptom is that the allowlist works
  in staging and fails in prod because `https://chat.acme.com/` was listed with a slash.

## When to use
Configure CORS whenever browser JavaScript on one origin calls your API on another: SPA front ends,
embeddable chat widgets, browser extensions, notebooks hosted elsewhere. Skip it for server-to-server
and mobile-native clients, which don't enforce it, and for same-origin deployments. Default to an
explicit allowlist from configuration per environment, the minimum methods and headers, and bearer
tokens unless you have a reason for cookies. For a widget embedded on many customer sites, consider
per-tenant origin lists stored with the tenant. When debugging, read the preflight in the browser's
network tab first. Most "CORS bugs" are a missing header in `Allow-Headers` or middleware ordering.
Interviewers expect you to say plainly that CORS is browser-enforced and not a security boundary.

## Role lens
- **Engineer:** Adds `CORSMiddleware` with per-environment origins, lists every custom request and
  response header, and keeps the middleware outermost so error responses stay readable.
- **Architect:** Decides same-origin versus cross-origin topology, cookie versus token auth, and how
  embeddable widgets get tenant-specific origin allowlists without a code deploy.
- **FDE:** Debugs "the widget works on our demo but not on the customer intranet" by checking which
  origin the customer's portal actually sends and adding it to their tenant config.

## Check yourself
**Q:** A teammate says, "We set `allow_origins` to only our domain, so nobody else can use the API." Is
that right? What does the allowlist actually achieve, and what else is needed?
**A:** No. CORS is enforced by browsers to protect users. It stops a *script on another website*
from reading your API's responses in a victim's browser. Any non-browser client (curl, Python, a
partner server, an attacker's script) ignores the headers and calls the API directly. So the
allowlist protects against cross-site abuse of logged-in users, and real access control still needs
authentication on every request (API keys or validated tokens), authorization checks, rate limits, and
CSRF defences if cookies are used. Even for browsers, a simple POST is sent anyway; CORS only hides the
response.

## Quiz
### Q: Why does a `fetch` POST with `Content-Type: application/json` trigger an OPTIONS request first?
- [ ] JSON bodies must be validated by the server before sending
- [x] That content type is not CORS-safelisted, so the browser preflights to ask whether the server allows it
- [ ] All POST requests are always preflighted
- [ ] FastAPI requires OPTIONS before any POST
> Only a few content types count as "simple". application/json does not, so the browser checks
> permission with a preflight first. A form-encoded POST from another origin would be sent directly.

### Q: You set `allow_origins=["*"]` and `allow_credentials=True` and the front end uses cookies. What happens?
- [ ] It works: any origin can call with cookies
- [ ] The server rejects all requests with 403
- [x] Browsers refuse a wildcard origin on credentialed requests, so the call fails unless the server echoes the exact origin
- [ ] Cookies are sent but the response is cached without Vary
> The Fetch standard forbids "*" for credentialed responses because it would let any site act as the
> user. You need an exact origin per request, which is why an explicit allowlist with Vary: Origin is used.

### Q: The browser console shows a CORS error, but server logs show the request returned 429. Most likely cause?
- [ ] The client's origin is missing from the allowlist
- [x] The rate limiter short-circuits before CORS middleware runs, so the 429 has no CORS headers
- [ ] Browsers always report 429 as a CORS error
- [ ] max_age was set too high
> If middleware that returns early sits outside CORS, its responses lack Access-Control-Allow-Origin,
> and the browser hides them. Making CORS outermost lets the front end see the real 429 and its Retry-After.
