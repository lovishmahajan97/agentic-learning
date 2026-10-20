---
id: llm-as-a-judge
module: evals
name: LLM-as-a-judge
kind: pattern
difficulty: core
est_minutes: 16
related:
  - evals/judge-calibration-and-bias
  - evals/rubrics
  - evals/pairwise-comparison
  - evals/inter-annotator-agreement
  - evals/faithfulness-groundedness
  - evals/eval-harness
  - prompting/chain-of-thought
  - prompting/structured-outputs-json-mode
sources:
  - Zheng et al., 2023 — Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena
  - Liu et al., 2023 — G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment
  - Wang et al., 2023 — Large Language Models are not Fair Evaluators
---

## TL;DR
LLM-as-a-judge uses a model, given a rubric, the output to assess and usually a reference answer or a
second output, to produce a grade in place of a human reviewer. It exists because open-ended outputs
have no exact-match answer and human grading does not scale to hundreds of items per prompt
iteration. It is only as good as its agreement with human labels on your task, which must be measured,
because judges carry known biases (position, verbosity, self-preference) and grade confidently whether
or not they are right.

## Why it exists
A summarizer, a support reply or an agent's final report cannot be graded by string equality. The
string-overlap metrics inherited from machine translation (BLEU, ROUGE) reward wording, not
correctness, and correlate poorly with human judgment on open-ended tasks. Embedding similarity cannot
see that one number in a financial summary is wrong. Humans can, but a prompt iteration loop that
needs 300 outputs graded ten times a day is a full-time reviewer per engineer, and human grading drifts
over a long session anyway.

The practical gap is a grader that is cheap, fast, consistent and *good enough*, and a strong language
model with a clear rubric fills it. The evidence that it can work came from chat-quality comparisons:
with a capable judge and pairwise prompts, agreement with human preferences reached levels comparable
to human-human agreement in the MT-Bench study. The evidence that it can fail came from the same line
of work: judges prefer the answer in the first position, prefer longer answers, prefer their own
model family's style, and struggle to verify math and code by reading.

The naive alternative, "ask the model to rate it 1 to 10", fails because an undefined scale compresses
toward 7 or 8, varies with phrasing, and gives you a number you cannot act on or audit. The pattern is
therefore not "use an LLM to grade" but "design a grader, calibrate it against humans, and keep it
calibrated".

## How it works
### Modes
- **Pointwise with a rubric.** The judge sees one output and a rubric and returns pass/fail or a small
  scale. Binary verdicts per criterion are preferred *because* they are easier to calibrate and to
  act on than a 1–10 score, and because a rubric decomposed into criteria tells you *what* failed.
- **Reference-guided.** The judge also sees the golden answer and checks the output against it. This
  is far more reliable for correctness than unguided judging, *because* the judge no longer has to
  know the answer, only to compare.
- **Pairwise.** The judge sees outputs A and B and states a preference. Appropriate for relative
  comparisons (prompt v1 versus v2, model X versus Y), and mandatory to run in both orders *because*
  of position bias; a preference that flips when the order flips is a tie.
- **Reference-free quality checks** such as faithfulness: the judge checks each claim in an answer
  against retrieved context. Here the "reference" is the context, and the rubric is "supported, not
  supported, contradicted".

### Designing the judge prompt
State the task the output was supposed to do, the criterion in concrete terms with examples of pass and
fail, and the output format. Ask for a short justification *before* the verdict, *because* reasoning
first improves agreement with humans and produces an audit trail you can read when the judge is wrong.
Return structured output (JSON with `verdict` and `reason`) so the harness never parses prose. Keep each
judge single-purpose: one criterion per call, *because* a judge asked to assess accuracy, tone and
completeness at once blurs them into one score. Few-shot examples taken from human-labeled cases, kept
out of the calibration set, anchor the scale.

### Known biases and their mitigations
- **Position bias:** prefers the first (or second) candidate. Swap and require consistency.
- **Verbosity bias:** prefers longer, more detailed-looking answers. Instruct explicitly, and prefer
  binary criteria that length cannot satisfy.
- **Self-enhancement:** prefers outputs in its own style. Use a judge from a different model family
  than the generator, or at least check agreement by generator.
- **Surface anchoring:** rewards outputs that echo the reference's wording. Reference-guided rubrics
  should ask about facts, not phrasing.
