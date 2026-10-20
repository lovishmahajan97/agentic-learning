---
id: small-language-models
module: model-internals
name: Small language models (SLMs)
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - model-internals/scaling-laws
  - model-internals/hallucination
  - model-internals/kv-cache
  - system-design/slm-vs-llm-trade-offs
  - system-design/model-routing-cascading
  - training-adaptation/knowledge-distillation
  - training-adaptation/quantization
  - infra-capacity-economics/edge-ai-deployment
sources:
  - Hoffmann et al., 2022 — Training Compute-Optimal Large Language Models
  - Gunasekar et al., 2023 — Textbooks Are All You Need
  - Hinton, Vinyals and Dean, 2015 — Distilling the Knowledge in a Neural Network
  - Liu et al., 2024 — MobileLLM: Optimizing Sub-billion Parameter Language Models for On-Device Use Cases
---

## TL;DR
"Small language model" has no fixed boundary; in practice it means a model of roughly 0.5–10B parameters
that runs on one consumer GPU, a CPU or a phone. SLMs matter because most production tasks are narrow,
cost and latency scale with parameter count, and modern recipes (training on far more tokens than
compute-optimal, distillation from large teachers, curated data) make small models far stronger than their
size once implied. They trade breadth of knowledge and multi-step reasoning for speed, cost, privacy and
cheap fine-tuning.

## Why it exists
A frontier model is the wrong tool for most of the requests a production system handles. A ticket
classifier, an entity extractor, a query rewriter or a PII redactor does not need encyclopaedic knowledge;
it needs one narrow skill executed millions of times at low latency and low cost. Inference cost, however,
scales roughly with parameters: every decode step reads every active weight, so a 70B model moves about
20× the bytes of a 3.5B model for the same output token, and prefill FLOPs scale the same way.

Three developments made small models worth building rather than merely tolerating. First, the Chinchilla
result showed earlier small models were badly under-trained, and the Llama family then showed that loss
keeps falling far past the compute-optimal token count; because inference dominates the lifetime cost of a
popular model, spending extra training compute on a small model is repaid many times over. Second,
capability can be transferred: distillation trains the small model on a large teacher's output
distribution, and synthetic "textbook" data (the Phi line) or teacher-generated corpora give far more
signal per token than raw web text. Third, deployment constraints are real: on-device assistants,
air-gapped networks, data-residency rules and offline operation simply cannot call a hosted frontier API.

The naive alternatives fail in predictable ways. "Just prompt the big model" fails on cost, on
time-to-first-token, on availability, and on privacy. "Just quantise the big model" buys roughly 4× in
memory, not the 20× that separates a 70B from a 3B, and still needs a server GPU. A small model trained
well, fed the facts it needs through retrieval, and fine-tuned to the task is the option that actually
fits the constraints.

## How it works
### What "small" changes architecturally
An SLM is the same decoder-only transformer with fewer layers, a smaller hidden size, and almost always
grouped-query attention. One structural difference matters at this scale: the embedding matrix. A 128k
vocabulary with a 2,048-dimensional hidden state is about 262M parameters, roughly a fifth of a 1.2B model,
so input and output embeddings are usually tied (one shared matrix) to avoid paying twice. Depth is kept
where possible: MobileLLM reported that deep-and-thin layouts beat shallow-and-wide ones at sub-billion
scale, because reasoning-like composition benefits from more sequential layers.

### Training recipe
- **Over-training.** Chinchilla-optimal is about 20 training tokens per parameter. Llama 3 8B was trained
  on more than 15 trillion tokens, close to 2,000 tokens per parameter. The loss curve flattens but keeps
  improving, and since serving cost per token is about 2N FLOPs, a smaller N that will serve trillions of
  tokens justifies the extra training.
- **Data quality.** With few parameters, every one must earn its place, so small models lean on heavily
  filtered web data, code, and synthetic or curated educational text.
- **Distillation.** Instead of one-hot next tokens, the student is trained to match the teacher's full
  probability distribution (a KL term against teacher logits), which carries information about which wrong
  answers are nearly right; or the student is trained on teacher-generated outputs (sequence-level
  distillation). Reasoning distillation fine-tunes a small model on long chain-of-thought traces from a
  reasoning model and transfers a surprising share of the capability.
- **Pruning from a larger model.** Instead of training from scratch, remove whole layers, attention
  heads or FFN channels from a trained larger model, chosen by an importance score measured on calibration
  data, then recover quality with a short distillation run against the original. Work such as
  Sheared-LLaMA and NVIDIA's Minitron used this route because the starting weights already encode much of
  what the small model must learn, so far fewer training tokens are needed than for a model of the same
  size trained from random initialisation.
