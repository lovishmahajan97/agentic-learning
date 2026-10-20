---
id: approval-and-authorization-boundaries
module: agent-runtime-architecture
name: Approval & authorization boundaries
kind: pattern
difficulty: core
est_minutes: 15
related:
  - agents/human-in-the-loop
  - agents/approval-gates-for-consequential-actions
  - agent-reliability/tool-permissions-and-least-privilege
  - safety-security/excessive-agency
  - safety-security/tool-privilege-escalation
  - backend-fundamentals/authentication-vs-authorization
  - backend-fundamentals/oauth-2-0-oidc
  - agent-runtime-architecture/policy-enforcement-across-agents-and-tools
sources:
  - OWASP — Top 10 for Large Language Model Applications
  - IETF RFC 8693 — OAuth 2.0 Token Exchange
  - Hardy, 1988 — The Confused Deputy
---

## TL;DR
Authorization answers "may this principal perform this action at all"; approval answers "does a human need
to confirm this particular instance". Both must be decided by the runtime at the tool-dispatch boundary,
outside the model's influence, because model output is untrusted input: a prompt-injected or hallucinated
tool call carries exactly as much authority as a legitimate one. The agent acts with the *intersection* of
the user's rights and its own delegated scope, and an approval is bound to the exact arguments it approved.

## Why it exists
An agent that can call tools is a program that performs actions chosen by a model. Whoever influences the
model, whether the user, a web page the agent read, or a document in the retrieval index, influences those
actions. That makes two questions unavoidable for every tool call: is the principal behind this run allowed
to do this, and, even if so, is this specific action consequential enough that a person should look first?

The naive answer is to put the rules in the system prompt: "never delete data, ask before sending email".
This fails for three reasons. Instructions are advisory, not enforced; a sufficiently persuasive injection
overrides them, and models comply with injected instructions at a rate that no security review will accept.
The model cannot verify permissions it cannot see; it does not know which records this user may read. And
nothing is recorded; when something goes wrong there is no decision to audit, only a transcript.

A second naive answer is to give the agent a powerful service account "so it can do what users need". This
creates the classic confused deputy: a user who may not read a record asks the agent, the agent reads it
with its own broad credential, and the access control of the underlying system is bypassed by a layer of
indirection. Agents that are more privileged than the people driving them are the central finding behind
the "excessive agency" class of LLM vulnerabilities.

So the boundary has to live in the runtime, be evaluated deterministically with the real principal's
identity, and treat the human approval as a durable, argument-bound record rather than a conversational
"are you sure?".

## How it works
### Three principals, one effective authority
Every run has at least three identities in play: the **end user** on whose behalf it acts, the **agent**
itself (a workload identity with its own allowed tool set), and the **tool owner** whose service will be
called. The effective authority of a tool call is the intersection: the action must be within the user's
permissions *and* within the agent's delegated scope. Intersecting rather than unioning is the design
choice that prevents the confused deputy, because the agent can never reach more than the user could.

In practice this is implemented by delegation: the runtime holds a user token or scopes obtained at
sign-in and, when dispatching a tool, exchanges it for a short-lived, down-scoped token for that tool
(OAuth token exchange / on-behalf-of flows, or cloud role assumption with a session policy). The tool
service then enforces its own authorization against the delegated identity, which gives defence in depth:
even if the runtime's check is bypassed, the tool sees a token that cannot do more than the user could.

### Classifying actions
Authorization is binary per (principal, action, resource). Approval needs a richer classification because
"allowed" and "safe to do unattended" differ. A workable tiering:

- **auto** — read-only, idempotent, inside the tenant (search, fetch a record).
- **log** — writes that are reversible or low blast radius (create a draft, add a comment).
- **approve** — irreversible, external, financial or above a threshold (send email, refund, delete,
  merge, call anything that costs money beyond a cap).
- **deny** — never from an agent, regardless of user (drop tables, change IAM, disable logging).

Tiers are assigned per tool and refined per argument (a refund under a threshold may be `log`, above it
`approve`). Keeping this table in configuration owned by security, not in the agent's code or prompt, is
what makes it reviewable and changeable without redeploying agents.

