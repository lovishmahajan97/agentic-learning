---
id: rlhf
module: training-adaptation
name: RLHF
kind: concept
difficulty: advanced
est_minutes: 18
status: reviewed
related:
  - training-adaptation/dpo
  - training-adaptation/grpo
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/post-training
  - training-adaptation/data-labeling
  - evals/pairwise-comparison
  - evals/inter-annotator-agreement
  - evals/llm-as-a-judge
sources:
  - Christiano et al., 2017 — Deep Reinforcement Learning from Human Preferences
  - Stiennon et al., 2020 — Learning to Summarize from Human Feedback
  - Ouyang et al., 2022 — Training language models to follow instructions with human feedback
  - Gao et al., 2022 — Scaling Laws for Reward Model Overoptimization
---

## TL;DR
RLHF turns human judgements of "which answer is better" into a trained reward model, then uses
reinforcement learning (usually PPO) to push the language model toward responses that score highly,
while a KL penalty keeps it close to the supervised starting point. It exists because quality is easier
to compare than to write down, and because a learned reward can be optimised far beyond the examples
people could author. Its central hazard is that the policy learns to exploit the reward model's errors.

## Why it exists
After SFT a model answers in the right format, but for most prompts the demonstration it imitated is
one of many acceptable answers, and the model has no way to know that a confident wrong answer is worse
than a careful right one. Writing a perfect demonstration for every prompt is slow, and annotators'
own answers are often worse than what the model can already produce. Asking "which of these two is
better?" is a far cheaper and more reliable judgement: people agree with each other on comparisons much
more than they agree on what the ideal response is.

The problem is that a comparison is not a training target for next-token prediction. There is no
token sequence to maximise the likelihood of; there is only a scalar notion of goodness. That is the
shape of a reinforcement learning problem: the model is a *policy* that emits a response (an action
sequence), the environment returns a *reward*, and the objective is expected reward. Christiano et al.
showed the recipe in 2017 for control tasks: fit a reward model to human comparisons, optimise the
policy against it, and keep collecting comparisons on the new policy's behaviour. Stiennon et al. and
then InstructGPT applied it to language, and the result was the jump from "completes text" to "helps".

The naive alternative of optimising directly against human ratings is hopeless at RL's data appetite:
PPO scores every sampled response, which over a run is hundreds of thousands of samples or more,
far beyond what raters could label. The reward model is the amortisation trick: tens of thousands of
human comparisons train a model that can score every sample for free. The price is that the
reward model is wrong in places, and the policy will find those places.

## How it works
### Step 1: collect comparisons
Prompts are sampled from the target distribution. For each, the current policy generates two or more
responses; raters pick the better one, or rank several. Rankings are decomposed into pairs. Rater
guidelines, calibration sessions and agreement measurement are a large part of the engineering: a
reward model trained on inconsistent labels learns noise.

### Step 2: train the reward model
The reward model r_φ(x, y) is often the SFT model (or a smaller sibling, as in InstructGPT) with its
output head replaced by a scalar head, so it reads prompt and response and emits one number. It is trained with the Bradley–Terry pairwise
loss, which says the probability that y_w beats y_l is the sigmoid of their score difference:

```
L_RM(φ) = − E_(x, y_w, y_l) [ log σ( r_φ(x, y_w) − r_φ(x, y_l) ) ]
```

Only differences matter, so the reward scale is arbitrary; implementations often normalise it. The loss
is chosen because it directly models the data you have (comparisons) rather than absolute scores
nobody gave you. Starting the reward model from the SFT checkpoint is deliberate: it must judge the
kind of text the policy produces, and a model that already represents that distribution learns the
judgement from far fewer comparisons. Its quality is measured as accuracy on held-out comparison pairs,
and that number should be read against inter-annotator agreement on the same pairs: a reward model
cannot meaningfully beat the rate at which humans agree with each other.

### Step 3: optimise the policy with PPO
The objective is expected reward with a KL penalty to the reference policy π_ref (the SFT model):

```
J(θ) = E_x E_y~π_θ [ r_φ(x, y) − β · KL( π_θ(·|x) ‖ π_ref(·|x) ) ]
```

In practice the KL term is applied per token as a shaped reward (−β·[log π_θ(y_t|·) − log π_ref(y_t|·)])
and the reward model's score is added at the final token, because it judges the finished response and
says nothing about individual tokens. That creates a credit-assignment problem: which of 300 tokens
earned the score? PPO answers it with a learned *value model* V(s_t) that predicts the eventual return
from each prefix. The temporal-difference error `δ_t = r_t + V(s_{t+1}) − V(s_t)` says whether token t
made the outcome look better or worse than expected, and generalised advantage estimation sums these
errors with a decay, `A_t = Σ_k (γλ)^k · δ_{t+k}` (γ is usually 1 for text; λ trades bias against
variance). PPO then runs the standard loop: sample responses from the current policy, compute rewards,
compute advantages, and update the policy with the clipped surrogate objective
`min(ρ·A, clip(ρ, 1−ε, 1+ε)·A)` where ρ is the ratio of new to old token probabilities. The clip is
there because policy-gradient steps on sampled text are high-variance; it prevents any one batch from
moving the policy too far. InstructGPT additionally mixed in pre-training gradients ("PPO-ptx") to
limit the capability loss they called the alignment tax.

