---
id: hugging-face
module: python-for-ai
name: Hugging Face (transformers, datasets, tokenizers, accelerate, PEFT, TRL)
kind: tool
difficulty: core
est_minutes: 22
related:
  - python-for-ai/pytorch
  - python-for-ai/safetensors
  - python-for-ai/sentence-transformers
  - model-internals/tokenization
  - training-adaptation/peft
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/dpo
  - safety-security/supply-chain-security-for-models-and-datasets
sources:
  - Wolf et al., 2020 — Transformers: State-of-the-Art Natural Language Processing
  - Hu et al., 2021 — LoRA: Low-Rank Adaptation of Large Language Models
  - Official docs: Hugging Face Transformers, Datasets, Accelerate, PEFT, TRL
---

## TL;DR
Hugging Face is a stack of libraries over a model hub. `transformers` loads any published architecture
and its weights by name; `tokenizers` turns text into the exact ids that model expects; `datasets` streams
and memory-maps training data; `accelerate` runs the same PyTorch code on one GPU or many; `PEFT` trains
small adapters instead of all weights; `TRL` provides SFT and preference-tuning trainers. It exists
because every open model would otherwise ship its own incompatible loading and training code.

## Why it exists
An open model is not just a weights file. To use one you need the architecture code that matches the
weights, the config (layer count, heads, rope settings), the tokenizer with its exact vocabulary and
special tokens, and the chat template that formats a conversation. Before a common hub, each research
release came with its own repository and conventions, so swapping models meant rewriting glue code, and
a subtle mismatch, such as the wrong tokenizer or a missing BOS token, produced a model that ran but
generated garbage with no error.

Training adds a second layer of glue. Datasets of tens of gigabytes do not fit in RAM as Python lists;
multi-GPU training needs process launching, gradient synchronisation and mixed precision; full fine-tuning
of a 7B model needs memory for weights, gradients and optimizer states (with AdamW that is several times
the weights alone), which rules out most single GPUs. And preference tuning (DPO, PPO-style RLHF) needs
paired data handling, a frozen reference model and specific loss functions that are easy to get subtly
wrong.

The naive alternative, "write it in plain PyTorch", is possible but means re-solving each of these per
project. The Hugging Face libraries standardise them behind `from_pretrained` and a few trainer classes,
so the expensive knowledge (correct tokenisation, correct loss masking, correct sharding) lives in one
tested place.

## How it works
### transformers
`AutoModelForCausalLM.from_pretrained("org/name")` downloads `config.json`, reads its `model_type`,
picks the matching PyTorch class, and loads the weights (preferably `.safetensors`) into it. Task heads
are separate classes (`...ForSequenceClassification`, `...ForCausalLM`) because the same backbone feeds
different output layers. `device_map="auto"` (via Accelerate) spreads layers across available GPUs and
CPU when a model does not fit on one device, and `torch_dtype` selects precision at load time so you
never materialise an fp32 copy. `generate()` implements the decoding loop with the KV cache, sampling
and stopping criteria. `pipeline()` wraps tokenise-model-decode for quick experiments.

### tokenizers
The fast tokenizers are written in Rust and exposed to Python, because tokenising a large corpus in pure
Python is a bottleneck. `AutoTokenizer` loads the vocabulary and merges for that exact model, returns
offsets for span alignment, and provides `apply_chat_template`, which renders a list of messages into the
model's expected prompt format, using the Jinja template stored with the tokenizer. Using it, rather than
hand-concatenating `"User: ...\nAssistant:"`, is what keeps a chat model on-distribution.

### datasets
`load_dataset` stores data as Apache Arrow files on disk and memory-maps them, so a 50 GB dataset costs
little RAM and random access is cheap. `.map(fn, batched=True)` applies tokenisation in batches and caches
the result keyed by a fingerprint of the function and inputs, so re-running a notebook does not
re-tokenise. `streaming=True` iterates without downloading everything first.

