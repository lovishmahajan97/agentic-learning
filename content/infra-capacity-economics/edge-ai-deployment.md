---
id: edge-ai-deployment
module: infra-capacity-economics
name: Edge AI deployment
kind: concept
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - infra-capacity-economics/hybrid-on-prem
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
  - model-internals/small-language-models
  - training-adaptation/quantization
  - training-adaptation/knowledge-distillation
  - system-design/slm-vs-llm-trade-offs
  - serving-inference/quantization-quality-evaluation
  - serving-inference/engines
sources:
  - Frantar et al., 2022 — GPTQ: Accurate Post-Training Quantization for Generative Pre-trained Transformers
  - Official docs: llama.cpp
  - Official docs: ONNX Runtime
---

## TL;DR
Edge AI runs the model on the device that has the data: a phone, laptop, browser, vehicle, factory
controller or retail box. It exists for latency with no network round trip, operation when offline,
privacy because the data never leaves, and cost because the user's hardware does the compute. The
price is a hard budget on memory, bandwidth, power and model size, so edge means small or
compressed models on a device-specific runtime, usually with a cloud fallback for what the small
model cannot do.

## Why it exists
A cloud call costs a network round trip, needs connectivity, uploads the user's data and bills per
token. For many workloads none of that matters. For some it is disqualifying: a keyboard
suggestion or voice assistant that waits 400 ms for a server feels broken; a field technician in a
basement has no signal; a camera stream from a factory line cannot be shipped to a datacenter for
every frame; a health app's users will not accept their data leaving the phone; and a product with
a hundred million installs cannot pay per-token prices for every interaction. Each of those is a
reason to run the model where the data is.

The naive answer, "ship the model we run in the cloud", fails on arithmetic. A 70B-parameter model
in fp16 is roughly 140 GB of weights; a phone gives an app a few gigabytes at most, and even a
laptop with 16 GB of unified memory comfortably holds only a 4-bit model of about 7–8B parameters
(a 13–14B model is a squeeze) once the OS, other apps and the KV cache are counted. Decode speed is bound by memory bandwidth, which on a phone is a small fraction of a
datacenter GPU's, so the same model that streams at 100 tokens per second in the cloud would crawl
on device even if it fit. Thermal limits add a third constraint: sustained inference throttles the
chip and drains the battery. The opposite naive answer, "quantize everything to 4-bit and hope",
fails on quality: compression without evaluation produces models that pass a demo and fail in the
field. Edge deployment is therefore a budgeting and engineering discipline, not a flag on the cloud
model.

## How it works
### Start from the device budget
Three numbers bound the design. **Memory**: weights plus KV cache plus activations must fit in what
the platform grants an app, with margin for the app itself. Weight bytes ≈ parameters × bits per
weight ÷ 8, so a 3B model at 4-bit is about 1.5 GB and an 8B model about 4 GB, before the cache.
**Bandwidth**: each decode step reads all the weights once, so tokens per second is bounded by
memory bandwidth ÷ model bytes; as an illustrative order of magnitude, a device with tens of GB/s
of memory bandwidth and a 2 GB model cannot exceed a few tens of tokens per second, whatever the
compute. **Power and thermal**: sustained throughput is lower than burst throughput because the
chip throttles, so plan for sustained figures measured on warm devices.

### Make the model fit
- **Choose a small model first.** Down to about 4 bits per weight, a larger quantized model often
  beats a smaller one at the same byte budget, so 4-bit is a common sweet spot. Below roughly 3 bits
  quality usually falls off sharply, so a large model that only fits at 2-bit is typically beaten by
  a small language model (hundreds of millions to a few billion parameters) at 4-bit, especially one
  trained or distilled for the task. The crossover is model- and task-dependent; measure it.
- **Quantize**, typically to 4-bit or 8-bit weights, with activations kept at higher precision.
  Post-training quantization methods (GPTQ, AWQ and the k-quant families used by llama.cpp) cut
  memory and raise bandwidth-bound speed roughly in proportion to the bit reduction. The quality
  cost must be measured per model and task; it is small for many models at 8-bit and 4-bit but
  can be severe for the smallest models.
- **Distil** a large model's behaviour into a small one for the specific task, which is how most
  production on-device models are made task-competent.
- **Prune and adapt.** Structured pruning removes capacity the task does not need; LoRA adapters
  let one base model serve several device features with small per-feature deltas.

