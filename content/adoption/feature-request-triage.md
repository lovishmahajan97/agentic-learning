---
id: feature-request-triage
module: adoption
name: Feature-request triage
kind: practice
difficulty: core
est_minutes: 14
status: reviewed
related:
  - customer-data-evaluation/feedback-loops-to-product
  - delivery-ownership/productizing-repeated-customer-solutions
  - customer-data-evaluation/production-feedback-classification
  - adoption/continuous-improvement
  - adoption/stakeholder-management
  - strategy/ai-roadmap
  - scoping/problem-decomposition
---

## TL;DR
Feature-request triage turns the stream of customer asks into routed decisions. Capture the underlying
problem rather than the proposed solution. Classify what the ask really is: in AI products, many
"feature requests" are quality failures, training gaps or model limits in disguise. Cluster across
customers, score with explicit criteria, route each item, and tell the requester the outcome. It exists
so the roadmap follows evidence instead of whoever asks loudest, and so field teams stop silently forking
the product.

## Why it exists
A deployed AI system attracts asks constantly: in workshops, in Slack, from the sponsor, and from end
users during training sessions. An FDE hears dozens a week. Two naive responses both fail. Saying yes in
the field ("we can add that") wins goodwill this week. It also produces promises product never made,
custom branches that make every upgrade painful, and a roadmap set by one account. Forwarding everything
to the product team unprocessed buries real signal in noise: twenty differently worded requests for the
same capability look like twenty small asks instead of one large one.

AI products add a twist. A large share of asks are not features at all. "Make it more accurate on
foreign-exchange questions" is a quality failure that belongs in the continuous-improvement loop with a
regression case, not in a backlog for next quarter. "Let it handle bulk uploads" may already exist, and
the real need is training. "Guarantee it never makes a mistake" is a model capability limit that needs an
expectation reset and a human-review design, not engineering work. Misrouting these is costly: users keep
suffering a quality bug while it sits in a product queue, or product time goes to building what already
exists.

## How it works
### 1. Capture the problem, not the solution
Record each request in a standard form: who asked (role and account), the job they are trying to get done,
the current workaround, what happens if nothing changes (blocks a deal, risks renewal, costs time), how
often it occurs, and a concrete example. Keep asking "what would that let you do?" to get from "add an
Excel export button" to "finance reconciles AI answers against the ledger monthly". You need the problem
*because* clustering and solution design both depend on it; the customer's proposed solution is one
option among several.

### 2. Classify what the ask really is
- **Quality failure:** the system already tries to do this and gets it wrong. Route it to failure triage,
  add a regression case, and fix it through the release gate.
- **Usage or knowledge gap:** the capability exists. Route it to enablement, and fix the docs or UI if
  many people miss it.
- **Configuration:** achievable with existing prompts, knowledge sources, settings or permissions. The field
  team does it.
- **Customer-specific extension:** a real need unique to this customer, such as an integration with their
  ERP. Build it on a supported extension point, never as a fork.
- **Product gap:** a capability missing for a class of customers. It goes to the product backlog with evidence.
- **Capability limit:** not reliably achievable with current models. Reset expectations and propose
  mitigations such as human review, narrower scope or confidence thresholds.

Ask the classifying questions in a fixed order, cheapest and most urgent first. Does the system already
attempt this? Look at the traces for the requester's example: if it tries and fails, it is a quality
failure, which goes first *because* users are being hurt now. If it succeeds when used correctly, it is a
usage gap. Can existing configuration do it? Can current models do it reliably? Try it on a couple of
dozen real examples before calling it a product gap, because "the model can't" and "nobody tried" look
the same from a ticket. Only then ask whether the need is unique to this customer or plausibly shared
by others. Split compound asks: "make pricing answers right and never wrong" is one quality failure plus
one capability limit, and each half has a different route.

### 3. Deduplicate and cluster across customers
Group requests by underlying problem, not by wording. One cluster with five accounts and concrete examples
is far stronger evidence than five separate tickets. A shared tagging taxonomy across all field teams is
what makes this possible.

