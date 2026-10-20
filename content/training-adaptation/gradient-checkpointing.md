---
id: gradient-checkpointing
module: training-adaptation
name: Gradient checkpointing
kind: concept
difficulty: core
est_minutes: 14
related:
  - training-adaptation/checkpointing
  - training-adaptation/peft
  - training-adaptation/distributed-training
  - training-adaptation/tooling
  - ml-fundamentals/backpropagation
  - ml-fundamentals/mixed-precision
  - ml-fundamentals/gradient-accumulation
  - model-internals/flashattention
sources:
  - Chen et al., 2016 — Training Deep Nets with Sublinear Memory Cost
  - Korthikanti et al., 2022 — Reducing Activation Recomputation in Large Transformer Models
  - Official docs: PyTorch torch.utils.checkpoint
---

## TL;DR
Despite the name, gradient checkpointing has nothing to do with saving a model to disk; it is activation
recomputation. Backprop needs every layer's forward activations, and at long sequence lengths those
outweigh the weights. Checkpointing keeps only each block's input, discards the rest, and recomputes a
block's internals during backward. You pay roughly one extra forward pass of compute to shrink activation
memory from "all layers" to "all block inputs plus one block", which is what lets long-context fine-tunes
fit on one GPU.

## Why it exists
Training memory has four parts: weights, gradients, optimizer state and activations. The first three are
a fixed number of bytes per parameter (about 16 with AdamW in mixed precision) and PEFT and quantization
attack them. Activations are different: they scale with batch × sequence length × hidden size × layers,
and they exist only because backprop needs them. For a linear layer `y = xW`, the weight gradient is
`xᵀ · dL/dy`, so `x` must be kept; GELU needs its input; softmax needs its output. The autograd engine
therefore holds every intermediate tensor from the forward pass until the backward pass consumes it, and
backward runs last-layer-first, so at the end of the forward pass *all* of them are live at once.

Put numbers on it. Korthikanti et al. estimate the activations of one transformer layer in bf16, without
FlashAttention, at roughly `s·b·h·(34 + 5·a·s/h)` bytes (s = sequence, b = batch, h = hidden, a = heads).
For a 7B-class model (h = 4096, a = 32, 32 layers) at s = 4096, b = 1: the linear term 34·s·b·h is about
570 MB per layer and the attention-score term 5·a·s²·b is about 2.7 GB per layer, so ~105 GB in total.
FlashAttention removes the s² term by never materialising the score matrix, which leaves ~18 GB, and that
figure doubles at 8k. A QLoRA run whose weights plus adapter plus optimizer state fit in 6 GB still
cannot fit on a 24 GB card at 8k context because of activations alone (all figures illustrative, from
the formula above).

The naive alternatives all fail on this shape of problem. Gradient accumulation shrinks the micro-batch,
but you are already at b = 1. Shortening the sequence changes the task you are training for. A bigger GPU
is a procurement ladder, not an engineering answer. Gradient checkpointing is the lever that trades the
one resource GPUs have in abundance, compute, for the one they lack.

## How it works
### The observation
A block's internal activations are a deterministic function of its input (dropout aside, which is handled
by replaying the RNG state). If you still have the input, you can rebuild the internals whenever you need
them. So you do not have to keep them across the whole forward pass; you only need them during that
block's own backward step.

### The algorithm
1. Partition the network into segments. In transformers the common choice is one decoder block per
   segment, because block boundaries are where the activation tensor is smallest (just `s·b·h`).
2. In the forward pass, run each segment without recording the autograd graph. Keep only the segment's
   input tensor; let its internal activations be freed.
3. In the backward pass, when the gradient reaches segment *k*'s output, re-run segment *k*'s forward
   from its stored input with graph recording on, backpropagate through that local graph, free it, and
   move on to segment *k − 1*.

