---
id: dpo
module: training-adaptation
name: DPO
kind: concept
difficulty: advanced
est_minutes: 17
status: reviewed
related:
  - training-adaptation/rlhf
  - training-adaptation/grpo
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/post-training
  - training-adaptation/overfitting
  - evals/pairwise-comparison
  - evals/llm-as-a-judge
  - ml-fundamentals/loss-functions
sources:
  - Rafailov et al., 2023 — Direct Preference Optimization: Your Language Model is Secretly a Reward Model
  - Ouyang et al., 2022 — Training language models to follow instructions with human feedback
  - Official docs: Hugging Face TRL
---

## TL;DR
DPO trains a model on preference pairs with a plain classification-style loss, no reward model and no
RL loop. It works because the KL-constrained objective RLHF optimises has a closed-form solution in
which the reward is a function of the policy itself, so the reward model can be substituted out of the
Bradley–Terry loss. You get most of RLHF's effect for the cost of an SFT run, but offline: the pairs
describe some earlier model's outputs, not the policy's current ones.

## Why it exists
RLHF with PPO delivers, but the machinery is heavy. Four models have to be resident, two of them
training; a generation loop dominates wall-clock time; PPO's hyperparameters (clip range, value loss
weight, KL coefficient, learning rate schedule) interact in ways that take experienced teams weeks to
stabilise; and the reward model is a separate artefact to train, validate and keep current. For a team
with ten thousand preference pairs and one node of GPUs, this is a mismatch between the size of the
problem and the size of the tool.

The obvious shortcut, fine-tuning on the chosen responses and ignoring the rejected ones, throws away
half the signal: the model learns what good looks like but nothing about what bad looks like, and it
cannot push probability *away* from the rejected answers. The next obvious shortcut, a contrastive loss
that raises chosen log-probability and lowers rejected log-probability, has no leash: the model can
satisfy it by driving the rejected sequence to zero probability, trashing everything nearby.

DPO's insight is that the leash and the comparison can be combined analytically. The RLHF objective,
maximise reward minus β times KL from the reference, has an exact optimum, and inverting it expresses
the reward in terms of the policy and reference log-probabilities. Substituting that expression into
the Bradley–Terry loss gives a loss on the policy alone that is the same optimisation problem RLHF
solves, with no reward model and no sampling. The leash is still there; it is just inside the formula.

## How it works
### The derivation in four lines
1. The KL-regularised objective `max_π E_y~π[r(x,y)] − β·KL(π ‖ π_ref)` is maximised by
   `π*(y|x) = π_ref(y|x)·exp(r(x,y)/β) / Z(x)`, where Z(x) normalises over all responses.
2. Solve for the reward: `r(x,y) = β·log(π*(y|x)/π_ref(y|x)) + β·log Z(x)`.
3. Put that into the Bradley–Terry probability that y_w beats y_l. The `β·log Z(x)` term is the same for
   both responses to the same prompt, so it cancels in the difference.
4. The resulting loss over the policy θ is:

```
L_DPO(θ) = − E [ log σ( β·log π_θ(y_w|x)/π_ref(y_w|x)  −  β·log π_θ(y_l|x)/π_ref(y_l|x) ) ]
```

Each log-ratio term is the *implicit reward* `r̂(x,y) = β·log(π_θ(y|x)/π_ref(y|x))`: how much more the
policy likes this response than the reference does, scaled by β. The loss wants the implicit reward
margin between chosen and rejected to be large and positive.

### What the gradient does
Differentiating gives `−β·σ(r̂_l − r̂_w)·[∇log π_θ(y_w|x) − ∇log π_θ(y_l|x)]`. The bracket raises the
chosen sequence's likelihood and lowers the rejected one's; the weight in front is the sigmoid of how
wrong the current implicit reward ranking is. Pairs the model already ranks correctly with a large
margin contribute almost nothing; pairs it gets backwards contribute most. That weighting is what the
naive contrastive loss lacks and what stops DPO from endlessly pushing already-separated pairs.

