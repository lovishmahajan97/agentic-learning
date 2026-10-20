---
id: gpu-utilization-and-memory-bandwidth
module: infra-capacity-economics
name: GPU utilization & memory bandwidth
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - serving-inference/prefill-vs-decode
  - serving-inference/continuous-batching
  - serving-inference/throughput-vs-latency
  - serving-inference/tpot
  - model-internals/flashattention
  - model-internals/kv-cache
  - infra-capacity-economics/gpu-sizing
sources:
  - Williams, Waterman & Patterson, 2009 — Roofline: An Insightful Visual Performance Model for Multicore Architectures
  - Pope et al., 2022 — Efficiently Scaling Transformer Inference
  - Chowdhery et al., 2022 — PaLM: Scaling Language Modeling with Pathways (model FLOPs utilization)
  - Official docs: NVIDIA DCGM (profiling metrics)
---

## TL;DR
The "GPU-Util" number in `nvidia-smi` only says a kernel was running, not that the chip was busy.
Real throughput is bounded by the roofline: for every byte moved from GPU memory the chip can do a
fixed number of floating-point operations, roughly 150 on an A100 and 300 on an H100 at bf16. LLM
decode does about one operation per bf16 weight byte per sequence, so small batches are memory-bandwidth-bound and the fix is batching, fewer bytes or faster memory, not more FLOPs.

## Why it exists
Teams buy GPUs for their FLOPs and are then surprised that an H100 serving a chat model produces
tokens at a rate that would embarrass its spec sheet, while the dashboard shows 98% utilisation.
Both symptoms come from the same mistake: treating the GPU as a compute device when, for the workload
in question, it is a memory device. Every decode step of a transformer must stream every weight byte
from HBM into the compute units to produce one token per sequence. If only one sequence is in flight,
each byte is used for a single multiply-add and the tensor cores sit idle most of the cycle, waiting on
memory.

The naive diagnosis, "utilisation is high, so we need more GPUs", spends money to replicate idle
hardware. The naive fix, "buy the card with the most TFLOPs", buys the wrong axis. The roofline model
gives the right question: how many operations does this workload perform per byte it moves (its
arithmetic intensity), and is that above or below the point where the card switches from
bandwidth-limited to compute-limited? That one ratio explains why batching multiplies throughput
almost for free, why quantisation speeds up decode even when the math is not faster, why prefill and
decode behave like different workloads, and which utilisation metric to trust. Without it capacity
planning is guesswork dressed up as a dashboard.

## How it works
### The roofline
A kernel's attainable throughput is the lower of two ceilings:
```
attainable FLOP/s = min( peak FLOP/s , arithmetic intensity × memory bandwidth )
arithmetic intensity = FLOPs performed / bytes moved from memory   (FLOP per byte)
ridge point          = peak FLOP/s / bandwidth                      (FLOP per byte)
```
Below the ridge point the kernel is memory-bound and runs at `intensity × bandwidth`; above it, the
kernel is compute-bound and runs near peak. Using NVIDIA's datasheet figures for dense bf16 tensor
throughput and memory bandwidth, the ridge sits around 312 TFLOP/s ÷ 2.0 TB/s ≈ 156 FLOP/byte for an
A100 80 GB SXM, about 989 ÷ 3.35 ≈ 295 for an H100 SXM, and roughly 121 ÷ 0.3 ≈ 400 for an L4 (whose
bandwidth is small relative to its compute). The number to remember is "hundreds": a workload must
perform hundreds of operations per byte before the tensor cores are the bottleneck.

### Decode intensity: why batch size is the lever
In one decode step, each weight is multiplied by one activation and accumulated: two FLOPs per
parameter per sequence. In bf16 each parameter is two bytes and must be read once per step regardless
of how many sequences share the step. So for a batch of *B* sequences the dense layers do about
`2 × B` FLOPs per 2 bytes, an intensity of roughly *B* FLOP/byte. At *B* = 1 that is 1 FLOP/byte
against a ridge of ~300: the H100 is using well under 1% of its arithmetic and is entirely limited by
how fast HBM can stream 16 GB of 8B-model weights. Doubling the batch doubles tokens per second at
almost no cost in per-token latency, until *B* approaches the ridge point. This is the whole case for
continuous batching.

The lower bound on a decode step is therefore `bytes read ÷ bandwidth`. For an 8B bf16 model: 16 GB ÷
3.35 TB/s ≈ 4.8 ms per step on an H100 (at most ~200 tokens/s for a single stream); on an L4 at
300 GB/s it is ~53 ms (at most ~19 tokens/s). Real engines land below these bounds, but the ratio
between cards holds, which is why decode speed tracks bandwidth rather than TFLOPs.