### 4. Score with explicit criteria
A common framework is RICE, popularised by Intercom: Reach × Impact × Confidence ÷ Effort. Reach is
originally people or events per period; across customers, distinct accounts with the problem is a
reasonable proxy. Impact uses a coarse scale (3 massive, 2 high, 1 medium, 0.5 low, 0.25 minimal),
confidence a percentage that discounts guesses, and effort person-weeks or person-months. The coarse
scales are deliberate: they stop the team arguing over decimals and make the score a ranking aid, not a
measurement. Add strategic fit, and risk (safety,
compliance, maintenance burden). Record commercial weight (ARR, deal-blocking status) as a *separate*
input, not multiplied into the score, *because* folding revenue into the score lets the biggest account set
the roadmap without anyone deciding that on purpose.

### 5. Route and decide
Field fixes happen now. Product gaps go to the product owner, who decides; the FDE supplies evidence, not
commitments. Declines get a written reason and, where possible, a workaround. Apply a recurrence rule:
when the same customer-specific extension has been built for several customers, propose productizing it.

### 6. Close the loop
Tell every requester what happened and why, including "no". A visible outcome keeps customers reporting
real problems, instead of escalating the same ask to their executive sponsor.

## Example
A weekly triage pass over seven captured requests from five accounts. Requests are clustered by underlying
problem and type, each cluster is routed, and only product gaps get a RICE score. ARR is shown next to the
score but kept out of it. Impact, confidence and effort are the team's estimates.

```python
from collections import defaultdict

# (account, arr_k, problem_key, kind)
requests = [
    ("Acme", 400, "cite-source-page", "product_gap"),
    ("Globex", 250, "cite-source-page", "product_gap"),
    ("Initech", 120, "cite-source-page", "product_gap"),
    ("Acme", 400, "sap-form-export", "customer_specific"),
    ("Globex", 250, "stale-fx-rates", "quality_failure"),
    ("Umbrella", 90, "bulk-upload", "usage_gap"),
    ("MegaCorp", 2000, "approval-workflow", "product_gap"),
]
ROUTE = {
    "quality_failure": "failure triage + regression case",
    "usage_gap": "enablement; fix discoverability",
    "customer_specific": "field build on extension point",
    "product_gap": "product backlog with evidence",
}
# Team estimates per product-gap cluster: impact (0.25-3), confidence (0-1), effort (weeks).
ESTIMATES = {"cite-source-page": (2, 0.8, 3), "approval-workflow": (2, 0.8, 3)}

clusters = defaultdict(list)
for acct, arr, problem, kind in requests:
    clusters[(problem, kind)].append((acct, arr))

for (problem, kind), rows in clusters.items():
    accounts = {a for a, _ in rows}
    arr = sum(dict(rows).values())          # ARR per distinct account
    line = f"{problem:17} {kind:17} accounts={len(accounts)} arr=${arr}k -> {ROUTE[kind]}"
    if kind == "product_gap":
        impact, conf, effort = ESTIMATES[problem]
        line += f" | RICE={len(accounts) * impact * conf / effort:.2f}"
    print(line)
```

Seven tickets become five decisions. Page-level citations is one cluster spanning three accounts
(RICE 3 × 2 × 0.8 ÷ 3 = 1.60, ARR $770k), so it goes to product with three concrete examples. The
approval workflow is classed as a product gap because approval chains are common in the segment, but
only one account has asked; with identical estimates it scores RICE 0.53 at ARR $2,000k. Had reach been
weighted by ARR, the order would flip: 2,000 against 770. Neither ordering is automatically right; the
separate column puts that choice in front of the product owner instead of letting arithmetic make it. The stale FX rates go straight into the quality loop this week.
The SAP export is built by the field team on the integration API. Bulk upload already exists, so the
action is a training note and a UI change review.

