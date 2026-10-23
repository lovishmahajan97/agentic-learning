---
id: choosing-an-slm
module: slm-for-agents
name: Choosing an SLM (Llama, Qwen, Phi, Gemma, Mistral small models)
kind: decision
difficulty: core
est_minutes: 15
status: typed
lesson_type: decision
agent_relevance: core
mastery: evaluate
code_tested: partial
prerequisites:
  - slm-for-agents/when-an-slm-is-enough
  - serving-inference/kv-cache-memory-estimation
related:
  - model-internals/small-language-models
  - system-design/model-selection
  - slm-for-agents/when-an-slm-is-enough
  - slm-for-agents/quantized-slms
  - slm-for-agents/tool-calling-and-structured-output-with-slms
  - slm-for-agents/evaluating-slms-on-your-agent-tasks
  - evals/golden-dataset
  - security-governance-compliance/vendor-exit-strategy
sources:
  - Hugging Face model cards (Meta Llama, Qwen, Microsoft Phi, Google Gemma, Mistral AI)
  - Ollama documentation (model library)
  - vLLM documentation (supported models)
---

## TL;DR
Choosing an SLM is a checklist, not a leaderboard: filter by license, hardware fit, context, tool-calling
template and engine support, then shortlist two or three models from different families and let your own
agent eval pick the winner.

## For agent builders
- **Why it matters for agents.** The model decides whether tool calls parse, whether JSON validates and
  whether the agent fits your GPU or laptop. Two models with similar benchmark averages can differ sharply on
  your tool-calling step.
- **Where it shows up in your stack.** The model tag in Ollama, the GGUF file for llama.cpp, the Hugging Face
  repo id in `vllm serve`, and the chat template that formats tools for that model.
- **What you control.** The shortlist, the size and quantization that fit your hardware, the license review,
  and the eval that decides.
- **What you can skip.** Blog benchmark numbers: families ship new versions every few months, so learn the
  criteria and re-run them on current cards.

## Decision case
### Scenario
A clinic network deploys an appointment-scheduling agent on premises in 40 clinics. Patient data may not
leave the clinic, so every model call runs locally. All model figures below are illustrative, not
published scores.
- **Hardware:** one 16 GB GPU per clinic; the desktop and an OCR service leave 14 GB usable.
- **Workload:** English and French, 12,288-token prompts (9 tool schemas plus history), 2 concurrent
  sessions, so 24,576 tokens of KV cache.
- **Quality bar:** at least 90% correct tool and arguments on a 150-case French golden set; failures go
  to a human scheduler.
- **License:** Apache 2.0 or MIT are pre-approved; a custom license needs legal review of about 4 weeks.
- **Deadline:** pilot in 6 weeks. **Team:** three engineers, Ollama already in use.

Memory per candidate at 4.5 bits per weight, fp16 KV cache, plus 10% headroom. KV bytes per token are
2 (K and V) x layers x KV heads x head dim x 2 bytes, read from each `config.json`; all four use 8 KV
heads of dim 128.

### Options
| Candidate | Weights | KV for 24,576 tokens | Total + 10% | License | French tool step |
|---|---|---|---|---|---|
| A: 14B, 48 layers, Apache 2.0 | 7.9 GB | 196,608 B x 24,576 = 4.8 GB | 14.0 GB, no margin under 14 GB | pass | 95% |
| B: 8B, 32 layers, custom license | 4.5 GB | 131,072 B x 24,576 = 3.2 GB | 8.5 GB | 4-week review | 94% |
| C: 8B, 32 layers, Apache 2.0 | 4.5 GB | 3.2 GB | 8.5 GB | pass | 92% |
| D: 3B, 28 layers, MIT | 1.7 GB | 114,688 B x 24,576 = 2.8 GB | 5.0 GB | pass | 84% |

### Decision
**Candidate C, at the 4-bit build that was evaluated.** It is the smallest model that clears every hard
filter and the 90% bar. D is smaller but fails the bar by 6 points; A fits only with zero margin; B scores
2 points higher but its review would consume 4 of the 6 weeks.

C also leaves about 5.5 GB spare, room for a third session or longer history without a new GPU.