- **Post-training.** The same supervised fine-tuning and preference tuning as large models, with less
  headroom: a small model follows complex, multi-constraint instructions less reliably.

### What is lost, and why
Parameters are memory. Long-tail facts live in FFN weights, so fewer and smaller FFNs mean fewer facts,
stored less reliably. The consequences are specific: SLMs hallucinate more on factual recall, degrade faster
on multi-step composition, are more sensitive to prompt wording, and lose more quality under aggressive
quantisation than large models do. The compensation is architectural rather than model-level: supply the
facts (retrieval), supply the computation (tools), narrow the job (fine-tuning), and put a larger model
behind a router for the cases that need it.

### The inference arithmetic
Weights cost `params × bytes_per_param`: a 3B model is 6 GB in bf16, 3 GB in int8, and about 2 GB at 4-bit
once scales are included. Decode is bandwidth-bound, so single-stream tokens per second is roughly memory
bandwidth divided by bytes read per step. As illustrative orders of magnitude: a laptop GPU with a few
hundred GB/s moves a 2 GB model over 100 times a second, so 100+ tokens/s is plausible; a phone with tens of
GB/s lands in the tens of tokens/s; on a server GPU with several TB/s the weights are almost free per
request and the KV cache and batching become the limits. This is why "runs on a phone" and "cheap on a
server" are both true, for different reasons.

### Specialist versus generalist
A fine-tuned 1–8B model routinely matches or beats a zero-shot frontier model on a narrow task when three
conditions hold: the task is well defined, you have thousands of labelled examples, and production inputs
look like the training data. It does not when the task is open-ended, needs novel reasoning, or depends on
world knowledge the fine-tuning set and the prompt do not contain.

## Example
A support organisation receives 2 million tickets a month and wants each one classified into 40 queues
and three fields extracted. Each ticket is about 700 input tokens and the answer about 60 output tokens,
so the monthly volume is 1.4B input and 120M output tokens. All prices and rates below are illustrative
placeholders to show the shape of the calculation, not quotes.

Frontier API path: at a placeholder $3 per million input tokens and $15 per million output tokens, the bill
is $4,200 + $1,800 = $6,000 per month, with multi-second latency per ticket and the tickets leaving the
company's network.

SLM path: fine-tune a 3B instruct model with LoRA on 8,000 labelled tickets (a few GPU-hours). Serve it in
bf16 (6 GB of weights) on one 24 GB GPU with batched inference; sequences are short, so the KV cache is
small and batches of 32–64 fit easily. Average load is 2M / (30 × 86,400 s) ≈ 0.8 tickets per second, with
bursts an order of magnitude higher, well inside what one card handles with continuous batching. At a
placeholder $1 per GPU-hour that is about $730 per month, roughly 8× cheaper, with sub-second latency and
no data egress.

The decision is made on the golden set, not the invoice: run both models on 1,000 held-out tickets. If the
SLM is within the tolerance the business accepts on accuracy and its errors are not concentrated in the
expensive queues, ship it. Route the low-confidence remainder (a small logit margin between the top two
classes, or a schema-validation failure) to the frontier model; in practice that is a minority of traffic,
so the blended cost stays close to the SLM figure while the quality ceiling is preserved.

## Trade-offs
- Cost and latency fall roughly in proportion to parameter count, but factual breadth and multi-step
  reasoning fall too; the trade flips the moment the task needs knowledge that is not supplied in context.
- Fine-tuning is cheap enough to do per task or per customer on a single GPU, but every fine-tune is a model
  to version, evaluate and redeploy, whereas prompting a frontier model has no such pipeline.
- On-device or air-gapped deployment gives privacy and offline operation, but hardware fragmentation and
  quantisation variance make quality less predictable than on a controlled server fleet.
- Over-training makes small models better per parameter but costs more training compute per parameter; it
  pays only at high inference volume, which is why model vendors, not application teams, absorb that cost.
- A router or cascade captures most of the savings while keeping a frontier-quality ceiling, but adds a new
  failure mode: a miscalibrated router sends hard cases to the small model and nobody notices until an
  audit.

## Pitfalls
- Judging an SLM with zero-shot prompts written for a frontier model; the symptom is "it fails" on tasks it
  would handle well after a few-shot prompt or a small fine-tune, and a premature conclusion that SLMs are
  useless.
- Asking an SLM for facts from its weights; the symptom is confident fabrication of dates, citations and API
  names. Give it the source text and let it extract.
- Comparing candidates on a public benchmark that may be in the small model's training data; the symptom is
  a benchmark score far above the in-house eval on your own data.
