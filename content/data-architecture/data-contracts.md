---
id: data-contracts
module: data-architecture
name: Data contracts
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
related:
  - data-architecture/ingestion-pipelines
  - data-architecture/data-lineage
  - enterprise-integration/data-mapping-and-canonical-schemas
  - enterprise-integration/data-profiling-and-quality-assessment
  - backend-fundamentals/api-versioning-and-backward-compatibility
  - node-ts-for-ai/runtime-schema-validation
  - python-for-ai/contract-testing
  - agents/typed-tool-contracts
sources:
  - Official docs: Confluent Schema Registry
  - Official docs: Great Expectations
---

## TL;DR
A data contract is an explicit, versioned, machine-checked agreement between the team that produces a
dataset and the teams that consume it: schema, meaning, quality guarantees, freshness and ownership. It
exists because AI pipelines are the most fragile consumers in the company: a renamed column or a changed
unit does not crash an embedding job or a training run, it quietly produces a worse model or index. The
contract moves the failure to the producer's side, at write time, where it is loud and cheap.

## Why it exists
Imagine the support-ticket pipeline that feeds a RAG index and a fine-tuning set. The ticketing team
renames `resolution_text` to `resolution` and starts writing `category` as an integer id instead of a
label. Nothing errors. The chunker reads a missing field as an empty string and emits empty chunks; the
metadata filter on `category == "billing"` silently matches nothing; the fine-tuning set gets three weeks
of examples with blank targets. The first signal is a product manager asking why answers got worse, and
the investigation takes days because the producer did not know anyone depended on those fields.

Traditional applications fail fast on this: a typed API client breaks at compile time or returns a 400.
Data pipelines do not, *because* data flows through files and tables with no type-checked boundary, and
*because* consumers are typically discovered after the fact. AI pipelines make it worse: embeddings and
gradients absorb any input, so bad data becomes a slightly wrong vector or weight, invisible until an
evaluation runs, if one runs.

The naive alternative is defensive consumer-side validation: each pipeline checks what it reads. It helps,
but it fails on three counts. The check runs after the bad data was written, so the producer still has to
be found and persuaded to fix it. Every consumer writes its own checks and they disagree. And semantic
changes (the same column now in cents instead of dollars) pass schema checks entirely. A contract makes
the producer responsible for a published, versioned specification and enforces it at the point of
production, where the person who made the change sees the failure.

## How it works
### What a contract contains
1. **Schema.** Field names, types, nullability, and allowed values, in a machine-readable form (JSON
   Schema, Avro, Protobuf, or a Pydantic or dataclass model that generates one). This catches renames and
   type changes.
2. **Semantics.** What each field means, its unit, its timezone, its reference data. `amount` is "USD,
   two decimal places, net of tax". This is prose plus, where possible, checks: ranges, regexes,
   cross-field rules such as `closed_at >= opened_at`.
3. **Quality guarantees.** Null rates, uniqueness of keys, referential integrity, row-count bounds per
   partition, and distribution expectations that catch "all categories are suddenly 0".
4. **Freshness and delivery.** How often data lands, maximum lag, partitioning, and how late data is
   handled.
5. **Ownership and change policy.** Who owns the dataset, how changes are proposed, what counts as a
   breaking change, and how long a deprecated version stays available.
6. **Version.** The contract has a version; the data carries it, so consumers know which contract a
   record was written under.
7. **Known consumers.** Which pipelines depend on which fields. This is what lets a producer's CI tell the
   author of a breaking change whom it will break; the list is best derived from lineage (a forward
   traversal from the dataset) rather than maintained by hand, because hand-kept lists miss the newest
   consumer, which is usually the AI pipeline.

The schema part is usually expressed in an existing format; the remaining parts are often kept in a YAML
specification beside it. Open specifications for the whole document exist (the Open Data Contract
Standard is one), but the format matters far less than that it is versioned, reviewed and checked by
machines.

### Where enforcement lives
The contract is enforced at the **producer boundary**, *because* a failure there is attributed correctly
and happens before any consumer reads bad data. Concretely: the producing service validates each record
against the schema before publishing; a streaming platform's schema registry rejects incompatible schema
versions at registration time (Confluent's registry has backward, forward and full compatibility modes
for exactly this); a batch pipeline runs a quality suite (Great Expectations or a hand-rolled equivalent)
on the output partition and refuses to mark it complete if checks fail. Records that violate the contract
go to a dead-letter location with the violation attached, not silently dropped and not passed through.

Per-record dead-lettering needs a batch-level circuit breaker beside it. If 0.1% of a partition violates
the contract, quarantining those rows and publishing the rest is right. If 40% violates it, the cause is
almost certainly a systematic upstream change, and publishing the surviving 60% hands consumers a biased
sample that looks valid: a training set missing every ticket from one product, say. So the contract also
states a violation-rate threshold above which the whole partition fails and nothing is published,
*because* "no new data today" is a loud, safe failure and "a skewed subset" is a silent one.