- **Reasoning limits:** judges misgrade math and code by inspection. Use execution and tests there,
  and keep the judge for what cannot be executed.

### Calibration: the step that makes it an eval
Build a human-labeled set for the criterion (on the order of a hundred or two items, illustrative, with
both passes and failures represented). Run the judge, then compute agreement, Cohen's kappa (agreement
corrected for chance), and per-class recall. For a judge used as a gate, recall on *failures* is the
number that matters, *because* a judge that passes 95% of items while missing a quarter of real
failures is a leaky gate. Iterate the judge prompt on a dev split and confirm on a held-out split, the
same discipline as any model. Re-calibrate when the generator, the task or the judge model changes,
and keep sampling live outputs for human spot checks so drift is caught.

### Cost and placement
A judge call costs tokens per item per criterion, so judge cost can exceed generation cost. Use
deterministic checks for anything deterministic, a smaller model for simple criteria once calibrated,
and batch offline. Judges belong in the offline harness and in sampled online monitoring; using one as
a synchronous guardrail doubles latency and should be a deliberate decision.

## Example
A single-criterion, reference-guided binary judge with a reasoning-first JSON verdict, followed by the
calibration numbers that decide whether it can be trusted. `llm` is any chat-completion call returning
text; the judge prompt is the part that matters.

```python
import json
from itertools import product

JUDGE_PROMPT = """You are grading a customer-support answer for FACTUAL CORRECTNESS only.
Task the answer was meant to solve: {task}
Reference answer written by an expert: {reference}
Answer to grade: {answer}

Pass only if every factual claim in the answer agrees with the reference; wording, tone and length
do not matter. Fail if any claim contradicts the reference or adds an unsupported fact.
Reply with JSON: {{"reason": "<one or two sentences>", "verdict": "pass" | "fail"}}"""

def judge(task: str, reference: str, answer: str) -> dict:
    raw = llm(JUDGE_PROMPT.format(task=task, reference=reference, answer=answer), temperature=0)
    out = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    assert out["verdict"] in ("pass", "fail")
    return out

def calibrate(items, human: dict[str, bool]) -> dict:
    """items: labeled cases; human: id -> True if humans marked it a pass."""
    pred = {it["id"]: judge(it["task"], it["reference"], it["answer"])["verdict"] == "pass" for it in items}
    n = len(items)
    cm = {(h, p): sum(human[i] == h and pred[i] == p for i in pred) for h, p in product([True, False], repeat=2)}
    acc = (cm[True, True] + cm[False, False]) / n
    p_yes = (cm[True, True] + cm[False, True]) / n * (cm[True, True] + cm[True, False]) / n
    p_no = (cm[True, False] + cm[False, False]) / n * (cm[False, True] + cm[False, False]) / n
    kappa = (acc - (p_yes + p_no)) / (1 - (p_yes + p_no))
    fail_recall = cm[False, False] / max(1, cm[False, False] + cm[False, True])
    return {"n": n, "agreement": round(acc, 3), "kappa": round(kappa, 3), "fail_recall": round(fail_recall, 3)}

# Worked numbers for 200 items, 140 human-pass / 60 human-fail; judge passes 150:
#   135 true passes, 15 fails marked pass, 5 passes marked fail, 45 fails marked fail
#   agreement = 180/200 = 0.90, chance agreement = 0.75*0.70 + 0.25*0.30 = 0.60, kappa = 0.75
#   fail_recall = 45/60 = 0.75  -> the judge misses one real failure in four: not yet a release gate.
```

The 90% agreement looks fine; the 75% recall on failures is the number that says the judge is not yet
good enough to gate a release, and the 15 missed failures are where to look to improve the rubric.

## Trade-offs
- A judge grades hundreds of outputs in minutes for a few dollars, but every verdict carries the
  judge's biases, so the savings are real only after calibration against human labels on this task.
- Reference-guided judging is far more accurate for correctness, but it needs a golden answer per
  item, which is the expensive thing a judge was supposed to avoid; it moves labeling, not removes it.
- Pairwise preference is more sensitive than absolute scores for comparing two versions, but it
  requires two runs per item in both orders and yields no absolute pass rate to gate on.
- A smaller, cheaper judge model cuts eval cost sharply, but its agreement must be re-measured per
  criterion; cheap judges fail first on subtle factual errors.
- Reasoning-first verdicts improve agreement and auditability, but add output tokens and latency
  per item, which matters when the judge is sampled online.

