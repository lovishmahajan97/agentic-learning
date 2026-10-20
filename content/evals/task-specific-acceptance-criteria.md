---
id: task-specific-acceptance-criteria
module: evals
name: Task-specific acceptance criteria
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - evals/golden-dataset
  - evals/rubrics
  - evals/error-analysis
  - evals/regression-tests
  - evals/eval-harness
  - customer-data-evaluation/acceptance-testing-against-customer-kpis
  - llmops-platform/evaluation-gates-for-production-releases
  - system-design/nfrs
---

## TL;DR
Task-specific acceptance criteria turn "the model should work well" into explicit, checkable
statements about one task's outputs: format, required and forbidden content, correctness against
references, behaviour on edge cases, and operational limits such as latency and cost, each with a
threshold and a scope. They exist because generic scores (a BLEU value, a 4.2/5 helpfulness rating,
a leaderboard rank) cannot tell you whether a contract-clause extractor is safe to ship; only
criteria derived from that task's actual failure modes can.

## Why it exists
Ask three people on a team whether the new summarizer is "good enough" and you get three answers
based on three anecdotes. Without a written definition of acceptable, the argument restarts at every
review, the eval measures whatever metric was easiest to compute, and the release decision is made by
whoever is most confident. Software engineering solved this decades ago with acceptance criteria and
acceptance tests derived from requirements; LLM features regressed to intuition because their outputs
looked too fuzzy to specify.

The naive alternative is a single overall quality score from an LLM judge: "rate this output 1 to
10". It fails in three ways. It hides *which* property failed, so a drop from 8.1 to 7.6 is not
actionable. It averages catastrophic and cosmetic failures together, so a system that leaks a customer
address in 1% of outputs and is otherwise perfect scores 99%. And it is untethered from the task, so
the same prompt scores well on a task it is unsuited for.

Criteria fix this by being specific, decomposed and thresholded. "Output is valid JSON matching the
schema, 100% of items" is a hard constraint anyone can check. "Priority is correct on at least 90% of
golden items overall and at least 85% in every category" is a soft constraint with a per-slice floor.
"No output contains a phone number or email address, 100%" is a safety criterion that must never
average away. The set of such statements is the contract between the people building the feature and
the people who will live with it.

