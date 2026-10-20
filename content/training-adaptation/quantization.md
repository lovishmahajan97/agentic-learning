---
id: quantization
module: training-adaptation
name: Quantization (INT8/INT4, GPTQ, AWQ, GGUF)
kind: concept
difficulty: core
est_minutes: 18
status: reviewed
related:
  - training-adaptation/peft
  - training-adaptation/pruning
  - serving-inference/quantization-quality-evaluation
  - serving-inference/engines
  - serving-inference/gpu-memory-optimization
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
  - infra-capacity-economics/edge-ai-deployment
  - ml-fundamentals/mixed-precision
sources:
  - Dettmers et al., 2022 — LLM.int8(): 8-bit Matrix Multiplication for Transformers at Scale
  - Frantar et al., 2022 — GPTQ: Accurate Post-Training Quantization for Generative Pre-trained Transformers
  - Lin et al., 2023 — AWQ: Activation-aware Weight Quantization for LLM Compression and Acceleration
  - Official docs: llama.cpp (GGUF)
---

## TL;DR
Quantization stores weights, and sometimes activations, in fewer bits than the 16-bit training format:
INT8, INT4 or small floats. It pays off because LLM decoding is limited by bytes read per token, not by
arithmetic, so fewer bytes per weight means a model that fits cheaper hardware and decodes faster. GPTQ
and AWQ are calibration-based recipes for choosing 4-bit values that minimise each layer's output error;
GGUF is the llama.cpp file format for shipping such models to CPUs and laptops.

## Why it exists
Two numbers drive everything. A 70B-parameter model in bf16 is 140 GB of weights, which does not fit on
one 80 GB GPU; at 4 bits it is about 35–40 GB and does. A 7B model goes from 14 GB to about 4 GB, which is
the difference between a data-centre card and a laptop. That is the memory argument.

The speed argument is subtler. During decode the GPU reads every weight once per generated token, and the
arithmetic per weight is tiny (one multiply-add per token at batch size 1), so time per token is roughly
bytes of weights divided by memory bandwidth. Halve the bytes and small-batch decoding nearly doubles in
speed, with no change to the maths. Quantization is a bandwidth optimisation wearing a compression costume.

The naive alternatives both fail. "Train a smaller model instead" loses more quality than quantizing a
bigger one: at a fixed memory budget, a 4-bit model with roughly four times the parameters generally
beats the 16-bit model that fills the same bytes, which is the core finding of the k-bit inference
scaling-law work (Dettmers & Zettlemoyer) and the reason 4-bit became the default for local models. "Just cast to int8 with one scale per tensor" fails because
of outliers: transformers past a few billion parameters develop a handful of activation channels with
magnitudes tens of times larger than the rest, and one such value sets the scale for the whole tensor, so
everything else rounds to zero or one level. Every modern method is a strategy for handling that range
problem: finer-grained scales, outlier-aware scaling, or error compensation.

## How it works
### The arithmetic of a quantizer
Symmetric *b*-bit quantization maps a real value *x* to an integer *q* with one scale *s*:

```
s = max|x| / (2^(b−1) − 1)        q = clamp(round(x / s), −2^(b−1), 2^(b−1) − 1)        x̂ = s · q
```

INT8 gives 127 levels on each side of zero; INT4 gives only −8…7. The asymmetric (affine) variant adds a
zero-point *z* so the grid covers [min, max] exactly, which matters for skewed distributions and is the
usual choice for 4-bit weights. Rounding error is roughly uniform in [−*s*/2, *s*/2] with mean-squared
error *s*²/12, so error grows with the range the scale has to cover. That single fact explains the
granularity ladder:

- **Per-tensor:** one scale. Cheapest, and ruined by a single outlier.
- **Per-channel:** one scale per output row. Standard for INT8.
- **Per-group:** one scale (and zero-point) per *g* consecutive weights along the input dimension,
  typically *g* = 128 or 32. An outlier now only degrades its own group. The price is overhead: INT4 with
  *g* = 128, an fp16 scale and a 4-bit zero-point costs 4 + 20/128 ≈ 4.16 bits per weight; *g* = 32 costs
  about 4.6. "4-bit" models are never exactly 4 bits.

### Weights only, or weights and activations
**W4A16 / W8A16** (weight-only) keeps weights in low-bit storage and dequantizes them to fp16/bf16 inside
the matmul kernel. Memory and bandwidth drop; compute does not, because the multiply still happens in
16-bit. This helps decode (bandwidth-bound) and does nothing for prefill (compute-bound); at large batch
sizes the dequantization overhead can make a W4A16 model slower than the fp16 original. GPTQ and AWQ
live here.

