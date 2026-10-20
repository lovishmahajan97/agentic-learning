---
id: scim-provisioning
module: enterprise-integration
name: SCIM provisioning
kind: concept
difficulty: core
est_minutes: 15
status: crisp
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
SCIM 2.0 is a standard REST API you expose so the customer's IdP can create, update, deactivate and
group your users automatically. SSO says who logs in; SCIM says who *exists* and when they leave, so
leavers and stale memberships stop lingering.

## Why it exists
- **JIT provisioning never learns who left.** Creating an account at first SSO sign-in looks sufficient,
  but a leaver never signs in again. Their account, API tokens, scheduled agent jobs and shared documents
  remain.
- **Moves lag too.** A user who moves from Finance to Sales keeps Finance group membership in your app until
  their next login, and if sessions are long-lived, longer.
- **You cannot address people who have not logged in.** You cannot assign work or share a document with
  them, because they do not exist in your system yet.
- **AI products feel it sharper.** Retrieval filters, agent permissions and audit trails are keyed by users
  and groups; if membership lags the directory, the assistant keeps answering from documents a person
  should no longer see.
- **Naive fixes are bespoke and still lag.** A nightly CSV export or polling the customer's directory API is
  per customer and per IdP and needs credentials into their directory. SCIM inverts the direction: the IdP
  pushes changes to a standard endpoint you build once.

## How it works
### Resources and endpoints
RFC 7643 defines the schema, RFC 7644 the protocol. Under a per-tenant base URL you expose:
- **`/Users` and `/Groups`:** the core resources.
- **`/ServiceProviderConfig`, `/Schemas`, `/ResourceTypes`:** discovery, telling the IdP which features you
  support (PATCH, filtering, bulk, ETags).