**What would change it.**
- **Legal approves B and the gap grows:** if B leads by more than the 3-point tolerance on the final set,
  re-open the choice.
- **Concurrency drops to 1:** A then needs 7.9 + 2.4 = 10.3 GB, 11.3 GB with headroom, and fits.
- **Bigger GPUs or an 8-bit KV cache:** either brings A back into range.
- **C's French drops below 90%** after a template or engine upgrade: fall back to B or A with a size plan.

### Trade-offs accepted
- **Three points below the best score.** About 3 extra cases in 100 escalate to a human versus A. Watch
  the escalation rate per clinic each week.
- **Dependence on one family's release cadence.** Keep B's scores in the registry as the tested
  fallback, so a swap is a config change.
- **Estimate, not measurement, for memory.** Confirm with `ollama ps` at 12k context and 2 parallel
  sessions on a pilot clinic before rollout.

### Review rubric
- **Hard filters first:** license, memory with the real context and concurrency, and tool template are
  checked before any quality score is compared.
- **Memory math shown:** weights and KV cache are computed from each config, with headroom, and match the
  stated GPU budget.
- **Evidence from the deployed build:** scores come from the quantization and engine that will ship, on
  the team's own golden set.
- **Smallest passing model chosen,** with the reason each runner-up lost.
- **Re-open triggers named:** the write-up states which constraint change would flip the choice.

## Why it exists
- **There are hundreds of candidates.** Each family ships several sizes, base and instruct variants, and
  many community quantizations. Without a filter, teams pick whatever trended last week.
- **Hard constraints eliminate most options.** A license that forbids your use, a model that does not fit
  in 8 GB, or a chat template without tool support removes a model regardless of its quality.
- **Benchmarks measure someone else's task.** General averages say little about selecting one of your 6
  tools with the right arguments, or extracting your schema from your documents.

## How it works
### Step 1: hard filters (pass or fail)
| Filter | What to check | Why it eliminates |
|---|---|---|
| **License** | Model card license: Apache 2.0, MIT, or a custom community or terms-of-use license | Some custom licenses add usage policies, attribution or scale conditions your legal team must accept |
| **Hardware fit** | Parameters × bits per weight ÷ 8, plus KV cache for your context, under your VRAM or RAM | A model that does not fit runs on CPU or swaps, and latency collapses |
| **Context length** | Supported context versus your prompt (system prompt, tool schemas, history) | Tool schemas and transcripts eat context fast in agents |
| **Tool and chat template** | Whether the chat template defines tool calls and roles you need | Without a tool template, the engine cannot parse tool calls natively |
| **Engine support** | Listed in Ollama's library, convertible to GGUF, supported by vLLM | No runtime means no deployment |
| **Languages and modality** | Languages and images you need | Small models often degrade fastest outside their main languages |

### Step 2: the families, described by traits (verify on current model cards)
| Family | Typical small sizes | Traits worth knowing | License to check |
|---|---|---|---|
| **Llama** (Meta) | 1B, 3B, 8B | Broad ecosystem support, many fine-tunes and quantizations | Custom community license with usage policy |
| **Qwen** (Alibaba) | from under 1B to 14B and up | Wide size ladder, strong multilingual and coding variants | Mostly Apache 2.0, but not all sizes (Qwen2.5-3B shipped under a research license); check each |
| **Phi** (Microsoft) | around 4B mini, 14B | Strong reasoning for the size from synthetic data; narrower world knowledge | MIT for recent releases; check each |
| **Gemma** (Google) | 1B to 27B | Good quality per size, some sizes accept images | Gemma terms of use |
| **Mistral** (Mistral AI) | 7B, 8B, small around 24B | Efficient, function-calling support in recent instruct models | Apache 2.0 for some, other terms for others |

### Step 3: shortlist and test
1. **Pick 2-3 candidates from different families** at the size your hardware allows, instruct variants only.
2. **Run the same golden set** for each agent step you plan to move, with the same prompt, engine and
   quantization you will deploy.
3. **Score what agents need:** task pass rate, valid-JSON rate, correct-tool rate, p50 and p95 latency, and
   peak memory.
