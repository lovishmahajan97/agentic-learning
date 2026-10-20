---
id: tooling
module: training-adaptation
name: Tooling (Unsloth, Axolotl, TRL)
kind: tool
difficulty: core
est_minutes: 13
related:
  - training-adaptation/peft
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/dpo
  - training-adaptation/grpo
  - training-adaptation/gradient-checkpointing
  - training-adaptation/distributed-training
  - training-adaptation/quantization
  - python-for-ai/hugging-face
sources:
  - Official docs: TRL (Hugging Face)
  - Official docs: Axolotl
  - Official docs: Unsloth
  - Hu et al., 2021 — LoRA: Low-Rank Adaptation of Large Language Models
---

## TL;DR
Fine-tuning an LLM is the same loop almost every time: load the model, maybe quantise it, attach LoRA,
format chat data, train with a known loss, save the adapter. TRL (Hugging Face) provides those loops as
trainers (SFT, DPO, GRPO, PPO, reward). Axolotl wraps them in a YAML file so a run is reproducible and
multi-GPU is a config flag. Unsloth rewrites the kernels so the same LoRA/QLoRA run is faster and lighter
on one GPU. Pick by how much control, reproducibility or raw speed you need.

## Why it exists
Before these tools every fine-tune was a bespoke script, and the hard part was never the training loop;
it was the dozen details around it. Chat data has to be rendered through the exact template the model
expects. The loss has to be masked so the model learns the assistant turns, not the user's questions.
Sequences have to be packed without letting examples attend to each other. LoRA has to be injected into
the right projection matrices. Mixed precision, gradient checkpointing, a 4-bit base with paged
optimizers, checkpoint and resume, logging: each is a few lines, and each is easy to get subtly wrong.
Preference training adds a second layer, because DPO needs log-probabilities from a reference model on
exactly the right tokens, and GRPO needs a generation loop, reward functions and group-relative
advantages.

The damaging property of these mistakes is that they do not crash. Loss on the prompt tokens gives a
model that parrots questions. A template mismatch between training and serving gives a model that
looked fine in the notebook and misbehaves in vLLM. A padded-token bug in DPO gives a model that is
slightly worse for reasons nobody can diagnose. Teams that write the loop from scratch reproduce these
bugs one at a time, over weeks.

The tools encode the known-good recipe so the only variables left are the ones that should vary: data,
hyperparameters and evaluation. A hand-written loop is still the right way to *learn* what happens, and
the right escape hatch for a genuinely new loss, but not the way to ship a fine-tune.

## How it works
### TRL: trainers as the primitive
TRL sits on top of `transformers` and PEFT, and the other two tools call into it. `SFTTrainer` with
`SFTConfig` accepts a dataset of `messages` (conversational) or plain `text`, renders it with the
tokenizer's chat template, optionally packs sequences, applies LoRA through a `peft_config`, and runs
the standard `Trainer` loop. `DPOTrainer` takes `prompt`/`chosen`/`rejected` rows, computes the
log-probability ratio of each answer against a reference model, and minimises
`−log σ(β · (Δ_chosen − Δ_rejected))`; with a LoRA policy the reference is the base model with the
adapter disabled, so no second copy is held in memory. `GRPOTrainer` samples several completions per
prompt (optionally through vLLM), scores them with plain Python reward functions, and normalises
rewards within each group to form advantages. `RewardTrainer` and `PPOTrainer` cover the classic RLHF
path. You write Python, every piece is overridable, and that is the point: TRL is the layer for people
who need control or a custom loss.

### Axolotl: the run as a config file
Axolotl turns a run into a YAML document: base model, adapter type (full, LoRA, QLoRA), LoRA rank and
targets, datasets with their format type (`alpaca`, `sharegpt`, `chat_template`, and others), sequence
length, sample packing, micro-batch and accumulation, learning rate and scheduler, precision,
FlashAttention, gradient checkpointing, DeepSpeed or FSDP settings, and experiment tracking. It
tokenises and packs the data once and caches it, launches through `accelerate` so a one-GPU test and an
eight-GPU run use the same file, and can merge the adapter afterwards. It exists because the dataset
format matrix is a genuine pain and because a config file in git is a reproducible experiment; a Python
script with twenty keyword arguments is not.

