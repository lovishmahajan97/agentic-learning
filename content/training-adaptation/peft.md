---
id: peft
module: training-adaptation
name: PEFT (LoRA, QLoRA)
kind: concept
difficulty: core
est_minutes: 16
related:
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/quantization
  - training-adaptation/catastrophic-forgetting
  - training-adaptation/gradient-checkpointing
  - training-adaptation/tooling
  - python-for-ai/hugging-face
  - serving-inference/engines
  - ml-fundamentals/optimizers
sources:
  - Hu et al., 2021 — LoRA: Low-Rank Adaptation of Large Language Models
  - Dettmers et al., 2023 — QLoRA: Efficient Finetuning of Quantized LLMs
  - Aghajanyan et al., 2020 — Intrinsic Dimensionality Explains the Effectiveness of Language Model Fine-Tuning
  - Official docs: Hugging Face PEFT
---

## TL;DR
Full fine-tuning updates every weight, so a 7B model needs on the order of 100 GB of GPU memory for
weights, gradients and optimizer state. PEFT freezes the base model and trains a small set of extra
parameters instead. LoRA learns each weight update as the product of two thin matrices; QLoRA also stores
the frozen base in 4-bit so the job fits one consumer GPU. You keep most of full fine-tuning's quality and
get adapters you can swap at serving time.

## Why it exists
Fine-tuning a transformer with AdamW in mixed precision costs far more memory than the weights alone. Per
parameter you hold the bf16 weight (2 bytes), its gradient (2 bytes), and an fp32 master copy plus the two
Adam moments (12 bytes): about 16 bytes per parameter, before activations. For a 7B model that is
roughly 112 GB, which does not fit on one 80 GB GPU. "Just fine-tune it" therefore means multi-GPU
sharding, a bigger bill, and a 14 GB checkpoint for every task or customer you adapt to.

The second problem is what you do with the result. If every task produces a full copy of the model, you
cannot keep ten of them resident on one server, you cannot A/B two variants cheaply, and rollback means
moving tens of gigabytes.

The observation that makes PEFT possible is that the *change* a fine-tune makes to a pretrained weight
matrix has low intrinsic rank: most of the adaptation lives in a small subspace of the full parameter
space. So instead of letting every entry of W move, you can parametrize the update ΔW directly in that
subspace and train only the subspace. Earlier PEFT methods did this indirectly, by inserting small adapter
layers between transformer blocks or by learning "virtual tokens" prepended to the input (prefix and
prompt tuning). Both work, but adapters add inference latency and virtual tokens eat context. LoRA's trick
is that its update has exactly the shape of the original matrix, so it can be folded back in and
inference cost is unchanged.

## How it works
### LoRA: the update as a low-rank product
For a frozen weight matrix W₀ of shape d×k, LoRA writes the fine-tuned weight as W₀ + ΔW with
ΔW = B·A, where A is r×k, B is d×r, and the rank r is small (8–64 is typical, versus d = 4,096). The
forward pass becomes:

```
h = W₀x + (α / r) · B(Ax)
```

Only A and B receive gradients. Three design choices carry the reasoning:

- **B starts at zero, A at small random values.** ΔW = BA is therefore exactly zero at step 0, so training
  begins at the pretrained model's function instead of a perturbed one; the first steps cannot wreck what
  pretraining built.
- **The α/r scale.** It keeps the magnitude of the update roughly independent of r, so you can change rank
  without retuning the learning rate. (A later variant, rsLoRA, argues α/√r is the better scaling when r
  is large.)
- **Which matrices get an adapter.** The original paper adapted only the attention query and value
  projections. Later evidence, including the QLoRA paper's ablations, showed that putting adapters on *all*
  linear layers, MLP included, matters more for quality than raising r. Knowledge-heavy tasks in particular
  need the MLP capacity.

The parameter arithmetic for a Llama-2-7B-style layer (d = k = 4,096, MLP width 11,008, 32 layers) at
r = 16: each 4,096×4,096 attention matrix gets 16 × (4,096 + 4,096) ≈ 131k adapter parameters instead of
16.8M, which is under 1%. Over 32 layers × 4 attention matrices that is 16.8M; adding the three MLP
matrices (16 × (4,096 + 11,008) ≈ 242k each) brings the total to about 40M trainable parameters, roughly
0.6% of 6.7B. Their fp32 Adam state is 40M × 16 bytes ≈ 640 MB instead of ~107 GB.

Note what LoRA does *not* save: the frozen base still has to be in memory (≈13.4 GB in bf16 for 7B), and
backpropagation still flows through every layer to reach the adapters, so compute per step is only
modestly lower than full fine-tuning (the weight-gradient matmuls for frozen matrices are skipped,
roughly a third of the backward FLOPs). The win is memory for optimizer state, master weights and weight
gradients, plus a checkpoint measured in tens of megabytes.

