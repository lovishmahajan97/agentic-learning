---
id: per-customer-guardrails
module: customer-data-evaluation
name: Per-customer guardrails
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
related:
  - safety-security/input-output-guardrails
  - safety-security/content-moderation
  - safety-security/pii-redaction
  - enterprise-integration/tenant-specific-configuration-and-feature-flags
  - agent-runtime-architecture/policy-enforcement-across-agents-and-tools
  - system-design/tenant-isolation
  - customer-data-evaluation/customer-specific-regression-suites
  - safety-security/nemo-guardrails
---

## TL;DR
Per-customer guardrails are input, output and action policies resolved per tenant at request time: a
platform safety floor nobody can lower, plus each customer's own rules and thresholds layered on top.
They exist because one global policy is too strict for some customers and too loose for others. The
rules live in versioned config, are enforced in code rather than in the prompt, and each customer's
rules are tested on that customer's own labelled data.

## Why it exists
Customers disagree, legitimately, about what the system may do. A bank forbids anything that reads like
investment advice. A pharmaceutical company needs drug dosages discussed freely, which a generic medical
safety filter would block. A retailer bans mentions of competitors. A hospital needs PHI redacted in
logs but not in the clinician-facing answer. One customer allows the agent to issue refunds up to 50
units of currency, another forbids refunds altogether.