**W8A8** quantizes activations too, so the matmul runs on INT8 (or FP8) tensor cores at roughly twice the
fp16 throughput. Activations are harder than weights because of the outlier channels and because they
change with every input. LLM.int8() handles this by pulling the few outlier columns out into an fp16
side-matmul; SmoothQuant migrates activation range into the weights with a per-channel scale before
quantizing both. On GPUs with native FP8 (E4M3) the problem eases, because a float format has a built-in
exponent and copes with dynamic range, so FP8 weights+activations+KV cache has become the default
"almost free" setting for throughput serving on that hardware.

The **KV cache** can be quantized too (8-bit is common), which doubles the number of concurrent sequences
at long context; see the KV cache lesson for why that is often the binding constraint rather than weights.

### Post-training quantization versus quantization-aware training
PTQ takes a trained model and a small calibration set (a few hundred sequences) and produces a quantized
model in minutes to hours; GPTQ and AWQ are PTQ. QAT simulates quantization in the forward pass during
fine-tuning and uses a straight-through estimator for the backward pass, so the weights learn to live on
the grid; it is more accurate at extreme bit-widths (2–3 bits, or small models) but costs a training run.
QLoRA sits in between: a frozen 4-bit base with trainable adapters that learn around its rounding error
(see the PEFT lesson).

### GPTQ: compensate the error you just made
Round-to-nearest treats every weight independently. GPTQ instead minimises the layer's reconstruction
error ‖**WX** − **ŴX**‖² over calibration inputs **X**, using the second-order machinery of Optimal Brain
Surgeon. The Hessian of that objective is **H** = 2**XXᵀ**, which depends on the inputs, not on **W**.
GPTQ quantizes a weight matrix one column at a time: round column *j*, compute the error, and update all
still-unquantized columns by a multiple of the corresponding row of **H⁻¹** so they absorb the error.
Doing this in a fixed column order with a Cholesky factor of **H⁻¹** and lazy batched updates made it
numerically stable and fast enough to quantize a 175B model in a few GPU-hours. The result is 4-bit (and
usable 3-bit) weights with a small perplexity increase. Because it fits **W** to the calibration inputs,
GPTQ is somewhat sensitive to the calibration data's domain.

### AWQ: protect the weights that matter
AWQ starts from a measurement: not all weights are equal, and the ones that matter are identified by the
*activations* flowing through them, not by their own magnitude. A small weight on a channel that always
carries large activations contributes a lot to the output, so its rounding error hurts. Keeping that
~1% of channels in fp16 works but produces a mixed-precision layout hardware dislikes. AWQ's trick is
mathematically free: multiply a salient weight channel by *s* > 1 and divide the matching activation
channel by *s*; the product is unchanged, but the weight is now larger relative to the rounding step, so
its relative error falls. The per-channel scales are chosen by a small grid search
(*s* = mean activation magnitude^α, α ∈ [0, 1]) minimising output error on calibration data. There is
no gradient and no weight reconstruction, so AWQ runs fast, overfits the calibration set less than GPTQ,
and transfers well across domains and modalities. Both ship with fast INT4 GPU kernels (Marlin, ExLlama)
in vLLM, SGLang and TensorRT-LLM.

### GGUF: a container, not a method
GGUF is the single-file format used by llama.cpp (it replaced the older GGML format in 2023). One file
holds the tensors, their quantization type per tensor, and all metadata: architecture hyperparameters,
the tokenizer vocabulary and the chat template, laid out so the file can be memory-mapped and used
directly. Its quantization types encode a bit-width and a block scheme: `Q8_0` and `Q4_0` use blocks of 32
with one fp16 scale (about 8.5 and 4.5 bits per weight); the K-quants (`Q4_K_M`, `Q5_K_M`, `Q6_K`) use
256-weight super-blocks with compact sub-block scales, and the `S`/`M`/`L` suffix is a mixing recipe that
keeps the most sensitive tensors (attention value/output and parts of the MLP) at higher precision; the
I-quants (`IQ2_XXS`…`IQ4_XS`) use non-uniform codebooks and an optional importance matrix from
calibration data to push below 4 bits. GGUF exists because llama.cpp's audience is CPUs, Apple silicon and
mixed CPU/GPU machines where tensors are offloaded layer by layer, and the block layouts are designed for
SIMD dequantization there. On a GPU server, GPTQ/AWQ/FP8 through a batching engine will usually
out-throughput it.

