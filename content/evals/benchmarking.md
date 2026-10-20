---
id: benchmarking
module: evals
name: Benchmarking (MMLU, HumanEval, SWE-bench, GPQA)
kind: concept
difficulty: core
est_minutes: 18
status: reviewed
related:
  - evals/model-evaluation
  - evals/dataset-contamination
  - evals/pass-k
  - evals/golden-dataset
  - system-design/model-selection
  - model-internals/reasoning-models-test-time-compute
  - agent-reliability/task-completion-rate
sources:
  - Hendrycks et al., 2020 — Measuring Massive Multitask Language Understanding
  - Chen et al., 2021 — Evaluating Large Language Models Trained on Code
  - Jimenez et al., 2023 — SWE-bench: Can Language Models Resolve Real-World GitHub Issues?
  - Rein et al., 2023 — GPQA: A Graduate-Level Google-Proof Q&A Benchmark
---

## TL;DR
A benchmark is a fixed dataset with a fixed scoring rule, so models can be compared on the same
task. MMLU measures broad knowledge by multiple choice, HumanEval measures Python function synthesis
by unit tests, SWE-bench measures resolving real GitHub issues, and GPQA measures expert science
reasoning that web search cannot shortcut. Benchmarks exist so model comparison is not anecdote, but
each measures something narrow, saturates and leaks into training data: treat a leaderboard as a
screening filter, never as evidence your task will work.

## Why it exists
Before shared benchmarks, every lab reported results on tasks of its own choosing, with its own
prompts and scoring, and no two numbers were comparable. A benchmark freezes three things, the items,
the prompt format and the scoring rule, so that a score from one lab means the same as a score from
another. That is what makes model selection, progress tracking and claim verification possible at all.

Why are there so many? Because each benchmark targets one capability, and each has a lifetime. A
benchmark is informative while models score well below its ceiling and have not seen its items. As
models improve it saturates (differences at the top are within noise or within label error), and as
its items spread across the web it becomes contaminated (scores reflect memorization). Newer, harder,
better-guarded benchmarks replace older ones: GPQA and SWE-bench (both 2023) took over from MMLU
(2020) and HumanEval (2021) as the numbers people watch, and they in turn are approaching their own
ceilings at the frontier as harder successors appear. Which benchmarks are "current" changes yearly;
the cycle is the stable fact.

The naive alternative is picking a model from the table in a vendor's announcement. It fails because
numbers across reports are rarely comparable (different shot counts, chain-of-thought or not, pass@1
versus pass@k, subsets, sampling settings, agent scaffolds), because contamination inflates some models
more than others, and because a benchmark measures the model alone, not your prompt, retrieval and
tools around it.

## How it works
### Anatomy of a benchmark
Items, a prompt format, a scoring rule, and a reporting convention. The scoring rule is the part
engineers underestimate: a multiple-choice benchmark can be scored by comparing the log-probability of
the four answer letters, or by generating text and parsing a letter out of it, and the two methods give
different numbers for the same model. Always read how a score was produced before comparing it.

### MMLU: breadth of knowledge
Multiple-choice questions, four options each, across 57 subjects from elementary mathematics to
professional law and medicine, roughly 14,000 test items in total; the original protocol is 5-shot.
Scored by accuracy, chance is 25%. It measures recall of world knowledge plus reading comprehension,
not multi-step reasoning. Its weaknesses are now well known: top models are close to its ceiling, later
audits found a non-trivial number of mislabeled or ill-posed questions in some subjects, scores are
sensitive to the answer-extraction method, and its items are everywhere on the web. Successors such as
MMLU-Pro raise the option count and the reasoning load to restore headroom.

### HumanEval: functional correctness of code
164 hand-written Python problems, each a function signature with a docstring; the model completes the
body and hidden unit tests decide pass or fail. The metric is pass@k: the probability that at least one
of *k* samples passes, estimated without bias from *n* ≥ *k* samples by
`pass@k = 1 − C(n−c, k) / C(n, k)` where *c* samples passed. Read the formula as counting: of all
ways to choose *k* of the *n* samples, C(n−c, k) contain only failures, so the fraction of size-*k*
subsets with at least one pass is the estimate. The obvious plug-in, 1 − (1 − c/n)^k, is biased low,
*because* that function is concave in the pass probability, so averaging it over noisy per-problem
estimates understates the true value; drawing many samples and counting subsets avoids this. pass@k
also rewards diversity: a model that solves a problem 1 time in 10 scores 0.1 at pass@1 but 0.5 at
pass@5 on that problem, which is why pass@k suits settings with a verifier that can pick the
passing sample and misleads when a user gets one shot. Its design point was to grade code by
*executing* it rather than by text similarity to a reference, which is why it displaced BLEU-style
code metrics. Weaknesses: tiny (one problem is 0.6 points), Python only, saturated at the top, heavily
contaminated because solutions are all over GitHub, and its original tests are sparse enough that
extended test suites (HumanEval+ from the EvalPlus project) catch solutions the originals passed.

