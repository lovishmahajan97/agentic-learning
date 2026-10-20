---
id: peft
module: training-adaptation
name: PEFT (LoRA, QLoRA)
kind: concept
difficulty: core
est_minutes: 16
status: crisp-verified
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
PEFT freezes the base model and trains a small set of extra parameters. LoRA learns each weight update as
two thin matrices; QLoRA also stores the frozen base in 4-bit, so the job fits one consumer GPU, keeps most of full
fine-tuning's quality, and yields swappable adapters.

## Why it exists
- **Full fine-tuning does not fit.** Updating every weight of a 7B model needs on the order of 100 GB of
  GPU memory for weights, gradients and optimizer state.
- **AdamW in mixed precision costs ~16 bytes per parameter.** The bf16 weight (2 bytes), its gradient
  (2 bytes), and an fp32 master copy plus the two Adam moments (12 bytes), before activations. For 7B that
  is roughly 112 GB, which does not fit on one 80 GB GPU.
- **"Just fine-tune it" is expensive twice.** It means multi-GPU sharding, a bigger bill, and a 14 GB
  checkpoint for every task or customer. You cannot keep ten copies resident on one server, cannot A/B two
  variants cheaply, and rollback means moving tens of gigabytes.
- **The update has low intrinsic rank.** The *change* a fine-tune makes to a pretrained matrix lives
  mostly in a small subspace, so you can parametrize ΔW directly in that subspace and train only it.
- **LoRA beats earlier PEFT on serving cost.** Earlier methods work, but adapter layers between blocks
  add inference latency and learned "virtual tokens" (prefix and prompt tuning) eat context. LoRA's update has exactly the shape of
  the original matrix, so it folds back in and inference cost is unchanged, while keeping most of full
  fine-tuning's quality.

## How it works
### LoRA: the update as a low-rank product
For a frozen weight W₀ of shape d×k, the fine-tuned weight is W₀ + ΔW with ΔW = B·A:
- A is r×k, B is d×r, and the rank r is small (8–64 is typical, versus d = 4,096).
- Only A and B receive gradients. The forward pass becomes:

```
h = W₀x + (α / r) · B(Ax)
```

Three design choices, each with its reason:
- **B starts at zero, A at small random values.** ΔW = BA is exactly zero at step 0, so training begins
  at the pretrained model's function; the first steps cannot wreck what pretraining built.
- **The α/r scale.** Keeps the update's magnitude roughly independent of r, so you can change rank
  without retuning the learning rate. (rsLoRA argues α/√r is better when r is large.)
- **Which matrices get an adapter.** The original paper adapted only attention query and value. Later
  evidence, including the QLoRA paper's ablations, showed adapters on *all* linear layers, MLP included,
  matter more for quality than raising r. Knowledge-heavy tasks especially need the MLP capacity.

### Parameter arithmetic (Llama-2-7B-style, r = 16)
Layer shape: d = k = 4,096, MLP width 11,008, 32 layers.

| Item | Count |
|---|---|
| One 4,096×4,096 attention matrix | 16.8M weights |
| Its adapter: 16 × (4,096 + 4,096) | ≈ 131k (under 1%) |
| 32 layers × 4 attention matrices | 16.8M adapter params |
| Plus 3 MLP matrices: 16 × (4,096 + 11,008) | ≈ 242k each |
| Total trainable | about 40M, roughly 0.6% of 6.7B |
| fp32 Adam state | 40M × 16 bytes ≈ 640 MB instead of ~107 GB |

### What LoRA does *not* save
- **The frozen base stays in memory.** ≈13.4 GB in bf16 for 7B.
- **Compute per step drops only modestly.** Backprop still flows through every layer to reach the
  adapters. Only the weight-gradient matmuls for frozen matrices are skipped: about half of the backward
  FLOPs, or a third of the forward-plus-backward total.
- **Activation memory is unchanged.** Every frozen layer's activations are needed to backpropagate to the
  adapters below it. At long sequence lengths activations, not weights, OOM, which is why LoRA recipes
  almost always switch on gradient checkpointing.
