---
id: workflow-analysis
module: scoping
name: Workflow analysis
kind: practice
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - scoping/customer-discovery
  - scoping/problem-decomposition
  - scoping/baseline-measurement-before-deployment
  - agents/human-in-the-loop
  - system-design/workflow-orchestration
  - adoption/change-management
sources:
  - Mike Rother and John Shook, 1999 — Learning to See
  - Wil van der Aalst, 2016 — Process Mining: Data Science in Action
---

## TL;DR
Workflow analysis maps how work actually flows today: the steps, people, systems, handoffs, decisions,
exceptions and, crucially, how long items wait between steps. It exists because AI only improves an
outcome if it lands on the step that limits it; speeding up a ten-minute task inside a three-day process
changes almost nothing, and the documented process is rarely the one people follow.

## Why it exists
AI projects are usually pitched at a task: "summarise the ticket", "draft the reply", "extract the
fields". The customer, however, cares about an outcome of the whole workflow: tickets resolved faster,
claims paid correctly, fewer escalations. The link between the two is the workflow, and it is where many
AI deployments quietly fail to deliver.

Two facts make the naive approach, automating the most visible task, unreliable. First, in most
knowledge-work processes the elapsed time is dominated by **waiting**, not working: items sit in queues,
wait for an approver, or wait for missing information from someone else. If an AI halves the time an
agent spends researching a ticket but the ticket then waits two days for approval, the customer-visible
cycle time barely moves. Lean practice calls the ratio of working time to elapsed time *flow efficiency*,
and in office processes it is commonly a small fraction, so most of the opportunity sits in the waits.

Second, the **documented process is not the real one**. Process diagrams show the happy path; real work
includes exception paths, rework loops, side channels (spreadsheets, chat messages, personal email) and
tacit checks that experienced staff perform without writing them down. An AI system designed from the
official diagram breaks on the cases that make up much of the actual volume, or skips a check that
quietly mattered. Workflow analysis exists to see the real flow before deciding where AI belongs.

## How it works
### 1. Fix the scope and the unit of work
Choose one workflow and its unit: a ticket, a claim, an invoice, a contract. Define where it starts and
ends (complaint received to complaint closed). Everything is measured per unit, *because* averages across
mixed units hide the bottleneck.

### 2. Observe, do not only interview
Sit with people doing the work ("go to the gemba", in lean terms) and watch several real items end to
end. Interviews give the process people believe they follow; observation shows the copy-paste between
systems, the lookup in a personal spreadsheet and the colleague they message for an answer.

### 3. Map the steps with actors and systems
Draw a swimlane diagram: one lane per role or system, boxes for steps, arrows for handoffs, diamonds for
decisions. For each step capture who does it, which system they use, what input they need and what they
produce. Handoffs are marked explicitly *because* every handoff is a queue and a chance for context loss.

### 4. Measure touch time and wait time
For each step record *touch time* (someone actively working) and *wait time* (the item sitting before the
step starts). This is the core of a value stream map. Where an event log exists (ticket history, case
management audit trail, ERP change log) mine it: every record of case ID, activity and timestamp lets you
compute waits and paths for thousands of cases instead of the handful you observed. This is the basis of
process mining.

### 5. Enumerate variants and exceptions
Count how many distinct paths items take and how frequent each is. Track rework loops (an item sent back),
escalations and "request more information" steps. Design for the variants that carry the volume, and
decide explicitly which rare variants stay manual.

### 6. Locate the constraint
Using the measurements, find the step that limits throughput or dominates elapsed time. A queue that
keeps growing before one step means items arrive faster than that step completes them: its capacity is
below the arrival rate. Little's law (average items in progress = throughput × average time in system,
for a stable system) then explains why that hurts: with throughput capped by the constraint, every extra
item queued adds directly to time in system. That is where improvement changes the outcome.

### 7. Identify AI insertion points
Now ask which steps AI could help at, and how: removing a step, shortening touch time, preventing a
rework loop (by catching missing information at intake), or eliminating a wait (by pre-computing what the
approver needs). Mark where humans must stay in the loop and what they will review.

### 8. Draft the to-be workflow and the baseline
Draw the proposed flow with the AI component, its human checkpoints and the fallback path when it is
unsure. Record current cycle time, touch time, error and rework rates as the baseline, *because* without
them the pilot cannot show improvement.

## Example
A support team wants AI to "write better research notes". Exporting three tickets from the ticketing
system's history (illustrative data) and splitting each step into touch and wait time shows where time
actually goes.

```python
from collections import defaultdict, Counter

# (case_id, activity, start_hour, end_hour) exported from the ticketing system
log = [
    ("T1", "receive", 0.0, 0.1), ("T1", "triage", 4.0, 4.2), ("T1", "research", 22.0, 22.6),
    ("T1", "approve", 47.0, 47.1), ("T1", "reply", 49.0, 49.3),
    ("T2", "receive", 0.0, 0.1), ("T2", "triage", 2.0, 2.1), ("T2", "research", 26.0, 26.4),
    ("T2", "approve", 50.0, 50.1), ("T2", "reply", 51.0, 51.2),
    ("T3", "receive", 0.0, 0.1), ("T3", "triage", 6.0, 6.3), ("T3", "research", 20.0, 20.8),
    ("T3", "request info", 21.0, 21.1), ("T3", "research", 70.0, 70.5),
    ("T3", "approve", 93.0, 93.1), ("T3", "reply", 95.0, 95.2),
]
cases = defaultdict(list)
for case, act, start, end in log:
    cases[case].append((start, end, act))

touch, wait, variants = defaultdict(float), defaultdict(float), Counter()
for steps in cases.values():
    steps.sort()
    variants[" > ".join(a for _, _, a in steps)] += 1
    prev_end = steps[0][0]
    for start, end, act in steps:
        wait[act] += start - prev_end      # time the item sat in a queue before this step
        touch[act] += end - start          # time someone actually worked on it
        prev_end = end

t, w = sum(touch.values()), sum(wait.values())
print(f"touch {t:.1f}h, wait {w:.1f}h, flow efficiency {t / (t + w):.1%}")
for act in touch:
    print(f"{act:13} touch {touch[act]:4.1f}h   wait before {wait[act]:5.1f}h")
print(len(variants), "variants; most common:", variants.most_common(1)[0])
```

