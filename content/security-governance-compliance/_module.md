---
id: security-governance-compliance
title: Security, governance & compliance
---

## Overview
This module is about making an AI system safe to run, defensible to an auditor and acceptable to an
enterprise buyer. It starts with security. AI security is ordinary application security plus one new
component: a model that follows instructions found in its data. Threat modeling draws the trust
boundaries a classical review misses, where untrusted text flows into context and model output flows
into actions. Prompt injection defence turns that diagram into deterministic enforcement outside the
model, using the rule of two. Zero trust makes every tool call a request to verify, with delegated,
scoped, short-lived credentials. The data group comes next. Classification labels data so controls are
decided once per label and inherited by chunks, embeddings and weights. PII protection and DLP decide,
per data class and destination, whether to allow, redact, tokenize or block. Residency maps every place a
request's data actually goes. Governance follows: an inventory of every AI use, a risk tier per use case,
a risk register, responsible AI requirements, a model registry enforced at the gateway, model cards, and
audit trails that can reconstruct a decision months later. The module closes with operations and
assurance: AI incident response with kill switches and legal clocks, a tested vendor exit plan, and a
compliance architecture that maps one control set to GDPR, SOC 2, HIPAA, ISO/IEC 42001, the EU AI Act
and the DPDP Act. Afterwards you can threat-model an agent, put a policy gateway in front of it, classify
and govern its data, and hand an auditor evidence instead of policy documents. It matters most for
Architects and FDEs, who face these questions in design reviews and security questionnaires, and for
Engineers who build the enforcement points.

## Why it matters
- **Engineer:** You build the enforcement points that make policy real: the tool dispatcher that checks the rule of two, the gateway that redacts PII and refuses unregistered models or out-of-region endpoints, the scoped token exchange, the hash-chained audit log and the kill switch, so a prompt injection or a leaked card number is a bug in your code path.
- **Architect:** You decide where the trust boundaries sit, which data classes may reach which model and region, how labels propagate into vectors and logs, what the registry and audit schema look like, and how one control set maps to several frameworks; retrofitting residency, deletion or provenance fields later costs far more than designing them in.
- **FDE:** Enterprise customers gate deployment on security questionnaires, data residency, DPAs, model documentation and an incident plan; you answer from a threat model, a data-flow map, model and system cards and automatically collected evidence, and you raise residency and risk tier in the first scoping call because they can rule out the default provider.

## Mental model
Picture two planes around every AI system. The runtime plane is a single chokepoint, a policy gateway and
tool dispatcher, that sits between the model and everything it can read, call or send. The model is
treated as an untrusted interpreter: whatever it proposes, the gateway checks identity, delegation, data
label, destination, region, model approval and the rule of two in code, then allows, redacts, reroutes,
asks a human or denies, and writes an audit event either way. The governance plane decides what rules
that gateway enforces. It starts from the inventory: every AI use case, tiered by what decisions it
informs and about whom. The tier sets how much risk assessment, fairness evaluation, documentation,
validation and oversight the use case needs, and those decisions are recorded in the registry, the risk
register and the model card. The two planes meet in evidence. The audit trail and gateway metrics prove
the controls ran, incident response uses them to contain and reconstruct, and compliance becomes mapping
one set of enforced, evidenced controls onto each framework a customer or regulator brings.

