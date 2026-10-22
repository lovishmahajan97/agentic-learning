---
id: regression-tests
module: evals
name: Regression tests
kind: practice
difficulty: core
est_minutes: 15
status: crisp-verified
related:
  - evals/golden-dataset
  - evals/eval-harness
  - evals/eval-driven-development
  - evals/pass-k
  - llmops-platform/ci-cd-for-prompts-and-models
  - llmops-platform/evaluation-gates-for-production-releases
  - customer-data-evaluation/customer-specific-regression-suites
  - prompting/prompt-versioning
sources:
  - Official docs: pytest
---

## TL;DR
An AI regression test re-runs pinned inputs whenever prompt, model, retriever or tools change, and fails
the change if passing cases now fail. LLM changes are non-local, so the hard part is separating real
regressions from noise with paired, per-item comparisons.

## Why it exists
- **A prompt is one shared artifact.** In ordinary code a change to function A does not alter B. A
  prompt shapes every output at once: "always use ISO dates" changes the tone of refund answers; a newer
  model version follows a JSON schema more or less strictly; a higher retriever top-k changes citations.
- **Fixes reintroduce old bugs.** Fixing bug #1 brings back bug #3 fixed last month, and nobody notices
  until the customer does. Without a pinned suite the team plays whack-a-mole with no memory.
- **The system changes with no commit.** Hosted models are updated, deprecated and re-routed; a
  dependency bumps the tokenizer; the index is rebuilt with a different chunker. A diff-only suite never
  sees these.
- **The naive alternatives fail.** Re-checking the five remembered examples is biased toward the cases
  just fixed. "Full eval before release" finds regressions weeks late across dozens of commits, with no
  cheap bisect. Exact-string snapshots fail on every harmless rephrasing, so someone adds a skip flag.
- **What this borrows from unit tests.** Fast, automatic, per change, but with behavioural assertions
  and judges instead of string equality, and a noise-aware baseline comparison instead of "all pass".

## How it works
### The suite is a golden set with a contract
Each case holds:
- an input, plus any context, tool stubs or conversation history;
- one or more graders and the expected behaviour;
- tags: slice, criticality, the incident it came from.

| Tier | Size and graders | When it runs | Catches |
|---|---|---|---|
| **Smoke** | A few dozen cases, deterministic graders (schema valid, must or must not contain, tool-call sequence, refuses or not) | Every pull request, in minutes | Obvious breakage |
| **Full** | Hundreds of cases, including judge-graded ones | Nightly and before release | Smaller and quality regressions |
| **Scheduled, no diff** | The full tier | On a schedule regardless of commits | Provider-side drift: if Tuesday differs from Monday with nothing committed, the model changed under you |

### Graders ordered by determinism
1. **Code graders first:** schema, regex, numeric tolerance, set equality on extracted entities,
   tool-trace match.
2. **Then reference-based judges**, then rubric judges.
- **Why the order matters:** a flaky grader makes a flaky suite, and a flaky suite is ignored within weeks.
- **Narrow judge questions.** A judge-graded case carries a reference answer and asks "does the answer
  state the 30-day window?", not "is this good".

### Handle nondeterminism explicitly
- **Temperature 0 reduces variance but does not remove it.** Batched inference, floating-point ordering
  and provider changes all still move outputs.
- **Run each case *k* times** and record a pass rate. For a critical case "pass" means all *k* passed
  (pass^k, the reliability reading of pass@k).
- **Read flips against noise.** 3 of 5 before and 3 of 5 after is no regression; 5/5 to 2/5 is.
- **Pin everything pinnable:** model identifier with version or snapshot, prompt hash, index version,
  seed where the API supports it, and recorded external tool responses, so the test exercises the model,
  not the weather API.

### Compare per item, paired, not by aggregate
- **Aggregates say little.** 82% versus 80% could be two items, or ten flipping each way.
- **Two lists.** Regressions are items that went pass to fail; improvements the reverse. A human reviews
  both, because a "regressed" item may have a stale expectation.
- **McNemar's test on discordant pairs** checks paired pass/fail significance:

```
statistic = (b − c)² / (b + c)      b = fail→pass flips, c = pass→fail flips
significant at p < 0.05 (one degree of freedom) when statistic > 3.84
```

| Scenario | Value |
|---|---|
| 300 items, 20 fail→pass, 14 pass→fail | (20 − 14)² / (20 + 14) ≈ 1.06 < 3.84: no claim of net improvement |
| What it still gives you | The 14 regressed items to inspect |
| Resolution with 34 discordant pairs | Net difference must be at least 12 (23 against 11) to clear 3.84 |
| Few discordant pairs (under about 25) | Use the exact binomial version, not the chi-squared approximation |