At export time you either **merge** (W = W₀ + (α/r)BA, a dense matrix of the original shape, zero extra
latency) or keep adapters separate so a server can hold one base and many adapters, batching requests for
different adapters together; vLLM, SGLang and similar engines support this.

### QLoRA: shrinking the frozen base
LoRA leaves one large term: the 2 bytes per parameter for the frozen weights. QLoRA attacks that with
three pieces:

1. **4-bit NormalFloat (NF4).** Pretrained weights are approximately normally distributed, so instead of
   evenly spaced 4-bit levels, NF4 places its 16 levels at quantiles of a standard normal; each bin holds
   about the same probability mass, which minimizes expected rounding error for that distribution.
   Quantization is block-wise (blocks of 64 weights, one absmax scale each).
2. **Double quantization.** The per-block scales are themselves quantized to 8-bit with a second-level
   scale, because at a block size of 64 the fp32 scales alone would cost 0.5 bits per weight. This saves a
   few tenths of a bit per parameter on average.
3. **Paged optimizers.** Optimizer state lives in CUDA unified memory so it can be paged to CPU RAM
   during the memory spikes that long sequences cause, turning a hard OOM into a slowdown.

During the forward pass each 4-bit block is dequantized to bf16 on the fly and multiplied as usual; the
LoRA matrices stay in bf16 and get full-precision gradients. The base is never updated, so its
quantization error is fixed and the adapter learns around it. The paper's headline was fine-tuning a 65B
model on a single 48 GB GPU with quality on their benchmarks matching 16-bit fine-tuning. The costs are a
slower step (dequantization is extra work on every matmul) and a subtle coupling: the adapter was trained
against *this* quantized base, so merging it into a bf16 copy gives a slightly different model.

### Other PEFT methods in one line each
Adapter layers (bottleneck MLPs inserted into each block; extra latency), prefix/prompt tuning (learned
virtual tokens; consume context and are weak on small models), (IA)³ (learned per-channel rescaling
vectors), and DoRA (splits W into magnitude and direction and applies LoRA to the direction; often tracks
full fine-tuning more closely at the same rank). LoRA remains the default because it merges away.

## Example
Loading a model in NF4, attaching LoRA to all linear projections, and inspecting the result. On an
8B Llama-3-style model with r = 16 this yields on the order of 42M trainable parameters (about 0.5%).

```python
import torch
from transformers import AutoModelForCausalLM, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

name = "meta-llama/Llama-3.1-8B"                       # any causal LM; ~6 GB VRAM for the base in 4-bit
bnb = BitsAndBytesConfig(
    load_in_4bit=True, bnb_4bit_quant_type="nf4",       # NormalFloat4 for the frozen base
    bnb_4bit_use_double_quant=True,                      # quantize the quantization constants too
    bnb_4bit_compute_dtype=torch.bfloat16)               # dequantize to bf16 for the matmuls
model = AutoModelForCausalLM.from_pretrained(name, quantization_config=bnb, device_map="auto")
model = prepare_model_for_kbit_training(model)          # fp32 norms, gradient-checkpointing hooks

cfg = LoraConfig(
    r=16, lora_alpha=32, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
model = get_peft_model(model, cfg)
model.print_trainable_parameters()                      # trainable ~0.5% of total

q = model.base_model.model.model.layers[0].self_attn.q_proj
print(q.lora_A["default"].weight.shape, q.lora_B["default"].weight.shape)   # [16, 4096], [4096, 16]
print(float(q.lora_B["default"].weight.abs().max()))     # 0.0: training starts at the base model

# ... train with TRL's SFTTrainer or a plain loop; LoRA LRs of 1e-4 to 2e-4 are typical ...
model.save_pretrained("adapter/")                        # only A and B: tens of MB, not 16 GB
# To ship without the peft runtime: load the base in bf16, attach the adapter, then
# merged = model.merge_and_unload(); merged.save_pretrained("merged/")  -> re-evaluate after merging
```

## Trade-offs
- Memory falls by an order of magnitude and checkpoints by three, but per-step compute barely drops
  because backprop still traverses the frozen base; PEFT makes fine-tuning *fit*, not fast.
- LoRA learns less and forgets less than full fine-tuning: on narrow tasks that is a feature (base
  capabilities survive), but for large distribution shifts such as a new language or heavy code
  pretraining, full fine-tuning or a much higher rank on all layers pulls ahead.
- QLoRA cuts base-model memory roughly 3–4× relative to bf16 at the cost of slower steps and a fixed
  quantization error; when you have the GPU memory for bf16 LoRA, it is the simpler and faster choice.
- Keeping adapters unmerged enables one base serving many tenants, but each request pays a small extra
  matmul and the engine must support multi-adapter batching; merging gives zero overhead and one model
  per adapter.
- Higher rank adds capacity with diminishing returns; past the point where training loss stops improving
  with r, extra rank only adds memory and overfitting risk.

## Pitfalls
- Copying the learning rate from a full fine-tuning recipe (≈1e-5): LoRA parameters are tiny and need a
  larger LR; the symptom is a training loss that barely moves across epochs.
