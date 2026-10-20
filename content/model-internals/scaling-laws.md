---
id: scaling-laws
module: model-internals
name: Scaling laws
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - model-internals/small-language-models
  - model-internals/mixture-of-experts
  - model-internals/perplexity
  - model-internals/reasoning-models-test-time-compute
  - training-adaptation/pre-training
  - training-adaptation/data-curation
  - infra-capacity-economics/cost-modeling
sources:
  - Kaplan et al., 2020 — Scaling Laws for Neural Language Models
  - Hoffmann et al., 2022 — Training Compute-Optimal Large Language Models
  - Schaeffer, Miranda and Koyejo, 2023 — Are Emergent Abilities of Large Language Models a Mirage?
  - Muennighoff et al., 2023 — Scaling Data-Constrained Language Models
---

## TL;DR
Scaling laws are empirical power-law fits that predict a language model's pre-training loss from its
parameter count N, training tokens D and compute C. They exist because frontier runs cost too much to try
blindly: fitting the curve on small runs tells you how to split a budget between model size and data.
Kaplan showed loss falls smoothly along each axis; Chinchilla corrected the split to about 20 tokens per
parameter; practice has since moved to over-training smaller models because serving cost scales with N.

## Why it exists
Deciding how big a model to train and how long to train it used to be craft. The cost of guessing wrong is
enormous: a frontier run is on the order of 10²⁵ FLOPs (illustrative) and months of accelerator time, so you
cannot grid-search the design. Scaling laws turn the question into extrapolation. Train a ladder of small
models spanning several orders of magnitude of compute, fit a curve, and read off the best configuration for
the budget you actually have.

Kaplan et al. found in 2020 that loss is a clean power law in N, D and C across many orders of magnitude,
and that architectural details such as depth versus width matter far less than scale. That result licensed
the "just scale it" strategy of the following years. But their fit also recommended growing parameters
much faster than data as compute grows, which produced under-trained giants such as Gopher (280B
parameters on about 300B tokens). Hoffmann et al. repeated the study in 2022 with a learning-rate schedule
matched to each run's length and found that the compute-optimal recipe scales N and D roughly equally,
about 20 tokens per parameter. Chinchilla, 70B parameters on 1.4T tokens, beat Gopher at the same training
compute. Nothing about the architecture changed; the budget split did.

The naive alternative, "bigger is always better", fails twice. At a fixed budget it wastes compute on
parameters that never see enough data to be useful. And it ignores that every parameter must be paid for
again on every token the model ever serves, which for a popular model is far more compute than training.

## How it works
### The compute identity
A forward pass costs about 2N FLOPs per token (one multiply-add per parameter); the backward pass costs
about twice that; so training costs approximately `C = 6 N D` FLOPs. Inference costs about 2N FLOPs per
generated token, plus attention, which is small unless contexts are very long. This identity is the bridge
between the training-side and serving-side arguments below.

### The loss fit
Chinchilla's parametric form is

```
L(N, D) = E + A / N^α + B / D^β
```

E is the irreducible entropy of the text under the tokenizer, A/N^α is the approximation error from finite
capacity, and B/D^β is the estimation error from finite data. Their published fit gave E ≈ 1.69, A ≈ 406.4,
B ≈ 410.7, α ≈ 0.34 and β ≈ 0.28 for their data and tokenizer. The constants do not transfer to other
setups; the shape does. Minimising L subject to 6ND = C gives N_opt ∝ C^a and D_opt ∝ C^b with
a = β/(α+β) and b = α/(α+β). When a ≈ b ≈ 0.5, N and D grow in lockstep as compute grows, and that is why
the compute-optimal tokens-per-parameter ratio is a constant rather than a function of budget. With the
constants above, a ≈ 0.45, so this particular fit favours data slightly more; the other two methods below
gave close to 0.5 each.

### Three ways to fit it
Hoffmann et al. used three methods and got the same answer: fix N and vary training length, taking the
lower envelope of the curves; draw IsoFLOP curves (at fixed C, vary N and find the loss-minimising size); and
fit the parametric form directly. The agreement is what made the result convincing. Method matters: Kaplan's
different exponents came partly from using one learning-rate schedule regardless of run length, which
penalises long runs, and partly from counting parameters without embeddings, which distorts the small-model
end of the ladder. If you ever fit your own law, tune the schedule per run and state what you count.

### Inference-aware over-training
Compute-optimal means "cheapest training for a given loss". It ignores serving. If a model will generate T
tokens over its life, lifetime compute is roughly `6ND + 2NT`. When T is much larger than D, which is true
for any widely used model, shrinking N and raising D at the same training budget raises the first term only
slightly and cuts the second term in proportion. This is why Llama 3 8B was trained on more than 15T tokens,
close to 2,000 tokens per parameter, and why strong small models exist at all. The loss curve flattens past
the optimum but does not stop improving.

