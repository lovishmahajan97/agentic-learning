---
id: model-merging
module: training-adaptation
name: Model merging
kind: concept
difficulty: advanced
est_minutes: 15
status: reviewed
related:
  - training-adaptation/peft
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/catastrophic-forgetting
  - training-adaptation/tooling
  - evals/regression-tests
  - system-design/model-portfolio
  - llmops-platform/model-registry
  - safety-security/supply-chain-security-for-models-and-datasets
sources:
  - Wortsman et al., 2022 — Model soups: averaging weights of multiple fine-tuned models improves accuracy without increasing inference time
  - Ilharco et al., 2023 — Editing Models with Task Arithmetic
  - Yadav et al., 2023 — TIES-Merging: Resolving Interference When Merging Models
  - Yu et al., 2023 — Language Models are Super Mario: Absorbing Abilities from Homologous Models as a Free Lunch
---

## TL;DR
Model merging combines several fine-tuned checkpoints of one base model into a single set of weights by
arithmetic on the parameters: averaging, adding scaled task vectors, or trimming and sign-voting the
deltas. No training is involved. It works because fine-tunes that start from the same initialisation
stay in a connected low-loss region, so their deltas behave roughly additively. You get one model carrying
several skills at the serving cost of one, paying with task interference that no gradient step corrects.

## Why it exists
Suppose you have three fine-tunes of the same 8B base: one for your support domain, one that follows tool
call formats, and a community instruct tune with far better conversational quality than yours. Serving
all three means three copies of the weights, a router, and no way for one answer to use two skills at
once. Training one multi-task model instead means obtaining all three datasets (the community one is not
yours to have), rebuilding the mix every time one dataset changes, and paying for a training run per
iteration. Ensembling costs three forward passes per token.

Merging offers a fourth option: because every one of those models is `base + small delta`, you can add the
deltas and get a single model in minutes on a CPU. The reason this is not absurd is empirical and
well-replicated: models fine-tuned from the same pretrained weights stay in the same basin of the loss
landscape, so the straight line between them has no loss barrier (linear mode connectivity), and
averaging two good solutions tends to land on a third good one that is often better than either. Model
soups showed that averaging fine-tunes with different hyperparameters improved accuracy and
out-of-distribution robustness at zero inference cost. Task arithmetic showed the deltas behave like
vectors you can add, scale and even negate to remove a behaviour.

The naive version, plain averaging, breaks as soon as the tasks disagree about a weight: one tune pushes it
up, another pushes it down, and the average cancels both. The later methods are all answers to that
interference problem.

## How it works
### Task vectors
For a base θ_base and a fine-tune θ_i, the task vector is τ_i = θ_i − θ_base, computed tensor by tensor.
Task arithmetic forms θ = θ_base + λ Σ τ_i for multi-task merging, θ_base − λ τ for forgetting (negating a
toxicity fine-tune reduces toxicity), and even analogies. λ is a single scalar (or one per task) tuned on
a validation set; because the deltas are additive approximations, λ around 0.3–1.0 in total is where
things usually work, and larger sums push activations out of the range the base was trained in.

### Linear averaging and soups
θ = Σ w_i θ_i with Σ w_i = 1. Uniform soups average everything; greedy soups add a model only if the
validation metric improves. This is the right tool when the models learned the *same* thing in slightly
different ways (same data, different seeds or learning rates), and checkpoint averaging at the end of a
long training run is the same idea applied along one trajectory. It is the wrong tool when the models
learned different things.

### SLERP
Spherical linear interpolation treats each tensor (or the whole flattened model) as a vector and
interpolates along the arc between the two, with cos Ω = a·b / (‖a‖‖b‖) and

```
slerp(a, b, t) = sin((1 − t)Ω) / sin Ω · a  +  sin(tΩ) / sin Ω · b
```

The point is norm preservation: a straight-line midpoint between two vectors at an angle is *shorter* than
either, which shrinks activation scales layer by layer; SLERP keeps the magnitude. It only handles two
models at a time and is the usual choice for blending two closely related instruct tunes.

### TIES-Merging: trim, elect sign, disjoint merge
TIES names two sources of interference. First, most entries of a task vector are tiny noise that still
perturb other tasks when summed. Second, sign conflicts: when τ_A and τ_B disagree on direction, averaging
cancels useful movement. The method does three things, in order, because each removes one failure mode:

1. **Trim.** Keep only the top *k*% of each τ_i by magnitude (20% is a common setting); zero the rest.
   Fine-tuning changes concentrate in a few parameters, so this removes noise without losing much.
2. **Elect.** For each parameter, choose the sign carrying more total magnitude across tasks:
   sign(Σ_i τ_i) after trimming. This decides which direction the merged model will move.
3. **Disjoint merge.** Average only the task vectors that agree with the elected sign at that parameter,
   ignoring the dissenters instead of letting them cancel the majority. Then θ = θ_base + λ τ_merged.

