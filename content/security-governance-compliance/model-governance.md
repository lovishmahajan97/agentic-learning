---
id: model-governance
module: security-governance-compliance
name: Model governance
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - security-governance-compliance/ai-inventory-and-use-case-risk-classification
  - security-governance-compliance/model-cards
  - security-governance-compliance/audit-trails
  - security-governance-compliance/ai-risk-management
  - llmops-platform/model-registry
  - llmops-platform/evaluation-gates-for-production-releases
  - llmops-platform/model-lifecycle-and-deprecation-policy
  - data-architecture/model-and-dataset-provenance
sources:
  - Board of Governors of the Federal Reserve System, 2011 — SR 11-7, Guidance on Model Risk Management
  - NIST, 2023 — AI Risk Management Framework (AI RMF 1.0)
  - ISO/IEC 42001:2023 — Artificial intelligence management system
  - Regulation (EU) 2024/1689 — Artificial Intelligence Act
---

## TL;DR
Model governance is the set of decisions, records and enforced checks that control which models may be
used, for what, by whom, on what evidence, and how changes are approved, monitored and retired. It exists
because models change behaviour without code changes and fail statistically, so ordinary change
management misses them. Good governance is mostly automated: a registry, risk-tiered approval with
evidence, and a gateway that refuses unregistered or unapproved models.

## Why it exists
Banks learned this first. A pricing or credit model that is wrong does not crash; it quietly makes bad
decisions at scale. US supervisory guidance on model risk management (SR 11-7, 2011) formalised the
response: inventory every model, validate it independently before use, monitor it, and assign clear
ownership. Generative AI widens the problem in three ways.

First, models arrive from outside. A hosted provider can update the model behind an alias, deprecate a
version on its own schedule, or change its data-handling terms, so "we didn't deploy anything" no longer
means nothing changed. Second, use spreads faster than oversight: any team with an API key can put a
model into a customer-facing flow, so organisations discover dozens of untracked uses at audit time.
Third, regulation now expects it. The EU AI Act imposes obligations on high-risk systems, such as risk
management, documentation, logging and human oversight, and on general-purpose model providers;
frameworks such as NIST AI RMF and ISO/IEC 42001 give the management-system shape.

The naive approach is a review committee that approves models by meeting. It fails twice: it is too slow
for teams shipping weekly, so they bypass it, and its approvals are not enforced anywhere, so an approved
model and the model actually serving traffic drift apart. Governance has to live in the delivery path.

## How it works
### 1. Inventory and registry
Every model in use is a registry entry: model and version (or provider plus pinned snapshot), owner, use
cases, data classes it may receive, deployment endpoints, approval status and expiry, linked evidence.
Use cases are inventoried and risk-classified separately, *because* the same model can be low-risk for
drafting internal notes and high-risk for screening job applicants; the AI inventory lesson covers this.

### 2. Risk-tiered approval
Approval depth follows the use case's risk tier, not the model's fame. A low tier may need an automated
eval pass and owner sign-off; a high tier needs independent validation, red-teaming, a bias or fairness
assessment where people are affected, a documented human-oversight design, and sign-off from risk or
compliance. Tiering keeps the process proportionate, *because* a uniform heavy process is what drives
teams to shadow AI.

### 3. Evidence, not assertions
Approval attaches artifacts: eval results on a versioned dataset, red-team findings and their
dispositions, the model card, data provenance for any fine-tuning, and the provider's terms for hosted
models. Evidence is tied to the exact model version, so a new version needs new evidence.

### 4. Enforcement in the path
The AI gateway or deployment pipeline checks the registry on every request or release: model registered,
approved for this use case and data class, approval unexpired. Unregistered models are refused. This is
what turns the registry from documentation into control.

### 5. Change management
Pin provider snapshots instead of floating aliases where the provider allows it, *because* an alias can
change behaviour beneath you. Treat model swaps, prompt changes and fine-tune refreshes as changes that
rerun the tier's evals before promotion, with canary or shadow rollout for higher tiers.

### 6. Monitoring and retirement
Production monitoring watches quality signals, drift, incidents and cost per use case. Approvals carry
an expiry or review date. Provider deprecation notices feed the registry so migrations are planned rather
than forced. Retirement removes the endpoint, archives evidence and records why.

### Roles
A model or use-case owner is accountable; an independent validator reviews evidence for higher tiers; a
risk or compliance function owns the policy; platform engineering owns enforcement. Separation between
builder and validator is the point SR 11-7 insists on, because builders grade their own work generously.
Its term for what the validator must provide is "effective challenge": critical review by people with
the competence, independence and authority to force changes. A validator who reports to the product
owner, or who can comment but not block, is review in name only.

### What counts as a change
The practical question every week is which edits re-open approval. A useful rule: anything that can
change the distribution of outputs for the approved use case is a change, and its depth of re-review
follows the tier. That covers a new model snapshot, a prompt or system-instruction edit, a change to
retrieval sources or chunking, new tools, a fine-tune refresh, and a provider's change to data-handling
terms (which affects which data classes the model may receive, not its quality). Purely operational
edits, such as scaling or a timeout, are not. Writing this list down per tier is what stops both
failure modes: re-validating on every typo, and shipping a new retrieval corpus to a high-tier use case
without anyone noticing it was a material change.

## Example
A gateway check against the registry before routing a request. The scenario: a team's code asks for
`provider-x/large-latest`, an alias whose underlying snapshot the provider just changed.

