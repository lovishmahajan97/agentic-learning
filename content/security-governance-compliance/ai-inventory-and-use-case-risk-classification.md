---
id: ai-inventory-and-use-case-risk-classification
module: security-governance-compliance
name: AI inventory & use-case risk classification
kind: practice
difficulty: core
est_minutes: 16
status: crisp
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
An AI inventory lists every AI system and use case (built, bought or embedded in SaaS) with owner, data,
models and purpose. Risk classification gives each *use case* a tier that sets its controls, because the
same model can draft copy or screen applicants.

## Why it exists
- **You cannot govern AI you do not know exists.** Model cards, evaluations, audit trails, incident
  response and regulatory filings all presume a list of things to apply them to. In most organizations
  that list does not exist.
- **AI arrives through many doors.** A product team calls a model API, a vendor switches on an "AI
  assistant" in the CRM, an analyst pastes customer data into a chatbot, a data science team shipped a
  churn model years ago. Asked "where do we use AI, and which uses are high-risk?", the honest answer is
  often "we don't know".
- **Governing everything identically fails both ways.** Applied strictly, one heavyweight review buries a
  meeting-summary tool under credit-model paperwork, so teams route around it and shadow AI grows.
  Applied loosely, the credit model gets the light-touch review the summary tool deserved.
- **Classifying by model fails too,** because risk is a property of the use: which decisions the output
  informs, about whom, how reversibly, in what legal context. The EU AI Act attaches obligations to a
  system's intended purpose, not its technology.
- **So you need both halves:** a complete inventory, and a per-use-case classification that proportions
  controls to stakes.

## How it works
### 1. Define what counts
- **Write a short, inclusive definition:** any system where a learned model produces outputs that
  influence decisions, content or actions.
- **Include third-party SaaS features and employee use of general chat tools,** *because* those are where
  data leaves and where nobody thinks they are "doing AI".

### 2. Discover
Combine declaration with detection, *because* self-reporting alone misses shadow use:
- **Intake:** a lightweight registration form required before procurement, launch or production access.
- **Technical signals:** outbound traffic to model API domains through egress proxies, API keys found by
  secret scanning, cloud bills with AI service line items, SSO logs for AI SaaS apps, expense reports.
- **Vendor review:** ask existing SaaS vendors which features use AI and on which data.

### 3. Record a minimum useful entry
| Field | Note |
|---|---|
| Owner | A named person, not a team alias |
| Business purpose, users, affected people | Who benefits and who bears the risk |
| Decision type | Advisory or automated |
| Models and providers with versions | Links to the model registry |
| Data categories processed | Links to data classification |
| Deployment surface, geographies | Decides which laws apply |
| *Your role* | Built it, bought it, or substantially modified something bought; under the EU AI Act this decides whether you are provider or deployer |
| Lifecycle status, model or system card link | Keeps governance artifacts attached |

Keep it small enough that people fill it in; fields nobody reads are fields nobody maintains.

### 4. Classify the use case
Use a short questionnaire whose answers map to tiers, so classification is repeatable rather than a
judgement call each time. The deciding factors:
- **Legal category.** Does the use fall in a regulated list? Under the EU AI Act:
  - *Prohibited practices,* for example social scoring (the final text bans it for public and private
    actors alike) or manipulative techniques causing significant harm.
  - *High-risk areas in Annex III:* employment, education, access to essential services including credit
    scoring, biometric identification, law enforcement, migration and justice, plus AI that is a safety
    component of regulated products.
  - *Transparency obligations* for systems like chatbots and generated media; *minimal risk* for the
    rest. General-purpose AI models carry their own separate provider obligations.
- **Impact.** Who is affected, how severely and how reversibly if the output is wrong.
- **Autonomy.** Human decides with AI advice, human approves AI decision, or fully automated action.
- **Data sensitivity.** Health, financial, children's, biometric or other special-category data.
- **Exposure.** Internal staff only, customers, or the public.

### The combining rule
The order and the rule matter:
1. **Knock-out questions first.** A prohibited practice stops the process. A use in a regulated high-risk
   area sets a floor that no other answer can lower.
2. **Then take the maximum across the remaining factors, not an average,** *because* averaging lets
   "internal only" cancel out "decides who gets disciplined", and harm does not average.
3. **Decide which factors drive the tier and which only trigger controls.** Data sensitivity in
   particular can instead be wired to specific controls at any tier (vendor terms, redaction, access
   logging). That is a policy choice to write down once.
4. **Encode the Annex III exemption.** An Annex III use is not high-risk if it only performs a narrow
   procedural task, improves the result of a completed human activity, detects patterns without replacing
   human assessment, or does preparatory work. The exemption never applies when the system profiles
   people, and the reasoning must be documented.
5. **Record the answers, not just the tier,** so a later change in one answer re-runs the classification
   instead of requiring someone to remember why.

### 5. Attach controls to tiers
| Tier | Required controls |
|---|---|
| Low | Inventory entry and acceptable-use rules |
| Medium | Adds an evaluation, a system card and monitoring |
| High | Adds impact assessment, human oversight design, audit trails, bias testing, review board sign-off and periodic re-review |

