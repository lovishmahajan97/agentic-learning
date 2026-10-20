---
id: human-in-the-loop
module: agents
name: Human-in-the-loop
kind: pattern
difficulty: core
est_minutes: 13
status: fact-checked
related:
  - agents/approval-gates-for-consequential-actions
  - agents/state-machines
  - safety-security/excessive-agency
  - agent-runtime-architecture/approval-and-authorization-boundaries
  - customer-data-evaluation/shadow-mode-validation
  - customer-data-evaluation/sme-review-and-annotation-guidelines
  - customer-data-evaluation/production-feedback-classification
  - evals/offline-vs-online-evaluation
sources:
  - Parasuraman and Riley, 1997 — Humans and Automation: Use, Misuse, Disuse, Abuse
  - Anthropic, 2024 — Building effective agents
---

## TL;DR
Human-in-the-loop means placing a person at chosen points of an agent's run: before an action (approve
or edit), during it (steer, clarify) or after it (review a sample, correct, label). It exists because
models err too often for some actions and because the person holds authority and information the agent
lacks. Human attention is the scarcest resource, so the design problem is where it buys the most risk
reduction and how to keep each decision cheap, informed and genuine rather than a rubber stamp.

## Why it exists
An agent that is right 97% of the time is wrong 30 times per thousand actions. For drafting a summary
that is fine; for issuing refunds, changing access rights or emailing customers it is not, and some of
those errors cannot be undone. The model also lacks two things no amount of capability supplies:
*authority* (many jurisdictions give people the right to have a significant automated decision, such
as a credit refusal, reviewed by an accountable person; a contract must be signed by someone who can be
held to it) and *information* (the user knows which of two plausible
interpretations of "clean up the old reports" they meant, and the agent cannot find that out by
searching).

The naive answers fail in opposite directions. "Review everything" does not scale, and worse, it
degrades: a reviewer who approves 400 proposals a day, 399 of them fine, stops reading. The
human-factors literature has called this automation bias and misuse for decades; the approval becomes a
reflex and the control becomes fiction while still appearing on the compliance chart. "Review nothing"
gives you the full blast radius of every error. "Let the model decide when to ask" is the subtle
failure: the model's sense of its own uncertainty is poorly calibrated, it rarely asks on the injected
or adversarial case that most needs it, and it asks constantly on easy cases if prompted to be careful.

Human-in-the-loop as a discipline means the *harness* decides when a person is consulted, based on
explicit triggers, and the interaction is designed so a person can make a good decision in seconds. It
also closes a loop the agent cannot close alone: each human decision is a labelled example of what
"correct" means in this deployment.

## How it works
### Three positions for the person
- **In the loop (blocking).** The run pauses until a person decides. Maximum control, latency measured
  in minutes to days, and the pattern for consequential actions, whose mechanism the approval-gates
  lesson covers.
- **On the loop (supervising).** The run proceeds; a person watches a live stream of actions and can
  interrupt or take over. Cheap per action, catches runaway behaviour, misses individual bad actions
  that look normal.
- **After the loop (auditing).** A sample of completed runs is reviewed and labelled. Cheapest, catches
  drift and systematic error, catches no individual mistake in time.
Mature systems use all three, assigned by action class: block on refunds over a threshold, supervise
bulk edits, audit summaries.

### Triggers: when the harness involves a person
1. **Stakes and reversibility.** Irreversible, external or costly actions are gated regardless of
   confidence.
2. **Uncertainty.** Not the model's self-reported confidence, which is weak evidence, but signals the
   harness can observe: disagreement between the model and a judge model, low retrieval scores,
   output-validation failures, repeated retries, an unusually long trajectory.
3. **Novelty.** Inputs outside the distribution the system was evaluated on: a new customer, a document
   type never seen, a request category with no golden examples.
4. **Policy.** Decisions a regulation or internal rule assigns to a person, independent of how good the
   model is.
5. **Ambiguity.** The task has two reasonable readings and the cost of guessing wrong exceeds the cost
   of asking. A clarifying question is human-in-the-loop too, and should be a structured request the
   harness surfaces, not a sentence buried in output.

### Making the decision cheap and real
What the person sees determines whether the control is real. Show the proposed action and its *effect*
(the diff, the rows affected, the email as it will be sent), the evidence the agent relied on, the
alternatives it considered, and buttons for approve, edit, reject with a reason. Do not show the
transcript; nobody reads 80 tool calls. Route to the person who owns the system being changed, not a
generic queue. Define the default on timeout: deny for consequential actions, proceed for reversible
ones. Batch similar low-risk decisions so one look covers ten.

### Closing the loop
Every decision is a label: (input, proposal, human decision, reason). Store it. Agreement rate per
action class is the key metric: it tells you where autonomy can be widened. Graduated autonomy follows:
start at 100% review, automate classes where agreement is high and error cost is low, keep a random
audit sample, and tighten automatically if audit agreement drops. The reasons become eval cases and
regression tests.

### Detecting a fake loop
An approval rate near 100% with a median decision time of a few seconds means nobody is looking. Inject
known-bad proposals (canaries) and measure the catch rate. Watch queue depth: decisions made under
backlog pressure are worse. If reviewers cannot articulate what they are checking, redesign what you
show them.

## Example
A refund agent proposes decisions on 2,000 requests a day (all figures illustrative). Week one runs
fully in the loop: a reviewer checks every proposal at about 45 seconds each, 25 reviewer-hours per day.
The logged decisions show 99.2% agreement on refunds of $50 or less, which are 70% of volume, and 94%
agreement above $50.

