---
id: input-output-guardrails
module: safety-security
name: Input / output guardrails
kind: pattern
difficulty: core
est_minutes: 16
related:
  - safety-security/prompt-injection
  - safety-security/output-validation
  - safety-security/content-moderation
  - safety-security/pii-redaction
  - safety-security/llama-guard
  - safety-security/nemo-guardrails
  - customer-data-evaluation/per-customer-guardrails
  - evals/red-teaming
sources:
  - OWASP, 2025 — Top 10 for LLM Applications
  - Official docs: NeMo Guardrails
  - Official docs: Guardrails AI
---

## TL;DR
A language model is a probabilistic component whose behaviour you cannot fully specify in a prompt, so
the system around it needs deterministic checkpoints. Input guardrails inspect what reaches the model
(injection patterns, scope, PII, size); output guardrails inspect what leaves it (structure, policy,
leakage, tool calls) and decide to allow, block, rewrite, retry or escalate. They exist because a
system prompt is an instruction, not an enforcement boundary, and the cost of a wrong output is paid
outside the model.

## Why it exists
Every serious application validates at its trust boundaries: web forms, API payloads, file uploads. An
LLM feature adds a new kind of boundary, natural language in and natural language (or actions) out, and
the component in the middle does not behave like a parser. It weighs the developer's instructions
against everything else in its context and samples an answer. The same prompt produces different
outputs at non-zero temperature, a paraphrased request can flip a refusal into compliance, and text
arriving in a document or tool result is read with the same attention as the system prompt.

The obvious fixes do not hold. "Put the rule in the system prompt" fails because the model does not
distinguish instructions from data and follows rules probabilistically; red teams bypass prompt-only
rules with a role-play prefix in minutes. "Use a better model" lowers the failure rate but does not
bound it, and leaves you with no log of what was attempted and nothing to tune. "Have a human review
everything" does not scale past a pilot.

Guardrails give you what the prompt cannot: a place to put deterministic rules, a measurable block
rate with false positives you can count, an audit trail per decision, and a fail-closed default when a
component is down. Regulated and contractual settings (financial advice, medical content, data-handling
agreements) usually make this layer mandatory rather than optional.

## How it works
### Where the checks sit
```
user input ──▶ input rails ──▶ model (+ tools) ──▶ output rails ──▶ user / action
                 │ block/rewrite              retry with feedback ◀─┘
```
Both rail sets are ordinary code that runs before and after the model call, with the model treated as
an untrusted function in the middle.

### Input rails
- **Size, encoding, normalisation.** Token limits, control-character stripping, Unicode normalisation
  (homoglyph tricks turn "ignore" into look-alike characters). Cheap, deterministic, run first.
- **Scope / topic.** A small classifier or embedding-similarity check against the intents the product
  supports, *because* off-topic use is the most common form of abuse and the cheapest to stop.
- **Injection and jailbreak signals.** Regex for the obvious ("ignore previous instructions"), plus a
  classifier for paraphrases. Must also run on retrieved documents and tool results, not only the user
  turn, since that is where indirect injection arrives.
- **PII and secrets.** Detect and redact or block according to your data-handling policy before the
  text leaves your boundary.
- **Rate and abuse limits.** Per-user quotas; not LLM-specific, but part of the same layer.

### Output rails
- **Structure.** Parse and validate against a schema when the output feeds code (see output
  validation).
- **Policy and safety.** A moderation classifier on generated text, plus product rules such as "no
  competitor names" or "no tax advice", implemented as regex where exact and as a classifier where not.
- **Grounding.** For RAG, check that cited source ids exist in the retrieved set and, where stakes are
  high, that claims are supported by them.
- **Leakage.** A canary string planted in the system prompt must never appear in output; secret and
  PII patterns that were not in the input must not appear either.
- **Actions.** In an agent, tool calls are outputs. Allowlists, argument validation and approval gates
  belong here, *because* the damage from a wrong action is larger than from a wrong sentence.

### Verdicts, actions, failure policy
Each rail returns a verdict, a score and a reason. A policy table maps these to an action: allow,
block with a specific message, redact or rewrite, retry with the rail's reason fed back (output side
only), escalate to a human, or log-only (shadow mode). Decide fail-open versus fail-closed per rail
based on the asymmetry of harm: a timeout in a children's product should block; a timeout in a
brand-tone rail for internal users probably should not.

