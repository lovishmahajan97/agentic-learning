---
id: data-access-and-permissions-discovery
module: scoping
name: Data access & permissions discovery
kind: practice
difficulty: core
est_minutes: 16
status: reviewed
related:
  - rag-retrieval/acl-aware-retrieval
  - enterprise-integration/rbac-abac-mapping-to-customer-identity
  - enterprise-integration/enterprise-connectors
  - enterprise-integration/data-profiling-and-quality-assessment
  - security-governance-compliance/data-classification
  - agent-reliability/tool-permissions-and-least-privilege
  - scoping/dependency-and-risk-management
---

## TL;DR
Data access and permissions discovery establishes, before building, which data a use case needs, where
it lives, who owns it, how you can technically and legally reach it, and how the source's permission
rules must carry into the AI system. It exists because access approvals are often the longest lead
time in an enterprise AI project, and because a prototype built on an export quietly discards the
permissions that production must honour.

## Why it exists
The typical stall in enterprise AI work is not model quality. It is week six of a pilot, the prototype
works on a folder of files someone emailed over, and the team is still waiting for a service account,
a firewall rule, a data-processing agreement or the data owner's sign-off for production access. Each of
those has its own approver, queue and lead time, and none of them was started on day one because nobody
listed them.

The second failure is worse because it is invisible. Source systems enforce permissions: a SharePoint
site restricted to HR, a CRM record visible only to its account team, a database row filtered by region.
When you export documents into a vector index, or connect with a broad service account, those rules are
gone. The assistant now answers anyone's question from everyone's data, which is a data leak delivered
through a helpful interface. The demo cannot reveal this, because the demo user is usually an admin.

The naive approach, "get the data first and sort out permissions later", fails on both counts: it
defers the longest lead times and bakes a permission-free design into the architecture. Discovery runs
access and permissions as a first-class workstream, in parallel with the technical spike, so the
prototype is built on the same access path and permission model production will use.

## How it works
### 1. Derive the data needs from the use case
From the decomposition, list every input each node needs and every system each action touches. For
each, ask whether it is needed to answer, to act, or only to evaluate. Fewer sources means fewer
approvals and less exposure, *because* each source carries its own owner, contract and risk.

### 2. Build a source inventory
For each source record: system and owner (a named person, not a team), data volume and format, freshness
requirement, access method (API, connector, database replica, file export, event stream), authentication
method, rate limits, and whether a sandbox or non-production copy exists. Ask for a sample in week one,
*because* samples expose format and quality problems that descriptions hide.

### 3. Map the permission model at the source
Find out how the source decides who sees what: document or item-level ACLs, inherited folder permissions,
group membership from the identity provider, record ownership and sharing rules in a CRM, row-level
security in a database, or attribute-based rules (region, clearance, customer). Note how often
permissions change and whether the source exposes them through its API, *because* an AI system can only
enforce what it can read.

### 4. Classify the data and find the legal constraints
Determine sensitivity (public, internal, confidential, regulated personal or health data), applicable
regulation, contractual limits on use (for example, whether data may be sent to a third-party model
provider), residency requirements and retention or legal-hold obligations. These decide the deployment
shape (provider API, private endpoint, in-customer-VPC), so they must be known before architecture is
fixed.

### 5. Choose how permissions are enforced in the AI system
The main options:
- **Delegated user access**: the system calls the source with the end user's own token (for example via
  an OAuth token-exchange or on-behalf-of flow), so the source enforces its own rules. Most faithful, but slower and bound by
  per-user rate limits.
- **Index with ACL metadata**: ingest content with its permission principals attached, and filter every
  retrieval by the requesting user's resolved groups. Fast, but you must sync permission changes,
  resolve nested groups correctly, and honour explicit deny entries where the source has them. Apply the
  filter inside the search (pre-filtering), not to the top-k results afterwards, *because* post-filtering
  a top-10 list for a user who can see a small share of the corpus often leaves zero results, which looks
  like a retrieval bug rather than a permission rule.
- **Partitioned indexes**: one index per audience (per department or tenant). Simple and robust, but
  only works when permissions are coarse.
Each choice depends on what step 3 found, *because* a source that does not expose ACLs through its API
forces delegated access or coarse partitioning.