## Suggested order
1. ai-security — the map of what changes when a model enters the system, used to decide which later lessons apply to a design.
2. threat-modeling — draws the data-flow diagram and trust boundaries every later control is chosen against.
3. prompt-injection-defense — turns the content sources and action sinks from the threat model into enforcement outside the model.
4. zero-trust — gives each tool call delegated, scoped, short-lived credentials checked at a PEP, the identity half of injection defence.
5. data-classification — labels data and its derived artifacts so controls are decided per label, which DLP and residency both key on.
6. pii-protection-dlp — applies per-label, per-destination actions to personal data flowing into prompts, logs, indexes and weights.
7. data-residency — extends the data-flow map to the locations of every endpoint, log, cache and support path a request touches.
8. ai-inventory-and-use-case-risk-classification — builds the list of AI uses and the risk tiers that make all governance proportionate.
9. ai-risk-management — runs the map, measure and manage loop per inventory entry, with owners, appetite and residual risk.
10. responsible-ai — turns fairness, transparency and oversight into testable requirements for the high-tier use cases the risk loop flags.
11. model-governance — enforces which models are approved for which use through a registry and the gateway, using the tiers and risk decisions above.
12. model-cards — documents intended use, out-of-scope uses and disaggregated results as a release gate in the governance flow.
13. audit-trails — records prompts, versions, retrieved context, tool calls and gateway decisions so any output can be reconstructed and defended.
14. ai-incident-response — uses kill switches, degraded modes and the audit trail to contain AI failures that never trip an error-rate alert.
15. vendor-exit-strategy — measures how quickly and safely you can leave a provider, drawing on governance, evals and data ownership.
16. compliance-architecture — maps the whole control set and its evidence onto GDPR, SOC 2, HIPAA, ISO/IEC 42001, the EU AI Act and DPDP, so it comes last.

## Capstone
### Brief
You are the FDE and architect for an AI assistant being rolled out to a mid-sized EU insurer. Claims
handlers use it to summarise claim files (PDFs and emails from policyholders, which carry health and
financial data), answer questions from internal policy documents, draft emails to claimants, and
recommend whether a claim needs a fraud review. The customer's security team will not sign off until
you pass their review. Over a week, take an existing prototype (a model API behind a small web app, with
local mocks for the claims system, document store and email) and make it reviewable. Threat-model it
with trust boundaries around claimant content and the email and claims-update sinks. Put one policy
gateway in front of the model and every tool that enforces the rule of two, delegated per-user tokens,
data labels, PII redaction per destination, EU-only endpoints that fail closed, and a model registry.
Inventory and tier the four use cases (the fraud recommendation is the high-risk one), write the risk
register, a fairness evaluation and a system card for it, add a hash-chained audit trail, rehearse an
incident, write a vendor exit plan, and assemble an evidence pack mapped to GDPR, the EU AI Act and SOC 2.

### Deliverables
- A threat model: a data-flow diagram with trust boundaries, every content source and action sink marked, a STRIDE-based threat register, and a verified test linked to each high-severity threat.
- A policy gateway and tool dispatcher in code, enforcing per-user delegated scopes, the rule of two with human approval for outbound email after reading claimant content, label-based PII redaction or blocking per destination, EU-region endpoints that fail closed, and refusal of unregistered models.
- A data map and classification table covering every store and derived artifact (chunks, embeddings, traces, caches, eval sets) with its label, region, retention and deletion path, plus a DLP precision and recall measurement on a labelled sample.
- An AI inventory with risk tiers and rationale for all four use cases, a risk register with owners, KRIs and residual risk, a fairness evaluation of the fraud-review recommendation by relevant groups, and a versioned model or system card with out-of-scope uses.
- A hash-chained audit trail capturing user, prompt template version, model version, retrieved chunk ids, tool calls and gateway decisions, with a verification script and a reconstruction query.
- An incident runbook with a kill switch and degraded mode, a tabletop write-up of an injected claimant email that tries to forward a claim file, a vendor exit plan with measured eval deltas on an alternative model, and a compliance evidence matrix mapping each control to GDPR, the EU AI Act and SOC 2 with links to automatically collected evidence.

### Rubric
- A claimant PDF containing an injected instruction to email the full claim file externally results in no email being sent without approval, and the audit log shows the denied or pending call with the rule that fired.
- Switching the configured model endpoint to a non-EU region or an unregistered model makes requests fail closed with a logged denial, not a silent fallback.
- A request made as a handler without access to a given claim cannot retrieve that claim's documents through the assistant, shown by a PEP denial tied to the user's delegated token.
- Running the verification script after manually editing one audit record reports the exact broken link, and the reconstruction query returns the model version, prompt version and chunk ids for a chosen past recommendation.
- Every inventory entry has a tier, owner and review date, and the high-risk fraud recommendation has a card with disaggregated results and a named human oversight step, while low-tier uses carry visibly lighter documentation.
- The evidence matrix links each control to an artifact generated by the system (gateway metrics, audit samples, test runs), and a deletion request for one claimant is shown removing their data from the document store, vector index, traces and caches.
