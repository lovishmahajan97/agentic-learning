---
id: error-analysis
module: evals
name: Error analysis
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - evals/golden-dataset
  - evals/eval-harness
  - evals/llm-as-a-judge
  - evals/inter-annotator-agreement
  - evals/eval-driven-development
  - evals/regression-tests
  - rag-retrieval/retrieval-failure-diagnosis
  - customer-data-evaluation/production-feedback-classification
sources:
  - Ng, 2018 — Machine Learning Yearning
  - Husain, 2024 — Your AI Product Needs Evals
  - Shankar et al., 2024 — Who Validates the Validators? Aligning LLM-Assisted Evaluation of LLM Outputs with Human Preferences
---

## TL;DR
Error analysis is the practice of reading a sample of your system's real traces one at a time, writing
down what went wrong in each, grouping those notes into a short taxonomy of failure modes, and counting
them. It exists because an aggregate score says *that* the system fails, never *why* or *where*. The
counts tell you which fix buys the most, and each stable failure mode becomes a metric and a set of
golden items, so the eval suite grows from observed failures rather than imagined ones.

## Why it exists
A pass rate is a summary, and a summary cannot be fixed. Suppose a support assistant scores 71% on the
golden set and the product owner asks for 90%. The engineer remembers one failure from last week's
demo, adds a paragraph to the prompt about it, and re-runs: 72%. That failure was one sample from an
unknown distribution; nobody knows whether it accounts for 1% of failures or 40%. Every fix made
without the distribution is a guess, and prompt fixes made by guessing accumulate into a two-thousand-
token prompt of special cases that nobody dares to delete.

Classical ML already had the answer. Ng's advice for a misbehaving classifier was to read about a
hundred misclassified dev-set examples by hand, tally the categories, and work on the largest one,
because an afternoon of counting beats a month of optimizing the wrong thing. LLM systems make this
more necessary, not less. Outputs are free text, pipelines have several stages (retrieval, prompt,
model, tools, post-processing), and the failure modes are open-ended: you cannot define a metric for a
failure you have not yet seen.

The naive alternatives all skip the reading. "Trust the judge score" fails because a judge grades the
criteria you gave it; it cannot see the failure mode you did not name, and Shankar et al. found that
people's criteria themselves drift as they read outputs. "Fix what you notice" is whack-a-mole with
regressions elsewhere. "Collect thumbs-down" gives a sample biased toward the failures users notice
and bother to report. Error analysis is the cheap, systematic alternative: an afternoon with a few
dozen to a hundred traces (an illustrative order of magnitude) usually reorders the roadmap.

## How it works
### 1. Sample traces, not just outputs
Pull whole traces: the input, retrieved context, tool calls and results, intermediate steps, and the
final output. Attribution is impossible from the final answer alone. Take a random sample from
production or from the last eval run for honest prevalence numbers, and add a stratified sample (by
feature, user segment, input length, or the items that failed) so rare slices appear at all. Keep
reading until new traces stop producing new categories; qualitative researchers call this saturation.
In practice that is dozens to low hundreds of traces per round. The sizing arithmetic is worth knowing:
a failure mode with prevalence *p* appears at least once in *n* random traces with probability
1 − (1 − p)ⁿ. At n = 100, a 2% mode shows up about 87% of the time and a 1% mode only about 63%, which
is why rare-but-severe modes need the stratified sample rather than a bigger random one.

### 2. Open coding: write what went wrong, in free text
For each trace record pass/fail and a one- or two-sentence note describing the **first** thing that
went wrong. The first-failure rule exists *because* downstream errors are usually consequences: if
retrieval returned the wrong policy document, the confident wrong answer that followed is not a
generation bug, and counting it as one sends you to fix the prompt. Do not pre-define categories at
this stage; free-form notes stop you from forcing observations into the buckets you expected. Use one
domain-competent annotator at first, *because* a committee negotiates definitions before it has data,
and bring in a second annotator later to measure agreement on the definitions that emerge.

### 3. Axial coding: group notes into failure modes
Cluster the notes into a taxonomy, typically five to fifteen categories, each with a crisp definition
and one positive and one negative example. An LLM can propose a first clustering of the *human-written*
notes, which saves time on a few hundred rows, but a person accepts, merges and renames the clusters,
*because* the model groups by surface wording while the useful grouping is by remediation. Merge categories you cannot tell apart; split a category
when its members need different fixes. The test of a good category is behavioural: two people given
the definition label the same trace the same way, and the category implies a specific remediation.
"Model did not understand" fails that test; "answered for the wrong product tier" passes.

### 4. Count, then prioritise
Tally failures per category and sort: a Pareto chart. Priority is roughly frequency × severity ×
fixability. Severity matters *because* a wrong refund amount at 6% of failures outranks a formatting
slip at 20%. Fixability matters *because* a retrieval filter is a day's work while a model-capability
gap may need a different model or a different product decision.

