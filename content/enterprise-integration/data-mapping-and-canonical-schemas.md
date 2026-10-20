---
id: data-mapping-and-canonical-schemas
module: enterprise-integration
name: Data mapping & canonical schemas (schema mapping)
kind: pattern
difficulty: core
est_minutes: 16
status: reviewed
related:
  - data-architecture/data-contracts
  - scoping/domain-modeling
  - system-design/domain-driven-design-and-bounded-contexts
  - enterprise-integration/data-profiling-and-quality-assessment
  - enterprise-integration/data-onboarding
  - enterprise-integration/tenant-specific-configuration-and-feature-flags
  - data-architecture/data-lineage
  - node-ts-for-ai/runtime-schema-validation
sources:
  - Hohpe & Woolf, 2003 — Enterprise Integration Patterns
---

## TL;DR
Every client represents the same business concepts differently: one CRM's "Account" is another's
"Customer", dates arrive in three formats, and a status of "Closed" may mean won, lost or cancelled. A
canonical schema is your one internal model of those concepts; schema mapping is the per-source,
versioned translation into it. With N sources and M consumers you then maintain about N + M mappings
instead of N × M, and every downstream component, prompts and retrieval filters included, reads one shape.

## Why it exists
Suppose your AI product consumes tickets from Zendesk, ServiceNow and Jira Service Management across
twelve customers, and feeds a retriever, a summarizer, an analytics view and an evaluation harness. If
each consumer reads each source's native shape, every consumer contains knowledge of every source:
the summarizer knows ServiceNow's `priority` is an integer from 1 (critical) to 5, derived from impact and
urgency, while Zendesk uses strings such as `urgent` and `low`, the
retrieval filter knows which field holds the product name in each system. Adding a source means editing
every consumer; changing a consumer means testing it against every source. That is the N × M problem.

The naive fix is to let the LLM cope: dump the raw JSON into the prompt and let the model figure out
which field is the priority. That works in a demo and fails in production in three ways. Retrieval
filters, access control and analytics are not LLM calls; they need a field they can query, so they still
need the mapping. Model interpretation is inconsistent across records and model versions, so the same
code can be read two ways on two days. And the meaning of a source value is often not in the data at all:
whether `STATUS=7` means "escalated" or "awaiting customer" is knowledge held by the client's data owner,
and no amount of inference recovers it reliably.

The canonical data model, a named pattern in Hohpe and Woolf's catalogue, puts that knowledge in one
place per source: a mapping, reviewed with the people who know the data, tested, and versioned.

## How it works
### Designing the canonical schema
The canonical schema models *your* domain, the concepts your product reasons about (case, party,
document, interaction), not a union of every source's fields. Keep it small and stable, *because* each
field is a promise every mapping must keep. Keep its scope to your product too. The pattern has a
well-known failure at enterprise scale: a single canonical model for a whole company tries to make
"customer" mean the same thing to sales, billing and support, needs every team's agreement to change,
and becomes the bottleneck. Domain-driven design's answer is a model per bounded context with
translation at the edges, and your product's canonical schema is exactly one such context; it does not
need to be the client's enterprise data model, and should not try. Typical choices:
- Types are normalized: timestamps in UTC with zone, money as amount plus ISO currency, explicit units.
- Enumerations are closed and documented (`status ∈ {open, pending, resolved, closed}`), *because*
  downstream logic branches on them.
- Every record keeps provenance: source system, source ID, source version and mapping version, so any
  canonical value can be traced back.
- An **extensions** bag holds source fields with no canonical home, so mapping is lossless and a field
  can be promoted later without re-ingesting.
- Evolution is additive and versioned: new optional fields are cheap; renames and meaning changes are a
  new version with a migration, as with any data contract.

### Layers of a mapping
Mapping is more than renaming, and the layers fail in different ways:
1. **Structural:** field renames, flattening or nesting, splitting one field into two or joining two.
2. **Type and format:** parse dates and numbers, normalize time zones, convert units and currencies.
3. **Value or semantic mapping:** crosswalks from source codes to canonical enums, agreed with the data
   owner. This is where meaning lives and where most real errors happen.
4. **Identity:** resolve source keys to canonical IDs and link entities across sources (the same
   customer in the CRM and in billing), which may need matching rules rather than equality.
5. **Derivation:** computed fields such as a canonical `is_escalated` from several source flags.

### Mappings as declarative, versioned config
Express each source's mapping as data (a field table and value crosswalks) interpreted by one engine,
rather than as hand-written code per customer, *because* data can be reviewed by non-engineers, diffed,
validated and reused for the next tenant on the same system. Tenant-specific overrides layer on top of a
per-system base mapping, since two customers on the same CRM often customize the same fields differently.

