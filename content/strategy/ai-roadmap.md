---
id: ai-roadmap
module: strategy
name: AI roadmap
kind: practice
difficulty: core
est_minutes: 16
status: reviewed
related:
  - scoping/ai-use-case-discovery
  - scoping/roi-model
  - scoping/pilot-to-production-plan
  - security-governance-compliance/ai-inventory-and-use-case-risk-classification
  - evals/eval-driven-development
  - strategy/platform-strategy
  - strategy/stakeholder-alignment
---

## TL;DR
An AI roadmap is a sequenced portfolio of use cases, each treated as a hypothesis with an evidence
gate, plus the shared capabilities (data access, evaluation, platform, governance) that the early bets
build for the later ones. It exists because AI projects fail mostly on data, evaluation and adoption
rather than on models, and because model capability shifts faster than annual plans, so the roadmap
must commit to outcomes and learning milestones, not to specific models or dates for everything.

## Why it exists
Most organisations start with a list: twenty ideas collected in workshops, each championed by someone,
none compared on the same terms. The naive plan picks the most exciting ones, staffs them in parallel and
promises dates. Three things then go wrong. First, the exciting ideas are often the ones with the least
ready data or the highest risk (customer-facing, regulated decisions), so they stall in pilot. Second,
every team rebuilds the same plumbing (model access, document ingestion, an eval set, logging), so the
fifth project costs as much as the first. Third, the plan is pinned to a specific model or vendor, and
when capability or pricing moves a few months later, half the plan is obsolete while the other half
solved problems that a new model now handles out of the box.

A feature roadmap from ordinary software does not transfer cleanly either. In normal software, if you
build the feature to spec, it works. In AI, you can build the system exactly as designed and still find
it is only right some of the time, which is not enough for the workflow. Feasibility is an empirical
question answered by an eval on real data, so the roadmap has to schedule *finding out* before it
schedules *scaling*. Without that, the organisation discovers in month nine that a flagship use case
cannot clear the accuracy bar, and the credibility loss poisons the next proposal.

