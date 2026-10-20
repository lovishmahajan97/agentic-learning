---
id: knowledge-management
module: data-architecture
name: Knowledge management
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - data-architecture/document-processing-pipelines
  - data-architecture/ingestion-pipelines
  - rag-retrieval/freshness-document-deletion-index-consistency
  - rag-retrieval/acl-aware-retrieval
  - rag-retrieval/metadata-filtering
  - rag-retrieval/retrieval-failure-diagnosis
  - enterprise-integration/data-ingestion-from-client-systems
  - rag-retrieval/citations-grounding
---

## TL;DR
A retrieval system cannot answer better than the corpus it searches, and most enterprise corpora are
stale, duplicated, contradictory and unowned. Knowledge management is the practice of deciding which
sources are canonical, who owns each, how documents enter, age and leave the index, and what metadata
travels with them, so that the retriever is choosing among correct candidates. In enterprise RAG, a
large share of "the model hallucinated" reports trace back to this layer, not to the model.

## Why it exists
Point a crawler at a company wiki, a ticketing system and a shared drive, embed everything, and you get
an index where the question "what is the refund window" matches a 2019 policy page, a 2023 revision, a
draft that was never approved, a Slack export of someone guessing, and three copies of the same PDF with
different file names. The retriever ranks them by similarity, which has nothing to do with correctness or
currency. The model then synthesizes an answer from a mixture and the output is confidently wrong.
Improving the embedding model, the reranker or the prompt does not fix this: all of them operate on
candidates, and the candidates are the problem.

The naive alternative is "index more, the model will sort it out". It fails because the model has no
signal that one chunk is authoritative and another is deprecated unless that signal is carried in the
data. Larger context windows make it worse, not better: more contradictory passages fit, and the model's
choice between them is arbitrary.

The second naive alternative is a one-time cleanup. It fails because corpora drift continuously: pages are
edited, products are renamed, people leave and their documents go unmaintained. Without ownership and
lifecycle rules, the cleaned corpus decays back within months. Knowledge management turns a one-off
cleanup into a standing process with owners, metadata and feedback loops.

## How it works
### Decide what is canonical
For each class of question, name the system of record. Pricing comes from the catalogue, not from sales
decks that quote it. Policy comes from the policy repository, not from support macros that paraphrase it.
This matters *because* retrieval needs a way to break ties between similar passages, and "which source is
authoritative for this topic" is the tiebreaker humans use. Encode it as a `source_rank` or `authority`
field on every chunk so the retriever or reranker can prefer canonical sources and the generator can cite
them. Secondary sources are still useful for recall, but they should lose to primaries on conflict.

### Assign ownership and freshness expectations
Every indexed source gets an owner (a team, not a person who may leave) and a review cadence. The owner's
job is to confirm the content is still correct or to retire it. Record `last_verified_at` separately from
`last_modified_at`: a document untouched for two years may be perfectly current, and one edited yesterday
may be a typo fix on an obsolete page. Freshness scoring in retrieval should use verification, not
modification, where the distinction exists.

### Give documents a lifecycle
Model the states explicitly: draft, published, deprecated, archived. Only published content enters the
index by default. Deprecated content either leaves the index or carries a flag that the prompt surfaces
("this page is marked superseded by X"). Archived content is removed. The lifecycle lives in the source
system where possible (a wiki label, a status field) and is read by ingestion, *because* content owners
work in the source tool, not in the vector database, and a state they cannot see will not be maintained.

### Deduplicate and canonicalize
Near-duplicates waste context slots and make the retriever look confident when it has really found the
same passage three times. Exact duplicates are caught by content hashing at ingestion. Near-duplicates
(the same PDF exported twice with different headers) need a similarity threshold or a MinHash pass, and a
rule for which copy survives, usually the one in the canonical source. Keep the mapping so that
retrieval hits on the discarded copy can be redirected to the surviving one.

