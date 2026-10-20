---
id: rbac
module: backend-fundamentals
name: RBAC
kind: concept
difficulty: core
est_minutes: 14
related:
  - backend-fundamentals/authentication-vs-authorization
  - backend-fundamentals/oauth-2-0-oidc
  - backend-fundamentals/kubernetes
  - enterprise-integration/rbac-abac-mapping-to-customer-identity
  - enterprise-integration/scim-provisioning
  - agent-reliability/tool-permissions-and-least-privilege
  - safety-security/tool-privilege-escalation
  - agent-runtime-architecture/approval-and-authorization-boundaries
sources:
  - Sandhu, Coyne, Feinstein, Youman, 1996 — Role-Based Access Control Models
  - Ferraiolo, Sandhu, Gavrila, Kuhn, Chandramouli, 2001 — Proposed NIST Standard for Role-Based Access Control
  - Pang et al., 2019 — Zanzibar: Google's Consistent, Global Authorization System
  - Official docs: Kubernetes (Using RBAC Authorization)
---

## TL;DR
Role-based access control puts one layer of indirection, the role, between who a caller is and what
they may do: permissions attach to roles, callers are assigned roles within a scope, and a request is
allowed only if some role of the caller grants that permission on that scope. It exists because
assigning permissions directly to users does not survive growth: the assignment table explodes, access
reviews become impossible and offboarding leaks. In AI systems the same model must also bound what an
agent can do on a user's behalf and what a retriever may surface.

## Why it exists
Authentication answers "who is calling"; every request after that has to answer "may they do this".
The obvious implementation is a direct grant: a table of (user, permission) rows, or an access-control
list on each resource naming the users allowed. It works for ten users and dies at a few hundred.

Do the arithmetic. 2,000 employees and 300 distinct permissions ("read invoices", "delete customer",
"run eval job") can produce up to 600,000 direct grants. Onboarding a support engineer means someone
deciding, grant by grant, which 40 of the 300 they need; when they move to another team, someone must
remember to revoke those 40 and add 35 others. An auditor asking "who can delete customer records?"
gets an answer only by scanning the whole table, and "is this person's access appropriate?" has no
reference to compare against. Each grant is a bespoke decision, so the set drifts toward "whatever
was needed once" and never shrinks. This is how a departed contractor keeps production access.

The structure RBAC exploits is that organisations are made of jobs, and jobs need bundles of
permissions. Name the bundle (the role), attach permissions to it once, and assign people to roles.
Assignments drop from users × permissions to users → roles plus roles → permissions: with twelve roles
averaging forty permissions that is 2,000 + 480 rows instead of up to 600,000, and both reviews become
tractable. "Is the editor role's permission set right?" is a question one owner can answer; "is Alice
in the right roles?" is a question her manager can answer.

For AI systems the stakes are sharper. An agent acting for a user must not be able to do more than
that user; a retrieval system must not surface chunks from documents the user cannot open; a tool
call must be checked at the moment it runs, not at login. All three need a decision that is cheap to
evaluate on every request and every retrieved item, which is what a role lookup gives you.

## How it works
### The model
Four sets and two relations. Subjects (human users, service accounts, agents), roles, permissions
(an operation on an object type, written `documents:read`), and sessions, in which a subject activates
some of its roles. User assignment `UA ⊆ subjects × roles` and permission assignment `PA ⊆ roles ×
permissions`. The decision is: allow `(u, p)` iff there exists a role `r` with `(u, r) ∈ UA` and
`(r, p) ∈ PA`. Anything not explicitly allowed is denied, *because* a default-allow system cannot be
reasoned about: every missing rule is a hole rather than a lock.

The NIST model layers refinements on this core. **Hierarchical** RBAC lets a senior role inherit a
junior role's permissions (`admin ⊇ editor ⊇ viewer`), because without inheritance the admin's
permission list duplicates the editor's and the two drift apart. **Constrained** RBAC adds separation
of duty: nobody may hold both "submit payment" and "approve payment", because fraud should require
collusion. These exist as named levels because they are the two extensions every real deployment
reinvents.

### Scope: where a role applies
Flat RBAC answers "can this user read documents?", but the production question is "can this user read
*this tenant's* documents?". The fix is to make assignments three-part: (subject, role, scope), where
scope is a node in a resource tree such as `org → project → resource`. A role granted at a scope
applies to everything beneath it, because that is how delegation works in organisations: the org
admin administers every project. Kubernetes implements exactly this split: a `Role` and `RoleBinding`
are namespaced, while `ClusterRole` and `ClusterRoleBinding` apply cluster-wide, and binding a
ClusterRole inside a namespace restricts it to that namespace.

