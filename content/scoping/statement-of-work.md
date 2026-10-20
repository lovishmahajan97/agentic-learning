---
id: statement-of-work
module: scoping
name: Statement of work
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - scoping/success-metrics-kpis
  - scoping/requirements-traceability
  - scoping/dependency-and-risk-management
  - scoping/timeboxing
  - scoping/pilot-to-production-plan
  - evals/task-specific-acceptance-criteria
  - delivery-ownership/operational-ownership-and-support-boundaries
---

## TL;DR
A statement of work (SOW) is the project-specific contract that says what will be delivered, how
acceptance is decided, what the customer must provide and by when, what is out of scope, and how
changes are handled. AI work needs a stricter SOW than ordinary software, because outputs are
probabilistic and progress depends on customer data and people. Acceptance must be statistical
against an agreed eval set, and customer dependencies must be dated obligations.

## Why it exists
Two parties start a project with different pictures of the finish line. The customer imagines "an
assistant that answers our policy questions correctly." The vendor or internal team imagines "a RAG
prototype over the documents you give us." Both pictures are reasonable, and the gap stays invisible
until the delivery date, when it becomes a dispute about money and reputation. The SOW makes the
pictures match in writing while changing them is still cheap.

Generic software SOWs fail on AI work in specific ways. "The system shall answer questions correctly"
cannot be accepted or rejected, because any probabilistic system answers some questions wrongly, so the
customer can always find a failure. "Customer will provide data" with no date or format turns a
three-week data delay into the delivery team's missed milestone. A fixed-scope, fixed-price commitment
to a quality level nobody has measured on the customer's data transfers unpriced research risk to
whoever signed it. And when nothing says who owns the eval set, prompts and fine-tuned weights, or who
operates the system after go-live, those questions come up at the worst moment. The SOW exists to settle
these questions before work starts, while each side can still walk away.

## How it works
### Where it sits
Commercial engagements usually have a master services agreement (MSA) holding the legal terms, such
as liability, confidentiality, IP defaults and payment. Each project then gets an SOW under it with the
specifics. An order-of-precedence clause says which document wins when they conflict. Internal projects often
have a charter or project brief that does the same job. The
structure below applies to both. The legal terms are for counsel, and this lesson is not legal advice.

### Core sections
1. **Objective and success metrics.** The business problem and the agreed metric tree, so every
   deliverable can be traced to a purpose.
2. **Scope and deliverables.** These are concrete artefacts: a deployed service in a named
   environment, an eval report, a runbook, a handover session. Name the use cases, document types,
   languages and channels that are in scope.
3. **Out of scope.** This is explicit, because scope disputes are almost always about what was
   *assumed* to be included: other languages, write-back to the CRM, mobile, 24/7 support.
4. **Acceptance criteria.** Each deliverable has an observable test, and for model behaviour that test
   is statistical. "On the agreed held-out eval set of N cases, field accuracy is at least X% overall
   and at least Y% on each named critical field, with no critical-severity failures, measured by the
   agreed harness." Name who runs the test, how long the customer has to accept or reject, and what
   happens if they do not respond, for example deemed acceptance after 10 business days.
5. **Customer responsibilities.** List each as a dated obligation: data extract in a specified format
   by a date, SSO and network access by a date, SME hours per week for labelling and review, security
   review turnaround. Then state the consequence when one slips: the timeline moves day for day.
6. **Assumptions.** These are the facts the plan rests on, such as volumes, document formats, API
   availability and model provider access. Each one, if false, triggers change control.
7. **Timeline and milestones**, ideally phase-gated: discovery, then proof of concept, then pilot,
   then production. Each gate has exit criteria and a go/no-go decision.
8. **Change control.** How a change is requested, estimated, approved and priced. Without it, small
   additions accumulate unpaid.
9. **Roles, governance and support boundaries.** Who decides, the steering cadence, the escalation
   path, and who operates, monitors and pays for inference after go-live.
10. **Commercials.** Fixed fee, time and materials, or milestone-based payment, tied to the gates.

