---
id: continuous-improvement
module: adoption
name: Continuous improvement
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - evals/error-analysis
  - evals/regression-tests
  - customer-data-evaluation/production-feedback-classification
  - customer-data-evaluation/feedback-loops-to-product
  - llmops-platform/evaluation-gates-for-production-releases
  - llmops-platform/canary-shadow-deployments
  - llmops-platform/drift
  - adoption/usage-analytics
---

## TL;DR
Continuous improvement is the operating loop that turns production evidence into measured fixes. You
observe failures, classify them, prioritise by frequency × severity, fix at the cheapest layer that
works, capture the cases in a regression suite, gate the release, and confirm online that the failure
bucket shrank. It exists because an AI system's quality degrades or stalls by default, and ad-hoc prompt
tweaks fix one complaint while silently breaking others.

## Why it exists
A system that passed acceptance on launch day keeps meeting inputs nobody tested. New users phrase things
differently, the knowledge base changes, the vendor updates the model behind an alias, and the business
adds products the prompts never mention. Quality does not stay where acceptance left it. It drifts, and
users quietly route around a tool they stop trusting.

The naive response is reactive patching. A complaint arrives, someone edits the prompt until that example
works, and the change ships. That fails in three ways. First, LLM behaviour is coupled: an instruction
added to fix French users' replies can change tone or format for everyone else, and without a regression
suite nobody notices until the next complaint. Second, the loudest stakeholder's anecdote gets fixed
before the failure that hits two hundred users a week, because nothing measures frequency. Third, nobody
can say whether the system is getting better overall. The team is busy but the acceptance rate is flat.

Continuous improvement replaces that with a loop that has a measurable input (classified failures), a
measurable gate (no regression on a frozen suite) and a measurable output (the targeted bucket shrinks
in production). It is the plan-do-check-act cycle from quality management, with test-driven bug fixing
as the "check" step, adapted to systems whose outputs are probabilistic and whose dependencies change
underneath them.

## How it works
### 1. Observe
Pull signals from several sources, because each sees different failures: disposition events from usage
analytics (regenerations, heavy edits, abandons), explicit feedback, escalations to humans, trace-level
errors (tool failures, timeouts, empty retrievals), incidents, and periodic human review of a random
sample. The random sample matters *because* every other source is biased toward failures that users
notice and bother to report.

### 2. Classify
Group failures into a stable taxonomy through error analysis: read real traces and name the failure
modes ("cited superseded policy", "wrong language", "tool called with wrong account ID"). Use an LLM
classifier to scale the labelling once the categories are stable, and spot-check it. Counts per bucket
per week are the input to everything that follows.

### 3. Prioritise
A simple score is weekly count × severity ÷ effort, with a hard override: anything that is harmful,
non-compliant or loses money gets fixed first regardless of its score. Use the override *because* a
pure frequency score will always rank a frequent cosmetic issue above a rare one that ends the contract.

### 4. Fix at the cheapest layer that works
Diagnose which layer owns the failure before touching anything. In rough order of cost: UX or training
(the user asked for something out of scope), prompt or instructions, retrieval and data (wrong, stale or
missing documents), tools and integrations, guardrails, model choice, and fine-tuning. Many "model
failures" turn out to be retrieval or data failures, and a prompt cannot fix a document that is not in
the index.

### 5. Encode before you fix
Add the failing production cases, de-identified as needed, to the regression suite *before* changing the
system. This proves the suite catches the failure, and keeps it caught after later changes.

### 6. Gate, release, verify
Run the full frozen suite on the candidate. Require improvement on the targeted slice and no regression
beyond noise on every other slice. "Noise" needs a number, for two reasons. Slices are small, so a pass
rate over *n* cases has a standard error of about `sqrt(p(1−p)/n)`; and LLM outputs and LLM judges vary
between runs even on identical inputs. Because baseline and candidate run on the *same* cases, compare
them paired: count cases that flipped pass→fail (*b*) and fail→pass (*c*). McNemar's test treats
`(b − c)² / (b + c)` above about 3.84 as a change unlikely to be chance at the 5% level (with only a
handful of flips, use the exact binomial version instead). For flaky
cases, run each several times and score the majority, or the gate will block and pass at random.

