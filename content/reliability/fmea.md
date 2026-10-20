---
id: fmea
module: reliability
name: FMEA (Failure Mode and Effects Analysis)
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - reliability/chaos-engineering-and-resilience-testing
  - reliability/fault-tolerance
  - reliability/slas-slos
  - delivery-ownership/failure-analysis
  - delivery-ownership/root-cause-analysis-and-postmortems
  - security-governance-compliance/threat-modeling
  - security-governance-compliance/ai-risk-management
  - scoping/dependency-and-risk-management
sources:
  - AIAG & VDA, 2019 — FMEA Handbook
---

## TL;DR
FMEA is a structured, tabular way to enumerate how each part of a system can fail (failure modes), what
happens when it does (effects), why (causes), how you would notice (detection), and to rank each by
Severity × Occurrence × Detection so the worst risks are fixed first. It comes from safety engineering.
For AI systems it is the cheapest way to find the failures nobody is monitoring: confident wrong answers,
silently stale retrieval, tool calls that succeed against the wrong record. It is a postmortem written
before the incident.

## Why it exists
Most teams discover failure modes one incident at a time. Postmortems are reactive by definition, and
chaos experiments only test hypotheses you already hold. Something has to generate the hypotheses, and
"we will add monitoring and handle issues as they come" generates them at the worst possible moment.

This is especially weak for AI systems because their worst failures are silent. The service returns
200 OK with a plausible, wrong answer. The embedding index stops refreshing and retrieval drifts stale
over weeks. An agent's tool call succeeds, against the wrong customer's record. Availability monitoring
sees a healthy system throughout. Intuition does not help either: engineers rank failures by vividness,
so "the provider goes down" gets a failover project while "the tenant filter is missing on the new
index" gets nothing, even though the second is catastrophic and the first is a nuisance with a retry.

FMEA counters both problems mechanically. Walking the request path component by component with a
checklist of ways to fail forces attention onto boring, high-consequence modes. Scoring detection
separately from likelihood and severity surfaces the key asymmetry: a failure you cannot see is more
dangerous than one that pages you, because its effect compounds until a customer reports it. The output
is not a document but a ranked backlog: the top rows become guards, evals, alerts and chaos experiments,
in that order of priority.

## How it works
### Decompose the system into functions
List the steps in the request path and the cross-cutting concerns. For a retrieval-augmented agent:
ingestion, chunking, embedding, index refresh, query embedding, retrieval, tenant scoping, prompt
assembly, model call, output parsing and validation, tool execution, response delivery; plus
authentication, rate limiting, logging and the deployment process itself. Granularity should match the
decisions you can act on; "the backend" is too coarse, individual functions in a file too fine.

### Enumerate failure modes per function
A failure mode is *how* the function fails, stated as an observable deviation, not as "it breaks". Use
guide words to force breadth, borrowed from hazard analysis: none (no output), more, less, late, early,
wrong, partial, stale, duplicated, to the wrong recipient, without authorization. Applied to retrieval
this yields "returns nothing", "returns too many chunks and truncates the prompt", "returns stale
documents", "returns another tenant's documents", "returns the right documents too slowly". A team that
only lists "retrieval is down" has not done the step.

### Effects and Severity (S)
Describe the effect at the user or business level, then score 1 to 10. A usable scale for software:
9 to 10 for irreversible or legal harm (money moved, data leaked, unsafe advice acted on), 6 to 8 for a
wrong result the user is likely to act on, 3 to 5 for visible degradation or a failed request, 1 to 2
for cosmetic issues. Anchor each number with a written example so two raters agree.

### Causes and Occurrence (O)
For each mode list the mechanisms that produce it, and score how often, 1 (practically never) to 10
(constant). Use data where it exists: provider error rates, eval failure rates, how often the index job
has failed historically. Guesses are allowed but must be marked as guesses, because O is the number
most often wrong.

### Current controls and Detection (D)
What exists today that would catch the failure *before* its effect reaches the user, and how likely it
is to do so. Scale 1 (certain: a type check fails the build) to 10 (no way to detect). Note the
inversion: a high D is bad. "We have logs" scores 8 or 9 unless someone is alerted by them, because
logs nobody reads detect nothing.

### Risk Priority Number and its limits
The classic ranking is RPN = S × O × D, from 1 to 1,000, sorted descending. It is useful because it is
simple, and misleading in two specific ways. First, it treats the three dimensions as interchangeable,
so a rare, hard-to-detect catastrophe (S 10, O 1, D 9 gives 90) ranks below a frequent, moderately
visible annoyance (S 4, O 6, D 5 gives 120), and rows with identical RPNs can carry completely different
risks (S 10, O 2, D 5 and S 5, O 4, D 5 are both 100). Second, the scales are ordinal: a 6 is "worse than
a 5", not 20 percent worse, so multiplying them produces a number with no physical meaning, and only 120
distinct values between 1 and 1,000 can occur at all, which makes gaps between RPNs look larger than the
judgment behind them. The automotive industry's 2019 handbook replaced RPN with an Action Priority table
that looks at severity first, then occurrence, then detection, for these reasons. The practical rule:
any row with severity 9 or 10 goes to the top regardless of RPN, then sort the rest by RPN.