### 6. Treat derived data as carrying its sources' permissions
Embeddings, chunk stores, cached answers, conversation memory, summaries, prompt and trace logs, and
evaluation datasets all contain or reveal source content. Each derived item should be visible only to
users who could see every source it was built from, *because* a summary of an HR file and a public memo
is as sensitive as the HR file. List where derived data lands (including the observability tool and the
model provider's logs) and who can read each of those stores.

### 7. Apply least privilege to actions
For agents that write (create tickets, update records, send email), list each action, the scope it
needs, and whether it acts as the user or as a service. Request read-only scopes where reading suffices
and gate write actions behind approval.

### 8. Map the approval path and lead times
For each source, list who must approve access (data owner, security, privacy, legal, the platform team
that issues credentials) and their typical turnaround. Start the slowest first and track them as project
dependencies with owners and dates.

### 9. Write permission tests early
Before launch, and ideally in the prototype, test that a user without access to a document cannot
retrieve, quote or summarise it, including through indirect questions. Use real test identities with
differing access.

## Example
A worked scenario (lead times and counts illustrative). A legal team wants an assistant over contracts
in SharePoint, matter data in a practice-management database, and client billing in the ERP.

In week one the FDE builds the inventory. SharePoint holds about 40,000 documents, with permissions
inherited from matter sites, plus item-level exceptions for conflicted matters where named lawyers are
walled off. The practice-management database is reachable only through a nightly replica, and row-level
visibility is enforced by its application, not the database, so a direct database connection would see
everything. Billing data is classified confidential, and the ERP owner says AI use requires a privacy
review.

Decisions follow from the facts. Billing is cut from the first phase, because the use case can answer
its target questions without it and the privacy review would dominate the timeline. Contracts are
indexed with their ACL principals and filtered by the user's resolved groups at query time; the ethical
walls make permission sync freshness a hard requirement, so the team asks how quickly permission changes
must propagate and designs incremental ACL sync to meet it. Because a sync can still lag, documents
from walled matters are also re-checked against SharePoint's live permissions at query time, before any
of their text reaches the prompt. Matter data is read through the application's
API with delegated user tokens rather than from the replica, so the application keeps enforcing its row
rules.

The security approval for the API integration and the service registration has the longest lead time,
so it is requested in week one. The first test written is: a lawyer walled off from a conflicted matter
asks "summarise the indemnity terms in the Acme agreement" and must get no content from it.

## Trade-offs
- Delegated user access keeps the source authoritative for permissions, but adds latency and per-user
  rate limits; when queries are high-volume and permissions are coarse, an ACL-filtered index is faster.
- Indexing content with ACL metadata enables fast filtered retrieval, but makes you responsible for
  syncing permission changes; when walls or revocations must apply immediately, delegate instead.
- Cutting sensitive sources from phase one shortens approvals dramatically, but limits what the system
  can answer; add them once the access pattern is proven and the review has had time to run.
- Using a non-production copy speeds development, but its permissions and data quality may differ from
  production; when the use case depends on fine-grained ACLs, test against production permissions early.

## Pitfalls
- Building the prototype on an exported folder; the symptom is a demo that delights an admin user but
  answers questions from documents ordinary users were never allowed to see.
- Connecting with a broad service account and promising to "add permissions later"; the symptom is an
  architecture with no place to carry user identity, discovered during security review.
- Ignoring permission-change latency; the symptom is a revoked or walled-off user still retrieving
  content for hours or days after the change, until the next full re-index.
- Naming a team instead of a person as data owner; the symptom is access requests that sit unanswered
  for weeks because nobody feels entitled to approve them.
- Securing retrieval but not the traces; the symptom is a developer or vendor support engineer able to
  read restricted document text in the prompt logs of the observability tool.
- Treating "the data exists" as "the data is usable"; the symptom is a pipeline that connects
  successfully and returns scanned images, empty fields or a schema nobody can explain.

## When to use
Start this in the first week of any engagement that touches customer data, which in enterprise AI is
almost all of them, and run it in parallel with the technical spike rather than after it. Revisit it
whenever a new source or action is added. It is non-negotiable for retrieval over internal documents,
for agents with write access, and for regulated data. Measure it by whether every source has a named
owner, an access method, a permission model, a classification and an approval with a date, and whether
permission tests exist and pass. In interviews, "how would you build RAG over the company's SharePoint"
is often really a question about this topic.

## Role lens
- **Engineer:** Implements identity propagation and ACL filtering on retrieval, and writes automated
  permission tests with distinct test identities that run on every index change.
- **Architect:** Chooses delegated access, ACL-filtered indexing or partitioning per source, and sets the
  deployment boundary that the data classification and residency rules require.
- **FDE:** Finds the real data owners, files and chases access requests from week one, and surfaces
  permission models and lead times that would otherwise stall the pilot.

## Check yourself
**Q:** A customer asks for a company-wide assistant over their document management system and offers a
service account with read access to everything "to get started quickly". How do you respond?
**A:** I would accept it only for a sandbox with non-sensitive content, and not as the production design.
A read-everything account erases the source's permissions, so every user could retrieve anything the
account sees. Instead I would find out how the system expresses permissions (item ACLs, inherited site
permissions, identity-provider groups) and whether its API exposes them, then either ingest with ACL
principals and filter by the user's resolved groups or call the source with delegated user tokens. I
would also ask how quickly revocations must propagate, classify which content is sensitive, start the
approvals now, and write a test proving a user cannot retrieve a document they lack access to.

## Quiz
### Q: Why is a prototype built on exported documents risky even if it performs well?
- [ ] Exported documents are usually lower resolution than the originals
- [x] The export drops the source's permissions, so the design never accounts for who may see what
- [ ] Exports cannot be embedded by most embedding models
- [ ] Exported documents are always out of date
> Exports and broad service accounts discard ACLs. The prototype then demonstrates answers that ordinary
> users should never get, and the architecture has no place to carry user identity into retrieval.

### Q: A source system enforces row-level visibility in its application layer, not in its database. What does that imply for the AI integration?
- [ ] A read replica of the database is the safest integration point
- [ ] Row-level rules can be ignored because the database has no restrictions
- [x] Reading the database directly bypasses the rules, so access should go through the application's API with user context
- [ ] The data must be copied into a separate warehouse first
> If the application enforces visibility, the database itself returns every row to any connection.
> Going through the application's API as the user keeps its rules in force; a direct replica bypasses them.

### Q: Why start access and approval requests in the first week rather than after the prototype proves value?
- [ ] Approvers prefer to see requests before any code exists
- [ ] Early requests are cheaper because licences cost less
- [x] Approvals often have the longest lead times, and the access path chosen shapes the architecture
- [ ] The prototype cannot run without production credentials
> Security, privacy and owner sign-offs run on their own queues. Deferring them adds their full lead time
> after the prototype, and the access method (delegated, ACL index, partitioned) changes the design.
