---
id: reproducible-evaluations
module: llmops-platform
name: Reproducible evaluations
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - llmops-platform/experiment-tracking
  - llmops-platform/evaluation-frameworks
  - llmops-platform/evaluation-gates-for-production-releases
  - evals/eval-harness
  - llmops-platform/model-registry
  - llmops-platform/prompt-registry
  - evals/judge-calibration-and-bias
  - python-for-ai/lockfiles-and-dependency-pinning
sources:
  - Pineau et al., 2021 — Improving Reproducibility in Machine Learning Research
  - Chen, Zaharia & Zou, 2023 — How Is ChatGPT's Behavior Changing over Time?
  - Sculley et al., 2015 — Hidden Technical Debt in Machine Learning Systems
---

## TL;DR
A reproducible evaluation is one anyone can re-run from a recorded manifest and reach the same
conclusion: same dataset version, same bundle (model snapshot, prompt version, decoding parameters,
index), same scorer versions (judge model and rubric), same code and environment, with raw outputs stored
and residual sampling noise measured rather than assumed away. It exists because an LLM eval has many
silently drifting parts, so an unpinned comparison across weeks compares two different systems and blames
the wrong cause.

## Why it exists
"Did the prompt change help?" is only answerable if everything else held still, and in an LLM system
almost nothing does by default. The provider updates the model behind an alias such as `-latest`, and
published studies have documented large behaviour shifts between dated versions of the same hosted model.
Sampling at temperature above zero produces different outputs on every run, and even at temperature zero
outputs are generally not bit-identical across calls, because batching and floating-point reduction order
on GPUs are not deterministic. The judge used to score free text is itself a model and drifts for the same
reasons. The dataset gets edited in place. The retrieval index is re-embedded. The prompt contains today's
date or a live tool result. A dependency bump changes a tokenizer or an SDK default. A warm cache serves
last month's outputs for a model that has since changed.

The naive answers each cover one source. "Set temperature to 0 and a seed" addresses sampling only, and
many APIs treat the seed as best-effort. "Compare with last week's number" compares two systems. "Run it
twice and see" is the right instinct without the bookkeeping to act on it. The cost of getting this wrong
is not academic: a team ships a prompt that "improved three points" when the provider changed the model
underneath, or rolls back a genuine improvement because the judge was upgraded the same day. Regulated
customers add a second reason: they ask for the evidence behind a release, and "we ran it and it looked
fine" is not evidence. Reproducibility is the discipline of pinning what can be pinned, measuring the
noise in what cannot, and recording both.

## How it works
### Three levels, and which one you actually need
*Bit-exact* reproduction (identical outputs) is achievable with locally served open-weight models at
temperature zero with fixed batching and deterministic kernels (at some throughput cost), and rarely
through a hosted API. *Statistical* reproduction (the
same score distribution within a measured noise band) is the practical target for every decision eval.
*Re-derivable* (anyone can reconstruct the run from its manifest, even if the provider has since changed)
is the minimum bar for any eval that is cited in a release.

### The eval lockfile
Every run records a manifest, and each field is there *because* a specific drift has burned someone:
- **Dataset**: the content hash of the items, not a file name, because files get edited in place.
- **Bundle**: model snapshot id (or weights digest), prompt versions, decoding parameters, seed where
  supported, tool and retrieval index snapshots. A dated snapshot rather than an alias, because the alias
  is the single most common cause of unexplained score changes.
- **Scorers**: judge model snapshot, judge prompt and rubric version, scorer code version; the judge is
  pinned because it is a model.
- **Environment**: git SHA, dependency lockfile hash, container image digest; because tokenizers, SDK
  defaults and retry behaviour live in dependencies.
- **Run policy**: concurrency, timeout and retry settings, cache mode; because timeouts recorded as zero
  scores make a provider incident look like a regression, and a cache that ignores a pin hides a change.
The run id is a hash of the manifest, so two runs with identical pins are recognisably the same
experiment, and the manifest is stored next to the raw outputs in the experiment-tracking or evaluation
store.

