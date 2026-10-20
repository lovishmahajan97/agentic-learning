---
id: ai-risk-management
module: security-governance-compliance
name: AI risk management
kind: practice
difficulty: core
est_minutes: 16
related:
  - security-governance-compliance/ai-inventory-and-use-case-risk-classification
  - security-governance-compliance/threat-modeling
  - security-governance-compliance/responsible-ai
  - security-governance-compliance/ai-incident-response
  - security-governance-compliance/compliance-architecture
  - scoping/dependency-and-risk-management
  - llmops-platform/model-monitoring
  - evals/red-teaming
sources:
  - NIST, 2023 — AI Risk Management Framework (AI RMF 1.0)
  - NIST, 2024 — AI RMF Generative AI Profile (NIST AI 600-1)
  - ISO/IEC 23894:2023 — AI: Guidance on risk management
---

## TL;DR
AI risk management is the continuous loop of identifying what can go wrong with an AI system, estimating
how likely and how harmful each risk is, choosing controls, and tracking what risk remains, with named
owners and an explicit appetite for what is acceptable. It exists because AI risks are probabilistic,
shift after launch, and span security, safety, legal and business harms, so a one-time security review
cannot cover them.

## Why it exists
Traditional software risk reviews assume behaviour is specified and stable: test against the spec, fix
deviations, ship. AI systems break those assumptions. Their behaviour is statistical (an error rate, not a
bug), depends on data that drifts, can change when a provider updates a model you did not touch, and
produces harms that are not outages: a confident wrong answer, a biased outcome, leaked training data, an
agent taking an action nobody intended. Many of these harms fall on people outside the organization who
never see the system.

The naive approach is a launch checklist: security sign-off, privacy sign-off, done. It fails because it
is binary and one-off. It cannot express "this hallucinates on roughly one answer in fifty for long
documents and that is acceptable only with a citation check", and it says nothing about what happens six
months later when the corpus, the users or the model have changed. It also fragments ownership: security
looks at prompt injection, legal at copyright, product at accuracy, and nobody sees the combined picture or
decides which risk to fund first.

Frameworks such as the NIST AI RMF and ISO/IEC 23894 exist to give that picture a shared structure: a
common vocabulary, a lifecycle, and the expectation that risk decisions are recorded and revisited. They
are voluntary guidance, but regulators, auditors and enterprise customers increasingly expect to see them.

## How it works
### The NIST AI RMF functions
The AI RMF organizes the work into four functions:
- **Govern**: policies, roles, risk appetite, accountability and culture. It is cross-cutting *because*
  without an owner and an agreed appetite, the other three produce reports nobody acts on.
- **Map**: establish context for a specific system: purpose, users, affected people, legal setting,
  dependencies, and the risks that context creates. Mapping comes first *because* the same model carries
  different risks in different uses.
- **Measure**: quantify or qualitatively assess the mapped risks with evaluations, red teaming,
  monitoring and expert review, including measuring what you cannot measure well and saying so.
- **Manage**: prioritize, treat (mitigate, transfer, avoid or accept), monitor, and respond to incidents.

NIST's Generative AI Profile extends this with risks specific to generative systems, such as
confabulation, harmful content, information integrity and data privacy.

### The risk register
The working artifact is a register per system (rolled up across the portfolio). Each entry has:
1. **Risk statement** in cause-event-consequence form: "Because retrieved documents may be outdated, the
   assistant may state a superseded policy, leading a customer to act on wrong terms."
2. **Category**: security, privacy, fairness, safety, reliability, legal or IP, third-party, operational.
3. **Inherent rating**: likelihood × impact before controls, on a defined scale *because* "high" means
   nothing without anchors (for example, impact 5 = regulatory breach or harm to many people).
4. **Controls** mapped to the risk, each with an owner and evidence that it works.
5. **Residual rating** after controls, compared with the risk appetite. Above appetite means treat
   further, avoid the use, or record formal acceptance by someone with authority.
6. **Key risk indicators**: live metrics that warn the risk is materializing (groundedness rate, override
   rate, injection detections, complaint volume).
7. **Review trigger and date**.

### Treatment options
Mitigate (guardrails, human review, evaluation gates), transfer (contract terms, insurance, vendor
indemnities), avoid (narrow scope, do not automate the decision), or accept with sign-off. Avoidance is
underused: removing autonomy or a data source often removes a risk more cheaply than any control.

### Integration
Risk management is only real when wired into delivery: high residual risks block releases, KRIs feed
monitoring alerts, incidents update likelihood estimates, and the register is reviewed when the model,
data, purpose or regulation changes. The three-lines model is common in regulated firms: builders own
risks, a risk or compliance function challenges them, internal audit checks the process.

## Example
A customer-support assistant with retrieval and a refund tool. Scores use a 1 to 5 scale; numbers are
illustrative judgements, not measurements.