```yaml
base_model: Qwen/Qwen2.5-7B-Instruct
adapter: qlora
load_in_4bit: true
lora_r: 16
lora_alpha: 32
lora_target_linear: true
datasets:
  - path: data/support_chats.jsonl
    type: chat_template
sequence_len: 4096
sample_packing: true
micro_batch_size: 2
gradient_accumulation_steps: 8
num_epochs: 2
learning_rate: 2e-4
lr_scheduler: cosine
bf16: auto
flash_attention: true
gradient_checkpointing: true
output_dir: ./out/support-qlora
```

Run it with the Axolotl CLI (`axolotl train config.yml`; older releases use
`accelerate launch -m axolotl.cli.train config.yml`).

### Unsloth: the kernels
Unsloth replaces the attention, MLP and LoRA forward and backward passes of supported model families
with hand-written Triton kernels and manual autograd. Fewer intermediate tensors are materialised, more
operations are fused, and the cross-entropy over the vocabulary is computed in chunks instead of as one
huge logits tensor; it also ships its own gradient-checkpointing variant that offloads block inputs to
CPU asynchronously (`use_gradient_checkpointing="unsloth"`). The API is a two-call wrapper:
`FastLanguageModel.from_pretrained(name, max_seq_length=..., load_in_4bit=True)` then
`FastLanguageModel.get_peft_model(model, r=16, target_modules=[...], lora_alpha=16, ...)`, after which
you hand the model to a TRL trainer as usual. The project's own figures are roughly 2× faster training
and substantially lower VRAM than stock Transformers plus PEFT on the same GPU; treat those as vendor
claims and benchmark on your model. The sharp edges: only patched architectures are accelerated, and the
open-source release has historically been single-GPU focused, so check the current docs before planning
a multi-GPU run around it. It exports merged 16-bit weights, bare adapters, or GGUF for llama.cpp.

### How they compose
TRL is the base; Axolotl configures TRL/PEFT/`accelerate` from YAML; Unsloth swaps in faster kernels
underneath a TRL trainer. Similar config-driven frameworks exist (LLaMA-Factory, torchtune) and the
same mental model applies.

## Example
A LoRA SFT on reviewed support conversations with TRL. The dataset rows carry a `messages` list, so the
trainer renders them with the model's chat template; the adapter is saved on its own and merged into the
bf16 base later only if the serving stack needs a single checkpoint. The commented lines show the
Unsloth swap-in.

```python
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer

name = "Qwen/Qwen2.5-1.5B-Instruct"
tok = AutoTokenizer.from_pretrained(name)
model = AutoModelForCausalLM.from_pretrained(name, torch_dtype="bfloat16")

# rows: {"messages": [{"role": "user", "content": ...}, {"role": "assistant", "content": ...}]}
ds = load_dataset("json", data_files="support_chats.jsonl", split="train")

peft_cfg = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                      "gate_proj", "up_proj", "down_proj"])

args = SFTConfig(
    output_dir="out/support-lora",
    per_device_train_batch_size=4, gradient_accumulation_steps=4,   # effective batch 16
    learning_rate=2e-4, num_train_epochs=2, lr_scheduler_type="cosine", warmup_ratio=0.03,
    bf16=True, gradient_checkpointing=True, packing=True,
    max_length=2048,                     # older TRL releases call this max_seq_length
    assistant_only_loss=True,            # needs a chat template that marks assistant turns;
    logging_steps=10, save_strategy="epoch",   # otherwise use prompt/completion rows
)
trainer = SFTTrainer(model=model, args=args, train_dataset=ds,
                     processing_class=tok,   # tokenizer= in older TRL releases
                     peft_config=peft_cfg)
trainer.train()
trainer.save_model()                     # writes the adapter only

# Unsloth variant: from unsloth import FastLanguageModel
# model, tok = FastLanguageModel.from_pretrained(name, max_seq_length=2048, load_in_4bit=True)
# model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=32, target_modules=[...])
# then build SFTTrainer(model=model, ...) with peft_config=None.
```

## Trade-offs
- TRL gives full control and the newest algorithms first, but you own the plumbing around the trainer
  (data formatting, launch, multi-GPU config); it flips toward Axolotl once a team runs many similar jobs.
- Axolotl gives reproducible configs and painless multi-GPU, but a custom loss or unusual data flow means
  reading its source; it flips back to raw TRL when the experiment is not one of its supported shapes.
- Unsloth gives real speed and memory savings on one GPU, but only for patched architectures and with
  single-GPU focus historically; it flips to Axolotl or plain FSDP when the job needs several GPUs.
- Every layer of convenience hides a decision; the lower you go, the more you must verify yourself, and
  the higher you go, the more you must trust defaults you did not read.

