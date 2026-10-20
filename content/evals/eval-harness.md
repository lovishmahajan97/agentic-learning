---
id: eval-harness
module: evals
name: Eval harness
kind: pattern
difficulty: core
est_minutes: 15
related:
  - evals/golden-dataset
  - evals/llm-as-a-judge
  - evals/regression-tests
  - evals/pass-k
  - evals/tooling
  - llmops-platform/reproducible-evaluations
  - llmops-platform/ci-cd-for-prompts-and-models
  - backend-fundamentals/ci-cd
sources:
  - Official docs: EleutherAI lm-evaluation-harness
  - Official docs: Inspect (UK AI Security Institute)
---

## TL;DR
An eval harness is the code that takes a versioned dataset, runs every item through the system under
test with pinned configuration, grades the raw outputs, aggregates scores per slice with confidence
intervals, and writes a report that can be compared with any previous run. It exists because
evaluation done in notebooks is not repeatable, so Tuesday's number cannot be compared with Friday's;
the harness is what turns evaluation into an engineering artifact with the status of a test suite.

## Why it exists
The first eval most teams run is a for-loop in a notebook: iterate over a CSV, call the model, eyeball
the outputs, maybe count passes. It works once. The second time, the prompt has changed, the
temperature was different, half the calls hit a rate limit and were silently skipped, the raw outputs
were overwritten, and nobody can say whether the new 83% is better than the old 80% or just a
different 80%. The problem is not the loop; it is everything the loop does not record.

Three specific failures force the harness into existence. *Non-reproducibility*: without pinned model
id, prompt hash, sampling parameters, dataset version and grader version, a score cannot be attributed
to a change. *Lost evidence*: if only the aggregate is kept, you cannot re-grade when the rubric
improves, cannot inspect the twelve items that regressed, and cannot show a stakeholder a concrete
failure. *Operational fragility*: hundreds of LLM calls hit rate limits, timeouts and transient errors;
a loop that drops them biases the score toward whichever items happened to succeed.

The naive alternative, "just be careful", does not survive a team. Two engineers running the same
notebook will get numbers that differ for reasons neither can explain. A harness makes carefulness
structural: the same inputs, the same configuration and the same graders, every time, with everything
written down.

## How it works
### The components
```
dataset (versioned) → target adapter → runner → raw outputs (stored) → graders → scores → aggregator → report + gate
```

- **Dataset loader.** Reads a specific version of the golden dataset and records its hash, *because*
  a changed dataset changes the metric.
- **Target adapter.** Wraps the system under test behind one function, `run(item) -> output`. The
  harness does not know whether that is a single prompt, a RAG chain or a multi-step agent, *because*
  the harness should outlive any one architecture.
- **Runner.** Executes items concurrently with retries, backoff and timeouts; caches results keyed by
  (configuration hash, item id) so a re-run after a grader change costs nothing; samples each item
  *k* times when temperature is above zero, *because* a single sample of a stochastic system is a coin
  flip, not a measurement.
- **Raw output store.** Every output is written, with the request, latency, token counts and cost,
  before any grading happens. Running is expensive; grading is cheap and changes often, so the two are
  separated.
- **Graders.** Pluggable: deterministic checks (schema, exact, regex, unit tests), LLM judges with their
  own pinned model and prompt version, and a queue for human review. Grader version is recorded with the
  score, *because* a grader change can move a score without any change to the system.
- **Aggregator.** Pass rate per slice with a confidence interval, plus latency percentiles and cost
  per item. Quality is one of three axes; a harness that reports only accuracy hides the trade-off.
- **Reporter and gate.** A run manifest (who, when, every pinned value) and a comparison against a
  baseline run: which items flipped, in which direction, in which slice. A gate turns thresholds into
  an exit code so CI can block a merge.

### Design choices and their reasons
*Pin everything in a manifest.* Model id, prompt text hash, temperature, seed, max tokens, retrieval
index version, dataset version, grader versions, git SHA. A diff between two manifests should explain
any diff between two scores.

*Compare paired, not averaged.* Because both runs use the same items, the harness can list flipped
items; a net +15 of 300 with 27 gains and 12 losses is far more informative than "78% → 83%", and the
12 losses are where the next bug lives.

