---
id: tensor-parallelism-at-inference
module: serving-inference
name: Tensor parallelism at inference
kind: concept
difficulty: advanced
est_minutes: 16
status: reviewed
related:
  - training-adaptation/distributed-training
  - model-internals/grouped-query-attention
  - model-internals/kv-cache
  - model-internals/mixture-of-experts
  - serving-inference/gpu-memory-optimization
  - serving-inference/engines
  - infra-capacity-economics/gpu-infrastructure
  - infra-capacity-economics/a100-h100-l4-selection
sources:
  - Shoeybi et al., 2019 — Megatron-LM: Training Multi-Billion Parameter Language Models Using Model Parallelism
  - Pope et al., 2022 — Efficiently Scaling Transformer Inference
  - Official docs: vLLM
---

## TL;DR
Tensor parallelism (TP) splits every weight matrix across N GPUs so each holds 1/N of the parameters and
KV cache and does 1/N of each layer's arithmetic, stitched together by an all-reduce after attention and
after the MLP in every layer. At inference it is how you fit a model larger than one GPU and pool memory
bandwidth for faster decode, but it pays off only over a fast intra-node interconnect, because decode
sends hundreds of tiny all-reduces per token.

## Why it exists
A 70B-parameter model in bf16 is about 140 GB of weights. No single 80 GB GPU can hold it, let alone
leave room for a KV cache. Something has to split the model, and the question is what to split and what
that costs per token.

Pipeline parallelism splits by layers: GPU 0 runs layers 1–40, GPU 1 runs 41–80. It fits the model, but
a single request still passes through the GPUs one after another, so per-token latency does not improve,
and keeping both GPUs busy needs several requests in flight at different stages. Offloading keeps part of
the weights in CPU memory and streams them in, but PCIe moves data roughly 50–100× slower than HBM,
so decode slows to seconds per token. Quantization halves or quarters the bytes but costs quality, and a
4-bit 70B model still needs about 40 GB: it fits an 80 GB card, does not fit a 24 GB one, and leaves
little cache room on a 48 GB one.

Tensor parallelism splits *inside* each layer. Every GPU works on the same token at the same time on its
slice of every matrix, so the memory bandwidth of N GPUs is pooled: decode, which is bandwidth-bound,
gets faster roughly in proportion to N as long as the cost of exchanging partial results stays small.
That is why TP is the default within a node for large models, and why its limits are set by the
interconnect rather than by the GPUs.

## How it works
### Megatron-style partitioning
Consider a two-matrix MLP, `Y = GeLU(X·A)·B`, with A of shape [h, 4h] and B of shape [4h, h]. Split A by
*columns* into N pieces: each GPU computes `GeLU(X·A_i)`, an [tokens, 4h/N] slice. No communication is
needed *because* GeLU is elementwise, so each slice of the intermediate activation is independent. Then
split B by *rows* to match: GPU i computes the partial product `GeLU(X·A_i)·B_i`, which has the full
output shape [tokens, h] but contains only its share of the sum. One all-reduce adds the N partial
outputs and every GPU ends up with the full Y. Llama-style SwiGLU MLPs have three matrices, but the
pattern is identical: gate and up projections column-parallel, down projection row-parallel, one
all-reduce.

Attention is split by heads. With 64 query heads and TP = 8, each GPU owns 8 query heads, their
projections, and the K/V heads they attend with, so the attention computation for those heads is
entirely local. The output projection W_O is row-parallel, producing partial sums that one all-reduce
combines. Embeddings and the LM head are split along the vocabulary dimension. Net result: two
all-reduces per layer per forward pass, and every GPU holds 1/N of the weights.

### The KV cache is sharded too
Because heads are partitioned, each GPU stores K and V only for its heads: per-GPU KV cache per token is
the full figure divided by N, so the aggregate free memory of all N GPUs is available for cache. This is
often the real reason to choose TP = 4 over TP = 2 even when the weights fit at TP = 2.

Grouped-query attention adds a constraint. Llama-3-70B has 8 KV heads, so TP up to 8 gives each GPU at
least one; beyond that, KV heads must be replicated across GPUs that share them, which engines support
but which forfeits part of the memory saving. The number of attention heads, the MLP intermediate size
and the vocabulary should all be divisible by the TP degree, which is why TP degrees are almost always powers of two.

### Why the interconnect decides everything
Each all-reduce exchanges a [tokens_in_step, hidden] activation. For a decode step with 32 sequences on
a hidden size of 8,192 in bf16, that is 32 × 8,192 × 2 = 512 KB, and an 80-layer model issues 160 of them
per step. These messages are far too small to be bandwidth-bound; the cost is per-collective latency,
dozens of microseconds each on NVLink with NCCL as an illustrative order of magnitude. A few milliseconds
of overhead per step is acceptable when the step itself is 10 ms, but on PCIe-only hosts each collective
is slower, and across nodes over Ethernet or even InfiniBand the latency multiplied by 160 per token is
ruinous. Hence the rule: tensor parallel within a node, pipeline parallel (or independent replicas)
across nodes.

A step-time model makes the limit explicit:

