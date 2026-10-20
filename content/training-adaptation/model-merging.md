---
id: model-merging
module: training-adaptation
name: Model merging
kind: concept
difficulty: advanced
est_minutes: 15
status: crisp
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
Model merging combines fine-tunes of one base into one set of weights by parameter arithmetic (averaging,
task vectors, trimming and sign-voting), with no training. You get several skills at one model's serving
cost, paying with task interference nothing corrects.

## Why it exists
- **Serving several fine-tunes is costly.** Three fine-tunes of one 8B base (support domain, tool-call
  formats, a community instruct tune with better conversation) mean three copies of the weights, a router,
  and no way for one answer to use two skills at once.
- **Multi-task training is often impossible.** You need all three datasets (the community one is not yours
  to have), must rebuild the mix whenever one changes, and pay a training run per iteration. Ensembling
  costs three forward passes per token.
- **Merging is a fourth option.** Every model is `base + small delta`, so you add the deltas and get one
  model in minutes on a CPU.
- **Why it is not absurd.** Fine-tunes from the same pretrained weights stay in the same loss basin, so the
  straight line between them has no loss barrier (linear mode connectivity). Averaging two good solutions
  tends to land on a third, often better than either.
- **The evidence.** Model soups: averaging fine-tunes with different hyperparameters improved accuracy and
  out-of-distribution robustness at zero inference cost. Task arithmetic: deltas behave like vectors you
  can add, scale and even negate to remove a behaviour.
- **Plain averaging breaks on disagreement.** One tune pushes a weight up, another down, and the average
  cancels both. Later methods all answer that interference problem.

## How it works
### Task vectors
- **Definition:** τ_i = θ_i − θ_base, computed tensor by tensor for base θ_base and fine-tune θ_i.
- **Uses:**
  - multi-task merging: θ = θ_base + λ Σ τ_i;
  - forgetting: θ_base − λ τ (negating a toxicity fine-tune reduces toxicity);
  - even analogies.
- **λ** is one scalar (or one per task) tuned on a validation set. Deltas are additive approximations, so
  λ around 0.3–1.0 in total usually works; larger sums push activations out of the range the base was
  trained in.

### Linear averaging and soups
- **Formula:** θ = Σ w_i θ_i with Σ w_i = 1.
- **Uniform soups** average everything; **greedy soups** add a model only if the validation metric improves.
- **Right tool when** models learned the *same* thing differently (same data, different seeds or learning
  rates). Checkpoint averaging at the end of a long run is the same idea along one trajectory.
- **Wrong tool when** the models learned different things.

### SLERP
Spherical linear interpolation treats each tensor (or the whole flattened model) as a vector and moves
along the arc between two, with cos Ω = a·b / (‖a‖‖b‖) and

```
slerp(a, b, t) = sin((1 − t)Ω) / sin Ω · a  +  sin(tΩ) / sin Ω · b
```

- **Why: norm preservation.** A straight-line midpoint between two vectors at an angle is *shorter* than
  either, shrinking activation scales layer by layer; SLERP keeps the magnitude.
- **Limit:** two models at a time. The usual choice for blending two closely related instruct tunes.

### TIES-Merging: trim, elect sign, disjoint merge
TIES names two sources of interference:
- **Noise:** most entries of a task vector are tiny but still perturb other tasks when summed.
- **Sign conflicts:** when τ_A and τ_B disagree on direction, averaging cancels useful movement.

Three steps, in order, each removing one failure mode:
1. **Trim.** Keep only the top *k*% of each τ_i by magnitude (20% is common); zero the rest. Fine-tuning
   changes concentrate in a few parameters, so this removes noise without losing much.
2. **Elect.** Per parameter, pick the sign carrying more total magnitude: sign(Σ_i τ_i) after trimming.
   This decides which way the merged model moves.
3. **Disjoint merge.** Average only task vectors agreeing with the elected sign there, ignoring dissenters
   instead of letting them cancel the majority. Then θ = θ_base + λ τ_merged.

Four-parameter walkthrough (trim to top 50%, two entries each):

