---
id: compliance-constraints
module: deployment
name: Compliance constraints
kind: concept
difficulty: core
est_minutes: 16
related:
  - security-governance-compliance/compliance-architecture
  - security-governance-compliance/data-residency
  - security-governance-compliance/audit-trails
  - security-governance-compliance/pii-protection-dlp
  - data-architecture/data-retention-and-deletion-architecture
  - deployment/customer-vpc-on-prem-air-gapped
  - deployment/security-questionnaires
  - deployment/security-and-architecture-reviews
sources:
  - Official docs: EU General Data Protection Regulation (Regulation (EU) 2016/679)
  - Official docs: US HHS — HIPAA Privacy and Security Rules
  - Official docs: EU Artificial Intelligence Act (Regulation (EU) 2024/1689)
  - Official docs: AICPA — SOC 2
---

## TL;DR
Compliance constraints are the deployment-shaping rules that laws, contracts and audit frameworks impose:
where data may be processed, which vendors may see it, how long it is kept, who may access it, and what
must be logged and proven. For AI systems they decide region, model provider, logging design and even
whether the cloud is allowed at all. This lesson orients you to how regimes become concrete constraints;
the deep treatment of compliance design lives in the security-governance-compliance module.

## Why it exists
A model-backed feature can be technically finished and still be undeployable. A hospital cannot send
patient notes to a model provider that has not signed a business associate agreement. A European insurer
may need contractual safeguards before prompts containing customer data leave the EU. A defense
contractor's data may be export-controlled, so only certain personnel may touch the system. None of these
are engineering preferences; they are obligations whose breach means fines, lost certifications, voided
contracts or worse.

The naive approach is to build first and "do compliance" at the end, with a policy document and a
checklist. That fails because compliance requirements are architectural. Data residency determines which
regions and model endpoints you may use. A no-training, limited-retention requirement determines which
model providers qualify. An erasure requirement determines whether you may fine-tune on customer data,
since deleting a record from model weights is not practical. An audit requirement determines what your
logs must contain, while a minimization requirement limits what they may contain. Discovering these after
the build means rework, sometimes a different deployment model entirely, such as moving from SaaS into the
customer's VPC.

The useful skill is translation: hearing "we are subject to HIPAA and SOC 2" and turning it into a short
list of concrete constraints the design must satisfy and the evidence it must produce.

## How it works
### From obligation to evidence
Every compliance requirement flows through the same chain: **obligation** (a law, contract clause or
framework criterion) → **constraint** on the system → **control** that enforces it → **evidence** that the
control works. Auditors and reviewers check the last link, so design controls that produce evidence
naturally: configuration in code, logs, test results.

### The regimes you will meet most
- **GDPR (EU personal data).** Requires a lawful basis, purpose limitation and data minimization, data
  processing agreements with processors, honoring rights such as erasure and access, and safeguards
  for transfers outside the EEA (adequacy decisions or mechanisms such as standard contractual clauses).
  It does not strictly mandate EU hosting, but many customers impose EU residency contractually because
  it is the simplest way to stay safe.
- **HIPAA (US health data).** Covered entities may share protected health information with a vendor only
  under a business associate agreement, and that obligation flows down to the vendor's subprocessors,
  including model and cloud providers. Security Rule safeguards cover access control, audit controls and
  transmission security.
- **SOC 2 and ISO/IEC 27001.** Not laws but assurance frameworks customers require of vendors. They turn
  into constraints on change management, access reviews, logging, incident response and vendor management.
- **PCI DSS (payment cards).** Any system that stores, processes or transmits cardholder data is in scope;
  the usual design move is keeping card data out of prompts and logs so the AI system stays out of scope.
- **Public sector and defense.** US federal cloud use requires FedRAMP-authorized services at an
  appropriate impact level; export-controlled data (for example under ITAR) restricts who may access it,
  which often pushes deployments on-prem or air-gapped.
- **EU AI Act.** A risk-based regulation that phases in obligations over several years: certain practices
  are prohibited, high-risk systems carry requirements for risk management, data governance, logging,
  human oversight and transparency, and general-purpose model providers have their own duties. Which tier
  a use case falls into is a legal determination, so involve counsel.
- **Sector rules.** Financial services add model risk management expectations (validation, monitoring,
  documentation); many industries add record-keeping rules that set minimum retention.

### The constraint categories
Translate any regime into these questions: **where** may data be processed and stored; **who** may access
it, including vendors and personnel; **what** data may enter prompts, logs and indexes; **how long** must
or may it be kept; **how** must it be protected (encryption, customer-managed keys); **what proof** must
exist (audit trails, change records); and **AI-specific** rules (no training use, human oversight,
disclosure that users are talking to AI).

### Constraints conflict
Audit retention can collide with erasure; debug logging with minimization; a cross-region fallback with
residency; fine-tuning on customer data with deletion. Resolve conflicts explicitly in the design, for
example by logging metadata and hashes rather than content, or keeping customer data in retrieval indexes
(which can be deleted) instead of in fine-tuned weights.

## Example
A US hospital network wants an assistant that summarizes clinician notes. Requirements stated by the
customer: HIPAA, the vendor's SOC 2 Type II, and their own policy of seven-year audit retention. The
translation into constraints and controls:

1. **BAA chain.** Notes are PHI, so the vendor signs a BAA with the hospital, and every subprocessor that
   sees PHI must be covered too: the cloud provider and the model provider. The model provider's offering
   is checked for BAA eligibility; a provider without one is excluded, whatever its quality.
2. **Retention and training.** The model API path must not retain prompts beyond what the BAA allows and
   must not train on them. Where that cannot be confirmed, the design switches to a self-hosted open-weight
   model in the hospital's VPC.
