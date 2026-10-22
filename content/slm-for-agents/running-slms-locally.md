---
id: running-slms-locally
module: slm-for-agents
name: Running SLMs locally (Ollama, llama.cpp, vLLM)
kind: tool
difficulty: core
est_minutes: 16
status: agentic-verified
agent_relevance: core
related:
  - serving-inference/engines
  - slm-for-agents/quantized-slms
  - slm-for-agents/tool-calling-and-structured-output-with-slms
  - slm-for-agents/on-device-and-edge-agents-with-slms
  - python-for-ai/litellm
  - serving-inference/kv-cache-memory-estimation
  - observability/langfuse
sources:
  - Ollama documentation
  - llama.cpp documentation (GitHub repository and llama-server README)
  - vLLM documentation
---

## TL;DR
Ollama, llama.cpp and vLLM all run open small models behind an OpenAI-compatible API, so one agent client
works with each. Pick Ollama for developer laptops, llama.cpp for CPU, edge and fine control, vLLM for
GPU servers with many concurrent agents.

## For agent builders
- **Why it matters for agents.** A local endpoint lets you develop agents offline, keep private data in your
  boundary, and serve high-volume SLM steps without per-token bills.
- **Where it shows up in your stack.** A `base_url` such as `http://localhost:11434/v1` (Ollama),
  `http://localhost:8080/v1` (llama.cpp server) or `http://localhost:8000/v1` (vLLM) in your OpenAI SDK,
  LiteLLM or framework config.
- **What you control.** The model and quantization, context length, how many requests run in parallel, and
  whether native tool-call parsing is turned on.
- **What you can skip.** Engine internals such as kernels and schedulers; `serving-inference/engines` covers
  them. Here you need the commands, the flags that matter to agents, and when to switch engines.

## Why it exists
- **Hosted APIs are not always allowed or affordable.** Regulated data, air-gapped sites and high call volume
  push SLM steps onto hardware you run.
- **Raw model weights are not a service.** You need tokenisation, chat templates, batching, a KV cache and an
  HTTP API; these three engines package all of that.
- **Agents need tool calls parsed, not just text.** Each engine can turn the model's tool-call tokens into
  structured `tool_calls` in the response, if you enable the right template or parser.
- **One size does not fit all.** A laptop, a CPU-only edge box and an 80 GB GPU serving 50 agents need
  different engines, but the same OpenAI-style API keeps the agent code identical.

## How it works
### The three engines at a glance
| | Ollama | llama.cpp (`llama-server`) | vLLM |
|---|---|---|---|
| **Built for** | Easy local use, model management | Portable C/C++ inference, CPU and GPU, edge | High-throughput GPU serving |
| **Model format** | GGUF, pulled from its library or imported | GGUF | Hugging Face weights, plus AWQ, GPTQ, FP8 and others |
| **Core abstraction** | Model tag plus Modelfile (`FROM`, `PARAMETER`, `SYSTEM`) | A GGUF file plus command-line flags | A model repo id plus engine arguments |
| **Concurrency** | A few parallel requests per model | Fixed number of slots | Continuous batching, PagedAttention, prefix caching |
| **Default port** | 11434 | 8080 | 8000 |
| **Tool calls** | `tools` in its native and OpenAI-compatible APIs for models whose template supports them | Pass `--jinja` to use the model's chat template (explicitly, whatever your build's default) | `--enable-auto-tool-choice` plus a `--tool-call-parser` for the model family |

### Starting each one
```bash
# Ollama: the server (desktop app or `ollama serve`) listens on :11434; pull a model into it
ollama pull qwen2.5:7b
ollama run qwen2.5:7b "Say hi"          # quick test; ollama ps shows loaded models

# llama.cpp: serve a GGUF file on :8080, offload all layers to GPU, 8k context
llama-server -m ./qwen2.5-7b-instruct-q4_k_m.gguf -c 8192 -ngl 99 --port 8080 --jinja

# vLLM: serve Hugging Face weights on :8000 with native tool-call parsing
vllm serve Qwen/Qwen2.5-7B-Instruct --max-model-len 8192 \
  --enable-auto-tool-choice --tool-call-parser hermes
```