### Approval as a durable, argument-bound state
When the gate returns `approve`, the runtime does not block; it writes an **approval request** containing
the run id, the tool name, the full arguments, and a fingerprint (hash of the canonicalised call), parks the
run in `waiting_for_approval`, and notifies an approver through whatever channel the organisation uses.
The approver sees the exact action, not the model's description of it, because descriptions can be
misleading while arguments are what will execute.

When the approval event arrives, the runtime resumes and re-checks three things before executing: the
fingerprint of the call it is about to dispatch equals the approved fingerprint (so the model cannot
"adjust" the arguments after approval), the approval has not expired (a TTL bounds how stale the human's
context can be), and authorization still holds (permissions may have been revoked while parked). Only then
does it dispatch. The approval record, the approver's identity and the final decision are appended to the
run's log, which turns the audit question "who authorised this?" into a lookup.

### Avoiding approval fatigue
A gate that asks too often trains approvers to click yes, which is worse than no gate. Mitigations that
preserve the control: approve a *plan* up front (a bounded set of actions and arguments) rather than each
step; batch low-risk approvals; allow a scoped, time-boxed "always allow this tool with these constraints
for this session"; and track the approval rate and median time-to-approve so you notice when the gate has
become a rubber stamp.

## Example
A dispatch-time gate that computes effective authority as an intersection, tiers the action, and treats
approvals as fingerprint-bound records with a TTL. The policy table would normally be loaded from
configuration; it is inlined here to keep the mechanics visible.

```python
import hashlib, json, time

TOOL_SCOPES = {"search_docs": {"docs:read"}, "create_ticket": {"tickets:write"},
               "refund_payment": {"payments:refund"}, "drop_table": {"db:admin"}}
TIER = {"search_docs": "auto", "create_ticket": "log", "refund_payment": "approve", "drop_table": "deny"}

def fingerprint(call):                       # canonical bytes -> same args always hash the same
    return hashlib.sha256(json.dumps(call, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def tier_for(call):
    t = TIER.get(call["name"], "approve")    # unknown tools default to the safe side
    if call["name"] == "refund_payment" and call["args"].get("amount", 0) <= 50:
        t = "log"                            # argument-level refinement
    return t

def gate(run, call, approvals):
    need = TOOL_SCOPES.get(call["name"], {"__unknown__"})
    if not (need <= run.user_scopes and need <= run.agent_scopes):
        return ("deny", "outside effective authority (user ∩ agent)")
    tier = tier_for(call)
    if tier == "deny":
        return ("deny", "action never permitted from an agent")
    if tier in ("auto", "log"):
        return ("allow", tier)
    fp = fingerprint(call)
    rec = approvals.get(run.id, fp)          # keyed by run AND fingerprint
    if rec and rec["expires_at"] > time.time() and rec["decision"] == "approved":
        return ("allow", f"approved by {rec['approver']}")
    if rec and rec["decision"] == "rejected":
        return ("deny", f"rejected by {rec['approver']}")
    approvals.create(run.id, fp, call, ttl_s=4 * 3600)   # park the run; a human decides later
    return ("wait", fp)
```

## Trade-offs
- Intersecting user and agent scopes gives least privilege and clean audit attribution, but requires
  per-user delegated tokens; for background agents with no user present you fall back to a tightly scoped
  workload identity and tier more actions as `approve`.
- Binding approvals to exact argument fingerprints defeats argument-swapping after approval, but any
  benign retry with different arguments needs a fresh approval; loosen to a constrained intent (recipient
  fixed, body free) only where you can express the constraint in policy.
- Enforcing at the runtime alone is simpler than also enforcing at each tool service, but a single
  bypass is then total; defence in depth costs token plumbing and buys survival of your own bugs.
- Plan-level approval cuts prompts dramatically and keeps humans engaged, but the agent may legitimately
  need to deviate, and every deviation outside the approved set must re-trigger the gate.
- Short approval TTLs keep the human's context fresh, yet in asynchronous organisations they expire
  before anyone looks; pick the TTL from your observed time-to-approve, not from a security wish.

## Pitfalls
- Delegating the approval question to the model ("ask the user before sending"); the symptom is a red-team
  injection that gets the action executed with a polite confirmation message the user never saw.
- Approving by description rather than by arguments; the symptom is an audit log where the executed call
  differs from what the approver believed they approved.
