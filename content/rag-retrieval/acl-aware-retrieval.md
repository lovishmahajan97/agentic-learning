---
id: acl-aware-retrieval
module: rag-retrieval
name: ACL-aware retrieval
kind: pattern
difficulty: advanced
est_minutes: 16
status: reviewed
related:
  - rag-retrieval/metadata-filtering
  - safety-security/cross-tenant-data-leakage
  - backend-fundamentals/authentication-vs-authorization
  - enterprise-integration/rbac-abac-mapping-to-customer-identity
  - system-design/tenant-isolation
  - agents/mcp-authentication-authorization-transport-security
  - scoping/data-access-and-permissions-discovery
  - rag-retrieval/freshness-document-deletion-index-consistency
sources:
  - Official docs: Elasticsearch document level security
  - Official docs: Qdrant multitenancy
  - Official docs: Microsoft Graph permissions and SharePoint sharing model
---

## TL;DR
An enterprise index flattens documents with very different permissions into one searchable bag, yet
each answer must draw only on documents the asking user could open themselves. ACL-aware retrieval turns
the authenticated identity into a hard predicate over each chunk's allowed principals inside the search,
because the model cannot be trusted to withhold what it has already read and filtering after generation
is too late. The hard parts are capturing permissions at ingestion, expanding groups at query time, and
keeping both fresh.

## Why it exists
A company deploys RAG over SharePoint, Confluence and Google Drive. The crawler runs as a service account
that can read everything, because it has to in order to index everything. An intern asks "what is the
CFO's compensation?", the top-k returns a chunk of a board deck, and the model summarizes it politely. The
source systems enforced that permission for years; the index threw it away in one afternoon. That is not
an edge case, it is the default outcome of building a RAG system without an authorization model.

The naive defenses all fail in instructive ways. Telling the model "do not reveal documents the user is
not allowed to see" fails because the model does not know the user's permissions, has already consumed
the text, and follows instructions only probabilistically; confidentiality by prompt is not a control.
Scanning the generated answer for restricted content fails because the content has already shaped the
answer, and even "there is a document about that, but I cannot share it" leaks existence. Filtering the
retrieved candidates after the search inherits the selectivity problem from metadata filtering, so
low-privilege users, who are most of the users, get empty results. Worse, it is a per-call-site
discipline: the debug endpoint, the eval script, the agent's tool and the semantic cache each have to
remember to filter, and the one that forgets is the breach.

The fix is to make authorization a property of the retrieval query itself: derived from authenticated
identity, enforced in one place, applied inside the search, and failing closed.

## How it works
### 1. Capture permissions at ingestion
Every source has a permission model: SharePoint sites, libraries and items with users and groups;
Google Drive per-file sharing to users, groups, the whole domain or anyone with the link; Confluence space
permissions plus page restrictions; Slack channel membership; databases with row-level security. The
connector must read the ACL alongside the content and translate it into one canonical principal
vocabulary, for example `user:alice@acme.com`, `group:<id>`, `domain:acme.com`, `public`. It must
materialize the *effective* permission set rather than the inheritance chain (folder grants roll down),
because the query-time predicate cannot walk chains. Chunks inherit their document's ACL; store the
principals on every chunk payload so the predicate is a single field match rather than a join the vector
database cannot do. If the source has explicit denies, store a `denied` list too.

Store groups, not their members. A document shared with a 50,000-person group should carry one
principal, not 50,000; membership changes then never touch the index.

### 2. Expand the user's principals at query time
Authenticated user → identity provider (Entra ID, Okta, Google Workspace) → the user's transitive group
memberships, including nested groups. This expansion is often dozens to a few hundred principals; cache
it with a short TTL (minutes) so a removal propagates quickly without a directory call per query. Add the
built-ins: the user's own id, their domain, and `public`.

