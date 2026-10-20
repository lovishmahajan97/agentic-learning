---
id: security-and-architecture-reviews
module: deployment
name: Security & architecture reviews
kind: practice
difficulty: core
est_minutes: 18
status: reviewed
related:
  - deployment/security-questionnaires
  - deployment/compliance-constraints
  - deployment/deployment-readiness-reviews
  - security-governance-compliance/threat-modeling
  - security-governance-compliance/ai-security
  - system-design/c4-diagrams
  - system-design/adrs
  - safety-security/owasp-top-10-for-llms
sources:
  - Adam Shostack, 2014 — Threat Modeling: Designing for Security
  - Official docs: OWASP Top 10 for LLM Applications
  - Official docs: NIST AI Risk Management Framework
---

## TL;DR
A security and architecture review is the customer's (or your own) structured examination of a proposed
system before it goes live: data flows, trust boundaries, identity, encryption, network paths, failure
modes and, for AI, prompt injection, tool permissions and model data use. It exists because a
questionnaire captures claims while a review tests the design itself. You pass it by arriving with an
accurate diagram, a threat model and evidence, and leaving with tracked, dated findings.

## Why it exists
A questionnaire tells the buyer what you say your controls are. It cannot tell them how your system
fits into theirs: which of their networks it touches, what identity it uses to read their SharePoint,
whether an agent could email a customer, where an LLM prompt containing patient data is logged. Those
questions depend on the specific deployment, so large organizations route new systems through an
architecture review board and a security review before production access is granted. Internally, good
engineering teams run the same kind of review on their own designs before building.

Without a review, problems surface in the worst places: an over-privileged service account noticed during
an incident, a data path to an unapproved region found by an auditor, an agent's tool that can modify
records discovered by a user. Fixing design problems after deployment costs far more than changing a
diagram.

The naive way to "pass" is to show up with marketing slides and answer questions live. That fails
because reviewers are looking for specifics: every data store, every boundary crossing, every credential.
Vague answers produce a long list of open actions and a second meeting weeks later. AI systems make this
harder, because reviewers are often meeting LLM-specific risks for the first time and need you to explain
how the model can be manipulated and what bounds the damage. The vendor who brings that analysis
unprompted earns trust that a slide deck cannot.

## How it works
### The typical flow
1. **Intake.** The sponsor files a request with a system description, data classification and owners.
   Data classification sets the depth of review: a system touching regulated personal data gets more
   scrutiny than an internal FAQ bot.
2. **Pre-read package.** You submit documents in advance, because reviewers want to come to the meeting
   with questions, not discover the design in it.
3. **Review session.** Reviewers walk the diagram, probe boundaries and ask "what happens if" questions.
4. **Findings.** Each finding gets a severity, an owner and an outcome: fix before go-live, fix by a
   date, accept the risk with a named approver, or mitigate with a compensating control.
5. **Decision.** Approve, approve with conditions, or reject; conditions are tracked to closure,
   usually in the customer's risk register.

### What the pre-read contains
- **Architecture diagram** at container level (C4-style is common): every service, data store, external
  dependency and trust boundary, including model providers and observability tools.
- **Data flow diagram** for each sensitive flow: what data, from where, to where, encrypted how, retained
  how long. This is the document reviewers scrutinize most, because breaches follow data.
- **Identity and access:** how users authenticate (their SSO), how services authenticate to each other and to
  customer systems, which permissions each role and service account has, and why.
- **Network:** ingress and egress paths, private endpoints, VPNs, what is publicly reachable.
- **Encryption and keys:** in transit, at rest, who holds keys, whether customer-managed keys are used.
- **Logging and monitoring:** what is logged, where, for how long, whether prompts or outputs are included.
- **Threat model:** for example STRIDE per boundary, plus AI-specific threats from the OWASP LLM list:
  prompt injection (direct and via retrieved documents), sensitive information disclosure, excessive
  agency of tools, improper output handling (called insecure output handling in the 2023 list), vector
  and embedding weaknesses, and supply chain of models and datasets.
- **Evidence:** penetration test summary, vulnerability scanning, SBOM, SOC 2 or ISO reports.

