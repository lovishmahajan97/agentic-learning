---
id: mixed-precision
module: ml-fundamentals
name: Mixed precision
kind: practice
difficulty: core
est_minutes: 15
related:
  - ml-fundamentals/gradient-clipping
  - ml-fundamentals/gradient-accumulation
  - ml-fundamentals/normalization
  - ml-fundamentals/optimizers
  - ml-fundamentals/batch-size
  - training-adaptation/quantization
  - serving-inference/gpu-memory-optimization
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
sources:
  - Micikevicius et al., 2018 — Mixed Precision Training
  - Kalamkar et al., 2019 — A Study of BFLOAT16 for Deep Learning Training
  - Micikevicius et al., 2022 — FP8 Formats for Deep Learning
  - Official docs: PyTorch Automatic Mixed Precision (torch.amp)
---

## TL;DR
Mixed precision runs the expensive parts of training (the matrix multiplications) in a 16-bit float while
keeping an fp32 copy of the weights for the update. Tensor cores are many times faster at 16 bits,
activations take half the memory and bandwidth, and the fp32 master copy stops tiny updates from being
rounded away. fp16 has range problems that need loss scaling; bf16 has fp32's range and skips that
machinery at a precision cost, so it is the default where hardware allows.

## Why it exists
Training time is dominated by matrix multiplications, and a GPU's arithmetic throughput depends sharply
on the number format. On an A100 the datasheet gives about 19.5 TFLOPS for fp32 on the CUDA cores and
about 312 TFLOPS for fp16 or bf16 on the tensor cores, a 16× gap on the same chip. Memory traffic halves
too: a 16-bit activation tensor is half the bytes to store and move, and for the bandwidth-bound parts of
a step (normalization, softmax, element-wise ops) that halving is the whole speed-up. Activation memory,
which scales with batch × sequence length, also halves, which means a larger micro-batch or a longer
context on the same card.

The naive version, "cast the whole model to fp16", fails in three ways that the 2018 mixed-precision paper
documented. First, fp16's largest value is 65,504; attention logits, loss sums and the squared terms
inside a variance can exceed it, becoming `inf` and then `NaN`. Second, fp16's smallest normal value is
about 6e-5 (subnormals reach about 6e-8), and a large fraction of real gradient values fall below that,
so they underflow to exactly zero and the affected weights stop learning. Third, and independent of
range, an update is `w ← w − η·g`, and with `η` around 1e-4 the update is routinely less than a
thousandth of the weight's magnitude. A 16-bit float carries only 11 (fp16) or 8 (bf16) significant bits,
so an update smaller than roughly 2^-11 or 2^-8 of the weight is rounded away: the optimizer steps and
nothing moves.

Keeping the weights in fp32 for the update is what makes the arithmetic survivable; running the
matmuls in 16 bits is what makes it fast. That split is the "mixed" in mixed precision.

## How it works
### The formats
```
           sign  exponent  mantissa   max value   smallest normal   spacing at 1.0
fp32         1       8        23       3.4e38       1.2e-38          2^-23 ≈ 1.2e-7
tf32         1       8        10       3.4e38       1.2e-38          2^-10 ≈ 1.0e-3   (tensor-core internal)
fp16         1       5        10       65,504       6.1e-5           2^-10 ≈ 1.0e-3
bf16         1       8         7       3.4e38       1.2e-38          2^-7  ≈ 7.8e-3
fp8 E4M3     1       4         3       448          1.6e-2           2^-3  = 0.125
fp8 E5M2     1       5         2       57,344       6.1e-5           2^-2  = 0.25
```

bf16 is fp32 with the low 16 bits of the mantissa cut off, which is why converting between them is a
shift, why its range is identical, and why it keeps only about three significant decimal digits. fp16
spends its bits on precision instead of range. The exponent width decides everything downstream: fp16
needs a mechanism against under- and overflow, bf16 does not, but bf16 rounds more coarsely, so long sums
and the weight update are where it hurts.

