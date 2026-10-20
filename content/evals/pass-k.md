---
id: pass-k
module: evals
name: pass@k
kind: metric
difficulty: core
est_minutes: 14
status: reviewed
related:
  - evals/benchmarking
  - evals/eval-harness
  - evals/regression-tests
  - evals/dataset-contamination
  - model-internals/sampling-strategies
  - agent-reliability/agent-trajectory-evaluation
sources:
  - Kulal et al., 2019 — SPoC: Search-based Pseudocode to Code
  - Chen et al., 2021 — Evaluating Large Language Models Trained on Code
  - Yao et al., 2024 — τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains
---

## TL;DR
pass@k is the probability that at least one of k sampled solutions passes a correctness check, averaged
over problems. It exists for tasks with an executable oracle, where one greedy sample understates what
a model can do if you can sample and verify. Estimate it from n ≥ k samples with the unbiased
combinatorial estimator, not by plugging a pooled pass rate into a formula. For k > 1 it is an *oracle*
number: what a perfect verifier could extract, not what a user sees on one try.

## Why it exists
Code generation has a property most LLM tasks lack: correctness can be checked by running the output.
Before functional metrics, generated code was scored by text similarity to a reference solution (BLEU),
which fails in both directions: a correct program written differently scores low, and a wrong program
that copies most of the reference scores high. Running tests fixes that. pass@k, introduced for
program synthesis by Kulal et al. and adopted for LLM evaluation in the Codex paper, adds the second
ingredient: generation is stochastic, so "did it pass?" has to be asked about a *distribution* of
samples, not one.

Why not just report greedy accuracy? Because production systems rarely take one sample. They draw
several, run the tests, and keep a passing one, or rank them with a verifier. The question that
matters for such a system is "how likely is it that at least one of k attempts is correct", and
greedy accuracy does not answer it. Conversely, a system that shows the user one answer cares about
pass@1 and nothing else, so the metric has to be explicit about k.

The naive way to estimate pass@k is to draw exactly k samples per problem and mark the problem solved
if any passes. That is unbiased but very noisy: one coin flip per problem. Worse, the shortcut many
people reach for, 1 − (1 − p̂)^k with p̂ the benchmark-wide pass rate, is wrong in a systematic
direction. Problems differ: some are solved nine times out of ten, others never. Averaging 1 − (1 − p_i)^k
over problems gives less than applying it to the mean p, because the function is concave, so the pooled
shortcut **overestimates** pass@k, sometimes by a lot. The estimator below exists to avoid both failures.

## How it works
### Definition
For one problem with true per-sample success probability p, pass@k = 1 − (1 − p)^k. The benchmark
score is the mean over problems. pass@1 is simply the mean of p, the expected success of a single
sample at the chosen temperature.

### The unbiased estimator
Draw n ≥ k samples for each problem and count the c that pass. Then

```
pass@k ≈ 1 − C(n − c, k) / C(n, k)
```

The logic: among the C(n, k) possible k-subsets of your n samples, the ones containing *no* passing
sample are the k-subsets of the n − c failures, C(n − c, k) of them. The ratio is the probability a
random k-subset fails entirely, and its expectation over the sampling is exactly (1 − p)^k because the
n samples are independent draws. Averaging over all k-subsets instead of looking at one is what cuts
the variance; Chen et al. used n = 200 samples per problem to report k up to 100. Computing the
binomials through floating-point factorials overflows (200! ≈ 7.9 × 10^374, far beyond float64's
≈ 1.8 × 10^308), so the standard implementation uses the numerically stable product form:

```
pass@k ≈ 1 − Π_{i = n − c + 1}^{n} (1 − k / i)
```

and returns 1 when n − c < k, since every k-subset then contains a pass.

### Temperature changes the answer
pass@1 is highest at low temperature, where the model commits to its most likely program. pass@100
is highest at a higher temperature, because diversity is what makes a hundred samples cover more of
the solution space. So every pass@k figure is tied to a temperature, and comparing two models' pass@1
sampled at different temperatures, or greedy versus sampled, is not a comparison.

