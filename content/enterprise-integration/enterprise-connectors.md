---
id: enterprise-connectors
module: enterprise-integration
name: Enterprise connectors (Salesforce, SAP, ServiceNow, SharePoint, Confluence, Jira, Slack, Teams)
kind: pattern
difficulty: core
est_minutes: 18
status: typed
lesson_type: decision
agent_relevance: core
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
Connectors are how an enterprise agent sees and acts: they sync content, metadata and permissions into
its RAG index, or expose a system as a tool. Get auth, rate limits, change, deletions or permissions
wrong and the agent is stale or leaky.

## For agent builders
- **Why it matters for agents.** An agent is only as useful as the systems it can reach. Retrieval tools
  read the synced copy; action tools (create a Jira issue, look up an SAP order) call the source live. Both
  inherit every auth, quota and permission decision the connector makes.
- **Where it shows up in your stack.** The ingestion pipeline behind your vector store, the MCP servers or
  function tools your agent calls, the tool's OAuth app registration in the customer tenant, and the
  retrieval filter that applies ACLs before the model sees a chunk.
- **What you control.** Sync versus live-tool per source, which identity each tool call uses (service
  account or the end user's delegated token), per-tool call budgets so a looping agent cannot exhaust the
  customer's API quota, and the canonical document shape every source normalizes into.
- **What you can skip.** Memorising each vendor's object model. Know the five-step contract, how each
  source models change and permissions, and the backfill arithmetic; read the vendor docs per project.

## Decision case
### Scenario
A 6,000-employee manufacturer wants an internal help agent that answers from SharePoint, Confluence and
Slack and opens or updates ServiceNow incidents. You must pick the connector approach for the pilot. All
figures are illustrative planning numbers, not vendor limits.
- **Corpus:** 1.2 million SharePoint files in 30,000 folders, 80,000 Confluence pages, 400 Slack channels.
- **Load:** 3,000 agent questions a day, each with about 2 retrieval steps; 20% also touch ServiceNow at
  about 3 calls each, so 3,000 x 0.2 x 3 = 1,800 ServiceNow calls a day.
- **Permissions:** HR and legal sites use broken inheritance; security will run a restricted-user test
  before go-live.
- **Freshness and deletion:** Slack answers within 15 minutes of a post; legal removals gone from answers
  within 24 hours.
- **Residency:** content copies must stay in the customer's own Azure subscription.
- **Team and deadline:** 2 engineers, 12 weeks (24 engineer-weeks); pilot on the top 50 sites by week 8.

### Options
| Option | How it works | Permission fidelity | Freshness and deletion | Residency | 24 engineer-weeks |
|---|---|---|---|---|---|
| Buy a SaaS connector platform | vendor crawls all sources into its cloud | vendor's model; broken inheritance must be proven | vendor's sync cadence | fails if the shortlisted vendor is SaaS-only | fits easily |
| Build sync for all four | own connectors, one ACL-aware index | full control | delta cursors plus tombstones | passes | tight: ServiceNow scripted ACLs are hard to replicate |
| Federated live search | agent queries each source's search API as the user | source enforces it natively | current | passes, no copy | fits, but 3 sources per step, no shared ranking |
| Sync prose, live tools for records | build SharePoint, Confluence, Slack sync; ServiceNow as delegated live tools | full control on prose; ServiceNow checks itself | delta plus Events API | passes | fits |

Backfill arithmetic for the build options, at an illustrative 10 requests per second for SharePoint:
- **Per-file permissions:** 1.2M x 2 = 2.4M calls, 240,000 s, about 2.8 days; with 20% retried after 429,
  2.88M calls, about 3.3 days.
- **Per-folder permissions where inheritance is intact:** 1.2M + 30,000 = 1.23M calls, about 1.4 days.
- **Confluence at an illustrative 5 requests per second:** 80,000 x 2 = 160,000 calls, about 8.9 hours.

### Decision
**Sync prose into an index in the customer's subscription, and expose ServiceNow as live tools called with
the user's delegated identity.** It is the only option that passes residency, proves broken-inheritance
handling to security, and fits 24 engineer-weeks.
- **Why not buy:** the residency constraint removes the SaaS-only shortlist; a self-hosted platform would
  flip this.
- **Why not build all four:** replicating ServiceNow's scripted per-field ACLs is the riskiest work and buys
  nothing, because records must be current and writes must hit the source anyway.
- **Why not federated:** every retrieval step waits on three source APIs (about 3,000 x 2 x 3 = 18,000
  search calls a day), and results from three engines cannot be ranked together.
- **Order:** crawl the top 50 sites first with per-folder permissions; the full SharePoint backfill of
  about 1.4 to 3.3 days fits well before week 8.

**What would change it.** A connector vendor that deploys into the customer's subscription and passes the
restricted-user test makes buying cheaper. A corpus small enough that source search is good enough, with
latency budgets that allow three calls per step, makes federated search viable.

### Trade-offs accepted
- **A second copy to keep consistent.** Watch sync lag per source and the restricted-user canary's
  permission-mismatch rate; any mismatch is a P1.
- **Permission changes lag one cycle.** Run permission sync on its own faster job (Slack membership every
  15 minutes) and alert on permission-sync lag.
- **Deletions depend on tombstones.** Back delta deletes with weekly reconciliation; a non-zero count of
  reconciliation deletes means the delta path is missing removals.
- **ServiceNow quota shared with other integrations.** Cap calls per agent session (say 10) and alert when
  the agent's share of the instance's API usage passes the agreed slice.

### Review rubric
- **Constraints before tools:** the write-up states corpus size, permissions, freshness, deletion SLA,
  residency, team and deadline before naming an approach.
- **Sync versus live per source:** each source is placed as corpus or tool with a reason (prose versus
  records and writes).
- **Arithmetic shown:** backfill calls and days follow from item counts, calls per item and a labelled
  illustrative rate, including retry overhead.
- **Permission plan testable:** names how broken inheritance, sharing links and unresolved principals are
  handled, and the restricted-user canary that proves it.
- **Deletion and freshness owned:** a mechanism and a metric for each stated SLA.
- **Reversibility stated:** what evidence would switch the choice to buy or federated search.

## Why it exists
- **Knowledge is scattered.** Enterprise knowledge lives in a dozen SaaS and on-prem systems (Salesforce,
  SAP, ServiceNow, SharePoint, Confluence, Jira, Slack, Teams), each with its own API, object model, query
  language, auth scheme and permission semantics.
- **Reach decides value.** A RAG assistant or agent is only as useful as the systems it can see, so "can it
  read our Confluence and Jira?" is usually the first question in a deal.
- **The naive script demos well and fails in production.** Authenticate with an admin token, page through
  everything, dump text into a vector store. It fails for four reasons:
  1. it reads with admin rights and discards source ACLs, so a junior employee gets a page from a
     restricted HR space;
  2. nightly full re-fetches burn API quotas the customer's other integrations also depend on, and get
     throttled;
  3. it never notices deletions, so documents removed for legal reasons keep surfacing;
  4. it hard-codes one tenant's quirks, so the second customer breaks it.
- **A connector is the disciplined version.** A repeatable contract for auth, enumeration, change
  tracking, permissions and normalization that every source implements, so every agent tool on top of it
  behaves the same way.

## How it works
### The connector contract
Every connector implements the same five responsibilities, *because* the downstream pipeline (chunking,
embedding, ACL-aware retrieval, tool calling) should not care where a document came from:

1. **Authenticate** as an app registered in the customer's tenant (OAuth client credentials, a service
   account, or delegated user tokens), with the narrowest scopes that work.