## How it works
### Derive criteria from failures, not from the armchair
Start from error analysis of real outputs: a sample of production or pilot responses, read and
categorized by hand. Each failure cluster ("wrong priority when the ticket mentions an outage",
"summary over 60 words", "invented order numbers") becomes a criterion and a handful of golden items.
Criteria written before looking at outputs tend to restate the prompt ("the summary should be
concise and helpful") and miss what actually breaks.

### The kinds of criteria
- **Structural:** valid JSON, matches schema, required fields present, length bounds, language.
  Deterministic, cheap, checked on every item.
- **Content constraints:** must include (a citation, a disclaimer, the order id from the input); must
  not include (PII, competitor names, instructions the user did not ask for). Mostly deterministic
  via regex or lookup against the input.
- **Correctness:** matches the label or reference, field-level precision and recall for extraction,
  unit tests pass for code, factual claims supported by the retrieved context for RAG.
- **Behavioural:** refuses out-of-scope requests, asks a clarifying question when the input is
  ambiguous, escalates when confidence is low. Checked on dedicated golden slices built for those
  behaviours.
- **Consistency:** the same answer on a paraphrase, the same classification when irrelevant fields
  change (metamorphic checks).
- **Operational:** p95 latency, cost per request, token budget. Measured by the harness alongside
  quality, *because* a quality gain that doubles cost is a different decision.

### Hard versus soft, and thresholds per slice
Hard criteria are 100%: a single violation blocks the release, *because* the cost of one occurrence is
unacceptable (PII leak, invalid output that crashes a downstream parser). Soft criteria are statistical:
a pass-rate threshold on the golden dataset, with a floor per slice, *because* an overall 92% can be
a 60% on the slice the biggest customer uses. Thresholds come from the business (what error rate the
downstream human reviewer can absorb) and from the baseline, not from round numbers.

Two statistical facts shape how those thresholds are written. First, "100%" on a finite set is weaker
than it sounds. Zero violations in *n* items only bounds the true violation rate: the 95% upper bound
is about 3/*n* (the "rule of three"), so a PII check that passes all 300 golden items is consistent
with a leak rate of up to 1%. That is why a hard criterion usually needs two layers: an offline gate,
plus a runtime guard (a validator, a redaction step, a schema check with retry) that enforces it on
every production request, because the eval alone can never certify "never". Second, "never worse than
baseline" must be written as a non-inferiority margin, such as "no slice more than 3 points below the
baseline on the same items", *because* a strict "never worse" on a noisy measurement blocks harmless changes by chance: with no
true effect, each slice lands below its baseline close to half the time (ties aside), and with several
slices checked, most harmless changes fail somewhere.

The arithmetic also tells you how to size the slice behind each floor: a floor of 85% on a slice of
40 items carries an interval of roughly ±11 points, so the gate either needs more items in that slice
or a looser floor that the slice can actually resolve.

### Writing one down
Each criterion has a name, the scope it applies to (all items, or a slice or condition), the check
(a function, or rubric text for a judge), the threshold, a severity (blocking or warning) and an
owner. Express the check as code wherever possible, *because* prose criteria drift from what the
grader actually tests; where a judge is needed, the rubric text is versioned with the grader. The
criteria document is what the stakeholder or customer signs before the build starts, so "done" is
defined up front rather than negotiated at demo time.

### Where criteria live
They become graders in the eval harness, gates in CI (hard criteria and soft thresholds), monitors in
production for the checks that are cheap enough to run online (schema, PII, length), and the headings
of the release report. One definition, used in four places, is what keeps offline, gate and online
views consistent.

## Example
Criteria for a support-ticket triage task, expressed as checks. Each returns the criterion name, a
pass flag and a severity; the gate blocks on any hard failure and on soft pass rates below their
floors, overall and per category.

```python
import json, re
from collections import defaultdict

ALLOWED = {"billing", "refund", "shipping", "cancel", "other"}
# crude on purpose: also flags ISO dates and long order ids; use a real PII detector in production
PII = re.compile(r"\b[\w.+-]+@[\w-]+\.\w+\b|\b\+?\d[\d\s().-]{8,}\d\b")

def checks(ticket: dict, raw_output: str, label: dict):
    try:
        out = json.loads(raw_output)
    except json.JSONDecodeError:
        out = None
    yield ("valid_json", isinstance(out, dict), "hard")   # a bare string or list is not a valid record
    if not isinstance(out, dict): return                  # generator: report the failure, then stop
    yield ("category_in_set", out.get("category") in ALLOWED, "hard")
    yield ("no_pii_in_summary", not PII.search(out.get("summary", "")), "hard")
    yield ("summary_max_40_words", len(out.get("summary", "").split()) <= 40, "soft")
    yield ("outage_is_p1", out.get("priority") == "P1" if "outage" in ticket["text"].lower() else True, "hard")
    yield ("category_correct", out.get("category") == label["category"], "soft")
    yield ("priority_correct", out.get("priority") == label["priority"], "soft")

FLOORS = {"category_correct": 0.90, "priority_correct": 0.85, "summary_max_40_words": 0.95}
SLICE_FLOOR_DISCOUNT = 0.05            # each category may sit up to 5 points under the overall floor

def gate(results):                     # results: [(slice, [(name, passed, severity), ...]), ...]
    hard_fail, tally = [], defaultdict(lambda: defaultdict(list))
    for slc, rows in results:
        for name, ok, sev in rows:
            if sev == "hard" and not ok: hard_fail.append((slc, name))
            tally[name]["all"].append(ok); tally[name][slc].append(ok)
    verdict = {"blocking": hard_fail, "soft": {}}
    for name, floor in FLOORS.items():
        for slc, oks in tally[name].items():
            rate, need = sum(oks) / len(oks), floor - (0 if slc == "all" else SLICE_FLOOR_DISCOUNT)
            verdict["soft"][f"{name}/{slc}"] = (round(rate, 3), rate >= need)
    verdict["ship"] = not hard_fail and all(ok for _, ok in verdict["soft"].values())
    return verdict
```

A single ticket whose summary contains an email address blocks the release regardless of the 94%
category accuracy, which is the intended behaviour: the criteria encode which failures are allowed to
average and which are not.

## Trade-offs
- Decomposed criteria make failures actionable and gates precise, but a long list costs grader
  maintenance and can turn into a checklist that nobody re-derives when the task changes.
- Hard 100% criteria protect against unacceptable outputs, but every hard criterion needs a
  deterministic check; making a judgment call "hard" produces flaky blocks from judge noise.
- Per-slice floors catch regressions that the average hides, but each slice needs enough golden items
  for its floor to be meaningful; a floor on a slice of eight items is theatre.
- Writing criteria before building defines "done" and aligns stakeholders, but criteria frozen too
  early miss the failure modes only seen after real outputs exist; revise them after error analysis.

## Pitfalls
- Criteria that paraphrase the prompt ("output should be accurate and helpful"); the symptom is a
  judge that passes almost everything and a release meeting that still argues from anecdotes.
- One aggregate quality score as the only gate; the symptom is a rare catastrophic failure (PII, a
  fabricated refund) shipping inside a high overall score.
- Thresholds chosen as round numbers rather than from the downstream cost of errors; the symptom is a
  gate that blocks harmless regressions and passes harmful ones.
- No operational criteria; the symptom is a quality improvement that triples latency and is discovered
  by users rather than by the eval.
- Criteria stored in a document while the graders drift; the symptom is a passing eval for a behaviour
  the document says is forbidden.
- No per-slice floors; the symptom is a merged change that raised the average by regressing the one
  category the enterprise customer cares about.
- Treating a clean offline run as proof a hard criterion holds in production; the symptom is the first
  real violation arriving weeks after launch on an input pattern the golden set never contained, with
  no runtime guard to catch it.

## When to use
Write criteria for any LLM feature before committing to a delivery date, and revise them after the
first error analysis on real outputs. Reach for hard criteria wherever a single bad output is
unacceptable and a deterministic check exists; use soft thresholds with slice floors for correctness
and quality; add latency and cost limits always. Measure them in the harness, enforce them in CI and,
where cheap, monitor them online. They matter most in forward-deployed work, where the customer's
sign-off on criteria is what makes "done" unambiguous. In interviews, expect to be handed a task and
asked to write its criteria: the strong answer separates hard from soft, names slices, ties thresholds
to downstream cost, and includes an operational limit.

## Role lens
- **Engineer:** Implements each criterion as a grader, keeps rubric text versioned with the judge, and
  wires hard and soft gates into the pull-request pipeline.
- **Architect:** Owns the policy for which criteria are blocking, how thresholds are justified, and how
  the same criteria flow from offline gates into production monitors.
- **FDE:** Writes the criteria with the customer's experts from their real failures, gets sign-off
  before building, and reports against those criteria at every milestone.

## Check yourself
**Q:** You are asked to define acceptance criteria for a system that drafts replies to customer emails
for a human agent to approve. Walk through how you would derive and structure them.
**A:** I would first read a few hundred real drafts and categorize failures: wrong facts about the
order, promises the company cannot keep, tone problems, missing required disclaimers, length. Each
cluster becomes a criterion. Hard, 100%: no PII beyond what the customer already sent, no refund or
discount promised unless the policy tool returned one, required legal footer present, valid structured
output for the agent UI. Soft, with thresholds: factual claims about the order grounded in the fetched
record on at least 97% of golden items, tone rated acceptable by a calibrated judge on at least 95%,
never below 90% for any language slice. Operational: p95 under a few seconds, bounded cost per draft.
I would get the support lead to sign the list, encode every check as a grader, and gate releases on it.

## Quiz
### Q: Why should "no personal data in the output" be a hard criterion rather than a soft one with a 99% threshold?
- [ ] Soft criteria cannot be checked deterministically
- [x] A single occurrence is unacceptable, so the failure must not be allowed to average away
- [ ] Hard criteria are cheaper to evaluate
- [ ] Regulators require 100% thresholds for all LLM outputs
> The distinction between hard and soft is the cost of one occurrence. A PII leak is unacceptable even
> once, so it must block; a 99% soft threshold explicitly tolerates it one time in a hundred.

### Q: A criteria document says "responses must be concise and helpful". What is the main problem?
- [ ] It does not mention latency
- [ ] Conciseness is impossible to measure
- [x] It restates the prompt without a check, a threshold or a scope, so it cannot be graded or gated
- [ ] It should be a hard criterion
> A usable criterion has a concrete check (a word limit, a rubric with anchors), a threshold and a
> scope. Vague adjectives give the judge nothing to grade against and the team nothing to argue from.

### Q: Overall category accuracy is 93% against a 90% floor, but the "cancel" slice is at 71%. With per-slice floors in place, what should the gate do?
- [ ] Pass, since the overall floor is met
- [ ] Pass with a warning, since slices are informational
- [x] Block, because a slice floor is violated and the average is hiding a regression that matters
- [ ] Re-run with more samples until the slice reaches 90%
> Per-slice floors exist precisely so that an average cannot mask a failing segment. Re-running does not
> fix a real 71%; the slice must be investigated and the fix measured.