- Adapting only `q_proj` and `v_proj` because an old tutorial did: tasks needing new knowledge or formats
  underfit; the symptom is good style, wrong facts, fixed by targeting the MLP projections too.
- Merging a QLoRA adapter into a bf16 base and shipping without re-evaluation: the adapter compensated
  for a specific quantized base, so the merged model drifts slightly; always re-run evals after merging
  or after re-quantizing the merged model to GPTQ/AWQ for serving.
- Training with one chat template and serving with another: the adapter learned token patterns around
  the template, so the symptom is a model that rambles, never emits the end token or ignores the system
  prompt.
- Shipping an adapter without pinning the exact base model revision: adapters are deltas against specific
  weights, and a silently updated base produces a quietly different model.
- Forgetting `prepare_model_for_kbit_training` or input-gradient hooks when enabling gradient
  checkpointing on a frozen base: the symptom is "element 0 of tensors does not require grad" or an
  adapter whose loss never changes.

## When to use
Reach for LoRA whenever you fine-tune a model larger than a few hundred million parameters and the task is
a style, format, domain-vocabulary or behaviour shift: support-ticket tone, structured output, a
house-specific classifier, tool-call formatting. Choose QLoRA when the bf16 base does not fit your GPU
alongside activations, and accept the slower step. Prefer full fine-tuning only when you have the hardware
and the task is a large shift that LoRA demonstrably underfits on your eval set. Measure three things:
task metric versus the base model, a held-out general benchmark to catch forgetting, and the merged
model's metric versus the unmerged one. In day-to-day work PEFT shows up as a `LoraConfig` block, an
adapter directory in object storage, and a serving decision about merging. In interviews, be ready to
derive the 16-bytes-per-parameter figure, explain why B is zero-initialized, and say where QLoRA's
savings come from and what they cost.

## Role lens
- **Engineer:** Writes the `LoraConfig`, picks rank and target modules, sets a LoRA-appropriate learning
  rate, and verifies the merged checkpoint matches the adapter's eval numbers before handing it to serving.
- **Architect:** Decides between one merged model per task and one base with hot-swapped adapters, based
  on tenant count, GPU budget and latency, and owns the base-model version pinning policy.
- **FDE:** Fine-tunes on a customer's data inside their VPC on whatever GPU they have, often a single
  24–48 GB card, which is precisely the regime QLoRA was built for.

## Check yourself
**Q:** You have one 24 GB GPU and 20k support transcripts, and you need to adapt an 8B model to the
customer's tone and ticket format. Walk through what fits in memory and what you would choose.
**A:** Full fine-tuning is out: 8B × 16 bytes ≈ 128 GB for weights, gradients and Adam state. bf16 LoRA
needs the frozen base at ≈16 GB plus adapter state (tens of MB) plus activations; with gradient
checkpointing and short sequences it might squeeze into 24 GB but it is tight. QLoRA stores the base in
NF4 at roughly 5–6 GB, leaving ample room for activations at longer sequence lengths, so that is the
practical choice. I would target all linear projections at r = 16, LR around 1e-4 to 2e-4, train one to
three epochs while watching a held-out slice of transcripts and a small general benchmark for forgetting,
then merge into a bf16 base for serving and re-run the evals on the merged model, because the adapter was
trained against the quantized base.

## Quiz
### Q: Why is LoRA's B matrix initialized to zero while A is random?
- [ ] Zero initialization keeps the adapter sparse so it can be stored compactly
- [x] So the update BA is zero at step 0 and training starts from the exact pretrained function
- [ ] Because gradients cannot flow through two randomly initialized matrices
- [ ] To make the rank r adjustable without changing α
> With B = 0 the product BA vanishes, so the model behaves identically to the base model before training
> and early updates cannot damage it. Random A still gives B a non-degenerate gradient, so learning proceeds.

### Q: Where does QLoRA's memory saving over plain LoRA come from?
- [ ] It trains fewer adapter parameters by using a smaller rank
- [ ] It drops the optimizer state entirely
- [x] It stores the frozen base weights in 4-bit NF4 and dequantizes them on the fly during matmuls
- [ ] It offloads activations to CPU memory
> LoRA already removed optimizer state and gradients for the base; what remained was the 2-byte-per-weight
> frozen model. QLoRA shrinks that term to about half a byte per weight; paged optimizers only handle
> transient spikes.

### Q: What happens to inference latency after a LoRA adapter is merged into the base weights?
- [ ] It increases slightly because of the extra rank-r projection
- [ ] It decreases because the merged matrix is lower rank
- [x] It is unchanged, because W₀ + BA is a dense matrix of the original shape
- [ ] It depends on whether the base was quantized during training
> Merging adds BA into W₀, producing one dense d×k matrix, so the forward pass performs exactly the same
> matmul as the base model. Only an *unmerged* adapter adds the extra low-rank computation.
