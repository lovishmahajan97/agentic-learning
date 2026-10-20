---
id: compliance-architecture
module: security-governance-compliance
name: Compliance architecture (GDPR, SOC 2, HIPAA, ISO 42001, EU AI Act, DPDP Act)
kind: concept
difficulty: advanced
est_minutes: 24
related:
  - security-governance-compliance/data-residency
  - security-governance-compliance/data-classification
  - security-governance-compliance/pii-protection-dlp
  - security-governance-compliance/audit-trails
  - security-governance-compliance/ai-inventory-and-use-case-risk-classification
  - data-architecture/data-retention-and-deletion-architecture
  - deployment/compliance-constraints
  - deployment/security-questionnaires
sources:
  - Regulation (EU) 2016/679 — General Data Protection Regulation
  - Regulation (EU) 2024/1689 — Artificial Intelligence Act
  - ISO/IEC 42001:2023 — Information technology, Artificial intelligence, Management system
  - Digital Personal Data Protection Act, 2023 (India)
---

## TL;DR
Compliance architecture is designing an AI system so that legal and assurance obligations are enforced
by its structure (data flows, regions, access, logging, retention, documentation) and proven by
automatically collected evidence, rather than asserted in policy documents. Its core move is one control
set mapped to many frameworks, because GDPR, SOC 2, HIPAA, ISO/IEC 42001, the EU AI Act and India's DPDP
Act overlap heavily but differ in scope, triggers and proof. This is orientation, not legal advice.

## Why it exists
An AI product sold to enterprises meets several regimes at once. A European customer brings GDPR; a US
hospital brings HIPAA; every enterprise buyer asks for a SOC 2 report; an Indian customer brings the DPDP
Act; a high-risk use case in the EU brings the AI Act; and a growing number of buyers ask for ISO/IEC
42001 certification. Each arrives as a questionnaire, a contract clause or an audit.

The naive approach treats each one as a separate project: a GDPR spreadsheet, a SOC 2 sprint before the
audit window, a HIPAA addendum. It fails for three reasons. First, duplicated work: "who can access
customer data, and how do you know?" is asked by all of them, and answering it five ways guarantees five
inconsistent answers. Second, compliance by document drifts from reality: the policy says data stays in
the EU while a new feature quietly calls a model endpoint in the US. Third, AI adds data paths that
classic controls never saw: prompts containing personal data sent to a third-party model, embeddings of
documents that must later be erased, logs of conversations, training on customer data, and decisions about
people.

The architectural answer is to identify the obligations that actually bind, express them as a common set
of controls, enforce as many as possible in code and infrastructure, and generate evidence continuously,
so that answering a new framework is mostly a mapping exercise.

## How it works
### The six regimes, by what they demand
**GDPR (EU and EEA personal data).** Principles of lawfulness, purpose limitation, data minimization,
accuracy, storage limitation, integrity and confidentiality; a lawful basis for each processing purpose;
stricter rules for special categories such as health data; data subject rights (access, rectification,
erasure, objection); rules on solely automated decisions with legal or similarly significant effects;
data protection impact assessments for high-risk processing; processor contracts with every vendor
handling the data; restrictions on transfers outside the EEA; and personal data breach notification to
the supervisory authority, normally within 72 hours. For AI: is training on customer data a compatible
purpose, can you erase someone from an index or a fine-tune, and is the model provider a processor with a
proper agreement?

**SOC 2 (assurance, not law).** An attestation report by an independent CPA firm against the AICPA Trust
Services Criteria: security (always in scope), plus optionally availability, processing integrity,
confidentiality and privacy. A Type I report assesses control design at a point in time; Type II assesses
operating effectiveness over a period. It is not a certification and prescribes no specific technology;
you define controls and prove they ran. For AI: model providers and vector stores become in-scope
sub-processors, and change management must cover prompts and models, not just code.

**HIPAA (US protected health information).** Applies to covered entities (providers, health plans,
clearinghouses) and their business associates. The Privacy Rule limits uses and disclosures and requires
the minimum necessary; the Security Rule requires administrative, physical and technical safeguards
including access controls and audit controls; the Breach Notification Rule sets notification duties.
For AI: any vendor receiving PHI, including a model API, must sign a business associate agreement first,
and the specific service and configuration must be covered by it. De-identification via the Safe Harbor
or Expert Determination methods takes data out of scope.

**ISO/IEC 42001 (AI management system).** A certifiable management-system standard with the same
high-level structure as ISO/IEC 27001: context, leadership and AI policy, planning with AI risk and impact
assessment, support, operation, performance evaluation, and improvement, plus Annex A controls. It
governs *how the organization manages AI* (inventory, roles, lifecycle, impact assessment, supplier
oversight), not one system's accuracy. It pairs naturally with an existing ISO 27001 programme.