The naive alternatives fail in predictable ways. **One global policy** has to be either the union of
everyone's restrictions (so the pharma customer's core use case is blocked) or the intersection (so the
bank's rule is missing). **Writing each customer's rules into their system prompt** ("never mention
competitors") feels configurable, but a prompt instruction is a request to the model, not an
enforcement point: a user can talk the model out of it, a model upgrade can change how strictly it
follows it, and there is no log of when it fired. **Forking code per customer** enforces rules but turns
every platform change into N merges, and the forks drift until nobody knows which customer has which
fix.

Per-customer guardrails make policy data: one enforcement engine, many policies, each policy versioned,
tested and auditable per tenant.

## How it works
### Layers, and why the floor only tightens
Policy is resolved from layers, most general first:
1. **Platform baseline.** Non-negotiable rules: illegal content, credential leakage, cross-tenant data,
   prompt-injection defences. No customer can relax them, because the platform's own legal and
   reputational risk does not change per contract.
2. **Customer layer.** Set by the customer (often through their admin console) within an allowed
   envelope: blocked topics, PII entity types to redact, allowed languages, action limits, classifier
   thresholds.
3. **Deployment or use-case layer.** A customer's HR assistant and its sales assistant need different
   rules, so policy can narrow further per deployment.

Merging is monotonic toward safety: deny wins over allow, numeric limits take the minimum, allowlists
intersect, blocklists union. A *relaxation* (the pharma customer allowing dosage discussion) is not a
lower layer overriding a higher one; it is an explicit, platform-approved exception declared in the
envelope, so it can be reviewed and audited.

### Resolution comes from identity, not the request
The tenant and deployment are taken from the authenticated context (API key, SSO token, service
identity), never from a field in the request body or the prompt. Otherwise a caller can choose the
loosest policy by claiming to be another tenant.

### Enforcement points
The resolved policy is applied wherever risk enters or leaves:
- **Input:** topic and injection classifiers, size limits, language checks.
- **Retrieval:** ACL and document-scope filters, so the model never sees what the user may not.
- **Tool calls:** argument validation against per-tenant limits (refund ceiling, allowed recipients),
  checked in the tool adapter before execution, because that is the last point you control.
- **Output:** PII redaction, banned-term and topic checks, format validation, citation requirements.

### Thresholds are per customer too
Classifier-based guards output a score; the threshold that turns a score into a block decides the
trade-off between false blocks and misses. That trade-off depends on the customer's traffic and risk
appetite, so thresholds are calibrated on each customer's labelled sample (from their SMEs), and the
false-positive rate is monitored in production. A threshold copied from another customer is a guess.

The calibration procedure is ordinary operating-point selection. Score the customer's labelled sample,
then pick the lowest threshold that meets their stated tolerance on the side they care about most,
usually misses: "catch at least 95% of real investment-advice responses". Read off the false-block rate
that threshold implies and show it to the customer, because that number is the cost they are accepting.
Two cautions. Violations are rare, so a 1,000-case sample may hold only 20 positives, and a recall
estimate from 20 positives moves 5 points per case; enrich the sample with known violations before
trusting it. And the operating point drifts when the customer's traffic changes, so re-check it on fresh
labelled samples rather than treating it as set-and-forget.

### Fail-open or fail-closed
Guards are services that time out and fail. What happens then is itself per-customer policy: a
regulated tenant's output check fails closed (return a safe refusal or route to a human), while a
low-risk internal tool may fail open with the event logged and alerted. Leaving it to whatever the
exception handler happens to do means the guardrail is silently off during exactly the incidents when
load is highest. Streaming output raises the same question at a finer grain: either buffer and check
before release (safer, adds latency to the first visible token) or check chunk by chunk and retract,
which only works for rules a partial answer cannot already violate.

### Change control and audit
Policies are versioned config validated against a schema. A change is diffed, run against that
customer's guardrail test cases (which belong in their regression suite), and rolled out in stages.
Every block or redaction logs the policy version and rule id, so "why was this answer blocked?" has an
answer and the customer's auditors can see the rule fired.

## Example
A tighten-only merge of platform, customer and deployment layers. Relaxations are *requested* by a
layer but only take effect if the platform's approval registry lists them for that tenant, so the review
gate is in the code path, not in a wiki.

```python
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class Policy:
    blocked_topics: frozenset = frozenset()
    redact_pii: frozenset = frozenset()
    allowed_tools: Optional[frozenset] = None     # None = no restriction at this layer
    max_refund: float = float("inf")
    requested_exceptions: frozenset = frozenset() # e.g. {"dosage"}; inert until approved

APPROVED = {"pharma-co": frozenset({"dosage"})}   # owned by platform policy, change-controlled

def merge(tenant: str, *layers: Policy) -> Policy:
    tools = [l.allowed_tools for l in layers if l.allowed_tools is not None]
    asked = frozenset().union(*(l.requested_exceptions for l in layers))
    granted = asked & APPROVED.get(tenant, frozenset())          # unapproved asks are ignored
    return Policy(
        blocked_topics=frozenset().union(*(l.blocked_topics for l in layers)) - granted,
        redact_pii=frozenset().union(*(l.redact_pii for l in layers)),   # blocklists union
        allowed_tools=frozenset.intersection(*tools) if tools else None,  # allowlists intersect
        max_refund=min(l.max_refund for l in layers),                     # limits take the min
    )

PLATFORM = Policy(blocked_topics=frozenset({"weapons", "dosage"}), redact_pii=frozenset({"ssn"}))
ACME     = Policy(redact_pii=frozenset({"iban"}), max_refund=50.0,
                  allowed_tools=frozenset({"lookup_order", "issue_refund"}),
                  requested_exceptions=frozenset({"dosage"}))   # asks, but is not approved
ACME_FAQ = Policy(allowed_tools=frozenset({"lookup_order"}), max_refund=100.0)

p = merge("acme", PLATFORM, ACME, ACME_FAQ)
print(sorted(p.blocked_topics), p.allowed_tools, p.max_refund)
# ['dosage', 'weapons'] frozenset({'lookup_order'}) 50.0
print(sorted(merge("pharma-co", PLATFORM, Policy(requested_exceptions=frozenset({"dosage"}))).blocked_topics))
# ['weapons']
```

Read the output against the rules. Acme asked to unblock dosage, but no approval exists, so the request
is inert. The FAQ deployment tried to raise the refund ceiling to 100 and got 50, because limits take the
minimum. Its tool allowlist intersected Acme's down to `lookup_order`, so a refund call is rejected in the
tool adapter before it runs. Only the pharma tenant, with a recorded approval, has the floor relaxed, and
only for the one topic approved.

## Trade-offs
- Per-tenant policy lets one platform serve customers with opposite rules, but every customer now has a
  policy to test and maintain; offer a few vetted presets when most customers want similar rules.
- Enforcing in code and classifiers is reliable and auditable, at the cost of latency per check; prompt
  instructions are cheaper but should only shape tone, never carry a rule that must hold.
- Strict thresholds reduce misses but raise false blocks, which push users to work around the tool; the
  balance flips toward strict where one miss is a regulatory incident.
- Letting customer admins edit their own policy speeds iteration, but needs schema validation, an
  envelope of allowed values and staged rollout, or a typo becomes a production outage.

## Pitfalls
- Implementing a customer rule only as a system-prompt instruction; the symptom is a user screenshot of
  the assistant doing the forbidden thing after a little persuasion.
- Reading the tenant id from the request payload; the symptom is a penetration test where changing one
  field gives a caller another tenant's looser policy.
- Copying classifier thresholds between customers; the symptom is one tenant reporting constant false
  blocks while another reports misses, on the same model and policy.
- Leaving guard timeouts unhandled so the request proceeds unchecked; the symptom is violations that
  cluster in high-load windows, when the classifier service was timing out and nobody noticed.
- Not logging which rule and policy version fired; the symptom is support spending hours unable to
  explain why a specific answer was blocked or redacted.
- Letting a customer layer silently relax the platform floor; the symptom is an audit finding that a
  baseline protection was off for one tenant with no approval record.

## When to use
Use per-customer guardrails once two customers need different rules, or once a customer's compliance
team hands you a written policy. Start with a platform floor and a short schema of customer-settable
fields; resist free-form rules until you have demand. Do not build it for a single-customer deployment
where one policy is the customer policy. Measure per tenant: block rate, false-block rate on reviewed
samples, misses found in red-teaming, and time to change a rule safely. In interviews, the key ideas are
tighten-only merging and enforcement outside the prompt.

## Role lens
- **Engineer:** Implements the policy schema, the monotonic merge, enforcement hooks at input, retrieval,
  tool and output, and rule-level logging with policy versions.
- **Architect:** Defines the non-negotiable platform floor, the envelope customers may configure, and
  that tenant identity for policy resolution comes only from authentication.
- **FDE:** Translates the customer's compliance documents into policy config, calibrates thresholds with
  their SMEs on labelled samples, and walks their auditors through the logs.

## Check yourself
**Q:** A healthcare customer wants clinicians to see unredacted patient names in answers, but your
platform baseline redacts names everywhere. How do you support them without weakening the platform?
**A:** I would not let their layer simply override the baseline. Instead the baseline gets a declared,
reviewable exception, "names visible in output for authenticated clinician roles in this deployment",
approved by whoever owns platform policy and recorded with the contract basis. Redaction stays on in
logs, traces and any data leaving the deployment. The exception applies only for that tenant and role,
resolved from the SSO identity, and a guardrail test asserting names are still redacted for other roles
and in logs goes into their regression suite.

## Quiz
### Q: Why should a customer's "never discuss competitors" rule not live only in the system prompt?
- [ ] System prompts have a strict length limit
- [x] A prompt instruction is a request the model can be talked out of, with no enforcement or audit trail
- [ ] Models ignore system prompts entirely in multi-turn conversations
- [ ] Prompt instructions cannot be different per customer
> Prompts can be per customer, which is why the approach is tempting, but they are not enforcement. A
> code-level output check enforces the rule and logs every time it fires.

### Q: The customer layer sets a refund limit of 50 and that customer's deployment layer sets 100. What limit applies?
- [ ] Use the deployment's value because it is the most specific layer
- [ ] Use the larger value to avoid blocking legitimate refunds
- [x] Use the minimum, because merging is tighten-only
- [ ] Average them to balance risk and usability
> Monotonic merging means numeric limits take the minimum (50 here) and denies win, so no layer can loosen
> what a more general layer set. "Most specific wins" is the tempting rule, but it would let any lower layer
> relax a higher one; relaxations must be explicit, approved exceptions.

### Q: Where should the tenant used to resolve guardrail policy come from?
- [ ] A tenant field in the request body
- [ ] The model's own inference from the conversation
- [ ] A header that the client application sets freely
- [x] The authenticated identity, such as the API key or SSO token
> Anything the caller controls can be spoofed to select a looser policy. Binding policy to the
> authenticated identity makes the resolved rules as trustworthy as the authentication.
