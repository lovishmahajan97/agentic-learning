---
id: data-residency
module: security-governance-compliance
name: Data residency
kind: concept
difficulty: core
est_minutes: 15
related:
  - security-governance-compliance/data-classification
  - security-governance-compliance/pii-protection-dlp
  - deployment/compliance-constraints
  - deployment/customer-vpc-on-prem-air-gapped
  - system-design/self-host-vs-api
  - system-design/ai-gateway
  - data-architecture/data-retention-and-deletion-architecture
  - security-governance-compliance/vendor-exit-strategy
sources:
  - Regulation (EU) 2016/679 — General Data Protection Regulation (Chapter V, transfers)
  - CJEU, 2020 — Data Protection Commissioner v Facebook Ireland and Maximillian Schrems (Schrems II)
  - European Data Protection Board, 2020 — Recommendations 01/2020 on supplementary transfer measures
---

## TL;DR
Data residency is the requirement that data be stored and processed only in approved locations, set by
law, regulators or contracts. In AI systems it is harder than "pick an EU region", because a single
request can touch a model endpoint, provider-side logs and abuse monitoring, caches, vector stores,
traces and support staff, each with its own location. Residency is a property of the whole data flow,
and it is distinct from sovereignty, which is about whose law can compel access.

## Why it exists
Governments and regulators want personal and sensitive data to stay within reach of their own law. The
EU's GDPR does not, in general, require data to stay in the EU; it restricts *transfers* outside the
European Economic Area unless the destination has an adequacy decision or safeguards such as standard
contractual clauses are in place, with supplementary measures where the destination's law undermines
them. The Schrems II judgment in 2020 invalidated the EU-US Privacy Shield on exactly that ground, which
showed organisations that a transfer mechanism can disappear overnight. Other jurisdictions go further
with localisation rules that require certain data to stay in-country, and sectors such as banking,
health and public administration often add their own requirements. Many enterprise contracts simply say
"customer data stays in region X" regardless of what the law allows.

The naive answer is to deploy the application in the right cloud region. That covers the database and
the app servers, which in an AI product are a minority of the places data goes. The prompt is sent to a
model endpoint that may route to other regions for capacity. The provider may retain prompts for abuse
monitoring. Your observability vendor stores full traces in its default region. Embeddings sit in a
managed vector database. A fine-tuning job copies data to wherever training capacity is. Support
engineers in another country can read production logs. Each hop is a potential transfer, and missing one
puts the whole product out of compliance for every customer who required residency.

## How it works
### Separate three ideas
- **Residency:** where data is stored and processed. A technical and contractual property you can design.
- **Localisation:** a legal mandate that certain data must remain in a country. A hard constraint.
- **Sovereignty:** which jurisdiction's law governs the data and can compel access. A provider
  headquartered in one country may be subject to its laws (the US CLOUD Act is the usual example) even
  for data stored elsewhere, so residency alone does not settle sovereignty concerns.

Customers often say "residency" when they mean one of the others; ask which.

### Map every place data exists
For an LLM feature, list data at rest and in processing:
1. Inference: the model endpoint's processing location, and whether the provider routes across regions
   (global or cross-region routing modes trade residency for capacity).
2. Provider-side retention: abuse-monitoring logs, stored conversations, batch files, and whether a
   zero-retention or regional-processing option exists under your contract.
3. Your own stores: primary database, vector index, caches including prompt and semantic caches, agent
   memory, object storage for uploads.
4. Telemetry: traces, prompt logs, evaluation datasets, error reports, analytics.
5. Derived assets: embeddings, fine-tuned weights, summaries, all of which carry the source data's
   obligations.
6. Human access: support, on-call and labelling staff, *because* remote access from another country
   can itself count as a transfer.
7. Backups and disaster recovery replicas, which are frequently configured cross-region by default.

### Design the controls
- **Regional cells.** Deploy a full stack per region (app, stores, telemetry) and pin each tenant to a
  home region, *because* shared global services are where residency leaks hide.
- **Region-aware gateway.** The AI gateway reads the tenant's region and the request's data class and
  routes only to endpoints approved for that combination, failing closed rather than spilling over to
  another region when capacity is short.
- **Provider terms.** Choose endpoints and contract options that pin processing and retention to the
  region; vendor offerings change, so verify current documentation and contract language, not marketing
  pages.
- **Self-hosted or in-customer deployment** when no approved managed endpoint exists or sovereignty
  concerns rule out a foreign provider.
- **Encryption with customer-held keys** reduces sovereignty exposure for data at rest but does not help
  during inference, where the model needs plaintext.

### Evidence
Auditors and customers ask for a data-flow map, sub-processor list with locations, and proof that
routing and retention settings are enforced. Generate this from configuration, not from a slide.

## Example
A German health-insurance customer requires that member data be stored and processed only in the EU, and
that the provider not retain prompts. The vendor's first draft architecture:

| Component | Initial location | Issue |
|---|---|---|
| App and Postgres | EU region | fine |
| Model endpoint | provider global endpoint | may process outside the EU |
| Provider abuse logs | default retention | prompts retained, location unclear |
| Vector DB (managed) | EU region | fine |
| Tracing SaaS | US default region | full prompts with member data in the US |
| On-call engineers | India and US | production log access is a transfer |
| Backups | cross-region to US | replicas of everything |

