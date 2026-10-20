---
id: engines
module: serving-inference
name: Engines (vLLM, SGLang, TGI, TensorRT-LLM, Ollama, llama.cpp)
kind: tool
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - serving-inference/model-serving
  - serving-inference/continuous-batching
  - serving-inference/pagedattention
  - serving-inference/prefix-caching
  - serving-inference/structured-constrained-generation
  - training-adaptation/quantization
  - system-design/self-host-vs-api
  - infra-capacity-economics/gpu-sizing
sources:
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Zheng et al., 2024 — SGLang: Efficient Execution of Structured Language Model Programs
  - Official docs: TensorRT-LLM
  - Official docs: llama.cpp and Ollama
---

## TL;DR
An inference engine loads weights onto hardware and turns requests into tokens; it owns batching,
KV-cache memory, kernels, quantization and the serving API. The six named here split into two families:
datacenter servers built around continuous batching and paged KV cache for many concurrent users (vLLM,
SGLang, TGI, TensorRT-LLM) and local runtimes built for portability and small footprints (llama.cpp, and
Ollama, which wraps it). Choose by hardware, concurrency and how much operational effort you can afford.

## Why it exists
A plain `model.generate()` loop in a training framework serves one request at a time, allocates KV cache
contiguously at maximum length, and runs generic attention kernels. The result is single-digit GPU
utilisation and throughput tens of times below what the hardware can do, because decode is
memory-bandwidth-bound and only batching amortises the weight reads. Serving well requires a long list of
systems work: continuous batching so new requests join between decode steps, paged KV cache so memory is
not lost to fragmentation, fused attention kernels, quantized weight kernels, prefix caching, speculative
decoding, constrained decoding for structured output, tensor parallelism across GPUs, a streaming HTTP
API, metrics and graceful draining. Each of these is a research-paper-sized problem; engines package them
so an application team does not rebuild them.

The other end of the spectrum has a different problem. Running a 7B model on a laptop or an edge box
needs memory-mapped quantized weights, CPU vector instructions, Apple Metal or a consumer GPU, and a
binary with no Python dependency tree. That is llama.cpp's reason to exist, and Ollama's reason is to
make it a one-line install with a model registry.

The naive alternatives fail predictably. "Write our own server on Transformers" loses on throughput and
memory. "Only use a hosted API" fails when data cannot leave the premises, when per-token prices exceed
owning GPUs at volume, or when you need a fine-tuned or open-weight model the provider does not host.
Engines are the layer that makes self-hosting viable.

## How it works
### Shared machinery
All the datacenter engines implement the same core ideas: a scheduler that batches at token granularity
(continuous or in-flight batching), a block-based KV cache with a page table (PagedAttention or an
equivalent), prefix sharing across requests, quantized weight formats (AWQ, GPTQ, FP8 and relatives), an
OpenAI-compatible HTTP server with streaming, and grammar-constrained decoding. They differ in emphasis,
hardware reach, model format and how much work it takes to run them.

### vLLM
The engine that introduced PagedAttention. Python-first, loads Hugging Face checkpoints directly,
supports the widest range of models and several hardware backends (NVIDIA first, AMD and others
following), tensor and pipeline parallelism, automatic prefix caching, speculative decoding, multi-LoRA
serving and guided decoding. `vllm serve <model>` gives an OpenAI-compatible endpoint; the flags that
matter most are `--max-model-len`, `--gpu-memory-utilization`, `--max-num-seqs` and
`--tensor-parallel-size`. It is the default choice for production serving on NVIDIA hardware because of
breadth and community pace.

### SGLang
From the LMSYS group. Its distinctive piece is RadixAttention: the KV cache is organised as a radix tree
over token sequences, so any request sharing a prefix with any cached sequence reuses it automatically,
with LRU eviction. That makes it strong for multi-turn chat, agents and few-shot workloads where prefixes
repeat heavily. It also ships a frontend language for structured programs (parallel calls, constrained
generation) and fast constrained decoding. API and deployment model are similar to vLLM; teams usually
benchmark both on their own traffic.

### TGI (Text Generation Inference)
Hugging Face's server: a Rust router in front of Python model shards, continuous batching, paged
attention, several quantization backends, token streaming, and an OpenAI-style Messages API. Its draw is
integration with the Hub and Inference Endpoints; if your fleet already lives in the Hugging Face
ecosystem it is the shortest path. Its feature pace on the newest serving tricks has generally trailed
vLLM and SGLang.

### TensorRT-LLM
NVIDIA's engine. Its distinctive workflow compiles models ahead of time into TensorRT engines specialised
for a GPU architecture and a configuration (maximum batch size, sequence length, parallelism), which lets
it fuse kernels aggressively and use FP8 on Hopper-class parts (recent releases also add a PyTorch-based
backend that loads checkpoints directly, narrowing the operational gap, but the compiled path remains the
reason to choose it); it implements in-flight batching and paged KV
cache and is typically served behind Triton Inference Server (NVIDIA's NIM containers build on the same
stack). It has the highest performance ceiling on NVIDIA hardware and the highest operational cost: a
build step per model and GPU type, NVIDIA-only, and rebuilds when limits change.

