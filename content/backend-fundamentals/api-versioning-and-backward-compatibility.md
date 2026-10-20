---
id: api-versioning-and-backward-compatibility
module: backend-fundamentals
name: API versioning & backward compatibility
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - backend-fundamentals/rest-graphql-api-design
  - fastapi-for-ai/openapi-contract-testing
  - fastapi-for-ai/routers
  - python-for-ai/contract-testing
  - data-architecture/data-contracts
  - llmops-platform/model-lifecycle-and-deprecation-policy
  - prompting/prompt-versioning
  - agent-runtime-architecture/cross-language-api-contracts
sources:
  - IETF RFC 8594 — The Sunset HTTP Header Field
  - Official docs: Stripe API versioning
  - Official docs: Anthropic API versions
  - Winters, Manshreck & Wright, 2020 — Software Engineering at Google (Hyrum's Law)
---

## TL;DR
Once someone else's code depends on your API, every change is a negotiation with clients you cannot
redeploy. Backward compatibility means additive changes by default (new optional fields, new
endpoints) and tolerant readers on both sides; versioning is the escape hatch for the rare breaking
change, expressed in the path, a header or a pinned date, with an announced deprecation and sunset.
In AI systems the same discipline covers model snapshots, prompt versions and tool schemas.

## Why it exists
The clients of an API are distributed in time as well as space: a mobile app that users never update,
a partner integration built by a contractor who left, an SDK pinned in a lockfile, an agent whose
system prompt describes your tool's arguments. Rename a field and each of them breaks silently at its
own pace, and you cannot roll them back. Hyrum's law states the underlying fact: with enough users,
every observable behaviour of your interface becomes something somebody depends on, documented or not.

The first naive response is "never break anything", which freezes bad decisions forever and bloats
the API with fields kept only for one client. The second is "bump to `/v2` whenever we change
something", which produces version sprawl: each version is a fork to maintain, test and secure, and
clients never migrate because nothing forces them. The third is "be careful", which fails the first
time a well-meaning engineer makes a required field optional in the response and a strict client
starts throwing.

The working discipline sits between these: make additive, compatible changes the normal path so that
versions are rare; when a break is unavoidable, version deliberately, announce a deprecation with
dates, measure who is still on the old version, and remove it on schedule. For AI services the
discipline matters twice over, because a model or prompt change can alter outputs for the same input,
which is also a breaking change from the client's point of view.

## How it works
### What counts as breaking
Breaking for clients: removing or renaming a field or endpoint; changing a field's type or format
(integer to string, seconds to milliseconds); making an optional request field required; tightening
validation; changing a default; changing the meaning of a value; changing status codes or the error
envelope; changing authentication. Usually compatible: adding an optional request field, adding a
response field, adding an endpoint, relaxing validation. Contested: adding an enum value, which breaks
clients that switch exhaustively, so document that enums are open and clients must handle unknowns.

### Tolerant readers
Compatibility is a two-sided contract. Clients should ignore unknown response fields and treat enums
as open; servers should accept missing optional fields and ignore unknown request fields unless the
endpoint is security-sensitive. Pydantic's default of ignoring extra fields is the tolerant choice;
`extra="forbid"` is stricter and turns a client's harmless new field into a 422, so choose it
knowingly. This is Postel's principle applied with judgement, *because* total leniency hides bugs.

### Versioning strategies
- **URL path** (`/v1/...`, as OpenAI does): visible, routable, easy to test and cache; coarse, since
  the whole API moves together.
- **Header** (`Accept: application/vnd.acme.v2+json`, or a custom header such as Anthropic's
  `anthropic-version` date): keeps URLs stable and allows per-request choice; harder to try in a
  browser and easy to forget in a proxy.
- **Pinned date per account** (Stripe's approach): each account is pinned to the version current
  when it first called the API, overridable per request with a `Stripe-Version` header; the server
  applies a chain of transformations to render new behaviour in old shapes. Maximum compatibility, maximum server-side machinery.
- **Query parameter** (`?version=2`): simple but mixes with filters and cache keys.
- **Evolution only** (GraphQL's stance): no versions; add fields, mark old ones `@deprecated`, remove
  after usage reaches zero.

Whichever you choose, keep one implementation and render versions at the edge as representations,
*because* two forked code paths drift and the old one stops getting fixes.

### Deprecation lifecycle
Announce in docs and changelog; emit `Sunset` (RFC 8594, an HTTP date) and a `Link` to the migration
guide on old-version responses, and mark the route deprecated in OpenAPI. RFC 9745 (2025) adds a
`Deprecation` header whose value is a structured-field date (`@` followed by Unix seconds) and a
`deprecation` link relation for the migration guide. Measure requests per
version per client so you know who is left. Run brownouts (short planned outages of the old version)
before removal. Remove on the announced date.

### Data and schema changes: expand and contract
Rename a column or field in three steps: add the new one, write both and read either, migrate
consumers, then remove the old. The same pattern applies to message schemas and tool arguments.

### AI-specific contracts
Model providers retire model versions; pin exact snapshot names and track deprecation notices. A
prompt revision that changes output format is a contract change for everything downstream, so version
prompts alongside code. A tool schema change can break an agent that learned the old arguments from
its prompt or few-shot examples. Re-embedding with a new model invalidates an index built with the old
one, which is versioning applied to data.

## Example
Two versioned routers in FastAPI sharing one implementation. The v2 shape renamed `summary` to
`text` and added citations, a breaking change, so v1 is kept as a thin representation with a `Sunset`
header and an OpenAPI deprecation flag rather than as a forked code path.

```python
from fastapi import APIRouter, FastAPI, Response
from pydantic import BaseModel, ConfigDict

class SummaryV2(BaseModel):             # current shape: renamed field, structured citations
    id: str
    text: str
    citations: list[dict]
    model: str

class SummaryV1(BaseModel):             # frozen shape old clients depend on
    model_config = ConfigDict(extra="ignore")   # Pydantic's default, spelled out: unknown input dropped
    id: str
    summary: str                        # renamed to `text` in v2, hence the shim below

async def summarize(doc_id: str) -> SummaryV2:
    return SummaryV2(id="sum_1", text="...", citations=[{"doc": doc_id, "span": [0, 40]}],
                     model="summarizer-2026-03-01")   # pinned model snapshot is part of the contract

v1, v2 = APIRouter(prefix="/v1"), APIRouter(prefix="/v2")

@v2.post("/summaries", response_model=SummaryV2)
async def create_v2(doc_id: str):
    return await summarize(doc_id)

@v1.post("/summaries", response_model=SummaryV1, deprecated=True)   # flagged in OpenAPI docs
async def create_v1(doc_id: str, response: Response):
    s = await summarize(doc_id)                      # one implementation, two representations
    response.headers["Deprecation"] = "@1767225600"            # RFC 9745: deprecated since 2026-01-01
    response.headers["Sunset"] = "Sat, 31 Oct 2026 00:00:00 GMT"  # RFC 8594: removal date
    response.headers["Link"] = '</docs/migrate-v2>; rel="deprecation"'
    return SummaryV1(id=s.id, summary=s.text)        # render the old shape; drop fields v1 never promised

app = FastAPI()
app.include_router(v1)
app.include_router(v2)
```

## Trade-offs
- Additive-only evolution keeps every client working with no migration, but the API accumulates
  legacy fields and awkward names; the balance flips to a versioned break when the old shape actively
  misleads new integrators.
- Path versioning is simple and visible, but it moves the whole API at once; header or date pinning
  allows fine-grained evolution at the cost of transformation code and harder debugging.
- Stripe-style per-account pinning gives clients indefinite stability, but every change adds a
  transformation layer the server must maintain and test forever.
- Strict request validation (`extra="forbid"`) catches client typos early, but it turns harmless
  additive changes by clients into outages; loosen it on public endpoints, keep it on internal ones.

## Pitfalls
- Shipping a field rename as a "small cleanup"; the symptom is a partner integration failing days
  later with `KeyError` on a field you removed.
- Forking the codebase per version; the symptom is a bug fixed in v2 that still ships in v1 and a
  security patch applied to only one of them.
- Announcing a deprecation without measuring usage; the symptom is removing v1 on the date and
  discovering the largest customer was still on it.
- Treating model or prompt updates as invisible; the symptom is downstream parsers breaking because
  the new model stopped emitting the code fence the old one always produced.
- Clients that switch exhaustively on an enum; the symptom is a crash the day a new status value is
  added, even though no field changed.

## When to use
Apply the compatibility rules to every change on a published interface: review each diff of a
response or request model for the breaking list above, and treat prompt and model changes with the
same review. Introduce a version only when a break is unavoidable, and then introduce exactly one.
Measure requests per version per client, error rates after a release, and time-to-migrate for
deprecated versions. In day-to-day work this shows up as contract tests in CI that fail on breaking
schema diffs and as release notes written for integrators. Interviews probe whether you can list
what breaks, pick a strategy with reasons, and describe a deprecation lifecycle with dates and
telemetry rather than hope.

## Role lens
- **Engineer:** Keeps changes additive, renders old versions as representations over one
  implementation, and wires contract tests that fail on breaking OpenAPI diffs.
- **Architect:** Chooses the versioning strategy and deprecation policy, decides what is a published
  contract versus internal, and extends the policy to models, prompts and tool schemas.
- **FDE:** Shields customer integrations from provider model deprecations and your own API changes
  by pinning versions and planning migrations with the customer's release calendar.

## Check yourself
**Q:** A product team wants to change the `confidence` field in your extraction API from a 0–100
integer to a 0–1 float, and to add a `reasons` array. How do you ship this without breaking the three
external integrators on the endpoint?
**A:** The `reasons` array is additive and ships immediately. The `confidence` change is breaking:
same name, different type and scale, so a client that thresholds at 80 would silently treat every
result as low confidence. Options: add a new field (`confidence_score` as a float), keep `confidence`
as the integer, and deprecate it with a `Sunset` header and docs, removing it after telemetry shows
zero use; or, if the API is already versioned, make the change in the next version and render the old
shape from one implementation. I would avoid a bare `/v2` for one field. Either way, contract tests
in CI should fail on the type change so it cannot ship by accident.

## Quiz
### Q: Which change is backward compatible for a typical JSON API client?
- [ ] Renaming `summary` to `text` in the response
- [x] Adding an optional `citations` array to the response
- [ ] Changing `created_at` from seconds to milliseconds
- [ ] Making the optional `language` request field required
> Tolerant clients ignore fields they do not know, so additions are safe. Renames, unit changes and
> newly required inputs all change what existing clients send or read.

### Q: Why does Stripe-style per-account date pinning cost more on the server than path versioning?
- [ ] Dates are harder to parse than path segments
- [x] The server must maintain a chain of transformations that renders current behaviour in every older pinned shape
- [ ] Pinned accounts cannot use the SDKs
- [ ] It requires a separate database per version
> Each pinned version is a representation the server must keep producing from the current
> implementation, so every change adds a transformation to maintain and test indefinitely.

### Q: What should happen before an old API version is removed?
- [ ] Nothing; the new version has been available for a month
- [ ] Return 500 from the old version so clients notice
- [x] Announce a sunset date, emit deprecation headers, measure remaining usage per client, and run brownouts
- [ ] Silently redirect old-version requests to the new one
> Removal is safe only when you know who is left and they have been warned in-band and out-of-band.
> Brownouts surface stragglers before the hard cutoff; silent redirects hide breaking shape changes.
