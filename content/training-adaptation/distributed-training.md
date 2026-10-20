---
id: distributed-training
module: training-adaptation
name: Distributed training (data / tensor / pipeline parallelism, FSDP, DeepSpeed)
kind: concept
difficulty: advanced
est_minutes: 18
status: fact-checked
related:
  - training-adaptation/pre-training
  - training-adaptation/gradient-checkpointing
  - training-adaptation/peft
  - training-adaptation/tooling
  - ml-fundamentals/batch-size
  - ml-fundamentals/mixed-precision
  - infra-capacity-economics/gpu-infrastructure
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
sources:
  - Rajbhandari et al., 2020 — ZeRO: Memory Optimizations Toward Training Trillion Parameter Models
  - Shoeybi et al., 2019 — Megatron-LM: Training Multi-Billion Parameter Language Models Using Model Parallelism
  - Huang et al., 2019 — GPipe: Efficient Training of Giant Neural Networks using Pipeline Parallelism
  - Zhao et al., 2023 — PyTorch FSDP: Experiences on Scaling Fully Sharded Data Parallel
---

## TL;DR
Caveat: this topic matters only for training-heavy roles; for LLM-application roles, learn the vocabulary
and skip the practice. One GPU cannot hold a large model's training state (about 16 bytes per parameter
with Adam in mixed precision) or finish pretraining in calendar time. Data parallelism splits the batch,
tensor parallelism splits each matrix, pipeline parallelism splits the layers, and FSDP or DeepSpeed ZeRO
shard the replicated state so data parallelism stops wasting memory. The interconnect decides which
combination is affordable.

## Why it exists
Two separate limits force training across GPUs: memory and time.

Memory first. Mixed-precision training with Adam keeps, per parameter, a bf16 weight (2 bytes), a bf16
gradient (2), an fp32 master weight (4) and two fp32 Adam moments (8): 16 bytes, which is the accounting
the ZeRO paper uses. A 7B model is therefore ~112 GB of state before a single activation, and a 70B model
is ~1.1 TB. No single accelerator holds that. Even a LoRA fine-tune of a 70B model needs its 140 GB of
bf16 base weights to live somewhere.

Then time. Training compute is roughly 6 × parameters × tokens FLOPs. Pretraining a 7B model on one
trillion tokens is about 4.2 × 10²² FLOPs; at an illustrative sustained 400 TFLOP/s on one GPU (an H100's
dense bf16 peak is about 989 TFLOP/s, and 40% utilisation is a realistic ballpark) that is ~10⁸ seconds,
over three years. Pretraining is a thousands-of-GPUs problem by arithmetic, not ambition.

The naive fixes each solve one limit and not the other. Replicating the model on N GPUs and splitting
the batch (plain data parallelism) divides time by nearly N but leaves every replica holding the full
112 GB. Putting different layers on different GPUs (naive model parallelism) divides memory but runs one
GPU at a time while the others idle. The design space of distributed training is the set of ways to split
both state and work so that the GPUs stay busy and the communication they need fits the links between
them.

## How it works
### Data parallelism (DDP)
Every GPU holds a full replica and processes a different micro-batch. After backward, gradients are
averaged with an all-reduce; in a ring all-reduce each GPU sends and receives about `2·(N−1)/N` times the
gradient size, so the volume is roughly two gradient-sized transfers per step regardless of N. DDP
buckets gradients and starts reducing early buckets while later layers are still in backward, hiding most
of the cost. Throughput scales with N; memory per GPU does not change. The global batch becomes
N × micro-batch × accumulation steps, which is why learning rate and warmup must be re-tuned when the GPU
count changes.

### Sharded data parallelism: ZeRO and FSDP
The waste in DDP is that all N replicas store identical optimizer state and gradients. ZeRO partitions
them across the data-parallel group and gathers only what each step needs. Per GPU, with Ψ parameters
and N ranks:

```
DDP            16Ψ
ZeRO stage 1   2Ψ + 2Ψ + 12Ψ/N     optimizer state sharded
ZeRO stage 2   2Ψ + 14Ψ/N          gradients sharded too
ZeRO stage 3   16Ψ/N               parameters sharded too
```

For 7B on 8 GPUs: 112 GB, ~38.5 GB, ~26 GB and 14 GB respectively, before activations. Stage 3 pays for
this by all-gathering each layer's parameters just before it is used in forward and again in backward,
then reduce-scattering its gradients, which is about 1.5× DDP's communication volume but is spread
through the step and overlapped by prefetching the next layer. PyTorch FSDP is the native implementation
of the same idea: `SHARD_GRAD_OP` is roughly stage 2, `FULL_SHARD` is stage 3, and `HYBRID_SHARD`
shards within a node and replicates across nodes so the frequent all-gathers stay on fast intra-node
links. The wrapping unit (usually one transformer block) sets peak memory, because one unit is held
unsharded at a time. DeepSpeed adds ZeRO-Offload (optimizer state and gradients to CPU RAM) and
ZeRO-Infinity (NVMe) for jobs that still do not fit, at a large bandwidth cost.

### Tensor parallelism
When a single layer is too large, or per-GPU activation memory and latency must fall without idle time,
split the matrices themselves (Megatron-LM). In the MLP, the first linear is split by columns so each GPU
computes its slice and applies GELU independently; the second linear is split by rows so each GPU
produces a partial sum, combined with one all-reduce. Attention splits heads across GPUs the same way.
That is two all-reduces per layer in forward and two in backward, each moving an `s·b·h` activation,
so tensor parallelism is bandwidth- and latency-hungry and is kept inside a node over NVLink-class
links, typically at degree 2–8.

### Pipeline parallelism
Assign contiguous groups of layers to stages on different GPUs and split the batch into m micro-batches
that stream through so stages work concurrently (GPipe). The idle fraction, the bubble, is roughly
`(p−1)/(m+p−1)` for p stages: four stages with sixteen micro-batches idle about 16% of the time, with
four micro-batches about 43%. The 1F1B schedule interleaves forward and backward to cap how many
micro-batches' activations are alive at once. Only stage-boundary activations cross GPUs, once per
micro-batch, so pipeline parallelism tolerates slower inter-node links. Its costs are the bubble, stage
imbalance, and the engineering of partitioning a model.

### 3D parallelism and the choice procedure
Large runs compose them: tensor parallelism inside a node, pipeline across nodes, and data or
sharded-data parallelism over the remaining replicas, with world size = TP × PP × DP. Long contexts add
sequence or context parallelism, which splits the sequence dimension (ring attention). The decision
order for most teams: (1) if the state fits with FSDP or ZeRO-3 across the available GPUs, use only that;
it covers nearly all fine-tuning. (2) If a layer or its activations still do not fit, or step latency
matters, add tensor parallelism within the node. (3) If the job spans many nodes with slow interconnect,
add pipeline parallelism rather than stretching tensor parallelism across the network.

### What the frameworks provide
PyTorch ships DDP, FSDP (and the newer `fully_shard` FSDP2 API), the `torchrun` launcher and the NCCL
backend. DeepSpeed provides ZeRO stages 1–3, offloading and a pipeline engine behind a JSON config, and
plugs into the Hugging Face Trainer and Accelerate. Megatron-LM/Megatron-Core implements tensor,
pipeline and sequence parallelism for pretraining. Accelerate and Axolotl expose FSDP or DeepSpeed as
configuration so the training script does not change.

## Example
A minimal full-parameter fine-tune of a 7B model across eight GPUs with FSDP in `FULL_SHARD` mode (the
ZeRO-3 equivalent), wrapping per decoder block and checkpointing activations per block. The model is
loaded in bf16 to keep the example short; production runs usually keep fp32 master weights through the
mixed-precision policy. Launch with `torchrun --nproc_per_node=8 train.py`.

