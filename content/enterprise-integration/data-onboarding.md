---
id: data-onboarding
module: enterprise-integration
name: Data onboarding
kind: practice
difficulty: core
est_minutes: 14
status: reviewed
related:
  - scoping/data-access-and-permissions-discovery
  - enterprise-integration/data-ingestion-from-client-systems
  - enterprise-integration/data-profiling-and-quality-assessment
  - enterprise-integration/data-mapping-and-canonical-schemas
  - enterprise-integration/client-document-parsing
  - security-governance-compliance/data-classification
  - customer-data-evaluation/golden-dataset-creation-with-client-smes
  - enterprise-integration/sandbox-vs-production-environments
---

## TL;DR
Data onboarding is the repeatable process that takes a new customer from "access granted" to "their data
is in the system, validated and syncing". It runs in gated stages: inventory, a small sample, profiling,
mapping, a pilot load checked by the customer's experts, then a full backfill and incremental sync. It
exists because the slowest and riskiest part of an enterprise AI deployment is usually the customer's
data, not the model, and a big-bang import hides problems until users find them.

## Why it exists
An AI product is only as good as the data it retrieves or learns from, and every enterprise customer's
data is different: different systems, different field meanings, different quality, different
permission models, different legal constraints. The model and the pipeline code are shared across
customers; the data is not. That makes onboarding the per-customer cost that dominates time to value.

The naive approach is "get a full export, load it, see what happens". It fails in predictable ways. A
full export takes weeks to arrange and transfer, so the first real feedback arrives late. When it
arrives, problems are found all at once and in production-shaped volume: a third of documents are
scanned images, the "status" field has forty undocumented values, timestamps are in local time with no
zone, half the records are duplicates from a past migration. Because nobody agreed in advance what
"loaded correctly" means, there is no basis for sign-off, and the customer's first view of the system is
answers built on bad data, which damages trust that is hard to rebuild.

A second failure is treating each onboarding as a one-off. The tenth customer goes through the same
discovery the first did, with findings in someone's notes rather than in checks and mappings that can be
reused. A staged, gated, documented process fixes both: problems surface on a sample when they are
cheap to fix, each stage has an exit criterion the customer agrees to, and what you learn becomes
reusable mappings, checks and connector settings.

## How it works
This lesson orients the whole sequence; each stage has its own lesson in this module.

### 1. Inventory and scope
List the sources the use case needs (from discovery), and for each: owner, system, approximate volume,
change rate, sensitivity classification, and how it can be reached. Agree which subset is in scope
first, usually the slice that serves the pilot use case, *because* onboarding everything before proving
value multiplies cost and approval work. Confirm the legal basis: data processing agreement, residency
constraints, whether PII may leave the customer boundary, retention rules. Record each source's
**permission model** too (document ACLs, sharing groups, record-level visibility rules), because an
assistant that ingests content without its permissions will show a user what the source would have
hidden from them, and permissions are much harder to retrofit than to ingest alongside the content from
the first load.

### 2. Access and a representative sample
Get read access with a dedicated, least-privilege service identity, ideally against a sandbox or replica
first. Pull a sample that is *representative*, not convenient: across time periods, departments, document
types and record states. The first 1,000 rows of a table are often the oldest and least like current
data, so stratify the sample deliberately.

### 3. Profile and assess quality
Profile the sample: null rates, cardinalities, value distributions, formats, duplicates, and for
documents the mix of born-digital and scanned files, languages and sizes. The output is a written
assessment of what is usable, what needs cleaning, and what blocks the use case, shared with the customer.

### 4. Map to the canonical schema
Map source fields and value sets to your canonical model, record each decision, and quarantine values
you cannot map rather than defaulting them. Unmapped values go back to the customer's data owner as
questions, because only they know what `STATUS=7` means.

### 5. Pilot load and SME validation
Load the sample end to end (parse, map, chunk, embed or index) into a non-production tenant and have
the customer's subject-matter experts check it: are these records right, are these answers grounded in
the right documents, is anything missing. This is where a small golden set often starts, *because* the
same experts who validate the data can write the first evaluation questions. Validate permissions in the
same pass: log in as two or three seeded users with different roles and confirm each sees only what the
source shows them.