### Autocast: which op runs in which precision
Frameworks implement the recipe as a per-operator policy rather than a global cast. Under
`torch.autocast`, matmuls, convolutions and linear layers run in the 16-bit type, with inputs cast on the
way in; numerically sensitive ops (softmax, log-softmax, cross-entropy, layer norm, sums, exponentials)
run in fp32 and return fp32; element-wise ops take the widest input type. The parameters themselves stay
fp32 in memory, and autocast makes a 16-bit copy per op, cached for the duration of a forward pass. The
op-level split exists because a tensor-core matmul accumulates its products in fp32 even with 16-bit
inputs, so it is safe, whereas a softmax over thousands of logits or a variance over 4,096 features is a
long sum whose rounding error compounds in 16 bits.

### fp32 master weights and the update
Forward and backward produce 16-bit activations and gradients. The optimizer reads the gradients (cast
up), updates the fp32 master weights, whose rounding unit is 2^-23, and the next forward casts the
updated masters down again. Memory per parameter under Adam is therefore *not* reduced by mixed
precision: 16-bit weight copy (2) + 16-bit gradient (2) + fp32 master (4) + fp32 first moment (4) + fp32
second moment (4) = 16 bytes per parameter, the accounting used in the ZeRO paper, so a 7B model carries
about 112 GB of model state regardless. Mixed precision shrinks activation memory and buys compute speed;
cutting model-state memory is the job of sharding, 8-bit optimizers and quantization.