Two sanity checks belong here. First, **ceiling analysis** (Ng's term): fixing a category perfectly can
raise the pass rate by at most that category's share of *all* traces, not of failures. A category with
13 of 100 traces caps the gain at 13 points, so if the target is +20 points, no single fix gets there and
you know it before writing code. Second, **noise**: counts from one round are estimates. A rate of 13 in
100 has a standard error of about √(0.13 × 0.87 / 100) ≈ 3.4 points, so a 95% interval of roughly
6–20%; two categories at 7 and 9 are effectively tied, and the ranking among small categories should
not drive the roadmap. For classification or extraction tasks with a closed label set, the confusion
matrix *is* the count table: the off-diagonal cells are the failure modes, and the largest one is where
to start.

### 5. Fix one thing and re-measure on the same sample
Change the top-priority component, then re-run the same traces plus the eval set. Confirm that the
target category shrank and that no other category grew. Measuring on the same sample removes
input-sampling noise from the before/after comparison; it does not remove the model's own run-to-run
variance, so treat a change of one or two traces as noise. Then confirm on a fresh sample, *because*
a fix tuned while staring at these exact traces can overfit them.

### 6. Promote stable categories into evals
Each category that survives a couple of rounds becomes (a) golden items — the failing traces with a
corrected expected output or rubric — and (b) a grader: a deterministic assertion where the failure
has an observable signature (cites a document id that exists in the retrieved set; contains a handoff
when the question asks for account data), otherwise a binary LLM judge whose rubric is distilled from
the annotation notes and calibrated against your labels. This is the mechanism by which an eval suite
stays representative: its metrics are the failure modes that actually occurred.

### Attribution in multi-stage systems
For RAG, split every failure into retrieval (the right chunk is not in the top-k) versus generation
(the right chunk is present and the answer is still wrong or unfaithful), because the two have
different owners and different fixes. For agents, record the first wrong step — wrong tool, wrong
arguments, misread tool result, stopped early, looped — and tabulate failures by step; the table shows
where a guardrail or a better tool description would have the most leverage.

### Cadence
Error analysis is not a launch activity. Re-run it after each significant change, and on a schedule
against production traffic, *because* the input distribution drifts. Retire categories whose count has
been zero for several rounds; expect new ones.

## Example
An illustrative round on a customer-support RAG assistant. One hundred random production traces are
read; 36 are judged failures. Open coding, then grouping, gives these counts: correct article not in
the top-5 retrieved chunks, 13; correct article present but the answer contradicts it, 7; question
needs account data the bot cannot see and it guesses a number, 6; answer describes the wrong product
tier, 5; over-long or badly formatted, 3; other, 2. Retrieval is 36% of failures and the tier-mix-up
is mostly a metadata-filter problem, so the first fix is retrieval (hybrid search plus a tier filter);
"guesses account data" is only 6 cases but highest severity, so it is second, fixed with a handoff
rule and a deterministic grader. Re-running the same 100 traces after the retrieval fix: retrieval
failures 13 → 4, wrong tier 5 → 2 (the tier filter), contradictions unchanged at 7 (expected,
generation was untouched), total 36 → 24. Ceiling analysis predicted this shape: the two categories
touched held 18 of 100 traces, so +18 points was the most the fix could buy, and it bought +12. The
remaining 24 failures are now led by "contradicts context" (7) and "guessed account data" (6), which
is the next round's agenda; a confirm run on a fresh 100 traces checks that the gain was not specific
to the traces the fix was tuned on.
The schema and the counting are a few lines:

```python
from collections import Counter
from dataclasses import dataclass

@dataclass
class Annotation:
    trace_id: str
    passed: bool
    note: str           # free text, written first (open coding)
    first_failure: str  # category assigned afterwards (axial coding); "" when passed
    severity: int       # 1 cosmetic, 2 wrong but harmless, 3 wrong facts or wrong action

rows = [
    Annotation("t-014", False, "top-5 chunks all about SSO; question was about SCIM", "wrong_doc_retrieved", 2),
    Annotation("t-021", False, "right article in context; answer says 30 days, article says 14", "contradicts_context", 3),
    Annotation("t-033", False, "asked for last invoice total; bot invented a number", "guessed_account_data", 3),
    Annotation("t-040", True, "", "", 0),
    # ... ~100 rows, read in full, in random order
]

fails = [a for a in rows if not a.passed]
counts = Counter(a.first_failure for a in fails)
worst = {c: max(a.severity for a in fails if a.first_failure == c) for c in counts}
for cat, n in counts.most_common():
    print(f"{cat:22s} n={n:3d} share={n/len(fails):5.1%} severity={worst[cat]} priority={n*worst[cat]}")
```

The point of the code is the two separate fields: `note` is written while reading, `first_failure` is
assigned only after all notes exist, so the taxonomy comes from the data rather than the other way
round.

## Trade-offs
- Reading traces by hand costs expert hours and cannot cover every trace, but it finds failure modes no
  predefined metric could; once a category is stable, automate it and spend the human time on the next
  unknown.
- A single annotator is fast and consistent but encodes one person's blind spots; this flips when the
  domain is specialised or when "what counts as a failure" is itself contested, and then a second
  annotator and an agreement measure are worth the slowdown.
- Stratified sampling surfaces rare but severe failures a random sample would miss, at the price of
  distorting prevalence; keep the random sample for counts and the stratified one for discovery.
- The first-failure rule keeps counts attributable but hides compounding failures; when traces fail in
  several independent ways, record secondary failures in their own field rather than in the primary
  count.
- A fixed taxonomy makes categories measurable over time but ossifies; re-open-code every few rounds or
  the analysis only ever finds the failures you already know about.

## Pitfalls
- Fixing while reading: a failure gets patched before the counting is done. Symptom: the prompt grows
  with special cases and the aggregate barely moves, because the patched category was 3% of failures.
- Sampling only thumbs-down conversations or escalations. Symptom: the taxonomy is dominated by tone
  and formatting complaints while silent wrong answers, which users rarely flag, go uncounted.
- Letting an LLM judge do the open coding. Symptom: categories sound generic ("inaccurate",
  "unhelpful"), match the judge's prior rather than your data, and suggest no concrete fix.
- Grading the final answer without the trace. Symptom: "hallucination" is the biggest category, yet
  every prompt fix fails, because the real cause was retrieval or a tool returning stale data.
- Categories defined by unobservable cause ("model got confused") instead of observable behaviour.
  Symptom: annotators disagree and counts swing between rounds with no change to the system.
- Skipping the re-measure on the same sample after a fix. Symptom: a change "felt better" in the
  playground, and the next round shows the category unchanged or a neighbouring one larger.

## When to use
Run error analysis at three moments: before writing any evals, to learn which failure modes exist;
whenever the aggregate plateaus or a change has an effect you cannot explain; and on a cadence against
production traffic. It is never a substitute for automated evals, because its output *is* the next set
of automated evals. Track the number of traces read per round, the categories and their counts, the
share of failures covered by the top three categories, and whether a fix reduced its target category
on a fresh sample. In day-to-day work it looks like a shared sheet or trace viewer with a notes column
and a weekly hour of reading. In interviews, "the score dropped after your change, what do you do?"
should be answered with "diff the runs, pull the items that flipped, read their traces, count", not
with "try a different prompt".

## Role lens
- **Engineer:** Builds the trace sampler and annotation sheet, reads the traces, and converts stable
  categories into graders and golden items that run in the eval harness and CI.
- **Architect:** Decides where traces are captured and retained so failures are attributable per stage,
  and maps failure categories to component ownership (retrieval, prompt, tools, model).
- **FDE:** Runs annotation sessions with the customer's subject-matter experts on the customer's own
  traces, because they define what counts as a failure, and turns the taxonomy into acceptance criteria.

## Check yourself
**Q:** Your RAG assistant's golden-set pass rate fell from 82% to 74% after you swapped the embedding
model. The product owner wants it reverted today. What do you do in the next two hours, and what do
you ship?
**A:** Diff the two runs item by item and pull the items that flipped, in both directions, since some
will have improved. Read their full traces, not the answers alone, and note the first failure for each.
If most pass→fail items are "correct chunk no longer in top-k", it is a retrieval regression; check
whether the flips cluster in a slice such as short keyword queries or product codes. Count. Then decide
with evidence: revert if the regressed slice matters to users and there is no quick fix; otherwise ship
hybrid search or a re-chunk together with the new regression tests. Either way, promote the flipped
items into the golden set so the next embedding change is caught before merge, and report the counts
and slices, not the aggregate, to the product owner.

## Quiz
### Q: Why record only the first failure in a multi-step trace during open coding?
- [ ] Because annotators rarely have time to read past the first step
- [x] Because later errors are usually consequences of the first, so counting them inflates the wrong categories and misdirects the fix
- [ ] Because LLM judges can only evaluate one error per response
- [ ] Because the first step is always retrieval, which is the most important component
> A wrong retrieved document produces a confidently wrong answer; counting that answer as a generation
> failure would send effort to the prompt when the fix belongs in retrieval. Secondary failures can be
> recorded separately, but the primary count must be attributable.

### Q: A team runs error analysis only on conversations where users clicked "thumbs down". What is the main problem?
- [ ] Thumbs-down data is too sparse to form any categories
- [ ] Users who click thumbs down are usually wrong about answer quality
- [x] The sample is biased toward failures users notice and report, so silent wrong answers and rare slices are under-counted
- [ ] It makes the sample too large to annotate by hand
> Explicit feedback over-represents tone, formatting and obvious refusals, and under-represents
> plausible-sounding wrong answers that users accept. A random sample of all traffic is needed for
> prevalence; flagged traces are a useful supplement for discovery.

### Q: What is the right output of an error-analysis round, beyond a list of fixes?
- [ ] A new system prompt with one paragraph per failure category
- [ ] A retrained LLM judge that reports a single overall quality score
- [x] A failure taxonomy with counts, plus new golden items and graders for the stable categories so they are measured automatically from then on
- [ ] A larger random sample to be read in the next round
> The counts decide priority, and promoting each stable category into golden items and a grader is what
> keeps the eval suite representative of real failures. A single overall score or a longer prompt would
> throw away the attribution the round produced.
