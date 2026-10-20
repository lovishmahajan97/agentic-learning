---
id: demos
module: scoping
name: Demos
kind: practice
difficulty: core
est_minutes: 11
related:
  - scoping/rapid-prototyping-poc
  - scoping/success-metrics-kpis
  - scoping/customer-discovery
  - evals/golden-dataset
  - adoption/exec-communication
  - strategy/stakeholder-alignment
---

## TL;DR
A demo is a decision instrument, not a performance: it should help a specific audience decide something,
using their real workflow and data. LLM systems make impressive demos dangerously easy, because a
cherry-picked input can look magical while the typical input fails, so a good demo pairs the live story
with eval numbers, shows a failure honestly, and makes clear what is and is not yet built.

## Why it exists
Most people who fund, approve or adopt an AI system will never read an eval report. They form their view
from what they see in a meeting. That makes the demo one of the highest-leverage artifacts in an
engagement, and also one of the most hazardous.

The hazard is specific to generative AI. With conventional software, if a feature works once on stage it
mostly works every time, because behavior is deterministic and covered by tests. With an LLM, the same
prompt can produce different outputs across runs, and quality varies enormously across inputs. It takes an
afternoon to find five inputs where a prototype looks brilliant; it takes weeks to make the median input
good. A demo built on those five inputs creates expectations the system cannot meet, and the gap surfaces
in the pilot as "it worked in the demo".

The naive alternatives both fail. The polished-showcase demo (curated inputs, rehearsed path, everything
green) wins the meeting and loses the account later. The opposite, refusing to demo until the system is
"ready", leaves stakeholders without anything concrete to react to, so feedback on the workflow arrives
months late, when changes are expensive. The useful middle is a demo designed around a decision, built on
representative inputs, honest about its boundaries, and backed by numbers.

## How it works
### 1. Start from the decision and the audience
Before building anything, write one sentence: "After this demo, *who* should be able to decide *what*?"
An executive deciding whether to fund a pilot needs the business outcome and the risk. The operations
lead deciding whether her team will use it needs to see her own workflow, including the awkward cases.
The security reviewer needs the data flow. One demo rarely serves all three, *because* each decision
needs different evidence.

### 2. Script around a real workflow with real data
Use the customer's own documents, tickets or records (with permission and appropriate handling), arranged
as the task a real user performs from start to finish. Real data matters *because* it carries the mess
synthetic data lacks: scanned pages, abbreviations, missing fields. Recognizable inputs also make the
audience trust what they see.

### 3. Choose inputs representatively, and say how
Pick demo inputs from the evaluation set: some typical cases, one hard case, and one case the system gets
wrong or escalates. Then state the aggregate: "On 200 of your past tickets, it routed 87% correctly; here
are three of them." The demo is the anecdote, the eval is the evidence, and showing both prevents the
anecdote from standing in for the evidence.

### 4. Show the boundary on purpose
Demonstrate what happens when the system is unsure: it abstains, cites its sources, or hands off to a
human. Showing a controlled failure builds more trust than a flawless run, *because* experienced buyers
know nothing is flawless, and they want to see that failures are contained.

### 5. Control the live risks
Live demos of non-deterministic systems fail in ways rehearsals miss: provider latency spikes, rate
limits, a different output on the same input. Mitigations: lower sampling temperature for the demo path,
pre-warm caches, have a recorded backup of each segment, and test on the actual network you will present
from. If you show cached or recorded output, say so, *because* hidden caching also hides real latency.

### 6. Label what is real
Separate what is built from what is mocked: "The extraction is live; the CRM write-back is a stub." Any
UI, integration or data source that is faked becomes an implied commitment unless it is labeled.

### 7. Capture the reaction as data
Assign someone to record questions, objections and feature requests. These are discovery inputs and often
reveal the real success criterion, such as "the summary is nice, but what I need is the deadline date".

## Example
A 25-minute demo of a contract-review assistant to a legal operations team deciding whether to run a
six-week pilot.

| Minutes | Segment | Purpose |
|---|---|---|
| 0–3 | The decision on the table and today's process: 40 minutes per NDA review, two reviewers | Anchor on their baseline |
| 3–10 | Live run on three of *their* past NDAs: two typical, one with an unusual indemnity clause | Show the real workflow |
| 10–14 | Results on 120 historical NDAs: clause-flag recall 91%, precision 84%, misses listed by type | Evidence behind the anecdote |
| 14–18 | A document where the model is unsure: it flags low confidence and routes to a lawyer | Show the boundary |
| 18–21 | What is live vs. stubbed (the document-management integration is mocked) | Prevent implied commitments |
| 21–25 | Proposed pilot scope, success metric and their questions | Turn reaction into a decision |

