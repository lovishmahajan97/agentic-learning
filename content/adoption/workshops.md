---
id: workshops
module: adoption
name: Workshops
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - adoption/user-training
  - adoption/stakeholder-management
  - scoping/ai-use-case-discovery
  - scoping/workflow-analysis
  - customer-data-evaluation/labeling-sessions
  - customer-data-evaluation/sme-review-and-annotation-guidelines
  - evals/rubrics
sources:
  - Gray, Brown & Macanufo, 2010 — Gamestorming
  - Lipmanowicz & McCandless, 2013 — The Surprising Power of Liberating Structures
---

## TL;DR
A workshop is a facilitated working session that produces a specific artifact or decision with the people
who hold the knowledge and the authority, in hours instead of weeks of email. In AI work the useful
artifacts are concrete: a ranked use-case list, a mapped workflow, an agreed rubric, labeled examples. A
workshop without a defined output and follow-up is just a long meeting.

## Why it exists
AI projects depend on knowledge that lives in people's heads: which cases are hard, what "good" looks like
for an answer, where the workflow really breaks, which data is allowed. That knowledge is spread across
subject-matter experts, operators, IT, security and a sponsor who rarely sit together. Collecting it one
interview at a time is slow and produces contradictions you discover only when the system ships. Asking
for it by email produces short answers from the busiest people and silence from the rest.

AI adds a sharper version of the problem. Quality criteria for generative output are often tacit: two
experts can look at the same drafted answer and disagree on whether it is acceptable, and neither has
written the rule down. You cannot build an eval, a prompt or a training program until that disagreement is
surfaced and resolved, and the fastest way to do that is to put the experts in front of the same real
examples at the same time.

The naive alternatives fail differently. An open "brainstorm" with no structure gets dominated by the most
senior or loudest voice and ends with a list of ideas nobody owns. A presentation dressed up as a workshop
(you talk, they listen) captures nothing. A well-run workshop is designed backwards from the artifact it
must produce, with structure that makes everyone's knowledge visible before it is debated.

## How it works
### 1. Fix the output and the decision first
Write down what will exist at the end: "a rubric with 4 to 6 criteria that 5 claims experts agree on" or
"a top-3 use-case shortlist with an owner and data source each". Everything else is designed backwards
from this, *because* a workshop without a defined artifact drifts into discussion.

Then budget the agenda from the artifact, not from the calendar slot. If the output needs 20 examples
labelled and every disagreement discussed, estimate how many will split and how long each discussion
takes; a three-hour slot that needs four hours of discussion produces a half-finished rubric and a
"we'll continue by email" that never converges.

### 2. Choose participants for knowledge and authority
Invite the people who do the work, the people who can decide, and anyone with a veto (security, data
owner, legal) for the parts that concern them. Keep the working group small, often under about ten,
*because* participation per person falls fast as groups grow. Separate sessions are better than one huge
one when audiences need different things.

### 3. Prepare real material and pre-work
Bring the customer's actual documents, tickets or outputs, anonymized as needed, and send short pre-work
(list your three most painful cases). Real material grounds the conversation in facts *because* people
argue less about a specific ticket than about abstractions.

### 4. Structure: diverge, then converge
Open with silent individual work (sticky notes, labeling, writing) before any discussion, *because* it
stops the first senior opinion from anchoring everyone else. Then share in small groups and in plenary,
for example with the 1-2-4-All pattern (alone, pairs, fours, whole group), cluster, and converge with
explicit criteria: dot voting, an impact-versus-feasibility grid, or discussing only the disagreements.
Keep a parking lot for off-topic items so they are captured without derailing the session.

Agree the decision rule before the decision, *because* a group that has not agreed how to decide will
argue about the process as soon as the outcome is close. Options: the accountable owner decides after
hearing everyone; consent (adopt unless someone has a reasoned objection); or a vote with the owner
breaking ties. Hard constraints (legal, regulatory, security) are not voted on; the person who owns them
rules, and the group designs around the ruling.

