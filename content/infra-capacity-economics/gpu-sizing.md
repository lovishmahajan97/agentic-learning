---
id: gpu-sizing
module: infra-capacity-economics
name: GPU sizing (VRAM math)
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - model-internals/kv-cache
  - serving-inference/kv-cache-memory-estimation
  - serving-inference/gpu-memory-optimization
  - serving-inference/tensor-parallelism-at-inference
  - training-adaptation/quantization
  - training-adaptation/distributed-training
  - infra-capacity-economics/a100-h100-l4-selection
sources:
  - Rajbhandari et al., 2020 — ZeRO: Memory Optimizations Toward Training Trillion Parameter Models
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Official docs: vLLM (engine arguments, gpu_memory_utilization)
---

## TL;DR
GPU memory for inference is weights + KV cache + activations/workspace + runtime overhead; for
training it is weights + gradients + optimizer state + activations, about 16 bytes per parameter with
mixed-precision Adam before activations are counted. The arithmetic takes a minute and decides the
card, the card count, the precision and the concurrency you can promise. Skipping it is how both
out-of-memory crashes and half-empty 80 GB cards happen.

## Why it exists
A GPU cannot page to disk the way a CPU process can. When the device runs out of memory, the engine
does not slow down gracefully; it throws a CUDA out-of-memory error and the process dies, often
minutes into start-up after loading most of a checkpoint. The opposite failure is quieter and more
expensive: a team provisions eight 80 GB cards for a model whose weights occupy 16 GB, then wonders
why the bill is high and throughput is low. Both come from treating VRAM as a vibe instead of a sum.

The naive rule, "the model needs about as much memory as its parameter count in GB", is wrong in
both directions. In fp16 every parameter is two bytes, so a 70-billion-parameter model needs 140 GB
for weights alone, nearly twice the naive estimate. And weights are only the fixed part: every
in-flight request grows a KV cache proportional to its context length, prefill needs transient
activation memory, and the CUDA context and engine allocations take space before the first request
arrives. For fine-tuning the picture flips again: gradients and optimizer state make a full fine-tune
of that 70B model need hundreds of gigabytes more than serving it. VRAM math is the discipline of
writing each term down, with the model's actual shapes, before buying or promising anything.

## How it works
### The inference budget
```
VRAM        ≈ weights + KV cache + activations/workspace + runtime overhead
weights      = n_params × bytes_per_param
KV per token = 2 × n_layers × n_kv_heads × head_dim × bytes_per_element
KV cache     = KV per token × tokens in flight (summed over all concurrent sequences)
```
Bytes per parameter: fp32 4, fp16/bf16 2, int8/fp8 1, int4 roughly 0.5 plus a few percent for
scales and zero-points (call it 0.55). The factor 2 in the KV term is one key and one value vector;
`n_kv_heads` rather than `n_heads` is why grouped-query attention matters: Llama-3 models use 8 KV
heads against 32 or 64 query heads, which cuts the cache 4–8× relative to full multi-head attention.

Runtime overhead is everything that is neither weights nor cache: the CUDA context, cuBLAS/cuDNN
workspaces, captured CUDA graphs and the engine's own buffers. Order of magnitude, illustrative, it
is around a gigabyte per process, and it exists before any request. Activations during prefill scale
with (tokens in the batch × hidden size) per layer and are transient; engines bound them by capping
how many prompt tokens one forward pass may contain (chunked prefill), so the practical rule is to
leave headroom rather than compute them exactly.

Engines expose the budget directly. vLLM's `gpu_memory_utilization` (default 0.9) is the fraction of
the card the engine may use *because* other processes and driver allocations need the rest; it
profiles weights plus peak activations once at start-up and gives everything remaining to KV-cache
blocks. That remaining space, divided by KV-per-token, is the total number of tokens the server can
hold across all concurrent requests, which is the real capacity number.