*Fail loudly on dropped items.* An item that errored is reported as an error, not excluded, and the
summary says how many; a quiet 5% drop rate is a biased score.

*Make it cheap to run a subset.* A PR run on 50 items in minutes, a nightly run on everything, a
release run with *k* = 5 samples per item. The same code path, different selectors.

### Capability harnesses versus application harnesses
Frameworks such as EleutherAI's lm-evaluation-harness and Inspect standardize prompting and scoring
for public benchmarks, so that an MMLU number from one lab is comparable with another's. Application
harnesses (hosted products, open-source libraries or a few hundred lines you own) point at your
dataset and your graders. The structure is identical; the difference is who owns the dataset and the
grading rules. The tooling lesson in this module compares the options.

### Integration with the delivery pipeline
The harness runs in CI like tests: a fast subset on every pull request that touches a prompt, chain or
retrieval config, a full run nightly or before a release, with results stored so trends are visible.
Thresholds come from task-specific acceptance criteria. Flaky items (those that flip between samples
with no change) are tracked separately rather than letting them block merges, and their count is
itself a quality signal.

## Example
A minimal harness in one file: a run manifest, concurrent execution with retries, stored raw outputs,
pluggable graders, and a per-slice summary with a normal-approximation confidence interval. `target`
is any callable, so the same code evaluates a prompt or an agent.

```python
import hashlib, json, math, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

GRADERS = {
    "exact": lambda out, item: out.strip() == item["expected"].strip(),
    "contains_all": lambda out, item: all(s in out for s in item["expected"]),
}

def run_eval(items, target, config: dict, out_path: str, workers=8, retries=3):
    manifest = {**config, "dataset_sha": hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()[:12],
                "prompt_sha": hashlib.sha256(config["prompt"].encode()).hexdigest()[:12], "started": time.time()}

    def one(item):
        for attempt in range(retries):
            try:
                t0 = time.time(); out = target(item, config)
                ok = GRADERS[item["grader"]](out, item)
                return {"id": item["id"], "slice": item["slice"], "output": out, "pass": ok,
                        "latency_s": round(time.time() - t0, 3), "error": None}
            except Exception as e:                      # rate limit, timeout, parse error
                time.sleep(2 ** attempt); last = repr(e)
        return {"id": item["id"], "slice": item["slice"], "output": None, "pass": None, "error": last}

    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(one, items))
    with open(out_path, "w") as f:                      # raw outputs first; grading can be redone later
        f.write(json.dumps({"manifest": manifest}) + "\n")
        for r in results: f.write(json.dumps(r) + "\n")
    return summarize(results)

def summarize(results):
    by_slice = defaultdict(list)
    for r in results: by_slice[r["slice"]].append(r)
    report = {}
    for s, rs in sorted(by_slice.items()):
        graded = [r for r in rs if r["pass"] is not None]
        n, p = len(graded), sum(r["pass"] for r in graded) / max(1, len(graded))
        ci = 1.96 * math.sqrt(p * (1 - p) / n) if n else float("nan")
        report[s] = {"n": n, "pass": round(p, 3), "ci95": round(ci, 3), "errors": len(rs) - n}
    return report
# e.g. {'billing': {'n': 60, 'pass': 0.817, 'ci95': 0.098, 'errors': 0}, ...}
```

The report line for `billing` says what the run can decide: ±10 points at n = 60, so a 5-point change
in that slice needs the paired flipped-item view or more items, not a victory lap.

## Trade-offs
- Storing every raw output makes re-grading free and failures inspectable, but the store grows by
  items × samples × runs and may contain customer data that needs retention rules.
- High concurrency makes a 500-item run take minutes instead of an hour, but it can trip provider rate
  limits and distort the latency numbers you are also trying to measure; use a separate latency run.
- Caching by configuration hash saves money on repeated runs, but a cache that ignores any pinned value
  (a changed retrieval index, a new model snapshot behind the same alias) silently returns stale outputs.
- A hosted eval product gives dashboards, trend history and human-review queues out of the box, but it
  owns your dataset format and graders; a few hundred lines you own are portable but need maintenance.

