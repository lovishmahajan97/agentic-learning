---
id: excessive-agency
module: safety-security
name: Excessive agency
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - safety-security/tool-privilege-escalation
  - agents/approval-gates-for-consequential-actions
  - agents/human-in-the-loop
  - agent-reliability/tool-permissions-and-least-privilege
  - agents/sandboxing
  - agent-runtime-architecture/approval-and-authorization-boundaries
  - safety-security/owasp-top-10-for-llms
  - agent-reliability/max-step-budgets
sources:
  - OWASP, 2025 — Top 10 for LLM Applications
---

## TL;DR
Excessive agency is an LLM system that can do more than its task needs: too many functions, too much
permission, or too much autonomy. Then a bad decision, from a hallucinated intent, an injected
instruction or an ambiguous request, becomes real damage. The fix is not a smarter model but a smaller
blast radius: minimal tools, credentials scoped to the user and enforced outside the model, and human
checkpoints before irreversible or external actions.

## Why it exists
Agents act, and the decision to call a tool is a probabilistic output in the same way a sentence is.
Its upstream causes are the usual ones: an ambiguous request, a hallucinated detail, an injected
instruction in a document, a loop that never converges. In a traditional application the damage from a
user's mistaken click is bounded by the UI; an agent holding a shell, a database client or an API token
is bounded only by its credentials and by whatever the runtime refuses to do. That is the confused
deputy problem in a new costume: a component with privileges that can be talked into using them on
behalf of someone who should not have them.

The naive mitigations are instructions aimed at the model, and instructions are exactly what an attacker
or a hallucination overrides. "Be careful with destructive actions" is not enforcement. "Ask the model to
confirm" makes the same model both actor and reviewer. "Trust the user's instructions" ignores that the
user did not write the email the agent just read. OWASP lists excessive agency in its Top 10 for LLM
applications because the incidents are already familiar: agents that mass-mailed, deleted data, ran
destructive commands or made purchases while technically doing what they were told.

## How it works
### Three axes
OWASP frames the problem along three dimensions, and each has its own controls.

1. **Excessive functionality.** Tools beyond the task (a summariser that can also send email), broad
   tools where narrow ones would do (a shell, raw SQL, a generic HTTP client), leftover development tools,
   or a third-party tool server exposing forty functions when three are needed.
2. **Excessive permissions.** Credentials broader than the user's: an admin service account shared by
   everyone, unscoped or long-lived tokens, no tenant or row-level enforcement. The agent can touch
   everything the service can, regardless of who is asking.
3. **Excessive autonomy.** Irreversible or high-impact actions without confirmation, no budgets for steps,
   time or money, no dry run, no kill switch.

### Controls per axis
**Functionality.** Select a tool set per workflow, not globally. Split read from write tools, *because*
the risk profile differs and the split lets you grant read widely. Prefer narrow typed tools over generic
executors, *because* a typed `issue_refund(order_id, amount)` can be policed while `run_sql(query)`
cannot. Keep a deny-by-default registry and review every tool before it is exposed.

**Permissions.** Act as the user, not as the service: delegated or on-behalf-of tokens scoped to the
user's resources and tenant, with short lifetimes. Enforce in the resource layer (database row-level
security, API authorisation) rather than in the prompt, *because* enforcement there is deterministic and
survives any model behaviour. Give each tool its own credential, and never place secrets in the context
window.

**Autonomy.** Classify every action by consequence: read, reversible write, irreversible write, external
communication, financial. Gate the last three with human approval that displays the exact arguments, not
the model's description of them. Offer a dry-run or plan-review mode for destructive operations. Set
budgets per session for steps, tokens, spend and rows affected; apply rate limits; prefer reversible
designs (drafts instead of sends, soft deletes). Keep a kill switch and an audit log of every call.

