---
id: rbac
module: backend-fundamentals
name: RBAC
kind: pattern
difficulty: core
est_minutes: 14
related:
  - backend-fundamentals/authentication-vs-authorization
  - backend-fundamentals/oauth-2-0-oidc
  - enterprise-integration/rbac-abac-mapping-to-customer-identity
  - enterprise-integration/scim-provisioning
  - agent-reliability/tool-permissions-and-least-privilege
  - safety-security/tool-privilege-escalation
  - system-design/tenant-isolation
sources:
  - Ferraiolo, Sandhu, Gavrila, Kuhn, Chandramouli, 2001 — Proposed NIST Standard for Role-Based Access Control
  - Pang et al., 2019 — Zanzibar: Google's Consistent, Global Authorization System
  - Official docs: Kubernetes RBAC
---

## TL;DR
Role-based access control puts an indirection between people and permissions: permissions attach to
roles, people get roles, and a request is allowed if one of the caller's roles, within the right scope,
carries the needed permission. It exists because per-user permission lists grow as users × permissions,
drift silently, and cannot answer "who can delete production data?". The price is that roles describe
job functions, not ownership or context, so RBAC needs a companion rule for "only the author may edit".

## Why it exists
Start with the arithmetic. An application with 2,000 users and 300 distinct permissions has up to
600,000 user-permission edges to maintain if each is granted directly, and every edge is a place to make
a mistake. Onboarding means guessing which 40 edges a new analyst needs; offboarding means finding all
of theirs; an auditor asking "who can export customer data?" gets a database scan nobody trusts.

The usual first attempt is boolean flags: `is_admin`, then `is_billing_admin`, then `can_export`,
sprinkled through handlers as `if user.is_admin or user.can_export`. Policy and code become inseparable:
nobody can list what an admin can do without grepping, adding a capability touches dozens of files, and
the flags inevitably disagree between services.

RBAC, standardised by NIST in 2001 after a decade of practice, replaces both with a vocabulary the
organisation already uses. Permissions are grouped into roles that mirror job functions ("editor",
"billing admin"); users receive a handful of roles. Edges become users × roles-per-user plus roles ×
permissions, both small. Policy lives in one table that can be reviewed, versioned and audited.
Onboarding is "assign the analyst role"; offboarding is "remove it"; the audit question is a lookup.

## How it works
### The core model
Sets: users *U*, roles *R*, permissions *P* = operations × object types. Two relations: user assignment
*UA ⊆ U × R* and permission assignment *PA ⊆ R × P*. The check is

```
allowed(u, op, obj)  ⇔  ∃ r ∈ roles(u) : (op, type(obj)) ∈ perms(r)
```

**Hierarchical RBAC** lets roles inherit (`admin ⊇ editor ⊇ viewer`) so each permission is defined once.
**Constrained RBAC** adds separation of duty: a user may not hold both "submit expense" and "approve
expense" (static), or may not activate both in one session (dynamic). **Sessions** let a user activate
a subset of their roles, which is least privilege in practice: the `sudo` of application security.

### Scope: what the textbook model leaves out
In a multi-tenant product "editor" is meaningless without "of what". The practical unit is a **role
binding**: `(subject, role, scope)`, where scope is an organisation, project or namespace. Kubernetes
models this literally: a `Role` lists verbs on resources, a `RoleBinding` ties a subject to a role within
a namespace, and `ClusterRole`/`ClusterRoleBinding` do the same cluster-wide. The check becomes
`roles(u, scope(obj)) ∪ roles(u, ancestors of scope(obj))`. Scope hierarchies are shallow (org → project)
*because* every extra level multiplies the lookups and the explanations.

### Where roles come from
In B2B products the customer's identity provider is the source of truth: a `groups` or `roles` claim in
the OIDC token or SAML assertion, or SCIM pushing group membership. Map provider groups to application
roles in one data-driven place (`okta:eng-leads → admin`), never in code, so a customer reorganising
their groups does not need a deploy.

### Decision point versus enforcement point
Keep the *decision* ("may u do op on obj?") in one function or service (your own module, Casbin, OPA,
Cedar) and *enforce* it at every boundary: the API route and the data layer, because hiding a button is
not security and a row returned by a query is already a leak. Deny by default: an endpoint with no
permission attached should fail, not pass.

