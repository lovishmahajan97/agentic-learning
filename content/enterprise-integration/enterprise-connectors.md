---
id: enterprise-connectors
module: enterprise-integration
name: Enterprise connectors (Salesforce, SAP, ServiceNow, SharePoint, Confluence, Jira, Slack, Teams)
kind: pattern
difficulty: core
est_minutes: 18
status: reviewed
related:
  - enterprise-integration/incremental-synchronization-and-cdc
  - enterprise-integration/webhooks
  - enterprise-integration/rbac-abac-mapping-to-customer-identity
  - enterprise-integration/data-mapping-and-canonical-schemas
  - rag-retrieval/acl-aware-retrieval
  - rag-retrieval/freshness-document-deletion-index-consistency
  - backend-fundamentals/pagination-rate-limits-api-quotas
  - agents/mcp-authentication-authorization-transport-security
sources:
  - Official docs: Microsoft Graph (delta query, SharePoint and Teams resources)
  - Official docs: Salesforce REST, Bulk and Change Data Capture APIs
  - Official docs: Atlassian Confluence and Jira Cloud REST APIs
  - Official docs: Slack Web API and Events API
---

## TL;DR
An enterprise connector is the adapter that pulls content, metadata and permissions out of a system of
record (Salesforce, SAP, ServiceNow, SharePoint, Confluence, Jira, Slack, Teams) and keeps that copy
current, or that lets an agent act in it. The hard part is not the first fetch: it is authentication,
rate limits, incremental change, deletions and faithfully carrying each system's permission model, because
an AI assistant that ignores any of these is either stale or leaks data.

## Why it exists
Enterprise knowledge lives in a dozen SaaS and on-prem systems, each with its own API, object model, query
language, auth scheme and permission semantics. A RAG assistant or agent is only as useful as the systems
it can see, so "can it read our Confluence and Jira?" is usually the first question in a deal.

The naive approach is a script per source: authenticate with an admin token, page through everything,
dump the text into a vector store. It demos well and fails in production for four reasons. First, it reads
with admin rights and discards the source ACLs, so the assistant happily answers a junior employee's
question with a page from a restricted HR space. Second, re-fetching everything nightly burns through API
quotas that the customer's other integrations also depend on, and gets throttled. Third, it never notices
deletions, so documents removed for legal reasons keep surfacing. Fourth, it hard-codes one tenant's quirks,
so the second customer breaks it. A connector is the disciplined version: a repeatable contract for
auth, enumeration, change tracking, permissions and normalization that every source implements.

## How it works
### The connector contract
Every connector, whatever the source, implements the same five responsibilities, *because* the downstream
pipeline (chunking, embedding, ACL-aware retrieval, tool calling) should not care where a document came from:

1. **Authenticate** as an app registered in the customer's tenant (OAuth client credentials, a service
   account, or delegated user tokens), with the narrowest scopes that work.
2. **Enumerate** the containers in scope (sites, spaces, projects, channels, objects) and page through
   items, honouring pagination cursors and rate-limit responses (HTTP 429, `Retry-After`).
3. **Track change**: store a cursor (delta token, change-event replay ID, or `updated` watermark) so the next
   run fetches only what changed, including deletions.
4. **Capture permissions** alongside content: who can see this item, expressed as source principals that can
   later be resolved to users.
5. **Normalize** into a canonical document (id, source URL, title, body, author, timestamps, ACL, source
   metadata) so one indexing pipeline serves all sources.

### What differs per system
- **Salesforce.** Records are queried with SOQL over the REST API; large extracts go through the Bulk API
  because per-record REST calls burn the org's API allocation. Change is available via Change Data Capture
  events or `SystemModstamp` watermarks. Visibility is computed from profiles, permission sets, role
  hierarchy and sharing rules, which is too complex to replicate faithfully, so many connectors restrict
  scope to objects with simple sharing or check access at query time.
- **SAP.** Access is typically through OData services (S/4HANA, SAP Gateway), RFC/BAPI calls, or IDoc
  messages for asynchronous document exchange. Data is structured transactional records rather than prose,
  so the connector is more often a tool an agent calls (look up an order, check stock) than a corpus to
  embed. Authorization objects are fine-grained and usually enforced by calling SAP as the user.
- **ServiceNow.** The Table API exposes records (incidents, knowledge articles, CMDB items) with encoded
  queries. ACLs are scripted per table and field, so a service account's view is not a user's view.
- **SharePoint and Teams.** Both are reached through Microsoft Graph. Drive and list items support delta
  queries that return changes and deletions since a token. Permissions are per site, library, folder and
  item, with inheritance that can be broken at any level. Teams channel messages are also in Graph; some
  message-export APIs are classed as protected and require an approval or licensing step, so check before
  promising them.
- **Confluence and Jira.** Atlassian REST APIs with CQL and JQL for querying by `lastmodified` or `updated`.
  Confluence combines space permissions with page restrictions that inherit down the page tree; Jira uses
  project permission schemes plus optional issue security levels.