### 3. The predicate is a set intersection
A chunk is visible when `allowed ∩ principals ≠ ∅` and `denied ∩ principals = ∅`. In a vector database
that is `must: match_any(allowed, principals)` plus `must_not: match_any(denied, principals)` over a
keyword-indexed array field; in Elasticsearch it is a `terms` filter or document-level security roles.
It is applied as a pre-filter or filtered traversal (metadata filtering lesson), identically in every arm
of hybrid search and before reranking, so no ranking stage ever scores a chunk the user cannot see.

### 4. Early binding versus late binding
*Early binding* indexes the permissions with the content, as above. It is fast and scales, but it is
only as fresh as the last ACL sync: a user removed from a group at 09:00 keeps seeing results until the
payloads are updated. *Late binding* asks the source system at query time whether this user can read
each candidate. It is always correct, but it costs one source-API call per candidate, hits rate limits,
and cannot be used as the only filter because of the post-filter selectivity problem.

The practical design is a hybrid: early-bound predicate for retrieval, then a late-bound check on the
final top-k when the data warrants it. The check is cheap because k is small (5 to 10 documents) and many
sources accept batched permission queries. Alongside it, subscribe to the source's change feed (webhooks,
delta queries) to update ACL payloads without re-embedding, and run a periodic full re-sync to catch
missed events. Because groups are expanded at query time, group-membership changes take effect within the
principal-cache TTL (plus any propagation delay in the identity provider itself); the sync lag only
applies to direct shares and new restrictions.

### 5. Revocation and deletion
A document that becomes restricted or is deleted must disappear from results for everyone immediately:
tombstone or delete all its chunks by `doc_id`. This is the same machinery as index freshness, with a
stricter SLO.

### 6. One enforcement point, failing closed
The retrieval service accepts an authenticated identity and computes the principal set itself. It never
accepts a principal list or a raw filter from the caller, and if the principal set is empty or the
directory lookup fails it returns nothing, not everything. Agents and MCP servers call retrieval with the
end user's identity (on-behalf-of tokens), not a service account, so a tool call cannot escalate
privileges on the user's behalf.

### 7. Side channels
Result counts, "related documents", autocomplete suggestions, snippets in logs, eval dumps, the answers
cached by a semantic cache keyed on the query alone, summaries an agent stores into shared memory, and
LLM-generated documents written back into the index all carry content and must be scoped by the same
principals. A semantic cache must key on (principal-set hash, query) or cache only public results.
Responses for "not found" and "forbidden" should be indistinguishable.

