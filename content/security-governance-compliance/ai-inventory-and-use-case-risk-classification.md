---
id: ai-inventory-and-use-case-risk-classification
module: security-governance-compliance
name: AI inventory & use-case risk classification
kind: practice
difficulty: core
est_minutes: 16
status: reviewed
related:
  - security-governance-compliance/model-governance
  - security-governance-compliance/ai-risk-management
  - security-governance-compliance/compliance-architecture
  - security-governance-compliance/model-cards
  - security-governance-compliance/data-classification
  - scoping/ai-use-case-discovery
  - llmops-platform/model-registry
sources:
  - Regulation (EU) 2024/1689 — Artificial Intelligence Act
  - NIST, 2023 — AI Risk Management Framework (AI RMF 1.0)
  - ISO/IEC 42001:2023 — AI management system
---

## TL;DR
You cannot govern AI you do not know exists. An AI inventory is the system of record listing every AI
system and use case in the organization (built, bought or embedded in a SaaS tool) with its owner, data,
models and purpose. Risk classification then assigns each *use case* a tier that decides how much control
it gets, because the same model is harmless drafting marketing copy and high-stakes screening job
applicants.

## Why it exists
Every governance activity, from model cards and evaluations to audit trails, incident response and
regulatory filings, presumes a list of things to apply it to. In most organizations that list does not
exist. AI arrives through many doors: a product team calls a model API, a vendor switches on an "AI
assistant" feature in the CRM, an analyst pastes customer data into a chatbot, a data science team ships a
churn model years ago. When a regulator, auditor or board asks "where do we use AI, and which uses are
high-risk?", the honest answer is often "we don't know".

The naive alternative is governing everything identically: one heavyweight review for every AI use. It
fails in both directions. Applied strictly, it buries a meeting-summary tool under the same paperwork as a
credit model, so teams route around it and shadow AI grows. Applied loosely, the credit model gets the
light-touch review the summary tool deserved.

Classifying by *model* also fails, because risk is a property of the use: what decisions the output
informs, about whom, with what reversibility and in what legal context. The EU AI Act encodes exactly
this; its obligations attach to the intended purpose of a system, not to the underlying technology. So
you need both halves: a complete inventory, and a classification of each use case that proportions
controls to stakes.

## How it works
### 1. Define what counts
Write a short, inclusive definition: any system where a learned model produces outputs that influence
decisions, content or actions. Include third-party SaaS features and employee use of general chat tools,
*because* those are where data leaves and where nobody thinks they are "doing AI".

### 2. Discover
Combine declaration with detection, *because* self-reporting alone misses shadow use:
- Intake: a lightweight registration form required before procurement, launch or production access.
- Technical signals: outbound traffic to model API domains through egress proxies, API keys found by
  secret scanning, cloud bills with AI service line items, SSO logs for AI SaaS apps, expense reports.
- Vendor review: ask existing SaaS vendors which features use AI and on which data.

### 3. Record a minimum useful entry
Owner (a named person, not a team alias), business purpose, users and affected people, decision type
(advisory or automated), models and providers with versions, data categories processed (linking to data
classification), deployment surface, geographies, *your role* (built it, bought it, or substantially
modified something bought, which under the EU AI Act decides whether you are provider or deployer), the
lifecycle status, and links to the model or system card. Keep it
small enough that people fill it in; fields nobody reads are fields nobody maintains.

### 4. Classify the use case
Use a short questionnaire whose answers map to tiers, so classification is repeatable rather than a
judgement call each time. The deciding factors:
- **Legal category**: does the use fall in a regulated list? Under the EU AI Act: prohibited practices
  (for example social scoring, which the final text bans for public and private actors alike, or
  manipulative techniques causing significant harm);
  high-risk areas listed in Annex III such as employment, education, access to essential services
  including credit scoring, biometric identification, law enforcement, migration and justice, plus AI that
  is a safety component of regulated products; transparency obligations for systems like chatbots and
  generated media; and minimal risk for the rest. General-purpose AI models carry their own separate
  provider obligations.