### 6. Backfill, then incremental sync
Run the full historical load in batches with throttling (the source is someone's production system),
checkpointing so a failure resumes rather than restarts, and reconciling counts per batch.

The order of the hand-over to incremental sync matters, because a large backfill takes days and the
source keeps changing while it runs. If you start the change feed only after the backfill finishes,
every record edited during the backfill, after its batch was read, is lost: the backfill saw the old
version, and the feed starts too late to see the new one. So record the change-feed position first (a
CDC log position, or a `modified_at` watermark taken *before* the first batch), run the backfill, then
replay changes from that recorded position. Records touched in both arrive twice; that is safe as long as
the apply step is idempotent and keeps the higher source version. Keep a periodic reconciliation running
afterwards, because the backfill is a snapshot and the change feed can still miss deletes and lost
events.

### 7. Sign-off and handover
Exit with an agreed acceptance record: coverage (records and documents loaded versus in scope), reject
and quarantine counts with reasons, quality findings and their owners, sync freshness, and the runbook
for re-onboarding a source. Encode the mappings, checks and connector config as versioned artifacts so
the next customer on the same system starts from them.

## Example
A worked scenario. An insurer wants an assistant for claims handlers over two sources: a claims system
(a relational database, roughly 2 million claim records) and a SharePoint document library (roughly
300,000 files). Figures are illustrative.

**Week 1, inventory:** scope is narrowed to motor claims from the last three years, which cuts the claims
table to about 400,000 records and the library to about 90,000 files. Legal confirms claimant PII may be
processed only in the customer's cloud region, which fixes the deployment target before any code.

**Week 2, sample:** a stratified sample of 2,000 claims (by year, region and status) and 1,500 documents
(by folder and file type). Profiling shows 38% of documents are scanned PDFs with no text layer, 6% are
password-protected, the `loss_date` column is 11% null for one region, and `status` has 27 distinct
codes where the documentation lists 9.

**Week 3, mapping and fixes:** 18 status codes are mapped with the claims operations lead; the remaining
9 turn out to be legacy codes from a migration and are mapped to "closed, legacy". Scans are routed to
OCR. Protected files are reported back, because only the customer can supply passwords or exclude them.

**Week 4, pilot:** the sample is loaded to a pilot tenant. Three claims handlers review 50 claim
summaries and 40 question-answer pairs; they find that adjuster notes are attached to the wrong claim
for one region, traced to a join on a non-unique key. Fixed and re-run.

**Weeks 5 to 6, backfill and sync:** the CDC connector is started and its log position recorded before
the first batch runs. The full in-scope set then loads in nightly batches, throttled to stay off peak
hours, with per-batch count reconciliation. Over those two weeks handlers keep editing claims, so when
the backfill ends the CDC changes since the recorded position are applied; claims edited during the
backfill arrive in both paths, and the version check keeps the later edit. The sign-off record
lists coverage of 99.1% of in-scope claim records, the password-protected files (counted on the full
library, not extrapolated from the sample's 6%) as excluded with a named owner, and a nightly
reconciliation job.

## Trade-offs
- Staged onboarding finds problems on a sample when they are cheap, but takes more calendar time before
  "all the data" is in; it pays off whenever the data is unfamiliar, and is overkill for a second tenant
  on an identical, already-mapped system.
- Narrowing scope to the pilot slice gets to value faster, but risks a design that does not generalize;
  include at least one awkward source in the first slice to keep the design honest.
- Customer SME validation catches meaning errors no automated check can see, but SME time is scarce and
  expensive; ask for short, structured reviews of specific samples rather than open-ended exploration.
- Reusable onboarding artifacts (mappings, checks, connector templates) make later customers faster, but
  only if they are kept in version control and tested; otherwise they rot into misleading defaults.

## Pitfalls
- Loading the full export before profiling a sample; the symptom is a multi-week load followed by the
  discovery that a key field is unusable, and a restart.
- Sampling the first N rows or the most convenient folder; the symptom is a pilot that looks clean and a
  production load full of formats and states the sample never contained.
- Defaulting unmappable values to a catch-all bucket; the symptom is answers and analytics that are
  quietly wrong for a whole category of records.
- No agreed acceptance criteria; the symptom is a sign-off meeting that turns into a debate, with the
  customer judging success by individual bad answers.
- Running the backfill without throttling against the customer's production database; the symptom is a
  call from their operations team about slow screens during business hours.
- Starting the incremental feed only after the backfill completes; the symptom is a set of records whose
  indexed version is older than the source's, all with source edit times inside the backfill window.
- Ingesting content without its permissions; the symptom is a pilot user seeing a document or record in
  an answer that the source system would not have shown them.
- Treating onboarding as finished at backfill; the symptom is a slowly diverging index because deletes
  and late updates after the snapshot were never reconciled.

## When to use
Run this process for every new customer data source, scaled to its novelty: an unfamiliar system or a
regulated dataset gets every stage; a new tenant on a system you have already mapped can often reuse
mappings and go from sample to backfill quickly, but still needs profiling, because two customers on
the same software use its fields differently. Measure time from access granted to first validated pilot
load, coverage against scope, quarantine and reject rates, and the number of mapping questions
outstanding. In interviews and customer conversations, the strong signal is describing onboarding as a
gated sequence with exit criteria rather than as "we import their data".

## Role lens
- **Engineer:** Builds the sample, profile and pilot-load tooling so each stage is a command rather than
  a notebook, with checkpointed backfills and per-batch count reconciliation.
- **Architect:** Defines the canonical schema and onboarding stages, decides what is reusable across
  customers, and sets the acceptance criteria every onboarding must meet before production.
- **FDE:** Runs onboarding inside the customer's environment, chases access and data-owner answers, and
  turns each finding into an agreed decision recorded with a named owner.

## Check yourself
**Q:** A customer offers to send a full database dump "so you have everything" on day one. Why might you
still insist on a staged onboarding, and what would you do with the dump?
**A:** The dump does not remove the need for profiling, mapping and SME validation; it only changes where
the sample comes from. Loading it all first means finding unusable fields, unmapped codes and
duplicates at full volume, after weeks of work, with no agreed definition of correct. I would accept the
dump if legal and residency allow it, but pull a stratified sample from it, profile and map that, run a
pilot load reviewed by their experts, and only then backfill the rest. I would also note a dump is a
snapshot: I would ask exactly when it was taken (ideally a log position or transaction timestamp), start
incremental sync from that point rather than from the day we finish loading, and keep reconciliation
running, or every change made in between is silently missing and the system drifts from the source.

## Quiz
### Q: Why should the onboarding sample be stratified rather than the first N rows of each table?
- [ ] Stratified samples are smaller and faster to load
- [x] The first rows are often old or atypical, so a convenient sample can miss formats and states common in current data
- [ ] Database exports always return rows in random order
- [ ] Stratification is required by most data processing agreements
> Convenient samples are biased toward whatever the source returns first, often legacy records. Sampling
> across time, region, type and state surfaces the variety the production load will actually contain.

### Q: What is the right handling of source values that cannot be mapped to the canonical schema during onboarding?
- [ ] Map them to the most common canonical value to keep coverage high
- [ ] Drop the records silently so they do not pollute the index
- [x] Quarantine them and send them to the customer's data owner as questions
- [ ] Let the LLM infer the meaning at query time
> Only the data owner knows what an undocumented code means. Defaulting or dropping makes results quietly
> wrong; quarantining keeps the record and makes the gap visible and assignable.

### Q: Why does onboarding not end when the historical backfill completes?
- [ ] The embedding model must be retrained after each backfill
- [ ] Backfills always miss a fixed percentage of records
- [x] The backfill is a snapshot, and later changes and deletes need incremental sync and reconciliation
- [ ] Customers require a second full import before sign-off
> The source keeps changing after the snapshot. Without incremental sync and periodic reconciliation, the
> system drifts from the source and serves deleted or outdated content.