### 8. Derived content inherits the most restrictive ACL
Anything the pipeline writes *from* several documents (a parent-section summary, a contextual-retrieval
prefix, a GraphRAG community report, a knowledge-graph entity description, an agent's memory note) may
only be shown to someone who could read every input, because a summary of a board deck and a public FAQ
leaks the board deck. The tempting shortcut, intersecting the two `allowed` lists, is wrong when they
hold groups: `[group:finance] ∩ [group:legal]` is empty as strings, yet a user in both groups may read
both sources. Store the derived chunk's ACL as a conjunction instead (the list of source ACLs, each of
which the user's principals must match, enforced with nested filters or a late-bound check) or, more simply, only build derived artifacts within one permission domain, for
example per site or per space. Either way the derived item must be rebuilt or tombstoned when any source
ACL tightens.

## Example
Ingestion stores effective principals on each chunk; query time expands the user's groups, builds the
predicate, runs a filtered search and late-binds a check on the survivors. The vector-store calls follow
the Qdrant client's shape; the identity and source clients are the customer's directory and document
system.

```python
from qdrant_client import QdrantClient, models

client = QdrantClient("http://localhost:6333")
# Ingestion wrote, per chunk: payload={"doc_id": "d17", "allowed": ["group:finance", "user:cfo@acme.com"],
#                                     "denied": []}
client.create_payload_index("docs", field_name="allowed", field_schema="keyword")
client.create_payload_index("docs", field_name="denied", field_schema="keyword")

def principals_for(user, directory):
    """Authenticated identity -> transitive groups. Cache for a few minutes; never trust the client."""
    groups = directory.transitive_groups(user.id)                 # e.g. 60 group ids, nested included
    return [f"user:{user.email}", f"domain:{user.domain}", "public", *[f"group:{g}" for g in groups]]

def retrieve(user, query_vec, directory, source, k=8):
    try:
        who = principals_for(user, directory)
    except Exception:                                             # directory down: fail closed, never open
        return []
    flt = models.Filter(
        must=[models.FieldCondition(key="allowed", match=models.MatchAny(any=who))],
        must_not=[models.FieldCondition(key="denied", match=models.MatchAny(any=who))],
    )
    # Fetch a little extra: the late-bound check below may drop a few stale grants.
    hits = client.query_points("docs", query=query_vec, query_filter=flt, limit=2 * k).points
    # Late binding on the few survivors closes the window between ACL syncs for direct shares.
    doc_ids = {h.payload["doc_id"] for h in hits}
    readable = source.can_read(user.id, doc_ids)                  # one batched permission check
    return [h for h in hits if h.payload["doc_id"] in readable][:k]
```

Worked numbers: with 2 million chunks and a user in 60 groups, the predicate is a 63-value `match_any`
against a keyword index, which is a bitmap union and costs about the same as any other payload filter.
If a nightly ACL sync at 02:00 is the only refresh, a direct share revoked at 10:00 stays visible for 16
hours (up to 24 hours for a revocation made just after the sync) without the late-binding check; with it,
the window closes at the next query.

## Trade-offs
- Early binding is fast and scales to millions of chunks, but freshness is bounded by ACL sync lag; it
  flips to unacceptable when revocations must be immediate (HR, legal, M&A), which is when you add
  late-binding checks on the top-k.
- Late binding is always correct, but it costs per-query calls to rate-limited source APIs and cannot
  stand alone when a user can see only a sliver of the corpus; it flips to impractical at high query
  volume without batching and caching.
- Storing effective principals on every chunk duplicates ACL data (storage, update fan-out per document)
  but turns the predicate into one indexed field; normalizing into a document table would need a join
  the vector store cannot perform.
- A shared index with an ACL predicate is simpler and cheaper than an index per security domain, which
  gives blast-radius containment; the flip comes when a tenant demands physical isolation or data
  residency.
- Expanding groups at query time costs a directory round trip (cached) but makes membership changes
  instant; expanding at index time (storing users) removes the round trip but forces a re-index on every
  membership change.

## Pitfalls
- Crawling with a service account and never capturing ACLs; the symptom is the first security review, or
  the first user who finds payroll data in a chat answer.
- Accepting principal lists or filters from the client or from an agent's tool call; the symptom is a
  trivially forged request body that reads everything.
- Post-filtering candidates after an unfiltered ANN search; the symptom is low-privilege users getting
  empty answers to questions whose sources they are allowed to read.
- Storing individual users on widely shared documents; the symptom is payloads with tens of thousands of
  entries and re-index jobs triggered by routine membership changes.
- A semantic or response cache keyed on the query text alone; the symptom is user B receiving user A's
  cached answer that drew on a restricted document.
- Debug, eval and agent code paths that query the index directly; the symptom is a log line showing a
  "test" endpoint returning documents with no principal set attached.
- No change feed and no late-binding check; the symptom is an offboarded employee's direct shares still
  surfacing hours later, found during an audit.
- Treating "not in the index" and "not permitted" differently in the response; the symptom is users
  discovering the existence of documents by probing.
- Taking group memberships from the sign-in token's group claim; identity providers cap or omit that
  claim for users in many groups (Entra ID switches to an "overage" indicator), so the symptom is
  heavily grouped users, often managers and admins, suddenly seeing far less than they should.
- Summaries, contextual prefixes or graph reports built across documents with different ACLs; the
  symptom is a restricted fact surfacing in a chunk whose own `allowed` list looks harmless.

## When to use
Any RAG system over data with per-user permissions needs this, which in practice means almost every
enterprise deployment; the only exemption is a corpus that is uniformly public to every user of the app,
and even then tenant isolation still applies. Decide early, because retrofitting ACL capture into a
connector is a rewrite. Measure ACL sync lag (p95 time from a permission change to the index reflecting
it), leakage tests (seed a restricted document and run red-team queries as a low-privilege test user in
CI), filtered recall for low-privilege users, and the latency overhead of principal expansion. In
interviews, expect to be asked how a revoked permission propagates, how groups are expanded, where
enforcement lives, and which side channels you would close; early versus late binding is the vocabulary
the strong answer uses.

## Role lens
- **Engineer:** Builds connector-side ACL capture, cached principal expansion and the filter builder, and
  writes leakage tests that run restricted-document probes as low-privilege users in CI.
- **Architect:** Owns the authorization boundary: end-to-end identity propagation with on-behalf-of
  tokens, a single enforcement point, the ACL freshness SLO, and which data classes require late-binding
  checks.
- **FDE:** Discovers the customer's permission model in the first week (nested groups, external shares,
  site-level versus item-level grants), secures read access to ACL metadata, and proves to the security
  team with a live demo that a low-privilege account cannot surface restricted content.