### SWE-bench: resolving real issues
Task instances built from real GitHub issues in popular Python repositories: the model receives the
issue text and the repository at the pre-fix commit and must produce a patch. Grading applies the patch
and runs the tests that the human fix made pass (FAIL_TO_PASS) while checking that previously passing
tests still pass (PASS_TO_PASS). The full set has about 2,300 instances from a dozen repositories;
SWE-bench Lite is a 300-instance subset and SWE-bench Verified is a 500-instance subset screened by
humans for solvable issues and fair tests. It measures agentic software engineering: navigating a
codebase, editing, running tests. The sharp edge is that a score is a *system* score: the scaffold
(agent loop, tools, retries) moves results as much as the model, so "model X scores Y%" is incomplete
without the scaffold and the cost per task.

### GPQA: expert reasoning that search cannot shortcut
448 four-option questions in biology, physics and chemistry written by domain experts who have or
are pursuing PhDs, and validated by other experts. The construction target was that experts score high while skilled
non-experts with unrestricted web access score low (the paper reports roughly 65% for experts, about
74% discounting clear expert mistakes, versus about 34% for non-experts), hence "Google-proof". The
Diamond subset (198 questions) keeps only items that both expert validators answered correctly (or
where the expert who erred later acknowledged the mistake) and that most non-experts got wrong, and is
the number usually reported. It measures reasoning beyond retrieval and is where reasoning-oriented
models separate from others. Its weakness is size: 198 items means a wide confidence interval.

### Reading a score
Before comparing two numbers, align: shots, chain-of-thought, pass@1 versus pass@k, sampling
temperature and number of samples averaged, subset (Diamond, Verified, Lite), scaffold, evaluation
date, and whether the score is self-reported. Then apply statistics: a score is a proportion with
standard error √(p(1−p)/n). On GPQA Diamond at p = 0.6 that is ±3.5 points standard error, so two
models four points apart are not distinguishable at 95% confidence; on MMLU's ~14k items the same
calculation gives ±0.4. Two refinements matter in practice. Models evaluated on the *same* items can
be compared paired (count the items where exactly one model is right), which is tighter than the
independent intervals suggest, *because* item difficulty is shared; but this needs per-item results,
which published tables do not give you. And for sampled or reasoning models, a single run adds
sampling noise on top of item noise, so careful reports average several runs per item; a score from
one run on a 198-item set can move by several points on a re-run with nothing changed.

Saturation has a precise meaning here. A benchmark's effective ceiling is below 100% whenever some
items are mislabeled or ambiguous, so as top scores approach that ceiling, the remaining differences
reflect which models happen to agree with the label errors rather than capability. Finally ask about contamination: the dataset-contamination lesson covers how
to detect it.

### From benchmark to decision
Use benchmarks to shortlist: a coding assistant looks at SWE-bench-style results and the scaffold used;
a knowledge-heavy assistant looks at MMLU-style results; a scientific reasoning tool looks at GPQA. Then
build your own golden dataset, because none of these contain your documents, your prompt or your
failure modes. The benchmark answers "is this model capable enough to try"; your eval answers "does it
work here".

## Example
The unbiased pass@k estimator and a confidence interval for a reported score. With 10 samples per
problem of which 3 pass, pass@1 is 0.30 but pass@5 is about 0.92, which is why the two must never be
compared as if they were the same metric.

```python
from math import comb, sqrt

def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate of P(at least one of k samples passes) from n samples with c passes."""
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)

def ci95(score: float, n_items: int) -> float:
    """Half-width of a 95% normal-approximation interval for a proportion over n_items."""
    return 1.96 * sqrt(score * (1 - score) / n_items)

print(round(pass_at_k(10, 3, 1), 3), round(pass_at_k(10, 3, 5), 3))   # 0.3  0.917

benchmarks = {"GPQA Diamond": 198, "HumanEval": 164, "SWE-bench Verified": 500, "MMLU": 14_042}
for name, n in benchmarks.items():
    print(f"{name:20s} n={n:6d}  score 0.60 -> ±{ci95(0.60, n):.3f}")
# GPQA Diamond         n=   198  score 0.60 -> ±0.068
# HumanEval            n=   164  score 0.60 -> ±0.075
# SWE-bench Verified   n=   500  score 0.60 -> ±0.043
# MMLU                 n= 14042  score 0.60 -> ±0.008

# Per-problem resolution: one HumanEval problem moves the score by 1/164 ≈ 0.61 points.
```

The printed intervals are the point: a 5-point lead on GPQA Diamond or HumanEval is inside the noise
of a single evaluation run, while on MMLU it is not, and only a paired comparison on the same items
with the same protocol can tighten that.

## Trade-offs
- Standardized items and scoring make models comparable across labs, but the standardization also
  makes items easy to find and train on, so comparability decays with time.