2. **Enumerate** the containers in scope (sites, spaces, projects, channels, objects) and page through
   items, honouring pagination cursors and rate-limit responses (HTTP 429, `Retry-After`).
3. **Track change**: store a cursor (delta token, change-event replay ID, or `updated` watermark) so the
   next run fetches only what changed, including deletions.
4. **Capture permissions** alongside content: who can see this item, expressed as source principals that
   can later be resolved to users.
5. **Normalize** into a canonical document (id, source URL, title, body, author, timestamps, ACL, source
   metadata) so one indexing pipeline serves all sources.

### Two shapes an agent consumes
| Shape | What the agent calls | Freshness | Permission enforcement |
|---|---|---|---|
| Synced corpus | A `search_docs` retrieval tool over your index | One sync cycle behind | ACL filter on stored principals |
| Live tool | An action or lookup tool calling the source API | Current | The source's own checks, if called as the user |

- **Prose goes in the corpus** (Confluence pages, SharePoint files, Slack threads), because agents need
  ranked semantic search across them.
- **Records and actions go behind tools** (SAP orders, ServiceNow incidents, creating Jira issues), because
  values change constantly and writes must hit the source.

### What differs per system
| System | Access and query | Change tracking | Permission model |
|---|---|---|---|
| Salesforce | SOQL over REST; Bulk API for large extracts, because per-record REST calls burn the org's API allocation | Change Data Capture events or `SystemModstamp` watermarks | Profiles, permission sets, role hierarchy, sharing rules |
| SAP | OData services (S/4HANA, SAP Gateway), RFC/BAPI calls, IDoc messages for async document exchange | Usually live calls rather than a synced corpus | Fine-grained authorization objects, enforced by calling SAP as the user |
| ServiceNow | Table API (incidents, knowledge articles, CMDB items) with encoded queries | Record update timestamps | ACLs scripted per table and field |
| SharePoint, Teams | Microsoft Graph | Drive and list items support delta queries returning changes and deletions since a token | Per site, library, folder and item, inheritance breakable at any level |
| Confluence, Jira | Atlassian REST with CQL and JQL | Query by `lastmodified` or `updated` | Space permissions plus page restrictions inheriting down the tree; Jira project permission schemes plus optional issue security levels |
| Slack | Web API (`conversations.history`, `conversations.replies`) for backfill | Events API for live changes | Channel membership, which changes often |

