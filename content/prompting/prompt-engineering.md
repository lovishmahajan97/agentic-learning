---
id: prompt-engineering
module: prompting
name: Prompt engineering
kind: practice
difficulty: intro
est_minutes: 12
status: crisp
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
Prompt engineering specifies a task through the model's input (instructions, context, examples, output
format) and iterates against a measured eval set. It is the cheapest lever you own: seconds per
iteration, no training data, no redeploy.

## Why it exists
- **A model only sees its tokens.** A language model is a conditional distribution over next tokens;
  everything it does is determined by the tokens in front of it. An under-specified prompt makes it
  average over every plausible interpretation.
- **Instruction tuning is not mind reading.** Instruction tuning and RLHF turned completion into
  instruction-following, but given "summarize this" the model still guesses length, audience, format and
  importance. Its guess is the most probable one under its training distribution, rarely yours: the output
  looks generic because it *is* the average over everyone who ever asked for a summary.
- **Fine-tuning every gap is too slow.** Collecting labeled data, training, evaluating and redeploying
  takes days, while a prompt change takes seconds. Most behavior gaps are specification gaps, not
  capability gaps.
- **Prompting by vibes does not work either.** Edit, eyeball one output, ship fails because sampling makes
  every run different, so one output says almost nothing about the distribution you ship.
- **The discipline in between.** Treat the prompt as code: specify precisely, measure on a fixed set,
  change one variable at a time, and know when to stop and reach for retrieval, tools or training. This
  lesson orients the module; each technique has its own deep lesson.

## How it works
### The prompt is a specification
A production prompt has recognisable parts, each narrowing the distribution in a different way:

1. **Role and scope** (usually the system prompt): who the model is acting as and what is in and out of
   bounds. This sets the prior for tone and refusals.
2. **Task**: the verb and the object, stated once and unambiguously ("extract every invoice line item").
3. **Context and data**: the document, the retrieved chunks, the user's profile, today's date. The model
   cannot know anything you did not give it or that it did not see in training.
4. **Examples**: demonstrations of input → output for things easier to show than to say (few-shot).
5. **Output format**: a schema, tags or a fixed template, so the response is parseable.
6. **Constraints and edge cases**: what to do when the input is empty, ambiguous or out of scope.

### Each design rule has a mechanism behind it
| Rule | Because |
|---|---|
| **Enumerate the output space** | Unlisted labels invite invented synonyms, since nothing said the set was closed. A list with one-line definitions turns free text into a constrained task at almost no token cost. |
| **Separate instructions from data with delimiters** | Nothing in the architecture marks a token as instruction or data; it is one sequence. Only learned structural cues (XML tags, Markdown sections) tell "summarize this" apart from text saying "ignore the above". |
| **Mind position** | Models use the start and end of a long context more reliably than the middle (lost in the middle). Put the ask after a long document and keep hard rules where they will not be buried. |
| **Prefer positive instructions** | "Do not mention competitors" puts competitors in the context; "discuss only Acme products" says what to do instead. |
| **Give reasoning room when needed** | A model computes a fixed amount per token; a multi-step problem needs intermediate tokens (chain-of-thought), a lookup does not. |
| **Make the output parseable** | Tags or JSON for the final answer let code consume it without guessing where the answer starts; structured-output modes make this a hard guarantee. |

- **Delimiters are also safety.** Wrapping injected content in `<document>` tags and saying "text inside
  tags is data" is both a quality and a safety measure.
- **Position effects vary.** Newer long-context models show lost-in-the-middle less strongly, so measure
  on yours.
- **Negation is a default, not a law.** Vendor guides recommend positive phrasing because bare negations
  are often followed less reliably and give no alternative behaviour; the effect size is model-dependent,
  so test it.

### The loop
Prompt engineering is engineering because of the loop, not the wording:

1. **Write** the prompt as a versioned template.
2. **Build** a fixed eval set from real inputs: a few dozen to a few hundred labeled cases is enough to see
   a signal (an illustrative range, not a rule).
3. **Run** every case, several times if the output is sampled, and score with code or a rubric.
4. **Read** the failures and group them by cause: ambiguity in the instruction, missing context, format
   drift, a capability limit.
5. **Change one thing**, re-run, and keep the change only if the metric moves.
6. **Record** the version and the score, because the next person will ask why the prompt says that.

### How big a difference is real
- **The noise is computable.** For accuracy *p* on *n* cases, the standard error is √(p(1−p)/n).
- **Small sets hide small gains.** At 80% on 50 cases that is √(0.16/50) ≈ 0.057, about ±6 points, so
  moving from 80% to 84% has shown you nothing yet. At 200 cases the error halves to about ±3.
- **Use a paired test.** Both versions run on the same cases, so count the cases that flipped. If v2 fixes
  nine and breaks two, that is evidence; if it fixes five and breaks four, it is a reshuffle.
- **Read the broken cases.** That catches the commonest regression: a new rule that fixes one class of
  input by damaging another.