### Roles in the token or looked up per request
Embedding roles in the access token costs no database calls but goes stale until expiry: a removed
admin keeps rights for the token's lifetime. Looking roles up per request is fresh and instantly
revocable at the price of one cached query. The usual compromise is short tokens with roles for ordinary
reads and a live check for destructive or administrative operations.

### Where RBAC stops: ABAC and ReBAC
"Only the document's author may edit it" is not a role; it is a relationship between a user and one
object. Encoding it as roles produces `editor-of-doc-123` and role explosion. Attribute-based access
control evaluates a policy over user, resource and environment attributes (`resource.owner == user.id`).
Relationship-based systems in the Zanzibar family (OpenFGA, SpiceDB) store tuples such as
`doc:123#editor@user:alice` and `folder:7#viewer@group:eng#member` and answer checks by graph walk. The
pattern that works in practice is RBAC for coarse job-function permissions plus ownership or
relationship checks for the fine-grained ones.

### Agents are subjects too
An agent calling tools is a subject with roles. Either give it its own narrowly scoped role or, better,
have it act on behalf of the user with the user's roles via a delegated token, so the tool layer runs
the same `allowed()` check a human request would. A runtime granted a super-role "because it is
internal" turns every prompt injection into privilege escalation.

## Example
A scoped RBAC check as a FastAPI dependency. Permissions are strings of the form `action:resource`,
roles are sets of permissions, and a user carries bindings per project plus an optional org-wide `*`
scope. `current_user` is assumed to decode the session and load bindings.

```python
from dataclasses import dataclass, field
from fastapi import Depends, FastAPI, HTTPException

PERMS = {                                     # role -> permissions; the only place policy lives
    "viewer": {"read:doc"},
    "editor": {"read:doc", "write:doc"},
    "admin":  {"read:doc", "write:doc", "delete:doc", "manage:members"},
}

@dataclass
class User:
    id: str
    bindings: dict = field(default_factory=dict)   # scope -> set(roles); "*" means org-wide

def allowed(user: User, action: str, project_id: str) -> bool:
    roles = user.bindings.get(project_id, set()) | user.bindings.get("*", set())
    return any(action in PERMS[r] for r in roles)  # deny unless some role in scope grants it

def require(action: str):
    def dep(project_id: str, user: User = Depends(current_user)):
        if not allowed(user, action, project_id):
            raise HTTPException(403, "forbidden")   # 403: identity known, permission absent
        return user
    return dep

app = FastAPI()

@app.delete("/projects/{project_id}/docs/{doc_id}")
def delete_doc(project_id: str, doc_id: str, user: User = Depends(require("delete:doc"))):
    return repo.delete(project_id=project_id, doc_id=doc_id)   # data layer also filters by project_id

alice = User("alice", {"p1": {"editor"}})
bob   = User("bob",   {"*":  {"admin"}})
assert allowed(alice, "write:doc", "p1") and not allowed(alice, "delete:doc", "p1")
assert not allowed(alice, "read:doc", "p2")      # scope matters: editor of p1 says nothing about p2
assert allowed(bob, "delete:doc", "p9")          # org-wide binding applies to every project
```