Per-system caveats that change the agent design:
- **Salesforce visibility is too complex to replicate faithfully**, so many connectors restrict scope to
  objects with simple sharing or check access at query time.
- **SAP data is structured transactional records, not prose**, so the connector is more often a tool an
  agent calls (look up an order, check stock) than a corpus to embed.
- **A ServiceNow service account's view is not a user's view**, because ACLs are scripted per table and field.
- **Teams messages may need approval.** Channel messages are in Graph, but some message-export APIs are
  classed as protected and require an approval or licensing step, so check before promising them.
- **Slack limits vary by app type.** It uses method-specific rate-limit tiers and has tightened
  history-read limits for some classes of apps (the 2025 changes for apps not distributed through its
  Marketplace). Check current limits before sizing a backfill. Private channels and DMs are visible only
  to apps added or granted the right scopes.

### From source principals to people
- **Each ACL is in the source's own vocabulary.** SharePoint and Teams use Entra object IDs plus SharePoint
  site groups; Confluence and Jira use Atlassian account IDs and groups; Slack uses its own user IDs and
  channel membership; Salesforce uses user IDs, roles and public groups. None is the sign-in identity.
- **Maintain a principal crosswalk.** Map source principal ID to the IdP's immutable user or group ID,
  built from each source's user directory API. Join on the IdP object ID where the source records it
  (Microsoft sources do), or on a verified email where it does not.
- **Expand groups.** Source-local groups are expanded to members, nested groups recursively, *because* the
  retrieval filter can only test identities it knows.
- **Model the special cases explicitly.** Sharing links ("anyone in the organization with the link",
  anonymous links) grant access to a scope, not a listed principal. Guest or external users have no IdP
  account. Neither may be dropped.
- **Fail closed.** A principal that resolves to nobody grants nothing; it must never be ignored in a way
  that widens access, because the agent will happily quote whatever retrieval returns.

### Sizing the first crawl
The initial backfill, not the steady state, decides when the agent pilot can start.

| Input (illustrative SharePoint tenant) | Value |
|---|---|
| Files | 2 million |
| Calls per file | 1 content download + 1 permissions call |
| Total requests | about 4 million |
| Sustained rate (illustrative; real one depends on throttling for your app and tenant) | 10 requests per second |
| Duration | 400,000 seconds, roughly four and a half days, plus every retry after a 429 |

Levers that follow from the arithmetic:
- **Batch where the API allows it.** Graph JSON batching groups up to 20 requests per call, though
  throttling still counts the individual requests.
- **Read permissions once per folder** whose inheritance is intact, instead of once per file.
- **Crawl the highest-value containers first**, so the pilot agent is useful before the crawl finishes.
- **Make the crawl resumable** by checkpointing per container.
- **Expect steady state to be cheap.** After the backfill, cost scales with the change rate, typically a
  small fraction of the corpus per day.