```python
import os, functools, torch, torch.distributed as dist
from torch.distributed.fsdp import FullyShardedDataParallel as FSDP, MixedPrecision, ShardingStrategy
from torch.distributed.fsdp.wrap import transformer_auto_wrap_policy
from transformers import AutoModelForCausalLM, AutoTokenizer
from transformers.models.qwen2.modeling_qwen2 import Qwen2DecoderLayer

dist.init_process_group("nccl")
rank = int(os.environ["LOCAL_RANK"]); torch.cuda.set_device(rank)
name = "Qwen/Qwen2.5-7B"
tok = AutoTokenizer.from_pretrained(name)
model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.bfloat16)
model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})

model = FSDP(
    model,
    auto_wrap_policy=functools.partial(transformer_auto_wrap_policy,
                                      transformer_layer_cls={Qwen2DecoderLayer}),
    sharding_strategy=ShardingStrategy.FULL_SHARD,      # params, grads, optimizer state all sharded
    mixed_precision=MixedPrecision(param_dtype=torch.bfloat16, reduce_dtype=torch.bfloat16),
    device_id=rank, use_orig_params=True,
)
opt = torch.optim.AdamW(model.parameters(), lr=1e-5)

batch = tok(["Replace me with a sharded DataLoader."] * 4, return_tensors="pt", padding=True).to(rank)
labels = batch["input_ids"].clone(); labels[batch["attention_mask"] == 0] = -100
for step in range(10):
    loss = model(**batch, labels=labels).loss
    loss.backward(); opt.step(); opt.zero_grad(set_to_none=True)
    if rank == 0: print(step, round(loss.item(), 4))
dist.destroy_process_group()
# Per GPU the sharded bf16 weights are ~14 GB / 8 ≈ 1.75 GB; the all-gather of one decoder block
# plus its activations is what actually sets peak memory.
```

## Trade-offs
- DDP is the simplest and most compute-efficient scheme, but every GPU holds the full state; it flips to
  FSDP or ZeRO the moment weights plus optimizer state exceed one card, or batch is memory-limited.
- ZeRO-3/FSDP divides memory by N but adds all-gathers on the critical path and is sensitive to the
  interconnect; across slow inter-node links use hybrid sharding so gathers stay inside the node.
- Tensor parallelism cuts per-GPU weight and activation memory with no bubble, but needs NVLink-class
  bandwidth every layer; it flips to pipeline parallelism as soon as the split crosses a node boundary.
- Pipeline parallelism is communication-light, but its bubble only shrinks when micro-batches far outnumber
  stages; it flips against you when the global batch is small or stages are unbalanced.
- CPU or NVMe offload fits otherwise impossible jobs on few GPUs at a large speed cost; it pays only when
  wall-clock is cheap and GPUs are not.

## Pitfalls
- Scaling to more GPUs without re-tuning for the larger global batch; the symptom is a loss curve that
  no longer matches the single-GPU run, usually because the learning rate and warmup were left unchanged.
- Materialising the full model on every rank before sharding; the symptom is host-RAM or GPU OOM during
  initialisation of a 70B model, fixed by meta-device init and syncing weights from rank 0.
- Wrapping the whole model as one FSDP unit; the symptom is OOM at the first all-gather despite
  `FULL_SHARD`, because the entire parameter set is gathered at once instead of one block.
- Uneven data across ranks so one rank skips a collective; the symptom is a run that hangs at 100% GPU
  utilisation with no progress, fixed with `drop_last`, a join context or an explicit barrier.
- Saving sharded checkpoints tied to one world size; the symptom is resume failing after changing the GPU
  count, avoided by distributed checkpoint formats that reshard or by consolidating on save.
- Using tensor parallelism across GPUs connected only by PCIe or Ethernet; the symptom is single-digit
  model FLOPs utilisation while the links sit saturated.

