---
id: cpu-offloading
module: serving-inference
name: CPU offloading
kind: pattern
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - serving-inference/gpu-memory-optimization
  - serving-inference/kv-cache-paging-and-eviction
  - serving-inference/throughput-vs-latency
  - serving-inference/engines
  - training-adaptation/quantization
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
  - infra-capacity-economics/edge-ai-deployment
sources:
  - Sheng et al., 2023 — FlexGen: High-Throughput Generative Inference of Large Language Models with a Single GPU
  - Rajbhandari et al., 2021 — ZeRO-Infinity: Breaking the GPU Memory Wall for Extreme Scale Deep Learning
  - Official docs: llama.cpp
  - Official docs: Hugging Face Accelerate
---

## TL;DR
CPU offloading keeps part of a model's weights or KV cache in host RAM (or on NVMe) and brings it to the
GPU when needed, trading scarce GPU memory for PCIe bandwidth. PCIe moves data roughly 50–100× slower
than GPU memory, and decode touches every weight every token, so offloading weights makes interactive
latency collapse unless the offloaded share is tiny or batches are huge. It is a tool for fitting,
offline throughput and tiering idle KV cache, not for low-latency serving.

## Why it exists
The model does not fit. A 70B model is about 140 GB in bf16 and about 40 GB at 4-bit, and the GPU on
the desk has 24 GB. Options are a smaller model, more aggressive quantization, more GPUs, or putting the
overflow somewhere else and fetching it on demand. Host RAM is plentiful and cheap (hundreds of GB on a
workstation), and the PCIe bus can move data in both directions while the GPU computes, so offloading
looks like free capacity.

The catch is in the arithmetic of decode. Each generated token needs every weight once. Whatever is not
resident on the GPU must cross PCIe on every step. A Gen4 x16 link moves about 25 GB/s in practice (the
theoretical figure is a little above 30 GB/s; Gen5 roughly doubles it), while an H100's HBM delivers
around 3.35 TB/s and even a consumer GPU around 1 TB/s. Streaming 20 GB of weights per token therefore
costs about 0.8 s per token, before any computation happens. Offloading just 10% of a 40 GB model still
puts roughly 160 ms of bus transfer on every token, several times the resident compute.

So "just offload the layers that don't fit" is a trap for interactive use. The idea survives because
three situations change the arithmetic: the offloaded share is small enough to be a rounding error, the
workload is offline and a huge batch amortises each weight transfer over thousands of tokens (FlexGen's
design), or the thing offloaded is KV cache for sequences that are idle or preempted, which is read once
when they resume rather than on every step.

## How it works
### What can live off the GPU
1. **Weights**, usually at layer granularity. Hugging Face Accelerate's `device_map="auto"` places
   layers on GPU until it is full and the rest on CPU (or disk via `offload_folder`); vLLM has a
   `--cpu-offload-gb` option; DeepSpeed's ZeRO-Inference and FlexGen stream weights with prefetching.
2. **KV cache**, at block granularity: vLLM's `--swap-space` (its V0 scheduler; the V1 engine preempts
   by recomputation only) reserves pinned host memory to swap the blocks of preempted sequences; tiered prefix caches keep warm system-prompt blocks in CPU RAM.
3. **Activations and optimizer state**, which matter for training (ZeRO-Offload, ZeRO-Infinity) and
   barely at all for inference.

### Two execution styles
*Streaming offload* keeps the compute on the GPU and copies each offloaded layer's weights in before it
runs, ideally prefetching layer i+1 on a second CUDA stream while layer i computes. The host buffers must
be *pinned* (page-locked) memory *because* DMA engines cannot read pageable memory; an unpinned transfer
goes through a staging copy and roughly halves effective bandwidth. Decode cannot hide the transfer
behind compute because the compute per layer for one token is microseconds, so step time is essentially
offloaded bytes ÷ PCIe bandwidth. Prefill of long prompts, or decode of very large batches, has enough
compute per layer to overlap the copies, which is exactly FlexGen's regime.

*Split execution* leaves CPU-resident layers on the CPU and computes them there. llama.cpp's
`-ngl` (number of GPU layers) works this way: the first N layers run on the GPU, the rest on CPU cores
using host memory bandwidth, typically tens of GB/s on a dual-channel desktop and a few hundred on a
server. Nothing crosses PCIe except the activations at the boundary, so the cost is CPU memory bandwidth
and CPU matmul speed rather than bus speed. For a few layers on a fast CPU this is often better than
streaming; for half the model it is still 10–40× slower than keeping it on the GPU.

