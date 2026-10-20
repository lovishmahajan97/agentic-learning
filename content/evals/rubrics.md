---
id: rubrics
module: evals
name: Rubrics
kind: practice
difficulty: intro
est_minutes: 13
status: reviewed
related:
  - evals/llm-as-a-judge
  - evals/judge-calibration-and-bias
  - evals/inter-annotator-agreement
  - evals/task-specific-acceptance-criteria
  - evals/error-analysis
  - customer-data-evaluation/sme-review-and-annotation-guidelines
---

## TL;DR
A rubric turns "is this output good?" into a list of specific, observable criteria with defined levels,
so two humans, or a human and an LLM judge, score the same output the same way. It exists because an
unanchored score ("rate quality 1–10") measures the grader more than the output. Good rubrics come from
observed failure modes, prefer binary checks over wide scales, make catastrophic criteria gating rather
than averaged, and are versioned like code.

## Why it exists
Ask three engineers to rate a support-bot answer from 1 to 10 and you get 6, 8 and 9. They rarely
disagree about the answer; they disagree about what a 7 means. That disagreement is pure noise in your
evaluation, and noise sets the smallest improvement you can detect. If scores wobble by two points
between graders, a real half-point gain between prompt versions is invisible. The same thing happens with
an LLM judge: "rate the quality" produces compressed, run-to-run varying scores, because the judge fills
in its own unstated criteria, which in practice tend to be length, confidence and formatting.

The naive fix is a longer instruction: "consider accuracy, completeness and tone." It fails because prose
still leaves every level boundary undefined. What separates a 6 from a 7 on "completeness"? A rubric fixes
this by making the criteria enumerable and each level observable. "Every factual claim is supported by a
quoted span from the retrieved context" is checkable; "well grounded" is not. Observable anchors are what
let a second grader, or a model, reproduce the first grader's score.

A rubric also does a second job: it is the executable definition of "good" that product, subject-matter
experts and engineering agree on. Disagreements surface early as edits to a criterion instead of later as
arguments about a number. In forward-deployed work the rubric often ends up in the statement of work as
the acceptance test.

## How it works
### Anatomy of a rubric
A rubric has **criteria** (the dimensions you judge: correctness, completeness, groundedness, format
compliance, tone, safety), **levels** per criterion with **behavioural anchors**, and an **aggregation
rule**. Anchors describe the output, not the grader's feeling. Compare "Correctness: 0 = contains a
factual error that changes the conclusion; 1 = minor inaccuracy that does not affect the conclusion;
2 = no errors" with "poor / fair / good". The first can be applied by someone who has never met you.

Criteria must be independent and single-barreled. "Accurate and complete" is two criteria; an answer
that is accurate but incomplete splits graders down the middle, and you lose the information about which
half failed.

### Analytic vs holistic
An **analytic** rubric scores each criterion and combines them (sum, weighted sum, or fraction passed).
It is better for diagnosis, because a regression is attributable to a dimension, and better for LLM
judges, because "is X present?" is an easier question than "how good is this overall?". A **holistic**
rubric assigns one overall level with a descriptor per level. It is faster for humans and fits situations
where you want a single preference signal, such as pairwise comparison. Most production evals use
analytic rubrics with a holistic sanity check.

### Gating criteria
Some failures must not be averaged away: a fabricated citation, a leaked secret, an unsafe instruction.
Mark those criteria as gates: failing any gate makes the overall result a fail regardless of the rest.
This exists because an arithmetic mean over criteria is exactly the mechanism by which a 4-out-of-5
answer with a made-up policy number ships.

### Applicability and the denominator
Many criteria apply only to some items: "escalates when the request is out of scope" means nothing for an
in-scope question, and "cites the refund clause" means nothing for a password reset. Give such criteria
an explicit **N/A** outcome with its own trigger condition, and exclude N/A from the denominator. Scoring
them as automatic passes inflates the score with free points (an answer that passes 2 of 3 real checks
plus 3 inapplicable ones reports 5/6 instead of 2/3) and hides the criterion's true pass rate, which
should be computed only over the items where it applied. Scoring them as fails does the opposite. Ask the
judge, or the item's metadata, to decide applicability first, because "did it escalate?" is ambiguous
when escalation was never warranted.

### Weighting
Weighted sums look principled but hide trade-offs: with weights 0.5 on correctness and 0.1 on tone, a
regression in tone is invisible in the headline, and arguments about weights are really arguments about
priorities that should be explicit. The usual pattern is gates for what must never happen, an unweighted
fraction of the remaining checks for the trend line, and per-criterion pass rates for decisions. Reach
for weights only when a single scalar must rank many variants, and report the per-criterion numbers
next to it.

