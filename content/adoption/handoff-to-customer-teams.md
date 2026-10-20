---
id: handoff-to-customer-teams
module: adoption
name: Handoff to customer teams
kind: practice
difficulty: core
est_minutes: 14
status: reviewed
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
Handoff is the transfer of *ownership* of a deployed AI system from the vendor or FDE team to the
customer's own engineers and operators: running it, changing it safely, and judging its quality.
Documents are not enough. It works when responsibility moves in stages, and when the exit criteria are
tasks the customer team has shown it can do (a gated prompt change, a simulated incident, a credential
rotation) rather than a calendar date.

## Why it exists
Forward-deployed work ends. The FDE moves to the next account, and the system has to keep working
without them. The common failure is not a dramatic outage on the day they leave. It is slow decay over
the following months. Nobody on the customer side knows why retrieval filters by region, so someone
"simplifies" it. Nobody can run the eval suite, so a vendor model update ships unchecked. An alert fires
into a channel nobody watches. A credential expires that was issued to the FDE's personal account. Six
months later the customer concludes "the AI didn't work", and the renewal goes with it.

The naive handoff is a document dump: a wiki page, an architecture diagram and a recorded walkthrough in
the final week. It fails because operating an AI system is a skill, not a body of facts. Reading about
triaging a quality regression does not prepare someone to do it at 2 a.m. AI systems also carry more tacit
knowledge than ordinary services. Why a prompt is phrased the way it is, which eval slices are noisy,
which failures are known and acceptable: much of that lives only in the builder's head. The forcing
problem is moving that knowledge and the matching responsibility to people who will be accountable for
the system after the builder is gone.

## How it works
### 1. Define what is being handed off
Split ownership into explicit areas, because each needs different skills and often different people:
- **Run**: on-call, alerts, incident response, capacity and cost budgets.
- **Change**: prompt and config edits, re-indexing, dependency and model upgrades, all through the
  release gate.
- **Quality**: the regression suite, failure triage, and the continuous-improvement cadence.
- **Access and accounts**: service identities, secrets, data permissions, audit, and who owns and pays
  for the model-provider and cloud accounts (a vendor-billed API account is a dependency that outlives
  the engagement unless it is moved).
- **Vendor interface**: what the vendor still supports after handoff, how to escalate, and with what SLAs.
Write this as a responsibility matrix (RACI) that both sides sign. An unclear boundary turns into
support tickets the vendor bounces back and forth with the customer.

### 2. Check the receiving team is real
Confirm named people, allocated time and the needed skills before planning the transfer. A handoff to a
team with no capacity is a handoff to nobody. If the skills are missing, the honest options are training
plus a longer transition, or a managed-service arrangement.

Plan for at least two people per ownership area. If every exit task is done by the one engineer who
paired with the FDE, the single point of failure has only moved from the vendor to the customer, and it
reappears the first time that person is on leave or changes job. A simple skills matrix (areas down the
side, named people across, a mark when someone has *done* the task) makes the gap visible early.

### 3. Make the artifacts operable, not just readable
Move infrastructure-as-code, prompts and configs into the customer's repositories and CI. Route dashboards
and alerts to the customer's on-call tooling. Write runbooks keyed to specific alerts. Make the eval suite
runnable in the customer's pipeline. Re-issue every credential to customer-owned service identities.
Write decision records for the non-obvious choices, because the "why" is what stops a well-meaning
simplification from undoing a fix. Hand over a known-issues list and the open backlog.

### 4. Transfer responsibility in stages
Use a graduated sequence: the FDE does and the customer watches; they work as a pair; the customer does
and the FDE watches (reverse shadowing); the customer works alone with the FDE as backup; exit. Each stage
moves the operator role one step, *because* people build operational skill by doing the task with a safety
net, not by reading about it.

### 5. Exit on demonstrated capability
Exit criteria are tasks with evidence. For example, the customer team shipped a prompt change through the
gate, ran the suite and correctly interpreted a regression, resolved a game-day incident using the
runbook, rotated a secret, and ran a weekly triage without help. A date can be a target, but the criteria
decide. Each criterion should be checkable by someone other than the FDE (a merged pull request, a gate
report, an incident ticket), because the FDE is the person with the strongest incentive to declare
victory on schedule.