### Where the decision runs
Separate the *decision* (given subject, permission, scope, return allow/deny) from *enforcement*
(middleware at the API boundary, a filter in a database query, a guard in front of a tool call). The
decision needs the caller's role assignments, and there are two places to get them:

1. **From the token.** OIDC providers can put `groups` or `roles` claims in the ID or access token, so
   the check is a local set lookup with no I/O. The cost is staleness: the token says what was true
   when it was issued, so a revoked role stays usable until expiry. This is acceptable for coarse,
   rarely-changing org roles with short token lifetimes.
2. **From the authorization store.** Look the assignments up per request (cached with a short TTL).
   One extra read, but revocation takes effect in seconds, and scoped assignments at project or
   resource level, which would bloat a token, live naturally here.

Most systems do both: coarse roles in the token, scoped assignments resolved server-side.

### Enforce at every boundary, not just HTTP
A permission check in the request handler does nothing for the retrieval query that runs inside it,
the background job that re-indexes documents, or the tool the agent calls. The same decision function
has to be applied as a row or chunk filter at data access and as a guard at tool invocation, and it
must run as the *end user's* identity, not as the service's. An agent that runs under a privileged
service account has effectively every role at once; a prompt injection then inherits all of them.

### When roles are not enough
Roles describe job functions, not relationships. "The owner of a document can edit it" or "share this
with Bob" cannot be expressed without a role per document, which is role explosion by another name.
Two extensions handle this. Attribute-based access control (ABAC) adds conditions on attributes of the
subject, resource and context: `resource.classification <= subject.clearance`, `request.time in
business_hours`. Relationship-based access control (ReBAC), of which Google's Zanzibar is the
canonical design, derives permissions from a graph of tuples such as `doc:7#viewer@group:eng#member`
and is what sharing in Google Docs and Drive runs on. In practice you layer: RBAC for organisation-level
capabilities, ownership or relationship checks for per-object access, and ABAC conditions where the
customer's policy demands them.

## Example
A scoped, hierarchical RBAC check with permission-named guards. Roles inherit downward, assignments
carry a scope, and a role at any ancestor scope applies to the resource. `current_user` is the
authentication dependency from the OAuth lesson; the point here is that handlers name *permissions*,
never roles, so adding a role touches one table instead of every endpoint.

```python
from fastapi import Depends, FastAPI, HTTPException

app = FastAPI()
PERMS = {                                   # role -> permissions granted directly
    "viewer": {"documents:read"},
    "editor": {"documents:write"},
    "admin":  {"documents:delete", "members:manage"},
}
INHERITS = {"admin": "editor", "editor": "viewer"}      # senior -> junior

def permissions_of(role: str) -> set[str]:
    out: set[str] = set()
    while role:                             # walk down the hierarchy
        out |= PERMS[role]; role = INHERITS.get(role, "")
    return out

# (subject, scope) -> roles. Scopes form a tree: project -> org.
ASSIGNMENTS = {("alice", "org:acme"): {"viewer"}, ("alice", "project:42"): {"editor"}}
PARENT = {"project:42": "org:acme", "project:7": "org:acme"}

def allowed(subject: str, perm: str, scope: str) -> bool:
    while scope:                            # a role at any ancestor scope applies here
        if any(perm in permissions_of(r) for r in ASSIGNMENTS.get((subject, scope), ())):
            return True
        scope = PARENT.get(scope, "")
    return False                            # deny by default

def require(perm: str):
    def guard(project_id: int, user: str = Depends(current_user)) -> str:
        if not allowed(user, perm, f"project:{project_id}"):
            raise HTTPException(403)        # authenticated, but not authorized
        return user
    return guard

@app.delete("/projects/{project_id}/documents/{doc_id}")
def delete_doc(project_id: int, doc_id: int, user: str = Depends(require("documents:delete"))):
    return {"deleted_by": user}

assert allowed("alice", "documents:write", "project:42")       # editor at the project
assert allowed("alice", "documents:read", "project:7")         # viewer inherited from the org
assert not allowed("alice", "documents:delete", "project:42")  # admin nowhere
```

## Trade-offs
- Indirection shrinks the assignment problem from users × permissions to users + roles, but the role
  catalogue needs an owner; once roles are minted per team per exception ("role explosion") you are
  back to per-user lists with an extra hop.
- Roles carried in the token make every check a local set lookup with no database round trip, but
  revocation lags by the token lifetime; the moment you need removal to bite within seconds
  (offboarding, a compromised account) the balance flips to a server-side lookup.
- Hierarchies remove duplication and mirror the org chart, but a senior role silently inherits every
  new junior permission, which is how admins accumulate access nobody reviewed; for high-impact
  permissions (payments, deletion, key management) flat, explicitly granted roles are safer.
- Coarse roles are simple to audit, but cannot express ownership or sharing; as soon as the product
  needs "share this document with Bob" you add a relationship layer and keep RBAC only for
  organisation-level capabilities.