```
t_step(N) ≈ (weight_bytes + kv_bytes_read) / (N × HBM_bw)  +  2 × n_layers × t_allreduce(N)  +  overhead
```

The first term shrinks as 1/N; the second does not shrink at all, and grows slightly with N because a
ring all-reduce takes 2(N − 1) communication steps (each GPU sends about 2(N − 1)/N of the message in
total, so bandwidth cost is nearly flat in N while latency is not). Speedup therefore saturates once the
collectives are comparable to the memory term. Illustratively, for a 70B bf16 model on H100-class GPUs
the memory term is about 141 GB ÷ (4 × 3.35 TB/s) ≈ 10.5 ms at TP = 4 and 5.3 ms at TP = 8; add 160
all-reduces at an assumed 20 µs each (3.2 ms) and the step goes from about 13.7 ms to 8.5 ms, a 1.6×
gain for 2× the GPUs. Engines fight the second term directly: vLLM, for example, ships a custom
all-reduce kernel for small messages over NVLink, and CUDA graphs remove per-kernel launch overhead from
the decode step.

Prefill is a different regime. Its all-reduce messages are [prompt_tokens, hidden]: 4,096 × 8,192 × 2
bytes = 64 MiB each for the model above, so the collectives become bandwidth-bound rather than
latency-bound. NVLink's bandwidth hides them behind prefill's large matmuls; over PCIe they can become a
large share of time to first token.

### When TP speeds up a model that already fits
TP = 2 on a model that fits one GPU roughly halves the weight bytes each GPU reads per step, so
single-stream decode gets faster, less than 2× because of the all-reduces. But two independent replicas
(data parallelism) give 2× throughput with zero communication at the same per-token latency. If you
care about tokens per second per user, TP; if you care about aggregate tokens per dollar at acceptable
latency, replicas, as long as each replica has enough KV room.

### Related splits
Pipeline parallelism (layer ranges, cross-node friendly, adds latency), expert parallelism (whole experts
of a mixture-of-experts model on different GPUs, exchanging tokens via all-to-all), and context or
sequence parallelism (splitting a very long prompt across GPUs for prefill) are combined with TP in large
deployments. At inference on a single node, TP alone is the common case.

## Example
Row- and column-parallel partitioning of one MLP, run with `torchrun --nproc_per_node=2`. Each rank
keeps only its slice of the weights, computes its partial output, and the single all-reduce recovers the
full-precision result; rank 0 checks it against the unsharded computation and prints the bytes the
collective moved, which is the per-layer communication cost discussed above.

```python
# torchrun --nproc_per_node=2 tp_mlp.py
import torch, torch.distributed as dist
import torch.nn.functional as F

dist.init_process_group("nccl")
rank, N = dist.get_rank(), dist.get_world_size()
torch.cuda.set_device(rank); dev = f"cuda:{rank}"
h, ff, tokens = 4096, 11008, 8                     # hidden, intermediate, tokens in this decode step
torch.manual_seed(0)                               # identical full weights on every rank
A = torch.randn(h, ff, dtype=torch.float16) * 0.02  # up projection  [h, ff]
B = torch.randn(ff, h, dtype=torch.float16) * 0.02  # down projection [ff, h]
X = torch.randn(tokens, h, dtype=torch.float16)

A_i = A[:, rank * ff // N:(rank + 1) * ff // N].to(dev)   # column-parallel slice of A
B_i = B[rank * ff // N:(rank + 1) * ff // N, :].to(dev)   # matching row-parallel slice of B
X = X.to(dev)

Z_i = F.gelu(X @ A_i)                              # [tokens, ff/N]; elementwise GeLU needs no comm
Y_i = Z_i @ B_i                                    # partial sum with full output shape [tokens, h]
dist.all_reduce(Y_i, op=dist.ReduceOp.SUM)         # the one collective for this MLP

if rank == 0:
    ref = F.gelu(X.cpu().float() @ A.float()) @ B.float()
    print("max abs error vs unsharded:", (Y_i.cpu().float() - ref).abs().max().item())
    print("bytes moved by the all-reduce:", Y_i.numel() * Y_i.element_size())
    print("weight bytes held per rank:", (A_i.numel() + B_i.numel()) * 2)
dist.destroy_process_group()
```

A sizing check for the same idea at scale: a Llama-3-70B-class model (80 layers, 8 KV heads, head_dim
128) has 320 KB of fp16 KV per token. At TP = 4 on 80 GB GPUs, each holds about 35 GB of weights and 80
KB of KV per token; with a 0.9 budget and an illustrative 2 GB activation reserve, about 35 GB per GPU
remains for cache, roughly 450k tokens. The weight-read floor per step on an H100-class GPU is 35 GB ÷
3.35 TB/s ≈ 10 ms, so single-stream decode cannot exceed about 95 tokens/s before all-reduce overhead.
At TP = 2 each GPU holds about 70 GB of weights against a 72 GB budget, leaving essentially nothing for
cache once the activation reserve is taken: not a usable deployment in bf16.

## Trade-offs
- Per-token latency falls with TP because bandwidth is pooled, but each extra GPU adds all-reduce
  overhead; the gain flips negative once collective latency rivals the shrinking step time.