### What the laws do and do not predict
They predict pre-training loss, equivalently perplexity, on held-out text. They do not directly predict
downstream task accuracy. Metrics with thresholds (exact match, pass/fail) can jump abruptly while loss
falls smoothly; this is the "emergent abilities" phenomenon, and Schaeffer et al. argued that much of the
discontinuity comes from the metric, since smoother measures such as the probability assigned to the correct
answer improve gradually. The laws also assume a fixed data distribution: filtered or synthetic data shifts
the constants, repeated data behaves like fresh data for only a few epochs before returns diminish sharply
(Muennighoff et al.), and mixture-of-experts models change what N means, since active and total parameters
scale loss and cost differently. Finally, reasoning models added a third lever: for some tasks, spending more
inference compute per query buys more accuracy than a larger base model, which is covered in the test-time
compute lesson.

## Example
Take a 10²⁴ FLOP training budget and a model that will serve 10¹³ tokens over its life (both
illustrative), and use Chinchilla's published parametric fit as a stand-in for the curve. Compare three
ways to spend the same budget: the 20-tokens-per-parameter rule, the minimum of the fitted curve itself,
and a model 10× smaller than the rule suggests.

```python
import math

E, A, B, alpha, beta = 1.69, 406.4, 410.7, 0.34, 0.28   # Chinchilla's published fit; setup-specific

def loss(N, D):
    return E + A / N**alpha + B / D**beta

C = 1e24                                     # training budget in FLOPs (illustrative)
T = 1e13                                     # tokens served over the model's life (illustrative)
tokens_for = lambda N: C / (6 * N)           # spend the whole budget: 6 N D = C

N_rule = math.sqrt(C / (6 * 20))             # the "20 tokens per parameter" rule
N_fit = min((10 ** (9 + i / 100) for i in range(300)),
            key=lambda N: loss(N, tokens_for(N)))   # argmin of the fitted curve itself
N_small = N_rule / 10                        # deliberately over-trained

for tag, N in [("20:1 rule", N_rule), ("fit argmin", N_fit), ("over-trained", N_small)]:
    D = tokens_for(N)
    print(f"{tag:12s} N={N/1e9:5.1f}B  D={D/1e12:5.1f}T  D/N={D/N:6.0f}  loss={loss(N, D):.3f}  "
          f"lifetime={6*N*D + 2*N*T:.2e} FLOPs")
# 20:1 rule    N= 91.3B  D=  1.8T  D/N=    20  loss=1.918  lifetime=2.83e+24 FLOPs
# fit argmin   N= 41.7B  D=  4.0T  D/N=    96  loss=1.911  lifetime=1.83e+24 FLOPs
# over-trained N=  9.1B  D= 18.3T  D/N=  2000  loss=1.936  lifetime=1.18e+24 FLOPs
```

Three lessons sit in those rows. First, the over-trained 9B model is only about 0.02–0.025 nats worse
than either alternative yet costs 10× less per served token, so its lifetime compute is less than half the
20:1 model's. Second, the loss surface is flat near its minimum: models spanning a 10× range of N land
within a few hundredths of a nat, which is why the budget split can be bent toward inference economics at
little cost. Third, the published constants and the published rule disagree: minimising this fit puts the
optimum near 96 tokens per parameter, not 20. The paper's other two estimation methods gave the roughly
20:1 split, and a later replication (Besiroglu et al., 2024) argued the published parametric constants were
poorly fitted. Trust the shape of a scaling law, not its constants to the second decimal place.

The decision then moves to the question the law cannot answer: does 0.02 nats of loss matter on the tasks
you care about? That is measured with task evals, not predicted.

## Trade-offs
- Compute-optimal training minimises training cost per unit of loss, but yields a large model that is
  expensive to serve; it flips to over-training as soon as lifetime served tokens exceed a few multiples of
  the training tokens.
- Extrapolating from a ladder of small runs is cheap and reliable for loss, but reliability degrades when you
  change the data mix, tokenizer or architecture, or extrapolate many orders of magnitude past the fitted
  range.
- Spending on data quality shifts the whole curve down, often more cheaply than extra compute, but the shift
  cannot be measured without training ladder models on the new data.
- Test-time compute improves hard-task accuracy without retraining, but it is paid per query forever; it
  wins for low-volume hard queries and loses to a better base model for high-volume easy ones.

## Pitfalls
- Treating 20 tokens per parameter as a law of nature; it is a fit for Chinchilla's data and objective, and
  the ratio for a model you will deploy is set by inference economics, often in the hundreds or thousands.
