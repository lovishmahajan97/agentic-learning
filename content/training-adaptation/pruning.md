---
id: pruning
module: training-adaptation
name: Pruning
kind: concept
difficulty: advanced
est_minutes: 14
status: fact-checked
related:
  - training-adaptation/quantization
  - training-adaptation/knowledge-distillation
  - model-internals/small-language-models
  - model-internals/multi-head-attention
  - model-internals/mixture-of-experts
  - serving-inference/inference-optimization
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
sources:
  - Han et al., 2015 — Learning both Weights and Connections for Efficient Neural Networks
  - Frantar & Alistarh, 2023 — SparseGPT: Massive Language Models Can Be Accurately Pruned in One-Shot
  - Sun et al., 2023 — A Simple and Effective Pruning Approach for Large Language Models (Wanda)
  - Muralidharan et al., 2024 — Compact Language Models via Pruning and Knowledge Distillation
---

## TL;DR
Pruning removes parameters a trained network barely uses, individual weights, attention heads, MLP
neurons or whole layers, and then repairs the damage with a short fine-tune or distillation from the
original. It exists because large models are heavily over-parameterised, so much of the compute spent at
inference moves the output very little. The catch is that only structured removal (whole rows, heads,
layers, or the hardware-supported 2:4 pattern) turns into real speed and memory wins on GPUs; scattered
zeros mostly do not.

## Why it exists
Models are trained big because width and depth make optimisation easy and sample-efficient, not because
every parameter is needed at inference. The evidence of slack is old and consistent: many attention heads
can be removed after training with little loss, a sizeable fraction of middle transformer layers barely
change the residual stream they receive, and half the individual weights of a large model can be zeroed in
one shot with a modest perplexity increase. All that slack is paid for on every token you serve.

The naive route to a smaller model, training one from scratch at the target size, costs the full
pretraining budget, trillions of tokens, and gives you a model that was never as good as the big one.
Quantization, the other obvious lever, shaves a constant factor on bytes but leaves parameter count and
FLOPs unchanged, and it bottoms out around 4 bits. Pruning attacks the count itself: a pruned-and-repaired
model starts from the parent's knowledge, so recovering most of its quality has been reported to need
tens of times fewer tokens than training the small size fresh. It also composes with quantization, since
fewer weights at fewer bits multiply.

What makes pruning harder than it sounds is hardware. GPUs multiply dense matrices; a matrix that is 50%
zeros in random places costs exactly the same to multiply as a full one unless the kernel knows the
pattern. So the central design question of pruning is not "which weights are unimportant" but "which
*shape* of removal can the hardware cash in".

## How it works
### What you remove: the granularity ladder
- **Unstructured** pruning zeroes individual weights anywhere. It preserves accuracy best at a given
  sparsity because it has the most freedom, but the result is an irregular sparse matrix. Dense kernels
  gain nothing, and storing it sparsely needs column indices whose overhead eats most of the saving at 50%;
  general sparse kernels only beat dense ones at very high sparsity (around 90% and up). It is useful for
  CPU runtimes with sparse support and for research, rarely for GPU serving.
- **Semi-structured N:M** pruning, in practice **2:4**, zeroes exactly two of every four consecutive
  weights. NVIDIA GPUs from Ampere onward have sparse tensor cores that execute this pattern at up to twice
  the dense matmul rate, and store it as the two surviving values plus 2-bit position indices: per four
  16-bit weights that is 2 × 16 + 2 × 2 = 36 bits instead of 64, about 56% of the dense size. The sparsity
  is fixed at 50% and the constraint is local, so quality drops more than with free unstructured pruning
  at the same ratio.
- **Structured** pruning removes whole units: attention heads (the matching slices of W_Q, W_K, W_V and
  W_O), MLP neurons (columns of the up/gate projections and the matching rows of the down projection),
  hidden channels (hard, because the residual stream touches every layer), or entire layers (depth
  pruning). The output is a smaller *dense* model, so every kernel on every device gets faster with no
  special support. The price is the coarsest constraint and therefore the largest quality hit per
  parameter removed, which is why structured pruning is almost always followed by recovery training.

### How you decide: importance scores
- **Magnitude** (|w|): the classic criterion, cheap and surprisingly good after training with weight
  decay. On large LLMs it fails in one shot at 50% because of outlier features: a small weight sitting on
  an input channel that always carries huge activations is important, and magnitude cannot see that.
- **Activation-aware** (Wanda): score each weight by |W_ij| · ‖X_j‖₂, its magnitude times the norm of
  the input feature it multiplies over a few calibration sequences, and prune the lowest scores *within
  each output row* so every output keeps the same number of inputs. No gradients, no retraining, seconds
  per layer, and it matches far more expensive methods at 50% on large models precisely because it fixes
  the outlier blind spot.
