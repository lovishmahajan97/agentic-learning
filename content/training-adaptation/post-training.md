---
id: post-training
module: training-adaptation
name: Post-training
kind: concept
difficulty: core
est_minutes: 14
related:
  - training-adaptation/pre-training
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/instruction-tuning
  - training-adaptation/rlhf
  - training-adaptation/dpo
  - training-adaptation/grpo
  - training-adaptation/catastrophic-forgetting
  - evals/model-evaluation
sources:
  - Ouyang et al., 2022 — Training language models to follow instructions with human feedback
  - Touvron et al., 2023 — Llama 2: Open Foundation and Fine-Tuned Chat Models
  - Lambert et al., 2024 — Tülu 3: Pushing Frontiers in Open Language Model Post-Training
---

## TL;DR
Post-training is everything done to a pre-trained base model to make it useful and safe: supervised
fine-tuning on demonstrations, preference optimisation (RLHF, DPO) on comparisons, and increasingly
reinforcement learning on verifiable rewards. It uses a tiny fraction of pre-training's compute but
decides almost everything a user experiences: whether the model answers, how it formats, what it
refuses, how it reasons and how it calls tools. The stages are ordered because each one needs the
behaviour the previous one installed.

## Why it exists
A base model is a very good autocomplete. Ask it a question and it may continue with more questions,
a forum thread, or an answer in the wrong register, because all are likely continuations of web text.
It has no notion of a "user" and an "assistant", no reason to be concise, no reason to decline a
harmful request, and no habit of showing its reasoning. The knowledge is there; the behaviour is not.

The first fix people tried was prompting: write a few examples of question-and-answer in the context
and let the base model pattern-match. It works surprisingly well, which is itself evidence that the
skills are latent, but it is fragile, eats context, and cannot teach the model anything that is not
already an obvious pattern (a consistent refusal policy, a house JSON format, calling a tool when
unsure). It also cannot express preferences: "this answer is better than that one" has no place in a
next-token loss.

Post-training solves this in layers. Demonstrations teach the format and the basic behaviour.
Comparisons teach the quality dimension that demonstrations cannot express, because for most prompts
there is no single correct answer, only better and worse ones. Reinforcement learning with checkable
rewards teaches behaviours that need many attempts to discover, such as multi-step reasoning. Each
layer exists because the previous one has a limit that only a different signal can get past.

## How it works
### Stage 1: supervised fine-tuning on demonstrations
Humans (or a stronger model) write prompt-response pairs in the target format: a system prompt, a
user turn, an assistant turn. The model is trained with the same next-token loss as pre-training, but
only on the assistant tokens. This installs the chat template, the assistant persona and the basic
"answer the question" behaviour. It is cheap and stable, which is why it comes first: later stages
need a policy that already produces plausible candidate answers. Its limit is that it can only imitate;
it cannot tell the model that one of two fluent answers is wrong. See *supervised fine-tuning* and
*instruction tuning* for the mechanics and data design.

### Stage 2: preference optimisation on comparisons
Raters see two or more responses to the same prompt and rank them. This data is cheaper to produce
than demonstrations and captures judgements people find easy to make but hard to write down. Two
families consume it. RLHF trains a reward model on the comparisons and then optimises the policy
against it with PPO, with a KL penalty that stops the policy drifting too far from the SFT model. DPO
skips the reward model and optimises the policy directly on the pairs with a loss that is
mathematically equivalent under the same KL constraint. The choice is an engineering one: RLHF is
on-policy and can keep improving as the policy changes; DPO is simpler, cheaper and offline. Both
raise the quality of answers the SFT model already produces and are where refusal behaviour and tone
are mostly shaped.

### Stage 3: reinforcement learning on verifiable rewards
For tasks with a checkable answer, such as maths with a known result, code with unit tests, or output
that must parse as JSON, the reward can be computed by a program rather than learned from humans.
The model samples many attempts per prompt, is rewarded for the correct ones, and is pushed toward
whatever reasoning got it there. GRPO is the common algorithm because it needs no value model. This is
the stage that produces long chain-of-thought reasoning behaviour in recent models, and it is the one
whose compute cost is closest to pre-training, because it generates far more tokens than it trains on.