- **Impact**: who is affected, how severely and how reversibly if the output is wrong.
- **Autonomy**: human decides with AI advice, human approves AI decision, or fully automated action.
- **Data sensitivity**: health, financial, children's, biometric or other special-category data.
- **Exposure**: internal staff only, customers, or the public.

The order and the combining rule matter. Ask the knock-out questions first: a prohibited practice stops
the process, and a use in a regulated high-risk area sets a floor that no other answer can lower. Then
take the tier as the *maximum* across the remaining factors, not an average, *because* averaging lets
"internal only" cancel out "decides who gets disciplined", and harm does not average. Some factors, data sensitivity in particular, can instead be wired to specific
controls that apply at any tier (vendor terms, redaction, access logging); which factors drive the tier
and which only trigger controls is a policy choice to write down once. The EU AI Act has
one refinement worth encoding: an Annex III use is not high-risk if it only performs a narrow procedural
task, improves the result of a completed human activity, detects patterns without replacing human
assessment, or does preparatory work, but that exemption never applies when the system profiles people,
and the reasoning must be documented. Record the answers, not just the tier, so a later change in one
answer re-runs the classification instead of requiring someone to remember why.

### 5. Attach controls to tiers
Each tier maps to required controls: low tier needs inventory entry and acceptable-use rules; medium adds
an evaluation, a system card and monitoring; high adds impact assessment, human oversight design, audit
trails, bias testing, sign-off by a review board and periodic re-review. This mapping is the payoff,
*because* teams know up front what a tier costs them.

### 6. Keep it alive
Trigger re-classification on change of purpose, data, model provider, geography or autonomy level, and
reconcile the inventory against discovery signals on a schedule.

## Example
A mid-size insurer runs discovery and intake and gets 46 entries. Three of them show why classification
must be by use case:

| Use case | Same model? | Decision | Affected | Tier |
|---|---|---|---|---|
| Drafting marketing emails | Vendor LLM A | Human edits and sends | Public (indirect) | Low |
| Summarizing claim files for adjusters | Vendor LLM A | Advisory, adjuster decides | Claimants | Medium |
| Ranking job applicants for interview | Vendor LLM A via an HR SaaS | Shortlist shown to recruiter | Applicants | High |

All three run on the same model. The first needs an inventory entry and a rule against pasting customer
data. The second processes personal and possibly health data, informs a consequential decision and needs
evaluation on real claim files, a groundedness check, a system card and an audit trail of which summary
an adjuster saw. It is also the contested row: health data alone would make it high under a pure
maximum rule, and this insurer chose to let sensitive data trigger data controls rather than the tier
when a human makes the decision. Another organization could reasonably tier it high; what matters is
that the rule is explicit and applied the same way to every entry. The third falls in an area the EU AI Act lists as high-risk (employment) and affects
people who never consented to the tool: it needs an impact assessment, bias testing across applicant
groups, documented human oversight, vendor documentation, and review board approval.

Discovery also found 9 entries nobody had declared, including two teams calling model APIs from
personal accounts, which became the first remediation items. The output is a register that tells the
governance team where their review effort should go: the handful of high-tier uses, not all 46 equally.

## Trade-offs
- A broad definition of "AI" catches shadow and embedded use but inflates the inventory; tiering keeps
  that cheap because most entries land in the low tier with minimal obligations.
- Questionnaire-driven classification is consistent and fast, but edge cases need human judgement; add an
  escalation path rather than more questions when ambiguous answers become common.
- Mandatory registration before procurement gives early visibility but adds friction; it flips to driving
  shadow AI when intake is slow, so the low-tier path must take minutes, not weeks.
- Technical discovery finds what people forget, but egress monitoring of employee traffic raises privacy
  and works-council concerns in some jurisdictions; agree scope with legal and HR first.