Peak activation memory becomes (all segment inputs) + (one segment's full internals). With n layers and
segments of √n layers each, both terms are O(√n), which is the "sublinear memory" result of Chen et al.
Per-block checkpointing in a transformer stores n block inputs, each `s·b·h·2` bytes, plus one block's
internals. In the 7B example at 4k: 32 × 33.5 MB ≈ 1.1 GB of inputs plus ~570 MB for the live block,
down from ~18 GB.

The cost is that each segment's forward runs twice. Forward is roughly a third of a training step's FLOPs
(backward is about twice forward), so the worst case is about a third more compute; the commonly quoted
wall-clock slowdown is in the 20–30% range (illustrative; it depends on how much of the step is
communication or data loading rather than math).

### Selective recomputation
Not every activation costs the same to store or to recompute. Korthikanti et al. point out that the
attention scores and their softmax/dropout outputs are the entire s² term yet are cheap to recompute, so
recomputing *only* those gives most of the memory benefit for a fraction of the overhead. FlashAttention
does exactly this implicitly: it saves the per-row log-sum-exp and rebuilds score tiles in backward. The
two techniques compose cleanly: FlashAttention deletes the s² term and per-block checkpointing deletes the
34·s·b·h term.

### Framework mechanics and the reentrant trap
PyTorch exposes `torch.utils.checkpoint.checkpoint(fn, *args, use_reentrant=False)`. There are two
implementations. The older *reentrant* one runs the segment under `no_grad` and re-enters autograd in
backward; its sharp edge is that if **no input to the segment requires grad**, autograd never records
the segment at all, so parameters inside it silently receive no gradient. That is the LoRA case: the
embedding output does not require grad because the embeddings are frozen. PEFT recipes work around it
with `model.enable_input_require_grads()`. The *non-reentrant* implementation uses saved-tensor hooks,
handles inputs without grad correctly, and is the recommended mode. In Transformers you enable it with
`model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})` or
`TrainingArguments(gradient_checkpointing=True)`. Transformers also forces `use_cache=False` when
checkpointing is on, because the KV cache that generation builds is pointless during training and
conflicts with recomputation.

### Where it sits among the other levers
Gradient accumulation cuts the b in `s·b·h`; sequence or context parallelism cuts the s by splitting it
across GPUs; FlashAttention cuts the s² term; CPU offloading of activations moves them across PCIe, which
is usually slower than simply recomputing. With FSDP, checkpoint per block *inside* each FSDP unit so
that recomputation does not trigger extra parameter all-gathers.

## Example
A residual MLP stack with and without checkpointing. Weights and gradients are identical in both runs
(about 6 GB in fp32 for this configuration), so the entire difference in peak memory is activations: the
checkpointed run keeps only each block's `[tokens, d]` input and rebuilds the `4·d`-wide hidden and GELU
tensors during backward.

```python
import torch, torch.nn as nn
from torch.utils.checkpoint import checkpoint

class Block(nn.Module):
    def __init__(self, d):
        super().__init__()
        self.ff = nn.Sequential(nn.Linear(d, 4 * d), nn.GELU(), nn.Linear(4 * d, d))
    def forward(self, x):
        return x + self.ff(x)

class Net(nn.Module):
    def __init__(self, d, n, ckpt):
        super().__init__()
        self.blocks = nn.ModuleList(Block(d) for _ in range(n))
        self.ckpt = ckpt
    def forward(self, x):
        for b in self.blocks:
            x = checkpoint(b, x, use_reentrant=False) if self.ckpt else b(x)
        return x

def peak_gb(ckpt, d=2048, n=24, tokens=4096):
    torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats()
    net = Net(d, n, ckpt).cuda()
    x = torch.randn(tokens, d, device="cuda", requires_grad=True)
    net(x).sum().backward()
    return torch.cuda.max_memory_allocated() / 2**30

for flag in (False, True):
    print(f"checkpointing={flag}: peak {peak_gb(flag):.1f} GB")
# Expect the checkpointed peak to be roughly half; raise `tokens` and the gap widens, because
# stored activations grow with tokens while the weight/gradient share stays fixed.
# Hugging Face equivalent: model.gradient_checkpointing_enable(
#     gradient_checkpointing_kwargs={"use_reentrant": False})
```

## Trade-offs
- You pay roughly one extra forward pass per step (20–30% wall-clock, illustrative) for a large cut in
  activation memory; if you are compute-bound with memory to spare, it is pure loss and should be off.
- The freed memory usually lets you raise the micro-batch, and a GPU at micro-batch 1 is badly
  under-utilised, so tokens/second often goes *up* overall despite the recompute; this flips once the
  batch already saturates the GPU.
- Per-block granularity is simple and matches FSDP wrapping, but coarser segments store less and recompute
  more; the √n split is the theoretical optimum and rarely worth the engineering over per-block.
- Non-reentrant mode handles frozen inputs and nested checkpoints correctly; reentrant mode can have
  slightly lower overhead in some stacks but fails silently in the LoRA case, so the default should be
  non-reentrant.

## Pitfalls
- Confusing it with model checkpointing (saving state to disk); the symptom is someone enabling "checkpointing"
  for resumability, losing a multi-day run, and discovering nothing was ever written.
- Reentrant mode with frozen embeddings in a LoRA run; the symptom is a flat loss curve and adapter
  gradients that are `None`, fixed by `enable_input_require_grads()` or `use_reentrant=False`.
- Enabling checkpointing and still hitting OOM at long context; the symptom is peak memory barely moving,
  because the s² attention-score term is materialised and only FlashAttention/SDPA removes it.
- Measuring "steps got slower" and switching it off globally instead of raising the micro-batch to recover
  utilisation; the right metric is tokens per second, not seconds per step.
- Side effects inside a checkpointed function (counters, logging, in-place updates, custom RNG not covered
  by `preserve_rng_state`) run twice; the symptom is doubled metrics or a gradient mismatch versus the
  uncheckpointed run.

## When to use
Reach for it when activation memory is the thing that does not fit, which you diagnose by computing the
`s·b·h·34` estimate or by comparing `torch.cuda.max_memory_allocated()` with and without the flag. In
practice it is on by default for any fine-tune at 4k+ context on a single GPU, and it is what makes
QLoRA on a 24 GB card at 8k realistic. Leave it off for small models and short sequences where the GPU is
compute-bound and memory is plentiful. Always pair it with FlashAttention or SDPA, re-tune the micro-batch
after enabling it, and judge the result by tokens/second. In interviews, the expected depth is to separate
the per-parameter memory (weights, gradients, optimizer) from activation memory, state what checkpointing
trades, explain the √n argument, and name the reentrant trap.

## Role lens
- **Engineer:** Flips the flag in `TrainingArguments`, Axolotl or Unsloth, pairs it with FlashAttention,
  raises the micro-batch until memory is full again, and checks tokens/second against the baseline.
- **Architect:** Sets the sequence-length-versus-GPU-tier policy knowing that checkpointing lets a 24 GB card
  handle an 8k QLoRA, so the capacity plan is not "buy 80 GB cards" by default.
- **FDE:** Fits a customer's long-document fine-tune onto the fixed GPU quota in their VPC instead of
  waiting on a procurement cycle, and explains the 20–30% step-time cost up front.

## Check yourself
**Q:** A team runs QLoRA on a 7B model at 8k context on a 24 GB GPU. The 4-bit weights are ~4 GB and the
adapter plus optimizer state is tiny, yet they OOM at micro-batch 1. Explain what is using the memory and
what you would change, with rough numbers.
**A:** The rest is activations. With h = 4096 and 32 layers, the linear term 34·s·b·h at s = 8192 is about
1.1 GB per layer, roughly 36 GB across the model even with FlashAttention; without it the attention-score
term 5·a·s²·b adds about 10 GB per layer. So first turn on FlashAttention or SDPA to delete the s² term,
then enable gradient checkpointing in non-reentrant mode (or call `enable_input_require_grads()`), which
cuts stored activations to one block input per layer (~67 MB each, ~2 GB total) plus a single live block
(~1.1 GB). That fits with room to raise the micro-batch, at roughly a 20–30% per-step cost. Verify with
`max_memory_allocated()` before and after, and judge the change by tokens per second.

## Quiz
### Q: What does gradient checkpointing trade, and for what?
- [ ] Disk space for GPU memory, by paging activations to NVMe
- [x] Extra forward compute for activation memory, by recomputing block internals during backward
- [ ] Numerical precision for memory, by storing activations in 8-bit
- [ ] Communication bandwidth for memory, by sharding activations across GPUs
> Checkpointing keeps only block inputs and re-runs each block's forward during the backward pass. Nothing
> leaves the GPU and nothing changes precision; the price is roughly one additional forward pass of compute.

### Q: A LoRA run with reentrant checkpointing shows a flat loss and `None` adapter gradients. Why?
- [ ] LoRA adapters cannot be placed inside a checkpointed segment
- [ ] 4-bit base weights cannot be recomputed during the backward pass
- [x] No segment input requires grad because embeddings are frozen, so autograd never records the segment
- [ ] The RNG state is not restored, so dropout masks differ between the two forward passes
> In reentrant mode the segment is only attached to the graph through inputs that require grad. Frozen
> embeddings produce inputs that do not, so the adapters inside get no gradient. `enable_input_require_grads()`
> or `use_reentrant=False` fixes it.

### Q: After enabling checkpointing at 8k context, peak memory barely moved. What is the most likely cause?
- [ ] Optimizer state dominates and checkpointing does not touch it
- [x] The attention-score matrices (the s² term) are still materialised because FlashAttention/SDPA is off
- [ ] Too many gradient-accumulation steps are keeping old activations alive
- [ ] The checkpoint files on disk are too large for the page cache
> Per-block checkpointing removes the linear 34·s·b·h term, but the quadratic score term lives inside the
> block and is only eliminated by an attention kernel that never materialises the full matrix.
