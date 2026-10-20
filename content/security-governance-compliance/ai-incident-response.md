---
id: ai-incident-response
module: security-governance-compliance
name: AI incident response
kind: practice
difficulty: core
est_minutes: 16
related:
  - security-governance-compliance/audit-trails
  - security-governance-compliance/ai-risk-management
  - security-governance-compliance/compliance-architecture
  - delivery-ownership/incident-severity-and-escalation
  - delivery-ownership/root-cause-analysis-and-postmortems
  - llmops-platform/rollback
  - system-design/graceful-degradation
  - customer-data-evaluation/customer-specific-regression-suites
sources:
  - NIST SP 800-61 — Computer Security Incident Handling Guide
  - NIST, 2023 — AI Risk Management Framework (AI RMF 1.0)
  - Regulation (EU) 2016/679 — General Data Protection Regulation
---

## TL;DR
AI incident response is the prepared process for detecting, containing, investigating and learning from
AI-specific failures: harmful or false outputs at scale, data leakage through a model, prompt injection
that triggers actions, bias discovered in production, or a provider model change that silently alters
behaviour. It differs from ordinary incident response because the system is often "up" while causing
harm, there is no stack trace, and the fix may be a prompt, a data source or a vendor.

## Why it exists
Standard incident processes are triggered by error rates, latency and outages. Many AI incidents trip none
of those. A support assistant that starts quoting a wrong refund policy returns HTTP 200 in 800 ms. An
agent manipulated by text in a web page performs a perfectly valid API call. A model update from the
provider makes the classifier, say, 10 points worse on one language, and dashboards are green. The first signal
is often a screenshot on social media, a customer escalation or a journalist.

Teams without a plan improvise in exactly the wrong ways. They cannot switch the feature off quickly
because no kill switch exists. They patch the prompt live without capturing what the old one was, so the
investigation cannot reproduce the failure. They do not know which users received the bad outputs, so
they cannot notify them. They miss a legal clock: a personal data breach under GDPR must normally be
reported to the supervisory authority within 72 hours of becoming aware of it, and AI regulation adds its
own serious-incident reporting for some systems.

The naive alternative, "our SRE incident process covers it", is half right: severity levels, incident
commanders and postmortems carry over. What it lacks is AI-specific detection, containment levers,
evidence to preserve, and the people who must be involved (legal, privacy, responsible AI, the model
vendor).

## How it works
### Prepare
- **Taxonomy and severity**: define AI incident types (harmful output, factual error at scale, data
  exposure, security abuse such as injection or jailbreak, unfair outcome, unauthorized action, third-party
  model change) and severity criteria based on harm to people and the organization, not uptime.
- **Containment levers built in advance**, because the time to build a kill switch is not during the
  incident: feature flags to disable the AI path per tenant or globally; a degraded mode (canned answers,
  search results only, human queue); disabling individual tools for agents; pinning or rolling back the
  model and prompt version; pulling a document from the retrieval index.
- **Evidence**: audit trails and traces that record model version, prompt version, retrieved documents
  and tool calls per request, retained long enough to investigate.
- **Runbooks and contacts**: legal and privacy on call, the provider's security contact, a comms owner.

### Detect
Combine signals, *because* no single one is reliable: online evaluations on sampled traffic
(groundedness, policy violations), guardrail trigger rates, user feedback and complaint spikes, override
rates by human reviewers, injection detector hits, and eval deltas after any dependency change. Give staff
and customers an obvious way to report a bad output.

### Contain
Stop the harm first, diagnose second. Use the narrowest lever that stops it: disable one tool before the
whole agent, one tenant before all tenants, one document before the index. Preserve state before changing
it: snapshot the current prompt, config and model version so the failure can be reproduced.

### Investigate
Reconstruct from the evidence: which requests were affected (scope), since when (onset, often correlated
with a deploy, index refresh or provider update), and why (root cause). AI root causes typically live in
one of: input data, retrieved content, prompt or config, model version, tool permissions, or user
behaviour. Reproduce with the recorded versions; because outputs are stochastic, measure a failure rate
over many runs instead of trusting one reproduction.

### Remediate and notify
Fix the cause, add the failing cases to the regression suite so it cannot silently return, and assess
notification duties with legal: affected users, customers under contract, data protection authorities,
and regulators where AI-specific reporting applies.

### Learn
A blameless postmortem that updates the risk register (likelihood was higher than estimated), adds or
changes key risk indicators, and checks whether the containment lever worked fast enough.

## Example
A worked timeline for an HR policy assistant used by 8,000 employees (times and counts illustrative).

**09:10** Two employees report the assistant says parental leave is 6 weeks; current policy is 16. No
alerts fired: latency and error rate normal.

**09:25** On-call classifies it as factual error at scale on a policy that affects employment decisions:
high severity. Incident commander assigned; HR policy owner and legal paged.

**09:32** Containment: the "leave policy" intent is switched by feature flag to a degraded mode that
returns a link to the policy page plus a human contact. The rest of the assistant stays up. Current prompt
version, index version and model version are snapshotted.