### Reading the gap
pass@1 = 30% with pass@10 = 70% says that many problems are solvable *sometimes*: a verifier that
picked the right sample would more than double accuracy. The pass@k − pass@1 gap is the headroom for
selection work: test-based filtering, reranking, verifiers, majority voting. A small gap means the model
either solves a problem reliably or not at all, and the lever is the model or the prompt, not sampling.

### Relatives that answer different questions
- **maj@k** (majority vote over k samples) and **best-of-n with a verifier** measure what a deployable
  selection strategy actually achieves. Both are bounded above by pass@k and usually sit between pass@1
  and pass@k, though maj@k can fall below pass@1 when the wrong answers agree with each other more often
  than the right ones do.
- **pass^k** (introduced for agent benchmarks in τ-bench) is the probability that *all* k attempts
  succeed: p^k per problem, estimated by C(c, k) / C(n, k). It measures reliability. A customer who uses
  an agent every day experiences pass^k, not pass@k; the two diverge sharply as k grows.
- For multi-step agents, "pass" means the final state checks out. Retrying k times only makes sense
  when side effects are idempotent, so pass@k for agents assumes a sandbox or safe rollback.

## Example
The estimator in the numerically stable product form, applied to one problem and then to a small
benchmark, with the pooled shortcut computed alongside to show how far off it is.

```python
import numpy as np

def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k from n samples of which c passed (Chen et al., 2021)."""
    if n - c < k:
        return 1.0                                  # every k-subset contains a passing sample
    return 1.0 - np.prod(1.0 - k / np.arange(n - c + 1, n + 1))

# One problem: 20 samples at T=0.8, 4 passed the hidden tests.
for k in (1, 5, 10):
    print(f"n=20 c=4  pass@{k} = {pass_at_k(20, 4, k):.3f}")
# pass@1 = 0.200   pass@5 = 0.718   pass@10 = 0.957

# A five-problem benchmark, (n, c) per problem. Average per problem; never pool c and n.
results = [(20, 4), (20, 0), (20, 20), (20, 1), (20, 9)]
pooled_p = sum(c for _, c in results) / sum(n for n, _ in results)        # 0.34
for k in (1, 5):
    per_problem = np.mean([pass_at_k(n, c, k) for n, c in results])
    shortcut = 1 - (1 - pooled_p) ** k
    print(f"pass@{k}: mean over problems = {per_problem:.3f} | pooled shortcut = {shortcut:.3f}")
# pass@1: 0.340 vs 0.340   (identical: k=1 is linear in p)
# pass@5: 0.588 vs 0.875   (the shortcut overstates by almost 30 points)
```

The problem with c = 0 is why the shortcut fails: no amount of sampling solves it, but pooling spreads
its zero across the other problems as if sampling could.

## Trade-offs
- A larger k reveals headroom, but that headroom is only realisable with a verifier or test oracle; with
  no cheap check, pass@1 or maj@k is the honest number to publish.
- More samples per problem reduce estimator variance but cost n× inference; use a small n for the pass@1
  trend in CI and a large n only when comparing models carefully.
- Higher temperature raises pass@k for large k and lowers pass@1; a product that serves one answer
  should tune and report at the temperature it ships with.
- Tests are an objective oracle but an incomplete one: a sample can pass visible tests and still be
  wrong; once the model can see the tests, hidden tests become necessary.

## Pitfalls
- Computing pass@k from greedy decoding, so all k samples are identical; the symptom is pass@10 equal to
  pass@1 to the last decimal.
- Pooling c and n across problems; the symptom is a pass@k that does not reproduce when someone recomputes
  it per problem.
- Computing the binomials with floating-point factorials (or a float `comb`) at n = 200; the symptom is
  `inf` or a NaN in the report. Python's exact-integer `math.comb` avoids this, but the product form is
  simpler and vectorises.