### Applying STRIDE at a boundary
A threat model is not a list of scary words; it is the same six questions asked at every place data or
control crosses a trust boundary. STRIDE names them: **S**poofing (can someone pretend to be a
legitimate caller?), **T**ampering (can data be altered in transit or at rest?), **R**epudiation (could
an actor deny an action because nothing recorded it?), **I**nformation disclosure (can data reach
someone not entitled to it?), **D**enial of service (can the path be exhausted?) and **E**levation of
privilege (can a caller gain rights it was not given?). Take one boundary in a RAG agent, "retrieved
document text enters the model prompt": tampering is a planted instruction in a document (indirect
prompt injection); information disclosure is the model quoting a passage the user may not see;
elevation of privilege is the injected instruction causing a tool call with the agent's broader
credentials; repudiation is an action with no log tying it to a user and a source document. Each
answer points to a control, which is exactly what the reviewer wants to see written down. The method
works *because* boundaries are finite and enumerable, while "think of attacks" is not.

### The architecture half
An architecture review board asks questions a security reviewer may not: does the design use approved
technologies and integration patterns, or does it introduce a new database the customer's operations
team has never run? What are the availability target and the recovery point and time objectives, and
does the design meet them? Who operates it, and does its monitoring land in their tooling? What does it
cost at expected volume, and how does that scale? How would they leave: can data and indexes be
exported if the vendor is replaced? These questions exist *because* the customer will live with the
system long after the project team moves on, so fit with their estate matters as much as its
internal elegance.

### AI-specific questions to prepare for
Reviewers increasingly ask: can a document in the index instruct the model to leak other documents? Does
retrieval respect the user's permissions in the source system? Which tools can the agent call, with
whose credentials, and which actions need human approval? What happens when the model is wrong, and how
would you know? Can a user's data be deleted from indexes, caches and fine-tuned weights? Prepared
answers trace each risk to a control: permission-filtered retrieval, least-privilege tool credentials,
approval gates, output validation, evaluation results and monitoring.

### Your own reviews
Run the same process internally before the customer's: a design review with a peer architect and a
security engineer catches the obvious findings cheaply, and decisions are recorded as ADRs so the
reasoning survives staff changes.

## Example
An FDE prepares a bank's architecture review for an internal-policy assistant that retrieves from
SharePoint and can draft, but not send, emails. The pre-read has a container diagram, two data-flow
diagrams (indexing and query), an access matrix and a threat model. The review produces five findings:

1. **High:** the indexing service account can read all SharePoint sites, so retrieval would ignore
   per-user permissions. Fix before go-live: each chunk carries the source document's access list captured at
   index time, retrieval filters by the user's group memberships synced from the identity provider, and the indexer's scope is reduced to the approved site collections. The residual risk,
   noted in the finding, is staleness: a permission revoked in SharePoint still applies in the index
   until the next sync, so the sync interval becomes a documented, agreed number.
2. **High:** full prompts and retrieved passages are written to the vendor-hosted trace store. Fix before
   go-live: traces stay in the bank's log platform; vendor telemetry receives only metadata.
3. **Medium:** indirect prompt injection through a malicious document could make the assistant produce
   a misleading draft. Mitigation accepted: drafts are never auto-sent, the UI marks generated text, and
   red-team results on injection are attached as evidence.
4. **Medium:** no documented response if the model provider changes the model version. Fix by date:
   pin the model version and add a regression eval gate before upgrades.
5. **Low:** the internal ingress still accepts TLS 1.2 with legacy CBC-mode cipher suites, while the
   bank's standard requires AEAD-only suites. Risk accepted by the bank's security lead with a review in
   six months.