### Carry metadata that the retriever can act on
At minimum: source system, owner, authority rank, lifecycle state, verified date, audience or access
scope, product or domain tags, and language. These are what metadata filters and rerankers use to turn
"similar" into "relevant to this user now". Metadata that is not populated at ingestion is never
populated, so the ingestion contract should reject documents missing required fields rather than
defaulting them.

### Close the loop from failures to content
Retrieval failures are the best signal about the corpus. When users thumbs-down an answer or an eval
query misses, classify the cause: missing document, stale document, duplicate crowding out the right
one, or a genuine retrieval failure. Route the first three to the content owner with the query attached.
This is the mechanism that keeps the corpus healthy over time, and it is cheap once ownership exists.
Without it, the same gap is rediscovered by every user who hits it.

### Access and tenancy are part of knowledge, not an afterthought
A document's permissions are knowledge about who may see it. Ingestion must capture ACLs from the source
and retrieval must filter on them; a corpus that is correct but leaks HR pages to engineering is a
failed deployment. Treat access scope as a required metadata field like any other.

## Example
A worked scenario with illustrative numbers. An internal support assistant for a software company is built
over three sources: the product documentation site (about 4,000 pages), the support team's Confluence
space (about 9,000 pages accumulated over six years) and a Google Drive folder of PDFs (about 2,500
files). Everything is chunked and embedded; the eval set of 300 support questions scores 58 percent
"answer correct" when judged by the support leads.

Failure analysis on the 126 wrong answers finds: 51 cite a Confluence page that was superseded by a newer
one still in the index; 23 blend numbers from a PDF export of an old price list with the current docs;
19 retrieve near-identical chunks from three copies of the same onboarding guide, pushing the actually
relevant page out of the top five; 14 answer from an unapproved draft; 19 are genuine retrieval or
generation problems.

The team does not change the model. They declare the documentation site canonical for product behaviour
and the pricing catalogue canonical for prices, and give both `authority=1`; Confluence gets
`authority=2`. Confluence pages get a lifecycle label; the support leads spend two afternoons marking 3,100
pages deprecated and 800 archived, which ingestion now respects. Content hashing plus a near-duplicate
pass removes about 1,900 chunks. Price-list PDFs are excluded entirely because the catalogue API is now
ingested directly. The reranker is given `authority` and `last_verified_at` as features.

Re-running the eval gives 81 percent correct (243 of 300), so the content fixes recovered 69 of the 107
content-caused failures. Of the 57 that remain, 19 are the genuine retrieval or generation problems, which
now get attention as a clean, isolated bucket. The rest are content issues the first pass missed (pages
superseded but not yet labelled, near-duplicates below the dedup threshold) and questions whose answers
exist nowhere in writing, which are logged as content requests to the docs team. A weekly report now lists deprecated pages still receiving retrieval hits and
unanswered question clusters, owned by the support leads.

## Trade-offs
- Declaring canonical sources improves correctness on conflicts but suppresses useful secondary context;
  when the canonical source is thin, you may need secondaries to win on recall and lose only on conflict.
- Strict lifecycle gating (only published content indexed) keeps the corpus clean but hides work in
  progress; for engineering teams that live in drafts, indexing drafts with a visible flag can be better.
- Aggressive deduplication frees context slots and sharpens ranking, but a threshold set too low merges
  legitimately different versions, so the choice depends on how much the corpus versions in place.
- Ownership and review cadences make the corpus trustworthy, but they cost owner time every cycle; if
  nobody will do the reviews, a shorter freshness-based demotion is more honest than a fake cadence.
- Rich required metadata improves filtering, but every required field is a reason for a document to be
  rejected at ingestion; keep the required set small and the optional set large.

## Pitfalls
- Treating a bad answer as a model problem and iterating on prompts; the symptom is eval scores that
  move a point or two per week while the same stale pages keep getting cited.
- Indexing everything a connector can reach because recall seems free; the symptom is top-k results
  dominated by duplicates and drafts and a context window full of contradictory passages.
