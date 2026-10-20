---
id: uat
module: deployment
name: UAT (user acceptance testing)
kind: practice
difficulty: core
est_minutes: 16
status: reviewed
related:
  - customer-data-evaluation/acceptance-testing-against-customer-kpis
  - evals/task-specific-acceptance-criteria
  - customer-data-evaluation/representative-test-case-sampling
  - customer-data-evaluation/sme-review-and-annotation-guidelines
  - customer-data-evaluation/customer-specific-regression-suites
  - enterprise-integration/sandbox-vs-production-environments
  - deployment/deployment-readiness-reviews
  - customer-data-evaluation/shadow-mode-validation
---

## TL;DR
User acceptance testing is the phase where the customer's real users, not the builders, run the system
on realistic tasks and decide whether it meets the acceptance criteria agreed up front. It exists because
"it passes our tests" and "it does the job our users need" are different claims. For AI systems, UAT must
also handle non-deterministic output: score samples against a rubric, size the sample so the pass rate
is statistically meaningful, and separate model-quality defects from integration bugs.

## Why it exists
Engineering tests verify that the system behaves as the builders understood the requirement. They
cannot catch a requirement that was misunderstood, a workflow step nobody mentioned, or an output that is
technically correct but useless in context: a claims summary that omits the one field adjusters always
check, an answer that cites the right policy but in a format the compliance team cannot file. Only the
people who will live with the system know those things, and they often only discover them by using it.

UAT also has a contractual role. In enterprise and forward-deployed work, acceptance is often the
milestone that triggers payment or the move from pilot to production. Without a defined UAT, acceptance
becomes a negotiation over impressions: the sponsor saw two bad answers in a demo and the project
stalls, or everyone says "looks good" and the system is abandoned three weeks after launch.

The naive version, "give users access for two weeks and ask what they think", fails for AI systems in
particular. Users try a handful of queries, judge on memorable failures, and each person's sample is too
small to mean anything. Because model output varies run to run, the same question may pass for one
tester and fail for another, and without a rubric the disagreement cannot be resolved. A useful UAT is
designed like an experiment: fixed criteria, representative cases, a scoring rubric, a sample large
enough to separate "good enough" from "not quite".

## How it works
### 1. Fix acceptance criteria before testing starts
Criteria come from scoping and are written as measurable statements: "at least 85% of triage
recommendations rated correct or acceptable by adjusters", "zero responses that disclose another
customer's data", "p95 response under 8 seconds". Splitting criteria into **hard gates** (safety,
privacy, legal: any failure blocks) and **rate thresholds** (quality metrics judged on a sample) matters
*because* a single privacy leak is not averaged away by 99 good answers.

### 2. Build a representative case set
Draw cases from real historical work, stratified by the slices the business cares about (claim type,
language, document quality, customer tier), plus deliberately chosen edge cases. Representativeness
matters *because* the pass rate on easy cases tells you nothing about the hard ones. Keep each case's
expected outcome or reference answer, written by subject-matter experts, so scoring is not improvised.

### 3. Run in a production-like environment
UAT runs against the real integrations (identity, document stores, downstream systems) with
production-shaped data, in an environment isolated from production writes. Running on mocks would hide
the integration failures that UAT is best at catching: permission errors, slow connectors, malformed
documents.

### 4. Score with a rubric, not a thumbs-up
Testers rate each output on a small scale with written definitions (for example *correct*, *acceptable
with minor edit*, *wrong*, *harmful*). Overlap a fraction of cases between testers and measure agreement,
*because* if two adjusters disagree on the same output the criterion itself is ambiguous and must be
fixed before the numbers mean anything.

### 5. Size the sample and handle non-determinism
A rate threshold is a claim about the true pass rate, so the sample must be big enough that the lower
end of its confidence interval clears the bar. A quick planning estimate uses the normal approximation:

```
n ≈ z² · p(1 − p) / m²        z = 1.96 for 95%, p = expected pass rate, m = margin you can afford
```

If you expect about 88% against an 85% bar, the margin is 3 points: 1.96² × 0.88 × 0.12 / 0.03² ≈ 450
cases. The Wilson interval used to report the result is slightly more conservative and needs roughly 550.
The lesson of the formula is the *m²* in the denominator: halving the margin quadruples the cases, which
is why a criterion set just below the expected rate is expensive to prove, and why negotiating the bar
and the sample together during scoping is cheaper than discovering the problem at sign-off.

Non-determinism changes what counts as a sample. Running each case three times does not turn 200 cases
into 600 independent observations, *because* runs of the same case share the same input and difficulty
and fail together. Score at the case level (for example "passes on at least 2 of 3 runs", or the
per-case pass fraction averaged across cases), and report the run-to-run flip rate separately: a case
that passes once and fails once is a stability defect in its own right, even if the average looks fine.

### 6. Triage defects by cause
Every failure gets a severity (blocker, major, minor) and a cause: **integration** (wrong data reached the
model), **retrieval** (right data existed, was not found), **model/prompt** (right context, wrong answer),
or **requirement** (system did what was specified, specification was wrong). The cause decides the fix and
whether a re-test is needed. A "requirement" finding is a change request, not a defect, and treating it as
a defect is how UAT never ends.

### 7. Exit and sign off
UAT exits when hard gates are clean, rate thresholds are met with enough confidence, and no open blockers
remain. Sign-off is a named person on the customer side, recorded. Every failing case found during UAT
goes into the customer-specific regression suite so it stays fixed.

## Example
An insurer's acceptance criterion is "at least 85% of triage recommendations rated correct or
acceptable". Adjusters score 200 stratified cases and 176 pass: **88%**. The project manager wants to
sign off. The question is whether 88% on 200 cases is evidence that the true rate is at least 85%.

