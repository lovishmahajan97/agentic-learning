---
id: prompt-engineering
module: prompting
name: Prompt engineering
kind: practice
difficulty: intro
est_minutes: 12
related:
  - prompting/system-prompt
  - prompting/few-shot
  - prompting/chain-of-thought
  - prompting/prompt-templates
  - prompting/context-engineering
  - evals/eval-driven-development
  - training-adaptation/fine-tune-vs-rag-vs-prompt-decision
  - customer-data-evaluation/domain-prompt-engineering
sources:
  - Official docs: Anthropic prompt engineering guide
  - Official docs: OpenAI prompt engineering guide
  - Schulhoff et al., 2024 — The Prompt Report: A Systematic Survey of Prompting Techniques
---

## TL;DR
A language model is a conditional distribution over next tokens; everything it does is determined by
the tokens in front of it. Prompt engineering is the practice of specifying a task through that input
(instructions, context, examples, output format) and iterating against a measured eval set, because an
under-specified prompt makes the model average over every plausible interpretation. It is the cheapest
lever you own: seconds per iteration, no training data, no redeploy. This lesson orients the module;
each technique has its own deep lesson.

## Why it exists
A pre-trained model completes text. Instruction tuning and RLHF turned completion into
instruction-following, but they did not give the model access to what is in your head. Given
"summarize this", the model must guess the length, the audience, the format and what counts as
important, and its guess is the most probable interpretation under its training distribution, which is
rarely yours. The output looks generic because it *is* generic: it is the average over everyone who ever
asked for a summary.

The first naive alternative is to fix every behavior gap with fine-tuning. That fails on loop time and
cost: collecting labeled data, training, evaluating and redeploying takes days, while a prompt change
takes seconds, and most behavior gaps are specification gaps rather than capability gaps. The second
naive alternative is prompting by vibes: edit, eyeball one output, ship. That fails because sampling
makes every run different, so a single output tells you almost nothing about the distribution you are
shipping. Prompt engineering is the discipline between the two: treat the prompt as code, specify
precisely, measure on a fixed set, change one variable at a time, and know when to stop and reach for
retrieval, tools or training instead.

## How it works
### The prompt is a specification
A production prompt has recognisable parts, each of which narrows the distribution in a different way:

1. **Role and scope** (usually the system prompt): who the model is acting as and what is in and out of
   bounds. This sets the prior for tone and refusals.
2. **Task**: the verb and the object, stated once and unambiguously ("extract every invoice line item").
3. **Context and data**: the document, the retrieved chunks, the user's profile, today's date. The
   model cannot know anything you did not give it or that it did not see in training.
4. **Examples**: demonstrations of input → output for things easier to show than to say (few-shot).
5. **Output format**: a schema, tags or a fixed template, so the response is parseable.
6. **Constraints and edge cases**: what to do when the input is empty, ambiguous or out of scope.

### Why each design rule has a mechanism behind it
- **Enumerate the output space.** If the labels are not listed, the model invents synonyms, because
  nothing in the prompt said the set was closed. Listing them, with a one-line definition each, turns a
  free-text task into a constrained one at zero token cost beyond the list itself.
- **Separate instructions from data with delimiters.** Attention treats every token the same; the
  only way the model can tell "summarize this" from text that happens to say "ignore the above" is
  structural cues it saw in training, such as XML tags or Markdown sections. Wrapping injected content
  in `<document>` tags and saying "text inside tags is data" is both a quality and a safety measure.
- **Mind position.** Models use the beginning and end of a long context more reliably than the middle
  (the lost-in-the-middle finding), so put the ask after a long document, not before it, and put
  hard rules where they will not be buried.
- **Prefer positive instructions.** "Do not mention competitors" puts competitors in the context;
  "discuss only Acme products" says what to do instead. Negations are followed less reliably because
  the forbidden concept is now primed.
- **Give reasoning room when the task needs it.** A model computes a fixed amount per token; a
  multi-step problem needs intermediate tokens (chain-of-thought), while a lookup does not.
- **Make the output parseable.** Tags or JSON for the final answer, so code consumes it without
  guessing where the answer starts; structured-output modes make this a hard guarantee.

