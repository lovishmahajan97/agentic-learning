---
id: handoff-to-customer-teams
module: adoption
name: Handoff to customer teams
kind: practice
difficulty: core
est_minutes: 14
status: crisp
related:
  - delivery-ownership/operational-ownership-and-support-boundaries
  - deployment/runbooks
  - deployment/hypercare
  - delivery-ownership/technical-documentation
  - customer-data-evaluation/customer-specific-regression-suites
  - agent-runtime-architecture/secrets-and-credential-isolation
  - adoption/continuous-improvement
  - adoption/user-training
---

## TL;DR
Handoff transfers *ownership* of a deployed AI system from the vendor or FDE team to the customer's
engineers: running it, changing it safely, judging its quality. It works in stages, with exit criteria
the customer has demonstrated, not a calendar date.

## Why it exists
- **Forward-deployed work ends.** The FDE moves to the next account, and the system has to keep working
  without them.
- **The failure is slow decay, not a dramatic outage.** Nobody knows why retrieval filters by region, so
  someone "simplifies" it. Nobody can run the eval suite, so a vendor model update ships unchecked. An alert
  fires into a channel nobody watches. A credential issued to the FDE's personal account expires. Six
  months later the customer concludes "the AI didn't work", and the renewal goes with it.
- **A document dump does not transfer a skill.** A wiki page, an architecture diagram and a recorded
  walkthrough in the final week fail because operating an AI system is a skill, not a body of facts.
  Reading about triaging a quality regression does not prepare someone to do it at 2 a.m.
- **AI systems carry more tacit knowledge than ordinary services.** Why a prompt is phrased the way it is,
  which eval slices are noisy, which failures are known and acceptable: much of that lives only in the
  builder's head.
- **The forcing problem:** move that knowledge, and the matching responsibility, to people who will be
  accountable after the builder is gone.

## How it works
### 1. Define what is being handed off
Split ownership into explicit areas, because each needs different skills and often different people.

| Area | Covers |
|---|---|
| **Run** | On-call, alerts, incident response, capacity and cost budgets |
| **Change** | Prompt and config edits, re-indexing, dependency and model upgrades, all through the release gate |
| **Quality** | The regression suite, failure triage, and the continuous-improvement cadence |
| **Access and accounts** | Service identities, secrets, data permissions, audit, and who owns and pays for the model-provider and cloud accounts |
| **Vendor interface** | What the vendor still supports after handoff, how to escalate, and with what SLAs |

- **Accounts are dependencies.** A vendor-billed API account outlives the engagement unless it is moved.
- **Write it as a responsibility matrix (RACI) that both sides sign.** An unclear boundary turns into
  support tickets the vendor bounces back and forth with the customer.

### 2. Check the receiving team is real
- **Confirm named people, allocated time and the needed skills** before planning the transfer. A handoff
  to a team with no capacity is a handoff to nobody.
- **If skills are missing,** the honest options are training plus a longer transition, or a
  managed-service arrangement.
- **Plan for at least two people per ownership area.** If every exit task is done by the one engineer who
  paired with the FDE, the single point of failure has only moved from vendor to customer, and it
  reappears the first time that person is on leave or changes job.
- **Keep a skills matrix:** areas down the side, named people across, a mark when someone has *done* the
  task. It makes the gap visible early.

### 3. Make the artifacts operable, not just readable
- **Code and config:** move infrastructure-as-code, prompts and configs into the customer's repositories
  and CI.
- **Signals:** route dashboards and alerts to the customer's on-call tooling, and write runbooks keyed to
  specific alerts.
- **Evals:** make the suite runnable in the customer's pipeline.
- **Credentials:** re-issue every one to customer-owned service identities.
- **Decision records** for the non-obvious choices, because the "why" is what stops a well-meaning
  simplification from undoing a fix.
- **Known-issues list and open backlog,** handed over explicitly.

### 4. Transfer responsibility in stages
Each stage moves the operator role one step, *because* people build operational skill by doing the task
with a safety net, not by reading about it.
1. **FDE does, customer watches.**
2. **They work as a pair.**
3. **Customer does, FDE watches** (reverse shadowing).
4. **Customer works alone,** FDE as backup.
5. **Exit.**

