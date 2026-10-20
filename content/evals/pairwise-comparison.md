---
id: pairwise-comparison
module: evals
name: Pairwise comparison
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - evals/llm-as-a-judge
  - evals/judge-calibration-and-bias
  - evals/a-b-testing
  - evals/benchmarking
  - training-adaptation/rlhf
  - training-adaptation/dpo
sources:
  - Bradley and Terry, 1952 — Rank Analysis of Incomplete Block Designs: The Method of Paired Comparisons
  - Hunter, 2004 — MM Algorithms for Generalized Bradley-Terry Models
  - Zheng et al., 2023 — Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena
  - Chiang et al., 2024 — Chatbot Arena: An Open Platform for Evaluating LLMs by Human Preference
---

## TL;DR
Instead of asking "how good is this answer on a 1–10 scale?", pairwise comparison asks "which of these
two answers is better?". Relative judgments are more consistent for both humans and LLM judges because
they need no shared absolute scale. Many verdicts are aggregated into a win rate against a fixed
baseline, or into a ranking of several systems with a Bradley–Terry model (the method behind Chatbot
Arena), and the same pairs are the raw material of preference training (RLHF, DPO).

## Why it exists
Absolute scores drift. The same annotator gives a 7 on Monday and an 8 on Friday for equivalent
answers; different annotators carry different internal scales; an LLM judge compresses almost everything
into 7–9. The differences you actually want to detect between two prompt versions are a few points of
quality, and that signal drowns in scale noise.

Side-by-side judgment removes the scale. A rater who cannot say whether an answer is a 6 or a 7 can
usually say which of two answers is better, and will say the same thing tomorrow. This is an old
observation in psychometrics: comparative judgments are more reliable than absolute ones, which is why
paired-comparison models from the 1950s are now the backbone of LLM leaderboards. There is a second
benefit specific to generated text: a judge scoring one answer alone cannot notice what it *omitted*,
but with a second answer next to it the omission is visible.

The naive alternative, score each answer separately and compare the averages, suffers from both scale
noise and from any drift between the two scoring sessions. It also answers the wrong question: for a
release decision you want "is B better than A", which is what a pairwise verdict measures directly.

Pairwise has a real cost, which is why it is not the only method: a win rate says nothing about absolute
acceptability. Winning 70% of comparisons against a bad baseline does not make a product usable. So
pairwise is combined with a fixed strong baseline and with absolute gating criteria from a rubric.

## How it works
### The unit: one comparison
One prompt, two responses A and B, a verdict in {A, B, tie}. The judge may be a human or an LLM, free or
rubric-guided. Record the verdict, the order shown and the judge, because order matters: LLM judges show
position bias, so each pair is judged in both orders (or inconsistent pairs are treated as ties), and
for human raters the order is randomised.

### Aggregation 1: win rate against a baseline
Fix a baseline, usually the production system or a reference model, and compare each candidate to it.
Win rate = (wins + 0.5 × ties) / n. The noise is binomial: standard error ≈ √(p(1−p)/n). With n = 200
and p = 0.6 that is about 0.035, so the 95% interval is roughly ±7 points. To show a 55% win rate is
distinguishable from 50% you need the interval half-width below 5 points, which works out to several
hundred comparisons. This is why "B won 57 of 100" is not yet a decision.

### Aggregation 2: ranking many systems with Bradley–Terry
With more than two systems you want one consistent ranking from an incomplete, unbalanced set of
comparisons (not every pair was compared, and not equally often). The Bradley–Terry model gives each
system a strength β_i and says

```
P(i beats j) = e^{β_i} / (e^{β_i} + e^{β_j}) = sigmoid(β_i − β_j)
```

