---
id: scim-provisioning
module: enterprise-integration
name: SCIM provisioning
kind: concept
difficulty: core
est_minutes: 15
related:
  - enterprise-integration/sso-iam-integration
  - enterprise-integration/saml-sso
  - enterprise-integration/rbac-abac-mapping-to-customer-identity
  - enterprise-integration/retry-deduplication-reconciliation
  - rag-retrieval/acl-aware-retrieval
  - backend-fundamentals/rest-graphql-api-design
  - data-architecture/data-retention-and-deletion-architecture
sources:
  - Hunt et al., 2015 — RFC 7643: SCIM Core Schema
  - Hunt et al., 2015 — RFC 7644: SCIM Protocol
  - Official docs: Okta and Microsoft Entra SCIM provisioning guides
---

## TL;DR
SCIM 2.0 is a standard REST API your application exposes so the customer's identity provider can create,
update, deactivate and group users in your product automatically. SSO tells you who is logging in; SCIM
tells you who *exists*, what groups they are in, and, most importantly, when they leave. Without it,
terminated employees and stale group memberships linger in your app and in every permission filter built on them.

## Why it exists
Just-in-time provisioning, creating an account the first time someone signs in through SSO, looks
sufficient until you ask what happens next. A user who leaves the company never signs in again, so your app
never learns they are gone: their account, API tokens, scheduled agent jobs and shared documents remain. A
user who moves from Finance to Sales keeps Finance group membership in your app until their next login,
and if their sessions are long-lived, longer. You also cannot assign work or share a document with someone
who has not logged in yet, because they do not exist in your system.

For an AI product this is sharper. Retrieval filters, agent permissions and audit trails are keyed by users
and groups. If membership lags the customer's directory, the assistant keeps answering from documents a
person should no longer see. The naive fixes, a nightly CSV export from the customer or polling their
directory API, are bespoke per customer and per IdP, need credentials into their directory, and still lag.
SCIM inverts the direction: the IdP pushes changes to a standard endpoint you build once.

## How it works
### Resources and endpoints
RFC 7643 defines the schema, RFC 7644 the protocol. You expose, under a per-tenant base URL:
- `/Users` and `/Groups`: the core resources.
- `/ServiceProviderConfig`, `/Schemas`, `/ResourceTypes`: discovery, telling the IdP which features you
  support (PATCH, filtering, bulk, ETags).