### A timing model
When transfer and compute overlap, `step_time ≈ max(resident_bytes / HBM_bw, offloaded_bytes /
PCIe_bw) + overhead`; when they do not, add them. Because PCIe bandwidth is so much lower, the second
term dominates as soon as more than a few percent of weights are offloaded. Solve the formula backwards
to find how much you can offload while meeting a latency target; the answer is usually "less than you
hoped", which is why quantizing first is nearly always the better lever.

### KV cache swap versus recompute
When the block pool runs dry, a preempted sequence's blocks can be swapped to pinned host memory and
swapped back when it resumes, or dropped and recomputed by re-running prefill. Swapping an 8k-token
context of a Llama-3-8B-class model moves about 1 GB, roughly 40 ms over Gen4, while recomputing its
prefill costs on the order of a hundred TFLOP, several hundred milliseconds. For short prompts recompute
is simpler and nearly free; for long prompts swap wins. Engines pick per sequence, and the swap space
size bounds how many sequences can be parked.

### NVMe and unified memory
ZeRO-Infinity and FlexGen extend the hierarchy to NVMe (GPU ← CPU ← SSD), with GPUDirect Storage able to
DMA from disk to GPU, at bandwidths of a few GB/s per drive. At the other end, Apple silicon and Grace
Hopper systems expose unified memory shared by CPU and GPU, so a 40 GB model on a 64 GB Mac is not
"offloaded" at all; it is simply bandwidth-limited by that machine's memory, which is why llama.cpp
runs large models acceptably there.

## Example
A 70B model quantized to about 4.5 bits per weight (the 4-bit `Q4_0` layout: 32 weights × 4 bits plus a
16-bit scale is 144 bits per 32 weights) is roughly 40 GB across 80 layers, about 0.5 GB per layer. The
target machine has a 24 GB GPU with about 1 TB/s of memory bandwidth and a Gen4 x16 slot. Reserving 4 GB
for KV cache, activations and the CUDA context leaves 20 GB for weights: 40 layers resident, 40 layers
streamed. The resident half costs 20 GB ÷ 1 TB/s = 20 ms per token; the streamed half costs 20 GB ÷ 25
GB/s = 800 ms. Overlapped, the step is max(20, 800) = 800 ms, about 1.2 tokens/s, versus roughly 25
tokens/s if the whole model were resident. Offloading only 10% (4 GB) still gives max(36, 160) = 160 ms,
a 4× slowdown. The calculator below reproduces this and lets you solve for the resident share a latency
budget allows.

```python
def step_ms(total_gb, gpu_frac, hbm_tbs=1.0, pcie_gbs=25.0, overlap=True):
    resident, offloaded = total_gb * gpu_frac, total_gb * (1 - gpu_frac)
    t_gpu = resident / hbm_tbs                 # GB / (TB/s) = ms, since 1 TB/s is 1 GB per ms
    t_bus = offloaded / pcie_gbs * 1000        # GB / (GB/s) = s, converted to ms
    return max(t_gpu, t_bus) if overlap else t_gpu + t_bus

for frac in (1.0, 0.9, 0.5):
    ms = step_ms(40, frac)
    print(f"{frac:.0%} resident -> {ms:6.0f} ms/token, {1000/ms:5.1f} tok/s")

budget_ms = 100                                  # latency target per token
max_offload_gb = budget_ms / 1000 * 25.0         # bytes PCIe can move within the budget
print(f"a {budget_ms} ms budget tolerates at most {max_offload_gb:.1f} GB offloaded")
```

## Trade-offs
- Offloading turns an impossible deployment into a possible one, but every offloaded gigabyte of weights
  adds roughly 40 ms per token over Gen4 PCIe; worthwhile only when the share is tiny or latency is
  irrelevant.
- Large-batch offline generation amortises weight transfers across many tokens per step, so throughput
  can be acceptable where single-stream latency is not; it flips back to useless when requests must be
  answered one at a time.
- KV swap preserves long contexts cheaply compared with recomputation, but pinned host memory is reserved
  up front and PCIe traffic competes with any weight streaming on the same link.
- Split execution on the CPU avoids bus traffic and suits a few layers on a fast machine, yet CPU matmul
  throughput caps batch size, so it does not scale to many concurrent users.

## Pitfalls
- `device_map="auto"` silently placing layers on CPU when the GPU is slightly too small; the symptom is a
  model that "works" at one or two tokens per second with the GPU at 5% utilization and host RAM full.