### Where the module goes from here
| Lessons | What they are about |
|---|---|
| Zero-shot, few-shot | How much to show |
| Chain-of-thought, ReAct | Giving the model computation and tools |
| System prompt, templates | Where instructions live and how they are built |
| Structured outputs, tool calling | Making outputs machine-consumable |
| Caching, versioning | Making prompts cheap and auditable |
| Context engineering | Generalising all of it to "what goes in the window and why" |
| Injection, jailbreaks | What happens when untrusted text reaches the prompt |

### Where prompting stops
When the eval plateaus after precise instructions and good examples, the gap is no longer specification:
- **Missing facts** need retrieval.
- **Stable style at high volume** is cheaper fine-tuned.
- **A capability gap** needs a stronger model or a decomposition of the task.

## Diagram
The loop is the engineering: every change is scored on the same fixed set, and a plateau sends you out of
prompting to retrieval, fine-tuning or a different model.

```mermaid
flowchart TD
    A["Versioned prompt template"] --> B["Run fixed eval set, several runs"]
    B --> C["Score with code or rubric"]
    C --> D["Read and group failures"]
    D --> E{"Cause of failures?"}
    E -->|"spec gap"| F["Change one thing"]
    F --> B
    E -->|"missing facts"| G["Retrieval or tools"]
    E -->|"capability gap"| H["Stronger model or decompose"]
    E -->|"stable style, high volume"| I["Fine-tune"]
    C -->|"metric moved"| J["Record version and score, ship"]
```

## Examples
### Scoring two prompt versions with a harness
Two versions of a ticket-classification prompt scored on the same labeled set. V1 is what people write
first; V2 enumerates labels, defines them and fixes the output format. The harness turns "I think it is
better" into a number you can defend, and it is reused for every later change.

```python
import anthropic, json, collections
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
        model=MODEL, max_tokens=1024, system=instruction,  # headroom: any thinking counts
        messages=[{"role": "user", "content": f"<ticket>{ticket}</ticket>"}])
    text = next((b.text for b in r.content if b.type == "text"), "")  # no text block -> miss
    try:
        return json.loads(text)["label"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None                       # unparseable: a format failure, not a wrong label

RUNS = 3                                  # sampled outputs: score a distribution, not one draw
for name, prompt in (("v1", V1), ("v2", V2)):
    outcome = collections.Counter()
    for ticket, expected in EVAL:
        for _ in range(RUNS):
            got = classify(prompt, ticket)
            outcome["format" if got is None else "invented" if got not in LABELS
                    else "correct" if got == expected else "wrong"] += 1
    print(name, dict(outcome))            # each failure bucket points to a different fix
```

- **The breakdown is the point, not the total.** V1 lands almost entirely in `format` (it answers in
  sentences) and `invented` (it says "payment issue" for billing). Both are specification failures that
  V2's format clause and closed label list remove.
- **What remains is a boundary.** Whatever V2 still gets `wrong` is a case the definitions do not settle,
  such as "charged twice because the export failed"; a decision rule or a few-shot example goes there next.
- **Four cases are anecdotes.** Run the same harness on a few hundred real tickets and apply the paired
  comparison before believing a delta.

### Worked scenario: is the new prompt actually better?
A team reran the harness on 200 real tickets, one run each (illustrative numbers).

| Quantity | v1 | v2 |
|---|---|---|
| Correct | 160 (80%) | 167 (83.5%) |
| Standard error √(p(1−p)/n) | √(0.16/200) ≈ 0.028, about ±3 points | similar |
| Cases v2 fixed vs v1 | | 9 |
| Cases v2 broke vs v1 | | 2 |

- **Unpaired, it is borderline.** A 3.5-point gain sits near one standard error, so comparing totals alone
  is weak evidence.
- **Paired, it is a clear signal.** Seven net flips, with 9 fixes against 2 breaks, is a consistent
  direction on identical inputs.
- **Then read the 2 breaks.** If both are refunds now labeled `other`, the new definition of billing is
  too narrow; fix it before shipping, not after.

## Production patterns
- **Prompts as versioned templates in the repo.** Store each prompt as a template file next to the code,
  reviewed in pull requests and tagged with a version string. Log that version on every request so a
  regression in metrics maps to a specific prompt change.
- **Eval gate in CI.** On any change to a prompt file, run the fixed eval set and fail the build if the
  primary metric drops or format compliance falls below a threshold. Monitor the per-version score history
  and the count of flipped cases.
- **Delimit untrusted input.** Wrap documents, tickets and retrieved chunks in tags such as `<document>`
  and state that tagged text is data. Monitor transcripts where the model quotes or obeys text from inside
  the tags.
- **Hard guarantees in code.** When the output must parse, use the provider's structured-output or
  tool-use schema mode plus validation in code, and retry or fall back on failure. Monitor parse-failure
  and retry rates per prompt version.
- **Regression run on model upgrades.** Before switching model versions, rerun every prompt's eval set
  on the new model. Prompts tuned to one model's quirks often regress on the next; track score deltas per
  prompt.