- **The win:** optimizer state, master weights and weight gradients, plus a checkpoint of tens to a few
  hundred megabytes (40M adapter parameters are about 80 MB in bf16 or 160 MB in fp32, against 13.4 GB).

### Why rank 16 suffices
- A gradient step on W₀ for a narrow task mostly pushes in a few consistent directions.
- LoRA bets the full ΔW is well approximated by its top r singular directions.
- The bet holds for style, format and behaviour shifts and weakens as the task demands genuinely new
  information. That condition decides LoRA versus full fine-tuning.

### Export: merge or keep separate
- **Merge:** W = W₀ + (α/r)BA, a dense matrix of the original shape, zero extra latency.
- **Keep separate:** one base and many adapters on a server, batching requests for different adapters
  together; vLLM, SGLang and similar engines support this.

### QLoRA: shrinking the frozen base
LoRA leaves one large term: 2 bytes per parameter for the frozen weights. QLoRA attacks it with three
pieces:
1. **4-bit NormalFloat (NF4).** Pretrained weights are roughly normal, so NF4 puts its 16 levels at
   quantiles of a standard normal instead of evenly spaced. Each bin holds about equal probability mass,
   minimizing expected rounding error. Quantization is block-wise (blocks of 64 weights, one absmax scale
   each).
2. **Double quantization.** The per-block scales are quantized to 8-bit with a second-level scale,
   because at block size 64 the fp32 scales alone cost 0.5 bits per weight. Saves a few tenths of a bit
   per parameter on average.
3. **Paged optimizers.** Optimizer state lives in CUDA unified memory and pages to CPU RAM during the
   memory spikes long sequences cause, turning a hard OOM into a slowdown.

During training:
- Each 4-bit block is dequantized to bf16 on the fly and multiplied as usual; LoRA matrices stay in bf16
  with full-precision gradients.
- The base is never updated, so its quantization error is fixed and the adapter learns around it.
- **Headline:** fine-tuning a 65B model on a single 48 GB GPU, matching 16-bit fine-tuning on the paper's
  benchmarks.
- **Costs:** a slower step (dequantization on every matmul), and a coupling: the adapter was trained
  against *this* quantized base, so merging into a bf16 copy gives a slightly different model.

### Other PEFT methods
| Method | Idea | Catch |
|---|---|---|
| Adapter layers | bottleneck MLPs inserted into each block | extra latency |
| Prefix / prompt tuning | learned virtual tokens | consume context; weak on small models |
| (IA)³ | learned per-channel rescaling vectors | small capacity |
| DoRA | split W into magnitude and direction, LoRA on direction | often tracks full FT closer at same rank |

LoRA remains the default because it merges away.

## Diagram
The input feeds two paths: the frozen base matrix and the trainable low-rank pair A then B. Only A and B
get gradients; after training the update is either merged into W₀ or kept as a swappable adapter.

```mermaid
flowchart TD
    X["Input x"] --> W["Frozen W0: bf16 in LoRA, NF4 in QLoRA"]
    X --> A["A: r x k, trainable, small random init"]
    A --> B["B: d x r, trainable, starts at zero"]
    W --> S["h = W0x + (alpha / r) B(Ax)"]
    B -->|"scale by alpha / r"| S
    S --> L["Loss: gradients flow only to A and B"]
    L -.->|"update"| A
    L -.->|"update"| B
    B -->|"after training"| M["Merge: W = W0 + (alpha / r)BA, zero extra latency"]
    B -->|"after training"| K["Keep separate: one base serves many adapters"]
```

## Examples
### QLoRA setup with adapters on all linear layers
Load in NF4, attach LoRA to all linear projections, inspect. On an 8B Llama-3-style model with r = 16
this yields on the order of 42M trainable parameters (about 0.5%).

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
model.save_pretrained("adapter/")                        # only A and B: ~85 MB bf16 / ~170 MB fp32, not 16 GB
# To ship without the peft runtime: load the base in bf16, attach the adapter, then
# merged = model.merge_and_unload(); merged.save_pretrained("merged/")  -> re-evaluate after merging
```

### Memory calculator: full FT vs LoRA vs QLoRA
Pure Python, runnable anywhere. Static memory only; activations come on top.

```python
def ft_memory_gb(params_b, method, trainable_frac=0.006):
    p = params_b * 1e9
    if method == "full":                     # bf16 weight + grad, fp32 master + 2 Adam moments
        return p * 16 / 1e9
    base = p * (2 if method == "lora" else 0.5)   # bf16 vs ~4-bit NF4 frozen base
    adapter = p * trainable_frac * 16             # adapters keep the full 16 bytes/param
    return (base + adapter) / 1e9