```python
APPETITE = 8   # residual scores above this need treatment or formal acceptance

register = [
    # risk, inherent (likelihood, impact), controls, residual (likelihood, impact), KRI
    ("Answers state superseded policy from stale docs", (4, 3),
     ["nightly re-index", "citation required", "doc expiry metadata"], (2, 3), "ungrounded-answer rate"),
    ("Indirect prompt injection triggers refunds", (3, 5),
     ["refund tool requires human approval", "tool allow-list", "input scanning"], (1, 4), "approval rejection rate"),
    ("Customer PII sent to provider and retained", (3, 4),
     ["zero-retention contract terms", "PII redaction"], (2, 4), "redaction misses in samples"),
    ("Provider model update changes tone and refusals", (3, 2),
     ["pinned model version", "regression suite on upgrade"], (2, 2), "eval score delta"),
]

for risk, (li, ii), controls, (lr, ir), kri in register:
    inherent, residual = li * ii, lr * ir
    status = "OK" if residual <= APPETITE else "TREAT OR ACCEPT"
    print(f"{inherent:>2} -> {residual:>2}  {status:<15} {risk}  [KRI: {kri}]")
```

The PII risk lands exactly at the appetite line with residual 8, which prompts the useful conversation:
is redaction measured (the KRI) and good enough, or should the team avoid sending free-text history at all?
The injection risk shows the value of avoidance-style controls: requiring human approval for refunds cuts
impact more than any detector could.

## Trade-offs
- Quantified likelihood-impact scoring makes risks comparable and prioritizable, but scores are judgement
  dressed as numbers; anchor scales with examples and revisit scores when incident data arrives.
- A detailed per-system register gives depth, but a portfolio of hundreds becomes unmaintainable;
  standard risk libraries per use-case tier with system-specific deltas scale better.
- Strict risk appetite thresholds give clear release decisions, but can push teams to under-score risks;
  independent challenge from a second line keeps scoring honest.
- Mitigating with more controls reduces residual risk, but each control has cost, latency and failure
  modes of its own; avoiding a risky capability can be cheaper and more robust.

## Pitfalls
- Writing vague risks such as "bias" or "hallucination"; the symptom is a register where no control can
  be shown to address any specific entry.
- Rating only inherent risk and never re-rating after controls; the symptom is a register that never
  changes no matter what the team builds.
- Controls without evidence; the symptom is "guardrails in place" in the register while nobody can show a
  detection rate or a test result.
- No key risk indicators; the symptom is learning about a materialized risk from a customer complaint.
- Treating the register as a launch artifact; the symptom is a review date a year in the past on a system
  whose model was swapped twice.

## When to use
Every AI system in the inventory gets a risk assessment proportionate to its tier: a short standard
template for low-tier uses, a full register with KRIs and second-line review for high-tier uses. Run it
before design is fixed, because the cheapest treatments (scope and autonomy decisions) are only available
early, then re-run on any material change. It underpins ISO/IEC 42001 certification, EU AI Act risk
management obligations for high-risk systems, and enterprise customer due diligence. In interviews, show
the loop: map in context, measure with evidence, manage with explicit residual risk and an owner.

## Role lens
- **Engineer:** Implements the controls the register names and exposes the metrics that serve as key risk
  indicators, so each control has evidence rather than an assertion.
- **Architect:** Owns risk appetite translation into design: which capabilities to avoid, where human
  approval sits, and how residual risk rolls up across the AI portfolio.
- **FDE:** Runs the risk mapping workshop with the customer's security, legal and business owners and
  records which residual risks they formally accept for their deployment.

## Check yourself
**Q:** Your register shows a high residual risk that an agent with email access could exfiltrate data via
prompt injection. Detection-based guardrails only lower it slightly. What options do you have, and how do
you choose?
**A:** Detection is a probabilistic control against an adaptive attacker, so it will never drive likelihood
near zero. I would look at impact and avoidance: restrict the email tool to internal recipients or
templates, require human approval for external sends, separate the agent that reads untrusted content
from the one that can act, and strip attachments. Each reduces impact or removes the path entirely. Then
re-score residual risk, compare with appetite, define KRIs such as blocked external sends, and if it is
still above appetite either drop the capability or obtain formal acceptance from an accountable owner.

## Quiz
### Q: In the NIST AI RMF, why does the Map function come before Measure?
- [ ] Measurement tools are only available after mapping is approved
- [x] Risks depend on the context of use, so you must establish purpose, users and setting before deciding what to measure
- [ ] Mapping is required by law and measurement is optional
- [ ] Map produces the model card that Measure then publishes
> The same model has different risks in different deployments. Mapping establishes that context so that
> measurement targets the risks that actually matter for this system rather than generic ones.

### Q: What distinguishes residual risk from inherent risk in a risk register?
- [ ] Residual risk only covers security threats
- [ ] Inherent risk is measured after deployment and residual before
- [x] Residual risk is the rating that remains after controls are applied, compared against risk appetite
- [ ] They are the same rating expressed on different scales
> Inherent risk is assessed before controls; residual risk reflects what remains after them. Release and
> acceptance decisions are made on residual risk relative to the organization's stated appetite.

### Q: Which treatment usually reduces prompt-injection risk for a tool-using agent most reliably?
- [ ] Adding a stronger system prompt telling the model to ignore injected instructions
- [ ] Logging all injected prompts for later review
- [ ] Transferring the risk by adding a disclaimer to the user interface
- [x] Limiting tool capability and requiring human approval for consequential actions
> Instruction-based and detection controls are bypassable by adaptive attackers. Reducing what the agent can
> do, and gating consequential actions, cuts impact regardless of whether the injection succeeds.
