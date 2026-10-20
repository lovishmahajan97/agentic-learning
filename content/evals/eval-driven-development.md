---
id: eval-driven-development
module: evals
name: Eval-driven development
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - evals/golden-dataset
  - evals/eval-harness
  - evals/error-analysis
  - evals/regression-tests
  - evals/task-specific-acceptance-criteria
  - evals/offline-vs-online-evaluation
  - llmops-platform/ci-cd-for-prompts-and-models
  - prompting/prompt-versioning
sources:
  - Beck, 2002 — Test-Driven Development: By Example
  - Husain, 2024 — Your AI Product Needs Evals
  - Shankar et al., 2024 — Who Validates the Validators? Aligning LLM-Assisted Evaluation of LLM Outputs with Human Preferences
---

## TL;DR
Eval-driven development treats the eval suite as the executable specification of an LLM feature:
write the acceptance criteria and a seed golden set before building, judge every change by the eval's
numbers rather than by a demo, and grow the suite from the failures each round of error analysis
finds. It exists because prompt, retrieval and model changes have non-local effects that "it looked
fine in the playground" cannot detect, and because a bug without a failing eval item is never
reliably fixed.

## Why it exists
LLM features break the ordinary development loop in three ways. They are non-deterministic: the same
input gives different outputs. They are non-local: a sentence added to the prompt for case A changes
behaviour on cases B and C that nobody re-checked. And their outputs are open-ended: no compiler or
type checker flags a wrong answer. The playground loop of tweak, try three examples, ship survives
none of these. The usual trajectory is that a user hits a failure, a line is added to the prompt, and
six months later the prompt is two thousand tokens of special cases and nobody can say which lines
still matter or what the last change broke.

Model deprecations make it worse. The vendor retires the model your feature runs on; the replacement is
"better on benchmarks"; you swap it and have no way to say whether your feature got better or worse.

The naive alternatives each fail for a specific reason. Writing evals after the system is built means
the evals encode what the system already does, so they pass by construction and say nothing about the
goal. Relying on vendor benchmarks measures a model on generic tasks, not your task with your prompt
and your data. Relying on production metrics alone is weeks too slow, too noisy to attribute to a
single change, and the damage is done before the metric moves. Test-driven development solved the same
problem for ordinary code: write the failing test first so the test describes the requirement rather
than the implementation. Eval-driven development is that discipline with two adjustments: the tests
are probabilistic and graded with thresholds, and the suite grows from production data rather than
from the specification alone.

## How it works
### The loop
1. **Define before building.** With the domain owner, write the task, the acceptance criteria (metric,
   threshold, and the slices on which it must hold), the budgets that are also requirements (p95
   latency, cost per request, benign refusal rate), and a handful of hand-written good and bad outputs.
   Doing this before seeing model outputs matters *because* criteria written afterwards anchor on
   current behaviour; Shankar et al. observed this drift in people grading outputs.
2. **Seed the golden set.** A few dozen items (an illustrative size) covering the main input types and
   the known hard cases, with expected outputs or a rubric, and the first graders: deterministic where
   possible (schema valid, cites an id present in the context, within length limits), an LLM judge for
   what cannot be checked by code.
3. **Build the simplest version** — one prompt, one model — and run the eval to get a per-slice
   baseline, not just an overall number.
4. **Error analysis on the failures** produces new categories; each becomes new items and, when stable,
   a new grader. The rule that makes the suite grow: a bug is not fixed until an eval item fails before
   the change and passes after.
5. **Change one variable at a time** (prompt, retrieval, model, decoding) and compare to the baseline
   with the harness's confidence intervals, recording the configuration hash with the score, *because*
   a number without its configuration cannot be compared with anything.
6. **Gate by cost.** Deterministic graders run on every commit, judge-graded evals on each pull request
   or nightly, and human review or an online A/B before release. This pyramid exists *because* judge
   calls cost money and minutes, and a gate that takes an hour gets skipped.
7. **Ship and close the loop.** Sample production traces back into the golden set; use online metrics to
   check that the offline score still predicts what users experience.

### One iteration, concretely
An illustrative round: a user reports that the assistant quotes the 30-day refund window to customers
on the annual plan, whose window is 14 days. The engineer first adds six items for annual-plan refund
questions and runs the current system: 1 of 6 passes, so the bug is now a failing eval rather than an
anecdote. The fix, adding plan tier to the retrieval filter, is one change. The eval diff on the
pull request then reads: the six new items 1 → 6 passing; across the other 194 items, 3 improved, 2
regressed, mean 0.81 → 0.84; every slice still above its floor. The reviewer reads the two regressed
traces, finds one is noise (it passes on re-run) and one is a real loss on a question spanning both
tiers, adds that case as an item, and the next iteration starts from there. Nothing in this loop
required anyone to have "tried it in the playground".