## Example
Why the granularity ladder matters, in NumPy. A Gaussian weight matrix gets one outlier input channel, as
real transformer weights and (more severely) activations do. The relative output error on random
activations shows per-tensor INT4 collapsing, per-channel barely helping, and per-group scales rescuing it.

```python
import numpy as np
rng = np.random.default_rng(0)
W = rng.normal(0, 0.02, size=(4096, 4096)).astype(np.float32)   # [out, in]
W[:, 7] *= 40                                                   # one outlier input channel

def quant_sym(x, bits, axis=None):
    """Symmetric round-to-nearest with one scale per slice along `axis` (None = per-tensor)."""
    qmax = 2 ** (bits - 1) - 1
    s = np.max(np.abs(x), axis=axis, keepdims=True) / qmax
    q = np.clip(np.round(x / s), -qmax - 1, qmax)
    return (q * s).astype(np.float32)

def quant_grouped(x, bits, g):
    rows, cols = x.shape
    return quant_sym(x.reshape(rows, cols // g, g), bits, axis=2).reshape(rows, cols)

X = rng.normal(size=(512, 4096)).astype(np.float32)             # calibration activations
ref = X @ W.T
for name, W_hat in [("int8 per-tensor", quant_sym(W, 8)),
                    ("int4 per-tensor", quant_sym(W, 4)),
                    ("int4 per-channel", quant_sym(W, 4, axis=1)),
                    ("int4 group=128", quant_grouped(W, 4, 128)),
                    ("int4 group=32", quant_grouped(W, 4, 32))]:
    err = np.linalg.norm(X @ W_hat.T - ref) / np.linalg.norm(ref)
    print(f"{name:17s} relative output error = {err:.3f}")
# Prints roughly 0.32 / 0.85 / 0.68 / 0.16 / 0.10 (int8 per-tensor already hurts with a 40x outlier).
# Per-channel still fails here because every output row contains the outlier column, so every
# row's scale is set by it; grouping isolates the damage to 1/32 (g=128) or 1/128 (g=32) of the weights.
# GPTQ would additionally push each group's rounding error into its neighbours; AWQ would rescale
# channel 7 before rounding. Bits per weight with an fp16 scale + 4-bit zero-point per group, as in
# the ladder above: 8 / 4 / 4 / ~4.16 / ~4.6 respectively (this symmetric toy stores no zero-point).
```

## Trade-offs
- Memory and decode speed improve roughly in proportion to bits saved, while quality loss is small at 8-bit
  and usually modest at 4-bit for models of 7B and up; the loss grows for small models and for
  precision-sensitive tasks such as maths, code and long multi-step reasoning, where it must be measured.
- Weight-only INT4 accelerates small-batch decode but not prefill, and at high batch its dequantization
  overhead can lose to fp16 or FP8 W8A8; the right choice flips with batch size and with whether your
  workload is prompt-heavy (RAG) or generation-heavy (chat).
- Smaller groups cut error but add bits per weight and kernel complexity; *g* = 128 is the usual
  compromise, and *g* = 32 is worth it mainly below 4 bits or on small models.
- PTQ is cheap and fast but depends on calibration data matching deployment; QAT is more robust at extreme
  bit-widths but costs a training run and an engineering pipeline most teams do not have.
- GGUF runs on nearly anything with partial GPU offload and a single portable file, but a batching GPU
  engine with AWQ/GPTQ/FP8 kernels delivers far higher throughput per dollar for a multi-user service.

## Pitfalls
- Evaluating only perplexity on a generic text set: perplexity can move by 0.1 while instruction
  following, maths or a minority language regress sharply; the symptom is a clean quantization report and
  a stream of user complaints. Run your task evals before and after.
- Calibrating a code or multilingual model on English web text: the symptom is quality that looks fine on
  general prompts and degrades exactly in the deployment domain; GPTQ is more sensitive than AWQ here.
- Expecting INT4 to cut time-to-first-token on long-prompt workloads: prefill is compute-bound and
  weight-only quantization does not reduce FLOPs; the symptom is unchanged or worse TTFT.
- Sizing hardware at 4.0 bits per weight: scales and zero-points make it 4.2–5.5 depending on scheme, and
  the KV cache is extra; the symptom is an OOM on a card that "should have fit".
