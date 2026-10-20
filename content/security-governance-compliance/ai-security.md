---
id: ai-security
module: security-governance-compliance
name: AI security
kind: concept
difficulty: intro
est_minutes: 13
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
AI security is ordinary application security plus one new component: a model that follows instructions
found in its data, cannot be patched the way a parser can, and is increasingly handed credentials and
tools. The attack surface therefore moves from syntax (SQL, HTML) to semantics (any text the model
reads), and from the model to everything around it. The controls that hold are the old ones applied to
the model as an untrusted interpreter: least privilege, isolation, output handling, approval gates and
monitoring, not cleverer prompts.

## Why it exists
Three things changed when an LLM went into the system.

First, control and data share one channel. A parser has a grammar: `'; DROP TABLE` is data until you
concatenate it into SQL, and parameterised queries end the argument. A transformer has one token stream.
The system prompt, the user's message, a retrieved PDF and a tool's JSON result are all tokens with
attention between them, and the model has no hardware notion of which tokens are allowed to give orders.
Anything it reads can steer it.

Second, the failure mode is probabilistic and not patchable in the usual sense. When a parser bug is
found you fix the code and the class is gone. When a jailbreak is found you add training data or a
filter, and the attack mostly stops working, for a while, in that phrasing. There is no proof of absence,
and a determined attacker with unlimited attempts eventually gets through a probabilistic filter.

Third, models are being given agency: browsing, email, code execution, database access, payment APIs. A
chatbot that says something embarrassing is a PR problem. An agent that reads a poisoned web page and
then acts with your credentials is a breach.

The naive responses fail for the same reason. "Add a guardrail prompt" uses the same instruction-following
component to defend itself. "Our WAF already inspects requests" fails because the payload is natural
language, not a signature. "The vendor handles safety" confuses the vendor's content policy with your
authorisation boundary; the vendor does not know which of your users may issue refunds.

## How it works
### What you are protecting
Assets: data (training sets, retrieval corpora, conversation logs, which are often the most sensitive
store you own), the model itself (weights, fine-tunes, and the system prompt, both as IP and as a map of
your controls), compute (the GPU bill), actions (every tool and credential the model can reach), and
decisions (what users and downstream systems trust the output for).

### Where attacks enter, by lifecycle
- Supply chain and training time: weights from a hub (pickle-based checkpoints run code on load, which is
  why safetensors exists), poisoned fine-tune or pre-training data, malicious dependencies, tampered
  datasets. Covered in depth in the supply-chain lesson.
- Inference-time input: direct prompt injection and jailbreaks (the user attacks the system), indirect
  prompt injection (content the model reads attacks the user, as Greshake et al. demonstrated against real
  deployed applications), and poisoning of the retrieval corpus so injected content is retrieved later.
- Inference-time output: improper output handling, meaning model text flowing unescaped into HTML, SQL,
  a shell or a tool call; and excessive agency, meaning the model holding more permissions than the task
  needs.
- Exfiltration and privacy: system prompt leakage, training data extraction (Carlini et al. showed
  memorised sequences can be recovered from a production-scale language model), cross-tenant leakage
  through shared indexes or caches, and membership inference.
- Availability and cost: unbounded consumption, sometimes called denial of wallet, via long inputs,
  recursive agent loops or expensive tool calls triggered by injected content.
- Model theft: approximating a model by querying its API at scale.

### The three facts to design around
1. Any text the model reads may act as an instruction. Trust boundaries must therefore be drawn around
   content sources, not around the model.
2. The model will be steered some fraction of the time. Security must come from what a steered model
   cannot do, not from what it is told not to do.
3. Output is attacker-influenced. Treat it as untrusted input to everything downstream.

### The controls that follow
- Identity and least privilege for the model's tools: the agent acts with at most the user's permissions,
  scoped per task, with short-lived credentials, read-only unless writing is the job. This bounds the
  damage of fact 2.