### Build, buy or use MCP
- **Buy** when unified-API vendors or the AI platforms' own connector catalogues cover the source.
- **Build** when you need permissions, fields or freshness they do not provide.
- **MCP for agent actions.** Wrapping each system as an MCP server gives a standard tool interface, but it
  does not remove any of the auth, quota or permission work above.

## Diagram
Every source runs the same five-step contract; only the cursor and the ACL vocabulary differ. The agent's
retrieval tool reads the ACL-aware index at the bottom.

```mermaid
flowchart TD
    S["Source system API"] --> A["1. Authenticate as tenant app"]
    A --> E["2. Enumerate and page"]
    E -->|"HTTP 429"| B["Back off per Retry-After"]
    B --> E
    E --> C["3. Changes since cursor"]
    C -->|"tombstone"| DEL["Delete from index"]
    C -->|"new or changed"| P["4. Capture source ACL"]
    P --> X["Crosswalk to IdP IDs"]
    X --> N["5. Normalize canonical doc"]
    N --> I[("ACL-aware index")]
    I --> T["Agent search tool, filtered per user"]
    C -.->|"persist cursor last"| K[("Cursor store")]
```

## Examples
### SharePoint library sync with Graph delta queries
The first call returns every item; later calls with the saved `deltaLink` return only changes, and
deleted items carry a `deleted` facet, which is how the connector removes them from the agent's index.

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

What the code commits to:
- **One permissions call per changed file.** Fine for incremental runs, expensive for a 2-million-file
  initial crawl, which is where batching and a separate permission-sync job come in. The permissions call
  also needs the same 429 handling as the delta call, omitted here for length.
- **Ordering makes it crash-safe.** The `deltaLink` is persisted only after the last page is processed, so
  a crash mid-round replays the round from the previous token. Upserts and deletes are idempotent by key,
  so replaying them is harmless.
- **Sharing changes are not fully reported.** By default a delta round surfaces content and metadata
  changes; a sharing change on a folder does not list every descendant whose effective access changed.
  Graph has request options for reporting sharing changes on business drives; without them, or in
  addition, run a periodic permission resync.

### The agent's retrieval tool over that index
The tool the model calls never takes a user ID as an argument; the runtime injects the authenticated
caller's resolved principals, so a prompt injection cannot ask for someone else's documents.

```python
SEARCH_TOOL = {
    "name": "search_company_docs",
    "description": "Search SharePoint, Confluence and Slack content the current user may see.",
    "input_schema": {"type": "object",
                     "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
                     "required": ["query"]},
}

def run_search_tool(args: dict, session, index, crosswalk) -> list[dict]:
    principals = crosswalk.principals_for(session.idp_user_id)   # user + expanded groups + link scopes
    if not principals:
        return []                                                # fail closed: unresolved user sees nothing
    hits = index.search(args["query"], top_k=min(args.get("top_k", 8), 20),
                        filter={"acl_any": principals})          # filter BEFORE ranking, not after
    return [{"title": h["title"], "url": h["url"], "modified": h["modified"],
             "snippet": h["text"][:500]} for h in hits]          # URLs let the agent cite sources
```

### Backfill planner: will the pilot be ready in time?
Plug in the item count, calls per item and the rate the source really gives you.

```python
def backfill_days(items, calls_per_item, rps, retry_overhead=0.0, perms_per_folder=None):
    """Days for an initial crawl. perms_per_folder: folder count if permissions are read per folder."""
    calls = items * 1                                   # content download per item
    calls += perms_per_folder if perms_per_folder else items * (calls_per_item - 1)
    seconds = calls * (1 + retry_overhead) / rps
    return round(seconds / 86_400, 1)

print(backfill_days(2_000_000, 2, 10))                        # 4.6  per-file permissions
print(backfill_days(2_000_000, 2, 10, retry_overhead=0.2))    # 5.6  with 20% retried after 429
print(backfill_days(2_000_000, 2, 10, perms_per_folder=50_000))  # 2.4  inheritance intact per folder
```

| Plan | Requests | Days at 10 req/s |
|---|---|---|
| Content + permissions per file | 4,000,000 | 4.6 |
| Same, 20% of calls retried after 429 | 4,800,000 | 5.6 |
| Permissions once per folder (50,000 folders, illustrative) | 2,050,000 | 2.4 |

The per-folder plan only holds where inheritance is intact; folders with broken inheritance still need
per-item reads.