| | p1 | p2 | p3 | p4 |
|---|---|---|---|---|
| τ_A | +0.30 | −0.02 | +0.10 | −0.40 |
| τ_B | −0.25 | +0.01 | +0.12 | −0.05 |
| τ_C | +0.05 | 0.00 | −0.08 | −0.30 |
| Plain mean | +0.03 | 0.00 | +0.05 | −0.25 |
| Trimmed A / B / C | +0.30 / −0.25 / 0 | 0 / 0 / 0 | 0 / +0.12 / −0.08 | −0.40 / 0 / −0.30 |
| Elected sign | + | 0 | + | − |
| τ_merged | +0.30 | 0 | +0.12 | −0.35 |

- **Agreement survives at full strength.** It is not divided by the number of tasks, which is why λ for
  TIES sits near 1 rather than 1/N.
- **Conflicts are decided, not resolved.** On p1, B loses outright because A's magnitude was larger, so
  B's skill pays wherever it depended on that weight.

### DARE: drop and rescale
- **Observation:** SFT deltas are extremely redundant. Zero a random 90% (or more, on large models) of a
  task vector's entries and multiply survivors by 1/(1 − p): behaviour barely changes.
- **Why:** the rescaling keeps the expected delta identical, and the redundancy covers dropped entries.
- **Use:** sparsified deltas from different tasks collide far less, so DARE is preprocessing before
  summing or before TIES ("dare_ties").
- **Limit:** works when deltas are small relative to the base; fails on continued-pretraining deltas,
  which are large and not redundant.

### Passthrough and frankenmerges
- Stacking layers from different models, or repeating a block to make a deeper model (depth up-scaling),
  is merging without arithmetic.
- The result is broken until healed with continued training, so this is model building, not a free
  combination step.

### Constraints that follow from the mechanism
- **Same architecture, shapes and lineage.** Everything is elementwise arithmetic, so it needs identical
  tensor shapes (hence the same tokenizer and vocabulary size) and the same base.
- **Different base checkpoints:** deltas measured from different origins.
- **Different random initialisations:** related by hidden permutations of neurons; they average into
  noise unless aligned first.
- **LoRA adapters are task vectors** in a low-rank subspace, so adapter merging (a weighted sum of B_iA_i
  products, with the same TIES/DARE options) has the same interference problems.
- **Nothing is trained, so nothing is verified.** Every merge needs the full eval suite, including chat
  template, end-of-turn behaviour and safety checks, before anyone calls it a model.

## Examples
### TIES merge over Hugging Face state dicts
PyTorch. All checkpoints must share the base model and tensor shapes. It works tensor by tensor, so peak
memory is one tensor times the number of models, and copies non-float buffers unchanged.

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

### Reproduce the four-parameter walkthrough
Pure Python; runs anywhere and prints the table's last row.

```python
tau = {"A": [+0.30, -0.02, +0.10, -0.40],
       "B": [-0.25, +0.01, +0.12, -0.05],
       "C": [+0.05, 0.00, -0.08, -0.30]}

def trim(v, keep=2):                       # keep the top-`keep` entries by magnitude
    top = sorted(range(len(v)), key=lambda i: abs(v[i]), reverse=True)[:keep]
    return [x if i in top else 0.0 for i, x in enumerate(v)]

sign = lambda x: (x > 0) - (x < 0)
mean = [round(sum(c) / 3, 2) for c in zip(*tau.values())]
trimmed = [trim(v) for v in tau.values()]
merged = []
for col in zip(*trimmed):
    s = sign(sum(col))                                   # elect
    agree = [x for x in col if x != 0 and sign(x) == s]  # disjoint merge
    merged.append(round(sum(agree) / len(agree), 2) if agree else 0.0)
print("plain mean:", mean)      # [0.03, -0.0, 0.05, -0.25]
print("TIES      :", merged)    # [0.3, 0.0, 0.12, -0.35]
```

### The same merge as a mergekit config
The declarative form teams usually commit to version control; run it with `mergekit-yaml config.yml
./merged`.

