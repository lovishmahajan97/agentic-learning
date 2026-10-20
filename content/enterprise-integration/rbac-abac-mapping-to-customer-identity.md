---
id: rbac-abac-mapping-to-customer-identity
module: enterprise-integration
name: RBAC / ABAC mapping to customer identity
kind: pattern
difficulty: advanced
est_minutes: 17
status: reviewed
related:
  - enterprise-integration/sso-iam-integration
  - enterprise-integration/scim-provisioning
  - enterprise-integration/enterprise-connectors
  - backend-fundamentals/rbac
  - rag-retrieval/acl-aware-retrieval
  - agent-reliability/tool-permissions-and-least-privilege
  - agent-runtime-architecture/policy-enforcement-across-agents-and-tools
  - safety-security/cross-tenant-data-leakage
sources:
  - Ferraiolo & Kuhn, 1992 — Role-Based Access Controls
  - NIST, 2014 — SP 800-162: Guide to Attribute Based Access Control (ABAC)
  - Pang et al., 2019 — Zanzibar: Google's Consistent, Global Authorization System
---

## TL;DR
Your product has its own permission model; each customer's identity provider has its own groups and
attributes. Mapping is the per-tenant translation layer between them: customer group IDs become your roles
(RBAC), and customer attributes such as department, region or clearance become inputs to policies (ABAC).
It must be explicit, stored as configuration, keyed by immutable IDs and evaluated on every request,
because the AI layer (retrieval filters, agent tools) is where a wrong mapping turns into a data leak.

## Why it exists
A customer arrives with an Entra or Okta directory full of groups like `GRP-FIN-EMEA-RO`, `All Staff` and
`Contractors-2023`, and attributes like `department=Legal` and `country=DE`. Your product has roles such as
`viewer`, `editor` and `admin`, and documents synced from their systems with their own ACLs. Nobody
designed these to fit.

The naive approaches fail in predictable ways. Hard-coding group names in code ("if 'Admins' in groups")
works for one customer and is wrong for the next, and breaks on rename. Granting everyone in the tenant the
same access ignores the fact that their source systems already restrict HR, M&A and legal content.
Re-implementing their whole permission model inside your product creates a second source of truth that
drifts. Pure RBAC also struggles with rules like "regional managers see deals in their region": with six
functions, five regions and three seniority levels, encoding each combination as a role gives 90 roles,
and adding one region adds 18 more, each of which a customer admin must bind and review. ABAC states the
rule once, `subject.region == resource.region`, and adding a region adds no policy. And AI adds a new principal: an agent acting for a user
must not exceed that user's rights, nor be granted broader rights because it runs as a service. A mapping
layer gives each customer a reviewable, testable statement of who can do what.

## How it works
### Three kinds of permission inputs
1. **Product permissions**: what actions exist in your app (configure workspace, run agent X, export).
   These are your roles; RBAC fits *because* the action set is small and stable.
2. **Data permissions**: who may see each document or record. For synced content these come from the
   source system's ACLs (users and groups), so you carry them rather than invent them.
3. **Contextual rules**: attribute conditions such as region, department, data classification, device or
   time. ABAC fits *because* these are combinations that would otherwise multiply roles.

### The mapping record (per tenant)
- `role_bindings`: IdP group *object ID* to product role, e.g. Entra group `3f2a…` to `workspace_admin`.
  IDs, *because* display names are neither unique nor stable.
- `attribute_map`: IdP claim name to your attribute, e.g. `extension_region` to `region`, with allowed values.
- `policies`: ABAC rules over subject, resource and action attributes, e.g. `resource.classification ==
  "confidential" requires subject.clearance >= 2`.
- `default`: what an authenticated user with no matching binding gets, usually nothing beyond basic access.

### Translating source ACLs into the customer's identities
Carried ACLs name principals in each source's vocabulary (SharePoint site groups, Confluence groups, Slack
channels, Salesforce public groups), not the IdP groups your role bindings use. There are two ways to join
them, and the choice is a real design decision:
- **Expand at index time.** Resolve each document's ACL to user IDs and store those. Queries are a simple
  membership test, but one person joining a 10,000-member group rewrites the ACL of every document that group
  can see, so membership churn becomes index churn.