### llama.cpp
A C/C++ runtime built around the GGUF weight format and its family of k-quant formats (Q4_K_M and
relatives). It runs on CPUs with SIMD, on Apple Silicon through Metal, and on GPUs through CUDA, Vulkan
and others, and can offload a chosen number of layers to the GPU (`-ngl`) while keeping the rest in
system RAM. `llama-server` exposes an OpenAI-compatible API. It is designed for one or a few users on
commodity hardware; it has batching, but not the many-user throughput machinery of the datacenter engines.

### Ollama
A packaging layer over llama.cpp and its ggml library (newer releases run some model families on
Ollama's own ggml-based engine, but the design and limits are the same): a model registry (`ollama pull`), a Modelfile for prompts and
parameters, a REST API on port 11434 with an OpenAI-compatible endpoint, and automatic GPU detection. It
is the standard developer-laptop and prototyping runtime. Defaults are single-user; environment variables
such as `OLLAMA_NUM_PARALLEL` and the per-model context length need changing before it serves a team,
and it inherits llama.cpp's throughput ceiling.

### Side by side
| Engine | Target | Concurrency machinery | Hardware | Weight format |
|---|---|---|---|---|
| vLLM | datacenter | continuous batching, paged KV, prefix cache | NVIDIA, AMD, others | HF checkpoints; AWQ/GPTQ/FP8 |
| SGLang | datacenter | as vLLM, plus RadixAttention | NVIDIA, AMD | HF checkpoints; quantized variants |
| TGI | datacenter, HF ecosystem | continuous batching, paged attention | NVIDIA, AMD, others | HF checkpoints; several quant backends |
| TensorRT-LLM | datacenter, peak performance | in-flight batching, paged KV | NVIDIA only | compiled TensorRT engines |
| llama.cpp | laptop, edge, CPU | small-scale batching | CPU, Apple Silicon, consumer GPUs | GGUF |
| Ollama | developer machine | single-user defaults | as llama.cpp | GGUF via registry |

## Example
The same client code runs against a datacenter engine and a laptop runtime because both speak the OpenAI
chat API; only the base URL and model name change. The vLLM flags set the context ceiling, the share of
GPU memory handed to weights plus KV cache, the batch cap and prefix caching.

```bash
# Datacenter: vLLM serving an 8B instruct model, OpenAI-compatible API on :8000
vllm serve meta-llama/Llama-3.1-8B-Instruct --max-model-len 8192 \
  --gpu-memory-utilization 0.90 --max-num-seqs 128 --enable-prefix-caching
# Laptop: Ollama (llama.cpp underneath), OpenAI-compatible API on :11434
ollama pull llama3.1:8b && ollama serve
```

```python
from openai import OpenAI

ENGINES = {"vllm":   ("http://localhost:8000/v1",  "meta-llama/Llama-3.1-8B-Instruct"),
           "ollama": ("http://localhost:11434/v1", "llama3.1:8b")}

def stream(engine: str, prompt: str):
    base_url, model = ENGINES[engine]
    client = OpenAI(base_url=base_url, api_key="not-needed")   # local engines ignore the key
    resp = client.chat.completions.create(
        model=model, max_tokens=128, stream=True,
        messages=[{"role": "user", "content": prompt}],
    )
    for chunk in resp:
        delta = chunk.choices[0].delta.content
        if delta:
            print(delta, end="", flush=True)
    print()

stream("vllm", "Explain continuous batching in two sentences.")
stream("ollama", "Explain continuous batching in two sentences.")
# Same code, two engines: what differs is throughput under concurrency, not the API.
```

## Trade-offs
- vLLM's breadth (models, hardware, features) comes with a fast-moving codebase; pinning versions and
  re-benchmarking on upgrade is part of owning it, whereas a compiled TensorRT-LLM engine is frozen and
  predictable until you need to change it.
- TensorRT-LLM's peak throughput is bought with an ahead-of-time build per model, GPU type and
  configuration; it pays off for a few high-volume models on a homogeneous NVIDIA fleet and not for a
  long tail of models or mixed hardware.
- SGLang's radix-tree prefix sharing wins when traffic has heavy prefix reuse (agents, multi-turn, shared
  system prompts); on single-shot traffic with unique prompts the advantage over vLLM shrinks to whatever
  the current kernels give.
- llama.cpp and Ollama run anywhere with no Python stack, but their throughput under many concurrent users
  is far below a GPU engine; they are the right tool for one user per box and the wrong tool for a shared
  service.
- TGI's ecosystem fit (Hub, Inference Endpoints) saves integration work, but adopting it means accepting
  Hugging Face's cadence for serving optimisations.

## Pitfalls
- Benchmarking a single request and choosing llama.cpp or Ollama for a multi-user service; the symptom is
  latency that climbs steeply as users are added, because batching is limited to a handful of parallel
  slots rather than the hundreds of sequences a datacenter scheduler juggles.