Strengths are fitted by maximum likelihood over all observed comparisons. It is used *because* it turns
heterogeneous pairwise outcomes into one scale, handles missing pairs naturally, and has a likelihood you
can bootstrap for confidence intervals. Only differences in β matter, so one system is anchored. Fitting
is a short fixed-point iteration (Hunter's MM algorithm): with p_i = e^{β_i}, W_i the total wins of i, and
n_ij the number of games between i and j,

```
p_i ← W_i / Σ_{j≠i} n_ij / (p_i + p_j)      then renormalise
```

**Elo** is the sequential approximation: update two ratings after every game with a step size K. It is
built for live play where the sequence matters, but on a static dataset its result depends on the order
comparisons are fed in and it is noisier than the batch fit. Leaderboards such as Chatbot Arena moved
from Elo-style updates to Bradley–Terry maximum likelihood with bootstrapped intervals for this reason.

### Ties, intransitivity and design
Ties are split as half a win each (or modelled explicitly with a tie parameter). Preferences can be
intransitive across prompt categories (A beats B on code, B beats C on writing, C beats A on safety);
Bradley–Terry assumes a single scale, so check per-category win rates before trusting a global rank.
Comparison budget is quadratic in the number of systems, so compare each new system against a few
anchors rather than against everything, and spend extra comparisons where the interval is widest.

### The same pairs train models
A reward model for RLHF is trained on exactly these (prompt, chosen, rejected) triples with the
Bradley–Terry likelihood as its loss, and DPO optimises the same preference likelihood directly against
the policy. The evaluation method and the training signal share a model, which means any bias in your
pairwise judging (a preference for length, say) is learned by the policy. Debiasing the judge is also
debiasing the training data.

## Example
Four systems compared on 100 prompts per pair; `wins[i, j]` counts how often system i beat system j,
with ties pre-split as 0.5. The MM iteration below fits Bradley–Terry strengths; the final line converts
two strengths into a predicted head-to-head probability.

```python
import numpy as np

names = ["prod", "promptB", "promptC", "ft-model"]
wins = np.array([[ 0, 42, 55, 30],
                 [58,  0, 61, 38],
                 [45, 39,  0, 27],
                 [70, 62, 73,  0]], dtype=float)      # wins[i, j]: i beat j this many times

def bradley_terry(wins, iters=200):
    n = len(wins)
    games = wins + wins.T                              # n_ij = comparisons between i and j
    W = wins.sum(axis=1)                               # total wins per system
    p = np.ones(n)
    for _ in range(iters):                             # MM / fixed-point iteration (Hunter, 2004)
        denom = np.array([sum(games[i, j] / (p[i] + p[j]) for j in range(n) if j != i)
                          for i in range(n)])
        p = W / denom
        p /= p.sum()                                   # strengths are relative; fix the scale
    return np.log(p)                                   # beta_i

beta = bradley_terry(wins)
for name, b in sorted(zip(names, beta), key=lambda t: -t[1]):
    print(f"{name:9s} beta = {b:+.2f}")
i, j = names.index("ft-model"), names.index("prod")
print(f"P(ft-model beats prod) = {1 / (1 + np.exp(-(beta[i] - beta[j]))):.2f}")
# Confidence intervals: resample the individual comparisons with replacement, refit,
# and report the 2.5th and 97.5th percentiles of each beta.
```

Running this ranks ft-model first, promptB second, prod third and promptC last, and predicts ft-model
beating prod about 70% of the time, close to the directly observed 70/100. The value of the fit is not
that pair, which was observed, but the consistent ranking of all four from unequal evidence and the
ability to bootstrap intervals on it.

## Trade-offs
- Pairwise verdicts are more consistent than absolute scores but say nothing about absolute acceptability;
  for a release gate, pair them with rubric criteria that an answer must pass on its own.
- Each comparison is cheap, but full coverage of n systems needs n(n−1)/2 pairs; once n grows past a
  handful, anchor every candidate to a baseline instead of comparing all pairs.
- Judging both orders doubles LLM-judge cost but is required to cancel position bias; with many human
  raters, randomising the order across raters is enough.
- Side-by-side presentation amplifies length and style bias because the longer answer looks more
  thorough; when variants differ in length, a length-controlled win rate is worth the modelling effort.
- Graded preferences ("slightly better", "much better") carry more information per comparison but lower
  agreement; use them only with several raters per item.

## Pitfalls
- A high win rate against a weak baseline; the symptom is 80% wins and a product nobody can use, because
  the baseline was never a bar.
- Dropping ties instead of splitting them; the symptom is a win rate that swings several points depending
  on tie handling.
- No confidence interval; the symptom is leaderboard positions reshuffling between runs on the same data.
- One global win rate over mixed prompt categories; the symptom is a variant that wins overall but loses
  on the category that matters most, such as safety-sensitive requests.
- Elo on a static dataset; the symptom is ratings that change when the comparison order is shuffled.
- Reusing LLM-judged pairs as DPO data without debiasing; the symptom is a model whose outputs get longer
  every training round while human preference stays flat.

## When to use
Reach for pairwise when comparing variants of one system (prompt A vs B, a model upgrade, a fine-tune),
when collecting preference data for training, and whenever humans judge subjective quality where an
absolute scale would not hold. Do not use it alone for release gating or for tasks with an executable
check. Measure win rate with a bootstrap interval, position-consistency rate, per-category win rates and
the length difference between arms. In interviews, expect to explain Bradley–Terry versus Elo, why a
leaderboard uses the batch fit, and roughly how many comparisons it takes to resolve a five-point
difference.

## Role lens
- **Engineer:** Builds the comparison harness: randomised order, both orders for an LLM judge, tie
  capture, a Bradley–Terry fit with bootstrap intervals, and a win-rate-versus-prod check per pull request.
- **Architect:** Chooses the baseline every comparison is anchored to and the win-rate-plus-interval
  threshold that justifies a switch, and decides when offline pairwise evidence must be confirmed by an
  online A/B test.
- **FDE:** Runs side-by-side sessions with customer SMEs, because "which is better?" is a question they
  answer quickly and consistently, then turns their preferences into a ranking of candidate configurations.

## Check yourself
**Q:** You have five candidate prompt variants and a budget of 300 human comparisons. How do you
allocate them, and how do you turn the verdicts into a decision?
**A:** Comparing all ten pairs evenly gives 30 comparisons each, far too few to separate anything. Anchor
instead: compare each variant against production first, around 40 each, drop the clear losers, then spend
the remainder on the top two against each other and against production. Randomise order; if an LLM judge
is used anywhere, judge both orders. Fit Bradley–Terry on every comparison for a joint ranking and
bootstrap intervals on the strengths. The decision rule is a variant whose interval against production
excludes 50% and which passes the rubric gates, with per-category win rates and the length difference
checked. If intervals overlap, buy more comparisons or move to an online A/B test.

## Quiz
### Q: Why did large preference leaderboards move from Elo-style updates to Bradley–Terry maximum likelihood?
- [ ] Elo cannot handle more than two competitors
- [x] Elo is a sequential update whose result depends on the order of comparisons and is noisier; the batch fit uses all comparisons jointly and supports bootstrap intervals
- [ ] Bradley–Terry corrects for length bias automatically
- [ ] Elo requires the same number of games for every pair
> On a fixed dataset there is no reason to process comparisons sequentially; the joint maximum-likelihood
> fit is order-independent and comes with a likelihood you can resample. Neither method addresses length bias.

### Q: A new variant wins 57 of 100 comparisons against production. Should it ship?
- [ ] Yes, 57% is above 50%
- [ ] No, a variant needs at least 60% to be meaningful
- [x] Not yet: the binomial standard error is about 5 points, so the 95% interval includes 50%; collect more comparisons or check per category
- [ ] Yes, provided ties were counted as wins
> √(0.57 × 0.43 / 100) ≈ 0.05, giving an interval of roughly 47–67%. The data is consistent with no
> difference; a fixed 60% bar is arbitrary and counting ties as wins would bias the result.

### Q: Under Bradley–Terry, P(A beats B) = 0.73 and P(B beats C) = 0.73. What does the model imply for P(A beats C)?
- [ ] Exactly 0.73, the same as the other two
- [ ] About 0.53, the product of the two probabilities
- [x] About 0.88, because strengths add on the log-odds scale; if observed data disagrees strongly, the preferences are intransitive and a single scale fits poorly
- [ ] Nothing can be inferred without a direct comparison
> logit(0.73) ≈ 0.99, so β_A − β_C ≈ 1.99 and sigmoid(1.99) ≈ 0.88. A large gap between that prediction
> and the observed A-vs-C rate is the standard check for intransitive preferences.