### Measure agreement, not just whether people nodded
For labelling and rubric sessions, compute agreement on the silent first pass and again after the
rubric. Raw percent agreement is easy to read but flatters you when one label dominates: if 90% of
outputs are acceptable, two raters who say "accept" to everything agree 90% of the time while
disagreeing on every case that matters. Chance-corrected statistics (Cohen's kappa for two raters,
Fleiss' kappa for more) correct for this. In the room, a simpler readout works: the count of items where
everyone agrees, and the list of split items, which is the agenda for the discussion. If agreement does
not rise after the rubric is written, the criteria are still ambiguous or the experts genuinely want
different things, and that has to be escalated to whoever owns the definition of "good".

### 5. Facilitate actively
The facilitator owns time, makes quiet participants heard, and stops solution debates that belong
elsewhere. On a customer engagement the FDE is often both facilitator and domain outsider, which is useful:
naive questions expose tacit rules.

### 6. Capture live and follow up fast
Write outputs into the artifact during the session, not from memory later. Send a summary within a day or
two with decisions, open questions, owners and dates, *because* agreement decays quickly and the summary
becomes the record people refer to.

### Common AI workshop types
Use-case discovery (rank candidates by value and feasibility), workflow mapping (as-is and to-be, where AI
sits and who reviews), rubric and labeling sessions (agree on what good output means), and enablement or
prompting sessions for users. Each has a different artifact, so do not merge them.

## Example
A hypothetical insurer needs acceptance criteria for AI-drafted claim-denial letters. The team runs a
three-hour rubric workshop with five senior claims handlers, a compliance officer and the FDE as
facilitator.

The agenda is designed backwards from that output (times illustrative): 15 minutes framing and the
decision rule (compliance rules on legal requirements, the claims director's delegate on everything
else); 30 minutes silent labeling; 15 minutes revealing the label grid; about 60 minutes on the split
cases at roughly 5 minutes each; 30 minutes drafting criteria; 20 minutes relabeling; 10 minutes on
owners and open questions. That budget only works if about 10 to 12 cases split, which is why the
facilitator picks the 20 drafts to include obvious passes and obvious fails as well as hard cases.

Pre-work: each handler sends two letters they consider excellent and two they would reject. In the session,
everyone first labels the same 20 AI drafts silently as accept, edit or reject. On the first pass the five
handlers agree unanimously on only 9 of the 20. Rather than debate all 20, the group discusses just the 11
split cases. The disagreements cluster into four themes: whether the letter cites the exact policy clause,
whether the tone is acceptable for a bereaved claimant, whether appeal rights are stated, and whether the
letter speculates about facts not in the file. Compliance rules that appeal rights are mandatory, which
turns one theme from preference into a hard requirement.

The group writes four criteria with a pass and a fail example each, then relabels 10 fresh drafts with the
rubric; unanimous agreement rises to 8 of 10. Output: a written rubric, 30 labeled examples for the golden
set, two open questions with owners, and a follow-up summary sent the next morning. (Figures are
illustrative.)

## Trade-offs
- In-person workshops build trust and energy, but cost travel and calendar time; remote works well for short, focused labeling sessions.
- Inviting decision-makers lets the session commit, but their presence can silence junior experts unless silent individual work comes first.
- Tight structure produces predictable artifacts, but can miss surprising insights; leave a slot for open discussion when exploring a new domain.
- Broad attendance builds buy-in, yet reduces depth per person; split into focused sessions when more than about ten people need to contribute.
- A full-day session gets much done at once, but fatigue erodes the afternoon; two half-days often produce better output.

## Pitfalls
- No defined output, so the session ends with energy but no artifact; the symptom is a follow-up email that only says "great session".
- Starting with open discussion, letting the most senior voice anchor everyone; the output mirrors one person's view.
- Using hypothetical examples instead of real data, revealed later when the rubric fails on actual cases.
- Missing the veto holder, so a security or compliance objection arrives weeks later and invalidates agreed decisions.
- Slow follow-up; within a week participants remember different decisions and reopen settled questions.
- Running a "workshop" to ratify a decision already made, which participants detect quickly; the symptom is polite silence and an output nobody defends later.
- No agreed decision rule, revealed when the last hour is spent arguing about who gets to decide rather than about the criteria.

## When to use
Reach for a workshop when the knowledge you need is spread across several people, when they are likely to
disagree, or when you need a joint commitment rather than individual opinions: discovery, workflow
redesign, defining quality criteria, labeling a first golden set, and user enablement. Do not use one when a
single expert can answer by message, or when the decision is already made and only needs communicating.
Judge a workshop by its artifact and by whether decisions held a month later; for labelling sessions,
also by whether agreement measurably rose between the first pass and the relabel. In interviews, describe the
output you would design for and how you would stop anchoring.

## Role lens
- **Engineer:** Prepares real examples and tooling for labeling sessions, and turns the agreed rubric and labels into eval cases and prompt changes.
- **Architect:** Uses workflow and discovery workshops to settle boundaries: where AI sits, who reviews, which systems and data are in scope.
- **FDE:** Designs and facilitates the sessions on site, extracts tacit expert rules through naive questions, and owns the follow-up summary.

## Check yourself
**Q:** Your client's experts keep disagreeing about whether the AI's answers are "good enough". How would you design a workshop to resolve this, and why that structure?
**A:** I would design backwards from a written rubric plus a labeled example set. Experts would first label the same real outputs silently, which makes disagreement measurable and stops anchoring on the most senior person. We would then discuss only the cases where labels split, cluster the reasons into criteria, and let the person with authority (often compliance or the process owner) rule on which criteria are mandatory. Finally we would relabel fresh examples with the rubric to check that agreement improved. The output feeds the eval set, and a summary with owners goes out within a day.

## Quiz
### Q: Why start a workshop segment with silent individual work before group discussion?
- [ ] It shortens the session by skipping discussion
- [x] It captures everyone's view before the first opinion, often the most senior, anchors the group
- [ ] It lets the facilitator grade participants individually
- [ ] It is required for remote sessions only
> Anchoring and deference to seniority distort group output. Writing or labeling alone first makes every
> view visible, and the following discussion can focus on genuine disagreements.

### Q: In a labeling workshop, five experts agree on 9 of 20 outputs. What is the most efficient next step?
- [ ] Replace the experts with a single senior reviewer
- [ ] Average the labels and move on
- [x] Discuss only the split cases and turn the reasons for disagreement into explicit criteria
- [ ] Ask the model to label the cases instead
> The split cases contain the tacit rules that differ between experts. Turning them into criteria makes
> the rubric explicit; averaging or delegating hides the disagreement that will resurface later.

### Q: What best indicates that a discovery workshop succeeded?
- [ ] Participants rated it highly in the feedback form
- [ ] The session ran to the planned agenda exactly
- [x] A defined artifact exists with owners, and the decisions are still holding weeks later
- [ ] Every attendee spoke at least once
> A workshop is a means to an artifact and a durable decision. Satisfaction and agenda adherence can be
> high in a session that produced nothing anyone acts on.
