---
id: security-questionnaires
module: deployment
name: Security questionnaires
kind: practice
difficulty: intro
est_minutes: 15
status: reviewed
related:
  - deployment/security-and-architecture-reviews
  - deployment/compliance-constraints
  - security-governance-compliance/compliance-architecture
  - security-governance-compliance/data-residency
  - security-governance-compliance/ai-security
  - safety-security/data-leakage
  - scoping/dependency-and-risk-management
sources:
  - Official docs: Cloud Security Alliance — Consensus Assessments Initiative Questionnaire (CAIQ)
  - Official docs: Shared Assessments — Standardized Information Gathering (SIG) questionnaire
  - Official docs: AICPA — SOC 2
---

## TL;DR
Before an enterprise lets a vendor touch its data, its third-party risk team sends a security
questionnaire: hundreds of questions on encryption, access control, incident response, subprocessors and
now AI-specific data use. It exists because the buyer cannot audit every vendor, so it collects written
claims and evidence that become part of the contract. Answer from one maintained source of truth,
precisely and never aspirationally, because every answer is a commitment you may be held to.

## Why it exists
A large company may have thousands of vendors, each a possible path to a breach. Its risk team cannot
send engineers to inspect each one, so it standardizes the inquiry: a questionnaire, either an industry
template such as the Shared Assessments SIG or the Cloud Security Alliance's CAIQ, or a custom spreadsheet
built from the buyer's own control framework. Answers are scored, gaps become findings, and serious
gaps block procurement or become contract clauses.

For AI vendors the questionnaire carries extra weight because the product's core function is sending
customer data to a model. Buyers ask questions they did not ask a CRM vendor: is our data used to train
any model, which model providers see it, how long do they retain prompts, can your agent take actions,
how do you stop prompt injection from exfiltrating data.

The naive approaches fail. Having whoever gets the spreadsheet answer it from memory produces
inconsistent answers across customers, which a sophisticated buyer or a later dispute will expose.
Answering "Yes" to everything to unblock the deal creates misrepresentation risk, because questionnaires
are often incorporated by reference into the contract. And treating it as paperwork separate from
engineering means answers drift from the system as it changes. The questionnaire is really a structured
interface between your actual controls and the buyer's risk model, and it should be maintained like one.

## How it works
### What arrives
Typically a spreadsheet or a portal from a third-party risk platform, with yes/no/not-applicable columns,
a free-text comment and an evidence-upload field per question. Questions cluster into domains:
governance and policies, access control and identity, encryption in transit and at rest, key
management, network security, vulnerability management and penetration testing, logging and
monitoring, incident response and notification, business continuity, data retention and deletion,
privacy, subprocessors, secure development, and increasingly an AI section.

### What happens on the buyer's side
Understanding the reviewer's workflow explains what they reward:
1. **Inherent-risk tiering.** Before sending anything, the risk team classifies the engagement by what
   you will touch: data sensitivity, volume, whether you get network or system access, and how
   critical you are to operations. A vendor that processes patient records with agent tool access
   lands in the top tier; a vendor that sees only marketing copy does not. The tier sets the depth,
   *because* the team's review hours are the scarce resource. (Templates are built for this: the SIG
   comes in a shorter Lite and a fuller Core form, and CAIQ maps each question to a control in the CSA
   Cloud Controls Matrix.)
2. **Scoring and findings.** Each answer is compared with the buyer's required control. A "No" or a
   vague answer on a required control becomes a finding with a severity.
3. **Follow-up and evidence requests.** Reviewers ask for proof on the answers that matter most to
   their tier; precise first answers mean fewer rounds.
4. **Disposition.** Findings are accepted as risk, mitigated by a compensating control, or turned into
   remediation commitments with dates, which often land in the contract.
5. **Reassessment.** High-tier vendors are typically re-reviewed periodically and on material change,
   so an answer you give today will be compared with the one you give next year.

The practical upshot: a reviewer is looking for residual risk they can sign off on, not a perfect
score, so a clearly scoped gap with a mitigation is easier to approve than an ambiguous "Yes".

