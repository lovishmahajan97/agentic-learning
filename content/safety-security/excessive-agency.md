---
id: excessive-agency
module: safety-security
name: Excessive agency
kind: concept
difficulty: core
est_minutes: 16
status: crisp
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
Excessive agency is an LLM system that can do more than its task needs: too many functions, permissions
or autonomy. The fix is a smaller blast radius: minimal tools, user-scoped credentials enforced outside
the model, and human checkpoints.

## Why it exists
- **Tool calls are probabilistic outputs.** The decision to call a tool is sampled like a sentence, with
  the usual upstream causes: an ambiguous request, a hallucinated detail, an injected instruction in a
  document, a loop that never converges. A bad decision becomes real damage.
- **Agents are bounded only by credentials.** A user's mistaken click is bounded by the UI; an agent
  holding a shell, a database client or an API token is bounded only by its credentials and whatever the
  runtime refuses to do.
- **It is the confused deputy in a new costume:** a component with privileges that can be talked into
  using them on behalf of someone who should not have them.
- **Naive mitigations are instructions, which are what gets overridden.** "Be careful with destructive
  actions" is not enforcement. "Ask the model to confirm" makes the same model actor and reviewer. "Trust
  the user's instructions" ignores that the user did not write the email the agent just read.
- **The incidents are familiar.** OWASP lists excessive agency in its Top 10 for LLM applications because
  agents have mass-mailed, deleted data, run destructive commands or made purchases while technically
  doing what they were told. The fix is not a smarter model.

## How it works
### Three axes
OWASP frames the problem along three dimensions, each with its own controls.

| Axis | What it looks like |
|---|---|
| 1. Excessive functionality | tools beyond the task (a summariser that can also send email); broad tools where narrow ones would do (a shell, raw SQL, a generic HTTP client); leftover development tools; a third-party tool server exposing forty functions when three are needed |
| 2. Excessive permissions | credentials broader than the user's: an admin service account shared by everyone, unscoped or long-lived tokens, no tenant or row-level enforcement; the agent can touch everything the service can, regardless of who asks |
| 3. Excessive autonomy | irreversible or high-impact actions without confirmation; no budgets for steps, time or money; no dry run; no kill switch |

### Controls: functionality
- **Select a tool set per workflow,** not globally.
- **Split read from write tools,** *because* the risk profile differs and the split lets you grant read
  widely.
- **Prefer narrow typed tools,** *because* a typed `issue_refund(order_id, amount)` can be policed while
  `run_sql(query)` cannot.
- **Deny by default:** keep a registry and review every tool before it is exposed.

### Controls: permissions
- **Act as the user, not the service:** delegated or on-behalf-of tokens scoped to the user's resources
  and tenant, with short lifetimes.
- **Enforce in the resource layer** (database row-level security, API authorisation) rather than in the
  prompt, *because* enforcement there is deterministic and survives any model behaviour.
- **One credential per tool,** and never place secrets in the context window.

### Controls: autonomy
1. **Classify every action by consequence:** read, reversible write, irreversible write, external
   communication, financial.
2. **Gate the last three with human approval** that displays the exact arguments, not the model's
   description of them.
3. **Offer dry-run or plan review** for destructive operations.
4. **Set budgets per session** for steps, tokens, spend and rows affected; apply rate limits.
5. **Prefer reversible designs:** drafts instead of sends, soft deletes.
6. **Keep a kill switch** and an audit log of every call.

### Runtime pattern: the model proposes, policy disposes
- **Every proposed call passes a policy check outside the model:** who is asking, which tool, which
  arguments, which consequence tier, how much budget is left.
- **Three outcomes:** allow, require approval, or deny with a reason returned to the model so it can
  choose another path.
- **This is where the guarantees live,** *because* a deterministic executor can be tested, logged and
  audited while a prompt cannot.
- **Approval ergonomics matter:** batch low-risk actions and escalate rarely, otherwise humans
  rubber-stamp and the gate becomes theatre.

### Blast radius as arithmetic
```
worst case ≈ what one call can do × how many calls can happen before a human notices
```

| Setup | Worst case |
|---|---|
| Email tool, no recipient restriction, no rate limit, agent runs overnight at one call per second for eight hours | 28,800 sends before anyone looks |
| Per-session cap of 25 calls, a recipient allowlist, a daily limit of 200 | 200 emails to known addresses |

Each control attacks a different factor:
- **Narrower tools** shrink the damage per call.
- **Scoped credentials** shrink what any call can reach.
- **Budgets** shrink the count.
- **Alerting** shrinks the time until someone notices.

