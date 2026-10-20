---
id: safety-security
title: Safety & security
---

## Overview
This module covers how to keep an LLM system from being turned against its users, its operator or its
data. It starts from one premise that every lesson returns to: the model is an untrusted component that
follows instructions wherever it finds them, and a system prompt is an instruction, not an enforcement
boundary. The OWASP Top 10 for LLM Applications gives the shared vocabulary. Prompt injection and RAG
poisoning explain how untrusted text, typed by a user or planted in a document, web page or corpus,
reaches the model's single input stream. The checkpoint layer comes next: input and output guardrails,
output validation that turns strings into typed data, content moderation with per-surface thresholds,
and two concrete tools, Llama Guard as a policy-in-the-prompt safety classifier and NeMo Guardrails as an
orchestrator of rails. The data lessons (PII redaction, data leakage, cross-tenant leakage) map every
place an LLM pipeline copies text: prompts, traces, caches, embeddings, memory and weights. The action
lessons (excessive agency, tool privilege escalation) cover agents, where a wrong token becomes a real
side effect. Supply-chain security closes the module by treating checkpoints and datasets as build
artifacts that can run code or carry backdoors. Afterwards you can threat-model an LLM feature, choose
and measure guardrails, design tenant isolation and redaction per boundary, scope agent authority, and
answer an enterprise security review item by item. It matters most for Engineers who build these layers
and Architects who own the trust boundaries, and FDEs need it to pass customer security reviews.

## Why it matters
- **Engineer:** You write the code that validates model output with Pydantic, threads a server-derived tenant ID into every vector, cache and memory call, redacts traces, runs moderation and Llama Guard checks, and executes tools with the requesting user's credentials instead of the service's, and each of these needs labelled tests and a tracked false-positive rate.
- **Architect:** You decide where the trust boundaries sit, which workflows combine private data, untrusted content and outbound capability, which tools are tiered as irreversible and gated, how tenants are isolated across every store, and which models and datasets may enter the registry; those choices, not the prompt, set the blast radius.
- **FDE:** Enterprise buyers send security questionnaires keyed to OWASP LLM items, ask where their data goes and whether another customer could see it, and want their own moderation policy; you need a threat model, a data-flow map and red-team evidence you can show, and the ability to tune rails per deployment.

## Mental model
Picture the model as a powerful but gullible deputy sitting in the middle of a building with many doors.
Text comes in through several doors (the user, retrieved chunks, tool results, downloaded checkpoints),
and all of it lands in the same ear; the deputy cannot tell instructions from data. Things go out through
other doors: answers shown to people, tool calls that change the world, and copies of the text written
to logs, caches, indexes and weights. Security means guarding the doors, not lecturing the deputy. At the
inbound doors you control provenance, trust tiers, redaction and injection screening. At the outbound
doors you validate structure and meaning, moderate content, scan for leaks and enforce tenancy. At the
action doors you give the deputy only the keys for this task and this user, checked by the resource,
with a human at the irreversible ones. Then you prove each guard works with labelled positives,
negatives and red-team cases, because a guard nobody has measured is not a control.

## Suggested order
1. owasp-top-10-for-llms — the intro-level map and shared vocabulary that every later lesson slots into.
2. prompt-injection — the root threat: one input stream with no code/data separation, and the lethal trifecta that turns it into an incident.
3. rag-poisoning — extends injection to the corpus, showing why write-path provenance and trust tiers beat prompt-side defences.
4. input-output-guardrails — introduces the deterministic checkpoint layer and the measure-before-enforce discipline the next lessons build on.
5. output-validation — the most mechanical output rail: syntactic, structural and semantic validity of anything code consumes.
6. content-moderation — classification against a harm taxonomy with per-surface thresholds, actions and review metrics.
7. llama-guard — a concrete open-weight moderation classifier with policy in the prompt, best read once moderation itself is clear.
8. nemo-guardrails — a toolkit that composes rails and classifiers such as Llama Guard, so it comes after the pieces it orchestrates.
9. pii-redaction — the first data control: typed, reversible pseudonymisation applied per boundary.
10. data-leakage — widens from PII to every channel data can leave by: provider, tools, rendering, logs, embeddings and weights.
11. cross-tenant-data-leakage — the multi-tenant special case where vector stores, caches and memory bypass row-level security.
12. excessive-agency — moves from words to actions, shrinking tools, permissions and autonomy to bound the blast radius.
13. tool-privilege-escalation — the confused-deputy deep dive: every tool call must carry only the asking principal's authority.
14. supply-chain-security-for-models-and-datasets — finishes below the application, treating checkpoints and datasets as signed, scanned build artifacts.