## Trade-offs
- Building a customer-specific feature in the field can save a deal now, but every fork taxes later upgrades; it is acceptable only on a supported extension point, with a recurrence rule for productizing it.
- Weighting by ARR aligns with revenue but lets one large account dominate; keep it a separate, visible input so product leadership makes that trade-off deliberately.
- Detailed capture and clustering take FDE time each week, but they turn anecdotes into evidence; for a single small pilot with one stakeholder, a lightweight list may be enough.
- Sharing roadmap intent builds customer trust, but it creates implied commitments; share problem clusters and status, not delivery dates, unless product has committed.

## Pitfalls
- Logging the proposed solution instead of the problem: the backlog fills with many near-duplicate items, and the cluster that matters never looks big.
- Routing quality failures to the product backlog: users keep reporting the same wrong answers for weeks while the item waits for roadmap planning.
- Field promises the product never made: the symptom is an escalation from the customer's executive asking where "the committed feature" is.
- Letting the largest customer's asks dominate: the roadmap drifts toward one account's workflow, and other customers' adoption stalls.
- Treating capability limits as features: an engineering effort chases "never wrong" for months instead of designing human review and scope limits.
- No closure to requesters: the same asks reappear louder through other channels, and feedback from end users dries up.

## When to use
Run triage whenever a deployed system has users who can ask for things, which is from the first pilot
week onward. Use a weekly cadence while volume is high, and hold a cross-customer review with product
monthly or per planning cycle. The full process matters most for platforms serving several customers.
There, clustering and the decline-with-reason discipline protect the roadmap. Measure time from request
to decision, the share of requests reclassified as quality or usage gaps, and how many field extensions
were later productized. In interviews, the signal is separating problem from solution and routing
AI-specific asks correctly.

## Role lens
- **Engineer:** Builds the capture form and tagging, and turns quality-failure requests into regression
  cases instead of feature tickets.
- **Architect:** Defines the supported extension points that make customer-specific asks buildable
  without forks, and sets the rule for when a pattern gets productized.
- **FDE:** Interviews requesters to get to the underlying problem, classifies and clusters asks, and
  delivers clear decisions back to customers without making roadmap commitments.

## Check yourself
**Q:** A customer's VP says, "We need the assistant to stop giving wrong answers about our pricing; put
it on your roadmap for next quarter." How do you triage this?
**A:** I would not treat it as a roadmap item. It is most likely a quality failure, so I collect concrete
examples and check the traces. Likely causes are retrieval of outdated pricing documents, missing
sources, or a prompt that lets the model guess. Those go into failure triage now, with regression cases
and a fix through the release gate, often within days. If part of the ask is "never be wrong", I explain
that as a model limit and propose mitigations: citing the pricing source, refusing when no document
matches, or human review for quotes. Then I tell the VP the plan and timeline.

## Quiz
### Q: Why should triage record the requester's underlying problem rather than their proposed solution?
- [ ] Proposed solutions are usually technically infeasible
- [x] Clustering across customers and choosing the best solution both depend on the problem, while solutions are worded differently
- [ ] Product teams refuse to read solution proposals
- [ ] It reduces the number of requests that must be logged
> Different customers propose different solutions to the same problem. Capturing the problem reveals the
> real size of the need and leaves room for a better design than any single proposal.

### Q: A user asks for the assistant to "be more accurate on currency conversions", and traces show it citing last year's FX table. Where should this go?
- [ ] The product backlog as a new feature for the next planning cycle
- [ ] Enablement, because the user should check the rates themselves
- [x] The continuous-improvement loop as a quality failure, with a regression case and a data or retrieval fix
- [ ] Decline it as a model capability limit
> The system already tries to answer this and fails because of stale data. That is a quality failure,
> fixed fastest through failure triage and the release gate, not a roadmap item.

### Q: Why keep ARR as a separate input rather than multiplying it into the priority score?
- [ ] ARR data is usually confidential and cannot be stored
- [ ] Revenue should never influence product decisions
- [x] Folding it in lets the largest account set the roadmap implicitly, while a separate input makes that trade-off a deliberate decision
- [ ] RICE scores are only defined for non-financial inputs
> Revenue matters, but hidden inside a score it silently outweighs reach and impact. Showing it
> separately lets product leaders decide openly when to favour a big account.
