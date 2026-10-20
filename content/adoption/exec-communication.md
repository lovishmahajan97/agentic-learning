---
id: exec-communication
module: adoption
name: Exec communication
kind: practice
difficulty: core
est_minutes: 12
status: fact-checked
related:
  - adoption/stakeholder-management
  - adoption/adoption-and-value-realization-measurement
  - adoption/change-management
  - scoping/roi-model
  - scoping/success-metrics-kpis
  - scoping/demos
  - strategy/stakeholder-alignment
sources:
  - Minto, 1987 — The Pyramid Principle
---

## TL;DR
Executive communication means giving a busy decision-maker the conclusion first, in business terms, with
the decision you need from them and the evidence behind it, in a form they can absorb in minutes. It
matters for AI because executives fund and protect projects based on a few short updates, and
probabilistic quality, technical jargon or late bad news can turn a healthy project into a cancelled one.

## Why it exists
Executives allocate money, people and political cover across many initiatives at once. They see your
project for a few minutes a week, usually between other meetings, and decide from that whether to keep
funding it, expand it, or intervene. Their attention, not your work, is the bottleneck.

Engineers default to chronological, bottom-up reporting: what we did, what we found, and finally what it
means. That order mirrors how the work happened, but forces the reader to do the synthesis, and the
conclusion often arrives after their attention has gone. Worse, technical framing ("retrieval recall@10
improved from 0.71 to 0.83") leaves the executive unable to tell whether this is good news for the
business.

AI makes it harder in three ways. Quality is a rate, not a yes or no, so "it works" and "it fails" are both
misleading; executives need the error rate translated into cases, cost and risk. Results are uncertain
early, so honest ranges beat false precision. And AI projects carry reputational and compliance risk, so
bad news that arrives late destroys trust faster than the bad news itself. The naive alternatives,
dumping the full status report or polishing everything green, both fail: one is not read, the other is
believed until it is not.

## How it works
### 1. Lead with the answer
Put the conclusion and any ask in the first two lines: bottom line up front (BLUF). Minto's Pyramid
Principle gives the structure: state the governing point, support it with a few grouped arguments, support
each with evidence. *Because* the reader may stop at any point, each level should stand on its own.
SCQA (situation, complication, question, answer) is a useful way to frame the opening when context is
needed: what is true, what changed, what question that raises, what you recommend.

### 2. Speak in business outcomes and baselines
Translate model metrics into the KPI the executive owns, against the baseline measured before deployment:
"average handling time on long claims is down from 42 to 31 minutes" rather than "summary ROUGE improved".
*Because* the executive's question is always "is this worth it", every number should connect to value,
cost or risk.

### 3. Make uncertainty and error rates concrete
Express quality per volume and with consequence: "about 3 in 100 drafts need a material correction; all
are caught at human review; zero reached customers this month". Give ranges when the sample is small,
and say what would change the estimate. *Because* executives make risk decisions, they need to know the
size and containment of the failure, not just the average.

### 4. Ask for a decision explicitly
When you need something, state the decision, the options, your recommendation, the cost of each, and the
date by which it must be made. *Because* "FYI" items get read and forgotten, an update without a clear ask
cannot unblock you.

### 5. No surprises: escalate early, pre-wire big messages
Report risks while they are still yellow, with a mitigation. Before a steering meeting where you will
deliver difficult news, brief the sponsor one-to-one, *because* executives react badly to learning
something in front of peers that they could have known earlier.

### 6. Fix the format and cadence
A one-page update with a stable structure (headline, status with trend, KPI versus baseline and target,
top risks, decisions needed) lets the reader compare week to week. Keep the detail in an appendix for
those who want it.

## Example
A hypothetical engineer drafts a steering update for the VP of Operations about an AI claims-summary
pilot:

"This sprint we migrated to a new embedding model, rebuilt the index, fixed a PDF parsing bug affecting
scanned files and ran the eval suite. Recall@10 went from 0.71 to 0.83. We are also seeing some issues
with handwritten notes. Usage is around 60%. Next sprint we will look at OCR options."

The VP cannot tell whether the pilot is on track, what the numbers mean or what is being asked. The
rewrite, using the pyramid structure:

"**Bottom line:** the pilot is on track to hit its target, and I need a decision on OCR budget by 3
November.
**Value:** handling time on long claims is down from 42 to 31 minutes against a 30-minute target, across
the 40 adjusters in the pilot.
**Quality:** about 3 in 100 summaries need a material correction; all were caught at adjuster review and
none reached a claimant.
**Risk:** about one file in eight contains handwritten notes, which the system cannot read today, so
those files fall back to manual review.
**Decision:** fund a specialist OCR service (estimated cost in the appendix) to extend coverage to those
files, or accept manual handling for them. I recommend funding it, because it is the largest remaining
gap to the target."

The technical details move to an appendix. (All figures are illustrative.)

## Trade-offs
- Leading with the conclusion saves executive time, but needs the analysis finished first; when it is not, say what you know and when you will know more.
- Translating into business metrics makes value visible, but hides technical nuance; keep an appendix for technical reviewers who need it.
- Reporting risks early builds trust, but may trigger premature intervention; pair each risk with a mitigation and an owner.
- A fixed one-page format enables week-to-week comparison, but can feel rigid when a crisis needs a dedicated note.
- Pre-wiring the sponsor avoids public surprises, but adds a meeting; skip it only when the news is routine.

## Pitfalls
- Chronological reporting that buries the conclusion; the symptom is executives asking "so is this on track?" after every update.
- Reporting model metrics without a business translation, revealed when steering discussions never touch value.
- Watermelon status (green outside, red inside) that hides problems until they explode, destroying trust in later updates.
- Updates without an explicit ask, so blockers wait weeks because nobody realized a decision was needed.
- Quoting a single accuracy number without volume, consequence or uncertainty, leading to over-confidence or panic.

## When to use
Use this structure for any communication with someone who decides about funding, scope or risk but does
not do the work: steering updates, escalations, go/no-go recommendations and quarterly value reviews. With
peers who need detail, lead with the conclusion but include the technical depth. Measure success by
whether decisions arrive on time and whether executives' understanding of status matches reality. In
interviews, you may be asked to explain a technical result to an executive; lead with the outcome and the
ask, give one number tied to value, and state the main risk.

## Role lens
- **Engineer:** Turns eval and telemetry output into the few business-facing numbers an update needs, with honest error rates and ranges.
- **Architect:** Frames trade-offs (cost, risk, time) as explicit options with a recommendation so executives can decide quickly.
- **FDE:** Writes the customer's steering updates, pre-wires the sponsor before difficult meetings, and escalates risks while they are still recoverable.

## Check yourself
**Q:** Your pilot's accuracy is below the target agreed with the customer's executive, but a fix is likely. How do you communicate this?
**A:** Early and directly, before the steering meeting, in a one-to-one with the sponsor. I would lead with the bottom line: we are below target, here is the gap in business terms (what share of cases need correction and whether any reached customers), here is the cause, here is the fix with a realistic date and its uncertainty, and here is the decision I need, such as extending the pilot by three weeks or narrowing scope to the case types already above target. I would not bury it or polish it green, because finding out later costs far more trust than the gap itself.

## Quiz
### Q: What should come first in an executive status update?
- [ ] A chronological list of work completed since the last update
- [ ] The technical metrics with their definitions
- [x] The conclusion and any decision you need from the reader
- [ ] The methodology used to evaluate the system
> BLUF and the Pyramid Principle put the governing point first because executives may stop reading at any
> time. Chronology and methodology belong lower or in an appendix.

### Q: Which phrasing best communicates AI quality to an executive?
- [ ] "Our F1 score improved by 0.12 this sprint"
- [ ] "The model is highly accurate"
- [x] "About 3 in 100 drafts need correction; all were caught at review and none reached customers"
- [ ] "The model hallucinates sometimes, as all LLMs do"
> Executives make risk decisions, so they need the rate in volume terms plus whether errors are contained.
> Raw metrics, vague praise or fatalism give them nothing to decide with.

### Q: Why brief the sponsor one-to-one before presenting bad news at a steering meeting?
- [ ] To get permission to leave the bad news out of the meeting
- [x] So the sponsor is not surprised in front of peers and can help shape the response
- [ ] Because steering committees cannot discuss risks formally
- [ ] To shorten the steering meeting agenda
> Pre-wiring keeps trust and turns the sponsor into an ally for the proposed fix. Hiding the news would
> violate the no-surprises rule that makes executive communication credible.
