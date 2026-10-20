---
id: regression-tests
module: evals
name: Regression tests
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
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
A regression test for an AI system re-runs a pinned set of inputs with known expected behaviour every
time the prompt, model, retriever or tools change, and fails the change if cases that used to pass now
fail. It exists because LLM changes are non-local: a prompt edit that fixes one case silently breaks
others, and the provider can change the model behind the same name. The engineering problem is telling
a real regression from sampling noise, which is why comparisons are per item and paired, not aggregate.

## Why it exists
In ordinary code a change to function A does not alter function B, so a test suite can be organised
around units. A prompt is one shared artifact that shapes every output at once: adding "always use ISO
dates" changes the tone of refunds answers; switching to a newer model version changes how strictly it
follows a JSON schema; raising the retriever's top-k changes which answers get cited. Fixing bug #1
reintroduces bug #3 that was fixed last month, and nobody notices until the customer does. Without a
pinned suite the team is playing whack-a-mole with no memory.

The second force is that the system changes without anyone committing anything. Hosted models are
updated, deprecated and re-routed; a dependency bumps the tokenizer; the retrieval index is rebuilt with
a different chunker. A suite that runs only on diffs never sees these.

The naive alternatives fail in predictable ways. "The engineer re-checks the five examples they
remember" is biased toward the cases that were just fixed and ignores everything else. "Run the full
eval before each release" finds regressions weeks after they were introduced, across dozens of commits,
with no cheap way to bisect. "Snapshot the exact output string" fails on every harmless rephrasing, so
within a month someone adds a flag to skip it. Regression tests for AI systems borrow the shape of unit
tests (fast, automatic, per change) but replace string equality with behavioural assertions and judges,
and replace "all tests pass" with a comparison against a baseline that respects the noise.

## How it works
### The suite is a golden set with a contract
Each case holds an input (and any context, tool stubs or conversation history), one or more graders,
the expected behaviour, and tags: slice, criticality, the incident it came from. Two tiers keep it
runnable. A **smoke tier** of a few dozen cases with deterministic graders (JSON schema valid, must
contain or must not contain, tool-call sequence matches, refuses or does not refuse) runs on every pull
request in minutes. A **full tier** of hundreds of cases including judge-graded ones runs nightly and
before release. A third **scheduled run with no diff** at all catches provider-side drift: if the
Tuesday run differs from Monday's and nothing was committed, the model changed under you.

### Graders ordered by determinism
Prefer code graders (schema, regex, numeric tolerance, set equality on extracted entities, tool-trace
match), then reference-based judges, then rubric judges. The order matters because a flaky grader
makes a flaky suite, and a flaky suite is ignored within weeks. A judge-graded case should carry a
reference answer and a narrow question ("does the answer state the 30-day window?") rather than
"is this good".

### Handle nondeterminism explicitly
Temperature 0 reduces variance but does not remove it: batched inference, floating-point ordering and
provider changes all move outputs. So each case runs *k* times and records a pass rate, and "pass" for
a critical case means all *k* passed (pass^k, the reliability reading of pass@k). A case that passed
3 of 5 before and 3 of 5 after has not regressed; a case that went 5/5 to 2/5 has. Pin everything that
can be pinned: model identifier with version or snapshot, prompt hash, index version, seed where the API
supports it, and recorded responses for external tools so the test exercises the model, not the weather
API.

### Compare per item, paired, not by aggregate
Aggregate pass rate 82% versus 80% says almost nothing: it could be two items, or ten items flipping
each way. The regression list is the set of items that went pass to fail; the improvement list is the
reverse. Both are reviewed by a human, because an item that "regressed" may have a stale expectation.
For a significance check on paired pass/fail outcomes use McNemar's test on the discordant pairs. With
300 items where 20 flipped fail-to-pass and 14 flipped pass-to-fail, the statistic is
(20 − 14)² / (20 + 14) ≈ 1.06, below the 3.84 threshold for p < 0.05 with one degree of freedom, so the
suite cannot claim the change is a net improvement; it can still hand you the 14 regressed items to
inspect. Inverting the formula gives the suite's resolution: with 34 discordant pairs the net difference
must be at least 12 (23 against 11) to clear 3.84. When discordant pairs are few (under about 25), use
the exact binomial version of the test instead of the chi-squared approximation. Publish this minimum
detectable effect next to the suite so nobody reads a two-item difference as a result.