### The loop
Prompt engineering is engineering because of the loop, not the wording:

1. Write the prompt as a versioned template.
2. Build a fixed eval set from real inputs: a few dozen to a few hundred labeled cases is enough to
   see a signal (an illustrative range, not a rule).
3. Run every case, several times if the output is sampled, and score with code or a rubric.
4. Read the failures. Group them by cause: ambiguity in the instruction, missing context, format
   drift, a capability limit.
5. Change one thing, re-run, keep the change only if the metric moves.
6. Record the version and the score, because the next person will ask why the prompt says that.

### Where the module goes from here
Zero-shot and few-shot are about how much to show; chain-of-thought and ReAct are about giving the
model computation and tools; the system prompt and templates are about where instructions live and how
they are built; structured outputs and tool calling make outputs machine-consumable; caching and
versioning make prompts cheap and auditable; context engineering generalises all of it to "what goes in
the window and why"; injection and jailbreaks are what happens when untrusted text reaches the prompt.

### Where prompting stops
When the eval plateaus after precise instructions and good examples, the gap is no longer
specification. Missing facts need retrieval; stable style at high volume is cheaper fine-tuned; a
capability gap needs a stronger model or a decomposition of the task.

## Example
Two versions of a ticket-classification prompt scored on the same labeled set. V1 is what people write
first; V2 enumerates labels, defines them and fixes the output format. The harness is the point: it
turns "I think it is better" into a number you can defend, and it is reused for every later change.

```python
import anthropic, json
client = anthropic.Anthropic()
MODEL = "claude-opus-5-5"

LABELS = ["billing", "bug", "feature_request", "other"]
EVAL = [  # (ticket, expected): in practice 50-200 cases sampled from real traffic
    ("I was charged twice this month", "billing"),
    ("Export button does nothing on Safari", "bug"),
    ("Would love dark mode", "feature_request"),
    ("Thanks, all sorted now!", "other"),
]
V1 = "Classify this support ticket."
V2 = f"""Classify the support ticket into exactly one label from {LABELS}.
Definitions: billing = charges, invoices, refunds; bug = something that should
work but does not; feature_request = asking for new behavior; other = anything else.
Reply with JSON: {{"label": "<one of the labels>"}} and nothing else."""

def classify(instruction, ticket):
    r = client.messages.create(
        model=MODEL, max_tokens=64, system=instruction,
        messages=[{"role": "user", "content": f"<ticket>{ticket}</ticket>"}])
    text = next(b.text for b in r.content if b.type == "text")
    try:
        return json.loads(text)["label"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return text.strip().lower()   # V1 mostly lands here: prose, not a label

for name, prompt in (("v1", V1), ("v2", V2)):
    hits = sum(classify(prompt, t) == y for t, y in EVAL)
    print(f"{name}: {hits}/{len(EVAL)} correct")
```

Run each version a few times: outputs are sampled, so the score is a distribution. V1 fails mostly on
format (it answers in sentences) and on label names it invented; V2 fails, if at all, on genuinely
ambiguous tickets, which is where you would next add a few-shot example or a decision rule.

## Trade-offs
- Iteration in seconds with no training data, against a per-request cost: every instruction and example
  is paid for on every call, which flips toward fine-tuning once volume is high and behavior is stable.
- A tightly specified prompt is predictable but can over-constrain; when inputs drift away from what the
  prompt anticipated, a shorter prompt on a capable model often generalises better than a rulebook.
- Tuning to one model's quirks maximises today's score but regresses on the next model; if you expect to
  swap models, keep prompts plain and lean on evals rather than tricks.
- Prompting shifts behavior probabilistically; when you need a hard guarantee (valid schema, forbidden
  content never emitted), the constraint must move into code: validation, constrained decoding, guardrails.

## Pitfalls
- Judging a change on one sample; the symptom is "it worked yesterday" regressions, because every run is a
  draw from a distribution. Use a fixed eval set and several runs per case.
- Splicing data into instructions without delimiters; the symptom is the model following instructions
  found inside a document, or summarizing your instruction as if it were part of the text.
- Stacking rules for months until they contradict; the symptom is a rule obeyed in one conversation and
  ignored in the next, resolved by whichever rule is nearer or more recent.