- **Second-order** (SparseGPT): the same Optimal Brain Surgeon machinery GPTQ uses. For each layer,
  prune the weight whose removal least increases the reconstruction error ‖WX − ŴX‖², then update the
  remaining weights in the row using the inverse Hessian **H⁻¹** = (XXᵀ)⁻¹ to compensate. Done
  column-wise with shared Hessian updates, it prunes 100B+ models to 50–60% unstructured or 2:4 in a few
  hours on one GPU, with better accuracy than Wanda at the cost of the Hessian work.
- **Gradient / Taylor** scores (|w · ∂L/∂w| and group versions) estimate the loss change from removing a
  unit and are the usual choice for heads and neurons. For layers, a cheap and effective proxy is how
  little a block changes its input: compute the cosine similarity between a layer's input and output
  hidden states on calibration data; blocks with similarity near 1 are nearly identity maps and can be
  dropped first. The first and last few layers almost never qualify.

### When you prune and how you recover
One-shot methods (Wanda, SparseGPT) prune a finished model and stop; they are the right tool for a quick
50% result. Classic iterative pruning alternates prune → fine-tune → prune with a sparsity schedule that
ramps gradually, because the network needs time to reroute around each cut. For structured pruning of LLMs
the recipe that works is prune, then **distil from the unpruned parent**: the pruned model is an excellent
initialisation, and token-level distillation provides a dense signal that repairs it with a small
fraction of a pretraining budget. Reported findings from that line of work: width pruning (heads, MLP,
embedding channels) tends to preserve quality better than depth pruning at equal parameter count, while
depth pruning gives the bigger latency win because fewer sequential layers means fewer kernel launches
per token. Mixture-of-experts can be read as learned, input-dependent structured sparsity, which is where
most of the industry's compute savings actually came from; see that lesson.

## Example
Wanda-style pruning of one linear layer in PyTorch, compared with magnitude pruning, in both unstructured
(per-row 50%) and 2:4 form. The calibration activations include a few outlier channels, which is exactly
the case where magnitude scores go wrong and activation-aware scores do not.

```python
import torch
torch.manual_seed(0)
d_in, d_out, n_tok = 1024, 1024, 4096
W = torch.randn(d_out, d_in) * 0.02                # [out, in]
X = torch.randn(n_tok, d_in)
X[:, torch.randperm(d_in)[:8]] *= 30                # a few scattered outlier input channels, as in real LLMs

def prune_rows(W, score, sparsity=0.5):
    """Zero the lowest-scoring fraction of weights within each output row (Wanda's comparison group)."""
    k = int(W.shape[1] * sparsity)
    idx = score.argsort(dim=1)[:, :k]
    mask = torch.ones_like(W, dtype=torch.bool).scatter_(1, idx, False)
    return W * mask

def prune_2of4(W, score):
    """Keep the two highest-scoring weights in every block of four along the input dimension."""
    s = score.view(W.shape[0], -1, 4)
    keep = torch.zeros_like(s, dtype=torch.bool).scatter_(2, s.topk(2, dim=2).indices, True)
    return W * keep.view_as(W)

ref = X @ W.T
magnitude = W.abs()                                 # |W_ij|
wanda = W.abs() * X.norm(dim=0)                     # |W_ij| * ||X_j||_2
for name, Wp in [("magnitude 50%", prune_rows(W, magnitude)), ("wanda 50%", prune_rows(W, wanda)),
                 ("magnitude 2:4", prune_2of4(W, magnitude)), ("wanda 2:4", prune_2of4(W, wanda))]:
    density = (Wp != 0).float().mean().item()
    err = ((X @ Wp.T - ref).norm() / ref.norm()).item()
    print(f"{name:14s} density={density:.2f} relative output error={err:.3f}")
# Expect wanda to beat magnitude clearly: magnitude happily deletes small weights on the outlier
# channels, which carry most of the output. Same density, very different damage.
# In a real model you would score every linear layer with a few hundred calibration sequences,
# then measure perplexity and task evals; for structured cuts, follow with distillation from W.
```

## Trade-offs
- Unstructured pruning gives the best accuracy per removed weight but almost no GPU speedup; it only pays
  on sparse-aware CPU runtimes or at extreme sparsity, so for GPU serving it flips to being a reporting
  exercise rather than a cost saving.
- 2:4 sparsity can run matmuls at up to twice the dense rate on supporting GPUs (realised gains are
  smaller and kernel-dependent) and cuts weight bytes to about 56%,
  but it is a fixed 50%, usually needs recovery training, and only helps decode if the kernel reads the
  compressed form; on hardware without sparse tensor cores it is pure quality loss.
- Structured pruning speeds up everything everywhere, but costs more quality per parameter and needs the
  most recovery compute; depth pruning is cheapest to implement and best for latency, width pruning
  preserves quality better at the same size.
- Prune-then-distil needs a small fraction of the tokens that training a fresh small model would, but the
  result inherits the parent's architecture, tokenizer and biases, and its ceiling is the parent.