### Match the runtime to the silicon
Device accelerators are heterogeneous: CPU vector units, mobile GPUs, NPUs on phones and laptops,
Apple's unified-memory GPU and Neural Engine, NVIDIA Jetson-class embedded GPUs. Runtimes exist to
map one model to many of them: llama.cpp with its GGUF format (CPU and GPU back ends across
desktop and mobile), ONNX Runtime (execution providers per accelerator, including mobile), Core ML
(Apple), LiteRT (formerly TensorFlow Lite) and ExecuTorch (mobile and embedded from the TensorFlow
and PyTorch ecosystems), TensorRT (NVIDIA embedded and desktop GPUs), and WebGPU-based runtimes
for in-browser inference. The runtime decision is made *per device class* because the fastest
path on each is different, and the test matrix must cover real devices, not a developer laptop.

### Hybrid by default
A small on-device model handles what it can; requests that need more capability, more context or
more knowledge escalate to the cloud. The escalation rule is explicit (confidence threshold, task
type, user setting) and is itself a privacy decision, since escalation uploads data. On-device
first with cloud fallback is the shape most consumer platforms have converged on, and it preserves
the latency and offline benefits for the common case.

### Lifecycle on a fleet you do not control
Model files are distributed like app assets: downloaded on Wi-Fi, versioned, delta-updated, with
integrity checks. A device may have any of several model versions at once, so telemetry (opt-in,
aggregated) must carry the model version, and the server-side fallback must accept requests from
old clients. Capability detection at install time decides which model variant, if any, a device
gets; low-end devices get cloud-only mode. Rollouts are staged by device class because a model
that fits one phone can crash another.

### Security on device
Anything on the device belongs to the user, including the weights, the prompts and any guardrail
logic. Treat model weights as extractable, keep anything proprietary in the cloud model, and never
rely on on-device prompts or filters for decisions that matter to the server; re-check on the
server side when the request escalates.

## Example
Running a quantized GGUF model locally with llama.cpp through its Python binding, with a budget
check first. The model file name is a placeholder; parameters and quantization are illustrative.

```python
import os, time
from llama_cpp import Llama

def fits(model_path: str, n_ctx: int, kv_bytes_per_token: int, budget_bytes: int) -> bool:
    """Weights on disk approximate weights in memory for GGUF; add the KV cache for n_ctx."""
    weights = os.path.getsize(model_path)
    need = weights + n_ctx * kv_bytes_per_token
    print(f"weights {weights/1e9:.2f} GB + cache {n_ctx*kv_bytes_per_token/1e9:.2f} GB vs budget {budget_bytes/1e9:.1f} GB")
    return need <= budget_bytes

MODEL = "small-chat-3b.Q4_K_M.gguf"          # ~3B params at 4-bit: roughly 1.5-2 GB on disk
N_CTX = 2048
# kv_bytes_per_token = 2 * n_layers * n_kv_heads * head_dim * bytes; read these from the model card
assert fits(MODEL, N_CTX, kv_bytes_per_token=2 * 28 * 8 * 128 * 2, budget_bytes=4 * 1024**3)

llm = Llama(model_path=MODEL, n_ctx=N_CTX, n_threads=os.cpu_count() or 4,
            n_gpu_layers=-1, verbose=False)   # offload all layers to Metal/CUDA/Vulkan if present

t = time.time()                      # timing below includes prompt processing, not just decode
out = llm.create_chat_completion(
    messages=[{"role": "system", "content": "Reply in one sentence."},
              {"role": "user", "content": "Summarise why the pump alarm fired."}],
    max_tokens=64,
)
text = out["choices"][0]["message"]["content"]
n_out = out["usage"]["completion_tokens"]
print(text); print(f"{n_out/(time.time()-t):.1f} tok/s on this device")
# Measure sustained tok/s after several minutes on a warm device, not this first burst.
```

The `fits` check is the on-device version of the capacity question: weights plus cache against
the budget the platform grants. The printed tokens-per-second figure is the number that decides
whether streaming feels responsive, and it will drop as the device heats up.

## Trade-offs
- On-device inference removes network latency, cloud cost and data upload, but caps model quality
  at what fits in the device's memory and bandwidth; the trade flips when the task needs frontier
  capability or broad knowledge.
- Quantization to 4-bit roughly halves memory and raises speed versus 8-bit, but the quality loss
  grows as models get smaller; measure on the task before shipping.
- A hybrid escalation path recovers quality for hard cases, but every escalation is a network
  dependency and a privacy event, so the rule must be visible to the user and the product.
- Per-device runtimes extract the most from each accelerator, but multiply the build, test and
  update matrix; a single portable runtime is slower and simpler.
- Owning the fleet's compute makes marginal cost near zero, but makes rollouts, debugging and
  telemetry slower and partial compared with a server you control.