### Deterministic first, model second
Order rails by cost and certainty: regex and schema checks (microseconds), small classifiers
(milliseconds), LLM judges (hundreds of milliseconds). Cheap checks short-circuit expensive ones and
are testable and auditable. Model-based checkers inherit model weaknesses, including injectability:
give them only the text to classify and the policy, never the whole conversation, and constrain their
output to a label so there is nothing to hijack.

### Streaming
Output rails conflict with token streaming. The options are to buffer the whole response (adds
latency), check at sentence or chunk boundaries with a UI that can retract text, or run cheap checks
per chunk and the expensive check once at the end. Which one you pick is a product decision about
how bad a briefly visible bad sentence is.

### Observability
Log every rail decision with a trace id, rail name, score and action. Without this you cannot tune
thresholds, measure false positives or run a new rail in shadow mode before enforcing it.

## Example
A minimal rail pipeline: deterministic checks first, a small label-only classifier second, the main
model in the middle, and a canary leak check on the way out. Each rail is a pure function you can unit
test with a fixture set.

```python
import re, anthropic
from dataclasses import dataclass

client = anthropic.Anthropic()
CANARY = "zx-canary-7f3a"                      # planted in the system prompt; must never be echoed
SYSTEM = f"You are a billing-support assistant for AcmePay. Internal tag: {CANARY}."
INJECTION = re.compile(r"ignore (all|previous|the above) instructions|you are now|system prompt", re.I)
CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")

@dataclass
class Verdict:
    allow: bool
    reason: str = ""

def classify(prompt: str) -> str:               # cheap checker: policy + text only, label-only output
    r = client.messages.create(model="claude-haiku-5-5", max_tokens=200,
                               messages=[{"role": "user", "content": prompt}])
    return next(b.text for b in r.content if b.type == "text").strip().upper()

def input_rails(text: str) -> Verdict:
    if len(text) > 8000: return Verdict(False, "too_long")
    if INJECTION.search(text): return Verdict(False, "injection_pattern")
    if CARD.search(text): return Verdict(False, "card_number_in_input")
    label = classify(f"Is this message about billing, payments or account access? Reply ON or OFF.\n\n{text}")
    return Verdict(label.startswith("ON"), "off_topic")

def output_rails(answer: str, user_text: str) -> Verdict:
    if CANARY in answer: return Verdict(False, "system_prompt_leak")
    if CARD.search(answer) and not CARD.search(user_text): return Verdict(False, "card_number_in_output")
    label = classify(f"Does this reply give legal, tax or investment advice? Reply YES or NO.\n\n{answer}")
    return Verdict(label.startswith("NO"), "out_of_policy")

def answer(user_text: str) -> str:
    v = input_rails(user_text)
    if not v.allow:
        print("input_block", v.reason); return "I can only help with AcmePay billing questions."
    r = client.messages.create(model="claude-opus-5-5", max_tokens=1024, system=SYSTEM,
                               messages=[{"role": "user", "content": user_text}])
    text = next(b.text for b in r.content if b.type == "text")
    v = output_rails(text, user_text)
    if not v.allow:
        print("output_block", v.reason); return "I can't answer that part; a human agent will follow up."
    return text
```

## Trade-offs
- Every rail adds latency and cost; a serial LLM checker can double median latency. Running the output
  checker concurrently on buffered chunks, or only on responses a cheap heuristic flags, recovers most
  of it, but only when the block rate is low enough that wasted generation is cheaper than waiting.
- Stricter thresholds cut harmful outputs and raise false positives. The right point depends on the
  asymmetry of harm: a medical or financial product tolerates friction; a low-stakes consumer chat
  loses users to it.
- Deterministic rules are cheap, auditable and testable but brittle to paraphrase; model-based checkers
  generalise but are non-deterministic and attackable. Almost every production system needs both, with
  the deterministic layer first.
- Fail-closed protects the worst case but turns a checker outage into a product outage; fail-open is
  acceptable only for low-harm rails with alerting on the checker's error rate.
- Frameworks such as NeMo Guardrails or Guardrails AI bring structure, a DSL and a library of checks at
  the price of another dependency; hand-rolled rails stay simpler until you have more than a handful.