- Stacking pruning with quantization multiplies savings, but the errors compound, so prune and recover
  first, quantize last, and evaluate at every stage.

## Pitfalls
- Reporting sparsity as if it were speedup: a 50% unstructured-sparse model runs at identical tokens per
  second in a dense engine; the symptom is a proud "50% smaller" slide and an unchanged GPU bill.
- Calibrating on data unlike the deployment traffic: scores are activation-dependent, so the symptom is
  fine perplexity on web text and broken behaviour on code or chat.
- Removing layers by position rules of thumb without measuring block influence, including the first or
  last layers: the symptom is degenerate, repetitive output even at a small depth cut.
- Skipping the recovery fine-tune because perplexity looked acceptable: perplexity and downstream ability
  diverge after pruning, so the symptom is intact next-token loss and collapsed instruction following or
  reasoning on your evals.
- Width-pruning one side of a paired matrix (dropping MLP up-projection columns without the matching
  down-projection rows, or query heads without accounting for grouped KV heads): the symptom is a shape
  error if you are lucky and silent garbage if you are not.
- Assuming pruning is behaviour-neutral: safety and refusal behaviour can be disproportionately fragile
  under pruning, so the symptom is a smaller model that complies with requests its parent refused. Re-run
  the safety suite, not just the capability suite.

## When to use
Prune when you need a model size that does not exist in the family you have standardised on, when you have
a strong parent and some training budget for recovery, or when the hardware has 2:4 support and the
workload is matmul-heavy. Prefer quantization first; it is cheaper and usually reaches the memory target
alone, and prune only when latency or parameter count, not bytes, is the constraint. Measure tokens per
second and task evals at the deployment batch size, never sparsity percentages, and keep the KV cache in
the picture: pruning query heads does not shrink it, pruning KV heads or layers does. In day-to-day work
pruning shows up as a model-shrinking project with a distillation phase attached; in interviews, expect to
explain why unstructured sparsity does not speed up a GPU, how Wanda differs from magnitude pruning, and
what 2:4 actually buys.

## Role lens
- **Engineer:** Runs one-shot pruning with calibration data from production prompts, wires the recovery
  distillation run, and benchmarks real tokens per second against the dense parent before claiming a win.
- **Architect:** Decides whether the size gap is better closed by pruning a known-good parent or by
  adopting a different model, weighing recovery compute against the operational cost of a new family.
- **FDE:** Rarely prunes in the field, but must recognise a vendor's "50% sparse" claim for what it is and
  ask which hardware and kernel path turns that into latency the customer will feel.

## Check yourself
**Q:** A product needs decode latency about 30% lower than its 8B model delivers on A100s, with no budget
for a different GPU. Which pruning options are realistic, and in what order would you try them?
**A:** Decode is bandwidth-bound, so the target is fewer bytes read per token, and the first option is not
pruning at all: INT4 or INT8 weights cut bytes more than 30% with no training, so quantize first and
measure. If latency is still short, depth pruning is the direct lever, since dropping a quarter of the
layers removes a quarter of the sequential work and the bytes; measure block influence by input/output
cosine similarity, remove the least influential middle layers, and distil from the unpruned 8B to recover.
2:4 sparsity is tempting on A100s, but its decode benefit depends on the engine reading the compressed
format and it needs recovery training too, so I would try it only after depth pruning. Throughout, I would
track task evals and safety evals, not perplexity, and verify the KV cache budget is unchanged.

## Quiz
### Q: Why does 50% unstructured sparsity usually not make a GPU model faster?
- [ ] Zero weights still consume registers during attention
- [x] Dense kernels multiply the zeros anyway, and sparse formats need index overhead and specialised kernels that only pay off at much higher sparsity
- [ ] The KV cache grows to compensate for the missing weights
- [ ] Sparse models cannot be quantized
> GPUs execute dense matmuls regardless of zero values. Cashing in sparsity needs either hardware-known
> patterns such as 2:4 or structural removal that yields a smaller dense matrix.

### Q: What does Wanda's importance score multiply together?
- [ ] Weight magnitude and the weight's gradient
- [ ] Weight magnitude and the inverse Hessian diagonal
- [x] Weight magnitude and the L2 norm of the input activation feature it multiplies
- [ ] Output activation variance and layer depth
> Wanda scores |W_ij| · ‖X_j‖₂ so that small weights on outlier input channels are protected, which is
> the case where pure magnitude pruning does the most damage in large language models.

### Q: After structurally pruning a large model, what is the most token-efficient way to recover quality?
- [ ] Retrain the pruned architecture from random initialisation on the original corpus
- [x] Distil from the unpruned parent, using the pruned weights as the initialisation
- [ ] Quantize it to 4-bit so the remaining weights are used more efficiently
- [ ] Increase the learning rate and run one more epoch of SFT
> The pruned model already sits near a good solution, and token-level distillation gives a dense signal, so
> recovery reportedly needs a small fraction of the tokens a fresh small model would require.