- Weights and KV cache divide by N, letting a large model fit and serve long contexts, but N GPUs are
  tied to one request stream and a single GPU failure takes the whole replica down.
- Compared with data-parallel replicas, TP gives faster single-stream decode; replicas give more total
  throughput per GPU-hour. Which wins depends on whether the SLO is per-user speed or cost.
- Compared with pipeline parallelism, TP has no pipeline bubble and lower latency, but it demands a fast
  intra-node interconnect; pipeline parallelism tolerates slow links between nodes.

## Pitfalls
- Running TP across PCIe-only GPUs or across nodes and seeing decode slower than on a single GPU with a
  quantized model; the symptom is tokens/s that barely improves or regresses as TP grows.
- Choosing a TP degree that does not divide the KV head count, forcing KV replication; the symptom is
  less cache capacity than the per-GPU arithmetic predicted.
- Assuming TP = 2 doubles throughput. Throughput per GPU usually drops relative to two replicas, because
  the communication is pure overhead when the batch is already large enough to saturate one GPU.
- Forgetting that each TP rank keeps its own CUDA context, NCCL buffers and activation workspace, so the
  per-GPU memory left for cache is less than weights ÷ N would suggest.
- Mixing GPU models or driver versions within a TP group; collectives run at the pace of the slowest
  device and some combinations fail to initialise at all.

## When to use
Use TP when the model does not fit one GPU at an acceptable precision, or when it fits but single-stream
decode latency is the binding SLO and the GPUs share NVLink. Keep the degree as small as the memory
budget allows, usually the smallest power of two that leaves healthy KV room, and prefer replicas for
scaling throughput beyond that. Across nodes, use pipeline parallelism or more replicas instead. Measure
tokens/s per request and aggregate tokens/s at TP = 1, 2, 4 before committing; the curve tells you where
communication starts to dominate on your hardware. Interviewers expect you to draw the column/row split,
count the all-reduces per layer, and explain why interconnect, not FLOPs, limits TP.

## Role lens
- **Engineer:** Picks `tensor_parallel_size`, verifies NVLink topology with `nvidia-smi topo -m`, and
  benchmarks per-request versus aggregate throughput across TP degrees before locking a config.
- **Architect:** Decides the node shape and parallelism strategy per model tier: TP within NVLink nodes,
  replicas for scale, pipeline across nodes, and the failure domain each implies.
- **FDE:** Diagnoses a customer's slow 70B deployment on PCIe-attached GPUs and proposes a quantized
  single-GPU or NVLink-node alternative with measured tokens/s to back it.

## Check yourself
**Q:** A customer has eight A10G-class GPUs (24 GB each, PCIe only, no NVLink) and wants to serve a 70B
model with sub-50 ms time per output token. Will TP = 8 get them there, and what would you propose?
**A:** Fitting is possible: 140 GB of bf16 weights divided eight ways is 17.5 GB per GPU, leaving a few
GB each for KV cache, which is thin but workable with a short context cap. The latency target is the
problem. An 80-layer model issues about 160 all-reduces per decode step; over PCIe each one costs far
more than on NVLink, and the per-GPU weight read of 17.5 GB at A10G-class bandwidth is already tens of
milliseconds. Sub-50 ms per token is unrealistic on that box. I would propose a 4-bit quantized 70B at
TP = 2 or 4 to cut both bytes and collectives, evaluate its quality on their tasks, or move to an
NVLink-connected node, and I would benchmark TP = 2, 4 and 8 on their hardware rather than argue from
first principles alone.

## Quiz
### Q: In Megatron-style tensor parallelism, why is the first MLP matrix split by columns and the second by rows?
- [ ] Because column splits are faster for cuBLAS and row splits save memory
- [x] So the elementwise activation between them needs no communication and one all-reduce finishes the layer
- [ ] Because the second matrix is transposed in memory in most checkpoints
- [ ] So that each GPU can hold a different attention head
> Splitting A by columns yields independent slices of the intermediate activation; the nonlinearity acts
> on each slice alone. Splitting B by rows lets each GPU produce a partial sum of the output that one
> all-reduce completes.

### Q: What primarily limits how many GPUs you can usefully put in a tensor-parallel group at decode time?
- [ ] The total FLOPs of the GPUs
- [ ] The size of the vocabulary
- [x] The latency of the two all-reduces per layer relative to the shrinking per-step compute
- [ ] The number of transformer layers, since each GPU needs at least one
> Decode messages are tiny, so collectives are latency-bound. As N grows each step gets shorter while
> the number of collectives stays fixed, so communication becomes the dominant cost, especially without
> NVLink.

### Q: A model fits comfortably on one GPU. You need twice the aggregate throughput at the same per-token latency. Which option is usually best?
- [ ] Tensor parallelism across two GPUs
- [x] Two independent replicas behind a load balancer
- [ ] Pipeline parallelism across two GPUs
- [ ] CPU offloading half the layers to free memory for a larger batch
> Replicas double throughput with no communication overhead and keep latency unchanged. TP would lower
> per-token latency but spends bandwidth and time on all-reduces, so throughput per GPU is lower.