### Attention does not batch the same way
Weights are shared across sequences; KV caches are not. Each sequence's attention reads its own cache,
so attention's bytes grow with *B* × context and its intensity stays low no matter the batch. With a
hundred 4k-token sequences on the 8B model the step reads ~54 GB of cache (100 × 4096 × 128 KB) on top
of 16 GB of weights, so the cache, not the weights, dominates the memory traffic. Grouped-query
attention, KV quantisation and FlashAttention's tiling (which avoids writing the full attention matrix
to HBM) all exist to reduce those bytes.

### Prefill is the other regime
Prefill processes all prompt tokens in one pass, so each weight byte serves every prompt token:
intensity is roughly the number of tokens in the pass. A 2,000-token prompt is far above the ridge
and compute-bound. That is why time-to-first-token scales with prompt length and TFLOPs, while
time-per-output-token scales with bandwidth, and why engines schedule prefill and decode differently.

### What the utilisation metrics measure
- `nvidia-smi` **GPU-Util** (DCGM `DCGM_FI_DEV_GPU_UTIL`): fraction of the sample period in which *any*
  kernel was executing. One tiny kernel per millisecond reads as 100%.
- DCGM **SM activity** (`DCGM_FI_PROF_SM_ACTIVE`): fraction of time at least one warp was resident on
  an SM; **SM occupancy** (`DCGM_FI_PROF_SM_OCCUPANCY`): how full the SMs were; **tensor pipe active**
  (`DCGM_FI_PROF_PIPE_TENSOR_ACTIVE`): whether the matrix units did work; **DRAM active**
  (`DCGM_FI_PROF_DRAM_ACTIVE`): memory bandwidth in use. A decode-bound server shows high DRAM
  activity and low tensor activity; a prefill-heavy one shows the reverse.
- **Model FLOPs utilisation (MFU)**: observed tokens/s × FLOPs per token ÷ peak FLOP/s. It is the
  honest single number for training; for decode it is naturally low and should be read alongside
  bandwidth utilisation (bytes moved per second ÷ peak bandwidth).

### Other things that masquerade as GPU limits
Kernel-launch overhead when batches are tiny (CUDA graphs fix it), Python-side scheduling and
tokenisation that leave the device starving, and host-to-device copies over PCIe. The tell is low DRAM
*and* low tensor activity together: the GPU is waiting on the host, and no card upgrade will help.

## Example
A small roofline estimator for decode. It takes datasheet bandwidth and peak compute, the bytes a step
must read (weights plus the KV cache of every sequence in the batch), and reports the bandwidth-bound
step time, the tokens/s ceiling and the batch at which the step becomes compute-bound. The outputs
for an 8B bf16 model reproduce the figures above; they are upper bounds, not measurements.

```python
CARDS = {  # datasheet-level figures, rounded: (memory bandwidth B/s, dense bf16 FLOP/s)
    "L4":       (300e9,  121e12),
    "A100-80":  (2.0e12, 312e12),
    "H100-SXM": (3.35e12, 989e12),
}

def decode_step(card, params=8.0e9, bytes_per_param=2, batch=1, ctx=4096, kv_per_token=131072):
    bw, peak = CARDS[card]
    weight_bytes = params * bytes_per_param
    kv_bytes = batch * ctx * kv_per_token              # every sequence reads its own cache
    bytes_read = weight_bytes + kv_bytes
    flops = 2 * params * batch                          # dense layers only; attention omitted
    intensity = flops / bytes_read
    t_mem, t_compute = bytes_read / bw, flops / peak
    t = max(t_mem, t_compute)                           # roofline: the slower ceiling wins
    ridge = peak / bw
    print(f"{card:9s} B={batch:4d} ctx={ctx:5d} read={bytes_read/1e9:6.1f} GB "
          f"intensity={intensity:6.1f} ridge={ridge:4.0f}  step={t*1e3:6.1f} ms "
          f"tok/s={batch/t:8.0f} bound={'memory' if t_mem >= t_compute else 'compute'}")

decode_step("H100-SXM", batch=1)                  # ~4.8 ms, ~200 tok/s, intensity ~1
decode_step("L4", batch=1)                        # ~53 ms, ~19 tok/s
decode_step("H100-SXM", batch=64)                 # weights amortised; KV now a large share
decode_step("H100-SXM", batch=100, ctx=4096)      # ~54 GB of KV per step dominates
decode_step("H100-SXM", batch=8, bytes_per_param=0.55)  # int4: fewer bytes, faster step
```

## Trade-offs
- Larger batches raise tokens/s nearly linearly while memory-bound, but each sequence's latency
  grows once the step becomes compute-bound or KV traffic dominates; the flip is near the ridge
  point or when cache bytes exceed weight bytes.
- Weight quantisation cuts bytes per step and speeds decode, yet dequantisation kernels can erase the
  gain at large batches where compute, not bandwidth, is the limit.