A four-parameter walkthrough makes the effect visible. Take τ_A = [+0.30, −0.02, +0.10, −0.40],
τ_B = [−0.25, +0.01, +0.12, −0.05] and τ_C = [+0.05, 0.00, −0.08, −0.30]. The plain mean is
[+0.03, 0.00, +0.05, −0.25]: on parameter 1, A's strong push and B's strong pull have cancelled to almost
nothing. Trimming each vector to its top 50% (two entries) leaves A = [+0.30, 0, 0, −0.40],
B = [−0.25, 0, +0.12, 0] and C = [0, 0, −0.08, −0.30]. The summed signs are [+, 0, +, −], and the disjoint
means give τ_merged = [+0.30, 0, +0.12, −0.35]. Two lessons follow. Movement that the tasks agree on
survives at full strength rather than being divided by the number of tasks, which is why λ for TIES sits
near 1 rather than 1/N. And conflicts are not resolved, only decided: on parameter 1, B loses outright
because A's magnitude was larger, so B's skill pays for the merge wherever it depended on that weight.

### DARE: drop and rescale
DARE starts from the observation that SFT deltas are extremely redundant: you can set a random 90% (or
more, on large models) of a task vector's entries to zero, multiply the survivors by 1/(1 − p), and the
model's behaviour barely changes, because the rescaling keeps the expected delta identical and the
redundancy covers the dropped entries. Sparsified deltas from different tasks collide far less often, so
DARE is used as a preprocessing step before summing or before TIES ("dare_ties"). It works when deltas
are small relative to the base; it fails on continued-pretraining deltas, which are large and not
redundant.

### Passthrough and frankenmerges
Stacking layers from different models, or repeating a block of layers to make a deeper model (depth
up-scaling), is merging without arithmetic. The result is broken until it is healed with continued
training, so this is a model-building technique rather than a free combination step.

### Constraints that follow from the mechanism
Everything above is elementwise arithmetic on tensors, so it presumes the same architecture, the same
tensor shapes (hence the same tokenizer and vocabulary size) and the same base lineage. Two fine-tunes of
different base checkpoints have deltas measured from different origins; two models trained from different
random initialisations are related by hidden permutations of their neurons and average into noise unless
aligned first. LoRA adapters are task vectors restricted to a low-rank subspace, so adapter merging (a
weighted sum of B_iA_i products, with the same TIES/DARE options) is the same technique with the same
interference problems. And because nothing is trained, nothing is verified: every merge needs the full
evaluation suite, including the chat template, end-of-turn behaviour and safety checks, before anyone
calls it a model.

## Example
A compact TIES merge over Hugging Face state dicts in PyTorch. All checkpoints must share the base model
and tensor shapes. It works tensor by tensor, so peak memory is one tensor times the number of models, and
it falls back to copying non-float buffers unchanged.

```python
import torch

def ties_merge(base, finetunes, k=0.2, lam=1.0):
    """base, finetunes: state dicts (name -> tensor) of the same architecture and base lineage.
    Returns base + lam * TIES-merged task vector."""
    merged = {}
    for name, w0 in base.items():
        if not w0.is_floating_point():                       # token ids, buffers: pass through
            merged[name] = w0.clone(); continue
        taus = torch.stack([ft[name].float() - w0.float() for ft in finetunes])   # [n_tasks, ...]
        # 1. Trim: keep the top-k fraction of each task vector by magnitude
        flat = taus.abs().flatten(1)
        n_keep = max(1, int(k * flat.shape[1]))
        thresh = flat.kthvalue(flat.shape[1] - n_keep + 1, dim=1).values  # per-task cutoff
        taus = taus * (taus.abs() >= thresh.view(-1, *([1] * (taus.dim() - 1))))
        # 2. Elect: the sign with more total magnitude at each parameter wins
        sign = torch.sign(taus.sum(0))
        # 3. Disjoint merge: average only the entries that agree with the elected sign
        agree = (torch.sign(taus) == sign) & (taus != 0)
        tau = (taus * agree).sum(0) / agree.sum(0).clamp(min=1)
        merged[name] = (w0.float() + lam * tau).to(w0.dtype)
    return merged

# usage sketch (every path must be a fine-tune of exactly the same base revision):
# from transformers import AutoModelForCausalLM
# base = AutoModelForCausalLM.from_pretrained("org/base-8b").state_dict()
# fts = [AutoModelForCausalLM.from_pretrained(p).state_dict() for p in ("org/support-sft", "org/tools-sft")]
# model = AutoModelForCausalLM.from_pretrained("org/base-8b"); model.load_state_dict(ties_merge(base, fts))
# Then: run the per-task evals, a general benchmark, the chat-template smoke test and the safety suite.
# mergekit expresses the same recipe as a YAML config (merge_method: ties, density: 0.2, weight: ...).
```

## Trade-offs
- Zero training compute and no access to the original datasets, but also zero training signal: nothing
  guarantees the merged model works, so evaluation effort replaces training effort; when you do own the
  data and compute, multi-task SFT is the more reliable path.
- One model for several skills saves GPUs and routing complexity, but interference grows with the number
  of tasks and the size of their deltas; past a handful of tasks, per-task quality usually slips below the
  specialists.
