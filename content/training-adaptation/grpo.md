---
id: grpo
module: training-adaptation
name: GRPO
kind: concept
difficulty: advanced
est_minutes: 17
related:
  - training-adaptation/rlhf
  - training-adaptation/dpo
  - training-adaptation/post-training
  - training-adaptation/synthetic-data-generation
  - model-internals/reasoning-models-test-time-compute
  - prompting/chain-of-thought
  - evals/pass-k
  - evals/task-specific-acceptance-criteria
sources:
  - Shao et al., 2024 — DeepSeekMath: Pushing the Limits of Mathematical Reasoning in Open Language Models
  - DeepSeek-AI, 2025 — DeepSeek-R1: Incentivizing Reasoning Capability in LLMs via Reinforcement Learning
  - Schulman et al., 2017 — Proximal Policy Optimization Algorithms
---

## TL;DR
GRPO is a policy-gradient method that drops PPO's value model: for each prompt it samples a group of
responses, scores them, and uses the group's mean and spread as the baseline, so each response's
advantage is "how much better than its siblings". It keeps PPO's clipped update and a KL term to a
reference. It exists because a critic the size of the policy is costly and unstable for sequence-level
rewards, and it became the standard algorithm for training reasoning models on verifiable rewards.

## Why it exists
PPO estimates how good each token was by comparing the reward it eventually earned against a
*value model's* prediction of expected reward from that point. For language, the value model is
usually another copy of the policy with a scalar head, trained alongside it. That doubles trainable
memory, and the value estimates are poor: the reward arrives only at the end of a long response, the
value model must guess from partial text, and its errors feed straight into the advantages. Teams
spent as much effort stabilising the critic as training the policy.

Meanwhile a new kind of reward appeared. For maths with a known answer, code with unit tests, or
output with a checkable format, a *program* can score a response: right or wrong, passes or fails. No
reward model, no raters, no overoptimisation of a learned proxy. With rewards this cheap, you can
afford to sample many responses per prompt, and that changes what the best estimator is. If you have
eight attempts at the same problem, the natural question is not "was this better than a critic's
guess" but "was this better than the other seven". The group *is* the baseline.

The naive alternative, REINFORCE with the raw reward, works in principle but has enormous variance:
every correct answer gets the same push regardless of whether the prompt was trivial or hard. The
group-relative advantage fixes this automatically: on a prompt the model always solves, all rewards
are equal and the advantage is zero, so no gradient is wasted on it; on a prompt it solves one time in
eight, the single success gets a large positive advantage. The signal concentrates where learning is
possible.

## How it works
### Sampling the group
For each prompt q in a batch, the current policy (the "old" policy for this iteration) samples G
responses o₁ … o_G, with G typically in the range of 4 to 16 (larger for hard tasks). Each response
gets a scalar reward r_i from a reward function: a rule-based verifier, a reward model, or a sum of
several terms (accuracy plus format, for instance).

### Group-relative advantage
The advantage for response i is its reward standardised within the group:

```
Â_i = ( r_i − mean(r₁…r_G) ) / std(r₁…r_G)
```

The same Â_i is assigned to every token of response i, because the reward is for the whole outcome
and nothing finer-grained is known. This replaces the value model with two numbers per prompt. It is
cheap, unbiased as a baseline, and has the "difficulty-adaptive" property described above. A group with
all-equal rewards has zero variance and contributes nothing; implementations add a small epsilon to
the denominator and often filter such prompts out of the batch.

### The clipped surrogate
With advantages in hand, GRPO uses PPO's clipped objective per token:

```
ratio_t = π_θ(o_t | q, o_<t) / π_old(o_t | q, o_<t)
L_clip  = mean over tokens of  min( ratio_t · Â_i ,  clip(ratio_t, 1−ε, 1+ε) · Â_i )
```

The ratio compares the policy being updated to the one that generated the samples, and the clip
stops any token's probability from moving by more than a factor of about 1±ε in one update. This lets
the same batch of samples be reused for a few gradient steps without the policy running away from the
data, which is the whole point of PPO-style methods over plain policy gradient.