### 5. Exit on demonstrated capability
- **Exit criteria are tasks with evidence.** For example: the customer team shipped a prompt change
  through the gate, ran the suite and correctly interpreted a regression, resolved a game-day incident
  using the runbook, rotated a secret, and ran a weekly triage without help.
- **A date can be a target, but the criteria decide.**
- **Evidence must be checkable by someone other than the FDE** (a merged pull request, a gate report, an
  incident ticket), because the FDE has the strongest incentive to declare victory on schedule.

### 6. Taper, then check in
- **Hypercare window** with reducing FDE involvement.
- **30- and 90-day check-ins** on the same adoption and quality metrics, so post-handoff decay shows up as
  a trend and not as a churn surprise.
- **Escalations back to the vendor per month:** a falling count means ownership has moved; a flat or
  rising one means the customer is still operating the system through the vendor's people.

## Diagram
Responsibility moves one stage at a time; the exit review checks evidence, and a failed criterion sends
the team back a stage instead of ending the engagement on the calendar date.

```mermaid
flowchart TD
    R["RACI signed, receiving team confirmed"] --> S1["FDE does, customer watches"]
    S1 --> S2["Pair"]
    S2 --> S3["Reverse shadow: customer does"]
    S3 --> S4["Customer alone, FDE backup"]
    S4 --> GD["Game day"]
    GD --> X{"Every criterion evidenced by 2+ people?"}
    X -->|"no"| S4
    X -->|"yes"| H["Hypercare taper"]
    H --> C["Day 30 and day 90 check-ins"]
    C -.->|"escalations not falling"| S4
```

## Examples
### Worked scenario: six-week handoff of a contract-review assistant
A two-person FDE team hands the assistant to the customer's platform team (three engineers and one
operations analyst) over six weeks. The timeline is illustrative.

| Weeks | Stage | What happened |
|---|---|---|
| 1–2 | FDE does, customer watches | RACI agreed. Repos, Terraform and the prompt registry move into the customer's GitHub organisation. The model-API key and document-store credentials are re-issued to customer service accounts and personal keys revoked. Alerts route to the customer's PagerDuty rota with the FDE as secondary. |
| 3–4 | Pair, then reverse shadow | A customer engineer adds the new indemnity clause type to the extraction prompt: twelve labelled contracts go into the suite first, the gate shows the "governing law" slice drop three points, they fix the instruction conflict and ship behind the canary. The analyst runs the weekly triage while the FDE observes. |
| 5 | Customer alone, FDE backup | Game day: the FDE injects a failure that empties retrieval results for one business unit. The on-call engineer finds it from the alert and runbook in 22 minutes, traces it to a revoked index permission, and restores it. The runbook gets two corrections. |
| 6 | Exit review | Each criterion is checked against evidence: change shipped through the gate, incident resolved, secret rotated, triage run unaided, decision records reviewed. |

- **What the review found:** the skills matrix shows each task was done by only one engineer, and one item
  fails outright: nobody has done a vendor model-version upgrade.
- **Decision:** exit is moved a week. The customer runs the upgrade with the suite, and a second engineer
  repeats the gated change and the secret rotation, before sign-off.
- **Follow-up:** check-ins are booked for day 30 and day 90.

### Readiness check from the skills matrix
The exit review as code: every required criterion needs evidence from at least two customer people, and
work the FDE did does not count. Names and ticket ids are illustrative.

```python
FDE = {"fde-anna", "fde-raj"}

done = [  # (exit criterion, area, performed by, evidence link)
    ("prompt change through gate", "change",  "priya", "PR-412 + gate report"),
    ("prompt change through gate", "change",  "tom",   "PR-431 + gate report"),
    ("game-day incident resolved", "run",     "tom",   "INC-88"),
    ("secret rotated",             "access",  "priya", "CHG-19"),
    ("weekly triage run unaided",  "quality", "maria", "triage notes wk5"),
    ("model-version upgrade",      "change",  "fde-raj", "PR-440"),
]
required = ["prompt change through gate", "game-day incident resolved", "secret rotated",
            "weekly triage run unaided", "model-version upgrade"]

def readiness(done, required, min_people=2):
    gaps = []
    for crit in required:
        people = {who for c, _, who, ev in done if c == crit and who not in FDE and ev}
        if not people:
            gaps.append(f"{crit}: no customer evidence")
        elif len(people) < min_people:
            gaps.append(f"{crit}: only {sorted(people)}")
    return gaps

for g in readiness(done, required):
    print("GAP", g)
# GAP game-day incident resolved: only ['tom']
# GAP secret rotated: only ['priya']
# GAP weekly triage run unaided: only ['maria']
# GAP model-version upgrade: no customer evidence
```