## How it works
### 1. Build the candidate list from workflows, not technology
Each candidate is a specific workflow step with an owner, a volume, a current cost or cycle time, and a
user who would act on the output ("triage inbound claims emails into twelve queues", not "use GenAI in
claims"). Frame it this way *because* value, baseline and adoption can only be measured on a workflow.
Use-case discovery (see the scoping module) is where this list comes from; the roadmap is what you do
with it.

### 2. Score on value and on feasibility separately
Value is a small formula, not an adjective:

```
annual value ≈ volume × value per unit × capture rate × automation share − (run cost + review cost)
```

Capture rate is the part people skip: hours "freed" are worth nothing unless someone redeploys them or a
cost line actually moves. Automation share is how much of the volume the system can handle at an
acceptable error rate, which you do not know until the feasibility eval, so early value scores are
ranges. Run cost is inference at production volume, not pilot volume; a use case that is valuable at
$0.02 per item can be uneconomic at $0.50. Feasibility has several independent axes, scored separately
*because* a single gut-feel number hides which one will kill the project:
- **Data readiness:** does the input data exist, can you legally use it, can you get access this quarter?
- **Evaluability:** can you build a labelled set and define "correct"? If experts disagree on the answer,
  you cannot prove the system works.
- **Error tolerance:** what does a wrong output cost, and is there a human checkpoint? Drafting with review
  tolerates errors that autonomous decisions do not.
- **Risk class:** regulatory, privacy and reputational exposure (feeds the AI inventory and risk tiering).
- **Integration effort:** read-only assistance is cheap; writing back into systems of record is not.

### 3. Sequence for capability, not just for score
Order the portfolio so early projects are high-feasibility, moderate-value and *build reusable assets*:
an access path to the document store, an evaluation harness, a gateway with logging, a review UI. Later,
higher-value but riskier projects then start with those assets in place. This is why the top-scoring
idea is not always first: if it needs four foundations nobody has built, it goes second or third, behind
a project that builds two of them.

### 4. Decide build, buy or wait for each item
For each candidate ask whether a vendor product already does it acceptably (buy), whether it needs your
data and workflow integration (build), or whether the blocker is raw model capability that the market is
improving quickly (wait). "Wait" is a legitimate roadmap state *because* building elaborate workarounds
for a capability gap is often wasted when the next model release closes it; but a waiting item needs a
trigger, such as "re-run the feasibility eval whenever a new model in our approved set ships", or it is
just a polite way of dropping it.

### 5. Attach a gate to every stage
Each use case moves through discovery → feasibility eval → pilot → production → scale, and each
transition has a pre-agreed criterion and a kill condition. Example: "proceed to pilot if the offline eval
on 300 labelled cases meets the agreed accuracy bar at the agreed cost per item; stop if after two
iterations it is still far below." Gates make cancelling cheap and normal, *because* the decision rule
was set before anyone was invested in the answer. The economics favour this: a feasibility eval costs
weeks and a production build costs months, so the early gate is buying information cheaply before the
expensive commitment. Gates also change what they measure as the item matures: the feasibility gate
checks offline accuracy and cost per item; the pilot gate checks whether users actually act on the output
and whether the workflow metric moved; the production gate checks cost at full volume, latency, monitoring
and an owner on call.

### 6. Plan in horizons and re-plan on a cadence
Near term (this quarter): committed scope and dates. Mid term: ranked candidates with the foundations they
need. Long term: themes and bets, explicitly unscheduled. Review quarterly, and re-score whenever the
model landscape moves: a capability jump can promote a parked use case; a price drop can flip one from
uneconomic to obvious. Commit to *capabilities required* ("extract fields from scanned forms at this
accuracy"), not to a named model, so the roadmap survives model churn.

### 7. Make the portfolio visible
Keep it as a living artefact: each row has owner, stage, gate criterion, latest eval result, cost per
item, and next decision date. This is also what governance needs for the AI inventory.

## Example
A scoring pass over a small portfolio. Scores are 1–5 judgements from workshops; the numbers are
illustrative, the mechanics are the point.

```python
cands = {  # value, data, evaluability, error_tolerance, needs (foundations)
    "claims_email_triage": (3, 5, 5, 4, {"gateway", "eval_harness"}),
    "contract_clause_qa":  (4, 4, 3, 3, {"gateway", "eval_harness", "doc_ingest"}),
    "auto_claim_decision": (5, 2, 2, 1, {"gateway", "eval_harness", "doc_ingest", "review_ui"}),
    "agent_reply_drafts":  (3, 4, 3, 4, {"gateway", "review_ui"}),
}
def feasibility(d, e, t):
    return min(d, e, t)          # the weakest axis is what kills a project, so use min, not mean

built, plan = set(), []
remaining = dict(cands)
while remaining:
    def priority(item):
        v, d, e, t, needs = item[1]
        new_work = len(needs - built)            # foundations this project would have to build
        return v * feasibility(d, e, t) - 2 * new_work
    name, (v, d, e, t, needs) = max(remaining.items(), key=priority)
    plan.append((name, v * feasibility(d, e, t), sorted(needs - built)))
    built |= needs
    del remaining[name]

for step, (name, score, builds) in enumerate(plan, 1):
    print(step, name, "score", score, "builds", builds)
# 1 claims_email_triage score 12 builds ['eval_harness', 'gateway']
# 2 contract_clause_qa score 12 builds ['doc_ingest']
# 3 agent_reply_drafts score 9 builds ['review_ui']
# 4 auto_claim_decision score 5 builds []
```

The highest-value item, automated claim decisions, comes last: its weakest axis (error tolerance 1) caps
its feasibility, and by the time it starts every foundation it needs already exists. Contract Q&A
beats reply drafting for second place because, once the gateway and harness exist, each needs exactly one
new foundation (doc ingestion vs. review UI), so the tie-break is the base score: 12 against 9. Using `min` rather
than an average is deliberate: excellent data does not rescue a use case nobody can evaluate. The
penalty of 2 points per new foundation is a judgement call, so check the plan is not an artefact of it:
here any weight from 1 to 4 gives the same order, which is what makes the sequence defensible in the
meeting. If a small change of weight reshuffles the plan, the scores are too close to decide on and the
honest move is a quick feasibility eval on the tied items.

## Trade-offs
- Starting with modest, feasible use cases builds foundations and credibility, but delays the headline
  value; it flips when one high-value case is so dominant that funding depends on attacking it first.
- Committing to capabilities instead of named models keeps the plan durable, but makes budgeting
  vaguer; once a use case is in production, pin the model and price it precisely.
- Running many small parallel pilots surfaces feasibility fast, but spreads scarce expert labellers and
  platform engineers thin; with a small team, run two or three sequentially with hard gates.
- Building shared foundations early lowers marginal cost later, but risks a platform with no users;
  build each foundation only when a scheduled use case needs it.

## Pitfalls
- Ranking by excitement or executive sponsorship alone; the symptom is a pilot graveyard where several
  projects sit for months at "almost good enough".
- No kill criteria at gates; projects never stop, they just quietly lose staff, and the portfolio view
  shows many items "in progress" with no recent eval result.
- Counting freed hours as value without a plan to redeploy them; finance later finds no cost moved and
  the next roadmap gets less funding.
- Pinning the plan to one model's capabilities; when a new release changes what is easy, the team keeps
  building workarounds that the new model makes unnecessary.
- Skipping baseline measurement before deployment; the symptom is a launch review where the system
  looks good but nobody can say by how much it beats the old process, so the next item gets no funding.
- Treating a pilot pass as production readiness; costs and error rates jump at full volume or on input
  types the hand-picked pilot users never sent, and the item slides back to "almost done".

## When to use
Write a roadmap as soon as there are more candidate use cases than people to build them, which is almost
immediately. Keep it short-horizon and evidence-driven: the quarter's commitments in detail, the rest as
ranked hypotheses. Measure each item with its gate metric (offline eval score, cost per item, adoption in
pilot) and review quarterly or when the model market moves. Measure the portfolio too: time from idea to
feasibility verdict, the share of items with a gate decision in the last quarter, and the kill rate. A
kill rate of zero is not a sign of good ideas; it means the gates are not being applied. Do not produce a three-year plan of dated AI
features; it will be wrong within months and will make every later change look like failure. In
interviews, a strong answer separates value from feasibility, names evaluability and data access as the
real constraints, and explains how sequencing builds reusable capability.

## Role lens
- **Engineer:** Turns a roadmap item into a feasibility eval on real data quickly, and reports results in
  the gate's terms so the go/stop call is obvious.
- **Architect:** Owns sequencing and the foundation list: which shared capabilities each stage builds, and
  which risk tier each use case sits in.
- **FDE:** Runs discovery inside the customer, scores their candidates honestly, and steers them away
  from the flagship-but-infeasible use case toward a first win that unlocks the rest.

## Check yourself
**Q:** An executive wants the roadmap to lead with fully automated loan decisions because it has the
largest projected savings. How do you respond?
**A:** I would not refuse it, I would sequence it. Automated credit decisions score high on value but low
on error tolerance and high on regulatory risk, and they need foundations we lack: document ingestion, an
eval set agreed by credit experts, an audit trail and a review UI. I would propose a first stage that
builds those through a lower-risk adjacent use case, such as extracting and summarising applications for
underwriters, with a gate: if extraction accuracy on a few hundred labelled files meets the bar, proceed
to decision support with a human approving, and only consider autonomy once the error profile is measured
in production. The savings case stays on the roadmap, with evidence gates instead of a date.

## Quiz
### Q: Why score feasibility as the minimum of its axes rather than their average?
- [ ] Averages are harder to compute consistently across workshops
- [x] A single blocking constraint, such as no way to evaluate correctness, stops a project regardless of strengths elsewhere
- [ ] The minimum always produces a higher priority for high-value projects
- [ ] Averaging would double-count the value score
> Feasibility constraints are conjunctive: excellent data cannot compensate for an output nobody can
> verify. An average lets one strong axis mask the one that will actually stall the project in pilot.

### Q: What is the main reason an AI roadmap should commit to required capabilities rather than named models?
- [ ] Named models are not allowed in governance documents
- [ ] Capabilities are easier to explain to executives
- [x] Model capability and pricing change faster than the planning cycle, so model-pinned plans go stale
- [ ] Vendors do not allow customers to plan around specific model versions
> Over a planning horizon, new releases change what is easy and what it costs. Stating the capability and
> its bar lets any model that clears it be used, while production items still pin a specific model.

### Q: A use case has the highest value score but needs four foundations nobody has built. What does good sequencing usually do?
- [ ] Start it first because value dominates every other factor
- [ ] Drop it from the roadmap as infeasible
- [x] Schedule it after projects that build those foundations, so it starts with them in place
- [ ] Build all four foundations as a standalone platform project before any use case
> Sequencing for capability lets cheaper, feasible projects build the shared assets while producing value.
> Building foundations with no consuming use case risks an unused platform; dropping it discards the value.