- Running the agent under a broad service account; the symptom is users retrieving through the agent
  records they cannot open directly, discovered during a security review or worse.
- Approvals without a TTL or run binding; the symptom is an approval granted on Monday executing a
  different run's call on Friday after the business context changed.
- Resuming after approval without re-authorizing; the symptom is a revoked user whose parked runs still
  complete their actions hours after offboarding.
- Unknown tools defaulting to `allow`; the symptom is a newly added tool executing consequential actions
  before anyone classified it.

## When to use
Every agent that can perform a write needs the authorization half from day one; the approval half becomes
necessary the moment an action is irreversible, external, financial or outside the tenant. Resist the
instinct to gate everything: measure the approval rate per tool, the reject rate (near zero means the gate
is theatre or the tier is wrong) and time-to-approve, and move tools between tiers on evidence. Day to day
this shows up as a tier table in configuration, delegated tokens in tool adapters and an approvals inbox
in the product. In interviews, expect to be asked where the check lives and why not in the prompt, how you
prevent the confused deputy, and what exactly an approver is approving; the strong answer names the
fingerprint, the TTL and the re-authorization on resume.

## Role lens
- **Engineer:** Implements the gate in the dispatcher, canonicalises and hashes calls, and writes tests
  where an injected tool call must park or deny rather than execute.
- **Architect:** Owns the tier taxonomy, the delegation model (user token exchange versus workload
  identity) and the rule that tool services independently enforce authorization.
- **FDE:** Maps tiers onto the customer's change-management and SoD rules, wires approvals into their
  ticketing or chat tooling, and tunes TTLs to how fast their approvers actually respond.

## Check yourself
**Q:** A support agent can issue refunds. Security asks: how do you guarantee a prompt-injected ticket
cannot cause a large refund, without making agents unusable for the thousands of small, legitimate refunds
per day? Describe the boundary you would build.
**A:** First, authorization: the refund tool requires a `payments:refund` scope that must be present in both
the support user's delegated token and the agent's allowed scopes, so a run for a user without refund rights
denies regardless of what the model asks. Second, approval tiering by argument: refunds at or below a
threshold are `log` (executed, recorded, sampled for review), above it `approve`. An approve-tier call parks
the run with a fingerprint of the exact arguments, a human sees amount and recipient, and on resume the
runtime verifies the fingerprint, the TTL and that authorization still holds. Third, defence in depth: the
payments service enforces the per-user limit itself on the delegated token, so a bug in the gate cannot
exceed it. Finally, I would monitor approval and reject rates to keep the threshold honest.

## Quiz
### Q: Why should an agent's effective authority be the intersection of the user's permissions and the agent's scopes, rather than the agent's scopes alone?
- [ ] Intersection reduces the number of tokens spent describing tools to the model
- [x] It prevents the confused-deputy problem where users reach data through the agent that they could not access directly
- [ ] Agent scopes are stored in the prompt and cannot be trusted
- [ ] Union would require a separate OAuth client per tool
> If the agent acts with only its own, typically broad, authority, it becomes a privileged proxy that
> launders access for whoever drives it. Intersecting with the user's rights means the agent can never do
> more than the person could, which is the defining property of a safe deputy.

### Q: An approver confirms "send the summary to the customer". The model then calls the email tool with a different recipient. Which mechanism stops the send?
- [ ] A system prompt instruction to use only approved recipients
- [ ] Rate limiting the email tool to one call per run
- [x] The runtime compares the fingerprint of the call about to execute with the fingerprint that was approved
- [ ] Requiring the approver to be an administrator
> Binding the approval to a hash of the canonicalised arguments makes it specific to that recipient and
> body; any change produces a different fingerprint and the call parks again. Prompt instructions and rate
> limits do not constrain which arguments execute.

### Q: A run parked for approval is resumed three days later. What should the runtime do before dispatching the approved call?
- [ ] Dispatch immediately, since the approval was already granted
- [ ] Ask the model to re-plan and generate a new tool call
- [x] Check the approval has not expired and re-evaluate authorization for the current principal
- [ ] Downgrade the action to the `log` tier because a human already saw it
> Approvals carry a TTL because the human's context goes stale, and permissions can be revoked while a run
> is parked. Re-checking both keeps a long-parked run from executing an action nobody would authorise today.
