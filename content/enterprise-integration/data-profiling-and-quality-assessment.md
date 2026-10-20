---
id: data-profiling-and-quality-assessment
module: enterprise-integration
name: Data profiling & quality assessment
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - enterprise-integration/data-onboarding
  - enterprise-integration/data-mapping-and-canonical-schemas
  - enterprise-integration/client-document-parsing
  - data-architecture/data-contracts
  - customer-data-evaluation/representative-test-case-sampling
  - scoping/baseline-measurement-before-deployment
  - llmops-platform/drift
  - security-governance-compliance/data-classification
sources:
  - Wang & Strong, 1996 — Beyond Accuracy: What Data Quality Means to Data Consumers
  - Official docs: Great Expectations
---

## TL;DR
Profiling measures what a client's data actually contains (null rates, value sets, formats, duplicates,
freshness, document types) instead of what its documentation claims. Quality assessment judges those
measurements against what the use case needs and turns them into decisions: usable, fixable, or
blocking. The findings then become automated checks on every load, because data that was fine at
onboarding drifts, and an AI system degrades quietly on bad data rather than crashing.

## Why it exists
A schema tells you a column is called `resolution_notes` and is a text field. It does not tell you that
40% of rows contain "see email", that one region writes in French, or that rows before a 2021 migration
are truncated at 255 characters. Documentation, when it exists, describes the system as designed, and
the people who know the quirks are busy. Every enterprise dataset has these surprises, and an AI system
is unusually sensitive to them because it rarely fails loudly. A pipeline that crashes on a bad date gets
fixed the same day. A retriever that indexes 30,000 empty notes returns them as top results, and a
summarizer given "see email" invents a resolution, and both look like model quality problems.

The naive approach is to look at a few records, decide the data "looks fine", and build. Eyeballing
finds the obvious and misses what matters, which is distributional: the 11% null rate in one region, the
duplicate records from a merge, the share of scanned PDFs. A second naive approach is to treat quality
as absolute ("is this data clean?"). Quality is fitness for a purpose: a field 30% null is irrelevant if
the use case never reads it and blocking if it is the retrieval filter. Wang and Strong's framing of
quality as defined by the data's consumers is the useful one.

Profiling exists to replace impressions with measurements early, when findings change the plan cheaply,
and assessment exists to connect each measurement to a decision someone owns.

## How it works
### What to profile in structured data
For each column: type conformance, null and empty rate, distinct count and cardinality, top values and
their frequencies, min and max, length distribution for text, and **format patterns**, the shape of
values after replacing digits and letters with placeholders (for example `9999-99-99` versus
`99/99/9999` for dates). Across columns and tables: candidate key uniqueness, referential integrity
(do child rows point to existing parents), functional dependencies you rely on, exact and near
duplicates, and freshness (how old is the newest record, how often does it change).

Two checks deserve to be called out because standard null counts miss them. **Disguised missing
values** are placeholders that are not null: `N/A`, `-`, `0`, `1900-01-01`, `unknown`, "see email". They
show up as a single value with suspiciously high frequency in the top-values list, or as a date
distribution with a spike on one day, and the real completeness of the field is the null rate *plus*
their share. A **sensitivity scan** (pattern and dictionary detection of emails, phone numbers, national
ID formats, card numbers, and names in free text) tells you which fields need redaction or tighter
handling before anything is indexed or sent to a model; free-text fields often hold identifiers that the
schema never declared.

### How big a sample
A sample can only report problems it contains. If a defect affects a share *p* of records, a random
sample of *n* contains at least one with probability `1 − (1 − p)^n`, which is about `1 − e^(−np)`. For a
0.1% defect, 2,000 rows catch it about 86% of the time and 5,000 rows over 99% of the time. The converse,
the "rule of three": if *n* rows show zero defects, the 95% upper bound on the true rate is roughly
`3 / n`, so a clean 5,000-row sample still allows up to about 0.06%. Apply this per slice, not overall: a
stratum with 200 sampled rows can hide a defect rate up to about 1.5%. This is why cheap metrics run on
the full table and the sample is reserved for checks that need human eyes or heavy compute.