- **Slack.** The Web API (`conversations.history`, `conversations.replies`) for backfill, the Events API for
  live changes, with method-specific rate-limit tiers. Slack has tightened history-read limits for some
  classes of apps (the 2025 changes for apps not distributed through its Marketplace), so check the
  current limits for your app type before sizing a backfill. Visibility is channel membership, which changes
  often, and private channels and DMs are only visible to apps that have been added or granted the right scopes.

### From source principals to people
The ACL a connector captures is written in the source's own vocabulary: SharePoint and Teams use Entra
object IDs plus SharePoint site groups, Confluence and Jira use Atlassian account IDs and groups, Slack uses
its own user IDs and channel membership, Salesforce uses user IDs, roles and public groups. None of these is
the identity the user signs in with. The connector therefore also maintains a **principal crosswalk**:
source principal ID to the IdP's immutable user or group ID, built from each source's user directory API
and joined on the IdP object ID where the source records it (Microsoft sources do) or on a verified email
where it does not. Source-local groups are expanded to members, nested groups recursively, *because* the
retrieval filter can only test identities it knows. Two special cases need explicit modelling rather than
dropping: sharing links ("anyone in the organization with the link", anonymous links), which grant access
to a scope rather than to a listed principal, and guest or external users with no IdP account. A principal
that resolves to nobody must fail closed (it grants nothing), not be ignored in a way that widens access.