```python
from datetime import date

REGISTRY = {
    "provider-x/large-2025-06-01": {
        "approved_for": {"support-drafting": "medium"},  # use case -> max risk tier
        "max_data_class": "CONFIDENTIAL",
        "approval_expires": date(2026, 12, 31),
        "evidence": ["evals/support-v14", "redteam/2025-07", "card/large-2025-06"],
    },
}
TIER_RANK = {"low": 0, "medium": 1, "high": 2}
CLASS_RANK = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3}

def authorise(model: str, use_case: str, tier: str, data_class: str, today: date) -> str:
    entry = REGISTRY.get(model)
    if entry is None:
        return f"DENY: {model} not registered (aliases must resolve to a pinned snapshot)"
    if use_case not in entry["approved_for"]:
        return f"DENY: not approved for {use_case}"
    if TIER_RANK[tier] > TIER_RANK[entry["approved_for"][use_case]]:
        return f"DENY: approved up to {entry['approved_for'][use_case]} risk, request is {tier}"
    if CLASS_RANK[data_class] > CLASS_RANK[entry["max_data_class"]]:
        return "DENY: data class exceeds approval"
    if today > entry["approval_expires"]:
        return "DENY: approval expired, re-validation due"
    return "ALLOW"

print(authorise("provider-x/large-latest", "support-drafting", "medium", "INTERNAL", date(2026, 10, 20)))
print(authorise("provider-x/large-2025-06-01", "support-drafting", "medium", "INTERNAL", date(2026, 10, 20)))
print(authorise("provider-x/large-2025-06-01", "claims-decisions", "high", "RESTRICTED", date(2026, 10, 20)))
```

The alias is refused, the pinned snapshot is allowed for its approved use, and reusing it for a
high-risk claims decision is denied until that use case has its own evidence. Model names are invented.

## Trade-offs
- Gateway enforcement makes governance real but blocks experimentation; a sandbox tier with synthetic or
  public data only keeps exploration fast without touching production paths.
- Pinning snapshots gives reproducibility, yet pinned versions get deprecated and miss improvements, so
  pinning pays off only alongside a planned re-validation cadence.
- Independent validation catches what builders miss but adds days to each high-tier release; it should
  flip to automated evidence review for low tiers where errors are cheap and reversible.
- Expiring approvals force periodic review and catch drift, but generate busywork if evidence must be
  rebuilt by hand; automated eval reruns make short expiry affordable.
- A central registry gives one view for auditors, but federated teams resist it unless registration is a
  side effect of using the platform rather than a separate form.

## Pitfalls
- Approving a model in a meeting with no enforcement; the symptom is an audit finding production traffic
  on models nobody on the committee has heard of.
- Calling floating aliases in production; quality or format shifts appear overnight with no deploy, and
  incident timelines show only a provider-side change.
- Tying approval to the model instead of the use case, so a model approved for summarisation is quietly
  reused for decisions about people without new evidence.
- Evidence not linked to versions; the eval report in the file is for last year's snapshot and nobody can
  show what the current one was tested on.
- Treating prompts and retrieval configuration as outside governance, although a prompt change can alter
  behaviour as much as a model swap.

## When to use
Start lightweight governance as soon as more than one team uses models or any model touches customers:
a registry, an owner per entry and gateway enforcement. Add independent validation and formal sign-off
when use cases affect people's rights, money, health or safety, or fall into regulated categories. Avoid
heavyweight process for internal low-risk tools, where it only creates shadow AI. Measure the share of
traffic through registered models, approvals past expiry, time from request to approval per tier, and
unregistered-model call attempts. In interviews, say how governance is enforced, not only documented.

## Role lens
- **Engineer:** Registers models with pinned versions, wires eval runs to produce linked evidence, and
  implements the gateway check that refuses unapproved models.
- **Architect:** Designs the risk tiers, registry schema and enforcement points, and decides which changes
  trigger re-validation and which rollout strategy each tier needs.
- **FDE:** Fits the product into the customer's model risk process, supplies model cards and eval
  evidence, and maps their approval steps to the deployment plan.

## Check yourself
**Q:** A bank's governance committee approved a hosted model for internal document summarisation. Six
months later a team uses the same model to draft credit decline letters and argues no new approval is
needed. What do you say?
**A:** Approval attaches to a use case and risk tier, not to the model. Decline letters affect customers
and fall into regulated territory, so the tier is higher: it needs evidence on representative letters,
checks for errors and unfair language, a human-review design, and an independent validator's sign-off.
Also confirm the version: if the team calls a floating alias, the snapshot may differ from the one
approved. The gateway should enforce this by checking the use case against the registry, so the request
is refused until the new approval exists.

## Quiz
### Q: Why should model approvals be tied to use cases rather than to models alone?
- [ ] Use cases are easier to name in a registry than model versions
- [x] The same model can be low-risk in one context and high-risk in another, requiring different evidence
- [ ] Regulators only recognise use-case approvals, never model approvals
- [ ] Model vendors forbid customers from approving their models directly
> Risk depends on what the output affects. A model fine for internal drafting can harm people when used
> for decisions about them, so evidence and oversight must match the use case's tier.

### Q: What is the main governance risk of calling a provider's floating model alias in production?
- [ ] Aliases cost more per token than pinned snapshots
- [ ] Aliases cannot be used with an AI gateway
- [x] Behaviour can change under you with no deploy, invalidating the evidence your approval relied on
- [ ] Aliases always point to an older, less capable model
> An alias may be repointed to a new snapshot at any time, so the evaluated model and the serving model
> diverge silently. Pinning ties approval evidence to the exact version actually in use.

### Q: Which mechanism most directly turns a model registry from documentation into control?
- [ ] A quarterly governance committee review of the registry contents
- [ ] Requiring every registry entry to include a model card
- [ ] Publishing the registry on the internal wiki
- [x] A gateway or pipeline check that refuses models not registered and approved for the use case
> Without enforcement in the request or release path, actual usage drifts from approved usage. Reviews
> and model cards are evidence and oversight; the refusal is the control.