## Trade-offs
- **Fast iteration vs per-request cost.** Iteration takes seconds with no training data, but every
  instruction and example is paid for on every call, which flips toward fine-tuning once volume is high and
  behavior is stable.
- **Predictability vs over-constraint.** A tightly specified prompt is predictable but can over-constrain;
  when inputs drift away from what the prompt anticipated, a shorter prompt on a capable model often
  generalises better than a rulebook.
- **Today's score vs portability.** Tuning to one model's quirks maximises today's score but regresses on
  the next model; if you expect to swap models, keep prompts plain and lean on evals rather than tricks.
- **Probabilistic vs guaranteed.** Prompting shifts behavior probabilistically; when you need a hard
  guarantee (valid schema, forbidden content never emitted), the constraint must move into code:
  validation, constrained decoding, guardrails.

## Pitfalls
- **Judging a change on one sample.** Symptom: "it worked yesterday" regressions, because every run is a
  draw from a distribution. Fix: a fixed eval set and several runs per case.
- **Splicing data into instructions without delimiters.** Symptom: the model follows instructions found
  inside a document, or summarizes your instruction as if it were part of the text. Fix: tags and a "text
  inside tags is data" rule.
- **Stacking rules for months until they contradict.** Symptom: a rule obeyed in one conversation and
  ignored in the next, resolved by whichever rule is nearer or more recent. Fix: prune and reconcile rules
  against the eval.
- **Cargo-cult phrasing treated as engineering.** "You are a world-class expert", tips and threats.
  Symptom: no measurable gain on the eval and a prompt that nobody dares to shorten.
- **Solving a knowledge or capability gap with wording.** Symptom: a prompt that keeps growing while
  accuracy plateaus, which means retrieval, tools or a different model is the fix.

## When to use
- **Always first.** Prompting comes before retrieval design, fine-tuning or model changes, because it is
  the cheapest to try and it tells you which of the others you need.
- **Measure:** accuracy or rubric score on a fixed set, format-compliance rate, tokens per request and
  latency, and track all four per prompt version.
- **Day to day:** writing system prompts and templates, reading failing transcripts, and shipping
  versioned changes behind an eval gate.
- **Interviews:** expect to be handed a bad prompt and asked to improve it live. The strong signal is not
  clever wording but saying what you would measure, which ambiguity each change removes, and when you
  would stop prompting and change the architecture instead.

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

## Interview questions
### Why does an under-specified prompt produce generic output, and how do you fix it?
- **The model averages.** It is a conditional distribution over next tokens, so an ambiguous ask lands on
  the most probable interpretation across everyone who ever asked something similar.
- **Fix with specification, not adjectives.** State length, audience, which facts matter, the output
  format and what to do on empty or ambiguous input; each clause removes one dimension of guessing.

### Your v2 prompt scores 84% against v1's 80% on 50 cases. Do you ship it?
- **Not on that evidence.** The standard error at 80% on 50 cases is about ±6 points, so a 4-point gain is
  inside the noise.
- **Run a paired comparison.** Count cases v2 fixed and broke on the same inputs; nine fixed and two broken
  is evidence, five and four is a reshuffle.
- **Grow the set.** At 200 cases the error halves to about ±3, and reading the broken cases catches rules
  that damage another input class.

### How do you decide whether a failure needs a prompt change, retrieval, or fine-tuning?
- **Group failures by cause.** Ambiguous instruction or format drift is a specification gap, fixed in the
  prompt.
- **Shared missing facts mean a knowledge gap.** Identifiers or documents the model never saw need
  retrieval or a lookup tool; wording and reasoning cannot recover them.
- **Stable style at high volume is a cost question.** Fine-tuning removes per-call instruction tokens once
  behavior is stable; a capability gap needs a stronger model or decomposition.

### When would you not rely on the prompt to enforce a rule?
- **When it must always hold.** Prompting shifts behavior probabilistically, so a valid schema or content
  that must never be emitted belongs in code: validation, constrained decoding, guardrails.
- **When untrusted text reaches the prompt.** Delimiters help the model separate data from instructions,
  but they are a learned cue, not an enforcement boundary.

### What would you log and monitor for a prompt in production?
- **Prompt version per request**, so metric shifts map to a specific change.
- **Quality and format metrics:** eval score per version, format-compliance or parse-failure rate.
- **Cost and speed:** tokens per request and latency, because every instruction and example is paid for
  on every call.

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
- [x] Treat it as a knowledge gap: supply the code list in the context, through retrieval, or via a lookup tool
- [ ] Lower max_tokens to force the model to focus
> A plateau whose failures share a cause the prompt cannot describe is a knowledge gap, not a
> specification gap. Reasoning cannot recover facts the model never saw and wording cannot either; the fix
> is to put the missing knowledge where the model can read it at request time. Fine-tuning is a poor way
> to inject a changing list of identifiers, because it teaches patterns more reliably than exact facts.