### accelerate
Accelerate wraps a plain training loop: `accelerator.prepare(model, optimizer, loader)` handles device
placement, distributed data parallel, mixed precision and FSDP/DeepSpeed backends, and `accelerate
launch` starts one process per GPU. Your loop stays ordinary PyTorch; the configuration decides the
topology.

### PEFT
PEFT freezes the base model and injects small trainable modules. With LoRA, a frozen weight `W` gets an
update `ΔW = B·A`, where `A` is r×k and `B` is d×r with rank r much smaller than d or k, so trainable
parameters drop from d·k to r·(d+k) per adapted matrix. Optimizer state shrinks proportionally, the
saved adapter is megabytes instead of gigabytes, and one base model can serve many adapters. QLoRA
combines this with a 4-bit quantized frozen base (via bitsandbytes) to fit larger models on one GPU.

### TRL
TRL provides trainers built on the `transformers` Trainer: `SFTTrainer` for supervised fine-tuning on
chat or instruction data (applying the chat template and, optionally, computing loss only on assistant
tokens), `DPOTrainer` for preference pairs, and trainers for reward modelling and online RL methods such
as PPO and GRPO. They accept a `peft_config`, so adapters and preference tuning compose. TRL's API has
changed frequently across releases, so pin the version and read the docs for the one you install.

## Example
LoRA supervised fine-tuning of a small chat model with TRL and PEFT. Argument names follow recent TRL
releases; check them against your pinned version.

```python
from datasets import load_dataset
from peft import LoraConfig
from trl import SFTConfig, SFTTrainer

# Each row: {"messages": [{"role": "user", ...}, {"role": "assistant", ...}]}
ds = load_dataset("json", data_files="support_chats.jsonl", split="train")
ds = ds.train_test_split(test_size=0.05, seed=0)

peft_cfg = LoraConfig(
    r=16, lora_alpha=32, lora_dropout=0.05,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],  # names depend on architecture
    task_type="CAUSAL_LM",
)
args = SFTConfig(
    output_dir="out/support-lora",
    per_device_train_batch_size=4, gradient_accumulation_steps=4,   # effective batch 16
    learning_rate=2e-4, num_train_epochs=2, bf16=True,
    logging_steps=10, eval_strategy="steps", eval_steps=100,
)
trainer = SFTTrainer(
    model="Qwen/Qwen2.5-0.5B-Instruct",   # loaded via transformers; chat template from its tokenizer
    args=args, train_dataset=ds["train"], eval_dataset=ds["test"],
    peft_config=peft_cfg,
)
trainer.train()
trainer.save_model()   # writes only adapter weights (adapter_model.safetensors) plus config
```

Parameter arithmetic for one 4096×4096 projection with r=16: full update is 16.8M parameters; LoRA
trains 16×(4096+4096) = 131,072, under 1% of that matrix.

## Trade-offs
- `from_pretrained` by name gives instant access to thousands of models, but it fetches remote code and
  weights at runtime; pin a revision hash and mirror artifacts once you leave experimentation.
- Trainer classes remove boilerplate and encode correct defaults, but they hide the loop; when you need
  a custom loss or schedule, subclassing can become harder than writing the PyTorch loop directly.
- LoRA cuts memory and storage dramatically and allows many adapters per base, but it can underfit tasks
  that need broad behaviour change, where full fine-tuning (if affordable) does better.
- `transformers.generate` is fine for experiments and batch jobs, but production serving with many
  concurrent users is better handled by vLLM, SGLang or TGI, which add continuous batching and paging.
- `device_map="auto"` makes a too-large model run, but layers offloaded to CPU make generation
  dramatically slower; it trades failure for latency, which is only acceptable offline.

## Pitfalls
- Formatting prompts by hand instead of `apply_chat_template`: the model answers but quality is oddly
  poor, ignores the system prompt, or keeps generating past where it should stop.
- Loss computed on prompt tokens as well as completions during SFT: the model learns to imitate users,
  and the symptom is outputs that echo or continue the question instead of answering it.