### The recurring design constraints
- **A reference model and a KL leash.** Every stage after SFT penalises distance from the previous
  checkpoint, because an unconstrained optimiser finds degenerate outputs that score well on the reward
  and badly with users. The strength of that leash is the central hyperparameter.
- **Iteration.** Production recipes run several rounds: train, sample from the new model, collect new
  comparisons on those samples, train again. Off-policy data from an old model becomes less useful as
  the policy moves.
- **Evaluation per stage.** Each stage has its own failure modes (forgetting after SFT, verbosity after
  preference training, reward hacking after RL), so each gets its own gates: capability benchmarks to
  check nothing was lost, preference win rates, and task-specific checks.
- **Safety and policy.** Refusal behaviour, handling of sensitive topics and tool-use permissions are
  trained in with targeted SFT data and preference pairs, then red-teamed, because a model that is
  helpful but unsafe cannot ship.

### What post-training costs
Pre-training is measured in GPU-years. SFT on tens of thousands of examples is GPU-hours on one node;
DPO on a comparable set is similar; RLHF or GRPO is more, because sampling dominates, but still orders
of magnitude below pre-training. The expensive part of post-training is the data: demonstrations and
comparisons written or reviewed by people, and the evaluation sets that tell you whether a round helped.

## Example
A team takes an open-weight 8B base model and post-trains it as a support assistant for a software
product. All sizes are illustrative.

1. **SFT.** They write 6,000 demonstrations: real support tickets paired with answers authored by
   senior agents, rendered through a chat template with a fixed system prompt. After two epochs the
   model answers in the right voice and format. Evaluation on a held-out set shows it is fluent but
   invents feature names about one time in eight, and it answers "how do I delete my account" with a
   wall of text where the policy is a short, specific procedure.
2. **Preference optimisation.** For 2,000 held-out tickets they sample four responses each from the SFT
   model and have agents rank them, with a rubric that penalises invented features and rewards
   concision. DPO on the resulting 6,000 pairs cuts hallucinated feature names by more than half in
   the held-out set and shortens answers. A capability benchmark run before and after confirms the
   model's general question-answering has not regressed.
3. **Verifiable rewards.** Some answers must emit a JSON block that opens a ticket in the help-desk
   system. They write a reward function: +1 if the JSON parses and matches the schema, +1 if the ticket
   category is correct per a lookup table, 0 otherwise. A few hundred GRPO steps with 8 samples per
   prompt raise schema validity from 91% to above 99%, something the SFT data alone never achieved
   because the few malformed examples were drowned out.
4. **Iteration.** They collect a fresh batch of rankings on the *new* model's outputs, because the old
   pairs no longer describe its mistakes, and run one more DPO round before release.

The whole cycle costs a few hundred GPU-hours and several weeks of agent time, and the second number
is the one that dominates the budget.

## Trade-offs
- Demonstrations are precise but expensive and only teach imitation; comparisons are cheaper and
  express quality, but a poorly designed rubric teaches length and confidence instead of correctness.
- Offline methods (SFT, DPO) are simple and reproducible, while on-policy RL keeps learning as the
  model changes, at the price of a sampling loop, more hyperparameters and harder debugging.
- A strong KL leash preserves capabilities and prevents reward hacking but caps how far behaviour can
  move; loosen it when the target behaviour is far from the SFT model and evaluation can catch drift.
- Training safety and refusals in makes the model safer by default but raises over-refusal on benign
  edge cases; the right balance depends on the deployment's audience and whether external guardrails
  exist.

## Pitfalls
- Running preference training on a model that has not been SFT'd into the target format; the symptom
  is that both responses in every pair are off-distribution and the loss moves without the behaviour
  improving.