- **Publish the minimum detectable effect** next to the suite, so nobody reads a two-item difference as
  a result.

### Run the baseline in the same job
- **Pair in time as well as by item.** A baseline from last month differs from today's candidate by the
  change *and* by drift (a provider snapshot, a rebuilt index), so the flip list mixes both.
- **Re-run production config** on the same cases in the same job, or at least the same night, and diff
  against that.
- **The stored baseline** then serves only the no-diff drift run, where drift is what you want to see.

### Gate policy
| Condition | Action | Because |
|---|---|---|
| Any smoke failure | Block | Cheap, deterministic |
| Any flip of a case tagged critical (from production incidents or contractual requirements) | Block | Cheap to run, expensive to break |
| Net regression beyond a tolerance on the full tier, per slice and overall | Block | A real aggregate loss |
| Judge-graded single-item flip | Warn, do not block | Judge noise would otherwise block every PR; it should drive review |

### The growth loop
1. **Every production incident** becomes a case tagged with its ticket.
2. **Every resolved review disagreement** ("should it have refused?") becomes a case.
3. **Intended behaviour changes** update the case's expectation in the same commit as the prompt.
4. **Version the suite with the prompt**, so a baseline is always comparable with its own expectations.

## Diagram
A pull request runs the smoke tier, then candidate and production baseline run together on the same
cases; the flip list, not the average, drives the gate.

```mermaid
flowchart TD
    PR["Prompt, model, retriever or tool change"] --> SM["Smoke tier: deterministic graders"]
    SM -->|"any failure"| BL["Block"]
    SM -->|"green"| RUN["Candidate and production baseline, same job, k samples"]
    RUN --> FL["Per-item flip lists"]
    FL --> CR{"Critical case flipped?"}
    CR -->|"yes"| BL
    CR -->|"no"| MC["McNemar and per-slice tolerance"]
    MC -->|"net regression"| BL
    MC -->|"within tolerance"| RV["Human reviews both flip lists"]
    RV --> MG["Merge"]
    CRON["Scheduled run, no diff"] -.->|"drift vs stored baseline"| FL
```

## Examples
### pytest suite over a JSONL case file
Each case runs *k* times, critical cases must pass every time, other cases fail only if they clearly
got worse than the recorded baseline, and the fixture writes candidate pass rates so a separate step can
diff them against the baseline per slice.

```python
# tests/test_regression.py  ->  pytest -q tests/test_regression.py
import json, pathlib, pytest
from myapp import answer                      # system under test: (input, context, model) -> dict
from myapp.graders import GRADERS             # {"schema": fn, "contains": fn, "judge_ref": fn, ...}

CASES = [json.loads(l) for l in pathlib.Path("evals/regression.jsonl").read_text().splitlines()]
# {case_id: pass_rate}, written by running the production config in the same CI job just before this
BASELINE = json.loads(pathlib.Path("evals/baseline_results.json").read_text())
K = 3                                         # samples per case

@pytest.fixture(scope="session")
def results():
    r = {}
    yield r
    pathlib.Path("evals/candidate_results.json").write_text(json.dumps(r, indent=1))
    # a separate step diffs candidate vs baseline per slice and runs McNemar on the flips

def passes(case) -> int:                      # integer count avoids float edge cases like 2/3 < 1 - 1/3
    n = 0
    for _ in range(K):
        out = answer(case["input"], context=case.get("context"), model=case["pinned_model"])
        n += all(GRADERS[g["type"]](out, g) for g in case["graders"])
    return n

@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_no_regression(case, results):
    n = passes(case)
    results[case["id"]] = n / K
    if "critical" in case["tags"]:
        assert n == K, f"critical case {case['id']} failed {K - n}/{K} runs"
    else:
        base = round(BASELINE.get(case["id"], 0.0) * K)   # baseline pass count at the same K
        # tolerate one sample of noise; a drop larger than that is a real per-item regression
        assert n >= base - 1, f"{case['id']} regressed: {base}/{K} -> {n}/{K}"
```

### The diff step: flip lists and McNemar
Pure Python, runnable. It treats a case as passing when its pass rate is 1.0, builds both flip lists
and reports the chi-squared statistic plus the exact binomial p-value for small counts.

```python
from math import comb

def flips(base: dict, cand: dict) -> tuple[list, list]:
    ok = lambda r: r >= 1.0
    up = [c for c in cand if c in base and not ok(base[c]) and ok(cand[c])]
    down = [c for c in cand if c in base and ok(base[c]) and not ok(cand[c])]
    return up, down

def mcnemar(b: int, c: int) -> tuple[float, float]:
    n = b + c
    chi2 = (b - c) ** 2 / n if n else 0.0
    exact_p = min(1.0, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)
    return round(chi2, 2), round(exact_p, 3)

print(mcnemar(20, 14))   # (1.06, 0.392)  not significant: review the 14 regressions anyway
print(mcnemar(23, 11))   # (4.24, 0.058)  clears 3.84, but borderline on the exact test
print(mcnemar(6, 0))     # (6.0, 0.031)   few pairs: trust the exact p-value
```