### Settings that matter to agents
- **Context length.** Ollama's default context is small and can silently truncate long tool schemas and
  transcripts; set `num_ctx` (Modelfile `PARAMETER num_ctx 8192` or request `options` on the native API).
- **Ollama's OpenAI-compatible endpoint has no per-request context setting,** so agents that use the OpenAI
  SDK need the Modelfile or the server-wide `OLLAMA_CONTEXT_LENGTH`. In llama.cpp use `-c`, in vLLM
  `--max-model-len`.
- **Keep-alive.** Ollama unloads idle models after a few minutes; `OLLAMA_KEEP_ALIVE` or the request's
  `keep_alive` avoids a cold load on the next agent step.
- **Parallelism.** `OLLAMA_NUM_PARALLEL`, llama.cpp `--parallel` slots, and vLLM `--max-num-seqs` cap
  concurrent requests; each one needs its own KV cache.
- **Tool parser match.** The vLLM parser must match the model's tool format (for example `hermes`,
  `llama3_json`, `mistral`); check the vLLM tool-calling docs for the current list.

## Diagram
The agent code stays the same; only the base URL changes, and each engine maps a model file or repo to an
OpenAI-style chat endpoint that returns parsed tool calls.

```mermaid
flowchart TD
    A["Agent loop with OpenAI SDK"] -->|"base_url"| R{"Which engine?"}
    R -->|"laptop dev"| O["Ollama :11434"]
    R -->|"CPU or edge"| L["llama-server :8080"]
    R -->|"GPU, many agents"| V["vLLM :8000"]
    O --> G[("GGUF model")]
    L --> G
    V --> H[("HF weights or AWQ")]
    O --> T["Response with tool_calls"]
    L --> T
    V --> T
    T -->|"run tool, append result"| A
```

## Examples
### A minimal tool-calling agent against any local engine
The same loop works with Ollama, llama.cpp (`--jinja`) or vLLM (tool parser on); change `BASE_URL` and
`MODEL` only.

```python
import json
from openai import OpenAI

BASE_URL, MODEL = "http://localhost:11434/v1", "qwen2.5:7b"   # or :8080/v1, :8000/v1
client = OpenAI(base_url=BASE_URL, api_key="local")           # local servers ignore the key

def get_stock(sku: str) -> dict:
    return {"sku": sku, "in_stock": 12}                         # stand-in for a real API

TOOLS = [{"type": "function", "function": {
    "name": "get_stock", "description": "Units in stock for a product SKU",
    "parameters": {"type": "object", "properties": {"sku": {"type": "string"}},
                   "required": ["sku"]}}}]

messages = [{"role": "user", "content": "How many units of SKU A-17 are in stock?"}]
for step in range(5):                                           # hard cap on agent steps
    msg = client.chat.completions.create(model=MODEL, messages=messages,
                                         tools=TOOLS, temperature=0).choices[0].message
    if not msg.tool_calls:
        print(msg.content); break
    messages.append(msg)
    for call in msg.tool_calls:
        try:
            result = get_stock(**json.loads(call.function.arguments))
        except (json.JSONDecodeError, TypeError) as e:          # small models do emit bad args
            result = {"error": f"invalid arguments: {e}"}
        messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
```

### Production setup: which engine where
A team runs the same agent in three places. The comparison is qualitative; measure throughput on your own
hardware and model.

| Setting | Engine | Configuration that mattered |
|---|---|---|
| Developer laptops (16-32 GB unified memory) | Ollama | Pinned model tags, Modelfile with `num_ctx 8192`, one shared Modelfile in the repo |
| Branch-office edge box, CPU only | llama.cpp `llama-server` | Q4_K_M GGUF, `-c 4096`, `--parallel 2`, started as a system service |
| Central GPU server, 40 concurrent agents | vLLM | AWQ 4-bit 7B model, `--max-model-len 8192`, `--enable-prefix-caching`, tool parser matched to the model |

All three expose `/v1/chat/completions`, so the agent and its eval suite run unchanged in each place.

## Production patterns
- **One client, many backends.** Route through LiteLLM or a thin gateway with entries for each local engine
  and a hosted fallback. Monitor per-backend error rate and latency so a failing local server shifts traffic
  instead of failing agent runs.