## Pitfalls
- Shipping a judge that was never compared with human labels; the symptom is a stable, high offline
  score alongside user complaints about exactly the failures the judge passes.
- Grading pairwise in one order only; the symptom is a preference rate near 60–70% for whichever
  candidate is listed first, regardless of content.
- One judge for many criteria with a 1–10 score; the symptom is scores clustered at 7–8 with no way to
  say what changed when the average moves by 0.3.
- Judging the generator with the same model; the symptom is a persistent preference for the house style
  that disappears when a different judge family is used.
- Using a judge to verify arithmetic or code correctness by reading; the symptom is confidently passed
  outputs that fail when executed.
- Never re-calibrating after a generator change; the symptom is the judge's failure recall quietly
  dropping as the new generator's errors look different from the ones the rubric anticipated.

## When to use
Use a judge for criteria that cannot be checked deterministically: factual agreement with a reference,
faithfulness to retrieved context, instruction adherence, tone and policy compliance. Do not use it
where a test, a schema check or a regex will do, or as the sole gate before its failure recall on
human-labeled items is known. Measure agreement, kappa and per-class recall, report them next to the
scores the judge produces, and spot-check live outputs continually. In day-to-day work the judge is a
versioned prompt in the harness with its own calibration report; in interviews, the expected depth is
naming the biases, explaining why binary rubric criteria beat numeric scales, and describing how you
would prove a judge is trustworthy before gating a release on it.

## Role lens
- **Engineer:** Writes single-criterion judge prompts with structured verdicts, runs the calibration
  script, and versions the rubric alongside the grader in the harness.
- **Architect:** Decides which criteria may be judge-graded, which require execution or humans, what
  minimum failure recall a gate needs, and which model families may judge which generators.
- **FDE:** Builds the human-labeled calibration set with the customer's reviewers and shows them the
  agreement numbers, so the customer trusts the automated scores that follow.

## Check yourself
**Q:** Your team wants to replace the weekly human review of chatbot answers with an LLM judge and use
its pass rate as the release gate. What would you need to see before agreeing, and how would you set
it up?
**A:** I would want a human-labeled calibration set of a couple of hundred answers covering real
failures, a judge designed as one rubric criterion per call with reference or retrieved context
supplied, reasoning before a binary verdict in JSON, and a calibration report showing agreement,
kappa and especially recall on failures above a threshold we set from the cost of a missed failure. I
would check for position and verbosity effects, use a judge from a different model family than the
chatbot, and keep execution-based checks for anything computable. After that, the judge gates releases
in the harness, the humans move to spot-checking a sample of live outputs weekly, and any disagreement
feeds back into the rubric and triggers re-calibration when the generator changes.

## Quiz
### Q: Why should a judge used as a release gate be evaluated on recall of failures rather than overall agreement with humans?
- [ ] Overall agreement cannot be computed for binary verdicts
- [x] Most items pass, so high agreement is compatible with missing a large share of the rare failures that the gate exists to catch
- [ ] Recall is cheaper to compute than agreement
- [ ] Humans only label failures
> With 70% passes, a judge that marks almost everything pass still agrees with humans most of the
> time. The gate's job is catching failures, so the fraction of real failures it flags is the quantity
> that determines whether it is doing that job.

### Q: In a pairwise comparison, prompt B is preferred 68% of the time when listed first and 44% when listed second. What is going on?
- [ ] B is better, since the average preference is above 50%
- [x] The judge shows position bias; the comparison needs both orders per item and inconsistent pairs counted as ties
- [ ] The golden dataset is too small
- [ ] The judge needs a higher temperature to break ties
> A 24-point swing from ordering alone means position, not content, is driving many verdicts. Running
> both orders and treating flips as ties removes the bias; averaging it away hides it.

### Q: Which criterion is the worst fit for an LLM judge?
- [ ] Whether a support reply's tone matches the brand guidelines
- [ ] Whether every claim in a RAG answer is supported by the retrieved passages
- [x] Whether a generated SQL query returns the correct rows
- [ ] Whether an answer follows the requested format and length instructions
> Judges misgrade executable artifacts by inspection. A SQL query can be run against a fixture and
> compared to the expected rows deterministically, which is cheaper and more reliable than any judgment.
> Tone, groundedness and instruction adherence are the kinds of criteria that need a judge.