### Worked scenario: a sales agent eating the Salesforce quota
An account-research agent calls a live `get_account` tool and a `list_opportunities` tool. Numbers are
illustrative; check the org's real daily allocation in its limits endpoint.

| Quantity | Value |
|---|---|
| Agent sessions per day | 2,000 |
| Steps per session (median) | 6 |
| Salesforce calls per step (tool plus pagination) | 2 |
| Calls per day | 24,000 |
| Same agent with a retry-loop bug in the worst 1% of sessions (40 steps) | about 1,400 extra calls a day from just 20 sessions (20 × 34 extra steps × 2) |

The fix was a per-session tool budget (stop after N Salesforce calls and say so), caching account
lookups for the session, and alerting when the agent's share of the org's API usage passed the agreed slice.

## Production patterns
- **Persist the cursor after the batch commits.** For every container, write items first and the delta
  token or watermark last, in the same job step. Monitor sync lag (source change to searchable) and the age
  of each container's cursor.
- **Run permission sync on its own, faster cadence.** A separate job re-reads ACLs and group membership
  (Slack channels, Entra groups) more often than content, because access changes matter more than edits.
  Monitor permission-sync lag and unresolved-principal count.
- **Budget the customer's quota explicitly, crawler and agent together.** Agree a share of the API
  allocation with the customer's admins, throttle the crawler to it, cap live tool calls per agent session,
  and honour `Retry-After` on every call. Salesforce reports org API usage in a response header and a limits
  endpoint; Graph returns 429 with `Retry-After`. Monitor 429 rate and calls per sync and per agent session.
- **Inject identity in the runtime, not the prompt.** Retrieval and action tools take the caller's
  identity from the authenticated session, never from model-supplied arguments, so injected text cannot
  widen access. Monitor tool calls rejected for missing identity.
- **Canary as a restricted test user.** Keep test users with known narrow access per source and run
  scheduled agent queries as them; any hit on a document they cannot open in the source is a P1. Monitor
  the permission-mismatch rate.
- **Reconcile deletions periodically.** Even with tombstones, list all live IDs per container on a slow
  schedule (weekly, say) and delete index entries the source no longer has. Monitor items deleted by
  reconciliation; a non-zero trend means the delta path is missing deletes.

## Trade-offs
- **Copy-and-index vs federated live search.** Your own index gives the agent fast, rankable retrieval but
  creates a second copy whose permissions and freshness you must keep in sync. Federated search through
  source APIs avoids the copy and flips the cost to latency per agent step and per-query rate limits.
- **Service accounts vs delegated tokens.** Application-level service accounts make crawling simple but
  see more than any user, so you must replicate ACLs. Delegated user tokens enforce source permissions
  natively on agent actions but see only one user's slice and stop working when the refresh token or
  consent is revoked.
- **Buy vs build.** A connector platform gets twenty sources quickly, but you inherit its permission
  fidelity and sync cadence. Building wins when one source is business-critical and needs field-level control.
- **Index-time vs query-time permissions.** Syncing permissions at index time makes agent retrieval cheap,
  but changes lag by one sync cycle. Checking access live is exact but adds a source API call per candidate
  document, multiplied by every retrieval step the agent takes.

## Pitfalls
- **Crawling with an admin token and dropping ACLs.** Symptom: a user reports the assistant quoted a
  document they cannot open in the source system. Fix: capture ACLs at sync and filter per user.
- **Nightly full re-fetches instead of delta cursors.** Symptom: HTTP 429s, and the customer's other
  integrations fail during your crawl window. Fix: delta tokens or change events.
- **Ignoring deletions and permission removals.** Symptom: retrieval returns links that 404 or show
  "access denied" in the source, and the agent cites them confidently.
- **Treating broken inheritance as rare in SharePoint or Confluence.** Symptom: a few sensitive folders
  indexed with their parent site's wider audience.
- **Joining users by display name or unverified email.** Symptom: ACL entries resolve to nobody (users see
  less than in the source) or to the wrong person after a name collision.
- **Dropping sharing-link permissions because they name no principal.** Symptom: documents shared "with
  the organization" are invisible to everyone, or, if mapped carelessly, visible to guests.
- **Letting the model pass the user or tenant as a tool argument.** Symptom: a red-team prompt retrieves
  another user's documents. Fix: bind identity in the tool runtime from the session.
- **Promising a source before checking scope, approval or licence.** Teams messages, Slack DMs or a SAP
  module can need extra steps. Symptom: a blocked project in week three.