### Run the baseline in the same job
Pair in time as well as by item. A baseline recorded last month and a candidate run today differ by the
change *and* by whatever drifted in between (a provider snapshot, a rebuilt index), so the flip list
mixes both. Re-run the current production configuration on the same cases in the same job, or at least
the same night, and diff against that; the stored baseline then serves only for the no-diff drift run,
where drift is exactly what you want to see.

### Gate policy
Block the change on any smoke failure, on any flip of a case tagged critical (cases born from
production incidents or contractual requirements), and on a net regression beyond a tolerance on the
full tier, per slice as well as overall. Warn, do not block, on judge-graded single-item flips, because
judge noise would otherwise block every PR. The asymmetry is deliberate: deterministic critical cases
are cheap to run and expensive to break; judge-graded ones are noisy and should drive review, not
blocks.

### The growth loop
Every production incident becomes a case tagged with its ticket. Every disagreement in a review ("should
it have refused?") becomes a case once resolved. When expected behaviour legitimately changes, the
case's expectation is updated in the same commit as the prompt, and the suite is versioned alongside
the prompt so a baseline is always comparable with its own expectations.

## Example
A pytest suite over a JSONL case file. Each case is run *k* times, critical cases must pass every time,
other cases fail only if they clearly got worse than the recorded baseline, and the fixture writes the
candidate pass rates so a separate step can diff them against the baseline per slice.

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

## Trade-offs
- Running each case *k* times multiplies cost and time by *k*, but without it a single unlucky sample
  blocks a PR; drop to *k* = 1 only for deterministic graders on temperature-0 structured outputs.
- Deterministic graders are stable and fast but blind to quality; judge graders see quality but add
  noise, so the mix shifts toward judges as the suite moves from the smoke tier to the nightly tier.
- Blocking on any critical flip is right when the cases come from incidents, but it becomes a tax if
  "critical" is applied loosely; the tag needs an owner and a justification per case.
- Recording external tool responses makes tests hermetic and cheap but hides integration breakage,
  which then needs its own smaller live suite.
- A large suite detects small regressions but takes longer to run and costs more per PR; a small
  smoke tier plus nightly full tier is the usual compromise, and it flips when the model is cheap or
  self-hosted.

## Pitfalls
- Comparing aggregate pass rates between runs; the symptom is "82% vs 81%, looks fine" while ten items
  silently flipped each way and a customer-facing case is among the losses.
- Asserting exact output strings; the symptom is a suite that fails on every harmless rephrasing until
  someone disables it.
- Running only on diffs; the symptom is a regression that appears on a day with no commits, traced
  weeks later to a provider model update.
- Letting the suite's expectations go stale after an intended behaviour change; the symptom is a
  permanent red test that everyone has learned to ignore, which hides the next real failure.
- Diffing against a baseline recorded weeks ago; the symptom is flips on cases the change could not
  plausibly affect, because provider or index drift is being attributed to the pull request.
- Letting cases leak into the prompt as few-shot examples; the symptom is a perfect suite score and
  unchanged production quality.
- Grading with a judge whose prompt was tuned on the same cases; the symptom is a suite that passes
  outputs the customer rejects.

## When to use
Start the suite the day the first production bug is found, with that bug as case one, and run the smoke
tier on every change to a prompt, model setting, retriever or tool schema. Reach for the full tier
before any release and on a schedule regardless of commits. Measure per-item flips, per-slice pass
rates and the suite's own flakiness (rate of items whose pass rate differs between two identical runs);
if flakiness exceeds the tolerance you set, fix graders before adding cases. Do not use the regression
suite to measure capability: it is a safety net of known behaviour, and a golden set with held-out items
answers "is the new version better". In interviews, the expected depth is to explain why comparisons
are paired and per item, how you handle nondeterminism, and how the suite grows from incidents.

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