### Sizing the first crawl
The initial backfill, not the steady state, decides the project plan. Take an illustrative SharePoint
tenant with 2 million files: one content download plus one permissions call per file is about 4 million
requests. At a sustained 10 requests per second (an illustrative rate; the real one depends on the
source's throttling for your app and tenant) that is 400,000 seconds, roughly four and a half days, and
every retry after a 429 extends it. The levers follow from the arithmetic: batch requests where the API
allows it (Graph JSON batching groups up to 20 requests per call, though throttling still counts the
individual requests), read permissions once per folder whose inheritance is intact instead of once per
file, crawl the highest-value containers first so the pilot is useful before the crawl finishes, and make
the crawl resumable by checkpointing per container. After the backfill, cost scales with the change rate,
which is typically a small fraction of the corpus per day.

### Build, buy or use MCP
Unified-API vendors and the AI platforms' own connector catalogues cover the common sources; building
your own makes sense when you need permissions, fields or freshness they do not provide. For agent actions,
wrapping each system as an MCP server gives a standard tool interface, but it does not remove any of the
auth, quota or permission work above.

## Example
A SharePoint document-library connector using Microsoft Graph delta queries. The first call returns every
item; subsequent calls with the saved `deltaLink` return only changes, and deleted items carry a `deleted`
facet, which is how the connector removes them from the index.

```python
import time, requests

GRAPH = "https://graph.microsoft.com/v1.0"

def sync_drive(token: str, drive_id: str, saved_delta: str | None, index) -> str:
    """Pull changes since saved_delta into `index`; return the new delta link to persist."""
    url = saved_delta or f"{GRAPH}/drives/{drive_id}/root/delta"
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 429:                       # throttled: honour the server's hint
            time.sleep(int(r.headers.get("Retry-After", "10")))
            continue
        r.raise_for_status()
        page = r.json()
        for item in page["value"]:
            key = f"sharepoint:{drive_id}:{item['id']}"
            if "deleted" in item:
                index.delete(key)                      # deletions arrive as tombstones
            elif "file" in item:
                perms = requests.get(f"{GRAPH}/drives/{drive_id}/items/{item['id']}/permissions",
                                     headers=headers, timeout=30).json()["value"]
                index.upsert(key, {
                    "title": item["name"],
                    "url": item["webUrl"],
                    "modified": item["lastModifiedDateTime"],
                    # sharing links carry a "link" facet with a scope instead of a named principal
                    "acl": [p.get("grantedToV2") or p.get("grantedToIdentitiesV2")
                            or {"link": p.get("link", {}).get("scope")} for p in perms],
                })                                     # content download + parsing happens downstream
        if "@odata.nextLink" in page:
            url = page["@odata.nextLink"]              # more pages in this round
        else:
            return page["@odata.deltaLink"]            # persist; next run starts here
```

Note what the code commits to: one permissions call per changed file (fine for incremental runs, expensive
for a 2-million-file initial crawl, which is where batching and a separate permission-sync job come in;
the permissions call also needs the same 429 handling as the delta call, omitted here for length).
The ordering matters too: the `deltaLink` is persisted only after the last page is processed, so a crash
mid-round replays the round from the previous token, and the upserts and deletes are idempotent by key,
so replaying them is harmless.
It also only re-reads permissions for items the delta reports: by default a delta round surfaces content and
metadata changes, and a sharing change on a folder does not list every descendant whose effective access
changed. Graph has request options for reporting sharing changes on business drives; without them, or in
addition to them, run a periodic permission resync.

## Trade-offs
- Copying content into your own index gives fast, rankable retrieval, but creates a second copy whose
  permissions and freshness you must keep in sync; federated live search through source APIs avoids that
  copy and flips the cost to latency and per-query rate limits.
- Application-level service accounts make crawling simple, but they see more than any user, so you must
  replicate ACLs; delegated user tokens enforce source permissions natively but see only that one user's
  slice and stop working when the user's refresh token or consent is revoked.
- Buying a connector platform gets twenty sources quickly, but you inherit its permission fidelity and sync
  cadence; building wins when one source is business-critical and needs field-level control.
- Syncing permissions at index time makes queries cheap, but permission changes lag by one sync cycle;
  checking access live at query time is exact but adds a source API call per candidate document.

## Pitfalls
- Crawling with an admin token and dropping ACLs; the symptom is a user reporting the assistant quoted
  a document they cannot open in the source system.
- Re-fetching full corpora nightly instead of using delta cursors; the symptom is HTTP 429s and the
  customer's other integrations failing during your crawl window.
- Ignoring deletions and permission removals; the symptom is retrieval returning links that 404 or
  show "access denied" in the source.
- Treating broken permission inheritance as rare in SharePoint or Confluence; the symptom is a few
  sensitive folders indexed with their parent site's wider audience.
- Joining source users to IdP users by display name or an unverified email; the symptom is ACL entries
  that resolve to nobody (users see less than in the source) or to the wrong person after a name collision.
- Dropping sharing-link permissions because they name no principal; the symptom is documents shared
  "with the organization" being invisible to everyone, or, if mapped carelessly, visible to guests.
- Promising a source (Teams messages, Slack DMs, a SAP module) before checking the API scope, approval or
  licence it needs; the symptom is a blocked project week three.

## When to use
Build or adopt a connector whenever an AI feature must read or act on a customer system more than once.
For a one-off pilot, an export file may be enough; for production, insist on delta sync, deletion handling
and permission capture from day one, because retrofitting ACLs onto a populated index means re-crawling it.
Before committing to a timeline, size the backfill: item count times calls per item divided by the
sustained rate the source will actually give you. Measure sync lag (source change to searchable), API calls
per sync and share of the customer's quota, 429 rate, permission-mismatch rate from spot checks as
restricted test users, unresolved-principal count, and items in error. In interviews, the expected depth is naming the five responsibilities and explaining
how one specific source models permissions and change.

## Role lens
- **Engineer:** Implements the connector contract per source: OAuth app, paging with backoff, delta
  cursors persisted per container, tombstone handling and ACL extraction into the canonical document.
- **Architect:** Decides copy-and-index versus federated query per source, build versus buy, and where
  permission enforcement lives so every connector feeds one ACL-aware retrieval path.
- **FDE:** Gets the app registered and admin consent granted in the customer tenant, negotiates API quota
  with the customer's admins and verifies permissions on real restricted spaces before go-live.

## Check yourself
**Q:** A customer wants their assistant to answer from Confluence and Slack. Security asks how you prevent
a user from seeing content they cannot see in the source. What is your design?
**A:** Capture permissions as source principals during sync: Confluence space permissions plus inherited page
restrictions, Slack channel membership. Resolve principals to users through the customer IdP (groups via
SCIM or Graph) and store them on each chunk. At query time, filter retrieval by the requesting user's
resolved principal set before ranking, never after generation. Because Slack membership changes often, sync
membership more frequently than content, or check membership live for private channels. Log every answer's
source documents so access can be audited, and spot-check by querying as a restricted test user.

## Quiz
### Q: Why do production connectors use delta tokens or change events rather than nightly full crawls?
- [ ] Full crawls cannot read file contents
- [x] They fetch only changes and deletions, staying within API quotas and noticing removals
- [ ] Delta tokens make the content more accurate for embedding
- [ ] Source systems forbid full crawls after the first one
> A full crawl repeats work, competes for the customer's shared API allocation and cannot easily see what
> disappeared. Delta mechanisms return just the changes, including deletion tombstones, so the index stays
> fresh and consistent cheaply.

### Q: A connector indexes SharePoint using an app-only token with read access to all sites. What must it add to be safe?
- [ ] A higher rate limit so crawling finishes faster
- [ ] Encryption of the vector store at rest
- [x] Per-item permissions captured at sync and enforced as a retrieval filter for the requesting user
- [ ] A rule that only site owners may use the assistant
> An app-only token sees everything, so the index must carry each item's ACL, including broken inheritance,
> and filter by the user's principals. Encryption protects storage but does nothing about authorized users
> seeing too much.

### Q: Why is SAP more often integrated as an agent tool than as a document corpus?
- [ ] SAP has no API of any kind
- [ ] SAP data is always encrypted and cannot be read
- [x] Its data is structured transactional records with fine-grained authorization best enforced by calling SAP itself
- [ ] Embedding models cannot process German text
> Orders, materials and invoices are rows, not prose, and change constantly; querying them live through
> OData or BAPI calls returns current values and lets SAP's own authorization objects apply, which embedding
> snapshots would bypass.