- Comparing pass@1 numbers from reports that used different temperatures or sample counts; the symptom
  is a model "winning" by a margin that vanishes under one protocol.
- Describing agent reliability with pass@k; the symptom is pass@5 = 90% on the slide while customers who
  get one attempt see failures most of the time.
- Public benchmark contamination; the symptom is pass@1 far above what the model achieves on a private
  set of equivalent difficulty.

## When to use
Use pass@k for any task with executable verification: code against tests, SQL against expected result
sets, math with checkable answers, agent tasks with state assertions. Report pass@1 as the user-facing
number, pass@k for headroom analysis when you are considering a sample-and-verify design, and pass^k
when repeated reliability is the product promise. Fix the temperature, use n ≥ k samples, keep tests
hidden, and check for contamination with a private set. In interviews, be able to derive the estimator,
explain why the pooled shortcut overestimates, and separate pass@k from pass^k.

## Role lens
- **Engineer:** Implements the product-form estimator, runs samples in a sandbox against hidden tests,
  and tracks pass@1 per commit with a noise band from the sample count.
- **Architect:** Decides whether latency and cost budgets allow best-of-n with a verifier, where the
  pass@k headroom matters, or force a single-shot design where only pass@1 counts.
- **FDE:** Builds the test oracle for a customer's code-migration or text-to-SQL deployment from their
  own cases and reports pass@1 and pass^k so expectations match what their users experience.

## Check yourself
**Q:** A vendor reports pass@1 = 45% and pass@10 = 80% on your internal text-to-SQL benchmark. Your
product returns one query per request within a two-second budget. What do these numbers tell you, and
what would you build?
**A:** pass@1 is today's user experience: 45% of queries correct on the first try. The 35-point gap is
headroom that exists only with selection, so the question is whether you can verify cheaply within two
seconds. Candidates: sample several queries in parallel, discard those that fail to parse or violate the
schema, execute the survivors and majority-vote on result sets (maj@k), or rank with a verifier. Measure
that strategy directly rather than assuming you get 80%; expect something between the two numbers. Check
which temperature the vendor used, re-run on a private set to rule out contamination, and if customers
rerun similar queries daily, report pass^k as well, since reliability is what they will feel.

## Quiz
### Q: With n = 20 samples of which 4 passed, why is 1 − C(16, 5) / C(20, 5) preferred over drawing 5 samples and checking whether any passed?
- [ ] It corrects for test leakage between samples
- [x] It averages over every possible 5-subset of the 20 samples, so it has much lower variance than one 5-sample draw while staying unbiased
- [ ] It is biased upward, which gives a more optimistic figure
- [ ] It allows k to be larger than n
> Both estimators are unbiased, but the single draw is one coin flip per problem; the combinatorial
> estimator uses all 20 samples for every k, which is the whole point of drawing n ≥ k.

### Q: Your benchmark shows pass@1 = 30% and pass@10 = 31%. What does that tell you?
- [ ] The tests are broken and need rewriting
- [ ] The sampling temperature was too high
- [x] Provided the samples were drawn at a normal sampling temperature, the model either solves a problem reliably or not at all, so sampling plus verification has little headroom; improve the model or the prompt
- [ ] A verifier would roughly double accuracy
> A tiny pass@k − pass@1 gap means extra samples rarely find a solution the first sample missed, the
> opposite of the case where a verifier pays off. Rule out near-greedy sampling first: identical samples
> also produce this gap, and a high temperature would widen it, not shrink it.

### Q: Which metric describes "the agent completes the task on every one of k repeated attempts"?
- [ ] pass@k
- [ ] maj@k
- [x] pass^k
- [ ] recall@k
> pass@k needs only one success among k tries, maj@k needs a majority to agree, and recall@k is a
> retrieval metric; pass^k is p^k per problem and is the reliability figure for repeated use.