Consumers still validate, but against the same published contract, so the two sides cannot disagree about
what valid means. The contract file lives in version control; a CI check diffs proposed changes and
classifies them as compatible (adding an optional field) or breaking (removing or renaming a field,
narrowing a type, changing a unit), and breaking changes require a new major version and a migration
window during which both versions are produced.

### Compatibility rules
The two directions are easy to swap, so use the schema-registry definitions. *Backward* compatible means
a consumer using the new schema can read data written with the old one; it lets you upgrade consumers
first, and permits deleting fields and adding fields that have defaults. *Forward* compatible means data
written with the new schema can be read by consumers still on the old one; it lets producers upgrade
first, and permits adding fields and deleting only fields that had defaults. *Full* compatibility is
both. Renaming a field breaks both directions. For AI consumers fed by an independent producer, forward
compatibility is the one that matters most day to day, *because* the producer ships first and a chunker or
feature pipeline deployed last month must keep working on today's data; backward compatibility matters
when you deploy a new consumer and reprocess history written under older schemas. Write the direction
into the contract explicitly, since teams outside the streaming world often use the words the other way
round.

### Contracts specific to AI pipelines
The datasets that feed AI have fields the rest of the company does not think about, and the contract is
where they become explicit:
- **Text fields intended for embedding:** encoding, language tag, maximum length, and whether the field
  may contain PII (which decides redaction before embedding).
- **Metadata used for filtering:** the exact value set for `tenant_id`, `access_scope`, `product`, since a
  filter on a value that stopped being produced returns nothing with no error.
- **Labels for fine-tuning or evaluation:** the label taxonomy version and the annotation guideline
  version, because a change in labeling policy is a semantic break even when the schema is unchanged.
- **Timestamps for point-in-time joins:** which timestamp is the event time versus the ingestion time,
  because confusing them creates leakage in training data.

### Testing the contract
Producer CI runs the validation suite against sample output; consumer CI runs its pipeline against
fixture data generated from the contract; and a compatibility check compares the proposed contract
version against the current one. This is the data equivalent of contract testing between services.

## Example
A contract as code for a support-ticket feed: a Pydantic model carrying schema and semantic checks, a
contract version embedded in every record, and a producer-side gate that routes violations to a
dead-letter list instead of publishing them.

```python
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field, ValidationError, model_validator

CONTRACT_VERSION = "support_tickets.v2"

class Ticket(BaseModel):
    contract_version: Literal["support_tickets.v2"] = CONTRACT_VERSION
    ticket_id: str = Field(pattern=r"^T-\d{6,}$")
    tenant_id: str = Field(min_length=1)
    category: Literal["billing", "login", "bug", "feature", "other"]   # closed value set
    opened_at: datetime
    closed_at: datetime | None = None
    resolution: str | None = Field(default=None, max_length=8000)       # text that will be embedded
    language: Literal["en", "de", "fr"] = "en"
    contains_pii: bool                                                  # decides redaction before embedding

    @model_validator(mode="after")
    def semantics(self):                    # tz check first: naive-vs-aware compare raises TypeError
        if any(t is not None and t.tzinfo is None for t in (self.opened_at, self.closed_at)):
            raise ValueError("timestamps must be timezone-aware (UTC)")
        if self.closed_at and self.closed_at < self.opened_at:
            raise ValueError("closed_at before opened_at")
        if self.closed_at and not self.resolution:
            raise ValueError("closed ticket must carry a resolution")
        return self

def publish(raw_records: list[dict]):
    good, dead = [], []
    for r in raw_records:
        try:
            good.append(Ticket(**r).model_dump())
        except ValidationError as e:
            dead.append({"record": r, "violations": e.errors(include_url=False)})
    return good, dead

now = datetime.now(timezone.utc)
good, dead = publish([
    {"ticket_id": "T-000123", "tenant_id": "acme", "category": "billing",
     "opened_at": now, "closed_at": now, "resolution": "Refunded.", "contains_pii": False},
    {"ticket_id": "T-000124", "tenant_id": "acme", "category": 3,       # upstream changed to int ids
     "opened_at": now, "contains_pii": False},
])
print(len(good), "published;", len(dead), "dead-lettered:", dead[0]["violations"][0]["msg"])
```

The second record is the silent failure from the "Why it exists" section, now caught at the producer with
a message naming the field, before the embedding job ever sees it.

## Trade-offs
- Producer-side enforcement attributes failures correctly and stops bad data early, but it asks the
  producing team to own a specification; without organisational buy-in, consumer-side checks are the
  realistic fallback.
- A closed value set for categorical fields makes filters safe, but every new business category needs a
  contract change; if categories churn weekly, use an open set plus a reference table the contract points to.
