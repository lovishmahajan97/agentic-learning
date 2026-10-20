---
id: domain-prompt-engineering
module: customer-data-evaluation
name: Domain prompt engineering
kind: practice
difficulty: core
est_minutes: 14
related:
  - prompting/prompt-engineering
  - prompting/few-shot
  - prompting/system-prompt
  - prompting/prompt-versioning
  - scoping/domain-modeling
  - customer-data-evaluation/golden-dataset-creation-with-client-smes
  - customer-data-evaluation/sme-review-and-annotation-guidelines
  - customer-data-evaluation/eval-review-loops
---

## TL;DR
Domain prompt engineering is turning one customer's tacit expertise (their vocabulary, policies,
exceptions and output conventions) into explicit instructions, definitions and examples a general model
can follow. It exists because a model knows the public version of a domain but not this customer's
definition of "late shipment" or "material change", and a persona line cannot supply that. The work is
elicitation and testing, not wordsmithing: every rule should trace to an SME decision and an eval case.

## Why it exists
A frontier model has read a great deal about insurance, logistics or clinical coding in general. What it
has never seen is the customer's internal policy manual, the exceptions their senior staff apply from
memory, or the fact that in this company "active account" means "billed in the last 90 days" rather than
"logged in recently". Those private definitions are exactly where an AI deployment wins or loses,
because the customer's experts judge outputs against them, not against textbook knowledge.

The naive approach is a persona prompt: "You are an expert claims adjuster with 20 years of experience."
It fails for a predictable reason. A persona changes tone and confidence, but it carries no information
the model did not already have, so the model fills gaps with the most common public interpretation. The
output sounds expert and is wrong in exactly the places the customer cares about, which is worse than
sounding uncertain. The second naive approach, pasting the entire 200-page policy manual into the
prompt, fails differently: manuals are written for humans who already share context, they contradict
themselves across versions, and they bury the three rules that decide most cases under fifty that
rarely matter. Domain prompt engineering sits between the two: extract the decision-relevant knowledge,
make it explicit and unambiguous, and prove each piece earns its place on real customer cases.

## How it works
### 1. Elicit the decision procedure, not the domain
Interview SMEs around concrete cases, not abstractions. Hand them ten real inputs and ask "what would you
do, and why?" People cannot list their own rules from memory, but they can explain a decision on a case
in front of them. Record each reason as a candidate rule along with the case that triggered it, because
the case later becomes an eval item that proves the rule works.

### 2. Write a glossary of contested terms
Most domain errors are definitional. List every term whose customer meaning differs from the common one,
define it operationally (what data field, what threshold, what time window), and state the default when
the input is ambiguous. A glossary entry like "Late: delivered after the promised window, where the
window is extended by any customer-requested reschedule" removes a whole class of disagreements. It is
written this way *because* the model resolves undefined terms toward the public meaning.

### 3. Order the rules as a procedure
Domain policies have precedence: regulatory rules override contract terms, which override house
preferences. Write rules in the order they should be checked and say explicitly which wins on conflict.
An unordered bullet list lets the model weight rules by how prominent they look, which is not the
customer's precedence.

### 4. Choose examples at the boundaries
Few-shot examples from the customer's own data teach format and judgement at once. Pick them where the
decision flips (just inside and just outside a threshold, the exception that overrides the default)
*because* easy central examples teach nothing the instructions did not already say. Keep examples out of
the golden set, or the eval measures memorisation.

### 5. Specify abstention and escalation
Domain work always has cases outside policy. Give the model a legitimate exit ("needs_review" with the
reason) and say when to use it. Without one, it is forced to guess, and guesses on out-of-policy cases
are where customers lose trust fastest.

### 6. Separate stable knowledge from per-request context
Policies and glossary go in a versioned system prompt; customer-specific facts that change per request
(the account, the contract, the retrieved clause) go in the user turn or via retrieval. When the domain
knowledge outgrows a prompt, move the long tail into retrieval and keep only the core procedure inline.

### 7. Test every change against the customer's cases
Each rule, glossary entry and example is a hypothesis. Run the prompt on the golden set after each
change, and review the failures with SMEs. A rule that does not move any eval case is either redundant or
untested; a rule that fixes three cases and breaks two needs rewording. Version the prompt per customer
so a regression can be traced to the change that caused it.

## Example
A freight customer wants shipment exceptions classified. The prompt is assembled from SME-owned parts so
each piece can be versioned and tested separately, and the eval reports which glossary terms appear in
failing cases.

```python
GLOSSARY = {
    "late": "Delivered after the promised window. The window moves if the consignee requested a "
            "reschedule (field reschedule_by == 'consignee'). Carrier reschedules do NOT move it.",
    "damaged": "Consignee noted damage on the POD, or a photo claim was filed within 48h of delivery.",
}
RULES = [  # checked in order; the first matching rule wins
    "1. If a hazmat flag is set and any exception exists, output needs_review (regulatory).",
    "2. If both late and damaged apply, output damaged (claims take priority over SLA credits).",
    "3. If the record is missing delivery_ts, output needs_review; never infer a delivery time.",
]
EXAMPLES = [  # boundary cases chosen by SMEs, excluded from the golden set
    ({"promised_end": "10:00", "delivery_ts": "10:20", "reschedule_by": "consignee",
      "new_end": "12:00"}, "on_time"),
    ({"promised_end": "10:00", "delivery_ts": "10:20", "reschedule_by": "carrier",
      "new_end": "12:00"}, "late"),
]

def build_prompt(version="acme-freight-v7"):
    terms = "\n".join(f"- {k}: {v}" for k, v in GLOSSARY.items())
    shots = "\n".join(f"Input: {x}\nLabel: {y}" for x, y in EXAMPLES)
    return (f"[prompt {version}]\nClassify the shipment as one of: on_time, late, damaged, "
            f"needs_review.\nDefinitions (use these, not general meanings):\n{terms}\n"
            "Rules, in priority order:\n" + "\n".join(RULES) + f"\nExamples:\n{shots}")

def failures_by_term(results):
    """results: list of dicts with gold, pred and the raw record."""
    counts = {t: 0 for t in GLOSSARY}
    for r in results:
        if r["gold"] != r["pred"]:
            for t in GLOSSARY:
                counts[t] += t in (r["gold"], r["pred"])
    return counts  # a term dominating failures means its definition needs SME review
```

