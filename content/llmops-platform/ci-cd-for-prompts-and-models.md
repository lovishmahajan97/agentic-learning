---
id: ci-cd-for-prompts-and-models
module: llmops-platform
name: CI/CD for prompts and models
kind: practice
difficulty: core
est_minutes: 16
related:
  - backend-fundamentals/ci-cd
  - llmops-platform/evaluation-gates-for-production-releases
  - llmops-platform/prompt-registry
  - llmops-platform/model-registry
  - llmops-platform/canary-shadow-deployments
  - evals/regression-tests
  - prompting/prompt-versioning
  - evals/eval-driven-development
sources:
  - Humble & Farley, 2010 — Continuous Delivery
  - Breck et al., 2017 — The ML Test Score: A Rubric for ML Production Readiness and Technical Debt Reduction
  - Sculley et al., 2015 — Hidden Technical Debt in Machine Learning Systems
---

## TL;DR
Classic CI/CD assumes behaviour is a deterministic function of code and ships one artifact. An LLM
application ships three — code, prompts and configuration, and model weights or model IDs — and any one
can change behaviour without touching the others. CI/CD for prompts and models adds eval-based tests
sized to each stage's blast radius, content-addressed caching so those evals stay affordable, and
promotion of one immutable, tested bundle instead of a rebuild, because a green unit suite says nothing
about whether answers got worse.

## Why it exists
A conventional pipeline rests on three assumptions: the behaviour of the system is determined by the
code, tests are deterministic, and the bits that passed are the bits that run in production. An LLM
application violates all three. Behaviour is a function of code, prompt text, the model behind an API
alias, decoding parameters and the retrieval index, and outputs are sampled rather than computed. The
model can also change underneath you: a provider updates the model behind `-latest`, a training job
produces a new adapter, an index is re-embedded.

Teams usually try one of three shortcuts first. Keeping prompts as string constants in the codebase
puts them under review but gives no evidence: a PR that fixes the one complaint in the ticket regresses
ten other cases, and the regression is discovered by users. Letting people edit prompts live in a
dashboard makes iteration fast but leaves no review, no rollback, and no record of which prompt produced
which answer. Running the entire eval suite on every commit is the correct instinct done badly: thousands
of model calls per push cost real money and take tens of minutes, so within a month the step is marked
optional and skipped.

The practice that works treats prompts and models as first-class release artifacts: the pipeline knows
which artifact changed, runs an eval of depth proportional to the risk of that stage, gates on aggregate
quality rather than exact strings, and promotes the exact bundle it tested.

## How it works
### Three artifacts, three cadences
Application code changes daily and is covered by deterministic tests. Prompts and configuration change
hourly to daily, are cheap to evaluate, and are often edited by product managers or forward-deployed
engineers. Models change weekly to quarterly, are expensive to evaluate, and have capacity and cost
consequences. A release is therefore defined as a *bundle*: `{code sha, prompt versions, model id or
weights revision, decoding params, index snapshot}`. The pipeline tests and promotes bundles, because
testing a prompt against one model and deploying it with another tests nothing.

### The test ladder
Depth grows with the blast radius of the stage, because cost and wall-clock time scale with model calls:

1. **Static and unit tests (seconds, no model calls).** Every template renders with every declared
   variable; output parsers run against recorded model outputs; tool JSON schemas validate; guardrail
   rules compile; the rendered prompt stays under its token budget.
2. **Contract tests with recorded responses.** Replay fixtures through the real code path to check
   plumbing (retries, streaming, parsing). They prove the wiring, not the quality.
3. **Smoke eval on every PR (minutes, a few dollars).** A stratified subset of the golden set, 50 to
   200 items as an illustrative size, scored with deterministic checks where possible and an LLM judge
   where necessary, compared against the baseline score recorded by the last green build of main.
4. **Full eval on merge, nightly and on release candidates.** The whole golden set plus slices, cost
   and latency, with every output stored as a build artifact so reviewers can diff behaviour.
5. **Deploy with a canary** and online metrics, and an automatic rollback trigger.

### Gating on distributions, not strings
Because outputs are sampled, exact-match assertions on free text flake. The pipeline compares score
distributions: a mean with a confidence interval against the champion, absolute floors for safety and
schema validity, and per-slice floors. The stored outputs are the review artifact; the diff between the
candidate's and the champion's answers on the same items is what a reviewer actually reads. The
`evaluation-gates-for-production-releases` lesson covers the statistics.

