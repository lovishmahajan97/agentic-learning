---
id: user-training
module: adoption
name: User training
kind: practice
difficulty: core
est_minutes: 13
status: fact-checked
related:
  - adoption/change-management
  - adoption/workshops
  - adoption/usage-analytics
  - adoption/handoff-to-customer-teams
  - agents/human-in-the-loop
  - customer-data-evaluation/golden-dataset-creation-with-client-smes
  - security-governance-compliance/pii-protection-dlp
sources:
  - Kirkpatrick, 1994 — Evaluating Training Programs: The Four Levels
---

## TL;DR
User training for an AI system teaches people a working mental model, not a feature tour: what the
system is good at, how it fails, how to verify its output, what data they may give it, and how to report
problems. It exists because probabilistic tools punish both blind trust and blanket distrust, and because
users who never learn to check outputs either let errors through or quietly stop using the tool.

## Why it exists
Traditional software training teaches where the buttons are, because the software behaves the same way
every time and correctness is the vendor's job. An AI assistant breaks that contract. Two users who click
the same button can get different results, the same input can produce a strong answer one day and a
weaker one after a model update, and correctness is now partly the user's job, because the user is
often the last check before an output reaches a customer or a record.

That changes what users need to know. They need to recognize the task types where the system is reliable
and where it is not; they need a fast way to verify an answer (open the cited source, check the number
against the record); they need to know what not to paste in (customer PII into an unapproved tool, for
example); and they need to know how to flag a bad output so it reaches the team that can fix it.

The naive alternatives fail. A recorded feature demo creates awareness and nothing else, and much of it is
forgotten quickly if it is not practised soon. "It's a chat box, it's intuitive" leaves each user to discover the
failure modes alone, usually by being burned in front of a customer. One big launch session covers too
much, before users have the context to absorb it, and is never repeated when the system changes.