## Pitfalls
- Chat-template mismatch between training and serving; the symptom is a model that behaves in the training
  notebook but misbehaves or emits stray special tokens once deployed in vLLM or Ollama.
- Computing the loss over prompt tokens; the symptom is suspiciously low training loss alongside a model
  that generates user-style questions or echoes instructions.
- Packing sequences without per-example attention masking; the symptom is answers that drift into the
  topic of a neighbouring training example, which is context bleed across the pack.
- Pinning nothing: TRL and Transformers rename arguments across releases (`tokenizer` → `processing_class`,
  `max_seq_length` → `max_length`); the symptom is a job that ran last month failing on an unknown keyword.
- Merging a QLoRA adapter into the 4-bit base instead of the bf16 base; the symptom is a merged model
  that scores worse than the adapter-plus-base it came from.
- Trusting Unsloth's speedup without checking the architecture list; the symptom is an error at load or a
  run that is no faster than stock Transformers.

## When to use
Reach for TRL when you need a trainer class in your own Python, a custom reward or loss, or the newest
method the day it lands. Reach for Axolotl when runs must be reproducible across people and machines,
when you juggle several dataset formats, or when the job spans several GPUs. Reach for Unsloth when the
job is a LoRA or QLoRA on one GPU and the model family is supported, because the saving in time and
VRAM is large enough to change what fits. Whatever you pick, measure the same things: tokens per second,
peak memory, eval score on a held-out set, and an end-to-end check through the serving stack's chat
template. In interviews, be ready to place the three on a control-versus-convenience axis and to name
the failure modes the tools exist to prevent.

## Role lens
- **Engineer:** Writes the TRL or Axolotl config, verifies loss masking and the chat template on one
  example by hand, and benchmarks Unsloth against stock PEFT before committing to it.
- **Architect:** Chooses the standard stack for the organisation so runs are reproducible and auditable,
  and decides where custom TRL code is allowed versus config-only training.
- **FDE:** Gets a customer's first LoRA running on the GPU they already have, often Unsloth on one card,
  and leaves behind an Axolotl config the customer's team can re-run without you.

## Check yourself
**Q:** You have 3,000 reviewed support chats and a single 24 GB GPU. A colleague proposes writing a
custom PyTorch training loop. Which tool would you pick, why, and what decisions would still be yours?
**A:** A QLoRA SFT through TRL's `SFTTrainer`, loaded with Unsloth if the model family is supported,
because that fits a 7B-class model on 24 GB and removes the known bugs a hand-written loop reinvents:
template rendering, loss masking, packing, adapter injection and checkpointing. Axolotl would be the
pick if the run must be reproducible for others or move to several GPUs later. What remains yours: data
quality and de-duplication, the chat template matching the serving stack, confirming the loss covers only
assistant turns, LoRA rank, learning rate and epochs, a held-out eval set, and whether to serve the
adapter or merge it into the bf16 base.

## Quiz
### Q: How do TRL, Axolotl and Unsloth relate to each other?
- [ ] They are three competing training loops that cannot be combined
- [x] TRL provides the trainers; Axolotl configures them from YAML; Unsloth accelerates the kernels beneath them
- [ ] Axolotl is the base library and TRL is its command-line wrapper
- [ ] Unsloth replaces TRL entirely with its own trainer classes
> TRL is the primitive layer. Axolotl orchestrates TRL, PEFT and accelerate from a config file, and Unsloth
> patches the model's forward and backward passes, then hands the model to a TRL trainer.

### Q: What is the actual mechanism behind Unsloth's speed and memory savings?
- [ ] It silently trains a smaller distilled model in place of the one you loaded
- [ ] It skips the backward pass through all frozen base-model layers
- [x] Hand-written fused Triton kernels and manual backward passes that cut intermediate tensors and memory traffic
- [ ] It performs all arithmetic in 4-bit integers instead of bf16
> The base weights may be stored in 4-bit, but compute still happens in 16-bit; the gain comes from fused
> kernels, chunked loss computation and fewer materialised intermediates, not from changing the model.

### Q: Which symptom most strongly suggests the loss was not masked to assistant tokens?
- [ ] Out-of-memory errors in the first training step
- [ ] A NaN loss after the warmup phase
- [x] Training loss that looks unusually low while the model generates user-style questions
- [ ] Loss that never decreases from its initial value
> Averaging the loss over the easy-to-predict user turns pulls the number down, and the model learns to
> continue conversations in the user's voice because that is what it was trained to predict.