### Content-addressed caching
Eval cost is dominated by model calls, so every call is keyed by
`hash(model id, params, prompt version, input)` and cached. A PR that touches one prompt re-runs only the
items that use that prompt; a code-only PR often re-runs nothing and just re-scores. This is what makes a
per-PR eval affordable, and it is also why the cache key must include every pin: a key that omits the
model id will happily serve stale outputs after a model upgrade and hide the change.

### Promote, do not rebuild
The bundle that passed the release eval is promoted dev → staging → production by moving a pointer in
the prompt and model registries, never by rebuilding at deploy time, because a rebuild re-resolves
floating aliases (`-latest`, `main`) and can ship something that was never tested.

### Prompts as code versus prompts as config
With prompts in the repository, prompt changes ride the normal PR pipeline; simple, reviewable, but
non-engineers are blocked and prompt deploys are coupled to code deploys. With a prompt registry, the
registry is the source of truth, a label change is the deploy, and the pipeline is triggered by a
registry webhook to run the same eval gate before the `production` label moves. Most mature teams land
on the hybrid: registry as source of truth, pipeline as the only thing allowed to move production labels.

### Model changes are releases too
Pin the model snapshot in the bundle. Run a scheduled "model upgrade" job that opens a PR with the new
snapshot and the full eval when the provider announces a new version or a deprecation; treat a retrained
adapter arriving in the model registry the same way. The trigger differs; the ladder is identical.

## Example
A PR-time smoke eval that sits as one job in the pipeline. The YAML fragment shows where it runs; the
script shows the content-addressed cache and the comparison against the baseline written by the last
green build of `main`. `call_app` and `score` are your harness's entrypoint and scorer.

```yaml
# .github/workflows/llm-ci.yml (fragment): trigger on anything that changes the bundle
on:
  pull_request:
    paths: ["app/**", "prompts/**", "release.json"]
jobs:
  smoke-eval:
    steps:
      - run: python scripts/smoke_eval.py --subset smoke   # nightly job runs --subset full
```

```python
# scripts/smoke_eval.py
import hashlib, json, pathlib, sys

bundle = json.load(open("release.json"))   # {"model": "vendor-model-2025-04-14", "prompt": "support_answer@v12", "params": {...}}
subset = sys.argv[sys.argv.index("--subset") + 1]
items = [json.loads(l) for l in open(f"data/golden/{subset}.jsonl")]
cache = pathlib.Path(".evalcache"); cache.mkdir(exist_ok=True)

def key(item):   # every pin is in the key, so an unchanged (model, prompt, params, input) never re-calls the model
    blob = json.dumps([bundle["model"], bundle["prompt"], bundle["params"], item["input"]], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()

def run_item(item):
    p = cache / key(item)
    if p.exists():
        return json.loads(p.read_text())
    out = call_app(bundle, item["input"])                 # the production code path, not a copy of it
    rec = {"id": item["id"], "output": out, "score": score(item, out)}
    p.write_text(json.dumps(rec)); return rec

results = [run_item(i) for i in items]
mean = sum(r["score"] for r in results) / len(results)
baseline = json.load(open("eval_baselines/main.json"))["mean"]   # written by the last green build of main
json.dump({"bundle": bundle, "mean": mean, "items": results}, open("eval_out.json", "w"))  # stored as a build artifact
print(f"candidate={mean:.3f} baseline={baseline:.3f} n={len(results)}")
sys.exit(0 if mean >= baseline - 0.02 else 1)   # smoke tolerance; the release gate uses a paired CI instead
```

## Trade-offs
- Per-PR smoke evals catch gross regressions in minutes, but a 100-item subset cannot resolve a one- or
  two-point change; the moment you need that resolution you must pay for the full set or a canary.
- Caching makes evals cheap and fast, yet every omitted pin in the cache key becomes a way to hide a real
  change; the more aggressive the cache, the more disciplined the key must be.
- Prompts-as-config decouples prompt deploys from code deploys and unblocks non-engineers, at the cost of
  a second deploy path that must be gated as strictly as the first or it becomes the bypass everyone uses.
- Gating on aggregate scores tolerates sampling noise, but it also tolerates an individual catastrophic
  answer; safety and schema checks therefore need absolute per-item floors, not averages.
- Pinning model snapshots gives reproducible builds, but snapshots get deprecated; without a scheduled
  upgrade job, pinning turns into an emergency migration on the provider's timetable.