```yaml
merge_method: ties
base_model: org/base-8b
models:
  - model: org/support-sft
    parameters:
      density: 0.2      # TIES trim: keep the top 20% of each task vector
      weight: 0.5
  - model: org/instruct-tune
    parameters:
      density: 0.2
      weight: 0.5
parameters:
  normalize: true
dtype: bfloat16
```

## Production patterns
- **Merge recipes as code.** Keep each merge as a versioned config (method, parent revisions, density,
  weights, λ) next to its eval report, so any registered model can be rebuilt exactly. Monitor that parent
  revisions in the config match the registry.
- **Lineage check before merging.** Verify every parent was fine-tuned from the same base revision and
  shares tokenizer and vocabulary size; refuse the merge on any shape mismatch instead of padding tensors.
- **Small hyperparameter grid on a mixed validation set.** Sweep density (for example 0.2 to 0.5) and
  weights or λ, plus a SLERP candidate for two-model merges, scoring every parent's task. Pick the point
  where no parent's metric drops beyond tolerance.
- **Full release gate, like a trained model.** Per-task evals against each parent, a general benchmark, a
  chat-template smoke test (end-of-turn, role tags, system prompt) and the safety suite. Track refusal
  rate on a fixed red-team set across every merge.
- **Routing fallback.** If the merge cannot hold every parent's quality, keep the specialists behind a
  router and treat the merge as an experiment. Compare cost per request and quality across both paths.

## Trade-offs
- **No training compute vs no training signal.** No datasets or GPUs needed, but nothing guarantees the
  merge works, so evaluation effort replaces training effort. When you own the data and compute, multi-task
  SFT is more reliable.
- **One model vs interference.** One model for several skills saves GPUs and routing, but interference
  grows with task count and delta size; past a handful of tasks, per-task quality usually slips below the
  specialists.
- **Averaging vs TIES/DARE.** Plain averaging improves robustness for near-duplicates but cancels
  conflicting updates; TIES and DARE fix that at the cost of hyperparameters (k, p, λ) tuned on a
  validation set.
- **SLERP vs N-way methods.** SLERP preserves norms and often beats linear interpolation for two models,
  but does not extend to many; linear and TIES scale to N at the cost of norm drift.
- **Negation power vs governance.** Task-vector negation removes unwanted behaviour cheaply, but the same
  mechanism can strip safety tuning, deliberately or by accident, making merges a governance problem too.

## Pitfalls
- **Wrong origin for deltas.** Merging fine-tunes of different base revisions, or treating an instruct
  model as the base for deltas computed against the pretrained one. Symptom: incoherent or garbage output.
- **Vocabulary mismatch from added special tokens.** Embedding and output-head shapes differ. Symptom: a
  shape error, or, if someone pads the tensors, wrong tokens at generation time.
- **Weights summing well above one, or λ too high.** Activations leave the range the base expects.
  Symptom: repetitive, degenerate text and logits that blow up.
- **Mixed chat templates.** Each parent learned its own, and the merge gets a mixture. Symptom: leaked
  role tags, never emitting end-of-turn, or ignoring the system prompt.
- **Trusting leaderboard scores for merges.** Benchmark contamination accumulates across parents.
  Symptom: tops a public board, underperforms its parents on your tasks.
- **Skipping the safety regression.** Merging can quietly undo refusal training. Symptom: complies with
  requests every parent refused, discovered by a user rather than a test.

## When to use
- **Use when:** you have several fine-tunes of the same base and need one artifact; you want a community
  tune's conversational quality for your domain model without its data; you are averaging checkpoints or
  seeds from your own run; or you want a fast experiment before a multi-task training job.
- **Avoid when:** you own the data, so training is the better substitute.
- **Never ship** a merge without per-task evals, a general benchmark, a template smoke test and the
  safety suite.
- **Measure:** each parent's task metric before and after; treat any drop as the merge's cost, not noise.
- **Interviews:** explain task vectors, why TIES elects a sign, why DARE's rescaling preserves behaviour,
  and why two models from different initialisations cannot simply be averaged.

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