## Pitfalls
- Benchmarking on a developer laptop; the symptom is a model that is fast in the demo and crawls or
  crashes on the median user's device.
- Measuring burst throughput; the symptom is responsiveness collapsing after a minute of use as the
  device throttles thermally.
- Forgetting the KV cache in the memory budget; the symptom is out-of-memory kills on long
  conversations even though the weights "fit".
- Shipping a quantized model without task evals; the symptom is a quality regression reported from
  the field that the cloud model never showed.
- Relying on on-device guardrails or prompts for policy; the symptom is users bypassing them by
  editing the app or its model files.
- No model-version field in telemetry and requests; the symptom is a bug that reproduces on some
  devices and cannot be attributed to the variant they run.

## When to use
Reach for edge deployment when latency must be below a network round trip, when the device is
often offline, when the data must not leave it, or when per-interaction cloud cost at your install
base is untenable, and when a small or distilled model can meet the quality bar on the task. Do not
reach for it when the task needs frontier reasoning or large context, or when the device matrix is
too fragmented to test. Measure sustained tokens per second on target devices, memory high-water
marks with the KV cache, task-eval scores per quantization level, escalation rate to the cloud, and
crash rates by device class. In interviews, expect to size a model to a device from parameter
count and bit width and to explain why decode speed follows memory bandwidth.

## Role lens
- **Engineer:** Quantizes and evaluates candidate models, integrates the runtime for each target
  platform, implements the memory check and the escalation path, and builds the device test matrix.
- **Architect:** Sets the on-device versus cloud split per feature, the privacy rule for escalation,
  the model distribution and versioning scheme, and which capabilities stay server-side for security.
- **FDE:** Fits a customer's workload to their actual devices (plant controllers, handhelds,
  kiosks), proves sustained performance on site, and designs the offline and degraded modes they need.

## Check yourself
**Q:** A logistics company wants a package-scanning assistant on handheld devices with 6 GB of RAM,
of which the app may use about 3 GB, working in warehouses with poor connectivity. Walk through
how you would choose and validate the model.
**A:** Budget first: 3 GB must hold weights, the KV cache for the longest expected interaction and
the app. A 3B-class model at 4-bit is about 1.5–2 GB of weights (k-quant formats spend a little
more than 4 bits per weight), and a 2k-token cache for a model of that shape is a few hundred MB,
leaving room for the app; an 8B model at 4-bit (about 4 GB) does not fit. Then quality: distil or fine-tune the
small model on the scanning tasks and evaluate it against the cloud model on a task set, at the
exact quantization that will ship. Then speed: measure sustained tokens per second on the real
handheld after thermal steady state, since decode is bound by its memory bandwidth, and check the
high-water memory mark on long sessions. Design offline-first with queued escalation to the cloud
when connectivity returns for cases below a confidence threshold. Stage the rollout by device
class, carry the model version in telemetry, and keep any policy enforcement on the server.

## Quiz
### Q: What primarily bounds decode tokens per second for an LLM on a phone?
- [ ] The number of CPU cores available to the app
- [x] Memory bandwidth divided by the bytes of weights read per step
- [ ] The size of the KV cache in the longest conversation
- [ ] Network latency to the model's update server
> Each generated token requires reading all the weights once; a phone's memory bandwidth is a
> small fraction of a datacenter GPU's, so the model's byte size divided into that bandwidth gives
> the ceiling. Quantization helps precisely because it shrinks the bytes read.

### Q: Why is a purpose-built small model at 4-bit usually better on device than a large model squeezed to 2–3 bits to fit the same memory?
- [ ] Small models do not need a KV cache
- [ ] Large models cannot be quantized below 8-bit
- [x] Quality degrades sharply below about 4 bits per weight, so the extra parameters do not make up for it, while a small model distilled for the task keeps its quality at the same byte budget
- [ ] Small models run on the CPU, which is always faster than the NPU
> At a fixed memory budget the question is where quality per byte is highest. Around 4 bits a larger
> model often wins, but pushing it to 2–3 bits degrades it sharply, whereas a distilled small model
> at 4-bit keeps task performance. The crossover still has to be measured per task.

### Q: Which safeguard belongs on the server even when the model runs entirely on device?
- [ ] The tokenizer, so that vocabulary stays consistent
- [ ] The model's quantization tables
- [x] Policy enforcement for decisions that matter, since on-device prompts, filters and weights can be modified by the user
- [ ] The KV cache, to save device memory
> Everything on the device is under the user's control and can be extracted or altered. Guardrails
> that protect the business or other users must be re-checked server-side when requests escalate
> or produce consequential actions.
