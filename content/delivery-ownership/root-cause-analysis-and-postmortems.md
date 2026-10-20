---
id: root-cause-analysis-and-postmortems
module: delivery-ownership
name: Root-cause analysis & postmortems
kind: practice
difficulty: core
est_minutes: 17
status: reviewed
related:
  - delivery-ownership/production-debugging
  - delivery-ownership/failure-analysis
  - delivery-ownership/incident-severity-and-escalation
  - delivery-ownership/technical-documentation
  - reliability/fmea
  - security-governance-compliance/ai-incident-response
  - evals/regression-tests
sources:
  - Beyer, Jones, Petoff, Murphy (eds.), 2016 — Site Reliability Engineering
  - Richard I. Cook, 1998 — How Complex Systems Fail
  - John Allspaw, 2012 — Blameless PostMortems and a Just Culture
---

## TL;DR
Root-cause analysis asks why an incident was possible, not just what broke, and a postmortem is the
written record that turns the answer into owned, tracked work. In complex systems there is rarely a
single root cause, so good analyses map several contributing factors across prevention, detection and
mitigation, and stay blameless so people report what really happened. "The model hallucinated" is a
symptom, never a root cause.

## Why it exists
Mitigation ends the incident; it does not stop the next one. If the team rolls back, closes the ticket
and moves on, the same class of failure returns in a different shape, because whatever let it through
(a missing test, a silent alert, a deploy path without review) is still there.

Two naive alternatives are common. The first is to stop at the proximate cause: "a bad prompt change
was deployed." That names the trigger and suggests "be more careful", which is not an action anyone can
execute. The second is to find someone to blame: "the engineer skipped the eval." Blame feels like
accountability but produces the opposite: people learn to hide mistakes and sanitise timelines, so the
organisation loses the very information it needs. It also misreads how failures happen. In complex
systems, incidents typically need several latent conditions to line up, and the human action at the end
was usually reasonable given what that person could see at the time.

For AI systems the temptation to stop early is stronger, because "the model is non-deterministic" seems
like an explanation. It is not one: a well-built system expects wrong model outputs and has grounding
checks, validation, guardrails and human review sized to the risk. The question is why none of those
caught this one.

## How it works
### 1. Build the timeline first
Assemble a factual, timestamped timeline from logs, alerts, chat and deploy records: when the trigger
happened, when impact began, when it was detected and by whom, each decision and its rationale, when it
was mitigated and resolved. Doing this before analysis matters *because* memory is reconstructive; the
timeline anchors discussion in evidence and exposes gaps such as a 40-minute stretch between impact and
detection.

### 2. Ask "why" along several chains
The **five whys** technique repeatedly asks why the previous answer happened. It is useful for getting
past the trigger, but it produces a single linear chain and stops wherever the asker's knowledge or
patience runs out. So run it along several axes: why did the defect exist, why did it reach production,
why was it not detected sooner, why did mitigation take as long as it did. An **Ishikawa (fishbone)**
diagram groups candidate causes by category (code, data, model, process, people, environment) and helps
avoid tunnel vision. A **fault tree** works backwards from the failure through AND/OR gates and is
useful when you need to show that several conditions were jointly necessary.

### 3. Prefer contributing factors over "the root cause"
Write down every condition without which the incident would not have happened or would have been
smaller. This framing is more accurate for complex systems and produces better actions, because each
factor is a separate place to add a defence. It is the "Swiss cheese" picture: each layer of defence has
holes, and an incident is the rare moment they line up, so shrinking the holes in any one layer helps.

Test each candidate factor in two ways. The counterfactual test: had this condition been absent, would
the incident have been prevented or smaller? If not, it is background, not a factor. The evidence test:
is the causal link shown by logs, traces or a reproduction, or only plausible? A plausible story that
nobody checked is how postmortems confidently fix the wrong thing.

### When to stop asking why
Stop a chain when you reach a condition that a team can change with a system control and own as an
action. Going one step further usually lands on something true but useless ("people make mistakes",
"the deadline was tight"); going one step short leaves a trigger ("a bad config was pushed"). If the
chain ends in an organisational factor such as no budget for eval coverage, record it and escalate it as
a structural finding rather than pretending a ticket can close it.

### 4. Stay blameless, not accountability-free
Describe actions in terms of the information and pressures people had: "the eval suite passed because it
contained no cases for policy documents" rather than "Sam didn't test properly." The goal is a safe
environment for honest accounts; accountability lives in owning the action items.

### 5. Write the postmortem
A standard structure: summary; impact (users, duration, data, money, SLA); timeline; detection (how and
how fast); response; contributing factors; what went well; where we got lucky; action items. "Where we got
lucky" captures near-misses that would otherwise be forgotten.

### 6. Action items that actually close
Each action has a single owner, a due date, a tracking ticket and a category: **prevent** (stop the
defect existing), **detect** (catch it faster), **mitigate** (limit blast radius or speed recovery).
Prefer actions that change the system (a gate, a test, an alert, a kill switch) over ones that rely on
people remembering. Review open postmortem actions on a fixed cadence; an action with no owner or date is
a wish.

### 7. Share and learn
Publish postmortems internally and look across them periodically; recurring factors (no eval coverage for
new document types, provider changes going unnoticed) are where structural investment pays off.

## Example
Incident: for six hours an HR assistant told employees the parental-leave policy was 12 weeks; the
current policy is 16. Proximate trigger: a re-indexing job ingested an archived policy folder.