Fixes, one per row: switch to the provider's EU-processing endpoint under a contract with zero data
retention, or self-host an open-weight model in the EU region if no such option is available; move
tracing to an EU instance and redact member identifiers before export; restrict production data access
to EU-based staff, with others seeing only redacted telemetry; change backups to a second EU region.
Then make the gateway fail closed: if the EU endpoint is throttled, return an error or queue the request
instead of falling back to the global endpoint. That last rule matters in practice, because a fallback
added during an incident is the most common way a compliant design silently stops being compliant.

## Trade-offs
- Regional cells give clean residency guarantees but multiply operational cost and split capacity, which
  is worth paying once regulated customers in that region justify a dedicated stack.
- Failing closed preserves compliance during provider incidents at the cost of availability; a
  cross-region fallback is acceptable only for data classes and tenants with no residency requirement.
- Regional model endpoints may lag global ones in model availability or capacity, so residency can cost
  access to the newest model; self-hosting fixes availability but moves the quality and operations burden
  to you.
- Customer-managed keys narrow sovereignty exposure for stored data but add key-management failure modes
  and do nothing for plaintext during inference.
- Restricting human access to in-region staff satisfies auditors but weakens follow-the-sun support,
  which matters most for products sold with tight incident response commitments.

## Pitfalls
- Checking only the application region; the symptom is a security questionnaire answered "EU only" while
  the tracing vendor's invoice shows a US data centre.
- Adding a cross-region fallback during an outage and never removing it; residency silently breaks
  until an audit or a customer's own monitoring finds it.
- Treating embeddings as anonymous; they are derived from personal data and can leak it, so they carry
  the same residency obligation as the source text.
- Assuming the provider's regional endpoint also keeps abuse-monitoring logs in region; retention and
  processing locations can differ and must be confirmed separately in the contract.
- Confusing residency with sovereignty and promising a customer that EU storage protects them from a
  foreign government's access request, which it may not.

## When to use
Raise residency in the first scoping call with any regulated, public-sector or EU and other non-US
enterprise customer, *because* it can rule out the default model provider entirely and change the
architecture. If no customer, law or contract requires it, a single-region deployment with documented
sub-processors is enough. When it applies, produce the full data-flow map, choose between regional
managed endpoints, regional cells and self-hosting, and make the gateway fail closed. Measure the share of
requests served by approved regional endpoints and alert on any cross-region call. Interviewers look for
the full list of hidden data locations and the residency versus sovereignty distinction.

## Role lens
- **Engineer:** Implements region-pinned routing in the gateway, fail-closed behaviour, in-region telemetry
  and backup configuration, and tests that no fallback crosses regions.
- **Architect:** Chooses between regional cells, regional managed endpoints and self-hosting, and owns
  the data-flow map and sub-processor list as living artifacts.
- **FDE:** Asks whether the customer means residency, localisation or sovereignty, and answers their
  questionnaire with evidence drawn from actual configuration.

## Check yourself
**Q:** A customer requires EU residency. Your app, database and vector store are in an EU region, and you
call a frontier model through its EU endpoint. Is the product compliant? What else would you check?
**A:** Not necessarily; those are three of many locations. I would check whether the endpoint guarantees
EU processing or can route globally under load, where provider-side abuse logs and stored files are
retained and for how long, where traces, prompt logs and eval datasets live, whether backups or DR
replicas cross regions, where caches and agent memory sit, which staff in which countries can access
production data, and whether any fallback path calls a non-EU endpoint. I would also ask whether the
real concern is sovereignty, since an EU-located service run by a non-EU provider may not satisfy it.

## Quiz
### Q: Under GDPR, what is the accurate characterisation of its rules on data location?
- [ ] Personal data of EU residents must always be stored inside the EU
- [x] Transfers outside the EEA are restricted and need an adequacy decision or appropriate safeguards
- [ ] Data may go anywhere as long as it is encrypted in transit
- [ ] Only health and financial data is subject to location rules
> GDPR's Chapter V governs transfers rather than mandating EU storage; transfers need an adequacy
> decision or safeguards such as SCCs. Strict in-country localisation comes from other laws or contracts.

### Q: The EU model endpoint is rate-limited during an incident. What should a residency-compliant gateway do for a tenant that requires EU processing?
- [ ] Fall back to the provider's global endpoint and log the event for later review
- [ ] Fall back to a different provider's US endpoint with stronger capacity
- [x] Fail closed: queue, degrade or return an error rather than route outside the approved region
- [ ] Strip obvious identifiers and route to any available region
> Residency is a hard constraint for that tenant; any out-of-region call is a breach, logged or not.
> Redacting identifiers does not make free text anonymous, so the safe behaviour is to fail closed.

### Q: Why does storing data in an EU region not by itself answer a sovereignty concern?
- [ ] EU regions are physically located outside the EU for redundancy
- [ ] Sovereignty only concerns encryption algorithms, not location
- [ ] Data residency and sovereignty are legally identical concepts
- [x] A provider may be subject to its home country's laws compelling access regardless of where data sits
> Sovereignty is about which jurisdiction can compel access. A provider incorporated elsewhere may face
> its home country's legal orders for data stored in the EU, so location alone does not settle it.