- Isolation: sandboxed code execution, egress allowlists, and no secrets in the context window, because a
  secret the model can see is a secret that can be printed.
- Input side: tag the provenance of each context segment; run classifiers on untrusted content as a
  signal, not as a boundary.
- Output side: schemas and validation, escaping before rendering, allowlisted URLs and recipients, and no
  auto-rendered images or links from model text, which is a standard exfiltration channel.
- Human approval for consequential or irreversible actions, shown with the actual arguments.
- Monitoring: log prompts, tool calls and content sources; detect anomalies; place canary strings in
  system prompts so leakage is observable.
- Supply chain: pinned versions, signed artifacts, safetensors, dataset scanning.

### The frameworks and what each is for
The OWASP Top 10 for LLM Applications is a checklist of vulnerability classes (prompt injection,
sensitive information disclosure, supply chain, data and model poisoning, improper output handling,
excessive agency, system prompt leakage, vector and embedding weaknesses, misinformation, unbounded
consumption); use it to seed a review. MITRE ATLAS catalogues attacker tactics and techniques against ML
systems in the style of ATT&CK, with case studies; use it for threat modeling and red-team planning. The
NIST AI RMF (govern, map, measure, manage) is about organisational risk management, not technical
controls. The safety-security module covers each attack class in depth; this module adds the layers
around them: threat modeling, policy, classification, residency and governance.

## Example
A tool boundary that encodes the three facts. Reading untrusted content taints the turn; a tainted turn
cannot cause an external side effect without a human seeing the real arguments; and argument limits
(recipient allowlist, refund ceiling) hold no matter what the model says, because they are checked in
code the model cannot reach.

```python
from dataclasses import dataclass, field

@dataclass
class Turn:
    user_id: str
    tainted: bool = False                       # set once any untrusted content entered the context
    approvals: set = field(default_factory=set)

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
    if spec["effect"] == "external" and turn.tainted and name not in turn.approvals:
        if not approve(turn.user_id, name, args):                 # a human sees the actual arguments
            raise PermissionError("not approved")
        turn.approvals.add(name)
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

## Trade-offs
- Least privilege and approval gates bound the blast radius but cost autonomy; the gate is worth it when an
  action is irreversible or external, and becomes friction theatre when applied to read-only tools.
- Input filters and classifiers catch cheap attacks and give telemetry, but every filter is bypassable;
  treat them as reducing volume rather than as a boundary unless the attacker has no incentive to retry.
- Self-hosting models removes vendor data exposure but moves supply-chain, patching and jailbreak-fix
  burden onto you; the balance flips when you have no in-house ML operations capacity.
- Logging full prompts gives forensic ability but creates a new sensitive store; it flips to redacted
  logging as soon as prompts carry regulated data.

## Pitfalls
- Treating the system prompt as a security control ("never reveal these instructions"); symptom: the
  prompt appears on social media within days of launch.
- Giving the agent a service account broader than the user's rights; symptom: a user asks the assistant
  for a record they cannot open themselves and receives it.
- Rendering model output as markdown with remote images or auto-links enabled; symptom: outbound requests
  to unknown domains carrying conversation text in query strings.
- Loading weights from an unverified hub in a pickle-based format; symptom: arbitrary code runs at load
  time, usually discovered only in the incident review.
- Securing the chat UI and forgetting the logs, traces and eval datasets that duplicate every prompt;
  symptom: customer PII found in an observability SaaS during an audit.

## When to use
This lesson is the map; use it on day one of any AI project to decide which deep lessons apply. Ask four
questions of any design: what untrusted content reaches the model, what can the model do, what can its
output reach, and where does the data go afterwards. Measure the number of side-effecting tools without a
gate, the share of context that comes from untrusted sources, and the time to revoke an agent's
credentials. In interviews, "how is securing an LLM application different from securing a web
application" is the standard opener; the three facts above are the expected answer, and listing prompt
tricks instead signals inexperience.

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