### Two models, worked
Llama-3-8B: about 8.0B parameters, 32 layers, 8 KV heads, head_dim 128. Weights in bf16: 16 GB.
KV per token = 2 × 32 × 8 × 128 × 2 = 131,072 bytes = 128 KB.

Llama-3-70B: about 70.6B parameters, 80 layers, 8 KV heads, head_dim 128. Weights in bf16: 141 GB.
KV per token = 2 × 80 × 8 × 128 × 2 = 327,680 bytes = 320 KB.

Now the fits, taking a 0.9 utilisation budget and ~1 GB overhead per card:
- 8B bf16 on a 24 GB L4: 21.6 − 16 − 1 ≈ 4.6 GB for KV ≈ 35k tokens ≈ eight or nine 4k-token
  conversations.
- 8B bf16 on an 80 GB H100: 72 − 16 − 1 = 55 GB ≈ 420k tokens ≈ a hundred 4k conversations, at which
  point memory bandwidth rather than memory capacity becomes the limit.
- 70B bf16 on two 80 GB H100s: 144 − 141.2 − 2 ≈ 0.8 GB for KV ≈ 2.4k tokens *total*. It "fits" but can
  barely serve one request; this is the case the formula catches and intuition misses.
- 70B in fp8 on the same two cards: 144 − 71 − 2 = 71 GB ≈ 220k tokens, a comfortable server.
- 70B in int4 (~39 GB) on one 80 GB card: 72 − 39 − 1 = 32 GB ≈ 100k tokens, with a quality cost that
  must be evaluated, not assumed.

A long-context request is the other surprise: one 128k-token prompt on the 8B model is 128k × 128 KB
= 16 GiB (about 17 GB) of cache, as much as the weights, and a single such request can evict everything else.

