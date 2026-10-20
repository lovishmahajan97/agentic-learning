---
id: labeling-sessions
module: customer-data-evaluation
name: Labeling sessions
kind: practice
difficulty: intro
est_minutes: 12
status: fact-checked
related:
  - customer-data-evaluation/golden-dataset-creation-with-client-smes
  - customer-data-evaluation/sme-review-and-annotation-guidelines
  - customer-data-evaluation/eval-review-loops
  - evals/inter-annotator-agreement
  - training-adaptation/data-labeling
  - adoption/stakeholder-management
---

## TL;DR
A labeling session is a scheduled, facilitated block where a customer's SMEs label eval data together:
a short calibration, independent labelling, then structured discussion of disagreements whose outcomes
are written into the guideline. Sessions exist because asynchronous "please label when you can" requests
to busy experts stall, and because tacit rules surface only when experts explain disagreements live. The
facilitator's job is throughput and decisions, while protecting label independence.

## Why it exists
SMEs are the bottleneck of every customer evaluation. They are senior, booked, and labelling is not their
job. The default approach, sharing a spreadsheet or a labelling-tool link with a deadline, produces a
predictable curve: a burst of labels on day one, then a long tail, with the hardest items skipped
because nobody is there to answer "what do you mean by this category?" Weeks pass, and the eval set the
pilot depends on is half done and labelled by whoever had time.

Asynchronous labelling also loses the most valuable output of expert labelling, which is not the labels
themselves but the rules they reveal. When two adjusters disagree on a claim and explain why in the same
room, you learn the unwritten exception that decides it, and you can write it down while the policy
owner is present. In async mode that disagreement shows up weeks later as two numbers in a column.

Finally, sessions create accountability that a link cannot: a recurring calendar slot with a sponsor's
backing is a commitment the customer's managers can plan around. The naive alternative, a single long
workshop, fails too, because fatigue degrades label quality after an hour or two, and a single session
leaves no room to fix the guideline between rounds.

## How it works
### Before the session
1. **Prepare the batch.** Select items from the sampled pool, sized to the time available with buffer,
   and include a few previously adjudicated "gold check" items to detect drift in individual reviewers.
2. **Pre-load the tool.** Whatever the tool (Label Studio, Argilla, the customer's own platform or a
   controlled spreadsheet), it must open on the item, show the guideline beside it and capture label,
   rationale and reviewer. Fiddling with access during the session wastes the scarcest time.
3. **Clear access.** Confirm every SME can open the data in the environment governance allows, the day
   before, not at minute one.
4. **Share the current guideline** and a one-page decision card.

### During the session
1. **Warm-up calibration (10–15 minutes).** Everyone labels the same handful of items silently, then
   reveals. This re-anchors reviewers to the guideline *because* standards drift between sessions.
2. **Independent labelling (the bulk).** Reviewers work alone on assigned items, with a fraction
   double-assigned for agreement. No discussion yet, *because* talking first turns the loudest opinion
   into the label.
3. **Disagreement review.** The facilitator shows double-labelled items where reviewers split, asks each
   to explain, and drives to one of three outcomes: a guideline clarification, an escalation to the
   policy owner, or "genuinely ambiguous" (flagged and excluded from scoring).
4. **Decision log.** Each outcome is written into the guideline changelog on the spot, with the item IDs
   it affects, so relabelling can be targeted later.

### After the session
Adjudicate remaining splits, relabel items affected by guideline changes, recompute agreement, and send
the sponsor a short note: items labelled, agreement, decisions made, open questions. That note is what
keeps the next session on the calendar.

### Facilitation choices
Keep sessions to about 60–90 minutes, *because* label quality decays with fatigue. Have an engineer
present to answer "what is the system actually seeing?" questions. Do not show model outputs as
pre-labels in calibration batches; pre-labels anchor reviewers, and if you use them later to save time,
measure how often reviewers change them.

## Example
A 90-minute weekly session for a contract-review pilot with three in-house lawyers (all timings and rates
illustrative).

**Prep:** 120 clause-extraction outputs are loaded, 30 of them assigned to two lawyers each, plus 3 gold
check items from last week's adjudication. Access was tested the previous afternoon; one lawyer's
account lacked permission and was fixed then.

**0–15 min, calibration:** all three label 5 clauses silently. On one, a limitation-of-liability clause
with a carve-out for confidentiality breaches, two mark "cap correctly extracted" and one marks "fail,
carve-out omitted". The guideline did not say whether carve-outs are part of the cap. The general counsel
(present for this slot) rules that carve-outs must be extracted, and the facilitator adds rule L4 and an
example.

**15–65 min, independent labelling:** at a little under a minute per item per lawyer, each completes
about 53 items, which covers the 150 assignments (120 items plus 30 doubles) and the 3 gold-check items
every lawyer labels, with no slack.