- **Resolve at query time.** Store source principal IDs on documents, and compute for each user the set of
  source principals they hold (from a crosswalk of source identities to IdP IDs plus synced source-group
  membership). Documents change only when their own ACL changes; the cost moves to keeping the per-user
  principal set current and fast to look up. It is the more common choice once groups are large.
Either way, a source principal the crosswalk cannot resolve grants nothing, *because* guessing (by display
name, say) is how one person's access lands on another.

### Resolving a principal
At login (OIDC/SAML) and on SCIM updates, build the principal: stable user key, group IDs (including nested
groups resolved by the IdP or Graph), mapped roles and attributes. Cache with a short TTL and invalidate on
SCIM events.

### Enforcing
Evaluate in one policy decision point, *because* scattered `if` statements cannot be audited. Fix the
combining rules explicitly: deny by default, an explicit deny overrides any allow, and a policy whose
attribute is missing evaluates to deny (fail closed), with the missing-attribute rate monitored so that
fail-closed shows up as a data-quality alert rather than as silent lockouts. Options range
from a small in-process function to a policy engine (OPA/Rego, Cedar) or a relationship-based system
(Zanzibar-style, e.g. OpenFGA or SpiceDB) when permissions follow object graphs like folders and shares.

### AI-specific enforcement
- **Retrieval**: filter candidate chunks by the principal's source groups and attributes *before* ranking
  and generation, never by asking the model to withhold.
- **Agents**: effective permission = user permission ∩ agent's granted scope. The agent never gains
  rights the user lacks, and a broadly entitled user does not make a narrow agent broad.
- **Audit**: log the decision inputs (principal, rule matched) so a customer can ask "why could Ana see this?"

## Example
An in-process evaluator showing the three layers. Tenant config is data, not code; agent scope intersects
with user rights.

```python
TENANT_CFG = {
    "acme": {
        "role_bindings": {"3f2a9c1e": "workspace_admin", "8b7d0e44": "analyst"},  # Entra group IDs
        "attribute_map": {"extension_region": "region", "department": "department"},
    }
}
ROLE_ACTIONS = {"workspace_admin": {"configure", "query", "export"}, "analyst": {"query"}}

def principal(tenant: str, claims: dict) -> dict:
    cfg = TENANT_CFG[tenant]
    groups = set(claims.get("groups", []))
    roles = {r for g, r in cfg["role_bindings"].items() if g in groups}
    attrs = {ours: claims.get(theirs) for theirs, ours in cfg["attribute_map"].items()}
    return {"tenant": tenant, "groups": groups, "roles": roles, "attrs": attrs}

def can_act(p: dict, action: str, agent_scope: set | None = None) -> bool:
    allowed = set().union(*(ROLE_ACTIONS[r] for r in p["roles"])) if p["roles"] else set()
    if agent_scope is not None:
        allowed &= agent_scope                       # agent never exceeds the user, nor vice versa
    return action in allowed

def can_read(p: dict, doc: dict) -> bool:
    if doc["tenant"] != p["tenant"]:
        return False                                 # tenant boundary first, always
    if not (p["groups"] & set(doc["acl_groups"])):   # source-system ACL (RBAC on data)
        return False
    if doc.get("region") and doc["region"] != p["attrs"].get("region"):
        return False                                 # ABAC: regional restriction
    return True

ana = principal("acme", {"groups": ["8b7d0e44", "g-sales-emea"], "extension_region": "EMEA"})
deal = {"tenant": "acme", "acl_groups": ["g-sales-emea", "g-sales-amer"], "region": "AMER"}
print(can_act(ana, "query"), can_act(ana, "export"), can_read(ana, deal))   # True False False
print(can_act(ana, "query", agent_scope={"export"}))                       # False
```

Ana is an analyst in the EMEA sales group. She can query but not export; she is in a group on the deal's
ACL, yet the regional attribute rule denies the AMER deal; and an agent scoped only to `export` cannot query
on her behalf.