### Measure the noise you cannot pin
Run the *same* bundle several times (three to five repeats is a common illustrative choice) and compute
the spread of the aggregate; that spread is the noise floor, and no delta smaller than it may be believed.
Report every metric as a mean with a confidence interval, compare candidate against champion on
identical items so item difficulty cancels, and evaluate at the temperature production uses rather than
forcing zero, *because* an eval at temperature zero of a system that ships at 0.7 measures a different
system; repeats, not determinism, are how you tame the variance. Deterministic scorers on structured
outputs from a locally pinned model can show a noise floor near zero, at which point one run suffices;
measure it before assuming it.

### Detect drift you cannot pin
Even a dated snapshot is a promise you cannot verify from outside. A *sentinel run* re-executes a frozen
reference bundle on a fixed subset on a schedule; if its score moves beyond the noise floor while
nothing in the manifest changed, something outside the manifest did (the provider, the network, the
environment), and the alert fires before a candidate is blamed. The same sentinel catches a silently
updated judge.

### Comparability as a rule, not a hope
Two runs are comparable only if their dataset version and scorer versions match; the tooling should refuse
to diff them otherwise, or label the comparison as cross-version. Re-scoring is cheap when raw outputs are
stored: a new judge version re-scores champion and candidate from stored outputs without re-calling the
target, which is how you re-baseline after a judge upgrade.

## Example
A manifest builder, a noise-floor measurement, and a comparability check. The run id is derived from the
manifest, so re-running with the same pins produces the same id; a changed pin produces a new one. The
noise floor is measured on the champion before any candidate is judged against it.

```python
import hashlib, json, statistics, subprocess, pathlib

def sha(path: str) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()[:12]

def build_manifest(bundle: dict, dataset_version: str, scorers: dict, image_digest: str) -> tuple[str, dict]:
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    m = {"dataset": dataset_version,                       # content hash of the items, never a file name
         "bundle": bundle,                                  # {"model": "vendor-model-2025-04-14", "prompt": "support@v12",
                                                            #  "params": {"temperature": 0.7, "max_tokens": 400}, "index": "idx-2025-06-01"}
         "scorers": scorers,                                # {"judge_model": "judge-2025-01-15", "rubric": "helpfulness@v3"}
         "env": {"git": git_sha, "lock": sha("uv.lock"), "image": image_digest},
         "policy": {"concurrency": 8, "timeout_s": 60, "retries": 2, "cache": "read-write"}}
    run_id = hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()[:12]
    return run_id, m

def noise_floor(run_once, k: int = 3) -> tuple[float, float]:
    """run_once() executes the SAME manifest with cache disabled and returns per-item scores.
    The spread between repeats is the smallest delta anyone is allowed to believe."""
    means = [statistics.mean(run_once()) for _ in range(k)]
    return statistics.mean(means), max(means) - min(means)

def comparable(a: dict, b: dict) -> tuple[bool, dict]:
    checks = {k: a[k] == b[k] for k in ("dataset", "scorers")}
    checks["same_model"] = a["bundle"]["model"] == b["bundle"]["model"]   # false is fine for a model-upgrade eval, but say so
    return all(checks[k] for k in ("dataset", "scorers")), checks

# champion_mean, floor = noise_floor(lambda: run_eval(champion_manifest, cache=False))
# delta = candidate_mean - champion_mean
# if abs(delta) <= floor: print("within noise: not a result")  # the gate lesson turns this into a CI test
```

## Trade-offs
- Pinning dated snapshots gives stable comparisons, but every deprecation forces a re-baseline; when the
  provider deprecates faster than you re-baseline, a scheduled model-upgrade job with a full eval is the
  only sustainable answer.
- Repeated runs quantify noise at a multiple of the cost; once a deterministic scorer on a locally pinned
  model shows a near-zero floor, a single run is enough, but only after the floor has been measured.
- Evaluating at production temperature measures what ships but is noisier; evaluating at zero is cleaner
  and may measure a system you do not ship, so the choice follows what production actually does.
- Content-addressed caching makes re-runs nearly free, yet a key that omits any pin hides a change; the
  sentinel run must bypass the cache, which is exactly the run you cannot make cheap.

## Pitfalls
- A model alias instead of a dated snapshot in the manifest; the symptom is scores jumping on a day when
  nobody deployed anything.