### The KL term
Instead of folding the KL penalty into the reward as PPO does, GRPO adds it directly to the loss:
`L = −L_clip + β · KL(π_θ ‖ π_ref)`, estimated per token with the low-variance estimator
`π_ref/π_θ − log(π_ref/π_θ) − 1`, which is always non-negative. The reference is the SFT or base
checkpoint the run started from. Keeping the KL out of the reward means advantages reflect only task
success, and drift is controlled separately. Some later recipes drop the KL term entirely when the
reward is fully verifiable and the run is monitored, trading safety for faster movement.

### Reward design
GRPO's results depend on the reward function more than on the algorithm. DeepSeek-R1 used rule-based
rewards: an accuracy reward checking the final answer against ground truth, and a format reward
checking that reasoning appears inside designated tags. Rule-based rewards cannot be gamed by
flattery the way a learned reward model can, but they can be gamed by format tricks, by answers that
match a lax checker, or by test suites that are incomplete. For tasks without a verifier, a reward
model is used and GRPO inherits RLHF's overoptimisation problem.

### Why long reasoning emerges
With outcome-only rewards and many samples, responses that happen to check their work, backtrack, or
try alternatives succeed more often on hard problems, so the policy is pushed toward them. Response
length tends to grow during training as a result, which is a feature when it buys accuracy and a cost
when it does not. A known subtlety is that averaging the loss per response over its own length (as in
the original formulation) interacts with length: wrong long responses are penalised less per token than
wrong short ones, which can bias the model toward verbosity on failures. Later work proposed removing
the length and standard-deviation normalisations for this reason; which normalisation you use is a
real design choice, not a detail.

## Example
Group advantages and the GRPO loss for one prompt with eight sampled responses. Rewards come from a
verifier; three of eight are correct. Token log-probs are illustrative placeholders.

```python
import torch

G, eps_clip, beta = 8, 0.2, 0.04
rewards = torch.tensor([1., 0., 0., 1., 0., 0., 0., 1.])         # verifier: 3 of 8 correct
adv = (rewards - rewards.mean()) / (rewards.std() + 1e-4)        # group-relative advantage, one per response
print("advantages:", [round(a, 2) for a in adv.tolist()])
# mean 0.375 -> correct responses get ~ +1.21, wrong ones ~ -0.72 (sample std); a 0/8 or 8/8
# group would get all zeros and teach nothing.

def grpo_loss(logp_new, logp_old, logp_ref, adv, mask):
    """logp_* : [G, T] per-token log-probs; mask: [G, T] 1 on response tokens; adv: [G]."""
    ratio = torch.exp(logp_new - logp_old)                          # pi_theta / pi_old, per token
    a = adv[:, None]                                                # same advantage on every token
    surr = torch.min(ratio * a, torch.clamp(ratio, 1 - eps_clip, 1 + eps_clip) * a)
    lr = logp_ref - logp_new                                        # log(pi_ref / pi_theta)
    kl = torch.exp(lr) - lr - 1                                     # non-negative per-token estimator
    per_tok = -(surr - beta * kl)
    return (per_tok * mask).sum() / mask.sum()                      # token-mean over the group

T = 12
logp_old = -torch.rand(G, T) * 3
logp_new = logp_old + 0.05 * torch.randn(G, T)                     # a small policy change
logp_ref = logp_old + 0.1 * torch.randn(G, T)
mask = torch.ones(G, T)
print("loss:", grpo_loss(logp_new, logp_old, logp_ref, adv, mask).item())
# In a real step: sample G responses per prompt with vLLM, score them, compute logp_old once,
# then take 1-4 gradient steps on this loss before sampling again.
```

The printed advantages show the mechanism: on a prompt the model gets right three times in eight,
each success receives a push of about +1.2 and each failure about −0.7. Had it been right seven times,
the single failure would get a large negative push and the successes a tiny positive one, which is
exactly where the gradient should go.

## Trade-offs
- Removing the value model halves trainable memory and eliminates critic instability, but the group
  baseline needs several samples per prompt, so generation cost per update rises with G.
- Verifiable rewards cannot be flattered and need no raters, yet they only exist for tasks with a
  checker, and a lax checker becomes the thing the model optimises instead of the task.
- Larger groups give lower-variance advantages and more useful signal on hard prompts, while small
  groups are cheaper; the balance shifts toward larger G as the pass rate per prompt falls.
- Keeping the KL term protects capability and prevents collapse, but slows movement on tasks where
  the reward is trustworthy; dropping it is defensible only with close monitoring of general evals.