### Mapping back: write paths need the reverse direction
An assistant that only reads needs the inbound mapping. One that writes back (updating a
ticket's status, filing a case) needs the reverse, and the reverse is not automatic, *because* value crosswalks
are often many-to-one. If source codes `W` and `K` both map to canonical `pending`, then canonical
`pending` has no single source code to write. Either keep the original source value on the canonical
record (provenance makes this cheap) and write it back unchanged when the meaning has not changed, or
define an explicit reverse crosswalk agreed with the data owner (`pending` writes `W`), and test the
round trip, source → canonical → source, as a fixture.

### Unmapped values are errors, not defaults
When a value has no crosswalk entry, the record goes to a quarantine with the reason, and the value is
reported to the data owner. Defaulting it to `other` or the most common value makes every downstream
result for that category silently wrong, which is worse than a visible gap.

### Testing and validating
Each mapping has fixture tests (source record in, expected canonical record out) built from profiled
samples, and every mapped record is validated against the canonical schema before it is written. Track
per-field null and quarantine rates per source after each load, because a sudden rise is usually a
source-side change rather than a data trend.

### LLM-assisted mapping
LLMs are useful for *proposing* mappings: given source field names, descriptions and sample values,
they can draft a field table and crosswalk quickly. Treat the output as a suggestion that a human
confirms with the data owner, then freeze it as config. The model accelerates authoring; it should not
be the runtime mapping.

## Example
A declarative mapping for one customer's ticket export with a structural rename, type normalization, a
value crosswalk, an extensions bag and quarantine on unknown values.

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MAPPING = {  # versioned config for tenant "acme", source "legacy_helpdesk"
    "version": "acme-helpdesk-v3",
    "fields": {"TicketNo": "source_id", "Subj": "title", "Opened": "opened_at", "Stat": "status"},
    "tz": "Europe/Berlin",                       # source writes naive local time
    "status_crosswalk": {"N": "open", "W": "pending", "K": "pending", "E": "resolved", "Z": "closed"},
}

class Unmapped(Exception): pass                     # caller sends the record to quarantine

def map_ticket(src: dict, m=MAPPING) -> dict:
    out = {"source_system": "legacy_helpdesk", "mapping_version": m["version"]}
    out.update({m["fields"][k]: v for k, v in src.items() if k in m["fields"]})
    ext = {k: v for k, v in src.items() if k not in m["fields"]}   # lossless: nothing is dropped
    missing = {"source_id", "opened_at", "status"} - out.keys()
    if missing:
        raise Unmapped(f"missing {missing} in {src.get('TicketNo')!r}")
    local = datetime.strptime(out["opened_at"], "%d.%m.%Y %H:%M").replace(tzinfo=ZoneInfo(m["tz"]))
    out["opened_at"] = local.astimezone(timezone.utc).isoformat()
    code = out["status"]
    if code not in m["status_crosswalk"]:
        raise Unmapped(f"status={code!r} for ticket {out['source_id']}")
    out["status"], out["source_status"] = m["status_crosswalk"][code], code   # keep original for write-back
    out["extensions"] = ext
    return out

print(map_ticket({"TicketNo": "T-1042", "Subj": "VPN drops", "Opened": "03.11.2025 09:15",
                  "Stat": "W", "Kostenstelle": "4711"}))
# opened_at becomes 2025-11-03T08:15:00+00:00 (CET is UTC+1 in November); "Kostenstelle" is kept in extensions.
```

The crosswalk maps two source codes, `W` and `K`, to one canonical `pending`; that decision came from the
client's helpdesk lead, not from the data. A new code `X` appearing next quarter raises `Unmapped` and
lands in quarantine with a message the FDE can forward, instead of becoming a silently wrong status.
Keeping `source_status` means a later write-back can restore `K` rather than guessing between `W` and
`K`. One edge the sketch does not handle: naive local times are ambiguous for one hour each autumn (the
clock repeats 02:00 to 03:00) and nonexistent for one hour each spring, and `replace(tzinfo=...)`
silently picks one reading. If ordering within that hour matters, flag those values rather than trusting
the default.

## Trade-offs
- A canonical schema turns N × M translations into roughly N + M, but costs an upfront modelling effort
  and an extra hop; with one source and one consumer, mapping directly is simpler and fine.
- A small canonical schema is stable and easy to map to, but pushes useful source detail into extensions;
  promote a field once several consumers need it, not before.
- Declarative mappings are reviewable and reusable, but awkward for genuinely complex logic such as
  multi-record identity resolution; allow a code escape hatch for those cases, tested like any code.
- Strict quarantine on unknown values keeps results correct, but lowers coverage and creates support
  work; it is the right default when values drive filters, routing or access, and too strict for a
  free-text field that is only shown to users.
- LLM-proposed mappings speed authoring considerably, but a confident wrong crosswalk is hard to spot;
  the saving holds only if a data owner reviews every proposed value mapping.

## Pitfalls
- Designing the canonical schema as the union of every source's fields; the symptom is hundreds of mostly
  null columns and no consumer able to tell which fields are reliably populated.
- Mapping by field name alone without checking values; the symptom is two sources with a `priority` field
  whose scales run in opposite directions, so high-priority tickets from one source sort to the bottom.
- Dropping naive timestamps into UTC without the source zone; the symptom is events shifted by hours and
  off-by-one-day errors around midnight and daylight-saving changes.
- Defaulting unknown codes; the symptom is a canonical category that grows quietly and contains records
  that belong elsewhere.
- No mapping version on records; the symptom is that after a crosswalk fix you cannot tell which records
  were mapped under the old rule and need remapping from raw.
- Writing back through an inverted many-to-one crosswalk; the symptom is source records whose status
  changes from `K` to `W` when the assistant updates an unrelated field, and the client's reports
  shifting for no visible reason.
- Letting the LLM interpret raw source records at query time instead of mapping; the symptom is filters
  and permissions that do not work and answers that vary across model versions.

## When to use
Introduce a canonical schema once you have more than one source system or more than one consumer of the
same data, which in enterprise AI is almost immediately. Keep the schema focused on the concepts your
product reasons about and agree crosswalks with the client's data owners during onboarding. Measure,
per source and mapping version, the quarantine rate, per-field null rate and the count of outstanding
unmapped values. In day-to-day FDE work this is where most onboarding questions come from. In
interviews, expect to explain why a canonical model reduces coupling and where it hurts.

## Role lens
- **Engineer:** Builds the mapping engine, fixture tests and quarantine path, and stamps every canonical
  record with source identity and mapping version for replay.
- **Architect:** Owns the canonical schema and its versioning policy, decides what is canonical versus an
  extension, and keeps the model focused on the product's domain concepts.
- **FDE:** Sits with the client's data owners to agree crosswalks, chases unmapped values, and writes the
  tenant-specific mapping overrides for each customer's customizations.

## Check yourself
**Q:** A colleague proposes skipping canonical mapping for a new CRM source: "just put the raw record in
the prompt, the model understands Salesforce fields." When is that acceptable and when is it not?
**A:** It is acceptable for content the model only reads and shows, in a prototype with one source, where
inconsistency costs little. It breaks for anything that is not an LLM call: retrieval filters, access
control, analytics and evaluation need a concrete field and value set. It also ties meaning to the
model's guesses, which vary across records and model versions, and cannot know a customer's custom
picklist meanings. With a second source or consumer, every component inherits every source's quirks.
I would map the fields used for filtering, routing and permissions to the canonical schema, keep
everything else in extensions, and let the LLM draft the mapping for review, not perform it at runtime.

## Quiz
### Q: With 6 source systems and 4 consumers, roughly how many translations does a canonical schema need compared with point-to-point?
- [ ] 24 with a canonical schema versus 10 point-to-point
- [x] About 10 with a canonical schema versus 24 point-to-point
- [ ] 6 in both cases
- [ ] 4 with a canonical schema versus 6 point-to-point
> Point-to-point needs one translation per source-consumer pair, 6 × 4 = 24. A canonical model needs one
> mapping in per source and one read per consumer, about 6 + 4 = 10.

### Q: Why should every canonical record carry the mapping version it was produced with?
- [ ] To let the index sort records by recency
- [ ] Because canonical schemas cannot be changed otherwise
- [x] So that after a mapping fix you can find and re-map exactly the affected records from raw
- [ ] So that consumers can parse each version differently
> When a crosswalk is corrected, records mapped under the old rule must be reprocessed. Without a version
> stamp you cannot tell which ones, so you either remap everything or leave errors in place.

### Q: What is the strongest reason to keep an extensions bag on canonical records?
- [ ] It lets consumers ignore the canonical schema entirely
- [x] It makes mapping lossless, so a source field can be promoted later without re-ingesting from the client
- [ ] It replaces the need for value crosswalks
- [ ] It stores the LLM's interpretation of each field
> Source fields without a canonical home are kept rather than dropped. If a consumer later needs one, it
> can be promoted and backfilled from stored data instead of negotiating a new pull from the client.