The figures are illustrative. Note what is absent: no curated "wow" prompt, no unexplained accuracy
claim, and no integration shown as working when it is not. The live segment has a recorded fallback, and
the session ends on the decision it was built to inform.

## Trade-offs
- Live demos build credibility because the audience sees real behavior, but they risk visible failure;
  recorded segments are safer when the network or provider is unreliable, provided you say they are recorded.
- Showing failures honestly costs some initial excitement, but it pays back in the pilot; the calculation
  flips only in pure exploratory sessions where nobody is making a commitment.
- Early, rough demos surface workflow feedback while it is cheap to act on, but they risk anchoring
  stakeholders on unfinished UI; label rough parts explicitly to keep feedback focused.
- Tailoring a demo to one audience makes it persuasive for that decision, but reusing it for a different
  audience usually answers the wrong question and should prompt a rebuild.

## Pitfalls
- Demoing on hand-picked inputs that are easier than real traffic; the symptom is a pilot whose users say
  the system "worked better in the demo".
- Showing mocked integrations or UI without labeling them, which turns them into commitments; the symptom
  is a statement of work that includes features nobody estimated.
- Demoing on synthetic or public data instead of the customer's own, so the audience cannot judge
  relevance and the hard cases never appear; the symptom is polite approval and no follow-up.
- Rehearsing only on a fast office network, then presenting through a customer VPN or proxy; the symptom
  is timeouts or long spinners during the live segment.
- Treating the demo as the evaluation, with no aggregate number behind it; the symptom is stakeholders
  quoting a single impressive example as the expected performance.

## When to use
Demo at decision points: after discovery to confirm you understood the workflow, at the end of a PoC to
decide on a pilot, during a pilot to show progress against the metric, and before production for sign-off.
Demo early and roughly when the main risk is building the wrong thing; demo later and with evidence when
the main risk is overpromising. Do not use a demo to substitute for an evaluation, and do not demo to an
audience whose decision you have not identified. Measure a demo by whether it produced a clear decision
and actionable feedback, not by applause.

## Role lens
- **Engineer:** Builds a reproducible demo path from the eval set, with fixed settings, warmed caches and a
  recorded fallback, and makes sure the aggregate numbers shown are real.
- **Architect:** Decides what the demo claims about the target architecture and labels which components
  are prototype stand-ins, so the demo does not quietly redefine the design.
- **FDE:** Runs demos inside the customer's environment on their data, reads the room for the real success
  criterion, and turns objections into scoped pilot requirements.

## Check yourself
**Q:** A sales lead asks you to demo a support-ticket triage prototype to the customer's VP next week, using
the ten examples that "look best". The prototype's accuracy on a 300-ticket eval set is 76%. How do you
build the demo?
**A:** First pin down the VP's decision, probably whether to fund a pilot. Then pick demo tickets from the
eval set to reflect the real mix: a few typical correct cases, one hard case, and one wrong or escalated
case showing how errors are caught. Present the 76% alongside the current baseline and what the pilot would
aim to improve, so the demo sets expectations the pilot can meet. Ten best-looking examples would win the
meeting but create a gap that surfaces as distrust during the pilot. Label anything mocked, run with a
recorded fallback, and close on the pilot proposal.

## Quiz
### Q: Why are cherry-picked demos a bigger risk for LLM systems than for conventional software?
- [ ] LLM systems are always slower, so demos need shorter inputs
- [x] Output quality varies widely across inputs and runs, so a few good cases say little about typical behavior
- [ ] Customers cannot see the source code of an LLM system
- [ ] LLM providers forbid showing failure cases in public demos
> Deterministic software that works once usually works every time. An LLM system's quality is a
> distribution over inputs, so curated examples can sit far above the median the pilot will experience.

### Q: What is the main reason to show a failure or low-confidence case during a demo?
- [ ] It makes the demo shorter by skipping the full workflow
- [ ] It proves the model is not overfitted to the demo inputs
- [x] It shows how errors are contained, which builds trust with buyers who know no system is flawless
- [ ] It satisfies regulatory requirements for AI transparency in all jurisdictions
> Experienced stakeholders expect errors; what they need to see is abstention, citations or human hand-off.
> One failure does not prove absence of overfitting, and no general rule requires it in demos.

### Q: You show a recorded segment because the customer network is unreliable. What should you do?
- [ ] Present it as live to avoid undermining confidence
- [ ] Skip the latency discussion entirely
- [x] Say it is recorded and give the real latency measured from the eval runs
- [ ] Re-record it at a higher speed so it fits the time slot
> Recorded output is a legitimate fallback, but hiding it misrepresents latency and reliability. Stating
> it and giving measured numbers keeps the demo honest and the expectations accurate.