### Runtime pattern: the model proposes, policy disposes
Each proposed tool call passes through a policy check outside the model: who is asking, which tool, with
which arguments, at which consequence tier, with how much budget left. The outcomes are allow, require
approval, or deny with a reason returned to the model so it can choose another path. This is where the
guarantees live, *because* a deterministic executor can be tested, logged and audited while a prompt
cannot. Approval ergonomics matter: batch low-risk actions and escalate rarely, otherwise humans
rubber-stamp and the gate becomes theatre.

### Measuring agency
Count tools exposed versus tools actually used per workflow; the gap is unnecessary surface. Track the
share of calls requiring approval and the approval acceptance rate; a rate near 100% means the gate is
either too broad or being rubber-stamped. Map the blast radius of each credential: what is the worst
query or action it permits? Review incidents for which axis failed.

## Example
A tool registry with consequence tiers and required scopes, and an executor that enforces the user's
scopes, a step budget, a spend cap and human approval for tier-2 actions. The model never sees
credentials and cannot talk its way past a scope it does not hold.

```python
from dataclasses import dataclass, field
from typing import Callable

@dataclass
class Tool:
    name: str
    fn: Callable[..., str]
    tier: int                    # 0 read, 1 reversible write, 2 irreversible / external / money
    scopes: set[str]             # permissions the *user's* token must carry, not the service's

@dataclass
class Session:
    user_scopes: set[str]
    approve: Callable[[str, dict], bool]       # asks a human and shows the exact arguments
    budget: dict = field(default_factory=lambda: {"calls": 25, "spent": 0, "spend_cap": 5000})
    audit: list = field(default_factory=list)

def execute(session: Session, tool: Tool, args: dict) -> str:
    if session.budget["calls"] <= 0:
        return "ERROR: step budget exhausted; stop and report progress"
    session.budget["calls"] -= 1
    if not tool.scopes <= session.user_scopes:                      # permissions axis
        return f"ERROR: {tool.name} requires {tool.scopes - session.user_scopes}"
    if tool.tier == 2:                                               # autonomy axis
        cents = int(args.get("amount_cents", 0))
        if session.budget["spent"] + cents > session.budget["spend_cap"]:
            return "ERROR: spend cap reached for this session"
        if not session.approve(tool.name, args):
            return "DENIED by user; choose another course of action"
        session.budget["spent"] += cents
    result = tool.fn(**args)
    session.audit.append({"tool": tool.name, "args": args, "tier": tool.tier})
    return result

TOOLS = {t.name: t for t in [                                        # functionality axis: two tools, no shell
    Tool("get_order", lambda order_id: f"order {order_id}: $89.99, delivered", 0, {"orders:read"}),
    Tool("issue_refund", lambda order_id, amount_cents: f"refunded {amount_cents}", 2, {"orders:refund"}),
]}
s = Session(user_scopes={"orders:read"},
            approve=lambda name, a: input(f"Allow {name} {a}? [y/N] ").strip().lower() == "y")
print(execute(s, TOOLS["get_order"], {"order_id": "ORD-104233"}))
print(execute(s, TOOLS["issue_refund"], {"order_id": "ORD-104233", "amount_cents": 8999}))
# -> ERROR: issue_refund requires {'orders:refund'}. A first-line agent's token cannot refund,
#    however persuasive the model, the customer, or an injected ticket is about it.
```

## Trade-offs
- Narrow typed tools are policeable but every new capability is engineering work; this flips for
  exploratory tasks such as coding agents, where one broad tool inside a strong sandbox beats fifty narrow
  ones.
- Approval gates bound damage but create fatigue; they work only while they are rare and informative,
  and once users rubber-stamp, the gate is theatre with a false sense of safety.
- Per-user delegated credentials remove the confused-deputy risk but require identity plumbing with every
  downstream system; for unattended batch jobs with no user, fall back to a tightly scoped service
  identity with hard budgets.
- Budgets stop runaway loops but can cut off legitimate long tasks; set them per task class and let the
  agent report "budget exhausted" instead of failing silently.
- Dry-run and plan review catch destructive mistakes at the cost of a second round trip; apply them to
  irreversible tiers only, not to reads.