- Setting `--max-model-len` and `--gpu-memory-utilization` without doing the KV-cache arithmetic; the
  symptom is an OOM at startup or a scheduler that admits only a handful of sequences.
- Mixing weight formats; the symptom is a GGUF file that the datacenter engines load only experimentally
  or not at all, or an AWQ checkpoint llama.cpp cannot read, discovered on deploy day.
- Assuming "OpenAI-compatible" means identical behaviour; the symptom is tool calls and JSON mode that
  parse differently across engines and models, because each implements function calling with its own
  parser and chat template.
- Running Ollama with its default context length; the symptom is long prompts silently truncated, since
  the runtime applies its own context window unless the model or request overrides it.
- Serving a base model with an instruct chat template, or the reverse; the symptom is rambling or
  never-terminating output.
- A TensorRT engine built on one GPU type deployed to another; the symptom is a load failure on the new
  nodes after a hardware refresh.

## When to use
Pick by three questions. Where does it run: developer laptop or edge means llama.cpp or Ollama; a shared
GPU service means one of the datacenter engines. How much concurrency and what traffic shape: unique
single-shot prompts favour vLLM, heavy prefix reuse and structured programs favour SGLang, a few very
high-volume models on a homogeneous NVIDIA fleet justify TensorRT-LLM, a Hugging Face-centric platform
points at TGI. How much operational effort can you spend: compiled engines need a build pipeline, vLLM
and SGLang need version discipline, Ollama needs almost nothing. Measure tokens per second at your target
concurrency, time to first token at p95, and GPU memory headroom; never choose on a single-request
benchmark. In interviews, be ready to explain what continuous batching and paged KV cache do, why
llama.cpp and vLLM are not competitors, and what the TensorRT-LLM build step buys and costs.

## Role lens
- **Engineer:** Runs the engine, tunes context length, memory utilisation and batch caps, enables prefix
  caching and quantization, and benchmarks candidates on recorded production traffic.
- **Architect:** Chooses the engine family per deployment target, decides whether the TensorRT-LLM build
  pipeline is worth its rigidity, and standardises on an OpenAI-compatible contract so engines stay swappable.
- **FDE:** Stands up vLLM or SGLang inside a customer's VPC, or Ollama on an analyst's workstation, and
  translates their "it is slow" into the batching and memory numbers that explain it.

## Check yourself
**Q:** A customer prototyped a support assistant on Ollama on a workstation and wants to roll it out to
300 agents on two NVIDIA GPUs. What do you change, and what do you keep?

**A:** Keep the model family, the prompts and the OpenAI-compatible client code, since those transfer.
Change the runtime: Ollama's llama.cpp core serves one or a few users well but lacks the large-batch
scheduler and paged KV-cache machinery that 300 concurrent users need, so latency would grow with every
added user.
Move to vLLM or SGLang on the GPUs; SGLang if the support prompts share a large system prefix, because
RadixAttention reuses it automatically, otherwise vLLM for breadth. Switch from the GGUF file to the
Hugging Face checkpoint, quantized with AWQ or FP8 if memory demands it, and do the KV-cache arithmetic
to set the context ceiling and memory utilisation so the scheduler can hold enough sequences. Put the two
GPUs behind one server with tensor parallelism or run two replicas behind a load balancer, depending on
model size. Benchmark on recorded traffic at target concurrency, verify tool calling still parses, and
add backpressure and metrics in front. TensorRT-LLM is a later optimisation if the model and fleet stay
fixed.

## Quiz
### Q: Which engine's distinctive workflow compiles the model ahead of time for a specific GPU architecture and configuration?
- [ ] vLLM
- [ ] SGLang
- [x] TensorRT-LLM
- [ ] TGI
> TensorRT-LLM builds a TensorRT engine specialised to the GPU type, batch and sequence limits, which
> enables aggressive kernel fusion but means a rebuild when hardware or limits change. The others load
> checkpoints at startup and never need a build step.

### Q: What is the relationship between Ollama and llama.cpp?
- [ ] They are independent implementations of the same GGUF specification
- [x] Ollama packages llama.cpp with a model registry, a REST API and defaults for local use
- [ ] llama.cpp is a GPU-only fork of Ollama
- [ ] Ollama is the datacenter version of llama.cpp with continuous batching
> Ollama's inference core is llama.cpp; it adds pulling models, a Modelfile, an HTTP API and device
> detection. It inherits llama.cpp's strengths on commodity hardware and its limits under many concurrent
> users.

### Q: Which workload benefits most from SGLang's RadixAttention relative to a standard paged KV cache?
- [ ] Single-shot requests with unique prompts
- [x] Multi-turn chat and agent loops where many requests share long prefixes
- [ ] Offline batch embedding of documents
- [ ] CPU-only inference on edge devices
> RadixAttention stores the KV cache as a radix tree over token sequences, so any request whose prefix
> matches a cached one reuses it without recomputation. The win scales with how much prefix is shared,
> which is largest in multi-turn and agent traffic.