## How it works
### 1. Derive the curriculum from the behavior change
Start from the target behavior defined in the change plan ("agents start each reply from the AI draft and
check every policy claim against the linked article") and work backwards to what a user must know and be
able to do. Anything that does not support that behavior is optional, *because* attention is the scarce
resource in training.

### 2. Teach the mental model and the failure modes
Explain, in the user's terms, what the system does: "it reads the five most relevant help articles and
drafts a reply from them; if the answer is not in those articles, it may guess." That one sentence predicts
the main failure (confident answers on topics not in the knowledge base) and tells users what to check.
Show real failures from your evaluation set, *because* people calibrate trust from examples far better
than from a stated accuracy number.

### 3. Practise on real tasks, with seeded errors
Hands-on exercises use the users' own kinds of cases. A verification drill gives each person a set of AI
outputs, some of which contain realistic seeded errors, and asks them to accept, edit or reject each. It
builds the skill that matters most and produces a measurable score. Role-specific tracks matter here:
the reviewer, the frontline user and the team lead need different drills.

### 4. Cover data handling and escalation explicitly
State what data may and may not go into the tool, why, and what to do on a bad output: the feedback
button, what to write, and when to escalate to a human expert. *Because* these rules carry compliance
risk, they deserve a dedicated, short, testable segment rather than a slide buried in the middle.

### 5. Deliver in the workflow, then reinforce
Short sessions close to go-live, followed by job aids inside the tool (tooltips, a one-page "when to
trust, when to check" card), office hours, and champions in each team. Spaced reinforcement beats one long
session, *because* skills not used soon after training fade quickly. Train-the-trainer lets the customer's
own staff carry the program after handoff.

### 6. Measure with the four levels, and retrain on change
Kirkpatrick's four levels give a useful ladder: *Reaction* (did they find it useful), *Learning* (can they
do the drill), *Behavior* (do usage logs show the target behavior), *Results* (did the business metric
move). Organizations measure levels 1 and 2 far more often than levels 3 and 4, yet 3 and 4 are
the ones that show whether the program worked. When the model, prompt or
knowledge base changes enough to shift behavior, update the failure-mode examples and tell users what is
different, *because* their calibrated trust is now calibrated to an old system.

## Example
A hypothetical support team of 60 agents is getting an AI reply drafter grounded in the help center. The
eval shows drafts are usually accurate on documented topics but sometimes invent policy details on
undocumented ones.

Training is three 45-minute sessions in the go-live week, by team. Session one explains the mental model
("it only knows the help center; if the answer is not there it may guess") and shows four real eval
failures. Session two is a verification drill: 20 drafts, 5 with seeded errors (an invented refund window,
a wrong plan name, a missing caveat). Before coaching, agents catch on average 2 of the 5; after a
walk-through of "click the citation, check every number and policy claim", a second drill set gets an
average of 4 of 5. Session three covers what customer data may be pasted into free-text fields, the
feedback button, and when to hand off to a tier-2 specialist.

Reinforcement follows: a pinned checklist in the drafting panel, two champions per team, and office hours
twice a week for a month. At level 3, logs show the share of replies sent with zero edits on undocumented
topics falling, which is the desired behavior, while overall draft use rises. At level 4 the team reports
handle time and the QA error rate to the sponsor. When the knowledge base is restructured two months later,
a 15-minute refresher shows what changed. (All figures are illustrative.)

## Trade-offs
- Live, hands-on sessions build real verification skill, but cost user hours; recorded material scales better when the tool is low-risk.
- Role-specific tracks fit each job closely, but multiply preparation work; one shared track is fine when roles use the tool identically.
- Showing real failures calibrates trust, but can scare off adopters if presented without the verification method that handles them.
- Training close to go-live improves retention, but compresses the schedule; train too early and people forget before they can practise.
- Customer-run train-the-trainer scales and survives handoff, but quality drifts unless you supply materials and check drill scores.

## Pitfalls
- Teaching features instead of failure modes; the symptom is users who know every button yet forward wrong answers unchecked.
- Measuring only satisfaction surveys, which look positive while usage logs show the target behavior never appeared.
- Never retraining after model or prompt changes, revealed by a sudden rise in overrides or complaints after an update.
- Treating data-handling rules as an aside, discovered later when sensitive data shows up in prompts or logs.
- Training once at launch with no reinforcement, shown by usage and drill scores sliding over the following weeks.

## When to use
Run structured training whenever users are expected to rely on AI output in work that matters, and
especially when they are the last check before the output reaches a customer, a record or a decision.
Scale the effort with the cost of errors and the number of users: a low-risk internal summarizer may need
a job aid and a short video; a drafting tool used for regulated customer communication needs drills,
data-handling rules and measured verification skill. Measure at behavior and results level, not just
reaction. In interviews, a strong answer explains why AI training must teach verification and failure
modes, and how you would tell whether it worked.

## Role lens
- **Engineer:** Builds the in-product affordances training relies on: inline citations, feedback buttons, tooltips and logging that shows whether verification happens.
- **Architect:** Decides where human review sits and which outputs require it, which in turn sets what each role must be trained to check.
- **FDE:** Runs the sessions with the customer's real cases, builds the seeded-error drills, and trains internal champions who own training after handoff.

## Check yourself
**Q:** Training satisfaction scores are high, yet QA finds agents are sending AI drafts with policy errors. What is wrong with the program, and how would you change it?
**A:** The program is probably measuring reaction, not learning or behavior, and teaching what the tool does rather than how it fails. I would add a verification drill with seeded, realistic policy errors to measure whether agents can catch them, teach a concrete checking method (open the citation, check every policy claim and number), and show real failures from the eval set. Then I would track behavior in logs, such as edit rates on undocumented topics, and the QA error rate as the result. I would also check whether the UI makes verification fast, because a slow check gets skipped regardless of training.

## Quiz
### Q: Which Kirkpatrick level is shown by usage logs where agents now open citations before sending drafts?
- [ ] Reaction
- [ ] Learning
- [x] Behavior
- [ ] Results
> Behavior is whether people apply the skill on the job, which logs can show. Learning would be a drill
> score in training; Results would be the change in the business metric such as QA errors.

### Q: Why are seeded-error drills more useful than a quiz on product features for AI tools?
- [ ] Feature quizzes are harder to grade automatically
- [x] They train and measure the skill users most need: catching plausible but wrong outputs in realistic cases
- [ ] They prove the model is accurate enough to deploy
- [ ] They remove the need for human review in production
> Users are often the last check, so recognizing wrong outputs is the critical skill. A drill measures it
> directly; it says nothing about model accuracy and does not replace review.

### Q: After a major model upgrade, what should happen to user training?
- [ ] Nothing, as long as the interface has not changed
- [ ] Users should be retrained from scratch with the full original program
- [x] Update the failure-mode examples and give a short refresher on what behaves differently
- [ ] Training should pause until the new model's accuracy reaches 100%
> Users' trust is calibrated to the old system's behavior. A targeted refresher on what changed recalibrates
> it cheaply; a full retrain wastes time and waiting for perfection never ends.