## Pitfalls
- One admin service account behind the agent for all users; the symptom is the agent answering questions
  about another customer's records when asked nicely.
- Showing the human the model's summary instead of the actual arguments; the symptom is approving "send
  the update to the team" and four hundred people receiving it.
- Asking the model "are you sure?" as the gate; the symptom is that it says yes.
- Shipping shell, raw SQL or generic HTTP tools "for flexibility"; the symptom is an incident report with
  a destructive command or an outbound call to an unknown host.
- No step or spend budget; the symptom is a loop that burned thousands of calls overnight.
- Passing secrets through tool arguments; the symptom is API keys in traces and occasionally in model
  output.
- Irreversible actions without a draft or soft-delete mode; the symptom is a misfire with nothing to roll
  back.

## When to use
Apply these controls whenever a model output can cause a side effect, which is every agent and most
tool-using assistants. Start from the task's minimal list of verbs, grant the user's scopes rather than
the service's, tier every tool by consequence, gate the irreversible and external tiers, budget every
session and log every call. Measure tools exposed versus used, approval rates and the blast radius per
credential. Day to day this is the review you run before enabling a new tool server and the first
question in an incident review. In interviews, name the three axes and explain "the model proposes, the
policy disposes" with an example of enforcement living in the resource layer.

## Role lens
- **Engineer:** Builds the tool registry with tiers and scopes, the executor that enforces them, and
  tests proving a denied call returns a clean error the model can recover from.
- **Architect:** Owns the authorisation boundary: delegated identity, which actions require a human,
  budgets per workflow, and the rule that enforcement lives in the runtime and resource layers.
- **FDE:** Negotiates tool scopes with the customer's security team, launches write tools in
  approval-only mode, and uses the audit log to earn expanded autonomy over time.

## Check yourself
**Q:** A customer wants a procurement agent that reads invoices, matches them to purchase orders and
pays vendors. Design the agency controls before you agree to build it.
**A:** Functionality: four narrow tools, read invoice, match purchase order, create payment draft and
submit payment, with no email, shell or generic HTTP. Permissions: the agent acts with the accounts
payable clerk's delegated identity, scoped to their entities and vendors, and the payments API enforces
limits server-side. Autonomy: tier the tools, reads at zero, draft creation reversible, submission
irreversible and financial, so submission requires human approval showing vendor, amount and bank
details exactly; cap spend per session and per day; treat any change of vendor bank account as a
separate verification step, since that is the classic fraud pattern; produce a dry-run matching report;
log every call; keep a kill switch. Launch in draft-only mode, measure match accuracy and approval
rejection rate, and enable submission only when the numbers justify it.

## Quiz
### Q: Which of these is not one of OWASP's three roots of excessive agency?
- [ ] Excessive functionality
- [ ] Excessive permissions
- [x] Excessive context length
- [ ] Excessive autonomy
> OWASP frames the risk as too many functions, too much permission and too much autonomy, each with its
> own controls. Context length affects cost and attention, not what the agent is allowed to do.

### Q: Why should an approval prompt show the exact tool arguments rather than the model's summary?
- [ ] Summaries are usually too long for the interface
- [x] The summary comes from the same possibly misled model, while the arguments are what will actually execute
- [ ] Arguments are easier to store in the audit log
- [ ] Showing arguments reduces token usage
> An approval gate exists to put a human between the model and the consequence; if the human reviews text
> the model wrote about itself, the gate inherits the model's error and becomes decoration.

### Q: What best prevents an agent from acting on another tenant's data?
- [ ] Adding "only access the current user's data" to the system prompt
- [ ] Lowering the model's temperature
- [x] Per-user delegated credentials enforced by the resource layer instead of a shared service account
- [ ] Reducing max_tokens so the agent cannot read large results
> Tenant isolation must hold regardless of what the model decides, so it belongs in authorisation and
> row-level security that the agent's credential cannot bypass; a prompt instruction is advisory.