**EU AI Act (Regulation (EU) 2024/1689).** Risk-based product-style regulation keyed to intended purpose
and to your role (provider, deployer, importer, distributor). Prohibited practices are banned. High-risk
systems (Annex III areas such as employment, credit scoring, education and essential services, plus
safety components of regulated products) require a risk management system, data governance, technical
documentation, automatic logging, transparency to deployers, human oversight, accuracy and robustness,
conformity assessment and post-market monitoring; deployers must use them as instructed, assign human
oversight and keep logs. Transparency duties cover chatbots and synthetic content, and general-purpose
AI model providers have their own obligations. Obligations phase in over several years from entry into
force in August 2024, and parts of the timeline have been subject to amendment proposals, so check the
current dates for your obligations.

**DPDP Act 2023 (India digital personal data).** Data Fiduciaries need valid consent (free, specific,
informed, unambiguous, with clear notice) or a listed legitimate use; Data Principals have rights to
access, correction and erasure and to grievance redress; consent managers are a recognized role;
Significant Data Fiduciaries carry extra duties such as audits and impact assessments; children's data
requires verifiable parental consent; breaches must be notified to the Data Protection Board and affected
people. Cross-border transfers are permitted except to countries the government restricts. Its
implementing rules phase in over time, so verify which duties are in force.

### The architecture
1. **Applicability matrix.** Per product and customer segment: which regimes apply and in what role
   (controller or processor, fiduciary, provider or deployer, business associate). Wrong role, wrong
   obligations.
2. **Data map.** Every flow of personal or regulated data through the AI path: ingestion, prompts,
   retrieval, model provider, logs, evals, training. Most compliance questions are answered here.