## Pitfalls
- Comparing role strings in code (`if user.role == "admin"`) instead of checking permissions; the
  symptom is that a new "support" role works nowhere until twenty endpoints are edited, and some
  are missed.
- Forgetting scope, so an editor in one tenant can edit in every tenant; the symptom is a
  cross-tenant finding from a penetration test or, worse, a customer.
- Enforcing only at the HTTP layer while the retrieval query or background job reads the index
  unfiltered; the symptom is the assistant quoting a document the user cannot open in the source
  system.
- Running the agent under a privileged service account instead of the user's delegated roles; the
  symptom is a prompt-injected agent deleting or reading what the asking user never could.
- Role explosion: hundreds of near-duplicate roles; the symptom is that "who can do X?" needs a
  script and quarterly access reviews are rubber-stamped.
- Stale claims after a role change; the symptom is "I removed their access an hour ago and they are
  still deleting things", because the token has not expired.

## When to use
RBAC is the default for any multi-user system with a stable set of job functions: admin panels,
SaaS workspaces, Kubernetes, cloud IAM. Add ABAC conditions when decisions depend on attributes of the
resource or context (classification, region, time of day) and ReBAC when the product has per-object
sharing. Watch the ratio of roles to users (creeping up signals explosion), time-to-revoke, the share
of checks that are permission-named rather than role-string comparisons, and whether every retrieval
and tool call carries the end user's identity. Day to day you write `require("x:y")` guards, map
identity-provider groups to roles and filter queries by scope. In interviews, explain users → roles →
permissions with scope, the token-versus-store freshness trade, and name the case RBAC cannot express.

## Role lens
- **Engineer:** Implements permission-named guards as middleware or dependencies, stores scoped
  assignments, and applies the same decision as a chunk filter in retrieval and a check before each
  tool call.
- **Architect:** Owns the role catalogue and scope hierarchy, decides what rides in the token versus
  the authorization service, and whether a central policy engine (OPA or a Zanzibar-style system) is
  justified.
- **FDE:** Maps the customer's IdP groups from Okta or Entra ID to product roles, wires SCIM-driven
  assignment, and proves to their security team that the assistant cannot see what the user cannot.

## Check yourself
**Q:** A customer wants an AI assistant over a shared document store where every document has its own
sharing list, and they also have org-level roles (admin, member, guest). Design authorization so the
assistant never leaks a document.
**A:** Separate two layers. Org-level RBAC handles coarse capabilities: who may use the assistant, who
manages members, which features guests get; carry these as roles in the token and check them with
permission-named guards at the API boundary. Document access is per-object sharing, which is
relationship-based, so do not model it as roles. Record each document's allowed principals (users and
groups) as metadata on every indexed chunk at ingestion, resolve the caller's identity and group
memberships at request time, and apply them as a filter *inside* the vector or keyword query so
unauthorized chunks never reach the prompt. Sync sharing changes incrementally and treat a stale ACL
as a leak, so re-check the top-k hits against the source system when freshness matters. Run the
assistant under the user's delegated identity, never a privileged service account, so a prompt
injection cannot widen its reach.

## Quiz
### Q: Why do permissions attach to roles rather than directly to users in RBAC?
- [ ] Because users cannot be identified reliably until after authorization has run
- [x] Because it replaces a users × permissions assignment problem with users → roles plus roles → permissions, which is far smaller to manage, review and revoke
- [ ] Because roles are stored in the identity provider while permissions are stored in the application
- [ ] Because direct permission grants cannot be represented as JWT claims
> The indirection collapses the number of assignments and makes reviews about a handful of roles.
> Where roles are stored is an implementation choice, and JWTs can carry any claim, so neither explains
> the design.

### Q: A user's role was removed ten minutes ago but they can still call a protected endpoint. What is the most likely cause?
- [ ] The role hierarchy still grants the permission through a junior role
- [x] Roles are carried in the access token, which has not expired, so the check is using stale claims
- [ ] The permission check is scoped to the wrong tenant
- [ ] The identity provider has not propagated the change through SCIM
> Token claims are a snapshot at issue time, so revocation lags by the token lifetime. A junior role
> would mean the removed role was never the source of the permission, and SCIM lag affects assignment
> creation, not a token that is already issued and valid.

### Q: Which requirement is RBAC alone poorly suited to express?
- [ ] Administrators can manage the members of their own organisation
- [ ] Viewers can read documents in the projects they are assigned to
- [x] A document's owner can edit it and share it with specific colleagues
- [ ] Only staff in the billing role can issue refunds
> Ownership and sharing are relationships between a subject and one object; expressing them with roles
> needs a role per document. The other three are role-at-scope statements that core or hierarchical
> RBAC handles directly.