Ship behind a canary or shadow deployment, then check online that the bucket shrank. Use a *rate* (failures
per 1,000 conversations) compared with the bucket's own pre-release weeks, not a raw count, because
traffic growth makes a fixed bucket look bigger. Version every prompt, retrieval config and model, so a
change in production metrics can be attributed to a specific release.

### 7. Run it on a cadence
A weekly triage and a fixed release train keep the loop moving without turning every complaint into an
emergency. External changes go through the same gate: a vendor model update, a re-indexed knowledge base,
or a new user group. Close the loop with reporters ("you flagged X, fixed in release 14"), because
visible responses keep feedback rates up.

## Example
A weekly triage and release gate for a support assistant. Bucket counts come from classified production
feedback. The ranking applies the severity override, and the gate compares per-slice pass rates on the
same frozen regression suite.

```python
from dataclasses import dataclass

@dataclass
class Bucket:
    name: str
    weekly_count: int        # classified production failures this week
    severity: int            # 1 cosmetic, 3 wrong answer, 5 harmful / non-compliant
    effort_days: float
    layer: str               # prompt | retrieval | tool | guardrail | model

def rank_key(b: Bucket):
    # Severity-5 items jump the queue; the rest are ranked by count x severity / effort.
    return (b.severity >= 5, b.weekly_count * b.severity / b.effort_days)

buckets = [
    Bucket("wrong date format", 300, 1, 0.5, "prompt"),           # score 600
    Bucket("cites superseded policy", 140, 3, 3.0, "retrieval"),  # score 140
    Bucket("replies in English to French users", 60, 2, 1.0, "prompt"),  # 120
    Bucket("promises refund outside policy", 12, 5, 2.0, "guardrail"),   # 30, but severity 5
]
for b in sorted(buckets, key=rank_key, reverse=True):
    print(f"{b.name:38} layer={b.layer}")

def release_ok(base: dict, cand: dict, target: str, tol: float = 0.02) -> bool:
    """Pass rates per slice on the same frozen suite; tol should reflect slice sample size."""
    improved = cand[target] > base[target]
    no_regression = all(cand[s] >= base[s] - tol for s in base)
    return improved and no_regression

base = {"refunds": 0.81, "billing": 0.93, "french": 0.88, "shipping": 0.95}
cand = {"refunds": 0.97, "billing": 0.94, "french": 0.84, "shipping": 0.95}
print(release_ok(base, cand, "refunds"))   # False: french fell 4 points, beyond tolerance
```

The refund guardrail goes first despite the lowest score. The candidate fixes refunds but is blocked,
because the fix cost the French slice four points. That is exactly the regression that
reactive patching would have shipped.

Is four points real or noise? Suppose (illustratively) the French slice has 250 cases: 0.88 → 0.84 is
ten net cases. Paired, say 13 flipped pass→fail and 3 fail→pass: `(13 − 3)² / 16 ≈ 6.3`, above 3.84, so
block. Had the slice held 25 cases, four points would be one case, and the right response is to rerun or
grow the slice, not to trust a fixed 2-point tolerance. In this scenario the cause was that the new
refund instruction ("always state the policy in English legal terms") overrode the language rule, which
the trace of a flipped case shows directly.

## Trade-offs
- A fast release train shortens time-to-fix but makes attribution harder; batch fewer changes per release when you need to know which change moved the metric.
- Fixing at the prompt layer is quick and cheap, but it accumulates brittle instructions; once the same bucket returns after several prompt fixes, move the fix to retrieval, tools or fine-tuning.
- A growing regression suite catches more, but it gets slower and costlier to run with an LLM judge; prune duplicate and obsolete cases, and tier the suite into fast and full runs.
- Fixing one customer's failure in shared prompts helps everyone but risks others' behaviour; when the failure is truly customer-specific, put the fix in that customer's configuration and suite.