A User carries `id` (yours, server-assigned), `externalId` (the IdP's identifier), `userName`, `name`,
`emails`, `active`, and optional enterprise-extension attributes such as `department` and `manager`.
A Group carries `displayName` and `members`.

### The operations
- `POST /Users` creates. Return 201 with your `id`; return 409 if `userName` already exists, *because* the
  IdP will then search and link rather than create a duplicate.
- `GET /Users?filter=userName eq "ana@acme.com"` lets the IdP look up a user before creating or matching.
  You must support at least the `eq` filter on `userName` and `externalId` that IdPs use.
- `PATCH /Users/{id}` with a `PatchOp` body of `add`/`replace`/`remove` operations updates attributes.
  Deprovisioning commonly arrives as `replace active=false`, not as `DELETE`, *because* customers want the
  account recoverable and its history retained.
- `DELETE /Users/{id}` hard-removes; some IdPs send it, often later than deactivation, if at all.
- `PATCH /Groups/{id}` adds or removes `members`; this is how group-based permissions stay current.

### Authentication and tenancy
The IdP authenticates with a bearer token (or OAuth client credentials) that you issue per tenant. The
token, not the request body, determines the tenant, *because* a SCIM endpoint that trusts a tenant field in
the payload lets one customer's IdP write into another's directory.

### What deactivation must do
Setting `active=false` should immediately revoke sessions and API tokens, pause the user's scheduled jobs
and agents, and invalidate any permission cache that includes them. Deleting their data is a separate
retention decision.

### Robustness
IdPs retry and may resend the same change; operations must be idempotent. Implementations also differ:
some send `op` values in different letter case, booleans as strings, or a `replace` with no `path` and an
object value. Parse leniently, log the raw request, and test against each IdP you support.

## Example
A FastAPI handler for `PATCH /scim/v2/Users/{id}` that handles the common deactivation shapes and does the
security-relevant work, not just a column update.

```python
from fastapi import FastAPI, Header, HTTPException

app = FastAPI()

def as_bool(v):  # some IdPs send "False" as a string
    return v if isinstance(v, bool) else str(v).lower() == "true"

@app.patch("/scim/v2/Users/{user_id}")
async def patch_user(user_id: str, body: dict, authorization: str = Header(...)):
    tenant = await tenant_for_scim_token(authorization.removeprefix("Bearer "))  # tenant from token
    user = await db.get_user(tenant.id, user_id)
    if user is None:
        raise HTTPException(404, detail="User not found")
    if "urn:ietf:params:scim:api:messages:2.0:PatchOp" not in body.get("schemas", []):
        raise HTTPException(400, detail="Expected PatchOp")
    changes = {}
    for op in body.get("Operations", []):
        kind, path, value = op["op"].lower(), op.get("path"), op.get("value")
        if kind == "replace" and path is None and isinstance(value, dict):
            changes.update(value)                      # {"op":"replace","value":{"active":false}}
        elif kind in ("replace", "add") and path:
            changes[path] = value                      # {"op":"replace","path":"active","value":false}
    if "active" in changes:
        changes["active"] = as_bool(changes["active"])
        if user.active and not changes["active"]:      # idempotent: only act on the transition
            await sessions.revoke_all(tenant.id, user_id)
            await api_tokens.revoke_all(tenant.id, user_id)
            await agent_jobs.pause_for_user(tenant.id, user_id)
            await acl_cache.invalidate_principal(tenant.id, user_id)
    user = await db.update_user(tenant.id, user_id, changes)
    await audit.log(tenant.id, "scim.patch_user", user_id, raw=body)
    return to_scim_user(user)                          # full resource, 200
```

A real implementation also handles filtered paths such as `emails[type eq "work"].value` and group
membership patches; the point here is that deactivation is a security event with side effects.

## Trade-offs
- SCIM gives near-real-time lifecycle sync with a standard you build once, but every IdP has quirks, so
  "SCIM compliant" still means per-IdP testing; JIT-only is acceptable for small customers without
  compliance requirements.
- Soft deactivation preserves history and allows reactivation, but you must ensure inactive users are
  excluded everywhere; hard deletion is simpler to reason about but conflicts with audit retention.
- Pushing group memberships enables group-based permissions in retrieval, but large groups generate many
  PATCH operations; syncing only groups assigned to your app keeps volume and blast radius small.
- SCIM-managed attributes stay authoritative, but users can no longer edit them in your UI; the trade-off
  flips if customers expect self-service profile edits.

## Pitfalls
- Treating `active=false` as a profile field update; the symptom is a deactivated user's API token still
  calling your API the next day.
- Determining the tenant from the payload or URL alone; the symptom is a misconfigured IdP creating users in
  the wrong customer's directory.
- Returning 201 for duplicate `userName` instead of 409; the symptom is duplicate accounts after the IdP
  retries a create.
- Strict parsing of PATCH bodies; the symptom is provisioning working with one IdP and failing with another
  with 400 errors in the IdP's provisioning log.
- Forgetting to propagate group removals to retrieval ACL caches; the symptom is a user who changed teams
  still getting answers from the old team's documents.

## When to use
Implement SCIM once you sell to organizations with an IAM team, compliance requirements, or more than a few
dozen users; many enterprise security reviews ask about it explicitly. Pair it with SSO, and make
deactivation revoke everything. Measure time from IdP change to effect in your app, provisioning error rate
per tenant and IdP, and the count of active users who have not been seen in the IdP feed. In interviews,
explaining why SSO alone does not deprovision, and what deactivation must trigger, is the core signal.

## Role lens
- **Engineer:** Builds the `/Users` and `/Groups` endpoints, lenient PATCH parsing, idempotent operations and
  the revocation side effects of deactivation.
- **Architect:** Decides the user lifecycle model (soft versus hard delete, retention), how SCIM identities
  join SSO identities, and how membership changes propagate to permission caches.
- **FDE:** Configures the customer's Okta or Entra provisioning app, scopes which groups are pushed, and
  proves with a test user that removal from the IdP revokes access within minutes.

## Check yourself
**Q:** A customer's security team asks: "If we terminate someone at 9:00, when do they lose access to your AI
assistant and the documents it indexed?" How do you answer, given SSO and SCIM are both in place?
**A:** At 9:00 the IdP blocks new sign-ins and sends a SCIM `active=false` on its next provisioning cycle, which
can be immediate or periodic depending on IdP and configuration. On receipt we revoke all sessions and API
tokens, pause their scheduled agents and invalidate permission caches, so access ends within that delay plus
seconds. Retrieval filters are evaluated per request against current memberships, so no indexed content is
reachable afterwards. Their data is retained or deleted per the contract's retention policy. We would verify
this with a test account and show the audit log.

## Quiz
### Q: Why do IdPs usually deprovision with PATCH active=false rather than DELETE?
- [ ] DELETE is not part of the SCIM protocol
- [x] Customers want the account recoverable and its history retained, so deactivation is the default
- [ ] PATCH is faster to process than DELETE
- [ ] DELETE would remove the user from the IdP as well
> SCIM defines DELETE, but deactivation keeps ownership, audit history and the option to reactivate; the app
> must still treat it as a full revocation event, not a cosmetic flag.

### Q: How should a SCIM endpoint determine which tenant a request belongs to?
- [ ] From a tenant attribute in the JSON body
- [ ] From the user's email domain
- [x] From the per-tenant bearer token or client credentials used to authenticate the request
- [ ] From the IdP's IP address
> The credential is the trust anchor issued to one customer's IdP; payload fields and email domains can be
> wrong or spoofed, and IdP egress IPs are shared across many customers.

### Q: What does SCIM provide that SSO with just-in-time provisioning does not?
- [ ] Multi-factor authentication
- [ ] Signed login assertions
- [x] Notification of leavers and group changes even when the user never signs in again
- [ ] Encryption of user passwords
> JIT only learns about users at login, so departures and moves are invisible; SCIM pushes lifecycle and
> membership changes from the directory as they happen.