## Pitfalls
- Running evals against a copy of the prompt chain inside the test harness instead of the production
  entrypoint; the symptom is a green pipeline and a production regression caused by code the eval never
  executed.
- Comparing the candidate against a fixed threshold rather than the current champion; quality erodes a
  point per release while every release passes, and nobody notices until a customer does.
- Retrying a flaky eval until it goes green; the symptom is a gate whose pass rate is uncorrelated with
  quality and a team that has stopped reading the numbers.
- Letting the `production` label in the registry be moved from the UI without the pipeline; the symptom
  is an incident whose timeline contains a prompt change with no PR, no eval and no author.
- Using `-latest` model aliases in the bundle; the symptom is a score change on a day with no commits,
  traced eventually to a provider update.

## When to use
Introduce this the moment more than one person edits prompts or a prompt is deployed to real users:
that is when "we changed something and it got worse" becomes unanswerable. Start with the cheapest
rungs, unit tests on templates and parsers plus a 50-item smoke eval against a stored baseline, and add
the full suite and canary when release frequency or user impact justifies them. Measure pipeline cost
per PR, eval wall-clock time, cache hit rate and, most importantly, the number of production regressions
that the pipeline would have caught. In interviews the expected answer to "how do you deploy a prompt
change safely" is this ladder: unit, recorded-response contract, smoke eval versus baseline, full eval
with gates, canary with rollback, with the bundle, not the prompt, as the unit of release.

## Role lens
- **Engineer:** Builds the smoke-eval job, the content-addressed cache and the baseline file, and keeps the
  harness calling the real application entrypoint rather than a reimplementation.
- **Architect:** Decides that the bundle is the unit of release, which registry owns each artifact, and that
  the pipeline is the only path allowed to move a production label.
- **FDE:** Reproduces a lightweight version of the ladder inside the customer's CI, often with the
  customer's own golden set, so their change-management process has evidence rather than a demo.

## Check yourself
**Q:** A product manager edits a system prompt in the registry UI at 4 pm and moves the `production`
label; latency is unchanged but support tickets about wrong refund amounts double overnight. The code
pipeline is green. What went wrong structurally, and how would you change the pipeline so it cannot
happen again?
**A:** Structurally, the prompt had a deploy path that bypassed every test: the code pipeline only runs on
commits, and the registry label move was an unreviewed, un-evaluated production change. The fix is not
to forbid non-engineers from editing prompts but to make the label move itself the thing the pipeline
gates. Registry edits create a new immutable version; a webhook or scheduled job runs the smoke eval and,
for `production`, the full eval and gates against the champion on the same golden set, including a
refund-intent slice with a floor; only the pipeline's service account may move the production label,
and it writes the eval evidence next to the version. Rollback is moving the label back. Finally, add the
incident's cases to the golden set so the regression is a permanent test.

## Quiz
### Q: Why does the PR stage run a stratified subset of the golden set rather than the full suite?
- [ ] Subsets are statistically more reliable than the full set
- [x] Eval cost and time scale with model calls, so depth is matched to the blast radius of each stage
- [ ] Full suites can only be run against production models
- [ ] GitHub Actions cannot run jobs longer than ten minutes
> Every item is a model call that costs money and time; a per-PR job must stay fast and cheap or it will be
> skipped. The full suite runs on merge, nightly and on release candidates where the stakes justify it.

### Q: Why should the tested bundle be promoted by pointer rather than rebuilt at deploy time?
- [ ] Rebuilding is slower because weights must be downloaded again
- [ ] Registries cannot store more than one version at a time
- [x] A rebuild re-resolves floating aliases and can deploy a model or prompt that was never evaluated
- [ ] Promotion avoids the need for a canary stage
> The whole point of the gate is that the exact bits that passed are the bits that run. A rebuild that
> resolves `-latest` or `main` again may pick up a provider update or a prompt edit the eval never saw.

### Q: What is the strongest reason to put the model snapshot id inside the eval cache key?
- [ ] It makes the cache directory easier to browse
- [ ] Providers require it for billing attribution
- [x] Without it, a model upgrade would be served stale cached outputs and the change would be invisible
- [ ] Hashing is cheaper when the key is longer
> The cache exists to skip calls whose inputs have not changed. If the model id is not part of the input
> definition, swapping the model counts as "unchanged" and the eval silently measures the old model.