**10:40** Investigation via audit trail: answers citing document `leave-policy-2019.pdf` started after
Monday's index refresh, which re-ingested an archived folder. 312 conversations since Monday retrieved
that document; 187 of them produced the wrong figure (measured by re-running the groundedness check on
the logged outputs).

**11:30** Remediation: archived folder excluded at ingestion, document removed from the index, an expiry
metadata filter added. The 187 affected employees are identified from the trail and HR sends a correction.
Legal confirms no personal data breach occurred, so no regulator notification.

**Next day** Twenty leave-policy questions added to the regression suite, which now runs after every index
refresh. Postmortem action: ingestion requires a source allow-list. Risk register likelihood for "stale
policy retrieved" raised; new KRI: share of answers citing documents older than policy effective dates.

## Trade-offs
- Broad kill switches contain any incident in seconds but take down useful functionality; finer-grained
  levers per tool, intent or tenant cost more to build and pay off once the system has many users.
- Retaining full prompts and outputs makes scoping affected users exact, but expands the sensitive data you
  hold; hashed references plus a shorter full-content window is a common middle ground.
- Rolling back to the previous model or prompt is fast, but unavailable when the provider has retired the
  old version; contracts with deprecation notice and pinned versions keep that option open.
- Low thresholds on output monitors catch incidents early but page people for noise; tune on labelled
  history and route low-confidence signals to review queues, not pagers.

## Pitfalls
- Hot-fixing the prompt in production without snapshotting the old one; the symptom is a postmortem that
  cannot reproduce or explain the original failure.
- Relying on uptime and error-rate alerts; the symptom is that customers or the press detect the incident
  before the team does.
- Reproducing once and declaring the fix works; the symptom is the same failure reappearing at a lower but
  non-zero rate, because outputs are stochastic.
- No way to identify affected users; the symptom is a blanket notification to everyone, or none at all.
- Leaving legal and privacy out until the end; the symptom is a missed notification deadline.

## When to use
Every production AI system needs at least a kill switch, a degraded mode, an incident category in the
on-call process and request-level evidence. Systems in high-risk tiers, with tools that act, or handling
regulated data need full runbooks, tabletop exercises (for example, a simulated prompt injection that
triggers an action), and pre-agreed notification paths. Measure time to detect, time to contain, and the
share of incidents first reported externally. In interviews, the signal is "contain before diagnose", the
specific levers, and treating stochastic reproduction with rates rather than single runs.

## Role lens
- **Engineer:** Builds the feature flags, degraded modes, per-tool disable switches and version pinning
  that make containment a one-click action, and keeps traces rich enough to scope impact.
- **Architect:** Designs containment granularity, evidence retention and dependency rollback paths into the
  system, and ensures vendor contracts preserve the ability to pin or roll back models.
- **FDE:** Agrees with the customer who gets called, which lever they can pull themselves, and how their
  notification duties interact with yours before the first incident happens.

## Check yourself
**Q:** Monday morning your agent's refusal rate on a customer's workload jumps and some answers become
noticeably wrong. You deployed nothing over the weekend. How do you approach it?
**A:** First contain if users are harmed: switch affected intents to a degraded mode or route to humans.
Then look for what changed outside our deploys: a provider model update behind an alias, an index refresh
or new documents, a config or guardrail policy change, or a shift in user inputs. The audit trail's
model version field tells me quickly whether the provider changed. I would run our regression suite against
current and previous configurations and compare failure rates over many runs. If it is the provider, pin
the previous version if available, open a vendor ticket, and add the cases to the regression suite.

## Quiz
### Q: Why do many AI incidents go undetected by standard SRE alerting?
- [ ] AI systems do not emit metrics
- [x] The system can return fast, successful responses while the content of those responses is harmful or wrong
- [ ] Model providers hide their error rates from customers
- [ ] AI incidents only happen during deploys, when alerts are suppressed
> Latency and error-rate alerts measure availability, not correctness. A wrong or harmful answer is a
> successful HTTP response, so detection needs content-level signals such as online evals and feedback.

### Q: During containment, what should you do before changing the live prompt or configuration?
- [ ] Notify every user of the system
- [ ] Retrain the model on the failing examples
- [x] Snapshot the current prompt, configuration, index and model versions so the failure can be reproduced
- [ ] Delete the logs of affected conversations to limit exposure
> Fixing first destroys the state you need to understand the root cause. Preserving versions lets the
> investigation reproduce the failure; deleting logs would destroy evidence and could breach obligations.

### Q: A fix is applied and one re-run of the failing request now produces a correct answer. What is the main risk in closing the incident?
- [ ] One re-run proves the fix works, so there is no risk
- [x] Outputs are stochastic, so the failure may persist at a lower rate; measure it over many runs and add regression cases
- [ ] The fix must be approved by the model provider first
- [ ] Re-running requests violates audit trail integrity
> A single success is weak evidence for a probabilistic system. Measuring the failure rate across many runs
> and pinning the cases in a regression suite shows the fix works and keeps it from silently regressing.