This mapping is the payoff, *because* teams know up front what a tier costs them.

### 6. Keep it alive
- **Re-classify on change** of purpose, data, model provider, geography or autonomy level.
- **Reconcile** the inventory against discovery signals on a schedule.

## Diagram
Read the classification top to bottom: knock-out questions first, then a floor, then the maximum of the
remaining factors, with sensitive data routed to controls.

```mermaid
flowchart TD
    I["New or changed use case"] --> P{"Prohibited practice?"}
    P -->|"yes"| S["Stop: do not build"]
    P -->|"no"| A{"Annex III area?"}
    A -->|"yes, no exemption or profiles people"| H["Floor: high"]
    A -->|"no, or exemption documented"| F["Score impact, autonomy, exposure"]
    H --> F
    F --> M["Tier = maximum of floor and scores"]
    D["Sensitive data"] -.->|"adds data controls"| C
    M --> C["Controls for tier"]
    C --> R[("Inventory entry with answers")]
    R -->|"purpose, data, provider or autonomy changes"| I
```

## Examples
### Classification as code: knock-outs, floor, maximum
Encoding the questionnaire makes tiers repeatable and keeps the answers next to the result.

```python
TIERS = ["low", "medium", "high"]
ANNEX_III = {"employment", "education", "essential_services", "credit_scoring", "biometric_id",
             "law_enforcement", "migration", "justice"}

def classify(a: dict) -> dict:
    if a["prohibited_practice"]:
        return {"tier": "prohibited", "why": ["prohibited practice: stop"]}
    why, floor = [], 0
    if a["area"] in ANNEX_III:
        # exemption: narrow procedural, improves completed human work, pattern detection
        # without replacing human assessment, or preparatory; never when profiling people
        if a["exemption_condition"] and not a["profiles_people"]:
            why.append("Annex III exemption claimed: document the reasoning")
        else:
            floor = 2
            why.append(f"Annex III area '{a['area']}' sets floor high")
    scores = {"impact": {"minor": 0, "significant": 1, "severe": 2}[a["impact"]],
              "autonomy": {"human_decides": 0, "human_approves": 1, "automated": 2}[a["autonomy"]],
              "exposure": {"internal": 0, "customers": 1, "public": 1}[a["exposure"]]}
    tier = max([floor, *scores.values()])          # maximum, never an average
    why += [f"{k}={v}" for k, v in scores.items() if v == tier and v > 0]
    controls = ["data controls"] if a["sensitive_data"] else []   # triggers controls, not the tier
    return {"tier": TIERS[tier], "why": why, "extra_controls": controls, "answers": a}

base = dict(prohibited_practice=False, area=None, exemption_condition=False, profiles_people=False,
            sensitive_data=False)
cases = {
    "marketing emails": {**base, "impact": "minor", "autonomy": "human_decides",
                         "exposure": "internal"},          # staff use it; a human edits and sends
    "claim summaries":  {**base, "impact": "significant", "autonomy": "human_decides",
                         "exposure": "internal", "sensitive_data": True},
    "rank applicants":  {**base, "area": "employment", "profiles_people": True,
                         "impact": "significant", "autonomy": "human_approves", "exposure": "internal"},
}
for name, ans in cases.items():
    r = classify(ans)
    print(f"{name:17} {r['tier']:7} {r['why']} {r['extra_controls']}")
# marketing emails  low     [] []
# claim summaries   medium  ['impact=1'] ['data controls']
# rank applicants   high    ["Annex III area 'employment' sets floor high"] []
```

### Worked scenario: one model, three tiers
A mid-size insurer runs discovery and intake and gets 46 entries. Three of them show why classification
must be by use case:

| Use case | Same model? | Decision | Affected | Tier |
|---|---|---|---|---|
| Drafting marketing emails | Vendor LLM A | Human edits and sends | Public (indirect) | Low |
| Summarizing claim files for adjusters | Vendor LLM A | Advisory, adjuster decides | Claimants | Medium |
| Ranking job applicants for interview | Vendor LLM A via an HR SaaS | Shortlist shown to recruiter | Applicants | High |

- **Marketing emails (low).** Needs an inventory entry and a rule against pasting customer data.
- **Claim summaries (medium).** Processes personal and possibly health data and informs a consequential
  decision. Needs evaluation on real claim files, a groundedness check, a system card and an audit trail
  of which summary an adjuster saw.
- **Claim summaries is the contested row.** Health data alone would make it high under a pure maximum
  rule; this insurer chose to let sensitive data trigger data controls rather than the tier when a human
  decides. Another organization could reasonably tier it high; what matters is that the rule is explicit
  and applied the same way to every entry.
- **Applicant ranking (high).** Employment is an EU AI Act high-risk area, and applicants never consented
  to the tool. Needs an impact assessment, bias testing across applicant groups, documented human
  oversight, vendor documentation and review board approval.
- **Discovery found 9 undeclared entries,** including two teams calling model APIs from personal accounts,
  which became the first remediation items.
- **The output is a register that targets review effort** at the handful of high-tier uses, not all 46
  equally.