- Execution-based grading (HumanEval, SWE-bench) is objective and resists paraphrase problems, but it
  needs sandboxes, dependencies and time per item, and test coverage bounds what "correct" means.
- Small expert-written sets (GPQA) are high in quality and were released with measures meant to keep
  them out of crawls (a canary string and a request not to post items in plain text), but they are
  public and not immune to contamination, and their size makes confidence intervals wide and
  single-run comparisons unreliable.
- Agentic benchmarks (SWE-bench) reflect real work far better than multiple choice, but they measure
  model plus scaffold, so a score is reproducible only with the full harness and its cost.

## Pitfalls
- Comparing pass@1 from one report with pass@k or best-of-n from another; the symptom is a "20-point
  lead" that disappears when both are run under the same protocol.
- Reading a 3-point difference on a 198-item set as a ranking; the symptom is model rankings that
  reorder between runs with no change to the models.
- Treating a SWE-bench number as a model property; the symptom is a model that scores far lower in your
  own agent scaffold than in the vendor's report.
- Ignoring contamination; the symptom is a model that aces HumanEval and fails on your private coding
  tasks of similar difficulty.
- Choosing a model for a domain task by its MMLU score; the symptom is a broad-knowledge leader that
  underperforms a cheaper model on your extraction job because the task needs instruction following,
  not trivia.
- Quoting self-reported scores as independent; the symptom is a leaderboard entry that nobody outside
  the vendor can reproduce.

## When to use
Use benchmarks at the start of model selection to shortlist candidates for the capability your task
needs, and when tracking the field to understand which capabilities are moving. Do not use them as the
release gate for a product, as evidence that a specific prompt works, or to compare models whose
scores came from different protocols. Measure what the benchmark measures: for coding, run a
SWE-bench-style harness yourself with your scaffold; for knowledge or reasoning, confirm on your own
items. In day-to-day work this looks like a one-page model-selection memo citing benchmark scores with
their protocol and intervals, followed by a golden-dataset run that actually decides. In interviews,
the expected depth is naming what each of these four benchmarks measures and how, computing pass@k,
and explaining why a leaderboard lead can be noise, contamination or a scaffold effect.

## Role lens
- **Engineer:** Reproduces a benchmark score locally with an open harness before trusting it, and
  computes the interval before calling one model better than another.
- **Architect:** Uses benchmarks to narrow the model portfolio, then defines the task-specific eval that
  governs the actual choice, including cost and latency.
- **FDE:** Explains to a customer why the model with the best public scores is not automatically the
  right one for their documents, and proposes a pilot eval on their data.

## Check yourself
**Q:** A vendor claims their new model beats a competitor by 6 points on GPQA Diamond and 9 points on
HumanEval. What questions do you ask before accepting the claim, and what do the numbers actually
support?
**A:** First the protocol: same shots, same chain-of-thought setting, pass@1 on both, same sampling
temperature, same number of samples averaged, and whether the competitor's number was re-run or copied
from an older report. Then the statistics: GPQA Diamond has 198 items, so each score carries roughly
±7 points at 95% confidence and a 6-point gap is inside the noise unless the comparison is paired on
items and repeated; HumanEval has 164 items, so 9 points is about 15 problems, borderline and heavily
dependent on contamination, since HumanEval solutions are widespread online. Finally relevance: neither
benchmark contains my task. The claim supports shortlisting the model for a pilot on our own golden set,
nothing more.

## Quiz
### Q: Why did HumanEval grade solutions by running unit tests instead of comparing them to a reference solution?
- [ ] Unit tests are cheaper than text comparison
- [x] Many different programs are correct, so text similarity to one reference does not measure correctness
- [ ] Reference solutions could not be written for the problems
- [ ] Text comparison cannot be automated for code
> Functional correctness is what matters for code and there are unboundedly many correct
> implementations. Execution against tests checks behaviour; similarity to one reference penalizes
> correct but different code and rewards incorrect but similar code.

### Q: Model A scores 62% and model B 58% on GPQA Diamond in one run each. What is the most defensible conclusion?
- [ ] A is better at expert science reasoning
- [ ] B is better because lower scores indicate less contamination
- [x] The difference is within the sampling noise of a 198-item set and does not rank the models
- [ ] GPQA is saturated and should not be used
> At p ≈ 0.6 and n = 198 the 95% interval is about ±7 points, wider than the 4-point gap. Ranking would
> need a paired comparison on the same items with the same protocol, ideally with several samples.

### Q: Why is a SWE-bench score best described as a system score rather than a model score?
- [ ] SWE-bench only evaluates proprietary systems
- [ ] The tests are run by the model itself
- [x] The agent scaffold (tools, loop, retries) materially changes how many issues get resolved with the same model
- [ ] Each instance is graded by human reviewers
> The task requires navigating a repository, editing files and running tests; how the harness exposes
> those actions, how many attempts it allows and what it costs all move the result. The same model in
> a different scaffold produces a different number.