**65–85 min, disagreement review:** 6 of the 30 double-labelled items split. Four trace to the meaning of
"auto-renewal" when notice periods differ; this becomes an escalation for the general counsel, due before
next week. Two are slips by the same lawyer, who also missed one of the three gold-check items, so
that lawyer's batch gets a second look.

**85–90 min, wrap:** the facilitator reads back two decisions and one open question. The follow-up note
reports 120 items labelled, 24 of 30 doubles in agreement, rule L4 added, 9 earlier items flagged for
relabel under L4.

## Trade-offs
- Live sessions get decisions and throughput, but demand synchronous calendars; when SMEs are across
  time zones, run async labelling with a short live disagreement review instead.
- Having the policy owner attend resolves splits immediately, but their presence can make reviewers defer
  to them; have them join only for the disagreement segment.
- Pre-labelling with model outputs can raise throughput substantially, but anchors reviewers toward the model; use it
  only after calibration, and track the change rate to detect rubber-stamping.
- Longer sessions label more per calendar booking, but quality drops with fatigue; when gold-check
  accuracy falls in the second half, shorten the session.

## Pitfalls
- Spending the first twenty minutes on access and tool problems; the symptom is a session that labels a
  third of its planned batch and a sponsor questioning the time.
- Discussing items before reviewers commit labels; the symptom is near-perfect agreement in session that
  is not reproduced when the same reviewers label alone.
- Making decisions without writing them down; the symptom is the same disagreement being debated again
  two sessions later.
- Not sending a summary to the sponsor; the symptom is SMEs being pulled into other work and sessions
  quietly cancelled.
- Ignoring gold-check items; the symptom is one reviewer's labels drifting for weeks unnoticed and
  dragging down agreement.

## When to use
Run labeling sessions whenever SME labels are on the critical path of a pilot: building the golden set,
calibrating an LLM judge, or reviewing a new failure category. A weekly recurring slot is usually better
than an occasional workshop because it allows guideline fixes between rounds. Pure async labelling is
fine for stable, well-calibrated tasks with high agreement. Measure items per session, agreement on
doubles, gold-check accuracy, unsure rate and decisions logged. In interviews, describe how you keep
labels independent while still extracting rules from disagreement.

## Role lens
- **Engineer:** Prepares batches, gold checks and double assignments, makes sure the tool captures
  rationale and guideline version, and computes agreement immediately after the session.
- **Architect:** Chooses labelling tooling and where it runs under the customer's data constraints, and
  designs how session decisions flow into guidelines, judge prompts and datasets.
- **FDE:** Books and facilitates sessions, keeps the sponsor informed, steers disagreements to decisions,
  and protects SME time from tooling and access problems.

## Check yourself
**Q:** Your sessions label plenty of items but agreement on double-labelled items keeps falling week over
week. What might be wrong and what would you change?
**A:** Falling agreement with steady throughput suggests drift rather than a bad guideline: reviewers'
private standards diverging, new item types in the batches that the guideline does not cover, or fatigue.
I would check gold-check accuracy per reviewer to find an individual drifting, look at which guideline
sections are cited in recent disagreements to find uncovered cases, and compare agreement in the first
versus second half of sessions for fatigue. Fixes would be a longer warm-up calibration, guideline
sections plus examples for the new case types, and shorter sessions if quality decays late.

## Quiz
### Q: Why start each labeling session with a short silent calibration on shared items?
- [ ] To measure how fast each reviewer works
- [x] To re-anchor reviewers to the guideline and surface new ambiguities before the bulk of labelling
- [ ] To generate pre-labels for the rest of the batch
- [ ] To test whether the labelling tool is working
> Standards drift between sessions. A few shared items labelled silently, then revealed, show where
> reviewers diverge so the guideline can be clarified before hundreds of labels inherit the split.

### Q: What is the main risk of showing model outputs as pre-labels to SMEs?
- [ ] It slows the session down
- [ ] It exposes customer data to the model
- [ ] It makes agreement statistics impossible to compute
- [x] Reviewers anchor on the pre-label, so labels drift toward the model's view
> Anchoring makes reviewers accept plausible model outputs they would have failed unaided. If
> pre-labels are used for throughput, measure how often reviewers change them.

### Q: During disagreement review, two SMEs split for a reason the guideline does not cover. What is the best outcome?
- [ ] Take the more senior SME's label
- [ ] Average the labels and move on to save time
- [ ] Remove the item permanently from the dataset
- [x] Record a guideline decision or escalation with affected item IDs so labels can be updated
> The value of the session is turning splits into written rules. Logging the decision with item IDs
> lets the team relabel precisely and stops the same debate recurring.