When reviewing an agent, compute this product per tool; the largest one is your first fix.

### Measuring agency
- **Tools exposed vs tools used** per workflow; the gap is unnecessary surface.
- **Share of calls requiring approval and the approval acceptance rate;** a rate near 100% means the gate
  is either too broad or being rubber-stamped.
- **Blast radius per credential:** what is the worst query or action it permits?
- **Incident reviews:** record which axis failed.

## Diagram
Every proposed call goes through the executor's checks in order; denials return a reason to the model and
are audited just like executions.

```mermaid
flowchart TD
    M["Model proposes tool call"] --> B{"Budget left?"}
    B -->|"no"| D["Deny with reason to model"]
    B -->|"yes"| S{"User token has scopes?"}
    S -->|"no"| D
    S -->|"yes"| T{"Consequence tier"}
    T -->|"read or reversible"| E["Execute"]
    T -->|"irreversible, external, money"| C{"Spend cap and human approval"}
    C -->|"approved"| E
    C -->|"denied or over cap"| D
    E --> L[("Audit log")]
    D --> L
```

## Examples
### Tool registry and policy-enforcing executor
Consequence tiers and required scopes per tool; the executor enforces the user's scopes, a step budget, a
spend cap and human approval for tier-2 actions. The model never sees credentials and cannot talk its way
past a scope it does not hold.

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
    def done(outcome: str, msg: str) -> str:                         # every decision is audited, denials too
        session.audit.append({"tool": tool.name, "args": args, "tier": tool.tier, "outcome": outcome})
        return msg
    if session.budget["calls"] <= 0:
        return done("budget", "ERROR: step budget exhausted; stop and report progress")
    session.budget["calls"] -= 1
    if not tool.scopes <= session.user_scopes:                      # permissions axis
        return done("scope", f"ERROR: {tool.name} requires {tool.scopes - session.user_scopes}")
    if tool.tier == 2:                                               # autonomy axis
        cents = int(args.get("amount_cents", 0))
        if session.budget["spent"] + cents > session.budget["spend_cap"]:
            return done("spend_cap", "ERROR: spend cap reached for this session")
        if not session.approve(tool.name, args):
            return done("denied", "DENIED by user; choose another course of action")
        session.budget["spent"] += cents
    return done("executed", tool.fn(**args))

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

### Blast-radius calculator
Pure Python: the count factor is the smallest of the rate-limited throughput and every cap in force.

```python
def worst_case_calls(rate_per_s: float, hours: float, session_cap=None, daily_cap=None, sessions=1):
    uncapped = int(rate_per_s * hours * 3600)
    caps = [uncapped]
    if session_cap is not None:
        caps.append(session_cap * sessions)
    if daily_cap is not None:
        caps.append(daily_cap)
    return min(caps)

print(worst_case_calls(1, 8))                                         # 28800 sends overnight
print(worst_case_calls(1, 8, session_cap=25, daily_cap=200, sessions=40))  # 200: the daily cap binds
print(worst_case_calls(1, 8, session_cap=25, daily_cap=200, sessions=1))   # 25: one runaway session
```

### Enforcing tenant scope in the resource layer
A production snippet for PostgreSQL row-level security: the agent's connection sets the tenant for the
transaction, and the database refuses other tenants' rows whatever SQL the tool sends.

```sql
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders FORCE ROW LEVEL SECURITY;   -- otherwise the table owner bypasses the policy
CREATE POLICY tenant_isolation ON orders
  USING (tenant_id = current_setting('app.tenant_id')::uuid);

-- per request, inside the tool's transaction, from the user's verified identity (never from the model):
BEGIN;
SET LOCAL app.tenant_id = '6f1c2d3e-0000-4000-8000-000000000001';
SELECT id, total FROM orders WHERE id = 'ORD-104233';   -- other tenants' rows are invisible
COMMIT;
```

## Production patterns
- **Draft-only launch, then earn autonomy.** Ship write tools in approval-only or draft mode, track the
  approval rejection rate and task accuracy from the audit log, and enable direct execution per tool only
  when the numbers justify it.
- **Delegated, short-lived credentials per user.** Use on-behalf-of tokens scoped to the user's tenant
  and resources, plus row-level security in the database (as above), so isolation holds regardless of the
  model. Monitor scope-denied calls per user.
- **Consequence-tier registry, deny by default.** Every tool declares a tier and required scopes before it
  can be exposed; tier 2 always routes to approval showing exact arguments. Monitor approval share and
  acceptance rate, and investigate a rate near 100%.