- Expecting downstream accuracy to follow the smooth loss curve; the symptom is a benchmark flat near zero
  across several scales and then jumping, leading either to "emergence" claims or to abandoning a capability
  one scale too early.
- Counting parameters inconsistently, with or without embeddings, or active versus total for MoE; the
  symptom is fits that disagree with published ones by a large factor, especially at the small end.
- Fitting on runs that share one learning-rate schedule regardless of length; the symptom is long runs that
  look worse than they are, biasing the fit toward bigger models, which is a large part of the
  Kaplan/Chinchilla discrepancy.
- Counting repeated epochs as fresh tokens; beyond a few repetitions the returns fall off sharply, so the D in
  your formula is not the D the model effectively saw.

## When to use
Most practitioners never fit a scaling law; they use its implications. Use them to choose which model to
deploy (a heavily over-trained 8B can match a compute-optimal 20B), to estimate inference cost from 2N
FLOPs per token, to sanity-check claims ("70B parameters trained on 100B tokens" means badly under-trained),
and to frame the choice between a bigger model and more test-time compute. If you do continued pre-training
or large fine-tunes, the data-constrained results tell you how many epochs are worth running. If you train
from scratch, run a ladder and fit before committing the budget. In interviews, be able to derive 6ND,
explain why Chinchilla overturned Kaplan, and explain why Llama-style models are trained far past the
compute-optimal point.

## Role lens
- **Engineer:** Uses 2N FLOPs per token and bytes per decode step to estimate cost and latency of candidate
  models, and reads tokens-per-parameter off a model card as a quick proxy for how well-trained it is.
- **Architect:** Frames model-size decisions as lifetime cost, training plus inference, and decides where a
  reasoning mode substitutes for a larger base model within the portfolio.
- **FDE:** Turns a customer's "bigger is better" instinct into a measured accuracy-versus-cost curve on their
  task, and explains why a well-trained small model can beat an older large one.

## Check yourself
**Q:** Your company has a fixed training budget and a product that will serve a very high token volume for
years. A colleague proposes training the Chinchilla-optimal model size. What do you push back on, and how
do you quantify it?
**A:** Chinchilla optimality minimises training compute for a target loss and ignores serving. Write
lifetime cost as 6ND + 2NT where T is tokens served; when T is far larger than D the inference term dominates,
so it is worth shrinking N and raising D at the same training budget and accepting a slightly higher loss.
Quantify it with a fitted L(N, D): at 10²⁴ FLOPs the 20:1 rule gives about 90B parameters on 1.8T
tokens, while a 9B model on 18T tokens is roughly 0.02 nats worse yet 10× cheaper per served token, because
the loss surface is flat near its optimum. Then check
whether that loss gap shows up on the downstream evals that matter, note that over-training has diminishing
returns, and confirm you can actually source 18T tokens without heavy repetition.

## Quiz
### Q: Why did Chinchilla (70B parameters, 1.4T tokens) outperform Gopher (280B parameters, 300B tokens) at similar training compute?
- [ ] It used a better tokenizer
- [x] Gopher was under-trained: at fixed compute, loss is minimised by scaling parameters and tokens roughly equally, about 20 tokens per parameter
- [ ] Larger models overfit on web text
- [ ] It used mixture-of-experts layers
> Both runs spent similar compute, but Gopher put it into parameters that saw barely one token each.
> Rebalancing toward data at the same budget lowers loss; neither tokenizer nor architecture was the lever.

### Q: A vendor's 7B model was trained on 2T tokens. Roughly how far is that from Chinchilla-optimal, and why might it be deliberate?
- [ ] At the optimum; 7B × 300 ≈ 2T
- [ ] Under-trained; a 7B model needs about 7T tokens to be optimal
- [x] About 14× past the roughly 140B-token optimum; serving cost scales with parameters, so a smaller over-trained model is cheaper over its lifetime
- [ ] Over-training is a mistake because loss stops improving after 20 tokens per parameter
> Chinchilla-optimal for 7B is about 7B × 20 = 140B tokens, so 2T is roughly 14× more. Loss keeps falling
> past the optimum, and since inference FLOPs are about 2N per token, a smaller model that will serve
> trillions of tokens repays the extra training many times over.

### Q: Which quantity do scaling laws predict most reliably?
- [ ] Accuracy on a multiple-choice benchmark
- [x] Held-out pre-training loss, that is cross-entropy per token
- [ ] Hallucination rate
- [ ] Instruction-following quality after RLHF
> The laws are fits of pre-training loss against N, D and C. Thresholded task metrics can jump
> discontinuously as loss falls smoothly, and post-training quality depends on data and reward signals the
> laws do not model.