### 6. Taper, then check in
Keep a defined hypercare window with reducing FDE involvement. Then schedule 30- and 90-day check-ins on
the same adoption and quality metrics, so post-handoff decay shows up as a trend and not as a churn surprise.
Track escalations back to the vendor per month as well: a falling count means ownership has moved; a
flat or rising one means the customer is still operating the system through the vendor's people.

## Example
A contract-review assistant built by a two-person FDE team is handed to the customer's platform team
(three engineers and one operations analyst) over six weeks. The timeline is illustrative.

- **Weeks 1–2 (FDE does, customer watches):** The RACI is agreed. Repos, Terraform and the prompt registry
  move into the customer's GitHub organisation. The FDE re-issues the model-API key and the document-store
  credentials to customer service accounts, and the personal keys are revoked. Alerts route to the
  customer's PagerDuty rota with the FDE as secondary.
- **Weeks 3–4 (pair, then reverse shadow):** A customer engineer makes a real change: adding the new
  indemnity clause type to the extraction prompt. They add twelve labelled contracts to the suite first,
  run the gate, see the "governing law" slice drop three points, fix the instruction conflict, and ship
  behind the canary. The analyst runs the weekly failure triage while the FDE observes.
- **Week 5 (customer alone, FDE backup):** Game day. The FDE injects a failure that empties retrieval
  results for one business unit. The on-call engineer finds it from the alert and the runbook in
  22 minutes, traces it to a revoked index permission, and restores it. The runbook gets two corrections.
- **Week 6 (exit review):** Each criterion is checked against evidence: change shipped through the gate,
  incident resolved, secret rotated, triage run unaided, decision records reviewed. The skills matrix shows
  each task was done by only one engineer, and one item fails outright: nobody has done a vendor
  model-version upgrade. Exit is moved a week. In that week the customer runs the upgrade with the suite,
  and a second engineer repeats the gated change and the secret rotation, before sign-off. Check-ins are booked for day 30 and day 90.

## Trade-offs
- A staged, criteria-based transfer costs weeks of FDE time, but it avoids decay after departure; a shorter handoff is reasonable only for simple, stable systems with experienced receiving teams.
- Handing off full ownership frees the vendor, but it suits customers with real platform capability; when the customer lacks AI skills or the system still changes weekly, a managed-service arrangement is safer.
- Adopting the customer's tooling (their CI, monitoring and ticketing) makes the system theirs, but it costs migration effort and loses vendor-standard dashboards; the trade flips toward vendor tooling when the vendor will keep operating the system.
- Exhaustive documentation feels safe but goes stale quickly; short runbooks keyed to alerts plus decision records for non-obvious choices are maintained more often.

## Pitfalls
- Credentials tied to the FDE's personal accounts: the symptom is an outage weeks after departure, when the account is deprovisioned or a personal API key expires.
- A last-week document dump: the receiving team cannot answer basic "what do I do when X alerts" questions at the exit review, or nobody holds one.
- Handing off to a team with no allocated capacity: alerts go unacknowledged and the triage cadence quietly stops within a month.
- Undocumented design rationale: the customer "simplifies" a deliberate choice, such as a retrieval filter or prompt constraint, and an old failure returns.
- An eval suite that only runs on the FDE's laptop: the first vendor model upgrade after handoff ships untested and quality shifts.
- Every exit task done by one customer engineer: the system runs fine until that person leaves or is on holiday, then the next incident takes days.
- A vague support boundary: tickets bounce between vendor support and the customer team, and resolution times climb.

## When to use
Plan the handoff at the start of the engagement, not at the end. Who will own the system should shape
early choices: which repos, which CI, which monitoring stack, and whose identities. Use the full staged
process for any system with real users, consequential outputs, or ongoing change. For a throwaway POC,
a short readme is enough. Measure success after handoff, not at the exit meeting: incident time-to-resolve
by the customer team, changes shipped through the gate, triage continuity, and quality trends at 30 and
90 days. In interviews, the signal is treating handoff as transfer of responsibility with exit criteria.

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