### Why the KL penalty is not optional
The reward model is accurate near the data it was trained on and increasingly wrong far from it. An
unconstrained policy climbs the reward surface into regions where the reward model's score is high
and the real quality is low: longer and longer answers, flattery, repeated keywords, an authoritative
tone regardless of correctness, or outright gibberish that happens to score well. Gao et al. measured this
*overoptimisation*: as the policy moves further from the reference (measured in KL), the proxy reward
keeps rising while the true reward peaks and then falls. β sets how far you let it go; it is tuned
against held-out human evaluation, not against the reward model.

### What is running during training
Four networks are involved: the policy (trained), the reference (frozen copy for the KL), the reward
model (frozen) and the value model (trained). Memory and orchestration are the practical burden: for a
large policy this is several copies of the weights plus optimiser state for two of them, plus a
generation loop that must be fast, because sampling dominates wall-clock time. This cost is the main
reason DPO and GRPO exist.

### Iteration and rejection sampling
Because the reward model is only trusted near its training data, production recipes iterate: after a
round of PPO, sample from the new policy, collect new comparisons, retrain the reward model, repeat.
A cheaper cousin is rejection sampling fine-tuning: sample k responses, keep the top-scored by the
reward model, SFT on those. It uses the same reward model with no RL machinery and is often the first
round before PPO.

### Where the comparisons come from
"Human feedback" is increasingly partly AI feedback. In RLAIF, a strong model ranks the pairs against a
written set of principles (the idea behind Constitutional AI), and humans label a smaller slice to
audit the judge. The rest of the pipeline is unchanged; what changes is that the judge's biases (for
length, for its own style) become the reward model's biases, so the audit slice is not optional.

## Example
The two numerical pieces that make RLHF concrete: the reward model's pairwise loss and the per-token
shaped reward that PPO optimises. Shapes are for one prompt with two candidate responses.

```python
import torch, torch.nn.functional as F

# --- Reward model training step: Bradley-Terry loss on one (chosen, rejected) pair ---
def rm_loss(score_chosen, score_rejected):
    # score_* : [batch] scalars from the reward head at the final token
    return -F.logsigmoid(score_chosen - score_rejected).mean()

s_w, s_l = torch.tensor([1.3]), torch.tensor([0.4])
print("RM loss:", rm_loss(s_w, s_l).item())          # 0.341 ; falls as the gap widens

# --- PPO reward shaping for one sampled response of T tokens ---
beta = 0.05
logp_policy = torch.tensor([-2.1, -0.4, -1.7, -0.9, -0.3])   # log pi_theta(y_t | x, y_<t)
logp_ref    = torch.tensor([-2.0, -0.6, -1.5, -0.9, -2.1])   # log pi_ref(y_t | x, y_<t)
rm_score    = 0.8                                            # r_phi(x, y) for the whole response

per_token_kl = logp_policy - logp_ref                        # positive where policy drifted up
rewards = -beta * per_token_kl                               # KL penalty at every token ...
rewards[-1] += rm_score                                      # ... plus the RM score at the end
print("shaped rewards:", rewards.tolist())                   # [0.005, -0.01, 0.01, 0.0, 0.71]
print("sequence KL estimate:", per_token_kl.sum().item())    # 1.7 ; watched as a drift gauge
# PPO then computes advantages from these rewards with a value model and applies the clipped
# surrogate objective to the policy's token log-probs. If the KL estimate climbs while held-out
# human win-rate stalls, beta is too low: the policy is optimising the reward model's mistakes.
```

The last token above carries a large KL penalty: its log-ratio is +1.8, meaning the policy puts about
six times (e^1.8) more mass on it than the reference did, so −β·1.8 ≈ −0.09 is subtracted from the
0.8 reward model score at that position. A few tokens where the policy has drifted far up is exactly
the pattern of a policy starting to exploit the reward model, and why per-token KL, not just the
final score, is logged during training. Note that this single-sample log-ratio can be negative on
individual tokens (tokens 1 and 3 above, where the policy moved down); only its sum or expectation
over a sequence estimates the KL, which is why the sequence-level number is the one watched.

## Trade-offs
- A learned reward model lets you optimise against millions of samples from tens of thousands of
  human labels, but it is wrong off-distribution, so the gain reverses once the policy drifts past the
  data it was trained on.
- On-policy PPO adapts to the policy as it improves and can reach behaviours no demonstration contains,
  at the cost of four models in memory, a sampling loop and fragile hyperparameters; DPO wins when the
  dataset is fixed and the budget is small.