- Streaming from pageable instead of pinned memory, which halves effective bandwidth and adds CPU copy
  overhead; visible as transfer time far above the PCIe spec.
- Sizing swap space too small, so the engine falls back to recomputation for long contexts under load;
  the symptom is a throughput cliff and prefill-heavy GPU traces when concurrency rises.
- Benchmarking on a workstation with a Gen5 x16 slot and deploying to a cloud instance whose GPU sits on
  a shared or narrower link; identical settings, half the speed.
- Treating offloading as an alternative to quantization. Quantizing from fp16 to 4-bit removes 75% of
  the bytes with modest quality cost; offloading 75% of the bytes multiplies latency by tens.

## When to use
Reach for weight offloading when a model is a few percent too large for the GPU and a short-term fix is
acceptable, for offline batch jobs where tokens per hour matter and latency does not, for local
experimentation on a machine that is not going to serve anyone, or when the remaining layers can run on
a strong CPU via split execution. Prefer quantization, a smaller model, or tensor parallelism for anything
interactive. Use KV offloading deliberately: size the swap space for your long-context tail and watch
preemption and swap metrics. Measure tokens/s with and without offload before deciding; the formula above
predicts the result well. In interviews, the test is whether you can compute the PCIe penalty rather
than assert that offloading "makes it fit".

## Role lens
- **Engineer:** Configures layer placement or swap space, pins host buffers, and reads GPU utilization
  and transfer timings to spot accidental offloading before it ships.
- **Architect:** Decides where offloading is permitted (batch pipelines, dev boxes) and where it is banned
  (interactive SLOs), and chooses quantization or more GPUs as the standard remedy.
- **FDE:** Gets a customer's oversized model running on the hardware they already own for a pilot, with
  explicit latency expectations, and a plan to move to properly sized GPUs for production.

## Check yourself
**Q:** A customer runs a 4-bit 70B model on a single 48 GB GPU by offloading the overflow to CPU and
reports 3 tokens/s. They ask whether a faster CPU would help. What is your analysis and recommendation?
**A:** A 4-bit 70B model is around 40 GB of weights; with KV cache and runtime overhead it slightly
exceeds 48 GB, so a few gigabytes are offloaded. If the engine streams those layers over PCIe, step time
is dominated by offloaded bytes divided by bus bandwidth, roughly 40 ms per gigabyte on Gen4, and a
faster CPU changes nothing; 3 tokens/s is about 330 ms per token, consistent with roughly 7–8 GB crossing
a Gen4 link every step. If it computes them on the CPU, host memory bandwidth and CPU matmul speed
matter, and a faster CPU helps a little but not to GPU-class speed. The real fix is to remove the
overflow: cap context length so the KV cache shrinks, use a slightly smaller quantization or a model
with fewer KV heads, or add a second GPU with tensor parallelism. I would measure the offloaded size and
transfer time first, then predict the gain of each option before they buy anything.

## Quiz
### Q: Why does offloading even a small fraction of weights hurt decode latency so much?
- [ ] The CPU must re-quantize the weights on every transfer
- [ ] GPU kernels cannot start until all layers are resident
- [x] Every decode step needs every weight once, and PCIe bandwidth is orders of magnitude below GPU memory bandwidth
- [ ] Offloaded layers lose their KV cache and must recompute attention
> Decode streams the full weight set per token. Bytes that live across PCIe move at tens of GB/s rather
> than terabytes per second, so a small offloaded share can take longer than the entire resident
> computation.

### Q: When is swapping a preempted sequence's KV cache to CPU preferable to recomputing it?
- [ ] Always, because recomputation wastes GPU memory
- [x] When the context is long, so moving the cache over PCIe is cheaper than re-running prefill
- [ ] When the context is short, because small transfers are nearly free
- [ ] Only when the model uses grouped-query attention
> Swap cost scales with cache bytes, recompute cost with prefill FLOPs over the prompt. For long prompts
> the transfer is tens of milliseconds while prefill is hundreds; for short prompts recompute is simpler
> and about as fast.

### Q: Why must host buffers used for GPU transfers be pinned?
- [ ] Pinned memory is compressed, so fewer bytes cross the bus
- [ ] The GPU cannot address memory above 4 GB otherwise
- [x] DMA engines need page-locked memory; otherwise data goes through a staging copy and bandwidth drops
- [ ] Pinned memory lets the GPU execute CPU layers directly
> Page-locked memory cannot be swapped out, so the DMA engine can read it directly. Pageable memory
> forces the driver to copy into a pinned staging buffer first, roughly halving effective transfer
> bandwidth.