for size in (7, 8, 70):
    row = {m: round(ft_memory_gb(size, m), 1) for m in ("full", "lora", "qlora")}
    print(size, "B:", row, "GB before activations")
# 7 B: {'full': 112.0, 'lora': 14.7, 'qlora': 4.2} GB before activations
# 8 B: {'full': 128.0, 'lora': 16.8, 'qlora': 4.8} GB before activations
# 70 B: {'full': 1120.0, 'lora': 146.7, 'qlora': 41.7} GB before activations
```

Reading the output (the 0.5 bytes per weight ignores scale overhead, so real NF4 bases run a little
larger):

| Model | Fits one 24 GB card? | Fits one 80 GB card? |
|---|---|---|
| 8B full FT | no | no |
| 8B bf16 LoRA | tight, with gradient checkpointing | yes |
| 8B QLoRA | yes, with room for activations | yes |
| 70B QLoRA | no | yes |

## Production patterns
- **One base, many adapters.** When tenants or tasks share a base, serve unmerged adapters: vLLM's
  `--enable-lora` with `--max-loras` and `--max-lora-rank` sized to your adapters. Monitor per-adapter
  latency and adapter load/evict churn.
- **Pin the base revision with the adapter.** Load the base with `revision="<commit sha>"` and store
  that sha next to the adapter directory, because an adapter is a delta against exact weights. Alert in
  CI if the serving base differs from the training base.
- **Merge-then-eval release gate.** Before shipping a merged or re-quantized model, re-run the task eval
  and a small general benchmark on the final artifact and block release if it drops below the
  unmerged adapter's numbers. Track task metric, general metric and the merged-versus-unmerged gap.
- **Small rank sweep, all linear layers.** Fix `target_modules` to all projections and sweep r over
  8, 16 and 32 with the LR held near 1e-4 to 2e-4; keep the smallest r where held-out loss stops
  improving. Watch train-versus-eval loss for overfitting.
- **Gradient checkpointing by default for long sequences.** Set `gradient_checkpointing=True` in the
  trainer arguments, because activations, not adapters, are what OOM. Monitor peak memory and step time.

## Trade-offs
- **Memory vs speed.** Memory falls by an order of magnitude and checkpoints by about two, but per-step
  compute barely drops because backprop still traverses the frozen base; PEFT makes fine-tuning *fit*,
  not fast.
- **Learns less, forgets less.** On narrow tasks that is a feature (base capabilities survive). For large
  shifts such as a new language or heavy code pretraining, full fine-tuning or a much higher rank on all
  layers pulls ahead.
- **QLoRA vs bf16 LoRA.** QLoRA cuts base-model memory roughly 3–4× relative to bf16, at the cost of
  slower steps and a fixed quantization error. When bf16 LoRA fits, it is simpler and faster.
- **Unmerged vs merged adapters.** Unmerged lets one base serve many tenants, but each request pays a
  small extra matmul and the engine must support multi-adapter batching. Merged gives zero overhead and
  one model per adapter.
- **Higher rank vs diminishing returns.** Past the point where training loss stops improving with r,
  extra rank only adds memory and overfitting risk.

## Pitfalls
- **Copying a full fine-tuning learning rate (≈1e-5).** LoRA parameters are tiny and need a larger LR.
  Symptom: training loss barely moves across epochs. Fix: start in the 1e-4 to 2e-4 range.
- **Adapting only `q_proj` and `v_proj` because an old tutorial did.** Tasks needing new knowledge or
  formats underfit. Symptom: good style, wrong facts. Fix: target the MLP projections too.
- **Merging a QLoRA adapter into a bf16 base without re-evaluation.** The adapter compensated for a
  specific quantized base, so the merged model drifts slightly. Re-run evals after merging, or after
  re-quantizing the merged model to GPTQ/AWQ for serving.
- **Training with one chat template, serving with another.** The adapter learned token patterns around
  the template. Symptom: rambling, never emitting the end token, or ignoring the system prompt.
- **Not pinning the exact base model revision.** Adapters are deltas against specific weights, so a
  silently updated base produces a quietly different model.
- **Forgetting `prepare_model_for_kbit_training` or input-gradient hooks with gradient checkpointing on
  a frozen base.** Symptom: "element 0 of tensors does not require grad", or an adapter whose loss never
  changes.

## When to use
- **Use LoRA when:** fine-tuning a model larger than a few hundred million parameters for a style,
  format, domain-vocabulary or behaviour shift: support-ticket tone, structured output, a house-specific
  classifier, tool-call formatting.
- **Use QLoRA when:** the bf16 base does not fit your GPU alongside activations; accept the slower step.
- **Use full fine-tuning when:** you have the hardware and the task is a large shift that LoRA
  demonstrably underfits on your eval set.
- **Measure:** task metric versus the base model, a held-out general benchmark to catch forgetting, and
  the merged model's metric versus the unmerged one.
- **Day to day:** PEFT shows up as a `LoraConfig` block, an adapter directory in object storage, and a
  serving decision about merging.
- **Interviews:** derive the 16-bytes-per-parameter figure, explain why B is zero-initialized, and say
  where QLoRA's savings come from and what they cost.

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

## Interview questions
### What is LoRA, and why does a rank-16 update work at all?
- **Definition:** LoRA freezes W₀ and learns the update as ΔW = B·A, with A of shape r×k and B of shape
  d×r, so only a tiny fraction of parameters train and the update can be merged back into W₀.
- **Why it works:** the change a fine-tune makes to a pretrained matrix has low intrinsic rank, so the
  top r directions capture most of it for style, format and behaviour shifts.
- **Init detail:** B starts at zero, so training begins exactly at the base model's function.

### Your LoRA run's training loss barely moves after an epoch. How do you debug it?
- **Learning rate first:** a full fine-tuning LR near 1e-5 is too small for LoRA; move to the typical
  1e-4 to 2e-4 range.
- **Check gradients reach the adapters:** with a k-bit base and gradient checkpointing, a missing
  `prepare_model_for_kbit_training` call or input-gradient hook leaves the adapter's loss flat.
- **Check capacity:** if only `q_proj` and `v_proj` are targeted, add the MLP projections before raising r.

### When would you not use LoRA, and when would you pick bf16 LoRA over QLoRA?
- **Skip LoRA for large shifts:** a new language or heavy code pretraining is where full fine-tuning or a
  much higher rank on all layers pulls ahead, so use it when your eval shows LoRA underfitting.
- **Prefer bf16 LoRA when it fits:** it is simpler and faster per step, with no fixed quantization error.
- **Reach for QLoRA** only when the bf16 base plus activations does not fit the GPU you have.

### Roughly how many parameters does LoRA train on a 7B model at r = 16 on all linear layers?
- **Count:** about 40M trainable parameters, roughly 0.6% of 6.7B, from 16.8M on attention plus about
  242k per MLP matrix per layer.
- **Optimizer state:** 40M × 16 bytes ≈ 640 MB instead of ~107 GB for full fine-tuning.
- **Checkpoint:** about 80 MB in bf16 against 13.4 GB for the base, which is what makes per-tenant
  adapters cheap to store and roll back.

### You must serve fine-tuned variants for 50 tenants on a shared base. How do you design it?
- **Serve unmerged adapters:** one base resident on the GPUs, adapters loaded on demand, with an engine
  that batches requests across adapters (vLLM's `--enable-lora`, `--max-loras`, `--max-lora-rank`).
- **Pin the base revision:** every adapter is a delta against exact weights, so store the base commit
  sha with each adapter and fail deploys on a mismatch.
- **Watch the costs:** each request pays a small extra matmul, and adapter load/evict churn shows up as
  latency; merge only the few highest-traffic tenants if that overhead matters.

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