Across the three tickets, 4.3 hours of work sit inside 191 hours of waiting (flow efficiency about 2%).
Research touch time is 2.3 hours, but 104 hours are spent waiting before research, including two days in
T3 waiting for customer information, and 70 hours waiting for approval. Better research notes would save
little; catching missing information at intake and pre-packaging the approver's evidence target the waits.

## Trade-offs
- Direct observation reveals tacit steps and side channels, but covers only a handful of cases; when an
  event log exists, combine both so observation explains what the log measures.
- Process mining from event logs gives statistically meaningful paths and waits, but only sees steps that
  write to a system; work done in email or chat is invisible unless you capture it separately.
- A detailed current-state map makes the constraint unarguable, but takes time customers may resent;
  when the bottleneck is obvious and agreed, map just that segment in depth.
- Targeting the constraint maximises outcome improvement, but the constraint may sit in another team or
  a policy you cannot change; then pick the best step within your mandate and say so explicitly.

## Pitfalls
- Analysing the documented process instead of the real one; the symptom is an AI system that handles the
  happy path but fails on the exception paths that make up much of real volume.
- Measuring only touch time; the symptom is a pilot that cuts handling time per item yet leaves
  customer-visible cycle time unchanged, and the sponsor sees no improvement.
- Ignoring side-channel tools; the symptom is users who keep their old spreadsheet open next to the new
  system, because a check or lookup it supported was never modelled.
- Inserting AI without redesigning the human step around it; the symptom is reviewers who either approve
  everything unread or redo the work from scratch, erasing the saving.
- Not recording a baseline before changing the flow; the symptom is an argument after launch about
  whether things improved, settled by anecdote.

## When to use
Do workflow analysis whenever an AI component will sit inside an existing business process, which is
almost always in enterprise and forward-deployed work, and do it before choosing which step to automate.
Keep it lean for small scopes: observe five to ten items, sketch a swimlane, and pull timestamps for a
few hundred cases if a log exists. Go deeper (full value stream map, process mining) when the sponsor's
goal is a cycle-time or throughput outcome. Measure flow efficiency, waits per step, variant frequencies
and rework rates. In interviews, the signal is recognising that the step the customer named is often not
the one that limits the outcome.

## Role lens
- **Engineer:** Pulls event logs from ticketing or case systems, computes per-step waits and variants, and
  wires the AI step to emit the same events so before and after are comparable.
- **Architect:** Decides where AI components and human checkpoints sit in the to-be workflow, and whether
  orchestration lives in an existing workflow engine or a new service.
- **FDE:** Shadows the customer's staff, uncovers the side channels and tacit checks, and presents the
  current-state map that convinces the sponsor where the real bottleneck is.

## Check yourself
**Q:** A customer wants an AI copilot to speed up loan officers' document review, which takes about an
hour per application. End-to-end, applications take about ten business days. How would you decide whether
the copilot is the right investment?
**A:** I would map the workflow and pull timestamps from the loan origination system for a few hundred
recent applications, splitting touch time from waits per step and counting variants. If most of the ten
days is waiting, for example for missing documents from applicants or for a credit committee that meets
twice a week, a review copilot saves officer time but barely changes time to decision. In that case the
higher-leverage AI use is at intake: checking completeness and requesting missing documents immediately,
or preparing the committee pack. If review itself queues because officers are the capacity constraint,
the copilot is right, and I would baseline review time and error rate first.

## Quiz
### Q: In a process with 30 minutes of touch time and 4 days of elapsed time, AI halves the touch time. What most likely happens to elapsed time?
- [ ] It halves, because the work is done twice as fast
- [x] It barely changes, because elapsed time is dominated by waiting between steps
- [ ] It doubles, because reviewers must check AI output
- [ ] It drops to 15 minutes, because the queue disappears
> Saving 15 minutes of work inside four days of queueing leaves elapsed time almost unchanged. Unless
> the touched step is the capacity constraint causing the queue, the waits are where elapsed time lives.

### Q: Why is direct observation of users valuable even when a detailed event log exists?
- [ ] Event logs are usually inaccurate about timestamps
- [ ] Regulations require in-person observation before automation
- [x] Logs only record steps that touch a system, missing side channels, tacit checks and workarounds
- [ ] Observation produces larger sample sizes than logs
> Logs are excellent for statistics on recorded steps, but spreadsheets, chat questions and mental checks
> leave no trace. Observation explains what the log measures and reveals what it cannot see.

### Q: A queue in front of the approval step grows every week. What does that indicate?
- [ ] The approval step has too much documentation
- [ ] Items arriving at approval are of lower quality than before
- [x] Approval capacity is below the arrival rate, so it is the constraint limiting throughput
- [ ] The upstream steps are working too slowly
> A queue only grows when work arrives faster than the step completes it, so approval capacity is the
> constraint. Slow upstream steps would shrink this queue, not grow it; and by Little's law, the growing
> backlog at a capped throughput translates directly into longer time in system.