- Quantising to 4-bit and skipping re-evaluation; the symptom is subtle regressions in JSON validity and
  multilingual output, because small models lose more under quantisation than large ones.
- Serving single-stream on a server GPU; the symptom is a 3B model whose cost per token rivals that of a
  well-batched 30B model, because the card is bandwidth-bound and mostly idle at batch 1 while the larger
  model amortises each weight read over dozens of requests.
- Front-loading a 6k-token system prompt plus retrieved context onto a small model; the symptom is slow
  prefill and answers that ignore the middle of the context, since small models degrade faster with long
  inputs.

## When to use
Reach for an SLM when the task is narrow and high-volume, the inputs carry the facts the answer needs,
latency or cost budgets are tight, data cannot leave a device or network, or you have labelled data to
fine-tune on. Avoid it as the only model for open-ended assistance, novel reasoning, or knowledge-heavy
question answering without retrieval. Measure task accuracy on your golden set against a frontier
baseline, calibration of its confidence signal for routing, p95 latency at target concurrency, and cost per
successful task. Day to day this appears as "can we move this step off the API"; in interviews as "when
would you fine-tune a 3B model instead of prompting a frontier model, and how would you know it worked".
The architecture decision is covered in system-design/slm-vs-llm-trade-offs, and the capability transfer in
training-adaptation/knowledge-distillation.

## Role lens
- **Engineer:** Fine-tunes a 1–8B model with LoRA on task data, quantises and serves it with vLLM or
  llama.cpp, and wires logit-margin escalation to a larger model for low-confidence cases.
- **Architect:** Owns the model portfolio: which pipeline steps run on SLMs, where the cascade boundary sits,
  and how per-task fine-tunes are versioned and gated by evals before release.
- **FDE:** Finds the customer's high-volume narrow workloads, proves an SLM on their data with a side-by-side
  eval, and handles the on-prem or edge constraints that made a hosted API unusable.

## Check yourself
**Q:** A bank wants to classify documents and extract fields on-prem across 5 million pages a month and
says "we will just run the biggest open model we can fit". How would you argue for an SLM, and what would
make you concede?
**A:** Frame it as cost per correct extraction and throughput, not model size. The task is narrow, the
inputs contain the answers, and the bank already has labelled outputs from its back office, so a fine-tuned
3–8B model at int8 on one or two GPUs can process 5 million pages with headroom and sub-second latency,
while a 70B model on the same hardware runs at a fraction of the throughput with no accuracy advantage on
in-distribution forms. Propose a golden set built with their SMEs, fine-tune, compare both on it, and route
low-confidence or schema-invalid outputs to the larger model. I would concede if the eval shows a material
accuracy gap, if the documents are heterogeneous free text that needs world knowledge, or if volume is low
enough that the big model is affordable anyway.

## Quiz
### Q: Why can a 3B model trained on 10 trillion tokens outperform an older 7B model trained on 1 trillion, even though both are "small"?
- [ ] Because the 3B model has a larger vocabulary
- [x] Because loss keeps falling far past the compute-optimal token count, and vendors now over-train small models since inference, not training, dominates lifetime cost
- [ ] Because smaller models have less capacity to overfit
- [ ] Because quantisation works better on 3B models
> Chinchilla-optimal is about 20 tokens per parameter, but the curve keeps improving beyond it. A 7B model
> on 1T tokens is near-optimal and under-trained by modern standards; a 3B on 10T tokens has absorbed far
> more data per parameter, and its lower serving cost is what pays for that training.

### Q: A team fine-tunes a 1B model to answer medication-dosage questions from memory and sees frequent confident errors. What is the most likely diagnosis?
- [ ] The learning rate was too high
- [ ] The model needs a longer context window
- [x] Parametric knowledge is what shrinks first with size; the facts should be supplied through retrieval, with the small model doing extraction and formatting
- [ ] 1B models cannot be fine-tuned
> Small models have few weights in which to store long-tail facts, so recall from memory is their weakest
> mode. Putting the dosage tables in context turns the task into extraction, which a 1B model does well.
> Hyperparameters and context length do not create knowledge that is not there.

### Q: Which workload is the weakest fit for a small language model?
- [ ] High-volume ticket classification with 20,000 labelled examples
- [ ] On-device dictation cleanup with no network access
- [x] Open-ended research assistance that depends on broad world knowledge and multi-step synthesis
- [ ] Query rewriting in front of a search index
> The first, second and fourth are narrow, in-distribution tasks where the needed information is in the
> input. Open-ended research needs breadth of knowledge and long chains of reasoning, which are exactly
> the capabilities that shrink fastest with parameter count.