- Using modification date as freshness; the symptom is a correct, stable policy page demoted below a
  recently edited but obsolete one.
- Storing lifecycle state only in the vector database; the symptom is owners marking pages obsolete in
  the wiki while the index keeps serving them because nobody re-ran the cleanup.
- Skipping ACL capture at ingestion to ship faster; the symptom is a security finding or a user seeing a
  page they could never open in the source system.
- No feedback path from users to content owners; the symptom is the same gap reported many times with
  nobody positioned to fix it.

## When to use
Knowledge management is required for any RAG system over content that people maintain, which is nearly
all of them; it can be skipped only for corpora that are effectively immutable and single-sourced, such
as a frozen archive of closed case files or a product's own generated logs. Start it before the first index, since the ingestion schema must carry the metadata.
Measure: share of eval failures attributed to content versus retrieval versus generation, count of
deprecated pages still receiving hits, duplicate ratio in top-k, and median `last_verified_at` of cited
chunks. In day-to-day work it shows up as the ingestion contract and the weekly content report. In
interviews, "how would you improve a RAG system scoring 60 percent" should start with failure analysis of
the corpus, not with a bigger model.

## Role lens
- **Engineer:** Builds ingestion that reads lifecycle and ACL state from source systems, enforces required
  metadata, deduplicates, and exposes authority and verification fields to the reranker.
- **Architect:** Decides which systems are canonical for which domains, defines the metadata schema every
  connector must satisfy, and sets the ownership and review model with the business.
- **FDE:** Runs the failure analysis with the customer's subject-matter experts, negotiates who owns which
  content, and turns "the bot is wrong" into a prioritized content backlog.

## Check yourself
**Q:** A customer's RAG assistant is scoring poorly, and they want to switch to a larger model. What do you
do first, and how do you argue for it?
**A:** Before touching the model, sample the failures and classify each by cause: missing document, stale or
superseded document, duplicates crowding out the right chunk, access-filtered content, or a true retrieval
or generation error. In enterprise corpora the first three often dominate, and no model fixes them because
the model only sees the candidates it is given. Show the customer the breakdown with concrete cited pages:
"this answer came from a 2021 page that your own wiki marks obsolete". Then propose the fixes that address
the largest buckets: canonical sources with authority ranks, lifecycle labels read by ingestion,
deduplication, and a feedback loop to content owners. Re-run the eval after those changes; if genuine
retrieval or generation errors remain the largest bucket, that is when a model or retriever change is
justified, and the eval now isolates its effect.

## Quiz
### Q: Why does a larger context window not fix a corpus with contradictory documents?
- [ ] Larger windows are slower, so fewer documents can be retrieved
- [x] More contradictory passages fit, and the model has no signal about which one is authoritative
- [ ] Embedding models cannot embed documents longer than the window
- [ ] The reranker discards documents beyond the first few thousand tokens
> The problem is the absence of an authority signal, not capacity. Fitting more passages increases the
> number of conflicting claims the model must choose between, and without metadata indicating which source
> is canonical, its choice is arbitrary.

### Q: Which field is the better basis for freshness in retrieval, and why?
- [ ] last_modified_at, because recent edits indicate active maintenance
- [x] last_verified_at, because an owner confirming correctness is different from any edit having happened
- [ ] created_at, because older documents are more established
- [ ] indexed_at, because it reflects when the chunk entered the system
> A stable policy page may be unedited for years yet fully current, while an obsolete page may get a typo
> fix yesterday. Verification captures what freshness actually means; modification only captures activity.

### Q: Where should a document's lifecycle state (published, deprecated, archived) be maintained?
- [ ] In the vector database metadata, since that is what retrieval reads
- [ ] In a spreadsheet maintained by the AI team
- [x] In the source system where content owners work, read by ingestion
- [ ] In the prompt, as a list of deprecated page titles
> Content owners maintain state where they already work. A state stored only in the index or a side
> spreadsheet drifts immediately because the people who know a page is obsolete never see it. Ingestion
> propagates the source's state into the index.