## Pitfalls
- Whack-a-mole prompt edits without a suite: the symptom is complaints you already fixed reappearing a few releases later.
- Prioritising by who complained loudest: the dashboard shows a large, steady bucket that has gone untouched for weeks while small anecdotes get fixed.
- Overfitting to the regression suite: offline pass rates climb every release while production acceptance and edit rates stay flat.
- Tracking bucket counts instead of rates: a fixed failure appears to "come back" in the month traffic doubles, while its rate per 1,000 conversations is still down.
- A gate that ignores run-to-run variance: the same candidate passes on Monday and fails on Tuesday, and the team learns to rerun until green.
- Unversioned changes: production quality shifts and nobody can tell whether the prompt edit, the re-index or the vendor's model update caused it.
- Feedback that disappears into a backlog: feedback volume drops over time because users learn that reporting changes nothing.

## When to use
Run this loop for any AI system in production with real users, starting in hypercare and continuing for
the system's life. The cadence can slow as the failure buckets shrink, but it should not stop, because
inputs and dependencies keep changing. It is less useful during early prototyping, when the design itself
is still moving and a frozen suite would freeze the wrong behaviour. Measure the loop itself: weekly
failure rate by bucket, time from report to fix, regression escapes per release, and production
acceptance trend. In interviews, describe the loop end to end and stress the gate and the online check.

## Role lens
- **Engineer:** Writes the classifier and the gate, adds failing cases to the suite before each fix, and
  versions prompts and configs so production changes are attributable.
- **Architect:** Owns the release process: eval gates, canary or shadow stages, which changes need full
  suites, and how customer-specific fixes are isolated from shared behaviour.
- **FDE:** Runs the weekly triage with the customer, translates their complaints into failure buckets, and
  reports back which fixes shipped and what moved.

## Check yourself
**Q:** After six weeks of prompt fixes driven by user complaints, the offline suite pass rate is up from
82% to 94%, but production acceptance is flat. What is likely happening and what do you change?
**A:** The suite and production have diverged. The suite probably holds only the complaint cases, so the
fixes overfit to them, while the bulk of production failures were never classified. Unreported failures
could also be growing, from drift or new users. I would review a random production sample to rebuild the
failure taxonomy, add representative cases rather than only complained-about ones, and check whether
prompt patches have accumulated into conflicting instructions. Then I would prioritise by measured bucket
frequency and severity. Each release would be validated by an online check that the targeted bucket
shrank, not only by offline pass rate.

## Quiz
### Q: Why add the failing production cases to the regression suite before implementing the fix?
- [ ] It makes the release pipeline run faster
- [x] It proves the suite detects the failure, and it keeps the failure detected after later changes
- [ ] It is needed so the LLM judge can learn from the examples
- [ ] It prevents the vendor from changing the model
> Encoding first is the test-driven pattern: a test that fails before the fix and passes after shows the
> suite actually covers the failure. That protects against regressions from future changes.

### Q: A bucket of "promises refunds outside policy" has 12 weekly cases, while "wrong date format" has 300. Which prioritisation is sound?
- [ ] Date format first, because frequency × severity ÷ effort gives it the higher score
- [x] Refund promises first, because harmful or non-compliant failures override the frequency score
- [ ] Whichever is cheaper to fix, because effort dominates
- [ ] Neither, until the counts are statistically significant
> Pure scores rank frequent cosmetic issues above rare costly ones. A severity override puts harmful,
> non-compliant or money-losing failures first, then the score orders the rest.

### Q: A candidate release raises the targeted slice from 81% to 97% but drops another slice by 4 points, beyond the agreed tolerance. What should happen?
- [ ] Ship it, because the average pass rate across slices went up
- [ ] Ship it and add the dropped slice to next week's triage
- [x] Block it and rework the fix, because the gate requires no regression beyond noise on any slice
- [ ] Widen the tolerance so the release passes
> Averages hide regressions for specific user groups. The per-slice no-regression rule exists to catch
> exactly this kind of coupled side effect before it reaches production.