- A silently upgraded judge; the symptom is champion and candidate shifting together, which preserves the
  delta but breaks every absolute floor in the gate.
- Datasets referenced by file name; the symptom is two runs "on golden.jsonl" disagreeing on the item count.
- Timeouts counted as zero scores; the symptom is eval scores that correlate with the provider's status page.
- Assuming temperature zero means deterministic; the symptom is a bug report that some outputs differ
  between identical runs (how many depends on the provider, output length and load), filed as a regression.
- Comparing runs scored by different judge versions; the symptom is a "regression" that vanishes when both
  runs are re-scored from stored outputs with the same judge.

## When to use
Every eval that informs a decision gets a manifest; the rigour scales with the stakes. In the development
loop, re-derivable is enough and the framework captures the manifest automatically. At a release gate,
statistical reproduction is required: repeats to establish the noise floor, identical dataset and scorer
versions, paired comparison. For regulated or customer-facing commitments, store the full evidence
(manifest, raw outputs, scores, approvals) with the promoted bundle. Measure the practice itself: the
fraction of runs with a complete manifest, the noise floor per metric, sentinel drift events per quarter,
and how often the comparability check refuses a diff. In interviews, the question is usually "how would
you know the model changed underneath you?"; the sentinel run is the answer.

## Role lens
- **Engineer:** Makes the harness refuse to start without a complete manifest, pins judge and model
  snapshots, stores raw outputs, and schedules the sentinel run with caching disabled.
- **Architect:** Sets the reproducibility level each stage requires, decides what evidence is retained for
  how long, and owns the policy that aliases never appear in a promoted bundle.
- **FDE:** Hands a customer the manifest and raw outputs behind an acceptance number, so their team can
  re-run it in their own environment and get the same conclusion.

## Check yourself
**Q:** A colleague reports that their prompt change raised the judge score from 0.78 to 0.83 on Friday,
against a baseline measured three weeks ago. What do you ask before believing it, and what do you run?
**A:** Ask whether both runs used the same dataset version, the same model snapshot rather than an alias,
the same judge model and rubric version, the same decoding parameters and index, and whether the baseline's
noise floor was ever measured. Three weeks is long enough for the provider, the judge or the dataset to
have moved, so the 0.05 may belong to any of them. Then run champion and candidate side by side now, with
manifests identical except for the prompt version, cache disabled, three repeats each, paired on identical
items, and report the mean difference with a confidence interval. If the interval excludes zero and the
delta exceeds the measured floor, treat the improvement as real (and confirm it on the slices that matter);
otherwise it is not yet a result.

## Quiz
### Q: Why must the judge model's snapshot be part of the eval manifest?
- [ ] Because judge calls are the most expensive part of the run
- [x] Because the judge is a model whose drift changes scores without any change to the application
- [ ] Because judges only work at temperature zero
- [ ] Because the cache key is derived from the judge's output
> A model-graded scorer is subject to the same provider updates as the target; if it moves, every score
> moves. Pinning it, and re-scoring stored outputs when it changes, keeps comparisons honest. Cost and
> cache keys are unrelated to the reason.

### Q: Setting temperature to zero guarantees that two identical eval runs produce identical outputs. True or false, and why?
- [ ] True, because greedy decoding always selects the same token
- [ ] True, as long as the seed parameter is also set
- [x] False, because batching and non-deterministic GPU arithmetic can change logits, so the noise floor must be measured
- [ ] False, because the tokenizer is randomised per request
> Greedy decoding is deterministic given identical logits, but logits can differ slightly across runs on
> hosted infrastructure, and seeds are often best-effort. The practical answer is repeats and a measured
> noise floor, not an assumption of determinism.

### Q: What does a scheduled sentinel run of a frozen reference bundle detect?
- [ ] Regressions introduced by a candidate prompt
- [ ] Contamination of the golden set
- [x] Drift outside the manifest, such as a provider changing the model behind a dated snapshot or an environment change
- [ ] Whether the cache hit rate is high enough
> Nothing in the sentinel's manifest changes between runs, so any movement beyond the noise floor must
> come from something the manifest cannot pin. Candidate regressions are caught by the gate, not the
> sentinel.