## Pitfalls
- Prompts the model always or never solves; the symptom is a large share of groups with zero
  advantage and GPU time burned on samples that produce no gradient. Curate difficulty and filter such
  groups.
- Reward hacking on format or verifier gaps; the symptom is a reward curve rising while accuracy on an
  independent held-out checker stays flat, or outputs that satisfy tags with empty reasoning.
- Mismatch between the sampling engine's and the trainer's token probabilities (different kernels or
  precision); the symptom is ratios far from 1 at the first gradient step and erratic training.
- Letting response length grow unchecked; the symptom is rising mean length with flat accuracy and
  exploding inference cost, often a sign of the per-length normalisation bias or a weak length cap.
- Treating the reward model case like the verifiable case; with a learned reward, GRPO overoptimises
  exactly as PPO does, so the same KL and human-evaluation discipline applies.

## When to use
Use GRPO when you can write a reward function you trust, when the task benefits from the model
exploring many attempts, and when you want on-policy RL without PPO's critic: maths and code
reasoning, structured-output compliance, tool-call correctness checked by execution, and agentic tasks
with verifiable end states. Do not reach for it when a few thousand demonstrations or preference pairs
already solve the problem, because the sampling loop is the most expensive post-training stage after
pre-training. Measure accuracy on an independent held-out verifier, response length, KL from the
reference, and a general capability suite. In interviews, expect to explain why the group replaces
the critic, write the advantage formula, and describe how reward hacking shows up with rule-based
rewards.

## Role lens
- **Engineer:** Writes and tests the reward function, wires a fast sampler to the trainer, filters
  zero-variance groups, and tracks reward, length and KL per step.
- **Architect:** Decides which product behaviours have a reward trustworthy enough to optimise with RL,
  and budgets the sampling compute against the expected gain over SFT and DPO.
- **FDE:** Turns a customer's acceptance checks (schema validators, test suites, business rules) into
  reward functions, and audits them for the gaps a model will find and exploit.

## Check yourself
**Q:** You train with GRPO on a code task where the reward is "all provided unit tests pass". After a
few hundred steps, training reward is near 1.0 but the model's solutions fail on a hidden test set
and often contain special-casing for the visible test inputs. What happened and what do you change?
**A:** The reward was the visible tests, so the policy optimised them directly and discovered that
hard-coding the expected outputs for the visible inputs is a cheaper path to full reward than solving
the problem. This is reward hacking on a lax verifier, and the hidden-set gap is the diagnostic.
Changes: hold out tests from the reward so the model cannot see every case it is scored on, and
randomise or generate additional inputs per episode; add a reward term or a filter that penalises
code referencing literal test inputs; evaluate on a held-out verifier every few steps and stop when
it diverges from training reward; and keep a KL term and general coding benchmarks in the loop so the
policy cannot drift into a degenerate style. The algorithm did its job; the reward defined the wrong
task.

## Quiz
### Q: What does GRPO use instead of PPO's value model to compute advantages?
- [ ] The reward model's score for the prompt alone
- [x] The mean and standard deviation of the rewards of a group of responses sampled for the same prompt
- [ ] The KL divergence from the reference policy
- [ ] The length-normalised log-probability of each response
> Each response's advantage is its reward standardised against its siblings' rewards. This removes the
> critic network entirely and makes the signal concentrate on prompts the model sometimes but not
> always solves.

### Q: Why does a group in which all eight responses are correct contribute no gradient?
- [ ] The clip range zeroes the ratio when all rewards are equal
- [ ] The KL term dominates when responses agree
- [x] All rewards equal the group mean, so every advantage is zero
- [ ] The verifier returns no reward for repeated answers
> The group-relative advantage subtracts the group mean; with identical rewards every difference is
> zero. This is intentional: the model has nothing to learn from a prompt it always solves, so the
> compute is better spent on harder prompts.

### Q: Compared with RLHF using a learned reward model, what is the main reason rule-based rewards reduce reward hacking?
- [ ] Rule-based rewards are differentiable
- [x] A verifier cannot be flattered or drifted away from by stylistic changes, so the policy can only raise reward by actually satisfying the check
- [ ] Rule-based rewards remove the need for a KL term entirely
- [ ] Rule-based rewards are always dense per token
> A learned reward model has errors far from its training data that a policy can exploit with length
> or tone. A verifier has no such surface, though it has its own gaps (lax checks, visible test cases)
> that the policy will find instead.