### Binary beats Likert
Prefer yes/no checks per criterion over 1–5 scales. A five-level scale has four boundaries to disagree
on; a binary check has one, so raw inter-annotator agreement and judge–human agreement are typically
higher. One caveat: when a binary criterion almost always passes, chance-corrected agreement (kappa) can
still be low, so read it alongside the pass rate. If you need granularity, add more binary criteria
rather than more levels. The resulting score, fraction of
checks passed, is also directly interpretable. When a quantity is genuinely continuous (how well a
summary fits a length budget) use a three-level anchored scale, rarely more.

### Where criteria come from
Not from a brainstorm. Read 50–100 real outputs, cluster the failures, and turn each recurring failure
into a criterion with a negative anchor. This is error analysis feeding the rubric, and it is why rubrics
evolve: as the failure mix changes, criteria get added and retired. Two layers work well: a **global**
rubric applied to every item, plus **item-specific** checks stored with each golden example ("must state
the refund window is 30 days", "must not recommend a competitor"). Item-specific checklists are the most
reliable thing an LLM judge can apply, because they reduce judgment to presence or absence.

### Calibration and versioning
Pilot the rubric with two or more annotators on 30–50 items and compute agreement per criterion. Low
agreement on a criterion means the anchors are ambiguous, not that the annotators are bad; rewrite and
re-pilot. Add one exemplar output per level per criterion so graders can pattern-match. Then freeze a
version. Scores are only comparable within a rubric version, so changing a criterion mid-experiment
invalidates the before/after.

### Rubrics inside an LLM judge
Put the rubric verbatim in the judge prompt. Ask the judge to quote the evidence for each criterion
before giving a verdict, because looking for a quote forces it to actually check rather than
pattern-match the tone. Use structured output. For the highest-stakes criteria, make one judge call per
criterion; a single call over twelve criteria tends to rubber-stamp the later ones. Validate the judge
per criterion against human labels: judges are usually excellent on format compliance and weak on
domain correctness, so you may keep humans on some criteria and automate the rest.

## Example
A rubric encoded as data, graded one criterion per judge call, with a gating rule applied before
aggregation. Item-specific criteria are attached to the golden item and skipped for items that do not
declare them.

```python
RUBRIC = {
    "no_fabricated_citation": {"gate": True, "check":
        "Every cited source ID appears in the provided context. FAIL if any citation is absent."},
    "answers_question": {"gate": False, "check":
        "The response directly answers the user's question within its first two sentences."},
    "grounded": {"gate": False, "check":
        "Every factual claim in the response is supported by a quoted span from the context."},
    "refund_window_stated": {"gate": False, "item_specific": True, "check":
        "The response states that the refund window is 30 days."},
}

PROMPT = """You are grading ONE criterion of a support-bot answer.
Context:
{context}

Question: {question}
Response: {response}
Criterion: {check}
Quote the evidence from the response and context (or write NONE), then output exactly PASS or FAIL on the last line."""

def grade(item, response, llm):
    """item = {'context':..., 'question':..., 'criteria': [item-specific criterion names]}"""
    results = {}
    for name, c in RUBRIC.items():
        if c.get("item_specific") and name not in item["criteria"]:
            continue
        out = llm(PROMPT.format(context=item["context"], question=item["question"],
                                response=response, check=c["check"]))
        results[name] = out.strip().splitlines()[-1].strip() == "PASS"
    gates_failed = [n for n, r in results.items() if RUBRIC[n]["gate"] and not r]
    if gates_failed:
        return 0.0, results, gates_failed          # a gate failure zeroes the score
    return sum(results.values()) / len(results), results, []   # skipped criteria never enter the denominator

# A response citing "DOC-9" when the context only holds DOC-1..DOC-4 returns (0.0, {...}, ['no_fabricated_citation'])
# even if it answers the question, is otherwise grounded and states the 30-day window.
```

The per-criterion results are what you log, because "grounded dropped from 94% to 81% after the prompt
change" is actionable and "average score dropped 0.04" is not.

## Trade-offs
- Analytic rubrics give per-criterion diagnosis but cost more grader time per item; a holistic rubric is
  the better choice when you only need a fast overall preference, as in a pairwise arena.
- Binary criteria raise agreement but lose gradation; when the judged quantity is inherently continuous,
  a short anchored scale beats forcing a yes/no.
- Item-specific checklists are the most reliable for LLM judges, but they cost SME time per golden item
  and go stale when the product or policy changes; with a large or fast-moving golden set, lean on the
  global rubric.
- A detailed rubric constrains the judge to the failures you already know about, so it misses novel
  failure modes; pair it with periodic open-ended error analysis on fresh production samples.
- Gating criteria protect against catastrophic ships but make the headline score lumpy; report gate
  failure rate and the non-gated score separately so trends stay readable.

## Pitfalls
- Double-barreled criteria such as "accurate and complete"; the symptom is annotators splitting on items
  that are accurate but incomplete, and agreement on that criterion collapsing.
- Anchors that describe grader feelings ("excellent", "adequate") instead of output features; the symptom
  is scores drifting between annotators and across weeks for the same outputs.
- Averaging over a gate; the symptom is an answer with a fabricated citation scoring 0.8 and passing the
  release threshold.
- Editing the rubric in the middle of an experiment; the symptom is an "improvement" that disappears when
  both arms are regraded with the same version.
- Scoring inapplicable criteria as passes; the symptom is a criterion such as "escalates when out of
  scope" reporting a 97% pass rate although the system mishandles most of the few out-of-scope items.
- Twelve criteria in one judge call; the symptom is later criteria agreeing with humans far less than the
  same criteria graded in isolation.
- Treating the rubric as finished; the symptom is a green eval dashboard while production complaints
  describe a failure mode that no criterion covers.

## When to use
Use a rubric whenever a human or model scores free-form output: chat answers, summaries, generated
documents, agent reports. Do not use one for tasks with an executable oracle; run the tests. Start with
three to six criteria drawn from error analysis, binary where possible, with a gating subset, and measure
per-criterion inter-annotator agreement and judge–human agreement before relying on it. Day to day, the
rubric lives in the judge prompt and the annotation guidelines and changes under version control. In
interviews, "how would you evaluate a summarisation feature?" is a rubric question: criteria, observable
anchors, gates, pilot, agreement, versioning.

## Role lens
- **Engineer:** Encodes the rubric as data, grades one criterion per judge call, and logs per-criterion
  results so a regression points at a dimension instead of a number.
- **Architect:** Owns which criteria are gating for a release, how rubric versions are tied to eval
  dataset versions, and how the rubric maps onto contractual acceptance criteria.
- **FDE:** Co-writes the rubric with the customer's SMEs in a labeling session, so the definition of done
  is shared before anyone argues about whether the model is "good enough".

## Check yourself
**Q:** A customer says the bot's answers "should be high quality". Turn that into a rubric an LLM judge
can apply, and explain how you would know the rubric itself is any good.
**A:** Collect 50 real transcripts and the SMEs' complaints, cluster the failures, and write one binary
observable criterion per failure: answers the question asked, no claim unsupported by the context, cites
a document, correct policy figures, escalates when out of scope, no fabricated references. Mark
fabrication and unsafe advice as gates. Write one example per outcome per criterion. Pilot with two SMEs
on 40 items and compute per-criterion kappa; rewrite any criterion with low agreement, because that is
an anchor problem. Then run the judge per criterion and compare it with the SME majority; automate the
criteria where judge agreement is near SME–SME agreement, keep humans on the rest, and freeze a rubric
version so future scores are comparable.

## Quiz
### Q: Why prefer several binary criteria over a single 1–10 "quality" score?
- [ ] LLM judges cannot output numeric scores reliably
- [x] Each binary check has one decision boundary, so graders and judges agree more, and the per-criterion result says what failed
- [ ] Binary scores are easier to average into a percentage
- [ ] A 1–10 scale takes longer to grade than ten binary checks
> Agreement drops with every extra level boundary, and a single overall number hides which dimension
> regressed. The averaging convenience is real but is not the reason the practice exists.

### Q: An answer passes 4 of 5 criteria but cites a document that is not in the context. Under a well-designed rubric, what is the outcome?
- [ ] It scores 0.8 and passes the usual threshold
- [ ] It scores 0.8 but is flagged for later review
- [x] It fails outright, because fabricated citations are a gating criterion that overrides the average
- [ ] The citation criterion is double-weighted, giving roughly 0.67
> Gates exist precisely so a catastrophic failure cannot be averaged away by otherwise good behaviour;
> a weighted average still lets the answer ship if the other criteria are strong.

### Q: Per-criterion agreement shows "completeness" at kappa 0.25 while every other criterion is above 0.7. What is the most likely cause and the first fix?
- [ ] The annotators are unreliable and should be replaced
- [x] The criterion's anchors are ambiguous for these items; add item-specific "must mention" lists or sharper anchors, then re-pilot
- [ ] Completeness is subjective, so drop the criterion
- [ ] Switch completeness to a 1–10 scale to capture nuance
> Low agreement isolated to one criterion points at that criterion's definition. Annotators agree fine
> elsewhere, so replacing them or widening the scale would not fix the ambiguity.