Five whys along the *defect* chain: the wrong answer was generated because the old PDF ranked top;
it ranked top because both versions were indexed with no effective-date metadata; there was no metadata
because the ingestion connector was configured by folder, and the archive sat inside the indexed folder;
nobody noticed because the connector config was not code-reviewed.

Along the *detection* chain: the issue was found by an employee complaint, not monitoring, because the
groundedness check verified that answers matched retrieved text (they did) rather than that the source
was current; there were no eval cases asserting current policy values.

Contributing factors: archive inside an indexed path; no effective-date filter; unreviewed connector
config; eval suite with no freshness cases; detection reliant on users.

Action items:
- Prevent: index only documents with an effective date and filter superseded versions (owner: retrieval team, 2 weeks).
- Prevent: connector configuration in version control with review (owner: platform, 3 weeks).
- Detect: run 30 "current value" questions from HR after every re-index and nightly, since a nightly-only check could be slower than the six hours it took a user to complain (owner: AI engineer and HR SME, 2 weeks).
- Mitigate: per-collection kill switch to exclude a source within minutes (owner: platform, 4 weeks).

Note what is absent: "the model hallucinated" and "the admin should have checked the folder."

## Trade-offs
- Deep analysis of every incident yields learning but consumes senior time; reserve full postmortems for
  higher severities and near-misses with high potential impact, and use a short template for the rest.
- The "contributing factors" framing is more accurate than a single root cause, but can produce a long
  list with no priority; rank actions by how many future failure paths each one closes.
- Publishing postmortems widely spreads lessons, but candid detail can be sensitive for customers; share
  an external version with impact and corrective actions, and keep the internal one fully candid.
- Systemic fixes (gates, tests, automation) are durable but slower to ship than process reminders; flip
  toward a temporary process control only as a bridge, with a dated plan for the systemic fix.

## Pitfalls
- Stopping at the trigger ("bad deploy", "model hallucinated"); the symptom is action items like "be more
  careful" and the same class of incident recurring within months.
- Blame, explicit or implied; the symptom is vague timelines, defensive meetings and fewer incidents
  reported even as customer complaints rise.
- Action items without owners, dates or tickets; the symptom is a postmortem archive whose actions are
  mostly still open when the next similar incident happens.
- Only "prevent" actions; the symptom is that a different trigger produces the same long, user-detected
  outage because detection and mitigation never improved.
- Writing the postmortem weeks later; the symptom is a timeline reconstructed from memory with gaps
  exactly where the important decisions were made.
- Hindsight bias, judging decisions by an outcome nobody could see at the time; the symptom is
  "should have" and "failed to" language in the timeline, and no explanation of why the action looked
  reasonable to the person who took it.

## When to use
Write a postmortem for every incident above a severity threshold defined in your incident policy, for
any data exposure or safety event regardless of scale, and for near-misses that would have been severe.
Start the timeline while the incident is fresh and hold the review within days. Measure action-item
completion rate and how often incidents share contributing factors with earlier ones; both tell you
whether postmortems are changing anything. In interviews, a strong answer separates trigger, contributing
factors and detection gaps, and proposes system changes rather than personal vigilance.

## Role lens
- **Engineer:** Builds the timeline from traces and deploy logs, and turns detection gaps into concrete
  eval cases, alerts and regression tests with owners.
- **Architect:** Looks across postmortems for recurring structural factors and funds the platform changes,
  such as release gates or source freshness controls, that close whole classes of failure.
- **FDE:** Writes the customer-facing version, joins joint reviews with the customer's teams, and makes
  sure factors on the customer's side become actions they own.

## Check yourself
**Q:** A postmortem draft says: "Root cause: the LLM hallucinated a refund amount. Action: improve the
prompt." What is wrong with it, and how would you rewrite the analysis?
**A:** Hallucination is an expected behaviour of the component, so it is a symptom, not a cause; the
question is why the system let an unverified amount reach a customer. I would ask why the amount was
generated rather than looked up from the order system, why no validator compared it to the order record,
why evals did not cover refund amounts, and how the issue was detected. Actions would then be: fetch
amounts via a tool, validate before sending, add eval cases, and alert on mismatches, each with an owner
and date. "Improve the prompt" may stay, but only as one item among them.

## Quiz
### Q: What is the main limitation of the five-whys technique in complex systems?
- [ ] It requires specialised software to run
- [x] It follows one linear chain and stops where the asker's knowledge ends, missing parallel contributing factors
- [ ] It can only be applied to hardware failures
- [ ] It always assigns the cause to an individual
> Incidents usually need several conditions at once; a single chain of whys finds one of them. Running
> it along prevention, detection and mitigation axes, or using a fishbone or fault tree, surfaces the rest.

### Q: Why are blameless postmortems more effective than assigning individual fault?
- [ ] They remove the need for anyone to own follow-up actions
- [ ] They shorten the postmortem meeting
- [x] People give honest, detailed accounts when they are not punished, which reveals the system conditions behind the error
- [ ] Regulators require that individuals never be named
> Blame drives people to hide or sanitise what happened, destroying the information needed to fix the
> system; blamelessness keeps accounts honest while accountability lives in owned action items.

### Q: Which action item best addresses a detection gap in an AI incident found by a user complaint?
- [ ] Remind the team to review outputs more carefully during releases
- [ ] Switch to a more capable model
- [ ] Add a disclaimer that answers may be inaccurate
- [x] Add an automated eval or online check for the failure type, with an owner and due date
> A detection action should catch the failure automatically before users do; reminders and disclaimers do
> not detect anything, and a model swap is a prevention guess rather than a detection control.