4. **Pick the smallest model that meets the bar,** because the smallest passing model leaves room for
   context, concurrency and a cheaper GPU.

## Diagram
Hard filters remove models before any testing; the eval picks among what is left, and the smallest passing
model wins.

```mermaid
flowchart TD
    C["Candidate models from current cards"] --> L{"License fits your use?"}
    L -->|"no"| X["Drop"]
    L -->|"yes"| H{"Fits VRAM with your context?"}
    H -->|"no"| X
    H -->|"yes"| T{"Tool template and engine support?"}
    T -->|"no"| X
    T -->|"yes"| S["Shortlist 2-3 from different families"]
    S --> E["Run your agent golden set"]
    E --> P["Smallest model that passes the bar"]
```

## Examples
### Screening a shortlist on your own tool-calling step
Pull the candidates with `ollama pull`, then score each on correct tool, valid arguments and latency through
Ollama's Python client. The model tags are examples; use the current tags from the Ollama library.

```python
# Run with the ollama Python client 0.6.3 against a mock server (attribute access needs 0.4+).
import json, time
import ollama

CANDIDATES = ["llama3.2:3b", "qwen2.5:3b", "phi4-mini", "gemma3:4b"]   # check current tags
TOOLS = [{"type": "function", "function": {
    "name": "lookup_order", "description": "Find an order by its ID",
    "parameters": {"type": "object", "required": ["order_id"],
                   "properties": {"order_id": {"type": "string"}}}}}]
cases = [json.loads(l) for l in open("tool_golden.jsonl")]   # {"text": ..., "order_id": ...}

for model in CANDIDATES:
    ok, lat = 0, []
    for c in cases:
        t = time.time()
        try:
            r = ollama.chat(model=model, messages=[{"role": "user", "content": c["text"]}],
                            tools=TOOLS, options={"temperature": 0})
        except ollama.ResponseError as e:          # e.g. "... does not support tools"
            print(f"{model:14} hard-filter fail: {e.error}")
            break
        lat.append(time.time() - t)
        calls = r.message.tool_calls or []
        ok += bool(calls) and calls[0].function.name == "lookup_order" \
              and calls[0].function.arguments.get("order_id") == c["order_id"]
    else:                                          # runs only if no hard-filter break
        lat.sort()
        print(f"{model:14} pass {ok/len(cases):.0%}  p50 {lat[len(lat)//2]:.2f}s")
```

A model whose template lacks tool support either makes Ollama return an error (caught above) or answers in
plain text with no tool call; both are hard-filter failures, not low scores.

### Worked scenario: picking a model for an on-premises document agent
Constraints: one 24 GB GPU, English and German, 16k-token prompts, commercial use, tool calls to 4 tools.

| Check | Result |
|---|---|
| License review | Two candidates cleared quickly; one custom license went to legal and was parked |
| Hardware | 14B at 4-bit fits weights but leaves little KV cache for 16k context at concurrency 4; 7B-8B fits comfortably |
| Shortlist | One 7B-8B model from each of two families, plus a 3B as a cheap baseline |
| Eval on 150 real documents | Both 7B-8B models within 2 points on extraction; one had clearly better German tool-argument filling |
| Decision | The better German model, at a 4-bit or 5-bit quantization that kept the eval within tolerance |

The 3B baseline failed German extraction, which no general benchmark had flagged.

## Production patterns
- **Keep a model registry entry per choice.** Record repo id, exact revision or file hash, quantization,
  license and legal sign-off, chat template, eval scores with run date, and the fallback model, so an
  upgrade can be re-tested against the same evidence.
- **Abstract the endpoint.** Serve every candidate behind an OpenAI-compatible API (Ollama, llama.cpp
  server, vLLM) and call it through one client or LiteLLM. With a golden set, a swap is then a config change
  and an afternoon; hard-coding one model's prompt quirks makes it costly.
- **Re-screen on a schedule.** Re-run the shortlist each quarter or when a family ships a new generation, since
  a newer small model often matches an older larger one and frees hardware.