## Production patterns
- **Customer-owned identities from day one** — when the build starts, create service accounts in the
  customer's identity provider and cloud, and never use personal API keys even temporarily. Keep a
  credential inventory with owner and expiry date; alert well before expiry and after any personal key is
  found in use.
- **Eval gate as a required CI check** — once prompts and configs live in the customer's repositories, make
  the regression suite a required status check on pull requests that touch them (branch protection in
  GitHub supports this). Monitor how many changes went through the gate per month after exit.
- **Alert-keyed runbooks** — every alert links to its runbook section, written for the customer's on-call
  engineer. Monitor alerts fired without a runbook link and time to acknowledge on the customer's rota.
- **Game day before exit** — inject a realistic failure (empty retrieval, revoked permission, provider
  outage) in a production-like environment with the FDE only observing. Measure time to resolve and fold
  every runbook correction back in.
- **Skills matrix with two names per cell** — track who has *performed* each exit task, with links to the
  evidence. Monitor cells with fewer than two customer names; they are single points of failure.
- **Vendor-escalation count after exit** — log every escalation back to the vendor team. Monitor the
  monthly count across the 30- and 90-day check-ins; it should fall.

## Trade-offs
- **Staged, criteria-based transfer vs FDE time.** It costs weeks of FDE time but avoids decay after
  departure; a shorter handoff is reasonable only for simple, stable systems with experienced receiving
  teams.
- **Full ownership vs managed service.** Full handoff frees the vendor but suits customers with real
  platform capability; when the customer lacks AI skills or the system still changes weekly, a
  managed-service arrangement is safer.
- **Customer tooling vs vendor tooling.** Adopting their CI, monitoring and ticketing makes the system
  theirs, but costs migration effort and loses vendor-standard dashboards; the trade flips toward vendor
  tooling when the vendor will keep operating the system.
- **Exhaustive documentation vs maintained documentation.** Exhaustive docs feel safe but go stale
  quickly; short runbooks keyed to alerts plus decision records for non-obvious choices are maintained
  more often.

## Pitfalls
- **Credentials tied to the FDE's personal accounts.** Symptom: an outage weeks after departure, when the
  account is deprovisioned or a personal API key expires.
- **A last-week document dump.** The receiving team cannot answer basic "what do I do when X alerts"
  questions at the exit review, or nobody holds one.
- **Handing off to a team with no allocated capacity.** Alerts go unacknowledged and the triage cadence
  quietly stops within a month.
- **Undocumented design rationale.** The customer "simplifies" a deliberate choice, such as a retrieval
  filter or prompt constraint, and an old failure returns.
- **An eval suite that only runs on the FDE's laptop.** The first vendor model upgrade after handoff ships
  untested and quality shifts.
- **Every exit task done by one customer engineer.** The system runs fine until that person leaves or is
  on holiday, then the next incident takes days.
- **A vague support boundary.** Tickets bounce between vendor support and the customer team, and
  resolution times climb.

## When to use
- **Plan it at the start of the engagement,** not the end. Who will own the system should shape early
  choices: which repos, which CI, which monitoring stack, and whose identities.
- **Use the full staged process** for any system with real users, consequential outputs, or ongoing change.
- **Avoid the full process** for a throwaway POC; a short readme is enough.
- **Measure success after handoff,** not at the exit meeting: incident time-to-resolve by the customer
  team, changes shipped through the gate, triage continuity, and quality trends at 30 and 90 days.
- **Interviews:** the signal is treating handoff as transfer of responsibility with exit criteria.

## Role lens
- **Engineer:** Moves code, prompts and infrastructure into customer-owned repos and identities, and makes
  the eval suite and gates runnable in the customer's CI without vendor-only tooling.
- **Architect:** Draws the ownership boundary and support model, decides between full handoff and managed
  service, and keeps the design rationale in decision records.