The two examples differ only in who rescheduled, which is the exact point where the public meaning of
"late" and the customer's meaning diverge.

## Trade-offs
- Explicit rules make behaviour predictable and auditable, but each rule adds tokens and a chance of
  conflict; once rules number in the dozens, retrieval of the relevant subset usually beats inlining all.
- Customer-specific few-shot examples teach judgement fast, but they leak format quirks and can bias the
  label distribution; when examples skew toward one class, predictions skew with them.
- A per-customer prompt fits each client precisely, but multiplies maintenance; once three customers
  share most rules, factor a shared base prompt with customer overlays.
- Abstention ("needs_review") protects trust on out-of-policy cases, but raises the human workload; if
  the review queue exceeds what operations can staff, tighten the abstention criteria with SMEs.

## Pitfalls
- Writing rules from the policy manual instead of from cases; the symptom is a long prompt that scores
  no better than the short one, because the manual rules were not the ones deciding outcomes.
- Leaving a contested term undefined; the symptom is SMEs and model disagreeing on the same subset of
  cases over and over, all of which contain that word.
- Reusing few-shot examples inside the golden set; the symptom is an eval score that drops sharply when a
  fresh sample of production cases is labelled.
- Encoding one SME's personal preference as policy; the symptom is a second SME rejecting outputs the
  first one approved, which means the rule needs the policy owner's decision, not more prompting.

## When to use
Reach for domain prompt engineering whenever a general model's outputs are fluent but judged wrong by the
customer's experts, and the errors cluster around definitions, precedence or exceptions. It is the first
lever before retrieval or fine-tuning because it is cheap, reversible and inspectable. Move on when the
needed knowledge is too large or changes too often for a prompt (use retrieval), or when the behaviour
is a style or skill that instructions cannot pin down after several rounds (consider fine-tuning).
Measure with the customer's golden set and per-term failure counts. In interviews, show that you treat
the prompt as a tested artefact owned jointly with SMEs, not as clever phrasing.

## Role lens
- **Engineer:** Builds prompts from versioned glossary, rule and example components, and wires each change
  to a golden-set eval run with per-term failure breakdowns.
- **Architect:** Decides the boundary between shared base prompt, per-customer overlays and retrieved
  policy, so domain knowledge scales across clients without copy-paste drift.
- **FDE:** Runs case-based interviews with the customer's experts, extracts definitions and precedence,
  and gets the policy owner to resolve rules where SMEs disagree.

## Check yourself
**Q:** Your insurance-claims classifier scores well on public examples but the customer's adjusters reject
a third of its outputs. Adding "You are a senior adjuster" did nothing. What do you do next, and why?
**A:** The persona carried no new information, so the model is still applying public definitions. I would
pull the rejected outputs, sit with two adjusters and ask them to explain each rejection on the case.
I expect clusters around a few terms or exceptions, such as what counts as a pre-existing condition. I
would write operational definitions for those terms, order the rules by the customer's precedence, add
boundary examples from their data that are kept out of the golden set, and add a needs_review exit for
out-of-policy cases. Then I would re-run the golden set and check that the rejected clusters shrink
without new failures elsewhere. Where adjusters disagree with each other, I escalate to the policy owner.

## Quiz
### Q: Why does a persona prompt ("you are an expert in X") rarely fix domain errors on customer data?
- [ ] Models ignore system prompts that describe a role
- [x] It changes tone and confidence but adds none of the customer's private definitions or policies
- [ ] Personas only work with fine-tuned models
- [ ] It makes the prompt too long for the context window
> The errors come from missing information: the customer's own meanings and precedence rules. A persona
> adds no facts, so the model still falls back to public interpretations, only more confidently.

### Q: Where should few-shot examples for a domain prompt come from?
- [ ] Random central cases from the golden set, so the prompt and the eval agree
- [ ] Synthetic examples written by the engineer to avoid exposing customer data
- [x] Boundary cases from the customer's data where the decision flips, kept out of the golden set
- [ ] The longest and most complex cases, since easy cases need no help
> Boundary examples teach where the customer's rule diverges from the default. Taking them from the
> golden set would make the eval measure memorisation instead of generalisation.

### Q: Two SMEs consistently disagree about whether a case is "late". What is the right fix?
- [ ] Add more few-shot examples until the model matches the more senior SME
- [ ] Average their labels in the golden set
- [ ] Remove the cases from the eval because they are ambiguous
- [x] Escalate to the policy owner for an operational definition, then encode it in the glossary
> Persistent SME disagreement means the policy itself is undefined. Prompting cannot resolve it; only
> the person who owns the policy can, and the decision then becomes a glossary entry and eval cases.
