---
id: responsible-ai
module: security-governance-compliance
name: Responsible AI
kind: concept
difficulty: intro
est_minutes: 15
related:
  - security-governance-compliance/ai-risk-management
  - security-governance-compliance/model-cards
  - security-governance-compliance/model-governance
  - security-governance-compliance/ai-inventory-and-use-case-risk-classification
  - evals/judge-calibration-and-bias
  - agents/human-in-the-loop
  - safety-security/content-moderation
sources:
  - NIST, 2023 — AI Risk Management Framework (AI RMF 1.0)
  - Kleinberg, Mullainathan & Raghavan, 2016 — Inherent Trade-Offs in the Fair Determination of Risk Scores
  - Chouldechova, 2017 — Fair Prediction with Disparate Impact
  - Mitchell et al., 2019 — Model Cards for Model Reporting
---

## TL;DR
Responsible AI is the discipline of turning values such as fairness, transparency, accountability,
privacy, safety and reliability into concrete requirements, measurements and owners for each AI system.
It exists because principles alone change nothing: a model can be accurate on average and still harm a
group, mislead users or act without anyone accountable. The work is choosing which properties matter for
a use case, measuring them, and deciding the trade-offs explicitly.

## Why it exists
AI systems fail in ways ordinary software rarely does. They learn patterns from historical data, including
historical discrimination; they produce fluent, confident output that may be false; they act at a scale
where a small per-decision error rate becomes thousands of affected people; and their reasoning is hard to
inspect. These are not edge cases. Hiring screens, credit scoring and content moderation systems have all
been publicly criticized for disparate outcomes, and generative systems for fabricated facts.

Most organizations' first response is a principles page: "we are committed to fair, transparent and
accountable AI." It fails for a predictable reason. A principle cannot be tested, so it cannot block a
release. Engineers facing a deadline have no way to know whether "fair" means equal approval rates, equal
error rates, or equal treatment of identical applicants, and those definitions conflict mathematically.
Nobody owns the decision, so nobody makes it.

The other naive response is treating responsible AI as purely technical: run a bias toolkit, ship. That
misses that most harms come from use-case choices (should this be automated at all? can people contest
it? do they know AI is involved?), not from a metric. Responsible AI exists to connect both layers:
socio-technical decisions about purpose and oversight, and technical measurements that make those
decisions testable.

## How it works
### From principle to requirement
Each principle becomes a requirement with a metric, a threshold and an owner, scoped to a use case:

| Principle | Example requirement for a loan pre-screen |
|---|---|
| Fairness | Approval-rate ratio across protected groups at or above an agreed threshold; equal opportunity gap below a limit |
| Transparency | Applicants told AI is used; adverse decisions come with principal reasons |
| Accountability | A named owner; human review of all declines; audit trail of every decision |
| Privacy | Only documented features used; no protected attributes as inputs; retention limits |
| Reliability and safety | Performance monitored by segment; fallback to manual processing on drift |

### Fairness has several incompatible definitions
- **Demographic parity**: selection rates equal across groups. Easy to measure without labels, but ignores
  differences in qualification.
- **Equal opportunity**: true positive rates equal, so qualified people in each group are selected at the
  same rate. Needs ground-truth labels, which are themselves often biased.
- **Equalized odds**: true positive *and* false positive rates equal.
- **Calibration**: a score of 0.7 means the same probability of the outcome in every group.

Kleinberg et al. and Chouldechova showed that when base rates differ between groups, a non-perfect
classifier cannot satisfy calibration and equal error rates at once. This is *why* choosing a fairness
definition is a policy decision with an owner, not an engineering default: you are choosing which kind of
unfairness you will tolerate. Legal context constrains the choice; US employment practice, for example,
uses the "four-fifths" rule of thumb for adverse impact on selection rates.

### For generative systems
The same principles apply, with different measurements: groundedness and hallucination rates; refusal
behaviour and harmful-content rates; consistency of answers across names, dialects or demographic cues in
otherwise identical prompts (counterfactual testing); disclosure that users are talking to AI; and
provenance labelling of generated media.

### The lifecycle
1. **Design**: decide whether to automate at all, who is affected, how decisions can be contested.
2. **Build**: data review, documented features, evaluation by group.
3. **Release**: thresholds as release gates; model or system card records results.
4. **Operate**: monitor metrics by segment, collect complaints, re-evaluate on change.
5. **Govern**: review board for high-tier uses, escalation path, periodic audit.

## Example
A recruiter tool shortlists applicants. On a labelled evaluation set, the team computes the two fairness
views the policy names.

```python
groups = {
    #        applicants, shortlisted, qualified, qualified_and_shortlisted
    "A": dict(n=400, sel=120, qual=150, tp=110),
    "B": dict(n=300, sel=60,  qual=100, tp=55),
}

rates = {g: d["sel"] / d["n"] for g, d in groups.items()}
tpr = {g: d["tp"] / d["qual"] for g, d in groups.items()}

impact_ratio = min(rates.values()) / max(rates.values())
tpr_gap = max(tpr.values()) - min(tpr.values())

print({g: round(r, 3) for g, r in rates.items()})   # A 0.30, B 0.20
print(round(impact_ratio, 3))                        # 0.667, below a 0.8 rule-of-thumb
print({g: round(t, 3) for g, t in tpr.items()})      # A 0.733, B 0.55
print(round(tpr_gap, 3))                             # 0.183: qualified B applicants missed more often
```