- Strict schemas reject records with unexpected fields and prevent drift, but they also reject harmless
  additions; "ignore unknown fields, forbid missing ones" is the usual compromise.
- Dead-lettering violations preserves data for repair, but a dead-letter queue nobody monitors is a
  silent data loss; alert on its rate and age.
- Versioning with dual-production windows makes breaking changes safe, but doubles the producer's
  output for the window; for high-volume streams, make the window short and the migration automated.

## Pitfalls
- Validating only schema and not semantics; the symptom is a model whose quality drops with no schema
  errors, traced weeks later to a unit or label-taxonomy change.
- Enforcing the contract only in the consumer; the symptom is the AI team discovering every upstream
  change and arguing for a fix after the damage is done.
- Omitting the contract version from the records themselves; the symptom is a reprocessing job that
  cannot tell which rules applied to which historical rows.
- Treating filter metadata as free text; the symptom is `tenant_id` values with different casing or
  whitespace making ACL filters leak or return nothing.
- Silently dropping violating records to keep the pipeline green; the symptom is row counts drifting down
  and a training set that over-represents whatever happened to validate.
- Making every field required to be safe; the symptom is the producer defaulting fields to placeholder
  values just to pass, which is worse than null.
- Dead-lettering per record with no batch-level threshold; the symptom is a partition that publishes on
  schedule with a fraction of its usual rows and a quietly shifted category distribution.

## When to use
Introduce a contract for any dataset that crosses a team boundary into an embedding, fine-tuning, feature
or evaluation pipeline; these are the consumers that fail silently, so they benefit most. A dataset
produced and consumed by the same team can get by with in-pipeline validation until a second consumer
appears. Measure the dead-letter rate (and alert on its change, not only its level), partitions failed by the
batch threshold, the time from an upstream change to its detection (should be minutes, at the producer),
and the number of breaking changes per quarter. In day-to-day work the contract
is the file you open before adding a metadata filter or a training feature. In interviews, "how do you
keep upstream schema changes from degrading your model" is answered with producer-side contracts,
compatibility checks in CI and versioned records, not with "we validate inputs".

## Role lens
- **Engineer:** Writes the contract as a typed model, wires validation and dead-lettering into the
  producer, and generates fixtures from the contract for consumer tests.
- **Architect:** Decides which datasets require contracts, the compatibility policy and versioning scheme,
  and the organisational rule that producers own their published data.
- **FDE:** Negotiates the contract with a customer's data owners during onboarding, including PII flags
  and tenant scoping, so the pipeline is protected from their internal changes.

## Check yourself
**Q:** A customer's CRM team wants to change the `region` field from free text to a code list and add a
`segment` field next month. Your RAG metadata filters and a fine-tuned router depend on `region`. How do you
handle the change?
**A:** Classify the two changes. Adding `segment` as an optional field with a default is compatible in both
directions and can ship under the current contract version once documented. Changing `region` from free text to codes is a
breaking semantic change even though the type stays string: existing filters compare against the old
values and the router was trained on them. So it requires a new major contract version. Agree a migration
window in which the producer emits both `region` (old values) and `region_code` (new), with the version
stamped on each record. During the window, update the filter logic to use codes, rebuild the index
metadata, and retrain or remap the router with the new values, validating on the eval set. Run the
compatibility check in CI so the old field cannot be dropped before consumers have migrated, and dead-letter
any record whose `region_code` is outside the agreed list.

## Quiz
### Q: Why are AI pipelines more exposed to upstream data changes than typical application code?
- [ ] They process more rows per second than applications
- [x] Embeddings and training absorb malformed inputs without errors, so damage appears only as degraded quality
- [ ] They usually run in a different programming language from the producer
- [ ] Their schemas change more frequently
> A typed application fails fast on a renamed field. An embedding job turns an empty string into a vector
> and a training job fits whatever labels it is given, so the only symptom is a worse index or model,
> discovered late.

### Q: Why is the contract enforced at the producer rather than only in consumers?
- [ ] Producers have more compute available for validation
- [ ] Consumers cannot parse schema files
- [x] A failure at the producer is attributed to the change that caused it and happens before any bad data is read
- [ ] It is required by schema registries
> Producer-side enforcement makes the person who changed the data see the failure immediately. Consumer
> checks still matter, but they run after the bad data exists and leave the AI team to track down the
> cause.

### Q: Which change passes a schema check yet should be treated as a breaking contract change?
- [ ] Adding a new optional field with a default value
- [ ] Widening a string field's maximum length
- [x] Switching an amount column from dollars to cents while keeping its type
- [ ] Adding a new enum value to a category list that consumers treat as open
> The type is unchanged, so schema validation passes, but every consumer's interpretation is now wrong by
> a factor of 100. Semantic checks (ranges, units documented and tested) and a version bump are what catch
> and manage this class of change.