Adding a "billing admin" role is a one-line change to `PERMS`; adding an ownership rule ("authors may
delete their own doc") is a separate check against the row, not a new role.

## Trade-offs
- Edges drop from users × permissions to users × roles plus roles × permissions, but you must design
  roles that match real job functions; when every user needs a unique set, you are back to ACLs and a
  relationship-based model fits better.
- A central decision point is auditable and changeable without code, but becomes a dependency on every
  request; embed the policy library or cache decisions for a few seconds to keep latency flat.
- Role hierarchies remove duplication but make "what can admin actually do" harder to read; flatten
  the hierarchy once a reviewer cannot hold it in their head.
- Roles carried in the token are fast and stateless but stale until expiry; per-request lookup is fresh
  but adds a query. Choose by how quickly a revocation must take effect for the operation in question.

## Pitfalls
- Role explosion: creating `project-123-editor` style roles per resource; the symptom is thousands of
  roles nobody can review. Use scoped bindings or a relationship-based check instead.
- Enforcing only in the router or UI and not in data access; the symptom is IDOR, where changing an id
  in the URL returns another tenant's rows. Filter every query by scope.
- Hard-coding `if role == "admin"` in handlers; the symptom is that adding a role touches forty files.
  Check permissions, never role names.
- Caching roles in long-lived tokens; the symptom is a removed employee who still has access for an
  hour. Shorten expiry and re-check live for sensitive operations.
- Mapping customer identity groups to roles in code; the symptom is that onboarding each customer
  needs a deploy. Make the mapping a table the FDE can edit.
- Forgetting deny-by-default; the symptom is a new endpoint that is open because nobody added a check.
  Make the permission dependency mandatory, or lint for routes without one.
- Giving the agent runtime an admin role; the symptom is an agent that deleted something the requesting
  user could not have deleted themselves.

## When to use
RBAC is the default authorization model for B2B software; the real decisions are granularity (three to
seven roles per scope is usually enough), the scope hierarchy, whether roles ride in the token or are
looked up, and whether a separate authorization service is warranted yet. Reach for ABAC or ReBAC when
access depends on ownership, sharing, or nested resources such as folders and documents. Watch the
number of roles (growth is a smell), time-to-revoke, and whether "who can do X" is answerable with one
query. In interviews you will be asked to design the permission model for a multi-tenant app; the
expected answer includes scoped bindings, deny by default, enforcement in the data layer, and an honest
account of where RBAC ends and ownership checks begin.

## Role lens
- **Engineer:** Writes the `allowed()` function and the route dependency, adds scope filters to
  queries, and makes sure tool handlers in the agent call the same check as the HTTP layer.
- **Architect:** Owns the role catalogue, the scope hierarchy, the token-versus-lookup decision, and the
  boundary where RBAC hands off to relationship-based checks for shared or owned resources.
- **FDE:** Maps the customer's identity-provider groups onto product roles, proves tenant scoping during
  the security review, and configures what the deployed agent may touch in the customer's systems.

## Check yourself
**Q:** Design authorization for a multi-tenant AI document assistant with organisations, projects,
documents, the ability to share one document with a colleague, and an agent that summarises documents on
request. Where does RBAC fit and where does it not?
**A:** Use RBAC with scoped bindings for job-function access: roles such as owner, admin, editor and
viewer bound at the organisation or project level, sourced from the customer's identity provider through
a data-driven group mapping. Sharing a single document is a relationship, not a role, so store it as a
tuple (`doc:42#viewer@user:bob`) and check it after the role check fails. Enforce in one `allowed()`
function called from both the API routes and the data layer, which filters every query by organisation
id so a wrong document id can never cross tenants. The agent acts on behalf of the requesting user with
a delegated token, so its tool calls run through the same check and it can only summarise what that user
could read. Deny by default, keep tokens short, re-check live for deletes, and log every decision for
audit.

## Quiz
### Q: Why does RBAC scale better than granting permissions directly to users?
- [ ] Roles are evaluated in constant time while direct grants require a database join
- [x] The number of relationships drops from users × permissions to users × roles plus roles × permissions, so policy is small enough to review and change in one place
- [ ] Roles are stored in the identity provider, which is faster than the application database
- [ ] Direct grants cannot express hierarchical permissions
> The win is combinatorial and organisational: a few roles each carrying a few dozen permissions
> replace hundreds of thousands of individual grants, and onboarding, offboarding and audit become
> single-row operations.

### Q: Which requirement signals that RBAC alone is not enough?
- [ ] Admins can delete any document in their organisation
- [ ] Viewers can read every document in projects they belong to
- [x] A document's author can edit it even without the editor role in that project
- [ ] Billing administrators can manage invoices but not documents
> Author-edits-own-document depends on a relationship between one user and one object. Expressing it
> with roles means a role per document; an ownership or relationship check alongside RBAC is the usual
> answer.

### Q: Where must the permission check be enforced to prevent a user from reading another tenant's data by changing an id in the URL?
- [ ] In the frontend, by hiding links to resources the user cannot access
- [ ] In the identity provider, by issuing tokens only to valid users
- [x] In the API and data layer, by checking the permission within the resource's scope and filtering queries by tenant
- [ ] In the load balancer, by routing each tenant to a separate pool
> IDOR happens when a valid, authenticated user supplies an id outside their scope. Only a server-side
> check against the resource's scope, backed by tenant-filtered queries, closes it; UI hiding and token
> issuance do not look at the id at all.