3. **Common control framework.** One control ("access to production customer data requires SSO, MFA and a
   ticket; access is logged and reviewed quarterly") mapped to the clauses it satisfies in each framework.
4. **Enforcement in the platform.** Region pinning and routing by tenant, a gateway that only allows
   vendors with the right agreements for the data class, redaction before prompts leave, tenant-scoped
   retrieval, retention and deletion jobs that cover vectors and logs, audit trails.
5. **Continuous evidence.** Controls emit evidence automatically (access reviews, config snapshots,
   deletion job results, eval reports), so audits sample existing evidence instead of triggering a scramble.

## Example
A SaaS clinical documentation assistant serves a German hospital group, a US clinic network and an Indian
hospital chain. The platform encodes the obligations as routing policy at the gateway, so a request
carrying PHI cannot reach a model endpoint lacking a BAA, and EU personal data cannot leave approved regions.

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Endpoint:
    name: str; region: str; baa: bool; dpa: bool; zero_retention: bool

ENDPOINTS = [
    Endpoint("vendor-a-eu", "eu-central", baa=False, dpa=True, zero_retention=True),
    Endpoint("vendor-a-us", "us-east", baa=True, dpa=True, zero_retention=True),
    Endpoint("selfhost-in", "ap-south", baa=False, dpa=True, zero_retention=True),
]

TENANT_POLICY = {   # derived from the applicability matrix, reviewed by legal
    "de-hospital": {"regions": {"eu-central"}, "needs_baa": False},
    "us-clinic":   {"regions": {"us-east"}, "needs_baa": True},    # PHI under HIPAA
    "in-hospital": {"regions": {"ap-south", "eu-central"}, "needs_baa": False},
}

def route(tenant: str, contains_personal_data: bool) -> Endpoint:
    p = TENANT_POLICY[tenant]
    for e in ENDPOINTS:
        if e.region not in p["regions"]: continue
        if p["needs_baa"] and not e.baa: continue
        if contains_personal_data and not (e.dpa and e.zero_retention): continue
        return e
    raise PermissionError(f"no compliant endpoint for {tenant}")   # fail closed, and audit it

print(route("us-clinic", True).name)    # vendor-a-us
print(route("de-hospital", True).name)  # vendor-a-eu
```

The same routing table is evidence: the SOC 2 auditor samples its change history, the hospital's GDPR
team sees region enforcement, the US clinic's HIPAA review sees the BAA check, and the ISO 42001 audit sees
supplier controls. One control, four frameworks.

## Trade-offs
- A unified control framework removes duplicated audit work, but needs an owner and a mapping that is
  maintained as frameworks change; for a single-framework startup a direct checklist is simpler.
- Enforcing compliance in the gateway and infrastructure makes violations impossible rather than merely
  forbidden, but adds latency and blocks features until vendors sign agreements; fail-closed is right for
  regulated data, fail-open may be acceptable for public content.
- Pinning regions and self-hosting satisfies strict residency, but loses access to the newest models and
  raises cost; it flips when a customer contract or regulator makes residency non-negotiable.
- Pursuing certifications such as ISO 42001 or SOC 2 Type II shortens sales cycles, but costs ongoing
  audit effort; it pays off when enterprise buyers repeatedly ask for them.

## Pitfalls
- Assuming a provider's certifications cover your system; the symptom is an auditor asking about your own
  access reviews, prompts and logs, which no vendor report addresses.
- Treating SOC 2 as a certificate or HIPAA as satisfied by encryption alone; the symptom is a failed
  customer review that asks for a BAA or for evidence of controls operating over time.
- Forgetting derived data in erasure; the symptom is a deletion request honoured in the database while the
  person's text lives on in embeddings, caches, logs and eval sets.
- Getting your role wrong (provider vs deployer, controller vs processor); the symptom is obligations
  discovered late in a deal or audit that the architecture never accounted for.
- Point-in-time compliance; the symptom is a frantic evidence hunt every audit season and a new feature
  calling an unapproved model endpoint between audits.

## When to use
Build compliance into the architecture as soon as you handle personal, health or regulated data or sell
to enterprises; retrofitting region pinning or deletion across vectors and logs is far harder than
designing it in. Start with the applicability matrix and data map, then add enforcement where violations
would be costly, and automate evidence before the first audit. Lean on specialist counsel for
interpretation: architects translate obligations into controls, they do not decide what the law requires.
In interviews, show the mapping mindset, the AI-specific data paths, and the difference between law
(GDPR, HIPAA, AI Act, DPDP), attestation (SOC 2) and certifiable management systems (ISO 42001).

## Role lens
- **Engineer:** Implements region-aware routing, redaction, tenant-scoped retrieval, deletion jobs that
  reach vectors and logs, and the automatic evidence each control emits.
- **Architect:** Owns the applicability matrix, data map and common control framework, and decides which
  obligations are enforced in infrastructure versus process.
- **FDE:** Fills security questionnaires and DPIAs with the customer, maps their regime (HIPAA, DPDP,
  sector rules) to existing controls, and flags gaps before deployment, not after.

## Check yourself
**Q:** An EU bank wants your LLM assistant to help credit officers assess loan applications. Which
regimes and roles come into play, and what architectural controls follow?
**A:** GDPR applies to applicant data; the bank is controller and we are likely processor, so we need a
processing agreement, EU-region processing or valid transfer mechanisms, minimization, erasure across
indexes and logs, and support for the bank's DPIA. Credit scoring is an Annex III high-risk area under the
EU AI Act, so we must establish whether we are provider of a high-risk system, which means risk management,
technical documentation, logging, human oversight design and conformity assessment, while the bank as
deployer needs oversight and log retention. Add SOC 2 evidence, audit trails, and human review of decisions.

## Quiz
### Q: What is a SOC 2 report?
- [ ] A government certification that a company complies with data protection law
- [x] An independent CPA attestation on controls against the Trust Services Criteria, with Type II covering operating effectiveness over a period
- [ ] An ISO standard for AI management systems
- [ ] A mandatory filing for any company processing personal data in the US
> SOC 2 is assurance, not law or certification: an auditor attests that defined controls are designed (Type I)
> and operated effectively over a period (Type II). ISO/IEC 42001 is the certifiable AI management standard.

### Q: A US healthcare customer wants to send PHI to your feature that calls a third-party model API. What must be in place first?
- [ ] The model vendor's SOC 2 report is sufficient
- [ ] Encrypting the prompts in transit is sufficient
- [x] A business associate agreement covering that specific vendor service, alongside your own BAA with the customer
- [ ] Nothing, because model APIs do not store data
> Under HIPAA, a vendor that receives PHI on behalf of a covered entity or business associate must be bound
> by a BAA. Encryption and attestations are safeguards but do not replace the contractual requirement.

### Q: Why does the EU AI Act force teams to determine their role before designing controls?
- [ ] Only providers are subject to fines
- [ ] Deployers are exempt from all obligations
- [x] Obligations differ by role, so a provider of a high-risk system carries documentation and conformity duties a deployer does not
- [ ] Roles determine which EU country's law applies
> The Act assigns different duties to providers, deployers, importers and distributors. A deployer must use
> the system as instructed with human oversight and logging, while the provider bears conformity assessment.