### Worked scenario: a "neutral" model upgrade
Illustrative numbers. The provider's new snapshot is swapped in; the full tier has 400 items.

| Step | Observation |
|---|---|
| Aggregate | 80% → 80%, looks harmless |
| Flip lists | 9 fail→pass, 9 pass→fail |
| Critical tags | 2 of the 9 regressions are incident cases (refund window, PII redaction) |
| Gate | Blocked on critical flips, regardless of the flat average |
| Fix | Prompt clarification, re-run in the same job: critical cases 3/3 again |

## Production patterns
- **Three cadences in CI.** Smoke tier on every pull request, full tier nightly, and the same full tier
  on a scheduled trigger with no diff (for example a cron `schedule` in GitHub Actions). Monitor
  no-diff flip counts as the provider-drift signal.
- **Incident-to-case as part of the postmortem.** The postmortem is not closed until the triggering
  input is a case tagged `critical` with the ticket ID and an owner. Monitor the share of incidents with
  a linked case.
- **Same-job baseline.** The CI job first runs the production config, writes the baseline file, then
  runs the candidate, as in the example. Monitor flips on cases the change could not plausibly affect.
- **Record and replay external tools.** Store tool and HTTP responses as fixtures (for example with
  vcrpy cassettes) so cases are hermetic, plus a small live suite for integration. Monitor fixture age.
- **Flakiness budget.** Periodically run the suite twice on an identical config and count items whose
  pass rate differs; fix graders before adding cases when it exceeds the tolerance. Monitor the
  flakiness rate per grader type.
- **Published resolution.** Print the minimum detectable effect (from McNemar on the expected number of
  discordant pairs) at the top of each report. Monitor reviewers citing differences below it.

## Trade-offs
- **k samples vs cost.** Running each case *k* times multiplies cost and time by *k*, but without it one
  unlucky sample blocks a PR; drop to *k* = 1 only for deterministic graders on temperature-0
  structured outputs.
- **Deterministic vs judge graders.** Deterministic graders are stable and fast but blind to quality;
  judges see quality but add noise, so the mix shifts toward judges from smoke tier to nightly tier.
- **Blocking on critical flips vs tax.** Right when cases come from incidents, a tax if "critical" is
  applied loosely; the tag needs an owner and a justification per case.
- **Recorded tool responses vs integration coverage.** Hermetic and cheap, but they hide integration
  breakage, which then needs its own smaller live suite.
- **Suite size vs speed.** A large suite detects small regressions but runs longer and costs more per
  PR; small smoke plus nightly full is the usual compromise, and it flips when the model is cheap or
  self-hosted.

## Pitfalls
- **Comparing aggregate pass rates.** Symptom: "82% vs 81%, looks fine" while ten items flipped each
  way and a customer-facing case is among the losses. Fix: review the flip lists.
- **Asserting exact output strings.** Symptom: failures on every harmless rephrasing until someone
  disables the suite. Fix: behavioural assertions and narrow judges.
- **Running only on diffs.** Symptom: a regression on a day with no commits, traced weeks later to a
  provider model update. Fix: the scheduled no-diff run.
- **Stale expectations after an intended change.** Symptom: a permanent red test everyone ignores, which
  hides the next real failure. Fix: update expectations in the same commit.
- **Diffing against a weeks-old baseline.** Symptom: flips on cases the change could not plausibly
  affect, because provider or index drift is attributed to the PR. Fix: same-job baseline.
- **Cases leaking into the prompt as few-shot examples.** Symptom: a perfect suite score and unchanged
  production quality. Fix: keep cases out of prompts.
- **A judge tuned on the same cases.** Symptom: the suite passes outputs the customer rejects. Fix: tune
  judges on a separate split.

## When to use
- **Start:** the day the first production bug is found, with that bug as case one.
- **Run:** the smoke tier on every change to a prompt, model setting, retriever or tool schema; the
  full tier before any release and on a schedule regardless of commits.
- **Measure:** per-item flips, per-slice pass rates and the suite's own flakiness (items whose pass rate
  differs between two identical runs); if flakiness exceeds your tolerance, fix graders first.
- **Avoid when:** measuring capability. The suite is a safety net of known behaviour; a golden set with
  held-out items answers "is the new version better".
- **Interviews:** explain why comparisons are paired and per item, how you handle nondeterminism, and how
  the suite grows from incidents.