- **FDE:** Runs the graduated transfer, designs the game day, judges readiness against evidence, and
  negotiates a later exit when a criterion is not met.

## Check yourself
**Q:** The engagement contract ends in two weeks, but the customer team has not yet shipped a change
through the release gate on its own. What do you do?
**A:** I would not treat the contract date as proof of readiness. First, I make the gap visible to both
sponsors: which exit criteria have evidence and which do not, and what risk each open item carries. In
the two weeks, I prioritise the highest-risk missing capability, usually a gated change and an incident
drill, by having the customer do them with me as backup. If they still are not ready, I propose
options: a short paid extension, a defined post-handoff support window with named escalation, or a
managed-service arrangement for the parts they cannot yet own. Then the sponsor chooses with the risk
written down.

## Interview questions
### What exactly is transferred in a handoff, and why is documentation not enough?
- **Ownership in five areas:** run, change, quality, access and accounts, and the vendor interface, written
  as a signed RACI.
- **Documentation transfers facts, not skill.** Operating an AI system means triaging regressions and
  resolving incidents, which people learn by doing with a safety net.
- **Tacit knowledge** (why a prompt is phrased a certain way, which slices are noisy) needs decision records
  and paired work, not a final-week walkthrough.

### Design the exit criteria for handing off a RAG assistant.
- **Tasks with checkable evidence:** a prompt change shipped through the gate, a regression correctly read,
  a game-day incident resolved from the runbook, a secret rotated, a re-index or model upgrade run with the
  suite, a weekly triage run unaided.
- **Evidence a third party can verify:** merged pull requests, gate reports, incident tickets.
- **Two people per area,** so the single point of failure does not move from vendor to customer.

### When would you not do a full handoff?
- **When the receiving team has no allocated capacity or AI skills,** because a handoff to nobody decays.
- **When the system still changes weekly,** since the vendor would be re-explaining constantly.
- **The alternative** is a managed-service arrangement or a longer transition with training, chosen by the
  sponsor with the risk written down.

### Three months after exit, vendor escalations are flat at around ten a month. What does that tell you and what do you do?
- **Ownership has not moved:** the customer is still operating the system through the vendor's people.
- **Classify the escalations** by ownership area to find which capability is missing, for example model
  upgrades or quality triage.
- **Run a targeted transfer** for that area: paired work, a drill, and evidence from two people, then
  re-check at the next check-in.

### What usually causes an outage a few weeks after the FDE leaves?
- **Credentials tied to the FDE personally:** the account is deprovisioned or a personal API key expires.
- **Prevention:** re-issue every credential to customer-owned service identities in the first weeks and
  revoke personal ones, with an inventory of owners and expiry dates.
- **Detection:** alerts that route to the customer's on-call, not to a channel nobody watches.

## Quiz
### Q: Why are handoff exit criteria best expressed as tasks the customer team has performed?
- [ ] Because contracts legally require task-based acceptance
- [x] Because operating an AI system is a skill, and only doing tasks shows the team can handle them unaided
- [ ] Because tasks are easier to schedule than reviews
- [ ] Because documentation cannot describe AI systems accurately
> Reading runbooks does not prove someone can triage a regression or resolve an incident. Demonstrated
> tasks with evidence do. Dates and documents are inputs to readiness, not proof of it.

### Q: Weeks after an FDE leaves, the assistant stops working with authentication errors to the model API. What handoff step most likely was missed?
- [ ] Moving prompts into the customer's prompt registry
- [ ] Running a game-day incident drill
- [x] Re-issuing credentials to customer-owned service identities and revoking personal ones
- [ ] Writing decision records for the retrieval design
> Credentials issued to an individual's account break when that account is deprovisioned or the key
> expires. Re-issuing them to customer service identities is a core handoff step.

### Q: When is a managed-service arrangement a better choice than a full handoff?
- [x] When the customer lacks the skills or capacity to operate the system and it still changes frequently
- [ ] When the customer has a mature platform team with on-call rotations
- [ ] Whenever the system uses a third-party model API
- [ ] When the documentation is complete
> Full ownership needs a capable, staffed receiving team. Without one, or while the system is still
> changing quickly, the vendor operating it avoids handing responsibility to nobody.