- **Pin the chat template.** Use the template from the model's own repo; community GGUF files sometimes ship
  an older or broken template, which shows up as unparsed tool calls.
- **Log model identity in traces.** Attach model name, revision and quantization to every span so a quality
  regression can be traced to a model change.

## Trade-offs
- **Bigger model vs headroom.** A 14B model may pass more cases, but on fixed hardware leaves less memory
  for context and concurrent agents.
- **Permissive license vs best quality.** An Apache 2.0 or MIT model is easy to ship; a custom license may
  have the best scores but adds legal review and conditions. This flips when legal has already approved one.
- **One family vs several.** Standardising on one family simplifies templates and tooling, but ties you to
  its release cadence and gaps, such as weak support for one of your languages.
- **Specialist variant vs general instruct.** Coder or math variants win on their domain but can be weaker at
  general instruction following and tool use.

## Pitfalls
- **Choosing from a leaderboard.** Symptom: a top-ranked model fails your tool calls. Diagnose: split its
  golden-set failures into no call, wrong tool and wrong arguments. Fix: shortlist with hard filters, then
  decide on your own golden set.
- **Testing a different build than you deploy.** Symptom: production quality is lower than the eval.
  Diagnose: diff tag or file hash, quantization, engine version and template between eval and production
  traces. Fix: evaluate the exact build you will ship.
- **Using a base model.** Symptom: rambling text and no tool calls. Diagnose: the output continues your
  prompt instead of answering, and the card or tag lacks "instruct". Fix: use the instruct or chat variant.
- **Skipping the license.** Symptom: a late legal block before launch. Diagnose: registry entries with no
  license sign-off. Fix: put license review first in the checklist and record the outcome.

## When to use
- **Use when** you have decided a step can run small and need a specific model for it, or when a family
  ships a new generation and you want to know if it should replace yours.
- **Avoid when** the step has not been shown to suit an SLM at all; settle that first.
- **Measure** pass rate, valid-JSON and correct-tool rates, p95 latency and peak memory, per candidate, on
  the deployed build.

## Exercise
**Goal:** turn the hard filters into a screener that rejects models with a stated reason before any quality
test. No solution is given.
- **Inputs:** 5-8 candidate models from current cards, each card's `config.json`, and your constraints:
  memory budget, context, concurrency, languages, allowed licenses.
- **Deliver:** a script computing weights plus KV cache plus 10% headroom per candidate and quantization,
  checking license and tool template, and printing pass or fail with the reason; then screen the survivors
  with the harness above.
- **Acceptance:** every rejected model names the filter it failed; for one model you can run, your memory
  estimate lands within about 15% of what `ollama ps` reports at your context and parallel setting
  (vLLM pre-allocates, so `nvidia-smi` misleads there).

## Role lens
- **Engineer:** Runs the screening harness across candidates, records scores per agent step, and pins the
  chosen model's revision, quantization and template in the registry.
- **Architect:** Sets the hard filters (license classes, hardware envelope, languages) and the acceptance bar,
  and keeps the endpoint abstraction that makes models swappable.
- **FDE:** Runs the shortlist on the customer's own documents and hardware and explains why the smallest
  passing model is the right one for their constraints.

## Check yourself
**Q:** A team must pick a small model for a tool-calling agent on a 16 GB GPU, for commercial use in English
and Spanish, with 8k-token prompts. How should they choose?
**A:** Start with hard filters: license that allows their commercial use, a size and quantization whose
weights plus KV cache for 8k tokens at their concurrency fit in 16 GB (typically 3B-8B at 4-5 bits), a chat
template with tool support, and engine support in their runtime. Then shortlist two or three instruct models
from different families, for example Llama, Qwen, Phi, Gemma or Mistral, checked on current model cards. Run
the same golden set of real tool-calling and Spanish cases on the exact build they will deploy, scoring
correct tool, valid arguments and latency. Pick the smallest model that meets the bar and record its revision
and template.

## Interview questions
### Why not just pick the small model with the highest benchmark score?
- **Benchmarks measure other tasks.** Your step is a specific tool list, schema and language mix that general
  averages do not cover.
- **Hard constraints come first.** License, memory with your context, and tool-template support eliminate
  models regardless of score.