- **Containerise with the model pinned.** Run the official Ollama or vLLM container images, or a llama.cpp
  build, with the model file or revision baked in or mounted read-only. Pin versions, because engine upgrades
  can change templates, parsers and defaults.
- **Health check with a real completion.** Probe with a short chat request, not just an open port, because a
  server can be up while the model is still loading. Alert on time to first token and on queue length.
- **Size concurrency from memory.** Set `--max-num-seqs` or `--parallel` from KV-cache arithmetic for your
  context length, then load-test at the concurrency your agents create. Watch preemptions in vLLM metrics.
- **Trace local calls like hosted ones.** Send spans with model, engine, quantization, tokens and latency to
  Langfuse or your tracing backend, so SLM steps are visible in the same agent trace.

## Trade-offs
- **Ollama convenience vs control.** It is the fastest path to a working local agent, but exposes fewer
  serving knobs and lower concurrency than vLLM. It flips once you serve many concurrent users.
- **llama.cpp portability vs throughput.** It runs on CPU, Apple silicon and modest GPUs from one GGUF file,
  but does not batch as aggressively as vLLM on a large GPU.
- **vLLM throughput vs footprint.** Continuous batching and prefix caching give the most agents per GPU, but
  it targets GPU servers, pre-allocates most GPU memory, and has heavier dependencies.
- **Local vs hosted.** Local keeps data in and per-token cost out, but you own uptime, upgrades and capacity.

## Pitfalls
- **Truncated context in Ollama.** Symptom: the agent ignores tools or the system prompt on longer runs.
  Fix: raise `num_ctx` (Modelfile or `OLLAMA_CONTEXT_LENGTH` when you call the OpenAI-compatible
  endpoint) and confirm prompt token counts in the response usage.
- **Tool calls returned as plain text.** Symptom: JSON-looking text in `content` and no `tool_calls`. Fix:
  start llama.cpp with `--jinja`, enable vLLM's auto tool choice with the right parser, or pick a model whose
  Ollama template supports tools.
- **Cold starts between steps.** Symptom: some agent steps take seconds longer. Fix: set keep-alive or keep
  the model resident, and avoid loading several large models on one small GPU.
- **Benchmarking at batch 1.** Symptom: fine in testing, slow under load. Fix: load-test with the real number
  of concurrent agents and long prompts.

## When to use
- **Use Ollama when** you want a local model for development, demos or a single user in minutes.
- **Use llama.cpp when** you need CPU or edge deployment, a single binary, or fine control over GGUF and
  offloading.
- **Use vLLM when** a GPU server must serve many concurrent agent sessions with high throughput.
- **Measure** time to first token, tokens per second, valid tool-call rate and memory at your real
  concurrency.

## Role lens
- **Engineer:** Starts the right engine with the right flags, points the agent's client at its base URL,
  and fixes context truncation and tool-parsing issues.
- **Architect:** Chooses engines per environment, sizes GPU servers from KV-cache math and concurrency, and
  keeps all of them behind one OpenAI-compatible interface with a hosted fallback.
- **FDE:** Installs a local model on a customer's machine or server, proves the agent works offline on their
  data, and explains when they will need to move from Ollama to vLLM.

## Check yourself
**Q:** Your agent works with a hosted model but, pointed at a local 7B model in Ollama, it ignores its tools
after a few steps and sometimes prints JSON as text. What do you check?
**A:** First the context: Ollama's default context is small, so a long system prompt, tool schemas and a
growing transcript get truncated; raise `num_ctx` and confirm prompt token counts in usage. Second, tool
support: the model's template must support tools, otherwise tool calls come back as text in `content`; pick
a model whose template does, or move to llama.cpp with `--jinja` or vLLM with the matching tool parser.
Third, robustness: validate arguments, return errors as tool results, cap steps, and run the agent's eval on
the local model before trusting it.

## Interview questions
### When would you choose vLLM over Ollama for serving an agent's small model?
- **Concurrency:** vLLM's continuous batching and PagedAttention serve many simultaneous agent sessions per
  GPU; Ollama targets a few local users.
- **Serving features:** prefix caching for shared system prompts and tool schemas, metrics, and fine control
  over sequence and memory limits.