Both views flag a problem, and the equal-opportunity view says something sharper: qualified applicants in
group B are shortlisted far less often, so the gap is not explained by qualification. The team traces it
to a feature correlated with group membership (gaps in employment history), removes it, re-evaluates, and
records before-and-after results and the chosen definitions in the system card. The policy decision about
which threshold applies was made by the HR and legal owners, not by the engineer running the script.

## Trade-offs
- Enforcing demographic parity is simple to monitor without labels, but can select less-qualified
  candidates when base rates truly differ; equal opportunity fits better when labels are trustworthy.
- Removing protected attributes from inputs avoids direct discrimination, but blinds you to measuring
  disparity and does not remove proxies; keep attributes for evaluation under access controls instead.
- Human review of AI decisions adds accountability, but reviewers who approve nearly everything provide
  automation bias, not oversight; it pays only when reviewers have time, context and authority to disagree.
- Explanations increase transparency and contestability, but post-hoc explanations of complex models can
  be unfaithful; for high-stakes decisions an inherently interpretable model may be worth the accuracy cost.

## Pitfalls
- Publishing principles with no metrics, thresholds or owners; the symptom is that no release has ever
  been blocked on responsible AI grounds.
- Measuring fairness only in aggregate or on a convenience sample; the symptom is a disparity first
  reported by affected users or the press.
- Picking a fairness metric because the toolkit defaults to it; the symptom is a dispute late in the
  project about which definition the business actually meant.
- Treating "a human is in the loop" as sufficient; the symptom is review logs showing near-100% approval
  in seconds per case.
- Testing generative systems only with neutral prompts; the symptom is different answer quality when names
  or dialect change in otherwise identical questions.

## When to use
Apply it proportionately: every AI system gets basic transparency and an owner, while systems affecting
people's access to jobs, credit, housing, healthcare, education or legal outcomes need full fairness
evaluation, oversight design and review board sign-off. Start at design time, because the most effective
decision is often scoping (advisory instead of automated, or not building it). Measure what the
requirements name: group-level error rates, complaint and override rates, groundedness. In interviews,
show you know fairness definitions conflict and that choosing one is an accountable policy decision.

## Role lens
- **Engineer:** Builds group-sliced and counterfactual evaluations into the eval harness and wires the
  agreed thresholds in as release gates rather than one-off notebook checks.
- **Architect:** Places oversight, contestability, disclosure and monitoring into the system design and
  ensures each principle maps to a measurable requirement with an accountable owner.
- **FDE:** Translates a customer's policies and legal context into concrete fairness and transparency
  requirements and tests them on the customer's own population and data.

## Check yourself
**Q:** A product manager asks you to "make the credit model fair" before launch. What do you do first,
and why can you not simply pick a metric and optimize it?
**A:** First I would get the decision owners (risk, legal, compliance) to define what fair means for this
use, because fairness definitions conflict. With different default rates across groups, a realistic model
cannot be both calibrated and have equal error rates, so equal approval rates, equal opportunity and
calibration each imply different trade-offs and legal exposure. Then I would turn the chosen definition
into metrics and thresholds, evaluate on representative labelled data by group, check proxies, document
results in the model card, and add monitoring by segment. Also: confirm contestability and adverse-action
explanations, since fairness is not only a metric.

## Quiz
### Q: Why can't a team simply require a classifier to satisfy every common fairness definition at once?
- [ ] Fairness metrics are too expensive to compute together
- [x] When base rates differ across groups, calibration and equal error rates cannot both hold for an imperfect classifier
- [ ] Regulators only allow one fairness metric per model
- [ ] Demographic parity and equal opportunity are the same metric
> The impossibility results of Kleinberg et al. and Chouldechova show these criteria conflict under unequal
> base rates. Choosing among them is therefore a policy decision with an owner, not a technical oversight.

### Q: A team removes gender and ethnicity from model inputs and declares the model fair. What is the main flaw?
- [ ] Removing attributes always reduces accuracy too much
- [x] Proxies can still encode group membership, and without the attributes you cannot measure disparities
- [ ] Protected attributes are legally required inputs
- [ ] The model will refuse to train without them
> Features like postcode or employment gaps can correlate with protected groups, so outcomes may still
> differ. Keeping attributes for evaluation under access control lets you detect and fix that.

### Q: Which change most strengthens accountability in an AI-assisted decision process?
- [ ] Adding a disclaimer that the AI may make mistakes
- [ ] Increasing the model's accuracy on the aggregate test set
- [ ] Logging only the model's final score
- [x] A named owner, reviewers with authority and context to override, and an audit trail of decisions and overrides
> Accountability requires someone responsible, a real ability to disagree with the model, and evidence of
> what happened. Disclaimers and accuracy gains do not establish who answers for a decision.