- **The deployed build differs.** Quantization and engine change behaviour, so you must test the exact build.

### What goes into your hardware-fit estimate for a candidate SLM?
- **Weights:** parameters times effective bits per weight divided by 8, for the quantization you will use.
- **KV cache:** bytes per token from the model config times context length times concurrent sequences.
- **Headroom:** around 10% for activations and fragmentation; if it does not fit, step down in size or
  precision and re-run the eval.

### A new version of your model family was released. How do you decide whether to upgrade?
- **Re-run the golden set** for every agent step that uses the model, on the deployed quantization and engine.
- **Check format changes:** tool-call format and chat template can change between generations and break
  parsing.
- **Upgrade only if it wins,** or matches at a smaller size, and roll out behind a flag with traces tagged by
  model revision.

### How would you choose between a 3B and an 8B model for an agent's argument-filling step?
- **Measure both** on one golden set with validation: pass rate, invalid-JSON rate, latency.
- **Price the difference:** the 8B needs more memory per replica and serves fewer concurrent agents.
- **Decide by the bar:** the 3B if it passes with escalation on failure, otherwise the 8B.

## Quiz
### Q: Which check should eliminate a candidate SLM before any quality testing?
- [ ] It ranks below another model on a public leaderboard
- [x] Its weights plus KV cache for your context do not fit in the target GPU memory
- [ ] It comes from a different family than your current model
- [ ] It has fewer parameters than your current model
> Hardware fit, license and tool-template support are hard filters: a model that does not fit runs slowly
> or not at all. Leaderboard rank and family are not reasons to drop a model before your own eval.

### Q: Why should you evaluate the exact quantization and engine you will deploy?
- [ ] Engines refuse to load models that were evaluated elsewhere
- [x] Quantization, chat templates and engine parsing change tool-call and JSON behaviour, so another build can score differently
- [ ] Quantized models always score higher than full precision
- [ ] Model licenses require it
> A 4-bit GGUF in llama.cpp and a bf16 model in vLLM are different artefacts with possibly different
> templates and parsers. Scores transfer poorly between them, especially for structured output.

### Q: Two candidates both pass your golden set; one is 3B and one is 8B. What is the usual choice and why?
- [ ] The 8B, because larger models are always safer
- [x] The 3B, because the smallest passing model leaves more memory for context and concurrent agents and costs less
- [ ] Neither, because small models should not be used in agents
- [ ] Whichever has the longer context window, regardless of fit
> When both meet the bar on your tasks, the smaller model serves more agents per GPU at lower latency. Keep
> an escalation path for cases it fails.

## Mastery test
**Problem:** a support agent must run on 8 GB edge GPUs, in Spanish and Portuguese, with 32k-token
transcripts; procurement allows only Apache 2.0 or MIT. The top leaderboard SLM is an 8B under a custom
license. What do you do?

**A strong answer:**
- drops the 8B at the license filter whatever its score, and checks licenses per size within a family;
- computes KV cache at 32k from each config, since it can rival 4-bit weights on 8 GB, and proposes
  summarising transcripts, an 8-bit KV cache or fewer sessions;
- tests Portuguese tool arguments for two permissive families on the deployed build.

## Use cases & projects
### Use cases
- **On-premises agents.** Teams pick the best model fitting the customer's single GPU and license policy,
  then pin its revision in a registry.
- **Multilingual assistants.** Screening shows which family keeps tool calling accurate in the customer's
  languages; benchmarks rarely do.
- **Vendor-exit planning.** Open models from different families give a tested fallback if a hosted
  provider changes terms.

### Mini project
**Build a model-selection scorecard (2-3 hours).**
- **Goal:** choose a small model for one agent step with a documented, repeatable process.
- **Steps:** write the hard filters for your setting (license, memory, context, languages); pick three
  instruct models from different families that pass them, using current model cards; pull them with Ollama;
  build a 50-case golden set for one tool-calling step; run the screening harness and record pass rate, p50
  latency and peak memory.
- **Done when:** you have a scorecard table, the chosen model's exact tag and quantization, and a note on
  why runners-up lost.