### Loss scaling (fp16 only)
Gradients are small, and by linearity the gradient of `S × loss` is `S` times the gradient of `loss`
everywhere. Multiply the loss by `S` before backward so the gradients land in fp16's representable band;
divide them by `S` (unscale) before clipping and before the optimizer uses them. A fixed `S` is fragile,
so dynamic loss scaling starts large (2^16 in PyTorch's `GradScaler`), checks the gradients for `inf` or
`NaN` after each backward, skips the optimizer step and halves `S` on overflow, and doubles `S` after a
run of clean steps (2,000 by default). A few skipped steps early are normal; a scale that keeps collapsing
means the *forward* is overflowing, which scaling cannot fix.

bf16 needs none of this: its exponent range is fp32's, so gradients neither overflow nor underflow in
practice. The cost is the 8-bit mantissa, which is why bf16 training keeps master weights in fp32 without
exception and why the autocast policy keeps reductions in fp32.

### Hardware
fp16 tensor cores arrived with Volta (V100), bf16 tensor cores with Ampere (A100) and earlier on TPUs.
T4 and V100 have no bf16 hardware, so fp16 with loss scaling is the only 16-bit option there. Ampere also
introduced TF32, which rounds fp32 matmul inputs to 10 mantissa bits inside the tensor core while keeping
fp32 storage and range; PyTorch enables it for cuDNN convolutions by default and for matmuls through
`torch.set_float32_matmul_precision("high")`. Hopper (H100) added fp8 tensor cores: E4M3 for forward
activations and weights, E5M2 for gradients, with per-tensor scaling factors managed by a library such as
Transformer Engine because three mantissa bits leave no margin. fp8 training of production models is
real (DeepSeek-V3 reported training in fp8 with fine-grained scaling), but it is a specialist recipe, not
a flag.

### Inference
Serving with bf16 or fp16 weights is the baseline and is not quantization: the weights are stored in the
type they trained in. Quantization to int8, fp8 or int4 is a further step with its own calibration and
quality evaluation, covered in the training-adaptation and serving modules.

## Example
The first block shows the three failure modes directly on a CPU; the second is the training step. Note
what is absent: no `model.half()`, no casting of the optimizer, no change to the learning rate. The model
stays fp32 (those are the master weights) and autocast handles the per-op casting.

```python
import torch
from torch.nn.utils import clip_grad_norm_

# 1. What the formats can and cannot hold
f16, b16 = torch.float16, torch.bfloat16
print(torch.tensor(70_000., dtype=f16))        # inf      fp16 max is 65,504
print(torch.tensor(70_000., dtype=b16))        # 70144.   bf16 has fp32's range but ~3 digits
print(torch.tensor(1e-8, dtype=f16))           # 0.       below fp16's smallest subnormal (~6e-8)
print(torch.tensor(1., dtype=b16) + 0.001)     # 1.       bf16 spacing at 1.0 is 2^-7 ≈ 0.0078
print(torch.tensor(1., dtype=f16) + 0.001)     # 1.0010   fp16 spacing at 1.0 is 2^-10 ≈ 0.00098

# 2. One training step: fp32 master weights, 16-bit matmuls, a scaler only for fp16
def train_step(model, opt, x, y, dtype, scaler=None, max_norm=1.0):
    opt.zero_grad(set_to_none=True)
    with torch.autocast("cuda", dtype=dtype):   # matmuls in dtype; softmax, norm, loss stay fp32
        loss = torch.nn.functional.cross_entropy(model(x), y)
    if scaler is None:                           # bf16: range is fine, nothing to scale
        loss.backward()
        clip_grad_norm_(model.parameters(), max_norm)
        opt.step()
    else:                                        # fp16: scale so small grads do not underflow
        scaler.scale(loss).backward()
        scaler.unscale_(opt)                     # back to true scale BEFORE clipping
        clip_grad_norm_(model.parameters(), max_norm)
        scaler.step(opt)                         # skips the step if any grad is inf/nan
        scaler.update()                          # halves S on overflow, doubles after 2,000 clean steps
    return loss.item()

# model = MyModel().cuda()                       # parameters stay fp32: these are the master weights
# bf16: train_step(model, opt, x, y, torch.bfloat16)
# fp16: scaler = torch.amp.GradScaler("cuda"); train_step(model, opt, x, y, torch.float16, scaler)
```

## Trade-offs
- bf16 removes loss scaling and overflow from the failure list but keeps only about three significant
  digits, so long reductions must stay in fp32; fp16 is more precise per element but needs the scaler and
  still overflows on large logits.
- 16-bit compute gives large speed-ups when the step is matmul-bound; for small models or tiny batches
  the step is launch- or bandwidth-bound and the gain shrinks to the halved activation traffic.
- Mixed precision halves activation memory and so raises the feasible micro-batch, but it leaves the
  16 bytes-per-parameter model state untouched; if weights plus optimizer state do not fit, it is the
  wrong tool.
- fp32 master weights cost 4 extra bytes per parameter and a cast per step; dropping them (pure bf16
  with stochastic rounding) saves that memory but makes convergence depend on the rounding scheme and on
  the learning rate staying above the rounding floor.
- fp8 roughly doubles tensor-core throughput over bf16 again, but demands per-tensor scaling, a choice of
  which tensors tolerate three mantissa bits, and library support; the engineering is justified at
  pre-training scale and rarely for fine-tuning.
- TF32 speeds up "fp32" matmuls several-fold with no code changes, at 10 bits of mantissa; it is a free
  win for training and a silent source of small numeric differences when results are compared with true
  fp32.

## Pitfalls
- Calling `model.half()` or `.to(torch.bfloat16)` and training that model: there are no master weights,
  so updates below the rounding unit vanish and the loss plateaus early (bf16) or NaNs appear (fp16).
  Keep parameters fp32 and use autocast.
- Clipping gradients before `scaler.unscale_()`: every step is clipped to near zero, the loss barely
  moves, and nothing raises an error.
- Attaching a `GradScaler` to a bf16 run: harmless but pointless, and a sign that an fp16 recipe was copied
  without understanding which problem the scaler solves.
- Computing a mean or variance in bf16 by hand, for example a custom RMSNorm doing `x.pow(2).mean()` on a
  bf16 tensor: the sum of thousands of small squares loses terms, and the model trains worse than the
  reference for no visible reason. Upcast to fp32 inside the op.
- Saving checkpoints with only the 16-bit weight copy, then resuming: the fp32 master state is gone, the
  restarted run follows a different trajectory and often shows a loss bump. Save fp32 masters plus
  optimizer state for resumable checkpoints; bf16-only exports are for inference.
- Comparing evaluation numbers across precisions and attributing small gaps to the model: bf16 logits
  legitimately differ from fp32 in the third digit, and a greedy-decoded answer can flip on a near tie.
- Moving an fp16 recipe to a GPU without bf16 (T4, V100) or the reverse without re-checking the scaler,
  the softmax precision and the loss computation; the symptom is a run that was stable on A100 and
  produces NaNs on T4.

## When to use
Use bf16 autocast for any training on Ampere-class or newer GPUs and on TPUs; use fp16 with dynamic loss
scaling only when the hardware forces it. In both cases keep fp32 master weights and fp32 reductions; the
frameworks default to this, and the main job is not to undo it. Enable TF32 for whatever remains in fp32.
Consider fp8 only for large pre-training runs with the budget to validate it. Measure three things: step
time (the gain should be large if the step was matmul-bound), peak memory (activations should roughly
halve), and a few hundred steps of loss against an fp32 run on a small model, which should overlap
within noise. In interviews the questions are why fp16 needs loss scaling and bf16 does not, why master
weights stay fp32, and which memory mixed precision does not reduce; the answers are exponent bits, the
rounding of small updates, and optimizer state.

## Role lens
- **Engineer:** Sets `bf16=True` (or fp16 plus a scaler) in the training config, keeps the
  unscale-clip-step order straight, upcasts custom reductions to fp32, and checks step time and memory
  before and after.
- **Architect:** Chooses GPU generations and numeric policy together: bf16 by default, fp8 where scale
  justifies the validation work, and checkpoint formats that keep fp32 master weights for resumability.
- **FDE:** Diagnoses customer runs that NaN on older cards or plateau after a naive `.half()`, and sets the
  expectation that bf16 serving can differ from the fp32 demo in the last digit.

## Check yourself
**Q:** A team trained a model stably on A100s in bf16. To cut cost they move fine-tuning to T4s, switch
the config to fp16, and the loss becomes NaN after a few hundred steps. Walk through why, and what you
would change.
**A:** A T4 has no bf16 tensor cores, so fp16 is the only 16-bit option, and fp16 trades exponent bits for
mantissa: maximum 65,504, smallest normal about 6e-5. Two different things can be going wrong. If
gradients underflow, a dynamic loss scaler fixes it; the first checks are that a scaler is actually in
the loop and that `unscale_` precedes clipping. If the *forward* overflows (attention logits, or the sum of
squares inside a norm, exceeding 65,504 once activations have grown over a few hundred steps), the scaler
cannot help: it keeps skipping steps and halving the scale, which is visible in its state. The fixes are
to keep softmax, layer norm and the loss in fp32 (autocast does this, a hand-rolled `.half()` model does
not), use an attention kernel that computes softmax in fp32, lower the peak learning rate slightly, and
confirm with a short fp32 run that the recipe itself is sound. If none of that is acceptable, the honest
answer is that this recipe wants bf16 hardware, and the saving on T4s is not real.

## Quiz
### Q: Why does fp16 training need loss scaling while bf16 training normally does not?
- [ ] fp16 has fewer mantissa bits, so gradients lose precision without scaling
- [x] fp16 has only 5 exponent bits, so many gradient values underflow to zero; bf16 keeps fp32's 8 exponent bits and the same range
- [ ] bf16 tensor cores apply scaling in hardware automatically
- [ ] Loss scaling compensates for Adam's bias correction, which bf16 does not use
> Loss scaling is a range fix, not a precision fix: it shifts small gradients up into fp16's representable
> band. bf16 actually has less precision than fp16, but its range matches fp32, so nothing underflows.

### Q: Why are the weights kept in fp32 even though forward and backward run in 16 bits?
- [ ] Because tensor cores can only read fp32 weights
- [ ] To keep checkpoints compatible with fp32 inference
- [x] Because an update much smaller than the weight's rounding unit would be rounded away in 16 bits, so the optimizer step would do nothing
- [ ] Because the optimizer state must have the same type as the weights
> With a learning rate near 1e-4, updates are routinely below 2^-8 of the weight's magnitude, which bf16
> cannot represent as a change. The fp32 master copy accumulates them; the 16-bit copy is just a cast for
> the matmuls.

### Q: Which of these does switching from fp32 to bf16 mixed precision NOT reduce?
- [ ] Activation memory per micro-batch
- [ ] Time per step on a matmul-bound model
- [x] Memory for Adam's optimizer state and the master weights
- [ ] Bytes moved per element in bandwidth-bound ops
> The master weights and both Adam moments stay fp32, so model state is still about 16 bytes per
> parameter. Sharding, 8-bit optimizers or parameter-efficient methods reduce that; mixed precision targets
> activations and compute.
