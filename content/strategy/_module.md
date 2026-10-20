---
id: strategy
title: Strategy
---

## Overview
This module is about the decisions that sit above any single AI system: which use cases to pursue and
in what order, who has to agree before building starts, which vendors to depend on, what to provide
once as a shared platform, and whether the AI claims behind a deal hold up. It opens with the AI
roadmap, a sequenced portfolio of use cases treated as hypotheses, each scored separately on value
and on feasibility (data readiness, evaluability, error tolerance, risk class, integration effort)
and moved through evidence gates from feasibility eval to pilot to production. Stakeholder alignment
then turns the conflicting goals of operations, compliance, finance and security into one agreed
operating point on a measured trade-off curve, set against a human baseline and written on one page.
Vendor evaluation supplies the comparative method: hard gates first, then a bake-off on your own
labelled data measuring quality, latency under load and cost per successful task, then exit terms.
Platform strategy decides which recurring capabilities to centralise, using breadth of demand,
variance between teams and externalities, and when to build them: after demand appears, as a thin
mandatory core plus an optional paved road. Technical due diligence applies the same habit of turning
claims into tests to an acquisition, investment or critical partner. The thread through all five is
that AI feasibility is an empirical question, so every strategic commitment should be gated on an
eval rather than on a demo or a slide. Afterwards you can build and defend a roadmap, run a launch
decision without a late veto, select a vendor on evidence, argue a platform boundary with numbers,
and write a diligence findings list. It matters most for Architects and FDEs, and for Engineers
moving into technical leadership.

## Why it matters
- **Engineer:** You build the feasibility evals, bake-off harnesses and cost-per-item models that the roadmap gates, vendor choice and launch decision actually rest on, and you can say plainly when a use case should be killed or parked until a new model ships.
- **Architect:** You draw the platform boundary (gateway, identity, budgets, audit, telemetry in the mandatory core; prompts, corpora and agents left with teams), sequence the portfolio so early projects build reusable assets, and keep vendor seams so decisions stay reversible as the model market moves.
- **FDE:** You get the customer's business owner, compliance, security and finance to agree an operating point and decision rights before building, run the vendor bake-off on their own data, and present results against the one-pager at each gate so sign-off does not stall in pilot purgatory.

## Mental model
Hold one picture: AI strategy is portfolio management under uncertainty, where every bet is bought
with information before it is bought with engineering. Each use case is a hypothesis with a value
range and a weakest feasibility axis; an eval on real data is the cheap experiment that resolves it.
Gates set in advance decide whether a bet advances, waits for a capability trigger, or is killed.
Stakeholder alignment fixes what "advance" means: an operating point on a trade-off curve, measured
against today's human baseline, with named decision owners. Vendor evaluation and due diligence are
the same experiment pointed outward: do not trust the claim, test it on your data and price the exit.
Platform strategy is what the portfolio leaves behind: the shared parts that several bets needed,
with little variance and organisation-wide risk, pulled into a thin core once demand exists. If a
commitment does not have an evidence gate and an owner, it is not yet strategy.

## Suggested order
1. ai-roadmap — sets the portfolio frame, value-versus-feasibility scoring and stage gates that every other lesson plugs into.
2. stakeholder-alignment — defines the agreed operating point, baseline and decision rights that a roadmap gate needs before anyone can say yes.
3. vendor-evaluation — teaches the gates-then-bake-off method on your own data, the core skill for each build, buy or wait decision on the roadmap.
4. platform-strategy — builds on roadmap sequencing and vendor sourcing to decide what to centralise, when, and how to fund it.
5. technical-due-diligence — the advanced case that extends vendor evaluation's claim-to-test discipline to deals, data rights and margin models.

## Capstone
### Brief
Act as the AI lead for a mid-sized insurer (fictional, with synthetic or public data) that has
collected fifteen AI ideas from workshops, has two teams already calling a model provider with their
own keys, and is being pitched a small claims-automation startup as an acquisition. Produce a strategy
pack the leadership team could decide from in one meeting. Turn the ideas into workflow-level
candidates and score each on the value formula (volume, value per unit, capture rate, automation
share, run and review cost) and on each feasibility axis separately, then sequence the next three
quarters so early items build shared assets. For the top item, write the stakeholder one-pager:
baseline human error rate and cycle time, hard constraints kept apart from outcome goals, a trade-off
curve from a small offline eval, the chosen operating point and the launch and pause owners. Run a
light vendor bake-off for that item between at least two model providers on 100 or more labelled
synthetic cases. Propose the platform core with a break-even estimate, and write a diligence plan and
mock findings list for the acquisition target.

### Deliverables
- A portfolio sheet with one row per candidate: owner, value range, per-axis feasibility scores, build/buy/wait decision with a trigger for each "wait", current stage, gate criterion, kill condition and next decision date, plus a three-horizon sequence.
- A one-page stakeholder alignment document for the top use case, including the measured baseline on the same items the AI will handle, a confidence-threshold trade-off curve, the chosen operating point, failure handling and a RACI or DACI table.
- A re-runnable vendor bake-off (script or notebook) and a short report: gate results, quality with confidence intervals, p95 latency at expected concurrency, cost per successful task, and priced exit terms.
- A platform proposal naming the mandatory core and the optional paved road, with the centralisation break-even worked through for at least three capabilities and a funding model.
- A diligence plan that turns each of the target's headline claims into a test, with a classified findings list (deal-breaker, price adjustment, post-close fix) and a usage-sensitive margin model.

### Rubric
- Feasibility is scored per axis and the weakest axis is named for every candidate; the top-value item is not first in the sequence unless its foundations already exist, and the reason is stated.
- Every roadmap stage, the launch decision and each "wait" item has a pre-agreed numeric criterion or trigger, and at least one candidate is killed or parked on evidence.
- The alignment one-pager compares the AI's error on its automated slice with the human error on that same slice, and a reviewer can make the launch call from the one-pager and the eval without new negotiation.
- Vendors are compared on cost per successful task (retries and review included), not list price, and the bake-off re-runs from one command when a new model is added.
- The platform core is justified by externalities or the break-even inequality, at least one capability is deliberately left with teams, and each diligence claim maps to a concrete test with a named finding class.