| Resource | Key attributes |
|---|---|
| User | `id` (yours, server-assigned), `externalId` (the IdP's identifier), `userName`, `name`, `emails`, `active`, optional enterprise-extension attributes such as `department` and `manager` |
| Group | `displayName` and `members` |

### The operations
| Request | Behaviour and why |
|---|---|
| `POST /Users` | Return 201 with your `id`; return 409 (`scimType: uniqueness`) if `userName` exists, *because* a retried create must not produce a second account. IdPs normally query by filter first and link to a match |
| `GET /Users?filter=userName eq "ana@acme.com"` | Lookup before create or match; support at least the `eq` filter on `userName` and `externalId` |
| `PATCH /Users/{id}` | A `PatchOp` body of `add`/`replace`/`remove` operations. Deprovisioning commonly arrives as `replace active=false`, *because* customers want the account recoverable and its history retained |
| `DELETE /Users/{id}` | Hard removal; some IdPs send it, often later than deactivation, if at all |
| `PATCH /Groups/{id}` | Adds or removes `members`; this is how group-based permissions stay current |

Group membership shapes:
- **Add:** `{"op":"add","path":"members","value":[{"value":"<user id>"}]}`
- **Remove:** `{"op":"remove","path":"members[value eq \"<user id>\"]"}`
- **Prefer these incremental operations to a `replace` of the whole list**, *because* a 5,000-member group
  replaced on every change is a 5,000-element write each time, and a replace racing another change can drop
  members.

Paging:
- **List and filter responses are a `ListResponse`** with `totalResults`, `startIndex` (1-based) and
  `itemsPerPage`.
- **Pagination must work before the first big customer turns provisioning on**, because IdPs page through
  `/Users` and `/Groups` during an initial sync.

### Authentication and tenancy
- **Per-tenant credential.** The IdP authenticates with a bearer token (or OAuth client credentials) that
  you issue per tenant.
- **The token, not the body, determines the tenant**, *because* an endpoint that trusts a tenant field in
  the payload lets one customer's IdP write into another's directory.

### Joining SCIM identities to SSO identities
SCIM and SSO are two channels describing the same person, and they must land on one account:
1. **Agree the join key with the customer's admin** when configuring provisioning.
2. **Ideally use the same immutable identifier the SSO token carries** (for example the Entra object ID or
   the Okta user ID), sent as `externalId` or a mapped attribute, so a SCIM-created user and the first SSO
   login resolve to the same row.
3. **Avoid email or `userName` joins.** They work until an address changes; then SCIM updates one account
   while login creates another, *because* the channels no longer agree on who the person is.
4. **If JIT stays enabled alongside SCIM**, it must look up by the same key before creating.

### What deactivation must do
`active=false` is a security event, not a profile edit. Immediately:
- revoke sessions and API tokens;
- pause the user's scheduled jobs and agents;
- invalidate any permission cache that includes them.

Deleting their data is a separate retention decision.

### Robustness
- **Idempotent operations**, because IdPs retry and may resend the same change.
- **Lenient parsing**, because implementations differ: some send `op` values in different letter case,
  booleans as strings, or a `replace` with no `path` and an object value.
- **Log the raw request** and test against each IdP you support.

## Diagram
Every request is scoped by its credential first; deactivation fans out to every place access lives.

```mermaid
flowchart TD
    I["Customer IdP"] -->|"SCIM request"| E["SCIM endpoint"]
    E --> T["Tenant from bearer token"]
    T --> O{"Operation"}
    O -->|"POST Users"| C["Create or 409 on duplicate"]
    O -->|"PATCH Groups"| G["Apply member add or remove"]
    O -->|"PATCH active false"| D["Deactivate"]
    G --> AC["Invalidate ACL cache for changed users"]
    D --> R["Revoke sessions and API tokens"]
    D --> J["Pause agents and jobs"]
    D --> AC
    E -.->|"raw request"| L[("Audit log")]
```

## Examples
### Deactivation-aware `PATCH /scim/v2/Users/{id}` in FastAPI
Handles the common deactivation shapes and does the security-relevant work, not just a column update.

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

- **Still to handle in a real implementation:** filtered paths such as `emails[type eq "work"].value` and
  group membership patches. The point here is that deactivation is a security event with side effects.

### Idempotent group-membership patches with an ACL delta
Pure Python, runnable. It returns who was added and removed, so only those principals' permission caches
are invalidated, and a replayed patch changes nothing.

```python
import re

REMOVE_FILTER = re.compile(r'^members\[value eq "([^"]+)"\]$')

def apply_group_patch(members: set, body: dict) -> tuple:
    """Apply a SCIM Group PatchOp; return (new_members, added, removed) for ACL invalidation."""
    out = set(members)
    for op in body.get("Operations", []):
        kind, path, value = op["op"].lower(), op.get("path"), op.get("value") or []
        if kind == "add" and path == "members":
            out |= {m["value"] for m in value}               # re-adding is a no-op: idempotent
        elif kind == "remove" and path == "members":
            out -= {m["value"] for m in value}               # some IdPs list removals in value
        elif kind == "remove" and path and (m := REMOVE_FILTER.match(path)):
            out.discard(m.group(1))                          # removing a non-member is a no-op
        elif kind == "replace" and path == "members":
            out = {m["value"] for m in value}                # legal, but a full rewrite each time
        else:
            raise ValueError(f"unsupported group operation: {op}")
    return out, out - members, members - out

body = {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "Add", "path": "members", "value": [{"value": "u3"}]},
                       {"op": "remove", "path": 'members[value eq "u1"]'}]}
new, added, removed = apply_group_patch({"u1", "u2"}, body)
print(sorted(new), added, removed)       # ['u2', 'u3'] {'u3'} {'u1'}
print(apply_group_patch(new, body)[1:])  # (set(), set()): replay changes nothing
```

### Worked scenario: one change to a 5,000-member group
| Approach | Elements written | ACL cache entries invalidated | Race risk |
|---|---|---|---|
| Incremental `add` of 1 member | 1 | 1 user | None: concurrent adds commute |
| `replace` of the full member list | 5,000 | all 5,000 users, unless you diff | A replace built from a stale list drops the other change's member |
| Daily: 20 changes as replaces | 100,000 | up to 100,000 | Grows with change rate |

## Production patterns
- **Canary leaver test.** Keep a test user in each customer's IdP, deactivate it on a schedule, and measure
  time until its session and API token stop working. Monitor that time against the bound you promised the
  security team.
- **Hashed, per-tenant SCIM tokens with rotation.** Store only a hash of each tenant's bearer token, scope it
  to the SCIM endpoints, and support two valid tokens during rotation. Monitor auth failures per tenant,
  which usually mean the customer rotated on their side.
- **Raw-request logging plus per-IdP contract tests.** Log each SCIM body (with personal data handled per
  policy) and replay captured Okta and Entra payloads in CI, including string booleans and path-less
  replaces. Monitor 4xx rate per IdP from your logs and the IdP's provisioning log.
- **Membership deltas drive ACL invalidation.** Compute added and removed members per patch and invalidate
  only those principals in retrieval caches. Monitor lag from a SCIM group change to the retrieval filter
  reflecting it.
- **Orphan and drift report.** Weekly, list accounts with no SCIM link (created only by JIT) and active users
  not seen in the IdP feed. Monitor both counts per tenant; a rising number means the join key or the push
  scope is wrong.

## Trade-offs
- **Near-real-time sync vs per-IdP quirks.** SCIM gives lifecycle sync with a standard you build once, but
  every IdP has quirks, so "SCIM compliant" still means per-IdP testing. JIT-only is acceptable for small
  customers without compliance requirements.
- **Soft deactivation vs hard deletion.** Soft deactivation preserves history and allows reactivation, but
  inactive users must be excluded everywhere. Hard deletion is simpler to reason about but conflicts with
  audit retention.
- **Pushed groups vs volume.** Pushing memberships enables group-based permissions in retrieval, but large
  groups generate many PATCH operations. Syncing only groups assigned to your app keeps volume and blast
  radius small.
- **Authoritative attributes vs self-service.** SCIM-managed attributes stay authoritative, but users can no
  longer edit them in your UI. The trade-off flips if customers expect self-service profile edits.

## Pitfalls
- **Treating `active=false` as a profile field update.** Symptom: a deactivated user's API token is still
  calling your API the next day.
- **Tenant from the payload or URL alone.** Symptom: a misconfigured IdP creates users in the wrong
  customer's directory.
- **Returning 201 for a duplicate `userName` instead of 409.** Symptom: duplicate accounts after the IdP
  retries a create.
- **Strict PATCH parsing.** Symptom: provisioning works with one IdP and fails with another, with 400 errors
  in the IdP's provisioning log.
- **Joining SCIM users to SSO logins by email.** Symptom: duplicate accounts for one person after an email
  change, with permissions on one and history on the other.
- **Not propagating group removals to retrieval ACL caches.** Symptom: a user who changed teams still gets
  answers from the old team's documents.

## When to use
- **Use when** you sell to organizations with an IAM team, compliance requirements, or more than a few dozen
  users; many enterprise security reviews ask about it explicitly.
- **Pair it with SSO**, and make deactivation revoke everything.
- **Measure:** time from IdP change to effect in your app (test end to end with a canary user), provisioning
  error rate per tenant and IdP from your logs and the IdP's provisioning log, accounts with no SCIM link
  (created only by JIT), and active users not seen in the IdP feed.
- **Interviews:** explain why SSO alone does not deprovision, and what deactivation must trigger; that is the
  core signal.

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
can be near-immediate or periodic depending on IdP and configuration (Okta pushes on the change event; Entra's
provisioning service documents incremental cycles on the order of tens of minutes, so state the bound for
the customer's IdP). On receipt we revoke all sessions and API
tokens, pause their scheduled agents and invalidate permission caches, so access ends within that delay plus
seconds. Retrieval filters are evaluated per request against current memberships, so no indexed content is
reachable afterwards. Their data is retained or deleted per the contract's retention policy. We would verify
this with a test account and show the audit log.

## Interview questions
### Why is SSO with just-in-time provisioning not enough for enterprise customers?
- **JIT only learns about users at login**, so a leaver who never signs in again keeps their account, API
  tokens and scheduled agents.
- **Moves lag too:** old group memberships persist until the next login, so retrieval filters stay wrong.
- **SCIM fixes both** by having the IdP push lifecycle and membership changes as they happen.

### Provisioning works with Okta but Entra shows 400 errors on every update. How do you debug it?
- **Pull the raw request from your log** and the matching entry in Entra's provisioning log.
- **Usual causes:** `op` in different letter case, `"False"` as a string, or a `replace` with no `path` and
  an object value.
- **Fix:** parse leniently, add the captured payload to a per-IdP contract test, and re-run provisioning.

### Should a deprovisioned user be soft-deactivated or hard-deleted?
- **Soft by default:** IdPs usually send `active=false`, customers want accounts recoverable, and audit
  history must survive.
- **Cost:** every query, filter and job must exclude inactive users.
- **Hard delete** belongs to the retention policy, run as a separate, deliberate process.

### How do you design the join between SCIM-created users and SSO logins?
- **Agree one immutable key** with the customer admin, such as the Entra object ID or Okta user ID, sent as
  `externalId` or a mapped attribute.
- **Make JIT look up by that key** before creating, so the two channels land on one row.
- **Watch the orphan report:** accounts with no SCIM link reveal a broken join early.

### A 5,000-member group changes 20 times a day. What does that cost and how do you keep it cheap?
- **With full replaces** it is 100,000 member writes a day and, without diffing, cache invalidation for
  everyone each time.
- **With incremental add and remove** it is about 20 writes, and only the changed principals are invalidated.
- **Also** sync only groups assigned to your app, which cuts volume and blast radius.

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