- Plain averaging improves robustness when models are near-duplicates of each other, but it cancels
  conflicting updates; TIES and DARE fix that at the cost of hyperparameters (k, p, λ) that need a
  validation set to tune.
- SLERP preserves norms and often beats linear interpolation for two models, but does not extend to many
  models; linear and TIES scale to N at the cost of norm drift.
- Task-vector negation can remove unwanted behaviour cheaply, but the same mechanism can strip safety
  tuning, intentionally or by accident, which makes merged models a governance problem as much as a
  technical one.

## Pitfalls
- Merging fine-tunes of different base revisions, or treating an instruct model as the base for deltas
  computed against the pretrained one: the symptom is incoherent or garbage output, because the deltas
  are measured from the wrong origin.
- Vocabulary mismatch from added special tokens: embedding and output-head shapes differ, so the symptom
  is a shape error, or, if someone pads the tensors, wrong tokens at generation time.
- Weights that sum well above one, or λ too high: the symptom is repetitive, degenerate text and logits
  that blow up, because activations leave the range the base model expects.
- Each parent learned its own chat template: the merged model gets a mixture, so the symptom is leaked
  role tags, never emitting end-of-turn, or ignoring the system prompt.
- Trusting leaderboard scores for merges: benchmark contamination accumulates across parents, so the
  symptom is a model that tops a public board and underperforms its parents on your tasks.
- Skipping the safety regression: merging can quietly undo refusal training; the symptom is a model that
  complies with requests every parent refused, discovered by a user rather than by a test.

## When to use
Merge when you have several fine-tunes of the same base and need one artifact, when you want to borrow a
community tune's conversational quality for your domain model without its data, when you are averaging
checkpoints or seeds from your own run, or when you want a fast experiment before committing to a
multi-task training job. Do not merge as a substitute for training when you own the data, and never ship a
merge that has not been through the per-task evals, a general benchmark, a template smoke test and the
safety suite. Measure each parent's task metric before and after, and treat any drop as the cost of the
merge, not noise. In interviews, be ready to explain task vectors, why TIES elects a sign, why DARE's
rescaling preserves behaviour, and why two models from different initialisations cannot simply be averaged.

## Role lens
- **Engineer:** Computes task vectors, tunes density and λ on a validation set, and runs the full eval and
  safety suite on the merged checkpoint before registering it like any trained model.
- **Architect:** Decides whether a merged generalist or routed specialists better fit the serving budget and
  risk posture, and sets provenance rules for which parent checkpoints may be merged into production.
- **FDE:** Combines a customer's domain fine-tune with a stronger instruct tune when retraining is not an
  option on site, and proves the result against the customer's own regression suite.

## Check yourself
**Q:** A team has a support-domain SFT of an 8B base and wants the conversational quality of a newer
community instruct tune built on the same base. What are their options, and how would you merge safely?
**A:** Options are routing between the two models, retraining on the instruct data (unavailable), or
merging. For merging, first confirm both checkpoints derive from the exact same base revision and share
the tokenizer and vocabulary size. Compute both task vectors against the base, then try TIES with density
around 0.2 and λ near 1, and SLERP between the two as a second candidate. Tune on a validation set holding
support tickets and general chat prompts. Evaluate per-task metrics against each parent, a general
benchmark for collateral damage, a chat-template smoke test for end-of-turn behaviour, and the safety
suite, because merging can erode refusals. Ship only if support quality holds within tolerance and the
conversational gain is measurable; otherwise fall back to routing.

## Quiz
### Q: Why does TIES-Merging elect a sign per parameter before averaging?
- [ ] To make the merged weights sparse enough for fast kernels
- [x] Because task vectors that disagree in direction cancel under plain averaging, so only the majority direction is kept
- [ ] Because negative deltas are usually noise
- [ ] To normalise each task vector to unit length
> Sign conflicts are a main source of interference: averaging +0.3 and −0.3 yields zero and loses both
> tasks' progress. Electing the dominant sign and averaging only agreeing entries preserves movement.

### Q: DARE drops 90% of a task vector's entries and rescales the rest by 10×. Why does the model still work?
- [ ] Because 90% of fine-tuned weights are exactly zero already
- [ ] Because the base model ignores small deltas
- [x] Because SFT deltas are highly redundant and rescaling keeps the expected delta unchanged
- [ ] Because dropped entries are recovered during the first inference pass
> Random dropping with 1/(1 − p) rescaling is unbiased, and small fine-tuning deltas carry redundant
> information, so the behaviour survives; the method fails on large, non-redundant continued-pretraining deltas.

### Q: Which pair can be safely merged by weight averaging?
- [ ] Two 7B models of the same architecture trained from different random initialisations
- [x] Two fine-tunes that started from the identical pretrained checkpoint
- [ ] A 7B and a 13B model from the same family
- [ ] Any two models that share a tokenizer
> Linear mode connectivity holds for models sharing an initialisation. Independently trained models have
> permuted hidden units and different basins, and different sizes do not even have matching tensor shapes.