- Speculative decoding converts spare compute into more tokens per weight read, which pays when
  decode is bandwidth-bound and acceptance rates are high, and costs throughput otherwise.
- A faster-memory card (H100 over A100) buys decode speed roughly in proportion to bandwidth, not
  price; for prefill-heavy or training workloads the FLOPs gap matters more.

## Pitfalls
- Reading `nvidia-smi` GPU-Util as "busy"; symptom is a fleet at 95% utilisation that delivers a
  small fraction of the tokens/s the bandwidth bound predicts.
- Scaling out on utilisation instead of on queue depth or tokens/s; symptom is more cards, same
  per-card throughput, and a bill that doubled.
- Benchmarking at batch 1 and extrapolating; symptom is a capacity plan off by an order of magnitude
  because batching was never measured.
- Ignoring attention's share of memory traffic; symptom is throughput collapsing as contexts get
  long, even though weights never changed.
- Blaming the GPU for host-side stalls; symptom is low DRAM *and* low tensor activity together, with
  a single Python process pegged at 100% CPU.

## When to use
Reach for the roofline whenever throughput is below expectation, when choosing between cards, or when
someone proposes "more GPUs" as the fix. Compute the bytes a decode step must read, divide by
bandwidth, and compare with observed time-per-output-token: if they are close you are bandwidth-bound
and need batching, quantisation or faster memory; if observed time is far worse, look at the host,
kernel launches or scheduling. Monitor DCGM SM activity, tensor activity and DRAM activity rather than
GPU-Util, and track tokens/s per card as the business metric. In interviews, "why is decode
memory-bound and what does batching do to it" is a standard question; the strong answer derives the
intensity of ~B FLOP/byte and names the ridge point.

## Role lens
- **Engineer:** Tunes batch limits, enables CUDA graphs and quantisation, and uses DCGM profiling
  metrics to prove whether a slow server is bandwidth-, compute- or host-bound.
- **Architect:** Chooses cards by bandwidth for decode-heavy services and by FLOPs for training or
  prefill-heavy ones, and sets capacity targets in tokens/s per card rather than utilisation.
- **FDE:** Shows a customer why their "fully utilised" cluster is idle in practice, and sizes the
  batching change that recovers throughput before recommending new hardware.

## Check yourself
**Q:** A customer's H100 serves an 8B bf16 model at 40 tokens/s per request with four concurrent
requests, and `nvidia-smi` shows 97% utilisation. They want to buy three more H100s. What do you tell
them?
**A:** The bandwidth bound for one decode step is about 16 GB ÷ 3.35 TB/s ≈ 5 ms, so a single stream
could approach 200 tokens/s and a batch of four should deliver far more than 160 aggregate. At 40
tokens/s per stream the step takes ~25 ms, five times the bound, and GPU-Util at 97% only means some
kernel was always running. Check DCGM: low DRAM activity and low tensor activity point to host-side
stalls, kernel-launch overhead or a misconfigured engine, which more cards will not fix. Raise
concurrency and batch size, enable CUDA graphs, and confirm the engine is using fused attention.
Buy hardware only after per-card tokens/s approaches the bandwidth bound at the target batch.

## Quiz
### Q: Why does doubling the batch size roughly double decode throughput on a bandwidth-bound GPU?
- [ ] The GPU clocks up under heavier load
- [x] Weights are read from memory once per step and shared by every sequence in the batch, so the dominant cost is amortised
- [ ] Larger batches compress the KV cache
- [ ] Tensor cores only activate above a minimum batch size
> A decode step must stream all weights from HBM regardless of batch size; with more sequences each
> byte does more useful work, raising arithmetic intensity. KV traffic still grows per sequence, so the
> gain is not unlimited.

### Q: Which DCGM signal most directly reveals that a serving GPU is memory-bandwidth-bound?
- [ ] High GPU-Util with low power draw
- [ ] High SM occupancy
- [x] High DRAM activity together with low tensor-pipe activity
- [ ] Low PCIe transmit throughput
> Bandwidth-bound decode keeps the memory system busy while the matrix units wait, so DRAM activity is
> high and tensor activity low. GPU-Util cannot distinguish that state from a fully compute-bound one.

### Q: Prefill of a 3,000-token prompt and decoding of its reply run on the same GPU. Which statement is correct?
- [ ] Both phases are memory-bound because they read the same weights
- [x] Prefill is compute-bound because every weight serves thousands of tokens per pass, while decode is memory-bound at one token per sequence per step
- [ ] Decode is compute-bound because it runs more steps
- [ ] Prefill is memory-bound because it must write the whole KV cache
> Arithmetic intensity in prefill is roughly the number of tokens in the pass, far above the ridge
> point; in decode it is roughly the batch size, usually far below it. Writing the KV cache is small
> relative to the matrix multiplications over thousands of tokens.