### What to profile in document corpora
File type mix; share of PDFs with a usable text layer versus image-only scans; encrypted or corrupt
files; page counts and sizes; languages; exact duplicates by content hash and near duplicates by
shingling or MinHash (drafts, versions, forwarded copies); date range; and the share of boilerplate
(signatures, disclaimers, templates). These determine parsing routes, chunking and cost estimates.

### Slice everything
Compute every metric per meaningful slice (source system, region, year, department, document folder),
*because* aggregate numbers hide the problems: an overall 3% null rate can be 0% everywhere and 40% in
the one region the pilot serves.

### Assess against quality dimensions
Turn measurements into a judgement along standard dimensions, each tied to the use case:
- **Completeness:** are the fields the use case reads populated?
- **Validity:** do values conform to type, format and allowed sets?
- **Consistency:** do related fields and systems agree (a closed ticket with no close date)?
- **Uniqueness:** are entities represented once?
- **Timeliness:** is data fresh enough for the decisions it supports?
- **Accuracy:** does the data match reality? This one cannot be profiled; it needs a sample checked by
  the client's experts against ground truth, *because* a perfectly formatted wrong value passes every
  automated test.

### From findings to decisions
Each finding is classified as acceptable, fixable in the pipeline (normalize, deduplicate, route scans
to OCR), fixable only at the source (the client's data owner must act), or blocking (the use case
cannot work without it). The assessment document names an owner for each, and it is shared with the
client, because many fixes are theirs.

### From one-off profile to continuous checks
The thresholds discovered at onboarding become expectations evaluated on every load: null rate of
`loss_date` below 2%, `status` within the agreed set, row count within a band of yesterday's. Tools such
as Great Expectations or dbt tests express these declaratively. A failed check quarantines the batch or
alerts, *because* source-side changes (an upgrade, a new form, a renamed picklist) are a common cause of
sudden AI quality drops.

## Example
A compact profiler over a client's ticket export using pandas, sliced by region. It computes the
measurements that most often change an onboarding plan.

```python
import pandas as pd, re

def shape(v):  # format pattern: digits -> 9, letters -> A
    return re.sub(r"[A-Za-z]", "A", re.sub(r"\d", "9", str(v)))

def profile(df: pd.DataFrame, slice_col: str, key: str):
    report = {"rows": len(df), "dup_keys": int(df[key].duplicated().sum())}
    for col in df.columns:
        s = df[col]
        report[col] = {
            "null_pct": round(s.isna().mean() * 100, 1),
            "distinct": int(s.nunique()),
            "top": s.value_counts().head(5).to_dict(),
            "shapes": s.dropna().map(shape).value_counts().head(3).to_dict(),
        }
    report["null_pct_by_slice"] = (df.isna().groupby(df[slice_col]).mean() * 100).round(1)
    return report

df = pd.read_csv("tickets_sample.csv", dtype=str)
r = profile(df, slice_col="region", key="ticket_id")
print(r["dup_keys"], r["status"]["distinct"], r["opened_at"]["shapes"])
print(r["null_pct_by_slice"][["closed_at", "resolution_notes"]])
```

Illustrative output on a 5,000-row stratified sample: 212 duplicate `ticket_id` values (a past
migration loaded one year twice); `status` has 23 distinct values against 8 documented; `opened_at` has
two shapes, `9999-99-99 99:99` and `99/99/9999 99:99`, meaning one source system writes slash dates
whose order (US `MM/DD` or European `DD/MM`) the shape cannot reveal, so you check for values with a
first field above 12 and confirm with the source owner; and `resolution_notes` is 4% null overall but 37% null in the APAC slice. The assessment then
reads: deduplicate on key plus latest version (pipeline fix); crosswalk the 23 statuses with the support
lead (mapping); parse dates per source once the order is confirmed (pipeline fix); and for APAC, ask whether notes live in another
system, because a resolution assistant without resolutions is blocked there (source question). The
`top` entries are also where disguised nulls show: if "see email" is the most common
`resolution_notes` value, the field's effective completeness is far below what `null_pct` reports.

## Trade-offs
- Profiling a stratified sample is fast and cheap, but can miss rare problems; profile the full dataset
  for cheap metrics (counts, nulls, key uniqueness) and use samples for expensive ones such as near
  duplicate detection and manual accuracy review.
- Strict load-time checks stop bad batches before they reach users, but every false alarm blocks fresh
  data; start checks as warnings, learn their natural variance, then promote stable ones to blocking.