### Evidence that shortcuts it
Buyers accept independent assurance in place of many answers. A SOC 2 Type II report is an auditor's
opinion on whether your controls operated effectively over a period (Type I covers design at a point in
time only). An ISO/IEC 27001 certificate shows a certified information security management system. A
recent penetration test summary, a public subprocessor list and a trust center page answer many questions
at once. Sending the report early, under NDA, often removes a large share of the questionnaire.

### The answer library
Mature vendors keep a single library of approved answers, each tied to the underlying control, an owner
(security, infrastructure, legal, ML), the evidence that proves it and a review date. New questionnaires
are answered by matching each question to library entries and adapting wording. This is a retrieval
problem, and many teams use RAG over the library to draft responses, but a human owner must approve
each answer, because a confidently wrong generated answer is precisely the misrepresentation you are
trying to avoid.

### Writing a good answer
State the fact, its scope, and the mechanism, and say where it is evidenced. Answer the question asked,
not a neighbouring one. When the honest answer is "No" or "Partially", say so and add the compensating
control or a dated roadmap item; reviewers deal with gaps constantly, but discovered misstatements
destroy trust. Use "Not applicable" only with a one-line reason.

### The AI section
Expect questions on: training use of customer data (yours and every model provider's), prompt and
output retention by each subprocessor, region of processing, model provenance and evaluation, human
oversight, guardrails against prompt injection and data leakage, tool permissions for agents, and how
customers can delete their data including embeddings and caches. Answer these per data flow, because the
honest answer often differs between the hosted-API path and a self-hosted model path.

## Example
A health-tech buyer sends 300 questions. Three show the difference between a weak and a strong answer.

"Is customer data used to train AI models?" Weak: "No." Strong: "No. Customer prompts, documents and
outputs are not used to train or fine-tune our models. Our LLM subprocessor processes data under API
terms that exclude training use; its retention terms and region are listed on our subprocessor page.
Evidence: DPA section 4, subprocessor list." The strong version covers the subprocessor, which is what
the buyer really wants to know.

"Is all data encrypted at rest with customer-managed keys?" The truth: data at rest is encrypted with
provider-managed keys; customer-managed keys exist only on the dedicated-deployment tier. Answer
"Partially", explain both tiers, and point to the dedicated option. Saying "Yes" would be true for one
tier and false for the one this customer bought.

"Do you retain prompts and responses?" The engineers check and find traces with full prompts kept for
30 days in the observability tool, which nobody had listed. The answer becomes: "Yes, for 30 days for
debugging, access-restricted, deletable on request," and the observability vendor is added to the
subprocessor list. The questionnaire surfaced a real gap before an audit did.

Overall, the SOC 2 report removed about a third of the questions as covered (an illustrative proportion),
and the remaining answers were drafted from the library and approved by owners within the week.

## Trade-offs
- Industry templates such as SIG or CAIQ let you answer once and reuse widely, but buyers with their own
  frameworks will still send custom forms, so the library must map to both.
- Independent reports like SOC 2 shortcut many questions and carry weight, but they cost significant time
  and money annually; for an early-stage vendor with few enterprise deals, a strong library may suffice first.
- Detailed, specific answers build trust and reduce follow-ups, but each specific detail is a commitment;
  where the system changes often, describe the control and its guarantee rather than its exact implementation.
- AI-drafted answers from the library save hours per questionnaire, but they only work when every
  generated answer is reviewed by the owner of the underlying control.

## Pitfalls
- Answering aspirationally to unblock a deal; the symptom is a later audit or incident where the
  contract-referenced answer is shown to be false, turning a technical gap into a legal one.
- Forgetting subprocessors in the data-flow story, especially model providers and observability tools;
  the symptom is a buyer finding an unlisted vendor in your trace exports or DNS logs.
- Letting answers diverge across customers; the symptom is two customers comparing notes, or the same
  question answered differently in a renewal, prompting a full re-review.
- Never revisiting answers after architecture changes; the symptom is stale claims, for example
  "data never leaves the EU" after someone added a US-hosted model fallback route.
- Attaching sensitive evidence without controls, such as a full penetration-test report with
  unfixed findings or a detailed network diagram, to a portal outside an NDA; the symptom is your
  internal security detail circulating among people you have never met. Share summaries and attestation
  letters by default, full reports under NDA on request.
- Treating "not applicable" as a shortcut; the symptom is reviewer follow-ups asking why, which slow
  the review more than a one-line justified answer would have.

## When to use
Every sale to a regulated or large enterprise passes through one, so prepare before the first deal: write
the answer library, the subprocessor list, a data-flow diagram and the AI data-use statement while the
architecture is fresh. Pursue SOC 2 or ISO 27001 when the volume of questionnaires and deal size justify
the audit cost. Route every questionnaire through the owner of each control rather than the account
team alone. Measure turnaround time, follow-up rounds and the number of answers changed after
engineering review. In interviews for FDE and architect roles, expect "how do you answer a question
where the honest answer is no?" The answer is: honestly, with compensating controls and a dated plan.

## Role lens
- **Engineer:** Owns accurate answers about the systems they build, such as retention in traces,
  embedding deletion and model routing, and flags when a change invalidates a published answer.
- **Architect:** Designs data flows so the answers are good ones, for example a no-retention model path or
  a region-pinned tier, and keeps the authoritative data-flow diagram the answers rest on.
- **FDE:** Shepherds the customer's questionnaire through internal owners, explains gaps and compensating
  controls on calls, and feeds recurring asks back to product as roadmap items.

## Check yourself
**Q:** A prospect's questionnaire asks "Does any customer data leave the EU?" Your app runs in an EU
region, but its fallback model route goes to a US-hosted provider when the EU model is rate-limited, and
your error tracker is US-hosted. How do you answer, and what do you do beyond answering?
**A:** The honest answer is "Yes, in specific cases," followed by each flow: the fallback route may send
prompts to a US region when the EU model is throttled, and error events may contain request fragments sent
to a US-hosted tracker. Name the subprocessors, the transfer mechanism in the DPA, and any mitigations such
as scrubbing payloads from error events. Beyond the answer, offer a configuration that keeps data in the EU:
disable cross-region fallback for this tenant, accepting more throttling or degraded responses, and switch
the tracker to an EU instance or strip content. Then update the answer library and subprocessor list so the
next questionnaire is correct, and tell engineering the routing change has compliance consequences.

## Quiz
### Q: Why does sending a SOC 2 Type II report early often shorten a security questionnaire?
- [ ] It certifies the product is free of vulnerabilities
- [x] It is an independent auditor's opinion that controls operated over a period, which buyers accept in place of many individual answers
- [ ] It replaces the need for a data processing agreement
- [ ] It proves compliance with HIPAA and GDPR
> A Type II report gives third-party assurance on control operation over time, so buyers can mark whole
> domains as covered. It is not a vulnerability guarantee, a legal agreement or proof of any regulation.

### Q: The honest answer to "Do you support customer-managed encryption keys?" is "only on our dedicated tier," and this buyer is on the shared tier. What is the best response?
- [ ] Answer "Yes", since the product supports the feature
- [ ] Answer "Not applicable", since this buyer did not purchase that tier
- [x] Answer "Partially", explain which tier offers it and what protects data on the shared tier
- [ ] Leave the question blank and raise it on a call later
> A scoped, accurate answer with the alternative is both honest and useful. "Yes" would misstate this
> buyer's protection, and "not applicable" is wrong because the question applies to them.

### Q: Your team uses an LLM over the answer library to draft questionnaire responses. What control matters most?
- [ ] Using the largest available model to reduce errors
- [ ] Letting the model answer only yes/no questions
- [ ] Re-running the model until two drafts agree
- [x] Requiring the owner of each underlying control to approve the drafted answer before it is sent
> Retrieval speeds drafting, but answers are commitments, and a fluent wrong answer is a misrepresentation.
> Model size and agreement between drafts do not establish truth; an accountable owner's review does.