### AI-specific clauses
- **Eval set ownership and freeze.** Agree who builds the acceptance set (ideally jointly with the
  customer's SMEs), that it is representative, and that it is frozen before tuning, so acceptance does
  not chase a moving target.
- **Model and provider changes.** State what happens if the provider deprecates the model or changes
  pricing mid-project, and that re-validation counts as a change.
- **Phase-based commitment.** Commit to quality targets only after a discovery or PoC phase has
  measured feasibility on real data. Before that, commit to effort and learning, not outcomes.
- **Sampling noise in thresholds.** A rate measured on N cases has an error bar, so say whether the
  threshold applies to the point estimate or to a confidence bound, and size N to match. Zero-tolerance
  lines need the same care. Zero failures in N cases shows, at 95% confidence, only that the true rate
  is below about 3/N (the "rule of three"). If a failure type truly must never happen, the clause should
  also require a deterministic control, such as a validator or a hard filter, not just a clean eval run.
- **Post-acceptance performance.** Acceptance certifies behaviour on a frozen set at one date, and
  production inputs drift afterwards. State the warranty period, who monitors which metrics after
  go-live, the threshold that triggers remediation, and whether remediation is in scope or a change.
- **Data handling.** State where customer data goes, retention, use (or no use) for training, and
  deletion at the end. This often references the data processing agreement.

## Example
Here is a worked rewrite of a weak SOW clause for an invoice-extraction pilot.

```text
WEAK
  4.1 Vendor will deliver an AI system that accurately extracts invoice data.
  5.1 Customer will provide sample invoices.

STRONG
  4.1 Deliverable D2: extraction service deployed in Customer's staging tenant, writing to the
      AP staging table defined in Appendix B.
  4.2 Acceptance of D2: on the frozen acceptance set (600 invoices, stratified by the 12
      highest-volume suppliers plus 100 long-tail invoices, labelled per Appendix C), measured
      with the harness in Appendix D:
        - invoice total, currency, supplier ID: >= 98% exact match each
        - line items: >= 90% F1 at line level
        - zero cases of a total written with the wrong currency
      Customer has 10 business days to accept or list failing cases; silence = acceptance.
  5.1 Customer provides 2,000 historical invoices with posted AP records (PDF + CSV export)
      by 3 March; SSO access to staging by 10 March; 4 SME hours/week from the AP team.
  5.2 Each business day of delay in 5.1 moves milestones M2-M4 by one business day.
  6.3 Out of scope: handwritten invoices, non-Latin scripts, posting to the ERP ledger.
```

All thresholds and dates here are illustrative. Notice what the strong version achieves. Acceptance
is a computation anyone can re-run, not an opinion. The sample is stratified so that long-tail
suppliers, where extraction usually breaks, cannot be quietly excluded. The one catastrophic error
type gets its own zero-tolerance line, separate from the averages. The numbers are also honest about
their resolution. On 600 invoices, 98% allows at most 12 misses per field, and a clean run on the
currency line bounds the true wrong-currency rate below about 0.5% (3/600), not at zero. So the team
also builds a deterministic check that the extracted currency is one the vendor master allows for
that supplier, and that check is what makes "zero" credible. And the data dependency has a date
and a consequence, so a late extract moves the schedule instead of eating the team's time. In week 4
the customer asks for posting to the ERP. Clause 6.3 makes that a change request with its own estimate
rather than an argument.

## Trade-offs
- Fixed-price SOWs give the customer budget certainty but make the vendor price research risk. Use them
  only after a PoC has measured feasibility on the customer's own data.
- Time-and-materials terms absorb uncertainty well but give the customer little cost control. They
  suit discovery and PoC phases, and should give way to milestones once the target is known.
- Tight, statistical acceptance criteria prevent disputes but take effort to agree on and to build the
  eval set for. That effort is worth it whenever payment or go-live depends on acceptance.
- A long out-of-scope list protects the delivery team but can read as defensive and slow the
  signature. Focus it on the extensions customers most often assume are included.
- Phase gates let both sides stop early, which caps loss. The cost is negotiating several times, which
  only pays off when uncertainty is high.

## Pitfalls
- Acceptance criteria in prose such as "accurate" or "production-ready". The symptom is an acceptance
  meeting where the customer shows three wrong answers and refuses sign-off.
- Customer dependencies without dates or consequences. The symptom is the team waiting weeks for data
  or access while the delivery date stays fixed.
- Committing to a quality target before measuring feasibility on real data. The symptom is a project
  that is technically sound but contractually failed because the data was worse than the sample.
- No support boundary after go-live. The symptom is the delivery team paged for production incidents
  months after the engagement ended.
- An eval set the customer can change during the project. The symptom is acceptance thresholds being
  met and then the set being "updated" with new hard cases before sign-off.

## When to use
Write an SOW, or its internal equivalent, for any engagement where someone outside the team must
accept the result, and especially when money or a go-live date depends on it. Draft it at the end of
scoping, once the success metrics, baseline plan and dependencies are known. Ideally, phase it so the
first SOW covers only discovery and a PoC. For a two-week internal spike, a one-page brief with
objective, out of scope and an exit question is enough. In day-to-day work the SOW is the reference in
every scope discussion. In interviews and customer conversations, expect "how would you write acceptance
criteria for a model?" The answer is a frozen, representative eval set, per-slice thresholds, a
zero-tolerance list and a named harness.

## Role lens
- **Engineer:** Turns acceptance clauses into a runnable eval harness and frozen dataset, so acceptance
  is a reproducible computation rather than a review meeting.
- **Architect:** Sets the deliverable boundaries, environments, support model and the technical
  assumptions whose failure would trigger change control.
- **FDE:** Drafts and negotiates the SOW with the customer, makes data and access dependencies dated
  obligations, and enforces change control on site.

## Check yourself
**Q:** A customer insists on a fixed-price SOW guaranteeing "95% accuracy" for a document-classification
assistant, before you have seen their documents. How do you respond?
**A:** I would not sign a quality guarantee for unseen data, because nobody knows whether 95% is
feasible, what "accuracy" covers, or what the human baseline is. I would propose a phased structure.
First, a short fixed-fee discovery and PoC phase: we receive a representative sample, build a jointly
labelled eval set, measure the human baseline, and report achievable accuracy per category. Second, a
pilot or production SOW with acceptance criteria set from those measurements: per-class thresholds on
the frozen set, zero-tolerance error types, and dated customer obligations. This gives the customer an
early exit and a defensible target, and it stops either side from pricing a guess.

## Quiz
### Q: Why are acceptance criteria for model behaviour written against a frozen eval set?
- [ ] Frozen datasets run faster in the evaluation harness
- [x] A fixed, agreed set makes acceptance a reproducible computation and stops the target moving during tuning
- [ ] Regulators require every model to be tested on frozen data
- [ ] It lets the team tune the model directly on the acceptance cases
> Probabilistic systems will always fail on some inputs, so acceptance must be a rate on a defined
> sample. Freezing the set prevents both sides from changing the test mid-project. Tuning on it would
> make the result meaningless.

### Q: What is the main purpose of the customer-responsibilities section with dates and consequences?
- [ ] To shift all project risk onto the customer
- [ ] To satisfy the MSA's liability terms
- [ ] To list the customer's internal stakeholders
- [x] To make data, access and SME-time dependencies explicit so their delays move the schedule rather than silently consuming it
> AI delivery depends on customer data, access and expert time. Dated obligations with a stated
> consequence make slips visible and fair, rather than turning them into the delivery team's missed
> milestones.

### Q: When does a fixed-price commitment to a quality target become reasonable for an AI project?
- [ ] When the vendor has hit that target with other customers
- [ ] When the model provider publishes benchmark results above the target
- [x] After a discovery or PoC phase has measured feasibility and the baseline on the customer's own data
- [ ] Never; AI projects should always be time-and-materials
> Quality depends on the customer's data and task definition, so other customers' results or public
> benchmarks do not transfer. Once feasibility is measured on real data, the risk is known well enough
> to price.