- Re-quantizing an already quantized checkpoint (GPTQ → GGUF, or a merged QLoRA model) instead of starting
  from the highest-precision weights: errors compound; the symptom is a model noticeably worse than the
  same scheme applied to the bf16 original.
- Losing or mangling tokenizer and chat-template metadata during GGUF conversion: the symptom is a model
  that rambles, never emits the end-of-turn token or ignores the system prompt despite correct weights.

## When to use
On GPU servers, start with FP8 if the hardware supports it: near-zero quality cost, faster prefill and
decode. Go to INT4 AWQ or GPTQ when a model must fit a smaller or single GPU, or when decode latency at
low batch dominates, and verify on your evals. Choose GGUF when the target is a laptop, a CPU box, an
edge device or a developer's machine, picking `Q4_K_M` or `Q5_K_M` as sensible defaults and moving up when
quality evals demand it. Use QLoRA, not a serving format, when the goal is fine-tuning on a small GPU.
Measure three things every time: task evals against the unquantized model, tokens per second at your real
batch size, and total memory including the KV cache at your context length. In interviews, be able to
derive bits per weight for a grouped scheme, explain why outliers break per-tensor scaling, and contrast
how GPTQ and AWQ decide what to protect.

## Role lens
- **Engineer:** Picks the quantization scheme per deployment target, runs the calibration and the task
  evals, and checks the serving engine actually has a fast kernel for that format before shipping.
- **Architect:** Owns the fit between model size, precision, GPU class and concurrency targets, and sets the
  rule that no quantized model ships without an eval delta against its full-precision parent.
- **FDE:** Gets a customer's chosen model running on the hardware they already own, often a single
  24–48 GB card or a CPU-only box, by choosing AWQ/GPTQ or a GGUF quant and proving quality on their data.

## Check yourself
**Q:** You must serve a 70B-class model (80 layers, 8 KV heads, head dim 128) on a single 80 GB GPU with 8k
context. Which precision do you pick, how much room is left for the KV cache, and what else do you check?
**A:** bf16 needs 140 GB, so it is out; INT8 needs about 70 GB, leaving almost nothing for activations and
cache; INT4 with group 128 is about 4.2 bits per weight, roughly 37 GB, leaving around 40 GB. The KV cache
per token in fp16 is 2 × 80 × 8 × 128 × 2 ≈ 0.33 MB, so an 8k sequence costs about 2.6 GB and about 15
concurrent sequences fit, more with an 8-bit cache. I would use AWQ (or GPTQ with in-domain calibration),
run the task evals against the bf16 model on a second machine, check that the engine has a fast INT4 kernel
for this architecture, and confirm the workload is decode-heavy, because INT4 does nothing for prefill. If
quality drops too far, the honest options are two GPUs with FP8 or a smaller model.

## Quiz
### Q: Why does weight-only INT4 quantization speed up decoding but not prefill?
- [ ] Prefill runs on the CPU in most engines
- [x] Decode is memory-bandwidth-bound, so reading fewer weight bytes helps; prefill is compute-bound and the matmul still runs in 16-bit
- [ ] The KV cache is written in fp16 during prefill
- [ ] INT4 kernels only support batch size 1
> Weight-only schemes dequantize to 16-bit inside the kernel, so FLOPs are unchanged. Decode reads all
> weights per token and benefits from fewer bytes; prefill is dominated by arithmetic over many tokens.

### Q: How does AWQ decide which weights deserve protection?
- [ ] It keeps the largest-magnitude weights in fp16
- [ ] It uses the Hessian of the reconstruction loss to rank weights
- [x] It looks at activation magnitudes per input channel and scales up the channels that carry large activations before rounding
- [ ] It trains the weights with a straight-through estimator
> AWQ's observation is that saliency comes from the activations a weight multiplies, not the weight's own
> size. Rescaling those channels is output-preserving and shrinks their relative rounding error.

### Q: What exactly is GGUF, and what does a GGUF file contain?
- [ ] A 4-bit quantization algorithm that replaces GPTQ
- [x] A single-file container used by llama.cpp holding per-tensor quantized weights plus tokenizer and architecture metadata
- [ ] NVIDIA's FP8 format for Hopper GPUs
- [ ] A calibration dataset for post-training quantization
> GGUF is a file format: it bundles tensors in block-quantized types such as Q4_K_M with everything needed
> to run them. The quantization recipes are chosen when converting into it, not defined by it.