## Trade-offs
- RBAC role bindings are easy for customer admins to review, but encoding contextual rules as roles explodes
  their number; switch to ABAC when roles start encoding region × function × level.
- ABAC expresses fine-grained rules compactly, but decisions depend on attribute quality in the customer's
  directory; if `department` is blank for a third of users, policies silently deny or over-grant.
- Carrying source-system ACLs keeps your app consistent with the customer's existing permissions, but
  inherits their mistakes and lags by a sync cycle; live checks against the source are exact but slow.
- A central policy engine gives auditability and consistent decisions, but adds a dependency on the request
  path; an in-process library is faster and is enough while rules stay simple.

## Pitfalls
- Binding roles to group display names; the symptom is a customer renaming a group and all its members
  losing or gaining access overnight.
- A permissive default for unmapped users; the symptom is a newly onboarded department seeing the whole
  workspace because no binding matched.
- Filtering retrieval after generation or relying on the prompt to withhold; the symptom is restricted facts
  appearing in answers, paraphrased.
- Running agents with a service identity broader than the user; the symptom is an agent completing an action
  the user could not perform directly in the UI.
- Expanding document ACLs to individual users at index time for large groups; the symptom is index write
  volume and permission lag that spike whenever HR moves people between big groups.
- Ignoring nested groups; the symptom is users whose access comes through a parent group seeing nothing.

## When to use
Introduce an explicit mapping layer as soon as a second customer brings its own directory, and before any AI
feature indexes customer content. Start with role bindings by group ID plus carried source ACLs; add ABAC
rules when customers ask for regional, classification or clearance restrictions. Measure unmapped-user
counts per tenant, denied-request reasons, and results from periodic access reviews where a customer admin
checks a sample of decisions. In interviews, explain how user rights, agent scope and document ACLs combine,
and where the decision is enforced.

## Role lens
- **Engineer:** Implements principal resolution, the policy decision function or engine integration, and
  pre-retrieval filters that use source ACL groups and attributes.
- **Architect:** Defines the product permission model, chooses RBAC, ABAC or relationship-based enforcement,
  and sets the rule that agent permissions are the intersection with the user's.
- **FDE:** Runs the mapping workshop with the customer's IAM admin, records group-ID bindings and attribute
  sources, and validates with test users from each sensitive group.

## Check yourself
**Q:** A customer wants: admins configure the workspace, everyone else queries, and deal documents are visible
only within the user's region unless they are in the global deals team. How do you model this?
**A:** Product permissions are RBAC: bind the customer's admin group ID to `workspace_admin`, and a default or
all-staff group ID to `analyst`. Document visibility starts from carried source ACLs. The regional rule is
ABAC: map the IdP region attribute and tag each deal with its region, then allow read when regions match or
the user is in the global-deals group ID. Enforce in one policy function used by the retrieval filter and by
agent tools, and verify attribute coverage first, because users with no region would otherwise be denied
everything.

## Quiz
### Q: Why should role bindings reference IdP group object IDs rather than names?
- [ ] IDs are shorter, so tokens are smaller
- [x] Names can be duplicated and renamed, while object IDs are unique and stable
- [ ] SAML cannot transmit group names
- [ ] Names leak information about the customer's org chart
> A rename or a second group with the same display name would silently change who holds a role; immutable IDs
> keep bindings correct, with names shown only for readability.

### Q: An agent with scope {query, summarize} runs for a user who can {query, export}. What may it do?
- [ ] query, summarize and export
- [ ] summarize only
- [x] query only
- [ ] export only
> Effective permissions are the intersection of the user's rights and the agent's scope, so only query is
> allowed; a union would let either party amplify the other.

### Q: When does ABAC become preferable to adding more roles?
- [ ] When the product has fewer than five actions
- [ ] When the customer uses SAML instead of OIDC
- [x] When access depends on combinations of attributes such as region, classification and department
- [ ] When users belong to only one group each
> Expressing attribute combinations as roles multiplies them, one per combination; ABAC states the rule once
> over attributes, at the cost of depending on attribute quality.