## Production patterns
- **Intake gate at procurement and launch.** Make an inventory ID a required field on purchase requests
  for AI SaaS and on production access requests for model API keys. Keep the low-tier form to a few
  minutes. Monitor median time from intake to classification and the share of requests escalated.
- **Egress and key discovery feed.** Route outbound traffic through a proxy that logs requests to known
  model API domains, and add model-provider key patterns to secret scanning. Reconcile hits against the
  inventory weekly. Monitor undeclared sources found per month; it should trend down.
- **Answers stored, tier computed.** Keep questionnaire answers as structured fields and compute the tier
  in code, as in the example, so a policy change re-tiers every entry in one run. Monitor how many entries
  changed tier after each policy update.
- **Change triggers on the entry.** Tie re-classification to events you can detect: a new model provider
  in the gateway config, a new data category in the pipeline, an autonomy flag flipped. Monitor entries
  past their review date.
- **Owner liveness check.** Nightly, look up every owner in the HR or identity directory and flag entries
  whose owner has left. Monitor orphaned entries and the time to reassign them.

## Trade-offs
- **Broad definition vs inventory size.** Calling more things "AI" catches shadow and embedded use but
  inflates the inventory; tiering keeps that cheap because most entries land in the low tier with minimal
  obligations.
- **Questionnaire vs judgement.** Questionnaire-driven classification is consistent and fast, but edge
  cases need human judgement; add an escalation path rather than more questions when ambiguous answers
  become common.
- **Mandatory registration vs friction.** Registration before procurement gives early visibility but adds
  friction; it flips to driving shadow AI when intake is slow, so the low-tier path must take minutes, not
  weeks.
- **Technical discovery vs employee privacy.** It finds what people forget, but egress monitoring of
  employee traffic raises privacy and works-council concerns in some jurisdictions; agree scope with
  legal and HR first.

## Pitfalls
- **Inventorying models instead of use cases.** Symptom: one "Vendor LLM" row covering twenty uses with
  wildly different risk.
- **Relying only on self-declaration.** Symptom: an auditor or journalist discovering a use the inventory
  does not contain.
- **Treating the inventory as a one-off spreadsheet.** Symptom: entries whose owner left months ago and
  whose model provider has changed.
- **Classifying once at launch.** Symptom: an "advisory" tool that quietly became automated decision-making
  without anyone re-reviewing it.
- **Ignoring AI features inside bought SaaS.** Symptom: customer data flowing to a sub-processor nobody
  assessed.

## When to use
- **Start when:** an organization has more than a handful of AI uses or faces any AI regulation, audit or
  customer due-diligence questionnaire, which today is nearly everyone.
- **It is a prerequisite for:** an ISO/IEC 42001 management system; the NIST AI RMF (inventorying AI
  systems is an explicit Govern outcome, GOVERN 1.6, and every Map activity presumes the list); and EU AI
  Act role and obligation analysis.
- **Measure:** coverage (discovered uses reconciled against declared), freshness (entries reviewed within
  their cycle) and time-to-classify.
- **Interviews:** the key insight is that risk attaches to use, autonomy and affected people, not to the
  model, and that tiering exists to make governance proportionate.

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

## Interview questions
### Why do we need an AI inventory if procurement already tracks vendors?
- **Procurement tracks contracts, not uses.** One vendor LLM can sit behind twenty use cases with very
  different risk, and AI features switched on inside existing SaaS never pass procurement again.
- **Governance needs the use-level list,** with owner, data, autonomy and affected people, because every
  other control (cards, evals, audit trails, incident response) is applied per entry.

### How would you find shadow AI in a 5,000-person company?
- **Declaration plus detection:** an intake form tied to procurement and API key issuance, plus egress
  proxy logs for model API domains, secret scanning for provider keys, AI line items on bills and SSO logs.
- **Ask SaaS vendors** which features use AI and on which data.
- **Agree scope with legal and HR** before monitoring employee traffic, because some jurisdictions restrict it.

### Why take the maximum across risk factors instead of a weighted score?
- **Harm does not average.** A weighted score lets "internal only" offset "decides who gets disciplined".
- **Knock-outs and floors come first:** prohibited practices stop the process and Annex III areas set a
  high floor no other answer can lower.
- **The trade-off:** a pure maximum over-tiers some uses, so decide explicitly which factors only trigger
  controls, such as sensitive data wired to redaction and vendor terms.

### A summarization tool was classified medium as advisory. Six months later it auto-closes tickets. What should happen?
- **Autonomy changed, so classification re-runs.** Moving from human decides to automated action can
  raise the tier and the required controls.
- **Detect it by trigger, not memory:** an autonomy flag or new tool permission on the entry should fire
  re-review automatically.
- **Until re-reviewed,** the new behaviour should be blocked or held behind human approval.

### When does the EU AI Act Annex III exemption apply, and when can it never apply?
- **It can apply** when the system only performs a narrow procedural task, improves a completed human
  activity, detects patterns without replacing human assessment, or does preparatory work.
- **It never applies** when the system profiles people, and the reasoning must be documented either way.

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