### Why thresholds rather than exact assertions
Because of sampling noise, an item may pass seven times in ten. Temperature zero reduces but does not
remove variance. So the gate runs *k* samples per item and compares against the previous accepted run
with a tolerance band, and lists per-item regressions rather than asserting 100%. A flaky gate is worse
than none, *because* people learn to re-run it until it is green.

Compare runs *paired*, item by item, rather than as two independent means. Most of the variance in a
pass rate comes from which items are hard, and that variance is identical in both runs, so the
per-item difference (candidate minus baseline) has a much smaller spread than either mean. A paired
test, or simply counting items that flipped in each direction, detects a real 2-point change on a set
where comparing two unpaired means could not.

### Keeping the evals honest
Keep a held-out subset you rarely inspect, *because* tuning a prompt against the items you look at
overfits them exactly as training on the test set would. Version prompts and datasets together with
their scores. Calibrate every judge against human labels before it gates anything, otherwise you are
optimising the judge rather than the product. Retire or harden items that have passed for months; a
suite at 100% carries no information, though a few should stay as smoke tests.

### What changes for the team
A prompt change is a pull request with an eval diff attached, like a test report. "Did you run the
evals?" replaces "did you try it?". The golden set is reviewed like code, and product owners write and
accept eval items, because the eval is the specification they can read.

## Example
A CI gate in pytest. It runs each golden item *k* times, scores with a deterministic check and a
calibrated judge, and fails only on a drop larger than a noise band against the last accepted run or
on a slice falling below its floor, so sampling noise does not turn it into a gate people ignore. The
band is not a constant to copy: its right width depends on the number of items and on how much the
score moves between identical runs.

```python
# tests/test_eval_gate.py — runs in CI on every prompt, retrieval or model change
import functools, json, statistics
from my_app import answer                          # system under test: answer(question, context) -> str
from graders import cites_valid_source, faithful   # deterministic check + judge calibrated on human labels

DATASET = [json.loads(l) for l in open("evals/support_v7.jsonl")]   # versioned golden set
BASELINE = json.load(open("evals/baseline_v7.json"))                # per-item scores of last accepted run
TOLERANCE, K = 0.03, 3   # illustrative: set the band from repeated baseline runs (~2-3 SD); K samples/item

@functools.lru_cache(maxsize=None)
def run():
    scores = {}
    for item in DATASET:
        outs = [answer(item["question"], item["context"]) for _ in range(K)]
        passes = sum(cites_valid_source(o, item["context"]) and faithful(o, item["context"]) for o in outs)
        scores[item["id"]] = passes / K
    return scores

def test_mean_did_not_drop():
    now, base = statistics.mean(run().values()), statistics.mean(BASELINE.values())
    assert now >= base - TOLERANCE, f"pass rate fell {base:.3f} -> {now:.3f}"

def test_few_items_regressed():
    # an item regressed if it lost at least two of its K samples versus the baseline run
    regressed = [i for i, s in run().items() if round((BASELINE.get(i, 0) - s) * K) >= 2]  # count, not float
    assert len(regressed) <= 0.02 * len(DATASET), f"regressed items: {regressed[:10]}"

def test_every_slice_above_floor():
    for name in {i["slice"] for i in DATASET}:
        ids = [i["id"] for i in DATASET if i["slice"] == name]
        assert statistics.mean(run()[i] for i in ids) >= 0.80, f"slice {name} below floor"
```

When the gate passes and the change is accepted, the new per-item scores are written to
`baseline_v7.json`, so the next change is compared against the state that actually shipped. To size
`TOLERANCE`, re-run the unchanged system a handful of times and look at the spread of the mean: as a
rough guide, the run-to-run standard error of a pass rate near 0.8 over the same 200 items × 3
samples is at most √(0.8 × 0.2 / 600) ≈ 0.016 (less when many items always pass or always fail), and
the difference between two such runs at most about 0.023, so a 0.03 band can still trip on pure noise
now and then. A larger set, or a paired per-item test, narrows it.

## Trade-offs
- Writing evals before building delays the first demo by days but makes every later iteration
  measurable; it flips for a throwaway spike whose purpose is to learn what the task even is, and even
  then the evals come before the second iteration.
- A larger golden set gives tighter confidence intervals but a slower and costlier loop; split it into
  a fast smoke subset per commit and the full set nightly or per release.
- A tolerance band absorbs sampling noise but lets slow drift through across many small accepted drops;
  pair the gate with a trend chart across runs.
- Deterministic graders are cheap and trustworthy but cover only part of quality; judge graders cover
  the rest at the cost of calibration work and per-run spend. Use a judge in the gate only once its
  agreement with human labels has been measured; until then it informs review but should not block.
- Holding out items prevents overfitting but means you cannot freely inspect their failures; rotate
  items between the dev and held-out sets periodically.