- Fixing quality in your pipeline is fast and under your control, but hides the problem from the client
  and must be maintained forever; for errors that originate at entry, pushing the fix to the source
  costs more time now and less over the life of the deployment.
- Automated metrics scale across tenants, but cannot measure accuracy; SME review of a sample is slow and
  expensive yet is the only way to learn whether correctly formatted data is actually right.

## Pitfalls
- Reporting only aggregate metrics; the symptom is a dataset judged healthy that fails badly for the
  one department or region the pilot serves.
- Profiling once at onboarding and never again; the symptom is a sudden quality drop months later traced
  to a source-side upgrade that renamed a picklist value.
- Counting only true nulls; the symptom is a field reported 98% complete whose most frequent value is
  `N/A` or `1900-01-01`, and prompts or filters that treat the placeholder as real data.
- Treating format validity as accuracy; the symptom is clean-looking data that SMEs reject because
  values are stale, defaulted or entered into the wrong field.
- Ignoring duplicates and near duplicates in document corpora; the symptom is retrieval returning five
  versions of the same policy and answers citing an outdated draft.
- Producing a profile report with no decisions or owners; the symptom is the same findings rediscovered
  in the pilot, after users have already seen the consequences.
- Copying production data to a laptop to profile it; the symptom is a security finding, because
  profiling exposes the most sensitive columns first; profile inside the approved environment.

## When to use
Profile every new source during onboarding before mapping or building, and again whenever a source
changes version, a new tenant joins on the same system, or AI quality drops without a model or prompt
change. Keep the profile tied to the use case: start from the fields and documents the product reads
and the slices users care about. Measure per-slice null rates, quarantine counts and check failures over
time. In customer conversations, a short written assessment with owners is often the most valuable
artifact of the first weeks. In interviews, distinguishing profiling (measurement) from assessment
(fitness for a purpose) is the point to make.

## Role lens
- **Engineer:** Writes the profiling job and the load-time expectations, slices metrics by source and
  region, and wires failed checks to quarantine and alerts instead of silent loads.
- **Architect:** Defines the quality dimensions and thresholds that gate production per use case, and
  decides which fixes belong in the pipeline versus at the client's source.
- **FDE:** Presents the assessment to the client's data owners, gets source-side fixes assigned, and
  arranges the SME sample review that measures accuracy.

## Check yourself
**Q:** Your RAG assistant's answer quality for one customer fell noticeably last month. No model,
prompt or code changed. How would you use profiling to investigate?
**A:** Compare the current data profile with the onboarding baseline, per slice. Check volumes first:
did a sync stop, or did ingestion double-load a period. Then per-field null rates, value sets and format
shapes on the fields retrieval and prompts use: a source upgrade may have renamed a status, moved notes
to a new field, or switched documents to scanned attachments with no text layer. Check duplicates and
freshness of the newest records. Any shift localized to a slice points to a source-side change. Then I
would add an expectation that would have caught it, so the next one quarantines a batch instead of
reaching users.

## Quiz
### Q: Why should profiling metrics be computed per slice (region, source, year) rather than only overall?
- [ ] Sliced metrics are cheaper to compute
- [x] Aggregates can hide a severe problem concentrated in one slice the use case depends on
- [ ] Data quality tools require a slice column
- [ ] Overall metrics are always biased upward
> A 4% overall null rate can be 0% in most regions and 37% in one; if the pilot serves that region, the
> aggregate leads to a wrong go decision.

### Q: Which quality dimension cannot be established by automated profiling alone?
- [ ] Completeness
- [ ] Validity
- [ ] Uniqueness
- [x] Accuracy
> Profiling checks presence, format and consistency, but a well-formed wrong value passes all of them.
> Accuracy needs comparison against ground truth, usually SME review of a sample.

### Q: A field is 30% null. What determines whether that is a problem?
- [ ] Whether the null rate exceeds an industry-standard threshold
- [ ] Whether the column is indexed in the source database
- [x] Whether the use case reads that field, for example as a retrieval filter or prompt input
- [ ] Whether the nulls are stored as empty strings or as NULL
> Quality is fitness for purpose. A sparse field the product never uses is irrelevant; the same rate on
> a field used for filtering or answering can block the use case.