## Capstone
### Brief
Secure a multi-tenant customer-support copilot before its first enterprise launch. The copilot answers
questions from a RAG index built from each customer's help-centre articles, inbound support tickets and a
shared product wiki, and it has three tools: look up an order, issue a refund up to a limit, and email the
end customer. It runs on a hosted LLM, uses an open-weight embedding model pulled from a public hub, keeps
per-conversation memory and a semantic cache, and ships every prompt to a tracing vendor. Over a week,
write an OWASP-structured threat model of the system, then build the controls the top rows call for:
injection and hidden-text handling at ingestion with per-source trust tiers; input and output rails with
Pydantic-validated tool arguments; moderation using Llama Guard with a custom category set, composed
with a topical rail in NeMo Guardrails or an equivalent pipeline; PII pseudonymisation before the
provider and irreversible redaction in traces; one server-derived tenant ID enforced on the vector store,
cache and memory; refunds and outbound email executed with the end user's scopes and gated by human
approval above a threshold; and a pinned, hashed, safe-format embedding model loaded without remote code.
Finish with a red-team run that tries to break each control.

### Deliverables
- A threat model with one row per OWASP LLM item, each naming the affected component, the control, the test that proves it and the residual risk, plus a data-flow diagram showing every store and egress point.
- Working code for the guardrail pipeline: ingestion sanitiser with provenance metadata, input and output rails, Pydantic schemas for every tool argument, Llama Guard moderation with per-rail thresholds, and a tool executor that attaches the requesting user's credential and an approval gate.
- A tenant-isolation module with a single tenant-context wrapper used by every vector, cache and memory call, and a CI suite of cross-tenant canary tests across at least three tenant pairs.
- A PII and leakage policy listing each boundary (provider, traces, index, cache, output, email tool) with the entity list and transformation applied, plus a recall measurement per entity on a labelled sample.
- A model supply-chain record for the embedding model: source revision pinned by commit, file hashes, format and scan results, licence, and the registry entry production loads from.
- A red-team report of at least 25 attacks (direct and indirect injection, poisoned wiki page, system-prompt extraction, cross-tenant probes, refund escalation, exfiltration through a rendered link or email) with outcomes and fixes.

### Rubric
- A poisoned wiki page with a hidden instruction to email order data to an external address is either quarantined at ingestion or, if retrieved, results in no outbound email, and the block appears in the decision log with its source and trust tier.
- Every cross-tenant canary test passes in CI, and grepping the codebase shows no vector, cache or memory call that bypasses the tenant-context wrapper.
- Each rail reports block rate on an adversarial set and false-refusal rate on a benign set of at least 100 examples each, and its thresholds are justified from those numbers rather than defaults.
- A refund above the threshold cannot execute without a recorded human approval, and a refund requested by an injected document fails at the resource layer because the call carries the end user's scopes, not a service account's.
- Sampled traces contain typed placeholders instead of raw emails, phone numbers and order addresses, and measured redaction recall per entity is reported next to the target.
- Production loads the embedding model only from the internal registry by hash, in a non-pickle format with remote code disabled, and swapping a tampered file makes the load fail.