3. **Logs.** Debug traces originally captured full prompts. Under minimum-necessary handling, traces now keep
   request IDs, token counts, latency and a hash of the input; full content is stored only in the hospital's
   own audit store, with access logged.
4. **Audit.** Seven-year retention applies to the record of who generated which summary and when, not to the
   vendor's debug traces, which are kept for 30 days. Separating the two resolves the conflict between audit
   and minimization.
5. **Human oversight.** Summaries are drafts that a clinician must approve before they enter the record; the
   approval event is part of the audit trail.
6. **Evidence pack.** BAA list, data-flow diagram, retention configuration in IaC, access review records, and
   the SOC 2 report, ready for the hospital's security review.

The result changed two design decisions (model provider selection and log content) before any code shipped.

## Trade-offs
- Region-pinning and single-provider routing satisfy residency simply, but remove cross-region failover;
  if availability targets are strict, you need multiple in-region providers or accept degraded modes.
- Self-hosting a model in the customer's environment satisfies the strictest data-custody constraints,
  but costs GPU capacity, operations and often model quality; API providers with suitable terms win when available.
- Minimal logging lowers compliance exposure, but makes production debugging harder; structured metadata,
  hashes and on-demand, access-logged content capture recover most of the debugging value.
- Building a single compliant tier for every customer simplifies operations, but over-serves smaller
  customers; separate tiers cost more to run and must be described precisely in questionnaires.

## Pitfalls
- Treating "compliant" as a property of a vendor rather than of a configuration; the symptom is using a
  provider's HIPAA-eligible service without a signed BAA or with ineligible features enabled.
- Forgetting derived data: embeddings, caches, eval datasets and traces contain the same personal data;
  the symptom is an erasure request completed in the database while vectors and logs still hold it.
- Fine-tuning on customer personal data without a deletion story; the symptom is an erasure request that
  can only be honored by retraining or discarding the model.
- Letting an engineering change silently violate a constraint, such as a new fallback region or a new
  analytics SDK; the symptom is an audit finding against a previously compliant system.
- Deciding the EU AI Act risk tier or other legal classifications in engineering alone; the symptom is a
  design built for the wrong obligations, discovered when counsel finally reviews it.

## When to use
Ask about compliance in discovery, before choosing a deployment model or model provider: which regimes
apply, which customer policies go beyond them, and what evidence their reviewers will want. Translate
each into where/who/what/how-long/how/proof constraints, record them as non-functional requirements, and
enforce them in code and configuration so evidence comes for free. Revisit them whenever data sources,
regions, providers or features change. Do not attempt legal interpretation yourself: bring counsel in for
classification and contract terms. For the full design of controls, audit trails and residency, go to the
security-governance-compliance module. In interviews, show the translation from regime to architecture.

## Role lens
- **Engineer:** Implements constraints as code: region and provider allowlists in the gateway, log
  redaction, retention policies in IaC, and deletion that reaches embeddings and caches.
- **Architect:** Maps each applicable regime to architectural constraints, resolves conflicts such as audit
  retention versus minimization, and chooses the deployment model and providers that satisfy them.
- **FDE:** Elicits the customer's real obligations and internal policies early, confirms subprocessor
  agreements like BAAs, and assembles the evidence pack for their compliance and security teams.

## Check yourself
**Q:** An EU customer says "we are GDPR-regulated, so everything must stay in the EU." Your system uses an
EU-hosted model by default, a US-hosted fallback, and fine-tunes a small model on customer tickets. What
do you check and change?
**A:** First separate the law from the policy: GDPR permits transfers with safeguards, but the customer's
contractual requirement is stricter and is what you must meet. The US fallback violates it, so disable
cross-region fallback for this tenant and use a second EU-region provider or a degraded mode instead. Check
every subprocessor for EU processing, including tracing and error tools. Fine-tuning on tickets creates an
erasure problem, because personal data in weights cannot be selectively removed; either drop fine-tuning in
favor of retrieval over deletable indexes, train only on anonymized data, or agree a retraining policy.
Ensure deletion reaches embeddings and caches. Document it all in the DPA and data-flow diagram.

## Quiz
### Q: Why are compliance requirements best gathered before choosing the deployment model?
- [ ] Because auditors require requirements documents to be dated before code
- [x] Because constraints such as residency, subprocessor agreements and retention determine which regions, providers and deployment models are allowed
- [ ] Because compliance requirements rarely change after launch
- [ ] Because compliance reviews are faster for systems with fewer services
> Compliance rules are architectural: they can rule out a model provider, a region or SaaS hosting entirely.
> Learning them after the build forces rework, which is the cost early discovery avoids.

### Q: A hospital asks whether your assistant may use a hosted model API for clinical notes. What is the deciding requirement under HIPAA?
- [ ] The model must score above a set threshold on medical benchmarks
- [ ] The API must be hosted in the same state as the hospital
- [ ] The prompts must be encrypted with the hospital's own keys
- [x] The model provider, as a subprocessor handling PHI, must be covered by a business associate agreement
> PHI may flow to a vendor and its subprocessors only under BAAs. Encryption and hosting location matter
> for security, but without a BAA the flow is not permitted regardless of quality or location.

### Q: An auditor requires seven-year retention of activity records while the privacy team demands minimal logging of personal data. Which design resolves both?
- [ ] Keep full prompts and outputs for seven years in the debug trace store
- [ ] Disable logging entirely to satisfy minimization
- [x] Retain an audit record of who did what and when, with content in an access-controlled store, while debug traces keep only metadata for a short period
- [ ] Encrypt the logs so retention rules no longer apply
> Separating audit records from debug telemetry satisfies each requirement on its own terms. Keeping all
> content for seven years violates minimization, encryption does not remove retention duties, and
> disabling logs fails the audit obligation.