- **Cost:** it needs a GPU server and more operational work, so it pays off only at real traffic.

### Your local model returns tool calls as raw text. How do you debug it?
- **Check the template:** the chat template must define tool calls; with llama.cpp start `llama-server` with
  `--jinja`, with vLLM enable auto tool choice with the right parser.
- **Check the model:** use an instruct model whose card documents tool support, at the deployed quantization.
- **Add a fallback parser** only as a stopgap, and add the case to your eval set.

### How do you keep one agent codebase working across laptop, edge and GPU-server deployments?
- **Same API everywhere:** all three engines expose OpenAI-compatible chat completions, so only `base_url` and
  model name change.
- **Config, not code:** keep engine, model, context and fallback in configuration or a gateway such as
  LiteLLM.
- **Eval per environment:** run the same golden set on each deployed build, since quantization can differ.

### How would you size a vLLM server for 40 concurrent agent sessions with 8k-token contexts?
- **Weights first:** model size at its precision, for example a 7B at 4-bit.
- **KV cache next:** bytes per token from the config times 8k times 40, plus about 10% headroom; set
  `--max-model-len` and `--max-num-seqs` to match.
- **Load-test:** measure time to first token and preemptions at 40 sessions before launch.

### What is the risk of upgrading the engine version under a running agent?
- **Behaviour changes:** templates, tool parsers, defaults such as context or sampling, and quantization
  kernels can change outputs.
- **Mitigation:** pin engine and model versions, run the agent's golden set on the new build, and roll out
  gradually with traces tagged by engine version.

## Quiz
### Q: What lets one agent client talk to Ollama, llama.cpp and vLLM without code changes?
- [ ] They all use the same model file format
- [x] Each exposes an OpenAI-compatible chat completions endpoint, so only the base URL and model name change
- [ ] They all run on the same default port
- [ ] They share one model registry
> Ollama, llama-server and vLLM all serve `/v1/chat/completions`. Formats and ports differ: Ollama and
> llama.cpp use GGUF, vLLM uses Hugging Face weights, and the defaults are 11434, 8080 and 8000.

### Q: An agent on a local Ollama model starts ignoring its tools once the transcript grows. What is the most likely cause?
- [ ] The model has been unloaded and reloaded
- [x] The context window is set too small, so the tool schemas or system prompt are truncated
- [ ] Ollama does not support tool calls at all
- [ ] Temperature was set to 0
> Ollama's default context is small. When the prompt exceeds it, earlier content is cut, including tool
> definitions. Raising `num_ctx` and checking prompt token counts fixes it.

### Q: Which setting is needed for vLLM to return parsed tool calls from a model?
- [ ] `--enable-prefix-caching`
- [ ] `--max-model-len`
- [x] `--enable-auto-tool-choice` with a `--tool-call-parser` that matches the model's tool format
- [ ] `--gpu-memory-utilization`
> vLLM parses tool calls only when auto tool choice is on and a parser matching the model family is chosen.
> Prefix caching, context length and memory utilisation are performance and capacity settings.

## Use cases & projects
### Use cases
- **Offline development.** Engineers build and debug agents on laptops against Ollama, with no API keys or
  per-token cost.
- **Air-gapped and on-premises agents.** llama.cpp or vLLM serve the agent's model inside the customer's
  network.
- **High-volume SLM steps.** A vLLM server handles classification and extraction calls for many agents,
  while a hosted large model handles planning.
- **Edge sites.** A CPU-only `llama-server` runs a small model in a store, factory or vehicle.

### Mini project
**Run one agent on three engines (2-3 hours).**
- **Goal:** prove your tool-calling agent runs unchanged on Ollama, llama.cpp and vLLM (or two of them if you
  have no GPU).
- **Steps:** pick one small instruct model with tool support; serve it with Ollama, then as a GGUF with
  `llama-server --jinja`, then with vLLM and its tool parser if a GPU is available; run the example agent and
  a 30-case tool-calling eval against each; record valid tool-call rate, time to first token and tokens per
  second.
- **Done when:** a table shows the three engines side by side, the agent code differs only in `BASE_URL` and
  `MODEL`, and you can explain which engine you would deploy where.