A 95% Wilson interval for 176/200 is roughly **82.8% to 91.8%**. The lower bound is below 85%, so this
sample cannot distinguish "comfortably above the bar" from "slightly below it". With the same 88% rate,
400 cases give roughly 84.4% to 90.8% (still not clear), and about 600 cases give roughly 85.2% to 90.4%,
which clears it. So the options are concrete: score about 400 more cases, renegotiate the criterion to be
judged on the point estimate (an explicit, recorded decision), or improve the system so the observed rate
rises.

The slice view matters too. Of the 24 failures, 15 are on handwritten claim forms, which are 20% of cases
(40 of 200). That slice passes only 25 of 40, about 62%, while typed forms pass 151 of 160, about 94%.
The aggregate hides that the system is not fit for handwritten claims. Triage shows 11 of the 15 are
OCR-quality failures (integration), not model reasoning, so the fix is the document parser, followed by a
re-test of that slice only.

## Trade-offs
- Larger, stratified case sets give statistically meaningful results, but consume scarce SME time; when
  experts are expensive, spend the budget on the high-risk slices and accept wider intervals elsewhere.
- Scripted test cases make results comparable and repeatable, while exploratory use surfaces unknown
  workflow needs; run both, and weight exploratory findings more heavily early in UAT.
- Strict exit criteria protect the customer, but may block a system that is already better than the
  current manual process; when a baseline exists, judging against it instead of an absolute bar can be
  the more honest criterion.
- Testing on production-like data catches real integration failures, but requires data access approvals
  that can take weeks; start those approvals at kickoff, not at UAT.

## Pitfalls
- Defining acceptance criteria during UAT rather than before; the symptom is a moving target where each
  round of fixes surfaces a new expectation nobody wrote down.
- Letting the build team score outputs instead of the customer's users; the symptom is a high UAT pass
  rate followed by low adoption and complaints in the first week of hypercare.
- Running each case once and treating the result as fixed; the symptom is testers reporting a failure
  that the engineers "cannot reproduce" because a re-run happens to pass.
- Treating every change request as a defect; the symptom is UAT rounds that never converge and a
  backlog where scope creep is indistinguishable from bugs.
- Judging on the aggregate only; the symptom is a passing headline number while one important slice,
  such as one language or document type, fails badly.

## When to use
Run UAT whenever a system will be used by people other than its builders and acceptance carries weight:
a customer go-live, a pilot-to-production gate, a contractual milestone. Plan it during scoping, because
the case set, rubric, environment and data approvals take longer than the testing itself. For internal
tools with a small user group, a lighter version (a few power users, a short rubric) is enough. Measure
pass rate with a confidence interval, per-slice pass rates, inter-rater agreement and defect counts by
cause. In interviews, the depth signal is talking about criteria fixed in advance, sample size,
non-determinism and triage by cause, rather than "users tried it and liked it".

## Role lens
- **Engineer:** Builds the UAT harness: case loading, repeated runs per case, rubric capture, and export
  of every failure into the regression suite with its trace attached.
- **Architect:** Defines the UAT environment boundary, ensuring production-like integrations and data
  without production writes, and owns how acceptance criteria map to measurable signals.
- **FDE:** Recruits the customer's testers, runs calibration on the rubric, triages findings with them,
  and separates genuine defects from change requests in front of the sponsor.

## Check yourself
**Q:** UAT testers rate a summarisation feature at 90% acceptable, but two senior adjusters say it is
unusable. How do you reconcile this before sign-off?
**A:** First check whether the disagreement is about the rubric or the cases. Have the seniors and other
testers score the same overlapping outputs; low agreement means "acceptable" is defined differently and
the rubric needs tightening, perhaps because seniors need a field others ignore. Then check slices: the
seniors may handle complex claims that are under-represented in the case set. If so, stratify, add cases
from their workload and report per-slice rates. If their concern is a workflow need never in the
criteria, record it as a change request with the sponsor rather than a defect, and decide whether it
blocks.

## Quiz
### Q: Why split UAT criteria into hard gates and rate thresholds?
- [ ] Hard gates are tested by engineers and rate thresholds by users
- [x] Some failures, like a privacy leak, are unacceptable even once, so they cannot be averaged into a pass rate
- [ ] Rate thresholds only apply to latency, not quality
- [ ] Hard gates make UAT shorter by skipping sampling
> A pass rate averages over cases, which is right for quality but wrong for safety, privacy or legal
> failures where a single occurrence is a blocker regardless of how many other cases succeed.

### Q: UAT shows 88% pass on 200 cases against an 85% bar. What is the most defensible conclusion?
- [ ] The system passes because 88% exceeds 85%
- [ ] The system fails because some cases failed
- [x] The sample is too small to show the true rate clears 85%, so either test more cases or explicitly agree to judge on the point estimate
- [ ] The result is invalid because AI outputs are non-deterministic
> The 95% interval for 176 of 200 reaches below 85%, so the data is consistent with being under the bar.
> Non-determinism does not invalidate sampling; it is one more reason to treat the rate statistically.

### Q: A tester reports that the system correctly followed the specification, but the specified output format does not fit their filing process. How should it be classified?
- [ ] As a blocker defect in the model
- [ ] As a retrieval failure
- [ ] As a minor integration bug
- [x] As a requirement finding, handled as a change request with the sponsor
> The system did what was specified; the specification was wrong or incomplete. Treating it as a defect
> hides scope change and stops UAT from converging, while a change request makes the cost explicit.