## Pitfalls
- Dropping errored items from the denominator; the symptom is a score that improves on the day the
  provider has an outage, because the hardest, longest items are the ones that timed out.
- Changing the grader and the system in the same run; the symptom is a score movement nobody can
  attribute, visible as two manifest fields differing at once.
- Running one sample per item at temperature 0.7; the symptom is a score that moves 3 to 5 points
  between identical runs, which the team reads as regressions and improvements.
- Grading inside the run loop and never storing outputs; the symptom is a rubric fix that forces a
  full, expensive re-run just to see new scores.
- Reporting the overall pass rate without intervals or slices; the symptom is a merged prompt change
  that lifted the average by lowering the one slice a key customer uses.
- Letting the harness read the model alias (`latest`) instead of a pinned snapshot; the symptom is a
  score change with an empty diff.

## When to use
Build a harness the moment a prompt or chain will be evaluated more than once; that is the second eval
run, so build it before the first. Use the fast subset on every change that touches model-facing code,
the full set before releases, and multi-sample runs when the decision is close. Measure pass rate per
slice with intervals, error rate, latency percentiles and cost per item in the same report. If you
adopt a framework, keep the target adapter and dataset format yours so you can leave it. In interviews,
the expected depth is describing the components, explaining why raw outputs are stored before grading,
why runs are paired, and how the harness plugs into CI with a gate.

## Role lens
- **Engineer:** Owns the harness code, the target adapter and the grader registry, and wires the subset
  run into the pull-request pipeline with a threshold gate.
- **Architect:** Sets the standard: what every run manifest must pin, where raw outputs live and for how
  long, and which runs block a release versus merely report.
- **FDE:** Stands up a harness inside the customer's environment against their data and credentials,
  so eval numbers can be reproduced by their team after the engagement ends.

## Check yourself
**Q:** A colleague reports that the summarizer's eval score rose from 80% to 86% after their prompt
change, from a notebook run. Which questions do you ask before believing it, and what would a harness
have answered already?
**A:** Were both runs on the same dataset version and the same items? Same model snapshot, temperature
and max tokens, or did one run use a new model alias? How many items errored and were they excluded?
One sample per item or several, and at what temperature? Did the grader change at the same time?
Which items flipped, and does any slice regress? A harness answers all of these from two manifests and
a paired diff: it pins the dataset hash, model id, prompt hash and grader version, reports error counts
instead of silently dropping items, stores raw outputs so the flipped items can be read, and gives a
per-slice interval that says whether six points at that n is signal at all.

## Quiz
### Q: Why does a well-designed harness store raw outputs before any grading happens?
- [ ] Graders require outputs on disk to run
- [x] Running is expensive and grading is cheap and changes often, so outputs must survive grader changes
- [ ] Storing outputs is required by most providers' terms of service
- [ ] It lets the harness skip items that already passed
> Separating the two means a rubric fix or a new grader can be applied to existing outputs for free,
> and failures can be inspected. Skipping passed items would bias future runs, which is the opposite of
> the goal.

### Q: During a run, 6% of items fail with timeouts. What should the harness do with them?
- [ ] Exclude them from the denominator so the score reflects completed items
- [ ] Mark them as failed, since a timeout is a bad user experience
- [x] Report them as errors with their count, separate from the pass rate, and retry them
- [ ] Replace them with cached outputs from the previous run
> Excluding errored items biases the score, because timeouts cluster on the hardest items; marking them
> failed mixes an infrastructure problem into a quality metric. Reporting and retrying keeps both
> signals honest.

### Q: Two runs differ only in that one used the model alias "latest" and the other a dated snapshot. The scores differ by four points. What is the harness design flaw?
- [ ] The dataset was too small to detect four points
- [x] The manifest did not pin the model snapshot, so a provider-side change is indistinguishable from a code change
- [ ] The grader should have been an LLM judge
- [ ] Concurrency was too high and caused rate limiting
> A manifest must pin every value that can move the score. An alias resolves to different weights over
> time, so an empty code diff produced a score diff that cannot be attributed; pinning the snapshot
> makes the cause visible.