- A low KL coefficient allows bigger behaviour changes and faster reward gains, while a high one
  preserves capability; the right value flips with how trustworthy the reward model is far from its
  data.
- Separate reward models for helpfulness and safety allow explicit trade-offs between them, but need
  a combination rule and twice the comparison data; a single model is simpler and wins when the two
  goals rarely conflict on your prompt distribution.

## Pitfalls
- Trusting the reward model's score as the metric of success; the symptom is rising proxy reward with
  flat or falling human win rate, the overoptimisation signature.
- Length and sycophancy bias in comparisons; raters prefer longer, more agreeable answers, so the
  policy learns verbosity and flattery, visible as answer length creeping up round over round.
- Noisy or inconsistent labels; low inter-annotator agreement produces a reward model that cannot
  separate good from bad, and PPO then optimises noise.
- Training the reward model on comparisons from an old policy and reusing it after the policy has
  moved; the symptom is a reward model that is confidently wrong on the new policy's typical outputs.
- Treating PPO's hyperparameters as defaults; a too-large clip range or learning rate shows up as
  sudden KL spikes and a collapse into repetitive text within a few hundred steps.

## When to use
Reach for RLHF-style training when demonstrations cannot express what you want, you can collect
reliable comparisons on your own prompt distribution, and you have the engineering capacity to run and
monitor an RL loop. For most application teams the practical path is DPO on the same comparison data,
or rejection sampling with a reward model, with full PPO reserved for cases where on-policy iteration
clearly pays. Whatever variant you run, measure with human or strong-judge win rates on held-out
prompts, track KL from the reference, and run a general capability suite to catch the alignment tax.
In interviews, be ready to draw the three stages, write the Bradley–Terry loss, explain why the KL
penalty exists, and describe reward overoptimisation and how you would detect it.

## Role lens
- **Engineer:** Implements comparison collection, trains and validates the reward model on held-out
  pairs, and watches KL, reward and win-rate curves together during PPO.
- **Architect:** Decides whether on-policy RL is justified over DPO or rejection sampling given the
  data, compute and evaluation budget, and owns the safety-versus-helpfulness reward policy.
- **FDE:** Runs the rater calibration with a customer's domain experts, measures agreement, and
  explains why the reward model, not the GPUs, is the component that decides what the model becomes.

## Check yourself
**Q:** During PPO, the reward model's average score on sampled responses rises steadily for a thousand
steps, but a weekly human evaluation shows win rate against the SFT model peaked at step 300 and has
since fallen. What is happening, how do you confirm it, and what do you change?
**A:** This is reward overoptimisation: the policy has moved into regions where the reward model
overestimates quality, so the proxy rises while real quality falls. I would confirm by plotting the KL
from the reference over steps and checking that the human win rate peaks at a particular KL and
declines beyond it, and by inspecting samples for the usual exploits, such as growing length,
flattery, or repeated phrasing. Remedies: raise the KL coefficient or stop at the KL where human
evaluation peaked; collect fresh comparisons on the current policy's outputs and retrain the reward
model so it is accurate where the policy now lives; consider a reward ensemble or a length penalty if
the exploit is identifiable. The deeper fix is institutional: never gate releases on the reward model's
own score.

## Quiz
### Q: Why does RLHF train a reward model instead of optimising the policy directly on human ratings?
- [ ] Human ratings are not differentiable, but a neural reward model is
- [x] RL scores far more sampled responses than raters could label; the reward model amortises tens of thousands of human comparisons over all of them
- [ ] PPO can only consume scalar rewards from a network of the same size as the policy
- [ ] Humans cannot compare responses reliably
> Policy-gradient methods are sample-hungry. A reward model turns a modest number of human comparisons
> into a function that can score every sampled response for free, which is the only way the RL loop
> is affordable. Differentiability is irrelevant: PPO does not backpropagate through the reward.

### Q: What does the Bradley-Terry loss −log σ(r(x,y_w) − r(x,y_l)) assume about the data?
- [ ] That each response has an absolute quality score from 0 to 1
- [ ] That rewards are normally distributed across prompts
- [x] That the probability one response beats another is a sigmoid of their score difference, so only differences matter
- [ ] That the chosen response is always correct
> The loss models comparisons, not absolute scores, which matches the data raters actually produce.
> Because only differences enter, the reward scale is arbitrary and implementations normalise it.

### Q: A team sets the KL coefficient β to zero to "let the model improve freely". What is the most likely result?
- [ ] Training diverges immediately because the gradients explode
- [ ] The model stops changing, because there is no penalty to push against
- [x] Proxy reward rises while real quality degrades into verbose, repetitive or sycophantic outputs that exploit the reward model
- [ ] The reward model is retrained automatically to compensate
> Without the leash the policy climbs the reward model's surface into regions far from its training
> data where its scores are unreliable. The classic exploits are length, flattery and degenerate text
> that happen to score well.