### The training budget
Full fine-tuning with mixed precision and Adam keeps, per parameter: 2 bytes of bf16 weights, 2 bytes
of bf16 gradient, 4 bytes of fp32 master weight, and 4 + 4 bytes of Adam first and second moments, for
16 bytes per parameter, the accounting the ZeRO paper uses. The 8B model therefore needs ~128 GB
before activations, so it does not fit one 80 GB card and must be sharded (ZeRO/FSDP) across several.
Activation memory adds batch × sequence × hidden per layer, and attention without a fused kernel adds
a sequence-squared term; gradient checkpointing trades roughly a third more compute for storing only
layer boundaries. LoRA changes the equation *because* the base weights are frozen: they cost 2 bytes
per parameter with no gradient or optimizer state, and only the adapter (typically well under 1% of
parameters) carries the 16-byte burden, so the 8B model fine-tunes in roughly 16 GB plus activations.
QLoRA stores the frozen base in 4-bit, about 4.5 GB for 8B, which is what makes 24 GB cards viable.
Eight-bit optimizers (for example bitsandbytes' 8-bit Adam) store each Adam moment in one byte with
block-wise scales, cutting the optimizer term from 8 bytes per parameter to about 2.

### Multi-GPU arithmetic
Tensor parallelism divides both weights and KV cache by the TP degree, while each card keeps its own
overhead; the head count must divide evenly (8 KV heads means TP up to 8 without replication).
Pipeline parallelism divides layers instead and is used when cards lack NVLink or the model spans
nodes; each stage holds the KV cache only for its own layers. Either way, "fits on N cards" means the
per-card sum, not the total, is under budget.

### Mixture-of-experts: size by total, not active, parameters
An MoE model routes each token through a few experts, so its *compute* per token follows the active
parameter count while its *memory* follows the total, *because* every expert must be resident for
whichever token picks it. Mixtral-8x7B has about 46.7B total and 12.9B active parameters: in bf16 it
needs ~93 GB for weights, like a dense 47B model, even though each token does the arithmetic of a 13B
one. Its KV cache follows the attention shape (32 layers, 8 KV heads, head_dim 128), so 128 KB per token,
the same as Llama-3-8B. Bandwidth sits between the two: at batch 1 a decode step reads only the active
experts, but at serving batch sizes different tokens hit different experts and the step reads close to
all of them. Size the card by total parameters; estimate latency with the batch you will run.

### Order of decisions
Precision first (it halves or quarters the dominant term), then card and count (capacity), then
`max_model_len` (the worst-case single request), then concurrency (what remains ÷ KV per token ÷
typical context). Reverse the order and you will size the card to a concurrency figure that the
precision choice later invalidates.

## Example
A twenty-line planner that reproduces the worked numbers above. It uses decimal gigabytes (1e9) to
match how vendors quote card memory; engines report GiB, so expect a 7% difference in their logs.

```python
GB = 1e9

def kv_per_token(n_layers, n_kv_heads, head_dim, bytes_el=2):
    return 2 * n_layers * n_kv_heads * head_dim * bytes_el        # one K and one V per layer

def plan(name, params_b, n_layers, n_kv_heads, head_dim, vram_gb, n_gpus=1,
         bytes_w=2, util=0.9, overhead_gb=1.0, ctx=4096):
    weights = params_b * GB * bytes_w
    budget = vram_gb * GB * util * n_gpus                       # engine's usable share
    kv_room = budget - weights - overhead_gb * GB * n_gpus
    tok = kv_per_token(n_layers, n_kv_heads, head_dim)
    tokens = max(kv_room, 0) / tok
    print(f"{name:26s} weights={weights/GB:6.1f}GB KV/tok={tok/1024:.0f}KB "
          f"KV room={kv_room/GB:5.1f}GB ~{tokens:,.0f} tok ≈ {tokens/ctx:.0f} seqs@{ctx}")

plan("8B bf16 / L4 24GB",       8.0, 32, 8, 128, 24)             # ~35k tok, ~9 seqs
plan("8B bf16 / H100 80GB",     8.0, 32, 8, 128, 80)             # ~420k tok, ~100 seqs
plan("70B bf16 / 2xH100",      70.6, 80, 8, 128, 80, n_gpus=2)   # ~2.4k tok: unusable
plan("70B fp8 / 2xH100",       70.6, 80, 8, 128, 80, n_gpus=2, bytes_w=1)     # ~220k tok
plan("70B int4 / 1xH100",      70.6, 80, 8, 128, 80, bytes_w=0.55)            # ~100k tok
```

## Trade-offs
- Lower weight precision frees memory for cache and raises concurrency, but int4 costs measurable
  quality on some tasks and dequantisation kernels can slow decode; it pays when memory, not quality,
  is the binding constraint.
- A higher `gpu_memory_utilization` gives more KV blocks, but leaves less slack for activation spikes
  and co-located processes; push it only on a card nothing else touches.
- Tensor parallelism across more cards multiplies KV capacity and bandwidth, yet adds per-layer
  communication and per-card overhead; it stops helping once the model already fits with KV headroom.
- LoRA and QLoRA make fine-tuning fit small cards at the cost of a smaller parameter update and, for
  QLoRA, slower steps from dequantisation; full fine-tuning is only worth its multi-card cost when the
  adapter measurably underperforms.

## Pitfalls
- Counting weights and forgetting overhead and activations; symptom is an OOM at start-up or on the
  first long prompt even though "the model fits".
- Sizing concurrency on the average context instead of `max_model_len`; symptom is sudden request
  preemption or queueing whenever a few long documents arrive together.
- Assuming a fine-tune needs the same memory as serving; symptom is an OOM in the first optimizer
  step after the forward pass succeeded, because Adam state is allocated lazily.
- Dividing total memory across cards instead of checking per-card budgets with TP; symptom is one
  rank OOMing while the others wait on NCCL.
- Sizing an MoE model by its active parameter count; symptom is an OOM at weight load on a card the
  "13B-class" label said was ample.
- Treating int4 as "half of fp16"; symptom is a card that fits the weights but throughput below the
  fp16 figure because dequantisation dominates at the batch sizes used.

## When to use
Run the arithmetic every time a model, card, precision, context limit or concurrency target changes,
and before any procurement conversation. The inputs are all public: parameter count and layer/head
shapes from the model config, memory from the card datasheet. Reach for it when someone asks "how many
users can this card serve", "why did we OOM at 2 a.m." or "can we fine-tune this on what we have".
Measure the engine's reported KV-block count against your estimate; a large gap means an overhead
term you missed. In interviews, deriving bytes-per-token from the config and turning it into a
concurrency figure, then adjusting for precision and TP, is the expected depth for both engineering
and architecture roles.

## Role lens
- **Engineer:** Computes weights and KV per token from the model config before setting
  `max_model_len`, `gpu_memory_utilization` and batch limits, and validates against the engine's
  start-up log.
- **Architect:** Owns the fit table — which models run on which card tier at which precision — and
  uses it to size clusters and to decide when quantisation or TP is mandatory.
- **FDE:** Translates a customer's "we have four A100s" into which models and context lengths are
  realistic, and shows the arithmetic when a 70B request will not fit as asked.

## Check yourself
**Q:** You have one 80 GB card and a customer wants Llama-3-70B serving twenty concurrent 8k-token
chats. Walk through whether it fits and what you would change.
**A:** bf16 weights are 141 GB, so no. fp8 weights are 71 GB; with a 72 GB budget at 0.9 utilisation
and ~1 GB overhead that leaves nothing for cache, so still no. int4 is about 39 GB, leaving ~32 GB
for KV at 320 KB per token, roughly 100k tokens; twenty chats at 8k need 160k, so about twelve fit.
Options: quantise the KV cache to 8-bit, doubling capacity to ~200k tokens, which meets the target if
quality evaluation passes; cap concurrency at twelve and queue; or add a second card and run fp8 with
TP=2 for ~220k tokens and better throughput. A strong answer also notes that twenty concurrent 8k
contexts will be bandwidth-bound on one card, so latency targets need checking too.

## Quiz
### Q: Why does a full fine-tune need roughly eight times the memory of serving the same model in bf16?
- [ ] Training must keep a copy of every activation for the whole dataset
- [x] Gradients, fp32 master weights and two Adam moments add about 14 bytes per parameter on top of the 2-byte weights
- [ ] Backpropagation requires the weights in fp64 for numerical stability
- [ ] The KV cache must be stored for every training sequence simultaneously
> Mixed-precision Adam keeps bf16 weights and gradients plus fp32 master weights and two fp32 moments,
> 16 bytes per parameter versus 2 for inference, before activations. Training does not use a KV cache
> and master weights are fp32, not fp64.

### Q: Llama-3-70B in bf16 (141 GB) is loaded on two 80 GB cards with tensor parallelism. What is the practical problem?
- [ ] The head count is not divisible by two, so TP fails
- [ ] NCCL cannot shard models larger than one card's memory
- [x] After weights and overhead only about a gigabyte remains for KV cache, so the server can hold only a few thousand tokens
- [ ] bf16 is not supported across tensor-parallel ranks
> The usable budget is about 144 GB at 0.9 utilisation; weights take 141 GB and overhead about 2 GB,
> leaving under a gigabyte, roughly 2.4k tokens of cache at 320 KB per token. The model technically loads but cannot serve
> real traffic, which is why fp8 or four cards are the usual choices.

### Q: Which single change most increases the number of concurrent 4k-token requests an 8B bf16 model can hold on a 24 GB card?
- [ ] Raising the number of CPU threads for tokenisation
- [ ] Switching from grouped-query to multi-head attention
- [x] Quantising the weights to int4, freeing roughly 11 GB for KV cache
- [ ] Increasing `max_model_len` to 32k
> With ~4.5 GB of cache room, int4 weights (~4.4 GB instead of 16 GB) more than triple the space for
> KV blocks. Multi-head attention would multiply KV per token by four and reduce concurrency; a larger
> `max_model_len` changes the worst case per request, not the total capacity.