## When to use
If your role builds LLM applications, you need this lesson's vocabulary and arithmetic, not its practice:
enough to read a DeepSpeed or FSDP config, to know why a 70B full fine-tune needs more than one 80 GB node,
and to recognise that the cheapest "distributed" option is usually to not need it (LoRA, QLoRA, a
smaller base). If your role trains models, the day-to-day decisions are the ZeRO stage or sharding
strategy, the FSDP wrap unit, whether to add tensor parallelism inside the node, the global batch and
learning-rate schedule, and checkpoint format. Measure tokens per second per GPU against the single-GPU
baseline (scaling efficiency) and model FLOPs utilisation. In interviews, the expected depth is to say
what each scheme communicates, how often, and which link it needs; and to derive the 16-bytes-per-parameter
memory budget on the spot.

## Role lens
- **Engineer:** Picks the sharding strategy and wrap policy, launches with `torchrun` or Accelerate, and
  checks scaling efficiency and MFU before trusting that eight GPUs are doing eight GPUs' worth of work.
- **Architect:** Owns the training topology and cost model: node interconnect, GPU count, whether full
  fine-tuning is justified over PEFT at all, and the `6 × params × tokens` estimate behind every schedule.
- **FDE:** Rarely runs this, but reads a customer's DeepSpeed or FSDP config to explain why their
  eight-GPU ZeRO-3 fine-tune will not reproduce on the four-GPU staging box they want to use.

## Check yourself
**Q:** A team wants a full-parameter SFT of a 70B model on one node of eight 80 GB GPUs. Does it fit, and
what are their options if not? Show the arithmetic.
**A:** With Adam in mixed precision the state is 16 bytes per parameter, so 70B × 16 ≈ 1.12 TB. ZeRO-3 or
FSDP across eight GPUs leaves 140 GB per GPU before activations, which exceeds 80 GB, so it does not fit.
Options: offload the 12 bytes-per-parameter of fp32 master weights and Adam moments to CPU (about 840 GB
of host RAM, and much slower); add a second node so sixteen GPUs hold 70 GB each, which is tight and
needs gradient checkpointing, a small micro-batch and hybrid sharding to keep all-gathers intra-node; or
change the problem to LoRA on a bf16 base (140 GB of weights sharded to ~17.5 GB per GPU plus a tiny
optimizer state) or QLoRA. The arithmetic decides, not the framework.

## Quiz
### Q: Which parallelism scheme communicates activations in every layer and therefore needs NVLink-class bandwidth?
- [ ] Data parallelism, which all-reduces gradients once per step
- [x] Tensor parallelism, which all-reduces partial results inside every layer
- [ ] Pipeline parallelism, which sends activations only at stage boundaries
- [ ] ZeRO stage 1, which shards only the optimizer state
> Splitting a matrix across GPUs means each layer's output must be combined before the next layer runs,
> so the traffic is per layer and latency-sensitive; the other schemes communicate far less often.

### Q: A 7B model trained with Adam in mixed precision across 8 GPUs under ZeRO stage 3 needs roughly how much state per GPU, before activations?
- [ ] About 112 GB, the same as data parallelism
- [ ] About 56 GB, because only the optimizer state is split
- [x] About 14 GB, since all 16 bytes per parameter are sharded across the 8 ranks
- [ ] About 1.75 GB, since only the bf16 weights are sharded
> Stage 3 shards weights, gradients and optimizer state, so per-GPU memory is 16Ψ/N = 112 GB / 8. The
> 1.75 GB figure counts only the bf16 weights and forgets the other 14 bytes per parameter.

### Q: In a four-stage pipeline, what most directly reduces the idle bubble?
- [ ] Using fewer micro-batches so each stage finishes sooner
- [x] Using more micro-batches per step, since the bubble fraction is about (p−1)/(m+p−1)
- [ ] Adding more pipeline stages to spread the work
- [ ] Enabling gradient checkpointing inside each stage
> The bubble is the time stages wait for work to arrive; more micro-batches in flight keep every stage busy
> for a larger share of the step, while more stages or checkpointing make the bubble worse or leave it alone.