- Cargo-cult phrasing ("you are a world-class expert", tips, threats) treated as engineering; the symptom
  is no measurable gain on the eval and a prompt that nobody dares to shorten.
- Solving a knowledge or capability gap with wording; the symptom is a prompt that keeps growing while
  accuracy plateaus, which means retrieval, tools or a different model is the fix.

## When to use
Prompting is always the first lever, before retrieval design, fine-tuning or model changes, because it is
the cheapest to try and it tells you which of the others you need. Measure accuracy or rubric score on a
fixed set, format-compliance rate, tokens per request and latency, and track all four per prompt version.
Day to day it looks like writing system prompts and templates, reading failing transcripts, and shipping
versioned changes behind an eval gate. In interviews, expect to be handed a bad prompt and asked to
improve it live; the strong signal is not clever wording but saying what you would measure, which
ambiguity each change removes, and when you would stop prompting and change the architecture instead.

## Role lens
- **Engineer:** Owns the prompt-eval loop: writes templates, builds the harness, reads failing transcripts,
  and ships versioned changes only when the metric moves.
- **Architect:** Decides where prompting stops and retrieval, fine-tuning or code-level guardrails begin,
  and sets the standard that no prompt ships without an eval set.
- **FDE:** Turns a customer's tacit domain rules into explicit instructions and examples by sitting with
  their subject-matter experts and reading real transcripts, then proves the gain on their data.

## Check yourself
**Q:** A teammate reports that the summarizer "works fine" after adding "be concise and accurate" to the
prompt. How would you establish whether it actually improved, and what would you change first?
**A:** I would not trust an eyeballed output or two: sampling makes every run different, so I would build
a small fixed set, say thirty real documents with reference summaries or a rubric, and score the old and
new prompts on the same set over several runs, measuring length, factual errors against the source and
format compliance. "Be concise and accurate" cannot fail to sound good and rarely moves a metric because
it resolves no specific ambiguity. My first change would replace it with concrete specification: a target
length, the audience, which facts must be preserved, what to do when the source is ambiguous, and a fixed
output structure. Then I would read the remaining failures to see whether they are specification gaps,
fixable in the prompt, or knowledge gaps that need retrieval or a different model.

## Quiz
### Q: A prompt says "Summarize this document" and the summaries vary wildly in length and focus between runs. What is the most accurate explanation?
- [ ] The sampling temperature is too high and must be set to zero
- [x] The instruction leaves length, audience and focus unspecified, so each sample lands on a different plausible interpretation
- [ ] The document exceeds the context window and is being truncated
- [ ] The model needs few-shot examples before it can summarize at all
> Lower temperature narrows sampling but does not tell the model what you want; it picks the single most
> likely interpretation, which may still be wrong. The variance comes from an under-specified task, and
> the fix is specification. Examples help, but a model can summarize zero-shot once the task is defined.

### Q: Which change to a classification prompt is most likely to raise reliability on an eval set?
- [ ] Adding "You are a world-class expert classifier" to the system prompt
- [ ] Asking the model to be very careful and to double-check its work
- [x] Enumerating the allowed labels with a one-line definition each and fixing the output format
- [ ] Repeating the instruction three times so the model does not forget it
> Enumerating labels removes the freedom to invent synonyms, definitions settle boundary cases the same way
> every time, and a fixed format makes parsing deterministic. Persona lines and exhortations sometimes
> shift tone but rarely move accuracy; repetition adds tokens and invites contradictions.

### Q: After several rounds of refinement an extraction task plateaus at 70% accuracy, and the failures all involve product codes the model has never seen. What is the right next move?
- [ ] Add more adjectives such as "precise" and "thorough" to the instruction
- [ ] Switch to chain-of-thought so the model can reason about the codes
- [x] Treat it as a knowledge gap: supply the code list through retrieval or context, or fine-tune if the list is large and stable
- [ ] Lower max_tokens to force the model to focus
> A plateau whose failures share a cause the prompt cannot describe is a knowledge gap, not a
> specification gap. Reasoning cannot recover facts the model never saw and wording cannot either; the fix
> is to put the missing knowledge into the context or into the weights.