## Pitfalls
- Inventorying models instead of use cases; the symptom is one "Vendor LLM" row covering twenty uses with
  wildly different risk.
- Relying only on self-declaration; the symptom is an auditor or journalist discovering a use the
  inventory does not contain.
- Treating the inventory as a one-off spreadsheet; the symptom is entries whose owner left months ago and
  whose model provider has changed.
- Classifying once at launch; the symptom is an "advisory" tool that quietly became automated decision-making
  without anyone re-reviewing it.
- Ignoring AI features inside bought SaaS; the symptom is customer data flowing to a sub-processor nobody
  assessed.

## When to use
Start the inventory as soon as an organization has more than a handful of AI uses or faces any AI
regulation, audit or customer due-diligence questionnaire, which today is nearly everyone. It is a
prerequisite for an ISO/IEC 42001 management system, for the NIST AI RMF (inventorying AI systems is an
explicit Govern outcome, GOVERN 1.6, and every Map activity presumes the list), and for EU AI Act role and
obligation analysis. Measure coverage (discovered uses reconciled against declared), freshness
(entries reviewed within their cycle) and time-to-classify. In interviews, the key insight is that risk
attaches to use, autonomy and affected people, not to the model, and that tiering exists to make
governance proportionate.

## Role lens
- **Engineer:** Registers each new AI feature with model, version, data categories and owner, and adds the
  egress or secret-scanning signals that feed automated discovery.
- **Architect:** Designs the inventory schema, the tiering questionnaire and the tier-to-control mapping,
  and links the inventory to the model registry and data classification.
- **FDE:** Helps a customer classify the use case being deployed in their environment, often discovering
  it is higher-tier than assumed because of the data or decisions involved.

## Check yourself
**Q:** A team argues their use case is low risk because it uses the same approved vendor model as the
company chatbot. They will use it to flag employees for performance review. How do you respond?
**A:** Model approval says the vendor and model meet baseline security and contractual requirements; it
says nothing about this use. Risk depends on purpose, affected people, autonomy and legal context.
Flagging employees for performance review is an employment decision about people with limited ability to
contest it, which the EU AI Act lists as a high-risk area and many labour regimes regulate. It needs its
own inventory entry and high-tier classification: an impact assessment, bias evaluation across groups,
documented human oversight, transparency to employees, an audit trail, and review board sign-off before
launch. The approved model is reusable; the approval is not.

## Quiz
### Q: Why should risk classification be done per use case rather than per model?
- [ ] Models are too numerous to classify individually
- [x] The same model can be low-risk in one use and high-risk in another, depending on decisions, affected people and autonomy
- [ ] Vendors classify their own models, so organizations only need to classify use cases
- [ ] Regulations prohibit classifying models directly
> Harm comes from what the output is used for and whom it affects. Regulation such as the EU AI Act attaches
> obligations to intended purpose, so a model-level label cannot capture the difference between drafting
> emails and screening job applicants.

### Q: Which discovery approach is most likely to find shadow AI usage that teams did not declare?
- [ ] Sending an annual survey asking teams to list their AI projects
- [ ] Reviewing the approved vendor list in procurement
- [x] Combining intake registration with technical signals such as egress traffic to model APIs, secret scanning and AI line items on bills
- [ ] Asking the model provider for a list of the company's users
> Self-reporting and procurement lists only contain what people chose to declare or buy formally. Technical
> signals catch personal-account API use and embedded SaaS features that never went through intake.

### Q: What is the main purpose of mapping risk tiers to required controls?
- [ ] To guarantee that every use case receives a full impact assessment
- [x] To make governance proportionate and predictable so teams know what each tier requires
- [ ] To replace the need for an AI review board entirely
- [ ] To let low-tier systems skip the inventory
> Tiers concentrate scarce review effort on high-stakes uses while giving low-risk uses a fast path. Every
> use still enters the inventory; what varies is the depth of control applied to it.
