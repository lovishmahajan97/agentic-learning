---
id: knowledge-distillation
module: training-adaptation
name: Knowledge distillation
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/synthetic-data-generation
  - training-adaptation/pruning
  - training-adaptation/quantization
  - model-internals/small-language-models
  - model-internals/logits
  - system-design/slm-vs-llm-trade-offs
  - evals/model-evaluation
sources:
  - Hinton, Vinyals & Dean, 2015 — Distilling the Knowledge in a Neural Network
  - Kim & Rush, 2016 — Sequence-Level Knowledge Distillation
  - Sanh et al., 2019 — DistilBERT, a distilled version of BERT
  - Agarwal et al., 2023 — On-Policy Distillation of Language Models: Learning from Self-Generated Mistakes
---

## TL;DR
A large model knows more than its top-1 answer shows: the full next-token distribution encodes which
alternatives are plausible and which are absurd. Knowledge distillation trains a smaller student to match a
larger teacher's outputs, either its soft probability distributions or its generated text, instead of only
the one-hot labels in the data. Each example then carries far more signal, which is why a distilled model
usually beats the same architecture trained on the labels alone.

## Why it exists
The model that scores best is usually the one you cannot afford to serve. A 70B-class model is accurate
but costs roughly ten times the GPU time per token of a 7B one, and the obvious fix, "train a 7B model on
the same data", loses a lot of that accuracy. Part of the loss is capacity, but a surprising part is the
training signal. A one-hot label says "the next token is ` Paris`" and nothing else. The large model's
output distribution says ` Paris` 0.9, ` the` 0.05, ` located` 0.02, ` Berlin` 0.0001, and that ranking of
the wrong answers is itself knowledge about the world and the language. Hinton called it *dark knowledge*:
information in the relative probabilities of the non-argmax classes that the hard label throws away.

Distillation keeps that information. Instead of asking the student to reproduce the dataset, it asks the
student to reproduce the teacher's function, and the teacher's function is smoother, lower-variance and
available for *any* input, labelled or not. That last property matters as much as the first: you can run
the teacher on millions of unlabelled prompts from your actual traffic, which you could never pay humans to
label, and train the student on all of it.

Why not just compress the teacher with quantization or pruning? Those keep the architecture and shave a
constant factor. Distillation lets you change the architecture entirely, fewer layers, narrower width, a
different family, and it combines with both: a common recipe for a small model today is prune a big
one, then distil from the original to repair it.

## How it works
### Classic logit distillation
The teacher and student each produce logits **z** over the vocabulary (or classes). Soften both with a
temperature *T*:

```
p_i(T) = exp(z_i / T) / Σ_j exp(z_j / T)
```

At *T* = 1 a confident teacher's distribution is almost one-hot, so the information in the small
probabilities is invisible. Raising *T* flattens the distribution and amplifies the ratios between the
wrong answers. For teacher logits [6, 2, 1], *T* = 1 gives [0.976, 0.018, 0.007]; *T* = 4 gives
[0.605, 0.222, 0.173]. Both preserve the *ranking* of the wrong answers (the ratio p₂/p₃ = e^(1/T) is
compressed from about 2.7 to 1.3 but never inverts), yet at *T* = 1 those two classes hold 2.5% of the mass
and produce almost no gradient; at *T* = 4 they hold 40% of it, so the student actually gets to learn the
ranking. Too high a *T* has the opposite failure: everything approaches uniform and the signal about
which alternative is *more* plausible washes out, which is why small values (roughly 1–4 for LLMs) are the
usual search range rather than a rule.

The loss mixes two terms:

```
L = α · T² · KL( p_teacher(T) ‖ p_student(T) )  +  (1 − α) · CE( y, p_student(1) )
```