Two highs are closed within two weeks, with evidence (a permission-filter test showing user A cannot
retrieve user B's restricted file, and a log-flow screenshot). The board approves with conditions on
findings 4 and 5, tracked in the bank's risk register.

## Trade-offs
- Engaging reviewers early, at design stage, finds structural problems while they are cheap, but the design
  may change and need re-review; late engagement saves meetings but risks findings that force a redesign.
- A thorough pre-read shortens the meeting and the follow-up cycle, but it takes days to prepare; for a
  low-risk internal tool, a lightweight review with a short template is proportionate.
- Accepting risks with a named approver keeps launches moving, but accumulated accepted risks become
  invisible debt unless they carry expiry dates and are re-reviewed.
- Standard frameworks such as STRIDE and the OWASP LLM list give reviewers shared vocabulary, but
  checklists miss deployment-specific risks; the data-flow walkthrough is what finds those.

## Pitfalls
- Bringing a diagram that does not match production; the symptom is a reviewer asking about a service
  that the diagram omits, and the review losing confidence in everything else presented.
- Omitting the model provider and observability vendors from data flows; the symptom is a high-severity
  finding for undisclosed data transfer, often discovered from a network-egress question.
- Agreeing to findings without owners and dates; the symptom is the same findings reappearing at the
  next review, or blocking go-live at the last minute.
- Treating the review as a one-time gate; the symptom is a later feature, such as a new agent tool or a
  cross-region fallback, shipping without review and being flagged by an audit.
- Arguing every finding; the symptom is a reviewer escalating to a reject, when proposing a compensating
  control or a dated fix would have closed it.

## When to use
Expect a customer review for any system touching confidential or regulated data, integrating with
identity or internal systems, or taking actions. Run your own internal review for every new system and
for significant changes: new data sources, new tools for an agent, new model providers, new regions.
Scale the depth to data classification and blast radius. Measure time from intake to approval, the
number of high findings per review (it should fall as your template matures) and the percentage of
findings closed by their due date. In interviews, a strong candidate describes the pre-read package and
names AI-specific threats with concrete controls, rather than claiming the system is secure.

## Role lens
- **Engineer:** Produces the evidence reviewers ask for, such as a test proving permission-filtered retrieval
  and log configuration excerpts, and implements fixes against named findings.
- **Architect:** Owns the diagrams, data flows, threat model and ADRs, presents the design, and negotiates
  findings into fixes, mitigations or formally accepted risks with expiry dates.
- **FDE:** Navigates the customer's review process, books the right reviewers, translates their concerns into
  engineering tickets and keeps the findings tracker moving until approval.

## Check yourself
**Q:** In a customer's review, the security architect asks: "If someone uploads a document containing
instructions to the model, what stops your agent from emailing our customer list to an outside address?"
What is a strong answer?
**A:** Acknowledge that indirect prompt injection cannot be fully prevented at the model level, then show
layered controls that bound impact. The agent's email tool can only create drafts, so a human must send;
recipients are restricted to internal domains by the tool, not the prompt; the agent's credentials cannot
read the customer list because retrieval is filtered by the requesting user's permissions and the CRM is not
connected; outputs are validated and links stripped; and actions are logged for audit. Add evidence: red-team
results for injection attempts and the tool's permission policy. Then offer monitoring and an alert on
anomalous tool use. The key point is that safety rests on capability limits, not on the model obeying.

## Quiz
### Q: Which pre-read document do reviewers usually scrutinize most closely, and why?
- [ ] The product roadmap, because it shows future security features
- [x] The data-flow diagrams, because they show what sensitive data crosses which boundaries and where it is stored
- [ ] The penetration test report, because it proves there are no vulnerabilities
- [ ] The pricing sheet, because it determines the review tier
> Breaches and compliance problems follow data, so reviewers trace each sensitive flow across trust
> boundaries. A penetration test is useful evidence but shows only what testers found, not the absence of flaws.

### Q: A review finding says the indexing service account can read every SharePoint site. Why is this rated high for a RAG assistant?
- [ ] Because SharePoint APIs are rate-limited
- [ ] Because broad read access slows down indexing
- [ ] Because the account password might expire
- [x] Because without per-user permission filtering at retrieval, any user could receive content they are not entitled to read
> The assistant would answer from documents the requesting user cannot access, bypassing the source
> system's permissions. The fix is permission-aware retrieval and a narrower indexing scope, not tuning throughput.

### Q: A finding cannot be fixed before launch. What is the most appropriate way to proceed?
- [ ] Remove it from the findings list since it cannot be fixed now
- [ ] Launch and revisit it if an incident occurs
- [x] Record a risk acceptance or compensating control with a named approver and a review or fix date
- [ ] Ask the reviewer to downgrade the severity so it does not block
> Formal acceptance makes the decision visible and owned, and a date ensures it is revisited. Deleting or
> downgrading the finding hides the risk, which is what the review exists to prevent.