- **Pin and diff third-party tool servers.** Pin the server version and diff the exposed tool list and
  descriptions on every update, blocking new or redefined functions until reviewed.
- **Budgets per task class with a kill switch.** Set step, spend and rows-affected caps per workflow, have
  the agent report "budget exhausted" rather than fail silently, and alert on sessions that hit caps.

## Trade-offs
- **Narrow typed tools vs engineering effort.** They are policeable, but every new capability is work;
  this flips for exploratory tasks such as coding agents, where one broad tool inside a strong sandbox
  beats fifty narrow ones.
- **Approval gates vs fatigue.** They bound damage but work only while rare and informative; once users
  rubber-stamp, the gate is theatre with a false sense of safety.
- **Delegated credentials vs identity plumbing.** Per-user credentials remove the confused-deputy risk but
  need identity integration with every downstream system; for unattended batch jobs with no user, fall
  back to a tightly scoped service identity with hard budgets.
- **Budgets vs long tasks.** They stop runaway loops but can cut off legitimate long tasks; set them per
  task class and let the agent report "budget exhausted" instead of failing silently.
- **Dry-run vs round trips.** Plan review catches destructive mistakes at the cost of a second round trip;
  apply it to irreversible tiers only, not to reads.

## Pitfalls
- **One admin service account for all users.** Symptom: the agent answers questions about another
  customer's records when asked nicely.
- **Showing the human the model's summary, not the arguments.** Symptom: approving "send the update to the
  team" and four hundred people receiving it.
- **Asking the model "are you sure?" as the gate.** Symptom: it says yes.
- **Shipping shell, raw SQL or generic HTTP tools "for flexibility".** Symptom: an incident report with a
  destructive command or an outbound call to an unknown host.
- **No step or spend budget.** Symptom: a loop that burned thousands of calls overnight.
- **Passing secrets through tool arguments.** Symptom: API keys in traces and occasionally in model output.
- **Reviewing a third-party tool server once.** Symptom: new or redefined functions appear after an
  upstream update nobody approved; version pinning and a diff of the exposed tool list on every change
  would have caught it.
- **Auditing only executed calls.** Symptom: an incident review that cannot tell how many times the agent
  tried a denied action before one worked.
- **Irreversible actions without a draft or soft-delete mode.** Symptom: a misfire with nothing to roll
  back.

## When to use
- **Apply when:** a model output can cause a side effect, which is every agent and most tool-using
  assistants.
- **Recipe:** start from the task's minimal list of verbs, grant the user's scopes rather than the
  service's, tier every tool by consequence, gate the irreversible and external tiers, budget every
  session and log every call.
- **Measure:** tools exposed versus used, approval rates and the blast radius per credential.
- **Day to day:** the review you run before enabling a new tool server, and the first question in an
  incident review.
- **Interviews:** name the three axes and explain "the model proposes, the policy disposes" with an
  example of enforcement living in the resource layer.

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

## Interview questions
### What is excessive agency, and why can't a better prompt fix it?
- **Definition:** the system can do more than the task needs, through too many functions, too broad
  permissions or too much autonomy, so a bad decision becomes real damage.
- **Prompts are not enforcement:** injected text or a hallucination overrides "be careful"; controls must
  live in the executor and resource layer where they are deterministic.

### An agent answered a question using another tenant's data. Debug it.
- **Check the credential first:** a shared admin service account means the agent can read everything the
  service can, regardless of who asked.
- **Fix at the resource layer:** delegated per-user tokens and row-level security keyed to the verified
  tenant, never to a value the model supplies.
- **Audit:** use the call log, including denied calls, to find every request that crossed tenants.

### When would you give an agent a broad tool like a shell?
- **For exploratory work such as coding agents,** where fifty narrow tools would never cover the task.
- **Only inside a strong sandbox:** no production credentials, restricted network egress, disposable
  environment, and budgets on steps and time, so the blast radius stays small even though the tool is
  broad.

### How do you compute and reduce the blast radius of an email tool?
- **Multiply damage per call by calls before a human notices:** at one call per second for eight hours
  that is 28,800 sends.
- **Shrink each factor:** recipient allowlist, a per-session cap of 25 calls and a daily limit of 200 cut
  it to 200 emails to known addresses; alerting shortens the time factor.

### Your approval gate has a 99% acceptance rate. Is that good?
- **Probably not:** it suggests the gate is too broad or being rubber-stamped, so it adds friction without
  scrutiny.
- **Narrow it to irreversible, external and financial actions,** batch low-risk ones, and always show
  exact arguments so the rare approvals that remain get real attention.

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