The harness is then reconfigured. Refunds of $50 or less (1,400 a day) execute automatically with a 5%
random audit (70 a day). Refunds above $50 (600 a day) stay blocking. Reviewer load becomes
(600 + 70) × 45 s ≈ 8.4 hours a day, a two-thirds reduction. Residual risk on the automated class is
1,400 × 0.8% ≈ 11 wrong decisions a day (treating every week-one disagreement as an agent error), each
bounded at $50, so at most about $560 a day of exposure,
which the business compares against the 16 reviewer-hours saved. The audit sample is the safety net: if
agreement on the audited cases falls below a set threshold, the harness automatically returns the class
to blocking review. At 99.2% agreement, 70 audits a day yield well under one disagreement a day on
average, so the threshold has to be judged over a rolling window of several hundred audits (or the
sample raised); a two-day window of 140 cases is too small to distinguish 99% from 97%. Canary proposals, obviously wrong refunds inserted into the
blocking queue, confirm reviewers are still catching the large majority of them; if that catch rate
drops, what reviewers see is redesigned before autonomy is widened further.

## Trade-offs
- Blocking review buys control at the price of latency measured in hours when approvers are busy; it
  flips to supervision or auditing when latency costs more than the expected error.
- Reviewing more raises the catch rate only until reviewers saturate; past that point approval quality
  collapses, so coverage beyond reviewer capacity is worse than a smaller, well-designed sample.
- Graduated autonomy cuts cost, but it trusts the audit sample to represent the traffic; it flips when
  the input distribution shifts faster than the audit cadence can notice.
- Clarifying questions prevent wrong work but spend the user's attention; past a few per task, users
  abandon the agent and do the work themselves.
- Rich previews (diffs, effects) make decisions fast and accurate but require every tool to support a
  dry run, which is engineering work per tool.

## Pitfalls
- Letting the model decide when to ask for help; the symptom is silence on the adversarial case and
  constant questions on the easy ones.
- Presenting the transcript instead of the effect; the symptom is reviewers who take minutes and still
  miss the harmful line, or stop reading entirely.
- No timeout or a default of "proceed" on consequential actions; the symptom is a run that executes an
  unreviewed refund at 3 a.m. because the approver was asleep.
- Not recording the reviewer's reason; the symptom is a year of decisions that cannot be turned into a
  single eval case.
- Rubber stamping; the symptom is 99.9% approval, three-second decisions, and canaries sailing through.
- Routing approvals to whoever is online rather than whoever owns the system; the symptom is approvals
  from people who could not have known the action was wrong.

## When to use
Put a person in the loop wherever an action is consequential, regulated, novel or ambiguous, and
nowhere else: every unnecessary review is attention taken from a necessary one. Start new deployments
with full review, not because the model is bad but because the logged decisions are how you learn where
it is good; widen autonomy per action class as agreement data accumulates. Measure agreement rate,
decision time, approval rate, canary catch rate and queue depth. Day to day this is the difference
between an agent the business trusts and one it turns off after the first incident; in interviews,
expect to be asked how you would avoid rubber stamping and how you would decide what to automate first.

## Role lens
- **Engineer:** Builds the pause-and-resume path, the structured clarification request, the review UI's
  preview payload, and the logging that turns each decision into a labelled example.
- **Architect:** Sets the policy: which action classes are blocking, supervised or audited, the timeout
  defaults, who may approve what, and the agreement thresholds for widening autonomy.
- **FDE:** Works with the customer's operations team to define triggers and reviewers, runs the initial
  full-review phase, and uses agreement data to negotiate how much autonomy the customer accepts.

## Check yourself
**Q:** A customer wants an agent to handle supplier emails but insists a human approves every outgoing
message. After a month, approvals average four seconds and the approval rate is 99.8%. What is
happening, and what would you change?
**A:** The control has become a rubber stamp: the volume of fine messages has trained reviewers to click
through, so the review no longer catches anything while still appearing as a control. First verify it
with canaries, inserting a few clearly wrong drafts and measuring the catch rate. Then redesign in two
directions. Narrow what is blocking to the classes that matter, such as emails that commit to prices,
dates or cancellations, detected by the harness, and move routine acknowledgements to a sampled audit.
Change what reviewers see: the draft with the commitments highlighted and the thread context, not a
transcript. Record reasons for rejections and feed them into evals, and report agreement rate per class
so the customer can widen autonomy on evidence rather than on trust.

## Quiz
### Q: Why should the harness, rather than the model, decide when to involve a person?
- [ ] Because models cannot generate questions
- [x] Because the model's sense of its own uncertainty is poorly calibrated and can be suppressed by injected instructions, while harness triggers are observable and enforceable
- [ ] Because asking a question costs more tokens than acting
- [ ] Because people prefer to be asked by a system rather than by a model
> Self-reported confidence is weak evidence, and an adversarial input can tell the model not to ask.
> Triggers such as stakes, validation failures, novelty and policy live in code the input cannot change.

### Q: An approval queue shows a 99.9% approval rate with a median decision time of three seconds. What does this most likely indicate?
- [ ] The agent has become reliable enough to remove the review
- [ ] The reviewers are highly skilled
- [x] Reviewers are rubber stamping, so the control is no longer catching errors and should be verified with canaries and redesigned
- [ ] The queue is misconfigured and skipping items
> Fast, near-unanimous approvals are the signature of automation bias. The right response is to measure
> the catch rate with known-bad items and narrow or redesign what is reviewed, not to conclude the agent
> is perfect.

### Q: What is the most useful thing to store with every human decision?
- [ ] The full agent transcript
- [ ] The model's temperature setting
- [x] The input, the agent's proposal, the decision and the reviewer's reason, so it becomes a labelled example for evals and autonomy policy
- [ ] The time of day the decision was made
> Decisions with reasons are labelled data; they measure agreement per action class and seed regression
> tests. Transcripts are large and rarely read; the other options carry little signal.