## When to use
- **Use when** an AI feature or agent must read or act on a customer system more than once.
- **Pilot shortcut:** for a one-off pilot, an export file may be enough.
- **Production minimum:** delta sync, deletion handling and permission capture from day one, because
  retrofitting ACLs onto a populated index means re-crawling it.
- **Sync or tool:** sync prose the agent searches; call live for records it reads exactly or writes.
- **Size before committing:** item count times calls per item divided by the sustained rate the source
  will actually give you.
- **Measure:** sync lag (source change to searchable), API calls per sync and per agent session, share of
  the customer's quota, 429 rate, permission-mismatch rate from spot checks as restricted test users,
  unresolved-principal count, and items in error.
- **Interviews:** name the five responsibilities and explain how one specific source models permissions
  and change.

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

## Interview questions
### What are the core responsibilities of an enterprise connector, and why standardise them?
- **Five jobs:** authenticate as a tenant app, enumerate and page with backoff, track change with a cursor,
  capture permissions, and normalize into a canonical document.
- **Why standardise:** chunking, embedding, ACL-aware retrieval and tool calling then work identically for
  every source, so adding the ninth source is a connector, not a pipeline rewrite.

### Your first SharePoint crawl for a 2-million-file tenant is projected at five days. How do you shorten it?
- **Do the arithmetic first:** about 4 million calls at the sustained rate the tenant gives you, plus 429
  retries.
- **Cut calls:** read permissions per folder where inheritance is intact, and use Graph JSON batching where
  it helps, remembering throttling still counts each inner request.
- **Change the order, not just the speed:** crawl high-value sites first and checkpoint per container so a
  crash resumes instead of restarting.

### A user says the agent quoted a document they cannot open. How do you debug it?
- **Find the chunk and its stored ACL** from the agent trace's retrieval step, then compare with the item's
  current permissions in the source.
- **Common causes:** admin-token crawl with no ACL, broken inheritance indexed with the parent's audience, a
  sharing link mapped too widely, a permission removal not yet synced, or a tool that trusted a
  model-supplied user argument.
- **Fix the class, not the item:** add a restricted-user canary and a faster permission-sync job.

### When would you check permissions live at query time instead of syncing them?
- **When the model is too complex to replicate**, as with Salesforce sharing rules or ServiceNow scripted
  ACLs, or SAP authorization objects.
- **When membership churns fast**, as with Slack private channels.
- **Cost:** a source API call per candidate document, so it suits small candidate sets and sources with
  generous rate limits.

### Does wrapping a source as an MCP server solve integration for your agent?
- **It standardises the tool interface** an agent sees, which helps reuse across agents and hosts.
- **It does not remove** OAuth app registration, scopes, rate limits, change tracking or permission
  enforcement; the MCP server still has to implement all of them.

### Which customer systems would you sync for the agent and which would you expose as live tools?
- **Sync prose** that needs semantic search across many items: Confluence, SharePoint, Slack threads.
- **Expose as tools** records that must be exact and current or that the agent writes: SAP orders,
  ServiceNow incidents, Salesforce opportunities, Jira issue creation.
- **Guard the tools** with per-session call budgets and the user's own identity, because a looping agent
  can drain a shared API quota in minutes.

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

## Use cases & projects
### Use cases
- **Internal knowledge agents.** An employee assistant answers from Confluence, SharePoint and Slack with
  per-user ACL filtering and citations back to the source.
- **Service desk agents.** A ServiceNow-connected agent reads knowledge articles from the index and opens
  or updates incidents through a live tool.
- **Sales and account research.** An agent combines synced call notes with live Salesforce lookups,
  under a per-session API budget.
- **Order and inventory agents.** SAP exposed as OData lookup tools, called as the user so SAP's
  authorization objects apply.

### Mini project
**Permission-aware Confluence search tool for an agent (2-3 hours).**
- **Goal:** give an agent a `search_company_docs` tool that only returns pages the caller may read.
- **Steps:** sync one Confluence space (or a fixture of 50 pages with fake restrictions) with an `updated`
  watermark; store each page's allowed principals; build the tool with identity injected from the session;
  run the same agent question as two test users with different access.
- **Done when:** the restricted user's trace shows zero hits from the restricted pages, a deleted page
  disappears after the next sync, and a re-run from the saved watermark fetches only changed pages.