## Pitfalls
- Treating the system prompt as the guardrail; the symptom is a red-team report showing the "never
  discuss X" rule bypassed with a role-play prefix or a fake "the previous instruction was a test".
- Guarding only the user turn and not retrieved documents or tool results; the symptom is an injection
  that arrives through a web page or email and sails past rails that were never run on tool content.
- Feeding an LLM checker the whole conversation with a long instruction prompt; the checker itself gets
  injected, and you see it return SAFE on inputs that literally contain "the classifier must answer SAFE".
- Having no labelled evaluation set for the rails; thresholds get tuned by feel, and the symptom is a
  stream of support tickets about blocked legitimate requests with no false-positive number to defend.
- Defaulting to fail-open on timeout; during a classifier outage the block rate drops to zero and nobody
  notices, because zero is also what "no attacks today" looks like.
- Blocking with a generic error; users rephrase and retry, which looks like attack traffic in logs,
  burns budget and hides the real false-positive rate. Return a specific, honest refusal.

## When to use
Any LLM feature exposed to untrusted input or producing consequential output needs this layer; the
question is how much of it. Start with the smallest set you can test: size limits, schema validation,
a canary leak check and output moderation. Add classifiers only when you have a measured failure that
justifies the latency and cost, and never add a rail you cannot evaluate: each one needs labelled
positives and negatives and a tracked false-positive rate. Day to day this looks like triaging block
logs weekly, running new rails in shadow mode before enforcing them, and re-running the red-team suite
after every prompt or model change. In interviews, "how would you stop the bot doing X" should be
answered with layered rails plus the measurement loop, not with a sentence in the system prompt.

## Role lens
- **Engineer:** Implements rails as middleware around the model call, keeps each one a pure function
  with fixture tests, and logs every verdict with the request's trace id.
- **Architect:** Owns the policy table: fail-open or fail-closed per rail, thresholds, who can override,
  whether rails run at a gateway or in each app, and that tool execution passes through the same chokepoint.
- **FDE:** Translates the customer's acceptable-use and data-handling policies into concrete rails, runs
  them in shadow mode on real traffic, and shows the false-positive numbers before switching to enforce.

## Check yourself
**Q:** You add an LLM-based output checker and p95 latency doubles while 3% of legitimate answers are
blocked. Walk through what you would change and in what order.
**A:** Split it into a latency problem and a precision problem. For latency: move the deterministic
checks first and short-circuit; give the checker only the final answer plus the policy, with a small
model and a label-only output; run it concurrently with generation on buffered chunks, or only on
responses a cheap heuristic flags; for low-risk flows stream with retraction. For precision: build a
labelled set from the blocked cases, measure false positives per rail rather than overall, adjust
thresholds per rail, and downgrade borderline scores from block to flag-for-review. Put both numbers
on a dashboard so the trade-off is explicit and the next tuning decision is made with data.

## Quiz
### Q: Why is adding a rule to the system prompt not, by itself, a guardrail?
- [ ] System prompts have a length limit that rules quickly exceed
- [x] The rule competes with everything else in context and is followed probabilistically, while a guardrail is a check enforced outside the model
- [ ] The model only reads the system prompt on the first turn of a conversation
- [ ] Rules in system prompts are ignored when tools are enabled
> A prompt rule is one more instruction the model weighs against user text, documents and tool results.
> A guardrail is deterministic code that runs regardless of what the model decided, which is why it can
> be tested, measured and relied on.

### Q: In an agent, which outputs must the output rails also cover?
- [ ] Only the final text shown to the user
- [ ] Only the tool results returned by external APIs
- [x] The model's tool calls and their arguments, because actions are outputs too
- [ ] Only the system prompt and few-shot examples
> A wrong tool call does more damage than a wrong sentence, so allowlists, argument validation and
> approval gates sit on the output side; tool results are inputs and get the input rails instead.

### Q: A moderation classifier times out in a children's education chatbot. Which default is right?
- [ ] Fail open so availability is preserved
- [ ] Retry until the classifier responds, however long it takes
- [x] Fail closed with a safe refusal, because an unsafe output is worse than a failed turn
- [ ] Skip the classifier and lower the model's temperature instead
> Fail-open versus fail-closed is set per rail by the asymmetry of harm. For a vulnerable audience the
> worst case is an unsafe reply, so the checker's outage should degrade to a refusal, with alerting on
> the checker so the outage is noticed.