- `trust_remote_code=True` on an unvetted repository: it executes arbitrary Python from the hub at load
  time; the symptom may be nothing visible at all, which is the danger.
- Unpinned library versions: TRL or transformers upgrades rename arguments or change defaults, and a
  training script that worked last month fails or silently trains differently.
- Mismatched tokenizer and model (for example after adding special tokens without resizing embeddings):
  index errors on new token ids, or generations that never emit the end-of-turn token.

## When to use
Use `transformers` + `tokenizers` whenever you run an open-weight model in Python for experiments,
offline batch work, evaluation or embedding; use `datasets` whenever training or eval data exceeds a few
hundred megabytes or needs reproducible preprocessing. Reach for PEFT + TRL when prompting and retrieval
have plateaued and you have a few thousand high-quality examples of the target behaviour; reach for
Accelerate when one GPU is not enough. Do not use this stack to serve high-concurrency traffic; export
the model (merged weights or base plus adapter) to an inference engine. Measure eval loss and a task
metric on a held-out set, not training loss. Interviewers commonly ask about LoRA's parameter
arithmetic, why chat templates matter, and when to fine-tune instead of prompt.

## Role lens
- **Engineer:** Loads checkpoints with correct dtype and template, writes SFT/DPO jobs with PEFT, and
  debugs tokenisation, loss masking and version drift.
- **Architect:** Decides fine-tune versus prompt-and-retrieve, sets the hub mirroring and revision-pinning
  policy, and chooses adapter-per-tenant versus merged models for serving.
- **FDE:** Fine-tunes or evaluates an open model inside a customer's air-gapped environment, which means
  pre-downloading weights, datasets and wheels, and loading them offline.

## Check yourself
**Q:** A team fine-tuned a 7B instruct model with LoRA on 5,000 support transcripts. Training loss fell
nicely, but in testing the model rambles, sometimes writes the customer's next message, and ignores the
system prompt. What do you check first?
**A:** These symptoms point at data formatting, not capacity. First, was the data rendered with the base
model's own chat template via `apply_chat_template`, including its end-of-turn token? If not, the model
learned a new format that conflicts with its instruction tuning and never learned to stop. Second, was
loss masked to assistant tokens only? Training on user turns teaches the model to write user turns. Third,
was the system prompt present in training examples? Then verify inference uses the same template. Only
after formatting is correct would I tune rank, learning rate or epochs, and I would judge with a task
metric on held-out conversations, since falling training loss says little about behaviour.

## Quiz
### Q: Why does LoRA reduce GPU memory during fine-tuning so much more than it reduces inference memory?
- [ ] Because LoRA quantizes the base model to 4 bits automatically
- [x] Because gradients and optimizer states are only kept for the small adapter matrices, not the frozen base
- [ ] Because LoRA removes the KV cache during training
- [ ] Because the adapter replaces the attention layers entirely
> Training memory is dominated by gradients and AdamW states for every trainable weight. Freezing the base
> removes those for almost all parameters; the frozen weights still must be loaded, so inference barely changes.

### Q: What does `datasets` gain by storing data as memory-mapped Arrow files?
- [ ] It compresses text so tokenisation is faster
- [ ] It guarantees the data is deduplicated
- [x] Large datasets can be accessed randomly without loading them fully into RAM
- [ ] It encrypts the data for compliance
> Memory mapping lets the operating system page in only the rows you touch, so a dataset far larger
> than RAM is usable. Compression and deduplication are separate concerns that Arrow does not imply.

### Q: Why use `tokenizer.apply_chat_template` instead of building the prompt string yourself?
- [ ] It makes the prompt shorter so it costs fewer tokens
- [ ] It is required for the model to accept a system prompt at all
- [x] It reproduces the exact special tokens and turn format the model was trained on
- [ ] It automatically removes prompt-injection attempts from user messages
> Chat models are trained on one specific format of role markers and end-of-turn tokens. Deviating keeps
> the model running but off-distribution, which degrades quality and stopping behaviour without any error.