### The role of β
β is the same KL coefficient as in RLHF, read the other way round: it scales how large a log-ratio is
needed to achieve a given preference probability. Small β (TRL's default is 0.1) lets the policy move
far from the reference to separate pairs; large β keeps it close. There is no explicit KL term, but the
reference log-probabilities appear in every pair, so drift is penalised implicitly.

### Sequence log-probabilities
Each term is the sum of per-token log-probabilities over the response tokens only, with prompt tokens
masked. The reference model is frozen, so its log-probs for the dataset can be computed once and
cached, which brings the training memory down to one trainable model plus stored numbers, versus
RLHF's four networks. A common trick when fine-tuning with LoRA is to disable the adapter for the
reference forward pass instead of loading a second copy.

### Why "offline" matters
DPO uses whichever pairs you give it. If the chosen and rejected responses came from a different model
than the one being trained, the loss is comparing sequences the policy would rarely generate, and the
gradient's effect on the policy's actual outputs is indirect. This is the distribution-shift cost of
dropping the sampling loop. The reference matters here too: the derivation treats π_ref as the
distribution the pairs live near, so when you only have someone else's pairs, the DPO paper's advice is
to first SFT the reference on the chosen responses so it is close to the data. The better fix is to
generate the candidate responses with the policy being trained (or its SFT ancestor), label those, and
iterate in rounds (often called iterative or online DPO), which recovers much of on-policy RLHF's
advantage while keeping the simple loss.

### Known failure: likelihood displacement
The loss only cares about the *difference* in log-ratios. A model can increase the margin by reducing
the probability of both chosen and rejected responses, as long as the rejected one falls faster. In
practice chosen log-probability often does decrease during DPO, and the probability mass goes
somewhere unobserved, sometimes to worse responses. Monitoring chosen log-probability, not just the
margin, catches this; mixing a small SFT term on the chosen responses or switching to a variant are the
usual mitigations.

### The variants, and what each one changes
- **IPO** replaces the log-sigmoid with a squared loss that pulls the margin toward a fixed target
  instead of toward infinity, because with near-deterministic preferences DPO's loss keeps rewarding
  ever-larger margins and overfits.
- **KTO** needs no pairs: each response is labelled simply desirable or undesirable, which fits
  thumbs-up / thumbs-down product feedback that never comes as matched comparisons.
- **ORPO** drops the reference model and adds an odds-ratio penalty to an ordinary SFT loss, folding SFT
  and preference training into one stage.
- **SimPO** also drops the reference and uses the *length-normalised* average log-probability as the
  implicit reward, with a target margin, which directly attacks the length bias of summed log-probs.
All four keep DPO's shape: an offline loss on preference data with no reward model and no sampling.
Which one wins is data-dependent, so treat them as ablations to run, not upgrades to assume.

## Example
The DPO loss on sequence log-probabilities, with the reference log-probs precomputed. Shapes are
`[batch]` after summing per-token log-probs over response tokens; the helper shows the masked sum.

```python
import torch, torch.nn.functional as F

def seq_logprob(logits, ids, response_mask):
    """Sum of log p(token) over response tokens. logits [B,T,V], ids [B,T], mask [B,T] (1 on response)."""
    lp = torch.log_softmax(logits[:, :-1].float(), -1).gather(-1, ids[:, 1:, None])[..., 0]
    return (lp * response_mask[:, 1:]).sum(-1)                          # [B]

def dpo_loss(pol_w, pol_l, ref_w, ref_l, beta=0.1):
    """pol_*/ref_*: sequence log-probs of chosen (w) and rejected (l) under policy / reference."""
    margin = beta * ((pol_w - ref_w) - (pol_l - ref_l))                 # implicit reward margin
    loss = -F.logsigmoid(margin).mean()
    reward_acc = (margin > 0).float().mean()                            # fraction of pairs ranked right
    return loss, margin, reward_acc

# One pair, numbers illustrative. Policy has drifted toward chosen and away from rejected.
pol_w, ref_w = torch.tensor([-40.0]), torch.tensor([-42.0])            # chosen: +2 nats vs reference
pol_l, ref_l = torch.tensor([-35.0]), torch.tensor([-33.0])            # rejected: -2 nats vs reference
loss, margin, acc = dpo_loss(pol_w, pol_l, ref_w, ref_l)
print(f"margin={margin.item():.2f} loss={loss.item():.3f} reward_acc={acc.item():.0%}")
# margin = 0.1 * (2 - (-2)) = 0.40 ; loss = -log sigma(0.40) = 0.513 ; accuracy 100%

# Watch during training: loss falling, reward_acc rising toward ~0.7-0.9 on a held-out set,
# and chosen log-prob (pol_w) NOT falling steadily. If pol_w drops while margin grows,
# probability mass is leaving both responses: likelihood displacement.
```

Note the scale: a 4-nat total swing in log-probability across responses of dozens of tokens is a small
per-token change, yet with β = 0.1 it already yields a 60/40 preference. β is what turns modest
log-probability shifts into decisive preferences, and lowering it lets the model move further.

## Trade-offs
- One trainable model and a cached reference instead of four networks makes DPO cheap and stable, but
  it is offline, so its advantage shrinks when the pairs were generated by a model far from the one you
  are training.
- A small β separates pairs faster and allows larger behaviour changes, while a large β preserves the
  reference; the right value flips toward large β when the pairs are noisy or off-policy.
- Dropping the reward model removes an artefact to maintain, but also removes a reusable scorer for
  rejection sampling, data filtering and monitoring, which matters once you run many rounds.
- Variants that drop the reference (ORPO, SimPO) save memory and can reduce length bias, but give up
  the explicit leash and need their own margin or length terms to stay stable.

## Pitfalls
- Training for several epochs on a small pair set; the symptom is reward accuracy near 100% on
  training pairs, falling held-out win rate, and verbose or strangely confident outputs.
- Ignoring chosen log-probability; margin and loss look healthy while the policy's likelihood of good
  responses falls, and the mass migrates to unobserved outputs, often degenerate ones.
- Using pairs from a stronger, different model as if they were on-policy; the loss moves but the
  trained model's actual outputs barely change, because it never produced sequences like those.
- Length-confounded pairs where the chosen response is usually longer; the model learns length, which
  shows up as answers growing every round with no quality gain.
- Mismatched tokenisation or chat template between the stored reference log-probs and the policy's
  inputs; the implicit rewards are then noise and training appears to work while nothing improves.

## When to use
DPO is the default first preference-optimisation method for an application team: you have a few
thousand to a few hundred thousand pairs, one node, and a need for reproducible runs. Use it after
SFT, on pairs generated by the SFT model or by the policy from the previous round, and iterate in
rounds if you can afford fresh labelling. Prefer RLHF with PPO or GRPO when you need on-policy
exploration, a reusable reward model, or a programmatic reward, and when you have the engineering to
run the loop. Measure held-out reward accuracy, chosen log-probability, judge or human win rate against
the SFT model, and a general capability suite. In interviews, be ready to sketch the derivation, name
the implicit reward, and explain why DPO is offline and what that costs.

## Role lens
- **Engineer:** Precomputes reference log-probs, verifies the response mask and chat template match,
  and plots margin, chosen log-prob and held-out reward accuracy together each run.
- **Architect:** Picks DPO over PPO when pair data is fixed and budgets are modest, and sets the
  iteration policy: how often fresh on-policy pairs are collected before the next round.
- **FDE:** Sets up pairwise review with a customer's experts on the model's own outputs, since pairs
  about the model's actual mistakes are worth far more than imported preference datasets.

## Check yourself
**Q:** A team runs DPO for three epochs on 8,000 pairs. Training loss falls to near zero and
reward accuracy is 99%, but a held-out judge prefers the SFT model and outputs have become long and
oddly phrased. Diagnose and propose fixes.
**A:** The run overfit the pairs: three epochs on 8k pairs lets the model separate every training pair
by memorising surface features, and length is the easiest such feature if chosen responses tend to be
longer. The near-zero loss means pairs are separated by huge margins, which with β = 0.1 implies large
log-ratio shifts from the reference, and that drift is what the held-out judge is penalising. I would
check chosen log-probability over training for likelihood displacement and check the length ratio of
chosen to rejected in the data. Fixes: one epoch with early stopping on held-out reward accuracy,
larger β or an added SFT term on chosen responses, balancing or length-normalising the pairs, and
regenerating pairs on-policy so they describe this model's real errors rather than the dataset's
artefacts.

## Quiz
### Q: Why does the partition function Z(x) disappear from the DPO loss?
- [ ] It is assumed to equal one for a well-normalised language model
- [x] It depends only on the prompt, so it is identical for the chosen and rejected responses and cancels in their difference
- [ ] It is absorbed into the learning rate
- [ ] The reference model is trained to make it constant
> Z(x) sums over all possible responses to the prompt, which is intractable, but Bradley–Terry only
> uses the difference of rewards for two responses to the same prompt, and the same Z(x) appears in both.

### Q: What is the "implicit reward" in DPO?
- [ ] The output of a frozen reward model used to label the pairs
- [x] β times the log-ratio of policy to reference probability for a response
- [ ] The number of pairs the policy ranks correctly
- [ ] The negative of the KL divergence between policy and reference
> Inverting the optimal policy of the KL-regularised objective gives r(x,y) = β·log(π/π_ref) up to a
> prompt-only constant. DPO optimises the margin of this quantity between chosen and rejected responses.

### Q: Which observation most directly indicates likelihood displacement during DPO?
- [ ] Reward accuracy on training pairs stays below 60%
- [ ] The margin between chosen and rejected stops increasing
- [x] The chosen response's log-probability falls steadily even as the margin grows
- [ ] The reference model's log-probabilities change between epochs
> The loss rewards a growing difference, which can be achieved by lowering both responses' probability
> as long as the rejected one falls faster. Falling chosen log-probability means mass is moving to
> sequences the loss never sees.