## Pitfalls
- Evals written after the system exists. Symptom: 100% from day one while users keep reporting
  failures, because the items describe current behaviour rather than the requirement.
- Tuning the prompt against the full eval set repeatedly. Symptom: the eval climbs while production
  quality does not, and the held-out slice, if there is one, stays flat.
- A hard threshold on a noisy metric. Symptom: CI fails every third run with no change, and engineers
  re-run until green or mark the test skipped.
- Treating the vendor's benchmark as your eval. Symptom: a model swap "improves" on a leaderboard
  while a key slice of your task regresses unnoticed.
- Judge-only evals with no human calibration. Symptom: scores rise after changes that only make
  outputs longer or more confident, which the judge happens to prefer.
- A golden set that never grows from production. Symptom: the same forty items for a year while the
  input distribution moves, and an eval that predicts nothing about user complaints.
- Scores stored without prompt and dataset versions. Symptom: nobody can say whether Tuesday's 83% and
  Friday's 80% were measured on the same thing.

## When to use
Apply it to every feature whose model output reaches users or triggers actions, and most strictly
before model swaps, prompt refactors and retrieval changes. Skip it only for a one-day spike. Measure
the eval's coverage of known failure modes, the time from "bug reported" to "failing eval item
added", CI eval runtime and cost, and the correlation between the offline score and the online metric
it is supposed to predict. Day to day it looks like a pull request whose description is an eval diff.
In interviews, "how do you know your prompt change is an improvement?" should be answered with a
baseline run, *k* samples per item, per-slice scores, a list of regressed items you have read, a
held-out check, and a calibrated judge; "it looked better" is the wrong answer.

## Role lens
- **Engineer:** Writes the graders and the CI gate, adds a failing eval item before fixing any reported
  bug, and attaches the eval diff to every prompt or model pull request.
- **Architect:** Sets the gate pyramid (what runs per commit, per PR, per release), the thresholds per
  risk tier, and the rule that nothing ships without a comparable eval run.
- **FDE:** Has the customer's experts author acceptance criteria and golden items before the build
  starts, so the eval becomes the shared definition of done in the statement of work.

## Check yourself
**Q:** Your vendor is deprecating the model behind your feature in six weeks; the replacement is
"better on benchmarks". Walk through how eval-driven development handles the migration, and what would
make you refuse to switch.
**A:** First make sure the suite is current: run error analysis on recent production traces and add
items for any new failure modes and slices. Re-run the current model with *k* samples per item to pin a
fresh per-slice baseline, since the old one may have drifted. Run the candidate under identical
configuration: same prompts, retrieval, graders and dataset version. Compare per item, not just the
mean; list regressions and improvements and read the regressed traces. Check that the judge still
agrees with humans on the new model's outputs, because a different style can fool a judge. Try prompt
adaptations as separate, recorded experiments. Canary in production and compare online metrics with
the offline prediction. Refuse to switch if a high-severity slice regresses with no fix, if cost,
latency or benign refusals exceed budget, or if the judge cannot yet be trusted on the new outputs.

## Quiz
### Q: Why write acceptance criteria and golden items before building the first version?
- [ ] Because the model cannot be called until a dataset file exists
- [x] Because criteria written after seeing outputs anchor on the model's current behaviour instead of the user's need, so they pass by construction
- [ ] Because CI pipelines require a baseline file before the first run
- [ ] Because vendor benchmarks are only valid for pre-registered tasks
> Criteria written first describe the requirement; criteria written after describe the implementation,
> which is why post-hoc evals start at 100% and catch nothing. The ordering, not the tooling, is what
> makes the eval a specification.

### Q: A CI eval gate uses a fixed 95% pass threshold and fails roughly every third run with no code change. What is the best fix?
- [ ] Lower the threshold to 80% so it passes reliably
- [ ] Set temperature to zero and keep the hard threshold
- [x] Gate on a significant drop against a baseline run using several samples per item and a tolerance band, and list per-item regressions
- [ ] Re-run the job until it passes before merging
> The failures are sampling noise, so the gate must compare distributions, not assert a point value.
> Temperature zero reduces but does not remove variance, and lowering the bar or re-running trains the
> team to ignore the gate.

### Q: Why keep a held-out subset of eval items that you rarely inspect?
- [ ] To reduce CI cost by running fewer items per commit
- [x] Because repeatedly tuning prompts against the items you inspect overfits them, so only the held-out set estimates real generalisation
- [ ] Because LLM judges need unseen items to stay calibrated
- [ ] Because the vendor requires test-data isolation for compliance
> Iterating on a prompt against visible items is training on the test set; the dev-set score keeps
> rising while production does not. A held-out slice, rotated occasionally, is the only unbiased
> offline estimate of how the change will behave on inputs you have not tuned for.