- Reusing stale comparison data across rounds; the pairs describe an old model's errors, so win rates
  on the training distribution rise while real-user complaints do not change.
- Measuring only the preference metric; a model can win more pairwise comparisons while losing points
  on reasoning or coding benchmarks, which is catastrophic forgetting hiding behind a good-looking
  number.
- Optimising a reward that is easy to compute rather than the one that matters; the symptom is
  outputs that satisfy the schema or the judge yet are useless to the person reading them.

## When to use
You post-train when prompting and retrieval cannot give you the behaviour: a consistent house format
that must hold on every response, a refusal or escalation policy that must be reliable, a domain style
that prompting keeps drifting from, or a multi-step skill that needs reward-driven practice. Start with
SFT because it is cheap and diagnostic; add preference optimisation when the failure is "fluent but
worse than it should be"; add verifiable-reward RL only when you can write a trustworthy reward
function. Measure before and after every stage with both task metrics and a general capability
suite. In interviews, be ready to name the three stages, say what signal each consumes, and explain why
the ordering is not arbitrary.

## Role lens
- **Engineer:** Builds the SFT and DPO pipelines, writes the reward functions and chat templates, and
  owns the before/after evaluation harness that gates each round.
- **Architect:** Decides whether the product needs post-training at all versus prompting and RAG, and
  sets the data, evaluation and safety gates a post-trained model must pass to ship.
- **FDE:** Turns a customer's ticket history and reviewer time into demonstrations and comparisons, and
  manages the expectation that the data effort, not the GPUs, is the schedule risk.

## Check yourself
**Q:** A colleague proposes skipping SFT and running DPO directly on the base model using the
preference pairs you already have, to save a stage. What goes wrong, and when could it work?
**A:** DPO adjusts the relative log-probabilities of chosen versus rejected responses against a
reference, and it assumes the policy already assigns reasonable probability to responses in that
format. A base model puts almost no mass on chat-formatted assistant turns, so the pairs are far
off-distribution: the loss can fall by making both responses even less likely in ways that generalise
poorly, and the model still does not reliably answer in the format. It could work if the pairs are in
plain completion format the base model already produces, or if the "base" is actually an
instruction-tuned checkpoint. In practice, a short SFT on the chosen responses first is cheap and makes
the DPO stage stable, so the saving is not worth it.

## Quiz
### Q: Why does post-training use comparisons between responses rather than only more demonstrations?
- [ ] Comparisons are the only data format PPO can consume
- [x] For most prompts there is no single correct answer, and ranking captures quality that imitation cannot express
- [ ] Demonstrations cause catastrophic forgetting while comparisons do not
- [ ] Comparisons require no human labour
> A demonstration says "produce this"; it cannot say "this is better than that". Preference data
> encodes the quality dimension directly, and people find ranking easier and cheaper than authoring
> ideal answers.

### Q: What role does the KL penalty against a reference model play in RLHF, DPO and GRPO?
- [ ] It speeds up convergence by reducing gradient variance
- [ ] It replaces the need for a separate evaluation set
- [x] It limits how far the policy can drift, preventing degenerate outputs that score well on the reward but read badly
- [ ] It ensures the model forgets the base model's unsafe behaviours
> An unconstrained optimiser will exploit any gap between the reward and true quality. Penalising
> divergence from the previous checkpoint keeps the policy in the region where the reward model or
> preference data is trustworthy and preserves learned capabilities.

### Q: In the support-assistant example, why did the JSON-validity problem need verifiable-reward RL rather than more SFT data?
- [ ] SFT cannot train on structured output
- [x] The rare malformed cases were drowned out in imitation data, while a programmatic reward targets exactly the failure
- [ ] DPO cannot be run after SFT
- [ ] RL is always cheaper than collecting demonstrations
> Imitation learning improves the average behaviour; a 9% failure rate persists because nothing in the
> loss singles it out. A reward computed by a schema check pushes directly on the failure, and sampling
> many attempts finds the behaviour that fixes it.