The *T²* factor is there because the gradient of the soft term scales roughly as 1/*T²*; multiplying it
back keeps the relative weight of the soft and hard terms stable when you change *T*. The hard-label term
is kept because the teacher is sometimes wrong, and the ground truth, where you have it, corrects that. Hinton
also showed that in the high-*T* limit matching softened distributions becomes matching logits directly
(a mean-squared error on **z**), which is why "logit distillation" and "soft-target distillation" are
used interchangeably.

### Three regimes for language models
1. **White-box, token-level.** Teacher and student share a tokenizer. For every position of a training
   sequence the student minimises a divergence between its next-token distribution and the teacher's. Per
   token the signal is a distribution over 100k+ vocabulary entries instead of one index, so the student
   learns far more per sequence. The cost is a teacher forward pass for every training sequence; in
   practice teams precompute the teacher's top-*k* logits (say 64 per position, renormalised) and store
   them, because full distributions would be hundreds of kilobytes per token. Several recent small models
   were pretrained this way with a larger sibling's logits as targets rather than from hard tokens alone.
2. **Black-box, sequence-level.** You only have the teacher's text. Generate responses for a large set of
   prompts, then run ordinary supervised fine-tuning on (prompt, teacher response) pairs. This is what
   "distilled" means in most product announcements, and technically it is SFT on synthetic data. The
   distillation framing changes what you optimise: which prompts cover the deployment distribution, how
   many samples per prompt, and whether you filter samples by a verifier (rejection sampling) so the
   student learns the teacher's correct reasoning rather than its average output. Reasoning-model
   distillates released in 2025 were made this way, fine-tuning open 1.5B–70B checkpoints on hundreds of
   thousands of teacher-generated traces.
3. **On-policy.** Both regimes above train the student on prefixes the *teacher* wrote. At inference the
   student sees its own prefixes, including its own mistakes, and has never learned to recover from them
   (exposure bias). On-policy distillation samples sequences from the student, has the teacher score every
   token of those sequences, and trains the student toward the teacher on its own trajectories. It costs
   generation inside the training loop and a live teacher, but it attacks the exact train/inference
   mismatch that sequence-level distillation leaves open.

### Which divergence
The forward KL(teacher ‖ student) is mode-covering: it punishes the student for assigning near-zero
probability to anything the teacher finds plausible, so a small student spreads its mass thinly over
every option and samples from it look hedged and low quality. The reverse KL(student ‖ teacher) is
mode-seeking: it lets the student concentrate on the teacher's dominant modes and ignore the rest, which
suits a student with less capacity than the teacher. Methods such as MiniLLM use reverse KL and
generalised-KD interpolates between the two with a Jensen–Shannon style divergence. Treat this as a
tunable, and remember that whichever you choose, the student's ceiling on the covered distribution is the
teacher.

### Feature (intermediate-layer) distillation
Outputs are not the only thing to match. When the student is architecturally close to the teacher, you can
add losses that pull the student's hidden states (through a learned projection if widths differ) or
attention maps toward the teacher's at chosen layers; DistilBERT added a cosine loss on hidden states to
its soft-target loss. This gives every layer a direct target instead of only the last one, which speeds
up recovery, but it needs white-box access to both models and a layer mapping (student layer *i* ↔
teacher layer *2i*, say), so it is used mostly when the student was built by shrinking the teacher.

### Why it pairs with pruning
A student initialised randomly must relearn everything. A student initialised by structurally pruning
the teacher (dropping layers, heads or MLP width) already sits near a good solution, and distillation
provides a dense correction signal, so the pair recovers most of the teacher's quality with a small
fraction of the tokens pretraining would need. That is the recipe behind several open small-model
families and the reason the two topics sit next to each other in this module.

## Example
A token-level distillation loss for a causal LM in PyTorch. Both models must use the same tokenizer so
vocabulary positions line up; the teacher is frozen. The `T²` scaling and the mixed hard/soft objective
follow Hinton's formulation; padded positions are masked out of the soft term.

```python
import torch
import torch.nn.functional as F

def kd_loss(student_logits, teacher_logits, labels, T=2.0, alpha=0.5, ignore_index=-100):
    """student/teacher logits: [batch, seq, vocab]; labels: [batch, seq], a copy of input_ids with
    ignore_index at masked positions (HF convention: unshifted, the shift happens below)."""
    s = student_logits[:, :-1].float()          # position t predicts token t+1
    t = teacher_logits[:, :-1].float()
    y = labels[:, 1:]
    mask = (y != ignore_index).float()
    # soft term: KL(teacher || student) at temperature T, scaled by T^2
    log_p_s = F.log_softmax(s / T, dim=-1)
    p_t = F.softmax(t / T, dim=-1)
    soft = F.kl_div(log_p_s, p_t, reduction="none").sum(-1)      # [batch, seq-1]
    soft = (soft * mask).sum() / mask.sum() * (T * T)
    # hard term: ordinary next-token cross-entropy against the ground-truth tokens
    hard = F.cross_entropy(s.reshape(-1, s.size(-1)), y.reshape(-1), ignore_index=ignore_index)
    return alpha * soft + (1 - alpha) * hard

# one training step; `teacher` is in eval mode with requires_grad disabled
with torch.no_grad():
    t_out = teacher(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"])
s_out = student(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"])
loss = kd_loss(s_out.logits, t_out.logits, batch["labels"], T=2.0, alpha=0.7)
loss.backward(); optimizer.step(); optimizer.zero_grad()
# For sequence-level distillation there is no kd_loss at all: generate teacher responses offline,
# filter them with a verifier, and run plain SFT on the survivors.
```

## Trade-offs
- The student gains accuracy per parameter, but you pay a teacher forward pass over the whole training set,
  plus storage if you cache logits; for sequence-level distillation that bill is teacher generation, which
  is the expensive direction.
- Token-level distillation carries far more signal per example but requires a shared tokenizer and access
  to logits; black-box distillation works across model families and APIs but transfers less per example and
  copies the teacher's mistakes verbatim.
- Off-policy sequence distillation is simple and embarrassingly parallel; on-policy distillation fixes
  exposure bias but needs generation inside the loop and a teacher available at training time, which
  roughly turns a data-processing job into an RL-shaped one.
- A distilled student is strong on the distribution the prompts covered and degrades faster than the
  teacher outside it; this is fine for a narrow product surface and a problem for a general assistant.
- Distilling from a third-party API may be forbidden by its terms of service for training competing
  models; check before building a pipeline around it.

## Pitfalls
- Leaving *T* = 1 and α high: the soft targets are nearly one-hot, so the student learns nothing beyond
  SFT; the symptom is a distilled model that matches the plain fine-tuning baseline exactly.
- Distilling across tokenizers with a naive logit loss: the vocabularies do not align, so the KL is
  meaningless; the symptom is a loss that plateaus high or goes NaN. Align tokens or fall back to
  sequence-level distillation.
- Training on unfiltered teacher generations: the student inherits hallucinations and verbosity; the
  symptom is a student that is confidently wrong in exactly the teacher's way. Filter with a verifier or
  rejection sampling first.
- Measuring success as agreement with the teacher on training prompts rather than task metrics on held-out
  real traffic; the symptom is a high "fidelity" number and unhappy users.
- Choosing the biggest available teacher regardless of the gap: a student far smaller than its teacher can
  learn *less* from it than from a mid-sized intermediate (the teacher-assistant effect); the symptom is a
  larger teacher producing a worse student.
- Forgetting general data in the mix: distilling a narrow capability into a small instruct model erodes
  its other skills; the symptom shows on a general benchmark run before and after.

## When to use
Reach for distillation when you have a strong model that is too slow or costly for the deployment, a large
supply of prompts (labelled or not) representing real usage, and a narrower target surface than the
teacher's. It is the right tool for turning a frontier model's behaviour on *your* task into a small model
you can host on one GPU, and for building reasoning-capable small models from a reasoning-capable large
one. Avoid it when the product needs the teacher's full breadth, or when you cannot evaluate the student
on real held-out data. Measure the student against the teacher on your task metric, the latency and cost
per thousand requests, and a general benchmark for collateral damage. In interviews, be ready to explain
dark knowledge and the *T²* factor, say when sequence-level distillation is just SFT on synthetic data, and
argue why on-policy distillation exists.

## Role lens
- **Engineer:** Builds the pipeline that runs the teacher over prompt logs, stores top-*k* logits or filtered
  generations, and trains the student with a temperature-scaled loss, verifying gains on held-out tasks.
- **Architect:** Decides which capabilities justify a frontier model and which should be distilled into a
  small hosted one, and owns the policy on which teachers are legally and contractually usable.
- **FDE:** Distils a customer's expensive prompt-chain into a small model trained on their own traffic
  inside their environment, so the solution survives their cost and data-residency constraints.

## Check yourself
**Q:** A team runs a 70B model for ticket triage plus a short drafted reply, at a cost they cannot sustain.
They want an 8B replacement. Lay out how you would distil it and what could go wrong.
**A:** Collect several hundred thousand real tickets from logs, deduplicated and scrubbed of PII, and run
the 70B teacher over them to produce triage labels and drafts. Filter: drop drafts failing a format check
and, where ground-truth triage labels exist from agents, drop teacher outputs that disagree. If both models
share a tokenizer, store the teacher's top-64 logits per position and train the 8B student with a
temperature-scaled KL plus cross-entropy; otherwise run SFT on the filtered teacher text. Evaluate on a
held-out month of tickets against the teacher's metrics, not against the teacher's outputs. Risks: the
student copies teacher hallucinations if filtering is weak; it degrades on ticket types rare in the sample;
and on-policy problems appear in long drafts, which argues for a final on-policy distillation pass or at
least rejection sampling with the teacher as judge.

## Quiz
### Q: Why does Hinton's distillation loss multiply the soft-target term by T²?
- [ ] To penalise the student more heavily for confident mistakes
- [x] Because the soft-term gradient shrinks roughly as 1/T², so T² keeps its weight stable across temperatures
- [ ] To convert the KL divergence into a cross-entropy
- [ ] Because the teacher's logits are stored at temperature T
> Softening by T scales the logit gradients down by about 1/T². Without the correction, raising T to expose
> dark knowledge would also silently shrink the soft term relative to the hard-label term.

### Q: Which distillation regime requires the teacher and student to share a tokenizer?
- [ ] Sequence-level distillation on generated text
- [x] Token-level distillation that matches next-token distributions
- [ ] Rejection-sampled SFT on teacher outputs
- [ ] Any distillation from an API-only teacher
> Matching distributions position by position only makes sense if the vocabulary indices mean the same
> thing in both models. Text-based regimes transfer through tokens, so any tokenizer pair works.

### Q: What problem does on-policy distillation solve that sequence-level distillation leaves open?
- [ ] The teacher's outputs are too long to fit the student's context
- [ ] The student cannot see the teacher's logits
- [x] The student only ever trains on teacher-written prefixes and never learns to recover from its own errors
- [ ] The teacher must be re-run every epoch
> Training on teacher trajectories creates exposure bias: at inference the student conditions on its own
> imperfect prefixes. Sampling from the student and scoring with the teacher trains on exactly that situation.