## Check yourself
**Q:** A customer wants a chat assistant over Google Drive. The connector runs as a Workspace admin. A
document is shared with a 2,000-member group; a user is removed from that group at 10:00, and the ACL
re-sync runs nightly at 02:00. Design retrieval so the user cannot see the document at 10:05, and state
what it costs.
**A:** Store `group:<id>` on the chunks rather than the 2,000 members, and expand the user's groups at
query time from the Workspace directory with a cache TTL shorter than the five-minute window (or
invalidate the cache on directory change events). At 10:05 the user's principal set no longer contains
the group, so the predicate excludes the document with no re-index at all. Direct
per-user shares stay early-bound and would be stale until 02:00, so cover them two ways: subscribe to
Drive change notifications and patch the `allowed` payload on permission changes, and run a late-binding
permission check on the final top-k before answering. Costs: one cached directory call per user per TTL,
one batched permission check on at most k documents per query, and the engineering of the change feed.
Fail closed if the directory lookup fails, and key any answer cache on the principal set.

## Quiz
### Q: Why is instructing the model "do not reveal documents the user cannot access" not a security control?
- [ ] Models cannot follow negative instructions reliably
- [x] The model does not know the user's permissions, has already read the content, and instructions can be bypassed
- [ ] System prompts are not sent to the model with retrieved chunks
- [ ] It works, but only for models with large context windows
> Authorization must be decided before content reaches the model, from authenticated identity. Once a
> restricted chunk is in the context it shapes the answer, and prompt instructions are probabilistic.

### Q: Which statement about early and late binding of ACLs is correct?
- [ ] Late binding indexes permissions with the content and is therefore faster
- [x] Early binding indexes permissions with the content and is fast but stale until re-sync; late binding checks the source per query and is fresh but expensive
- [ ] Early binding requires calling the source system for every candidate
- [ ] They are equivalent as long as the index is rebuilt weekly
> Early binding makes the permission a payload field evaluated inside the search; late binding asks the
> source at query time. Production systems combine them: filter early, verify the few survivors late.

### Q: A document is shared with a 10,000-member group. How should its ACL be stored in the index?
- [ ] List all 10,000 user ids on each chunk so lookups are exact
- [x] Store the group principal on the chunks and expand the user's group memberships at query time
- [ ] Store nothing and rely on the source system to filter the answer afterwards
- [ ] Embed the group name into the chunk text so the embedding carries it
> Group principals keep payloads small and make membership changes instant through query-time
> expansion. Listing users forces a re-index on every change, and embeddings cannot enforce a predicate.