## Role lens
- **Engineer:** Owns the case file and graders, wires the smoke tier into CI, pins model and index
  versions, and reviews the flip list on every change rather than the headline number.
- **Architect:** Sets the gate policy: which slices and critical tags block a release, what tolerance
  applies to judge-graded tiers, and how the suite is versioned alongside prompts and models.
- **FDE:** Turns each customer incident into a tagged case in that customer's suite and runs it before
  every prompt change, so "we fixed it last time" is a test, not a memory.

## Check yourself
**Q:** A prompt change raises the nightly suite from 80% to 83% on 400 items, and the team wants to
merge. What would you look at before agreeing, and what could make 83% worse than 80%?
**A:** First the flip lists, not the aggregate: which items went fail-to-pass and which went
pass-to-fail. A 3-point gain can hide, say, 30 improvements and 18 regressions, and if any regression is
a critical case from a production incident the change is blocked regardless of the average. Then the
per-slice view, because a gain concentrated in one intent can mask a loss in another that matters more
to the customer. Then noise: with *k* samples per item, how many flips are within one sample of
baseline, and does McNemar on the discordant pairs even reach significance at this suite size. Finally
whether any expectations were edited in the same change, which would be a prompt tuned to the test.

## Interview questions
### How is a regression test for an LLM system different from a unit test?
- **Same shape:** fast, automatic, run on every change, blocking on failure.
- **Different assertions:** behavioural checks and narrow reference-based judges replace string
  equality, because harmless rephrasings would break snapshots.
- **Different pass rule:** each case runs *k* times and the comparison is against a noise-aware baseline,
  because outputs are not deterministic even at temperature 0.

### A regression appears on a Tuesday when nothing was merged. How do you find the cause?
- **Confirm with the no-diff run:** flips against Monday's stored baseline with no commit point at drift.
- **Check pinned versions:** model snapshot, index version, tokenizer and dependency versions; an
  unpinned model alias is the usual culprit.
- **Bisect the environment,** not the code: re-run with yesterday's index or model snapshot to isolate
  which component moved.

### Why not just block any PR whose aggregate pass rate drops?
- **Aggregates hide flips:** 80% to 80% can contain several regressions, including critical ones.
- **Aggregates also cry wolf:** a one-item drop is usually noise; with 34 discordant pairs you need a
  net difference of 12 to clear McNemar's 3.84.
- **Better gate:** block on smoke failures and critical flips, apply a per-slice tolerance, and only warn
  on judge-graded single flips.

### How do you keep a regression suite from becoming flaky and ignored?
- **Order graders by determinism**, using judges only with a reference and a narrow question.
- **Pin and record:** model snapshot, prompt hash, index version, seeds where supported, recorded tool
  responses.
- **Measure flakiness** by running an identical config twice, and fix graders before adding cases when
  it exceeds the tolerance.

### What goes into the suite, and what does not?
- **In:** every production incident, every resolved review disagreement, contractual requirements, tagged
  with slice and criticality.
- **Not in the prompt:** cases must never double as few-shot examples, or the score becomes meaningless.
- **Not a capability benchmark:** "is the new version better" belongs to a held-out golden set.

## Quiz
### Q: Why are regression results compared per item as paired pass/fail flips rather than as two aggregate pass rates?
- [ ] Aggregate rates cannot be computed for judge-graded items
- [x] Equal aggregates can hide many items flipping in both directions, and the pass-to-fail list is what needs review
- [ ] Per-item comparison avoids the need for a baseline run
- [ ] Aggregates are only valid when every case is deterministic
> Two runs at 82% and 80% could differ on two items or on twenty flipping each way. Pairing by item
> isolates the regressed set for human review and allows a proper paired test (McNemar) on discordant
> pairs; the baseline is still required.

### Q: Why should the full regression suite also run on a schedule when nothing has been committed?
- [ ] To warm the model provider's cache before the working day
- [ ] To accumulate enough samples for the aggregate score
- [x] To detect changes in hosted models, indexes or dependencies that arrive without a code diff
- [ ] Because judge graders need repeated runs to converge
> Hosted models get updated under the same name, indexes get rebuilt and tokenizers get bumped. A run
> that differs from yesterday's with no commit is the only way to catch that drift before a customer
> does.

### Q: A team asserts exact string equality on every regression case. What happens within a few weeks?
- [ ] The suite becomes fully deterministic and stays green
- [x] Harmless rephrasings fail constantly and the suite gets skipped or disabled
- [ ] The model learns to produce the expected strings
- [ ] Judge graders become unnecessary
> Free-text outputs vary in wording even when the behaviour is identical, so snapshot equality flags
> every run. The practical response is to replace it with behavioural assertions, schema checks and
> narrow reference-based judges.