### Actions, owners and re-scoring
Each top row gets an action that reduces one of the three factors. Prevention lowers O (a schema that
makes the bad input impossible). Containment lowers S (a confirmation step before an irreversible tool,
a spending cap). Detection lowers D (an eval in CI, a freshness alert, a canary query). Assign an owner,
re-score after the action, and revisit the table on every architectural change. A score that never
changes is a sign the table has become decoration.

### Design versus process FMEA
Design FMEA analyzes the system as built. Process FMEA analyzes how it is operated and changed: deploys,
prompt edits, model version upgrades, index rebuilds, credential rotation. AI systems need both, and the
process rows often score highest, because a prompt edit or a model version change can alter behavior
everywhere at once without touching code, and without an eval gate nothing stands between the edit and
production.

### Bottom-up, one failure at a time, and what that misses
FMEA is bottom-up: it starts from components and asks what each failure does to the whole. Its blind
spot follows from that: each row considers a single failure mode, so combinations (the index is stale
*and* the freshness alert's job is down; the provider is slow *and* retries double the load) are easy to
miss. Fault tree analysis is the complementary top-down method: start from one unacceptable outcome
("money refunded to the wrong customer") and decompose it with AND/OR gates into the combinations of
events that produce it. Use FMEA to find the breadth of modes, and a small fault tree for each
severity-10 effect to check that no single failure, or common pair, reaches it.

## Example
A fragment of an FMEA for a support assistant that answers from a document index and can issue refunds
through a tool. Scores are the team's judgment calls on a 1 to 10 scale; the point is the ranking, not
the precision.

| Function | Failure mode | Effect | S | Cause | O | Current detection | D | RPN |
|---|---|---|---|---|---|---|---|---|
| Index refresh | Serves stale policy docs | Agent quotes outdated refund terms; customer promised the wrong amount | 7 | Nightly rebuild job fails silently | 4 | Job logs only, no alert | 9 | 252 |
| Refund tool | Refund issued against the wrong order | Money moved irreversibly | 10 | Model copies a similar-looking order id from context | 3 | None before execution | 8 | 240 |
| Tenant scoping | Retrieval returns another tenant's documents | Data leak across customers | 10 | Filter omitted in a new retrieval client | 2 | None | 9 | 180 |
| Model call | Provider timeout or 5xx | Request fails; user sees an error and retries | 4 | Provider incident | 5 | Error-rate alert pages on-call | 2 | 40 |
| Output parsing | Response violates JSON schema | Reply dropped, user sees generic error | 3 | Prompt edit or model drift | 4 | Parser raises; counted and alerted | 3 | 36 |

The provider outage, the failure everyone had been planning around, ranks last: it is loud, so it is
already detected and handled. The three rows that matter are quiet. The actions follow directly. For
stale documents, add a freshness SLI (age of newest indexed document) with an alert: D drops from 9 to
3 and the RPN to 84. For the refund tool, require the order id to be passed from the ticket's structured
field rather than generated from free text, and add a confirmation step above a value threshold: O
drops to 1, D to 2, and the RPN to 20, while S stays 10 because the harm, if it happens, is unchanged.
For tenant scoping, make the tenant filter mandatory in the retrieval client's constructor and run an
automated cross-tenant canary query hourly: D drops to 2 and the row, still severity 10, is now
contained. The team spent one afternoon and re-prioritized a quarter.

## Trade-offs
- Breadth versus time: a full FMEA across every component takes days, while a two-hour session over
  the request path finds most of the high-risk rows. Go deep when the system takes irreversible actions
  or carries regulatory exposure; stay shallow for an internal tool with a human in the loop.
- Numeric scores look objective but encode judgment; consistency between raters matters more than
  precision. With a small team, anchored scales or even high/medium/low buckets beat spurious decimals.
- RPN gives one sortable number but hides rare catastrophes; severity-first ordering protects against
  catastrophe but can leave frequent, irritating failures unaddressed. Use severity gates first, RPN
  second.
- A living table stays useful across architecture changes but needs an owner and a review cadence; a
  one-off exercise is cheap but stale after the first major change, and a stale FMEA creates false
  confidence.

## Pitfalls
- Listing only "component unavailable" modes. The symptom: the table reads like an uptime checklist and
  has no rows for wrong, stale, partial or cross-tenant output, which is where AI systems actually fail.
- Scoring detection as good because "we log it". The symptom: incident reviews that find the signal was
  sitting in logs for days before a customer reported the problem.
- Running the analysis alone. The symptom: the modes that bite live in areas the author does not own,
  such as the data pipeline, the vendor's change process or the customer's upstream system.
- Treating it as a compliance artifact. The symptom: RPNs never re-scored, the action column empty, and
  the file last modified the week before an audit.
- Scoring occurrence from intuition when data exists. The symptom: provider 429s scored as rare while
  the dashboard shows them daily, so the row is deprioritized and the retry budget never gets fixed.
- Treating rows as independent. The symptom: every row has a control, yet an incident happens when two
  controls fail together, for example the canary and the alert both running on the same scheduler that
  stopped.
- Confusing cause with mode. The symptom: rows like "engineer forgot the filter", which cannot be
  detected or scored for effect; the mode is "retrieval returns another tenant's data", the forgotten
  filter is one of its causes.

## When to use
Run an FMEA before launching anything with tool side effects or external users, before adding any
irreversible tool to an agent, after a serious incident to find its siblings, and whenever you need to
decide what to monitor or what to chaos-test: the D column is a monitoring backlog and the top rows are
your first experiments. Skip it for throwaway prototypes, and never let it substitute for testing; it
tells you where to test. In day-to-day work it shows up as a two-hour workshop with a spreadsheet and
the people who own each stage. In interviews the question is "how would you figure out what could go
wrong with this agent"; walk the request path with guide words, score a few rows aloud, and name the
top three with their mitigations.

## Role lens
- **Engineer:** Enumerates failure modes for the stages they own, scores them with data from dashboards
  and evals, and turns the top rows into guards, regression evals and alerts.
- **Architect:** Owns the system-level table across teams and vendors, sets the severity scale, and
  decides what severity-10 rows require by policy: confirmation steps, spending caps, kill switches.
- **FDE:** Runs a joint FMEA with the customer before go-live, because the customer knows the business
  effects and their legacy systems' failure modes; the result becomes the hypercare monitoring plan.

## Check yourself
**Q:** You are prioritizing reliability work for an agent that reads a customer's CRM and drafts emails
that a human approves before sending. One engineer wants to spend the sprint on multi-provider failover.
Using FMEA reasoning, how do you evaluate that proposal?
**A:** Score the row it addresses. Provider outage: severity about 4, since a human is waiting and can
retry and nothing irreversible happens; occurrence from the provider's history, perhaps 4; detection 2,
because error-rate alerts already fire. RPN around 32. Now score the quiet rows. "Agent reads the wrong
customer's record and drafts an email containing their data": severity 9 if the approver misses it
(personal data sent to the wrong recipient), occurrence 3 given lookups by fuzzy name, detection 8
because nothing checks that the retrieved record matches the recipient. RPN around 216, and it is a
severity-9 row, so it leads regardless. "CRM cache serves stale data" lands in between. The right sprint
is a cheap guard that asserts the recipient's account id equals the retrieved record's id, an eval that
exercises near-duplicate names, and a freshness alert on the cache; failover can follow once the
catastrophic, undetected rows are contained. Show the table so the decision is about numbers, not
opinions.

## Quiz
### Q: What does the Detection score measure, and which direction is bad?
- [ ] How quickly the team responds after an incident is declared; low is bad
- [x] How likely current controls are to catch the failure before its effect reaches the user; high is bad
- [ ] How many monitoring dashboards exist for the component; low is bad
- [ ] How severe the failure is when it is finally noticed; high is bad
> Detection rates the controls that exist today, from 1 (certain to catch it) to 10 (no way to notice).
> The inversion trips people up: a high D multiplies the risk, which is why "we have logs" should not be
> scored as good detection unless someone is alerted by them.

### Q: Why can sorting purely by RPN mislead, and what is the standard correction?
- [ ] RPN ignores occurrence, so frequent failures are under-ranked; weight occurrence double
- [x] RPN treats the factors as interchangeable, so a rare undetectable catastrophe can rank below a frequent cosmetic bug; gate on severity first, then sort by RPN
- [ ] RPN only works for hardware, so software teams should use mean time between failures instead
- [ ] RPN overweights detection, so detection should be dropped from the formula
> S 10 × O 1 × D 9 is 90 while S 4 × O 6 × D 5 is 120, yet the first row, a rare catastrophe nobody would
> notice, is the one that ends the company. Severity-first ordering, as in the Action Priority approach,
> keeps catastrophic rows at the top; doubling a weight only moves the distortion elsewhere.

### Q: Which of these is correctly stated as a failure mode rather than an effect or a cause?
- [ ] The engineer forgot to add the tenant filter to the new retrieval client
- [ ] A customer sees a competitor's pricing document in their answer
- [x] Retrieval returns documents belonging to another tenant
- [ ] The vector database was upgraded last week
> A mode is the observable deviation in the function's output. The forgotten filter and the upgrade are
> causes; the customer seeing the document is the effect. Keeping the three apart is what makes
> occurrence, severity and detection scorable.
